const STORAGE_KEY = 'gold-tracker-config';
const $ = (s) => document.querySelector(s);

function loadConfig() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}; }
  catch { return {}; }
}
function saveConfig(c) { localStorage.setItem(STORAGE_KEY, JSON.stringify(c)); }

function fmtDKK(n) { return new Intl.NumberFormat('da-DK', { style: 'currency', currency: 'DKK', maximumFractionDigits: 0 }).format(n); }
function fmtEUR(n) { return new Intl.NumberFormat('da-DK', { style: 'currency', currency: 'EUR', maximumFractionDigits: 2 }).format(n); }
function fmtPct(n) { return n == null ? '—' : (n > 0 ? '+' : '') + n.toFixed(1) + '%'; }

let lastSize = null;

async function fetchPrices(size) {
  const cfg = loadConfig();
  if (!cfg.backendUrl || !cfg.apiKey) {
    showStatus('Open Settings to configure backend URL and API key.');
    return;
  }
  lastSize = size;
  showStatus(`Loading… first request after idle can take ~60 s.`);
  $('#listings').hidden = true;
  $('#refresh').hidden = true;
  setActiveSize(size);

  let resp;
  try {
    resp = await fetch(`${cfg.backendUrl.replace(/\/$/, '')}/prices/${size}`, {
      headers: { 'X-API-Key': cfg.apiKey },
    });
  } catch (e) {
    showStatus(`Network error: ${e.message}`);
    return;
  }
  if (resp.status === 401) { showStatus('Bad API key — open Settings.'); return; }
  if (!resp.ok) { showStatus(`Server error: ${resp.status}`); return; }
  const data = await resp.json();
  render(data);
}

function render(data) {
  if (data.spot) {
    const g = data.spot.gold, s = data.spot.silver;
    $('#spot-content').innerHTML = `
      <div class="spot-row"><span>Gold</span><span>${fmtEUR(g.per_gram_eur)}/g · ${fmtDKK(g.per_gram_dkk)}/g</span></div>
      <div class="spot-row"><span>Silver</span><span>${fmtEUR(s.per_gram_eur)}/g · ${fmtDKK(s.per_gram_dkk)}/g</span></div>
      ${data.fx_stale ? '<div class="spot-row" style="color:var(--error)">⚠ FX rates stale (fallback in use)</div>' : ''}
    `;
  } else {
    $('#spot-content').textContent = 'Spot price unavailable.';
  }

  const tbody = $('#listings tbody');
  tbody.innerHTML = '';
  for (const li of data.listings) {
    const tr = document.createElement('tr');
    tr.className = li.status;
    if (li.status === 'ok') {
      tr.innerHTML = `
        <td>${li.dealer}</td>
        <td>${fmtDKK(li.price_dkk)}</td>
        <td>${fmtPct(li.premium_pct)}</td>
        <td><a href="${li.url}" target="_blank" rel="noopener">→</a></td>
      `;
    } else {
      const note = li.status === 'out_of_stock' ? 'out of stock'
                : li.status === 'unavailable' ? 'price on request'
                : `error (${li.error || 'unknown'})`;
      tr.innerHTML = `<td>${li.dealer}</td><td colspan="3">${note}</td>`;
    }
    tbody.appendChild(tr);
  }
  $('#listings').hidden = false;
  $('#refresh').hidden = false;
  $('#status').textContent = `Updated ${new Date(data.fetched_at).toLocaleTimeString()}`;
}

function showStatus(msg) {
  $('#status').textContent = msg;
}
function setActiveSize(size) {
  document.querySelectorAll('#size-picker button').forEach(b => {
    b.classList.toggle('active', parseFloat(b.dataset.size) === parseFloat(size));
  });
}

// Wire up UI
document.querySelectorAll('#size-picker button').forEach(b => {
  b.addEventListener('click', () => fetchPrices(parseFloat(b.dataset.size)));
});
$('#refresh').addEventListener('click', () => { if (lastSize != null) fetchPrices(lastSize); });

// Settings dialog
$('#settings-btn').addEventListener('click', () => {
  const cfg = loadConfig();
  $('#backend-url').value = cfg.backendUrl || '';
  $('#api-key').value = cfg.apiKey || '';
  $('#settings-dialog').showModal();
});
$('#settings-dialog').addEventListener('close', () => {
  if ($('#settings-dialog').returnValue === 'save') {
    saveConfig({ backendUrl: $('#backend-url').value, apiKey: $('#api-key').value });
    showStatus('Settings saved.');
  }
});

// Service worker registration (Task 24)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('service-worker.js').catch(() => {});
}
