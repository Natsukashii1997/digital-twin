"""PR 卡片按钮插件（#78）自测：只认合并官的按钮、参数原样转给 merge_act.py button、不回复。
不起网关：直接加载插件模块，Popen 换成记录器。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "merge-buttons" / "__init__.py"


def load():
    spec = importlib.util.spec_from_file_location("merge_buttons_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class MergeButtonsPlugin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"DT_SHADOW_DIR": self.tmp.name, "DT_MERGE_ACT": "/x/merge_act.py", "DT_CUSTODY_REPORT": "/x/custody_report.py", "DT_AUTOLOOP_ANSWER": "/x/autoloop_answer.py", "DT_PDAILY_ACT": "/x/pdaily_act.py", "DT_MERGE_PYTHON": "/x/python3",
               "HERMES_SESSION_CHAT_ID": "oc_home", "DT_PROBE_DIR": self.tmp.name + "/probe",
               "HERMES_SESSION_USER_ID": "ou_owner", "HERMES_SESSION_USER_ID_ALT": "on_owner", "HERMES_SESSION_USER_NAME": "常浩",
               "HERMES_SESSION_CHAT_TYPE": "dm", "HERMES_SESSION_PLATFORM": "feishu", "HERMES_SESSION_MESSAGE_ID": "c-callbacktoken123"}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        self.mod = load()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def click(self, raw):
        with mock.patch.object(self.mod.subprocess, "Popen") as popen:
            out = self.mod.handle(raw)
        return out, popen

    def test_merge_button_starts_merge_act_in_background(self):
        out, popen = self.click('button {"merge_officer": "merge", "ref": "trainee-hub#31@1cc8e02"}')
        self.assertIsNone(out)                                   # 不回复：结果由 merge_act 发
        args, kw = popen.call_args
        self.assertEqual(args[0], ["/x/python3", "/x/merge_act.py", "button", "merge", "trainee-hub#31@1cc8e02",
                                   "--chat", "oc_home"])
        self.assertTrue(kw["start_new_session"])
        self.assertIn("merge trainee-hub#31@1cc8e02", (Path(self.tmp.name) / "buttons.log").read_text())

    def test_return_button(self):
        _, popen = self.click('button {"merge_officer": "return", "ref": "digital-twin#79@abcdef1"}')
        self.assertEqual(popen.call_args[0][0][2:5], ["button", "return", "digital-twin#79@abcdef1"])

    def test_custody_buttons_start_custody_report(self):
        for verb in ("overturn", "merge_revert"):
            out, popen = self.click('button {"custody": "%s", "ref": "d-261115-3fa2c1"}' % verb)
            self.assertIsNone(out)
            self.assertEqual(popen.call_args[0][0], ["/x/python3", "/x/custody_report.py", "button", verb, "d-261115-3fa2c1",
                                                     "--chat", "oc_home"])

    def test_custody_card_token_passed_through(self):
        _, popen = self.click('button {"custody": "overturn", "ref": "d-261115-3fa2c1", "card": "0123456789abcdef"}')
        self.assertEqual(popen.call_args[0][0][-2:], ["--card", "0123456789abcdef"])
        for bad in ('"0123"', '"0123456789ABCDEF"', '"0123456789abcdef --x"', '12', '""'):
            out, popen = self.click('button {"custody": "overturn", "ref": "d-261115-3fa2c1", "card": %s}' % bad)
            popen.assert_not_called()

    def test_ignores_bad_custody_clicks(self):
        for raw in ('button {"custody": "delete", "ref": "d-261115-3fa2c1"}', 'button {"custody": "overturn", "ref": "d-1; rm -rf /"}',
                    'button {"custody": "overturn", "ref": "trainee-hub#31@1cc8e02"}', 'button {"custody": "overturn"}'):
            out, popen = self.click(raw)
            self.assertIsNone(out)
            popen.assert_not_called()

    def test_ignores_other_clicks(self):
        for raw in ("", "button", "button not-json", 'button ["x"]', 'select {"merge_officer": "merge", "ref": "a#1@abcdef1"}',
                    'button {"hermes_action": "deny"}', 'button {"merge_officer": "delete", "ref": "a#1@abcdef1"}',
                    'button {"merge_officer": "merge", "ref": "a#1@abc; rm -rf /"}'):
            out, popen = self.click(raw)
            self.assertIsNone(out)
            popen.assert_not_called()

    def test_probe_records_session_without_running_anything(self):
        out, popen = self.click('button {"probe": "0123456789abcdef"}')
        self.assertIsNone(out)
        popen.assert_not_called()
        path = Path(self.tmp.name) / "probe" / "clicks.jsonl"
        rec = json.loads(path.read_text().splitlines()[0])
        self.assertEqual((rec["token"], rec["operator"], rec["operator_alt"], rec["chat_id"], rec["chat_type"]),
                         ("0123456789abcdef", "ou_owner", "on_owner", "oc_home", "dm"))
        self.assertEqual((rec["message_id_prefix"], rec["message_id_len"]), ("c-c", 18))
        self.assertNotIn("callbacktoken", path.read_text())          # 回调 token 不落原文
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_probe_rejects_bad_tokens_and_extra_keys(self):
        for raw in ('button {"probe": "0123"}', 'button {"probe": "0123456789ABCDEF"}', 'button {"probe": 12}',
                    'button {"probe": "0123456789abcdef", "merge_officer": "merge", "ref": "a#1@abcdef1"}',
                    'select {"probe": "0123456789abcdef"}'):
            out, popen = self.click(raw)
            self.assertIsNone(out)
            popen.assert_not_called()
        self.assertFalse((Path(self.tmp.name) / "probe" / "clicks.jsonl").exists())

    def test_autoloop_click_forwards_token_option_version_chat_and_operator(self):
        out, popen = self.click('button {"autoloop": "0123456789abcdef", "o": 1, "v": 3}')
        self.assertIsNone(out)
        self.assertEqual(popen.call_args[0][0], ["/x/python3", "/x/autoloop_answer.py", "0123456789abcdef", "1",
                                                 "--version", "3", "--chat", "oc_home", "--operator", "ou_owner"])
        self.assertTrue(popen.call_args[1]["start_new_session"])
        self.assertNotIn("0123456789abcdef", (Path(self.tmp.name) / "buttons.log").read_text())   # 日志不记令牌

    def test_autoloop_old_button_without_version_forwarded_without_version(self):
        """#218 第 3 轮 P1-03：旧样式按钮（没有 v）照样转，不带 --version，由脚本拒绝并在私聊说明。"""
        out, popen = self.click('button {"autoloop": "0123456789abcdef", "o": 1}')
        self.assertIsNone(out)
        self.assertEqual(popen.call_args[0][0], ["/x/python3", "/x/autoloop_answer.py", "0123456789abcdef", "1",
                                                 "--chat", "oc_home", "--operator", "ou_owner"])

    def test_autoloop_rejects_bad_values(self):
        for raw in ('button {"autoloop": "0123", "o": 1}', 'button {"autoloop": "0123456789abcdef", "o": "1"}',
                    'button {"autoloop": "0123456789abcdef", "o": true}', 'button {"autoloop": "0123456789abcdef", "o": -1}',
                    'button {"autoloop": "0123456789abcdef", "o": 100}', 'button {"autoloop": "0123456789abcdef"}',
                    'button {"autoloop": "0123456789abcdef", "o": 1, "by": "ou_x"}',
                    'button {"autoloop": "0123456789abcdef --x", "o": 1}',
                    'button {"autoloop": "0123456789abcdef", "o": 1, "v": "2"}',
                    'button {"autoloop": "0123456789abcdef", "o": 1, "v": true}',
                    'button {"autoloop": "0123456789abcdef", "o": 1, "v": 2.0}',
                    'button {"autoloop": "0123456789abcdef", "o": 1, "v": 0}',
                    'button {"autoloop": "0123456789abcdef", "o": 1, "v": null}',
                    'button {"autoloop": "0123456789abcdef", "o": 1, "v": 2, "by": "ou_x"}'):
            out, popen = self.click(raw)
            self.assertIsNone(out)
            popen.assert_not_called()

    def pdaily_click(self, raw, out, inject=True):
        """日报按钮：脚本同步跑（换成假结果），注入换成记录器，后台的 after / say 记在 Popen 上。"""
        ctx = mock.Mock()
        ctx.inject_message.return_value = inject
        self.mod.register(ctx)
        done = mock.Mock(stdout=json.dumps(out, ensure_ascii=False) + "\n")
        with mock.patch.object(self.mod.subprocess, "run", return_value=done) as run, \
                mock.patch.object(self.mod.subprocess, "Popen") as popen:
            ret = self.mod.handle(raw)
        return ret, run, popen, ctx

    def test_pdaily_click_records_then_injects_then_refreshes(self):
        ok = {"ok": True, "msg": "记下", "day": "2026-11-18", "entries": ["p-261118-2-ab12"], "inject": "【日报按钮】……"}
        ret, run, popen, ctx = self.pdaily_click('button {"pdaily": "0123456789abcdef", "n": 2}', ok)
        self.assertIsNone(ret)
        self.assertEqual(run.call_args[0][0], ["/x/python3", "/x/pdaily_act.py", "button", "0123456789abcdef", "2",
                                               "--chat", "oc_home", "--operator", "ou_owner"])
        self.assertEqual(ctx.inject_message.call_args[0][0], "【日报按钮】……")
        self.assertEqual(ctx.inject_message.call_args[1]["origin"],
                         {"platform": "feishu", "chat_id": "oc_home", "chat_type": "dm", "user_id": "ou_owner"})
        self.assertEqual(popen.call_args[0][0], ["/x/python3", "/x/pdaily_act.py", "after", "--day", "2026-11-18",
                                                 "--entries", "p-261118-2-ab12", "--injected", "1"])

    def test_pdaily_injection_not_allowed_still_records(self):
        ok = {"ok": True, "msg": "记下", "day": "2026-11-18", "entries": ["p-1"], "inject": "x"}
        _, _, popen, _ = self.pdaily_click('button {"pdaily": "0123456789abcdef", "n": 0}', ok, inject=False)
        self.assertEqual(popen.call_args[0][0][-2:], ["--injected", "0"])

    def test_pdaily_refused_or_failed(self):
        _, _, popen, ctx = self.pdaily_click('button {"pdaily": "0123456789abcdef", "n": 1}', {"ok": False, "msg": "不认"})
        popen.assert_not_called()                                 # 不是常浩：不回话
        ctx.inject_message.assert_not_called()
        _, _, popen, ctx = self.pdaily_click('button {"pdaily": "0123456789abcdef", "n": 1}',
                                             {"ok": False, "msg": "这不是最新一张日报卡片上的按钮"})
        self.assertEqual(popen.call_args[0][0][2:], ["say", "--text", "这不是最新一张日报卡片上的按钮"])
        ctx.inject_message.assert_not_called()
        _, _, popen, ctx = self.pdaily_click('button {"pdaily": "0123456789abcdef", "n": 1}',
                                             {"ok": True, "msg": "定过了", "day": "x", "entries": []})
        popen.assert_not_called()
        ctx.inject_message.assert_not_called()

    def test_pdaily_rejects_bad_values(self):
        for raw in ('button {"pdaily": "0123", "n": 1}', 'button {"pdaily": "0123456789abcdef", "n": "1"}',
                    'button {"pdaily": "0123456789abcdef", "n": true}', 'button {"pdaily": "0123456789abcdef", "n": 21}',
                    'button {"pdaily": "0123456789abcdef", "n": -1}', 'button {"pdaily": "0123456789abcdef"}',
                    'button {"pdaily": "0123456789abcdef", "n": 1, "by": "ou_x"}'):
            _, run, popen, ctx = self.pdaily_click(raw, {"ok": True})
            run.assert_not_called()
            popen.assert_not_called()
            ctx.inject_message.assert_not_called()

    def test_registers_card_command(self):
        ctx = mock.Mock()
        self.mod.register(ctx)
        self.assertEqual(ctx.register_command.call_args[0][0], "card")


if __name__ == "__main__":
    unittest.main()
