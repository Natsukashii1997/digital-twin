"""中台和网关搬进 dt-gate（ADR-015 批 0E-2）：常浩在主账号里用 sudo 跑，可重复跑、先备份、能回滚。

用法（主账号终端，要输管理员密码）：
  第一次（合并后在仓库主目录 main 上，只这一次从仓库跑）：
  sudo /usr/bin/python3 -B -E -s ops/dt_gate_hub.py stage --engine-repo <引擎仓库> --engine-commit <提交>
       --repo <本仓库> --repo-commit <提交> --node <node 程序>
                         把要在 dt-gate 里运行的代码复制成 root 所有的只读副本（node、锁定提交的引擎、本仓库控制面脚本和名册），
                         另写一个 root 所有的入口 gate/bin/dt-gate-hub
  之后一律用 root 装的入口（GATE=/Library/Application Support/DigitalTwin/gate；仓库里这份主账号能改，sudo 跑它等于把 root
  交给主账号里的任何进程，所以除 stage 以外的命令只认副本，独立审查 P1-1）：
  sudo "$GATE/bin/dt-gate-hub" preflight          只看：账号、目录、副本、主账号中台能不能搬
  sudo "$GATE/bin/dt-gate-hub" migrate --confirm  搬家（见下）；任何一步出错自动退回主账号中台
  sudo "$GATE/bin/dt-gate-hub" windows --confirm  装只读窗口（3352）和秘书窄窗口（3353）两个服务，令牌给主账号一份
  sudo "$GATE/bin/dt-gate-hub" rotate-worker --confirm   给 dt-worker 换接单钥匙；工人接单程序重启后生效
  sudo "$GATE/bin/dt-gate-hub" stage …            换新副本（旧的记成 previous.json）
  sudo "$GATE/bin/dt-gate-hub" update --confirm   把已装的服务换到新副本并重启；新的起不来自动退回旧副本
  sudo "$GATE/bin/dt-gate-hub" run <工具> [参数…]  以 dt-gate 身份跑控制面工具（白名单见 TOOLS、CLI_ALLOWED）
  sudo "$GATE/bin/dt-gate-hub" status             只看：副本版本、gh 副本、真发布开关、搬家记录、三个服务在不在跑、中台健康检查
受信发布的凭据和开关（0E-4，#319；照抄清单 docs/DT-ORC/migration/0E-4-放开受信发布-照抄清单.md）：
  sudo "$GATE/bin/dt-gate-hub" stage … --gh <gh 程序>   连同 gh 一起 stage：复制成 root 的 GATE/bin/gh（记 SHA-256 和版本）；
                         不带 --gh 时保留已有的 gh 副本
  sudo "$GATE/bin/dt-gate-hub" put-publish-token --confirm   终端里粘贴机器账号的令牌（不显示、不进命令行），以 dt-gate 身份
                         写进它的 ~/.dt/github-publish.token（600），再以 dt-gate 身份核令牌的账号和权限（只打印账号名和权限）
  sudo "$GATE/bin/dt-gate-hub" publish on --confirm [--repo dt-sandbox] [--proxy http://127.0.0.1:<端口>]
                         打开真发布：副本里的受信发布要有代次边界（#320），以 dt-gate 身份 doctor --online 全过，才写 root 的
                         开关文件 GATE/publish.json，写完以 dt-gate 身份回读，读不出「开着」就删掉、算没打开
  sudo "$GATE/bin/dt-gate-hub" publish off        关真发布：删掉开关文件（关永远允许，不用 --confirm）
  sudo "$GATE/bin/dt-gate-hub" rollback --confirm 退回搬家前；搬家中途被打断（进程被杀、关了终端）也用它收拾

migrate 做的事（顺序固定；每一步做完记进 gate/migrating.json，被打断也能 rollback）：
  0. 核对（同 preflight，含主账号 0D 恢复闸 leftovers：不在维护；出口账本、关闸标记、本人点击、秘书代批、派单事件里
     没有结果不明 / 核不清 / 还在送 / 排着队的）；主账号控制面停写（autoloop_recovery.py hold，恢复点 gate-migrate-<时间>），
     等在途的写做完（writes 为空，最多 2 分钟），再看一遍 leftovers 为空
  1. 停掉主账号的中台（launchctl bootout + disable，等它真退出、端口空出来），停下后再核一次
  2. 以主账号身份用 dt-gate 那份引擎做备份（engine backup），root 把备份收进只有 root 能读的 gate/backups/<时间>/，
     主账号里的临时备份随即删掉
  3. 以 dt-gate 身份导入（engine restore）；config.json 只留 url、hub；master.key 换新；签新的本人密钥写进 config.json
  4. 装 LaunchDaemon cn.digital-twin.gate.hub（UserName dt-gate），等端口上确实是 dt-gate 的进程在听、健康检查通过，
     核对轮次、卡片、暂停、规则文件、事件编号和搬家前一致
  5. 删掉中台里主账号见过的钥匙：旧本人密钥、local / hermes 来源、desktop 通道、主账号的接单程序，只留新本人密钥和 dt-worker
  6. 主账号的 ~/.agent-autoloop 改名为 ~/.agent-autoloop.moved-to-gate-<时间>，原位置放一个说明文件（主账号的命令行不会
     悄悄新建一个空中台），LaunchAgent 挪进主账号的 evidence（不删，回滚用）
  搬完主账号控制面保持停写（主账号不再做受保护的写）；出错退回、rollback 时用同一个恢复点结束维护（release rolled_back）。
  控制面账本不搬：dt-gate 的恢复闸从新账本开始、关着，锁定中台版本（run autoloop_recovery pin，常浩说了才跑）并对账后才开。

安全约定：
  - root 不按路径碰任何账号家目录里的东西：要在 dt-gate、主账号、dt-worker 家里做的，都以那个账号的身份跑
    ops/dt_gate_user.py（用的是 root 装好的只读副本）；root 只在「从 / 起每一级只有 root 能改」的目录里写
    （核对函数在 ops/dt_root_guard.sh，和 0E-1 共用）。
  - dt-gate 里运行的一切（node、引擎、Python 脚本）都是 root 所有的副本，主账号改不了；PATH 只有系统目录。
  - 本机端口谁都能抢绑：dt-gate 的服务带钥匙访问中台前，先核对端口上在听的进程属于 dt-gate（hermes/scripts/hub_listener.py）；
    本脚本等服务起来时也按监听进程的 uid 核。
  - 钥匙和令牌不打印、不进日志；本脚本只在进程里转交（主账号 → root 备份 → dt-gate），常浩和 AI 都看不到内容。
  - 不签新的来源钥匙、通道钥匙（hermes 来源在 dt-gate 里重签、飞书 / 秘书通道钥匙，常浩说了才签）。

环境变量（测试用；正式运行不用设）：DT_GATE_ROOT、DT_GATE_SHARED、DT_GATE_LOGS、DT_GATE_DAEMONS、DT_GATE_ACCOUNT、
DT_GATE_WORKER_ACCOUNT、DT_GATE_USERS、DT_GATE_NODE_TEAM、DT_GATE_READONLY_PORT、DT_GATE_INTAKE_PORT、DT_ROOT_GUARD、
DT_GATE_ANY_COPY（测试里允许从仓库跑 stage 以外的命令）。
"""
import argparse
import datetime as dt
import errno
import hashlib
import json
import os
import plistlib
import posixpath
import pwd
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hermes/scripts"))
import hub_listener  # noqa: E402

PY = "/usr/bin/python3"
PY_FLAGS = ("-B", "-E", "-s")          # 不写缓存、不认 PYTHON* 环境变量和用户目录；脚本所在目录（root 的副本）照样在 sys.path
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
OWNER_AGENT = "agent-autoloop"         # 主账号中台的 LaunchAgent 标签
LABEL = {"hub": "cn.digital-twin.gate.hub", "readonly": "cn.digital-twin.gate.readonly",
         "intake": "cn.digital-twin.gate.intake"}
HUB_FILES = ("hub.db", "master.key", "config.json", "rules.json", "secrets.env")   # engine backup 的全部内容
NEEDED = ("hub.db", "master.key", "config.json", "rules.json")
REPO_FILES = ("projects.json", "ops/dt_gate_user.py", "ops/dt_gate_hub.py", "ops/dt_root_guard.sh", "ops/dt_worktree.py")
# run 子命令能以 dt-gate 身份跑的控制面工具（都在副本的 hermes/scripts 下）
TOOLS = ("autoloop_grants", "autoloop_config", "autoloop_readonly", "autoloop_feishu", "autoloop_answer",
         "autoloop_publish", "autoloop_recovery", "autoloop_exits", "secretary", "secretary_ledger", "secretary_gate",
         "custody", "custody_ledger", "twin_state", "gate_github")
# 中台命令行只放行看和停的子命令：key new / backup / restore / init / service 会把钥匙打到终端或换掉数据，不经这里
CLI_ALLOWED = ("status", "runs", "why", "rules", "pause", "resume", "stop", "doctor")
# git 取代码不受主账号的配置、替换对象、属性文件影响（独立审查 P2-8）
GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_ATTR_NOSYSTEM": "1"}
GIT = ("/usr/bin/git", "-c", "core.attributesFile=/dev/null")
MARK = "agent-autoloop 中台已搬进 dt-gate"   # 主账号原位置的说明文件开头
SHA = re.compile(r"^[0-9a-f]{40}$")
DRAIN_SECONDS = 120                    # 停写以后等在途的写做完最多几秒（0D 恢复门「停写 → 排空」）
GH_MIN_BYTES = 1_000_000               # gh 程序再小就不像真的（防把空文件、脚本当成 gh 装进去）
# 副本里的受信发布有代次边界（#320）才许打开真发布：DECIDED 11-22「机器级边界转 #320，放开 REAL_PUBLISH 之前必须合并」
BOUNDARY_MARK = re.compile(r"^GENERATION_BOUNDARY = 1\b", re.M)


class Stop(Exception):
    """停下：说清原因，不再往下做。"""


def env_path(name, default):
    return Path(os.environ.get(name) or default)


def gate_root():
    return env_path("DT_GATE_ROOT", "/Library/Application Support/DigitalTwin/gate")


def launchers():
    """工人侧启动脚本装在哪（中台 Agent dt-claude / dt-codex 的 program 指到这里，0C）。"""
    return env_path("DT_GATE_LAUNCHERS", "/Users/Shared/dt-autoloop/bin")


def shared():
    return env_path("DT_GATE_SHARED", "/Users/Shared/dt-gate")


def logs():
    return env_path("DT_GATE_LOGS", "/Library/Logs/DigitalTwin/gate")


def daemons():
    return env_path("DT_GATE_DAEMONS", "/Library/LaunchDaemons")


def gate_account():
    return os.environ.get("DT_GATE_ACCOUNT") or "dt-gate"


def worker_account():
    return os.environ.get("DT_GATE_WORKER_ACCOUNT") or "dt-worker"


def check_port(v, what):
    """端口只认 1–65535 的整数（0 会让服务绑随机端口、核的却是 0，L3 第 3 轮 P2-02）；不合格停下，不猜。"""
    if isinstance(v, bool):
        raise Stop(f"{what} 不是端口号：{v!r}")
    try:
        p = int(v)
    except (TypeError, ValueError):
        raise Stop(f"{what} 不是端口号：{v!r}")
    if not 1 <= p <= 65535 or str(v).strip() != str(p):
        raise Stop(f"{what} 不是 1–65535 的端口号：{v!r}")
    return p


def port(name, default):
    v = os.environ.get(name)
    return check_port(default if v in (None, "") else v, name)


def now_stamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def entry():
    return gate_root() / "bin/dt-gate-hub"


def from_staged_copy():
    try:
        return HERE.parent.parent == (gate_root() / "repo").resolve()
    except OSError:
        return False


