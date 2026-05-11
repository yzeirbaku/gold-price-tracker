from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.reports.analytics import (
    CadenceStats,
    WeekendActivity,
    compute_cadence,
    compute_weekend_activity,
)
from app.reports.loader import BarPoint

CPH = ZoneInfo("Europe/Copenhagen")


def _bar(t: datetime, dealer: str, size: float, price: float) -> BarPoint:
    return BarPoint(
        fetched_at=t, dealer=dealer, size_g=size,
        status="ok", price_dkk=price, spot_dkk_per_g=950.0,
    )


def test_cadence_counts_consecutive_changes_per_product() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    points = [
        _bar(t0, "Tavex", 5.0, 5000.0),
        _bar(t0 + timedelta(minutes=20), "Tavex", 5.0, 5000.0),  # no change
        _bar(t0 + timedelta(minutes=40), "Tavex", 5.0, 5050.0),  # change #1
        _bar(t0 + timedelta(hours=2), "Tavex", 5.0, 5050.0),     # no change
        _bar(t0 + timedelta(hours=4), "Tavex", 5.0, 5025.0),     # change #2
        # Different size \u2014 independent series
        _bar(t0, "Tavex", 10.0, 10000.0),
        _bar(t0 + timedelta(minutes=20), "Tavex", 10.0, 10100.0),  # change #3
    ]
    stats = compute_cadence("Tavex", points, weeks_in_period=1.0)
    assert isinstance(stats, CadenceStats)
    assert stats.total_changes == 3
    assert stats.changes_per_week == 3.0
    assert stats.latest_change is not None
    assert stats.latest_change == t0 + timedelta(hours=4)
    assert stats.median_interval_hours is not None


def test_cadence_zero_when_no_changes() -> None:
    t0 = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    points = [
        _bar(t0, "Stickdealer", 5.0, 5000.0),
        _bar(t0 + timedelta(hours=12), "Stickdealer", 5.0, 5000.0),
        _bar(t0 + timedelta(hours=24), "Stickdealer", 5.0, 5000.0),
    ]
    stats = compute_cadence("Stickdealer", points, weeks_in_period=1.0)
    assert stats.total_changes == 0
    assert stats.changes_per_week == 0.0
    assert stats.latest_change is None
    assert stats.median_interval_hours is None


def test_cadence_normalizes_per_week() -> None:
    """4 changes across 4 weeks \u2192 1.0 changes/week, not 4.0."""
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    points = [
        _bar(t0, "X", 5.0, 5000.0),
        _bar(t0 + timedelta(days=7), "X", 5.0, 5050.0),
        _bar(t0 + timedelta(days=14), "X", 5.0, 5100.0),
        _bar(t0 + timedelta(days=21), "X", 5.0, 5150.0),
        _bar(t0 + timedelta(days=28), "X", 5.0, 5200.0),
    ]
    stats = compute_cadence("X", points, weeks_in_period=4.0)
    assert stats.total_changes == 4
    assert stats.changes_per_week == 1.0


def test_weekend_activity_detects_saturday_change() -> None:
    # Sat May 9, 2026 in Europe/Copenhagen
    sat = datetime(2026, 5, 9, 14, 0, tzinfo=CPH)
    fri = datetime(2026, 5, 8, 22, 0, tzinfo=CPH)
    sun = datetime(2026, 5, 10, 11, 0, tzinfo=CPH)
    mon = datetime(2026, 5, 11, 9, 0, tzinfo=CPH)
    points = [
        _bar(fri.astimezone(UTC), "Tavex", 5.0, 5000.0),
        _bar(sat.astimezone(UTC), "Tavex", 5.0, 5025.0),   # weekend change
        _bar(sun.astimezone(UTC), "Tavex", 5.0, 5025.0),   # same price, no change
        _bar(mon.astimezone(UTC), "Tavex", 5.0, 5050.0),
    ]
    wa = compute_weekend_activity("Tavex", points)
    assert isinstance(wa, WeekendActivity)
    assert wa.change_count == 1
    assert len(wa.changes) == 1
    assert wa.changes[0].at.astimezone(CPH).weekday() == 5  # Sat


