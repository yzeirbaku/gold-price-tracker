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
    _downsample,
    _fetch_historical_spot_dkk_per_g,
    _period_change,
    _reconstruct_value_series,
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


# ── /portfolio/history helpers ──────────────────────────────────────────────


def _purchase(
    *,
    metal: str = "gold",
    gross: str = "10",
    purity: str = "1.0",
    price: str = "8000",
    at: datetime,
) -> dict:
    return {
        "metal": metal,
        "gross_weight_g": Decimal(gross),
        "purity": Decimal(purity),
        "price_paid_dkk": Decimal(price),
        "purchased_at": at,
    }


def _spot_row(
    *, at: datetime, gold: str | None = "800", silver: str | None = "10",
) -> dict:
    return {
        "fetched_at": at,
        "gold_dkk_per_g": Decimal(gold) if gold is not None else None,
        "silver_dkk_per_g": Decimal(silver) if silver is not None else None,
    }


def test_reconstruct_value_series_basic_single_purchase() -> None:
    """One 10g gold purchase, three spot ticks — value = 10 × spot at each."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    purchases = [_purchase(gross="10", purity="1.0", at=t0)]
    spots = [
        _spot_row(at=t0, gold="800"),
        _spot_row(at=t0.replace(hour=1), gold="810"),
        _spot_row(at=t0.replace(hour=2), gold="820"),
    ]
    points = _reconstruct_value_series(purchases, spots)
    assert [p["value_dkk"] for p in points] == [8000.0, 8100.0, 8200.0]


def test_reconstruct_value_series_purchase_before_first_spot_counts() -> None:
    """A purchase made before any spot row in the range is included from
    the first point. This is the 'user with 6 months of history clicks 1W'
    case — old holdings still need to contribute."""
    t_old = datetime(2025, 1, 1, tzinfo=UTC)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    purchases = [_purchase(gross="5", purity="1.0", at=t_old)]
    spots = [_spot_row(at=t0, gold="800")]
    points = _reconstruct_value_series(purchases, spots)
    assert points[0]["value_dkk"] == 4000.0  # 5g × 800


def test_reconstruct_value_series_purchase_mid_range_jumps_value() -> None:
    """A purchase made between two spot rows contributes from the next tick
    onward, producing a visible step in the value line."""
    t0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    t_buy = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    t1 = datetime(2026, 1, 1, 0, 20, tzinfo=UTC)
    purchases = [
        _purchase(gross="10", purity="1.0", at=datetime(2025, 12, 1, tzinfo=UTC)),
        _purchase(gross="5", purity="1.0", at=t_buy),  # +5g mid-range
    ]
    spots = [_spot_row(at=t0, gold="800"), _spot_row(at=t1, gold="800")]
    points = _reconstruct_value_series(purchases, spots)
    assert points[0]["value_dkk"] == 8000.0   # 10g × 800
    assert points[1]["value_dkk"] == 12000.0  # 15g × 800


def test_reconstruct_value_series_silver_uses_silver_spot() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    purchases = [_purchase(metal="silver", gross="100", purity="1.0", at=t0)]
    spots = [_spot_row(at=t0, gold="800", silver="10")]
    points = _reconstruct_value_series(purchases, spots)
    assert points[0]["value_dkk"] == 1000.0  # 100g × 10


def test_reconstruct_value_series_mixed_metals_sum_correctly() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    purchases = [
        _purchase(metal="gold", gross="10", purity="1.0", at=t0),
        _purchase(metal="silver", gross="100", purity="1.0", at=t0),
    ]
    spots = [_spot_row(at=t0, gold="800", silver="10")]
    points = _reconstruct_value_series(purchases, spots)
    assert points[0]["value_dkk"] == 9000.0  # 10×800 + 100×10


def test_reconstruct_value_series_empty_purchases_returns_zero_line() -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    points = _reconstruct_value_series([], [_spot_row(at=t0)])
    assert points == [{"t": t0, "value_dkk": 0.0}]


def test_reconstruct_value_series_handles_null_spot_gracefully() -> None:
    """An old spot_snapshots row with NULL gold (rare but possible if the
    upstream spot fetch failed during persistence) must not crash. That
    metal simply contributes 0 to value for that tick."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    purchases = [_purchase(gross="10", purity="1.0", at=t0)]
    spots = [_spot_row(at=t0, gold=None, silver="10")]
    points = _reconstruct_value_series(purchases, spots)
    assert points[0]["value_dkk"] == 0.0


