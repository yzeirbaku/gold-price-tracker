import httpx
import pytest

from app.scrapers.base import DEFAULT_HEADERS
from app.scrapers.registry import ALL_SCRAPERS


@pytest.mark.asyncio
@pytest.mark.parametrize("scraper", ALL_SCRAPERS, ids=lambda s: s.name)
async def test_dealer_returns_a_price_live(scraper) -> None:
    """Hit the real dealer site and assert we extract a numeric price for at least one of 2.5/5/10g.

    Some dealers don't carry every size, and stock fluctuates. The canary is whether
    the parser still finds *any* expected size as a numeric in-stock price. If all
    three return non-ok, the parser has likely gone stale.
    """
    found_ok = False
    last_status = None
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        for size_g in (2.5, 5.0, 10.0):
            listing = await scraper.fetch(size_g, client)
            if listing is None:
                continue
            last_status = listing.status
            if listing.status == "ok" and listing.price_dkk and listing.price_dkk > 0:
                found_ok = True
                break
    assert found_ok, f"{scraper.name}: no in-stock price for any of 2.5/5/10g (last_status={last_status})"
