#!/usr/bin/env python3
"""个股基本面管线（每周更新，与每日价格管线分离）。

数据源：
- macrotrends.net：PE / PS / ROE / ROIC / FCF 的 15~20 年季频历史（美股 + 欧股 ADR）
- yfinance：当前快照指标、近 4 年报表、完整分红史

输出：data/s_{ticker}_fund.json（逐股）+ data/{basket}_peers.json（同业对比快照）。
拉取失败的字段留空，前端按可用性渲染；绝不伪造数字。
"""
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_data import BASKETS, DATA, safe_ticker, write_json, UA

# 欧股用美股 ADR 的 macrotrends 数据（美元计价，比率类指标不受币种影响）
MT_SYMBOL = {"MC.PA": "LVMUY", "RMS.PA": "HESAY"}
MT_PAGES = ("pe-ratio", "ps-ratio", "price-book", "roe", "roic", "free-cash-flow")


def mt_fetch(sym: str, page: str) -> list:
    """返回 macrotrends 表格行（首列为 YYYY-MM-DD 的行），每行是字符串列表。
    404 = 该指标对此公司不适用（如银行无 PS）；429 = 限流，退避重试。"""
    url = f"https://www.macrotrends.net/stocks/charts/{sym}/x/{page}"
    for attempt in range(4):
        r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
        if r.status_code == 429:
            time.sleep(20 * (attempt + 1))
            continue
        break
    if r.status_code != 200:
        raise RuntimeError(f"{sym}/{page} HTTP {r.status_code}")
    rows = []
    for m in re.finditer(r"<tr>\s*((?:<td[^>]*>.*?</td>\s*)+)</tr>", r.text, re.S):
        tds = [re.sub(r"<[^>]+>", "", t).strip() for t in
               re.findall(r"<td[^>]*>(.*?)</td>", m.group(1), re.S)]
        if tds and re.match(r"^\d{4}-\d{2}-\d{2}$", tds[0]):
            rows.append(tds)
    rows.sort(key=lambda x: x[0])
    return rows


def num(s: str):
    s = s.replace("$", "").replace(",", "").replace("%", "").replace("B", "").strip()
    try:
        return round(float(s), 3)
    except ValueError:
        return None


def series_from(rows, col):
    dates, vals = [], []
    for r in rows:
        if len(rows[0]) > col and len(r) > col:
            v = num(r[col])
            if v is not None:
                dates.append(r[0])
                vals.append(v)
    return {"dates": dates, "values": vals}


_FX = {}


def _fx_to_usd(cur: str):
    """Units of USD per one unit of `cur`. 1.0 for USD; None when the rate cannot be fetched
    (then the caller keeps the local figure and labels it with its currency instead of faking USD)."""
    if cur == "USD":
        return 1.0
    if cur not in _FX:
        try:
            _FX[cur] = float(yf.Ticker(f"{cur}USD=X").fast_info["last_price"])
        except Exception as e:  # noqa: BLE001
            print(f"  fx {cur}USD=X unavailable: {e}")
            _FX[cur] = None
    return _FX[cur]


# Yahoo's quoteSummary answers GitHub runners only partially some weeks (2026-08-22 TSM/AVGO, 09-12 AVGO/AAPL:
# only the quote fields came back; 09-19 TSM/AVGO: nothing at all). `info` does not raise in that case, so the
# snapshot was written with blanks and the page showed empty cells. Now: retry with a fresh Ticker, and if the
# modules still do not come back, keep the previously published snapshot and say so instead of publishing blanks.
INFO_TRIES = 3
INFO_BACKOFF = (5, 15)
QUOTE_ONLY_KEYS = ("pe", "fwd_pe", "pb", "div_yield", "market_cap")   # what survives when only the quote endpoint answers
MODULE_KEYS = ("ps", "roe", "gross_margin", "net_margin", "fcf", "beta", "payout")


def snapshot_complete(snap: dict) -> bool:
    """True when the quote endpoint and the statistics/financial modules both answered."""
    return bool(snap.get("market_cap")) and any(snap.get(k) is not None for k in MODULE_KEYS)


