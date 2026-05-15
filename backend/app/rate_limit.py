"""In-memory per-IP rate limiter.

Single-process Render free tier → no Redis, no cross-worker coordination
needed. A plain dict mapping client IP → last-request monotonic timestamp
is enough. If we ever scale to multiple workers each will keep its own
counter; the effective rate would loosen by the worker count, which is
fine for the spirit of the protection (keep a bot from hammering live
scrapers, not enforce a strict SLA).

Used by the public `/coins` endpoint, which fans out to 5 dealer sites
per request. Without a throttle, a leaked X-API-Key could get our Render
egress IP blocked by dealer WAFs.
"""
from __future__ import annotations

import time
from threading import Lock


class IPRateLimiter:
    """Minimum-interval limiter: each IP must wait `min_interval_s` between
    requests. Returns the seconds-until-allowed (>0 means denied) so the
    caller can populate a `Retry-After` header.

    Thread-safe — FastAPI/uvicorn may run sync portions on worker threads.
    """

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last_seen: dict[str, float] = {}
        self._lock = Lock()
        # When the bucket dict grows past this many entries, prune anything
        # older than 2× the interval on the next check. Bounded memory at
        # ~10 KB for a million distinct IPs since the last sweep.
        self._gc_threshold = 1024

    def check(self, ip: str) -> float:
        """Returns 0.0 if the request is allowed (and records it), or the
        number of seconds remaining in the cool-down if it should be denied.
        """
        now = time.monotonic()
        with self._lock:
            if len(self._last_seen) > self._gc_threshold:
                cutoff = now - (self.min_interval_s * 2)
                self._last_seen = {
                    k: v for k, v in self._last_seen.items() if v >= cutoff
                }
            last = self._last_seen.get(ip)
            if last is not None:
                elapsed = now - last
                if elapsed < self.min_interval_s:
                    return self.min_interval_s - elapsed
            self._last_seen[ip] = now
            return 0.0

    def reset(self) -> None:
        """Test-only: clear all recorded IPs."""
        with self._lock:
            self._last_seen.clear()
