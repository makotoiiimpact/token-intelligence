"""Session health scoring (IIIMPACT addition).

Planned responsibilities:
  * Score every session on a 0-100 scale combining:
      - token discipline (distance from 120K / 250K thresholds)
      - cache hit rate (cache_read vs. rebuild tokens)
      - tool-error rate (is_error / total tool_calls)
      - session length (turns + wall-clock duration)
      - correction cycles (repeat-adjacent user prompts, "try again" patterns)
  * Expose `score(session_id)` for single-session lookup and aggregate helpers
    (`average_score`, `score_trend`, `distribution`) for Overview and the
    Session Discipline tab's trend chart + habit tracker.
  * Colour-map to the design system: 80-100 green, 50-79 yellow, 0-49 red.

Implementation lands in Spec 4.  See CLAUDE.md § New modules.
"""
from __future__ import annotations
