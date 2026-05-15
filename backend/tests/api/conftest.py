"""Shared fixtures for endpoint integration tests.

These exercise the FastAPI app against a real Postgres (local Docker or the
GitHub Actions sidecar). They catch the class of bugs that pure-logic unit
tests can't: header parsing, router wiring, Pydantic serialization, schema
↔ query drift, and dependency-injection wiring of the auth/session layer.

Skipped wholesale when `DATABASE_URL` is unset so a fresh checkout still
sees a green `pytest tests/unit` run without Docker.

External IO (Resend, yfinance, frankfurter, gold-api) is stubbed in
`mock_external` — the tests focus on the app's behavior, not upstream
correctness.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Skip the whole folder when no DB is configured. Local dev without Docker
# stays green; CI sets DATABASE_URL via the postgres service container.
if not os.environ.get("DATABASE_URL"):
    pytest.skip(
        "DATABASE_URL not set; skipping endpoint integration tests "
        "(run `docker compose up -d` and export DATABASE_URL to enable)",
        allow_module_level=True,
    )

# Sign-in flow needs this — set a sensible default so test runs don't depend
# on the developer's local shell env.
os.environ.setdefault("MAGIC_LINK_BASE_URL", "http://localhost:5500")
os.environ.setdefault("API_KEY", "test-api-key")

from app import db as db_module  # noqa: E402
from app import email as email_module  # noqa: E402
from app import portfolio as portfolio_module  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def pool():
    """Single asyncpg pool for the whole test session. Schema is bootstrapped
    on first acquire via db_module.SCHEMA_SQL."""
    p = await db_module.get_pool()
    if p is None:
        pytest.fail("get_pool() returned None despite DATABASE_URL being set")
    yield p
    await db_module.close_pool()


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _truncate(pool):
    """Reset all relevant tables before each test. TRUNCATE … CASCADE handles
    the FK from sessions → users → purchases/alerts in one shot."""
    async with pool.acquire() as conn:
        await conn.execute(
            "TRUNCATE TABLE "
            "  alerts, purchases, sessions, magic_links, users, "
            "  bar_snapshots, coin_snapshots, spot_snapshots, report_archive "
            "RESTART IDENTITY CASCADE"
        )
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Per-test reset so a 429 in test A doesn't bleed into test B."""
    from app.main import _COINS_RATE_LIMITER
    _COINS_RATE_LIMITER.reset()
    yield
    _COINS_RATE_LIMITER.reset()


@pytest.fixture(autouse=True)
def mock_external(monkeypatch):
    """Stub every external IO call the app makes.

    - Resend: capture the raw magic-link token so tests can verify it.
    - yfinance + frankfurter (historical): return deterministic values so
      portfolio writes don't depend on Yahoo being up.
    - gold-api (live spot) + frankfurter (live FX): same.
    """
    captured: dict = {"magic_links": [], "alert_emails": []}

    async def _fake_send_magic_link(to_email: str, link_url: str) -> None:
        token = link_url.split("#auth=", 1)[-1] if "#auth=" in link_url else link_url
        captured["magic_links"].append({"to": to_email, "token": token, "url": link_url})

    async def _fake_send_alert_email(to_email: str, fires: list[dict]) -> None:
        captured["alert_emails"].append({"to": to_email, "fires": fires})

    monkeypatch.setattr(email_module, "send_magic_link", _fake_send_magic_link)
    monkeypatch.setattr(email_module, "send_alert_email", _fake_send_alert_email)
    # auth_session imports send_magic_link by name — patch that too.
    from app import auth_session as auth_module
    monkeypatch.setattr(auth_module, "send_magic_link", _fake_send_magic_link)

    # Portfolio writes call _fetch_historical_spot_dkk_per_g which hits
    # yfinance + frankfurter. Return a known good Decimal so create/edit
    # flows don't depend on Yahoo.
    async def _fake_historical_spot(metal, purchased_at):
        return Decimal("600.0000") if metal == "gold" else Decimal("8.0000")

    monkeypatch.setattr(
        portfolio_module, "_fetch_historical_spot_dkk_per_g", _fake_historical_spot,
    )

    # Live spot (current) — used by GET /portfolio for P&L decoration.
    async def _fake_current_spot():
        return {"gold": Decimal("650.0000"), "silver": Decimal("8.5000")}

    monkeypatch.setattr(portfolio_module, "_current_spot_dkk_per_g", _fake_current_spot)

    return captured


@pytest_asyncio.fixture(loop_scope="session")
async def client() -> AsyncIterator[AsyncClient]:
    """httpx async client wired to the ASGI app — no real network."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- Auth helpers ----------------------------------------------------------


async def make_session(pool, email: str = "user@example.com") -> tuple[UUID, str]:
    """Insert a user + session row directly. Returns (user_id, bearer_token).

    Bypasses the magic-link flow for tests that aren't exercising auth itself
    (the full flow is covered separately). The returned token is what the
    frontend would send as `Authorization: Bearer <token>`.
    """
    session_id = uuid4()
    async with pool.acquire() as conn:
        user_row = await conn.fetchrow(
            "INSERT INTO users (email) VALUES ($1) RETURNING id", email,
        )
        await conn.execute(
            "INSERT INTO sessions (id, user_id) VALUES ($1, $2)",
            session_id, user_row["id"],
        )
    return user_row["id"], str(session_id)


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Snapshot row helpers (for /alerts/preview, etc.) ----------------------


async def insert_bar_snapshot(
    pool, *, dealer: str, size_g: float, price_dkk: float,
    spot_gold_dkk_per_g: float = 600.0, status: str = "ok",
    fetched_at: datetime | None = None,
):
    fetched_at = fetched_at or datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO bar_snapshots (fetched_at, dealer, size_g, status, price_dkk, "
            "  brand, error, spot_gold_dkk_per_g) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
            fetched_at, dealer, Decimal(str(size_g)), status,
            Decimal(str(price_dkk)), "Brand", None,
            Decimal(str(spot_gold_dkk_per_g)),
        )


async def insert_coin_snapshot(
    pool, *, dealer: str, coin_type: str, size_label: str,
    gross_weight_g: float, purity: float, fine_gold_g: float,
    price_dkk: float, spot_gold_dkk_per_g: float = 600.0,
    status: str = "ok", fetched_at: datetime | None = None,
):
    fetched_at = fetched_at or datetime.now(UTC)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO coin_snapshots ("
            "  fetched_at, dealer, coin_type, size_label, gross_weight_g, "
            "  purity, fine_gold_g, status, price_dkk, error, "
            "  spot_gold_dkk_per_g, listing_url"
            ") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            fetched_at, dealer, coin_type, size_label,
            Decimal(str(gross_weight_g)), Decimal(str(purity)),
            Decimal(str(fine_gold_g)), status, Decimal(str(price_dkk)),
            None, Decimal(str(spot_gold_dkk_per_g)), None,
        )


# --- Shared sample inputs --------------------------------------------------


def sample_purchase_payload(**overrides) -> dict:
    base = {
        "metal": "gold",
        "gross_weight_g": "31.10",
        "purity": "0.9999",
        "price_paid_dkk": "20000.00",
        "purchased_at": (datetime.now(UTC) - timedelta(days=30))
            .replace(microsecond=0).isoformat(),
        "label": "Test 1oz bar",
        "dealer": "Tavex",
        "notes": None,
    }
    base.update(overrides)
    return base


__all__ = [
    "auth_headers",
    "insert_bar_snapshot",
    "insert_coin_snapshot",
    "make_session",
    "sample_purchase_payload",
    "date",  # convenience re-export
]
