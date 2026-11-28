# -*- coding: utf-8 -*-
"""托管单回话不进猛鬼兽的网关插件（#305，常浩 11-22 选甲）自测。不起网关：直接加载插件，插件里的 away_sheet
换成仓库这份、账本和身份目录指到临时目录；账本用托管单测试那套假飞书真推出来（卡片和提醒都有消息号）。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import away_sheet as A  # noqa: E402
from test_away_sheet import ASK, CHAT, FAKE, OWNER  # noqa: E402

PLUGIN = SCRIPTS.parent / "plugins" / "away-sheet-mute" / "__init__.py"


def load_plugin():
    spec = importlib.util.spec_from_file_location("away_sheet_mute_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def event(quoted, chat=CHAT, user=OWNER, platform="feishu", chat_type="dm", is_bot=False, thread=None):
    source = SimpleNamespace(platform=SimpleNamespace(value=platform), chat_id=chat, chat_type=chat_type,
                             user_id=user, is_bot=is_bot, thread_id=thread)
    return SimpleNamespace(source=source, reply_to_message_id=quoted, text="① 选甲")


class Mute(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-away-mute-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        who = self.tmp / "who"
        who.mkdir()
        for name, value in (("owner_open_id", OWNER), ("owner_chat_id", CHAT)):
            (who / name).write_text(value + "\n", encoding="utf-8")
            (who / name).chmod(0o600)
        fake = self.tmp / "fake"
        fake.mkdir()
        (fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (fake / "lark-cli").chmod(0o755)
        (fake / "ctl.json").write_text("{}", encoding="utf-8")         # 假飞书每条命令都照常成功
        for name, value in (("OWNER_DIR", who), ("AWAY_DIR", self.tmp / "away"), ("LARK", str(fake / "lark-cli")), ("NOW", None)):
            patch = mock.patch.object(A, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.plugin = load_plugin()
        self.plugin._MODULE = A                                     # 用仓库这份脚本，不读装好的那份

    def run_cmd(self, hhmm, *args):
        A.NOW = f"2026-11-21T{hhmm}:00+08:00"
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = A.main(list(args))
        self.assertEqual(code, 0, out.getvalue())

    def sheet_with_reminder(self, end=False):
        """开一张单子：卡片随 ① 发出，之后冒出 ② 另发一句提醒。返回（卡片消息号，提醒消息号）。"""
        self.run_cmd("12:00", "start", "--by", "Claude 会话「测试」")
        self.run_cmd("12:05", "ask", *[x for k, v in ASK.items() for x in ("--" + k, v)])
        self.run_cmd("12:10", "push")
        self.run_cmd("12:20", "ask", *[x for k, v in dict(ASK, task="数字分身 · 合并官").items() for x in ("--" + k, v)])
        self.run_cmd("12:25", "push")
        if end:
            self.run_cmd("12:30", "resolve", "1", "--result", "办完")
            self.run_cmd("12:31", "resolve", "2", "--result", "办完")
            self.run_cmd("12:32", "end")
            self.run_cmd("12:33", "push")
        sheet = A.load()
        self.assertEqual(len(sheet["reminders"]), 1)
        return sheet["message_id"], sheet["reminders"][0]["message_id"]

    def drop(self, ev):
        return self.plugin._drop(event=ev, gateway=None, session_store=None)

    def test_quote_of_card_or_reminder_is_dropped_before_the_model(self):
        card, reminder = self.sheet_with_reminder()
        for quoted in (card, reminder):
            self.assertEqual(self.drop(event(quoted)), {"action": "skip", "reason": "away-sheet-reply"})

    def test_ended_sheets_count_too(self):
        # 结束的卡片上写着「引用它回话也没人接」：这话也不该交给猛鬼兽
        old_card, old_reminder = self.sheet_with_reminder(end=True)
        self.run_cmd("13:00", "start", "--by", "Claude 会话「测试」")
        for quoted in (old_card, old_reminder):
            self.assertIsNotNone(self.drop(event(quoted)))

    def test_tenant_user_id_is_accepted(self):
        # 网关的 user_id 优先给企业 user_id（不是 ou_ 开头），这时只靠私聊号认人
        card, _ = self.sheet_with_reminder()
        self.assertIsNotNone(self.drop(event(card, user="e9cgcf77")))

    def test_everything_else_goes_to_the_model(self):
        card, _ = self.sheet_with_reminder()
        for ev in (event(None), event("om_other_message"), event(card, chat="oc_someone_else"),
                   event(card, chat_type="group"), event(card, platform="telegram"), event(card, is_bot=True),
                   event(card, thread="omt_topic"), event(card, user="ou_someone_else")):
            self.assertIsNone(self.drop(ev), ev)

    def test_unreadable_sheet_is_skipped_not_fatal(self):
        card, _ = self.sheet_with_reminder()
        self.run_cmd("12:40", "resolve", "1", "--result", "办完")
        self.run_cmd("12:41", "resolve", "2", "--result", "办完")
        self.run_cmd("12:42", "end")
        self.run_cmd("12:43", "push")
        self.run_cmd("13:00", "start", "--by", "Claude 会话「测试」")
        self.run_cmd("13:05", "ask", *[x for k, v in ASK.items() for x in ("--" + k, v)])
        self.run_cmd("13:10", "push")
        second = A.load()["message_id"]
        first = A.base() / "2026-11-21-1200" / "sheet.json"
        first.write_text("{坏了", encoding="utf-8")
        self.assertIsNone(self.drop(event(card)))                      # 坏掉那张的回话照常交给猛鬼兽
        self.assertIsNotNone(self.drop(event(second)))                 # 别的单子不受影响

    def test_failures_let_the_message_through_and_warn(self):
        card, _ = self.sheet_with_reminder()
        (A.OWNER_DIR / "owner_chat_id").chmod(0o644)                   # 身份文件不合规：owner() 报错
        with self.assertLogs(self.plugin.logger, "WARNING") as logs:
            self.assertIsNone(self.drop(event(card)))
        self.assertIn("照常交给猛鬼兽", logs.output[0])
        (A.OWNER_DIR / "owner_chat_id").chmod(0o600)
        with mock.patch.object(A, "AWAY_DIR", self.tmp / "nowhere"):
            self.assertIsNone(self.drop(event(card)))                  # 没有账本：什么都不丢

    def test_missing_script_lets_the_message_through(self):
        plugin = load_plugin()
        plugin.SCRIPT = self.tmp / "no-such" / "away_sheet.py"
        with self.assertLogs(plugin.logger, "WARNING"):
            self.assertIsNone(plugin._drop(event=event("om_x"), gateway=None, session_store=None))

    def test_muted_ids_reads_without_the_lock(self):
        # 网关在事件循环里调它：不能等托管会话的写锁
        card, reminder = self.sheet_with_reminder()
        with mock.patch.object(A, "locked", side_effect=AssertionError("不该拿锁")):
            self.assertEqual(A.muted_ids(CHAT), {card, reminder})
        self.assertEqual(A.muted_ids("oc_someone_else"), set())

    def test_registers_the_dispatch_hook(self):
        hooks = []
        self.plugin.register(SimpleNamespace(register_hook=lambda name, fn: hooks.append((name, fn))))
        self.assertEqual(hooks, [("pre_gateway_dispatch", self.plugin._drop)])


if __name__ == "__main__":
    unittest.main()
