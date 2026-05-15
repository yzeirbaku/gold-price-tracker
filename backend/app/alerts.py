"""Per-user premium alerts: configure thresholds, evaluate on every successful
snapshot tick, email the user when a cross-dealer min premium drops below the
target. See docs/superpowers/specs/2026-05-14-alerts-design.md.

Evaluation hooks into /snapshot *after* INSERTs land — the fx_stale and
outlier guards already gate it, so alerts never fire on garbage data.
"""
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth_session import AuthedUser, require_session
from .coins import COINS, FINE_GOLD_PRECISION
from .coins import fine_gold_g as compute_fine_gold_g
from .db import get_pool
from .email import EmailSendError, send_alert_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["alerts"])

AlertKind = Literal["bar", "coin"]

# Must match ALLOWED_SIZES in main.py — the only valid bar sizes the rest of
# the system scrapes/persists. Keeping this list local rather than importing
# from main avoids a circular dependency.
_ALLOWED_BAR_SIZES: tuple[Decimal, ...] = (
    Decimal("2.5"), Decimal("5"), Decimal("10"), Decimal("20"),
)

# Premium must rise back above (threshold + HYSTERESIS_PCT) before an alert
# re-arms. Prevents flapping at exactly the threshold value. Tunable.
HYSTERESIS_PCT = Decimal("0.5")

# Cap of alert-fire events per user per rolling hour. A bundled email of N
# alerts counts as N fires. Stops runaway flap from spamming the inbox.
MAX_FIRES_PER_HOUR_PER_USER = 8

# Whitelist for dynamic UPDATE in update_alert — SQLi guardrail.
_ALLOWED_UPDATE_COLS: frozenset[str] = frozenset({
    "threshold_pct", "enabled", "muted_until_recovery",
})

# Cap coins to ≤20g fine to match the scrapers' FINE_GOLD_CAP_G. Same upper
# bound used downstream when filtering listings.
_COIN_FINE_CAP_G = Decimal("20")


# --- Pydantic models -------------------------------------------------------


class AlertCreate(BaseModel):
    kind: AlertKind
    size_g: Decimal | None = Field(default=None, gt=0)
    coin_type: str | None = Field(default=None, max_length=80)
    fine_gold_g: Decimal | None = Field(default=None, gt=0)
    threshold_pct: Decimal = Field(ge=0, le=200)


class AlertUpdate(BaseModel):
    threshold_pct: Decimal | None = Field(default=None, ge=0, le=200)
    enabled: bool | None = None


# --- Helpers ---------------------------------------------------------------


def _validate_kind_payload(body: AlertCreate) -> None:
    """The CHECK constraint on the table would catch shape errors, but we'd
    rather return a clean 400 with a helpful message than a 5xx from asyncpg."""
    if body.kind == "bar":
        if body.size_g is None or body.coin_type is not None or body.fine_gold_g is not None:
            raise HTTPException(
                status_code=400,
                detail="bar alerts require size_g; coin_type/fine_gold_g must be null",
            )
        if body.size_g not in _ALLOWED_BAR_SIZES:
            raise HTTPException(
                status_code=400,
                detail=f"size_g must be one of {[float(s) for s in _ALLOWED_BAR_SIZES]}",
            )
    else:  # coin
        if body.coin_type is None or body.fine_gold_g is None or body.size_g is not None:
            raise HTTPException(
                status_code=400,
                detail="coin alerts require coin_type + fine_gold_g; size_g must be null",
            )
        if body.coin_type not in COINS:
            raise HTTPException(
                status_code=400, detail=f"unknown coin_type: {body.coin_type}",
            )


def _row_to_json(row: dict) -> dict:
    """Convert asyncpg row → JSON-friendly dict. Mutates input; safe because
    callers always work on freshly-converted copies."""
    if isinstance(row.get("id"), UUID):
        row["id"] = str(row["id"])
    if isinstance(row.get("user_id"), UUID):
        row["user_id"] = str(row["user_id"])
    for f in ("size_g", "fine_gold_g", "threshold_pct"):
        if isinstance(row.get(f), Decimal):
            row[f] = float(row[f])
    for f in ("created_at", "last_fired_at"):
        if isinstance(row.get(f), datetime):
            row[f] = row[f].isoformat()
    return row


