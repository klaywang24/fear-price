# -*- coding: utf-8 -*-
"""从 index.html 生成各板块的平铺路由页（pricing.html 等）。

为什么存在：站是单页应用，服务端对所有路径回同一份 index.html，
其 canonical 硬写死指向首页 —— 于是 /pricing、/kindex 等 16 个路由在
Google 眼里全是首页的复本，除首页外任何路由都进不了索引
（2026-07-27 Search Console 邮件实锤，/pricing 上线以来搜索零可见）。

方案（§63，2026-08-17 重做）：每个路由页 = index.html 换头部身份
（title / description / canonical / og）+ **正文只保留自己的那个 panel**。
07-30 首版只换头、正文 17 份逐字节相同，Google 判重看正文不看 head ——
08-08 GSC 邮件「重复网页，Google 选择的规范网页与用户指定的不同」实锤，
canonical 声明在正文雷同时不被采信。tab 已换成 <a href>（app.js 委托拦截），
瘦身页上点到不存在的 panel 时浏览器整页导航到对应路由页。
sitemap.xml 也由本脚本生成（expected_sitemap_urls 是清单唯一实现，
tools/check_route_pages.py 闸用同一函数对账）。Cloudflare Pages 对平铺文件
foo.html 服务在 /foo（无尾斜杠），与 _redirects 现有规则同向；
🚨 绝不能用 foo/index.html 目录形式 —— Pages 会把 /foo 308 到 /foo/，
与 _redirects 里 /foo/ → /foo 的 301 对撞成无限循环（/welcome 线上实测
Pages 目录页就是强制加斜杠的）。相对资源路径在 /foo 下解析回根，无需改动。

用法：
    python3 scripts/build_route_pages.py            # 生成全部路由页
    python3 scripts/build_route_pages.py pricing    # 只生成指定路由

⚠️ index.html 每次改动后必须重跑本脚本，否则路由页与首页漂移。
⚠️ /pulse 故意不生成：它是首页别名（app.js 里 canonical 归到 /），
   生成了反而制造首页复本。
"""
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://chronicle.klay-wang.com"

# 每个路由：页面标题（title / og:title 共用）+ 描述（description / og:description 共用）。
# 文案对齐站内各 panel 的实际内容；旗舰两页（kindex / leaps）写得最满。
ROUTES = {
    "kindex": (
        "K 指数（KAPX Index）台账 · CNN 恐贪 ÷ VIX",
        "K 指数（KAPX Index）：CNN 恐惧贪婪指数除以 VIX 的反向情绪指标，K < 1 标记极端恐惧。"
        "每个交易日更新读数，2011 年以来每次信号对照纳指前向收益逐笔公开对账，赢的亏的都在。"
        "The KAPX Index: CNN Fear & Greed / VIX, reconciled daily.",
    ),
    "leaps": (
        "恐惧的标价指数（Fear-Price Index）· LEAPS 成本刻度",
        "长期期权（LEAPS）现在贵不贵：以一年期隐含波动率 VIX1Y 的三年分位为主读数，"
        "辅以波动率风险溢价、期限阶梯、SKEW 与实际利率，回填至 2007 年，每个交易日留痕。"
        "纯描述性刻度，不构成交易信号。Fear-Price Index: a cost gauge for LEAPS, formerly Fear's Price Tag.",
    ),
    "spy": (
        "标普 500 · 一个世纪的形状",
        "标普 500 百年图表档案：长期走势、历次回撤与估值刻度，每个交易日自动更新。"
        "A century of S&P 500 history in self-updating charts.",
    ),
    "qqq": (
        "纳斯达克 · 成长与代价",
        "纳斯达克与纳指 100 图表档案：成长的长坡与换来它的深回撤，每个交易日自动更新。"
        "Nasdaq's growth and its drawdowns, charted daily.",
    ),
    "tech": (
        "科技板块 · 增长的引擎",
        "美股科技板块图表档案：龙头公司的长期走势与估值水位，每个交易日自动更新。"
        "US tech sector charts, updated every trading day.",
    ),
    "fin": (
        "金融板块 · 钱的生意",
        "美股金融板块图表档案：银行、券商与支付公司的长期走势，每个交易日自动更新。"
        "US financials sector charts, updated every trading day.",
    ),
    "consumer": (
        "消费板块 · 慢变量的复利",
        "美股消费板块图表档案：消费公司的长期复利曲线，每个交易日自动更新。"
        "US consumer sector charts, updated every trading day.",
    ),
    "luxury": (
        "奢侈品板块 · 定价权的溢价",
        "奢侈品公司图表档案：定价权带来的长期溢价与周期波动，每个交易日自动更新。"
        "Luxury sector charts, updated every trading day.",
    ),
    "macro": (
        "宏观 · 市场的水位",
        "宏观图表档案：利率、流动性与市场水位的长期序列，每个交易日自动更新。"
        "Macro charts: rates, liquidity, and market context, updated daily.",
    ),
    "methodology": (
        "方法论 · 两把刻度，一本结果账",
        "K 指数与恐惧的标价指数的完整方法论：口径定义、数据来源、对账规则，"
        "以及为什么每条读数都要留下可验证的时间戳。Methodology of the KAPX Index and the Fear-Price Index.",
    ),
    "pricing": (
        "定价与订阅",
        "恐惧的标价订阅方案：读数与台账永久免费公开，付费订阅每日 digest 解读。"
        "月付与年付价格、创始价与退款政策入口都在这一页。Fear-Price pricing and subscription.",
    ),
    "about": (
        "关于恐惧的标价",
        "恐惧的标价是什么、为谁而做、指标为什么全部公开对账：站点定位与作者介绍。"
        "About Fear-Price and its author.",
    ),
    "contact": (
        "联系我",
        "联系恐惧的标价作者：邮箱与社交账号入口。Contact Fear-Price.",
    ),
    "privacy": (
        "隐私政策",
        "恐惧的标价隐私政策：收集什么、不收集什么、数据如何使用。Fear-Price privacy policy.",
    ),
    "terms": (
        "服务条款",
        "恐惧的标价服务条款：内容授权、订阅规则与免责声明。Fear-Price terms of service.",
    ),
    "refunds": (
        "退款政策",
        "恐惧的标价付费订阅的退款政策与申请方式。Fear-Price refund policy.",
    ),
}


