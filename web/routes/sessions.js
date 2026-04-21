// Sessions tab — list with health badges + per-session compound-token chart,
// correction markers, and "Autopsy with AI" button.
import { api, fmt, getThresholds } from '/web/app.js';
import { cumulativeAreaChart } from '/web/charts.js';

const CORRECTION_RE = /\b(try again|that'?s wrong|not what i meant|that isn'?t right|not quite|redo that)\b/i;

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

function renderAutopsy(result) {
  if (!result) return '';
  if (result.configured === false) {
    return `
      <div class="ai-panel">
        <h3>Autopsy unavailable</h3>
        <div class="summary">${fmt.htmlSafe(result.error || 'Configure API key in Settings')}</div>
      </div>`;
  }
  const narrative = fmt.htmlSafe(result.narrative || '');
  const off = Array.isArray(result.off_track_turns) ? result.off_track_turns : [];
  const waste = Array.isArray(result.wasteful_turns) ? result.wasteful_turns : [];
  const handoff = result.optimal_handoff_turn;
  const saved = result.estimated_savings_tokens || 0;
  const when = result.generated_at ? ` · ${fmt.ts(result.generated_at)}` : '';
  const cached = result.cached ? ' · cached' : '';
  const model = result.model_used ? ` · ${fmt.htmlSafe(result.model_used)}` : '';
  return `
    <div class="ai-panel">
      <h3>Session autopsy</h3>
      ${narrative ? `<div class="summary">${narrative}</div>` : ''}
      <ul>
        ${off.length   ? `<li><b>Off-track turns:</b> ${off.join(', ')}</li>`   : ''}
        ${waste.length ? `<li><b>Wasteful turns:</b> ${waste.join(', ')}</li>` : ''}
        ${handoff != null ? `<li><b>Optimal handoff:</b> turn ${handoff}</li>` : ''}
        ${saved > 0 ? `<li><b>Estimated savings:</b> ${compact(saved)} tokens</li>` : ''}
      </ul>
      <div class="meta">generated${when}${model}${cached}</div>
    </div>`;
}

export default async function (root) {
  const id = decodeURIComponent(location.hash.split('/')[2] || '');
  if (!id) return renderList(root);
  return renderSession(root, id);
}

