"""Per-size and per-coin aggregation tables for reports.

A "row" describes one dealer's stats for one (size_g) or (coin_type, size_label)
slice over the period: median price, median premium %, spread (max\u2212min premium
in pp), % time held cheapest, plus a unicode-block sparkline of the median
premium binned across `bins` equal-width time slices.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Iterable

from app.reports.loader import BarPoint, CoinPoint

BLOCK_CHARS = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
# 8 levels: \u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588


@dataclass(frozen=True)
class BarSizeRow:
    dealer: str
    median_price_dkk: float | None
    median_premium_pct: float | None
    spread_pp: float | None
    pct_time_cheapest: float | None
    sparkline: str


@dataclass(frozen=True)
class CoinVariantRow:
    dealer: str
    median_price_dkk: float | None
    median_premium_pct: float | None
    spread_pp: float | None
    pct_time_cheapest: float | None
    sparkline: str


def sparkline(values: list[float | None]) -> str:
    """Map a series of floats to unicode block chars. None \u2192 mid char."""
    real = [v for v in values if v is not None]
    if not real:
        return BLOCK_CHARS[3] * len(values)
    lo, hi = min(real), max(real)
    if hi == lo:
        return BLOCK_CHARS[3] * len(values)
    out: list[str] = []
    for v in values:
        if v is None:
            out.append(BLOCK_CHARS[3])
            continue
        idx = int((v - lo) / (hi - lo) * (len(BLOCK_CHARS) - 1) + 0.5)
        idx = max(0, min(len(BLOCK_CHARS) - 1, idx))
        out.append(BLOCK_CHARS[idx])
    return "".join(out)


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


def _bin_index(ts: datetime, start: datetime, end: datetime, bins: int) -> int:
    if end <= start or bins <= 0:
        return 0
    total = (end - start).total_seconds()
    if total <= 0:
        return 0
    pos = (ts - start).total_seconds() / total
    idx = int(pos * bins)
    return max(0, min(bins - 1, idx))


def build_bar_table(
    points: Iterable[BarPoint], size_g: float, bins: int,
) -> list[BarSizeRow]:
    """One row per dealer for this size, plus a final 'Market' aggregate row."""
    pts = [p for p in points if p.size_g == size_g]
    if not pts:
        return []
    start = min(p.fetched_at for p in pts)
    end = max(p.fetched_at for p in pts)

    by_dealer: dict[str, list[BarPoint]] = defaultdict(list)
    for p in pts:
        by_dealer[p.dealer].append(p)

    # %-time-cheapest: at each snapshot, find dealer(s) with min premium.
    by_ts: dict[datetime, list[tuple[str, float]]] = defaultdict(list)
    for p in pts:
        prem = _premium_for_bar(p)
        if prem is not None:
            by_ts[p.fetched_at].append((p.dealer, prem))
    cheapest_counts: dict[str, int] = defaultdict(int)
    total_ts = 0
    for ts, entries in by_ts.items():
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
        spread = round(max(prems) - min(prems), 2) if len(prems) >= 2 else None
        pct = (
            round(cheapest_counts[dealer] / total_ts * 100, 1) if total_ts > 0 else None
        )
        # Sparkline: for each bin, the median premium of all points in that bin.
        bin_buckets: list[list[float]] = [[] for _ in range(bins)]
        for p in dealer_pts:
            prem = _premium_for_bar(p)
            if prem is None:
                continue
            bin_buckets[_bin_index(p.fetched_at, start, end, bins)].append(prem)
        bin_values: list[float | None] = [
            (median(b) if b else None) for b in bin_buckets
        ]
        rows.append(BarSizeRow(
            dealer=dealer, median_price_dkk=med_price,
            median_premium_pct=med_prem, spread_pp=spread,
            pct_time_cheapest=pct, sparkline=sparkline(bin_values),
        ))

    rows.sort(key=lambda r: (
        r.median_premium_pct is None,
        r.median_premium_pct if r.median_premium_pct is not None else 0.0,
    ))

    all_prems = [pr for p in pts for pr in [_premium_for_bar(p)] if pr is not None]
    market_med = round(median(all_prems), 2) if all_prems else None
    market_spread = (
        round(max(all_prems) - min(all_prems), 2) if len(all_prems) >= 2 else None
    )
    market_med_price = round(
        median([p.price_dkk for p in pts
                if p.status == "ok" and p.price_dkk is not None]), 2,
    ) if any(p.status == "ok" and p.price_dkk is not None for p in pts) else None
    rows.append(BarSizeRow(
        dealer="Market", median_price_dkk=market_med_price,
        median_premium_pct=market_med, spread_pp=market_spread,
        pct_time_cheapest=None, sparkline="",
    ))
    return rows


def build_coin_table(
    points: Iterable[CoinPoint], coin_type: str, size_label: str, bins: int,
) -> list[CoinVariantRow]:
    """One row per dealer offering this (coin_type, size_label), plus a Market row."""
    pts = [p for p in points
           if p.coin_type == coin_type and p.size_label == size_label]
    if not pts:
        return []
    start = min(p.fetched_at for p in pts)
    end = max(p.fetched_at for p in pts)

    by_dealer: dict[str, list[CoinPoint]] = defaultdict(list)
    for p in pts:
        by_dealer[p.dealer].append(p)

    by_ts: dict[datetime, list[tuple[str, float]]] = defaultdict(list)
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
        spread = round(max(prems) - min(prems), 2) if len(prems) >= 2 else None
        pct = round(cheapest_counts[dealer] / total_ts * 100, 1) if total_ts > 0 else None
        bin_buckets: list[list[float]] = [[] for _ in range(bins)]
        for p in dealer_pts:
            prem = _premium_for_coin(p)
            if prem is None:
                continue
            bin_buckets[_bin_index(p.fetched_at, start, end, bins)].append(prem)
        bin_values: list[float | None] = [
            (median(b) if b else None) for b in bin_buckets
        ]
        rows.append(CoinVariantRow(
            dealer=dealer, median_price_dkk=med_price,
            median_premium_pct=med_prem, spread_pp=spread,
            pct_time_cheapest=pct, sparkline=sparkline(bin_values),
        ))

    rows.sort(key=lambda r: (
        r.median_premium_pct is None,
        r.median_premium_pct if r.median_premium_pct is not None else 0.0,
    ))

    all_prems = [pr for p in pts for pr in [_premium_for_coin(p)] if pr is not None]
    market_med = round(median(all_prems), 2) if all_prems else None
    market_spread = (
        round(max(all_prems) - min(all_prems), 2) if len(all_prems) >= 2 else None
    )
    market_med_price = round(
        median([p.price_dkk for p in pts
                if p.status == "ok" and p.price_dkk is not None]), 2,
    ) if any(p.status == "ok" and p.price_dkk is not None for p in pts) else None
    rows.append(CoinVariantRow(
        dealer="Market", median_price_dkk=market_med_price,
        median_premium_pct=market_med, spread_pp=market_spread,
        pct_time_cheapest=None, sparkline="",
    ))
    return rows
