from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_api_key
from app.models import PriceResponse
from app.orchestrator import run

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