def _tracked_digest(root, pattern):
    """digest/ 下匹配 pattern 且**已被 git 跟踪**（含已 git add 暂存）的页，按文件名排序。

    🔴 2026-09-25 根修（Klay 拍板）：原先直接 glob 工作区。09-24 09:06 有人在本机跑本脚本，
    digest 会话还没提交的 digest/2026-09-23(.en).html 被写进 sitemap 推上去，CI 检出里没有
    这两页 ⇒ check_route_pages 报「sitemap 多」⇒ 18:05 daily-update 整轮停。
    ⇒ 清单只认 git 知道的页：本机未提交的页不会漏进 sitemap，CI 与本机看到的是同一份。
    新页要进 sitemap，先 git add（run_digest_archive 已这么做），再跑本脚本。
    不是 git 仓（如临时副本）时退回 glob 并打警告，不静默。"""
    d = root / "digest"
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", "digest/"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"⚠️ {root} 不是 git 仓，digest 页清单退回工作区 glob（可能含未提交页）",
              file=sys.stderr)
        return sorted(d.glob(pattern))
    tracked = {pathlib.PurePosixPath(p).name for p in r.stdout.split("\0") if p}
    return sorted(f for f in d.glob(pattern) if f.name in tracked)


def expected_sitemap_urls(root=ROOT):
    """应收录页面清单的唯一实现 —— 生成器写 sitemap 用它，闸对账也用它。
    组成：首页 + ROUTES 全部路由 + /options（独立平铺页）+ /digest/ 归档页 + 各周报。
    noindex 转化页（pay/welcome/check-inbox/confirmed）与 /pulse（首页别名，
    _redirects 已 301 归 /）永不入内。digest 各页只算 git 已跟踪的（见 _tracked_digest）。"""
    urls = [f"{BASE}/"]
    urls += [f"{BASE}/{r}" for r in ROUTES]
    urls.append(f"{BASE}/options")
    urls.append(f"{BASE}/f13")          # 13F 开奖独立页（2026-08-18·与期权页数据完全不互通）
    urls.append(f"{BASE}/kapx")         # KAPX 术语页（2026-08-25·独立平铺页，定义/公式/口径/FAQ）
    urls.append(f"{BASE}/fear-price")   # 恐惧的标价术语页（2026-08-25·同上）
    urls.append(f"{BASE}/digest/")
    urls.append(f"{BASE}/digest/index.en")   # EN 归档索引（§77 2026-09-07：改无扩展。08-25 那版写 .html，Pages 把 .html 308 到无扩展 ⇒ sitemap 条目是跳转页、页内 canonical 又指回跳转页，GSC 09-06 拒收）
    for f in _tracked_digest(root, "*-weekly.html"):
        urls.append(f"{BASE}/digest/{f.stem}")
    for f in _tracked_digest(root, "*-weekly.en.html"):
        urls.append(f"{BASE}/digest/{f.stem}")    # EN 周报（2026-08-25·canonical 为无扩展 .en 形态，与页内一致）
    for f in _tracked_digest(root, "20??-??-??.html"):
        urls.append(f"{BASE}/digest/{f.stem}")    # 日更页（2026-08-25·Klay 拍板日更往期上站，T+1）
    for f in _tracked_digest(root, "20??-??-??.en.html"):
        urls.append(f"{BASE}/digest/{f.stem}")    # 日更 EN 页
    return urls


