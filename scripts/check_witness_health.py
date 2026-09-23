#!/usr/bin/env python3
"""见证链体检（2026-08-04 新建）。一条命令回答：这条证据链现在健康吗？

━━ 为什么必须单独存在 ━━
2026-08-04 查出：链头的 Wayback 快照从 08-01 起断了三天，**而 CI 每天照报成功**。
修完锚定逻辑后我加了 `::warning::` —— 然后意识到自己重建了 7·12 那次管线死 4 天的
同一个失败模式：**告警只 print 进 Actions 日志，而没人每天读日志**。
**没有送达通道的告警等于没有告警。**

∴ 本脚本是「判断」，送达交给两条独立通道：
  ① daily.yml 内：结果进 GITHUB_ENV → notify_discord.py 推 Discord（当天可见）
  ② witness-watchdog.yml：**独立 workflow、独立时刻**，不健康就开/更 GitHub Issue
     （Issue 会自动发邮件、且不会像日志一样被冲掉）

🔑 ② 必须独立，因为 ① 有个致命前提：**daily.yml 自己还活着**。
   7·12 那次正是 daily 整个死掉、于是「失败告警」也一起没了。
   所以体检项里有一条专查「daily 是不是已经不跑了」—— 看守看守人的那一层。

判据分三档（家规 §46：报「测量失败」和报绿一样危险）：
  ok / bad / unknown —— unknown 不算健康也不算故障，但必须说出来。

用法：
    python3 scripts/check_witness_health.py            # 人读
    python3 scripts/check_witness_health.py --json     # 机器读
不健康时非零退出。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CHAIN = DATA / "ledger_hashes.jsonl"
ANCHOR_LOG = DATA / "anchor_log.jsonl"
SITE_CHAIN_URL = "https://chronicle.klay-wang.com/data/ledger_hashes.jsonl"

# 各项容忍天数。写在一处，别散在判断里。
SLA = {
    "chain_row": 4,      # 链最后一行（容忍周末 + 一个假日）
    "anchor_log": 4,     # 锚定日志最后一条
    "snapshot": 3,       # 链头的 Wayback 快照
    "daily_run": 4,      # daily 最后一次提交数据（看守看守人）
    "options_page": 4,   # 期权页数据（本机 launchd 产、推上来的，见 check_options_page）
    # 🔴 2026-08-06 加：「持续测不到」多久算故障。
    # 病因见 check_archive_match 头注 —— unknown 此前永不升级，等于给攻击者
    # 留了一个「弄断探测源就能无声关掉警报」的开关，而 IA 的坏端点已免费替他按下。
    "stale_ok": 5,       # 某项连续 5 天没有成功过 → 由 unknown 升级为 bad
}
HEALTH_LOG = DATA / "health_log.jsonl"   # 每次 --record 追加一行，供「上次成功」判据用


ET = ZoneInfo("America/New_York")


def days_since(d: str, et: bool = False) -> int | None:
    """接受 2026-08-04 与 20260804 两种写法（链用前者，Wayback 时间戳用后者）。

    🔑 `et=True` 用于**日历日期**（交易日、提交日）—— 这些日子是按美东记的，
       而 `datetime.now(utc).date()` 在美东傍晚之后已经跨天，直接相减会把
       「昨天」读成「2 天前」。SLA 有 4 天余量所以从不误报，**但读数会骗人**，
       而「看起来对的错读数」正是家规里最危险的那一种量尺问题。
       2026-08-05 接线当天发现：`期权页` 一项按美东算显示 0 天，
       而同一天的 `链最后一行` 按 UTC 算显示 2 天 —— 同一份事实两个读数。
    `et=False`（默认）留给 Wayback 时间戳：那个本来就是 UTC，换成美东才是错的。
    """
    if not d:
        return None
    tz = ET if et else timezone.utc
    for cut, fmt in ((10, "%Y-%m-%d"), (8, "%Y%m%d")):
        try:
            t = datetime.strptime(d[:cut], fmt).date()
            return (datetime.now(tz).date() - t).days
        except ValueError:
            continue
    return None


def last_line_json(p: Path) -> dict | None:
    if not p.exists():
        return None
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else None


def days_since_last_ok(name: str) -> int | None:
    """该体检项上次报 ok 距今几天（美东日历日）。查不到返回 None。

    数据来自 `data/health_log.jsonl`：daily 每天用 `--record` 追加一行并自提交
    （与 `anchor_wayback.py` 自提交锚定日志同一范式 —— 家规 §26 先用现成做法）。
    看门狗只读不写（它的权限就是 contents: read）。
    """
    if not HEALTH_LOG.exists():
        return None
    for line in reversed(HEALTH_LOG.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (rec.get("checks") or {}).get(name) == "ok":
            return days_since(rec.get("date", ""), et=True)
    return None


def escalate_if_stale(name: str, res: dict) -> dict:
    """unknown 可以短暂容忍，不能无限期 —— 超期即升 bad（2026-08-06 新规）。

    🔑 家规 §46 说「报测量失败和报绿一样危险」。此前**展示层**遵守了（显示 ❔），
       **升级层**没有：unknown 不管持续多久都不开 Issue、不发邮件，
       而看门狗的关闭判据是 `!= bad`，于是 unknown 还会把真告警自动关掉。
       实证：08-05 15:15 那次看门狗日志原文写着「✅ 体检已无异常项，自动关闭」，
       而当时「与存档逐字对账」从建成起一次都没成功过。
    ⇒ 只要某项连续 STALE 天没有 ok 过，就当故障处理。ok 会重置计数。
    """
    if res.get("status") != "unknown":
        return res
    age = days_since_last_ok(name)
    lim = SLA["stale_ok"]
    if age is None:
        # 没有任何成功记录：给日志本身留出建立期，按日志首行年龄判
        first = None
        if HEALTH_LOG.exists():
            for line in HEALTH_LOG.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        first = days_since(json.loads(line).get("date", ""), et=True)
                    except json.JSONDecodeError:
                        pass
                    break
        if first is not None and first > lim:
            res = dict(res, status="bad",
                       detail=res["detail"] + f" · 🔴 建档 {first} 天以来从未成功过一次，按故障处理")
        return res
    if age > lim:
        res = dict(res, status="bad",
                   detail=res["detail"] + f" · 🔴 已连续 {age} 天没有成功过（上限 {lim}），按故障处理")
    return res


def check_chain_row() -> dict:
    row = last_line_json(CHAIN)
    if not row:
        return {"status": "bad", "detail": "ledger_hashes.jsonl 缺失或为空"}
    age = days_since(row["date"], et=True)      # 交易日按美东记
    return {"status": "ok" if age is not None and age <= SLA["chain_row"] else "bad",
            "detail": f"链最后一行 {row['date']}（{age} 天前）", "age": age}


def check_anchor_log() -> dict:
    if not ANCHOR_LOG.exists():
        # 🔴 这正是 08-04 之前的状态：文件从未存在过，而没有任何人发现
        return {"status": "bad", "detail": "anchor_log.jsonl 不存在 —— 锚定结果从未被留档"}
    rec = last_line_json(ANCHOR_LOG)
    age = days_since(rec.get("date", ""), et=True) if rec else None   # 同链，交易日
    if age is None:
        return {"status": "unknown", "detail": "anchor_log 最后一条无法解析日期"}
    if age > SLA["anchor_log"]:
        return {"status": "bad", "detail": f"锚定日志最后一条 {rec['date']}（{age} 天前）", "age": age}
    # 🔴 2026-09-23 修（Klay 令「去修啊」）：判据从**抽样**改成**聚合**。
    #    病：原来只读 `results[0]`（一条记录里 9 个探针的第 1 个），结论却挂在整项上。
    #    🔬 实测代价：`https://chronicle.klay-wang.com/kapx` 的存档停在 2026-09-15，
    #        到 09-23 已 8 天、SLA 4 天、记录里 within_sla=false、顶层 out_of_sla=1 ——
    #        而 results[0] 是 ledger_hashes.jsonl（1 天前，正常）⇒ **本项连报 8 天绿**。
    #    🔑 看板 JS 早在 2026-09-03 就改成读聚合字段了，**这一侧没跟着改** ——
    #        同一处判据两个实现，松的那把装在 CI 上，于是 CI 天天说没事。
    #        家规「一处判据只能有一个实现」，本次是它的第 N 次实证。
    #    🔑 第一性：**结论的覆盖面不许大于判据的覆盖面。** 量了 1 个探针就只能说这 1 个。
    #        推论：**绿灯的文案必须逐字等于判据** —— 所以下面把超期的 URL 逐个点名，
    #        不再只说一句「锚定正常」。
    # ⚠️ 点名的判据必须与聚合的算法逐字一致，否则「说 1 个、列 2 个」——
    #    本次修闸时当场犯了一次：起初写 `within_sla is False`，而 github commit 那条
    #    是 probe=unknown **且** within_sla=False（它计进 not_probed 不计进 out_of_sla），
    #    于是文案报「1 个超期」却列出 2 个 URL。⇒ 超期＝`probe=="ok" and within_sla is False`。
    #    📌 修一个「结论大于判据」的 bug 时，顺手又造了一个同族的 —— 说明这个错很好犯。
    res = rec.get("results") or []
    n_ok, n_out, n_unk = (rec.get("within_sla"), rec.get("out_of_sla"),
                          rec.get("not_probed"))
    if n_out is None and n_unk is None:
        # 旧格式记录没有聚合字段 ⇒ 没测到，不是没问题（家规：没查到 ≠ 没问题）
        return {"status": "unknown",
                "detail": f"{rec['date']} 这条记录没有聚合字段，无法判定全部探针"}
    # 🔑 点名的判据**逐字照抄** anchor_wayback.py:295-300 的分类三分支，不另写一遍：
    #       probe=="unknown" ⇒ not_probed；否则 within_sla 真 ⇒ within；假 ⇒ out_of_sla。
    #    ⚠️ 探针有三态 ok / none（确实无快照）/ unknown（没测到），**none 计进超期**。
    #       本次修闸连犯两回才对上：先写 `within_sla is False`（把 unknown 也算成超期，
    #       报 1 列 2），改成 `probe=="ok"` 又漏掉 none（报 2 列 1）。
    #       📌 可带走的：**要点名，就去读聚合是怎么数的，别照着样本猜**——
    #          猜出来的谓词在每一种没见过的状态上都会错，而错的方向是静默。
    def _is_stale(r):
        return r.get("probe") != "unknown" and not r.get("within_sla")

    def _save_note(rec):
        """🆕 2026-09-23：把「我们自己的提交成功了几个」缀在结论后面。

        🔴 这一条是本次事故的核心：匿名 save 对每个 URL 都返回 500，**一个都没成功**，
           而快照仍在更新（IA 自家爬虫顺手抓的）⇒ 从产物上完全看不出提交已经全废。
           `save_http` 字段一直忠实记着 500，**只是没有任何闸读它**。
           ⇒ 绿灯旁边必须带上这句，否则「见证靠运气」会继续隐形。
        """
        mode, ok, tried = (rec.get("save_mode"), rec.get("save_ok"), rec.get("save_tried"))
        if mode is None:
            return ""          # 建字段之前的旧记录，不替它断定
        if ok == 0 and tried:
            return (f"（⚠️ 但本轮 {tried} 次存档提交**一个都没成功**·模式 {mode}"
                    f"{'：匿名提交已失效，快照全靠 IA 爬虫运气，去配 IA 密钥' if mode == 'anon' else ''}）")
        return f"（本轮提交 {ok}/{tried} 成功·模式 {mode}）"

    def _names(pred):
        return "、".join(r.get("url", "?").replace("https://", "")[:52]
                         for r in res if pred(r)) or "（记录里没有逐条明细）"
    if (n_out or 0) > 0:
        return {"status": "bad", "age": age,
                "detail": (f"{rec['date']} 锚定：{n_out} 个存档超期"
                           f"（SLA 内 {n_ok} · 未测到 {n_unk}）—— 超期的是："
                           + _names(_is_stale))}
    if (n_unk or 0) > 0:
        return {"status": "unknown", "age": age,
                "detail": (f"{rec['date']} 锚定：{n_unk} 个未能查证（IA 限流）"
                           f"· SLA 内 {n_ok} —— 未测到的是："
                           + _names(lambda r: r.get("probe") == "unknown"))}
    return {"status": "ok", "age": age,
            "detail": (f"{rec['date']} 锚定正常，{n_ok} 个存档全部在 SLA 内"
                       + _save_note(rec))}


def check_snapshot_live() -> dict:
    """直接问 Internet Archive —— 不信任本地日志，独立复核一次。

    🔴 2026-08-06 重写（此项从建成起从未成功过一次，而没人发现）
    ━━ 病根：单探针，没有兜底 ━━
    旧实现只打 `__wb/sparkline`，而该端点已返回 498（实测多次确认）。
    `anchor_wayback.latest_snapshot` 早就有**两级探针**（sparkline → availability），
    所以它一直正常、每天记下真时间戳；本项自己另写了一份单级查询，就此永久失明。
    🔑 家规「一处判据只能有一个实现」—— 两份实现迟早分叉，这次分叉的代价是
       一个永远 unknown 的探测器，而 unknown 此前从不升级（见 escalate_if_stale）。
    ∴ 本项不再自己写查询，**直接复用那个已经带兜底的函数**（§26 先用现成做法）。

    ⚠️ 顺带记一次我自己的量尺错误：我曾用 `curl http://archive.org/wayback/available`
       实测得 HTTP 000，据此宣布「三个发现端点全死」。**协议错了** —— 同一端点走
       https 返回 200。判据：报「端点死了」之前，先确认自己用的 scheme 与代码里一致。
    """
    try:
        spec = importlib.util.spec_from_file_location(
            "_aw", str(ROOT / "scripts" / "anchor_wayback.py"))
        aw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(aw)
        st, snap = aw.latest_snapshot(SITE_CHAIN_URL, retries=2)
    except Exception as e:
        return {"status": "unknown", "detail": f"探针执行失败：{str(e)[:60]}"}
    if st == "unknown":
        return {"status": "unknown", "detail": "两级探针都没答上来（IA 限流或端点变动）"}
    if st == "none" or not snap:
        return {"status": "bad", "detail": "Internet Archive 上查不到链头的任何快照"}
    last = snap.get("timestamp")
    age = days_since(last)          # Wayback 时间戳本就是 UTC，这里刻意不换美东
    return {"status": "ok" if age is not None and age <= SLA["snapshot"] else "bad",
            "detail": f"链头最近快照 {last}（{age} 天前）", "age": age}


def check_daily_alive() -> dict:
    """看守看守人：daily 是不是已经不跑了。

    7·12→14 那次是 daily 整个死掉 4 天没人发现 —— 那种情况下，
    任何写在 daily 内部的告警都不会响。所以这一项必须从**产物**倒推，
    而不是从「有没有收到失败通知」倒推。
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", "data/kindex.json"],
            cwd=ROOT, capture_output=True, text=True, timeout=30).stdout.strip()
        # %cI 带时区偏移（CI 里是 +00:00）。**先换算到美东再取日期**：
        # 21:00 ET 的提交在 UTC 已是次日，直接切 [:10] 会让 age 变成负数。
        out = datetime.fromisoformat(out).astimezone(ET).strftime("%Y-%m-%d") if out else out
    except Exception as e:
        return {"status": "unknown", "detail": f"读 git 历史失败：{str(e)[:60]}"}
    if not out:
        return {"status": "unknown", "detail": "查不到 data/kindex.json 的提交记录"}
    age = days_since(out, et=True)
    return {"status": "ok" if age is not None and age <= SLA["daily_run"] else "bad",
            "detail": f"daily 最后一次更新数据 {out[:10]}（{age} 天前）", "age": age}


