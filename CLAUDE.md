# Gold Price Tracker — Claude guide

Personal PWA that compares gold-bar prices (2.5 / 5 / 10 / 20 g) and gold-coin prices (≤ 20 g of fine gold) across Danish online dealers, plus a live spot ticker for gold and silver. Online-only by design — every load of the bars view is a fresh scrape; the coins view + per-row history charts read from a Postgres snapshot table populated by a 20-min cron.

## Git

**All commits in this repo must be authored as `yzeirbaku@hotmail.com` (name: `Yzeir Baku`).** This is already set in the local repo config — do not change it, and verify with `git config user.email` before committing if anything looks off.

Co-author trailers from Claude Code's default workflow are fine; the *author* must remain the hotmail address.

## Architecture

```
backend/         FastAPI in Docker on an Oracle Cloud Always-Free VM, fronted by Caddy (Python 3.12)
  app/
    main.py            FastAPI app + CORS + endpoints
    auth.py            X-API-Key header check (constant-time compare)
    auth_session.py    magic-link auth: issue/verify, sessions, rate limit, require_session
    portfolio.py       per-user purchase CRUD + P&L assembly + worth-over-time (bearer-auth)
    alerts.py          per-user premium-threshold alerts: CRUD + evaluate_alerts hook
    email.py           Resend wrapper for magic-link + alert emails (with MAGIC_LINK_DEV_PRINT bypass)
    fx.py              USD→EUR/DKK via frankfurter.dev (+ stamped fallback + historical)
    spot.py            current spot via api.gold-api.com; historical via yfinance (GC=F / SI=F)
    orchestrator.py    fan-out bar scrapers, compute premium %, sort
    models.py          Pydantic response models — Listing + CoinListing
    coins.py           static registry of recognized bullion coin types + resolver
    buy_context.py     "buy now or wait?" stats for a single bar/coin (history-driven)
    db.py              asyncpg pool + idempotent SCHEMA_SQL bootstrap
    scrapers/
      base.py          DealerScraper Protocol, DEFAULT_HEADERS, parse_dkk_price
      simply_waf.py    Simply.com 454 proof-of-work solver + per-host clearance cache
      registry.py      ALL_SCRAPERS (bars) + ALL_COIN_SCRAPERS
      <dealer>.py / <dealer>_coins.py    one pair per dealer (Nordisk + Sero sit behind the Simply.com WAF)
    reports/           HTML report generation (windows, loader, analytics, tables, notable, renderer, builder, storage)
  scripts/
    seed.py            local-only: 30 days of fake bar+coin+spot data + one weekly + one monthly report into local Postgres
  tests/
    unit/              fixtures-driven, no network — runs in CI on every push
    integration/       hits real dealer sites — weekly cron + manual dispatch
    fixtures/          frozen HTML snapshots per dealer (one for bars, one for coins)

frontend/        Static PWA on Cloudflare Pages (vanilla JS, no build)
  index.html           ☰ side-drawer nav (Prices / Portfolio / Reports / Alerts / Settings)
                       with brand wordmark + dev credit, Bars/Coins tab strip,
                       five views, dialogs (purchase, alert, settings, login,
                       confirm), verify-view for magic-link landing
  app.js               menu drawer + edge-swipe gesture (open/close) + tabs +
                       size picker (bars) + ranked list (coins) + inline
                       history & buy-now-or-wait context + portfolio table
                       + worth-over-time chart (gradient fill) + CSV export
                       + alerts CRUD + magic-link verify
  config.js            window.BACKEND_URL — overwritten by CF Pages build script
  styles.css
  service-worker.js    minimal — required for iOS install, no caching
  manifest.webmanifest
  _headers             CSP + cache directives (Cloudflare Pages picks this up)
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
- `sessions` — opaque UUID bearer tokens, 90-day sliding TTL via `last_seen_at` (refresh debounced to 1h to avoid write amplification)
- `purchases` — per-user purchase rows; `spot_at_purchase_dkk_per_g` frozen at write
- `alerts` — per-user premium-threshold alerts; `kind` ∈ {bar, coin}; `muted_until_recovery` + `last_fired_at` + `fire_count` carry the fire/recover state machine

## Endpoints

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET  | `/` | none | health ping |
| GET  | `/prices/{size}` | `X-API-Key` | size ∈ {2.5, 5, 10, 20}; live bar scrapes + spot + FX in parallel |
| GET  | `/spot` | `X-API-Key` | spot + FX only — used by the auto-refreshing ticker every 20 s |
| GET  | `/coins` | `X-API-Key` | live fan-out to all coin scrapers + spot, sorted by premium % asc |
| GET  | `/history/bar/{dealer}/{size}` | `X-API-Key` | bar price/premium time series (`?range=24h\|7d\|30d`) |
| GET  | `/history/coin/{dealer}/{coin_type}/{fine_gold_g}` | `X-API-Key` | coin price/premium time series |
| GET  | `/context/bar/{dealer}/{size}` | `X-API-Key` | "buy now or wait?" — today's premium vs 30-day IQR + lowest; powers the buy-context panel |
| GET  | `/context/coin/{dealer}/{coin_type}/{fine_gold_g}` | `X-API-Key` | same, for a coin variant |
| GET  | `/snapshot/age` | `X-API-Key` | seconds since the most recent `spot_snapshots` row; powers the "Snapshots: N min ago" indicator |
| POST | `/snapshot` | `X-API-Key` | runs all scrapers + spot + coins, writes to Postgres (cron-only) |
| GET  | `/reports` | `X-API-Key` | list archived weekly + monthly reports (no html column) |
| GET  | `/reports/{id}` | `X-API-Key` | download a stored report as .html attachment |
| POST | `/reports/generate?range=week\|month` | `X-API-Key` | on-demand report, streamed back, not persisted |
| POST | `/reports/cron/{type}` (`weekly`\|`monthly`) | `X-API-Key` | cron-only — generate + upsert into `report_archive` |
| GET  | `/health` | `X-API-Key` | runs every bar scraper at 5 g, returns per-dealer pass/fail |
| POST | `/auth/request-link` | none | issue a magic-link email; always 204, rate-limited |
| POST | `/auth/verify` | none | exchange a magic-link token for a session bearer token |
| POST | `/auth/logout` | Bearer | delete the session row |
| GET  | `/auth/me` | Bearer | returns `{user_id, email}` or 401 |
| GET  | `/portfolio` | Bearer | the user's purchases + summary (live spot-driven P&L) |
| GET  | `/portfolio/history?range=…&metal=…` | Bearer | portfolio value over time + deposit-adjusted period change |
| POST | `/portfolio` | Bearer | create a purchase; freezes historical spot at write |
| PATCH | `/portfolio/{id}` | Bearer | edit; re-freezes spot if `purchased_at` or `metal` change |
| DELETE | `/portfolio/{id}` | Bearer | hard delete; 404 if not the caller's row |
| GET    | `/alerts` | Bearer | list user's alerts + current_min_premium_pct enrichment (batched: one query per kind across all targets) |
| GET    | `/alerts/options` | Bearer | bar sizes + coin registry for the dialog dropdowns |
| GET    | `/alerts/preview` | Bearer | preview current min premium for a prospective target — powers the dialog's "Current: X%" hint |
| POST   | `/alerts` | Bearer | create; bar requires size_g, coin requires (coin_type, fine_gold_g) |
| PATCH  | `/alerts/{id}` | Bearer | edit threshold/enabled; threshold change resets muted state |
| DELETE | `/alerts/{id}` | Bearer | hard delete; 404 if not the caller's row |

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

Sections (rendered as `<details>`, only Spot starts open): Header, Spot context, Dealer behavior fingerprints, Bars, Coins, Notable, Time-of-month drift (monthly only — gated on `window.is_calendar_aligned` so on-demand 30-day reports omit it). Plus a hidden `<script type="application/json" id="report-data">` sidecar holding every numeric value (`</`-escaped before injection so dealer-controlled strings can't break out).

`scripts/seed.py` truncates the four snapshot tables, fills 30 days of synthetic data, then calls `build_report` for previous-week + previous-month so the local archive is non-empty on first PWA load.

## Portfolio + magic-link auth

Two parallel auth schemes coexist. The shared `X-API-Key` (`auth.require_api_key`)
gates everything from the original site — prices, history, spot, reports.
The session bearer token (`auth_session.require_session`) gates only the
personal features added on top: the four `/auth/*` endpoints, `/portfolio*`,
and `/alerts*`. The site works fully without logging in; sign-in only
unlocks the portfolio and alerts.

**Sign-in flow:**
1. User submits email → `POST /auth/request-link` rate-limits (3/10min per
   email, 30/hour per IP), inserts a `magic_links` row holding only
   `sha256(token)`, and sends the raw token in an email via Resend.
   Always returns 204 — never reveals whether the email exists.
2. User clicks the link `https://.../#auth=<token>`. The frontend extracts
   the token from the URL fragment (never hits the server in proxies/logs)
   and `POST /auth/verify`s it. Backend looks up the hash inside a
   `FOR UPDATE` transaction, marks `used_at`, upserts the user, creates a
   `sessions` row, and returns `{user_id, email, token}` — `token` is the
   raw UUID from `sessions.id`.
3. Frontend writes the token to `localStorage` (`gold-tracker.session-token`)
   and broadcasts via `localStorage.gold-tracker.session = '1'`. The
   original "Check your inbox" tab picks it up via the `storage` event,
   re-reads `/auth/me` with the new token, and transitions to logged-in.
4. Subsequent authed calls send `Authorization: Bearer <token>` —
   `require_session` parses the header, validates against `sessions`, and
   slides `last_seen_at` forward only if it's older than
   `SESSION_LAST_SEEN_DEBOUNCE` (1h). A chatty UI hitting authed endpoints
   multiple times per minute fires at most one UPDATE per hour; the 90-day
   sliding TTL still slides forward on every active hour. Logout =
   `DELETE FROM sessions` + `localStorage.removeItem`.

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

**Same-day purchases use live spot, not yfinance.** When `purchased_at` lands on today's UTC date, `_fetch_historical_spot_dkk_per_g` short-circuits to `_current_spot_dkk_per_g`. yfinance's daily series only carries closed sessions, so "historical for today" would silently mean *yesterday's* close. The future-purchased-at validator rejects only when the UTC date is strictly tomorrow-or-later (with 5-min skew tolerance for midnight rollover); same-day stamps pass.

**Why bearer tokens, not cookies:** the backend (`yzeir-gold.duckdns.org`)
and frontend (Cloudflare Pages `*.pages.dev`) live on different sites.
Safari ITP, Brave, Firefox ETP, and Chrome (with 3rd-party cookies
disabled) all refuse to save a cross-site `SameSite=None; Secure` cookie
regardless of how correctly the headers are set. Bearer tokens in
`localStorage` sidestep that entirely and remove CSRF risk for free
(browsers don't auto-attach Authorization headers). Trade-off is XSS
resistance — mitigated with a strict CSP in `frontend/_headers` and
consistent `escapeHtml` on `innerHTML` insertion.

**Content-Security-Policy:** lives in `frontend/_headers` and ships with
Cloudflare Pages. Scripts: `'self'` + `https://cdn.jsdelivr.net` (Chart.js);
`connect-src` allowlists the backend host; `frame-ancestors 'none'` blocks
clickjacking. Update `connect-src` if the backend host ever changes.

## Alerts

Logged-in users can subscribe to **email alerts** that fire when a
cross-dealer minimum premium drops below a configured threshold. One
new table (`alerts`) + one module (`app/alerts.py`); evaluation is
called from `/snapshot` *after* the snapshot transaction commits — the
fx_stale + outlier guards have already gated whatever data lands in
the just-persisted bar_rows/coin_rows. Endpoints:

| Method | Path | Notes |
|---|---|---|
| GET    | `/alerts` | list user's alerts (with `current_min_premium_pct` + `current_best_dealer` enrichment from the most recent snapshot row within 90 min) |
| GET    | `/alerts/options` | bar sizes + coin registry (used by the dialog dropdowns; one-shot at view-open) |
| GET    | `/alerts/preview` | look up the current cross-dealer min for a prospective target (`?kind=bar&size_g=10` or `?kind=coin&coin_type=Krugerrand&fine_gold_g=15.55`). Powers the "Current: 8.34% (Dealer)" hint inside the add/edit dialog so the user can pick a sensible threshold without alt-tabbing. |
| POST   | `/alerts` | create; bar requires `size_g`, coin requires `(coin_type, fine_gold_g)`. The CHECK constraint on the table enforces shape; `_validate_kind_payload` returns a clean 400 first. |
| PATCH  | `/alerts/{id}` | edit threshold or enabled flag. Threshold edits **reset** `muted_until_recovery=FALSE` so a 7%→6% change doesn't stay stuck muted. |
| DELETE | `/alerts/{id}` | hard delete; 404 if not the caller's row |

**Cross-dealer matching only.** Bar alerts key on `size_g`; coin alerts on
`(coin_type, fine_gold_g)`. Premium is min across all `status='ok'` rows
in the just-persisted tick. The unit of matching is "what a buyer cares
about" — they want any 10g bar / any 1/2oz Krugerrand below threshold,
not a specific dealer. The `coins.resolve` function and
`alerts._index_coin_mins` MUST agree on the `round(_, 4)` /
`.quantize(Decimal("0.0001"))` for fine_gold_g, otherwise alert matching
silently buckets the same coin differently — there's a comment on
`coins.resolve` reminding future-you of the invariant.

**Dedup / state machine.** Each alert has a `muted_until_recovery` flag.
On fire: email sent → flag set TRUE + `last_fired_at = NOW()`. On any
subsequent tick where min premium climbs back above
`threshold + HYSTERESIS_PCT` (0.5%), flag flips back to FALSE — re-armed.
Standard fire-once-and-mute pattern; no spam during long flat-bottom dips.

**Bundling + rate limit.** Multiple alerts firing for the same user on
the same tick get bundled into **one email** with each alert as a
section. Per-user hard cap of `MAX_FIRES_PER_HOUR_PER_USER = 8`
alert-fires per rolling hour (a bundle of N alerts counts as N) —
prevents a flapping watch from carpet-bombing the inbox. Throttled
events log `alert_email_throttled` (structured JSON for log grep).

**Failure isolation.** If Resend fails for one user, their alerts stay
**un-muted** so the next tick retries; other users still get their
emails. Logged as `alert_email_failed` with user_id + alert_ids.
Evaluation runs *after* the snapshot transaction commits — so a slow or
hanging Resend HTTP call cannot hold the snapshot connection open and
cannot roll back snapshot data on timeout. The Resend SDK is synchronous;
`send_alert_email` wraps it in `asyncio.to_thread` so a slow upstream
blocks only the helper task, not the event loop.

**Why piggyback on `/snapshot`.** Fresh data already in scope, serial with persistence (snapshot commits first, alerts evaluate the committed rows), one scheduler. 20-min cadence is acceptable alert latency.

## Cron

Three schedules in **Upstash QStash** drive the cron-only endpoints:

| Schedule | Cron (UTC) | Target |
|---|---|---|
| Snapshot | `*/20 * * * *` | `POST /snapshot` |
| Weekly report | `30 22 * * 0` (Sun 22:30 UTC ≈ Mon 00:30 CPH) | `POST /reports/cron/weekly` |
| Monthly report | `30 0 1 * *` (1st of month at 00:30 UTC) | `POST /reports/cron/monthly` |

QStash forwards the `X-Api-Key` header via `Upstash-Forward-X-Api-Key` set on the schedule. Free tier covers our ~75 messages/day. Schedule destinations point at `https://yzeir-gold.duckdns.org/...` — update them in the QStash dashboard if the backend host ever changes. Managed via the QStash REST API (the dashboard UI didn't expose custom headers when we set this up).

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
- **Headers**: scrapers must use `DEFAULT_HEADERS` from `scrapers/base.py`. The full Sec-* fingerprint is still required for Nordisk + Sero — don't trim it — but as of 2026-08 it's no longer *sufficient*; see the Simply.com WAF bullet below.
- **Simply.com WAF (Nordisk + Sero).** Both dealers are hosted on Simply.com, whose WAF gates every path — `robots.txt` included — behind an HTTP **454** "Checking your browser" page carrying a SHA-256 proof-of-work challenge. Until 2026-08 the browser header fingerprint alone avoided it; when that stopped working, all four Nordisk/Sero scrapers (bars + coins) went to `http: HTTPStatusError` simultaneously. `scrapers/simply_waf.py` clears it: parse `var T=…,TS=…,D=…` out of the 454 body, find a nonce where `sha256(f"{token}:{nonce}")` has ≥ D leading zero bits, `POST` it to `/.sc-verify/`, then re-request with the returned `sc_clearance` cookie. Wired into `fetch_listing_html`, so it's transparent to every scraper and inert for the other five dealers (it only fires on a 454). Notes for future-you:
  - **The clearance is cached process-wide per host, not on the client.** Every httpx client here is built per-endpoint, so a cookie jar would drop the clearance after each request and re-solve on every page load. Cached 23h against the WAF's `max-age=86400`.
  - **Solving runs in `asyncio.to_thread`** — it's CPU-bound and the VM has one OCPU shared with net-tracker. Observed difficulty 16 ≈ 16–220 ms, far inside the 10 s scraper deadline.
  - **`MAX_DIFFICULTY = 24` is a CPU fence, not a tuning knob.** If the WAF ratchets difficulty past it we degrade those two dealers rather than pinning the CPU for every endpoint. A `FAILURE_BACKOFF_S` of 5 min stops a burst of page loads from re-attempting a handshake the WAF is already refusing.
  - If both dealers go to `http: HTTPStatusError` again, grep the logs for `simply_waf:` — `no challenge found` means the challenge markup moved, `verify rejected` means the protocol did.
