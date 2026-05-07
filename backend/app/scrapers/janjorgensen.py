import logging
import re

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import DEFAULT_HEADERS, make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)

# Matches Danish whole-number prices like "6.252 kr." or "109.220 kr."
# where dots are thousand separators with no decimal part.
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
        product = self._find_product_for_size(tree, size_g)
        if product is None:
            return None

        # Price is in the data-price attribute: e.g. "6.252 kr." or "1.833,75 kr."
        price_text = product.attributes.get("data-price") or ""
        # Link: <a class="card-link" href="...">
        link_node = product.css_first("a.card-link")
        # In-stock: delivery text in span.color-green indicates item is available
        in_stock = product.css_first("span.color-green") is not None

        if not price_text or link_node is None:
            return Listing(
                dealer=self.name,
                status="error",
                error="parse_failed: missing price/link node",
                fetched_at=now_utc(),
            )
        # Normalise whole-number Danish prices like "6.252 kr." where '.' is a
        # thousand separator with no decimal — strip dots before parsing.
        normalized = price_text.strip()
        if _DANISH_WHOLE_PRICE_RE.match(normalized):
            normalized = normalized.replace(".", "")
        price = parse_dkk_price(normalized)
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
        # Jan Jørgensen uses <div class="product-block" data-price="..."> cards.
        # Titles follow the pattern: "1 g. invest. guldbarre (incl.moms)"
        # or "2,5 g. invest. guldbarre (momsfri)".
        # We match " <size> g. " (with surrounding spaces/period) to avoid
        # partial matches (e.g. "5 g." matching "50 g.").
        if size_g.is_integer():
            needle = f"{int(size_g)} g."
        else:
            danish_size = f"{size_g}".replace(".", ",")
            needle = f"{danish_size} g."

        for card in tree.css("div.product-block"):
            title_node = card.css_first("span.card-title")
            if title_node is None:
                continue
            # card-title contains a child span.goldfordeling with "*" — use
            # the text of the first text node only to avoid the asterisk noise,
            # but a simple startswith check on the full text works fine too.
            title = title_node.text(strip=True)
            if title.startswith(needle):
                return card
        return None
