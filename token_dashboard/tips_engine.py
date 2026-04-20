"""Next-generation tips engine (IIIMPACT addition).

Planned responsibilities:
  * Replace / expand the rule set in `tips.py` with a composable registry so new
    detectors plug in without touching the core.
  * Ship additional rules: marathon-session, hot-file, bash-output-bloat,
    long-prompt-bloat, tool-error-thrash.
  * Emit the same tip-record shape as the upstream engine
    (`key`, `category`, `title`, `body`, `scope`) for drop-in UI compatibility.
  * Support severity tagging (`healthy` / `warning` / `critical` / `info`) so
    the Tips tab can render the Token Intelligence design system's severity
    accent bar correctly.

Implementation lands in Spec 3.  See CLAUDE.md § New modules.
"""
from __future__ import annotations
