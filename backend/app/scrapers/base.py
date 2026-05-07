from datetime import UTC, datetime
from typing import Protocol

import httpx
from selectolax.parser import HTMLParser

from app.models import Listing


class DealerScraper(Protocol):
    name: str
    base_url: str

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None: ...


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_dkk_price(text: str) -> float | None:
    """Extract a DKK price from text like '2.940,00 kr.' or '2940 DKK' or '2,940.00 kr'.

    Danish formatting uses '.' as thousand separator and ',' as decimal.
    Returns None if no number can be extracted.
    """
    import re

    # Strip currency markers and whitespace
    cleaned = re.sub(r"[^\d.,]", "", text)
    cleaned = cleaned.strip(".,")
    if not cleaned:
        return None

    # Heuristic: if both '.' and ',' appear and ',' is later, treat ',' as decimal
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Treat ',' as decimal if it has 1-2 digits after it; else thousand sep
        parts = cleaned.split(",")
        if len(parts[-1]) in (1, 2):
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def make_html_parser(html: str) -> HTMLParser:
    return HTMLParser(html)
