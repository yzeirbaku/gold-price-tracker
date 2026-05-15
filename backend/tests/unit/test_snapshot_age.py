"""Unit tests for GET /snapshot/age.

This endpoint is the frontend's only signal that the snapshot cron is alive
— if it stalls (outlier-skip, fx_stale-skip, QStash flaky), bar/coin live
fetches keep working but history charts go gappy with no visible warning.
Tests stub the pool so the route can be exercised without a real DB.
"""
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.main import snapshot_age


class _FakeAgeConn:
    """Minimal asyncpg-shaped: fetchval for MAX(fetched_at)."""

    def __init__(self, last_at: datetime | None) -> None:
        self._last_at = last_at

    async def fetchval(self, sql: str, *args):
        assert "MAX(fetched_at)" in sql and "spot_snapshots" in sql
        return self._last_at


class _FakeAgePool:
    def __init__(self, conn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


@pytest.fixture()
def patch_pool(monkeypatch):
    def install(conn) -> None:
        pool = _FakeAgePool(conn) if conn is not None else None

        async def fake() -> object:
            return pool
        monkeypatch.setattr("app.main.get_pool", fake)
    return install


@pytest.mark.asyncio
async def test_snapshot_age_returns_none_when_table_empty(patch_pool) -> None:
    """Fresh DB scenario: no snapshots ever taken. The endpoint must not
    crash on MAX(NULL); it returns null fields so the frontend renders a
    "—" placeholder rather than the "stale" banner."""
    patch_pool(_FakeAgeConn(last_at=None))
    out = await snapshot_age(_=None)
    assert out == {"last_at": None, "age_seconds": None}


@pytest.mark.asyncio
async def test_snapshot_age_reports_seconds_since_last_row(patch_pool) -> None:
    """The age is the wall-clock delta in integer seconds. Used by the
    frontend to decide whether to render a normal "8 min ago" caption or
    the warning state."""
    five_min_ago = datetime.now(UTC) - timedelta(minutes=5)
    patch_pool(_FakeAgeConn(last_at=five_min_ago))
    out = await snapshot_age(_=None)
    assert out["last_at"] == five_min_ago.isoformat()
    # 5 minutes ± a couple of seconds for clock drift between the fixture
    # and the route's `datetime.now(UTC)` call.
    assert 295 <= out["age_seconds"] <= 305


@pytest.mark.asyncio
async def test_snapshot_age_503_when_pool_unavailable(patch_pool) -> None:
    """No DATABASE_URL configured. Returns 503 rather than a 500 so a misread
    deploy config doesn't look like a code crash."""
    from fastapi import HTTPException

    patch_pool(None)
    with pytest.raises(HTTPException) as exc:
        await snapshot_age(_=None)
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_snapshot_age_handles_very_old_snapshot(patch_pool) -> None:
    """If the cron has been down for hours, age_seconds is large but still
    a clean int — no overflow, no negative weirdness. Frontend renders a
    "stale" warning regardless of magnitude."""
    long_ago = datetime.now(UTC) - timedelta(hours=6)
    patch_pool(_FakeAgeConn(last_at=long_ago))
    out = await snapshot_age(_=None)
    assert out["age_seconds"] >= 21600  # 6h in seconds, allow drift
