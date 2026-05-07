# Gold Bar Tracker

Personal tool to compare 2.5 g, 5 g, and 10 g gold-bar prices across Danish online dealers, plus live spot prices for gold and silver. Accessed as a PWA from iPhone.

## Architecture

- **Backend** — Python + FastAPI on Render free tier. `backend/`
- **Frontend** — Static PWA on Cloudflare Pages. `frontend/`
- **Spot price** — api.gold-api.com (USD/oz, free, no key)
- **FX** — frankfurter.dev (USD→EUR, USD→DKK)

## Dealers wired

| Dealer | Status |
|---|---|
| Tavex | ✓ live |
| Vitus Guld | ✓ live |
| Plaza | ✓ live |
| Nordisk Guld | ✓ live (needed Sec-Ch-Ua / Sec-Fetch-* headers to bypass Simply.com WAF) |
| Sero Guld | ✓ live (same WAF as Nordisk; stock varies) |
| Nyfortuna | ✓ live (carries 1g/10g/20g/50g — no 2.5g or 5g) |
| Jan Jørgensen Smykker | ✓ live |
| Mønthuset | dropped — live guldbarrer category empty since early 2025 |
| Silver Gold Bull DK | skipped — JS-rendered React SPA + Algolia + dynamic pricing |

## Local dev

### Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate          # Windows bash
# (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
pip install pytest pytest-asyncio ruff mypy

pytest tests/unit -v                   # unit tests, no network
ruff check app tests
mypy app

API_KEY=test uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
python -m http.server 5500
```

Open `http://127.0.0.1:5500/`, click ⚙️, paste your API key. The backend URL is set in `frontend/config.js` (defaults to `http://127.0.0.1:8000` for local dev; Cloudflare Pages overwrites it at build time via the `BACKEND_URL` env var).

## Environment variables (Render)

| Var | Required | Notes |
|---|---|---|
| `API_KEY` | yes | Shared secret. PWA sends as `X-API-Key`. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `FRONTEND_ORIGIN` | yes | Your Cloudflare Pages URL, e.g. `https://gold-tracker.pages.dev` — for CORS. |

## Tests

- Unit (no network): `pytest tests/unit -v`
- Live integration (hits real dealer sites): `pytest tests/integration -v`
- Lint + types: `ruff check app tests && mypy app`

## Spec & plan

- Design: `docs/superpowers/specs/2026-05-07-gold-bar-tracker-design.md`
- Plan: `docs/superpowers/plans/2026-05-07-gold-bar-tracker.md`
