"""项目进展取数：按项目名册读各仓库某一天的进展，给 Hermes 回答“今天各项目怎么样”用（只读，只用标准库）。

用法：python3 progress_data.py [--date YYYY-MM-DD] [--project 项目id或别名]
读：每个仓库当天的提交（所有本地分支，按分支归）、主分支停在哪、主目录未提交文件数、
    CURRENT 最上面一节的标题和“下一步”、当天的派单台账（~/.hermes/dispatch），
    名册里有 github 的项目再用 gh 取当天新开、合并、关闭的 PR 和新开、关闭的 issue。
输出：Markdown 到 stdout。某个仓库或它的 GitHub 读不了只标在该项目下；名册读不了输出「取数失败」，退出码 1。
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROSTER = Path(os.environ.get("DT_ROSTER", "~/Documents/ChatGPT/个人/work/digital-twin/projects.json")).expanduser()
DISPATCH = Path(os.environ.get("DT_DISPATCH", "~/.hermes/dispatch")).expanduser()
CLIP = 200  # “下一步”最多保留多少字
NEXT = re.compile(r"下一步\**\s*[：:|]|^#+.*下一步")  # “下一步：…”“**下一步**：…”、表格“| 唯一下一步 |”、标题“## 唯一下一步”


def git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200])
    return r.stdout


def commits(path, main, day):
    """当天的提交，按分支归；一个提交只记在第一个含它的分支下，主分支优先。"""
    since, until = f"{day} 00:00", f"{day + dt.timedelta(days=1)} 00:00"
    branches = git(path, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()
    branches.sort(key=lambda b: b != main)
    seen, out = set(), {}
    for b in branches:
        log = git(path, "log", b, f"--since={since}", f"--until={until}",
                  "--format=%h%x09%cd%x09%s", "--date=format:%H:%M")
        for line in log.splitlines():
            h, t, s = line.split("\t", 2)
            if h not in seen:
                seen.add(h)
                out.setdefault(b, []).append(f"{t} `{h}` {s}")
    return out


class GitHub:
    """用 gh 取当天的 PR 和 issue。连不上一次后，后面的项目不再等超时，直接标同一个原因。"""
    def __init__(self, day):
        self.day, self.down = day, None
        self.exe = shutil.which("gh", path=os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"]))

    def _list(self, repo, kind, fields):
        since = self.day - dt.timedelta(days=1)  # GitHub 按 UTC 算日期，往前放一天再按北京时间筛
        r = subprocess.run([self.exe, kind, "list", "-R", repo, "--state", "all", "--limit", "100",
                            "--search", f"updated:>={since}", "--json", fields],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout).strip()
            raise RuntimeError(msg.splitlines()[-1][:150] if msg else f"gh 退出码 {r.returncode}")
        return json.loads(r.stdout)

    def on_day(self, ts):
        return bool(ts) and dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date() == self.day

    def activity(self, repo):
        """返回 [(类别, ["#号 标题", ...]), ...]，只含有内容的类别；取不到抛 RuntimeError。"""
        if self.down:
            raise RuntimeError(self.down)
        try:
            if not self.exe:
                raise RuntimeError("找不到 gh 命令")
            prs = self._list(repo, "pr", "number,title,createdAt,mergedAt,closedAt")
            issues = self._list(repo, "issue", "number,title,createdAt,closedAt")
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as e:
            self.down = str(e) or type(e).__name__
            raise RuntimeError(self.down)
        def pick(items, test):
            return [f"#{x['number']} {x['title']}" for x in sorted(items, key=lambda x: x["number"]) if test(x)]
        groups = [
            ("PR 新开", pick(prs, lambda x: self.on_day(x.get("createdAt")))),
            ("PR 合并", pick(prs, lambda x: self.on_day(x.get("mergedAt")))),
            ("PR 关闭（未合并）", pick(prs, lambda x: not x.get("mergedAt") and self.on_day(x.get("closedAt")))),
            ("issue 新开", pick(issues, lambda x: self.on_day(x.get("createdAt")))),
            ("issue 关闭", pick(issues, lambda x: self.on_day(x.get("closedAt")))),
        ]
        return [(k, v) for k, v in groups if v]


def plain(s, clip=CLIP):
    s = re.sub(r"^[#|\-\s]+|[|\s]+$", "", s)
    s = re.sub(r"\s*\|\s*", "：", s, count=1)  # 表格行“| 唯一下一步 | …”变成“唯一下一步：…”
    s = s.replace("**", "")
    return s if len(s) <= clip else s[:clip] + "…"


def next_step(lines):
    """CURRENT 的“下一步”：先在最上面一节里找，再看文件头（第一个二级标题之前），最后才认单独成节的“## 唯一下一步”。
    不再从全文第一处匹配：旧的节、文件头里过期的“唯一下一步”会把当天写的下一步盖掉（#110）。"""
    starts = [i for i, x in enumerate(lines) if x.startswith("## ")]
    top = (starts[0], starts[1] if len(starts) > 1 else len(lines)) if starts else (0, len(lines))
    head = (0, starts[0] if starts else len(lines))
    rest = [(s, s + 1) for s in starts if re.match(r"^#+.*下一步", lines[s])]
    for lo, hi in (top, head, *rest):
        for i in range(lo, hi):
            x = lines[i]
            if NEXT.search(x):
                if x.startswith("#"):  # “## 唯一下一步”这种标题，取下面第一行正文
                    x = next((y for y in lines[i + 1:] if y.strip()), x)
                s = re.sub(r"^.*?下一步\s*[：:]\s*", "", plain(x, clip=10**9))  # 先去掉“遗留 / 下一步：”“更新：……唯一下一步：”这类前缀，再截断
                return (s if len(s) <= CLIP else s[:CLIP] + "…") or None
    return None


