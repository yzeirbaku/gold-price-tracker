"""Orchestrate loader + analytics + tables + notable + renderer.

`build_report(conn, window)` is the single public entrypoint. Endpoints
call it; tests patch the loader functions to inject synthetic data.
"""
from datetime import datetime
from typing import Any

import asyncpg

from app.reports.analytics import (
    classify_fingerprint,
    compute_cadence,
    compute_day_of_week,
    compute_premium_band,
    compute_spot_tracking,
    compute_time_of_day,
    compute_weekend_activity,
)
from app.reports.loader import (
    BarPoint,
    CoinPoint,
    SpotPoint,
    load_bars,
    load_coins,
    load_spot,
)
from app.reports.notable import detect_notable, detect_time_of_month_drift
from app.reports.renderer import render_report
from app.reports.tables import build_bar_table, build_coin_table
from app.reports.windows import CPH, Window


async def build_report(conn: asyncpg.Connection | None, window: Window) -> str:
    """Build the rendered HTML report covering the window."""
    bars: list[BarPoint] = await load_bars(conn, window.start_dt, window.end_dt)
    coins: list[CoinPoint] = await load_coins(conn, window.start_dt, window.end_dt)
    spots: list[SpotPoint] = await load_spot(conn, window.start_dt, window.end_dt)

    weeks = max(1.0, (window.end_dt - window.start_dt).total_seconds() / (7 * 86400))
    context: dict[str, Any] = {
        "kind": window.kind,
        "label": window.label,
        "kind_label": window.kind_label,
        "period_text": window.period_text,
        "period_start": window.period_start.isoformat(),
        "period_end": window.period_end.isoformat(),
        "generated_at": datetime.now(tz=CPH).strftime("%Y-%m-%d %H:%M:%S"),
        "spot": _build_spot_section(spots),
        "fingerprints": _build_fingerprints(bars, coins, spots, weeks),
        "bars": _build_bars_section(bars),
        "coins": _build_coins_section(coins),
        "notable": [
            {"text": b.text, "magnitude": b.magnitude}
            for b in detect_notable(bars, coins)
        ],
        "time_of_month": (
            [
                {
                    "dealer": r.dealer,
                    "weekly_avg_premium_pct": r.weekly_avg_premium_pct,
                    "delta_pp": r.delta_pp,
                }
                for r in detect_time_of_month_drift(
                    bars, coins, window.start_dt, window.end_dt,
                )
            ]
            if window.kind == "monthly" else None
        ),
    }
    return render_report(context)


def _build_spot_section(spots: list[SpotPoint]) -> dict[str, Any]:
    if not spots:
        zero = {"open": 0.0, "close": 0.0, "high": 0.0, "low": 0.0,
                "delta_dkk_per_g": 0.0, "delta_pct": 0.0}
        return {"gold": zero, "silver": zero, "weekend_flat": False, "fx_note": ""}
    gold = [s.gold_dkk_per_g for s in spots if s.gold_dkk_per_g is not None]
    silver = [s.silver_dkk_per_g for s in spots if s.silver_dkk_per_g is not None]

    def _stats(arr: list[float]) -> dict[str, float]:
        if not arr:
            return {"open": 0.0, "close": 0.0, "high": 0.0, "low": 0.0,
                    "delta_dkk_per_g": 0.0, "delta_pct": 0.0}
        open_v, close_v = arr[0], arr[-1]
        delta = close_v - open_v
        pct = (delta / open_v * 100) if open_v else 0.0
        return {
            "open": round(open_v, 2), "close": round(close_v, 2),
            "high": round(max(arr), 2), "low": round(min(arr), 2),
            "delta_dkk_per_g": round(delta, 2), "delta_pct": round(pct, 2),
        }
    return {
        "gold": _stats(gold),
        "silver": _stats(silver),
        "weekend_flat": _spot_flat_on_weekend(spots),
        "fx_note": "",
    }


def _spot_flat_on_weekend(spots: list[SpotPoint]) -> bool:
    weekend_pts = [s for s in spots
                   if s.fetched_at.astimezone(CPH).weekday() >= 5
                   and s.gold_dkk_per_g is not None]
    if len(weekend_pts) < 2:
        return False
    values = [s.gold_dkk_per_g for s in weekend_pts if s.gold_dkk_per_g is not None]
    return (max(values) - min(values)) < 0.5


def _build_fingerprints(
    bars: list[BarPoint], coins: list[CoinPoint],
    spots: list[SpotPoint], weeks: float,
) -> list[dict[str, Any]]:
    dealers = sorted({b.dealer for b in bars} | {c.dealer for c in coins})
    out: list[dict[str, Any]] = []
    for d in dealers:
        cad = compute_cadence(d, bars, weeks_in_period=weeks, coins=coins)
        wa = compute_weekend_activity(d, bars, coins=coins)
        tod = compute_time_of_day(d, bars, coins=coins)
        dow = compute_day_of_week(d, bars, coins=coins)
        st = compute_spot_tracking(d, bars, spots)
        pb = compute_premium_band(d, bars, coins=coins)
        tag = classify_fingerprint(
            changes_per_week=cad.changes_per_week,
            spot_correlation=st.correlation,
            weekend_change_count=wa.change_count,
        )
        out.append({
            "dealer": d,
            "cadence": {
                "total_changes": cad.total_changes,
                "changes_per_week": cad.changes_per_week,
                "median_interval_hours": cad.median_interval_hours,
                "latest_change": (
                    cad.latest_change.isoformat() if cad.latest_change else None
                ),
            },
            "time_of_day": {
                "morning": tod.morning, "afternoon": tod.afternoon,
                "evening": tod.evening, "night": tod.night,
            },
            "day_of_week": dow.by_day,
            "weekend": {
                "change_count": wa.change_count,
                "summary": (
                    f"{wa.change_count} change(s) on the weekend"
                    if wa.change_count else "no weekend changes"
                ),
            },
            "spot_tracking": {
                "correlation": st.correlation,
                "lag_hours": st.lag_hours,
                "sensitivity": st.sensitivity,
            },
            "premium_band": {"p25": pb.p25, "p75": pb.p75},
            "fingerprint_tag": tag,
        })
    return out


def _build_bars_section(bars: list[BarPoint]) -> list[dict[str, Any]]:
    if not bars:
        return []
    sizes = sorted({b.size_g for b in bars})
    return [
        {
            "size_g": s,
            "rows": [
                {
                    "dealer": r.dealer,
                    "median_price_dkk": r.median_price_dkk,
                    "median_premium_pct": r.median_premium_pct,
                    "spread_pp": r.spread_pp,
                    "pct_time_cheapest": r.pct_time_cheapest,
                }
                for r in build_bar_table(bars, size_g=s, bins=7)
            ],
        }
        for s in sizes
    ]


def _build_coins_section(coins: list[CoinPoint]) -> list[dict[str, Any]]:
    if not coins:
        return []
    variants: dict[tuple[str, str], None] = {}
    for c in coins:
        if c.coin_type and c.size_label:
            variants[(c.coin_type, c.size_label)] = None
    out: list[dict[str, Any]] = []
    for coin_type, size_label in sorted(variants):
        rows = build_coin_table(coins, coin_type, size_label, bins=7)
        out.append({
            "coin_type": coin_type,
            "size_label": size_label,
            "rows": [
                {
                    "dealer": r.dealer,
                    "median_price_dkk": r.median_price_dkk,
                    "median_premium_pct": r.median_premium_pct,
                    "spread_pp": r.spread_pp,
                    "pct_time_cheapest": r.pct_time_cheapest,
                }
                for r in rows
            ],
        })
    return out
