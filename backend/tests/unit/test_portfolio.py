"""Tests for portfolio decoration + summary math. Route-level CRUD tests
are integration-only (require DB) and out of scope for CI."""
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.portfolio import (
    _ALLOWED_UPDATE_COLS,
    _decorate,
    _fetch_historical_spot_dkk_per_g,
    _summary,
)


def _row(
    *,
    metal: str = "gold",
    gross_weight_g: str = "10.0000",
    purity: str = "0.99990",
    price_paid_dkk: str = "8000.00",
    spot_at_purchase: str | None = "750.0000",
    purchased_at: datetime | None = None,
    label: str = "10g bar",
    dealer: str | None = "Tavex",
    notes: str | None = None,
) -> dict:
    return {
        "id": UUID("00000000-0000-0000-0000-000000000001"),
        "user_id": UUID("00000000-0000-0000-0000-000000000099"),
        "metal": metal,
        "gross_weight_g": Decimal(gross_weight_g),
        "purity": Decimal(purity),
        "price_paid_dkk": Decimal(price_paid_dkk),
        "purchased_at": purchased_at or datetime(2026, 1, 15, tzinfo=UTC),
        "label": label,
        "dealer": dealer,
        "notes": notes,
        "spot_at_purchase_dkk_per_g": Decimal(spot_at_purchase) if spot_at_purchase else None,
    }


def _spot(gold: str = "800.0000", silver: str = "10.0000") -> dict:
    return {"gold": Decimal(gold), "silver": Decimal(silver)}


def test_decorate_computes_fine_weight_from_gross_times_purity() -> None:
    out = _decorate(_row(gross_weight_g="10", purity="0.9999"), _spot())
    assert out["fine_weight_g"] == pytest.approx(9.999, abs=1e-4)


def test_decorate_current_value_uses_current_spot() -> None:
    out = _decorate(_row(gross_weight_g="10", purity="1.0"), _spot(gold="800"))
    assert out["current_value_dkk"] == pytest.approx(8000.00, abs=1e-2)


def test_decorate_pnl_positive_when_value_exceeds_paid() -> None:
    out = _decorate(
        _row(price_paid_dkk="7500", gross_weight_g="10", purity="1.0"),
        _spot(gold="800"),
    )
    assert out["pnl_dkk"] == pytest.approx(500.00, abs=1e-2)
    assert out["pnl_pct"] > 0


def test_decorate_pnl_negative_when_value_below_paid() -> None:
    out = _decorate(
        _row(price_paid_dkk="9000", gross_weight_g="10", purity="1.0"),
        _spot(gold="800"),
    )
    assert out["pnl_dkk"] == pytest.approx(-1000.00, abs=1e-2)
    assert out["pnl_pct"] < 0


def test_decorate_purchase_premium_uses_frozen_historical_spot() -> None:
    # Paid 8000 for 10g fine at spot=750 → cost basis 7500 → premium = (8000-7500)/7500 = 6.67%
    out = _decorate(
        _row(price_paid_dkk="8000", gross_weight_g="10", purity="1.0", spot_at_purchase="750"),
        _spot(gold="800"),
    )
    assert out["purchase_premium_pct"] == pytest.approx(6.67, abs=1e-2)


def test_decorate_purchase_premium_none_when_no_historical_spot() -> None:
    out = _decorate(_row(spot_at_purchase=None), _spot())
    assert out["purchase_premium_pct"] is None
    assert out["spot_at_purchase_dkk_per_g"] is None


def test_decorate_handles_zero_paid_without_division_error() -> None:
    out = _decorate(_row(price_paid_dkk="0"), _spot())
    assert out["pnl_pct"] == 0.0


def test_decorate_silver_uses_silver_spot() -> None:
    out = _decorate(
        _row(metal="silver", gross_weight_g="100", purity="0.999"),
        _spot(silver="10"),
    )
    assert out["current_value_dkk"] == pytest.approx(999.00, abs=1e-2)


def test_summary_aggregates_total_paid_and_value() -> None:
    spot = _spot(gold="800")
    decorated = [
        _decorate(_row(price_paid_dkk="5000", gross_weight_g="5", purity="1.0"), spot),
        _decorate(_row(price_paid_dkk="9000", gross_weight_g="10", purity="1.0"), spot),
    ]
    summary = _summary(decorated)
    assert summary["total_paid_dkk"] == pytest.approx(14000.00)
    assert summary["total_value_dkk"] == pytest.approx(12000.00)  # 5×800 + 10×800 = 12000
    assert summary["total_pnl_dkk"] == pytest.approx(-2000.00)


