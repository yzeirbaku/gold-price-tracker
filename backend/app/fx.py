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


class HistoricalFxUnavailable(RuntimeError):
    """Raised when no historical FX rate can be resolved within the fallback window.

    Used by portfolio purchase writes — those values get frozen forever on the
    `purchases` row, so we'd rather fail loud and have the user retry than
    bake a stale STATIC_FALLBACK rate into history (see CLAUDE.md gotchas).
    """


async def fetch_usd_to(client: httpx.AsyncClient) -> tuple[dict[str, float], bool]:
    """Return ({'EUR': rate, 'DKK': rate}, stale_flag).

    Live request path — used by /spot, /prices, /coins. Returns the stamped
    STATIC_FALLBACK with stale_flag=True if Frankfurter errors. That's safe
    here because the value is shown briefly on screen and replaced on the
    next 30s refresh — it never lands in Postgres. The cron-only /snapshot
    path inspects `stale_flag` and refuses to persist when it's True (see
    main.py snapshot()).
    """
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
    Frankfurter returns no data. Cached in-process by date string.

    Raises HistoricalFxUnavailable if Frankfurter is unreachable for every
    day in the fallback window. We deliberately do NOT fall back to the
    stamped STATIC_FALLBACK here because this rate gets frozen onto a
    `purchases` row forever — letting the user retry beats silently baking
    in a stale rate.
    """
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
    raise HistoricalFxUnavailable(
        f"no USD→DKK rate available within {_HISTORICAL_FALLBACK_DAYS} days "
        f"of {on_date.isoformat()}"
    )
