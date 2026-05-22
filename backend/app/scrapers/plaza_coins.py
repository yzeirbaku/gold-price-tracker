"""Plaza only stocks Valcambi gold bars; no coin inventory at the time of
writing. The scraper points at the investeringsguld collection (the umbrella
'investment gold' category) so if Plaza ever adds coins later they'll surface
automatically. Today this returns an empty list.
"""
import logging

import httpx

from app.coins import resolve
from app.models import CoinListing
from app.scrapers.base import (
    FINE_GOLD_CAP_G,
    absolute_url,
    fetch_listing_html,
    http_error_coin_listing,
    make_html_parser,
    now_utc,
)

logger = logging.getLogger(__name__)


class PlazaCoinsScraper:
    name = "Plaza"
    base_url = "https://plaza.dk"
    listing_url = "https://plaza.dk/collections/investeringsguld"

    async def fetch(self, client: httpx.AsyncClient) -> list[CoinListing]:
        html, err = await fetch_listing_html(client, self.listing_url)
        if err is not None:
            logger.warning("Plaza coins fetch failed: %s", err)
            return [http_error_coin_listing(self.name, err.__class__.__name__)]
        assert html is not None
        return self.parse(html)

    def parse(self, html: str) -> list[CoinListing]:
        tree = make_html_parser(html)
        out: list[CoinListing] = []
        for card in tree.css(".product-card"):
            title_node = card.css_first("a.product-card-title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            resolved = resolve(title)
            if resolved is None:
                continue
            coin_type, size_label, gross_g, purity, fine_g = resolved
            if fine_g > FINE_GOLD_CAP_G:
                continue
            price_node = card.css_first(".price ins .amount [data-single-price]")
            price = None
            if price_node is not None:
                raw = price_node.attributes.get("data-single-price") or ""
                try:
                    price = float(raw)
                except ValueError:
                    pass
            in_stock = card.css_first(".price ins") is not None
            href = title_node.attributes.get("href") or ""
            url = absolute_url(href, self.base_url)
            out.append(CoinListing(
                dealer=self.name,
                status="ok" if (in_stock and price) else "out_of_stock",
                coin_type=coin_type,
                size_label=size_label,
                gross_weight_g=gross_g,
                purity=purity,
                fine_gold_g=fine_g,
                price_dkk=price,
                url=url if href else None,  # type: ignore[arg-type]
                fetched_at=now_utc(),
            ))
        return out
