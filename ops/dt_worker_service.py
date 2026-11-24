"""工人接单程序改成开机自起的系统服务（常浩 2026-11-24 选方案甲）：门卫重载、重启电脑之后自己回来，不用登录 dt-worker。

用法（主账号终端，要输管理员密码；先在 dt-worker 里跑过 setup-dt-worker.sh 补好 Claude 长期令牌，再注销 dt-worker）：
  sudo /usr/bin/python3 -B -E -s ops/dt_worker_service.py install   核对 → 实测没登录时两家 CLI 的登录 → 停用原 LaunchAgent → 装服务
  sudo /usr/bin/python3 -B -E -s ops/dt_worker_service.py status    只看
  sudo /usr/bin/python3 -B -E -s ops/dt_worker_service.py remove    拆服务、放开原 LaunchAgent（下次登录 dt-worker 它照旧起来）

为什么：接单程序原来是 `agent-autoloop join` 装的 LaunchAgent（gui/<uid> 域，RunAtLoad、KeepAlive）。门卫脚本
dt_worker_pf.sh 重新加载规则时按设计结束 dt-worker 的全部进程（DECIDED 11-21 第 4 条），其中有 dt-worker 自己的
loginwindow：图形会话结束，gui/<uid> 域整个没了，KeepAlive 管不到，接单程序就一直不回来；重启电脑也一样。

服务是什么样：/Library/LaunchDaemons/cn.digital-twin.worker-up.plist（root 所有），以 dt-worker 身份跑（UserName），
开机自起、退出了 launchd 10 秒后重起。命令照抄原 LaunchAgent 里引擎自己写的那一条（node + agent-autoloop.ts up），
前面套一小段 sh（写在 plist 里，root 所有）：门卫状态文件是本次开机写的 state=ok、10 分钟内写的、而且是这段 sh 这次
起来之后才写的，才启动接单程序，不然每 10 秒再看一次——门卫规则不在生效时工人照旧不在线。「这次起来之后才写的」
（#382 L3 第 1 轮 P1-02）：门卫判定失败、旧的 ok 作废不掉（状态目录不安全那条分支只清进程就退出）时，服务被清掉后
launchd 重起，不能拿旧的 ok 又把工人放出来；要等门卫下一次核对成功写新的 ok（每 5 分钟一次）。只比 root 写的状态
文件的修改时间和这段 sh 自己记在内存里的起始时间，不在 dt-worker 能改的地方留记录。代价：门卫重载或接单程序自己
崩溃后，最多 5 分钟才回来（装好时 install 叫门卫马上核对一次，不用等）。日志由这段 sh 以 dt-worker 身份追加到
~/.agent-autoloop/logs/up.log（不用 StandardOutPath：那是 launchd 以 root 打开的，路径在 dt-worker 能改的目录里）。
门卫规则的承诺不变：pf 按进程的账号认，服务里的进程仍是 dt-worker；门卫清进程照旧清全部，服务在新规则下重起。

没图形登录时，登录钥匙串是锁着的：Claude 改用 `claude setup-token` 出的长期令牌，存在 ~dt-worker/.dt/claude-oauth-token
（600），claude-proxy.sh 读它设 CLAUDE_CODE_OAUTH_TOKEN（setup-dt-worker.sh 那一步）；Codex 本来就存在 ~/.codex/auth.json。
install 先以 dt-worker 身份、只有系统 PATH、在它没有图形会话时跑一遍引擎自己用的两条检查（claude auth status 要
loggedIn、codex login status 退出码 0），不过就停下、什么都不改：这就是「没登录读不读得到凭据」的实测。

原 LaunchAgent 不删文件（引擎以后升级会重写它，install 从它读命令），只 `launchctl disable`（记在 launchd 自己的库里，
不碰 dt-worker 的家目录）：再登录 dt-worker 也不会起第二个（引擎有单实例锁，起了也只会反复退出）。
已知：在 dt-worker 里跑 `agent-autoloop update` / `join` 会重装 LaunchAgent，被 disable 挡住而报错；升级引擎后
在主账号重跑一次 install（它照新的 LaunchAgent 改服务）。

root 不按路径碰 dt-worker 的家目录：读 LaunchAgent、跑检查都以 dt-worker 身份（sudo -u）。写 plist 前核对
/Library/LaunchDaemons 从 / 起只有 root 能改（ops/dt_root_guard.sh）。Host、装卸服务、原子写文件复用 dt_gate_hub.py。
"""
import argparse
import json
import os
import plistlib
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dt_gate_hub as GH  # noqa: E402

