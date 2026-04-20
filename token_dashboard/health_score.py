"""Session health scoring (IIIMPACT addition).

Per-session 0-100 score combining token discipline, cache efficiency, correction
cycles, and file-read efficiency. Start at 100, apply penalties and bonuses, clamp.

Penalties:
  * Turn count: 0 up to 30, -1 per turn beyond, floor at -30.
  * Total tokens: 0 below 120K, -20 in [120K, 250K], -50 above 250K.
  * Correction cycles: -5 per correction detected (reuses tips_engine pattern).

Bonuses:
  * Cache hit rate > 60%: up to +10 (scaled linearly 0.6 -> 1.0).
  * File-read efficiency (unique_reads / total_reads) > 0.7: up to +5.

Color map (matches design-system.md): 80-100 green (#19F58C), 50-79 yellow (#FFD600),
0-49 red (#FF423D).
"""
from __future__ import annotations

import json
import re
import time
from typing import Dict, List, Optional

from .db import connect

_CORRECTION = re.compile(
    r"\b(try again|that'?s wrong|not what i meant|that isn'?t right|not quite|redo that)\b",
    re.IGNORECASE,
)


def _session_stats(db_path, session_id: str) -> Dict:
    with connect(db_path) as c:
        totals = c.execute(
            """
            SELECT SUM(CASE WHEN type='user' THEN 1 ELSE 0 END) AS turns,
                   COALESCE(SUM(input_tokens),0) AS in_tok,
                   COALESCE(SUM(output_tokens),0) AS out_tok,
                   COALESCE(SUM(cache_read_tokens),0) AS cache_read,
                   COALESCE(SUM(cache_create_5m_tokens),0)
                   + COALESCE(SUM(cache_create_1h_tokens),0) AS cache_create
              FROM messages
             WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        read_counts = c.execute(
            """
            SELECT COUNT(*) AS total, COUNT(DISTINCT target) AS unique_
              FROM tool_calls
             WHERE session_id = ? AND tool_name = 'Read' AND target IS NOT NULL
            """,
            (session_id,),
        ).fetchone()
        corrections = 0
        for row in c.execute(
            "SELECT prompt_text FROM messages WHERE session_id=? AND type='user' AND prompt_text IS NOT NULL",
            (session_id,),
        ):
            if _CORRECTION.search(row["prompt_text"] or ""):
                corrections += 1
    turns = (totals["turns"] or 0) if totals else 0
    in_tok = (totals["in_tok"] or 0) if totals else 0
    out_tok = (totals["out_tok"] or 0) if totals else 0
    cache_read = (totals["cache_read"] or 0) if totals else 0
    cache_create = (totals["cache_create"] or 0) if totals else 0
    total_tokens = in_tok + out_tok + cache_create
    cache_denom = cache_read + in_tok + cache_create
    cache_hit_rate = (cache_read / cache_denom) if cache_denom else 0.0
    reads_total = read_counts["total"] if read_counts else 0
    reads_unique = read_counts["unique_"] if read_counts else 0
    read_efficiency = (reads_unique / reads_total) if reads_total else 1.0
    return {
        "turns": turns,
        "total_tokens": total_tokens,
        "cache_hit_rate": cache_hit_rate,
        "correction_cycles": corrections,
        "reads_total": reads_total,
        "reads_unique": reads_unique,
        "read_efficiency": read_efficiency,
    }


def _compute_components(stats: Dict) -> Dict:
    components: Dict = {"base": 100}

    turns = stats["turns"]
    turn_penalty = -min(30, max(0, turns - 30))
    components["turn_penalty"] = turn_penalty

    tokens = stats["total_tokens"]
    if tokens > 250_000:
        token_penalty = -50
    elif tokens > 120_000:
        token_penalty = -20
    else:
        token_penalty = 0
    components["token_penalty"] = token_penalty

    corrections = stats["correction_cycles"]
    components["correction_penalty"] = -5 * corrections

    hit = stats["cache_hit_rate"]
    if hit > 0.6:
        cache_bonus = int(round(min(1.0, (hit - 0.6) / 0.4) * 10))
    else:
        cache_bonus = 0
    components["cache_bonus"] = cache_bonus

    eff = stats["read_efficiency"]
    if stats["reads_total"] > 0 and eff > 0.7:
        read_bonus = int(round(min(1.0, (eff - 0.7) / 0.3) * 5))
    else:
        read_bonus = 0
    components["read_bonus"] = read_bonus

    return components


def _score_from_components(components: Dict) -> int:
    score = sum(components.values())
    return max(0, min(100, score))


def score_session(db_path, session_id: str) -> int:
    stats = _session_stats(db_path, session_id)
    components = _compute_components(stats)
    return _score_from_components(components)


def session_breakdown(db_path, session_id: str) -> Dict:
    stats = _session_stats(db_path, session_id)
    components = _compute_components(stats)
    return {
        "session_id": session_id,
        "score": _score_from_components(components),
        "components": components,
        "stats": stats,
    }


def score_all(db_path) -> List[Dict]:
    with connect(db_path) as c:
        sids = [r["session_id"] for r in c.execute("SELECT DISTINCT session_id FROM messages")]
    return [session_breakdown(db_path, sid) for sid in sids]


def recompute_health(db_path) -> int:
    rows = score_all(db_path)
    now = time.time()
    with connect(db_path) as c:
        c.executemany(
            """
            INSERT OR REPLACE INTO session_health
              (session_id, health_score, turn_count, total_tokens,
               correction_cycles, cache_hit_rate, components_json, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    r["session_id"],
                    r["score"],
                    r["stats"]["turns"],
                    r["stats"]["total_tokens"],
                    r["stats"]["correction_cycles"],
                    r["stats"]["cache_hit_rate"],
                    json.dumps(r["components"]),
                    now,
                )
                for r in rows
            ],
        )
        c.commit()
    return len(rows)


def discipline_aggregate(db_path) -> Dict:
    with connect(db_path) as c:
        row = c.execute(
            """
            SELECT AVG(health_score) AS avg_score,
                   AVG(CASE WHEN total_tokens > 120000 THEN 1.0 ELSE 0.0 END) AS pct_over_120k,
                   AVG(correction_cycles) AS avg_corrections,
                   SUM(CASE WHEN total_tokens >= 60000  THEN 1 ELSE 0 END) AS n_60k,
                   SUM(CASE WHEN total_tokens >= 120000 THEN 1 ELSE 0 END) AS n_120k,
                   SUM(CASE WHEN total_tokens >= 180000 THEN 1 ELSE 0 END) AS n_180k,
                   SUM(CASE WHEN total_tokens >= 250000 THEN 1 ELSE 0 END) AS n_250k,
                   COUNT(*) AS total_sessions
              FROM session_health
            """
        ).fetchone()
    if not row or (row["total_sessions"] or 0) == 0:
        return {
            "avg_score": None,
            "pct_over_120k": 0.0,
            "avg_corrections": 0.0,
            "threshold_counts": {"60k": 0, "120k": 0, "180k": 0, "250k": 0},
            "total_sessions": 0,
        }
    return {
        "avg_score": round(row["avg_score"], 1) if row["avg_score"] is not None else None,
        "pct_over_120k": round(row["pct_over_120k"] or 0.0, 3),
        "avg_corrections": round(row["avg_corrections"] or 0.0, 2),
        "threshold_counts": {
            "60k":  row["n_60k"]  or 0,
            "120k": row["n_120k"] or 0,
            "180k": row["n_180k"] or 0,
            "250k": row["n_250k"] or 0,
        },
        "total_sessions": row["total_sessions"],
    }
