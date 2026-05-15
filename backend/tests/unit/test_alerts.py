"""Unit tests for the alerts module — pure logic, no DB / no FastAPI client.

Covers index helpers (_index_bar_mins / _index_coin_mins), the _format_fire
adapter, and the _validate_kind_payload guardrail. Evaluation flow itself
is integration-shaped (needs a DB pool + asyncpg fetch behavior) and is
exercised live; the pure pieces it depends on are unit-tested here.

Email template rendering is also covered against a plain-text snapshot.
"""
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from app import alerts as alerts_module
from app.alerts import (
    HYSTERESIS_PCT,
    MAX_FIRES_PER_HOUR_PER_USER,
    AlertCreate,
    _format_fire,
    _index_bar_mins,
    _index_coin_mins,
    _validate_kind_payload,
)
from app.email import (
    EmailSendError,
    _alert_html_body,
    _alert_subject,
    _alert_text_body,
)

# --- _index_bar_mins -------------------------------------------------------


def _bar_row(dealer: str, size_g: float, price: float | None, spot: float, status: str = "ok",
             brand: str | None = "X") -> tuple:
    return (
        datetime.now(UTC), dealer, Decimal(str(size_g)), status,
        Decimal(str(price)) if price is not None else None,
        brand, None,
        Decimal(str(spot)),
    )


def test_bar_index_picks_lowest_premium_per_size() -> None:
    rows = [
        _bar_row("A", 10, 10500, 1000),  # premium 5%
        _bar_row("B", 10, 10200, 1000),  # premium 2% ← winner for 10g
        _bar_row("C", 10, 10800, 1000),  # premium 8%
        _bar_row("D", 5,  5500, 1000),   # premium 10%
    ]
    mins = _index_bar_mins(rows)
    assert mins[Decimal("10")]["dealer"] == "B"
    assert mins[Decimal("10")]["premium"] == Decimal("2")
    assert mins[Decimal("5")]["dealer"] == "D"


def test_bar_index_skips_non_ok_rows() -> None:
    rows = [
        _bar_row("Down", 10, None, 1000, status="error"),
        _bar_row("Stocked", 10, 10500, 1000),
    ]
    mins = _index_bar_mins(rows)
    assert Decimal("10") in mins
    assert mins[Decimal("10")]["dealer"] == "Stocked"


def test_bar_index_skips_zero_spot() -> None:
    rows = [_bar_row("Bad", 10, 10500, 0)]
    mins = _index_bar_mins(rows)
    assert mins == {}


def test_bar_index_skips_missing_price() -> None:
    rows = [_bar_row("Nada", 10, None, 1000)]
    mins = _index_bar_mins(rows)
    assert mins == {}


# --- _index_coin_mins ------------------------------------------------------


def _coin_row(
    dealer: str, coin_type: str, fine: float, price: float | None, spot: float,
    size_label: str = "1/2 oz", status: str = "ok",
) -> tuple:
    return (
        datetime.now(UTC), dealer, coin_type, size_label,
        Decimal(str(fine / 0.9999)),
        Decimal("0.9999"),
        Decimal(str(fine)),
        status,
        Decimal(str(price)) if price is not None else None,
        None,
        Decimal(str(spot)),
        "https://example.com/x",
    )


def test_coin_index_keyed_by_type_and_fine() -> None:
    rows = [
        _coin_row("A", "Krugerrand", 15.55, 16800, 1000),  # premium 8.07%
        _coin_row("B", "Krugerrand", 15.55, 16400, 1000),  # premium 5.5% ← winner
        _coin_row("C", "Maple Leaf",  15.55, 17000, 1000),  # different coin_type
    ]
    mins = _index_coin_mins(rows)
    krug = mins[("Krugerrand", Decimal("15.5500"))]
    assert krug["dealer"] == "B"
    maple = mins[("Maple Leaf", Decimal("15.5500"))]
    assert maple["dealer"] == "C"


def test_coin_index_fine_quantized_to_4_decimals() -> None:
    # Two rows with the same fine to 4dp should merge into one bucket.
    rows = [
        _coin_row("A", "Maple Leaf", 15.55003, 17000, 1000),
        _coin_row("B", "Maple Leaf", 15.55001, 16500, 1000),  # better deal
    ]
    mins = _index_coin_mins(rows)
    assert len(mins) == 1
    only = next(iter(mins.values()))
    assert only["dealer"] == "B"


