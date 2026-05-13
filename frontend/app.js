const API_KEY_STORAGE = 'gold-tracker-api-key';
const THEME_STORAGE = 'gold-tracker-theme';
const BACKEND_URL = (window.BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const SPOT_REFRESH_MS = 20000;
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

let lastSize = null;
let hasRenderedSpot = false;
let lastListings = [];           // cached so we can re-sort without re-fetching
let sortState = { col: 'price', dir: 'asc' };  // default matches backend order
let lastCoinListings = [];       // ditto for coins
let coinSortState = { col: 'premium', dir: 'asc' };  // best deals first

// Tab state — persisted across page loads.
const TAB_STORAGE = 'gold-tracker-tab';
let currentTab = localStorage.getItem(TAB_STORAGE) || 'bars';

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
    showMessage(`Network error: ${e.message}`);
    return;
  }
  if (resp.status === 401) { showMessage('Bad API key — open Settings.'); return; }
  if (!resp.ok) { showMessage(`Server error: ${resp.status}`); return; }
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
    setHistoryStatus(`Network error: ${e.message}`);
    return;
  }
  if (resp.status === 503) { setHistoryStatus('History not configured on the server yet.'); return; }
  if (!resp.ok) { setHistoryStatus(`Server error: ${resp.status}`); return; }
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
    showCoinsMessage(`Network error: ${e.message}`);
    return;
  }
  if (resp.status === 401) { showCoinsMessage('Bad API key — open Settings.'); return; }
  if (resp.status === 503) { showCoinsMessage('History not configured on the server yet.'); return; }
  if (!resp.ok) { showCoinsMessage(`Server error: ${resp.status}`); return; }
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
  else if (action === 'signin') openLoginDialog();
  else if (action === 'signout') signOut();
  else if (action === 'prices') {
    const onPrices = $('#reports-view').hidden && $('#portfolio-view').hidden;
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
  $('#settings-dialog').showModal();
}
$('#settings-dialog').addEventListener('close', () => {
  if ($('#settings-dialog').returnValue === 'save') {
    saveApiKey($('#api-key').value);
    const theme = document.querySelector('input[name="theme"]:checked')?.value || 'dark';
    saveTheme(theme);
    applyTheme(theme);
    fetchSpot();   // immediately try with the new key
  }
});

// Spot price: load on page open, then auto-refresh while visible.
fetchSpot();
// Default size selection: load 10 g listings as soon as the page opens.
fetchPrices(10);
// Restore tab state from localStorage (defaults to 'bars').
setTab(currentTab);
setInterval(() => {
  if (document.visibilityState === 'visible') fetchSpot();
}, SPOT_REFRESH_MS);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') fetchSpot();
});

// Service worker registration
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('service-worker.js').catch(() => {});
}

// View switching ————————————————————————————————————————————————————————————

