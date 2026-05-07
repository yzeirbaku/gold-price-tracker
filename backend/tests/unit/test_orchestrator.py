from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Listing
from app.orchestrator import run


class FakeScraper:
    def __init__(self, name: str, price: float | None) -> None:
        self.name = name
        self.base_url = "https://example.com"
        self._price = price

    async def fetch(self, size_g: float, client) -> Listing | None:
        from app.scrapers.base import now_utc
        if self._price is None:
            raise RuntimeError("simulated boom")
        return Listing(
            dealer=self.name, status="ok",
            price_dkk=self._price, in_stock=True,
            url="https://example.com/x", fetched_at=now_utc(),
        )


@pytest.mark.asyncio
async def test_run_sorts_listings_cheapest_first() -> None:
    scrapers = [FakeScraper("B", 3000.0), FakeScraper("A", 2500.0)]
    spot = AsyncMock(return_value={"gold": 70.0, "silver": 1.0})
    fx = AsyncMock(return_value=({"EUR": 0.92, "DKK": 6.85}, False))

    with patch("app.orchestrator.ALL_SCRAPERS", scrapers), \
         patch("app.orchestrator.fetch_spot_usd_per_gram", spot), \
         patch("app.orchestrator.fetch_usd_to", fx):
        resp = await run(size_g=5.0)

    prices = [li.price_dkk for li in resp.listings if li.status == "ok"]
    assert prices == sorted(prices)


@pytest.mark.asyncio
async def test_run_keeps_response_when_one_scraper_throws() -> None:
    scrapers = [FakeScraper("Good", 2500.0), FakeScraper("Bad", None)]
    spot = AsyncMock(return_value={"gold": 70.0, "silver": 1.0})
    fx = AsyncMock(return_value=({"EUR": 0.92, "DKK": 6.85}, False))

    with patch("app.orchestrator.ALL_SCRAPERS", scrapers), \
         patch("app.orchestrator.fetch_spot_usd_per_gram", spot), \
         patch("app.orchestrator.fetch_usd_to", fx):
        resp = await run(size_g=5.0)

    statuses = {li.dealer: li.status for li in resp.listings}
    assert statuses["Good"] == "ok"
    assert statuses["Bad"] == "error"
    assert isinstance(resp.fetched_at, datetime)
