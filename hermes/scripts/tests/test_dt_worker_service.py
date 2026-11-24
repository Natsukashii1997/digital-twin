"""工人接单程序改成系统服务（ops/dt_worker_service.py，常浩 11-24 选甲）的自测。

真机要 root、要 dt-worker 账号，这里换成替身：账号、launchctl、进程表、以账号身份跑的命令都是假的；plist 写到隔离的临时
LaunchDaemons 目录。服务 plist 里那段等门卫规则的 sh 是真跑的（换掉状态文件路径）。真 launchd、真钥匙串由常浩在真机验。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import plistlib
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "ops"))
import dt_worker_service as S  # noqa: E402
import dt_gate_hub as GH  # noqa: E402

HOME = "/Users/dt-worker"
NODE = f"{HOME}/.nvm/versions/node/v24.22.3/bin/node"
PROGRAM = f"{HOME}/.agent-autoloop/app/bin/agent-autoloop.ts"
UID = 502


def engine_plist(**change):
    """引擎 host/service.ts 写的 LaunchAgent 的样子。"""
    p = {"Label": "agent-autoloop", "ProgramArguments": [NODE, S.FLAG, PROGRAM, "up"],
         "EnvironmentVariables": {"AGENT_AUTOLOOP_HOME": f"{HOME}/.agent-autoloop",
                                  "PATH": f"{HOME}/.nvm/versions/node/v24.22.3/bin:/opt/homebrew/bin:/usr/bin:/bin"},
         "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
         "StandardOutPath": f"{HOME}/.agent-autoloop/logs/up.log"}
    p.update(change)
    return plistlib.dumps(p)


class FakeHost(S.Host):
    def __init__(self):
        self.calls = []
        self.gui = False
        self.busy = False
        self.busy_from_ps = None          # 第几次看进程表起有派单在跑（模拟查完之后、卸服务之前刚接到一张）
        self.ps_calls = 0
        self.loaded = False
        self.up_starts = True
        self.disabled = False
        self.agent = engine_plist()
        self.claude = (0, json.dumps({"loggedIn": True}).encode(), "")
        self.codex = (0, b"Logged in using ChatGPT\n", "")
        self.token = (0, f"drwx------ {UID} 96\n-rw------- {UID} 108\n".encode(), "")
        self.enable_result = None         # 不是 None 时 launchctl enable 返回它、也不真放开
        self.print_code = None            # 不是 None 时 launchctl print 返回这个码（查不清）
        self.print_disabled_code = 0
        self.ps_fail_from = None          # 第几次看进程表起 ps 出错
        self.bootstrap_fails = 0          # 前几次 bootstrap 失败
        self.bootstrap_fail_loads = False # bootstrap 报失败、服务却装上了
        self.bootouts = 0                 # 卸服务（bootout system/…）第几次
        self.sticky_from_bootout = None   # 从第几次起 bootout 卸不掉
        self.unclear_from_bootout = None  # 从第几次起 bootout 之后 print 查不清（返回 5）

    def is_root(self):
        return True

    def account(self, name):
        if name != "dt-worker":
            raise KeyError(name)
        return {"name": name, "uid": UID, "gid": 20, "home": HOME}

    def guard(self, fn, *args):
        self.calls.append(("guard", fn, *args))
        return 0

    def chown(self, path, uid, gid):
        self.calls.append(("chown", str(path)))

    def sleep(self, s):
        pass

    def as_user(self, name, argv, stdin=None, env=None, timeout=600):
        self.calls.append(("as_user", name, *argv))
        if argv[0] == "/bin/cat":
            return (0, self.agent, "") if self.agent is not None else (1, b"", "No such file")
        if argv[0].endswith("/claude-proxy.sh"):
            return self.claude
        if argv[0].endswith("/codex-proxy.sh"):
            return self.codex
        if argv[0] == "/usr/bin/stat":
            assert argv[1:3] == ["-f", "%Sp %u %z"] and argv[3:] == [f"{HOME}/.dt", f"{HOME}/.dt/claude-oauth-token"], argv
            return self.token
        raise AssertionError(argv)

    def launchctl(self, *args):
        self.calls.append(("launchctl", *args))
        verb = args[0]
        if verb == "bootstrap":
            if self.bootstrap_fails > 0:
                self.bootstrap_fails -= 1
                if self.bootstrap_fail_loads:
                    self.loaded = True
                return 5, "Bootstrap failed: 5: Input/output error"
            self.loaded = True
            return 0, ""
        if verb == "bootout":
            if args[1] == f"system/{S.LABEL}":
                self.bootouts += 1
                if self.unclear_from_bootout is not None and self.bootouts >= self.unclear_from_bootout:
                    self.print_code = 5
                if self.sticky_from_bootout is None or self.bootouts < self.sticky_from_bootout:
                    self.loaded = False
            return 0, ""
        if verb == "print":
            if self.print_code is not None:
                return self.print_code, "Could not find domain"
            return (0, "state = running\n\tpid = 99\n\tlast exit code = (never exited)") if self.loaded else (113, "not found")
        if verb == "disable":
            self.disabled = True
            return 0, ""
        if verb == "enable":
            if self.enable_result is not None:
                return self.enable_result
            self.disabled = False
            return 0, ""
        if verb == "kickstart":
            return 0, ""
        if verb == "print-disabled":
            if self.print_disabled_code:
                return self.print_disabled_code, "Bad request"
            return 0, '\t"agent-autoloop" => disabled' if self.disabled else ""
        raise AssertionError(args)

    def run_ps(self):
        self.ps_calls += 1
        if self.ps_fail_from is not None and self.ps_calls >= self.ps_fail_from:
            return 1, "", "ps: sysctl failed"
        rows = ["  1 /sbin/launchd", "501 /usr/bin/whatever"]
        if self.gui:
            rows.append(f"{UID} /System/Library/CoreServices/loginwindow.app/Contents/MacOS/loginwindow console")
        if self.busy or (self.busy_from_ps is not None and self.ps_calls >= self.busy_from_ps):
            rows.append(f"{UID} /Users/Shared/dt-autoloop/bin/dt-worktree-claude -p hi")
        if self.loaded:
            rows.append(f"{UID} /bin/sh -c s=x dt-worker-up {NODE} {S.FLAG} {PROGRAM} up")
            if self.up_starts:
                rows.append(f"{UID} {NODE} {S.FLAG} {PROGRAM} up")
        return 0, "\n".join(rows) + "\n", ""


def quiet(*_):
    pass


class ParseAgent(unittest.TestCase):
    def test_engine_plist(self):
        cmd = S.parse_agent(engine_plist(), HOME)
        self.assertEqual(cmd["node"], NODE)
        self.assertEqual(cmd["program"], PROGRAM)
        self.assertEqual(cmd["autoloop_home"], f"{HOME}/.agent-autoloop")

    def test_rejects_anything_else(self):
        env = {"AGENT_AUTOLOOP_HOME": f"{HOME}/.agent-autoloop", "PATH": "/usr/bin:/bin"}
        bad = [
            b"not a plist",
            engine_plist(ProgramArguments=[NODE, S.FLAG, PROGRAM, "up", "--extra"]),
            engine_plist(ProgramArguments=[NODE, S.FLAG, "/tmp/evil.ts", "up"]),
            engine_plist(ProgramArguments=["node", S.FLAG, PROGRAM, "up"]),              # 不是绝对路径
            engine_plist(ProgramArguments=[NODE + " x", S.FLAG, PROGRAM, "up"]),
            engine_plist(ProgramArguments=[NODE, "--inspect", PROGRAM, "up"]),
            engine_plist(EnvironmentVariables={**env, "AGENT_AUTOLOOP_HOME": "/tmp/other"}),
            engine_plist(EnvironmentVariables={**env, "PATH": "/usr/bin:$(evil)"}),
            engine_plist(EnvironmentVariables="x"),
            engine_plist(Label="agent-autoloop.other"),                                 # Label 不对：停用、放开都按名字
            plistlib.dumps({k: v for k, v in plistlib.loads(engine_plist()).items() if k != "Label"}),
        ]
        for data in bad:
            with self.subTest(data=data[:60]):
                with self.assertRaises(GH.Stop):
                    S.parse_agent(data, HOME)


class PlistShape(unittest.TestCase):
    def test_daemon_plist(self):
        cmd = S.parse_agent(engine_plist(), HOME)
        p = plistlib.loads(plistlib.dumps(S.plist_for("dt-worker", HOME, cmd)))
        self.assertEqual(p["Label"], S.LABEL)
        self.assertEqual(p["UserName"], "dt-worker")
        self.assertEqual(p["ProgramArguments"][:4], ["/bin/sh", "-c", S.WRAPPER, "dt-worker-up"])
        self.assertEqual(p["ProgramArguments"][4:], [NODE, S.FLAG, PROGRAM, "up"])
        self.assertTrue(p["RunAtLoad"] and p["KeepAlive"])
        self.assertEqual(p["Umask"], 0o077)
        self.assertEqual(p["EnvironmentVariables"]["HOME"], HOME)
        self.assertEqual(p["EnvironmentVariables"]["AGENT_AUTOLOOP_HOME"], f"{HOME}/.agent-autoloop")
        # launchd 以 root 打开 StandardOutPath：路径在 dt-worker 能改的目录里，不能用
        self.assertNotIn("StandardOutPath", p)
        self.assertNotIn("StandardErrorPath", p)


@unittest.skipUnless(sys.platform == "darwin", "要 macOS 的 stat -f、sysctl kern.boottime（CI 是 Linux）")
class Wrapper(unittest.TestCase):
    """服务 plist 里那段 sh 真跑：它起来之后门卫新发布过状态文件（身份变了：门卫写临时文件再 mv 换掉），而且新的那份是
    本次开机的 state=ok、enabled=yes、users 里有本账号、0 ≤ 现在 − 修改时间 ≤ 600，才启动接单程序，日志追加到
    ~/.agent-autoloop/logs/up.log。等待间隔换成 0.2 秒。"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.d = Path(d.name)
        self.status = self.d / "pf.status"
        self.home = self.d / "home"
        self.home.mkdir()
        self.marker = self.d / "ran"
        self.script = S.WRAPPER.replace("/Library/Application Support/DigitalTwin/pf-dt-worker.status", str(self.status))
        self.assertNotEqual(self.script, S.WRAPPER)
        self.assertIn("/bin/sleep 10", self.script)
        self.script = self.script.replace("/bin/sleep 10", "/bin/sleep 0.2")
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
        self.boot = re.search(r"sec = (\d+)", out).group(1)

    def ok(self, **change):
        f = {"at": "x", "boot": self.boot, "state": "ok", "enabled": "yes", "rules": "9", "users": "dt-worker,dt-secretary"}
        f.update(change)
        return " ".join(f"{k}={v}" for k, v in f.items() if v is not None) + "\n"

    def publish(self, text, age=None):
        """像门卫那样发布：写临时文件、（可选）把修改时间摆到 age 秒以前，再 mv 换掉——身份跟着变。"""
        tmp = self.d / "pf.status.tmp"
        tmp.write_text(text)
        if age is not None:
            ts = time.time() - age
            os.utime(tmp, (ts, ts))
        os.replace(tmp, self.status)

    def start(self):
        cmd = ["/bin/sh", "-c", self.script, "dt-worker-up", "/bin/sh", "-c", f'echo started; touch "{self.marker}"']
        p = subprocess.Popen(cmd, env={"HOME": str(self.home), "PATH": "/usr/bin:/bin", "USER": "dt-worker"})
        self.addCleanup(self.stop, p)
        time.sleep(0.3)                              # 等它记下状态文件当时的身份
        return p

    def wait_for(self, path, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if path.exists():
                return True
            time.sleep(0.05)
        return False

    def stop(self, p):
        if p.poll() is None:
            p.kill()
        p.wait()

    def assert_waits(self, p, seconds=1.2):
        self.assertFalse(self.wait_for(self.marker, seconds))
        self.assertIsNone(p.poll())

    def test_runs_after_new_ok_published(self):
        self.publish(self.ok())                      # 起来之前就有的 ok
        p = self.start()
        self.publish(self.ok())                      # 起来之后门卫又发布了一次（同一秒也算：认身份不认时间）
        self.assertTrue(self.wait_for(self.marker, 5))
        self.assertEqual(p.wait(5), 0)
        self.assertIn("started", (self.home / ".agent-autoloop/logs/up.log").read_text())

    def test_runs_when_status_appears_after_start(self):
        p = self.start()                             # 起来时还没有状态文件
        self.publish(self.ok())
        self.assertTrue(self.wait_for(self.marker, 5))
        p.wait(5)

    def test_waits_for_new_publish_not_old_ok(self):
        """第 1、3 轮 P1-02：门卫判定失败、旧的 ok 作废不掉时，launchd 重起的外壳不能拿旧的 ok 放行：碰一下修改时间
        （时钟追上来）、原地改写内容都不算新发布；门卫真的换上新文件才起来。"""
        self.publish(self.ok())
        p = self.start()
        self.assert_waits(p)
        os.utime(self.status, None)                  # 修改时间变成「现在」，身份没变
        self.assert_waits(p)
        self.status.write_text(self.ok(at="y"))      # 原地改写，身份没变
        self.assert_waits(p)
        self.publish(self.ok())
        self.assertTrue(self.wait_for(self.marker, 5))

    def test_waits_when_clock_went_back_then_forward(self):
        """第 3 轮 P1-02：旧 ok 的修改时间在将来（时钟往回调过）；时钟再追上来（修改时间不再在将来）也不放行。"""
        self.publish(self.ok(), age=-100)
        p = self.start()
        self.assert_waits(p)
        os.utime(self.status, None)
        self.assert_waits(p)

    def test_waits_when_new_publish_is_bad(self):
        cases = [self.ok(state="failed"), self.ok(state="applying"), self.ok(boot="1"), self.ok(boot=self.boot + "0"),
                 self.ok(enabled="no"), self.ok(enabled=None), self.ok(users="dt-secretary"), self.ok(users=None),
                 self.ok(users="dt-worker2")]
        p = self.start()
        for text in cases:
            with self.subTest(text=text):
                self.publish(text)
                self.assert_waits(p, 0.6)

    def test_waits_when_new_publish_has_future_or_stale_mtime(self):
        """新发布的那份也要 0 ≤ 现在 − 修改时间 ≤ 600（和 dt_worktree 一样）。"""
        p = self.start()
        self.publish(self.ok(), age=-100)            # 修改时间在将来
        self.assert_waits(p)
        self.publish(self.ok(), age=700)             # 超过 10 分钟
        self.assert_waits(p)
        self.publish(self.ok(), age=30)
        self.assertTrue(self.wait_for(self.marker, 5))


class Install(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        self.daemons = GH.daemons()
        self.daemons.mkdir(parents=True, exist_ok=True)
        self.plist = self.daemons / f"{S.LABEL}.plist"
        self.addCleanup(lambda: self.plist.unlink(missing_ok=True))

    def run_install(self):
        lines = []
        code = S.cmd_install(self.host, say=lines.append)
        return code, "\n".join(lines)

    def assert_untouched(self):
        self.assertFalse(self.plist.exists())
        verbs = [c[1] for c in self.host.calls if c[0] == "launchctl"]
        self.assertNotIn("bootstrap", verbs)
        self.assertNotIn("disable", verbs)

    def test_installs(self):
        code, out = self.run_install()
        self.assertEqual(code, 0, out)
        p = plistlib.loads(self.plist.read_bytes())
        self.assertEqual(p["UserName"], "dt-worker")
        self.assertEqual(p["ProgramArguments"][4:], [NODE, S.FLAG, PROGRAM, "up"])
        verbs = [c[1:] for c in self.host.calls if c[0] == "launchctl"]
        self.assertIn(("bootstrap", "system", str(self.plist)), verbs)
        self.assertIn(("disable", f"gui/{UID}/agent-autoloop"), verbs)
        self.assertIn("已停用", out)
        self.assertIn("接单程序起来了", out)
        self.assertIn(("kickstart", f"system/{S.GATE_LABEL}"), verbs)      # 叫门卫马上写一次新的状态
        self.assertIn("令牌文件在", out)
        # 读 LaunchAgent、跑检查都以 dt-worker 身份
        self.assertTrue(all(c[1] == "dt-worker" for c in self.host.calls if c[0] == "as_user"))

    def test_stops_while_logged_in(self):
        """有图形会话时钥匙串开着，实测不出「没登录读不读得到」：先注销再装。"""
        self.host.gui = True
        with self.assertRaisesRegex(GH.Stop, "注销"):
            self.run_install()
        self.assert_untouched()

    def test_stops_while_task_running(self):
        self.host.busy = True
        with self.assertRaisesRegex(GH.Stop, "派单正在跑"):
            self.run_install()
        self.assert_untouched()

    def test_stops_when_logins_unreadable(self):
        cases = {"claude": [(0, json.dumps({"loggedIn": False}).encode(), ""), (1, b"", "keychain locked"),
                            (0, b"not json", "")],
                 "codex": [(1, b"Not logged in\n", "")]}
        for who, results in cases.items():
            for r in results:
                with self.subTest(who=who, r=r):
                    self.host = FakeHost()
                    setattr(self.host, who, r)
                    with self.assertRaisesRegex(GH.Stop, "什么都没改"):
                        self.run_install()
                    self.assert_untouched()

    def test_stops_when_token_file_wrong(self):
        """P2-04：没有令牌文件时 claude-proxy.sh 会退回钥匙串，检查可能碰巧过；令牌文件本身要在、权限对。"""
        bad = [(1, b"", "stat: No such file or directory"),
               (0, f"drwx------ {UID} 96\n".encode(), ""),
               (0, f"drwxr-xr-x {UID} 96\n-rw------- {UID} 108\n".encode(), ""),
               (0, f"drwx------ {UID} 96\n-rw-r--r-- {UID} 108\n".encode(), ""),
               (0, f"drwx------ {UID} 96\nlrwxr-xr-x {UID} 20\n".encode(), ""),
               (0, f"drwx------ {UID} 96\n-rw------- 0 108\n".encode(), ""),
               (0, f"drwx------ {UID} 96\n-rw------- {UID} 0\n".encode(), "")]
        for r in bad:
            with self.subTest(r=r):
                self.host = FakeHost()
                self.host.token = r
                with self.assertRaisesRegex(GH.Stop, "什么都没改"):
                    self.run_install()
                self.assert_untouched()

    def test_reinstall_task_started_before_unload(self):
        """P1-01：查完「没派单在跑」到卸下旧服务之间（中间有最长 2 分钟的登录检查），旧服务还在接单、刚接到一张：
        卸下后再看一次就发现了，把旧服务原样装回去接着管它，停下，plist 不动。"""
        self.plist.write_bytes(b"old")
        self.host.loaded = True
        self.host.busy_from_ps = 2                   # 第 1 次（卸之前）没有，第 2 次（卸之后）有
        with self.assertRaisesRegex(GH.Stop, "已把旧服务装回去"):
            self.run_install()
        self.assertTrue(self.host.loaded)
        self.assertEqual(self.plist.read_bytes(), b"old")
        verbs = [c[1:] for c in self.host.calls if c[0] == "launchctl"]
        self.assertIn(("bootstrap", "system", str(self.plist)), verbs)
        self.assertNotIn(("disable", f"gui/{UID}/agent-autoloop"), verbs)

    def test_reinstall_ps_fails_after_unload(self):
        """第 2 轮 P1-01：卸下旧服务后进程表查不清（ps 出错）不能当没有派单：装回旧服务、停下。"""
        self.plist.write_bytes(b"old")
        self.host.loaded = True
        self.host.ps_fail_from = 2
        with self.assertRaisesRegex(GH.Stop, "查不清有没有派单.*已把旧服务装回去"):
            self.run_install()
        self.assertTrue(self.host.loaded)
        self.assertEqual(self.plist.read_bytes(), b"old")

    def test_reinstall_ps_raises_after_unload(self):
        self.plist.write_bytes(b"old")
        self.host.loaded = True
        orig = self.host.run_ps

        def boom():
            if self.host.ps_calls >= 1:
                raise OSError("ps gone")
            return orig()
        self.host.run_ps = boom
        with self.assertRaisesRegex(GH.Stop, "查不清有没有派单.*已把旧服务装回去"):
            self.run_install()
        self.assertTrue(self.host.loaded)

    def test_reinstall_restore_fails(self):
        """装回旧服务也没成：说清派单可能没人管，给出手动命令。"""
        self.plist.write_bytes(b"old")
        self.host.loaded = True
        self.host.busy_from_ps = 2
        self.host.bootstrap_fails = 5
        with self.assertRaisesRegex(GH.Stop, "没装上.*sudo launchctl bootstrap system"):
            self.run_install()

    def test_stops_when_print_unclear(self):
        """第 2 轮 P1-01：launchctl print 既不是 0 也不是 113：查不清服务装没装，什么都不动。"""
        self.host.print_code = 5
        with self.assertRaisesRegex(GH.Stop, "查不清"):
            self.run_install()
        self.assert_untouched()

    def test_new_service_fails_to_load_rolls_back(self):
        """第 2 轮 P2-08：新 plist 装不上（bootstrap 连败 5 次）：写回旧 plist、装回旧服务，原 LaunchAgent 不动。"""
        self.plist.write_bytes(b"old")
        self.host.loaded = True
        self.host.bootstrap_fails = 5
        with self.assertRaisesRegex(GH.Stop, "新服务没装上.*已退回原样（旧 plist 写回、旧服务已装回去）"):
            self.run_install()
        self.assertEqual(self.plist.read_bytes(), b"old")
        self.assertTrue(self.host.loaded)
        self.assertNotIn("disable", [c[1] for c in self.host.calls if c[0] == "launchctl"])

    def test_first_install_fails_to_load_rolls_back(self):
        self.host.bootstrap_fails = 5
        with self.assertRaisesRegex(GH.Stop, "已退回原样（新写的 plist 已删掉，原来就没装着服务"):
            self.run_install()
        self.assertFalse(self.plist.exists())
        self.assertFalse(self.host.loaded)

    def test_waits_before_kicking_gate(self):
        """第 2 轮 P2-06：装好后先等几秒再叫门卫，门卫写状态才会落在外壳起来之后的下一秒以后。"""
        slept = []
        self.host.sleep = lambda s: (slept.append(s), self.host.calls.append(("sleep", s)))
        self.run_install()
        calls = [c for c in self.host.calls if c[0] == "sleep" or c[:2] == ("launchctl", "kickstart")]
        self.assertEqual(calls[0], ("sleep", S.GATE_DELAY))
        self.assertEqual(calls[1][:2], ("launchctl", "kickstart"))
        self.assertGreaterEqual(S.GATE_DELAY, 2)

    def test_reinstall_unload_unclear(self):
        """第 3 轮 P1-01：bootout 之后 print 一直查不清（不是 113）：不能说「什么都没改」，说清可能已经停了、给核实和装回的步骤；
        plist 不动。"""
        self.plist.write_bytes(b"old")
        self.host.loaded = True
        self.host.unclear_from_bootout = 1
        with self.assertRaisesRegex(GH.Stop, "bootout 之后查不清.*可能已经停了.*bootstrap system"):
            self.run_install()
        self.assertEqual(self.plist.read_bytes(), b"old")
        self.assertNotIn("disable", [c[1] for c in self.host.calls if c[0] == "launchctl"])

    def test_reinstall_unload_sticks(self):
        """bootout 之后 print 一直说还装着：服务照旧在跑，什么都没改。"""
        self.plist.write_bytes(b"old")
        self.host.loaded = True
        self.host.sticky_from_bootout = 1
        with self.assertRaisesRegex(GH.Stop, "卸不下来.*服务照旧在跑"):
            self.run_install()
        self.assertTrue(self.host.loaded)
        self.assertEqual(self.plist.read_bytes(), b"old")

    def test_rollback_unload_unclear_first_install(self):
        """第 3 轮 P2-08：新服务装不上、回滚时卸没卸掉查不清：plist 删掉，但不报「已退回原样」，给核实步骤。"""
        self.host.bootstrap_fails = 5
        self.host.unclear_from_bootout = 2           # 第 1 次是装之前卸旧的（没装着），第 2 次是回滚
        with self.assertRaisesRegex(GH.Stop, "只退回了一半：新写的 plist 已删掉，但查不清新服务还装没装.*113"):
            self.run_install()
        self.assertFalse(self.plist.exists())

    def test_rollback_unload_sticks_with_old_plist(self):
        """原来有 plist、没装着；新服务装不上却装上了一半、回滚卸不掉：旧 plist 写回，但报「新服务还装着」，不报退回原样。"""
        self.plist.write_bytes(b"old")
        self.host.bootstrap_fails = 5
        self.host.bootstrap_fail_loads = True
        self.host.sticky_from_bootout = 2
        with self.assertRaisesRegex(GH.Stop, "只退回了一半：旧 plist 已写回，但新服务还装着.*卸掉就行"):
            self.run_install()
        self.assertEqual(self.plist.read_bytes(), b"old")

    def test_rollback_unload_sticks_was_loaded(self):
        """原来装着旧服务：回滚卸不掉新的时，提示里要先卸干净（到 113）再 bootstrap 旧的。"""
        self.plist.write_bytes(b"old")
        self.host.loaded = True
        self.host.bootstrap_fails = 5
        self.host.bootstrap_fail_loads = True
        self.host.sticky_from_bootout = 2
        with self.assertRaisesRegex(GH.Stop, "新服务还装着.*113.*再 sudo launchctl bootstrap system"):
            self.run_install()
        self.assertEqual(self.plist.read_bytes(), b"old")

    def test_ps_fails_after_installed(self):
        """第 3 轮 P2-09：服务装好之后查进程表出错：不报装失败（不是 1），返回 3、plist 留着、说清查不清。"""
        self.host.ps_fail_from = 3                   # 第 1 次装之前、第 2 次卸旧服务后，之后都出错
        code, out = self.run_install()
        self.assertEqual(code, 3)
        self.assertTrue(self.plist.exists())
        self.assertIn("进程表查不清", out)
        self.assertIn("不是没装上", out)

    def test_first_install_task_appears_after_unload(self):
        """原来没装服务时卸之后才看到派单：不装回什么，停下，不写 plist。"""
        self.host.busy_from_ps = 2
        with self.assertRaisesRegex(GH.Stop, "什么都没改"):
            self.run_install()
        self.assert_untouched()

    def test_stops_without_agent_plist(self):
        self.host.agent = None
        with self.assertRaisesRegex(GH.Stop, "读不到原 LaunchAgent"):
            self.run_install()
        self.assert_untouched()

    def test_reports_when_up_does_not_start(self):
        """门卫状态不是本次开机的 ok 时，外层 sh 在等：服务装上了，但要说清没起来；外层 sh 不算接单程序。"""
        self.host.up_starts = False
        code, out = self.run_install()
        self.assertEqual(code, 3)                    # 装好了、还在等门卫：不是装失败（第 2 轮 P2-06）
        self.assertTrue(self.plist.exists())
        self.assertIn("还没看到接单程序起来（不是没装上）", out)

    def test_warns_when_disable_did_not_stick(self):
        """launchctl disable 没记上：服务照装，提示里给出能直接照抄的命令（uid 填好）。"""
        self.host.launchctl_orig = self.host.launchctl

        def no_disable(*args):
            if args[0] == "disable":
                self.host.calls.append(("launchctl", *args))
                return 1, "failed"
            return self.host.launchctl_orig(*args)
        self.host.launchctl = no_disable
        code, out = self.run_install()
        self.assertEqual(code, 0, out)
        self.assertIn(f"sudo launchctl disable gui/{UID}/agent-autoloop", out)
        self.assertNotIn("{", out)

    def test_not_root(self):
        self.host.is_root = lambda: False
        self.assertEqual(S.main(["install"], host=self.host), 2)
        self.assert_untouched()


class Remove(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost()
        GH.daemons().mkdir(parents=True, exist_ok=True)
        self.f = GH.daemons() / f"{S.LABEL}.plist"
        self.f.write_bytes(b"x")
        self.addCleanup(lambda: self.f.unlink(missing_ok=True))
        self.host.loaded = True
        self.host.disabled = True

    def test_remove_unloads_deletes_and_enables(self):
        self.assertEqual(S.cmd_remove(self.host, say=quiet), 0)
        self.assertFalse(self.f.exists())
        self.assertFalse(self.host.loaded)
        self.assertFalse(self.host.disabled)

    def test_stops_while_task_running(self):
        """P1-01：有派单在跑时拆服务会把那一轮掐断：停下，什么都不动。"""
        self.host.busy = True
        with self.assertRaisesRegex(GH.Stop, "派单正在跑"):
            S.cmd_remove(self.host, say=quiet)
        self.assertTrue(self.f.exists())
        self.assertTrue(self.host.loaded)
        self.assertNotIn("bootout", [c[1] for c in self.host.calls if c[0] == "launchctl"])

    def test_task_started_before_unload(self):
        self.host.busy_from_ps = 2
        with self.assertRaisesRegex(GH.Stop, "已把旧服务装回去"):
            S.cmd_remove(self.host, say=quiet)
        self.assertTrue(self.f.exists())
        self.assertTrue(self.host.loaded)
        self.assertTrue(self.host.disabled)

    def test_stops_before_bootout_when_plist_not_regular(self):
        """第 3 轮 P2-10：plist 不是普通文件（链接）：在卸服务之前就停下。"""
        self.f.unlink()
        self.f.symlink_to("/etc/hosts")
        with self.assertRaisesRegex(GH.Stop, "不是普通文件"):
            S.cmd_remove(self.host, say=quiet)
        self.assertNotIn("bootout", [c[1] for c in self.host.calls if c[0] == "launchctl"])
        self.assertTrue(self.host.loaded)

    def test_unlink_fails_reloads(self):
        """卸了服务却删不掉 plist：装回去，不留下没人接单的样子。"""
        from unittest import mock
        with mock.patch.object(Path, "unlink", side_effect=PermissionError("nope")):
            with self.assertRaisesRegex(GH.Stop, "删不掉.*已把服务装回去"):
                S.cmd_remove(self.host, say=quiet)
        self.assertTrue(self.host.loaded)
        self.assertTrue(self.host.disabled)

    def test_unload_unclear(self):
        self.host.unclear_from_bootout = 1
        with self.assertRaisesRegex(GH.Stop, "bootout 之后查不清"):
            S.cmd_remove(self.host, say=quiet)
        self.assertTrue(self.f.exists())

    def test_reports_half_done_when_disabled_unreadable(self):
        """第 2 轮 P2-01：enable 返回 0，但 print-disabled 自己出错、查不清：不报成功。"""
        self.host.print_disabled_code = 1
        lines = []
        self.assertEqual(S.cmd_remove(self.host, say=lines.append), 1)
        self.assertIn("只拆了一半", "\n".join(lines))

    def test_ps_fails_after_unload(self):
        self.host.ps_fail_from = 2
        with self.assertRaisesRegex(GH.Stop, "查不清有没有派单.*已把旧服务装回去"):
            S.cmd_remove(self.host, say=quiet)
        self.assertTrue(self.f.exists())
        self.assertTrue(self.host.loaded)

    def test_reports_half_done_when_enable_fails(self):
        """P2-01：放开原 LaunchAgent 没成（返回非 0，或返回 0 但 print-disabled 里还是停用）：不报成功。"""
        for result in ((1, "Operation not permitted"), (0, "")):
            with self.subTest(result=result):
                self.setUp()
                self.host.enable_result = result
                lines = []
                self.assertEqual(S.cmd_remove(self.host, say=lines.append), 1)
                self.assertIn("只拆了一半", "\n".join(lines))
                self.assertIn(f"sudo launchctl enable gui/{UID}/agent-autoloop", "\n".join(lines))


class Status(unittest.TestCase):
    def test_plist_and_launchd_reported_apart(self):
        """第 3 轮 P2-11：plist 没了服务却还装着、launchd 查不清，都要看得出来。"""
        GH.daemons().mkdir(parents=True, exist_ok=True)
        (GH.daemons() / f"{S.LABEL}.plist").unlink(missing_ok=True)
        for loaded, code, want in ((True, None, "plist 不存在.*launchd 里已装"),
                                   (False, None, "plist 不存在.*launchd 里没装（找不到）"),
                                   (False, 5, "launchd 里查不清（launchctl print 返回 5）")):
            with self.subTest(want=want):
                host = FakeHost()
                host.loaded = loaded
                host.print_code = code
                lines = []
                S.cmd_status(host, say=lines.append)
                self.assertRegex("\n".join(lines), want)

    def test_status_reports(self):
        host = FakeHost()
        host.loaded = True
        lines = []
        S.cmd_status(host, say=lines.append)
        text = "\n".join(lines)
        self.assertIn("接单程序：在跑", text)
        self.assertIn("没登录", text)


class ProxyToken(unittest.TestCase):
    """setup-dt-worker.sh 第 5 步生成的 claude-proxy.sh：有长期令牌文件就带上 CLAUDE_CODE_OAUTH_TOKEN，没有就不带；
    codex-proxy.sh 不碰令牌。取脚本里那段 for 循环真跑一遍，再真跑生成的脚本。"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = Path(d.name)
        text = (REPO / "ops/dt_worker_setup.sh").read_text()
        loop = re.search(r"^mkdir -p -m 700 ~/.agent-autoloop/bin\n(for pair in .*?^done)$", text, re.M | re.S).group(0)
        fake = self.home / "fakebin"
        fake.mkdir()
        for name in ("claude", "codex"):
            f = fake / name
            f.write_text('#!/bin/sh\necho "token=${CLAUDE_CODE_OAUTH_TOKEN-unset} proxy=$HTTPS_PROXY args=$*"\n')
            f.chmod(0o755)
        env = {"HOME": str(self.home), "PATH": "/usr/bin:/bin", "PROXY": "http://127.0.0.1:8090",
               "CLAUDE": str(fake / "claude"), "CODEX": str(fake / "codex")}
        r = subprocess.run(["/bin/bash", "-c", "set -euo pipefail\n" + loop], env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def proxy(self, name):
        r = subprocess.run([str(self.home / f".agent-autoloop/bin/{name}-proxy.sh"), "auth", "status"],
                           env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def test_without_token_file(self):
        self.assertEqual(self.proxy("claude"), "token=unset proxy=http://127.0.0.1:8090 args=auth status")

    def test_with_token_file(self):
        (self.home / ".dt").mkdir(mode=0o700)
        (self.home / ".dt/claude-oauth-token").write_text("sk-ant-oat01-test\n")
        self.assertEqual(self.proxy("claude"), "token=sk-ant-oat01-test proxy=http://127.0.0.1:8090 args=auth status")
        self.assertEqual(self.proxy("codex"), "token=unset proxy=http://127.0.0.1:8090 args=auth status")


class SetupToken(unittest.TestCase):
    """setup-dt-worker.sh 第 4b 步（存 Claude 长期令牌）真跑：取脚本里那一段，假的 claude，令牌从标准输入给。
    第 2 轮 P2-07：~/.dt 是链接时，在 setup-token、写令牌之前就停下，链接指向的目录里什么都不写。"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.home = Path(d.name) / "home"
        self.home.mkdir()
        self.elsewhere = Path(d.name) / "elsewhere"
        self.elsewhere.mkdir()
        text = (REPO / "ops/dt_worker_setup.sh").read_text()
        self.block = re.search(r"^TOKEN_DIR=.*?(?=^# 5\. )", text, re.M | re.S).group(0)
        self.claude = Path(d.name) / "claude"
        self.claude.write_text(f'#!/bin/sh\ntouch "{d.name}/setup-token-ran"\n')
        self.claude.chmod(0o755)
        self.ran = Path(d.name) / "setup-token-ran"

    def run_block(self, token="sk-ant-oat01-test"):
        script = ("set -euo pipefail\nsay() { :; }\nstop() { printf '[停下] %s\\n' \"$*\"; exit 1; }\n" + self.block)
        return subprocess.run(["/bin/bash", "-c", script], input=token + "\n", capture_output=True, text=True,
                              env={"HOME": str(self.home), "PATH": "/usr/bin:/bin", "CLAUDE": str(self.claude)})

    def test_writes_token_600(self):
        r = self.run_block()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        f = self.home / ".dt/claude-oauth-token"
        self.assertEqual(f.read_text(), "sk-ant-oat01-test\n")
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.home / ".dt").stat().st_mode & 0o777, 0o700)
        self.assertFalse((self.home / ".dt/claude-oauth-token.tmp").exists())

    def test_stops_before_writing_when_dir_is_link(self):
        (self.home / ".dt").symlink_to(self.elsewhere)
        r = self.run_block()
        self.assertEqual(r.returncode, 1)
        self.assertIn("是链接", r.stdout)
        self.assertFalse(self.ran.exists())                    # 没跑 setup-token
        self.assertEqual(list(self.elsewhere.iterdir()), [])   # 链接那头什么都没写

    def test_stops_when_token_or_tmp_is_link(self):
        for name in ("claude-oauth-token", "claude-oauth-token.tmp"):
            with self.subTest(name=name):
                for x in (self.home / ".dt").glob("*") if (self.home / ".dt").exists() else ():
                    x.unlink()
                (self.home / ".dt").mkdir(mode=0o700, exist_ok=True)
                (self.home / ".dt" / name).symlink_to(self.elsewhere / "target")
                r = self.run_block()
                self.assertEqual(r.returncode, 1)
                self.assertIn("是链接", r.stdout)
                self.assertFalse(self.ran.exists())
                self.assertFalse((self.elsewhere / "target").exists())

    def test_rejects_non_token(self):
        r = self.run_block("hello")
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.home / ".dt/claude-oauth-token").exists())


if __name__ == "__main__":
    unittest.main()
