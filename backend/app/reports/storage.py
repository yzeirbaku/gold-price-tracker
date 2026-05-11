"""report_archive CRUD: upsert, list, fetch_html.

Thin wrappers \u2014 endpoint handlers shouldn't be writing raw SQL.
"""
from datetime import date
from typing import Any

import asyncpg


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
