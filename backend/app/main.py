import asyncio
import os

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_api_key
from app.fx import fetch_usd_to
from app.models import PriceResponse
from app.orchestrator import run
from app.scrapers.base import DEFAULT_HEADERS, DealerScraper, now_utc
from app.scrapers.registry import ALL_SCRAPERS
from app.spot import fetch_spot_usd_per_gram

app = FastAPI(title="Gold Bar Price Tracker")

# CORS — only the deployed Cloudflare Pages frontend may call us.
# (Set FRONTEND_ORIGIN env var on Render to your *.pages.dev URL.)
_origin = os.environ.get("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_origin],
    allow_methods=["GET"],
    allow_headers=["X-API-Key", "Content-Type"],
)

ALLOWED_SIZES = {2.5, 5.0, 10.0}


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
