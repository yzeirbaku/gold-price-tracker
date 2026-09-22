"""Lightweight smoke tests for non-CRUD endpoints: /, /coins rate limit.

The /coins fan-out is neutralised in `mock_external` (empty scraper registry
plus stubbed spot/FX), so these exercise the rate-limit dependency without
touching the network.
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_root_returns_health_ok(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_coins_rate_limit_returns_429_on_burst(client):
    """Two rapid /coins calls from the same client should yield 200 then 429.

    Note for future-you: an earlier version of this test did
    `monkeypatch.setattr(main_module, "get_coins", stub)`. That never worked
    — FastAPI captures the endpoint function object when `@app.get("/coins")`
    runs at import time, so rebinding the module attribute leaves the route
    pointing at the original. The real fan-out ran every time, taking about
    as long as the limiter's 5s min_interval, and the test passed or failed
    on network latency. Stub what the handler *calls*, never the handler.
    """
    headers = {"X-API-Key": "test-api-key", "X-Forwarded-For": "9.9.9.9"}
    r1 = await client.get("/coins", headers=headers)
    assert r1.status_code == 200, r1.text
    r2 = await client.get("/coins", headers=headers)
    assert r2.status_code == 429
    assert "Retry-After" in r2.headers


async def test_coins_rate_limit_is_per_ip(client):
    """Different IPs get independent buckets."""
    base = {"X-API-Key": "test-api-key"}
    r1 = await client.get("/coins", headers={**base, "X-Forwarded-For": "1.1.1.1"})
    r2 = await client.get("/coins", headers={**base, "X-Forwarded-For": "2.2.2.2"})
    assert r1.status_code == 200
    assert r2.status_code == 200


async def test_coins_does_not_touch_the_network_in_tests(client):
    """Guards the stubbing above. If someone drops the ALL_COIN_SCRAPERS or
    spot/FX patches from mock_external, this endpoint starts making ~8 live
    HTTP calls per test and the rate-limit tests go flaky again."""
    r = await client.get("/coins", headers={"X-API-Key": "test-api-key"})
    assert r.status_code == 200
    body = r.json()
    assert body["listings"] == []
    assert body["fetched_at"]
