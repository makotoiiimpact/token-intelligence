"""Next-generation tips engine (IIIMPACT addition).

Composable rule registry. Each rule is a callable `(db_path, since_iso) -> list[dict]`.
All rules share the emit shape: `rule_id`, `severity`, `session_id`, `message`,
`estimated_savings`, `key`. `key` is a stable `rule_id:session_id|global` string so
existing dismissal (via the `dismissed_tips` table keyed on `tip_key`) keeps working.

Phase 1 of AI Recos adds structured fields per tip — `title`, `where`, `what`,
`how_to_fix`, `occurred_at`, `deep_link` — alongside (not replacing) the existing
prose `message`. Existing fields stay byte-identical; new fields are additive.

Twelve rules across four categories: session hygiene, file & context, cost, prompting.
Heuristic shortcuts (intentionally simple) are documented inline per rule.

/api/tips returns records in this NEW shape (rule_id/severity/message/estimated_savings),
not the legacy (key/category/title/body/scope) shape emitted by tips.py. Spec 5 rebuilds
the UI against this shape.

Callers:
  * `all_tips(db_path, since_iso=None) -> list[dict]` — runs every rule, sorts by
    estimated_savings desc, returns records.
  * `recompute_tips(db_path, since_iso=None) -> int` — runs rules, truncates and
    rewrites the `tips` table, returns rows written.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from .db import connect

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"

TODO_PRESCRIPTION = "TODO: prescription"


def _days_ago_iso(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def _key(rule_id: str, session_id: Optional[str]) -> str:
    return f"{rule_id}:{session_id or 'global'}"


def _tokens_from_chars(n_chars: Optional[int]) -> int:
    if not n_chars:
        return 0
    return n_chars // 4


def _session_started_at(conn, session_id: Optional[str]) -> Optional[str]:
    """Return MIN(messages.timestamp) for `session_id` as an ISO string, or None.

    None for global rules (session_id is None) and when the session has no rows.
    """
    if session_id is None:
        return None
    row = conn.execute(
        "SELECT MIN(timestamp) AS t FROM messages WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    return row["t"] if row and row["t"] else None


def _iso_to_epoch(iso: Optional[str]) -> Optional[float]:
    """Parse an ISO-8601 timestamp (with optional trailing Z) to epoch seconds."""
    if not iso:
        return None
    s = iso.replace("Z", "+00:00") if iso.endswith("Z") else iso
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


# ---------- SESSION HYGIENE ----------


def marathon_session(db_path, since_iso: str) -> List[dict]:
    sql = """
      SELECT session_id,
             SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
             SUM(input_tokens+output_tokens+cache_create_5m_tokens+cache_create_1h_tokens) AS tokens
        FROM messages
       WHERE timestamp >= ?
       GROUP BY session_id
      HAVING turns > 30 OR tokens > 120000
    """
    out: List[dict] = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            sid = row["session_id"]
            turns = row["turns"] or 0
            tokens = row["tokens"] or 0
            overage = max(0, tokens - 120000)
            severity = SEVERITY_CRITICAL if tokens > 250000 else SEVERITY_WARNING
            what = (
                "Session ran past the 120K context window where "
                "retrieval accuracy degrades measurably."
            )
            how_to_fix = (
                "Initiate a structured handoff at the 90K–120K (🟠) threshold. "
                "Externalize state to Notion or a plan doc, then start a fresh session."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "MARATHON_SESSION",
                "severity": severity,
                "session_id": sid,
                "message": message,
                "estimated_savings": overage,
                "key": _key("MARATHON_SESSION", sid),
                "title": "Marathon session",
                "where": f"Session {sid} · {turns:,} turns · {tokens:,} tokens",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


_CORRECTION_PATTERNS = re.compile(
    r"\b(try again|that'?s wrong|not what i meant|that isn'?t right|not quite|redo that)\b",
    re.IGNORECASE,
)


def correction_loops(db_path, since_iso: str) -> List[dict]:
    sql = """
      SELECT session_id, prompt_text
        FROM messages
       WHERE type='user' AND prompt_text IS NOT NULL AND timestamp >= ?
    """
    out: List[dict] = []
    with connect(db_path) as c:
        counts: dict = {}
        for row in c.execute(sql, (since_iso,)):
            if _CORRECTION_PATTERNS.search(row["prompt_text"] or ""):
                counts[row["session_id"]] = counts.get(row["session_id"], 0) + 1
        for sid, n in counts.items():
            severity = SEVERITY_WARNING if n >= 3 else SEVERITY_INFO
            what = (
                f"Phrases like \"try again\" and \"that's wrong\" surfaced {n} times. "
                "Repeated correction usually means the spec is wrong, not the output."
            )
            how_to_fix = (
                "Stop and rewind to the last good prompt. "
                "Restate the goal in one sentence, then retry. "
                "Don't iterate forward through corrections."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "CORRECTION_LOOPS",
                "severity": severity,
                "session_id": sid,
                "message": message,
                "estimated_savings": n * 2000,
                "key": _key("CORRECTION_LOOPS", sid),
                "title": "Correction loops",
                "where": f"Session {sid} · {n} correction message(s)",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


_NOUN_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "for", "in", "on", "at",
    "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "can", "could", "will", "would", "should", "i", "you",
    "we", "they", "this", "that", "these", "those", "it", "me", "my", "your",
    "please", "thanks", "thank", "okay", "ok", "yes", "no", "also", "just",
    "with", "from", "into", "over", "under", "about", "some", "more", "less",
}


def task_drift(db_path, since_iso: str) -> List[dict]:
    """Heuristic shortcut: count distinct 4+ char lowercase noun-ish tokens across
    all user prompts in a session. >=40 distinct tokens in one session suggests drift.
    """
    sql = """
      SELECT session_id, prompt_text
        FROM messages
       WHERE type='user' AND prompt_text IS NOT NULL AND timestamp >= ?
    """
    out: List[dict] = []
    with connect(db_path) as c:
        vocab: dict = {}
        for row in c.execute(sql, (since_iso,)):
            tokens = re.findall(r"[a-z][a-z0-9_-]{3,}", (row["prompt_text"] or "").lower())
            s = vocab.setdefault(row["session_id"], set())
            for t in tokens:
                if t not in _NOUN_STOPWORDS:
                    s.add(t)
        for sid, s in vocab.items():
            if len(s) < 40:
                continue
            what = (
                f"{len(s)} distinct topic tokens in this session "
                "suggest it's covering multiple jobs at once."
            )
            how_to_fix = (
                "One session per job. Externalize what's done so far, "
                "close this session, and open a fresh one for the next task."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "TASK_DRIFT",
                "severity": SEVERITY_INFO,
                "session_id": sid,
                "message": message,
                "estimated_savings": 0,
                "key": _key("TASK_DRIFT", sid),
                "title": "Task drift",
                "where": f"Session {sid} · {len(s)} distinct topic tokens",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


# ---------- FILE & CONTEXT ----------


def redundant_reads(db_path, since_iso: str) -> List[dict]:
    sql = """
      SELECT session_id, target, COUNT(*) AS n
        FROM tool_calls
       WHERE tool_name='Read' AND timestamp >= ? AND target IS NOT NULL
       GROUP BY session_id, target
      HAVING n > 3
    """
    out: List[dict] = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            sid = row["session_id"]
            target = row["target"]
            n = row["n"]
            what = f"File was read {n}× in a single session."
            how_to_fix = (
                "Summarize the file in CLAUDE.md once and reference the cached "
                "summary instead of re-reading on every turn."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "REDUNDANT_READS",
                "severity": SEVERITY_WARNING,
                "session_id": sid,
                "message": message,
                "estimated_savings": (n - 1) * 2000,
                "key": _key(f"REDUNDANT_READS:{target}", sid),
                "title": "Redundant reads",
                "where": f"`{target}` · session {sid}",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


def file_bloat(db_path, since_iso: str) -> List[dict]:
    sql = """
      SELECT session_id, target, result_tokens
        FROM tool_calls
       WHERE tool_name='_tool_result' AND result_tokens > 20000 AND timestamp >= ?
    """
    out: List[dict] = []
    seen: set = set()
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            sid = row["session_id"]
            target = row["target"]
            k = (sid, target)
            if k in seen:
                continue
            seen.add(k)
            tokens = row["result_tokens"]
            what = f"Tool result returned {tokens:,} tokens — past the 20K bloat threshold."
            how_to_fix = (
                "Read the file in slices (specific line ranges) or grep for the "
                "section needed. Avoid loading whole large files into context."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "FILE_BLOAT",
                "severity": SEVERITY_WARNING,
                "session_id": sid,
                "message": message,
                "estimated_savings": max(0, tokens - 20000),
                "key": _key(f"FILE_BLOAT:{target}", sid),
                "title": "File bloat",
                "where": f"`{target}` · session {sid}",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


def large_paste(db_path, since_iso: str) -> List[dict]:
    """LARGE_PASTE flags user messages >5K tokens (estimated as prompt_chars/4 > 5000,
    i.e. prompt_chars > 20000) — inline file pastes that should be @file references.
    """
    sql = """
      SELECT session_id, uuid, prompt_chars
        FROM messages
       WHERE type='user' AND prompt_chars IS NOT NULL AND prompt_chars > 20000
         AND timestamp >= ?
    """
    out: List[dict] = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            sid = row["session_id"]
            uuid = row["uuid"]
            chars = row["prompt_chars"]
            tokens = _tokens_from_chars(chars)
            what = f"Pasted prompt was ~{tokens:,} tokens, well past the 5K threshold."
            how_to_fix = (
                "Convert pasted content first: HTML→markdown (~90% reduction), "
                "PDF→text (~65–70%), DOCX→markdown (~33%). "
                "Or upload as a file instead of pasting inline."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "LARGE_PASTE",
                "severity": SEVERITY_INFO,
                "session_id": sid,
                "message": message,
                "estimated_savings": max(0, tokens - 5000),
                "key": _key(f"LARGE_PASTE:{uuid}", sid),
                "title": "Large paste",
                "where": f"Session {sid} · message {(uuid or '')[:8]}",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


# ---------- COST ----------


def output_heavy_session(db_path, since_iso: str) -> List[dict]:
    sql = """
      SELECT session_id,
             SUM(input_tokens) AS in_tok,
             SUM(output_tokens) AS out_tok
        FROM messages
       WHERE type='assistant' AND timestamp >= ?
       GROUP BY session_id
      HAVING in_tok > 0 AND out_tok > 0 AND (out_tok * 1.0 / in_tok) > 10
    """
    out: List[dict] = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            sid = row["session_id"]
            ratio = row["out_tok"] / max(1, row["in_tok"])
            what = (
                f"Output dominates the session at a {ratio:.1f}:1 ratio. "
                "The model is generating more than it's consuming."
            )
            how_to_fix = (
                "Route long artifacts to files instead of inline text. "
                "File output doesn't reload into the next turn's context."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "OUTPUT_HEAVY_SESSION",
                "severity": SEVERITY_INFO,
                "session_id": sid,
                "message": message,
                "estimated_savings": 0,
                "key": _key("OUTPUT_HEAVY_SESSION", sid),
                "title": "Output-heavy session",
                "where": f"Session {sid} · {ratio:.1f}:1 output:input ratio",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


def expensive_tool(db_path, since_iso: str) -> List[dict]:
    sql = """
      SELECT target,
             COUNT(*) AS n,
             AVG(result_tokens) AS avg_t
        FROM tool_calls
       WHERE tool_name='_tool_result' AND result_tokens > 0 AND target IS NOT NULL
         AND timestamp >= ?
       GROUP BY target
      HAVING n >= 3 AND avg_t > 50000
       ORDER BY avg_t DESC
       LIMIT 20
    """
    out: List[dict] = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            target = row["target"]
            n = row["n"]
            avg_t = int(row["avg_t"])
            # `target` and `n` already live in `where`; keep `what` to the diagnosis only.
            what = f"Each call averaged {avg_t:,} tokens."
            how_to_fix = (
                "Narrow the tool's input scope (specific path, line range, query). "
                "If breadth is required, run once and cache the result rather than re-invoking."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "EXPENSIVE_TOOL",
                "severity": SEVERITY_WARNING,
                "session_id": None,
                "message": message,
                "estimated_savings": max(0, avg_t - 50000) * n,
                "key": _key(f"EXPENSIVE_TOOL:{target}", None),
                "title": "Expensive tool",
                "where": f"`{target}` · {n} calls",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": None,
                "deep_link": None,
            })
    return out


def cache_miss_streak(db_path, since_iso: str) -> List[dict]:
    sql = """
      SELECT session_id, timestamp, cache_read_tokens
        FROM messages
       WHERE type='assistant' AND timestamp >= ?
       ORDER BY session_id, timestamp
    """
    out: List[dict] = []
    prev_sid = None
    streak = 0
    flagged: set = set()
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            sid = row["session_id"]
            if sid != prev_sid:
                streak = 0
                prev_sid = sid
            if (row["cache_read_tokens"] or 0) == 0:
                streak += 1
                if streak >= 5 and sid not in flagged:
                    flagged.add(sid)
                    what = (
                        f"{streak} consecutive assistant turns with zero cache reads. "
                        "The prompt cache is being invalidated."
                    )
                    how_to_fix = (
                        "Avoid editing earlier messages or system prompts mid-session. "
                        "Cache invalidation forces full re-tokenization on every turn "
                        "and erases the discount."
                    )
                    message = f"{what} {how_to_fix}"
                    out.append({
                        "rule_id": "CACHE_MISS_STREAK",
                        "severity": SEVERITY_WARNING,
                        "session_id": sid,
                        "message": message,
                        "estimated_savings": streak * 1000,
                        "key": _key("CACHE_MISS_STREAK", sid),
                        "title": "Cache miss streak",
                        "where": f"Session {sid} · {streak}+ consecutive misses",
                        "what": what,
                        "how_to_fix": how_to_fix,
                        "occurred_at": _session_started_at(c, sid),
                        "deep_link": f"/sessions/{sid}",
                    })
            else:
                streak = 0
    return out


# ---------- PROMPTING ----------


def vague_prompt(db_path, since_iso: str) -> List[dict]:
    """Heuristic shortcut: flag sessions with 3+ user messages <80 chars (<20 tokens).
    We don't reliably detect a 'clarification follow-up'; the accumulated short-length
    signal alone is enough to suggest terseness is a pattern.
    """
    sql = """
      SELECT session_id, COUNT(*) AS n
        FROM messages
       WHERE type='user' AND prompt_chars IS NOT NULL AND prompt_chars < 80
         AND prompt_chars > 0 AND timestamp >= ?
       GROUP BY session_id
      HAVING n >= 3
    """
    out: List[dict] = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            sid = row["session_id"]
            n = row["n"]
            what = (
                f"{n} user messages under 80 characters. "
                "Terse prompts force clarification turns that burn context."
            )
            how_to_fix = (
                "State the goal, the constraint, and the deliverable in the first message. "
                "\"Fix the bug\" → \"Fix the timezone bug in parse_session.py "
                "so test_dst_boundary passes.\""
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "VAGUE_PROMPT",
                "severity": SEVERITY_INFO,
                "session_id": sid,
                "message": message,
                "estimated_savings": 0,
                "key": _key("VAGUE_PROMPT", sid),
                "title": "Vague prompt",
                "where": f"Session {sid} · {n} short messages",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


_MULTITASK_PATTERNS = re.compile(
    r"\b(also|while you'?re at it|one more thing|additionally|oh and|by the way)\b",
    re.IGNORECASE,
)


def multi_task_prompt(db_path, since_iso: str) -> List[dict]:
    sql = """
      SELECT session_id, prompt_text
        FROM messages
       WHERE type='user' AND prompt_text IS NOT NULL AND timestamp >= ?
    """
    out: List[dict] = []
    with connect(db_path) as c:
        counts: dict = {}
        for row in c.execute(sql, (since_iso,)):
            if _MULTITASK_PATTERNS.search(row["prompt_text"] or ""):
                counts[row["session_id"]] = counts.get(row["session_id"], 0) + 1
        for sid, n in counts.items():
            what = (
                f"\"And also\" / \"while you're at it\" appeared {n} time(s). "
                "Bundled asks dilute focus and compound context."
            )
            how_to_fix = (
                "Finish the current task, ship it, then open a new turn for the next ask. "
                "Resist task creep mid-mission."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "MULTI_TASK_PROMPT",
                "severity": SEVERITY_INFO,
                "session_id": sid,
                "message": message,
                "estimated_savings": 0,
                "key": _key("MULTI_TASK_PROMPT", sid),
                "title": "Multi-task prompt",
                "where": f"Session {sid} · {n} 'and also' pattern(s)",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


def no_plan_mode(db_path, since_iso: str) -> List[dict]:
    """Flag sessions with >=10 turns where no 'plan' word appears in any user message
    AND no Skill tool was invoked. Heuristic proxy for 'jumped straight to implementation'.
    """
    sql = """
      SELECT m.session_id,
             SUM(CASE WHEN m.type='user' THEN 1 ELSE 0 END) AS turns,
             SUM(CASE WHEN m.prompt_text IS NOT NULL
                      AND lower(m.prompt_text) LIKE '%plan%' THEN 1 ELSE 0 END) AS plan_hits,
             (SELECT COUNT(*) FROM tool_calls tc
               WHERE tc.session_id = m.session_id AND tc.tool_name='Skill') AS skill_calls
        FROM messages m
       WHERE m.timestamp >= ?
       GROUP BY m.session_id
      HAVING turns >= 10 AND plan_hits = 0 AND skill_calls = 0
    """
    out: List[dict] = []
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            sid = row["session_id"]
            turns = row["turns"]
            what = (
                f"Session ran {turns} turns with no planning step "
                "and no skill invocation."
            )
            how_to_fix = (
                "Start with a plan. Invoke a planning skill (brainstorming, writing-plans) "
                "before code work — it pays for itself in fewer correction cycles."
            )
            message = f"{what} {how_to_fix}"
            out.append({
                "rule_id": "NO_PLAN_MODE",
                "severity": SEVERITY_INFO,
                "session_id": sid,
                "message": message,
                "estimated_savings": 0,
                "key": _key("NO_PLAN_MODE", sid),
                "title": "No plan mode",
                "where": f"Session {sid} · {turns} turns, no plan",
                "what": what,
                "how_to_fix": how_to_fix,
                "occurred_at": _session_started_at(c, sid),
                "deep_link": f"/sessions/{sid}",
            })
    return out


# ---------- Registry ----------

RULES: List[Callable] = [
    marathon_session,
    correction_loops,
    task_drift,
    redundant_reads,
    file_bloat,
    large_paste,
    output_heavy_session,
    expensive_tool,
    cache_miss_streak,
    vague_prompt,
    multi_task_prompt,
    no_plan_mode,
]


def all_tips(db_path, since_iso: Optional[str] = None) -> List[dict]:
    since_iso = since_iso or _days_ago_iso(7)
    out: List[dict] = []
    for rule in RULES:
        try:
            out.extend(rule(db_path, since_iso))
        except Exception as e:
            message = f"Rule {rule.__name__} crashed: {e}"
            out.append({
                "rule_id": "_RULE_ERROR",
                "severity": SEVERITY_INFO,
                "session_id": None,
                "message": message,
                "estimated_savings": 0,
                "key": _key(f"_RULE_ERROR:{rule.__name__}", None),
                "title": "Rule error",
                "where": f"rule {rule.__name__}",
                "what": message,
                "how_to_fix": TODO_PRESCRIPTION,
                "occurred_at": None,
                "deep_link": None,
            })
    out.sort(key=lambda t: t.get("estimated_savings", 0), reverse=True)
    return out


def recompute_tips(db_path, since_iso: Optional[str] = None) -> int:
    tips = all_tips(db_path, since_iso)
    now = time.time()
    with connect(db_path) as c:
        c.execute("DELETE FROM tips")
        c.executemany(
            "INSERT INTO tips (rule_id, severity, session_id, message, estimated_savings, "
            "created_at, title, where_text, what_text, how_to_fix, occurred_at, deep_link) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(
                t["rule_id"], t["severity"], t["session_id"], t["message"],
                int(t.get("estimated_savings", 0)), now,
                t.get("title"), t.get("where"), t.get("what"),
                t.get("how_to_fix"), _iso_to_epoch(t.get("occurred_at")),
                t.get("deep_link"),
            ) for t in tips],
        )
        c.commit()
    return len(tips)