async def _fetch_current_bar(conn: Any, size_g: Decimal) -> dict | None:
    """Lowest current bar premium for size_g from the last 90 minutes of
    bar_snapshots. Returns {premium_pct, dealer} or None if no recent row.

    The 90-min window covers ~4 missed cron ticks while still surfacing real
    outages — if the cron is healthy the most recent row is < 20 min old, so
    the "—" placeholder beyond that genuinely means "we haven't snapshotted
    recently". A wider window would silently mask broken cron with hours-old
    data labeled the same as fresh."""
    return await conn.fetchrow(
        """
        SELECT dealer,
          (price_dkk - spot_gold_dkk_per_g * size_g) /
          (spot_gold_dkk_per_g * size_g) * 100 AS premium_pct
        FROM bar_snapshots
        WHERE size_g = $1 AND status = 'ok'
          AND price_dkk IS NOT NULL AND spot_gold_dkk_per_g IS NOT NULL
          AND spot_gold_dkk_per_g > 0
          AND fetched_at >= NOW() - INTERVAL '90 minutes'
        ORDER BY (price_dkk - spot_gold_dkk_per_g * size_g) /
                 (spot_gold_dkk_per_g * size_g) ASC
        LIMIT 1
        """,
        size_g,
    )


async def _fetch_current_coin(
    conn: Any, coin_type: str, fine_gold_g: Decimal,
) -> dict | None:
    """Lowest current coin premium for (coin_type, fine_gold_g) from the last
    90 minutes. Same window rationale as _fetch_current_bar. 0.005g
    tolerance on fine_gold_g matches the history endpoint."""
    return await conn.fetchrow(
        """
        SELECT dealer,
          (price_dkk - spot_gold_dkk_per_g * fine_gold_g) /
          (spot_gold_dkk_per_g * fine_gold_g) * 100 AS premium_pct
        FROM coin_snapshots
        WHERE coin_type = $1
          AND ABS(fine_gold_g - $2::numeric) < 0.005
          AND status = 'ok'
          AND price_dkk IS NOT NULL AND spot_gold_dkk_per_g IS NOT NULL
          AND spot_gold_dkk_per_g > 0
          AND fetched_at >= NOW() - INTERVAL '90 minutes'
        ORDER BY (price_dkk - spot_gold_dkk_per_g * fine_gold_g) /
                 (spot_gold_dkk_per_g * fine_gold_g) ASC
        LIMIT 1
        """,
        coin_type, fine_gold_g,
    )


async def _fetch_current_bars_batch(
    conn: Any, size_gs: list[Decimal],
) -> dict[Decimal, dict]:
    """Cross-dealer min premium per `size_g`, batched. Returns
    {size_g: {dealer, premium_pct}} for the cheapest dealer in the last 90
    minutes. One query regardless of how many targets — used by /alerts list
    to avoid an N+1 against bar_snapshots."""
    if not size_gs:
        return {}
    rows = await conn.fetch(
        """
        WITH ranked AS (
          SELECT size_g, dealer,
            (price_dkk - spot_gold_dkk_per_g * size_g) /
            (spot_gold_dkk_per_g * size_g) * 100 AS premium_pct,
            ROW_NUMBER() OVER (
              PARTITION BY size_g
              ORDER BY (price_dkk - spot_gold_dkk_per_g * size_g) /
                       (spot_gold_dkk_per_g * size_g) ASC
            ) AS rn
          FROM bar_snapshots
          WHERE size_g = ANY($1::numeric[])
            AND status = 'ok'
            AND price_dkk IS NOT NULL AND spot_gold_dkk_per_g IS NOT NULL
            AND spot_gold_dkk_per_g > 0
            AND fetched_at >= NOW() - INTERVAL '90 minutes'
        )
        SELECT size_g, dealer, premium_pct FROM ranked WHERE rn = 1
        """,
        size_gs,
    )
    return {
        r["size_g"]: {"premium_pct": r["premium_pct"], "dealer": r["dealer"]}
        for r in rows
    }


