"""Regression tests for fix/tips-compute-on-fresh-db.

Locks in the fresh-DB → ingest → recompute chain that previously left
the dashboard's AI Recos tab empty. Three tests:

  1. Boot path against an empty DB seeded only with JSONL files: tips
     populate, MARATHON_SESSION fires, structured fields are present.
  2. `scan_and_recompute` is a no-op when nothing new ingested.
  3. POST /api/tips/recompute manual-trigger endpoint works end-to-end.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.request

from token_dashboard.db import init_db, connect
from token_dashboard.scanner import scan_dir, scan_and_recompute
from token_dashboard.health_score import recompute_health
from token_dashboard.tips_engine import recompute_tips
from token_dashboard.server import build_handler


# Recent enough that the default `recompute_tips` since-window (7 days) will
# pick it up. Today is 2026-04-29; this is yesterday-ish.
FIXTURE_TS_BASE = "2026-04-28T18:00"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _stage_marathon_jsonl(projects_root: str, *, session_id: str = "marathon-canary",
                          turns: int = 36) -> str:
    """Write a single project directory containing one JSONL session that is
    unambiguously over both MARATHON_SESSION thresholds (turns > 30 AND
    tokens > 120K). Returns the JSONL path.
    """
    proj_dir = os.path.join(projects_root, "C--work-canary")
    os.makedirs(proj_dir, exist_ok=True)
    jsonl_path = os.path.join(proj_dir, f"{session_id}.jsonl")
    lines = []
    for i in range(turns):
        # Two records per turn: one user, one assistant (carries usage tokens).
        ts_user = f"{FIXTURE_TS_BASE}:{i:02d}Z"
        ts_assist = f"{FIXTURE_TS_BASE}:{i:02d}Z"  # same minute is fine
        lines.append(json.dumps({
            "type": "user",
            "uuid": f"u{i}",
            "sessionId": session_id,
            "timestamp": ts_user,
            "isSidechain": False,
            "cwd": "/c/work/canary",
            "gitBranch": "main",
            "version": "2.1.98",
            "entrypoint": "cli",
            "message": {"role": "user", "content": "make it work"},
        }))
        lines.append(json.dumps({
            "type": "assistant",
            "uuid": f"a{i}",
            "parentUuid": f"u{i}",
            "sessionId": session_id,
            "timestamp": ts_assist,
            "isSidechain": False,
            "message": {
                "model": "claude-sonnet-4-5",
                "content": [{"type": "text", "text": "ok"}],
                # 4_000 tokens × 36 turns = 144_000 — clears the 120K bar.
                "usage": {"input_tokens": 2000, "output_tokens": 2000},
            },
        }))
    with open(jsonl_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return jsonl_path


class FreshDbBootPopulatesTips(unittest.TestCase):
    """The canary. Empty DB + JSONL fixture → boot path → tips populated.

    Mirrors what `cmd_dashboard` does on the post-fix branch:
      init_db(db)
      scan_dir(projects, db)
      recompute_health(db)
      recompute_tips(db)
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "fresh.db")
        self.projects = os.path.join(self.tmp, "projects")
        os.makedirs(self.projects)
        init_db(self.db)
        _stage_marathon_jsonl(self.projects)

    def test_tips_populate_after_boot(self):
        # Sanity: DB starts empty.
        with connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM tips").fetchone()[0], 0)

        # Exact sequence cmd_dashboard executes post-fix.
        n = scan_dir(self.projects, self.db)
        self.assertGreater(n["messages"], 0, "scanner should have ingested fixture")
        recompute_health(self.db)
        recompute_tips(self.db)

        # Tips populated, MARATHON_SESSION fires, structured fields land.
        with connect(self.db) as c:
            tips_count = c.execute("SELECT COUNT(*) FROM tips").fetchone()[0]
            self.assertGreaterEqual(tips_count, 1, "tips should have populated")

            marathon = c.execute(
                "SELECT rule_id, severity, session_id, title, where_text, "
                "what_text, how_to_fix, occurred_at "
                "FROM tips WHERE rule_id = 'MARATHON_SESSION'"
            ).fetchone()
            self.assertIsNotNone(
                marathon,
                "MARATHON_SESSION should fire on a 36-turn / 144K-token session",
            )
            # Structured fields all present (Phase 1 contract).
            self.assertEqual(marathon["session_id"], "marathon-canary")
            self.assertTrue(marathon["title"], "title must be populated")
            self.assertTrue(marathon["where_text"], "where_text must be populated")
            self.assertTrue(marathon["what_text"], "what_text must be populated")
            self.assertTrue(marathon["how_to_fix"], "how_to_fix must be populated")
            self.assertIsInstance(marathon["occurred_at"], float)
            self.assertGreater(marathon["occurred_at"], 0)


