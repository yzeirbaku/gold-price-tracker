const API_KEY_STORAGE = 'gold-tracker-api-key';
const THEME_STORAGE = 'gold-tracker-theme';
const BACKEND_URL = (window.BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const SPOT_REFRESH_MS = 30000;
const $ = (s) => document.querySelector(s);

function loadApiKey() { return localStorage.getItem(API_KEY_STORAGE) || ''; }
function saveApiKey(k) { localStorage.setItem(API_KEY_STORAGE, k); }

function loadTheme() { return localStorage.getItem(THEME_STORAGE) || 'dark'; }
function saveTheme(t) { localStorage.setItem(THEME_STORAGE, t); }
function applyTheme(t) {
  if (t === 'light') document.documentElement.setAttribute('data-theme', 'light');
  else document.documentElement.removeAttribute('data-theme');
}
// Apply persisted theme before first paint to avoid a flash of dark on light.
applyTheme(loadTheme());

function fmtNum(n, decimals, locale = 'en-US') {
  return new Intl.NumberFormat(locale, { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(n);
}
// Whole-number table prices use Danish thousand-grouping ('.') so the page
// shows dots only — no commas anywhere. Spot uses en-US so its '.' is the
// decimal separator. Both yield dots-only output.
function fmtDKK(n) { return `${fmtNum(n, 0, 'da-DK')} dkk`; }
// Compact form for tight contexts (mobile chart axes): 124567 → "125k",
// 1240000 → "1.2M". Trims the trailing ".0" so "1k" stays clean, not "1.0k".
function fmtCompactDKK(n) {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
  if (abs >= 1_000)     return Math.round(n / 1_000) + 'k';
  return String(Math.round(n));
}
function fmtEUR(n) { return `${fmtNum(n, 2)} eur`; }
function fmtSpotDKK(n) { return `${fmtNum(n, 2)} dkk`; }
function fmtSpotEUR(n) { return `${fmtNum(n, 2)} eur`; }
function fmtPct(n) { return n == null ? '—' : (n > 0 ? '+' : '') + n.toFixed(1) + '%'; }
function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c])); }

// URL safety: only allow http(s):// — block anything else (javascript:, data:,
// etc.) that might come from a compromised/scraped listing.
function safeHref(url) {
  try {
    const u = new URL(url);
    if (u.protocol === 'http:' || u.protocol === 'https:') return url;
  } catch { /* fall through */ }
  return '#';
}

// ── UI helpers ─────────────────────────────────────────────────────────────
// Three site-wide helpers + matching CLAUDE.md rules. Touch with care — every
// async submit / delete / error toast / showModal call routes through these.

// Disable a button (and optionally swap its label) while an async op runs.
// Always restores in `finally` so a thrown error can't leave it stuck. Reason:
// double-tapping Save fired two POSTs and the second surfaced as a "duplicate"
// error to the user. Any button that triggers a network call MUST go through
// withBusy. Omit busyLabel for icon-only buttons — they just get disabled.
async function withBusy(btn, fn, busyLabel) {
  if (!btn) return fn();
  const originalLabel = btn.textContent;
  const originalDisabled = btn.disabled;
  btn.disabled = true;
  if (busyLabel != null) btn.textContent = busyLabel;
  try {
    return await fn();
  } finally {
    btn.disabled = originalDisabled;
    if (busyLabel != null) btn.textContent = originalLabel;
  }
}

// Turn a fetch failure into user-facing copy. Never leak status codes, raw
// response bodies, or exception messages — they look unpolished and tell the
// user nothing actionable. Pass `fallback` to override the generic line per
// call site. `body` lets the caller pre-consume res.text() and still get the
// FastAPI-detail polishing (purchase/alert forms do this so they can render
// validation messages inline).
async function userFacingError({ res, body, err, fallback = 'Something went wrong. Try again.' } = {}) {
  if (err) return 'Network error. Check your connection and try again.';
  if (!res) return fallback;
  if (res.status === 401) return 'You need to sign in again.';
  if (res.status === 403) return "You don't have access to this.";
  if (res.status === 404) return 'Not found.';
  if (res.status === 429) return 'Too many attempts. Wait a moment and try again.';
  if (res.status >= 500) return 'The server is having trouble. Try again in a moment.';
  let parsed = null;
  try {
    parsed = body != null ? JSON.parse(body) : await res.clone().json();
  } catch { /* not JSON — fall through to fallback */ }
  return polishApiDetail(parsed?.detail) || fallback;
}

// FastAPI validation errors arrive as either { detail: [{msg, loc}] } or
// { detail: "..." }. Polish field names + scrub anything that looks like a
// Python exception class. Returns null when there's nothing safe to surface.
const _API_FIELD_LABELS = {
  purchased_at: 'Purchase date',
  price_paid_dkk: 'Price paid',
  gross_weight_g: 'Gross weight',
  purity: 'Purity',
  metal: 'Metal',
  label: 'Label',
  threshold_pct: 'Threshold',
  size_g: 'Bar size',
  coin_type: 'Coin type',
  fine_gold_g: 'Coin size',
};
function polishApiDetail(detail) {
  if (!detail) return null;
  const polish = (raw) => {
    let msg = String(raw || '').replace(/^Value error,\s*/i, '').trim();
    for (const [k, v] of Object.entries(_API_FIELD_LABELS)) {
      msg = msg.replace(new RegExp(`\\b${k}\\b`, 'g'), v);
    }
    if (!msg) return null;
    if (/\b(Error|Exception|Traceback)\b/.test(msg)) return null;
    return msg.charAt(0).toUpperCase() + msg.slice(1);
  };
  if (Array.isArray(detail) && detail.length) return polish(detail[0].msg);
  if (typeof detail === 'string') return polish(detail);
  return null;
}

// Open a <dialog> without leaving the auto-focused first button highlighted
// on iOS Safari. dialog.showModal() focuses the first focusable element
// synchronously; with no prior pointer activity, iOS treats that as a
// keyboard focus and paints a :focus-visible ring. Net result: Cancel looks
// pre-selected the moment the dialog appears, which reads as a UX glitch.
// Blur on the next frame so the ring never paints; keyboard nav still works
// because Tab re-establishes focus normally. Pass `focusEl` to redirect
// focus to a specific input (login email, etc.) after the blur.
function openDialog(dlg, focusEl = null) {
  dlg.showModal();
  requestAnimationFrame(() => {
    if (document.activeElement instanceof HTMLElement
        && dlg.contains(document.activeElement)) {
      document.activeElement.blur();
    }
    if (focusEl) focusEl.focus();
  });
}

let lastSize = null;
let hasRenderedSpot = false;
let lastListings = [];           // cached so we can re-sort without re-fetching
let sortState = { col: 'price', dir: 'asc' };  // default matches backend order
let lastCoinListings = [];       // ditto for coins
let coinSortState = { col: 'premium', dir: 'asc' };  // best deals first

// Tab state — persisted across page loads.
const TAB_STORAGE = 'gold-tracker-tab';
let currentTab = localStorage.getItem(TAB_STORAGE) || 'bars';

// Purchase dialog mode. Re-used between Add and Edit flows — submit handler
// reads this to decide POST /portfolio vs PATCH /portfolio/{id}.
let purchaseMode = { kind: 'add' };

// Portfolio metal filter — null = show all, 'gold' | 'silver' = filter to one.
// Toggled by clicking a metal panel under the summary.
let portfolioMetalFilter = null;

function renderSpot(data) {
  if (!data || !data.spot) {
    $('#spot-content').textContent = 'Spot price unavailable.';
    $('#spot-updated').textContent = '';
    return;
  }
  const g = data.spot.gold, s = data.spot.silver;
  const goldText = `${fmtSpotEUR(g.per_gram_eur)} · ${fmtSpotDKK(g.per_gram_dkk)}`;
  const silverText = `${fmtSpotEUR(s.per_gram_eur)} · ${fmtSpotDKK(s.per_gram_dkk)}`;
  // Flash on every refresh after the very first render — the animation triggers
  // when the .flash class is present on the freshly-inserted node.
  const flashClass = hasRenderedSpot ? ' flash' : '';
  $('#spot-content').innerHTML = `
    <div class="spot-row"><span>Gold/g</span><span class="spot-value${flashClass}" data-spot="gold">${goldText}</span></div>
    <div class="spot-row"><span>Silver/g</span><span class="spot-value${flashClass}" data-spot="silver">${silverText}</span></div>
    ${data.fx_stale ? '<div class="spot-row" style="color:var(--error)">⚠ FX rates stale (fallback in use)</div>' : ''}
  `;
  $('#spot-updated').textContent = `Updated ${new Date(data.fetched_at).toLocaleTimeString()}`;
  hasRenderedSpot = true;
}

async function fetchSpot() {
  const apiKey = loadApiKey();
  if (!apiKey) {
    $('#spot-content').textContent = 'Open Settings to configure your API key.';
    return;
  }
  try {
    const resp = await fetch(`${BACKEND_URL}/spot`, { headers: { 'X-API-Key': apiKey } });
    if (resp.status === 401) {
      $('#spot-content').textContent = 'Bad API key — open Settings.';
      return;
    }
    if (!resp.ok) return;
    renderSpot(await resp.json());
  } catch {
    /* silent — likely cold start or offline; next tick will retry */
  }
}

// ── Snapshot-cron freshness indicator ────────────────────────────────────
// Background signal that the 20-min snapshot cron is alive. Without this,
// outlier-skip / fx_stale-skip / QStash issues / Render cold-start lockups
// silently produce gappy history charts and stale alert-eval baselines —
// nothing else in the UI surfaces "we haven't ingested in an hour."
//
// Threshold: >60 min flips the indicator to error color. The cron runs
// every 20 min, so 60 min = 3 missed ticks. A single tick missed (~25-40
// min ago) is normal jitter and not worth alarming about.

const SNAPSHOT_STALE_THRESHOLD_S = 60 * 60;

function fmtSnapshotAge(seconds) {
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m === 0 ? `${h} h ago` : `${h} h ${m} min ago`;
}

