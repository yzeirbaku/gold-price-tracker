from datetime import datetime, timedelta, timezone

from app.reports.loader import BarPoint
from app.reports.tables import BarSizeRow, build_bar_table, sparkline

UTC = timezone.utc


def _bar(t: datetime, dealer: str, size: float, price: float | None,
         spot: float = 1000.0, status: str = "ok") -> BarPoint:
    return BarPoint(
        fetched_at=t, dealer=dealer, size_g=size,
        status=status, price_dkk=price, spot_dkk_per_g=spot,
    )


def test_sparkline_picks_chars_proportional_to_values() -> None:
    s = sparkline([1.0, 2.0, 3.0, 4.0, 5.0])
    assert len(s) == 5
    assert s[0] != s[-1]
    assert all(ch in "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588" for ch in s)


def test_sparkline_handles_flat_series() -> None:
    s = sparkline([3.0, 3.0, 3.0, 3.0])
    assert len(s) == 4
    assert len(set(s)) == 1  # all same char


def test_sparkline_handles_none_values() -> None:
    s = sparkline([None, 2.0, None, 4.0])
    assert len(s) == 4


def test_build_bar_table_aggregates_per_dealer() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    points = [
        _bar(t0, "Tavex", 5.0, 5350.0),    # 7% premium
        _bar(t0 + timedelta(hours=2), "Tavex", 5.0, 5400.0),  # 8%
        _bar(t0, "Vitus Guld", 5.0, 5300.0),  # 6%
        _bar(t0 + timedelta(hours=2), "Vitus Guld", 5.0, 5300.0),  # 6%
    ]
    rows = build_bar_table(points, size_g=5.0, bins=2)
    by_dealer = {r.dealer: r for r in rows if r.dealer != "Market"}
    assert "Tavex" in by_dealer
    assert "Vitus Guld" in by_dealer
    tavex = by_dealer["Tavex"]
    assert isinstance(tavex, BarSizeRow)
    assert tavex.median_price_dkk is not None
    assert tavex.median_premium_pct is not None
    # Vitus Guld is the cheapest in every snapshot \u2192 100%
    vitus = by_dealer["Vitus Guld"]
    assert vitus.pct_time_cheapest == 100.0
    assert tavex.pct_time_cheapest == 0.0
    # Market footer row is present
    assert any(r.dealer == "Market" for r in rows)
