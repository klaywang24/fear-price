#!/usr/bin/env python3
"""个股长历史（PE / EPS / PB / ROE / FCF）：SEC EDGAR 原始申报值 + 雅虎收盘价（拆股已调），自建；2026-09-26 起替代 macrotrends（09-24 起整站 Cloudflare 人机验证，自动访问一律 403）。

产出与旧源同形状，build_fundamentals.py 下游（series_from / driver / carry_history）不改：
  {"pe-ratio": [[date, price, eps_ttm, pe], …], "ps-ratio": […], "price-book": […], "roe": […], "roic": [], "free-cash-flow": [[yyyy-12-31, fcf_$M], …]}

口径（2026-09-26 与旧源 27 只逐季对过，PE/EPS/PB 差异 ≤1.1%、ROE ≤0.9 点；对账脚本与基线见工作区 ops/）：
· 价格＝雅虎月末收盘价（Close，只做拆股调整、不做除息调整）；2026-09-27 Klay 定与专业终端看齐：估值比率用当天真实价。
  旧源用的是除息调整价（Adj Close），越早越低估（KO 1995 年 12 月两者差 119%），已发布的旧值按当日 Close÷Adj Close 换算过。
  日期标签＝财季末所在月的月末（英伟达 1/4/7/10、美光 2/5/8/11）。
· EPS_TTM＝四个单季之和；财报从不单独给第四季，Q4＝全年−前三季 YTD。每期取**当时原始申报值**（点时间口径），
  重述不回写：微软 2016–18 ASC 606 期与旧源有差（旧源把重述后全年减重述前 YTD 造出 0.86 的幻影季度）。
· 拆股：SEC 存原始申报值，按申报日之后发生的拆股逐条回调（每股÷、股数×），与拆股调整后的收盘价对齐。
· PB＝价÷(总权益÷流通股)，总权益含少数股东（旧源口径；可口可乐 6.8%→0.4%、盈透 283%→0.7%）。
· ROE＝母公司净利 TTM ÷ 构成 TTM 的四个季末总权益均值（苹果五个时点精确相等）。
· FCF＝财年 OCF − 资本开支（净额，扣处置回款；只认 10-K，亚马逊每份 10-Q 也报 12 个月滚动值）。
  旧源对财年不按自然年的公司（苹果/微软/沃尔玛/好市多/家得宝/TJX/Visa/美光）把 12 月**单季**当成了全年，站上苹果一直显示 300–500 亿而非 ~1000 亿；本版改对。
· ROIC：旧源（Zacks 供 macrotrends）定义在付费墙后、网格搜索复现不了（最好 ±1.5 点），故按本站公开定义自算：NOPAT＝营业利润×(1−实际税率)（无营业利润用净利），投入资本＝总权益+长债(含一年内)+短期借款/商业票据−现金及等价物，取四个 TTM 季末平均；整条自 SEC 数据起算不缝合；银行/券商类（ROIC_NOT_APPLICABLE）不适用不显示，由 build_fundamentals 删键。
· 缝合：新序列首日之前沿用上一版已发布数据（1987 年起的 ROE 长史不丢）；新源若没接到上一版末端一年内则整段沿用。
· 冻结名单 FROZEN_TICKERS（3 只）：LVMH/爱马仕（不向 SEC 申报）、Circle（上市一年，新旧两边差一倍无法判定）。闪迪 2026-09-26 解冻；台积电/法拉利 2026-09-27 改为 IFRS_OWN。
· 台积电/法拉利（IFRS_OWN，2026-09-27）：旧源退役，全部本站自算——20-F 年报 IFRS 结构化数据（2015 起）逐年一个点：EPS＝稀释 EPS×每 ADR 股数×年末汇率，PB 用母公司权益÷封面股数（缺年用母公司净利÷基本 EPS），ROE＝净利÷期初期末权益均值，ROIC 年度版（投入资本取期初期末平均；债券＋长短期借款，不含租赁），FCF 同前；台积电最新年报之后按季度往后接（下条）。
· Visa（CLASS_A_EPS，2026-09-27 解冻）：每股口径＝公司公布的 A 类稀释 EPS，读每份 10-Q/10-K 原件实例里 StatementClassOfStockAxis＝ClassA 的那条；股数＝单季净利÷单季 A 类 EPS（即公司把 B/C 按转换比例折成 A 类的分母）；财年末 EPS 与年报逐年相等（FY2024 9.73、FY2025 10.20）。每周现读约 70 份原件。
· SEC 汇总接口漏收最新一期时（2026-09-26 实证：可口可乐 Q2 10-Q 报送两个月仍不在 companyfacts），自动读那份报告的 XBRL 实例补上（supplement_latest）；读失败不影响本次，沿用汇总接口。
· 台积电（QUARTERLY_FOREIGN）2026-09-26 起季度往后接：雅虎季报（每 ADR、新台币，近 5 季）按期末月末汇率折美元，追加晚于最新年报的季度；追加前用台湾证交所开放接口的官方累计 EPS 核对（差 >1% 或接口不通则不追加，下周自动补）；2026-03/06 两季与旧源对照 EPS -1.6%/-0.6%、PE ≈1%、ROE ≈0.5 点。年报此后只补 FCF。
· 冻结票每周仍刷新 PE 末点（最新价 ÷ 最后一期 EPS，旧源亦如此）；台积电/法拉利按 20-F 年报（IFRS 本币×每 ADR 股数×汇率）逐年追加晚于旧序列末点的年度行（TSM 年报值与旧季度值财年末对照差 ≤1%）；LVMH/爱马仕旧序列本就无 EPS，原样沿用。
· 伯克希尔：SEC EPS 标签 2013 后停更且股数按类别申报，用雅虎 B 股等价流通股（2015-11 起，与旧源反推股数一致）算 NI/股数；PE/EPS/ROE 与旧源精确相符，更早缝合上一版；PB 不缝合（旧源 PB 错）。
SEC 要求 UA 带联系方式、≤10 请求/秒。"""
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
    for ns in ("us-gaap", "ifrs-full", "dei", "legacy"):
        f = facts["facts"].get(ns, {})
        if tag in f:
            units = f[tag]["units"]; u = unit if unit in units else next(iter(units)); return units[u]
    return []
def quarterly(facts, tags, unit=None, derive=True):
    """单季流量，键＝财季末。先收 80–100 天单季；再按同一财年起点的累计数（半年/九个月/全年）相邻相减补缺季。均值类（加权股数）须 derive=False。"""
    single, cum = {}, {}
    for tag in tags:
        s1, c1 = {}, {}
        for a in _tag_recs(facts, tag, unit):
            if "start" not in a: continue
            d = _dur(a)
            if 80 <= d <= 100: s1[a["end"]] = _pick(s1.get(a["end"]), a)
            elif 170 <= d <= 190 or 260 <= d <= 290 or 350 <= d <= 380: c1[(a["start"], a["end"])] = _pick(c1.get((a["start"], a["end"])), a)
        single.update({k: v["val"] for k, v in s1.items()})      # 标签清单按「后者覆盖」排优先级（与 annual/instant 同规则）；09-26 曾误改成先到先得，营收等族优先级整体倒置，09-27 改回
        cum.update({k: v["val"] for k, v in c1.items()})
    if derive:
        starts = {}
        for (s0, e), v in cum.items(): starts.setdefault(s0, {})[e] = v
        for s0, ends in starts.items():
            pts = []
            q1 = [e for e in single if 80 <= (_d(e) - _d(s0)).days <= 100]
            if q1: pts.append((min(q1), single[min(q1)]))
            pts += sorted(ends.items())
            pts = sorted(dict(pts).items())
            for (e0, c0), (e1, c1_) in zip(pts, pts[1:]):
                if e1 not in single and 80 <= (_d(e1) - _d(e0)).days <= 100: single[e1] = c1_ - c0
            fy = [(e, v) for e, v in ends.items() if 350 <= (_d(e) - _d(s0)).days <= 380]
            for e, v in fy:
                if any(abs((_d(k) - _d(e)).days) <= 10 for k in single): continue   # 同一季已有（高盛 2008 财年后来的年报把 11-28 改标 11-30：再减一次会凭空多出一季 3.23，2009-06/09 两个 TTM EPS 各多算一季）
                qs = [vv for ee, vv in single.items() if s0 < ee < e]
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
    """滚动四季。🔴 2026-09-27 修：最后一季必须在期末前 45 天内。原先只取「期末之前最后四季」、不查离期末多远，
    公司停报某科目后会一直沿用好几年前的四季：TJX 2019 起不报营业利润 ⇒ 2019–2026 共 30 季投入资本回报率用的是
    2018 年营业利润（站上已发布过错值）；美银 2015 后不报资本开支 ⇒ 重算时 2015–2025 每年自由现金流多扣 11.55 亿。
    现在算不出就不给（ROIC 按既定规则退到净利，FCF 年度资本开支缺失且近四季也没有时按 0 计）。"""
    ks = [k for k in qd if k <= end][-4:]
    if len(ks) < 4 or (_d(ks[-1]) - _d(ks[0])).days > span - 70: return None   # 首尾季末正常相距 ~273 天；放到 310 是给换财年的过渡月（高盛 2008-12 单月不算季，四个财季首尾 301 天）；缺一季会 ≥360 天，仍拒
    if (_d(end) - _d(ks[-1])).days > 45: return None
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

