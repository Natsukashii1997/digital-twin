"""名册活跃项目自检：GitHub 设置和派单前提一次查完。只读：不改远端、不 fetch、不切分支、不写文件。

用法：python3 ops/check_repos.py   （名册路径可用环境变量 DT_ROSTER 覆盖，和 dispatch.py 一致）
只查名册 projects 里有 github 字段的项目。每个项目一段中文，最后一行一行 JSON：{"ok", "failed", "warnings"}；有失败退出码 1。
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROSTER = Path(os.environ.get("DT_ROSTER", "~/Documents/ChatGPT/个人/work/digital-twin/projects.json")).expanduser()
LABELS = ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix"]   # docs/agents/triage-labels.md
PR_TEMPLATE = ".github/pull_request_template.md"
FLOW_WORD = "工程流程"


def git(cwd, *args):
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["git"], 124, "", "git 超时")


def gh(*args):
    """调用 GitHub CLI；找不到 gh 或超时也按失败返回。"""
    exe = shutil.which("gh", path=os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"]))
    if not exe:
        return subprocess.CompletedProcess(["gh"], 127, "", "找不到 gh 命令")
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["gh"], 124, "", "gh 超时")


def err(r):
    s = (r.stderr or r.stdout).strip()
    return s.splitlines()[-1][:200] if s else f"退出码 {r.returncode}"


def check_remote(p):
    """返回 [(通过, 说明)]。仓库信息都取不到时只记一条，不再逐项等。"""
    repo, main = p["github"], p["main"]
    r = gh("api", f"repos/{repo}")
    try:
        info = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        info = None
    if info is None:
        return [(False, f"取不到远端仓库信息（{err(r)}），远端各项没查")]
    res = [
        (info.get("private") is True, "私有" if info.get("private") else "不是私有仓库"),
        (info.get("default_branch") == main,
         f"默认分支是 {main}" if info.get("default_branch") == main else f"默认分支是 {info.get('default_branch')}，名册是 {main}"),
    ]
    merge = {"squash": info.get("allow_squash_merge"), "merge commit": info.get("allow_merge_commit"),
             "rebase": info.get("allow_rebase_merge")}
    bad = [k + ("没开" if k == "squash" else "没关") for k, v in merge.items() if bool(v) != (k == "squash")]
    res.append((not bad, "只允许 squash" if not bad else f"合并方式不对：{'、'.join(bad)}"))
    res.append((info.get("delete_branch_on_merge") is True,
                "合并后自动删分支" if info.get("delete_branch_on_merge") else "合并后不会自动删分支"))
    res.append((info.get("has_issues") is True, "Issues 已打开" if info.get("has_issues") else "Issues 没打开"))

    r = gh("api", f"repos/{repo}/labels?per_page=100")
    try:
        names = {x["name"] for x in json.loads(r.stdout)} if r.returncode == 0 else None
    except (ValueError, TypeError, KeyError):
        names = None
    if names is None:
        res.append((False, f"取不到标签（{err(r)}）"))
    else:
        miss = [x for x in LABELS if x not in names]
        res.append((not miss, "5 个分流标签都在" if not miss else f"缺分流标签：{'、'.join(miss)}"))

    def content(path):
        return gh("api", f"repos/{repo}/contents/{path}?ref={main}", "-H", "Accept: application/vnd.github.raw")

    def missing(path, r):
        return f"远端 {main} 上没有 {path}" if "404" in (r.stderr or r.stdout) else f"取不到 {path}（{err(r)}）"

    r = content(PR_TEMPLATE)
    res.append((r.returncode == 0, f"有 PR 模板 {PR_TEMPLATE}" if r.returncode == 0 else missing(PR_TEMPLATE, r)))
    agents = p.get("agents") or "AGENTS.md"
    r = content(agents)
    if r.returncode != 0:
        res.append((False, missing(agents, r)))
    else:
        has = FLOW_WORD in r.stdout
        res.append((has, f"{agents} 里有《{FLOW_WORD}》指针" if has else f"{agents} 里没有「{FLOW_WORD}」字样"))
    return res


def check_local(p):
    """返回 [(通过, 说明)]，通过为 None 的是提示。"""
    path, main = Path(os.path.expanduser(p["path"])), p["main"]
    if not path.is_dir():
        return [(False, f"主目录不存在：{p['path']}")]
    res = []
    r = git(path, "symbolic-ref", "--short", "-q", "HEAD")
    head = r.stdout.strip() if r.returncode == 0 else "（分离头指针或读不了）"
    res.append((head == main, f"主目录检出 {main}" if head == main else f"主目录检出的是 {head}，不是 {main}"))
    r = git(path, "ls-remote", "origin", f"refs/heads/{main}")
    sha = r.stdout.split()[0] if r.returncode == 0 and r.stdout.strip() else None
    if not sha:
        res.append((False, f"取不到远端 {main} 的提交号（{err(r) if r.returncode else '远端没有这个分支'}）"))
    elif git(path, "rev-parse", "--verify", "-q", f"refs/heads/{main}").returncode != 0:
        res.append((False, f"本地没有 {main} 分支"))
    elif git(path, "cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        res.append((None, f"本地落后，需拉取（本地没有远端 {main} 的 {sha[:7]}）"))
    else:
        ahead = git(path, "rev-list", "--count", f"{sha}..refs/heads/{main}").stdout.strip()
        if ahead and ahead != "0":
            res.append((False, f"本地 {main} 比远端多 {ahead} 个提交没推"))
        else:
            res.append((True, f"本地 {main} 没有比远端多出来的提交"))
    return res


def main():
    try:
        projects = json.loads(ROSTER.read_text(encoding="utf-8"))["projects"]
    except Exception as e:
        print(f"读不了名册 {ROSTER}（{e}）")
        print(json.dumps({"ok": False, "failed": [f"读不了名册（{e}）"], "warnings": []}, ensure_ascii=False))
        return 1
    failed, warnings = [], []
    for p in projects:
        if not p.get("github"):
            continue
        res = check_remote(p) + check_local(p)
        if not p.get("test"):
            res.append((None, "名册没写 test，合并前不跑测试"))
        print(f"【{p['id']}】{p.get('name', '')}（{p['github']}）")
        for ok, msg in res:
            print(f"  {'通过' if ok else '提示' if ok is None else '不通过'}  {msg}")
            if ok is False:
                failed.append(f"{p['id']}：{msg}")
            elif ok is None:
                warnings.append(f"{p['id']}：{msg}")
        print()
    print(json.dumps({"ok": not failed, "failed": failed, "warnings": warnings}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
