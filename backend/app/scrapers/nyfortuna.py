import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import DEFAULT_HEADERS, make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class NyfortunaScraper:
    name = "Nyfortuna"
    base_url = "https://nyfortuna.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = f"{self.base_url}/butik/guld-salg/"
        try:
            resp = await client.get(
                url,
                timeout=8.0,
                follow_redirects=True,
                headers=DEFAULT_HEADERS,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Nyfortuna fetch failed: %s", e)
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

        # Price: .woocommerce-Price-amount.amount inside .eb-woo-product-price
        price_node = product.css_first(
            ".eb-woo-product-pricing-wrap .woocommerce-Price-amount.amount"
        )
        # Link: <a class="eb-woo-product-image-link" href="...">
        link_node = product.css_first("a.eb-woo-product-image-link")
        # In-stock: the add-to-cart button has class "add_to_cart_button" when in stock;
        # out-of-stock items show a plain "Read more" anchor without that class.
        in_stock = product.css_first("a.add_to_cart_button") is not None

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
        # Nyfortuna uses Essential Blocks WooCommerce carousel: each product is in
        # a <div class="eb-product-carousel-item"> card.
        # Bar titles follow the pattern: "[Brand] – [size] gram guldbarre [variant]"
        # We match only cards with "guldbarre" in the title to exclude coins, then
        # check that the title contains " <size> gram " to avoid partial matches
        # (e.g. "10 gram" matching "100 gram", or "20 gram" matching "120 gram").
        if size_g.is_integer():
            size_needle = f" {int(size_g)} gram "
        else:
            danish_size = f"{size_g}".replace(".", ",")
            size_needle = f" {danish_size} gram "

        for card in tree.css("div.eb-product-carousel-item"):
            title_node = card.css_first(".eb-woo-product-title")
            if title_node is None:
                continue
            title = title_node.text(strip=True).lower()
            if "guldbarre" in title and size_needle in title:
                return card
        return None
