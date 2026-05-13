"""Pure-function tests for auth_session helpers. Route-level tests live in
integration (require DB) and are out of scope for CI."""
import hashlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.auth_session import (
    RATE_LIMIT_PER_EMAIL,
    RATE_LIMIT_PER_IP,
    _build_magic_link_url,
    _check_rate_limit,
    _client_ip,
    _extract_bearer,
    _hash_token,
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
