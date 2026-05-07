from unittest.mock import AsyncMock

import httpx
import pytest

from app.spot import OUNCE_TO_GRAM, fetch_spot_usd_per_gram


@pytest.mark.asyncio
async def test_fetch_spot_usd_per_gram_converts_oz_to_gram() -> None:
    gold_resp = AsyncMock()
    gold_resp.json = lambda: {"price": 2400.0, "symbol": "XAU"}
    gold_resp.raise_for_status = lambda: None
    silver_resp = AsyncMock()
    silver_resp.json = lambda: {"price": 30.0, "symbol": "XAG"}
    silver_resp.raise_for_status = lambda: None

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = [gold_resp, silver_resp]

    result = await fetch_spot_usd_per_gram(mock_client)

    assert result is not None
    assert result["gold"] == pytest.approx(2400.0 / OUNCE_TO_GRAM)
    assert result["silver"] == pytest.approx(30.0 / OUNCE_TO_GRAM)


@pytest.mark.asyncio
async def test_fetch_spot_returns_none_on_http_error() -> None:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.HTTPError("boom")

    result = await fetch_spot_usd_per_gram(mock_client)

    assert result is None
