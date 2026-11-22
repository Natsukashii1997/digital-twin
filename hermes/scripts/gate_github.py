#!/usr/bin/env python3
"""dt-gate 的 GitHub 发布凭据与真发布开关（ADR-015 批 0E-4 最小版）。

受信发布（autoloop_publish.py）在 dt-gate 账号里真推分支、真开 PR，用的是机器账号 shanhao1997（#132）的令牌：
  - 令牌是谁的：机器账号 shanhao1997，dt-sandbox 的协作者（push 有、admin 没有），只能推非 main 分支、开 PR，合并不了 main
    （dt-sandbox 规则集 main-merge-reserved-to-admin 只对管理员豁免，hermes/baselines/dt-sandbox-github.json；所以管理员的令牌
    一律拒发——常浩名下的令牌能推 main）。常浩用这个账号建经典令牌，只勾 public_repo。
  - 放在哪：dt-gate 自己家目录的 ~/.dt/github-publish.token（目录 700、文件 600、属 dt-gate、没有放行别人的 ACL、不跟随链接），
    由 root 入口 `dt-gate-hub put-publish-token --confirm` 以 dt-gate 身份写入；主账号、秘书账号、工人账号都读不到。
    家目录按有效用户的系统账号取，不认环境变量 HOME。
  - gh 用 root 装的副本 GATE/bin/gh（stage --gh 复制、记 SHA-256）：Homebrew 的 gh 属主账号、主账号能换，dt-gate 不跑它。
    副本本身和从 / 起每一级目录只有 root 能改（属主、权限位、ACL），路径上不许有链接，否则不用。

**真发布开关**是 root 写的文件 GATE/publish.json（常浩在 doctor --online 全过后亲手 `sudo dt-gate-hub publish on --confirm`；
`publish off` 删掉它，关永远允许）：
    {"enabled": true, "repos": ["dt-sandbox"], "proxy": null 或 "http://127.0.0.1:<端口>", "at": "…", "by": "…"}
  可选 "login"（令牌应属的账号，默认 shanhao1997）。读的时候核文件和从 / 起每一级目录只有 root 能改、不是链接；没有文件、
  写法不对、多了不认得的字段、enabled 不是 true，一律当关着。repos 只认代码写死的 REPOS 里的（现在只有 dt-sandbox），名单外的不认。
  合并 0E-4 不等于放开：开关文件不在就不发。

**子进程最小环境**（env()）：不继承调用方的任何变量（代理、GIT_*、GH_* 一概不带），HOME 和 gh 的配置目录是 dt-gate 自己的
空目录，git 不读全局 / 系统配置、不弹提示；代理只从开关文件取（只认 http://127.0.0.1:<端口>，HTTPS_PROXY / HTTP_PROXY 大小写
两套），不从环境变量取，没设就直连（dt-gate 直连 github.com 11-22 实测通）。令牌只给推送和 gh 两类子进程：凭据助手只对
https://github.com 从环境变量 GH_TOKEN 取令牌，令牌不进命令行、不写配置；取包、核历史、取 main、查远端分支都用不带令牌的环境。
git / gh 的输出进异常和日志前先 mask()（像令牌的串一律换成 ***）。

**真发布前核对**（ready()）：开关开着、仓库在开关和 REPOS 里、和名册的 GitHub 对得上 → 令牌文件 → gh 副本 →
`gh api -i user` 正是登记的账号、经典令牌只勾了 public_repo（多勾 repo、workflow、delete_repo 都拒）→ 对这个仓库
admin 严格为 false、push 严格为 true（缺项、null 都拒）。任何一步不对 → NotReady。
对账回读（autoloop_exits）用 readback_env()：不要求开关开着（结果不明之后关了开关也要能对账），代理照开关文件读得到的用。

已核实（11-22，常浩的账号只读查）：常浩名下的仓库里 shanhao1997 只对 dt-sandbox 有 write，其余都是公开仓库人人有的 read；
经典令牌勾 public_repo 理论上能写这个账号有写权限的所有公开仓库，所以机器账号不要再被加成别的仓库的协作者。

剩余风险（记在这里）：令牌在 dt-gate 进程的环境变量里，同一账号的进程看得到（ADR-015 的隔离单位就是账号）；
设了代理时，127.0.0.1 上那个端口不归 dt-gate 管（加速器在主账号里；没开时任何本机用户都能占住）：令牌有 TLS 保护，
但代理能看到连接、能掐断推送让它记成结果不明、冻结这张单，所以默认直连；`publish off` 删掉开关文件后对账也跟着直连；
gh 副本是 stage 那一刻从主账号的 Homebrew 复制的，信任「装的那一刻主账号可信」，Homebrew 的 gh 没有官方签名可核；
dt-gate 直连只测过 github.com，api.github.com 由 doctor --online 第一次跑时一起看。

  gate_github.py check --repo dt-sandbox [--proxy URL]   核令牌、gh 副本、账号和权限，只打印账号名和权限（不要求开关）
  gate_github.py doctor --repo dt-sandbox [--online] [--proxy URL]   只读自检，逐行 [通过] / [不过]
不调用 AI，除了建 gh 的空配置目录不写任何东西。
"""
import argparse
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_grants as G  # noqa: E402

