# Gold Bar Price Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a $0/month personal tool that compares 2.5 g, 5 g, and 10 g gold-bar prices across 9 Danish online dealers and shows live spot prices for gold and silver, accessible from iPhone as a PWA.

**Architecture:** Three independent pieces — a static PWA on Cloudflare Pages, a FastAPI backend on Render's free web tier, and external services (metals.dev for spot, frankfurter.app for FX). On each request the backend fans out 11 concurrent HTTP calls (9 dealer scrapers + spot + FX) under a 12-second hard timeout and returns a sorted JSON response. Failures degrade soft — one bad dealer never breaks the whole response.

**Tech Stack:** Python 3.12, FastAPI, httpx (async HTTP), selectolax (HTML parser), pytest, vanilla JS (no build step), Render, Cloudflare Pages, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-05-07-gold-bar-tracker-design.md`

**Repo:** Personal GitHub account. Commit author identity must be `Yzeir Baku <yzeirbaku@hotmail.com>` — never the work email.

---

## File Structure

```
gold-bar-tracker/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                    # FastAPI app, routes, auth wiring
│   │   ├── orchestrator.py            # parallel fan-out, fail-soft
│   │   ├── models.py                  # Pydantic types
│   │   ├── auth.py                    # X-API-Key header check
│   │   ├── spot.py                    # metals.dev client
│   │   ├── fx.py                      # frankfurter.app client + static fallback
│   │   └── scrapers/
│   │       ├── __init__.py
│   │       ├── base.py                # DealerScraper protocol + helpers
│   │       ├── registry.py            # ordered list of all scrapers
│   │       ├── tavex.py
│   │       ├── vitusguld.py
│   │       ├── plaza.py
│   │       ├── nordiskguld.py
│   │       ├── seroguld.py
│   │       ├── nyfortuna.py
│   │       ├── silvergoldbull.py
│   │       ├── janjorgensen.py
│   │       └── monthuset.py
│   ├── tests/
│   │   ├── __init__.py
│   │   ├── fixtures/                  # saved HTML snapshots per dealer/size
│   │   ├── unit/
│   │   │   ├── __init__.py
│   │   │   ├── test_auth.py
│   │   │   ├── test_spot.py
│   │   │   ├── test_fx.py
│   │   │   ├── test_orchestrator.py
│   │   │   └── scrapers/
│   │   │       ├── __init__.py
│   │   │       ├── test_tavex.py
│   │   │       └── ... (one per dealer)
│   │   └── integration/
│   │       ├── __init__.py
│   │       └── test_live.py
│   ├── requirements.txt
│   ├── pyproject.toml                 # ruff + mypy config
│   └── runtime.txt                    # python version pin for Render
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   ├── manifest.webmanifest
│   ├── service-worker.js
│   └── icons/
│       ├── icon-192.png
│       └── icon-512.png
├── .github/workflows/
│   ├── tests.yml                      # unit tests + ruff + mypy on push
│   └── live-smoke.yml                 # weekly + manual live-site check
├── .gitignore
└── docs/superpowers/
    ├── specs/2026-05-07-gold-bar-tracker-design.md
    └── plans/2026-05-07-gold-bar-tracker.md
```

---

## Phases

- **Phase 1 (Tasks 1–11):** Backend skeleton + Tavex scraper end-to-end. Verifies the architecture before duplicating it.
- **Phase 2 (Tasks 12–19):** The remaining 8 scrapers, one task each.
- **Phase 3 (Tasks 20–25):** PWA frontend.
- **Phase 4 (Tasks 26–32):** Deploy + CI + on-phone test.

---

## Phase 1 — Backend skeleton + first scraper

### Task 1: Bootstrap repo

**Files:**
- Create: `.gitignore`
- Create: `backend/requirements.txt`
- Create: `backend/runtime.txt`
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py` (empty)
- Create: `backend/app/main.py`
- Create: `backend/tests/__init__.py` (empty)

- [ ] **Step 1: Create `.gitignore`**

```
# Python
__pycache__/
*.pyc
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db

# Env
.env
*.env.local

# Build artifacts
dist/
build/
*.egg-info/
```

- [ ] **Step 2: Create `backend/requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
httpx==0.27.2
selectolax==0.3.27
pydantic==2.9.2
```

- [ ] **Step 3: Create `backend/runtime.txt`** (Render reads this to pin the Python version)

```
python-3.12.7
```

- [ ] **Step 4: Create `backend/pyproject.toml`**

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
ignore = []

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 5: Create the empty package files**

```bash
touch backend/app/__init__.py backend/tests/__init__.py
```

- [ ] **Step 6: Create `backend/app/main.py` with a healthcheck**

```python
from fastapi import FastAPI

app = FastAPI(title="Gold Bar Price Tracker")


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "gold-bar-tracker"}
```

- [ ] **Step 7: Create a virtualenv and install**

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate    # Windows bash; on Linux/Mac use .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio ruff mypy
```

- [ ] **Step 8: Smoke-test the server**

```bash
cd backend
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/` in another terminal:

```bash
curl http://127.0.0.1:8000/
```

Expected: `{"status":"ok","service":"gold-bar-tracker"}`

Stop the server with Ctrl+C.

- [ ] **Step 9: Commit**

```bash
git add .gitignore backend/
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: bootstrap FastAPI backend with healthcheck"
```

---

### Task 2: Pydantic models

**Files:**
- Create: `backend/app/models.py`
- Test: (covered indirectly by later tasks)

- [ ] **Step 1: Create `backend/app/models.py`**

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, HttpUrl

ListingStatus = Literal["ok", "out_of_stock", "unavailable", "error"]


class PerCurrency(BaseModel):
    per_gram_eur: float
    per_gram_dkk: float


class SpotPrice(BaseModel):
    gold: PerCurrency
    silver: PerCurrency


class Listing(BaseModel):
    dealer: str
    status: ListingStatus
    price_dkk: float | None = None
    premium_pct: float | None = None
    in_stock: bool | None = None
    url: HttpUrl | None = None
    error: str | None = None
    fetched_at: datetime


class PriceResponse(BaseModel):
    size_g: float
    fetched_at: datetime
    spot: SpotPrice | None
    fx_stale: bool
    listings: list[Listing]
```

- [ ] **Step 2: Smoke-test the import**

```bash
cd backend
python -c "from app.models import PriceResponse, Listing, SpotPrice; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models.py
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: define Pydantic response models"
```

---

### Task 3: API key auth dependency

**Files:**
- Create: `backend/app/auth.py`
- Test: `backend/tests/unit/test_auth.py`

- [ ] **Step 1: Create the test directory**

```bash
mkdir -p backend/tests/unit
touch backend/tests/unit/__init__.py
```

- [ ] **Step 2: Write the failing test in `backend/tests/unit/test_auth.py`**

```python
import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.auth import require_api_key


def test_require_api_key_passes_when_header_matches_env() -> None:
    with patch.dict(os.environ, {"API_KEY": "secret"}):
        # Should not raise
        require_api_key(x_api_key="secret")


def test_require_api_key_rejects_missing_header() -> None:
    with patch.dict(os.environ, {"API_KEY": "secret"}):
        with pytest.raises(HTTPException) as exc:
            require_api_key(x_api_key=None)
        assert exc.value.status_code == 401


def test_require_api_key_rejects_wrong_header() -> None:
    with patch.dict(os.environ, {"API_KEY": "secret"}):
        with pytest.raises(HTTPException) as exc:
            require_api_key(x_api_key="wrong")
        assert exc.value.status_code == 401


def test_require_api_key_fails_closed_when_env_missing() -> None:
    # If API_KEY isn't set, every request must fail (don't allow open access)
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(HTTPException) as exc:
            require_api_key(x_api_key="anything")
        assert exc.value.status_code == 500
```

- [ ] **Step 3: Run the test — expected to fail**

```bash
cd backend
pytest tests/unit/test_auth.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.auth'`

- [ ] **Step 4: Implement `backend/app/auth.py`**

