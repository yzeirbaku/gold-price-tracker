# Gold Bar Tracker — Claude guide

Personal PWA that compares gold-bar prices (2.5 / 5 / 10 / 20 g) and gold-coin prices (≤ 20 g of fine gold) across Danish online dealers, plus a live spot ticker for gold and silver. Online-only by design — every load of the bars view is a fresh scrape; the coins view + per-row history charts read from a Postgres snapshot table populated by a 20-min cron.

## Git

**All commits in this repo must be authored as `yzeirbaku@hotmail.com` (name: `Yzeir Baku`).** This is already set in the local repo config — do not change it, and verify with `git config user.email` before committing if anything looks off.

Co-author trailers from Claude Code's default workflow are fine; the *author* must remain the hotmail address.

## Architecture

```
backend/         FastAPI on Render free tier (Python 3.12)
  app/
    main.py            FastAPI app + CORS + endpoints
    auth.py            X-API-Key header check (constant-time compare)
    auth_session.py    magic-link auth: issue/verify, sessions, rate limit, require_session
    portfolio.py       per-user purchase CRUD + P&L assembly (cookie-auth)
    email.py           Resend wrapper for the magic-link email (with MAGIC_LINK_DEV_PRINT bypass)
    fx.py              USD→EUR/DKK via frankfurter.dev (+ stamped fallback + historical)
    spot.py            current spot via api.gold-api.com; historical via yfinance (GC=F / SI=F)
    orchestrator.py    fan-out bar scrapers, compute premium %, sort
    models.py          Pydantic response models — Listing + CoinListing
    coins.py           static registry of recognized bullion coin types + resolver
    buy_context.py     "buy now or wait?" stats for a single bar/coin (history-driven)
    db.py              asyncpg pool + idempotent SCHEMA_SQL bootstrap
    scrapers/
      base.py          DealerScraper Protocol, DEFAULT_HEADERS, parse_dkk_price
      registry.py      ALL_SCRAPERS (bars) + ALL_COIN_SCRAPERS
      tavex.py / tavex_coins.py
      vitusguld.py / vitusguld_coins.py
      plaza.py / plaza_coins.py
      nordiskguld.py / nordiskguld_coins.py    (Simply.com WAF — needs Sec-* headers)
      seroguld.py / seroguld_coins.py          (same WAF)
      nyfortuna.py / nyfortuna_coins.py
      janjorgensen.py / janjorgensen_coins.py
    reports/           HTML report generation (windows, loader, analytics, tables, notable, renderer, builder, storage)
  scripts/
    seed.py            local-only: 30 days of fake bar+spot data into local Postgres
  tests/
    unit/              fixtures-driven, no network — runs in CI on every push
    integration/       hits real dealer sites — weekly cron + manual dispatch
    fixtures/          frozen HTML snapshots per dealer (one for bars, one for coins)

frontend/        Static PWA on Cloudflare Pages (vanilla JS, no build)
  index.html           ☰ slide-in nav (Prices / Reports / Settings), Bars/Coins
                       tab strip, Prices and Reports views, settings dialog
  app.js               menu drawer + tabs + size picker (bars) + ranked list
                       (coins) + inline history + Reports archive UI
  config.js            window.BACKEND_URL — overwritten by CF Pages build script
  styles.css
  service-worker.js    minimal — required for iOS install, no caching
  manifest.webmanifest
```

URL bar stays at `/` for every view — Prices ↔ Reports switch via the menu,
no pushState/hash routing.