class ScanAndRecomputeIdempotent(unittest.TestCase):
    """When `scan_and_recompute` is called and no new JSONL bytes exist
    to ingest, it must NOT truncate-and-rewrite the tips table. Avoids
    needless recompute churn on every 30s loop tick."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "warm.db")
        self.projects = os.path.join(self.tmp, "projects")
        os.makedirs(self.projects)
        init_db(self.db)
        _stage_marathon_jsonl(self.projects)
        # Prime: ingest + compute once.
        n1 = scan_and_recompute(self.projects, self.db)
        self.assertGreater(n1["messages"], 0, "primer scan must have ingested")

    def test_second_call_skips_recompute(self):
        # Capture the canonical fingerprint of the tips snapshot. recompute_tips
        # writes a single created_at across all rows in one transaction, so
        # any re-run would produce a strictly later timestamp.
        with connect(self.db) as c:
            before = c.execute("SELECT COUNT(*) AS n, MAX(created_at) AS ts FROM tips").fetchone()
            self.assertGreater(before["n"], 0, "primer should have populated tips")

        # Sleep enough that any real recompute would be detectable.
        time.sleep(0.05)

        n2 = scan_and_recompute(self.projects, self.db)
        self.assertEqual(
            n2["messages"], 0,
            "no new JSONL bytes — scanner should ingest 0 messages on second call",
        )

        with connect(self.db) as c:
            after = c.execute("SELECT COUNT(*) AS n, MAX(created_at) AS ts FROM tips").fetchone()
        self.assertEqual(after["n"], before["n"], "tips count should be unchanged")
        self.assertEqual(
            after["ts"], before["ts"],
            "tips.created_at should be unchanged — recompute must not have re-run",
        )


class ApiTipsRecomputeEndpoint(unittest.TestCase):
    """`POST /api/tips/recompute` is the manual-trigger surface for support
    and screenshot recapture. Should run unconditionally and return the
    fresh row count."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "api.db")
        self.projects = os.path.join(self.tmp, "projects")
        os.makedirs(self.projects)
        init_db(self.db)
        _stage_marathon_jsonl(self.projects)

        # Simulate the bug state: messages ingested, tips empty.
        scan_dir(self.projects, self.db)
        with connect(self.db) as c:
            self.assertGreater(
                c.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0,
                "fixture should have populated messages",
            )
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM tips").fetchone()[0], 0,
                "tips must be empty before the endpoint fires",
            )

        # Boot the http server.
        self.port = _free_port()
        H = build_handler(self.db, projects_dir=self.projects)
        self.httpd = http.server.HTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()

    def test_endpoint_recomputes_and_returns_count(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/tips/recompute",
            data=b"{}", method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 200)
            payload = json.loads(r.read())

        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(
            payload["count"], 1,
            "marathon-grade fixture should yield at least one tip",
        )

        with connect(self.db) as c:
            db_count = c.execute("SELECT COUNT(*) FROM tips").fetchone()[0]
        self.assertEqual(
            db_count, payload["count"],
            "endpoint count must match the actual tips row count",
        )


if __name__ == "__main__":
    unittest.main()
