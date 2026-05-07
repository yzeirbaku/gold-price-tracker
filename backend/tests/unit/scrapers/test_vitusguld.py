from pathlib import Path

from app.scrapers.vitusguld import VitusGuldScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "vitusguld_listing.html"


def test_vitusguld_picks_cheapest_branded_5g() -> None:
    """The cheapest 5g matching the brand filter is Valcambi, not the
    'Vilkårlige LBMA producenter' generic which is excluded."""
    html = FIXTURE.read_text(encoding="utf-8")
    listing = VitusGuldScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == 5368.83
    assert listing.url is not None
    assert "valcambi" in str(listing.url).lower()


def test_vitusguld_skips_generic_lbma_variant() -> None:
    """The 'Vilkårlige LBMA' option is cheaper but should be excluded as
    a non-comparable generic/circulated product."""
    html = FIXTURE.read_text(encoding="utf-8")
    listing = VitusGuldScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.url is not None
    assert "vilkaarlige" not in str(listing.url).lower()
    assert "forskellige-producenter" not in str(listing.url).lower()


def test_vitusguld_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = VitusGuldScraper().parse(html, size_g=1234.0)
    assert listing is None
