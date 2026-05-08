"""Postgres (Neon) connection pool + schema bootstrap.

Optional dependency: when DATABASE_URL is unset (local dev without history),
get_pool() returns None and the snapshot/history endpoints 503. Everything
else keeps working — the live /prices and /spot paths don't touch the DB.
"""
import logging
import os

import asyncpg

logger = logging.getLogger(__name__)

# Schema is intentionally tiny and idempotent — no migration tool. We run this
# on startup; new columns get added via plain ALTER TABLE statements appended
# below over time.
SCHEMA_SQL = """
-- Idempotent migration: rename dealer_snapshots → bar_snapshots if needed.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dealer_snapshots')
       AND NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'bar_snapshots')
    THEN
        ALTER TABLE dealer_snapshots RENAME TO bar_snapshots;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_dealer_snapshots_lookup')
       AND NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_bar_snapshots_lookup')
    THEN
        ALTER INDEX idx_dealer_snapshots_lookup RENAME TO idx_bar_snapshots_lookup;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS bar_snapshots (
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

CREATE INDEX IF NOT EXISTS idx_bar_snapshots_lookup
    ON bar_snapshots (dealer, size_g, fetched_at DESC);

CREATE TABLE IF NOT EXISTS spot_snapshots (
    id BIGSERIAL PRIMARY KEY,
    fetched_at TIMESTAMPTZ NOT NULL,
    gold_dkk_per_g NUMERIC(10,4),
    gold_eur_per_g NUMERIC(10,4),
    silver_dkk_per_g NUMERIC(10,4),
    silver_eur_per_g NUMERIC(10,4),
    fx_stale BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_spot_snapshots_time
    ON spot_snapshots (fetched_at DESC);

CREATE TABLE IF NOT EXISTS coin_snapshots (
    id BIGSERIAL PRIMARY KEY,
    fetched_at        TIMESTAMPTZ NOT NULL,
    dealer            TEXT NOT NULL,
    coin_type         TEXT,
    size_label        TEXT,
    gross_weight_g    NUMERIC(8,4),
    purity            NUMERIC(6,4),
    fine_gold_g       NUMERIC(8,4),
    status            TEXT NOT NULL,
    price_dkk         NUMERIC(10,2),
    error             TEXT,
    spot_gold_dkk_per_g NUMERIC(10,4),
    listing_url       TEXT
);

CREATE INDEX IF NOT EXISTS idx_coin_snapshots_lookup
    ON coin_snapshots (dealer, coin_type, fine_gold_g, fetched_at DESC);
"""

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool | None:
    """Return the global pool, creating it on first call. None if no DATABASE_URL."""
    global _pool
    if _pool is not None:
        return _pool
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    # Neon requires SSL; their connection strings already include sslmode=require
    # but asyncpg ignores that param and needs the kwarg explicitly.
    _pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=4,
        command_timeout=10.0,
        ssl="require" if "sslmode=require" in dsn or "neon.tech" in dsn else None,
    )
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
    logger.info("db pool initialized")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
