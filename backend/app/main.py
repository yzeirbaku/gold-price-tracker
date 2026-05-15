import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.alerts import evaluate_alerts
from app.alerts import router as alerts_router
from app.auth import require_api_key
from app.auth_session import router as auth_router
from app.buy_context import (
    context_to_dict,
    load_bar_context,
    load_coin_context,
)
from app.db import close_pool, get_pool
from app.fx import fetch_usd_to
from app.models import CoinListing, PriceResponse
from app.orchestrator import flag_coin_premium_outliers, run
from app.portfolio import router as portfolio_router
from app.rate_limit import IPRateLimiter
from app.reports.builder import build_report
from app.reports.storage import fetch_report_html, list_reports, upsert_report
from app.reports.windows import (
    previous_calendar_month,
    previous_calendar_week,
    rolling_last_n_days,
)
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
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: pool init is best-effort — a transient Neon outage shouldn't
    # take the whole service down. /snapshot and /history will 503 until the
    # next call retries get_pool().
    try:
        await get_pool()
    except Exception as e:
        logger.warning("db pool init failed at startup: %s", e)
    # `try/finally` around the yield: matches the unconditional shutdown
    # behavior of the previous @app.on_event("shutdown") decorator. Without
    # the finally, a raise during runtime that propagates into the lifespan
    # generator would skip pool cleanup.
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(title="Gold Bar Price Tracker", lifespan=lifespan)

# CORS — restrict by origin as an extra defense-in-depth layer. Auth is
# bearer-token-based (Authorization header, not cookies), so we don't need
# allow_credentials. Browsers treat Authorization-header requests as
# "non-credentialed" CORS, so cross-site is always permitted by the spec —
# the origin allowlist is just to keep random hostnames from poking the
# X-API-Key endpoints.
_origin = os.environ.get("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_origin],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type", "Authorization"],
    expose_headers=["Content-Disposition"],
)

app.include_router(auth_router)
app.include_router(portfolio_router)
app.include_router(alerts_router)

ALLOWED_SIZES = {2.5, 5.0, 10.0, 20.0}
HISTORY_RANGES = {"24h": "24 hours", "7d": "7 days", "30d": "30 days"}
DEALER_NAMES = {s.name for s in ALL_SCRAPERS}

# Max allowed deviation between consecutive snapshot ticks for gold spot in DKK
# per gram. If exceeded, the snapshot is logged + skipped instead of persisted.
# 10% is intentionally generous: real gold rarely moves more than a couple of
# percent in a 20-min window even during Fed announcements, but a 31x unit
# flip, near-zero glitch, or ~7%+ FX drift would all be caught comfortably.
# See CLAUDE.md "Conventions / gotchas".
SNAPSHOT_OUTLIER_THRESHOLD = 0.10

# Public /coins fan-out throttle: each client IP gets one call per 5 seconds.
# /coins hits 5 dealer sites per request, so a leaked X-API-Key could quickly
# get our Render egress blocked by dealer WAFs without this guard. Keep it
# loose enough that the PWA's own usage (one fetch on view-open + optional
# manual refresh) never hits it.
_COINS_RATE_LIMITER = IPRateLimiter(min_interval_s=5.0)