def write_sitemap(root=ROOT):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    lines += [f"  <url><loc>{u}</loc></url>" for u in expected_sitemap_urls(root)]
    lines.append("</urlset>")
    (root / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"生成 sitemap.xml（{len(expected_sitemap_urls(root))} 条）")


def patch(html: str, route: str, title: str, desc: str) -> str:
    """替换头部六处身份标记；任何一处找不到或不唯一都直接炸，绝不静默产出错页。"""
    full_title = f"{title} · 恐惧的标价 Fear-Price"
    subs = [
        # (旧串, 新串) —— 旧串必须与 index.html 逐字一致且全文唯一
        ("<title>恐惧的标价 · Fear-Price：K 指数（KAPX）与恐惧的标价指数</title>",
         f"<title>{full_title}</title>"),
        ('<link rel="canonical" href="https://chronicle.klay-wang.com/">',
         f'<link rel="canonical" href="{BASE}/{route}">'),
        ('<meta property="og:url" content="https://chronicle.klay-wang.com/">',
         f'<meta property="og:url" content="{BASE}/{route}">'),
        ('<meta property="og:title" content="恐惧的标价 · Fear-Price：K 指数（KAPX）与恐惧的标价指数">',
         f'<meta property="og:title" content="{full_title}">'),
    ]
    # description 与 og:description 整行替换（行内容随首页文案变化，锚定行首标记）
    out_lines = []
    seen = {"desc": 0, "ogdesc": 0}
    for line in html.split("\n"):
        if line.startswith('<meta name="description" content="'):
            out_lines.append(f'<meta name="description" content="{desc}">')
            seen["desc"] += 1
        elif line.startswith('<meta property="og:description" content="'):
            out_lines.append(f'<meta property="og:description" content="{desc}">')
            seen["ogdesc"] += 1
        else:
            out_lines.append(line)
    assert seen["desc"] == 1 and seen["ogdesc"] == 1, f"{route}: description 行未命中 {seen}"
    html = "\n".join(out_lines)
    for old, new in subs:
        n = html.count(old)
        assert n == 1, f"{route}: 锚串命中 {n} 次（应为 1）：{old[:60]}"
        html = html.replace(old, new, 1)
    return strip_foreign_panels(html, route)


def strip_foreign_panels(html: str, route: str) -> str:
    """只保留 id="panel-<route>" 的 section，删除其余全部 panel（§63 判重修复的核心）。

    边界用 <section>/</section> 配平计数找，不用正则贪婪匹配 —— panel 内部
    允许嵌套 section。保留页统计出的 panel 必须恰好 1 个，否则直接炸。"""
    lines = html.split("\n")
    starts = [(i, re.search(r'id="panel-([a-z]+)"', l).group(1))
              for i, l in enumerate(lines) if re.search(r'<section[^>]*id="panel-[a-z]+"', l)]
    assert len(starts) == 17, f"{route}: index.html 应有 17 个 panel，实际 {len(starts)}"
    drop = set()
    kept = 0
    for i, name in starts:
        depth = 0
        j = i
        while True:
            depth += lines[j].count("<section")
            depth -= lines[j].count("</section>")
            if depth <= 0:
                break
            j += 1
        if name == route:
            kept += 1
            # 静态可见：被保留的 panel 直接带 active（JS 起来之前/失败时正文也在）
            lines[i] = lines[i].replace('class="panel"', 'class="panel active"')
        else:
            drop.update(range(i, j + 1))
    assert kept == 1, f"{route}: 保留 panel {kept} 个（应为 1）"
    out = [l for i, l in enumerate(lines) if i not in drop]
    # 顶栏静态 active 从 pulse 挪到本页 tab（找不到本页顶栏 tab 就只摘 pulse 的，JS 会补）
    joined = "\n".join(out)
    joined = joined.replace('class="tab active" data-panel="pulse"', 'class="tab" data-panel="pulse"', 1)
    joined = joined.replace(f'class="tab" data-panel="{route}"', f'class="tab active" data-panel="{route}"', 1)
    return joined


def main():
    only = set(sys.argv[1:])
    if only - set(ROUTES):
        sys.exit(f"未知路由：{only - set(ROUTES)}")
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    targets = {r: v for r, v in ROUTES.items() if not only or r in only}
    for route, (title, desc) in targets.items():
        out = ROOT / f"{route}.html"
        out.write_text(patch(src, route, title, desc), encoding="utf-8")
        print(f"生成 {out.name}（仅含 panel-{route}）")
    write_sitemap()
    print(f"共 {len(targets)} 页")


if __name__ == "__main__":
    main()
