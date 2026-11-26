#!/usr/bin/env python3
"""受信发布（最小版，批 0C 执行门，#205；方案 docs/agent-autoloop-底座-方案.md 5.5 不变量 1「乙：受信发布」、第 12 节第 6 条；
规格 docs/秘书代批-规格.md §4「发布侧」）。

dt-worker 不持 GitHub 写凭据：工人侧启动脚本（ops/dt_worktree.py）在 CLI 退出后把本单分支打成交接包
（/Users/dt-worker/dt-outbox/<单号>/handoff.json + <单号>.bundle）。本脚本是控制面的受信发布出口，按 ADR-015 归 dt-gate
账号（「受信发布出口从第一天按归 dt-gate 写」）：取包、核对后推分支、开 PR：

  0. 只在 dt-gate 账号里跑（ADR-015；按有效用户判，不认环境变量）。真发布要 root 写的开关文件开着
     （gate_github.SWITCH，0E-4；没有文件、写法不对一律当关着，常浩 doctor --online 全过后亲手打开），只对开关和代码写死的
     gate_github.REPOS（现在只有 dt-sandbox）都列着的仓库放开：拿到凭证行后先过 gate_github.ready——开关、仓库和名册对得上、
     dt-gate 自己的令牌文件、root 装的 gh 副本、令牌正是登记的机器账号、对这个仓库 admin=false push=true，任何一步不对就不发。
     git、gh 一律用 gate_github 的最小环境（不继承调用方的环境变量，代理只从开关文件取）：令牌只给推送和 gh，取包、核历史、
     取 main、查远端分支都不带；git / gh 的输出进异常和日志前先打码（gate_github.mask）；gh 用 root 副本的路径。
     测试在进程内注入测试后端（TEST_BACKEND：本地裸仓库 file:// + 假 gh），行为照旧、不过开关和 ready；测试后端的远端不是
     file:// 一律拒绝。远端和 gh 都不从环境变量取（审查第 2 轮 P1-07）。
  1. 交接包只当资料：清单逐字段按白名单校验，分支名必须正好是 <执行方>/<单号>；包先复制到私有临时目录、
     核对哈希再用；只取 Git 对象（bundle verify + fetch，打开对象校验，钩子关掉），不检出、不运行里面任何东西。
  2. 凭证以凭证库为准（autoloop_grants.check，和所有出口共用）：动手前 push_branch、open_pr 两个动作一起预检；
     推分支、开 PR 各经出口账本（autoloop_exits.guard，批 0D）：在凭证库写锁里现读凭证、核目标仓库和分支落在这张凭证上，
     同一把锁下记账（executing）才动手，和冻结、撤销串行；做成的不重做，结果不明冻结这张单（task:<单号>）到对账确认，
     恢复闸关着一律不做（docs/恢复门-0D-设计.md 第 6 节）。仓库从凭证取，不信清单。
     清单里的代次不当证据（审查 P1-02）：交接包要落到中台里一轮真实的轮次（清单的 run 号，中台库只读查）——
     这一轮属于这张单（事项、规则、Agent 对得上）、已正常结束、不是被启动脚本挡回去的（最后一条以「停下了」开头的不算）、
     在当前代次开始之后才排进来、它的派单事件都是当前代次；还要有当前代次的正面证据（第 2 轮）：这一轮里有当前代次的
     派单事件，或者它是回答卡片的一轮，顺着卡片找到提问的那一轮（最多 5 层），那一轮同样过这些检查、最终落到当前代次的
     派单；一轮里派单和回答合在一起时，每个回答照样要追溯（第 3 轮）。只有定时复查、没有派单证据的轮次 0C 不发布
     （0D 记账后再放开）。对不上就不发。这套「轮次 → 派单」的追溯是 bind_run，秘书控制面（secretary_gate.py）绑定卡片时共用。
     剩余风险（常浩 11-18 定，记在这里）：正常结束、只是没有改动的轮次（如只向常浩提问）不写「停下了」（补了会盖掉 CLI 的
     回执），它在中台里也是正常结束；改写清单把旧包挂上去，本脚本分辨不出。和下一条同类，0D 结果账本补上。
     工人自己写的提交内容仍不可信（存心作恶的工人能在当前代次里把旧内容重做一遍），内容由凭证、PR 审查、常浩合并把关。
  3. 能机械判的发布边界：包里只有本单分支一个引用、头提交对得上；基线是 main 上的提交、头提交从基线长出来；
     不收合并提交；基线到头提交之间的每一个提交、以及最终差异，都不许碰合并规则 merge_rules.json 里的高风险路径
     （.github/、ops/、密钥类……；审查 P1-01：先加后删的历史也算）——碰了就不发、交常浩。
  3b. 代次边界（#320，常浩 2026-11-24 选甲）：清单第 2 版记这一代的起点 gen_base，必须等于凭证库算出的边界
     （autoloop_grants.boundary：更旧代次最后推上 GitHub 的头提交；没推过就是基线），且是头提交的祖先——作废代次在冻结之后、
     镜像还没刷新时做的提交，不会经新代次的发布进 PR。确认远端分支就是这次的头提交后记进凭证库（record_published），
     下一代的边界从这里算。第 1 版清单（旧启动脚本）只认第 1 代，起点当基线。
  4. 只推非 main：推送目标写死成 refs/heads/<执行方>/<单号>，不带 --force；远端同名分支已有别的提交就不覆盖。
     GitHub 一侧 dt-sandbox 的规则集只放行 main、agent/**、claude/**、codex/**（常浩 11-18 放行，hermes/baselines/dt-sandbox-github.json）。
  5. 同一分支已有开着的 PR，核对它的目标是本仓库 main、来源是本仓库（不是同名 fork 分支）、分支和头提交正是这次的，
     才沿用（审查 P1-05）；对不上就拒绝；已关闭或已合并的不再开新的。标题和正文用凭证库里的五要素，不用工人写的字。

本批只做手动入口：不接中台规则、不装常驻服务、不写入 Hermes 定时任务（ADR-015 生效顺序第 2 条）；测试只推本地裸仓库。
推送和开 PR 用 dt-gate 里机器账号的令牌（0E-4，gate_github.py：只能推非 main、不能合并，#132 的做法），
主账号、秘书账号里没有这份令牌，真跑不起来（ADR-015 第 5 节第 6 条「真实特权不回流」）。
状态只放本脚本自己的目录（运行账号的 DigitalTwin/autoloop/publish/、publish.jsonl），不和主账号其他脚本共用；任务锁例外：
在凭证库旁边（autoloop_grants.task_lock），和续接、秘书 ② / ③ 共用一把（续审第 3 轮）；
凭证库随 autoloop_grants 一起搬进 dt-gate。写前账本、结果不明冻结已接（0D）；接成正式出口在批 3。

  autoloop_publish.py publish --task t-20261118-abcdef
  autoloop_publish.py scan                 只读：列交接目录里的单和各自能不能发
  autoloop_publish.py watch [--interval 60] [--once]
                                         常驻（dt-gate 的服务 cn.digital-twin.gate.publisher）：每隔一会儿扫一遍，
                                         scan 判「可以发」、这个头提交还没处理过的逐张 publish；真发布开关关着就只睡觉
环境变量：DT_PUBLISH_OUTBOX（交接目录，默认 /Users/dt-worker/dt-outbox）、DT_PUBLISH_MIRRORS（主账号的裸仓库副本）、
DT_PUBLISH_LOG，以及 autoloop_grants 的 DT_GRANTS_*。远端固定 https://github.com/<仓库>.git、gh 固定 root 副本
（gate_github.GH），都不能用环境变量改（测试在进程内换 TEST_BACKEND）。
"""
import argparse
import contextlib
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_config as C  # noqa: E402
import autoloop_grants as G  # noqa: E402
import gate_github as GG  # noqa: E402

