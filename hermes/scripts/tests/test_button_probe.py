"""按钮实测探针（#195 批 0B-3）自测：发卡回读、登记、对照报告；不真发飞书。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class ButtonProbe(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"DT_PROBE_DIR": self.tmp.name, "FEISHU_HOME_CHANNEL": "oc_home",
                                                "DT_OWNER_OPEN_ID": "ou_owner"})
        self.env.start()
        sys.modules.pop("button_probe", None)
        import button_probe
        self.bp = button_probe
        self.sent = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fake_send(self, chat, msg_type, content, key):
        self.sent.append((chat, msg_type, content, key))
        return "om_card1"

    def fake_lark(self, args, what, cwd=None):
        content = json.dumps(self.sent[-1][2], ensure_ascii=False)
        return {"messages": [{"msg_type": "interactive", "content": content}]}

    def run_send(self, *args):
        with mock.patch.object(self.bp, "send", self.fake_send), mock.patch.object(self.bp, "lark", self.fake_lark):
            return self.bp.do_send(*args)

    def token(self):
        btn = self.sent[-1][2]["body"]["elements"][1]
        return btn["behaviors"][0]["value"]["probe"]

    def test_send_registers_token_and_button_only_carries_token(self):
        out = self.run_send(False)
        self.assertEqual((out["message_id"], out["registered"]), ("om_card1", True))
        self.assertEqual(self.sent[-1][0], "oc_home")
        value = self.sent[-1][2]["body"]["elements"][1]["behaviors"][0]["value"]
        self.assertEqual(set(value), {"probe"})
        self.assertRegex(value["probe"], r"^[0-9a-f]{16}$")
        rec = json.loads((Path(self.tmp.name) / "sent.jsonl").read_text())
        self.assertEqual((rec["token"], rec["message_id"]), (value["probe"], "om_card1"))
        self.assertEqual((Path(self.tmp.name) / "sent.jsonl").stat().st_mode & 0o777, 0o600)

    def test_unregistered_card_is_not_recorded(self):
        out = self.run_send(True)
        self.assertFalse(out["registered"])
        self.assertFalse((Path(self.tmp.name) / "sent.jsonl").exists())

    def test_readback_mismatch_fails_and_does_not_register(self):
        with mock.patch.object(self.bp, "send", self.fake_send), \
                mock.patch.object(self.bp, "lark", lambda *a, **k: {"messages": [{"msg_type": "text", "content": "x"}]}):
            with self.assertRaises(self.bp.SendError):
                self.bp.do_send(False)
        self.assertFalse((Path(self.tmp.name) / "sent.jsonl").exists())

    def test_no_home_chat_refuses(self):
        with mock.patch.object(self.bp, "home_chat", lambda: ""):
            with self.assertRaises(self.bp.SendError):
                self.run_send(False)
        self.assertEqual(self.sent, [])

    def test_report_matches_clicks_to_cards(self):
        self.run_send(False)
        tok = self.token()
        clicks = Path(self.tmp.name) / "clicks.jsonl"
        lines = [{"at": "t1", "token": tok, "operator": "ou_owner", "chat_id": "oc_home", "chat_type": "dm",
                  "message_id_prefix": "c-x", "message_id_len": 30},
                 {"at": "t2", "token": tok, "operator": "ou_other", "chat_id": "oc_group", "chat_type": "group"},
                 {"at": "t3", "token": "ffffffffffffffff", "operator": "ou_owner", "chat_id": "oc_home"}]
        clicks.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot-json\n")
        out = self.bp.report()
        self.assertEqual((out["registered_cards"], len(out["clicks"])), (1, 3))
        a, b, c = out["clicks"]
        self.assertEqual((a["card"], a["operator_is_owner"], a["chat_is_home"], a["nth_click"]), ("om_card1", True, True, 1))
        self.assertEqual((b["card"], b["operator_is_owner"], b["chat_is_home"], b["nth_click"]), ("om_card1", False, False, 2))
        self.assertEqual((c["card"], c["operator_is_owner"]), (None, True))

    def test_show_prints_plain_report(self):
        with mock.patch("sys.stdout") as out:
            self.assertEqual(self.bp.main(["show"]), 0)
        self.assertIn("登记的测试卡 0 张", "".join(str(x.args[0]) for x in out.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
