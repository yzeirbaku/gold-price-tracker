from pathlib import Path

from app.scrapers.vitusguld import VitusGuldScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "vitusguld_listing.html"

EXPECTED_5G_PRICE_DKK: float = 5320.61
EXPECTED_5G_URL_FRAGMENT: str = "5-gr-guldbarre-9999"


def test_vitusguld_parses_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = VitusGuldScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_5G_PRICE_DKK
    assert EXPECTED_5G_URL_FRAGMENT in str(listing.url)


def test_vitusguld_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = VitusGuldScraper().parse(html, size_g=1234.0)
    assert listing is None
