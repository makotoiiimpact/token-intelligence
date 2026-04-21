// Settings tab — plan, pricing table, AI key status, token threshold slider,
// CSV export, privacy note.
import { api, state, $, getThresholds, setThresholds } from '/web/app.js';

function csvEscape(v) {
  if (v == null) return '';
  const s = String(v);
  if (/[",\n\r]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function downloadCsv(filename, headers, rows) {
  const lines = [headers.map(csvEscape).join(',')];
  for (const r of rows) lines.push(r.map(csvEscape).join(','));
  const blob = new Blob([lines.join('\n') + '\n'], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function exportSessionsCsv() {
  const [sessions, health] = await Promise.all([
    api('/api/sessions?limit=1000'),
    api('/api/health').catch(() => []),
  ]);
  const healthById = new Map((health || []).map(h => [h.session_id, h]));
  const headers = ['session_id', 'project_slug', 'project_name', 'started', 'ended', 'turns', 'tokens', 'health_score', 'correction_cycles', 'cache_hit_rate'];
  const rows = sessions.map(s => {
    const h = healthById.get(s.session_id) || {};
    return [
      s.session_id,
      s.project_slug,
      s.project_name || '',
      s.started,
      s.ended,
      s.turns,
      s.tokens,
      h.health_score != null ? h.health_score : '',
      h.correction_cycles != null ? h.correction_cycles : '',
      h.cache_hit_rate != null ? Number(h.cache_hit_rate).toFixed(4) : '',
    ];
  });
  const stamp = new Date().toISOString().slice(0, 10);
  downloadCsv(`token-intelligence-sessions-${stamp}.csv`, headers, rows);
}

export default async function (root) {
  const [cur, aiStatus] = await Promise.all([
    api('/api/plan'),
    api('/api/ai/status').catch(() => ({ configured: false })),
  ]);
  const plans = Object.entries(cur.pricing.plans);
  const { warn, danger } = getThresholds();

  root.innerHTML = `
    <div class="flex" style="margin-bottom:20px">
      <h2 style="margin:0;font-family:var(--display);font-weight:500;font-size:24px">Settings</h2>
    </div>

    <div class="card">
      <h3 style="margin-top:0">Plan</h3>
      <p class="muted" style="margin:0 0 12px">Sets how cost is displayed. API mode shows pay-per-token rates. Subscription modes show what you actually pay each month.</p>
      <div class="flex">
        <select id="plan">
          ${plans.map(([k,v]) => `<option value="${k}" ${k===cur.plan?'selected':''}>${v.label}${v.monthly?` — $${v.monthly}/mo`:''}</option>`).join('')}
        </select>
        <button class="primary" id="save">Save</button>
        <span id="msg" class="muted"></span>
      </div>
    </div>

    <div class="card card-wins-atmos" style="margin-top:16px">
      <h3 style="margin-top:0">AI analysis</h3>
      <p class="muted" style="margin:0 0 12px">Optional. Powers the "Analyze with AI" button on AI Recos and the per-session "Autopsy with AI" button. Sends aggregate metrics only — never prompt text.</p>
      <div class="flex" style="margin-bottom:12px">
        <span class="api-key-status ${aiStatus.configured ? 'configured' : 'not-configured'}">
          ${aiStatus.configured ? 'ANTHROPIC_API_KEY configured' : 'ANTHROPIC_API_KEY not set'}
        </span>
      </div>
      <p class="muted" style="margin:0;font-size:13px;line-height:1.55">
        To configure: set <code>ANTHROPIC_API_KEY</code> in your environment, then restart the dashboard.
        <br>macOS/zsh: <code>export ANTHROPIC_API_KEY=sk-ant-…</code> in <code>~/.zshrc</code> or
        <code>~/.config/token-intelligence/env</code>.
      </p>
    </div>

    <div class="card card-projects-atmos" style="margin-top:16px">
      <h3 style="margin-top:0">Token thresholds</h3>
      <p class="muted" style="margin:0 0 14px">Where the Overview + Sessions charts draw the warning / danger lines. Stored locally in your browser.</p>

      <div style="display:grid;grid-template-columns:140px 1fr 80px;gap:14px;align-items:center;margin-bottom:14px">
        <div>
          <div class="muted" style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase">Warning</div>
          <div class="mono" style="color:var(--yellow);font-size:15px;margin-top:2px"><span id="warn-val">${Math.round(warn / 1000)}K</span></div>
        </div>
        <input type="range" id="warn-slider" min="30000" max="240000" step="5000" value="${warn}">
        <div class="mono" style="color:var(--text-3);font-size:11px;text-align:right">30K–240K</div>

        <div>
          <div class="muted" style="font-size:11px;letter-spacing:0.08em;text-transform:uppercase">Danger</div>
          <div class="mono" style="color:var(--red);font-size:15px;margin-top:2px"><span id="danger-val">${Math.round(danger / 1000)}K</span></div>
        </div>
        <input type="range" id="danger-slider" min="60000" max="500000" step="10000" value="${danger}">
        <div class="mono" style="color:var(--text-3);font-size:11px;text-align:right">60K–500K</div>
      </div>

      <div class="flex">
        <button id="th-save" class="primary">Save thresholds</button>
        <button id="th-reset" class="ghost">Reset to 120K / 250K</button>
        <span id="th-msg" class="muted"></span>
      </div>
    </div>

    <div class="card card-sessions-atmos" style="margin-top:16px">
      <h3 style="margin-top:0">Export</h3>
      <p class="muted" style="margin:0 0 12px">Download a CSV of every session, with project, dates, turn count, tokens, and health score.</p>
      <div class="flex">
        <button id="export-csv">Export sessions CSV</button>
        <span id="export-msg" class="muted"></span>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3 style="margin-top:0">Pricing table</h3>
      <p class="muted" style="margin:0 0 12px">Edit <code>pricing.json</code> in the project root to change rates. Reload the page after editing.</p>
      <table>
        <thead><tr><th>model</th><th class="num">input</th><th class="num">output</th><th class="num">cache read</th><th class="num">cache 5m</th><th class="num">cache 1h</th></tr></thead>
        <tbody>
          ${Object.entries(cur.pricing.models).map(([k,v]) => `
            <tr><td><span class="badge ${v.tier}">${k}</span></td>
              <td class="num">$${v.input.toFixed(2)}</td>
              <td class="num">$${v.output.toFixed(2)}</td>
              <td class="num">$${v.cache_read.toFixed(2)}</td>
              <td class="num">$${v.cache_create_5m.toFixed(2)}</td>
              <td class="num">$${v.cache_create_1h.toFixed(2)}</td>
            </tr>`).join('')}
        </tbody>
      </table>
      <p class="muted" style="margin-top:8px;font-size:11px">Rates per 1M tokens, USD.</p>
    </div>

    <div class="card" style="margin-top:16px">
      <h3 style="margin-top:0">Privacy</h3>
      <p class="muted">Press <code>Cmd/Ctrl + B</code> anywhere to blur prompt text and other sensitive content for screenshots.</p>
    </div>
  `;

  $('#save').addEventListener('click', async () => {
    const plan = $('#plan').value;
    await fetch('/api/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan }) });
    state.plan = plan;
    document.getElementById('plan-pill').textContent = plan;
    $('#msg').textContent = 'Saved.';
    $('#msg').style.color = 'var(--green)';
  });

  const warnSlider = $('#warn-slider');
  const dangerSlider = $('#danger-slider');
  const warnVal = $('#warn-val');
  const dangerVal = $('#danger-val');
  warnSlider.addEventListener('input', () => {
    warnVal.textContent = Math.round(Number(warnSlider.value) / 1000) + 'K';
    // Keep danger ≥ warn + 10K.
    if (Number(dangerSlider.value) < Number(warnSlider.value) + 10000) {
      dangerSlider.value = Number(warnSlider.value) + 10000;
      dangerVal.textContent = Math.round(Number(dangerSlider.value) / 1000) + 'K';
    }
  });
  dangerSlider.addEventListener('input', () => {
    dangerVal.textContent = Math.round(Number(dangerSlider.value) / 1000) + 'K';
    if (Number(dangerSlider.value) < Number(warnSlider.value) + 10000) {
      dangerSlider.value = Number(warnSlider.value) + 10000;
      dangerVal.textContent = Math.round(Number(dangerSlider.value) / 1000) + 'K';
    }
  });
  $('#th-save').addEventListener('click', () => {
    setThresholds({ warn: Number(warnSlider.value), danger: Number(dangerSlider.value) });
    $('#th-msg').textContent = 'Saved.';
    $('#th-msg').style.color = 'var(--green)';
  });
  $('#th-reset').addEventListener('click', () => {
    setThresholds({ warn: 120000, danger: 250000 });
    warnSlider.value = 120000;
    dangerSlider.value = 250000;
    warnVal.textContent = '120K';
    dangerVal.textContent = '250K';
    $('#th-msg').textContent = 'Reset.';
    $('#th-msg').style.color = 'var(--text-2)';
  });

  $('#export-csv').addEventListener('click', async () => {
    const msg = $('#export-msg');
    msg.textContent = 'Building…';
    msg.style.color = 'var(--text-2)';
    try {
      await exportSessionsCsv();
      msg.textContent = 'Downloaded.';
      msg.style.color = 'var(--green)';
    } catch (e) {
      msg.textContent = 'Failed: ' + (e.message || String(e));
      msg.style.color = 'var(--red)';
    }
  });
}
