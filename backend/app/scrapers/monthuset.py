import logging
import re

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import DEFAULT_HEADERS, make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)

# Strip the Wayback Machine timestamp prefix from archived URLs so tests work
# against real fixtures.  Matches "/web/<timestamp>/<scheme>://<host>/<path>".
_WAYBACK_PREFIX_RE = re.compile(r"^/web/\d+/")


def _clean_href(href: str) -> str:
    """Remove Wayback Machine URL prefix if present."""
    return _WAYBACK_PREFIX_RE.sub("/", href, count=1)


class MonthusetScraper:
    name = "Mønthuset"
    base_url = "https://www.monthuset.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = f"{self.base_url}/guld/guldbarrer"
        try:
            resp = await client.get(
                url,
                timeout=8.0,
                follow_redirects=True,
                headers={
                    **DEFAULT_HEADERS,
                    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Mønthuset fetch failed: %s", e)
            return Listing(
                dealer=self.name,
                status="error",
                error=f"http: {e.__class__.__name__}",
                fetched_at=now_utc(),
            )
        return self.parse(resp.text, size_g)

    def parse(self, html: str, size_g: float) -> Listing | None:
        tree = make_html_parser(html)
        product = self._find_product_for_size(tree, size_g)
        if product is None:
            return None

        # Price: span.promoproduct__price text (e.g. "7 500 kr")
        price_node = product.css_first("span.promoproduct__price")
        # Link: a.product__title href (contains the product slug)
        link_node = product.css_first("a.product__title")

        if price_node is None or link_node is None:
            return Listing(
                dealer=self.name,
                status="error",
                error="parse_failed: missing price/link node",
                fetched_at=now_utc(),
            )

        price = parse_dkk_price(price_node.text(strip=True))
        if price is None:
            return Listing(
                dealer=self.name,
                status="unavailable",
                error="non-numeric price text",
                fetched_at=now_utc(),
            )

        href = link_node.attributes.get("href") or ""
        href = _clean_href(href)
        url = href if href.startswith("http") else f"{self.base_url}{href}"

        # All server-rendered products on the default listing are in stock.
        # Sold-out products are only injected client-side when the
        # "Vis produkter der er udsolgt" filter is enabled; they are not
        # present in the initial HTML response.
        return Listing(
            dealer=self.name,
            status="ok",
            price_dkk=price,
            in_stock=True,
            url=url,  # type: ignore[arg-type]
            fetched_at=now_utc(),
        )

    def _find_product_for_size(self, tree: HTMLParser, size_g: float) -> Node | None:
        # Mønthuset product titles follow varying formats:
        #   "[Brand] [size] gram [suffix]"   e.g. "Kongeskibet Dannebrog 5 gram"
        #   "[size] gram guldbarre [suffix]"  e.g. "5 gram guldbarre - De 4 generationer"
        # We match by looking for the size string " [size] gram" (leading space to
        # avoid "25 gram" matching a "5 gram" search) anywhere in the title.
        # Danish decimal: 2.5 → "2,5".
        if size_g.is_integer():
            size_str = str(int(size_g))
        else:
            size_str = f"{size_g}".replace(".", ",")

        needle = f"{size_str} gram"

        for card in tree.css("div.product"):
            title_node = card.css_first("a.product__title span[itemprop=name]")
            if title_node is None:
                # Older template variant uses the span directly
                title_node = card.css_first("a.product__title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            # Use a word-boundary-like check: the size must appear preceded by a
            # non-digit character (space, start, letter) to avoid "5 gram"
            # matching inside "25 gram" or "105 gram".
            if re.search(r"(?<!\d)" + re.escape(needle), title):
                return card
        return None
