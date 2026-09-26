# -*- coding: utf-8 -*-
"""个股页生成器 /t/<TICKER>（2026-09-26 上线）。

一个模板、一个生成器：只读 data/options_page.json（管线已过红线闸的判断层，原始 CSV 从不经过这里），
每只票出两页：t/<TK>.html（中文）与 t/<TK>.en.html（英文）。
按「这只票有什么数据」决定显示哪几块，缺的块整块不出，不留空壳。

免费窗口（Klay 09-26 定）：一年分位（一个数）＋最近 30 个交易日＋该票全部开奖记录。
更早的历史指向期权页第五章（墙位）与第七章（保费），不写 Pro（Pro 交付包不含期权读数）。
不满 10 个交易日的页 noindex，且不进 sitemap（判据 page_days()，sitemap 与路由闸都调它）。

谁来跑：期权管线 run_eod_scan.publish_options_page() 写完 options_page.json 后调本脚本，
json、t/、sitemap.xml 同一笔提交。本脚本自己不 git add、不提交。

用法：
    python3 scripts/build_ticker_pages.py            # 全量
    python3 scripts/build_ticker_pages.py MU TLT     # 只出这几只（调试用；上线提交一律全量）
退出码：0 成功 · 3 撞红线（页面里出现数据源名等禁词，拒绝写盘）
"""
import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://chronicle.klay-wang.com"
OUT = ROOT / "t"
WINDOW = 30          # 免费窗口：最近 30 个交易日（一个月期保险的长度）
MIN_DAYS = 10        # 不满 10 个交易日 ⇒ noindex
EXCLUDE = {"XLC"}    # 只有 30 天读数、没有一年期（Klay 09-26 定：先不出页，不改管线）

# 中英名：scripts/ticker_names.json，逐只取自券商个股页（中文长桥、英文富途，缺一家用另一家），
# 出处链接在私有工作区名单里。查不到出处的票不进这份表 ⇒ 页面只显示代码，不猜。
# 读入时再去一遍 ® ™ ©：名单规则是不带商标符号，这里兜底，以后新加的票手滑带进来也不会上页面。
NAMES = {t: [re.sub(r"[®™©]", "", x).strip() for x in v]
         for t, v in json.loads((ROOT / "scripts" / "ticker_names.json").read_text(encoding="utf-8"))["names"].items()}

# 百年档案里有长期走势卡的票 ⇒ 给一条去那边的链接（名单与 js/app.js BASKET_CFG 同步）
_ARCH = {
    "/tech": ("百年档案 · 科技板块", "The Archive · Tech",
              "NVDA AVGO TSM AMD MU SNDK MSFT GOOGL META AMZN AAPL TSLA QQQ"),
    "/fin": ("百年档案 · 金融板块", "The Archive · Financials", "JPM BAC GS COIN HOOD XLF"),
    "/spy": ("百年档案 · 标普 500", "The Archive · S&P 500", "SPY"),
}
ARCHIVE = {t: (href, z, e) for href, (z, e, ts) in _ARCH.items() for t in ts.split()}

# 票签分组：与 options.html GROUPS 同序（Klay 09-23 定）＋09-26 加「银行」行，XLK/XLV/HYG/LQD 进指数 · ETF。
# 🔴 末行「其他」必须永远是空的：不在任何组的票自动落末行，末行若有名字，新票就挂错名。
GROUPS = [
    ("指数 · ETF", "Index · ETF", ["SPY", "QQQ", "IWM", "TLT", "SMH", "XLF", "XLK", "XLV", "HYG", "LQD"]),
    ("八巨头", "Mag 8", ["AAPL", "AMZN", "GOOG", "GOOGL", "META", "MSFT", "NVDA", "TSLA", "SPCX"]),
    ("半导体 · 存储", "Semis · Memory", ["AVGO", "TSM", "AMD", "INTC", "MU", "SNDK", "MRVL", "QCOM", "SKHY", "SMCI", "DELL"]),
    ("软件 · AI 云", "Software · AI Cloud", ["ORCL", "PLTR", "CRWV", "NBIS"]),
    ("加密 · 券商", "Crypto · Brokers", ["COIN", "HOOD", "MSTR", "SOFI"]),
    ("银行", "Banks", ["JPM", "BAC", "C", "WFC", "GS", "KRE"]),
    ("其他", "Others", []),
]

# 归因短语是管线产出的封闭集合，译文逐字照抄 options.html 的 ATTR_EN（一处译文，别另起一套）
ATTR_EN = {
    "更像新增：今日量至少两倍于既有持仓": "Likely new: volume at least 2x existing OI",
    "倾向新增：今日量超过既有持仓": "Leans new: volume exceeds existing OI",
    "无法归因：量小于存量，可能只是换手": "Inconclusive: volume below OI, may be churn",
}
EV_EN = {"立案": "Filed", "开奖": "Settled", "更正": "Corrected", "勘误": "Erratum", "撤案": "Withdrawn",
         "已撤案": "Withdrawn", "已开奖": "Settled", "待开奖": "Open"}   # 事件类型与案子状态共用一张表
CONF_EN = {"倾向于": "Leaning", "更像": "More likely", "无法归因": "Not attributable", "已确认": "Confirmed"}

# 写盘前的红线：页面里不许出现任何数据源或券商名（取数通道与名单出处都不上站）
FORBIDDEN = ["IBKR", "Interactive Brokers", "barchart", "optioncharts", "yfinance", "Yahoo",
             "moomoo", "长桥", "富途", "Longbridge", "futunn", "Futubull", "盈透"]

# 台账原文（立案判断、验证条件、开奖结论）原样存证，用这对注释圈起来：
# 标点闸对圈内只播报不失败（同 digest 归档），英文页的中文检查也跳过圈内。
VB, VE = "<!--verbatim-->", "<!--/verbatim-->"

esc = html.escape


def dot(d):
    return d[5:].replace("-", ".")


def num(v, nd=2):
    return f"{v:.{nd}f}".rstrip("0").rstrip(".") if isinstance(v, (int, float)) else "—"


def pct(v, nd=2, sign=True):
    if not isinstance(v, (int, float)):
        return "—"
    s = f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"
    return s.replace("-", "−") + "%"


def money(v, lang):
    if not isinstance(v, (int, float)):
        return "—"
    if lang == "en":
        return f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v / 1e3:.0f}K"
    return f"{v / 1e8:.2f} 亿美元" if v >= 1e8 else f"{v / 1e4:.0f} 万美元"


def vb(s):
    return VB + esc(s) + VE


# ─────────────────────────── 判据（sitemap 与路由闸 import 这里，别复制）───────────────────────────

def universe(D):
    """出页的票：期权结构 ∪ 恒定期限 ∪ 暂停中的信用两票，去掉 EXCLUDE。"""
    return sorted(({r["ticker"] for r in D["structure"]} | set(D.get("cm_history", {}))
                   | set((D["meta"].get("credit_paused") or {}).get("tickers", []))) - EXCLUDE)


