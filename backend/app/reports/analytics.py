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
