"""Dismissal endpoints + read-side filter for /api/tips.

Locks in the contract for `feat/recos-dismissal`:
  * POST /api/tips/dismiss inserts into dismissed_tips (idempotent)
  * DELETE /api/tips/dismiss removes from dismissed_tips (idempotent)
  * GET /api/tips filters dismissed rows by default
  * GET /api/tips?include_dismissed=1 returns all rows with `dismissed: bool`
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

from token_dashboard.db import init_db, connect
from token_dashboard.server import build_handler
from token_dashboard.tips_engine import recompute_tips


SINCE = "2026-04-11T00:00:00Z"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _seed_two_session_tips(db: str) -> None:
    """Seed enough message rows to fire MARATHON_SESSION (critical) on one
    session and CORRECTION_LOOPS (warning) on another."""
    with connect(db) as c:
        # Marathon — 35 turns, 280K tokens (critical)
        for i in range(35):
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, "
                "timestamp, input_tokens, output_tokens) "
                "VALUES (?, 'aaa', 'p', 'user', ?, 8000, 0)",
                (f"m{i}", f"2026-04-22T14:{32 + i // 60:02d}:{i % 60:02d}Z"),
            )
        # Correction Loops — 3 corrections (warning)
        for i, txt in enumerate(["please try again", "that's wrong", "not what i meant"]):
            c.execute(
                "INSERT INTO messages (uuid, session_id, project_slug, type, "
                "timestamp, prompt_text) VALUES (?, 'bbb', 'p', 'user', ?, ?)",
                (f"c{i}", f"2026-04-23T10:{i:02d}:00Z", txt),
            )
        c.commit()
    recompute_tips(db, SINCE)


class DismissalEndpointTests(unittest.TestCase):
    """POST /api/tips/dismiss, DELETE /api/tips/dismiss, and the read-side
    filter on GET /api/tips."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        init_db(self.db)
        _seed_two_session_tips(self.db)
        self.port = _free_port()
        H = build_handler(self.db, projects_dir="/nonexistent")
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.httpd.shutdown()

    def _request(self, method: str, path: str, body=None) -> tuple[int, dict]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            payload = e.read()
            return e.code, json.loads(payload) if payload else {}

    def _tips(self, include_dismissed: bool = False) -> list[dict]:
        path = "/api/tips" + ("?include_dismissed=1" if include_dismissed else "")
        status, body = self._request("GET", path)
        self.assertEqual(status, 200)
        return body

    def _row_count(self, key: str | None = None) -> int:
        with connect(self.db) as c:
            if key is None:
                return c.execute("SELECT COUNT(*) AS n FROM dismissed_tips").fetchone()["n"]
            return c.execute(
                "SELECT COUNT(*) AS n FROM dismissed_tips WHERE tip_key = ?", (key,)
            ).fetchone()["n"]

    # ------------------------------------------------------------------
    # 1. POST with valid key → 200, row in dismissed_tips
    # ------------------------------------------------------------------
    def test_post_dismiss_inserts_row(self):
        status, body = self._request(
            "POST", "/api/tips/dismiss", {"key": "MARATHON_SESSION:aaa"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {})
        self.assertEqual(self._row_count("MARATHON_SESSION:aaa"), 1)
        # dismissed_at is unix epoch seconds (REAL via strftime('%s','now'))
        with connect(self.db) as c:
            row = c.execute(
                "SELECT tip_key, dismissed_at FROM dismissed_tips WHERE tip_key = ?",
                ("MARATHON_SESSION:aaa",),
            ).fetchone()
        self.assertEqual(row["tip_key"], "MARATHON_SESSION:aaa")
        self.assertGreater(float(row["dismissed_at"]), 1_700_000_000)

    # ------------------------------------------------------------------
    # 2. POST with missing/malformed key → 400
    # ------------------------------------------------------------------
    def test_post_dismiss_rejects_missing_key(self):
        for body in ({}, {"key": ""}, {"key": "   "}, {"key": None}, {"not_key": "x"}):
            status, payload = self._request("POST", "/api/tips/dismiss", body)
            self.assertEqual(status, 400, f"body={body} should 400 (got {payload})")
            self.assertIn("missing key", payload.get("error", ""))
        self.assertEqual(self._row_count(), 0)

    # ------------------------------------------------------------------
    # 3. POST already-dismissed key → 200 (idempotent)
    # ------------------------------------------------------------------
    def test_post_dismiss_is_idempotent(self):
        status1, _ = self._request("POST", "/api/tips/dismiss", {"key": "X:1"})
        status2, _ = self._request("POST", "/api/tips/dismiss", {"key": "X:1"})
        self.assertEqual(status1, 200)
        self.assertEqual(status2, 200)
        self.assertEqual(self._row_count("X:1"), 1)

    # ------------------------------------------------------------------
    # 4. DELETE with valid key → 200, row removed
    # ------------------------------------------------------------------
    def test_delete_dismiss_removes_row(self):
        self._request("POST", "/api/tips/dismiss", {"key": "MARATHON_SESSION:aaa"})
        self.assertEqual(self._row_count("MARATHON_SESSION:aaa"), 1)
        status, body = self._request(
            "DELETE", "/api/tips/dismiss", {"key": "MARATHON_SESSION:aaa"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {})
        self.assertEqual(self._row_count("MARATHON_SESSION:aaa"), 0)

    # ------------------------------------------------------------------
    # 5. DELETE not-dismissed key → 200 (idempotent no-op)
    # ------------------------------------------------------------------
    def test_delete_dismiss_is_idempotent(self):
        status, body = self._request(
            "DELETE", "/api/tips/dismiss", {"key": "NOT_THERE:zzz"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, {})
        self.assertEqual(self._row_count(), 0)

    # ------------------------------------------------------------------
    # 6. GET /api/tips after dismiss → dismissed tip not in response
    # ------------------------------------------------------------------
    def test_get_tips_filters_dismissed_by_default(self):
        before = self._tips()
        keys_before = {t["key"] for t in before}
        self.assertIn("MARATHON_SESSION:aaa", keys_before)

        self._request("POST", "/api/tips/dismiss", {"key": "MARATHON_SESSION:aaa"})

        after = self._tips()
        keys_after = {t["key"] for t in after}
        self.assertNotIn("MARATHON_SESSION:aaa", keys_after)
        # other tips still there
        self.assertEqual(len(keys_after), len(keys_before) - 1)
        # the dismissed flag is False on every visible tip
        for t in after:
            self.assertFalse(
                t.get("dismissed", False),
                f"{t['key']} leaked into default view with dismissed=true",
            )

    # ------------------------------------------------------------------
    # 7. GET /api/tips?include_dismissed=1 → dismissed tip present + flag
    # ------------------------------------------------------------------
    def test_get_tips_include_dismissed_flag(self):
        self._request("POST", "/api/tips/dismiss", {"key": "MARATHON_SESSION:aaa"})

        rows = self._tips(include_dismissed=True)
        marathon = next(r for r in rows if r["key"] == "MARATHON_SESSION:aaa")
        self.assertTrue(marathon["dismissed"])

        # Non-dismissed tips still carry dismissed=false
        other = next(r for r in rows if r["key"] != "MARATHON_SESSION:aaa")
        self.assertFalse(other["dismissed"])


if __name__ == "__main__":
    unittest.main()
