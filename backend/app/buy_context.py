"""Compute "buy now or wait?" context for a single dealer × product.

Returns today's premium vs the dealer's typical band (IQR) over the last
N days, the lowest observed premium in that window, and a verdict bucket.
Used by /context/bar/* and /context/coin/* to power the inline context
panel on the prices page.

The "today" row is the most recent observation in the window. `is_new_low`
is True only when today's premium is *strictly* less than every prior
observation in the window — equal-to-the-min doesn't count (happens often
with sticky-pricing dealers).
"""
from dataclasses import dataclass
from datetime import datetime
from statistics import quantiles
from typing import Literal

import asyncpg

Verdict = Literal[
    "below typical", "in line with typical", "above typical", "insufficient data",
]

MIN_OBS_FOR_CONTEXT = 5


@dataclass(frozen=True)
class BuyContext:
    today_premium_pct: float | None
    today_premium_at: datetime | None
    min_premium_pct: float | None
    min_premium_at: datetime | None
    iqr_low_premium_pct: float | None
    iqr_high_premium_pct: float | None
    n_observations: int
    is_new_low: bool
    verdict: Verdict


def _compute(rows: list[tuple[datetime, float]]) -> BuyContext:
    """rows is (fetched_at, premium_pct) sorted chronologically ascending."""
    n = len(rows)
    if n < MIN_OBS_FOR_CONTEXT:
        return BuyContext(
            today_premium_pct=None, today_premium_at=None,
            min_premium_pct=None, min_premium_at=None,
            iqr_low_premium_pct=None, iqr_high_premium_pct=None,
            n_observations=n, is_new_low=False, verdict="insufficient data",
        )
    today_ts, today_prem = rows[-1]
    prior = rows[:-1]
    prior_min_ts, prior_min_prem = min(prior, key=lambda r: r[1])
    # Strictly less than every prior observation. Equal-to-min doesn't count.
    is_new_low = today_prem < prior_min_prem
    min_ts = today_ts if is_new_low else prior_min_ts
    min_prem = today_prem if is_new_low else prior_min_prem

    prems = [p for _, p in rows]
    q1, _, q3 = quantiles(prems, n=4)

    verdict: Verdict
    if today_prem < q1:
        verdict = "below typical"
    elif today_prem > q3:
        verdict = "above typical"
    else:
        verdict = "in line with typical"

    return BuyContext(
        today_premium_pct=round(today_prem, 2),
        today_premium_at=today_ts,
        min_premium_pct=round(min_prem, 2),
        min_premium_at=min_ts,
        iqr_low_premium_pct=round(q1, 2),
        iqr_high_premium_pct=round(q3, 2),
        n_observations=n,
        is_new_low=is_new_low,
        verdict=verdict,
    )


async def load_bar_context(
    conn: asyncpg.Connection, dealer: str, size_g: float, window_days: int = 30,
) -> BuyContext:
    rows = await conn.fetch(
        f"""
        SELECT fetched_at, price_dkk, spot_gold_dkk_per_g
        FROM bar_snapshots
        WHERE dealer = $1 AND size_g = $2 AND status = 'ok'
          AND fetched_at >= NOW() - INTERVAL '{int(window_days)} days'
        ORDER BY fetched_at ASC
        """,
        dealer, size_g,
    )
    premiums: list[tuple[datetime, float]] = []
    for r in rows:
        if r["price_dkk"] is None or r["spot_gold_dkk_per_g"] is None:
            continue
        ref = float(r["spot_gold_dkk_per_g"]) * size_g
        if ref <= 0:
            continue
        prem = (float(r["price_dkk"]) - ref) / ref * 100.0
        premiums.append((r["fetched_at"], prem))
    return _compute(premiums)


async def load_coin_context(
    conn: asyncpg.Connection, dealer: str, coin_type: str, fine_gold_g: float,
    window_days: int = 30,
) -> BuyContext:
    rows = await conn.fetch(
        f"""
        SELECT fetched_at, price_dkk, spot_gold_dkk_per_g
        FROM coin_snapshots
        WHERE dealer = $1 AND coin_type = $2 AND status = 'ok'
          AND ABS(fine_gold_g - $3::numeric) < 0.005
          AND fetched_at >= NOW() - INTERVAL '{int(window_days)} days'
        ORDER BY fetched_at ASC
        """,
        dealer, coin_type, fine_gold_g,
    )
    premiums: list[tuple[datetime, float]] = []
    for r in rows:
        if r["price_dkk"] is None or r["spot_gold_dkk_per_g"] is None:
            continue
        ref = float(r["spot_gold_dkk_per_g"]) * fine_gold_g
        if ref <= 0:
            continue
        prem = (float(r["price_dkk"]) - ref) / ref * 100.0
        premiums.append((r["fetched_at"], prem))
    return _compute(premiums)


def context_to_dict(ctx: BuyContext) -> dict[str, object]:
    return {
        "today_premium_pct": ctx.today_premium_pct,
        "today_premium_at": (
            ctx.today_premium_at.isoformat() if ctx.today_premium_at else None
        ),
        "min_premium_pct": ctx.min_premium_pct,
        "min_premium_at": (
            ctx.min_premium_at.isoformat() if ctx.min_premium_at else None
        ),
        "iqr_low_premium_pct": ctx.iqr_low_premium_pct,
        "iqr_high_premium_pct": ctx.iqr_high_premium_pct,
        "n_observations": ctx.n_observations,
        "is_new_low": ctx.is_new_low,
        "verdict": ctx.verdict,
    }
