#!/usr/bin/env python3
"""Wayback 锚定（2026-08-04 重写，取代 daily.yml 里的内联 bash）。

━━ 为什么重写：内联版有三个叠加的 bug，一起把「效力来源」这层悄悄漏空了 ━━
2026-08-04 全盘扫描时实测发现，`ledger_hashes.jsonl`（链头）的 Wayback 快照
**8 次全在 7 月，最后一次 07-31 23:15**，而链已写到 08-03 —— 断了三天，
CI 却每天照报「锚定结果：成功 5/5」。三个原因缺一不可：

  A. `curl -sS URL -o /dev/null` **不带 -f**：HTTP 429/503 时 curl 退出码仍是 0
     （已实测），于是被限流计成成功。Internet Archive 对 SPN2 限流很凶。
  B. 锚定步排在 `commit data` **之后**，写完 `data/anchor_log.jsonl` runner 就销毁 ——
     注释里写的「随下次 commit 提交」永远不会发生，**该文件从未存在过一次**。
  C. 只记 ok/fail 计数，不记快照 URL 与时间戳 —— 十年后只能说「我调用过」，
     不能一秒证明「它存下来了」。

∴ 本脚本三条设计：
  1. **判据是「查得到快照」，不是「调用没报错」**。save 之后再查 availability API，
     记下真实的 archived_url + timestamp。调用成功但没存下来 = 失败。
     ⚠️ **但 SPN2 是异步的**（2026-08-04 实测：save 回 200，availability 仍是上一张快照）——
     存完立刻查几乎永远查到旧的。∴ 本步「保存今天、确认的是此前已完成的」，
     告警阈值按 STALE_DAYS 天算，**不按「今天有没有」算**：
     每天都响的告警等于没有告警（家规）。
  2. **链头排第一**。限流总是从循环后段开始咬，最重要的锚不能排在最后。
  3. **逐 URL 记录结构化结果**，且 daily.yml 里本步骤**自己提交自己的日志**
     （不能指望别人的 commit 带上它 —— 那正是 B 的成因）。

🔑 与 anchor_hashes.py 的分工：那边证明「没被改、也没被偷删」（自洽），
   这边证明「那天它确实被外人看见过」（他证）。**自洽可以整体重造，他证不能。**

用法：
    python3 scripts/anchor_wayback.py --sha <commit>     # CI
    python3 scripts/anchor_wayback.py --check-only       # 只查现状，不发起存档
"""
from __future__ import annotations

import argparse
import json
import os
import time
import pathlib
import configparser
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "data" / "anchor_log.jsonl"
SITE = "https://chronicle.klay-wang.com"
UA = "market-chronicle-anchor/1.0 (+https://chronicle.klay-wang.com)"

# 🔴 顺序即优先级：限流从后段开始咬，链头必须排第一。
#    改这个列表前先想清楚「如果今天只存得下一个，应该是哪个」。
def targets(sha: str | None) -> list[str]:
    t = [
        f"{SITE}/data/ledger_hashes.jsonl",   # 链头 —— 最重要，永远排第一
        f"{SITE}/data/kindex.json",
        f"{SITE}/data/options_page.json",     # 期权判断台账的公开投影（2026-08-05 加）：
                                              # 人的判断比机械读数更需要第三方见证——
                                              # 「抄走的快照没有时间戳」这句话要对期权页成立，
                                              # 前提就是这里每天有人作证。排第三：判断层优先于门面。
        f"{SITE}/",
        f"{SITE}/data/leaps_gauge.json",
        f"{SITE}/data/credit_witness.json",  # 信用线两门哈希承诺（2026-08-25 加·08-11 设计落地）：纯指纹零数据
        # 两个术语页（2026-08-25 加）：它们见证的不是读数而是**定义**。
        # 家法「公式与序列身份一经公开即不静默更改」要成立，前提是有第三方能作证
        # 「某天的定义就是这么写的」——数据文件证明读数没被改，术语页证明口径没被改。
        # 排在数据之后：读数每天变、优先见证；定义少变，但一旦变就是最需要留痕的那种。
        f"{SITE}/kapx",
        f"{SITE}/fear-price",
    ]
    if sha:
        t.insert(1, f"https://github.com/klaywang24/fear-price/commit/{sha}")
    return t