- **Listing status**: `ok | out_of_stock | unavailable | error`. The frontend hides `out_of_stock` and surfaces `error`/`unavailable` as a single-cell row. `_safe_fetch` in the orchestrator turns any scraper exception into an `error` Listing — never let a scraper crash the response.
- **Premium %** = `(price_dkk - spot_dkk_per_g * size_g) / (spot_dkk_per_g * size_g) * 100`, computed in the orchestrator after spot resolves.
- **Sort**: `ok` rows by ascending price, then everything else (errors/unavailable) at the bottom.
- **No caching of API responses** — both the service worker and `app.js` are deliberately cache-free since prices are live. Static assets are cache-busted via `?v=N` query strings in `index.html`.
- **Number formatting (display)**: dots only, never commas. DKK prices use `da-DK` for thousand-grouping (`12.345 dkk`), spot uses `en-US` so the `.` is a decimal separator (`695.42 dkk`). See `fmtDKK` / `fmtSpotDKK` in `app.js`.
- **Number formatting (inputs)**: large-value DKK fields (currently only `#purchase-price`) use **Danish input format** — dot thousands, comma decimal (e.g. `8.000,50`). Wired via `installPriceFormatter(input)` in `app.js`; `parsePriceFromInput()` converts back to `Number` on submit. Field is `type="text" inputmode="decimal"` (not `type="number"`, which blocks dots/commas). Small numeric fields (gross weight, purity, threshold) stay `type="number"` — grouping doesn't help on 1–3 digits.
- **Backend URL on the frontend** comes from `window.BACKEND_URL` in `config.js`. Cloudflare Pages overwrites this file at build time:
  `echo "window.BACKEND_URL = '${BACKEND_URL}';" > frontend/config.js`
