#!/usr/bin/env python3
"""Offline test for second-source failures in the Cboe paths (no network, writes nothing).

Why (2026-09-25): three asymmetric-degradation bugs, same shape as fetch_institutional's CSV/JSON one.
  1. _cboe_close_resilient called the Yahoo top-up inside Cboe's try. Any error in the top-up threw
     away a good Cboe history and switched to the Yahoo fallback (labelled yahoo_fallback, so that
     night's Cboe-vs-Yahoo reconciliation was skipped too).
  2. _vol_yahoo_topup had no empty check. yfinance often returns an empty frame instead of raising;
     the empty series has an integer index, comparing it with a Timestamp raises TypeError, and the
     whole vol-indices section stopped updating even though Cboe was fine.
  3. build_vol_indices dropped a symbol entirely when Cboe failed (VIX and its ratios vanished from
     the page), while a merely stale Cboe got a Yahoo top-up. Now it carries the last published
     reading forward, marked stale, with its old date.

Usage: python scripts/test_vol_fallback.py   (exit 0 = pass, 1 = fail)
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as bd  # noqa: E402

FAILS = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILS.append(name)


idx = pd.bdate_range("2025-08-01", "2026-09-24")
good = pd.Series([20.0 + (i % 7) for i in range(len(idx))], index=idx, dtype=float)
EMPTY = pd.Series(dtype=float)

# ---- 1. resilient (gauge members, e.g. VIX9D): a top-up error must not discard good Cboe data ----
real_patch = bd._patch_stale_with_yahoo


def broken_patch(name, s, now=None):
    bd._YAHOO_PATCHED[name] = ["2026-09-25"]          # fails after recording, the worst case
    raise RuntimeError("top-up bug")


bd._cboe_close = lambda name: good.copy()
bd._patch_stale_with_yahoo = broken_patch
bd._YAHOO_PATCHED.clear()
bd._SOURCE_TRACE.clear()
out = bd._cboe_close_resilient("VIX9D")
check("1 top-up error: Cboe history kept", len(out) == len(good) and out.index.max() == good.index.max())
check("1 top-up error: source stays cboe (reconciliation not skipped)", bd._SOURCE_TRACE.get("VIX9D") == "cboe")
check("1 top-up error: half-written patched mark is rolled back", "VIX9D" not in bd._YAHOO_PATCHED)
bd._patch_stale_with_yahoo = real_patch


def cboe_down(name):
    raise ConnectionError("cboe down")


bd._cboe_close = cboe_down
bd._yahoo_close = lambda sym: good.copy()
bd._SOURCE_TRACE.clear()
out = bd._cboe_close_resilient("VIX9D")
check("1b Cboe down: Yahoo fallback still used", bd._SOURCE_TRACE.get("VIX9D") == "yahoo_fallback" and len(out) == len(good))

# ---- 2. vol top-up: empty Yahoo must not raise ----
bd._yahoo_close = lambda sym: EMPTY.copy()
try:
    s2, got = bd._vol_yahoo_topup("VIX", good.copy())
    check("2 empty Yahoo: returns Cboe unchanged, no dates patched", len(s2) == len(good) and got == [])
except Exception as e:  # noqa: BLE001
    check(f"2 empty Yahoo: raised {type(e).__name__}", False)
try:
    EMPTY[(EMPTY.index > good.index.max())]
    raised = False
except TypeError:
    raised = True
check("2 negative sample: without the guard the comparison does raise TypeError", raised)

# ---- 3. build_vol_indices: Cboe failure carries the last published reading ----
captured = {}
bd.write_json = lambda name, obj: captured.__setitem__(name, obj)
prev = {
    "members": [{"symbol": s, "label": s, "current": 11.0, "date": "2026-09-23"} for s in bd.VOL_INDICES],
    "ratios": {"vxn_vix": {"current": 1.2, "date": "2026-09-23"}, "rvx_vix": {"current": 1.3, "date": "2026-09-23"}},
}
bd._prev_vol_indices = lambda: prev


def run(failing, prev_doc=prev, empty=()):
    def fake(sym):
        if sym in failing:
            raise ConnectionError("cboe down")
        return EMPTY.copy() if sym in empty else good.copy()
    bd._cboe_close = fake
    bd._prev_vol_indices = lambda: prev_doc
    captured.clear()
    bd.build_vol_indices()
    return captured["vol_indices.json"]


doc = run({"VIX"})
m = {x["symbol"]: x for x in doc["members"]}
check("3 VIX Cboe down: VIX still on the page", "VIX" in m)
check("3 carried VIX keeps its old date and is marked stale", m["VIX"].get("date") == "2026-09-23" and m["VIX"].get("stale") is True)
check("3 other members fresh and not stale", m["VXN"]["date"] == "2026-09-24" and "stale" not in m["VXN"])
check("3 meta.carried_forward names VIX", doc["meta"].get("carried_forward") == ["VIX"])
check("3 ratios needing VIX carried, marked stale", doc["ratios"]["vxn_vix"].get("stale") is True and doc["ratios"]["vxn_vix"]["date"] == "2026-09-23")
check("3 member order unchanged", [x["symbol"] for x in doc["members"]] == list(bd.VOL_INDICES))

doc = run(set(), empty={"VVIX"})
m = {x["symbol"]: x for x in doc["members"]}
check("3b Cboe returns empty series: treated as failure and carried", m["VVIX"].get("stale") is True)

doc = run({"VIX"}, prev_doc={})
check("3c no earlier reading: VIX absent, nothing invented", "VIX" not in {x["symbol"] for x in doc["members"]})

doc = run(set())
check("3d all healthy: nothing stale, carried_forward empty",
      not any(x.get("stale") for x in doc["members"]) and doc["meta"]["carried_forward"] == []
      and not any(r.get("stale") for r in doc["ratios"].values()))

# ---- 4. vol_family (2026-09-25): same two fixes as vol_indices ----
import json as _json
import tempfile as _tf
from pathlib import Path as _P
_tmp = _P(_tf.mkdtemp())
bd.DATA = _tmp
(_tmp / "vol_family.json").write_text(_json.dumps({"members": [
    {"symbol": s, "label": s, "current": 30.0, "date": "2026-09-22"} for s in bd.VOL_FAMILY]}), encoding="utf-8")
stale_cboe = good[good.index <= pd.Timestamp("2026-09-23")]
bd._yahoo_close = lambda sym: pd.Series([31.5], index=pd.to_datetime(["2026-09-24"]))


def fam(failing):
    def fake(sym):
        if sym in failing:
            raise ConnectionError("cboe down")
        return stale_cboe.copy()
    bd._cboe_close = fake
    captured.clear()
    bd.build_vol_family(good.copy())
    return captured["vol_family.json"]


doc = fam(set())
m = {x["symbol"]: x for x in doc["members"]}
check("4 vol_family: Cboe stale at 09-23, Yahoo has 09-24 => topped up and recorded",
      m["VXAPL"]["date"] == "2026-09-24" and m["VXAPL"]["current"] == 31.5
      and doc["meta"]["yahoo_patched"].get("VXAPL") == ["2026-09-24"])
doc = fam({"VXGS"})
m = {x["symbol"]: x for x in doc["members"]}
check("4 vol_family: Cboe down for VXGS => carried with old date, stale, named in meta",
      m["VXGS"]["stale"] is True and m["VXGS"]["date"] == "2026-09-22" and doc["meta"]["carried_forward"] == ["VXGS"])

print("\n" + ("all passed" if not FAILS else f"{len(FAILS)} failed"))
sys.exit(1 if FAILS else 0)
