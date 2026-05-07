import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class PlazaScraper:
    name = "Plaza"
    base_url = "https://plaza.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = f"{self.base_url}/collections/guldbarre"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Plaza fetch failed: %s", e)
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

        # Price: <span data-single-price> inside .price ins .amount
        price_node = product.css_first(".price ins .amount [data-single-price]")
        # Link: anchor with class product-card-title (also carries the title text)
        link_node = product.css_first("a.product-card-title")
        # In-stock: presence of .price ins indicates an active (non-struck-through) price
        in_stock_node = product.css_first(".price ins")

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
        # Plaza uses Shopify <product-card> custom elements with class "product-card".
        # Titles follow the pattern "Guldbarre X gram Valcambi Suisse".
        # We match by checking that the title contains " X gram " (with surrounding
        # spaces) to prevent "5 gram" from matching "25 gram" or "50 gram".
        # For non-integer sizes (2.5), Plaza uses Danish comma decimal: "2,5 gram".
        if size_g.is_integer():
            needle = f" {int(size_g)} gram "
        else:
            danish_size = f"{size_g}".replace(".", ",")
            needle = f" {danish_size} gram "

        for card in tree.css(".product-card"):
            title_node = card.css_first("a.product-card-title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            if needle in title:
                return card
        return None
