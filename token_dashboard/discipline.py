"""Session discipline integration (IIIMPACT addition).

Planned responsibilities:
  * Mirror the contract of the `session-discipline` skill so the CLI / dashboard
    can surface the same alerts the skill emits in-chat.
  * Detect threshold crossings (60K / 120K / 180K / 250K) on live or historical
    sessions and attach severity to the session health record.
  * Handoff protocol: build the structured summary the skill produces
    (mission, decisions, shipped, files, state, pick-up-here) from the session
    transcript so users have a copy-paste handoff ready.
  * Sub-agent delegation triggers: scan session turns for research / code-review
    / bulk-file-op patterns and surface a recommendation to delegate.
  * Anti-pattern detection: marathon sessions, repeated corrections,
    context-heavy debugging, "just one more thing" task creep.

Implementation lands in Spec 6.  See CLAUDE.md § New modules.
"""
from __future__ import annotations
