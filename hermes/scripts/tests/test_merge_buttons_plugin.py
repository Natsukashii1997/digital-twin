"""PR 卡片按钮插件（#78）自测：只认合并官的按钮、参数原样转给 merge_act.py button、不回复。
不起网关：直接加载插件模块，Popen 换成记录器。"""
import importlib.util
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
        env = {"DT_SHADOW_DIR": self.tmp.name, "DT_MERGE_ACT": "/x/merge_act.py", "DT_CUSTODY_REPORT": "/x/custody_report.py", "DT_MERGE_PYTHON": "/x/python3",
               "HERMES_SESSION_CHAT_ID": "oc_home"}
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

    def test_registers_card_command(self):
        ctx = mock.Mock()
        self.mod.register(ctx)
        self.assertEqual(ctx.register_command.call_args[0][0], "card")


if __name__ == "__main__":
    unittest.main()
