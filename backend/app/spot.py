import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

OUNCE_TO_GRAM = 31.1034768
GOLD_API_BASE = "https://api.gold-api.com/price"


async def fetch_spot_usd_per_gram(client: httpx.AsyncClient) -> dict[str, float] | None:
    """Return {'gold': USD/g, 'silver': USD/g} or None on failure.

    Uses api.gold-api.com — free, no API key required, no advertised rate limit.
    Returns spot price per troy ounce in USD; we convert to per-gram here.
    """
    try:
        gold_resp, silver_resp = await asyncio.gather(
            client.get(f"{GOLD_API_BASE}/XAU", timeout=8.0),
            client.get(f"{GOLD_API_BASE}/XAG", timeout=8.0),
        )
        gold_resp.raise_for_status()
        silver_resp.raise_for_status()
        gold_oz = float(gold_resp.json()["price"])
        silver_oz = float(silver_resp.json()["price"])
        return {
            "gold": gold_oz / OUNCE_TO_GRAM,
            "silver": silver_oz / OUNCE_TO_GRAM,
        }
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.exception("spot fetch failed: %s", e)
        return None
