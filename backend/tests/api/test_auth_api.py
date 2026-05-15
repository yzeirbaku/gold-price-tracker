"""End-to-end tests for /auth/*: request-link, verify, /me, logout, rate limit.

These exercise the bearer-token wiring (Authorization header parsing,
session lookup, sliding TTL) that no unit test can catch — a typo in
require_session would slip through unit coverage but fail here.
"""
from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.asyncio


async def test_request_link_returns_204_and_calls_send(client, mock_external):
    r = await client.post("/auth/request-link", json={"email": "alice@example.com"})
    assert r.status_code == 204
    assert len(mock_external["magic_links"]) == 1
    assert mock_external["magic_links"][0]["to"] == "alice@example.com"
    assert mock_external["magic_links"][0]["token"]  # raw token captured


async def test_request_link_normalizes_email_lowercase(client, mock_external, pool):
    await client.post("/auth/request-link", json={"email": "Alice@Example.COM"})
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT email FROM magic_links LIMIT 1")
    assert row["email"] == "alice@example.com"


async def test_request_link_rate_limited_per_email(client, mock_external):
    """RATE_LIMIT_PER_EMAIL = 3 per 10 minutes."""
    for _ in range(3):
        r = await client.post("/auth/request-link", json={"email": "bob@example.com"})
        assert r.status_code == 204
    r = await client.post("/auth/request-link", json={"email": "bob@example.com"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers


async def test_request_link_invalid_email_rejected(client):
    r = await client.post("/auth/request-link", json={"email": "not-an-email"})
    assert r.status_code == 422


async def test_verify_returns_session_token(client, mock_external):
    await client.post("/auth/request-link", json={"email": "carol@example.com"})
    token = mock_external["magic_links"][-1]["token"]

    r = await client.post("/auth/verify", json={"token": token})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "carol@example.com"
    assert body["token"]
    assert body["user_id"]


async def test_verify_rejects_invalid_token(client):
    r = await client.post("/auth/verify", json={"token": "nope-not-a-real-token"})
    assert r.status_code == 400


async def test_verify_is_single_use(client, mock_external):
    await client.post("/auth/request-link", json={"email": "dan@example.com"})
    token = mock_external["magic_links"][-1]["token"]
    r1 = await client.post("/auth/verify", json={"token": token})
    assert r1.status_code == 200
    r2 = await client.post("/auth/verify", json={"token": token})
    assert r2.status_code == 400  # used_at is set; lookup misses


async def test_verify_rejects_expired_token(client, mock_external, pool):
    await client.post("/auth/request-link", json={"email": "eve@example.com"})
    token = mock_external["magic_links"][-1]["token"]
    # Backdate the magic_link's expires_at so it's already past.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE magic_links SET expires_at = $1",
            datetime.now(UTC) - timedelta(minutes=1),
        )
    r = await client.post("/auth/verify", json={"token": token})
    assert r.status_code == 400


async def test_me_requires_bearer_token(client):
    r = await client.get("/auth/me")
    assert r.status_code == 401


async def test_me_returns_user_for_valid_token(client, mock_external):
    await client.post("/auth/request-link", json={"email": "frank@example.com"})
    token = mock_external["magic_links"][-1]["token"]
    verify = await client.post("/auth/verify", json={"token": token})
    session_token = verify.json()["token"]
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {session_token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "frank@example.com"


async def test_me_rejects_malformed_authorization_header(client):
    r = await client.get("/auth/me", headers={"Authorization": "Basic xyz"})
    assert r.status_code == 401
    r = await client.get("/auth/me", headers={"Authorization": "Bearer"})
    assert r.status_code == 401


async def test_logout_invalidates_session(client, mock_external):
    await client.post("/auth/request-link", json={"email": "gina@example.com"})
    token = mock_external["magic_links"][-1]["token"]
    session = (await client.post("/auth/verify", json={"token": token})).json()["token"]
    headers = {"Authorization": f"Bearer {session}"}

    assert (await client.get("/auth/me", headers=headers)).status_code == 200
    assert (await client.post("/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/auth/me", headers=headers)).status_code == 401
