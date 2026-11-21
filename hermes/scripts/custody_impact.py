#!/usr/bin/env python3
"""托管决定被推翻时的下游影响清单（#133，规格 §3.6「下游影响清单」）。只读：读账本、夜跑 run-log、GitHub，不改任何东西。

四类规则（rule 字段就是规格里的序号）：
  1 同一子单在该决定之后的所有 run（回复类）：读夜跑记录 DT_NIGHT_ROOT/<夜跑>/run-log.jsonl 里 event=run、issue 是这张子单、
    created_at 晚于决定的 run（同一个 run 多条记录取最后一条）；
  2 这些 run 开出的分支、PR，以及 git 上以它们为祖先的其他分支、PR（回复类）：run 的 pr_url 给出 PR；再拿这些 PR 的 HEAD 当种子，
    仓库里 HEAD 以种子为祖先的分支、决定之后建的 PR 都算；
  3 托管合并：之后合并、合并提交以本次合并提交为祖先的 PR，以及引用了这个 PR（或 target.gh_issue）的后续 issue、PR（GitHub 时间线
    cross-referenced 事件，晚于决定的）；
  4 账本里依据它做出的其他决定（cites 引用了它的），顺着依据一层层往下找。
取不到的数据不当成「没有影响」：记进 gaps（哪条规则、为什么），汇报里写「没列全」。

GitHub 用本机 gh 登录只读（gh api），和合并官的只读调用一样。
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import custody_ledger as L  # noqa: E402

GH = os.environ.get("DT_GH", "gh")
TIMEOUT = 60
NIGHT_ROOT = "~/Library/Application Support/DigitalTwin/runs"
PR_URL = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)$")
RULES = {1: "这个任务后来的运行", 2: "这些运行开的分支和 PR", 3: "之后的合并和引用它的 issue / PR", 4: "依据这个决定做的其他决定"}
RUN_WORDS = {"completed": "跑完了", "running": "正在跑", "queued": "排队中", "failed": "失败了", "cancelled": "被取消了", "canceled": "被取消了"}


class Unavailable(Exception):
    """取不到某项数据（GitHub 调不通、记录读不了）。"""


def parse_time(s):
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


class GitHub:
    """只读 GitHub（本机 gh 登录）。测试换成假对象，方法同名即可。"""

    def _api(self, path, paginate=False):
        cmd = [GH, "api", path] + (["--paginate", "--slurp"] if paginate else [])
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:
            raise Unavailable(f"gh 跑不起来（{type(e).__name__}）")
        if r.returncode != 0:
            raise Unavailable(f"gh api {path} 失败（{(r.stderr or r.stdout).strip()[-120:]}）")
        try:
            data = json.loads(r.stdout)
        except ValueError:
            raise Unavailable(f"gh api {path} 输出不是 JSON")
        if paginate and isinstance(data, list):
            data = [x for page in data for x in (page if isinstance(page, list) else [page])]
        return data

    def pulls(self, repo):
        """全部 PR（各种状态，翻完所有页，#133 第 1 轮审查 P1-05）：number、html_url、head{ref,sha}、created_at、merged_at、merge_commit_sha。"""
        return self._api(f"repos/{repo}/pulls?state=all&per_page=100&sort=created&direction=desc", paginate=True)

    def pr_commits(self, repo, number):
        """PR 里的全部提交（P1-06：拿历史提交当种子，不拿可变的当前 HEAD）。"""
        return [c["sha"] for c in self._api(f"repos/{repo}/pulls/{number}/commits?per_page=100", paginate=True)]

    def force_pushed(self, repo, number):
        """PR 有没有被强推过（时间线 head_ref_force_pushed）：强推过的话，被冲掉的旧提交查不到。"""
        return any((e or {}).get("event") == "head_ref_force_pushed"
                   for e in self._api(f"repos/{repo}/issues/{number}/timeline?per_page=100", paginate=True))

    def pull(self, repo, number):
        return self._api(f"repos/{repo}/pulls/{number}")

    def branches(self, repo):
        return {b["name"]: b["commit"]["sha"] for b in self._api(f"repos/{repo}/branches?per_page=100", paginate=True)}

    def is_ancestor(self, repo, base, head):
        """base 是不是 head 的祖先（含相同）：compare 接口的 status 是 ahead 或 identical。"""
        return self._api(f"repos/{repo}/compare/{base}...{head}").get("status") in ("ahead", "identical")

    def cross_refs(self, repo, number):
        """引用了这个 issue / PR 的其他 issue、PR（时间线里的 cross-referenced 事件）。"""
        out = []
        for e in self._api(f"repos/{repo}/issues/{number}/timeline?per_page=100", paginate=True):
            src = ((e or {}).get("source") or {}).get("issue") or {}
            if e.get("event") == "cross-referenced" and src.get("html_url"):
                out.append({"number": src.get("number"), "url": src["html_url"], "title": src.get("title", ""),
                            "is_pr": bool(src.get("pull_request")), "at": e.get("created_at")})
        return out


