#!/usr/bin/env python3
"""Offline test for the 2026-09-26 dual-source audit fixes in build_data.py (no network, writes only to a temp dir).

Why: the audit found five ways one bad upstream answer either killed the whole daily build or was
written over good data:
  #1 main() called build_kindex / build_leaps / build_index_val / build_macro / build_index_panels /
     build_index_extras / build_constituents / build_valuation_extras / build_pulse / build_cape bare,
     so one exception exited non-zero and nothing that day was committed.
  #2 ^NDX fell back to kindex.json (2011+) and then overwrote the 1985+ ndx_century.json.
  #3 kindex / leaps / *_century / sentiment were overwritten without comparing dates.
  #4 build_macro started from {} each run: a failed FRED key vanished from macro.json.
  #5 DFII10 (a context card) was fetched bare: FRED down => the headline gauge was not written.

Cases:
  1-6   history_would_regress: shorter start / older end refused; same, extended, no old file pass; empty new refused.
  7-8   _refuse_if_regress against a temp data dir: raises on a truncated file, passes on an extended one.
  9-12  merge_macro: failed key keeps the old value; no old value => absent; good key replaced; key order kept.
  13    Negative sample: the pre-fix loop (out = {} and skip) loses the key.
  14    main() has no bare build_* call (every one goes through _guard).
  15    Negative sample: the AST check catches a bare call.
  16    ^NDX fallback points at a file that starts before 1990.
  17-19 build_leaps_index with FRED down: gauge still written, real_rate carried from the old file, failure listed.
  20    Negative sample: the pre-fix bare _fred raises.
  21-22 build_index_panels fed the 2011+ series against a 1985+ ndx_century.json: raises, file byte-identical.
  23    kindex / leaps / sentiment run their date check before their whole-file write (source order).
  24-25 sentiment_would_regress: either date older is refused; same day, next day and no old file pass.
  Mutation check (2026-09-26): removing each fix turns at least one case red.

Usage: python scripts/test_build_guards.py   (exit 0 = pass, 1 = fail)
"""
import ast
import inspect
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as bd  # noqa: E402

FAILS = []
REAL_DATA = bd.DATA


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILS.append(name)


# ---- 1-6 history_would_regress
old = ["1985-10-01", "2026-09-24", "2026-09-25"]
check("1 start moved later (2011 fallback) is refused",
      "截短" in (bd.history_would_regress(["2011-01-03", "2026-09-25"], old) or ""))
check("2 end moved earlier is refused",
      "回退" in (bd.history_would_regress(["1985-10-01", "2026-09-24"], old) or ""))
check("3 same range passes", bd.history_would_regress(old, old) is None)
check("4 one more day passes", bd.history_would_regress(old + ["2026-09-28"], old) is None)
check("5 no old file passes", bd.history_would_regress(["2011-01-03"], []) is None)
check("6 empty new series is refused", bd.history_would_regress([], old) == "新序列为空")

# ---- 7-8 _refuse_if_regress on a temp data dir
tmp = Path(tempfile.mkdtemp())
try:
    bd.DATA = tmp
    (tmp / "x_century.json").write_text(json.dumps({"dates": old, "close": [1, 2, 3]}))
    try:
        bd._refuse_if_regress("x_century.json", ["2011-01-03", "2026-09-25"])
        check("7 truncated history raises", False)
    except RuntimeError as e:
        check("7 truncated history raises", "不覆盖" in str(e))
    try:
        bd._refuse_if_regress("x_century.json", old + ["2026-09-28"])
        check("8 extended history passes", True)
    except RuntimeError:
        check("8 extended history passes", False)
finally:
    bd.DATA = REAL_DATA
    shutil.rmtree(tmp, ignore_errors=True)

# ---- 9-13 merge_macro
plan = {"sofr": ("SOFR", lambda s: s), "hy_oas": ("BAML", lambda s: s), "new_key": ("NEW", lambda s: s)}
prev = {"sofr": "old-sofr", "hy_oas": "old-hy"}


def fetch(sid):
    if sid in ("BAML", "NEW"):
        raise RuntimeError("HTTP 500 test")
    return "fresh-" + sid


out, failed = bd.merge_macro(plan, prev, fetch)
check("9 failed key keeps the old value", out.get("hy_oas") == "old-hy")
check("10 failed key with no old value is absent", "new_key" not in out and failed == ["BAML", "NEW"])
check("11 good key is replaced", out["sofr"] == "fresh-SOFR")
check("12 key order follows the fetch plan", list(out) == ["sofr", "hy_oas"])
legacy = {}
for key, (sid, f) in plan.items():
    try:
        legacy[key] = f(fetch(sid))
    except Exception:
        pass
check("13 negative: the pre-fix loop loses hy_oas", "hy_oas" not in legacy)


# ---- 14-15 main() has no bare build_* call
def bare_build_calls(src):
    tree = ast.parse(src)
    bare = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith("build_"):
            bare.append(node.func.id)
    return bare


main_src = inspect.getsource(bd.main)
# lambdas passed to _guard (ETF panels) are guarded by construction: drop them before the check
main_tree = ast.parse(main_src)
for node in ast.walk(main_tree):
    for field, value in ast.iter_fields(node):
        if isinstance(value, list):
            setattr(node, field, [v for v in value if not isinstance(v, ast.Lambda)])
        elif isinstance(value, ast.Lambda):
            setattr(node, field, ast.Constant(None))
