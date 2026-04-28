import http.server
import json
import os
import socket
import sqlite3
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from token_dashboard.db import init_db
from token_dashboard.server import build_handler


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens, prompt_text, prompt_chars) VALUES ('u',NULL,'s','p','user','2026-04-19T00:00:00Z',NULL,0,0,0,0,0,'hi',2)")
            c.execute("INSERT INTO messages (uuid, parent_uuid, session_id, project_slug, type, timestamp, model, input_tokens, output_tokens, cache_read_tokens, cache_create_5m_tokens, cache_create_1h_tokens) VALUES ('a','u','s','p','assistant','2026-04-19T00:00:01Z','claude-haiku-4-5',1,1,0,0,0)")
            c.commit()
        self.port = _free_port()
        H = build_handler(self.db, projects_dir="/nonexistent")
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def _get(self, path):
        return urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}").read()

    def test_index_html(self):
        body = self._get("/")
        self.assertIn(b"Token Intelligence", body)

    def test_overview_json(self):
        body = json.loads(self._get("/api/overview"))
        self.assertIn("sessions", body)
        self.assertEqual(body["sessions"], 1)

    def test_prompts_json(self):
        body = json.loads(self._get("/api/prompts?limit=10"))
        self.assertIsInstance(body, list)

    def test_projects_json(self):
        body = json.loads(self._get("/api/projects"))
        self.assertIsInstance(body, list)
        self.assertEqual(body[0]["project_slug"], "p")

    def test_plan_json(self):
        body = json.loads(self._get("/api/plan"))
        self.assertIn("plan", body)
        self.assertIn("pricing", body)

    def test_head_returns_200_not_501(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/", method="HEAD")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"")

    def test_head_api_endpoint(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/overview", method="HEAD")
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"")

    def test_ai_status(self):
        body = json.loads(self._get("/api/ai/status"))
        self.assertIn("configured", body)
        self.assertIsInstance(body["configured"], bool)


class NoScanFlagTests(unittest.TestCase):
    """`run(no_scan=True)` must skip starting the background `_scan_loop`
    thread. Regression: previously the loop ran unconditionally, causing a
    second scanner to compete with the daemon dashboard against the same
    `~/.claude/token-dashboard.db` whenever a verify-only server was launched
    alongside it (discovered during AI Recos Phase 1 verification)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)

    def _run_with_stubbed_server(self, no_scan: bool):
        """Invoke `run(...)` but capture which threads it tried to start.

        We patch `ThreadingHTTPServer.serve_forever` to return immediately so
        `run` doesn't block, and patch `threading.Thread` to record calls
        targeting `_scan_loop` without actually starting that loop.
        """
        from token_dashboard import server as server_module

        scan_thread_started: list = []
        real_thread_cls = threading.Thread

        def fake_thread(*args, **kwargs):
            target = kwargs.get("target") or (args[0] if args else None)
            if target is server_module._scan_loop:
                scan_thread_started.append(target)
                return real_thread_cls(target=lambda: None, daemon=True)
            return real_thread_cls(*args, **kwargs)

        with patch.object(server_module.threading, "Thread", side_effect=fake_thread), \
             patch.object(server_module.http.server, "ThreadingHTTPServer") as MockHTTPD:
            MockHTTPD.return_value.serve_forever.return_value = None
            server_module.run(
                "127.0.0.1", _free_port(), self.db, "/nonexistent",
                no_scan=no_scan,
            )
        return scan_thread_started

    def test_no_scan_flag_prevents_background_scan_thread(self):
        started = self._run_with_stubbed_server(no_scan=True)
        self.assertEqual(
            started, [],
            "`run(no_scan=True)` started the _scan_loop background thread; "
            "should have skipped it.",
        )

    def test_scan_runs_when_no_scan_is_false(self):
        started = self._run_with_stubbed_server(no_scan=False)
        self.assertEqual(
            len(started), 1,
            "`run(no_scan=False)` did not start the _scan_loop background thread; "
            "default behavior regressed.",
        )


if __name__ == "__main__":
    unittest.main()
