import re
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
    assert listing.brand == "Valcambi Suisse"
    assert EXPECTED_5G_URL_FRAGMENT in str(listing.url)


def test_plaza_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = PlazaScraper().parse(html, size_g=1234.0)
    assert listing is None


def _mark_5g_sold_out(html: str) -> str:
    """Inject the theme's sold-out badge into the 5 g card's label slot."""
    idx = html.index("Guldbarre 5 gram Valcambi Suisse</a>")
    labels = list(re.finditer(r'<div\s+class="product-page-labels"[^>]*>', html[:idx]))[-1]
    badge = '<span class="badge out-of-stock">Udsolgt</span>'
    return html[:labels.end()] + badge + html[labels.end():]


def test_plaza_marks_sold_out_bar_out_of_stock() -> None:
    html = _mark_5g_sold_out(FIXTURE.read_text(encoding="utf-8"))
    listing = PlazaScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "out_of_stock"
    assert listing.in_stock is False


def test_plaza_other_sizes_unaffected_by_sold_out_badge() -> None:
    html = _mark_5g_sold_out(FIXTURE.read_text(encoding="utf-8"))
    listing = PlazaScraper().parse(html, size_g=10.0)
    assert listing is not None
    assert listing.status == "ok"


def _card(labels_inner: str):  # noqa: ANN202
    from selectolax.parser import HTMLParser

    html = (
        '<product-card class="product-card"><div class="product-page-labels">'
        f"{labels_inner}</div></product-card>"
    )
    return HTMLParser(html).css_first(".product-card")


def test_is_sold_out_reads_badge_not_price_markup() -> None:
    from app.scrapers.plaza import is_sold_out

    assert is_sold_out(_card("")) is False
    assert is_sold_out(_card('<span class="badge out-of-stock">Udsolgt</span>')) is True
    assert is_sold_out(_card("<span>Sold out</span>")) is True
    # A sale badge alone must not hide an in-stock bar.
    assert is_sold_out(_card('<span class="badge sale">Tilbud</span>')) is False


def test_almost_sold_out_label_is_still_in_stock() -> None:
    from app.scrapers.plaza import is_sold_out

    assert is_sold_out(_card("<span>Næsten udsolgt</span>")) is False
