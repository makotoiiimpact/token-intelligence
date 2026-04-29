// AI Recos tab — severity filter pills, structured reco cards,
// dismiss/undismiss flow, "Analyze with AI" panel.
//
// Card rendering + expand/collapse + dismiss button live in /web/recos.js
// (shared with the Overview Quick Wins panel). This route owns: data
// fetch, filtering, the show-dismissed toggle, optimistic dismiss UI,
// and the AI analysis side-panel.
import { api, fmt } from '/web/app.js';
import { renderReco, severityOf, attachExpandHandlers, attachDismissHandlers } from '/web/recos.js';

const FILTERS = [
  { key: 'all',      label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'warning',  label: 'Warning' },
  { key: 'info',     label: 'Info' },
];

// Module-scope route state. Resets on page reload.
let allTips = [];
let showDismissed = false;

function readFilter() {
  const q = (location.hash.split('?')[1] || '');
  const m = /(?:^|&)filter=([^&]+)/.exec(q);
  const k = m && decodeURIComponent(m[1]);
  return FILTERS.find(f => f.key === k) || FILTERS[0];
}

function writeFilter(key) {
  const base = (location.hash.replace(/^#/, '').split('?')[0]) || '/ai-recos';
  location.hash = '#' + base + '?filter=' + encodeURIComponent(key);
}

function visibleSet() {
  return showDismissed
    ? allTips.filter(t => t.dismissed)
    : allTips.filter(t => !t.dismissed);
}

function chipCounts(set) {
  const c = { all: set.length, critical: 0, warning: 0, info: 0 };
  for (const t of set) c[severityOf(t)] = (c[severityOf(t)] || 0) + 1;
  return c;
}

function renderListHtml(filter) {
  const set = visibleSet();
  const filtered = filter.key === 'all'
    ? set
    : set.filter(t => severityOf(t) === filter.key);
  filtered.sort((a, b) => (b.estimated_savings || 0) - (a.estimated_savings || 0));
  if (filtered.length === 0) {
    const empty = showDismissed
      ? 'No dismissed recommendations.'
      : `No ${filter.key === 'all' ? 'recommendations' : filter.key + ' recos'} right now. Run a scan to recompute.`;
    return `<p class="muted" style="margin:0;font-size:13px">${empty}</p>`;
  }
  return filtered.map(t => renderReco(t)).join('');
}

function updateChipCounts() {
  const counts = chipCounts(visibleSet());
  document.querySelectorAll('.range-tabs button[data-filter]').forEach(btn => {
    const span = btn.querySelector('.muted');
    if (span) span.textContent = String(counts[btn.dataset.filter] || 0);
  });
}

function updateToggleLabel() {
  const btn = document.getElementById('toggle-dismissed');
  if (!btn) return;
  const n = allTips.filter(t => t.dismissed).length;
  btn.textContent = showDismissed ? 'Hide dismissed' : `Show dismissed (${n})`;
  btn.classList.toggle('active', showDismissed);
}

// Optimistic dismiss / undismiss: remove article + fire-and-forget network.
// On network failure: console.error; the next SSE re-render reconciles state.
function onDismiss(key, articleEl) {
  const tip = allTips.find(t => t.key === key);
  if (tip) tip.dismissed = true;
  articleEl.remove();
  updateChipCounts();
  updateToggleLabel();
  fetch('/api/tips/dismiss', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  }).catch(console.error);
}

function onUndismiss(key, articleEl) {
  const tip = allTips.find(t => t.key === key);
  if (tip) tip.dismissed = false;
  articleEl.remove();
  updateChipCounts();
  updateToggleLabel();
  fetch('/api/tips/dismiss', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  }).catch(console.error);
}

