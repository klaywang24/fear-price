#!/usr/bin/env python3
"""生成「见证链看板」HTML（2026-08-04 新建，对标 访客数据/访客看板.html 的用法）。

Klay 的用法：跑一次生成，之后**每天双击打开就行** —— 页面在打开时自己去
GitHub raw 拉最新数据（raw.githubusercontent.com 回 Access-Control-Allow-Origin: *，
已实测），所以不需要再跑脚本、也不依赖本地仓库是不是最新。

🔑 三条设计（都从今晚的教训来）：
  1. **三档显示 ok / bad / unknown**。拉不到数据显示「没查到」，
     绝不显示成「正常」—— 家规 §46：报「测量失败」和报绿一样危险。
  2. **不信任任何单一来源**。链头快照那项给的是「去 Wayback 自己看」的直达链接，
     因为浏览器跨域查不了 IA，而**假装查过比不查更糟**。
  3. 页面上写死「现在该做什么」，红了不用回来问。

输出：见证链看板.html（已进 .gitignore，是本地工具不是站上内容 ——
      运维状态公开挂着，红的时候会变成别人的弹药）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 默认写到**项目文件夹**（仓库外，和 访客数据/访客看板.html 同一层级的用法）：
#   …/恐惧的标价：market-chronicle/见证链看板.html
# 放仓外而不是仓内，是因为它是本地运维工具 —— 运维状态公开挂着，
# 红的时候会变成别人的弹药。仓内那份的 .gitignore 条目保留作兜底。
PROJECT = ROOT.parent.parent                     # KAPX/ 的上一层
OUT = (PROJECT / "见证链看板.html") if PROJECT.is_dir() else (ROOT / "见证链看板.html")
RAW = "https://raw.githubusercontent.com/klaywang24/fear-price/main"
REPO = "https://github.com/klaywang24/fear-price"

HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>见证链看板 · 恐惧的标价</title>
<style>
:root{--bg:#FAF8F3;--card:#fff;--ink:#1a1a1a;--soft:#5a5a5a;--muted:#8a8a8a;
      --line:#e5e0d8;--ok:#00A86B;--bad:#FF2400;--unk:#B8893E}
*{box-sizing:border-box}
body{margin:0;padding:28px 20px 60px;background:var(--bg);color:var(--ink);
     font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Noto Sans CJK SC",sans-serif}
.wrap{max-width:860px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.3px}
.sub{color:var(--soft);font-size:13.5px;margin:0 0 4px}
.ts{color:var(--muted);font-size:12px;margin:0 0 24px}
.big{background:var(--card);border:1px solid var(--line);border-radius:14px;
     padding:22px;margin-bottom:18px;display:flex;align-items:center;gap:18px}
.dot{width:46px;height:46px;border-radius:50%;flex:0 0 46px;display:flex;
     align-items:center;justify-content:center;font-size:24px;color:#fff}
.big h2{margin:0 0 3px;font-size:19px}.big p{margin:0;color:var(--soft);font-size:13.5px}
.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-bottom:22px}
@media(max-width:660px){.grid{grid-template-columns:1fr}}
.c{background:var(--card);border:1px solid var(--line);border-left-width:4px;
   border-radius:10px;padding:14px 16px}
.c h3{margin:0 0 4px;font-size:14px;display:flex;justify-content:space-between;gap:8px}
.c .d{color:var(--soft);font-size:12.5px;word-break:break-all}
.c .w{color:var(--muted);font-size:11.5px;margin-top:6px;font-style:normal}
h4{font-size:15px;margin:26px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--ink)}
table{width:100%;border-collapse:collapse;font-size:12.5px;background:var(--card);
      border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--line)}
th{background:#f3efe7;font-weight:600;font-size:11.5px;color:var(--soft)}
tr:last-child td{border-bottom:none}
code{background:#f3efe7;padding:1.5px 5px;border-radius:4px;font-size:12px;
     font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
pre{background:#2b2b2b;color:#eee;padding:12px 14px;border-radius:8px;overflow-x:auto;
    font-size:12px;line-height:1.5}
.steps{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:4px 18px}
.steps li{margin:11px 0;font-size:13.5px}.steps b{color:var(--ink)}
a{color:var(--bad)}
.note{font-size:12.5px;color:var(--muted);margin-top:10px}
</style></head><body><div class="wrap">

<h1>见证链看板</h1>
<p class="sub">这一页只回答一件事：<b>「证明台账没被改过」的那套机制，现在还在正常工作吗。</b>
它坏了不会影响读者看到的任何东西，所以只能靠主动看。</p>
<p class="ts">页面打开时自动拉取最新数据 · 本次加载 <span id="now"></span></p>

<div class="big" id="big">
  <div class="dot" style="background:var(--muted)">…</div>
  <div><h2 id="bigT">正在检查…</h2><p id="bigD">从 GitHub 拉取台账与锚定记录</p></div>
</div>

<div class="grid" id="cards"></div>

<h4>最近的锚定记录</h4>
<div id="hist"><p class="note">读取中…</p></div>

<h4>红了怎么办</h4>
<ol class="steps">
  <li><b>先本地复现一次</b>，看到底哪一项坏了：
    <pre>cd &lt;本仓根目录&gt; &amp;&amp; git pull -q &amp;&amp; python3 scripts/check_witness_health.py</pre></li>
  <li><b>「锚定日志」红</b> = 存档结果没被留档。看
    <a href="REPO_URL/actions/workflows/daily.yml" target="_blank">daily 的运行记录</a>，
    锚定那步是不是失败了。</li>
  <li><b>「链头快照」红</b> = Internet Archive 上没有近期快照。手动补一次：
    <pre>python3 scripts/anchor_wayback.py --sha "$(git rev-parse HEAD)"</pre></li>
  <li><b>「daily 是否还活着」红</b> = 自动任务本身停了（<b>最严重</b>，2026-07-12 那次就是它，
    死了 4 天没人发现）。去
    <a href="REPO_URL/actions" target="_blank">Actions 页</a> 看是不是被禁用了
    ——GitHub 会在仓库长期无活动后自动停掉定时任务。</li>
  <li><b>全是 ❔</b> = 没查到，不是坏了。多半是网络或 GitHub raw 抽风，过一会儿再刷新。</li>
</ol>

<h4>三层报警是怎么跑的</h4>
<table>
<tr><th>层</th><th>什么时候跑</th><th>怎么通知你</th><th>管什么</th></tr>
<tr><td><b>①本页</b></td><td>你打开时</td><td>你自己看</td><td>随时想查就查</td></tr>
<tr><td><b>②daily 内</b></td><td>排定 18:00 美东<br><span style="color:var(--muted)">cron 只入队不准时，实测起跑 19:5x–次日 02:00</span></td><td>Discord 推送</td><td>当天出问题当天知道</td></tr>
<tr><td><b>③看门狗</b></td><td>排定 09:00 美东（独立任务）<br><span style="color:var(--muted)">cron 只入队不准时：08-26 前延迟 &lt;1h，<b>08-27 起实测 12:5x–18:5x（迟 4–10h）</b></span></td><td><b>开 GitHub Issue<br>→ 自动发邮件</b></td>
    <td><b>连 daily 自己死了也能报</b><br>②在 daily 里面，daily 死了它也没了</td></tr>
</table>
<p class="note">③ 恢复后会自动关闭 Issue，免得旧告警一直挂着让人麻木。
三样通知一个都没有、本页又是绿的，才叫真的正常。</p>

<script>
/*WITNESS_VERDICT_JS*/
const RAW="RAW_URL";   // REPO 常量 2026-09-03 删：声明了从没用过
const ICON={ok:"✓",bad:"!",unknown:"?"}, COLOR={ok:"var(--ok)",bad:"var(--bad)",unknown:"var(--unk)"};
const SLA={chain:4, anchor:4, daily:4, opt:4};   // opt=期权页，与 check_witness_health 同值（snap 是 manual 卡，无判据键）
// 🔴 2026-09-03 修（Klay 令）：原来拿 `Date.now()`（**浏览器本地时区**）去减一个
//    按 UTC 午夜解析的日期。Klay 人在中国，北京比美东快 12 小时 ⇒ 同一份数据
//    在北京早晨会被算成多 1 天；而期权卡的 `a>=1` 是**硬边界** ⇒ 北京早晨打开看到**假红**。
//    （站侧 check_witness_health 用的是 days_since(..., et=True)，一直是对的 —— 又一处两把尺子。）
//    改法：两边都归一到**美东日历日**再相减，浏览器在哪个时区都得同一个数。
//    🔑 假红不是"偏保守"，它和假绿一样会养出麻木——只是死法不同。
const etDay=d=>new Intl.DateTimeFormat("en-CA",{timeZone:"America/New_York",
  year:"numeric",month:"2-digit",day:"2-digit"}).format(d);          // → "YYYY-MM-DD"
const days=s=>{if(!s)return null;
  const iso=s.length>8?s.slice(0,10):s.slice(0,4)+"-"+s.slice(4,6)+"-"+s.slice(6,8);
  const t=Date.parse(iso+"T00:00:00Z"), n=Date.parse(etDay(new Date())+"T00:00:00Z");
  return (isNaN(t)||isNaN(n))?null:Math.round((n-t)/864e5)};
// 🔴 2026-08-26 修：days() 可能返回 null，而 null<=SLA 在 JS 里是 true ⇒ 日期字段一坏就假绿。
//    所有「新鲜度」判据一律走这个函数：解析不出日期 = unknown，不是 ok。没查到 ≠ 没问题。
// 🚨 2026-09-03 回填：这三行此前**只改在产物 HTML 上、没回生成器**（生成器停在 08-18）——
//    任何人重跑一次生成器就会把它们整体抹掉、假绿复活。**改产物不改生成器＝装了一颗定时雷。**
const stat=(a,sla)=>a===null?"unknown":(a<=sla?"ok":"bad");
const ago=a=>a===null?"日期未解析":`${a} 天前`;

async function txt(u){const r=await fetch(u,{cache:"no-store"});if(!r.ok)throw 0;return r.text()}
async function lastJsonl(u){const t=await txt(u);const L=t.trim().split("\\n").filter(Boolean);
  // 2026-09-03：只解析尾部 60 行——历史表只看最近 10 条，全量 parse 会随日志线性变慢
  return {last:JSON.parse(L[L.length-1]), all:L.slice(-60).map(x=>JSON.parse(x))}}

function card(name,st,detail,why){
  return `<div class="c" style="border-left-color:${COLOR[st]}">
    <h3><span>${name}</span><span style="color:${COLOR[st]}">${ICON[st]}</span></h3>
    <div class="d">${detail}</div><div class="w">${why}</div></div>`}

(async()=>{
  document.getElementById("now").textContent=new Date().toLocaleString("zh-CN");
  const out=[];

  // ① 链最后一行
  try{const {last}=await lastJsonl(RAW+"/data/ledger_hashes.jsonl");const a=days(last.date);
    out.push({n:"链最后一行",s:stat(a,SLA.chain),
      d:`${last.date}（${ago(a)}）· 链头 <code>${last.chain.slice(0,16)}…</code>`,
      w:"数据本身还在不在逐日入链"});
  }catch(e){out.push({n:"链最后一行",s:"unknown",d:"拉取失败",w:"没查到 ≠ 没问题"})}

  // ② 锚定日志
  //    🔴 2026-09-03 修（Klay 令）：判据从**抽样**改成**聚合**。
  //    病：原来只读 `results[0]`（一条记录里 5~9 个探针的第 1 个），结论却挂在整张卡上；
  //        而每条记录**顶层现成就有** within_sla / out_of_sla / not_probed 三个聚合数。
  //    🔬 实测代价：31 条记录里 **20 天** out_of_sla>0 或 not_probed>0，其中 **18 天**
  //        results[0] 恰好正常 ⇒ **卡片报绿**（最近：09-01 超期 2、08-28 超期 2）。
  //        而同一页下面的历史表把「超期 2」直接印了出来 —— **证据与绿灯同框**，
  //        和 08-18 期权卡那次是同一个病的第二个发病部位，隔了半个月没人发现。
  //    🔑 第一性：**结论的覆盖面不许大于判据的覆盖面。** 量了 1 个探针就只能说这 1 个。
  //        推论：**绿灯的文案必须逐字等于判据** —— 人只会读那句话，不会去读代码。
  //    🔴 2026-09-23 再修（Klay 令「看板也接上」）：判据不再内联在这里，
  //        改调 scripts/witness_verdict.js（生成器内联进本页），与 CI 的 python 版共读
  //        scripts/witness_fixtures.json —— 两个实现、一份样本、改判据先改样本。
  //        同时带了矛盾检测：明细重算 vs 顶层聚合，不一致本身就是红。
  try{const {last,all}=await lastJsonl(RAW+"/data/anchor_log.jsonl");const a=days(last.date);
    const v=witnessVerdict.anchorVerdict(last,a,SLA.anchor);
    out.push({n:"锚定日志",s:v.status,d:`${ago(a)} · ${v.detail}`,
      w:"存档结果有没有被留档 —— 判据＝该条记录的**全部**探针（与 CI 同源同样本，含明细/聚合矛盾检测）"});
    window.__hist=all;
  }catch(e){out.push({n:"锚定日志",s:"unknown",
      d:"anchor_log.jsonl 取不到",
      w:"🚫 不替它断定是哪一种：原文案写死「文件还不存在」，而任何一次网络抖动都会走到这里——"
        +"那句话本闸从没验证过。取不到就是取不到（家规：没查到 ≠ 没问题）"})}

  // ③ 链头快照 —— 浏览器跨域查不了 IA，给直达链接，不假装查过。
  //    manual:true = 不计入总体状态（2026-08-05 修：它永远是 ❔，算进去的话
  //    横幅永远显示「部分未查到」，绿灯永远够不着 —— 永远黄的横幅=没有横幅）
  out.push({n:"链头快照（去 Wayback 自己看）",s:"unknown",manual:true,
    d:`<a href="https://web.archive.org/web/2026*/chronicle.klay-wang.com/data/ledger_hashes.jsonl" target="_blank">点这里看日历 →</a> 最近一格应在 3 天内`,
    w:"浏览器跨域查不了 Internet Archive；假装查过比不查更糟。CI 每天替你查并记进②"});

  // ④ daily 是否还活着
  try{const r=await fetch("https://api.github.com/repos/klaywang24/fear-price/commits?path=data/kindex.json&per_page=1",{cache:"no-store"});
    const j=await r.json();const dt=j[0].commit.committer.date;const a=days(dt);
    out.push({n:"daily 是否还活着",s:stat(a,SLA.daily),
      d:`最后一次更新数据 ${dt.slice(0,10)}（${ago(a)}）`,
      w:"最严重的一项：2026-07-12 那次 daily 整个死掉 4 天没人发现"});
  }catch(e){out.push({n:"daily 是否还活着",s:"unknown",d:"GitHub API 拉取失败",w:"没查到 ≠ 没问题"})}

  // ⑤ 期权页是否在更新（2026-08-05 晚新增；2026-08-18 补上第二道）
  //    这一项查的是**本机那条链路**：launchd → 生成器 → push 站仓。
  //    整段在 CI 之外，GitHub 一侧看不见；任何一环断掉都只表现为「页面停止更新」。
  //
  //    🔴 2026-08-18 修：本段注释从建成起就写着「与体检第六项同判据」，而它其实只抄了
  //       第一道。08-18 凌晨实况 —— 线上 08-17 那版 17 只票全是盘中读数，
  //       check_witness_health 判 bad，本卡片却是绿勾、旁边还并排印着「· 盘中临时读数」：
  //       证据与绿灯同框。总体横幅因此显示「一切正常」。
  //       🔑 一处判据只能有一个实现。两套尺子并存时，人看到的永远是松的那把 ——
  //          而松的那把恰恰装在人唯一会打开的那个页面上。
  //
  //    🔑 第一道（在不在更新）用 meta.data_date 不用 generated_at —— 重跑旧数据会刷新
  //       后者却不代表页面变新，那正是最该被抓住的假绿。
  //    🔑 第二道（做完没有）读 structure[].spot_kind 这个**事实字段**，
  //       🚫 刻意**不读** meta.provisional：那是生成器给自己贴的标签，08-14 贴错过
  //       （页面确实是临时的，它却写 False）。**体检不该依赖被检查方的自我声明。**
  //       此处与 check_witness_health.check_options_page 各自独立成立，不是互抄。
  //    🔑 18:30 ET 这个点：官方收盘价结算实测落在 17:3x–17:4x（08-13 17:41 / 08-14 17:31），
  //       留将近一小时余量 ⇒ 过了还没收盘价，就不是「还没结算」，是「这一版没做完」。
  //       当天盘中 spot_kind 非 close 是**合法状态**，不报。
  try{const r=await fetch("https://chronicle.klay-wang.com/data/options_page.json",{cache:"no-store"});
    const j=await r.json(), m=j.meta||{}, a=days(m.data_date);
    const stale=(j.structure||[]).filter(t=>t.spot_kind!=="close").map(t=>t.ticker);
    const et=new Date(new Date().toLocaleString("en-US",{timeZone:"America/New_York"}));
    const overdue=stale.length>0 && (a>=1 || (a===0 && et.getHours()*60+et.getMinutes()>=18*60+30));
    out.push(overdue
      ? {n:"期权页是否在更新",s:"bad",
         d:`期权页 ${m.data_date} 数据不完整：${stale.length} 只票仍非官方收盘价（${stale.slice(0,6).join(", ")}${stale.length>6?"…":""}）`,
         w:"多半是收盘价落库前那一班生成的版本卡在站上 —— 看本机 eod-scan 日志有没有 push/rebase 未成功"}
      : {n:"期权页是否在更新",s:stat(a,SLA.opt),
         d:`数据日 ${m.data_date}（${ago(a)}）${stale.length?" · 盘中临时读数":""} · ${m.n_tickers} 只标的`,
         w:"本机 launchd 那条链路的唯一体温计：它停了说明定时任务/生成器/推送里有一环断了"});
  }catch(e){out.push({n:"期权页是否在更新",s:"unknown",d:"拉取失败",w:"没查到 ≠ 没问题"})}

  // ⑦ CI 体检末条（2026-09-03 加·Klay 令「按第一性原理」）
  //    看板此前缺 CI 的两项：「daily 定时有没有触发」和「unknown 连续 N 天升级为 bad（stale_ok）」。
  //    🔑 第一性：问题不是"看板少两个判据"，是**「CI 已经算出的结论，人唯一会打开的页面上看不见」**。
  //       CI 在写 health_log 之前就做了 stale_ok 升级（check_witness_health 第 393 行），
  //       daily 没触发则 health_log 根本不会有新行 —— 两件事 CI 那把尺子都量了，只是没人看。
  //    ⇒ 不在 JS 里再写一遍那两条判据（那是第二把尺子，必漂），**原样搬 CI 的末条结论**。
  //       本页自己的 ①②④⑤ 是独立的第二尺，本卡是 CI 那把尺——两把尺子并排，不互抄。
  //    🔑 本卡的**新鲜度本身**就是「daily 有没有跑」的信号：CI 不跑，这条日志就不会长。
  try{const {last}=await lastJsonl(RAW+"/data/health_log.jsonl");const a=days(last.date);
    const ch=last.checks||{};
    const bads=Object.entries(ch).filter(([,v])=>v==="bad").map(([k])=>k);
    const unks=Object.entries(ch).filter(([,v])=>v==="unknown").map(([k])=>k);
    let s="ok",d=`${last.date}（${ago(a)}）· CI 判 <code>${last.overall||"?"}</code>`;
    if(a===null){s="unknown";d+=" · 记录里的日期解析不出来"}
    else if(a>SLA.daily){s="bad";d+=` · <b>CI 已 ${a} 天没记体检</b> —— daily 没跑或压根没触发，<b>这是 CI 自己报不出的那种沉默</b>`}
    else if(bads.length){s="bad";d+=` · 红：<b>${bads.join("、")}</b>（含连续 5 天未测到升级来的）`}
    else if(unks.length){s="unknown";d+=` · 未测到：${unks.join("、")}（未满 5 天，CI 尚未升级）`}
    else{d+=` · ${Object.keys(ch).length} 项全过`}
    out.push({n:"CI 体检末条（7 项·含 stale_ok 升级）",s,d,
      w:"CI 那把尺子的原样结论（含「daily 定时有没有触发」与「连续 5 天未测到⇒红」）。本卡不新鲜＝daily 没在跑"});
  }catch(e){out.push({n:"CI 体检末条（7 项·含 stale_ok 升级）",s:"unknown",d:"health_log.jsonl 取不到",w:"没查到 ≠ 没问题"})}

  // ⑥ 与存档逐字对账 —— 浏览器做不了（跨域 + 要下整份存档字节），给命令，不假装查过。
  //    manual:true ⇒ 不计入总体状态，理由同③（永远黄的横幅等于没有横幅）。
  out.push({n:"与存档逐字对账（要跑命令）",s:"unknown",manual:true,
    d:`<code>python3 scripts/verify_against_archive.py</code>`,
    w:"唯一能挡住「全链重造」的一项：把 Wayback 存的旧内容下下来逐字比。浏览器跨域做不到，CI 每天替你跑并计入体检"});

  document.getElementById("cards").innerHTML=out.map(o=>card(o.n,o.s,o.d,o.w)).join("");

  const auto=out.filter(o=>!o.manual);
  const bad=auto.filter(o=>o.s==="bad"), unk=auto.filter(o=>o.s==="unknown");
  const st=bad.length?"bad":(unk.length?"unknown":"ok");
  // 2026-09-03 改：原写「一切正常」——但只查了自动那几项，2 项手动的从没被检查过。
  //    文案不许大于判据：查了几项就说几项。
  const T={ok:["自动体检全绿",`${auto.length} 项自动体检全绿。另有 ${out.length-auto.length} 项**未查**（要手动跑，卡片里有命令/链接）——它们不在这个绿灯的覆盖面里。`],
           bad:[`${bad.length} 项异常`,"见证链有问题，往下看「红了怎么办」。异常："+bad.map(b=>b.n).join("、")],
           unknown:["部分未查到","不是故障，但本次结果不完整："+unk.map(b=>b.n).join("、")]}[st];
  const big=document.getElementById("big");
  big.querySelector(".dot").style.background=COLOR[st];
  big.querySelector(".dot").textContent=ICON[st];
  document.getElementById("bigT").textContent=T[0];
  document.getElementById("bigD").textContent=T[1];

  const H=window.__hist;
  document.getElementById("hist").innerHTML = H&&H.length
    ? `<table><tr><th>日期</th><th>SLA 内</th><th>超期</th><th>没测到</th><th>链头快照</th></tr>`
      + (()=>{ // 2026-09-03：同一天跑多次会有多条记录（08-06/08-25/08-26/08-28 各两条），
               //   卡片读的是最后一条，表也只留最后一条并标 ×N，别让人以为是两天
               const byDay=new Map(); for(const r of H){const p=byDay.get(r.date); byDay.set(r.date,{r,n:(p?p.n:0)+1})}
               return [...byDay.values()].slice(-10).reverse(); })().map(({r,n})=>{const h=(r.results||[])[0]||{};
          return `<tr><td>${r.date}${n>1?` <span style="color:var(--muted)">×${n}</span>`:""}</td><td>${r.within_sla??"-"}</td><td>${r.out_of_sla??"-"}</td>
          <td>${r.not_probed??"-"}</td><td><code>${h.timestamp||"-"}</code></td></tr>`}).join("")
      + `</table>`
    : `<p class="note">锚定记录没读到 —— 可能是取数失败，也可能确实还没有第一条。不替它断定是哪一种。</p>`;
})();
</script>
</div></body></html>
"""


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="输出路径（默认写到项目文件夹）")
    out = Path(ap.parse_args().out) if ap.parse_args().out else OUT
    js = (ROOT / "scripts" / "witness_verdict.js").read_text(encoding="utf-8")
    assert "</script>" not in js, "witness_verdict.js 不许含 </script>（要内联进 HTML）"
    html = (HTML.replace("/*WITNESS_VERDICT_JS*/", js)
                .replace("RAW_URL", RAW).replace("REPO_URL", REPO))
    out.write_text(html, encoding="utf-8")
    globals()["OUT"] = out
    print(f"✅ 已生成 {OUT}")
    print(f"   {datetime.now(timezone.utc).astimezone():%Y-%m-%d %H:%M %Z}")
    print("   双击打开即可；页面每次打开自动拉最新数据，不必重跑本脚本。")


if __name__ == "__main__":
    main()
