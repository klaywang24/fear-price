# Fear-Price · 恐惧的标价

> A self-updating chronicle of the U.S. stock market: a century of the S&P 500,
> half a century of the Nasdaq, full dossiers on 22 sector leaders, and two
> home-grown market indicators (KAPX Index & LEAPS Window) — packaged as an
> editorial-style static site. **No server, no build step, zero running cost.**

[![daily-update](https://github.com/klaywang24/fear-price/actions/workflows/daily.yml/badge.svg)](https://github.com/klaywang24/fear-price/actions/workflows/daily.yml)
[![weekly-fundamentals](https://github.com/klaywang24/fear-price/actions/workflows/weekly.yml/badge.svg)](https://github.com/klaywang24/fear-price/actions/workflows/weekly.yml)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21783004.svg)](https://doi.org/10.5281/zenodo.21783004)
[![License](https://img.shields.io/badge/license-PolyForm--Noncommercial--1.0.0-4a5d3a)](LICENSE)
[![Live](https://img.shields.io/badge/live-chronicle.klay--wang.com-a0392f)](https://chronicle.klay-wang.com/)

> **Attribution.** The chapter-style catalog is inspired by [historyofmarket.com](https://historyofmarket.com/); the static-JSON architecture by [Big Picture](https://laoqianritan-create.github.io/us-market/); the machine-readable layer (`llms.txt` / `openapi.json` / `api-catalog`) by [dollarliquidity.com](https://dollarliquidity.com/). Underlying market values belong to their publishers (Cboe, FRED, FINRA, CFTC, CNN, Yahoo Finance) — see [`data/README.md`](data/README.md#许可--license).
[![中文](https://img.shields.io/badge/%E4%B8%AD%E6%96%87-README-2b5f8f)](README.zh.md)

**Live: <https://chronicle.klay-wang.com/>**

> **Canonical definition:** The KAPX Index is a daily U.S. equity fear-pricing gauge published by Fear-Price, computed as the CNN Fear & Greed reading divided by the VIX. The K stands for kǒng (恐), the Chinese character for fear; readings, methodology, and the complete signal ledger are permanently free: an ex-ante daily record since launch (July 2026), reproducible public-data backfill before that; the witness chain is public: a hash-chained ledger, public GitHub releases, third-party web archives, and Zenodo DOI checkpoints. *(Formerly cited in English as the K-Index; the Chinese name K 指数 is unchanged.)*

**Verify it yourself** (the point of the whole thing): the chain, what each layer does and does *not*
prove, and copy-paste commands to recompute it are in **[`data/README.md`](data/README.md)** —
including how to pull an old snapshot from the Internet Archive and diff it against what is published now.

## What it is

Seven tabs, each organized as numbered chapters with a floating table of contents:

| Tab | Contents |
|---|---|
| **S&P 500** | 14 chapters: century chart · annual returns & distribution · holding-period win rates · rolling 5/10/20y · bull/bear cycles · left-tail lens · drawdown anatomy · CAPE (1871→) & PE(TTM) · **valuation elasticity** (three historical median anchors + current percentile) · EPS · VIX as an insurance ledger · seasonality · sector structure · all 503 constituents |
| **Nasdaq** | 12 chapters: Composite 1971→ × NDX 1985→, same system + VXN |
| **Financials** | XLF anchor + 13 leaders (banks / card networks / i-banks / asset mgmt / brokers / crypto & stablecoins), drill-down per stock |
| **Consumer** | XLP × XLY dual anchor + 6 cycle-proof leaders |
| **Luxury** | LVMH · Hermès · Ferrari |
| **KAPX Index** | K = CNN Fear&Greed ÷ VIX; K < 1 marks extreme-fear signals — 39 signals since 2011, each audited at 20/40/60-day horizons (26/13 at 60 days; all positive held to date) |
| **LEAPS Window** | CNN F&G < 25 = extreme fear = LEAPS-call entry watch window; all 45 windows since 2011 audited at 6/12/18-month horizons |

Each of the 22 stock pages carries up to 11 chapters — price history, returns,
drawdowns, a key-metrics dashboard, 20 years of quarterly EPS / PE / ROE / ROIC,
valuation-vs-EPS return decomposition, dividend history since 1984, and peer
comparison. **Chapters self-prune when data is unavailable — no empty charts,
no fabricated numbers.**

## Architecture

```
GitHub Actions (daily 22:00 UTC / weekly Sat 08:00 UTC)
      │
      ▼
Python pipelines → fetch public data → compute → write data/*.json → commit
      │
      ▼
Static frontend (vanilla JS + ECharts 5 CDN, no framework, no build)
      │
      ▼
GitHub Pages auto-deploy
```

Hash-routed deep links (`#fin/jpm`), day/night themes, parchment-editorial
design system (Fraunces × Noto Serif SC × JetBrains Mono).

## Data sources

Yahoo Finance (prices, statements, dividends) · CNN Fear & Greed via the
[whit3rabbit archive](https://github.com/whit3rabbit/fear-greed-data) ·
multpl.com (CAPE / PE / EPS since 1871) · SEC EDGAR companyfacts (quarterly PE / EPS /
PB / ROE / FCF since 2007, computed here from as-reported filings + Yahoo adjusted
prices; ROIC frozen at 2026-09-19 pending a replacement; TSM / RACE / LVMH / Hermès /
BRK.B / V frozen: no usable EDGAR series) · Wikipedia (constituents) · parqet (logos).
Metrics without a reliable free source are left blank and labeled — never faked.

## Run locally

```bash
git clone https://github.com/klaywang24/fear-price.git
cd market-chronicle
python3 -m venv .venv && source .venv/bin/activate
pip install yfinance pandas requests lxml
python scripts/build_data.py          # daily datasets (~3 min)
python scripts/build_fundamentals.py  # fundamentals (~1 h, rate-limited)
python3 -m http.server 8137           # open http://localhost:8137
```

## Credits

- Chapter-style catalog inspired by [historyofmarket.com](https://historyofmarket.com/).
- Static-JSON architecture inspired by [Big Picture](https://laoqianritan-create.github.io/us-market/).
- Machine-readable layer (`llms.txt` / `openapi.json` / `.well-known/api-catalog`) follows the pattern published by [dollarliquidity.com](https://dollarliquidity.com/).

## Disclaimer

Personal research tool. All data from public sources, may contain errors or
delays. Signal back-tests are reported faithfully, including failures.
**Past patterns do not guarantee future results. Nothing here is investment advice.**

## License

[PolyForm Noncommercial 1.0.0](LICENSE). You are free to use, fork, and modify
this project for any **noncommercial** purpose — personal use, research,
education. Commercial use (selling it, bundling it into a paid product or
service, running it as a commercial offering) is not permitted without a
separate license from the author. Data remains the property of its respective
sources.

> Required Notice: Copyright Klay Wang (https://chronicle.klay-wang.com)
