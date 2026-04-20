import json
import os
import tempfile
import unittest
from unittest import mock

from token_dashboard.db import init_db, connect
from token_dashboard import ai_analyzer


def _seed_messages(db_path):
    with connect(db_path) as c:
        c.execute(
            "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
            "input_tokens, output_tokens, prompt_text, prompt_chars) "
            "VALUES ('u1','S','p','user','2026-04-18T00:00:00Z', 100, 0, "
            "'SECRET-PROMPT-STRING please help me', 35)"
        )
        c.execute(
            "INSERT INTO messages (uuid, session_id, project_slug, type, timestamp, "
            "input_tokens, output_tokens, model, tool_calls_json) "
            "VALUES ('a1','S','p','assistant','2026-04-18T00:00:01Z', 500, 200, "
            "'claude-opus-4-7','[{\"name\":\"Read\"}]')"
        )
        c.execute(
            "INSERT INTO tool_calls (message_uuid, session_id, project_slug, tool_name, target, "
            "result_tokens, timestamp, is_error) "
            "VALUES ('a1','S','p','Read','foo.md', 400, '2026-04-18T00:00:01Z', 0)"
        )
        c.commit()


def _mock_api_body():
    content = json.dumps({
        "summary": "Aggregate OK.",
        "recommendations": ["Use plan mode more."],
    })
    payload = {"content": [{"type": "text", "text": content}]}
    m = mock.MagicMock()
    m.read.return_value = json.dumps(payload).encode()
    m.__enter__ = lambda s: s
    m.__exit__ = lambda *a: False
    return m


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        _seed_messages(self.db)

    def test_aggregate_shape(self):
        stats = ai_analyzer._aggregate_usage_stats(self.db)
        for k in (
            "session_count", "total_turns", "avg_turns_per_session",
            "avg_tokens_per_session", "cache_hit_rate", "top_tools",
            "model_distribution", "daily_token_trend",
        ):
            self.assertIn(k, stats)
        self.assertEqual(stats["session_count"], 1)

    def test_aggregate_strips_prompt_text(self):
        stats = ai_analyzer._aggregate_usage_stats(self.db)
        serialized = json.dumps(stats)
        self.assertNotIn("SECRET-PROMPT-STRING", serialized)


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def test_missing_key_raises(self):
        ai_analyzer._LAST_CALL_AT = 0.0
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ai_analyzer.ApiKeyMissingError) as cm:
                ai_analyzer.analyze_usage(self.db)
        self.assertEqual(str(cm.exception), "Configure API key in Settings")


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        _seed_messages(self.db)
        ai_analyzer._LAST_CALL_AT = 0.0

    def test_second_call_within_24h_returns_cached(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with mock.patch("urllib.request.urlopen", return_value=_mock_api_body()) as m:
                first = ai_analyzer.analyze_usage(self.db)
                self.assertEqual(m.call_count, 1)
                self.assertFalse(first["cached"])
            with mock.patch("urllib.request.urlopen", return_value=_mock_api_body()) as m2:
                second = ai_analyzer.analyze_usage(self.db)
                self.assertEqual(m2.call_count, 0)
                self.assertTrue(second["cached"])

    def test_force_bypasses_cache(self):
        ai_analyzer._LAST_CALL_AT = 0.0
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with mock.patch("urllib.request.urlopen", return_value=_mock_api_body()):
                ai_analyzer.analyze_usage(self.db)
            ai_analyzer._LAST_CALL_AT = 0.0
            with mock.patch("urllib.request.urlopen", return_value=_mock_api_body()) as m:
                result = ai_analyzer.analyze_usage(self.db, force=True)
                self.assertEqual(m.call_count, 1)
                self.assertFalse(result["cached"])


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        _seed_messages(self.db)
        ai_analyzer._LAST_CALL_AT = 0.0

    def test_rapid_calls_trip_rate_limit(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with mock.patch("urllib.request.urlopen", return_value=_mock_api_body()):
                ai_analyzer.analyze_usage(self.db, force=True)
            with mock.patch("urllib.request.urlopen", return_value=_mock_api_body()):
                with self.assertRaises(ai_analyzer.RateLimitError):
                    ai_analyzer.analyze_usage(self.db, force=True)


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        _seed_messages(self.db)
        ai_analyzer._LAST_CALL_AT = 0.0

    def test_request_body_contains_no_prompt_text(self):
        captured = {}

        def fake_urlopen(req, *a, **kw):
            captured["body"] = req.data.decode("utf-8")
            return _mock_api_body()

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
                ai_analyzer.analyze_usage(self.db, force=True)
        self.assertIn("body", captured)
        self.assertNotIn("SECRET-PROMPT-STRING", captured["body"])


class SessionAutopsyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        _seed_messages(self.db)
        ai_analyzer._LAST_CALL_AT = 0.0

    def test_session_autopsy_shape(self):
        session_content = json.dumps({
            "narrative": "Session ran long.",
            "off_track_turns": [5],
            "wasteful_turns": [],
            "optimal_handoff_turn": 10,
            "estimated_savings_tokens": 4000,
        })
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps({
            "content": [{"type": "text", "text": session_content}],
        }).encode()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda *a: False
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}):
            with mock.patch("urllib.request.urlopen", return_value=resp):
                result = ai_analyzer.analyze_session(self.db, "S", force=True)
        self.assertEqual(result["optimal_handoff_turn"], 10)
        self.assertEqual(result["estimated_savings_tokens"], 4000)


if __name__ == "__main__":
    unittest.main()
