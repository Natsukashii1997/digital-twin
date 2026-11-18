"""统一测试隔离的守护测试（#212）：python3 -B -m unittest discover -s hermes/scripts/tests -p test_isolation_guard.py -v

1. 扫描本目录每个 test*.py：第一句（文档字符串之后）必须是 `import _isolation`，漏写、写在别的 import 后面都失败——
   以后新加的测试写不出不隔离的版本。fixtures/isolation_bypass/ 里两个故意绕过的假模块证明这条会判失败。
2. 隔离本身：不靠外层保护，在子进程里故意给真实家目录和指向正式环境的变量，导入 _isolation 后都被改写到临时目录；
   对外连接、解析域名、本机正式服务端口被拒，回环上测试自己起的服务照常能连；危险命令被桩拦下。
3. discover 时 _isolation 包排在所有测试模块前面先导入：漏写第一句的模块也已经在隔离里。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import ast
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BYPASS = HERE / "fixtures/isolation_bypass"
ISO = _isolation


def offenders(directory):
    """directory 下（不递归）第一句不是 `import _isolation` 的测试模块 → [(文件名, 原因)]。"""
    out = []
    for p in sorted(Path(directory).glob("test*.py")):
        body = ast.parse(p.read_text(encoding="utf-8")).body
        i = 0
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            i = 1                                       # 文档字符串
        while i < len(body) and isinstance(body[i], ast.ImportFrom) and body[i].module == "__future__":
            i += 1
        first = body[i] if i < len(body) else None
        if not (isinstance(first, ast.Import) and [a.name for a in first.names] == ["_isolation"]):
            seen = any(isinstance(n, ast.Import) and any(a.name == "_isolation" for a in n.names) for n in body)
            out.append((p.name, "import _isolation 不是第一句" if seen else "没有 import _isolation"))
    return out


def inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


class Guard(unittest.TestCase):
    def test_every_test_module_imports_isolation_first(self):
        self.assertEqual(offenders(HERE), [], "这些测试模块绕过了统一隔离：第一句（文档字符串之后）必须是 import _isolation")

    def test_guard_flags_modules_that_bypass_isolation(self):
        self.assertEqual(offenders(BYPASS), [("test_bypass_isolation.py", "import _isolation 不是第一句"),
                                             ("test_no_isolation.py", "没有 import _isolation")])

    def test_guard_goes_red_when_a_bypass_module_is_added(self):
        with tempfile.TemporaryDirectory() as d:                    # 拷一份测试目录的最小副本，放进不隔离的模块
            shutil.copytree(HERE / "_isolation", Path(d) / "_isolation")
            shutil.copy(HERE / "test_isolation_guard.py", d)
            shutil.copy(BYPASS / "test_no_isolation.py", d)
            r = subprocess.run([sys.executable, "-B", "-m", "unittest",
                                "test_isolation_guard.Guard.test_every_test_module_imports_isolation_first"],
                               cwd=d, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("test_no_isolation.py", r.stderr)


class Isolation(unittest.TestCase):
    def test_isolation_ran_before_any_script_was_imported(self):
        self.assertEqual(ISO.PRELOADED_SCRIPTS, [])
        self.assertLessEqual(len(ISO.PRELOADED_TESTS), 1)          # discover 时为空；单跑一个文件时只有它自己

    def test_env_points_into_this_runs_temp_dir(self):
        self.assertFalse(inside(ISO.ROOT, ISO.REAL_HOME))
        for k in ("HOME", "AGENT_AUTOLOOP_HOME", "HERMES_HOME", "XDG_CONFIG_HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
                  "DT_GRANTS_STATUS", "DT_GRANTS_TASKS"):
            self.assertTrue(inside(os.environ[k], ISO.ROOT), k)
        self.assertEqual(Path.home(), ISO.HOME)
        self.assertEqual(os.path.expanduser("~"), str(ISO.HOME))
        self.assertEqual(os.environ["DT_HUB"], "http://127.0.0.1:9")

    def test_rewrites_real_home_and_prod_vars_without_outer_protection(self):
        real = str(ISO.REAL_HOME)
        env = {"PATH": os.environ["PATH"], "HOME": real, "AGENT_AUTOLOOP_HOME": real + "/.agent-autoloop",
               "HERMES_HOME": real + "/.hermes", "DT_OWNER_FILE": real + "/Library/Application Support/DigitalTwin/owner",
               "DT_HUB": "http://127.0.0.1:3350", "HERMES_SESSION_MESSAGE_ID": "om_fixture", "FEISHU_HOME_CHANNEL": "oc_x",
               "GH_TOKEN": "fixture", "DT_HUB_E2E": "0", "PYTHONPATH": str(HERE)}
        code = ("import _isolation, os, json, pathlib; print(json.dumps({'env': dict(os.environ), "
                "'home': str(pathlib.Path.home()), 'root': str(_isolation.ROOT)}))")
        r = subprocess.run([sys.executable, "-B", "-c", code], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        e, root = got["env"], got["root"]
        for k in ("HOME", "AGENT_AUTOLOOP_HOME", "HERMES_HOME"):
            self.assertTrue(e[k].startswith(root + "/"), k)
        self.assertTrue(got["home"].startswith(root + "/"))
        for k in ("DT_OWNER_FILE", "HERMES_SESSION_MESSAGE_ID", "FEISHU_HOME_CHANNEL", "GH_TOKEN"):
            self.assertNotIn(k, e)
        self.assertEqual(e["DT_HUB"], "http://127.0.0.1:9")
        self.assertEqual(e["DT_HUB_E2E"], "0")                     # 跑测试的人给的开关保留
        self.assertFalse(os.path.exists(root))                     # 进程退出时临时目录已清掉

    def test_external_connections_are_refused(self):
        with self.assertRaises(ISO.BlockedNetwork):
            socket.create_connection(("203.0.113.7", 443), timeout=1)
        with self.assertRaises(ISO.BlockedNetwork):
            socket.getaddrinfo("open.feishu.cn", 443)
        with self.assertRaises(ISO.BlockedNetwork):
            socket.gethostbyname("api.github.com")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s, self.assertRaises(ISO.BlockedNetwork):
            s.sendto(b"x", ("203.0.113.7", 53))
        with self.assertRaises(urllib.error.URLError) as cm:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open("https://open.feishu.cn/", timeout=1)
        self.assertIsInstance(cm.exception.reason, ISO.BlockedNetwork)

    def test_local_prod_service_ports_are_refused(self):
        for port in (3350, 3352, 8090):
            with socket.socket() as s, self.assertRaises(ISO.BlockedNetwork):
                s.connect(("127.0.0.1", port))

    def test_loopback_servers_started_by_tests_still_work(self):
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            with socket.create_connection(srv.getsockname(), timeout=5):
                conn, _ = srv.accept()
                conn.close()

    def test_children_get_dead_proxies_and_command_stubs(self):
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "https_proxy"):
            self.assertEqual(os.environ[k], "http://127.0.0.1:9")
        self.assertTrue(os.environ["PATH"].startswith(str(ISO.BIN) + os.pathsep))
        for name in ("launchctl", "security", "lark-cli", "hermes", "claude", "codex", "gh"):
            self.assertEqual(shutil.which(name), str(ISO.BIN / name), name)
        r = subprocess.run(["launchctl"], capture_output=True, text=True, timeout=10)
        self.assertEqual(r.returncode, 97)
        self.assertIn("测试隔离", r.stderr)

    def test_git_uses_temp_global_config(self):
        r = subprocess.run(["git", "config", "--global", "--get", "user.email"], capture_output=True, text=True,
                           timeout=10)
        self.assertEqual(r.stdout.strip(), "dt-test@example.invalid")

    def test_discover_isolates_even_a_module_that_forgot_the_first_line(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(HERE / "_isolation", Path(d) / "_isolation")
            (Path(d) / "test_forgot.py").write_text(
                "import os, unittest\n"
                "HOME_AT_IMPORT = os.environ['HOME']\n"
                "class T(unittest.TestCase):\n"
                "    def test_home(self):\n"
                "        self.assertTrue(HOME_AT_IMPORT.startswith(os.environ['DT_TEST_ISOLATION'] + '/'))\n",
                encoding="utf-8")
            env = {"PATH": os.environ["PATH"], "HOME": str(ISO.REAL_HOME)}      # 故意不给外层保护
            r = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", d],
                               env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Ran 1 test", r.stderr)


if __name__ == "__main__":
    unittest.main()