def later_runs(d, night_root=None):
    """规则 1：同一子单在决定之后的 run。返回 (runs, problems)：problems 是读到但不可信的地方（坏行、记录标了不完整、
    缺创建时间），调用方写进 gaps，不当成没有（#133 第 1 轮审查 P1-05）。整个文件读不了抛 Unavailable。"""
    t = d["target"]
    if not (t.get("night") and t.get("issue")):
        raise Unavailable("决定里没记夜跑号或子单（target.night / target.issue）")
    root = Path(night_root or os.environ.get("DT_NIGHT_ROOT") or NIGHT_ROOT).expanduser()
    log = root / str(t["night"]) / "run-log.jsonl"
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise Unavailable(f"读不了夜跑记录 {log}（{e.strerror or e}）")
    since, runs, problems = parse_time(d["created_at"]), {}, []
    for n, ln in enumerate(lines, 1):
        if not ln.strip():
            continue
        try:
            r = json.loads(ln)
        except ValueError:
            problems.append(f"夜跑记录第 {n} 行读不了")
            continue
        if not isinstance(r, dict):
            problems.append(f"夜跑记录第 {n} 行不是对象")
            continue
        if r.get("event") == "run" and r.get("issue") == t["issue"]:
            at = parse_time(r.get("created_at"))
            if not r.get("run_id") or at is None:
                problems.append(f"夜跑记录第 {n} 行缺运行号或创建时间")
                continue
            if at > since:
                runs[r["run_id"]] = r
                if r.get("incomplete"):
                    problems.append(f"运行 {r['run_id']} 的记录标了不完整")
    return sorted(runs.values(), key=lambda r: r.get("created_at") or ""), problems


def descendants(store, decision_id, use=L.CUSTODY):
    """规则 4：账本里依据它（cites）的决定，顺着往下找；返回 (决定, 经由哪个决定)。use 是哪本账（秘书推翻 0B-5 用秘书账本）。"""
    out, seen, todo = [], {decision_id}, [decision_id]
    rows = L.decisions(store, use=use)
    while todo:
        cur = todo.pop(0)
        for x in rows:
            if cur in x["cites"] and x["id"] not in seen:
                seen.add(x["id"])
                out.append((x, cur))
                todo.append(x["id"])
    return out


