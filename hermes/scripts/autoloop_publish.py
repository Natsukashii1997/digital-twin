#!/usr/bin/env python3
"""受信发布（最小版，批 0C 执行门，#205；方案 docs/agent-autoloop-底座-方案.md 5.5 不变量 1「乙：受信发布」、第 12 节第 6 条；
规格 docs/秘书代批-规格.md §4「发布侧」）。

dt-worker 不持 GitHub 写凭据：工人侧启动脚本（ops/dt_worktree.py）在 CLI 退出后把本单分支打成交接包
（/Users/dt-worker/dt-outbox/<单号>/handoff.json + <单号>.bundle）。本脚本是控制面的受信发布出口，按 ADR-015 归 dt-gate
账号（「受信发布出口从第一天按归 dt-gate 写」）：取包、核对后推分支、开 PR：

  1. 交接包只当资料：清单逐字段按白名单校验，分支名必须正好是 agent/<执行方>/<单号>；包先复制到私有临时目录、
     核对哈希再用；只取 Git 对象（bundle verify + fetch，打开对象校验，钩子关掉），不检出、不运行里面任何东西。
  2. 凭证以主账号的凭证库为准（autoloop_grants.check，和所有出口共用）：推分支前核 push_branch、开 PR 前核 open_pr，
     代次用清单里的、必须是当前代且没作废；仓库从凭证取，不信清单。
  3. 能机械判的发布边界：包里只有本单分支一个引用、头提交对得上；基线是 main 上的提交、头提交从基线长出来；
     改动不碰合并规则 merge_rules.json 里的高风险路径（.github/、ops/、密钥类……）——碰了就不发、交常浩。
  4. 只推非 main：推送目标写死成 refs/heads/agent/<执行方>/<单号>，不带 --force；远端同名分支已有别的提交就不覆盖。
     GitHub 一侧 dt-sandbox 的规则集也只放行 agent/**（hermes/baselines/dt-sandbox-github.json）。
  5. 同一分支已有开着的 PR 就沿用；已关闭或已合并的不再开新的。标题和正文用凭证库里的五要素，不用工人写的字。

本批只做手动入口：不接中台规则、不装常驻服务、不写入 Hermes 定时任务、不配真实凭据（ADR-015 生效顺序第 2 条）；
测试只推本地裸仓库。推送和开 PR 用运行账号自己的 git / gh 登录：0E 在 dt-gate 里配机器账号令牌（只能推非 main、不能合并，
#132 的做法），之前不在主账号或秘书账号里真跑（ADR-015 第 5 节第 6 条「真实特权不回流」）。
状态只放本脚本自己的目录（运行账号的 DigitalTwin/autoloop/publish/、publish.jsonl），不和主账号其他脚本共用；
凭证库随 autoloop_grants 一起搬进 dt-gate。写前账本、结果不明冻结在 0D；接成正式出口在批 3。

  autoloop_publish.py publish --task t-20261118-abcdef
  autoloop_publish.py scan                 只读：列交接目录里的单和各自能不能发
环境变量：DT_PUBLISH_OUTBOX（交接目录，默认 /Users/dt-worker/dt-outbox）、DT_PUBLISH_MIRRORS（主账号的裸仓库副本）、
DT_PUBLISH_REMOTE（默认 https://github.com/{github}.git）、DT_PUBLISH_LOG、DT_GH，以及 autoloop_grants 的 DT_GRANTS_*。
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_grants as G  # noqa: E402

MAX_MANIFEST = 4096
MAX_BUNDLE = 50 * 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
KEYS = {"schema", "task_id", "generation", "executor", "repo", "branch", "base", "head", "bundle", "bundle_sha256",
        "run", "at"}
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "transfer.fsckObjects=true",
            "-c", "fetch.fsckObjects=true"]
RULES = HERE / "merge_rules.json"


class Rejected(Exception):
    """不发：说人话的原因。"""


def branch_name(executor, task_id):
    """和工人侧 ops/dt_worktree.branch_name 同一写法（测试核对两边一致）。"""
    return f"agent/{executor}/{task_id}"


def outbox():
    return Path(os.environ.get("DT_PUBLISH_OUTBOX") or "/Users/dt-worker/dt-outbox")


def mirrors():
    return Path(os.environ.get("DT_PUBLISH_MIRRORS") or G.SUPPORT / "autoloop/publish").expanduser()


def remote_url(github):
    return (os.environ.get("DT_PUBLISH_REMOTE") or "https://github.com/{github}.git").format(github=github)


def log_path():
    return Path(os.environ.get("DT_PUBLISH_LOG") or G.SUPPORT / "autoloop/publish.jsonl").expanduser()


def gh_cmd():
    return os.environ.get("DT_GH", "gh")


def sensitive_patterns(repo):
    r = json.loads(RULES.read_text(encoding="utf-8"))
    return [re.compile(p) for p in r.get("sensitive", []) + r.get("repo_sensitive", {}).get(repo, [])]


# ---------- 交接包（不可信资料） ----------
def _read_regular(p, limit, what):
    """不跟随链接、只读普通文件、限大小；读不了按「没有」处理。"""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise Rejected(f"读不到{what}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            raise Rejected(f"{what}不是普通文件或太大")
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(limit + 1)
    finally:
        os.close(fd)
    if len(data) > limit:
        raise Rejected(f"{what}太大")
    return data


def read_manifest(task_id):
    if not G.TASK_ID.match(task_id or ""):
        raise Rejected("单号格式不对")
    raw = _read_regular(outbox() / task_id / "handoff.json", MAX_MANIFEST, "交接清单")
    try:
        m = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise Rejected("交接清单不是合法的 JSON")
    if not isinstance(m, dict) or set(m) != KEYS:
        raise Rejected("交接清单的字段不对")
    ok = (m["schema"] == 1 and m["task_id"] == task_id and type(m["generation"]) is int and m["generation"] >= 1
          and m["executor"] in G.EXECUTORS and isinstance(m["repo"], str) and G.REPO_NAME.match(m["repo"])
          and m["branch"] == branch_name(m["executor"], task_id) and isinstance(m["base"], str)
          and SHA.match(m["base"]) and isinstance(m["head"], str) and SHA.match(m["head"])
          and m["bundle"] == f"{task_id}.bundle" and isinstance(m["bundle_sha256"], str)
          and HEX64.match(m["bundle_sha256"]))
    if not ok:
        raise Rejected("交接清单的内容不合格式（分支名必须是 agent/<执行方>/<单号>）")
    return m


# ---------- git ----------
def git(*args, cwd=None, check=True, timeout=300):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", *GIT_SAFE, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise Rejected(f"git {args[0]} 失败：{(r.stderr or r.stdout).strip()[:300]}")
    return r


def ensure_mirror(repo, github):
    m = mirrors() / f"{repo}.git"
    if not (m / "HEAD").exists():
        m.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(m.parent, 0o700)
        git("init", "--quiet", "--bare", str(m))
    git("fetch", "--quiet", remote_url(github), "+refs/heads/main:refs/remotes/origin/main", cwd=m)
    return m


def is_ancestor(mirror, a, b):
    return git("merge-base", "--is-ancestor", a, b, cwd=mirror, check=False).returncode == 0


def assert_not_main(branch):
    """推送目标的最后一道写死检查：只推 agent/ 前缀的本单分支。"""
    if not branch.startswith("agent/") or branch.split("/")[-1] in ("main", "master") or ".." in branch:
        raise Rejected(f"不推这个分支：{branch}")


@contextlib.contextmanager
def task_lock(task_id):
    d = mirrors()
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(d / f"{task_id}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("这张单另一个发布还在进行")
        yield
    finally:
        os.close(fd)


def record(entry):
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": G.iso(G.now_tz()), **entry}, ensure_ascii=False) + "\n")


def must(task_id, action, generation, now, path):
    reason = G.check(task_id, action, generation, now=now, path=path)
    if reason:
        raise Rejected(f"{reason}（{action}）")


# ---------- 发布 ----------
def publish(task_id, now=None, path=None):
    now = now or G.now_tz()
    m = read_manifest(task_id)
    with task_lock(task_id):
        must(task_id, "push_branch", m["generation"], now, path)
        with G.tx(path) as con:
            row = dict(G.grant_row(con, task_id))
        if (row["repo"], row["executor"]) != (m["repo"], m["executor"]):
            raise Rejected("交接清单的仓库或执行方和凭证不一致")
        github, branch, head, base = row["github"], m["branch"], m["head"], m["base"]
        assert_not_main(branch)
        mirror = ensure_mirror(row["repo"], github)
        with tempfile.TemporaryDirectory(prefix="dt-publish-") as tmp:
            data = _read_regular(outbox() / task_id / m["bundle"], MAX_BUNDLE, "交接包")
            if hashlib.sha256(data).hexdigest() != m["bundle_sha256"]:
                raise Rejected("交接包和清单的哈希对不上（可能正在更新，稍后再试）")
            bundle = Path(tmp) / "handoff.bundle"
            bundle.write_bytes(data)
            git("bundle", "verify", "--quiet", str(bundle), cwd=mirror)
            heads = git("bundle", "list-heads", str(bundle), cwd=mirror).stdout.split()
            if heads != [head, f"refs/heads/{branch}"]:
                raise Rejected("交接包里不只有本单分支，或头提交对不上")
            git("fetch", "--quiet", "--no-tags", str(bundle), f"+refs/heads/{branch}:refs/dt-publish/{task_id}",
                cwd=mirror)
        if git("rev-parse", f"refs/dt-publish/{task_id}", cwd=mirror).stdout.strip() != head:
            raise Rejected("取进来的头提交对不上")
        if not is_ancestor(mirror, base, "refs/remotes/origin/main") or not is_ancestor(mirror, base, head):
            raise Rejected("基线不是 main 上的提交，或头提交不是从基线长出来的")
        changed = git("diff", "--name-only", "-z", "--no-renames", base, head, cwd=mirror).stdout.split("\0")
        hits = sorted({f for f in changed if f and any(p.search(f) for p in sensitive_patterns(row["repo"]))})
        if hits:
            raise Rejected(f"改动碰到高风险路径（{'、'.join(hits[:5])}），不自动发布，交常浩")
        remote = git("ls-remote", remote_url(github), f"refs/heads/{branch}", cwd=mirror).stdout.split()
        existing = remote[0] if remote else None
        pushed = False
        if existing != head:
            if existing and not (SHA.match(existing) and is_ancestor(mirror, existing, head)):
                raise Rejected("远端同名分支已有别的提交，不覆盖")
            must(task_id, "push_branch", m["generation"], G.now_tz(), path)        # 推之前现读一次
            git("push", "--quiet", "--no-verify", remote_url(github), f"{head}:refs/heads/{branch}", cwd=mirror)
            pushed = True
        must(task_id, "open_pr", m["generation"], G.now_tz(), path)
        pr = open_pr(github, branch, row)
        out = {"task_id": task_id, "generation": m["generation"], "github": github, "branch": branch, "head": head,
               "pushed": pushed, **pr}
        record({"what": "发布", **out})
        return out


def open_pr(github, branch, row):
    r = subprocess.run([gh_cmd(), "pr", "list", "-R", github, "--head", branch, "--state", "all",
                        "--json", "number,url,state"], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise Rejected(f"查 PR 失败：{r.stderr.strip()[:200]}")
    prs = json.loads(r.stdout or "[]")
    live = [p for p in prs if p.get("state") == "OPEN"]
    if live:
        return {"pr": live[0].get("url"), "pr_opened": False}
    if prs:
        raise Rejected("这条分支的 PR 已关闭或已合并，不再开新的；要继续请常浩处理")
    first = row["scope_text"].strip().splitlines()[0][:80]
    body = "\n".join([f"任务单：{row['task_id']}（第 {row['generation']} 代）", f"执行方：{row['executor']}",
                      f"范围哈希：{row['scope_hash']}", "",
                      "由受信发布（autoloop_publish.py）开出：提交来自工人账号 dt-worker，主账号只取 Git 对象、核过凭证。",
                      "合并照 B 档：常浩点合并卡片或在电脑上处理。", "", "## 五要素", "", row["scope_text"]])
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
    try:
        r = subprocess.run([gh_cmd(), "pr", "create", "-R", github, "-B", "main", "-H", branch,
                            "-t", f"{row['task_id']}：{first}", "-F", f.name], capture_output=True, text=True,
                           timeout=120)
    finally:
        os.unlink(f.name)
    if r.returncode != 0:
        raise Rejected(f"开 PR 失败：{r.stderr.strip()[:200]}")
    return {"pr": r.stdout.strip().splitlines()[-1] if r.stdout.strip() else None, "pr_opened": True}


def scan(now=None, path=None):
    """只读：交接目录里每张单能不能发（只核清单和凭证，不取包、不联网）。"""
    now = now or G.now_tz()
    out = []
    root = outbox()
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        item = {"task_id": d.name}
        try:
            m = read_manifest(d.name)
            item.update(generation=m["generation"], head=m["head"][:12],
                        check=G.check(d.name, "push_branch", m["generation"], now=now, path=path) or "可以发")
        except Rejected as e:
            item["check"] = str(e)
        out.append(item)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="受信发布（最小版）：取 dt-worker 的交接包，核凭证后推非 main 分支、开 PR")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--task", required=True)
    sub.add_parser("scan")
    a = ap.parse_args(argv)
    try:
        out = publish(a.task) if a.cmd == "publish" else scan()
    except (Rejected, G.Rejected) as e:
        if a.cmd == "publish":
            with contextlib.suppress(OSError):
                record({"what": "拒发", "task_id": a.task, "reason": str(e)})
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "result": out}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
