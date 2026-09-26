#!/usr/bin/env python3
"""每交易日收盘后，把「今日头版」数据卡片推送到 Discord #每日头版。
Webhook URL 从环境变量 DISCORD_WEBHOOK_URL 读取（GitHub Actions secret）；
未配置则静默跳过，不影响主管线。"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

SITE = "https://chronicle.klay-wang.com/"


def load(name):
    try:
        with open(f"data/{name}.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def pct(v):
    return ("+" if v > 0 else "") + f"{v:.2f}%"


def post(url, payload, tries=3, sleep=time.sleep):
    """发一条；Discord 429 按 retry_after 等了再发（2026-09-25：原先不接，连发几条告警撞 429 抛 HTTPError，
    后面的告警和日报卡全丢）。其余错误原样抛给 alert 去接。"""
    body = json.dumps(payload).encode()
    for i in range(tries):
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     # Cloudflare 会 403 掉默认的 python-urllib UA
                     "User-Agent": "Mozilla/5.0 (market-chronicle daily bot)"})
        try:
            urllib.request.urlopen(req, timeout=20)
            return
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1:
                raise
            try:
                wait = float(json.loads(e.read().decode() or "{}").get("retry_after", 2))
            except Exception:
                wait = 2.0
            sleep(min(max(wait, 0.5), 30))


def alert(url, title, desc):
    """管线出事时发红色告警。宁可吵，也不要静默——2026-07-12→14 就是被静默掉的。
    每条单独接住（2026-09-25）：一条发不出去不许连累后面的告警与日报卡。"""
    try:
        post(url, {"embeds": [{"title": title, "description": desc, "color": 0xD93025}]})
        print(f"已发告警: {title}")
    except Exception as e:  # noqa: BLE001
        print(f"告警发送失败（{type(e).__name__}: {e}）：{title}", file=sys.stderr)


def main():
    url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not url:
        print("DISCORD_WEBHOOK_URL 未配置，跳过 Discord 推送")
        return

    run_url = os.environ.get("RUN_URL", "")
    status = (os.environ.get("JOB_STATUS") or "").lower()

    # ① job 本身失败 → 立刻红色告警，绝不发正常播报（旧 JSON 会让它看起来一切正常）
    if status and status != "success":
        # 2026-09-25：站点 HTML 检查挪到了数据提交之后 ⇒ job 失败不再等于数据没更新；按提交步骤留的标记分开说
        if os.environ.get("DATA_COMMITTED") == "1":
            alert(url, f"🟠 daily-update 部分失败（{status}）",
                  f"今天的数据**已更新并提交**；之后的步骤（站点检查／存档／见证）有失败，站点代码或存档需要看一眼。\n日志：{run_url}")
        else:
            alert(url, f"🔴 daily-update 失败（{status}）",
                  f"站上数据**没有更新**，仍是上一次成功时的值。\n请查日志：{run_url}")
        sys.exit(0)

    d = load("pulse")
    if not d:
        alert(url, "🔴 daily-update 异常：pulse.json 缺失", f"日志：{run_url}")
        return

    # ② job 成功但数据没动（上游静默返回旧值/被限流）→ 同样告警
    asof = d.get("date")
    if asof:
        try:
            age = (dt.date.today() - dt.date.fromisoformat(asof)).days
            if age > 4:   # 容忍周末 + 假日；超过就不正常
                alert(url, f"🟠 数据陈旧：pulse.json 停在 {asof}（{age} 天前）",
                      f"job 报成功但数据没前进，检查上游取数。\n日志：{run_url}")
        except Exception:
            pass

    # ③ 台账自核：已发布过的值被改写、或交易日消失 → 告警。
    #    ⚠️ 触发判据取自 verify_ledger 步骤的 env 输出，不取磁盘文件——文件可能是上一次的，
    #    「读旧文件发平安播报」正是 2026-07-12 那次故障被静默掉的形态。
    #    按「相比上次的新增」响，不按绝对数：存量分歧天天叫 = 狼来了。
    #    检查器看不见的时候必须承认看不见（浅克隆=历史不全=数字不作数），绝不发平安播报。
    if os.environ.get("LEDGER_RELIABLE") == "false":
        alert(url, "🔴 台账自核失效：仓库历史不全",
              "verify_ledger 跑在浅克隆上，看不到历史 commit，**本次自核数字不作数**。\n"
              f"检查 daily.yml 的 checkout 是否有 `fetch-depth: 0`。\n日志：{run_url}")

    delta_raw = os.environ.get("LEDGER_DELTA", "")
    if delta_raw not in ("", "0"):
        au = load("ledger_audit") or {}
        total = au.get("total_divergences", "?")
        lines = []
        for r in au.get("reports", []):
            c = r.get("counts") or {}
            if c.get("total"):
                lines.append(f'· {r.get("file")}：消失 {c.get("missing", 0)} / '
                             f'当场记录被改 {c.get("revised_live", 0)} / '
                             f'回填段被改 {c.get("revised_backfill", 0)}')
        alert(url, f"🔴 台账自核：分歧数较上次变化 {delta_raw}（现共 {total} 处）",
              "**已发布过的数值被改写，或有交易日消失。**\n" + "\n".join(lines)
              + f"\n\n逐条明细：data/ledger_audit.json\n日志：{run_url}")

    # ③b 口径自检（2026-07-25 新增）：当日头条读数用参考实现独立重算一遍。
    #    verify_ledger 管「发布过的值有没有被改」，这条管「今天发布的值是不是仍由
    #    同一把尺算出」——取数漂移/窗口污染/代码漂移三种翻车都在这里现形。
    #    触发判据取 env（同③的理由：不读旧文件发平安播报）；明细读本次刚写的文件。
    if os.environ.get("GAUGE_MATH_OK") == "false":
        gm = load("gauge_math") or {}
        lines = [f'· {m.get("field")} @ {m.get("date")}：存档 {m.get("stored")} vs '
                 f'重算 {m.get("recomputed")}（差 {m.get("diff")}）'
                 for m in (gm.get("mismatches") or [])[:6]]
        lines += [f'· 完整性：{p.get("kind")}：{p.get("detail")}'
                  for p in (gm.get("integrity_problems") or [])[:4]]
        alert(url, "🔴 恐惧的标价：口径自检失败（当日读数与参考实现对不上）",
              "**今天发布的头条读数，用同一把尺独立重算对不上了。**\n"
              "可能：当日取数进了脏值 / 序列窗口被污染 / 计算口径被改动。\n"
              "**发 digest / 出卡前先人工核对。**\n"
              + "\n".join(lines)
              + f"\n\n明细：data/gauge_math.json\n日志：{run_url}")

    # ④ 非致命小节失败（2026-07-19 新增）：build_data 的 _guard 把失败写进 meta.json。
    #    此前这些失败只 print 进 Actions 日志，而没有人每天读日志——2026-07-12→14 静默死
    #    4 天就是这个形态。job 整体仍是 success，所以①②③都不会响，必须单独看这个字段。
    meta = load("meta") or {}
    # ⑤ 见证链体检（2026-08-04 加）：锚定/哈希链/daily 存活四项里有 bad 就报。
    #    判据取 env 不读磁盘文件 —— 同③的理由：文件可能是上一次的，会发平安播报。
    #    ⚠️ 这条只是「当天可见」；真正兜底的是独立的 witness-watchdog.yml（开 Issue）。
    if os.environ.get("WITNESS_HEALTH") == "bad":
        alert(url, "🔴 见证链体检未通过",
              "锚定 / 哈希链 / daily 存活 四项中有异常。\n"
              "**这不影响读者看到的东西，只影响「证明没被改过」那套机制** —— "
              "所以坏了只能靠这条告警发现。\n"
              f"本地复现：`python3 scripts/check_witness_health.py`\n日志：{run_url}")

    failures = meta.get("failures") or []
    if failures:
        lines = [f'· **{f.get("section")}**：{f.get("error")}' for f in failures]
        alert(url, f"🟠 {len(failures)} 个数据小节失败（站上仍是旧值）",
              "job 报成功，但下列小节这次没跑成，站上对应板块还是上一次的数据：\n"
              + "\n".join(lines) + f"\n\n日志：{run_url}")

    # ⑤ 恐惧的标价降级（2026-07-19 新增）：这是唯一在赚钱的读数，走备胎或数据陈旧都要当天知道。
    #    degraded=true 有两种：headline_source=banked_only（Cboe+Yahoo 双挂，只剩本地存底）
    #    或 stale_days>4（源还在但没前进）。两种都意味着卡片与 digest 不该照常发。
    lg_meta = (load("leaps_gauge") or {}).get("meta") or {}
    if lg_meta.get("degraded"):
        alert(url, "🔴 恐惧的标价：数据降级",
              f'来源=**{lg_meta.get("headline_source")}** · 数据停在 '
              f'**{lg_meta.get("stale_days")}** 天前。\n'
              "**这是付费产品的头条读数**，发 digest / 出卡前先确认。\n"
              f"日志：{run_url}")
    elif lg_meta.get("headline_source") == "yahoo_fallback":
        # 没到降级线但已经在吃备胎 = 早期信号，Cboe 那边出事了
        alert(url, "🟠 恐惧的标价：正在走 Yahoo 备胎",
              "Cboe 主源这次没拉到，已自动切 Yahoo（口径实测一致，读数可信）。\n"
              "但备胎只有最近 1 个交易日、没有缓冲：**若管线漏跑一天，那天补不回来**。\n"
              f"请查 Cboe 侧是否恢复。日志：{run_url}")
    if lg_meta.get("reconcile_warning"):
        rw = lg_meta["reconcile_warning"]
        alert(url, "🟠 恐惧的标价：Cboe 与 Yahoo 对不上",
              f'{rw.get("date")} 当天 Cboe=**{rw.get("cboe")}** vs Yahoo=**{rw.get("yahoo")}**'
              f'（差 {rw.get("diff")}）。\n两源本应一致，对不上意味着**其中一个可能给了错数**——'
              "Cboe 没报错所以备胎没触发，需人工判断哪个对。\n"
              f"日志：{run_url}")
    if lg_meta.get("revisions"):
        n = len(lg_meta["revisions"])
        alert(url, f"🟠 恐惧的标价：上游修订了 {n} 个历史值",
              "按修订政策，**台账旧行保持原样未动**，分歧已记入 "
              "`leaps_gauge.json` 的 `meta.revisions` 公开。\n"
              f"逐条：{json.dumps(lg_meta['revisions'][:5], ensure_ascii=False)}\n日志：{run_url}")

    # AIAE 自检（2026-07-20 新增）：值落在 0.30~0.60 合理区间外 = FRED series ID 可能有误。
    #   前端未上线前靠这个 + 人工核 CI 输出把关，绝不让错数据上站。
    aiae_meta = (load("aiae") or {}).get("meta") or {}
    if aiae_meta and not aiae_meta.get("sane_check", True):
        alert(url, "🔴 AIAE 自检失败：值落在合理区间外",
              f'当前值 **{aiae_meta.get("current_value")}**（应在 0.30~0.60），'
              "FRED series ID 或单位可能有误——**前端勿上线，先核对**。\n"
              f"as-of {aiae_meta.get('asof')} · 日志：{run_url}")

    leaps = load("leaps") or {}
    q = d.get("quotes", {})

    temp = d.get("temp", 0)
    word = "炙热" if temp >= 75 else "偏暖" if temp >= 50 else "温和" if temp >= 25 else "冰点"
    color = 0xB8421E if temp >= 75 else 0xC9882E if temp >= 50 else 0x14A63E if temp >= 25 else 0x2B5F8F
    win_open = (leaps.get("current") or {}).get("window_open")

    def quote(label, key):
        v = q.get(key)
        return f"{label} **{round(v['close']):,}** {pct(v['chg'])}" if v else ""

    indices = " · ".join(filter(None, [
        quote("标普500", "spx"), quote("纳指100", "ndx"),
        quote("道指", "dji"), quote("罗素2000", "rut"),
    ]))
    fields = [
        {"name": "🌡️ 市场温度", "value": f"**{temp:.1f}°** · {word}（估值 {d.get('val_pct')} 百分位 / 情绪 {d.get('sent_pct')} 百分位）", "inline": False},
        {"name": "📈 四大指数", "value": indices or "—", "inline": False},
        {"name": "😱 情绪", "value": f"恐惧贪婪 **{round(d.get('fng', 0))}** · VIX **{q.get('vix', {}).get('close', '—')}** · K 指数 **{d.get('k', 0):.2f}**", "inline": False},
        {"name": "🎯 LEAPS Call 窗口", "value": ("**开启** 🟢" if win_open else "关闭"), "inline": True},
        {"name": "📊 涨跌", "value": f"{d.get('adv')} 涨 / {d.get('flat')} 平 / {d.get('dec')} 跌", "inline": True},
    ]
    payload = {
        "username": "恐惧的标价",
        "embeds": [{
            "title": f"今日头版 · {d.get('date')}",
            "url": SITE,
            "color": color,
            "fields": fields,
            "footer": {"text": "数据仅供参考，非投资建议 · Fear-Price"},
        }],
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            # Discord/Cloudflare 会 403 掉默认的 python-urllib UA，需带正常 UA
            "User-Agent": "FearPrice-Bot/1.0 (+https://chronicle.klay-wang.com/)",
        }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print("Discord 推送成功", r.status)
    except Exception as e:
        print("Discord 推送失败：", e, file=sys.stderr)
        # 不让主管线失败
        return


if __name__ == "__main__":
    main()
