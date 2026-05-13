# Gold Prices Tracker

Personal tool to compare gold-bar prices (2.5 / 5 / 10 / 20 g) and gold-coin prices (≤ 20 g of fine gold) across Danish online dealers, plus live spot prices for gold and silver. Optional sign-in unlocks a per-user portfolio with live P&L.

## Architecture

- **Backend** — Python + FastAPI on Render free tier. `backend/`
- **Frontend** — Static PWA on Cloudflare Pages. `frontend/`
- **Spot price (live)** — api.gold-api.com (USD/oz, free, no key)
- **Spot price (historical, portfolio)** — yfinance `GC=F` / `SI=F` futures (free, no key; tracks spot within fractions of a percent)
- **FX** — frankfurter.dev (USD→EUR, USD→DKK) — live and historical
- **Auth** — passwordless magic-link email via Resend; opaque session cookies (Postgres-backed, 90-day sliding TTL)

## Dealers wired

| Dealer | Status |
|---|---|
| Tavex | ✓ live |
| Vitus Guld | ✓ live |
| Plaza | ✓ live |
| Nordisk Guld | ✓ live (needed Sec-Ch-Ua / Sec-Fetch-* headers to bypass Simply.com WAF) |
| Sero Guld | ✓ live (same WAF as Nordisk; stock varies) |
| Nyfortuna | ✓ live |
| Jan Jørgensen | ✓ live |
| Mønthuset | dropped — live guldbarrer category empty since early 2025 |
| Silver Gold Bull DK | skipped — JS-rendered React SPA + Algolia + dynamic pricing |

Coin coverage is bullion-only via a static registry in `backend/app/coins.py`: Krugerrand, Maple Leaf, Vienna Philharmonic, American Eagle, Britannia, Sovereign, Ducat, Panda, plus the Danish Scandinavian-Monetary-Union 20 kr and 10 kr (Christian IX, Christian X, Frederik VIII) which still trade as quasi-bullion. Listings whose title doesn't match the registry are skipped. Plaza and Jan Jørgensen don't currently stock bullion coins; their coin scrapers return empty lists and pick up new listings automatically if the dealers ever expand.

## API endpoints

| Method | Path | Notes |
|---|---|---|
| GET  | `/`                                                          | unauthenticated health ping |
| GET  | `/prices/{size}`                                             | live bar prices for {2.5, 5, 10, 20} g |
| GET  | `/spot`                                                      | live gold + silver spot in EUR/DKK per g |
| GET  | `/coins`                                                     | live fan-out to all coin scrapers + spot, sorted by premium |
| GET  | `/history/bar/{dealer}/{size}?range=24h\|7d\|30d`             | bar price history (renamed from `/history/dealer/...`) |
| GET  | `/history/coin/{dealer}/{coin_type}/{fine_gold_g}?range=...`  | coin price history |
| POST | `/snapshot`                                                  | runs all scrapers + spot, persists to Postgres (cron-only) |
| GET  | `/reports`                                                   | list archived weekly + monthly reports |
| GET  | `/reports/{id}`                                              | download a stored report as .html attachment |
| POST | `/reports/generate?range=week\|month`                         | on-demand report (not persisted) |
| POST | `/reports/cron/{type}` (`weekly` or `monthly`)                | cron-only — generate + persist to archive |
| GET  | `/health`                                                    | per-scraper pass/fail summary |
| POST | `/auth/request-link`                                         | issue a magic-link email; always 204; rate-limited (3/10min/email, 30/hr/IP) |
| POST | `/auth/verify`                                               | exchange a magic-link token for a session cookie |
| POST | `/auth/logout`                                               | delete the session row + clear cookie |
| GET  | `/auth/me`                                                   | `{user_id, email}` or 401 |
| GET  | `/portfolio`                                                 | current user's purchases + summary (live spot-driven P&L) |
| POST | `/portfolio`                                                 | create a purchase; freezes historical spot at write time |
| PATCH | `/portfolio/{id}`                                           | edit; re-freezes spot if `purchased_at` or `metal` change |
| DELETE | `/portfolio/{id}`                                          | hard delete; 404 if not the caller's row |

Endpoints up through `/health` use `X-API-Key` (legacy shared secret). The `/auth/*` and `/portfolio*` endpoints use a session cookie set during magic-link verification. Both schemes coexist — the site works fully without signing in.

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

Open `http://127.0.0.1:5500/`, click the ☰ hamburger menu → **Settings**, paste your API key. The backend URL is set in `frontend/config.js` (defaults to `http://127.0.0.1:8000` for local dev; Cloudflare Pages overwrites it at build time via the `BACKEND_URL` env var).

## Environment variables (Render)

