#!/usr/bin/env python3
"""恐惧的标价 · 数据管线
拉取公开数据 → 计算指标 → 写出 data/*.json（前端只读这些静态文件）。

数据源：
- 价格/指数：yfinance（^GSPC ^IXIC ^NDX ^VIX ^VXN）
- CNN 恐贪指数：whit3rabbit/fear-greed-data 每日存档（2011→）+ CNN 官方接口（当天值）
- 席勒 CAPE：multpl.com 月度表
"""
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")  # 2026-08-06 Klay 拍板：日期标签一律=数据的美东交易日，now() 不许推日期（UTC 案勘误）

import numpy as np
import pandas as pd
import requests
import yfinance as yf

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

FNG_ARCHIVE = "https://raw.githubusercontent.com/whit3rabbit/fear-greed-data/main/fear-greed.csv"
FNG_LIVE = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
MULTPL_CAPE = "https://www.multpl.com/shiller-pe/table/by-month"


# 许可标记：文件被单独拷走/热链时声明跟着走；改这句必须同步重写存量文件，否则新旧不一致
#
# 2026-07-19：把「本站的」与「不是本站的」分开写。此前整句是 "© Klay Wang"，
# 而文件里装着 4,909 个 Cboe 原始收盘值、Yahoo 价格、FINRA 成交量——对别人的事实
# 主张自己的版权、还在同一句里对外卖授权，与 data/README.md 自己写的
# 「这里没有任何一个数字是私有的」直接冲突。承诺不改，改机制。
# 能授权的只有编排、衍生计算与台账结构；原始数值各归各源。
NOTICE = ("Compilation, derived metrics and ledger structure © Klay Wang — "
          "PolyForm Noncommercial 1.0.0; commercial use requires a license "
          "(klaywang24+fearprice@gmail.com). "
          "Underlying values are facts transcribed from public sources "
          "(Cboe, FRED, FINRA, CFTC, Yahoo Finance, CNN) and remain subject to those "
          "sources' own terms — no ownership is claimed over them. "
          "https://chronicle.klay-wang.com")


def write_json(name: str, obj):
    path = DATA / name
    # 拉失败时上游函数会抛异常走到不了这里，留旧文件不覆盖
    if isinstance(obj, dict):
        obj = {"_notice": NOTICE, **{k: v for k, v in obj.items() if k != "_notice"}}
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
    print(f"  wrote {name} ({path.stat().st_size/1024:.0f} KB)")


def fetch_history(ticker: str, retries: int = 3) -> pd.DataFrame:
    # 美股含点代码（BRK.B）Yahoo 用连字符，欧股（MC.PA）保留点：原样优先、连字符兜底
    candidates = [ticker] + ([ticker.replace(".", "-")] if "." in ticker else [])
    for i in range(retries):
        for sym in candidates:
            try:
                df = yf.Ticker(sym).history(period="max", auto_adjust=True)
                if len(df) > 100:
                    df.index = df.index.tz_localize(None)
                    return df
            except Exception as e:
                print(f"  {sym} attempt {i+1} failed: {e}")
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"failed to fetch {ticker}")


# 价格全史的自家后备源（2026-08-06 加）。各取自家已发布过的最长那份。
_PRICE_FALLBACK = {
    "^GSPC": ("sp500_century.json", "close"),   # 1927 起，比 kindex 的 spx 更长
    "^IXIC": ("ixic_century.json", "close"),    # 1971 起
    "^NDX":  ("kindex.json", "ndx"),            # 这两条本来就只回溯到 2011
    "^VIX":  ("kindex.json", "vix"),
}


_GAPFILL: list = []