LOGIN = "shanhao1997"                 # 默认登记的机器账号（#132）；开关文件可写 "login" 改
REPOS = ("dt-sandbox",)               # 代码写死的上限：开关文件的 repos 只认这里面的。前提：仓库公开（取 main、对账查分支不带令牌）
GATE = "/Library/Application Support/DigitalTwin/gate"
GH = GATE + "/bin/gh"                 # root 装的 gh 副本（dt_gate_hub.py stage --gh）
SWITCH = GATE + "/publish.json"       # root 写的真发布开关；测试里在进程内替换，不经环境变量
TRUSTED_UIDS = (0,)                   # gh 副本、开关文件和它们往上的目录只认这些属主；测试里在进程内替换
GIT_BIN = "/usr/bin/git"
SAFE_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
TOKEN_RE = re.compile(r"^(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,255})$")
TOKEN_ANY = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})")
PROXY_RE = re.compile(r"^http://127\.0\.0\.1:([1-9]\d{0,4})$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
SWITCH_KEYS = {"enabled", "repos", "proxy", "at", "by", "login"}
MAX_TOKEN = 512
MAX_SWITCH = 4096
# git 的凭据助手：只答 get，用户名固定、密码从环境变量 GH_TOKEN 取（令牌不进配置、不进命令行）
HELPER = '!f() { test "$1" = get && printf "username=x-access-token\\npassword=%s\\n" "$GH_TOKEN"; }; f'


class NotReady(Exception):
    """发布凭据或开关没就绪、对不上：说人话的原因（不含令牌）。"""


def mask(text):
    """像 GitHub 令牌的串一律换成 ***：git / gh 的输出进异常、日志、账本前都过一遍。"""
    return TOKEN_ANY.sub("***", text or "")


def home():
    try:
        return Path(pwd.getpwuid(os.geteuid()).pw_dir)          # 不认 HOME
    except KeyError:
        raise NotReady("运行账号查不到家目录")


def token_path():
    return home() / ".dt/github-publish.token"


def private_dir():
    """HOME、gh 配置目录的上级：dt-gate 自己的、只有自己能进。"""
    return home() / "Library/Application Support/DigitalTwin/autoloop/gh-publish"


# ---------- 文件和路径的可信核对 ----------
def has_acl(path):
    """macOS 上有没有放行别人的 ACL（ls -led 第二行起出现 allow）；看不了按「有」算。别的系统没有这种 ACL。"""
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(["/bin/ls", "-led", str(path)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    return r.returncode != 0 or any(" allow " in line for line in r.stdout.splitlines()[1:])


def _private(st, path, what, is_kind):
    if not is_kind(st.st_mode):
        raise NotReady(f"{what}不是预期的类型（不能是链接）")
    if st.st_uid != os.geteuid():
        raise NotReady(f"{what}不属于运行账号")
    if st.st_mode & 0o077 or has_acl(path):
        raise NotReady(f"{what}的权限太宽（组和其他人不能有任何权限、不能有放行别人的 ACL）")


def root_only(path, what):
    """path 本身是普通文件，它和从 / 起每一级目录：不是链接、属主在 TRUSTED_UIDS、组和其他人不能写（带粘滞位的上级目录除外）、
    没有放行别人的 ACL。别人能换掉它就能拿到令牌（gh）或打开真发布、改走的代理（开关）。"""
    p = Path(path)
    try:
        st = os.lstat(p)
    except OSError:
        raise NotReady(f"没有{what}（{p}）")
    if not stat.S_ISREG(st.st_mode) or st.st_uid not in TRUSTED_UIDS or st.st_mode & 0o022 or has_acl(p):
        raise NotReady(f"{what}不是 root 所有、别人改不了的普通文件（属主、权限位、ACL 或是链接）")
    d = p.parent
    while True:
        try:
            st = os.lstat(d)
        except OSError:
            raise NotReady(f"{what}所在目录读不了：{d}")
        if (not stat.S_ISDIR(st.st_mode) or st.st_uid not in TRUSTED_UIDS
                or (st.st_mode & 0o022 and not st.st_mode & stat.S_ISVTX) or has_acl(d)):
            raise NotReady(f"{what}往上的目录不是只有 root 能改：{d}")
        if d == d.parent:
            return st
        d = d.parent


def _read_nofollow(p, limit):
    fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        return os.fstat(fd), os.read(fd, limit + 1)
    finally:
        os.close(fd)


def read_token():
    """读令牌文件：目录 700、文件 600、都属运行账号、没有 ACL、不跟随链接；内容只能是一个 GitHub 令牌。"""
    p = token_path()
    try:
        _private(os.lstat(p.parent), p.parent, "令牌目录 ~/.dt", stat.S_ISDIR)
        st, data = _read_nofollow(p, MAX_TOKEN)
    except FileNotFoundError:
        raise NotReady("还没放发布令牌（~/.dt/github-publish.token），请常浩跑 dt-gate-hub put-publish-token --confirm")
    except OSError as e:
        raise NotReady(f"令牌文件打不开（{type(e).__name__}）")
    _private(st, p, "令牌文件", stat.S_ISREG)
    try:
        tok = data.decode("ascii").strip()
    except UnicodeDecodeError:
        tok = ""
    if len(data) > MAX_TOKEN or not TOKEN_RE.fullmatch(tok):
        raise NotReady("令牌文件的内容不是一个 GitHub 令牌")
    return tok


def check_gh():
    root_only(GH, "root 装的 gh 副本")
    if not os.stat(GH).st_mode & 0o100:
        raise NotReady("gh 副本不可执行")
    return GH


def valid_proxy(proxy):
    m = PROXY_RE.fullmatch(proxy) if isinstance(proxy, str) else None
    if not (m and 1 <= int(m.group(1)) <= 65535):
        raise NotReady("代理只认本机 http://127.0.0.1:<端口>")
    return proxy


def read_switch(require_on=True):
    """真发布开关（root 写的 GATE/publish.json）。require_on=True 时关着就 NotReady；
    返回 {"enabled", "repos"（只留 REPOS 里的）, "proxy", "login", "at", "by"}。"""
    if not os.path.lexists(SWITCH):
        raise NotReady("真发布开关关着（没有开关文件，常浩 doctor --online 全过后用 dt-gate-hub publish on --confirm 打开）")
    root_only(SWITCH, "真发布开关文件")
    try:
        st, data = _read_nofollow(SWITCH, MAX_SWITCH)
        sw = json.loads(data.decode("utf-8")) if len(data) <= MAX_SWITCH else None
    except (OSError, ValueError):
        sw = None
    if not isinstance(sw, dict) or set(sw) - SWITCH_KEYS:
        raise NotReady("真发布开关文件写法不对，当关着")
    repos = sw.get("repos")
    if not isinstance(repos, list) or not all(isinstance(r, str) for r in repos):
        raise NotReady("真发布开关文件的 repos 不是仓库名列表，当关着")
    proxy = sw.get("proxy")
    if proxy is not None:
        try:
            valid_proxy(proxy)
        except NotReady:
            raise NotReady("真发布开关文件的 proxy 只认 http://127.0.0.1:<端口>，当关着")
    login = sw.get("login", LOGIN)
    if not isinstance(login, str) or not LOGIN_RE.fullmatch(login):
        raise NotReady("真发布开关文件的 login 不是 GitHub 账号名，当关着")
    out = {"enabled": sw.get("enabled") is True, "repos": [r for r in repos if r in REPOS], "proxy": proxy,
           "login": login, "at": sw.get("at"), "by": sw.get("by")}
    if require_on and not out["enabled"]:
        raise NotReady("真发布开关关着（enabled 不是 true）")
    if require_on and not out["repos"]:
        raise NotReady(f"真发布开关里没有放开的仓库（只认 {'、'.join(REPOS)}）")
    return out


def switch_login():
    """令牌应属的账号：开关文件里写了 login 就用它（不管开没开），否则默认 LOGIN。"""
    try:
        return read_switch(require_on=False)["login"]
    except NotReady:
        return LOGIN


def switch_proxy():
    """对账回读用的代理：开关文件读得到、写法对就用它的 proxy（不管开没开），否则直连。"""
    try:
        return read_switch(require_on=False)["proxy"]
    except NotReady:
        return None


# ---------- 子进程环境 ----------
def env(token=None, proxy=None):
    """子进程的最小环境：不继承调用方任何变量；HOME、gh 配置目录是 dt-gate 自己的空目录；
    token 给了才带令牌和凭据助手（只给推送和 gh），proxy 给了才走本机代理。"""
    base = private_dir()
    h, cfg = base / "home", base / "config"
    for d in (base, h, cfg):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    e = {"HOME": str(h), "PATH": SAFE_PATH, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
         "GH_CONFIG_DIR": str(cfg), "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1",
         "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1", "GH_SPINNER_DISABLED": "1", "NO_COLOR": "1",
         "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/false", "GIT_CONFIG_GLOBAL": "/dev/null",
         "GIT_CONFIG_NOSYSTEM": "1"}
    if proxy:
        valid_proxy(proxy)
        e.update({"HTTPS_PROXY": proxy, "https_proxy": proxy, "HTTP_PROXY": proxy, "http_proxy": proxy,
                  "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1"})
    if token:
        e.update({"GH_TOKEN": token, "GH_HOST": "github.com", "GIT_CONFIG_COUNT": "2",
                  "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": "",
                  "GIT_CONFIG_KEY_1": "credential.https://github.com.helper", "GIT_CONFIG_VALUE_1": HELPER})
    return e


def _gh(args, e, what):
    """跑 gh（工作目录是 dt-gate 的空家目录：sudo -u 沿用调用者的当前目录，dt-gate 可能读不了）。输出先打码。"""
    try:
        r = subprocess.run([GH, *args], env=e, cwd=e["HOME"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as x:
        raise NotReady(f"{what}：gh 跑不起来或超时（{type(x).__name__}）")
    if r.returncode != 0:
        raise NotReady(f"{what}失败：{mask(r.stderr).strip()[:200]}")
    return r.stdout


def _json(text, what):
    try:
        return json.loads(text)
    except ValueError:
        raise NotReady(f"{what}：gh 的输出读不懂")


def _scopes(head):
    """gh api -i 的响应头里经典令牌的权限范围（X-OAuth-Scopes）；没有这一行返回 None（细粒度令牌没有）。"""
    for line in head.splitlines()[1:]:
        k, _, v = line.partition(":")
        if k.strip().lower() == "x-oauth-scopes":
            return {s.strip() for s in v.split(",") if s.strip()}
    return None


def identity(github, e):
    """令牌是谁的、勾了哪些权限范围、对这个仓库有什么权限（gh api -i user、gh api repos/<仓库>，都只读）。"""
    out = _gh(["api", "-i", "user"], e, "查令牌的账号")
    head, _, body = out.replace("\r\n", "\n").partition("\n\n")
    me = _json(body, "查令牌的账号")
    repo = _json(_gh(["api", f"repos/{github}"], e, f"查 {github} 的权限"), f"查 {github} 的权限")
    perm = repo.get("permissions") if isinstance(repo, dict) else None
    perm = perm if isinstance(perm, dict) else {}
    return {"login": me.get("login") if isinstance(me, dict) else None, "scopes": _scopes(head),
            "fine_grained": str(e.get("GH_TOKEN", "")).startswith("github_pat_"),
            "admin": perm.get("admin"), "push": perm.get("push"), "maintain": perm.get("maintain")}


def require_identity(who, github, login):
    if who["login"] != login:
        raise NotReady(f"发布令牌不是登记的账号 {login} 的（是 {who['login']}）")
    if who["scopes"] is None and not who["fine_grained"]:
        raise NotReady("读不出经典令牌勾了哪些权限范围（响应里没有 X-OAuth-Scopes）")
    if who["scopes"] is not None and not who["scopes"] <= {"public_repo"}:
        raise NotReady(f"发布令牌多勾了权限（只许 public_repo，现在：{'、'.join(sorted(who['scopes']))}），请重建令牌")
    if not isinstance(who["admin"], bool) or not isinstance(who["push"], bool):
        raise NotReady(f"读不出 {login} 对 {github} 的权限（admin={who['admin']}、push={who['push']}）")
    if who["admin"] is not False or who["push"] is not True or who["maintain"] is True:
        raise NotReady(f"{login} 对 {github} 的权限不对（要 admin=false、push=true，"
                       f"现在 admin={who['admin']}、push={who['push']}；管理员能推 main，拒发）")
    return who


def _roster(repo, github):
    if G.allowed_repos().get(repo) != github:
        raise NotReady(f"{repo} 在名册里的 GitHub 仓库不是 {github}")


def ready(repo, github):
    """真发布前核对：先过开关，再核仓库、令牌、gh 副本、账号和权限。返回 (不带令牌的环境, 带令牌的环境)。"""
    sw = read_switch()
    if repo not in sw["repos"]:
        raise NotReady(f"真发布开关没有放开 {repo}（放开的：{'、'.join(sw['repos'])}）")
    _roster(repo, github)
    token = read_token()
    check_gh()
    authed = env(token, sw["proxy"])
    require_identity(identity(github, authed), github, sw["login"])
    return env(None, sw["proxy"]), authed


def readback_env(auth):
    """对账回读用（不核身份、不要求开关开着）：auth=True 带令牌给 gh（先核 gh 副本），False 不带令牌给 git ls-remote。"""
    proxy = switch_proxy()
    if not auth:
        return env(None, proxy)
    token = read_token()
    check_gh()
    return env(token, proxy)


# ---------- 自检 ----------
def _readable(db, table):
    """只读打开一个 SQLite 库、读一下这张表（不建库、不升结构、不拿写锁）。"""
    import sqlite3
    import urllib.parse
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro", uri=True, timeout=5)
    try:
        con.execute(f"SELECT count(*) FROM {table}").fetchone()
    finally:
        con.close()


def doctor(repo, online=False, proxy=None):
    """真发布要的东西齐不齐（只读：不显示令牌、不推、不开 PR）。返回 {"ok", "checks": [{"name", "ok", "detail"}]}。
    proxy 不给就用开关文件里的；--online 时直连和代理（有的话）两条路都测，要走的那条必须通，另一条只报告。"""
    import autoloop_publish as P                 # 放函数里：受信发布导入本模块
    checks = []

    def add(name, fn, required=True):
        try:
            detail, ok = fn() or "好", True
        except (NotReady, P.Rejected, G.Rejected) as e:
            detail, ok = mask(str(e)), False
        except Exception as e:                    # 自检不因为某一项意外出错就整体退出
            detail, ok = f"{type(e).__name__}：{mask(str(e))[:200]}", False
        if not ok and not required:
            detail, ok = f"{detail}（不走这条路，只报告）", True
        checks.append({"name": name, "ok": ok, "detail": detail})
        return ok

    def need(cond, why):
        if not cond:
            raise NotReady(why)

    def switch():
        if not os.path.lexists(SWITCH):
            return "关着（没有开关文件）"
        sw = read_switch(require_on=False)
        return (f"{'开着' if sw['enabled'] else '关着'}，放开 {'、'.join(sw['repos']) or '（无）'}，"
                f"代理 {sw['proxy'] or '无（直连）'}，账号 {sw['login']}，{sw['by'] or '?'} 于 {sw['at'] or '?'}")

    def proxy_used():
        nonlocal proxy
        if proxy is None:
            proxy = switch_proxy()
        if proxy:
            valid_proxy(proxy)
            return f"走本机代理 {proxy}"
        return "不走代理，直连 github.com"

    def token():
        read_token()
        return f"{token_path()}：目录 700、文件 600、属本账号、格式像令牌（内容不显示）"

    def branch_selftest():
        for bad in ("main", "refs/heads/main", "agent/t-20261118-abcdef", "claude/T-20261118-ABCDEF", "claude/main"):
            try:
                P.assert_not_main(bad)
            except P.Rejected:
                continue
            raise NotReady(f"推送目标检查放过了 {bad}")
        P.assert_not_main("claude/t-20261118-abcdef")
        return "main 和别的分支都被拒，只放行 <执行方>/<单号>"

    try:
        github = G.allowed_repos().get(repo)
    except (OSError, ValueError) as e:
        github = None
        checks.append({"name": "名册读得到", "ok": False, "detail": f"{type(e).__name__}"})
    login = switch_login()
    add("运行账号是 dt-gate", lambda: P.require_gate() or P.GATE_USER)
    add("登记的仓库", lambda: need(repo in REPOS and github, f"{repo} 不在真发布名单 {'、'.join(REPOS)} 或名册里")
        or f"{repo} → {github}")
    add("发布令牌文件", token)
    add("gh 副本只有 root 能改", check_gh)
    add("真发布开关", switch)
    add("代理", proxy_used)
    add("git", lambda: need(os.access(GIT_BIN, os.X_OK), f"找不到 {GIT_BIN}") or GIT_BIN)
    add("交接目录读得到", lambda: need(P.outbox().is_dir() and os.access(P.outbox(), os.R_OK | os.X_OK),
                                f"读不了交接目录 {P.outbox()}") or str(P.outbox()))
    add("凭证库（只读）", lambda: _readable(G.db_path(), "grants") or str(G.db_path()))
    add("中台库（只读）", lambda: _readable(P.hub_db(), "runs") or str(P.hub_db()))
    add("推送目标只能是 <执行方>/<单号>", branch_selftest)
    add("高风险路径规则读得到", lambda: f"{len(P.sensitive_patterns(repo))} 条")
    if online and github:
        routes = [(None, not proxy)] + ([(proxy, True)] if proxy else [])
        for px, required in routes:
            way = f"经代理 {px}" if px else "直连"

            def who(px=px):
                w = require_identity(identity(github, env(read_token(), px)), github, login)
                return f"{w['login']}，push=true、admin=false"

            def read_main(px=px):
                e = env(None, px)
                r = subprocess.run([GIT_BIN, "ls-remote", f"https://github.com/{github}.git", "refs/heads/main"],
                                   env=e, cwd=e["HOME"], capture_output=True, text=True, timeout=60)
                out = r.stdout.split()
                need(r.returncode == 0 and out and re.fullmatch(r"[0-9a-f]{40}", out[0]),
                     f"读不到 {github} 的 main：{mask(r.stderr).strip()[:200]}")
                return f"main {out[0][:12]}"

            add(f"联网（{way}）：令牌身份和权限", who, required)
            add(f"联网（{way}）：不带令牌读得到 main", read_main, required)
    return {"ok": all(c["ok"] for c in checks), "online": bool(online), "checks": checks}


def main(argv=None):
    ap = argparse.ArgumentParser(description="dt-gate 的 GitHub 发布凭据：核对令牌、gh 副本、机器账号、权限和真发布开关")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check", help="核令牌、gh 副本、账号和权限（不要求开关）")
    p.add_argument("--repo", required=True, help="名册仓库名，如 dt-sandbox")
    p.add_argument("--proxy")
    p = sub.add_parser("doctor", help="只读自检")
    p.add_argument("--repo", required=True)
    p.add_argument("--online", action="store_true")
    p.add_argument("--proxy")
    a = ap.parse_args(argv)
    if a.cmd == "doctor":
        out = doctor(a.repo, a.online, a.proxy)
        for c in out["checks"]:
            print(f"[{'通过' if c['ok'] else '不过'}] {c['name']}：{c['detail']}")
        print("自检全过" if out["ok"] else "自检有不过的项，先处理再谈打开真发布")
        return 0 if out["ok"] else 1
    out = {"repo": a.repo, "github": None}
    try:
        try:
            github = out["github"] = G.allowed_repos().get(a.repo)
        except (OSError, ValueError) as e:
            raise NotReady(f"名册读不了（{type(e).__name__}）")
        if a.repo not in REPOS or not github:
            raise NotReady(f"{a.repo} 不在真发布名单 {'、'.join(REPOS)} 或名册里")
        proxy = valid_proxy(a.proxy) if a.proxy else switch_proxy()
        token = read_token()
        check_gh()
        who = identity(github, env(token, proxy))
        out.update(login=who["login"], admin=who["admin"], push=who["push"],
                   scopes=sorted(who["scopes"]) if who["scopes"] is not None else None)
        require_identity(who, github, switch_login())
        out["ok"] = True
    except NotReady as x:
        out.update(ok=False, reason=mask(str(x)))
    print(json.dumps(out, ensure_ascii=False))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