def _client_ip(request: Request) -> str:
    """Same X-Forwarded-For-aware extraction as auth_session._client_ip; the
    fallback string keeps this dependency total even when request.client is
    None (test client without an explicit host)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit_coins(request: Request) -> None:
    """FastAPI dependency: throttle /coins per client IP."""
    wait_s = _COINS_RATE_LIMITER.check(_client_ip(request))
    if wait_s > 0:
        raise HTTPException(
            status_code=429,
            detail="too many requests; please slow down",
            headers={"Retry-After": str(max(1, int(wait_s) + 1))},
        )


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

    # Refuse to persist a snapshot if any run's FX lookup fell back to the
    # static fallback in fx.py — that rate gets stale fast and even a ~7%
    # gap from live silently corrupts every premium calc downstream, since
    # bar_snapshots / coin_snapshots store the bad spot per row and history
    # is recomputed from those columns forever. See incident 2026-05-14
    # where one stale tick warped every dealer's premium chart for one
    # tick. The /spot live endpoint can still serve the stale fallback —
    # it disappears on the next 30s refresh — but the cron must NEVER bake
    # a stale value into history. The cron retries in 20 min.
    if any(r.fx_stale for r in results):
        skipped_at = now_utc()
        logger.warning(
            "snapshot_skipped %s",
            json.dumps({
                "event": "snapshot_skipped",
                "reason": "fx_stale",
                "fetched_at": skipped_at.isoformat(),
                "stale_sizes": [r.size_g for r in results if r.fx_stale],
                "sizes_attempted": sizes,
            }),
        )
        return {
            "ok": False,
            "skipped": True,
            "reason": "fx_stale",
            "fetched_at": skipped_at.isoformat(),
        }

    # Single canonical timestamp for the whole snapshot — easier to query
    # than keeping each Listing's individual fetched_at.
    fetched_at = now_utc()
    first_with_spot = next((r for r in results if r.spot is not None), None)
    spot_gold_dkk = (
        first_with_spot.spot.gold.per_gram_dkk
        if first_with_spot is not None and first_with_spot.spot is not None
        else None
    )

    # Outlier guard: catches whatever the fx_stale check misses (a bad
    # gold-api response, a Frankfurter return value that's wrong but didn't
    # error, a future unit-change at an upstream API). Compares the new
    # gold spot to the most recent spot_snapshots row within the last hour.
    # Threshold is generous on purpose — gold rarely moves more than a few
    # % in 20 minutes even during news events, but a 31x unit flip, a
    # near-zero glitch, or a ~10% data corruption would all be caught. See
    # CLAUDE.md "Conventions / gotchas" for the rationale + tuning notes.
    #
    # Non-transactional read is intentional: this is a single-runner cron
    # (QStash gives us strict serial delivery), so there's no concurrent
    # /snapshot writer to race with. Widening this into a transaction would
    # mean holding a connection open across the scraper fan-out — bad.
    # If we ever lose single-runner serialization, swap this for SELECT FOR
    # UPDATE on a sentinel row or a unique constraint on (fetched_at::minute).
    if spot_gold_dkk is not None:
        async with pool.acquire() as guard_conn:
            prev = await guard_conn.fetchrow(
                """
                SELECT gold_dkk_per_g FROM spot_snapshots
                WHERE fetched_at >= NOW() - INTERVAL '60 minutes'
                  AND gold_dkk_per_g IS NOT NULL
                ORDER BY fetched_at DESC LIMIT 1
                """
            )
        # No prior row in the 60-min window → accept whatever the upstream
        # returned. Outage recovery depends on this: after a long downtime,
        # the first tick has no baseline and must be allowed through;
        # subsequent ticks 20 min later then compare against it.
        # `prev_val > 0` guards against a (hypothetical) zero baseline from
        # pre-fix legacy rows that would otherwise ZeroDivisionError below.
        if prev is not None:
            prev_val = float(prev["gold_dkk_per_g"])
            new_val = float(spot_gold_dkk)
            if prev_val > 0:
                deviation = abs(new_val - prev_val) / prev_val
                if deviation > SNAPSHOT_OUTLIER_THRESHOLD:
                    logger.warning(
                        "snapshot_skipped %s",
                        json.dumps({
                            "event": "snapshot_skipped",
                            "reason": "outlier",
                            "fetched_at": fetched_at.isoformat(),
                            "new_gold_dkk_per_g": new_val,
                            "prev_gold_dkk_per_g": prev_val,
                            "deviation_pct": round(deviation * 100, 3),
                            "threshold_pct": SNAPSHOT_OUTLIER_THRESHOLD * 100,
                        }),
                    )
                    return {
                        "ok": False,
                        "skipped": True,
                        "reason": "outlier",
                        "fetched_at": fetched_at.isoformat(),
                    }

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

    # Flag implausible coin premiums (wrong-field grabs from the scraper) so
    # bad prices don't land in coin_snapshots. Same philosophy as the bar
    # outlier guard in orchestrator.run(). Float-cast on Decimal-tolerant
    # spot_gold_dkk; None-safe inside the helper.
    coin_spot_for_guard = float(spot_gold_dkk) if spot_gold_dkk is not None else None
    for batch in coin_results:
        flag_coin_premium_outliers(batch, coin_spot_for_guard)

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
    # Alerts evaluation runs *after* the snapshot transaction commits — so a
    # hung Resend HTTP call cannot hold the snapshot connection open and
    # cannot roll back the snapshot data on timeout. evaluate_alerts acquires
    # its own short-lived connections per write. The fx_stale and outlier
    # guards above already gated us; whatever evaluate_alerts reads from the
    # just-committed bar_rows/coin_rows is data we trust.
    await evaluate_alerts(pool, fetched_at, bar_rows, coin_rows)

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


@app.get("/context/bar/{dealer}/{size}")
async def get_bar_context(
    dealer: str, size: float, _: None = Depends(require_api_key),
) -> dict[str, object]:
    """Buy-now-or-wait analytics for a specific (dealer, bar size) over the
    last 30 days: today's premium vs the dealer's IQR band, the lowest
    premium recorded, and whether today is a new low."""
    if size not in ALLOWED_SIZES:
        raise HTTPException(
            status_code=400, detail=f"size must be one of {sorted(ALLOWED_SIZES)}",
        )
    if dealer not in DEALER_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown dealer: {dealer}")
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    async with pool.acquire() as conn:
        ctx = await load_bar_context(conn, dealer, size)
    return context_to_dict(ctx)


@app.get("/context/coin/{dealer}/{coin_type}/{fine_gold_g}")
async def get_coin_context(
    dealer: str, coin_type: str, fine_gold_g: float,
    _: None = Depends(require_api_key),
) -> dict[str, object]:
    """Buy-now-or-wait analytics for a specific (dealer, coin_type, fine_gold_g)
    over the last 30 days. See `get_bar_context` for response shape."""
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    async with pool.acquire() as conn:
        ctx = await load_coin_context(conn, dealer, coin_type, fine_gold_g)
    return context_to_dict(ctx)


@app.get("/coins")
async def get_coins(
    _: None = Depends(require_api_key),
    __: None = Depends(rate_limit_coins),
) -> dict[str, object]:
    """Live-scrape every coin dealer in parallel and rank by premium asc.

    No DB read \u2014 same model as `/prices/{size}`. Spot + FX run in the same
    gather so we get the freshest reference price for premium math. The
    20-min snapshot cron still writes to `coin_snapshots` for history and
    report aggregation; this endpoint just no longer reads from it.
    """
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        spot_task = asyncio.create_task(fetch_spot_usd_per_gram(client))
        fx_task = asyncio.create_task(fetch_usd_to(client))
        coin_tasks = [
            asyncio.create_task(_safe_fetch_coins(s, client))
            for s in ALL_COIN_SCRAPERS
        ]
        spot_usd = await spot_task
        fx_rates, _fx_stale = await fx_task
        coin_batches = await asyncio.gather(*coin_tasks)

    spot_gold_dkk_per_g: float | None = None
    if spot_usd is not None:
        spot_gold_dkk_per_g = round(spot_usd["gold"] * fx_rates["DKK"], 4)

    # Flag scraper outliers before building the response. Same helper as the
    # /snapshot path uses, so live and persisted views agree on what's valid.
    for batch in coin_batches:
        flag_coin_premium_outliers(batch, spot_gold_dkk_per_g)

    fetched_at = now_utc()
    listings: list[dict[str, object]] = []
    for batch in coin_batches:
        for c in batch:
            price = c.price_dkk
            fine = c.fine_gold_g
            premium: float | None = None
            if (c.status == "ok" and price is not None
                    and spot_gold_dkk_per_g is not None and fine and fine > 0):
                ref = spot_gold_dkk_per_g * fine
                if ref > 0:
                    premium = round((price - ref) / ref * 100, 2)
            listings.append({
                "dealer": c.dealer,
                "coin_type": c.coin_type,
                "size_label": c.size_label,
                "gross_weight_g": c.gross_weight_g,
                "purity": c.purity,
                "fine_gold_g": fine,
                "status": c.status,
                "price_dkk": price,
                "premium_pct": premium,
                "error": c.error,
                "url": str(c.url) if c.url else None,
            })
    listings.sort(key=lambda li: (
        0 if li["status"] == "ok" else 1,
        li["premium_pct"] if li["premium_pct"] is not None else float("inf"),
    ))
    return {"fetched_at": fetched_at.isoformat(), "listings": listings}


@app.get("/history/coin/{dealer}/{coin_type}/{fine_gold_g}")
async def get_coin_history(
    dealer: str,
    coin_type: str,
    fine_gold_g: float,
    range: str = "30d",
    _: None = Depends(require_api_key),
) -> dict[str, object]:
    """Time series for a specific (dealer, coin_type, fine_gold_g) combo."""
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
            SELECT fetched_at, status, price_dkk, spot_gold_dkk_per_g, size_label
            FROM coin_snapshots
            WHERE dealer = $1 AND coin_type = $2
              AND ABS(fine_gold_g - $3::numeric) < 0.005
              AND fetched_at >= NOW() - INTERVAL '{interval}'
            ORDER BY fetched_at ASC
            """,
            dealer, coin_type, fine_gold_g,
        )
    return {
        "dealer": dealer,
        "coin_type": coin_type,
        "fine_gold_g": fine_gold_g,
        "range": range,
        "points": [
            {
                "fetched_at": r["fetched_at"].isoformat(),
                "status": r["status"],
                "price_dkk": float(r["price_dkk"]) if r["price_dkk"] is not None else None,
                "spot_gold_dkk_per_g": (
                    float(r["spot_gold_dkk_per_g"])
                    if r["spot_gold_dkk_per_g"] is not None else None
                ),
                "size_label": r["size_label"],
            }
            for r in rows
        ],
    }


