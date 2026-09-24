# AGENTS.md · fear-price（公开站仓）

> 本仓公开。任何 agent 在这里写的每一个字都会被外面看到：不写本机路径、不写凭据、不写个人信息、不写数据源名字。更新于 2026-09-23。

## 这个仓是什么
chronicle.klay-wang.com 的静态站与它的每日数据：温度计、K 指数、期权判断层 `data/options_page.json`、传导链四张 JSON、见证链哈希 `data/ledger_hashes.jsonl` 与锚定日志。

## 先读
`README.md`。维护者手册不在本仓（09-12 起移出），在私有工作区；`HANDOFF.md` 是留空占位，不往里写。

## 硬规矩
- 原始行情 CSV 绝不进本仓，只放加工后的判断层。
- 2026-09-24 起，新页面、新 JSON、新 API 字段一律只放加工值（分位、比值、回撤、回报、本站读数与判断），不放第三方原始数值；拿不准先别放。已有文件的现状另案处理，不在这里改。
- `data/ledger_hashes.jsonl` 是单向链，只追加；改历史等于毁掉见证。
- `.github/workflows/`：daily 工作日 22:00 UTC、witness-watchdog 每天 13:00 UTC、upstream-integrity 工作日 13:40 UTC、weekly 周六、monthly-release 每月 2 号。cron 只入队，延迟数小时属常态，过点没跑不等于坏了。
- 体检 `scripts/check_witness_health.py`，红了先本地复现再动。
- `index.html` 改了就重跑 `python3 scripts/build_route_pages.py`，路由页是它的产物。只改源不重生成，夜班 `run_digest_archive.py` 会天天报红并回滚（09-19 到 09-22 连红四晚，就是 css 版本戳升了、产物没重生成）。
- 见证链判据有两份实现（`scripts/check_witness_health.py` 与 `scripts/witness_verdict.js`），共用一份样本 `scripts/witness_fixtures.json`。改判据先加样本，`python3 scripts/check_witness_health.py --selftest` 与 `node scripts/test_witness_fixtures.js` 都过才提交；daily 每天先跑这两份自检，失败即整班红。
- 生成出来的页面不直接改，改生成器再重生成。
