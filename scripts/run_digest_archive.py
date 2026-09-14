#!/usr/bin/env python3
"""判读档案生成器的定时入口（2026-08-24 接线，launchd: com.klay.digest-archive）

为什么是 launchd 而不是 daily.yml：
    build_digest_archive.py 取材自 ../../生意与起号/（稿件 + 卡片）。那个目录不在公开仓里、
    自身也没有远端 ⇒ GitHub Actions 的 runner 上根本没有源文件，挂进 CI 只会每天跑出 0 页。
    稿件在本地，定时也只能在本地。

为什么必须写成 Python 而不是 .sh：
    macOS TCC 挡 launchd 下的 /bin/bash 读 ~/Documents —— 实测 `/bin/bash <脚本>` 直接
    "Operation not permitted"。而 /opt/homebrew/bin/python3 有权限（chronicle-analytics /
    buttondown-hygiene 等作业一直这么跑）。所以入口必须是那个解释器 + 一个 .py。

为什么必须有这个东西：
    脚本从 2026-08-18 起因稿件格式漂移抛异常，站上档案停更两周无人知，直到 Klay 自己发现
    08-21 那期没上站。当时全仓 grep 零处 run —— **规矩没变成自动跑的动作，就等于没有。**
"""
import datetime, os, shutil, subprocess, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIZ = os.path.join(os.path.dirname(os.path.dirname(REPO)), "生意与起号")
LOG = os.path.join(REPO, "data", "_digest_archive.log")
# 2026-08-28 Klay 拍板加 sitemap.xml：本工具加了 digest 页却从不重跑路由生成器 ⇒ sitemap
# 失账 ⇒ nightly 撞 check_route_pages 红（08-22 f390123 / 08-28 各一次，同族二犯）。
# 末尾带跑 build_route_pages（见 main 内），其产物 sitemap.xml 随本轮一起提交。
WATCH = ["digest", "feed.xml", "data/digest_archive.json", "sitemap.xml"]
# launchd 的 PATH 极简：cwebp 在 homebrew 里，不补这一行 to_webp 必炸
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "/usr/bin:/bin")


def say(msg):
    line = f"[{datetime.datetime.now().astimezone():%Y-%m-%d %H:%M:%S %z}] {msg}"
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)


def notify(title, body):
    """macOS 通知：launchd 那班的红只落在退出码里没人看（「失败时的出口是谁在看」）。失败只记不抛。"""
    try:
        subprocess.run(["osascript", "-e", f'display notification "{body}" with title "{title}"'],
                       capture_output=True, timeout=10)
    except Exception as e:
        print(f"（通知没发出去：{e}）")


def git(*args, **kw):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, **kw)


def main():
    say("── 开始")
    if not os.path.isdir(BIZ):
        say(f"❌ 稿件源不存在：{BIZ}（外接盘没挂？）本次跳过")
        return 1
    if not shutil.which("cwebp"):
        say("❌ 找不到 cwebp")
        return 1

    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "build_digest_archive.py")],
                       cwd=REPO, capture_output=True, text=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(r.stdout + r.stderr)
    if r.returncode != 0:
        tail = " ⏎ ".join((r.stdout + r.stderr).strip().splitlines()[-4:])[:400]
        say(f"❌ 生成器退出码 {r.returncode}（2=缩水闸/冻结源不见 · 其它多半是周报稿件格式漂移）：{tail}")
        notify("判读档案没上站", f"生成器退出码 {r.returncode}，看 data/_digest_archive.log")
        return r.returncode

    # 🔴 2026-08-28（Klay 拍板·sitemap 失账同族二犯后）：加完页重跑路由生成器，sitemap 当晚入账。
    #    ⚠️ 前置依赖：index 源与产物已同步（0da9848 拆雷）——源脏时生成器会回滚定稿，
    #    所以要有源漂移守门。守门的两条铁律（首版实犯换来的，同晚被自己咬）：
    #      ① 检测与还原**只许**落在生成器自己的产物清单内（从它的 stdout「生成 X」逐行解析，
    #         不硬编码——清单会长）；② 绝不 `git checkout -- .`：多 agent 共仓，全仓还原会
    #         吞掉别人（和自己）的未提交工作——首版就把本工具自己的未提交改动斩了。
    g = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "build_route_pages.py")],
                       cwd=REPO, capture_output=True, text=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(g.stdout + g.stderr)
    if g.returncode != 0:
        say(f"❌ 路由生成器退出码 {g.returncode} —— sitemap 本轮没入账，会在 nightly 撞红")
        return g.returncode
    drift = False
    produced = [ln.split("生成 ", 1)[1].split("（")[0].strip()
                for ln in g.stdout.splitlines() if ln.startswith("生成 ") and ".html" in ln]
    if produced:
        dirty_pages = [l[3:].strip() for l in
                       git("status", "--porcelain", "--", *produced).stdout.splitlines() if l.strip()]
        if dirty_pages:
            git("checkout", "--", *dirty_pages)      # 只还原路由页产物，别的一概不碰
            say("🔴 路由页产物被生成器改写（index 源疑似又与产物漂移，有人只改产物没改源）。"
                "已仅还原这些页、不提交，sitemap 照常入账：" + " ".join(dirty_pages[:6]))
            drift = True   # 红要落到退出码上（launchd 才看得见），但 sitemap 记账照走

    # 变化判定必须**排除纯时间戳漂移**：digest_archive.json 的 generated_at 每跑一次就变，
    # 不排掉就天天产生一笔「什么都没变」的提交——既是噪声，也会把 GitHub contributions
    # 灌成机器数（§125：测量工具自己制造被测指标）。
    diff = git("diff", "--", *WATCH).stdout.splitlines()
    real = [l for l in diff
            if l[:1] in "+-" and not l.startswith(("+++", "---")) and "generated_at" not in l]
    untracked = [l for l in git("ls-files", "--others", "--exclude-standard", "digest")
                 .stdout.splitlines() if l.strip()]
    if not real and not untracked:
        say("✅ 跑通，无实质变化（只有 generated_at 时间戳漂移），不提交")
        git("checkout", "--", "data/digest_archive.json")
        return 1 if drift else 0

    n = len([l for l in git("status", "--porcelain", "--", *WATCH).stdout.splitlines() if l.strip()])
    git("add", "-A", *WATCH)
    stamp = datetime.date.today().isoformat()
    c = git("-c", "user.name=Klay", "-c", "user.email=klaywang24@gmail.com",
            "commit", "-q", "-m", f"digest 档案自动同步（{stamp}）：{n} 处变化")
    if c.returncode != 0:
        say(f"❌ commit 失败：{(c.stderr or c.stdout).strip()[:200]}")
        return 1
    p = git("push", "-q", "origin", "HEAD")
    if p.returncode != 0:
        say(f"❌ push 失败（钥匙串取不到凭据？）本地已 commit，下次会一起推："
            f"{(p.stderr or p.stdout).strip()[:200]}")
        return 1
    say(f"✅ 已提交并推送：{n} 处变化 · {git('log', '-1', '--format=%h').stdout.strip()}")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
