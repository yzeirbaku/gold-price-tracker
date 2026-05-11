"""Notable-events generator: threshold-driven bullet list.

All thresholds live in CONSTANTS at the top of the module \u2014 tuning is a
one-line change. The detector is deterministic and stateless; same inputs
always produce the same bullets in the same order.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from app.reports.loader import BarPoint, CoinPoint
from app.reports.tables import _premium_for_bar, _premium_for_coin

CPH = ZoneInfo("Europe/Copenhagen")

DEFAULT_PREMIUM_STEP_PP = 1.0
DEFAULT_MAX_BULLETS = 10


@dataclass(frozen=True)
class NotableBullet:
    text: str
    magnitude: float  # larger = more prominent; used for ranking


@dataclass(frozen=True)
class TimeOfMonthRow:
    dealer: str
    weekly_avg_premium_pct: list[float]  # length 4 or 5
    delta_pp: float                      # last week \u2212 first week


def detect_notable(
    bars: Iterable[BarPoint],
    coins: Iterable[CoinPoint],
    premium_step_threshold_pp: float = DEFAULT_PREMIUM_STEP_PP,
    max_bullets: int = DEFAULT_MAX_BULLETS,
) -> list[NotableBullet]:
    bullets: list[NotableBullet] = []
    bars_list = list(bars)
    coins_list = list(coins)

    # 1. Premium step changes per (dealer, size_g)
    by_key: dict[tuple[str, str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for b in bars_list:
        prem = _premium_for_bar(b)
        if prem is None:
            continue
        by_key[("bar", b.dealer, f"{b.size_g:g}g")].append((b.fetched_at, prem))
    for c in coins_list:
        prem = _premium_for_coin(c)
        if prem is None or c.coin_type is None or c.size_label is None:
            continue
        by_key[("coin", c.dealer, f"{c.coin_type} {c.size_label}")].append(
            (c.fetched_at, prem)
        )

    for (kind, dealer, prod), series in by_key.items():
        series.sort(key=lambda t: t[0])
        for (t_prev, p_prev), (t_curr, p_curr) in zip(series, series[1:]):
            delta = p_curr - p_prev
            if abs(delta) >= premium_step_threshold_pp:
                arrow = "\u2193" if delta < 0 else "\u2191"
                day = t_curr.astimezone(CPH).strftime("%a %b %d")
                text = (
                    f"{dealer} {prod} premium {arrow}{abs(delta):.1f}pp on {day} "
                    f"({p_prev:.1f}% \u2192 {p_curr:.1f}%)"
                )
                bullets.append(NotableBullet(text=text, magnitude=abs(delta)))

    # 2. Cheapest-crown flips, per size_g (bars only \u2014 coins matrix is huge)
    bar_by_size: dict[str, list[BarPoint]] = defaultdict(list)
    for b in bars_list:
        if _premium_for_bar(b) is None:
            continue
        bar_by_size[f"{b.size_g:g}g"].append(b)
    for size_key, pts in bar_by_size.items():
        per_ts: dict[datetime, list[tuple[str, float]]] = defaultdict(list)
        for p in pts:
            prem = _premium_for_bar(p)
            if prem is not None:
                per_ts[p.fetched_at].append((p.dealer, prem))
        ranked = sorted(per_ts.items())
        prev_winner: str | None = None
        for ts, entries in ranked:
            if not entries:
                continue
            winner = min(entries, key=lambda e: e[1])[0]
            if prev_winner is not None and winner != prev_winner:
                day = ts.astimezone(CPH).strftime("%a %b %d")
                text = (
                    f"{winner} took the cheapest crown for {size_key} bars on {day} "
                    f"(from {prev_winner})"
                )
                bullets.append(NotableBullet(text=text, magnitude=0.5))
            prev_winner = winner

    # Sort by magnitude desc, cap, return
    bullets.sort(key=lambda b: b.magnitude, reverse=True)
    return bullets[:max_bullets]


def detect_time_of_month_drift(
    bars: Iterable[BarPoint],
    coins: Iterable[CoinPoint],
    period_start: datetime,
    period_end: datetime,
) -> list[TimeOfMonthRow]:
    bars_list = list(bars)
    coins_list = list(coins)
    # Floor division so a month (29-31 days) folds into 4 weeks rather than
    # spawning a stub 5th week of 1-3 days. For longer periods (35+ days)
    # we naturally get 5+ weekly buckets.
    weeks = max(1, (period_end - period_start).days // 7)

    def week_idx(t: datetime) -> int:
        idx = (t - period_start).days // 7
        return max(0, min(weeks - 1, idx))

    by_dealer: dict[str, list[list[float]]] = defaultdict(
        lambda: [[] for _ in range(weeks)]
    )
    for b in bars_list:
        prem = _premium_for_bar(b)
        if prem is None:
            continue
        by_dealer[b.dealer][week_idx(b.fetched_at)].append(prem)
    for c in coins_list:
        prem = _premium_for_coin(c)
        if prem is None:
            continue
        by_dealer[c.dealer][week_idx(c.fetched_at)].append(prem)

    rows: list[TimeOfMonthRow] = []
    all_weeks: list[list[float]] = [[] for _ in range(weeks)]
    for dealer, bucket_list in by_dealer.items():
        weekly_avg: list[float] = []
        for i, b in enumerate(bucket_list):
            avg = round(sum(b) / len(b), 2) if b else 0.0
            weekly_avg.append(avg)
            all_weeks[i].extend(b)
        delta = round(weekly_avg[-1] - weekly_avg[0], 2)
        rows.append(TimeOfMonthRow(
            dealer=dealer, weekly_avg_premium_pct=weekly_avg, delta_pp=delta,
        ))

    rows.sort(key=lambda r: r.dealer)
    market = [round(sum(b) / len(b), 2) if b else 0.0 for b in all_weeks]
    market_delta = round(market[-1] - market[0], 2) if market else 0.0
    rows.append(TimeOfMonthRow(
        dealer="Market", weekly_avg_premium_pct=market, delta_pp=market_delta,
    ))
    return rows
