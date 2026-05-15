"""Email delivery via Resend.

Two helpers: send_magic_link (auth) and send_alert_email (premium alerts).
Dev mode (MAGIC_LINK_DEV_PRINT=1) bypasses Resend for BOTH helpers and logs
to stdout — saves quota and avoids needing a real inbox during local testing.
"""
import asyncio
import html
import json
import logging
import os
from typing import Any

import resend

logger = logging.getLogger(__name__)

_DEFAULT_FROM = "Gold Price Tracker <onboarding@resend.dev>"

# Frontend URL used in the "Manage alerts" link inside alert emails. Same
# env var used by the magic-link flow — Cloudflare Pages origin in prod.
_FRONTEND_BASE = os.environ.get("MAGIC_LINK_BASE_URL", "").rstrip("/")

# Hard ceiling on a single Resend SDK call. The SDK is synchronous and uses
# `requests`, which has no default timeout — a stalled upstream could
# otherwise hang the snapshot response past QStash's 2-min delivery budget
# and trigger a retry that would re-send every alert from the same tick.
# 10s is generous for a single HTTP POST; tune down if Resend latency p99
# starts brushing it.
_RESEND_TIMEOUT_S = 10.0


class EmailSendError(RuntimeError):
    """Raised when Resend fails. Surfaces as 500 to the client."""


async def _resend_send(payload: Any, *, to_email: str, context: str) -> None:
    """Push the synchronous Resend SDK call into a worker thread with a hard
    timeout. Raises EmailSendError on any failure (timeout, SDK exception,
    network) so callers handle a single failure mode.

    `context` is a short identifier ("magic_link" / "alert_email") used in
    the timeout log line for grep-ability.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(resend.Emails.send, payload),
            timeout=_RESEND_TIMEOUT_S,
        )
    except TimeoutError as e:
        logger.warning(
            "resend_timeout %s",
            json.dumps({
                "event": "resend_timeout",
                "context": context,
                "to": to_email,
                "timeout_s": _RESEND_TIMEOUT_S,
            }),
        )
        raise EmailSendError(f"resend send timed out after {_RESEND_TIMEOUT_S:g}s") from e
    except Exception as e:
        logger.exception("resend send failed for %s (%s)", to_email, context)
        raise EmailSendError(str(e)) from e


async def send_magic_link(to_email: str, link_url: str) -> None:
    if os.environ.get("MAGIC_LINK_DEV_PRINT") == "1":
        logger.info("magic link for %s: %s", to_email, link_url)
        return

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise EmailSendError("RESEND_API_KEY not configured")
    resend.api_key = api_key

    from_addr = os.environ.get("RESEND_FROM", _DEFAULT_FROM)
    await _resend_send(
        {
            "from": from_addr,
            "to": [to_email],
            "subject": "Sign in to Gold Price Tracker",
            "html": _html_body(link_url),
            "text": _text_body(link_url),
        },
        to_email=to_email,
        context="magic_link",
    )


def _html_body(link_url: str) -> str:
    body_style = (
        "font-family: -apple-system, BlinkMacSystemFont, sans-serif;"
        " max-width: 480px; margin: 0 auto; padding: 24px; color: #1a1a1a;"
    )
    btn_style = (
        "display: inline-block; padding: 12px 24px; background: #e2c054;"
        " color: #1a1300; text-decoration: none; border-radius: 6px;"
        " font-weight: 600;"
    )
    return f"""\
<!DOCTYPE html>
<html><body style="{body_style}">
  <h2 style="margin: 0 0 16px;">Sign in to Gold Price Tracker</h2>
  <p>You requested a sign-in link. Click the button below to continue — valid for 15 minutes.</p>
  <p style="margin: 24px 0;">
    <a href="{link_url}" style="{btn_style}">Sign in to Gold Price Tracker</a>
  </p>
  <p style="color: #666; font-size: 0.9em;">Or paste this link into your browser:<br>
    <span style="word-break: break-all;">{link_url}</span></p>
  <p style="color: #666; font-size: 0.85em; margin-top: 32px;">
    If you didn't request this, ignore this email.
  </p>
