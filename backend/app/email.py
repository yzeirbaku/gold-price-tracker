"""Email delivery via Resend.

Single helper: send_magic_link. Dev mode (MAGIC_LINK_DEV_PRINT=1) bypasses
Resend entirely and logs the link to stdout — saves quota and avoids needing
a real inbox during local testing.
"""
import logging
import os

import resend

logger = logging.getLogger(__name__)

_DEFAULT_FROM = "Gold Tracker <onboarding@resend.dev>"


class EmailSendError(RuntimeError):
    """Raised when Resend fails. Surfaces as 500 to the client."""


async def send_magic_link(to_email: str, link_url: str) -> None:
    if os.environ.get("MAGIC_LINK_DEV_PRINT") == "1":
        logger.info("magic link for %s: %s", to_email, link_url)
        return

    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise EmailSendError("RESEND_API_KEY not configured")
    resend.api_key = api_key

    from_addr = os.environ.get("RESEND_FROM", _DEFAULT_FROM)
    html_body = _html_body(link_url)
    text_body = _text_body(link_url)

    try:
        resend.Emails.send({
            "from": from_addr,
            "to": [to_email],
            "subject": "Sign in to Gold Tracker",
            "html": html_body,
            "text": text_body,
        })
    except Exception as e:
        logger.exception("resend send failed for %s", to_email)
        raise EmailSendError(str(e)) from e


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
  <h2 style="margin: 0 0 16px;">Sign in to Gold Tracker</h2>
  <p>You requested a sign-in link. Click the button below to continue — valid for 15 minutes.</p>
  <p style="margin: 24px 0;">
    <a href="{link_url}" style="{btn_style}">Sign in to Gold Tracker</a>
  </p>
  <p style="color: #666; font-size: 0.9em;">Or paste this link into your browser:<br>
    <span style="word-break: break-all;">{link_url}</span></p>
  <p style="color: #666; font-size: 0.85em; margin-top: 32px;">
    If you didn't request this, ignore this email.
  </p>
</body></html>"""


def _text_body(link_url: str) -> str:
    return (
        "Sign in to Gold Tracker\n\n"
        "You requested a sign-in link. Open this URL to continue — valid for 15 minutes:\n\n"
        f"{link_url}\n\n"
        "If you didn't request this, ignore this email."
    )