async function fetchSnapshotAge() {
  const apiKey = loadApiKey();
  const el = $('#snapshot-age');
  if (!apiKey || !el) return;
  try {
    const resp = await fetch(`${BACKEND_URL}/snapshot/age`, { headers: { 'X-API-Key': apiKey } });
    if (!resp.ok) return;  // silent — secondary indicator, never block UI
    const data = await resp.json();
    if (data.age_seconds == null) {
      // Fresh DB (no snapshots ever) — don't show a misleading "—" line.
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.textContent = `Snapshots: ${fmtSnapshotAge(data.age_seconds)}`;
    el.classList.toggle('is-stale', data.age_seconds > SNAPSHOT_STALE_THRESHOLD_S);
  } catch {
    /* silent — secondary indicator */
  }
}

async function fetchPrices(size) {
  const apiKey = loadApiKey();
  if (!apiKey) {
    showMessage('Open Settings to configure your API key.');
    return;
  }
  lastSize = size;
  showSpinner();
  $('#listings').hidden = true;
  $('#refresh').hidden = true;
  $('#status').textContent = '';
  setActiveSize(size);

  let resp;
  try {
    resp = await fetch(`${BACKEND_URL}/prices/${size}`, {
      headers: { 'X-API-Key': apiKey },
    });
  } catch (e) {
    showMessage(await userFacingError({ err: e, fallback: 'Could not load prices.' }));
    return;
  }
  if (resp.status === 401) { showMessage('Bad API key — open Settings.'); return; }
  if (!resp.ok) { showMessage(await userFacingError({ res: resp, fallback: 'Could not load prices.' })); return; }
  const data = await resp.json();
  renderPrices(data);
  // Reuse the spot block from the prices response — it's fresher than the cached one.
  renderSpot(data);
}

function sortListings(listings) {
  // Always keep ok rows above non-ok rows regardless of sort direction —
  // an "error" sorted to the top would be useless. Within each group we
  // sort by the chosen column; null values at group bottom.
  const dirMul = sortState.dir === 'asc' ? 1 : -1;
  const key = sortState.col === 'price' ? 'price_dkk' : 'premium_pct';
  return [...listings].sort((a, b) => {
    const aOk = a.status === 'ok' ? 0 : 1;
    const bOk = b.status === 'ok' ? 0 : 1;
    if (aOk !== bOk) return aOk - bOk;
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return (av - bv) * dirMul;
  });
}

function updateSortIndicators() {
  document.querySelectorAll('#listings th.sortable').forEach(th => {
    const active = th.dataset.sort === sortState.col;
    th.classList.toggle('sort-active', active);
    th.classList.toggle('sort-asc', active && sortState.dir === 'asc');
    th.classList.toggle('sort-desc', active && sortState.dir === 'desc');
    th.setAttribute('aria-sort', active ? (sortState.dir === 'asc' ? 'ascending' : 'descending') : 'none');
  });
}

function renderPrices(data) {
  // Hide out-of-stock listings — they're noise; only surface ok rows + scraper errors.
  lastListings = data.listings.filter(li => li.status !== 'out_of_stock');
  renderListingsBody();
  $('#loading').hidden = true;
  $('#listings').hidden = false;
  $('#refresh').hidden = false;
  $('#status').textContent = `Updated ${new Date(data.fetched_at).toLocaleTimeString()}`;
}

function renderListingsBody() {
  const tbody = $('#listings tbody');
  // Clear any expansion state — the history-row will be wiped along with
  // the rest of tbody, so leave nothing dangling.
  destroyHistoryCharts();
  historyState.key = null;
  tbody.innerHTML = '';
  updateSortIndicators();
  const ordered = sortListings(lastListings);
  for (const li of ordered) {
    const tr = document.createElement('tr');
    tr.className = li.status;
    const brand = li.brand ? escapeHtml(li.brand) : '—';
    if (li.status === 'ok') {
      // The whole row is the click target for expanding history (the dealer
      // link inside still navigates to the dealer site — handled in the
      // delegated click listener via .dealer-link guard).
      tr.classList.add('row-clickable');
      tr.dataset.dealer = li.dealer;
      tr.innerHTML = `
        <td><a class="dealer-link" href="${escapeHtml(safeHref(li.url))}" target="_blank" rel="noopener">${escapeHtml(li.dealer)}<span class="visit-arrow" aria-hidden="true">↗</span></a></td>
        <td class="brand-cell">${brand}</td>
        <td>${fmtDKK(li.price_dkk)}</td>
        <td>${fmtPct(li.premium_pct)}</td>
      `;
    } else {
      const note = li.status === 'unavailable' ? (li.error || 'unavailable')
                : `error (${li.error || 'unknown'})`;
      tr.innerHTML = `<td>${escapeHtml(li.dealer)}</td><td class="brand-cell">${brand}</td><td colspan="2">${note}</td>`;
    }
    tbody.appendChild(tr);
  }
}

function showSpinner() {
  $('#loading').innerHTML = `
    <div class="spinner"><span></span><span></span><span></span></div>
    <div class="loading-text">Fetching prices…</div>
  `;
  $('#loading').hidden = false;
}
function showMessage(msg) {
  $('#loading').textContent = msg;
  $('#loading').hidden = false;
}
function setActiveSize(size) {
  document.querySelectorAll('#size-picker button').forEach(b => {
    b.classList.toggle('active', parseFloat(b.dataset.size) === parseFloat(size));
  });
}

// Wire up size buttons + refresh
document.querySelectorAll('#size-picker button').forEach(b => {
  b.addEventListener('click', () => fetchPrices(parseFloat(b.dataset.size)));
});
$('#refresh').addEventListener('click', () => { if (lastSize != null) fetchPrices(lastSize); });

// Inline history expansion — only one row can be expanded at a time across
// either the bars or the coins table. Clicking a row inserts a tr.history-row
// directly below it; clicking the same row again collapses; clicking a
// different row collapses the old and expands the new. Re-rendering either
// table (size change, sort, refresh) clears the expansion automatically.
//
// historyState is shaped as { key, titleText, historyUrlBuilder, range } —
// the URL builder is a closure over the row's identity, so the same expand
// logic works for both bars (/history/bar/...) and coins (/history/coin/...).
let historyState = { key: null, titleText: '', historyUrlBuilder: null, range: '7d' };
const historyCharts = { price: null, premium: null };

function destroyHistoryCharts() {
  for (const k of Object.keys(historyCharts)) {
    if (historyCharts[k]) { historyCharts[k].destroy(); historyCharts[k] = null; }
  }
}

function collapseHistory() {
  destroyHistoryCharts();
  document.querySelectorAll('tr.row-expanded').forEach(tr => tr.classList.remove('row-expanded'));
  document.querySelectorAll('tr.history-row').forEach(tr => tr.remove());
  historyState.key = null;
}

function expandHistory(rowEl, config) {
  collapseHistory();
  historyState = { ...config, range: '7d' };
  rowEl.classList.add('row-expanded');

  const colspan = rowEl.children.length;
  const histTr = document.createElement('tr');
  histTr.className = 'history-row';
  histTr.innerHTML = `
    <td colspan="${colspan}">
      <div class="history-panel">
        <div class="history-panel-head">
          <h3>${escapeHtml(config.titleText)}</h3>
          <div class="range-toggle" role="tablist">
            <button data-range="24h" type="button">24h</button>
            <button data-range="7d" class="active" type="button">7d</button>
            <button data-range="30d" type="button">30d</button>
          </div>
        </div>
        <div class="history-status loading-msg">Loading…</div>
        <div class="charts-grid">
          <div class="chart-block">
            <h4>Price</h4>
            <div class="chart-container"><canvas id="history-price-chart"></canvas></div>
          </div>
          <div class="chart-block">
            <h4>Premium</h4>
            <div class="chart-container"><canvas id="history-premium-chart"></canvas></div>
          </div>
        </div>
        <div class="buy-context" id="buy-context"></div>
      </div>
    </td>
  `;
  rowEl.after(histTr);

  histTr.querySelectorAll('.range-toggle button').forEach(b => {
    b.addEventListener('click', () => {
      if (b.dataset.range === historyState.range) return;
      historyState.range = b.dataset.range;
      histTr.querySelectorAll('.range-toggle button').forEach(x => {
        x.classList.toggle('active', x.dataset.range === historyState.range);
      });
      fetchHistory();
    });
  });

  fetchHistory();
  fetchBuyContext();
}

async function fetchBuyContext() {
  const el = document.getElementById('buy-context');
  if (!el || !historyState.contextUrl) return;
  const apiKey = loadApiKey();
  if (!apiKey) return;
  el.innerHTML = '';
  try {
    const resp = await fetch(historyState.contextUrl, {
      headers: { 'X-API-Key': apiKey },
    });
    if (!resp.ok) return;  // silent — chart still works without context
    const data = await resp.json();
    renderBuyContext(el, data);
  } catch (e) {
    // Network error — silently skip. Chart already loaded fine.
  }
}

function renderBuyContext(el, ctx) {
  if (ctx.verdict === 'insufficient data') {
    el.innerHTML = `
      <h4>Buy now or wait?</h4>
      <div class="bc-muted">Not enough history yet (${ctx.n_observations} observations — need at least 5).</div>
    `;
    return;
  }
  const latest = ctx.today_premium_pct.toFixed(2);
  const iqrLo = ctx.iqr_low_premium_pct.toFixed(2);
  const iqrHi = ctx.iqr_high_premium_pct.toFixed(2);
  const minPrem = ctx.min_premium_pct.toFixed(2);
  const minAt = new Date(ctx.min_premium_at).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
  });
  const latestAgo = relativeTimeFrom(ctx.today_premium_at);
  const newLowPill = ctx.is_new_low
    ? `<span class="bc-new-low">New 30-day low</span>` : '';
  el.innerHTML = `
    <h4>Buy now or wait? ${newLowPill}</h4>
    <div class="bc-stats">
      <div class="bc-stat"><span class="bc-label">Latest recorded</span>
        <span class="bc-value">${latest}% <span class="bc-ago">(${latestAgo})</span></span></div>
      <div class="bc-stat"><span class="bc-label">Typical band (30d IQR)</span>
        <span class="bc-value">${iqrLo}% – ${iqrHi}%</span></div>
      <div class="bc-stat"><span class="bc-label">Lowest in 30d</span>
        <span class="bc-value">${minPrem}% <span class="bc-ago">(${minAt})</span></span></div>
    </div>
    <p class="bc-verdict">Latest snapshot is <strong>${ctx.verdict}</strong> for this dealer.</p>
  `;
}

function relativeTimeFrom(iso) {
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const diffSec = Math.max(0, (Date.now() - t) / 1000);
  if (diffSec < 60) return 'just now';
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

async function fetchHistory() {
  const apiKey = loadApiKey();
  if (!apiKey) { setHistoryStatus('Open Settings to configure your API key.'); return; }
  if (!historyState.historyUrlBuilder) return;
  setHistoryStatus('Loading…');
  let resp;
  try {
    resp = await fetch(historyState.historyUrlBuilder(historyState.range), {
      headers: { 'X-API-Key': apiKey },
    });
  } catch (e) {
    setHistoryStatus(await userFacingError({ err: e, fallback: 'Could not load history.' }));
    return;
  }
  if (resp.status === 503) { setHistoryStatus('History not configured on the server yet.'); return; }
  if (!resp.ok) { setHistoryStatus(await userFacingError({ res: resp, fallback: 'Could not load history.' })); return; }
  renderHistory(await resp.json());
}

function setHistoryStatus(msg) {
  const el = document.querySelector('tr.history-row .history-status');
  if (!el) return;
  el.textContent = msg;
  el.hidden = false;
}

function renderHistory(data) {
  const okPoints = data.points.filter(p =>
    p.status === 'ok' && p.price_dkk != null && p.spot_gold_dkk_per_g != null,
  );
  const statusEl = document.querySelector('tr.history-row .history-status');
  if (okPoints.length === 0) {
    setHistoryStatus('No data yet for this range.');
    destroyHistoryCharts();
    return;
  }
  if (statusEl) statusEl.hidden = true;

  // Bars: data.size_g + brand-per-point in tooltip.
  // Coins: data.fine_gold_g and no per-point label (coin_type+size_label
  // are already in the panel header, repeating in the tooltip is noise).
  const sizeG = data.size_g ?? data.fine_gold_g;
  const isBars = data.size_g != null;

  const pricePoints = okPoints.map(p => ({
    x: new Date(p.fetched_at).getTime(),
    y: p.price_dkk,
    label: isBars ? p.brand : null,
  }));
  const premiumPoints = okPoints.map(p => {
    const ref = p.spot_gold_dkk_per_g * sizeG;
    return {
      x: new Date(p.fetched_at).getTime(),
      y: ref > 0 ? Number(((p.price_dkk - ref) / ref * 100).toFixed(2)) : null,
      label: isBars ? p.brand : null,
    };
  });

  drawChart('price', 'history-price-chart', pricePoints, 'DKK', '#e2c054', v => fmtNum(v, 0, 'da-DK'));
  drawChart('premium', 'history-premium-chart', premiumPoints, '%', '#7dc8a4', v => v.toFixed(1) + '%');
}

function drawChart(key, canvasId, points, unit, color, yFmt) {
  // If a chart already exists for this key, update in place. Destroying and
  // re-creating on every range toggle causes a brief width flicker because
  // Chart.js's responsive sizing measures the canvas during the init handshake;
  // updating in place keeps the DOM stable.
  if (historyCharts[key]) {
    historyCharts[key].data.datasets[0].data = points;
    historyCharts[key].data.datasets[0].pointRadius = points.length < 60 ? 2 : 0;
    historyCharts[key].update('none');
    return;
  }
  // Mobile: smaller font + tighter gutters so the plot can stretch wider.
  const isMobile = window.matchMedia('(max-width: 600px)').matches;
  const tickFontSize = isMobile ? 10 : 12;
  const yAxisWidth = isMobile ? 38 : 52;
  const xMaxTicks = isMobile ? 3 : 5;
  const ctx = document.getElementById(canvasId).getContext('2d');
  historyCharts[key] = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [{
        data: points,
        borderColor: color,
        backgroundColor: color + '22',
        borderWidth: 2,
        tension: 0.25,
        pointRadius: points.length < 60 ? 2 : 0,
        pointHoverRadius: 4,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      // Strip Chart.js's default 3px outer padding so the plot fills the box.
      layout: { padding: 0 },
      scales: {
        x: {
          type: 'linear',
          // Stop Chart.js from extending the x-range to the next "nice" tick
          // boundary — it leaves blank space past the last data point.
          bounds: 'data',
          ticks: {
            color: '#8a8a90',
            maxTicksLimit: xMaxTicks,
            padding: 2,
            font: { size: tickFontSize },
            callback: v => new Date(v).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }),
          },
          grid: { color: 'rgba(255,255,255,0.04)' },
          // Trim Chart.js's reserved gutter on the right edge so the plot can
          // run nearly to the canvas edge. Last x-tick label may slightly
          // overhang on the right but stays readable.
          afterFit: (axis) => { axis.paddingRight = 4; },
        },
        y: {
          ticks: {
            color: '#8a8a90',
            padding: 2,         // pulls the value labels right up against the plot
            font: { size: tickFontSize },
            callback: yFmt,
          },
          grid: { color: 'rgba(255,255,255,0.04)' },
          // Lock the y-axis gutter to a fixed width so the price chart and
          // premium chart in the same row have identically-sized plot areas.
          // Mobile uses a tighter gutter + smaller font so the plot stretches
          // closer to the canvas edge.
          afterFit: (axis) => {
            axis.width = yAxisWidth;
            axis.paddingTop = 0;
            axis.paddingBottom = 0;
          },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => new Date(items[0].parsed.x).toLocaleString(),
            label: item => {
              const valueLabel = `${yFmt(item.parsed.y)} ${unit === '%' ? '' : unit}`.trim();
              const lbl = item.raw && item.raw.label;
              return lbl ? `${valueLabel} — ${lbl}` : valueLabel;
            },
          },
        },
      },
    },
  });
}

// Coins view ————————————————————————————————————————————————————————————

async function fetchCoins() {
  const apiKey = loadApiKey();
  if (!apiKey) { showCoinsMessage('Open Settings to configure your API key.'); return; }
  showCoinsSpinner();
  $('#coin-listings').hidden = true;
  $('#coins-refresh').hidden = true;
  $('#coins-status').textContent = '';
  let resp;
  try {
    resp = await fetch(`${BACKEND_URL}/coins`, { headers: { 'X-API-Key': apiKey } });
  } catch (e) {
    showCoinsMessage(await userFacingError({ err: e, fallback: 'Could not load coin prices.' }));
    return;
  }
  if (resp.status === 401) { showCoinsMessage('Bad API key — open Settings.'); return; }
  if (resp.status === 503) { showCoinsMessage('History not configured on the server yet.'); return; }
  if (!resp.ok) { showCoinsMessage(await userFacingError({ res: resp, fallback: 'Could not load coin prices.' })); return; }
  renderCoins(await resp.json());
}

function showCoinsSpinner() {
  $('#coins-loading').innerHTML = `
    <div class="spinner"><span></span><span></span><span></span></div>
    <div class="loading-text">Fetching prices…</div>
  `;
  $('#coins-loading').hidden = false;
}

function showCoinsMessage(msg) {
  $('#coins-loading').textContent = msg;
  $('#coins-loading').hidden = false;
}

function renderCoins(data) {
  // Coins arrive sorted by premium asc from the backend, but we re-sort
  // client-side so column-header clicks work without refetching.
  lastCoinListings = (data.listings || []).filter(li => li.status !== 'out_of_stock');
  renderCoinListingsBody();
  $('#coins-loading').hidden = true;
  $('#coin-listings').hidden = false;
  $('#coins-refresh').hidden = false;
  $('#coins-status').textContent = data.fetched_at
    ? `Updated ${new Date().toLocaleTimeString()}`
    : 'No data yet — wait for the snapshot cron to run.';
}

