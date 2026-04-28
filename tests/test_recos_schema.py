"""AI Recos Phase 1 — structured-output additive schema.

Locks in:
  * each rule emits the new fields (title, where, what, how_to_fix,
    occurred_at, deep_link) without changing the legacy fields
    (rule_id, severity, session_id, message, estimated_savings, key)
  * recompute_tips() persists the new columns to the `tips` table
  * /api/tips returns "where" / "what" (not where_text/what_text) and
    serializes occurred_at as an ISO string
  * the schema migration is idempotent and adds columns to a legacy table
"""
import http.server
import json
import os
import socket
import sqlite3
import tempfile
import threading
import unittest
import urllib.request

from token_dashboard.db import (
    init_db, connect, _migrate_add_recos_columns,
)
from token_dashboard.server import build_handler
from token_dashboard.tips_engine import (
    marathon_session, correction_loops, redundant_reads, file_bloat,
    large_paste, expensive_tool, cache_miss_streak,
    task_drift, output_heavy_session, vague_prompt,
    multi_task_prompt, no_plan_mode, all_tips, recompute_tips,
    TODO_PRESCRIPTION,
)


SINCE = "2026-04-11T00:00:00Z"

LEGACY_FIELDS = {
    "rule_id", "severity", "session_id", "message", "estimated_savings", "key",
}
STRUCTURED_FIELDS = {
    "title", "where", "what", "how_to_fix", "occurred_at", "deep_link",
}
EDITORIAL_RULES = {
    "TASK_DRIFT", "OUTPUT_HEAVY_SESSION",
    "VAGUE_PROMPT", "MULTI_TASK_PROMPT", "NO_PLAN_MODE",
}


def _ts(offset: int = 0) -> str:
    return f"2026-04-18T00:00:{offset:02d}Z"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed_marathon(db_path: str, session_id: str = "abc123",
                   first_ts: str = "2026-04-18T00:00:00Z",
                   turns: int = 35) -> None:
    with connect(db_path) as c:
        for i in range(turns):
            ts = first_ts if i == 0 else _ts(i)
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                "VALUES (?, ?, 'p', 'user', ?)",
                (f"u{i}", session_id, ts),
            )
        c.commit()


