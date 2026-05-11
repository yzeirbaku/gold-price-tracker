"""Schema migration verification.

Skips when TEST_DATABASE_URL is unset (CI). When set, exercises both the
fresh-create path and the rename-existing path against a real Postgres.
"""
import os

import asyncpg
import pytest

from app.db import SCHEMA_SQL

LOCAL_DSN = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    LOCAL_DSN is None,
    reason="TEST_DATABASE_URL not set; requires local Postgres",
)


@pytest.mark.asyncio
async def test_fresh_schema_creates_bar_and_coin_tables() -> None:
    conn = await asyncpg.connect(LOCAL_DSN)
    try:
        await conn.execute(
            "DROP TABLE IF EXISTS bar_snapshots, coin_snapshots, "
            "spot_snapshots, dealer_snapshots CASCADE"
        )
        await conn.execute(SCHEMA_SQL)
        tables = {
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            )
        }
        assert "bar_snapshots" in tables
        assert "coin_snapshots" in tables
        assert "spot_snapshots" in tables
        assert "dealer_snapshots" not in tables
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fresh_schema_creates_report_archive() -> None:
    conn = await asyncpg.connect(LOCAL_DSN)
    try:
        await conn.execute(
            "DROP TABLE IF EXISTS bar_snapshots, coin_snapshots, "
            "spot_snapshots, dealer_snapshots, report_archive CASCADE"
        )
        await conn.execute(SCHEMA_SQL)
        tables = {
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            )
        }
        assert "report_archive" in tables

        await conn.execute(
            """
            INSERT INTO report_archive (report_type, period_start, period_end, html)
            VALUES ('weekly', '2026-05-04', '2026-05-10', '<html>1</html>')
            """
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                """
                INSERT INTO report_archive (report_type, period_start, period_end, html)
                VALUES ('weekly', '2026-05-04', '2026-05-10', '<html>2</html>')
                """
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_migration_renames_existing_dealer_snapshots() -> None:
    conn = await asyncpg.connect(LOCAL_DSN)
    try:
        await conn.execute(
            "DROP TABLE IF EXISTS bar_snapshots, coin_snapshots, "
            "spot_snapshots, dealer_snapshots CASCADE"
        )
        await conn.execute(
            """
            CREATE TABLE dealer_snapshots (
                id BIGSERIAL PRIMARY KEY,
                fetched_at TIMESTAMPTZ NOT NULL,
                dealer TEXT NOT NULL,
                size_g NUMERIC(4,1) NOT NULL,
                status TEXT NOT NULL,
                price_dkk NUMERIC(10,2),
                brand TEXT,
                error TEXT,
                spot_gold_dkk_per_g NUMERIC(10,4)
            );
            CREATE INDEX idx_dealer_snapshots_lookup
                ON dealer_snapshots (dealer, size_g, fetched_at DESC);
            """
        )
        await conn.execute(
            "INSERT INTO dealer_snapshots (fetched_at, dealer, size_g, status, price_dkk) "
            "VALUES (NOW(), 'Tavex', 5.0, 'ok', 5000.00)"
        )
        await conn.execute(SCHEMA_SQL)
        tables = {
            r["table_name"]
            for r in await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public'"
            )
        }
        assert "bar_snapshots" in tables
        assert "dealer_snapshots" not in tables
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM bar_snapshots WHERE dealer='Tavex'"
        )
        assert n == 1
        # Re-running is a no-op
        await conn.execute(SCHEMA_SQL)
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM bar_snapshots WHERE dealer='Tavex'"
        )
        assert n == 1
    finally:
        await conn.close()
