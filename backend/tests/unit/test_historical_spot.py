"""Tests for the yfinance-backed historical spot fetcher in app/spot.py."""
from datetime import date
from unittest.mock import patch

import pytest

from app import spot
from app.spot import (
    OUNCE_TO_GRAM,
    HistoricalSpotUnavailable,
    fetch_historical_usd_per_gram,
)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    spot._HISTORICAL_USD_PER_GRAM_CACHE.clear()
    yield
    spot._HISTORICAL_USD_PER_GRAM_CACHE.clear()


def _stub_yf_closes(closes_by_date: dict[str, float]):
    """Patch _yf_closes to return the given map without hitting the network."""
    def stub(ticker: str, start: date, end: date) -> dict[str, float]:
        return dict(closes_by_date)
    return patch("app.spot._yf_closes", side_effect=stub)


@pytest.mark.asyncio
async def test_returns_per_gram_for_exact_date() -> None:
    # 3110.34768 USD/oz → exactly 100 USD/g
    with _stub_yf_closes({"2026-01-15": 3110.34768}):
        result = await fetch_historical_usd_per_gram("gold", date(2026, 1, 15))
    assert result == pytest.approx(100.0, abs=1e-4)


@pytest.mark.asyncio
async def test_walks_back_through_weekend() -> None:
    # Sunday 2026-01-18 requested; only Friday 2026-01-16 has data.
    with _stub_yf_closes({"2026-01-16": 3110.34768}):
        result = await fetch_historical_usd_per_gram("gold", date(2026, 1, 18))
    assert result == pytest.approx(100.0, abs=1e-4)


@pytest.mark.asyncio
async def test_raises_when_window_returns_no_data() -> None:
    with _stub_yf_closes({}):
        with pytest.raises(HistoricalSpotUnavailable):
            await fetch_historical_usd_per_gram("gold", date(2026, 1, 15))


@pytest.mark.asyncio
async def test_picks_latest_available_when_multiple_in_window() -> None:
    # All three dates returned; fetcher should pick on_date (2026-01-15) exactly.
    closes = {
        "2026-01-13": 3000.0,
        "2026-01-14": 3050.0,
        "2026-01-15": 3110.34768,
    }
    with _stub_yf_closes(closes):
        result = await fetch_historical_usd_per_gram("gold", date(2026, 1, 15))
    assert result == pytest.approx(100.0, abs=1e-4)


@pytest.mark.asyncio
async def test_caches_repeat_lookups_by_metal_and_date() -> None:
    calls = {"n": 0}

    def stub(ticker: str, start: date, end: date) -> dict[str, float]:
        calls["n"] += 1
        return {"2026-01-15": 3110.34768}

    with patch("app.spot._yf_closes", side_effect=stub):
        await fetch_historical_usd_per_gram("gold", date(2026, 1, 15))
        await fetch_historical_usd_per_gram("gold", date(2026, 1, 15))
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_different_metals_cache_separately() -> None:
    calls: list[str] = []

    def stub(ticker: str, start: date, end: date) -> dict[str, float]:
        calls.append(ticker)
        return {"2026-01-15": 3110.34768}

    with patch("app.spot._yf_closes", side_effect=stub):
        await fetch_historical_usd_per_gram("gold", date(2026, 1, 15))
        await fetch_historical_usd_per_gram("silver", date(2026, 1, 15))
    assert calls == ["GC=F", "SI=F"]


@pytest.mark.asyncio
async def test_yfinance_exception_surfaces_as_unavailable() -> None:
    def stub(ticker: str, start: date, end: date) -> dict[str, float]:
        raise RuntimeError("yahoo flapped")

    with patch("app.spot._yf_closes", side_effect=stub):
        with pytest.raises(HistoricalSpotUnavailable):
            await fetch_historical_usd_per_gram("gold", date(2026, 1, 15))


def test_yf_ticker_map_uses_futures_symbols() -> None:
    assert spot._YF_TICKER == {"gold": "GC=F", "silver": "SI=F"}


def test_ounce_constant() -> None:
    assert OUNCE_TO_GRAM == pytest.approx(31.1034768)
