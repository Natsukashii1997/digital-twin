"""测试统一隔离（#212）：hermes/scripts/tests 下的测试碰不到本机正式环境。

来由：2026-11-17 0B-2 的「故意改坏」让锁测试真的跑了 install，装进本机正式中台并签出正式钥匙；10-27 假模型名写进正式
凭证状态（全局坑 G-13）；11-11 核查时覆盖正式配置 32 秒。都是测试只隔离了一部分、其余落到默认的正式路径。

怎么生效（不靠每个测试作者记得）：
- 每个测试模块的第一句是 `import _isolation`（test_isolation_guard.py 扫描整个目录，漏写、写在别的 import 后面都判失败）。
- 按仓库测试命令 `python3 -B -m unittest discover -s hermes/scripts/tests` 跑时，discover 按名字排序扫描，这个包
  （以 _ 开头）排在所有 test_*.py 前面先被导入，所以哪怕某个模块漏了第一句，第一个测试模块导入之前隔离也已生效。
- 单跑一个文件（`-p test_x.py` 或直接 `python3 test_x.py`）靠第一句。

导入时（整个进程只做一次；子进程继承环境变量和 PATH）：
1. 清掉可能指向正式环境的环境变量（前缀见 SCRUB_PREFIXES，KEEP 里的测试开关除外），再把 HOME、XDG_*、
   AGENT_AUTOLOOP_HOME、HERMES_HOME、CODEX_HOME、CLAUDE_CONFIG_DIR 指到本次运行的临时目录（AGENT_AUTOLOOP_HOME 特意不放在
   临时家目录下，和默认的 ~/.agent-autoloop 不同，「--target 和实际目录不一致」这类检查才测得到）；默认路径不在家目录下的
   DT_GRANTS_STATUS、DT_GRANTS_TASKS、DT_GATE_SHARED（默认 /Users/Shared/dt-gate/…）、DT_GATE_ROOT、DT_GATE_LOGS、
   DT_GATE_DAEMONS（0E-2 搬家脚本的 root 目录）、DT_GATE_LAUNCHERS（工人启动脚本）、DT_PF_DIR / DT_PF_STATUS（门卫目录和状态）、
   DT_PUBLISH_OUTBOX
   （默认 /Users/dt-worker/dt-outbox）也指过去，DT_HUB 指到不监听的 127.0.0.1:9。
   要读真正的正式目录（只读核对、上游源码）用 REAL_HOME（按系统账号取），不用 ~。
   git 用临时全局配置（固定的测试身份、默认分支 main），不读本机 ~/.gitconfig。
2. 默认禁网：本进程里连非回环地址、解析非本机域名、往非回环地址发 UDP 一律抛 BlockedNetwork（OSError 子类，
   被测脚本按「连不上」处理）；回环放行（测试自己起的假服务、临时中台要用），但本机正式服务的端口（PROD_PORTS）照样拦。
   子进程拿到指向不监听端口的 HTTP(S)_PROXY / ALL_PROXY（NO_PROXY 只放回环），守规矩的 HTTP 客户端出不去。
3. PATH 最前面放一组拦截桩（STUB_COMMANDS：launchctl、security、lark-cli、hermes、claude、codex、gh 等），
   调到就退出码 97、不做任何事；测试自己把假命令目录放在 PATH 更前面或用 DT_*_CLI 指定的，照常优先。

拦下的命令、连接在进程退出时汇总打到 stderr。剩余缺口：测试自己给子进程拼一个不含这些变量的全新环境
（如 PATH 只写 /usr/bin:/bin）时，桩和代理对那个子进程不起作用；反向验证时另用 prod_snapshot.py 前后核对正式目录。
"""
import atexit
import ipaddress
import os
import pwd
import shutil
import socket
import sys
import tempfile
from pathlib import Path

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)     # 按系统账号取，不信环境变量 HOME
SCRUB_PREFIXES = ("DT_", "HERMES_", "AGENT_AUTOLOOP", "AGENT_KIT_", "FEISHU_", "LARK_", "OPENCLAW_", "MULTICA_",
                  "TIDINGS_", "RELAY_", "ORCA_", "SKILL_HEALTH_", "MY_SKILLS_", "GH_", "GITHUB_TOKEN", "GIT_CONFIG",
                  "ANTHROPIC_", "OPENAI_", "CODEX_", "CLAUDE_", "XDG_")
