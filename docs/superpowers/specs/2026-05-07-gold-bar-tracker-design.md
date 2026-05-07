# Gold Bar Price Tracker — Design Spec

- **Date:** 2026-05-07
- **Status:** Approved (awaiting implementation plan)
- **Owner:** Yzeir
- **Repo:** to be created under user's **personal** GitHub account (not the Chronoshub work account)

## Goal

Compare 2.5 g, 5 g, and 10 g investment-gold bar prices across Danish online dealers from the user's iPhone. On every check, fetch live prices from each dealer in parallel, plus current spot prices for gold and silver, and present a sorted ranking — cheapest first — with each dealer's premium over spot.

The user currently does this manually by visiting ~9 dealer sites in turn. The goal is to replace that with one tap.

## Non-goals (YAGNI)

- No price history, charts, or trend tracking
- No price-alert notifications
- No multi-user support, accounts, or login UI
- No buying/transactions — listings link out to the dealer site
- No native iOS app
- No automatic background refresh — user-initiated only

## Constraints

- **Total cost: $0/month**, indefinitely. No paid tiers, no Apple Developer account.
- **Single user.** Accessed only from Yzeir's iPhone.
- **Personal GitHub account.** Project must not live in the Chronoshub work account.
- **On-demand only.** No scheduled scrapes or caching of dealer prices.
- **Display:** spot price in EUR + DKK; dealer listings in DKK only.

## Decisions made during brainstorming

| Question | Decision |
|---|---|
| Freshness model | On-demand live scrape every request |
| Sizes supported | 2.5 g, 5 g, 10 g |
| Dealers in scope | All 9 (Tavex, Vitus Guld, Plaza, Nordisk Guld, Sero Guld, Nyfortuna, Silver Gold Bull DK, Jan Jørgensen Smykker, Mønthuset) |
| Spot price source | metals.dev free tier, USD per troy ounce |
| FX source | frankfurter.app (ECB-backed, no key) |
| Spot display | EUR and DKK |
| Listings display | DKK only |
| iPhone access | PWA (Add to Home Screen) — no native app |
| Backend hosting | Render.com free web service tier |
| Frontend hosting | Cloudflare Pages |
| Code repo | Single monorepo on personal GitHub: `/backend` + `/frontend` |
| Auth | Shared-secret `X-API-Key` header |
| Scraper strategy | HTTP-only (httpx + selectolax). No headless browser. Revisit per-site only if a dealer turns out to be JS-rendered. |
| Backend stack | Python 3.12 + FastAPI |

## Architecture

Three independently-deployed pieces:

```
┌───────────────────────┐         ┌──────────────────────────────┐
│ iPhone (PWA)          │  HTTPS  │ Backend (Render free tier)   │
│ Cloudflare Pages      │────────▶│ Python + FastAPI             │
│ Static HTML/JS/CSS    │  +API   │                              │
│                       │  key    │  /prices/{size}, /spot,      │
│                       │◀────────│  /health                     │
└───────────────────────┘  JSON   │                              │
                                  │  Orchestrator fans out to:   │
                                  │   • 9 dealer scrapers        │
                                  │   • metals.dev (spot)        │
                                  │   • frankfurter.app (FX)     │
                                  │  All in parallel.            │
                                  └──────────────────────────────┘
```

The split lets either side be replaced without touching the other. If Render's free tier ever changes, swap to Hugging Face Spaces or Oracle Cloud Always Free with no frontend changes.

## Components

### Backend (`/backend`)

```
backend/
├── app/
│   ├── main.py              # FastAPI app, routes, auth wiring
│   ├── orchestrator.py      # parallel fan-out of scrapers + spot + FX
│   ├── models.py            # Pydantic types: Listing, SpotPrice, PriceResponse
│   ├── auth.py              # X-API-Key header check
│   ├── spot.py              # gold/silver spot from metals.dev
│   ├── fx.py                # USD→EUR, USD→DKK from frankfurter.app
│   └── scrapers/
│       ├── base.py          # DealerScraper protocol
│       ├── tavex.py
│       ├── vitusguld.py
│       ├── plaza.py
│       ├── nordiskguld.py
│       ├── seroguld.py
│       ├── nyfortuna.py
│       ├── silvergoldbull.py
│       ├── janjorgensen.py
│       ├── monthuset.py
│       └── registry.py      # ordered list of all scrapers
├── tests/
│   ├── fixtures/            # saved HTML snapshots per dealer
│   ├── unit/                # parser tests, no network
│   └── integration/         # live network tests, manual / weekly
└── requirements.txt
```