async def _fetch_current_coins_batch(
    conn: Any, targets: list[tuple[str, Decimal]],
) -> dict[tuple[str, Decimal], dict]:
    """Cross-dealer min premium per (coin_type, fine_gold_g), batched.

    Same 90-minute window + 0.005g fine_gold_g tolerance as the single-target
    helper. The UNNEST-join collapses N decorations into one query and keys
    the result by the *target* fine_gold_g (the caller's value), so dict
    lookups in `_attach_current` line up with what went in.
    """
    if not targets:
        return {}
    coin_types = [t[0] for t in targets]
    fine_gs = [t[1] for t in targets]
    rows = await conn.fetch(
        """
        WITH targets AS (
          SELECT * FROM UNNEST($1::text[], $2::numeric[])
                       AS t(coin_type, fine_gold_g)
        ),
        ranked AS (
          SELECT t.coin_type AS t_coin_type,
                 t.fine_gold_g AS t_fine_gold_g,
                 c.dealer,
                 (c.price_dkk - c.spot_gold_dkk_per_g * c.fine_gold_g) /
                 (c.spot_gold_dkk_per_g * c.fine_gold_g) * 100 AS premium_pct,
                 ROW_NUMBER() OVER (
                   PARTITION BY t.coin_type, t.fine_gold_g
                   ORDER BY (c.price_dkk - c.spot_gold_dkk_per_g * c.fine_gold_g) /
                            (c.spot_gold_dkk_per_g * c.fine_gold_g) ASC
                 ) AS rn
          FROM coin_snapshots c
          JOIN targets t
            ON c.coin_type = t.coin_type
           AND ABS(c.fine_gold_g - t.fine_gold_g) < 0.005
          WHERE c.status = 'ok'
            AND c.price_dkk IS NOT NULL AND c.spot_gold_dkk_per_g IS NOT NULL
            AND c.spot_gold_dkk_per_g > 0
            AND c.fetched_at >= NOW() - INTERVAL '90 minutes'
        )
        SELECT t_coin_type, t_fine_gold_g, dealer, premium_pct
        FROM ranked WHERE rn = 1
        """,
        coin_types, fine_gs,
    )
    return {
        (r["t_coin_type"], r["t_fine_gold_g"]): {
            "premium_pct": r["premium_pct"], "dealer": r["dealer"],
        }
        for r in rows
    }


async def _decorate_with_current(conn: Any, row: dict) -> dict:
    """Tack on current_min_premium_pct + current_best_dealer for UI context.
    Single-row variant — used by create/update where there's only one alert
    to decorate. List path uses the batched helpers above."""
    if row["kind"] == "bar":
        rec = await _fetch_current_bar(conn, row["size_g"])
    else:
        rec = await _fetch_current_coin(conn, row["coin_type"], row["fine_gold_g"])
    out = _row_to_json(row)
    if rec is not None and rec["premium_pct"] is not None:
        out["current_min_premium_pct"] = round(float(rec["premium_pct"]), 2)
        out["current_best_dealer"] = rec["dealer"]
    else:
        out["current_min_premium_pct"] = None
        out["current_best_dealer"] = None
    return out


def _attach_current(
    row: dict,
    bar_mins: dict[Decimal, dict],
    coin_mins: dict[tuple[str, Decimal], dict],
) -> dict:
    """In-memory variant of _decorate_with_current driven by precomputed batch
    maps. Returns the same shape so list-endpoint callers can't tell the
    difference."""
    if row["kind"] == "bar":
        rec = bar_mins.get(row["size_g"])
    else:
        key = (row["coin_type"], row["fine_gold_g"].quantize(FINE_GOLD_PRECISION))
        rec = coin_mins.get(key)
    out = _row_to_json(row)
    if rec is not None and rec["premium_pct"] is not None:
        out["current_min_premium_pct"] = round(float(rec["premium_pct"]), 2)
        out["current_best_dealer"] = rec["dealer"]
    else:
        out["current_min_premium_pct"] = None
        out["current_best_dealer"] = None
    return out


# --- CRUD ------------------------------------------------------------------


