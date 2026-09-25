#!/usr/bin/env python3
"""Offline test for build_data._refetch_missing_closes (no network, writes nothing).

Why: on 2026-09-23 and 09-24 the cloud build got a latest close for only 60 of ~500 S&P 500
constituents from one yf.download pass, so the coverage gate froze pulse.json and breadth.json on
older values for two days. The fix re-requests only the tickers missing the latest close.

Cases:
  1. Partial answer then a full retry: missing latest closes are filled.
  2. Values already fetched are never overwritten by the retry (combine_first, not update).
  3. A retry that raises, then one that works: the second attempt fills the gap.
  4. Tickers that never come back stay missing; the function returns and does not loop.
  5. Empty first pass (everything failed): the retry rebuilds the frame.
  6. Nothing to fix: no download call is made.
  7. Negative sample: an overwrite-style merge is caught by the check in case 2.

Usage: python scripts/test_pulse_refetch.py   (exit 0 = pass, 1 = fail)
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_data as bd  # noqa: E402

FAILS = []
IDX = pd.to_datetime(["2026-09-22", "2026-09-23", "2026-09-24"])
TICKS = ["AAA", "BBB", "CCC", "DDD"]
FULL = pd.DataFrame({t: [10.0 + i, 11.0 + i, 12.0 + i] for i, t in enumerate(TICKS)}, index=IDX)


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILS.append(name)


def partial():
    px = FULL.copy()
    px.loc[IDX[-1], ["CCC", "DDD"]] = np.nan      # latest close missing for two tickers
    px["AAA"] = [10.0, 11.0, 99.0]               # a value that must survive the retry
    return px


def fake_dl(frame, fail_first=0, calls=None):
    state = {"n": 0}

    def dl(tks):
        state["n"] += 1
        if calls is not None:
            calls.append(list(tks))
        if state["n"] <= fail_first:
            raise ConnectionError("simulated")
        return frame[[t for t in tks if t in frame.columns]]
    return dl


def noop(_):
    return None


# 1 + 2
calls = []
out = bd._refetch_missing_closes(partial(), TICKS, download=fake_dl(FULL, calls=calls), sleep=noop)
check("1 missing latest closes are filled", not bd._missing_latest(out, TICKS))
check("1 only the missing tickers are re-requested", calls == [["CCC", "DDD"]])
check("2 already-fetched value is not overwritten", out.at[IDX[-1], "AAA"] == 99.0)

# 3
out = bd._refetch_missing_closes(partial(), TICKS, download=fake_dl(FULL, fail_first=1), sleep=noop)
check("3 second attempt fills after a failed first retry", not bd._missing_latest(out, TICKS))

# 4
never = FULL.drop(columns=["DDD"])
calls = []
out = bd._refetch_missing_closes(partial(), TICKS, download=fake_dl(never, calls=calls), sleep=noop)
check("4 a ticker that never returns stays missing", bd._missing_latest(out, TICKS) == ["DDD"])
check("4 at most two retries", len(calls) == 2)

# 5
out = bd._refetch_missing_closes(pd.DataFrame(), TICKS, download=fake_dl(FULL), sleep=noop)
check("5 empty first pass is rebuilt", not bd._missing_latest(out, TICKS) and out.shape == FULL.shape)

# 6
calls = []
bd._refetch_missing_closes(FULL.copy(), TICKS, download=fake_dl(FULL, calls=calls), sleep=noop)
check("6 nothing missing, no download call", calls == [])

# 7 negative sample: an overwrite merge must be caught by the case-2 check
bad = partial()
bad.update(FULL)                                   # overwrite style
check("7 negative sample: overwrite merge is detected", bad.at[IDX[-1], "AAA"] != 99.0)

print("\n%s" % ("all passed" if not FAILS else "%d failed: %s" % (len(FAILS), FAILS)))
sys.exit(1 if FAILS else 0)
