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


def test_vitusguld_coins_keeps_gram_pandas() -> None:
    """Vitus spells grams "15 gr." — whole-token size matching once dropped
    the 3/8/15 g Pandas from this listing."""
    from pathlib import Path

    from app.scrapers.vitusguld_coins import VitusGuldCoinsScraper

    fixture = Path(__file__).parents[2] / "fixtures" / "vitusguld_coins.html"
    html = fixture.read_text(encoding="utf-8")
    sizes = {r.size_label for r in VitusGuldCoinsScraper().parse(html) if r.coin_type == "Panda"}
    assert {"3 g", "8 g", "15 g"} <= sizes
