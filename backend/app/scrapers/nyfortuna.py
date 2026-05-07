import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)

# Skip cards whose lower-cased title starts with any of these (circulated/used bars).
_SKIP_PREFIXES = ("cirkuleret",)
# Skip cards whose title contains any of these (combi/multipack/accessory items).
_SKIP_CONTAINS = ("combi", "multigram", " x ", "samleæske", "samle-")


class NyfortunaScraper:
    name = "Nyfortuna"
    base_url = "https://nyfortuna.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = f"{self.base_url}/produkt-kategori/guldbarre/"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Nyfortuna fetch failed: %s", e)
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

        price_node = product.css_first(".woocommerce-Price-amount.amount")
        link_node = product.css_first("a.woocommerce-loop-product__link")
        in_stock = product.css_first("a.add_to_cart_button") is not None

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
        # Nyfortuna's catalog (/produkt-kategori/guldbarre/) uses standard WooCommerce
        # `li.product` cards with titles like "Heimerle+Meule – 5 gram guldbarre stanset".
        # Match by space-padded " <size> gram " to avoid partials (e.g. "0,5 gram"
        # otherwise matching "5 gram"). Skip used and combi variants.
        if size_g.is_integer():
            needle = f" {int(size_g)} gram "
        else:
            needle = " " + f"{size_g}".replace(".", ",") + " gram "

        for card in tree.css("li.product"):
            title_node = card.css_first(".woocommerce-loop-product__title")
            if title_node is None:
                continue
            title = title_node.text(strip=True).lower()
            if any(title.startswith(p) for p in _SKIP_PREFIXES):
                continue
            if any(c in title for c in _SKIP_CONTAINS):
                continue
            if "guldbarre" in title and needle in title:
                return card
        return None