LABEL = "cn.digital-twin.worker-up"
AGENT_LABEL = "agent-autoloop"            # 引擎 join 装的 LaunchAgent（默认 AGENT_AUTOLOOP_HOME 时的标签）
FLAG = "--disable-warning=ExperimentalWarning"
ABS = re.compile(r"^/[A-Za-z0-9._+/-]+$")
SEARCH_PATH = re.compile(r"^/[A-Za-z0-9._+/:-]+$")
START_WAIT = 120                          # 装好后等接单程序起来最多几秒（含门卫核对一次）
GATE_LABEL = "cn.digital-twin.pf-dt-worker"   # 门卫规则的定时服务（ops/dt_worker_pf.sh），每 5 分钟核对并写状态
CHECK_TIMEOUT = 60
GATE_DELAY = 3                            # 装好后等几秒再叫门卫（外壳要先起来、记下起来时间）

# 门卫状态是本次开机写的 state=ok、10 分钟内（0 ≤ 现在 − 修改时间 ≤ 600，和 dt_worktree 准入一样；时钟往回调过、
# 修改时间在「现在」之后的不认，#382 L3 第 2 轮 P1-02）、而且是这段 sh 起来之后写的（修改时间 > 起来时间，按秒比：
# 同一秒里写的不认，宁可多等门卫一轮，#382 L3 第 2 轮 P2-06），才启动；日志以 dt-worker 身份追加。
# 先读修改时间再读内容：状态文件是整个换掉的（mv），后读的内容只会比修改时间那一版更新，不会拿旧的 ok 配新的时间。
# "$@" 是原 LaunchAgent 的命令。
WRAPPER = r'''s="/Library/Application Support/DigitalTwin/pf-dt-worker.status"
w=$(/bin/date +%s)
while :; do
  b=$(/usr/sbin/sysctl -n kern.boottime 2>/dev/null | /usr/bin/sed -n 's/^{ sec = \([0-9][0-9]*\),.*/\1/p')
  m=$(/usr/bin/stat -f %m "$s" 2>/dev/null)
  l=$(/bin/cat "$s" 2>/dev/null)
  n=$(/bin/date +%s)
  case "$b:$m:$w:$n" in *[!0-9:]*|:*|*::*|*:) ;; *)
    if [ "$m" -gt "$w" ] && [ "$n" -ge "$m" ] && [ $((n - m)) -le 600 ]; then
      case " $l " in *" state=ok "*) case " $l " in *" boot=$b "*) break ;; esac ;; esac
    fi ;;
  esac
  /bin/sleep 10
done
/bin/mkdir -p "$HOME/.agent-autoloop/logs"
exec "$@" >>"$HOME/.agent-autoloop/logs/up.log" 2>&1
'''


def worker():
    return GH.worker_account()


def plist_path():
    return GH.daemons() / f"{LABEL}.plist"


def agent_plist(home):
    return f"{home}/Library/LaunchAgents/{AGENT_LABEL}.plist"


def autoloop_home(home):
    return f"{home}/.agent-autoloop"


def parse_agent(data, home):
    """从原 LaunchAgent 里取命令：只认引擎 host/service.ts 写的那种样子，别的一律停下（不猜）。"""
    try:
        p = plistlib.loads(data)
    except Exception as e:                           # plistlib 对坏文件抛的异常种类很多
        raise GH.Stop(f"读不懂原 LaunchAgent（{type(e).__name__}）")
    if not isinstance(p, dict) or p.get("Label") != AGENT_LABEL:
        raise GH.Stop(f"原 LaunchAgent 的 Label 不是 {AGENT_LABEL}（后面停用、放开都按这个名字）")
    args = p.get("ProgramArguments")
    env = p.get("EnvironmentVariables")
    ah = autoloop_home(home)
    program = f"{ah}/app/bin/agent-autoloop.ts"
    if not (isinstance(args, list) and len(args) == 4 and all(isinstance(a, str) for a in args)):
        raise GH.Stop("原 LaunchAgent 的命令不是引擎写的那种（node、参数、agent-autoloop.ts、up 四段）")
    node, flag, prog, up = args
    if not ABS.match(node) or flag != FLAG or prog != program or up != "up":
        raise GH.Stop(f"原 LaunchAgent 的命令对不上（应为 <node> {FLAG} {program} up）")
    if not isinstance(env, dict) or env.get("AGENT_AUTOLOOP_HOME") != ah:
        raise GH.Stop(f"原 LaunchAgent 的 AGENT_AUTOLOOP_HOME 不是 {ah}")
    search = env.get("PATH")
    if not isinstance(search, str) or not SEARCH_PATH.match(search):
        raise GH.Stop("原 LaunchAgent 的 PATH 不对")
    return {"node": node, "program": program, "autoloop_home": ah, "path": search}