def page_days(D, tk):
    """这只票有几个交易日的记录（墙位与保费两条序列的日期并集）。<MIN_DAYS ⇒ noindex。"""
    return len({r["date"] for r in D.get("flip_history", {}).get(tk, [])}
               | {r["date"] for r in D.get("cm_history", {}).get(tk, [])})


def page_files(tk):
    return OUT / f"{tk}.html", OUT / f"{tk}.en.html"


def case_tickers(s):
    """台账 ticker 字段写法很杂：'SNDK,SKHY,MU'、'GOOGL/TSLA'、'SPY,QQQ,MU,META,NVDA等13只'。
    只认明确写出的代码；'21只'、'动量组21只' 这类没写代码的一律不匹配（宁缺，不猜）。"""
    out = []
    for tok in re.split(r"[,/，、]", s or ""):
        tok = re.sub(r"等\d+只$", "", tok.strip())
        if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,6}", tok):
            out.append(tok)
    return out


def short_j(txt, en):
    """照抄 options.html shortJ：取冒号前的结论句，括号里的证据剥掉，不生成新句子。"""
    t = re.sub(r"（[^（）]*）|\([^()]*\)", "", txt or "")
    t = re.sub(r"\s{2,}", " ", t).strip()
    depth, cut = 0, -1
    for k in range(30 if en else 6, len(t)):
        ch = t[k]
        if ch in "「“":
            depth += 1
        elif ch in "」”":
            depth = max(0, depth - 1)
        elif depth == 0 and (ch == "：" or (ch == ":" and (not en or t[k + 1:k + 2] == " "))):
            cut = k
            break
    if cut > 0:
        t = t[:cut]
    t = re.split(r";\s" if en else r"；", t)[0]
    t = re.split(r"(?<=[.!?])\s" if en else r"(?<=[。！？])", t)[0].rstrip("，,、；;")
    lim = 150 if en else 70
    return t[:lim] + "…" if len(t) > lim else t


def short_outcome(c, en):
    o = (c.get("outcome_en") if en else "") or c.get("outcome") or ""
    o = re.split(r"[:.;]" if en else r"[:：。；]", o)[0].strip()
    lim = 60 if en else 24
    return o[:lim] + "…" if len(o) > lim else o


def pctl_words(p, en):
    for hi, zh, e in [(20, "一年里最便宜的那一段", "the cheapest stretch of the past year"),
                      (40, "一年里偏便宜的一段", "the cheaper part of the past year"),
                      (60, "一年里的中间位置", "the middle of the past year"),
                      (80, "一年里偏贵的一段", "the pricier part of the past year"),
                      (101, "一年里最贵的那一段", "the most expensive stretch of the past year")]:
        if p < hi:
            return e if en else zh


def lives_words(iv30, iv365, en):
    d = iv30 - iv365
    if abs(d) < 1:
        return ("The two are about level: nothing near-term is being priced on its own."
                if en else "两条几乎一样高：眼前没有哪件事被单独加价。")
    if d > 0:
        return (f"The 30-day sits {d:.2f} points above the one-year: something near-term is being priced on its own."
                if en else f"30 天比一年期高 {d:.2f} 点：眼前有事被单独加价。")
    return (f"The one-year sits {-d:.2f} points above the 30-day, the usual shape: a longer policy covers more."
            if en else f"一年期比 30 天高 {-d:.2f} 点，这是常态：保的时间越长，要保的事越多。")


def slice_ticker(D, tk):
    st = next((r for r in D["structure"] if r["ticker"] == tk), None)
    fh = D.get("flip_history", {}).get(tk, [])
    cm = D.get("cm_history", {}).get(tk, [])
    # 两张图共用同一个 30 日窗口：站上交易日历 meta.dates 的最后 30 格（数据日为止），按日期对齐，缺日留空不补。
    cal = [d for d in D["meta"].get("dates", []) if d <= D["meta"]["data_date"]]
    win = cal[-WINDOW:]
    fhd, cmd = {r["date"]: r for r in fh}, {r["date"]: r for r in cm}
    fh_w = [dict(fhd.get(d) or {}, date=d) for d in win] if any(d in fhd for d in win) else []
    cm_w = [dict(cmd.get(d) or {}, date=d) for d in win] if any(d in cmd for d in win) else []
    paused = D["meta"].get("credit_paused") or {}
    return dict(tk=tk, st=st, fh=fh_w, cm=cm_w, fh_n=len(fh), cm_n=len(cm),
                lives=next((r for r in D.get("two_lives", []) if r["ticker"] == tk), None),
                flagged=[r for r in D.get("flagged", []) if r["ticker"] == tk],
                net=next((r for r in D.get("net_premium", []) if r["ticker"] == tk), None),
                cases=[c for c in D.get("ledger", []) if tk in case_tickers(c.get("ticker"))],
                days=page_days(D, tk),
                first=min([r["date"] for r in fh] + [r["date"] for r in cm], default=""),
                paused=paused if tk in paused.get("tickers", []) else None)


# ─────────────────────────── 渲染 ───────────────────────────