@app.get("/reports")
async def reports_list(_: None = Depends(require_api_key)) -> list[dict[str, object]]:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    async with pool.acquire() as conn:
        rows = await list_reports(conn)
    return [
        {**r, "period_start": r["period_start"].isoformat(),
         "period_end": r["period_end"].isoformat()}
        for r in rows
    ]


@app.get("/reports/{report_id}")
async def reports_fetch(
    report_id: int, _: None = Depends(require_api_key),
) -> Response:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    async with pool.acquire() as conn:
        result = await fetch_report_html(conn, report_id)
    if result is None:
        raise HTTPException(status_code=404, detail="report not found")
    html, kind, period_start, period_end = result
    filename = f"{kind}-report_{period_start.isoformat()}_to_{period_end.isoformat()}.html"
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/reports/generate")
async def reports_generate(
    range: str, _: None = Depends(require_api_key),
) -> Response:
    if range not in {"week", "month"}:
        raise HTTPException(status_code=400, detail="range must be 'week' or 'month'")
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    n = 7 if range == "week" else 30
    now = datetime.now(tz=UTC)
    window = rolling_last_n_days(now, n)
    async with pool.acquire() as conn:
        html = await build_report(conn, window)
    filename = (
        f"{window.kind}-report_{window.period_start.isoformat()}"
        f"_to_{window.period_end.isoformat()}.html"
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/reports/cron/{type}")
async def reports_cron(
    type: str, _: None = Depends(require_api_key),
) -> JSONResponse:
    if type not in {"weekly", "monthly"}:
        raise HTTPException(status_code=400, detail="type must be 'weekly' or 'monthly'")
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured")
    now = datetime.now(tz=UTC)
    window = (previous_calendar_week(now) if type == "weekly"
              else previous_calendar_month(now))
    async with pool.acquire() as conn:
        html = await build_report(conn, window)
        rid = await upsert_report(
            conn, type, window.period_start, window.period_end, html,
        )
    return JSONResponse({
        "id": rid, "type": type,
        "period_start": window.period_start.isoformat(),
        "period_end": window.period_end.isoformat(),
    })


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
