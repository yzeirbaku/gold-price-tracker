import asyncio
import logging

import httpx

from app.fx import fetch_usd_to
from app.models import Listing, PerCurrency, PriceResponse, SpotPrice
from app.scrapers.base import DEFAULT_HEADERS, now_utc
from app.scrapers.registry import ALL_SCRAPERS
from app.spot import fetch_spot_usd_per_gram

logger = logging.getLogger(__name__)


async def _safe_fetch(scraper, size_g: float, client: httpx.AsyncClient) -> Listing:
    try:
        result = await scraper.fetch(size_g, client)
        if result is None:
            return Listing(
                dealer=scraper.name, status="unavailable",
                error="size not offered", fetched_at=now_utc(),
            )
        return result
    except Exception as e:
        logger.exception("scraper %s threw: %s", scraper.name, e)
        return Listing(
            dealer=scraper.name, status="error",
            error=f"{e.__class__.__name__}: {e}", fetched_at=now_utc(),
        )


def _sort_key(li: Listing) -> tuple[int, float]:
    if li.status == "ok" and li.price_dkk is not None:
        return (0, li.price_dkk)
    return (1, float("inf"))


async def run(size_g: float) -> PriceResponse:
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        scraper_tasks = [_safe_fetch(s, size_g, client) for s in ALL_SCRAPERS]
        spot_task = fetch_spot_usd_per_gram(client)
        fx_task = fetch_usd_to(client)

        results = await asyncio.wait_for(
            asyncio.gather(*scraper_tasks, spot_task, fx_task, return_exceptions=False),
            timeout=12.0,
        )

    listings: list[Listing] = list(results[: len(scraper_tasks)])
    spot_per_g_usd = results[len(scraper_tasks)]
    fx_rates, fx_stale = results[len(scraper_tasks) + 1]

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

    return PriceResponse(
        size_g=size_g,
        fetched_at=now_utc(),
        spot=spot,
        fx_stale=fx_stale,
        listings=listings,
    )