def test_downsample_noop_when_under_max() -> None:
    points = [{"t": i, "value_dkk": float(i)} for i in range(50)]
    assert _downsample(points, max_points=500) == points


def test_downsample_exact_length_and_endpoints_preserved() -> None:
    points = [{"t": i, "value_dkk": float(i)} for i in range(10_000)]
    out = _downsample(points, max_points=500)
    assert len(out) == 500
    assert out[0] == points[0]
    assert out[-1] == points[-1]


def test_downsample_preserves_monotonic_order() -> None:
    """Decimation must not reorder — chart relies on t-ascending input."""
    points = [{"t": i, "value_dkk": float(i)} for i in range(2_000)]
    out = _downsample(points, max_points=100)
    ts = [p["t"] for p in out]
    assert ts == sorted(ts)


def test_period_change_simple_no_new_purchases() -> None:
    """100k → 110k with no purchases mid-period: +10k / +10%."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 2, 1, tzinfo=UTC)
    points = [
        {"t": t0, "value_dkk": 100_000.0},
        {"t": t1, "value_dkk": 110_000.0},
    ]
    purchases = [_purchase(at=datetime(2025, 1, 1, tzinfo=UTC), price="50000")]
    c = _period_change(points, purchases)
    assert c["period_change_dkk"] == 10_000.0
    assert c["period_change_pct"] == 10.0
    assert c["net_purchases_in_period_dkk"] == 0.0


def test_period_change_subtracts_purchases_made_during_period() -> None:
    """100k start, user buys a 20k bar mid-period, market doesn't move →
    end value is 120k. Naive change would be +20k (+20%); deposit-adjusted
    must report ~0 because all of the gain is the user's own money."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t_buy = datetime(2026, 1, 15, tzinfo=UTC)
    t1 = datetime(2026, 2, 1, tzinfo=UTC)
    points = [
        {"t": t0, "value_dkk": 100_000.0},
        {"t": t1, "value_dkk": 120_000.0},
    ]
    purchases = [
        _purchase(at=datetime(2025, 1, 1, tzinfo=UTC), price="50000"),
        _purchase(at=t_buy, price="20000"),
    ]
    c = _period_change(points, purchases)
    assert c["net_purchases_in_period_dkk"] == 20_000.0
    assert c["period_change_dkk"] == 0.0
    assert c["period_change_pct"] == 0.0


def test_period_change_purchase_at_exact_period_start_not_double_counted() -> None:
    """A purchase made AT the first spot row's timestamp is already in
    period_start_value. Counting it again as a net purchase would
    double-subtract and produce a misleading negative change."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 2, 1, tzinfo=UTC)
    points = [
        {"t": t0, "value_dkk": 50_000.0},
        {"t": t1, "value_dkk": 55_000.0},
    ]
    purchases = [_purchase(at=t0, price="50000")]  # at exact period_start
    c = _period_change(points, purchases)
    assert c["net_purchases_in_period_dkk"] == 0.0
    assert c["period_change_dkk"] == 5_000.0


def test_period_change_empty_points_returns_zeros() -> None:
    c = _period_change([], [])
    assert c == {
        "period_start_value_dkk": 0.0,
        "current_value_dkk": 0.0,
        "net_purchases_in_period_dkk": 0.0,
        "period_change_dkk": 0.0,
        "period_change_pct": 0.0,
    }


def test_period_change_pct_zero_when_denominator_is_zero() -> None:
    """No prior holdings, no in-period purchases: avoid ZeroDivisionError."""
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 2, 1, tzinfo=UTC)
    points = [{"t": t0, "value_dkk": 0.0}, {"t": t1, "value_dkk": 0.0}]
    c = _period_change(points, [])
    assert c["period_change_pct"] == 0.0


def test_allowed_update_cols_lists_all_pydantic_fields() -> None:
    """If PurchaseUpdate gains a new field, this allowlist must grow with
    it — otherwise PATCH on the new field will 400. Catches the drift."""
    from app.portfolio import PurchaseUpdate

    pydantic_fields = set(PurchaseUpdate.model_fields.keys())
    missing = pydantic_fields - _ALLOWED_UPDATE_COLS
    assert not missing, f"PurchaseUpdate fields not in _ALLOWED_UPDATE_COLS: {missing}"
