#!/usr/bin/env python3
"""Offline test for the gauge's Yahoo top-up (no network, writes nothing).

Why (2026-09-25):
  1. _patch_stale_with_yahoo had no 16:30 ET guard. VIX-family indices trade from ~03:00 ET, so a
     daytime run could append today's intraday value as a close into the append-only VIX1Y bank.
     _vol_yahoo_topup already had this guard; the gauge path did not.
  2. meta.yahoo_patched was rewritten every run. Yahoo's ^VIX1Y returns only the latest day, so the
     09-23 value (21.69, Yahoo) stayed in the series while its "patched" mark vanished on 09-24.

Cases:
  1. Before 16:30 ET today's Yahoo row is not appended; yesterday's is.
  2. After 16:30 ET today's row is appended.
  3. Cboe-last is recorded for later provenance decisions.
  4. _merge_yahoo_patched keeps an earlier patched date while Cboe has not published it.
  5. ... and drops it once Cboe has published through that date.
  6. ... keeps it when Cboe-last is unknown (Cboe failed this run).
  7. ... drops a date no longer in the series.
  8. Negative sample: a guard-less cutoff would have appended the intraday row.

Usage: python scripts/test_gauge_yahoo_patch.py   (exit 0 = pass, 1 = fail)
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


cboe = pd.Series([21.59], index=pd.to_datetime(["2026-09-22"]))
yahoo = pd.Series([21.69, 21.82, 22.40], index=pd.to_datetime(["2026-09-23", "2026-09-24", "2026-09-25"]))
bd._yahoo_close = lambda sym: yahoo.copy()
NY = "America/New_York"

bd._YAHOO_PATCHED.clear(); bd._CBOE_LAST.clear()
out = bd._patch_stale_with_yahoo("VIX1Y", cboe.copy(), now=pd.Timestamp("2026-09-25 04:56", tz=NY))
check("1 before 16:30 today's intraday row is not appended", out.index.max() == pd.Timestamp("2026-09-24"))
check("1 earlier missing days are appended", bd._YAHOO_PATCHED["VIX1Y"] == ["2026-09-23", "2026-09-24"])
check("3 Cboe-last recorded", bd._CBOE_LAST["VIX1Y"] == "2026-09-22")

bd._YAHOO_PATCHED.clear()
out = bd._patch_stale_with_yahoo("VIX1Y", cboe.copy(), now=pd.Timestamp("2026-09-25 18:05", tz=NY))
check("2 after 16:30 today's close is appended", out.index.max() == pd.Timestamp("2026-09-25"))

dates = {"2026-09-22", "2026-09-23", "2026-09-24"}
m = bd._merge_yahoo_patched({"VIX1Y": ["2026-09-23"]}, {"VIX1Y": ["2026-09-24"]}, {"VIX1Y": "2026-09-22"}, dates)
check("4 earlier patched date kept while Cboe has not published it", m == {"VIX1Y": ["2026-09-23", "2026-09-24"]})
m = bd._merge_yahoo_patched({"VIX1Y": ["2026-09-23"]}, {}, {"VIX1Y": "2026-09-23"}, dates)
check("5 dropped once Cboe has published that date", m == {})
m = bd._merge_yahoo_patched({"VIX1Y": ["2026-09-23"]}, {}, {}, dates)
check("6 kept when Cboe-last is unknown", m == {"VIX1Y": ["2026-09-23"]})
m = bd._merge_yahoo_patched({"VIX1Y": ["2026-09-19"]}, {}, {"VIX1Y": "2026-09-18"}, dates)
check("7 dropped when the date is no longer in the series", m == {})

no_guard = yahoo[yahoo.index > cboe.index.max()]
check("8 negative sample: without the cutoff the 09-25 intraday row would be appended",
      no_guard.index.max() == pd.Timestamp("2026-09-25"))

print("\n%s" % ("all passed" if not FAILS else "%d failed: %s" % (len(FAILS), FAILS)))
sys.exit(1 if FAILS else 0)
