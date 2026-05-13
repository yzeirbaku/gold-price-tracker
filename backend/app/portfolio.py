"""Per-user portfolio: purchase CRUD + P&L assembly.

All endpoints require a session cookie (require_session). Historical spot is
fetched once at write time and frozen onto the row in DKK/gram; current value
is computed from live spot at read time.
"""
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth_session import AuthedUser, require_session
from .db import get_pool
from .fx import fetch_usd_to, fetch_usd_to_dkk_on
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
    Both sources are walked back through weekends/holidays if needed."""
    on_date = purchased_at.astimezone(UTC).date()
    try:
        usd_per_g = await fetch_historical_usd_per_gram(metal, on_date)
    except HistoricalSpotUnavailable as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    async with httpx.AsyncClient() as client:
        dkk_rate = await fetch_usd_to_dkk_on(client, on_date)
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
