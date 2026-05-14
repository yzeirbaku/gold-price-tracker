import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.models import CoinListing, Listing
from app.orchestrator import (
    BAR_PREMIUM_BOUNDS_PCT,
    COIN_PREMIUM_BOUNDS_PCT,
    flag_bar_premium_outliers,
    flag_coin_premium_outliers,
    run,
)
from app.scrapers.base import now_utc


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


def _bar(dealer: str, price: float, premium: float | None, status: str = "ok") -> Listing:
    return Listing(
        dealer=dealer, status=status,  # type: ignore[arg-type]
        price_dkk=price, premium_pct=premium, in_stock=True,
        url="https://example.com/x", fetched_at=now_utc(),
    )


def _coin(
    dealer: str, price: float | None, fine_g: float, status: str = "ok",
) -> CoinListing:
    return CoinListing(
        dealer=dealer, status=status,  # type: ignore[arg-type]
        coin_type="Krugerrand", size_label="1/2 oz",
        gross_weight_g=fine_g / 0.9167, purity=0.9167, fine_gold_g=fine_g,
        price_dkk=price,
        url="https://example.com/c", fetched_at=now_utc(),
    )


# --- flag_bar_premium_outliers --------------------------------------------


def test_bar_outlier_flips_status_and_clears_price() -> None:
    bad = _bar("Bogus", price=100.0, premium=-50.0)
    flag_bar_premium_outliers([bad], size_g=10.0)
    assert bad.status == "error"
    assert bad.price_dkk is None
    assert bad.premium_pct is None
    assert bad.error is not None and "out of bar bounds" in bad.error


def test_bar_outlier_leaves_in_range_alone() -> None:
    ok = _bar("Normal", price=9750.0, premium=7.5)
    flag_bar_premium_outliers([ok], size_g=10.0)
    assert ok.status == "ok"
    assert ok.price_dkk == 9750.0
    assert ok.premium_pct == 7.5


def test_bar_outlier_floor_catches_buy_back_rate() -> None:
    # Buy-back rates are typically spot-1% to spot-3%, i.e. negative premium.
    # The 0% floor must reject them — a -1% floor would let them slip.
    buy_back = _bar("ConfusedScraper", price=9500.0, premium=-2.5)
    flag_bar_premium_outliers([buy_back], size_g=10.0)
    assert buy_back.status == "error"


def test_bar_outlier_ceiling() -> None:
    # 80% ceiling — above is "scraper grabbed wrong field" territory.
    too_high = _bar("Multipack", price=20000.0, premium=100.0)
    flag_bar_premium_outliers([too_high], size_g=10.0)
    assert too_high.status == "error"


def test_bar_outlier_skips_non_ok_status() -> None:
    err = _bar("Down", price=0.0, premium=None, status="error")
    flag_bar_premium_outliers([err], size_g=10.0)
    assert err.status == "error"  # unchanged (was already error)


def test_bar_outlier_skips_none_premium() -> None:
    # No spot available → premium never computed → guard is a no-op.
    no_premium = _bar("NoSpot", price=9750.0, premium=None)
    flag_bar_premium_outliers([no_premium], size_g=10.0)
    assert no_premium.status == "ok"
    assert no_premium.price_dkk == 9750.0


def test_bar_outlier_constants_are_what_we_expect() -> None:
    # Lock the floor at 0%: if anyone ever loosens this without thinking,
    # this assertion will scream. See orchestrator.py rationale comment.
    assert BAR_PREMIUM_BOUNDS_PCT[0] == 0.0
    assert BAR_PREMIUM_BOUNDS_PCT[1] >= 50.0


# --- flag_coin_premium_outliers -------------------------------------------


def test_coin_outlier_flags_above_ceiling() -> None:
    # 15g fine at spot=1000 DKK/g → ref=15000. price=40000 → premium=166% (>120%).
    c = _coin("Bogus", price=40000.0, fine_g=15.0)
    flag_coin_premium_outliers([c], spot_gold_dkk_per_g=1000.0)
    assert c.status == "error"
    assert c.price_dkk is None


def test_coin_outlier_leaves_high_but_legit_premium() -> None:
    # Fractional coins legitimately premium 50-80% — must NOT be flagged.
    # 3.1g at spot=1000 → ref=3100. price=5000 → premium=61.3%.
    c = _coin("Realistic", price=5000.0, fine_g=3.1)
    flag_coin_premium_outliers([c], spot_gold_dkk_per_g=1000.0)
    assert c.status == "ok"
    assert c.price_dkk == 5000.0


def test_coin_outlier_floor_at_zero() -> None:
    # Below spot is impossible for online retail — flag.
    # 10g at spot=1000 → ref=10000. price=9500 → premium=-5%.
    c = _coin("BuyBack", price=9500.0, fine_g=10.0)
    flag_coin_premium_outliers([c], spot_gold_dkk_per_g=1000.0)
    assert c.status == "error"


def test_coin_outlier_no_spot_is_noop() -> None:
    # When spot is unavailable we can't compute premium, so the guard
    # leaves listings untouched (premium would have been None anyway in the
    # /coins endpoint, but raw price stays intact for caller decisions).
    c = _coin("UnchangedNoSpot", price=999_999_999.0, fine_g=10.0)
    flag_coin_premium_outliers([c], spot_gold_dkk_per_g=None)
    assert c.status == "ok"
    assert c.price_dkk == 999_999_999.0


def test_coin_outlier_idempotent_on_already_flagged() -> None:
    # Calling the helper twice on the same list is safe — flipped rows have
    # status != "ok" and are skipped. Matters because /snapshot may guard,
    # then the same batch reference flows through downstream code.
    c = _coin("Bogus", price=40000.0, fine_g=15.0)
    flag_coin_premium_outliers([c], spot_gold_dkk_per_g=1000.0)
    flag_coin_premium_outliers([c], spot_gold_dkk_per_g=1000.0)
    assert c.status == "error"


def test_coin_outlier_constants_are_what_we_expect() -> None:
    assert COIN_PREMIUM_BOUNDS_PCT[0] == 0.0
    assert COIN_PREMIUM_BOUNDS_PCT[1] >= 80.0


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
