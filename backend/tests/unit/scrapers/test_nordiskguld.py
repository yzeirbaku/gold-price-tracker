from pathlib import Path

from app.scrapers.nordiskguld import NordiskGuldScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "nordiskguld_listing.html"

# First 5 g card in fixture: "Argor-Heraeus 5 gram Kinebar", sell price 5 469,53 kr.
EXPECTED_5G_PRICE_DKK: float = 5469.53
EXPECTED_5G_URL_FRAGMENT: str = "argor-heraeus-5-gram-kinebar"


def test_nordiskguld_parses_cheapest_5g_bar() -> None:
    """Cheapest in-stock 5g in fixture is the Argor-Heraeus Kinebar at 5469.53."""
    html = FIXTURE.read_text(encoding="utf-8")
    listing = NordiskGuldScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_5G_PRICE_DKK
    assert listing.brand == "Argor-Heraeus"
    assert EXPECTED_5G_URL_FRAGMENT in str(listing.url)


def test_nordiskguld_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = NordiskGuldScraper().parse(html, size_g=1234.0)
    assert listing is None
