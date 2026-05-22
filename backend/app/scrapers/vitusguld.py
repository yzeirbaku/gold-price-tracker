import logging

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import (
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


class VitusGuldScraper:
    name = "Vitus Guld"
    base_url = "https://vitusguld.dk"
    listing_url = (
        "https://vitusguld.dk/produkt-kategori/"
        "guldbarre-guldmoenter-guldsmykker/guldbarre/"
    )

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        # Vitus's category listing is cached for several minutes, so its prices
        # drift behind the live spot. The product detail pages render fresh,
        # so we use the listing only to pick a candidate URL+brand and read the
        # price from the product page.
        listing_html, err = await fetch_listing_html(
            client, self.listing_url, timeout=6.0,
        )
        if err:
            logger.warning("Vitus Guld listing fetch failed: %s", err)
            return http_error_listing(self.name, err)

        picked = self.parse_listing(listing_html or "", size_g)
        if picked is None:
            return None
        product_url, brand = picked

        product_html, err = await fetch_listing_html(
            client, product_url, timeout=6.0,
        )
        if err:
            logger.warning("Vitus Guld product fetch failed: %s", err)
            return http_error_listing(self.name, err)

        return self.parse_product(product_html or "", product_url, brand)

    def parse_listing(self, html: str, size_g: float) -> tuple[str, str | None] | None:
        """Pick the cheapest valid in-stock variant; return (product_url, brand)."""
        tree = make_html_parser(html)
        picked = self._find_product_for_size(tree, size_g)
        if picked is None:
            return None
        card, brand = picked
        link_node = card.css_first("a.uael-loop-product__link")
        if link_node is None:
            return None
        href = link_node.attributes.get("href") or ""
        if not href:
            return None
        url = href if href.startswith("http") else f"{self.base_url}{href}"
        return url, brand

    def parse_product(self, html: str, url: str, brand: str | None) -> Listing:
        """Read live price + availability from a Vitus product page's OpenGraph meta."""
        tree = make_html_parser(html)
        price_meta = tree.css_first('meta[property="product:price:amount"]')
        avail_meta = (
            tree.css_first('meta[property="product:availability"]')
            or tree.css_first('meta[property="og:availability"]')
        )
        if price_meta is None:
            return error_listing(self.name, "parse_failed: missing og:price meta")
        price = parse_dkk_price(price_meta.attributes.get("content") or "")
        if price is None:
            return Listing(
                dealer=self.name, status="unavailable",
                error="non-numeric og:price", fetched_at=now_utc(),
            )
        availability = ((avail_meta.attributes.get("content") if avail_meta else "") or "").lower()
        in_stock = availability in {"instock", "in stock"}
        return Listing(
            dealer=self.name,
            status="ok" if in_stock else "out_of_stock",
            price_dkk=price,
            in_stock=in_stock,
            brand=brand,
            url=url,  # type: ignore[arg-type]
            fetched_at=now_utc(),
        )

    def _find_product_for_size(
        self, tree: HTMLParser, size_g: float,
    ) -> tuple[Node, str | None] | None:
        # Vitus has multiple variants per size (Valcambi, PAMP, Argor, plus
        # the "Vilkårlige LBMA producenter" mixed-producer offering, special
        # editions like Eid Mubarak/Rose/Lunar, and combi multipacks).
        # Skip used and combi/multipack only — keep the mixed-LBMA option
        # and label its brand "Mixed". Pick cheapest (in-stock first).
        if size_g.is_integer():
            needle = f"{int(size_g)} gr."
        else:
            needle = f"{size_g}".replace(".", ",") + " gr."

        candidates: list[tuple[float, bool, str | None, Node]] = []
        for card in tree.css("li.product"):
            title_node = card.css_first(".woocommerce-loop-product__title")
            if title_node is None:
                continue
            title = title_node.text(strip=True)
            if not title.startswith(needle):
                continue
            tl = title.lower()
            if "cirkuleret" in tl or "uden emballage" in tl:
                continue
            if " x " in tl or "combi" in tl or "multigram" in tl:
                continue
            price_node = card.css_first(".woocommerce-Price-amount.amount bdi")
            if price_node is None:
                continue
            price = parse_dkk_price(price_node.text(strip=True))
            if price is None:
                continue
            in_stock = card.css_first(".stock.in-stock") is not None
            brand = _extract_brand(title)
            candidates.append((price, in_stock, brand, card))

        picked = pick_cheapest_in_stock(candidates)
        if picked is None:
            return None
        card, _price, _in_stock, brand = picked
        return card, brand


def _extract_brand(title: str) -> str | None:
    # Title shape: "<size> gr. Guldbarre[, fineness], <BRAND>" (varying punctuation).
    # The brand is the last comma-separated chunk; mixed-brand descriptors
    # like "Vilkårlige LBMA producenter" collapse to "Mixed" via normalize_brand.
    last = title.rsplit(",", 1)[-1].strip()
    return normalize_brand(last)
