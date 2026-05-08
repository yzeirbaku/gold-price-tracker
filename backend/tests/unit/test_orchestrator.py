import asyncio
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


class HangingScraper:
    """Sleeps longer than the per-scraper deadline to exercise the timeout path."""

    def __init__(self, name: str, sleep_s: float) -> None:
        self.name = name
        self.base_url = "https://example.com"
        self._sleep_s = sleep_s

    async def fetch(self, size_g: float, client) -> Listing | None:
        await asyncio.sleep(self._sleep_s)
        from app.scrapers.base import now_utc
        return Listing(
            dealer=self.name, status="ok",
            price_dkk=1.0, in_stock=True,
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


@pytest.mark.asyncio
async def test_run_returns_partial_when_one_scraper_times_out() -> None:
    # Compress the deadline so the test stays fast. The fast scraper still
    # resolves; the hung one must come back as a timeout error, not poison
    # the whole response.
    scrapers = [FakeScraper("Fast", 2500.0), HangingScraper("Slow", sleep_s=2.0)]
    spot = AsyncMock(return_value={"gold": 70.0, "silver": 1.0})
    fx = AsyncMock(return_value=({"EUR": 0.92, "DKK": 6.85}, False))

    with patch("app.orchestrator.ALL_SCRAPERS", scrapers), \
         patch("app.orchestrator.SCRAPER_DEADLINE_S", 0.2), \
         patch("app.orchestrator.fetch_spot_usd_per_gram", spot), \
         patch("app.orchestrator.fetch_usd_to", fx):
        resp = await run(size_g=5.0)

    by_dealer = {li.dealer: li for li in resp.listings}
    assert by_dealer["Fast"].status == "ok"
    assert by_dealer["Slow"].status == "error"
    assert by_dealer["Slow"].error is not None
    assert "timeout" in by_dealer["Slow"].error


@pytest.mark.asyncio
async def test_run_emits_structured_log_line(caplog: pytest.LogCaptureFixture) -> None:
    import json as _json

    scrapers = [FakeScraper("A", 2500.0), FakeScraper("B", 3000.0)]
    spot = AsyncMock(return_value={"gold": 70.0, "silver": 1.0})
    fx = AsyncMock(return_value=({"EUR": 0.92, "DKK": 6.85}, False))

    with caplog.at_level("INFO", logger="app.orchestrator"), \
         patch("app.orchestrator.ALL_SCRAPERS", scrapers), \
         patch("app.orchestrator.fetch_spot_usd_per_gram", spot), \
         patch("app.orchestrator.fetch_usd_to", fx):
        await run(size_g=5.0)

    # Find the prices_request line and parse its JSON payload.
    lines = [r.message for r in caplog.records if "prices_request" in r.message]
    assert lines, "expected a prices_request log line"
    payload = _json.loads(lines[-1].split("prices_request ", 1)[1])
    assert payload["size_g"] == 5.0
    assert payload["spot_ok"] is True
    assert payload["fx_stale"] is False
    assert {d["name"] for d in payload["dealers"]} == {"A", "B"}
    assert all("duration_ms" in d for d in payload["dealers"])
