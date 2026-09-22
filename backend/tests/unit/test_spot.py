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


@pytest.mark.parametrize(
    "payload",
    [
        {"price": None, "symbol": "XAU"},   # 200 with a null price
        {"symbol": "XAU"},                  # key absent entirely
        ["unexpected", "shape"],            # list where an object belongs
    ],
    ids=["null-price", "missing-key", "list-body"],
)
@pytest.mark.asyncio
async def test_fetch_spot_returns_none_on_malformed_200(payload) -> None:
    """A 200 carrying junk must degrade to None, not raise.

    float(None) raises TypeError and a list subscript raises TypeError too.
    Neither is an httpx error, so before this was guarded the exception
    escaped a function documented as "or None on failure" — which made
    /spot, /prices, /coins and the /snapshot cron all 500 together.
    """
    resp = AsyncMock()
    resp.json = lambda: payload
    resp.raise_for_status = lambda: None
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = [resp, resp]

    assert await fetch_spot_usd_per_gram(mock_client) is None