function sortCoinListings(listings) {
  const dirMul = coinSortState.dir === 'asc' ? 1 : -1;
  const key = coinSortState.col === 'price' ? 'price_dkk' : 'premium_pct';
  return [...listings].sort((a, b) => {
    const aOk = a.status === 'ok' ? 0 : 1;
    const bOk = b.status === 'ok' ? 0 : 1;
    if (aOk !== bOk) return aOk - bOk;
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return (av - bv) * dirMul;
  });
}

function updateCoinSortIndicators() {
  document.querySelectorAll('#coin-listings th.sortable').forEach(th => {
    const active = th.dataset.sort === coinSortState.col;
    th.classList.toggle('sort-active', active);
    th.classList.toggle('sort-asc', active && coinSortState.dir === 'asc');
    th.classList.toggle('sort-desc', active && coinSortState.dir === 'desc');
    th.setAttribute('aria-sort', active ? (coinSortState.dir === 'asc' ? 'ascending' : 'descending') : 'none');
  });
}

function renderCoinListingsBody() {
  const tbody = $('#coin-listings tbody');
  destroyHistoryCharts();
  historyState.key = null;
  tbody.innerHTML = '';
  updateCoinSortIndicators();
  const ordered = sortCoinListings(lastCoinListings);
  for (const li of ordered) {
    const tr = document.createElement('tr');
    tr.className = li.status;
    const coinLabel = li.size_label
      ? `${li.coin_type} ${li.size_label}`
      : (li.coin_type ?? '—');
    if (li.status === 'ok') {
      tr.classList.add('row-clickable');
      tr.dataset.dealer = li.dealer;
      tr.dataset.coinType = li.coin_type;
      tr.dataset.fineGoldG = String(li.fine_gold_g);
      tr.innerHTML = `
        <td><a class="dealer-link" href="${escapeHtml(safeHref(li.url))}" target="_blank" rel="noopener">${escapeHtml(li.dealer)}<span class="visit-arrow" aria-hidden="true">↗</span></a></td>
        <td class="brand-cell">${escapeHtml(coinLabel)}</td>
        <td>${li.fine_gold_g != null ? fmtNum(li.fine_gold_g, 2) + ' g' : '—'}</td>
        <td>${fmtDKK(li.price_dkk)}</td>
        <td>${fmtPct(li.premium_pct)}</td>
      `;
    } else {
      const note = li.error || li.status;
      tr.innerHTML = `<td>${escapeHtml(li.dealer)}</td><td class="brand-cell">${escapeHtml(coinLabel)}</td><td colspan="3">${escapeHtml(note)}</td>`;
    }
    tbody.appendChild(tr);
  }
}

$('#coins-refresh').addEventListener('click', () => fetchCoins());

document.querySelectorAll('#coin-listings th.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.sort;
    if (coinSortState.col === col) {
      coinSortState.dir = coinSortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
      coinSortState.col = col;
      coinSortState.dir = 'asc';
    }
    if (lastCoinListings.length) renderCoinListingsBody();
  });
});

$('#coin-listings tbody').addEventListener('click', (e) => {
  if (e.target.closest('.dealer-link')) return;
  const tr = e.target.closest('tr.row-clickable');
  if (!tr) return;
  const dealer = tr.dataset.dealer;
  const coinType = tr.dataset.coinType;
  const fineGoldG = parseFloat(tr.dataset.fineGoldG);
  if (!dealer || !coinType || !fineGoldG) return;
  const key = `coin:${dealer}:${coinType}:${fineGoldG}`;
  if (historyState.key === key) {
    collapseHistory();
  } else {
    expandHistory(tr, {
      key,
      titleText: `${dealer} — ${coinType} (${fineGoldG.toFixed(2)} g)`,
      historyUrlBuilder: (range) =>
        `${BACKEND_URL}/history/coin/${encodeURIComponent(dealer)}/${encodeURIComponent(coinType)}/${fineGoldG}?range=${range}`,
      contextUrl:
        `${BACKEND_URL}/context/coin/${encodeURIComponent(dealer)}/${encodeURIComponent(coinType)}/${fineGoldG}`,
    });
  }
});

// Tab toggle ——————————————————————————————————————————————————————————————

function setTab(tab) {
  currentTab = tab;
  localStorage.setItem(TAB_STORAGE, tab);
  document.querySelectorAll('#tab-strip button').forEach(b => {
    const active = b.dataset.tab === tab;
    b.classList.toggle('active', active);
    b.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  $('#bars-view').hidden = tab !== 'bars';
  $('#coins-view').hidden = tab !== 'coins';
  collapseHistory();  // stop a panel from sticking around when switching tabs
  if (tab === 'coins' && !lastCoinListings.length) fetchCoins();
}

document.querySelectorAll('#tab-strip button').forEach(b => {
  b.addEventListener('click', () => setTab(b.dataset.tab));
});

// Listings tbody click — delegate to row-clickable rows. Clicks on the dealer
// link itself fall through to the anchor (opens dealer site in a new tab);
// any other click on a clickable row toggles the inline history panel.
$('#listings tbody').addEventListener('click', (e) => {
  if (e.target.closest('.dealer-link')) return;  // let the link navigate
  const tr = e.target.closest('tr.row-clickable');
  if (!tr) return;
  const dealer = tr.dataset.dealer;
  if (!dealer || lastSize == null) return;
  const key = `bar:${dealer}:${lastSize}`;
  if (historyState.key === key) {
    collapseHistory();
  } else {
    expandHistory(tr, {
      key,
      titleText: `${dealer} — ${lastSize} g`,
      historyUrlBuilder: (range) =>
        `${BACKEND_URL}/history/bar/${encodeURIComponent(dealer)}/${lastSize}?range=${range}`,
      contextUrl:
        `${BACKEND_URL}/context/bar/${encodeURIComponent(dealer)}/${lastSize}`,
    });
  }
});

// Sortable headers — click to switch column, click again to flip direction.
document.querySelectorAll('#listings th.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.sort;
    if (sortState.col === col) {
      sortState.dir = sortState.dir === 'asc' ? 'desc' : 'asc';
    } else {
      sortState.col = col;
      sortState.dir = 'asc';
    }
    if (lastListings.length) renderListingsBody();
  });
});

// Hamburger menu — slide-in drawer with backdrop.
function isMenuOpen() {
  return $('#menu-dropdown').classList.contains('is-open');
}
function setMenuOpen(open) {
  $('#menu-dropdown').classList.toggle('is-open', open);
  $('#menu-backdrop').classList.toggle('is-open', open);
  $('#menu-btn').setAttribute('aria-expanded', open ? 'true' : 'false');
}
$('#menu-btn').addEventListener('click', (e) => {
  e.stopPropagation();
  setMenuOpen(!isMenuOpen());
});
$('#menu-backdrop').addEventListener('click', () => setMenuOpen(false));
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && isMenuOpen()) setMenuOpen(false);
});

// Drawer swipe — opens the menu when closed (left-edge → right) and
// closes it when open (any → left). One handler covers both directions
// via a `mode` flag captured at touchstart.
//
// Open path: touch must START in a 0-50px band from the viewport's left
// edge. iOS Safari's own browser-back gesture fires from the very edge,
// but only when the page has back-history; this PWA sits at "/" so back
// rarely applies, and iOS's gesture is velocity- and direction-sensitive
// anyway (slow horizontal pans don't trigger it). Keeping EDGE_START_MIN
// at 0 maximises the chance of catching a natural thumb swipe.
//
// Close path: any touch start works (no edge requirement) — the drawer
// is already visible and the user expects "swipe it away" to close.
//
// Both paths: motion must be predominantly horizontal — vertical deviation
// past MAX_DEVIATION_Y cancels so vertical scrolling never accidentally
// triggers. The touchmove listener is non-passive so we can preventDefault
// once the gesture reads as horizontal-dominant — that stops the page
// from scrolling vertically along with the swipe, which was the dragging-
// shimmy visual the user noticed. Below the lock threshold we still let
// native scroll proceed, so a touch that starts in our trigger zone but
// is actually a vertical scroll falls through cleanly.
(() => {
  const EDGE_START_MIN = 0;
  const EDGE_START_MAX = 50;
  const MIN_DISTANCE_X = 40;
  const MAX_DEVIATION_Y = 50;
  // Once horizontal motion exceeds LOCK_THRESHOLD_X AND dominates vertical
  // motion, we commit to the gesture and stop the viewport from scrolling.
  // Small enough that the lock kicks in well before the user notices any
  // vertical drift; big enough that a true vertical scroll starting in
  // our trigger zone gets a chance to escape.
  const LOCK_THRESHOLD_X = 5;
  let startX = 0, startY = 0, mode = null;  // 'open' | 'close' | null

  document.addEventListener('touchstart', (e) => {
    if (e.touches.length !== 1) return;
    if (document.querySelector('dialog[open]') !== null) return;
    const t = e.touches[0];
    if (isMenuOpen()) {
      startX = t.clientX;
      startY = t.clientY;
      mode = 'close';
    } else {
      if (t.clientX < EDGE_START_MIN || t.clientX > EDGE_START_MAX) return;
      startX = t.clientX;
      startY = t.clientY;
      mode = 'open';
    }
  }, { passive: true });

  document.addEventListener('touchmove', (e) => {
    if (!mode) return;
    const t = e.touches[0];
    const dxRaw = t.clientX - startX;
    const dy = Math.abs(t.clientY - startY);
    if (dy > MAX_DEVIATION_Y) { mode = null; return; }
    // Horizontal-dominant lock: stop the viewport from scrolling
    // vertically along with the swipe. preventDefault requires the
    // listener to be non-passive (see options below).
    if (Math.abs(dxRaw) > LOCK_THRESHOLD_X && Math.abs(dxRaw) > dy) {
      e.preventDefault();
    }
    const dx = mode === 'open' ? dxRaw : -dxRaw;
    if (dx >= MIN_DISTANCE_X) {
      setMenuOpen(mode === 'open');
      mode = null;
    }
  }, { passive: false });

  const cancel = () => { mode = null; };
  document.addEventListener('touchend', cancel, { passive: true });
  document.addEventListener('touchcancel', cancel, { passive: true });
})();

// Click the page title to go back to Prices with bars tab + 10g size.
function goToPricesHome() {
  setTab('bars');
  showPricesView();
  // Snap the size selector back to 10 g and refetch.
  fetchPrices(10);
}
$('#header-title-link').addEventListener('click', goToPricesHome);
$('#header-title-link').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    goToPricesHome();
  }
});
$('#menu-dropdown').addEventListener('click', (e) => {
  const action = e.target.closest('.menu-item')?.dataset.action;
  if (!action) return;
  setMenuOpen(false);
  if (action === 'settings') openSettings();
  else if (action === 'reports') openReportsView();
  else if (action === 'portfolio') openPortfolioView();
  else if (action === 'alerts') openAlertsView();
  else if (action === 'signin') openLoginDialog();
  else if (action === 'signout') signOut();
  else if (action === 'prices') {
    const onPrices = $('#reports-view').hidden && $('#portfolio-view').hidden && $('#alerts-view').hidden;
    // Coming from any aux view: snap back to the canonical home — Bars tab at
    // 10 g. Already on Prices: leave the user's current tab/size alone.
    if (onPrices) showPricesView();
    else goToPricesHome();
  }
});

// Settings dialog — API key + theme. Backend URL from config.js.
function openSettings() {
  $('#api-key').value = loadApiKey();
  const theme = loadTheme();
  const themeRadio = document.querySelector(`input[name="theme"][value="${theme}"]`);
  if (themeRadio) themeRadio.checked = true;
  openDialog($('#settings-dialog'));
}
$('#settings-dialog').addEventListener('close', () => {
  if ($('#settings-dialog').returnValue === 'save') {
    saveApiKey($('#api-key').value);
    const theme = document.querySelector('input[name="theme"]:checked')?.value || 'dark';
    saveTheme(theme);
    applyTheme(theme);
    fetchSpot();   // immediately try with the new key
    fetchSnapshotAge();
  }
});

// Spot price: load on page open, then auto-refresh while visible.
fetchSpot();
fetchSnapshotAge();
// Default size selection: load 10 g listings as soon as the page opens.
fetchPrices(10);
// Restore tab state from localStorage (defaults to 'bars').
setTab(currentTab);
setInterval(() => {
  if (document.visibilityState === 'visible') fetchSpot();
}, SPOT_REFRESH_MS);
// Snapshot age changes every ~20 min (cron cadence). Re-check every 5 min
// while visible — frequent enough to catch a freshly-stale cron quickly,
// cheap enough not to matter (one tiny query).
const SNAPSHOT_AGE_REFRESH_MS = 5 * 60 * 1000;
setInterval(() => {
  if (document.visibilityState === 'visible') fetchSnapshotAge();
}, SNAPSHOT_AGE_REFRESH_MS);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    fetchSpot();
    fetchSnapshotAge();
  }
});

// Service worker registration
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('service-worker.js').catch(() => {});
}

// View switching ————————————————————————————————————————————————————————————

function hideAllAuxViews() {
  $('#reports-view').hidden = true;
  $('#portfolio-view').hidden = true;
  $('#alerts-view').hidden = true;
  $('#verify-view').hidden = true;
}

function showPricesView() {
  hideAllAuxViews();
  $('#spot').hidden = false;
  $('#tab-strip').hidden = false;
  // Restore the bars/coins tab the user was last on.
  setTab(currentTab);
}

