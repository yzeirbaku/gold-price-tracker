from pathlib import Path

import pytest

from app.scrapers.nordiskguld_coins import NordiskGuldCoinsScraper

FIXTURE = Path(__file__).parents[2] / "fixtures" / "nordiskguld_coins.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_nordiskguld_coins_returns_recognized_listings(html: str) -> None:
    results = NordiskGuldCoinsScraper().parse(html)
    types = {r.coin_type for r in results}
    assert types & {"Britannia", "Maple Leaf", "Ducat"}
    assert all((r.fine_gold_g or 0) <= 20 for r in results)


def test_nordiskguld_coins_skips_unrecognized(html: str) -> None:
    results = NordiskGuldCoinsScraper().parse(html)
    assert all(r.coin_type is not None for r in results)