Postgres tables (Neon in prod, local Docker in dev):
- `bar_snapshots` (was `dealer_snapshots` until 2026-05-08; idempotent rename in `db.py`)
- `coin_snapshots`
- `spot_snapshots`
- `report_archive` — rendered HTML reports, keyed by (`report_type`, `period_start`), upsert on conflict
- `users` — email-keyed user records (magic-link signup)
- `magic_links` — one-time SHA-256-hashed tokens, 15-min TTL, single-use, `created_ip` for rate-limit
- `sessions` — opaque UUID cookie tokens, 90-day sliding TTL via `last_seen_at`
- `purchases` — per-user purchase rows; `spot_at_purchase_dkk_per_g` frozen at write

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET  | `/` | none | health ping |
| GET  | `/prices/{size}` | `X-API-Key` | size ∈ {2.5, 5, 10, 20}; live bar scrapes + spot + FX in parallel |
| GET  | `/spot` | `X-API-Key` | spot + FX only — used by the auto-refreshing ticker every 20 s |
| GET  | `/coins` | `X-API-Key` | live fan-out to all coin scrapers + spot, sorted by premium % asc |
| GET  | `/history/bar/{dealer}/{size}` | `X-API-Key` | bar price/premium time series (`?range=24h\|7d\|30d`) |
| GET  | `/history/coin/{dealer}/{coin_type}/{fine_gold_g}` | `X-API-Key` | coin price/premium time series |
| POST | `/snapshot` | `X-API-Key` | runs all scrapers + spot + coins, writes to Postgres (cron-only) |
| GET  | `/reports` | `X-API-Key` | list archived weekly + monthly reports (no html column) |
| GET  | `/reports/{id}` | `X-API-Key` | download a stored report as .html attachment |
| POST | `/reports/generate?range=week\|month` | `X-API-Key` | on-demand report, streamed back, not persisted |
| POST | `/reports/cron/{type}` (`weekly`\|`monthly`) | `X-API-Key` | cron-only — generate + upsert into `report_archive` |
| GET  | `/health` | `X-API-Key` | runs every bar scraper at 5 g, returns per-dealer pass/fail |
| POST | `/auth/request-link` | none | issue a magic-link email; always 204, rate-limited |
| POST | `/auth/verify` | none | exchange a magic-link token for a session cookie |
| POST | `/auth/logout` | session cookie | delete the session row + clear cookie |
| GET  | `/auth/me` | session cookie | returns `{user_id, email}` or 401 |
| GET  | `/portfolio` | session cookie | the user's purchases + summary (live spot-driven P&L) |
| POST | `/portfolio` | session cookie | create a purchase; freezes historical spot at write |
| PATCH | `/portfolio/{id}` | session cookie | edit; re-freezes spot if `purchased_at` or `metal` change |
| DELETE | `/portfolio/{id}` | session cookie | hard delete; 404 if not the caller's row |

## Dealers

Live (bars): **Tavex, Vitus Guld, Plaza, Nordisk Guld, Sero Guld, Nyfortuna, Jan Jørgensen**.
Live (coins): **Tavex, Vitus Guld, Nordisk Guld, Sero Guld, Nyfortuna**. Plaza and Jan Jørgensen don't currently stock bullion coins; their coin scrapers run anyway and return empty.
Dropped: **Mønthuset** (live category empty since early 2025).
Skipped: **Silver Gold Bull DK** (JS-rendered React SPA + Algolia + dynamic pricing).

## Coins

Coin coverage is **bullion-only** via the static registry in `app/coins.py`: Krugerrand, Maple Leaf, Vienna Philharmonic, American Eagle, Britannia, Sovereign, Ducat, Panda, plus the Danish Scandinavian-Monetary-Union **20 kr** and **10 kr** (Christian IX, Christian X, Frederik VIII — same physical spec, tracked as separate `size_label`s so per-monarch premiums surface independently). Each entry pins `(gross_weight_g, purity)` per recognized size variant; `fine_gold_g = gross × purity` is the canonical size axis. Listings whose title doesn't match the registry are silently skipped — that's by design.

The 20 g fine-gold cap excludes all 1 oz coins (1 oz Krugerrand/Eagle = 31.1 g fine, 1 oz Maple/Phil/Britannia = 31.1 g fine). Easy to lift later by raising `FINE_GOLD_CAP_G` in each coin scraper.

**`GET /coins` is live, not snapshot-backed.** Every request fans out to all
`ALL_COIN_SCRAPERS` + spot + FX in parallel and computes premiums against
the just-fetched spot. The 20-min `POST /snapshot` cron still writes to
`coin_snapshots` for `/history/coin/...` and for report aggregation; this
endpoint just no longer reads from that table. Mirrors the way `/prices/{size}`
works for bars.

## Reports

`POST /reports/cron/{type}` (`weekly`|`monthly`) builds an HTML report from
`bar_snapshots` + `coin_snapshots` + `spot_snapshots` over the previous
calendar week (Mon–Sun) or month, in Europe/Copenhagen. The rendered HTML
is upserted into `report_archive` keyed by (`report_type`, `period_start`)
— re-running for the same period overwrites cleanly. `POST /reports/generate?range=week|month`
is the on-demand variant: rolling last-7 / last-30 days, streamed back to
the client, **not persisted**. `GET /reports` lists archived entries and
`GET /reports/{id}` returns the stored HTML as a `Content-Disposition:
attachment` download.

