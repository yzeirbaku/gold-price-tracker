import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import (
    DEFAULT_HEADERS,
    absolute_url,
    error_listing,
    fetch_listing_html,
    http_error_listing,
    make_html_parser,
    now_utc,
    parse_dkk_price,
    pick_cheapest_in_stock,
)

logger = logging.getLogger(__name__)


class JanJorgensenScraper:
    name = "Jan Jørgensen"
    base_url = "https://janjorgensensmykker.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        html, err = await fetch_listing_html(
            client,
            f"{self.base_url}/smykker/investeringsguldbarrer",
            headers={
                **DEFAULT_HEADERS,
                "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
            },
        )
        if err:
            logger.warning("Jan Jørgensen fetch failed: %s", err)
            return http_error_listing(self.name, err)
        return self.parse(html or "", size_g)

    def parse(self, html: str, size_g: float) -> Listing | None:
        tree = make_html_parser(html)
        best = self._find_cheapest_for_size(tree, size_g)
        if best is None:
            return None
        card, price, in_stock, brand = best

        link_node = card.css_first("a.card-link")
        if link_node is None:
            return error_listing(self.name, "parse_failed: missing link node")
        href = link_node.attributes.get("href") or ""
        url = absolute_url(href, self.base_url)

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
            price = parse_dkk_price(price_text)
            if price is None:
                continue
            in_stock = card.css_first("span.color-green") is not None
            candidates.append((price, in_stock, "Mixed", card))

        return pick_cheapest_in_stock(candidates)
