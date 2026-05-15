"""Per-user portfolio: purchase CRUD + P&L assembly.

All endpoints require a session cookie (require_session). Historical spot is
fetched once at write time and frozen onto the row in DKK/gram; current value
is computed from live spot at read time.
"""
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth_session import AuthedUser, require_session
from .db import get_pool
from .fx import HistoricalFxUnavailable, fetch_usd_to, fetch_usd_to_dkk_on
from .spot import (
    HistoricalSpotUnavailable,
    fetch_historical_usd_per_gram,
    fetch_spot_usd_per_gram,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

Metal = Literal["gold", "silver"]

# Whitelist of columns PATCH may UPDATE. The dynamic UPDATE in
# update_purchase interpolates column names directly, so this set is the
# guardrail against SQLi if a future change accidentally widens the
# `updates` dict to include non-pydantic-bound keys.
_ALLOWED_UPDATE_COLS: frozenset[str] = frozenset({
    "metal", "gross_weight_g", "purity", "price_paid_dkk", "purchased_at",
    "label", "dealer", "notes", "spot_at_purchase_dkk_per_g",
})


class PurchaseCreate(BaseModel):
    metal: Metal
    gross_weight_g: Decimal = Field(gt=0)
    purity: Decimal = Field(gt=0, le=1)
    price_paid_dkk: Decimal = Field(ge=0)
    purchased_at: datetime
    label: str = Field(min_length=1, max_length=200)
    dealer: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class PurchaseUpdate(BaseModel):
    metal: Metal | None = None
    gross_weight_g: Decimal | None = Field(default=None, gt=0)
    purity: Decimal | None = Field(default=None, gt=0, le=1)
    price_paid_dkk: Decimal | None = Field(default=None, ge=0)
    purchased_at: datetime | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    dealer: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


async def _fetch_historical_spot_dkk_per_g(metal: Metal, purchased_at: datetime) -> Decimal:
    """USD spot on the purchase date × USD→DKK on the same date.
    Both sources are walked back through weekends/holidays if needed.

    Surfaces upstream failures (yfinance, Frankfurter) as HTTP 502 so the
    user can retry. We deliberately do NOT silently fall back to a stamped
    static rate here — the result lands on a `purchases` row and stays
    frozen forever, defining the cost-basis premium. Better to ask for a
    retry than bake an off-by-7% rate into history.
    """
    on_date = purchased_at.astimezone(UTC).date()
    try:
        usd_per_g = await fetch_historical_usd_per_gram(metal, on_date)
    except HistoricalSpotUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    try:
        async with httpx.AsyncClient() as client:
            dkk_rate = await fetch_usd_to_dkk_on(client, on_date)
    except HistoricalFxUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return Decimal(str(usd_per_g * dkk_rate)).quantize(Decimal("0.0001"))


async def _current_spot_dkk_per_g() -> dict[Metal, Decimal]:
    """Live spot in DKK/g for both metals. Uses today's FX."""
    async with httpx.AsyncClient() as client:
        usd_per_g = await fetch_spot_usd_per_gram(client)
        rates, _ = await fetch_usd_to(client)
    if usd_per_g is None:
        raise HTTPException(status_code=502, detail="live spot unavailable")
    dkk = rates["DKK"]
    return {
        "gold": Decimal(str(usd_per_g["gold"] * dkk)).quantize(Decimal("0.0001")),
        "silver": Decimal(str(usd_per_g["silver"] * dkk)).quantize(Decimal("0.0001")),
    }


def _decorate(row: dict, current_spot: dict[Metal, Decimal]) -> dict:
    """Add computed P&L fields to a raw purchase row."""
    gross = Decimal(row["gross_weight_g"])
    purity = Decimal(row["purity"])
    paid = Decimal(row["price_paid_dkk"])
    spot_then_raw = row["spot_at_purchase_dkk_per_g"]
    spot_then = Decimal(spot_then_raw) if spot_then_raw else None
    metal = row["metal"]
    fine_g = (gross * purity).quantize(Decimal("0.0001"))
    spot_now = current_spot[metal]
    value_now = (spot_now * fine_g).quantize(Decimal("0.01"))
    pnl_dkk = (value_now - paid).quantize(Decimal("0.01"))
    pnl_pct = (pnl_dkk / paid * 100).quantize(Decimal("0.01")) if paid > 0 else Decimal("0")
    if spot_then and spot_then > 0:
        cost_basis_spot = (spot_then * fine_g).quantize(Decimal("0.01"))
        purchase_premium_pct = (
            (paid - cost_basis_spot) / cost_basis_spot * 100
        ).quantize(Decimal("0.01")) if cost_basis_spot > 0 else None
    else:
        purchase_premium_pct = None
    return {
        "id": str(row["id"]),
        "metal": metal,
        "gross_weight_g": float(gross),
        "purity": float(purity),
        "fine_weight_g": float(fine_g),
        "price_paid_dkk": float(paid),
        "purchased_at": row["purchased_at"].isoformat(),
        "label": row["label"],
        "dealer": row["dealer"],
        "notes": row["notes"],
        "spot_at_purchase_dkk_per_g": float(spot_then) if spot_then else None,
        "purchase_premium_pct": (
            float(purchase_premium_pct) if purchase_premium_pct is not None else None
        ),
        "current_spot_dkk_per_g": float(spot_now),
        "current_value_dkk": float(value_now),
        "pnl_dkk": float(pnl_dkk),
        "pnl_pct": float(pnl_pct),
    }


def _summary(decorated: list[dict]) -> dict:
    total_paid = Decimal("0")
    total_value = Decimal("0")
    by_metal: dict[str, dict[str, Decimal]] = {
        "gold": {"paid": Decimal("0"), "value": Decimal("0"), "fine_g": Decimal("0")},
        "silver": {"paid": Decimal("0"), "value": Decimal("0"), "fine_g": Decimal("0")},
    }
    for p in decorated:
        paid = Decimal(str(p["price_paid_dkk"]))
        val = Decimal(str(p["current_value_dkk"]))
        fg = Decimal(str(p["fine_weight_g"]))
        total_paid += paid
        total_value += val
        m = p["metal"]
        by_metal[m]["paid"] += paid
        by_metal[m]["value"] += val
        by_metal[m]["fine_g"] += fg
    total_pnl = total_value - total_paid
    total_pnl_pct = (total_pnl / total_paid * 100) if total_paid > 0 else Decimal("0")
    return {
        "total_paid_dkk": float(total_paid),
        "total_value_dkk": float(total_value),
        "total_pnl_dkk": float(total_pnl),
        "total_pnl_pct": float(total_pnl_pct.quantize(Decimal("0.01"))),
        "by_metal": {
            m: {
                "paid_dkk": float(by_metal[m]["paid"]),
                "value_dkk": float(by_metal[m]["value"]),
                "fine_weight_g": float(by_metal[m]["fine_g"]),
                "pnl_dkk": float(by_metal[m]["value"] - by_metal[m]["paid"]),
            }
            for m in ("gold", "silver")
        },
    }


@router.get("")
async def list_portfolio(user: AuthedUser = Depends(require_session)) -> dict:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM purchases WHERE user_id = $1 ORDER BY purchased_at DESC",
            user.id,
        )
    current = await _current_spot_dkk_per_g()
    decorated = [_decorate(dict(r), current) for r in rows]
    return {"purchases": decorated, "summary": _summary(decorated)}