CSS = """
.tp-wrap{max-width:1180px;margin:0 auto;padding:0 24px 24px}
.tp-top{display:flex;align-items:center;justify-content:space-between;padding:18px 0 10px}
.tp-brand{font-family:'Noto Serif SC','Songti SC','STSong',serif;font-size:19px;font-weight:700;color:var(--ink);text-decoration:none}
.tp-nav{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:4px 0 14px;border-bottom:1px solid var(--border);margin-bottom:8px}
.tp-nav a.tab{text-decoration:none}
@media (min-width:821px){.tp-nav .tabs{flex:1}}
@media (max-width:820px){.tp-nav .tabs{order:3;flex:0 0 100%;flex-wrap:nowrap;overflow-x:auto}.tp-navctl{order:2}}
.tp-navctl{margin-left:auto;display:flex;align-items:center;gap:10px}
.tp-navctl a.tp-sub{font-size:13px;font-weight:600;color:#fff;background:var(--accent);border-radius:20px;padding:6px 16px;text-decoration:none;white-space:nowrap}
.tp-navctl a.tp-lang{font-size:12.5px;color:var(--ink-soft);border:1px solid var(--border);border-radius:20px;padding:5px 13px;text-decoration:none}
html:not([lang^="zh"]) .tp-nav{gap:8px}
html:not([lang^="zh"]) .tp-nav .tabs{gap:6px}
html:not([lang^="zh"]) .tp-nav a.tab{padding:6px 10px}
.tp-crumb{font-size:12.5px;color:var(--ink-muted);margin:14px 0 0}.tp-crumb a{color:var(--ink-muted)}
.tp-h1logo{width:34px;height:34px;border-radius:8px;vertical-align:-4px;margin-right:12px;background:#fff}
.tp-note{font-size:12.5px;color:var(--ink-muted);margin:8px 0 0}
.tp-fresh a,.tp-note a,.tp-more a{color:var(--accent)}
.tp-fresh{font-size:12px;color:var(--ink-muted);margin:14px 0 40px;padding-left:11px;border-left:2px solid var(--border);line-height:1.8;text-wrap:balance}
.tp-paused{border:1px dashed var(--border);border-radius:10px;padding:14px 16px;margin:10px 0 30px;font-size:13.5px;color:var(--ink-soft)}
.tp-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:14px 0 6px}
.tp-kpi{border:1px solid var(--border);border-radius:10px;padding:14px 16px;background:var(--bg-card)}
.tp-kpi .k{font-size:11.5px;color:var(--ink-muted);letter-spacing:.02em}
.tp-kpi .v{display:block;font-size:26px;font-weight:800;font-family:'JetBrains Mono',monospace;color:var(--ink);line-height:1.25;margin:4px 0 6px;white-space:nowrap}
.tp-kpi .v small{font-size:14px;font-weight:600;color:var(--ink-soft)}
.tp-kpi p{font-size:13px;color:var(--ink-soft);line-height:1.6;margin:0;text-wrap:balance}
.tp-meter{position:relative;height:8px;border-radius:4px;margin:2px 0 8px;background:linear-gradient(90deg,color-mix(in srgb,#00A86B 22%,transparent),color-mix(in srgb,#FF2400 22%,transparent))}
.tp-meter i{position:absolute;top:50%;width:11px;height:11px;border-radius:50%;background:var(--ink);border:2px solid var(--bg);transform:translate(-50%,-50%)}
.tp-ladder{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
.tp-ladder td{padding:8px 8px;border-bottom:1px solid var(--border);white-space:normal;text-align:left;vertical-align:top}
.tp-ladder td.p{font-family:'JetBrains Mono',monospace;text-align:right;white-space:nowrap;width:1%}
.tp-ladder td.d{font-family:'JetBrains Mono',monospace;text-align:right;white-space:nowrap;width:1%;color:var(--ink-muted)}
.tp-ladder td.n{white-space:nowrap;width:1%;font-weight:600;color:var(--ink);font-family:inherit}
.tp-ladder td.m{color:var(--ink-muted);font-size:12.5px;line-height:1.55;font-family:inherit}
.tp-ladder tr.spot td{background:color-mix(in srgb,var(--gold) 12%,transparent)}
.tp-ladder tr.spot td.n{color:var(--accent)}
.tp-chart{width:100%;height:300px}
.tp-table{width:100%;border-collapse:collapse;font-size:13px}
.tp-table th{text-align:left;font-size:11.5px;color:var(--ink-muted);font-weight:600;padding:6px 8px;border-bottom:2px solid var(--ink);white-space:nowrap}
.tp-table td{padding:7px 8px;border-bottom:1px solid var(--border);font-family:'JetBrains Mono',monospace;font-size:12.5px;text-align:left}
.tp-table td.zh{font-family:inherit;font-size:13px;white-space:normal}
.tp-table th.n,.tp-table td.n{text-align:right}
.tp-scroll{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}
.tp-scroll .tp-table{width:max-content;min-width:100%}
.tp-cases{border-top:2px solid var(--ink);margin-top:10px}
.tp-case{border-bottom:1px solid var(--border)}
.tp-case summary{display:grid;grid-template-columns:56px minmax(0,1fr) auto;gap:12px;align-items:baseline;padding:10px 4px;cursor:pointer;list-style:none}
.tp-case summary::-webkit-details-marker{display:none}
.tp-case summary .dt{font-family:'JetBrains Mono',monospace;font-size:12.5px;color:var(--ink-muted)}
.tp-case summary .j{font-size:13.5px;color:var(--ink);line-height:1.6}
.tp-case summary .j em{font-style:normal;font-size:11.5px;color:var(--ink-muted);margin-left:6px}
.tp-case[open] summary{background:color-mix(in srgb,var(--border) 30%,transparent)}
.tp-case .body{padding:6px 4px 14px 72px;font-size:12.5px;color:var(--ink-soft);line-height:1.75}
.tp-case .body .ev{margin:6px 0}.tp-case .body b{color:var(--ink)}
.tp-case .body .h{font-size:11px;color:var(--ink-muted);letter-spacing:.04em;margin:8px 0 2px}
.tp-badge{display:inline-block;font-size:11px;font-weight:600;border-radius:10px;padding:2px 9px;white-space:nowrap}
.tp-badge.wait{color:var(--gold);border:1px solid var(--gold)}
.tp-badge.done{color:var(--ink-soft);border:1px solid var(--ink-muted)}
.tp-badge.wrong{color:var(--accent);border:1px solid var(--accent)}
.tp-count{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0 4px}
.tp-count div{border:1px solid var(--border);border-radius:10px;padding:8px 14px;background:var(--bg-card);font-size:11.5px;color:var(--ink-muted)}
.tp-count b{display:block;font-size:20px;font-weight:800;font-family:'JetBrains Mono',monospace;color:var(--ink)}
.tp-more{margin:10px 0 0;font-size:13.5px;line-height:1.8}
.tp-idx{display:flex;flex-direction:column;gap:10px;margin:12px 0}
.tp-idx .row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.tp-idx .g{font-size:11px;color:var(--ink-muted);min-width:96px}
.tp-idx a{font-family:'JetBrains Mono',monospace;font-size:12px;padding:4px 10px;border:1px solid var(--border);border-radius:14px;color:var(--ink-soft);text-decoration:none;background:var(--bg-card)}
.tp-idx a.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.tp-idx a img{width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:5px;background:#fff}
.tp-cal{border:1px dashed var(--border);border-radius:10px;padding:16px 18px;font-size:13px;color:var(--ink-soft);line-height:1.8;margin-top:40px}
.tp-cal b{color:var(--ink)}
@media (max-width:640px){.tp-ladder td.n{white-space:normal;min-width:64px}.tp-ladder td{padding:8px 5px}.tp-badge{white-space:normal;max-width:100%}.tp-idx .g{min-width:100%}.tp-case summary{grid-template-columns:48px minmax(0,1fr)}.tp-case summary .tp-badge{grid-column:2;justify-self:start}.tp-case .body{padding-left:4px}.tp-kpi .v{font-size:23px}}
"""

NAV = [("/", "今日", "Today"), ("/kindex", "K 指数", "KAPX"), ("/leaps", "恐惧的标价", "Fear-Price Index"),
       ("/options", "期权异动", "Options"), ("/f13", "13F 开奖", "13F"), ("/macro", "宏观", "Macro"),
       ("/spy", "百年档案", "The Archive")]


def T(lang, zh, en):
    return en if lang == "en" else zh


