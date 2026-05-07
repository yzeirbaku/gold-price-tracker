from pathlib import Path

from app.scrapers.vitusguld import VitusGuldScraper

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"
LISTING_FIXTURE = FIXTURES / "vitusguld_listing.html"
PRODUCT_FIXTURE = FIXTURES / "vitusguld_product.html"


def test_vitusguld_listing_picks_cheapest_branded_5g_url() -> None:
    """The cheapest 5g matching the brand filter is Valcambi, not the
    'Vilkårlige LBMA producenter' generic which is excluded."""
    html = LISTING_FIXTURE.read_text(encoding="utf-8")
    url = VitusGuldScraper().parse_listing(html, size_g=5.0)
    assert url is not None
    assert "valcambi" in url.lower()


def test_vitusguld_listing_skips_generic_lbma_variant() -> None:
    """The 'Vilkårlige LBMA' option is cheaper but should be excluded as
    a non-comparable generic/circulated product."""
    html = LISTING_FIXTURE.read_text(encoding="utf-8")
    url = VitusGuldScraper().parse_listing(html, size_g=5.0)
    assert url is not None
    assert "vilkaarlige" not in url.lower()
    assert "forskellige-producenter" not in url.lower()


def test_vitusguld_listing_returns_none_for_unknown_size() -> None:
    html = LISTING_FIXTURE.read_text(encoding="utf-8")
    assert VitusGuldScraper().parse_listing(html, size_g=1234.0) is None


def test_vitusguld_product_reads_live_price_from_og_meta() -> None:
    """Listings are cached and lag spot price by ~10–40 DKK; product pages
    render live, so we read price + availability from OpenGraph meta."""
    html = PRODUCT_FIXTURE.read_text(encoding="utf-8")
    listing = VitusGuldScraper().parse_product(html, url="https://example.test/p")
    assert listing.status == "ok"
    assert listing.price_dkk == 5345.47
    assert listing.in_stock is True
    assert str(listing.url) == "https://example.test/p"
