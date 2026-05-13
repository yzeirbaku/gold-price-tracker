import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger(__name__)

# Stamped fallback used when frankfurter.app is unreachable.
# Refresh quarterly. USD/EUR drifts; USD/DKK is pegged to EUR so moves with it.
# Last refreshed: 2026-05-07
STATIC_FALLBACK: dict[str, float] = {"EUR": 0.92, "DKK": 6.85}

FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"
FRANKFURTER_URL = f"{FRANKFURTER_BASE}/latest"

_HISTORICAL_FX_CACHE: dict[str, float] = {}
_HISTORICAL_FALLBACK_DAYS = 7


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


async def fetch_usd_to_dkk_on(client: httpx.AsyncClient, on_date: date) -> float:
    """Return USD→DKK rate for `on_date`, walking back through weekends if
    Frankfurter returns no data. Falls back to STATIC_FALLBACK['DKK'] after
    7 misses. Cached in-process by date string."""
    for offset in range(_HISTORICAL_FALLBACK_DAYS + 1):
        target = on_date - timedelta(days=offset)
        key = target.isoformat()
        if key in _HISTORICAL_FX_CACHE:
            return _HISTORICAL_FX_CACHE[key]
        try:
            resp = await client.get(
                f"{FRANKFURTER_BASE}/{key}",
                params={"from": "USD", "to": "DKK"},
                timeout=8.0,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            rate = float(resp.json()["rates"]["DKK"])
            _HISTORICAL_FX_CACHE[key] = rate
            return rate
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            logger.warning("historical FX fetch failed for %s: %s", key, e)
            continue
    logger.warning("historical FX unavailable for %s; using static fallback", on_date.isoformat())
    return STATIC_FALLBACK["DKK"]
