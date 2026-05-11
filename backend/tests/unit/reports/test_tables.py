from datetime import UTC, datetime, timedelta

from app.reports.loader import BarPoint
from app.reports.tables import BarSizeRow, build_bar_table


def _bar(t: datetime, dealer: str, size: float, price: float | None,
         spot: float = 1000.0, status: str = "ok") -> BarPoint:
    return BarPoint(
        fetched_at=t, dealer=dealer, size_g=size,
        status=status, price_dkk=price, spot_dkk_per_g=spot,
    )


def test_build_bar_table_aggregates_per_dealer() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    points = [
        _bar(t0, "Tavex", 5.0, 5350.0),    # 7% premium
        _bar(t0 + timedelta(hours=2), "Tavex", 5.0, 5400.0),  # 8%
        _bar(t0, "Vitus Guld", 5.0, 5300.0),  # 6%
        _bar(t0 + timedelta(hours=2), "Vitus Guld", 5.0, 5300.0),  # 6%
    ]
    rows = build_bar_table(points, size_g=5.0)
    by_dealer = {r.dealer: r for r in rows if r.dealer != "Market"}
    assert "Tavex" in by_dealer
    assert "Vitus Guld" in by_dealer
    tavex = by_dealer["Tavex"]
    assert isinstance(tavex, BarSizeRow)
    assert tavex.median_price_dkk is not None
    assert tavex.median_premium_pct is not None
    # Tavex premiums: 7% and 8% \u2192 min 7, max 8
    assert tavex.min_premium_pct == 7.0
    assert tavex.max_premium_pct == 8.0
    # Vitus Guld is the cheapest in every snapshot \u2192 100%
    vitus = by_dealer["Vitus Guld"]
    assert vitus.pct_time_cheapest == 100.0
    assert tavex.pct_time_cheapest == 0.0
    # Market footer row is present
    assert any(r.dealer == "Market" for r in rows)


def test_build_bar_table_returns_empty_for_missing_size() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    rows = build_bar_table([_bar(t0, "Tavex", 5.0, 5300.0)], size_g=10.0)
    assert rows == []