def test_weekend_activity_empty_when_only_weekday_changes() -> None:
    mon = datetime(2026, 5, 11, 9, 0, tzinfo=CPH)
    tue = datetime(2026, 5, 12, 9, 0, tzinfo=CPH)
    points = [
        _bar(mon.astimezone(UTC), "Tavex", 5.0, 5000.0),
        _bar(tue.astimezone(UTC), "Tavex", 5.0, 5050.0),
    ]
    wa = compute_weekend_activity("Tavex", points)
    assert wa.change_count == 0
    assert wa.changes == []


from app.reports.analytics import (  # noqa: E402
    DayOfWeekDist,
    PremiumBand,
    TimeOfDayDist,
    compute_day_of_week,
    compute_premium_band,
    compute_time_of_day,
)


def test_time_of_day_distribution_counts_into_4_buckets() -> None:
    def _at(h: int) -> datetime:
        return datetime(2026, 5, 6, h, 0, tzinfo=CPH).astimezone(UTC)
    points = [
        _bar(_at(2), "X", 5.0, 5000.0),
        _bar(_at(3), "X", 5.0, 5050.0),   # night change
        _bar(_at(8), "X", 5.0, 5100.0),   # morning change
        _bar(_at(14), "X", 5.0, 5150.0),  # afternoon change
        _bar(_at(15), "X", 5.0, 5200.0),  # afternoon change
        _bar(_at(20), "X", 5.0, 5250.0),  # evening change
    ]
    dist = compute_time_of_day("X", points)
    assert isinstance(dist, TimeOfDayDist)
    assert dist.morning == 1
    assert dist.afternoon == 2
    assert dist.evening == 1
    assert dist.night == 1
    assert dist.total == 5


def test_day_of_week_distribution() -> None:
    mon = datetime(2026, 5, 4, 10, 0, tzinfo=CPH).astimezone(UTC)
    tue = datetime(2026, 5, 5, 10, 0, tzinfo=CPH).astimezone(UTC)
    wed = datetime(2026, 5, 6, 10, 0, tzinfo=CPH).astimezone(UTC)
    points = [
        _bar(mon, "X", 5.0, 5000.0),
        _bar(tue, "X", 5.0, 5050.0),   # Tue change
        _bar(wed, "X", 5.0, 5100.0),   # Wed change
    ]
    dist = compute_day_of_week("X", points)
    assert isinstance(dist, DayOfWeekDist)
    assert dist.by_day == [0, 1, 1, 0, 0, 0, 0]  # Mon..Sun
    assert dist.total == 2


def test_premium_band_iqr() -> None:
    # Spot at 1000 DKK/g, 5g bar should be 5000 baseline.
    ts = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    points = [
        BarPoint(fetched_at=ts, dealer="X", size_g=5.0, status="ok",
                 price_dkk=5250.0, spot_dkk_per_g=1000.0),   # 5.00%
        BarPoint(fetched_at=ts, dealer="X", size_g=5.0, status="ok",
                 price_dkk=5300.0, spot_dkk_per_g=1000.0),   # 6.00%
        BarPoint(fetched_at=ts, dealer="X", size_g=5.0, status="ok",
                 price_dkk=5350.0, spot_dkk_per_g=1000.0),   # 7.00%
        BarPoint(fetched_at=ts, dealer="X", size_g=5.0, status="ok",
                 price_dkk=5400.0, spot_dkk_per_g=1000.0),   # 8.00%
    ]
    band = compute_premium_band("X", points)
    assert isinstance(band, PremiumBand)
    # statistics.quantiles(n=4) on [5,6,7,8] interpolates p25=5.25, p75=7.75
    assert band.p25 is not None and band.p75 is not None
    assert 5.0 <= band.p25 <= 6.0
    assert 7.0 <= band.p75 <= 8.0
    # p75 should be greater than p25
    assert band.p75 > band.p25


