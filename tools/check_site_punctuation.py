# -*- coding: utf-8 -*-
"""对外标点闸：直角引号「」· 弯引号“”· 中文破折号 —— 一律不许出现在可见文字里。

为什么存在（2026-08-18 Klay 令，见记忆 digest-copy-voice）：
日更那条线早有 `check_redline.py` 管这件事，**站上一直没有**。
实证：f13.html 上线时带着 14 个直角引号，其中 2 处在导语正文，
在线上挂了整段时间没人发现，最后是 Klay 自己看出来的。
日更管住了、站上没管住，同一条红线漏了半边。

判据（只扫可见文字层，与日更那道闸同口径）：
    剥掉 HTML 注释 / <style> / <script>，只取 `>…<` 之间的文字。
    ⇒ CSS 注释里的破折号、JS 里的字符串键名都不算违规；
      读者看不见的字不是对外文字。这条口径今天刚踩过一次：
      拿 raw grep 数整库会得到六万多个 ASCII 直引号，全是语法。

范围（与禁词闸同一分层，理由也同一条）：
    · 根目录 *.html          → 硬失败
    · digest/*.html、feed.xml → 只播报不失败
      归档是当时发出去的原话，按承诺·机制·勘误准则旧文不回改。

用法：
    python3 tools/check_site_punctuation.py        # 0=过 1=报红 2=量尺失效
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BAD = {"「": "直角引号", "」": "直角引号", "“": "弯引号", "”": "弯引号", "——": "中文破折号"}
ARCHIVE = set()
TICKER = set()
VB, VE = "<!--verbatim-->", "<!--/verbatim-->"


def visible(html: str) -> str:
    """只留读者看得见的字（与 每日 digest/tools/check_redline.py 同口径）。"""
    h = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    h = re.sub(r"<style\b.*?</style>", "", h, flags=re.S | re.I)
    h = re.sub(r"<script\b.*?</script>", "", h, flags=re.S | re.I)
    return "".join(re.findall(r">([^<]*)<", h))


def targets():
    for p in sorted(ROOT.glob("*.html")):
        yield p
    for p in sorted(ROOT.glob("digest/*.html")) + [ROOT / "feed.xml"]:
        if p.exists():
            ARCHIVE.add(str(p))
            yield p
    # 2026-09-26：个股页 t/*.html。模板文字硬失败；台账原文用 <!--verbatim-->…<!--/verbatim--> 圈起，
    # 圈内按归档口径只播报（立案原文原样存证不改），见 main() 里的拆分。
    for p in sorted(ROOT.glob("t/*.html")):
        TICKER.add(str(p))
        yield p


def main():
    files = hard = soft = 0
    bad, archived = [], []
    for path in targets():
        files += 1
        raw = path.read_text(encoding="utf-8", errors="replace")
        parts = [(raw, str(path) in ARCHIVE)]
        if str(path) in TICKER:   # 个股页：圈外硬失败、圈内（台账原文）只播报
            inside = "".join(f">{m}<" for m in re.findall(re.escape(VB) + "(.*?)" + re.escape(VE), raw, re.S))
            outside = re.sub(re.escape(VB) + ".*?" + re.escape(VE), "", raw, flags=re.S)
            parts = [(outside, False), (inside, True)]
        for chunk, soft_part in parts:
          text = visible(chunk)
          for ch, name in BAD.items():
            n = text.count(ch)
            if not n:
                continue
            rel = path.relative_to(ROOT)
            sample = ""
            m = re.search(r".{0,18}" + re.escape(ch) + r".{0,18}", text)
            if m:
                sample = m.group().replace("\n", " ").strip()
            line = f"{rel}：{name} ×{n}   …{sample}…"
            if soft_part:
                archived.append(line); soft += n
            else:
                bad.append(line); hard += n

    if files == 0:                      # 量尺先自证有输出，清单为空不许报绿
        print("🔴 一个文件都没扫到 —— 目录结构变了？本闸此刻无效")
        return 2

    print(f"扫了 {files} 个文件（其中归档 {len(ARCHIVE)} 个只播报不失败；个股页 {len(TICKER)} 个的台账原文只播报）")
    if archived:
        print(f"ℹ️ 归档面 {soft} 处（旧文不回改，不失败）：")
        for b in archived:
            print("   ·", b)
    if bad:
        print(f"🔴 对外可见文字里有 {hard} 处违规标点：")
        for b in bad:
            print("   ·", b)
        print("   改法：中文不靠引号也读得通，直接去掉；破折号改用逗号或句号断句。")
        print("   ⚠️ 若该串同时是 js/i18n.js 的查表 key，必须逐字节一起改，否则英文层 fallback 成中文。")
        return 1
    print("✅ 对外可见文字标点干净")
    return 0


if __name__ == "__main__":
    sys.exit(main())