- **Coins use a static registry.** `app/coins.py` is the source of truth for which coins we recognize. The resolver does case-insensitive substring matching; aliases live in `_TYPE_ALIASES` and `_SIZE_ALIASES`. Add to those dicts to widen coverage, not to the scraper code. Real-world Danish/German spelling drift ("Wiener Philharmoniker", "Amerikansk Eagle", "American Gold Eagle") tends to surface during the first run against a new dealer's fixture.
- **Coin scrapers don't pick a single cheapest variant per dealer.** Unlike bar scrapers, coin scrapers emit every recognized in-stock coin from the listing page; the global ranked `/coins` view sorts them across all dealers.
- **Bars table was renamed `dealer_snapshots` → `bar_snapshots`** on 2026-05-08. The schema bootstrap in `app/db.py` includes an idempotent migration block that runs on every backend startup; safe to re-run.
- **Bar history endpoint was renamed `/history/dealer/...` → `/history/bar/...`** at the same time, for symmetry with `/history/coin/...`. The PWA is the only client, so no compatibility shim.
- **Buttons**: two styles, one rule. **Cancel / Close / non-affirmative → neutral**. **Save / Add / Submit / affirmative → gold gradient** (same yellow as the active size pill). Inside a dialog `<menu>`, the neutral is automatic — `dialog menu button` styles it. For the gold variant inside a dialog, give the button `value="save"` (the `dialog menu button[value="save"]` rule paints it). For standalone buttons **outside** any dialog, use `class="site-btn"` (neutral) or `class="site-btn-primary"` (gold). All four classes live in `styles.css`. The active-pill gold gradient is also used as a *state* indicator on the tab strip / size picker — that's the same colour by design.
- **Date format**: `DD-MM-YYYY` (or `DD-MM-YYYY HH:MM` with time). Use `fmtDate(iso)` from `app.js`. Don't use `toLocaleDateString` — it drifts by locale. ISO (`YYYY-MM-DD`) is fine inside the report content (internal to the renderer).
- **Busy buttons.** Any async-network button MUST be wrapped in `withBusy(btn, async () => {...}, 'Saving…')` from `app.js` — disables + swaps label, always restores in `finally`. Reason: a double-tap on Save fires two POSTs and the second surfaces as a confusing "duplicate" error. Omit `busyLabel` for icon-only buttons; pass a verb-ing form (`'Saving…'`, `'Sending…'`) for text buttons.
- **No backend leakage in user-facing errors.** Never show HTTP codes, raw bodies, or exception messages. Route every fetch failure through `userFacingError({ res?, body?, err?, fallback })` from `app.js` — maps known statuses, parses FastAPI `{detail: [...]}`, scrubs `Error`/`Exception`/`Traceback`, falls back to the call-site string. Pass `body` only when the caller already consumed `res.text()`. Use `infoDialog({title, message})` instead of native `alert()`.
- **Dialog focus ring on iOS.** Open dialogs via `openDialog(dlg, focusEl?)` from `app.js`, not `dlg.showModal()` directly. Reason: `showModal()` auto-focuses the first focusable element; on iOS Safari that paints a `:focus-visible` ring on Cancel which reads as "pre-selected." The helper blurs the auto-focused element on the next frame; Tab nav still works. Pass `focusEl` to focus a specific input after the blur.
- **`/snapshot` FX-stale guard.** `fx.py` falls back to a stamped USD→DKK rate when frankfurter.dev errors out; that rate drifts fast (a 7% gap once corrupted every premium chart for a tick — incident 2026-05-14). The cron-only `/snapshot` checks `any(r.fx_stale for r in results)` and returns `{"skipped": true, "reason": "fx_stale"}` without writing. `/spot` still serves the stale fallback for the live ticker (refreshes 30s later). Grep `sudo docker logs gold-price-backend` for `snapshot_skipped`.
- **`/snapshot` outlier guard.** Rejects the new tick if `gold_dkk_per_g` deviates by more than `SNAPSHOT_OUTLIER_THRESHOLD = 10%` from the most recent `spot_snapshots` row in the last 60 min (`main.py`). Same `snapshot_skipped` line with `reason: "outlier"`. 10% is deliberately wide — real gold rarely moves more than 2–3% in 20 min; this catches unit flips, near-zero glitches, and silently-wrong FX. Widen rather than disable if normal vol starts brushing it.
- **Historical FX and historical spot raise instead of falling back.** `fx.fetch_usd_to_dkk_on` raises `HistoricalFxUnavailable` and `spot.fetch_historical_usd_per_gram` raises `HistoricalSpotUnavailable` (also fires when yfinance lands outside sanity bounds — gold `[$30/g, $500/g]`, silver `[$0.20/g, $50/g]`). `portfolio._fetch_historical_spot_dkk_per_g` catches both and surfaces a 502 the frontend renders as retry-able. A brief retry beats baking a bad value into a `purchases` row forever.
- **Scraper outliers filtered before persistence.** `orchestrator.flag_bar_premium_outliers` flips any bar with premium outside `BAR_PREMIUM_BOUNDS_PCT = (0.0, 80.0)` to `status="error"` and emits a `scraper_outlier` log line. Coin equivalent with `(0.0, 120.0)`. Bounds are a "scraper grabbed the wrong HTML field" fence, not a market-vol fence — leaves headroom for small fractional coins (1/20oz routinely 50%+).
- **`_current_spot_dkk_per_g` self-protects with `LIVE_SPOT_TIMEOUT_S = 3.0`.** Wraps the two upstream calls (api.gold-api.com + frankfurter.dev) which would otherwise compound to ~20s. Raises `HTTPException(502)` on failure or timeout. `/portfolio/history` catches the 502 and degrades to snapshot-tail-only; list/create/update propagate it.
- **`GET /alerts` uses a batched current-min lookup.** `_fetch_current_bars_batch` + `_fetch_current_coins_batch` (`alerts.py`) each issue one UNNEST + ROW_NUMBER window query across every target — list endpoint is O(1) queries instead of O(N). Result shape matches the single-row `_decorate_with_current` path used by `create/update/preview`.
- **`GET /portfolio/history` reconstructs value-over-time on demand** — no new table. Joins `purchases` against `spot_snapshots` and walks a two-pointer aggregator. Appends a synthetic "now" point from live spot so the tail matches the summary card. Decimates to ≤ 500 points. Period change is Modified-Dietz (deposit-adjusted) so a pure cash injection shows 0%.

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

