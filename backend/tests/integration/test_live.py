import httpx
import pytest

from app.scrapers.base import DEFAULT_HEADERS
from app.scrapers.registry import ALL_COIN_SCRAPERS, ALL_SCRAPERS


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


# Dealers known not to stock bullion coins — a 0-result run here is expected,
# not a regression. Other dealers must produce at least one recognized coin.
_NO_COIN_INVENTORY = {"Plaza", "Jan Jørgensen"}


@pytest.mark.asyncio
@pytest.mark.parametrize("scraper", ALL_COIN_SCRAPERS, ids=lambda s: s.name)
async def test_coin_scraper_produces_listings_or_explicit_empty(scraper) -> None:
    """Live-fetch the dealer's coin category and assert the contract holds.

    For dealers that stock bullion coins, expect at least one recognized
    listing. For dealers that don't, expect an empty list (NOT an exception
    or error row).
    """
    async with httpx.AsyncClient(headers=DEFAULT_HEADERS) as client:
        results = await scraper.fetch(client)
    assert isinstance(results, list)
    error_rows = [r for r in results if r.status == "error"]
    assert not error_rows, (
        f"{scraper.name}: coin scraper returned error rows: "
        f"{[r.error for r in error_rows]}"
    )
    if scraper.name in _NO_COIN_INVENTORY:
        return  # 0 results is fine
    assert any(r.coin_type is not None for r in results), (
        f"{scraper.name}: no recognized coins — registry or selectors stale"
    )
