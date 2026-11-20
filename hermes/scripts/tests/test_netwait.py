"""netwait 自测（P9：常驻任务先等环境就绪）：闸门、重试预算、网络错误判断，外加「断网时触发入口，任务等待而不是失败」的端到端。

验证环境都是替身：探测地址换成本机端口（先不开、过一会儿再开），时钟和 sleep 用假的，不连真实网络、不发飞书。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import netwait  # noqa: E402

PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "evidence" / "netwait.jsonl"
        env = mock.patch.dict(os.environ, {"DT_NETWAIT_LOG": str(self.log), "DT_NET_WAIT_SECONDS": "300",
                                           "HERMES_HOME": str(Path(self.tmp.name) / "hermes")})
        env.start()
        self.addCleanup(env.stop)
        self.fc = FakeClock()

    def records(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def probe(self, answers):
        seq = iter(answers)
        return lambda url: next(seq)


class WaitReady(Base):
    def test_reachable_goes_straight_on(self):
        rec = netwait.gate("t", ("feishu",), probe=self.probe([(True, "")]), sleep=self.fc.sleep, clock=self.fc.clock)
        self.assertEqual(self.fc.sleeps, [])
        self.assertEqual((rec["ok"], rec["attempts"], rec["waited_s"], rec["error"]), (True, 1, 0, ""))
        r, = self.records()
        self.assertEqual((r["kind"], r["ok"], r["targets"]), ("t", True, ["feishu"]))

    def test_waits_until_reachable(self):
        answers = [(False, "URLError: refused")] * 2 + [(True, "")]
        rec = netwait.gate("t", ("feishu",), probe=self.probe(answers), sleep=self.fc.sleep, clock=self.fc.clock)
        self.assertEqual(self.fc.sleeps, [15, 15])
        self.assertEqual((rec["ok"], rec["attempts"], rec["waited_s"]), (True, 3, 30))

    def test_all_targets_must_pass_and_passed_ones_are_not_reprobed(self):
        seen = []

        def probe(url):
            seen.append(url)
            return (url == netwait.TARGETS["feishu"] or len(seen) > 3), "URLError: refused"
        rec = netwait.gate("t", ("feishu", "github"), probe=probe, sleep=self.fc.sleep, clock=self.fc.clock)
        self.assertTrue(rec["ok"])
        self.assertEqual(seen.count(netwait.TARGETS["feishu"]), 1)   # 通了的不再探
        self.assertEqual(seen.count(netwait.TARGETS["github"]), 3)

    def test_gives_up_at_limit_with_reason(self):
        with self.assertRaises(netwait.NotReady) as cm:
            netwait.gate("t", ("github",), probe=lambda u: (False, "URLError: refused"),
                         sleep=self.fc.sleep, clock=self.fc.clock)
        self.assertEqual(sum(self.fc.sleeps), 300)          # 不超过上限
        self.assertIn("300 秒没通", str(cm.exception))
        self.assertIn("github：URLError: refused", str(cm.exception))
        r, = self.records()
        self.assertEqual((r["ok"], r["attempts"], r["waited_s"]), (False, 21, 300))

    def test_zero_disables_without_probe_or_record(self):
        with mock.patch.dict(os.environ, {"DT_NET_WAIT_SECONDS": "0"}):
            rec = netwait.gate("t", ("feishu",), probe=lambda u: self.fail("不该探测"))
        self.assertTrue(rec["ok"])
        self.assertEqual(self.records(), [])

    def test_env_bad_value_means_default(self):
        for raw, want in (("abc", 300), ("-5", 0), ("60", 60)):
            with mock.patch.dict(os.environ, {"DT_NET_WAIT_SECONDS": raw}):
                self.assertEqual(netwait.wait_seconds(), want)
        with mock.patch.dict(os.environ):
            os.environ.pop("DT_NET_WAIT_SECONDS")
            self.assertEqual(netwait.wait_seconds(), 300)

    def test_log_write_failure_does_not_raise(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x", encoding="utf-8")
        with mock.patch.dict(os.environ, {"DT_NETWAIT_LOG": str(blocker / "sub" / "log.jsonl")}):
            netwait.gate("t", ("feishu",), probe=self.probe([(True, "")]))

    def test_record_has_no_env_values(self):
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://user:SECRETVALUE@127.0.0.1:1"}):
            with self.assertRaises(netwait.NotReady):
                netwait.gate("t", ("feishu",), probe=lambda u: (False, "URLError: refused"), limit=1,
                             sleep=self.fc.sleep, clock=self.fc.clock)
        self.assertNotIn("SECRETVALUE", self.log.read_text(encoding="utf-8"))


class Model(Base):
    def config(self, text):
        cfg = Path(os.environ["HERMES_HOME"]) / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(text, encoding="utf-8")

    def test_probe_url_follows_main_provider(self):
        cases = {
            "model:\n  default: claude-opus-5-5[1m]\n  provider: claude-subscription-directsdk-experimental\n": "https://api.anthropic.com/",
            "model:\n  default: gpt-6.1-sol\n  provider: openai-codex\n  base_url: https://chatgpt.com/backend-api/codex\n": "https://chatgpt.com/",
            "model:\n  provider: 'anthropic'\n": "https://api.anthropic.com/",
            "model:\n  provider: deepseek\nfallback_model:\n  provider: openai-codex\n": None,   # 只看 model 段
            "fallback_model:\n  provider: openai-codex\n": None,
        }
        with mock.patch.dict(os.environ, {"DT_MODEL_PROBE_URL": ""}):
            self.assertIsNone(netwait.model_url())   # 没有配置文件
            for text, want in cases.items():
                self.config(text)
                self.assertEqual(netwait.model_url(), want, text)
        with mock.patch.dict(os.environ, {"DT_MODEL_PROBE_URL": "http://127.0.0.1:9/"}):
            self.assertEqual(netwait.model_url(), "http://127.0.0.1:9/")   # 显式指定优先

    def test_unknown_provider_is_skipped_not_waited(self):
        self.config("model:\n  provider: deepseek\n")
        with mock.patch.dict(os.environ, {"DT_MODEL_PROBE_URL": ""}):
            rec = netwait.gate("t", ("model", "feishu"), probe=self.probe([(True, "")]), sleep=self.fc.sleep, clock=self.fc.clock)
        self.assertEqual((rec["ok"], rec["skipped"], self.fc.sleeps), (True, ["model"], []))

    def test_probe_classifies_responses(self):
        err = urllib.error.HTTPError("https://chatgpt.com/", 403, "Forbidden", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err):
            self.assertEqual(netwait.reachable("https://x.test/"), (True, ""))   # 有 HTTP 响应就是通
        for exc in (urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")), TimeoutError("timed out"),
                    OSError("Tunnel connection failed: 502 Bad Gateway"), RuntimeError("怪响应")):
            with mock.patch("urllib.request.urlopen", side_effect=exc):
                ok, why = netwait.reachable("https://x.test/")
                self.assertFalse(ok)
                self.assertIn(type(exc).__name__, why)


class Guard(Base):
    def guard(self, ok, quiet_hours=0):
        with mock.patch("builtins.print") as out:
            try:
                rec = netwait.guard("t", ("feishu",), "测试任务", quiet_hours=quiet_hours, limit=30,
                                    probe=lambda u: (ok, "" if ok else "URLError: refused"),
                                    sleep=self.fc.sleep, clock=self.fc.clock)
                return 0 if rec["ok"] else None, out
            except SystemExit as e:
                return e.code, out

    def test_ok_returns_quietly(self):
        code, out = self.guard(True)
        self.assertEqual(code, 0)
        out.assert_not_called()

    def test_not_ready_prints_reason_and_exits_1(self):
        code, out = self.guard(False)
        self.assertEqual(code, 1)
        text = out.call_args.args[0]
        self.assertTrue(text.startswith("⚠️ 测试任务没跑：网络 / 代理"), text)
        self.assertIn("feishu：URLError: refused", text)
        self.assertTrue(self.records()[-1]["reported"])

    def test_quiet_hours_reports_once_until_ok_again(self):
        self.assertEqual(self.guard(False, quiet_hours=6)[0], 1)
        code, out = self.guard(False, quiet_hours=6)
        self.assertEqual(code, 0)                                   # 已报过：只记录
        out.assert_not_called()
        self.assertFalse(self.records()[-1]["reported"])
        self.assertEqual(self.guard(True, quiet_hours=6)[0], 0)     # 通过一次
        self.assertEqual(self.guard(False, quiet_hours=6)[0], 1)    # 再不通重新报

    def test_quiet_hours_expire(self):
        old = (dt.datetime.now().astimezone() - dt.timedelta(hours=7)).isoformat(timespec="seconds")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        self.log.write_text(json.dumps({"at": old, "kind": "t", "ok": False, "reported": True}) + "\n", encoding="utf-8")
        self.assertEqual(self.guard(False, quiet_hours=6)[0], 1)


class BudgetAndErrors(unittest.TestCase):
    def test_budget_shared_from_first_failure(self):
        fc = FakeClock()
        b = netwait.Budget(60, sleep=fc.sleep, clock=fc.clock)
        self.assertEqual([b.retry() for _ in range(5)], [True, True, True, True, False])
        self.assertEqual(sum(fc.sleeps), 60)
        self.assertFalse(b.retry())                                 # 用完以后后面的调用不再等
        self.assertFalse(netwait.Budget(0, sleep=fc.sleep, clock=fc.clock).retry())

    def test_is_net_error_covers_all_old_sources(self):
        net = ["Post \"https://api.github.com/graphql\": read: connection reset by peer", "Post ...: EOF",
               "error connecting to api.github.com", "read: operation timed out", "TLS handshake timeout",
               "dial tcp: lookup open.feishu.cn: no such host", "could not resolve host",
               "token refresh request failed", "could not export … Retry the export", "network is unreachable",
               "SSL_ERROR_SYSCALL"]
        for text in net:
            self.assertTrue(netwait.is_net_error(text), text)
        for text in ("gh: Not Found (HTTP 404)", "panic: something else", "", None, "permission denied"):
            self.assertFalse(netwait.is_net_error(text), text)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Status403(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(403)
        self.end_headers()

    def log_message(self, *a):
        pass


class EntryWaitsWhenOffline(unittest.TestCase):
    """验收：断网（替身：探测地址是本机还没开的端口）时触发一次定时任务入口，任务等待而不是失败；一直不通才明确失败。

    用 skill_health.py 当入口：它开跑前过闸门，过了才调（假的）node 出体检结果。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        node = self.tmp / "node"
        node.write_text("#!/bin/sh\nprintf '# skill 月度体检\\n\\n- 一切正常\\n'\n", encoding="utf-8")
        node.chmod(0o755)
        self.port = free_port()
        self.env = {k: v for k, v in os.environ.items() if k not in PROXY_VARS}
        self.env.update({"no_proxy": "*", "NO_PROXY": "*", "HOME": str(self.tmp), "SKILL_HEALTH_NODE": str(node),
                         "MY_SKILLS_REPO": str(self.tmp), "DT_NETWAIT_LOG": str(self.tmp / "netwait.jsonl"),
                         "DT_PROBE_URL_FEISHU": f"http://127.0.0.1:{self.port}/"})

    def run_entry(self, wait):
        return subprocess.run([sys.executable, "-B", str(HERE.parent / "skill_health.py")], capture_output=True, text=True,
                              env={**self.env, "DT_NET_WAIT_SECONDS": str(wait)}, timeout=120)

    def log(self):
        return [json.loads(x) for x in (self.tmp / "netwait.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_offline_then_online_waits_and_runs(self):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", self.port))
        listener.listen(1)

        def bring_up():
            conn, _ = listener.accept()   # 第一次探测：连上就断，算不通（「唤醒后代理还没起来」）
            conn.close()
            listener.close()
            srv = HTTPServer(("127.0.0.1", self.port), Status403)
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
            srv.serve_forever()
        threading.Thread(target=bring_up, daemon=True).start()
        t = time.monotonic()
        r = self.run_entry(60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("skill 月度体检", r.stdout)
        self.assertNotIn("没跑", r.stdout)
        self.assertGreaterEqual(time.monotonic() - t, 14)          # 确实等了一轮
        rec, = self.log()
        self.assertEqual((rec["kind"], rec["ok"], rec["attempts"]), ("skill-health", True, 2))

    def test_build_jobs_record_reason_for_the_send_step(self):
        """写稿类入口（09:15 日报写稿、10:00 群报告取数）等不到网络：不写稿、不出声，原因记进产物，由发送那次报。"""
        dead = f"http://127.0.0.1:{self.port}/"
        env = {**self.env, "DT_NET_WAIT_SECONDS": "1", "DT_PROBE_URL_GITHUB": dead, "DT_MODEL_PROBE_URL": dead,
               "DT_DAILY": str(self.tmp / "daily"), "DT_GD_DIR": str(self.tmp / "gd")}
        for script, out in (("pdaily_build_job.py", self.tmp / "daily"), ("groupdigest_build_job.py", self.tmp / "gd")):
            r = subprocess.run([sys.executable, "-B", str(HERE.parent / script)], capture_output=True, text=True,
                               env=env, timeout=120)
            self.assertEqual((r.returncode, r.stdout), (0, ""), script + r.stderr)
            err, = out.glob("*error.json")
            self.assertIn("没通", json.loads(err.read_text(encoding="utf-8"))["error"])
            self.assertFalse(list(out.glob("*.card-fields.json")))          # 没往下写稿

    def test_offline_all_along_fails_loudly(self):
        r = self.run_entry(20)
        self.assertEqual(r.returncode, 1)
        self.assertIn("⚠️ skill 月度体检没跑：网络 / 代理", r.stdout)
        self.assertNotIn("一切正常", r.stdout)                       # 没通就没往下跑
        rec, = self.log()
        self.assertFalse(rec["ok"])


if __name__ == "__main__":
    unittest.main()
