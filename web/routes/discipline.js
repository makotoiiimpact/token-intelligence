// Discipline tab — health ring, score trend, habit tracker, metric cards.
import { api, fmt } from '/web/app.js';
import { scoreTrendChart } from '/web/charts.js';

function bandColor(score) {
  if (score == null) return 'rgba(255,255,255,0.25)';
  if (score >= 80) return '#19F58C';
  if (score >= 50) return '#FFD600';
  return '#FF423D';
}

function healthBand(score) {
  if (score == null) return { label: '—',       hex: 'rgba(255,255,255,0.35)' };
  if (score >= 80)   return { label: 'Healthy', hex: '#19F58C' };
  if (score >= 50)   return { label: 'Watch',   hex: '#FFD600' };
  return               { label: 'Critical', hex: '#FF423D' };
}

function bigRing(score) {
  const clamped = Math.max(0, Math.min(100, Math.round(score ?? 0)));
  const color = bandColor(score);
  const C = 2 * Math.PI * 95; // r=95 in a 220x220 svg
  const offset = C * (1 - clamped / 100);
  const band = healthBand(score).label;
  return `
    <div class="health-ring-lg" aria-label="Average session health ${clamped}">
      <svg viewBox="0 0 220 220">
        <circle class="track" cx="110" cy="110" r="95" stroke-width="14" fill="none"/>
        <circle class="fill"  cx="110" cy="110" r="95" stroke-width="14" fill="none"
                stroke="${color}"
                stroke-dasharray="${C.toFixed(2)}"
                stroke-dashoffset="${offset.toFixed(2)}"/>
      </svg>
      <div class="center">
        <div class="num">${score == null ? '—' : clamped}</div>
        <div class="lbl">Session Health</div>
        <div class="lbl" style="color:${color};letter-spacing:0.16em;margin-top:4px">${band}</div>
      </div>
    </div>`;
}

function metricCard(label, value, sub = '', tone = '') {
  const toneClass = tone ? ` card-tint-${tone}` : '';
  return `
    <div class="card${toneClass} metric">
      <div class="label">${label}</div>
      <div class="value" style="font-size:34px">${value}</div>
      ${sub ? `<div class="sub">${sub}</div>` : ''}
    </div>`;
}

function habitCell(pass, missing) {
  if (missing) return `<div class="habit-cell" title="no data">·</div>`;
  return pass
    ? `<div class="habit-cell yes" title="yes">✓</div>`
    : `<div class="habit-cell no" title="no">✕</div>`;
}

function buildHabitRows(sessions, healthById) {
  const rows = [
    { label: 'Under 120K tokens',      fn: s => (s.tokens || 0) < 120000 },
    { label: 'Under 250K tokens',      fn: s => (s.tokens || 0) < 250000 },
    { label: 'No correction cycles',   fn: s => {
        const h = healthById.get(s.session_id);
        return h != null && (h.correction_cycles || 0) === 0;
      }, missing: s => !healthById.has(s.session_id) },
    { label: 'Cache hit rate ≥ 60%',   fn: s => {
        const h = healthById.get(s.session_id);
        return h != null && (h.cache_hit_rate || 0) >= 0.6;
      }, missing: s => !healthById.has(s.session_id) },
  ];
  return rows.map(row => {
    const cells = sessions.map(s => {
      const missing = row.missing ? row.missing(s) : false;
      return habitCell(missing ? false : row.fn(s), missing);
    });
    return {
      label: row.label,
      cells: cells.join(''),
    };
  });
}

function recommendationsFromAgg(agg) {
  const out = [];
  const pct = Math.round((agg.pct_over_120k || 0) * 100);
  if (pct > 40) out.push(`${pct}% of sessions cross 120K tokens. Budget handoffs earlier — past 120K, retrieval quality drops.`);
  else if (pct > 20) out.push(`${pct}% of sessions cross 120K. Consider wrapping before that threshold.`);
  else out.push(`Only ${pct}% of sessions cross 120K — token discipline is holding.`);

  if ((agg.avg_corrections || 0) > 1) out.push(`Averaging ${agg.avg_corrections} correction cycles per session. Restate the goal up front; ambiguity at turn 1 often snowballs.`);
  else if ((agg.avg_corrections || 0) > 0.3) out.push(`Correction cycles at ${agg.avg_corrections}/session — mostly clean, but a few sessions are getting stuck.`);

  const t = agg.threshold_counts || {};
  if ((t['250k'] || 0) > 0) out.push(`${t['250k']} session${t['250k'] === 1 ? '' : 's'} hit the 250K danger zone. Those should have been handed off.`);

  if ((agg.avg_score || 0) < 50 && agg.total_sessions > 0) out.push(`Average health ${agg.avg_score}/100 — systemic issue, not one-off. Review the longest sessions and where they went off-track.`);

  return out;
}

