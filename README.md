# Token Intelligence

**An IIIMPACT project.** Local dashboard for Claude Code token intelligence — usage analytics, session health scoring, AI-powered tips, and session discipline tracking.

Reads the JSONL transcripts Claude Code writes to `~/.claude/projects/` and turns them into per-prompt cost analytics, tool/file heatmaps, subagent attribution, cache analytics, project comparisons, a smart tips engine, per-session health scoring, and AI-powered analysis of your usage patterns.

**Everything runs locally.** No data leaves your machine — no telemetry, no API calls for your data, no login.

> **Based on an [upstream open-source token-dashboard project](https://github.com/nateherkai/token-dashboard)** (MIT). Token Intelligence is an IIIMPACT fork that layers on session health scoring, AI-powered analysis, session discipline tracking, and a redesigned UI. Full credit and thanks to the upstream project for the original scanner, server, and rule-based tips foundation.

## What's new vs. the original

- **Smart tips engine** — expanded rule coverage (marathon sessions, hot files, bash output bloat, prompt bloat, tool-error thrash) on top of the original cache/repeat/right-size/outlier rules.
- **Session health scoring** — a per-session 0–100 score combining token discipline, cache efficiency, tool-error rate, and session length.
- **AI-powered analysis** — opt-in, local-only hook that passes aggregate metrics to a model for higher-level recommendations.
- **Session discipline integration** — ships with the `session-discipline` skill contract: threshold alerts (60K / 120K / 250K), handoff protocol, sub-agent delegation triggers.
- **Token Intelligence design system** — Red Hat typography, glass cards, severity-coded state (green/yellow/red/cyan/purple). Fonts served locally; zero CDN calls.

## What this is useful for

- Seeing which of your prompts are expensive (surprise: they usually involve large tool results).
- Comparing token usage across projects you've worked on.
- Spotting wasteful patterns — the same file read twenty times in a session, a tool call returning 80k tokens.
- Understanding what a "cache hit" actually saves you.
- If you're on Pro or Max, confirming you're getting your money's worth in API-equivalent dollars.

## Prerequisites

- **Python 3.8 or newer** — already installed on macOS and most Linux. On Windows: `winget install Python.Python.3.12` or download from python.org.
- **Claude Code** — installed and with at least one session run. The dashboard reads those sessions.
- **A web browser.** Any modern one.

No `pip install`. No Node.js. No build step.

## Quickstart

```bash
git clone https://github.com/makotoiiimpact/token-intelligence.git
cd token-intelligence
python3 cli.py dashboard
```

> On Windows, if `python3` isn't on your PATH, substitute `py -3` for `python3` in every command below.

The command:
1. Scans `~/.claude/projects/` (first run can take 20–60 seconds on a heavy user's machine).
2. Starts a local server at http://127.0.0.1:8080.
3. Opens your default browser to that URL.

Leave it running; it re-scans every 30 seconds and pushes updates live. Stop with `Ctrl+C`.

## Where the data comes from

Claude Code writes one JSONL file per session here:

| OS | Path |
|---|---|
| macOS / Linux | `~/.claude/projects/<project-slug>/<session-id>.jsonl` |
| Windows | `C:\Users\<you>\.claude\projects\<project-slug>\<session-id>.jsonl` |

The dashboard never modifies those files — it only reads them and keeps a local SQLite cache at `~/.claude/token-dashboard.db`.

To point at a different location:

```bash
python3 cli.py dashboard --projects-dir /path/to/projects --db /path/to/cache.db
```

### Environment variables

| Var | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | Port the local web server listens on |
| `HOST` | `127.0.0.1` | Bind address. Keep the default. Setting `0.0.0.0` exposes your entire prompt history to anyone on your local network — don't do this on any network you don't fully control. |
| `CLAUDE_PROJECTS_DIR` | `~/.claude/projects` | Where to scan for session JSONL files |
| `TOKEN_DASHBOARD_DB` | `~/.claude/token-dashboard.db` | SQLite cache location |

Pricing lives in [`pricing.json`](pricing.json). Edit it directly if model prices change or to add a new plan.

## CLI reference

```bash
python3 cli.py scan          # populate / refresh the local DB, then exit
python3 cli.py today         # today's totals (terminal)
python3 cli.py stats         # all-time totals (terminal)
python3 cli.py tips          # active suggestions (terminal)
python3 cli.py dashboard     # scan + serve the UI at http://localhost:8080

# dashboard flags
python3 cli.py dashboard --no-open   # don't auto-open the browser
python3 cli.py dashboard --no-scan   # skip the initial scan (use cached DB only)
```

Change the port: `PORT=9000 python3 cli.py dashboard`.

## The tabs

Hash-routed single page. Each tab is backed by its own JSON API under `/api/`:

- **Overview** — all-time input/output/cache tokens, sessions, turns, estimated cost on your chosen plan, daily work and cache-read charts, tokens-by-project, token share by model, top tools by call count, and recent sessions.
- **Prompts** — your most expensive user prompts ranked by tokens. Click any row to see the assistant response, tool calls made, and the size of each tool result.
- **Sessions** — turn-by-turn view of any single session, with per-turn tokens and tool calls. Session health score per row.
- **Projects** — per-project comparison: tokens, session counts, and which files were touched most.
- **Skills** — which skills you invoke most often, and (where we can measure them) their token cost.
- **Tips** — rule-based + AI-powered suggestions for reducing token usage.
- **Session Discipline** — health-score ring, threshold-crossing trend, habit tracker, handoff recommendations.
- **Settings** — switch pricing between API / Pro / Max / Max-20x so cost figures reflect your actual plan.

## Troubleshooting

**"No data" or empty charts.** Run `python3 cli.py scan` once to populate the DB, then reload.

**Port 8080 already in use.** `PORT=9000 python3 cli.py dashboard`.

**Numbers look wrong / stuck.** The DB lives at `~/.claude/token-dashboard.db`. Delete it and re-run `python3 cli.py scan` to rebuild from scratch.

**Running the dashboard twice at the same time.** Don't — both processes will fight over the SQLite DB.

## Accuracy note

Claude Code writes each assistant response 2–3 times to disk while it streams (the same API message gets snapshotted as output grows). The dashboard dedupes these by `message.id` so the final tally matches what the API actually billed.

## Privacy

Nothing leaves your machine. No telemetry. No remote calls for your data. The browser fetches its JSON from `127.0.0.1`, and all JS/CSS/fonts are served from that same local server — ECharts is vendored, Red Hat fonts are bundled in `web/fonts/`, zero CDN calls. AI analysis (when enabled in Settings) is an explicit opt-in and clearly labeled — it's off by default.

## Tech stack

Python 3 (stdlib only) for the CLI, scanner, and HTTP server. SQLite for the local cache. Vanilla JS + ECharts for the UI, no build step. Red Hat Display / Text / Mono for typography, served from `web/fonts/`.

Data flow: `cli.py` → `token_dashboard/scanner.py` → SQLite DB; `token_dashboard/server.py` exposes `/api/*` JSON routes and serves `web/`.

## Further reading

- [`CLAUDE.md`](CLAUDE.md) — conventions and architecture overview
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to develop and test
- [`docs/KNOWN_LIMITATIONS.md`](docs/KNOWN_LIMITATIONS.md) — rough edges
- [`docs/inspiration.md`](docs/inspiration.md) — prior art

## Credits

- **Upstream**: [open-source `token-dashboard`](https://github.com/nateherkai/token-dashboard) — scanner, server, DB schema, original rule-based tips engine, inspiration template.
- **IIIMPACT additions**: health scoring, AI analysis hook, session-discipline integration, redesigned UI, expanded tips.
- **Fonts**: Red Hat Display / Text / Mono (SIL Open Font License 1.1).

## License

[MIT](LICENSE). Upstream © its respective authors; IIIMPACT modifications © Makoto Kern / IIIMPACT.
