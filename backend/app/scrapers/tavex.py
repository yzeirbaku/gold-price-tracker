import json
import logging
import re

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
        best = self._find_cheapest_for_size(tree, size_g)
        if best is None:
            return None
        card, price, in_stock, brand = best

        link_node = card.css_first("a.product__overlay-link")
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
        # Tavex cards (.not-listing.js-product) carry their price tiers in the
        # data-pricelist JSON of `.product__price--single .js-product-price-from`.
        # We pick the qty-1 (single-bar) sell price, which is the apples-to-apples
        # comparison with other dealers. Falls back to the rendered price text on
        # the rare cards where the JSON is absent.
        if size_g.is_integer():
            size_token = f"{int(size_g)} gram"
        else:
            size_token = f"{size_g:g}".replace(".", ",") + " gram"

        candidates: list[tuple[float, bool, str | None, Node]] = []
        for card in tree.css(".js-product"):
            cls = card.attributes.get("class") or ""
            if "not-listing" not in cls:
                continue
            title_node = card.css_first(".product__title-inner")
            if title_node is None:
                continue
            raw_title = title_node.text(strip=True)
            tl = raw_title.lower()
            if not tl.startswith(size_token + " "):
                continue
            if "combibar" in tl or " x " in tl:
                continue
            price = _read_sell_price(card)
            if price is None:
                continue
            in_stock = price > 0
            brand = _extract_brand(raw_title, size_token)
            candidates.append((price, in_stock, brand, card))

        if not candidates:
            return None
        candidates.sort(key=lambda c: (not c[1], c[0]))
        price, in_stock, brand, card = candidates[0]
        return card, price, in_stock, brand


def _read_sell_price(card: Node) -> float | None:
    # Preferred path: parse the JSON pricelist on the sell-side price node and
    # take the first tier (quantityFrom: 1) — that's the single-bar price.
    sell_node = card.css_first(".product__price--single .js-product-price-from")
    if sell_node is not None:
        raw = sell_node.attributes.get("data-pricelist")
        if raw:
            try:
                pl = json.loads(raw)
                sell_tiers = pl.get("sell") or []
                if sell_tiers and isinstance(sell_tiers[0], dict):
                    val = sell_tiers[0].get("price")
                    if isinstance(val, int | float) and val > 0:
                        return float(val)
            except (ValueError, TypeError):
                pass
    # Fallback: read the rendered text price.
    text_node = card.css_first(".product__price--single .product__price-value")
    if text_node is not None:
        return parse_dkk_price(text_node.text(strip=True))
    return None


def _extract_brand(title: str, size_token: str) -> str | None:
    # Title shape: "<size_token> <BRAND> Guldbarre" or
    #             "<size_token> Guldbarre (forskellige mærker)" (mixed).
    # Strip the leading "X gram " then trailing "guldbarre" (case-insensitive).
    tl = title.lower()
    idx = tl.find(size_token.lower())
    rest = title[idx + len(size_token):].strip() if idx != -1 else title
    if "forskellige mærker" in rest.lower():
        return "Mixed"
    rest = re.sub(r"\s*guldbarre\s*$", "", rest, flags=re.IGNORECASE).strip()
    return rest or None
