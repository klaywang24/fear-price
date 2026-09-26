#!/usr/bin/env python3
"""上游序列回改警报器 · 云端侧（2026-08-25 建 · 08-11 设计的「站仓 CI 侧」落地）。

━━ 它证明什么 ━━
本站引用的上游指数序列（Cboe CDN 每日史）若被**回头修改历史**，本地采集器会发现——
但本地那台笔记本合盖/死机时，它的警报也跟着死。本 workflow 住在云端：
笔记本消失一个月，它每天照样对账，历史被改当天就开 Issue 发邮件。

━━ 它怎么做到「对账但不存数据」━━
公开仓里只存**指纹**（data/upstream_fp.json）：
  {"series": {"VIXHY": {"through": "<日期>", "rows": N, "sha": "<前缀哈希>"}}}
前缀哈希 = 从文件头到 through 那一行（含）的**逐字节** SHA256。
每天：拉全史 → 重算「截至上次 through」的前缀哈希 → 与指纹比对：
  · 一致 ⇒ 历史未动，把 through 推进到今天最后一行，指纹随 CI 提交（append-only 的正常步进）
  · 不一致 / through 行消失 ⇒ **上游改了已发布的历史** ⇒ 退出码 1，workflow 开 Issue
数据本身一个字节不进公开仓（上游条款；同 anchor_wayback「公开指纹不公开内容」的哈希承诺路线）。

━━ 与本地采集器的分工（不是重复建设）━━
本地（每日 18:35）：逐字节比对 + 记回改台账 + **跟随**（转录者条款）——它是**账房**。
本云端：只回答一个是非题「历史动没动」——它是**哨兵**，在账房断电时兜底。
两边判据独立实现但语义一致；哨兵响了而账房没记账 ⇒ 说明本地线已死，双倍警报正确。

退出码：0=历史未动（或首建基线）· 1=检出回改（workflow 据此开 Issue）· 3=拉取失败（无法验证，
不算回改；连续多天 3 会在 Actions 历史里肉眼可见，v1 不为它单独开 Issue）。
自检：--selftest（篡改/删行必须报，纯追加必须过）。
"""
import hashlib, json, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FP = ROOT / "data" / "upstream_fp.json"
# 2026-09-25 换源：原 `us_indices/daily_prices/{s}_History.csv` 自 08/14 起冻结（本机已 403，云端读到的是
#   08/14 那份死文件）⇒ 哨兵每天对着死文件报「历史未动 +0 新行」，一个多月没人发现。改用本机账房
#   fetch_institutional 同一个活端点（charts/historical JSON·全史 2012-03 起·齐到最新交易日）。
SERIES = {s: f"https://cdn.cboe.com/api/global/delayed_quotes/charts/historical/_{s}.json"
          for s in ("VIXHY", "VIXIG")}
SOURCE = "cboe-json-v1"      # 指纹里记来源；与旧 CSV 指纹不可比，换源时旧指纹存档、重建基线
MAX_STALE_WEEKDAYS = 5       # 上游末日落后超过 5 个工作日＝源停更了（不是历史被改），退出码 4
UA = "market-chronicle-integrity/1.0 (+https://chronicle.klay-wang.com)"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def parse_json(text):
    """JSON 全史 → (逐行 bytes「mm/dd/yyyy,close」, 日期列表)。close 保留上游原串（逐字转录）。"""
    j = json.loads(text)
    seq = j["data"] if isinstance(j.get("data"), list) else j["data"]["data"]
    rows, dates = [], []
    for rec in seq:
        v = (rec.get("close") or "").strip() if isinstance(rec.get("close"), str) else rec.get("close")
        if v in (None, ""):
            continue
        y, m, d = rec["date"][:10].split("-")
        ds = f"{m}/{d}/{y}"
        rows.append(f"{ds},{v}".encode("utf-8"))
        dates.append(ds)
    return rows, dates


def weekday_lag(mdY, today):
    """(末日, today] 之间的工作日数（未扣假日：宁可多报）。"""
    from datetime import date, timedelta
    m, d, y = mdY.split("/")
    cur, n = date(int(y), int(m), int(d)), 0
    while cur < today:
        cur += timedelta(days=1)
        n += cur.weekday() < 5
    return n


def prefix_sha(rows_bytes, upto_idx):
    h = hashlib.sha256()
    for b in rows_bytes[: upto_idx + 1]:
        h.update(b); h.update(b"\n")
    return h.hexdigest()