def impacts(store, d, gh=None, night_root=None, use=L.CUSTODY):
    """返回 {"items": [{rule, what, ref, url}], "gaps": [{rule, why}]}。use 是哪本账（规则 4 顺着它的依据找）。"""
    gh = gh or GitHub()
    items, gaps = [], []

    def add(rule, what, ref, url=""):
        if not any(i["rule"] == rule and i["ref"] == ref for i in items):
            items.append({"rule": rule, "what": what, "ref": ref, "url": url})

    since = parse_time(d["created_at"])
    t = d["target"]
    if d["action"] == "reply":
        try:
            runs, problems = later_runs(d, night_root)
            for why in problems:
                gaps.append({"rule": 1, "why": why})
        except Unavailable as e:
            runs = None
            gaps.append({"rule": 1, "why": str(e)})
            gaps.append({"rule": 2, "why": "规则 1 没取到 run，无从找它们开的分支和 PR"})
        if runs is not None:
            prs = {}
            for r in runs:
                add(1, f"运行 {r['run_id']}（{RUN_WORDS.get(r.get('status'), '状态不明')}）", r["run_id"])
                m = PR_URL.match(r.get("pr_url") or "")
                if m:
                    prs[(m.group(1), int(m.group(2)))] = r["pr_url"]
            no_pr = [r["run_id"] for r in runs if not PR_URL.match(r.get("pr_url") or "")]
            if no_pr:
                gaps.append({"rule": 2, "why": f"{len(no_pr)} 个 run 没记 PR 链接（{'、'.join(no_pr[:3])}），它们推了分支但没开 PR 的话查不到"})
            try:
                seeds = {}
                for (repo, n), url in prs.items():
                    add(2, f"PR #{n}（这些运行开的）", f"{repo}#{n}", url)
                    seeds.setdefault(repo, set()).update(gh.pr_commits(repo, n))
                    if gh.force_pushed(repo, n):
                        gaps.append({"rule": 2, "why": f"PR #{n} 被强推过，被冲掉的旧提交查不到，以它们为基础的分支可能漏列"})
                for repo, shas in seeds.items():
                    for name, sha in gh.branches(repo).items():
                        if name != "main" and any(gh.is_ancestor(repo, s, sha) for s in shas):
                            add(2, f"分支 {name}", f"{repo}@{name}")
                    for p in gh.pulls(repo):
                        made = parse_time(p.get("created_at"))
                        if made and made > since and any(gh.is_ancestor(repo, s, p["head"]["sha"]) for s in shas):
                            add(2, f"PR #{p['number']}（在这些运行的改动上接着改的）", f"{repo}#{p['number']}", p.get("html_url", ""))
            except (Unavailable, KeyError, TypeError) as e:
                gaps.append({"rule": 2, "why": f"GitHub 没查全（{e}）"})
    elif d["action"] == "merge":
        repo, mc = t.get("repo"), (d["outcome"] or {}).get("merge_commit")
        if not mc:
            gaps.append({"rule": 3, "why": "账本里没有合并提交号（合并没做成或没记），无从找之后的合并"})
        else:
            try:
                for p in gh.pulls(repo):
                    merged = parse_time(p.get("merged_at"))
                    sha = p.get("merge_commit_sha")
                    if merged and merged > since and sha and sha != mc and gh.is_ancestor(repo, mc, sha):
                        add(3, f"PR #{p['number']}（之后合进 main，里面带着这次合并的改动）", f"{repo}#{p['number']}", p.get("html_url", ""))
            except (Unavailable, KeyError, TypeError) as e:
                gaps.append({"rule": 3, "why": f"GitHub 没查全之后的合并（{e}）"})
        for n in (t.get("pr"), t.get("gh_issue")):
            if not n:
                continue
            try:
                for x in gh.cross_refs(repo, n):
                    at = parse_time(x.get("at"))
                    if at and at > since:
                        add(3, f"{'PR' if x['is_pr'] else 'issue'} #{x['number']} 引用了 #{n}", f"{repo}#{x['number']}", x["url"])
            except (Unavailable, KeyError, TypeError) as e:
                gaps.append({"rule": 3, "why": f"GitHub 没查全引用 #{n} 的 issue / PR（{e}）"})
    for x, via in descendants(store, d["id"], use):
        add(4, f"「{x['context'] or L.describe_target(x)}」：{x['summary'] or '没替你定'}（{x['id']}）", x["id"])
    return {"items": items, "gaps": gaps}


def describe(result):
    """给人看的几行字。"""
    lines = []
    for rule, name in RULES.items():
        got = [i for i in result["items"] if i["rule"] == rule]
        if got:
            lines.append(f"{rule}. {name}：" + "；".join(i["what"] for i in got))
    for g in result["gaps"]:
        lines.append(f"⚠️ 没列全（规则 {g['rule']} {RULES[g['rule']]}）：{g['why']}")
    return lines or ["没有找到受影响的后续事项"]