```python
import os
import secrets

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("API_KEY")
    if not expected:
        raise HTTPException(status_code=500, detail="API_KEY not configured on server")
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

- [ ] **Step 5: Run the test — expected to pass**

```bash
pytest tests/unit/test_auth.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/auth.py backend/tests/unit/test_auth.py backend/tests/unit/__init__.py
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: add X-API-Key auth dependency"
```

---

### Task 4: Spot price client (metals.dev)

**Files:**
- Create: `backend/app/spot.py`
- Test: `backend/tests/unit/test_spot.py`

**Background:** metals.dev returns gold/silver in USD per troy ounce. We convert to per-gram (1 troy oz = 31.1034768 g). Sign up at https://metals.dev for a free API key; set as env var `METALS_DEV_API_KEY`.

- [ ] **Step 1: Write the failing test in `backend/tests/unit/test_spot.py`**

```python
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.spot import OUNCE_TO_GRAM, fetch_spot_usd_per_gram


@pytest.mark.asyncio
async def test_fetch_spot_usd_per_gram_converts_oz_to_gram() -> None:
    fake_payload = {
        "metals": {"gold": 2400.0, "silver": 30.0},
    }
    mock_response = AsyncMock()
    mock_response.json = lambda: fake_payload
    mock_response.raise_for_status = lambda: None

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    with patch.dict("os.environ", {"METALS_DEV_API_KEY": "test-key"}):
        result = await fetch_spot_usd_per_gram(mock_client)

    assert result is not None
    assert result["gold"] == pytest.approx(2400.0 / OUNCE_TO_GRAM)
    assert result["silver"] == pytest.approx(30.0 / OUNCE_TO_GRAM)


@pytest.mark.asyncio
async def test_fetch_spot_returns_none_on_http_error() -> None:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.HTTPError("boom")

    with patch.dict("os.environ", {"METALS_DEV_API_KEY": "test-key"}):
        result = await fetch_spot_usd_per_gram(mock_client)

    assert result is None
```

- [ ] **Step 2: Run the test — expected to fail**

```bash
cd backend
pytest tests/unit/test_spot.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.spot'`

- [ ] **Step 3: Implement `backend/app/spot.py`**

```python
import logging
import os

import httpx

logger = logging.getLogger(__name__)

OUNCE_TO_GRAM = 31.1034768
METALS_DEV_URL = "https://api.metals.dev/v1/latest"


async def fetch_spot_usd_per_gram(client: httpx.AsyncClient) -> dict[str, float] | None:
    """Return {'gold': USD/g, 'silver': USD/g} or None on failure."""
    api_key = os.environ.get("METALS_DEV_API_KEY")
    if not api_key:
        logger.warning("METALS_DEV_API_KEY not set; skipping spot fetch")
        return None
    try:
        resp = await client.get(
            METALS_DEV_URL,
            params={"api_key": api_key, "currency": "USD", "unit": "toz"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        metals = data["metals"]
        return {
            "gold": float(metals["gold"]) / OUNCE_TO_GRAM,
            "silver": float(metals["silver"]) / OUNCE_TO_GRAM,
        }
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.exception("spot fetch failed: %s", e)
        return None
```

- [ ] **Step 4: Run the test — expected to pass**

```bash
pytest tests/unit/test_spot.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/spot.py backend/tests/unit/test_spot.py
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: add metals.dev spot price client"
```

---

### Task 5: FX client (frankfurter.app) + static fallback

**Files:**
- Create: `backend/app/fx.py`
- Test: `backend/tests/unit/test_fx.py`

- [ ] **Step 1: Write the failing test in `backend/tests/unit/test_fx.py`**

```python
from unittest.mock import AsyncMock

import httpx
import pytest

from app.fx import STATIC_FALLBACK, fetch_usd_to


@pytest.mark.asyncio
async def test_fetch_usd_to_returns_live_rates() -> None:
    fake_payload = {"rates": {"EUR": 0.92, "DKK": 6.85}}
    mock_response = AsyncMock()
    mock_response.json = lambda: fake_payload
    mock_response.raise_for_status = lambda: None
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    rates, stale = await fetch_usd_to(mock_client)

    assert rates == {"EUR": 0.92, "DKK": 6.85}
    assert stale is False


@pytest.mark.asyncio
async def test_fetch_usd_to_falls_back_on_error() -> None:
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = httpx.HTTPError("boom")

    rates, stale = await fetch_usd_to(mock_client)

    assert rates == STATIC_FALLBACK
    assert stale is True
```

- [ ] **Step 2: Run the test — expected to fail**

```bash
pytest tests/unit/test_fx.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.fx'`

- [ ] **Step 3: Implement `backend/app/fx.py`**

```python
import logging

import httpx

logger = logging.getLogger(__name__)

# Stamped fallback used when frankfurter.app is unreachable.
# Refresh quarterly. USD/EUR drifts; USD/DKK is pegged to EUR so moves with it.
# Last refreshed: 2026-05-07
STATIC_FALLBACK: dict[str, float] = {"EUR": 0.92, "DKK": 6.85}

FRANKFURTER_URL = "https://api.frankfurter.app/latest"


async def fetch_usd_to(client: httpx.AsyncClient) -> tuple[dict[str, float], bool]:
    """Return ({'EUR': rate, 'DKK': rate}, stale_flag)."""
    try:
        resp = await client.get(
            FRANKFURTER_URL,
            params={"from": "USD", "to": "EUR,DKK"},
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
        rates = data["rates"]
        return {"EUR": float(rates["EUR"]), "DKK": float(rates["DKK"])}, False
    except (httpx.HTTPError, KeyError, ValueError) as e:
        logger.warning("FX fetch failed; using static fallback: %s", e)
        return STATIC_FALLBACK.copy(), True
```

- [ ] **Step 4: Run the test — expected to pass**

```bash
pytest tests/unit/test_fx.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/fx.py backend/tests/unit/test_fx.py
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: add frankfurter.app FX client with static fallback"
```

---

### Task 6: Scraper protocol + base helpers

**Files:**
- Create: `backend/app/scrapers/__init__.py` (empty)
- Create: `backend/app/scrapers/base.py`
- Create: `backend/app/scrapers/registry.py`
- Test: (no direct tests — proven by Task 7)

- [ ] **Step 1: Create the package**

```bash
mkdir -p backend/app/scrapers
touch backend/app/scrapers/__init__.py
```

- [ ] **Step 2: Create `backend/app/scrapers/base.py`**

```python
from datetime import UTC, datetime
from typing import Protocol

import httpx
from selectolax.parser import HTMLParser

from app.models import Listing


class DealerScraper(Protocol):
    name: str
    base_url: str

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None: ...


def now_utc() -> datetime:
    return datetime.now(UTC)


def parse_dkk_price(text: str) -> float | None:
    """Extract a DKK price from text like '2.940,00 kr.' or '2940 DKK' or '2,940.00 kr'.

    Danish formatting uses '.' as thousand separator and ',' as decimal.
    Returns None if no number can be extracted.
    """
    import re

    # Strip currency markers and whitespace
    cleaned = re.sub(r"[^\d.,]", "", text)
    if not cleaned:
        return None

    # Heuristic: if both '.' and ',' appear and ',' is later, treat ',' as decimal
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        # Treat ',' as decimal if it has 1-2 digits after it; else thousand sep
        parts = cleaned.split(",")
        if len(parts[-1]) in (1, 2):
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def make_html_parser(html: str) -> HTMLParser:
    return HTMLParser(html)
```

- [ ] **Step 3: Create `backend/app/scrapers/registry.py`** (will be populated as scrapers are added)

```python
from app.scrapers.base import DealerScraper

ALL_SCRAPERS: list[DealerScraper] = []
```

- [ ] **Step 4: Smoke-test the imports**

```bash
cd backend
python -c "from app.scrapers.base import DealerScraper, parse_dkk_price; print(parse_dkk_price('2.940,00 kr.'))"
```

Expected: `2940.0`

- [ ] **Step 5: Commit**

```bash
git add backend/app/scrapers/
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: scraper protocol and DKK price parser"
```

---

### Task 7: Tavex scraper (full template)

**Files:**
- Create: `backend/tests/fixtures/tavex_5g.html`
- Create: `backend/app/scrapers/tavex.py`
- Test: `backend/tests/unit/scrapers/test_tavex.py`
- Modify: `backend/app/scrapers/registry.py`

- [ ] **Step 1: Save a real HTML fixture**

```bash
mkdir -p backend/tests/fixtures
mkdir -p backend/tests/unit/scrapers
touch backend/tests/unit/scrapers/__init__.py
```

