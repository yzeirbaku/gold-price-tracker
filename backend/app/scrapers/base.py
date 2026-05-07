from datetime import UTC, datetime
from typing import Protocol

import httpx
from selectolax.parser import HTMLParser

from app.models import Listing

# Some dealer sites (Simply.com WAF — Nordisk Guld, Sero Guld) reject anything that
# doesn't look like a real browser. Sending the full Sec-* fingerprint set bypasses
# the WAF without triggering the proof-of-work challenge page.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Chromium";v="120", "Not(A:Brand";v="24", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


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
