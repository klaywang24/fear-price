#!/usr/bin/env python3
"""Offline test for build_fundamentals.build_stock_fund (no network, writes nothing).

Cases:
  1. The quote endpoint raises on `info`: the income statement must still be produced, kept in its
     reported units and labelled 原币 (unknown currency), never as USD. Before 2026-09-24 this case
     raised a NameError on `fin_fx` and the income statement was silently dropped.
  2. A non-USD filer (financial currency TWD, quote currency USD): statements convert with the
     statements-currency rate, market cap with the quote-currency rate.
  3. Partial quoteSummary answers (the shapes seen in the 2026-08-22, 09-12 and 09-19 weekly builds): retried,
     and when the modules never come back the previously published snapshot is kept and flagged stale.
  4. Nothing is written to data/ while testing.

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
    bf.history_rows = lambda ticker, prev=None: {}   # 长历史不走网络（2026-09-26 起 edgar_history）
    bf.previous_fund = lambda ticker: {}
    bf.time.sleep = lambda s: None
    bf.yf.Ticker = ticker_cls
    bf._fx_to_usd = lambda cur: fx.get(cur)
    bf.build_stock_fund("TEST")
    return written


FULL = {"currency": "USD", "financialCurrency": "USD", "marketCap": 2_000_000_000, "freeCashflow": 50_000_000,
        "trailingPE": 20.0, "forwardPE": 18.0, "priceToSalesTrailing12Months": 5.0, "priceToBook": 6.0,
        "returnOnEquity": 0.3, "grossMargins": 0.5, "profitMargins": 0.2, "dividendYield": 1.0, "beta": 1.1, "payoutRatio": 0.2}
QUOTE_ONLY = {k: FULL[k] for k in ("currency", "marketCap", "trailingPE", "forwardPE", "priceToBook", "dividendYield")}


def make_ticker(answers):
    """A Ticker class whose successive `info` reads return the given dicts (the last one repeats)."""
    calls = []

    class T(Down):
        @property
        def info(self):
            calls.append(1)
            return answers[min(len(calls) - 1, len(answers) - 1)]
    T.calls = calls
    return T


def main():
    # Partial quoteSummary answers (the 2026-08-22 / 09-12 / 09-19 weekly-build shapes)
    T = make_ticker([QUOTE_ONLY, QUOTE_ONLY, FULL])
    bf.previous_snapshot = lambda ticker: None
    w = run(T, {"USD": 1.0})
    snap = (w.get("s_test_fund.json") or {}).get("snapshot") or {}
    check("partial answer twice then full: retried and the full snapshot was kept", len(T.calls) == 3 and snap.get("ps") == 5.0 and not snap.get("snapshot_stale"))

    T = make_ticker([QUOTE_ONLY])
    prev = {"pe": 19.0, "ps": 4.0, "market_cap": 1_900_000_000, "beta": 1.0, "snapshot_as_of": "2026-09-12"}
    bf.previous_snapshot = lambda ticker: dict(prev)
    w = run(T, {"USD": 1.0})
    snap = (w.get("s_test_fund.json") or {}).get("snapshot") or {}
    check("always partial: gave up after 3 tries", len(T.calls) == 3)
    check("always partial: the previously published snapshot is kept and flagged stale",
          snap.get("ps") == 4.0 and snap.get("snapshot_as_of") == "2026-09-12" and snap.get("snapshot_stale") is True)

    T = make_ticker([{}])
    bf.previous_snapshot = lambda ticker: None
    w = run(T, {"USD": 1.0})
    snap = (w.get("s_test_fund.json") or {}).get("snapshot") or {}
    check("empty answer and no previous file: written blank but flagged stale, never silently", snap.get("snapshot_stale") is True and snap.get("market_cap") is None)
    check("complete snapshot is recognised", bf.snapshot_complete({"market_cap": 1, "ps": 2}) and not bf.snapshot_complete({"market_cap": 1}) and not bf.snapshot_complete({"ps": 2}))

    bf.previous_snapshot = lambda ticker: None
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

    # 2026-09-25: a macrotrends page that comes back empty must not wipe the published history
    prev = {"pe": {"dates": ["2025-12-31"], "values": [30.1]}, "eps": {"dates": ["2025-12-31"], "values": [6.0]},
            "driver": [{"year": 2025}], "roe": {"dates": ["2025"], "values": [1.5]}, "history_as_of": "2026-09-19"}
    fund = {"ticker": "T"}
    carried = bf.carry_history(fund, {p: [] for p in bf.PAGE_KEYS}, prev, "2026-09-26")
    check("empty macrotrends pages keep the last published history (09-24 wiped 35/35)",
          fund.get("pe") == prev["pe"] and fund.get("roe") == prev["roe"] and set(carried) == {"pe", "eps", "driver", "roe"})
    check("carried history says where it came from, and is not re-dated to today",
          fund["history_carried"]["from"] == "2026-09-19" and fund["history_as_of"] == "2026-09-19")
    fund = {"ticker": "T", "roe": {"dates": ["2026"], "values": [9.9]}}
    carried = bf.carry_history(fund, {"roe": [["2026", "x"]], "pe-ratio": []}, prev, "2026-09-26")
    check("a page that did return rows is never overwritten by the old file",
          fund["roe"]["values"] == [9.9] and "roe" not in carried and fund["history_as_of"] == "2026-09-26")
    fund = {"ticker": "T"}
    bf.carry_history(fund, {p: [] for p in bf.PAGE_KEYS}, {}, "2026-09-26")
    check("nothing to carry and nothing fetched: keys stay absent (never invented)", "pe" not in fund and "history_carried" not in fund)
    # 2026-09-27：滚动四季不许沿用停报之后的旧季度（edgar_history.ttm）
    import edgar_history as eh
    q = {"2014-12-31": 1, "2015-03-31": 2, "2015-06-30": 3, "2015-09-30": 4}
    check("ttm: stale quarters years before the period end are not reused (BAC capex / TJX operating income shape)",
          eh.ttm(q, "2023-12-31") is None)
    check("ttm: four quarters ending at the period end still sum", eh.ttm(q, "2015-09-30") == 10)
    # 2026-09-27 改成行为测试（原先只比清单下标，f0cbf232 把合并改成先到先得后，那条下标断言照过、意图却反了）。
    #   锁的是引擎注释写明的优先级：同一期多个营收标签时取最全的口径。麦当劳 SalesRevenueGoodsNet 只是直营销售，
    #   Revenues 才是总营收（2019-03 两者 22.4 亿 vs 50.2 亿）；含税与不含税都报时取不含税。
    #   ⚠️ 这两条红＝本周基本面不发布（weekly.yml 先跑本测试），宁可沿用上周也不发错数。
    def fx(tag_vals):
        return {"facts": {"us-gaap": {t: {"units": {"USD": [{"start": "2019-01-01", "end": "2019-03-31", "val": v,
                "form": "10-Q", "filed": "2019-05-01"}]}} for t, v in tag_vals.items()}}}
    r1 = eh.quarterly(fx({"SalesRevenueGoodsNet": 2240500000, "Revenues": 5024100000}), eh.REV_TAGS, "USD")
    check("revenue merge: total Revenues wins over the SalesRevenueGoodsNet subset (McDonald's shape)",
          r1.get("2019-03-31") == 5024100000)
    r2 = eh.quarterly(fx({"RevenueFromContractWithCustomerIncludingAssessedTax": 110,
                          "RevenueFromContractWithCustomerExcludingAssessedTax": 100}), eh.REV_TAGS, "USD")
    check("revenue merge: ExcludingAssessedTax wins when both tax variants exist", r2.get("2019-03-31") == 100)
    print("pass" if not FAILS else f"FAIL {len(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
