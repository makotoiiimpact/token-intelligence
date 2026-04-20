"""Next-generation tips engine (IIIMPACT addition).

Composable rule registry. Each rule is a callable `(db_path, since_iso) -> list[dict]`.
All rules share the emit shape: `rule_id`, `severity`, `session_id`, `message`,
`estimated_savings`, `key`. `key` is a stable `rule_id:session_id|global` string so
existing dismissal (via the `dismissed_tips` table keyed on `tip_key`) keeps working.

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
from datetime import datetime, timedelta
from typing import Callable, List, Optional

from .db import connect

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"


def _days_ago_iso(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


def _key(rule_id: str, session_id: Optional[str]) -> str:
    return f"{rule_id}:{session_id or 'global'}"


def _tokens_from_chars(n_chars: Optional[int]) -> int:
    if not n_chars:
        return 0
    return n_chars // 4


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
            turns = row["turns"] or 0
            tokens = row["tokens"] or 0
            overage = max(0, tokens - 120000)
            severity = SEVERITY_CRITICAL if tokens > 250000 else SEVERITY_WARNING
            out.append({
                "rule_id": "MARATHON_SESSION",
                "severity": severity,
                "session_id": row["session_id"],
                "message": (
                    f"Session hit {turns} turns / {tokens:,} tokens. "
                    "Handoff earlier — past 120K, retrieval accuracy drops measurably."
                ),
                "estimated_savings": overage,
                "key": _key("MARATHON_SESSION", row["session_id"]),
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
    counts: dict = {}
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            if _CORRECTION_PATTERNS.search(row["prompt_text"] or ""):
                counts[row["session_id"]] = counts.get(row["session_id"], 0) + 1
    out: List[dict] = []
    for sid, n in counts.items():
        severity = SEVERITY_WARNING if n >= 3 else SEVERITY_INFO
        out.append({
            "rule_id": "CORRECTION_LOOPS",
            "severity": severity,
            "session_id": sid,
            "message": (
                f"{n} correction message(s) detected. Rewinding with /re before "
                "retrying keeps context clean; failed attempts otherwise linger."
            ),
            "estimated_savings": n * 2000,
            "key": _key("CORRECTION_LOOPS", sid),
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
    vocab: dict = {}
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            tokens = re.findall(r"[a-z][a-z0-9_-]{3,}", (row["prompt_text"] or "").lower())
            s = vocab.setdefault(row["session_id"], set())
            for t in tokens:
                if t not in _NOUN_STOPWORDS:
                    s.add(t)
    out: List[dict] = []
    for sid, s in vocab.items():
        if len(s) >= 40:
            out.append({
                "rule_id": "TASK_DRIFT",
                "severity": SEVERITY_INFO,
                "session_id": sid,
                "message": (
                    f"{len(s)} distinct topic tokens in this session — possible task drift. "
                    "One session per job keeps the model focused."
                ),
                "estimated_savings": 0,
                "key": _key("TASK_DRIFT", sid),
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
            n = row["n"]
            out.append({
                "rule_id": "REDUNDANT_READS",
                "severity": SEVERITY_WARNING,
                "session_id": row["session_id"],
                "message": (
                    f"`{row['target']}` was Read {n}x in one session. "
                    "Summarize in CLAUDE.md or read once per session."
                ),
                "estimated_savings": (n - 1) * 2000,
                "key": _key(f"REDUNDANT_READS:{row['target']}", row["session_id"]),
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
            k = (row["session_id"], row["target"])
            if k in seen:
                continue
            seen.add(k)
            out.append({
                "rule_id": "FILE_BLOAT",
                "severity": SEVERITY_WARNING,
                "session_id": row["session_id"],
                "message": (
                    f"Tool result was {row['result_tokens']:,} tokens. "
                    "Read narrower line ranges or pipe output through head/tail."
                ),
                "estimated_savings": max(0, row["result_tokens"] - 20000),
                "key": _key(f"FILE_BLOAT:{row['target']}", row["session_id"]),
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
            tokens = _tokens_from_chars(row["prompt_chars"])
            out.append({
                "rule_id": "LARGE_PASTE",
                "severity": SEVERITY_INFO,
                "session_id": row["session_id"],
                "message": (
                    f"User message was ~{tokens:,} tokens ({row['prompt_chars']:,} chars). "
                    "Prefer @file references over inline pastes — they cache better."
                ),
                "estimated_savings": max(0, tokens - 5000),
                "key": _key(f"LARGE_PASTE:{row['uuid']}", row["session_id"]),
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
            ratio = row["out_tok"] / max(1, row["in_tok"])
            out.append({
                "rule_id": "OUTPUT_HEAVY_SESSION",
                "severity": SEVERITY_INFO,
                "session_id": row["session_id"],
                "message": (
                    f"Output:input ratio is {ratio:.1f}:1. Unusual — check if the model "
                    "is generating long artifacts that could be files instead."
                ),
                "estimated_savings": 0,
                "key": _key("OUTPUT_HEAVY_SESSION", row["session_id"]),
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
            avg_t = int(row["avg_t"])
            out.append({
                "rule_id": "EXPENSIVE_TOOL",
                "severity": SEVERITY_WARNING,
                "session_id": None,
                "message": (
                    f"`{row['target']}` averaged {avg_t:,} tokens across {row['n']} calls. "
                    "Consider narrower queries or sub-agent delegation."
                ),
                "estimated_savings": max(0, avg_t - 50000) * row["n"],
                "key": _key(f"EXPENSIVE_TOOL:{row['target']}", None),
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
                    out.append({
                        "rule_id": "CACHE_MISS_STREAK",
                        "severity": SEVERITY_WARNING,
                        "session_id": sid,
                        "message": (
                            f"{streak}+ consecutive cache misses. System prompt may be "
                            "changing between turns — review skill loads and CLAUDE.md churn."
                        ),
                        "estimated_savings": streak * 1000,
                        "key": _key("CACHE_MISS_STREAK", sid),
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
            out.append({
                "rule_id": "VAGUE_PROMPT",
                "severity": SEVERITY_INFO,
                "session_id": row["session_id"],
                "message": (
                    f"{row['n']} very-short user messages (<80 chars). "
                    "Terse prompts often force clarification turns; state the goal upfront."
                ),
                "estimated_savings": 0,
                "key": _key("VAGUE_PROMPT", row["session_id"]),
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
    counts: dict = {}
    with connect(db_path) as c:
        for row in c.execute(sql, (since_iso,)):
            if _MULTITASK_PATTERNS.search(row["prompt_text"] or ""):
                counts[row["session_id"]] = counts.get(row["session_id"], 0) + 1
    out: List[dict] = []
    for sid, n in counts.items():
        out.append({
            "rule_id": "MULTI_TASK_PROMPT",
            "severity": SEVERITY_INFO,
            "session_id": sid,
            "message": (
                f"{n} 'and also' pattern(s). Bundled asks compound context; "
                "finish one thread cleanly, then open a new turn."
            ),
            "estimated_savings": 0,
            "key": _key("MULTI_TASK_PROMPT", sid),
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
            out.append({
                "rule_id": "NO_PLAN_MODE",
                "severity": SEVERITY_INFO,
                "session_id": row["session_id"],
                "message": (
                    f"Long session ({row['turns']} turns) with no planning step or skill "
                    "invocation. brainstorming / writing-plans skills pay for themselves."
                ),
                "estimated_savings": 0,
                "key": _key("NO_PLAN_MODE", row["session_id"]),
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
            out.append({
                "rule_id": "_RULE_ERROR",
                "severity": SEVERITY_INFO,
                "session_id": None,
                "message": f"Rule {rule.__name__} crashed: {e}",
                "estimated_savings": 0,
                "key": _key(f"_RULE_ERROR:{rule.__name__}", None),
            })
    out.sort(key=lambda t: t.get("estimated_savings", 0), reverse=True)
    return out


def recompute_tips(db_path, since_iso: Optional[str] = None) -> int:
    tips = all_tips(db_path, since_iso)
    now = time.time()
    with connect(db_path) as c:
        c.execute("DELETE FROM tips")
        c.executemany(
            "INSERT INTO tips (rule_id, severity, session_id, message, estimated_savings, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(t["rule_id"], t["severity"], t["session_id"], t["message"],
              int(t.get("estimated_savings", 0)), now) for t in tips],
        )
        c.commit()
    return len(tips)
