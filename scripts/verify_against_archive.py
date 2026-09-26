#!/usr/bin/env python3
"""与第三方存档逐字对账（2026-08-05 新建，堵住渗透审计发现的最后一个洞）。

━━ 为什么必须有它：一次攻击者视角审计的产物 ━━
2026-08-05 以「我是拿到你 GitHub 凭据的攻击者」的角度全面推演，发现一条能穿透
全部四道自动防线的路径：

  攻击 A（改一天、重算那一行的 chain）：后续行 prev 断裂 → anchor_hashes --verify 报红 ✅挡住
  攻击 B（**全链重造**：改数据后把整条链从创世重新算一遍）：
     · verify_ledger  过 ✅ —— 它比对的是 git 历史，而历史同样被强推改写了
     · anchor_hashes --verify 过 ✅ —— 链内部完全自洽，它只检查自洽
     · check_witness_health 过 ✅ —— 它只问 Wayback「有没有近期快照」，
       **从不下载快照内容与本地链头比对**
     · 看门狗全绿、Discord 平安播报、Issue 不开
  ⇒ 四道防线全部放行。唯一能戳穿攻击 B 的动作，此前只存在于「人想起来手动去查」。

**而 Wayback 快照恰恰是解药**：它存的是当天那份 ledger_hashes.jsonl 的**真实内容**，
攻击者改不了 Internet Archive。把它下下来逐字比对，攻击 B 立刻现形。
本脚本就是把「人想起来手动做」变成一道每天自动跑的闸。

🔑 家规印证：`anchor_hashes.py` 头注早写过「**第三方见证才是效力来源**」——
   但此前只做到了「让它被见证」，没做到「**回头核对见证的内容**」。
   见证若从不被回读，它就只是一次仪式。

判据分三档（家规 §46）：
  ok      —— 存档里的历史行与本地逐字一致（本地可以更长，那是正常追加）
  bad     —— **存档里存在、本地却不同或消失** = 历史被改写，这是最高级别告警
  unknown —— IA 限流/网络失败，本次没测到（不算通过也不算故障）

用法：
    python3 scripts/verify_against_archive.py            # 取最近一份存档比对
    python3 scripts/verify_against_archive.py --all      # 比对全部存档快照（慢，做深度审计时用）
    python3 scripts/verify_against_archive.py --file /tmp/x.jsonl   # 拿本地某文件当「本地版」测试
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAIN = ROOT / "data" / "ledger_hashes.jsonl"
TARGET_URL = "https://chronicle.klay-wang.com/data/ledger_hashes.jsonl"
UA = "market-chronicle-archive-verify/1.0 (+https://chronicle.klay-wang.com)"


def _get(url: str, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:
        return -1, str(e)[:120]


def snapshot_timestamps(limit: int) -> list[str]:
    """列出可用于对账的存档时间戳（新→旧）。

    🔴 2026-08-06 大改（攻击者视角审计发现这道闸从建成起从未成功跑过一次）
    ━━ 病因不是限流，是「找」和「取」两条路命运不同 ━━
    实测（同一时刻、同一网络）：
        web.archive.org 首页            → 200  可达
        /__wb/sparkline（找时间戳）      → 498
        /wayback/available（找）         → 000
        /cdx/search/cdx（找）            → 000
        /web/<ts>id_/<url>（**取内容**）→ 200，45KB 真链内容
    三条「发现」路全断，而「取内容」那条完好。旧实现把发现放在第一步，
    于是每次都在第一步返回空 → 报 unknown → 看门狗把 unknown 当「无异常」
    → **这道唯一能挡「全链重造」的闸，实际是一场仪式**（本文件头注早写过这句话，
    却应在了自己身上）。

    ━━ 修法：时间戳我们自己有，不必问 IA ━━
    `data/anchor_log.jsonl` 每天记着当日**确认过**的链头快照时间戳。
    拿它当发现源，IA 的发现端点降为备用。
    ⚠️ 有人会问：用自家日志当输入，攻击者删掉它不就躲过对账了吗？
       答：时间戳只是**去哪儿看**的线索，效力来自**IA 服务的那份内容**（他改不了）。
       删线索会让本项测不到 → 而「持续测不到」本身已被 check_witness_health
       升级为 bad（见那边 STALE_OK_DAYS），删线索也躲不过，只是换了个警报响。
    """
    out: list[str] = []
    log = ROOT / "data" / "anchor_log.jsonl"
    if log.exists():
        for line in reversed(log.read_text(encoding="utf-8").splitlines()):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for res in (rec.get("results") or []):
                if "ledger_hashes" in str(res.get("url", "")) and res.get("timestamp"):
                    if res["timestamp"] not in out:
                        out.append(res["timestamp"])
            if len(out) >= limit:
                return out[:limit]
    if out:
        return out[:limit]

    # 备用：IA 自己的发现端点（2026-08-06 实测三条全断，留着以防它们哪天复活）
    q = ("https://web.archive.org/__wb/sparkline?output=json&collection=web&url="
         + urllib.parse.quote(TARGET_URL, safe=""))
    code, body = _get(q, timeout=45)
    if code == 200 and body:
        try:
            d = json.loads(body)
            out = [x for x in (d.get("last_ts"), d.get("first_ts")) if x]
        except json.JSONDecodeError:
            pass
    if len(out) < limit:
        cdx = ("http://web.archive.org/cdx/search/cdx?output=json&fl=timestamp&limit=-40&url="
               + urllib.parse.quote(TARGET_URL, safe=""))
        code, body = _get(cdx, timeout=60)
        if code == 200 and body.strip().startswith("["):
            try:
                rows = json.loads(body)[1:]
                out = sorted({r[0] for r in rows} | set(out), reverse=True)
            except Exception:
                pass
    return out[:limit]


# 🔴 2026-08-18：一次请求 ≠ 一个判决。
#    本项 08-12→08-17 连续 6 个交易日报「未能取得存档」，被 check_witness_health 升级为 bad。
#    复查：不是链子有问题，也不是端点变动 —— IA 对自动请求间歇性限流（429），
#    而 fetch_archived **只打一次 HTTP，失败即返回 None**。
#    ⇒ 「这一次没拿到」被当成了「这一天没测到」，再由连败计数升级成故障。
#    事后用 --all 补测 9 份存档（08-04→08-17）全部逐字一致 —— 那 6 天链子一直是干净的，
#    红的是尺子不是被测物。
# 🔑 第一性：本项要回答的是「存档副本与本地是否一致」。单次 HTTP 只是这个问题的**代理**，
#    代理失败时该做的是再试，而不是把代理的失败当成问题的答案。（与 §83 同族。）
# ⚠️ 重试要分清「拿不到」的两种：
#    · 429 / 超时 / 连接错  → 传输问题，退避后重试
#    · 404                  → 该时间戳真没有这份存档，重试多少次都一样，立刻放弃
#    合成一类的话，要么白等（对 404 死磕），要么误判（对 429 说「不存在」）。
# 🚫 退避是必须的，不是礼貌：无退避的密集重试正是招来 429 的原因，会把偶发限流变成持续限流。
RETRY_DELAYS = (5, 15, 45)          # 秒；共 4 次尝试，最坏约 65 秒，仍远短于单轮预算


def fetch_archived(ts: str, get=None) -> list[dict] | None:
    """取该时间戳的存档内容。id_ 后缀 = 拿原始字节，不带 Wayback 注入的工具条。

    get 参数只为可测：默认走真网络，selftest 注入假的。"""
    get = get or _get
    url = f"https://web.archive.org/web/{ts}id_/{TARGET_URL}"
    last = None
    for attempt in range(len(RETRY_DELAYS) + 1):
        code, body = get(url, 60)
        last = code
        if code == 200 and body.strip():
            try:
                return [json.loads(l) for l in body.splitlines() if l.strip()]
            except json.JSONDecodeError:
                print(f"       （{ts} 取到了但解析失败 —— 这不是限流，是内容坏了，不重试）")
                return None
        if code == 404:
            print(f"       （{ts} 该存档不存在(404) —— 重试无用，放弃）")
            return None
        if attempt < len(RETRY_DELAYS):
            d = RETRY_DELAYS[attempt]
            print(f"       （{ts} 第 {attempt + 1} 次取回失败 code={code}，{d}s 后重试）")
            time.sleep(d)
    print(f"       （{ts} 重试 {len(RETRY_DELAYS) + 1} 次仍取不到，最后 code={last}"
          f"{' ＝ IA 限流' if last == 429 else ''}）")
    return None


def compare(archived: list[dict], local: list[dict]) -> tuple[str, list[str]]:
    """存档必须是本地的**前缀**：第 i 行对第 i 行，逐行逐字段比。

    🔴 2026-08-06 改（改跳过判据之前的**前置条件**，顺序不能反）：
    初版按 `date` 建字典查表 —— 同一天出现两行时后者覆盖前者，存档里的旧行会被
    拿去和当天的新行比 ⇒ 误报「历史被改写」＝我们自己造的最高级别假警报（推演实证）。
    而「同日两行」马上会真实存在：行是**事件**（拍了一次指纹）不是日历格子，
    内容变了当天重跑就该再写一行。
    「前缀」本来就是只追加链的本征不变量：**存档那一刻的每一行，必须原封不动地
    出现在本地的相同位置上** —— 比按日期查表更强（连行序都锁死），且天然容忍同日多行。
    `date` 也纳入比对：行不可变，标签同样不许事后改。
    """
    problems: list[str] = []
    if len(archived) > len(local):
        problems.append(f"存档 {len(archived)} 行 > 本地 {len(local)} 行（历史被截断或删除）")
    for i, a in enumerate(archived):
        if i >= len(local):
            break
        l = local[i]
        bad = [k for k in ("date", "chain", "prev", "files") if a.get(k) != l.get(k)]
        if bad:
            problems.append(f"第 {i+1} 行（存档日期 {a.get('date')}）：字段 {'/'.join(bad)} 与存档不符（历史被改写）")
    return ("bad" if problems else "ok"), problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="比对全部存档快照（深度审计）")
    ap.add_argument("--file", help="用指定文件当本地版（测试用）")
    args = ap.parse_args()

    src = Path(args.file) if args.file else CHAIN
    if not src.exists():
        print(f"🔴 本地链文件不存在：{src}")
        return 1
    local = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]

    tss = snapshot_timestamps(40 if args.all else 1)
    if not tss:
        print("❔ 未能取得存档时间戳（多半是 Internet Archive 限流）——本次没测到，不代表通过")
        return 0                       # unknown 不算失败：量不到 ≠ 出事（与体检同一家规）

    checked = 0
    all_problems: list[str] = []
    for ts in tss:
        arch = fetch_archived(ts)
        if arch is None:
            print(f"  ❔ {ts} 存档取不到内容，跳过")
            time.sleep(2)
            continue
        st, probs = compare(arch, local)
        checked += 1
        mark = "✅" if st == "ok" else "🔴"
        print(f"  {mark} {ts} 存档 {len(arch)} 行 vs 本地 {len(local)} 行"
              + ("" if st == "ok" else f" —— {len(probs)} 处不符"))
        for p in probs[:6]:
            print(f"       · {p}")
        all_problems += probs
        time.sleep(2)

    if checked == 0:
        print("❔ 所有存档均取不到内容，本次未能核对")
        return 0
    if all_problems:
        print(f"\n🔴 与第三方存档对账失败：{len(all_problems)} 处不符。"
              "\n   这意味着**已公开发布过的历史被改写或删除** —— 最高级别告警，立即人工介入。")
        return 1
    print(f"\n✅ 与 {checked} 份第三方存档逐字一致；本地 {len(local)} 行（更长属正常追加）")
    return 0


if __name__ == "__main__":
    # 2026-09-25：自身崩溃退 2（原先 traceback 退 1，与「和存档不符」同码，被体检报成篡改）
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