Each report has eight sections — Header, Spot context, Dealer behavior
fingerprints, Bars, Coins, Notable, Time-of-month drift (monthly only;
gated on `window.is_calendar_aligned` so rolling 30-day on-demand reports
omit it), and a hidden `<script type="application/json" id="report-data">`
sidecar holding every numeric value for future programmatic extraction.
The visual sections render as `<details>` elements (only Spot starts open).
The header shows "Weekly Report" / "Monthly Report" on the first line with
"DD-MM-YYYY HH:MM → DD-MM-YYYY HH:MM" below it. The sidecar JSON is
`</`-escaped before injection so dealer-controlled strings can't break out
of the `<script>` block.

Backend module layout (`app/reports/`): `windows.py` (period boundary math),
`loader.py` (typed dataclass loaders from DB), `analytics.py` (cadence,
weekend activity, time-of-day, day-of-week, premium band IQR, spot tracking
correlation/lag/sensitivity, fingerprint classifier), `tables.py` (per-size
bar + per-coin-variant tables), `notable.py` (threshold-driven bullet
generator + time-of-month drift), `renderer.py` (Jinja2), `builder.py`
(orchestrator), `storage.py` (CRUD on `report_archive`), `templates/report.html`.

`scripts/seed.py` truncates the four snapshot tables, fills 30 days of
synthetic data, then calls `build_report` for previous-week + previous-month
and upserts both so the local archive is non-empty on first PWA load.

## Portfolio + magic-link auth

Two parallel auth schemes coexist. The shared `X-API-Key` (`auth.require_api_key`)
gates everything from the original site — prices, history, spot, reports.
The session cookie (`auth_session.require_session`) gates only the personal
features added on top: the four `/auth/*` endpoints and `/portfolio*`. The
site works fully without logging in; sign-in only unlocks the portfolio.

**Sign-in flow:**
1. User submits email → `POST /auth/request-link` rate-limits (3/10min per
   email, 30/hour per IP), inserts a `magic_links` row holding only
   `sha256(token)`, and sends the raw token in an email via Resend.
   Always returns 204 — never reveals whether the email exists.
2. User clicks the link `https://.../#auth=<token>`. The frontend extracts
   the token from the URL fragment (never hits the server in proxies/logs)
   and `POST /auth/verify`s it. Backend looks up the hash inside a
   `FOR UPDATE` transaction, marks `used_at`, upserts the user, and sets a
   90-day `session` cookie (`HttpOnly Secure SameSite=None` in prod;
   `SameSite=Lax` for `http://` local dev — detected from `FRONTEND_ORIGIN`).
3. Verify tab writes `localStorage.gold-tracker.session = '1'` to broadcast
   the new session; the original "Check your inbox" tab picks it up via the
   `storage` event and transitions to logged-in.

**Portfolio P&L math:**
- `fine_weight_g = gross_weight_g × purity` (always derived; row stores gross + purity).
- `purchase_premium_pct` = (paid − frozen_spot_dkk_per_g × fine_g) / (frozen_spot × fine_g) × 100.
- `current_value_dkk` = current_spot_dkk_per_g × fine_g.
- `pnl_dkk` = current_value − paid; `pnl_pct` = pnl / paid × 100.

**Historical spot for `spot_at_purchase_dkk_per_g`:** fetched once at
write-time and frozen onto the row. **api.gold-api.com does *not* expose
historical** (despite the early design assumption), so we use yfinance
with `GC=F` (gold futures) and `SI=F` (silver futures) — they track spot
to fractions of a percent, well below dealer bid/ask spreads. USD→DKK
historical comes from frankfurter.dev's `/v1/{date}` endpoint. Both walk
back up to 7 days through weekends/holidays. The PATCH endpoint
re-freezes the spot if `purchased_at` *or* `metal` changes.

**CORS + cookies:** because the backend (Render) and frontend (Cloudflare
Pages) are on different sites, the session cookie requires
`SameSite=None; Secure`. That in turn requires `allow_credentials=True`,
which means `allow_origins` MUST be an explicit URL (not `*`). The
backend hard-fails at startup if `FRONTEND_ORIGIN=*`.

## Cron

Three schedules in **Upstash QStash** drive the cron-only endpoints:

| Schedule | Cron (UTC) | Target |
|---|---|---|
| Snapshot | `*/20 * * * *` | `POST /snapshot` |
| Weekly report | `30 22 * * 0` (Sun 22:30 UTC ≈ Mon 00:30 CPH) | `POST /reports/cron/weekly` |
| Monthly report | `30 0 1 * *` (1st of month at 00:30 UTC) | `POST /reports/cron/monthly` |