def fetch_info(ticker: str):
    """(Ticker, info) — retried until the modules answer; the last attempt's info is returned either way."""
    t, info = None, {}
    for attempt in range(INFO_TRIES):
        t = yf.Ticker(ticker)
        info = t.info or {}
        # 美股含点代码（BRK.B）Yahoo 用连字符；欧股（MC.PA）带点有效，靠 marketCap 判空回退
        if "." in ticker and not info.get("marketCap"):
            t = yf.Ticker(ticker.replace(".", "-"))
            info = t.info or {}
        if info.get("marketCap") and any(info.get(k) is not None for k in
                                         ("priceToSalesTrailing12Months", "returnOnEquity", "grossMargins",
                                          "profitMargins", "freeCashflow", "beta", "payoutRatio")):
            return t, info
        if attempt < INFO_TRIES - 1:
            print(f"  {ticker} info: partial answer ({len(info)} keys), retry {attempt + 2}/{INFO_TRIES}")
            time.sleep(INFO_BACKOFF[min(attempt, len(INFO_BACKOFF) - 1)])
    return t, info


def previous_snapshot(ticker: str):
    """The snapshot last published for this ticker (the committed data file), or None."""
    try:
        prev = json.loads((DATA / f"s_{safe_ticker(ticker)}_fund.json").read_text(encoding="utf-8"))
        snap = prev.get("snapshot") or {}
        return snap if snapshot_complete(snap) else None
    except Exception:  # noqa: BLE001  no previous file, or unreadable: nothing to fall back on
        return None


# Which published keys each macrotrends page feeds (2026-09-25). When a page comes back empty this run,
# its keys are carried from the last published file instead of vanishing from the site.
PAGE_KEYS = {"pe-ratio": ("pe", "eps", "driver"), "ps-ratio": ("ps",), "price-book": ("pb_hist",),
             "roe": ("roe",), "roic": ("roic",), "free-cash-flow": ("fcf",)}
CARRIED = {}   # ticker -> keys carried this run (read by main for the run report)


