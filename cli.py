"""Token Dashboard CLI entrypoint."""
from __future__ import annotations

import argparse
import os
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

from token_dashboard.db import init_db, default_db_path, overview_totals
from token_dashboard.scanner import scan_dir
from token_dashboard.tips_engine import all_tips


def _db_path(args) -> str:
    return args.db or os.environ.get("TOKEN_DASHBOARD_DB") or str(default_db_path())


def _projects(args) -> str:
    return (
        args.projects_dir
        or os.environ.get("CLAUDE_PROJECTS_DIR")
        or str(Path.home() / ".claude" / "projects")
    )


def _today_range():
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc).isoformat()
    end = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return start, end


def cmd_scan(args):
    db = _db_path(args)
    init_db(db)
    from token_dashboard.scanner import scan_and_recompute
    n = scan_and_recompute(_projects(args), db)
    print(f"Token Dashboard: scanned {n['files']} files, {n['messages']} messages, {n['tools']} tool calls")
    if n["messages"] > 0:
        # Wrapper just ran recompute — read fresh counts from the tables.
        import sqlite3
        with sqlite3.connect(db) as c:
            hn = c.execute("SELECT COUNT(*) FROM session_health").fetchone()[0]
            tn = c.execute("SELECT COUNT(*) FROM tips").fetchone()[0]
        print(f"  recomputed {hn} session health rows, {tn} tips")
    else:
        print("  no new messages — recompute skipped")


def cmd_today(args):
    db = _db_path(args)
    init_db(db)
    s, e = _today_range()
    t = overview_totals(db, since=s, until=e)
    print("Token Dashboard — today")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")
    print(f"  cache rd: {t['cache_read_tokens']:>12,}    cache cr: {t['cache_create_5m_tokens']+t['cache_create_1h_tokens']:>12,}")


def cmd_stats(args):
    db = _db_path(args)
    init_db(db)
    t = overview_totals(db)
    print("Token Dashboard — all time")
    print(f"  sessions: {t['sessions']}    turns: {t['turns']}")
    print(f"  input:    {t['input_tokens']:>12,}    output: {t['output_tokens']:>12,}")


def cmd_tips(args):
    db = _db_path(args)
    init_db(db)
    tips = all_tips(db)
    if not tips:
        print("Token Intelligence: no recommendations")
        return
    for tip in tips:
        savings = tip.get("estimated_savings") or 0
        header = f"[{tip['severity']}] {tip['rule_id']}"
        if savings:
            header += f"  (~{savings:,} saved)"
        print(header)
        print(f"  {tip['message']}\n")


def cmd_dashboard(args):
    db = _db_path(args)
    init_db(db)
    if not args.no_scan:
        # Unconditional recompute on boot — `scan_and_recompute` would skip
        # when nothing new is ingested, but a DB that was already fully
        # populated by a previous run still needs tips/health computed
        # if they're stale or missing. One ~0.2s pass at boot guarantees
        # the dashboard never opens to an empty AI Recos tab.
        scan_dir(_projects(args), db)
        from token_dashboard.health_score import recompute_health
        from token_dashboard.tips_engine import recompute_tips
        recompute_health(db)
        recompute_tips(db)
    from token_dashboard.server import run

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8080"))
    url = f"http://{host}:{port}/"
    if not args.no_open:
        webbrowser.open(url)
    print(f"Token Dashboard listening on {url}")
    run(host, port, db, _projects(args), no_scan=args.no_scan)


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="SQLite path (default ~/.claude/token-dashboard.db)")
    common.add_argument("--projects-dir", help="JSONL root (default ~/.claude/projects)")

    p = argparse.ArgumentParser(prog="token-dashboard", description="Local Claude Code usage dashboard", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan",  parents=[common]).set_defaults(func=cmd_scan)
    sub.add_parser("today", parents=[common]).set_defaults(func=cmd_today)
    sub.add_parser("stats", parents=[common]).set_defaults(func=cmd_stats)
    sub.add_parser("tips",  parents=[common]).set_defaults(func=cmd_tips)
    d = sub.add_parser("dashboard", parents=[common])
    d.add_argument(
        "--no-scan", action="store_true",
        help="skip the startup scan AND the background re-scan loop "
             "(useful when running a verify-only server alongside the main daemon)",
    )
    d.add_argument("--no-open", action="store_true")
    d.set_defaults(func=cmd_dashboard)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