QStash forwards the `X-Api-Key` header via `Upstash-Forward-X-Api-Key` set
on the schedule. Free tier covers our ~75 messages/day; 2-min delivery
timeout absorbs Render free-tier cold starts. Managed via the QStash REST
API (the dashboard UI didn't expose custom headers when we set this up).

The previous GitHub Actions–based scheduler (`.github/workflows/snapshot.yml`)
was removed on 2026-05-11 because GH Actions cron is best-effort on free
tier and was dropping runs.

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
export DATABASE_URL='postgresql://gold:gold@localhost:5433/goldtracker'
.venv/Scripts/python.exe -m scripts.seed

# Start the backend with DATABASE_URL set so /snapshot and /history work
API_KEY=test DATABASE_URL='postgresql://gold:gold@localhost:5433/goldtracker' \
  uvicorn app.main:app --reload
```

The seed uses `random.seed(42)` so re-running gives identical data. It TRUNCATEs `bar_snapshots`, `coin_snapshots`, and `spot_snapshots` on every run.

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
- **Coins use a static registry.** `app/coins.py` is the source of truth for which coins we recognize. The resolver does case-insensitive substring matching; aliases live in `_TYPE_ALIASES` and `_SIZE_ALIASES`. Add to those dicts to widen coverage, not to the scraper code. Real-world Danish/German spelling drift ("Wiener Philharmoniker", "Amerikansk Eagle", "American Gold Eagle") tends to surface during the first run against a new dealer's fixture.
- **Coin scrapers don't pick a single cheapest variant per dealer.** Unlike bar scrapers, coin scrapers emit every recognized in-stock coin from the listing page; the global ranked `/coins` view sorts them across all dealers.
- **Bars table was renamed `dealer_snapshots` → `bar_snapshots`** on 2026-05-08. The schema bootstrap in `app/db.py` includes an idempotent migration block that runs on every backend startup; safe to re-run.
- **Bar history endpoint was renamed `/history/dealer/...` → `/history/bar/...`** at the same time, for symmetry with `/history/coin/...`. The PWA is the only client, so no compatibility shim.
- **Buttons**: two styles, one rule. **Cancel / Close / non-affirmative → neutral**. **Save / Add / Submit / affirmative → gold gradient** (same yellow as the active size pill). Inside a dialog `<menu>`, the neutral is automatic — `dialog menu button` styles it. For the gold variant inside a dialog, give the button `value="save"` (the `dialog menu button[value="save"]` rule paints it). For standalone buttons **outside** any dialog, use `class="site-btn"` (neutral) or `class="site-btn-primary"` (gold). All four classes live in `styles.css`. The active-pill gold gradient is also used as a *state* indicator on the tab strip / size picker — that's the same colour by design.
- **Date format**: site-wide canonical display format is `DD-MM-YYYY` (date only) or `DD-MM-YYYY HH:MM` (when a timestamp is needed). Use `fmtDate(iso)` from `app.js`. Don't use `toLocaleDateString` directly for user-facing dates; that drifts by locale. Existing report archive list still uses ISO (`YYYY-MM-DD`) inside the report content itself — that's separate, internal to the report renderer.

## Adding a new bar scraper

1. Drop a fixture in `backend/tests/fixtures/<dealer>_listing.html`.
2. Add `backend/app/scrapers/<dealer>.py` implementing the `DealerScraper` Protocol — return `Listing | None` (`None` = size not offered).
3. Register it in `scrapers/registry.py` → `ALL_SCRAPERS`.
4. Add a unit test in `backend/tests/unit/scrapers/` using the fixture; cover at least one in-stock and one out-of-stock case.
5. Add the dealer to the live smoke test in `tests/integration/test_live.py`.
6. Update the README + this file's "Dealers" section.

## Adding a new coin scraper

1. Capture a fixture from the dealer's gold-coin category page into `backend/tests/fixtures/<dealer>_coins.html` (use `httpx` with `DEFAULT_HEADERS` if curl is blocked by a WAF).
2. Audit which titles `coins.resolve()` recognizes — extend `_TYPE_ALIASES` / `_SIZE_ALIASES` in `app/coins.py` if you see Danish/German/English variants that should match but don't.
3. Add `backend/app/scrapers/<dealer>_coins.py` modeled on `tavex_coins.py`. Return `list[CoinListing]`.
4. Register in `scrapers/registry.py` → `ALL_COIN_SCRAPERS`.
5. Add unit test in `backend/tests/unit/scrapers/test_<dealer>_coins.py` — assert at least one recognized coin type is found and that all results are ≤ 20 g fine.
6. Update README + this file's "Dealers" / "Coins" sections.
