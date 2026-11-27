#!/usr/bin/python3
"""看一个 GitHub 仓库有没有新 issue，交给中台（第一条自动规则「跟进 GitHub Issue · 只分析」，常浩 2026-11-27 定）。

中台的命令型 Agent 每 5 分钟跑一次本脚本（规则见 ops/autoloop_rules/dt-sandbox-issue-watch.json）：
  1. 把只读镜像 --mirror 更新到远端默认分支（没有就克隆）：分析 issue 的 Agent 只读这份代码，不改、不提交；
  2. 不带凭据读 GitHub 公开接口，列开着的 issue（去掉 PR），只留 --since 那天（北京时间 0 点）以后开的、
     作者在 --author 名单里的（公开仓库谁都能开 issue，正文是别人写的资料；默认只认仓库主人，挡掉陌生人往 Agent 里塞话）；
  3. 按中台的回执格式打印事件（```agent-autoloop 代码块，events），id 是 issue-<号>，中台按 id 去重，同一张 issue 只处理一次。
没有新 issue 也照样打印空的 events，不花 Token。任何一步失败退出码 1、写一句原因，下一个 5 分钟再试。

联网走环境里的 https_proxy（dt-worker 只能经过滤代理 8090 出去，命令里显式给）；不读、不写任何凭据。
用法：issue_watch.py <owner/repo> --mirror <目录> [--since YYYY-MM-DD] [--author 登录名 …]
只用标准库，/usr/bin/python3（3.9）能跑。
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
TZ = dt.timezone(dt.timedelta(hours=8))
SUMMARY_MAX = 200
DETAIL_MAX = 4000
TIMEOUT = 30


class Fail(Exception):
    pass


def cut(text, n):
    """按字符截，不截出半个字。"""
    return "".join(list(text or "")[:n])


def since_utc(day):
    """北京时间那天 0 点，换成 UTC（GitHub 的 created_at 是 UTC）。"""
    try:
        d = dt.date.fromisoformat(day)
    except ValueError:
        raise Fail(f"--since 要写成 YYYY-MM-DD，不认 {day!r}") from None
    return dt.datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(dt.timezone.utc)


def created(issue):
    # 3.9 的 fromisoformat 不认结尾的 Z（本仓库坑 M5 #5）
    return dt.datetime.fromisoformat(str(issue["created_at"]).replace("Z", "+00:00"))


def fetch(repo, opener=None):
    url = f"{API}/repos/{repo}/issues?state=open&per_page=50&sort=created&direction=desc"
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "dt-issue-watch"})
    try:
        with (opener or urllib.request.build_opener()).open(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise Fail(f"GitHub 回了 HTTP {e.code}（{repo}）") from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Fail(f"读不到 GitHub（{repo}）：{e}") from None
    if not isinstance(data, list):
        raise Fail(f"GitHub 回的不是列表（{repo}）")
    return data


def pick(issues, since, authors):
    out = []
    for i in issues:
        if not isinstance(i, dict) or "pull_request" in i:
            continue
        login = (i.get("user") or {}).get("login")
        if authors and login not in authors:
            continue
        if since and created(i) < since:
            continue
        out.append(i)
    return sorted(out, key=lambda i: i["number"])


def events(issues):
    return [{"id": f"issue-{i['number']}", "type": "issues.opened", "subject": f"#{i['number']}",
             "summary": cut(i.get("title"), SUMMARY_MAX),
             "detail": cut(f"作者：{(i.get('user') or {}).get('login')}\n\n{i.get('body') or ''}", DETAIL_MAX),
             "link": i.get("html_url")} for i in issues]


def git(*args, cwd=None):
    p = subprocess.run(["/usr/bin/git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if p.returncode:
        raise Fail(f"git {args[0]} 失败：{p.stderr.decode('utf-8', 'replace').strip()[:200]}")
    return p.stdout.decode("utf-8", "replace").strip()


def refresh(repo, mirror):
    """只读镜像对齐远端默认分支：分析的 Agent 只读，这里丢掉镜像里任何本地改动也不丢东西。"""
    mirror = Path(mirror).expanduser()
    if not (mirror / ".git").is_dir():
        mirror.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "-q", f"https://github.com/{repo}.git", str(mirror))
        return git("rev-parse", "--short", "HEAD", cwd=mirror)
    git("fetch", "-q", "--prune", "origin", cwd=mirror)
    head = git("rev-parse", "--abbrev-ref", "origin/HEAD", cwd=mirror)
    git("reset", "-q", "--hard", head, cwd=mirror)
    git("clean", "-qfdx", cwd=mirror)
    return git("rev-parse", "--short", "HEAD", cwd=mirror)


def render(repo, commit, evs):
    return "\n".join([f"看了 {repo} 开着的 issue：符合条件的 {len(evs)} 张，处理过的中台不再派（代码镜像在 {commit}）", "",
                      "```agent-autoloop", json.dumps({"events": evs}, ensure_ascii=False), "```"])


def main(argv=None, opener=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("--mirror", required=True)
    ap.add_argument("--since")
    ap.add_argument("--author", action="append", default=[])
    a = ap.parse_args(argv)
    try:
        if a.repo.count("/") != 1:
            raise Fail(f"仓库要写成 owner/repo，不认 {a.repo!r}")
        since = since_utc(a.since) if a.since else None
        commit = refresh(a.repo, a.mirror)
        print(render(a.repo, commit, events(pick(fetch(a.repo, opener), since, set(a.author)))))
    except Fail as e:
        print(f"没看成：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
