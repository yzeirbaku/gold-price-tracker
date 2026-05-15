"""Unit tests for IPRateLimiter — pure logic, no FastAPI."""
import time
from unittest.mock import patch

from app.rate_limit import IPRateLimiter


def test_first_request_is_allowed():
    rl = IPRateLimiter(min_interval_s=5.0)
    assert rl.check("1.2.3.4") == 0.0


def test_immediate_second_request_is_denied_with_remaining_time():
    rl = IPRateLimiter(min_interval_s=5.0)
    with patch("time.monotonic", side_effect=[1000.0, 1001.0]):
        assert rl.check("1.2.3.4") == 0.0
        wait = rl.check("1.2.3.4")
    assert 3.99 < wait <= 4.0


def test_request_after_interval_is_allowed_again():
    rl = IPRateLimiter(min_interval_s=5.0)
    with patch("time.monotonic", side_effect=[1000.0, 1006.0]):
        assert rl.check("1.2.3.4") == 0.0
        assert rl.check("1.2.3.4") == 0.0


def test_different_ips_dont_share_bucket():
    rl = IPRateLimiter(min_interval_s=5.0)
    with patch("time.monotonic", side_effect=[1000.0, 1000.5]):
        assert rl.check("1.2.3.4") == 0.0
        assert rl.check("5.6.7.8") == 0.0  # different IP, independent


def test_check_records_timestamp_only_on_allow():
    """A denied request must not reset the cool-down clock — otherwise a
    rapid burst could repeatedly slide the window forward."""
    rl = IPRateLimiter(min_interval_s=5.0)
    with patch("time.monotonic", side_effect=[1000.0, 1001.0, 1006.0]):
        assert rl.check("1.2.3.4") == 0.0    # t=1000 recorded
        rl.check("1.2.3.4")                  # t=1001 denied — must NOT overwrite
        assert rl.check("1.2.3.4") == 0.0    # t=1006, 6s after the recorded 1000


def test_gc_prunes_old_entries():
    """When the bucket dict grows past the GC threshold, entries older than
    2× the interval get swept on the next check."""
    rl = IPRateLimiter(min_interval_s=5.0)
    rl._gc_threshold = 5
    base = 1000.0
    timeline = [base + i for i in range(7)] + [base + 100.0]
    with patch("time.monotonic", side_effect=timeline):
        for i in range(7):
            rl.check(f"ip{i}")
        # Threshold exceeded; next call sweeps anything older than t=100-10=90.
        # All 7 old entries are older than 90, so they're pruned.
        rl.check("new_ip")
    assert "new_ip" in rl._last_seen
    assert len(rl._last_seen) == 1


def test_real_clock_smoke():
    """Smoke test against the real monotonic clock — verifies wiring."""
    rl = IPRateLimiter(min_interval_s=0.05)
    assert rl.check("x") == 0.0
    assert rl.check("x") > 0
    time.sleep(0.06)
    assert rl.check("x") == 0.0


def test_reset_clears_state():
    rl = IPRateLimiter(min_interval_s=5.0)
    rl.check("1.2.3.4")
    rl.reset()
    assert rl.check("1.2.3.4") == 0.0