def render(D, s, lang, css_v, uni):
    tk, st, en = s["tk"], s["st"], lang == "en"
    zh_name, en_name = NAMES.get(tk, (None, None))
    zh_label = f"{zh_name} {tk}" if zh_name else tk          # 名单里没有 ⇒ 只显示代码
    en_label = f"{en_name} ({tk})" if en_name else tk
    # 正文句子里怎么称呼它：公司用名字；基金类名字太长（「20 年期以上美国国债 ETF - iShares」），句子里用代码，
    # 前后留空格（「今天给 TLT 买保护」）。标题与 h1 照样用名单里的全名。
    is_fund = bool(zh_name and "ETF" in zh_name.upper()) or bool(en_name and re.search(r"\b(ETF|Fund|Trust)\b", en_name, re.I))
    who_zh = f" {tk} " if (is_fund or not zh_name) else zh_name
    who_en = tk if (is_fund or not en_name) else en_name
    CH = iter(["一", "二", "三", "四", "五", "六"])        # 章节号按实际出现的顺序连续编（缺块时不跳号）
    CE = iter(["I", "II", "III", "IV", "V", "VI"])
    chap = lambda z, e: (f'<div class="chapter-head"><span class="chapter-no">{T(lang, next(CH), next(CE))}</span>'
                         f'<h2>{T(lang, z, e)}</h2></div>')
    self_url = f"{BASE}/t/{tk}" + (".en" if en else "")
    zh_url, en_url = f"{BASE}/t/{tk}", f"{BASE}/t/{tk}.en"
    data_date = D["meta"]["data_date"]
    noindex = s["days"] < MIN_DAYS
    has_walls = bool(s["fh"])

    title = T(lang, f"{zh_label} 期权读数：保险价格一年分位、墙位与开奖记录 · 恐惧的标价 Fear-Price",
              f"{en_label} options: implied volatility percentile, walls and settled calls · Fear-Price")
    desc = T(lang,
             f"{zh_name or tk}（{tk}）期权市场每个交易日收盘后的读数：一个月期保险价格在过去一年里的位置、上下两面墙、"
             f"本周预期波动区间、最近 30 个交易日的轨迹，以及我们对它立过的每一案和开奖结果。",
             f"{en_name or tk} ({tk}) options after every close: where one-month implied volatility sits in its one-year range, "
             f"the put and call walls, this week's priced move, the last 30 trading days, and every call we have filed "
             f"on it with its settlement.")

    out = []
    w = out.append
    w("<!doctype html>")
    w(f'<html lang="{"en" if en else "zh-CN"}">')
    w("<head>")
    w('<meta charset="utf-8">')
    w('<meta name="viewport" content="width=device-width,initial-scale=1">')
    w(f"<title>{esc(title)}</title>")
    w(f'<link rel="canonical" href="{self_url}">')
    w(f'<link rel="alternate" hreflang="zh-CN" href="{zh_url}">')
    w(f'<link rel="alternate" hreflang="en" href="{en_url}">')
    w(f'<link rel="alternate" hreflang="x-default" href="{zh_url}">')
    w(f'<meta name="description" content="{esc(desc)}">')
    if noindex:
        w('<meta name="robots" content="noindex,follow">')
    w(f'<meta property="og:title" content="{esc(title)}">')
    w(f'<meta property="og:description" content="{esc(desc)}">')
    w(f'<meta property="og:url" content="{self_url}">')
    w('<meta property="og:type" content="website">')
    w(f'<meta name="fp-data-date" content="{data_date}">')   # 路由闸拿它对 options_page.json：数据更新了而页面没跟上＝红
    w("<script>try{var _t=localStorage.getItem(\"mc-theme\");if(_t?_t===\"dark\":true)"
      "document.documentElement.classList.add(\"dark-mode\")}catch(e){}</script>")
    w("<style>html{background:#FFFFFF}html.dark-mode{background:#111111}</style>")
    w(f'<link rel="stylesheet" href="/css/style.css?v={css_v}">')
    w(f"<style>{CSS}</style>")
    w("</head>")
    w("<body>")
    w('<div class="tp-wrap">')
    w(f'<div class="tp-top"><a class="tp-brand" href="/">{T(lang, "恐惧的标价", "Fear-Price")}</a></div>')
    w('<nav class="tp-nav topbar-navband"><nav class="tabs">')
    for href, z, e in NAV:
        cls = "tab tab-ext active" if href == "/options" else "tab"
        w(f'<a class="{cls}" href="{href}">{T(lang, z, e)}</a>')
    w("</nav>")
    other = f"/t/{tk}" if en else f"/t/{tk}.en"
    w(f'<div class="tp-navctl"><a class="tp-sub" href="/pricing">{T(lang, "订阅", "Subscribe")}</a>'
      f'<a class="tp-lang" href="{other}" data-lang="{"zh" if en else "en"}">{T(lang, "EN", "简")}</a>'
      f'<button class="theme-toggle" id="tp-theme" title="{T(lang, "日夜切换", "Toggle theme")}">☀</button></div>')
    w("</nav>")

    # ── 页头 ──
    w(f'<p class="tp-crumb"><a href="/options">{T(lang, "期权异动", "Options")}</a> / {T(lang, "个股页", "Tickers")} / {tk}</p>')
    w('<div class="hero">')
    w(f'<div class="kicker">{T(lang, "个股页", "TICKER PAGE")} · {tk}</div>')
    w(f'<h1><img class="tp-h1logo" src="/logos/{tk.lower()}.png" alt="" onerror="this.remove()">'
      f'{esc(T(lang, zh_label, en_label))}</h1>')
    # 开头一句只说这一页真有的块：没有墙位数据的票不提「钱押在哪些价位」，暂停中的票不提保险价格
    q_zh, q_en = [], []
    if s["lives"] or st:
        q_zh.append(f"今天给{who_zh}买保护贵不贵"); q_en.append(f"is protection on {who_en} cheap or dear today")
    if st:
        q_zh.append("钱押在哪些价位"); q_en.append("at which prices has money been placed")
    if s["fh"] or s["cm"]:
        q_zh.append("最近一个月怎么走过来的"); q_en.append("how did the last month get here")
    q_zh.append(f"我们对{who_zh}立过的案开奖了没有"); q_en.append(f"how did the calls we filed on {who_en} settle")
    q_en[0] = q_en[0][0].upper() + q_en[0][1:]
    w('<p class="dek">' + T(lang,
        "，".join(q_zh).strip() + "。每个交易日收盘后更新，对的错的都留着。",
        (", ".join(q_en[:-1]) + ", and " + q_en[-1] if len(q_en) > 1 else q_en[0]) +
        ". Updated after every close, right calls and wrong ones alike.") + "</p>")
    if st:
        w('<p class="tp-note">' + T(lang,
            f"数据日 {dot(data_date)} · 收盘 {num(st['spot'])}（{pct(st.get('chg_pct'))}）· 距 52 周高点 {pct(st.get('dd_52w'), sign=False)}",
            f"Data date {dot(data_date)} · close {num(st['spot'])} ({pct(st.get('chg_pct'))}) · {pct(st.get('dd_52w'), sign=False)} from the 52-week high")
          + "</p>")
    elif s["lives"] or s["cm"]:
        w(f'<p class="tp-note">{T(lang, "数据日", "Data date")} {dot(data_date)}</p>')
    w("</div>")

    if s["paused"]:
        p = s["paused"]
        w('<div class="tp-paused">' + T(lang,
            f"这只票的保险价格读数正在切换计算方法，{dot(p.get('resume', ''))} 恢复。恢复前本页只放开奖记录。",
            f"The implied-vol readings for this name are switching methodology and return on {dot(p.get('resume', ''))}. "
            f"Until then this page carries the case record only.") + "</div>")

    # ── 一 今天在哪 ──
    if st or s["lives"]:
        w(chap("今天在哪", "Where it stands today"))
        w('<div class="tp-kpis">')
        p = st.get("iv_pctl") if st else None
        if isinstance(p, (int, float)):
            # 口径（Klay 09-26 定）：只说 0 是一年最便宜、100 是一年最贵，两种算法都成立；对外不写「百分之几的日子」。
            w('<div class="tp-kpi">'
              f'<span class="k">{T(lang, "保险价格一年分位", "One-year percentile")}</span>'
              f'<span class="v">{p:.0f}<small> / 100</small></span>'
              f'<div class="tp-meter"><i style="left:{max(2, min(98, p)):.1f}%"></i></div>'
              '<p>' + T(lang,
                  f"一个月期保险（30 天隐含波动率）在过去一年里的位置，0 是一年最便宜，100 是一年最贵。"
                  f"现在给{who_zh}买一个月的保护，价格在{pctl_words(p, False)}。",
                  f"Where one-month protection (30-day implied volatility) sits in its past year, 0 the cheapest "
                  f"and 100 the dearest. Protection on {who_en} is now in {pctl_words(p, True)}.") + "</p></div>")
        lv = s["lives"]
        if lv:
            w('<div class="tp-kpi">'
              f'<span class="k">{T(lang, "30 天 / 一年期保险价格（隐含波动率 %）", "30-day / one-year implied vol (%)")}</span>'
              f'<span class="v">{num(lv["iv30"])}<small> / {num(lv["iv365"])}</small></span>'
              '<p>' + lives_words(lv["iv30"], lv["iv365"], en) + "</p></div>")
        if st and st.get("em_abs") and st.get("spot"):
            em_pct = st["em_abs"] / st["spot"] * 100
            w('<div class="tp-kpi">'
              f'<span class="k">{T(lang, "期权定价的本周波动", "Priced move this week")}</span>'
              f'<span class="v">±{em_pct:.1f}%<small> ±{num(st["em_abs"])}</small></span>'
              '<p>' + T(lang,
                  f"期权价格隐含的本周区间是 {num(st['em_lo'])} 到 {num(st['em_hi'])}。收在区间外，卖保护的人亏钱。",
                  f"Option prices imply {num(st['em_lo'])} to {num(st['em_hi'])} for the week. A close outside it "
                  f"means the protection sellers lose.") + "</p></div>")
        if st and isinstance(st.get("pc_vol"), (int, float)):
            pv, po = st["pc_vol"], st.get("pc_oi")
            side = (T(lang, "成交里看涨多于看跌", "Calls outtraded puts") if pv < 1
                    else T(lang, "成交里看跌多于看涨", "Puts outtraded calls"))
            w('<div class="tp-kpi">'
              f'<span class="k">{T(lang, "看跌 ÷ 看涨（成交 / 持仓）", "Put ÷ call (volume / open interest)")}</span>'
              f'<span class="v">{num(pv)}<small> / {num(po)}</small></span>'
              '<p>' + side + T(lang, "。这个比只说张数，不说方向：买看跌的人可能在给持股上保险。",
                              ". It counts contracts, not direction: a put buyer may be insuring shares they own.") + "</p></div>")
        w("</div>")

        if st and st.get("put_wall") and st.get("call_wall") and st.get("spot"):
            spot = st["spot"]
            rows = [
                (st.get("em_hi"), T(lang, "本周区间上沿", "Priced move, top"), T(lang, "期权隐含的本周高点。", "This week's implied high.")),
                (st.get("call_wall"), T(lang, "上墙", "Call wall"), T(lang, "看涨持仓最厚的价位，别人付过定金的目标价。", "The strike with the most call open interest: a target someone paid a deposit on.")),
                (spot, T(lang, "收盘", "Close"), ""),
                (st.get("put_wall"), T(lang, "下墙", "Put wall"), T(lang, "看跌持仓最厚的价位，别人付过保费的保护层。", "The strike with the most put open interest: protection someone already paid for.")),
                (st.get("em_lo"), T(lang, "本周区间下沿", "Priced move, bottom"), T(lang, "期权隐含的本周低点。", "This week's implied low.")),
                (st.get("flip"), T(lang, "多空分界", "Flip level"), T(lang, "价格在它上方，做市商的对冲会压住波动；跌破它，对冲会放大波动。", "Above it, dealer hedging damps moves; below it, hedging amplifies them.")),
            ]
            rows = sorted([r for r in rows if isinstance(r[0], (int, float))], key=lambda r: -r[0])
            w(f'<p class="tp-note" style="margin-top:18px">{T(lang, "价位从高到低，右列是离收盘价多远：", "Levels from high to low, with distance from the close:")}</p>')
            w('<table class="tp-ladder">')
            for price, label, mean in rows:
                is_spot = price == spot and not mean
                d = "" if is_spot else pct((price / spot - 1) * 100)
                w(f'<tr{" class=\"spot\"" if is_spot else ""}><td class="n">{label}</td><td class="p">{num(price)}</td>'
                  f'<td class="d">{d}</td><td class="m">{mean}</td></tr>')
            w("</table>")
        w('<p class="tp-fresh">' + (T(lang,
            f"数据日 {dot(data_date)}。每个交易日收盘后更新一次，现价取官方收盘价。",
            f"Data date {dot(data_date)}. Updated once after each trading day's close; spot is the official close.") if st else T(lang,
            f"数据日 {dot(data_date)}。保险价格每个交易日收盘前取一次。",
            f"Data date {dot(data_date)}. Implied vols are read once before each close.")) + "</p>")

    # ── 二 最近 30 个交易日 ──
    if s["fh"] or s["cm"]:
        w(chap("最近 30 个交易日", "The last 30 trading days"))
        if s["fh"]:
            w('<p class="tp-note">' + T(lang,
                "收盘价、多空分界、上下两面墙，逐日。价格在墙的哪一边、墙自己有没有搬家，一眼能看出来。",
                "Close, flip level, put wall and call wall, day by day: which side of the walls price is on, and whether the walls moved.") + "</p>")
            w('<div class="tp-chart" id="ch-walls"></div>')
        if s["cm"]:
            w('<p class="tp-note" style="margin-top:22px">' + T(lang,
                "一年期和 30 天的保险价格，逐日。短线跳、长线不动，是眼前这件事在涨价；长线也抬头，涨价的是整段故事。",
                "One-year and 30-day implied vol, day by day. The short line jumping while the long line holds means only the near-term event is being repriced; the long line rising too means the whole story is.") + "</p>")
            w('<div class="tp-chart" id="ch-cm"></div>')
        # Klay 09-26 定：不写 Pro；更早的记录指向期权页（第五章墙位、第七章保费）。只有保费的票只有第七章。
        where_zh = ('<a href="/options#op-ch5">期权页第五章</a>（墙位）和<a href="/options#op-ch7">第七章</a>（保费）'
                    if has_walls else '<a href="/options#op-ch7">期权页第七章</a>')
        where_en = ('<a href="/options#op-ch5">Chapter V</a> (walls) and <a href="/options#op-ch7">Chapter VII</a> '
                    '(implied vol) of the options page' if has_walls
                    else '<a href="/options#op-ch7">Chapter VII of the options page</a>')
        w('<p class="tp-fresh">' + T(lang, f"这里放最近 30 个交易日；更早的在{where_zh}。",
                                     f"Shown: the last 30 trading days. Earlier records are in {where_en}.") + "</p>")

    # ── 三 今天的大单 ──
    if st:
        w(chap("今天的大单", "Today's big prints"))
        net = s["net"]
        if net:
            w('<p class="tp-note">' + T(lang,
                f"今天{who_zh}的看涨合约换手 {money(net['call'], 'zh')}，看跌 {money(net['put'], 'zh')}。换手额不分买卖，谁买谁卖我们看不见。",
                f"Today {who_en} calls traded {money(net['call'], 'en')} in premium and puts {money(net['put'], 'en')}. "
                f"Traded, not bought: we cannot see who bought and who sold.") + "</p>")
        if s["flagged"]:
            w('<div class="tp-scroll"><table class="tp-table">')
            w("<tr>" + "".join(f'<th{" class=n" if n else ""}>{h}</th>' for h, n in [
                (T(lang, "到期", "Expiry"), 0), (T(lang, "行权价", "Strike"), 1), (T(lang, "类型", "Type"), 0),
                (T(lang, "成交", "Volume"), 1), (T(lang, "持仓", "OI"), 1), (T(lang, "权利金换手", "Premium traded"), 1),
                (T(lang, "归因", "Attribution"), 0)]) + "</tr>")
            for r in s["flagged"]:
                typ = {"C": T(lang, "看涨", "Call"), "P": T(lang, "看跌", "Put")}.get(r["type"], r["type"])
                a_zh = r["attribution"]
                a_txt = (ATTR_EN.get(a_zh) or vb(a_zh)) if en else esc(a_zh)
                if en and a_zh in ATTR_EN:
                    a_txt = esc(a_txt)
                w(f'<tr><td>{dot(r["expiry"])}</td><td class=n>{num(r["strike"])}</td><td>{typ}</td>'
                  f'<td class=n>{r["volume"]:.0f}</td><td class=n>{r["oi"]:.0f}</td>'
                  f'<td class=n>{money(r["premium"], lang)}</td><td class="{"" if en and a_zh in ATTR_EN else "zh"}">{a_txt}</td></tr>')
            w("</table></div>")
            w('<p class="tp-note">' + T(lang,
                "只列逐条复核过的 A 级。归因只做算术：量至少两倍于持仓记更像新增，量超过持仓记倾向新增，量小于持仓记无法归因。",
                "A-grade rows only, each rechecked. Attribution is arithmetic: volume at least twice OI reads likely new; above OI, leans new; below OI, inconclusive.") + "</p>")
        else:
            w('<p class="tp-note">' + T(lang, f"今天{who_zh}没有合约进大单榜。", f"No {who_en} contract made the big board today.") + "</p>")
        w(f'<p class="tp-fresh">{T(lang, "只放数据日当天。全部标的的大单榜在", "Data date only. The board for every name is in")} '
          f'<a href="/options#op-ch4">{T(lang, "期权页第四章", "Chapter IV of the options page")}</a>{T(lang, "。", ".")}</p>')

    # ── 四 开奖记录 ──
    cases = ([c for c in s["cases"] if c["status"] == "待开奖"] +
             sorted([c for c in s["cases"] if c["status"] != "待开奖"], key=lambda c: c["events"][0]["date"], reverse=True))
    w(chap("我们对它立过的案", "Every call we filed on it"))
    if cases:
        n_open = sum(c["status"] == "待开奖" for c in cases)
        n_done = sum(c["status"] == "已开奖" for c in cases)
        first_case = min(c["events"][0]["date"] for c in cases)
        w('<p class="tp-note">' + T(lang,
            f"每一案先写下判断和验证条件，到期按条件开奖，错了原样留着。这里是所有提到{who_zh}的案子，一案不删。点开看立案原文。",
            f"Each call is written down with its test before the fact, settled by that test, and left standing when wrong. "
            f"These are all the cases that name {who_en}, none removed. Open one for the original filing.") + "</p>")
        w('<div class="tp-count">'
          f'<div><b>{len(cases)}</b>{T(lang, f"案 · 自 {dot(first_case)}", f"cases since {dot(first_case)}")}</div>'
          f'<div><b>{n_done}</b>{T(lang, "已开奖", "settled")}</div>'
          f'<div><b>{n_open}</b>{T(lang, "待开奖", "open")}</div></div>')
        w('<div class="tp-cases">')
        for c in cases:
            f = c["events"][0]
            j_en = en and bool(f.get("judgment_en"))
            jt = f.get("judgment_en") if j_en else f["judgment"]
            others = [t for t in case_tickers(c["ticker"]) if t != tk]
            extra = re.search(r"等(\d+)只", c["ticker"] or "")
            with_ = ""
            if others:
                with_ = T(lang, "同案：" + "、".join(others), "Also: " + ", ".join(others))
                if extra:
                    with_ += T(lang, f" 等 {extra.group(1)} 只", f" ({extra.group(1)} names in all)")
            if c["status"] == "待开奖":
                badge = f'<span class="tp-badge wait">{T(lang, "待开奖 · 预计 ", "Open · est. ")}{dot(f.get("expected") or "")}</span>'
            elif c["status"] == "已开奖":
                o = short_outcome(c, en) or T(lang, "已开奖", "Settled")
                o_is_zh = not (en and c.get("outcome_en"))
                wrong = re.match(r"^(错|判断错误)", c.get("outcome") or "")    # 与 options.html verdict() 的「错」类同一判据
                badge = f'<span class="tp-badge {"wrong" if wrong else "done"}">{vb(o) if o_is_zh else esc(o)}</span>'
            else:
                badge = f'<span class="tp-badge done">{esc(T(lang, c["status"], EV_EN.get(c["status"], c["status"])))}</span>'
            w('<details class="tp-case">')
            j_html = esc(short_j(jt, True)) if j_en else vb(short_j(jt, False))
            w(f'<summary><span class="dt">{dot(f["date"])}</span><span class="j"{"" if j_en or not en else " lang=zh"}>{j_html}'
              f'{f"<em>{esc(with_)}</em>" if with_ else ""}</span>{badge}</summary>')
            w('<div class="body">')
            if c["status"] == "已开奖":
                oc_en = en and c.get("outcome_en")
                oc = esc(c["outcome_en"]) if oc_en else vb(c.get("outcome") or "")
                w(f'<div class="ev"><b>{T(lang, "开奖", "Settlement")}</b><br>{oc}</div>')
            w(f'<div class="h">{T(lang, "立案原文与验证条件 · 原样存证不改", "Original filing and test, archived verbatim in Chinese")}</div>')
            for ev in c["events"]:
                typ = EV_EN.get(ev["type"], ev["type"]) if en else ev["type"]
                c0 = ev.get("confidence") or ""
                conf = CONF_EN.get(c0, c0) if en else c0
                conf_html = "" if not c0 else (T(lang, "（置信度：", " (confidence: ")
                                               + (esc(conf) if (not en or c0 in CONF_EN) else vb(conf)) + T(lang, "）", ")"))
                w(f'<div class="ev" lang="zh"><b>{dot(ev["date"])} {esc(typ)}</b>{conf_html}<br>{vb(ev["judgment"])}'
                  + (f'<br>{T(lang, "验证条件：", "Test: ")}{vb(ev["verify"])}' if ev.get("verify") else "")
                  + (f'<br>{T(lang, "结果：", "Result: ")}{vb(ev["result"])}' if ev.get("result") else "") + "</div>")
            w("</div></details>")
        w("</div>")
    else:
        w('<p class="tp-note">' + T(lang, f"还没有提到{who_zh}的案子。", f"No case names {who_en} yet.") + "</p>")
    w('<p class="tp-fresh">' + T(lang,
        '全部标的的案子在<a href="/options#op-ch3">期权页第三章</a>。台账只加不改，改判也是新加一行。',
        'Cases for every name are in <a href="/options#op-ch3">Chapter III of the options page</a>. The ledger is append-only; a revision is a new row.') + "</p>")

    # ── 五 其他个股 ──
    w(chap("其他个股", "Other names"))
    arch = ARCHIVE.get(tk)
    if arch:
        w(f'<p class="tp-more">{T(lang, f"{who_zh}几十年的股价走势与回撤，在", f"Decades of {who_en} price history and drawdowns are in")} '
          f'<a href="{arch[0]}">{T(lang, arch[1], arch[2])}</a>{T(lang, "。", ".")}</p>')
    have = set(uni)
    grouped = {t for g in GROUPS for t in g[2]}
    groups = [list(g) for g in GROUPS]
    groups[-1][2] = sorted(have - grouped)
    w('<div class="tp-idx">')
    for gz, ge, ts in groups:
        ts = [t for t in ts if t in have]
        if not ts:
            continue
        links = "".join(
            f'<a href="/t/{t}{".en" if en else ""}"{" class=on aria-current=page" if t == tk else ""}>'
            f'<img src="/logos/{t.lower()}.png" alt="" loading="lazy" onerror="this.remove()">{t}</a>' for t in ts)
        w(f'<div class="row"><span class="g">{T(lang, gz, ge)}</span>{links}</div>')
    w("</div>")

    # ── 口径 ──
    w('<div class="tp-cal">')
    w(f'<b>{T(lang, "口径说明", "How to read this page")}</b><br>')
    for zh_line, en_line in [
        ("一年分位与 30 天、一年期保险价格来自两把不同的尺子，只各自比自己的过去，互相不拼。",
         "The one-year percentile and the 30-day and one-year implied vols come from two different rulers; each is compared only with its own past."),
        ("30 天、一年期保险价格是恒定期限读数：用两个相邻到期日插值到正好 30 天和 365 天，每天收盘前取一次。",
         "The 30-day and one-year vols are constant-maturity: interpolated between the two nearest expiries to exactly 30 and 365 days, read once before each close."),
        ("大单一行是一个合约当天的累计，不是一笔交易。我们没有逐笔时间戳和主动买卖方向，所以只说换手额。",
         "A big-print row is one contract's full-day total, not a single trade. Without per-trade timestamps or aggressor side we state premium traded only."),
        ("每个读数由两把独立的尺子核对，收盘后更新一次。原始数据不在本站提供。",
         "Every reading is cross-checked against two independent sources and updated once after the close. Raw data is not provided on this site."),
    ]:
        w("· " + T(lang, zh_line, en_line) + "<br>")
    w("· <b>" + T(lang, "仅为数据与信息，不构成投资建议，不含任何买卖推荐。",
                  "Data and information only; not investment advice; no buy or sell recommendations.") + "</b>")
    w("</div>")
    # Klay 09-26 定的导流句（真话：当日判读只进订户邮箱，往期隔天公开）
    w('<p class="tp-more">' + T(lang,
        '今天对这只票的判读在<a href="/pricing">当日订户邮件</a>里，<a href="/digest/">往期</a>隔天公开。',
        'Today\'s read on this name is in the <a href="/pricing">subscriber email</a>; <a href="/digest/">past issues</a> go public the next day.')
      + "</p>")
    w("</div>")

    # ── 页脚（与 options.html 同结构，链接改根路径）──
    w('<footer class="site-footer"><div class="footer-main"><div class="footer-brand-col">'
      f'<div class="footer-brand">{T(lang, "恐惧的标价", "Fear-Price")}<span>Fear-Price</span></div>'
      f'<div class="footer-tag">{T(lang, "每个交易日自动更新的美股百年图表档案与读数台账", "A self-updating archive of a century of US market charts, plus a public ledger of daily readings")}</div>'
      '</div><div class="footer-colgroup">'
      f'<div class="footer-col"><div class="footer-col-h">About</div><a href="/about">{T(lang, "关于我们", "About us")}</a>'
      f'<a href="/methodology">{T(lang, "方法论", "Methodology")}</a><a href="/contact">{T(lang, "联系我们", "Contact us")}</a></div>'
      f'<div class="footer-col"><div class="footer-col-h">Get Started</div><a href="/pricing">{T(lang, "定价与套餐", "Pricing & plans")}</a>'
      f'<a href="https://github.com/klaywang24/fear-price/blob/main/data/README.md" target="_blank" rel="noopener">{T(lang, "数据 &amp; API", "Data &amp; API")}</a>'
      f'<a href="/digest/">{T(lang, "周报档案", "Weekly digest")}</a></div>'
      f'<div class="footer-col"><div class="footer-col-h">Legal</div><a href="/terms">{T(lang, "服务条款", "Terms")}</a>'
      f'<a href="/privacy">{T(lang, "隐私政策", "Privacy")}</a><a href="/refunds">{T(lang, "退款政策", "Refunds")}</a></div>'
      '</div></div>'
      f'<div class="footer-note"><span class="footer-left">{T(lang, "仅供信息与教育用途，不构成投资建议", "For information and education only; not investment advice")}</span>'
      '<span class="footer-copy">© 2026 Fear-Price</span></div></footer>')

    payload = {"lang": lang, "fh": s["fh"], "cm": s["cm"]}
    w(f'<script type="application/json" id="tp-data">{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}</script>')
    w("""<script>
(function(){
  var root=document.documentElement,btn=document.getElementById("tp-theme");
  btn.textContent=root.classList.contains("dark-mode")?"\\u263e":"\\u2600";
  btn.addEventListener("click",function(){root.classList.toggle("dark-mode");
    try{localStorage.setItem("mc-theme",root.classList.contains("dark-mode")?"dark":"light")}catch(e){}location.reload();});
  var lb=document.querySelector(".tp-lang");
  lb.addEventListener("click",function(){try{localStorage.setItem("mc-lang",lb.dataset.lang)}catch(e){}});
})();
</script>""")
    if s["fh"] or s["cm"]:
        w('<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>')
        w("""<script>
(function(){
  if(!window.echarts)return;
  var D=JSON.parse(document.getElementById("tp-data").textContent),EN=D.lang==="en";
  var tr=function(z,e){return EN?e:z};
  var dark=document.documentElement.classList.contains("dark-mode");
  var INK=dark?"#e8e4dc":"#1a1a1a",MUTED=dark?"#8a867e":"#8a8a8a",BORDER=dark?"#3a3833":"#e5e0d8",
      RED="#FF2400",GREEN=dark?"#34D96C":"#00A86B",GOLD="#B8893E";
  var dot=function(d){return d.slice(5).replace("-",".")};
  var ax={axisLine:{lineStyle:{color:BORDER}},axisLabel:{color:MUTED,fontSize:11},splitLine:{lineStyle:{color:BORDER,opacity:.5}}};
  function mk(id){var el=document.getElementById(id);if(!el)return null;var c=echarts.init(el);
    new ResizeObserver(function(){c.resize()}).observe(el);return c;}
  var a=mk("ch-walls");
  if(a){var h=D.fh;a.setOption({grid:{left:56,right:18,top:36,bottom:30},
    legend:{top:4,textStyle:{color:MUTED}},tooltip:{trigger:"axis"},
    xAxis:{type:"category",data:h.map(function(r){return dot(r.date)}),axisLine:ax.axisLine,axisLabel:ax.axisLabel},
    yAxis:{type:"value",scale:true,axisLine:ax.axisLine,axisLabel:ax.axisLabel,splitLine:ax.splitLine},
    series:[
      {name:tr("收盘","Close"),type:"line",data:h.map(function(r){return r.close}),lineStyle:{color:INK,width:2.5},itemStyle:{color:INK},connectNulls:true,z:5},
      {name:tr("多空分界","Flip"),type:"line",data:h.map(function(r){return r.flip}),lineStyle:{color:GOLD,width:1.5,type:"dashed"},itemStyle:{color:GOLD},connectNulls:true},
      {name:tr("下墙","Put wall"),type:"line",step:"middle",data:h.map(function(r){return r.pw}),lineStyle:{color:RED,width:1.5},itemStyle:{color:RED},symbol:"none",connectNulls:true},
      {name:tr("上墙","Call wall"),type:"line",step:"middle",data:h.map(function(r){return r.cw}),lineStyle:{color:GREEN,width:1.5},itemStyle:{color:GREEN},symbol:"none",connectNulls:true}]});}
  var b=mk("ch-cm");
  if(b){var k=D.cm;b.setOption({grid:{left:56,right:18,top:36,bottom:30},
    legend:{top:4,textStyle:{color:MUTED}},tooltip:{trigger:"axis",valueFormatter:function(v){return v==null?"—":v+"%"}},
    xAxis:{type:"category",data:k.map(function(r){return dot(r.date)}),axisLine:ax.axisLine,axisLabel:ax.axisLabel},
    yAxis:{type:"value",scale:true,name:tr("隐含波动率 %","Implied vol %"),nameTextStyle:{color:MUTED},axisLine:ax.axisLine,axisLabel:ax.axisLabel,splitLine:ax.splitLine},
    series:[
      {name:tr("一年期","One-year"),type:"line",data:k.map(function(r){return r.iv365}),lineStyle:{color:INK,width:2.5},itemStyle:{color:INK},z:5},
      {name:tr("30 天","30-day"),type:"line",data:k.map(function(r){return r.iv30}),lineStyle:{color:RED,width:1.5,type:"dashed"},itemStyle:{color:RED}}]});}
})();
</script>""")
    w('<script defer src="https://static.cloudflareinsights.com/beacon.min.js" '
      'data-cf-beacon=\'{"token": "21badd8cf43d430e8b00d2fcad37a312"}\'></script>')
    w("</body>")
    w("</html>")
    return "\n".join(out) + "\n", noindex