def _fill_index_gaps(s: pd.Series, fred_id: str, label: str, lookback_days: int = 730) -> pd.Series:
    """Fill trading days that Yahoo's index history silently omits, using FRED's official close.

    2026-09-24: Yahoo's ^GSPC / ^IXIC / ^NDX histories all skipped 2026-09-22 (a normal session);
    FRED SP500 had it. An append-only merge never revisits a hole, so the century charts carried
    the gap forward. Only dates strictly inside Yahoo's own range are filled (never extend either
    end); every fill is listed in meta.json.gap_filled with its source. If FRED is unreachable the
    gap stays and the failure is listed, never hidden."""
    try:
        if s is None or s.empty:
            return s
        start = (s.index.max() - pd.Timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        f = _fred(fred_id, start=start)
        inside = (f.index > s.index.min()) & (f.index < s.index.max()) & ~f.index.isin(s.index)
        holes = f[inside]
        if holes.empty:
            return s
        filled = pd.concat([s, holes.round(2)]).sort_index()
        for d, v in holes.items():
            _GAPFILL.append({"ticker": label, "date": d.strftime("%Y-%m-%d"), "value": round(float(v), 2), "source": f"FRED {fred_id}"})
        print(f"  ↪ {label}: Yahoo history missing {len(holes)} session(s) inside its range, filled from FRED {fred_id}: "
              + ", ".join(d.strftime("%Y-%m-%d") for d in holes.index[:5]))
        return filled
    except Exception as e:  # noqa: BLE001
        _FAILURES.append({"section": f"index gap check ({label} vs FRED {fred_id})", "error": f"{type(e).__name__}: {str(e)[:160]}"})
        print(f"  ⚠️ {label} gap check unavailable: {e}")
        return s


def _fill_vix_gaps(s: pd.Series, calendar: pd.Index, label: str = "^VIX") -> pd.Series:
    """Same rule as _fill_index_gaps, for VIX, but the second source is Cboe's own VIX history CSV
    (already used by build_vol_indices) rather than FRED VIXCLS, which FRED marks as Cboe-copyrighted.

    2026-09-24: Yahoo's ^VIX daily history also skipped 2026-09-22. build_kindex and build_sentiment's
    term structure drop any day where VIX is missing, so K index 09-22 and the term chart stayed short
    even after the three price indices were filled. Only dates strictly inside Yahoo's own range are
    filled; every fill goes to meta.json.gap_filled; if Cboe is unreachable the gap stays and is listed.

    ⚠️ Cboe publishes VIX on US market holidays (global trading hours); Yahoo rightly omits them. A first
    dry run filled 33 "gaps", nearly all holidays (Memorial Day, July 4, Thanksgiving, 2025-01-09 closure).
    So only dates that are also S&P 500 sessions (``calendar`` = the gap-filled ^GSPC index) are filled."""
    try:
        if s is None or s.empty:
            return s
        c = _cboe_close("VIX")
        inside = (c.index > s.index.min()) & (c.index < s.index.max()) & ~c.index.isin(s.index) & c.index.isin(calendar)
        holes = c[inside]
        if holes.empty:
            return s
        for d, v in holes.items():
            _GAPFILL.append({"ticker": label, "date": d.strftime("%Y-%m-%d"), "value": round(float(v), 2), "source": "Cboe VIX_History.csv"})
        print(f"  ↪ {label}: Yahoo history missing {len(holes)} session(s) inside its range, filled from Cboe: "
              + ", ".join(d.strftime("%Y-%m-%d") for d in holes.index[:5]))
        return pd.concat([s, holes.round(2)]).sort_index()
    except Exception as e:  # noqa: BLE001
        _FAILURES.append({"section": f"index gap check ({label} vs Cboe VIX)", "error": f"{type(e).__name__}: {str(e)[:160]}"})
        print(f"  ⚠️ {label} gap check unavailable: {e}")
        return s


def fetch_close_or_own(ticker: str) -> pd.Series:
    """取全史收盘序列；上游（Yahoo）全挂时回退**自家已发布过的同一序列**。

    ━━ 为什么价格也需要后备（2026-08-06 Klay 追问后补，与恐贪那半对称）━━
    `fetch_history` 取的是**全史**，不是今天一个点。Yahoo 挂掉时丢的不是一天，
    而是**重建整条序列的能力** —— 而 main() 开头那四行是**裸调**：任何一条抛错，
    整轮 daily 在第一步就死，后面所有 _guard 保护的小节一个都跑不到。

    ━━ 这不违反「永不编造」，界线在这里 ━━
      · 回退**历史**：拿回我们自己发布过、且已被哈希链与 Wayback 见证的值 → 合法
      · 伪造**今天**：拿昨天的价格冒充今天 → 绝不做
    后备序列止于最后一次发布日，**今天那一格自然缺席**；build_kindex 的
    `.dropna()` 会让合并框跟着止于同一天，所以**不会凭空多出一行**。
    缺格就是缺格，写明、不回填（同期权页第七章那条）。

    降级绝不静默：写进 _FAILURES → meta.json.failures → Discord 当天告警。
    """
    try:
        return fetch_history(ticker)["Close"]
    except Exception as e:
        src = _PRICE_FALLBACK.get(ticker)
        if not src:
            raise
        fname, key = src
        try:
            d = json.loads((DATA / fname).read_text(encoding="utf-8"))
            s = pd.Series(d[key], index=pd.to_datetime(d["dates"]), dtype=float).dropna()
            if len(s) < 100:
                raise ValueError(f"自家后备只有 {len(s)} 点，不足以重建")
            _FAILURES.append({"section": f"价格全史上游不可达（{ticker}）",
                              "error": f"{type(e).__name__}: {str(e)[:110]}"
                                       f" → 已回退自家 {fname} 的 {len(s)} 点历史（止于 "
                                       f"{s.index[-1]:%Y-%m-%d}），今日缺格、不回填"})
            print(f"  🟠 {ticker} 上游不可达，回退自家 {fname} {len(s)} 点（止于 {s.index[-1]:%Y-%m-%d}）")
            return s
        except Exception as e2:
            raise RuntimeError(f"{ticker} 上游不可达且自家后备也读不到：{e} / {e2}") from e


def dates(idx) -> list:
    return [d.strftime("%Y-%m-%d") for d in idx]


def rnd(s, n=2) -> list:
    return [None if pd.isna(v) else round(float(v), n) for v in s]


# ---------------------------------------------------------------- K 指数

def fetch_fng():
    """CNN 恐贪：whit3rabbit 存档（2011→）+ CNN 官方逐日定稿序列（滚动一年，权威）。
    返回 (Series, 末日 rating)。

    🚨 绝不取 fear_and_greed.score —— 那是「此刻」的实时读数，不是当日定稿值。
    2026-07-18 台账自核查出：管线曾把它当收盘值入账，7 个交易日的值事后被定稿值
    系统性下修 1.6–3.6 分（7/7 同向，不是源头重述是取数错），且 2026-07-15 因
    只存在于实时快照、从未进过任何定稿序列，事后整天消失。
    fear_and_greed_historical 才是官方逐日定稿序列，且每条自带 rating。
    口径：官方定稿序列优先；它只覆盖滚动一年，更早的历史仍由 whit3rabbit 存档提供。
    """
    from io import StringIO
    archive_note = ""
    try:
        csv = requests.get(FNG_ARCHIVE, headers=UA, timeout=30)
        csv.raise_for_status()
        fng = pd.read_csv(StringIO(csv.text), parse_dates=["Date"]).set_index("Date")["Fear Greed"]
    except Exception as e:
        # 🔑 后备：回退到**我们自己上次发布的** kindex.json 里的恐贪历史（2026-08-06 加）。
        # ━━ 为什么需要 ━━
        # 2011→去年这 13 年的恐贪值只有 FNG_ARCHIVE 这一条路（CNN 官方接口只覆盖滚动一年）。
        # 它是**第三方个人仓库**：真实风险不是投毒（verify_ledger 会抓历史被改），
        # 而是**弃养/删仓** —— 原来一个 raise_for_status 就让整轮 daily 死掉。
        # ━━ 为什么用自家副本而不是另存一份上游拷贝 ━━
        # 自家 kindex.json 已含全部 3896 天恐贪历史，且它**在 git 里、进哈希链、被 Wayback 见证**，
        # 来源比上游更硬：上游随时可被静默改写且我们事后无从得知，自家的改了 verify_ledger 当天抓。
        # ⚠️ 降级必须说出来（家规：机器坏了不要晒给读者，但绝不能对自己静默）：
        #    写进 _FAILURES → meta.json.failures → Discord 当天告警，并在产物里标注来源。
        try:
            prev = json.loads((DATA / "kindex.json").read_text(encoding="utf-8"))
            fng = pd.Series(prev["cnn"], index=pd.to_datetime(prev["dates"]), dtype=float).dropna()
            archive_note = f"archive_unreachable_used_own_last_published:{type(e).__name__}"
            _FAILURES.append({"section": "恐贪历史存档不可达",
                              "error": f"{type(e).__name__}: {str(e)[:120]}"
                                       f" → 已回退用自家上次发布的 {len(fng)} 天历史继续，站上非陈旧但来源已降级"})
            print(f"  🟠 恐贪存档不可达（{type(e).__name__}），回退自家上次发布值 {len(fng)} 天")
        except Exception as e2:
            raise RuntimeError(f"恐贪存档不可达且自家后备也读不到：{e} / {e2}") from e
    live_note = ""
    try:
        hist = requests.get(FNG_LIVE, headers=UA, timeout=30).json()["fear_and_greed_historical"]["data"]
        off = pd.DataFrame(hist)
        # 同一天可能有两条（00:00 定稿 + 当日尾盘刷新），normalize 后取最后一条
        off["d"] = pd.to_datetime(off["x"], unit="ms").dt.normalize()
        off = off.drop_duplicates("d", keep="last").set_index("d").sort_index()
        fng = pd.concat([fng[~fng.index.isin(off.index)], off["y"].astype(float)])
        live_note = str(off["rating"].iloc[-1])
    except Exception as e:
        print(f"  CNN official daily series unavailable, archive only: {e}")
    if archive_note:
        live_note = (live_note + " | " if live_note else "") + archive_note
    return fng[~fng.index.duplicated(keep="last")].sort_index(), live_note


def sanity_kindex_inputs(fng: pd.Series, vix: pd.Series) -> list[str]:
    """K 指数两个输入的合理性守卫（2026-08-06 新建 · 攻击者视角审计产物）。

    ━━ 为什么需要它 ━━
    K 指数 = 恐贪 ÷ VIX，而恐贪的 2011→去年这 13 年**每天从一个第三方个人仓库
    重新下载**（CNN 官方接口只覆盖滚动一年）。既有两道防线都盖不住「污染今天的新值」：
      · verify_ledger 比的是「以前发布过的值有没有被改」—— 新值没有历史可比，它放过
      · verify_gauge_math 用**同一份被污染的输入**重算 —— 它也放过
    ∴ 唯一能拦的是「这个数在物理上可能吗」。14 个数据源里此前只有 AIAE 有这层
    （见 build_aiae 的 sane_check），本函数按同一形状补上旗舰读数。

    ━━ 阈值怎么定的：由 15 年实测分布推出，不是拍的 ━━
      恐贪 实测 min 0 / max 97（定义域本就是 0-100）· 日跳幅 中位 2.6 / p99 15.8 / 史上最大 35.0
      VIX  实测 min 9.14 / max 82.69            · 日跳幅 中位 0.67 / p99 6.60 / 史上最大 24.86
    取「史上最大 × 约 1.3-1.4 倍」作阈值 —— 本函数的职责是抓**物理上不可能的值**
    （投毒 / 单位错 / 换错数据源），**不是抓极端行情**。
    🔑 家规：会误报的闸比没有闸更糟。真出现史上最猛的一天也不该响，只有明显不可能才响。

    只报不拦：返回问题清单交给 _FAILURES → meta.json.failures → Discord 当天告警。
    绝不因此中止管线 —— 真实的极端行情不该让整站停更。
    """
    problems: list[str] = []
    if fng is not None and len(fng.dropna()):
        f = fng.dropna()
        oob = f[(f < 0) | (f > 100)]
        if len(oob):
            problems.append(f"恐贪有 {len(oob)} 个值落在定义域 0-100 之外"
                            f"（例：{oob.index[0]:%Y-%m-%d}={oob.iloc[0]}）")
        jm = f.diff().abs().max()
        if pd.notna(jm) and jm > 45:          # 史上最大 35.0，留约 1.3 倍余量
            d = f.diff().abs().idxmax()
            problems.append(f"恐贪单日跳幅 {jm:.1f}（{d:%Y-%m-%d}）超过史上最大 35 的 1.3 倍，疑似源被换或被污染")
    if vix is not None and len(vix.dropna()):
        v = vix.dropna()
        oob = v[(v <= 0) | (v > 200)]         # 1987 年那次约 150，200 是物理上限量级
        if len(oob):
            problems.append(f"VIX 有 {len(oob)} 个值不在 (0,200]"
                            f"（例：{oob.index[0]:%Y-%m-%d}={oob.iloc[0]}）")
        jm = v.diff().abs().max()
        if pd.notna(jm) and jm > 35:          # 史上最大 24.86，留约 1.4 倍余量
            d = v.diff().abs().idxmax()
            problems.append(f"VIX 单日跳幅 {jm:.2f}（{d:%Y-%m-%d}）超过史上最大 24.9 的 1.4 倍，疑似单位错或源被换")
    return problems


def build_kindex(ndx_close: pd.Series, spx_close: pd.Series, vix_close: pd.Series):
    print("== K 指数")
    fng, live_note = fetch_fng()
    # 输入合理性守卫（只报不拦，见 sanity_kindex_inputs 头注）
    for msg in sanity_kindex_inputs(fng, vix_close):
        _FAILURES.append({"section": "K 指数输入合理性", "error": msg})
        print(f"  🔴 输入守卫：{msg}")
    df = pd.DataFrame({"cnn": fng, "vix": vix_close, "ndx": ndx_close, "spx": spx_close}).dropna()
    df = df[df.index >= "2011-01-01"]  # 恐贪存档 2011 起 → KAPX 回溯至此（历史回填，非事前记录）
    df["k"] = df["cnn"] / df["vix"]

    # K<1 信号分段：连续 K<1 的交易日聚成一个 episode，间隔 >10 个交易日算新信号
    below = df["k"] < 1
    episodes = []
    cur = None
    last_below_pos = None
    for pos, (dt, b) in enumerate(zip(df.index, below)):
        if b:
            if cur is None or (last_below_pos is not None and pos - last_below_pos > 10):
                if cur:
                    episodes.append(cur)
                cur = {"start": dt, "end": dt, "start_pos": pos, "min_k": df["k"].iloc[pos]}
            cur["end"] = dt
            cur["min_k"] = min(cur["min_k"], df["k"].iloc[pos])
            last_below_pos = pos
    if cur:
        episodes.append(cur)

    # 收益视界在「纯价格日历」上取（价格序列无缺日），而不是合并框行数——
    # CNN 存档 2021 年前有缺日（wayback 重建），按合并框行数取 +N 行会让视界悄悄变长。
    # 双锚对账：纳指（fwd*，策略实际交易标的）+ 标普（spx_fwd*，主流市场锚）
    ndx_full = ndx_close.dropna()
    spx_full = spx_close.dropna()
    sig_rows = []
    for ep in episodes:
        if ep["start"] < pd.Timestamp("2011-01-01"):
            continue
        p0 = int(ndx_full.index.get_indexer([ep["start"]], method="backfill")[0])
        entry = ndx_full.iloc[p0]
        row = {
            "start": ep["start"].strftime("%Y-%m-%d"),
            "end": ep["end"].strftime("%Y-%m-%d"),
            "days_below": int(((df.index >= ep["start"]) & (df.index <= ep["end"]) & below).sum()),
            "min_k": round(float(ep["min_k"]), 3),
            "ndx_entry": round(float(entry), 2),
        }
        for horizon in (20, 40, 60, 120, 250):
            p = p0 + horizon
            row[f"fwd{horizon}"] = round(float(ndx_full.iloc[p] / entry - 1) * 100, 2) if p < len(ndx_full) else None
        row["fwd_to_date"] = round(float(ndx_full.iloc[-1] / entry - 1) * 100, 2)
        # K 页净值曲线用：60 个交易日持有的离场日（纳指价格日历）
        row["exit60"] = ndx_full.index[min(p0 + 60, len(ndx_full) - 1)].strftime("%Y-%m-%d")
        ps = int(spx_full.index.get_indexer([ep["start"]], method="backfill")[0])
        s_entry = spx_full.iloc[ps]
        for horizon in (20, 40, 60, 120, 250):
            p = ps + horizon
            row[f"spx_fwd{horizon}"] = round(float(spx_full.iloc[p] / s_entry - 1) * 100, 2) if p < len(spx_full) else None
        row["spx_to_date"] = round(float(spx_full.iloc[-1] / s_entry - 1) * 100, 2)
        sig_rows.append(row)

    write_json("kindex.json", {
        "dates": dates(df.index),
        "cnn": rnd(df["cnn"], 1),
        "vix": rnd(df["vix"], 2),
        "ndx": rnd(df["ndx"], 2),
        "spx": rnd(df["spx"], 2),
        "k": rnd(df["k"], 3),
        "current": {
            "date": df.index[-1].strftime("%Y-%m-%d"),
            "cnn": round(float(df["cnn"].iloc[-1]), 1),
            "vix": round(float(df["vix"].iloc[-1]), 2),
            "k": round(float(df["k"].iloc[-1]), 3),
            "rating": live_note,
            # 2026-09-24 加：当晚（美东同一天）跑出的末格 CNN 是 CNN 的当日读数，次日才定稿，会改
            #   （09-21 当晚 33.7→定稿 34.17；09-24 00:05 那班的 09-23 已与定稿一致）。过了美东午夜再跑即视为定稿。
            #   正文引「昨天的 K」须读下一轮的站上历史（digest 闸㉚ E 项对账）。
            "provisional": pd.Timestamp.now(tz="America/New_York").strftime("%Y-%m-%d") == df.index[-1].strftime("%Y-%m-%d"),
        },
    })
    write_json("kindex_signals.json", {"signals": sig_rows, "since": "2011-01-01"})
    return sig_rows


# ------------------------------------------------------- 指数通用面板

def drawdown_episodes(close: pd.Series, threshold=-0.10):
    """从日线收盘价提取回撤 episode（峰值→谷底→修复）。"""
    cummax = close.cummax()
    dd = close / cummax - 1
    episodes = []
    in_dd = False
    peak_date = trough_date = None
    trough = 0.0
    for dt, v in dd.items():
        if not in_dd and v < 0:
            in_dd = True
            peak_date = close.loc[:dt].idxmax() if False else None  # set below
            # 峰值 = 此刻 cummax 对应的最近日期
            peak_date = close[close == cummax.loc[dt]].index[-1]
            trough, trough_date = v, dt
        elif in_dd:
            if v < trough:
                trough, trough_date = v, dt
            if v == 0:
                if trough <= threshold:
                    episodes.append({
                        "peak": peak_date.strftime("%Y-%m-%d"),
                        "trough": trough_date.strftime("%Y-%m-%d"),
                        "recovery": dt.strftime("%Y-%m-%d"),
                        "depth": round(trough * 100, 1),
                        "days_down": int((trough_date - peak_date).days),
                        "days_recover": int((dt - trough_date).days),
                    })
                in_dd = False
    if in_dd and trough <= threshold:
        episodes.append({
            "peak": peak_date.strftime("%Y-%m-%d"),
            "trough": trough_date.strftime("%Y-%m-%d"),
            "recovery": None,
            "depth": round(trough * 100, 1),
            "days_down": int((trough_date - peak_date).days),
            "days_recover": None,
        })
    return dd, episodes


def build_index_panels(prefix: str, close: pd.Series, vol_index: pd.Series | None = None,
                       vol_name: str = ""):
    """一个指数板块的全部面板数据。close 为日线收盘。"""
    print(f"== {prefix}")
    close = close.dropna()

    # 世纪图：日频（2020/1987 这类快速崩盘的尖 V 自然显出真实深度；月末收盘会把它抹平）
    daily_c = close.dropna()
    write_json(f"{prefix}_century.json", {"dates": dates(daily_c.index), "close": rnd(daily_c, 2)})

    # 月频序列：世纪图已改日频不再用它，但下面的「滚动 5 年年化」与「月度季节性」仍依赖。
    # ⚠️ bd24343 把世纪图改日频时连这行定义一起删了，导致本函数抛
    #    NameError: name 'monthly' is not defined —— daily-update 自 2026-07-12 起连续失败，
    #    站上数据一直冻结在 2026-07-10。改动本行前先确认 :239/:258/:267 是否还在用。
    monthly = close.resample("ME").last()

    # 年度回报 + 分桶
    annual = close.resample("YE").last().pct_change().dropna() * 100
    # 首年用年内首尾补（yfinance 首年不完整就跳过）
    write_json(f"{prefix}_annual.json", {
        "years": [d.year for d in annual.index],
        "returns": rnd(annual, 2),
    })

    # 回撤
    dd, episodes = drawdown_episodes(close)
    weekly_dd = dd.resample("W").min()
    write_json(f"{prefix}_drawdowns.json", {
        "dates": dates(weekly_dd.index),
        "dd": rnd(weekly_dd * 100, 2),
        "episodes": sorted(episodes, key=lambda e: e["depth"])[:25],
    })

    # 年内最大回撤 vs 年度收益
    rows = []
    for year, grp in close.groupby(close.index.year):
        if len(grp) < 60:
            continue
        intra = (grp / grp.cummax() - 1).min() * 100
        ret = (grp.iloc[-1] / grp.iloc[0] - 1) * 100
        rows.append({"year": int(year), "intra_dd": round(float(intra), 1), "ret": round(float(ret), 1)})
    write_json(f"{prefix}_intrayear.json", {"rows": rows})

    # 滚动 5 年年化（月频）
    m = monthly.dropna()
    roll5 = (m / m.shift(60)) ** (1 / 5) - 1
    roll5 = (roll5.dropna()) * 100
    write_json(f"{prefix}_rolling5y.json", {"dates": dates(roll5.index), "cagr": rnd(roll5, 2)})

    # 波动率（20 日 / 60 日年化）+ 恐慌指数
    ret_d = close.pct_change()
    vol20 = ret_d.rolling(20).std() * (252 ** 0.5) * 100
    vol60 = ret_d.rolling(60).std() * (252 ** 0.5) * 100
    vol_m = vol20.resample("W").last().dropna()
    out = {"dates": dates(vol_m.index), "vol20": rnd(vol_m, 2),
           "vol60": rnd(vol60.resample("W").last().reindex(vol_m.index), 2),
           "vol_index_name": vol_name}
    if vol_index is not None:
        vi = vol_index.resample("W").last().reindex(vol_m.index)
        out["vol_index"] = rnd(vi, 2)
    write_json(f"{prefix}_volatility.json", out)

    # 月度季节性
    mret = monthly.pct_change().dropna() * 100
    season = []
    for month in range(1, 13):
        sub = mret[mret.index.month == month]
        season.append({
            "month": month,
            "avg": round(float(sub.mean()), 2),
            "win": round(float((sub > 0).mean() * 100), 1),
        })
    write_json(f"{prefix}_seasonality.json", {"rows": season, "years": f"{monthly.index[0].year}–{monthly.index[-1].year}"})


# ---------------------------------------------------------------- 今日头版

SECTOR_ETFS = [
    ("XLK", "科技"), ("XLF", "金融"), ("XLV", "医疗保健"), ("XLY", "可选消费"),
    ("XLP", "必需消费"), ("XLE", "能源"), ("XLI", "工业"), ("XLB", "原材料"),
    ("XLU", "公用事业"), ("XLRE", "房地产"), ("XLC", "通信服务"),
]


def pick_index_quotes(idx_px, idx_spec, prev_q):
    """把下载结果翻译成 quotes 字典；缺的符号逐个降级而不是整轮崩。
    （抽出来是为了可单测——原逻辑埋在 build_pulse 里，而那函数会先下载 500 只成分股。）

    回 (quotes, missing)。任何符号缺席都**必须出声**：不出声的降级等于没发生。
    降级顺序：本轮真值 → pulse.json 旧值(标 stale=True) → 该指数本轮缺席。
    🔑 判据是 `len(s) >= 2` 不是 `len(s) >= 1`：chg 要拿末两根算，只有一根同样会崩。
    """
    quotes, missing = {}, []
    for sym, key in idx_spec:
        s = idx_px[sym].dropna() if (idx_px is not None and sym in idx_px.columns) else None
        if s is None or len(s) < 2:
            missing.append(sym)
            if key in prev_q:
                quotes[key] = dict(prev_q[key], stale=True)
                print(f"  🔴 {sym} 取不到（重试后仍空）⇒ 沿用 pulse.json 旧值并标 stale=True")
            else:
                print(f"  🔴 {sym} 取不到且无旧值可用 ⇒ 本轮该指数缺席")
            continue
        quotes[key] = {"close": round(float(s.iloc[-1]), 2),
                       "chg": round(float(s.iloc[-1] / s.iloc[-2] - 1) * 100, 2)}
    if missing:
        print(f"  ⚠️ 指数行情缺 {len(missing)}/{len(idx_spec)} 个：{'、'.join(missing)}"
              f" —— 已按「沿用旧值+标 stale」降级，本轮其余产物照常生成")
    return quotes, missing


PULSE_MIN_COVER = 400   # 与上面 200 日广度的 cover>=400 同一条尺子：成分股当日样本不足即视为「没取到」


def pulse_breadth_gate(tot, prev, min_cover=PULSE_MIN_COVER):
    """涨跌家数写盘前的覆盖率闸（2026-09-12 建）。
    返回 (ok, fields_to_reuse)：ok=True 照写；ok=False ⇒ 沿用 prev（上一份 pulse.json）里的
    涨跌分布/温度/板块字段，并在 stale 里标明它们来自哪一天。
    🔴 起因：2026-09-11 19:58 ET 那班 yfinance 只回 1 只成分股，1/0/0 照样写盘，
       把同日 18:05 那班算对的 335/2/166（503 只）覆盖成「1 涨 0 平 0 跌 · 100% 百分位」，
       首页就这么顶着一夜。同一个函数里 200 日广度早有 cover>=400 的闸，涨跌家数这一段没有。
    🔑 判据落在样本数上，不落在「和昨天差多少」上——差多少是市场的事，样本够不够是我们的事。
    prev 为 None（首次跑）时无旧可留 ⇒ 仍拒绝写垃圾：返回 (False, {})，调用方按缺席处理。"""
    if tot >= min_cover:
        return True, {}
    keep = {}
    if prev:
        for k in ("adv", "flat", "dec", "total", "adv_ratio", "sent_pct", "temp", "sectors", "val_pct"):
            if k in prev:
                keep[k] = prev[k]
        keep["stale"] = {"breadth_from": prev.get("date"),
                         "why": f"本轮成分股当日样本 {tot} < {min_cover}，涨跌分布/温度/板块沿用上一份"}
    return False, keep


def _missing_latest(px, ticks):
    """最新一个交易日没有收盘价的成分股（列缺席或该格为 NaN）。px 为空⇒全部算缺。"""
    if px is None or px.empty:
        return list(ticks)
    last = px.index.max()
    return [t for t in ticks if t not in px.columns or pd.isna(px.at[last, t])]


def _refetch_missing_closes(px, ticks, waits=(5, 15), chunk=110, download=None, sleep=time.sleep):
    """成分股批量下载漏票就补取（2026-09-25 建·Klay 令「漏洞依次修掉」）。

    🔴 勘误（09-25 本机复现）：建它时以为 09-23/09-24 的「样本 60」是批量下载漏票，**判错了**——
       真因是 Yahoo 日线整天缺 09-22，见 _drop_hole_days。本函数仍保留：它防的是另一种真实存在的病
       （Yahoo 对 GitHub 出口只回一半，同族先例＝09-24 基本面快照，752b2c27），复现当天也补回了 1 只超时的票。
    🔑 修法：批量下完后查「最新交易日缺收盘」的票，只对这些票新发请求重取，最多 2 次（退避 5/15 秒），
       用 combine_first 只填空格、不覆盖已取到的值。重取后仍缺的票照旧交给覆盖率闸判（闸不动）。
    🚫 不降低 PULSE_MIN_COVER，不拿别的源顶：够不够 400 仍是唯一判据。
    download/sleep 可注入，供 scripts/test_pulse_refetch.py 离线测试。"""
    dl = download or (lambda tks: yf.download(" ".join(tks), period="2y", interval="1d",
                                              progress=False, auto_adjust=True)["Close"])
    for n, wait in enumerate(waits, 1):
        miss = _missing_latest(px, ticks)
        if not miss:
            break
        print(f"  🟡 成分股最新交易日缺收盘 {len(miss)}/{len(ticks)} 只，第 {n} 次补取（先等 {wait}s）")
        sleep(wait)
        for i in range(0, len(miss), chunk):
            part = miss[i:i + chunk]
            try:
                new = dl(part)
            except Exception as e:                      # noqa: BLE001
                print(f"    补取一批 {len(part)} 只失败：{type(e).__name__}，下一轮再试")
                continue
            if isinstance(new, pd.Series):
                new = new.to_frame(part[0])
            if new is None or new.empty:
                continue
            px = new if (px is None or px.empty) else px.combine_first(new)
        px = px.dropna(how="all")
        print(f"    补取后仍缺 {len(_missing_latest(px, ticks))} 只")
    return px


def _drop_hole_days(px, min_cover=PULSE_MIN_COVER):
    """剔掉「中间某天大部分成分股都没有收盘价」的空洞日（2026-09-25 建）。返回 (px, holes)。

    🔴 起因（09-25 本机复现定案）：Yahoo 的日线历史**整天缺 09-22**（503 只里 443 只没有这一行，
       与世纪图缺 09-22 同一个洞）。后果两处，都不报错：
       ① 涨跌家数：09-23 的收益率是对 09-22 算的，09-22 为空 ⇒ 只剩 60 只 ⇒ 覆盖率闸沿用旧值；
       ② 200 日广度：rolling(200).mean() 窗口里有一个空值就整段为空 ⇒ 之后 **200 个交易日**
          每天都只剩 60 只 ⇒ breadth.json 永远停在 09-22。
       09-24 当晚曾误判为「批量下载漏票」并加了补取（_refetch_missing_closes，保留，防真的漏票）。
    🔑 判据只看覆盖：某天有收盘价的票 < min_cover 即空洞；**最后一行不剔**——今天不全是另一件事，
       归 pulse_breadth_gate 拒写，不能让它悄悄退成「昨天」。
    🚫 不填补（ffill 会把缺的那天造成「全体持平」）。剔掉后，空洞后一天的涨跌跨两个交易日，
       200 日均线少一天，这两点随 holes 写进产物，不隐瞒。"""
    if px is None or px.empty or len(px) < 2:
        return px, []
    cnt = px.notna().sum(axis=1)
    body = cnt.iloc[:-1]
    hole_idx = body[body < min_cover].index
    holes = [d.strftime("%Y-%m-%d") for d in hole_idx]
    if holes:
        print(f"  🟡 剔除 {len(holes)} 个空洞日（当天有收盘价的成分股 < {min_cover}）：{holes[-6:]}")
    return px.drop(index=hole_idx), holes


def build_pulse():
    """今日头版：市场温度（估值百分位+情绪百分位）/2、涨跌家数分布、板块当日涨跌。
    情绪 = 上涨家数占比 (涨 + 平/2)/总数 在近一年中的百分位；
    估值 = 标普 500 PE(TTM) 在 1871 年以来全历史中的百分位。"""
    print("== 今日头版")
    ticks = [r["ticker"].replace(".", "-")
             for r in json.loads((DATA / "sp500_constituents.json").read_text())["rows"]]
    frames = []
    for i in range(0, len(ticks), 110):
        chunk = ticks[i:i + 110]
        # 2y：多出的一年只为算 200 日均线广度；温度的情绪百分位仍显式取近一年
        df = yf.download(" ".join(chunk), period="2y", interval="1d",
                         progress=False, auto_adjust=True)["Close"]
        frames.append(df)
        time.sleep(2)
    px = pd.concat(frames, axis=1).dropna(how="all")
    px = _refetch_missing_closes(px, ticks)
    px, holes = _drop_hole_days(px)
    ret = px.pct_change().iloc[1:]
    FLAT = 0.0001  # |涨跌| < 0.01% 记为持平
    up = (ret > FLAT).sum(axis=1)
    flat = (ret.abs() <= FLAT).sum(axis=1)
    total = ret.notna().sum(axis=1)
    ratio = ((up + flat / 2) / total).iloc[-252:]  # 温度口径：近一年，勿随下载窗口变

    # 广度：% 成分股收于 200 日均线上（只留均线覆盖足够的交易日），与旧 breadth.json 合并累积
    try:
        ma200 = px.rolling(200).mean()
        valid = px.notna() & ma200.notna()
        above = ((px > ma200) & valid).sum(axis=1)
        cover = valid.sum(axis=1)
        b = (above / cover * 100)[cover >= 400].round(1)
        merged = {}
        old_b = DATA / "breadth.json"
        if old_b.exists():
            try:
                prev = json.loads(old_b.read_text())
                merged = dict(zip(prev.get("dates", []), prev.get("pct", [])))
            except Exception:
                pass
        merged.update({d.strftime("%Y-%m-%d"): float(v) for d, v in b.items()})
        b_dates = sorted(merged)
        b_vals = [merged[d] for d in b_dates]
        b_cur = b_vals[-1]
        b_pct = round(sum(1 for v in b_vals if v <= b_cur) / len(b_vals) * 100, 1)
        write_json("breadth.json", {
            "dates": b_dates, "pct": b_vals,
            "current": b_cur, "pctile": b_pct,
            "since": b_dates[0] if b_dates else None,
            # 本轮 2 年窗口里剔掉的空洞日（见 _drop_hole_days）：本轮不为这些天算广度，200 日均线少算这几天；
            # 旧文件里若早先已有这天的值（当晚数据源还没丢这行时算的），合并时照旧保留
            "holes_dropped": holes,
        })
    except Exception as e:
        print(f"  广度计算失败（留旧文件）: {e}")
    today = ratio.index[-1]
    adv, fl, tot = int(up.iloc[-1]), int(flat.iloc[-1]), int(total.iloc[-1])
    dec = tot - adv - fl
    sent_pct = round(float((ratio <= ratio.iloc[-1]).mean() * 100), 1)

    pe = json.loads((DATA / "sp500_pe_ttm.json").read_text())
    val_pct = round(sum(1 for v in pe["values"] if v <= pe["values"][-1]) / len(pe["values"]) * 100, 1)
    temp = round((val_pct + sent_pct) / 2, 1)

    # 板块当日涨跌（11 只 SPDR 行业 ETF）
    etf_px = yf.download(" ".join(e for e, _ in SECTOR_ETFS), period="5d",
                         interval="1d", progress=False, auto_adjust=True)["Close"]
    etf_chg = (etf_px.iloc[-1] / etf_px.iloc[-2] - 1) * 100
    sectors = [{"etf": e, "name": n, "chg": round(float(etf_chg[e]), 2)}
               for e, n in SECTOR_ETFS if e in etf_chg and pd.notna(etf_chg[e])]
    sectors.sort(key=lambda x: -x["chg"])

    # 指数当日行情 + 现有信号
    # 🔴 2026-08-18 修：本段原来是「一次请求当成一个判决」——
    #    `yf.download` 五个指数一把取，任何一个当时返回空 ⇒ `s.iloc[-1]` 抛
    #    IndexError: single positional indexer is out-of-bounds ⇒ **整个 build_data.py 死在这里**，
    #    后面的热力图/pulse/写盘全不执行，当轮一行都没提交，站上 kindex/pulse 停在前一天。
    #    实况：08-18 22:26 UTC 那轮如此挂掉；同一批符号在 18:4x 复测**五个全部正常** ⇒ 瞬时故障。
    # 🔑 病不在数据在结构，两点：
    #    ① **没有重试**：一次空返回就定终局（与 verify_against_archive 同族，见该文件 2026-08-18 注）。
    #    ② **没有逐符号容错**：一个符号空掉，另外四个也跟着陪葬——而本文件别处（广度计算、
    #       ndx constituents）都写着「失败留旧文件」继续跑。**同一个文件里，唯独这一处一坏全崩。**
    #    ③ 附带：原报错**不说是哪个符号**，只抛一句越界，定位要靠猜。
    # ⇒ 改成：整体最多试 3 次（退避 5/15 秒）；仍缺的符号**逐个跳过并明写是哪个**，
    #   本轮该指数沿用 pulse.json 里的旧值（宁可旧，不可崩）。
    IDX = (("^GSPC", "spx"), ("^NDX", "ndx"), ("^DJI", "dji"), ("^RUT", "rut"), ("^VIX", "vix"))
    idx_px = None
    for attempt, wait in enumerate((5, 15, 0)):
        try:
            idx_px = yf.download(" ".join(s_ for s_, _ in IDX), period="5d", interval="1d",
                                 progress=False, auto_adjust=True)["Close"]
        except Exception as e:
            print(f"  指数行情第 {attempt + 1} 次下载抛错：{type(e).__name__}: {str(e)[:80]}")
            idx_px = None
        ok = idx_px is not None and all(
            s_ in idx_px.columns and len(idx_px[s_].dropna()) >= 2 for s_, _ in IDX)
        if ok or not wait:
            break
        missing = [] if idx_px is None else [
            s_ for s_, _ in IDX if s_ not in idx_px.columns or len(idx_px[s_].dropna()) < 2]
        print(f"  指数行情第 {attempt + 1} 次不完整（缺 {missing or '全部'}），{wait}s 后重试")
        time.sleep(wait)

    prev_q = {}
    try:
        prev_q = json.loads((DATA / "pulse.json").read_text()).get("quotes") or {}
    except Exception:
        pass
    quotes, idx_missing = pick_index_quotes(idx_px, IDX, prev_q)
    fng = k = None
    try:
        kd = json.loads((DATA / "kindex.json").read_text())["current"]
        fng, k = kd["cnn"], kd["k"]
    except Exception:
        pass

    # 全成分股热力图：面积=市值、颜色=当日涨跌、按 GICS 行业分组（Finviz 式 treemap）
    sec_of = {r["ticker"].replace(".", "-"): r["sector"]
              for r in json.loads((DATA / "sp500_constituents.json").read_text())["rows"]}
    last_ret = ret.iloc[-1]
    heat = []
    for t in px.columns:
        c = last_ret.get(t)
        if pd.isna(c):
            continue
        try:
            mc = yf.Ticker(t).fast_info["market_cap"]
        except Exception:
            mc = None
        if not mc:
            continue
        heat.append([t.replace("-", "."), sec_of.get(t, "Other"),
                     round(float(c) * 100, 2), round(mc / 1e9, 1)])
        time.sleep(0.15)
    prev_pulse = None
    try:
        prev_pulse = json.loads((DATA / "pulse.json").read_text())
    except Exception:
        pass
    gate_ok, reuse = pulse_breadth_gate(tot, prev_pulse)
    if len(heat) >= PULSE_MIN_COVER:
        write_json("pulse_heatmap.json", {"date": today.strftime("%Y-%m-%d"), "rows": heat})
    else:
        print(f"  🔴 热力图只有 {len(heat)} 只（<{PULSE_MIN_COVER}）⇒ 留旧文件不写")

    out = {
        "date": today.strftime("%Y-%m-%d"),
        "temp": temp, "val_pct": val_pct, "sent_pct": sent_pct,
        "adv": adv, "flat": fl, "dec": dec, "total": tot,
        "adv_ratio": round(float(ratio.iloc[-1]) * 100, 1),
        "sectors": sectors, "quotes": quotes, "fng": fng, "k": k,
        "sent_window": f"{ratio.index[0].strftime('%Y-%m')}→",
    }
    # 空洞日紧挨在今天之前 ⇒ 今天的涨跌是对空洞前一天算的、跨两个交易日，写明，不冒充单日
    _prev = px.index[-2] if len(px) >= 2 else None
    _span = [h for h in holes if _prev is not None and _prev.strftime("%Y-%m-%d") < h < today.strftime("%Y-%m-%d")]
    if _span:
        out["ret_vs"] = {"date": _prev.strftime("%Y-%m-%d"), "skipped_holes": _span,
                         "why": "数据源整天缺这些交易日，涨跌按空洞前一个交易日算，跨多日"}
    if not gate_ok:
        if reuse:
            print(f"  🔴 成分股当日样本 {tot} < {PULSE_MIN_COVER} ⇒ 涨跌分布/温度/板块沿用 "
                  f"{reuse['stale']['breadth_from']} 那份，指数行情照常更新")
            out.update(reuse)
        else:
            print(f"  🔴 成分股当日样本 {tot} < {PULSE_MIN_COVER} 且无旧 pulse.json 可留 ⇒ 本轮不写 pulse.json")
            return
    elif not sectors and prev_pulse and prev_pulse.get("sectors"):
        print("  🔴 板块 ETF 当日全空 ⇒ sectors 沿用上一份")
        out["sectors"] = prev_pulse["sectors"]
        out["stale"] = {"sectors_from": prev_pulse.get("date")}
    write_json("pulse.json", out)


# ---------------------------------------------------------------- 宏观（FRED）

def _fred(series_id: str, start: str = "2000-01-01") -> pd.Series:
    """FRED 数据。首选官方 API（需免费 key，环境变量 FRED_API_KEY，Actions 里从 Secrets 注入）；
    无 key 时回退 fredgraph.csv（注意：该端点对数据中心/代理 IP 常不可达）。"""
    import os
    from io import StringIO
    key = os.environ.get("FRED_API_KEY")
    if key:
        url = ("https://api.stlouisfed.org/fred/series/observations"
               f"?series_id={series_id}&api_key={key}&file_type=json&observation_start={start}")
        for attempt in range(3):
            try:
                r = requests.get(url, headers=UA, timeout=60)
                r.raise_for_status()
                obs = r.json()["observations"]
                pairs = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "")]
                return pd.Series([v for _, v in pairs], index=pd.to_datetime([d for d, _ in pairs]))
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    r = requests.get(url, headers=UA, timeout=60)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df.columns = ["d", "v"]
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    df = df.dropna()
    df = df[df["d"] >= start]
    return pd.Series(df["v"].values, index=pd.to_datetime(df["d"]))