def plist_for(name, home, cmd):
    return {"Label": LABEL, "UserName": name,
            "ProgramArguments": ["/bin/sh", "-c", WRAPPER, "dt-worker-up", cmd["node"], FLAG, cmd["program"], "up"],
            "EnvironmentVariables": {"HOME": home, "USER": name, "LOGNAME": name, "PATH": cmd["path"],
                                     "AGENT_AUTOLOOP_HOME": cmd["autoloop_home"]},
            "WorkingDirectory": home, "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10, "Umask": 0o077}


def processes(host):
    """全部进程：(uid, 命令行)。"""
    code, out, err = host.run_ps()
    if code != 0:
        raise GH.Stop(f"ps 出错（{err.strip()[:80]}）")
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1]))
    return rows


def gui_session(rows, uid):
    return any(u == uid and a.split()[0].endswith("/loginwindow") for u, a in rows)


def busy(rows, uid):
    """有派单正在跑（工人启动脚本 dt-worktree 在跑）：这时停接单程序会把那一轮掐断。"""
    return any(u == uid and "dt-worktree-" in a for u, a in rows)


def up_running(rows, uid, cmd):
    """接单程序本身（node 起的那个；外面那层 sh 的命令行里也有这几个字，不算）。"""
    return any(u == uid and a.split()[0] == cmd["node"] and f"{cmd['program']} up" in a for u, a in rows)


def check_token(host, name, uid, home):
    """Claude 长期令牌文件：~/.dt 是 700 的目录、令牌是 600 的普通文件（不是链接）、都归 dt-worker、不是空的。
    以 dt-worker 身份 stat（不跟随链接）。返回 (通过, 说明)。没有它时 claude-proxy.sh 会退回钥匙串，没登录就读不到。"""
    d, f = f"{home}/.dt", f"{home}/.dt/claude-oauth-token"
    try:
        code, so, err = host.as_user(name, ["/usr/bin/stat", "-f", "%Sp %u %z", d, f], timeout=CHECK_TIMEOUT)
    except Exception as e:
        return False, f"查不了（{type(e).__name__}）"
    rows = so.decode("utf-8", "replace").splitlines() if code == 0 else []
    if len(rows) != 2:
        return False, f"没有令牌文件 {f}（{(err or '').strip()[:80]}）"
    for line, mode, path in zip(rows, ("drwx------", "-rw-------"), (d, f)):
        parts = line.split()
        if len(parts) != 3 or parts[0] != mode or parts[1] != str(uid) or not parts[2].isdigit():
            return False, f"{path} 不对（应为 {mode}、归 {name}；实际 {line.strip()[:60]}）"
    if int(rows[1].split()[2]) == 0:
        return False, f"{f} 是空的"
    return True, "令牌文件在（600，只有本账号能读）"


def check_logins(host, name, home):
    """以 dt-worker 身份、只有系统 PATH，跑引擎自己用的两条检查（plugins/claude、codex 的 agent.ts）。
    返回 {执行方: (通过, 说明)}。"""
    out = {}
    bins = f"{autoloop_home(home)}/bin"
    try:
        code, so, err = host.as_user(name, [f"{bins}/claude-proxy.sh", "auth", "status"], timeout=CHECK_TIMEOUT)
        try:
            ok = code == 0 and json.loads(so.decode("utf-8", "replace")).get("loggedIn") is True
        except (ValueError, AttributeError):
            ok = False
        out["claude"] = (ok, "已登录" if ok else f"没登录或读不到（退出码 {code}，{(err or so.decode('utf-8', 'replace')).strip()[:120]}）")
    except Exception as e:                           # 超时、sudo 出错都算没过
        out["claude"] = (False, f"检查没跑成（{type(e).__name__}）")
    try:
        code, so, err = host.as_user(name, [f"{bins}/codex-proxy.sh", "login", "status"], timeout=CHECK_TIMEOUT)
        out["codex"] = (code == 0, "已登录" if code == 0 else f"没登录或读不到（退出码 {code}，{(err or so.decode('utf-8', 'replace')).strip()[:120]}）")
    except Exception as e:
        out["codex"] = (False, f"检查没跑成（{type(e).__name__}）")
    return out


