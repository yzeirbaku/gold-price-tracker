"""Endpoint smoke tests using FastAPI's TestClient + a patched DB pool.

We mock the pool to avoid needing a real Postgres for these tests. The
storage CRUD has its own DB-backed tests; here we only verify routing,
auth, and content-disposition headers.
"""
import os
from datetime import date
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


def _client_with_api_key():
    os.environ["API_KEY"] = "test"
    return TestClient(app), {"X-API-Key": "test"}


def _fake_pool():
    fake_pool = AsyncMock()
    fake_conn = AsyncMock()

    class _Acquired:
        async def __aenter__(self):
            return fake_conn

        async def __aexit__(self, *a):
            return False

    fake_pool.acquire = lambda: _Acquired()
    return fake_pool


def test_list_reports_returns_array() -> None:
    client, headers = _client_with_api_key()
    with patch("app.main.get_pool", new=AsyncMock(return_value=_fake_pool())), \
         patch("app.main.list_reports",
               new=AsyncMock(return_value=[
                   {"id": 1, "type": "weekly",
                    "period_start": date(2026, 5, 4),
                    "period_end": date(2026, 5, 10),
                    "generated_at": "2026-05-11T00:30:00+00:00"},
               ])):
        r = client.get("/reports", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert body[0]["type"] == "weekly"


def test_fetch_report_returns_html_attachment() -> None:
    client, headers = _client_with_api_key()
    with patch("app.main.get_pool", new=AsyncMock(return_value=_fake_pool())), \
         patch("app.main.fetch_report_html",
               new=AsyncMock(return_value=(
                   "<html>body</html>", "weekly",
                   date(2026, 5, 4), date(2026, 5, 10),
               ))):
        r = client.get("/reports/1", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "attachment" in r.headers["content-disposition"]
    assert "weekly-2026-05-04.html" in r.headers["content-disposition"]
    assert r.text == "<html>body</html>"


def test_generate_on_demand_returns_html_without_persist() -> None:
    client, headers = _client_with_api_key()
    upsert_mock = AsyncMock()
    with patch("app.main.get_pool", new=AsyncMock(return_value=_fake_pool())), \
         patch("app.main.build_report",
               new=AsyncMock(return_value="<html>ondemand</html>")), \
         patch("app.main.upsert_report", new=upsert_mock):
        r = client.post("/reports/generate?range=week", headers=headers)
    assert r.status_code == 200
    assert r.text == "<html>ondemand</html>"
    upsert_mock.assert_not_called()


def test_cron_persists_to_archive() -> None:
    client, headers = _client_with_api_key()
    upsert_mock = AsyncMock(return_value=42)
    with patch("app.main.get_pool", new=AsyncMock(return_value=_fake_pool())), \
         patch("app.main.build_report",
               new=AsyncMock(return_value="<html>cron</html>")), \
         patch("app.main.upsert_report", new=upsert_mock):
        r = client.post("/reports/cron?type=weekly", headers=headers)
    assert r.status_code == 200
    upsert_mock.assert_called_once()
    body = r.json()
    assert body["id"] == 42
    assert body["type"] == "weekly"


def test_endpoints_require_api_key() -> None:
    client, _ = _client_with_api_key()
    assert client.get("/reports").status_code == 401
    assert client.post("/reports/generate?range=week").status_code == 401
    assert client.post("/reports/cron?type=weekly").status_code == 401
