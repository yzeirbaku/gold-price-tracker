from pathlib import Path

from app.scrapers.tavex import TavexScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "tavex_5g.html"

# Values read from the saved fixture (2026-05-07):
# First 5g product: 5 gram Valcambi Suisse Guldbarre, price 5366,32 DKK (1+ qty)
EXPECTED_PRICE_DKK = 5366.32
EXPECTED_URL_FRAGMENT = "5g-investeringsguldbarre-valcambi"


def test_tavex_parses_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = TavexScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_PRICE_DKK
    assert listing.in_stock is True
    assert listing.url is not None and EXPECTED_URL_FRAGMENT in str(listing.url)


def test_tavex_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = TavexScraper().parse(html, size_g=1234.0)
    assert listing is None
