#!/usr/bin/env python3
"""Offline test for notify_discord failure paths (no network, sends nothing).

Why (2026-09-25):
  1. post() had no retry: several alerts in a row could hit Discord's 429 and the HTTPError killed
     every later alert and the daily card. Now 429 waits retry_after and retries.
  2. One alert failing must not stop the next one.
  3. The site HTML gates now run after the data commit, so a failed job no longer means "data not
     updated"; the failure alert must say which case it is (DATA_COMMITTED set by the commit step).

Usage: python scripts/test_notify_discord.py   (exit 0 = pass, 1 = fail)
"""
import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import notify_discord as nd  # noqa: E402

FAILS = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILS.append(name)


calls = []


def fake_urlopen(plan):
    it = iter(plan)

    def _open(req, timeout=20):
        calls.append(json.loads(req.data))
        kind = next(it, "ok")
        if kind == "429":
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many", {}, io.BytesIO(b'{"retry_after": 0.01}'))
        if kind == "500":
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, io.BytesIO(b""))
        return io.BytesIO(b"")
    return _open


calls.clear()
nd.urllib.request.urlopen = fake_urlopen(["429", "ok"])
nd.post("https://x", {"a": 1}, sleep=lambda s: None)
check("429 then ok: retried once and delivered", len(calls) == 2)

calls.clear()
nd.urllib.request.urlopen = fake_urlopen(["500", "ok"])
nd.alert("https://x", "first", "d")
nd.alert("https://x", "second", "d")
check("a failed alert does not stop the next one", [c["embeds"][0]["title"] for c in calls] == ["first", "second"])

for committed, want in (("1", "已更新并提交"), ("", "没有更新")):
    calls.clear()
    nd.urllib.request.urlopen = fake_urlopen([])
    os.environ.update(DISCORD_WEBHOOK_URL="https://x", JOB_STATUS="failure", DATA_COMMITTED=committed)
    try:
        nd.main()
    except SystemExit:
        pass
    check(f"job failed, DATA_COMMITTED={committed!r}: alert says '{want}'",
          calls and want in calls[0]["embeds"][0]["description"])

print("\n" + ("all passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