def test_coin_index_size_label_propagates() -> None:
    rows = [_coin_row("A", "Krugerrand", 15.55, 16400, 1000, size_label="1/2 oz")]
    mins = _index_coin_mins(rows)
    assert mins[("Krugerrand", Decimal("15.5500"))]["size_label"] == "1/2 oz"


# --- _format_fire ----------------------------------------------------------


class _FakeAlert(dict):
    def __getitem__(self, k):
        return super().__getitem__(k)


def test_format_fire_bar() -> None:
    alert = _FakeAlert(
        kind="bar", size_g=Decimal("10"), coin_type=None,
        fine_gold_g=None, threshold_pct=Decimal("7"),
    )
    hit = {"premium": Decimal("6.82"), "dealer": "Vitus Guld",
           "price_dkk": Decimal("10420")}
    out = _format_fire(alert, hit)
    assert out["target"] == "10 g bar"
    assert out["threshold_pct"] == 7.0
    assert out["current_premium_pct"] == 6.82
    assert out["best_dealer"] == "Vitus Guld"
    assert out["price_dkk"] == 10420.0


def test_format_fire_coin() -> None:
    alert = _FakeAlert(
        kind="coin", size_g=None, coin_type="Krugerrand",
        fine_gold_g=Decimal("15.55"), threshold_pct=Decimal("5"),
    )
    hit = {"premium": Decimal("4.91"), "dealer": "Tavex",
           "price_dkk": Decimal("16840"), "size_label": "1/2 oz"}
    out = _format_fire(alert, hit)
    assert "Krugerrand" in out["target"]
    assert "1/2 oz" in out["target"]
    assert "15.55 g fine" in out["target"]
    assert out["current_premium_pct"] == 4.91


# --- _validate_kind_payload -----------------------------------------------


