from pathlib import Path

from app.scrapers.vitusguld import VitusGuldScraper

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"
LISTING_FIXTURE = FIXTURES / "vitusguld_listing.html"
PRODUCT_FIXTURE = FIXTURES / "vitusguld_product.html"


def test_vitusguld_listing_picks_cheapest_5g_includes_mixed() -> None:
    """The cheapest in-stock 5g card in the fixture is the 'Vilkårlige LBMA'
    multi-producer offering (5320.61). It used to be skipped, but the user
    wants it surfaced as brand=Mixed since it's often the cheapest option."""
    html = LISTING_FIXTURE.read_text(encoding="utf-8")
    picked = VitusGuldScraper().parse_listing(html, size_g=5.0)
    assert picked is not None
    url, brand = picked
    assert brand == "Mixed"
    assert "vilkaarlige" in url.lower() or "lbma" in url.lower()


def test_vitusguld_listing_skips_uden_emballage_variant() -> None:
    """The 'UDEN emballage' variant is even cheaper (5272.24) but it's
    out-of-stock AND a different (no-packaging) product."""
    html = LISTING_FIXTURE.read_text(encoding="utf-8")
    picked = VitusGuldScraper().parse_listing(html, size_g=5.0)
    assert picked is not None
    url, _brand = picked
    assert "uden-emballage" not in url.lower()
    assert "u-emballage" not in url.lower()


def test_vitusguld_listing_returns_none_for_unknown_size() -> None:
    html = LISTING_FIXTURE.read_text(encoding="utf-8")
    assert VitusGuldScraper().parse_listing(html, size_g=1234.0) is None


def test_vitusguld_product_reads_live_price_from_og_meta() -> None:
    """Listings are cached and lag spot price; product pages render live,
    so we read price + availability from OpenGraph meta."""
    html = PRODUCT_FIXTURE.read_text(encoding="utf-8")
    listing = VitusGuldScraper().parse_product(
        html, url="https://example.test/p", brand="Valcambi Schweiz",
    )
    assert listing.status == "ok"
    assert listing.price_dkk == 5345.47
    assert listing.in_stock is True
    assert listing.brand == "Valcambi Schweiz"
    assert str(listing.url) == "https://example.test/p"
