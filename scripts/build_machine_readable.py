#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""机器可读层生成器（2026-09-05 建 · Klay 令「llms.txt + openapi 那套我们怎么做」）。

## 为什么建
`llms.txt` / `sitemap.xml` / `robots.txt` 早就有，**缺的是 API 那一半**：
站上 398 个公开 JSON 事实上已经是一套只读 API（免鉴权、CORS 开放、每交易日更新），
但没有任何机器可读的目录说「有哪些端点、各自是什么、各自什么授权」。
⇒ 模型和聚合站只能靠猜，或者干脆绕开。SEO 记忆那条原话：**造词战场是 AI 引用不是关键词排名，
   而 llms.txt 是最有效载体且最易漏** —— openapi.json 是同一件事的另一半。

## 产出三样（全部由本脚本生成，别手写：手写必与实际文件漂移）
  openapi.json              OpenAPI 3.1 规格，逐端点带 `x-license`
  .well-known/api-catalog   RFC 9727 标准发现位置，指向 openapi.json
  llms-full.txt             llms.txt 的全文版（把 data/README 的口径与验证章节一并喂给模型）

## 🔴 授权：**只做机器可读，不做任何新授权**（2026-09-05 定，别改）
站上早有一套刻意的三段划分，写在 llms.txt 与 data/README：
  · **编纂、派生指标、台账结构** ＝ 我们的，PolyForm Noncommercial 1.0.0
  · **原始数值** ＝ 转录自公开源（Cboe/FRED/FINRA/CFTC/Yahoo/CNN）的事实，我们不主张所有权
  · **Kaggle / HuggingFace 上的季度提取物** ＝ 单独以 CC BY 4.0 释出
🚫 整站 CC0（放弃一切权利换引用）不是本站的选择：恐惧的标价刻意留在 PolyForm（「数字要跑，台账不跑」是原话）。
   本脚本只是把已有的划分**写成机器读得懂的形式**，一个字的授权都没有多给。
   要不要改成 CC0 是 Klay 的商业决定，不是工程决定，别在这里顺手做掉。

用法：python3 scripts/build_machine_readable.py [--check]
      --check ＝ 只比对不写盘，产物与当前应生成内容不一致就非零退出（给 CI 当闸用）
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://chronicle.klay-wang.com"

