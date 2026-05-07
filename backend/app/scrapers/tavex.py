import logging
import re

import httpx
from selectolax.parser import HTMLParser, Node

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class TavexScraper:
    name = "Tavex"
    base_url = "https://tavex.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = "https://tavex.dk/guld/guldbarrer/"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Tavex fetch failed: %s", e)
            return Listing(
                dealer=self.name, status="error",
                error=f"http: {e.__class__.__name__}", fetched_at=now_utc(),
            )
        return self.parse(resp.text, size_g)

    def parse(self, html: str, size_g: float) -> Listing | None:
        tree = make_html_parser(html)
        best = self._find_cheapest_for_size(tree, size_g)
        if best is None:
            return None
        card, price, in_stock, brand = best

        link_node = card.css_first("a.product__overlay-link")
        if link_node is None:
            return Listing(
                dealer=self.name, status="error",
                error="parse_failed: missing link node", fetched_at=now_utc(),
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
        # Product grid cards have class "not-listing js-product".
        # Skip "product--listing" (carousel/sidebar items without prices) and combi/multipacks.
        # Tavex titles: "5 gram Valcambi Suisse Guldbarre",
        #               "5 gram Guldbarre (forskellige mærker)" (mixed-brand offering).
        if size_g.is_integer():
            size_token = f"{int(size_g)} gram"
        else:
            size_token = f"{size_g:g}".replace(".", ",") + " gram"

        candidates: list[tuple[float, bool, str | None, Node]] = []
        for card in tree.css(".js-product"):
            cls = card.attributes.get("class") or ""
            if "not-listing" not in cls:
                continue
            title_node = card.css_first(".product__title-inner")
            if title_node is None:
                continue
            raw_title = title_node.text(strip=True)
            tl = raw_title.lower()
            # Tavex titles always start with the size, so use startswith to avoid
            # "5 gram" matching "2,5 gram" / "25 gram" / "50 gram".
            if not tl.startswith(size_token + " "):
                continue
            # Skip combi/multipack ("100x 1 gram", "50 x 1 gram CombiBar") — different product.
            if "combibar" in tl or " x " in tl:
                continue
            price_node = card.css_first(".product__price--single .product__price-value")
            if price_node is None:
                continue
            price = parse_dkk_price(price_node.text(strip=True))
            if price is None:
                continue
            in_stock = card.css_first(".product__in-stock") is not None
            brand = _extract_brand(raw_title, size_token)
            candidates.append((price, in_stock, brand, card))

        if not candidates:
            return None
        candidates.sort(key=lambda c: (not c[1], c[0]))
        price, in_stock, brand, card = candidates[0]
        return card, price, in_stock, brand


def _extract_brand(title: str, size_token: str) -> str | None:
    # Title shape: "<size_token> <BRAND> Guldbarre" or
    #             "<size_token> Guldbarre (forskellige mærker)" (mixed).
    # Strip the leading "X gram " then trailing "guldbarre" (case-insensitive).
    tl = title.lower()
    idx = tl.find(size_token.lower())
    rest = title[idx + len(size_token):].strip() if idx != -1 else title
    if "forskellige mærker" in rest.lower():
        return "Mixed"
    rest = re.sub(r"\s*guldbarre\s*$", "", rest, flags=re.IGNORECASE).strip()
    return rest or None
