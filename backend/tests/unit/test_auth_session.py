"""Pure-function tests for auth_session helpers. Route-level tests live in
integration (require DB) and are out of scope for CI."""
import hashlib
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.auth_session import (
    RATE_LIMIT_PER_EMAIL,
    RATE_LIMIT_PER_IP,
    AuthedUser,
    _build_magic_link_url,
    _check_rate_limit,
    _client_ip,
    _extract_bearer,
    _hash_token,
    _resolve_session,
)


def test_hash_token_is_sha256_of_utf8_bytes() -> None:
    raw = "abc"
    expected = hashlib.sha256(b"abc").digest()
    assert _hash_token(raw) == expected


def test_hash_token_is_deterministic() -> None:
    assert _hash_token("xyz") == _hash_token("xyz")


def test_hash_token_differs_for_different_inputs() -> None:
    assert _hash_token("a") != _hash_token("b")


def test_extract_bearer_returns_token() -> None:
    assert _extract_bearer("Bearer abc123") == "abc123"


def test_extract_bearer_is_case_insensitive_on_scheme() -> None:
    assert _extract_bearer("bearer abc123") == "abc123"
    assert _extract_bearer("BEARER abc123") == "abc123"


def test_extract_bearer_returns_none_for_other_schemes() -> None:
    assert _extract_bearer("Basic abc123") is None
    assert _extract_bearer("Token abc123") is None


def test_extract_bearer_returns_none_for_none_or_empty() -> None:
    assert _extract_bearer(None) is None
    assert _extract_bearer("") is None
    assert _extract_bearer("Bearer ") is None


def test_extract_bearer_strips_whitespace_from_token() -> None:
    assert _extract_bearer("Bearer  spaced  ") == "spaced"


def test_client_ip_from_x_forwarded_for_takes_first() -> None:
    request = MagicMock()
    request.headers.get.return_value = "203.0.113.5, 10.0.0.1"
    assert _client_ip(request) == "203.0.113.5"


def test_client_ip_falls_back_to_request_client_when_no_header() -> None:
    request = MagicMock()
    request.headers.get.return_value = None
    request.client.host = "192.168.1.10"
    assert _client_ip(request) == "192.168.1.10"


def test_client_ip_none_when_no_client_and_no_header() -> None:
    request = MagicMock()
    request.headers.get.return_value = None
    request.client = None
    assert _client_ip(request) is None


def test_build_magic_link_url_strips_trailing_slash_and_uses_fragment() -> None:
    with patch.dict(os.environ, {"MAGIC_LINK_BASE_URL": "https://app.example.com/"}):
        url = _build_magic_link_url("MYTOKEN")
        assert url == "https://app.example.com/#auth=MYTOKEN"


def test_build_magic_link_url_raises_when_env_missing() -> None:
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(HTTPException) as exc:
            _build_magic_link_url("MYTOKEN")
        assert exc.value.status_code == 500


