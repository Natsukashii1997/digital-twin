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
                return 5, "Bootstrap failed: 5: Input/output error"
            self.loaded = True
            return 0, ""
        if verb == "bootout":
            if args[1] == f"system/{S.LABEL}":
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


@unittest.skipUnless(sys.platform == "darwin", "要 macOS 的 sysctl kern.boottime（CI 是 Linux）")
class Wrapper(unittest.TestCase):
    """服务 plist 里那段 sh 真跑：门卫状态是本次开机的 state=ok、0 ≤ 现在 − 修改时间 ≤ 600、而且修改时间（按秒）
    晚于这段 sh 起来的时间，才启动接单程序，日志追加到 ~/.agent-autoloop/logs/up.log。等待间隔换成 0.2 秒；
    要「起来之后写的」就先起来、过一秒多再写，修改时间是真实的写入时间。"""

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

    def ok(self):
        return f"at=x boot={self.boot} state=ok enabled=yes rules=9 users=dt-worker\n"

    def write(self, text, age=None):
        """写状态文件；age 给了就把修改时间摆到 age 秒以前（负数 = 以后）。"""
        self.status.write_text(text)
        if age is not None:
            t = time.time() - age
            os.utime(self.status, (t, t))

    def start(self, script=None):
        cmd = ["/bin/sh", "-c", script or self.script, "dt-worker-up", "/bin/sh", "-c", f'echo started; touch "{self.marker}"']
        p = subprocess.Popen(cmd, env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})
        self.addCleanup(self.stop, p)
        return p

    def started_then_write(self, text):
        """先起来，过 1.2 秒（换了一秒）再写：修改时间按秒算也晚于起来时间。"""
        p = self.start()
        time.sleep(1.2)
        self.write(text)
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

    def test_runs_when_ok_written_after_it_started(self):
        p = self.started_then_write(self.ok())
        self.assertTrue(self.wait_for(self.marker, 5))
        self.assertEqual(p.wait(5), 0)
        self.assertIn("started", (self.home / ".agent-autoloop/logs/up.log").read_text())

    def test_waits_when_not_ok(self):
        for text in (f"at=x boot={self.boot} state=failed reason=x\n",
                     f"at=x boot={self.boot} state=applying\n",
                     "at=x boot=1 state=ok enabled=yes\n",                       # 上次开机的 ok 不算
                     f"at=x boot={self.boot}0 state=ok\n"):                      # 前缀相同也不算
            with self.subTest(text=text):
                self.marker.unlink(missing_ok=True)
                p = self.started_then_write(text)
                self.assertFalse(self.wait_for(self.marker, 1))
                self.assertIsNone(p.poll())
                self.stop(p)

    def test_waits_without_status_file(self):
        p = self.start()
        self.assertFalse(self.wait_for(self.marker, 1.5))
        self.assertIsNone(p.poll())

    def test_waits_for_ok_written_after_it_started(self):
        """第 1 轮 P1-02：门卫判定失败、旧的 ok 作废不掉（只清了进程）时，launchd 重起的外壳不能拿旧的 ok 放行；
        门卫下一次核对成功写了新的 ok 才起来。"""
        self.write(self.ok(), 5)                     # 这段 sh 起来之前写的 ok（哪怕只早 5 秒）
        p = self.start()
        self.assertFalse(self.wait_for(self.marker, 1.5))
        self.assertIsNone(p.poll())
        self.write(self.ok())                        # 门卫又写了一次 ok（已经过了一秒多）
        self.assertTrue(self.wait_for(self.marker, 5))

    def test_waits_for_ok_written_in_the_same_second(self):
        """第 2 轮 P2-06：按秒比，和起来同一秒写的不认（宁可多等门卫一轮，不放行可能是起来之前写的）。"""
        self.write(self.ok(), -0.0)
        p = self.start()                             # 和写入大概率同一秒；不同秒时修改时间也早于起来时间
        self.assertFalse(self.wait_for(self.marker, 1.5))
        self.assertIsNone(p.poll())

    def test_waits_when_clock_went_back(self):
        """第 2 轮 P1-02：时钟往回调过，旧 ok 的修改时间在「现在」之后：不认（0 ≤ 现在 − 修改时间）。
        起始时间换成 0，单看这一条。"""
        script = self.script.replace("w=$(/bin/date +%s)", "w=0")
        self.assertNotEqual(script, self.script)
        self.write(self.ok(), -100)
        p = self.start(script)
        self.assertFalse(self.wait_for(self.marker, 1.5))
        self.assertIsNone(p.poll())

    def test_waits_when_ok_is_stale(self):
        """ok 写了超过 10 分钟没刷新（门卫停了）：不算。起始时间换成 0，只看新不新鲜。"""
        script = self.script.replace("w=$(/bin/date +%s)", "w=0")
        self.write(self.ok(), 700)
        p = self.start(script)
        self.assertFalse(self.wait_for(self.marker, 1.5))
        self.write(self.ok(), 30)
        self.assertTrue(self.wait_for(self.marker, 5))
        p.wait(5)


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
        with self.assertRaisesRegex(GH.Stop, "原来没装服务，新写的 plist 已删掉"):
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