def touched_on(path, f, day):
    """CURRENT 那天改过没有：有提交就按最后一次提交的日期（git pull 会刷新文件时间，靠不住），从没提交过才看文件时间。"""
    r = subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%cd", "--date=short", "--", f.name],
                       capture_output=True, text=True)
    stamp = r.stdout.strip() if r.returncode == 0 else ""
    return (stamp == day.isoformat()) if stamp else dt.date.fromtimestamp(f.stat().st_mtime) == day


def current(path, day):
    """CURRENT 最上面一节的标题、“下一步”、这天有没有改过。"""
    f = Path(path) / "CURRENT.md"
    if not f.exists():
        return None
    lines = f.read_text(encoding="utf-8").splitlines()
    head = next((plain(x, 80) for x in lines if x.startswith("## ")), "（没有二级标题）")
    return head, next_step(lines) or "（没写下一步）", touched_on(path, f, day)


def orders(day):
    out = []
    for f in sorted(DISPATCH.glob("*/order.json")):
        try:
            o = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if any(str(o.get(k, "")).startswith(day.isoformat()) for k in ("created", "updated")):
            out.append(o)
    return out


def pick(projects, key):
    key = key.lower()
    return [p for p in projects if key == p["id"].lower() or key in (a.lower() for a in p.get("aliases", []))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="哪一天，默认今天")
    ap.add_argument("--project", help="只看一个项目（id 或别名）")
    a = ap.parse_args()
    day = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    try:
        projects = json.loads(ROSTER.read_text())["projects"]
    except (OSError, ValueError, KeyError) as e:
        print(f"取数失败：读不了项目名册 {ROSTER}（{e}）")
        sys.exit(1)
    if a.project:
        projects = pick(projects, a.project)
        if not projects:
            print(f"取数失败：名册里没有项目「{a.project}」")
            sys.exit(1)

    todays = orders(day)
    hub = GitHub(day)
    print(f"# 项目进展 · {day}（{'截至 ' + dt.datetime.now().strftime('%H:%M') if day == dt.date.today() else '全天'}）")
    quiet = []
    for p in projects:
        path = os.path.expanduser(p["path"])
        mine = [o for o in todays if o.get("project") == p["id"]]
        try:
            cm = commits(path, p["main"], day)
            branch = git(path, "branch", "--show-current").strip() or "（游离 HEAD）"
            head = git(path, "log", "-1", "--format=%h %s", p["main"]).strip()
            dirty = len(git(path, "status", "--porcelain").splitlines())
        except (RuntimeError, OSError) as e:
            print(f"\n## {p['name']}（{p['id']}）\n\n- 读不了这个仓库：{e}")
            continue
        cur = current(path, day)
        gh_items, gh_down = [], None
        if p.get("github"):
            try:
                gh_items = hub.activity(p["github"])
            except RuntimeError as e:
                gh_down = str(e)
        if not cm and not mine and not gh_items and not (cur and cur[2]):
            quiet.append(p["name"] + ("（GitHub 取不到）" if gh_down else ""))
            continue
        print(f"\n## {p['name']}（{p['id']}）\n")
        n = sum(len(v) for v in cm.values())
        print(f"- 今天提交：{n} 个" + ("" if n else "（没有）"))
        for b, items in cm.items():
            print(f"  - 分支 `{b}`：")
            for c in items:
                print(f"    - {c}")
        print(f"- 主分支 `{p['main']}`：{head}")
        print(f"- 主目录检出 `{branch}`，未提交文件 {dirty} 个")
        if cur:
            print(f"- CURRENT 最新一节：{cur[0]}{'（今天改过）' if cur[2] else ''}")
            print(f"- CURRENT 下一步：{cur[1]}")
        else:
            print("- 无 CURRENT")
        for o in mine:
            print(f"- 派单 {o['no']}：{o.get('tool')} · {'写入' if o.get('mode') == 'write' else '只读'} · {o.get('status')}")
        if gh_down:
            print(f"- GitHub 取不到：{gh_down}")
        elif p.get("github"):
            print("- 今天 PR 和 issue：" + ("" if gh_items else "没有"))
            for k, v in gh_items:
                print(f"  - {k}：{'；'.join(v)}")
    if quiet:
        print(f"\n## 今天没动静\n\n- {'、'.join(quiet)}：没有提交、没有派单、CURRENT 没改")
    print(f"\n来源：本机 git、派单台账 {DISPATCH} 与 GitHub（gh）")


if __name__ == "__main__":
    main()