# ---------- 系统调用（测试换成假的） ----------
class Host:
    def is_root(self):
        return os.geteuid() == 0

    def account(self, name):
        p = pwd.getpwnam(name)
        return {"name": name, "uid": p.pw_uid, "gid": p.pw_gid, "home": p.pw_dir}

    def _argv(self, name, argv, env):
        a = self.account(name)
        base = {"HOME": a["home"], "PATH": SAFE_PATH, "USER": name, "LOGNAME": name, "LC_ALL": "en_US.UTF-8"}
        base.update(env or {})
        return ["/usr/bin/sudo", "-u", name, "-H", "/usr/bin/env", "-i", *[f"{k}={v}" for k, v in base.items()], *argv]

    def as_user(self, name, argv, stdin=None, env=None, timeout=600):
        """以账号身份跑，从 / 出发（主账号家目录 700，别的账号进不去）；返回 (退出码, 标准输出字节, 标准错误文本)。"""
        r = subprocess.run(self._argv(name, argv, env), input=stdin, capture_output=True, cwd="/", timeout=timeout)
        return r.returncode, r.stdout, r.stderr.decode("utf-8", "replace")

    def launchctl(self, *args):
        r = subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)
        return r.returncode, (r.stdout + r.stderr).strip()

    def guard(self, fn, *args):
        """dt_root_guard.sh 里的核对函数；退出码原样返回（is_member 有 0 / 1 / 2 三种）。"""
        f = os.environ.get("DT_ROOT_GUARD") or str(HERE / "dt_root_guard.sh")
        r = subprocess.run(["/bin/bash", "-c", '. "$0" || exit 9; "$@"', f, fn, *args], capture_output=True)
        return r.returncode

    def chown(self, path, uid, gid):
        os.chown(path, uid, gid, follow_symlinks=False)

    def node_signed(self, path, team):
        req = f'anchor apple generic and certificate leaf[subject.OU] = "{team}"'
        r = subprocess.run(["/usr/bin/codesign", "--verify", "--strict", f"-R={req}", str(path)], capture_output=True)
        return r.returncode == 0

    def listeners(self, port_no):
        """端口上 LISTEN 的进程 uid（root 看得到所有账号的）。"""
        return hub_listener.listener_uids(port_no, timeout=60)

    def sleep(self, s):
        time.sleep(s)

    def boot_sec(self):
        """本次开机时间（kern.boottime 的秒），读不到返回 None。"""
        try:
            out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True,
                                 timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return None
        m = re.search(r"\bsec\s*=\s*(\d+)", out)
        return m.group(1) if m else None

    def read_secret(self, prompt):
        """从终端读一行（不回显、不进命令行和 shell 历史）；没有终端时 getpass 退回读标准输入。"""
        import getpass
        return getpass.getpass(prompt)

    def pf_apply(self, script):
        """跑 root 副本的门卫脚本 apply（0E-1 dt_worker_pf.sh）；返回退出码，结果以状态文件为准。"""
        try:
            return subprocess.run(["/bin/bash", str(script), "apply"], capture_output=True, timeout=120).returncode
        except (OSError, subprocess.SubprocessError):
            return 127


# ---------- 小工具 ----------
def write_root_file(path, data, mode=0o644):
    """在只有 root 能改的目录里原子写一个文件（临时文件 O_EXCL、不跟随链接，再改名）。"""
    path = Path(path)
    tmp = path.parent / f".{path.name}.tmp-{secrets.token_hex(6)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_json(path, obj, mode=0o644):
    write_root_file(path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode(), mode)


def read_json(path):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def make_dir(path, mode):
    os.mkdir(path, mode)
    os.chmod(path, mode)                   # mkdir 受 umask 影响（独立审查 P2-9）


def root_dir(host, path, mode=0o755, make=True):
    """建（或核对）一个只有 root 能改的目录：上一级必须从 / 起只有 root 能改。"""
    path = Path(path)
    if host.guard("root_path", str(path.parent)) != 0:
        raise Stop(f"{path.parent} 不是从 / 起只有 root 能改的目录，请人工查看")
    if not path.exists() and not path.is_symlink():
        if not make:
            raise Stop(f"还没有 {path}")
        make_dir(path, mode)
    if host.guard("root_dir", str(path)) != 0:
        raise Stop(f"{path} 不是只有 root 能改的目录，请人工查看")
    return path


def shared_dir(host):
    """/Users/Shared/dt-gate：/Users/Shared 谁都能往里建东西，靠粘滞位挡住别人删换 root 建的目录；
    别人抢先建了同名目录（不属 root）就停下。"""
    d = shared()
    if host.guard("sticky_dir", str(d.parent)) != 0:
        raise Stop(f"{d.parent} 不是属 root、带粘滞位的目录，请人工查看")
    if not d.exists() and not d.is_symlink():
        make_dir(d, 0o755)
    if host.guard("root_dir", str(d)) != 0:
        raise Stop(f"{d} 不是只有 root 能改的目录（可能被别人抢先建了），请人工查看")
    return d


def helper(current):
    return [PY, *PY_FLAGS, str(Path(current["repo"]["dir"]) / "ops/dt_gate_user.py")]


def run_helper(host, current, name, args, stdin=None, env=None):
    code, out, err = host.as_user(name, [*helper(current), *args], stdin=stdin, env=env)
    try:
        lines = [ln for ln in out.decode("utf-8").splitlines() if ln.strip()]
        res = json.loads(lines[-1]) if lines else {}
    except ValueError:
        res = {}
    if code != 0 or "error" in res:
        raise Stop(f"以 {name} 身份执行 {args[0]} 失败：{res.get('error') or err.strip()[:300]}")
    return res


def cli_env(current, home):
    return {"AGENT_AUTOLOOP_HOME": str(home), "AGENT_AUTOLOOP_COMMIT": current["engine"]["commit"]}


def run_cli(host, current, name, home, args, timeout=120):
    code, out, err = host.as_user(name, [current["cli"], *args], env=cli_env(current, home), timeout=timeout)
    if code != 0:
        raise Stop(f"以 {name} 身份执行 agent-autoloop {args[0]} 失败：{(err or out.decode('utf-8', 'replace')).strip()[:300]}")
    return out.decode("utf-8", "replace")


def load_current(name="current.json"):
    f = gate_root() / name
    if not f.exists():
        raise Stop("还没有 stage：先跑 dt_gate_hub.py stage")
    return read_json(f)


def load_state():
    f = gate_root() / "state.json"
    return read_json(f) if f.exists() else None


def settle_progress():
    """state.json 写完、还没来得及归档进度就被杀：两个都在时认作已搬完，把进度记录归档（复核 P2-b）。"""
    half = gate_root() / "migrating.json"
    if (gate_root() / "state.json").exists() and half.exists():
        stamp = read_json(half).get("stamp", now_stamp())
        os.replace(half, gate_root() / f"migrating.done-{stamp}.json")


def gate_home(host):
    return Path(host.account(gate_account())["home"])


def gate_paths(host):
    h = gate_home(host)
    return {"home": h, "hub": h / ".agent-autoloop", "support": h / "Library/Application Support/DigitalTwin"}


# ---------- stage：root 所有的运行副本 ----------
def resolve_commit(host, owner, repo, commit):
    """只认完整的 40 位提交号（分支名、origin/main 这类引用主账号本地能改）；对象本身在 extract_archive 里由 root 核。"""
    if not SHA.match(commit or ""):
        raise Stop(f"提交要写完整的 40 位提交号（和 GitHub 上的合并提交对照），不认 {commit!r}")
    return commit


def _safe_name(name):
    n = name.rstrip("/")
    if not n or n.startswith("/") or posixpath.normpath(n) != n or n.split("/")[0] == "..":
        raise Stop(f"提交里有不安全的路径：{name!r}")
    return n


def obj_sha(kind, data):
    return hashlib.sha1(b"%s %d\0" % (kind.encode(), len(data)) + data).hexdigest()


def blob_sha(data):
    return obj_sha("blob", data)


def parse_batch(out, objs, kind="blob"):
    """git cat-file --batch 的输出：按顺序拆出每个对象的原始内容，root 自己按类型重算哈希，和对象号对不上就停。"""
    got, i = {}, 0
    for obj in objs:
        nl = out.index(b"\n", i)
        head = out[i:nl].decode().split(" ")
        if len(head) != 3 or head[0] != obj or head[1] != kind:
            raise Stop(f"取对象 {obj[:12]} 时回的不对：{head}")
        size = int(head[2])
        data = out[nl + 1:nl + 1 + size]
        if len(data) != size or out[nl + 1 + size:nl + 2 + size] != b"\n" or obj_sha(kind, data) != obj:
            raise Stop(f"对象 {obj[:12]} 的内容和对象号对不上")
        got[obj] = data
        i = nl + 2 + size
    if out[i:]:
        raise Stop("取对象时多出了内容")
    return got


def cat_objects(host, owner, repo, objs, kind):
    """以主账号身份 git cat-file --batch 取原始对象（不经任何过滤），root 逐个重算哈希。"""
    objs = list(dict.fromkeys(objs))
    if not objs:
        return {}
    code, out, err = host.as_user(owner, [*GIT, "-C", str(repo), "cat-file", "--batch"],
                                  stdin=("\n".join(objs) + "\n").encode(), env=GIT_ENV, timeout=300)
    if code != 0:
        raise Stop(f"git cat-file 失败：{err.strip()[:200]}")
    return parse_batch(out, objs, kind)


def parse_tree(data):
    """原始树对象：[(模式, 名字, 对象号)]。"""
    out, i = [], 0
    while i < len(data):
        sp = data.index(b" ", i)
        nul = data.index(b"\0", sp)
        mode, name = data[i:sp].decode(), data[sp + 1:nul].decode("utf-8")
        if not name or "/" in name or name in (".", ".."):
            raise Stop(f"树里有不安全的名字：{name!r}")
        out.append((mode, name, data[nul + 1:nul + 21].hex()))
        i = nul + 21
    return out


def tree_blobs(host, owner, repo, sha):
    """提交里的全部文件：路径 → (模式, 对象号)。提交、每一层树都由 root 取原始对象、重算哈希、自己解析，
    不信主账号 git 的 ls-tree 输出（独立审查复核 P2-a：改了松散对象，同一个提交号能指到另一棵树）。"""
    commit = cat_objects(host, owner, repo, [sha], "commit")[sha]
    first = commit.split(b"\n", 1)[0].decode()
    if not re.match(r"^tree [0-9a-f]{40}$", first):
        raise Stop(f"提交 {sha[:12]} 的格式不对")
    blobs, level = {}, [("", first[5:])]
    while level:
        trees = cat_objects(host, owner, repo, [t for _, t in level], "tree")
        nxt = []
        for prefix, t in level:
            for mode, name, obj in parse_tree(trees[t]):
                path = f"{prefix}{name}"
                if mode == "40000":
                    nxt.append((path + "/", obj))
                else:
                    blobs[path] = (mode, obj)
        level = nxt
    return blobs