def previous_fund(ticker: str) -> dict:
    """The fundamentals file last published for this ticker (committed data file), or {}."""
    try:
        return json.loads((DATA / f"s_{safe_ticker(ticker)}_fund.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  no previous file, or unreadable
        return {}


def carry_history(fund: dict, mt: dict, prev: dict, today: str) -> list:
    """Fill keys whose macrotrends page came back empty from the previous file; mark provenance.

    2026-09-25: a macrotrends outage on 09-24 wrote every s_*_fund.json without pe/eps/driver/pb_hist/
    roe/roic/fcf (35/35) and the job still passed; the site's history charts went blank. Returns the keys
    carried. Never invents values: a key absent from the previous file stays absent."""
    carried = []
    for page, keys in PAGE_KEYS.items():
        if mt.get(page):
            continue
        for k in keys:
            if k not in fund and prev.get(k):
                fund[k] = prev[k]
                carried.append(k)
    fresh = any(mt.get(p) for p in PAGE_KEYS)
    fund["history_as_of"] = today if fresh else (prev.get("history_as_of") or prev.get("history_carried", {}).get("from"))
    if carried:
        fund["history_carried"] = {"keys": carried,
                                   "from": (prev.get("history_carried", {}).get("from") if not fresh and prev.get("history_carried")
                                            else prev.get("history_as_of")) or "previous build"}
    return carried


def build_stock_fund(ticker: str):
    sym = MT_SYMBOL.get(ticker, ticker)
    fund = {"ticker": ticker, "mt_symbol": sym}

    # ---- macrotrends 长历史 ----
    mt = {}
    for page in MT_PAGES:
        try:
            mt[page] = mt_fetch(sym, page)
        except Exception as e:
            print(f"  {ticker} {page}: {e}")
            mt[page] = []
        time.sleep(2.5)

    pe_rows = mt["pe-ratio"]  # [date, price, eps_ttm, pe]
    if pe_rows:
        fund["pe"] = series_from(pe_rows, 3)
        fund["eps"] = series_from(pe_rows, 2)
        # 估值驱动 vs EPS 驱动：取每年最后一行做年度分解
        by_year = {}
        for r in pe_rows:
            by_year[r[0][:4]] = r
        years = sorted(by_year)
        driver = []
        for a, b in zip(years, years[1:]):
            p0, e0 = num(by_year[a][1]), num(by_year[a][2])
            p1, e1 = num(by_year[b][1]), num(by_year[b][2])
            if not all(x and x > 0 for x in (p0, e0, p1, e1)):
                continue
            driver.append({
                "year": int(b),
                "price_ret": round((p1 / p0 - 1) * 100, 1),
                "eps_chg": round((e1 / e0 - 1) * 100, 1),
                "pe_chg": round((p1 / e1) / (p0 / e0) * 100 - 100, 1),
            })
        fund["driver"] = driver
    if mt["ps-ratio"]:
        fund["ps"] = series_from(mt["ps-ratio"], 3)
    if mt["price-book"]:
        fund["pb_hist"] = series_from(mt["price-book"], 3)
    if mt["roe"]:
        fund["roe"] = series_from(mt["roe"], 3)
    if mt["roic"]:
        fund["roic"] = series_from(mt["roic"], 3)
    if mt["free-cash-flow"]:
        # 2 列（date, FCF $M）：只取 12-31 年度行，季频行同样是 TTM 会重复
        rows = [r for r in mt["free-cash-flow"] if len(r) == 2]
        annual = [r for r in rows if r[0].endswith("12-31")] or rows
        fund["fcf"] = {"dates": [r[0][:4] for r in annual],
                       "values": [num(r[1]) for r in annual]}

    _c = carry_history(fund, mt, previous_fund(ticker), datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    if _c:
        CARRIED[ticker] = _c
        print(f"  {ticker} history carried from the last published file: {', '.join(_c)}")

    # ---- yfinance：快照 / 近4年报表 / 分红史 ----
    # 美股含点代码（BRK.B）Yahoo 用连字符；欧股（MC.PA）带点有效，靠 marketCap 判空回退
    t = yf.Ticker(ticker)
    # Initialised outside the try (2026-09-24): if the quote endpoint is down, `info` raises before these
    # are set and the income statement below would be dropped with a NameError. Unknown currency stays
    # unknown: the statements are then kept in their reported units and labelled 原币, never as USD.
    cur = fin_cur = None
    fx = fin_fx = None
    try:
        t, info = fetch_info(ticker)
        # Currency (2026-09-24): Yahoo reports marketCap / freeCashflow in the listing currency
        # (EUR for MC.PA, TWD for TSM). The site prints these with a $ sign, so convert to USD at the
        # current FX rate and keep the local figures alongside; never mix currencies in a peers table.
        # Two currencies, not one (2026-09-24 review caught TSM): the quote currency (`currency`, USD for
        # the ADR) prices the market cap; the statements currency (`financialCurrency`, TWD) prices free
        # cash flow and the income statement. Convert each with its own rate. When a rate cannot be
        # fetched, keep the local figure and label it with its currency code rather than blanking it.
        cur = (info.get("currency") or "USD").upper()
        fin_cur = (info.get("financialCurrency") or cur).upper()
        fx, fin_fx = _fx_to_usd(cur), _fx_to_usd(fin_cur)
        mc_local, fcf_local = info.get("marketCap"), info.get("freeCashflow")
        mc_usd = round(mc_local * fx) if (mc_local and fx) else mc_local
        fcf_usd = round(fcf_local * fin_fx) if (fcf_local and fin_fx) else fcf_local
        fund["snapshot"] = {
            "pe": info.get("trailingPE"), "fwd_pe": info.get("forwardPE"),
            "ps": info.get("priceToSalesTrailing12Months"), "pb": info.get("priceToBook"),
            "roe": round(info["returnOnEquity"] * 100, 1) if info.get("returnOnEquity") else None,
            "gross_margin": round(info["grossMargins"] * 100, 1) if info.get("grossMargins") else None,
            "net_margin": round(info["profitMargins"] * 100, 1) if info.get("profitMargins") else None,
            "div_yield": info.get("dividendYield"),
            "market_cap": mc_usd,
            "fcf": fcf_usd,
            # Each converted figure carries its own currency as shown (USD once converted, else the
            # local code). `currency` is kept for older readers and equals USD only when both converted.
            "market_cap_currency": "USD" if fx else cur,
            "fcf_currency": "USD" if fin_fx else fin_cur,
            "currency": "USD" if (fx and fin_fx) else (cur if not fx else fin_cur),
            "quote_currency": cur,
            "financial_currency": fin_cur,
            "fx_to_usd": fx,
            "fin_fx_to_usd": fin_fx,
            "market_cap_local": mc_local,
            "fcf_local": fcf_local,
            "beta": info.get("beta"),
            "payout": round(info["payoutRatio"] * 100, 1) if info.get("payoutRatio") else None,
            "snapshot_as_of": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        }
        if not snapshot_complete(fund["snapshot"]):
            got = sum(1 for k in QUOTE_ONLY_KEYS + MODULE_KEYS if fund["snapshot"].get(k) is not None)
            prev = previous_snapshot(ticker)
            if prev:
                print(f"  {ticker} snapshot incomplete after {INFO_TRIES} tries ({got}/12 fields); "
                      f"keeping the previously published snapshot (as of {prev.get('snapshot_as_of', 'earlier build')})")
                fund["snapshot"] = dict(prev, snapshot_stale=True)
            else:
                print(f"  {ticker} snapshot incomplete after {INFO_TRIES} tries ({got}/12 fields); no previous snapshot to keep")
                fund["snapshot"]["snapshot_stale"] = True
    except Exception as e:
        print(f"  {ticker} info: {e}")
    try:
        inc = t.income_stmt
        years = [str(c)[:4] for c in inc.columns][::-1]
        # Statements are reported in `financialCurrency`; convert to USD with that rate (2026-09-24),
        # otherwise keep the local figures and say so in `currency` so the page can title them.
        _inc_fx = fin_fx if fin_fx else 1.0
        def row(name):
            if name in inc.index:
                return [None if pd.isna(v) else round(float(v) * _inc_fx / 1e9, 2)
                        for v in inc.loc[name]][::-1]
            return None
        fund["income4"] = {"years": years, "currency": "USD" if fin_fx else (fin_cur or "原币"),
                           "revenue": row("Total Revenue"),
                           "net_income": row("Net Income")}
    except Exception as e:
        print(f"  {ticker} income_stmt: {e}")
    try:
        div = t.dividends
        if len(div):
            per_year = div.groupby(div.index.year).sum()
            per_year = per_year[per_year.index < datetime.now().year + 1]
            fund["dividends"] = {"years": [int(y) for y in per_year.index],
                                 "amounts": [round(float(v), 3) for v in per_year]}
    except Exception as e:
        print(f"  {ticker} dividends: {e}")

    write_json(f"s_{safe_ticker(ticker)}_fund.json", fund)
    return fund.get("snapshot", {})


def main():
    for basket, members in BASKETS.items():
        peers = []
        for ticker, name in members:
            print(f"== fund {ticker}")
            snap = build_stock_fund(ticker)
            peers.append(dict(snap or {}, ticker=ticker, name=name))
            time.sleep(1)
        write_json(f"{basket}_peers.json", {
            "rows": peers,
            "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        })
    report = os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(), "fund_carried.json")
    with open(report, "w", encoding="utf-8") as f:
        json.dump(CARRIED, f, ensure_ascii=False)
    if CARRIED:
        print(f"history carried for {len(CARRIED)} ticker(s): {sorted(CARRIED)}")
    print("done.")


def check_carried(limit: int) -> int:
    """Run after the data is committed: fail the job (so it is noticed) when more than `limit` tickers had
    to carry history. Kept out of the build step so a macrotrends outage never blocks the week's commit."""
    report = os.path.join(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir(), "fund_carried.json")
    try:
        carried = json.load(open(report, encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"::error::no build report ({type(e).__name__}); the build step did not finish")
        return 1
    if len(carried) > limit:
        print(f"::error::macrotrends history came back empty for {len(carried)} tickers (> {limit}); "
              f"the site shows the last published history for: {sorted(carried)}")
        return 1
    print(f"history carried for {len(carried)} ticker(s) (limit {limit}) — ok")
    return 0


if __name__ == "__main__":
    if "--check-carried" in sys.argv:
        sys.exit(check_carried(int(sys.argv[sys.argv.index("--check-carried") + 1])))
    main()
