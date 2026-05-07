import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class SeroGuldScraper:
    name = "Sero Guld"
    base_url = "https://seroguld.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        # /shop/guld/ is a landing page; the actual product grid is /shop/guld/guldbarrer/
        url = f"{self.base_url}/shop/guld/guldbarrer/"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Sero Guld fetch failed: %s", e)
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

        # Price: .woocommerce-Price-amount.amount bdi inside the card
        price_node = product.css_first(".woocommerce-Price-amount.amount bdi")
        # Link: first anchor in the card
        link_node = product.css_first("a")
        # In-stock: the li element has class "instock" when in stock
        in_stock = "instock" in (product.attributes.get("class") or "")

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
        # Sero Guld uses WooCommerce <li class="product ..."> cards.
        # Gold bar titles follow the pattern: "[Brand] guldbarre [size]g"
        # where size is an integer for whole grams ("1g", "5g", "10g")
        # or uses Danish comma-decimal for fractions ("2,5g").
        # We match only cards with "guldbarre" in the title, then check
        # that the title ends with " <size>g" to avoid false matches
        # (e.g. "10g" matching "100g", or "5g" matching "50g").
        if size_g.is_integer():
            needle = f"guldbarre {int(size_g)}g"
        else:
            danish_size = f"{size_g}".replace(".", ",")
            needle = f"guldbarre {danish_size}g"

        for card in tree.css("li.product"):
            title_node = card.css_first(".woocommerce-loop-product__title")
            if title_node is None:
                continue
            title = title_node.text(strip=True).lower()
            if needle in title:
                return card
        return None
