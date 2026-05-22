"""Unit tests for the shared scraper helpers introduced in
`app.scrapers.base` (commit 3e948cc on refactor/scraper-base).

The 32 fixture-based dealer scraper tests cover the `.parse()` path
indirectly — they pass identical fixtures through the post-refactor
scrapers and verify the output is unchanged. They do NOT exercise the
new `.fetch()` boilerplate (the AsyncClient.get call, the httpx error
folding) or the helpers in isolation, so this file fills that gap.

Scope: only the helpers added in the refactor. Pure functions get
direct assertions; `fetch_listing_html` is covered with a mocked
httpx client (same pattern as test_fx.py).
"""
from unittest.mock import AsyncMock

import httpx
import pytest

from app.models import CoinListing, Listing
from app.scrapers.base import (
    FINE_GOLD_CAP_G,
    absolute_url,
    error_coin_listing,
    error_listing,
    fetch_listing_html,
    http_error_coin_listing,
    http_error_listing,
    pick_cheapest_in_stock,
)

# ── FINE_GOLD_CAP_G ──────────────────────────────────────────────────────


def test_fine_gold_cap_is_20g() -> None:
    """Single source of truth for the bullion cap — was duplicated across
    five coin scrapers pre-refactor."""
    assert FINE_GOLD_CAP_G == 20.0


# ── pick_cheapest_in_stock ───────────────────────────────────────────────


def test_pick_cheapest_returns_none_for_empty_list() -> None:
    assert pick_cheapest_in_stock([]) is None


def test_pick_cheapest_prefers_in_stock_even_when_more_expensive() -> None:
    """The sort key is (not in_stock, price) — an in-stock row at 6000
    should beat an out-of-stock row at 5000. This is the load-bearing
    invariant of the helper and matches the pre-refactor scrapers'
    `candidates.sort(key=lambda c: (not c[1], c[0]))` line."""
    candidates = [
        (5000.0, False, "Argor",   "card_a"),
        (6000.0, True,  "Valcambi","card_b"),
        (5500.0, False, "PAMP",    "card_c"),
    ]
    picked = pick_cheapest_in_stock(candidates)
    assert picked is not None
    card, price, in_stock, brand = picked
    assert card == "card_b"
    assert price == 6000.0
    assert in_stock is True
    assert brand == "Valcambi"


def test_pick_cheapest_picks_cheapest_among_in_stock() -> None:
    """When multiple rows are in stock, ascending price wins."""
    candidates = [
        (6000.0, True, "Argor",    "card_a"),
        (5500.0, True, "Valcambi", "card_b"),
        (5800.0, True, "PAMP",     "card_c"),
    ]
    picked = pick_cheapest_in_stock(candidates)
    assert picked is not None
    card, price, _in_stock, brand = picked
    assert card == "card_b"
    assert price == 5500.0
    assert brand == "Valcambi"


def test_pick_cheapest_falls_back_to_out_of_stock_when_none_in_stock() -> None:
    """All out-of-stock → cheapest of them — keeps the "we tried" row
    visible to the user instead of returning None."""
    candidates = [
        (6000.0, False, "Argor",    "card_a"),
        (5500.0, False, "Valcambi", "card_b"),
    ]
    picked = pick_cheapest_in_stock(candidates)
    assert picked is not None
    card, price, in_stock, _brand = picked
    assert card == "card_b"
    assert price == 5500.0
    assert in_stock is False


def test_pick_cheapest_returns_reshuffled_tuple_shape() -> None:
    """Input is (price, in_stock, brand, card); output is reshuffled to
    (card, price, in_stock, brand) — the shape every scraper unpacks
    after the call. Regression-test the order so a future tweak can't
    silently swap two fields."""
    picked = pick_cheapest_in_stock(
        [(1234.0, True, "BrandX", "card_only")]
    )
    assert picked == ("card_only", 1234.0, True, "BrandX")


def test_pick_cheapest_handles_none_brand() -> None:
    """Some dealers can't extract a brand and pass None — must not crash."""
    picked = pick_cheapest_in_stock([(1000.0, True, None, "card_x")])
    assert picked is not None
    assert picked[3] is None