Open https://tavex.dk/guld/guldbarrer/ in a browser. Find the listing for a 5 g gold bar. Right-click → "Save as" → save to `backend/tests/fixtures/tavex_5g.html` (Webpage, HTML only). Alternatively from the command line:

```bash
curl -A "Mozilla/5.0" "https://tavex.dk/guld/guldbarrer/" -o backend/tests/fixtures/tavex_5g.html
```

Inspect the saved file in a text editor and find:
- The element containing the price for the 5 g bar (note the CSS selector — class names, data attributes)
- The element indicating in-stock vs sold-out (note the selector and the strings used)
- The product detail URL for the 5 g bar

Record those selectors — you'll use them in Step 4.

- [ ] **Step 2: Write the failing test in `backend/tests/unit/scrapers/test_tavex.py`**

Replace `EXPECTED_PRICE_DKK` with the price you read from the saved HTML, and `EXPECTED_URL_FRAGMENT` with a substring from the product link.

```python
from pathlib import Path

from app.scrapers.tavex import TavexScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "tavex_5g.html"

# REPLACE these two values with what you observe in the saved fixture:
EXPECTED_PRICE_DKK = 2940.0          # update from the saved HTML
EXPECTED_URL_FRAGMENT = "5-gram"     # update from the saved HTML


def test_tavex_parses_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = TavexScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_PRICE_DKK
    assert listing.in_stock is True
    assert listing.url is not None and EXPECTED_URL_FRAGMENT in str(listing.url)


def test_tavex_returns_none_for_unknown_size() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = TavexScraper().parse(html, size_g=1234.0)
    assert listing is None
```

- [ ] **Step 3: Run the test — expected to fail**

```bash
cd backend
pytest tests/unit/scrapers/test_tavex.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.scrapers.tavex'`

- [ ] **Step 4: Implement `backend/app/scrapers/tavex.py`**

The exact CSS selectors below are placeholders — replace them with what you identified in Step 1. The structure of the parser stays the same regardless of dealer.

```python
import logging

import httpx
from selectolax.parser import HTMLParser

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class TavexScraper:
    name = "Tavex"
    base_url = "https://tavex.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = "https://tavex.dk/guld/guldbarrer/"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("Tavex fetch failed: %s", e)
            return Listing(
                dealer=self.name, status="error",
                error=f"http: {e.__class__.__name__}", fetched_at=now_utc(),
            )
        return self.parse(resp.text, size_g)

    def parse(self, html: str, size_g: float) -> Listing | None:
        tree = make_html_parser(html)
        product = self._find_product_for_size(tree, size_g)
        if product is None:
            return None

        # Replace these selectors with what you observed in the saved fixture.
        price_node = product.css_first(".product-price")
        link_node = product.css_first("a.product-link")
        sold_out_node = product.css_first(".sold-out-badge")

        if price_node is None or link_node is None:
            return Listing(
                dealer=self.name, status="error",
                error="parse_failed: missing price/link node", fetched_at=now_utc(),
            )
        price = parse_dkk_price(price_node.text(strip=True))
        if price is None:
            return Listing(
                dealer=self.name, status="unavailable",
                error="non-numeric price text", fetched_at=now_utc(),
            )
        in_stock = sold_out_node is None
        href = link_node.attributes.get("href", "")
        url = href if href.startswith("http") else f"{self.base_url}{href}"

        return Listing(
            dealer=self.name,
            status="ok" if in_stock else "out_of_stock",
            price_dkk=price,
            in_stock=in_stock,
            url=url,
            fetched_at=now_utc(),
        )

    def _find_product_for_size(self, tree: HTMLParser, size_g: float) -> object:
        # Replace this with the real lookup once you've inspected the HTML.
        # Approach: iterate product cards, match the one whose title/heading
        # contains the size in grams (e.g. "5 gram", "5 g", "5g").
        for card in tree.css(".product-card"):
            title = card.css_first(".product-title")
            if title is None:
                continue
            text = title.text(strip=True).lower().replace(" ", "")
            if f"{int(size_g) if size_g.is_integer() else size_g}gram" in text \
               or f"{int(size_g) if size_g.is_integer() else size_g}g" in text:
                return card
        return None
```

- [ ] **Step 5: Run the test — iterate selectors until pass**

```bash
pytest tests/unit/scrapers/test_tavex.py -v
```

If selectors are wrong, the test will fail with `parse_failed` or assert mismatches. Open the fixture in a browser/editor and adjust selectors in `tavex.py` until both tests pass. Expected on success: 2 passed.

- [ ] **Step 6: Register Tavex in `backend/app/scrapers/registry.py`**

```python
from app.scrapers.base import DealerScraper
from app.scrapers.tavex import TavexScraper

ALL_SCRAPERS: list[DealerScraper] = [
    TavexScraper(),
]
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/scrapers/tavex.py backend/app/scrapers/registry.py \
        backend/tests/unit/scrapers/__init__.py backend/tests/unit/scrapers/test_tavex.py \
        backend/tests/fixtures/tavex_5g.html
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat(scraper): tavex 5g bar"
```

---

### Task 8: Orchestrator

**Files:**
- Create: `backend/app/orchestrator.py`
- Test: `backend/tests/unit/test_orchestrator.py`

- [ ] **Step 1: Write the failing test in `backend/tests/unit/test_orchestrator.py`**

```python
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Listing
from app.orchestrator import run


class FakeScraper:
    def __init__(self, name: str, price: float | None) -> None:
        self.name = name
        self.base_url = "https://example.com"
        self._price = price

    async def fetch(self, size_g: float, client) -> Listing | None:
        from app.scrapers.base import now_utc
        if self._price is None:
            raise RuntimeError("simulated boom")
        return Listing(
            dealer=self.name, status="ok",
            price_dkk=self._price, in_stock=True,
            url="https://example.com/x", fetched_at=now_utc(),
        )


@pytest.mark.asyncio
async def test_run_sorts_listings_cheapest_first() -> None:
    scrapers = [FakeScraper("B", 3000.0), FakeScraper("A", 2500.0)]
    spot = AsyncMock(return_value={"gold": 70.0, "silver": 1.0})
    fx = AsyncMock(return_value=({"EUR": 0.92, "DKK": 6.85}, False))

    with patch("app.orchestrator.ALL_SCRAPERS", scrapers), \
         patch("app.orchestrator.fetch_spot_usd_per_gram", spot), \
         patch("app.orchestrator.fetch_usd_to", fx):
        resp = await run(size_g=5.0)

    prices = [li.price_dkk for li in resp.listings if li.status == "ok"]
    assert prices == sorted(prices)


@pytest.mark.asyncio
async def test_run_keeps_response_when_one_scraper_throws() -> None:
    scrapers = [FakeScraper("Good", 2500.0), FakeScraper("Bad", None)]
    spot = AsyncMock(return_value={"gold": 70.0, "silver": 1.0})
    fx = AsyncMock(return_value=({"EUR": 0.92, "DKK": 6.85}, False))

    with patch("app.orchestrator.ALL_SCRAPERS", scrapers), \
         patch("app.orchestrator.fetch_spot_usd_per_gram", spot), \
         patch("app.orchestrator.fetch_usd_to", fx):
        resp = await run(size_g=5.0)

    statuses = {li.dealer: li.status for li in resp.listings}
    assert statuses["Good"] == "ok"
    assert statuses["Bad"] == "error"
    assert isinstance(resp.fetched_at, datetime)
```

- [ ] **Step 2: Run the test — expected to fail**

```bash
pytest tests/unit/test_orchestrator.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.orchestrator'`

- [ ] **Step 3: Implement `backend/app/orchestrator.py`**

