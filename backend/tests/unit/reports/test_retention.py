"""Retention-cutoff date math. Pure function, no DB — runs in CI unlike the
DB-backed prune test in test_storage.py.
"""
from datetime import date

from app.reports.storage import RETENTION_MONTHS, retention_cutoff


def test_keeps_three_months_plus_current() -> None:
    # The motivating case: on 22-09-2026 the archive should keep June, July
    # and August alongside the in-progress September, and drop May.
    assert retention_cutoff(date(2026, 9, 22)) == date(2026, 6, 1)


def test_cutoff_is_stable_across_the_whole_month() -> None:
    first = retention_cutoff(date(2026, 9, 1))
    last = retention_cutoff(date(2026, 9, 30))
    assert first == last == date(2026, 6, 1)


def test_wraps_across_the_year_boundary() -> None:
    # February minus three months is the bug a naive `today.month - 3` has.
    assert retention_cutoff(date(2026, 2, 14)) == date(2025, 11, 1)
    assert retention_cutoff(date(2026, 1, 1)) == date(2025, 10, 1)
    assert retention_cutoff(date(2026, 3, 31)) == date(2025, 12, 1)


def test_cutoff_is_exactly_retention_months_back() -> None:
    for month in range(1, 13):
        cutoff = retention_cutoff(date(2026, month, 15))
        months_back = (2026 * 12 + month - 1) - (cutoff.year * 12 + cutoff.month - 1)
        assert months_back == RETENTION_MONTHS
        assert cutoff.day == 1