# 只登记 llms.txt / data/README 已经公开点名的那些端点。
# 🚫 别把 398 个文件全倒进去：面板私有中间产物（spy_* / consumer_* 那一批）会随改版增删，
#    登记它们＝给自己造一份必然过期的清单。判据＝「文档里点过名的才算对外契约」。
ENDPOINTS = [
    ("kindex.json", "KAPX 指数逐日序列", "mc.kapx.cnn_vix.v1",
     "K = CNN 恐贪 ÷ VIX 的逐日读数：日期、恐贪、VIX、纳斯达克100、K 值、当前读数。"),
    ("kindex_signals.json", "KAPX 信号台账", None,
     "2011 年以来每一次 K < 1 的信号，含 20/40/60 交易日与至今的前向纳指回报。赢的和输的都在。"),
    ("leaps.json", "极端恐惧窗口台账", None,
     "2011 年以来 CNN 恐贪收在 25 以下的每一段，含 6/12/18 个月与至今的前向标普/纳指回报。"),
    ("leaps_gauge.json", "恐惧的标价指数逐日序列", "mc.fear_price.vix1y_pct3y.v1",
     "VIX1Y 的三年滚动百分位，另带四项 context。⚠️ 授权与其余不同，见 x-license。"),
    ("pulse.json", "当日市场脉动", None,
     "头版当日读数：市场温度（估值+广度分位）、指数报价、板块涨跌。"),
    ("sentiment.json", "情绪仪表盘", None,
     "Put/Call、VXN 溢价、SKEW、期限结构、恐贪七分量。"),
    ("breadth.json", "市场广度", None,
     "标普成分股在 200 日均线上的占比。"),
    ("macro.json", "宏观与传导链序列", None,
     "FRED 公开序列的周频/同比视图：资金面、信用利差、利率曲线、贴现窗口、央行互换，"
     "以及 2026-09-05 起的水位层（资产负债表/财政部账户/准备金/期限溢价/两条利率腿）。"),
    ("cot_vix.json", "VIX 期货 COT 站位", None, "CFTC 承诺持仓：VIX 期货的杠杆基金与资管净持仓。"),
    ("cot_rates.json", "国债期货 COT 站位", None, "CFTC TFF：10 个国债/短端期货市场的杠杆基金站位。"),
    ("cot_equity.json", "股指期货 COT 站位", None, "CFTC TFF：标普 500 与纳斯达克 100 期货的杠杆基金与资管四条腿原值、净额与历史分位（周频，2010→）。"),
    ("margin_debt.json", "FINRA 融资余额", None, "客户保证金账户融资余额、闲置资金与净融资，含同比与全史分位（月频，1997→）。"),
    ("ofr_hedge_funds.json", "OFR 对冲基金杠杆", None, "SEC Form PF 汇总：股票策略杠杆率、最大十家杠杆率、主经纪商借款、总资产与净资产（季频，2013→）。"),
    ("vol_indices.json", "指数波动率家族", None, "Cboe VIX/VXN/RVX/VXD/VXTLT/VVIX 现值与三年、全史分位，另带 VXN÷VIX、RVX÷VIX 比值（日频）。"),
    ("vx_curve.json", "VIX 期货期限结构", None, "9 档月份的 VIX 期货价与升水/倒挂形态。"),
    ("move.json", "MOVE 指数与三年分位", None, "美债期权隐含波动率，利率侧的「保险价格」。"),
    ("meta.json", "数据 as-of 与署名", None, "各序列的数据截止日期与数据源署名。"),
    ("ledger_audit.json", "每日自核结果", None,
     "git 里当天发布过的值 vs 今天重算的值，分歧逐条公开。"),
    ("gauge_math.json", "每日口径自检", None,
     "当日头条读数用参考实现独立重算的比对结果。"),
    ("ledger_hashes.jsonl", "哈希链", None,
     "每日锚定：删任何一天，其后全部对不上。Wayback 上有独立见证快照。"),
    ("digest_archive.json", "日更归档索引", None, "每期 digest 的标题、日期与各平台链接。"),
    ("options_page.json", "期权异动与判断层", None,
     "墙位、开奖台账、大单榜等**加工读数**。🚫 原始逐笔合约行不在此，也永不上网。"),
    ("f13_page.json", "13F 开奖页", None, "机构名册的季度持仓变化与开奖记录。"),
]

POLYFORM = {"name": "PolyForm Noncommercial 1.0.0",
            "url": "https://polyformproject.org/licenses/noncommercial/1.0.0"}
LICENSE_NOTE = (
    "三段划分，别混：**编纂/派生指标/台账结构**＝Klay Wang，PolyForm Noncommercial 1.0.0"
    "（非商业免费，商用另谈 klaywang24+fearprice@gmail.com）；**原始数值**＝转录自公开源"
    "（Cboe / FRED / FINRA / CFTC / Yahoo Finance / CNN）的事实，受各自源条款约束，我们不主张所有权；"
    "**Kaggle 与 Hugging Face 上的季度提取物**＝另以 CC BY 4.0 释出（署名即可，含商用）。")


