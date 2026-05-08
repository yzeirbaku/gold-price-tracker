from pathlib import Path

import pytest

from app.scrapers.seroguld_coins import SeroGuldCoinsScraper

FIXTURE = Path(__file__).parents[2] / "fixtures" / "seroguld_coins.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_seroguld_coins_returns_recognized_listings(html: str) -> None:
    results = SeroGuldCoinsScraper().parse(html)
    types = {r.coin_type for r in results}
    assert types & {"Krugerrand", "Vienna Philharmonic", "Maple Leaf"}
    assert all((r.fine_gold_g or 0) <= 20 for r in results)


def test_seroguld_coins_skips_unrecognized(html: str) -> None:
    results = SeroGuldCoinsScraper().parse(html)
    assert all(r.coin_type is not None for r in results)
