"""Simply.com WAF proof-of-work challenge handling.

Nordisk Guld and Sero Guld are both hosted on Simply.com, whose WAF gates
every path — including `robots.txt` — behind an HTTP 454 "Checking your
browser" interstitial. Until 2026-08 the browser header fingerprint in
`DEFAULT_HEADERS` was enough to avoid it. It no longer is: the WAF now always
issues a SHA-256 proof-of-work challenge, which is why all four Nordisk/Sero
scrapers (bars + coins) began returning `http: HTTPStatusError` at once.

The handshake the challenge page's inline script performs, and that we mirror:

1. Parse `var T="<token>",TS="<ts>",D=<difficulty>` out of the 454 body.
2. Find a nonce where `sha256(f"{token}:{nonce}")` has >= D leading zero bits.
3. `POST ts/nonce/token` (form-encoded) to `/.sc-verify/`, which answers
   `{"ok": true, "cookie": "<clearance>"}`.
4. Re-request with `sc_clearance=<clearance>` in the cookie jar.

The clearance is cached per host and reused, because:

- The challenge script sets `max-age=86400`, so one solve covers 24h.
- Every httpx client in this app is built per-endpoint (`orchestrator.run`,
  `/coins`, `/snapshot`, `/health`), so a client cookie jar throws the
  clearance away after each request and we would re-solve on every page load.
  A process-level cache keyed by host is the only place it survives.

Solving is CPU-bound, so it runs in a worker thread — the backend shares a
single-OCPU Always-Free VM with net-tracker and must not stall its event loop.
`MAX_DIFFICULTY` bounds how much work we accept, so a ratcheted difficulty
degrades these two dealers instead of pinning the CPU for every endpoint.
"""
import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

CHALLENGE_STATUS = 454
VERIFY_PATH = "/.sc-verify/"
CLEARANCE_COOKIE = "sc_clearance"

# The challenge script sets max-age=86400. Expire ours an hour early so we
# never present a cookie that dies mid-flight.
CLEARANCE_TTL_S = 23 * 3600

# Difficulty 16 (what the WAF serves today) is ~65k hashes, well under 0.1s.
# Refuse past 24 (~16M hashes, tens of seconds): the scraper deadline is 10s
# and the VM has one OCPU, so burning it would take down every endpoint rather
# than just these two dealers.
MAX_DIFFICULTY = 24

# Hard ceiling on the search regardless of difficulty, so a malformed or
# unsatisfiable challenge cannot spin forever.
MAX_NONCE = 1 << 26

# After a failed handshake, stop trying for this long. Without it a burst of
# /prices loads would each re-attempt against a WAF already refusing us.
FAILURE_BACKOFF_S = 300.0

_CHALLENGE_RE = re.compile(r'var\s+T="([0-9a-fA-F]+)"\s*,\s*TS="(\d+)"\s*,\s*D=(\d+)')


@dataclass(frozen=True)
class Challenge:
    token: str
    ts: str
    difficulty: int


@dataclass(frozen=True)
class _Clearance:
    cookie: str
    expires_at: float


# Process-level state. Plain dicts are safe here: every mutation happens on
# the event loop thread between awaits, and the per-host lock below serialises
# the read-modify-write around a handshake.
_clearance: dict[str, _Clearance] = {}
_blocked_until: dict[str, float] = {}
_locks: dict[str, asyncio.Lock] = {}


def reset_state() -> None:
    """Drop all cached clearances, backoffs and locks. Test helper."""
    _clearance.clear()
    _blocked_until.clear()
    _locks.clear()


def _lock_for(host: str) -> asyncio.Lock:
    lock = _locks.get(host)
    if lock is None:
        lock = asyncio.Lock()
        _locks[host] = lock
    return lock


def leading_zero_bits(digest: bytes) -> int:
    """Count leading zero bits in `digest` — the page's `lz()` over hex."""
    bits = 0
    for byte in digest:
        if byte:
            return bits + 8 - byte.bit_length()
        bits += 8
    return bits


def parse_challenge(html: str) -> Challenge | None:
    """Extract the PoW parameters from a 454 body, or None if absent."""
    m = _CHALLENGE_RE.search(html)
    if m is None:
        return None
    return Challenge(token=m.group(1), ts=m.group(2), difficulty=int(m.group(3)))


def solve(challenge: Challenge, *, max_nonce: int = MAX_NONCE) -> int | None:
    """Find a nonce satisfying `challenge`, or None within `max_nonce` tries.

    Blocking and CPU-bound — call via `asyncio.to_thread`. Compares the digest
    as a big integer rather than counting hex nibbles like the page's `lz()`;
    the two agree because "N leading zero bits" is exactly "value < 2^(256-N)".
    """
    prefix = f"{challenge.token}:".encode()
    ceiling = 1 << (256 - challenge.difficulty)
    for nonce in range(max_nonce):
        digest = hashlib.sha256(prefix + str(nonce).encode()).digest()
        if int.from_bytes(digest, "big") < ceiling:
            return nonce
    return None


