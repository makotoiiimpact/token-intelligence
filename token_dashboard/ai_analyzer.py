"""AI-powered usage analysis (IIIMPACT addition).

Opt-in, off by default. Sends AGGREGATE METRICS ONLY to Claude API (never
prompt_text, file contents, or raw tool results). Uses stdlib urllib.request —
no anthropic SDK dependency, preserving zero-install.

Public functions:
  * `analyze_usage(db_path, force=False) -> dict` — cross-session recommendations
  * `analyze_session(db_path, session_id, force=False) -> dict` — session autopsy
  * `get_cached_analysis(db_path, session_id)` — lookup only, no API call

Caching: ai_analyses table stores results for 24h. Rate limit: 1 API call per 60s.

Errors:
  * `ApiKeyMissingError` — raised when ANTHROPIC_API_KEY is absent. Message is
    the verbatim string shown to users: "Configure API key in Settings".
  * `RateLimitError` — raised when called <60s after the last API call.

Server graceful degradation: /api/analyze* endpoints catch ApiKeyMissingError
and return HTTP 200 with `{"configured": false, "error": ...}` — not 500.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
CACHE_TTL_SECONDS = 24 * 3600
RATE_LIMIT_SECONDS = 60
HTTP_TIMEOUT = 60

_LAST_CALL_AT: float = 0.0


class ApiKeyMissingError(Exception):
    def __init__(self) -> None:
        super().__init__("Configure API key in Settings")


class RateLimitError(Exception):
    pass


def _require_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise ApiKeyMissingError()
    return key


def _check_rate_limit() -> None:
    global _LAST_CALL_AT
    elapsed = time.time() - _LAST_CALL_AT
    if elapsed < RATE_LIMIT_SECONDS:
        raise RateLimitError(f"Wait {int(RATE_LIMIT_SECONDS - elapsed)}s before next call")


def _mark_api_called() -> None:
    global _LAST_CALL_AT
    _LAST_CALL_AT = time.time()


def _connect(db_path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _aggregate_usage_stats(db_path) -> dict:
    with _connect(db_path) as c:
        totals = c.execute(
            """
            SELECT COUNT(DISTINCT session_id) AS session_count,
                   SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS total_turns,
                   COALESCE(SUM(input_tokens),0)  AS input_tokens,
                   COALESCE(SUM(output_tokens),0) AS output_tokens,
                   COALESCE(SUM(cache_read_tokens),0) AS cache_read,
                   COALESCE(SUM(cache_create_5m_tokens),0)
                   + COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create
              FROM messages
            """
        ).fetchone()
        per_session = c.execute(
            """
            SELECT session_id,
                   SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
                   SUM(input_tokens+output_tokens) AS tokens
              FROM messages
             GROUP BY session_id
            """
        ).fetchall()
        top_tools = [dict(r) for r in c.execute(
            """
            SELECT tool_name, COUNT(*) AS call_count,
                   COALESCE(AVG(result_tokens),0) AS avg_result_tokens
              FROM tool_calls
             WHERE tool_name != '_tool_result'
             GROUP BY tool_name
             ORDER BY call_count DESC
             LIMIT 10
            """
        )]
        model_dist = [dict(r) for r in c.execute(
            """
            SELECT COALESCE(model,'unknown') AS model, COUNT(*) AS turns
              FROM messages
             WHERE type='assistant'
             GROUP BY model
             ORDER BY turns DESC
            """
        )]
        daily_trend = [dict(r) for r in c.execute(
            """
            SELECT substr(timestamp,1,10) AS day,
                   COALESCE(SUM(input_tokens+output_tokens),0) AS tokens
              FROM messages
             WHERE timestamp IS NOT NULL
               AND timestamp >= date('now','-30 days')
             GROUP BY day ORDER BY day ASC
            """
        )]

    session_count = totals["session_count"] or 0
    turns_per = [row["turns"] or 0 for row in per_session]
    tokens_per = [row["tokens"] or 0 for row in per_session]
    avg_turns = (sum(turns_per) / session_count) if session_count else 0
    avg_tokens = (sum(tokens_per) / session_count) if session_count else 0
    cache_denom = (totals["cache_read"] or 0) + (totals["input_tokens"] or 0) + (totals["cache_create"] or 0)
    cache_hit_rate = ((totals["cache_read"] or 0) / cache_denom) if cache_denom else 0.0
    return {
        "session_count":            session_count,
        "total_turns":              totals["total_turns"] or 0,
        "avg_turns_per_session":    round(avg_turns, 1),
        "avg_tokens_per_session":   int(avg_tokens),
        "total_input_tokens":       totals["input_tokens"] or 0,
        "total_output_tokens":      totals["output_tokens"] or 0,
        "cache_hit_rate":           round(cache_hit_rate, 3),
        "top_tools":                top_tools,
        "model_distribution":       model_dist,
        "daily_token_trend":        daily_trend,
    }


def _aggregate_session_stats(db_path, session_id: str) -> dict:
    with _connect(db_path) as c:
        turns = [dict(r) for r in c.execute(
            """
            SELECT type, input_tokens, output_tokens, cache_read_tokens,
                   prompt_chars, tool_calls_json, timestamp
              FROM messages
             WHERE session_id = ?
             ORDER BY timestamp ASC
            """,
            (session_id,),
        )]
    turn_summaries = []
    total_input = 0
    total_output = 0
    turn_number = 0
    for t in turns:
        if t["type"] != "assistant":
            continue
        turn_number += 1
        tools_used = []
        if t["tool_calls_json"]:
            try:
                calls = json.loads(t["tool_calls_json"])
                tools_used = [c.get("name", "") for c in calls if isinstance(c, dict)]
            except json.JSONDecodeError:
                pass
        turn_summaries.append({
            "turn_number": turn_number,
            "input_tokens":       t["input_tokens"] or 0,
            "output_tokens":      t["output_tokens"] or 0,
            "cache_read_tokens":  t["cache_read_tokens"] or 0,
            "tool_names_used":    tools_used,
            "user_msg_length_chars": t["prompt_chars"] or 0,
        })
        total_input  += t["input_tokens"] or 0
        total_output += t["output_tokens"] or 0
    return {
        "session_id":   session_id,
        "turn_count":   turn_number,
        "total_input":  total_input,
        "total_output": total_output,
        "turns":        turn_summaries,
    }


def _post_to_claude(api_key: str, system: str, user_msg: str, model: str = DEFAULT_MODEL) -> dict:
    _check_rate_limit()
    body = json.dumps({
        "model": model,
        "max_tokens": 2048,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}],
    }).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=body,
        headers={
            "x-api-key":         api_key,
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type":      "application/json",
        },
        method="POST",
    )
    _mark_api_called()
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Claude API {e.code}: {detail}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude API returned non-JSON: {raw[:200]}") from e


def _extract_text(api_response: dict) -> str:
    blocks = api_response.get("content", []) or []
    parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip()


def _cached_row(db_path, analysis_type: str, session_id: Optional[str]) -> Optional[dict]:
    with _connect(db_path) as c:
        if session_id is None:
            row = c.execute(
                "SELECT analysis_json, analyzed_at, model_used FROM ai_analyses "
                "WHERE analysis_type=? AND session_id IS NULL "
                "ORDER BY analyzed_at DESC LIMIT 1",
                (analysis_type,),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT analysis_json, analyzed_at, model_used FROM ai_analyses "
                "WHERE analysis_type=? AND session_id=? "
                "ORDER BY analyzed_at DESC LIMIT 1",
                (analysis_type, session_id),
            ).fetchone()
    if not row:
        return None
    age = time.time() - row["analyzed_at"]
    if age > CACHE_TTL_SECONDS:
        return None
    data = json.loads(row["analysis_json"])
    data["cached"] = True
    data["model_used"] = row["model_used"]
    data["configured"] = True
    return data


def _store_row(db_path, analysis_type: str, session_id: Optional[str], data: dict, model: str) -> None:
    with _connect(db_path) as c:
        c.execute(
            "INSERT INTO ai_analyses (session_id, analysis_type, analysis_json, analyzed_at, model_used) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, analysis_type, json.dumps(data), time.time(), model),
        )
        c.commit()


_USAGE_SYSTEM = (
    "You are a senior Claude Code usage coach. Given aggregate usage metrics for a "
    "developer, return concise, actionable recommendations. Cite specific numbers. "
    "Output a JSON object with keys: summary (one paragraph), recommendations "
    "(array of strings, <=5 items). Return ONLY valid JSON, no prose."
)

_SESSION_SYSTEM = (
    "You are a senior Claude Code session analyst performing an autopsy on a single "
    "session's turn-by-turn metrics (no actual content, only counts and lengths). "
    "Identify where the session went off-track, wasteful turns, optimal handoff point, "
    "and estimated savings. Output a JSON object with keys: narrative (one paragraph), "
    "off_track_turns (array of turn_number ints), wasteful_turns (array of turn_number ints), "
    "optimal_handoff_turn (int or null), estimated_savings_tokens (int). Return ONLY valid JSON."
)


def _parse_structured(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {"summary": text, "recommendations": [], "narrative": text}


def analyze_usage(db_path, force: bool = False) -> dict:
    api_key = _require_api_key()
    if not force:
        cached = _cached_row(db_path, "usage", None)
        if cached:
            return cached
    stats = _aggregate_usage_stats(db_path)
    user_msg = (
        "Here are my last 30 days of Claude Code usage metrics (aggregates only, "
        "no prompt text). Recommend concrete adjustments to reduce waste and "
        "improve session discipline.\n\n"
        f"```json\n{json.dumps(stats, indent=2)}\n```"
    )
    response = _post_to_claude(api_key, _USAGE_SYSTEM, user_msg)
    text = _extract_text(response)
    parsed = _parse_structured(text)
    result = {
        "summary":         parsed.get("summary", ""),
        "recommendations": parsed.get("recommendations", []),
        "generated_at":    datetime.utcnow().isoformat() + "Z",
        "cached":          False,
        "configured":      True,
        "model_used":      DEFAULT_MODEL,
    }
    _store_row(db_path, "usage", None, result, DEFAULT_MODEL)
    return result


def analyze_session(db_path, session_id: str, force: bool = False) -> dict:
    api_key = _require_api_key()
    if not force:
        cached = _cached_row(db_path, "session", session_id)
        if cached:
            return cached
    stats = _aggregate_session_stats(db_path, session_id)
    user_msg = (
        "Here is the turn-by-turn metrics profile of a single Claude Code session "
        "(no message content, only token counts, tools used, and message lengths). "
        "Produce the autopsy.\n\n"
        f"```json\n{json.dumps(stats, indent=2)}\n```"
    )
    response = _post_to_claude(api_key, _SESSION_SYSTEM, user_msg)
    text = _extract_text(response)
    parsed = _parse_structured(text)
    result = {
        "narrative":                 parsed.get("narrative", ""),
        "off_track_turns":           parsed.get("off_track_turns", []),
        "wasteful_turns":            parsed.get("wasteful_turns", []),
        "optimal_handoff_turn":      parsed.get("optimal_handoff_turn"),
        "estimated_savings_tokens":  int(parsed.get("estimated_savings_tokens", 0) or 0),
        "generated_at":              datetime.utcnow().isoformat() + "Z",
        "cached":                    False,
        "configured":                True,
        "model_used":                DEFAULT_MODEL,
    }
    _store_row(db_path, "session", session_id, result, DEFAULT_MODEL)
    return result


def get_cached_analysis(db_path, session_id: str) -> Optional[dict]:
    return _cached_row(db_path, "session", session_id)
