import asyncio
import logging
import os
import sys

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_api_key
from app.db import close_pool, get_pool
from app.fx import fetch_usd_to
from app.models import CoinListing, PriceResponse
from app.orchestrator import run
from app.scrapers.base import DEFAULT_HEADERS, DealerScraper, now_utc
from app.scrapers.registry import ALL_COIN_SCRAPERS, ALL_SCRAPERS
from app.spot import fetch_spot_usd_per_gram

# Per-coin-scraper deadline. Coin scrapers do a single fetch each (unlike the
# Vitus bar scraper that does two), so 10s is plenty of headroom and a hung
# coin dealer can't block the whole snapshot.
COIN_SCRAPER_DEADLINE_S = 10.0

# Send INFO+ logs to stdout — Render captures stdout per service.
# `force=True` so we win over uvicorn's default handler config and the
# orchestrator's structured JSON lines actually surface in logs.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    force=True,
)

app = FastAPI(title="Gold Bar Price Tracker")


@app.on_event("startup")
async def _startup() -> None:
    # Best-effort — pool init is allowed to fail (e.g. transient Neon outage)
    # without taking the whole service down. /snapshot and /history will 503
    # until the next call retries.
    try:
        await get_pool()
    except Exception as e:
        logging.getLogger(__name__).warning("db pool init failed at startup: %s", e)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_pool()

# CORS — only the deployed Cloudflare Pages frontend may call us.
# (Set FRONTEND_ORIGIN env var on Render to your *.pages.dev URL.)
_origin = os.environ.get("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_origin],
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type"],
)

ALLOWED_SIZES = {2.5, 5.0, 10.0, 20.0}
HISTORY_RANGES = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}
DEALER_NAMES = {s.name for s in ALL_SCRAPERS}


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "gold-bar-tracker"}


@app.get("/prices/{size}", response_model=PriceResponse)
async def get_prices(size: float, _: None = Depends(require_api_key)) -> PriceResponse:
    if size not in ALLOWED_SIZES:
        raise HTTPException(status_code=400, detail=f"size must be one of {sorted(ALLOWED_SIZES)}")
    return await run(size_g=size)


@app.get("/spot")
async def get_spot(_: None = Depends(require_api_key)) -> dict[str, object]:
    """Spot prices for gold and silver, converted to per-gram in EUR and DKK.

    Used by the frontend for the auto-refreshing spot ticker — does not run
    any dealer scrapers, so it's fast and cheap.
    """
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        spot_usd_per_g, (fx_rates, fx_stale) = await asyncio.gather(
            fetch_spot_usd_per_gram(client),
            fetch_usd_to(client),
        )
    fetched_at = now_utc().isoformat()
    if spot_usd_per_g is None:
        return {"spot": None, "fx_stale": fx_stale, "fetched_at": fetched_at}
    return {
        "spot": {
            "gold": {
                "per_gram_eur": round(spot_usd_per_g["gold"] * fx_rates["EUR"], 2),
                "per_gram_dkk": round(spot_usd_per_g["gold"] * fx_rates["DKK"], 2),
            },
            "silver": {
                "per_gram_eur": round(spot_usd_per_g["silver"] * fx_rates["EUR"], 4),
                "per_gram_dkk": round(spot_usd_per_g["silver"] * fx_rates["DKK"], 4),
            },
        },
        "fx_stale": fx_stale,
        "fetched_at": fetched_at,
    }


async def _safe_fetch_coins(scraper, client: httpx.AsyncClient) -> list[CoinListing]:
    """Run a coin scraper with a deadline. Errors → single error Listing."""
    try:
        return await asyncio.wait_for(
            scraper.fetch(client), timeout=COIN_SCRAPER_DEADLINE_S,
        )
    except TimeoutError:
        return [CoinListing(
            dealer=scraper.name, status="error",
            error=f"timeout after {COIN_SCRAPER_DEADLINE_S:g}s", fetched_at=now_utc(),
        )]
    except Exception as e:
        return [CoinListing(
            dealer=scraper.name, status="error",
            error=f"{e.__class__.__name__}: {e}", fetched_at=now_utc(),
        )]


