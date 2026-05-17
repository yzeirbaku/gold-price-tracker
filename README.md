# Gold Price Tracker

Compare gold-bar (2.5–20 g) and gold-coin (≤ 20 g fine) prices across Danish online dealers in real time, with live gold + silver spot, per-row history charts, "buy now or wait?" context, weekly/monthly reports, and an optional sign-in that unlocks a personal portfolio with P&L and email alerts when a cross-dealer premium drops below your threshold.

## Architecture

- **Backend** — Python + FastAPI on Render (`backend/`)
- **Frontend** — Static PWA on Cloudflare Pages (`frontend/`)
- **Storage** — Neon Postgres (snapshots, sessions, purchases, alerts, reports)
- **Spot (live)** — api.gold-api.com
- **Spot (historical, portfolio cost basis)** — yfinance `GC=F` / `SI=F` futures
- **FX (live + historical)** — frankfurter.dev
- **Email** — Resend (magic-link sign-in + alert emails)
- **Cron** — Upstash QStash (snapshot + weekly/monthly report generation)
- **Auth** — passwordless magic links; opaque session bearer tokens in `localStorage` (chosen over cookies because the frontend and backend live on different sites and modern browsers drop cross-site cookies)

## Dealers

Live: **Tavex, Vitus Guld, Plaza, Nordisk Guld, Sero Guld, Nyfortuna, Jan Jørgensen**. Nordisk and Sero need the full Sec-Ch-Ua / Sec-Fetch-* header set to bypass the Simply.com WAF.

Dropped: **Mønthuset** (live category empty since early 2025). Skipped: **Silver Gold Bull DK** (JS-rendered React SPA with dynamic pricing).

Coin coverage is bullion-only, drawn from a static registry in `backend/app/coins.py`: Krugerrand, Maple Leaf, Vienna Philharmonic, American Eagle, Britannia, Sovereign, Ducat, Panda, plus the Danish Scandinavian-Monetary-Union 20 kr and 10 kr (Christian IX, Christian X, Frederik VIII). Listings whose title doesn't match the registry are silently skipped. Plaza and Jan Jørgensen don't currently stock bullion coins; their scrapers run anyway and pick up new listings if those dealers ever expand.

## Frontend

Installable PWA. On mobile, swiping right from the left edge opens the side menu; swiping the menu right-to-left closes it (in addition to tap-outside).

- **Prices** — bar prices for the selected size (2.5/5/10/20 g) ranked by total price + premium %. Click any row to expand an inline 24h/7d/30d price + premium chart and a "Buy now or wait?" panel comparing today's premium against the dealer's 30-day IQR + lowest observation.
- **Coins** — every recognized in-stock bullion coin from every dealer, ranked by premium. Same inline charts + context on row click.
- **Portfolio** (signed in) — table of purchases with per-row P&L, summary card with total paid / value / PnL split by metal, and a value-over-time chart with range pills (1W / 1M / 6M / 1Y / all) and deposit-adjusted change. CSV export.
- **Alerts** (signed in) — list of premium-threshold alerts with current-min enrichment. Add/edit dialog previews the live cross-dealer min for the target so you can pick a sensible threshold without alt-tabbing.
- **Reports** — archive of weekly + monthly HTML reports (filterable by year and month) plus on-demand "Last week" / "Last month" generation that doesn't archive.
- **Settings** — paste in the X-API-Key on first run; pick light/dark theme.
- **Spot card** — auto-refreshes every 30 s and surfaces a "Snapshots: N min ago" indicator so a stalled cron is visible.

## Endpoints

Public (`X-API-Key` header):

| Method | Path | Notes |
|---|---|---|
| GET  | `/` | health ping |
| GET  | `/prices/{size}` | live bar fan-out for size ∈ {2.5, 5, 10, 20} |
| GET  | `/coins` | live coin fan-out (≤ 20 g fine), sorted by premium |
| GET  | `/spot` | live gold + silver spot per gram (EUR / DKK) |
| GET  | `/history/bar/{dealer}/{size}?range=24h\|7d\|30d` | bar price + premium history |
| GET  | `/history/coin/{dealer}/{coin_type}/{fine_gold_g}?range=…` | coin price + premium history |
| GET  | `/context/bar/{dealer}/{size}` | today's premium vs 30-day IQR + min |
| GET  | `/context/coin/{dealer}/{coin_type}/{fine_gold_g}` | same, for a coin variant |
| GET  | `/snapshot/age` | seconds since the most recent persisted snapshot |
| GET  | `/reports` | archived weekly + monthly reports |
| GET  | `/reports/{id}` | download stored report as HTML |
| POST | `/reports/generate?range=week\|month` | on-demand report, not archived |
| GET  | `/health` | per-scraper pass/fail |
| POST | `/snapshot` | cron-only — run scrapers + persist |
| POST | `/reports/cron/{type}` | cron-only — generate + archive (`weekly` \| `monthly`) |

