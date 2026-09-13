# 恐惧的标价 · Fear-Price

> 一个每日自动更新的美股历史数据档案站：把标普 500 的一个世纪、纳斯达克的半个世纪、
> 22 只行业龙头的完整履历，以及两个自研市场指标（K 指数——英文官方名 **KAPX Index**——与 LEAPS 窗口），
> 放进一份"金融报刊"式的静态网页。**纯静态、零服务器、零运行成本。**

[![每日行情更新](https://github.com/klaywang24/fear-price/actions/workflows/daily.yml/badge.svg)](https://github.com/klaywang24/fear-price/actions/workflows/daily.yml)
[![每周基本面更新](https://github.com/klaywang24/fear-price/actions/workflows/weekly.yml/badge.svg)](https://github.com/klaywang24/fear-price/actions/workflows/weekly.yml)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21783004.svg)](https://doi.org/10.5281/zenodo.21783004)
[![License](https://img.shields.io/badge/license-PolyForm--Noncommercial--1.0.0-4a5d3a)](LICENSE)
[![在线访问](https://img.shields.io/badge/%E5%9C%A8%E7%BA%BF%E8%AE%BF%E9%97%AE-chronicle.klay--wang.com-a0392f)](https://chronicle.klay-wang.com/)
[![数据集](https://img.shields.io/badge/%E9%A2%84%E7%94%9F%E6%88%90%E6%95%B0%E6%8D%AE%E9%9B%86-250%2B%20JSON-b8893e)](data/)
[![EN](https://img.shields.io/badge/EN-README-2b5f8f)](README.md)

> **参照说明。** 本站章节式目录编排参照 [historyofmarket.com](https://historyofmarket.com/)；纯静态 + 预生成 JSON 架构参照 [Big Picture](https://laoqianritan-create.github.io/us-market/)；机器可读层（`llms.txt` / `openapi.json` / `api-catalog`）做法参照 [dollarliquidity.com](https://dollarliquidity.com/)。原始行情数值归各自发布方（Cboe、FRED、FINRA、CFTC、CNN、Yahoo Finance）所有，见 [`data/README.md`](data/README.md)。

> **官方定义**：KAPX 指数（K 取自「恐」字拼音首字母）是 Fear-Price 每个交易日发布的美股恐惧定价指标：用 CNN 恐贪指数除以 VIX，衡量人群情绪相对波动率价格的偏离。读数、方法论与完整信号台账永久免费公开：上线（2026-07）起为逐日事前记录，2011 至上线前为公开数据回填、可独立复现；见证链公开可查：哈希链台账、GitHub 公开发布、第三方网页存档、Zenodo DOI 检查点。

**在线地址：<https://chronicle.klay-wang.com/>**

---

**自己验一遍**（这才是重点）：哈希链、每一层**证明什么与不证明什么**、以及可直接粘贴运行的
重算命令，全在 **[`data/README.md`](data/README.md)** —— 包含如何从 Internet Archive
取出旧快照，与现在公开的这份逐行比对。

## 它是什么

一份编年体的美股数据刊物，七个横版 tab，每个 tab 按"章节"组织（左侧悬浮目录随滚动高亮）：

| Tab | 内容 |
|---|---|
| **标普 500** | 十四章：百年走势 · 逐年成绩单与年度回报分桶 · 持有期胜率 · 滚动 5/10/20 年 · 牛熊周期 · 最坏的那些日子 · 历次回撤 · 估值两条曲线（CAPE 1871→ / PE TTM）· **离中位数多远**（三条历史中位数锚 + 当前百分位）· EPS · VIX 保护的价钱 · 十二个月的脾气 · 行业占比 · 503 家成分股 |
| **纳斯达克** | 十二章：综指 1971→ × 纳指 100 1985→，同款章节体系 + VXN |
| **金融** | XLF 锚 + 13 只龙头（银行/卡组织/投行/资管/券商/加密·稳定币），个股可钻取 |
| **消费** | XLP × XLY 双锚 + 6 只穿越周期的消费龙头 |
| **奢侈品** | LVMH · 爱马仕 · 法拉利（欧股，等权组合作锚） |
| **K 指数** | 自研信号：K = CNN 恐贪 ÷ VIX，K < 1 即"金风玉露一相逢"。2011 年以来 39 次信号逐次对账（20/40/60 交易日窗口；60 日 26/13，持有至今全部为正） |
| **LEAPS 窗口** | CNN 恐贪 < 25 = 极端恐惧 = LEAPS call 开仓观察窗口。2011 年以来 45 次窗口 × 6/12/18 个月视界全对账 |

**个股页**（22 只，含 COIN/HOOD/CRCL）各有最多十一章：上市以来走势 → 回报/回撤/波动 →
关键指标仪表盘 → 利润这条线（EPS 20 年季频）→ 资本效率（ROE × ROIC）→
估值的位置（PE 历史 + 百分位，银行类自动换 PB）→ 估值驱动 vs EPS 驱动 →
股东回报（1984→ 分红史）→ 同业对比。**章节按数据可用性自动裁剪，绝不显示空图。**

## 架构

```
GitHub Actions（每交易日 22:00 UTC / 每周六 08:00 UTC）
        │
        ▼
Python 管线  scripts/build_data.py（价格/指标/信号，每日）
             scripts/build_fundamentals.py（基本面，每周）
        │  拉取公开数据 → 计算 → 写出 data/*.json → commit
        ▼
纯静态前端  index.html + css + js（原生 JS + ECharts 5 CDN，无框架无构建）
        │  fetch 本地 JSON 渲染；hash 路由；day/night 主题
        ▼
GitHub Pages 自动发布
```

- **零服务器、零构建步骤**：commit 即部署；
- **日夜双主题**：设计系统源自个人 field guide 系列（羊皮纸 + 铁锈红/金/橄榄，Fraunces × Noto Serif SC × JetBrains Mono）；
- **可收藏的深链**：`#fin/jpm`、`#leaps` 等 hash 路由。

## 数据源

| 数据 | 来源 | 频率 |
|---|---|---|
| 指数 / ETF / 个股价格 | Yahoo Finance（yfinance） | 每日 |
| CNN 恐惧贪婪指数 | [whit3rabbit/fear-greed-data](https://github.com/whit3rabbit/fear-greed-data) 存档（2011→）+ CNN 官方接口当天值 | 每日 |
| 席勒 CAPE / PE(TTM) / EPS（1871→） | multpl.com | 每日 |
| 个股 PE/PS/PB/ROE/ROIC/FCF（约 20 年季频） | macrotrends.net（欧股用 ADR：LVMUY / HESAY） | 每周 |
| 个股报表（近 4 财年）/ 分红全史 / 快照 | Yahoo Finance | 每周 |
| 成分股名单与行业 | Wikipedia | 每日 |
| 公司 Logo | assets.parqet.com | 实时 |

拿不到可靠免费数据的指标（指数级远期 PE、银行 NIM 等运营指标）**留空并注明，不伪造**。

## 本地运行

```bash
git clone https://github.com/klaywang24/fear-price.git
cd market-chronicle
python3 -m venv .venv && source .venv/bin/activate
pip install yfinance pandas requests lxml

python scripts/build_data.py           # 重刷每日数据（约 3 分钟）
python scripts/build_fundamentals.py   # 重刷基本面（约 1 小时，macrotrends 限流）

python3 -m http.server 8137            # 打开 http://localhost:8137
```

## 项目结构

```
├── index.html              # 单页应用（7 个 tab + 个股钻取）
├── css/style.css           # 设计系统（light/dark 双套 CSS 变量）
├── js/app.js               # 路由 / 图表构建器 / 章节目录
├── data/                   # 250+ 预生成 JSON（由管线维护，勿手改）
├── scripts/
│   ├── build_data.py       # 每日管线：价格/指标/K指数/LEAPS/成分股
│   └── build_fundamentals.py  # 每周管线：个股基本面
└── .github/workflows/      # daily.yml + weekly.yml
```

## 致谢与灵感

- 章节式目录编排参照 [historyofmarket.com](https://historyofmarket.com/)；
- 纯静态 + 预生成 JSON 架构参照老钱日日谈的 [Big Picture](https://laoqianritan-create.github.io/us-market/)；
- 机器可读层（`llms.txt` / `openapi.json` / `api-catalog`）的做法参照 [dollarliquidity.com](https://dollarliquidity.com/)。

## 免责声明

本项目为个人研究工具。所有数据来自公开来源、可能有误或延迟；K 指数与 LEAPS 窗口的历史对账
（含胜率与失效案例）均如实呈现，**历史规律不保证未来，本站不构成任何投资建议**。

## License

[PolyForm Noncommercial 1.0.0](LICENSE)。个人使用、研究、学习等**非商业**用途可自由使用、fork、修改；商业用途（售卖、打包进付费产品/服务、作为商业服务运营）需另行获得作者授权。数据版权归各数据源所有。

> Required Notice: Copyright Klay Wang (https://chronicle.klay-wang.com)
