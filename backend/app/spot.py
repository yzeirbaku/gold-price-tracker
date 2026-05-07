import logging
import os

import httpx

logger = logging.getLogger(__name__)

OUNCE_TO_GRAM = 31.1034768
METALS_DEV_URL = "https://api.metals.dev/v1/latest"


async def fetch_spot_usd_per_gram(client: httpx.AsyncClient) -> dict[str, float] | None:
    """Return {'gold': USD/g, 'silver': USD/g} or None on failure."""
    api_key = os.environ.get("METALS_DEV_API_KEY")
    if not api_key:
        logger.warning("METALS_DEV_API_KEY not set; skipping spot fetch")
        return None
    try:
        resp = await client.get(
            METALS_DEV_URL,
            params={"api_key": api_key, "currency": "USD", "unit": "toz"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        metals = data["metals"]
        return {
            "gold": float(metals["gold"]) / OUNCE_TO_GRAM,
            "silver": float(metals["silver"]) / OUNCE_TO_GRAM,
        }
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.exception("spot fetch failed: %s", e)
        return None