def extract_archive(host, owner, repo, sha, dest, keep):
    """把提交里要的文件取成新的 root 目录：路径和对象号来自 root 自己核过的提交、树（tree_blobs），内容用 git cat-file
    --batch 取原始对象（不经行尾转换、export-subst 等过滤），root 重算哈希；只收普通文件，链接、子模块一律停下。"""
    if dest.exists():
        return dest                       # 按提交号命名、写完才改名：在的就是完整的
    items = sorted((_safe_name(p), mode, obj) for p, (mode, obj) in tree_blobs(host, owner, repo, sha).items() if keep(p))
    if not items:
        raise Stop(f"{repo} 的 {sha[:12]} 里没有要的文件")
    bad = [p for p, mode, _ in items if mode not in ("100644", "100755")]
    if bad:
        raise Stop(f"提交里有链接或子模块：{bad[:5]}")
    blobs = cat_objects(host, owner, repo, [obj for _, _, obj in items], "blob")
    tmp = dest.parent / f".{dest.name}.tmp-{secrets.token_hex(6)}"
    make_dir(tmp, 0o755)
    try:
        for name, mode, obj in items:
            target = tmp / name
            target.parent.mkdir(parents=True, exist_ok=True)
            m = 0o755 if mode == "100755" else 0o644
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, m)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blobs[obj])
            os.chmod(target, m)
        for d, _, _ in os.walk(tmp):
            os.chmod(d, 0o755)
        os.rename(tmp, dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest


def repo_keep(name):
    return name in REPO_FILES or (name.startswith("hermes/scripts/") and not name.startswith("hermes/scripts/tests/"))


def stage_node(host, owner, gate, src, root):
    code, data, err = host.as_user(owner, ["/bin/cat", str(src)], timeout=300)
    if code != 0 or len(data) < 1_000_000:
        raise Stop(f"读不到 node 程序 {src}：{err.strip()[:200]}")
    digest = hashlib.sha256(data).hexdigest()
    dest = root / "runtime" / f"node-{digest[:16]}"
    team = os.environ.get("DT_GATE_NODE_TEAM") or "HX7739G8FX"
    if not dest.exists():
        tmp = dest.parent / f".{dest.name}.tmp-{secrets.token_hex(6)}"
        make_dir(tmp, 0o755)
        try:
            write_root_file(tmp / "node", data, 0o755)
            if not host.node_signed(tmp / "node", team):     # 核的是复制好的这一份，不是主账号那份
                raise Stop(f"{src} 不是 Node.js 官方签名（团队号 {team}）的程序")
            os.rename(tmp, dest)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    node = dest / "node"
    if not host.node_signed(node, team):
        raise Stop(f"{node} 的签名核不过")
    code, out, err = host.as_user(gate, [str(node), "--version"])
    m = re.match(r"^v(\d+)\.", out.decode().strip())
    if code != 0 or not m or int(m.group(1)) < 24:
        raise Stop(f"node 版本不够（要 24 以上）：{out.decode().strip() or err.strip()[:100]}")
    return node, out.decode().strip()


def stage_gh(host, owner, gate, src, root):
    """gh 副本（0E-4）：以主账号身份读出 gh 程序，root 原子写成 GATE/bin/gh（755），以 dt-gate 身份跑 --version 核对。
    Homebrew 的 gh 没有官方签名可核：信任「stage 那一刻主账号可信」（gate_github 头注释的剩余风险）。"""
    code, data, err = host.as_user(owner, ["/bin/cat", str(src)], timeout=300)
    if code != 0 or len(data) < GH_MIN_BYTES:
        raise Stop(f"读不到 gh 程序 {src}（或太小，不像 gh）：{err.strip()[:200]}")
    dest = root / "bin/gh"
    write_root_file(dest, data, 0o755)
    code, out, err = host.as_user(gate, [str(dest), "--version"])
    m = re.match(r"^gh version (\d+\.\d+\.\d+)", out.decode("utf-8", "replace").strip())
    if code != 0 or not m:
        os.unlink(dest)
        raise Stop(f"{src} 跑不出 gh 的版本号，没装：{(err or out.decode('utf-8', 'replace')).strip()[:200]}")
    return {"path": str(dest), "sha256": hashlib.sha256(data).hexdigest(), "version": m.group(1)}


def kept_gh(root, prev):
    """stage 没带 --gh：已有的 gh 副本还在、内容和上次记的一样就沿用记录，否则不记（status 会说没有）。"""
    gh = (prev or {}).get("gh")
    dest = root / "bin/gh"
    if not gh or not dest.is_file() or dest.is_symlink():
        return None
    with open(dest, "rb") as fh:
        return gh if hashlib.sha256(fh.read()).hexdigest() == gh.get("sha256") else None


def _quoted(p):
    if "'" in str(p):
        raise Stop(f"路径里有单引号：{p}")
    return f"'{p}'"


def write_cli(root, node, engine_dir, sha):
    cli = root / "bin" / f"agent-autoloop-{sha[:12]}"
    body = (f"#!/bin/sh\n# root 装的中台命令行（{sha}）；dt-gate 的服务和 dt_gate_hub.py 用它\n"
            f"exec {_quoted(node)} --disable-warning=ExperimentalWarning {_quoted(str(engine_dir) + '/bin/agent-autoloop.ts')} \"$@\"\n")
    write_root_file(cli, body.encode(), 0o755)
    return cli


def write_entry(rdir):
    """root 所有的入口：以后的 sudo 命令都跑副本里的 dt_gate_hub.py，不跑主账号仓库里那份（独立审查 P1-1）。"""
    body = (f"#!/bin/sh\n# root 装的搬家脚本入口（数字分身 ADR-015 0E-2）\n"
            f"exec {PY} {' '.join(PY_FLAGS)} {_quoted(str(rdir) + '/ops/dt_gate_hub.py')} \"$@\"\n")
    write_root_file(entry(), body.encode(), 0o755)


def cmd_stage(host, a):
    owner = a.owner_account
    root = root_dir(host, gate_root())
    for sub in ("engine", "repo", "runtime", "bin"):
        root_dir(host, root / sub)
    esha = resolve_commit(host, owner, a.engine_repo, a.engine_commit)
    rsha = resolve_commit(host, owner, a.repo, a.repo_commit)
    edir = extract_archive(host, owner, a.engine_repo, esha, root / "engine" / esha, lambda n: True)
    if not (edir / "bin/agent-autoloop.ts").is_file():
        raise Stop(f"{a.engine_repo} 的 {esha[:12]} 不像 agent-autoloop（没有 bin/agent-autoloop.ts）")
    rdir = extract_archive(host, owner, a.repo, rsha, root / "repo" / rsha, repo_keep)
    for f in ("ops/dt_gate_user.py", "ops/dt_gate_hub.py", "hermes/scripts/gate_intake.py",
              "hermes/scripts/hub_listener.py", "projects.json"):
        if not (rdir / f).is_file():
            raise Stop(f"{a.repo} 的 {rsha[:12]} 里没有 {f}（要用合并了 0E-2 的提交）")
    node, version = stage_node(host, owner, gate_account(), a.node, root)
    cli = write_cli(root, node, edir, esha)
    current = {"node": str(node), "node_version": version, "engine": {"commit": esha, "dir": str(edir)},
               "repo": {"commit": rsha, "dir": str(rdir)}, "cli": str(cli), "staged_at": now_stamp()}
    old = root / "current.json"
    prev = read_json(old) if old.exists() else None
    gh = stage_gh(host, owner, gate_account(), a.gh, root) if getattr(a, "gh", None) else kept_gh(root, prev)
    if gh:
        current["gh"] = gh
    if prev and (prev["engine"]["commit"], prev["repo"]["commit"], prev["node"]) != (esha, rsha, str(node)):
        write_json(root / "previous.json", prev)
    write_json(old, current)
    write_entry(rdir)
    say(f"副本好了：引擎 {esha[:12]}、本仓库 {rsha[:12]}、node {version}"
        + (f"、gh {gh['version']}（SHA-256 {gh['sha256'][:12]}）" if gh else "（没有 gh 副本）"))
    say(f"  在 {root}（属 root，dt-gate 只能读和运行）；以后的命令用：sudo '{entry()}' <命令>")
    return 0


# ---------- 核对 ----------
def owner_hub(host, owner):
    return Path(host.account(owner)["home"]) / ".agent-autoloop"


def owner_agent_plist(host, owner):
    return Path(host.account(owner)["home"]) / "Library/LaunchAgents" / f"{OWNER_AGENT}.plist"


def owner_evidence(host, owner, stamp):
    return Path(host.account(owner)["home"]) / "Library/Application Support/DigitalTwin/evidence/gate-migrate" / stamp


def exists_as(host, name, path):
    code, _, _ = host.as_user(name, ["/bin/test", "-e", str(path)])
    return code == 0


def snapshot_as(host, current, name, home):
    return run_helper(host, current, name, ["snapshot", str(home)])


# ---------- 0D 恢复门：停写 → 排空（docs/恢复门-0D-设计.md 第 7 节） ----------
# 控制面的账本（出口账本、凭证库、卡片库、秘书账本）不搬：dt-gate 里从空的新库开始，恢复闸在那边「还没对过账」、关着，
# 锁定版本并对账后才开。新库里没有主账号账本里的结果不明、核不清、还在送、排着队的东西，搬过去就等于把它们忘了，所以
# 停中台之前要主账号这边一样都没有：先停写（hold），等在途的写做完（writes），再看一遍剩下的（leftovers，只读、
# 不依赖出口账本在不在、不新建任何库）。主账号的账本原样留在原处，搬家不删不动。搬完主账号保持停写（主账号不再做受保护的写，
# ADR-015 第 3.4 节）；退回或回滚时用同一个恢复点结束维护（release rolled_back）。
def recovery_as(host, current, name, args):
    """以账号身份跑副本里的 0D 恢复闸命令行（hermes/scripts/autoloop_recovery.py），返回它打印的 JSON。
    拒绝（ok: false）、读不懂、跑不起来、超时一律停下。"""
    argv = [PY, *PY_FLAGS, str(Path(current["repo"]["dir"]) / "hermes/scripts/autoloop_recovery.py"), *args]
    try:
        code, out, err = host.as_user(name, argv, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise Stop(f"以 {name} 身份执行恢复闸 {args[0]} 跑不起来或超时（{type(e).__name__}）")
    try:
        res = json.loads(out.decode("utf-8"))
    except ValueError:
        res = None
    if not isinstance(res, dict) or code != 0 or res.get("ok") is False:
        why = res.get("why") if isinstance(res, dict) else (err or out.decode("utf-8", "replace")).strip()[:300]
        raise Stop(f"以 {name} 身份执行恢复闸 {args[0]} 失败：{why}")
    return res


def ledger_checks(host, current, owner, check):
    """主账号控制面能不能搬：不在维护停写、各账本里没有结果不明 / 核不清 / 还在送 / 排着队的东西、没有关闸标记。只看。"""
    left = recovery_as(host, current, owner, ["leftovers"])
    check(not left.get("hold"), f"主账号控制面不在维护停写中（现在：{left.get('hold') or '没有'}；搬家留下的恢复点按 rollback "
                                "输出的命令结束，别的维护等它结束）")
    items = left.get("items")
    check(isinstance(items, list) and not items,
          f"主账号控制面账本里没有没结清的东西（现在：{items if items else '无'}；dt-gate 里是新账本，搬过去会把它们忘掉，"
          "先在主账号对账或交常浩结算）")


def hold_and_drain(host, current, owner, point, snapshot, max_card):
    """停写（hold 登记恢复点），等在途的写做完，再确认各账本没有剩下的；有剩的就停下（调用方退回、结束这次维护）。"""
    # hold 这一下期间屏蔽停止信号：不然停在 sudo 上、下面的 python 照样把 hold 写进账本，退回时已经核过「不是这次的」，
    # 留下一个没记录的停写（独立审查第 2 轮 P2-3）。做完再放开，挂着的信号这时才抛出，退回能看到这次的恢复点。
    old = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
    try:
        recovery_as(host, current, owner, ["hold", "--point", point, "--snapshot", str(snapshot),
                                           "--commit", current["engine"]["commit"], "--max-card", str(int(max_card)),
                                           "--confirm"])
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
    tries = max(1, DRAIN_SECONDS // 2)
    left = None
    for i in range(tries + 1):                           # 最后一次睡完再复查一遍
        left = recovery_as(host, current, owner, ["writes"]).get("in_flight")
        if not left:
            break
        if i < tries:
            host.sleep(2)
    else:
        raise Stop(f"停写以后 {DRAIN_SECONDS} 秒还有在途的写：{left}")
    after = recovery_as(host, current, owner, ["leftovers"])
    if after.get("hold") != point:
        raise Stop(f"停写以后闸上的恢复点不是这次的（{after.get('hold')}）")
    if after.get("items"):
        raise Stop(f"排空以后主账号账本里还有没结清的东西：{after['items']}")


def gate_cards_clean(host, current, max_card):
    """回滚前补主账号恢复闸看不到的那一半（0D 会话 11-22 指出）：dt-gate 开闸后发过的卡片记在 dt-gate 自己的出口账本和
    卡片库里，主账号的回滚核对（mismatched_after）查不到；中台退回旧快照会复用这些卡片号，和飞书上已有的消息撞号。
    以 dt-gate 身份只读查卡片号大于搬家时最大号的；查到、或查不了，都不结束主账号停写。"""
    if max_card is None:
        say("  搬家记录里没有快照时的最大卡片号，核不了 dt-gate 发过的卡片")
        return False
    try:
        found = recovery_as(host, current, gate_account(), ["cards-after", "--max-card", str(int(max_card))]).get("cards")
    except Stop as e:
        say(f"  查 dt-gate 发过的卡片没查成：{e}")
        return False
    if not isinstance(found, list) or found:
        say(f"  dt-gate 搬家后发过卡片（{found}），卡片号大于搬家时的 {max_card}")
        return False
    return True


def release_hold(host, current, owner, point):
    """结束搬家登记的维护停写（退回、回滚用）。闸上不是这个恢复点（hold 没做成、已经结束）就什么都不做。
    回滚核对不过（快照之后发过卡片）恢复闸会拒绝，保持停写、说清原因和手工命令，交常浩。返回是否结束干净。"""
    try:
        if recovery_as(host, current, owner, ["leftovers"]).get("hold") != point:
            return True
        recovery_as(host, current, owner, ["release", "--point", point, "--outcome", "rolled_back",
                                           "--note", "数字分身 0E-2 搬家退回", "--confirm"])
        return True
    except Stop as e:
        say(f"  主账号控制面还在停写（恢复点 {point}）：{e}；看清原因后在主账号跑 autoloop_recovery.py release "
            f"--point {point} --outcome rolled_back --confirm（核对不过而确认卡片号不会重复的，交常浩定）")
        return False


def checks(host, a, migrating=True):
    """返回 [(是否通过, 说明)]；查不清的一律算不通过。"""
    out = []

    def check(ok, msg):
        out.append((bool(ok), msg))
        return ok

    check(host.is_root(), "以 root 运行（sudo）")
    try:
        current = load_current()
        check(all(Path(current[k]).exists() for k in ("node", "cli")) and Path(current["repo"]["dir"]).is_dir(),
              f"运行副本在（引擎 {current['engine']['commit'][:12]}、本仓库 {current['repo']['commit'][:12]}）")
    except (Stop, KeyError, ValueError) as e:
        check(False, f"运行副本：{e}")
        current = None
    gate = gate_account()
    try:
        g = host.account(gate)
        check(host.guard("uid_ok", gate) == 0, f"{gate} 的 uid 大于 0、只对应这一个账号")
        check(host.guard("is_member", gate, "admin") == 1, f"{gate} 不是管理员")
        want = f"{os.environ.get('DT_GATE_USERS') or '/Users'}/{gate}"
        check(g["home"] == want, f"{gate} 的家目录是 {want}")
    except KeyError:
        check(False, f"没有 {gate} 账号")
    try:
        host.account(worker_account())
        check(host.guard("uid_ok", worker_account()) == 0, f"{worker_account()} 的 uid 核对")
    except KeyError:
        check(False, f"没有 {worker_account()} 账号")
    try:
        host.account(a.owner_account)
        check(True, f"主账号 {a.owner_account}")
    except KeyError:
        check(False, f"没有主账号 {a.owner_account}（用 --owner-account 指定）")
        return out
    check(host.guard("root_path", str(gate_root())) == 0, f"{gate_root()} 从 / 起只有 root 能改")
    check(host.guard("root_path", str(daemons())) == 0, f"{daemons()} 从 / 起只有 root 能改")
    check(host.guard("root_path", str(logs().parent.parent)) == 0, f"{logs().parent.parent} 从 / 起只有 root 能改")
    check(host.guard("sticky_dir", str(shared().parent)) == 0, f"{shared().parent} 属 root、带粘滞位")
    if not migrating or current is None:
        return out
    settle_progress()
    check(not (gate_root() / "migrating.json").exists(), "没有搬到一半的记录（有的话先跑 rollback --confirm 收拾）")
    check(load_state() is None, "还没搬过（gate/state.json 不存在）")
    check(not exists_as(host, gate, gate_paths(host)["hub"]), f"{gate} 里还没有中台目录")
    check(not os.path.lexists(hub_mark()), f"没有上次留下的门卫标记 {hub_mark()}（有就是上次没收干净，先 rollback --confirm）")
    prob = pf_guard_problem(host, "no")
    if prob is None and "guard=" not in (pf_dir() / "pf-dt-worker.status").read_text(encoding="utf-8"):
        prob = "门卫是旧版（状态里没有 guard=），先装 #313 的新版"
    check(prob is None, f"门卫规则在生效、监听者规则还关着（{prob or 'state=ok guard=no，本次开机写的'}；"
                        "不然要到第 4b 步才停下，退回时也关不掉门卫）")
    hub = owner_hub(host, a.owner_account)
    check(exists_as(host, a.owner_account, owner_agent_plist(host, a.owner_account)),
          f"主账号的中台 LaunchAgent（{OWNER_AGENT}.plist）在")
    try:
        s = snapshot_as(host, current, a.owner_account, hub)
        check(s["live_runs"] == 0, f"主账号中台没有在跑的轮次（现在 {s['live_runs']}）")
        check(s["live_cards"] == 0, f"主账号中台没有没答完的卡片（现在 {s['live_cards']}）")
        unpaused = sorted(set(s["dispatch_rules"]) - set(s["pauses"]))
        check(not unpaused, f"派单规则全部暂停（没暂停的：{unpaused or '无'}）")
        check(not s["secrets_env"], f"secrets.env 里没有验签密钥（主账号见过的搬过去照样有效，先换掉；"
                                    f"现在有：{s['secrets_env'] or '无'}）")
        check(urllib.parse.urlsplit(s.get("url") or "").hostname == "127.0.0.1"
              and str(s.get("listen") or "").startswith("127.0.0.1:"),
              f"中台地址是 127.0.0.1（现在 {s.get('url')}，监听 {s.get('listen')}；监听核对只认这一个地址）")
        check(True, f"中台里现在的钥匙：{', '.join(s['keys'])}（搬过去以后只留新本人密钥和 {worker_account()}）")
    except (Stop, KeyError) as e:
        check(False, f"读主账号中台：{e}")
    try:
        ledger_checks(host, current, a.owner_account, check)
    except Stop as e:
        check(False, f"读主账号 0D 恢复闸：{e}")
    launcher_checks(current, check)
    return out


def launcher_checks(current, check):
    """工人侧启动脚本要换成这份副本里的版本（读 /Users/Shared/dt-gate/ 的凭证镜像和任务单）：搬家后 dt-gate 写的是新位置，
    旧版读的还是主账号那份。装着的每一个都要和副本里的 ops/dt_worktree.py 逐字节一样；没装的不拦（工人开不了工，不会出错）。"""
    want = Path(current["repo"]["dir"]) / "ops/dt_worktree.py"
    for name in ("dt-worktree-claude", "dt-worktree-codex"):
        f = launchers() / name
        if not os.path.lexists(f):
            check(True, f"工人启动脚本 {f} 没装（装的时候用副本里的 ops/dt_worktree.py）")
            continue
        try:
            same = f.is_file() and not f.is_symlink() and f.read_bytes() == want.read_bytes()
        except OSError:
            same = False
        check(same, f"工人启动脚本 {f} 是这份副本里的版本（读 {shared()}；不是就先换：命令清单「开始前」）")


def print_checks(results):
    for ok, msg in results:
        say(f"  [{'通过' if ok else '不过'}] {msg}")
    return all(ok for ok, _ in results)


def cmd_preflight(host, a):
    say("搬家前核对：")
    return 0 if print_checks(checks(host, a)) else 1


# ---------- launchd ----------
def gate_env(host, current, hub_url):
    p = gate_paths(host)
    return {"HOME": str(p["home"]), "PATH": SAFE_PATH, "USER": gate_account(), "LOGNAME": gate_account(),
            "LC_ALL": "en_US.UTF-8", **cli_env(current, p["hub"]), "DT_AUTOLOOP_CLI": current["cli"],
            "DT_ROSTER": str(Path(current["repo"]["dir"]) / "projects.json"), "DT_HUB": hub_url,
            "DT_HUB_LISTENER_UID": str(host.account(gate_account())["uid"]),
            "DT_GATE_SHARED": str(shared()), "DT_GRANTS_STATUS": str(shared() / "grants/status.json"),
            "DT_GRANTS_TASKS": str(shared() / "tasks")}


def program(current, which):
    scripts = Path(current["repo"]["dir"]) / "hermes/scripts"
    if which == "hub":
        return [current["cli"], "up"]
    if which == "readonly":
        return [PY, *PY_FLAGS, str(scripts / "autoloop_readonly.py"), "serve", "--port",
                str(port("DT_GATE_READONLY_PORT", 3352))]
    return [PY, *PY_FLAGS, str(scripts / "gate_intake.py"), "serve", "--port", str(port("DT_GATE_INTAKE_PORT", 3353))]


def plist_for(host, current, which, hub_url):
    p = gate_paths(host)
    log = logs() / f"{which}.log"
    return {"Label": LABEL[which], "UserName": gate_account(), "ProgramArguments": program(current, which),
            "EnvironmentVariables": gate_env(host, current, hub_url), "WorkingDirectory": str(p["home"]),
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30, "Umask": 0o077, "ProcessType": "Background",
            "StandardOutPath": str(log), "StandardErrorPath": str(log)}


def prepare_log(host, which):
    """日志文件由 root 在只有 root 能改的目录里先建好、交给 dt-gate（600：主账号和 dt-gate 同在 staff 组，不能给组读）。"""
    root_dir(host, logs().parent)
    d = root_dir(host, logs())
    f = d / f"{which}.log"
    if f.is_symlink() or (f.exists() and not f.is_file()):
        raise Stop(f"{f} 不是普通文件")
    if not f.exists():
        os.close(os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
    g = host.account(gate_account())
    host.chown(f, g["uid"], g["gid"])
    os.chmod(f, 0o600)


LAUNCHCTL_NOT_FOUND = 113                  # launchctl print：找不到这个服务


def unload(host, domain_label):
    """卸掉服务，等 launchctl print 明确说「找不到」（113）才算卸了；还在、或别的错误（查不清）都算没卸掉。"""
    host.launchctl("bootout", domain_label)
    for _ in range(40):                    # bootout 是异步的，等真卸完（全局坑 G-18）
        code, _ = host.launchctl("print", domain_label)
        if code == LAUNCHCTL_NOT_FOUND:
            return True
        host.sleep(0.5)
    return False


def load(host, plist_path, domain="system"):
    last = ""
    for _ in range(5):
        code, last = host.launchctl("bootstrap", domain, str(plist_path))
        if code == 0:
            return
        host.sleep(3)
    raise Stop(f"装不上 {plist_path}：{last[:200]}")


def install_daemon(host, current, which, hub_url):
    root_dir(host, daemons(), make=False)
    prepare_log(host, which)
    f = daemons() / f"{LABEL[which]}.plist"
    if not unload(host, f"system/{LABEL[which]}"):
        raise Stop(f"{LABEL[which]} 卸不下来")
    write_root_file(f, plistlib.dumps(plist_for(host, current, which, hub_url)), 0o644)
    load(host, f)
    return f


def remove_daemon(host, which):
    """卸掉服务再删 plist。卸不掉（launchctl 一直说还在）就停下、plist 留着：服务是 KeepAlive 的，plist 删了它照样跑，
    也就没人知道它还在（L3 第 2 轮 P1-01）。"""
    if not unload(host, f"system/{LABEL[which]}"):
        raise Stop(f"{LABEL[which]} 卸不下来（launchctl 一直显示还在），plist 留着；看清原因后再来")
    f = daemons() / f"{LABEL[which]}.plist"
    try:
        st = os.lstat(f)
    except FileNotFoundError:
        return
    except OSError as e:
        raise Stop(f"核不了 {f}（{type(e).__name__}）")
    if not stat.S_ISREG(st.st_mode):                     # 链接、目录：不删也不跳过（L3 第 3 轮 P2-03）
        raise Stop(f"{f} 不是普通文件（链接或目录），没删；请人工查看")
    f.unlink()
    if os.path.lexists(f):
        raise Stop(f"{f} 删了以后还在")


def installed(which):
    return os.path.lexists(daemons() / f"{LABEL[which]}.plist")   # 失效的链接也算还在


def http_get(url, timeout=3):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


def listened_by(host, port_no, uid):
    """端口上有人在听、而且全是这个 uid 的进程（独立审查 P1-2、P2-6：抢先绑上端口的冒充者过不了）。"""
    try:
        uids = host.listeners(port_no)
    except OSError:
        return False
    return bool(uids) and all(u == uid for u in uids)


def wait_health(host, url, commit, uid, seconds=60):
    last = ""
    p = urllib.parse.urlsplit(url).port
    for _ in range(int(seconds * 2)):
        try:
            if not listened_by(host, p, uid):
                last = f"{p} 端口上在听的不是 uid {uid}"
            else:
                code, body = http_get(url.rstrip("/") + "/healthz")
                h = json.loads(body or b"{}")
                if code == 200 and h.get("ok") and h.get("commit") == commit:
                    return
                last = f"HTTP {code} {h}"
        except (OSError, ValueError) as e:
            last = type(e).__name__
        host.sleep(0.5)
    raise Stop(f"{url} 没有起来：{last}")


def wait_answering(host, port_no, uid, seconds=30):
    last = ""
    for _ in range(int(seconds * 2)):
        if listened_by(host, port_no, uid):
            try:
                http_get(f"http://127.0.0.1:{port_no}/", timeout=2)
                return
            except OSError as e:
                last = type(e).__name__
        else:
            last = f"端口上在听的不是 uid {uid}"
        host.sleep(0.5)
    raise Stop(f"127.0.0.1:{port_no} 没有起来：{last}")


def wait_port_free(host, hostport, seconds=30, what="主账号中台"):
    """只有「连接被拒」才算没人在听；连上了、超时、别的错误都算还没空（超时可能是监听队列满了，L3 第 2 轮 P1-01 补测时发现）。"""
    h, _, p = hostport.rpartition(":")
    h = h.strip("[]") or "127.0.0.1"
    for _ in range(int(seconds * 2)):
        with socket.socket(socket.AF_INET6 if ":" in h else socket.AF_INET) as s:
            s.settimeout(1)
            if s.connect_ex((h, int(p))) == errno.ECONNREFUSED:
                return
        host.sleep(0.5)
    raise Stop(f"{what}停了以后 {hostport} 还有人在听（或核不清）")


def wait_no_listener(host, port_no, seconds=30, allow=()):
    """回滚关门卫前用：以 root 用 lsof 看这个端口上有没有任何账号在听（门卫开着时，别的账号在听和没人在听连过去都是
    「被拒」，连接分不清，L3 第 2 轮返修审查）。lsof 查不了算没空。allow 里的 uid 在听不算（退回再跑时主账号中台
    可能已经起来了）。"""
    last = ""
    for _ in range(int(seconds * 2)):
        try:
            uids = host.listeners(port_no)
            if all(u in allow for u in uids):
                return
            last = f"uid {sorted(set(uids))} 在听"
        except (OSError, subprocess.SubprocessError) as e:
            last = f"查不清（{type(e).__name__}）"
        host.sleep(0.5)
    raise Stop(f"dt-gate 的服务停了以后 {port_no} 端口还有人在听：{last}")


def hub_port_of(url):
    try:
        p = urllib.parse.urlsplit(url).port
    except ValueError:
        p = "超出范围"
    if p is None:
        raise Stop(f"中台地址 {url} 没写端口")
    return check_port(p, f"中台地址 {url} 的端口")


def wait_only_listener(host, port_no, uid, seconds=60):
    """以 root 用 lsof 核：这个端口上有人在听，而且全是 uid 的进程。查不清不算。"""
    last = ""
    for _ in range(int(seconds * 2)):
        try:
            uids = host.listeners(port_no)
            if uids and all(u == uid for u in uids):
                return
            last = f"在听的是 uid {sorted(set(uids))}" if uids else "还没人在听"
        except (OSError, subprocess.SubprocessError) as e:
            last = f"查不清（{type(e).__name__}）"
        host.sleep(0.5)
    raise Stop(f"{port_no} 端口上不是只有主账号中台（uid {uid}）在听：{last}")


def owner_health(host, url, seconds=30):
    last = ""
    for _ in range(int(seconds * 2)):
        try:
            code, body = http_get(url.rstrip("/") + "/healthz")
            if code == 200 and json.loads(body or b"{}").get("ok"):
                return
            last = f"HTTP {code}"
        except (OSError, ValueError) as e:
            last = type(e).__name__
        host.sleep(0.5)
    raise Stop(f"主账号中台健康检查没过：{last}")


def owner_takeover(host, owner_account, hub_url, guard=True):
    """回滚 / 退回的最后一段（L3 第 3 轮 P1-03）：门卫还开着时先启用主账号中台，让它先占住中台端口；以 root 的 lsof
    核端口上只有主账号在听，才关门卫；关了再核一次，再做健康检查。门卫开着时别的账号就算抢先绑了端口也收不到连接，
    核出来就停。任何一步不过都 Stop：门卫保持开着（或已关但核不过）、停写不结束、state / 进度记录留着，再跑一次接着来。
    主账号自己的进程抢先绑上端口分辨不出来：回滚后本来就回到主账号运行中台的信任边界（搬家前也一样）。"""
    if not hub_url:
        raise Stop("没有中台地址，核不了端口上是谁在听；主账号中台先不启用")
    uid = host.account(owner_account)["uid"]
    p = hub_port_of(hub_url)
    start_owner_agent(host, owner_account)
    wait_only_listener(host, p, uid)
    if guard and not guard_off(host):
        raise Stop("门卫监听者规则没关掉（见上）。主账号中台已起、只有它在听，门卫开着时谁都连不上它；"
                   "看清原因后再跑一次 rollback --confirm")
    wait_only_listener(host, p, uid)
    owner_health(host, hub_url)


# ---------- migrate ----------
FACTS = ("runs", "cards", "pauses", "max_event", "rules_sha256")


class Progress:
    """搬家进度记在 root 目录的 migrating.json：进程被杀、关了终端，rollback 能照着收拾（独立审查 P2-2）。"""

    def __init__(self, stamp, owner_account, hub_url):
        self.f = gate_root() / "migrating.json"
        self.data = {"stamp": stamp, "owner_account": owner_account, "hub_url": hub_url,  # 退回时核中台端口、监听者用
                     "done": {"held": False, "stopped": False, "gate_hub": False, "daemon": False, "guard_on": False,
                              "moved": False}}
        write_json(self.f, self.data)

    def mark(self, step):
        self.data["done"][step] = True
        write_json(self.f, self.data)

    @property
    def done(self):
        return self.data["done"]

    def close(self, how):
        os.replace(self.f, gate_root() / f"migrating.{how}-{self.data['stamp']}.json")


def _on_term(signum, frame):
    raise Stop(f"收到停止信号 {signum}")


def cmd_migrate(host, a):
    if not a.confirm:
        raise Stop("搬家会停掉主账号的中台：确认后加 --confirm")
    say("搬家前核对：")
    if not print_checks(checks(host, a)):
        raise Stop("核对没通过，没有动任何东西")
    current = load_current()
    owner = host.account(a.owner_account)
    ohub = owner_hub(host, a.owner_account)
    gp = gate_paths(host)
    gate = gate_account()
    guid = host.account(gate)["uid"]
    stamp = now_stamp()
    root = gate_root()
    bk = root_dir(host, root / "backups", 0o700) / stamp
    make_dir(bk, 0o700)
    before = snapshot_as(host, current, a.owner_account, ohub)
    agent = f"gui/{owner['uid']}/{OWNER_AGENT}"
    tmp = owner_evidence(host, a.owner_account, stamp)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)       # 关终端不打断（被打断也有进度记录）
    signal.signal(signal.SIGTERM, _on_term)
    prog = Progress(stamp, a.owner_account, before["url"])
    point = f"gate-migrate-{stamp}"
    try:
        say(f"0. 主账号控制面停写、排空（0D 恢复门，恢复点 {point}）")
        prog.mark("held")                                # 先记：hold 做成了还没来得及记就被杀，退回时照样结束它
        max_card = snapshot_as(host, current, a.owner_account, ohub).get("max_card", 0)   # hold 之前现取（停写后不再发卡）
        hold_and_drain(host, current, a.owner_account, point, bk, max_card)

        say("1. 停主账号的中台")
        prog.mark("stopped")
        if not unload(host, agent):                       # 真退了才备份：还在跑的中台备份出来的不是停下时的样子（P1-01）
            raise Stop(f"主账号中台卸不下来（launchctl 一直显示 {OWNER_AGENT} 还在）")
        host.launchctl("disable", agent)
        wait_port_free(host, before["listen"])
        stopped = snapshot_as(host, current, a.owner_account, ohub)
        if stopped["live_runs"] or stopped["live_cards"]:
            raise Stop("停下来以后又出现了在跑的轮次或没答完的卡片")

        say("2. 备份（root 收一份，只有 root 能读）")
        run_helper(host, current, a.owner_account, ["mkdirs", str(tmp)])
        run_cli(host, current, a.owner_account, ohub, ["backup", str(tmp / "hub")])
        files = {}
        for name in HUB_FILES:
            code, data, _ = host.as_user(a.owner_account, ["/bin/cat", str(tmp / "hub" / name)])
            if code != 0:
                if name in NEEDED:
                    raise Stop(f"备份里缺 {name}")
                continue
            files[name] = data
        make_dir(bk / "owner-hub", 0o700)
        for name, data in files.items():
            write_root_file(bk / "owner-hub" / name, data, 0o600)
        code, plist_data, _ = host.as_user(a.owner_account, ["/bin/cat", str(owner_agent_plist(host, a.owner_account))])
        if code == 0:
            write_root_file(bk / f"{OWNER_AGENT}.plist", plist_data, 0o600)
        host.as_user(a.owner_account, ["/bin/rm", "-rf", str(tmp / "hub")])
        manifest = {"stamp": stamp, "owner_account": a.owner_account, "owner_uid": owner["uid"],
                    "engine": current["engine"]["commit"], "repo": current["repo"]["commit"],
                    "files": {n: hashlib.sha256(d).hexdigest() for n, d in files.items()}, "before": stopped}
        write_json(bk / "manifest.json", manifest, 0o600)

        say(f"3. 导入 {gate}（config 只留 url、hub，master.key 换新，签新的本人密钥）")
        staging = gp["home"] / f".agent-autoloop-migrate-{stamp}"
        run_helper(host, current, gate, ["mkdirs", str(staging), str(gp["support"] / "autoloop")])
        for name, data in files.items():
            run_helper(host, current, gate, ["put", str(staging / name)], stdin=data)
        run_helper(host, current, gate, ["prepare", str(staging)])
        prog.mark("gate_hub")
        run_cli(host, current, gate, gp["hub"], ["restore", str(staging), "--yes"])
        owner_name = f"gate-owner-{stamp.lower()}"
        run_helper(host, current, gate, ["new-owner", owner_name, "--cli", current["cli"], "--home", str(gp["hub"])],
                   env=cli_env(current, gp["hub"]))
        host.as_user(gate, ["/bin/rm", "-rf", str(staging)])

        say("4. 装 dt-gate 的中台服务，核对和搬家前一致")
        shared_dir(host)
        g = host.account(gate)
        for sub in ("grants", "tasks"):
            d = shared() / sub
            if d.is_symlink():
                raise Stop(f"{d} 是链接")
            if not d.exists():
                make_dir(d, 0o755)
            host.chown(d, g["uid"], g["gid"])
            os.chmod(d, 0o755)
        url = before["url"]
        prog.mark("daemon")                              # 先记：plist 写了但没装上，也要收拾（独立审查 P2-1）
        install_daemon(host, current, "hub", url)
        wait_health(host, url, current["engine"]["commit"], guid)
        after = snapshot_as(host, current, gate, gp["hub"])
        diff = [k for k in FACTS if after[k] != stopped[k]]
        if diff:
            raise Stop(f"搬过去以后对不上：{diff}（有到期的定时项被补跑时会这样，退回后再搬一次）")

        say("4b. 打开门卫监听者规则（3350 / 3352 / 3353 只放行 dt-gate 在听的）")
        prog.mark("guard_on")                            # 先记：标记写了还没来得及记，退回时照样删
        guard_on(host, stamp)

        say("5. 删掉主账号见过的钥匙")
        keep = [f"owner:{owner_name}"]
        if f"worker:{worker_account()}" in after["keys"]:
            keep.append(f"worker:{worker_account()}")
        env = {**cli_env(current, gp["hub"]), "DT_HUB_LISTENER_UID": str(guid)}
        pruned = run_helper(host, current, gate, ["prune-keys", "--cli", current["cli"], "--home", str(gp["hub"]),
                                                  *sum((["--keep", k] for k in keep), [])], env=env)

        say("6. 主账号的中台目录改名留着（回滚用），LaunchAgent 挪走")
        moved = Path(str(ohub) + f".moved-to-gate-{stamp}")
        plist_moved = tmp / f"{OWNER_AGENT}.plist"
        prog.mark("moved")
        code, _, err = host.as_user(a.owner_account, ["/bin/mv", "-n", str(ohub), str(moved)])
        if code != 0 or exists_as(host, a.owner_account, ohub):
            raise Stop(f"主账号的中台目录改不了名：{err.strip()[:200]}")
        host.as_user(a.owner_account, ["/bin/mv", "-n", str(owner_agent_plist(host, a.owner_account)), str(plist_moved)])
        note = (f"{MARK}（{stamp}，数字分身 ADR-015 0E-2）。\n原来的目录：{moved}\n"
                f"回滚：sudo '{entry()}' rollback --confirm\n").encode()
        run_helper(host, current, a.owner_account, ["put", str(ohub), "--mode", "644"], stdin=note)
        state = {"migrated_at": stamp, "owner_account": a.owner_account, "owner_uid": owner["uid"], "backup": str(bk),
                 "owner_moved": str(moved), "owner_hub": str(ohub), "plist_moved": str(plist_moved),
                 "plist_home": str(owner_agent_plist(host, a.owner_account)), "owner_key": owner_name,
                 "hub_url": url, "keys_removed": pruned["removed"], "keys_kept": pruned["kept"], "worker_rotated": False,
                 "owner_hold": point, "owner_hold_max_card": max_card}
        write_json(root / "state.json", state)
    except BaseException as e:
        say(f"出错了，退回主账号中台：{e}")
        if undo(host, current, a.owner_account, stamp, prog.done, prog.data.get("hub_url")):
            prog.close("undone")
        else:
            say(f"退回没做完整（见上），进度记录留在 {prog.f}：看清原因后跑 rollback --confirm")
        raise
    prog.close("done")
    say(f"搬好了：中台以 {gate} 身份运行（{LABEL['hub']}），{url}")
    say(f"  删掉的钥匙：{', '.join(pruned['removed']) or '无'}；留下：{', '.join(pruned['kept'])}")
    say(f"  主账号原来的中台在 {moved}（钥匙已作废，回滚用）；root 备份在 {bk}")
    say(f"  主账号控制面保持停写（恢复点 {point}，回滚时结束）；dt-gate 的恢复闸从新账本开始、关着，"
        "锁定中台版本并对账后才开（run autoloop_recovery pin / reconcile，常浩说了才 pin）")
    return 0


def owner_note_ok(host, owner, path):
    """主账号原位置是本脚本放的说明文件（或已经没有了）：只读，以主账号身份看（root 不碰账号家目录）。"""
    code, out, _ = host.as_user(owner, ["/bin/cat", str(path)])
    if code != 0:
        return not exists_as(host, owner, path)
    return out.decode("utf-8", "replace").startswith(MARK)


def unmark_owner(host, current, owner_account):
    """主账号原位置的说明文件：是本脚本放的就删掉（别的东西不动）。"""
    run_helper(host, current, owner_account, ["unmark", str(owner_hub(host, owner_account)), "--prefix", MARK])


def undo(host, current, owner_account, stamp, done, hub_url=None):
    """migrate 中途出错或被打断：dt-gate 这边停掉、挪开留证，主账号中台原样放回、重新启用。尽力做完每一步。
    dt-gate 的中台卸不掉、或卸了端口上还有人在听（L3 第 2 轮 P1-01）：现场保持「还在 dt-gate」——目录不挪、主账号目录不放回、
    门卫不关、主账号中台不启用、停写和进度记录都留着，返回 False，看清原因后 rollback 再收拾。"""
    gp = gate_paths(host)
    daemon_ok = True
    if done.get("daemon"):
        try:
            remove_daemon(host, "hub")
            if not hub_url:
                raise Stop("进度记录里没有中台地址，核不了端口上还有没有人在听")
            wait_no_listener(host, hub_port_of(hub_url), allow={host.account(owner_account)["uid"]})
        except Stop as e:
            say(f"  {e}")
            daemon_ok = False
    if done.get("gate_hub") and daemon_ok:
        host.as_user(gate_account(), ["/bin/mv", "-n", str(gp["hub"]), str(gp["hub"]) + f".failed-{stamp}"])
    ohub = owner_hub(host, owner_account)
    if done.get("moved") and daemon_ok:
        try:
            unmark_owner(host, current, owner_account)
        except Stop as e:
            say(f"  {e}")
        host.as_user(owner_account, ["/bin/mv", "-n", str(ohub) + f".moved-to-gate-{stamp}", str(ohub)])
        host.as_user(owner_account, ["/bin/mv", "-n", str(owner_evidence(host, owner_account, stamp) / f"{OWNER_AGENT}.plist"),
                                     str(owner_agent_plist(host, owner_account))])
        state = gate_root() / "state.json"
        if state.exists():
            os.replace(state, gate_root() / f"state.undone-{stamp}.json")
    up_ok = daemon_ok
    if done.get("stopped") and daemon_ok:                # 主账号中台先占住端口、核过监听者，再关门卫（P1-03）
        try:
            owner_takeover(host, owner_account, hub_url, guard=bool(done.get("guard_on")))
        except Stop as e:
            say(f"  {e}")
            up_ok = False
    # 核结果（复核 P2-c）：主账号中台目录回来了、dt-gate 的中台目录已挪开、服务卸了
    ok = True
    if not exists_as(host, owner_account, ohub / "config.json"):
        say(f"  主账号的 {ohub} 没有放回来")
        ok = False
    if done.get("gate_hub") and exists_as(host, gate_account(), gp["hub"]):
        say(f"  dt-gate 的 {gp['hub']} 没有挪开")
        ok = False
    if done.get("daemon") and installed("hub"):
        say(f"  {LABEL['hub']} 没有卸掉")
        ok = False
    ok = ok and up_ok
    if done.get("held"):                                 # 别的都放回去了才结束停写；没放回的保持停写，rollback 再收拾
        if not ok:
            say("  主账号控制面保持停写，等上面的收拾好再结束（rollback --confirm）")
        else:
            ok = release_hold(host, current, owner_account, f"gate-migrate-{stamp}")
    return ok


def start_owner_agent(host, owner_account):
    owner = host.account(owner_account)
    agent = f"gui/{owner['uid']}/{OWNER_AGENT}"
    host.launchctl("enable", agent)
    code, msg = host.launchctl("bootstrap", f"gui/{owner['uid']}", str(owner_agent_plist(host, owner_account)))
    if code != 0 and host.launchctl("print", agent)[0] != 0:   # 已经装着（上次起过）不算没起来
        say(f"  主账号中台已重新启用，但这次没起来（{msg[:120]}）；要主账号登录着才起得来，"
            "登录后再跑一次 rollback --confirm")


# ---------- windows / rotate-worker / update / run ----------
def need_state():
    s = load_state()
    if not s:
        raise Stop("还没搬（gate/state.json 不存在）：先跑 migrate")
    return s


def cmd_windows(host, a):
    if not a.confirm:
        raise Stop("会装两个常驻服务：确认后加 --confirm")
    s, current = need_state(), load_current()
    gp = gate_paths(host)
    guid = host.account(gate_account())["uid"]
    owner = host.account(s["owner_account"])
    osup = Path(owner["home"]) / "Library/Application Support/DigitalTwin/autoloop"
    for which, tok, p in (("readonly", "readonly.token", port("DT_GATE_READONLY_PORT", 3352)),
                          ("intake", "intake.token", port("DT_GATE_INTAKE_PORT", 3353))):
        f = gp["support"] / "autoloop" / tok
        run_helper(host, current, gate_account(), ["token", str(f)])
        code, data, err = host.as_user(gate_account(), ["/bin/cat", str(f)])
        if code != 0:
            raise Stop(f"读不到 {gate_account()} 的 {tok}：{err[:100]}")
        run_helper(host, current, s["owner_account"], ["put", str(osup / tok), "--mkdir"], stdin=data)
        del data
        s.setdefault("ports", {})[which] = p             # 先记端口再装：回滚按装的时候的端口核（独立审查建议 3）
        write_json(gate_root() / "state.json", s)
        install_daemon(host, current, which, s["hub_url"])
        wait_answering(host, p, guid)
        say(f"{LABEL[which]} 在 127.0.0.1:{p} 起来了；主账号的令牌副本：{osup / tok}（600）")
    return 0


# ---------- 门卫监听者规则（0E-1 #313：3350 / 3352 / 3353 只放行 dt-gate 在听的） ----------
# 规则由 root 写的标记 hub-in-gate 控制：中台搬进 dt-gate 之后才打开（标记在 → apply 加上规则、状态写 guard=yes），
# 回滚时先关（删标记 → apply、guard=no）再启用主账号中台，反过来主账号中台起来后谁都连不上它。pf 认的是建套接字时的
# 身份：三个端口都由 dt-gate 的进程自己绑（本脚本装的服务都是 UserName dt-gate、不用 launchd 的 Sockets）。
PF_STALE_SECONDS = 10 * 60
PF_APPLY_TRIES = 3
ROOT_UID = 0                           # 测试里在进程内替换，不经环境变量


def pf_dir():
    return env_path("DT_PF_DIR", "/Library/Application Support/DigitalTwin")


def hub_mark():
    return pf_dir() / "hub-in-gate"


def pf_guard_problem(host, want):
    """门卫状态不是「state=ok、guard=<want>、10 分钟内写的、本次开机写的」就返回原因，是就返回 None。"""
    f = pf_dir() / "pf-dt-worker.status"
    try:
        text = f.read_text(encoding="utf-8")
        age = time.time() - f.stat().st_mtime
    except OSError:
        return "读不到门卫状态（pf-dt-worker.status）"
    fields = dict(kv.split("=", 1) for kv in text.split() if "=" in kv)
    if fields.get("state") != "ok":
        return f"门卫规则不在生效（state={fields.get('state') or '?'}）"
    if fields.get("guard") != want and not (want == "no" and "guard" not in fields):   # 旧版门卫不认标记，等于关着
        return f"门卫监听者规则是 guard={fields.get('guard') or '没有'}，要 guard={want}（门卫是不是还没装 #313 的新版）"
    if not 0 <= age <= PF_STALE_SECONDS:
        return f"门卫状态 {int(age // 60)} 分钟没刷新"
    boot = host.boot_sec()
    if not boot or fields.get("boot") != boot:
        return "门卫状态不是本次开机写的（或读不到开机时间）"
    return None


def pf_apply(host, want):
    """让门卫按现在的标记重新加载，直到状态是 guard=<want>；和定时 apply 撞上会判 failed，隔 2 秒再来，最多 3 次。"""
    script = pf_dir() / "dt_worker_pf.sh"
    if host.guard("root_path", str(pf_dir())) != 0:
        raise Stop(f"{pf_dir()} 不是从 / 起只有 root 能改的目录，不以 root 跑里面的门卫脚本")
    try:
        st = os.lstat(script)
    except OSError:
        raise Stop(f"没有门卫脚本 {script}（先装 #313 的新版门卫）")
    if not stat.S_ISREG(st.st_mode) or st.st_uid != ROOT_UID or st.st_mode & 0o022:
        raise Stop(f"{script} 不是 root 所有、别人不能改的普通文件，不以 root 跑它")
    prob = None
    for i in range(PF_APPLY_TRIES):
        host.pf_apply(script)
        prob = pf_guard_problem(host, want)
        if prob is None:
            return
        if i < PF_APPLY_TRIES - 1:
            host.sleep(2)
    raise Stop(f"门卫监听者规则没切到 guard={want}：{prob}")


def guard_on(host, stamp):
    """写标记（root:wheel 644，同目录临时文件 O_EXCL|O_NOFOLLOW 再改名）、apply，核 guard=yes。"""
    if host.guard("root_path", str(pf_dir())) != 0:
        raise Stop(f"{pf_dir()} 不是从 / 起只有 root 能改的目录，不写门卫标记")
    write_root_file(hub_mark(), f"{gate_account()} {stamp}\n".encode(), 0o644)
    host.chown(hub_mark(), 0, 0)
    pf_apply(host, "yes")


def guard_off(host):
    """先核目录只有 root 能改、再删标记（不在也算成功；是链接就删链接本身）、apply，核 guard=no。
    成功返回 True，失败说清原因返回 False；目录核不过就什么都不删（L3 第 2 轮 P2-01）。"""
    try:
        if host.guard("root_path", str(pf_dir())) != 0:
            raise Stop(f"{pf_dir()} 不是从 / 起只有 root 能改的目录，不动里面的门卫标记")
        if os.path.lexists(hub_mark()):
            os.unlink(hub_mark())
        pf_apply(host, "no")
        return True
    except (OSError, Stop) as e:
        say(f"  门卫监听者规则没关掉：{e}；关掉之前主账号中台起来也没人连得上，先不启用它")
        return False


def pf_listener_rule(host):
    """换工人钥匙的前提：门卫规则已按监听账号放行 3350（只有 dt-gate 在听的才收），否则中台重启的空档里新钥匙照样
    能被抢绑端口的冒充者拿走（复核：pf 规则要和换钥匙、恢复派单定好先后）。规则文件里有这一行不等于已经加载生效：
    再核状态 guard=yes、够新、本次开机写的（#313）。"""
    try:
        rules = (pf_dir() / "pf-dt-worker.rules").read_text(encoding="utf-8")
    except OSError:
        raise Stop("读不到门卫规则（pf-dt-worker.rules）")
    pat = re.compile(rf"^\s*(pass|block)\b.*\bin\b.*\b3350\b.*\buser\b.*{re.escape(gate_account())}", re.M)
    if not pat.search(rules):
        raise Stop("门卫规则还没按监听账号保护 3350（要 0E-1 的 dt_worker_pf.sh 加上「3350 只收 dt-gate 在听的」）；"
                   "装上之前换工人钥匙没用，先不换")
    prob = pf_guard_problem(host, "yes")
    if prob:
        raise Stop(f"门卫监听者规则没在生效：{prob}；先不换")


def cmd_rotate_worker(host, a):
    if not a.confirm:
        raise Stop("换钥匙后 dt-worker 的接单程序要重启才接得上：确认后加 --confirm")
    s, current = need_state(), load_current()
    pf_listener_rule(host)
    gp = gate_paths(host)
    w = host.account(worker_account())
    cfg = Path(w["home"]) / ".agent-autoloop/config.json"
    if not exists_as(host, worker_account(), cfg):
        raise Stop(f"{worker_account()} 还没接入中台（没有 {cfg}）")
    key = run_cli(host, current, gate_account(), gp["hub"], ["key", "new", "worker", worker_account()]).strip()
    run_helper(host, current, worker_account(), ["set-worker-key", str(cfg)], stdin=key.encode())
    del key
    s["worker_rotated"] = now_stamp()
    write_json(gate_root() / "state.json", s)
    say(f"{worker_account()} 的接单钥匙换好了，旧的已失效；重启它的接单程序后生效")
    return 0


def _install_all(host, current, s, which_list):
    guid = host.account(gate_account())["uid"]
    for which in which_list:
        install_daemon(host, current, which, s["hub_url"])
        if which == "hub":
            wait_health(host, s["hub_url"], current["engine"]["commit"], guid)
        else:
            wait_answering(host, port("DT_GATE_READONLY_PORT" if which == "readonly" else "DT_GATE_INTAKE_PORT",
                                      3352 if which == "readonly" else 3353), guid)


def cmd_update(host, a):
    if not a.confirm:
        raise Stop("会重启 dt-gate 的服务：确认后加 --confirm")
    s, current = need_state(), load_current()
    which_list = [w for w in ("hub", "readonly", "intake") if installed(w)]
    if "hub" in which_list:                     # 新引擎可能改写库：换之前先备份（在 dt-gate 自己家里，复核 P2-d）
        gp = gate_paths(host)
        target = gp["home"] / f".agent-autoloop-update-{now_stamp()}"
        run_cli(host, load_current(), gate_account(), gp["hub"], ["backup", str(target)])
        say(f"换之前的中台备份：{target}（dt-gate 家里）")
    try:
        _install_all(host, current, s, which_list)
    except Stop as e:
        prev = gate_root() / "previous.json"
        if not prev.exists():
            raise
        say(f"新副本没起来（{e}），退回上一份副本")
        old = read_json(prev)
        _install_all(host, old, s, which_list)
        write_json(gate_root() / "current.json", old)
        raise Stop(f"新副本没起来，已退回上一份（引擎 {old['engine']['commit'][:12]}、本仓库 {old['repo']['commit'][:12]}）")
    for which in which_list:
        say(f"{LABEL[which]} 换到新副本并重启了")
    return 0


def as_gate_tool(host, current, tool, args, timeout=3600):
    """以 dt-gate 身份跑副本里的一个控制面工具；环境同 dt-gate 的服务（gate_env），中台地址取 state，还没搬家时用默认。"""
    s = load_state()
    hub_url = (s or {}).get("hub_url") or "http://127.0.0.1:3350"
    argv = [PY, *PY_FLAGS, str(Path(current["repo"]["dir"]) / "hermes/scripts" / f"{tool}.py"), *args]
    env = {k: v for k, v in gate_env(host, current, hub_url).items() if k not in ("HOME", "PATH", "USER", "LOGNAME")}
    code, out, err = host.as_user(gate_account(), argv, env=env, timeout=timeout)
    return code, out.decode("utf-8", "replace"), err


def gate_github():
    """副本里的 gate_github（令牌格式、仓库名单、代理写法只在那里定义一份）；放函数里：只有发布凭据的命令用。"""
    import gate_github as GG
    return GG


def cmd_put_publish_token(host, a):
    if not a.confirm:
        raise Stop("会把机器账号的发布令牌写进 dt-gate 的 ~/.dt/github-publish.token：确认后加 --confirm")
    GG = gate_github()
    current = load_current()
    if not (Path(current["repo"]["dir"]) / "hermes/scripts/gate_github.py").is_file():
        raise Stop("副本里没有 gate_github.py：先用合并了 0E-4 的提交重新 stage")
    tok = host.read_secret(f"粘贴 {GG.LOGIN} 的经典令牌（只勾 public_repo；输入不显示），回车：").strip()
    if not GG.TOKEN_RE.fullmatch(tok):
        raise Stop("这不像一个 GitHub 令牌（ghp_… 或 github_pat_…），没写")
    path = gate_home(host) / ".dt/github-publish.token"
    run_helper(host, current, gate_account(), ["put", str(path), "--mode", "600", "--mkdir"], stdin=(tok + "\n").encode())
    tok = None
    say(f"1. 令牌已以 dt-gate 身份写进 {path}（600，内容不显示）")
    say(f"2. 以 dt-gate 身份核令牌的账号和权限（gate_github check --repo {a.repo}）：")
    code, out, err = as_gate_tool(host, current, "gate_github", ["check", "--repo", a.repo], timeout=300)
    say("   " + GG.mask(out.strip() or err.strip())[:600])
    if code != 0:
        raise Stop("令牌放进去了，但核对没过（见上）：按原因处理（重建令牌就再跑一次本命令），不要打开真发布")
    say("令牌核对通过。下一步：sudo …/dt-gate-hub run gate_github doctor --repo dt-sandbox --online，全过再 publish on --confirm")
    return 0


def read_switch_file():
    """root 写的开关文件原样读出（只给 status 显示）；没有返回 None，读不懂返回 {"bad": 原因}。"""
    f = gate_root() / "publish.json"
    if not os.path.lexists(f):
        return None
    try:
        if os.path.islink(f):
            return {"bad": "是链接（受信发布当关着）"}
        sw = read_json(f)
        return sw if isinstance(sw, dict) else {"bad": "不是对象（受信发布当关着）"}
    except (OSError, ValueError) as e:
        return {"bad": f"读不懂（{type(e).__name__}，受信发布当关着）"}


def cmd_publish(host, a):
    f = gate_root() / "publish.json"
    if a.state == "off":
        if os.path.lexists(f):
            os.unlink(f)
            say(f"真发布已关：删掉了 {f}。受信发布从下一次起不推、不开 PR；出口账本对账照常（直连）")
        else:
            say("真发布本来就关着（没有开关文件）")
        return 0
    if not a.confirm:
        raise Stop("打开后，dt-gate 会用机器账号的令牌真推这个仓库的 <执行方>/<单号> 分支、开 PR：确认后加 --confirm")
    GG = gate_github()
    if a.repo not in GG.REPOS:
        raise Stop(f"只能放开 {'、'.join(GG.REPOS)}（gate_github.REPOS）")
    if a.proxy:
        try:
            GG.valid_proxy(a.proxy)
        except GG.NotReady as e:
            raise Stop(str(e))
    root_dir(host, gate_root(), make=False)
    current = load_current()
    pub = Path(current["repo"]["dir"]) / "hermes/scripts/autoloop_publish.py"
    if not pub.is_file() or not BOUNDARY_MARK.search(pub.read_text(encoding="utf-8")):
        raise Stop("副本里的受信发布还没有代次边界（#320，DECIDED 11-22：放开之前必须合并）：#320 合并、重新 stage 后再打开")
    if not current.get("gh"):
        raise Stop("副本里没有 gh：先 stage … --gh <gh 程序>")
    args = ["doctor", "--repo", a.repo, "--online", *(["--proxy", a.proxy] if a.proxy else [])]
    say(f"1. 以 dt-gate 身份自检（gate_github {' '.join(args)}）：")
    code, out, err = as_gate_tool(host, current, "gate_github", args, timeout=600)
    for line in GG.mask(out).strip().splitlines():
        say("   " + line)
    if code != 0:
        raise Stop(f"自检有不过的项，没打开：{GG.mask(err).strip()[:200]}")
    sw = {"enabled": True, "repos": [a.repo], "proxy": a.proxy or None, "at": now_stamp(), "by": a.owner_account}
    write_json(f, sw, 0o644)
    say(f"2. 写好开关文件 {f}：放开 {a.repo}，{'代理 ' + a.proxy if a.proxy else '直连'}")
    code, out, err = as_gate_tool(host, current, "gate_github", ["doctor", "--repo", a.repo], timeout=300)
    if code != 0 or not re.search(r"^\[通过\] 真发布开关：开着", out, re.M):
        os.unlink(f)
        raise Stop(f"打开后以 dt-gate 身份回读不对，已删掉开关文件、算没打开：{GG.mask(out + err).strip()[-400:]}")
    say("3. 以 dt-gate 身份回读：开关开着。真发布已打开；随时 sudo …/dt-gate-hub publish off 关掉")
    return 0


def cmd_run(host, a):
    if a.tool not in TOOLS and a.tool != "agent-autoloop":
        raise Stop(f"只能跑这些：agent-autoloop、{'、'.join(TOOLS)}")
    s, current = need_state(), load_current()
    if a.tool == "agent-autoloop":
        if not a.args or (a.args[0] not in CLI_ALLOWED and a.args[:2] != ["key", "list"]):
            raise Stop(f"中台命令行只放行：{'、'.join(CLI_ALLOWED)}、key list（签钥匙、备份、恢复不经这里）")
        if a.args[:2] != ["key", "list"]:        # 其余子命令由引擎带本人密钥连中台：先核端口上是 dt-gate 在听（复核 P1-B）
            hub_port = urllib.parse.urlsplit(s["hub_url"]).port
            if not listened_by(host, hub_port, host.account(gate_account())["uid"]):
                raise Stop(f"{hub_port} 端口上在听的不是 dt-gate（中台没起来或被别人占了），不带本人密钥去；先看 status")
        env = {k: v for k, v in gate_env(host, current, s["hub_url"]).items() if k not in ("HOME", "PATH", "USER", "LOGNAME")}
        code, out, err = host.as_user(gate_account(), [current["cli"], *a.args], env=env, timeout=3600)
        out = out.decode("utf-8", "replace")
    else:
        code, out, err = as_gate_tool(host, current, a.tool, a.args)
    sys.stdout.write(out)
    sys.stderr.write(err)
    return code


def cmd_status(host, a):
    try:
        current = load_current()
        say(f"副本：引擎 {current['engine']['commit'][:12]}、本仓库 {current['repo']['commit'][:12]}、"
            f"node {current.get('node_version')}（stage 于 {current['staged_at']}）")
        gh = current.get("gh")
        say(f"  gh 副本：{gh['version'] + '（SHA-256 ' + gh['sha256'][:12] + '）' if gh else '没有（stage … --gh 装）'}")
    except Stop as e:
        say(f"副本：{e}")
    sw = read_switch_file()
    if sw is None:
        say("真发布开关：关着（没有开关文件）")
    elif "bad" in sw:
        say(f"真发布开关：开关文件{sw['bad']}")
    else:
        say(f"真发布开关：{'开着' if sw.get('enabled') is True else '关着'}，放开 {'、'.join(map(str, sw.get('repos') or [])) or '（无）'}，"
            f"{'代理 ' + str(sw['proxy']) if sw.get('proxy') else '直连'}，{sw.get('by') or '?'} 于 {sw.get('at') or '?'}"
            "（令牌和联网：sudo …/dt-gate-hub run gate_github doctor --repo dt-sandbox --online）")
    s = load_state()
    if host.is_root():
        settle_progress()
    say(f"搬家：{'于 ' + s['migrated_at'] + '，主账号原目录 ' + s['owner_moved'] if s else '还没搬'}")
    if (gate_root() / "migrating.json").exists():
        say("  有搬到一半的记录（migrating.json）：跑 rollback --confirm 收拾")
    for which in ("hub", "readonly", "intake"):
        code, out = host.launchctl("print", f"system/{LABEL[which]}")
        pid = re.search(r"\bpid = (\d+)", out or "")
        say(f"  {LABEL[which]}：{'在跑（进程 ' + pid.group(1) + '）' if code == 0 and pid else ('已装、没在跑' if code == 0 else '没装')}")
    if s:
        p = urllib.parse.urlsplit(s["hub_url"]).port
        try:
            uid = host.account(gate_account())["uid"]
            who = "dt-gate" if listened_by(host, p, uid) else "不是 dt-gate（或没人）"
            code, body = http_get(s["hub_url"].rstrip("/") + "/healthz")
            say(f"  中台：{p} 端口上在听的是 {who}；健康检查 HTTP {code} {body.decode('utf-8', 'replace')[:120]}")
        except OSError as e:
            say(f"  中台健康检查：连不上（{type(e).__name__}）")
    return 0


# ---------- rollback ----------
def rollback_ports(s):
    """回滚要核的三个端口：中台按 state 的地址；两个窗口按 windows 装的时候记下的（记了就只认它，不合格停下），
    没记的（没装窗口、或旧 state）按现在的设置（L3 第 3 轮 P2-02）。"""
    ports = s.get("ports") or {}
    out = [hub_port_of(s["hub_url"])]
    for which, env, default in (("readonly", "DT_GATE_READONLY_PORT", 3352), ("intake", "DT_GATE_INTAKE_PORT", 3353)):
        v = ports.get(which)
        out.append(port(env, default) if v is None else check_port(v, f"state 里记的 {which} 端口"))
    return out


def _rollback_move(host, current, s, gp, gate, stamp):
    """回滚的前几步：留备份、停服务、核端口、主账号中台目录放回。门卫这时还开着，由 owner_takeover 在主账号中台
    占住端口、核过监听者之后才关（L3 第 3 轮 P1-03）。"""
    # 先只读核对，再停服务，最后才删说明文件、放回目录；中途停下时说明文件还在，主账号命令行不会新建空中台，
    # 再跑一次 rollback 能接着来（独立审查第 2 轮 P2-2）
    if not owner_note_ok(host, s["owner_account"], s["owner_hub"]):
        raise Stop(f"主账号里 {s['owner_hub']} 不是搬家时放的说明文件，没法放回：请人工查看")
    if not exists_as(host, s["owner_account"], s["owner_moved"]):
        raise Stop(f"主账号里找不到搬家时留下的 {s['owner_moved']}")
    say("1. 给 dt-gate 的中台留一份备份（在 dt-gate 自己家里）")
    if installed("hub"):
        try:
            run_cli(host, current, gate, gp["hub"], ["backup", str(gp["home"] / f".agent-autoloop-rollback-{stamp}")])
        except Stop as e:
            say(f"  备份没做成（{e}），继续回滚；dt-gate 的中台目录整个改名留着")
    say("2. 停掉 dt-gate 的三个服务，核三个端口都没人在听（门卫先开着）")
    for which in ("intake", "readonly", "hub"):          # 卸不掉就停在这里：plist、门卫、目录、停写都不动（P1-01）
        remove_daemon(host, which)
    for p in rollback_ports(s):
        wait_no_listener(host, p)                        # 三个端口都没有任何账号在听了才往下
    try:
        unmark_owner(host, current, s["owner_account"])
    except Stop as e:
        raise Stop(f"主账号里 {s['owner_hub']} 不是搬家时放的说明文件，没法放回：请人工查看（{e}）")
    if exists_as(host, s["owner_account"], s["owner_hub"]):
        raise Stop(f"主账号里已经有 {s['owner_hub']}，没法放回：请人工查看")
    host.as_user(gate, ["/bin/mv", "-n", str(gp["hub"]), str(gp["hub"]) + f".rolled-back-{stamp}"])
    say("3. 主账号的中台放回去")
    code, _, err = host.as_user(s["owner_account"], ["/bin/mv", "-n", s["owner_moved"], s["owner_hub"]])
    if code != 0:
        raise Stop(f"放不回去：{err[:200]}")


def cmd_rollback(host, a):
    if not a.confirm:
        raise Stop("回滚会停掉 dt-gate 的中台、把主账号的中台放回去：确认后加 --confirm")
    current = load_current()
    settle_progress()
    half = gate_root() / "migrating.json"
    if load_state() is None and half.exists():
        m = read_json(half)
        say(f"收拾搬到一半的那次（{m['stamp']}，做过：{[k for k, v in m['done'].items() if v]}）")
        if not undo(host, current, m["owner_account"], m["stamp"], m["done"], m.get("hub_url")):
            raise Stop("没收拾完整（见上），进度记录留着；看清原因后再跑一次 rollback --confirm")
        os.replace(half, gate_root() / f"migrating.undone-{m['stamp']}.json")
        say("收拾好了：主账号中台已放回并重新启用")
        return 0
    s = need_state()
    gp = gate_paths(host)
    gate = gate_account()
    stamp = now_stamp()
    # 上次回滚已经把主账号中台目录放回、还没走完就停了（独立审查；L3 第 3 轮起启用、关门卫、核监听者都在放回之后）：
    # 放回之前一定卸过三个服务、核过端口，接着做剩下的
    restored = (not exists_as(host, s["owner_account"], s["owner_moved"])
                and exists_as(host, s["owner_account"], Path(s["owner_hub"]) / "config.json"))
    if restored:
        say("上次回滚已把主账号中台目录放回，接着做剩下的（启用、核监听者、关门卫、结束停写）")
    else:
        _rollback_move(host, current, s, gp, gate, stamp)
    host.as_user(s["owner_account"], ["/bin/mv", "-n", s["plist_moved"], s["plist_home"]])
    say("4. 门卫开着时启用主账号中台，核过端口上只有它在听再关门卫，关了再核一次、做健康检查")
    owner_takeover(host, s["owner_account"], s["hub_url"])
    if s.get("owner_hold") and not gate_cards_clean(host, current, s.get("owner_hold_max_card")):
        s["owner_hold_left"] = s["owner_hold"]
        write_json(gate_root() / "state.json", s)
        say(f"  主账号控制面保持停写（恢复点 {s['owner_hold']}），交常浩：中台退回旧快照后会复用这些卡片号")
    elif s.get("owner_hold") and not release_hold(host, current, s["owner_account"], s["owner_hold"]):
        s["owner_hold_left"] = s["owner_hold"]           # 没结束的停写记进归档的 state；preflight 也会拦着
        write_json(gate_root() / "state.json", s)
        say("  主账号中台已放回，但主账号控制面的维护停写没结束（见上）")
    os.replace(gate_root() / "state.json", gate_root() / f"state.rolled-back-{stamp}.json")
    say("回滚好了：主账号的中台回到搬家前的数据（搬家之后 dt-gate 里新记下的不在里面，备份在 dt-gate 家里）")
    if s.get("worker_rotated"):
        say(f"  注意：{worker_account()} 的接单钥匙在 dt-gate 里换过，主账号旧中台不认，要重新给它接入")
    return 0


def say(line):
    print(line, flush=True)


def main(argv=None, host=None):
    host = host or Host()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--owner-account", default=os.environ.get("SUDO_USER") or "nat")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("stage")
    for k in ("engine-repo", "engine-commit", "repo", "repo-commit", "node"):
        p.add_argument(f"--{k}", required=True)
    p.add_argument("--gh", help="连同 gh 程序一起 stage（受信发布用，0E-4）")
    sub.add_parser("preflight")
    for name in ("migrate", "windows", "rotate-worker", "update", "rollback"):
        sub.add_parser(name).add_argument("--confirm", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("put-publish-token")
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--repo", default="dt-sandbox")
    p = sub.add_parser("publish")
    p.add_argument("state", choices=("on", "off"))
    p.add_argument("--confirm", action="store_true")
    p.add_argument("--repo", default="dt-sandbox")
    p.add_argument("--proxy")
    p = sub.add_parser("run")
    p.add_argument("tool")
    p.add_argument("args", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd != "status" and not host.is_root():
        say("要用 sudo 运行")
        return 1
    if a.cmd not in ("stage", "status") and not from_staged_copy() and not os.environ.get("DT_GATE_ANY_COPY"):
        say(f"[停下] 这份脚本不是 root 装的副本（主账号能改它）。stage 之后请用：sudo '{entry()}' {a.cmd}")
        return 1
    fn = {"stage": cmd_stage, "preflight": cmd_preflight, "migrate": cmd_migrate, "windows": cmd_windows,
          "rotate-worker": cmd_rotate_worker, "update": cmd_update, "run": cmd_run, "status": cmd_status,
          "rollback": cmd_rollback, "put-publish-token": cmd_put_publish_token, "publish": cmd_publish}[a.cmd]
    try:
        return fn(host, a)
    except Stop as e:
        say(f"[停下] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
