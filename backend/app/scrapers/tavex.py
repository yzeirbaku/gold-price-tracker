import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class TavexScraper:
    name = "Tavex"
    base_url = "https://tavex.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = "https://tavex.dk/guld/guldbarrer/"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Tavex fetch failed: %s", e)
            return Listing(
                dealer=self.name, status="error",
                error=f"http: {e.__class__.__name__}", fetched_at=now_utc(),
            )
        return self.parse(resp.text, size_g)

    def parse(self, html: str, size_g: float) -> Listing | None:
        tree = make_html_parser(html)
        product = self._find_product_for_size(tree, size_g)
        if product is None:
            return None

        # Price: first "Vi sælger 1+" price span inside .product__price--single
        price_node = product.css_first(".product__price--single .product__price-value")
        # Link: overlay anchor that wraps the whole card
        link_node = product.css_first("a.product__overlay-link")
        # In-stock: span with class product__in-stock is present when on-stock
        in_stock_node = product.css_first(".product__in-stock")

        if price_node is None or link_node is None:
            return Listing(
                dealer=self.name, status="error",
                error="parse_failed: missing price/link node", fetched_at=now_utc(),
            )
        price = parse_dkk_price(price_node.text(strip=True))
        if price is None:
            return Listing(
                dealer=self.name, status="unavailable",
                error="non-numeric price text", fetched_at=now_utc(),
            )
        in_stock = in_stock_node is not None
        href = link_node.attributes.get("href") or ""
        url = href if href.startswith("http") else f"{self.base_url}{href}"

        return Listing(
            dealer=self.name,
            status="ok" if in_stock else "out_of_stock",
            price_dkk=price,
            in_stock=in_stock,
            url=url,  # type: ignore[arg-type]
            fetched_at=now_utc(),
        )

    def _find_product_for_size(self, tree: HTMLParser, size_g: float) -> Node | None:
        # Product grid cards have class "not-listing js-product".
        # Skip "product--listing" cards (carousel/sidebar items without prices).
        # Tavex uses Danish decimal: "2,5 gram", "5 gram", "10 gram".
        if size_g.is_integer():
            needle = f"{int(size_g)} gram"
        else:
            needle = f"{size_g:g}".replace(".", ",") + " gram"
        for card in tree.css(".js-product"):
            cls = card.attributes.get("class") or ""
            if "not-listing" not in cls:
                continue
            title_node = card.css_first(".product__title-inner")
            if title_node is None:
                continue
            if needle in title_node.text(strip=True).lower():
                return card
        return None
