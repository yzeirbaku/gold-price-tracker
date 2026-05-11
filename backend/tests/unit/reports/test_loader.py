from datetime import UTC, datetime

from app.reports.loader import (
    BarPoint,
    CoinPoint,
    SpotPoint,
    rows_to_bars,
    rows_to_coins,
    rows_to_spot,
)


def test_rows_to_bars_filters_status_and_casts_decimals() -> None:
    ts = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
    rows = [
        {"fetched_at": ts, "dealer": "Tavex", "size_g": 5.0,
         "status": "ok", "price_dkk": "5345.50", "spot_gold_dkk_per_g": "964.20"},
        {"fetched_at": ts, "dealer": "Vitus Guld", "size_g": 5.0,
         "status": "error", "price_dkk": None, "spot_gold_dkk_per_g": None},
    ]
    bars = rows_to_bars(rows)
    assert len(bars) == 2
    assert isinstance(bars[0], BarPoint)
    assert bars[0].price_dkk == 5345.50
    assert bars[0].spot_dkk_per_g == 964.20
    assert bars[1].price_dkk is None
    assert bars[1].status == "error"


def test_rows_to_coins_extracts_all_fields() -> None:
    ts = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
    rows = [{
        "fetched_at": ts, "dealer": "Tavex",
        "coin_type": "Krugerrand", "size_label": "1/4 oz",
        "gross_weight_g": "8.48", "purity": "0.9167", "fine_gold_g": "7.7736",
        "status": "ok", "price_dkk": "5210.00", "spot_gold_dkk_per_g": "964.20",
    }]
    coins = rows_to_coins(rows)
    assert len(coins) == 1
    c = coins[0]
    assert isinstance(c, CoinPoint)
    assert c.coin_type == "Krugerrand"
    assert c.size_label == "1/4 oz"
    assert c.fine_gold_g == 7.7736


def test_rows_to_spot_decimal_cast() -> None:
    ts = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
    rows = [{
        "fetched_at": ts,
        "gold_dkk_per_g": "964.20",
        "silver_dkk_per_g": "12.83",
    }]
    spots = rows_to_spot(rows)
    assert len(spots) == 1
    assert isinstance(spots[0], SpotPoint)
    assert spots[0].gold_dkk_per_g == 964.20
    assert spots[0].silver_dkk_per_g == 12.83
