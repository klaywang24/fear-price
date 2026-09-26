#!/usr/bin/env python3
"""个股长历史（PE / EPS / PB / ROE / FCF）：证监会 EDGAR 原始申报值 + 雅虎复权价，自建；2026-09-26 起替代 macrotrends（09-24 起整站 Cloudflare 人机验证，自动访问一律 403）。

产出与旧源同形状，build_fundamentals.py 下游（series_from / driver / carry_history）不改：
  {"pe-ratio": [[date, price, eps_ttm, pe], …], "ps-ratio": […], "price-book": […], "roe": […], "roic": [], "free-cash-flow": [[yyyy-12-31, fcf_$M], …]}

口径（2026-09-26 与旧源 27 只逐季对过，PE/EPS/PB 差异 ≤1.1%、ROE ≤0.9 点；对账脚本与基线见工作区 ops/）：
· 价格＝雅虎 Adj Close 月末（旧源即此口径：七个年份隐含价÷Adj Close 全部＝1.000）；日期标签＝财季末所在月的月末（英伟达 1/4/7/10、美光 2/5/8/11）。
· EPS_TTM＝四个单季之和；财报从不单独给第四季，Q4＝全年−前三季 YTD。每期取**当时原始申报值**（点时间口径），
  重述不回写：微软 2016–18 ASC 606 期与旧源有差（旧源把重述后全年减重述前 YTD 造出 0.86 的幻影季度）。
· 拆股：证监会存原始申报值，按申报日之后发生的拆股逐条回调（每股÷、股数×），与复权价对齐。
· PB＝价÷(总权益÷流通股)，总权益含少数股东（旧源口径；可口可乐 6.8%→0.4%、盈透 283%→0.7%）。
· ROE＝母公司净利 TTM ÷ 构成 TTM 的四个季末总权益均值（苹果五个时点精确相等）。
· FCF＝财年 OCF − 资本开支（净额，扣处置回款；只认 10-K，亚马逊每份 10-Q 也报 12 个月滚动值）。
  旧源对财年不按自然年的公司（苹果/微软/沃尔玛/好市多/家得宝/TJX/Visa/美光）把 12 月**单季**当成了全年，站上苹果一直显示 300–500 亿而非 ~1000 亿；本版改对。
· ROIC：旧源（Zacks 供 macrotrends）定义在付费墙后、网格搜索复现不了（最好 ±1.5 点），故按本站公开定义自算：NOPAT＝营业利润×(1−实际税率)（无营业利润用净利），投入资本＝总权益+长债(含一年内)+短期借款/商业票据−现金及等价物，取四个 TTM 季末平均；整条自证监会数据起算不缝合；银行/券商类（ROIC_NOT_APPLICABLE）不适用不显示，由 build_fundamentals 删键。
· 缝合：新序列首日之前沿用上一版已发布数据（1987 年起的 ROE 长史不丢）；新源若没接到上一版末端一年内则整段沿用。
· 冻结名单 FROZEN_TICKERS：台积电/法拉利（IFRS 本币年报）、LVMH/爱马仕（不向美国证监会申报）、伯克希尔/Visa（股数按类别拆开申报，汇总层为空）、闪迪/Circle（上市不足两年）。
证监会要求 UA 带联系方式、≤10 请求/秒。"""
import json, os, csv, time, statistics as st, datetime as dt
import requests

# ───────── 引擎：companyfacts → 季频/年频/时点 ─────────
import datetime as dt, os
PREFER = "earliest"   # 每期取当时原始申报值（点时间口径；重述不回写历史）
def _d(s): return dt.date.fromisoformat(s)
def _dur(a): return (_d(a["end"]) - _d(a["start"])).days
def _pick(cur, a):
    if cur is None: return a
    fa, fc = a.get("filed", ""), cur.get("filed", "")
    return (a if fa < fc else cur) if PREFER == "earliest" else (a if fa >= fc else cur)