MAX_MANIFEST = 4096
MAX_BUNDLE = 50 * 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BRANCH = re.compile(r"^(claude|codex)/t-\d{8}-[0-9a-f]{6}$")
KEYS = {"schema", "task_id", "generation", "executor", "repo", "branch", "base", "head", "bundle", "bundle_sha256",
        "run", "at"}                                       # 清单第 1 版（#320 之前的启动脚本）：只认第 1 代
KEYS_V2 = KEYS | {"gen_base"}                              # 第 2 版：多记这一代的起点（#320）
GENERATION_BOUNDARY = 1         # 发布核「本代起点 = 代次边界」（#320）；dt-gate-hub publish on 认这一行才肯打开真发布
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "transfer.fsckObjects=true",
            "-c", "fetch.fsckObjects=true"]
RULES = HERE / "merge_rules.json"
RUN_ID = re.compile(r"^[1-9]\d{0,11}$")
MAX_COMMITS = 500
GATE_USER = "dt-gate"           # ADR-015 控制面账号；测试里在进程内替换，不经环境变量
TEST_BACKEND = None             # 测试在进程内换成 {"remote": "file://…/{github}.git", "gh": 假 gh 路径}；不经环境变量
STOPPED = "停下了"               # 启动脚本出了问题没交接的一轮，最后一条消息以它开头（ops/dt_worktree.stop_text）
MAX_HOPS = 5                    # 回答卡片的一轮往回追提问那一轮，最多几层