```python
import asyncio
import logging

import httpx

from app.fx import fetch_usd_to
from app.models import Listing, PerCurrency, PriceResponse, SpotPrice
from app.scrapers.base import now_utc
from app.scrapers.registry import ALL_SCRAPERS
from app.spot import fetch_spot_usd_per_gram

logger = logging.getLogger(__name__)


async def _safe_fetch(scraper, size_g: float, client: httpx.AsyncClient) -> Listing:
    try:
        result = await scraper.fetch(size_g, client)
        if result is None:
            return Listing(
                dealer=scraper.name, status="unavailable",
                error="size not offered", fetched_at=now_utc(),
            )
        return result
    except Exception as e:
        logger.exception("scraper %s threw: %s", scraper.name, e)
        return Listing(
            dealer=scraper.name, status="error",
            error=f"{e.__class__.__name__}: {e}", fetched_at=now_utc(),
        )


def _sort_key(li: Listing) -> tuple[int, float]:
    # ok first (sorted by price asc), then everything else
    if li.status == "ok" and li.price_dkk is not None:
        return (0, li.price_dkk)
    return (1, float("inf"))


async def run(size_g: float) -> PriceResponse:
    async with httpx.AsyncClient() as client:
        scraper_tasks = [_safe_fetch(s, size_g, client) for s in ALL_SCRAPERS]
        spot_task = fetch_spot_usd_per_gram(client)
        fx_task = fetch_usd_to(client)

        results = await asyncio.wait_for(
            asyncio.gather(*scraper_tasks, spot_task, fx_task, return_exceptions=False),
            timeout=12.0,
        )

    listings: list[Listing] = list(results[: len(scraper_tasks)])
    spot_per_g_usd = results[len(scraper_tasks)]
    fx_rates, fx_stale = results[len(scraper_tasks) + 1]

    spot: SpotPrice | None = None
    if spot_per_g_usd is not None:
        spot = SpotPrice(
            gold=PerCurrency(
                per_gram_eur=round(spot_per_g_usd["gold"] * fx_rates["EUR"], 2),
                per_gram_dkk=round(spot_per_g_usd["gold"] * fx_rates["DKK"], 2),
            ),
            silver=PerCurrency(
                per_gram_eur=round(spot_per_g_usd["silver"] * fx_rates["EUR"], 4),
                per_gram_dkk=round(spot_per_g_usd["silver"] * fx_rates["DKK"], 4),
            ),
        )

    if spot is not None:
        ref_dkk_per_g = spot.gold.per_gram_dkk
        for li in listings:
            if li.status == "ok" and li.price_dkk is not None and ref_dkk_per_g > 0:
                ref_total = ref_dkk_per_g * size_g
                li.premium_pct = round((li.price_dkk - ref_total) / ref_total * 100, 2)

    listings.sort(key=_sort_key)

    return PriceResponse(
        size_g=size_g,
        fetched_at=now_utc(),
        spot=spot,
        fx_stale=fx_stale,
        listings=listings,
    )
```

- [ ] **Step 4: Run the test — expected to pass**

```bash
pytest tests/unit/test_orchestrator.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/orchestrator.py backend/tests/unit/test_orchestrator.py
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: parallel orchestrator with fail-soft per scraper"
```

---

### Task 9: GET /prices/{size} endpoint

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Replace `backend/app/main.py` contents**

```python
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.auth import require_api_key
from app.models import PriceResponse
from app.orchestrator import run

app = FastAPI(title="Gold Bar Price Tracker")

# CORS — only the deployed Cloudflare Pages frontend may call us.
# (Set FRONTEND_ORIGIN env var on Render to your *.pages.dev URL.)
import os
_origin = os.environ.get("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_origin],
    allow_methods=["GET"],
    allow_headers=["X-API-Key", "Content-Type"],
)

ALLOWED_SIZES = {2.5, 5.0, 10.0}


@app.get("/")
async def root() -> dict[str, str]:
    return {"status": "ok", "service": "gold-bar-tracker"}


@app.get("/prices/{size}", response_model=PriceResponse)
async def get_prices(size: float, _: None = Depends(require_api_key)) -> PriceResponse:
    if size not in ALLOWED_SIZES:
        raise HTTPException(status_code=400, detail=f"size must be one of {sorted(ALLOWED_SIZES)}")
    return await run(size_g=size)
```

- [ ] **Step 2: Smoke-test locally**

```bash
cd backend
API_KEY=test METALS_DEV_API_KEY=<your_real_key> uvicorn app.main:app --reload
```

In another terminal:

```bash
# Auth check
curl -i http://127.0.0.1:8000/prices/5
# Expected: 401 Unauthorized

# Bad size
curl -i -H "X-API-Key: test" http://127.0.0.1:8000/prices/3
# Expected: 400 with detail

# Real call
curl -H "X-API-Key: test" http://127.0.0.1:8000/prices/5 | python -m json.tool
# Expected: JSON with at least Tavex listing and (if API key valid) spot prices
```

Stop the server.

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: GET /prices/{size} endpoint with auth and CORS"
```

---

### Task 10: GET /health endpoint

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Append the `/health` route to `backend/app/main.py`** (above the existing `get_prices` block — order doesn't matter; below the imports is fine)

```python
import asyncio

import httpx

from app.scrapers.registry import ALL_SCRAPERS


@app.get("/health")
async def health(_: None = Depends(require_api_key)) -> dict[str, object]:
    """Run all scrapers against 5g and return per-dealer pass/fail summary."""
    async def _check(s) -> dict[str, object]:
        try:
            async with httpx.AsyncClient() as client:
                listing = await s.fetch(5.0, client)
            return {
                "dealer": s.name,
                "ok": listing is not None and listing.status == "ok",
                "status": listing.status if listing else "no_listing",
            }
        except Exception as e:
            return {"dealer": s.name, "ok": False, "status": "exception", "error": str(e)}

    results = await asyncio.gather(*[_check(s) for s in ALL_SCRAPERS])
    return {"ok": all(r["ok"] for r in results), "scrapers": results}
```

- [ ] **Step 2: Smoke-test**

```bash
cd backend
API_KEY=test METALS_DEV_API_KEY=<your_real_key> uvicorn app.main:app --reload
```

```bash
curl -H "X-API-Key: test" http://127.0.0.1:8000/health | python -m json.tool
```

Expected: JSON with `"scrapers": [{"dealer": "Tavex", "ok": true, ...}]`. Stop the server.

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat: /health endpoint with per-scraper status"
```

---

### Task 11: Local end-to-end smoke

**Files:** none

- [ ] **Step 1: Run all unit tests**

```bash
cd backend
pytest tests/unit/ -v
```

Expected: all green (auth, spot, fx, orchestrator, tavex).

- [ ] **Step 2: Confirm `ruff` and `mypy` pass**

```bash
ruff check app tests
mypy app
```

Fix any reported issues. Both must report no errors before proceeding.

- [ ] **Step 3: Run the live server one more time and hit /prices/5, /prices/2.5, /prices/10**

```bash
API_KEY=test METALS_DEV_API_KEY=<key> uvicorn app.main:app --reload
```

In another terminal:

```bash
for size in 2.5 5 10; do
  echo "=== size=$size ==="
  curl -s -H "X-API-Key: test" http://127.0.0.1:8000/prices/$size | python -m json.tool
done
```

Confirm: spot prices populate (gold and silver in EUR + DKK), Tavex listing appears, sort order makes sense.

- [ ] **Step 4: No commit needed (no file changes); proceed to Phase 2.**

---

## Phase 2 — Remaining 8 scrapers

**Procedure for every task in Phase 2 (Tasks 12–19):**

1. Save a fresh HTML fixture from the dealer's listing page.
2. Inspect it to identify the price element, in-stock indicator, and product link for the 2.5 g, 5 g, and 10 g bars (where offered).
3. Write parser tests using the saved fixture.
4. Implement the scraper module.
5. Iterate selectors until tests pass.
6. Register in `app/scrapers/registry.py`.
7. Commit.

**Code template for Tasks 13–19:** Task 12 (Vitus Guld) below contains the **complete, canonical** scraper module + test file. For each subsequent dealer, copy `backend/app/scrapers/vitusguld.py` and `backend/tests/unit/scrapers/test_vitusguld.py` to the new dealer's filename, then apply the substitutions listed in the per-task block (class name, dealer name, base URL, fetch URL). The per-dealer parser body still requires inspecting the saved HTML to derive correct selectors — only the boilerplate is identical.

If you are an agent executing one of Tasks 13–19 in isolation, the full template module and test files are in **Task 12** above; do not improvise the boilerplate.

### Task 12: Vitus Guld

**Listing URL:** https://vitusguld.dk/produkt-kategori/guldbarre-guldmoenter-guldsmykker/guldbarre/

- [ ] **Step 1: Save fixture**