def test_pick_cheapest_does_not_mutate_input() -> None:
    """Helper must not mutate the caller's list — uses sorted(), not
    list.sort(). Locks the contract so a future "swap to .sort() for one
    less allocation" refactor surprises a caller that reuses the list."""
    candidates = [
        (6000.0, False, "Argor",    "card_a"),
        (5500.0, True,  "Valcambi", "card_b"),
        (5800.0, True,  "PAMP",     "card_c"),
    ]
    snapshot = list(candidates)
    pick_cheapest_in_stock(candidates)
    assert candidates == snapshot


def test_pick_cheapest_breaks_price_ties_by_input_order() -> None:
    """Two in-stock rows at identical prices: Python's stable sort
    preserves input order — first appended wins. Pre-refactor scrapers
    had this exact tie-break behaviour. Pin it so a future "let's add
    a secondary sort key" tweak doesn't silently change which dealer's
    variant wins on a flat day."""
    candidates = [
        (5500.0, True, "FirstAppended",  "card_first"),
        (5500.0, True, "SecondAppended", "card_second"),
    ]
    picked = pick_cheapest_in_stock(candidates)
    assert picked is not None
    card, _price, _in_stock, brand = picked
    assert card == "card_first"
    assert brand == "FirstAppended"


@pytest.mark.asyncio
async def test_fetch_listing_html_default_timeout_is_eight_seconds() -> None:
    """13 of 14 scrapers pre-refactor used timeout=8.0; only Vitus uses
    6.0 (passed explicitly). Pin the default so a future helper edit
    doesn't silently shift everyone's deadline."""
    resp = AsyncMock()
    resp.text = "<html/>"
    resp.raise_for_status = lambda: None
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp

    await fetch_listing_html(client, "https://x.dk")

    client.get.assert_called_once()
    _args, kwargs = client.get.call_args
    assert kwargs["timeout"] == 8.0


@pytest.mark.asyncio
async def test_fetch_listing_html_default_headers_are_none() -> None:
    """Non-Jan-Jorgensen scrapers don't pass any headers — the dealer
    headers come from the orchestrator-level AsyncClient(headers=
    DEFAULT_HEADERS). Pin the default so a helper edit doesn't
    accidentally start passing `{}` (which would override the
    client-level defaults to nothing)."""
    resp = AsyncMock()
    resp.text = "<html/>"
    resp.raise_for_status = lambda: None
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp

    await fetch_listing_html(client, "https://x.dk")

    client.get.assert_called_once()
    _args, kwargs = client.get.call_args
    assert kwargs["headers"] is None


# ── absolute_url ─────────────────────────────────────────────────────────


def test_absolute_url_passes_http_through_unchanged() -> None:
    href = "http://example.com/p/123"
    assert absolute_url(href, "https://elsewhere.com") == href


def test_absolute_url_passes_https_through_unchanged() -> None:
    href = "https://tavex.dk/produkt/x"
    assert absolute_url(href, "https://otherdomain.dk") == href


def test_absolute_url_prefixes_relative_path() -> None:
    assert (
        absolute_url("/shop/guld/5g", "https://nordiskguld.dk")
        == "https://nordiskguld.dk/shop/guld/5g"
    )


def test_absolute_url_returns_bare_base_for_empty_href() -> None:
    """Pre-refactor scrapers all had a guard like `url=url if href else None`
    AFTER the ternary, so the helper preserves the same "empty → just the
    base URL" behaviour. Callers stay responsible for the None-guard."""
    assert absolute_url("", "https://x.dk") == "https://x.dk"


# ── error_listing + http_error_listing ───────────────────────────────────


def test_error_listing_sets_status_dealer_and_reason() -> None:
    listing = error_listing("Tavex", "parse_failed: missing link node")
    assert isinstance(listing, Listing)
    assert listing.status == "error"
    assert listing.dealer == "Tavex"
    assert listing.error == "parse_failed: missing link node"
    assert listing.price_dkk is None
    assert listing.in_stock is None
    assert listing.fetched_at is not None


def test_http_error_listing_prefixes_reason_with_http_marker() -> None:
    """Wire format `"http: <ExcClassName>"` is what every scraper produced
    pre-refactor and what the orchestrator + outlier guards expect to see.
    This is the contract this refactor must preserve verbatim."""
    listing = http_error_listing("Tavex", "ConnectError")
    assert listing.status == "error"
    assert listing.error == "http: ConnectError"


# ── error_coin_listing + http_error_coin_listing ─────────────────────────


