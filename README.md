# Gold Bar Tracker

Personal tool to compare 2.5 g, 5 g, and 10 g gold-bar prices across Danish online dealers, plus live spot prices for gold and silver. Accessed as a PWA from iPhone.

## Architecture

- **Backend** — Python + FastAPI on Render free tier. `backend/`
- **Frontend** — Static PWA on Cloudflare Pages. `frontend/`
- **Spot price** — metals.dev (USD/oz)
- **FX** — frankfurter.app (USD→EUR, USD→DKK)

## Dealers wired

| Dealer | Status |
|---|---|
| Tavex | ✓ live |
| Vitus Guld | ✓ live |
| Plaza | ✓ live |
| Nordisk Guld | ✓ (stock-dependent) |
| Sero Guld | ✓ (stock-dependent) |
| Nyfortuna | ✓ live |
| Jan Jørgensen Smykker | ✓ live |
| Mønthuset | ✓ code (live category currently empty) |
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
| `API_KEY` | yes | Shared secret. PWA sends as `X-API-Key`. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `METALS_DEV_API_KEY` | yes | metals.dev free-tier key. |
| `FRONTEND_ORIGIN` | yes | Your Cloudflare Pages URL, e.g. `https://gold-tracker.pages.dev` — for CORS. |

## Tests

- Unit (no network): `pytest tests/unit -v`
- Live integration (hits real dealer sites): `pytest tests/integration -v`
- Lint + types: `ruff check app tests && mypy app`

## Spec & plan

- Design: `docs/superpowers/specs/2026-05-07-gold-bar-tracker-design.md`
- Plan: `docs/superpowers/plans/2026-05-07-gold-bar-tracker.md`