def _weekly(s: pd.Series, nd: int = 2):
    w = s.resample("W").last().dropna()
    return {"dates": dates(w.index), "values": rnd(w, nd)}


def _yoy(s: pd.Series, nd: int = 2):
    y = (s.pct_change(12).dropna()) * 100
    return {"dates": dates(y.index), "values": rnd(y, nd)}


def build_macro():
    """资金面 / 信用 / 物价 / 增长与就业 / **传导链管道层**。全部 FRED 公开序列，2000 年起。
    传导链管道层用到的三个已在此：hy_oas / ig_oas（信用价差·与 VIXHY 的信用波动率互为表里）、
    rrp（流动性水位）、discount_window（2026-08-28 加）。"""
    print("== 宏观（FRED）")
    out = {}
    fetch_plan = {
        # 资金面
        "sofr": ("SOFR", lambda s: _weekly(s)),
        "effr": ("EFFR", lambda s: _weekly(s)),
        "target": ("DFEDTARU", lambda s: _weekly(s)),
        "rrp": ("RRPONTSYD", lambda s: _weekly(s, 1)),          # 十亿美元
        "walcl": ("WALCL", lambda s: _weekly(s / 1e6, 3)),      # 百万→万亿美元
        # 信用 / 利率
        "dgs2": ("DGS2", lambda s: _weekly(s)),
        "dgs10": ("DGS10", lambda s: _weekly(s)),
        # 🆕 2026-09-22（Klay 拍板「接」）：10 年期**日频**。起因＝「快速加息」判据要 10 年期
        #    一个月 +50bp 或两周 +30bp，周频桶算不出两周差。与 dgs10 同一序列不同采样：
        #    周频那条留给传导链管道层，本条日频留给本机 每日十年期.csv 镜像。
        #    🚫 两条不互替；差值不在这里算（存原始日频，两周/一月差由读的人算＝不立第二把尺子）。
        #    ⚠️ FRED 日频滞后一个交易日（H.15 次日 16:15 ET 发），当天读到的是前一交易日。
        "dgs10_daily": ("DGS10", lambda s: {"dates": dates(s.index), "values": rnd(s, 2)}),
        "dgs20": ("DGS20", lambda s: _weekly(s)),
        "dgs30": ("DGS30", lambda s: _weekly(s)),
        "t10y2y": ("T10Y2Y", lambda s: _weekly(s)),
        # 🆕 2026-08-28（Klay 点名补·传导链管道层）：10Y-3M 是**纽约联储衰退模型**用的那条曲线，
        #    比 10Y-2Y 更标准。已 double check：1982 起 11,167 点、2019-08-27 倒挂 -0.49、
        #    2023-05-04 最深 -1.89、最近一次倒挂 2025-10-16、今日 +0.83（已转正）。两条并存不互替。
        "t10y3m": ("T10Y3M", lambda s: _weekly(s)),
        "hy_oas": ("BAMLH0A0HYM2", lambda s: _weekly(s)),
        "ig_oas": ("BAMLC0A0CM", lambda s: _weekly(s)),
        # 🆕 2026-08-28（传导链第三层「管道层」）：贴现窗口借款＝银行**已经在跑向央行**的直接证据。
        #    序列已 double check：2023 SVB 那周 4,581 → **152,853** 百万美元（33 倍，全史峰值就在
        #    2023-03-15），最新 2026-08-26 回落到 4,890 常态 ⇒ 确认这个号就是要的东西，不是贴现「利率」
        #    （那是 DPCREDIT，实测返回 2.25 的利率值，差一个字母意思全变）。
        "discount_window": ("WLCFLPCL", lambda s: _weekly(s, 0)),   # 百万美元·周频
        # 🆕 2026-08-28（Klay 点名·补「离岸美元荒」盲区）：央行流动性互换余额。
        #    与贴现窗口同性格＝**确凿证据不是预警**：国内银行跑向美联储看 discount_window，
        #    **外国**央行跑来借美元看这个。已 double check：2008-12-17 峰值 583,135 百万、
        #    2020-05-27 峰值 448,946 百万，而 1237 周里有 328 周（27%）读数为 0 ⇒
        #    「平时是 0，出事才有」得到证实；今日 121 百万≈零。
        #    🚫 它替代不了交叉货币基差（那是**事前**的价格信号、付费墙），只覆盖**事后**的余额。
        "cb_swaps": ("SWPT", lambda s: _weekly(s, 0)),              # 百万美元·周频
        # 🆕 2026-09-05（传导链新增「④水位层」＝池子里还有多少钱）。
        #    起因：管道层那七条全在答「压力大不大」，**没有一条答「水有多少」**。
        #    09-05 对照外部公开仪表盘时暴露的正是这个缺口。
        #    🔑 为什么加在这里而不是本机新写一个 FRED 取数器：家法「不要自己打 FRED 原始端点，
        #       两处 fetch 必漂移」——`walcl` 早就在本 plan 里，新的三条走同一扇门，
        #       本机侧只做镜像（fetch_transmission.do_liquidity），全仓仍只有 _fred 一个实现。
        #    单位与 walcl 对齐：H.4.1 三条原始单位都是百万美元。
        "tga": ("WTREGEN", lambda s: _weekly(s / 1e3, 1)),          # 财政部一般账户·百万→十亿美元
        "reserves": ("WRESBAL", lambda s: _weekly(s / 1e6, 3)),     # 银行准备金·百万→万亿美元
        # 期限溢价：长端「额外要的补偿」，与 dgs10 的水平是两回事（水平涨可能只是短端预期涨）。
        "term_premium": ("THREEFYTP10", lambda s: _weekly(s, 3)),   # 10 年期限溢价·%（美联储理事会 Kim-Wright，不是纽约联储 ACM）
        # 准备金余额利率：与已在拉的 sofr 配对才能算 SOFR-IORB 融资压力价差。
        # 🚫 价差本身**不在这里算**——存两条原始腿，派生值留给读的人（存派生值＝立第二把尺子）。
        "iorb": ("IORB", lambda s: _weekly(s, 2)),                  # 准备金余额利率·%
        # 🆕 2026-09-06（Klay 令补 09-05 遗漏的那条）：银行现金分层。
        #    上面几条答「系统里有多少钱」，这两条答**「钱在谁手里」**——
        #    2023 SVB 那次的形态正是**总量没少、但钱从小行搬到了大行**，只看总量的指标全程哑火。
        #    源＝FRED H.8 周报（季调），四条原始腿，比率在**展示层**算：
        #      现金缓冲 ＝ 现金资产 ÷ 总资产（全部商业银行）
        #      现金分层 ＝ 大型行现金比 − 小型行现金比（单位 pp）
        #    🚫 **只存四条腿，不存那两个比率**——同 §「不存净流动性派生值」：
        #       比率是派生值，存下来就成了会与分子分母漂移的第二把尺子。
        #    落库前已用独立公开源逐位交叉：
        #      缓冲 2943.5/25629.5＝11.485% vs 其 11.485% ✅
        #      分层 8.575%−6.925%＝+1.650pp vs 其 1.65pp ✅
        "bank_cash_all": ("CASACBW027SBOG", lambda s: _weekly(s, 1)),    # 现金资产·全部商业银行·十亿
        "bank_assets_all": ("TLAACBW027SBOG", lambda s: _weekly(s, 1)),  # 总资产·全部
        "bank_cash_large": ("CASLCBW027SBOG", lambda s: _weekly(s, 1)),  # 现金资产·大型
        "bank_assets_large": ("TLALCBW027SBOG", lambda s: _weekly(s, 1)),
        "bank_cash_small": ("CASSCBW027SBOG", lambda s: _weekly(s, 1)),  # 现金资产·小型
        "bank_assets_small": ("TLASCBW027SBOG", lambda s: _weekly(s, 1)),
        # 物价（同比）
        "cpi_yoy": ("CPIAUCSL", _yoy),
        "core_pce_yoy": ("PCEPILFE", _yoy),
        "ppi_yoy": ("PPIACO", _yoy),
        # 增长与就业
        "unrate": ("UNRATE", lambda s: {"dates": dates(s.index), "values": rnd(s, 1)}),
        "nfp": ("PAYEMS", lambda s: (lambda d: {"dates": dates(d.index), "values": rnd(d, 0)})(s.diff().dropna())),  # 千人/月
        "gdp_qoq": ("GDPC1", lambda s: (lambda g: {"dates": dates(g.index), "values": rnd(g, 1)})(((s / s.shift(1)) ** 4 - 1).dropna() * 100)),
        "cp_yoy": ("CP", lambda s: (lambda y: {"dates": dates(y.index), "values": rnd(y, 1)})((s.pct_change(4).dropna()) * 100)),  # 季频
    }
    for key, (sid, f) in fetch_plan.items():
        try:
            out[key] = f(_fred(sid))
        except Exception as e:
            print(f"  {sid} failed (kept old if any): {e}")
        time.sleep(0.6)
    if out:
        write_json("macro.json", out)


# ---------------------------------------------------------------- LEAPS 窗口

def build_leaps(spx_close: pd.Series, ndx_close: pd.Series, vix_close: pd.Series = None, threshold: float = 25.0):
    """CNN 恐贪 < 25 = 极端恐惧 = LEAPS call 开仓观察窗口。
    对 2011 年以来每个窗口计算 SPX/NDX 之后 6/12/18 个月（126/252/378 交易日）收益。"""
    print("== LEAPS 窗口")
    fng, live_note = fetch_fng()
    df = pd.DataFrame({"fng": fng, "spx": spx_close, "ndx": ndx_close}).dropna()

    below = df["fng"] < threshold
    episodes = []
    cur = None
    last_pos = None
    for pos, (dt, b) in enumerate(zip(df.index, below)):
        if b:
            if cur is None or pos - last_pos > 10:
                if cur:
                    episodes.append(cur)
                cur = {"start": dt, "start_pos": pos, "end": dt, "min": df["fng"].iloc[pos]}
            cur["end"] = dt
            cur["min"] = min(cur["min"], float(df["fng"].iloc[pos]))
            last_pos = pos
    if cur:
        episodes.append(cur)

    # 同 build_kindex：收益视界在纯价格日历上取，避免恐贪存档缺日拉长视界
    full = {"spx": spx_close.dropna(), "ndx": ndx_close.dropna()}
    rows = []
    for ep in episodes:
        row = {
            "start": ep["start"].strftime("%Y-%m-%d"),
            "end": ep["end"].strftime("%Y-%m-%d"),
            "days_below": int(((df.index >= ep["start"]) & (df.index <= ep["end"]) & below).sum()),
            "min_fng": round(ep["min"], 1),
        }
        for idx in ("spx", "ndx"):
            s = full[idx]
            p0 = int(s.index.get_indexer([ep["start"]], method="backfill")[0])
            entry = s.iloc[p0]
            for label, horizon in (("m6", 126), ("m12", 252), ("m18", 378)):
                p = p0 + horizon
                row[f"{idx}_{label}"] = round(float(s.iloc[p] / entry - 1) * 100, 1) if p < len(s) else None
            row[f"{idx}_to_date"] = round(float(s.iloc[-1] / entry - 1) * 100, 1)
        # 头版净值曲线用：12 个月持有的离场日（纳指价格日历 +252 交易日）
        pn = int(full["ndx"].index.get_indexer([ep["start"]], method="backfill")[0])
        pe = min(pn + 252, len(full["ndx"]) - 1)
        row["m12_exit"] = full["ndx"].index[pe].strftime("%Y-%m-%d")
        # 「恐惧的标价」：窗口开启当天的 VIX（保费水位）
        if vix_close is not None:
            vf = vix_close.dropna()
            pv = int(vf.index.get_indexer([ep["start"]], method="backfill")[0])
            row["vix_start"] = round(float(vf.iloc[pv]), 2)
        rows.append(row)

    write_json("leaps.json", {
        "threshold": threshold,
        "dates": dates(df.index),
        "fng": rnd(df["fng"], 1),
        "ndx": rnd(df["ndx"], 2),
        "spx": rnd(df["spx"], 2),
        "current": {
            "date": df.index[-1].strftime("%Y-%m-%d"),
            "fng": round(float(df["fng"].iloc[-1]), 1),
            "rating": live_note,
            "window_open": bool(df["fng"].iloc[-1] < threshold),
        },
        "episodes": rows,
    })


# --------------------------------------------------------- 情绪仪表盘
CBOE_HIST = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{}_History.csv"


