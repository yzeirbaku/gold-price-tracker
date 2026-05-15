"""End-to-end tests for /alerts/*: CRUD, preview, options, validation.

Exercises the CHECK constraint shape via the API (good 400 messages), the
cross-user 404 isolation, and the preview/options helpers that the dialog
relies on.
"""
from uuid import uuid4

import pytest

from tests.api.conftest import (
    auth_headers,
    insert_bar_snapshot,
    insert_coin_snapshot,
    make_session,
)

pytestmark = pytest.mark.asyncio


# --- options ---------------------------------------------------------------


async def test_options_lists_bar_sizes_and_coins(client, pool):
    _, token = await make_session(pool)
    r = await client.get("/alerts/options", headers=auth_headers(token))
    assert r.status_code == 200
    body = r.json()
    assert body["bar_sizes"] == [2.5, 5.0, 10.0, 20.0]
    coin_types = {c["coin_type"] for c in body["coin_options"]}
    assert "Krugerrand" in coin_types
    assert "Maple Leaf" in coin_types


async def test_options_dedupes_danish_kroner_by_fine_weight(client, pool):
    """Danish 20 kr has 3 monarchs at the same physical spec — they should
    collapse to ONE entry under coin_options so the dialog doesn't show
    three identical lines."""
    _, token = await make_session(pool)
    body = (await client.get("/alerts/options", headers=auth_headers(token))).json()
    dk20 = next(c for c in body["coin_options"] if c["coin_type"] == "Danish 20 kr")
    assert len(dk20["sizes"]) == 1


# --- preview ---------------------------------------------------------------


