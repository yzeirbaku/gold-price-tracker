"""Per-dealer analytics for reports.

All functions are pure \u2014 they take dataclass lists (BarPoint / CoinPoint /
SpotPoint) and return new dataclasses. No DB or HTTP.

A "product series" is the sequence of snapshots for one (dealer, size_g)
or (dealer, coin_type, size_label) combination, in chronological order.
A "change" is any pair (snapshot[i-1], snapshot[i]) where price_dkk
differs between non-null statuses.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Iterable
from zoneinfo import ZoneInfo

from app.reports.loader import BarPoint, CoinPoint

CPH = ZoneInfo("Europe/Copenhagen")


@dataclass(frozen=True)
class PriceChange:
    at: datetime
    product_key: str  # e.g. "5g" or "Krugerrand 1/4 oz"
    from_price: float
    to_price: float


@dataclass(frozen=True)
class CadenceStats:
    total_changes: int
    changes_per_week: float
    median_interval_hours: float | None
    latest_change: datetime | None


@dataclass(frozen=True)
class WeekendActivity:
    change_count: int
    changes: list[PriceChange] = field(default_factory=list)


def _bar_key(b: BarPoint) -> str:
    return f"{b.size_g:g}g"


def _coin_key(c: CoinPoint) -> str:
    return f"{c.coin_type} {c.size_label}"


def _detect_changes_for_series(series: list[tuple[datetime, float | None]],
                                key: str) -> list[PriceChange]:
    """series is [(fetched_at, price_dkk_or_None)] in chronological order."""
    changes: list[PriceChange] = []
    prev_price: float | None = None
    for at, price in series:
        if price is None:
            continue
        if prev_price is not None and price != prev_price:
            changes.append(PriceChange(
                at=at, product_key=key,
                from_price=prev_price, to_price=price,
            ))
        prev_price = price
    return changes


def _all_changes_for_dealer(
    dealer: str,
    bars: Iterable[BarPoint],
    coins: Iterable[CoinPoint] = (),
) -> list[PriceChange]:
    by_key: dict[str, list[tuple[datetime, float | None]]] = defaultdict(list)
    for b in bars:
        if b.dealer != dealer:
            continue
        by_key[_bar_key(b)].append((b.fetched_at, b.price_dkk))
    for c in coins:
        if c.dealer != dealer or c.coin_type is None or c.size_label is None:
            continue
        by_key[_coin_key(c)].append((c.fetched_at, c.price_dkk))
    changes: list[PriceChange] = []
    for key, series in by_key.items():
        series.sort(key=lambda t: t[0])
        changes.extend(_detect_changes_for_series(series, key))
    changes.sort(key=lambda c: c.at)
    return changes


def compute_cadence(
    dealer: str,
    bars: Iterable[BarPoint],
    weeks_in_period: float,
    coins: Iterable[CoinPoint] = (),
) -> CadenceStats:
    changes = _all_changes_for_dealer(dealer, bars, coins)
    if not changes:
        return CadenceStats(
            total_changes=0, changes_per_week=0.0,
            median_interval_hours=None, latest_change=None,
        )
    # Median interval is computed per-product then aggregated, so that a
    # high-frequency size doesn't dominate.
    intervals_h: list[float] = []
    by_key: dict[str, list[datetime]] = defaultdict(list)
    for ch in changes:
        by_key[ch.product_key].append(ch.at)
    for ts_list in by_key.values():
        if len(ts_list) < 2:
            continue
        for prev, curr in zip(ts_list, ts_list[1:]):
            intervals_h.append((curr - prev).total_seconds() / 3600.0)
    return CadenceStats(
        total_changes=len(changes),
        changes_per_week=round(len(changes) / weeks_in_period, 2),
        median_interval_hours=round(median(intervals_h), 2) if intervals_h else None,
        latest_change=changes[-1].at,
    )


def compute_weekend_activity(
    dealer: str,
    bars: Iterable[BarPoint],
    coins: Iterable[CoinPoint] = (),
) -> WeekendActivity:
    """Count price changes that happened on Sat/Sun in Europe/Copenhagen."""
    changes = _all_changes_for_dealer(dealer, bars, coins)
    weekend = [ch for ch in changes if ch.at.astimezone(CPH).weekday() >= 5]
    return WeekendActivity(change_count=len(weekend), changes=weekend)


from statistics import quantiles  # noqa: E402


@dataclass(frozen=True)
class TimeOfDayDist:
    morning: int    # 06-12
    afternoon: int  # 12-18
    evening: int    # 18-24
    night: int      # 00-06
    total: int


@dataclass(frozen=True)
class DayOfWeekDist:
    by_day: list[int]  # length 7, Mon..Sun
    total: int


@dataclass(frozen=True)
class PremiumBand:
    p25: float | None
    p75: float | None


def compute_time_of_day(
    dealer: str,
    bars: Iterable[BarPoint],
    coins: Iterable[CoinPoint] = (),
) -> TimeOfDayDist:
    morning = afternoon = evening = night = 0
    for ch in _all_changes_for_dealer(dealer, bars, coins):
        hour = ch.at.astimezone(CPH).hour
        if 6 <= hour < 12:
            morning += 1
        elif 12 <= hour < 18:
            afternoon += 1
        elif 18 <= hour < 24:
            evening += 1
        else:
            night += 1
    return TimeOfDayDist(
        morning=morning, afternoon=afternoon,
        evening=evening, night=night,
        total=morning + afternoon + evening + night,
    )


def compute_day_of_week(
    dealer: str,
    bars: Iterable[BarPoint],
    coins: Iterable[CoinPoint] = (),
) -> DayOfWeekDist:
    by_day = [0] * 7
    for ch in _all_changes_for_dealer(dealer, bars, coins):
        by_day[ch.at.astimezone(CPH).weekday()] += 1
    return DayOfWeekDist(by_day=by_day, total=sum(by_day))


def compute_premium_band(
    dealer: str,
    bars: Iterable[BarPoint],
    coins: Iterable[CoinPoint] = (),
) -> PremiumBand:
    """Q1 and Q3 of premium % across all of this dealer's observed products."""
    premiums: list[float] = []
    for b in bars:
        if b.dealer != dealer or b.status != "ok":
            continue
        if b.price_dkk is None or b.spot_dkk_per_g is None or b.spot_dkk_per_g <= 0:
            continue
        ref = b.spot_dkk_per_g * b.size_g
        if ref <= 0:
            continue
        premiums.append((b.price_dkk - ref) / ref * 100.0)
    for c in coins:
        if c.dealer != dealer or c.status != "ok":
            continue
        if (c.price_dkk is None or c.spot_dkk_per_g is None
                or c.fine_gold_g is None or c.spot_dkk_per_g <= 0):
            continue
        ref = c.spot_dkk_per_g * c.fine_gold_g
        if ref <= 0:
            continue
        premiums.append((c.price_dkk - ref) / ref * 100.0)
    if len(premiums) < 4:
        return PremiumBand(p25=None, p75=None)
    # statistics.quantiles returns interior cut points; n=4 \u2192 [q1, q2, q3]
    q1, _, q3 = quantiles(premiums, n=4)
    return PremiumBand(p25=round(q1, 2), p75=round(q3, 2))


