import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class VitusGuldScraper:
    name = "Vitus Guld"
    base_url = "https://vitusguld.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = f"{self.base_url}/produkt-kategori/guldbarre-guldmoenter-guldsmykker/guldbarre/"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Vitus Guld fetch failed: %s", e)
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

        # Price: first .woocommerce-Price-amount.amount bdi inside the card
        price_node = product.css_first(".woocommerce-Price-amount.amount bdi")
        # Link: anchor with class uael-loop-product__link
        link_node = product.css_first("a.uael-loop-product__link")
        # In-stock: <p class="stock in-stock"> present when in stock
        in_stock_node = product.css_first(".stock.in-stock")

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
        # WooCommerce product grid uses <li class="product ..."> cards.
        # Vitus Guld titles use "5 gr." format (Danish decimal: "2,5 gr.").
        # We match the first in-stock card whose title starts with "<size> gr."
        # For integer sizes (5.0, 10.0) the needle is "5 gr."; for non-integer
        # (2.5) the needle is "2,5 gr." (Danish comma-decimal).
        if size_g.is_integer():
            needle = f"{int(size_g)} gr."
        else:
            # Format non-integer with Danish comma decimal (e.g. 2.5 → "2,5")
            needle = f"{size_g}".replace(".", ",") + " gr."

        for card in tree.css("li.product"):
            title_node = card.css_first(".woocommerce-loop-product__title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            # Title must start with the size needle to avoid false matches
            # (e.g. "25 gr." matching "5 gr." check, or "50 gr." matching "5 gr.")
            if title.startswith(needle):
                # Prefer in-stock cards; return first match regardless
                return card
        return None
