# AGENTS.md · fear-price（公开站仓）

> 本仓公开。任何 agent 在这里写的每一个字都会被外面看到：不写本机路径、不写凭据、不写个人信息、不写数据源名字。生成于 2026-09-19 05:13 -0400。

## 这个仓是什么
chronicle.klay-wang.com 的静态站与它的每日数据：温度计、K 指数、期权判断层 `data/options_page.json`、传导链四张 JSON、见证链哈希 `data/ledger_hashes.jsonl` 与锚定日志。

## 先读
`README.md`。维护者手册不在本仓（09-12 起移出），在私有工作区。

## 硬规矩
- 原始行情 CSV 绝不进本仓，只放加工后的判断层。
- `data/ledger_hashes.jsonl` 是单向链，只追加；改历史等于毁掉见证。
- `.github/workflows/`：daily 工作日 22:00 UTC、witness-watchdog 每天 13:00 UTC、upstream-integrity 工作日 13:40 UTC、weekly 周六、monthly-release 每月 2 号。cron 只入队，延迟数小时属常态，过点没跑不等于坏了。
- 体检 `scripts/check_witness_health.py`，红了先本地复现再动。