function hideAllAuxViews() {
  $('#reports-view').hidden = true;
  $('#portfolio-view').hidden = true;
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
    if (!res.ok) throw new Error(`status ${res.status}`);
    rows = await res.json();
  } catch (e) {
    weeklyList.innerHTML = `<div class="muted-tiny">Error: ${e.message}</div>`;
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
    alert(`Download failed: status ${res.status}`);
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
  button.disabled = true;
  try {
    const res = await fetch(
      `${BACKEND_URL}/reports/generate?range=${range}`,
      { method: 'POST', headers: { 'X-API-Key': loadApiKey() } },
    );
    if (!res.ok) throw new Error(`status ${res.status}`);
    await streamToFileFromResponse(res);
    status.innerHTML = '';
  } catch (e) {
    status.textContent = `Error: ${e.message}`;
  } finally {
    button.disabled = false;
  }
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
let currentUser = null;
let lastPortfolio = { purchases: [], summary: null };
let portfolioSort = { col: 'date', dir: 'desc' };

function updateAuthUI() {
  const signedIn = currentUser != null;
  $('.menu-item[data-action="portfolio"]').hidden = !signedIn;
  $('.menu-item[data-action="signin"]').hidden = signedIn;
  $('.menu-item[data-action="signout"]').hidden = !signedIn;
  const accountInfo = $('.menu-account-info');
  accountInfo.hidden = !signedIn;
  if (signedIn) accountInfo.textContent = currentUser.email;
}

async function loadAuthState() {
  try {
    const res = await fetch(`${BACKEND_URL}/auth/me`, { credentials: 'include' });
    if (res.ok) currentUser = await res.json();
    else currentUser = null;
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
  $('#login-dialog').showModal();
  setTimeout(() => $('#login-email').focus(), 30);
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
  $('#login-submit').disabled = true;
  try {
    const res = await fetch(`${BACKEND_URL}/auth/request-link`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    if (res.status === 429) {
      errEl.textContent = 'Too many attempts. Wait a few minutes and try again.';
      errEl.hidden = false;
      return;
    }
    if (!res.ok) {
      errEl.textContent = `Couldn't send link (status ${res.status}). Try again.`;
      errEl.hidden = false;
      return;
    }
    $('#login-stage-2-email').textContent = email;
    $('#login-stage-1').hidden = true;
    $('#login-stage-2').hidden = false;
  } catch (err) {
    errEl.textContent = `Network error: ${err.message}`;
    errEl.hidden = false;
  } finally {
    $('#login-submit').disabled = false;
  }
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
    if (!$('#portfolio-view').hidden) showPricesView();
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
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    if (!res.ok) {
      const msg = res.status === 400
        ? 'This link is invalid or expired.'
        : `Sign-in failed (status ${res.status}).`;
      contentEl.innerHTML = `
        <h2>Sign-in failed</h2>
        <p>${msg}</p>
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
    currentUser = user;
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
      <p>Network error: ${escapeHtml(err.message)}</p>
    `;
  }
  return true;
}

async function signOut() {
  try {
    await fetch(`${BACKEND_URL}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    });
  } catch {}
  currentUser = null;
  try { localStorage.setItem(SESSION_BROADCAST_KEY, ''); } catch {}
  updateAuthUI();
  if (!$('#portfolio-view').hidden) showPricesView();
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
    const res = await fetch(`${BACKEND_URL}/portfolio`, { credentials: 'include' });
    if (res.status === 401) { currentUser = null; updateAuthUI(); showPricesView(); openLoginDialog(); return; }
    if (!res.ok) {
      loadingEl.innerHTML = `Failed to load portfolio (status ${res.status}).`;
      return;
    }
    const data = await res.json();
    lastPortfolio = data;
    renderPortfolioSummary(data.summary);
    renderPortfolioTable(data.purchases);
  } catch (e) {
    loadingEl.innerHTML = `Network error: ${escapeHtml(e.message)}`;
  } finally {
    loadingEl.hidden = true;
  }
}

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
  const isEmpty = !(m.paid_dkk > 0);
  if (isEmpty) {
    return `
      <div class="metal-panel is-empty">
        <div class="metal-panel-head"><span class="metal-chip metal-${metal}">${metal}</span></div>
        <div class="metal-panel-empty">No ${metal} purchases yet.</div>
      </div>
    `;
  }
  const pnl = m.value_dkk - m.paid_dkk;
  const pnlPct = (pnl / m.paid_dkk) * 100;
  const pnlClass = pnl >= 0 ? 'pnl-pos' : 'pnl-neg';
  return `
    <div class="metal-panel">
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
// `okLabel` if "Confirm" isn't right (e.g. "Delete").
function confirmDialog({ title = 'Confirm', message, okLabel = 'Confirm' }) {
  return new Promise((resolve) => {
    const dlg = $('#confirm-dialog');
    $('#confirm-dialog-title').textContent = title;
    $('#confirm-dialog-message').textContent = message;
    $('#confirm-dialog-ok').textContent = okLabel;
    const onClose = () => {
      dlg.removeEventListener('close', onClose);
      resolve(dlg.returnValue === 'save');
    };
    dlg.addEventListener('close', onClose);
    dlg.showModal();
  });
}

// Canonical site-wide date format: DD-MM-YYYY (see CLAUDE.md "Date format").
function fmtDate(isoOrDate) {
  const d = isoOrDate instanceof Date ? isoOrDate : new Date(isoOrDate);
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const yyyy = d.getFullYear();
  return `${dd}-${mm}-${yyyy}`;
}

function renderPortfolioTable(purchases) {
  const tableEl = $('#portfolio-table');
  const emptyEl = $('#portfolio-empty');
  const tbody = tableEl.querySelector('tbody');
  if (!purchases.length) {
    tableEl.hidden = true;
    emptyEl.hidden = false;
    tbody.innerHTML = '';
    updatePortfolioSortIndicators();
    return;
  }
  emptyEl.hidden = true;
  tableEl.hidden = false;
  const sorted = [...purchases].sort((a, b) => {
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
        <td>${escapeHtml(p.label)}</td>
        <td><span class="metal-chip metal-${p.metal}">${p.metal}</span></td>
        <td>${gross} g</td>
        <td><button class="row-delete icon-btn" aria-label="Delete" data-id="${p.id}" type="button">✕</button></td>
      </tr>
    `;
  }).join('');
  updatePortfolioSortIndicators();
}

function updatePortfolioSortIndicators() {
  document.querySelectorAll('#portfolio-table th.sortable').forEach(th => {
    const active = th.dataset.sort === portfolioSort.col;
    th.classList.toggle('sort-active', active);
    th.classList.toggle('sort-desc', active && portfolioSort.dir === 'desc');
    th.classList.toggle('sort-asc', active && portfolioSort.dir === 'asc');
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
      <section class="pd-section">
        <h4 class="pd-section-head">Spec</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Fine weight</span><span class="pd-value">${fmtFineG(p.fine_weight_g)} g</span></div>
          <div class="pd-cell"><span class="pd-label">Purity</span><span class="pd-value">${p.purity}</span></div>
          ${p.dealer ? `<div class="pd-cell"><span class="pd-label">Dealer</span><span class="pd-value">${escapeHtml(p.dealer)}</span></div>` : ''}
        </div>
      </section>
      <section class="pd-section">
        <h4 class="pd-section-head">At purchase</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Price paid</span><span class="pd-value">${fmtDKK(p.price_paid_dkk)}</span></div>
          <div class="pd-cell"><span class="pd-label">Spot then</span><span class="pd-value">${spotThen}</span></div>
          <div class="pd-cell"><span class="pd-label">Purchase premium</span><span class="pd-value">${purchasePrem}</span></div>
        </div>
      </section>
      <section class="pd-section">
        <h4 class="pd-section-head">Now</h4>
        <div class="pd-section-grid">
          <div class="pd-cell"><span class="pd-label">Current spot</span><span class="pd-value">${fmtSpotDKK(p.current_spot_dkk_per_g)}/g</span></div>
          <div class="pd-cell"><span class="pd-label">Current value</span><span class="pd-value">${fmtDKK(p.current_value_dkk)}</span></div>
          <div class="pd-cell"><span class="pd-label">P&amp;L</span><span class="pd-value ${pnlClass}">${fmtDKKSigned(p.pnl_dkk)} (${fmtPctSigned(p.pnl_pct)})</span></div>
        </div>
      </section>
      ${p.notes ? `<section class="pd-section pd-section-notes"><h4 class="pd-section-head">Notes</h4><p class="pd-notes">${escapeHtml(p.notes)}</p></section>` : ''}
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
  const del = e.target.closest('.row-delete');
  if (del) {
    e.stopPropagation();
    const ok = await confirmDialog({
      title: 'Delete purchase',
      message: 'This permanently removes the purchase from your portfolio. This cannot be undone.',
      okLabel: 'Delete',
    });
    if (!ok) return;
    try {
      const res = await fetch(`${BACKEND_URL}/portfolio/${del.dataset.id}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!res.ok && res.status !== 204) {
        alert(`Delete failed (status ${res.status})`);
        return;
      }
      await loadPortfolio();
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
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
  tr.innerHTML = `<td colspan="5">${buildPortfolioDetail(purchase)}</td>`;
  row.after(tr);
  row.classList.add('is-expanded');
});

// Add-purchase dialog ———————————————————————————————————————————————————————

$('#portfolio-add-btn').addEventListener('click', () => {
  $('#purchase-form').reset();
  $('#purchase-purity').value = '0.9999';
  // Default purchase date to today in the user's local timezone (date input
  // expects yyyy-mm-dd; the API gets noon UTC at submit time).
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  $('#purchase-date').value = `${yyyy}-${mm}-${dd}`;
  $('#purchase-error').hidden = true;
  $('#purchase-dialog').showModal();
});

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

  $('#purchase-submit').disabled = true;
  try {
    const res = await fetch(`${BACKEND_URL}/portfolio`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        metal, label,
        gross_weight_g: gross, purity, price_paid_dkk: price,
        purchased_at, dealer, notes,
      }),
    });
    if (res.status === 401) {
      $('#purchase-dialog').close();
      currentUser = null; updateAuthUI();
      showPricesView(); openLoginDialog();
      return;
    }
    if (!res.ok) {
      const body = await res.text();
      errEl.textContent = `Save failed (status ${res.status}): ${body.slice(0, 200)}`;
      errEl.hidden = false;
      return;
    }
    $('#purchase-dialog').close();
    await loadPortfolio();
  } catch (err) {
    errEl.textContent = `Network error: ${err.message}`;
    errEl.hidden = false;
  } finally {
    $('#purchase-submit').disabled = false;
  }
});

// Boot: handle verify fragment first, then resolve auth state.
(async () => {
  const handled = await handleVerifyFragment();
  if (!handled) await loadAuthState();
})();