def check_series(name, rows, dates, fp_entry):
    """核心判定，纯函数可测。返回 (verdict, new_entry, msg)；verdict ∈ ok/revised/bootstrap"""
    if not fp_entry:
        e = {"through": dates[-1], "rows": len(rows), "sha": prefix_sha(rows, len(rows) - 1)}
        return "bootstrap", e, f"{name}: 首建基线（{len(rows)} 行，through {dates[-1]}）"
    thr = fp_entry["through"]
    if thr not in dates:
        return "revised", None, f"{name}: 🔴 基线行 {thr} 在上游消失 ⇒ 历史被删改"
    i = dates.index(thr)
    got = prefix_sha(rows, i)
    if got != fp_entry["sha"]:
        return "revised", None, (f"{name}: 🔴 截至 {thr} 的前缀哈希不符 "
                                 f"⇒ 上游改了已发布的历史（记录 {fp_entry['sha'][:12]}… 现 {got[:12]}…）")
    e = {"through": dates[-1], "rows": len(rows), "sha": prefix_sha(rows, len(rows) - 1)}
    grew = len(rows) - fp_entry.get("rows", 0)
    return "ok", e, f"{name}: ✅ 历史未动（+{grew} 新行，through {dates[-1]}）"


def selftest():
    rows = [f"0{i}/01/2020,{100+i}.5".encode() for i in range(1, 8)]
    dates = [r.decode().split(",")[0] for r in rows]
    _, base, _ = check_series("T", rows, dates, None)
    # ① 纯追加 → ok
    rows2 = rows + [b"08/01/2020,200.0"]; dates2 = dates + ["08/01/2020"]
    v, _, m = check_series("T", rows2, dates2, base); assert v == "ok", m
    # ② 改历史一行 → revised
    rows3 = list(rows2); rows3[2] = b"03/01/2020,999.9"
    v, _, m = check_series("T", rows3, dates2, base); assert v == "revised", m
    # ③ 删基线行 → revised
    rows4 = rows2[:3] + rows2[4:]; dates4 = dates2[:3] + dates2[4:]
    # 删的是中间行，through(07/01)还在但前缀变了
    v, _, m = check_series("T", rows4, dates4, base); assert v == "revised", m
    # ④ 删掉 through 行本身 → revised
    rows5 = rows2[:-2]; dates5 = dates2[:-2]
    v, _, m = check_series("T", rows5, dates5, base); assert v == "revised", m
    # 2026-09-25：JSON 解析与新鲜度
    from datetime import date
    r, d = parse_json('{"data":[{"date":"2026-09-24","close":"131.1"},{"date":"2026-09-25","close":"131.38"}]}')
    assert r == [b"09/24/2026,131.1", b"09/25/2026,131.38"] and d[-1] == "09/25/2026", r
    assert weekday_lag("08/14/2026", date(2026, 9, 25)) > MAX_STALE_WEEKDAYS, "08/14 冻结没判出陈旧"
    assert weekday_lag("09/24/2026", date(2026, 9, 25)) <= MAX_STALE_WEEKDAYS
    print("selftest: 7/7 通过（追加放行·改行/删行/删基线全报·JSON 解析·08/14 冻结判陈旧·昨日不判）")
    return 0


def main():
    """退出码：0 历史未动 · 1 检出回改（开 Issue）· 2 本脚本自身出错 · 3 全部拉取失败 · 4 上游停更（末日陈旧）。
    2026-09-25：2/3/4 在 workflow 里单独判红（原先 3 两个分支都不进、job 绿；崩溃退 1 会误开「历史被改」Issue）。"""
    if "--selftest" in sys.argv:
        return selftest()
    try:
        return _run()
    except Exception as e:                                   # noqa: BLE001
        print(f"🔴 哨兵自身出错（不是上游回改）：{type(e).__name__}: {e}")
        return 2


def _run():
    from datetime import datetime, timezone
    fp = json.loads(FP.read_text()) if FP.exists() else {"series": {}}
    revised, fetched, stale = [], 0, []
    today = datetime.now(timezone.utc).date()
    for name, url in SERIES.items():
        try:
            text = fetch(url)
            rows, dates = parse_json(text)
        except Exception as e:
            print(f"{name}: ⚠️ 拉取/解析失败（无法验证，不算回改）：{type(e).__name__}")
            continue
        if len(rows) < 3000:
            print(f"{name}: ⚠️ 仅 {len(rows)} 行，疑半截页，跳过（不算回改）")
            continue
        fetched += 1
        old = fp["series"].get(name)
        if old and old.get("source") != SOURCE:
            fp.setdefault("archived", {})[name] = dict(old, archived_at=today.isoformat(),
                                                       why="旧 CSV 源 08/14 起冻结／403，换 JSON 活端点重建基线")
            print(f"{name}: ↪ 换源（{old.get('source', 'csv')} → {SOURCE}）⇒ 旧指纹存档，按新源重建基线")
            old = None
        verdict, entry, msg = check_series(name, rows, dates, old)
        print(msg)
        if verdict == "revised":
            revised.append(msg)
            continue
        entry["source"] = SOURCE
        fp["series"][name] = entry
        lag = weekday_lag(entry["through"], today)
        if lag > MAX_STALE_WEEKDAYS:
            stale.append(f"{name} 末日 {entry['through']}（落后 {lag} 个工作日）")
    if revised:
        return 1
    if fetched == 0:
        return 3
    fp["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    FP.write_text(json.dumps(fp, ensure_ascii=False, indent=1) + "\n")
    if stale:
        print("🔴 上游停更（不是历史被改）：" + "；".join(stale))
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