</body></html>"""


def _text_body(link_url: str) -> str:
    return (
        "Sign in to Gold Price Tracker\n\n"
        "You requested a sign-in link. Open this URL to continue — valid for 15 minutes:\n\n"
        f"{link_url}\n\n"
        "If you didn't request this, ignore this email."
    )


# --- Alert emails ----------------------------------------------------------


async def send_alert_email(to_email: str, fires: list[dict]) -> None:
    """Send a bundled premium-alert email. `fires` is a list of dicts in the
    shape produced by alerts._format_fire (target/threshold_pct/current_premium_pct
    /best_dealer/price_dkk). Dev mode logs instead of sending."""
    if not fires:
        return
    subject = _alert_subject(fires)
    if os.environ.get("MAGIC_LINK_DEV_PRINT") == "1":
        logger.info(
            "alert email for %s: %s — %d fires (would send via Resend)",
            to_email, subject, len(fires),
        )
        return

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise EmailSendError("RESEND_API_KEY not configured")
    resend.api_key = api_key

    from_addr = os.environ.get("RESEND_FROM", _DEFAULT_FROM)
    await _resend_send(
        {
            "from": from_addr,
            "to": [to_email],
            "subject": subject,
            "html": _alert_html_body(fires),
            "text": _alert_text_body(fires),
        },
        to_email=to_email,
        context="alert_email",
    )


def _alert_subject(fires: list[dict]) -> str:
    if len(fires) == 1:
        f = fires[0]
        return (
            f"Gold Price Tracker — {f['target']} at "
            f"{f['current_premium_pct']:.2f}% premium"
        )
    return f"Gold Price Tracker — {len(fires)} alerts triggered"


def _alert_html_body(fires: list[dict]) -> str:
    body_style = (
        "font-family: -apple-system, BlinkMacSystemFont, sans-serif;"
        " max-width: 520px; margin: 0 auto; padding: 24px; color: #1a1a1a;"
    )
    card_style = (
        "border: 1px solid #e5e5ea; border-radius: 8px;"
        " padding: 16px; margin: 12px 0; background: #fafafa;"
    )
    target_style = "margin: 0 0 8px; font-size: 1.05em; color: #1a1a1a; font-weight: 600;"
    line_style = "margin: 4px 0; color: #444; font-size: 0.95em;"
    premium_style = "color: #2a6e2a; font-weight: 600;"

    # Defense in depth: target + best_dealer both originate from scraper /
    # registry data. They're trusted today (dealer names come from our own
    # config), but escaping ensures a future scraper sourcing those from
    # page content cannot inject HTML into anyone's inbox.
    sections = []
    for f in fires:
        price_fmt = f"{int(round(f['price_dkk'])):,}".replace(",", ".")
        target_safe = html.escape(str(f["target"]))
        dealer_safe = html.escape(str(f["best_dealer"]))
        sections.append(
            f'<div style="{card_style}">'
            f'  <p style="{target_style}">▼ {target_safe}</p>'
            f'  <p style="{line_style}">'
            f'    Currently <span style="{premium_style}">{f["current_premium_pct"]:.2f}%</span> '
            f'    premium (you wanted ≤ {f["threshold_pct"]:.2f}%)'
            f'  </p>'
            f'  <p style="{line_style}">'
            f'    Best: <strong>{dealer_safe}</strong> at {price_fmt} dkk'
            f'  </p>'
            f'</div>'
        )

    intro = (
        "1 of your gold-price alerts triggered just now:"
        if len(fires) == 1
        else f"{len(fires)} of your gold-price alerts triggered just now:"
    )
    manage_link = (
        f'<p style="color: #666; font-size: 0.9em; margin-top: 24px;">'
        f'  Manage your alerts: <a href="{_FRONTEND_BASE}/">{_FRONTEND_BASE}</a>'
        f'</p>'
        if _FRONTEND_BASE
        else ""
    )
    return f"""\
<!DOCTYPE html>
<html><body style="{body_style}">
  <h2 style="margin: 0 0 16px;">Premium alert</h2>
  <p>Hi,</p>
  <p>{intro}</p>
  {"".join(sections)}
  {manage_link}
  <p style="color: #999; font-size: 0.85em; margin-top: 24px;">
    — Gold Price Tracker
  </p>
</body></html>"""


def _alert_text_body(fires: list[dict]) -> str:
    lines = ["Premium alert", ""]
    if len(fires) == 1:
        lines.append("1 of your gold-price alerts triggered just now:")
    else:
        lines.append(f"{len(fires)} of your gold-price alerts triggered just now:")
    lines.append("")
    for f in fires:
        price_fmt = f"{int(round(f['price_dkk'])):,}".replace(",", ".")
        lines.append(f"  ▼ {f['target']}")
        lines.append(
            f"    Currently {f['current_premium_pct']:.2f}% premium "
            f"(you wanted ≤ {f['threshold_pct']:.2f}%)"
        )
        lines.append(f"    Best: {f['best_dealer']} at {price_fmt} dkk")
        lines.append("")
    if _FRONTEND_BASE:
        lines.append(f"Manage your alerts: {_FRONTEND_BASE}/")
    lines.append("")
    lines.append("— Gold Price Tracker")
    return "\n".join(lines)
