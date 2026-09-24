#!/usr/bin/env python3
"""Offline test for build_fundamentals.build_stock_fund (no network, writes nothing).

Cases:
  1. The quote endpoint raises on `info`: the income statement must still be produced, kept in its
     reported units and labelled 原币 (unknown currency), never as USD. Before 2026-09-24 this case
     raised a NameError on `fin_fx` and the income statement was silently dropped.
  2. A non-USD filer (financial currency TWD, quote currency USD): statements convert with the
     statements-currency rate, market cap with the quote-currency rate.
  3. Nothing is written to data/ while testing.

Usage: python scripts/test_build_fundamentals.py   (exit 0 = pass, 1 = fail)
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_fundamentals as bf  # noqa: E402

FAILS = []


def check(name, ok):
    print(("  ok   " if ok else "  FAIL ") + name)
    if not ok:
        FAILS.append(name)


def income():
    return pd.DataFrame({pd.Timestamp("2025-12-31"): [1e11, 2e10], pd.Timestamp("2024-12-31"): [9e10, 1.8e10]},
                        index=["Total Revenue", "Net Income"])


class Down:
    """Quote endpoint down: `info` raises, statements still available."""
    def __init__(self, t):
        pass

    @property
    def info(self):
        raise ConnectionError("quote endpoint down")

    @property
    def income_stmt(self):
        return income()

    @property
    def dividends(self):
        return pd.Series(dtype=float)


class TwdFiler(Down):
    @property
    def info(self):
        return {"currency": "USD", "financialCurrency": "TWD", "marketCap": 1_000_000_000, "freeCashflow": 3_200_000_000}


def run(ticker_cls, fx):
    written = {}
    bf.write_json = lambda name, obj: written.__setitem__(name, obj)
    bf.mt_fetch = lambda *a, **k: []
    bf.time.sleep = lambda s: None
    bf.yf.Ticker = ticker_cls
    bf._fx_to_usd = lambda cur: fx.get(cur)
    bf.build_stock_fund("TEST")
    return written


def main():
    w = run(Down, {"USD": 1.0, "TWD": 0.03125})
    inc = (w.get("s_test_fund.json") or {}).get("income4")
    check("endpoint down: income statement still produced", bool(inc) and inc.get("revenue") == [90.0, 100.0])
    check("endpoint down: unknown currency labelled 原币, not USD", bool(inc) and inc.get("currency") == "原币")
    check("endpoint down: no snapshot invented", "snapshot" not in w.get("s_test_fund.json", {}))

    w = run(TwdFiler, {"USD": 1.0, "TWD": 0.03125})
    f = w.get("s_test_fund.json") or {}
    inc, snap = f.get("income4") or {}, f.get("snapshot") or {}
    check("TWD filer: statements converted with the TWD rate", inc.get("currency") == "USD" and inc.get("revenue") == [2.81, 3.12])
    check("TWD filer: FCF uses the statements currency", snap.get("fcf") == 100_000_000 and snap.get("fcf_currency") == "USD")
    check("TWD filer: market cap uses the quote currency", snap.get("market_cap") == 1_000_000_000 and snap.get("market_cap_currency") == "USD")
    check("nothing written to disk (only the intercepted writer was called)", set(w) == {"s_test_fund.json"})

    print("pass" if not FAILS else f"FAIL {len(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
