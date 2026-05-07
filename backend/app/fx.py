import logging

import httpx

logger = logging.getLogger(__name__)

# Stamped fallback used when frankfurter.app is unreachable.
# Refresh quarterly. USD/EUR drifts; USD/DKK is pegged to EUR so moves with it.
# Last refreshed: 2026-05-07
STATIC_FALLBACK: dict[str, float] = {"EUR": 0.92, "DKK": 6.85}

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"


async def fetch_usd_to(client: httpx.AsyncClient) -> tuple[dict[str, float], bool]:
    """Return ({'EUR': rate, 'DKK': rate}, stale_flag)."""
    try:
        resp = await client.get(
            FRANKFURTER_URL,
            params={"from": "USD", "to": "EUR,DKK"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        rates = data["rates"]
        return {"EUR": float(rates["EUR"]), "DKK": float(rates["DKK"])}, False
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("FX fetch failed; using static fallback: %s", e)
        return STATIC_FALLBACK.copy(), True