def _get(url: str, timeout: int = 60) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:                      # 网络层失败也要能分辨，不能吞成 0
        return -1, str(e)[:120]


# ══════════════════════════════════════════════════════════════════════
# 存档凭据（2026-09-23 建 · Klay 令「去修啊」后查出的真根因）
# ══════════════════════════════════════════════════════════════════════
# 🔴 **匿名存档已经不工作了，而我们一直没发现。**
#    2026-09-23 实测：`GET https://web.archive.org/save/<url>` 对**每一个** URL
#    都返回 500（直接 curl 也一样）⇒ 本脚本发出去的存档请求**没有一个成功过**。
#    那为什么快照还在更新？——靠 IA 自家爬虫碰巧路过。运气好就有，运气差就断：
#    `chronicle.klay-wang.com/kapx` 就这么停在 09-19 连续 4 天。
# 🔑 更要命的是它**不报错**：save_http 记了 500，却没有任何闸读这个字段，
#    而「快照还在更新」掩盖了「我们的提交全失败」。⇒ 靠运气的见证链不叫见证链。
#
# 解法＝用 Save Page Now v2 的**带凭据接口**（POST + Authorization: LOW key:secret）。
# 凭据取处，按优先级（**一个都没有就退回匿名 GET，行为与从前完全一致，不破坏 CI**）：
#   ① 环境变量 IA_ACCESS_KEY / IA_SECRET_KEY   ← GitHub Actions 走这条（仓库 Secrets）
#   ② ~/.config/internetarchive/ia.ini 的 [s3] access/secret ← 本机走这条
#      （这是 `ia` 官方 CLI 的标准位置，将来装了它也能直接用同一份）
# 🚫 **凭据永不进仓**：本仓是公开仓。两处都在仓外。
def _ia_keys():
    """返回 (access, secret, 来源标签)；没有就 (None, None, 'anon')。"""
    a, sec = os.environ.get("IA_ACCESS_KEY"), os.environ.get("IA_SECRET_KEY")
    if a and sec:
        return a.strip(), sec.strip(), "env"
    ini = pathlib.Path.home() / ".config" / "internetarchive" / "ia.ini"
    if ini.exists():
        try:
            cp = configparser.ConfigParser()
            cp.read(ini)
            a = cp.get("s3", "access", fallback="").strip()
            sec = cp.get("s3", "secret", fallback="").strip()
            if a and sec:
                return a, sec, "ia.ini"
        except Exception:                        # 配置坏了当没有，不让它炸整轮
            pass
    return None, None, "anon"


SAVE_MODE = "anon"          # 本轮实际用的模式，入 anchor_log 供闸读


LAST_SPN: dict = {}     # 本轮最后一次带凭据提交的回执：{job_id, status, timestamp, message}


def _spn_status(job: str, access: str, secret: str, waits: int = 3) -> dict:
    """问一下这单存档到底成了没。

    🔑 **它解决的是一种「看不出来」**：IA 的公开索引（CDX / availability / sparkline）
       落后于真实抓取——2026-09-23 实证：SPN 回执说 20260923053538 抓取成功，
       而同一时刻 CDX 与 availability 给出的最新快照仍是 4 天前的 20260919001549。
       ⇒ **「存进去了但还没被索引」与「压根没存进去」在产物上长得一模一样**，
          而这两件事的处置完全相反（前者等，后者查）。回执是唯一能把它们分开的东西。
    🚫 但**不拿它改判定**：见证链的价值在于第三方**可公开查证**，没进索引就还查不到，
       该红就红。回执只进记录，供人判断「这红是等索引，还是真没存」。
    """
    H = {"User-Agent": UA, "Accept": "application/json",
         "Authorization": f"LOW {access}:{secret}"}
    for _ in range(waits):
        time.sleep(8)
        try:
            req = urllib.request.Request(
                f"https://web.archive.org/save/status/{job}", headers=H)
            with urllib.request.urlopen(req, timeout=45) as r:
                st = json.loads(r.read().decode())
            if st.get("status") != "pending":
                return st
        except Exception:
            return {}
    return {"status": "pending"}