KEEP = {"DT_HUB_E2E", "DT_AUTOLOOP_CLI"}                # 跑测试的人给的开关，不指向正式数据
PROD_PORTS = {3350, 3352, 3353, 5433, 8085, 8086, 8090}   # 中台、只读窗口、秘书窄窗口、本机数据库、加速器控制口 / 代理、工人过滤代理
DEAD = "http://127.0.0.1:9"
STUB_COMMANDS = ("launchctl", "security", "sudo", "crontab", "osascript", "pfctl", "lark-cli", "hermes", "claude",
                 "codex", "gh", "multica", "docker")
_PARENTS = Path(__file__).resolve().parents
# hermes/scripts 和仓库根的 ops；守护测试把本包拷到浅层临时目录（CI 的 /tmp/tmpXXX）时上溯不到四层，那里也没有被测脚本
SCRIPT_DIRS = [_PARENTS[2], _PARENTS[4] / "ops"] if len(_PARENTS) > 4 else []


class BlockedNetwork(OSError):
    """测试隔离拦下的对外连接。"""


def _script_names():
    names = set()
    for d in SCRIPT_DIRS:
        names.update(p.stem for p in d.glob("*.py"))
    return names


# 隔离生效前已经导入的被测脚本 / 测试模块（守护测试据此判「先隔离、后导入」）
PRELOADED_SCRIPTS = sorted(_script_names() & set(sys.modules))
PRELOADED_TESTS = sorted(m for m in sys.modules if m.startswith("test_"))

ROOT = Path(tempfile.mkdtemp(prefix="dt-test-iso-")).resolve()
HOME = ROOT / "home"
BIN = ROOT / "bin"
BLOCKED_LOG = ROOT / "blocked.log"
_blocked = []


def isolated_env():
    """本次运行的隔离环境变量（只含要设的；清理在 _apply 里做）。"""
    return {
        "HOME": str(HOME), "XDG_CONFIG_HOME": str(HOME / ".config"), "XDG_DATA_HOME": str(HOME / ".local/share"),
        "XDG_CACHE_HOME": str(HOME / ".cache"), "XDG_STATE_HOME": str(HOME / ".local/state"),
        "AGENT_AUTOLOOP_HOME": str(ROOT / "agent-autoloop"), "HERMES_HOME": str(HOME / ".hermes"),
        "CODEX_HOME": str(HOME / ".codex"), "CLAUDE_CONFIG_DIR": str(HOME / ".claude"),
        "DT_GRANTS_STATUS": str(ROOT / "shared/dt-gate/grants/status.json"),
        "DT_GRANTS_TASKS": str(ROOT / "shared/dt-gate/tasks"),
        "DT_GATE_SHARED": str(ROOT / "shared/dt-gate"),
        "DT_GATE_ROOT": str(ROOT / "library/gate"), "DT_GATE_LOGS": str(ROOT / "library/logs/gate"),
        "DT_GATE_DAEMONS": str(ROOT / "library/LaunchDaemons"),
        "DT_GATE_LAUNCHERS": str(ROOT / "shared/dt-autoloop/bin"),
        "DT_PF_DIR": str(ROOT / "library/DigitalTwin"),
        "DT_PF_STATUS": str(ROOT / "library/DigitalTwin/pf-dt-worker.status"),
        "DT_PUBLISH_OUTBOX": str(ROOT / "users/dt-worker/dt-outbox"),
        "DT_HUB": DEAD,
        "HTTP_PROXY": DEAD, "HTTPS_PROXY": DEAD, "ALL_PROXY": DEAD, "http_proxy": DEAD, "https_proxy": DEAD,
        "all_proxy": DEAD, "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
        "DT_TEST_ISOLATION": str(ROOT),
        "DT_NET_WAIT_SECONDS": "0",   # 定时任务入口的网络闸门（netwait，P9）默认不等不探；测等待的用例自己打开
    }