@router.get("/options")
async def list_options(_: AuthedUser = Depends(require_session)) -> dict:
    """Bar sizes + coin variants the user may pick from in the dialog."""
    coin_options = []
    for coin_type, sizes in COINS.items():
        seen_fine: dict[Decimal, str] = {}
        for size_label, (gross, purity) in sizes.items():
            fine = Decimal(str(compute_fine_gold_g(gross, purity)))
            if fine > _COIN_FINE_CAP_G:
                continue
            # Dedupe by fine_gold_g — Danish 20kr has 3 monarchs with same
            # physical spec, all collapse to one alert target.
            if fine not in seen_fine:
                seen_fine[fine] = size_label
        if not seen_fine:
            continue
        coin_options.append({
            "coin_type": coin_type,
            "sizes": [
                {"size_label": lbl, "fine_gold_g": float(fine)}
                for fine, lbl in sorted(seen_fine.items(), reverse=True)
            ],
        })
    return {
        "bar_sizes": [float(s) for s in _ALLOWED_BAR_SIZES],
        "coin_options": coin_options,
    }


@router.get("/preview")
async def preview_current(
    kind: AlertKind,
    size_g: Decimal | None = None,
    coin_type: str | None = None,
    fine_gold_g: Decimal | None = None,
    _: AuthedUser = Depends(require_session),
) -> dict:
    """Look up the current cross-dealer min premium for a prospective alert
    target. Powers the "Current: X% (Dealer)" hint inside the add/edit
    dialog so the user can pick a sensible threshold without alt-tabbing
    to the prices view first."""
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    async with pool.acquire() as conn:
        if kind == "bar":
            if size_g is None:
                raise HTTPException(status_code=400, detail="size_g required for kind=bar")
            rec = await _fetch_current_bar(conn, size_g)
        else:
            if coin_type is None or fine_gold_g is None:
                raise HTTPException(
                    status_code=400,
                    detail="coin_type + fine_gold_g required for kind=coin",
                )
            rec = await _fetch_current_coin(conn, coin_type, fine_gold_g)
    if rec is None or rec["premium_pct"] is None:
        return {"current_min_premium_pct": None, "current_best_dealer": None}
    return {
        "current_min_premium_pct": round(float(rec["premium_pct"]), 2),
        "current_best_dealer": rec["dealer"],
    }


@router.get("")
async def list_alerts(user: AuthedUser = Depends(require_session)) -> dict:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM alerts WHERE user_id = $1 ORDER BY created_at DESC",
            user.id,
        )
        alerts = [dict(r) for r in rows]
        # Batch decoration: one query per kind across all targets, instead of
        # one query per alert. Pre-quantize fine_gold_g to FINE_GOLD_PRECISION
        # so the keys we send into _fetch_current_coins_batch line up with
        # what _attach_current will look up by.
        bar_sizes = sorted({
            a["size_g"] for a in alerts
            if a["kind"] == "bar" and a["size_g"] is not None
        })
        coin_targets = sorted({
            (a["coin_type"], a["fine_gold_g"].quantize(FINE_GOLD_PRECISION))
            for a in alerts
            if a["kind"] == "coin"
            and a["coin_type"] is not None and a["fine_gold_g"] is not None
        })
        bar_mins = await _fetch_current_bars_batch(conn, list(bar_sizes))
        coin_mins = await _fetch_current_coins_batch(conn, list(coin_targets))
    decorated = [_attach_current(a, bar_mins, coin_mins) for a in alerts]
    return {"alerts": decorated}


