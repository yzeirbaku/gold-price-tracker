"""Builder test \u2014 runs against synthetic in-memory snapshot data.

We bypass `load_bars`/`load_coins`/`load_spot` (which hit the DB) by patching
them to return pre-built lists, then assert the builder produces a valid
HTML report with the expected section markers.
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.reports.builder import build_report
from app.reports.loader import BarPoint, SpotPoint
from app.reports.windows import previous_calendar_week


def _synthetic_bars() -> list[BarPoint]:
    base = datetime(2026, 5, 4, 0, 0, tzinfo=UTC)
    out: list[BarPoint] = []
    for i in range(7 * 24):  # one snapshot per hour for a week
        ts = base + timedelta(hours=i)
        out.append(BarPoint(
            fetched_at=ts, dealer="Tavex", size_g=5.0, status="ok",
            price_dkk=5300.0 + (i % 10), spot_dkk_per_g=1000.0,
        ))
        out.append(BarPoint(
            fetched_at=ts, dealer="Nordisk Guld", size_g=5.0, status="ok",
            price_dkk=5280.0 + (i % 7), spot_dkk_per_g=1000.0,
        ))
    return out


def _synthetic_spot() -> list[SpotPoint]:
    base = datetime(2026, 5, 4, 0, 0, tzinfo=UTC)
    return [
        SpotPoint(
            fetched_at=base + timedelta(hours=i),
            gold_dkk_per_g=1000.0 + (i * 0.1),
            silver_dkk_per_g=13.0,
        )
        for i in range(7 * 24)
    ]


@pytest.mark.asyncio
async def test_build_report_produces_html_with_expected_sections() -> None:
    now = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    window = previous_calendar_week(now)

    with patch("app.reports.builder.load_bars",
               new=AsyncMock(return_value=_synthetic_bars())), \
         patch("app.reports.builder.load_coins",
               new=AsyncMock(return_value=[])), \
         patch("app.reports.builder.load_spot",
               new=AsyncMock(return_value=_synthetic_spot())):
        html = await build_report(conn=None, window=window)  # type: ignore[arg-type]

    assert "<!doctype html>" in html.lower()
    assert 'id="section-header"' in html
    assert 'id="section-fingerprints"' in html
    assert "Tavex" in html
    assert "Nordisk Guld" in html
    assert 'id="report-data"' in html


@pytest.mark.asyncio
async def test_build_report_handles_empty_period() -> None:
    now = datetime(2026, 5, 13, 12, 0, tzinfo=UTC)
    window = previous_calendar_week(now)

    with patch("app.reports.builder.load_bars",
               new=AsyncMock(return_value=[])), \
         patch("app.reports.builder.load_coins",
               new=AsyncMock(return_value=[])), \
         patch("app.reports.builder.load_spot",
               new=AsyncMock(return_value=[])):
        html = await build_report(conn=None, window=window)  # type: ignore[arg-type]

    assert 'id="section-header"' in html
    assert 'id="section-bars"' in html
    assert 'id="section-coins"' in html
    assert 'id="section-notable"' in html