@router.post("", status_code=201)
async def create_purchase(
    body: PurchaseCreate,
    user: AuthedUser = Depends(require_session),
) -> dict:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    spot_at_purchase = await _fetch_historical_spot_dkk_per_g(body.metal, body.purchased_at)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO purchases ("
            "  user_id, metal, gross_weight_g, purity, price_paid_dkk, purchased_at, "
            "  label, dealer, notes, spot_at_purchase_dkk_per_g"
            ") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING *",
            user.id, body.metal, body.gross_weight_g, body.purity, body.price_paid_dkk,
            body.purchased_at, body.label, body.dealer, body.notes, spot_at_purchase,
        )
    current = await _current_spot_dkk_per_g()
    return _decorate(dict(row), current)


@router.patch("/{purchase_id}")
async def update_purchase(
    purchase_id: UUID,
    body: PurchaseUpdate,
    user: AuthedUser = Depends(require_session),
) -> dict:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM purchases WHERE id = $1 AND user_id = $2",
            purchase_id, user.id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="purchase not found")

        if "purchased_at" in updates or "metal" in updates:
            metal = updates.get("metal", existing["metal"])
            purchased_at = updates.get("purchased_at", existing["purchased_at"])
            updates["spot_at_purchase_dkk_per_g"] = await _fetch_historical_spot_dkk_per_g(
                metal, purchased_at
            )

        set_clauses = []
        values: list = []
        for i, (k, v) in enumerate(updates.items(), start=1):
            if k not in _ALLOWED_UPDATE_COLS:
                raise HTTPException(status_code=400, detail=f"unknown field: {k}")
            set_clauses.append(f"{k} = ${i}")
            values.append(v)
        values.append(purchase_id)
        values.append(user.id)
        sql = (
            f"UPDATE purchases SET {', '.join(set_clauses)} "
            f"WHERE id = ${len(values) - 1} AND user_id = ${len(values)} RETURNING *"
        )
        row = await conn.fetchrow(sql, *values)
    current = await _current_spot_dkk_per_g()
    return _decorate(dict(row), current)


