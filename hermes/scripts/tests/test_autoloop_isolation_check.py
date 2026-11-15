"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

ops/autoloop_isolation_check.py（0A 隔离门反向测试）的判定逻辑。只用临时目录和打桩，不连中台、不联网、不读主账号文件。
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "ops/autoloop_isolation_check.py"
spec = importlib.util.spec_from_file_location("autoloop_isolation_check", SCRIPT)
iso = importlib.util.module_from_spec(spec)
spec.loader.exec_module(iso)


class FileChecks(unittest.TestCase):
    def test_readable_file_counts_as_not_blocked(self):
        with tempfile.NamedTemporaryFile() as f:
            self.assertEqual(iso.try_read(f.name)[0], iso.REACHABLE)

    def test_missing_path_counts_as_blocked(self):
        self.assertEqual(iso.try_read("/nonexistent/x/y")[0], iso.BLOCKED)

    @unittest.skipIf(os.geteuid() == 0, "root 读得到一切")
    def test_unreadable_file_counts_as_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "secret"
            p.write_text("x")
            p.chmod(0)
            try:
                self.assertEqual(iso.try_read(p)[0], iso.BLOCKED)
            finally:
                p.chmod(0o600)

    def test_append_to_missing_dir_is_blocked(self):
        self.assertEqual(iso.try_append("/nonexistent/x/rules.json")[0], iso.BLOCKED)


class HubChecks(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(iso.classify_http(401), iso.BLOCKED)
        self.assertEqual(iso.classify_http(403), iso.BLOCKED)
        self.assertEqual(iso.classify_http(200), iso.REACHABLE)
        self.assertEqual(iso.classify_http(500), iso.ERROR)
        self.assertEqual(iso.classify_http(404), iso.ERROR)

    def test_no_key_is_error_not_pass(self):
        out = iso.hub_checks(None)
        self.assertEqual([i["result"] for i in out], [iso.ERROR])

    def test_all_rejected(self):
        calls = []
        out = iso.hub_checks("k", call=lambda m, p, k, b: calls.append((m, p)) or 401)
        self.assertEqual({i["result"] for i in out}, {iso.BLOCKED})
        self.assertIn(("POST", "/v1/cards/1/answer"), calls)
        self.assertTrue(any(p.endswith("/run") for _, p in calls))

    def test_one_accepted_fails(self):
        out = iso.hub_checks("k", call=lambda m, p, k, b: 200 if p == "/v1/state" else 401)
        self.assertEqual(iso.summarize(out)["not_blocked"], 1)

    def test_read_worker_key(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".agent-autoloop").mkdir()
            (Path(d) / ".agent-autoloop/config.json").write_text('{"worker": {"name": "w", "key": "abc"}}')
            self.assertEqual(iso.read_worker_key(d), "abc")
            self.assertIsNone(iso.read_worker_key("/nonexistent"))


class ServiceChecks(unittest.TestCase):
    NETSTAT = """Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)
tcp4       0      0  127.0.0.1.3350         *.*                    LISTEN
tcp4       0      0  127.0.0.1.5280         *.*                    LISTEN
tcp46      0      0  *.8086                 *.*                    LISTEN
tcp6       0      0  ::1.5178               *.*                    LISTEN
tcp4       0      0  127.0.0.1.5280         127.0.0.1.60001        ESTABLISHED
"""

    def test_listening_ports(self):
        self.assertEqual(iso.listening_ports(self.NETSTAT), [3350, 5178, 5280, 8086])

    def test_hub_and_proxy_ports_are_allowed(self):
        out = iso.service_checks([3350, 8086, 1080], None,
                                 connect=lambda h, p: True, via_proxy=lambda pp, h, p: (True, ""))
        self.assertEqual({i["result"] for i in out}, {iso.BLOCKED})

    def test_other_reachable_port_fails(self):
        out = iso.service_checks([3350, 5280], "10.0.0.2",
                                 connect=lambda h, p: p == 5280, via_proxy=lambda pp, h, p: (False, ""))
        direct = [i for i in out if i["name"].startswith("直连")]
        self.assertEqual(len(direct), 2)
        self.assertTrue(all(i["result"] == iso.REACHABLE for i in direct))
        self.assertIn("5280", direct[0]["detail"])

    def test_no_ports_read_is_error_not_pass(self):
        out = iso.service_checks([], None, connect=lambda h, p: False, via_proxy=lambda pp, h, p: (False, ""))
        self.assertEqual([i["result"] for i in out], [iso.ERROR])

    def test_run_keeps_full_output_when_asked(self):
        code, text = iso.run(["/bin/sh", "-c", "printf '%0500d' 0"], limit=None)
        self.assertEqual(len(text), 500)
        self.assertEqual(len(iso.run(["/bin/sh", "-c", "printf '%0500d' 0"])[1]), 300)

    def test_proxy_loopback_fails(self):
        out = iso.service_checks([5280], None,
                                 connect=lambda h, p: False, via_proxy=lambda pp, h, p: (True, "HTTP/1.1 200"))
        self.assertEqual([i["result"] for i in out], [iso.BLOCKED, iso.REACHABLE])


class ExecChecks(unittest.TestCase):
    def test_writable_dir_fails(self):
        with tempfile.TemporaryDirectory() as d:
            out = iso.exec_checks(dirs=[d], shared=Path("/nonexistent"))
            self.assertEqual(out[0]["result"], iso.REACHABLE)
            self.assertEqual(out[1]["result"], iso.BLOCKED)


class Summary(unittest.TestCase):
    def test_errors_do_not_pass(self):
        s = iso.summarize([iso.item("g", "n", iso.BLOCKED), iso.item("g", "m", iso.ERROR)])
        self.assertFalse(s["passed"])
        self.assertEqual(s["errors"], 1)

    def test_refuses_main_account(self):
        self.assertEqual(iso.main(["--main-home", str(Path.home())]), 2)


if __name__ == "__main__":
    unittest.main()
