"""秘书窄窗口（ADR-015 批 0E-2）自测：hermes/scripts/gate_intake.py，以及秘书巡视经 dt-gate 两个窗口看卡、交提议。

窗口在本进程里起（随机端口），控制面脚本换成记录参数的假工具；巡视那边用真的 secretary_patrol.submit / read_snapshot。
不碰正式的 3352、3353 和正式目录。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import autoloop_readonly as R  # noqa: E402
import gate_intake as I  # noqa: E402
import hub_listener as HL  # noqa: E402
import secretary_patrol as SP  # noqa: E402

TOKEN = "t" * 40


def post(url, body=b"{}", token=TOKEN, headers=None):
    h = {"Authorization": f"Bearer {token}", **(headers or {})}
    req = urllib.request.Request(url, data=body, method="POST", headers=h)
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-intake-")
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        self.calls = []
        self.reply = (0, {"result": "handoff", "decision": "d-1"})
        self.slow = None
        p = mock.patch.dict(os.environ, {"DT_INTAKE_INBOX": str(self.d / "inbox")})
        p.start()
        self.addCleanup(p.stop)
        import http.server
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), I.make_handler(TOKEN, self.tool))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tool(self, script, args):
        seen = {"script": script, "args": list(args)}
        if script == "secretary_gate.py":
            f = Path(args[2])
            seen["body"] = f.read_bytes()
            seen["mode"] = stat.S_IMODE(f.stat().st_mode)
            if self.slow:
                self.slow.wait(10)
        self.calls.append(seen)
        return self.reply


class IntakeTest(Base):
    def test_token_required(self):
        self.assertEqual(post(self.url + "/v1/secretary/off", token="wrong")[0], 401)
        self.assertEqual(self.calls, [])

    def test_proposal_goes_to_gate_as_private_file_then_removed(self):
        body = json.dumps({"schema": 1, "category": "ask", "card": 3, "card_version": 1}).encode()
        code, res = post(self.url + "/v1/secretary/proposal", body)
        self.assertEqual((code, res), (200, {"code": 0, "result": {"result": "handoff", "decision": "d-1"}}))
        (c,) = self.calls
        self.assertEqual((c["script"], c["args"][:2], c["body"], c["mode"]), ("secretary_gate.py", ["submit", "--file"], body, 0o600))
        self.assertEqual(list((self.d / "inbox").iterdir()), [])
        self.assertEqual(stat.S_IMODE((self.d / "inbox").stat().st_mode), 0o700)

    def test_bad_or_big_proposal_refused(self):
        self.assertEqual(post(self.url + "/v1/secretary/proposal", b"not json")[0], 400)
        self.assertEqual(post(self.url + "/v1/secretary/proposal", b"[1]")[0], 400)
        self.assertEqual(post(self.url + "/v1/secretary/proposal", b"x" * (I.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.calls, [])

    def test_off_and_revoke(self):
        self.assertEqual(post(self.url + "/v1/secretary/off")[0], 200)
        self.assertEqual(post(self.url + "/v1/tasks/t-20261120-abc123/revoke", json.dumps({"reason": "不做了"}).encode())[0], 200)
        self.assertEqual(self.calls[0], {"script": "secretary.py", "args": ["off", "--source", I.SOURCE]})
        self.assertEqual(self.calls[1]["args"], ["revoke", "--task", "t-20261120-abc123", "--reason", f"{I.SOURCE}：不做了"])

    def test_only_three_paths_and_valid_task_ids(self):
        for path in ("/v1/tasks/../revoke", "/v1/tasks/t-1/revoke", "/v1/secretary/on", "/v1/answer", "/v1/tasks/t-20261120-abc123/approve"):
            with self.subTest(path=path):
                self.assertEqual(post(self.url + path)[0], 404)
        self.assertEqual(post(self.url + "/v1/tasks/t-20261120-abc123/revoke", json.dumps({"reason": "长" * 201}).encode())[0], 400)
        self.assertEqual(self.calls, [])

    def test_off_does_not_wait_behind_a_slow_proposal(self):
        """关秘书、撤销单另排一队：提议卡着时照样马上办（独立审查 P2-14）。"""
        self.slow = threading.Event()
        t = threading.Thread(target=post, args=(self.url + "/v1/secretary/proposal", b"{}"))
        t.start()
        self.addCleanup(t.join)
        self.addCleanup(self.slow.set)
        for _ in range(50):
            if any(f.name.startswith("p-") for f in (self.d / "inbox").glob("*")):
                break
            threading.Event().wait(0.05)
        self.assertEqual(post(self.url + "/v1/secretary/off")[0], 200)
        self.assertEqual(self.calls[0]["script"], "secretary.py")
        self.slow.set()

    def test_get_refused(self):
        req = urllib.request.Request(self.url + "/v1/secretary/off", headers={"Authorization": f"Bearer {TOKEN}"})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)
        self.assertEqual(e.exception.code, 405)

    def test_timeout_is_unknown(self):
        self.reply = (None, None)
        self.assertEqual(post(self.url + "/v1/secretary/off")[0], 504)

    def test_token_file_must_be_private(self):
        f = self.d / "intake.token"
        f.write_text(TOKEN)
        os.chmod(f, 0o644)
        with mock.patch.dict(os.environ, {"DT_INTAKE_TOKEN_FILE": str(f)}):
            with self.assertRaises(I.Refused):
                I.load_token()
            os.chmod(f, 0o600)
            self.assertEqual(I.load_token(), TOKEN)
            f.write_text("short")
            with self.assertRaises(I.Refused):
                I.load_token()


class PatrolThroughWindows(Base):
    """控制面在 dt-gate 以后，巡视（主账号）经只读窗口看卡、经窄窗口交提议；不设这两个变量时照旧（另见 test_secretary_patrol）。"""
    CARD = {"id": 7, "version": 1, "kind": "ask", "state": "open"}

    def setUp(self):
        super().setUp()
        (self.d / "tok").mkdir()
        (self.d / "tok/intake.token").write_text(TOKEN + "\n")
        (self.d / "tok/readonly.token").write_text("r" * 40 + "\n")
        p = mock.patch.dict(os.environ, {"DT_SECRETARY_INTAKE": self.url, "DT_SECRETARY_DIR": str(self.d / "secretary"),
                                         "DT_SECRETARY_INTAKE_TOKEN_FILE": str(self.d / "tok/intake.token"),
                                         "DT_READONLY_TOKEN_FILE": str(self.d / "tok/readonly.token")})
        p.start()
        self.addCleanup(p.stop)

    def test_submit_goes_through_intake(self):
        out = SP.submit({"schema": 1, "category": "ask", "card": 7, "card_version": 1}, "ask", self.CARD)
        self.assertEqual(out["result"], "handoff")
        self.assertEqual(json.loads(self.calls[0]["body"])["card"], 7)
        self.assertEqual(list((self.d / "secretary/outbox").iterdir()), [])

    def test_intake_down_or_garbled_is_unreachable_and_file_kept(self):
        self.reply = (1, {"result": "maybe"})
        self.assertEqual(SP.submit({"category": "ask"}, "ask", self.CARD)["result"], "unreachable")
        with mock.patch.dict(os.environ, {"DT_SECRETARY_INTAKE": "http://127.0.0.1:9"}):
            self.assertEqual(SP.submit({"category": "ask"}, "ask", self.CARD)["result"], "unreachable")
        self.assertEqual(len(list((self.d / "secretary/outbox").iterdir())), 2)

    def test_wrong_token_is_unreachable(self):
        (self.d / "tok/intake.token").write_text("x" * 40)
        self.assertEqual(SP.submit({"category": "ask"}, "ask", self.CARD)["result"], "unreachable")
        self.assertEqual(self.calls, [])

    def test_reads_cards_through_readonly_window(self):
        import http.server
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), R.make_handler("r" * 40, lambda: {"cards": [self.CARD]}))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with mock.patch.dict(os.environ, {"DT_READONLY_URL": f"http://127.0.0.1:{srv.server_address[1]}"}), \
                mock.patch.object(R, "read", side_effect=AssertionError("不该本机读中台")):
            self.assertEqual(SP.candidates(), [("ask", self.CARD)])


if __name__ == "__main__":
    unittest.main()


class ListenerCheck(unittest.TestCase):
    """dt-gate 带钥匙访问本机端口前核对监听者（hub_listener，独立审查 P1-2）。"""

    def test_off_without_setting(self):
        with mock.patch.dict(os.environ, {"DT_HUB_LISTENER_UID": ""}), \
                mock.patch.object(HL, "listener_uids", side_effect=AssertionError("不该查")):
            HL.check("http://127.0.0.1:3350")

    def test_only_the_expected_uid_passes(self):
        with mock.patch.object(HL, "listener_uids", return_value=[503]):
            HL.check("http://127.0.0.1:3350", want="503")
        for uids in ([501], [503, 501], []):
            with self.subTest(uids=uids), mock.patch.object(HL, "listener_uids", return_value=uids):
                with self.assertRaises(HL.NotOurListener):
                    HL.check("http://127.0.0.1:3350", want="503")
        self.assertTrue(issubclass(HL.NotOurListener, OSError))           # 调用方都按「连不上」处理

    def test_only_127_0_0_1_when_checking(self):
        """设了核对时别的地址一律不发钥匙：localhost 可能先连 ::1，127.0.0.2 / 外部地址这边核不了（复核 P2-e）。"""
        with mock.patch.object(HL, "listener_uids", return_value=[503]):
            for url in ("http://localhost:3350", "http://[::1]:3350", "http://127.0.0.2:3350", "https://hub.example.invalid"):
                with self.subTest(url=url), self.assertRaises(HL.NotOurListener):
                    HL.check(url, want="503")

    def test_real_lsof_sees_our_own_listener(self):
        import socket
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen()
            port = srv.getsockname()[1]
            self.assertIn(os.getuid(), HL.listener_uids(port))
            HL.check(f"http://127.0.0.1:{port}", want=str(os.getuid()))
            with self.assertRaises(HL.NotOurListener):
                HL.check(f"http://127.0.0.1:{port}", want=str(os.getuid() + 1))
