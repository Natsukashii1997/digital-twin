#!/usr/bin/env python3
"""受信发布（最小版，批 0C 执行门，#205；方案 docs/agent-autoloop-底座-方案.md 5.5 不变量 1「乙：受信发布」、第 12 节第 6 条；
规格 docs/秘书代批-规格.md §4「发布侧」）。

dt-worker 不持 GitHub 写凭据：工人侧启动脚本（ops/dt_worktree.py）在 CLI 退出后把本单分支打成交接包
（/Users/dt-worker/dt-outbox/<单号>/handoff.json + <单号>.bundle）。本脚本是控制面的受信发布出口，按 ADR-015 归 dt-gate
账号（「受信发布出口从第一天按归 dt-gate 写」）：取包、核对后推分支、开 PR：

  0. 只在 dt-gate 账号里跑（ADR-015；按有效用户判，不认环境变量）。0D 恢复门之前（REAL_PUBLISH = False）发布整体关闭：
     推分支、开 PR 都不做；只有测试在进程内注入测试后端（TEST_BACKEND：本地裸仓库 file:// + 假 gh）时才走一遍，
     远端和 gh 都不从环境变量取（审查第 2 轮 P1-07：授权收紧和发布动作的串行化、结果账本在 0D，之前不启用）。
  1. 交接包只当资料：清单逐字段按白名单校验，分支名必须正好是 <执行方>/<单号>；包先复制到私有临时目录、
     核对哈希再用；只取 Git 对象（bundle verify + fetch，打开对象校验，钩子关掉），不检出、不运行里面任何东西。
  2. 凭证以凭证库为准（autoloop_grants.check，和所有出口共用）：动手前 push_branch、open_pr 两个动作一起预检，
     推之前、开 PR 之前再各现读一次；仓库从凭证取，不信清单。
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
  4. 只推非 main：推送目标写死成 refs/heads/<执行方>/<单号>，不带 --force；远端同名分支已有别的提交就不覆盖。
     GitHub 一侧 dt-sandbox 的规则集只放行 main、agent/**、claude/**、codex/**（常浩 11-18 放行，hermes/baselines/dt-sandbox-github.json）。
  5. 同一分支已有开着的 PR，核对它的目标是本仓库 main、来源是本仓库（不是同名 fork 分支）、分支和头提交正是这次的，
     才沿用（审查 P1-05）；对不上就拒绝；已关闭或已合并的不再开新的。标题和正文用凭证库里的五要素，不用工人写的字。

本批只做手动入口：不接中台规则、不装常驻服务、不写入 Hermes 定时任务（ADR-015 生效顺序第 2 条）；测试只推本地裸仓库。
发布凭据（批 0E-4 最小版，清单 docs/DT-ORC/migration/0E-4-发布凭据-常浩照做.md）：真发布不用运行账号的 git / gh 登录，只认
  - 令牌文件 ~/.dt/publish-github.token（dt-gate 家目录；常浩亲手放，不经任何 AI；属本账号、600、普通文件、不认链接、
    没有放行别人的 ACL）。登记的机器账号见 PUBLISH_CRED（只能推非 main、不能合并，由 GitHub 规则集在服务端挡，#132 实测）；
  - 写死路径的 gh（GH：root 所有的副本，从 / 起每一级只有 root 能改才用）和 /usr/bin/git。
  令牌只在内存和两类子进程的环境里：git push（凭据经 gh auth git-credential 从环境取，不进命令行、不写进任何配置）和 gh。
  取包、验包、取 main、查远端分支都不带令牌。真发布的所有子进程用最小环境：不继承运行账号的 GH_* / GIT_* 变量，HOME 和
  gh 配置目录是空的临时目录，不读全局和系统 git 配置。推之前联网核令牌身份：登录名是登记的机器账号、对仓库能写、不是管理员
  （管理员令牌能绕过 main 的规则集，拒发）。报错和日志里出现令牌一律打码。
  doctor [--online] 只读自检（账号、令牌文件、gh、交接目录、凭证库、中台库、推送目标、开关；--online 加令牌身份和读仓库）。
不在主账号或秘书账号里真跑（ADR-015 第 5 节第 6 条「真实特权不回流」）。
状态只放本脚本自己的目录（运行账号的 DigitalTwin/autoloop/publish/、publish.jsonl），不和主账号其他脚本共用；
凭证库随 autoloop_grants 一起搬进 dt-gate。写前账本、结果不明冻结在 0D；接成正式出口在批 3。

  autoloop_publish.py publish --task t-20261118-abcdef
  autoloop_publish.py scan                 只读：列交接目录里的单和各自能不能发
  autoloop_publish.py doctor [--online]    只读自检：真发布要的东西齐不齐（不读出令牌内容、不推、不开 PR）
环境变量：DT_PUBLISH_OUTBOX（交接目录，默认 /Users/dt-worker/dt-outbox）、DT_PUBLISH_MIRRORS（主账号的裸仓库副本）、
DT_PUBLISH_LOG，以及 autoloop_grants 的 DT_GRANTS_*。远端固定 https://github.com/<仓库>.git，gh、令牌文件的位置写死，
都不能用环境变量改（测试在进程内换 TEST_BACKEND 或这几个常量）。
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import pwd
import re
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_config as C  # noqa: E402
import autoloop_grants as G  # noqa: E402

MAX_MANIFEST = 4096
MAX_BUNDLE = 50 * 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BRANCH = re.compile(r"^(claude|codex)/t-\d{8}-[0-9a-f]{6}$")
KEYS = {"schema", "task_id", "generation", "executor", "repo", "branch", "base", "head", "bundle", "bundle_sha256",
        "run", "at"}
GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "transfer.fsckObjects=true",
            "-c", "fetch.fsckObjects=true"]
RULES = HERE / "merge_rules.json"
RUN_ID = re.compile(r"^[1-9]\d{0,11}$")
MAX_COMMITS = 500
GATE_USER = "dt-gate"           # ADR-015 控制面账号；测试里在进程内替换，不经环境变量
REAL_PUBLISH = False            # 0D 恢复门（串行化、结果账本）之前发布整体关闭（审查 P1-07）
TEST_BACKEND = None             # 测试在进程内换成 {"remote": "file://…/{github}.git", "gh": 假 gh 路径}；不经环境变量
STOPPED = "停下了"               # 启动脚本出了问题没交接的一轮，最后一条消息以它开头（ops/dt_worktree.stop_text）
MAX_HOPS = 5                    # 回答卡片的一轮往回追提问那一轮，最多几层
# 发布凭据（0E-4 最小版）。都是写死的常量，不从环境变量取；测试在进程内替换。
GIT_BIN = "/usr/bin/git"
GH = "/Library/Application Support/DigitalTwin/bin/gh"      # root 所有的 gh 副本（和门卫的 lark-cli 同一个目录）
TOKEN_FILE = "~/.dt/publish-github.token"                  # 运行账号（dt-gate）家目录里，常浩亲手放
PUBLISH_CRED = {"Natsukashii1997/dt-sandbox": "shanhao1997"}   # 完整仓库名 → 登记的机器账号（#132：协作者 write，非管理员）
PUBLISH_CONF = "/Library/Application Support/DigitalTwin/gate/publish.json"   # root 才能写的发布配置（现在只有代理一项）
PROXY_SHAPE = re.compile(r"^http://127\.0\.0\.1:[1-9]\d{1,4}$")                  # 只认本机代理
TRUST_UIDS = (0,)               # gh 副本、发布配置从 / 起每一级的属主只能是这些（root）
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_]{20,255}$")
CRED = None                     # 真发布进行中的凭据环境（credentials() 设置）；None 时 git / gh 照测试后端原样跑


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
    return TEST_BACKEND["gh"] if TEST_BACKEND else GH


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
          and HEX64.match(m["bundle_sha256"]) and isinstance(m["run"], str) and RUN_ID.match(m["run"]))
    if not ok:
        raise Rejected("交接清单的内容不合格式（分支名必须是 <执行方>/<单号>）")
    return m


# ---------- git ----------
def git(*args, cwd=None, check=True, timeout=300, auth=False):
    """auth=True 只给要写远端的那一步（git push）：真发布时带令牌环境和凭据助手；其余 git 命令永远不带令牌。"""
    cred = CRED
    if cred is None:                                     # 测试后端：原样（运行环境里的 git）
        cmd, env = ["git", *GIT_SAFE], {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    else:
        cmd = [GIT_BIN, *GIT_SAFE, *(cred.helper() if auth else ["-c", "credential.helper="])]
        env = cred.env(token=auth)
    r = subprocess.run([*cmd, *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if cred is not None:
        r.stdout, r.stderr = cred.scrub(r.stdout), cred.scrub(r.stderr)
    if check and r.returncode != 0:
        raise Rejected(f"git {args[0]} 失败：{(r.stderr or r.stdout).strip()[:300]}")
    return r


def gh(*args, timeout=60):
    """跑一次 gh：真发布时用写死路径的副本和令牌环境，输出里的令牌打码；测试后端原样。"""
    cred = CRED
    r = subprocess.run([gh_cmd(), *args], capture_output=True, text=True, timeout=timeout,
                       env=cred.env(token=True) if cred is not None else None)
    if cred is not None:
        r.stdout, r.stderr = cred.scrub(r.stdout), cred.scrub(r.stderr)
    return r


# ---------- 发布凭据（批 0E-4 最小版） ----------
def has_acl(path):
    """macOS 上有没有放行别人的 ACL（ls -led 第二行起出现 allow）；看不了按「有」算。别的系统没有这种 ACL。"""
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(["/bin/ls", "-led", str(path)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return r.returncode != 0 or any(" allow " in line for line in r.stdout.splitlines()[1:])


def trusted_path(path, what):
    """从 / 起每一级（含它自己，链接先解析）属主都在 TRUST_UIDS 里、组和其他人不能写、没有放行别人的 ACL：
    别人能换掉它就能拿到令牌（gh）或改掉发布走的代理（配置）。返回解析后的真实路径。"""
    try:
        real = Path(path).resolve(strict=True)
    except OSError:
        raise Rejected(f"找不到{what} {path}（按清单用 sudo 放一份 root 所有的）")
    for q in (real, *real.parents):
        st = os.stat(q)
        sticky = stat.S_ISDIR(st.st_mode) and st.st_mode & stat.S_ISVTX and q != real   # 带粘滞位的上级：别人换不掉里面不属于他的条目
        if st.st_uid not in TRUST_UIDS or (st.st_mode & 0o022 and not sticky) or has_acl(q):
            raise Rejected(f"{what}路径上的 {q} 不是只有 root 能改（属主、权限位或 ACL），不用")
    return real


def trusted_program(path, what):
    real = trusted_path(path, what)
    if not (real.is_file() and os.access(real, os.X_OK)):
        raise Rejected(f"{what} {real} 不是可执行文件")
    return str(real)


def load_conf():
    """发布配置（root 才能写的文件；dt-gate 的工具在清空的环境里跑，没有代理变量，G-19）：
    {"schema": 1, "proxy": "http://127.0.0.1:<端口>"}，proxy 可以不写（直连）。文件不在 = 直连。多一项、格式不对都拒绝。"""
    if not os.path.lexists(PUBLISH_CONF):
        return {"schema": 1}
    real = trusted_path(PUBLISH_CONF, "发布配置 ")
    try:
        conf = json.loads(_read_regular(real, 4096, "发布配置").decode("utf-8"))
    except ValueError:
        raise Rejected(f"发布配置 {PUBLISH_CONF} 不是合法的 JSON")
    if not isinstance(conf, dict) or conf.get("schema") != 1 or set(conf) - {"schema", "proxy"}:
        raise Rejected(f"发布配置 {PUBLISH_CONF} 的字段不对（只认 schema、proxy）")
    if "proxy" in conf and not (isinstance(conf["proxy"], str) and PROXY_SHAPE.match(conf["proxy"])):
        raise Rejected("发布配置里的 proxy 只能是本机代理 http://127.0.0.1:<端口>")
    return conf


def token_file():
    return Path(TOKEN_FILE).expanduser()


def read_token():
    """发布令牌：本账号自己的普通文件（不认链接）、权限 600、没有放行别人的 ACL、一行。只返回给调用方放进子进程环境，
    不打印、不写日志。"""
    f = token_file()
    try:
        fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise Rejected(f"读不到发布令牌 {f}（不存在、是链接或没权限）：请常浩按清单亲手放进 dt-gate")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077 or has_acl(f):
            raise Rejected(f"发布令牌 {f} 必须是本账号的普通文件、权限 600、没有放行别人的 ACL")
        if st.st_size > 1024:
            raise Rejected(f"发布令牌 {f} 太大，不像令牌")
        raw = os.read(fd, 1025)
    finally:
        os.close(fd)
    try:
        v = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        v = ""
    if not TOKEN_SHAPE.match(v):
        raise Rejected(f"发布令牌 {f} 的内容不像 GitHub 令牌（应是一行字母、数字、下划线）")
    return v


class Cred:
    """一次真发布（或联网自检）用的凭据环境：令牌只在内存里；HOME、gh 配置目录是这一次的空临时目录。"""

    def __init__(self):
        self.gh = trusted_program(GH, "gh ")
        if not os.access(GIT_BIN, os.X_OK):
            raise Rejected(f"找不到 {GIT_BIN}")
        self.proxy = load_conf().get("proxy")
        self.token = read_token()
        self.tmp = tempfile.TemporaryDirectory(prefix="dt-publish-cred-")
        for n in ("home", "gh"):
            os.mkdir(Path(self.tmp.name) / n, 0o700)

    def env(self, token=False):
        """子进程的最小环境：不继承运行账号的任何变量（GH_*、GIT_*、代理都不带），不读全局和系统 git 配置，不交互。
        代理只用发布配置里写的本机代理。token=True 才带令牌（GH_TOKEN，主机固定 github.com，gh 配置目录是空的：
        读不到任何已有登录）。"""
        t = Path(self.tmp.name)
        e = {"PATH": SAFE_PATH, "HOME": str(t / "home"), "LANG": "en_US.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/false",
             "GH_CONFIG_DIR": str(t / "gh"), "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}
        if self.proxy:
            for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                e[k] = self.proxy
            e["NO_PROXY"] = e["no_proxy"] = "localhost,127.0.0.1,::1"
        if token:
            e.update(GH_HOST="github.com", GH_TOKEN=self.token)
        return e

    def helper(self):
        """git push 的凭据助手：先清空已有助手，再只用 gh auth git-credential（令牌从环境取，不进命令行）。"""
        return ["-c", "credential.helper=", "-c", f"credential.helper=!{shlex.quote(self.gh)} auth git-credential"]

    def scrub(self, text):
        return (text or "").replace(self.token, "***")

    def close(self):
        self.token = ""
        self.tmp.cleanup()


@contextlib.contextmanager
def credentials():
    """真发布期间把凭据环境挂到 CRED；测试后端（TEST_BACKEND）不读令牌、不换环境。"""
    global CRED
    if TEST_BACKEND:
        yield None
        return
    cred = Cred()
    CRED = cred
    try:
        yield cred
    finally:
        CRED = None
        cred.close()


def check_identity(github):
    """推之前核令牌是谁的（联网，只读）：登录名是这个仓库登记的机器账号，对仓库能写、不是管理员。
    管理员令牌能绕过 main 的规则集（hermes/baselines/dt-sandbox-github.json：只对 admin 豁免），所以拒发。"""
    if CRED is None:
        return None
    want = PUBLISH_CRED.get(github)
    if not want:
        raise Rejected(f"{github} 没有登记发布用的机器账号（只登记了 {'、'.join(PUBLISH_CRED)}）")
    r = gh("api", "user", "--jq", ".login")
    if r.returncode != 0:
        raise Rejected(f"查令牌身份失败：{r.stderr.strip()[:200]}")
    login = r.stdout.strip()
    if login != want:
        raise Rejected(f"发布令牌属于 {login or '（读不出）'}，不是登记的机器账号 {want}，不发")
    r = gh("api", f"repos/{github}", "--jq", ".permissions")
    try:
        perms = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        perms = None
    if not isinstance(perms, dict):
        raise Rejected(f"查令牌对 {github} 的权限失败：{r.stderr.strip()[:200]}")
    if perms.get("admin") is not False:
        raise Rejected(f"发布令牌对 {github} 有管理员权限（能绕过 main 的规则集），不发；换成机器账号的令牌")
    if perms.get("push") is not True:
        raise Rejected(f"发布令牌对 {github} 没有写权限，推不了分支")
    return {"login": login, "push": True, "admin": False}


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
    """推送目标的最后一道写死检查：只推 <执行方>/<单号> 形式的本单分支。"""
    if not BRANCH.match(branch or ""):
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
    """0D 之前整体关闭：不推分支、不开 PR；只认进程内注入的测试后端，而且远端必须是本地裸仓库。"""
    if REAL_PUBLISH:
        return
    if not TEST_BACKEND or not str(TEST_BACKEND.get("remote", "")).startswith("file://"):
        raise Rejected("0D 恢复门（授权收紧与发布动作的串行化、结果账本）之前受信发布整体关闭，推分支、开 PR 都不做")


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


def history_hits(mirror, base, head, repo):
    """基线到头提交之间每个提交的改动路径，加上最终差异，碰到高风险路径的都列出来；有合并提交直接拒绝。"""
    if git("rev-list", "--min-parents=2", f"{base}..{head}", cwd=mirror).stdout.split():
        raise Rejected("交接里有合并提交，不自动发布")
    commits = git("rev-list", f"{base}..{head}", cwd=mirror).stdout.split()
    if len(commits) > MAX_COMMITS:
        raise Rejected(f"交接里提交太多（{len(commits)} 个），不自动发布")
    paths = set(git("diff", "--name-only", "-z", "--no-renames", base, head, cwd=mirror).stdout.split("\0"))
    for c in commits:
        paths |= set(git("diff-tree", "-r", "--no-commit-id", "--name-only", "-z", "--no-renames", c,
                         cwd=mirror).stdout.split("\0"))
    pats = sensitive_patterns(repo)
    return sorted(f for f in paths if f and any(pt.search(f) for pt in pats))


# ---------- 发布 ----------
def publish(task_id, now=None, path=None):
    require_gate()
    require_publish_enabled()
    with credentials():                # 真发布：先读令牌、核 gh 副本，读不到就停在最前面
        return _publish(task_id, now, path)


def _publish(task_id, now, path):
    now = now or G.now_tz()
    m = read_manifest(task_id)
    with task_lock(task_id):
        gen = m["generation"]
        for action in ("push_branch", "open_pr"):            # 两个动作一起预检（审查 P2-01）
            must(task_id, action, gen, now, path)
        with G.tx(path) as con:
            row = dict(G.grant_row(con, task_id))
        if (row["repo"], row["executor"]) != (m["repo"], m["executor"]):
            raise Rejected("交接清单的仓库或执行方和凭证不一致")
        github, branch, head, base = row["github"], m["branch"], m["head"], m["base"]
        assert_not_main(branch)
        reason = check_run(m["run"], row, gen)
        if reason:
            raise Rejected(reason)
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
        hits = history_hits(mirror, base, head, row["repo"])
        if hits:
            raise Rejected(f"改动或提交历史碰到高风险路径（{'、'.join(hits[:5])}），不自动发布，交常浩")
        check_identity(github)         # 真发布：推之前核令牌是登记的机器账号、不是管理员
        remote = git("ls-remote", remote_url(github), f"refs/heads/{branch}", cwd=mirror).stdout.split()
        existing = remote[0] if remote else None
        pushed = False
        if existing != head:
            if existing and not (SHA.match(existing) and is_ancestor(mirror, existing, head)):
                raise Rejected("远端同名分支已有别的提交，不覆盖")
            must(task_id, "push_branch", gen, G.now_tz(), path)        # 推之前现读一次
            git("push", "--quiet", "--no-verify", remote_url(github), f"{head}:refs/heads/{branch}", cwd=mirror,
                auth=True)
            pushed = True
        must(task_id, "open_pr", gen, G.now_tz(), path)
        pr = open_pr(github, branch, head, row)
        out = {"task_id": task_id, "generation": gen, "github": github, "branch": branch, "head": head,
               "run": m["run"], "pushed": pushed, **pr}
        record({"what": "发布", **out})
        return out


PR_FIELDS = "number,url,state,baseRefName,headRefName,headRefOid,isCrossRepository,headRepository,headRepositoryOwner"


def pr_is_ours(p, github, branch, head):
    """已有 PR 必须正是这次的：目标本仓库 main、来源本仓库、本单分支、头提交一致（审查 P1-05）。"""
    owner, name = github.split("/")
    src_owner = ((p.get("headRepositoryOwner") or {}).get("login") or "").lower()
    src_name = ((p.get("headRepository") or {}).get("name") or "").lower()
    return (p.get("baseRefName") == "main" and p.get("headRefName") == branch and p.get("headRefOid") == head
            and p.get("isCrossRepository") is False and src_owner == owner.lower() and src_name == name.lower())


def open_pr(github, branch, head, row):
    r = gh("pr", "list", "-R", github, "--head", branch, "--state", "all", "--json", PR_FIELDS)
    if r.returncode != 0:
        raise Rejected(f"查 PR 失败：{r.stderr.strip()[:200]}")
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
        r = gh("pr", "create", "-R", github, "-B", "main", "-H", branch, "-t", f"{row['task_id']}：{first}",
               "-F", f.name, timeout=120)
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


# ---------- 自检（只读） ----------
def _readable(db, table):
    """只读打开一个 SQLite 库、读一下这张表（不建库、不升结构、不拿写锁）。"""
    import sqlite3
    import urllib.parse
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro", uri=True, timeout=5)
    try:
        con.execute(f"SELECT count(*) FROM {table}").fetchone()
    finally:
        con.close()


def _branch_selftest():
    """推送目标的写死检查自测：main、别的分支、别人的单号写法都拒，只放行 <执行方>/<单号>。"""
    for bad in ("main", "refs/heads/main", "claude/main", "agent/t-20261118-abcdef", "claude/t-20261118-abcdef/x",
                "claude/t-20261118-ABCDEF", "", None):
        try:
            assert_not_main(bad)
        except Rejected:
            continue
        raise Rejected(f"推送目标检查放过了 {bad!r}")
    for good in ("claude/t-20261118-abcdef", "codex/t-20261118-abcdef"):
        assert_not_main(good)


def doctor(online=False):
    """真发布要的东西齐不齐（只读；不读出令牌内容、不推、不开 PR）。返回 {"ok": 全过没有, "checks": [...]}。
    每项 {"name", "ok", "detail"}；--online 多两项联网的只读查询（令牌身份、读得到仓库的 main）。"""
    checks = []

    def add(name, fn):
        try:
            detail = fn()
            checks.append({"name": name, "ok": True, "detail": detail or "好"})
        except (Rejected, G.Rejected) as e:
            checks.append({"name": name, "ok": False, "detail": str(e)})
        except Exception as e:                        # 自检不因为某一项意外出错就整体退出
            checks.append({"name": name, "ok": False, "detail": f"{type(e).__name__}：{str(e)[:200]}"})

    def need(cond, why):
        if not cond:
            raise Rejected(why)

    def token():
        read_token()
        d = token_file().parent
        st = os.lstat(d)
        need(stat.S_ISDIR(st.st_mode) and st.st_uid == os.geteuid() and not st.st_mode & 0o077 and not has_acl(d),
             f"{d} 必须是本账号自己的目录、权限 700、没有放行别人的 ACL（不认链接）")
        return f"{token_file()}：属本账号、600、格式像令牌（内容不显示）"

    def outbox_ok():
        need(outbox().is_dir() and os.access(outbox(), os.R_OK | os.X_OK), f"读不了交接目录 {outbox()}")
        return str(outbox())

    def grants():
        need(G.db_path().is_file(), f"凭证库 {G.db_path()} 不在（派单网关还没在本账号里建过凭证）")
        _readable(G.db_path(), "grants")
        return str(G.db_path())

    def hub():
        need(hub_db().is_file(), f"中台库 {hub_db()} 不在")
        _readable(hub_db(), "runs")
        return str(hub_db())

    add("运行账号是 dt-gate", lambda: require_gate() or GATE_USER)
    add("发布令牌文件", token)
    add("gh 副本只有 root 能改", lambda: trusted_program(GH, "gh "))
    add("发布配置（代理）", lambda: f"走本机代理 {load_conf().get('proxy')}" if load_conf().get("proxy")
        else "没配代理，直连 github.com")
    add("git", lambda: need(os.access(GIT_BIN, os.X_OK), f"找不到 {GIT_BIN}") or GIT_BIN)
    add("交接目录读得到", outbox_ok)
    add("凭证库", grants)
    add("中台库（只读）", hub)
    add("推送目标只能是 <执行方>/<单号>", lambda: _branch_selftest() or "main 和别的分支都被拒")
    add("登记的仓库和机器账号", lambda: "；".join(f"{k} → {v}" for k, v in PUBLISH_CRED.items()))
    add("高风险路径规则读得到", lambda: f"{len(sensitive_patterns('dt-sandbox'))} 条")
    switch = {"name": "真发布开关", "ok": True,
              "detail": "开着" if REAL_PUBLISH else "关着（推分支、开 PR 都不做）"}
    if online:
        def with_cred(fn):
            def run():
                global CRED
                cred = Cred()
                CRED = cred
                try:
                    return fn()
                finally:
                    CRED = None
                    cred.close()
            return run

        def identity():
            out = []
            for github in PUBLISH_CRED:
                who = check_identity(github)
                out.append(f"{github}：{who['login']}，能写、不是管理员")
            return "；".join(out)

        def read_main():
            out = []
            for github in PUBLISH_CRED:
                r = git("ls-remote", remote_url(github), "refs/heads/main").stdout.split()
                need(r and SHA.match(r[0]), f"读不到 {github} 的 main")
                out.append(f"{github} main {r[0][:12]}")
            return "；".join(out)

        add("联网：令牌身份", with_cred(identity))
        add("联网：读得到仓库（不带令牌）", with_cred(read_main))
    checks.append(switch)
    return {"ok": all(c["ok"] for c in checks), "online": bool(online), "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser(description="受信发布（最小版）：取 dt-worker 的交接包，核凭证后推非 main 分支、开 PR")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish")
    p.add_argument("--task", required=True)
    sub.add_parser("scan")
    p = sub.add_parser("doctor")
    p.add_argument("--online", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "doctor":
        out = doctor(a.online)
        for c in out["checks"]:
            print(f"[{'通过' if c['ok'] else '不过'}] {c['name']}：{c['detail']}")
        print("自检全过" if out["ok"] else "自检有不过的项，先处理再谈打开真发布")
        return 0 if out["ok"] else 1
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
