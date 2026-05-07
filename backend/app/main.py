import asyncio

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_api_key
from app.models import PriceResponse
from app.orchestrator import run
from app.scrapers.base import DEFAULT_HEADERS
from app.scrapers.registry import ALL_SCRAPERS

app = FastAPI(title="Gold Bar Price Tracker")

# CORS — only the deployed Cloudflare Pages frontend may call us.
# (Set FRONTEND_ORIGIN env var on Render to your *.pages.dev URL.)
import os
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


@app.get("/health")
async def health(_: None = Depends(require_api_key)) -> dict[str, object]:
    """Run all scrapers against 5g and return per-dealer pass/fail summary."""
    async def _check(s) -> dict[str, object]:
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
