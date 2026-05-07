from pathlib import Path

from app.scrapers.nyfortuna import NyfortunaScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "nyfortuna_listing.html"


def test_nyfortuna_parses_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = NyfortunaScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == 5366.02
    assert listing.in_stock is True
    assert listing.brand == "Heimerle+Meule"
    assert listing.url is not None
    assert "guldbarre-standset-5g" in str(listing.url)


def test_nyfortuna_parses_2_5g_bar() -> None:
    """Catalog has a Heimerle+Meule 2,5g bar; carousel page didn't."""
    html = FIXTURE.read_text(encoding="utf-8")
    listing = NyfortunaScraper().parse(html, size_g=2.5)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == 2894.21


def test_nyfortuna_skips_circulated_bars() -> None:
    """The cheapest 50g listing in the fixture is 'Cirkuleret – 50 gram guldbarre'.
    The scraper must skip it and pick the standard Heimerle+Meule branded one."""
    html = FIXTURE.read_text(encoding="utf-8")
    listing = NyfortunaScraper().parse(html, size_g=50.0)
    assert listing is not None
    # 'Cirkuleret' price was 50,252.57 DKK; this should be a non-circulated bar.
    assert listing.price_dkk != 50252.57
    assert listing.url is not None
    assert "cirkuleret" not in str(listing.url).lower()


def test_nyfortuna_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = NyfortunaScraper().parse(html, size_g=1234.0)
    assert listing is None