def test_error_coin_listing_sets_status_dealer_and_reason() -> None:
    listing = error_coin_listing("Vitus Guld", "parse_failed: weird HTML")
    assert isinstance(listing, CoinListing)
    assert listing.status == "error"
    assert listing.dealer == "Vitus Guld"
    assert listing.error == "parse_failed: weird HTML"
    assert listing.fetched_at is not None


def test_http_error_coin_listing_prefixes_reason_with_http_marker() -> None:
    listing = http_error_coin_listing("Plaza", "TimeoutException")
    assert listing.status == "error"
    assert listing.error == "http: TimeoutException"


# ── fetch_listing_html ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_listing_html_returns_html_on_success() -> None:
    resp = AsyncMock()
    resp.text = "<html>ok</html>"
    resp.raise_for_status = lambda: None
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp

    html, err = await fetch_listing_html(client, "https://example.dk/list")
    assert html == "<html>ok</html>"
    assert err is None


@pytest.mark.asyncio
async def test_fetch_listing_html_folds_httpx_failure_into_error_tuple() -> None:
    """The whole point of the helper: turn the try/except boilerplate every
    scraper used to inline into a single (None, exc) return. The exception
    object is returned (not just the class name) so callers can log it with
    full message detail; the wire format `http: <ClassName>` is built from
    `exc.__class__.__name__` at the call site."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = httpx.ConnectError("dns lookup failed")

    html, err = await fetch_listing_html(client, "https://example.dk/list")
    assert html is None
    assert isinstance(err, httpx.ConnectError)
    assert err.__class__.__name__ == "ConnectError"
    # Message detail must survive — log greppability depends on it.
    assert "dns lookup failed" in str(err)


@pytest.mark.asyncio
async def test_fetch_listing_html_catches_raise_for_status_failure() -> None:
    """4xx / 5xx responses raise via `raise_for_status` — same broad
    `httpx.HTTPError` family, must be folded into the error tuple too."""
    resp = AsyncMock()
    resp.text = "<html>500</html>"

    def _raise() -> None:
        raise httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=httpx.Request("GET", "https://example.dk/list"),
            response=httpx.Response(503),
        )

    resp.raise_for_status = _raise
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp

    html, err = await fetch_listing_html(client, "https://example.dk/list")
    assert html is None
    assert isinstance(err, httpx.HTTPStatusError)
    assert err.__class__.__name__ == "HTTPStatusError"


@pytest.mark.asyncio
async def test_fetch_listing_html_passes_custom_headers_to_client() -> None:
    """janjorgensen.py and janjorgensen_coins.py rely on a custom Sec-* /
    Accept-Language header set bypassing a WAF. Refactor must keep the
    headers reaching `client.get` unchanged."""
    resp = AsyncMock()
    resp.text = "<html/>"
    resp.raise_for_status = lambda: None
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp

    headers = {"User-Agent": "test/1.0", "Accept-Language": "da-DK"}
    await fetch_listing_html(client, "https://x.dk", headers=headers)

    client.get.assert_called_once()
    _args, kwargs = client.get.call_args
    assert kwargs["headers"] == headers


@pytest.mark.asyncio
async def test_fetch_listing_html_passes_custom_timeout_to_client() -> None:
    """Vitus's two-stage fetch uses timeout=6.0 (not the default 8.0).
    The helper has to honour the override or that scraper will quietly
    start using a different deadline."""
    resp = AsyncMock()
    resp.text = "<html/>"
    resp.raise_for_status = lambda: None
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp

    await fetch_listing_html(client, "https://x.dk", timeout=6.0)

    client.get.assert_called_once()
    _args, kwargs = client.get.call_args
    assert kwargs["timeout"] == 6.0


@pytest.mark.asyncio
async def test_fetch_listing_html_follows_redirects() -> None:
    """All pre-refactor scrapers passed follow_redirects=True. Helper must
    too — otherwise dealers that 301 their category URLs (some do) would
    suddenly start failing with 301-as-error."""
    resp = AsyncMock()
    resp.text = "<html/>"
    resp.raise_for_status = lambda: None
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = resp

    await fetch_listing_html(client, "https://x.dk")

    client.get.assert_called_once()
    _args, kwargs = client.get.call_args
    assert kwargs["follow_redirects"] is True
