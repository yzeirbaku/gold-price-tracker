# Gold Price Tracker

Real-time comparison of gold-bar (2.5–20 g) and gold-coin (≤ 20 g fine) prices across Danish online dealers, with live spot, per-row history charts, "buy now or wait?" context, weekly/monthly reports, and optional sign-in for portfolio P&L + email alerts on cross-dealer premium drops.

## Architecture

- **Backend** — Python + FastAPI in Docker on an Oracle Cloud Always-Free VM, behind Caddy with auto-renewing Let's Encrypt TLS at `https://yzeir-gold.duckdns.org` (`backend/`)
- **Frontend** — Static PWA on Cloudflare Pages (`frontend/`)
- **Storage** — Neon Postgres (snapshots, sessions, purchases, alerts, reports)
- **Spot (live)** — api.gold-api.com
- **Spot (historical, portfolio cost basis)** — yfinance `GC=F` / `SI=F` futures
- **FX (live + historical)** — frankfurter.dev
- **Email** — Resend (magic-link sign-in + alert emails)
- **Cron** — Upstash QStash (snapshot + weekly/monthly report generation)
- **Auth** — passwordless magic links; opaque session bearer tokens in `localStorage`

## Dealers

Live: **Tavex, Vitus Guld, Plaza, Nordisk Guld, Sero Guld, Nyfortuna, Jan Jørgensen**. Nordisk + Sero sit behind the Simply.com WAF, which needs the full Sec-* header set plus the proof-of-work handshake in `scrapers/simply_waf.py`. Plaza + Jan Jørgensen don't currently stock bullion coins (their coin scrapers run anyway and pick up new listings if that changes). Skipped: **Silver Gold Bull DK** (JS-rendered SPA). Dropped: **Mønthuset** (empty live category).

Coin coverage is bullion-only via the registry in `backend/app/coins.py`: Krugerrand, Maple Leaf, Vienna Philharmonic, American Eagle, Britannia, Sovereign, Ducat, Panda, plus the Danish 20 kr / 10 kr (Christian IX, Christian X, Frederik VIII). Listings whose title doesn't match the registry are silently skipped.

## Frontend

Installable PWA. Mobile: swipe right from the left edge to open the side menu; right-to-left to close (plus tap-outside).

- **Prices** — bars ranked by total price + premium %. Click a row for inline 24h/7d/30d charts + "Buy now or wait?" (today's premium vs dealer's 30-day IQR + lowest).
- **Coins** — every recognized in-stock bullion coin, ranked by premium. Same inline charts on row click.
- **Portfolio** (signed in) — per-row P&L, total/value/PnL split by metal, value-over-time chart with 1W/1M/6M/1Y/all range pills and deposit-adjusted change. CSV export.
- **Alerts** (signed in) — premium-threshold alerts with current-min enrichment; add/edit dialog previews the live min so you can pick a sensible threshold.
- **Reports** — archive of weekly + monthly HTML reports plus on-demand "Last week" / "Last month" generation (not archived).
- **Settings** — paste the X-API-Key on first run; pick light/dark theme.
- **Spot card** — auto-refreshes every 30 s and surfaces "Snapshots: N min ago" so a stalled cron is visible.

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
# Backend (no DB needed for /prices, /coins, /spot)
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Linux/Mac: .venv/bin/activate
pip install -r requirements.txt
API_KEY=test uvicorn app.main:app --reload

# Frontend (separate shell)
cd frontend && python -m http.server 5500
```

Open `http://127.0.0.1:5500/`, ☰ → Settings, paste the API key. Backend URL lives in `frontend/config.js` (defaults to `http://127.0.0.1:8000`; Cloudflare Pages overwrites at build time via `BACKEND_URL`).

For portfolio/history/reports/alerts, also bring up local Postgres + seed:

```bash
docker compose up -d   # Postgres on localhost:5433
cd backend
DATABASE_URL='postgresql://gold:gold@localhost:5433/goldtracker' .venv/Scripts/python.exe -m scripts.seed

API_KEY=test DATABASE_URL='postgresql://gold:gold@localhost:5433/goldtracker' \
  MAGIC_LINK_BASE_URL=http://127.0.0.1:5500 MAGIC_LINK_DEV_PRINT=1 \
  uvicorn app.main:app --reload
```

`MAGIC_LINK_DEV_PRINT=1` logs sign-in + alert emails to stdout instead of sending via Resend.

### Verification

```bash
ruff check app tests
mypy app
pytest tests/unit -v          # pure logic
pytest tests/api -v           # FastAPI against real Postgres (skipped if DATABASE_URL unset)
```

CI runs all four on every push to `main`. Live integration suite (`tests/integration/test_live.py`) hits real dealer sites Mondays 09:00 UTC + on workflow_dispatch.

## Environment variables

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

Three Upstash QStash schedules drive the cron-only endpoints. Free tier covers ~75 messages/day. QStash forwards `X-Api-Key` via the schedule's `Upstash-Forward-X-Api-Key` header setting. Destinations must point at `https://yzeir-gold.duckdns.org/...` — update the schedule URLs if the backend host ever changes.

| Schedule | Cron (UTC) | Target |
|---|---|---|
| Snapshot | `*/20 * * * *` | `POST /snapshot` |
| Weekly report | `30 22 * * 0` (Sun 22:30 UTC ≈ Mon 00:30 CPH) | `POST /reports/cron/weekly` |
| Monthly report | `30 0 1 * *` (1st at 00:30 UTC) | `POST /reports/cron/monthly` |

## Security notes

- **Auth.** Magic links SHA-256 hashed at rest (15-min TTL, single-use, rate-limited). Sessions are opaque UUIDs in `localStorage` with a 90-day sliding TTL — bearer-token only, so no CSRF risk. Logout revokes immediately.
- **XSS.** Strict CSP in `frontend/_headers`; `escapeHtml` at every `innerHTML` insertion of scraper / user content; report renderer `</`-escapes its JSON sidecar.
- **Snapshot defense.** Stale-FX guard refuses writes if FX fell back to the stamped rate. Outlier guard rejects >10% spot deviations within an hour. Scraper outliers (bar premium outside [0, 80%], coin outside [0, 120%]) flip to `status='error'` before persistence. All log `snapshot_skipped` / `scraper_outlier` for grep.
- **Alerts.** Per-user cap of 8 fire-events/hour. Resend calls happen *after* the snapshot commits — a hung upstream can't roll back data. A Resend failure for one user leaves their alerts un-muted (retries next tick) and doesn't poison the loop for others.

## Production deploy

- **Backend:** FastAPI in Docker on an Oracle Cloud Always-Free VM (Frankfurt), behind Caddy with auto-renewing Let's Encrypt TLS. Public URL: `https://yzeir-gold.duckdns.org`. VM layout: dir `~/apps/gold-price-tracker/`, container `gold-price-backend`. Shares the VM with `net-tracker` on a single Caddy + shared `apps_web` Docker network. Python pinned via `backend/runtime.txt` (3.12.7). `SCHEMA_SQL` runs idempotently on every backend boot.
- **Frontend:** Cloudflare Pages. `BACKEND_URL` env var injected at build time into `frontend/config.js`. CSP `connect-src` in `frontend/_headers` must include the backend host.
- **DB:** Neon Postgres (separate DB from net-tracker).
- **Email:** Resend.
- **Deploying:** use the `deploy` skill — say "deploy" / "ship it" in a Claude Code conversation in this repo. The skill SSHes into the VM, pulls `origin/main`, rebuilds the Docker image, restarts the container, and verifies the public health endpoint. See `.claude/skills/deploy/SKILL.md`. One-time per machine: create `.claude/skills/deploy/deploy.env.local` (gitignored) with the VM connection details — format in the SKILL.md "Setup" section.
- **Cron:** Upstash QStash schedules must point at the new `https://yzeir-gold.duckdns.org/...` URLs. Update them in the QStash dashboard if the backend host ever changes.
