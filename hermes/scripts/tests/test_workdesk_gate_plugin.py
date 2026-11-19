"""亚古兽工具拦截插件（#142）自测：他人只能用安全工具、常浩本人两个 ID 都不受限、本机与定时任务不受限、
会话信息读不到时拦截、牛马之家只在本人私聊。不起网关：直接加载插件，假的 gateway.session_context 提供会话信息。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "workdesk-gate" / "__init__.py"
OPEN_ID, USER_ID, OTHER = "ou_6fac84a8164f84c0c591a9b3a98b462c", "e9cgcf77", "ou_bff4d45b002e33982a5d3adc3309809b"
RISKY = ["terminal", "process_manage", "read_file", "write_file", "patch", "search_files", "execute_code",
         "browser_navigate", "browser_exec", "computer_use", "cronjob_manage", "delegate_task", "memory",
         "session_search", "skill_manage", "manage_connections", "vision_analyze", "mcp__vault-read__search",
         "mcp__other__anything", "some_new_tool"]
SAFE = ["clarify", "feishu_doc_read", "feishu_drive_add_comment", "feishu_drive_list_comments",
        "feishu_drive_list_comment_replies", "feishu_drive_reply_comment", "web_search", "web_extract",
        "skill_view", "skills_list", "image_generate", "text_to_speech", "todo_list"]


def load():
    spec = importlib.util.spec_from_file_location("workdesk_gate_plugin", PLUGIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WorkdeskGate(unittest.TestCase):
    def setUp(self):
        self.mod = load()
        self.sess = {}
        fake = types.ModuleType("gateway.session_context")
        fake.get_session_env = lambda name, default="": self.sess.get(name, default)
        pkg = types.ModuleType("gateway")
        pkg.session_context = fake
        patcher = mock.patch.dict(sys.modules, {"gateway": pkg, "gateway.session_context": fake})
        patcher.start()
        self.addCleanup(patcher.stop)

    def at(self, platform="feishu", chat_type="dm", user="", chat="oc_x"):
        self.sess = {"HERMES_SESSION_PLATFORM": platform, "HERMES_SESSION_CHAT_TYPE": chat_type,
                     "HERMES_SESSION_USER_ID": user, "HERMES_SESSION_CHAT_ID": chat}

    def gate(self, tool):
        return self.mod._gate(tool_name=tool, args={})

    def test_owner_both_ids_unrestricted_in_dm_and_comments(self):
        for user in (OPEN_ID, USER_ID):
            for chat_type in ("dm", "comment", "group"):
                self.at(user=user, chat_type=chat_type)
                for tool in RISKY + SAFE:
                    self.assertIsNone(self.gate(tool), (user, chat_type, tool))

    def test_others_blocked_from_risky_tools(self):
        for chat_type in ("comment", "group", "dm"):
            self.at(user=OTHER, chat_type=chat_type)
            for tool in RISKY:
                r = self.gate(tool)
                self.assertEqual(r["action"], "block", (chat_type, tool))
                self.assertIn("常浩本人", r["message"])

    def test_others_keep_safe_tools(self):
        self.at(user=OTHER, chat_type="comment")
        for tool in SAFE:
            self.assertIsNone(self.gate(tool), tool)

    def test_missing_sender_on_feishu_treated_as_other(self):
        self.at(user="", chat_type="comment")
        self.assertEqual(self.gate("terminal")["action"], "block")
        self.assertIsNone(self.gate("web_search"))

    def test_local_cli_and_cron_unrestricted(self):
        for platform in ("", "cli", "cron", "local", "desktop", "CLI"):
            self.at(platform=platform, chat_type="", user="")
            self.assertIsNone(self.gate("terminal"), platform)
            self.assertIsNone(self.gate("write_file"), platform)

    def test_unknown_platform_counts_as_messaging(self):
        self.at(platform="telegram", user=OTHER)
        self.assertEqual(self.gate("terminal")["action"], "block")

    def test_session_unreadable_blocks_risky_keeps_safe(self):
        broken = types.ModuleType("gateway.session_context")   # 没有 get_session_env：导入即失败
        with mock.patch.dict(sys.modules, {"gateway.session_context": broken}):
            self.assertEqual(self.gate("terminal")["action"], "block")
            self.assertEqual(self.gate("mcp__workdesk__project_list")["action"], "block")
            self.assertIsNone(self.gate("web_search"))

    def test_workdesk_only_owner_dm_and_caller_overwritten(self):
        for user in (OPEN_ID, USER_ID):
            self.at(user=user, chat_type="dm", chat="oc_dm")
            r = self.gate("mcp__workdesk__project_list")
            self.assertEqual(r["action"], "modify")
            self.assertEqual(r["args"]["_caller"], {"platform": "feishu", "chatType": "dm", "userId": user, "chatId": "oc_dm"})
        for kw in ({"user": OPEN_ID, "chat_type": "comment"}, {"user": OPEN_ID, "chat_type": "group"},
                   {"user": OTHER, "chat_type": "dm"}, {"platform": "cli", "user": "", "chat_type": ""}):
            self.at(**kw)
            for tool in ("mcp__workdesk__project_list", "mcp_workdesk_strategy_month"):
                r = self.gate(tool)
                self.assertEqual(r["action"], "block", (kw, tool))
                self.assertIn("牛马之家", r["message"])

    def test_registers_pre_tool_call(self):
        ctx = mock.Mock()
        self.mod.register(ctx)
        ctx.register_hook.assert_called_once_with("pre_tool_call", self.mod._gate)


if __name__ == "__main__":
    unittest.main()
