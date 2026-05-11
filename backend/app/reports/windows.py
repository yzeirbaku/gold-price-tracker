"""Period-boundary computation for reports.

All boundaries are in Europe/Copenhagen (the Danish-market trading day).
`Window.start_dt` / `end_dt` are inclusive-exclusive bounds for snapshot
queries; `period_start` / `period_end` are the human-facing dates printed
in the report header.
"""
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

CPH = ZoneInfo("Europe/Copenhagen")
WindowKind = Literal["weekly", "monthly"]


@dataclass(frozen=True)
class Window:
    kind: WindowKind
    period_start: date  # inclusive (e.g. Mon)
    period_end: date    # inclusive (e.g. Sun for weekly, last-of-month for monthly)
    start_dt: datetime  # tz-aware, inclusive lower bound for snapshot queries
    end_dt: datetime    # tz-aware, exclusive upper bound for snapshot queries
    label: str          # full one-line label (kind + period text)
    kind_label: str     # just the kind, e.g. "Weekly Report"
    period_text: str    # just the range, e.g. "04-05-2026 18:04 \u2192 11-05-2026 18:04"
    is_calendar_aligned: bool  # True for previous_calendar_*; False for rolling


def previous_calendar_week(now: datetime) -> Window:
    """The Mon\u2013Sun week immediately before `now` (Europe/Copenhagen)."""
    now_cph = now.astimezone(CPH)
    # weekday(): Mon=0 .. Sun=6. Today's most recent Mon 00:00 in CPH:
    this_week_monday = (now_cph - timedelta(days=now_cph.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_monday = this_week_monday - timedelta(days=7)
    last_sunday = this_week_monday - timedelta(days=1)
    return Window(
        kind="weekly",
        period_start=last_monday.date(),
        period_end=last_sunday.date(),
        start_dt=last_monday,
        end_dt=this_week_monday,
        is_calendar_aligned=True,
        **_label_fields("Weekly", last_monday, this_week_monday),
    )


def previous_calendar_month(now: datetime) -> Window:
    """The full calendar month immediately before `now` (Europe/Copenhagen)."""
    now_cph = now.astimezone(CPH)
    first_of_this_month = now_cph.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    # Step back one day to land in the previous month, then snap to day 1.
    prev_month_some_day = first_of_this_month - timedelta(days=1)
    prev_start = prev_month_some_day.replace(day=1)
    last_day = monthrange(prev_start.year, prev_start.month)[1]
    prev_end_date = date(prev_start.year, prev_start.month, last_day)
    return Window(
        kind="monthly",
        period_start=prev_start.date(),
        period_end=prev_end_date,
        start_dt=prev_start,
        end_dt=first_of_this_month,
        is_calendar_aligned=True,
        **_label_fields("Monthly", prev_start, first_of_this_month),
    )


def rolling_last_n_days(now: datetime, n: int) -> Window:
    """Rolling N-day window ending at `now` (Europe/Copenhagen).

    Used for on-demand reports \u2014 same content shape as the cron variants,
    but unaligned to calendar boundaries. The `kind` field follows the
    intent (7 days = weekly-shape, 30 days = monthly-shape).
    """
    now_cph = now.astimezone(CPH)
    start_dt = now_cph - timedelta(days=n)
    kind: WindowKind = "weekly" if n <= 7 else "monthly"
    return Window(
        kind=kind,
        period_start=start_dt.date(),
        period_end=now_cph.date(),
        start_dt=start_dt,
        end_dt=now_cph,
        is_calendar_aligned=False,
        **_label_fields("Weekly" if kind == "weekly" else "Monthly",
                         start_dt, now_cph),
    )


def _label_fields(
    prefix: str, start_dt: datetime, end_dt: datetime,
) -> dict[str, str]:
    """Build the three label fields (kind_label, period_text, label).

    `period_text` uses a Unicode right-arrow (\u2192) instead of ASCII '<-->'
    so the report header renders elegantly. `label` is the full one-line
    form kept for backward-compat (filenames, archive list rows).
    """
    s = start_dt.astimezone(CPH).strftime("%d-%m-%Y %H:%M")
    e = end_dt.astimezone(CPH).strftime("%d-%m-%Y %H:%M")
    kind_label = f"{prefix} Report"
    period_text = f"{s} \u2192 {e}"
    return {
        "kind_label": kind_label,
        "period_text": period_text,
        "label": f"{kind_label} ({period_text})",
    }
