import asyncio
import json
import logging
import time

import httpx

from app.fx import fetch_usd_to
from app.models import Listing, PerCurrency, PriceResponse, SpotPrice
from app.scrapers.base import DEFAULT_HEADERS, DealerScraper, now_utc
from app.scrapers.registry import ALL_SCRAPERS
from app.spot import fetch_spot_usd_per_gram

logger = logging.getLogger(__name__)

# Hard per-scraper deadline. Vitus does two sequential ~6s fetches, so 10s leaves
# headroom while still bounding the worst case. One slow dealer must not block
# the whole response — each scraper times out independently and others still
# resolve in parallel.
SCRAPER_DEADLINE_S = 10.0


async def _safe_fetch(
    scraper: "DealerScraper", size_g: float, client: httpx.AsyncClient
) -> tuple[Listing, float]:
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            scraper.fetch(size_g, client), timeout=SCRAPER_DEADLINE_S,
        )
        if result is None:
            listing = Listing(
                dealer=scraper.name, status="unavailable",
                error="size not offered", fetched_at=now_utc(),
            )
        else:
            listing = result
    except TimeoutError:
        listing = Listing(
            dealer=scraper.name, status="error",
            error=f"timeout after {SCRAPER_DEADLINE_S:g}s", fetched_at=now_utc(),
        )
    except Exception as e:
        logger.exception("scraper %s threw: %s", scraper.name, e)
        listing = Listing(
            dealer=scraper.name, status="error",
            error=f"{e.__class__.__name__}: {e}", fetched_at=now_utc(),
        )
    duration_ms = (time.monotonic() - started) * 1000
    return listing, duration_ms


def _sort_key(li: Listing) -> tuple[int, float]:
    if li.status == "ok" and li.price_dkk is not None:
        return (0, li.price_dkk)
    return (1, float("inf"))


async def run(size_g: float) -> PriceResponse:
    started = time.monotonic()
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        # No outer wait_for: each scraper bounds itself, spot/fx use httpx
        # timeouts and have their own fallbacks. A single slow component
        # cannot cancel the others.
        scraper_results, spot_per_g_usd, (fx_rates, fx_stale) = await asyncio.gather(
            asyncio.gather(*[_safe_fetch(s, size_g, client) for s in ALL_SCRAPERS]),
            fetch_spot_usd_per_gram(client),
            fetch_usd_to(client),
        )
    listings = [r[0] for r in scraper_results]
    durations_ms = {r[0].dealer: r[1] for r in scraper_results}

    spot: SpotPrice | None = None
    if spot_per_g_usd is not None:
        spot = SpotPrice(
            gold=PerCurrency(
                per_gram_eur=round(spot_per_g_usd["gold"] * fx_rates["EUR"], 2),
                per_gram_dkk=round(spot_per_g_usd["gold"] * fx_rates["DKK"], 2),
            ),
            silver=PerCurrency(
                per_gram_eur=round(spot_per_g_usd["silver"] * fx_rates["EUR"], 4),
                per_gram_dkk=round(spot_per_g_usd["silver"] * fx_rates["DKK"], 4),
            ),
        )

    if spot is not None:
        ref_dkk_per_g = spot.gold.per_gram_dkk
        for li in listings:
            if li.status == "ok" and li.price_dkk is not None and ref_dkk_per_g > 0:
                ref_total = ref_dkk_per_g * size_g
                li.premium_pct = round((li.price_dkk - ref_total) / ref_total * 100, 2)

    listings.sort(key=_sort_key)

    total_ms = (time.monotonic() - started) * 1000
    logger.info(
        "prices_request %s",
        json.dumps({
            "event": "prices_request",
            "size_g": size_g,
            "duration_ms": round(total_ms),
            "spot_ok": spot is not None,
            "fx_stale": fx_stale,
            "dealers": [
                {
                    "name": li.dealer,
                    "status": li.status,
                    "price_dkk": li.price_dkk,
                    "premium_pct": li.premium_pct,
                    "duration_ms": round(durations_ms[li.dealer]),
                }
                for li in listings
            ],
        }),
    )

    return PriceResponse(
        size_g=size_g,
        fetched_at=now_utc(),
        spot=spot,
        fx_stale=fx_stale,
        listings=listings,
    )