class Rejected(Exception):
    """不发：说人话的原因。"""


def branch_name(executor, task_id):
    """和工人侧 ops/dt_worktree.branch_name 同一写法（测试核对两边一致）。"""
    return f"{executor}/{task_id}"


def outbox():
    return Path(os.environ.get("DT_PUBLISH_OUTBOX") or "/Users/dt-worker/dt-outbox")


def mirrors():
    return Path(os.environ.get("DT_PUBLISH_MIRRORS") or G.SUPPORT / "autoloop/publish").expanduser()


def remote_url(github):
    if TEST_BACKEND:
        return TEST_BACKEND["remote"].format(github=github)
    return f"https://github.com/{github}.git"


def log_path():
    return Path(os.environ.get("DT_PUBLISH_LOG") or G.SUPPORT / "autoloop/publish.jsonl").expanduser()


def hub_db():
    return Path(os.environ.get("AGENT_AUTOLOOP_HOME") or "~/.agent-autoloop").expanduser() / "hub.db"


def gh_cmd():
    return TEST_BACKEND["gh"] if TEST_BACKEND else GG.GH          # 真路径用 root 装的 gh 副本（0E-4）


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
    if not isinstance(m, dict) or set(m) not in (KEYS, KEYS_V2) or m.get("schema") != (1 if set(m) == KEYS else 2):
        raise Rejected("交接清单的字段不对")
    if m["schema"] == 1:                                    # 旧版启动脚本：只有第 1 代，起点就是基线
        if m["generation"] != 1:
            raise Rejected("旧格式的交接清单（没记本代起点）只认第 1 代，请用新版启动脚本重跑这一轮")
        m["gen_base"] = m["base"]
    ok = (m["task_id"] == task_id and type(m["generation"]) is int and m["generation"] >= 1
          and m["executor"] in G.EXECUTORS and isinstance(m["repo"], str) and G.REPO_NAME.match(m["repo"])
          and m["branch"] == branch_name(m["executor"], task_id) and isinstance(m["base"], str)
          and SHA.match(m["base"]) and isinstance(m["head"], str) and SHA.match(m["head"])
          and isinstance(m["gen_base"], str) and SHA.match(m["gen_base"])
          and m["bundle"] == f"{task_id}.bundle" and isinstance(m["bundle_sha256"], str)
          and HEX64.match(m["bundle_sha256"]) and isinstance(m["run"], str) and RUN_ID.match(m["run"]))
    if not ok:
        raise Rejected("交接清单的内容不合格式（分支名必须是 <执行方>/<单号>）")
    return m


