import os
import tempfile
import unittest

from token_dashboard.db import init_db, connect
from token_dashboard.tips_engine import (
    marathon_session, correction_loops, redundant_reads, file_bloat,
    large_paste, output_heavy_session, expensive_tool, cache_miss_streak,
    vague_prompt, multi_task_prompt, no_plan_mode, task_drift,
    all_tips, recompute_tips,
)


def _ts(offset: int = 0) -> str:
    return f"2026-04-18T00:00:{offset:02d}Z"


class MarathonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_fires_on_long_session(self):
        with connect(self.db) as c:
            for i in range(35):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                    "VALUES (?, 'S', 'p', 'user', ?)",
                    (f"u{i}", _ts(i)),
                )
            c.commit()
        tips = marathon_session(self.db, "2026-04-11T00:00:00Z")
        self.assertEqual(len(tips), 1)
        self.assertEqual(tips[0]["rule_id"], "MARATHON_SESSION")

    def test_silent_on_healthy_session(self):
        with connect(self.db) as c:
            for i in range(5):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                    "VALUES (?, 'S', 'p', 'user', ?)",
                    (f"u{i}", _ts(i)),
                )
            c.commit()
        self.assertEqual(marathon_session(self.db, "2026-04-11T00:00:00Z"), [])


class CorrectionLoopsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_detects_phrases(self):
        with connect(self.db) as c:
            for i, text in enumerate([
                "please try again",
                "that's wrong, fix it",
                "not what I meant at all",
            ]):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, prompt_text) "
                    "VALUES (?, 'S', 'p', 'user', ?, ?)",
                    (f"u{i}", _ts(i), text),
                )
            c.commit()
        tips = correction_loops(self.db, "2026-04-11T00:00:00Z")
        self.assertEqual(len(tips), 1)
        self.assertEqual(tips[0]["rule_id"], "CORRECTION_LOOPS")
        self.assertEqual(tips[0]["estimated_savings"], 6000)

    def test_silent_on_clean_prompts(self):
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, prompt_text) "
                "VALUES ('u1', 'S', 'p', 'user', ?, 'add a button to the page')",
                (_ts(1),),
            )
            c.commit()
        self.assertEqual(correction_loops(self.db, "2026-04-11T00:00:00Z"), [])


class RedundantReadsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_fires_over_threshold(self):
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                "VALUES ('m1','S','p','assistant', ?)", (_ts(0),),
            )
            for i in range(5):
                c.execute(
                    "INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error) "
                    "VALUES ('m1','S','p','Read','src/foo.ts',?,0)",
                    (_ts(i),),
                )
            c.commit()
        tips = redundant_reads(self.db, "2026-04-11T00:00:00Z")
        self.assertEqual(tips[0]["rule_id"], "REDUNDANT_READS")

    def test_silent_under_threshold(self):
        with connect(self.db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                "VALUES ('m1','S','p','assistant', ?)", (_ts(0),),
            )
            for i in range(2):
                c.execute(
                    "INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, timestamp, is_error) "
                    "VALUES ('m1','S','p','Read','src/foo.ts',?,0)",
                    (_ts(i),),
                )
            c.commit()
        self.assertEqual(redundant_reads(self.db, "2026-04-11T00:00:00Z"), [])


class FileBloatTests(unittest.TestCase):
    def test_fires_on_large_result(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                "VALUES ('m1','S','p','user', ?)", (_ts(0),),
            )
            c.execute(
                "INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, result_tokens, timestamp, is_error) "
                "VALUES ('m1','S','p','_tool_result','big.md',50000,?,0)", (_ts(0),),
            )
            c.commit()
        tips = file_bloat(db, "2026-04-11T00:00:00Z")
        self.assertEqual(tips[0]["rule_id"], "FILE_BLOAT")
        self.assertEqual(tips[0]["estimated_savings"], 30000)


class ExpensiveToolTests(unittest.TestCase):
    def test_fires_on_consistent_big_results(self):
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
                    "INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, result_tokens, timestamp, is_error) "
                    "VALUES ('m1','S','p','_tool_result','Glob(**)',80000,?,0)",
                    (_ts(i),),
                )
            c.commit()
        tips = expensive_tool(db, "2026-04-11T00:00:00Z")
        self.assertEqual(tips[0]["rule_id"], "EXPENSIVE_TOOL")


class CacheMissStreakTests(unittest.TestCase):
    def test_fires_on_five_consecutive_misses(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            for i in range(6):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                    "cache_read_tokens) VALUES (?, 'S','p','assistant',?,0)",
                    (f"a{i}", _ts(i)),
                )
            c.commit()
        tips = cache_miss_streak(db, "2026-04-11T00:00:00Z")
        self.assertEqual(tips[0]["rule_id"], "CACHE_MISS_STREAK")


class MultiTaskPromptTests(unittest.TestCase):
    def test_detects_and_also_pattern(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, prompt_text) "
                "VALUES ('u1','S','p','user',?,?)",
                (_ts(1), "Fix the button. Also update the tests."),
            )
            c.commit()
        tips = multi_task_prompt(db, "2026-04-11T00:00:00Z")
        self.assertEqual(tips[0]["rule_id"], "MULTI_TASK_PROMPT")


class AllTipsAndRecomputeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        with connect(self.db) as c:
            for i in range(35):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp) "
                    "VALUES (?, 'S', 'p', 'user', ?)",
                    (f"u{i}", _ts(i)),
                )
            c.commit()

    def test_all_tips_sorted_and_keyed(self):
        tips = all_tips(self.db, "2026-04-11T00:00:00Z")
        self.assertTrue(tips)
        for t in tips:
            self.assertIn("rule_id", t)
            self.assertIn("severity", t)
            self.assertIn("key", t)
        savings = [t["estimated_savings"] for t in tips]
        self.assertEqual(savings, sorted(savings, reverse=True))

    def test_recompute_writes_table(self):
        n = recompute_tips(self.db, "2026-04-11T00:00:00Z")
        self.assertGreater(n, 0)
        with connect(self.db) as c:
            row = c.execute("SELECT COUNT(*) AS c FROM tips").fetchone()
        self.assertEqual(row["c"], n)


if __name__ == "__main__":
    unittest.main()
