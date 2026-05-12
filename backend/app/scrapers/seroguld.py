import logging
import re

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


class SeroGuldScraper:
    name = "Sero Guld"
    base_url = "https://seroguld.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
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
        best = self._find_cheapest_for_size(tree, size_g)
        if best is None:
            return None
        card, price, in_stock, brand = best

        link_node = card.css_first("a")
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
        # Sero Guld uses WooCommerce <li class="product ...">.
        # Titles: "Valcambi guldbarre 5g", "PAMP guldbarre 2,5g".
        # Skip combi/pre-owned/special variants.
        if size_g.is_integer():
            needle = f"guldbarre {int(size_g)}g"
        else:
            danish_size = f"{size_g}".replace(".", ",")
            needle = f"guldbarre {danish_size}g"

        candidates: list[tuple[float, bool, str | None, Node]] = []
        for card in tree.css("li.product"):
            title_node = card.css_first(".woocommerce-loop-product__title")
            if title_node is None:
                continue
            raw_title = title_node.text(strip=True)
            tl = raw_title.lower()
            if needle not in tl:
                continue
            if "combi" in tl or "pre-owned" in tl or "stjerne" in tl or "ramme" in tl:
                continue
            price_node = card.css_first(".woocommerce-Price-amount.amount bdi")
            if price_node is None:
                continue
            price = parse_dkk_price(price_node.text(strip=True))
            if price is None:
                continue
            in_stock = "instock" in (card.attributes.get("class") or "")
            brand = _extract_brand(raw_title)
            candidates.append((price, in_stock, brand, card))

        if not candidates:
            return None
        candidates.sort(key=lambda c: (not c[1], c[0]))
        price, in_stock, brand, card = candidates[0]
        return card, price, in_stock, brand


def _extract_brand(title: str) -> str | None:
    # Title shape: "<BRAND> guldbarre <size>g" (case varies).
    # Take everything before "guldbarre" as brand, then route through
    # normalize_brand so Danish mixed-brand descriptors collapse to "Mixed".
    m = re.match(r"^(.*?)\s+guldbarre\s+", title, flags=re.IGNORECASE)
    if not m:
        return None
    return normalize_brand(m.group(1))
