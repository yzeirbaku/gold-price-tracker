"""End-to-end tests for /portfolio/*: CRUD + cross-user isolation.

Historical spot is stubbed in conftest.mock_external so writes don't depend
on yfinance / frankfurter.
"""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from tests.api.conftest import auth_headers, make_session, sample_purchase_payload

pytestmark = pytest.mark.asyncio


async def test_list_requires_auth(client):
    r = await client.get("/portfolio")
    assert r.status_code == 401


async def test_empty_portfolio_returns_zero_summary(client, pool):
    _, token = await make_session(pool)
    r = await client.get("/portfolio", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert body["purchases"] == []
    assert body["summary"]["total_paid_dkk"] == 0


async def test_create_purchase_decorates_pnl(client, pool):
    _, token = await make_session(pool)
    r = await client.post(
        "/portfolio", json=sample_purchase_payload(), headers=auth_headers(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["metal"] == "gold"
    assert body["fine_weight_g"] == pytest.approx(31.0969, abs=1e-4)
    assert body["spot_at_purchase_dkk_per_g"] == 600.0  # stubbed historical
    assert body["current_spot_dkk_per_g"] == 650.0      # stubbed live
    # paid=20000, fine=31.10 × purity ≈ 31.0969 g
    # current_value = 650 × 31.0969 ≈ 20213, pnl ≈ +213
    assert body["current_value_dkk"] == pytest.approx(20213.0, abs=1.0)
    assert body["pnl_dkk"] == pytest.approx(213.0, abs=1.0)


async def test_create_purchase_invalid_payload(client, pool):
    _, token = await make_session(pool)
    r = await client.post(
        "/portfolio",
        json=sample_purchase_payload(purity="1.5"),  # > 1 violates Field constraint
        headers=auth_headers(token),
    )
    assert r.status_code == 422


async def test_list_returns_own_purchases_only(client, pool):
    """The cross-user isolation that matters most: A's purchases must never
    appear in B's GET /portfolio, even if A's session token leaked."""
    _, token_a = await make_session(pool, email="a@example.com")
    _, token_b = await make_session(pool, email="b@example.com")
    await client.post(
        "/portfolio", json=sample_purchase_payload(label="A's bar"),
        headers=auth_headers(token_a),
    )
    r = await client.get("/portfolio", headers=auth_headers(token_b))
    assert r.json()["purchases"] == []


async def test_patch_updates_fields(client, pool):
    _, token = await make_session(pool)
    created = (await client.post(
        "/portfolio", json=sample_purchase_payload(), headers=auth_headers(token),
    )).json()
    pid = created["id"]
    r = await client.patch(
        f"/portfolio/{pid}",
        json={"label": "Renamed bar", "notes": "added later"},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["label"] == "Renamed bar"
    assert body["notes"] == "added later"


async def test_patch_metal_change_refreezes_spot(client, pool):
    """Per CLAUDE.md: changing `metal` re-fetches the historical spot since
    the same date implies a different price for gold vs silver."""
    _, token = await make_session(pool)
    created = (await client.post(
        "/portfolio", json=sample_purchase_payload(metal="gold"),
        headers=auth_headers(token),
    )).json()
    assert created["spot_at_purchase_dkk_per_g"] == 600.0
    r = await client.patch(
        f"/portfolio/{created['id']}",
        json={"metal": "silver", "purity": "0.999"},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    assert r.json()["spot_at_purchase_dkk_per_g"] == 8.0  # silver stub


async def test_patch_other_users_row_returns_404(client, pool):
    _, token_a = await make_session(pool, email="a@example.com")
    _, token_b = await make_session(pool, email="b@example.com")
    a_row = (await client.post(
        "/portfolio", json=sample_purchase_payload(),
        headers=auth_headers(token_a),
    )).json()
    r = await client.patch(
        f"/portfolio/{a_row['id']}", json={"label": "hijacked"},
        headers=auth_headers(token_b),
    )
    assert r.status_code == 404


async def test_patch_empty_body_returns_400(client, pool):
    _, token = await make_session(pool)
    created = (await client.post(
        "/portfolio", json=sample_purchase_payload(), headers=auth_headers(token),
    )).json()
    r = await client.patch(
        f"/portfolio/{created['id']}", json={}, headers=auth_headers(token),
    )
    assert r.status_code == 400


async def test_delete_removes_row(client, pool):
    _, token = await make_session(pool)
    created = (await client.post(
        "/portfolio", json=sample_purchase_payload(), headers=auth_headers(token),
    )).json()
    r = await client.delete(f"/portfolio/{created['id']}", headers=auth_headers(token))
    assert r.status_code == 204
    r2 = await client.get("/portfolio", headers=auth_headers(token))
    assert r2.json()["purchases"] == []


async def test_delete_other_users_row_returns_404(client, pool):
    _, token_a = await make_session(pool, email="a@example.com")
    _, token_b = await make_session(pool, email="b@example.com")
    a_row = (await client.post(
        "/portfolio", json=sample_purchase_payload(),
        headers=auth_headers(token_a),
    )).json()
    r = await client.delete(f"/portfolio/{a_row['id']}", headers=auth_headers(token_b))
    assert r.status_code == 404


async def test_delete_unknown_id_returns_404(client, pool):
    _, token = await make_session(pool)
    r = await client.delete(f"/portfolio/{uuid4()}", headers=auth_headers(token))
    assert r.status_code == 404


async def test_create_clamps_a_future_noon_stamp_into_the_history_window(client, pool):
    """A purchase added before noon UTC must show up on the value chart.

    The frontend stamps date-only input as T12:00:00Z, which is ahead of the
    server clock for anyone submitting before noon UTC. _reconstruct_value_series
    advances on `purchased_at <= t`, so an un-clamped stamp read as "not yet
    held" and the chart reported 0 while the summary card reported the real
    value.
    """
    _, token = await make_session(pool)
    today = datetime.now(UTC).date()
    r = await client.post(
        "/portfolio",
        json={
            "metal": "gold", "gross_weight_g": "10", "purity": "1",
            "price_paid_dkk": "8000", "purchased_at": f"{today.isoformat()}T12:00:00Z",
            "label": "Noon stamp",
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 201, r.text
    stored = datetime.fromisoformat(r.json()["purchased_at"])
    assert stored <= datetime.now(UTC), "purchased_at must never be in the future"
    # The UTC date is preserved — that is what drives the spot lookup.
    assert stored.astimezone(UTC).date() == today


async def test_patch_clamps_a_future_noon_stamp(client, pool):
    _, token = await make_session(pool)
    created = (await client.post(
        "/portfolio",
        json={
            "metal": "gold", "gross_weight_g": "10", "purity": "1",
            "price_paid_dkk": "8000", "purchased_at": "2026-01-15T12:00:00Z",
            "label": "Bar",
        },
        headers=auth_headers(token),
    )).json()
    today = datetime.now(UTC).date()
    r = await client.patch(
        f"/portfolio/{created['id']}",
        json={"purchased_at": f"{today.isoformat()}T12:00:00Z"},
        headers=auth_headers(token),
    )
    assert r.status_code == 200, r.text
    assert datetime.fromisoformat(r.json()["purchased_at"]) <= datetime.now(UTC)


async def test_create_accepts_a_naive_iso_timestamp(client, pool):
    """An offset-less ISO string is ordinary input. It used to escape the
    validator as a TypeError (naive vs aware compare) and surface as a 500
    rather than being read as UTC."""
    _, token = await make_session(pool)
    r = await client.post(
        "/portfolio",
        json={
            "metal": "gold", "gross_weight_g": "10", "purity": "1",
            "price_paid_dkk": "8000", "purchased_at": "2026-01-15T12:00:00",
            "label": "Naive",
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()["purchased_at"]).tzinfo is not None


@pytest.mark.parametrize(
    "payload,expect",
    [
        ({"purchased_at": None}, 400),
        ({"metal": None}, 400),
        ({"label": None}, 400),
        ({"gross_weight_g": None}, 400),
        ({"dealer": None}, 200),   # genuinely nullable — clearing is allowed
        ({"notes": None}, 200),
    ],
    ids=["purchased_at", "metal", "label", "gross_weight", "dealer", "notes"],
)
async def test_patch_null_handling(client, pool, payload, expect):
    """Explicit nulls on NOT NULL columns must 400, not 500 — while the two
    genuinely nullable columns stay clearable."""
    _, token = await make_session(pool)
    created = (await client.post(
        "/portfolio",
        json={
            "metal": "gold", "gross_weight_g": "10", "purity": "1",
            "price_paid_dkk": "8000", "purchased_at": "2026-01-15T12:00:00Z",
            "label": "Bar", "dealer": "Tavex", "notes": "n",
        },
        headers=auth_headers(token),
    )).json()

    r = await client.patch(
        f"/portfolio/{created['id']}", json=payload, headers=auth_headers(token),
    )
    assert r.status_code == expect, r.text


async def test_history_1y_decimates_in_sql_and_keeps_both_ends(client, pool):
    """A year of 20-min snapshots is ~26k rows. Decimation now happens in SQL
    (one row per time bucket) so 1Y / ALL stop shipping and reconstructing
    every row. What must survive the bucketing: the first point is the true
    period start (_period_change opens from it), the newest DB row is still
    there when live spot fails, and the point count stays capped."""
    _, token = await make_session(pool)
    created = (await client.post(
        "/portfolio",
        json={
            "metal": "gold", "gross_weight_g": "10", "purity": "1",
            "price_paid_dkk": "6000", "purchased_at": "2025-01-01T12:00:00Z",
            "label": "Old bar",
        },
        headers=auth_headers(token),
    ))
    assert created.status_code == 201, created.text
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO spot_snapshots (fetched_at, gold_dkk_per_g, silver_dkk_per_g) "
            "SELECT t, 600 + extract(epoch FROM t - NOW()) / 1e6, 8 "
            "FROM generate_series(NOW() - interval '400 days', NOW() - interval '5 minutes', "
            "                     interval '20 minutes') AS t",
        )
        newest = await conn.fetchval("SELECT MAX(fetched_at) FROM spot_snapshots")

    r = await client.get("/portfolio/history?range=1y", headers=auth_headers(token))
    assert r.status_code == 200, r.text
    points = r.json()["points"]
    assert 400 <= len(points) <= 500
    first = datetime.fromisoformat(points[0]["t"])
    window_start = datetime.now(UTC) - timedelta(days=365)
    assert timedelta(0) <= first - window_start < timedelta(minutes=21)
    # Synthetic "now" point from the stubbed live spot (650 × 10 g).
    assert points[-1]["value_dkk"] == 6500.0

    from app import portfolio as portfolio_module

    async def _down() -> None:
        raise HTTPException(status_code=502, detail="down")

    original = portfolio_module._current_spot_dkk_per_g
    portfolio_module._current_spot_dkk_per_g = _down
    try:
        r = await client.get("/portfolio/history?range=1y", headers=auth_headers(token))
    finally:
        portfolio_module._current_spot_dkk_per_g = original
    assert r.status_code == 200, r.text
    assert datetime.fromisoformat(r.json()["points"][-1]["t"]) == newest