def save(url: str, retries: int = 3) -> int:
    """发起存档。返回 HTTP 状态码；429 退避重试。

    有凭据 ⇒ POST + Authorization 头（v2 接口，返回 job_id，并回读一次任务状态）；
    没有 ⇒ 老的匿名 GET。
    """
    global SAVE_MODE
    LAST_SPN.clear()
    access, secret, how = _ia_keys()
    SAVE_MODE = how
    for i in range(retries):
        if access:
            data = urllib.parse.urlencode({"url": url, "skip_first_archive": "1"}).encode()
            req = urllib.request.Request(
                "https://web.archive.org/save",
                data=data,
                headers={"User-Agent": UA,
                         "Accept": "application/json",
                         "Content-Type": "application/x-www-form-urlencoded",
                         "Authorization": f"LOW {access}:{secret}"})
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    code = r.status
                    try:
                        j = json.loads(r.read().decode())
                    except Exception:
                        j = {}
                if j.get("job_id"):
                    st = _spn_status(j["job_id"], access, secret)
                    # 🚫 不记 job_id：跨轮用不上，而那串十六进制会被公开仓的个人信息闸
                    #    误判成证件类数字串，实撞过一次、提交被拒。留状态与时间戳足够。
                    LAST_SPN.update({"spn_status": st.get("status"),
                                     "spn_timestamp": st.get("timestamp"),
                                     "spn_message": (j.get("message") or
                                                     st.get("message") or "")[:120]})
            except urllib.error.HTTPError as e:
                code = e.code
            except Exception:
                code = -1
        else:
            code, _ = _get("https://web.archive.org/save/" + url, timeout=90)
        if code != 429:
            return code
        time.sleep(20 * (i + 1))                # 20s / 40s / 60s
    return 429


def _probe_sparkline(url: str) -> tuple[str, dict | None]:
    """主探针：web.archive.org 的 sparkline。

    🔑 为什么用它而不是 archive.org/wayback/available（2026-08-04 实证换掉）：
    整晚交叉验证下来，**sparkline 每次都给出正确的 first_ts/last_ts**，
    而 availability 对同一个 URL 时而返回空（首页明明有 08-03 的快照却报「无」）、
    时而返回限流页。判据不是「哪个看起来更官方」，是**哪个每次都答对**。
    """
    q = ("https://web.archive.org/__wb/sparkline?output=json&collection=web&url="
         + urllib.parse.quote(url, safe=""))
    code, body = _get(q, timeout=45)
    if code != 200 or not body:
        return "unknown", None
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return "unknown", None
    last = d.get("last_ts")
    if not last:
        return "none", None
    return "ok", {"archived_url": f"https://web.archive.org/web/{last}/{url}",
                  "timestamp": last, "first_ts": d.get("first_ts")}


def _probe_availability(url: str) -> tuple[str, dict | None]:
    """备用探针。保留它只是为了「两个都说没有」时更有把握，不作主判据。"""
    q = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    code, body = _get(q, timeout=45)
    if code != 200 or not body:
        return "unknown", None
    try:
        snap = json.loads(body).get("archived_snapshots", {}).get("closest")
    except json.JSONDecodeError:
        return "unknown", None
    if snap and snap.get("available"):
        return "ok", {"archived_url": snap.get("url"), "timestamp": snap.get("timestamp")}
    return "none", None


