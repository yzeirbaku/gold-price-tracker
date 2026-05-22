from datetime import UTC, datetime
from typing import Protocol

import httpx
from selectolax.parser import HTMLParser

from app.models import CoinListing, Listing

# Bullion-only coin cap: 1 oz coins (31.1g fine) are excluded from /coins
# so the size axis stays comparable across the bar + coin views. Lift this
# only if the comparison story changes. Each coin scraper imports it; do
# not redefine locally.
FINE_GOLD_CAP_G = 20.0

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


# ─── Shared scraper helpers ──────────────────────────────────────────────
# Every dealer scraper performs the same shape of work: fetch a listing
# page, walk cards, collect (price, in_stock, brand, card) candidates,
# pick the cheapest in-stock variant, then build a Listing/CoinListing.
# The helpers below cover the boilerplate so each scraper stays focused on
# its dealer-specific HTML extraction.


async def fetch_listing_html(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 8.0,
    headers: dict[str, str] | None = None,
) -> tuple[str | None, httpx.HTTPError | None]:
    """Fetch an HTML listing page.

    Returns ``(html, None)`` on success, ``(None, exc)`` on httpx failure
    where ``exc`` is the raised ``httpx.HTTPError`` (or subclass such as
    ``ConnectError``, ``TimeoutException``, ``HTTPStatusError``). The
    raw exception is returned so callers can log it with full message
    detail; the on-the-wire ``error`` field is built from
    ``exc.__class__.__name__`` to keep the ``"http: <ClassName>"``
    contract that downstream consumers (snapshot outlier guards, etc.)
    rely on.

    Model-agnostic on purpose — bar scrapers wrap the failure into a
    ``Listing``, coin scrapers into a ``CoinListing``.
    """
    try:
        resp = await client.get(
            url, timeout=timeout, follow_redirects=True, headers=headers,
        )
        resp.raise_for_status()
        return resp.text, None
    except httpx.HTTPError as e:
        return None, e


def pick_cheapest_in_stock[B, N](
    candidates: list[tuple[float, bool, B, N]],
) -> tuple[N, float, bool, B] | None:
    """Sort candidates so in-stock rows come first, then ascending price,
    and return the head reshuffled to ``(card, price, in_stock, brand)``.

    Input tuple shape: ``(price, in_stock, brand, card)`` — matches what
    every bar scraper already builds. Returns ``None`` if the list is
    empty so callers can early-out cleanly.

    Does **not** mutate the input list (uses ``sorted()`` rather than
    ``list.sort``). On ties (same in-stock and same price) Python's
    stable sort preserves input order — first appended wins.
    """
    if not candidates:
        return None
    head = sorted(candidates, key=lambda c: (not c[1], c[0]))[0]
    price, in_stock, brand, card = head
    return card, price, in_stock, brand


def absolute_url(href: str, base_url: str) -> str:
    """Resolve a possibly-relative href against a dealer's base URL.

    Returns ``href`` unchanged if it already starts with ``http``; otherwise
    prefixes it with ``base_url``. Caller is responsible for guarding against
    empty hrefs — this helper preserves that behaviour from the original
    inline expressions.
    """
    return href if href.startswith("http") else f"{base_url}{href}"


def error_listing(dealer: str, reason: str) -> Listing:
    """Construct a status="error" Listing with a fresh ``fetched_at``."""
    return Listing(
        dealer=dealer, status="error", error=reason, fetched_at=now_utc(),
    )


def http_error_listing(dealer: str, exc_class_name: str) -> Listing:
    """Listing for an httpx failure — formatted as ``http: <ExcClassName>``."""
    return error_listing(dealer, f"http: {exc_class_name}")


def error_coin_listing(dealer: str, reason: str) -> CoinListing:
    """Construct a status="error" CoinListing with a fresh ``fetched_at``."""
    return CoinListing(
        dealer=dealer, status="error", error=reason, fetched_at=now_utc(),
    )


def http_error_coin_listing(dealer: str, exc_class_name: str) -> CoinListing:
    """CoinListing for an httpx failure — formatted as ``http: <ExcClassName>``."""
    return error_coin_listing(dealer, f"http: {exc_class_name}")