def _tag_recs(facts, tag, unit):
    for ns in ("us-gaap", "ifrs-full", "dei"):
        f = facts["facts"].get(ns, {})
        if tag in f:
            units = f[tag]["units"]; u = unit if unit in units else next(iter(units)); return units[u]
    return []
def quarterly(facts, tags, unit=None, derive=True):
    single, ytd9, fy = {}, {}, {}
    for tag in tags:
        s1, y1, f1 = {}, {}, {}
        for a in _tag_recs(facts, tag, unit):
            if "start" not in a: continue
            d = _dur(a)
            if 80 <= d <= 100: s1[a["end"]] = _pick(s1.get(a["end"]), a)
            elif 260 <= d <= 290: y1[(a["start"], a["end"])] = _pick(y1.get((a["start"], a["end"])), a)
            elif 350 <= d <= 380: f1[(a["start"], a["end"])] = _pick(f1.get((a["start"], a["end"])), a)
        single.update({k: v["val"] for k, v in s1.items()}); ytd9.update({k: v["val"] for k, v in y1.items()}); fy.update({k: v["val"] for k, v in f1.items()})
    if derive:
        for (s, e), v in fy.items():
            if e in single: continue
            y = [vv for (ss, ee), vv in ytd9.items() if ss == s]
            if y: single[e] = v - y[-1]; continue
            qs = [vv for ee, vv in single.items() if s < ee < e]
            if len(qs) == 3: single[e] = v - sum(qs)
    return dict(sorted(single.items()))
def annual(facts, tags, unit=None):
    out = {}
    for tag in tags:
        o1 = {}
        for a in _tag_recs(facts, tag, unit):
            if "start" in a and 350 <= _dur(a) <= 380 and a.get("form","").startswith(("10-K","20-F","40-F")): o1[a["end"]] = _pick(o1.get(a["end"]), a)   # 只认年报：亚马逊等在每份 10-Q 里也报 12 个月滚动现金流
        out.update({k: v["val"] for k, v in o1.items()})
    return dict(sorted(out.items()))
def instant(facts, tags, unit=None):
    out = {}
    for tag in tags:
        o1 = {}
        for a in _tag_recs(facts, tag, unit):
            if "start" not in a: o1[a["end"]] = _pick(o1.get(a["end"]), a)
        out.update({k: v["val"] for k, v in o1.items()})
    return dict(sorted(out.items()))
def ttm(qd, end, span=380):
    ks = [k for k in qd if k <= end][-4:]
    if len(ks) < 4 or (_d(ks[-1]) - _d(ks[0])).days > span - 80: return None
    return sum(qd[k] for k in ks)
def near(inst, e, back=45, fwd=0):
    lo, hi = _d(e) - dt.timedelta(days=back), _d(e) + dt.timedelta(days=fwd)
    ks = [k for k in inst if lo <= _d(k) <= hi]
    return inst[min(ks, key=lambda k: abs((_d(k) - _d(e)).days))] if ks else None
def month_end_label(e):
    """旧源规则：财季末所在月的月末（6-27→6-30，1-26→1-31）；落在月初 ≤7 日的归上月（9-1→8-31）。"""
    d = _d(e)
    if d.day <= 7: d = d.replace(day=1) - dt.timedelta(days=1)
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return (nxt - dt.timedelta(days=1)).isoformat()

# ───────── 取数：证监会 / 雅虎 ─────────
import json, time, os, io, csv
import requests
SEC_UA = {"User-Agent": "fear-price.klay-wang.com research contact klaywang24@gmail.com", "Accept-Encoding": "gzip"}
EXTRA_CIK = {"BLK": [1364742]}          # 贝莱德 2022 换注册主体，旧主体另拉一份合并（旧在前、新覆盖）
_TICKERS = None

def cik_for(ticker):
    global _TICKERS
    if _TICKERS is None:
        r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_UA, timeout=30); r.raise_for_status()
        _TICKERS = {v["ticker"]: v["cik_str"] for v in r.json().values()}
    return _TICKERS.get(ticker.replace(".", "-"))

