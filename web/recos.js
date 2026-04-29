// AI Recos shared render module — used by both the AI Recos tab
// (web/routes/tips.js) and the Overview Quick Wins panel
// (web/routes/overview.js). Reads the structured fields Phase 1 added and
// Phase 1.5 polished: title, where, what, how_to_fix, occurred_at, deep_link.
//
// Expand state lives in a module-scope Map keyed by tip.key. It survives
// hash navigation, route remounts, and SSE-triggered re-renders. It resets
// only on actual page refresh — by virtue of the module being re-imported.
import { fmt } from '/web/app.js';

const expandState = new Map();
const ATTACHED = new WeakSet();

export function severityOf(t) {
  const s = String((t && t.severity) || '').toLowerCase();
  return (s === 'critical' || s === 'warning' || s === 'info') ? s : 'info';
}

export function titleFromRule(ruleId) {
  if (!ruleId) return 'Recommendation';
  return String(ruleId)
    .replace(/_/g, ' ')
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase());
}

export function compact(n) {
  const x = Number(n || 0);
  if (Math.abs(x) >= 1e9) return (x / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
  if (Math.abs(x) >= 1e6) return (x / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (Math.abs(x) >= 1e3) return (x / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return Math.round(x).toLocaleString();
}

function iconFor(severity) {
  if (severity === 'critical') return '!';
  if (severity === 'warning')  return '▲';
  return 'i';
}

function severityLabel(severity) {
  return severity.charAt(0).toUpperCase() + severity.slice(1);
}

const defaultExpanded = (sev) => sev === 'critical';

function isExpanded(t, sev) {
  const key = t && t.key;
  if (key && expandState.has(key)) return expandState.get(key);
  return defaultExpanded(sev);
}

function row(label, value) {
  if (!value) return '';
  return `<div class="reco__row">
    <div class="reco__label">${label}</div>
    <div class="reco__value">${fmt.htmlSafe(value)}</div>
  </div>`;
}

export function renderReco(t, opts = {}) {
  const { compact: isCompact = false } = opts;
  const sev = severityOf(t);
  const title = (t && t.title) || titleFromRule(t && t.rule_id);
  const expanded = isExpanded(t, sev);

  const dateStr = t && t.occurred_at ? fmt.tsLong(t.occurred_at) : '';
  const meta = dateStr
    ? `${severityLabel(sev)} · ${fmt.htmlSafe(dateStr)}`
    : severityLabel(sev);

  const saves = t && t.estimated_savings
    ? `<span class="reco__saves">~${fmt.htmlSafe(compact(t.estimated_savings))} saved</span>`
    : '';

  // Body rows. If structured fields are missing (e.g. an older row not yet
  // recomputed), fall back to the legacy `message` so users still see prose.
  let bodyRows = '';
  if (t && (t.where || t.what || t.how_to_fix)) {
    bodyRows = row('Where', t.where) + row('What', t.what) + row('How to fix', t.how_to_fix);
  } else if (t && (t.message || t.body)) {
    bodyRows = row('Note', t.message || t.body);
  }

  const footer = t && t.deep_link
    ? `<div class="reco__footer">
        <a href="#${fmt.htmlSafe(t.deep_link)}">View autopsy →</a>
      </div>`
    : '';

  const compactCls = isCompact ? ' reco--compact' : '';
  const expandedCls = expanded ? ' reco--expanded' : '';
  const dataKey = t && t.key ? ` data-key="${fmt.htmlSafe(t.key)}"` : '';

  return `<article class="reco reco--${sev}${compactCls}${expandedCls}"${dataKey}>
    <button class="reco__header" type="button" aria-expanded="${expanded}">
      <div class="reco__icon">${iconFor(sev)}</div>
      <div class="reco__head-body">
        <div class="reco__title">${fmt.htmlSafe(title)}</div>
        <div class="reco__meta">${meta}</div>
      </div>
      ${saves}
    </button>
    <div class="reco__body">
      ${bodyRows}
      ${footer}
    </div>
  </article>`;
}

// Wire one click listener per route container (event delegation). Idempotent:
// if attached more than once on the same root, the second call is a no-op.
// Direct DOM manipulation per click — no full re-render, per spec.
export function attachExpandHandlers(root) {
  if (!root || ATTACHED.has(root)) return;
  ATTACHED.add(root);
  root.addEventListener('click', (e) => {
    const header = e.target.closest('.reco__header');
    if (!header || !root.contains(header)) return;
    const article = header.closest('.reco');
    if (!article) return;
    const newState = !article.classList.contains('reco--expanded');
    const key = article.dataset.key;
    if (key) expandState.set(key, newState);
    header.setAttribute('aria-expanded', String(newState));
    article.classList.toggle('reco--expanded', newState);
  });
}