```bash
curl -A "Mozilla/5.0" "https://vitusguld.dk/produkt-kategori/guldbarre-guldmoenter-guldsmykker/guldbarre/" \
  -o backend/tests/fixtures/vitusguld_listing.html
```

Inspect to find price/stock/link selectors for the 2.5 g, 5 g, 10 g bars.

- [ ] **Step 2: Write the failing test in `backend/tests/unit/scrapers/test_vitusguld.py`**

```python
from pathlib import Path

from app.scrapers.vitusguld import VitusGuldScraper

FIXTURE = Path(__file__).parent.parent.parent / "fixtures" / "vitusguld_listing.html"

EXPECTED_5G_PRICE_DKK: float = 0.0  # FILL IN from the saved HTML
EXPECTED_5G_URL_FRAGMENT: str = ""  # FILL IN from the saved HTML


def test_vitusguld_parses_5g_bar() -> None:
    html = FIXTURE.read_text(encoding="utf-8")
    listing = VitusGuldScraper().parse(html, size_g=5.0)
    assert listing is not None
    assert listing.status == "ok"
    assert listing.price_dkk == EXPECTED_5G_PRICE_DKK
    assert EXPECTED_5G_URL_FRAGMENT in str(listing.url)
```

- [ ] **Step 3: Run — expected to fail with ModuleNotFoundError**

```bash
pytest tests/unit/scrapers/test_vitusguld.py -v
```

- [ ] **Step 4: Implement `backend/app/scrapers/vitusguld.py`** (use the Tavex template; replace selectors)

```python
import logging

import httpx

from app.models import Listing
from app.scrapers.base import make_html_parser, now_utc, parse_dkk_price

logger = logging.getLogger(__name__)


class VitusGuldScraper:
    name = "Vitus Guld"
    base_url = "https://vitusguld.dk"

    async def fetch(self, size_g: float, client: httpx.AsyncClient) -> Listing | None:
        url = f"{self.base_url}/produkt-kategori/guldbarre-guldmoenter-guldsmykker/guldbarre/"
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return Listing(
                dealer=self.name, status="error",
                error=f"http: {e.__class__.__name__}", fetched_at=now_utc(),
            )
        return self.parse(resp.text, size_g)

    def parse(self, html: str, size_g: float) -> Listing | None:
        tree = make_html_parser(html)
        # REPLACE the selectors below with what you observed in the fixture.
        for card in tree.css(".product"):
            title = card.css_first(".product-title")
            if title is None:
                continue
            text = title.text(strip=True).lower().replace(" ", "")
            wanted = f"{size_g:g}gram" if size_g != int(size_g) else f"{int(size_g)}gram"
            if wanted not in text:
                continue
            price_node = card.css_first(".price")
            link_node = card.css_first("a")
            sold_out = card.css_first(".out-of-stock")
            if price_node is None or link_node is None:
                return Listing(
                    dealer=self.name, status="error",
                    error="parse_failed", fetched_at=now_utc(),
                )
            price = parse_dkk_price(price_node.text(strip=True))
            if price is None:
                return Listing(dealer=self.name, status="unavailable",
                               error="non-numeric price", fetched_at=now_utc())
            in_stock = sold_out is None
            href = link_node.attributes.get("href", "")
            return Listing(
                dealer=self.name,
                status="ok" if in_stock else "out_of_stock",
                price_dkk=price, in_stock=in_stock,
                url=href if href.startswith("http") else f"{self.base_url}{href}",
                fetched_at=now_utc(),
            )
        return None
```

- [ ] **Step 5: Iterate selectors and re-run until pass**

```bash
pytest tests/unit/scrapers/test_vitusguld.py -v
```

- [ ] **Step 6: Register in `backend/app/scrapers/registry.py`**

```python
from app.scrapers.base import DealerScraper
from app.scrapers.tavex import TavexScraper
from app.scrapers.vitusguld import VitusGuldScraper

ALL_SCRAPERS: list[DealerScraper] = [
    TavexScraper(),
    VitusGuldScraper(),
]
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/scrapers/vitusguld.py backend/app/scrapers/registry.py \
        backend/tests/unit/scrapers/test_vitusguld.py backend/tests/fixtures/vitusguld_listing.html
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat(scraper): vitus guld"
```

---

### Task 13: Plaza

**Listing URL:** https://plaza.dk/collections/guldbarre

Repeat the Task 12 pattern with these substitutions:
- Class name: `PlazaScraper`
- Module: `backend/app/scrapers/plaza.py`
- Test: `backend/tests/unit/scrapers/test_plaza.py`
- Fixture: `backend/tests/fixtures/plaza_listing.html`
- `name = "Plaza"`, `base_url = "https://plaza.dk"`, fetch URL `/collections/guldbarre`
- Register in `registry.py` after VitusGuld
- Commit message: `feat(scraper): plaza`

The full implementation template is identical to Task 12 — replace `vitusguld` with `plaza`, `VitusGuld` with `Plaza`, the URL, and inspect selectors against the saved fixture. Use the same TDD loop: save fixture → write failing test → implement → iterate → register → commit.

---

### Task 14: Nordisk Guld

**Listing URL:** https://nordiskguld.dk/shop/guld/guldbarre/

- Class: `NordiskGuldScraper`
- Module: `backend/app/scrapers/nordiskguld.py`
- Test: `backend/tests/unit/scrapers/test_nordiskguld.py`
- Fixture: `backend/tests/fixtures/nordiskguld_listing.html`
- `name = "Nordisk Guld"`, `base_url = "https://nordiskguld.dk"`, fetch URL `/shop/guld/guldbarre/`
- Commit: `feat(scraper): nordisk guld`

Same TDD loop as Tasks 12–13.

---

### Task 15: Sero Guld

**Listing URL:** https://seroguld.dk/shop/guld/

- Class: `SeroGuldScraper`
- Module: `backend/app/scrapers/seroguld.py`
- Test: `backend/tests/unit/scrapers/test_seroguld.py`
- Fixture: `backend/tests/fixtures/seroguld_listing.html`
- `name = "Sero Guld"`, `base_url = "https://seroguld.dk"`, fetch URL `/shop/guld/`
- Commit: `feat(scraper): sero guld`

Same TDD loop.

---

### Task 16: Nyfortuna

**Listing URL:** https://nyfortuna.dk/butik/guld-salg/

- Class: `NyfortunaScraper`
- Module: `backend/app/scrapers/nyfortuna.py`
- Test: `backend/tests/unit/scrapers/test_nyfortuna.py`
- Fixture: `backend/tests/fixtures/nyfortuna_listing.html`
- `name = "Nyfortuna"`, `base_url = "https://nyfortuna.dk"`, fetch URL `/butik/guld-salg/`
- Commit: `feat(scraper): nyfortuna`

Same TDD loop.

---

### Task 17: Silver Gold Bull DK

**Listing URL:** https://silvergoldbull.dk/gold-bars

- Class: `SilverGoldBullScraper`
- Module: `backend/app/scrapers/silvergoldbull.py`
- Test: `backend/tests/unit/scrapers/test_silvergoldbull.py`
- Fixture: `backend/tests/fixtures/silvergoldbull_listing.html`
- `name = "Silver Gold Bull"`, `base_url = "https://silvergoldbull.dk"`, fetch URL `/gold-bars`
- Commit: `feat(scraper): silver gold bull`

Same TDD loop. **Note:** if the page is JavaScript-rendered (price absent from raw HTML), this is the trigger to revisit the design — escalate to Playwright for this one site only and add `playwright` to `requirements.txt`. Don't introduce Playwright preemptively.

---

### Task 18: Jan Jørgensen Smykker

**Listing URL:** https://janjorgensensmykker.dk/smykker/investeringsguld

- Class: `JanJorgensenScraper`
- Module: `backend/app/scrapers/janjorgensen.py`
- Test: `backend/tests/unit/scrapers/test_janjorgensen.py`
- Fixture: `backend/tests/fixtures/janjorgensen_listing.html`
- `name = "Jan Jørgensen"`, `base_url = "https://janjorgensensmykker.dk"`, fetch URL `/smykker/investeringsguld`
- Commit: `feat(scraper): jan jorgensen`

Same TDD loop. **Note:** site advertises "live world market price updated every 2 minutes" — likely JS-rendered. Same Playwright-escalation guidance as Task 17 if needed.

