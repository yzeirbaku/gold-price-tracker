from pathlib import Path

from app.scrapers.tavex import TavexScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "tavex_5g.html"

# Cheapest in-stock 5g in fixture: "5 gram Guldbarre (forskellige mærker)" at 5342,14
# (Tavex's mixed-producer offering — cheaper than the branded variants).
EXPECTED_PRICE_DKK = 5342.14
EXPECTED_URL_FRAGMENT = "5-gram-guldbarre-blandede-maerker"


def test_tavex_picks_cheapest_5g_including_mixed_offering() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = TavexScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_PRICE_DKK
    assert listing.brand == "Mixed"
    assert listing.in_stock is True
    assert listing.url is not None and EXPECTED_URL_FRAGMENT in str(listing.url)


def test_tavex_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = TavexScraper().parse(html, size_g=1234.0)
    assert listing is None