@router.delete("/{purchase_id}", status_code=204)
async def delete_purchase(
    purchase_id: UUID,
    user: AuthedUser = Depends(require_session),
) -> None:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM purchases WHERE id = $1 AND user_id = $2",
            purchase_id, user.id,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="purchase not found")


# ── /portfolio/history ──────────────────────────────────────────────────────
# Reconstructs portfolio market value over time from spot_snapshots + purchases.
# No new table; everything is computed on demand. See CLAUDE.md for the design.

RANGE_TO_DAYS: dict[str, int | None] = {
    "1w": 7,
    "1m": 30,
    "6m": 183,   # ~6 calendar months
    "1y": 365,
    "all": None,
}
MetalFilter = Literal["all", "gold", "silver"]

# Max points returned to the chart. 1y at 20-min cadence is ~26k rows, which
# would dwarf the JSON payload and slow the chart for no visual gain — uniform
# decimation to ≤ HISTORY_MAX_POINTS keeps everything snappy.
HISTORY_MAX_POINTS = 500

# Cap the live-spot fetch inside /portfolio/history so a flaky upstream
# (api.gold-api.com / frankfurter.dev) can only delay the chart by ~3s.
# httpx's default timeouts can compound across the two upstream calls to
# ~20s worst case — too long for an auxiliary chart. Pre-existing
# /portfolio path has the same risk; consider capping there too if it
# ever bites.
LIVE_SPOT_TIMEOUT_S = 3.0