def _write_stubs():
    BIN.mkdir(parents=True)
    for name in STUB_COMMANDS:
        p = BIN / name
        p.write_text("#!/bin/sh\n"
                     f'echo "[测试隔离] 拦下 {name} $*" >> "{BLOCKED_LOG}"\n'
                     f'echo "[测试隔离] 测试里不许调本机真实的 {name}（hermes/scripts/tests/_isolation）" >&2\n'
                     "exit 97\n", encoding="utf-8")
        p.chmod(0o755)


def _write_gitconfig():
    HOME.mkdir(parents=True)
    (HOME / ".gitconfig").write_text("[user]\n\tname = dt-test\n\temail = dt-test@example.invalid\n"
                                     "[init]\n\tdefaultBranch = main\n", encoding="utf-8")


def _apply():
    for k in list(os.environ):
        if k.startswith(SCRUB_PREFIXES) and k not in KEEP:
            del os.environ[k]
    os.environ.update(isolated_env())
    os.environ["PATH"] = str(BIN) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")


# ---- 禁网 ----

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_sendto = socket.socket.sendto
_real_getaddrinfo = socket.getaddrinfo
_real_gethostbyname = socket.gethostbyname
_real_gethostbyname_ex = socket.gethostbyname_ex
_LOCAL_NAMES = {"localhost", socket.gethostname().lower()}


def _block(what):
    _blocked.append(what)
    raise BlockedNetwork(f"[测试隔离] 测试里不许对外连接：{what}")


def _local_name(host):
    h = str(host).lower().rstrip(".")
    return h in _LOCAL_NAMES or h.endswith(".localhost")


def _check_addr(family, address):
    if family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple):
        return                                          # AF_UNIX 等本机通信放行
    host, port = address[0], address[1]
    try:
        loopback = ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        loopback = _local_name(host)
    if not loopback:
        _block(f"{host}:{port}")
    if port in PROD_PORTS:
        _block(f"本机正式服务端口 {host}:{port}")


def _connect(self, address):
    _check_addr(self.family, address)
    return _real_connect(self, address)


def _connect_ex(self, address):
    _check_addr(self.family, address)
    return _real_connect_ex(self, address)


def _sendto(self, data, *args):
    if args:
        _check_addr(self.family, args[-1])
    return _real_sendto(self, data, *args)


def _resolvable(host):
    if host is None or host == "" or _local_name(host if isinstance(host, str) else host.decode()):
        return True
    try:
        ipaddress.ip_address(str(host if isinstance(host, str) else host.decode()).split("%")[0])
        return True                                     # IP 字面量不发 DNS；连不连得上由 connect 判
    except ValueError:
        return False


def _getaddrinfo(host, *args, **kw):
    if not _resolvable(host):
        _block(f"解析域名 {host}")
    return _real_getaddrinfo(host, *args, **kw)


def _gethostbyname(host):
    if not _resolvable(host):
        _block(f"解析域名 {host}")
    return _real_gethostbyname(host)


def _gethostbyname_ex(host):
    if not _resolvable(host):
        _block(f"解析域名 {host}")
    return _real_gethostbyname_ex(host)


def _install_network_guard():
    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    socket.socket.sendto = _sendto
    socket.getaddrinfo = _getaddrinfo
    socket.gethostbyname = _gethostbyname
    socket.gethostbyname_ex = _gethostbyname_ex


def _report_and_cleanup():
    lines = list(dict.fromkeys(_blocked))
    try:
        lines += BLOCKED_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        pass
    if lines:
        shown = "\n".join(f"  {x}" for x in lines[:20])
        more = f"\n  另有 {len(lines) - 20} 条" if len(lines) > 20 else ""
        sys.stderr.write(f"[测试隔离] 本次运行拦下 {len(lines)} 次对外动作（多为测试故意验证「连不上」）：\n{shown}{more}\n")
    shutil.rmtree(ROOT, ignore_errors=True)


_write_gitconfig()
_write_stubs()
_apply()
_install_network_guard()
atexit.register(_report_and_cleanup)
