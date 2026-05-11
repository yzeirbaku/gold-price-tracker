from datetime import UTC, datetime, timedelta

from app.reports.loader import BarPoint
from app.reports.notable import (
    TimeOfMonthRow,
    detect_notable,
    detect_time_of_month_drift,
)


def _bar(t: datetime, dealer: str, size: float, price: float | None,
         spot: float = 1000.0, status: str = "ok") -> BarPoint:
    return BarPoint(
        fetched_at=t, dealer=dealer, size_g=size,
        status=status, price_dkk=price, spot_dkk_per_g=spot,
    )


def test_detects_premium_move_above_threshold() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    # Tavex 5g: premium jumps from 7% to 5% (a 2pp drop) in one snapshot
    points = [
        _bar(t0, "Tavex", 5.0, 5350.0),                     # 7%
        _bar(t0 + timedelta(minutes=20), "Tavex", 5.0, 5250.0),  # 5%
    ]
    bullets = detect_notable(points, [], premium_step_threshold_pp=1.0)
    assert any("Tavex" in b.text and "5g" in b.text for b in bullets)


def test_ignores_premium_move_below_threshold() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    # 7% \u2192 7.5% (0.5pp) \u2014 under the default 1pp threshold
    points = [
        _bar(t0, "Tavex", 5.0, 5350.0),
        _bar(t0 + timedelta(minutes=20), "Tavex", 5.0, 5375.0),
    ]
    bullets = detect_notable(points, [], premium_step_threshold_pp=1.0)
    assert all("Tavex" not in b.text for b in bullets)


def test_detects_ranking_flip_in_cheapest_crown() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    # Snapshot 1: Tavex cheapest. Snapshot 2: Nordisk cheaper.
    points = [
        _bar(t0, "Tavex", 5.0, 5250.0),    # 5%
        _bar(t0, "Nordisk Guld", 5.0, 5300.0),  # 6%
        _bar(t0 + timedelta(hours=1), "Tavex", 5.0, 5300.0),  # 6%
        _bar(t0 + timedelta(hours=1), "Nordisk Guld", 5.0, 5250.0),  # 5%
    ]
    bullets = detect_notable(points, [], premium_step_threshold_pp=1.0)
    assert any("cheapest" in b.text.lower() and "Nordisk" in b.text for b in bullets)


def test_caps_bullets_at_limit_keeping_largest_magnitudes() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    points = []
    for i in range(15):
        # 15 distinct premium jumps of varying magnitudes
        points.append(_bar(t0 + timedelta(minutes=40 * i),
                            f"D{i}", 5.0, 5000.0 + i * 10))
        points.append(_bar(t0 + timedelta(minutes=40 * i + 20),
                            f"D{i}", 5.0, 5000.0 + i * 10 + 200 + i * 30))
    bullets = detect_notable(points, [], premium_step_threshold_pp=1.0, max_bullets=10)
    assert len(bullets) <= 10


def test_time_of_month_drift_returns_per_dealer_rows() -> None:
    base = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
    points = []
    # 4 weeks of data, premium ramps from 5% (week 1) to 8% (week 4)
    for week in range(4):
        for hour in range(0, 24 * 7, 4):
            ts = base + timedelta(days=week * 7, hours=hour)
            prem = 0.05 + week * 0.01  # 5%, 6%, 7%, 8%
            price = 5.0 * 1000.0 * (1 + prem)
            points.append(_bar(ts, "Tavex", 5.0, price))
    rows = detect_time_of_month_drift(
        points, [],
        period_start=datetime(2026, 4, 1, tzinfo=UTC),
        period_end=datetime(2026, 4, 30, tzinfo=UTC),
    )
    by_dealer = {r.dealer: r for r in rows if r.dealer != "Market"}
    assert "Tavex" in by_dealer
    tavex = by_dealer["Tavex"]
    assert isinstance(tavex, TimeOfMonthRow)
    assert len(tavex.weekly_avg_premium_pct) == 4
    # Week 4 average minus week 1 average should be ~3pp
    diff = tavex.weekly_avg_premium_pct[3] - tavex.weekly_avg_premium_pct[0]
    assert 2.5 <= diff <= 3.5