# ---------- git ----------
def git(*args, cwd=None, check=True, timeout=300, env=None):
    """env=None 只在测试后端用（继承当前环境）；真路径一律传 gate_github 的最小环境，没传就拒（不退回继承调用方的环境、
    PATH 里的 git 和已有登录）。没给工作目录时用最小环境的空家目录。超时 → Rejected（推送时算结果不明：不是 Definite）。"""
    if env is None:
        if not TEST_BACKEND:
            raise Rejected("真发布路径的 git 没拿到最小环境，不跑")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    elif cwd is None:
        cwd = env["HOME"]
    try:
        r = subprocess.run(["git", *GIT_SAFE, *args], cwd=cwd, env=env, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Rejected(f"git {args[0]} 超时（{timeout} 秒）")
    r.stdout, r.stderr = GG.mask(r.stdout), GG.mask(r.stderr)
    if check and r.returncode != 0:
        raise Rejected(f"git {args[0]} 失败：{(r.stderr or r.stdout).strip()[:300]}")
    return r


def ensure_mirror(repo, github, env=None):
    m = mirrors() / f"{repo}.git"
    if not (m / "HEAD").exists():
        m.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(m.parent, 0o700)
        git("init", "--quiet", "--bare", str(m), env=env)
    git("fetch", "--quiet", remote_url(github), "+refs/heads/main:refs/remotes/origin/main", cwd=m, env=env)
    return m


def is_ancestor(mirror, a, b, env=None):
    return git("merge-base", "--is-ancestor", a, b, cwd=mirror, check=False, env=env).returncode == 0


def assert_not_main(branch):
    """推送目标的最后一道写死检查：只推 <执行方>/<单号> 形式的本单分支。"""
    if not BRANCH.match(branch or ""):
        raise Rejected(f"不推这个分支：{branch}")


@contextlib.contextmanager
def task_lock(task_id):
    """这张单的任务锁：和续接、秘书 ② / ③ 共用 autoloop_grants.task_lock（凭证库旁边的 task-locks/，续审第 3 轮）。"""
    try:
        lock = G.task_lock(task_id)
        lock.__enter__()
    except G.Busy:
        raise Rejected("这张单另有入口正在处理（另一个发布、秘书 ② / ③ 或续接）")
    except G.Rejected as e:
        raise Rejected(str(e))
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


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


# ---------- 运行身份、轮次绑定、历史检查 ----------
def require_gate():
    """受信发布只在 dt-gate 账号里跑（ADR-015）：按有效用户判，主账号、秘书账号、工人账号一律拒绝。"""
    try:
        me = pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        me = str(os.geteuid())
    if me != GATE_USER:
        raise Rejected(f"受信发布只在 {GATE_USER} 账号里跑（ADR-015），现在是 {me}")


def require_publish_enabled():
    """测试后端只认本地裸仓库（file://）；没有测试后端时 root 写的真发布开关必须开着（0E-4），先挡一道，
    拿到凭证行后 publish_envs 再按仓库完整核一遍。"""
    if TEST_BACKEND:
        if not str(TEST_BACKEND.get("remote", "")).startswith("file://"):
            raise Rejected("测试后端只能是本地裸仓库（file://），不推真远端")
        return
    try:
        GG.read_switch()
    except GG.NotReady as e:
        raise Rejected(f"推分支、开 PR 都不做：{e}")


def publish_envs(repo, github):
    """git / gh 用的环境：(不带令牌的, 带令牌的)。测试后端返回 (None, None)，行为不变；真路径先过 gate_github.ready
    （开关、仓库、令牌、gh 副本、账号和权限都对）。带令牌的只给推送和 gh。不对 → Rejected，不发。"""
    if TEST_BACKEND:
        return None, None
    try:
        return GG.ready(repo, github)
    except GG.NotReady as e:
        raise Rejected(f"发布凭据没就绪：{e}")


def _hub():
    import sqlite3
    import urllib.parse
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(hub_db()))}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def hub_run(run_id):
    """中台库只读：这一轮的事项、规则、Agent、状态、排进来的时间、最后一条消息的首行和它的事件。查不到返回 None。"""
    import sqlite3
    try:
        con = _hub()
    except sqlite3.Error:
        return None
    try:
        r = con.execute("SELECT id, rule, agent, lane, state, created_at, summary FROM runs WHERE id = ?",
                        (int(run_id),)).fetchone()
        if r is None:
            return None
        events = [dict(e) for e in con.execute(
            "SELECT e.id AS id, e.source AS source, e.\"key\" AS key, e.type AS type, e.subject AS subject, e.detail AS detail "
            "FROM run_events re JOIN events e ON e.id = re.event_id WHERE re.run_id = ?", (int(run_id),))]
        return {**dict(r), "events": events}
    except sqlite3.Error:
        return None
    finally:
        con.close()


CARD_COLUMNS = ("id", "key", "kind", "state", "version", "title", "text", "options", "choice", "by", "rule", "lane",
                "source", "run_id", "created_at", "decided_at", "closed_at")
CARD_SELECT = ", ".join(f'"{c}"' for c in CARD_COLUMNS)      # by、key 是 SQL 关键字，列名都加引号


def hub_card(card_id):
    """中台库只读：一张卡片的种类、事项、提问的那一轮，以及秘书控制面要的状态、版本、选项、回答人（#195 0B-4c 起多取这几列，
    发布只用前几项）。卡片号在中台库里是整数（视图列没有类型亲和性，按字符串查永远查不到），不是正整数写法的直接当查不到。
    查不到返回 None。"""
    import sqlite3
    if not re.fullmatch(r"[1-9]\d{0,11}", str(card_id)):
        return None
    try:
        con = _hub()
    except sqlite3.Error:
        return None
    try:
        r = con.execute(f"SELECT {CARD_SELECT} FROM cards WHERE id = ?", (int(card_id),)).fetchone()
        return dict(r) if r else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def _run_basics(run, row, generation, states=("done",), on_dispatch=None):
    """一轮本身的检查：属于这张单、正常结束、不是被挡回去的、在当前代次开始后排进来、事件来源和派单代次都对。
    states：这一轮可以处在哪些终态（发布只认 done；秘书 ② 认中台放弃的 cancelled，只用于最外层那一轮）。
    on_dispatch(事件)：每个派单事件额外核一次（秘书控制面用它核网关记账），返回拒绝原因或 None；不给就不核（发布原样）。"""
    if run is None:
        return "中台里查不到这一轮"
    task_id = row["task_id"]
    if (run["lane"], run["rule"], run["agent"]) != (task_id, row["rule"], C.AGENTS[row["executor"]][0]):
        return "这一轮不属于这张单"
    if run["state"] not in states:
        return f"这一轮没有正常结束（{run['state']}）"
    if str(run.get("summary") or "").lstrip().startswith(STOPPED):
        return "这一轮被启动脚本挡回去了（停下了），不算数"
    try:
        created = G.dt.datetime.fromisoformat(str(run["created_at"]).replace("Z", "+00:00"))
        started = G.parse_iso(row["generation_started_at"])
    except (TypeError, ValueError):
        return "这一轮或代次的时间读不懂"
    if created < started:
        return f"这一轮在第 {generation} 代开始之前就排进来了"
    for e in run["events"]:
        if e.get("source") != C.SOURCE or e.get("subject") != task_id:
            return "这一轮有不是派单网关为这张单发的事件"
        if e.get("type") == G.event_type(row["repo"], row["executor"]):
            try:
                g = json.loads(e.get("detail") or "").get("generation")
            except (ValueError, AttributeError):
                return "这一轮的派单事件读不懂"
            if g != generation:
                return f"这一轮的派单事件是第 {g} 代，不是第 {generation} 代"
            reason = on_dispatch(e) if on_dispatch else None
            if reason:
                return reason
    return None