def latest_snapshot(url: str, retries: int = 3) -> tuple[str, dict | None]:
    """查真实快照，返回 (probe_status, snap)。

    🔴 **三档，不是两档**（2026-08-04 修）：初版把「查不到」和「没有」混成同一个 None，
    availability 被限流时会误报「无快照」。这是家规 §46「报『测量失败』和报绿一样危险」
    的同一形状：输出必须能分 **ok / none / unknown**。
    unknown 不算失败也不算成功 —— 它只说明这次没量到，不触发断供告警。
    """
    for i in range(retries):
        for probe in (_probe_sparkline, _probe_availability):
            st, snap = probe(url)
            if st == "ok":
                return st, snap
        if i < retries - 1:
            time.sleep(12 * (i + 1))
    # 两个探针都没给出 ok：再判一次到底是「都说没有」还是「都没测到」
    st1, _ = _probe_sparkline(url)
    return ("none", None) if st1 == "none" else ("unknown", None)


# 链头允许的最大失联天数。设 3 不设 1：SPN2 异步 + IA 时常限流，
# 按「今天有没有」判会天天误报，而**每周都响的警报等于没有警报**。
# 连续 3 天没有新快照才是真的断供 —— 这是要有人去看的信号。
STALE_DAYS = 3

# ── 🆕 补探队列（2026-09-03 建·Klay 令）────────────────────────────────────
# 【病】GH 每日 commit 页的 URL **每天都是新的**（/commit/<sha>），只在「刚提交完」
#   那一刻被探一次，此后永远不会被再探 ⇒ SPN2 提交失败那天，那一页**永久没有存档**：
#   没有重试、没有补救、也不会有第二次机会发现。
#   🔬 实证（2026-09-03）：08-20 / 08-24 / 08-28 三条抽验，IA availability **至今零快照**。
#      那 5 天的 save_http ＝ 4 次 -1 + 1 次 523（Cloudflare 源站不可达）⇒ **提交本身失败了**。
#      ⚠️ 该结论先用**两个已知有快照的 URL 做对照**才下的（先试的 CDX 路子对照组也返回空
#      ＝尺子坏了；不做对照就会拿坏尺子得出「没存上」）。
# 【修】把「今天没存上／已超期」的 URL 排进队列，**以后每轮回头补探 + 重新提交**。
#   一举两得：救回失败的；让「当天没测到」不再冒充「确实没有」。
# 🔒 队列必须是**仓里的文件、且随本步一起 git add** —— CI 每次都是全新机器，
#   内存里的名单活不过一轮。daily.yml 的 anchor 步已同步点名它；**加新产物必须回去改那一行**。
# 🚦 预算：补探排在当日主锚**之后**，每轮最多 RETRY_BUDGET 条 ——
#   IA 会限流（2026-09-03 本机实测被 429 多次），别让补旧账把当天的正事饿死。
PENDING = ROOT / "data" / "anchor_pending.json"
RETRY_BUDGET = 3        # 每轮补探上限
ZOMBIE_ATTEMPTS = 10    # 补这么多轮仍未成 ⇒ 单独报：永不排空的队列就是下一个僵尸


def _load_pending() -> dict:
    try:
        d = json.loads(PENDING.read_text(encoding="utf-8"))
        if isinstance(d.get("pending"), list):
            return d
    except Exception:
        pass
    return {"pending": [], "resolved_recent": []}


def enqueue(state: dict, url: str, today: str, why: str) -> bool:
    """把「确实无快照／已超期」的目标排进队列。已在队列里就不重复排。
    🚫 probe=unknown 的**不排** —— 那是没测到，排进去等于把测量失败当成结论。"""
    if any(e.get("url") == url for e in state["pending"]):
        return False
    state["pending"].append({"url": url, "since": today, "attempts": 0,
                             "last_try": None, "why": why})
    return True