@app.post("/snapshot")
async def snapshot(_: None = Depends(require_api_key)) -> dict[str, object]:
    """Run all sizes × all dealers + spot + coins, persist to Postgres.

    Called by the GitHub Action cron every 20 min. Writes one spot_snapshots
    row, N bar_snapshots rows (one per dealer × size, including errors), and
    M coin_snapshots rows (one per recognized in-stock coin per dealer). All
    in a single transaction so the snapshot is atomic.
    """
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")

    sizes = sorted(ALLOWED_SIZES)
    # 4 parallel run() calls — wasteful on the spot+fx fetch (4x instead of 1x)
    # but keeps the code dead simple. The 4 spot reads are within seconds of
    # each other, so we just persist one of them.
    results = await asyncio.gather(*[run(size_g=s) for s in sizes])

    # Single canonical timestamp for the whole snapshot — easier to query
    # than keeping each Listing's individual fetched_at.
    fetched_at = now_utc()
    first_with_spot = next((r for r in results if r.spot is not None), None)
    spot_gold_dkk = (
        first_with_spot.spot.gold.per_gram_dkk
        if first_with_spot is not None and first_with_spot.spot is not None
        else None
    )

    bar_rows = [
        (
            fetched_at,
            li.dealer,
            r.size_g,
            li.status,
            li.price_dkk,
            li.brand,
            li.error,
            spot_gold_dkk if r.spot is None else r.spot.gold.per_gram_dkk,
        )
        for r in results for li in r.listings
    ]

    # Now fan out the coin scrapers. They share a fresh client.
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as coin_client:
        coin_results = await asyncio.gather(
            *[_safe_fetch_coins(s, coin_client) for s in ALL_COIN_SCRAPERS]
        )

    coin_rows = [
        (
            fetched_at,
            c.dealer,
            c.coin_type,
            c.size_label,
            c.gross_weight_g,
            c.purity,
            c.fine_gold_g,
            c.status,
            c.price_dkk,
            c.error,
            spot_gold_dkk,
            str(c.url) if c.url else None,
        )
        for batch in coin_results for c in batch
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            if first_with_spot is not None and first_with_spot.spot is not None:
                spot = first_with_spot.spot
                await conn.execute(
                    """
                    INSERT INTO spot_snapshots (
                        fetched_at, gold_dkk_per_g, gold_eur_per_g,
                        silver_dkk_per_g, silver_eur_per_g, fx_stale
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    fetched_at,
                    spot.gold.per_gram_dkk, spot.gold.per_gram_eur,
                    spot.silver.per_gram_dkk, spot.silver.per_gram_eur,
                    first_with_spot.fx_stale,
                )
            await conn.executemany(
                """
                INSERT INTO bar_snapshots (
                    fetched_at, dealer, size_g, status, price_dkk,
                    brand, error, spot_gold_dkk_per_g
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                bar_rows,
            )
            if coin_rows:
                await conn.executemany(
                    """
                    INSERT INTO coin_snapshots (
                        fetched_at, dealer, coin_type, size_label,
                        gross_weight_g, purity, fine_gold_g,
                        status, price_dkk, error,
                        spot_gold_dkk_per_g, listing_url
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    """,
                    coin_rows,
                )

    return {
        "ok": True,
        "fetched_at": fetched_at.isoformat(),
        "sizes": sizes,
        "bar_rows": len(bar_rows),
        "coin_rows": len(coin_rows),
        "spot_recorded": first_with_spot is not None,
    }


@app.get("/history/bar/{dealer}/{size}")
async def get_bar_history(
    dealer: str,
    size: float,
    range: str = "30d",
    _: None = Depends(require_api_key),
) -> dict[str, object]:
    """Time series of (fetched_at, status, price_dkk, spot_gold_dkk_per_g)."""
    if size not in ALLOWED_SIZES:
        raise HTTPException(status_code=400, detail=f"size must be one of {sorted(ALLOWED_SIZES)}")
    if dealer not in DEALER_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown dealer: {dealer}")
    if range not in HISTORY_RANGES:
        raise HTTPException(
            status_code=400, detail=f"range must be one of {sorted(HISTORY_RANGES)}",
        )
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")

    interval = HISTORY_RANGES[range]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT fetched_at, status, price_dkk, spot_gold_dkk_per_g, brand
            FROM bar_snapshots
            WHERE dealer = $1 AND size_g = $2
              AND fetched_at >= NOW() - INTERVAL '{interval}'
            ORDER BY fetched_at ASC
            """,
            dealer,
            size,
        )

    return {
        "dealer": dealer,
        "size_g": size,
        "range": range,
        "points": [
            {
                "fetched_at": row["fetched_at"].isoformat(),
                "status": row["status"],
                "price_dkk": float(row["price_dkk"]) if row["price_dkk"] is not None else None,
                "spot_gold_dkk_per_g": (
                    float(row["spot_gold_dkk_per_g"])
                    if row["spot_gold_dkk_per_g"] is not None else None
                ),
                "brand": row["brand"],
            }
            for row in rows
        ],
    }


@app.get("/health")
async def health(_: None = Depends(require_api_key)) -> dict[str, object]:
    """Run all scrapers against 5g and return per-dealer pass/fail summary."""
    async def _check(s: DealerScraper) -> dict[str, object]:
        try:
            async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
                listing = await s.fetch(5.0, client)
            return {
                "dealer": s.name,
                "ok": listing is not None and listing.status == "ok",
                "status": listing.status if listing else "no_listing",
            }
        except Exception as e:
            return {"dealer": s.name, "ok": False, "status": "exception", "error": str(e)}

    results = await asyncio.gather(*[_check(s) for s in ALL_SCRAPERS])
    return {"ok": all(r["ok"] for r in results), "scrapers": results}
