#!/usr/bin/env python3
"""口径自检：把「今天发布的头条读数」用参考实现独立重算一遍。

verify_ledger.py 回答「发布过的值有没有被悄悄改写」；本脚本回答另一半：
「今天新发布的值，是不是仍由发布时那把尺算出来的」。理论上永远一致——
但「理论上应该一样」恰恰是最需要每天被检查、而不是被假设的命题，
因为它有三种真实的翻车方式：
  ① 取数漂移：当日进了脏值（盘后价当收盘、时区错位、上游改版）
  ② 窗口污染：序列混进重复日/丢日/坏值，百分位随之整体偏移
  ③ 代码漂移：日更代码被改动（≤ 变 <、窗口长度、含不含当日、舍入位置）
2026-07-25 首建。当天上下文：同一周里 snapshot 盘后价被当成收盘价两次、
cm_iv 管线红警写进没人读的日志静默死一周——「应该一样」不能靠假设活着。

口径（参考实现，2026-07-25 与线上序列逐日核对过：回测段+前向段
最大差 0.048 = 一位小数舍入噪声）：
  百分位 = 含当日的过去 N 个交易日里 ≤ 当日值的占比 × 100
  N：p3y=756 / p5y=1260 / pfull=全史(2007+)
阈值 0.1：存档保留一位小数，合法舍入差 ≤0.05；真实口径漂移远大于 0.1。
阈值由分布定，不由「今天想不想报警」定。

只核当日最新点 + current 三窗口，不回扫历史——历史点的存档值按修订政策
「发布时算的保持原样」，上游修订后与今日重算会合法地不同，回扫=天天假警。
构建永不 fail（站停更比出一处分歧更糟）；出口两条都必须有人看得见：
data/gauge_math.json（随 daily commit 公开）+ GITHUB_OUTPUT → notify_discord 红警。
"""
import json
import os
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "data", "leaps_gauge.json")
DEST = os.path.join(REPO, "data", "gauge_math.json")
TOL = 0.1


def pctile(series, i, win):
    """含当日的过去 win 个交易日里 ≤ 当日值的占比 ×100；win=None 取全史。"""
    w = series[max(0, i - win + 1):i + 1] if win else series[:i + 1]
    x = series[i]
    return sum(1 for y in w if y <= x) / len(w) * 100


def main() -> int:
    checks, problems = [], []

    try:
        g = json.load(open(SRC, encoding="utf-8"))
    except Exception as e:
        problems.append({"kind": "load_failed", "detail": str(e)})
        g = None

    if g:
        dates, v, p3 = g.get("dates") or [], g.get("vix1y") or [], g.get("expensiveness_3y") or []

        # 序列完整性——窗口被污染时百分位会「静默地全体偏移」，先把窗口本身钉住
        if not (len(dates) == len(v) == len(p3)):
            problems.append({"kind": "length_mismatch",
                             "detail": f"dates={len(dates)} vix1y={len(v)} expensiveness_3y={len(p3)}"})
        if len(set(dates)) != len(dates):
            dup = sorted({d for d in dates if dates.count(d) > 1})[:5]
            problems.append({"kind": "duplicate_dates", "detail": str(dup)})
        if dates != sorted(dates):
            problems.append({"kind": "dates_not_sorted", "detail": "日期序列非升序"})
        if any(x is None for x in v):
            problems.append({"kind": "null_in_vix1y",
                             "detail": f"{sum(1 for x in v if x is None)} 个空值"})

        # 当日最新点：expensiveness_3y 序列 vs 参考实现
        if dates and not problems:
            i = len(dates) - 1
            if p3[i] is not None:
                r = pctile(v, i, 756)
                checks.append({"field": "expensiveness_3y", "date": dates[i],
                               "stored": p3[i], "recomputed": round(r, 1),
                               "diff": round(abs(r - p3[i]), 3)})

            # current 三窗口（头条+两个小字坐标，全部对外展示，全部要能复算）
            cur = g.get("current") or {}
            exp = cur.get("expensiveness") or {}
            if cur.get("date") == dates[i]:
                for f, win in (("p3y", 756), ("p5y", 1260), ("pfull", None)):
                    if exp.get(f) is not None:
                        r = pctile(v, i, win)
                        checks.append({"field": f"current.{f}", "date": cur["date"],
                                       "stored": exp[f], "recomputed": round(r, 1),
                                       "diff": round(abs(r - exp[f]), 3)})
            elif cur.get("date"):
                problems.append({"kind": "current_date_mismatch",
                                 "detail": f'current.date={cur["date"]} vs 序列末日={dates[i]}'})

    mismatches = [c for c in checks if c["diff"] > TOL]
    # 2026-09-25（兜底审计）：一个数都没核（序列空、p3[i] 为 None、current 日期不对上）原先 ok=true ⇒ 零覆盖冒充通过
    if not checks and not problems:
        problems.append({"kind": "no_checks", "detail": "本次一个读数都没能复算（序列空或当日值为空）—— 零覆盖不算通过"})
    ok = not mismatches and not problems
    max_diff = max((c["diff"] for c in checks), default=None)

    out = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "what": "口径自检：当日头条读数用参考实现独立重算。ok=true = 今天的数仍由发布时那把尺算出。",
        "convention": "百分位=含当日的过去N个交易日内≤当日值占比×100；N: p3y=756/p5y=1260/pfull=全史",
        "tolerance": TOL,
        "ok": ok,
        "max_abs_diff": max_diff,
        "checks": checks,
        "mismatches": mismatches,
        "integrity_problems": problems,
    }
    with open(DEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))

    for c in checks:
        flag = "🔴" if c["diff"] > TOL else "✅"
        print(f'{flag} {c["field"]} @ {c["date"]}: 存档 {c["stored"]} vs 重算 {c["recomputed"]} (差 {c["diff"]})')
    for p in problems:
        print(f'🔴 完整性: {p["kind"]}: {p["detail"]}')
    print(f"→ data/gauge_math.json  ok={ok}  max_diff={max_diff}")

    # 供 workflow 读取；告警由 notify_discord.py 发，此处永不 fail 构建
    if gh := os.environ.get("GITHUB_OUTPUT"):
        with open(gh, "a") as f:
            f.write(f"ok={'true' if ok else 'false'}\n")
            f.write(f"max_diff={'' if max_diff is None else max_diff}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
