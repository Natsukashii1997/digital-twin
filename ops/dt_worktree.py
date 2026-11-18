#!/usr/bin/python3
"""dt-worktree：工人账号 dt-worker 上的派单启动脚本（批 0C 执行门，#205；方案 docs/agent-autoloop-底座-方案.md 5.1、5.2、
6.1、第 9 节 0C；规格 docs/秘书代批-规格.md §4「工人侧」、§8「执行代次」）。

挂在中台 Agent dt-claude / dt-codex 的 `program` 上（上游给包装脚本留的口子，引擎零补丁）：中台的接单程序照常拼好
claude / codex 的命令行，只是第一个词换成本脚本；本脚本按名字（dt-worktree-claude / dt-worktree-codex）认执行方，
把其余参数原样交给真正的 CLI（dt-worker 自己的 ~/.agent-autoloop/bin/<执行方>-proxy.sh，带过滤代理）。

每一轮：
  1. 准入（不过就不启动 CLI）：这一轮的事件都来自 hermes 来源、事项（subject）是同一个任务单号；派单事件的类型是
     dispatch.<仓库>.<执行方>.write、detail 里的单号对得上；回答和复查事件沿用这张单上次派单的代次。
     读工人只读的凭证镜像（/Users/Shared/dt-autoloop/grants/status.json）和任务单（tasks/<单号>.md）：凭证有效、没过期、
     允许改文件、代次是当前的且没作废、执行方 / 仓库 / 规则名对得上、任务单五要素的哈希等于凭证记的范围哈希；
     续接时还要和第一轮钉下的范围哈希、仓库、执行方一致（续接后范围不扩大）；这张单累计用时没到上限（2 小时）。
  2. 工作目录按单号固定：~/dt-work/<仓库>/wt/<单号>，分支 <执行方>/<单号>（如 claude/t-…），基线是第一轮时的 origin/main；
     续接回同一目录、同一分支（会话也接得上）。每张单的缓存目录分开。
  3. 启动 CLI（提示词末尾补一段说明：只改文件，不推、不开 PR、不切分支）：标准输出不经本脚本（直接交给接单程序），退出码原样返回；本脚本的说明只写自己的日志
     ~/dt-work/.state/<单号>.log，不混进输出。用时到上限就停掉 CLI。
  4. CLI 退出后：把工作目录里的改动提交到本单分支；再核一次凭证，仍有效才打交接包（git bundle + 清单）放进
     ~/dt-outbox/<单号>/，由主账号的受信发布脚本（hermes/scripts/autoloop_publish.py）取走核对后推分支、开 PR。
     本脚本不推、不碰 GitHub 写凭据（方案第 12 节第 6 条）。

准入不过、或用时到上限：输出一条 CLI 格式的最终消息（第一行「停下了：原因」，回执里一张「任务停下了」通知卡），
退出码 0，中台按「本轮结束」处理、不自动重派。临时问题（取不到仓库、同一张单另一轮还在跑）写「没跑成」，中台过几分钟重派。

这里的检查只防正常流程出错，不当作不可绕过的边界：脚本归主账号所有、工人只能运行不能改，但工人账号的进程能伪造镜像
路径之类；真正的边界在主账号的受信发布（再按凭证库核一遍）和 GitHub 保护（方案 5.1 第 3 条）。

只用 macOS 自带的 /usr/bin/python3（3.9）标准库。环境变量（测试用）：DT_AUTOLOOP_SHARED、DT_GRANTS_STATUS、
DT_GRANTS_TASKS、DT_WORK_ROOT、DT_OUTBOX、DT_WORKTREE_REAL_CLAUDE / _CODEX、DT_WORKTREE_REMOTE、DT_WORKTREE_BUDGET。
"""
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