def agent_disabled(host, uid):
    """原 LaunchAgent 停没停用：True 停用、False 没停用、None 查不清（print-disabled 自己出错）。"""
    code, out = host.launchctl("print-disabled", f"gui/{uid}")
    if code != 0:
        return None
    return re.search(rf'"{re.escape(AGENT_LABEL)}" => (true|disabled)', out) is not None


def service_loaded(host):
    """服务现在装没装：True / False；launchctl print 既不是 0 也不是 113（找不到）就是查不清，停下、什么都不动。"""
    code, out = host.launchctl("print", f"system/{LABEL}")
    if code == 0:
        return True
    if code == GH.LAUNCHCTL_NOT_FOUND:
        return False
    raise GH.Stop(f"查不清 {LABEL} 现在装没装（launchctl print 返回 {code}：{out[:80]}），什么都没改")


def old_plist():
    """服务 plist 原来的内容（没有就是 None）；只认普通文件。"""
    f = plist_path()
    if not os.path.lexists(f):
        return None
    if f.is_symlink() or not f.is_file():
        raise GH.Stop(f"{f} 不是普通文件，什么都没改；请人工查看")
    return f.read_bytes()


def account(host):
    name = worker()
    try:
        a = host.account(name)
    except KeyError:
        raise GH.Stop(f"没有 {name} 这个账号")
    if host.guard("uid_ok", name) != 0:
        raise GH.Stop(f"{name} 的 uid 是 0、和别的账号重复，或目录服务查不清")
    if a["home"] != f"/Users/{name}":
        raise GH.Stop(f"{name} 的家目录不是 /Users/{name}")
    return a


