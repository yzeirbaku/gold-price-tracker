"""Jan Jørgensen carries jewelry and investment bars; no bullion-coin
category at the time of writing. The scraper points at the investment-gold
umbrella page so any future coin listings surface automatically. Today
this returns an empty list.
"""
import logging

import httpx

from app.coins import resolve
from app.models import CoinListing
from app.scrapers.base import DEFAULT_HEADERS, make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)
FINE_GOLD_CAP_G = 20.0


class JanJorgensenCoinsScraper:
    name = "Jan Jørgensen"
    base_url = "https://janjorgensensmykker.dk"
    listing_url = "https://janjorgensensmykker.dk/smykker/investeringsguld"

    async def fetch(self, client: httpx.AsyncClient) -> list[CoinListing]:
        try:
            resp = await client.get(
                self.listing_url,
                timeout=8.0,
                follow_redirects=True,
                headers={**DEFAULT_HEADERS, "Accept-Language": "da-DK,da;q=0.9,en;q=0.8"},
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Jan Jorgensen coins fetch failed: %s", e)
            return [CoinListing(
                dealer=self.name, status="error",
                error=f"http: {e.__class__.__name__}", fetched_at=now_utc(),
            )]
        return self.parse(resp.text)

    def parse(self, html: str) -> list[CoinListing]:
        tree = make_html_parser(html)
        out: list[CoinListing] = []
        for card in tree.css("div.product-block"):
            title_node = card.css_first("span.card-title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            resolved = resolve(title)
            if resolved is None:
                continue
            coin_type, size_label, gross_g, purity, fine_g = resolved
            if fine_g > FINE_GOLD_CAP_G:
                continue
            price_node = card.css_first(".price, .product-price, .woocommerce-Price-amount.amount")
            price = parse_dkk_price(price_node.text(strip=True)) if price_node else None
            in_stock = card.css_first("span.color-green") is not None
            link_node = card.css_first("a.card-link") or card.css_first("a")
            href = (link_node.attributes.get("href") if link_node else "") or ""
            url = href if href.startswith("http") else f"{self.base_url}{href}"
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