check("14 main() has no bare build_* call", bare_build_calls(ast.unparse(main_tree)) == [])
check("15 negative: a bare call is caught",
      bare_build_calls("def main():\n    _guard('a', build_x)\n    build_macro()\n") == ["build_macro"])

# ---- 16 ^NDX fallback file reaches back before 1990
fname, key = bd._PRICE_FALLBACK["^NDX"]
d = json.loads((REAL_DATA / fname).read_text())
check("16 ^NDX fallback starts before 1990", key in d and d["dates"][0] < "1990-01-01")

# ---- 17-20 build_leaps_index with FRED down
tmp = Path(tempfile.mkdtemp())
saved = (bd._cboe_close_resilient, bd._fred)
try:
    shutil.copy(REAL_DATA / "leaps_gauge.json", tmp / "leaps_gauge.json")
    old_rr = json.loads((tmp / "leaps_gauge.json").read_text())["current"]["context"]["real_rate"]
    spx = json.loads((REAL_DATA / "sp500_century.json").read_text())
    kx = json.loads((REAL_DATA / "kindex.json").read_text())
    gspc = pd.Series(spx["close"], index=pd.to_datetime(spx["dates"]), dtype=float)
    vix = pd.Series(kx["vix"], index=pd.to_datetime(kx["dates"]), dtype=float)
    bd.DATA = tmp
    bd._cboe_close_resilient = lambda name: vix.copy()   # any non-empty series; VIX1Y comes from the bank

    def fred_down(*a, **k):
        raise RuntimeError("FRED 503 test")
    bd._fred = fred_down
    bd._FAILURES.clear()
    before = (tmp / "leaps_gauge.json").stat().st_mtime_ns
    bd.build_leaps_index(gspc, vix)
    new = json.loads((tmp / "leaps_gauge.json").read_text())
    check("17 gauge is still written when FRED is down", (tmp / "leaps_gauge.json").stat().st_mtime_ns != before)
    check("18 real_rate card carried from the old file",
          new["current"]["context"]["real_rate"] == old_rr and new["meta"].get("context_carried") == ["real_rate"])
    check("19 the DFII10 failure is listed in meta.failures",
          any("DFII10" in f["section"] for f in bd._FAILURES))
    try:
        bd._fred("DFII10", start="2003-01-01")
        check("20 negative: the pre-fix bare _fred raises", False)
    except RuntimeError:
        check("20 negative: the pre-fix bare _fred raises", True)
finally:
    bd._cboe_close_resilient, bd._fred = saved
    bd.DATA = REAL_DATA
    bd._FAILURES.clear()
    shutil.rmtree(tmp, ignore_errors=True)

# ---- 21-22 build_index_panels refuses to truncate a century file; the old file is untouched
tmp = Path(tempfile.mkdtemp())
try:
    bd.DATA = tmp
    nd = json.loads((REAL_DATA / "ndx_century.json").read_text())
    (tmp / "ndx_century.json").write_text(json.dumps(nd))
    full = pd.Series(nd["close"], index=pd.to_datetime(nd["dates"]), dtype=float)
    short = full[full.index >= "2011-01-03"]          # what the old kindex.json fallback handed over
    raw_before = (tmp / "ndx_century.json").read_bytes()
    try:
        bd.build_index_panels("ndx", short)
        check("21 truncated ^NDX history is refused by build_index_panels", False)
    except RuntimeError as e:
        check("21 truncated ^NDX history is refused by build_index_panels", "截短" in str(e))
    check("22 the 1985+ century file is byte-identical afterwards",
          (tmp / "ndx_century.json").read_bytes() == raw_before)
finally:
    bd.DATA = REAL_DATA
    shutil.rmtree(tmp, ignore_errors=True)

# ---- 23 kindex / leaps / sentiment compare dates before their whole-file write
def guarded_before_write(fn, fname, guard):
    s = inspect.getsource(fn)
    w = s.find(f'write_json("{fname}"')
    g = s.rfind(guard, 0, w)
    return w > 0 and g > 0


check("23 kindex / leaps / sentiment check dates before writing",
      guarded_before_write(bd.build_kindex, "kindex.json", "_refuse_if_regress(\"kindex.json\"")
      and guarded_before_write(bd.build_leaps, "leaps.json", "_refuse_if_regress(\"leaps.json\"")
      and guarded_before_write(bd.build_sentiment, "sentiment.json", "if sentiment_would_regress("))
prev_s = {"date": "2026-09-25", "term_date": "2026-09-25"}
check("24 sentiment: an older CNN date or an older term date is refused",
      bd.sentiment_would_regress("2026-09-24", "2026-09-25", prev_s)
      and bd.sentiment_would_regress("2026-09-25", "2026-09-24", prev_s))
check("25 sentiment: same day and next day pass; no old file passes",
      not bd.sentiment_would_regress("2026-09-25", "2026-09-25", prev_s)
      and not bd.sentiment_would_regress("2026-09-28", "2026-09-28", prev_s)
      and not bd.sentiment_would_regress("2026-09-25", "2026-09-25", {}))

print("\nall pass" if not FAILS else f"\n{len(FAILS)} FAILED")
sys.exit(1 if FAILS else 0)
