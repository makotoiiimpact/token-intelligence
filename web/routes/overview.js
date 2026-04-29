import { api, fmt, state, getThresholds } from '/web/app.js';
import { areaChartWithThresholds, horizontalBarChart, donutChart } from '/web/charts.js';
import { renderReco, compact, attachExpandHandlers } from '/web/recos.js';

const RANGES = [
  { key: '7d',  label: '7d',  days: 7 },
  { key: '30d', label: '30d', days: 30 },
  { key: '90d', label: '90d', days: 90 },
  { key: 'all', label: 'All', days: null },
];

function readRange() {
  const q = (location.hash.split('?')[1] || '');
  const m = /(?:^|&)range=([^&]+)/.exec(q);
  const k = m && decodeURIComponent(m[1]);
  return RANGES.find(r => r.key === k) || RANGES[1];
}

function writeRange(key) {
  const base = (location.hash.replace(/^#/, '').split('?')[0]) || '/overview';
  location.hash = '#' + base + '?range=' + encodeURIComponent(key);
}

function sinceIso(range) {
  if (!range.days) return null;
  return new Date(Date.now() - range.days * 86400 * 1000).toISOString();
}

function withSince(url, since) {
  if (!since) return url;
  return url + (url.includes('?') ? '&' : '?') + 'since=' + encodeURIComponent(since);
}

function healthBand(score) {
  if (score == null) return { band: '—', color: 'rgba(255,255,255,0.25)' };
  if (score >= 80)   return { band: 'good',  color: '#19F58C' };
  if (score >= 50)   return { band: 'warn',  color: '#FFD600' };
  return               { band: 'bad',   color: '#FF423D' };
}

function healthRing(score) {
  const clamped = Math.max(0, Math.min(100, Math.round(score ?? 0)));
  const { color } = healthBand(score);
  const C = 2 * Math.PI * 60; // circumference for r=60
  const offset = C * (1 - clamped / 100);
  return `
    <div class="health-ring" aria-label="Average health score ${clamped} out of 100">
      <svg viewBox="0 0 140 140">
        <circle class="track" cx="70" cy="70" r="60" stroke-width="10" fill="none"/>
        <circle class="fill"  cx="70" cy="70" r="60" stroke-width="10" fill="none"
                stroke="${color}"
                stroke-dasharray="${C.toFixed(2)}"
                stroke-dashoffset="${offset.toFixed(2)}"/>
      </svg>
      <div class="center">
        <div class="num">${score == null ? '—' : clamped}</div>
        <div class="lbl">Health</div>
      </div>
    </div>`;
}

function severityFromScore(score) {
  if (score == null) return '';
  if (score >= 80) return 'good';
  if (score >= 50) return 'warn';
  return 'bad';
}

function animateCounter(el, target, opts = {}) {
  const duration = opts.duration || 900;
  const format = opts.format || (v => Math.round(v).toLocaleString());
  const start = performance.now();
  function step(t) {
    const p = Math.min(1, (t - start) / duration);
    // ease-out cubic
    const eased = 1 - Math.pow(1 - p, 3);
    el.textContent = format(target * eased);
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

export default async function (root) {
  const range = readRange();
  const since = sinceIso(range);

  const [totals, projects, sessions, daily, byModel, health, tips] = await Promise.all([
    api(withSince('/api/overview', since)),
    api(withSince('/api/projects', since)),
    api(withSince('/api/sessions?limit=10', since)),
    api(withSince('/api/daily', since)),
    api(withSince('/api/by-model', since)),
    api('/api/health').catch(() => []),
    api('/api/tips').catch(() => []),
  ]);

  // Total billable tokens (input + output + cache create 5m/1h)
  const billable =
    (totals.input_tokens || 0) +
    (totals.output_tokens || 0) +
    (totals.cache_create_5m_tokens || 0) +
    (totals.cache_create_1h_tokens || 0);

  // Health score aggregate — build a session_id → health lookup
  const healthList = Array.isArray(health) ? health : [];
  const avgHealth = healthList.length
    ? healthList.reduce((s, h) => s + (Number(h.health_score) || 0), 0) / healthList.length
    : null;
  const healthById = new Map(healthList.map(h => [h.session_id, Number(h.health_score)]));

  // Top 3 tips by estimated savings
  const tipsArr = Array.isArray(tips) ? tips : [];
  const quickWins = tipsArr
    .slice()
    .sort((a, b) => (b.estimated_savings || 0) - (a.estimated_savings || 0))
    .slice(0, 3);

  const healthBadge = (score) => {
    const band = severityFromScore(score);
    if (!band) return '';
    return `<span class="badge-health ${band}">${Math.round(score)}</span>`;
  };

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  // Hero metrics — 4 cards (each gets a subtle accent tint)
  const heroMetrics = `
    <div class="row cols-4">
      <div class="card card-tint-green metric">
        <div class="label">Total tokens</div>
        <div class="value" id="m-tokens" title="${fmt.int(billable)} billable tokens">0</div>
        <div class="sub">input + output + cache create</div>
      </div>
      <div class="card card-tint-blue metric">
        <div class="label">Sessions</div>
        <div class="value" id="m-sessions" title="${fmt.int(totals.sessions)}">0</div>
        <div class="sub">${fmt.int(totals.turns)} turns total</div>
      </div>
      <div class="card card-tint-cyan" style="display:flex;align-items:center;gap:20px">
        ${healthRing(avgHealth)}
        <div class="metric" style="gap:6px">
          <div class="label">Avg health</div>
          <div class="sub" style="font-family:var(--sans);color:var(--text-2);font-size:13px;max-width:180px;line-height:1.45">
            ${avgHealth == null
              ? 'Run a scan to compute session health scores.'
              : `Across ${healthList.length} session${healthList.length === 1 ? '' : 's'}.`}
          </div>
        </div>
      </div>
      <div class="card card-tint-purple metric good">
        <div class="label">Est. cost</div>
        <div class="value" id="m-cost" title="${fmt.usd(totals.cost_usd)}">—</div>
        ${planSub()}
      </div>
    </div>`;

  // Quick Wins panel — top 3 AI Recos rendered via the shared module.
  const quickWinsHtml = quickWins.length === 0
    ? `<p class="muted" style="margin:0;font-size:13px">No recommendations detected — nice hygiene. Run a fresh scan to recompute.</p>`
    : quickWins.map(t => renderReco(t, { compact: true })).join('');

  root.innerHTML = `
    <div class="flex" style="margin-bottom:20px">
      <h2 style="margin:0;font-family:var(--display);font-weight:500;font-size:24px">Overview</h2>
      <span class="muted" style="font-size:12px">${range.days ? `last ${range.days} days` : 'all time'}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>

    ${heroMetrics}

    <div class="row cols-2-wide" style="margin-top:16px">
      <div class="card card-burn">
        <div class="flex" style="margin-bottom:12px">
          <h3 style="margin:0">Daily token burn</h3>
          <span class="spacer"></span>
          <span class="muted" style="font-size:11px;font-family:var(--mono)">
            <span style="color:var(--yellow)">— ${Math.round(getThresholds().warn / 1000)}K</span>
            &nbsp;&nbsp;<span style="color:var(--red)">— ${Math.round(getThresholds().danger / 1000)}K</span>
          </span>
        </div>
        <div id="ch-daily-burn" style="height:280px"></div>
      </div>
      <div class="card card-wins-atmos">
        <h3 style="display:flex;align-items:center;margin:0">
          <span>Quick wins</span>
          <span class="spacer"></span>
          <a href="#/ai-recos" style="font-family:var(--sans);font-weight:500;font-size:12px">all →</a>
        </h3>
        <p class="muted" style="margin:6px 0 10px;font-size:12px">Top recommendations by estimated savings.</p>
        <div id="quickwins-list">${quickWinsHtml}</div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card card-projects-atmos">
        <h3>Tokens by project</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px">Top 8 projects by billable tokens.</p>
        <div id="ch-projects" style="height:320px"></div>
      </div>
      <div class="card card-model-atmos">
        <h3>Model distribution</h3>
        <p class="muted" style="margin:-4px 0 4px;font-size:12px">Share of billable tokens per Claude model.</p>
        <div id="ch-model" style="height:300px"></div>
      </div>
    </div>

    <div class="card card-sessions-atmos" style="margin-top:16px">
      <h3 style="display:flex;align-items:center;margin:0 0 14px">
        <span>Recent sessions</span>
        <span class="spacer"></span>
        <a href="#/sessions" style="font-family:var(--sans);font-weight:500;font-size:12px">all →</a>
      </h3>
      <table>
        <thead>
          <tr>
            <th>Started</th>
            <th>Project</th>
            <th class="num">Turns</th>
            <th class="num">Tokens</th>
            <th>Health</th>
          </tr>
        </thead>
        <tbody>
          ${sessions.length === 0
            ? '<tr><td colspan="5" class="muted">No sessions in this range.</td></tr>'
            : sessions.map(s => {
                const score = healthById.get(s.session_id);
                return `
                <tr>
                  <td class="mono">${fmt.ts(s.started)}</td>
                  <td><a href="#/sessions/${encodeURIComponent(s.session_id)}">${fmt.htmlSafe(s.project_name || s.project_slug)}</a></td>
                  <td class="num">${fmt.int(s.turns)}</td>
                  <td class="num">${fmt.compact(s.tokens)}</td>
                  <td>${score == null ? '<span class="muted">—</span>' : healthBadge(score)}</td>
                </tr>`;
              }).join('')}
        </tbody>
      </table>
    </div>
  `;

  // Range buttons
  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeRange(btn.dataset.range));
  });

  // Counter animations
  animateCounter(document.getElementById('m-tokens'),   billable,           { format: v => compact(v) });
  animateCounter(document.getElementById('m-sessions'), totals.sessions || 0);
  animateCounter(document.getElementById('m-cost'),     Number(totals.cost_usd || 0),
    { format: v => '$' + v.toFixed(2) });

  // Wire expand/collapse on Quick Wins recos.
  const winsList = document.getElementById('quickwins-list');
  if (winsList) attachExpandHandlers(winsList);

  // Daily token burn — area chart with threshold lines
  const burnPerDay = daily.map(d =>
    (d.input_tokens || 0) +
    (d.output_tokens || 0) +
    (d.cache_create_tokens || 0)
  );
  const { warn: tWarn, danger: tDanger } = getThresholds();
  areaChartWithThresholds(document.getElementById('ch-daily-burn'), {
    x: daily.map(d => d.day),
    values: burnPerDay,
    color: '#19F58C',
    thresholds: [
      { value: tWarn,   color: '#FFD600', label: `${Math.round(tWarn / 1000)}K warning` },
      { value: tDanger, color: '#FF423D', label: `${Math.round(tDanger / 1000)}K danger` },
    ],
  });

  // Tokens by project (horizontal bar)
  const topProjects = projects.slice(0, 8).map(p => {
    const name = p.project_name || p.project_slug || '—';
    const label = name.length > 28 ? name.slice(0, 27) + '…' : name;
    const tokens = (p.billable_tokens != null)
      ? p.billable_tokens
      : (p.input_tokens || 0) + (p.output_tokens || 0);
    return { label, tokens };
  });
  horizontalBarChart(document.getElementById('ch-projects'), {
    categories: topProjects.map(p => p.label),
    values: topProjects.map(p => p.tokens),
    color: '#19F58C',
  });

  // Model distribution donut
  donutChart(document.getElementById('ch-model'),
    byModel.map(m => ({
      name: fmt.modelShort(m.model) || 'unknown',
      value: (m.input_tokens || 0) + (m.output_tokens || 0)
           + (m.cache_create_5m_tokens || 0) + (m.cache_create_1h_tokens || 0),
    })).filter(d => d.value > 0),
  );
}

function planSub() {
  if (!state.pricing || state.plan === 'api') return '';
  const p = state.pricing.plans[state.plan];
  if (!p || !p.monthly) return '';
  return `<div class="sub">on ${fmt.htmlSafe(p.label)} · $${p.monthly}/mo</div>`;
}