import numpy as np  # noqa: E402

from app.reports.loader import SpotPoint  # noqa: E402


@dataclass(frozen=True)
class SpotTracking:
    correlation: float | None
    lag_hours: float | None
    sensitivity: float | None


def _build_dealer_avg_series(
    dealer: str, bars: Iterable[BarPoint],
) -> dict[datetime, float]:
    """At each fetched_at, average the dealer's ok-status bar prices
    normalized per-gram (price_dkk / size_g)."""
    by_ts: dict[datetime, list[float]] = defaultdict(list)
    for b in bars:
        if b.dealer != dealer or b.status != "ok" or b.price_dkk is None:
            continue
        if b.size_g <= 0:
            continue
        by_ts[b.fetched_at].append(b.price_dkk / b.size_g)
    return {ts: sum(vs) / len(vs) for ts, vs in by_ts.items()}


def compute_spot_tracking(
    dealer: str,
    bars: Iterable[BarPoint],
    spots: Iterable[SpotPoint],
) -> SpotTracking:
    """Pearson correlation + lag + sensitivity on matched snapshot pairs."""
    dealer_avg = _build_dealer_avg_series(dealer, bars)
    spot_by_ts = {
        s.fetched_at: s.gold_dkk_per_g
        for s in spots if s.gold_dkk_per_g is not None
    }
    matched_ts = sorted(set(dealer_avg) & set(spot_by_ts))
    if len(matched_ts) < 5:
        return SpotTracking(correlation=None, lag_hours=None, sensitivity=None)

    dealer_arr = np.array([dealer_avg[ts] for ts in matched_ts], dtype=float)
    spot_arr = np.array([spot_by_ts[ts] for ts in matched_ts], dtype=float)

    if np.std(dealer_arr) == 0 or np.std(spot_arr) == 0:
        return SpotTracking(correlation=None, lag_hours=None, sensitivity=None)

    corr = float(np.corrcoef(dealer_arr, spot_arr)[0, 1])

    # Lag: shift the dealer series by k in {-12..12}, pick the k that maximizes
    # the correlation. Snapshots are nominally 20 minutes apart.
    best_corr = corr
    best_k = 0
    n = len(matched_ts)
    for k in range(-12, 13):
        if k == 0 or n - abs(k) < 5:
            continue
        if k > 0:
            d = dealer_arr[k:]
            s = spot_arr[:n - k]
        else:
            d = dealer_arr[:n + k]
            s = spot_arr[-k:]
        if np.std(d) == 0 or np.std(s) == 0:
            continue
        c = float(np.corrcoef(d, s)[0, 1])
        if c > best_corr:
            best_corr = c
            best_k = k
    lag_hours = best_k * (20 / 60.0) if best_k != 0 else 0.0

    # Sensitivity: OLS slope of pct-change(dealer) vs pct-change(spot).
    pct_d = np.diff(dealer_arr) / dealer_arr[:-1]
    pct_s = np.diff(spot_arr) / spot_arr[:-1]
    mask = np.abs(pct_s) > 1e-9
    sensitivity: float | None
    if mask.sum() < 3:
        sensitivity = None
    else:
        slope, _ = np.polyfit(pct_s[mask], pct_d[mask], 1)
        sensitivity = float(round(slope, 3))

    return SpotTracking(
        correlation=round(corr, 3),
        lag_hours=round(lag_hours, 2),
        sensitivity=sensitivity,
    )