def check_archive_match() -> dict:
    """与第三方存档逐字对账 —— 唯一能挡住「全链重造」的一项（2026-08-05 渗透审计后加）。

    其余各项检查的都是「内部自洽」：攻击者拿到写权限后把整条链从创世重算一遍，
    verify_ledger / anchor_hashes / 快照新鲜度三项**全部放行**（实测推演）。
    只有把 Wayback 存的旧内容下下来逐字比，才会现形。
    Wayback 的内容攻击者改不了 —— 这就是「第三方见证」四个字真正兑现的地方。
    """
    try:
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_against_archive.py")],
                             cwd=ROOT, capture_output=True, text=True, timeout=180)
    except Exception as e:
        return {"status": "unknown", "detail": f"对账脚本执行失败：{str(e)[:60]}"}
    tail = [l for l in out.stdout.strip().splitlines() if l.strip()]
    last = tail[-1] if tail else ""
    if out.returncode != 0:
        return {"status": "bad", "detail": "🔴 已发布的历史与第三方存档不符 —— " + last[:90]}
    if "未能" in out.stdout or "没测到" in out.stdout:
        return {"status": "unknown", "detail": "本次未能取得存档（IA 限流）"}
    return {"status": "ok", "detail": last[:90]}


def check_options_page() -> dict:
    """期权页数据是否还在更新（2026-08-05 接线当天加）。

    ━━ 为什么这一项属于「见证链体检」而不是别处 ━━
    表面上它查的是一个页面的数据新鲜度，实际上它是**整条本机链路的唯一送达通道**。
    这份 JSON 由本机 launchd（`com.klay.eod-scan`）在收盘后生成并 push 上来，
    途中任何一环断掉都会让它停止更新：launchd 挂了 / 生成器抛异常 / 站仓 push
    凭据失效 / rebase 冲突留在本地。**这些全发生在 CI 之外，GitHub 一侧完全看不见。**
    ∴ 唯一能发现它们的办法就是从**产物**倒推 —— 与 `check_daily_alive` 同一手法：
    不问「有没有收到失败通知」（那要求失败方还活着），只问「东西多久没更新了」。
    🔑 家规：没有送达通道的告警等于没有告警。本项就是那条通道。

    判据用 `meta.data_date`（数据对应的交易日）而不是 `generated_at`：
    重跑一次旧数据会刷新 generated_at 却不代表页面变新了 —— **那正是最该被抓住的假绿**。

    ━━ 🔴 2026-08-14 补第二道：新鲜 ≠ 完整（Klay 令·第一性原理修）━━
    上面那道只问「多久没更新」。08-14 实证它抓不到另一种假绿：
    17:30 那班生成页面时官方收盘价还没落库 ⇒ 17 只票的 `spot_kind` 全是 `premarket`、
    `chg_pct` 全 null，**而 `data_date` 照样是当天** ⇒ 上面那道看一眼日期就报绿。
    随后 19:00 那班生成了正确版本却推不上去（自动 rebase 被一个纯噪音文件挡住），
    **站上整夜挂着半成品，而体检全绿。** 次日是周六、生成器不上班 ⇒ 本会挂满整个周末。

    🔑 第一性：`data_date` 回答的是「这页说的是**哪天**」，不是「这页**做完了吗**」。
       两者平时相关、出事时脱钩，而故障恰恰只发生在出事时。
       ⇒ 完整度必须由**事实字段**回答：`structure[].spot_kind`（close / intraday / premarket）。
    🚫 刻意**不读** `meta.provisional`：那是生成器给自己贴的标签，08-14 它就贴错过
       （页面确实是临时的，它却写 False）。**体检不该依赖被检查方的自我声明。**
       ——生成器侧已同步修成由事实推导，但这里仍只看原始事实，两处独立成立。
    """
    p = DATA / "options_page.json"
    if not p.exists():
        return {"status": "bad", "detail": "options_page.json 不存在 —— 期权页无数据可读"}
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
        meta = j.get("meta") or {}
    except Exception as e:
        return {"status": "bad", "detail": f"options_page.json 解析失败：{str(e)[:60]}"}
    d = meta.get("data_date")
    age = days_since(str(d), et=True) if d else None    # data_date 是美东交易日
    if age is None:
        return {"status": "unknown", "detail": "meta.data_date 缺失或无法解析"}

    # ── 第二道：完整度（读事实，不读标签）──
    stale = [t.get("ticker") for t in (j.get("structure") or [])
             if t.get("spot_kind") != "close"]
    if stale:
        now_et = datetime.now(ZoneInfo("America/New_York"))
        # 当日盘中/刚收盘 spot_kind 非 close 是**合法状态**，不报。
        # 判据取 18:30 ET：官方收盘价的结算窗口实测落在 17:3x–17:4x（08-13 17:41 / 08-14 17:31），
        # 留将近一小时余量 ⇒ 过了这个点还没收盘价，就不是"还没结算"，是"这一版没做完"。
        overdue = (age >= 1) or (age == 0 and (now_et.hour, now_et.minute) >= (18, 30))
        if overdue:
            return {"status": "bad", "age": age, "detail":
                    f"期权页 {d} 数据不完整：{len(stale)} 只票仍非官方收盘价"
                    f"（{', '.join(stale[:6])}{'…' if len(stale) > 6 else ''}）"
                    f" —— 多半是收盘价落库前那一班生成的版本卡在站上，"
                    f"看本机 eod-scan 日志有没有 push/rebase 未成功"}

    prov = "（盘中临时读数）" if meta.get("provisional") else ""
    return {"status": "ok" if age <= SLA["options_page"] else "bad",
            "detail": f"期权页数据 {d}{prov}（{age} 天前）", "age": age}


