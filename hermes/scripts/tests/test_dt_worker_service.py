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
        self.loaded = False
        self.up_starts = True
        self.disabled = False
        self.agent = engine_plist()
        self.claude = (0, json.dumps({"loggedIn": True}).encode(), "")
        self.codex = (0, b"Logged in using ChatGPT\n", "")

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
        raise AssertionError(argv)

    def launchctl(self, *args):
        self.calls.append(("launchctl", *args))
        verb = args[0]
        if verb == "bootstrap":
            self.loaded = True
            return 0, ""
        if verb == "bootout":
            if args[1] == f"system/{S.LABEL}":
                self.loaded = False
            return 0, ""
        if verb == "print":
            return (0, "state = running\n\tpid = 99\n\tlast exit code = (never exited)") if self.loaded else (113, "not found")
        if verb == "disable":
            self.disabled = True
            return 0, ""
        if verb == "enable":
            self.disabled = False
            return 0, ""
        if verb == "print-disabled":
            return 0, '\t"agent-autoloop" => disabled' if self.disabled else ""
        raise AssertionError(args)

    def run_ps(self):
        rows = ["  1 /sbin/launchd", "501 /usr/bin/whatever"]
        if self.gui:
            rows.append(f"{UID} /System/Library/CoreServices/loginwindow.app/Contents/MacOS/loginwindow console")
        if self.busy:
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


class Wrapper(unittest.TestCase):
    """服务 plist 里那段 sh 真跑：门卫状态是本次开机的 state=ok 才启动接单程序，日志追加到 ~/.agent-autoloop/logs/up.log。"""

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
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
        self.boot = re.search(r"sec = (\d+)", out).group(1)

    def start(self):
        cmd = ["/bin/sh", "-c", self.script, "dt-worker-up", "/bin/sh", "-c", f'echo started; touch "{self.marker}"']
        return subprocess.Popen(cmd, env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"})

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

    def test_runs_when_ok_this_boot(self):
        self.status.write_text(f"at=x boot={self.boot} state=ok enabled=yes rules=9 users=dt-worker\n")
        p = self.start()
        self.addCleanup(self.stop, p)
        self.assertTrue(self.wait_for(self.marker, 5))
        self.assertEqual(p.wait(5), 0)
        self.assertIn("started", (self.home / ".agent-autoloop/logs/up.log").read_text())

    def test_waits_when_not_ok(self):
        for text in (f"at=x boot={self.boot} state=failed reason=x\n",
                     f"at=x boot={self.boot} state=applying\n",
                     "at=x boot=1 state=ok enabled=yes\n",                       # 上次开机的 ok 不算
                     f"at=x boot={self.boot}0 state=ok\n",                       # 前缀相同也不算
                     None):                                                     # 没有状态文件
            with self.subTest(text=text):
                if text is None:
                    self.status.unlink(missing_ok=True)
                else:
                    self.status.write_text(text)
                p = self.start()
                self.assertFalse(self.wait_for(self.marker, 1.5))
                self.assertIsNone(p.poll())
                self.stop(p)


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

    def test_stops_without_agent_plist(self):
        self.host.agent = None
        with self.assertRaisesRegex(GH.Stop, "读不到原 LaunchAgent"):
            self.run_install()
        self.assert_untouched()

    def test_reports_when_up_does_not_start(self):
        """门卫状态不是本次开机的 ok 时，外层 sh 在等：服务装上了，但要说清没起来；外层 sh 不算接单程序。"""
        self.host.up_starts = False
        code, out = self.run_install()
        self.assertEqual(code, 1)
        self.assertTrue(self.plist.exists())
        self.assertIn("没看到接单程序起来", out)

    def test_not_root(self):
        self.host.is_root = lambda: False
        self.assertEqual(S.main(["install"], host=self.host), 2)
        self.assert_untouched()


class Remove(unittest.TestCase):
    def test_remove_unloads_deletes_and_enables(self):
        host = FakeHost()
        GH.daemons().mkdir(parents=True, exist_ok=True)
        f = GH.daemons() / f"{S.LABEL}.plist"
        f.write_bytes(b"x")
        host.loaded = True
        host.disabled = True
        self.assertEqual(S.cmd_remove(host, say=quiet), 0)
        self.assertFalse(f.exists())
        self.assertFalse(host.loaded)
        self.assertFalse(host.disabled)


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


if __name__ == "__main__":
    unittest.main()
