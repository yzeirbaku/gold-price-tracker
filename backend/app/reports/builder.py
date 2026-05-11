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
    compute_spot_tracking_coins,
    compute_time_of_day,
    compute_weekend_activity,
    correlation_label,
    sensitivity_label,
)
from app.reports.loader import (
    BarPoint,
    CoinPoint,
    SpotPoint,
    load_bars,
    load_coins,
    load_spot,
)
from app.reports.notable import (
    detect_best_coin_deals,
    detect_notable,
    detect_time_of_month_drift,
)
from app.reports.renderer import render_report
from app.reports.tables import build_bar_table
from app.reports.windows import CPH, Window

MIN_OBSERVATIONS_FOR_FINGERPRINT = 10


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
        "coin_highlights": [
            {"text": b.text} for b in detect_best_coin_deals(coins)
        ],
        "notable": [
            {"text": b.text, "magnitude": b.magnitude}
            for b in detect_notable(bars, coins, spots)
        ],
        # Time-of-month drift only makes sense on canonical calendar-month
        # windows where week 1..N actually map to real Mon-Sun weeks. Rolling
        # last-30-days slices weeks arbitrarily; showing W1..W4 over those is
        # misleading. Also requires \u22652 weeks of data to be meaningful.
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
            if (window.kind == "monthly" and window.is_calendar_aligned
                and (window.end_dt - window.start_dt).days >= 14)
            else None
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
        categories: list[dict[str, Any]] = []
        bar_obs = sum(1 for b in bars if b.dealer == d)
        coin_obs = sum(1 for c in coins if c.dealer == d)
        if bar_obs >= MIN_OBSERVATIONS_FOR_FINGERPRINT:
            categories.append(_build_category_card(
                "Bars", d, bars=bars, coins=[],
                spot_tracking=compute_spot_tracking(d, bars, spots),
                weeks=weeks,
            ))
        if coin_obs >= MIN_OBSERVATIONS_FOR_FINGERPRINT:
            categories.append(_build_category_card(
                "Coins", d, bars=[], coins=coins,
                spot_tracking=compute_spot_tracking_coins(d, coins, spots),
                weeks=weeks,
            ))
        if categories:
            out.append({"dealer": d, "categories": categories})
    return out


def _build_category_card(
    name: str,
    dealer: str,
    bars: list[BarPoint],
    coins: list[CoinPoint],
    spot_tracking: Any,
    weeks: float,
) -> dict[str, Any]:
    cad = compute_cadence(dealer, bars, weeks_in_period=weeks, coins=coins)
    wa = compute_weekend_activity(dealer, bars, coins=coins)
    tod = compute_time_of_day(dealer, bars, coins=coins)
    dow = compute_day_of_week(dealer, bars, coins=coins)
    pb = compute_premium_band(dealer, bars, coins=coins)
    tag = classify_fingerprint(
        changes_per_week=cad.changes_per_week,
        spot_correlation=spot_tracking.correlation,
        weekend_change_count=wa.change_count,
    )
    return {
        "name": name,
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
            "correlation": spot_tracking.correlation,
            "correlation_label": correlation_label(spot_tracking.correlation),
            "sensitivity": spot_tracking.sensitivity,
            "sensitivity_label": sensitivity_label(spot_tracking.sensitivity),
        },
        "premium_band": {"p25": pb.p25, "p75": pb.p75},
        "fingerprint_tag": tag,
    }


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
                    "min_premium_pct": r.min_premium_pct,
                    "max_premium_pct": r.max_premium_pct,
                    "pct_time_cheapest": r.pct_time_cheapest,
                }
                for r in build_bar_table(bars, size_g=s)
            ],
        }
        for s in sizes
    ]