class MarathonStructuredFields(unittest.TestCase):
    """Mechanical-split rule: every structured field has real content."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        _seed_marathon(self.db)

    def test_emits_all_structured_fields(self):
        [tip] = marathon_session(self.db, SINCE)
        for k in LEGACY_FIELDS | STRUCTURED_FIELDS:
            self.assertIn(k, tip, f"missing field: {k}")
        self.assertEqual(tip["rule_id"], "MARATHON_SESSION")
        self.assertEqual(tip["title"], "Marathon session")
        self.assertEqual(tip["how_to_fix"], "Handoff earlier")
        self.assertEqual(tip["what"], "past 120K, retrieval accuracy drops measurably")
        self.assertEqual(tip["deep_link"], "/sessions/abc123")

    def test_where_summarizes_session(self):
        [tip] = marathon_session(self.db, SINCE)
        self.assertIn("abc123", tip["where"])
        self.assertIn("35", tip["where"])

    def test_message_byte_identical_to_pre_migration(self):
        # The pre-Phase-1 message format must be reproduced verbatim, since the
        # frontend still reads tip.message in Phase 1.
        [tip] = marathon_session(self.db, SINCE)
        self.assertEqual(
            tip["message"],
            "Session hit 35 turns / 0 tokens. "
            "Handoff earlier — past 120K, retrieval accuracy drops measurably.",
        )

    def test_occurred_at_is_session_min_timestamp(self):
        [tip] = marathon_session(self.db, SINCE)
        self.assertEqual(tip["occurred_at"], "2026-04-18T00:00:00Z")


class ExpensiveToolGlobalRule(unittest.TestCase):
    """Global rule: occurred_at and deep_link must be None."""

    def test_global_fields_are_none(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                "VALUES ('m1','S','p','user', ?)", (_ts(0),),
            )
            for i in range(4):
                c.execute(
                    "INSERT INTO tool_calls (message_uuid, session_id, project_slug, "
                    "tool_name, target, result_tokens, timestamp, is_error) "
                    "VALUES ('m1','S','p','_tool_result','Glob(**)',80000,?,0)",
                    (_ts(i),),
                )
            c.commit()
        [tip] = expensive_tool(db, SINCE)
        self.assertEqual(tip["rule_id"], "EXPENSIVE_TOOL")
        self.assertIsNone(tip["session_id"])
        self.assertIsNone(tip["occurred_at"])
        self.assertIsNone(tip["deep_link"])
        self.assertEqual(tip["title"], "Expensive tool")
        self.assertIn("Glob(**)", tip["where"])


class EditorialRulesUseTodoPlaceholder(unittest.TestCase):
    """Editorial rules: how_to_fix is the TODO placeholder until copy lands."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def _seed_task_drift(self):
        # 40+ distinct topic tokens in one session
        words = " ".join(f"topic{i:03d}word" for i in range(45))
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, "
                "timestamp, prompt_text) VALUES ('u1','S','p','user',?,?)",
                (_ts(0), words),
            )
            c.commit()

    def _seed_vague_prompt(self):
        with connect(self.db) as c:
            for i in range(4):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, "
                    "timestamp, prompt_chars) VALUES (?,'S','p','user',?,30)",
                    (f"u{i}", _ts(i)),
                )
            c.commit()

    def _seed_multi_task(self):
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, "
                "timestamp, prompt_text) VALUES ('u1','S','p','user',?,?)",
                (_ts(0), "Fix the button. Also update the tests."),
            )
            c.commit()

    def _seed_no_plan(self):
        with connect(self.db) as c:
            for i in range(12):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, "
                    "timestamp, prompt_text) VALUES (?,'S','p','user',?,'just do it')",
                    (f"u{i}", _ts(i)),
                )
            c.commit()

    def _seed_output_heavy(self):
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, "
                "timestamp, input_tokens, output_tokens) "
                "VALUES ('a1','S','p','assistant',?,100,5000)",
                (_ts(0),),
            )
            c.commit()

    def test_task_drift_uses_todo(self):
        self._seed_task_drift()
        [tip] = task_drift(self.db, SINCE)
        self.assertEqual(tip["rule_id"], "TASK_DRIFT")
        self.assertEqual(tip["how_to_fix"], TODO_PRESCRIPTION)
        # editorial: what holds the full diagnosis prose
        self.assertEqual(tip["what"], tip["message"])

    def test_vague_prompt_uses_todo(self):
        self._seed_vague_prompt()
        [tip] = vague_prompt(self.db, SINCE)
        self.assertEqual(tip["how_to_fix"], TODO_PRESCRIPTION)

    def test_multi_task_prompt_uses_todo(self):
        self._seed_multi_task()
        [tip] = multi_task_prompt(self.db, SINCE)
        self.assertEqual(tip["how_to_fix"], TODO_PRESCRIPTION)

    def test_no_plan_mode_uses_todo(self):
        self._seed_no_plan()
        [tip] = no_plan_mode(self.db, SINCE)
        self.assertEqual(tip["how_to_fix"], TODO_PRESCRIPTION)

    def test_output_heavy_uses_todo(self):
        self._seed_output_heavy()
        [tip] = output_heavy_session(self.db, SINCE)
        self.assertEqual(tip["how_to_fix"], TODO_PRESCRIPTION)


