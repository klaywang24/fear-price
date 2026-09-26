# -*- coding: utf-8 -*-
"""禁词闸：「恐慌指数」只允许归属性用法（前面紧跟 VIX）。

为什么存在（2026-08-07 定案）：
「恐慌指数」是 VIX 在中文世界的俗名，与本站「恐惧的标价指数」不是一回事。
裁定：**禁止用它指称本站的任何指数，但允许归属性提及 VIX 本身**
（例：行情条标签「VIX 恐慌指数」）。

判据（机器可判，不靠自觉）：
    「恐慌指数」之前紧邻的 4 个字符里必须出现 "VIX"，否则报红。
    窗口取 4 是为了只放行紧邻修饰（"VIX "/"VIX（"/"VIX的"），
    而挡住「VIX 很高，恐慌指数也……」这类真违规。

用法：
    python3 tools/check_banned_terms.py        # 退出码 0=过 1=报红
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TERM = "恐慌指数"
ALLOW_WITHIN = 4      # 紧邻窗口（字符数）
ALLOW_ANCHOR = "VIX"  # 窗口内必须出现它


def targets():
    """对外可见的文本面：页面、脚本里的字符串、机器可读入口、仓库门面。"""
    # 2026-08-11：归档页与 feed 原本在扫描范围外＝闸对新内容失明（实测：它报「违规 0」
    # 的同时，digest/ 里有 2 处裸用）。归档是「当时发出去的原话」，按勘误准则旧文不回改
    # ⇒ 这里把它们纳入扫描但只作 INFO 播报，不 fail 构建；非归档路径照旧硬失败。
    for p in sorted(ROOT.glob("*.html")):
        yield p
    for p in sorted(ROOT.glob("t/*.html")):   # 2026-09-26 个股页
        yield p
    # 归档面：真的 yield 出去才叫扫（2026-08-11 首版只往集合里塞路径没 yield，
    # 闸照旧报「24 个文件·违规 0」——自造 bug 只在真跑时现形）
    for p in sorted(ROOT.glob("digest/*.html")) + [ROOT / "feed.xml"]:
        if p.exists():
            ARCHIVE_PATHS.add(str(p))
            yield p
    for p in sorted((ROOT / "js").glob("*.js")):
        yield p
    for name in ("llms.txt", "README.md", "README.zh.md"):
        p = ROOT / name
        if p.exists():
            yield p


ARCHIVE_PATHS = set()

def main():
    files = 0
    hits = 0
    ok = 0
    bad = []
    archived = []

    for path in targets():
        files += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        is_archive = str(path) in ARCHIVE_PATHS
        for m in re.finditer(TERM, text):
            hits += 1
            window = text[max(0, m.start() - ALLOW_WITHIN):m.start()]
            if ALLOW_ANCHOR in window:
                ok += 1
                continue
            line = text.count("\n", 0, m.start()) + 1
            ctx = text[max(0, m.start() - 24):m.start() + len(TERM) + 12]
            ctx = ctx.replace("\n", " ")
            # 归档＝当时发出去的原话，按勘误准则旧文不回改 ⇒ 只播报不失败。
            # 但**必须播报**：不播报就等于闸又瞎了一次。
            (archived if is_archive else bad).append((path.relative_to(ROOT), line, ctx))

    if archived:
        print(f"INFO: 往期归档里有 {len(archived)} 处裸用「{TERM}」（原文如此，不回改）：")
        for rel, line, ctx in archived:
            print(f"  {rel}:{line}  …{ctx}…")

    if bad:
        print(f"RED: 扫 {files} 个文件，「{TERM}」共 {hits} 处，"
              f"归属性合法 {ok} 处，**违规 {len(bad)} 处**：")
        for rel, line, ctx in bad:
            print(f"  {rel}:{line}  …{ctx}…")
        print(f"  修法：指本站指数时改用其正式名（K 指数 / 恐惧的标价指数）；"
              f"确实在说 VIX 就把 VIX 紧挨着写在前面。")
        return 1

    print(f"OK: 扫 {files} 个文件，「{TERM}」共 {hits} 处 = "
          f"归属性 {ok} + 归档原文 {len(archived)}，**新增违规 0**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
