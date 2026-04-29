// AI Recos tab — severity filter pills, structured reco cards, "Analyze with AI" panel.
// Card rendering + expand/collapse live in /web/recos.js (shared with the
// Overview Quick Wins panel). This route owns: data fetch, filtering, and
// the AI analysis side-panel.
import { api, fmt } from '/web/app.js';
import { renderReco, severityOf, attachExpandHandlers } from '/web/recos.js';

const FILTERS = [
  { key: 'all',      label: 'All' },
  { key: 'critical', label: 'Critical' },
  { key: 'warning',  label: 'Warning' },
  { key: 'info',     label: 'Info' },
];

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
  const tips = await api('/api/tips');

  const sorted = Array.isArray(tips)
    ? tips.slice().sort((a, b) => (b.estimated_savings || 0) - (a.estimated_savings || 0))
    : [];

  const counts = { all: sorted.length, critical: 0, warning: 0, info: 0 };
  for (const t of sorted) counts[severityOf(t)] = (counts[severityOf(t)] || 0) + 1;

  const filtered = filter.key === 'all'
    ? sorted
    : sorted.filter(t => severityOf(t) === filter.key);

  const filterPills = `
    <div class="range-tabs" role="tablist">
      ${FILTERS.map(f => `
        <button data-filter="${f.key}" class="${f.key === filter.key ? 'active' : ''}">
          ${f.label} <span class="muted" style="margin-left:4px;font-family:var(--mono);font-size:11px">${counts[f.key] || 0}</span>
        </button>`).join('')}
    </div>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:20px">
      <h2 style="margin:0;font-family:var(--display);font-weight:500;font-size:24px">AI Recos</h2>
      <span class="muted" style="font-size:12px">rule-based detection · top by estimated savings</span>
      <div class="spacer"></div>
      <button class="ai" id="btn-analyze">Analyze with AI ✨</button>
    </div>

    <div class="card">
      <div class="flex" style="margin-bottom:14px">
        ${filterPills}
      </div>
      <div id="reco-list">
        ${filtered.length === 0
          ? `<p class="muted" style="margin:0;font-size:13px">No ${filter.key === 'all' ? 'recommendations' : filter.key + ' recos'} right now. Run a scan to recompute.</p>`
          : filtered.map(t => renderReco(t)).join('')}
      </div>
    </div>

    <div id="ai-out"></div>
  `;

  // Filter chip clicks — full re-render via hashchange (existing pattern).
  root.querySelectorAll('.range-tabs button[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => writeFilter(btn.dataset.filter));
  });

  // Expand/collapse — single delegated listener on the list container.
  // No full re-render; toggles aria-expanded + .reco--expanded class.
  const list = root.querySelector('#reco-list');
  if (list) attachExpandHandlers(list);

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
