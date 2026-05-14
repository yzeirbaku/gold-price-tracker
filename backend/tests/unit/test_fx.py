from datetime import date
from unittest.mock import AsyncMock

import httpx
import pytest

from app import fx as fx_module
from app.fx import (
    STATIC_FALLBACK,
    HistoricalFxUnavailable,
    fetch_usd_to,
    fetch_usd_to_dkk_on,
)


@pytest.fixture(autouse=True)
def _clear_historical_cache() -> None:
    fx_module._HISTORICAL_FX_CACHE.clear()
    yield
    fx_module._HISTORICAL_FX_CACHE.clear()


@pytest.mark.asyncio
async def test_fetch_usd_to_returns_live_rates() -> None:
    fake_payload = {"rates": {"EUR": 0.92, "DKK": 6.85}}
    mock_response = AsyncMock()
    mock_response.json = lambda: fake_payload
    mock_response.raise_for_status = lambda: None
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    rates, stale = await fetch_usd_to(mock_client)

    assert rates == {"EUR": 0.92, "DKK": 6.85}
    assert stale is False


@pytest.mark.asyncio
async def test_fetch_usd_to_falls_back_on_error() -> None:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.HTTPError("boom")

    rates, stale = await fetch_usd_to(mock_client)

    assert rates == STATIC_FALLBACK
    assert stale is True


# --- fetch_usd_to_dkk_on: historical FX for portfolio writes ---------------


@pytest.mark.asyncio
async def test_historical_fx_returns_rate_for_exact_date() -> None:
    fake = {"rates": {"DKK": 6.4123}}
    resp = AsyncMock()
    resp.status_code = 200
    resp.json = lambda: fake
    resp.raise_for_status = lambda: None
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = resp

    rate = await fetch_usd_to_dkk_on(mock_client, date(2026, 5, 14))
    assert rate == pytest.approx(6.4123)


@pytest.mark.asyncio
async def test_historical_fx_walks_back_through_404() -> None:
    # First two calls return 404 (Frankfurter has no data for those dates,
    # e.g. weekends or holidays), third call returns a rate.
    hit = AsyncMock()
    hit.status_code = 200
    hit.json = lambda: {"rates": {"DKK": 6.55}}
    hit.raise_for_status = lambda: None
    miss = AsyncMock()
    miss.status_code = 404
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = [miss, miss, hit]

    rate = await fetch_usd_to_dkk_on(mock_client, date(2026, 5, 14))
    assert rate == pytest.approx(6.55)
    assert mock_client.get.call_count == 3


@pytest.mark.asyncio
async def test_historical_fx_raises_when_all_dates_fail() -> None:
    # Frankfurter is fully unreachable across the entire walk-back window.
    # We deliberately do NOT fall back to the static rate here — the value
    # would be frozen onto a `purchases` row forever (see CLAUDE.md).
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.HTTPError("flapping")

    with pytest.raises(HistoricalFxUnavailable):
        await fetch_usd_to_dkk_on(mock_client, date(2026, 5, 14))


@pytest.mark.asyncio
async def test_historical_fx_cached_after_first_lookup() -> None:
    resp = AsyncMock()
    resp.status_code = 200
    resp.json = lambda: {"rates": {"DKK": 6.42}}
    resp.raise_for_status = lambda: None
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = resp

    await fetch_usd_to_dkk_on(mock_client, date(2026, 5, 14))
    await fetch_usd_to_dkk_on(mock_client, date(2026, 5, 14))
    assert mock_client.get.call_count == 1  # second call served from cache