def test_premium_band_none_when_no_valid_observations() -> None:
    ts = datetime(2026, 5, 5, 10, 0, tzinfo=UTC)
    points = [
        BarPoint(fetched_at=ts, dealer="X", size_g=5.0, status="error",
                 price_dkk=None, spot_dkk_per_g=None),
    ]
    band = compute_premium_band("X", points)
    assert band.p25 is None
    assert band.p75 is None


from app.reports.analytics import SpotTracking, compute_spot_tracking  # noqa: E402
from app.reports.loader import SpotPoint  # noqa: E402


def test_spot_tracking_high_correlation_when_dealer_mirrors_spot() -> None:
    base = datetime(2026, 5, 5, 0, 0, tzinfo=UTC)
    bars: list[BarPoint] = []
    spots: list[SpotPoint] = []
    for i in range(50):
        ts = base + timedelta(minutes=20 * i)
        spot = 950.0 + i * 0.5
        bars.append(BarPoint(
            fetched_at=ts, dealer="X", size_g=5.0, status="ok",
            price_dkk=5.0 * spot * 1.07,  # 7% premium, perfect tracking
            spot_dkk_per_g=spot,
        ))
        spots.append(SpotPoint(
            fetched_at=ts, gold_dkk_per_g=spot, silver_dkk_per_g=None,
        ))
    st = compute_spot_tracking("X", bars, spots)
    assert isinstance(st, SpotTracking)
    assert st.correlation is not None
    assert st.correlation > 0.99
    # Sensitivity should be ~1.0 (dealer price moves 1% per 1% spot)
    assert st.sensitivity is not None
    assert 0.95 <= st.sensitivity <= 1.05


def test_spot_tracking_low_correlation_when_dealer_is_decoupled() -> None:
    import random
    random.seed(7)
    base = datetime(2026, 5, 5, 0, 0, tzinfo=UTC)
    bars: list[BarPoint] = []
    spots: list[SpotPoint] = []
    for i in range(50):
        ts = base + timedelta(minutes=20 * i)
        spot = 950.0 + i * 0.5  # spot drifts up
        # Dealer price walks randomly, ignores spot
        random_price = 5050.0 + random.uniform(-50, 50)
        bars.append(BarPoint(
            fetched_at=ts, dealer="X", size_g=5.0, status="ok",
            price_dkk=random_price, spot_dkk_per_g=spot,
        ))
        spots.append(SpotPoint(
            fetched_at=ts, gold_dkk_per_g=spot, silver_dkk_per_g=None,
        ))
    st = compute_spot_tracking("X", bars, spots)
    assert st.correlation is not None
    assert abs(st.correlation) < 0.5


def test_spot_tracking_returns_none_with_insufficient_data() -> None:
    st = compute_spot_tracking("Empty", [], [])
    assert st.correlation is None
    assert st.lag_hours is None
    assert st.sensitivity is None


from app.reports.analytics import classify_fingerprint  # noqa: E402


def test_classify_high_cadence_tight_active() -> None:
    tag = classify_fingerprint(
        changes_per_week=8.0,
        spot_correlation=0.92,
        weekend_change_count=2,
    )
    assert tag == "high-cadence \u00b7 tight-tracking \u00b7 weekend-active"


def test_classify_low_cadence_decoupled_frozen() -> None:
    tag = classify_fingerprint(
        changes_per_week=0.5,
        spot_correlation=0.2,
        weekend_change_count=0,
    )
    assert tag == "low-cadence \u00b7 decoupled \u00b7 weekend-frozen"


def test_classify_med_cadence_loose_frozen() -> None:
    tag = classify_fingerprint(
        changes_per_week=3.0,
        spot_correlation=0.7,
        weekend_change_count=0,
    )
    assert tag == "med-cadence \u00b7 loose-tracking \u00b7 weekend-frozen"


def test_classify_unknown_when_correlation_missing() -> None:
    tag = classify_fingerprint(
        changes_per_week=4.0,
        spot_correlation=None,
        weekend_change_count=0,
    )
    assert "tracking-unknown" in tag
