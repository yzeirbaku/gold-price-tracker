"""Unit tests for the Simply.com WAF proof-of-work handling.

Covers `app.scrapers.simply_waf` in isolation plus its integration into
`fetch_listing_html`. Everything runs against `httpx.MockTransport` — no
network, so this suite is safe for the on-every-push CI job.

The 454 body below is trimmed from a real capture (seroguld.dk, 2026-08-20).
The difficulty is lowered to 8 so the solver finishes in ~256 hashes; the
shape of the `var T=...,TS=...,D=...` line is byte-for-byte what the WAF ships.
"""
import asyncio

import httpx
import pytest

from app.scrapers import simply_waf
from app.scrapers.base import DEFAULT_HEADERS, fetch_listing_html

TOKEN = "620d7710cb823290731a497063af70ad9486de0355b053e741bce9421f0faac2"
TS = "1787222140"

PAGE_URL = "https://seroguld.dk/shop/guld/guldbarrer/"
VERIFY_URL = "https://seroguld.dk/.sc-verify/"
REAL_HTML = "<html><body><li class='product'>Valcambi guldbarre 5g</li></body></html>"


def challenge_body(difficulty: int = 8, token: str = TOKEN) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en" class="errorpage">\n'
        "<head><title>Checking your browser...</title></head>\n"
        "<body><div id=\"challenge\"><h1>454 Checking your browser</h1></div>\n"
        "<script>\n(function(){\n"
        'var SS=location.protocol==="https:"?";SameSite=None;Secure":"";\n'
        f'var T="{token}",TS="{TS}",D={difficulty};\n'
        "})();\n</script></body></html>"
    )


@pytest.fixture(autouse=True)
def _clean_waf_state():
    """The clearance cache is process-level, so isolate every test."""
    simply_waf.reset_state()
    yield
    simply_waf.reset_state()


# ── pure helpers ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("digest", "expected"),
    [
        (b"\xff", 0),
        (b"\x80", 0),
        (b"\x7f", 1),
        (b"\x01", 7),
        (b"\x00\xff", 8),
        (b"\x00\x0f", 12),
        (b"\x00\x00\x80", 16),
        (b"\x00\x00\x00", 24),
    ],
)
def test_leading_zero_bits(digest: bytes, expected: int) -> None:
    assert simply_waf.leading_zero_bits(digest) == expected


def test_parse_challenge_extracts_token_ts_and_difficulty() -> None:
    c = simply_waf.parse_challenge(challenge_body(difficulty=16))
    assert c is not None
    assert c.token == TOKEN
    assert c.ts == TS
    assert c.difficulty == 16


def test_parse_challenge_returns_none_for_ordinary_page() -> None:
    assert simply_waf.parse_challenge(REAL_HTML) is None


def test_solve_finds_nonce_meeting_difficulty() -> None:
    """The solved nonce must satisfy the page's own `lz(...) >= D` rule."""
    import hashlib

    challenge = simply_waf.Challenge(token=TOKEN, ts=TS, difficulty=12)
    nonce = simply_waf.solve(challenge)
    assert nonce is not None
    digest = hashlib.sha256(f"{TOKEN}:{nonce}".encode()).digest()
    assert simply_waf.leading_zero_bits(digest) >= 12


def test_solve_gives_up_at_max_nonce() -> None:
    """An unsatisfiable-within-budget challenge returns None rather than
    spinning — this is what keeps a ratcheted difficulty from pinning the
    single-OCPU VM."""
    challenge = simply_waf.Challenge(token=TOKEN, ts=TS, difficulty=64)
    assert simply_waf.solve(challenge, max_nonce=50) is None


# ── full handshake through fetch_listing_html ────────────────────────────


class Recorder:
    """MockTransport handler scripting the 454 -> verify -> 200 sequence."""

    def __init__(
        self,
        *,
        difficulty: int = 8,
        verify_payload: dict | None = None,
        verify_status: int = 200,
    ) -> None:
        self.difficulty = difficulty
        self.verify_payload = (
            verify_payload if verify_payload is not None
            else {"ok": True, "cookie": "clearance-abc123"}
        )
        self.verify_status = verify_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == simply_waf.VERIFY_PATH:
            return httpx.Response(self.verify_status, json=self.verify_payload)
        cookies = request.headers.get("cookie", "")
        if f"{simply_waf.CLEARANCE_COOKIE}=clearance-abc123" in cookies:
            return httpx.Response(200, text=REAL_HTML)
        return httpx.Response(
            simply_waf.CHALLENGE_STATUS, text=challenge_body(self.difficulty),
        )

    @property
    def page_gets(self) -> int:
        return sum(
            1 for r in self.requests
            if r.method == "GET" and r.url.path != simply_waf.VERIFY_PATH
        )

    @property
    def verify_posts(self) -> int:
        return sum(1 for r in self.requests if r.url.path == simply_waf.VERIFY_PATH)