---

### Task 19: Mønthuset

**Listing URL:** https://www.monthuset.dk/guld/guldbarrer

- Class: `MonthusetScraper`
- Module: `backend/app/scrapers/monthuset.py`
- Test: `backend/tests/unit/scrapers/test_monthuset.py`
- Fixture: `backend/tests/fixtures/monthuset_listing.html`
- `name = "Mønthuset"`, `base_url = "https://www.monthuset.dk"`, fetch URL `/guld/guldbarrer`
- Commit: `feat(scraper): monthuset`

Same TDD loop.

After registry has all 9 scrapers, run a full local end-to-end check:

```bash
cd backend
pytest tests/unit/ -v
ruff check app tests
mypy app
API_KEY=test METALS_DEV_API_KEY=<key> uvicorn app.main:app --reload
# in another terminal:
curl -H "X-API-Key: test" http://127.0.0.1:8000/health | python -m json.tool
```

Confirm all 9 scrapers report `ok: true`. If a scraper reports `ok: false`, fix it before proceeding to Phase 3.

---

## Phase 3 — PWA frontend

### Task 20: HTML skeleton + CSS

**Files:**
- Create: `frontend/index.html`
- Create: `frontend/styles.css`

- [ ] **Step 1: Create `frontend/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
  <meta name="apple-mobile-web-app-capable" content="yes" />
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
  <meta name="theme-color" content="#1a1a1a" />
  <link rel="manifest" href="manifest.webmanifest" />
  <link rel="apple-touch-icon" href="icons/icon-192.png" />
  <link rel="stylesheet" href="styles.css" />
  <title>Gold Bar Tracker</title>
</head>
<body>
  <header>
    <h1>Gold Bar Tracker</h1>
    <button id="settings-btn" aria-label="Settings">⚙️</button>
  </header>

  <section id="spot" class="card">
    <h2>Spot price</h2>
    <div id="spot-content">Loading…</div>
  </section>

  <section id="size-picker" class="card">
    <h2>Size</h2>
    <div class="buttons">
      <button data-size="2.5">2.5 g</button>
      <button data-size="5">5 g</button>
      <button data-size="10">10 g</button>
    </div>
  </section>

  <section id="results" class="card">
    <div id="status"></div>
    <table id="listings" hidden>
      <thead>
        <tr><th>Dealer</th><th>Price</th><th>Premium</th><th></th></tr>
      </thead>
      <tbody></tbody>
    </table>
    <button id="refresh" hidden>Refresh</button>
  </section>

  <dialog id="settings-dialog">
    <form method="dialog">
      <h2>Settings</h2>
      <label>Backend URL
        <input id="backend-url" type="url" placeholder="https://your-app.onrender.com" required />
      </label>
      <label>API key
        <input id="api-key" type="password" placeholder="X-API-Key value" required />
      </label>
      <menu>
        <button value="cancel">Cancel</button>
        <button id="save-settings" value="save">Save</button>
      </menu>
    </form>
  </dialog>

  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `frontend/styles.css`**

```css
:root {
  --bg: #1a1a1a;
  --fg: #f5f5f5;
  --muted: #8a8a8a;
  --accent: #d4af37;
  --error: #d97757;
  --card: #262626;
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg); color: var(--fg);
  padding-bottom: env(safe-area-inset-bottom);
  padding-top: env(safe-area-inset-top);
}
header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 1rem; border-bottom: 1px solid #333;
}
header h1 { margin: 0; font-size: 1.2rem; }
header button { background: none; border: 0; color: var(--fg); font-size: 1.4rem; cursor: pointer; }
.card { margin: 1rem; padding: 1rem; background: var(--card); border-radius: 12px; }
.card h2 { margin: 0 0 0.5rem 0; font-size: 0.9rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }
.buttons { display: flex; gap: 0.5rem; }
.buttons button {
  flex: 1; padding: 0.75rem; background: var(--bg); color: var(--fg);
  border: 1px solid #444; border-radius: 8px; font-size: 1rem; cursor: pointer;
}
.buttons button.active { background: var(--accent); color: var(--bg); border-color: var(--accent); }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 0.5rem; text-align: left; border-bottom: 1px solid #333; }
th { color: var(--muted); font-weight: normal; font-size: 0.8rem; }
tr.error td { color: var(--error); }
tr.unavailable td { color: var(--muted); }
#status { padding: 0.5rem 0; color: var(--muted); }
#refresh { margin-top: 0.5rem; padding: 0.5rem 1rem; background: var(--accent); color: var(--bg); border: 0; border-radius: 8px; font-weight: bold; cursor: pointer; }
dialog { background: var(--card); color: var(--fg); border: 0; border-radius: 12px; padding: 1.5rem; }
dialog::backdrop { background: rgba(0,0,0,0.6); }
dialog label { display: block; margin: 0.75rem 0; }
dialog input { width: 100%; padding: 0.5rem; margin-top: 0.25rem; background: var(--bg); color: var(--fg); border: 1px solid #444; border-radius: 6px; }
.spot-row { display: flex; justify-content: space-between; padding: 0.25rem 0; }
```

- [ ] **Step 3: Smoke-test the static HTML**

```bash
cd frontend
python -m http.server 5500
```

Open `http://127.0.0.1:5500/` in a browser. Layout should render with placeholder text. Stop the server.

- [ ] **Step 4: Commit**

```bash
git add frontend/index.html frontend/styles.css
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat(frontend): HTML skeleton and styles"
```

---

### Task 21: JS — fetch and render

**Files:**
- Create: `frontend/app.js`

- [ ] **Step 1: Create `frontend/app.js`**

```javascript
const STORAGE_KEY = 'gold-tracker-config';
const $ = (s) => document.querySelector(s);

function loadConfig() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch { return {}; }
}
function saveConfig(c) { localStorage.setItem(STORAGE_KEY, JSON.stringify(c)); }

function fmtDKK(n) { return new Intl.NumberFormat('da-DK', { style: 'currency', currency: 'DKK', maximumFractionDigits: 0 }).format(n); }
function fmtEUR(n) { return new Intl.NumberFormat('da-DK', { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 }).format(n); }
function fmtPct(n) { return n == null ? '—' : (n > 0 ? '+' : '') + n.toFixed(1) + '%'; }

let lastSize = null;

async function fetchPrices(size) {
  const cfg = loadConfig();
  if (!cfg.backendUrl || !cfg.apiKey) {
    showStatus('Open Settings to configure backend URL and API key.');
    return;
  }
  lastSize = size;
  showStatus(`Loading… first request after idle can take ~60 s.`);
  $('#listings').hidden = true;
  $('#refresh').hidden = true;
  setActiveSize(size);

  let resp;
  try {
    resp = await fetch(`${cfg.backendUrl.replace(/\/$/, '')}/prices/${size}`, {
      headers: { 'X-API-Key': cfg.apiKey },
    });
  } catch (e) {
    showStatus(`Network error: ${e.message}`);
    return;
  }
  if (resp.status === 401) { showStatus('Bad API key — open Settings.'); return; }
  if (!resp.ok) { showStatus(`Server error: ${resp.status}`); return; }
  const data = await resp.json();
  render(data);
}

function render(data) {
  if (data.spot) {
    const g = data.spot.gold, s = data.spot.silver;
    $('#spot-content').innerHTML = `
      <div class="spot-row"><span>Gold</span><span>${fmtEUR(g.per_gram_eur)}/g · ${fmtDKK(g.per_gram_dkk)}/g</span></div>
      <div class="spot-row"><span>Silver</span><span>${fmtEUR(s.per_gram_eur)}/g · ${fmtDKK(s.per_gram_dkk)}/g</span></div>
      ${data.fx_stale ? '<div class="spot-row" style="color:var(--error)">⚠ FX rates stale (fallback in use)</div>' : ''}
    `;
  } else {
    $('#spot-content').textContent = 'Spot price unavailable.';
  }

  const tbody = $('#listings tbody');
  tbody.innerHTML = '';
  for (const li of data.listings) {
    const tr = document.createElement('tr');
    tr.className = li.status;
    if (li.status === 'ok') {
      tr.innerHTML = `
        <td>${li.dealer}</td>
        <td>${fmtDKK(li.price_dkk)}</td>
        <td>${fmtPct(li.premium_pct)}</td>
        <td><a href="${li.url}" target="_blank" rel="noopener">→</a></td>
      `;
    } else {
      const note = li.status === 'out_of_stock' ? 'out of stock'
                : li.status === 'unavailable' ? 'price on request'
                : `error (${li.error || 'unknown'})`;
      tr.innerHTML = `<td>${li.dealer}</td><td colspan="3">${note}</td>`;
    }
    tbody.appendChild(tr);
  }
  $('#listings').hidden = false;
  $('#refresh').hidden = false;
  $('#status').textContent = `Updated ${new Date(data.fetched_at).toLocaleTimeString()}`;
}

function showStatus(msg) {
  $('#status').textContent = msg;
}
function setActiveSize(size) {
  document.querySelectorAll('#size-picker button').forEach(b => {
    b.classList.toggle('active', parseFloat(b.dataset.size) === parseFloat(size));
  });
}

// Wire up UI
document.querySelectorAll('#size-picker button').forEach(b => {
  b.addEventListener('click', () => fetchPrices(parseFloat(b.dataset.size)));
});
$('#refresh').addEventListener('click', () => { if (lastSize != null) fetchPrices(lastSize); });

// Settings dialog
$('#settings-btn').addEventListener('click', () => {
  const cfg = loadConfig();
  $('#backend-url').value = cfg.backendUrl || '';
  $('#api-key').value = cfg.apiKey || '';
  $('#settings-dialog').showModal();
});
$('#settings-dialog').addEventListener('close', () => {
  if ($('#settings-dialog').returnValue === 'save') {
    saveConfig({ backendUrl: $('#backend-url').value, apiKey: $('#api-key').value });
    showStatus('Settings saved.');
  }
});

// Service worker registration (Task 24)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('service-worker.js').catch(() => {});
}
```

- [ ] **Step 2: Smoke-test against the local backend**

In one terminal:

```bash
cd backend
API_KEY=test METALS_DEV_API_KEY=<key> uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

In another:

```bash
cd frontend
python -m http.server 5500
```

Open `http://127.0.0.1:5500/`. Click ⚙️, set backend URL to `http://127.0.0.1:8000` and API key to `test`. Save. Click "5 g". Confirm spot prices and dealer rows render. Stop both servers.

- [ ] **Step 3: Commit**

```bash
git add frontend/app.js
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat(frontend): fetch + render + settings dialog"
```

---

### Task 22: PWA manifest + icons

**Files:**
- Create: `frontend/manifest.webmanifest`
- Create: `frontend/icons/icon-192.png`
- Create: `frontend/icons/icon-512.png`

- [ ] **Step 1: Create the icons folder and PNGs**

You need two square PNG icons. The simplest path: open any 512×512 image (a screenshot, an emoji rendered to PNG, etc.) and save it as `frontend/icons/icon-512.png`, then resize a copy to 192×192 as `icon-192.png`. Or use a CLI:

```bash
mkdir -p frontend/icons
# Example: generate solid-color placeholders with ImageMagick if available
magick -size 512x512 xc:#d4af37 -gravity center -fill black -pointsize 200 \
  -annotate 0 "Au" frontend/icons/icon-512.png
magick frontend/icons/icon-512.png -resize 192x192 frontend/icons/icon-192.png
```

If you don't have ImageMagick, any 512×512 and 192×192 PNGs you produce manually are fine.

- [ ] **Step 2: Create `frontend/manifest.webmanifest`**

```json
{
  "name": "Gold Bar Tracker",
  "short_name": "GoldBars",
  "description": "Compare DK gold bar prices",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#1a1a1a",
  "theme_color": "#1a1a1a",
  "icons": [
    { "src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
```

- [ ] **Step 3: Smoke-test**

Reload `http://127.0.0.1:5500/` in Chrome DevTools → Application → Manifest. Confirm no errors and the icons load.

- [ ] **Step 4: Commit**

```bash
git add frontend/manifest.webmanifest frontend/icons/
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat(frontend): PWA manifest and icons"
```

---

### Task 23: Service worker

**Files:**
- Create: `frontend/service-worker.js`

iOS Safari requires a registered service worker for "Add to Home Screen" to give the app standalone behavior. The service worker can be minimal — no offline caching needed since the tool is online-only by design.

- [ ] **Step 1: Create `frontend/service-worker.js`**

```javascript
// Minimal service worker — required for iOS PWA install but does no caching.
// The tool is online-only by design (live scrapes), so caching API responses is undesirable.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => { /* let browser handle every request */ });
```

- [ ] **Step 2: Smoke-test**

Reload `http://127.0.0.1:5500/`. Open DevTools → Application → Service Workers. Confirm one is registered for the page.

- [ ] **Step 3: Commit**

```bash
git add frontend/service-worker.js
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "feat(frontend): minimal service worker for PWA install"
```

---

### Task 24: Live integration smoke test (backend)

**Files:**
- Create: `backend/tests/integration/__init__.py`
- Create: `backend/tests/integration/test_live.py`

- [ ] **Step 1: Create the integration test directory and file**

```bash
mkdir -p backend/tests/integration
touch backend/tests/integration/__init__.py
```

```python
# backend/tests/integration/test_live.py
import httpx
import pytest

from app.scrapers.registry import ALL_SCRAPERS


@pytest.mark.asyncio
@pytest.mark.parametrize("scraper", ALL_SCRAPERS, ids=lambda s: s.name)
async def test_dealer_returns_5g_price_live(scraper) -> None:
    """Hit the real dealer site and assert we extract a numeric price for 5g."""
    async with httpx.AsyncClient() as client:
        listing = await scraper.fetch(5.0, client)
    assert listing is not None, f"{scraper.name} returned no listing"
    assert listing.status == "ok", f"{scraper.name} status={listing.status} error={listing.error}"
    assert listing.price_dkk is not None and listing.price_dkk > 0
```

- [ ] **Step 2: Run it locally to confirm all 9 scrapers actually work against live sites**

```bash
cd backend
pytest tests/integration/test_live.py -v
```

Expected: 9 passed. If any fail, fix the corresponding scraper before continuing — these are the canary that catches stale parsers.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/integration/
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "test: live integration smoke for all scrapers"
```

---

### Task 25: README

**Files:**
- Create: `README.md` (repo root)

- [ ] **Step 1: Create `README.md`**

```markdown
# Gold Bar Tracker

Personal tool to compare 2.5 g, 5 g, and 10 g gold-bar prices across 9 Danish online dealers,
plus live spot prices for gold and silver. Accessed as a PWA from iPhone.

## Architecture

- **Backend** — Python + FastAPI on Render free tier. `backend/`
- **Frontend** — Static PWA on Cloudflare Pages. `frontend/`
- **Spot price** — metals.dev (USD/oz)
- **FX** — frankfurter.app (USD→EUR, USD→DKK)

## Local dev

### Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio ruff mypy
pytest tests/unit -v
API_KEY=test METALS_DEV_API_KEY=<your_key> uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
python -m http.server 5500
```

Open `http://127.0.0.1:5500/`, click ⚙️, set backend URL and API key.

## Environment variables (Render)

| Var | Required | Notes |
|---|---|---|
| `API_KEY` | yes | Shared secret. PWA sends as `X-API-Key`. |
| `METALS_DEV_API_KEY` | yes | metals.dev free tier key. |
| `FRONTEND_ORIGIN` | yes | Your Cloudflare Pages URL, e.g. `https://gold-tracker.pages.dev` — for CORS. |

## Tests

- Unit: `pytest tests/unit -v` (no network)
- Live integration: `pytest tests/integration -v` (hits real dealer sites — run sparingly)
- Type/lint: `ruff check app tests && mypy app`

## Spec & plan

- Design: `docs/superpowers/specs/2026-05-07-gold-bar-tracker-design.md`
- Plan: `docs/superpowers/plans/2026-05-07-gold-bar-tracker.md`
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "docs: README"
```

---

## Phase 4 — Deploy + CI

### Task 26: Push repo to personal GitHub

**Files:** none (creates remote)

- [ ] **Step 1: Verify the existing local commits are authored by the personal email**

```bash
cd /c/Users/YzeirBaku/projects/gold-bar-tracker
git log --pretty=format:"%h %an <%ae> %s"
```

Every line must show `Yzeir Baku <yzeirbaku@hotmail.com>`. If any commit shows a work email, stop and rewrite history before pushing — once pushed, this is hard to fix.

- [ ] **Step 2: Create the empty repo on the personal GitHub account**

In a browser, sign into the **personal** GitHub account, create a new repo named `gold-bar-tracker`. Choose Private or Public — your call. **Do not initialize** with README/license/gitignore (we already have local commits).

- [ ] **Step 3: Add the remote and push**

Replace `<your-username>` with the personal account's username:

```bash
git remote add origin git@github.com:<your-username>/gold-bar-tracker.git
git push -u origin main
```

If pushing over HTTPS instead of SSH, you'll be prompted for credentials — use the personal-account Personal Access Token, not the work account's. **At this point you'll be prompted to authenticate; let me know if it fails.**

- [ ] **Step 4: Confirm on GitHub web UI that the repo shows under the personal account, with all commits attributed to `yzeirbaku@hotmail.com`.**

---

### Task 27: Deploy backend to Render

**Files:** none (cloud config only)

- [ ] **Step 1: Sign in to https://render.com using GitHub** — connect with the personal GitHub account specifically; Render will request access to your repos.

- [ ] **Step 2: New → Web Service → connect `gold-bar-tracker`**

- Name: `gold-bar-tracker` (gives you `gold-bar-tracker.onrender.com`)
- Region: Frankfurt (closest to DK)
- Branch: `main`
- Root directory: `backend`
- Runtime: Python 3
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Plan: **Free**

- [ ] **Step 3: Add env vars under "Environment"**

- `API_KEY` — generate a strong random string (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`). Save the value somewhere — you'll need it for the PWA settings.
- `METALS_DEV_API_KEY` — your metals.dev free-tier key.
- `FRONTEND_ORIGIN` — leave as `*` for now; revisit in Task 28 once Cloudflare Pages URL exists.