def _generation_evidence(run, row, generation, depth, on_dispatch=None):
    """当前代次的正面证据（审查第 2 轮 P1-02）：这一轮有当前代次的派单事件，或者有回答卡片的事件；
    不管有没有派单，每个回答都要顺着卡片追到提问的那一轮，那一轮也过 _run_basics 并最终落到当前代次的派单
    （第 3 轮：派单和旧回答合在同一轮时，不许因为有派单就跳过回答）。"""
    dispatch = G.event_type(row["repo"], row["executor"])
    has_dispatch = any(e.get("type") == dispatch for e in run["events"])   # 代次已在 _run_basics 里核过等于当前
    answers = [e for e in run["events"] if e.get("type") == "card.answered"]
    if not has_dispatch and not answers:
        return "这一轮没有当前代次的派单证据（只有定时复查的轮次 0C 不发布，0D 记账后再放开）"
    if answers and depth >= MAX_HOPS:
        return f"顺着回答卡片往回追了 {MAX_HOPS} 层还没找到派单，不认"
    for e in answers:
        key = str(e.get("key") or "")
        card = hub_card(key[len("card:"):]) if key.startswith("card:") else None
        if not card or card.get("lane") != row["task_id"] or card.get("kind") == "notice" or not card.get("run_id"):
            return "回答事件追溯不到这张单提问的那一轮"
        asked = hub_run(card["run_id"])
        reason = (_run_basics(asked, row, generation, on_dispatch=on_dispatch)
                  or _generation_evidence(asked, row, generation, depth + 1, on_dispatch))
        if reason:
            return f"提问的那一轮：{reason}"
    return None


def bind_run(run_id, row, generation, states=("done",), on_dispatch=None):
    """一轮能不能追到这张单当前代次的派单（发布和秘书控制面共用）：_run_basics + 当前代次的正面证据（回答卡片的一轮顺着卡片往回追）。
    返回 None 表示绑得上，否则返回原因。参数见 _run_basics。"""
    run = hub_run(run_id)
    return (_run_basics(run, row, generation, states, on_dispatch)
            or _generation_evidence(run, row, generation, 0, on_dispatch))


def check_run(run_id, row, generation):
    """交接包落到的那一轮，必须是当前代次里这张单正常结束、有正面代次证据的一轮（审查 P1-02）。返回 None 表示通过。"""
    reason = bind_run(run_id, row, generation)
    return f"交接包对应的那一轮：{reason}" if reason else None


def history_hits(mirror, base, head, repo, env=None):
    """基线到头提交之间每个提交的改动路径，加上最终差异，碰到高风险路径的都列出来；有合并提交直接拒绝。"""
    if git("rev-list", "--min-parents=2", f"{base}..{head}", cwd=mirror, env=env).stdout.split():
        raise Rejected("交接里有合并提交，不自动发布")
    commits = git("rev-list", f"{base}..{head}", cwd=mirror, env=env).stdout.split()
    if len(commits) > MAX_COMMITS:
        raise Rejected(f"交接里提交太多（{len(commits)} 个），不自动发布")
    paths = set(git("diff", "--name-only", "-z", "--no-renames", base, head, cwd=mirror, env=env).stdout.split("\0"))
    for c in commits:
        paths |= set(git("diff-tree", "-r", "--no-commit-id", "--name-only", "-z", "--no-renames", c,
                         cwd=mirror, env=env).stdout.split("\0"))
    pats = sensitive_patterns(repo)
    return sorted(f for f in paths if f and any(pt.search(f) for pt in pats))


