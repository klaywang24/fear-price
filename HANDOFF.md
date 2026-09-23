# HANDOFF · fear-price 公开仓（2026-09-23 建，首节＝见证链）

> 给任何接手的 agent（Claude / Codex 都一样）。读完这一节，你就知道见证链是什么、每天怎么跑、红了怎么判、哪些坑已经踩过。
> 密钥永不在本仓；本仓公开。

## 一、见证链是什么（一段话）

Klay 每天发布的指数读数与判断存在自己仓库里，自己随时能改，所以「我 X 月 X 日就这么说了」光靠自己的记录没有说服力。
见证链＝每天把账本的哈希入链（`data/ledger_hashes.jsonl`），再把账本与几张关键页推给 Internet Archive（Wayback Machine）存一份带时间戳的第三方副本，事后改不掉。
**它只证明一件事：台账没被事后改过。** 它坏了不影响任何读者，所以只能主动看。

## 二、部件与班次

| 部件 | 文件 | 谁跑 · 何时 | 产物 |
|---|---|---|---|
| 入链 | `scripts/anchor_hashes.py` | daily.yml（排定 18:00 ET，cron 只入队，实起 19:5x～次日 02:00） | `data/ledger_hashes.jsonl` |
| 锚定 | `scripts/anchor_wayback.py --sha <HEAD>` | 同上，紧接入链 | `data/anchor_log.jsonl`（每 URL 一条：save_http / probe / within_sla / spn_status / spn_timestamp）+ `data/anchor_pending.json` 补探队列 |
| 体检 | `scripts/check_witness_health.py --record` | 同上，锚定之后；**之前先跑 `--selftest` 与 `node scripts/test_witness_fixtures.js`** | `data/health_log.jsonl`（七项 status + overall） |
| 看门狗 | `witness-watchdog.yml` | 每天 09:00 ET（实迟 4～10h） | daily 死了也能开 Issue → 邮件 |
| 看板 | `scripts/build_witness_dashboard.py` → `../../见证链看板.html` | 人手动重生成；页面打开时自己拉远端 raw | 单文件 HTML，判据内联自 `scripts/witness_verdict.js` |

锚定的 9 个 URL 在 `anchor_wayback.targets()`：账本哈希链、当日 commit 页、`kindex.json`、`options_page.json`、站首页、`leaps_gauge.json`、`credit_witness.json`、`/kapx`、`/fear-price`。

## 三、体检七项，红了怎么判

```
cd "<本仓>" && git pull -q && python3 scripts/check_witness_health.py
```

| 项 | 红的意思 | 先看什么 |
|---|---|---|
| 链最后一行 | 账本没在逐日入链 | daily 跑没跑（Actions 页） |
| 锚定日志 | 存档超期 / 记录自相矛盾 | 文案会**逐个点名** URL；带「当轮已存 <ts>·等 IA 索引」＝已存成、等对方索引，**不是漏存** |
| 链头快照(直接问 IA) | Wayback 上没近期快照 | 手动补：`python3 scripts/anchor_wayback.py --sha "$(git rev-parse HEAD)"` |
| 与存档逐字对账 | 存档内容与本地不一致 | **最严重**，意味着链被改过或重造 |
| daily 是否还活着 | 数据 4 天没更新 | Actions 是否被 GitHub 自动停用 |
| daily 定时有没有触发 | cron 没入队 | 同上 |
| 期权页是否在更新 | 本机 launchd→生成器→push 站仓 那条链断了 | 期权管线仓 `_eod_scan.log` |

**SLA**：记录/数据新鲜度 4 天（容周末＋一个假日）；单个快照 3 天（`anchor_wayback.STALE_DAYS`）。两者是两件事，不是两把尺子。

## 四、2026-09-23 之后你必须知道的四件事

1. **存档提交要凭据**。匿名 Save Page Now 对每个 URL 都回 500（09-23 实测），从前没人发现是因为 IA 自家爬虫顺手在抓、快照看着还在更新。现在走带凭据的 v2 接口：CI 用仓库 Secrets `IA_ACCESS_KEY` / `IA_SECRET_KEY`，本机用 `~/.config/internetarchive/ia.ini` 的 `[s3]`。没凭据自动退回匿名，行为不变但等于没存。看 `anchor_log` 顶层 `save_mode` / `save_ok`：`anon` 且 `save_ok=0` ＝ 见证靠运气。
2. **IA 的公开索引落后于真实抓取**，有时几小时。所以「超期」可能是「已存成、索引没更新」。每条 URL 记了 SPN 回执 `spn_status`/`spn_timestamp`，体检文案会缀「当轮已存·等 IA 索引，非漏存」。**回执不改判定**（没进索引就还不可公开查证），只告诉你该等还是该查。
3. **判据有两个实现、一份样本**：python `anchor_verdict()` 与 JS `witness_verdict.js`，都必须过 `scripts/witness_fixtures.json`（7 条，6 负向，第 1 条就是 09-23 那次「只读 results[0] 连报 8 天绿」的原形）。**改判据先加样本**，两边都过才算改完。CI 每天先跑这两份自检，不允许失败。
4. **矛盾检测**：一条记录同时带明细与聚合计数，两者必须一致，不一致本身就是红，不用知道谁对。写样本时自己也会写出不自洽的，被它抓过一次。

## 五、已知弱点（未修，接手者别当故障）

- `/kapx` 与 `/fear-price` 两页的存档副本只有 5.3KB，实际页面 13KB：存的是渲染前的骨架。这两页是弱见证；真正的证明力在几个 JSON 数据文件（纯文本，存进去就是完整的）。
- 当日 commit 页有时 `probe=none`（确实无快照），会计进超期，补探队列会重提交。

## 六、硬规矩

- 判据改动：先改 `witness_fixtures.json`，再改两处实现，`--selftest` 与 node 测试都过才提交。
- 🚫 不改看板产物 HTML，只改生成器再重生成（09-03 踩过：改产物不改生成器＝定时雷）。
- 🚫 凭据不进仓、不进日志、不贴进对话。`job_id` 也不记（40 位十六进制会被个人信息闸误判）。
- 🚫 不拿「自述状态」当验收：看产物文件与 Wayback 实际快照。
