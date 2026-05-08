"""Plaza only stocks Valcambi gold bars; no coin inventory at the time of
writing. The scraper points at the investeringsguld collection (the umbrella
'investment gold' category) so if Plaza ever adds coins later they'll surface
automatically. Today this returns an empty list.
"""
import logging

import httpx

from app.coins import resolve
from app.models import CoinListing
from app.scrapers.base import make_html_parser, now_utc

logger = logging.getLogger(__name__)
FINE_GOLD_CAP_G = 20.0


class PlazaCoinsScraper:
    name = "Plaza"
    base_url = "https://plaza.dk"
    listing_url = "https://plaza.dk/collections/investeringsguld"

    async def fetch(self, client: httpx.AsyncClient) -> list[CoinListing]:
        try:
            resp = await client.get(self.listing_url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Plaza coins fetch failed: %s", e)
            return [CoinListing(
                dealer=self.name, status="error",
                error=f"http: {e.__class__.__name__}", fetched_at=now_utc(),
            )]
        return self.parse(resp.text)

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
