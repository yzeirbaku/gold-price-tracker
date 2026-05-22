import json
import logging

import httpx
from selectolax.parser import Node

from app.coins import resolve
from app.models import CoinListing
from app.scrapers.base import (
    FINE_GOLD_CAP_G,
    absolute_url,
    fetch_listing_html,
    http_error_coin_listing,
    make_html_parser,
    now_utc,
    parse_dkk_price,
)

logger = logging.getLogger(__name__)


class TavexCoinsScraper:
    name = "Tavex"
    base_url = "https://tavex.dk"
    listing_url = "https://tavex.dk/guld/guldmonter/"

    async def fetch(self, client: httpx.AsyncClient) -> list[CoinListing]:
        html, err = await fetch_listing_html(client, self.listing_url)
        if err:
            logger.warning("Tavex coins fetch failed: %s", err)
            return [http_error_coin_listing(self.name, err)]
        return self.parse(html or "")

    def parse(self, html: str) -> list[CoinListing]:
        tree = make_html_parser(html)
        out: list[CoinListing] = []
        for card in tree.css(".js-product"):
            cls = card.attributes.get("class") or ""
            if "not-listing" not in cls:
                continue
            title_node = card.css_first(".product__title-inner")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            tl = title.lower()
            if "combibar" in tl or "samleæske" in tl or " x " in tl:
                continue
            resolved = resolve(title)
            if resolved is None:
                continue
            coin_type, size_label, gross_g, purity, fine_g = resolved
            if fine_g > FINE_GOLD_CAP_G:
                continue
            price = _read_sell_price(card)
            in_stock = price is not None and price > 0
            link_node = card.css_first("a.product__overlay-link")
            href = (link_node.attributes.get("href") if link_node else "") or ""
            url = absolute_url(href, self.base_url)
            out.append(CoinListing(
                dealer=self.name,
                status="ok" if in_stock else "out_of_stock",
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


def _read_sell_price(card: Node) -> float | None:
    sell_node = card.css_first(".product__price--single .js-product-price-from")
    if sell_node is not None:
        raw = sell_node.attributes.get("data-pricelist")
        if raw:
            try:
                pl = json.loads(raw)
                tiers = pl.get("sell") or []
                if tiers and isinstance(tiers[0], dict):
                    val = tiers[0].get("price")
                    if isinstance(val, int | float) and val > 0:
                        return float(val)
            except (ValueError, TypeError):
                pass
    text_node = card.css_first(".product__price--single .product__price-value")
    if text_node is not None:
        return parse_dkk_price(text_node.text(strip=True))
    return None
