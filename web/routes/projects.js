// Projects tab — per-project cards with avg health + token efficiency bar.
import { api, fmt } from '/web/app.js';
import { horizontalBarChart } from '/web/charts.js';

function healthBadge(score) {
  if (score == null) return '<span class="muted">—</span>';
  const band = score >= 80 ? 'good' : score >= 50 ? 'warn' : 'bad';
  return `<span class="badge-health ${band}">${Math.round(score)}</span>`;
}

function compact(n) {
  const x = Number(n || 0);
  if (Math.abs(x) >= 1e9) return (x / 1e9).toFixed(1).replace(/\.0$/, '') + 'B';
  if (Math.abs(x) >= 1e6) return (x / 1e6).toFixed(1).replace(/\.0$/, '') + 'M';
  if (Math.abs(x) >= 1e3) return (x / 1e3).toFixed(1).replace(/\.0$/, '') + 'K';
  return Math.round(x).toLocaleString();
}

export default async function (root) {
  const [rows, sessions, health] = await Promise.all([
    api('/api/projects'),
    api('/api/sessions?limit=1000').catch(() => []),
    api('/api/health').catch(() => []),
  ]);

  // project_slug → [health_score, ...]
  const healthById = new Map((health || []).map(h => [h.session_id, h.health_score]));
  const scoresByProject = new Map();
  for (const s of sessions || []) {
    const slug = s.project_slug;
    if (!slug) continue;
    const score = healthById.get(s.session_id);
    if (score == null) continue;
    if (!scoresByProject.has(slug)) scoresByProject.set(slug, []);
    scoresByProject.get(slug).push(score);
  }

  // Decorate rows with avg health + tokens-per-session.
  const decorated = rows.map(r => {
    const scores = scoresByProject.get(r.project_slug) || [];
    const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
    const tps = r.sessions ? (r.billable_tokens || 0) / r.sessions : 0;
    return { ...r, avg_health: avg, tokens_per_session: tps };
  });

  // Efficiency chart: top 10 projects by tokens-per-session.
  const top = decorated.slice().sort((a, b) => b.tokens_per_session - a.tokens_per_session).slice(0, 10);

  root.innerHTML = `
    <div class="flex" style="margin-bottom:20px">
      <h2 style="margin:0;font-family:var(--display);font-weight:500;font-size:24px">Projects</h2>
      <span class="muted" style="font-size:12px">${decorated.length} project${decorated.length === 1 ? '' : 's'}</span>
    </div>

    <div class="card card-projects-atmos">
      <h3>Token efficiency</h3>
      <p class="muted" style="margin:-4px 0 10px;font-size:12px">Billable tokens per session, top ${top.length}. Lower = more efficient.</p>
      <div id="ch-efficiency" style="height:${Math.max(200, top.length * 38)}px"></div>
    </div>

    <div class="row cols-3" style="margin-top:16px">
      ${decorated.map(r => `
        <div class="card">
          <div class="flex" style="margin-bottom:10px">
            <h3 style="margin:0;font-size:15px" title="${fmt.htmlSafe(r.project_slug)}">${fmt.htmlSafe(r.project_name || r.project_slug)}</h3>
            <span class="spacer"></span>
            ${healthBadge(r.avg_health)}
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px 16px;margin-top:4px">
            <div>
              <div class="muted" style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase">Sessions</div>
              <div class="mono" style="font-size:16px;margin-top:2px">${fmt.int(r.sessions)}</div>
            </div>
            <div>
              <div class="muted" style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase">Turns</div>
              <div class="mono" style="font-size:16px;margin-top:2px">${fmt.int(r.turns)}</div>
            </div>
            <div>
              <div class="muted" style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase">Billable</div>
              <div class="mono" style="font-size:16px;margin-top:2px">${compact(r.billable_tokens)}</div>
            </div>
            <div>
              <div class="muted" style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase">Tokens / session</div>
              <div class="mono" style="font-size:16px;margin-top:2px">${compact(r.tokens_per_session)}</div>
            </div>
          </div>
        </div>`).join('')}
    </div>
  `;

  if (top.length > 0) {
    horizontalBarChart(document.getElementById('ch-efficiency'), {
      categories: top.map(p => {
        const name = p.project_name || p.project_slug;
        return name.length > 28 ? name.slice(0, 27) + '…' : name;
      }),
      values: top.map(p => Math.round(p.tokens_per_session)),
      color: '#0066FF',
    });
  }
}