def test_summary_breaks_down_by_metal() -> None:
    decorated = [
        _decorate(
            _row(metal="gold", price_paid_dkk="5000", gross_weight_g="5", purity="1.0"),
            _spot(gold="800", silver="10"),
        ),
        _decorate(
            _row(metal="silver", price_paid_dkk="1500", gross_weight_g="150", purity="1.0"),
            _spot(gold="800", silver="10"),
        ),
    ]
    summary = _summary(decorated)
    assert summary["by_metal"]["gold"]["paid_dkk"] == pytest.approx(5000.00)
    assert summary["by_metal"]["gold"]["value_dkk"] == pytest.approx(4000.00)  # 5g × 800
    assert summary["by_metal"]["silver"]["paid_dkk"] == pytest.approx(1500.00)
    assert summary["by_metal"]["silver"]["value_dkk"] == pytest.approx(1500.00)  # 150g × 10


def test_summary_total_pnl_pct_handles_zero_paid() -> None:
    summary = _summary([])
    assert summary["total_pnl_pct"] == 0.0
    assert summary["total_paid_dkk"] == 0.0


@pytest.mark.asyncio
async def test_fetch_historical_spot_dkk_per_g_multiplies_usd_by_fx() -> None:
    """USD/g × USD→DKK rate, quantised to 4dp."""
    with (
        patch("app.portfolio.fetch_historical_usd_per_gram", AsyncMock(return_value=100.0)),
        patch("app.portfolio.fetch_usd_to_dkk_on", AsyncMock(return_value=6.85)),
    ):
        result = await _fetch_historical_spot_dkk_per_g(
            "gold", datetime(2026, 1, 15, 12, tzinfo=UTC),
        )
    assert result == Decimal("685.0000")


@pytest.mark.asyncio
async def test_fetch_historical_spot_dkk_per_g_uses_utc_date_of_purchase() -> None:
    """Whatever timezone the `purchased_at` is in, the historical lookup
    must use its UTC calendar date — yfinance and Frankfurter both work in
    UTC daily closes."""
    captured: dict = {}

    async def capture_spot(metal: str, on_date) -> float:
        captured["spot_date"] = on_date
        return 100.0

    async def capture_fx(client, on_date) -> float:
        captured["fx_date"] = on_date
        return 6.85

    with (
        patch("app.portfolio.fetch_historical_usd_per_gram", side_effect=capture_spot),
        patch("app.portfolio.fetch_usd_to_dkk_on", side_effect=capture_fx),
    ):
        # 23:30 Copenhagen on 2026-01-15 → already 2026-01-15 22:30 UTC.
        # The lookup should target 2026-01-15.
        purchased_at = datetime.fromisoformat("2026-01-15T22:30:00+00:00")
        await _fetch_historical_spot_dkk_per_g("gold", purchased_at)
    assert captured["spot_date"].isoformat() == "2026-01-15"
    assert captured["fx_date"].isoformat() == "2026-01-15"


@pytest.mark.asyncio
async def test_fetch_historical_spot_dkk_per_g_surfaces_unavailable_as_http_502() -> None:
    from fastapi import HTTPException

    from app.spot import HistoricalSpotUnavailable

    async def boom(metal: str, on_date) -> float:
        raise HistoricalSpotUnavailable("no data")

    with patch("app.portfolio.fetch_historical_usd_per_gram", side_effect=boom):
        with pytest.raises(HTTPException) as exc:
            await _fetch_historical_spot_dkk_per_g(
                "gold", datetime(2026, 1, 15, tzinfo=UTC),
            )
    assert exc.value.status_code == 502


def test_allowed_update_cols_lists_all_pydantic_fields() -> None:
    """If PurchaseUpdate gains a new field, this allowlist must grow with
    it — otherwise PATCH on the new field will 400. Catches the drift."""
    from app.portfolio import PurchaseUpdate

    pydantic_fields = set(PurchaseUpdate.model_fields.keys())
    missing = pydantic_fields - _ALLOWED_UPDATE_COLS
    assert not missing, f"PurchaseUpdate fields not in _ALLOWED_UPDATE_COLS: {missing}"