async def test_preview_bar_returns_min_premium_across_dealers(client, pool):
    _, token = await make_session(pool)
    # Two dealers selling 10g; Vitus is cheaper.
    await insert_bar_snapshot(
        pool, dealer="Tavex", size_g=10.0, price_dkk=6500.0, spot_gold_dkk_per_g=600.0,
    )
    await insert_bar_snapshot(
        pool, dealer="Vitus Guld", size_g=10.0, price_dkk=6400.0,
        spot_gold_dkk_per_g=600.0,
    )
    r = await client.get(
        "/alerts/preview?kind=bar&size_g=10", headers=auth_headers(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current_best_dealer"] == "Vitus Guld"
    # (6400 - 6000) / 6000 * 100 = 6.6667 → rounded to 6.67
    assert body["current_min_premium_pct"] == pytest.approx(6.67, abs=0.01)


async def test_preview_returns_none_when_no_recent_data(client, pool):
    _, token = await make_session(pool)
    r = await client.get(
        "/alerts/preview?kind=bar&size_g=10", headers=auth_headers(token),
    )
    assert r.json() == {"current_min_premium_pct": None, "current_best_dealer": None}


async def test_preview_bar_missing_size_returns_400(client, pool):
    _, token = await make_session(pool)
    r = await client.get("/alerts/preview?kind=bar", headers=auth_headers(token))
    assert r.status_code == 400


async def test_preview_coin_returns_min_premium(client, pool):
    _, token = await make_session(pool)
    await insert_coin_snapshot(
        pool, dealer="Tavex", coin_type="Krugerrand", size_label="1/2 oz",
        gross_weight_g=16.96, purity=0.9167, fine_gold_g=15.55,
        price_dkk=10500.0, spot_gold_dkk_per_g=600.0,
    )
    r = await client.get(
        "/alerts/preview?kind=coin&coin_type=Krugerrand&fine_gold_g=15.55",
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["current_best_dealer"] == "Tavex"
    assert body["current_min_premium_pct"] is not None


# --- create ----------------------------------------------------------------


async def test_create_bar_alert(client, pool):
    _, token = await make_session(pool)
    r = await client.post(
        "/alerts",
        json={"kind": "bar", "size_g": "10", "threshold_pct": "5"},
        headers=auth_headers(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "bar"
    assert body["size_g"] == 10.0
    assert body["threshold_pct"] == 5.0
    assert body["enabled"] is True
    assert body["muted_until_recovery"] is False
    # New alerts start at zero fires; the UI uses this in the expanded
    # detail row ("Times triggered").
    assert body["fire_count"] == 0


async def test_create_coin_alert(client, pool):
    _, token = await make_session(pool)
    r = await client.post(
        "/alerts",
        json={
            "kind": "coin", "coin_type": "Krugerrand",
            "fine_gold_g": "15.55", "threshold_pct": "6.5",
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 201
    assert r.json()["coin_type"] == "Krugerrand"


async def test_create_bar_with_coin_fields_rejected(client, pool):
    """The shape validator should return a clean 400 before asyncpg's CHECK
    constraint fires a 5xx."""
    _, token = await make_session(pool)
    r = await client.post(
        "/alerts",
        json={
            "kind": "bar", "size_g": "10",
            "coin_type": "Krugerrand",  # not allowed for bar
            "threshold_pct": "5",
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 400


async def test_create_bar_with_unsupported_size_rejected(client, pool):
    _, token = await make_session(pool)
    r = await client.post(
        "/alerts",
        json={"kind": "bar", "size_g": "3", "threshold_pct": "5"},
        headers=auth_headers(token),
    )
    assert r.status_code == 400


async def test_create_coin_with_unknown_type_rejected(client, pool):
    _, token = await make_session(pool)
    r = await client.post(
        "/alerts",
        json={
            "kind": "coin", "coin_type": "MadeUpCoin",
            "fine_gold_g": "31.1", "threshold_pct": "5",
        },
        headers=auth_headers(token),
    )
    assert r.status_code == 400


# --- list ------------------------------------------------------------------


async def test_list_returns_own_alerts_only(client, pool):
    _, token_a = await make_session(pool, email="a@example.com")
    _, token_b = await make_session(pool, email="b@example.com")
    await client.post(
        "/alerts", json={"kind": "bar", "size_g": "10", "threshold_pct": "5"},
        headers=auth_headers(token_a),
    )
    r = await client.get("/alerts", headers=auth_headers(token_b))
    assert r.json()["alerts"] == []


async def test_list_enriches_with_current_min(client, pool):
    _, token = await make_session(pool)
    await insert_bar_snapshot(
        pool, dealer="Tavex", size_g=10.0, price_dkk=6500.0, spot_gold_dkk_per_g=600.0,
    )
    await client.post(
        "/alerts", json={"kind": "bar", "size_g": "10", "threshold_pct": "5"},
        headers=auth_headers(token),
    )
    body = (await client.get("/alerts", headers=auth_headers(token))).json()
    alert = body["alerts"][0]
    assert alert["current_best_dealer"] == "Tavex"
    assert alert["current_min_premium_pct"] is not None


# --- update ----------------------------------------------------------------


async def test_patch_threshold_resets_muted_state(client, pool):
    _, token = await make_session(pool)
    created = (await client.post(
        "/alerts", json={"kind": "bar", "size_g": "10", "threshold_pct": "5"},
        headers=auth_headers(token),
    )).json()
    # Simulate that the alert previously fired and got muted.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE alerts SET muted_until_recovery = TRUE WHERE id = $1::uuid",
            created["id"],
        )
    r = await client.patch(
        f"/alerts/{created['id']}", json={"threshold_pct": "4"},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    assert r.json()["muted_until_recovery"] is False


async def test_patch_enabled_toggle(client, pool):
    _, token = await make_session(pool)
    created = (await client.post(
        "/alerts", json={"kind": "bar", "size_g": "10", "threshold_pct": "5"},
        headers=auth_headers(token),
    )).json()
    r = await client.patch(
        f"/alerts/{created['id']}", json={"enabled": False},
        headers=auth_headers(token),
    )
    assert r.status_code == 200
    assert r.json()["enabled"] is False


async def test_patch_other_users_alert_returns_404(client, pool):
    _, token_a = await make_session(pool, email="a@example.com")
    _, token_b = await make_session(pool, email="b@example.com")
    a = (await client.post(
        "/alerts", json={"kind": "bar", "size_g": "10", "threshold_pct": "5"},
        headers=auth_headers(token_a),
    )).json()
    r = await client.patch(
        f"/alerts/{a['id']}", json={"threshold_pct": "3"},
        headers=auth_headers(token_b),
    )
    assert r.status_code == 404


# --- delete ----------------------------------------------------------------


async def test_delete_alert(client, pool):
    _, token = await make_session(pool)
    a = (await client.post(
        "/alerts", json={"kind": "bar", "size_g": "10", "threshold_pct": "5"},
        headers=auth_headers(token),
    )).json()
    r = await client.delete(f"/alerts/{a['id']}", headers=auth_headers(token))
    assert r.status_code == 204
    assert (await client.get("/alerts", headers=auth_headers(token))).json()["alerts"] == []


async def test_delete_other_users_alert_returns_404(client, pool):
    _, token_a = await make_session(pool, email="a@example.com")
    _, token_b = await make_session(pool, email="b@example.com")
    a = (await client.post(
        "/alerts", json={"kind": "bar", "size_g": "10", "threshold_pct": "5"},
        headers=auth_headers(token_a),
    )).json()
    r = await client.delete(f"/alerts/{a['id']}", headers=auth_headers(token_b))
    assert r.status_code == 404


async def test_delete_unknown_id_returns_404(client, pool):
    _, token = await make_session(pool)
    r = await client.delete(f"/alerts/{uuid4()}", headers=auth_headers(token))
    assert r.status_code == 404