def build_sentiment(vix_close: pd.Series, vxn_close: pd.Series = None):
    """情绪仪表盘：CNN 恐贪七子指标快照 + Put/Call 比（CNN 原始 5 日均值，滚动累积自建历史）
    + VIX 期限结构（Cboe 官方历史 CSV：VIX9D/VIX3M/VIX6M + yfinance VIX）
    + SKEW（黑天鹅保险价格，Cboe 官方 CSV）+ VXN/VIX 比值（纳指波动溢价）。"""
    print("== 情绪仪表盘")
    live = requests.get(FNG_LIVE, headers=UA, timeout=30).json()

    # --- 七个子指标当日快照（CNN 官方口径，score 0-100）
    SUB_KEYS = ["market_momentum_sp500", "stock_price_strength", "stock_price_breadth",
                "put_call_options", "market_volatility_vix", "junk_bond_demand", "safe_haven_demand"]
    subs = {}
    for k in SUB_KEYS:
        v = live.get(k) or {}
        if "score" in v:
            subs[k] = {"score": round(float(v["score"]), 1), "rating": v.get("rating", "")}
    # 2026-09-24 修：七分项的日期＝CNN 官方逐日序列末点（与 K 指数同一来源，fetch_fng 同法 normalize），
    #   不再借期限结构的末日——期限结构四条缺一条就停住，当轮现抓的分项会被标上旧日期（09-24 实例：标成 09-21）。
    subs_date = None
    try:
        _h = (live.get("fear_and_greed_historical") or {}).get("data") or []
        if _h:
            subs_date = pd.Timestamp(max(p["x"] for p in _h), unit="ms").strftime("%Y-%m-%d")
    except Exception as e:
        print(f"  七分项日期取不到（{type(e).__name__}），留空")

    # --- Put/Call 比：CNN put_call_options.data 的 y 就是原始 5 日均值比率（滚动一年窗口）
    #     每日运行时与已有 sentiment.json 合并，历史随管线逐日累积（超出一年后仍保留）
    pc_new = {}
    for pt in (live.get("put_call_options") or {}).get("data", []):
        d = pd.Timestamp(pt["x"], unit="ms").strftime("%Y-%m-%d")
        pc_new[d] = round(float(pt["y"]), 4)
    old_path = DATA / "sentiment.json"
    if old_path.exists():
        try:
            old_pc = json.loads(old_path.read_text()).get("pc", {})
            merged = dict(zip(old_pc.get("dates", []), old_pc.get("ratio", [])))
            merged.update(pc_new)  # 新值（含 CNN 修订）覆盖旧值
            pc_new = merged
        except Exception as e:
            print(f"  旧 sentiment.json 读取失败，仅用当次数据: {e}")
    pc_dates = sorted(pc_new)
    pc_vals = [pc_new[d] for d in pc_dates]
    pc_cur = pc_vals[-1] if pc_vals else None
    pc_pct = round(sum(1 for v in pc_vals if v <= pc_cur) / len(pc_vals) * 100, 1) if pc_vals else None

    # --- VIX 期限结构：Cboe 官方历史 CSV（开高低收，取 CLOSE）+ 管线自有 VIX
    def cboe_hist(name):
        r = requests.get(CBOE_HIST.format(name), headers=UA, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(pd.io.common.StringIO(r.text), parse_dates=["DATE"]).set_index("DATE")
        # VIX 系列是 OHLC（取 CLOSE）；SKEW 等单值序列只有一列
        col = "CLOSE" if "CLOSE" in df.columns else df.columns[-1]
        return df[col]

    term = pd.DataFrame({
        "vix9d": cboe_hist("VIX9D"),
        "vix": vix_close,
        "vix3m": cboe_hist("VIX3M"),
        "vix6m": cboe_hist("VIX6M"),
    }).dropna()
    term = term[term.index >= "2011-01-01"]
    ratio = term["vix"] / term["vix3m"]  # >1 = 倒挂（近端恐慌），<1 = 正常升水
    cur_ratio = round(float(ratio.iloc[-1]), 3)
    ratio_pct = round(float((ratio <= ratio.iloc[-1]).mean() * 100), 1)

    # --- SKEW：黑天鹅保险的价格（Cboe 官方全史，百分位按全史算，序列存 2011→）
    skew_obj = None
    try:
        skew = cboe_hist("SKEW").dropna()
        s_cur = round(float(skew.iloc[-1]), 1)
        s_pct = round(float((skew <= skew.iloc[-1]).mean() * 100), 1)
        sk = skew[skew.index >= "2011-01-01"]
        skew_obj = {"dates": dates(sk.index), "values": rnd(sk, 1),
                    "current": s_cur, "pctile": s_pct}
    except Exception as e:
        print(f"  SKEW 拉取失败（跳过该卡）: {e}")

    # --- VXN/VIX 比值：纳指波动溢价（>1 = 市场为纳指波动付更高保费）
    vxn_obj = None
    if vxn_close is not None:
        try:
            pair = pd.DataFrame({"vxn": vxn_close, "vix": vix_close}).dropna()
            nr = pair["vxn"] / pair["vix"]
            n_cur = round(float(nr.iloc[-1]), 3)
            n_pct = round(float((nr <= nr.iloc[-1]).mean() * 100), 1)
            nrs = nr[nr.index >= "2011-01-01"]
            vxn_obj = {"dates": dates(nrs.index), "ratio": rnd(nrs, 3),
                       "current": n_cur, "pctile": n_pct,
                       "vxn": round(float(pair["vxn"].iloc[-1]), 2)}
        except Exception as e:
            print(f"  VXN 比值失败（跳过该卡）: {e}")

    write_json("sentiment.json", {
        # date＝七分项（CNN 快照）的日期；term_date＝期限结构末日。09-24 前 date 用的是期限结构末日（见上 subs_date 注）。
        "date": subs_date or term.index[-1].strftime("%Y-%m-%d"),
        "term_date": term.index[-1].strftime("%Y-%m-%d"),
        "subs": subs,
        "skew": skew_obj,
        "vxn": vxn_obj,
        "pc": {"dates": pc_dates, "ratio": pc_vals, "current": pc_cur, "pctile": pc_pct},
        "term": {
            "dates": dates(term.index),
            "vix9d": rnd(term["vix9d"], 2), "vix": rnd(term["vix"], 2),
            "vix3m": rnd(term["vix3m"], 2), "vix6m": rnd(term["vix6m"], 2),
            "current": {"vix9d": round(float(term["vix9d"].iloc[-1]), 2),
                        "vix": round(float(term["vix"].iloc[-1]), 2),
                        "vix3m": round(float(term["vix3m"].iloc[-1]), 2),
                        "vix6m": round(float(term["vix6m"].iloc[-1]), 2),
                        "ratio_3m": cur_ratio, "pctile": ratio_pct,
                        "state": "backwardation" if cur_ratio > 1 else "contango"},
        },
    })


# ------------------------------------------------- 恐惧的标价指数（LEAPS 温度计）
# 头条 = VIX1Y 在过去 3 年的百分位，高=贵（0-100）；4 项 context 只算不平均进头条。
# 设计锁定见 KAPX-恐惧标价指数/REGISTRATION.md（v2）——改动=另立新兄弟，绝不追溯改。
# 云原生：VIX 家族取自 CBOE 官方 CSV，实际利率取自 FRED，SPX 用管线已有 ^GSPC。零本地依赖。
LEAPS_FORWARD_START = "2026-07-13"  # 首次上线日：此日(含)起=前向台账，之前=可复现回测。锁定常量，勿随每日运行改动。


def _cboe_close(name: str) -> pd.Series:
    """CBOE 官方历史 CSV → 日频收盘 Series（VIX 家族取 CLOSE，SKEW 取末列）。"""
    from io import StringIO
    r = requests.get(CBOE_HIST.format(name), headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df["DATE"] = pd.to_datetime(df["DATE"])
    col = "CLOSE" if "CLOSE" in df.columns else df.columns[-1]
    return pd.Series(pd.to_numeric(df[col], errors="coerce").values,
                     index=df["DATE"]).dropna().sort_index()


# ---- 2026-07-19：断供保护。恐惧的标价是唯一在赚钱的产品，而它此前单源、无备胎、每天全量覆盖写 ----
#
# 三件事分开看：
#   ① 历史（VIX1Y 4,909 天，2007→）——已经在 git 全史 + 哈希链 + Wayback 锚里，Cboe 关站也拿不走。
#      此前的问题不是拿不到，是「每天重抓重写」：源挂一天，19 年历史就整个消失。∴ 改成本地存底 + 只追加。
#   ② 每日增量（1 行）——Cboe 主，Yahoo 备。
#   ③ 两边都挂——保留存底，标记陈旧，让站上显式说「数据源中断」，绝不白屏也绝不假装新鲜。
#
# Yahoo 备胎口径实测（2026-07-19，非推断）：
#   ^VIX1Y 日线与 Cboe 官方收盘**逐日精确一致**（07-17 双方均 23.82，差 0.0000）→ 可入账。
#   ⚠️ 但 ^VIX1Y 在 Yahoo 只有最近 1 个交易日（period 传 max/5d/1d 均只返回 1 行，
#      传 max 直接报 "Period 'max' is invalid, must be one of: 1d, 5d"）——它能救「今天」，
#      救不了「漏掉的那天」。∴ 存底是必需的，不是锦上添花；且管线漏跑要能被发现（见 notify 侧）。
#   ⚠️ 小时线能给 5 天，但末根小时线与官方收盘差 0.01~0.07（收在 4:15 结算前）＝不同口径，
#      **绝不可用来回补**：混口径入账比缺一行严重得多。
#   其余四条 Yahoo 有全史（实测 ^VIX9D 3907 / ^VIX3M 5032 / ^VIX6M 4664 / ^SKEW 9128 行），
#   ^VIX9D 行数与 Cboe 完全一致。
CBOE_YAHOO_FALLBACK = {
    "VIX1Y": "^VIX1Y", "VIX9D": "^VIX9D", "VIX3M": "^VIX3M",
    "VIX6M": "^VIX6M", "SKEW": "^SKEW",
}
# 本次运行各序列的实际来源，写进 meta 供站上显示降级状态
_SOURCE_TRACE: dict[str, str] = {}
# 2026-09-23：Cboe 正常返回但停更时，用 Yahoo 补上的日期（写进 meta.yahoo_patched）
_YAHOO_PATCHED: dict[str, list] = {}
# 2026-09-25：各序列 Cboe 自己发布到的最后一天（判断早先补过的日期 Cboe 发没发）
_CBOE_LAST: dict[str, str] = {}
# 累计记录上线前已被每轮重写丢掉的补数日（有据可查才登记）：VIX1Y 2026-09-23＝21.69 由雅虎补，
# 证据＝提交 1b881f3f 的 leaps_gauge.json meta.yahoo_patched。Cboe 发布到该日后按 _merge_yahoo_patched 自动退场。
_YAHOO_PATCHED_SEED = {"VIX1Y": ["2026-09-23"]}


def _yahoo_close(sym: str) -> pd.Series:
    """Yahoo 备胎 → 日频收盘 Series。^VIX1Y 只有 1 交易日，其余为全史。"""
    df = yf.Ticker(sym).history(period="5d" if sym == "^VIX1Y" else "max")
    if df is None or df.empty:
        return pd.Series(dtype=float)
    s = pd.Series(df["Close"].values,
                  index=pd.to_datetime([d.date() for d in df.index]))
    return s.dropna().sort_index()


def _patch_stale_with_yahoo(name: str, s: pd.Series, now=None) -> pd.Series:
    """Cboe 正常返回、但末日落后于 Yahoo ⇒ 只把 Yahoo 更新的日期接到末尾（2026-09-23 加）。

    起因：09-23 Cboe 官网公共发布整体停在 09-22（历史 CSV 最后修改 09-22 21:51 ET、延迟行情接口
    VIX/VIX9D/VIX1Y 的 last_trade_time 全是 09-22 16:15），而 Yahoo 走 Cboe 行情专线，16:15 就有 09-23。
    旧备胎只在 Cboe **报错**时触发，「正常返回但停更」时不触发 ⇒ 温度计整晚停在 09-22。
    Klay 09-23 常设令：单一上游缺数，直接用对过账的第二源顶上。两源同一个官方收盘值（每日对账一致，
    07-17 起差 0.0000）。只补 Cboe 没有的新日期，已有日期一律用 Cboe；Cboe 日后发出同日值走
    _merge_append_only 的修订检测，不一致会公开记入 meta.revisions，不悄悄覆盖。"""
    try:
        y = _yahoo_close(CBOE_YAHOO_FALLBACK[name])
    except Exception as e:
        print(f"  {name}: Yahoo 查新鲜度失败（{type(e).__name__}），按 Cboe 原样用")
        return s
    if y.empty:
        return s
    _CBOE_LAST[name] = s.index.max().strftime("%Y-%m-%d")
    # 🔴 2026-09-25 补（与 _vol_yahoo_topup 同一条保护）：VIX 家族美东凌晨 3 点就开盘，雅虎凌晨就给出
    #    「今天」一行实时值，那不是收盘价。16:30 ET 前一律不收今天这一行。原先没有这道：
    #    09-24 00:05 那班恰好在开盘前才没出事，任何白天手动触发的轮次都会把盘中值写进只追加的存底。
    now = now or pd.Timestamp.now(tz="America/New_York")   # now 可注入，供 scripts/test_gauge_yahoo_patch.py
    today = pd.Timestamp(now.date())
    cutoff = today if now.hour * 60 + now.minute >= 16 * 60 + 30 else today - pd.Timedelta(days=1)
    newer = y[(y.index > s.index.max()) & (y.index <= cutoff)]
    if newer.empty:
        return s
    newer = newer.round(2)
    _SOURCE_TRACE[name] = "cboe+yahoo_patch"
    _YAHOO_PATCHED[name] = [d.strftime("%Y-%m-%d") for d in newer.index]
    print(f"  ↪ {name}: Cboe 末日 {s.index.max().date()} 落后，Yahoo 补 {_YAHOO_PATCHED[name]} = {list(newer.values)}")
    return pd.concat([s, newer]).sort_index()


def _merge_yahoo_patched(prev: dict, cur: dict, cboe_last: dict, series_dates: set) -> dict:
    """累计「哪些日期是雅虎补的」（2026-09-25 建）。

    本轮补的一律记；上一份文件里记过的日期，只要 Cboe 仍没发布到那天（> cboe_last）
    且这天还在序列里，就继续记。Cboe 发布到那天以后不再记：那之后同日值走修订检测，
    两源不一致会进 meta.revisions，一致就是已被官方确认。cboe_last 缺（本轮 Cboe 取数失败）
    ⇒ 旧记录照留，宁可多记不可漏记。"""
    out = {}
    for name in set(prev) | set(cur):
        ds = set(cur.get(name) or [])
        last = cboe_last.get(name)
        for d in prev.get(name) or []:
            if d in series_dates and (last is None or d > last):
                ds.add(d)
        if ds:
            out[name] = sorted(ds)
    return out


def _cboe_close_resilient(name: str) -> pd.Series:
    """Cboe 主 → Yahoo 备 → 空序列。永不抛异常：让调用方拿存底兜底，而不是整条管线倒下。"""
    try:
        s = _cboe_close(name)
        if s.empty:
            raise ValueError("Cboe 返回空序列")
    except Exception as e:
        print(f"  ⚠️ {name}: Cboe 拉取失败（{type(e).__name__}: {str(e)[:60]}），转 Yahoo 备胎")
    else:
        # 🔴 2026-09-25 移出 Cboe 的 try：原先补丁放在里面，补丁代码自己出任何错都会被当成「Cboe 失败」，
        #    把已经拿到的 Cboe 全史丢掉、改走 Yahoo 并标 yahoo_fallback（当晚对账随之跳过）。
        #    补丁只是锦上添花：它出错就按 Cboe 原样用。
        _SOURCE_TRACE[name] = "cboe"
        try:
            return _patch_stale_with_yahoo(name, s)
        except Exception as e:
            _SOURCE_TRACE[name] = "cboe"
            _YAHOO_PATCHED.pop(name, None)       # 补丁半路出错：已记的「补过哪天」要撤，不然 meta 声称补了序列里没有的日子
            print(f"  ⚠️ {name}: Yahoo 补新日期出错（{type(e).__name__}: {str(e)[:60]}），按 Cboe 原样用")
            return s
    try:
        s = _yahoo_close(CBOE_YAHOO_FALLBACK[name])
        if not s.empty:
            _SOURCE_TRACE[name] = "yahoo_fallback"
            print(f"  ✅ {name}: Yahoo 备胎取到 {len(s)} 行（末日 {s.index[-1].date()}）")
            return s
    except Exception as e:
        print(f"  ⚠️ {name}: Yahoo 备胎也失败（{type(e).__name__}: {str(e)[:60]}）")
    _SOURCE_TRACE[name] = "banked_only"
    return pd.Series(dtype=float)


# 修订判定阈值：台账按 2 位小数记账（rnd(v, 2)），真实修订至少是 0.01 量级；
# 而 Yahoo 备胎返回 float32（23.82 实际是 23.81999969482422，与存底差 ~3e-7）。
# 2026-07-19 实测：阈值设 1e-9 会让每次走备胎都假报「上游修订了历史值」——
# 这个站的信誉全押在修订记录可信上，**假报比不报严重得多**。
# 5e-4 远高于 float32 噪声（~2.4e-6）、远低于任何真实修订（≥0.01），两头都不沾。
_REVISION_EPS = 5e-4


def _merge_append_only(banked: pd.Series, fresh: pd.Series, label: str):
    """只追加合并：已入账的日子一律以存底为准，新日子才写入。返回 (合并序列, 分歧列表)。

    这是 data/README「修订政策」的代码级强制——此前那条政策只写在文档里，
    而 leaps_gauge 每天整份覆盖写（同 kindex 80 处分歧的根因 build_data.py:42）。
    上游日后修订历史值时：**旧行原样不动**，分歧另行记入 meta.revisions 公开，绝不悄悄覆盖。"""
    if banked.empty:
        return fresh.sort_index(), []
    revisions = []
    for d, old in banked.items():
        if d in fresh.index:
            new = float(fresh[d])
            if abs(new - float(old)) > _REVISION_EPS:
                revisions.append({"date": d.strftime("%Y-%m-%d"),
                                  "as_recorded": round(float(old), 4),
                                  "source_now": round(new, 4)})
    added = fresh[~fresh.index.isin(banked.index)]
    merged = pd.concat([banked, added]).sort_index()
    if len(added):
        print(f"  {label}: 存底 {len(banked)} 行 + 新增 {len(added)} 行 → {len(merged)} 行")
    else:
        print(f"  {label}: 存底 {len(banked)} 行，无新增（源末日 "
              f"{fresh.index[-1].date() if not fresh.empty else '—'}）")
    if revisions:
        print(f"  ⚠️ {label}: 上游修订了 {len(revisions)} 个历史值，旧行保持原样，已记入 meta.revisions")
    return merged, revisions


# 对账阈值：Cboe 与 Yahoo 都发官方 VIX1Y，实测同日逐字节一致（07-17 差 0.0000）。
# 用 0.10 抓「Cboe 悄悄给了错数」（陈旧值=昨天的、或损坏值，都会偏 ≥0.1），
# 同时容忍两源极小的取整差。比修订阈值 5e-4 宽得多——那是同源浮点噪声，这是跨源比对。
_RECONCILE_EPS = 0.10


def _reconcile_vix1y(series: pd.Series):
    """交叉验证：Cboe 正常时**额外**拉一次 Yahoo，比最新共有交易日的值。
    抓的是「Cboe 没报错、但数字悄悄错了」——只报错的备胎盖不住这种。
    返回 None（一致/无法比较）或 dict（分歧详情，写进 meta 让告警器响）。
    这是给运营者的内部核查，不上站横幅：两源不一致时并不知道哪个对。"""
    try:
        y = _yahoo_close("^VIX1Y")
        if y.empty or series.empty:
            return None
        common = series.index.intersection(y.index)
        # 2026-09-23：Yahoo 补上的日期不参与对账——那一格本来就是 Yahoo 的值，拿它比 Yahoo 是自证
        patched = set(pd.to_datetime(_YAHOO_PATCHED.get("VIX1Y", [])))
        common = common[~common.isin(patched)]
        if len(common) == 0:
            print("  对账：两源没有 Cboe 自有的共同日期（Yahoo 只留最近 1 天且那天是补上的）——本轮无从比较")
            return None
        d = common.max()
        cv, yv = float(series[d]), float(y[d])
        if abs(cv - yv) > _RECONCILE_EPS:
            print(f"  🟠 对账分歧 {d.date()}：Cboe {cv} vs Yahoo {yv}（差 {round(cv - yv, 4)}）")
            return {"date": d.strftime("%Y-%m-%d"), "cboe": round(cv, 4),
                    "yahoo": round(yv, 4), "diff": round(cv - yv, 4)}
        print(f"  ✅ 对账一致 {d.date()}：Cboe 与 Yahoo 差 {round(abs(cv - yv), 4)}")
    except Exception as e:
        print(f"  对账跳过（{type(e).__name__}）: {e}")
    return None


def _banked_series(fname: str, date_key: str, val_key: str) -> pd.Series:
    """从已发布的 JSON 取回本地存底序列。文件不存在/损坏 → 空序列（首次运行即为此）。"""
    p = DATA / fname
    if not p.exists():
        return pd.Series(dtype=float)
    try:
        j = json.loads(p.read_text())
        s = pd.Series(j[val_key], index=pd.to_datetime(j[date_key]))
        return s.dropna().sort_index()
    except Exception as e:
        print(f"  ⚠️ {fname} 存底读取失败（{type(e).__name__}），本次不做只追加保护: {e}")
        return pd.Series(dtype=float)


def _roll_pctile(s: pd.Series, window: int, min_periods: int = 250) -> pd.Series:
    """滚动百分位：当前值在过去 window 交易日内的分位（0-1，高值=高分位=贵）。"""
    return s.rolling(window, min_periods=min_periods).apply(
        lambda x: (x <= x[-1]).mean(), raw=True)


def _pctile_now(s: pd.Series, window: int | None = None):
    """当前值在（全史或末 window 观测）中的百分位 ×100。空序列返回 None。"""
    w = s.dropna()
    if window:
        w = w.iloc[-window:]
    if len(w) == 0:
        return None
    return round(float((w <= w.iloc[-1]).mean() * 100), 1)


def compute_leaps_index(vix1y, gspc, vix, vix9d, vix3m, vix6m, skew, dfii):
    """把已取好的各序列合成「恐惧的标价指数」台账 dict。纯函数（无网络），便于 dry-run。
    头条 = VIX1Y 3 年滚动百分位（高=贵）；4 context（VRP/期限阶梯/SKEW/实际利率）只算不平均进头条。"""
    idx = vix1y.dropna().index                        # 主日历 = VIX1Y 交易日（2007+）
    v = vix1y.reindex(idx).ffill()

    exp3y = _roll_pctile(v, 756) * 100                # 头条序列：高=贵（0-100）

    # VRP（波动税）：VIX1Y − SPX 已实现波动率（同标的、期限对齐）。主=252日(1年)，小字=63日(3月)。
    logret = np.log(gspc / gspc.shift(1))
    rv = lambda n: (logret.rolling(n).std() * np.sqrt(252) * 100).reindex(idx).ffill()
    rv252, rv63 = rv(252), rv(63)
    vrp_main, vrp_small = v - rv252, v - rv63

    def last(s):
        s = s.dropna()
        return None if s.empty else round(float(s.iloc[-1]), 2)

    cur_v = round(float(v.iloc[-1]), 2)
    cur_date = idx[-1].strftime("%Y-%m-%d")
    return {
        "meta": {
            "name": "恐惧的标价指数",
            "question": "今天买长期期权（LEAPS）在历史上算贵还是便宜？",
            "headline": "VIX1Y 在过去 3 年的百分位，高=贵（0=历史最便宜，100=历史最贵）",
            "nature": "描述性温度计，非交易信号/非预测；仅为数据，非投资建议。",
            "design": "描述性成本刻度 v2；方法论与口径详见本站 LEAPS 页",
            "forward_start": LEAPS_FORWARD_START,
            "segment_note": "dates < forward_start = 可复现回测(backtest)；>= forward_start = 前向台账(forward)，逐日 commit 记录。",
            "windows": {  # 各项口径分别标清——头条与 context 不是同一把尺，口径透明=诚实的一部分
                "headline_expensiveness": "VIX1Y 百分位；主看=近 3 年(756td)，小字坐标=近 5 年(1260td) / 全史(2007+)",
                "vrp": "值(非百分位)：VIX1Y − SPX 已实现波动率；主=252td(1 年) / 小字=63td(3 月)",
                "term_ladder": "当前值快照 + VIX/VIX1Y 比（非百分位）",
                "call_skew": "SKEW 的全史百分位",
                "real_rate": "DFII10(10 年期实际利率) 的全史百分位（慢变量，故用全史）",
            },
            "caveats": {
                "warmup": "早期(2007–2009)百分位在不足 756 交易日的窗口上计算(min_periods=250)，随历史积累趋于满 3 年基数。",
                "context_asof": "context 各值 as-of 其数据源各自最新印次；FRED 实际利率(DFII10)可能比头条日期滞后约 1 交易日。",
            },
        },
        "dates": dates(idx),
        "vix1y": rnd(v, 2),
        "expensiveness_3y": rnd(exp3y, 1),            # 头条：高=贵
        "vrp_main": rnd(vrp_main, 2),                 # VIX1Y − 252 日已实现
        "vrp_small": rnd(vrp_small, 2),               # VIX1Y − 63 日已实现
        "current": {
            "date": cur_date,
            "segment": "forward" if cur_date >= LEAPS_FORWARD_START else "backtest",
            "vix1y": cur_v,
            "expensiveness": {                        # 头条读数 + 三窗口坐标（主看 3 年，小字 5 年/全史）
                "p3y": _pctile_now(v, 756),
                "p5y": _pctile_now(v, 1260),
                "pfull": _pctile_now(v),
            },
            "context": {                              # 4 项，展示不平均进头条（每项独立经济论点）
                "vrp": {"main": last(vrp_main), "small": last(vrp_small),
                        "rv252": last(rv252), "rv63": last(rv63)},
                "term_ladder": {"vix9d": last(vix9d), "vix": last(vix), "vix3m": last(vix3m),
                                "vix6m": last(vix6m), "vix1y": cur_v,
                                "ratio_vix_vix1y": round(float(vix.dropna().iloc[-1] / cur_v), 3)},
                "call_skew": {"value": last(skew), "pctile_full": _pctile_now(skew)},
                "real_rate": {"value": last(dfii), "pctile_full": _pctile_now(dfii)},
            },
        },
    }


def build_leaps_index(gspc_close: pd.Series, vix_close: pd.Series):
    """恐惧的标价指数旗舰台账 → data/leaps_gauge.json。

    2026-07-19 起：VIX1Y 走「本地存底 + 只追加 + Yahoo 备胎」，源全挂也不丢历史、不覆盖旧行。
    其余四条 Cboe 序列只用于 context 当前值快照（不入台账字段），故只做备胎不做存底。"""
    print("== 恐惧的标价指数（LEAPS 温度计）")
    _SOURCE_TRACE.clear()
    _YAHOO_PATCHED.clear()

    # 头条输入：存底优先、只追加。这是全站唯一在赚钱的读数，单独按最高规格保护。
    banked = _banked_series("leaps_gauge.json", "dates", "vix1y")
    vix1y, revisions = _merge_append_only(banked, _cboe_close_resilient("VIX1Y"), "VIX1Y")
    if vix1y.empty:
        raise RuntimeError("VIX1Y 三条路（Cboe / Yahoo / 本地存底）全空——绝不写出空台账，留旧文件")

    out = compute_leaps_index(
        vix1y, gspc_close.dropna(), vix_close.dropna(),
        _cboe_close_resilient("VIX9D"), _cboe_close_resilient("VIX3M"),
        _cboe_close_resilient("VIX6M"), _cboe_close_resilient("SKEW"),
        _fred("DFII10", start="2003-01-01"),
    )

    # 降级状态写进 meta：站上据此显示「数据源中断」，绝不白屏，也绝不假装数据是新鲜的
    stale_days = (pd.Timestamp(datetime.now(ET).date()) - vix1y.index[-1].normalize()).days  # 2026-08-06 UTC 案同族修
    out["meta"]["sources"] = dict(_SOURCE_TRACE)
    # Cboe 停更时 Yahoo 补的日期（2026-09-23 起）。🔴 2026-09-25 改为累计：VIX1Y 是只追加存底，
    # 雅虎 ^VIX1Y 每次只给最近 1 天 ⇒ 原先每轮重写，09-23 那天的补数值留在序列里、记号却丢了。
    try:
        _prev_yp = json.loads((DATA / "leaps_gauge.json").read_text()).get("meta", {}).get("yahoo_patched") or {}
    except Exception:
        _prev_yp = {}
    for _k, _ds in _YAHOO_PATCHED_SEED.items():
        _prev_yp.setdefault(_k, [])
        _prev_yp[_k] = sorted(set(_prev_yp[_k]) | set(_ds))
    _yp = _merge_yahoo_patched(_prev_yp, _YAHOO_PATCHED, _CBOE_LAST, set(out["dates"]))
    if _yp:
        out["meta"]["yahoo_patched"] = _yp
    out["meta"]["headline_source"] = _SOURCE_TRACE.get("VIX1Y", "unknown")
    out["meta"]["stale_days"] = int(stale_days)
    # 4 天＝跨一个周末+一个假日的上限；与 make_card.sh 的新鲜度闸同阈值，两处口径别打架
    out["meta"]["degraded"] = bool(_SOURCE_TRACE.get("VIX1Y") == "banked_only" or stale_days > 4)
    if revisions:
        out["meta"]["revisions"] = revisions        # 上游改了历史值：旧行不动，分歧公开在此
    if out["meta"]["degraded"]:
        print(f"  🔴 降级：headline_source={out['meta']['headline_source']} stale_days={stale_days}")

    # 每日对账（2026-07-20）：只在源=Cboe 时做——已经走了 Yahoo 备胎就没有独立第二源可比。
    # 抓「Cboe 没报错但数字悄悄错了」，只报错的备胎盖不住这种。分歧写进 meta 让告警器响，
    # 不设站上横幅（两源不一致时不知道哪个对，是给运营者查的内部信号，不是给读者的状态）。
    if str(_SOURCE_TRACE.get("VIX1Y", "")).startswith("cboe"):   # cboe+yahoo_patch 也照常对账（比两边共有的最近一天）
        rec = _reconcile_vix1y(vix1y)
        if rec:
            out["meta"]["reconcile_warning"] = rec

    write_json("leaps_gauge.json", out)


# ---------------- 期货维度两源（2026-07-17）：VX 期限结构 + CFTC COT ----------------
# 官方免费源、不依赖任何券商（期权侧的断粮备份）。拉挂了只留旧文件，绝不拖死主管线。

VX_SETTLE = "https://www.cboe.com/us/futures/market_statistics/settlement/csv"


def _vx_curve_on(day) -> list:
    """该日 VX 标准月结算曲线 [[到期日, 结算价], ...]；无数据（周末/未发布）返回 []。"""
    r = requests.get(VX_SETTLE, params={"dt": day.strftime("%Y-%m-%d")}, headers=UA, timeout=30)
    if r.status_code != 200:
        return []
    rows = []
    for line in r.text.strip().splitlines()[1:]:
        p = line.split(",")
        # 只取标准月（符号形如 VX/N6）；周链（VX30/N6）与父月同值，画曲线只会重复
        if len(p) >= 4 and p[0] == "VX" and p[1].startswith("VX/"):
            try:
                rows.append([p[2], round(float(p[3]), 4)])
            except ValueError:
                pass
    rows.sort()
    return rows


def build_vx_curve(vix_close: pd.Series):
    """VIX 期货逐月结算曲线（真钱轨，与指数轨互证）→ data/vx_curve.json。历史逐日累积。"""
    print("== VX 期货期限结构")
    # 2026-08-06 修（UTC 案）：美东 20:00 后 UTC 已是次日，而 Cboe 端点对未结算日期也回吐最近数据
    # ⇒ 08-05 结算被错标 asof=08-06（本地台账同日实证）。日期起点必须用美东日历。
    today = datetime.now(ET).date()
    asof, curve = None, []
    for back in range(6):  # 当晚结算未发布/周末则回退到最近有数的交易日
        d = today - timedelta(days=back)
        curve = _vx_curve_on(d)
        if curve:
            asof = d
            break
    if not curve:
        raise RuntimeError("Cboe VX settlement 连续 6 日无数据")
    wk_asof, wk_curve = None, []
    for back in range(7, 13):
        d = today - timedelta(days=back)
        wk_curve = _vx_curve_on(d)
        if wk_curve:
            wk_asof = d
            break
    hist = {}
    try:
        hist = json.loads((DATA / "vx_curve.json").read_text()).get("history", {})
    except Exception:
        pass
    hist[asof.isoformat()] = curve
    write_json("vx_curve.json", {
        "asof": asof.isoformat(),
        "spot_vix": round(float(vix_close.dropna().iloc[-1]), 2),
        "curve": curve,
        "week_ago": {"asof": wk_asof.isoformat() if wk_asof else None, "curve": wk_curve},
        "history": hist,
    })


COT_TFF = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"


def build_cot_vix():
    """CFTC TFF（金融期货持仓）里 VIX 期货的机构站位，全史一次回填（2006→）→ data/cot_vix.json。
    ⚠️ 该数据集列名没有 _all 后缀（lev_money_positions_long，不是 …_long_all）。
    周五 15:30 ET 发布周二数据：每日拉最新即可（幂等），周五自动出新。"""
    print("== CFTC COT（VIX 期货站位）")
    r = requests.get(COT_TFF, params={
        "$where": "starts_with(market_and_exchange_names,'VIX')",
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": "5000",
        "$select": ("report_date_as_yyyy_mm_dd,lev_money_positions_long,lev_money_positions_short,"
                    "asset_mgr_positions_long,asset_mgr_positions_short,open_interest_all"),
    }, headers=UA, timeout=60)
    r.raise_for_status()
    series = []
    for row in r.json():
        try:
            ll, ls = int(row["lev_money_positions_long"]), int(row["lev_money_positions_short"])
            al, am = int(row["asset_mgr_positions_long"]), int(row["asset_mgr_positions_short"])
            # 四条腿原样保留（2026-07-17 落库线提出：净额看不出「空头平了还是多头加了」）
            series.append({
                "date": row["report_date_as_yyyy_mm_dd"][:10],
                "lev_long": ll, "lev_short": ls, "lev_net": ll - ls,
                "am_long": al, "am_short": am, "am_net": al - am,
                "oi": int(row["open_interest_all"]),
            })
        except (KeyError, ValueError):
            pass
    if not series:
        raise RuntimeError("CFTC TFF 返回空")
    series.sort(key=lambda x: x["date"])
    write_json("cot_vix.json", {
        "latest": series[-1],
        "short_weeks_52": sum(1 for s in series[-52:] if s["lev_net"] < 0),
        "series": series,
    })


def build_move():
    """ICE BofA MOVE 指数（美债期权隐含波动率）→ data/move.json（2026-08-28 新建·传导链保费层·利率腿）。

    ━━ 它在传导链里是什么 ━━
    MOVE 之于债市 = VIX 之于股市：**利率保险的市场价**。它与本仓 VIXHY（信用保险的波动率）
    构成保费层的两根**历史长腿**——我们自己的 TLT/银行 IV 序列 2026-08-28 才起算、补不回来，
    而这两根有十几年史，回测与分位判据全靠它们。

    ━━ 2026-08-28 回测实证（建线依据，别删）━━
    · 两条腿会分家：2016 能源债危机 信用响而利率哑（MOVE 峰值仅 41 分位）；
      2022 加息 利率响而信用哑（VIXHY 峰值 89 未破 90）。⇒ 测的是两种不同的恐惧。
    · 先响者指认危机类型：2016/2018=信用型先响；2020/2022/2023/2025=利率型先响。
    · **误报率必须随读数一起公布**：3 年分位首破 90 的报警，MOVE 命中 4/8、VIXHY 5/13（9 个月窗）
      ⇒ 本线定性为**仪表盘不是预测器**，只报"市场在为哪种灾难付保费"，不预测会不会出事。

    🚫 FRED 无 MOVE（ICE 专有指数，实测 VIXCLS 通而 MOVE 无号）⇒ 走 yfinance ^MOVE。
    """
    print("== MOVE（美债期权隐含波动率·传导链利率腿）")
    df = yf.download("^MOVE", period="max", interval="1d", auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise RuntimeError("^MOVE 返回空")
    s_ = df["Close"]
    if hasattr(s_, "columns"):
        s_ = s_.iloc[:, 0]
    s_ = s_.dropna()
    pct = s_.rolling(756, min_periods=252).apply(lambda x: (x <= x[-1]).mean() * 100, raw=True)
    write_json("move.json", {
        "_what": "ICE BofA MOVE：美债期权隐含波动率＝利率保险的市场价。pctl_3y=756 交易日滚动百分位"
                 "（与恐惧的标价/信用的标价同一把尺）。",
        "_caveat": "报警（分位≥90）历史命中率约 4/8 —— 仪表盘不是预测器。",
        "latest": {"date": s_.index[-1].strftime("%Y-%m-%d"), "value": round(float(s_.iloc[-1]), 2),
                   "pctl_3y": (None if pd.isna(pct.iloc[-1]) else round(float(pct.iloc[-1]), 1))},
        "dates": dates(s_.index), "values": rnd(s_, 2),
        "pctl_3y": [None if pd.isna(v) else round(float(v), 1) for v in pct],
    })


def build_policy_path():
    """联邦基金期货隐含利率 → data/policy_path.json（2026-08-28 新建·Klay 拍板）。

    ━━ 为什么自己算而不用 CME FedWatch ━━
    · CME 官网实测 **403**（fedwatch/probabilities 与行情端点都被挡），且条款不许再分发
      ⇒ 抓它＝把管线架在随时会断的墙上（本仓红线：抓取被掐＝整条线断粮）。
    · FedWatch 的「降息概率 78%」是**被它的模型加工过的二手数字**；我们要的是
      **预期翻转的速度**，用原始隐含利率更干净：隐含利率 = 100 − 期货价（一个减法，
      公式可公开、可复现、永不静默改 —— 与本站三条指数同一个性格）。

    ━━ 读法（Klay 定）━━
    **翻转速度才是恐慌信号，绝对水平不是。** 故本 JSON 除水平值外，重点给
    `chg_5d` / `chg_20d`（隐含利率的 5/20 交易日变化，单位 bp）与其 3 年分位。
    2023-03 SVB 那周市场对政策路径的定价 48 小时内翻转，比银行股崩得还快。

    ⚠️ ZQ=F 是**连续合约**，换月当日会有跳变 —— 变化量因此可能出现假尖峰。
       本函数如实记录不做平滑（家法：表只存事实，判据留给读的人），
       但在 _caveat 写明；日后若要精确，须改用分月合约自行接续（另一个工程）。
    ⚠️ SOFR 期货（SR3=F）实测 yfinance 只回 1 天历史 ⇒ **本次不采**，只用 ZQ。
    """
    print("== 联邦基金期货隐含利率（政策路径）")
    df = yf.download("ZQ=F", period="max", interval="1d", auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise RuntimeError("ZQ=F 返回空")
    px = df["Close"]
    if hasattr(px, "columns"):
        px = px.iloc[:, 0]
    px = px.dropna()
    implied = 100.0 - px                       # 期货价 → 隐含联邦基金利率（%）
    d1 = (implied.diff() * 100)                # 单日变化 bp
    # 🚨 **换月检测（2026-08-28 首跑当场抓到，不是预防性代码）**：
    #    实测当日 ZQ 单日跳 +15.5bp，而**前 11 个交易日全在 ±0.3bp** 内（3.63 一线纹丝不动）
    #    ⇒ 真实政策重定价不会这样，那是连续合约从 9 月换到 10 月合约的接缝。
    #    若不标出来，它会让 |5日变化| 冲到 98 分位、在仪表盘上伪装成「市场刚刚改主意了」。
    #    **判据**：|单日变化| > 8bp 且 前 5 日 |单日变化| 中位数 < 1bp ⇒ 判为换月跳变。
    #    （只标记不删除——表只存事实，家法；读的人据 roll_flag 自行剔除。）
    _med5 = d1.abs().rolling(5).median().shift(1)
    roll_flag = ((d1.abs() > 8) & (_med5 < 1)).fillna(False)
    chg5 = (implied.diff(5) * 100).dropna()    # bp
    chg20 = (implied.diff(20) * 100).dropna()
    def _pctl(x, win=756):
        return x.rolling(win, min_periods=252).apply(lambda v: (v <= v[-1]).mean() * 100, raw=True)
    p5 = _pctl(chg5.abs())                     # 用绝对值：**翻转速度**不分方向
    write_json("policy_path.json", {
        "_what": "ZQ=F(联邦基金期货)收盘 → 隐含利率=100−价格。chg_5d/chg_20d 单位 bp；"
                 "pctl_abs_chg5d=|5日变化|的 756 交易日滚动分位＝**翻转速度**分位。",
        "_caveat": "🚫 ZQ=F 是连续合约，换月当日跳变会造成变化量假尖峰（本表如实记录不平滑）。"
                   "🚫 隐含利率≠降息概率：后者要对整条曲线建模，本表只给单一近月合约的水平与变化。",
        "roll_flag_note": "True=该日被判为连续合约换月跳变（|单日变化|>8bp 且前 5 日中位<1bp），"
                          "其 chg_5d/chg_20d 在其后 5/20 日内均不可信 —— 读的人自行剔除，本表不删事实。",
        "latest": {
            "date": implied.index[-1].strftime("%Y-%m-%d"),
            "is_roll": bool(roll_flag.iloc[-1]),
            "price": round(float(px.iloc[-1]), 4),
            "implied_rate": round(float(implied.iloc[-1]), 4),
            "chg_5d_bp": (None if chg5.empty else round(float(chg5.iloc[-1]), 1)),
            "chg_20d_bp": (None if chg20.empty else round(float(chg20.iloc[-1]), 1)),
            "pctl_abs_chg5d": (None if p5.empty or pd.isna(p5.iloc[-1]) else round(float(p5.iloc[-1]), 1)),
        },
        "dates": dates(implied.index), "implied_rate": rnd(implied, 4),
        "chg5_dates": dates(chg5.index), "chg_5d_bp": rnd(chg5, 1),
        "roll_flag": [bool(v) for v in roll_flag.reindex(implied.index).fillna(False)],
        "pctl_abs_chg5d": [None if pd.isna(v) else round(float(v), 1) for v in p5],
    })


# 银行优先股＝单名 CDS 的免费替代（2026-08-28 Klay 拍板）。
# 逻辑最接近 CDS：同样问「这家银行还不还得上钱」，只是从**资本结构的中间层**看。
# 2023 瑞信 AT1 被清零那次，优先股早于普通股崩 ⇒ 真危机时它与股票期权分岔，正是我们要的那一维。
# 代码逐个 yfinance 实测（2026-08-28，6mo≥100 天才收）；C-PK 实测无数据已剔除。
BANK_PREFERRED = {
    "BAC-PL": "美国银行 优先股 L", "BAC-PK": "美国银行 优先股 K",
    "C-PN": "花旗 优先股 N",
    "JPM-PC": "摩根大通 优先股 C", "JPM-PD": "摩根大通 优先股 D",
    "WFC-PL": "富国 优先股 L", "GS-PA": "高盛 优先股 A",
    "PFF": "优先股 ETF（全市场·粗尺）", "KBWB": "银行股 ETF（对照组）",
}


def build_bank_credit():
    """银行优先股/银行 ETF 价格 → data/bank_credit.json（单名 CDS 的免费替代）。

    ⚠️ **它不是 CDS**，差别必须随读数一起说：CDS 是「违约保险的年费」，优先股价是「股权价」；
       平时高度相关，**真出事时优先股会先于普通股崩**（2023 瑞信 AT1 清零）——我们要的正是这个分岔。
    🚫 不做跨银行横向比价（各家票息/赎回条款不同，价格不可直接比）；
       只看**每只自己 vs 自己历史**（同 cm-iv「各自跟自己比，换源才致命」的家法）。
    """
    print("== 银行优先股/ETF（单名 CDS 替代）")
    out, missing = {}, []
    for tkr, label in BANK_PREFERRED.items():
        try:
            # 🔧 2026-09-03 修：原来一发不中就丢。实测 C-PN 连打三次＝0 行 / 32 行 / 0 行
            # ——**雅虎对同一个符号的回答本身在抖**，无重试等于把抖动当事实。
            # 照本文件指数行情那处的家法（退避 5/15 秒，第三次不再等）。
            px = None
            for _at, _wait in ((0, 5), (1, 15), (2, 0)):
                df = yf.download(tkr, period="5y", interval="1d", auto_adjust=False, progress=False)
                px = df["Close"]
                if hasattr(px, "columns"):
                    px = px.iloc[:, 0]
                px = px.dropna()
                if len(px) >= 100 or not _wait:
                    break
                print(f"  {tkr} 第 {_at + 1} 次只取到 {len(px)} 根，退避 {_wait}s 重试")
                time.sleep(_wait)
            if len(px) < 100:
                # 🚫 不许静默：说清是「取不到」还是「取到但太短」——两者的下一步不同
                print(f"  ⚠️ {tkr} 三次后仍只有 {len(px)} 根（<100，不足以算一年回撤），本轮跳过")
                missing.append(tkr); continue
            dd = (px / px.rolling(252, min_periods=60).max() - 1) * 100   # 距一年高点回撤 %
            out[tkr] = {
                "label": label,
                "latest": {"date": px.index[-1].strftime("%Y-%m-%d"),
                           "close": round(float(px.iloc[-1]), 2),
                           "dd_1y_pct": (None if pd.isna(dd.iloc[-1]) else round(float(dd.iloc[-1]), 2))},
                "dates": dates(px.index), "close": rnd(px, 2),
                "dd_1y_pct": [None if pd.isna(v) else round(float(v), 2) for v in dd],
            }
        except Exception as e:
            print(f"  {tkr} failed: {e}")
            missing.append(tkr)
        time.sleep(0.4)
    if not out:
        raise RuntimeError("银行优先股全部取空")
    if missing:
        print(f"  ⚠️ 未取到：{missing}")
    write_json("bank_credit.json", {
        "_what": "银行优先股与银行/优先股 ETF 收盘价 + 距一年高点回撤。单名 CDS 的免费替代"
                 "（CDS 付费墙）。dd_1y_pct 为负＝距一年高点的回撤幅度。",
        "_caveat": "🚫 不是 CDS：它是股权价不是违约保险年费；只看各自 vs 自己历史，"
                   "🚫 不做跨银行横向比价（票息/赎回条款不同）。",
        "missing": missing, "series": out,
    })


def build_cot_rates():
    """CFTC TFF 里**利率/国债期货**的机构站位 → data/cot_rates.json（2026-08-28 新建·传导链第一层「火药层」）。

    ━━ 为什么要它（与 cot_vix 分开的理由）━━
    传导链三层：**火药层（谁挤在船一边）→ 保费层（谁先买保险）→ 管道层（堵没堵）**。
    cot_vix 量的是"怕波动的人"的站位；本函数量的是**国债期货里堆了多大的杠杆赌注**——
    杠杆基金在超长债/10Y 的巨额净空 ≈ 基差交易（basis trade）规模，2020-03 国债市场断流
    的直接导火索。这是"债券先出事再传导到银行"那条路径上唯一能被公开数据看见的火药桶。
    2026-08-28 实测当期：超长债杠杆净空 -848,988 手、10Y -372,157、SOFR-3M -2,596,865。

    ━━ 市场名逐字实测，不许拼写猜（2026-08-28）━━
    首次扫描用 limit=60 **只扫到部分**（UST BOND / ULTRA 两个 / FED FUNDS / SOFR 三个），
    2Y/5Y/10Y NOTE 全被截在后面；提到 limit=80 才拿全 12 个。⇒ 全名一律取自实测返回，
    并在下方写死；日后若 CFTC 改名，本函数会因该市场零行而在 status 里露出来（不静默）。
    """
    print("== CFTC COT（利率/国债期货站位·传导链火药层）")
    # 🚨 **CFTC 在 2022-02 改过市场命名**，只认新名会静默丢掉 2022 年之前的全部历史
    #    （2026-08-28 首跑实测：只用新名 ⇒ 全史起点 2022-02-08、仅 238 期；而 VIX 那条有 20 年）。
    #    而本线的价值恰恰在 2008/2020 那些老案发现场 ⇒ 每个市场登记「新名 + 历史别名」，合并去重。
    #    别名同样逐字实测（对 2015-08-25 那期扫全名取得），不许拼写猜。
    markets = {                     # 键=短名（进 JSON）；值=该市场的全部历史称谓（新→旧）
        "ust_2y":       ["UST 2Y NOTE - CHICAGO BOARD OF TRADE",
                         "2-YEAR U.S. TREASURY NOTES - CHICAGO BOARD OF TRADE"],
        "ust_5y":       ["UST 5Y NOTE - CHICAGO BOARD OF TRADE",
                         "5-YEAR U.S. TREASURY NOTES - CHICAGO BOARD OF TRADE"],
        "ust_10y":      ["UST 10Y NOTE - CHICAGO BOARD OF TRADE",
                         "10-YEAR U.S. TREASURY NOTES - CHICAGO BOARD OF TRADE"],
        "ust_ultra10y": ["ULTRA UST 10Y - CHICAGO BOARD OF TRADE",
                         "ULTRA 10-YEAR U.S. TREASURY NOTES - CHICAGO BOARD OF TRADE"],
        "ust_bond":     ["UST BOND - CHICAGO BOARD OF TRADE",
                         "U.S. TREASURY BONDS - CHICAGO BOARD OF TRADE"],
        "ust_ultrabond": ["ULTRA UST BOND - CHICAGO BOARD OF TRADE",
                          "LONG-TERM U.S. TREASURY BONDS - CHICAGO BOARD OF TRADE"],
        "fed_funds":    ["FED FUNDS - CHICAGO BOARD OF TRADE"],
        "sofr_3m":      ["SOFR-3M - CHICAGO MERCANTILE EXCHANGE"],
        "sofr_1m":      ["SOFR-1M - CHICAGO MERCANTILE EXCHANGE"],
        # 🔑 欧洲美元期货＝SOFR 的前身（2023 前的短端杠杆都在这里），单列成腿而不是并进 sofr_3m：
        #    两者合约规格与规模量级不同，拼成一条序列会造出一个假跳变（同本仓「换源＝废表」的家法）。
        "eurodollar_3m": ["3-MONTH EURODOLLARS - CHICAGO MERCANTILE EXCHANGE"],
    }
    out, missing = {}, []
    for key, names in markets.items():
        try:
            _in = ",".join("'" + n.replace("'", "''") + "'" for n in names)
            r = requests.get(COT_TFF, params={
                "$where": f"market_and_exchange_names in({_in})",
                "$order": "report_date_as_yyyy_mm_dd ASC",
                "$limit": "5000",
                "$select": ("report_date_as_yyyy_mm_dd,lev_money_positions_long,lev_money_positions_short,"
                            "asset_mgr_positions_long,asset_mgr_positions_short,open_interest_all"),
            }, headers=UA, timeout=60)
            r.raise_for_status()
            series = []
            for row in r.json():
                try:
                    ll, ls = int(row["lev_money_positions_long"]), int(row["lev_money_positions_short"])
                    al, am = int(row["asset_mgr_positions_long"]), int(row["asset_mgr_positions_short"])
                    # 四条腿原样保留（同 cot_vix：净额看不出"空头平了还是多头加了"）
                    series.append({
                        "date": row["report_date_as_yyyy_mm_dd"][:10],
                        "lev_long": ll, "lev_short": ls, "lev_net": ll - ls,
                        "am_long": al, "am_short": am, "am_net": al - am,
                        "oi": int(row["open_interest_all"]),
                    })
                except (KeyError, ValueError):
                    pass
            if not series:
                missing.append(key); continue
            series.sort(key=lambda x: x["date"])
            # 同一市场跨改名合并后按日期排序去重（改名当周可能新旧名各一行）
            ded = {r["date"]: r for r in series}
            series = [ded[d] for d in sorted(ded)]
            out[key] = {"market": names[0], "aliases": names[1:], "latest": series[-1],
                        "series": series}
        except Exception as e:
            print(f"  {key} failed: {e}")
            missing.append(key)
        time.sleep(0.6)
    if not out:
        raise RuntimeError("CFTC 利率 COT 全部市场返回空")
    if missing:
        print(f"  ⚠️ 这些市场没取到（可能 CFTC 改名，查全名）：{missing}")
    write_json("cot_rates.json", {
        "_what": "CFTC TFF 利率/国债期货持仓（传导链火药层）。lev_*=杠杆基金(对冲基金)，am_*=资管。"
                 "杠杆基金在长端的巨额净空≈基差交易规模。周五 15:30 ET 发布周二数据。",
        "_caveat": "🚫 净空扩大≠必然出事：它是火药量不是引信。与保费层(MOVE/VIXHY/银行IV)、"
                   "管道层(贴现窗口/OAS)三层合读，单看一层无判断力。",
        "missing": missing,
        "markets": out,
    })


# ---------------- 波动率家族（2026-07-18 新建）：个股与板块的恐惧标价 ----------------
# 旗舰「恐惧的标价」量的是标普 500 的 1 年期保护，它天生看不见个体的极值——
# 2026-07-17 实测：大盘 30 天分位 75.4，而 5 只个股全在 86–96。分散化把个股抹平了。
# 本模块把同一把尺（756 交易日滚动百分位）换个输入，量个股与板块。
#
# ⚠️ 口径警告（写进 meta，前端引用时必须一起显示）：本家族全部是 **30 天**，
#    旗舰是 **1 年**。两者不是一把尺，绝不可混在同一张图或同一句话里比较。
# ⚠️ 绝对 IV 不可横向比（个股天然高于指数）；可横向比的只有百分位——
#    因为百分位是各自跟自己的历史比，自归一化。
VOL_FAMILY = {
    # ⚠️ label 要短：它是横条图的 y 轴刻度，长了会被截断（2026-07-18 实测
    # 「标普500成分股（加权几何平均）」在 1200px 下被切成「(加权几何平均)」）。
    # 口径细节写在图注里，不写在标签里。
    "VIXEQ": "标普500成分股",
    "VXSMH": "半导体 ETF",
    "VXAPL": "苹果", "VXAZN": "亚马逊", "VXGOG": "谷歌",
    "VXIBM": "IBM", "VXGS": "高盛",
}
# Cboe 个股 VIX 只有这 5 只，且全部 2011-01-07 同批上市后再没扩过——
# 它是「2010–2011 年期权流量王」的时间胶囊：NVDA 当年还小、TSLA 刚 IPO、
# META 2012 才上市。名单没变，市场变了。别期待 NVDA/TSLA 出现。
MIN_PCTILE_DAYS = 250   # 与 _roll_pctile 的 min_periods 一致；不足就报 null，绝不用短窗口冒充


def build_vol_family(vix_close: pd.Series):
    """个股/板块 30 天波动率的贵贱百分位 → data/vol_family.json。拉挂了留旧文件。"""
    print("== 波动率家族（个股与板块）")
    members, series_cache, patched, carried = [], {}, {}, []
    try:
        prev_members = {m.get("symbol"): m for m in
                        json.loads((DATA / "vol_family.json").read_text(encoding="utf-8")).get("members") or []}
    except Exception:
        prev_members = {}
    for sym, label in VOL_FAMILY.items():
        try:
            s = _cboe_close(sym).dropna()
            if s.empty:
                raise ValueError("Cboe 返回空序列")
        except Exception as e:
            # 🔴 2026-09-25（与 vol_indices 同一修法·§398）：原先 Cboe 一挂这只就从页面消失。沿用上一份并标 stale。
            old = prev_members.get(sym)
            if old:
                members.append(dict(old, stale=True, stale_reason=f"Cboe 拉取失败（{type(e).__name__}），沿用上一份已发布读数"))
                carried.append(sym)
                print(f"  ⚠️ {sym} Cboe 拉取失败，沿用上一份已发布读数（{old.get('date')}）: {str(e)[:60]}")
            else:
                print(f"  ⚠️ {sym} Cboe 拉取失败且无旧读数可沿用，本轮缺: {str(e)[:60]}")
            continue
        # 🔴 2026-09-25：Cboe 停更（09-22/09-23 实犯）时雅虎补更新的日期。雅虎 ^<代码> 与 Cboe 同日同值
        #    （09-25 七只逐只核对 7/7 一致），只补 Cboe 没有的新日期、16:30 前不收当日（同 _vol_yahoo_topup）。
        try:
            s, got = _vol_yahoo_topup(sym, s, ysym="^" + sym)
        except Exception as e:
            print(f"  ⚠️ {sym}: 雅虎补新日期出错（{type(e).__name__}），按 Cboe 原样用")
            got = []
        if got:
            patched[sym] = got
        series_cache[sym] = s
        enough = len(s) >= MIN_PCTILE_DAYS
        members.append({
            "symbol": sym, "label": label,
            "current": round(float(s.iloc[-1]), 2),
            "date": s.index[-1].strftime("%Y-%m-%d"),
            "start": s.index[0].strftime("%Y-%m-%d"),
            "days": len(s),
            # 历史不够就明说不够，不用短窗口凑一个看起来像样的数字
            "p3y": _pctile_now(s, 756) if enough else None,
            "p5y": _pctile_now(s, 1260) if enough else None,
            "pfull": _pctile_now(s) if enough else None,
            "pctile_available": enough,
            "pctile_note": None if enough else f"历史仅 {len(s)} 交易日，不足 {MIN_PCTILE_DAYS} 日，百分位不计算",
        })

    # 个股÷大盘 = 分散化的价格。VIXEQ 量个股平均、VIX 量指数，两者之比即隐含分散度。
    dispersion = None
    if "VIXEQ" in series_cache:
        eq = series_cache["VIXEQ"]
        idx = eq.index.intersection(vix_close.dropna().index)
        if len(idx) >= MIN_PCTILE_DAYS:
            ratio = (eq[idx] / vix_close[idx]).dropna()
            dispersion = {
                "what": "VIXEQ ÷ VIX：个股平均波动率相对大盘波动率。高=个股被单独定价得远比指数贵。",
                "current": round(float(ratio.iloc[-1]), 3),
                "date": ratio.index[-1].strftime("%Y-%m-%d"),
                "p3y": _pctile_now(ratio, 756),
                "pfull": _pctile_now(ratio),
                "median_full": round(float(ratio.median()), 3),
                "start": ratio.index[0].strftime("%Y-%m-%d"),
                "days": len(ratio),
            }

    write_json("vol_family.json", {
        "meta": {
            "name": "波动率家族（个股与板块）",
            "tenor": "30 天",
            "tenor_warning": "本家族全部为 30 天口径；旗舰「恐惧的标价」为 1 年口径。两者不是一把尺，不可混比。",
            "headline": "各标的 30 天隐含波动率在过去 3 年（756 交易日）的百分位，高=贵",
            "nature": "描述性温度计，非交易信号/非预测；仅为数据，非投资建议。",
            "comparability": "绝对 IV 不可横向比（个股天然高于指数）；可横向比的只有百分位——它是各自跟自己的历史比。",
            "roster_note": "Cboe 个股 VIX 仅 5 只，2011-01-07 同批上市后未再扩容，是当年期权流量王的名单，不含 NVDA/TSLA/META。",
            "yahoo_patched": patched, "carried_forward": carried,
        },
        "members": members,
        "dispersion": dispersion,
    })


# ================================================================================
# 2026-09-15 仓位与杠杆四件（Klay 09-15 令：免费的全接入直接上线）
#   起因：09-09 期引了「对冲基金杠杆回升」「半导体波动率 65→36」两条外部信号，回头查，
#   波动率那条站上本来就有（vol_family 的 VXSMH），杠杆那条本该看 NAAIM 而 NAAIM 08-01 起收费停更。
#   ⇒ 补四张免费公开表：CFTC 股指期货站位（周）、FINRA 融资余额（月）、OFR 对冲基金杠杆（季）、
#      Cboe 指数波动率家族（日）。全部只报位置与四条腿原值，不合成综合分，不做方向判断。
# ================================================================================
COT_EQUITY_MARKETS = {
    # TFF 的市场名逐字匹配。Consolidated＝大合约+E-mini 合并，2010→，是最长的连续序列。
    "spx": ("S&P 500 Consolidated - CHICAGO MERCANTILE EXCHANGE", "标普 500 期货（合并）"),
    "ndx": ("NASDAQ-100 Consolidated - CHICAGO MERCANTILE EXCHANGE", "纳斯达克 100 期货（合并）"),
    # 2026-09-23 加罗素 2000（Klay 令「视频里提到的数据能接的全接」）：TFF 没有 Consolidated 名，只有 E-mini，
    # 逐字匹配 CFTC 现名；本机镜像 fetch_futures.do_cot_equity 按 markets 键自动多出一个市场，不用改。
    "rty": ("RUSSELL E-MINI - CHICAGO MERCANTILE EXCHANGE", "罗素 2000 期货（E-mini）"),
}


def _cot_tff_market(name: str) -> list:
    r = requests.get(COT_TFF, params={
        "$where": f"market_and_exchange_names='{name}'",
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": "5000",
        "$select": ("report_date_as_yyyy_mm_dd,lev_money_positions_long,lev_money_positions_short,"
                    "asset_mgr_positions_long,asset_mgr_positions_short,open_interest_all"),
    }, headers=UA, timeout=60)
    r.raise_for_status()
    out = []
    for row in r.json():
        try:
            ll, ls = int(row["lev_money_positions_long"]), int(row["lev_money_positions_short"])
            al, am = int(row["asset_mgr_positions_long"]), int(row["asset_mgr_positions_short"])
            out.append({"date": row["report_date_as_yyyy_mm_dd"][:10],
                        "lev_long": ll, "lev_short": ls, "lev_net": ll - ls,
                        "am_long": al, "am_short": am, "am_net": al - am,
                        "oi": int(row["open_interest_all"])})
        except (KeyError, ValueError):
            pass
    out.sort(key=lambda x: x["date"])
    return out


def build_cot_equity():
    """CFTC TFF 里**股指期货**的机构站位（标普 500 / 纳斯达克 100，合并合约，周频 2010→）→ data/cot_equity.json。
    ⚠️ 口径警告（必须与读数同时出现）：杠杆基金在股指期货上长期净空，大头是基差交易与对冲，
       不是「对冲基金看空股市」。本表只报四条腿原值与净额在自己历史里的位置，不解读方向。"""
    print("== CFTC COT（股指期货站位）")
    markets = {}
    for key, (name, label) in COT_EQUITY_MARKETS.items():
        series = _cot_tff_market(name)
        if not series:
            raise RuntimeError(f"CFTC TFF 返回空：{name}")
        net = pd.Series([x["lev_net"] for x in series], index=pd.to_datetime([x["date"] for x in series]))
        markets[key] = {
            "market": name, "label": label,
            "latest": series[-1],
            "lev_net_pctile_full": round(float((net <= net.iloc[-1]).mean() * 100), 1),
            "lev_net_pctile_3y": round(float((net.iloc[-156:] <= net.iloc[-1]).mean() * 100), 1),
            "weeks_net_short_52": sum(1 for x in series[-52:] if x["lev_net"] < 0),
            "series": series,
        }
    write_json("cot_equity.json", {
        "_what": "CFTC 交易者分类报告（TFF）：杠杆基金与资管机构在标普 500、纳斯达克 100 期货上的多空持仓，周二数据周五 15:30 ET 发布。",
        "_caveat": "杠杆基金在股指期货上长期净空，大头是基差交易与对冲，不等于看空；只报位置，不报方向。",
        "markets": markets,
    })


FINRA_MARGIN_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
FINRA_MARGIN_PAGE = "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics"


def build_margin_debt():
    """FINRA 客户保证金账户余额（月频，1997→，滞后约三周）→ data/margin_debt.json。
    三列原值：融资余额（借出去的钱）、现金账户闲置资金、保证金账户闲置资金；派生「净融资」＝融资余额−两项闲置资金。
    xlsx 路径带 2021-03 是 FINRA 的历史遗留，文件本身每月更新（2026-09-15 实测含 2026-08）。"""
    print("== FINRA 融资余额")
    # ⚠️ FINRA 的 WAF 对全站通用的 Chrome UA 回 403（2026-09-15 实测），裸 "Mozilla/5.0" 或 curl 都放行。别换回 UA。
    r = requests.get(FINRA_MARGIN_XLSX, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    r.raise_for_status()
    df = pd.read_excel(pd.io.common.BytesIO(r.content), header=None)
    rows = []
    for _, row in df.iterrows():
        ym = str(row[0]).strip()
        if not re.match(r"^\d{4}-\d{2}$", ym):
            continue
        def _n(v):
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None
        rows.append({"month": ym, "debit": _n(row[1]), "free_cash": _n(row[2]), "free_margin": _n(row[3])})
    if not rows:
        raise RuntimeError("FINRA margin xlsx 解析为空")
    rows.sort(key=lambda x: x["month"])
    for x in rows:
        fc, fm = x["free_cash"] or 0, x["free_margin"] or 0
        x["net"] = (x["debit"] - fc - fm) if x["debit"] is not None else None
    debit = pd.Series([x["debit"] for x in rows], index=[x["month"] for x in rows], dtype="float")
    cur = rows[-1]
    yoy = None
    prev_y = next((x for x in rows if x["month"] == f"{int(cur['month'][:4]) - 1}{cur['month'][4:]}"), None)
    if prev_y and prev_y["debit"]:
        yoy = round((cur["debit"] / prev_y["debit"] - 1) * 100, 1)
    mom = round((cur["debit"] / rows[-2]["debit"] - 1) * 100, 1) if len(rows) > 1 and rows[-2]["debit"] else None
    write_json("margin_debt.json", {
        "_what": "FINRA 会员经纪商客户账户余额，单位百万美元，月频，参考月次月第三周发布。融资余额＝客户借券商的钱买股票。",
        "_caveat": "融资余额随市值一起涨，绝对值创新高不等于杠杆创新高；看同比、看它与闲置资金的差、看它在自己历史里的位置。",
        "latest": {**cur, "yoy_pct": yoy, "mom_pct": mom,
                   "debit_pctile_full": round(float((debit <= debit.iloc[-1]).mean() * 100), 1),
                   "debit_record_high": bool(debit.iloc[-1] >= debit.max())},
        "series": rows,
        "source": FINRA_MARGIN_PAGE,
    })


OFR_HFM = "https://data.financialresearch.gov/hf/v1"
OFR_SERIES = {
    # 全部来自 SEC Form PF 汇总（合格对冲基金），季频，2013→，滞后约两个季度。
    "equity_leverage": ("FPF-STRATEGY_EQUITY_LEVERAGERATIO_GAVWMEAN", "股票策略对冲基金杠杆率（总资产/净资产，按总资产加权）"),
    "all_top10_leverage": ("FPF-ALLQHF_GAVN10_LEVERAGERATIO_AVERAGE", "最大十家合格对冲基金平均杠杆率"),
    "pb_borrowing": ("FPF-BORROW_PRIMEBROKER_SUM", "向主经纪商借款总额（美元）"),
    "gav": ("FPF-ALLQHF_GAV_SUM", "合格对冲基金总资产（美元）"),
    "nav": ("FPF-ALLQHF_NAV_SUM", "合格对冲基金净资产（美元）"),
}


def build_ofr_hedge_funds():
    """OFR 对冲基金监测（Form PF 汇总，季频 2013→）→ data/ofr_hedge_funds.json。免费公开 API，免鉴权。"""
    print("== OFR 对冲基金杠杆")
    out = {}
    for key, (mn, label) in OFR_SERIES.items():
        r = requests.get(f"{OFR_HFM}/series/timeseries", params={"mnemonic": mn}, headers=UA, timeout=60)
        r.raise_for_status()
        pts = [[d[:10], v] for d, v in r.json() if v is not None]
        if not pts:
            raise RuntimeError(f"OFR 返回空：{mn}")
        vals = pd.Series([v for _, v in pts])
        out[key] = {"mnemonic": mn, "label": label, "dates": [d for d, _ in pts], "values": [v for _, v in pts],
                    "latest": {"date": pts[-1][0], "value": pts[-1][1]},
                    "pctile_full": round(float((vals <= vals.iloc[-1]).mean() * 100), 1)}
    if "gav" in out and "nav" in out:
        g, n = out["gav"], out["nav"]
        common = [d for d in g["dates"] if d in set(n["dates"])]
        gi, ni = dict(zip(g["dates"], g["values"])), dict(zip(n["dates"], n["values"]))
        ratio = [[d, round(gi[d] / ni[d], 3)] for d in common if ni[d]]
        out["all_leverage_gav_nav"] = {"label": "全部合格对冲基金总资产/净资产", "dates": [d for d, _ in ratio],
                                       "values": [v for _, v in ratio], "latest": {"date": ratio[-1][0], "value": ratio[-1][1]}}
    write_json("ofr_hedge_funds.json", {
        "_what": "美国财政部金融研究办公室对冲基金监测：SEC Form PF 汇总，只含合格对冲基金，季频，滞后约两个季度。",
        "_caveat": "杠杆率＝总资产/净资产，含衍生品名义敞口的处理按 Form PF 口径；不同策略不可横比，看各自历史位置。",
        "series": out,
        "source": "https://www.financialresearch.gov/hedge-fund-monitor/",
    })


VOL_INDICES = {
    # Cboe 官方指数波动率家族（30 天口径），全部免费 CSV；VXSMH 已在 vol_family（板块）里，此处只放指数与利率。
    "VIX": "标普 500", "VXN": "纳斯达克 100", "RVX": "罗素 2000", "VXD": "道琼斯", "VXTLT": "20 年以上国债 ETF",
    "VVIX": "VIX 的波动率",
}


# 2026-09-24（Klay 定：指数波动率家族照温度计接雅虎补较新日期）。
# 只收四只：09-24 实测 ^VIX/^VXN/^VXD/^VVIX 与 Cboe 近 20 个交易日逐日一致（差 0）；
#   ^RVX 雅虎没有；^VXTLT 雅虎只给 1 行、对不了账 ⇒ 不收（家规：只用对过账的第二源）。
# ⚠️ 雅虎也缺 09-22（它的指数日线整日缺）⇒ 只能补「比 Cboe 末日更新」的日子，中间缺口照样留空，绝不硬填。
VOL_INDEX_YAHOO = {"VIX": "^VIX", "VXN": "^VXN", "VXD": "^VXD", "VVIX": "^VVIX"}


def _vol_yahoo_topup(sym: str, s: pd.Series, ysym: str = None):
    """Cboe 末日落后于雅虎 ⇒ 只把雅虎更新的日期接到末尾。返回 (序列, 补上的日期列表)。

    🔴 今天这一行不收，除非已过 16:30 ET：VIX 在美东凌晨 3 点就开盘（全球交易时段），
       雅虎凌晨就给出「今天」一行实时值（09-24 06:32 实测 ^VIX 当日 16.28），它不是收盘价。
       云端 00:05 那班若照收，就会把盘前实时值当收盘入账。
    与温度计的 _patch_stale_with_yahoo 分开写：那个把补过的日期记进全局 _YAHOO_PATCHED、写进 leaps_gauge 的 meta，
    复用会把本家族的日期混进温度计的来源记录。"""
    ysym = ysym or VOL_INDEX_YAHOO.get(sym)
    if not ysym or s.empty:
        return s, []
    try:
        y = _yahoo_close(ysym)
    except Exception as e:
        print(f"  {sym}: 雅虎查新鲜度失败（{type(e).__name__}），按 Cboe 原样用")
        return s, []
    if y.empty:   # 🔴 09-25 补：yfinance 限流常返回空表而不抛错 ⇒ 空序列的整数索引跟日期一比就 TypeError，整节停更
        return s, []
    now = pd.Timestamp.now(tz="America/New_York")
    today = pd.Timestamp(now.date())
    cutoff = today if now.hour * 60 + now.minute >= 16 * 60 + 30 else today - pd.Timedelta(days=1)
    newer = y[(y.index > s.index.max()) & (y.index <= cutoff)].round(2)
    if newer.empty:
        return s, []
    got = [d.strftime("%Y-%m-%d") for d in newer.index]
    print(f"  ↪ {sym}: Cboe 末日 {s.index.max().date()} 落后，雅虎补 {got} = {list(newer.values)}")
    return pd.concat([s, newer]).sort_index(), got


def _prev_vol_indices() -> dict:
    """上一份已发布的 vol_indices.json（Cboe 挂时沿用用）。读不到 ⇒ {}。"""
    try:
        return json.loads((DATA / "vol_indices.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_vol_indices():
    """Cboe 指数波动率家族（VIX/VXN/RVX/VXD/VXTLT/VVIX，日频）→ data/vol_indices.json。只报现值与三年/全史分位。"""
    print("== 指数波动率家族")
    members, closes, patched, carried = [], {}, {}, []
    prev = _prev_vol_indices()
    prev_members = {m.get("symbol"): m for m in prev.get("members") or []}
    for sym, label in VOL_INDICES.items():
        try:
            s = _cboe_close(sym).dropna()
            if s.empty:
                raise ValueError("Cboe 返回空序列")
        except Exception as e:
            # 🔴 2026-09-25 改：原先直接 continue ⇒ Cboe 一挂，这只（连同它参与的比值）就从页面上整只消失，
            #    而 Cboe「停更」时反倒有雅虎补新日期——同一上游，挂比停更的待遇还差。
            #    现在沿用上一份已发布的读数原样不动，标 stale（日期仍是旧日期，不冒充今天）。
            old = prev_members.get(sym)
            if old:
                members.append(dict(old, stale=True,
                                    stale_reason=f"Cboe 拉取失败（{type(e).__name__}），沿用上一份已发布读数"))
                carried.append(sym)
                print(f"  ⚠️ {sym} Cboe 拉取失败，沿用上一份已发布读数（{old.get('date')}）: {str(e)[:60]}")
            else:
                print(f"  ⚠️ {sym} Cboe 拉取失败且无旧读数可沿用，本轮缺: {str(e)[:60]}")
            continue
        try:
            s, got = _vol_yahoo_topup(sym, s)
        except Exception as e:   # 第二源只是补新日期：它出任何错都按 Cboe 原样用，不许连累整节
            print(f"  ⚠️ {sym}: 雅虎补新日期出错（{type(e).__name__}: {str(e)[:60]}），按 Cboe 原样用")
            got = []
        if got:
            patched[sym] = got
        closes[sym] = s
        enough = len(s) >= MIN_PCTILE_DAYS
        tail = s.iloc[-260:]
        members.append({
            "symbol": sym, "label": label,
            "current": round(float(s.iloc[-1]), 2), "prev": round(float(s.iloc[-2]), 2),
            "date": s.index[-1].strftime("%Y-%m-%d"), "start": s.index[0].strftime("%Y-%m-%d"), "days": len(s),
            "p3y": _pctile_now(s, 756) if enough else None, "pfull": _pctile_now(s) if enough else None,
            "hi_1y": round(float(tail.max()), 2), "lo_1y": round(float(tail.min()), 2),
            "dates_1y": dates(tail.index), "values_1y": rnd(tail, 2),
            "src": "cboe+yahoo_patch" if sym in patched else "cboe",
        })
    if not members:
        raise RuntimeError("指数波动率家族一条都没拉到")
    ratios = {}
    if "VXN" in closes and "VIX" in closes:
        idx = closes["VXN"].index.intersection(closes["VIX"].index)
        r = (closes["VXN"][idx] / closes["VIX"][idx]).dropna()
        ratios["vxn_vix"] = {"what": "VXN ÷ VIX：纳指相对标普的波动溢价", "current": round(float(r.iloc[-1]), 3),
                             "p3y": _pctile_now(r, 756), "pfull": _pctile_now(r), "date": r.index[-1].strftime("%Y-%m-%d")}
    if "RVX" in closes and "VIX" in closes:
        idx = closes["RVX"].index.intersection(closes["VIX"].index)
        r = (closes["RVX"][idx] / closes["VIX"][idx]).dropna()
        ratios["rvx_vix"] = {"what": "RVX ÷ VIX：小盘相对大盘的波动溢价", "current": round(float(r.iloc[-1]), 3),
                             "p3y": _pctile_now(r, 756), "pfull": _pctile_now(r), "date": r.index[-1].strftime("%Y-%m-%d")}
    for k, old in (prev.get("ratios") or {}).items():     # 比值同理：算不出（一腿沿用）就沿用旧值并标 stale
        if k not in ratios:
            ratios[k] = dict(old, stale=True)
    write_json("vol_indices.json", {
        "meta": {"name": "指数波动率家族", "tenor": "30 天",
                 "headline": "各指数 30 天隐含波动率在过去 3 年（756 交易日）的百分位，高=贵",
                 "nature": "描述性温度计，非交易信号/非预测；仅为数据，非投资建议。",
                 "note": "VVIX 量的是 VIX 自己的波动率（VIX 期权隐含），单位不同，只看分位。",
                 "yahoo_patched": patched, "carried_forward": carried},
        "members": members, "ratios": ratios,
    })


# ---------------- 做空成交结构（2026-07-18 新建）：FINRA 逐日 RegSHO ----------------
# 🚨 口径警告（必须与读数同时出现，勿弱化）：做空占比高 ≠ 有人看空。
#    做市商对冲、ETF 申赎套利、可转债对冲全部计入做空量。它量的是**卖方向成交的
#    结构占比**，不是情绪、不是持仓、更不是"谁在赌跌"。CFTC COT 能说"谁在押注恐惧"
#    是因为它按交易者类别拆分了；FINRA 这份没有拆，所以那句话这里说不得。
# 因此本模块只做一件事：把当日占比放进它自己的历史里给一个位置（百分位）。
#    ——同「恐惧的标价」的逻辑：报位置，不报判断。
FINRA_SHVOL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{}.txt"
SHORT_FLOW_TICKERS = ["SPY", "QQQ", "AAPL", "AMZN", "GOOGL", "META", "MSFT",
                      "NVDA", "TSLA", "AVGO", "MU", "SNDK", "SPCX",
                      # 2026-09-23 加 7 只 ETF（Klay 令）：板块/风格做空占比，与本机 cm-iv 板块腿 XLV/XLK/XLC 同批。
                      # 新票只从加入日起有数（FINRA 日文件永不重取），百分位要等满 250 日；短仓表同批照加。
                      "IWM", "TLT", "XLF", "XLK", "XLV", "XLC", "SMH"]
SHORT_FLOW_WINDOW = 756     # 3 年，与站上其余百分位同口径
SHORT_FLOW_MIN = 250        # 不足则不给百分位，绝不用短窗口冒充


def _finra_day(day: str) -> dict:
    """某交易日的 {ticker: 做空占比%}。非交易日/未发布返回 {}。"""
    try:
        r = requests.get(FINRA_SHVOL.format(day), headers=UA, timeout=30)
        if r.status_code != 200 or len(r.text) < 1000:
            return {}
    except Exception:
        return {}
    out, want = {}, set(SHORT_FLOW_TICKERS)
    for line in r.text.splitlines()[1:]:
        p = line.split("|")
        if len(p) >= 5 and p[1] in want:
            try:
                sv, tv = float(p[2]), float(p[4])
            except ValueError:
                continue
            if tv > 0:
                out[p[1]] = round(sv / tv * 100, 2)
    return out


def build_short_flow(max_backfill: int = 3):
    """FINRA 逐日做空成交占比 → data/short_flow.json。

    **增量追加**：读回已有文件，只拉缺的交易日。日常只缺 1 天（≈2 秒）；
    首次回填用 max_backfill 调大（1.88 秒/文件，756 天约 24 分钟）。
    已写入的日期永不重取、永不覆盖——FINRA 不修订历史，且这也让本表天然
    具备前向台账属性（与 verify_ledger 的诉求同向）。
    """
    print("== 做空成交结构（FINRA RegSHO）")
    path = DATA / "short_flow.json"          # DATA 是模块里既有的 pathlib 路径
    old = {}
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8")).get("by_date", {})
        except Exception:
            old = {}

    today = datetime.now(ET).date()  # 2026-08-06 UTC 案同族修：回退起点用美东日历
    added, tried = 0, 0
    d = today
    while tried < max_backfill * 2 and added < max_backfill:
        key = d.strftime("%Y%m%d")
        if d.weekday() < 5 and key not in old:
            row = _finra_day(key)
            tried += 1
            if row:
                old[key] = row
                added += 1
        d -= timedelta(days=1)
        if (today - d).days > max_backfill * 3 + 10:
            break
    print(f"   新增 {added} 个交易日，累计 {len(old)}")

    dates = sorted(old)
    series, current = {}, {}
    for tk in SHORT_FLOW_TICKERS:
        vals = [old[dt].get(tk) for dt in dates]
        series[tk] = vals

        # 🚨 代码复用检测：交易所会把退市公司的代码分配给新公司，两段历史必须切断。
        # 实测 SPCX——旧 SPCX（SPAC 主题 ETF）到 2026-04-06，断 47 个交易日后
        # 2026-06-12 变成 SpaceX。不切的话就是拿 SpaceX 的读数去跟一只 ETF 的历史
        # 排百分位，数字看着正常、含义完全是假的。
        # 判据=最后一个 >10 交易日的断口；实测 12 只正常票断口均为 0，零误伤。
        idx = [i for i, v in enumerate(vals) if v is not None]
        cut = 0
        for a, b in zip(idx, idx[1:]):
            if b - a - 1 > 10:
                cut = b
        if cut:
            print(f"   ⚠️ {tk} 在 {dates[cut]} 前有 {cut} 天疑似属于同代码的前任标的，已切断")
        seen = [vals[i] for i in idx if i >= cut]
        if not seen:
            continue
        cur = seen[-1]
        win = seen[-SHORT_FLOW_WINDOW:]
        enough = len(win) >= SHORT_FLOW_MIN
        current[tk] = {
            "ratio": cur,
            "pctile": round(sum(1 for v in win if v <= cur) / len(win) * 100, 1) if enough else None,
            "days": len(win),
            "note": None if enough else f"历史仅 {len(win)} 交易日，不足 {SHORT_FLOW_MIN} 日，百分位不计算",
        }

    write_json("short_flow.json", {
        "meta": {
            "name": "做空成交结构",
            "source": "FINRA RegSHO 每日合并文件（官方）",
            "headline": "当日做空成交占总成交的比例，及其在过去 3 年（756 交易日）的百分位",
            "caveat": "做空占比高 ≠ 有人看空：做市商对冲、ETF 申赎套利、可转债对冲均计入做空量。"
                      "本读数量的是卖方向成交的结构占比，不是情绪、不是持仓、不是方向判断。",
            "nature": "描述性数据，非交易信号/非预测；仅为数据，非投资建议。",
            "window": f"{SHORT_FLOW_WINDOW} 交易日；不足 {SHORT_FLOW_MIN} 日不给百分位",
            "asof": dates[-1] if dates else None,
        },
        "dates": dates,
        "by_date": old,          # 原样留存，只追加不覆盖
        "series": series,
        "current": current,
    })


# ---------------- 做空持仓（2026-07-18 新建）：FINRA 双月合并短仓 ----------------
# 与上面的 short_flow 是**两样东西**，勿混：
#   short_flow  = 流量。每日做空成交占比。当天的卖单结构。
#   short_int   = 存量。此刻有多少股仍在被做空。每月两次，滞后约 2 周。
#
# 🚨 滞后是本表的第一属性，必须与读数同时出现：结算日之后约 8 个交易日才发布。
#    实测 2026-07-18 当天，最新可得的是 2026-06-30 结算期 = 18 天前的世界。
#    因此它**永远不能当"今天"的读数用**，只能当缓慢变动的背景变量。
#
# 🚨 2026-07-18 实测过、写下来免得后人重做：把"每日做空占比"与"持仓变化"配对，
#    水平对变化 Spearman = -0.006（零）；变化对变化 = +0.168，且分票看只有
#    AAPL/GOOGL/META/MSFT 四只 p<0.05，Bonferroni 校正后一只不剩；ETF（SPY/QQQ）
#    精确为零——与"ETF 申赎套利主导做空量"的理论预期一致。
#    ∴ **"流量与存量背离"不足以当头条**，本模块只发布存量自身的位置，不发布背离判读。
FINRA_SI_API = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"
SHORT_INT_TICKERS = SHORT_FLOW_TICKERS      # 与流量表同一批，便于并排
SI_WINDOW = 48          # 48 期 ≈ 2 年（双月）
SI_MIN = 24             # 不足 24 期不给百分位，绝不用短窗口冒充
SI_NAME_SIM = 0.45      # 名称归一化后相似度低于此 → 判为换了标的


def _si_norm_name(s: str) -> str:
    """归一化发行人名称。只用相似度、不用字面相等——2026-07-18 实测：字面比较会把
    'Apple Inc. - Common Stock' → 'Apple Inc. Common Stock'（去了个连字符）当成换公司，
    也会把 SPY 的品牌改名（SPDR S&P 500 ETF Trust → State Street SPDR S&P 500）当成
    换公司，一刀砍掉 194 期真历史。"""
    s = s.lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(common stock|ordinary shares?|class [a-c]|inc|corp|corporation|"
               r"ltd|limited|co|company|trust|series \d+|the)\b", " ", s)
    return " ".join(s.split())


def _si_fetch(sym: str) -> tuple[list, tuple | None]:
    """某代码的全部结算期（最早在前）。返回 (记录, 切断信息)。"""
    import csv as _csv, io as _io, difflib as _difflib
    r = requests.post(FINRA_SI_API,
                      json={"limit": 500, "compareFilters": [
                          {"fieldName": "symbolCode", "fieldValue": sym, "compareType": "EQUAL"}]},
                      headers={"Accept": "text/plain"}, timeout=60)
    r.raise_for_status()
    rows = sorted(_csv.DictReader(_io.StringIO(r.text)), key=lambda x: x["settlementDate"])
    if not rows:
        return [], None
    # 🚨 代码复用切断（与 short_flow 同一个坑，判据不同：那边看断口，这边看发行人名称）
    last = _si_norm_name(rows[-1]["issueName"])
    for i in range(len(rows) - 1, 0, -1):
        sim = _difflib.SequenceMatcher(None, _si_norm_name(rows[i - 1]["issueName"]), last).ratio()
        if sim < SI_NAME_SIM:
            return rows[i:], (rows[i]["settlementDate"], rows[i - 1]["issueName"].strip(),
                              rows[i]["issueName"].strip(), round(sim, 2), i)
    return rows, None


def build_short_interest():
    """FINRA 双月合并短仓 → data/short_interest.json。

    **只追加、不覆盖**：已发布过的结算期永不改写。FINRA 会修订历史（实测 NVDA 205 期
    里 11 期带 revisionFlag），修订**另记一行**到 revisions[]，旧值原样留着——这正是
    data/README.md「修订政策」承诺的做法：官方改了口径，我们不悄悄跟着改。
    """
    print("== 做空持仓（FINRA 双月短仓）")
    path = DATA / "short_interest.json"
    old_by, old_rev = {}, []
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            old_by, old_rev = prev.get("by_settle", {}), prev.get("revisions", [])
        except Exception:
            old_by, old_rev = {}, []

    today = datetime.now(ET).strftime("%Y-%m-%d")  # 2026-08-06 UTC 案同族修：记录日=美东
    cuts, added, revised = {}, 0, 0
    for tk in SHORT_INT_TICKERS:
        try:
            rows, cut = _si_fetch(tk)
        except Exception as e:
            print(f"   {tk} 拉取失败，跳过（旧值保留）: {e}")
            continue
        if cut:
            cuts[tk] = {"from": cut[0], "was": cut[1], "now": cut[2], "similarity": cut[3],
                        "dropped_periods": cut[4]}
            print(f"   ⚠️ {tk} 代码复用切断于 {cut[0]}：{cut[1][:28]} → {cut[2][:28]}"
                  f"（相似度 {cut[3]}，弃 {cut[4]} 期）")
        for r in rows:
            d = r["settlementDate"]
            try:
                si = int(r["currentShortPositionQuantity"])
            except (ValueError, TypeError):
                continue
            slot = old_by.setdefault(d, {})
            rec = {"si": si,
                   "dtc": _f(r.get("daysToCoverQuantity")),
                   "adv": _i(r.get("averageDailyVolumeQuantity")),
                   "chg": _f(r.get("changePercent"))}
            if tk not in slot:
                slot[tk] = rec
                added += 1
            elif slot[tk].get("si") != si:
                # 官方修订：旧行不动，另注一行（承诺与机制冲突时改机制，不改承诺）
                old_rev.append({"settle": d, "ticker": tk, "field": "si",
                                "first_published": slot[tk]["si"], "now": si, "detected": today})
                revised += 1
    print(f"   新增 {added} 条，检出官方修订 {revised} 条，累计 {len(old_by)} 个结算期")

    settles = sorted(old_by)
    series, dtc_s, chg_s, current = {}, {}, {}, {}
    for tk in SHORT_INT_TICKERS:
        cut_from = cuts.get(tk, {}).get("from")
        vals, dtcs, chgs = [], [], []
        for d in settles:
            rec = old_by.get(d, {}).get(tk)
            drop = bool(cut_from) and d < cut_from      # 切断前的历史不进序列
            vals.append(None if (rec is None or drop) else rec["si"])
            dtcs.append(None if (rec is None or drop) else rec["dtc"])
            chgs.append(None if (rec is None or drop) else rec["chg"])
        series[tk], dtc_s[tk], chg_s[tk] = vals, dtcs, chgs

        seen = [v for v in vals if v is not None]
        seen_d = [v for v in dtcs if v is not None]
        if not seen:
            continue
        win, win_d = seen[-SI_WINDOW:], seen_d[-SI_WINDOW:]
        enough = len(win) >= SI_MIN
        cur_i = next(i for i in range(len(vals) - 1, -1, -1) if vals[i] is not None)
        current[tk] = {
            "settle": settles[cur_i],
            "si": vals[cur_i],
            "dtc": dtcs[cur_i],
            "chg": chgs[cur_i],
            "pctile_si": round(sum(1 for v in win if v <= seen[-1]) / len(win) * 100, 1) if enough else None,
            "pctile_dtc": (round(sum(1 for v in win_d if v <= seen_d[-1]) / len(win_d) * 100, 1)
                           if enough and win_d else None),
            "periods": len(win),
            "note": None if enough else f"历史仅 {len(win)} 期，不足 {SI_MIN} 期，百分位不计算",
        }

    write_json("short_interest.json", {
        "meta": {
            "name": "做空持仓",
            "source": "FINRA 合并短仓（consolidatedShortInterest，官方 API，无需认证）",
            "headline": "每月两次结算的做空持仓股数与补仓天数，及其在自身近 48 期中的位置",
            "caveat": "① 与「做空成交结构」不是一回事：那是当日流量，这是存量。"
                      "② 做空持仓高 ≠ 有人看空：做市商对冲、ETF 申赎套利、可转债与并购套利均留下空头持仓。"
                      "③ 本表只报位置，不报方向、不作预测。",
            "lag": "结算日后约 8 个交易日发布；实测 2026-07-18 当天最新可得为 2026-06-30 期，"
                   "滞后 18 天。**它不是今天的读数**，只能当缓慢变动的背景变量。",
            "revision_policy": "已发布的结算期永不覆盖；官方修订另记于 revisions[]，旧值原样保留。",
            "nature": "描述性数据，非交易信号/非预测；仅为数据，非投资建议。",
            "window": f"{SI_WINDOW} 期（≈2 年）；不足 {SI_MIN} 期不给百分位",
            "asof": settles[-1] if settles else None,
        },
        "dates": settles,
        "by_settle": old_by,        # 原样留存，只追加不覆盖
        "series": series,
        "days_to_cover": dtc_s,
        "change_pct": chg_s,
        "current": current,
        "cuts": cuts,
        "revisions": old_rev,
    })


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def build_index_val():
    """SPY/QQQ ETF 口径的估值快照（yfinance；指数级 Forward PE 无免费长史，只取当前值）。"""
    print("== 指数估值快照")
    out = {}
    for etf in ("SPY", "QQQ"):
        try:
            info = yf.Ticker(etf).info
            # 2026-09-24 加 as_of：这份市盈率对应的那次报价的美东日期（原来不带日期，本机无法按日存档）
            _t = info.get("regularMarketTime")
            as_of = (pd.Timestamp(_t, unit="s", tz="UTC").tz_convert("America/New_York").strftime("%Y-%m-%d")
                     if isinstance(_t, (int, float)) else None)
            out[etf] = {"trailing_pe": info.get("trailingPE"), "forward_pe": info.get("forwardPE"), "as_of": as_of}
        except Exception as e:
            print(f"  {etf} info: {e}")
    write_json("index_val.json", out)


# ------------------------------------------------- 指数扩容章节（SPY/QQQ 专用）

def build_index_extras(prefix: str, close: pd.Series):
    """年度回报分桶 / 持有期胜率 / 5-10-20 年滚动矩阵 / 牛熊交替 / 最坏的那些日子。"""
    print(f"== extras {prefix}")
    close = close.dropna()

    # 年度回报分桶（每桶列出年份，前端 tooltip 展示）
    annual = close.resample("YE").last().pct_change().dropna() * 100
    buckets = [(-100, -30), (-30, -20), (-20, -10), (-10, 0),
               (0, 10), (10, 20), (20, 30), (30, 200)]
    dist = []
    for lo, hi in buckets:
        yrs = sorted(int(d.year) for d, v in annual.items() if lo <= v < hi)
        label = (f"<{-30}%" if lo == -100 else f">{30}%" if hi == 200 else f"{lo}~{hi}%")
        dist.append({"label": label, "count": len(yrs), "years": yrs})
    write_json(f"{prefix}_distribution.json", {"buckets": dist, "years_total": len(annual)})

    # 买入之后持有多久：持有 1/3/5/10/20 年（月频滚动）的胜率与年化分位
    m = close.resample("ME").last().dropna()
    hp = []
    for y in (1, 3, 5, 10, 20):
        r = ((m / m.shift(12 * y)) ** (1 / y) - 1).dropna() * 100
        if len(r) < 24:
            continue
        hp.append({
            "years": y,
            "win": round(float((r > 0).mean() * 100), 1),
            "median": round(float(r.median()), 1),
            "worst": round(float(r.min()), 1),
            "best": round(float(r.max()), 1),
            "samples": int(len(r)),
        })
    write_json(f"{prefix}_holding.json", {"rows": hp})

    # 5 / 10 / 20 年滚动矩阵（月频）
    out = {"dates": dates(m.index)}
    for y in (5, 10, 20):
        r = ((m / m.shift(12 * y)) ** (1 / y) - 1) * 100
        out[f"cagr{y}"] = rnd(r, 2)
    write_json(f"{prefix}_rollmatrix.json", out)

    # 牛熊周期：标准 zigzag，跌 20% 确认熊、涨 25% 确认牛（对称：跌20%需涨25%回本）
    pivots = []  # 相邻牛熊阶段之间的极值点 (日期, 价格)
    hi, hi_d = float(close.iloc[0]), close.index[0]
    lo, lo_d = float(close.iloc[0]), close.index[0]
    direction = 0  # 1 牛 / -1 熊 / 0 未定
    for dt, v in close.items():
        v = float(v)
        if direction >= 0 and v > hi:
            hi, hi_d = v, dt
        if direction <= 0 and v < lo:
            lo, lo_d = v, dt
        if direction >= 0 and v < hi * 0.8:
            pivots.append((hi_d, hi))
            direction, lo, lo_d = -1, v, dt
        elif direction <= 0 and v > lo * 1.25:
            pivots.append((lo_d, lo))
            direction, hi, hi_d = 1, v, dt
    cycles = []
    prev_d, prev_p = close.index[0], float(close.iloc[0])
    for d, px in pivots:
        if d <= prev_d:
            continue
        ret = (px / prev_p - 1) * 100
        cycles.append({"kind": "bull" if ret > 0 else "bear",
                       "start": prev_d.strftime("%Y-%m-%d"), "end": d.strftime("%Y-%m-%d"),
                       "ret": round(ret, 1), "days": int((d - prev_d).days)})
        prev_d, prev_p = d, px
    ret = (float(close.iloc[-1]) / prev_p - 1) * 100
    cycles.append({"kind": "bull" if ret > 0 else "bear",
                   "start": prev_d.strftime("%Y-%m-%d"), "end": None,
                   "ret": round(ret, 1), "days": int((close.index[-1] - prev_d).days)})
    write_json(f"{prefix}_bullbear.json", {"cycles": cycles})

    # 最坏的那些日子：最差/最好单日 + 单日涨跌分布
    d = close.pct_change().dropna() * 100
    def top(series, n=12):
        return [{"date": dt.strftime("%Y-%m-%d"), "ret": round(float(v), 2)}
                for dt, v in series.items()]
    hist_edges = [(-100, -5), (-5, -3), (-3, -2), (-2, -1), (-1, 0),
                  (0, 1), (1, 2), (2, 3), (3, 5), (5, 100)]
    hist = [{"label": (f"<-5%" if lo == -100 else f">5%" if hi == 100 else f"{lo}~{hi}%"),
             "count": int(((d >= lo) & (d < hi)).sum())} for lo, hi in hist_edges]
    write_json(f"{prefix}_extremes.json", {
        "worst": top(d.nsmallest(12)), "best": top(d.nlargest(12)),
        "hist": hist, "days_total": int(len(d)),
    })


def build_constituents():
    """成分股（Wikipedia）：标普 500 与纳指 100，含 GICS 行业分布。"""
    print("== constituents")
    from io import StringIO
    try:
        html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                            headers=UA, timeout=30).text
        t = pd.read_html(StringIO(html))[0]
        rows = [{"ticker": r["Symbol"], "name": r["Security"], "sector": r["GICS Sector"],
                 "sub": r["GICS Sub-Industry"], "added": str(r.get("Date added", ""))[:10]}
                for _, r in t.iterrows()]
        sectors = pd.Series([r["sector"] for r in rows]).value_counts()
        write_json("sp500_constituents.json", {
            "rows": rows,
            "sectors": [{"sector": s, "count": int(c)} for s, c in sectors.items()],
        })
    except Exception as e:
        print(f"  sp500 constituents failed (kept old): {e}")
        _FAILURES.append({"section": "sp500 constituents", "error": f"{type(e).__name__}: {str(e)[:200]}"})
    try:
        html = requests.get("https://en.wikipedia.org/wiki/Nasdaq-100", headers=UA, timeout=30).text
        found = None
        for t in pd.read_html(StringIO(html)):
            cols = [str(c) for c in t.columns]
            if any("Ticker" in c or "Symbol" in c for c in cols) and len(t) > 80:
                found = t
                break
        if found is None:
            raise RuntimeError("ndx table not found")
        tick_col = next(c for c in found.columns if "Ticker" in str(c) or "Symbol" in str(c))
        name_col = next(c for c in found.columns if "Company" in str(c))
        # 维基的纳指 100 表用 ICB 分类（"ICB Industry"），此处泛匹配 Sector/Industry
        sec_col = next((c for c in found.columns
                        if "Sector" in str(c) or "Industry" in str(c)), None)
        rows = [{"ticker": r[tick_col], "name": r[name_col],
                 "sector": r[sec_col] if sec_col is not None else ""}
                for _, r in found.iterrows()]
        sectors = pd.Series([r["sector"] for r in rows if r["sector"]]).value_counts()
        write_json("ndx_constituents.json", {
            "rows": rows,
            "sectors": [{"sector": s, "count": int(c)} for s, c in sectors.items()],
        })
    except Exception as e:
        print(f"  ndx constituents failed (kept old): {e}")
        _FAILURES.append({"section": "ndx constituents", "error": f"{type(e).__name__}: {str(e)[:200]}"})


# 前二十大持仓两张卡均已下线（SPY/SSGA 于 2026-07-19，QQQ/stockanalysis 于 2026-07-20）。
# 理由：都是 Google 一秒可查的 commodity，不产生差异化；且下掉了 SSGA 授权风险与
# stockanalysis 抓取依赖。集中度改由新的「行业暴露（按权重）」环形图承载，更直观。
# build_top_holdings 随之删除（曾抓 stockanalysis QQQ 持仓）。


# GICS 行业英文键 → 中文名（Yahoo funds_data 的键）
_SECTOR_ZH = {
    "technology": "信息技术", "financial_services": "金融", "communication_services": "通信服务",
    "consumer_cyclical": "可选消费", "healthcare": "医疗保健", "industrials": "工业",
    "consumer_defensive": "必需消费", "energy": "能源", "utilities": "公用事业",
    "realestate": "房地产", "basic_materials": "原材料",
}


# AIAE（Average Investor Allocation to Equities，Jesse Livermore/Philosophical Economics）
# = 全社会股票市值 ÷（股票市值 + 五类借款人债务）。数据全来自 FRED 资金流量表 Z.1（公有领域，季频）。
# 🔑 单位（2026-07-20 CI debug 实测定死）：七个序列**全是百万美元同一单位**，不需换算——
#    首跑我误以为债务是十亿、给股票 ÷1000，造出 1000 倍错配（值 0.0011），被 sane_check 拦下。
#    实测 CMDEBT=21万亿/FGSDODNS=34.5万亿等原始值都在千万量级=百万单位；不除后 AIAE=0.5289 ✅。
# ⚠️ 本机代理连不上 FRED，数据正确性靠 CI 验；现已用 CI 真实分量值算对 0.5289。
# 🚫 只取「今天在历史上排第几」的位置读数，绝不发布 implied_10y_forecast（撞「永不预测方向」红线）。
AIAE_EQUITY = ["NCBEILQ027S", "FBCELLQ027S"]                    # 股票市值：非金融企业 + 金融部门
AIAE_DEBT = ["BCNSDODNS", "CMDEBT", "FGSDODNS", "SLGSDODNS", "WCMITCMFODNS"]  # 五类借款人债务


def compute_aiae(equity: pd.DataFrame, debt: pd.DataFrame) -> pd.Series:
    """纯函数（无网络，便于单测）：七个序列同一单位（百万），不换算。
    对齐季度 → AIAE = 股票 ÷ (股票 + 债务)。"""
    eq = equity.sum(axis=1)
    dt = debt.sum(axis=1)
    df = pd.concat({"eq": eq, "dt": dt}, axis=1).dropna()
    return (df["eq"] / (df["eq"] + df["dt"])).dropna()


def build_aiae():
    """AIAE 台账 → data/aiae.json。头条=当前值在全史的百分位（低=股票配置低=历史上偏悲观）。"""
    print("== AIAE 全社会股票配置")
    eq = pd.DataFrame({s: _fred(s, start="1945-01-01") for s in AIAE_EQUITY})
    dt = pd.DataFrame({s: _fred(s, start="1945-01-01") for s in AIAE_DEBT})
    aiae = compute_aiae(eq, dt)
    if aiae.empty:
        raise RuntimeError("AIAE 空序列——绝不写空台账，留旧文件")
    cur = float(aiae.iloc[-1])
    # 🔑 自检：AIAE 有公开发布真值，近年在 0.40~0.55 高位。落此区间外 = series ID/单位错，标记待人工核。
    sane = 0.30 <= cur <= 0.60
    pct_full = round(float((aiae <= cur).mean() * 100), 1)
    out = {
        "meta": {
            "name": "全社会股票配置（AIAE）",
            "question": "今天所有投资者把多少钱压在股票上，历史上算高还是低？",
            "headline": "AIAE 当前值在全史的百分位（高=仓位重=历史上偏乐观）",
            "nature": "描述性位置读数，非交易信号/非预测；只报位置不报方向。",
            "formula": "股票市值 ÷ (股票市值 + 五类借款人债务)；数据 FRED 资金流量表 Z.1（季频）",
            "source": "FRED (Federal Reserve Z.1 Financial Accounts)",
            "sane_check": sane,          # false = 值落在合理区间外，前端别展示、等人工核 series ID
            "current_value": round(cur, 4),
            "current_pctile_full": pct_full,
            "asof": aiae.index[-1].strftime("%Y-%m-%d"),
        },
        "dates": [d.strftime("%Y-%m-%d") for d in aiae.index],
        "aiae": [round(float(v), 4) for v in aiae.values],
    }
    print(f"  AIAE 当前 {cur:.4f}（全史第 {pct_full} 百分位，as-of {out['meta']['asof']}）"
          f"{'' if sane else ' 🔴 落合理区间外，series ID 可能有误，前端勿用'}")
    write_json("aiae.json", out)


def build_sector_weights():
    """指数按权重的行业暴露（Yahoo funds_data，每 ETF 1 次调用）。
    与现有「按家数」的行业结构互补：家数看分布，权重看集中度（QQQ 科技占六成才是真相）。"""
    print("== 行业暴露（按权重）")
    for etf, out_name in (("SPY", "sp500_sector_weights.json"), ("QQQ", "ndx_sector_weights.json")):
        try:
            sw = yf.Ticker(etf).funds_data.sector_weightings
            if not sw:
                raise RuntimeError("funds_data.sector_weightings 空")
            rows = [{"key": k, "name": _SECTOR_ZH.get(k, k), "weight": round(float(v) * 100, 2)}
                    for k, v in sw.items() if v]
            rows.sort(key=lambda x: -x["weight"])
            write_json(out_name, {"rows": rows, "etf": etf, "source": f"Yahoo Finance / {etf} funds_data"})
        except Exception as e:
            print(f"  {etf} 行业权重失败（留旧文件）: {e}")
            _FAILURES.append({"section": f"sector weights {etf}", "error": f"{type(e).__name__}: {str(e)[:200]}"})


def _multpl_series(slug: str):
    html = requests.get(f"https://www.multpl.com/{slug}/table/by-month", headers=UA, timeout=30).text
    rows = re.findall(r'<td>([A-Z][a-z]{2} \d{1,2}, \d{4})</td>\s*<td>\s*(?:&#x2002;)?\s*\$?([\d.,]+)', html)
    recs = sorted((datetime.strptime(d, "%b %d, %Y"), float(v.replace(",", ""))) for d, v in rows)
    return recs


def _multpl_estimates(slug: str):
    """multpl 标成 Estimate（带 <abbr title="Estimate">†</abbr>）的月份。2026-09-24：原正则认不出这个标记，
    估计值被静默丢掉，站上 PE 末点停在 06-01。现在单独取出，只作「E」展示，不进历史序列、不进任何分位。"""
    html = requests.get(f"https://www.multpl.com/{slug}/table/by-month", headers=UA, timeout=30).text
    rows = re.findall(r'<td>([A-Z][a-z]{2} \d{1,2}, \d{4})</td>\s*<td>\s*<abbr title="Estimate">[^<]*</abbr>\s*\$?([\d.,]+)', html)
    return sorted((datetime.strptime(d, "%b %d, %Y"), float(v.replace(",", ""))) for d, v in rows)


def build_valuation_extras():
    """标普 PE(TTM) 与 EPS（multpl，格式脆弱，失败保留旧文件）。"""
    print("== 估值/盈利（multpl）")
    for slug, out in (("s-p-500-pe-ratio", "sp500_pe_ttm.json"),
                      ("s-p-500-earnings", "sp500_eps_hist.json")):
        try:
            recs = _multpl_series(slug)
            doc = {"dates": [d.strftime("%Y-%m-%d") for d, _ in recs],
                   "values": [v for _, v in recs]}
            if slug == "s-p-500-pe-ratio":
                est = [(d, v) for d, v in _multpl_estimates(slug) if not recs or d > recs[-1][0]]
                doc["estimates"] = {"dates": [d.strftime("%Y-%m-%d") for d, _ in est],
                                    "values": [v for _, v in est],
                                    "note": "multpl 标 Estimate 的月份（基于预估盈利），只作 E 展示；不进 dates/values、不进任何分位"}
            write_json(out, doc)
        except Exception as e:
            print(f"  {slug} failed (kept old): {e}")
            _FAILURES.append({"section": f"multpl {slug}", "error": f"{type(e).__name__}: {str(e)[:200]}"})


# ------------------------------------------------------- 个股篮子板块

BASKETS = {
    # 科技：半导体 → 存储 → 平台/软件 → 硬件/终端（总览锚用 QQQ，复用 ndx 面板）
    # ⚠️ 顺序必须与 js/app.js 的 BASKET_CFG.tech 一致（rows 决定版面、members 决定翻页顺序）
    "tech": [
        ("NVDA", "英伟达"), ("AVGO", "博通"), ("TSM", "台积电"), ("AMD", "AMD"),
        ("MU", "美光"), ("SNDK", "闪迪"),
        ("MSFT", "微软"), ("GOOGL", "谷歌"), ("META", "Meta"), ("AMZN", "亚马逊"),
        ("AAPL", "苹果"), ("TSLA", "特斯拉"),
    ],
    # 顺序即页面展示顺序：银行 → 卡组织 → 券商 → 投行 → 资管 → 保险 → 加密/稳定币
    "fin": [
        ("JPM", "摩根大通"), ("BAC", "美国银行"), ("V", "Visa"), ("MA", "万事达"),
        ("AXP", "美国运通"), ("SCHW", "嘉信理财"), ("IBKR", "盈透证券"),
        ("GS", "高盛"), ("MS", "摩根士丹利"), ("BLK", "贝莱德"), ("BRK.B", "伯克希尔"),
        ("COIN", "Coinbase"), ("HOOD", "Robinhood"), ("CRCL", "Circle"),
    ],
    "consumer": [
        ("KO", "可口可乐"), ("WMT", "沃尔玛"), ("COST", "好市多"),
        ("HD", "家得宝"), ("TJX", "TJX"), ("MCD", "麦当劳"),
    ],
    "luxury": [
        ("MC.PA", "LVMH"), ("RMS.PA", "爱马仕"), ("RACE", "法拉利"),
    ],
}

# 板块锚 ETF（个股钻取页之上的"总览"层；奢侈品无合适 ETF，用等权组合当锚；XLK 只作个股对比线）
ETF_ANCHORS = ["XLF", "XLP", "XLY", "XLK"]

# 卫星成员：上市太晚，不进"共同起点"的成长曲线与等权组合，但有个股页和对照表
# 卫星成员 = 上市太晚，进不了「共同起点」分析（build_basket 的共同起点 = 核心成员里最晚上市那只）。
# ⚠️ 加新成员前先查上市日：把上市晚的当核心加进来，会把整个篮子的归一化成长曲线与等权组合
#    年度回报/回撤一路截断到它的上市日——站还在、图还画，只是历史悄悄没了。
#    SNDK 2025-02-13 才从西数分拆（实测 355 根），当核心会把科技篮从 2012 截到 2025。
BASKET_SATELLITES = {"fin": {"COIN", "HOOD", "CRCL"}, "tech": {"SNDK"}}


def safe_ticker(t: str) -> str:
    return t.lower().replace(".", "-")


_FX_CACHE = {}

def mcap_usd_b(ticker: str):
    """市值（十亿美元）。先按原代码试（欧股 MC.PA），再试 . → - （美股 BRK.B）；非美元按汇率换算。"""
    for sym in (ticker, ticker.replace(".", "-")):
        try:
            fi = yf.Ticker(sym).fast_info
            v = fi["market_cap"]
            if not v:
                continue
            cur = (fi.get("currency") or "USD").upper()
            if cur != "USD":
                if cur not in _FX_CACHE:
                    _FX_CACHE[cur] = float(yf.Ticker(f"{cur}USD=X").fast_info["last_price"])
                v *= _FX_CACHE[cur]
            return round(v / 1e9, 1)
        except Exception:
            continue
    return None


def build_basket(prefix: str, members: list):
    """个股篮子：归一化成长曲线 + 等权组合面板 + 个股对照表。
    组合与归一化都基于各自币种的百分比回报，跨币种混合仅供比较参考。
    卫星成员（上市太晚）不进共同起点分析，只出对照表行与个股页。"""
    print(f"== basket {prefix}")
    satellites = BASKET_SATELLITES.get(prefix, set())
    core = [(t, n) for t, n in members if t not in satellites]
    closes = {}
    for ticker, label in members:
        closes[ticker] = fetch_history(ticker)["Close"].dropna()
        time.sleep(1)
    df = pd.DataFrame({t: closes[t] for t, _ in core}).dropna()  # 共同起点 = 核心成员最晚上市者
    start = df.index[0]

    # 归一化成长曲线（周频，起点=100）+ 等权组合
    norm = df / df.iloc[0] * 100
    daily_ret = df.pct_change().dropna()
    ew = (1 + daily_ret.mean(axis=1)).cumprod() * 100
    ew = pd.concat([pd.Series([100.0], index=[df.index[0]]), ew])
    weekly = norm.resample("W").last().dropna(how="all")
    ew_w = ew.resample("W").last().dropna()
    write_json(f"{prefix}_growth.json", {
        "start": start.strftime("%Y-%m-%d"),
        "dates": dates(weekly.index),
        "series": [{"ticker": t, "name": n, "values": rnd(weekly[t], 1)} for t, n in core],
        "ew": {"dates": dates(ew_w.index), "values": rnd(ew_w, 1)},
    })

    # 等权组合：年度回报 + 回撤
    annual = ew.resample("YE").last().pct_change().dropna() * 100
    write_json(f"{prefix}_annual.json", {
        "years": [d.year for d in annual.index],
        "returns": rnd(annual, 2),
    })
    dd, episodes = drawdown_episodes(ew)
    weekly_dd = dd.resample("W").min()
    write_json(f"{prefix}_drawdowns.json", {
        "dates": dates(weekly_dd.index),
        "dd": rnd(weekly_dd * 100, 2),
        "episodes": sorted(episodes, key=lambda e: e["depth"])[:15],
    })

    # 个股对照表（共同起点口径）+ 各自全历史统计（个股页 hero 用）
    rows = []
    for t, n in members:
        is_sat = t in satellites
        s = closes[t] if is_sat else df[t]
        years_total = (s.index[-1] - s.index[0]).days / 365.25
        def cagr(y):
            past = s[s.index <= s.index[-1] - pd.Timedelta(days=int(y * 365.25))]
            if past.empty:
                return None
            return round(((s.iloc[-1] / past.iloc[-1]) ** (1 / y) - 1) * 100, 1)
        max_dd = (s / s.cummax() - 1).min() * 100
        full = closes[t]
        full_years = max((full.index[-1] - full.index[0]).days / 365.25, 0.25)
        prev_year = s[s.index.year < s.index[-1].year]
        ytd = round((s.iloc[-1] / prev_year.iloc[-1] - 1) * 100, 1) if len(prev_year) else None
        mc = mcap_usd_b(t)
        rows.append({
            "ticker": t, "name": n, "safe": safe_ticker(t), "mcap": mc,
            "ytd": ytd,
            "y1": cagr(1), "y3": cagr(3), "y5": cagr(5), "y10": cagr(10),
            "since": None if is_sat else round(((s.iloc[-1] / s.iloc[0]) ** (1 / years_total) - 1) * 100, 1),
            "max_dd": round(float(max_dd), 1),
            "start_full": full.index[0].strftime("%Y-%m-%d"),
            "since_full": round(float(((full.iloc[-1] / full.iloc[0]) ** (1 / full_years) - 1) * 100), 1),
            "total_mult": round(float(full.iloc[-1] / full.iloc[0]), 1),
            "max_dd_full": round(float((full / full.cummax() - 1).min() * 100), 1),
        })
    write_json(f"{prefix}_table.json", {"rows": rows, "start": start.strftime("%Y-%m-%d")})

    # 七巨头等权指数（2026-07-20）：复用 tech 篮子已抓的价格，不另发请求。
    # 共同起点 = 七只中最晚上市者（META 2012-05），等权、每日再平衡、rebase 到 100。
    if prefix == "tech":
        build_mag7(closes)

    # 个股钻取页：每只成员生成全套指数面板（各自全历史）
    for t, n in members:
        build_index_panels(f"s_{safe_ticker(t)}", closes[t])


MAG7 = [("NVDA", "英伟达"), ("MSFT", "微软"), ("GOOGL", "谷歌"), ("AMZN", "亚马逊"),
        ("META", "Meta"), ("AAPL", "苹果"), ("TSLA", "特斯拉")]


def build_mag7(closes: dict):
    """七巨头等权指数：共同起点=最晚上市者，等权每日再平衡，rebase 到 100。
    closes 来自 tech 篮子（已抓），只算不抓。缺任一成员则跳过（不出半真的指数）。"""
    print("== 七巨头等权指数")
    try:
        missing = [t for t, _ in MAG7 if t not in closes or closes[t].dropna().empty]
        if missing:
            raise RuntimeError(f"缺成员 {missing}")
        df = pd.DataFrame({t: closes[t] for t, _ in MAG7}).dropna()  # 共同起点=七只都在
        start = df.index[0]
        norm = df / df.iloc[0] * 100                                  # 各成员 rebase 100
        daily_ret = df.pct_change().dropna()
        idx = (1 + daily_ret.mean(axis=1)).cumprod() * 100           # 等权每日再平衡
        idx = pd.concat([pd.Series([100.0], index=[start]), idx])
        weekly = norm.resample("W").last().dropna(how="all")
        idx_w = idx.resample("W").last().dropna()
        # 各成员自起点至今的总倍数（给标注/tooltip 用）
        members = [{"ticker": t, "name": n,
                    "mult": round(float(df[t].iloc[-1] / df[t].iloc[0]), 1),
                    "values": rnd(weekly[t], 1)} for t, n in MAG7]
        write_json("m7_index.json", {
            "name": "七巨头等权指数",
            "start": start.strftime("%Y-%m-%d"),
            "methodology": "等权、每日再平衡、起点=100；起点为七只中最晚上市者的首日",
            "index_mult": round(float(idx.iloc[-1] / 100), 1),
            "dates": dates(weekly.index),
            "index": {"dates": dates(idx_w.index), "values": rnd(idx_w, 1)},
            "members": members,
            "source": "Yahoo Finance（等权指数为本站自建）",
        })
    except Exception as e:
        print(f"  七巨头指数失败（留旧文件）: {e}")


def build_cape():
    print("== 席勒 CAPE")
    try:
        html = requests.get(MULTPL_CAPE, headers=UA, timeout=30).text
        rows = re.findall(r'<td>([A-Z][a-z]{2} \d{1,2}, \d{4})</td>\s*<td>\s*(?:&#x2002;)?\s*([\d.]+)', html)
        if not rows:
            raise RuntimeError("multpl table parse failed")
        recs = [(datetime.strptime(d, "%b %d, %Y"), float(v)) for d, v in rows]
        recs.sort()
        write_json("sp500_cape.json", {
            "dates": [d.strftime("%Y-%m-%d") for d, _ in recs],
            "cape": [v for _, v in recs],
        })
    except Exception as e:
        print(f"  CAPE unavailable this run (kept old file if any): {e}")
        _FAILURES.append({"section": "CAPE", "error": f"{type(e).__name__}: {str(e)[:200]}"})


# ---- 2026-07-19：非致命小节的失败必须留痕 ----
# 2026-07-12→14 管线静默死 4 天，根因**不是**没有 try/except，是 except 里只 print——
# print 进了 Actions 日志，而没有人每天读日志（「真洞=人不看」）。
# ∴ 失败除了打印，还要写进 data/meta.json，让 notify_discord 看得见、当天就吵。
_FAILURES: list[dict] = []


def _guard(label: str, fn, *args, **kwargs) -> bool:
    """跑一个非致命小节：失败只留旧文件、不拖死主管线，但一定留痕。"""
    try:
        fn(*args, **kwargs)
        return True
    except Exception as e:
        _FAILURES.append({"section": label, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        print(f"  ⚠️ {label} 失败（留旧文件）: {e}")
        return False


def main():
    print("fetching prices …")
    # 四条都走「上游优先、自家已发布历史兜底」（见 fetch_close_or_own 头注）。
    # 此前是裸调 fetch_history：任一条抛错，整轮在第一步就死、后面 _guard 全都跑不到。
    gspc = _fill_index_gaps(fetch_close_or_own("^GSPC"), "SP500", "^GSPC")
    ixic = _fill_index_gaps(fetch_close_or_own("^IXIC"), "NASDAQCOM", "^IXIC")
    ndx = _fill_index_gaps(fetch_close_or_own("^NDX"), "NASDAQ100", "^NDX")
    vix = _fill_vix_gaps(fetch_close_or_own("^VIX"), gspc.index)
    try:
        vxn = fetch_history("^VXN")["Close"]
    except RuntimeError:
        vxn = None

    build_kindex(ndx, gspc, vix)
    build_leaps(gspc, ndx, vix)
    _guard("情绪仪表盘", build_sentiment, vix, vxn)
    # （build_naaim 已于 2026-09-24 删除）🔴 2026-09-24 Klay 定停抓：NAAIM 08-01 起收费、公开页无当期数据，每天报错；
    #   宏观页「仓位与杠杆」改由 CFTC 股指期货持仓（cot_equity，周频免费）承担，NAAIM 卡片已下线
    _guard("恐惧的标价指数", build_leaps_index, gspc, vix)   # ← 唯一在赚钱的读数，失败必须吵
    _guard("VX 期限结构", build_vx_curve, vix)
    _guard("COT 持仓", build_cot_vix)
    # 🆕 2026-08-28 传导链（Klay 拍板）：火药层=利率/国债期货持仓；保费层利率腿=MOVE。
    #    管道层三个走 build_macro 的 fetch_plan（hy_oas/ig_oas/rrp 早已在拉，08-28 补 discount_window）。
    _guard("COT 利率持仓（传导链火药层）", build_cot_rates)
    _guard("MOVE（传导链利率腿）", build_move)
    _guard("政策路径（联邦基金期货隐含利率）", build_policy_path)   # 2026-08-28 Klay 拍板
    _guard("银行优先股（单名 CDS 替代）", build_bank_credit)        # 同上
    _guard("波动率家族", build_vol_family, vix)
    # 🆕 2026-09-15 仓位与杠杆四件（Klay 令：免费的全接入直接上线）
    _guard("COT 股指期货站位", build_cot_equity)
    _guard("FINRA 融资余额", build_margin_debt)
    _guard("OFR 对冲基金杠杆", build_ofr_hedge_funds)
    _guard("指数波动率家族", build_vol_indices)
    _guard("做空成交结构", build_short_flow)   # 增量：日常只补 1 天；回填另用大 max_backfill 手动跑
    _guard("做空持仓", build_short_interest)   # 双月，滞后约 2 周；只追加、修订另注
    _guard("行业暴露", build_sector_weights)   # 按权重（Yahoo funds_data，每 ETF 1 次调用）
    _guard("AIAE 股票配置", build_aiae)         # FRED Z.1 季频；数据正确性首跑靠 CI 验（本机连不上 FRED）
    build_index_val()
    build_macro()
    build_index_panels("sp500", gspc, vix, "VIX")
    build_index_panels("ixic", ixic)
    build_index_panels("ndx", ndx, vxn, "VXN")
    build_index_extras("sp500", gspc)
    build_index_extras("ndx", ndx)
    build_constituents()
    build_valuation_extras()
    build_pulse()  # 依赖 constituents 与 pe_ttm，放在其后
    # Guarded per basket / per ETF: one member's upstream error must not stop the whole
    # daily run (and with it the already-built headline gauge). Failures land in meta.failures.
    for prefix, members in BASKETS.items():
        _guard(f"basket {prefix}", build_basket, prefix, members)
    for etf in ETF_ANCHORS:
        _guard(f"ETF panel {etf}", lambda e=etf: build_index_panels(f"s_{safe_ticker(e)}", fetch_history(e)["Close"]))
        time.sleep(1)
    build_cape()

    write_json("meta.json", {
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": "Yahoo Finance · Cboe · FRED · FINRA · CFTC · CNN Fear & Greed · multpl.com",
        # 非致命小节的失败清单：空 = 本次全绿。notify_discord 读这个字段决定要不要吵。
        "failures": _FAILURES,
        # Sessions Yahoo omitted and FRED supplied (2026-09-24). Empty = no fills this run.
        "gap_filled": _GAPFILL,
    })
    if _FAILURES:
        print(f"\n⚠️ 本次有 {len(_FAILURES)} 个小节失败（已写入 meta.json，告警器会响）：")
        for f in _FAILURES:
            print(f"   · {f['section']}: {f['error']}")
    print("done.")


if __name__ == "__main__":
    sys.exit(main())