#### The scraper interface

Every adapter implements the same minimal contract. The orchestrator never branches on dealer identity.

```python
class DealerScraper(Protocol):
    name: str                      # human-readable, e.g. "Tavex"
    base_url: str                  # for the buy link

    async def fetch(
        self,
        size_g: float,
        client: httpx.AsyncClient,
    ) -> Listing | None:
        """Return a Listing or None if unavailable / out of stock / parse failed."""
```

When a dealer changes its HTML, only that one file is touched.

### Frontend (`/frontend`)

```
frontend/
├── index.html               # the entire UI
├── app.js                   # vanilla JS — fetch + render
├── styles.css
├── manifest.webmanifest     # PWA manifest (icon, name, theme color)
├── service-worker.js        # minimal SW (required for Add to Home Screen)
└── icons/                   # 192x192 + 512x512 PNGs
```

Deliberately no React, no build step, no npm. Cloudflare Pages serves the files directly.

## Data flow — single request

1. iPhone loads `index.html` from Cloudflare Pages (cached locally after first load).
2. User taps **5g**. PWA calls:
   `GET https://<app>.onrender.com/prices/5` with `X-API-Key: <secret>`.
3. **Cold-start note:** if the Render service has been idle ≥15 min, this first request takes ~30–60 s while it spins up. Frontend shows a spinner with a hint after 5 s.
4. Backend `auth.py` validates the header → 401 on miss.
5. `orchestrator.run(size_g=5.0)` launches 11 concurrent tasks under one `httpx.AsyncClient`:
   - 9 dealer scrapers
   - 1 spot fetch (gold + silver in USD/oz)
   - 1 FX fetch (USD→EUR, USD→DKK)

   Total request hard timeout: **12 s**. Per-task timeout: **8 s**.

6. Orchestrator builds the response. Failed scrapers become entries with `status: "error"`; the response always returns *something*.

   ```jsonc
   {
     "size_g": 5,
     "fetched_at": "2026-05-07T14:23:11Z",
     "spot": {
       "gold":   { "per_gram_eur": 78.42, "per_gram_dkk": 584.91 },
       "silver": { "per_gram_eur":  0.94, "per_gram_dkk":   7.01 }
     },
     "fx_stale": false,
     "listings": [
       { "dealer": "Plaza", "status": "ok",
         "price_dkk": 2895, "premium_pct": 6.2, "in_stock": true,
         "url": "https://plaza.dk/products/...", "fetched_at": "..." },
       { "dealer": "Tavex", "status": "ok", "price_dkk": 2940, ... },
       { "dealer": "Silver Gold Bull", "status": "error",
         "error": "timeout", "price_dkk": null }
     ]
   }
   ```

   Listings sorted ascending by `price_dkk`; `error`/`unavailable`/`out_of_stock` sink to the bottom.

7. PWA renders spot prices at the top (EUR + DKK) and a sorted dealer table below, with the buy link per row.

## Error handling

**Principle:** fail soft on every external dependency. One bad site never breaks the response.

### Per-dealer scraper failures

| Failure mode | Listing status | UI presentation |
|---|---|---|
| HTTP timeout (>8 s) | `error`, `error: "timeout"` | Bottom of list: "⚠ Dealer X — timed out" |
| HTTP 4xx/5xx | `error`, `error: "http_<code>"` | "⚠ Dealer X — site error" |
| Page fetched but no price element found | `error`, `error: "parse_failed"` | "⚠ Dealer X — couldn't read price" + structured log |
| Non-numeric price text ("Ring for pris") | `unavailable` | "Dealer X — price on request" |
| Out-of-stock badge detected | `out_of_stock`, last seen `price_dkk` if present | Greyed out |
| Successful price extracted | `ok` | Sorted into ranking |

### Spot / FX failures

- **metals.dev fails** → `spot: null`. Listings still render, but no `premium_pct` column.
- **frankfurter.app fails** → fall back to a static USD/EUR + USD/DKK rate stamped into the codebase, refreshed when convenient (e.g. quarterly). Set `fx_stale: true` so the UI flags it visibly. Caveat: USD/EUR can drift 5–10% over a year, so a long-stale fallback can meaningfully skew the premium-vs-spot column. The flag is there so the user knows not to trust the premium numbers when it's set.

### Backend-level