def companyfacts(cik):
    r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json", headers=SEC_UA, timeout=90)
    time.sleep(0.15); r.raise_for_status(); return r.json()

def merge_facts(base, extra):
    for ns, tags in extra["facts"].items():
        dst = base["facts"].setdefault(ns, {})
        for tag, body in tags.items():
            if tag in dst:
                for u, arr in body["units"].items(): dst[tag]["units"][u] = arr + dst[tag]["units"].get(u, [])
            else: dst[tag] = body
    return base

def get_facts(ticker):
    cik = cik_for(ticker)
    if not cik: raise RuntimeError(f"{ticker}: 证监会代码表无此票")
    F = companyfacts(cik)
    for old in EXTRA_CIK.get(ticker, []): F = merge_facts(companyfacts(old), F)
    return F

def _yf(ticker):
    import yfinance as yf
    return yf.Ticker("BRK-B" if ticker == "BRK.B" else ticker)

def get_prices_me(ticker):
    """月末 Adj Close（旧源口径：除息调整价）→ {yyyy-mm-dd: adj}；另返回最新交易日与价。"""
    import pandas as pd
    h = _yf(ticker).history(period="max", interval="1d", auto_adjust=False)
    if h.empty: raise RuntimeError(f"{ticker}: 雅虎无价格")
    if h.index.tz is not None: h.index = h.index.tz_localize(None)
    me = h["Adj Close"].resample("ME").last().dropna()
    prices = {d.date().isoformat(): round(float(v), 4) for d, v in me.items()}
    latest = {"last_date": h.index[-1].date().isoformat(), "last_adj": round(float(h["Adj Close"].iloc[-1]), 4)}
    return prices, latest

def get_splits(ticker):
    s = _yf(ticker).splits
    return [(d.date().isoformat(), float(r)) for d, r in s.items() if d.year >= 2000 and float(r) > 0]

# ───────── 标签族与冻结名单 ─────────
# 标签族。择一类（EPS/NI/权益）按列表顺序取第一个覆盖达标者；改名换代类（营收/资本开支/现金流/现金/债务）按顺序合并、后者覆盖。
EPS_TAGS = ["EarningsPerShareDiluted","IncomeLossFromContinuingOperationsPerDilutedShare","EarningsPerShareBasicAndDiluted","DilutedEarningsLossPerShare"]   # 择一·优先级从高到低：标准稀释 EPS 优先
NI_TAGS  = ["NetIncomeLoss","NetIncomeLossAvailableToCommonStockholdersBasic","ProfitLoss"]   # 择一：母公司净利优先，覆盖不全时退到「普通股可分配净利」（盈透）
REV_TAGS = ["Revenue","RevenuesNetOfInterestExpense","SalesRevenueGoodsNet","SalesRevenueNet","Revenues","RevenueFromContractWithCustomerExcludingAssessedTax"]
SH_INST  = ["NumberOfSharesOutstanding","EntityCommonStockSharesOutstanding","CommonStockSharesOutstanding"]
SH_DUR   = ["WeightedAverageNumberOfSharesOutstandingBasic","WeightedAverageNumberOfDilutedSharesOutstanding"]
OCF_TAGS = ["CashFlowsFromUsedInOperatingActivities","NetCashProvidedByUsedInOperatingActivitiesContinuingOperations","NetCashProvidedByUsedInOperatingActivities"]   # 2013–2016 年不少公司用「持续经营」口径标签
CX_TAGS  = ["PaymentsForProceedsFromProductiveAssets","PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities","PaymentsToAcquireProductiveAssets","PaymentsToAcquirePropertyPlantAndEquipment"]
PROC_TAGS= ["ProceedsFromSaleOfPropertyPlantAndEquipment","ProceedsFromSaleOfProductiveAssets","ProceedsFromSalesOfPropertyPlantAndEquipment"]   # 资本开支按净额：扣处置回款
OPI_TAGS = ["OperatingIncomeLoss"]; TAX_TAGS = ["IncomeTaxExpenseBenefit"]
CASH_TAGS= ["Cash","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents","CashAndCashEquivalentsAtCarryingValue"]
DEBT_NC  = ["LongTermDebtAndCapitalLeaseObligations","LongTermDebtNoncurrent"]; DEBT_C = ["LongTermDebtAndCapitalLeaseObligationsCurrent","LongTermDebtCurrent"]
DEBT_TOT = ["LongTermDebt"]; STB = ["ShortTermBorrowings","CommercialPaper"]
ROIC_EMIT = True    # 2026-09-26 Klay 定：按本站公开定义自算，不追旧源；整条自证监会数据起算，不与旧线缝合
ROIC_NOT_APPLICABLE = {"JPM","BAC","GS","MS","SCHW","IBKR","AXP","COIN","HOOD","CRCL","BRK.B"}   # 银行/券商/支付牌照类：资产负债表无「有息负债减现金」概念，此指标不适用，不显示
FROZEN_TICKERS = {"TSM","RACE","MC.PA","RMS.PA","BRK.B","SNDK","CRCL","V"}   # IFRS本币年报／无证监会申报／多类别股无汇总股数／上市不足两年 → 长历史沿用上一版（carry_history 负责）
FIRST = "2005-01-01"

