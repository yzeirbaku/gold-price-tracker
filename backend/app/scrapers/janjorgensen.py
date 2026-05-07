import logging
import re

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import DEFAULT_HEADERS, make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)

# Matches Danish whole-number prices like "6.252 kr." or "109.220 kr."
_DANISH_WHOLE_PRICE_RE = re.compile(r"^(\d{1,3}(?:\.\d{3})+)\s*kr", re.IGNORECASE)


class JanJorgensenScraper:
    name = "Jan Jørgensen"
    base_url = "https://janjorgensensmykker.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = f"{self.base_url}/smykker/investeringsguldbarrer"
        try:
            resp = await client.get(
                url,
                timeout=8.0,
                follow_redirects=True,
                headers={
                    **DEFAULT_HEADERS,
                    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
                },
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Jan Jørgensen fetch failed: %s", e)
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

        link_node = card.css_first("a.card-link")
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
        # Cards are <div class="product-block" data-price="...">.
        # Titles: "5 g. invest. guldbarre (momsfri)*" — brand not specified, so "Mixed".
        if size_g.is_integer():
            needle = f"{int(size_g)} g."
        else:
            danish_size = f"{size_g}".replace(".", ",")
            needle = f"{danish_size} g."

        candidates: list[tuple[float, bool, str | None, Node]] = []
        for card in tree.css("div.product-block"):
            title_node = card.css_first("span.card-title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            if not title.startswith(needle):
                continue
            price_text = (card.attributes.get("data-price") or "").strip()
            if not price_text:
                continue
            normalized = price_text
            if _DANISH_WHOLE_PRICE_RE.match(normalized):
                normalized = normalized.replace(".", "")
            price = parse_dkk_price(normalized)
            if price is None:
                continue
            in_stock = card.css_first("span.color-green") is not None
            candidates.append((price, in_stock, "Mixed", card))

        if not candidates:
            return None
        candidates.sort(key=lambda c: (not c[1], c[0]))
        price, in_stock, brand, card = candidates[0]
        return card, price, in_stock, brand
