from pathlib import Path

from app.scrapers.janjorgensen import JanJorgensenScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "janjorgensen_listing.html"

# 5 g card in fixture: "5 g. invest. guldbarre (momsfri)", price 6.252 kr.
EXPECTED_5G_PRICE_DKK: float = 6252.0
EXPECTED_5G_URL_FRAGMENT: str = "5-g.-invest.-guldbarre"


def test_janjorgensen_parses_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = JanJorgensenScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_5G_PRICE_DKK
    assert listing.brand == "Mixed"
    assert EXPECTED_5G_URL_FRAGMENT in str(listing.url)


def test_janjorgensen_parses_10g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = JanJorgensenScraper().parse(html, size_g=10.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == 12065.0
    assert "10-g.-invest.-guldbarre" in str(listing.url)


def test_janjorgensen_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = JanJorgensenScraper().parse(html, size_g=1234.0)
    assert listing is None
