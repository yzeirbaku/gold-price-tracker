"""Snapshot retention. DB-backed — needs TEST_DATABASE_URL.

The spot_snapshots exclusion has its own test because it is a correctness
constraint, not a preference: /portfolio/history reads that table back to the
user's first purchase, so pruning it silently truncates the portfolio chart.
"""
import os
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.db import SCHEMA_SQL
from app.retention import (
    DELETE_BATCH_SIZE,
    PRUNABLE_TABLES,
    RETENTION_DAYS,
    prune_snapshots,
)

LOCAL_DSN = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    LOCAL_DSN is None,
    reason="TEST_DATABASE_URL not set; requires local Postgres",
)

TABLES = ("bar_snapshots", "coin_snapshots", "spot_snapshots")


@pytest.fixture
async def conn():
    c = await asyncpg.connect(LOCAL_DSN)
    await c.execute(SCHEMA_SQL)
    for t in TABLES:
        await c.execute(f"DELETE FROM {t}")
    try:
        yield c
    finally:
        for t in TABLES:
            await c.execute(f"DELETE FROM {t}")
        await c.close()


async def _add_bar(c, age_days: float) -> None:
    await c.execute(
        "INSERT INTO bar_snapshots (fetched_at, dealer, size_g, status, price_dkk, "
        "spot_gold_dkk_per_g) VALUES ($1, 'Tavex', 10, 'ok', 7000, 650)",
        datetime.now(tz=UTC) - timedelta(days=age_days),
    )


async def _add_coin(c, age_days: float) -> None:
    await c.execute(
        "INSERT INTO coin_snapshots (fetched_at, dealer, coin_type, fine_gold_g, "
        "status, price_dkk, spot_gold_dkk_per_g) "
        "VALUES ($1, 'Tavex', 'Krugerrand', 15.5517, 'ok', 11000, 650)",
        datetime.now(tz=UTC) - timedelta(days=age_days),
    )


async def _add_spot(c, age_days: float) -> None:
    await c.execute(
        "INSERT INTO spot_snapshots (fetched_at, gold_dkk_per_g, silver_dkk_per_g) "
        "VALUES ($1, 650, 8)",
        datetime.now(tz=UTC) - timedelta(days=age_days),
    )


async def _count(c, table: str) -> int:
    return await c.fetchval(f"SELECT COUNT(*) FROM {table}")


def test_spot_snapshots_is_not_prunable() -> None:
    """/portfolio/history?range=all reads spot_snapshots back to the user's
    first purchase. Adding it to PRUNABLE_TABLES would silently truncate the
    portfolio value chart."""
    assert "spot_snapshots" not in PRUNABLE_TABLES
    assert set(PRUNABLE_TABLES) == {"bar_snapshots", "coin_snapshots"}


def test_retention_window_clears_every_bounded_reader() -> None:
    """30d is the widest UI lookback (/history, /context); the monthly report
    cron reaches ~32 days back. Retention must sit clear of both."""
    assert RETENTION_DAYS >= 60


@pytest.mark.asyncio
async def test_prunes_only_rows_past_the_window(conn) -> None:
    await _add_bar(conn, RETENTION_DAYS + 5)
    await _add_bar(conn, RETENTION_DAYS - 5)
    await _add_coin(conn, RETENTION_DAYS + 5)
    await _add_coin(conn, 1)

    removed = await prune_snapshots(conn)

    assert removed == {"bar_snapshots": 1, "coin_snapshots": 1}
    assert await _count(conn, "bar_snapshots") == 1
    assert await _count(conn, "coin_snapshots") == 1


@pytest.mark.asyncio
async def test_never_touches_spot_snapshots(conn) -> None:
    """The guard that matters. Ancient spot rows must survive a prune."""
    await _add_spot(conn, RETENTION_DAYS + 400)
    await _add_spot(conn, 1)
    await _add_bar(conn, RETENTION_DAYS + 400)

    removed = await prune_snapshots(conn)

    assert "spot_snapshots" not in removed
    assert await _count(conn, "spot_snapshots") == 2, "portfolio history depends on these"
    assert await _count(conn, "bar_snapshots") == 0


@pytest.mark.asyncio
async def test_is_idempotent(conn) -> None:
    """Runs on every /snapshot tick, so a no-op second pass must be free."""
    await _add_bar(conn, RETENTION_DAYS + 1)
    assert (await prune_snapshots(conn))["bar_snapshots"] == 1
    assert (await prune_snapshots(conn))["bar_snapshots"] == 0


@pytest.mark.asyncio
async def test_batches_past_the_batch_size(conn) -> None:
    """The first prod run had 265k expired coin rows. One unbounded DELETE
    would blow the pool's command_timeout, so the loop must keep going past
    DELETE_BATCH_SIZE rather than stopping after one statement."""
    old = datetime.now(tz=UTC) - timedelta(days=RETENTION_DAYS + 1)
    n = DELETE_BATCH_SIZE + 25
    await conn.executemany(
        "INSERT INTO bar_snapshots (fetched_at, dealer, size_g, status, price_dkk, "
        "spot_gold_dkk_per_g) VALUES ($1, 'Tavex', 10, 'ok', 7000, 650)",
        [(old,) for _ in range(n)],
    )
    await _add_bar(conn, 1)

    removed = await prune_snapshots(conn)

    assert removed["bar_snapshots"] == n
    assert await _count(conn, "bar_snapshots") == 1


@pytest.mark.asyncio
async def test_rejects_a_non_whitelisted_table(conn) -> None:
    from app.retention import _prune_table

    with pytest.raises(ValueError, match="not prunable"):
        await _prune_table(conn, "spot_snapshots", RETENTION_DAYS)
    with pytest.raises(ValueError, match="not prunable"):
        await _prune_table(conn, "purchases; DROP TABLE users", RETENTION_DAYS)
