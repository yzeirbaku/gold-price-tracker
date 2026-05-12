from datetime import UTC, datetime, timedelta

from app.buy_context import _compute


def _series(prems: list[float]) -> list[tuple[datetime, float]]:
    t0 = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    return [(t0 + timedelta(hours=i), p) for i, p in enumerate(prems)]


def test_insufficient_data_when_fewer_than_5() -> None:
    ctx = _compute(_series([5.0, 5.2, 5.4, 5.6]))
    assert ctx.verdict == "insufficient data"
    assert ctx.n_observations == 4
    assert ctx.today_premium_pct is None
    assert ctx.is_new_low is False


def test_today_at_iqr_low_is_below_typical() -> None:
    # Premiums clustered around 6–8% with today at 5% (below Q1)
    prems = [6.0, 6.5, 7.0, 7.0, 7.5, 8.0, 8.0, 5.0]
    ctx = _compute(_series(prems))
    assert ctx.today_premium_pct == 5.0
    assert ctx.verdict == "below typical"
    assert ctx.iqr_low_premium_pct is not None and ctx.iqr_low_premium_pct > 5.0


def test_today_inside_iqr_is_in_line() -> None:
    prems = [5.0, 6.0, 7.0, 8.0, 7.0, 6.0, 5.0, 7.0]
    ctx = _compute(_series(prems))
    assert ctx.verdict == "in line with typical"


def test_today_above_iqr_is_above_typical() -> None:
    prems = [5.0, 5.5, 6.0, 6.0, 6.5, 7.0, 7.0, 9.5]
    ctx = _compute(_series(prems))
    assert ctx.verdict == "above typical"


def test_is_new_low_only_when_strictly_lower() -> None:
    # Prior min is 5.0 — today at 4.9 is a strict new low.
    new_low = _compute(_series([5.0, 6.0, 7.0, 8.0, 6.0, 4.9]))
    assert new_low.is_new_low is True
    assert new_low.min_premium_pct == 4.9

    # Prior min is 5.0 — today equal to 5.0 is NOT a new low.
    tied = _compute(_series([5.0, 6.0, 7.0, 8.0, 6.0, 5.0]))
    assert tied.is_new_low is False
    assert tied.min_premium_pct == 5.0


def test_min_date_points_to_today_when_today_is_new_low() -> None:
    rows = _series([6.0, 7.0, 8.0, 7.0, 6.5, 4.5])  # today (last) is the new low
    ctx = _compute(rows)
    assert ctx.is_new_low is True
    assert ctx.min_premium_at == rows[-1][0]


def test_min_date_points_to_prior_when_today_isnt_new_low() -> None:
    rows = _series([6.0, 5.2, 7.0, 8.0, 6.5, 5.5])  # prior min at index 1
    ctx = _compute(rows)
    assert ctx.is_new_low is False
    assert ctx.min_premium_at == rows[1][0]
    assert ctx.min_premium_pct == 5.2