def apply_probe(entry: dict, probe: str, within_sla: bool, today: str) -> str:
    """对一条队列项应用补探结果，返回 'resolve' / 'retry' / 'skip'。

    · ok 且在 SLA 内 ⇒ **resolve，必须离开队列**（永不排空的队列＝下一个僵尸）
    · ok 但超期 / none ⇒ retry（重新提交，attempts+1）
    · unknown         ⇒ skip：这次没量到，**不计 attempts** ——
      拿 IA 的限流把条目推向僵尸线，等于用测量失败给它定罪。
    """
    if probe == "unknown":
        return "skip"
    if within_sla:
        return "resolve"
    entry["attempts"] = int(entry.get("attempts", 0)) + 1
    entry["last_try"] = today
    return "retry"


def snapshot_age_days(snap: dict | None) -> int | None:
    if not snap or not snap.get("timestamp"):
        return None
    t = datetime.strptime(snap["timestamp"][:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).days


def _selftest() -> int:
    """补探队列的纯逻辑自检（零网络）。双向：该离队的必须离队，该留的必须留。

    🔑 负向的核心不是「会不会报红」，是「**队列会不会排空**」——
       一个永不排空的 pending 名单，就是下一个僵尸。
    """
    ok = bad = 0

    def chk(name, got, want):
        nonlocal ok, bad
        good = got == want
        print(f"  {'✅' if good else '❌'} {name}: 期望 {want} 实得 {got}")
        ok += good
        bad += not good

    st = {"pending": [], "resolved_recent": []}
    chk("入队：新 URL 排进去", enqueue(st, "u1", "2026-09-03", "确实无快照"), True)
    chk("入队：同一 URL 不重复排", enqueue(st, "u1", "2026-09-04", "又一次"), False)
    chk("入队后队列长度", len(st["pending"]), 1)

    e = st["pending"][0]
    # 负向①：没测到 ⇒ 不计 attempts（别拿 IA 限流给条目定罪）
    chk("unknown ⇒ skip", apply_probe(e, "unknown", False, "2026-09-04"), "skip")
    chk("unknown 后 attempts 不变", e["attempts"], 0)
    # 负向②：确实没有 ⇒ 留队并计次
    chk("none ⇒ retry", apply_probe(e, "none", False, "2026-09-05"), "retry")
    chk("retry 后 attempts=1", e["attempts"], 1)
    chk("测到但超期 ⇒ retry", apply_probe(e, "ok", False, "2026-09-06"), "retry")
    chk("attempts 累加到 2", e["attempts"], 2)
    # 正向：确认到了 ⇒ 必须离队
    chk("ok 且在 SLA 内 ⇒ resolve", apply_probe(e, "ok", True, "2026-09-07"), "resolve")

    # 🔑 排空判据：resolve 的条目从队列里消失
    st["pending"] = [x for x in st["pending"] if x["url"] not in {"u1"}]
    chk("resolve 后队列排空", len(st["pending"]), 0)

    # 僵尸判据：补够轮数仍未离队的要能被点名
    st2 = {"pending": [{"url": "z", "attempts": ZOMBIE_ATTEMPTS, "since": "2026-08-01"}],
           "resolved_recent": []}
    chk("僵尸能被点名",
        len([x for x in st2["pending"] if x["attempts"] >= ZOMBIE_ATTEMPTS]), 1)
    chk("没到线的不算僵尸",
        len([x for x in [{"url": "y", "attempts": ZOMBIE_ATTEMPTS - 1}]
             if x["attempts"] >= ZOMBIE_ATTEMPTS]), 0)

    print(f"\n队列自检 {ok} 过 / {bad} 败")
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sha", help="当日 commit SHA（同时锚定其 commit 页）")
    ap.add_argument("--check-only", action="store_true", help="只查现状不发起存档")
    ap.add_argument("--selftest", action="store_true", help="补探队列纯逻辑自检（零网络）")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()

    # 日志行标签=美东日历日（2026-08-06 改，与链行同一把尺子；体检按美东读它，
    # 此前按 UTC 写 ⇒ 美东晚 8 点后手跑会差一天，同 anchor_hashes 的病）
    today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    results, fresh, stale, unknown = [], 0, 0, 0

    for u in targets(args.sha):
        code = None
        if not args.check_only:
            code = save(u)
            time.sleep(6)
        probe, snap = latest_snapshot(u)
        age = snapshot_age_days(snap)
        ok = age is not None and age <= STALE_DAYS
        if probe == "unknown":
            unknown += 1
        elif ok:
            fresh += 1
        else:
            stale += 1
        results.append({"url": u, "save_http": code, "probe": probe,
                        "confirmed_age_days": age, "within_sla": ok,
                        **dict(LAST_SPN), **(snap or {})})
        mark = {"unknown": "❔", "none": "🔴"}.get(probe, "✅" if ok else "🟡")
        ts = (f"{snap['timestamp']}（{age} 天前）" if snap
              else ("这次没测到" if probe == "unknown" else "确实无快照"))
        print(f"  {mark} {u[-52:]:<52} save={code} 已确认快照={ts}")

    # ⚠️ 字段语义：save_http = 今天这次调用；confirmed_* = 此刻已完成并可查的快照，
    #    因 SPN2 异步，今天的那张通常要到下一次运行才会被确认。审计读 confirmed_*。
    # ── 补探队列：入队 → 预算内补探 → 回写（详见 PENDING 上方注释）──
    pend = _load_pending()
    for r in results:
        if r["probe"] != "unknown" and not r["within_sla"]:
            enqueue(pend, r["url"], today,
                    "确实无快照" if r["probe"] == "none"
                    else f"超期 {r.get('confirmed_age_days')} 天")
    resolved, retried, skipped = [], 0, 0
    # 🔴 常设 URL（每天都在主锚里探）一旦入队，必须**用当轮主锚的结果结算**，
    #    不能只是「跳过不重复打 IA」——那样它既不 resolve 也不计次，会**永远躺在队列里**，
    #    正好造出条件三要防的那个「永不排空的队列」。（本条是实现当天自己踩出来的：
    #    第一版写 `queue = [不在 seen_now 的]`，对 options_page.json 这类目标就是永久滞留。）
    _now_by_url = {r["url"]: r for r in results}
    for e in list(pend["pending"]):
        r = _now_by_url.get(e["url"])
        if r and r["probe"] != "unknown" and r["within_sla"]:
            resolved.append({"url": e["url"], "on": today,
                             "timestamp": r.get("timestamp")})
    # 剩下的才需要额外补探；当轮已探过的不重复打 IA（省预算，主锚刚给过答案）
    queue = [e for e in pend["pending"]
             if e["url"] not in _now_by_url
             and e["url"] not in {d["url"] for d in resolved}]
    queue.sort(key=lambda x: (x.get("last_try") or "", x.get("since", "")))   # 最久没试的优先
    for e in queue[:RETRY_BUDGET]:
        p2, s2 = latest_snapshot(e["url"])
        a2 = snapshot_age_days(s2)
        act = apply_probe(e, p2, a2 is not None and a2 <= STALE_DAYS, today)
        if act == "resolve":
            resolved.append({"url": e["url"], "on": today,
                             "timestamp": (s2 or {}).get("timestamp")})
        elif act == "retry":
            retried += 1
            if not args.check_only:
                save(e["url"])
                time.sleep(6)
        else:
            skipped += 1
        print(f"  ↻ 补探 {e['url'][-48:]:<48} → {act}（已试 {e.get('attempts', 0)} 轮）")
    _done = {d["url"] for d in resolved}
    pend["pending"] = [e for e in pend["pending"] if e["url"] not in _done]
    pend["resolved_recent"] = (resolved + pend.get("resolved_recent", []))[:20]
    zombies = [e for e in pend["pending"] if int(e.get("attempts", 0)) >= ZOMBIE_ATTEMPTS]
    pend.update({
        "_what": "锚定补探队列：当轮「确实无快照/已超期」的 URL 排这里，以后每轮回头补探+重提交。"
                 "**必须进 git** —— CI 每次都是全新机器，不入仓则重试只存在于当轮的想象里。",
        "_rule": "unknown（没测到）不入队、也不计 attempts；只有『测到了且在 SLA 内』才离队。"
                 f"attempts ≥ {ZOMBIE_ATTEMPTS} 仍未离队的单独报——永不排空的队列就是下一个僵尸。",
        "updated": today, "queue_len": len(pend["pending"]),
    })
    PENDING.write_text(json.dumps(pend, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n补探队列：待补 {len(pend['pending'])} 条 · 本轮解决 {len(resolved)}"
          f" · 重提交 {retried} · 没测到 {skipped}")
    if zombies:
        print(f"  🔴 队列僵尸（补 ≥{ZOMBIE_ATTEMPTS} 轮仍未成，{len(zombies)} 条）："
              + "；".join(z["url"][-44:] for z in zombies)
              + "\n     —— 要么真存不上、要么判据错了，去看一眼；别让它在队列里挂成常态。")

    rec = {"date": today, "sha": args.sha, "stale_days_sla": STALE_DAYS,
           # 🆕 2026-09-23：把「这一轮是怎么存的、存成功了几个」记进来。
           #    起因＝匿名 save 全线 500 却整整没人发现：save_http 一直在记，
           #    **但没有任何闸读它**，而「快照还在更新」（靠 IA 爬虫）掩盖了提交全失败。
           #    ⇒ 光记不够，得让下游闸读得到：save_mode=anon 且 save_ok=0 ＝ 见证靠运气。
           "save_mode": SAVE_MODE,
           "save_ok": sum(1 for r in results if isinstance(r.get("save_http"), int)
                          and 200 <= r["save_http"] < 300),
           "save_tried": sum(1 for r in results if r.get("save_http") is not None),
           "within_sla": fresh, "out_of_sla": stale, "not_probed": unknown,
           # 🔑 2026-09-03 首跑当晚补：原来只记 len/resolved/zombies，
           #    **分不出「探了 3 条全没测到」和「压根没探」** —— 而首跑正好全是 skip，
           #    从仓里看不出机制跑没跑（「没测到 3」只活在 Actions 日志里＝没人读）。
           #    补 probed/retried/skipped 三个数，让「它动过」这件事进入可查的产物。
           "pending_len": len(pend["pending"]), "pending_resolved": len(resolved),
           "pending_zombies": len(zombies),
           "pending_probed": min(len(queue), RETRY_BUDGET),
           "pending_retried": retried, "pending_skipped": skipped,
           "results": results}
    if not args.check_only:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    head = results[0] if results else {}
    # 🔑 只有「测到了且超期」才算断供；「这次没测到」单独报，不冒充断供也不冒充正常。
    chain_bad = head and head.get("probe") != "unknown" and not head.get("within_sla")
    print(f"\n锚定：{fresh} 在 {STALE_DAYS} 天 SLA 内 / {stale} 超期 / {unknown} 未测到"
          f"（共 {len(results)}）")
    if unknown:
        print(f"::warning::{unknown} 个 URL 本次未能查证（Internet Archive 限流），"
              f"非断供、但本次结果不完整")
    if chain_bad:
        # 🔴 链头连续 STALE_DAYS 天没有新快照 = 独立见证真的断了。这条必须响。
        age = head.get("confirmed_age_days")
        print(f"::warning::链头 ledger_hashes.jsonl 最近快照已 {age} 天前"
              f"（SLA {STALE_DAYS} 天），独立见证断供")
    return 0                                     # 锚定失败不阻塞管线，但绝不静默


if __name__ == "__main__":
    raise SystemExit(main())
