"""Magic-link issue/verify, session management, and the require_session
dependency. Coexists with the existing X-API-Key auth — neither is required
to call the other's endpoints.

Token lifecycle:
  - Magic-link raw token = 256-bit secrets.token_urlsafe(32). Only leaves
    the server in the sign-in email. DB stores sha256(token).
  - Session token = UUIDv4 from sessions.id. Returned in the /auth/verify
    response body; the frontend stores it in localStorage and sends it as
    `Authorization: Bearer <token>` on every authenticated request.

We switched from cookie sessions to bearer tokens because Cloudflare Pages
(*.pages.dev) and Render (*.onrender.com) are different sites, and many
browsers (Safari ITP, Brave, Firefox ETP, Chrome with 3rd-party cookies
off) refuse to save SameSite=None cross-site cookies regardless of how
correctly the headers are set. Bearer-in-localStorage works everywhere.
"""
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr

from .db import get_pool
from .email import EmailSendError, send_magic_link

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_SLIDING_DAYS = 90
MAGIC_LINK_TTL_MIN = 15
RATE_LIMIT_PER_EMAIL = 3
RATE_LIMIT_PER_EMAIL_WINDOW_MIN = 10
RATE_LIMIT_PER_IP = 30
RATE_LIMIT_PER_IP_WINDOW_MIN = 60


@dataclass(frozen=True)
class AuthedUser:
    id: UUID
    email: str


class RequestLinkBody(BaseModel):
    email: EmailStr


class VerifyBody(BaseModel):
    token: str


def _hash_token(raw: str) -> bytes:
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def _check_rate_limit(conn: asyncpg.Connection, email: str, ip: str | None) -> None:
    now = datetime.now(UTC)
    email_window_start = now - timedelta(minutes=RATE_LIMIT_PER_EMAIL_WINDOW_MIN)
    email_count = await conn.fetchval(
        "SELECT count(*) FROM magic_links WHERE email = $1 AND created_at > $2",
        email, email_window_start,
    )
    if email_count >= RATE_LIMIT_PER_EMAIL:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many sign-in attempts; try again in a few minutes",
            headers={"Retry-After": str(RATE_LIMIT_PER_EMAIL_WINDOW_MIN * 60)},
        )
    if ip:
        ip_window_start = now - timedelta(minutes=RATE_LIMIT_PER_IP_WINDOW_MIN)
        ip_count = await conn.fetchval(
            "SELECT count(*) FROM magic_links WHERE created_ip = $1::inet AND created_at > $2",
            ip, ip_window_start,
        )
        if ip_count >= RATE_LIMIT_PER_IP:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many sign-in attempts from your network",
                headers={"Retry-After": str(RATE_LIMIT_PER_IP_WINDOW_MIN * 60)},
            )


def _build_magic_link_url(token: str) -> str:
    base = os.environ.get("MAGIC_LINK_BASE_URL", "").rstrip("/")
    if not base:
        raise HTTPException(status_code=500, detail="MAGIC_LINK_BASE_URL not configured")
    return f"{base}/#auth={token}"


@router.post("/request-link", status_code=status.HTTP_204_NO_CONTENT)
async def request_link(body: RequestLinkBody, request: Request) -> Response:
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    email = body.email.lower().strip()
    ip = _client_ip(request)
    async with pool.acquire() as conn:
        await _check_rate_limit(conn, email, ip)
        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(minutes=MAGIC_LINK_TTL_MIN)
        await conn.execute(
            "INSERT INTO magic_links (token_hash, email, expires_at, created_ip) "
            "VALUES ($1, $2, $3, $4::inet)",
            _hash_token(token), email, expires, ip,
        )
    try:
        await send_magic_link(email, _build_magic_link_url(token))
    except EmailSendError as e:
        logger.error("magic link email send failed: %s", e)
        raise HTTPException(status_code=500, detail="couldn't send email; try again") from e
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/verify")
async def verify(body: VerifyBody, request: Request) -> dict[str, str]:
    """Exchange a magic-link token for a session bearer token. The session
    token returned is the same uuid stored in `sessions.id`; the client
    sends it as `Authorization: Bearer <token>` on subsequent requests."""
    pool = await get_pool()
    if pool is None:
        raise HTTPException(status_code=503, detail="database not configured")
    token_hash = _hash_token(body.token)
    async with pool.acquire() as conn:
        async with conn.transaction():
            # No constant-time comparison needed here: the SHA-256 hash of a
            # 256-bit random token has no useful timing channel through a
            # B-tree index lookup, and the raw token is never stored.
            row = await conn.fetchrow(
                "SELECT id, email FROM magic_links "
                "WHERE token_hash = $1 AND used_at IS NULL AND expires_at > now() "
                "FOR UPDATE",
                token_hash,
            )
            if row is None:
                raise HTTPException(status_code=400, detail="invalid or expired link")
            await conn.execute(
                "UPDATE magic_links SET used_at = now() WHERE id = $1",
                row["id"],
            )
            user_row = await conn.fetchrow(
                "INSERT INTO users (email) VALUES ($1) "
                "ON CONFLICT (email) DO UPDATE SET last_login_at = now() "
                "RETURNING id, email",
                row["email"],
            )
            session_id = uuid4()
            user_agent = request.headers.get("user-agent", "")[:500]
            await conn.execute(
                "INSERT INTO sessions (id, user_id, user_agent) VALUES ($1, $2, $3)",
                session_id, user_row["id"], user_agent,
            )
    return {
        "user_id": str(user_row["id"]),
        "email": user_row["email"],
        "token": str(session_id),
    }


async def _resolve_session(session_id_str: str | None) -> AuthedUser | None:
    if not session_id_str:
        return None
    try:
        session_id = UUID(session_id_str)
    except ValueError:
        return None
    pool = await get_pool()
    if pool is None:
        return None
    cutoff = datetime.now(UTC) - timedelta(days=SESSION_SLIDING_DAYS)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT s.id, s.user_id, u.email "
            "FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.id = $1 AND s.last_seen_at > $2",
            session_id, cutoff,
        )
        if row is None:
            return None
        await conn.execute(
            "UPDATE sessions SET last_seen_at = now() WHERE id = $1",
            session_id,
        )
    return AuthedUser(id=row["user_id"], email=row["email"])


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


async def require_session(
    authorization: str | None = Header(default=None),
) -> AuthedUser:
    token = _extract_bearer(authorization)
    user = await _resolve_session(token)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(authorization: str | None = Header(default=None)) -> Response:
    token = _extract_bearer(authorization)
    if token:
        try:
            session_id = UUID(token)
            pool = await get_pool()
            if pool is not None:
                async with pool.acquire() as conn:
                    await conn.execute("DELETE FROM sessions WHERE id = $1", session_id)
        except ValueError:
            pass
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
async def me(user: AuthedUser = Depends(require_session)) -> dict[str, str]:
    return {"user_id": str(user.id), "email": user.email}
