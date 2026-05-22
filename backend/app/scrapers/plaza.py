import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import (
    absolute_url,
    error_listing,
    fetch_listing_html,
    http_error_listing,
    make_html_parser,
    normalize_brand,
    now_utc,
    parse_dkk_price,
    pick_cheapest_in_stock,
)

logger = logging.getLogger(__name__)


class PlazaScraper:
    name = "Plaza"
    base_url = "https://plaza.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        html, err = await fetch_listing_html(
            client, f"{self.base_url}/collections/guldbarre",
        )
        if err is not None:
            logger.warning("Plaza fetch failed: %s", err)
            return http_error_listing(self.name, err.__class__.__name__)
        assert html is not None
        return self.parse(html, size_g)

    def parse(self, html: str, size_g: float) -> Listing | None:
        tree = make_html_parser(html)
        best = self._find_cheapest_for_size(tree, size_g)
        if best is None:
            return None
        card, price, in_stock, brand = best

        link_node = card.css_first("a.product-card-title")
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
        # Plaza uses Shopify <product-card> with class "product-card".
        # Titles: "Guldbarre 5 gram Valcambi Suisse".
        if size_g.is_integer():
            needle = f" {int(size_g)} gram "
        else:
            danish_size = f"{size_g}".replace(".", ",")
            needle = f" {danish_size} gram "

        candidates: list[tuple[float, bool, str | None, Node]] = []
        for card in tree.css(".product-card"):
            title_node = card.css_first("a.product-card-title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            if needle not in title:
                continue
            price_node = card.css_first(".price ins .amount [data-single-price]")
            if price_node is None:
                continue
            price = parse_dkk_price(price_node.text(strip=True))
            if price is None:
                continue
            in_stock = card.css_first(".price ins") is not None
            raw_brand = title.split(needle, 1)[1] if needle in title else ""
            candidates.append((price, in_stock, normalize_brand(raw_brand), card))

        return pick_cheapest_in_stock(candidates)