# ───────── 取数：SEC / 雅虎 ─────────
import json, time, os, io, csv
import requests
SEC_UA = {"User-Agent": "fear-price.klay-wang.com research contact klaywang24@gmail.com", "Accept-Encoding": "gzip"}
EXTRA_CIK = {"BLK": [1364742], "GOOGL": [1288776], "AVGO": [1441634, 1649338]}   # 前身主体：贝莱德 2022 换主体；谷歌 2015 改组 Alphabet；博通前身 Avago(2009–15)、Broadcom Ltd(2016–18)          # 贝莱德 2022 换注册主体，旧主体另拉一份合并（旧在前、新覆盖）
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

def _latest_periodic(cik):
    """submissions 里最近一份 10-Q/10-K：(期末日, 报送日, accession)。"""
    r = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=SEC_UA, timeout=60); time.sleep(0.15); r.raise_for_status()
    rec = r.json()["filings"]["recent"]
    for f, rp, fd, acc in zip(rec["form"], rec["reportDate"], rec["filingDate"], rec["accessionNumber"]):
        if f in ("10-Q", "10-K"): return rp, fd, f, acc
    return None

def _instance_facts(cik, acc, form, filed):
    """直接读报告原件的 XBRL 实例（*_htm.xml），取无维度的 us-gaap/dei 事实，转成 companyfacts 记录形状。"""
    import re as _re
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    idx = requests.get(f"{base}/index.json", headers=SEC_UA, timeout=60).json(); time.sleep(0.15)
    inst = [x["name"] for x in idx["directory"]["item"] if x["name"].endswith("_htm.xml")]
    if not inst: return {}
    x = requests.get(f"{base}/{inst[0]}", headers=SEC_UA, timeout=90).text; time.sleep(0.15)
    ctx = {}
    for m in _re.finditer(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', x, _re.S):
        body = m.group(2)
        if "segment" in body or "scenario" in body: continue
        s_ = _re.search(r'<(?:xbrli:)?startDate>([^<]+)<', body); e_ = _re.search(r'<(?:xbrli:)?endDate>([^<]+)<', body); i_ = _re.search(r'<(?:xbrli:)?instant>([^<]+)<', body)
        ctx[m.group(1)] = (s_.group(1), e_.group(1)) if s_ and e_ else ((None, i_.group(1)) if i_ else None)
    out = {"us-gaap": {}, "dei": {}}
    for m in _re.finditer(r'<(us-gaap|dei):([A-Za-z0-9]+)\b([^>]*)>([^<]+)</\1:\2>', x):
        ns, tag, attrs, val = m.groups()
        c = _re.search(r'contextRef="([^"]+)"', attrs)
        if not c or not ctx.get(c.group(1)): continue
        try: v = float(val)
        except ValueError: continue
        start, end = ctx[c.group(1)]
        unit = "USD/shares" if ("PerShare" in tag or tag in EPS_TAGS) else ("shares" if "Shares" in tag and "PerShare" not in tag else "USD")
        rec = {"end": end, "val": v, "form": form, "filed": filed, "src": "instance"}
        if start: rec["start"] = start
        out[ns].setdefault(tag, {"units": {}})["units"].setdefault(unit, []).append(rec)
    return out

def supplement_latest(ticker, F, cik):
    """SEC 汇总接口有时漏收最新一期（2026-09-26 实证：可口可乐 7-29 报送的 Q2 10-Q 带 XBRL，两个月后 companyfacts 里仍没有）。
    submissions 里最新 10-Q/10-K 的期末晚于 companyfacts 净利末期时，直接读那份报告的 XBRL 实例补进去；原有记录照旧（earliest 规则下旧记录优先）。"""
    ni = [a["end"] for tag in ("NetIncomeLoss", "ProfitLoss") for a in (F["facts"].get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", []))]
    last = max(ni) if ni else ""
    lp = _latest_periodic(cik)
    if not lp or lp[0] <= last: return F, None
    rp, fd, form, acc = lp
    extra = _instance_facts(cik, acc, form, fd)
    if not extra.get("us-gaap"): return F, None
    return merge_facts(F, {"facts": extra}), f"补读原件 {form} 期末{rp}"

CLASS_A_EPS = {"V"}   # 2026-09-27 Klay 定：Visa 每股口径＝公司公布的 A 类稀释 EPS（B/C 类已按转换比例折进分母）；汇总接口按股份类别申报、汇总层为空，只能读报告原件

def _periodic_filings(cik, since="2009-01-01", forms=("10-Q", "10-K")):
    """submissions（含翻页）里全部 10-Q/10-K：[(期末, 报送日, 表格, accession)]，按报送日排序。"""
    r = requests.get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", headers=SEC_UA, timeout=60); time.sleep(0.15); r.raise_for_status()
    j = r.json(); blocks = [j["filings"]["recent"]]
    for f in j["filings"].get("files", []):
        b = requests.get(f"https://data.sec.gov/submissions/{f['name']}", headers=SEC_UA, timeout=60); time.sleep(0.15); b.raise_for_status(); blocks.append(b.json())
    out = set()
    for b in blocks:
        for f, rp, fd, acc in zip(b["form"], b["reportDate"], b["filingDate"], b["accessionNumber"]):
            if f in forms and fd >= since: out.add((rp, fd, f, acc))
    return sorted(out, key=lambda x: x[1])

def _instance_xml(cik, acc):
    """报告原件的 XBRL 实例全文：2019 年后是 *_htm.xml，之前是不带 _cal/_def/_lab/_pre 的那份 .xml。"""
    import re as _re
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    idx = requests.get(f"{base}/index.json", headers=SEC_UA, timeout=60).json(); time.sleep(0.15)
    names = [x["name"] for x in idx["directory"]["item"]]
    inst = [n for n in names if n.endswith("_htm.xml")] or [n for n in names if n.endswith(".xml") and not _re.search(r"_(cal|def|lab|pre)\.xml$|FilingSummary", n)]
    if not inst: return ""
    x = requests.get(f"{base}/{inst[0]}", headers=SEC_UA, timeout=120).text; time.sleep(0.15)
    return x

def class_a_from_instance(x, form, filed):
    """一份实例里：A 类稀释 EPS（StatementClassOfStockAxis 只挂一个 ClassA 成员）＋无维度的股东权益与净利，转成 companyfacts 形状。"""
    import re as _re
    ctx = {}
    for m in _re.finditer(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', x, _re.S):
        body = m.group(2)
        mem = _re.findall(r'dimension="([^"]+)"[^>]*>([^<]+)<', body)
        s_ = _re.search(r'<(?:xbrli:)?startDate>([^<]+)<', body); e_ = _re.search(r'<(?:xbrli:)?endDate>([^<]+)<', body); i_ = _re.search(r'<(?:xbrli:)?instant>([^<]+)<', body)
        per = (s_.group(1), e_.group(1)) if s_ and e_ else ((None, i_.group(1)) if i_ else None)
        if not per: continue
        if not mem: ctx[m.group(1)] = ("", per)
        elif len(mem) == 1 and mem[0][0].endswith("StatementClassOfStockAxis") and _re.search(r"ClassA(Common)?(Stock)?Member$", mem[0][1]): ctx[m.group(1)] = ("A", per)
    out = {"us-gaap": {}}
    want = {("A", "EarningsPerShareDiluted"): "USD/shares", ("", "StockholdersEquity"): "USD", ("", "NetIncomeLoss"): "USD",
            ("", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"): "USD"}   # Visa 近年只报含少数股东的权益
    for m in _re.finditer(r'<us-gaap:([A-Za-z0-9]+)\b([^>]*)>([^<]+)</us-gaap:\1>', x):
        tag, attrs, val = m.groups()
        c = _re.search(r'contextRef="([^"]+)"', attrs)
        if not c or c.group(1) not in ctx: continue
        kind, (start, end) = ctx[c.group(1)]
        unit = want.get((kind, tag))
        if not unit: continue
        try: v = float(val)
        except ValueError: continue
        rec = {"end": end, "val": v, "form": form, "filed": filed, "src": "instance-A" if kind else "instance"}
        if start: rec["start"] = start
        out["us-gaap"].setdefault(tag, {"units": {}})["units"].setdefault(unit, []).append(rec)
    return out

INSTANCE_FILL = {"GOOGL": ("eps_a", "2015-04-01", "2016-01-31"),   # Alphabet 改组那三季（2015 Q2–Q4）EPS 只按股份类别申报、汇总层空：读原件 A 类稀释 EPS，只补这个窗口
                 "HOOD": ("cover_shares", "2021-07-01", "9999-12-31")}   # 两类股，汇总层期末股数缺或记 0：读每份 10-Q/10-K 资产负债表日 A＋B 类流通股之和（与其他公司同为期末股数；原先退到季度加权数，IPO 当季少算股本）
def cover_shares_from_instance(x, form, filed):
    """一份实例里的流通股：资产负债表日 CommonStockSharesOutstanding 与封面 dei:EntityCommonStockSharesOutstanding；有无维度总数用总数，否则把各股份类别相加。返回 {标签: [记录]}。"""
    import re as _re
    ctx = {}
    for m in _re.finditer(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', x, _re.S):
        body = m.group(2); i_ = _re.search(r'<(?:xbrli:)?instant>([^<]+)<', body)
        mem = _re.findall(r'dimension="([^"]+)"[^>]*>([^<]+)<', body)
        if not i_: continue
        if not mem: ctx[m.group(1)] = (i_.group(1), "")
        elif len(mem) == 1 and mem[0][0].endswith("StatementClassOfStockAxis"): ctx[m.group(1)] = (i_.group(1), mem[0][1])
    res = {}
    for pre, tag, name in (("dei", "EntityCommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"), ("us-gaap", "CommonStockSharesOutstanding", "CommonStockSharesOutstandingSumOfClasses")):
        tot, cls = {}, {}
        for m in _re.finditer(rf'<{pre}:{tag}\b([^>]*)>([^<]+)<', x):
            c = _re.search(r'contextRef="([^"]+)"', m.group(1))
            if not c or c.group(1) not in ctx: continue
            d, mem = ctx[c.group(1)]
            try: v = float(m.group(2))
            except ValueError: continue
            if mem: cls.setdefault(d, {})[mem] = v
            else: tot[d] = v
        for d in set(tot) | set(cls):
            v = tot.get(d) or sum(cls.get(d, {}).values())
            if v > 0: res.setdefault(name, []).append({"end": d, "val": v, "form": form, "filed": filed, "src": "instance-shares"})
    return res
def instance_fill_facts(t, fetch):
    """INSTANCE_FILL 里的票：只读窗口内的原件，补汇总接口缺的那几项；fetch(cik, acc) 返回实例全文。"""
    kind, lo, hi = INSTANCE_FILL[t]; eps, cov = [], {}
    for cik in [cik_for(t)] + EXTRA_CIK.get(t, []):
        for rp, fd, form, acc in _periodic_filings(cik):
            if not (lo <= rp <= hi): continue
            x = fetch(cik, acc)
            if not x: continue
            if kind == "eps_a":
                f = class_a_from_instance(x, form, fd)["us-gaap"].get("EarningsPerShareDiluted", {}).get("units", {}).get("USD/shares", [])
                eps += [r for r in f if r.get("src") == "instance-A" and lo <= r["end"] <= hi]
            else:
                for k, v in cover_shares_from_instance(x, form, fd).items(): cov.setdefault(k, []).extend(v)
    g = {}
    if eps: g["us-gaap"] = {"EarningsPerShareDiluted": {"units": {"USD/shares": eps}}}
    if cov.get("CommonStockSharesOutstandingSumOfClasses"): g.setdefault("us-gaap", {})["CommonStockSharesOutstandingSumOfClasses"] = {"units": {"shares": cov["CommonStockSharesOutstandingSumOfClasses"]}}
    if cov.get("EntityCommonStockSharesOutstanding"): g["dei"] = {"EntityCommonStockSharesOutstanding": {"units": {"shares": cov["EntityCommonStockSharesOutstanding"]}}}
    return {"facts": g}

IFRS_TAGS = ["DilutedEarningsLossPerShare", "BasicEarningsLossPerShare", "ProfitLoss", "ProfitLossAttributableToOwnersOfParent", "Equity",
             "EquityAttributableToOwnersOfParent", "CashFlowsFromUsedInOperatingActivities", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
             "ProfitLossFromOperatingActivities", "IncomeTaxExpenseContinuingOperations", "CashAndCashEquivalents", "Borrowings",
             "NoncurrentPortionOfNoncurrentBondsIssued", "CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued", "LongtermBorrowings",
             "CurrentPortionOfLongtermBorrowings", "ShorttermBorrowings"]
def ifrs_from_instance(x, cur, form, filed):
    """20-F 实例 → companyfacts 形状（只取无维度、报表币种的值；同一数常另附美元便利折算，按计量单位剔掉）。"""
    import re as _re
    ctx, units = {}, {}
    for m in _re.finditer(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', x, _re.S):
        body = m.group(2)
        if "explicitMember" in body or "typedMember" in body: continue
        s_ = _re.search(r'<(?:xbrli:)?startDate>([^<]+)<', body); e_ = _re.search(r'<(?:xbrli:)?endDate>([^<]+)<', body); i_ = _re.search(r'<(?:xbrli:)?instant>([^<]+)<', body)
        ctx[m.group(1)] = (s_.group(1), e_.group(1)) if s_ and e_ else ((None, i_.group(1)) if i_ else None)
    for m in _re.finditer(r'<(?:xbrli:)?unit id="([^"]+)">(.*?)</(?:xbrli:)?unit>', x, _re.S):
        ms = [v.replace("xbrli:", "") for v in _re.findall(r'<(?:xbrli:)?measure>([^<]+)<', m.group(2))]   # 股数单位有的带 xbrli: 前缀、有的不带（台积电 2025 年报）
        units[m.group(1)] = f"{cur}/shares" if ms == [f"iso4217:{cur}", "shares"] else (cur if ms == [f"iso4217:{cur}"] else ("shares" if ms == ["shares"] else None))
    out = {"ifrs-full": {}, "dei": {}}
    for m in _re.finditer(r'<(ifrs-full|dei):([A-Za-z]+)\b([^>]*)>([^<]+)</\1:\2>', x):
        ns, tag, attrs, val = m.groups()
        if (ns == "ifrs-full" and tag not in IFRS_TAGS) or (ns == "dei" and tag != "EntityCommonStockSharesOutstanding"): continue
        c = _re.search(r'contextRef="([^"]+)"', attrs); u = _re.search(r'unitRef="([^"]+)"', attrs)
        if not c or not u or not ctx.get(c.group(1)) or not units.get(u.group(1)): continue
        try: v = float(val)
        except ValueError: continue
        start, end = ctx[c.group(1)]; unit = units[u.group(1)]
        rec = {"end": end, "val": v, "form": form, "filed": filed, "src": "instance-ifrs"}
        if start: rec["start"] = start
        out[ns].setdefault(tag, {"units": {}})["units"].setdefault(unit, []).append(rec)
    return {k: v for k, v in out.items() if v}
def ifrs_supplement(t, F, cik, fetch):
    """IFRS_OWN：SEC 汇总接口漏收最新 20-F 时（2026-09-27 实证：台积电 2025 年报 04-16 报送仍不在 companyfacts）读那份原件补上。"""
    cur = ANNUAL_IFRS[t][0]
    have = max((a["end"] for a in _tag_recs(F, "DilutedEarningsLossPerShare", f"{cur}/shares") if "start" in a), default="")
    for rp, fd, form, acc in sorted(_periodic_filings(cik, forms=("20-F",)), key=lambda r: r[0])[-2:]:
        if rp > have:
            x = fetch(cik, acc)
            if x: F = merge_facts(F, {"facts": ifrs_from_instance(x, cur, form, fd)}); print(f"  {t} 补读原件 20-F 期末{rp}")
    return F

def class_a_facts(cik, fetch=None):
    """全部 10-Q/10-K 原件读 A 类口径；fetch(acc) 可换成本地缓存。"""
    F = {"facts": {"us-gaap": {}}}
    for rp, fd, form, acc in _periodic_filings(cik):
        x = fetch(acc) if fetch else _instance_xml(cik, acc)
        if x: F = merge_facts(F, {"facts": class_a_from_instance(x, form, fd)})
    return F

def get_facts(ticker):
    cik = cik_for(ticker)
    if not cik: raise RuntimeError(f"{ticker}: SEC 代码表无此票")
    F = companyfacts(cik)
    for old in EXTRA_CIK.get(ticker, []): F = merge_facts(companyfacts(old), F)
    if ticker in CLASS_A_EPS: F = merge_facts(F, class_a_facts(cik))
    if ticker in INSTANCE_FILL: F = merge_facts(F, instance_fill_facts(ticker, _instance_xml))
    if ticker in IFRS_OWN:
        try: F = ifrs_supplement(ticker, F, cik, _instance_xml)
        except Exception as ex: print(f"  {ticker} 补读 20-F 原件失败（不影响本次，沿用汇总接口）：{type(ex).__name__}: {str(ex)[:80]}")
    if ticker not in FROZEN_TICKERS and ticker not in IFRS_OWN:
        try:
            F, note = supplement_latest(ticker, F, cik)
            if note: print(f"  {ticker} {note}")
        except Exception as ex:
            print(f"  {ticker} 补读原件失败（不影响本次，沿用汇总接口）：{type(ex).__name__}: {str(ex)[:80]}")
    return F

def _yf(ticker):
    import yfinance as yf
    return yf.Ticker("BRK-B" if ticker == "BRK.B" else ticker)

def get_prices_me(ticker):
    """月末收盘价 Close（拆股已调、分红未调＝当天真实价）→ {yyyy-mm-dd: close}；另返回最新交易日与价。"""
    import pandas as pd
    h = _yf(ticker).history(period="max", interval="1d", auto_adjust=False)
    if h.empty: raise RuntimeError(f"{ticker}: 雅虎无价格")
    if h.index.tz is not None: h.index = h.index.tz_localize(None)
    me = h["Close"].resample("ME").last().dropna()
    prices = {d.date().isoformat(): round(float(v), 4) for d, v in me.items()}
    latest = {"last_date": h.index[-1].date().isoformat(), "last_close": round(float(h["Close"].iloc[-1]), 4)}
    return prices, latest

def get_shares_history(ticker):
    """雅虎流通股逐日历史（拆股已调）→ {yyyy-mm-dd: shares}；只对 SHARES_FROM_YAHOO 用。"""
    s = _yf(ticker).get_shares_full(start="2005-01-01")
    if s is None or len(s) == 0: return {}
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return {d.date().isoformat(): float(v) for d, v in s.items()}

def get_fred_daily(series_id):
    """FRED 日频序列 → [(yyyy-mm-dd, float)]。有 FRED_API_KEY（Actions Secrets）走官方接口，没有则走 fredgraph.csv；取不到就抛错，不拿别的源顶替。"""
    key = os.environ.get("FRED_API_KEY")
    if key:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": series_id, "api_key": key, "file_type": "json"}, timeout=60)
        r.raise_for_status()
        return [(o["date"], float(o["value"])) for o in r.json()["observations"] if o["value"] not in (".", "")]
    r = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", timeout=60)
    r.raise_for_status()
    return [(a, float(b)) for a, b in csv.reader(io.StringIO(r.text)) if a[:1].isdigit() and b not in (".", "")]

def fred_month_end(obs):
    """日频 → 自然月月末键（与 pandas resample("ME") 同一键），值取该月最后一个有效观测。"""
    me = {}
    for d, v in sorted(obs):
        y, m = int(d[:4]), int(d[5:7])
        last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).isoformat()
        me[last] = v
    return me

def get_fx_me(cur):
    """月末汇率：一单位外币值多少美元 → {yyyy-mm-dd: usd}。
    🔴 2026-09-28 新台币改用美联储 H.10（FRED DEXTAUS，每美元兑新台币）：雅虎 TWD=X 的 2014-12-31 是坏点（3.67，实为 31.6，倒数后放大 8 倍多），
    2004-11～2006-04 断档，2015 年末 31.99 与美联储 32.79 差 2.4%（台积电 FY2015 EPS 因此偏高 2.4%）。FRED 自 1983 年起，与 20-F 里印的美联储汇率同源。
    取不到就抛错：台积电本周不出新值、沿用上一版并触发 --check-carried 报警，不退回雅虎。欧元（法拉利）雅虎序列无跳点，未改。"""
    if cur == "TWD":
        return {k: 1 / v for k, v in fred_month_end(get_fred_daily("DEXTAUS")).items() if v > 0}
    sym = {"TWD": "TWD=X", "EUR": "EURUSD=X"}[cur]
    h = _yf(sym).history(period="max", interval="1d", auto_adjust=False)
    if h.index.tz is not None: h.index = h.index.tz_localize(None)
    me = h["Close"].resample("ME").last().dropna()
    if cur == "TWD": me = 1 / me
    return {d.date().isoformat(): float(v) for d, v in me.items()}

def get_yahoo_quarterly(ticker):
    """雅虎季报（外国票，报表币种）：{"eps":{季末:每ADR EPS}, "ni":{}, "eq":{}, "sh":{}}；只有最近约 5 季。"""
    import pandas as pd
    t = _yf(ticker); q, b = t.quarterly_income_stmt, t.quarterly_balance_sheet
    def row(df, name): return {str(c.date()): float(v) for c, v in df.loc[name].items() if pd.notna(v)} if name in df.index else {}
    return {"eps": row(q, "Diluted EPS"), "ni": row(q, "Net Income Common Stockholders") or row(q, "Net Income"),
            "eq": row(b, "Stockholders Equity"), "sh": row(b, "Ordinary Shares Number")}

def get_twse_latest(code):
    """台湾证交所开放接口（官方）：该公司最新一季的累计基本 EPS（每普通股、新台币）→ (期末日, eps)；拿不到返回 None。"""
    UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
    r = requests.get("https://openapi.twse.com.tw/v1/opendata/t187ap14_L", headers=UA, timeout=30); r.raise_for_status()
    row = next((x for x in r.json() if x.get("公司代號") == code), None)
    if not row: return None
    y = int(row["年度"]) + 1911; qn = int(row["季別"])
    end = {1: f"{y}-03-31", 2: f"{y}-06-30", 3: f"{y}-09-30", 4: f"{y}-12-31"}[qn]
    return end, float(row["基本每股盈餘(元)"])

def get_splits(ticker):
    s = _yf(ticker).splits
    return [(d.date().isoformat(), float(r)) for d, r in s.items() if d.year >= 1985 and float(r) > 0]

# ───────── 标签族与冻结名单 ─────────
# 标签族。择一类（EPS/NI/权益）按列表顺序取第一个覆盖达标者；改名换代类（营收/资本开支/现金流/现金/债务）按顺序合并、后者覆盖。
EPS_TAGS = ["EarningsPerShareDiluted","IncomeLossFromContinuingOperationsPerDilutedShare","EarningsPerShareBasicAndDiluted","DilutedEarningsLossPerShare","IncomeLossFromContinuingOperationsPerBasicAndDilutedShare"]   # 择一·优先级从高到低：标准稀释 EPS 优先
NI_TAGS  = ["NetIncomeLoss","NetIncomeLossAvailableToCommonStockholdersBasic","ProfitLoss"]   # 择一：母公司净利优先，覆盖不全时退到「普通股可分配净利」（盈透）
REV_TAGS = ["Revenue","RevenuesNetOfInterestExpense","SalesRevenueGoodsNet","SalesRevenueNet","Revenues","RevenueFromContractWithCustomerIncludingAssessedTax","RevenueFromContractWithCustomerExcludingAssessedTax"]
# 2026-09-27 补 IncludingAssessedTax：TJX 2017 起只报含代收销售税的营收，原清单没有它 ⇒ 2018-08 起 33 季营收取不到、市销率沿用 2018 年旧营收。
#   ⚠️ 09-27 更正：我写这行时以为合并是「后者覆盖」，但 f0cbf232 起 quarterly() 已是先到先得（setdefault），
#   本清单整体顺序（连同 OCF/CX/CASH/DEBT 各族）都按旧规则排，优先级实际已倒置（麦当劳营收取成直营销售）。
#   test_build_fundamentals 两条行为测试锁住意图：总营收优先于子项、不含税优先于含税——现在是红的，
#   weekly.yml 会在测试这步停下不发布，待基本面线按先到先得重排各族清单后转绿。
SH_INST  = ["NumberOfSharesOutstanding","EntityCommonStockSharesOutstanding","CommonStockSharesOutstanding","CommonStockSharesOutstandingSumOfClasses"]   # 末项只由原件补读产生（多类别股各类相加）
SH_DUR   = ["WeightedAverageNumberOfSharesOutstandingBasic","WeightedAverageNumberOfDilutedSharesOutstanding"]
OCF_TAGS = ["CashFlowsFromUsedInOperatingActivities","NetCashProvidedByUsedInOperatingActivitiesContinuingOperations","NetCashProvidedByUsedInOperatingActivities"]   # 2013–2016 年不少公司用「持续经营」口径标签
CX_TAGS  = ["PaymentsForProceedsFromProductiveAssets","PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities","PaymentsToAcquireProductiveAssets","PaymentsToAcquirePropertyPlantAndEquipment"]
PROC_TAGS= ["ProceedsFromSaleOfPropertyPlantAndEquipment","ProceedsFromSaleOfProductiveAssets","ProceedsFromSalesOfPropertyPlantAndEquipment"]   # 资本开支按净额：扣处置回款
OPI_TAGS = ["OperatingIncomeLoss"]; TAX_TAGS = ["IncomeTaxExpenseBenefit"]
CASH_TAGS= ["Cash","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents","CashAndCashEquivalentsAtCarryingValue"]
DEBT_NC  = ["LongTermDebtAndCapitalLeaseObligations","LongTermDebtNoncurrent"]; DEBT_C = ["LongTermDebtAndCapitalLeaseObligationsCurrent","LongTermDebtCurrent"]
DEBT_TOT = ["LongTermDebt"]; STB = ["ShortTermBorrowings","CommercialPaper"]
ROIC_EMIT = True    # 2026-09-26 Klay 定：按本站公开定义自算，不追旧源；整条自 SEC 数据起算，不与旧线缝合
FROZEN_PRICE_TICKER = {"MC.PA": "LVMUY", "RMS.PA": "HESAY"}   # 旧源用 ADR（美元）算的，刷新末点也用 ADR 价
ANNUAL_IFRS = {"TSM": ("TWD", 5), "RACE": ("EUR", 1)}            # 20-F 年报：报表币种、每 ADR 对应普通股数
QUARTERLY_FOREIGN = {"TSM": {"cur": "TWD", "ratio": 5, "twse": "2330"}}   # 2026-09-26 Klay 定：台积电季度往后接（雅虎季报＋台湾证交所官方累计 EPS 核对）
PB_NO_STITCH = {"BRK.B"}        # 旧源伯克希尔 PB 错（按 B 股数量未折算 A 股，算出 0.9 倍；实际约 1.5 倍），不缝合，只用新算
SHARES_FROM_YAHOO = {"BRK.B"}   # SEC 接口把按股份类别申报的股数整个剔除；伯克希尔用雅虎 B 股等价流通股（2015-11 起，与旧源反推股数一致），更早缝合上一版
ROIC_NOT_APPLICABLE = {"JPM","BAC","GS","MS","SCHW","IBKR","AXP","COIN","HOOD","CRCL","BRK.B"}   # 银行/券商/支付牌照类：资产负债表无「有息负债减现金」概念，此指标不适用，不显示
HISTORY_BASIS = {"TSM": "tw_gaap_ifrs"}   # 2026-09-28：台积电 FY2012 及以前的主报表是台湾会计准则（2008 年前员工分红不计入费用，利润偏高），FY2013 起 IFRS；页面图注按此代码写明
IFRS_KEEP_BEFORE = {"TSM": "2015-01-01"}   # 2026-09-28 Klay 令补台积电 2015 前历史：FY2000–2014 从 20-F 原件（HTML，无 XBRL）读出、本地算好发布，此日期前的已发布点每周原样保留（同美股 keep_published_before）
IFRS_OWN = {"TSM", "RACE"}   # 2026-09-27 Klay 定：旧源退役，改为 20-F 年报（IFRS 结构化数据，2015 起）逐年自算，每年一个点；台积电年报之后按季度往后接
FROZEN_TICKERS = {"MC.PA","RMS.PA","CRCL"}   # 2026-09-27 Visa 解冻（CLASS_A_EPS 读原件 A 类口径）   # 2026-09-26 闪迪解冻（新算与旧线对齐：ROE 逐季相同、EPS 末季差 0.7%）   # IFRS本币年报／无 SEC 申报／多类别股无汇总股数／上市不足两年 → 长历史沿用上一版（carry_history 负责）
FROZEN_WHY = {"MC.PA": "no_sec", "RMS.PA": "no_sec", "CRCL": "young"}   # 2026-09-28 Klay 定三只继续挂旧数并标来源：原因代码给页面图注用（no_sec＝不向 SEC 申报，本站没有原始财报可算；young＝上市不足两年，本站能算的季度还不够）。改冻结名单时两处一起改，test_build_fundamentals 会查键是否一致
FROZEN_SOURCE = "macrotrends（数据商 Zacks）"   # 冻结序列的来源：09-19 最后一次从 macrotrends 抓到的版本，其底层数据商是 Zacks
FIRST = "1994-01-01"   # 2026-09-26：老报告（附件 27 与正文表格）补到 1995 年起

# ───────── 构建 ─────────
def split_adjust(F, splits):
    """按申报日回调：申报日之后的拆股 → 每股值÷累计比、股数×累计比。"""
    if not splits: return F
    def cum(filed): 
        c = 1.0
        for d, r in splits:
            if d > filed: c *= r
        return c
    for ns in ("us-gaap","ifrs-full","dei","legacy"):
        for tag, body in F["facts"].get(ns, {}).items():
            per_share = tag in EPS_TAGS or "PerShare" in tag
            shares = tag in SH_INST or tag in SH_DUR or (tag.startswith("Legacy") and "Shares" in tag and "PerShare" not in tag)
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
    """新数优先；旧数只填新数没覆盖到的日期（前 60 天内无新点的空档，含新序列起点之前）。
    新源若没接到旧序列末端一年内（如伯克希尔 2015 后断档）则整段沿用旧数据。"""
    if not new: return prev
    real_prev = [r for r in prev if r[2] != "" or r[3] != ""]
    if prev and new[-1][0] < (max(r[0] for r in real_prev)[:4] + "-01-01" if real_prev else "0000"): return prev
    nd = sorted(_d(r[0]) for r in new)
    import bisect
    def covered(x):
        dx = _d(x); i = bisect.bisect_left(nd, dx)
        return any(0 <= j < len(nd) and abs((nd[j] - dx).days) <= 60 for j in (i - 1, i))
    keep = [r for r in prev if not covered(r[0]) and r[0] <= new[-1][0]]
    return sorted(keep + new, key=lambda r: r[0])

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

def fill_series(base, extra, tol=None):
    """base 优先；extra 只补 base 缺的季度。tol：两者重叠季中位偏差超过 tol 就不补（口径不同，如含少数股东损益）。"""
    if not extra: return base
    if tol is not None:
        ok_ = [k for k in sorted(base) if k in extra and base[k]]; ov = [abs(extra[k] / base[k] - 1) for k in ok_]
        if ov and st.median(ov) > tol:
            # 全部重叠不达标时只许「往后接」：最近 8 个重叠季口径一致，才补基准末期之后的季度，历史选择一律不动
            # （博通：母公司净利 2019 停报、普通股可分配净利 2024-02 停报，含少数股东净利 2018 后与之逐位相同，但早年合伙架构差 5%）
            rec = ov[-8:]
            if base and len(rec) >= 4 and st.median(rec) <= tol:
                lo = ok_[-8:][0]; out = dict(base); out.update({k: v for k, v in extra.items() if k > lo and k not in base})   # 09-27 改：最近一致窗口内的空档也补（好市多含少数股东权益 2024-09 与 2025-08 之间停报三季），窗口前的历史照旧不动
                return dict(sorted(out.items()))
            return base
    out = dict(extra); out.update(base); return dict(sorted(out.items()))

# 补缺顺序：含少数股东净利与母公司净利对得上时先用它（口径＝优先股分红前），对不上再退到普通股可分配净利（博通 2020：29.60 亿 vs 26.63 亿）
NI_FILL_ORDER = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
EQ_FILL_ORDER = ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]

def fill_ni(F, NI, ni_tag):
    """净利：pick_one 选出的主标签之外，按 NI_FILL_ORDER 接备用标签（站点 build 与 Pro 包 sec_financials 共用，2026-09-28 从 build 内提出）。"""
    for tg in NI_FILL_ORDER:
        if tg != ni_tag: NI = fill_series(NI, quarterly(F, [tg], "USD"), 0.02)
    return NI

def fill_eq(F, EQ, eq_tag):
    """总权益：同上，时点科目。"""
    for tg in EQ_FILL_ORDER:
        if tg != eq_tag: EQ = fill_series(EQ, instant(F, [tg], "USD"), 0.02)
    return EQ

def build(t, prev, src, do_stitch=True):
    B = prev or {}; flags = []
    F = src.facts(t)
    LEG = src.legacy(t) if hasattr(src, "legacy") else {}
    if LEG: F = merge_facts(F, {"facts": {"legacy": LEG}})
    AEQ = quarterly(F, ["WeightedAverageNumberOfSharesOutstandingBasic"], "shares", derive=False) if t in SHARES_FROM_YAHOO else {}   # 伯克希尔自报「平均 A 股等价股数」（拆股调整前取原值）
    if AEQ:                                # 原件数量级标错的丢掉：2011-06、2011-09、2012-03 三份把 1,649,052 股标成 1.649 万亿（与中位数差一倍以上即弃，取前一季）
        md = st.median(AEQ.values()); AEQ = {k: v for k, v in AEQ.items() if 0.5 <= v / md <= 2}
    F = split_adjust(F, src.splits(t))
    Q = lambda tags, u="USD": quarterly(F, tags, u); I = lambda tags, u="USD": instant(F, tags, u)
    EPS, eps_tag = pick_one(F, EPS_TAGS, "USD/shares"); NI, ni_tag = pick_one(F, NI_TAGS, "USD"); REV = Q(REV_TAGS); OPI = Q(OPI_TAGS); TAX = Q(TAX_TAGS)
    OCF = Q(OCF_TAGS); CX = Q(CX_TAGS); OCFa = annual(F, OCF_TAGS, "USD"); CXa = annual(F, CX_TAGS, "USD"); PRa = annual(F, PROC_TAGS, "USD")
    EQ, eq_tag = pick_one(F, ["StockholdersEquity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], "USD", "i", prefer_last=True)
    SHi = I(SH_INST, "shares"); SHd = quarterly(F, SH_DUR, "shares", derive=False); CASH = I(CASH_TAGS)
    SHi = {k: v for k, v in SHi.items() if v and v > 0}; SHd = {k: v for k, v in SHd.items() if v and v > 0}   # 多类别股汇总层会记 0（Robinhood 2021）：0 股当缺，不当「没有市净率」
    fill = fill_series      # 2026-09-28：原本就地定义的补缺函数提到模块层（Pro 包要调同一份），函数体一字未改
    for tg in EPS_TAGS:
        if tg != eps_tag: EPS = fill(EPS, quarterly(F, [tg], "USD/shares"), 0.02)
    NI = fill_ni(F, NI, ni_tag); EQ = fill_eq(F, EQ, eq_tag)
    # XBRL 起点：该公司第一份带结构化数据的定期报告的本期末。之前的期在 XBRL 里只是后来年报的比较数（可能已重述）。
    cur = [a["end"] for tg in ("NetIncomeLoss", "ProfitLoss", "EarningsPerShareDiluted") for a in _tag_recs(F, tg, "USD" if tg != "EarningsPerShareDiluted" else "USD/shares")
           if "start" in a and a.get("form", "").startswith(("10-Q", "10-K")) and "filed" in a and 0 <= (_d(a["filed"]) - _d(a["end"])).days <= 120]
    cutoff = min(cur) if cur else "0000"
    if LEG:
        def prefer(base, extra):
            out = dict(base); bk = sorted(base)
            for k, v in extra.items():
                near_k = [b for b in bk if abs((_d(b) - _d(k)).days) <= 20]
                if near_k:
                    if k < cutoff: out[near_k[0]] = v        # 同一期：XBRL 之前以原件为准
                else: out[k] = v                               # 缺的期：补上
            ek = sorted(extra)
            for b in bk:                                       # 换财年的公司（MS 2009 改日历季）：XBRL 比较数落在另一套季度网格上，老报告已覆盖的那段不混进来
                gap = min((abs((_d(b) - _d(k)).days) for k in ek), default=999)
                if b < cutoff and 20 < gap < 70 and any(0 < (_d(b) - _d(k)).days <= 100 for k in ek) and any(0 < (_d(k) - _d(b)).days <= 100 for k in ek):
                    out.pop(b, None)                           # 离老报告季末 20–70 天＝夹在两套网格之间；离 90 天左右＝老报告缺的那季，留着
            return dict(sorted(out.items()))
        n0 = len(NI)
        EPS = prefer(EPS, quarterly(F, ["LegacyEarningsPerShareDiluted"], "USD/shares"))
        NI = prefer(NI, quarterly(F, ["LegacyNetIncomeLoss"], "USD"))
        EQ = prefer(EQ, instant(F, ["LegacyStockholdersEquity"], "USD"))
        REV = prefer(REV, quarterly(F, ["LegacyRevenues"], "USD"))
        SHd = prefer(SHd, quarterly(F, ["LegacyWeightedAverageNumberOfDilutedSharesOutstanding"], "shares", derive=False))
        e1 = quarterly(F, ["LegacyEarningsPerShareDiluted"], "USD/shares", derive=False); n1 = quarterly(F, ["LegacyNetIncomeLoss"], "USD", derive=False)
        imp = {e: n1[e] / e1[e] for e in e1 if e in n1 and e < cutoff and abs(e1[e]) >= 0.10 and near(SHd, e, 10, 0) is None}
        if imp: SHd = dict(sorted({**imp, **SHd}.items())); flags.append(f"老报告股数缺{len(imp)}季→单季净利÷单季稀释EPS")   # 两个数都是公司原件直接报的单季数（不用累计相减推出的季），EPS 两位小数、|EPS|≥0.10 时股数误差≤5%
        flags.append(f"老报告补{len(NI) - n0}季·原件优先至{cutoff}")
    if t in CLASS_A_EPS:   # 股数＝单季净利 ÷ 单季 A 类稀释 EPS＝公司把 B/C 按转换比例折成 A 类后的分母（2026-06 季 56.28 亿÷2.97≈18.95 亿，公司公布 18.98 亿）
        SHi = {}; SHd = {e: NI[e] / EPS[e] for e in EPS if e in NI and abs(EPS[e]) >= 0.05}
        flags.append("A类口径")
    if ni_tag and ni_tag != "NetIncomeLoss": flags.append("NI=" + ni_tag[:14])
    if eq_tag == "StockholdersEquity": flags.append("EQ=母")
    Dnc, Dc, Dt, Sb = I(DEBT_NC), I(DEBT_C), I(DEBT_TOT), I(STB)
    prices, latest = src.prices(t)
    YSH = src.shares(t) if t in SHARES_FROM_YAHOO else {}
    def shares_at(e):
        if YSH:                            # 伯克希尔：SEC 股数标签是 A 股折算数（百万级），与 B 股口径不同；雅虎 B 股等价（2015-11 起）日期不规则，季末前 120 天/后 45 天取最近
            v = near(YSH, e, 120, 45)
            if v is None and AEQ:          # 更早：公司自报平均 A 股等价股数 ×1500（1 A＝1500 B；2015-09 的 1,643,316×1500 与雅虎 2015-11 的 24.65 亿差 0.003%）
                a = near(AEQ, e, 100, 0) or near(AEQ, e, 280, 280); v = a * 1500 if a else None   # 第四季度没有单季数取前一季（2010-02 收购 BNSF 增发，不能往后取）；三季数量级标错已丢的，再取前后最近一季（2010–15 各季 164.7–165.2 万，差 <0.3%）
            return v
        v = near(SHi, e, 10, 0)            # 资产负债表日同日
        if v is None: v = near(SHd, e, 10, 0)   # 单季加权稀释股数
        if v is None: v = near(SHi, e, 0, 75)   # dei 封面日在季末后
        return v
    if EPS and NI and max(EPS) < max(NI)[:4] + "-01-01" and int(max(NI)[:4]) - int(max(EPS)[:4]) >= 2:
        flags.append(f"EPS标签{max(EPS)[:4]}后停更→NI/股数"); EPS = {}
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
    byL = {}
    for e in sorted(set(EPS) | set(NI)):
        if e >= FIRST: byL[month_end_label(e)] = e          # 同一月末标签取最后那个财季末
    rows["_ttm"] = {}                                      # 内部用（上线闸算隐含股数）：未四舍五入的 TTM 净利/每股收益
    for L, e in sorted(byL.items()):
        p = prices.get(L)
        eps = ttm(EPS, e) if e in EPS else None
        rows["_ttm"][L] = (ttm(NI, e) if e in NI else None, eps)
        if eps is not None and p is not None:
            rows["pe-ratio"].append([L, round(p, 2), round(eps, 2), round(p / eps, 2) if eps > 0 else 0.0]); last_eps = eps
        sh = shares_at(e); eq = near(EQ, e)
        if p is not None and eq and sh: rows["price-book"].append([L, round(p, 2), round(eq / sh, 2), round(p / (eq / sh), 2)])
        rev = ttm(REV, e)
        if p is not None and rev and sh and rev > 0: rows["ps-ratio"].append([L, round(p, 2), round(rev / sh, 2), round(p / (rev / sh), 2)])
        ni = ttm(NI, e) if e in NI else None
        if ni is None: continue
        ks = [k for k in NI if k <= e][-4:]
        eqs = [near(EQ, k) for k in ks]
        if all(eqs) and st.mean(eqs) != 0: rows["roe"].append([L, ni, st.mean(eqs), round(ni / st.mean(eqs) * 100, 2)])
        elif LEG and e < cutoff:           # 老报告只给年末权益（10-Q 资产负债表没解析到）：按公司年报口径＝近四季净利 ÷（期初＋期末权益）/2，两端都须是原件数
            q0, q1 = near(EQ, e, 10, 0), near(EQ, (_d(e) - dt.timedelta(days=365)).isoformat(), 10, 10)
            if q0 and q1 and q0 + q1 != 0: rows["roe"].append([L, ni, (q0 + q1) / 2, round(ni / ((q0 + q1) / 2) * 100, 2)])
        opi, tax = ttm(OPI, e), ttm(TAX, e)
        nopat = opi * (1 - tax / (ni + tax)) if (opi is not None and tax is not None and (ni + tax) > 0 and opi > 0) else ni
        ic = []
        for k in ks:
            eqk = near(EQ, k)
            if eqk is None: ic = []; break
            ic.append(eqk + debt_at(k) - (near(CASH, k) or 0))
        if ROIC_EMIT and t not in ROIC_NOT_APPLICABLE and ic and st.mean(ic) > 0: rows["roic"].append([L, round(nopat / 1e6, 1), round(st.mean(ic) / 1e6, 1), round(nopat / st.mean(ic) * 100, 2)])
    if rows["pe-ratio"] and latest and last_eps:
        rows["pe-ratio"].append([latest["last_date"], latest["last_close"], "", round(latest["last_close"] / last_eps, 2) if last_eps > 0 else 0.0])
    for fe, v in OCFa.items():
        if fe < FIRST: continue
        cx = CXa.get(fe)
        if cx is None: cx = ttm(CX, fe) or 0
        cx -= PRa.get(fe) or 0
        rows["free-cash-flow"].append([f"{fe[:4]}-12-31", round((v - cx) / 1e6, 1)])
    if not Dnc and not Dt: flags.append("无债务标签")
    if not ROIC_EMIT: flags.append("roic沿用上一版")
    if rows["pe-ratio"] and rows["pe-ratio"][0][0] > "2012-01-01": flags.append(f"起{rows['pe-ratio'][0][0][:4]}")
    rows["_n_new"] = len(rows["pe-ratio"]); rows["_cutoff"] = cutoff
    def keep_published_before(prev, new):
        """XBRL 起点之前：已发布的值是历史记录（原件或已核对的老报告），每周更新不改写；只在已发布没有的日期用新算的补。"""
        if LEG or not prev: return new
        pv = {r[0]: r for r in prev if r[0] < cutoff}
        out = [r for r in new if r[0] >= cutoff or not any(abs((_d(r[0]) - _d(k)).days) <= 20 for k in pv)]
        return sorted(out + list(pv.values()), key=lambda r: r[0])
    if do_stitch and B:
        rows["pe-ratio"] = keep_published_before(prev_rows(B, "pe", True), rows["pe-ratio"])
        rows["price-book"] = keep_published_before(prev_rows(B, "pb_hist"), rows["price-book"])
        rows["roe"] = keep_published_before(prev_rows(B, "roe"), rows["roe"])
        rows["pe-ratio"]   = stitch(prev_rows(B, "pe", True), rows["pe-ratio"])
        rows["price-book"] = rows["price-book"] if t in PB_NO_STITCH else stitch(prev_rows(B, "pb_hist"), rows["price-book"])
        rows["roe"]        = stitch(prev_rows(B, "roe"), rows["roe"])
        # roic 不缝合：本站定义与旧源不同，整条自 SEC 数据起算（≈2009），避免接缝；不适用票留空由上游删键
    return rows, flags

class NetSource:
    """CI 用：现拉。"""
    def facts(self, t): return get_facts(t)
    def shares(self, t): return get_shares_history(t)
    def fx(self, cur): return get_fx_me(cur)
    def yq(self, t): return get_yahoo_quarterly(t)
    def twse(self, code): return get_twse_latest(code)
    def prices(self, t): return get_prices_me(t)
    def splits(self, t): return get_splits(t)

class CacheSource:
    """本地复算用：读取工作区缓存目录（facts/ prices_me/ _latest.json _splits.json）。"""
    def __init__(self, d): self.d = d
    def facts(self, t):
        F = json.load(open(f"{self.d}/facts/{t}.json", encoding="utf-8"))
        if t == "BLK" and os.path.exists(f"{self.d}/facts/BLK_old.json"): F = merge_facts(json.load(open(f"{self.d}/facts/BLK_old.json", encoding="utf-8")), F)
        for cik in EXTRA_CIK.get(t, []):
            p = f"{self.d}/facts/{t}__{cik}.json"
            if os.path.exists(p): F = merge_facts(json.load(open(p, encoding="utf-8")), F)
        if t in CLASS_A_EPS:               # 原件缓存在 <d>/visa/<accession>.xml，缺的现下现存
            cik = cik_for(t); d = f"{self.d}/visa"; os.makedirs(d, exist_ok=True)
            def fetch(acc):
                p = f"{d}/{acc}.xml"
                if not os.path.exists(p): open(p, "w", encoding="utf-8").write(_instance_xml(cik, acc))
                return open(p, encoding="utf-8").read()
            F = merge_facts(F, class_a_facts(cik, fetch))
        if t in INSTANCE_FILL:
            d = f"{self.d}/instance_fill"; os.makedirs(d, exist_ok=True)
            def fetch2(cik, acc):
                p = f"{d}/{acc}.xml"
                if not os.path.exists(p): open(p, "w", encoding="utf-8").write(_instance_xml(cik, acc))
                return open(p, encoding="utf-8").read()
            F = merge_facts(F, instance_fill_facts(t, fetch2))
        if t in IFRS_OWN:
            d = f"{self.d}/instance_fill"; os.makedirs(d, exist_ok=True)
            def fetch3(cik, acc):
                p = f"{d}/{acc}.xml"
                if not os.path.exists(p): open(p, "w", encoding="utf-8").write(_instance_xml(cik, acc))
                return open(p, encoding="utf-8").read()
            F = ifrs_supplement(t, F, cik_for(t), fetch3)
        return F
    def legacy(self, t):
        """老报告（2009 年前）解析结果；目录由环境变量 LEGACY_DIR 指定，未指定则不用。标签改名进 legacy 命名空间，只补缺不改选择。"""
        d = os.environ.get("LEGACY_DIR")
        if not d or not os.path.exists(f"{d}/{t}.json"): return {}
        g = json.load(open(f"{d}/{t}.json", encoding="utf-8"))["facts"].get("us-gaap", {})
        return {"Legacy" + k: v for k, v in g.items()}
    def prices(self, t):
        out = {}
        for r in csv.reader(open(f"{self.d}/prices_me/{t}.csv")):
            if r and r[0][:1].isdigit(): out[r[0][:10]] = float(r[1])      # 列：Date,close,adj —— 用 close
        return out, json.load(open(f"{self.d}/prices/_latest.json")).get(t)
    def splits(self, t): return [tuple(x) for x in json.load(open(f"{self.d}/prices/_splits.json")).get(t, [])]
    def fx(self, cur):
        return {r[0][:10]: float(r[1]) for r in csv.reader(open(f"{self.d}/prices/_fx_{cur}.csv")) if r and r[0][:1].isdigit()}
    def yq(self, t): return get_yahoo_quarterly(t)       # 本地复算也现拉（雅虎只给近 5 季，缓存意义不大）
    def twse(self, code): return get_twse_latest(code)
    def shares(self, t):
        p = f"{self.d}/prices/_shares_{t}.csv"
        if not os.path.exists(p): return {}
        return {r[0][:10]: float(r[1]) for r in csv.reader(open(p)) if r and r[0][:1].isdigit()}

def annual_ifrs_rows(t, src, F):
    """台积电/法拉利：20-F 年报（IFRS、本币）→ 每 ADR 美元口径的年度行。EPS×每ADR股数×年末汇率；流量用财年 12 个月末汇率均值。"""
    cur, ratio = ANNUAL_IFRS[t]; fx = src.fx(cur); prices, _ = src.prices(t)
    EPS = annual(F, ["DilutedEarningsLossPerShare"], f"{cur}/shares"); NI = annual(F, ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"], cur)
    EQ = instant(F, ["Equity", "EquityAttributableToOwnersOfParent"], cur); OCF = annual(F, ["CashFlowsFromUsedInOperatingActivities"], cur)
    CX = annual(F, ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"], cur)
    SH = instant(F, ["EntityCommonStockSharesOutstanding"], "shares")   # 只用封面股数；法拉利 NumberOfSharesOutstanding 含库存股，PB 会偏三成
    BEPS = annual(F, ["BasicEarningsLossPerShare"], f"{cur}/shares"); NIo = annual(F, ["ProfitLossAttributableToOwnersOfParent"], cur)
    OPI = annual(F, ["ProfitLossFromOperatingActivities"], cur); TAX = annual(F, ["IncomeTaxExpenseContinuingOperations"], cur)
    CASH = instant(F, ["CashAndCashEquivalents"], cur)
    DEBT_PARTS = ["NoncurrentPortionOfNoncurrentBondsIssued", "CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued", "LongtermBorrowings", "CurrentPortionOfLongtermBorrowings", "ShorttermBorrowings"]
    DEBT_ALL = instant(F, ["Borrowings"], cur); PARTS = {k: instant(F, [k], cur) for k in DEBT_PARTS}
    def debt(fe):                          # 有「借款合计」用合计（法拉利），否则债券＋长短期借款逐项相加（台积电）；租赁负债不算（与美股口径一致）
        v = near(DEBT_ALL, fe, 10)
        return v if v is not None else sum(near(PARTS[k], fe, 10) or 0 for k in DEBT_PARTS)
    def shares(fe):                        # 封面股数缺的年份（台积电 2015–16、法拉利 2021 起）：母公司净利 ÷ 基本 EPS＝全年加权流通股
        v = near(SH, fe, 45, 120)
        if v is None and NIo.get(fe) and BEPS.get(fe): v = NIo[fe] / BEPS[fe]
        return v
    out = {"pe-ratio": [], "price-book": [], "roe": [], "roic": [], "free-cash-flow": []}
    for fe, eps in EPS.items():
        L = month_end_label(fe); p = prices.get(L); r_fx = near(fx, L, 40)
        if not p or not r_fx: continue
        eps_usd = eps * ratio * r_fx
        out["pe-ratio"].append([L, round(p, 2), round(eps_usd, 2), round(p / eps_usd, 2) if eps_usd > 0 else 0.0])
        eq, sh = near(EQ, fe), shares(fe)
        if eq and sh: bvps = eq / sh * ratio * r_fx; out["price-book"].append([L, round(p, 2), round(bvps, 2), round(p / bvps, 2)])
        ni = NI.get(fe); eq_prev = near(EQ, (_d(fe) - dt.timedelta(days=365)).isoformat(), 45)
        if ni and eq and eq_prev: out["roe"].append([L, ni, (eq + eq_prev) / 2, round(ni / ((eq + eq_prev) / 2) * 100, 2)])
        opi, tax, fe0 = OPI.get(fe), TAX.get(fe), (_d(fe) - dt.timedelta(days=365)).isoformat()
        if ni and eq and eq_prev and near(CASH, fe) is not None and near(CASH, fe0, 45) is not None:   # ROIC 年度版：本站定义，投入资本取期初期末平均（美股取四个季末平均）
            nopat = opi * (1 - tax / (ni + tax)) if (opi is not None and tax is not None and (ni + tax) > 0 and opi > 0) else ni
            ic = ((eq + debt(fe) - near(CASH, fe)) + (eq_prev + debt(fe0) - near(CASH, fe0, 45))) / 2
            if ic > 0: out["roic"].append([L, round(nopat / 1e6, 1), round(ic / 1e6, 1), round(nopat / ic * 100, 2)])
        ocf, cx = OCF.get(fe), CX.get(fe)
        months = [k for k in fx if (_d(fe) - dt.timedelta(days=365)) < _d(k) <= _d(fe)]
        if ocf is not None and cx is not None and months:
            out["free-cash-flow"].append([f"{fe[:4]}-12-31", round((ocf - cx) * st.mean(fx[k] for k in months) / 1e6, 1)])
    return out

def foreign_quarterly_rows(t, src, after):
    """台积电：雅虎季报 → 季度点（每 ADR 美元口径），只返回晚于 after[页] 的行。
    TTM EPS＝四季每 ADR EPS 之和 × 期末月末汇率（与旧源对照：-1.6%、-0.6%，三种折算法里最贴）；PB＝月末价 ÷（母公司权益 ÷ 普通股 × 每ADR股数 × 汇率）；ROE＝四季净利 ÷ 四季末权益均值。
    追加前用台湾证交所官方累计 EPS 核对当年各季之和（差 >1% 或接口不通则本周不追加，下周自动补）。"""
    cfg = QUARTERLY_FOREIGN[t]; Y = src.yq(t); fx = src.fx(cfg["cur"]); prices, _ = src.prices(t)
    qs = sorted(Y["eps"]); out = {"pe-ratio": [], "price-book": [], "roe": []}
    if len(qs) < 4: return out, "雅虎季报不足 4 季"
    try: tw = src.twse(cfg["twse"])
    except Exception as ex: return out, f"台湾证交所接口不通（{type(ex).__name__}），本周不追加"
    if not tw: return out, "台湾证交所无此代码"
    tw_end, tw_eps = tw
    ytd = [k for k in qs if k[:4] == tw_end[:4] and k <= tw_end]
    if tw_end in Y["eps"]:
        mine = sum(Y["eps"][k] for k in ytd) / cfg["ratio"]
        if abs(mine - tw_eps) / abs(tw_eps) > 0.01: return out, f"雅虎与台湾证交所不符（{mine:.2f} vs 官方 {tw_eps:.2f}），本周不追加"
        check = f"官方核对通过 {tw_end} 累计 EPS {tw_eps}"
    else:
        check = f"雅虎最新季 {qs[-1]} 尚无官方累计数（官方最新 {tw_end}），只追加已核对过的季度"
    for i in range(3, len(qs)):
        e = qs[i]
        if e > max(tw_end, qs[0]) : continue            # 只追加官方已公布（核对过）的季度
        four = qs[i - 3:i + 1]; L = month_end_label(e); p = prices.get(L); f = near(fx, e, 10, 5)
        if not p or not f: continue
        eps_usd = sum(Y["eps"][k] for k in four) * f
        if L > after.get("pe-ratio", ""): out["pe-ratio"].append([L, round(p, 2), round(eps_usd, 2), round(p / eps_usd, 2) if eps_usd > 0 else 0.0])
        if e in Y["eq"] and e in Y["sh"] and L > after.get("price-book", ""):
            bvps = Y["eq"][e] / Y["sh"][e] * cfg["ratio"] * f; out["price-book"].append([L, round(p, 2), round(bvps, 2), round(p / bvps, 2)])
        if all(k in Y["ni"] and k in Y["eq"] for k in four) and L > after.get("roe", ""):
            ni = sum(Y["ni"][k] for k in four); eqa = st.mean(Y["eq"][k] for k in four)
            out["roe"].append([L, ni, eqa, round(ni / eqa * 100, 2)])
    return out, check

def frozen_rows(t, prev, src):
    """冻结票：长历史沿用上一版（其它页留空由 carry_history 打标）；PE 末点按最新价 ÷ 最后一期 EPS 刷新；台积电/法拉利另追加晚于旧序列末点的年报行。"""
    rows = {}
    old = prev_rows(prev or {}, "pe", True); real = [r for r in old if r[2] != ""]
    if not real: return rows
    last_eps = real[-1][2]
    if not (isinstance(last_eps, (int, float)) and last_eps > 0): return {}     # LVMH/爱马仕旧序列 EPS 为空或 0：原样沿用，不动
    _, latest = src.prices(FROZEN_PRICE_TICKER.get(t, t))
    pe_rows = list(real)
    if t in QUARTERLY_FOREIGN:
        after = {"pe-ratio": real[-1][0], "price-book": ((prev.get("pb_hist") or {}).get("dates") or [""])[-1][:10], "roe": ((prev.get("roe") or {}).get("dates") or [""])[-1]}
        after["price-book"] = max([d for d in ((prev.get("pb_hist") or {}).get("dates") or [""]) if d[5:7] in ("03", "06", "09", "12") and d[8:] in ("30", "31")] or [""])
        try:
            qrows, msg = foreign_quarterly_rows(t, src, after); print(f"  {t} 季度: {msg}；新增 PE {len(qrows['pe-ratio'])} / PB {len(qrows['price-book'])} / ROE {len(qrows['roe'])}")
            pe_rows += qrows["pe-ratio"]
            if qrows["price-book"]: rows["price-book"] = [r for r in prev_rows(prev, "pb_hist") if r[0] <= after["price-book"]] + qrows["price-book"]
            if qrows["roe"]: rows["roe"] = prev_rows(prev, "roe") + qrows["roe"]
            if qrows["pe-ratio"]: rows["_fresh"] = True
        except Exception as ex:
            print(f"  {t} 季度: {type(ex).__name__}: {str(ex)[:80]}（本周不追加）")
    if t in ANNUAL_IFRS:
        try:
            ann = annual_ifrs_rows(t, src, src.facts(t)); last_pe = real[-1][0]
            if t in QUARTERLY_FOREIGN:     # 季度源在，年报只补 FCF
                ann = {"pe-ratio": [], "price-book": [], "roe": [], "free-cash-flow": ann["free-cash-flow"]}
            pe_rows += [r for r in ann["pe-ratio"] if r[0] > last_pe]
            for page, key in (("price-book", "pb_hist"), ("roe", "roe")):
                lastd = (prev.get(key) or {}).get("dates", [""])[-1]
                add = [r for r in ann[page] if r[0] > lastd]
                if add: rows[page] = prev_rows(prev, key) + add
            lasty = (prev.get("fcf") or {}).get("dates", [""])[-1]
            addf = [r for r in ann["free-cash-flow"] if r[0][:4] > lasty]
            if addf: rows["free-cash-flow"] = [[f"{y}-12-31", v] for y, v in zip((prev.get("fcf") or {}).get("dates", []), (prev.get("fcf") or {}).get("values", []))] + addf
            rows["_annual_preview"] = ann
        except Exception as ex:
            print(f"  {t} annual: {type(ex).__name__}: {str(ex)[:80]}")
    if last_eps and last_eps > 0 and latest:
        pe_rows.append([latest["last_date"], latest["last_close"], "", round(latest["last_close"] / (pe_rows[-1][2] if pe_rows[-1][2] != "" else last_eps), 2)])
    rows["pe-ratio"] = pe_rows
    return rows

def ifrs_own_rows(t, src):
    """台积电/法拉利（IFRS_OWN）：全部本站自算。20-F 年报逐年一个点（PE/EPS/PB/ROE/ROIC/FCF）；台积电在最新年报之后按季度往后接
    （雅虎季报＋台湾证交所官方累计 EPS 核对，见 foreign_quarterly_rows）；PE 末点＝最新价 ÷ 最后一期 EPS。不沿用任何上一版数据。"""
    rows = annual_ifrs_rows(t, src, src.facts(t))
    if t in QUARTERLY_FOREIGN:
        after = {pg: (rows[pg][-1][0] if rows[pg] else "") for pg in ("pe-ratio", "price-book", "roe")}
        try:
            q, msg = foreign_quarterly_rows(t, src, after); print(f"  {t} 季度: {msg}；新增 PE {len(q['pe-ratio'])} / PB {len(q['price-book'])} / ROE {len(q['roe'])}")
            for pg in ("pe-ratio", "price-book", "roe"): rows[pg] = rows[pg] + q[pg]
        except Exception as ex:
            print(f"  {t} 季度: {type(ex).__name__}: {str(ex)[:80]}（本周不追加）")
    _, latest = src.prices(t)
    if rows["pe-ratio"] and latest and rows["pe-ratio"][-1][2] > 0:
        rows["pe-ratio"].append([latest["last_date"], latest["last_close"], "", round(latest["last_close"] / rows["pe-ratio"][-1][2], 2)])
    rows["_fresh"] = True
    return rows

def history_rows(ticker, prev=None, src=None):
    """给 build_fundamentals 用：返回与旧源同形状的六页行；冻结票只刷新 PE 末点（及台积电/法拉利年报追加），其余页留空由 carry_history 沿用上一版。"""
    if ticker in IFRS_OWN:
        rows = ifrs_own_rows(ticker, src or NetSource())
        cut = IFRS_KEEP_BEFORE.get(ticker)
        if cut and prev:   # 2026-09-28：结构化数据起点前的年度点是从 20-F 原件读的（本地核对后发布一次），每周更新原样保留、不重算
            keep = {"pe-ratio": prev_rows(prev, "pe", True), "price-book": prev_rows(prev, "pb_hist"), "roe": prev_rows(prev, "roe"),
                    "roic": prev_rows(prev, "roic"),
                    "free-cash-flow": [[f"{y}-12-31", v] for y, v in zip((prev.get("fcf") or {}).get("dates", []), (prev.get("fcf") or {}).get("values", []))]}
            for pg, old in keep.items():
                rows[pg] = [r for r in old if r[0] < cut] + [r for r in rows.get(pg, []) if r[0] >= cut]
        return {k: v for k, v in rows.items() if not k.startswith("_") or k == "_fresh"}
    if ticker in FROZEN_TICKERS:
        rows = frozen_rows(ticker, prev, src or NetSource())
        return {k: v for k, v in rows.items() if not k.startswith("_") or k == "_fresh"}
    rows, flags = build(ticker, prev, src or NetSource())
    if flags: print(f"  {ticker} history: {' '.join(flags)}")
    return {k: v for k, v in rows.items() if not k.startswith("_")}

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "--validate":      # 离线复算：与工作区验证产物比对
        d = sys.argv[2]; os.makedirs(f"{d}/out_repo", exist_ok=True); src = CacheSource(d)
        for t in json.load(open(f"{d}/tickers.json")):
            p = f"{d}/baseline/s_{t.lower().replace('.', '-')}_fund.json"
            prev = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
            json.dump(history_rows(t, prev, src), open(f"{d}/out_repo/{t}.json", "w"), ensure_ascii=False)
        print("validate: 写入", f"{d}/out_repo/")
    else:
        t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
        r = history_rows(t); print({k: (len(v), v[-1] if v else None) for k, v in r.items()})
