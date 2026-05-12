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
        best = self._find_cheapest_for_size(tree, size_g)
        if best is None:
            return None
        card, price, in_stock, brand = best

        link_node = card.css_first("a.woocommerce-loop-product__link")
        if link_node is None:
            return Listing(
                dealer=self.name,
                status="error",
                error="parse_failed: missing link node",
                fetched_at=now_utc(),
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
        # Cards: standard WooCommerce <li class="product"> with titles like
        # "Heimerle+Meule – 5 gram guldbarre stanset" or "Metalor – 10 gram guldbarre stanset".
        # Match with leading space + Danish decimal so "0,5 gram" doesn't hit "5 gram".
        if size_g.is_integer():
            needle = f" {int(size_g)} gram "
        else:
            needle = " " + f"{size_g}".replace(".", ",") + " gram "

        candidates: list[tuple[float, bool, str | None, Node]] = []
        for card in tree.css("li.product"):
            title_node = card.css_first(".woocommerce-loop-product__title")
            if title_node is None:
                continue
            raw_title = title_node.text(strip=True)
            tl = raw_title.lower()
            if any(tl.startswith(p) for p in _SKIP_PREFIXES):
                continue
            if any(c in tl for c in _SKIP_CONTAINS):
                continue
            if "guldbarre" not in tl or needle not in tl:
                continue
            price_node = card.css_first(".woocommerce-Price-amount.amount")
            if price_node is None:
                continue
            price = parse_dkk_price(price_node.text(strip=True))
            if price is None:
                continue
            in_stock = card.css_first("a.add_to_cart_button") is not None
            brand = _extract_brand(raw_title)
            candidates.append((price, in_stock, brand, card))

        if not candidates:
            return None
        candidates.sort(key=lambda c: (not c[1], c[0]))
        price, in_stock, brand, card = candidates[0]
        return card, price, in_stock, brand


def _extract_brand(title: str) -> str | None:
    # Titles split by an en-dash with surrounding spaces: "Brand – size gram type".
    # Some entries use a regular dash; handle both. normalize_brand collapses
    # any "Blandede Mærker" / "Forskellige Mærker" variants into "Mixed".
    for sep in (" – ", " - "):
        if sep in title:
            return normalize_brand(title.split(sep, 1)[0])
    return None