- [ ] **Step 4: Deploy. Wait for green build.**

- [ ] **Step 5: Smoke-test the deployed API**

```bash
# Replace <APIKEY> with the value you set
curl -H "X-API-Key: <APIKEY>" https://gold-bar-tracker.onrender.com/prices/5 | python -m json.tool
```

First request will take 30–60s (cold start). Expected: full JSON response with 9 listings + spot.

---

### Task 28: Deploy frontend to Cloudflare Pages

**Files:** none (cloud config only)

- [ ] **Step 1: Sign in to https://pages.cloudflare.com** with a Cloudflare account (free tier).

- [ ] **Step 2: Create a project → Connect to Git → personal GitHub → select `gold-bar-tracker`**

- Production branch: `main`
- Build command: *(leave empty — no build step)*
- Build output directory: `frontend`

- [ ] **Step 3: Deploy. Wait for green build.**

You'll get a URL like `https://gold-bar-tracker.pages.dev`.

- [ ] **Step 4: Tighten CORS on the backend**

Back in Render → service → Environment, change `FRONTEND_ORIGIN` from `*` to your exact Cloudflare Pages URL (e.g. `https://gold-bar-tracker.pages.dev`). Render will auto-redeploy.

- [ ] **Step 5: Smoke-test the deployed PWA**

