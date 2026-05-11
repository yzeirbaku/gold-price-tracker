"""Per-size and per-coin aggregation tables for reports.

A "row" describes one dealer's stats for one (size_g) or (coin_type, size_label)
slice over the period: median price, median premium %, min/max premium %,
and % time held cheapest.
"""
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import median

from app.reports.loader import BarPoint, CoinPoint


@dataclass(frozen=True)
class BarSizeRow:
    dealer: str
    median_price_dkk: float | None
    median_premium_pct: float | None
    min_premium_pct: float | None
    max_premium_pct: float | None
    pct_time_cheapest: float | None


@dataclass(frozen=True)
class CoinVariantRow:
    dealer: str
    median_price_dkk: float | None
    median_premium_pct: float | None
    min_premium_pct: float | None
    max_premium_pct: float | None
    pct_time_cheapest: float | None


def _premium_for_bar(p: BarPoint) -> float | None:
    if (p.status != "ok" or p.price_dkk is None
            or p.spot_dkk_per_g is None or p.spot_dkk_per_g <= 0):
        return None
    ref = p.spot_dkk_per_g * p.size_g
    if ref <= 0:
        return None
    return (p.price_dkk - ref) / ref * 100.0


def _premium_for_coin(c: CoinPoint) -> float | None:
    if (c.status != "ok" or c.price_dkk is None
            or c.spot_dkk_per_g is None or c.fine_gold_g is None
            or c.spot_dkk_per_g <= 0):
        return None
    ref = c.spot_dkk_per_g * c.fine_gold_g
    if ref <= 0:
        return None
    return (c.price_dkk - ref) / ref * 100.0


def build_bar_table(
    points: Iterable[BarPoint], size_g: float,
) -> list[BarSizeRow]:
    """One row per dealer for this size, plus a final 'Market' aggregate row."""
    pts = [p for p in points if p.size_g == size_g]
    if not pts:
        return []

    by_dealer: dict[str, list[BarPoint]] = defaultdict(list)
    for p in pts:
        by_dealer[p.dealer].append(p)

    # %-time-cheapest: at each snapshot, find dealer(s) with min premium.
    by_ts: dict[object, list[tuple[str, float]]] = defaultdict(list)
    for p in pts:
        prem = _premium_for_bar(p)
        if prem is not None:
            by_ts[p.fetched_at].append((p.dealer, prem))
    cheapest_counts: dict[str, int] = defaultdict(int)
    total_ts = 0
    for _ts, entries in by_ts.items():
        if not entries:
            continue
        min_prem = min(e[1] for e in entries)
        for dealer, prem in entries:
            if prem == min_prem:
                cheapest_counts[dealer] += 1
        total_ts += 1

    rows: list[BarSizeRow] = []
    for dealer, dealer_pts in by_dealer.items():
        prems = [pr for pr in (_premium_for_bar(p) for p in dealer_pts) if pr is not None]
        prices = [p.price_dkk for p in dealer_pts
                  if p.status == "ok" and p.price_dkk is not None]
        med_price = round(median(prices), 2) if prices else None
        med_prem = round(median(prems), 2) if prems else None
        min_premium = round(min(prems), 2) if prems else None
        max_premium = round(max(prems), 2) if prems else None
        pct = (
            round(cheapest_counts[dealer] / total_ts * 100, 1) if total_ts > 0 else None
        )
        rows.append(BarSizeRow(
            dealer=dealer, median_price_dkk=med_price,
            median_premium_pct=med_prem,
            min_premium_pct=min_premium, max_premium_pct=max_premium,
            pct_time_cheapest=pct,
        ))

    rows.sort(key=lambda r: (
        r.median_premium_pct is None,
        r.median_premium_pct if r.median_premium_pct is not None else 0.0,
    ))

    all_prems = [pr for p in pts for pr in [_premium_for_bar(p)] if pr is not None]
    market_med = round(median(all_prems), 2) if all_prems else None
    market_min = round(min(all_prems), 2) if all_prems else None
    market_max = round(max(all_prems), 2) if all_prems else None
    market_med_price = round(
        median([p.price_dkk for p in pts
                if p.status == "ok" and p.price_dkk is not None]), 2,
    ) if any(p.status == "ok" and p.price_dkk is not None for p in pts) else None
    rows.append(BarSizeRow(
        dealer="Market", median_price_dkk=market_med_price,
        median_premium_pct=market_med,
        min_premium_pct=market_min, max_premium_pct=market_max,
        pct_time_cheapest=None,
    ))
    return rows


def build_coin_table(
    points: Iterable[CoinPoint], coin_type: str, size_label: str,
) -> list[CoinVariantRow]:
    """One row per dealer offering this (coin_type, size_label), plus a Market row."""
    pts = [p for p in points
           if p.coin_type == coin_type and p.size_label == size_label]
    if not pts:
        return []

    by_dealer: dict[str, list[CoinPoint]] = defaultdict(list)
    for p in pts:
        by_dealer[p.dealer].append(p)

    by_ts: dict[object, list[tuple[str, float]]] = defaultdict(list)
    for p in pts:
        prem = _premium_for_coin(p)
        if prem is not None:
            by_ts[p.fetched_at].append((p.dealer, prem))
    cheapest_counts: dict[str, int] = defaultdict(int)
    total_ts = 0
    for entries in by_ts.values():
        if not entries:
            continue
        min_prem = min(e[1] for e in entries)
        for dealer, prem in entries:
            if prem == min_prem:
                cheapest_counts[dealer] += 1
        total_ts += 1

    rows: list[CoinVariantRow] = []
    for dealer, dealer_pts in by_dealer.items():
        prems = [pr for pr in (_premium_for_coin(p) for p in dealer_pts) if pr is not None]
        prices = [p.price_dkk for p in dealer_pts
                  if p.status == "ok" and p.price_dkk is not None]
        med_price = round(median(prices), 2) if prices else None
        med_prem = round(median(prems), 2) if prems else None
        min_premium = round(min(prems), 2) if prems else None
        max_premium = round(max(prems), 2) if prems else None
        pct = round(cheapest_counts[dealer] / total_ts * 100, 1) if total_ts > 0 else None
        rows.append(CoinVariantRow(
            dealer=dealer, median_price_dkk=med_price,
            median_premium_pct=med_prem,
            min_premium_pct=min_premium, max_premium_pct=max_premium,
            pct_time_cheapest=pct,
        ))

    rows.sort(key=lambda r: (
        r.median_premium_pct is None,
        r.median_premium_pct if r.median_premium_pct is not None else 0.0,
    ))

    all_prems = [pr for p in pts for pr in [_premium_for_coin(p)] if pr is not None]
    market_med = round(median(all_prems), 2) if all_prems else None
    market_min = round(min(all_prems), 2) if all_prems else None
    market_max = round(max(all_prems), 2) if all_prems else None
    market_med_price = round(
        median([p.price_dkk for p in pts
                if p.status == "ok" and p.price_dkk is not None]), 2,
    ) if any(p.status == "ok" and p.price_dkk is not None for p in pts) else None
    rows.append(CoinVariantRow(
        dealer="Market", median_price_dkk=market_med_price,
        median_premium_pct=market_med,
        min_premium_pct=market_min, max_premium_pct=market_max,
        pct_time_cheapest=None,
    ))
    return rows
