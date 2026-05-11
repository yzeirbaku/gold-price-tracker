from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.reports.windows import (
    previous_calendar_month,
    previous_calendar_week,
    rolling_last_n_days,
)

CPH = ZoneInfo("Europe/Copenhagen")


def test_previous_calendar_week_from_wednesday() -> None:
    now = datetime(2026, 5, 13, 14, 0, tzinfo=CPH)  # Wed May 13
    w = previous_calendar_week(now)
    assert w.period_start == date(2026, 5, 4)   # Mon
    assert w.period_end == date(2026, 5, 10)    # Sun
    assert w.start_dt == datetime(2026, 5, 4, 0, 0, tzinfo=CPH)
    assert w.end_dt == datetime(2026, 5, 11, 0, 0, tzinfo=CPH)
    assert w.label == "Week of May 4 \u2013 May 10, 2026"
    assert w.kind == "weekly"


def test_previous_calendar_week_from_monday() -> None:
    # On Mon May 11 just after midnight, "previous week" must be May 4\u201310,
    # not May 11\u201317 (today). The cron fires Mon 00:30 CPH and must produce
    # the just-completed week.
    now = datetime(2026, 5, 11, 0, 30, tzinfo=CPH)
    w = previous_calendar_week(now)
    assert w.period_start == date(2026, 5, 4)
    assert w.period_end == date(2026, 5, 10)


def test_previous_calendar_month_from_mid_month() -> None:
    now = datetime(2026, 5, 13, 14, 0, tzinfo=CPH)
    w = previous_calendar_month(now)
    assert w.period_start == date(2026, 4, 1)
    assert w.period_end == date(2026, 4, 30)
    assert w.start_dt == datetime(2026, 4, 1, 0, 0, tzinfo=CPH)
    assert w.end_dt == datetime(2026, 5, 1, 0, 0, tzinfo=CPH)
    assert w.label == "April 2026"
    assert w.kind == "monthly"


def test_previous_calendar_month_on_first_of_month() -> None:
    # Cron fires May 1 at 00:30 \u2192 should produce April's report.
    now = datetime(2026, 5, 1, 0, 30, tzinfo=CPH)
    w = previous_calendar_month(now)
    assert w.period_start == date(2026, 4, 1)
    assert w.period_end == date(2026, 4, 30)


def test_previous_calendar_month_january_rolls_year() -> None:
    now = datetime(2026, 1, 15, 12, 0, tzinfo=CPH)
    w = previous_calendar_month(now)
    assert w.period_start == date(2025, 12, 1)
    assert w.period_end == date(2025, 12, 31)
    assert w.label == "December 2025"


def test_rolling_last_7_days() -> None:
    now = datetime(2026, 5, 11, 14, 30, tzinfo=CPH)
    w = rolling_last_n_days(now, 7)
    assert w.end_dt == now
    # Exactly 7 \u00d7 24h earlier
    assert w.start_dt == datetime(2026, 5, 4, 14, 30, tzinfo=CPH)
    assert w.period_start == date(2026, 5, 4)
    assert w.period_end == date(2026, 5, 11)
    assert w.kind == "weekly"  # on-demand week rolls in as "weekly" type
    assert "Last 7 days" in w.label


def test_rolling_last_30_days() -> None:
    now = datetime(2026, 5, 11, 14, 30, tzinfo=CPH)
    w = rolling_last_n_days(now, 30)
    assert w.start_dt == datetime(2026, 4, 11, 14, 30, tzinfo=CPH)
    assert w.kind == "monthly"
    assert "Last 30 days" in w.label


def test_window_dataclass_is_frozen() -> None:
    now = datetime(2026, 5, 13, 14, 0, tzinfo=CPH)
    w = previous_calendar_week(now)
    with pytest.raises(AttributeError):
        w.label = "changed"  # type: ignore[misc]