## Production deploy

- **Backend:** FastAPI in Docker on an **Oracle Cloud Always-Free VM** (Ubuntu 24.04, AMD E2.1.Micro, Frankfurt), fronted by **Caddy** with auto-renewing Let's Encrypt TLS. Public URL: `https://yzeir-gold.duckdns.org` (DuckDNS). VM layout: dir `~/apps/gold-price-tracker/`, container `gold-price-backend`, image `gold-price-tracker-backend`. Shares the VM with `net-tracker` on a single Caddy and a shared external Docker network `apps_web`.
- **Frontend:** Cloudflare Pages. `BACKEND_URL` env var injected at build time. The CSP `connect-src` allowlist in `frontend/_headers` MUST include the backend host — update both env var and CSP if the host ever changes.
- **DB:** Neon Postgres (separate DB from net-tracker).
- **Email:** Resend.
- **Cron:** Upstash QStash. Schedule destinations point at `https://yzeir-gold.duckdns.org/...`. If the backend host changes, update the destinations in the QStash dashboard.
- **Deploy = `git pull` on the VM + `docker compose up -d --build`.** Use the **`deploy` skill** (`.claude/skills/deploy/SKILL.md`) — natural-language triggers: "deploy", "ship it", "deploy gold-price". The skill reads VM connection details from the gitignored `.claude/skills/deploy/deploy.env.local` (template inside the SKILL.md "Setup" section). Pre-flight: code must be pushed to `origin/main` first.
- **VM-local files (not in this repo):** `Dockerfile` (in `repo/backend/`), `docker-compose.yml`, `.env`. Caddy's `Caddyfile` lives in `~/apps/net-tracker/` (shared between both backends) and proxies `yzeir-gold.duckdns.org` to `gold-price-backend:8000`. Reconstruct from the SKILL.md if the VM is ever recreated.
- **Secret rotation:** SSH in, edit `~/apps/gold-price-tracker/.env`, then `sudo docker compose -f ~/apps/gold-price-tracker/docker-compose.yml up -d --force-recreate backend`. The deploy skill does NOT touch `.env`.