@router.post("", status_code=201)
async def create_alert(
    body: AlertCreate,
    user: AuthedUser = Depends(require_session),
) -> dict:
    _validate_kind_payload(body)
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO alerts (user_id, kind, size_g, coin_type, fine_gold_g, threshold_pct)
            VALUES ($1, $2, $3, $4, $5, $6) RETURNING *
            """,
            user.id, body.kind, body.size_g, body.coin_type,
            body.fine_gold_g, body.threshold_pct,
        )
        return await _decorate_with_current(conn, dict(row))


@router.patch("/{alert_id}")
async def update_alert(
    alert_id: UUID,
    body: AlertUpdate,
    user: AuthedUser = Depends(require_session),
) -> dict:
    updates: dict = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    # Threshold edits reset muted state — otherwise toggling 7% → 6% leaves
    # the alert stuck muted with stale semantics.
    if "threshold_pct" in updates:
        updates["muted_until_recovery"] = False
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM alerts WHERE id = $1 AND user_id = $2",
            alert_id, user.id,
        )
        if existing is None:
            raise HTTPException(status_code=404, detail="alert not found")
        set_clauses: list[str] = []
        values: list = []
        for i, (k, v) in enumerate(updates.items(), start=1):
            if k not in _ALLOWED_UPDATE_COLS:
                raise HTTPException(status_code=400, detail=f"unknown field: {k}")
            set_clauses.append(f"{k} = ${i}")
            values.append(v)
        values.append(alert_id)
        values.append(user.id)
        sql = (
            f"UPDATE alerts SET {', '.join(set_clauses)} "
            f"WHERE id = ${len(values) - 1} AND user_id = ${len(values)} RETURNING *"
        )
        row = await conn.fetchrow(sql, *values)
        return await _decorate_with_current(conn, dict(row))


@router.delete("/{alert_id}", status_code=204)
async def delete_alert(
    alert_id: UUID,
    user: AuthedUser = Depends(require_session),
) -> None:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM alerts WHERE id = $1 AND user_id = $2",
            alert_id, user.id,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="alert not found")


# --- Evaluation ------------------------------------------------------------


def _index_bar_mins(bar_rows: list[tuple]) -> dict[Decimal, dict]:
    """{size_g: {premium, dealer, brand, price}} — best deal per size from
    the just-persisted bar rows."""
    best: dict[Decimal, dict] = {}
    for row in bar_rows:
        # tuple is (fetched_at, dealer, size_g, status, price_dkk, brand, error, spot)
        _, dealer, size_g, status, price_dkk, brand, _, spot = row
        if status != "ok" or price_dkk is None or spot is None or spot == 0:
            continue
        size_dec = Decimal(str(size_g))
        ref = Decimal(str(spot)) * size_dec
        if ref <= 0:
            continue
        premium = (Decimal(str(price_dkk)) - ref) / ref * 100
        cur = best.get(size_dec)
        if cur is None or premium < cur["premium"]:
            best[size_dec] = {
                "premium": premium,
                "dealer": dealer,
                "brand": brand,
                "price_dkk": Decimal(str(price_dkk)),
            }
    return best


def _index_coin_mins(coin_rows: list[tuple]) -> dict[tuple[str, Decimal], dict]:
    """{(coin_type, fine_gold_g): {premium, dealer, size_label, price}}."""
    best: dict[tuple[str, Decimal], dict] = {}
    for row in coin_rows:
        # tuple is (fetched_at, dealer, coin_type, size_label, gross, purity,
        #           fine, status, price, error, spot, listing_url)
        _, dealer, coin_type, size_label, _, _, fine_g, status, price_dkk, _, spot, _ = row
        if status != "ok" or price_dkk is None or spot is None or spot == 0:
            continue
        if coin_type is None or fine_g is None:
            continue
        fine_dec = Decimal(str(fine_g)).quantize(FINE_GOLD_PRECISION)
        ref = Decimal(str(spot)) * fine_dec
        if ref <= 0:
            continue
        premium = (Decimal(str(price_dkk)) - ref) / ref * 100
        key = (coin_type, fine_dec)
        cur = best.get(key)
        if cur is None or premium < cur["premium"]:
            best[key] = {
                "premium": premium,
                "dealer": dealer,
                "size_label": size_label,
                "price_dkk": Decimal(str(price_dkk)),
            }
    return best


def _format_fire(alert: asyncpg.Record, hit: dict) -> dict:
    """Translate a triggered alert + hit into the dict shape expected by
    send_alert_email's HTML template."""
    if alert["kind"] == "bar":
        target = f"{alert['size_g']:g} g bar"
    else:
        size_lbl = hit.get("size_label") or ""
        target = (
            f"{alert['coin_type']} {size_lbl} "
            f"({alert['fine_gold_g']:g} g fine)"
        ).strip()
    return {
        "target": target,
        "threshold_pct": float(alert["threshold_pct"]),
        "current_premium_pct": round(float(hit["premium"]), 2),
        "best_dealer": hit["dealer"],
        "price_dkk": float(hit["price_dkk"]),
    }


