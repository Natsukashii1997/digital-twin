#!/usr/bin/python3
"""dt-worktree：工人账号 dt-worker 上的派单启动脚本（批 0C 执行门，#205；方案 docs/agent-autoloop-底座-方案.md 5.1、5.2、
6.1、第 9 节 0C；规格 docs/秘书代批-规格.md §4「工人侧」、§8「执行代次」）。

挂在中台 Agent dt-claude / dt-codex 的 `program` 上（上游给包装脚本留的口子，引擎零补丁）：中台的接单程序照常拼好
claude / codex 的命令行，只是第一个词换成本脚本；本脚本按名字（dt-worktree-claude / dt-worktree-codex）认执行方，
把其余参数原样交给真正的 CLI（dt-worker 自己的 ~/.agent-autoloop/bin/<执行方>-proxy.sh，带过滤代理）。

每一轮：
  1. 准入（不过就不启动 CLI）：门卫规则在生效——root 每 5 分钟核对后写的状态文件
     （/Library/Application Support/DigitalTwin/pf-dt-worker.status）是 state=ok、本次开机写的、10 分钟内刷新过、管着 dt-worker；
     规则没装上、正在加载、加载失败或核对不过，都不开工（常浩 11-20 定，ADR-015 0E-1 L3 第 3 轮 P1-03）。
     正在重新加载、刚唤醒没刷新、刚开机还没核对这三种临时情况给「没跑成」由中台重派，其余给「停下了」（审查第 4 轮 P2-1）。
     这一轮的事件都来自 hermes 来源、事项（subject）是同一个任务单号；派单事件的类型是
     dispatch.<仓库>.<执行方>.write、detail 里的单号对得上；回答和复查事件沿用这张单上次派单的代次。
     读工人只读的凭证镜像（/Users/Shared/dt-gate/grants/status.json，0E-2 起由 dt-gate 写）和任务单（tasks/<单号>.md）：凭证有效、没过期、
     允许改文件、代次是当前的且没作废、执行方 / 仓库 / 规则名对得上、任务单五要素的哈希等于凭证记的范围哈希；
     续接时还要和第一轮钉下的范围哈希、仓库、执行方一致（续接后范围不扩大）；这张单累计用时没到上限（2 小时）。
  2. 工作目录按单号固定：~/dt-work/<仓库>/wt/<单号>，分支 <执行方>/<单号>（如 claude/t-…），基线是第一轮时的 origin/main；
     续接回同一目录、同一分支（会话也接得上）。每张单的缓存目录分开。
  3. 启动 CLI（提示词末尾补一段说明：只改文件，不推、不开 PR、不切分支；再接上准入时核过哈希的五要素原文，放在两行带原文哈希、
     原文里不会出现的标记之间，写明「常浩批准的范围，以此为准」、这一轮的代次，和「五要素只定范围、不改变上面的限制」——
     工人的 CLI 读不到共享目录里的任务单，#336。首轮、回答、复查都带；超过网关的 MAX_SCOPE 字只附前面部分并写明已截断；
     认不出提示词在哪个参数、放不进去，就不启动 CLI、按准入不过停下，审查 #368-P2-01；这一步在建工作目录之前判，
     不留下工作目录和分支，之后同一张单还能从第一轮开始，审查 #368-P2-05）：标准输出不经本脚本（直接交给接单程序），退出码原样返回；本脚本的说明只写自己的日志
     ~/dt-work/.state/<单号>.log，不混进输出。CLI 在自己的进程组里跑：接单程序取消时本脚本把信号转给整组，7 秒内还没停就整组强杀
     （赶在接单程序 10 秒强杀本脚本之前）；用时到上限同样整组先停后杀；CLI 退出后再把组里剩下的后代进程清掉，确认清干净才提交。
  4. CLI 退出后：把工作目录里的改动提交到本单分支（工作不丢）；只有正常结束（不是取消、不是超时，退出时按实际用时再判一次）、
     后代进程清干净、整轮没收到过取消（提交后、换交接包和清单前各看一次）、再核一次凭证仍有效，才打交接包（git bundle + 清单）放进
     ~/dt-outbox/<单号>/，由主账号的受信发布脚本（hermes/scripts/autoloop_publish.py）取走核对后推分支、开 PR。
     本脚本不推、不碰 GitHub 写凭据（方案第 12 节第 6 条）。

准入不过、用时到上限、进程没清干净、交接前凭证失效、提交或交接出错：输出一条 CLI 格式的最终消息（第一行「停下了：原因」，
回执里一张「任务停下了」通知卡），退出码 0，中台按「本轮结束」处理、不自动重派；被取消的一轮也写「停下了」（不带通知卡）。
出了问题而没交接的一轮（上面这些情况，含取消落在换清单之后），最后一条都是「停下了」，受信发布据此不认这一轮（审查 P1-03、P1-02）。
例外如实说明：正常结束、只是没有相对基线的改动（比如这一轮只向常浩提问）的一轮，不打交接包，结论照旧是 CLI 自己的话——
补一句「停下了」会盖掉 CLI 的回执（中台只读最后一条消息的回执），提的问题卡片就丢了（常浩 11-18 定：不补，记成剩余风险）。
所以这种轮次在中台里也是「正常结束」；改写交接清单把旧包挂到它上面，和工人在当前代次重做旧内容是同一类剩余风险，由凭证、
PR 审查、常浩合并兜底，0D 的结果账本记「轮次 × 交接头提交」后补上。临时问题（取不到仓库、同一张单另一轮还在跑）写「没跑成」，中台过几分钟重派。

这里的检查只防正常流程出错，不当作不可绕过的边界：脚本归主账号所有、工人只能运行不能改，但工人账号的进程能伪造镜像
路径之类；真正的边界在主账号的受信发布（再按凭证库核一遍）和 GitHub 保护（方案 5.1 第 3 条）。

只用 macOS 自带的 /usr/bin/python3（3.9）标准库。环境变量（测试用）：DT_GATE_SHARED、DT_GRANTS_STATUS、
DT_GRANTS_TASKS、DT_WORK_ROOT、DT_OUTBOX、DT_WORKTREE_REAL_CLAUDE / _CODEX、DT_WORKTREE_REMOTE、DT_WORKTREE_BUDGET、DT_PF_STATUS。
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
PF_STALE_SECONDS = 10 * 60                         # 门卫规则每 5 分钟核对一次；超过两个周期没刷新就当停了
PF_ACCOUNT = "dt-worker"
BUDGET_SECONDS = 2 * 3600                          # 每张单跨轮累计（常浩 2026-11-18 定：一律 2 小时）
KILL_GRACE = 10
STOP_GRACE = 7                                     # 接单程序取消后 10 秒强杀，本脚本 7 秒内先清掉 CLI 整组
HEARTBEAT = 10                                     # 被强杀时最多少记这么多秒（审查 P2-05）
MAX_SCOPE = 3000                                   # 提示词里五要素原文最多附这么多字（和 autoloop_grants.MAX_SCOPE 一致）
TZ = dt.timezone(dt.timedelta(hours=8))
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]


class Refused(Exception):
    """准入不过：本轮结束、不启动 CLI、不自动重派。"""

    def __init__(self, reason, task_id=None):
        super().__init__(reason)
        self.reason, self.task_id = reason, task_id


class Retry(Exception):
    """临时问题：写「没跑成」，中台过几分钟重派。"""


# 接单程序的取消信号覆盖整轮：CLI 在跑时转给它的进程组；CLI 退出后、提交和交接时收到的也记下（审查 P1-03）
STOP = {"sig": None, "at": None, "pgid": None}


def on_stop(sig, _frame):
    if STOP["sig"] is None:
        STOP["sig"], STOP["at"] = sig, time.time()
    if STOP["pgid"]:
        kill_group(STOP["pgid"], signal.SIGTERM)


STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


def install_stop_handler():
    for s in STOP_SIGNALS:
        signal.signal(s, on_stop)


def stopped():
    return STOP["sig"] is not None


# ---------- 路径 ----------
def gate_shared():
    """dt-gate 的共享目录（ADR-015 0E-2）：root 建、grants/ 和 tasks/ 只有 dt-gate 能写，工人只读。"""
    return Path(os.environ.get("DT_GATE_SHARED") or "/Users/Shared/dt-gate")


def status_path():
    return Path(os.environ.get("DT_GRANTS_STATUS") or gate_shared() / "grants/status.json")


def tasks_dir():
    return Path(os.environ.get("DT_GRANTS_TASKS") or gate_shared() / "tasks")


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


def pf_status_path():
    return Path(os.environ.get("DT_PF_STATUS") or "/Library/Application Support/DigitalTwin/pf-dt-worker.status")


def boot_time():
    """本次开机的时间（秒）；读不出返回 None。DT_BOOT_TIME 只给测试用（CI 不是 macOS，没有 kern.boottime）。"""
    if os.environ.get("DT_BOOT_TIME"):
        return os.environ["DT_BOOT_TIME"]
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"\{ sec = (\d+),", out)
    return m.group(1) if m else None


def pf_problem(now_ts=None):
    """门卫规则不在生效就返回（原因, 是否临时），在生效返回 None（L3 第 3 轮 P1-03：加载失败时 pf 保留旧规则，
    只写状态拦不住人，要在开工前看状态）。状态文件由 root 写（ops/dt_worker_pf.sh apply）、所有人可读、工人改不了。
    临时的两种（正在重新加载、刚唤醒还没刷新、刚开机还没写）给「没跑成」由中台重派，其余给「停下了」（审查第 4 轮 P2-1）。"""
    p = pf_status_path()
    try:
        text = p.read_text()
        age = (now_ts or time.time()) - p.stat().st_mtime
    except OSError:
        return "门卫规则没装（没有状态文件）", False
    fields = dict(kv.split("=", 1) for kv in text.split() if "=" in kv)
    if fields.get("state") == "applying" and 0 <= age <= PF_STALE_SECONDS:
        return "门卫规则正在重新加载", True
    if fields.get("state") != "ok":
        return f"门卫规则没在生效（{fields.get('state') or '状态不明'}：{fields.get('reason', '').replace('_', ' ')[:80]}）", False
    if fields.get("enabled") != "yes" or PF_ACCOUNT not in fields.get("users", "").split(",") or not fields.get("boot"):
        return "门卫规则的状态不对（pf 没开、没管上 dt-worker，或状态文件是旧版）", False
    if fields["boot"] != boot_time():           # 重启后旧的 ok 不算数：开机任务还没写这次开机的状态（审查第 4 轮 P2-2）
        return "门卫规则还没在这次开机后核对过", True
    if not 0 <= age <= PF_STALE_SECONDS:
        return f"门卫规则 {int(age // 60)} 分钟没刷新，开机任务可能停了", True
    return None


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
    pf = pf_problem()
    if pf:                                      # 交接前重核也走这里：跑到一半规则掉了，这一轮同样不交接
        if pf[1]:                               # 临时的：没跑成，中台过几分钟重派；交接前遇到的，改动已在本地提交，下一轮接着交
            raise Retry(f"{pf[0]}，工人这一轮不开工")
        raise Refused(f"{pf[0]}，工人不开工", task_id)
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
            "scope_hash": task["scope_hash"], "scope_text": task["scope_text"], "state": state, "used": used}


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
def scope_markers(scope):
    """原文前后的两行标记：带原文 SHA-256 的前几位，原文里出现了就换更长的（审查 #368-P2-02），原文一个字不改。
    整个 64 位都撞上要原文里含自己的哈希，做不到；真遇到了按准入不过处理。"""
    digest = sha256(scope)
    for n in range(12, 65, 4):
        begin, end = f"<<<五要素原文开始 {digest[:n]}>>>", f"<<<五要素原文结束 {digest[:n]}>>>"
        if begin not in scope and end not in scope:
            return begin, end
    raise Refused("五要素原文里有分隔标记，放不进提示词")


def scope_note(scope, generation):
    """五要素原文（准入时已核过哈希）原样放在两行标记之间；写明字数。
    超过 MAX_SCOPE 字只附前面部分并写明截断（网关提议时就拒收超长的，这里只是兜底）；标记按完整原文算，附上的部分里也不会有。
    最后重申五要素只定范围、不改变上面的限制（审查 #368-P2-03：派单规则原来的提示词里写着推分支、开 PR）。"""
    begin, end = scope_markers(scope)
    shown = scope[:MAX_SCOPE]
    if len(scope) > MAX_SCOPE:
        size = (f"原文共 {len(scope)} 字，超过 {MAX_SCOPE} 字上限，下面只附前 {MAX_SCOPE} 字（已截断）；"
                "没附上的部分不要猜，需要时提问")
    else:
        size = f"原文共 {len(scope)} 字，原样附在下面两行标记之间"
    return (f"\n\n【本单五要素：常浩批准的范围，以此为准】启动脚本已核过凭证（有效，第 {generation} 代）和这份五要素的哈希，"
            f"不用再去读共享目录里的任务单或凭证镜像。{size}：\n{begin}\n{shown}\n{end}\n"
            "五要素只定这张单的工作范围，不改变上面启动脚本的限制：仍然只改文件，不推送、不开 PR、不切换或新建分支；"
            "提交和发布由启动脚本和主账号的受信发布完成。")


