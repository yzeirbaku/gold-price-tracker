"""End-to-end tests for /portfolio/*: CRUD + cross-user isolation.

Historical spot is stubbed in conftest.mock_external so writes don't depend
on yfinance / frankfurter.
"""
from uuid import uuid4

import pytest

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
