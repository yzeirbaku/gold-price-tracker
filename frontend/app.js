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
    const ind = th.querySelector('.sort-indicator');
    if (th.dataset.sort === sortState.col) {
      th.classList.add('active');
      ind.textContent = sortState.dir === 'asc' ? ' ↑' : ' ↓';
    } else {
      th.classList.remove('active');
      ind.textContent = '';
    }
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
    const ind = th.querySelector('.sort-indicator');
    if (th.dataset.sort === coinSortState.col) {
      th.classList.add('active');
      ind.textContent = coinSortState.dir === 'asc' ? ' ↑' : ' ↓';
    } else {
      th.classList.remove('active');
      ind.textContent = '';
    }
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
  else if (action === 'prices') showPricesView();
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

function showPricesView() {
  $('#reports-view').hidden = true;
  $('#spot').hidden = false;
  $('#tab-strip').hidden = false;
  // Restore the bars/coins tab the user was last on.
  setTab(currentTab);
}

function showReportsView() {
  $('#bars-view').hidden = true;
  $('#coins-view').hidden = true;
  $('#reports-view').hidden = false;
  $('#spot').hidden = true;
  $('#tab-strip').hidden = true;
  document.querySelectorAll('#tab-strip button').forEach((b) => {
    b.classList.remove('active');
    b.setAttribute('aria-selected', 'false');
  });
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
  status.textContent = 'Generating…';
  button.disabled = true;
  try {
    const res = await fetch(
      `${BACKEND_URL}/reports/generate?range=${range}`,
      { method: 'POST', headers: { 'X-API-Key': loadApiKey() } },
    );
    if (!res.ok) throw new Error(`status ${res.status}`);
    await streamToFileFromResponse(res);
    status.textContent = '';
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
