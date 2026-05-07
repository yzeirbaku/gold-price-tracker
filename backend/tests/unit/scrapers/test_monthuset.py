from pathlib import Path

from app.scrapers.monthuset import MonthusetScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "monthuset_listing.html"

# Fixture: Wayback Machine snapshot of monthuset.dk/guld/guldbarrer (Sep 2024).
# Three in-stock products:
#   1. "Kongeskibet Dannebrog 5 gram"  – 7 500 kr  → 7500.0 DKK
#   2. "Kongeskibet Dannebrog 2,5 gram" – 4 000 kr  → 4000.0 DKK
#   3. "Tour de France 2024 2,5 gram guldbarre" – 4 500 kr  → 4500.0 DKK

EXPECTED_5G_PRICE_DKK: float = 7500.0
EXPECTED_5G_URL_FRAGMENT: str = "kongeskibet-dannebrog-5g"

EXPECTED_2_5G_PRICE_DKK: float = 4000.0
EXPECTED_2_5G_URL_FRAGMENT: str = "kongeskibet-dannebrog-2-5g"


def test_monthuset_parses_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = MonthusetScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_5G_PRICE_DKK
    assert EXPECTED_5G_URL_FRAGMENT in str(listing.url)


def test_monthuset_parses_2_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = MonthusetScraper().parse(html, size_g=2.5)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_2_5G_PRICE_DKK
    assert EXPECTED_2_5G_URL_FRAGMENT in str(listing.url)


def test_monthuset_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = MonthusetScraper().parse(html, size_g=1234.0)
    assert listing is None
