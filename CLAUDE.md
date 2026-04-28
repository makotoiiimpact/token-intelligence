# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project overview

**Token Intelligence** — an IIIMPACT project. Local dashboard for Claude Code token intelligence: usage analytics, session health scoring, AI-powered tips, and session discipline tracking. Reads the JSONL transcripts Claude Code writes to `~/.claude/projects/` and turns them into per-prompt cost analytics, tool/file heatmaps, subagent attribution, cache analytics, project comparisons, a smart tips engine, per-session health scoring, and AI-powered analysis of your usage patterns.

Forked from the upstream open-source [token-dashboard](https://github.com/nateherkai/token-dashboard) (MIT). IIIMPACT additions: health scoring, AI analysis hook, session-discipline integration, redesigned UI, expanded tips rules.

## Status

Working codebase. 111 Python unit tests (`python3 -m unittest discover tests`). Seven UI tabs wired up (Overview, Prompts, Sessions, Projects, Skills, Tips, Settings) + a new Session Discipline tab planned. New modules stubbed pending Specs 2–6: `tips_engine.py`, `health_score.py`, `ai_analyzer.py`, `discipline.py`.

## Architecture

- `cli.py` → `token_dashboard/scanner.py` → `~/.claude/token-dashboard.db` (SQLite)
- `token_dashboard/server.py` exposes JSON APIs (`/api/*`) + SSE stream (`/api/stream`) + static frontend (`web/`)
- `web/` is vanilla JS, no build step — hash router + ECharts
- Red Hat Display / Text / Mono fonts served from `web/fonts/` (zero CDN calls)

### New modules (IIIMPACT additions)

Currently stubbed with docstrings describing planned responsibilities; implementation lands across Specs 2–6.

- **`token_dashboard/tips_engine.py`** — Replacement for / expansion of `tips.py`. Adds rules for marathon sessions, hot files, bash output bloat, long-prompt bloat, tool-error thrash. Composable rule registry so new detectors plug in without touching the core. Emits the same tip-record shape (`key`, `category`, `title`, `body`, `scope`) for drop-in UI compatibility.
- **`token_dashboard/health_score.py`** — Per-session 0–100 scoring. Combines token-discipline (distance from 120K / 250K thresholds), cache hit rate, tool-error rate, session length, and correction cycles. Exposes `score(session_id)` + aggregate helpers for trend charts.
- **`token_dashboard/ai_analyzer.py`** — Opt-in, off-by-default. Passes aggregate metrics (not raw prompt text) to an LLM for higher-level recommendations beyond rule-based tips. Settings tab controls whether this runs at all. Results are cached in SQLite so we don't re-call on every dashboard load.
- **`token_dashboard/discipline.py`** — Session-discipline integration. Threshold crossings (60K / 120K / 250K), handoff-protocol recommendations, sub-agent delegation triggers. Mirrors the contract of the `session-discipline` skill so the CLI can emit the same alerts the skill surfaces in-chat.

## Data source

Claude Code writes one JSONL file per session to `~/.claude/projects/<project-slug>/<session-id>.jsonl`. Each line is a message record; usage fields live at `message.usage` and model identifier at `message.model`. The scanner is incremental — it tracks each file's mtime and byte offset in the `files` table and only reads new bytes on subsequent scans.

## Conventions

- **Fully local.** No telemetry, no remote calls for user data by default. The AI analyzer is opt-in and clearly labeled; off by default.
- **Stdlib only for scanner / server / DB.** The AI analyzer may call the Anthropic API when explicitly enabled — that's the one sanctioned outbound path, behind a feature flag.
- **SQLite parameter binding always.** Any f-string in a SQL statement must interpolate only internal, caller-controlled values. User-reachable values go through `?`.
- **Small files with clear responsibilities.** If a file grows past ~400 lines or accretes three distinct concerns, split it.
- **Streaming-snapshot dedup.** When adding scanner logic that joins the `messages` table, remember `(session_id, message_id)` is the dedup key, not `uuid`. See `scanner._evict_prior_snapshots` and the migration note in `db._migrate_add_message_id`.
- **Design system.** New UI follows the Token Intelligence design system: Red Hat typography, glass cards, severity colors (green #19F58C / yellow #FFD600 / red #FF423D / cyan #00FFE0 / purple #8F00FF). All fonts served locally from `web/fonts/`.

## Customizing

Env vars: `PORT` (default 8080), `HOST` (default 127.0.0.1), `CLAUDE_PROJECTS_DIR`, `TOKEN_DASHBOARD_DB`. Pricing lives in `pricing.json`. See README.md § Environment variables for details.

## Known limitations

See `docs/KNOWN_LIMITATIONS.md`. Current summary: Skills `tokens_per_call` is populated only for skills installed under the three scanned roots (`~/.claude/skills/`, `~/.claude/scheduled-tasks/`, `~/.claude/plugins/`); project-local skills and subagent-dispatched skills show invocation counts but blank token counts.

## Verifying changes

```bash
python3 -m unittest discover tests        # all tests
python3 cli.py dashboard --no-open        # start the server
curl http://127.0.0.1:8080/api/overview   # sanity-check an endpoint
```
