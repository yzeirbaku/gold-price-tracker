import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class NordiskGuldScraper:
    name = "Nordisk Guld"
    base_url = "https://nordiskguld.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = f"{self.base_url}/shop/guld/guldbarre/"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Nordisk Guld fetch failed: %s", e)
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

        # Price: the "Vi sælger" (sell) regular price bdi inside .sale .regular-price
        price_node = product.css_first(
            ".sale .regular-price .woocommerce-Price-amount.amount bdi"
        )
        # Link: <a class="thumbnail" href="..."> wraps the product image
        link_node = product.css_first("a.thumbnail")
        # In-stock: the container div has class "instock" when in stock
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
        # Nordisk Guld uses <div class="product-container [instock|outofstock]"> cards.
        # Titles follow Danish format: "Argor-Heraeus 5 gram Kinebar",
        # "PAMP Fortuna 2,5 gram guldbarre" (comma-decimal for non-integers).
        # We match by " X gram " (with surrounding spaces) so "5 gram" does not
        # match "50 gram" or "15 gram".
        if size_g.is_integer():
            needle = f" {int(size_g)} gram "
        else:
            danish_size = f"{size_g}".replace(".", ",")
            needle = f" {danish_size} gram "

        for card in tree.css("div.product-container"):
            title_node = card.css_first(".title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            if needle in title:
                return card
        return None