function showReportsView() {
  $('#bars-view').hidden = true;
  $('#coins-view').hidden = true;
  hideAllAuxViews();
  $('#reports-view').hidden = false;
  $('#spot').hidden = true;
  $('#tab-strip').hidden = true;
  document.querySelectorAll('#tab-strip button').forEach((b) => {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
}

function showPortfolioView() {
  $('#bars-view').hidden = true;
  $('#coins-view').hidden = true;
  hideAllAuxViews();
  $('#portfolio-view').hidden = false;
  $('#spot').hidden = true;
  $('#tab-strip').hidden = true;
  document.querySelectorAll('#tab-strip button').forEach((b) => {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
}

function showAlertsView() {
  $('#bars-view').hidden = true;
  $('#coins-view').hidden = true;
  hideAllAuxViews();
  $('#alerts-view').hidden = false;
  $('#spot').hidden = true;
  $('#tab-strip').hidden = true;
  document.querySelectorAll('#tab-strip button').forEach((b) => {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
}

function showVerifyView() {
  $('#bars-view').hidden = true;
  $('#coins-view').hidden = true;
  hideAllAuxViews();
  $('#verify-view').hidden = false;
  $('#spot').hidden = true;
  $('#tab-strip').hidden = true;
}

async function openReportsView() {
  showReportsView();
  await loadReportsArchive();
}

// All archive rows from the latest fetch — kept so filters can re-render
// without hitting the network on every dropdown change.
let reportsArchive = { weekly: [], monthly: [] };

async function loadReportsArchive() {
  const weeklyList = $('#reports-weekly-list');
  const monthlyList = $('#reports-monthly-list');
  weeklyList.innerHTML = '<div class="muted-tiny">Loading…</div>';
  monthlyList.innerHTML = '<div class="muted-tiny">Loading…</div>';

  let rows = [];
  try {
    const res = await fetch(`${BACKEND_URL}/reports`, {
      headers: { 'X-API-Key': loadApiKey() },
    });
    if (!res.ok) {
      const msg = await userFacingError({ res, fallback: 'Could not load reports.' });
      weeklyList.innerHTML = `<div class="muted-tiny">${escapeHtml(msg)}</div>`;
      monthlyList.innerHTML = '';
      return;
    }
    rows = await res.json();
  } catch (err) {
    const msg = await userFacingError({ err, fallback: 'Could not load reports.' });
    weeklyList.innerHTML = `<div class="muted-tiny">${escapeHtml(msg)}</div>`;
    monthlyList.innerHTML = '';
    return;
  }
  reportsArchive.weekly = rows.filter((r) => r.type === 'weekly');
  reportsArchive.monthly = rows.filter((r) => r.type === 'monthly');
  populateArchiveFilters();
  renderArchives();
}

// A weekly report [period_start, period_end] (inclusive) can touch up to
// two calendar months and two calendar years. Yield every (year, month)
// pair the week overlaps.
function weeklyTouches(row) {
  const out = [];
  const a = new Date(row.period_start + 'T00:00:00Z');
  const b = new Date(row.period_end + 'T00:00:00Z');
  for (let d = new Date(a); d <= b; d.setUTCDate(d.getUTCDate() + 1)) {
    const key = `${d.getUTCFullYear()}-${d.getUTCMonth() + 1}`;
    if (!out.length || out[out.length - 1] !== key) out.push(key);
  }
  return out;
}

function populateArchiveFilters() {
  const weeklyYears = new Set();
  const weeklyMonths = new Set();
  for (const r of reportsArchive.weekly) {
    for (const ym of weeklyTouches(r)) {
      const [y, m] = ym.split('-').map(Number);
      weeklyYears.add(y);
      weeklyMonths.add(m);
    }
  }
  const monthlyYears = new Set(
    reportsArchive.monthly.map((r) => Number(r.period_start.split('-')[0])),
  );
  fillDropdown('#reports-weekly-year', [...weeklyYears].sort((a, b) => b - a));
  fillDropdown('#reports-weekly-month', [...weeklyMonths].sort((a, b) => a - b),
                (m) => MONTH_NAMES[m - 1]);
  fillDropdown('#reports-monthly-year', [...monthlyYears].sort((a, b) => b - a));
}

const MONTH_NAMES = ['January','February','March','April','May','June',
                     'July','August','September','October','November','December'];

function fillDropdown(selector, values, labelFn = (v) => String(v)) {
  const root = $(selector);
  const list = root.querySelector('.dd-list');
  const current = root.dataset.value || '';  // preserve selection across refreshes
  list.innerHTML = '<li data-value="">All</li>'
    + values.map((v) => `<li data-value="${v}">${labelFn(v)}</li>`).join('');
  const stillThere = values.map(String).includes(current);
  setDropdownValue(root, stillThere ? current : '',
                    stillThere ? labelFn(Number(current) || current) : 'All');
}

function setDropdownValue(root, value, label) {
  root.dataset.value = value;
  root.querySelector('.dd-trigger').textContent = label;
  root.querySelectorAll('.dd-list li').forEach((li) => {
    li.classList.toggle('selected', li.dataset.value === value);
  });
}

function closeAllDropdowns(except) {
  document.querySelectorAll('.dd.is-open').forEach((d) => {
    if (d === except) return;
    d.classList.remove('is-open');
    d.querySelector('.dd-list').hidden = true;
  });
}

document.addEventListener('click', (e) => {
  // Trigger toggles its own dropdown; items inside the list close on selection.
  const trigger = e.target.closest('.dd-trigger');
  if (trigger) {
    e.stopPropagation();
    const root = trigger.closest('.dd');
    const list = root.querySelector('.dd-list');
    const willOpen = list.hidden;
    closeAllDropdowns(root);
    list.hidden = !willOpen;
    root.classList.toggle('is-open', willOpen);
    return;
  }
  const item = e.target.closest('.dd-list li');
  if (item) {
    const root = item.closest('.dd');
    setDropdownValue(root, item.dataset.value, item.textContent);
    root.querySelector('.dd-list').hidden = true;
    root.classList.remove('is-open');
    root.dispatchEvent(new CustomEvent('dd:change', { bubbles: true }));
    return;
  }
  // Click outside any dropdown closes everything.
  if (!e.target.closest('.dd')) closeAllDropdowns();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeAllDropdowns();
});

function renderArchives() {
  const wYear = $('#reports-weekly-year').dataset.value || '';
  const wMonth = $('#reports-weekly-month').dataset.value || '';
  const mYear = $('#reports-monthly-year').dataset.value || '';

  const weekly = reportsArchive.weekly.filter((r) => {
    if (!wYear && !wMonth) return true;
    const touches = weeklyTouches(r);
    return touches.some((ym) => {
      const [y, m] = ym.split('-');
      if (wYear && y !== wYear) return false;
      if (wMonth && m !== wMonth) return false;
      return true;
    });
  });
  const monthly = reportsArchive.monthly.filter((r) => {
    if (!mYear) return true;
    return r.period_start.startsWith(`${mYear}-`);
  });

  $('#reports-weekly-list').innerHTML = renderArchiveItems(weekly, 'weekly');
  $('#reports-monthly-list').innerHTML = renderArchiveItems(monthly, 'monthly');
}

document.addEventListener('dd:change', (e) => {
  if (e.target.matches(
    '#reports-weekly-year, #reports-weekly-month, #reports-monthly-year',
  )) {
    renderArchives();
  }
});

function renderArchiveItems(rows, kind) {
  if (!rows.length) {
    return `<div class="muted-tiny">No ${kind} reports archived yet.</div>`;
  }
  return rows.map((r) => {
    const label = kind === 'weekly'
      ? `Week of ${r.period_start} – ${r.period_end}`
      : monthLabel(r.period_start);
    return `
      <div class="archive-item" data-id="${r.id}">
        <span class="archive-label">${label}</span>
        <button class="archive-download icon-btn" aria-label="Download" type="button">↓</button>
      </div>`;
  }).join('');
}

function monthLabel(periodStart) {
  const [y, m] = periodStart.split('-');
  const names = ['January','February','March','April','May','June',
                 'July','August','September','October','November','December'];
  return `${names[parseInt(m, 10) - 1]} ${y}`;
}

async function downloadReport(id) {
  const res = await fetch(`${BACKEND_URL}/reports/${id}`, {
    headers: { 'X-API-Key': loadApiKey() },
  });
  if (!res.ok) {
    await infoDialog({
      title: 'Download failed',
      message: await userFacingError({ res, fallback: 'Could not download this report.' }),
    });
    return;
  }
  await streamToFileFromResponse(res);
}

async function generateOnDemand(range, button) {
  const status = $('#reports-gen-status');
  status.innerHTML = `
    <div class="loading-msg">
      <div class="spinner"><span></span><span></span><span></span></div>
      <div class="loading-text">Generating report…</div>
    </div>`;
  await withBusy(button, async () => {
    try {
      const res = await fetch(
        `${BACKEND_URL}/reports/generate?range=${range}`,
        { method: 'POST', headers: { 'X-API-Key': loadApiKey() } },
      );
      if (!res.ok) {
        status.textContent = await userFacingError({ res, fallback: 'Could not generate report.' });
        return;
      }
      await streamToFileFromResponse(res);
      status.innerHTML = '';
    } catch (err) {
      status.textContent = await userFacingError({ err, fallback: 'Could not generate report.' });
    }
  });
}

async function streamToFileFromResponse(res) {
  const blob = await res.blob();
  const cd = res.headers.get('content-disposition') || '';
  const m = cd.match(/filename="([^"]+)"/);
  const filename = m ? m[1] : 'report.html';
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

$('#reports-gen-week').addEventListener('click', (e) => {
  generateOnDemand('week', e.currentTarget);
});
$('#reports-gen-month').addEventListener('click', (e) => {
  generateOnDemand('month', e.currentTarget);
});
$('#reports-view').addEventListener('click', (e) => {
  const item = e.target.closest('.archive-item');
  if (!item) return;
  downloadReport(parseInt(item.dataset.id, 10));
});

// Auth + portfolio ——————————————————————————————————————————————————————————

const SESSION_BROADCAST_KEY = 'gold-tracker.session';
const SESSION_TOKEN_KEY = 'gold-tracker.session-token';
let currentUser = null;
let lastPortfolio = { purchases: [], summary: null };
let portfolioSort = { col: 'date', dir: 'desc' };

// Portfolio worth chart state. Range is the selected pill (1w/1m/6m/1y/all).
// The Chart.js instance is held so we can update in place on range/filter
// changes — destroying and recreating causes a width flicker (same reason
// as the inline bar/coin history chart, see drawChart()).
let portfolioChartRange = '1m';
let portfolioChartInstance = null;

function getSessionToken() {
  try { return localStorage.getItem(SESSION_TOKEN_KEY) || ''; } catch { return ''; }
}
function setSessionToken(t) {
  try { localStorage.setItem(SESSION_TOKEN_KEY, t); } catch {}
}
function clearSessionToken() {
  try { localStorage.removeItem(SESSION_TOKEN_KEY); } catch {}
}
function authHeaders() {
  const t = getSessionToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

function updateAuthUI() {
  const signedIn = currentUser != null;
  $('.menu-item[data-action="portfolio"]').hidden = !signedIn;
  $('.menu-item[data-action="alerts"]').hidden = !signedIn;
  $('.menu-item[data-action="signin"]').hidden = signedIn;
  $('.menu-item[data-action="signout"]').hidden = !signedIn;
  const accountInfo = $('.menu-account-info');
  accountInfo.hidden = !signedIn;
  if (signedIn) accountInfo.textContent = currentUser.email;
}

async function loadAuthState() {
  if (!getSessionToken()) { currentUser = null; updateAuthUI(); return; }
  try {
    const res = await fetch(`${BACKEND_URL}/auth/me`, { headers: authHeaders() });
    if (res.ok) currentUser = await res.json();
    else {
      // Stale or revoked token — clear it so we don't keep retrying.
      if (res.status === 401) clearSessionToken();
      currentUser = null;
    }
  } catch (e) {
    currentUser = null;
  }
  updateAuthUI();
}

function openLoginDialog() {
  $('#login-stage-1').hidden = false;
  $('#login-stage-2').hidden = true;
  $('#login-email').value = '';
  $('#login-error').hidden = true;
  $('#login-error').textContent = '';
  openDialog($('#login-dialog'), $('#login-email'));
}

$('#login-form').addEventListener('submit', async (e) => {
  // Only the "Send link" button triggers the request; Cancel/Close just close.
  const submitter = e.submitter;
  if (!submitter || submitter.value === 'cancel') return;
  if (submitter.id !== 'login-submit') return;
  e.preventDefault();

  const email = $('#login-email').value.trim();
  if (!email) return;
  const errEl = $('#login-error');
  errEl.hidden = true;
  await withBusy($('#login-submit'), async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/auth/request-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        errEl.textContent = await userFacingError({ res, fallback: "Couldn't send link. Try again." });
        errEl.hidden = false;
        return;
      }
      $('#login-stage-2-email').textContent = email;
      $('#login-stage-1').hidden = true;
      $('#login-stage-2').hidden = false;
    } catch (err) {
      errEl.textContent = await userFacingError({ err });
      errEl.hidden = false;
    }
  }, 'Sending…');
});

// Cross-tab broadcast: when verify completes in another tab, this tab notices.
window.addEventListener('storage', async (e) => {
  if (e.key !== SESSION_BROADCAST_KEY) return;
  if (e.newValue === '1') {
    await loadAuthState();
    const dialog = $('#login-dialog');
    if (dialog.open) dialog.close();
  } else if (e.newValue === '' || e.newValue === null) {
    currentUser = null;
    updateAuthUI();
    // If user is on portfolio view, kick them back to prices.
    if (!$('#portfolio-view').hidden || !$('#alerts-view').hidden) showPricesView();
  }
});

async function handleVerifyFragment() {
  const hash = window.location.hash;
  if (!hash.startsWith('#auth=')) return false;
  // Token is secrets.token_urlsafe — already URL-safe, no decode needed.
  const token = hash.slice('#auth='.length);
  showVerifyView();
  const contentEl = $('#verify-content');
  contentEl.innerHTML = '<div class="loading-msg"><div class="loading-text">Signing you in…</div></div>';
  try {
    const res = await fetch(`${BACKEND_URL}/auth/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    if (!res.ok) {
      const msg = res.status === 400
        ? 'This link is invalid or expired.'
        : await userFacingError({ res, fallback: 'Sign-in failed. Send a fresh link.' });
      contentEl.innerHTML = `
        <h2>Sign-in failed</h2>
        <p>${escapeHtml(msg)}</p>
        <p><button class="site-btn" id="verify-retry" type="button">Send a new link</button></p>
      `;
      $('#verify-retry').addEventListener('click', () => {
        history.replaceState(null, '', window.location.pathname);
        showPricesView();
        openLoginDialog();
      });
      return true;
    }
    const user = await res.json();
    // Persist the session bearer token first so authHeaders() works on the
    // next call, then derive currentUser shape and update UI.
    if (user.token) setSessionToken(user.token);
    currentUser = { user_id: user.user_id, email: user.email };
    updateAuthUI();
    try { localStorage.setItem(SESSION_BROADCAST_KEY, '1'); } catch {}
    // Strip the token from the URL immediately so a reload doesn't re-fire
    // the now-used token and surface "Sign-in failed" to an already-signed-in user.
    history.replaceState(null, '', window.location.pathname);
    contentEl.innerHTML = `
      <h2>You're signed in</h2>
      <p>Signed in as <strong>${escapeHtml(user.email)}</strong>. You can close this tab.</p>
      <p><button class="site-btn" id="verify-continue" type="button">Continue to app</button></p>
    `;
    $('#verify-continue').addEventListener('click', () => {
      showPricesView();
    });
  } catch (err) {
    contentEl.innerHTML = `
      <h2>Sign-in failed</h2>
      <p>${escapeHtml(await userFacingError({ err }))}</p>
    `;
  }
  return true;
}

async function signOut() {
  try {
    await fetch(`${BACKEND_URL}/auth/logout`, {
      method: 'POST',
      headers: authHeaders(),
    });
  } catch {}
  clearSessionToken();
  currentUser = null;
  try { localStorage.setItem(SESSION_BROADCAST_KEY, ''); } catch {}
  updateAuthUI();
  if (!$('#portfolio-view').hidden || !$('#alerts-view').hidden) showPricesView();
  await infoDialog({
    title: 'Signed out',
    message: 'You have been signed out. The site keeps working without sign-in — pop back in anytime to view your portfolio.',
    okLabel: 'OK',
  });
}

// Portfolio view ————————————————————————————————————————————————————————————

async function openPortfolioView() {
  if (!currentUser) { openLoginDialog(); return; }
  showPortfolioView();
  await loadPortfolio();
}

async function loadPortfolio() {
  const loadingEl = $('#portfolio-loading');
  const tableEl = $('#portfolio-table');
  const emptyEl = $('#portfolio-empty');
  const summaryEl = $('#portfolio-summary-content');
  loadingEl.hidden = false;
  loadingEl.innerHTML = '<div class="spinner"><span></span><span></span><span></span></div><div class="loading-text">Loading portfolio…</div>';
  tableEl.hidden = true;
  emptyEl.hidden = true;
  summaryEl.innerHTML = '';
  try {
    const res = await fetch(`${BACKEND_URL}/portfolio`, { headers: authHeaders() });
    if (res.status === 401) {
      clearSessionToken(); currentUser = null; updateAuthUI();
      showPricesView(); openLoginDialog(); return;
    }
    if (!res.ok) {
      loadingEl.textContent = await userFacingError({ res, fallback: 'Could not load portfolio.' });
      return;
    }
    const data = await res.json();
    lastPortfolio = data;
    renderPortfolioSummary(data.summary);
    renderPortfolioTable(data.purchases);
    loadPortfolioHistory();
  } catch (err) {
    loadingEl.textContent = await userFacingError({ err, fallback: 'Could not load portfolio.' });
  } finally {
    loadingEl.hidden = true;
  }
}

async function loadPortfolioHistory() {
  // Mirrors loadPortfolio's auth-handling for 401, but doesn't blow away the
  // whole view on a transient error — the chart card just shows a soft
  // status message inside its own card.
  //
  // Spinner + min display time: see feedback memory feedback_loading_states.md.
  // On a fast local backend the fetch completes in <50ms, so the bare-text
  // "Loading…" appeared and vanished in a flash. We use the canonical
  // .spinner / .loading-text pattern + a ~LOADING_MIN_MS floor so the load
  // state actually reads as one.
  const card = $('#portfolio-chart');
  const statusEl = $('#pc-status');
  const metal = portfolioMetalFilter || 'all';
  const url = `${BACKEND_URL}/portfolio/history?range=${encodeURIComponent(portfolioChartRange)}&metal=${encodeURIComponent(metal)}`;
  const started = performance.now();
  try {
    statusEl.hidden = false;
    statusEl.innerHTML = '<div class="spinner"><span></span><span></span><span></span></div><div class="loading-text">Loading chart…</div>';
    const res = await fetch(url, { headers: authHeaders() });
    if (res.status === 401) {
      clearSessionToken(); currentUser = null; updateAuthUI();
      showPricesView(); openLoginDialog(); return;
    }
    if (!res.ok) {
      // Error paths skip the min-wait — surface the failure immediately.
      statusEl.textContent = await userFacingError({ res, fallback: 'Chart unavailable.' });
      card.hidden = false; return;
    }
    const data = await res.json();
    if (data.first_purchase_at == null) {
      // No purchases at all → don't show the chart card. The summary card's
      // empty state already handles "add your first purchase".
      card.hidden = true;
      return;
    }
    // Honour the min display time before swapping spinner → final UI.
    await loadingMinWait(started);
    if (!data.points.length) {
      // Have purchases, but no snapshot history yet (fresh DB or all
      // purchases newer than every snapshot row). Keep the card visible
      // but show a placeholder instead of an empty chart.
      card.hidden = false;
      statusEl.hidden = false;
      statusEl.textContent = 'Not enough snapshot history yet for this range.';
      $('#pc-value').textContent = '—';
      $('#pc-change').textContent = '';
      $('#pc-since').textContent = '';
      destroyPortfolioChart();
      return;
    }
    card.hidden = false;
    statusEl.hidden = true;
    renderPortfolioChart(data);
  } catch (err) {
    statusEl.hidden = false;
    statusEl.textContent = await userFacingError({ err, fallback: 'Chart unavailable.' });
  }
}

const LOADING_MIN_MS = 500;
async function loadingMinWait(startedAt) {
  const elapsed = performance.now() - startedAt;
  if (elapsed < LOADING_MIN_MS) {
    await new Promise(r => setTimeout(r, LOADING_MIN_MS - elapsed));
  }
}

// Scriptable backgroundColor for the portfolio chart: builds a vertical
// linear gradient from "more opaque near the line" → "fully transparent
// at the bottom". The modern finance-app fill style (Nordnet et al.) —
// gives the area under the line a sense of weight without dominating
// the chart. Chart.js calls this on every layout; we read the current
// dataset color from a custom `lineColor` property so the green↔red
// flip on positive/negative change carries through to the gradient.
function portfolioChartGradient(context) {
  const chart = context.chart;
  const chartArea = chart.chartArea;
  // Chart.js calls scriptables before chartArea is calculated on the
  // very first render; returning null lets Chart.js fall back without
  // throwing. The next render call will have the layout in hand.
  if (!chartArea) return null;
  const color = (context.dataset && context.dataset.lineColor) || '#5ec27a';
  const canvasCtx = chart.ctx;
  const gradient = canvasCtx.createLinearGradient(
    0, chartArea.top, 0, chartArea.bottom,
  );
  gradient.addColorStop(0, color + '66');     // ~40% alpha near the line
  gradient.addColorStop(0.5, color + '1f');   // ~12% mid-fade
  gradient.addColorStop(1, color + '00');     //   0% at the bottom
  return gradient;
}

function renderPortfolioChart(data) {
  const valueEl = $('#pc-value');
  const changeEl = $('#pc-change');
  const sinceEl = $('#pc-since');

  valueEl.textContent = fmtDKK(data.current_value_dkk);
  const chgDkk = data.period_change_dkk || 0;
  const chgPct = data.period_change_pct || 0;
  const cls = chgDkk >= 0 ? 'pnl-pos' : 'pnl-neg';
  changeEl.className = `pc-change ${cls}`;
  changeEl.textContent = `${fmtDKKSigned(chgDkk)} (${fmtPctSigned(chgPct)})`;

  // If the requested range pre-dates the user's first purchase, the line
  // starts at first_purchase_at — surface that so "+5% on 1Y" doesn't look
  // misleading when the user has only held for two months.
  sinceEl.textContent = portfolioRangeSinceLabel(data);

  // Green when up, red when down — match the change-number colour. Faint
  // fill so a flat chart still reads as "the area under the line".
  const positive = chgDkk >= 0;
  const lineColor = positive ? '#5ec27a' : '#e85a5a';
  const points = data.points.map(p => ({ x: new Date(p.t).getTime(), y: p.value_dkk }));

  if (portfolioChartInstance) {
    const ds = portfolioChartInstance.data.datasets[0];
    ds.data = points;
    ds.borderColor = lineColor;
    ds.lineColor = lineColor;  // gradient reads from this on each layout
    ds.backgroundColor = portfolioChartGradient;
    ds.pointRadius = points.length < 60 ? 2 : 0;
    portfolioChartInstance.update('none');
    return;
  }

  const isMobile = window.matchMedia('(max-width: 600px)').matches;
  const tickFontSize = isMobile ? 10 : 12;
  // Compact "22k" / "1.2M" labels at every breakpoint — full Danish-grouped
  // DKK numbers ("22.000") read like dates and force a 50–70px gutter that
  // eats the plot. Stocks apps universally abbreviate; this matches that
  // convention and lets the line stretch closer to both card edges.
  const yAxisWidth = isMobile ? 30 : 38;
  const yTickFmt = (v) => fmtCompactDKK(v);
  const xMaxTicks = isMobile ? 3 : 6;
  const ctx = document.getElementById('portfolio-chart-canvas').getContext('2d');
  portfolioChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [{
        data: points,
        borderColor: lineColor,
        // Custom prop read by portfolioChartGradient on each layout pass.
        lineColor: lineColor,
        backgroundColor: portfolioChartGradient,
        borderWidth: 2,
        tension: 0.25,
        pointRadius: points.length < 60 ? 2 : 0,
        pointHoverRadius: 4,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      layout: { padding: 0 },
      scales: {
        x: {
          type: 'linear',
          bounds: 'data',
          ticks: {
            color: '#8a8a90',
            maxTicksLimit: xMaxTicks,
            padding: 2,
            font: { size: tickFontSize },
            callback: v => new Date(v).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }),
          },
          grid: { color: 'rgba(255,255,255,0.04)' },
          afterFit: (axis) => { axis.paddingRight = 4; },
        },
        y: {
          ticks: {
            color: '#8a8a90',
            padding: 0,   // hug the plot's left edge — the card has its
                          // own padding already, no need for a second gutter
            font: { size: tickFontSize },
            callback: yTickFmt,
          },
          grid: { color: 'rgba(255,255,255,0.04)' },
          afterFit: (axis) => {
            axis.width = yAxisWidth;
            axis.paddingTop = 0;
            axis.paddingBottom = 0;
          },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => fmtDate(new Date(items[0].parsed.x)) + ' ' + new Date(items[0].parsed.x).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            label: item => fmtDKK(item.parsed.y),
          },
        },
      },
    },
  });
}

function portfolioRangeSinceLabel(data) {
  // Two distinct "the chart starts later than the requested range" cases —
  // both need to be surfaced so the period-change percent reads honestly
  // (otherwise "+5% on 1Y" misleads when only 2 months are actually plotted):
  //
  //   1. Backend clamped range_start to first_purchase_at because the user
  //      requested more history than they hold. Authoritative via the
  //      `clamped_to_first_purchase` flag the backend now sets.
  //   2. spot_snapshots in the DB don't go back as far as the user has
  //      held — backend's clamp is moot, the line still starts at the
  //      oldest available snapshot. Detect locally by comparing
  //      first_purchase_at against points[0].t.
  if (!data.points.length || !data.first_purchase_at) return '';
  if (data.clamped_to_first_purchase) {
    return `since ${fmtDate(data.first_purchase_at)}`;
  }
  const firstPoint = new Date(data.points[0].t).getTime();
  const firstPurchase = new Date(data.first_purchase_at).getTime();
  if (firstPoint - firstPurchase > 86_400_000) {
    return `since ${fmtDate(data.points[0].t)} (limited history)`;
  }
  return '';
}

function destroyPortfolioChart() {
  if (portfolioChartInstance) {
    portfolioChartInstance.destroy();
    portfolioChartInstance = null;
  }
}

// Range pill clicks. Wired once at startup — the pill bar lives inside the
// always-present #portfolio-chart section so we don't need to re-bind on
// each render.
$('#pc-ranges').addEventListener('click', (e) => {
  const btn = e.target.closest('button[data-range]');
  if (!btn || btn.dataset.range === portfolioChartRange) return;
  portfolioChartRange = btn.dataset.range;
  $('#pc-ranges').querySelectorAll('button').forEach(b => {
    b.classList.toggle('active', b.dataset.range === portfolioChartRange);
  });
  loadPortfolioHistory();
});

function renderPortfolioSummary(s) {
  const pnlClass = s.total_pnl_dkk >= 0 ? 'pnl-pos' : 'pnl-neg';
  $('#portfolio-summary-content').innerHTML = `
    <div class="portfolio-summary-grid">
      <div class="ps-stat"><span class="ps-label">Total paid</span><span class="ps-value">${fmtDKK(s.total_paid_dkk)}</span></div>
      <div class="ps-stat"><span class="ps-label">Current value</span><span class="ps-value">${fmtDKK(s.total_value_dkk)}</span></div>
      <div class="ps-stat"><span class="ps-label">P&amp;L</span><span class="ps-value ${pnlClass}">${fmtDKKSigned(s.total_pnl_dkk)} (${fmtPctSigned(s.total_pnl_pct)})</span></div>
    </div>
    <div class="metal-breakdown">
      ${renderMetalPanel('gold', s.by_metal.gold)}
      ${renderMetalPanel('silver', s.by_metal.silver)}
    </div>
  `;
}

function renderMetalPanel(metal, m) {
  const tone = metal === 'gold' ? 'is-gold' : 'is-silver';
  const isEmpty = !(m.paid_dkk > 0);
  if (isEmpty) {
    return `
      <div class="metal-panel ${tone} is-empty" data-metal="${metal}">
        <div class="metal-panel-head"><span class="metal-chip metal-${metal}">${metal}</span></div>
        <div class="metal-panel-empty">No ${metal} purchases yet.</div>
      </div>
    `;
  }
  const pnl = m.value_dkk - m.paid_dkk;
  const pnlPct = (pnl / m.paid_dkk) * 100;
  const pnlClass = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
  const activeCls = portfolioMetalFilter === metal ? ' is-active' : '';
  return `
    <div class="metal-panel ${tone}${activeCls}" data-metal="${metal}" role="button" tabindex="0" aria-pressed="${portfolioMetalFilter === metal}" title="Click to filter by ${metal}">
      <div class="metal-panel-head"><span class="metal-chip metal-${metal}">${metal}</span></div>
      <div class="metal-panel-stats">
        <div class="ps-stat"><span class="ps-label">Fine weight</span><span class="ps-value">${fmtFineG(m.fine_weight_g)} g</span></div>
        <div class="ps-stat"><span class="ps-label">Cost basis</span><span class="ps-value">${fmtDKK(m.paid_dkk)}</span></div>
        <div class="ps-stat"><span class="ps-label">Value</span><span class="ps-value">${fmtDKK(m.value_dkk)}</span></div>
        <div class="ps-stat"><span class="ps-label">P&amp;L</span><span class="ps-value ${pnlClass}">${fmtDKKSigned(pnl)} (${fmtPctSigned(pnlPct)})</span></div>
      </div>
    </div>
  `;
}

function setPortfolioFilter(metal) {
  // Pass null to clear, 'gold'/'silver' to filter. Clicking the active panel
  // again toggles the filter off so the panels themselves are a toggle.
  portfolioMetalFilter = (portfolioMetalFilter === metal) ? null : metal;
  if (lastPortfolio.summary) renderPortfolioSummary(lastPortfolio.summary);
  if (lastPortfolio.purchases) renderPortfolioTable(lastPortfolio.purchases);
  // Chart follows the filter: re-fetch the time series for the new metal
  // scope so the value line matches what the table is showing.
  if (currentUser) loadPortfolioHistory();
}

// Delegated click + keyboard on the summary card: any .metal-panel with a
// data-metal attribute toggles the filter for that metal. Empty panels (no
// purchases of that metal) ignore clicks because there's nothing to filter to.
$('#portfolio-summary-content').addEventListener('click', (e) => {
  const panel = e.target.closest('.metal-panel[data-metal]');
  if (!panel || panel.classList.contains('is-empty')) return;
  setPortfolioFilter(panel.dataset.metal);
});
$('#portfolio-summary-content').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter' && e.key !== ' ') return;
  const panel = e.target.closest('.metal-panel[data-metal]');
  if (!panel || panel.classList.contains('is-empty')) return;
  e.preventDefault();
  setPortfolioFilter(panel.dataset.metal);
});

function fmtPctSigned(n) {
  if (n == null) return '—';
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(2)}%`;
}
function fmtDKKSigned(n) {
  const sign = n > 0 ? '+' : (n < 0 ? '−' : '');
  return `${sign}${fmtDKK(Math.abs(n))}`;
}

function fmtFineG(g) {
  // Round display to 2 decimals, strip trailing zeros so 4.9995→"5", 9.999→"10".
  // Actual stored value remains full-precision; this only tidies the displayed digits.
  return parseFloat(g.toFixed(2)).toString();
}

// Generic in-app confirmation dialog. Resolves to true when the user clicks
// the affirmative button, false otherwise. Pass a custom button label via
// `okLabel` if "Confirm" isn't right (e.g. "Delete"). Pass `okOnly: true`
// for one-button informational dialogs ("Signed out", etc.) — the cancel
// button is hidden and the resolve value is ignored by most callers.
function confirmDialog({ title = 'Confirm', message, okLabel = 'Confirm', okOnly = false }) {
  return new Promise((resolve) => {
    const dlg = $('#confirm-dialog');
    const cancelBtn = dlg.querySelector('menu button[value="cancel"]');
    $('#confirm-dialog-title').textContent = title;
    $('#confirm-dialog-message').textContent = message;
    $('#confirm-dialog-ok').textContent = okLabel;
    cancelBtn.hidden = !!okOnly;
    const onClose = () => {
      dlg.removeEventListener('close', onClose);
      cancelBtn.hidden = false;  // restore for the next caller
      resolve(dlg.returnValue === 'save');
    };
    dlg.addEventListener('close', onClose);
    openDialog(dlg);
  });
}

function infoDialog({ title, message, okLabel = 'OK' }) {
  return confirmDialog({ title, message, okLabel, okOnly: true });
}

// Canonical site-wide date format: DD-MM-YYYY (see CLAUDE.md "Date format").
function fmtDate(isoOrDate) {
  const d = isoOrDate instanceof Date ? isoOrDate : new Date(isoOrDate);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const yyyy = d.getFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

function renderPortfolioFilterBar() {
  // Shows "Showing {metal} only · Show all" above the table when a metal
  // filter is active; renders nothing when filter is null. Lives inside
  // #portfolio-list so it sits with the table it scopes.
  const bar = $('#portfolio-filter-bar');
  if (!bar) return;
  if (!portfolioMetalFilter) {
    bar.hidden = true;
    bar.innerHTML = '';
    return;
  }
  bar.hidden = false;
  bar.innerHTML = `
    <span class="filter-chip-label">Showing <strong>${portfolioMetalFilter}</strong> only</span>
    <button id="portfolio-filter-clear" type="button" class="filter-chip-clear">Show all</button>
  `;
  $('#portfolio-filter-clear').addEventListener('click', () => {
    setPortfolioFilter(portfolioMetalFilter);  // toggle off
  });
}

function renderPortfolioTable(purchases) {
  const tableEl = $('#portfolio-table');
  const emptyEl = $('#portfolio-empty');
  const exportBtn = $('#portfolio-export-btn');
  const tbody = tableEl.querySelector('tbody');
  renderPortfolioFilterBar();
  // Export button visibility tracks "has any purchases" (independent of the
  // metal filter — exporting the full set, not the current view, is the
  // sensible default for a personal-records dump).
  if (exportBtn) exportBtn.hidden = !purchases.length;
  if (!purchases.length) {
    tableEl.hidden = true;
    emptyEl.hidden = false;
    emptyEl.innerHTML = '<p>No purchases yet. Add your first one to start tracking gains.</p>';
    tbody.innerHTML = '';
    updatePortfolioSortIndicators();
    return;
  }
  const filtered = portfolioMetalFilter
    ? purchases.filter(p => p.metal === portfolioMetalFilter)
    : purchases;
  if (!filtered.length) {
    // Filter excludes everything — show a filter-aware empty state with a
    // one-click escape hatch instead of leaving a blank card.
    tableEl.hidden = true;
    emptyEl.hidden = false;
    emptyEl.innerHTML = `<p>No ${portfolioMetalFilter} purchases. <a href="#" id="filter-empty-clear">Show all</a></p>`;
    $('#filter-empty-clear').addEventListener('click', (e) => {
      e.preventDefault();
      setPortfolioFilter(portfolioMetalFilter);  // toggle off
    });
    tbody.innerHTML = '';
    updatePortfolioSortIndicators();
    return;
  }
  emptyEl.hidden = true;
  tableEl.hidden = false;
  const sorted = [...filtered].sort((a, b) => {
    if (portfolioSort.col === 'date') {
      const cmp = new Date(a.purchased_at) - new Date(b.purchased_at);
      return portfolioSort.dir === 'asc' ? cmp : -cmp;
    }
    return 0;
  });
  tbody.innerHTML = sorted.map(p => {
    const gross = fmtFineG(p.gross_weight_g);
    return `
      <tr class="portfolio-row" data-id="${p.id}">
        <td>${fmtDate(p.purchased_at)}</td>
        <td><span class="metal-chip metal-${p.metal}">${p.metal}</span></td>
        <td>${gross} g</td>
        <td>
          <div class="row-actions">
            <button class="row-edit icon-btn" aria-label="Edit" data-id="${p.id}" type="button">✎</button>
            <button class="row-delete icon-btn" aria-label="Delete" data-id="${p.id}" type="button">✕</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
  updatePortfolioSortIndicators();
}

function updatePortfolioSortIndicators() {
  document.querySelectorAll('#portfolio-table th.sortable').forEach(th => {
    const active = th.dataset.sort === portfolioSort.col;
    th.classList.toggle('sort-active', active);
    th.classList.toggle('sort-asc', active && portfolioSort.dir === 'asc');
    th.classList.toggle('sort-desc', active && portfolioSort.dir === 'desc');
    th.setAttribute('aria-sort', active ? (portfolioSort.dir === 'asc' ? 'ascending' : 'descending') : 'none');
  });
}

document.querySelectorAll('#portfolio-table th.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.sort;
    if (portfolioSort.col === col) {
      portfolioSort.dir = portfolioSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
      portfolioSort.col = col;
      portfolioSort.dir = 'desc';
    }
    if (lastPortfolio.purchases.length) renderPortfolioTable(lastPortfolio.purchases);
  });
});

function buildPortfolioDetail(p) {
  const purchasePrem = p.purchase_premium_pct != null ? fmtPctSigned(p.purchase_premium_pct) : '—';
  const spotThen = p.spot_at_purchase_dkk_per_g != null
    ? `${fmtSpotDKK(p.spot_at_purchase_dkk_per_g)}/g` : '—';
  const pnlClass = p.pnl_dkk >= 0 ? 'pnl-pos' : 'pnl-neg';
  return `
    <div class="purchase-detail">
      <section class="pd-section pd-section-about">
        <h4 class="pd-section-head">About</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Label</span><span class="pd-value">${escapeHtml(p.label)}</span></div>
          ${p.dealer ? `<div class="pd-cell"><span class="pd-label">Dealer</span><span class="pd-value">${escapeHtml(p.dealer)}</span></div>` : ''}
          ${p.notes ? `<div class="pd-cell pd-cell-wide"><span class="pd-label">Notes</span><span class="pd-value pd-value-multiline">${escapeHtml(p.notes)}</span></div>` : ''}
        </div>
      </section>
      <section class="pd-section">
        <h4 class="pd-section-head">Spec</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Fine weight</span><span class="pd-value">${fmtFineG(p.fine_weight_g)} g</span></div>
          <div class="pd-cell"><span class="pd-label">Purity</span><span class="pd-value">${p.purity}</span></div>
        </div>
      </section>
      <section class="pd-section">
        <h4 class="pd-section-head">At purchase</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Price paid</span><span class="pd-value">${fmtDKK(p.price_paid_dkk)}</span></div>
          <div class="pd-cell"><span class="pd-label">Spot then</span><span class="pd-value">${spotThen}</span></div>
          <div class="pd-cell"><span class="pd-label">Premium</span><span class="pd-value">${purchasePrem}</span></div>
        </div>
      </section>
      <section class="pd-section">
        <h4 class="pd-section-head">Now</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Current spot</span><span class="pd-value">${fmtSpotDKK(p.current_spot_dkk_per_g)}/g</span></div>
          <div class="pd-cell"><span class="pd-label">Current value</span><span class="pd-value">${fmtDKK(p.current_value_dkk)}</span></div>
          <div class="pd-cell"><span class="pd-label">P&amp;L</span><span class="pd-value ${pnlClass}">${fmtDKKSigned(p.pnl_dkk)}<br><span class="muted-tiny">${fmtPctSigned(p.pnl_pct)}</span></span></div>
        </div>
      </section>
    </div>
  `;
}

function collapsePortfolioDetail() {
  const open = document.querySelector('#portfolio-table tr.portfolio-row.is-expanded');
  if (open) {
    open.classList.remove('is-expanded');
    const next = open.nextElementSibling;
    if (next && next.classList.contains('portfolio-detail-row')) next.remove();
  }
}

$('#portfolio-table tbody').addEventListener('click', async (e) => {
  const edit = e.target.closest('.row-edit');
  if (edit) {
    e.stopPropagation();
    const purchase = (lastPortfolio.purchases || []).find(x => x.id === edit.dataset.id);
    if (!purchase) return;
    openPurchaseDialog({ kind: 'edit', id: edit.dataset.id }, purchase);
    return;
  }

  const del = e.target.closest('.row-delete');
  if (del) {
    e.stopPropagation();
    const ok = await confirmDialog({
      title: 'Delete purchase',
      message: 'This permanently removes the purchase from your portfolio. This cannot be undone.',
      okLabel: 'Delete',
    });
    if (!ok) return;
    await withBusy(del, async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/portfolio/${del.dataset.id}`, {
          method: 'DELETE',
          headers: authHeaders(),
        });
        if (!res.ok && res.status !== 204) {
          await infoDialog({
            title: 'Delete failed',
            message: await userFacingError({ res, fallback: 'Could not delete this purchase.' }),
          });
          return;
        }
        await loadPortfolio();
      } catch (err) {
        await infoDialog({
          title: 'Delete failed',
          message: await userFacingError({ err }),
        });
      }
    });
    return;
  }

  const row = e.target.closest('tr.portfolio-row');
  if (!row) return;
  const wasOpen = row.classList.contains('is-expanded');
  collapsePortfolioDetail();
  if (wasOpen) return;

  // Look up the cached purchase data for this id.
  const purchase = (lastPortfolio.purchases || []).find(x => x.id === row.dataset.id);
  if (!purchase) return;
  const tr = document.createElement('tr');
  tr.className = 'portfolio-detail-row';
  tr.innerHTML = `<td colspan="4">${buildPortfolioDetail(purchase)}</td>`;
  row.after(tr);
  row.classList.add('is-expanded');
});

// Add / Edit purchase dialog ————————————————————————————————————————————————

function openPurchaseDialog(mode, purchase = null) {
  // Stash the original row on edit so the submit handler can diff against it
  // and PATCH only changed fields. Avoids needlessly re-fetching historical
  // spot when the user edits only e.g. the label or notes.
  purchaseMode = mode.kind === 'edit' ? { ...mode, original: purchase } : mode;
  const form = $('#purchase-form');
  form.reset();
  const titleEl = $('#purchase-dialog-title');
  if (mode.kind === 'edit' && purchase) {
    titleEl.textContent = 'Edit purchase';
    $(`#purchase-form input[name="metal"][value="${purchase.metal}"]`).checked = true;
    $('#purchase-label').value = purchase.label || '';
    $('#purchase-gross').value = purchase.gross_weight_g;
    $('#purchase-purity').value = purchase.purity;
    $('#purchase-price').value = purchase.price_paid_dkk;
    $('#purchase-date').value = (purchase.purchased_at || '').slice(0, 10);
    $('#purchase-dealer').value = purchase.dealer || '';
    $('#purchase-notes').value = purchase.notes || '';
  } else {
    titleEl.textContent = 'Add purchase';
    $('#purchase-purity').value = '0.9999';
    // Default purchase date to today in the user's local timezone (date input
    // expects yyyy-mm-dd; the API gets noon UTC at submit time).
    const now = new Date();
    const yyyy = now.getFullYear();
    const mm = String(now.getMonth() + 1).padStart(2, '0');
    const dd = String(now.getDate()).padStart(2, '0');
    $('#purchase-date').value = `${yyyy}-${mm}-${dd}`;
  }
  $('#purchase-error').hidden = true;
  openDialog($('#purchase-dialog'));
}

$('#portfolio-add-btn').addEventListener('click', () => {
  openPurchaseDialog({ kind: 'add' });
});

// ── Portfolio CSV export ────────────────────────────────────────────────────
// Client-side build from the already-loaded lastPortfolio.purchases. Exports
// the full set (ignores the metal filter chip) — the file is a personal
// records dump, not a view snapshot. Bearer-auth fetch wasn't needed since
// the data is already in memory.

const CSV_COLUMNS = [
  // [header, getter]
  ['purchased_at', p => p.purchased_at],
  ['metal', p => p.metal],
  ['label', p => p.label],
  ['dealer', p => p.dealer ?? ''],
  ['gross_weight_g', p => p.gross_weight_g],
  ['purity', p => p.purity],
  ['fine_weight_g', p => p.fine_weight_g],
  ['price_paid_dkk', p => p.price_paid_dkk],
  ['spot_at_purchase_dkk_per_g', p => p.spot_at_purchase_dkk_per_g ?? ''],
  ['purchase_premium_pct', p => p.purchase_premium_pct ?? ''],
  ['current_spot_dkk_per_g', p => p.current_spot_dkk_per_g],
  ['current_value_dkk', p => p.current_value_dkk],
  ['pnl_dkk', p => p.pnl_dkk],
  ['pnl_pct', p => p.pnl_pct],
  ['notes', p => p.notes ?? ''],
];

function csvEscape(value) {
  // RFC 4180: quote if value contains comma, quote, CR, or LF; double any
  // embedded quotes. Numbers and empty strings pass through bare.
  // Formula-injection guard: Excel/Numbers/Sheets execute cells whose first
  // char is =, +, -, @, tab, or CR. We prefix a single-quote which the
  // spreadsheet strips on display but won't evaluate. Today's columns (label,
  // dealer, notes) come from auth'd users typing into their own records so
  // the actual risk is low — but the guard is two lines and future-proofs
  // any column that ever pipes scraper-controlled text into the dump.
  let s = String(value);
  if (s === '') return '';
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  if (/[",\r\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

function buildPortfolioCsv(purchases) {
  const header = CSV_COLUMNS.map(c => c[0]).join(',');
  const rows = purchases.map(p =>
    CSV_COLUMNS.map(c => csvEscape(c[1](p))).join(',')
  );
  // Excel/Numbers prefer CRLF; the BOM coaxes Excel into reading as UTF-8
  // rather than guessing the codepage and mangling Danish characters.
  return '\ufeff' + [header, ...rows].join('\r\n') + '\r\n';
}

function exportPortfolioCsv() {
  const purchases = lastPortfolio.purchases || [];
  if (!purchases.length) return;
  const csv = buildPortfolioCsv(purchases);
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  const today = new Date();
  const dd = String(today.getDate()).padStart(2, '0');
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const yyyy = today.getFullYear();
  a.href = url;
  a.download = `portfolio-${yyyy}-${mm}-${dd}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Revoking too eagerly cancels the download in some Safari versions; a
  // microtask is enough for the click to land.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

$('#portfolio-export-btn').addEventListener('click', exportPortfolioCsv);

$('#purchase-form').addEventListener('submit', async (e) => {
  const submitter = e.submitter;
  if (!submitter || submitter.value === 'cancel') return;
  if (submitter.id !== 'purchase-submit') return;
  e.preventDefault();

  const errEl = $('#purchase-error');
  errEl.hidden = true;

  const metal = $('#purchase-form input[name="metal"]:checked').value;
  const label = $('#purchase-label').value.trim();
  const gross = parseFloat($('#purchase-gross').value);
  const purity = parseFloat($('#purchase-purity').value);
  const price = parseFloat($('#purchase-price').value);
  const dateOnly = $('#purchase-date').value;
  const dealer = $('#purchase-dealer').value.trim() || null;
  const notes = $('#purchase-notes').value.trim() || null;

  if (!label || isNaN(gross) || isNaN(purity) || isNaN(price) || !dateOnly) {
    errEl.textContent = 'Fill in all required fields.';
    errEl.hidden = false;
    return;
  }

  // Date-only input: use noon UTC so the historical spot lookup lands on the
  // midpoint of the trading day in the user's likely timezone.
  const purchased_at = `${dateOnly}T12:00:00Z`;

  const isEdit = purchaseMode.kind === 'edit';
  const url = isEdit
    ? `${BACKEND_URL}/portfolio/${purchaseMode.id}`
    : `${BACKEND_URL}/portfolio`;
  const method = isEdit ? 'PATCH' : 'POST';

  // POST sends every field. PATCH diffs against the cached original so the
  // backend only re-freezes historical spot when purchased_at/metal actually
  // moved — avoids hitting yfinance on a notes-only edit.
  let payload;
  if (isEdit) {
    const original = purchaseMode.original || {};
    const originalDate = (original.purchased_at || '').slice(0, 10);
    const diff = {};
    if (metal !== original.metal) diff.metal = metal;
    if (label !== (original.label || '')) diff.label = label;
    if (gross !== parseFloat(original.gross_weight_g)) diff.gross_weight_g = gross;
    if (purity !== parseFloat(original.purity)) diff.purity = purity;
    if (price !== parseFloat(original.price_paid_dkk)) diff.price_paid_dkk = price;
    if (dateOnly !== originalDate) diff.purchased_at = purchased_at;
    if ((dealer || null) !== (original.dealer || null)) diff.dealer = dealer;
    if ((notes || null) !== (original.notes || null)) diff.notes = notes;
    if (Object.keys(diff).length === 0) {
      $('#purchase-dialog').close();
      return;
    }
    payload = diff;
  } else {
    payload = {
      metal, label,
      gross_weight_g: gross, purity, price_paid_dkk: price,
      purchased_at, dealer, notes,
    };
  }

  await withBusy($('#purchase-submit'), async () => {
    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(payload),
      });
      if (res.status === 401) {
        $('#purchase-dialog').close();
        clearSessionToken(); currentUser = null; updateAuthUI();
        showPricesView(); openLoginDialog();
        return;
      }
      if (!res.ok) {
        const body = await res.text();
        const fallback = res.status === 502
          ? "Couldn't look up the historical spot price. Try again in a moment."
          : 'Save failed. Try again.';
        errEl.textContent = await userFacingError({ res, body, fallback });
        errEl.hidden = false;
        return;
      }
      $('#purchase-dialog').close();
      await loadPortfolio();
    } catch (err) {
      errEl.textContent = await userFacingError({ err });
      errEl.hidden = false;
    }
  }, 'Saving…');
});

// Alerts view ——————————————————————————————————————————————————————————————

// Cached /alerts/options response so the dialog dropdowns don't refetch on
// every open. Loaded once on first openAlertsView; refresh on demand if
// stale (we never invalidate — the registry is essentially static).
let alertsOptions = null;
let alertsCache = [];
// Edit mode: { kind: 'edit', id, original } | { kind: 'add' }
let alertMode = { kind: 'add' };

async function openAlertsView() {
  showAlertsView();
  await Promise.all([loadAlertsOptions(), loadAlerts()]);
}

async function loadAlertsOptions() {
  if (alertsOptions) return alertsOptions;
  try {
    const res = await fetch(`${BACKEND_URL}/alerts/options`, { headers: authHeaders() });
    if (!res.ok) return null;
    alertsOptions = await res.json();
    return alertsOptions;
  } catch {
    return null;
  }
}

async function loadAlerts() {
  const loadingEl = $('#alerts-loading');
  const emptyEl = $('#alerts-empty');
  const tableEl = $('#alerts-table');
  loadingEl.hidden = false;
  loadingEl.innerHTML = '<div class="spinner"><span></span><span></span><span></span></div><div class="loading-text">Loading alerts…</div>';
  emptyEl.hidden = true;
  tableEl.hidden = true;
  try {
    const res = await fetch(`${BACKEND_URL}/alerts`, { headers: authHeaders() });
    if (res.status === 401) {
      clearSessionToken(); currentUser = null; updateAuthUI();
      showPricesView(); openLoginDialog(); return;
    }
    if (!res.ok) {
      loadingEl.textContent = await userFacingError({ res, fallback: 'Could not load alerts.' });
      return;
    }
    const data = await res.json();
    alertsCache = data.alerts || [];
    renderAlerts();
  } catch (err) {
    loadingEl.textContent = await userFacingError({ err, fallback: 'Could not load alerts.' });
  } finally {
    loadingEl.hidden = true;
  }
}

function renderAlerts() {
  const emptyEl = $('#alerts-empty');
  const tableEl = $('#alerts-table');
  const tbody = tableEl.querySelector('tbody');
  if (!alertsCache.length) {
    emptyEl.hidden = false;
    tableEl.hidden = true;
    tbody.innerHTML = '';
    return;
  }
  emptyEl.hidden = true;
  tableEl.hidden = false;
  tbody.innerHTML = alertsCache.map((a) => {
    // Type column: Title-case to match the radio button labels in the dialog.
    const kind = a.kind === 'bar' ? 'Bar' : 'Coin';
    // Target column: just size for bars (no redundant "bar"; Type column
    // carries that). For coins: coin type + dimmed fine weight.
    const target = a.kind === 'bar'
      ? `${formatBarSize(a.size_g)} g`
      : `${escapeHtml(a.coin_type)} <span class="muted-tiny">(${formatFineG(a.fine_gold_g)} g fine)</span>`;
    return `
      <tr class="alert-row" data-id="${a.id}">
        <td>${kind}</td>
        <td>${target}</td>
        <td>≤ ${Number(a.threshold_pct).toFixed(2)}%</td>
        <td>
          <div class="row-actions">
            <button class="row-edit icon-btn" aria-label="Edit" data-id="${a.id}" type="button">✎</button>
            <button class="row-delete icon-btn" aria-label="Delete" data-id="${a.id}" type="button">✕</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function buildAlertDetail(a) {
  let statusCls = 'status-disabled';
  let statusLabel = 'Disabled';
  let statusDescription = 'Toggle this alert on in the edit dialog to start watching.';
  if (a.enabled && !a.muted_until_recovery) {
    statusCls = 'status-armed';
    statusLabel = 'Armed';
    statusDescription = "Watching the market. We'll email you when the premium drops to your threshold.";
  } else if (a.enabled && a.muted_until_recovery) {
    statusCls = 'status-muted';
    statusLabel = 'Muted';
    statusDescription = 'Already fired. Will re-arm once the premium climbs back above your threshold.';
  }
  const currentPrem = a.current_min_premium_pct != null
    ? `${a.current_min_premium_pct.toFixed(2)}%`
    : 'No data';
  const bestDealer = a.current_best_dealer ? escapeHtml(a.current_best_dealer) : 'No data';
  const fires = Number.isFinite(a.fire_count) ? a.fire_count : 0;
  const lastFired = a.last_fired_at ? fmtDateTime(a.last_fired_at) : 'Never';
  return `
    <div class="purchase-detail">
      <section class="pd-section pd-section-status">
        <h4 class="pd-section-head">Status</h4>
        <div class="pd-section-grid">
          <div class="pd-cell">
            <span class="pd-label">State</span>
            <span class="pd-value"><span class="status-dot ${statusCls}" aria-hidden="true"></span> ${statusLabel}</span>
          </div>
          <div class="pd-cell">
            <span class="pd-label">Description</span>
            <span class="pd-value">${statusDescription}</span>
          </div>
        </div>
      </section>
      <section class="pd-section">
        <h4 class="pd-section-head">Now</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Current premium</span><span class="pd-value">${currentPrem}</span></div>
          <div class="pd-cell"><span class="pd-label">Best dealer</span><span class="pd-value">${bestDealer}</span></div>
        </div>
      </section>
      <section class="pd-section">
        <h4 class="pd-section-head">History</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Times triggered</span><span class="pd-value">${fires}</span></div>
          <div class="pd-cell"><span class="pd-label">Last fired</span><span class="pd-value">${lastFired}</span></div>
        </div>
      </section>
    </div>
  `;
}

function collapseAlertDetail() {
  const open = document.querySelector('#alerts-table tr.alert-row.is-expanded');
  if (open) {
    open.classList.remove('is-expanded');
    const next = open.nextElementSibling;
    if (next && next.classList.contains('portfolio-detail-row')) next.remove();
  }
}

function formatBarSize(g) {
  // Match the size labels used elsewhere (2.5 / 5 / 10 / 20).
  return String(Number(g));
}
function formatFineG(g) {
  return Number(g).toFixed(2).replace(/\.?0+$/, '');
}
function fmtDateTime(iso) {
  const d = iso instanceof Date ? iso : new Date(iso);
  return `${fmtDate(d)} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

// Table click delegate — edit + delete actions + row-expand.
$('#alerts-table tbody').addEventListener('click', async (e) => {
  const edit = e.target.closest('.row-edit');
  if (edit) {
    e.stopPropagation();
    const a = alertsCache.find((x) => x.id === edit.dataset.id);
    if (!a) return;
    openAlertDialog({ kind: 'edit', id: a.id, original: a }, a);
    return;
  }
  const del = e.target.closest('.row-delete');
  if (del) {
    e.stopPropagation();
    const ok = await confirmDialog({
      title: 'Delete alert',
      message: 'This permanently removes this alert. You won\u2019t be notified about it anymore.',
      okLabel: 'Delete',
    });
    if (!ok) return;
    await withBusy(del, async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/alerts/${del.dataset.id}`, {
          method: 'DELETE', headers: authHeaders(),
        });
        if (!res.ok && res.status !== 204) {
          await infoDialog({
            title: 'Delete failed',
            message: await userFacingError({ res, fallback: 'Could not delete this alert.' }),
          });
          return;
        }
        await loadAlerts();
      } catch (err) {
        await infoDialog({
          title: 'Delete failed',
          message: await userFacingError({ err }),
        });
      }
    });
    return;
  }

  // Row-expand: toggle the detail panel directly under the clicked row.
  // Mirrors the portfolio table pattern (one row expanded at a time).
  const row = e.target.closest('tr.alert-row');
  if (!row) return;
  const wasOpen = row.classList.contains('is-expanded');
  collapseAlertDetail();
  if (wasOpen) return;
  const a = alertsCache.find((x) => x.id === row.dataset.id);
  if (!a) return;
  const tr = document.createElement('tr');
  // Reuses the portfolio detail row class so the styling matches exactly.
  tr.className = 'alert-detail-row portfolio-detail-row';
  tr.innerHTML = `<td colspan="4">${buildAlertDetail(a)}</td>`;
  row.after(tr);
  row.classList.add('is-expanded');
});

// Add / edit dialog ----------------------------------------------------------

function openAlertDialog(mode, existing = null) {
  alertMode = mode;
  const form = $('#alert-form');
  form.reset();
  const titleEl = $('#alert-dialog-title');
  const opts = alertsOptions || { bar_sizes: [2.5, 5, 10, 20], coin_options: [] };
  populateAlertDialogOptions(opts);
  const enabledField = $('#alert-enabled-field');
  if (mode.kind === 'edit' && existing) {
    titleEl.textContent = 'Edit alert';
    enabledField.hidden = false;
    $(`input[name="alert-kind"][value="${existing.kind}"]`).checked = true;
    switchAlertKind(existing.kind);
    if (existing.kind === 'bar') {
      setDropdownValue($('#alert-bar-size'), String(existing.size_g), `${existing.size_g} g`);
    } else {
      setDropdownValue($('#alert-coin-type'), existing.coin_type, existing.coin_type);
      refreshCoinSizeOptions(existing.coin_type, existing.fine_gold_g);
    }
    $('#alert-threshold').value = Number(existing.threshold_pct).toFixed(2);
    $('#alert-enabled').checked = !!existing.enabled;
  } else {
    titleEl.textContent = 'Add alert';
    enabledField.hidden = true;
    document.querySelector('input[name="alert-kind"][value="bar"]').checked = true;
    switchAlertKind('bar');
    $('#alert-enabled').checked = true;
  }
  $('#alert-error').hidden = true;
  refreshAlertPreview();
  openDialog($('#alert-dialog'));
}

// Render items into a .dd container's <ul>. `items` is [{value, label}] and
// `preselect` is the value string to mark selected. Reuses setDropdownValue
// (originally written for the reports filters) so both screens share the
// same render / selection plumbing — the .dd component is centralized.
function setDdItems(root, items, preselect = null) {
  const list = root.querySelector('.dd-list');
  list.innerHTML = items
    .map((it) => `<li data-value="${escapeHtml(String(it.value))}">${escapeHtml(it.label)}</li>`)
    .join('');
  let target = null;
  if (preselect != null) {
    target = items.find((it) => String(it.value) === String(preselect)) || null;
  }
  if (target == null && items.length) target = items[0];
  if (target) {
    setDropdownValue(root, String(target.value), target.label);
  } else {
    setDropdownValue(root, '', root.querySelector('.dd-trigger').textContent);
  }
}

function populateAlertDialogOptions(opts) {
  setDdItems(
    $('#alert-bar-size'),
    opts.bar_sizes.map((s) => ({ value: s, label: `${s} g` })),
  );
  setDdItems(
    $('#alert-coin-type'),
    (opts.coin_options || []).map((co) => ({ value: co.coin_type, label: co.coin_type })),
  );
  if (opts.coin_options && opts.coin_options.length) {
    refreshCoinSizeOptions(opts.coin_options[0].coin_type);
  }
}

function refreshCoinSizeOptions(coinType, preselectFine = null) {
  const opts = alertsOptions || { coin_options: [] };
  const entry = (opts.coin_options || []).find((c) => c.coin_type === coinType);
  const root = $('#alert-coin-size');
  if (!entry) {
    setDdItems(root, []);
    return;
  }
  const items = entry.sizes.map((s) => ({
    value: s.fine_gold_g,
    label: `${s.size_label} (${s.fine_gold_g.toFixed(2)} g)`,
  }));
  let preselect = null;
  if (preselectFine != null) {
    const target = Number(preselectFine);
    const match = entry.sizes.find((s) => Math.abs(s.fine_gold_g - target) < 0.005);
    if (match) preselect = String(match.fine_gold_g);
  }
  setDdItems(root, items, preselect);
}

function switchAlertKind(kind) {
  $('#alert-bar-fields').hidden = kind !== 'bar';
  $('#alert-coin-type-field').hidden = kind !== 'coin';
  $('#alert-coin-size-field').hidden = kind !== 'coin';
  refreshAlertPreview();
}

// Hint shown inside the add/edit modal: "Current: 8.34% (Vitus Guld)" — looks
// up the same min-premium-in-last-6h that the alerts table column uses, so
// the user can pick a sensible threshold without alt-tabbing to the bars
// or coins view first. Cancels any in-flight fetch when the target changes.
let alertPreviewRequest = 0;

// Minimum time the spinner+"Fetching current premium…" state stays on screen
// before flipping to the result. Local DB lookups return in tens of ms which
// makes the loading flash too quickly to read; this floor turns it into a
// readable beat. Matches the loading-msg cadence used elsewhere in the app.
const ALERT_PREVIEW_MIN_LOAD_MS = 550;

function refreshAlertPreview() {
  const previewEl = $('#alert-preview');
  const kind = document.querySelector('input[name="alert-kind"]:checked')?.value;
  if (!kind) { previewEl.hidden = true; return; }
  let qs;
  if (kind === 'bar') {
    const size = $('#alert-bar-size').dataset.value;
    if (!size) { previewEl.hidden = true; return; }
    qs = `kind=bar&size_g=${encodeURIComponent(size)}`;
  } else {
    const coinType = $('#alert-coin-type').dataset.value;
    const fine = $('#alert-coin-size').dataset.value;
    if (!coinType || !fine) { previewEl.hidden = true; return; }
    qs = `kind=coin&coin_type=${encodeURIComponent(coinType)}&fine_gold_g=${encodeURIComponent(fine)}`;
  }
  previewEl.hidden = false;
  previewEl.classList.add('is-loading');
  previewEl.innerHTML =
    '<div class="spinner"><span></span><span></span><span></span></div>' +
    '<div class="loading-text">Fetching current premium…</div>';
  const reqId = ++alertPreviewRequest;
  const startedAt = Date.now();

  const settle = (renderFn) => {
    if (reqId !== alertPreviewRequest) return;  // a newer request superseded
    const elapsed = Date.now() - startedAt;
    const remaining = Math.max(0, ALERT_PREVIEW_MIN_LOAD_MS - elapsed);
    setTimeout(() => {
      if (reqId !== alertPreviewRequest) return;
      previewEl.classList.remove('is-loading');
      renderFn();
    }, remaining);
  };

  fetch(`${BACKEND_URL}/alerts/preview?${qs}`, { headers: authHeaders() })
    .then((r) => {
      if (r.status === 401) {
        // Session expired between opening the dialog and the preview
        // resolving. Kick the user back to sign-in like /alerts list does
        // — preserves the auth invariant across the page.
        $('#alert-dialog').close();
        clearSessionToken(); currentUser = null; updateAuthUI();
        showPricesView(); openLoginDialog();
        return null;
      }
      return r.ok ? r.json() : null;
    })
    .then((data) => settle(() => {
      if (!data || data.current_min_premium_pct == null) {
        previewEl.innerHTML = 'Current: <span class="muted-tiny">no recent data</span>';
        return;
      }
      previewEl.innerHTML =
        `Current: <strong>${data.current_min_premium_pct.toFixed(2)}%</strong> ` +
        `<span class="muted-tiny">${escapeHtml(data.current_best_dealer || '')}</span>`;
    }))
    .catch(() => settle(() => {
      previewEl.innerHTML = 'Current: <span class="muted-tiny">unavailable</span>';
    }));
}

document.querySelectorAll('input[name="alert-kind"]').forEach((r) => {
  r.addEventListener('change', (e) => switchAlertKind(e.target.value));
});

// Coin-type cascade: changing the Coin Type dropdown refreshes the Coin
// Size dropdown to the matching variants from the registry. .dd dispatches
// dd:change on selection — we listen at the root and dispatch a refresh.
$('#alert-coin-type').addEventListener('dd:change', (e) => {
  const coinType = e.currentTarget.dataset.value;
  if (coinType) refreshCoinSizeOptions(coinType);
  refreshAlertPreview();
});
$('#alert-bar-size').addEventListener('dd:change', () => refreshAlertPreview());
$('#alert-coin-size').addEventListener('dd:change', () => refreshAlertPreview());

$('#alerts-add-btn').addEventListener('click', () => {
  openAlertDialog({ kind: 'add' });
});

$('#alert-form').addEventListener('submit', async (e) => {
  const submitter = e.submitter;
  if (!submitter || submitter.value === 'cancel') return;
  if (submitter.id !== 'alert-submit') return;
  e.preventDefault();

  const errEl = $('#alert-error');
  errEl.hidden = true;

  const kind = document.querySelector('input[name="alert-kind"]:checked').value;
  const threshold = parseFloat($('#alert-threshold').value);
  if (isNaN(threshold) || threshold < 0) {
    errEl.textContent = 'Threshold must be a non-negative number.';
    errEl.hidden = false;
    return;
  }

  let payload;
  if (alertMode.kind === 'edit') {
    const original = alertMode.original || {};
    const diff = {};
    if (Math.abs(threshold - Number(original.threshold_pct)) > 0.001) {
      diff.threshold_pct = threshold;
    }
    const enabled = $('#alert-enabled').checked;
    if (enabled !== !!original.enabled) diff.enabled = enabled;
    if (!Object.keys(diff).length) {
      $('#alert-dialog').close();
      return;
    }
    payload = diff;
  } else {
    payload = { kind, threshold_pct: threshold };
    if (kind === 'bar') {
      const v = $('#alert-bar-size').dataset.value;
      if (!v) {
        errEl.textContent = 'Pick a bar size.';
        errEl.hidden = false;
        return;
      }
      payload.size_g = parseFloat(v);
    } else {
      const coinType = $('#alert-coin-type').dataset.value;
      const fine = $('#alert-coin-size').dataset.value;
      if (!coinType || !fine) {
        errEl.textContent = 'Pick a coin type and size.';
        errEl.hidden = false;
        return;
      }
      payload.coin_type = coinType;
      payload.fine_gold_g = parseFloat(fine);
    }
  }

  const url = alertMode.kind === 'edit'
    ? `${BACKEND_URL}/alerts/${alertMode.id}`
    : `${BACKEND_URL}/alerts`;
  const method = alertMode.kind === 'edit' ? 'PATCH' : 'POST';

  await withBusy($('#alert-submit'), async () => {
    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(payload),
      });
      if (res.status === 401) {
        $('#alert-dialog').close();
        clearSessionToken(); currentUser = null; updateAuthUI();
        showPricesView(); openLoginDialog();
        return;
      }
      if (!res.ok) {
        const body = await res.text();
        errEl.textContent = await userFacingError({ res, body, fallback: 'Save failed. Try again.' });
        errEl.hidden = false;
        return;
      }
      $('#alert-dialog').close();
      await loadAlerts();
    } catch (err) {
      errEl.textContent = await userFacingError({ err });
      errEl.hidden = false;
    }
  }, 'Saving…');
});

// Boot: handle verify fragment first, then resolve auth state.
(async () => {
  const handled = await handleVerifyFragment();
  if (!handled) await loadAuthState();
})();
