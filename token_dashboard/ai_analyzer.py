"""AI-powered usage analysis (IIIMPACT addition).

Planned responsibilities:
  * Opt-in, off-by-default.  Settings tab controls whether this runs at all.
  * Pass AGGREGATE metrics only (not raw prompt_text) to an LLM so we preserve
    the "nothing leaves your machine without explicit opt-in" property.
  * Produce higher-level recommendations than the rule-based tips engine —
    cross-project patterns, week-over-week regressions, model-mix advice.
  * Cache results in SQLite so we don't re-call on every dashboard load; only
    re-run when underlying metrics have materially changed or on manual
    "re-analyze" from the UI.
  * Clearly label AI-sourced suggestions in the Tips tab with the #8F00FF
    purple accent so users can distinguish them from deterministic rules.

Implementation lands in Spec 5.  See CLAUDE.md § New modules.
"""
from __future__ import annotations