def cmd_install(host, say=print):
    a = account(host)
    name, uid, home = a["name"], a["uid"], a["home"]
    GH.root_dir(host, GH.daemons(), make=False)
    rows = processes(host)
    if gui_session(rows, uid):
        raise GH.Stop(f"{name} 现在登录着（有图形会话）：请先在 {name} 里点苹果菜单 → 注销，再来装。"
                      "要在没登录的情况下实测它读不读得到 Claude、Codex 的登录")
    if busy(rows, uid):
        raise GH.Stop("有派单正在跑（dt-worktree 进程在），现在装会把那一轮掐断；等它做完再来")
    code, data, err = host.as_user(name, ["/bin/cat", agent_plist(home)])
    if code != 0:
        raise GH.Stop(f"读不到原 LaunchAgent {agent_plist(home)}（{err.strip()[:80]}）：dt-worker 是不是还没接过中台")
    cmd = parse_agent(data, home)
    say(f"接单程序：{cmd['node']} {cmd['program']} up")
    logins = {"claude 令牌": check_token(host, name, uid, home), **check_logins(host, name, home)}
    for who, (ok, why) in logins.items():
        say(f"  {who}（没登录、后台环境）：{why}")
    if not all(ok for ok, _ in logins.values()):
        raise GH.Stop("没登录时读不到上面那家的登录，什么都没改。Claude：登录 dt-worker 重跑 "
                      "`bash /Users/Shared/dt-autoloop/setup-dt-worker.sh` 补长期令牌；Codex：确认 ~/.codex/auth.json 在。"
                      "补完注销 dt-worker 再来")
    # 先装服务再停 LaunchAgent：没有图形会话时 LaunchAgent 本来就没在跑，disable 只管下次登录
    before = old_plist()
    was_loaded = unload_idle(host, uid, "装")
    try:
        GH.write_root_file(plist_path(), plistlib.dumps(plist_for(name, home, cmd)), 0o644)
        host.chown(plist_path(), 0, 0)
        GH.load(host, plist_path())
    except Exception as e:                           # 写不进、装不上：退回原样（#382 L3 第 2 轮 P2-08）
        raise GH.Stop(f"新服务没装上（{e}）；{roll_back(host, before, was_loaded)}")
    # 叫门卫马上核对一次、写新的状态（外壳只认它起来之后写的 ok）；规则没变时门卫不重新加载、不清进程。
    # 先等几秒：外壳按秒比时间，门卫要在外壳起来之后的下一秒以后写，才算数（P2-06）。
    # 没叫成也不要紧：门卫每 5 分钟自己核对一次，只是要多等
    host.sleep(GATE_DELAY)
    code, out = host.launchctl("kickstart", f"system/{GATE_LABEL}")
    if code != 0:
        say(f"[注意] 没叫成门卫马上核对（{out[:80]}）：接单程序要等门卫下一次核对（5 分钟内）才起来")
    host.launchctl("bootout", f"gui/{uid}/{AGENT_LABEL}")
    host.launchctl("disable", f"gui/{uid}/{AGENT_LABEL}")
    if agent_disabled(host, uid) is True:
        say(f"原 LaunchAgent：已停用（gui/{uid}/{AGENT_LABEL}，文件保留）")
    else:
        say(f"[注意] 原 LaunchAgent 没停用成或查不清（launchctl print-disabled gui/{uid} 里没看到它停用）：下次登录 {name} 时它会起来、"
            f"撞上单实例锁反复退出，不影响服务；手动停：sudo launchctl disable gui/{uid}/{AGENT_LABEL}")
    for _ in range(START_WAIT):
        if up_running(processes(host), uid, cmd):
            say(f"已装好：{LABEL} 在跑，接单程序起来了（几十秒内中台显示 {name} 在线）")
            return 0
        host.sleep(1)
    say(f"[注意] 服务装好了，但 {START_WAIT} 秒内还没看到接单程序起来（不是没装上）：它在等门卫写一份它起来之后的"
        "本次开机 state=ok，门卫每 5 分钟核对一次，正常最多再等 5 分钟。5 分钟后跑 "
        f"sudo /usr/bin/python3 -B -E -s {Path(__file__).name} status 核对；还不在跑就看 ~{name}/.agent-autoloop/logs/up.log "
        "和门卫状态文件")
    return 3


def cmd_status(host, say=print):
    a = account(host)
    name, uid, home = a["name"], a["uid"], a["home"]
    installed = os.path.lexists(plist_path())
    say(f"服务 {LABEL}：{'已装' if installed else '没装'}（{plist_path()}）")
    if installed:
        code, out = host.launchctl("print", f"system/{LABEL}")
        keep = [ln.strip() for ln in out.splitlines() if re.match(r"\s*(state|pid|last exit code|runs) =", ln)]
        say("  " + ("；".join(keep) if code == 0 else f"launchctl 里没有（{out[:80]}）"))
    rows = processes(host)
    code, data, _ = host.as_user(name, ["/bin/cat", agent_plist(home)])
    try:
        cmd = parse_agent(data, home) if code == 0 else None
    except GH.Stop as e:
        cmd = None
        say(f"原 LaunchAgent：{e}")
    if cmd:
        say(f"接单程序：{'在跑' if up_running(rows, uid, cmd) else '没在跑'}")
    say(f"原 LaunchAgent：{ {True: '已停用', False: '没停用', None: '查不清'}[agent_disabled(host, uid)] }")
    logged_in = gui_session(rows, uid)
    say(f"{name} 图形会话：{'登录着' if logged_in else '没登录'}")
    say(f"  claude 令牌：{check_token(host, name, uid, home)[1]}")
    for who, (ok, why) in check_logins(host, name, home).items():
        say(f"  {who}：{why}" + ("（登录着时钥匙串开着，这一项不代表没登录时）" if logged_in else ""))
    return 0


