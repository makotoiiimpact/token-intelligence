import os
import tempfile
import unittest

from token_dashboard.db import init_db, connect
from token_dashboard.health_score import (
    score_session, session_breakdown, recompute_health, discipline_aggregate,
)


def _ts(i: int) -> str:
    return f"2026-04-18T00:00:{i:02d}Z"


class HealthyScoresHigh(unittest.TestCase):
    def test_short_session_scores_100(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            for i in range(3):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                    "input_tokens, output_tokens) VALUES (?, 'S','p','assistant',?, 1000, 1000)",
                    (f"a{i}", _ts(i)),
                )
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, prompt_text) "
                    "VALUES (?, 'S','p','user',?, 'add a feature')",
                    (f"u{i}", _ts(i + 10)),
                )
            c.commit()
        score = score_session(db, "S")
        self.assertEqual(score, 100)


class MarathonScoresLow(unittest.TestCase):
    def test_over_250k_loses_50(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "input_tokens, output_tokens) VALUES ('a0','S','p','assistant',?, 100000, 200000)",
                (_ts(0),),
            )
            c.commit()
        breakdown = session_breakdown(db, "S")
        self.assertEqual(breakdown["components"]["token_penalty"], -50)
        self.assertEqual(breakdown["score"], 50)


class CorrectionHeavyLosesPoints(unittest.TestCase):
    def test_three_corrections_penalize_15(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            for i, text in enumerate(["try again", "that's wrong", "not quite right"]):
                c.execute(
                    "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, prompt_text) "
                    "VALUES (?, 'S','p','user',?,?)",
                    (f"u{i}", _ts(i), text),
                )
            c.commit()
        breakdown = session_breakdown(db, "S")
        self.assertEqual(breakdown["components"]["correction_penalty"], -15)


class CacheRichGetsBonus(unittest.TestCase):
    def test_high_hit_rate_adds_bonus(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "input_tokens, output_tokens, cache_read_tokens) "
                "VALUES ('a0','S','p','assistant',?, 1000, 500, 100000)",
                (_ts(0),),
            )
            c.commit()
        breakdown = session_breakdown(db, "S")
        self.assertGreater(breakdown["components"]["cache_bonus"], 0)


class RecomputeTests(unittest.TestCase):
    def test_recompute_writes_and_aggregate(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        with connect(db) as c:
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
                "input_tokens, output_tokens) VALUES ('a0','S','p','assistant',?, 1000, 500)",
                (_ts(0),),
            )
            c.commit()
        n = recompute_health(db)
        self.assertEqual(n, 1)
        agg = discipline_aggregate(db)
        self.assertEqual(agg["total_sessions"], 1)
        self.assertEqual(agg["threshold_counts"]["120k"], 0)


class EmptyAggregate(unittest.TestCase):
    def test_no_sessions_returns_zero(self):
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "t.db")
        init_db(db)
        agg = discipline_aggregate(db)
        self.assertEqual(agg["total_sessions"], 0)


if __name__ == "__main__":
    unittest.main()