Open `https://gold-bar-tracker.pages.dev` in your **desktop** browser. Click ⚙️, set:
- Backend URL: `https://gold-bar-tracker.onrender.com`
- API key: the value from Task 27 Step 3

Click "5 g". First load takes ~60s (Render cold start). Confirm spot + 9 dealer rows render correctly.

---

### Task 29: GitHub Actions — unit tests + lint + types

**Files:**
- Create: `.github/workflows/tests.yml`

- [ ] **Step 1: Create `.github/workflows/tests.yml`**

```yaml
name: tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  unit:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio ruff mypy
      - name: Lint
        run: ruff check app tests
      - name: Type-check
        run: mypy app
      - name: Unit tests
        run: pytest tests/unit -v
```

- [ ] **Step 2: Commit and push**

```bash
git add .github/workflows/tests.yml
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "ci: unit tests + ruff + mypy on push"
git push
```

- [ ] **Step 3: Confirm green** on GitHub → Actions tab. Fix any issues until green.

---

### Task 30: GitHub Actions — weekly live smoke test

**Files:**
- Create: `.github/workflows/live-smoke.yml`

- [ ] **Step 1: Create `.github/workflows/live-smoke.yml`**

```yaml
name: live-smoke

on:
  schedule:
    - cron: '0 9 * * 1'   # Mondays at 09:00 UTC
  workflow_dispatch:

jobs:
  smoke:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - name: Install
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-asyncio
      - name: Live integration tests
        run: pytest tests/integration -v
        continue-on-error: false
```

- [ ] **Step 2: Commit and push**

```bash
git add .github/workflows/live-smoke.yml
git -c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku" commit -m "ci: weekly live smoke test for all scrapers"
git push
```

- [ ] **Step 3: Trigger the workflow manually** from GitHub Actions tab → live-smoke → "Run workflow". Confirm green. From this point, you'll be notified by GitHub if a scraper silently breaks.

---

### Task 31: Add to iPhone Home Screen

**Files:** none (on-device)

- [ ] **Step 1: On the iPhone, open Safari → `https://gold-bar-tracker.pages.dev`**

- [ ] **Step 2: Tap the Share button → "Add to Home Screen" → Add**

- [ ] **Step 3: Open the new home-screen icon. It should launch fullscreen with no Safari chrome.**

- [ ] **Step 4: Open ⚙️ → enter backend URL `https://gold-bar-tracker.onrender.com` and the API key from Task 27. Save.**

- [ ] **Step 5: End-to-end test**

Tap **5 g**. Wait ~30–60s on first cold start. Confirm:
- Spot prices show in EUR + DKK for both gold and silver
- 9 dealer rows show
- Sorted ascending by price
- Premium % column populated
- Tapping a row's "→" opens the dealer's product page in Safari
- Tap **Refresh** — should now be fast (warm)
- Tap **2.5 g** and **10 g** — both should work

---

### Task 32: Final wrap

**Files:** none

- [ ] **Step 1: Run the full test suite locally one more time**

```bash
cd backend
pytest tests/unit -v
pytest tests/integration -v
ruff check app tests
mypy app
```

All green.

- [ ] **Step 2: Verify the `git log` is clean**

```bash
cd /c/Users/YzeirBaku/projects/gold-bar-tracker
git log --oneline
git log --pretty=format:"%an <%ae>" | sort -u
```

The author list must show only `Yzeir Baku <yzeirbaku@hotmail.com>`. If anything else appears, raise the issue before declaring done.

- [ ] **Step 3: Sanity-check the deployed services**

- Render dashboard: build is green, no error logs
- Cloudflare Pages dashboard: build is green
- GitHub Actions: both workflows green

- [ ] **Step 4: Stop. The project is done.** From now on, opening the home-screen icon and tapping a size button gives you a sorted DK gold-bar comparison in 30–60 s (or faster, while warm).

---

## Notes for the executing engineer

- **Selectors are the only thing the plan can't pre-fill.** Every dealer's HTML is unique and may shift between writing this plan and running it. When a `parse_failed` shows up, open the saved fixture and re-derive the selectors. Don't guess — inspect.
- **JS-rendered fallback.** If a dealer page has no price in raw HTML (likely candidates: Silver Gold Bull, Jan Jørgensen), introduce Playwright *only* for that dealer's module. Add `playwright` and a post-install `playwright install chromium` to the requirements + Render build command. Keep the rest HTTP-only.
- **Render cold-start.** First request after 15 min idle takes ~30–60 s. The PWA already shows a hint after 5 s. Don't add a keep-warm cron unless you find yourself hitting cold starts constantly — it eats free hours and is rarely worth it for personal use.
- **API key handling.** Stored in `localStorage`, never in the repo. If you ever rotate it, change the env var on Render and re-enter it in the PWA Settings.
- **Commit identity.** Every git command in this plan passes `-c user.email="yzeirbaku@hotmail.com" -c user.name="Yzeir Baku"` explicitly so neither machine-global nor shell-env settings can leak the wrong identity. If you set repo-local config (`git config user.email …`) you can drop the `-c` flags.