def _reconstruct_value_series(
    purchases: list[dict[str, Any]],
    spot_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """For each spot row, sum fine_g × spot_per_g over all purchases held at
    that time. Purchases are expected sorted by purchased_at asc; spot_rows
    by fetched_at asc. Two-pointer walk, O(N+M).

    Purchases from BEFORE the first spot row are included naturally — at the
    first iteration we advance through every purchase whose `purchased_at`
    is ≤ the first spot's `fetched_at`, so prior holdings contribute to the
    period's opening value.
    """
    gold_fine = Decimal("0")
    silver_fine = Decimal("0")
    pi = 0
    points: list[dict[str, Any]] = []
    for sr in spot_rows:
        t = sr["fetched_at"]
        while pi < len(purchases) and purchases[pi]["purchased_at"] <= t:
            p = purchases[pi]
            fine = Decimal(p["gross_weight_g"]) * Decimal(p["purity"])
            if p["metal"] == "gold":
                gold_fine += fine
            else:
                silver_fine += fine
            pi += 1
        gold_spot = sr["gold_dkk_per_g"]
        silver_spot = sr["silver_dkk_per_g"]
        value = Decimal("0")
        if gold_spot is not None:
            value += gold_fine * Decimal(gold_spot)
        if silver_spot is not None:
            value += silver_fine * Decimal(silver_spot)
        points.append({"t": t, "value_dkk": float(value)})
    return points


def _downsample(
    points: list[dict[str, Any]], max_points: int = HISTORY_MAX_POINTS,
) -> list[dict[str, Any]]:
    """Uniform-stride decimation that always keeps the first and last point.
    Returns the input unchanged when already ≤ max_points."""
    n = len(points)
    if n <= max_points:
        return points
    step = (n - 1) / (max_points - 1)
    return [points[round(i * step)] for i in range(max_points)]


def _period_change(
    points: list[dict[str, Any]],
    purchases: list[dict[str, Any]],
) -> dict[str, float]:
    """Deposit-adjusted change over the period spanned by `points`.

    Modified Dietz simplified: treat each purchase made strictly after the
    first point as a deposit at its purchased_at. The change is
    `current - start - net_purchases`; the percent uses
    `start + net_purchases` as denominator so a pure capital injection
    shows 0% (not infinite). Returns zeros for an empty series.
    """
    if not points:
        return {
            "period_start_value_dkk": 0.0,
            "current_value_dkk": 0.0,
            "net_purchases_in_period_dkk": 0.0,
            "period_change_dkk": 0.0,
            "period_change_pct": 0.0,
        }
    period_start = points[0]["t"]
    period_end = points[-1]["t"]
    start_value = points[0]["value_dkk"]
    current_value = points[-1]["value_dkk"]
    # Strict-greater on the lower bound: a purchase at exactly the first
    # spot row's timestamp is already reflected in start_value (see the
    # `<=` advance condition in _reconstruct_value_series).
    net_purchases = sum(
        float(p["price_paid_dkk"])
        for p in purchases
        if period_start < p["purchased_at"] <= period_end
    )
    change_dkk = current_value - start_value - net_purchases
    denom = start_value + net_purchases
    change_pct = (change_dkk / denom * 100) if denom > 0 else 0.0
    return {
        "period_start_value_dkk": round(start_value, 2),
        "current_value_dkk": round(current_value, 2),
        "net_purchases_in_period_dkk": round(net_purchases, 2),
        "period_change_dkk": round(change_dkk, 2),
        "period_change_pct": round(change_pct, 2),
    }


@router.get("/history")
async def portfolio_history(
    range: str = "1m",
    metal: MetalFilter = "all",
    user: AuthedUser = Depends(require_session),
) -> dict[str, Any]:
    """Time series of portfolio market value in DKK over the selected range.

    Range pills: 1w / 1m / 6m / 1y / all. `metal` mirrors the gold/silver
    filter chip in the summary panel so the chart follows the table.

    No new table — we reconstruct on demand by joining `purchases` against
    `spot_snapshots` rows in the range, plus a final synthetic point from
    live spot so the chart's tail stays consistent with the summary card's
    live current-value number (otherwise the chart can lag by up to 20 min).
    """
    if range not in RANGE_TO_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"range must be one of {sorted(RANGE_TO_DAYS)}",
        )
    # `metal` is a Literal — FastAPI validates it at request parse time and
    # returns 422 for anything else. No runtime check needed.
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")

    async with pool.acquire() as conn:
        if metal == "all":
            purchase_rows = await conn.fetch(
                "SELECT purchased_at, metal, gross_weight_g, purity, "
                "price_paid_dkk FROM purchases WHERE user_id = $1 "
                "ORDER BY purchased_at ASC",
                user.id,
            )
        else:
            purchase_rows = await conn.fetch(
                "SELECT purchased_at, metal, gross_weight_g, purity, "
                "price_paid_dkk FROM purchases WHERE user_id = $1 "
                "AND metal = $2 ORDER BY purchased_at ASC",
                user.id, metal,
            )

        if not purchase_rows:
            return {
                "range": range, "metal": metal, "points": [],
                "current_value_dkk": 0.0, "period_start_value_dkk": 0.0,
                "net_purchases_in_period_dkk": 0.0,
                "period_change_dkk": 0.0, "period_change_pct": 0.0,
                "first_purchase_at": None,
                "clamped_to_first_purchase": False,
            }

        purchases = [dict(r) for r in purchase_rows]
        first_purchase_at: datetime = purchases[0]["purchased_at"]

        # Range start: clamp to first_purchase_at so "1Y" on a 2-week-old
        # portfolio shows the line starting at the first purchase, not 14
        # days of flatline-zero followed by a tiny tail. `clamped` is
        # surfaced to the frontend so it can render an honest
        # "since DD-MM-YYYY" caption when the requested window is wider
        # than the user's holding history.
        days = RANGE_TO_DAYS[range]
        if days is None:
            range_start = first_purchase_at
            clamped = False  # "all" was the explicit ask — no surprise to flag
        else:
            window_start = datetime.now(tz=UTC) - timedelta(days=days)
            clamped = first_purchase_at > window_start
            range_start = max(window_start, first_purchase_at)

        spot_rows_raw = await conn.fetch(
            "SELECT fetched_at, gold_dkk_per_g, silver_dkk_per_g "
            "FROM spot_snapshots WHERE fetched_at >= $1 "
            "ORDER BY fetched_at ASC",
            range_start,
        )

    spot_rows = [dict(r) for r in spot_rows_raw]

    # Append a synthetic "now" point using live spot so the chart's last
    # value matches the summary card. A purchase added 1 minute ago wouldn't
    # otherwise be visible on the line until the next 20-min snapshot.
    #
    # Skip the synthetic point if the DB returned zero rows in range — a
    # lone synthetic point produces a single-dot chart and confuses the
    # frontend's "not enough snapshot history" empty state. Letting points
    # stay empty lets that empty state render correctly.
    if spot_rows_raw:
        try:
            current = await asyncio.wait_for(
                _current_spot_dkk_per_g(), timeout=LIVE_SPOT_TIMEOUT_S,
            )
            now_t = datetime.now(tz=UTC)
            spot_rows.append({
                "fetched_at": now_t,
                "gold_dkk_per_g": current["gold"],
                "silver_dkk_per_g": current["silver"],
            })
            # Defensive sort: in pathological cases (clock skew, test seed
            # data with future fetched_at) the synthetic point may not be
            # strictly latest. The two-pointer walk in
            # _reconstruct_value_series assumes ascending order — one
            # cheap sort here keeps it bulletproof.
            spot_rows.sort(key=lambda r: r["fetched_at"])
        except (HTTPException, TimeoutError):
            # Live spot temporarily unavailable / slow — fall back to
            # snapshot-only. Chart tail will lag by ≤ 20 min but the
            # endpoint still returns promptly.
            logger.warning("portfolio_history: live spot unavailable; using snapshot tail")

    points = _reconstruct_value_series(purchases, spot_rows)
    points = _downsample(points)
    change = _period_change(points, purchases)

    return {
        "range": range,
        "metal": metal,
        "points": [
            {"t": p["t"].isoformat(), "value_dkk": round(p["value_dkk"], 2)}
            for p in points
        ],
        "first_purchase_at": first_purchase_at.isoformat(),
        "clamped_to_first_purchase": clamped,
        **change,
    }