- Hard request timeout 12 s. Whatever finished by then is returned.
- If even spot+FX fail and zero listings succeed → return `503` with a `Retry-After: 30` header.
- No per-task retries within a single request — retries would blow the timeout, and persistent failures don't fix in 1 second.
- Structured logging (JSON) with `dealer`, `error_type`, `traceback`. View on Render's log tab.

### Frontend

- 5 s into a request: show "first request after idle takes up to a minute" hint under the spinner.
- 401 → "Bad API key — open settings."
- 5xx / network error → "Couldn't reach server — try again."
- Per-row error states styled distinctly so the user can see *which* dealer is broken.

## Testing

Scrapers break when dealers change their HTML. The strategy assumes that and is designed to detect it quickly.

### Unit tests — fast, deterministic, run in CI

For each dealer, save a real HTML snapshot to `tests/fixtures/<dealer>_<size>.html`. Tests parse the fixture and assert price extraction, in-stock detection, out-of-stock detection. **No network.** Whole suite runs in <1 s.

```python
def test_tavex_parses_5g_bar():
    html = read_fixture("tavex_5g.html")
    listing = TavexScraper().parse(html, size_g=5.0)
    assert listing.price_dkk == 2940
    assert listing.in_stock is True
```

### Integration smoke test — runs against live sites

`tests/integration/test_live.py` hits each real dealer once and asserts each returns *some* `price_dkk` for a 5 g bar. **This is the canary** for silently stale parsers. Run manually, or schedule weekly via GitHub Actions.

### `/health` endpoint

`GET /health` runs all scrapers in parallel against a known size and returns per-dealer pass/fail. Hit it from the phone whenever the app feels off — instantly shows which dealer is broken.

### CI

GitHub Actions on push to `main`:
1. Run unit tests
2. `ruff` lint
3. `mypy` type-check

Live integration tests are **not** run on every push (would spam dealer sites). They run on a weekly cron + manual `workflow_dispatch`.

### Out of scope for testing

- No frontend tests (it's ~150 lines of vanilla JS — manual eyeball)
- No load testing (single user)
- No mocks beyond the unit test layer

## Hosting & cost

| Piece | Provider | Cost | Notes |
|---|---|---|---|
| Frontend (PWA) | Cloudflare Pages | $0 | Forever-free, unlimited bandwidth, HTTPS |
| Backend API | Render.com free web service | $0 | 750 hr/month free; sleeps after 15 min idle; ~30–60 s cold start |
| Spot price API | metals.dev free tier | $0 | Within personal-use daily limits |
| FX API | frankfurter.app | $0 | Open-source, no key, unlimited |
| Code hosting / CI | GitHub (personal account) | $0 | Public or private, free Actions minutes |
| Domain | none — use `*.onrender.com` and `*.pages.dev` | $0 | |

**Total: $0/month.** If Render ever drops the free web tier, fallbacks: Hugging Face Spaces, Oracle Cloud Always Free, or a Raspberry Pi at home with a Cloudflare Tunnel. Architecture decoupled enough to swap in a day.

## Security

- **Backend protected by `X-API-Key` shared secret.** Set as an environment variable on Render. The PWA stores the key in `localStorage` once on first load (settings page); user enters it manually.
- **HTTPS everywhere** — Render and Cloudflare Pages give it for free.
- **No PII collected.** No logs of user activity beyond technical scraping logs.
- **Outgoing requests** — backend talks only to the 9 known dealer domains plus `metals.dev` and `frankfurter.app`. Hard-code the allowlist; no user-provided URLs.

## Open questions / future considerations

These are explicitly NOT in scope for v1, but worth noting so they don't get lost:

1. If Render cold starts become annoying, a 5-min cron (e.g., uptimerobot.com free tier) could ping the service to keep it warm. Free, but eats free hours faster.
2. Live integration test failures could post to a private Slack/Discord webhook so silently broken scrapers surface even when the user isn't checking.
3. If a dealer turns out to require JavaScript rendering, the `DealerScraper` interface allows that one adapter to use Playwright internally. Memory will be the constraint on Render free tier — may need to upgrade hosting if more than one or two dealers need it.
4. Could expose the spot endpoint (`GET /spot`) so the user can check live spot prices without doing a full dealer fetch.

## Approvals

- Brainstorming session: 2026-05-07 — design approved by user across all 5 sections (architecture, components, data flow, error handling, testing).
- Next step: hand off to **writing-plans** skill to produce step-by-step implementation plan.
