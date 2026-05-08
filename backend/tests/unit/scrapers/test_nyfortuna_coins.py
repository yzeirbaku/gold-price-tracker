from pathlib import Path

import pytest

from app.scrapers.nyfortuna_coins import NyfortunaCoinsScraper

FIXTURE = Path(__file__).parents[2] / "fixtures" / "nyfortuna_coins.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_nyfortuna_coins_returns_recognized_listings(html: str) -> None:
    results = NyfortunaCoinsScraper().parse(html)
    types = {r.coin_type for r in results}
    assert types & {"Krugerrand", "Maple Leaf", "American Eagle"}
    assert all((r.fine_gold_g or 0) <= 20 for r in results)


def test_nyfortuna_coins_skips_unrecognized(html: str) -> None:
    results = NyfortunaCoinsScraper().parse(html)
    assert all(r.coin_type is not None for r in results)
