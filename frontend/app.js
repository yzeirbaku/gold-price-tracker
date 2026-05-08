const API_KEY_STORAGE = 'gold-tracker-api-key';
const BACKEND_URL = (window.BACKEND_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
const SPOT_REFRESH_MS = 20000;
const $ = (s) => document.querySelector(s);

function loadApiKey() { return localStorage.getItem(API_KEY_STORAGE) || ''; }
function saveApiKey(k) { localStorage.setItem(API_KEY_STORAGE, k); }

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

let lastSize = null;
let hasRenderedSpot = false;
let lastListings = [];           // cached so we can re-sort without re-fetching
let sortState = { col: 'price', dir: 'asc' };  // default matches backend order

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
  tbody.innerHTML = '';
  updateSortIndicators();
  const ordered = sortListings(lastListings);
  for (const li of ordered) {
    const tr = document.createElement('tr');
    tr.className = li.status;
    const brand = li.brand ? escapeHtml(li.brand) : '—';
    if (li.status === 'ok') {
      tr.innerHTML = `
        <td>
          <a class="dealer-link" href="${li.url}" target="_blank" rel="noopener">${escapeHtml(li.dealer)}<span class="visit-arrow" aria-hidden="true">↗</span></a>
          <button type="button" class="history-btn" data-dealer="${escapeHtml(li.dealer)}" aria-label="Show price history">📈</button>
        </td>
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

// History modal —————————————————————————————————————————————————————————
let historyState = { dealer: null, size: null, range: '30d' };
const historyCharts = { price: null, premium: null };

function openHistory(dealer, size) {
  historyState = { dealer, size, range: '30d' };
  $('#history-title').textContent = `${dealer} — ${size} g`;
  document.querySelectorAll('#history-dialog .range-toggle button').forEach(b => {
    b.classList.toggle('active', b.dataset.range === '30d');
  });
  $('#history-dialog').showModal();
  fetchHistory();
}

function closeHistory() {
  $('#history-dialog').close();
  // Free Chart.js instances so the canvases can be re-initialized cleanly next time.
  for (const k of Object.keys(historyCharts)) {
    if (historyCharts[k]) { historyCharts[k].destroy(); historyCharts[k] = null; }
  }
}

async function fetchHistory() {
  const apiKey = loadApiKey();
  if (!apiKey) { setHistoryStatus('Open Settings to configure your API key.'); return; }
  const { dealer, size, range } = historyState;
  setHistoryStatus('Loading…');
  let resp;
  try {
    resp = await fetch(
      `${BACKEND_URL}/history/dealer/${encodeURIComponent(dealer)}/${size}?range=${range}`,
      { headers: { 'X-API-Key': apiKey } },
    );
  } catch (e) {
    setHistoryStatus(`Network error: ${e.message}`);
    return;
  }
  if (resp.status === 503) { setHistoryStatus('History not configured on the server yet.'); return; }
  if (!resp.ok) { setHistoryStatus(`Server error: ${resp.status}`); return; }
  renderHistory(await resp.json());
}

function setHistoryStatus(msg) {
  $('#history-status').textContent = msg;
  $('#history-status').hidden = false;
}

function renderHistory(data) {
  const okPoints = data.points.filter(p =>
    p.status === 'ok' && p.price_dkk != null && p.spot_gold_dkk_per_g != null,
  );
  if (okPoints.length === 0) {
    setHistoryStatus('No data yet for this range.');
    for (const k of Object.keys(historyCharts)) {
      if (historyCharts[k]) { historyCharts[k].destroy(); historyCharts[k] = null; }
    }
    return;
  }
  $('#history-status').hidden = true;

  const pricePoints = okPoints.map(p => ({
    x: new Date(p.fetched_at).getTime(),
    y: p.price_dkk,
  }));
  const premiumPoints = okPoints.map(p => {
    const ref = p.spot_gold_dkk_per_g * data.size_g;
    return {
      x: new Date(p.fetched_at).getTime(),
      y: ref > 0 ? Number(((p.price_dkk - ref) / ref * 100).toFixed(2)) : null,
    };
  });

  drawChart('price', 'price-chart', pricePoints, 'DKK', '#e2c054', v => fmtNum(v, 0, 'da-DK'));
  drawChart('premium', 'premium-chart', premiumPoints, '%', '#7dc8a4', v => v.toFixed(1) + '%');
}

function drawChart(key, canvasId, points, unit, color, yFmt) {
  if (historyCharts[key]) historyCharts[key].destroy();
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
      scales: {
        x: {
          type: 'linear',
          ticks: {
            color: '#8a8a90',
            maxTicksLimit: 5,
            callback: v => new Date(v).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' }),
          },
          grid: { color: 'rgba(255,255,255,0.04)' },
        },
        y: {
          ticks: { color: '#8a8a90', callback: yFmt },
          grid: { color: 'rgba(255,255,255,0.04)' },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: items => new Date(items[0].parsed.x).toLocaleString(),
            label: item => `${yFmt(item.parsed.y)} ${unit === '%' ? '' : unit}`.trim(),
          },
        },
      },
    },
  });
}

// Range toggle inside the history dialog.
document.querySelectorAll('#history-dialog .range-toggle button').forEach(b => {
  b.addEventListener('click', () => {
    const range = b.dataset.range;
    if (range === historyState.range) return;
    historyState.range = range;
    document.querySelectorAll('#history-dialog .range-toggle button').forEach(x => {
      x.classList.toggle('active', x.dataset.range === range);
    });
    fetchHistory();
  });
});

$('#history-close').addEventListener('click', closeHistory);
$('#history-dialog').addEventListener('cancel', closeHistory);  // Esc key

// Listings tbody click — delegate to history-btn so we don't rebind handlers
// every render. Uses the row's size context (lastSize is the active size).
$('#listings tbody').addEventListener('click', (e) => {
  const btn = e.target.closest('.history-btn');
  if (!btn) return;
  const dealer = btn.dataset.dealer;
  if (dealer && lastSize != null) openHistory(dealer, lastSize);
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

// Settings dialog (API key only — backend URL comes from config.js)
$('#settings-btn').addEventListener('click', () => {
  $('#api-key').value = loadApiKey();
  $('#settings-dialog').showModal();
});
$('#settings-dialog').addEventListener('close', () => {
  if ($('#settings-dialog').returnValue === 'save') {
    saveApiKey($('#api-key').value);
    fetchSpot();   // immediately try with the new key
  }
});

// Spot price: load on page open, then auto-refresh while visible.
fetchSpot();
// Default size selection: load 10 g listings as soon as the page opens.
fetchPrices(10);
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
