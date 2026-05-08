from pathlib import Path

import pytest

from app.scrapers.plaza_coins import PlazaCoinsScraper

FIXTURE = Path(__file__).parents[2] / "fixtures" / "plaza_coins.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_plaza_coins_either_recognized_or_empty(html: str) -> None:
    """Plaza only stocks Valcambi bars; no coin inventory expected.

    Acceptable: empty list (current state) OR at least one recognized
    coin (if Plaza ever adds coins later, the scraper picks them up
    automatically without re-coding).
    """
    results = PlazaCoinsScraper().parse(html)
    if results:
        assert all(r.coin_type is not None for r in results)
        assert all((r.fine_gold_g or 0) <= 20 for r in results)


def test_plaza_coins_skips_unrecognized(html: str) -> None:
    results = PlazaCoinsScraper().parse(html)
    assert all(r.coin_type is not None for r in results)