# ---------- 发布 ----------
def publish(task_id, now=None, path=None):
    require_gate()
    require_publish_enabled()
    now = now or G.now_tz()
    m = read_manifest(task_id)
    with task_lock(task_id):
        gen = m["generation"]
        for action in ("push_branch", "open_pr"):            # 两个动作一起预检（审查 P2-01）
            must(task_id, action, gen, now, path)
        with G.tx(path) as con:
            row = dict(G.grant_row(con, task_id))
            edge = G.boundary(con, task_id, gen)
        if (row["repo"], row["executor"]) != (m["repo"], m["executor"]):
            raise Rejected("交接清单的仓库或执行方和凭证不一致")
        if m["gen_base"] != (edge or m["base"]):           # 代次边界（#320）：这一代必须从旧代次最后推出去的提交起
            raise Rejected(f"第 {gen} 代的起点 {m['gen_base'][:12]} 不是代次边界 {(edge or m['base'])[:12]}"
                           f"（{'旧代次最后推上 GitHub 的头提交' if edge else '基线'}）：分支上可能有作废代次的提交，不发，交常浩")
        github, branch, head, base = row["github"], m["branch"], m["head"], m["base"]
        assert_not_main(branch)
        reason = check_run(m["run"], row, gen)
        if reason:
            raise Rejected(reason)
        local, authed = publish_envs(row["repo"], github)
        mirror = ensure_mirror(row["repo"], github, local)
        with tempfile.TemporaryDirectory(prefix="dt-publish-") as tmp:
            data = _read_regular(outbox() / task_id / m["bundle"], MAX_BUNDLE, "交接包")
            if hashlib.sha256(data).hexdigest() != m["bundle_sha256"]:
                raise Rejected("交接包和清单的哈希对不上（可能正在更新，稍后再试）")
            bundle = Path(tmp) / "handoff.bundle"
            bundle.write_bytes(data)
            git("bundle", "verify", "--quiet", str(bundle), cwd=mirror, env=local)
            heads = git("bundle", "list-heads", str(bundle), cwd=mirror, env=local).stdout.split()
            if heads != [head, f"refs/heads/{branch}"]:
                raise Rejected("交接包里不只有本单分支，或头提交对不上")
            git("fetch", "--quiet", "--no-tags", str(bundle), f"+refs/heads/{branch}:refs/dt-publish/{task_id}",
                cwd=mirror, env=local)
        if git("rev-parse", f"refs/dt-publish/{task_id}", cwd=mirror, env=local).stdout.strip() != head:
            raise Rejected("取进来的头提交对不上")
        if not is_ancestor(mirror, base, "refs/remotes/origin/main", local) or not is_ancestor(mirror, base, head, local):
            raise Rejected("基线不是 main 上的提交，或头提交不是从基线长出来的")
        if not is_ancestor(mirror, m["gen_base"], head, local):
            raise Rejected("头提交不是从这一代的起点长出来的")
        hits = history_hits(mirror, base, head, row["repo"], local)
        if hits:
            raise Rejected(f"改动或提交历史碰到高风险路径（{'、'.join(hits[:5])}），不自动发布，交常浩")
        url = remote_url(github)
        remote = git("ls-remote", url, f"refs/heads/{branch}", cwd=mirror, env=local).stdout.split()
        existing = remote[0] if remote else None
        pushed = False
        if existing != head:
            if existing and not (SHA.match(existing) and is_ancestor(mirror, existing, head, local)):
                raise Rejected("远端同名分支已有别的提交，不覆盖")
            got = exit_guard("push_branch", task_id, gen, branch, head,     # 推之前在凭证库锁里现读（取代 must）
                             {"github": github, "remote": url, "branch": branch, "head": head, "before": existing},
                             lambda: push_branch(mirror, url, branch, head, authed))
            pushed = not got["again"]
        G.record_published(task_id, gen, head, path=path)  # 远端分支现在就是 head：记进凭证库，下一代的边界从这里算（#320）
        pr = exit_guard("open_pr", task_id, gen, branch, head, {"github": github, "branch": branch, "head": head},
                        lambda: _open_pr_once(github, branch, head, row, authed))
        pr = {"pr": pr.get("pr"), "pr_opened": bool(pr.get("pr_opened")) and not pr["again"]}
        out = {"task_id": task_id, "generation": gen, "github": github, "branch": branch, "head": head,
               "run": m["run"], "pushed": pushed, **pr}
        record({"what": "发布", **out})
        return out


