# Gold Bar Tracker — Claude guide

Personal PWA that compares 2.5 / 5 / 10 / 20 g gold-bar prices across Danish online dealers, plus a live spot ticker for gold and silver. Online-only by design — every load is a fresh scrape.

## Git

**All commits in this repo must be authored as `yzeirbaku@hotmail.com` (name: `Yzeir Baku`).** This is already set in the local repo config — do not change it, and verify with `git config user.email` before committing if anything looks off.

Co-author trailers from Claude Code's default workflow are fine; the *author* must remain the hotmail address.

## Architecture

```
backend/         FastAPI on Render free tier (Python 3.12)
  app/
    main.py            FastAPI app + CORS + endpoints
    auth.py            X-API-Key header check (constant-time compare)
    fx.py              USD→EUR/DKK via frankfurter.dev (+ stamped fallback)
    spot.py            api.gold-api.com USD/oz → per-gram USD
    orchestrator.py    fan-out scrapers, compute premium %, sort
    models.py          Pydantic response models
    scrapers/
      base.py          DealerScraper Protocol, DEFAULT_HEADERS, parse_dkk_price
      registry.py      ALL_SCRAPERS list (order = display order before sort)
      tavex.py         JSON pricelist on .product__price--single
      vitusguld.py     listing → product page (apples-to-apples)
      plaza.py
      nordiskguld.py   needs Sec-Ch-Ua / Sec-Fetch-* to bypass Simply.com WAF
      seroguld.py      same WAF as Nordisk
      nyfortuna.py
      janjorgensen.py
  tests/
    unit/              fixtures-driven, no network — runs in CI on every push
    integration/       hits real dealer sites — weekly cron + manual dispatch
    fixtures/          frozen HTML snapshots per dealer

frontend/        Static PWA on Cloudflare Pages (vanilla JS, no build)
  index.html
  app.js               size picker, listings table, spot ticker, settings dialog
  config.js            window.BACKEND_URL — overwritten by CF Pages build script
  styles.css
  service-worker.js    minimal — required for iOS install, no caching
  manifest.webmanifest
```

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/` | none | health ping |
| GET | `/prices/{size}` | `X-API-Key` | size ∈ {2.5, 5, 10, 20}; runs all scrapers + spot + FX in parallel with a 15 s wall-clock timeout |
| GET | `/spot` | `X-API-Key` | spot + FX only — used by the auto-refreshing ticker every 20 s |
| GET | `/health` | `X-API-Key` | runs every scraper at 5 g, returns per-dealer pass/fail |

## Dealers

Live: **Tavex, Vitus Guld, Plaza, Nordisk Guld, Sero Guld, Nyfortuna, Jan Jørgensen Smykker**.
Dropped: **Mønthuset** (live category empty since early 2025).
Skipped: **Silver Gold Bull DK** (JS-rendered React SPA + Algolia + dynamic pricing).

## Local dev

```bash
# Backend
cd backend
python -m venv .venv && source .venv/Scripts/activate    # Windows bash
pip install -r requirements.txt
pip install pytest pytest-asyncio ruff mypy
API_KEY=test uvicorn app.main:app --reload

# Frontend (separate shell)
cd frontend
python -m http.server 5500
# open http://127.0.0.1:5500/ → ⚙ → paste API key (any value matching backend)
```

### Local Postgres + seed (only needed to test the history feature)

```bash
# From repo root — starts Postgres on localhost:5432
docker compose up -d

# Seed 30 days of fake snapshots (refuses to run against non-localhost DBs)
cd backend
export DATABASE_URL='postgresql://gold:gold@localhost:5432/goldtracker'
.venv/Scripts/python.exe -m scripts.seed

# Start the backend with DATABASE_URL set so /snapshot and /history work
API_KEY=test DATABASE_URL='postgresql://gold:gold@localhost:5432/goldtracker' \
  uvicorn app.main:app --reload
```

The seed uses `random.seed(42)` so re-running gives identical data. It TRUNCATEs both tables on every run.

## Verification before completion

Run from `backend/`:

```bash
ruff check app tests
mypy app
pytest tests/unit -v
```

CI (`.github/workflows/tests.yml`) runs all three on push/PR to `main`. The live integration suite (`tests/integration/test_live.py`) runs Mondays 09:00 UTC and on workflow_dispatch — it's `continue-on-error` because dealer sites move.

## Conventions / gotchas

- **`parse_dkk_price` in `scrapers/base.py`** is the canonical price parser — it handles Danish `2.940,00 kr.`, US-style `5345.47`, and the gnarly `6.252` (Danish thousands, not 6 + decimal). New scrapers should use it; if a site needs a different rule, add a comment explaining why.
- **Headers**: scrapers must use `DEFAULT_HEADERS` from `scrapers/base.py`. The full Sec-* fingerprint is what gets us past the Simply.com WAF on Nordisk + Sero — don't trim it.
- **Listing status**: `ok | out_of_stock | unavailable | error`. The frontend hides `out_of_stock` and surfaces `error`/`unavailable` as a single-cell row. `_safe_fetch` in the orchestrator turns any scraper exception into an `error` Listing — never let a scraper crash the response.
- **Premium %** = `(price_dkk - spot_dkk_per_g * size_g) / (spot_dkk_per_g * size_g) * 100`, computed in the orchestrator after spot resolves.
- **Sort**: `ok` rows by ascending price, then everything else (errors/unavailable) at the bottom.
- **No caching of API responses** — both the service worker and `app.js` are deliberately cache-free since prices are live. Static assets are cache-busted via `?v=N` query strings in `index.html`.
- **Number formatting**: dots only, never commas. DKK prices use `da-DK` for thousand-grouping (`12.345 dkk`), spot uses `en-US` so the `.` is a decimal separator (`695.42 dkk`). See `fmtDKK` / `fmtSpotDKK` in `app.js`.
- **Backend URL on the frontend** comes from `window.BACKEND_URL` in `config.js`. Cloudflare Pages overwrites this file at build time:
  `echo "window.BACKEND_URL = '${BACKEND_URL}';" > frontend/config.js`

## Adding a new scraper

1. Drop a fixture in `backend/tests/fixtures/<dealer>_listing.html`.
2. Add `backend/app/scrapers/<dealer>.py` implementing the `DealerScraper` Protocol — return `Listing | None` (`None` = size not offered).
3. Register it in `scrapers/registry.py`.
4. Add a unit test in `backend/tests/unit/scrapers/` using the fixture; cover at least one in-stock and one out-of-stock case.
5. Add the dealer to the live smoke test in `tests/integration/test_live.py`.
6. Update the README + this file's "Dealers" section.
