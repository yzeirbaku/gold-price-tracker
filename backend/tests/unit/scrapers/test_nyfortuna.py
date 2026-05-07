from pathlib import Path

from app.scrapers.nyfortuna import NyfortunaScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "nyfortuna_listing.html"

# First bar in fixture matching "10 gram guldbarre": "PAMP – 10 gram guldbarre stanset"
# Price: kr. 11.982,40 → 11982.4
EXPECTED_10G_PRICE_DKK: float = 11982.4
EXPECTED_10G_URL_FRAGMENT: str = "pamp-10-gram-guldbarre-stanset"


def test_nyfortuna_parses_10g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = NyfortunaScraper().parse(html, size_g=10.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_10G_PRICE_DKK
    assert EXPECTED_10G_URL_FRAGMENT in str(listing.url)


def test_nyfortuna_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = NyfortunaScraper().parse(html, size_g=1234.0)
    assert listing is None