| Var | Required | Notes |
|---|---|---|
| `API_KEY` | yes | Shared secret. PWA sends as `X-API-Key`. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `FRONTEND_ORIGIN` | no | Your Cloudflare Pages URL, e.g. `https://gold-tracker.pages.dev`. Defaults to `*` (permissive). Auth is bearer-token-based so a wildcard origin doesn't open a credential-leak path; explicit is just tidier. |
| `DATABASE_URL` | yes (for portfolio + history + reports) | Neon Postgres connection string. Without it, only `/prices`, `/spot`, `/coins` work. |
| `RESEND_API_KEY` | yes (for portfolio sign-in) | Resend API key (`re_...`). Get it from resend.com. |
| `MAGIC_LINK_BASE_URL` | yes (for portfolio sign-in) | Frontend origin used to build the `#auth=` URL in the email, e.g. `https://gold-tracker.pages.dev`. |
| `RESEND_FROM` | no | Override the From address. Defaults to `Gold Tracker <onboarding@resend.dev>` (Resend's shared sender; no DNS setup). |
| `MAGIC_LINK_DEV_PRINT` | no (dev only) | Set to `1` locally to log the magic link to stdout instead of sending via Resend. Saves quota during testing. |

## Portfolio + magic-link auth

Optional sign-in unlocks a per-user portfolio. Anyone with a valid email can sign in — there's no password to remember.

**The flow:**

1. Open the ☰ menu → **Sign in**, enter an email, click **Send link**.
2. A one-time link arrives (valid 15 minutes). Click it; that tab signs in and the original tab picks up the new session via a `localStorage` broadcast.
3. **Portfolio** appears in the menu. Click **+ Add purchase** to log a bar or coin.

**What gets tracked per purchase:** metal (gold/silver), gross weight, purity (so fine weight = gross × purity), price paid in DKK, purchase date, free-text label, optional dealer + notes. Spot at the purchase date is fetched from yfinance (`GC=F` / `SI=F`) plus historical USD→DKK from frankfurter.dev and frozen onto the row at write-time, so the cost-basis premium stays stable forever. Current value uses live spot at read time.

**Authentication architecture:** SHA-256 hashed one-time tokens in `magic_links` (15-min TTL, single-use, rate-limited at 3/10min per email and 30/hr per IP); opaque UUID session tokens in `sessions` with a 90-day sliding TTL via `last_seen_at`. Sessions are transported as `Authorization: Bearer <token>` (the verify endpoint returns the token; the frontend stashes it in `localStorage` and sends it on every authed call). We don't use cookies because the backend and frontend live on different sites and modern browsers (Safari ITP, Brave, Firefox ETP) silently drop cross-site cookies. No passwords stored anywhere. Logout = `DELETE FROM sessions` + `localStorage.removeItem` and revokes immediately.

A strict CSP in `frontend/_headers` limits script and connect origins to mitigate the XSS surface that comes with localStorage-stored tokens.

The site continues to work fully without signing in — the existing X-API-Key endpoints (prices, history, spot, coins, reports) are untouched.

## Reports

Weekly and monthly HTML reports are generated by cron and stored in
Postgres (`report_archive` table). The frontend "Reports" hamburger menu
lists archived reports and offers on-demand "Last week" / "Last month"
generation that downloads a one-off report without archiving.

The Reports view splits the archive into Weekly and Monthly sections —
each collapsible, each with a filter dropdown (Year + Month for weekly,
Year for monthly). A week straddling two calendar months shows in both.

Each report is a self-contained HTML file with inline CSS, no external
assets, and an embedded `<script type="application/json" id="report-data">`
sidecar containing every numeric value for future processing. Inside the
report, Spot context / Dealer fingerprints / Bars / Coins / Notable /
Time-of-month drift each render as `<details>` so they can be expanded
or collapsed individually; only Spot starts open. The header shows
"Weekly Report" (or "Monthly Report") on one line, with the period
"DD-MM-YYYY HH:MM → DD-MM-YYYY HH:MM" below it (Europe/Copenhagen).
Time-of-month drift only renders for canonical calendar-month windows —
rolling 30-day on-demand reports omit it, since the W1..W4 buckets
wouldn't map to real Mon–Sun weeks.

`scripts/seed.py` ends by generating one weekly + one monthly report into
the local archive so the UI isn't empty on first open.

## Cron

Upstash QStash drives the three cron-only endpoints: `/snapshot` every 20
minutes, `/reports/cron/weekly` on Sundays at 22:30 UTC, and
`/reports/cron/monthly` on the 1st of each month at 00:30 UTC. Free tier
covers ~75 messages/day with a 2-minute delivery timeout (handles Render
free-tier cold starts). Previously these were driven by GitHub Actions,
which was dropping runs on the free tier.

## Tests

- Unit (no network): `pytest tests/unit -v`
- Live integration (hits real dealer sites): `pytest tests/integration -v`
- Lint + types: `ruff check app tests && mypy app`
