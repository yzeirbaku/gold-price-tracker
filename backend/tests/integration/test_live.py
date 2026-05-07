import httpx
import pytest

from app.scrapers.base import DEFAULT_HEADERS
from app.scrapers.registry import ALL_SCRAPERS


@pytest.mark.asyncio
@pytest.mark.parametrize("scraper", ALL_SCRAPERS, ids=lambda s: s.name)
async def test_dealer_returns_a_price_live(scraper) -> None:
    """Live-fetch the dealer and assert a parseable price for one of 2.5/5/10/20 g.

    The canary is parser correctness, not dealer inventory: a price found on an
    out-of-stock card still proves the selectors work. If every size returns
    error / unavailable / no price at all, the parser has likely gone stale.
    """
    found_price = False
    last_status = None
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        for size_g in (2.5, 5.0, 10.0, 20.0):
            listing = await scraper.fetch(size_g, client)
            if listing is None:
                continue
            last_status = listing.status
            if (
                listing.status in ("ok", "out_of_stock")
                and listing.price_dkk
                and listing.price_dkk > 0
            ):
                found_price = True
                break
    assert found_price, (
        f"{scraper.name}: no parseable price for any of 2.5/5/10/20g "
        f"(last_status={last_status})"
    )