def unload_idle(host, uid, verb):
    """卸掉服务，保证卸的时候没有派单在跑（#382 L3 第 1 轮 P1-01）。卸之前查过一次，但从那时到卸下，旧服务还在接单
    （install 中间还跑了最长 2 分钟的登录检查）；卸下之后不会再接新单，所以再查一次就没有空档了。这时有派单在跑，
    就把旧服务原样装回去（引擎把派单放在单独的进程组里跑、状态记在文件里，接单程序回来会接着管它），然后停下。
    返回服务原来装没装。卸下后进程表查不清（ps 出错）也按「可能有派单」办：装回去、停下（#382 L3 第 2 轮 P1-01）。"""
    was_loaded = service_loaded(host)
    if not GH.unload(host, f"system/{LABEL}"):
        raise GH.Stop(f"{LABEL} 卸不下来（launchctl 一直显示还在），什么都没改；看清原因后再来")
    try:
        why = "卸旧服务前刚接到一张派单（dt-worktree 在跑）" if busy(processes(host), uid) else None
    except Exception as e:                           # ps 出错、超时：查不清不能当没有
        why = f"卸下旧服务后查不清有没有派单在跑（{e}）"
    if why is None:
        return was_loaded
    if not was_loaded:
        raise GH.Stop(f"{why}，现在{verb}可能把那一轮掐断，什么都没改；等它做完、进程表查得清再来")
    try:
        GH.load(host, plist_path())
    except Exception as e:
        raise GH.Stop(f"{why}，想把旧服务装回去接着管它，没装上：{e}。派单可能还在跑、没人管："
                      f"请马上手动装回 sudo launchctl bootstrap system {plist_path()}，再跑 status 看")
    raise GH.Stop(f"{why}，已把旧服务装回去接着管它，什么都没改；等它做完再来{verb}")


def roll_back(host, before, was_loaded):
    """新服务写不进或装不上时退回原样：原来有 plist 就写回原内容，原来装着就装回去；原来没有就删掉新写的。
    原 LaunchAgent 这时还没动（停用在装好之后）。返回一句话说清现在是什么样。"""
    f = plist_path()
    try:
        GH.unload(host, f"system/{LABEL}")
        if before is None:
            if os.path.lexists(f):
                f.unlink()
            return "已退回原样（原来没装服务，新写的 plist 已删掉；原 LaunchAgent 没动）"
        GH.write_root_file(f, before, 0o644)
        host.chown(f, 0, 0)
        if was_loaded:
            GH.load(host, f)
            return "已退回原样（旧 plist 写回、旧服务已装回去）"
        return "已退回原样（旧 plist 写回，原来就没装着，没装）"
    except Exception as e:
        return (f"退回也没成（{e}）：现在服务可能没在跑。请跑 status 看；原来装着服务的，手动 "
                f"sudo launchctl bootstrap system {f}（plist 内容不对就先 remove 再 install）")


def cmd_remove(host, say=print):
    a = account(host)
    if busy(processes(host), a["uid"]):
        raise GH.Stop("有派单正在跑（dt-worktree 进程在），现在拆会把那一轮掐断，什么都没改；等它做完再来")
    unload_idle(host, a["uid"], "拆")
    f = plist_path()
    if os.path.lexists(f):
        if f.is_symlink() or not f.is_file():
            raise GH.Stop(f"{f} 不是普通文件，没删；请人工查看")
        f.unlink()
    code, out = host.launchctl("enable", f"gui/{a['uid']}/{AGENT_LABEL}")
    if code != 0 or agent_disabled(host, a["uid"]) is not False:     # 查不清也不算放开了（第 2 轮 P2-01）
        say(f"[注意] 只拆了一半：{LABEL} 已拆掉，但原 LaunchAgent 没放开或查不清（{out[:80]}）。现在登录 {a['name']} 也不会接单；"
            f"手动放开：sudo launchctl enable gui/{a['uid']}/{AGENT_LABEL}，再跑 status 核对")
        return 1
    say(f"已拆掉 {LABEL}，原 LaunchAgent 已放开：登录 {a['name']} 后接单程序照旧起来（不登录就不在线）")
    return 0


class Host(GH.Host):
    def run_ps(self):
        import subprocess
        r = subprocess.run(["/bin/ps", "-axww", "-o", "uid=,args="], capture_output=True, text=True)
        return r.returncode, r.stdout, r.stderr


def main(argv=None, host=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("install", "status", "remove"))
    a = ap.parse_args(argv)
    host = host or Host()
    if not host.is_root():
        print(f"要用 sudo 运行：sudo /usr/bin/python3 -B -E -s {__file__} {a.cmd}")
        return 2
    os.chdir("/")                        # 主账号家目录 700，以 dt-worker 身份起的进程进不去
    try:
        return {"install": cmd_install, "status": cmd_status, "remove": cmd_remove}[a.cmd](host)
    except GH.Stop as e:
        print(f"[停下] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
