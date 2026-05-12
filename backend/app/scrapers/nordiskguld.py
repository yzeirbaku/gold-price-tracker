import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import (
    make_html_parser,
    normalize_brand,
    now_utc,
    parse_dkk_price,
)

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
        best = self._find_cheapest_for_size(tree, size_g)
        if best is None:
            return None
        card, price, in_stock, brand = best

        link_node = card.css_first("a.thumbnail")
        if link_node is None:
            return Listing(
                dealer=self.name, status="error",
                error="parse_failed: missing link node", fetched_at=now_utc(),
            )
        href = link_node.attributes.get("href") or ""
        url = href if href.startswith("http") else f"{self.base_url}{href}"

        return Listing(
            dealer=self.name,
            status="ok" if in_stock else "out_of_stock",
            price_dkk=price,
            in_stock=in_stock,
            brand=brand,
            url=url,  # type: ignore[arg-type]
            fetched_at=now_utc(),
        )

    def _find_cheapest_for_size(
        self, tree: HTMLParser, size_g: float,
    ) -> tuple[Node, float, bool, str | None] | None:
        # Cards are <div class="product-container [instock|outofstock]">.
        # Titles: "Argor-Heraeus 5 gram Kinebar", "PAMP Fortuna 2,5 gram guldbarre".
        # Match by " X gram " (space-padded) so "5 gram" doesn't hit "50 gram".
        if size_g.is_integer():
            needle = f" {int(size_g)} gram "
        else:
            danish_size = f"{size_g}".replace(".", ",")
            needle = f" {danish_size} gram "

        candidates: list[tuple[float, bool, str | None, Node]] = []
        for card in tree.css("div.product-container"):
            title_node = card.css_first(".title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            idx = title.find(needle)
            if idx == -1:
                continue
            price_node = card.css_first(
                ".sale .regular-price .woocommerce-Price-amount.amount bdi"
            )
            if price_node is None:
                continue
            price = parse_dkk_price(price_node.text(strip=True))
            if price is None:
                continue
            in_stock = "instock" in (card.attributes.get("class") or "")
            brand = normalize_brand(title[:idx])
            candidates.append((price, in_stock, brand, card))

        if not candidates:
            return None
        # In-stock first, then cheapest.
        candidates.sort(key=lambda c: (not c[1], c[0]))
        price, in_stock, brand, card = candidates[0]
        return card, price, in_stock, brand