def build_openapi():
    paths = {}
    for fname, title, sid, desc in ENDPOINTS:
        ext = "application/jsonl" if fname.endswith(".jsonl") else "application/json"
        op = {
            "operationId": "get_" + fname.split(".")[0],
            "summary": title,
            "description": desc,
            "tags": ["data"],
            "x-license": POLYFORM,
            "responses": {"200": {"description": title,
                                  "content": {ext: {"schema": {"type": "object"}}}}},
        }
        if sid:
            op["x-series-id"] = sid
        paths[f"/data/{fname}"] = {"get": op}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "Fear-Price 公开数据 · Public Data",
            "summary": "恐惧的标价的公开 JSON：KAPX 指数、恐惧的标价指数、宏观与传导链序列、"
                       "每日自核与哈希链。免鉴权、CORS 开放、每交易日更新。",
            "description": (
                "本规格登记的是 llms.txt 与 data/README.md 已点名的对外契约端点。"
                "站自己的前端也吃这些文件，所以它们不会被锁——**免费门永远敞开**是这个站的地基"
                "（锁它＝自断 AI 引用，也自毁「网站永远免费」的承诺）。\n\n"
                "口径与如实披露、三层证据、怎么自己重算一遍：见 " + SITE + "/data/README.md。\n\n"
                "授权：" + LICENSE_NOTE),
            "version": "1.0.0",
            "contact": {"name": "Fear-Price", "url": SITE},
            "license": POLYFORM,
        },
        "servers": [{"url": SITE}],
        "x-license-note": LICENSE_NOTE,
        "x-machine-readable": {
            "llms": f"{SITE}/llms.txt",
            "llmsFull": f"{SITE}/llms-full.txt",
            "sitemap": f"{SITE}/sitemap.xml",
            "apiCatalog": f"{SITE}/.well-known/api-catalog",
            "openapi": f"{SITE}/openapi.json",
        },
        "x-refresh": "每个美股交易日一次（云管线 GitHub Actions，收盘后 1~2 小时）",
        "x-auth": "none",
        "x-cors": "*",
        "paths": paths,
    }


def build_catalog():
    """RFC 9727 api-catalog：标准发现位置，内容是一份 linkset。"""
    return {"linkset": [{
        "anchor": SITE + "/",
        "service-desc": [{"href": SITE + "/openapi.json", "type": "application/openapi+json"}],
        "service-doc": [{"href": SITE + "/data/README.md", "type": "text/markdown"},
                        {"href": SITE + "/llms.txt", "type": "text/plain"}],
        "license": [{"href": POLYFORM["url"], "title": POLYFORM["name"]}],
    }]}


def build_llms_full():
    """llms.txt 的全文版：把口径与验证章节一并喂给模型。

    🔑 不是把整个站塞进来 —— 而是把**读数据前必须知道的东西**（口径、边界、怎么自己验）
       放进一份文件。模型抓 llms.txt 只拿到索引，抓这份才拿到「怎么正确使用」。
    """
    parts = [open(os.path.join(ROOT, "llms.txt"), encoding="utf-8").read().rstrip()]
    readme = os.path.join(ROOT, "data", "README.md")
    if os.path.exists(readme):
        parts.append("\n\n" + "=" * 72 + "\n# data/README.md（口径 · 出处 · 怎么自己验一遍）\n"
                     + "=" * 72 + "\n\n" + open(readme, encoding="utf-8").read().rstrip())
    parts.append("\n\n" + "=" * 72 + "\n# 机器可读入口 · Machine-readable entry points\n" + "=" * 72 + "\n\n"
                 f"- OpenAPI 3.1: {SITE}/openapi.json\n"
                 f"- API catalog (RFC 9727): {SITE}/.well-known/api-catalog\n"
                 f"- Sitemap: {SITE}/sitemap.xml\n"
                 f"- Index for LLMs: {SITE}/llms.txt\n\n"
                 "免鉴权、CORS 开放、每交易日更新。授权：" + LICENSE_NOTE + "\n")
    return "".join(parts)


TARGETS = [("openapi.json", lambda: json.dumps(build_openapi(), ensure_ascii=False, indent=1) + "\n"),
           (".well-known/api-catalog", lambda: json.dumps(build_catalog(), ensure_ascii=False, indent=1) + "\n"),
           ("llms-full.txt", build_llms_full)]


def main():
    check = "--check" in sys.argv
    stale = []
    for rel, fn in TARGETS:
        path = os.path.join(ROOT, rel)
        want = fn()
        have = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if have == want:
            print(f"  = {rel}（未变）")
            continue
        if check:
            stale.append(rel)
            print(f"  🔴 {rel} 与生成内容不一致（跑 build_machine_readable.py 重出）")
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(want)
        # 判据落在产物上：写完回读必须逐字节等于要写的内容
        assert open(path, encoding="utf-8").read() == want, f"{rel} 回读不等于写入"
        print(f"  ✅ {rel}（{len(want.encode())} 字节）")
    if check and stale:
        print(f"🔴 机器可读层过期 {len(stale)} 处：{stale}")
        return 1
    print("✅ 机器可读层" + ("已核对一致" if check else "已生成"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
