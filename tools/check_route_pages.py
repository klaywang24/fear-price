# -*- coding: utf-8 -*-
"""路由页判重闸 + sitemap 对账（§63，2026-08-17）。

为什么存在：07-30 的 §49 路由改造只换了 head 六行身份，正文 17 份逐字节相同。
Google 判重看正文不看 head —— canonical 声明了它也不采信，08-08 GSC 邮件
「重复网页，Google 选择的规范网页与用户指定的不同」实锤。修复方案是每个
路由页只留自己的 panel（build_route_pages.py §63 版）。

本闸把「不许再回到那个状态」变成机器判据，替代 §49 那条没人执行的
「两三周后 site: 搜一下」人肉判据：

  ① 每个路由页恰好含 1 个 panel，且是自己的那个 —— 多一个都算重复正文
  ② canonical / og:url 必须自指
  ③ 任意两个路由页 <body> 不得逐字节相同
  ④ sitemap.xml 与「实际应收录页面清单」精确对账（清单唯一实现在
     build_route_pages.expected_sitemap_urls，本闸只调用不复制 —— 一处判据一个实现）
  ⑤ noindex 转化页绝不许出现在 sitemap
  ⑥ index.html 的导航 tab 必须是带 href 的 <a> —— 爬虫沿内链发现路由页的唯一通道

负向实证（RULES：新闸必须先用坏样本证明它会报红）：
2026-08-17 对修复前的仓跑本闸 → 16 页各含 17 个 panel + sitemap 缺 digest，
共 33 条红，随后修复转绿。凡本闸绿着而 GSC 又报重复，先怀疑闸瞎了再怀疑 Google。
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_route_pages import BASE, ROUTES, expected_sitemap_urls  # noqa: E402

NOINDEX_PAGES = ["pay", "welcome", "check-inbox", "confirmed"]
errors = []


def err(msg):
    errors.append(msg)
    print(f"🔴 {msg}")


def panel_ids(html):
    return re.findall(r'id="panel-([a-z]+)"', html)


# ① ② 每个路由页：只含自己的 panel + canonical 自指
for route in ROUTES:
    f = ROOT / f"{route}.html"
    if not f.exists():
        err(f"{route}.html 不存在（ROUTES 有它，盘上没有）")
        continue
    html = f.read_text(encoding="utf-8")
    ids = panel_ids(html)
    if ids.count(route) != 1:
        err(f"{route}.html：自己的 panel 出现 {ids.count(route)} 次（应为 1）")
    foreign = [i for i in ids if i != route]
    if foreign:
        err(f"{route}.html：混入 {len(foreign)} 个别人的 panel（{', '.join(sorted(set(foreign)))}）—— 这就是 GSC 判重的原料")
    want = f'<link rel="canonical" href="{BASE}/{route}">'
    if want not in html:
        err(f"{route}.html：canonical 不自指（找不到 {want}）")
    if f'<meta property="og:url" content="{BASE}/{route}">' not in html:
        err(f"{route}.html：og:url 不自指")

# ③ 任意两页 body 不得相同（index 也参与：路由页不许和首页正文一模一样）
bodies = {}
for f in [ROOT / "index.html"] + [ROOT / f"{r}.html" for r in ROUTES]:
    if f.exists():
        m = re.search(r"<body>.*</body>", f.read_text(encoding="utf-8"), re.S)
        bodies[f.name] = m.group(0) if m else ""
names = list(bodies)
for i, a in enumerate(names):
    for b in names[i + 1:]:
        if bodies[a] and bodies[a] == bodies[b]:
            err(f"{a} 与 {b} 的 <body> 逐字节相同 —— 重复正文")

# ④ ⑤ sitemap 对账
sitemap = (ROOT / "sitemap.xml").read_text(encoding="utf-8")
in_map = set(re.findall(r"<loc>([^<]+)</loc>", sitemap))
expected = set(expected_sitemap_urls(ROOT))
for u in sorted(expected - in_map):
    err(f"sitemap 缺 {u}（页面存在却没申报 —— digest 孤儿页就是这么来的）")
for u in sorted(in_map - expected):
    err(f"sitemap 多 {u}（申报了不该收录/不存在的页）")
for p in NOINDEX_PAGES:
    if f"{BASE}/{p}" in sitemap:
        err(f"sitemap 混入 noindex 转化页 /{p}")

# ⑥ 导航必须可爬：所有 data-panel tab 都得是带 href 的 <a>
index_html = (ROOT / "index.html").read_text(encoding="utf-8")
for m in re.finditer(r"<(\w+)[^>]*class=\"tab[ \"][^>]*data-panel=\"([a-z]+)\"[^>]*>", index_html):
    tag, panel = m.group(1), m.group(2)
    if tag != "a" or 'href="' not in m.group(0):
        err(f'index.html：tab data-panel="{panel}" 是 <{tag}> 不是带 href 的 <a> —— 爬虫看不见这条内链')

# ⑦ JSON-LD 语法闸（2026-08-25 加）
# 为什么要有：GSC 08-25 对 /leaps 报「无法解析的结构化数据：含有语法错误」，
# 当时靠人手动全站扫一遍才确认现网是好的（告警指向部署前的旧抓取）。
# 结构化数据坏掉不会让页面报错、不会让别的闸变红——它只是静默地让富媒体结果消失，
# 而 GSC 要隔周才告诉你。⇒ 这种「坏了没人知道」的东西必须由闸每天看，不能靠人记得。
# 判据是逐块 json.loads：Google 的解析器只比它更严，本闸绿不保证 Google 收，
# 但本闸红就一定是我们自己的语法错。
ld_pages = sorted(set(
    [ROOT / "index.html"] + [ROOT / f"{r}.html" for r in ROUTES] +
    [ROOT / f"{n}.html" for n in ("options", "f13", "kapx", "fear-price")]
))
ld_checked = 0
for p in ld_pages:
    if not p.exists():
        continue
    for i, block in enumerate(re.findall(
            r'<script type="application/ld\+json">(.*?)</script>',
            p.read_text(encoding="utf-8"), re.S)):
        ld_checked += 1
        try:
            json.loads(block)
        except json.JSONDecodeError as e:
            err(f"{p.name} 第 {i+1} 个 ld+json 块语法错：{e}")

# ⑧ sitemap / canonical / hreflang 不许出现 .html 形态（2026-09-07 加）
# 为什么要有：Cloudflare Pages 把 /x.html 一律 308 到 /x（无扩展）。08-25 把 EN 归档索引以
# /digest/index.en.html 形态写进 sitemap，且页内 canonical 也写成 .html ⇒ sitemap 条目是跳转页、
# 落地页 /digest/index.en 的 canonical 又指回那个跳转页，成环。GSC 09-06 两封邮件：
# 「站点地图中的网页会自动重定向」+「备用网页（有适当的规范标记）」。本闸把「URL 形态必须是
# Pages 实际 200 的那个」变成机器判据：sitemap 的 <loc>、digest 页的 canonical 与 hreflang 三处
# 都不许带 .html。负向实证：对修复前的仓跑本闸 → 红（见 §77），修复后转绿。
for u in sorted(in_map):
    if u.endswith(".html"):
        err(f"sitemap 条目带 .html：{u} —— Pages 会 308 到无扩展形态，Google 记成「网页会自动重定向」")
for f in sorted((ROOT / "digest").glob("*.html")):
    html = f.read_text(encoding="utf-8")
    stem = "" if f.name == "index.html" else f.name[:-5]
    want = f'<link rel="canonical" href="{BASE}/digest/{stem}">'
    if want not in html:
        err(f"digest/{f.name}：canonical 不是 Pages 实际 200 的形态（应为 {BASE}/digest/{stem}）")
    for alt in re.findall(r'<link rel="alternate" hreflang="[^"]+" href="([^"]+)"', html):
        if alt.endswith(".html"):
            err(f"digest/{f.name}：hreflang 指向带 .html 的 {alt} —— 落地会 308，语言对儿对不上")

if errors:
    print(f"\n共 {len(errors)} 条红。路由页正文重复/sitemap 失账会直接导致 GSC 拒收，修完再提交。")
    sys.exit(1)
print(f"✅ 路由页判重闸全绿：{len(ROUTES)} 路由页各含唯一 panel，canonical 自指，sitemap {len(expected)} 条对账一致，导航内链可爬")