Auth (passwordless magic link):

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/request-link` | issue a sign-in email; always 204; rate-limited 3/10min/email, 30/hr/IP |
| POST | `/auth/verify` | exchange the magic-link token for a session bearer token |
| GET  | `/auth/me` | current user or 401 |
| POST | `/auth/logout` | delete the session row |

Per-user (`Authorization: Bearer <token>`):

| Method | Path | Notes |
|---|---|---|
| GET    | `/portfolio` | user's purchases + summary (live spot-driven P&L) |
| GET    | `/portfolio/history?range=…&metal=…` | portfolio value over time + period change |
| POST   | `/portfolio` | create purchase (freezes historical spot at write time) |
| PATCH  | `/portfolio/{id}` | edit; re-freezes spot if `purchased_at` or `metal` change |
| DELETE | `/portfolio/{id}` | hard delete; 404 if not the caller's row |
| GET    | `/alerts` | user's threshold alerts + live "current min" enrichment |
| GET    | `/alerts/options` | bar sizes + coin registry for the dialog |
| GET    | `/alerts/preview` | preview current min premium for a prospective target |
| POST   | `/alerts` | create (bar by `size_g`; coin by `(coin_type, fine_gold_g)`) |
| PATCH  | `/alerts/{id}` | edit threshold or enabled flag; threshold change resets muted state |
| DELETE | `/alerts/{id}` | hard delete; 404 if not the caller's row |

Both auth schemes coexist — the site works fully without signing in.

## Local dev

```bash
# Backend
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows bash; Linux/Mac: .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-asyncio ruff mypy
API_KEY=test uvicorn app.main:app --reload
```

```bash
# Frontend (separate shell)
cd frontend
python -m http.server 5500
```

Open `http://127.0.0.1:5500/`, click ☰ → **Settings**, paste the API key. Backend URL lives in `frontend/config.js` (defaults to `http://127.0.0.1:8000` for local dev; Cloudflare Pages overwrites it at build time via the `BACKEND_URL` env var).

### Local Postgres (portfolio / history / reports / alerts)

```bash
docker compose up -d  # Postgres on localhost:5433

cd backend
DATABASE_URL='postgresql://gold:gold@localhost:5433/goldtracker' \
  .venv/Scripts/python.exe -m scripts.seed     # 30 days fake snapshots + one weekly + one monthly report

API_KEY=test DATABASE_URL='postgresql://gold:gold@localhost:5433/goldtracker' \
  MAGIC_LINK_BASE_URL=http://127.0.0.1:5500 MAGIC_LINK_DEV_PRINT=1 \
  uvicorn app.main:app --reload
```

`MAGIC_LINK_DEV_PRINT=1` makes both sign-in AND alert emails log to stdout instead of going through Resend — useful for testing without burning quota.

### Verification

```bash
ruff check app tests
mypy app
pytest tests/unit -v          # pure logic, no network
pytest tests/api -v           # FastAPI routes against real Postgres (skipped if DATABASE_URL unset)
```

CI runs all four on every push to `main` (Postgres comes from a sidecar service container). The live integration suite (`tests/integration/test_live.py`) hits real dealer sites and runs Mondays 09:00 UTC + on workflow_dispatch.

## Environment variables (Render)

| Var | Required | Notes |
|---|---|---|
| `API_KEY` | yes | Shared secret. PWA sends as `X-API-Key`. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `FRONTEND_ORIGIN` | no | Cloudflare Pages URL. Defaults to `*` (permissive). Auth is bearer-token-based so wildcard doesn't open a credential-leak path; explicit is just tidier. |
| `DATABASE_URL` | yes (for portfolio + history + reports + alerts) | Neon Postgres DSN. Without it, only `/prices`, `/coins`, `/spot` work. |
| `RESEND_API_KEY` | yes (for sign-in + alerts) | Resend API key (`re_…`). |
| `MAGIC_LINK_BASE_URL` | yes (for sign-in) | Frontend origin used to build the `#auth=` URL in the email. |
| `RESEND_FROM` | no | Override the From address. Defaults to `onboarding@resend.dev` (Resend's shared sender; no DNS setup). |
| `MAGIC_LINK_DEV_PRINT` | no (dev only) | Set to `1` to log sign-in + alert emails to stdout instead of sending via Resend. |

## Cron

Three Upstash QStash schedules drive the cron-only endpoints. Free tier covers ~75 messages/day; the 2-minute delivery timeout absorbs Render free-tier cold starts. QStash forwards `X-Api-Key` via the schedule's `Upstash-Forward-X-Api-Key` header setting.

| Schedule | Cron (UTC) | Target |
|---|---|---|
| Snapshot | `*/20 * * * *` | `POST /snapshot` |
| Weekly report | `30 22 * * 0` (Sun 22:30 UTC ≈ Mon 00:30 CPH) | `POST /reports/cron/weekly` |
| Monthly report | `30 0 1 * *` (1st at 00:30 UTC) | `POST /reports/cron/monthly` |

## Security notes

- **Auth tokens.** Magic links are SHA-256 hashed on disk (15-min TTL, single-use, rate-limited). Session tokens are opaque UUIDs with a 90-day sliding TTL (the `last_seen_at` refresh is debounced to 1 h to avoid write amplification). Tokens live in `localStorage` and ship as `Authorization: Bearer <token>` — no CSRF risk since browsers don't auto-attach the Authorization header. Logout revokes the session immediately.
- **XSS mitigation.** Strict CSP in `frontend/_headers` allowlists script + connect origins; `escapeHtml` is applied at every `innerHTML` insertion of scraper / user-sourced content; the report renderer escapes `</` inside its JSON sidecar so dealer-controlled strings can't break out of the `<script>` block.
- **Snapshot persistence is defended in depth.** A stale-FX guard refuses to write when the upstream FX call fell back to the static stamped rate. An outlier guard rejects spot deviations >10 % from the most recent value within an hour. Scraper outliers (bar premium outside [0, 80 %] or coin premium outside [0, 120 %]) flip to `status='error'` before they land in history. Each guard logs a structured `snapshot_skipped` / `scraper_outlier` event for grep.
- **Alerts.** Per-user rate cap of 8 fire-events per rolling hour. Resend HTTP calls happen *after* the snapshot transaction commits, so a hung upstream cannot roll back the snapshot. A Resend failure for one user leaves their alerts un-muted (next tick retries) and does not poison the loop for other users.
