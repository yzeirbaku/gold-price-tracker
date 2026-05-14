import asyncio
import logging
from datetime import date, timedelta
from typing import Literal

import httpx
import yfinance as yf

logger = logging.getLogger(__name__)

OUNCE_TO_GRAM = 31.1034768
GOLD_API_BASE = "https://api.gold-api.com/price"

_HISTORICAL_USD_PER_GRAM_CACHE: dict[tuple[str, str], float] = {}
_HISTORICAL_FALLBACK_DAYS = 7
_YF_TICKER = {"gold": "GC=F", "silver": "SI=F"}

# Generous sanity bounds on the yfinance historical spot, in USD/gram. Gold
# has historically traded ~[$30/g, $130/g]; silver ~[$0.30/g, $1.60/g]. The
# guard bounds widen those ~4-30× on purpose — they are NOT a market-vol
# fence, they are a "did the upstream API silently break" fence. A unit-flip
# (per-oz returned as per-gram → ~31× off → caught at 500 / 50), a stuck-at-
# zero / NaN cast, or a wrong-ticker substitution would all blow past these.
# Wide enough that no real market price can ever trip them; tight enough to
# refuse to freeze obvious garbage onto a `purchases` row. Tune by widening
# rather than tightening if a real future price ever rejects. See CLAUDE.md
# "Conventions / gotchas" for the rationale.
_HISTORICAL_BOUNDS_USD_PER_G: dict[str, tuple[float, float]] = {
    "gold":   (30.0, 500.0),
    "silver": (0.20, 50.0),
}


class HistoricalSpotUnavailable(RuntimeError):
    """Raised when no historical spot can be resolved within the fallback window,
    OR when the value returned by yfinance falls outside the generous sanity
    bounds defined above. Either way the right move is to surface the failure
    to the user so they can retry — never silently persist a suspect value."""


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


async def fetch_historical_usd_per_gram(
    metal: Literal["gold", "silver"],
    on_date: date,
) -> float:
    """Return USD/g spot for `metal` on `on_date` via Yahoo Finance futures
    (GC=F gold, SI=F silver). Walks back up to 7 days through weekends and
    market closures. Cached in-process by (ticker, date).

    Futures trade within fractions of a percent of spot, so for portfolio
    P&L purposes this is equivalent to a true spot quote.
    """
    ticker = _YF_TICKER[metal]
    cache_keys_to_set: list[tuple[str, str]] = []
    cache_key = (ticker, on_date.isoformat())
    if cache_key in _HISTORICAL_USD_PER_GRAM_CACHE:
        return _HISTORICAL_USD_PER_GRAM_CACHE[cache_key]

    # Fetch a window covering on_date back through the fallback window in one
    # shot — yfinance accepts a date range, returning only days that traded.
    start = on_date - timedelta(days=_HISTORICAL_FALLBACK_DAYS)
    # yfinance's `end` is exclusive; +1 day to include on_date.
    end = on_date + timedelta(days=1)
    try:
        closes_by_date = await asyncio.to_thread(_yf_closes, ticker, start, end)
    except Exception as e:  # noqa: BLE001
        logger.warning("yfinance fetch failed for %s %s..%s: %s", ticker, start, end, e)
        closes_by_date = {}

    for offset in range(_HISTORICAL_FALLBACK_DAYS + 1):
        target = on_date - timedelta(days=offset)
        key_iso = target.isoformat()
        if key_iso in closes_by_date:
            per_gram = closes_by_date[key_iso] / OUNCE_TO_GRAM
            lo, hi = _HISTORICAL_BOUNDS_USD_PER_G[metal]
            if not (lo <= per_gram <= hi):
                # Don't cache the suspect value — a future call might get a
                # correct quote that we'd otherwise mask with the bad one.
                raise HistoricalSpotUnavailable(
                    f"{metal} historical spot {per_gram:.4f} USD/g for "
                    f"{key_iso} is outside sanity bounds [{lo}, {hi}]"
                )
            _HISTORICAL_USD_PER_GRAM_CACHE[(ticker, key_iso)] = per_gram
            # Cache the requested date too, even if we ended up walking back —
            # so a future request for the same target_date doesn't refetch.
            for k in cache_keys_to_set:
                _HISTORICAL_USD_PER_GRAM_CACHE[k] = per_gram
            _HISTORICAL_USD_PER_GRAM_CACHE[cache_key] = per_gram
            return per_gram
        cache_keys_to_set.append((ticker, key_iso))

    raise HistoricalSpotUnavailable(
        f"no spot for {metal} within {_HISTORICAL_FALLBACK_DAYS} days of {on_date.isoformat()}"
    )


def _yf_closes(ticker: str, start: date, end: date) -> dict[str, float]:
    """Blocking yfinance call. Returns {iso_date: close_price} for trading
    days in [start, end). Runs in a thread via asyncio.to_thread."""
    hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
    if hist.empty:
        return {}
    out: dict[str, float] = {}
    for ts, row in hist.iterrows():
        # ts is a tz-aware pandas Timestamp; pull the calendar date.
        out[ts.date().isoformat()] = float(row["Close"])
    return out