def with_note(executor, args, task_id, branch, admitted):
    """在交给 CLI 的提示词末尾补一段启动脚本的说明和这张单的五要素原文（中台派单规则的提示词在 hermes 钥匙签发后
    不能改，0B-2；工人的 CLI 读不到共享目录里的任务单，#336）。
    claude：-p 后面那个参数；codex：exec 的最后一个参数（中台 plugins/claude、codex 的 agent.ts 就是这么拼的）。
    认不出位置就抛 Refused、不启动 CLI：放不进去等于工人收不到范围和限制（审查 #368-P2-01，原来原样放行）。"""
    note = (f"\n\n【dt-worktree 启动脚本】你在任务单 {task_id} 的独立工作目录里，当前分支 {branch}。只改文件；"
            "不要 git push、不要开 PR、不要切换或新建分支——本轮结束后启动脚本会把改动提交到本单分支，"
            "交给主账号的受信发布核对凭证后推分支、开 PR。")
    try:
        note += scope_note(admitted["scope_text"], admitted["generation"])
    except Refused as e:
        raise Refused(e.reason, task_id)
    out = list(args)
    if executor == "claude" and "-p" in out[:-1]:
        i = out.index("-p") + 1
        out[i] += note
    elif executor == "codex" and len(out) > 1 and out[0] == "exec" and not out[-1].startswith("-"):
        out[-1] += note
    else:
        raise Refused("认不出提示词的位置，五要素放不进去", task_id)
    return out


