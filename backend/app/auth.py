import os
import secrets

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server")
    if x_api_key is None or not _safe_compare(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _safe_compare(provided: str, expected: str) -> bool:
    # secrets.compare_digest raises TypeError if either string contains non-ASCII;
    # encode to bytes so any header value (including garbage) just fails the compare.
    try:
        return secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except Exception:
        return False
