from pathlib import Path

import pytest

from app.scrapers.vitusguld_coins import VitusGuldCoinsScraper

FIXTURE = Path(__file__).parents[2] / "fixtures" / "vitusguld_coins.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_vitusguld_coins_returns_recognized_listings(html: str) -> None:
    results = VitusGuldCoinsScraper().parse(html)
    types = {r.coin_type for r in results}
    assert types & {
        "Krugerrand", "Maple Leaf", "Vienna Philharmonic",
        "American Eagle", "Britannia", "Ducat",
    }
    assert all((r.fine_gold_g or 0) <= 20 for r in results)
    for r in results:
        if r.status == "ok":
            assert r.price_dkk and r.price_dkk > 0


def test_vitusguld_coins_skips_unrecognized(html: str) -> None:
    results = VitusGuldCoinsScraper().parse(html)
    assert all(r.coin_type is not None for r in results)
