"""Snapshot retention: drop bar/coin rows past the window we actually read.

`spot_snapshots` is deliberately NOT prunable. `/portfolio/history` reads it
back to the user's first purchase to draw value-over-time, so it is the one
snapshot table with genuinely unbounded lookback — and at ~0.3 MB/month it is
a rounding error next to the other two (233 MB of coin rows vs 1.4 MB of spot
as of 2026-09-22). Pruning it would break the portfolio chart to save nothing.

Every other reader of bar/coin snapshots is bounded:

    /history/{bar,coin}   24h | 7d | 30d
    /context/{bar,coin}   30d (buy_context.load_* window_days, never overridden)
    /alerts               90 minutes
    reports loader        explicit [start_dt, end_dt) window

The binding constraint is the monthly report cron, which builds the *previous*
calendar month — ~32 days back when it fires on the 1st, more if it fails and
gets re-run late. RETENTION_DAYS is set well beyond that; archived reports are
already-rendered HTML and never re-read their source rows.

Deleting does not shrink the table on disk. Postgres marks tuples dead and
autovacuum makes the space reusable *by that table*; returning it to the OS
needs a VACUUM FULL (ACCESS EXCLUSIVE lock, ~2x table size free during the
rewrite). This module stops the growth; reclaiming is a separate manual call.
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Keep this many days of bar/coin snapshots. 3x the 30-day UI lookback, and
# well past the ~32 days the monthly report cron reaches back.
RETENTION_DAYS = 90

# Rows per DELETE statement. The first run on a table that has never been
# pruned has hundreds of thousands of rows to clear (265k coin rows on
# 2026-09-22) — one unbounded DELETE would blow the pool's 10s
# command_timeout, hold a single long transaction open and write one huge WAL
# burst. Batching keeps each statement well inside the timeout and each
# transaction short.
DELETE_BATCH_SIZE = 10_000

# Backstop against an unbounded loop if something pathological happens (a
# cutoff that never advances, rows reappearing). 500 x 10k = 5M rows, far
# above any real backlog. Not a tuning knob.
MAX_BATCHES = 500

# Whitelist. Table names cannot be parameterized, so the only names that ever
# reach the SQL string come from this tuple — never from a caller argument.
# `spot_snapshots` is absent on purpose; see the module docstring.
PRUNABLE_TABLES: tuple[str, ...] = ("bar_snapshots", "coin_snapshots")


async def _prune_table(conn: Any, table: str, retention_days: int) -> int:
    """Delete rows older than the cutoff from one table, in bounded batches.
    Returns the total number of rows removed."""
    if table not in PRUNABLE_TABLES:
        # Defence in depth: this is what keeps the f-string below safe, and
        # what stops a future caller from passing "spot_snapshots".
        raise ValueError(f"table not prunable: {table}")

    # NOW() is Postgres's clock, not the container's — same reasoning as the
    # report prune. An irreversible DELETE keyed on "how old is this row"
    # should not depend on VM clock drift. int() on retention_days is the
    # second half of the injection guard.
    sql = (
        f"DELETE FROM {table} WHERE ctid IN ("  # noqa: S608 - whitelisted above
        f"  SELECT ctid FROM {table}"
        f"  WHERE fetched_at < NOW() - INTERVAL '{int(retention_days)} days'"
        f"  ORDER BY fetched_at"
        f"  LIMIT {int(DELETE_BATCH_SIZE)}"
        f")"
    )

    total = 0
    for _ in range(MAX_BATCHES):
        result = await conn.execute(sql)
        deleted = int(result.rsplit(" ", 1)[-1]) if result.startswith("DELETE") else 0
        total += deleted
        if deleted < DELETE_BATCH_SIZE:
            # Short batch means we drained everything past the cutoff.
            break
    else:
        logger.warning(
            "snapshot_retention_batch_cap table=%s removed=%d cap=%d "
            "(more rows remain; next run continues)",
            table, total, MAX_BATCHES,
        )
    return total


async def prune_snapshots(
    conn: Any, retention_days: int = RETENTION_DAYS,
) -> dict[str, int]:
    """Prune every table in PRUNABLE_TABLES. Returns {table: rows_deleted}.

    Idempotent: a second call right after the first deletes nothing.
    """
    removed: dict[str, int] = {}
    for table in PRUNABLE_TABLES:
        removed[table] = await _prune_table(conn, table, retention_days)
    if any(removed.values()):
        logger.info(
            "snapshot_retention_pruned retention_days=%d %s",
            retention_days,
            " ".join(f"{t}={n}" for t, n in removed.items()),
        )
    return removed