# ───────── 构建 ─────────
def split_adjust(F, splits):
    """按申报日回调：申报日之后的拆股 → 每股值÷累计比、股数×累计比。"""
    if not splits: return F
    def cum(filed): 
        c = 1.0
        for d, r in splits:
            if d > filed: c *= r
        return c
    for ns in ("us-gaap","ifrs-full","dei"):
        for tag, body in F["facts"].get(ns, {}).items():
            per_share = tag in EPS_TAGS or "PerShare" in tag
            shares = tag in SH_INST or tag in SH_DUR
            if not (per_share or shares): continue
            for u, arr in body["units"].items():
                for a in arr:
                    c = cum(a.get("filed", a["end"]))
                    if c != 1.0: a["val"] = a["val"] / c if per_share else a["val"] * c
    return F

def prev_rows(B, key, with_price=False):
    s = B.get(key)
    if not s or not s.get("dates"): return []
    if with_price:
        eps = dict(zip(B.get("eps",{}).get("dates",[]), B.get("eps",{}).get("values",[])))
        return [[d, (v*eps[d]) if (d in eps and v) else "", eps.get(d,""), v] for d, v in zip(s["dates"], s["values"])]
    return [[d, "", "", v] for d, v in zip(s["dates"], s["values"])]

def stitch(prev, new):
    """新序列首日之前沿用旧数据；新源若没接到旧序列末端一年内（如伯克希尔 2015 后断档）则整段沿用旧数据。"""
    if not new: return prev
    real_prev = [r for r in prev if r[2] != "" or r[3] != ""]
    if prev and new[-1][0] < (max(r[0] for r in real_prev)[:4] + "-01-01" if real_prev else "0000"): return prev
    first = new[0][0]
    return [r for r in prev if r[0] < first] + new

def pick_one(F, tags, unit, kind="q", prefer_last=False):
    """同类替代标签择一：取覆盖最全的；prefer_last=True 时若最后一个标签覆盖≥其他的 80% 则优先它（总权益）。"""
    fn = quarterly if kind == "q" else instant
    cands = [(tag, fn(F, [tag], unit)) for tag in tags]
    cands = [(tag, d) for tag, d in cands if d]
    if not cands: return {}, None
    mx = max(len(d) for _, d in cands)
    if prefer_last and cands[-1][0] == tags[-1] and len(cands[-1][1]) >= 0.8 * mx: return cands[-1][1], cands[-1][0]
    best = next(c for c in cands if len(c[1]) >= 0.8 * mx)     # 按传入顺序取第一个覆盖 ≥ 最大值 80% 的（盈透：母公司口径优先于含少数股东）
    return best[1], best[0]

