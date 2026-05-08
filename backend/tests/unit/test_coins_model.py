from datetime import UTC, datetime

from app.models import CoinListing


def test_coin_listing_minimal_ok() -> None:
    li = CoinListing(
        dealer="Tavex",
        status="ok",
        coin_type="Krugerrand",
        size_label="1/4 oz",
        gross_weight_g=8.48,
        purity=0.9167,
        fine_gold_g=7.77,
        price_dkk=4825.0,
        url="https://tavex.dk/example",
        fetched_at=datetime.now(UTC),
    )
    assert li.dealer == "Tavex"
    assert li.status == "ok"
    assert li.fine_gold_g == 7.77


def test_coin_listing_error_status_no_price() -> None:
    li = CoinListing(
        dealer="Vitus Guld",
        status="error",
        error="http: TimeoutException",
        fetched_at=datetime.now(UTC),
    )
    assert li.price_dkk is None
    assert li.coin_type is None