class MechanicalSplitMessagesByteIdentical(unittest.TestCase):
    """For the 7 mechanical-split rules, message must remain byte-identical.

    `message` is composed from {what, how_to_fix} (or includes them) at emit
    time; the composition must reproduce the pre-Phase-1 string exactly.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_correction_loops_message(self):
        with connect(self.db) as c:
            for i, text in enumerate([
                "please try again",
                "that's wrong, fix it",
                "not what I meant at all",
            ]):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, "
                    "timestamp, prompt_text) VALUES (?,'S','p','user',?,?)",
                    (f"u{i}", _ts(i), text),
                )
            c.commit()
        [tip] = correction_loops(self.db, SINCE)
        self.assertEqual(
            tip["message"],
            "3 correction message(s) detected. "
            "Rewinding with /re before retrying keeps context clean; "
            "failed attempts otherwise linger.",
        )

    def test_redundant_reads_message(self):
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                "VALUES ('m1','S','p','assistant', ?)", (_ts(0),),
            )
            for i in range(5):
                c.execute(
                    "INSERT INTO tool_calls (message_uuid, session_id, project_slug, "
                    "tool_name, target, timestamp, is_error) "
                    "VALUES ('m1','S','p','Read','src/foo.ts',?,0)",
                    (_ts(i),),
                )
            c.commit()
        [tip] = redundant_reads(self.db, SINCE)
        self.assertEqual(
            tip["message"],
            "`src/foo.ts` was Read 5x in one session. "
            "Summarize in CLAUDE.md or read once per session.",
        )

    def test_file_bloat_message(self):
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                "VALUES ('m1','S','p','user', ?)", (_ts(0),),
            )
            c.execute(
                "INSERT INTO tool_calls (message_uuid, session_id, project_slug, "
                "tool_name, target, result_tokens, timestamp, is_error) "
                "VALUES ('m1','S','p','_tool_result','big.md',50000,?,0)", (_ts(0),),
            )
            c.commit()
        [tip] = file_bloat(self.db, SINCE)
        self.assertEqual(
            tip["message"],
            "Tool result was 50,000 tokens. "
            "Read narrower line ranges or pipe output through head/tail.",
        )

    def test_cache_miss_streak_message(self):
        with connect(self.db) as c:
            for i in range(6):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, "
                    "timestamp, cache_read_tokens) VALUES (?, 'S','p','assistant',?,0)",
                    (f"a{i}", _ts(i)),
                )
            c.commit()
        [tip] = cache_miss_streak(self.db, SINCE)
        self.assertEqual(
            tip["message"],
            "5+ consecutive cache misses. "
            "System prompt may be changing between turns "
            "— review skill loads and CLAUDE.md churn.",
        )


class AllRulesEmitNewFields(unittest.TestCase):
    """all_tips() must populate the new fields for every rule it returns."""

    def test_every_emitted_tip_has_structured_fields(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        _seed_marathon(db)
        tips = all_tips(db, SINCE)
        self.assertTrue(tips)
        for tip in tips:
            for k in STRUCTURED_FIELDS | LEGACY_FIELDS:
                self.assertIn(k, tip, f"{tip['rule_id']} missing {k}")
            self.assertIsInstance(tip["title"], str)
            self.assertIsInstance(tip["how_to_fix"], str)
            if tip["rule_id"] in EDITORIAL_RULES:
                self.assertEqual(tip["how_to_fix"], TODO_PRESCRIPTION)


class RecomputePersistsNewColumns(unittest.TestCase):
    """recompute_tips() must write the new columns into the `tips` table."""

    def test_columns_round_trip(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        _seed_marathon(db)
        n = recompute_tips(db, SINCE)
        self.assertGreater(n, 0)
        with connect(db) as c:
            row = c.execute(
                "SELECT rule_id, title, where_text, what_text, how_to_fix, "
                "occurred_at, deep_link FROM tips "
                "WHERE rule_id = 'MARATHON_SESSION'"
            ).fetchone()
        self.assertEqual(row["title"], "Marathon session")
        self.assertEqual(row["how_to_fix"], "Handoff earlier")
        self.assertIn("Session abc123", row["where_text"])
        self.assertEqual(row["deep_link"], "/sessions/abc123")
        self.assertIsInstance(row["occurred_at"], float)


class MigrationIsIdempotent(unittest.TestCase):
    """Calling _migrate_add_recos_columns twice must not error and must add
    columns to a legacy `tips` table that lacks them."""

    def test_legacy_table_gets_new_columns(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        # Seed a legacy `tips` table that predates Phase 1.
        with sqlite3.connect(db) as c:
            c.execute("""
                CREATE TABLE tips (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  rule_id TEXT NOT NULL,
                  severity TEXT NOT NULL,
                  session_id TEXT,
                  message TEXT NOT NULL,
                  estimated_savings INTEGER NOT NULL DEFAULT 0,
                  created_at REAL NOT NULL
                )
            """)
            c.commit()
            _migrate_add_recos_columns(c)
            cols_after_first = {row[1] for row in c.execute("PRAGMA table_info(tips)")}
            # Idempotent — second call is a no-op.
            _migrate_add_recos_columns(c)
            cols_after_second = {row[1] for row in c.execute("PRAGMA table_info(tips)")}
        self.assertEqual(cols_after_first, cols_after_second)
        for col in (
            "title", "where_text", "what_text", "how_to_fix",
            "occurred_at", "deep_link",
        ):
            self.assertIn(col, cols_after_first)

    def test_returns_silently_when_table_missing(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        with sqlite3.connect(db) as c:
            _migrate_add_recos_columns(c)  # no `tips` table yet — must not raise


class TipsApiSerializesStructuredFields(unittest.TestCase):
    """/api/tips returns where/what (not where_text/what_text) and ISO occurred_at."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        _seed_marathon(self.db)
        recompute_tips(self.db, SINCE)
        self.port = _free_port()
        H = build_handler(self.db, projects_dir="/nonexistent")
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def test_marathon_row_in_api_response(self):
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/api/tips"
        ).read()
        rows = json.loads(body)
        marathon = next(r for r in rows if r["rule_id"] == "MARATHON_SESSION")

        # keys: clean (no _text suffix)
        self.assertIn("where", marathon)
        self.assertIn("what", marathon)
        self.assertNotIn("where_text", marathon)
        self.assertNotIn("what_text", marathon)

        # legacy fields preserved
        self.assertIn("rule_id", marathon)
        self.assertIn("estimated_savings", marathon)
        self.assertIn("message", marathon)
        self.assertIn("key", marathon)

        # structured payload
        self.assertEqual(marathon["title"], "Marathon session")
        self.assertEqual(marathon["how_to_fix"], "Handoff earlier")
        self.assertEqual(marathon["deep_link"], "/sessions/abc123")
        self.assertIsInstance(marathon["occurred_at"], str)
        self.assertTrue(marathon["occurred_at"].startswith("2026-04-18"))


if __name__ == "__main__":
    unittest.main()