def check_daily_dispatched() -> dict:
    """daily 的**定时触发**有没有按工作日发生（2026-08-06 加）。

    🔑 为什么 `check_daily_alive` 挡不住这一类：它看的是**产物新鲜度**、SLA 4 天。
       2026-08-06 实况 —— daily 的 schedule 事件停在 08-05（那次还失败了），
       08-06 一发都没触发，而 check_daily_alive 读到「1 天前」照样报 ok
       （离报红还差 3 天）。**缺一天在它眼里是正常的，这是刻意的**：
       它当初为 7·12→14「daily 整个死掉 4 天」而建，且日历天数法在周一必然
       回看到周五（3 天），阈值收到 1 就会每周一误报。
    🔑 为什么本项不需要任何假日日历：cron 是 `0 22 * * 1-5`，**GitHub 不认市场假日**
       —— 工作日它一定触发，假日那天照跑、只是取不到新市场数据。
       ∴ 判据可以是纯粹的「上一个工作日有没有 run 记录」，不必判断那天开不开市。
       （这正是绕开「日历天 vs 交易日」那个两难的地方：换一个不需要日历的判据。）
    🔑 为什么只认 `event=schedule`：手动 `workflow_dispatch` 是人在救火，
       把它算进来会让「没人管也在跑」和「每天靠人补」看起来一样。
       2026-08-05/06 两次手动补跑正是如此 —— 算进来本项就会绿。
    """
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/klaywang24/fear-price/actions/"
            "workflows/daily.yml/runs?event=schedule&per_page=10",
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "witness-watchdog"})
        tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        with urllib.request.urlopen(req, timeout=25) as r:
            runs = json.loads(r.read()).get("workflow_runs", [])
    except Exception as e:
        return {"status": "unknown", "detail": f"查 Actions API 失败：{str(e)[:70]}"}
    if not runs:
        return {"status": "unknown", "detail": "查不到 daily.yml 的 schedule 运行记录"}

    last = max(datetime.fromisoformat(x["created_at"].replace("Z", "+00:00")).date()
               for x in runs)
    # 期望值＝最近一个「22:00 UTC 那一发早该发生」的工作日。
    # 留 1.5h 宽限（GitHub 排程实测常延迟 ~1h，历史上见过 23:00–23:10 才起）。
    now = datetime.now(timezone.utc)
    d = now.date()
    if not (now.hour >= 23 and now.minute >= 30) and not now.hour > 23:
        d -= timedelta(days=1)
    while d.isoweekday() > 5:            # 回退到最近的工作日
        d -= timedelta(days=1)
    lag = (d - last).days
    if last >= d:
        return {"status": "ok", "detail": f"daily 定时触发最近一次 {last}（期望 ≥{d}）"}
    return {"status": "bad",
            "detail": f"⛔ daily **定时触发**最近一次 {last}，而期望至少到 {d}"
                      f"（差 {lag} 天）—— 不是跑失败，是**压根没触发**，"
                      f"这种情况不发任何通知，沉默和正常长得一样。"
                      f"补跑：gh workflow run daily-update"}


