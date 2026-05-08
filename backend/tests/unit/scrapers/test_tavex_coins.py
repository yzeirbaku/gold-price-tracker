from pathlib import Path

import pytest

from app.scrapers.tavex_coins import TavexCoinsScraper

FIXTURE = Path(__file__).parents[2] / "fixtures" / "tavex_coins.html"


@pytest.fixture
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_tavex_coins_returns_recognized_listings(html: str) -> None:
    scraper = TavexCoinsScraper()
    results = scraper.parse(html)
    types = {r.coin_type for r in results}
    # Spot-check: the fixture is known to contain at least these.
    assert types & {"Krugerrand", "Maple Leaf", "Britannia", "Panda"}
    # Cap enforced.
    assert all((r.fine_gold_g or 0) <= 20 for r in results)
    # ok rows have prices and URLs.
    for r in results:
        if r.status == "ok":
            assert r.price_dkk is not None and r.price_dkk > 0
            assert r.url is not None


def test_tavex_coins_skips_unrecognized_titles(html: str) -> None:
    """Coins not in the registry (Lunar, Kangaroo, Buffalo, etc.) are dropped."""
    scraper = TavexCoinsScraper()
    results = scraper.parse(html)
    assert all(r.coin_type is not None for r in results)