async def evaluate_alerts(
    pool: Any,
    fetched_at: datetime,
    bar_rows: list[tuple],
    coin_rows: list[tuple],
) -> None:
    """Fire/recover alerts and email users. Called from /snapshot AFTER the
    snapshot transaction commits — Resend HTTP calls happen here and must
    not be allowed to roll back snapshot data on timeout. Each DB write
    acquires its own short-lived connection.

    Failure isolation: a Resend hiccup for one user does not affect others
    and does not mute their alerts (next tick retries). The whole function
    swallows nothing else — uncaught exceptions propagate to the caller.
    """
    bar_mins = _index_bar_mins(bar_rows)
    coin_mins = _index_coin_mins(coin_rows)
    if not bar_mins and not coin_mins:
        return

    async with pool.acquire() as conn:
        enabled_alerts = await conn.fetch("SELECT * FROM alerts WHERE enabled = TRUE")
    if not enabled_alerts:
        return

    per_user_fires: dict[UUID, list[tuple[asyncpg.Record, dict]]] = {}
    to_unmute_ids: list[UUID] = []

    for alert in enabled_alerts:
        if alert["kind"] == "bar":
            hit = bar_mins.get(alert["size_g"])
        else:
            key = (
                alert["coin_type"],
                alert["fine_gold_g"].quantize(FINE_GOLD_PRECISION),
            )
            hit = coin_mins.get(key)
        if hit is None:
            continue
        threshold = alert["threshold_pct"]
        below = hit["premium"] <= threshold
        muted = alert["muted_until_recovery"]
        if below and not muted:
            per_user_fires.setdefault(alert["user_id"], []).append((alert, hit))
        elif not below and muted and hit["premium"] > threshold + HYSTERESIS_PCT:
            to_unmute_ids.append(alert["id"])

    if to_unmute_ids:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE alerts SET muted_until_recovery = FALSE WHERE id = ANY($1::uuid[])",
                to_unmute_ids,
            )

    for user_id, fires in per_user_fires.items():
        async with pool.acquire() as conn:
            # Per-user rate limit: count of alerts fired in the last hour. A
            # bundled email of N alerts counts as N. Stops a flapping watch
            # from carpet-bombing the inbox.
            recent_fires = await conn.fetchval(
                """
                SELECT COUNT(*) FROM alerts
                WHERE user_id = $1 AND last_fired_at >= NOW() - INTERVAL '1 hour'
                """,
                user_id,
            )
            if recent_fires and recent_fires >= MAX_FIRES_PER_HOUR_PER_USER:
                logger.warning(
                    "alert_email_throttled %s",
                    json.dumps({
                        "event": "alert_email_throttled",
                        "user_id": str(user_id),
                        "recent_hour_fires": int(recent_fires),
                        "cap": MAX_FIRES_PER_HOUR_PER_USER,
                        "fetched_at": fetched_at.isoformat(),
                    }),
                )
                continue

            email_row = await conn.fetchrow(
                "SELECT email FROM users WHERE id = $1", user_id,
            )
        if email_row is None:
            continue  # user deleted between snapshot and evaluation

        formatted = [_format_fire(alert, hit) for alert, hit in fires]
        # send_alert_email wraps the synchronous Resend SDK in asyncio.to_thread
        # so a slow upstream blocks only the helper task, not the event loop.
        # No DB connection is held while the HTTP call is in flight.
        try:
            await send_alert_email(to_email=email_row["email"], fires=formatted)
        except EmailSendError as e:
            logger.warning(
                "alert_email_failed %s",
                json.dumps({
                    "event": "alert_email_failed",
                    "user_id": str(user_id),
                    "alert_ids": [str(a["id"]) for a, _ in fires],
                    "error": str(e),
                    "fetched_at": fetched_at.isoformat(),
                }),
            )
            continue  # leave alerts un-muted so next tick retries

        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE alerts SET muted_until_recovery = TRUE, last_fired_at = NOW(), "
                "  fire_count = fire_count + 1 "
                "WHERE id = ANY($1::uuid[])",
                [a["id"] for a, _ in fires],
            )
        logger.info(
            "alert_email_sent %s",
            json.dumps({
                "event": "alert_email_sent",
                "user_id": str(user_id),
                "alert_count": len(fires),
                "fetched_at": fetched_at.isoformat(),
            }),
        )