CHECKS = {
    "链最后一行": check_chain_row,
    "锚定日志": check_anchor_log,
    "链头快照(直接问IA)": check_snapshot_live,
    "与存档逐字对账": check_archive_match,
    "daily是否还活着": check_daily_alive,
    "daily定时有没有触发": check_daily_dispatched,
    "期权页是否在更新": check_options_page,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--record", action="store_true",
                    help="把本次各项状态追加进 data/health_log.jsonl（只在 daily 用，供「上次成功」判据）")
    args = ap.parse_args()

    results = {name: escalate_if_stale(name, fn()) for name, fn in CHECKS.items()}
    bad = [n for n, r in results.items() if r["status"] == "bad"]
    unk = [n for n, r in results.items() if r["status"] == "unknown"]
    overall = "bad" if bad else ("unknown" if unk else "ok")

    if args.record:
        # 只记状态、不记 detail：detail 每天都不同，会让这份日志变成噪音
        rec = {"date": datetime.now(ET).strftime("%Y-%m-%d"),
               "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "overall": overall,
               "checks": {k: v["status"] for k, v in results.items()}}
        with open(HEALTH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")

    if args.json:
        print(json.dumps({"overall": overall, "bad": bad, "unknown": unk,
                          "checks": results}, ensure_ascii=False, indent=1))
    else:
        icon = {"ok": "✅", "bad": "🔴", "unknown": "❔"}
        for n, r in results.items():
            print(f"  {icon[r['status']]} {n:<20} {r['detail']}")
        print(f"\n总体：{icon[overall]} {overall}"
              + (f"（异常：{'、'.join(bad)}）" if bad else "")
              + (f"（未测到：{'、'.join(unk)}）" if unk else ""))
    return 1 if overall == "bad" else 0


if __name__ == "__main__":
    sys.exit(main())
