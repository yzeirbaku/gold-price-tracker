"""report_archive CRUD: upsert, list, fetch_html, prune.

Thin wrappers \u2014 endpoint handlers shouldn't be writing raw SQL.
"""
import logging
from datetime import date
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# Keep the current calendar month plus this many preceding ones.
#
# Pruning is DESTRUCTIVE and there is no endpoint that rebuilds an arbitrary
# past period: POST /reports/cron/{type} only ever builds the *previous*
# calendar week/month, and POST /reports/generate is rolling last-7/last-30
# and is not persisted. The underlying bar/coin/spot snapshots are never
# pruned, so a dropped period is still reconstructible by calling
# build_report() with a hand-made Window — but that is a code change, not an
# API call. Widen this before narrowing it.
RETENTION_MONTHS = 3


async def upsert_report(
    conn: asyncpg.Connection,
    report_type: str,
    period_start: date,
    period_end: date,
    html: str,
) -> int:
    row = await conn.fetchrow(
        """
        INSERT INTO report_archive (report_type, period_start, period_end, html)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (report_type, period_start)
        DO UPDATE SET
            period_end = EXCLUDED.period_end,
            html = EXCLUDED.html,
            generated_at = NOW()
        RETURNING id
        """,
        report_type, period_start, period_end, html,
    )
    return int(row["id"])


async def list_reports(conn: asyncpg.Connection) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT id, report_type, period_start, period_end, generated_at
        FROM report_archive
        ORDER BY period_start DESC, report_type DESC
        """
    )
    return [
        {
            "id": int(r["id"]),
            "type": r["report_type"],
            "period_start": r["period_start"],
            "period_end": r["period_end"],
            "generated_at": r["generated_at"].isoformat(),
        }
        for r in rows
    ]


async def fetch_report_html(
    conn: asyncpg.Connection, report_id: int,
) -> tuple[str, str, date, date] | None:
    row = await conn.fetchrow(
        """
        SELECT html, report_type, period_start, period_end
        FROM report_archive
        WHERE id = $1
        """,
        report_id,
    )
    if row is None:
        return None
    return (row["html"], row["report_type"], row["period_start"], row["period_end"])


def retention_cutoff(today: date) -> date:
    """First day of the oldest month we keep. Reports starting before this
    are pruned.

    2026-09-22 with RETENTION_MONTHS=3 → 2026-06-01, i.e. June/July/August
    stay alongside the in-progress September.
    """
    # Arithmetic on an absolute month index avoids the wrap-around bugs you
    # get from subtracting from `.month` directly (e.g. February − 3).
    month_index = today.year * 12 + (today.month - 1) - RETENTION_MONTHS
    return date(month_index // 12, month_index % 12 + 1, 1)


async def prune_old_reports(
    conn: asyncpg.Connection, today: date | None = None,
) -> int:
    """Delete archived reports older than the retention cutoff. Returns the
    number of rows removed.

    Keys on `period_start`, not `period_end`, so a week straddling a month
    boundary is bucketed by the month it *started* in — the Mon 25-May →
    Sun 31-May weekly goes with May and is dropped alongside May's monthly.
    """
    if today is None:
        # Postgres's clock, not the container's. This is an irreversible
        # DELETE driven entirely by "what day is it", and the VM's clock is
        # the one input we don't control — drift or a restored snapshot that
        # boots months ahead would compute a far-future cutoff and wipe the
        # whole archive on startup. Neon's clock is NTP-disciplined and is
        # already the authority for every `fetched_at` we compare against.
        # Europe/Copenhagen to match how report windows are cut
        # (reports/windows.py), so a UTC "today" just past midnight CPH
        # can't retire a month a day early.
        today = await conn.fetchval(
            "SELECT (NOW() AT TIME ZONE 'Europe/Copenhagen')::date"
        )
    cutoff = retention_cutoff(today)
    result = await conn.execute(
        "DELETE FROM report_archive WHERE period_start < $1", cutoff,
    )
    # asyncpg returns the raw command tag, e.g. "DELETE 7".
    deleted = int(result.rsplit(" ", 1)[-1]) if result.startswith("DELETE") else 0
    if deleted:
        logger.info(
            "report_retention_pruned deleted=%d cutoff=%s", deleted, cutoff.isoformat(),
        )
    return deleted
