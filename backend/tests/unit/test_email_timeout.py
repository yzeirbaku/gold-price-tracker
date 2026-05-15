"""Verify the Resend send wrapper enforces a hard timeout.

The Resend SDK is synchronous and offers no timeout knob. `_resend_send`
wraps it in `asyncio.wait_for` so a stalled upstream cannot hang the
snapshot response past QStash's 2-min delivery budget (which would trigger
a cron retry and a duplicate alert email).
"""
import logging

import pytest

from app import email as email_module
from app.email import EmailSendError, _resend_send, send_alert_email, send_magic_link


@pytest.mark.asyncio
async def test_resend_send_raises_on_timeout(monkeypatch, caplog):
    """A blocking SDK call past _RESEND_TIMEOUT_S must raise EmailSendError
    and emit a structured `resend_timeout` log line."""

    def slow_send(_payload):
        import time
        time.sleep(5)

    monkeypatch.setattr(email_module, "_RESEND_TIMEOUT_S", 0.05)
    monkeypatch.setattr(email_module.resend.Emails, "send", slow_send)

    with caplog.at_level(logging.WARNING, logger="app.email"):
        with pytest.raises(EmailSendError, match="timed out"):
            await _resend_send({}, to_email="u@example.com", context="alert_email")

    assert any("resend_timeout" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_resend_send_wraps_sdk_exception(monkeypatch):
    """Non-timeout SDK failures still raise EmailSendError so callers
    only handle one exception type."""

    def boom(_payload):
        raise RuntimeError("upstream 500")

    monkeypatch.setattr(email_module.resend.Emails, "send", boom)

    with pytest.raises(EmailSendError, match="upstream 500"):
        await _resend_send({}, to_email="u@example.com", context="alert_email")


@pytest.mark.asyncio
async def test_resend_send_passes_through_on_success(monkeypatch):
    """Happy path: no exception, returns None."""
    calls: list[dict] = []

    def record(payload):
        calls.append(payload)

    monkeypatch.setattr(email_module.resend.Emails, "send", record)
    await _resend_send({"to": ["u@example.com"]}, to_email="u@example.com", context="alert_email")
    assert calls == [{"to": ["u@example.com"]}]


@pytest.mark.asyncio
async def test_send_alert_email_propagates_timeout(monkeypatch):
    """End-to-end: timeout inside send_alert_email surfaces as EmailSendError
    so evaluate_alerts logs alert_email_failed and leaves the alert un-muted
    (so the next snapshot tick retries instead of swallowing the alert)."""
    monkeypatch.setenv("RESEND_API_KEY", "test")
    monkeypatch.delenv("MAGIC_LINK_DEV_PRINT", raising=False)
    monkeypatch.setattr(email_module, "_RESEND_TIMEOUT_S", 0.05)

    def slow_send(_payload):
        import time
        time.sleep(5)

    monkeypatch.setattr(email_module.resend.Emails, "send", slow_send)
    fires = [{
        "target": "10 g bar", "threshold_pct": 5.0, "current_premium_pct": 4.5,
        "best_dealer": "Tavex", "price_dkk": 7000.0,
    }]
    with pytest.raises(EmailSendError, match="timed out"):
        await send_alert_email("u@example.com", fires)


@pytest.mark.asyncio
async def test_send_magic_link_propagates_timeout(monkeypatch):
    """Same wrapper covers the magic-link path — confirm it also enforces
    the timeout (a hung Resend during sign-in would otherwise leave the
    /auth/request-link request stuck for minutes)."""
    monkeypatch.setenv("RESEND_API_KEY", "test")
    monkeypatch.delenv("MAGIC_LINK_DEV_PRINT", raising=False)
    monkeypatch.setattr(email_module, "_RESEND_TIMEOUT_S", 0.05)

    def slow_send(_payload):
        import time
        time.sleep(5)

    monkeypatch.setattr(email_module.resend.Emails, "send", slow_send)
    with pytest.raises(EmailSendError, match="timed out"):
        await send_magic_link("u@example.com", "https://example.com/#auth=abc")


@pytest.mark.asyncio
async def test_send_alert_email_dev_mode_skips_send(monkeypatch, caplog):
    """Dev-mode short-circuit still wins — no Resend call, no timeout risk."""
    monkeypatch.setenv("MAGIC_LINK_DEV_PRINT", "1")
    sentinel = {"called": False}

    def should_not_run(_payload):
        sentinel["called"] = True

    monkeypatch.setattr(email_module.resend.Emails, "send", should_not_run)
    fires = [{
        "target": "5 g bar", "threshold_pct": 6.0, "current_premium_pct": 5.5,
        "best_dealer": "Vitus", "price_dkk": 3500.0,
    }]
    await send_alert_email("u@example.com", fires)
    assert sentinel["called"] is False
