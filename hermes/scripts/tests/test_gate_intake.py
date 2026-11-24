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
RID = "0123456789abcdef0123456789abcdef"


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
        if script == "autoloop_grants.py" and args[:1] == ["propose"]:
            f = Path(args[args.index("--scope-file") + 1])
            seen["scope"] = f.read_text(encoding="utf-8")
            seen["mode"] = stat.S_IMODE(f.stat().st_mode)
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

    def test_dispatch_proposal_goes_to_grants_propose(self):
        """0E-3b：派单提议交网关 propose（只登记，确认卡由门卫发）；五要素写成私有临时文件，用完删掉。"""
        self.reply = (0, {"code": "abcd1234", "card": "门卫会把确认卡发到常浩私聊"})
        body = json.dumps({"repo": "dt-sandbox", "executor": "codex", "scope": "目标：改 README", "hours": 6,
                           "request_id": RID}).encode()
        code, res = post(self.url + "/v1/dispatch/proposal", body)
        self.assertEqual((code, res), (200, {"code": 0, "result": self.reply[1]}))
        (c,) = self.calls
        self.assertEqual(c["script"], "autoloop_grants.py")
        f = c["args"][c["args"].index("--scope-file") + 1]
        self.assertEqual(c["args"], ["propose", "--repo", "dt-sandbox", "--executor", "codex", "--scope-file", f, "--hours", "6",
                                     "--request-id", RID])
        self.assertEqual((c["scope"], c["mode"]), ("目标：改 README", 0o600))
        self.assertEqual(list((self.d / "inbox").iterdir()), [])

    def test_bad_dispatch_proposal_refused(self):
        good = {"repo": "dt-sandbox", "executor": "claude", "scope": "目标", "request_id": RID}
        for bad in ({k: v for k, v in good.items() if k != "request_id"}, {**good, "request_id": "ABC"},
                    {**good, "request_id": "a" * 15}, {**good, "request_id": 123}, {**good, "repo": "../x"}, {**good, "executor": "gpt"}, {**good, "scope": ""}, {**good, "scope": "长" * 3001},
                    {**good, "hours": "12"}, {**good, "approve": True}, [good]):
            with self.subTest(bad=bad):
                self.assertEqual(post(self.url + "/v1/dispatch/proposal", json.dumps(bad, ensure_ascii=False).encode())[0], 400)
        self.assertEqual(self.calls, [])

    def test_tool_output_parsed_whole_or_last_line(self):
        self.assertEqual(I.last_json('{\n "ok": false,\n "why": "x"\n}\n'), {"ok": False, "why": "x"})   # 网关按缩进打印多行
        self.assertEqual(I.last_json('提示一行\n{"code": "a"}\n'), {"code": "a"})
        self.assertIsNone(I.last_json("不是 JSON"))
        self.assertIsNone(I.last_json(""))

    def test_client_posts_and_maps_exit_codes(self):
        f = self.d / "intake.token"
        f.write_text(TOKEN)
        os.chmod(f, 0o600)
        with mock.patch.dict(os.environ, {"DT_INTAKE_TOKEN_FILE": str(f), "DT_INTAKE_URL": self.url}):
            self.reply = (0, {"code": "abcd1234"})
            code, out = I.propose_dispatch("dt-sandbox", "claude", "目标")
            self.assertEqual((code, out["code"]), (0, "abcd1234"))
            self.assertRegex(out["request_id"], r"^[0-9a-f]{32}$")                          # 没给就新生成，输出里带着
            self.assertEqual(I.propose_dispatch("dt-sandbox", "claude", "目标", request_id=RID),
                             (0, {"code": "abcd1234", "request_id": RID}))
            self.assertEqual(self.calls[-1]["args"][-2:], ["--request-id", RID])         # 给了就原样交给网关
            self.reply = (1, {"ok": False, "why": "dt-x 不在名册能派写入单的仓库里"})
            self.assertEqual(I.propose_dispatch("dt-x", "claude", "目标", request_id=RID), (1, {**self.reply[1], "request_id": RID}))
            self.assertEqual(I.propose_dispatch("../x", "claude", "目标")[0], 1)           # 窗口拒收形状不对的
            self.reply = (None, None)
            self.assertEqual(I.propose_dispatch("dt-sandbox", "claude", "目标")[0], 2)      # 控制面超时：结果不明
        with mock.patch.dict(os.environ, {"DT_INTAKE_TOKEN_FILE": str(f), "DT_INTAKE_URL": "http://127.0.0.1:9"}):
            code, out = I.propose_dispatch("dt-sandbox", "claude", "目标", request_id=RID)
            self.assertEqual((code, out["request_id"]), (2, RID))                          # 结果不明：带同一个请求号再跑
            self.assertIn("结果不明", out["why"])

    def test_only_four_paths_and_valid_task_ids(self):
        for path in ("/v1/tasks/../revoke", "/v1/tasks/t-1/revoke", "/v1/secretary/on", "/v1/answer", "/v1/tasks/t-20261120-abc123/approve",
                     "/v1/dispatch/approve", "/v1/dispatch/proposal/x"):
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

    def test_no_lsof_means_not_ours(self):
        """查不了（没有 lsof、出错）一律当不是：不发钥匙。"""
        with mock.patch.object(HL, "LSOF", "/nonexistent/lsof"):
            with self.assertRaises(HL.NotOurListener):
                HL.check("http://127.0.0.1:3350", want="503")

    def test_real_lsof_sees_our_own_listener(self):
        """真 lsof、真监听：正式环境（macOS）用 /usr/sbin/lsof，CI（Ubuntu）注入系统的 /usr/bin/lsof，两边都真跑，不跳过
        （L3 第 1 轮 P2-01）。找不到 lsof 判失败，不当通过。"""
        import shutil
        import socket
        lsof = shutil.which("lsof", path="/usr/sbin:/usr/bin:/sbin:/bin")
        self.assertIsNotNone(lsof, "本机没有 lsof：监听核对测不了（CI 里由 ci.yml 装上）")
        with socket.socket() as srv, mock.patch.object(HL, "LSOF", lsof):
            srv.bind(("127.0.0.1", 0))
            srv.listen()
            port = srv.getsockname()[1]
            self.assertIn(os.getuid(), HL.listener_uids(port))
            HL.check(f"http://127.0.0.1:{port}", want=str(os.getuid()))
            with self.assertRaises(HL.NotOurListener):
                HL.check(f"http://127.0.0.1:{port}", want=str(os.getuid() + 1))
        with self.assertRaises(HL.NotOurListener):                       # 没人在听：不放行
            with mock.patch.object(HL, "LSOF", lsof):
                HL.check(f"http://127.0.0.1:{port}", want=str(os.getuid()))

    def test_macos_uses_the_system_lsof(self):
        self.assertEqual(HL.LSOF, "/usr/sbin/lsof")
