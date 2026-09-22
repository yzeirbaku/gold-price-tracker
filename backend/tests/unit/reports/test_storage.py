import os
from datetime import date

import asyncpg
import pytest

from app.db import SCHEMA_SQL
from app.reports.storage import (
    fetch_report_html,
    list_reports,
    prune_old_reports,
    upsert_report,
)

LOCAL_DSN = os.environ.get("TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    LOCAL_DSN is None,
    reason="TEST_DATABASE_URL not set; requires local Postgres",
)


@pytest.fixture
async def conn():
    c = await asyncpg.connect(LOCAL_DSN)
    await c.execute(SCHEMA_SQL)
    await c.execute("DELETE FROM report_archive")
    try:
        yield c
    finally:
        # Don't leak placeholder rows into the user's local DB across test
        # runs — clean up after the test, not just before.
        await c.execute("DELETE FROM report_archive")
        await c.close()


@pytest.mark.asyncio
async def test_upsert_inserts_new_row(conn) -> None:
    rid = await upsert_report(
        conn, "weekly", date(2026, 5, 4), date(2026, 5, 10),
        "<html>v1</html>",
    )
    assert rid > 0
    rows = await list_reports(conn)
    assert len(rows) == 1
    assert rows[0]["type"] == "weekly"
    assert rows[0]["period_start"] == date(2026, 5, 4)


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_row(conn) -> None:
    await upsert_report(
        conn, "weekly", date(2026, 5, 4), date(2026, 5, 10),
        "<html>v1</html>",
    )
    rid2 = await upsert_report(
        conn, "weekly", date(2026, 5, 4), date(2026, 5, 10),
        "<html>v2</html>",
    )
    rows = await list_reports(conn)
    assert len(rows) == 1, "second upsert should overwrite, not insert"
    fetched = await fetch_report_html(conn, rid2)
    assert fetched is not None
    html, kind, _, _ = fetched
    assert html == "<html>v2</html>"
    assert kind == "weekly"


@pytest.mark.asyncio
async def test_list_orders_newest_first(conn) -> None:
    await upsert_report(
        conn, "weekly", date(2026, 4, 27), date(2026, 5, 3), "<html>old</html>",
    )
    await upsert_report(
        conn, "weekly", date(2026, 5, 4), date(2026, 5, 10), "<html>new</html>",
    )
    await upsert_report(
        conn, "monthly", date(2026, 4, 1), date(2026, 4, 30), "<html>april</html>",
    )
    rows = await list_reports(conn)
    assert rows[0]["period_start"] == date(2026, 5, 4)
    assert rows[-1]["period_start"] == date(2026, 4, 1)


@pytest.mark.asyncio
async def test_fetch_returns_none_when_missing(conn) -> None:
    out = await fetch_report_html(conn, 999_999)
    assert out is None


@pytest.mark.asyncio
async def test_prune_drops_reports_older_than_the_cutoff(conn) -> None:
    """The production case as of 22-09-2026: an archive reaching back to May,
    pruned down to June onwards. The May weeklies go with the May monthly."""
    await upsert_report(conn, "monthly", date(2026, 5, 1), date(2026, 5, 31), "may")
    await upsert_report(conn, "weekly", date(2026, 5, 4), date(2026, 5, 10), "may w1")
    # Straddles the boundary but *starts* in May, so it is bucketed with May.
    await upsert_report(conn, "weekly", date(2026, 5, 25), date(2026, 5, 31), "may w4")
    await upsert_report(conn, "monthly", date(2026, 6, 1), date(2026, 6, 30), "june")
    await upsert_report(conn, "weekly", date(2026, 6, 1), date(2026, 6, 7), "june w1")
    await upsert_report(conn, "weekly", date(2026, 9, 14), date(2026, 9, 20), "sept")

    deleted = await prune_old_reports(conn, today=date(2026, 9, 22))

    assert deleted == 3
    remaining = {(r["type"], r["period_start"]) for r in await list_reports(conn)}
    assert remaining == {
        ("monthly", date(2026, 6, 1)),
        ("weekly", date(2026, 6, 1)),
        ("weekly", date(2026, 9, 14)),
    }


@pytest.mark.asyncio
async def test_prune_is_idempotent(conn) -> None:
    """Runs on every startup, so a second pass must be a no-op rather than
    eating into the retained window."""
    await upsert_report(conn, "monthly", date(2026, 5, 1), date(2026, 5, 31), "may")
    await upsert_report(conn, "monthly", date(2026, 7, 1), date(2026, 7, 31), "july")

    assert await prune_old_reports(conn, today=date(2026, 9, 22)) == 1
    assert await prune_old_reports(conn, today=date(2026, 9, 22)) == 0
    assert len(await list_reports(conn)) == 1


@pytest.mark.asyncio
async def test_prune_keeps_everything_when_archive_is_young(conn) -> None:
    await upsert_report(conn, "weekly", date(2026, 9, 7), date(2026, 9, 13), "a")
    await upsert_report(conn, "weekly", date(2026, 9, 14), date(2026, 9, 20), "b")

    assert await prune_old_reports(conn, today=date(2026, 9, 22)) == 0
    assert len(await list_reports(conn)) == 2


@pytest.mark.asyncio
async def test_prune_defaults_to_the_database_clock(conn) -> None:
    """With no explicit `today` the cutoff comes from Postgres, not the
    container — the startup and cron paths both rely on that default, so it
    needs coverage beyond the fixed-date tests above."""
    today = await conn.fetchval("SELECT (NOW() AT TIME ZONE 'Europe/Copenhagen')::date")
    keep = date(today.year, today.month, 1)
    ancient = date(2020, 1, 1)
    await upsert_report(conn, "monthly", ancient, date(2020, 1, 31), "ancient")
    await upsert_report(conn, "monthly", keep, keep, "current month")

    assert await prune_old_reports(conn) == 1

    remaining = [r["period_start"] for r in await list_reports(conn)]
    assert remaining == [keep]