export default async function (root) {
  const [agg, health, sessions] = await Promise.all([
    api('/api/discipline').catch(() => ({})),
    api('/api/health').catch(() => []),
    api('/api/sessions?limit=100').catch(() => []),
  ]);

  // Merge health into session list by session_id.
  const healthById = new Map((health || []).map(h => [h.session_id, h]));

  // Last 30 sessions by started date ascending (oldest → newest for the chart).
  const withDate = (sessions || [])
    .filter(s => s.started)
    .slice() // copy
    .sort((a, b) => new Date(a.started) - new Date(b.started));
  const last30 = withDate.slice(-30);
  const trendX = last30.map(s => (s.started || '').slice(0, 10));
  const trendY = last30.map(s => {
    const h = healthById.get(s.session_id);
    return h ? Math.max(0, Math.min(100, h.health_score)) : null;
  });

  // Habit tracker: show the most-recent 30 sessions (newest first on far right).
  const habitSessions = withDate.slice(-30);
  const habitRows = buildHabitRows(habitSessions, healthById);

  const recs = recommendationsFromAgg(agg || {});

  const pctOver = Math.round((agg.pct_over_120k || 0) * 100);
  const avgCorr = (agg.avg_corrections != null) ? agg.avg_corrections.toFixed(2) : '—';
  const totalSessions = agg.total_sessions || 0;

  // Avg session length: (sum turns / sessions). Use health data.
  const healthList = Array.isArray(health) ? health : [];
  const totalTurns = healthList.reduce((s, h) => s + (h.turn_count || 0), 0);
  const avgTurns = healthList.length ? Math.round(totalTurns / healthList.length) : 0;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:20px">
      <h2 style="margin:0;font-family:var(--display);font-weight:500;font-size:24px">Session Discipline</h2>
      <span class="muted" style="font-size:12px">${totalSessions} scored session${totalSessions === 1 ? '' : 's'}</span>
    </div>

    <div class="row cols-2-wide">
      <div class="card card-tint-cyan" style="display:flex;align-items:center;justify-content:center;min-height:300px">
        ${bigRing(agg.avg_score)}
      </div>

      <div class="card card-sessions-atmos">
        <h3>Score trend</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px">Last ${last30.length} session${last30.length === 1 ? '' : 's'} by start date.</p>
        <div id="ch-trend" style="height:260px"></div>
      </div>
    </div>

    <div class="row cols-3" style="margin-top:16px">
      ${metricCard('Avg turns / session', fmt.int(avgTurns), `across ${healthList.length} session${healthList.length === 1 ? '' : 's'}`, 'blue')}
      ${metricCard('% over 120K',        pctOver + '%',    `${agg.threshold_counts ? agg.threshold_counts['120k'] || 0 : 0} of ${totalSessions}`, 'green')}
      ${metricCard('Avg correction cycles', avgCorr,       'per session', 'purple')}
    </div>

    <div class="card card-projects-atmos" style="margin-top:16px">
      <h3>Habit tracker</h3>
      <p class="muted" style="margin:-4px 0 10px;font-size:12px">Per-session habits across the last ${habitSessions.length} session${habitSessions.length === 1 ? '' : 's'}. Oldest → newest, left to right.</p>
      <div class="habit-grid">
        ${habitRows.map(r => `
          <div class="row-label">${fmt.htmlSafe(r.label)}</div>
          <div class="row-cells">${r.cells}</div>
        `).join('')}
      </div>
    </div>

    <div class="card card-wins-atmos" style="margin-top:16px">
      <h3>Recommendations</h3>
      ${recs.length === 0
        ? '<p class="muted" style="margin:0;font-size:13px">No data yet. Run a scan to populate.</p>'
        : `<ul style="margin:6px 0 0;padding:0 0 0 18px;line-height:1.7">
            ${recs.map(r => `<li style="margin:6px 0">${fmt.htmlSafe(r)}</li>`).join('')}
           </ul>`}
    </div>
  `;

  if (trendX.length > 0 && trendY.some(v => v != null)) {
    scoreTrendChart(document.getElementById('ch-trend'), { x: trendX, values: trendY.map(v => v ?? 0) });
  } else {
    document.getElementById('ch-trend').innerHTML =
      '<p class="muted" style="margin:12px 0;font-size:13px">Not enough data to chart. Run a scan.</p>';
  }
}