# ---------- 出口账本（批 0D 恢复门，docs/恢复门-0D-设计.md 第 6 节） ----------
NOT_SENT = ("Could not resolve host", "Failed to connect", "Connection refused", "Couldn't connect",
            "[rejected]", "[remote rejected]",   # 推送确定没落到远端：没连上，或远端明确拒绝（拒绝也是权威的「没推」）
            "Authentication failed", "could not read Username", "could not read Password",
            "The requested URL returned error: 401", "The requested URL returned error: 403")   # 认证没过：0E-4


class PrUnknown(Rejected):
    """gh pr create 报错：分不清 PR 开没开，结果不明（不当成确定没开）。"""


def exit_guard(kind, task_id, gen, branch, head, target, do):
    """推分支、开 PR 都经出口账本：先记账再动手，做成的不重做，结果不明冻结这张单（task:<单号>）等对账。
    凭证在凭证库写锁里现读，executing 在同一把锁下落盘（和冻结、撤销串行；取代原来的 must 现读复核），
    目标仓库和分支必须落在这张凭证上（autoloop_exits.bound）。恢复闸关着、范围冻着 → Rejected，不动手。
    返回出口账本的结果（again=True 表示以前已做成，这次没再做）。结果不明的异常原样抛出（调用方记日志）。"""
    import autoloop_exits as E
    try:
        return E.guard(kind, f"{kind}:{task_id}:{branch}:{head}", f"task:{task_id}", target, do,
                       task=(task_id, kind, gen))
    except E.Refused as e:
        raise Rejected(str(e))
    except E.Definite as e:
        raise Rejected(f"确定没做成（可以再试）：{e}")


def push_branch(mirror, url, branch, head, env=None):
    """真正的推送。没连上、远端明确拒绝 → 确定没推（Definite）；其余报错、超时 → 结果不明（原样抛出）。"""
    import autoloop_exits as E
    try:
        r = git("push", "--quiet", "--no-verify", url, f"{head}:refs/heads/{branch}", cwd=mirror, check=False, env=env)
    except OSError as e:                                     # git 本身没跑起来：确定没推
        raise E.Definite(f"git 跑不起来（{type(e).__name__}）")
    if r.returncode == 0:
        return {"pushed": head}
    err = (r.stderr or r.stdout).strip()
    if any(s in err for s in NOT_SENT):
        raise E.Definite(f"推送没落到远端：{err[:200]}")
    raise Rejected(f"git push 失败、分不清推没推：{err[:200]}")


def _open_pr_once(github, branch, head, row, env=None):
    """开 PR 的出口动作：建之前的查询和核对出问题 → 确定没开（Definite）；建的那一步报错 → 结果不明。"""
    import autoloop_exits as E
    try:
        return open_pr(github, branch, head, row, env)
    except PrUnknown:
        raise
    except Rejected as e:
        raise E.Definite(str(e))


PR_FIELDS = "number,url,state,baseRefName,headRefName,headRefOid,isCrossRepository,headRepository,headRepositoryOwner"


def pr_is_ours(p, github, branch, head):
    """已有 PR 必须正是这次的：目标本仓库 main、来源本仓库、本单分支、头提交一致（审查 P1-05）。"""
    owner, name = github.split("/")
    src_owner = ((p.get("headRepositoryOwner") or {}).get("login") or "").lower()
    src_name = ((p.get("headRepository") or {}).get("name") or "").lower()
    return (p.get("baseRefName") == "main" and p.get("headRefName") == branch and p.get("headRefOid") == head
            and p.get("isCrossRepository") is False and src_owner == owner.lower() and src_name == name.lower())


def open_pr(github, branch, head, row, env=None):
    """env=None 只在测试后端用；真路径传 gate_github 的最小环境（带令牌），没传就拒。gh 的输出先打码。"""
    if env is None and not TEST_BACKEND:
        raise Rejected("真发布路径的 gh 没拿到最小环境，不跑")
    cwd = env["HOME"] if env else None
    r = subprocess.run([gh_cmd(), "pr", "list", "-R", github, "--head", branch, "--state", "all",
                        "--json", PR_FIELDS], capture_output=True, text=True, timeout=60, env=env, cwd=cwd)
    if r.returncode != 0:
        raise Rejected(f"查 PR 失败：{GG.mask(r.stderr).strip()[:200]}")
    prs = json.loads(r.stdout or "[]")
    live = [p for p in prs if p.get("state") == "OPEN"]
    if live:
        if len(live) == 1 and pr_is_ours(live[0], github, branch, head):
            return {"pr": live[0].get("url"), "pr_opened": False}
        raise Rejected("同名分支已有开着的 PR，但目标、来源、分支或头提交对不上，不沿用")
    if prs:
        raise Rejected("这条分支的 PR 已关闭或已合并，不再开新的；要继续请常浩处理")
    first = row["scope_text"].strip().splitlines()[0][:80]
    body = "\n".join([f"任务单：{row['task_id']}（第 {row['generation']} 代）", f"执行方：{row['executor']}",
                      f"范围哈希：{row['scope_hash']}", "",
                      "由受信发布（autoloop_publish.py）开出：提交来自工人账号 dt-worker，控制面只取 Git 对象、核过凭证。",
                      "合并照 B 档：常浩点合并卡片或在电脑上处理。", "", "## 五要素", "", row["scope_text"]])
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(body)
    try:
        r = subprocess.run([gh_cmd(), "pr", "create", "-R", github, "-B", "main", "-H", branch,
                            "-t", f"{row['task_id']}：{first}", "-F", f.name], capture_output=True, text=True,
                           timeout=120, env=env, cwd=cwd)
    finally:
        os.unlink(f.name)
    if r.returncode != 0:                                    # 建的那一步报错：PR 可能已经开了，结果不明（对账查 PR 列表）
        raise PrUnknown(f"开 PR 报错、分不清开没开：{GG.mask(r.stderr).strip()[:200]}")
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