RD_BEGIN = "# ── 个股页小写归一（scripts/build_ticker_pages.py 全量运行时重写本段，勿手改）──"
RD_END = "# ── 个股页小写归一·结束 ──"


def write_redirects(uni):
    """/t/mu → /t/MU。读者在券商评论区只能手打链接，常打成小写；页面文件是大写的，小写会落到 SPA 兜底回首页。
    本文件规则只匹配全小写请求（顶部 §59 实测：/XQ 不命中 /xq 规则），所以大写请求不会被再跳一次，不会成环。"""
    p = ROOT / "_redirects"
    s = p.read_text(encoding="utf-8")
    lines = [RD_BEGIN]
    for tk in uni:
        if tk != tk.lower():
            lines.append(f"/t/{tk.lower()}      /t/{tk}      301")
            lines.append(f"/t/{tk.lower()}.en   /t/{tk}.en   301")
    lines.append(RD_END)
    block = "\n".join(lines)
    if RD_BEGIN in s:
        s = re.sub(re.escape(RD_BEGIN) + ".*?" + re.escape(RD_END), lambda m: block, s, flags=re.S)
    else:
        s = s.rstrip("\n") + "\n\n" + block + "\n"
    p.write_text(s, encoding="utf-8")


def main(argv):
    D = json.loads((ROOT / "data/options_page.json").read_text(encoding="utf-8"))
    m = re.search(r"css/style\.css\?v=(\w+)", (ROOT / "options.html").read_text(encoding="utf-8"))
    css_v = m.group(1) if m else ""
    uni = universe(D)
    todo = [t for t in (argv or uni) if t in uni]
    pages = {}
    for tk in todo:
        s = slice_ticker(D, tk)
        for lang in ("zh", "en"):
            page, noindex = render(D, s, lang, css_v, uni)
            pages[page_files(tk)[0 if lang == "zh" else 1]] = (page, noindex, s)
    # 红线：先全部生成、全部检查，一处命中就一个都不写（半套新页＋半套旧页比整套旧页更糟）
    hits = [(fn.name, w) for fn, (page, _, _) in pages.items() for w in FORBIDDEN
            if re.search(re.escape(w), page, re.I)]
    if hits:
        for fn, w in hits[:20]:
            print(f"🔴 {fn}：出现禁词「{w}」", file=sys.stderr)
        print(f"🔴 撞红线 {len(hits)} 处，拒绝写盘（一页都没写）", file=sys.stderr)
        return 3
    OUT.mkdir(exist_ok=True)
    if not argv:   # 全量时清掉不在宇宙里的旧页（票被移出后页面不能留着装作还在更新）
        keep = {p for tk in uni for p in page_files(tk)}
        for old in OUT.glob("*.html"):
            if old not in keep:
                old.unlink()
                print(f"  删除不在宇宙里的旧页 {old.name}")
    if not argv:
        write_redirects(uni)
    n_noidx = 0
    for fn, (page, noindex, s) in pages.items():
        if not fn.exists() or fn.read_text(encoding="utf-8") != page:
            fn.write_text(page, encoding="utf-8")
        n_noidx += noindex
    print(f"个股页 {len(pages)} 页（{len(todo)} 只×中英），noindex {n_noidx} 页，数据日 {D['meta']['data_date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