def _fake_conn(email_count: int = 0, ip_count: int = 0):
    """asyncpg-shaped fake. fetchval returns email_count on the first call,
    ip_count on the second — matching the order the rate-limit checks
    happen in _check_rate_limit."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=[email_count, ip_count])
    return conn


@pytest.mark.asyncio
async def test_rate_limit_allows_when_both_counts_under_caps() -> None:
    conn = _fake_conn(email_count=RATE_LIMIT_PER_EMAIL - 1, ip_count=RATE_LIMIT_PER_IP - 1)
    await _check_rate_limit(conn, "a@example.com", "203.0.113.5")


@pytest.mark.asyncio
async def test_rate_limit_rejects_when_email_at_cap() -> None:
    conn = _fake_conn(email_count=RATE_LIMIT_PER_EMAIL, ip_count=0)
    with pytest.raises(HTTPException) as exc:
        await _check_rate_limit(conn, "a@example.com", "203.0.113.5")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


@pytest.mark.asyncio
async def test_rate_limit_rejects_when_ip_at_cap() -> None:
    # email count under cap, IP count at cap
    conn = _fake_conn(email_count=0, ip_count=RATE_LIMIT_PER_IP)
    with pytest.raises(HTTPException) as exc:
        await _check_rate_limit(conn, "a@example.com", "203.0.113.5")
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_skips_ip_check_when_ip_is_none() -> None:
    # IP query should never fire — only fetchval call is for email.
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=0)
    await _check_rate_limit(conn, "a@example.com", None)
    assert conn.fetchval.await_count == 1


# --- _resolve_session -----------------------------------------------------
#
# The bearer-token resolution path. Failure modes matter — silent None on
# bad inputs (so a stale localStorage token simply becomes "not logged in")
# AND a sliding-update side effect on the happy path (so active users don't
# get logged out at exactly the 90-day mark).

class _FakeSessionConn:
    def __init__(self, row: dict | None) -> None:
        self._row = row
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        # Mirror real shape — caller cares about row presence + columns.
        return self._row

    async def execute(self, sql: str, *args) -> None:
        self.executed.append((sql, args))


class _FakeSessionPool:
    def __init__(self, conn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


@pytest.mark.asyncio
async def test_resolve_session_returns_none_for_none_token() -> None:
    """No Authorization header at all → no DB lookup, no surprise."""
    assert await _resolve_session(None) is None


@pytest.mark.asyncio
async def test_resolve_session_returns_none_for_empty_token() -> None:
    assert await _resolve_session("") is None


@pytest.mark.asyncio
async def test_resolve_session_returns_none_for_non_uuid_token() -> None:
    """A garbage Bearer header (e.g. attacker tries 'admin' or 'undefined')
    must not crash with a ValueError — quiet None lets the route 401."""
    assert await _resolve_session("not-a-uuid") is None
    assert await _resolve_session("12345") is None


@pytest.mark.asyncio
async def test_resolve_session_returns_none_when_pool_unavailable(monkeypatch) -> None:
    """If get_pool() returns None (DATABASE_URL not configured) we get None
    rather than blowing up. The dependency then 401s — acceptable graceful
    degradation for an unconfigured prod env."""
    async def no_pool() -> None:
        return None
    monkeypatch.setattr("app.auth_session.get_pool", no_pool)
    assert await _resolve_session(str(uuid4())) is None


@pytest.mark.asyncio
async def test_resolve_session_returns_none_when_no_row(monkeypatch) -> None:
    """Valid UUID format but session not found (expired beyond 90d, deleted,
    or simply never existed). Backend returns None → require_session 401s."""
    conn = _FakeSessionConn(row=None)
    pool = _FakeSessionPool(conn)

    async def fake_pool():
        return pool
    monkeypatch.setattr("app.auth_session.get_pool", fake_pool)
    assert await _resolve_session(str(uuid4())) is None
    # No UPDATE last_seen_at should have fired for a non-existent session.
    assert conn.executed == []


@pytest.mark.asyncio
async def test_resolve_session_slides_last_seen_at_on_hit(monkeypatch) -> None:
    """The happy path side-effect: every successful resolution must slide
    last_seen_at forward. Otherwise a daily user gets logged out at 90 days
    even though they've been active the whole time."""
    user_id = uuid4()
    session_id = uuid4()
    conn = _FakeSessionConn(row={
        "id": session_id, "user_id": user_id, "email": "user@example.com",
    })
    pool = _FakeSessionPool(conn)

    async def fake_pool():
        return pool
    monkeypatch.setattr("app.auth_session.get_pool", fake_pool)

    user = await _resolve_session(str(session_id))
    assert isinstance(user, AuthedUser)
    assert user.id == user_id
    assert user.email == "user@example.com"
    # Exactly one UPDATE last_seen_at, with our session_id as the arg.
    assert len(conn.executed) == 1
    sql, args = conn.executed[0]
    assert "UPDATE sessions" in sql
    assert "last_seen_at" in sql
    assert args == (session_id,)
