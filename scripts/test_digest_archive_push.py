#!/usr/bin/env python3
"""Offline test for run_digest_archive's push recovery (temp bare repo; touches nothing real).

Why (2026-09-25): a failed push said "will push next time", but a next run with no new changes returned
without pushing (09-24 20:20 push failed, 22:42 'no changes'). ahead() now drives a catch-up push, and
push() retries once after pull --rebase --autostash.

Usage: python scripts/test_digest_archive_push.py   (exit 0 = pass, 1 = fail)
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_digest_archive as rda  # noqa: E402

FAILS = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILS.append(name)


def sh(repo, *a):
    return subprocess.run(["git", "-C", repo, *a], capture_output=True, text=True, check=True).stdout


def commit(repo, name):
    open(os.path.join(repo, name), "w").write(name)
    sh(repo, "add", name)
    sh(repo, "-c", "user.name=Klay", "-c", "user.email=klaywang24@gmail.com", "commit", "-qm", name)


tmp = tempfile.mkdtemp()
try:
    origin, a, b = (os.path.join(tmp, x) for x in ("o.git", "a", "b"))
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", origin], check=True)
    subprocess.run(["git", "clone", "-q", origin, a], check=True, capture_output=True)
    commit(a, "base")
    sh(a, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-q", origin, b], check=True, capture_output=True)
    rda.REPO = a
    commit(a, "unpushed")
    check("a local commit that was never pushed is seen (ahead == 1)", rda.ahead() == 1)
    commit(b, "someone-else")
    sh(b, "push", "-q", "origin", "HEAD:main")
    open(os.path.join(a, "noise"), "w").write("dirty")          # unstaged noise must not veto recovery
    ok, _ = rda.push()
    log = sh(origin, "log", "--format=%s", "main")
    check("rejected push recovers via pull --rebase --autostash", ok and "unpushed" in log and "someone-else" in log)
    check("ahead back to 0 after the catch-up push", rda.ahead() == 0)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("✅ all passed · 闸对坏输入正确报红" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
