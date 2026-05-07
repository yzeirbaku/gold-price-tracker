from unittest.mock import AsyncMock

import httpx
import pytest

from app.fx import STATIC_FALLBACK, fetch_usd_to


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
