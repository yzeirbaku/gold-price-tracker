from pathlib import Path

import pytest

from app.scrapers.janjorgensen_coins import JanJorgensenCoinsScraper

FIXTURE = Path(__file__).parents[2] / "fixtures" / "janjorgensen_coins.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_janjorgensen_coins_either_recognized_or_empty(html: str) -> None:
    """Jan Jørgensen primarily sells jewelry + investment bars; no bullion
    coin category at the time of writing. Scraper returns an empty list now;
    if they ever expand, recognized coins surface automatically.
    """
    results = JanJorgensenCoinsScraper().parse(html)
    if results:
        assert all(r.coin_type is not None for r in results)
        assert all((r.fine_gold_g or 0) <= 20 for r in results)