def kill_group(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def reap_group(pgid, task_id):
    """CLI 退出后清掉它留下的后代进程（整组强杀），确认组里没有进程了才算工作目录稳定。"""
    kill_group(pgid, signal.SIGKILL)
    end = time.time() + 5
    while group_alive(pgid) and time.time() < end:
        time.sleep(0.1)
    if group_alive(pgid):
        log(task_id, "CLI 的进程组清不干净")
        return False
    return True


def run_cli(executor, args, admitted, place):
    """启动 CLI，标准输出直接交给接单程序；args 是 with_note 补好说明和五要素的参数（main 在建工作目录之前就拼好）。
    返回 (退出码, 用时秒, 结局, 后代是否清干净)；结局是 finished（自己结束）、cancelled（接单程序取消）或 over_budget（用时到上限）。"""
    task_id = admitted["task_id"]
    argv = [real_cli(executor), *args]
    left = budget() - admitted["used"]
    started = time.time()
    state = dict(admitted["state"] or {})
    state.update({"task_id": task_id, "generation": admitted["generation"], "scope_hash": admitted["scope_hash"],
                  "github": admitted["github"], "executor": executor, "repo": place["repo"], "base": place["base"],
                  "worktree": place["worktree"], "branch": place["branch"], "seconds_used": admitted["used"],
                  "running": {"started": int(started), "last_seen": int(started),
                              "run": os.environ.get("AGENT_AUTOLOOP_RUN", "")}})
    save_state(task_id, state)
    child = subprocess.Popen(argv, cwd=place["worktree"], env=task_env(task_id), start_new_session=True)
    STOP["pgid"] = child.pid                                     # 新会话：进程组号就是它的进程号
    if stopped():
        kill_group(child.pid, signal.SIGTERM)
    log(task_id, f"第 {admitted['generation']} 代开跑（轮次 {os.environ.get('AGENT_AUTOLOOP_RUN', '?')}，"
                 f"已用 {admitted['used']} 秒）")
    over_at, killed = None, False
    while True:
        now = time.time()
        if stopped() or over_at:
            timeout = 0.2
        else:
            timeout = max(0.2, min(HEARTBEAT, left - (now - started)))
        try:
            code = child.wait(timeout=timeout)
            break
        except subprocess.TimeoutExpired:
            pass
        now = time.time()
        if now - state["running"]["last_seen"] >= HEARTBEAT:
            state["running"]["last_seen"] = int(now)
            save_state(task_id, state)
        if not stopped() and not over_at and now - started >= left:
            over_at = now
            log(task_id, "累计用时到上限，停掉 CLI 整组")
            kill_group(child.pid, signal.SIGTERM)
        due = (STOP["at"] and now - STOP["at"] >= STOP_GRACE) or (over_at and now - over_at >= KILL_GRACE)
        if due and not killed:
            killed = True
            kill_group(child.pid, signal.SIGKILL)
    elapsed = time.time() - started
    if over_at is None and elapsed >= left:      # 掐着上限自己退出的，也按到上限算（审查 P2-06）
        over_at = time.time()
    clean = reap_group(child.pid, task_id)
    STOP["pgid"] = None
    outcome = "cancelled" if stopped() else "over_budget" if over_at else "finished"
    secs = int(elapsed)
    state["seconds_used"] = admitted["used"] + secs
    state["running"] = None
    state.setdefault("turns", []).append({"run": os.environ.get("AGENT_AUTOLOOP_RUN", ""), "generation":
                                          admitted["generation"], "seconds": secs, "exit": code, "outcome": outcome,
                                          "clean": clean, "at": iso(now_tz())})
    save_state(task_id, state)
    return code, secs, outcome, clean


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
    if stopped():                                # 换包前再看一次取消（审查 P1-03）
        tmp.unlink()
        return "cancelled"
    os.replace(tmp, bundle)
    manifest = {"schema": 1, "task_id": task_id, "generation": generation, "executor": executor, "repo": repo,
                "branch": branch, "base": base, "head": head, "bundle": bundle.name, "bundle_sha256": digest,
                "run": os.environ.get("AGENT_AUTOLOOP_RUN", ""), "at": iso(now_tz())}
    mtmp = d / f".handoff.{os.getpid()}.json"
    mtmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.chmod(mtmp, 0o644)
    # 提交点（审查第 3 轮 P1-03）：最后检查加换清单是一段不可打断的临界区（屏蔽取消信号）。进入前已记下的取消：不换清单；
    # 在临界区里到的取消（哪怕早于实际替换）：清单照样换上，解除屏蔽前从挂起信号里记下，after_run 再看一次，照样给中台
    # 「停下了」，中台把这一轮记成已取消、受信发布不认。安全靠取消不丢、取消的轮次不算 done，不靠「取消早于替换就不写清单」。
    old = signal.pthread_sigmask(signal.SIG_BLOCK, STOP_SIGNALS)
    try:
        if stopped():                            # 包已换、清单没换：受信发布按哈希对不上拒收
            mtmp.unlink()
            return "cancelled"
        os.replace(mtmp, d / "handoff.json")
    finally:
        pending = signal.sigpending() & set(STOP_SIGNALS)
        if pending and STOP["sig"] is None:
            STOP["sig"], STOP["at"] = min(pending), time.time()
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
    return manifest


def after_run(executor, repo, context, admitted, place, outcome="finished", clean=True):
    """CLI 退出后：先提交（工作不丢）；取消、超时、后代没清干净、提交后收到取消、凭证失效都不交接（审查 P1-03）。
    返回 None 表示交接了（或没有要交接的改动）；否则返回 (结局, 原因)，由 main 交一条「停下了」。"""
    task_id, gen = admitted["task_id"], admitted["generation"]
    try:
        committed = commit_changes(task_id, place, gen) if clean else False
        kept = "改动已在本地分支提交，" if committed else ""
        if not clean:
            return "stopped", "CLI 的进程没清干净，本轮没提交、没交接"
        if outcome == "cancelled" or stopped():
            return "cancelled", f"本轮被中台取消，{kept}没有交接"
        if outcome == "over_budget":
            return "stopped", f"这张单累计用时到了 {budget() // 60} 分钟上限，本轮已停，{kept}没有交接"
        try:
            admit(executor, repo, context, now_tz(), check_budget=False)
        except Refused as e:
            return "stopped", f"{e.reason}，{kept}没有交接"
        m = write_handoff(executor, repo, task_id, gen, place)
        if m == "cancelled":
            return "cancelled", f"本轮被中台取消，{kept}没有交接"
        if m and stopped():                      # 取消落在提交点之后：清单已换，结论照样是停下了，中台记这一轮已取消
            return "cancelled", "本轮被中台取消；交接清单在取消前已写出，这一轮不算交接"
        log(task_id, f"{'有新提交，' if committed else ''}"
                     f"{'交接包已更新：' + m['head'][:12] if m else '没有相对基线的改动，不交接'}")
        return None
    except (OSError, subprocess.SubprocessError) as e:
        return "stopped", f"提交或交接失败（{type(e).__name__}），没有交接"


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


def stop_text(reason, task_id, notice=True):
    if not notice:
        return f"停下了：{reason}"
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
    install_stop_handler()
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
            cli_args = with_note(executor, args, task_id, branch_name(executor, task_id), admitted)  # 放不进就停，不建工作目录
            try:
                place = {"repo": repo, **prepare_worktree(executor, repo, admitted)}
            except (OSError, subprocess.SubprocessError) as e:
                raise Retry(f"准备工作目录失败（{type(e).__name__}）")
            code, secs, outcome, clean = run_cli(executor, cli_args, admitted, place)
            ended = after_run(executor, repo, context, admitted, place, outcome, clean)
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
    if ended:                                    # 出了问题没交接的一轮，给中台一个明确结论
        kind, reason = ended
        log(task_id, f"停下：{reason}")
        final_message(executor, stop_text(reason, task_id, notice=kind != "cancelled"))
        return 0
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
