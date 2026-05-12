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
    """Extract a DKK price from text like '2.940,00 kr.' / '6.252 kr.' / '5345.47'.

    Danish formatting uses '.' as thousand separator and ',' as decimal. The
    edge case worth flagging: '6.252' (no comma) is a Danish whole-number
    6,252 — NOT 6.252 — which we detect by looking at how the dot-separated
    parts are sized. Pure US-style decimals like '5345.47' (used by Vitus's
    OpenGraph meta) keep parsing correctly because their tail is 1–2 digits.
    """
    import re

    cleaned = re.sub(r"[^\d.,]", "", text)
    cleaned = cleaned.strip(".,")
    if not cleaned:
        return None

    if "," in cleaned and "." in cleaned:
        # Both present: whichever comes last is the decimal mark.
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) in (1, 2):
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "." in cleaned:
        # Only dots present. Treat as Danish thousands separator if every
        # dot-separated chunk after the first is exactly 3 digits — covers
        # '1.825', '108.582', '5.366.123'. Leave US-style decimals alone.
        parts = cleaned.split(".")
        if len(parts) >= 2 and all(len(p) == 3 and p.isdigit() for p in parts[1:]):
            cleaned = cleaned.replace(".", "")

    try:
        return float(cleaned)
    except ValueError:
        return None


def make_html_parser(html: str) -> HTMLParser:
    return HTMLParser(html)


# Danish/English descriptors dealers use when a bar is from a mixed/unspecified
# brand pool ("we'll ship whatever's in stock"). All of these collapse to a
# single canonical "Mixed" label so the UI never surfaces Danish copy.
# Patterns are checked as case-insensitive substrings.
_MIXED_BRAND_PATTERNS: tuple[str, ...] = (
    "blandede mærker",
    "blandede merker",
    "forskellige mærker",
    "forskellige merker",
    "diverse mærker",
    "diverse merker",
    "div. mærker",
    "div. merker",
    "vilkårlige",
    "various brands",
    "mixed brands",
)


def normalize_brand(raw: str | None) -> str | None:
    """Return canonical brand label for a raw title fragment.

    Empty/None input → None. Known mixed-brand descriptors → "Mixed".
    Anything else is returned trimmed and unchanged.
    """
    if not raw:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    lowered = stripped.lower()
    for pattern in _MIXED_BRAND_PATTERNS:
        if pattern in lowered:
            return "Mixed"
    return stripped