async function renderList(root) {
  const [list, health] = await Promise.all([
    api('/api/sessions?limit=100'),
    api('/api/health').catch(() => []),
  ]);
  const healthById = new Map((health || []).map(h => [h.session_id, h.health_score]));

  root.innerHTML = `
    <div class="flex" style="margin-bottom:20px">
      <h2 style="margin:0;font-family:var(--display);font-weight:500;font-size:24px">Sessions</h2>
      <span class="muted" style="font-size:12px">${list.length} shown · click a row to open the session autopsy</span>
    </div>
    <div class="card card-sessions-atmos">
      <table>
        <thead>
          <tr>
            <th>Started</th>
            <th>Project</th>
            <th class="num">Turns</th>
            <th class="num">Tokens</th>
            <th>Health</th>
            <th>Session</th>
          </tr>
        </thead>
        <tbody>
          ${list.map(s => `
            <tr>
              <td class="mono">${fmt.ts(s.started)}</td>
              <td title="${fmt.htmlSafe(s.project_slug)}">${fmt.htmlSafe(s.project_name || s.project_slug)}</td>
              <td class="num">${fmt.int(s.turns)}</td>
              <td class="num">${compact(s.tokens)}</td>
              <td>${healthBadge(healthById.get(s.session_id))}</td>
              <td><a href="#/sessions/${encodeURIComponent(s.session_id)}" class="mono">${fmt.htmlSafe(s.session_id.slice(0,8))}…</a></td>
            </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

async function renderSession(root, id) {
  const [turns, breakdown, cachedAutopsy] = await Promise.all([
    api('/api/sessions/' + encodeURIComponent(id)),
    api('/api/health/' + encodeURIComponent(id)).catch(() => null),
    api('/api/analyze/' + encodeURIComponent(id)).catch(() => null),
  ]);

  let totalIn = 0, totalOut = 0, totalCacheRd = 0;
  const modelCounts = {};
  for (const t of turns) {
    if (t.type !== 'assistant') continue;
    totalIn += t.input_tokens || 0;
    totalOut += t.output_tokens || 0;
    totalCacheRd += t.cache_read_tokens || 0;
    const m = t.model || 'unknown';
    modelCounts[m] = (modelCounts[m] || 0) + 1;
  }

  const slug = (turns[0] && turns[0].project_slug) || '';
  const cwd = (turns.find(t => t.cwd) || {}).cwd || '';
  const base = cwd ? cwd.replace(/\\/g, '/').replace(/\/+$/, '').split('/').pop() : '';
  const project = base || slug;
  const started = (turns[0] && turns[0].timestamp) || '';
  const ended = (turns[turns.length - 1] && turns[turns.length - 1].timestamp) || '';

  // Build compound-token accumulation series (assistant turns).
  const cumX = [];
  const cumY = [];
  const correctionMarkers = [];
  let runningTokens = 0;
  let assistantTurn = 0;
  for (const t of turns) {
    // Detect a correction from the user message preceding this assistant turn.
    if (t.type === 'user' && t.prompt_text && CORRECTION_RE.test(t.prompt_text)) {
      correctionMarkers.push({ turn: assistantTurn + 1, value: runningTokens, label: 'correction' });
    }
    if (t.type !== 'assistant') continue;
    assistantTurn += 1;
    runningTokens +=
      (t.input_tokens || 0) +
      (t.output_tokens || 0) +
      (t.cache_create_5m_tokens || 0) +
      (t.cache_create_1h_tokens || 0);
    cumX.push(String(assistantTurn));
    cumY.push(runningTokens);
  }

  const score = breakdown && breakdown.score != null ? breakdown.score : null;
  const corrections = breakdown && breakdown.stats ? (breakdown.stats.correction_cycles || 0) : 0;

  const hasCache = cachedAutopsy && !cachedAutopsy.error;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:20px">
      <h2 style="margin:0;font-family:var(--display);font-weight:500;font-size:24px">
        Session <span class="mono" style="font-weight:400;color:var(--text-2);font-size:18px">${fmt.htmlSafe(id.slice(0,8))}…</span>
      </h2>
      <div class="spacer"></div>
      <a href="#/sessions" class="muted" style="font-size:13px">← all sessions</a>
    </div>

    <div class="row cols-4">
      <div class="card card-tint-green metric">
        <div class="label">Health</div>
        <div class="value" style="font-size:38px">${score == null ? '—' : score}</div>
        <div class="sub">of 100</div>
      </div>
      <div class="card card-tint-blue metric">
        <div class="label">Turns</div>
        <div class="value" style="font-size:38px">${fmt.int(assistantTurn)}</div>
        <div class="sub">${turns.length} records</div>
      </div>
      <div class="card card-tint-cyan metric">
        <div class="label">Billable tokens</div>
        <div class="value" style="font-size:38px">${compact(runningTokens)}</div>
        <div class="sub">${fmt.int(totalIn)} in · ${fmt.int(totalOut)} out</div>
      </div>
      <div class="card card-tint-purple metric">
        <div class="label">Correction cycles</div>
        <div class="value" style="font-size:38px">${fmt.int(corrections)}</div>
        <div class="sub">detected in user prompts</div>
      </div>
    </div>

    <div class="card card-burn" style="margin-top:16px">
      <div class="flex" style="margin-bottom:12px">
        <h3 style="margin:0">Compound token accumulation</h3>
        <span class="spacer"></span>
        <span class="muted" style="font-size:11px;font-family:var(--mono)">
          <span style="color:var(--yellow)">— ${Math.round(getThresholds().warn / 1000)}K</span>
          &nbsp;&nbsp;<span style="color:var(--red)">— ${Math.round(getThresholds().danger / 1000)}K</span>
          ${correctionMarkers.length ? `&nbsp;&nbsp;<span style="color:var(--red)">● correction</span>` : ''}
        </span>
      </div>
      <div id="ch-cum" style="height:280px"></div>
      <div class="muted" style="font-family:var(--mono);font-size:12px;margin-top:14px;display:flex;gap:18px;flex-wrap:wrap">
        <span>${fmt.htmlSafe(project)}</span>
        <span>${fmt.ts(started)} → ${fmt.ts(ended)}</span>
      </div>
    </div>

    <div class="flex" style="margin-top:16px">
      <h3 style="margin:0;font-family:var(--display);font-weight:500;font-size:18px">Turn-by-turn</h3>
      <div class="spacer"></div>
      <button class="ai" id="btn-autopsy">${hasCache ? 'Re-run autopsy' : 'Autopsy with AI ✨'}</button>
    </div>

    <div id="ai-out">${hasCache ? renderAutopsy(cachedAutopsy) : ''}</div>

    <div class="card" style="margin-top:16px">
      <table>
        <thead><tr><th>time</th><th>type</th><th>model</th><th class="blur-sensitive">prompt / tools</th><th class="num">in</th><th class="num">out</th><th class="num">cache rd</th></tr></thead>
        <tbody>
          ${turns.map(t => {
            const tools = t.tool_calls_json ? JSON.parse(t.tool_calls_json) : [];
            const summary = t.prompt_text ? fmt.short(t.prompt_text, 110)
              : tools.length ? tools.map(x => x.name).join(' · ')
              : '';
            const correction = t.type === 'user' && t.prompt_text && CORRECTION_RE.test(t.prompt_text);
            return `<tr${correction ? ' style="background:rgba(255,66,61,0.06)"' : ''}>
              <td class="mono">${(t.timestamp || '').slice(11,19)}</td>
              <td>${t.type}${t.is_sidechain ? ' <span class="badge">side</span>' : ''}${correction ? ' <span class="badge" style="color:var(--red);border-color:rgba(255,66,61,0.35);background:rgba(255,66,61,0.1)">correction</span>' : ''}</td>
              <td>${t.model ? `<span class="badge ${fmt.modelClass(t.model)}">${fmt.htmlSafe(fmt.modelShort(t.model))}</span>` : ''}</td>
              <td class="blur-sensitive">${fmt.htmlSafe(summary)}</td>
              <td class="num">${fmt.int(t.input_tokens)}</td>
              <td class="num">${fmt.int(t.output_tokens)}</td>
              <td class="num">${fmt.int(t.cache_read_tokens)}</td>
            </tr>`;
          }).join('')}
        </tbody>
      </table>
    </div>
  `;

  if (cumX.length > 0) {
    const { warn: tWarn, danger: tDanger } = getThresholds();
    cumulativeAreaChart(document.getElementById('ch-cum'), {
      x: cumX,
      values: cumY,
      markers: correctionMarkers,
      thresholds: [
        { value: tWarn,   color: '#FFD600', label: `${Math.round(tWarn / 1000)}K` },
        { value: tDanger, color: '#FF423D', label: `${Math.round(tDanger / 1000)}K` },
      ],
    });
  } else {
    document.getElementById('ch-cum').innerHTML =
      '<p class="muted" style="margin:12px 0">No assistant turns in this session.</p>';
  }

  const btn = root.querySelector('#btn-autopsy');
  const out = root.querySelector('#ai-out');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const prev = btn.textContent;
    btn.textContent = 'Analyzing…';
    try {
      const r = await fetch('/api/analyze/' + encodeURIComponent(id), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ force: hasCache }),
      });
      const data = await r.json();
      out.innerHTML = renderAutopsy(data);
      if (data && data.configured !== false) {
        btn.textContent = 'Re-run autopsy';
      } else {
        btn.textContent = prev;
      }
    } catch (e) {
      out.innerHTML = `
        <div class="ai-panel">
          <h3>Autopsy failed</h3>
          <div class="summary">${fmt.htmlSafe(String(e.message || e))}</div>
        </div>`;
      btn.textContent = prev;
    } finally {
      btn.disabled = false;
    }
  });
}