def client_for(handler: Recorder) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=DEFAULT_HEADERS, transport=httpx.MockTransport(handler),
    )


async def test_challenge_is_solved_and_page_is_returned() -> None:
    """The regression test for the outage: a 454 must end in real HTML, not
    an `http: HTTPStatusError`."""
    handler = Recorder()
    async with client_for(handler) as client:
        html, err = await fetch_listing_html(client, PAGE_URL)

    assert err is None
    assert html == REAL_HTML
    assert handler.verify_posts == 1
    assert handler.page_gets == 2  # challenged, then retried with clearance


async def test_verify_post_carries_ts_nonce_and_token() -> None:
    handler = Recorder()
    async with client_for(handler) as client:
        await fetch_listing_html(client, PAGE_URL)

    post = next(r for r in handler.requests if r.url.path == simply_waf.VERIFY_PATH)
    body = post.content.decode()
    assert f"ts={TS}" in body
    assert f"token={TOKEN}" in body
    assert "nonce=" in body
    assert post.headers["content-type"] == "application/x-www-form-urlencoded"
    # The challenge page issues an XHR, not a navigation.
    assert post.headers["sec-fetch-mode"] == "cors"
    assert post.headers["referer"] == PAGE_URL


async def test_clearance_is_cached_across_clients() -> None:
    """Clients are built per-endpoint, so the cache — not the cookie jar —
    is what stops us re-solving on every page load."""
    first = Recorder()
    async with client_for(first) as client:
        await fetch_listing_html(client, PAGE_URL)
    assert first.verify_posts == 1

    second = Recorder()
    async with client_for(second) as client:
        html, err = await fetch_listing_html(client, PAGE_URL)

    assert err is None
    assert html == REAL_HTML
    assert second.verify_posts == 0, "should reuse the cached clearance"
    assert second.page_gets == 1, "no challenge round trip on the cached path"


async def test_concurrent_scrapers_on_one_host_solve_once() -> None:
    """/snapshot runs the bar and coin scrapers for a host concurrently. The
    per-host lock must collapse them into a single handshake."""
    handler = Recorder()
    async with client_for(handler) as client:
        results = await asyncio.gather(*[
            fetch_listing_html(client, PAGE_URL) for _ in range(4)
        ])

    assert all(err is None and html == REAL_HTML for html, err in results)
    assert handler.verify_posts == 1


# ── degradation paths ────────────────────────────────────────────────────


async def test_rejected_verify_degrades_to_http_status_error() -> None:
    handler = Recorder(verify_payload={"ok": False, "error": "bad-nonce"})
    async with client_for(handler) as client:
        html, err = await fetch_listing_html(client, PAGE_URL)

    assert html is None
    assert isinstance(err, httpx.HTTPStatusError)
    assert err.response.status_code == simply_waf.CHALLENGE_STATUS


async def test_excessive_difficulty_is_refused_without_solving() -> None:
    handler = Recorder(difficulty=simply_waf.MAX_DIFFICULTY + 1)
    async with client_for(handler) as client:
        html, err = await fetch_listing_html(client, PAGE_URL)

    assert html is None
    assert isinstance(err, httpx.HTTPStatusError)
    assert handler.verify_posts == 0, "must not attempt work beyond the cap"


async def test_unparseable_challenge_degrades_cleanly() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            simply_waf.CHALLENGE_STATUS, text="<html>challenge markup changed</html>",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        html, err = await fetch_listing_html(client, PAGE_URL)

    assert html is None
    assert isinstance(err, httpx.HTTPStatusError)


async def test_failed_handshake_backs_off_instead_of_retrying() -> None:
    """A burst of /prices loads must not each re-attempt against a WAF that
    is already refusing us."""
    handler = Recorder(verify_payload={"ok": False, "error": "blocked"})
    async with client_for(handler) as client:
        for _ in range(3):
            await fetch_listing_html(client, PAGE_URL)

    assert handler.verify_posts == 1, "backoff should suppress repeat handshakes"


async def test_non_simply_dealer_path_is_untouched() -> None:
    """A plain 200 must cost exactly one GET and no WAF bookkeeping."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=REAL_HTML)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        html, err = await fetch_listing_html(client, "https://tavex.dk/guld/guldbarrer/")

    assert err is None
    assert html == REAL_HTML
    assert len(seen) == 1
    assert simply_waf.cached_cookie("tavex.dk") is None


async def test_ordinary_http_error_still_folds_to_exception() -> None:
    """Pre-existing contract: a non-454 failure keeps returning the httpx
    exception so scrapers render `http: <ClassName>`."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down for maintenance")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        html, err = await fetch_listing_html(client, PAGE_URL)

    assert html is None
    assert isinstance(err, httpx.HTTPStatusError)
    assert err.response.status_code == 503
