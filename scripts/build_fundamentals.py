#!/usr/bin/env python3
"""个股基本面管线（每周更新，与每日价格管线分离）。

数据源：
- macrotrends.net：PE / PS / ROE / ROIC / FCF 的 15~20 年季频历史（美股 + 欧股 ADR）
- yfinance：当前快照指标、近 4 年报表、完整分红史

输出：data/s_{ticker}_fund.json（逐股）+ data/{basket}_peers.json（同业对比快照）。
拉取失败的字段留空，前端按可用性渲染；绝不伪造数字。
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_data import BASKETS, safe_ticker, write_json, UA

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

    # ---- yfinance：快照 / 近4年报表 / 分红史 ----
    # 美股含点代码（BRK.B）Yahoo 用连字符；欧股（MC.PA）带点有效，靠 marketCap 判空回退
    t = yf.Ticker(ticker)
    try:
        info = t.info
        if "." in ticker and not info.get("marketCap"):
            t = yf.Ticker(ticker.replace(".", "-"))
            info = t.info
        # Currency (2026-09-24): Yahoo reports marketCap / freeCashflow in the listing currency
        # (EUR for MC.PA, TWD for TSM). The site prints these with a $ sign, so convert to USD at the
        # current FX rate and keep the local figures alongside; never mix currencies in a peers table.
        cur = (info.get("currency") or "USD").upper()
        fx = _fx_to_usd(cur)
        mc_local, fcf_local = info.get("marketCap"), info.get("freeCashflow")
        fund["snapshot"] = {
            "pe": info.get("trailingPE"), "fwd_pe": info.get("forwardPE"),
            "ps": info.get("priceToSalesTrailing12Months"), "pb": info.get("priceToBook"),
            "roe": round(info["returnOnEquity"] * 100, 1) if info.get("returnOnEquity") else None,
            "gross_margin": round(info["grossMargins"] * 100, 1) if info.get("grossMargins") else None,
            "net_margin": round(info["profitMargins"] * 100, 1) if info.get("profitMargins") else None,
            "div_yield": info.get("dividendYield"),
            "market_cap": (round(mc_local * fx) if (mc_local and fx) else None),
            "fcf": (round(fcf_local * fx) if (fcf_local and fx) else None),
            "currency": "USD" if fx else cur,
            "reported_currency": cur,
            "fx_to_usd": fx,
            "market_cap_local": mc_local,
            "fcf_local": fcf_local,
            "beta": info.get("beta"),
            "payout": round(info["payoutRatio"] * 100, 1) if info.get("payoutRatio") else None,
        }
    except Exception as e:
        print(f"  {ticker} info: {e}")
    try:
        inc = t.income_stmt
        years = [str(c)[:4] for c in inc.columns][::-1]
        def row(name):
            if name in inc.index:
                return [None if pd.isna(v) else round(float(v) / 1e9, 2)
                        for v in inc.loc[name]][::-1]
            return None
        fund["income4"] = {"years": years, "currency": cur,
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
    print("done.")


if __name__ == "__main__":
    main()
