from pathlib import Path

from app.scrapers.plaza import PlazaScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "plaza_listing.html"

EXPECTED_5G_PRICE_DKK: float = 5766.57
EXPECTED_5G_URL_FRAGMENT: str = "guldbarre-5-gram-valcambi-suisse"


def test_plaza_parses_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = PlazaScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_5G_PRICE_DKK
    assert EXPECTED_5G_URL_FRAGMENT in str(listing.url)


def test_plaza_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = PlazaScraper().parse(html, size_g=1234.0)
    assert listing is None
