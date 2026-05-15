"""Lightweight smoke tests for non-CRUD endpoints: /, /coins rate limit.

The full /coins fan-out is not exercised here (it hits real dealers); we
just verify the rate-limit dependency fires correctly when the underlying
handler is stubbed out.
"""
import pytest

pytestmark = pytest.mark.asyncio


async def test_root_returns_health_ok(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_coins_rate_limit_returns_429_on_burst(client, monkeypatch):
    """Two rapid /coins calls from the same client should yield 200 then 429."""
    from app import main as main_module

    async def _empty_coins(*_args, **_kwargs):
        return {"fetched_at": "2026-05-15T00:00:00+00:00", "listings": []}

    # Replace the orchestrator-heavy implementation with a stub so the
    # rate-limit dependency is the only thing under test.
    monkeypatch.setattr(main_module, "get_coins", _empty_coins)

    headers = {"X-API-Key": "test-api-key", "X-Forwarded-For": "9.9.9.9"}
    r1 = await client.get("/coins", headers=headers)
    assert r1.status_code == 200, r1.text
    r2 = await client.get("/coins", headers=headers)
    assert r2.status_code == 429
    assert "Retry-After" in r2.headers


async def test_coins_rate_limit_is_per_ip(client, monkeypatch):
    """Different IPs get independent buckets."""
    from app import main as main_module

    async def _empty_coins(*_args, **_kwargs):
        return {"fetched_at": "2026-05-15T00:00:00+00:00", "listings": []}

    monkeypatch.setattr(main_module, "get_coins", _empty_coins)
    base = {"X-API-Key": "test-api-key"}
    r1 = await client.get("/coins", headers={**base, "X-Forwarded-For": "1.1.1.1"})
    r2 = await client.get("/coins", headers={**base, "X-Forwarded-For": "2.2.2.2"})
    assert r1.status_code == 200
    assert r2.status_code == 200
