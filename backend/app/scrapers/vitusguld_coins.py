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
    parse_dkk_price,
)

logger = logging.getLogger(__name__)


class VitusGuldCoinsScraper:
    name = "Vitus Guld"
    base_url = "https://vitusguld.dk"
    listing_url = (
        "https://vitusguld.dk/produkt-kategori/"
        "guldbarre-guldmoenter-guldsmykker/guldmoenter/"
    )

    async def fetch(self, client: httpx.AsyncClient) -> list[CoinListing]:
        html, err = await fetch_listing_html(client, self.listing_url)
        if err:
            logger.warning("Vitus Guld coins fetch failed: %s", err)
            return [http_error_coin_listing(self.name, err)]
        return self.parse(html or "")

    def parse(self, html: str) -> list[CoinListing]:
        tree = make_html_parser(html)
        out: list[CoinListing] = []
        for card in tree.css("li.product"):
            title_node = card.css_first(".woocommerce-loop-product__title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            tl = title.lower()
            if "cirkuleret" in tl or "uden emballage" in tl or "samleæske" in tl:
                continue
            if " x " in tl or "combi" in tl or "multigram" in tl:
                continue
            resolved = resolve(title)
            if resolved is None:
                continue
            coin_type, size_label, gross_g, purity, fine_g = resolved
            if fine_g > FINE_GOLD_CAP_G:
                continue
            price_node = card.css_first(".woocommerce-Price-amount.amount bdi")
            if price_node is None:
                continue
            price = parse_dkk_price(price_node.text(strip=True))
            in_stock = card.css_first(".stock.in-stock") is not None
            link_node = (
                card.css_first("a.uael-loop-product__link")
                or card.css_first("a.woocommerce-loop-product__link")
            )
            href = (link_node.attributes.get("href") if link_node else "") or ""
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
