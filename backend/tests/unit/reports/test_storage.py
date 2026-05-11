import os
from datetime import date

import asyncpg
import pytest

from app.db import SCHEMA_SQL
from app.reports.storage import (
    fetch_report_html,
    list_reports,
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
