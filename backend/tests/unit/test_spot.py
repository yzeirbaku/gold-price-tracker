from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.spot import OUNCE_TO_GRAM, fetch_spot_usd_per_gram


@pytest.mark.asyncio
async def test_fetch_spot_usd_per_gram_converts_oz_to_gram() -> None:
    fake_payload = {
        "metals": {"gold": 2400.0, "silver": 30.0},
    }
    mock_response = AsyncMock()
    mock_response.json = lambda: fake_payload
    mock_response.raise_for_status = lambda: None

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    with patch.dict("os.environ", {"METALS_DEV_API_KEY": "test-key"}):
        result = await fetch_spot_usd_per_gram(mock_client)

    assert result is not None
    assert result["gold"] == pytest.approx(2400.0 / OUNCE_TO_GRAM)
    assert result["silver"] == pytest.approx(30.0 / OUNCE_TO_GRAM)


@pytest.mark.asyncio
async def test_fetch_spot_returns_none_on_http_error() -> None:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.HTTPError("boom")

    with patch.dict("os.environ", {"METALS_DEV_API_KEY": "test-key"}):
        result = await fetch_spot_usd_per_gram(mock_client)

    assert result is None