def build(t, prev, src, do_stitch=True):
    B = prev or {}; flags = []
    F = split_adjust(src.facts(t), src.splits(t))
    Q = lambda tags, u="USD": quarterly(F, tags, u); I = lambda tags, u="USD": instant(F, tags, u)
    EPS, eps_tag = pick_one(F, EPS_TAGS, "USD/shares"); NI, ni_tag = pick_one(F, NI_TAGS, "USD"); REV = Q(REV_TAGS); OPI = Q(OPI_TAGS); TAX = Q(TAX_TAGS)
    OCF = Q(OCF_TAGS); CX = Q(CX_TAGS); OCFa = annual(F, OCF_TAGS, "USD"); CXa = annual(F, CX_TAGS, "USD"); PRa = annual(F, PROC_TAGS, "USD")
    EQ, eq_tag = pick_one(F, ["StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], "USD", "i", prefer_last=True)
    SHi = I(SH_INST, "shares"); SHd = quarterly(F, SH_DUR, "shares", derive=False); CASH = I(CASH_TAGS)
    if ni_tag and ni_tag != "NetIncomeLoss": flags.append("NI=" + ni_tag[:14])
    if eq_tag == "StockholdersEquity": flags.append("EQ=母")
    Dnc, Dc, Dt, Sb = I(DEBT_NC), I(DEBT_C), I(DEBT_TOT), I(STB)
    prices, latest = src.prices(t)
    def shares_at(e):
        v = near(SHi, e, 10, 0)            # 资产负债表日同日
        if v is None: v = near(SHd, e, 10, 0)   # 单季加权稀释股数
        if v is None: v = near(SHi, e, 0, 75)   # dei 封面日在季末后
        return v
    if not EPS and NI:                     # 多类别股（Visa/伯克希尔）：EPS = 单季净利/股数
        for e, v in NI.items():
            sh = shares_at(e)
            if sh: EPS[e] = v / sh
        if EPS: flags.append("eps=NI/股数")
        else: flags.append("无EPS")
    def debt_at(k):
        nc, c = near(Dnc, k), near(Dc, k)
        d = (nc or 0) + (c or 0) if nc is not None else (near(Dt, k) or 0)
        return d + (near(Sb, k) or 0)
    rows = {"pe-ratio": [], "ps-ratio": [], "price-book": [], "roe": [], "roic": [], "free-cash-flow": []}
    last_eps = None
    for e in [k for k in EPS if k >= FIRST]:
        eps, ni = ttm(EPS, e), ttm(NI, e)
        if eps is None: continue
        L = month_end_label(e); p = prices.get(L)
        if p is None: continue
        ks = [k for k in EPS if k <= e][-4:]
        rows["pe-ratio"].append([L, round(p, 2), round(eps, 2), round(p / eps, 2) if eps > 0 else 0.0]); last_eps = eps
        sh = shares_at(e); eq = near(EQ, e)
        if eq and sh: rows["price-book"].append([L, round(p, 2), round(eq / sh, 2), round(p / (eq / sh), 2)])
        rev = ttm(REV, e)
        if rev and sh and rev > 0: rows["ps-ratio"].append([L, round(p, 2), round(rev / sh, 2), round(p / (rev / sh), 2)])
        if ni is None: continue
        eqs = [near(EQ, k) for k in ks]
        if all(eqs) and st.mean(eqs) != 0: rows["roe"].append([L, ni, st.mean(eqs), round(ni / st.mean(eqs) * 100, 2)])
        opi, tax = ttm(OPI, e), ttm(TAX, e)
        nopat = opi * (1 - tax / (ni + tax)) if (opi is not None and tax is not None and (ni + tax) > 0 and opi > 0) else ni
        ic = []
        for k in ks:
            eqk = near(EQ, k)
            if eqk is None: ic = []; break
            ic.append(eqk + debt_at(k) - (near(CASH, k) or 0))
        if ROIC_EMIT and t not in ROIC_NOT_APPLICABLE and ic and st.mean(ic) > 0: rows["roic"].append([L, round(nopat / 1e6, 1), round(st.mean(ic) / 1e6, 1), round(nopat / st.mean(ic) * 100, 2)])
    if rows["pe-ratio"] and latest and last_eps:
        rows["pe-ratio"].append([latest["last_date"], latest["last_adj"], "", round(latest["last_adj"] / last_eps, 2) if last_eps > 0 else 0.0])
    for fe, v in OCFa.items():
        if fe < FIRST: continue
        cx = CXa.get(fe)
        if cx is None: cx = ttm(CX, fe) or 0
        cx -= PRa.get(fe) or 0
        rows["free-cash-flow"].append([f"{fe[:4]}-12-31", round((v - cx) / 1e6, 1)])
    if not Dnc and not Dt: flags.append("无债务标签")
    if not ROIC_EMIT: flags.append("roic沿用上一版")
    if rows["pe-ratio"] and rows["pe-ratio"][0][0] > "2012-01-01": flags.append(f"起{rows['pe-ratio'][0][0][:4]}")
    rows["_n_new"] = len(rows["pe-ratio"])
    if do_stitch and B:
        rows["pe-ratio"]   = stitch(prev_rows(B, "pe", True), rows["pe-ratio"])
        rows["price-book"] = stitch(prev_rows(B, "pb_hist"), rows["price-book"])
        rows["roe"]        = stitch(prev_rows(B, "roe"), rows["roe"])
        # roic 不缝合：本站定义与旧源不同，整条自证监会数据起算（≈2009），避免接缝；不适用票留空由上游删键
    return rows, flags

class NetSource:
    """CI 用：现拉。"""
    def facts(self, t): return get_facts(t)
    def prices(self, t): return get_prices_me(t)
    def splits(self, t): return get_splits(t)

class CacheSource:
    """本地复算用：读取工作区缓存目录（facts/ prices_me/ _latest.json _splits.json）。"""
    def __init__(self, d): self.d = d
    def facts(self, t):
        F = json.load(open(f"{self.d}/facts/{t}.json", encoding="utf-8"))
        if t == "BLK" and os.path.exists(f"{self.d}/facts/BLK_old.json"): F = merge_facts(json.load(open(f"{self.d}/facts/BLK_old.json", encoding="utf-8")), F)
        return F
    def prices(self, t):
        out = {}
        for r in csv.reader(open(f"{self.d}/prices_me/{t}.csv")):
            if r and r[0][:1].isdigit(): out[r[0][:10]] = float(r[2])
        return out, json.load(open(f"{self.d}/prices/_latest.json")).get(t)
    def splits(self, t): return [tuple(x) for x in json.load(open(f"{self.d}/prices/_splits.json")).get(t, [])]

def history_rows(ticker, prev=None, src=None):
    """给 build_fundamentals 用：返回与旧源同形状的六页行；冻结票返回 {}（由 carry_history 沿用上一版）。"""
    if ticker in FROZEN_TICKERS: return {}
    rows, flags = build(ticker, prev, src or NetSource())
    if flags: print(f"  {ticker} history: {' '.join(flags)}")
    return {k: v for k, v in rows.items() if not k.startswith("_")}

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--validate":      # 离线复算：与工作区验证产物比对
        d = sys.argv[2]; os.makedirs(f"{d}/out_repo", exist_ok=True); src = CacheSource(d)
        for t in json.load(open(f"{d}/tickers.json")):
            if t in FROZEN_TICKERS: continue
            p = f"{d}/baseline/s_{t.lower().replace('.', '-')}_fund.json"
            prev = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
            json.dump(history_rows(t, prev, src), open(f"{d}/out_repo/{t}.json", "w"), ensure_ascii=False)
        print("validate: 写入", f"{d}/out_repo/")
    else:
        t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
        r = history_rows(t); print({k: (len(v), v[-1] if v else None) for k, v in r.items()})