def _done_path():
    return log_path().parent / "publish-watch-done.json"


def _load_done():
    try:
        d = json.loads(_done_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_done(d):
    p = _done_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, p)


def watch_once(now=None, path=None):
    """扫一遍：开关开着时，把 scan 判「可以发」、这个头提交还没处理过的单逐张发布。
    发成了、拒发了都记进 publish-watch-done.json（按单号 → 头提交），同一个头提交不再试；出错（超时、跑不起来）不记，下一轮再试，
    真推过、结果不明的由出口账本挡住（再试会被拒、随后记成处理过）。返回这一轮处理了哪些单。"""
    require_gate()
    try:
        require_publish_enabled()
    except Rejected:
        return []                                            # 开关关着：什么都不做
    done, out = _load_done(), []
    for item in scan(now=now, path=path):
        if item.get("check") != "可以发":
            continue
        tid = item["task_id"]
        try:
            head = read_manifest(tid)["head"]
        except Rejected:
            continue
        if done.get(tid) == head:
            continue
        try:
            res = publish(tid, now=now, path=path)
            entry = {"task_id": tid, "ok": True, "pr": res.get("pr")}
        except (Rejected, G.Rejected) as e:
            with contextlib.suppress(OSError):
                record({"what": "拒发", "task_id": tid, "reason": str(e), "by": "watch"})
            entry = {"task_id": tid, "ok": False, "reason": str(e)}
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            with contextlib.suppress(OSError):
                record({"what": "出错", "task_id": tid, "reason": GG.mask(str(e))[:300], "by": "watch"})
            out.append({"task_id": tid, "ok": False, "retry": True})
            continue
        done[tid] = head
        _save_done(done)
        out.append(entry)
    return out


def watch(interval=60):
    import time
    while True:
        try:
            for r in watch_once():
                print(json.dumps({"at": G.iso(G.now_tz()), **r}, ensure_ascii=False), flush=True)
        except Exception as e:                               # 常驻：一轮出错只记一行，下一轮照跑
            print(json.dumps({"at": G.iso(G.now_tz()), "error": f"{type(e).__name__}: {GG.mask(str(e))[:300]}"},
                             ensure_ascii=False), flush=True)
        time.sleep(interval)


def main(argv=None):
    ap = argparse.ArgumentParser(description="受信发布（最小版）：取 dt-worker 的交接包，核凭证后推非 main 分支、开 PR")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--task", required=True)
    sub.add_parser("scan")
    w = sub.add_parser("watch")
    w.add_argument("--interval", type=int, default=60)
    w.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "watch":
        if a.once:
            print(json.dumps({"ok": True, "result": watch_once()}, ensure_ascii=False, indent=1))
            return 0
        watch(max(10, a.interval))
        return 0
    try:
        out = publish(a.task) if a.cmd == "publish" else scan()
    except (Rejected, G.Rejected) as e:
        if a.cmd == "publish":
            with contextlib.suppress(OSError):
                record({"what": "拒发", "task_id": a.task, "reason": str(e)})
        print(json.dumps({"ok": False, "reason": str(e)}, ensure_ascii=False))
        return 1
    except (subprocess.SubprocessError, OSError, ValueError) as e:   # 超时、跑不起来、输出读不懂：留痕，不只是堆栈
        reason = f"出错（{type(e).__name__}）：{GG.mask(str(e))[:300]}；推分支、开 PR 若已开始，出口账本记结果不明"
        if a.cmd == "publish":
            with contextlib.suppress(OSError):
                record({"what": "出错", "task_id": a.task, "reason": reason})
        print(json.dumps({"ok": False, "reason": reason}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "result": out}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