function renderAiPanel(result) {
  if (!result) return '';
  if (result.configured === false) {
    return `
      <div class="ai-panel">
        <h3>AI analysis unavailable</h3>
        <div class="summary">${fmt.htmlSafe(result.error || 'Configure API key in Settings')}</div>
      </div>`;
  }
  const summary = fmt.htmlSafe(result.summary || '');
  const recs = Array.isArray(result.recommendations) ? result.recommendations : [];
  const cached = result.cached ? ' · cached' : '';
  const model = result.model_used ? ` · ${fmt.htmlSafe(result.model_used)}` : '';
  const when = result.generated_at ? ` · ${fmt.ts(result.generated_at)}` : '';
  return `
    <div class="ai-panel">
      <h3>AI analysis</h3>
      ${summary ? `<div class="summary">${summary}</div>` : ''}
      ${recs.length ? `<ul>${recs.map(r => `<li>${fmt.htmlSafe(r)}</li>`).join('')}</ul>` : ''}
      <div class="meta">generated${when}${model}${cached}</div>
    </div>`;
}

export default async function (root) {
  const filter = readFilter();
  // Single fetch with both buckets — partition client-side. Means the
  // "Show dismissed (N)" count is always accurate without a second call.
  const tips = await api('/api/tips?include_dismissed=1');
  allTips = Array.isArray(tips) ? tips : [];
  const set = visibleSet();
  const counts = chipCounts(set);
  const dismissedCount = allTips.filter(t => t.dismissed).length;

  const filterPills = `
    <div class="range-tabs" role="tablist">
      ${FILTERS.map(f => `
        <button data-filter="${f.key}" class="${f.key === filter.key ? 'active' : ''}">
          ${f.label} <span class="muted" style="margin-left:4px;font-family:var(--mono);font-size:11px">${counts[f.key] || 0}</span>
        </button>`).join('')}
    </div>`;

  const toggleBtn = `
    <button id="toggle-dismissed" type="button" class="dismissed-toggle ${showDismissed ? 'active' : ''}">
      ${showDismissed ? 'Hide dismissed' : `Show dismissed (${dismissedCount})`}
    </button>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:20px">
      <h2 style="margin:0;font-family:var(--display);font-weight:500;font-size:24px">AI Recos</h2>
      <span class="muted" style="font-size:12px">rule-based detection · top by estimated savings</span>
      <div class="spacer"></div>
      <button class="ai" id="btn-analyze">Analyze with AI ✨</button>
    </div>

    <div class="card" id="reco-controls">
      <div class="flex" style="margin-bottom:14px">
        ${filterPills}
        <div class="spacer"></div>
        ${toggleBtn}
      </div>
      <div id="reco-list">${renderListHtml(filter)}</div>
    </div>

    <div id="ai-out"></div>
  `;

  // Filter chip clicks — full re-render via hashchange (existing pattern).
  root.querySelectorAll('.range-tabs button[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => writeFilter(btn.dataset.filter));
  });

  // Show / Hide dismissed toggle — flips view and re-renders the list +
  // chip counts inline. No full route remount; preserves the AI panel state
  // and the current filter chip selection.
  root.querySelector('#toggle-dismissed')?.addEventListener('click', () => {
    showDismissed = !showDismissed;
    const f = readFilter();
    document.getElementById('reco-list').innerHTML = renderListHtml(f);
    updateChipCounts();
    updateToggleLabel();
  });

  // Expand/collapse + Dismiss/Undismiss — single delegated listener pair on
  // the controls container. Survives inner innerHTML replacements (toggle
  // click swaps the list contents but the controls wrapper stays).
  const controls = root.querySelector('#reco-controls');
  if (controls) {
    attachExpandHandlers(controls);
    attachDismissHandlers(controls, { onDismiss, onUndismiss });
  }

  const btn = root.querySelector('#btn-analyze');
  const out = root.querySelector('#ai-out');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = 'Analyzing…';
    out.innerHTML = '';
    try {
      const r = await fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await r.json();
      out.innerHTML = renderAiPanel(data);
    } catch (e) {
      out.innerHTML = `
        <div class="ai-panel">
          <h3>AI analysis failed</h3>
          <div class="summary">${fmt.htmlSafe(String(e.message || e))}</div>
        </div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = prev;
    }
  });
}
