from pathlib import Path

from app.scrapers.seroguld import SeroGuldScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "seroguld_listing.html"

# First in-stock bar in fixture: "Metalor guldbarre 1g", price 1.565,14 kr.
EXPECTED_1G_PRICE_DKK: float = 1565.14
EXPECTED_1G_URL_FRAGMENT: str = "metalor-guldbarre-1g"


def test_seroguld_parses_1g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = SeroGuldScraper().parse(html, size_g=1.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_1G_PRICE_DKK
    assert EXPECTED_1G_URL_FRAGMENT in str(listing.url)


def test_seroguld_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = SeroGuldScraper().parse(html, size_g=1234.0)
    assert listing is None