def cached_cookie(host: str) -> str | None:
    """Return a still-valid clearance cookie for `host`, or None."""
    entry = _clearance.get(host)
    if entry is None:
        return None
    if time.monotonic() >= entry.expires_at:
        del _clearance[host]
        return None
    return entry.cookie


def _in_backoff(host: str) -> bool:
    until = _blocked_until.get(host)
    if until is None:
        return False
    if time.monotonic() >= until:
        del _blocked_until[host]
        return False
    return True


def _install(client: httpx.AsyncClient, host: str, cookie: str) -> None:
    """Put `cookie` on `client`'s jar.

    Uses the jar rather than a per-request `Cookie` header because httpx
    rebuilds that header from the jar on every request, and would clobber ours
    as soon as the dealer's own WooCommerce cookies landed.
    """
    client.cookies.set(CLEARANCE_COOKIE, cookie, domain=host, path="/")


def apply_clearance(client: httpx.AsyncClient, url: str) -> None:
    """Install a cached clearance for `url`'s host, if we hold one.

    A no-op dict lookup for every non-Simply dealer.
    """
    host = httpx.URL(url).host
    cookie = cached_cookie(host)
    if cookie is not None:
        _install(client, host, cookie)


async def clear_challenge(
    client: httpx.AsyncClient,
    url: str,
    html: str,
    *,
    timeout: float,
) -> bool:
    """Solve the 454 challenge in `html` and install the clearance on `client`.

    Returns True when `url` is worth retrying. False means the caller should
    surface the original 454 — an unparseable body, a difficulty past
    `MAX_DIFFICULTY`, no nonce found, or a rejected verify call.
    """
    host = httpx.URL(url).host
    async with _lock_for(host):
        # Double-checked: a sibling scraper on the same host may have solved
        # while we waited. /snapshot runs bars and coins concurrently, so
        # Nordisk's two scrapers race here on every tick.
        cookie = cached_cookie(host)
        if cookie is not None:
            _install(client, host, cookie)
            return True
        if _in_backoff(host):
            return False

        cookie = await _handshake(client, url, html, timeout=timeout)
        if cookie is None:
            _blocked_until[host] = time.monotonic() + FAILURE_BACKOFF_S
            return False

        _clearance[host] = _Clearance(cookie, time.monotonic() + CLEARANCE_TTL_S)
        _install(client, host, cookie)
        return True


async def _handshake(
    client: httpx.AsyncClient,
    url: str,
    html: str,
    *,
    timeout: float,
) -> str | None:
    host = httpx.URL(url).host
    challenge = parse_challenge(html)
    if challenge is None:
        logger.warning(
            "simply_waf: no challenge found in 454 body for %s — WAF markup changed?", url,
        )
        return None
    if challenge.difficulty > MAX_DIFFICULTY:
        logger.warning(
            "simply_waf: refusing difficulty %d (max %d) for %s",
            challenge.difficulty, MAX_DIFFICULTY, url,
        )
        return None

    started = time.monotonic()
    nonce = await asyncio.to_thread(solve, challenge)
    if nonce is None:
        logger.warning(
            "simply_waf: no nonce within %d tries at difficulty %d for %s",
            MAX_NONCE, challenge.difficulty, url,
        )
        return None
    solve_ms = (time.monotonic() - started) * 1000

    verify_url = str(httpx.URL(url).join(VERIFY_PATH))
    # DEFAULT_HEADERS on the client describe a top-level navigation; this is
    # the challenge page's XHR, so correct the Sec-Fetch-* trio and add the
    # Referer the browser would send.
    xhr_headers = {
        "Referer": url,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    try:
        resp = await client.post(
            verify_url,
            data={"ts": challenge.ts, "nonce": str(nonce), "token": challenge.token},
            headers=xhr_headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("simply_waf: verify call failed for %s: %r", verify_url, e)
        return None

    if not isinstance(payload, dict) or not payload.get("ok"):
        detail = payload.get("error") if isinstance(payload, dict) else payload
        logger.warning("simply_waf: verify rejected for %s: %s", verify_url, detail)
        return None

    cookie = payload.get("cookie")
    if not isinstance(cookie, str) or not cookie:
        logger.warning("simply_waf: verify ok but no cookie for %s", verify_url)
        return None

    logger.info(
        "simply_waf: cleared %s (difficulty=%d nonce=%d solve_ms=%.0f)",
        host, challenge.difficulty, nonce, solve_ms,
    )
    return cookie