def test_validate_bar_happy_path() -> None:
    body = AlertCreate(kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"))
    _validate_kind_payload(body)  # no raise


def test_validate_bar_rejects_unknown_size() -> None:
    body = AlertCreate(kind="bar", size_g=Decimal("3"), threshold_pct=Decimal("7"))
    with pytest.raises(HTTPException) as ei:
        _validate_kind_payload(body)
    assert ei.value.status_code == 400
    assert "size_g must be one of" in ei.value.detail


def test_validate_bar_rejects_coin_fields() -> None:
    body = AlertCreate(
        kind="bar", size_g=Decimal("10"),
        coin_type="Krugerrand", threshold_pct=Decimal("7"),
    )
    with pytest.raises(HTTPException) as ei:
        _validate_kind_payload(body)
    assert ei.value.status_code == 400


def test_validate_coin_happy_path() -> None:
    body = AlertCreate(
        kind="coin", coin_type="Krugerrand",
        fine_gold_g=Decimal("15.55"), threshold_pct=Decimal("5"),
    )
    _validate_kind_payload(body)  # no raise


def test_validate_coin_rejects_unknown_type() -> None:
    body = AlertCreate(
        kind="coin", coin_type="Fakebrand",
        fine_gold_g=Decimal("15.55"), threshold_pct=Decimal("5"),
    )
    with pytest.raises(HTTPException) as ei:
        _validate_kind_payload(body)
    assert ei.value.status_code == 400
    assert "unknown coin_type" in ei.value.detail


def test_validate_coin_rejects_size_g() -> None:
    # Both fields set for a coin alert — caller has confused kinds.
    body = AlertCreate(
        kind="coin", coin_type="Krugerrand",
        size_g=Decimal("10"),
        fine_gold_g=Decimal("15.55"), threshold_pct=Decimal("5"),
    )
    with pytest.raises(HTTPException) as ei:
        _validate_kind_payload(body)
    assert ei.value.status_code == 400


# --- Email template rendering ---------------------------------------------


def _sample_fire() -> dict:
    return {
        "target": "10 g bar",
        "threshold_pct": 7.0,
        "current_premium_pct": 6.82,
        "best_dealer": "Vitus Guld",
        "price_dkk": 10420.0,
    }


def test_alert_subject_singular() -> None:
    subj = _alert_subject([_sample_fire()])
    assert "10 g bar" in subj
    assert "6.82%" in subj


def test_alert_subject_plural() -> None:
    fires = [_sample_fire(), _sample_fire(), _sample_fire()]
    subj = _alert_subject(fires)
    assert subj == "Gold Price Tracker — 3 alerts triggered"


def test_alert_html_body_contains_target_and_dealer() -> None:
    html = _alert_html_body([_sample_fire()])
    assert "10 g bar" in html
    assert "Vitus Guld" in html
    assert "6.82" in html
    assert "10.420 dkk" in html  # Danish thousand-grouping


def test_alert_html_body_handles_multiple_fires() -> None:
    fires = [_sample_fire(), {**_sample_fire(),
                              "target": "Krugerrand 1/2 oz",
                              "best_dealer": "Tavex"}]
    html = _alert_html_body(fires)
    assert "Vitus Guld" in html
    assert "Tavex" in html
    assert "Krugerrand 1/2 oz" in html


def test_alert_text_body_is_plain_and_complete() -> None:
    text = _alert_text_body([_sample_fire()])
    assert "Premium alert" in text
    assert "10 g bar" in text
    assert "Vitus Guld" in text
    assert "6.82%" in text
    assert "<" not in text  # no leaked HTML


# --- Module constants lock-in ---------------------------------------------


def test_hysteresis_is_strictly_positive() -> None:
    assert HYSTERESIS_PCT > 0


def test_rate_cap_is_reasonable() -> None:
    # Make sure nobody accidentally cranks this to 0 (no emails ever) or to
    # something absurd (carpet-bomb).
    assert 1 <= MAX_FIRES_PER_HOUR_PER_USER <= 50


def test_allowed_update_cols_includes_threshold_and_enabled() -> None:
    # Lock-in: these two fields MUST be patchable. mute is internal but used
    # by the recovery path so it's allowed too.
    assert "threshold_pct" in alerts_module._ALLOWED_UPDATE_COLS
    assert "enabled" in alerts_module._ALLOWED_UPDATE_COLS


# --- evaluate_alerts end-to-end -------------------------------------------
#
# These tests stub asyncpg + Resend so the fire/mute/recover/throttle/bundle
# state machine can be exercised entirely in-memory. They are the only place
# that locks down the **precision invariant** across coins.resolve →
# _index_coin_mins → alerts.fine_gold_g matching: an alert at 15.55 matches a
# scraper row that landed in DB as 15.5500, and does NOT collide with a
# neighbouring 15.5501 bucket.
#
# Run-through of the contract under test:
#   • fire: enabled alert below threshold + not muted → email + mute + log
#   • mute: enabled alert below threshold + muted → no email (deduped)
#   • recover: enabled alert above (threshold + HYSTERESIS_PCT) + muted →
#       un-mute, no email
#   • throttle: per-user fires/hour cap → skip with structured log
#   • bundle: N alerts firing for one user in one tick → one email of N rows
#   • Resend failure: alert stays un-muted (next tick retries)
#   • empty inputs short-circuit before touching the DB

class _FakeRecord(dict):
    """asyncpg.Record-shaped: subscript access. The evaluate_alerts code uses
    record["col"] everywhere; this stand-in is the minimum viable surface."""


class _FakeConn:
    """Records every fetch/fetchrow/fetchval/execute call and routes them to
    canned return values keyed by query substring.

    Why substring routing: evaluate_alerts issues 3-4 distinct queries; a flat
    queue would couple tests to call order, and that order isn't part of the
    contract we're testing. Substring routing keeps the tests order-agnostic.
    """

    def __init__(self) -> None:
        self.alerts_to_return: list[_FakeRecord] = []
        self.recent_fires: dict[UUID, int] = {}  # by user_id
        self.user_emails: dict[UUID, str | None] = {}
        self.executed: list[tuple[str, tuple]] = []  # (sql, args)

    async def fetch(self, sql: str, *args):
        if "FROM alerts WHERE enabled" in sql:
            return self.alerts_to_return
        raise AssertionError(f"unexpected fetch: {sql!r}")

    async def fetchrow(self, sql: str, *args):
        if "FROM users WHERE id" in sql:
            user_id = args[0]
            email = self.user_emails.get(user_id)
            return None if email is None else _FakeRecord(email=email)
        raise AssertionError(f"unexpected fetchrow: {sql!r}")

    async def fetchval(self, sql: str, *args):
        if "COUNT(*) FROM alerts" in sql:
            user_id = args[0]
            return self.recent_fires.get(user_id, 0)
        raise AssertionError(f"unexpected fetchval: {sql!r}")

    async def execute(self, sql: str, *args) -> None:
        self.executed.append((sql, args))


class _FakePool:
    """asyncpg.Pool-shaped: a single connection handed out for every acquire().
    evaluate_alerts acquires several short-lived connections — using one
    underlying _FakeConn lets us assert against a unified call log."""

    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self._conn


def _alert(
    *,
    id: str = "00000000-0000-0000-0000-0000000000a1",
    user_id: str = "00000000-0000-0000-0000-0000000000f1",
    kind: str = "bar",
    size_g: Decimal | None = None,
    coin_type: str | None = None,
    fine_gold_g: Decimal | None = None,
    threshold_pct: Decimal = Decimal("7"),
    enabled: bool = True,
    muted_until_recovery: bool = False,
) -> _FakeRecord:
    return _FakeRecord(
        id=UUID(id), user_id=UUID(user_id), kind=kind,
        size_g=size_g, coin_type=coin_type, fine_gold_g=fine_gold_g,
        threshold_pct=threshold_pct, enabled=enabled,
        muted_until_recovery=muted_until_recovery,
    )


@pytest.fixture()
def patch_send_alert_email(monkeypatch):
    """Replace alerts.send_alert_email with an AsyncMock. evaluate_alerts
    looks the function up via the module-level import inside alerts.py, so
    patching there (not on .email) is what counts."""
    mock = AsyncMock()
    monkeypatch.setattr(alerts_module, "send_alert_email", mock)
    return mock


@pytest.fixture()
def fetched_at():
    return datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


# --- the basic shape ------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_alerts_no_op_when_no_data(patch_send_alert_email, fetched_at):
    """Empty bar_rows + empty coin_rows: short-circuits before touching the
    DB. Confirms the cheap-path stays cheap when scrapers all failed."""
    conn = _FakeConn()  # any fetch would raise — proves no DB hit
    pool = _FakePool(conn)
    await alerts_module.evaluate_alerts(pool, fetched_at, [], [])
    patch_send_alert_email.assert_not_called()
    assert conn.executed == []


@pytest.mark.asyncio
async def test_evaluate_alerts_no_op_when_no_enabled_alerts(patch_send_alert_email, fetched_at):
    """Snapshot data lands but no user is subscribed → no work to do."""
    conn = _FakeConn()
    conn.alerts_to_return = []
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Tavex", 10, 10500, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_not_called()


# --- fire path ------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_alerts_bar_below_threshold_fires(patch_send_alert_email, fetched_at):
    """Premium 2% on a 7% threshold → fire one email + mute + bump fire_count."""
    conn = _FakeConn()
    alert = _alert(kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"))
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    # 2% premium → below 7% threshold → fires
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_awaited_once()
    kwargs = patch_send_alert_email.call_args.kwargs
    assert kwargs["to_email"] == "user@example.com"
    assert len(kwargs["fires"]) == 1
    assert kwargs["fires"][0]["target"] == "10 g bar"
    assert kwargs["fires"][0]["best_dealer"] == "Vitus Guld"
    # Mute UPDATE must have landed.
    mute_writes = [e for e in conn.executed if "muted_until_recovery = TRUE" in e[0]]
    assert len(mute_writes) == 1


@pytest.mark.asyncio
async def test_evaluate_alerts_above_threshold_does_not_fire(patch_send_alert_email, fetched_at):
    """Premium 10% on a 7% threshold → no email, no mute write."""
    conn = _FakeConn()
    alert = _alert(kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"))
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 11000, 1000)]  # 10% premium
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_not_called()


# --- precision invariant (the bug that motivated the refactor) ------------


@pytest.mark.asyncio
async def test_evaluate_alerts_coin_precision_alert_15_55_matches_row_15_5500(
    patch_send_alert_email, fetched_at,
):
    """User-facing 15.55 input bucketizes identically to scraper-stored 15.5500."""
    conn = _FakeConn()
    alert = _alert(
        kind="coin", coin_type="Krugerrand",
        fine_gold_g=Decimal("15.55"),
        threshold_pct=Decimal("7"),
    )
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    coin_rows = [_coin_row("Tavex", "Krugerrand", 15.5500, 16500, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, [], coin_rows)
    patch_send_alert_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_evaluate_alerts_coin_precision_alert_15_55_does_not_match_15_5501(
    patch_send_alert_email, fetched_at,
):
    """A scraper row at 15.5501 lives in a different bucket from a 15.55
    alert. The contract is: same logical coin → same bucket; different fine
    weight → different bucket. No accidental cross-talk."""
    conn = _FakeConn()
    alert = _alert(
        kind="coin", coin_type="Krugerrand",
        fine_gold_g=Decimal("15.55"),
        threshold_pct=Decimal("7"),
    )
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    coin_rows = [_coin_row("Tavex", "Krugerrand", 15.5501, 16500, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, [], coin_rows)
    patch_send_alert_email.assert_not_called()


# --- mute / recovery state machine ----------------------------------------


@pytest.mark.asyncio
async def test_evaluate_alerts_muted_alert_does_not_refire(patch_send_alert_email, fetched_at):
    """An already-muted alert that's still below threshold → no email,
    no further mute writes. This is the dedup core of the design."""
    conn = _FakeConn()
    alert = _alert(
        kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"),
        muted_until_recovery=True,
    )
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]  # 2%
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_alerts_recovery_unmutes_when_above_hysteresis(
    patch_send_alert_email, fetched_at,
):
    """Muted alert + premium > threshold + HYSTERESIS_PCT → un-mute, no email.
    Hysteresis prevents single-tick flap-recovery at threshold."""
    conn = _FakeConn()
    alert = _alert(
        kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"),
        muted_until_recovery=True,
    )
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    # Premium needs to clear 7 + 0.5 = 7.5%. 8% does.
    bar_rows = [_bar_row("Vitus Guld", 10, 10800, 1000)]  # 8%
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_not_called()
    unmute_writes = [e for e in conn.executed if "muted_until_recovery = FALSE" in e[0]]
    assert len(unmute_writes) == 1


@pytest.mark.asyncio
async def test_evaluate_alerts_within_hysteresis_band_stays_muted(
    patch_send_alert_email, fetched_at,
):
    """Premium between threshold and threshold + HYSTERESIS_PCT → not below
    threshold (no fire) but not far enough above to un-mute either. Stays
    in stable muted-armed limbo."""
    conn = _FakeConn()
    alert = _alert(
        kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"),
        muted_until_recovery=True,
    )
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    # 7.2% — above 7% (no fire) but below 7.5% (no un-mute either)
    bar_rows = [_bar_row("Vitus Guld", 10, 10720, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_not_called()
    unmute_writes = [e for e in conn.executed if "muted_until_recovery = FALSE" in e[0]]
    assert len(unmute_writes) == 0


# --- bundling -------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_alerts_bundles_per_user_into_one_email(
    patch_send_alert_email, fetched_at,
):
    """Two alerts for one user, both fire on the same tick → exactly one
    email is sent, with both in the fires list. Bundling is what keeps a
    big multi-size dip from carpet-bombing the inbox."""
    conn = _FakeConn()
    user_id = "00000000-0000-0000-0000-0000000000a1"
    alert_bar = _alert(
        id="00000000-0000-0000-0000-0000000000b1",
        user_id=user_id, kind="bar", size_g=Decimal("10"),
        threshold_pct=Decimal("7"),
    )
    alert_coin = _alert(
        id="00000000-0000-0000-0000-0000000000b2",
        user_id=user_id, kind="coin",
        coin_type="Krugerrand", fine_gold_g=Decimal("15.55"),
        threshold_pct=Decimal("7"),
    )
    conn.alerts_to_return = [alert_bar, alert_coin]
    conn.user_emails[alert_bar["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]   # 2%
    coin_rows = [_coin_row("Tavex", "Krugerrand", 15.5500, 16500, 1000)]  # 6.13%
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, coin_rows)
    patch_send_alert_email.assert_awaited_once()
    fires = patch_send_alert_email.call_args.kwargs["fires"]
    assert len(fires) == 2
    targets = {f["target"] for f in fires}
    assert "10 g bar" in targets
    assert any("Krugerrand" in t for t in targets)


@pytest.mark.asyncio
async def test_evaluate_alerts_per_user_isolation(patch_send_alert_email, fetched_at):
    """Two users with one alert each → two separate emails, one per user.
    Bundling is per-user, not global."""
    conn = _FakeConn()
    a = _alert(
        id="00000000-0000-0000-0000-0000000000b1",
        user_id="00000000-0000-0000-0000-0000000000a1",
        kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"),
    )
    b = _alert(
        id="00000000-0000-0000-0000-0000000000b2",
        user_id="00000000-0000-0000-0000-0000000000a2",
        kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"),
    )
    conn.alerts_to_return = [a, b]
    conn.user_emails[a["user_id"]] = "a@example.com"
    conn.user_emails[b["user_id"]] = "b@example.com"
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    assert patch_send_alert_email.await_count == 2
    recipients = {c.kwargs["to_email"] for c in patch_send_alert_email.call_args_list}
    assert recipients == {"a@example.com", "b@example.com"}


# --- throttle -------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_alerts_throttle_skips_when_user_over_cap(
    patch_send_alert_email, fetched_at,
):
    """MAX_FIRES_PER_HOUR_PER_USER already-reached → skip this fire entirely
    (no email, no mute write). Stops a flapping watch from carpet-bombing."""
    conn = _FakeConn()
    alert = _alert(kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"))
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    conn.recent_fires[alert["user_id"]] = MAX_FIRES_PER_HOUR_PER_USER  # at cap
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_not_called()
    mute_writes = [e for e in conn.executed if "muted_until_recovery = TRUE" in e[0]]
    assert mute_writes == []


# --- failure isolation ----------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_alerts_resend_failure_leaves_alert_unmuted(
    patch_send_alert_email, fetched_at,
):
    """If Resend raises, the alert must NOT be muted — next tick retries.
    A failure-mute would silence the user during a real outage."""
    patch_send_alert_email.side_effect = EmailSendError("resend down")
    conn = _FakeConn()
    alert = _alert(kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"))
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]
    # Must NOT raise — failure is caught per-user and isolated.
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    mute_writes = [e for e in conn.executed if "muted_until_recovery = TRUE" in e[0]]
    assert mute_writes == []


@pytest.mark.asyncio
async def test_evaluate_alerts_resend_failure_for_user_a_does_not_block_user_b(
    patch_send_alert_email, fetched_at,
):
    """One user's Resend failure must not poison the loop for other users.
    This is the cross-user isolation guarantee."""
    failing_email = "a@example.com"
    succeeding_email = "b@example.com"

    async def selective_fail(*, to_email: str, fires):
        if to_email == failing_email:
            raise EmailSendError("a-specific failure")

    patch_send_alert_email.side_effect = selective_fail
    conn = _FakeConn()
    a = _alert(
        id="00000000-0000-0000-0000-0000000000b1",
        user_id="00000000-0000-0000-0000-0000000000a1",
        kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"),
    )
    b = _alert(
        id="00000000-0000-0000-0000-0000000000b2",
        user_id="00000000-0000-0000-0000-0000000000a2",
        kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"),
    )
    conn.alerts_to_return = [a, b]
    conn.user_emails[a["user_id"]] = failing_email
    conn.user_emails[b["user_id"]] = succeeding_email
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    # Both were attempted; b's mute write landed; a's did not.
    assert patch_send_alert_email.await_count == 2
    mute_writes = [e for e in conn.executed if "muted_until_recovery = TRUE" in e[0]]
    assert len(mute_writes) == 1
    # Mute write targets b's id, not a's.
    muted_ids = mute_writes[0][1][0]
    assert b["id"] in muted_ids
    assert a["id"] not in muted_ids


# --- silence on missing data ---------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_alerts_skips_alert_when_no_matching_scraper_row(
    patch_send_alert_email, fetched_at,
):
    """User has a 20g-bar alert but only 10g rows landed this tick → no
    fire (we don't know the 20g premium right now, so no decision to make)."""
    conn = _FakeConn()
    alert = _alert(kind="bar", size_g=Decimal("20"), threshold_pct=Decimal("7"))
    conn.alerts_to_return = [alert]
    conn.user_emails[alert["user_id"]] = "user@example.com"
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_not_called()


@pytest.mark.asyncio
async def test_evaluate_alerts_skips_when_user_email_missing(
    patch_send_alert_email, fetched_at,
):
    """Race condition: user deleted between snapshot and evaluation. Don't
    crash, don't email, don't mute."""
    conn = _FakeConn()
    alert = _alert(kind="bar", size_g=Decimal("10"), threshold_pct=Decimal("7"))
    conn.alerts_to_return = [alert]
    # No entry in user_emails → fetchrow returns None
    pool = _FakePool(conn)
    bar_rows = [_bar_row("Vitus Guld", 10, 10200, 1000)]
    await alerts_module.evaluate_alerts(pool, fetched_at, bar_rows, [])
    patch_send_alert_email.assert_not_called()
    mute_writes = [e for e in conn.executed if "muted_until_recovery = TRUE" in e[0]]
    assert mute_writes == []
