"""Unit tests for the alerts module — pure logic, no DB / no FastAPI client.

Covers index helpers (_index_bar_mins / _index_coin_mins), the _format_fire
adapter, and the _validate_kind_payload guardrail. Evaluation flow itself
is integration-shaped (needs a DB pool + asyncpg fetch behavior) and is
exercised live; the pure pieces it depends on are unit-tested here.

Email template rendering is also covered against a plain-text snapshot.
"""
from datetime import UTC, datetime
from decimal import Decimal

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
from app.email import _alert_html_body, _alert_subject, _alert_text_body

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
