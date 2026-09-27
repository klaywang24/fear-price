#!/usr/bin/env python3
"""Offline test: the gauge page's context cards must not crash when one upstream comes back empty (2026-09-27).

Why: js/app.js rendered the SKEW card with `x.call_skew.value.toFixed(0)`. When Cboe and Yahoo SKEW are both
empty, call_skew (or its value) is null, `.toFixed` throws, and the whole context block of the gauge fails to
render. The real-rate card had the same shape. Fix: numOr / pctOr helpers show a dash for missing numbers.

How: pull the two helpers and the two card templates straight out of js/app.js (so the test follows the real
code) and evaluate them with node against good, null and missing-value inputs. No network, no writes.
Usage: python3 scripts/test_context_null_guard.py   exits 0 when all pass.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = open(os.path.join(ROOT, "js", "app.js"), encoding="utf-8").read()
bad = 0


def chk(name, cond):
    global bad
    bad += (not cond)
    print(("  ✅ " if cond else "  🔴 ") + name)


helpers = re.findall(r"^\s*const (?:numOr|pctOr) = .*;$", src, re.M)
m = re.search(r'(<div class="lc-name">尾部偏斜 SKEW.*?numOr\(x\.real_rate, "value", 2\)}%</span></div>)', src, re.S)
chk("source: both helpers and the SKEW/real-rate card block are present in js/app.js", len(helpers) == 2 and m)
chk("source: no bare `.value.toFixed` left on call_skew / real_rate",
    "x.call_skew.value.toFixed" not in src and "x.real_rate.value.toFixed" not in src)

if len(helpers) == 2 and m:
    cases = {
        "good": {"call_skew": {"value": 141.7, "pctile_full": 83.4}, "real_rate": {"value": 1.82, "pctile_full": 71.2}},
        "skew_null": {"call_skew": None, "real_rate": {"value": 1.82, "pctile_full": 71.2}},
        "skew_value_null": {"call_skew": {"value": None, "pctile_full": None}, "real_rate": {"value": 1.82, "pctile_full": 71.2}},
        "real_missing": {"call_skew": {"value": 141.7, "pctile_full": 83.4}},
    }
    js = "\n".join(helpers) + "\nconst render = (x) => `" + m.group(1) + "`;\nconst out = {};\n"
    js += "const cases = " + json.dumps(cases) + ";\n"
    js += "for (const k in cases) { try { out[k] = render(cases[k]); } catch (e) { out[k] = 'THREW ' + e.message; } }\n"
    js += "console.log(JSON.stringify(out));\n"
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    out = json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else {}
    chk("good data renders the numbers as before (83 / 142 / 71 / 1.82%)",
        all(s in out.get("good", "") for s in (">83<", "值 142", ">71<", "1.82%")))
    chk("call_skew null ⇒ no throw, SKEW card shows a dash, real-rate card still renders",
        "THREW" not in out.get("skew_null", "THREW") and "值 —" in out["skew_null"] and "1.82%" in out["skew_null"])
    chk("call_skew value/pctile null ⇒ no throw, dash shown",
        "THREW" not in out.get("skew_value_null", "THREW") and "值 —" in out["skew_value_null"])
    chk("real_rate missing ⇒ no throw, dash shown, SKEW card still renders",
        "THREW" not in out.get("real_missing", "THREW") and "—%" in out["real_missing"] and "值 142" in out["real_missing"])

print("\n" + ("✅ all pass" if not bad else f"🔴 {bad} failed"))
sys.exit(1 if bad else 0)