EXECUTORS = ("claude", "codex")
SOURCE = "hermes"
TASK_ID = re.compile(r"^t-\d{8}-[0-9a-f]{6}$")
REPO = re.compile(r"^[\w.-]{1,100}$")
GITHUB = re.compile(r"^[\w.-]{1,100}/[\w.-]{1,100}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
FOLLOW_TYPES = ("card.answered", "timer.due")     # 回答、复查：沿用这张单上次派单的代次
CHECK_ARGS = {"claude": (["auth", "status"], ["--version"]), "codex": (["login", "status"], ["--version"])}
BUDGET_SECONDS = 2 * 3600                          # 每张单跨轮累计（常浩 2026-11-18 定：一律 2 小时）
KILL_GRACE = 10
HEARTBEAT = 30
TZ = dt.timezone(dt.timedelta(hours=8))
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


class Refused(Exception):
    """准入不过：本轮结束、不启动 CLI、不自动重派。"""

    def __init__(self, reason, task_id=None):
        super().__init__(reason)
        self.reason, self.task_id = reason, task_id


class Retry(Exception):
    """临时问题：写「没跑成」，中台过几分钟重派。"""


# ---------- 路径 ----------
def shared():
    return Path(os.environ.get("DT_AUTOLOOP_SHARED") or "/Users/Shared/dt-autoloop")


def status_path():
    return Path(os.environ.get("DT_GRANTS_STATUS") or shared() / "grants/status.json")


def tasks_dir():
    return Path(os.environ.get("DT_GRANTS_TASKS") or shared() / "tasks")


def work_root():
    return Path(os.environ.get("DT_WORK_ROOT") or "~/dt-work").expanduser()


def outbox():
    return Path(os.environ.get("DT_OUTBOX") or "~/dt-outbox").expanduser()


def real_cli(executor):
    p = os.environ.get(f"DT_WORKTREE_REAL_{executor.upper()}") or f"~/.agent-autoloop/bin/{executor}-proxy.sh"
    return str(Path(p).expanduser())


def remote_url(github):
    return (os.environ.get("DT_WORKTREE_REMOTE") or "https://github.com/{github}.git").format(github=github)


def budget():
    return int(os.environ.get("DT_WORKTREE_BUDGET") or BUDGET_SECONDS)


def branch_name(executor, task_id):
    """本单分支 <执行方>/<单号>（常浩 2026-11-18 选：改 dt-sandbox 规则集放行 claude/**，基线 hermes/baselines/）；
    受信发布脚本按同一写法核对（autoloop_publish.branch_name）。"""
    return f"{executor}/{task_id}"


def now_tz():
    return dt.datetime.now(TZ)


def iso(t):
    return t.astimezone(TZ).isoformat(timespec="seconds")


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- 日志与状态（工人自己的，0600） ----------
def state_dir():
    d = work_root() / ".state"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def log(task_id, text):
    try:
        with open(state_dir() / f"{task_id or 'dt-worktree'}.log", "a", encoding="utf-8") as f:
            f.write(f"{iso(now_tz())} {text}\n")
    except OSError:
        pass


def load_state(task_id):
    p = state_dir() / f"{task_id}.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(task_id, state):
    p = state_dir() / f"{task_id}.json"
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def take_lock(task_id):
    """同一张单同一时间只跑一轮（同一个工作目录）；拿不到就让中台过会儿重派。"""
    fd = os.open(state_dir() / f"{task_id}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise Retry("同一张单另一轮还在跑")
    return fd


def used_seconds(state):
    """累计用时；上一轮被强杀没来得及结账的，按它最后一次心跳记上（人工挂起不在进程里，本来就不计）。"""
    if not state:
        return 0
    used = int(state.get("seconds_used", 0))
    run = state.get("running")
    if run:
        used += max(0, int(run.get("last_seen", 0)) - int(run.get("started", 0)))
    return used


# ---------- 准入 ----------
def read_json(p, what, task_id=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Refused(f"读不到{what}", task_id)


def parse_task_file(task_id):
    """任务单（autoloop_grants.write_task_file 写的）：仓库、执行方、范围哈希、五要素原文。"""
    p = tasks_dir() / f"{task_id}.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        raise Refused("读不到任务单", task_id)
    head, sep, scope = text.partition("\n## 五要素\n\n")
    fields = dict(m.groups() for m in re.finditer(r"^(仓库|执行方|范围哈希)：(.*)$", head, re.M))
    if not sep or len(fields) != 3:
        raise Refused("任务单格式不对", task_id)
    if scope.endswith("\n"):
        scope = scope[:-1]
    return {"github": fields["仓库"].strip(), "executor": fields["执行方"].strip(),
            "scope_hash": fields["范围哈希"].strip(), "scope_text": scope}


def admit(executor, repo, context, now, check_budget=True):
    """返回这一轮的准入结果；不过就抛 Refused。只读，不改任何东西。交接前重核时不查用时（上限内做完的照样交）。"""
    events = context.get("events") if isinstance(context, dict) else None
    if not isinstance(events, list) or not events:
        raise Refused("这一轮没有派单事件")
    subjects = {e.get("subject") for e in events if isinstance(e, dict)}
    if len(subjects) != 1 or len(events) != sum(isinstance(e, dict) for e in events):
        raise Refused("这一轮的事件不属于同一张任务单")
    task_id = subjects.pop()
    if not isinstance(task_id, str) or not TASK_ID.match(task_id):
        raise Refused("事件对象不是任务单号")
    dispatch_type = f"dispatch.{repo}.{executor}.write"
    gens = []
    for e in events:
        if e.get("source") != SOURCE:
            raise Refused("这一轮有不是派单网关发来的事件", task_id)
        if e.get("type") == dispatch_type:
            try:
                d = json.loads(e.get("detail") or "")
            except ValueError:
                raise Refused("派单事件的内容读不懂", task_id)
            g = d.get("generation") if isinstance(d, dict) else None
            if d.get("task_id") != task_id or not isinstance(g, int) or isinstance(g, bool) or g < 1:
                raise Refused("派单事件的单号或代次不对", task_id)
            gens.append(g)
        elif e.get("type") not in FOLLOW_TYPES:
            raise Refused(f"不认识的事件类型 {str(e.get('type'))[:60]}", task_id)
    state = load_state(task_id)
    if gens:
        generation = max(gens)
    elif state and isinstance(state.get("generation"), int):
        generation = state["generation"]
    else:
        raise Refused("没有派单记录，回答或复查接不上", task_id)
    mirror = read_json(status_path(), "凭证镜像", task_id)
    if not isinstance(mirror, dict) or mirror.get("schema") != 1 or not isinstance(mirror.get("tasks"), dict):
        raise Refused("凭证镜像格式不对", task_id)
    t = mirror["tasks"].get(task_id)
    if not isinstance(t, dict):
        raise Refused("凭证镜像里没有这张单", task_id)
    reason = check_grant(t, executor, repo, generation, now)
    if reason:
        raise Refused(reason, task_id)
    task = parse_task_file(task_id)
    if sha256(task["scope_text"]) != task["scope_hash"] or task["scope_hash"] != t.get("scope_hash"):
        raise Refused("任务单的五要素和凭证记的范围对不上", task_id)
    if not GITHUB.match(task["github"]) or task["github"].split("/")[-1] != repo or task["executor"] != executor:
        raise Refused("任务单的仓库或执行方对不上", task_id)
    if state:
        pinned = {k: state.get(k) for k in ("scope_hash", "github", "executor", "repo")}
        if pinned != {"scope_hash": task["scope_hash"], "github": task["github"], "executor": executor, "repo": repo}:
            raise Refused("续接时范围、仓库或执行方和第一轮不一样", task_id)
        if generation < int(state.get("generation", 0)):
            raise Refused(f"第 {generation} 代比这张单上次跑的旧", task_id)
    used = used_seconds(state)
    if check_budget and used >= budget():
        raise Refused(f"这张单累计已跑 {used // 60} 分钟，到了 {budget() // 60} 分钟上限", task_id)
    return {"task_id": task_id, "generation": generation, "github": task["github"],
            "scope_hash": task["scope_hash"], "state": state, "used": used}


def check_grant(t, executor, repo, generation, now):
    """按镜像判一张凭证；和主账号的 autoloop_grants.check_row 同一套条件。返回 None 表示放行。"""
    status = t.get("status")
    if status != "active":
        return {"frozen": "凭证已冻结", "revoked": "凭证已撤销", "done": "任务已完成"}.get(status, "凭证状态不对")
    try:
        if now >= dt.datetime.fromisoformat(t["valid_until"]):
            return "凭证已过期"
    except (KeyError, TypeError, ValueError):
        return "凭证的有效期读不懂"
    if "edit" not in (t.get("actions") or []):
        return "凭证不允许改文件"
    if generation in (t.get("voided_generations") or []):
        return f"第 {generation} 代已作废"
    if generation != t.get("generation"):
        return f"代次不对（凭证现在是第 {t.get('generation')} 代，这一轮是第 {generation} 代）"
    if t.get("executor") != executor or t.get("repo") != repo or t.get("rule") != f"dispatch-{repo}-{executor}":
        return "凭证的执行方、仓库或规则对不上"
    return None


# ---------- 工作目录 ----------
def git(*args, cwd=None, check=True, timeout=300):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    r = subprocess.run(["git", *GIT_SAFE, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise subprocess.CalledProcessError(r.returncode, ["git", *args], r.stdout, r.stderr)
    return r


def prepare_worktree(executor, repo, admitted):
    """建或回到本单工作目录。第一轮从最新 origin/main 开分支并钉下基线；续接不换基线。"""
    task_id, github, state = admitted["task_id"], admitted["github"], admitted["state"]
    root = work_root() / repo
    base, wt, branch = root / "base.git", root / "wt" / task_id, branch_name(executor, task_id)
    if state and (state.get("worktree") != str(wt) or state.get("branch") != branch):
        raise Refused("这张单记下的工作目录或分支对不上", task_id)
    try:
        if not (base / "HEAD").exists():
            git("clone", "--bare", "--quiet", remote_url(github), str(base))
        git("fetch", "--quiet", remote_url(github), "+refs/heads/main:refs/remotes/origin/main", cwd=base)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if not state:
            raise Retry(f"取不到 {github}（{type(e).__name__}）")
        log(task_id, f"续接时更新 origin/main 失败，沿用已有工作目录：{type(e).__name__}")
    git("worktree", "prune", cwd=base, check=False)
    has_branch = git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=base, check=False).returncode == 0
    if state:
        base_sha = state["base"]
        if not (wt / ".git").exists():
            if not has_branch:
                raise Refused("这张单的分支不见了", task_id)
            git("worktree", "add", "--quiet", str(wt), branch, cwd=base)
    else:
        if has_branch or wt.exists():
            raise Refused("第一轮却已有同名分支或工作目录", task_id)
        base_sha = git("rev-parse", "refs/remotes/origin/main", cwd=base).stdout.strip()
        wt.parent.mkdir(parents=True, exist_ok=True)
        git("worktree", "add", "--quiet", "-b", branch, str(wt), base_sha, cwd=base)
    head_ref = git("symbolic-ref", "--quiet", "HEAD", cwd=wt, check=False).stdout.strip()
    if head_ref != f"refs/heads/{branch}":
        raise Refused("工作目录不在本单分支上", task_id)
    return {"base": base_sha, "worktree": str(wt), "branch": branch}


def task_env(task_id):
    """每张单的缓存分开放（规格 §4 工人侧：共享缓存隔离）。"""
    cache = work_root() / ".cache" / task_id
    env = dict(os.environ)
    for name, sub in (("XDG_CACHE_HOME", "xdg"), ("npm_config_cache", "npm"), ("PIP_CACHE_DIR", "pip"),
                      ("UV_CACHE_DIR", "uv"), ("GOCACHE", "go")):
        (cache / sub).mkdir(parents=True, exist_ok=True)
        env[name] = str(cache / sub)
    return env


# ---------- 运行 ----------
def with_note(executor, args, task_id, branch):
    """在交给 CLI 的提示词末尾补一段启动脚本的说明（中台派单规则的提示词在 hermes 钥匙签发后不能改，0B-2）。
    claude：-p 后面那个参数；codex：exec 的最后一个参数。认不出位置就原样不动。"""
    note = (f"\n\n【dt-worktree 启动脚本】你在任务单 {task_id} 的独立工作目录里，当前分支 {branch}。只改文件；"
            "不要 git push、不要开 PR、不要切换或新建分支——本轮结束后启动脚本会把改动提交到本单分支，"
            "交给主账号的受信发布核对凭证后推分支、开 PR。")
    out = list(args)
    if executor == "claude" and "-p" in out[:-1]:
        i = out.index("-p") + 1
        out[i] += note
    elif executor == "codex" and len(out) > 1 and out[0] == "exec" and not out[-1].startswith("-"):
        out[-1] += note
    return out


def run_cli(executor, args, admitted, place):
    """启动 CLI，标准输出直接交给接单程序。返回 (退出码, 用时秒, 是否到上限被停)。"""
    task_id = admitted["task_id"]
    left = budget() - admitted["used"]
    stopping = {"sig": None}

    def on_signal(sig, _frame):          # 接单程序取消时整组收到信号：CLI 自己收尾，本脚本等它退出后结账
        stopping["sig"] = sig

    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, on_signal)
    started = time.time()
    state = dict(admitted["state"] or {})
    state.update({"task_id": task_id, "generation": admitted["generation"], "scope_hash": admitted["scope_hash"],
                  "github": admitted["github"], "executor": executor, "repo": place["repo"], "base": place["base"],
                  "worktree": place["worktree"], "branch": place["branch"], "seconds_used": admitted["used"],
                  "running": {"started": int(started), "last_seen": int(started),
                              "run": os.environ.get("AGENT_AUTOLOOP_RUN", "")}})
    save_state(task_id, state)
    argv = [real_cli(executor), *with_note(executor, args, task_id, place["branch"])]
    child = subprocess.Popen(argv, cwd=place["worktree"], env=task_env(task_id))
    log(task_id, f"第 {admitted['generation']} 代开跑（轮次 {os.environ.get('AGENT_AUTOLOOP_RUN', '?')}，"
                 f"已用 {admitted['used']} 秒）")
    over = False
    while True:
        try:
            code = child.wait(timeout=max(0.2, min(HEARTBEAT, left - (time.time() - started))) if not over
                              else HEARTBEAT)
            break
        except subprocess.TimeoutExpired:
            pass
        state["running"]["last_seen"] = int(time.time())
        save_state(task_id, state)
        if not over and time.time() - started >= left:
            over = True
            log(task_id, "累计用时到上限，停掉 CLI")
            child.terminate()
            try:
                child.wait(timeout=KILL_GRACE)
            except subprocess.TimeoutExpired:
                child.kill()
    secs = int(time.time() - started)
    state["seconds_used"] = admitted["used"] + secs
    state["running"] = None
    state.setdefault("turns", []).append({"run": os.environ.get("AGENT_AUTOLOOP_RUN", ""), "generation":
                                          admitted["generation"], "seconds": secs, "exit": code, "at": iso(now_tz())})
    save_state(task_id, state)
    return code, secs, over


def commit_changes(task_id, place, generation):
    wt = place["worktree"]
    git("add", "-A", cwd=wt)
    if git("diff", "--cached", "--quiet", cwd=wt, check=False).returncode == 0:
        return False
    git("-c", "user.name=dt-worker", "-c", "user.email=dt-worker@localhost", "commit", "--quiet", "--no-verify",
        "-m", f"{task_id}：第 {generation} 代的改动（dt-worktree 在 CLI 退出后提交）", cwd=wt)
    return True


def write_handoff(executor, repo, task_id, generation, place):
    """交接包：本单分支相对基线的提交（git bundle）+ 清单。先放包，最后原子换清单；受信发布只把它当资料。"""
    wt, branch, base = place["worktree"], place["branch"], place["base"]
    head = git("rev-parse", f"refs/heads/{branch}", cwd=wt).stdout.strip()
    if head == base:
        return None
    d = outbox() / task_id
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(outbox(), 0o755)
    os.chmod(d, 0o755)
    bundle = d / f"{task_id}.bundle"
    tmp = d / f".{task_id}.{os.getpid()}.bundle"
    git("bundle", "create", "--quiet", str(tmp), f"refs/heads/{branch}", f"^{base}", cwd=wt)
    os.chmod(tmp, 0o644)
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    os.replace(tmp, bundle)
    manifest = {"schema": 1, "task_id": task_id, "generation": generation, "executor": executor, "repo": repo,
                "branch": branch, "base": base, "head": head, "bundle": bundle.name, "bundle_sha256": digest,
                "run": os.environ.get("AGENT_AUTOLOOP_RUN", ""), "at": iso(now_tz())}
    mtmp = d / f".handoff.{os.getpid()}.json"
    mtmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.chmod(mtmp, 0o644)
    os.replace(mtmp, d / "handoff.json")
    return manifest


def after_run(executor, repo, context, admitted, place):
    """CLI 退出后：先提交（工作不丢），再核一次凭证，有效才交接。"""
    task_id, gen = admitted["task_id"], admitted["generation"]
    try:
        committed = commit_changes(task_id, place, gen)
        try:
            admit(executor, repo, context, now_tz(), check_budget=False)
        except Refused as e:
            log(task_id, f"已提交到本地分支，但{e.reason}，不打交接包")
            return
        m = write_handoff(executor, repo, task_id, gen, place)
        log(task_id, f"{'有新提交，' if committed else ''}"
                     f"{'交接包已更新：' + m['head'][:12] if m else '没有相对基线的改动，不交接'}")
    except (OSError, subprocess.SubprocessError) as e:
        log(task_id, f"提交或交接失败：{type(e).__name__} {str(e)[:300]}")


# ---------- 给接单程序的最终消息 ----------
def final_message(executor, text):
    """按 CLI 的输出格式交一条最终消息（中台照常解析），接在已有输出后面也是最后一条。"""
    if executor == "claude":
        lines = [{"type": "result", "subtype": "success", "is_error": False, "num_turns": 0, "result": text}]
    else:
        lines = [{"type": "item.completed", "item": {"type": "agent_message", "text": text}},
                 {"type": "turn.completed", "usage": {}}]
    sys.stdout.write("\n" + "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
    sys.stdout.flush()


def stop_text(reason, task_id):
    receipt = {"notice": [{"title": f"任务 {task_id or '（单号不明）'} 停下了",
                           "text": f"{reason}。工人这边没有接着做；要继续，请在私聊里处理这张单。",
                           "key": "dt-worktree-stopped"}]}
    return f"停下了：{reason}\n\n```agent-autoloop\n{json.dumps(receipt, ensure_ascii=False)}\n```"


# ---------- 入口 ----------
def parse_executor(argv):
    m = re.match(r"^dt-worktree-(claude|codex)$", Path(argv[0]).name)
    if m:
        return m.group(1), argv[1:]
    if len(argv) > 1 and argv[1] in EXECUTORS:
        return argv[1], argv[2:]
    raise SystemExit("用法：dt-worktree-claude|dt-worktree-codex <CLI 参数>，或 dt_worktree.py claude|codex <CLI 参数>")


def main(argv=None):
    executor, args = parse_executor(list(sys.argv if argv is None else argv))
    ctx_file = os.environ.get("AGENT_AUTOLOOP_CONTEXT")
    if not ctx_file:                     # 接单程序查「装没装、登没登」：只放行这几种
        if list(args) in [list(a) for a in CHECK_ARGS[executor]]:
            os.execv(real_cli(executor), [real_cli(executor), *args])
        print("dt-worktree 只在中台派的轮次里启动 CLI", file=sys.stderr)
        return 2
    cwd = Path.cwd().resolve()
    repo = cwd.name
    task_id = None
    try:
        if cwd.parent != work_root().resolve() or not REPO.match(repo):
            raise Refused(f"派单目录不对（应在 {work_root()}/<仓库>）")
        context = read_json(ctx_file, "这一轮的资料")
        admitted = admit(executor, repo, context, now_tz())
        task_id = admitted["task_id"]
        lock = take_lock(task_id)
        try:
            admitted = admit(executor, repo, context, now_tz())      # 拿到锁后按最新状态重判
            try:
                place = {"repo": repo, **prepare_worktree(executor, repo, admitted)}
            except (OSError, subprocess.SubprocessError) as e:
                raise Retry(f"准备工作目录失败（{type(e).__name__}）")
            code, secs, over = run_cli(executor, args, admitted, place)
            after_run(executor, repo, context, admitted, place)
        finally:
            os.close(lock)
    except Refused as e:
        log(e.task_id, f"停下：{e.reason}")
        final_message(executor, stop_text(e.reason, e.task_id))
        return 0
    except Retry as e:
        log(task_id, f"没跑成：{e}")
        final_message(executor, f"没跑成：{e}")
        return 0
    if over:
        final_message(executor, stop_text(f"这张单累计用时到了 {budget() // 60} 分钟上限，本轮已停", task_id))
        return 0
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
