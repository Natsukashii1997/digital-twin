"""隔离自测：车间只读单的命令行参数（#236 后续）。

Claude 只读单曾只加 --allowedTools，本机 bypassPermissions 下照样能用 Bash、Write 和 MCP 写文件；
这里钉住修好后的参数：只加载 Read / Grep / Glob、不加载 MCP、dontAsk，写入单和 Codex 不受影响。
真 claude 的反向验证（只读单写文件被拒）不在这里跑，见 claude_readonly_probe.py。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_run_worker_readonly.py -v
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import run_worker as rw  # noqa: E402

WRITE_TOOLS = {"Bash", "Write", "Edit", "NotebookEdit", "Task", "Agent", "Skill", "WebFetch", "Workflow"}


def args(mode, resume=None, cwd="/tmp/x"):
    return types.SimpleNamespace(mode=mode, resume=resume, cwd=cwd)


def flag_value(cmd, flag):
    return cmd[cmd.index(flag) + 1]


class ClaudeReadOnly(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(rw, "find_bin", lambda name: f"/bin/{name}")
        p.start()
        self.addCleanup(p.stop)

    def test_read_mode_restricts_tools_mcp_and_permission_mode(self):
        cmd = rw.claude_cmd(args("read"), "任务")
        self.assertEqual(cmd[:3], ["/bin/claude", "-p", "任务"])
        tools = set(flag_value(cmd, "--tools").split(","))
        self.assertEqual(tools, {"Read", "Grep", "Glob"})
        self.assertFalse(tools & WRITE_TOOLS)
        self.assertIn("--strict-mcp-config", cmd)
        self.assertNotIn("--mcp-config", cmd)
        self.assertEqual(flag_value(cmd, "--permission-mode"), "dontAsk")
        self.assertEqual(set(flag_value(cmd, "--allowedTools").split(",")), tools)
        for bad in ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"):
            self.assertNotIn(bad, cmd)

    def test_read_mode_resume_keeps_restrictions(self):
        cmd = rw.claude_cmd(args("read", resume="S-1"), "续")
        self.assertEqual(flag_value(cmd, "--resume"), "S-1")
        self.assertEqual(flag_value(cmd, "--tools"), "Read,Grep,Glob")
        self.assertEqual(flag_value(cmd, "--permission-mode"), "dontAsk")
        self.assertIn("--strict-mcp-config", cmd)

    def test_write_mode_unchanged(self):
        cmd = rw.claude_cmd(args("write"), "任务")
        self.assertIn("--dangerously-skip-permissions", cmd)
        for flag in ("--tools", "--strict-mcp-config", "--permission-mode", "--allowedTools"):
            self.assertNotIn(flag, cmd)


class CodexUnchanged(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(rw, "find_bin", lambda name: f"/bin/{name}")
        p.start()
        self.addCleanup(p.stop)

    def test_codex_read_is_sandboxed(self):
        cmd = rw.codex_cmd(args("read"), "任务", Path("/tmp/out"))
        self.assertEqual(flag_value(cmd, "-s"), "read-only")
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", cmd)

    def test_codex_write_bypasses(self):
        cmd = rw.codex_cmd(args("write"), "任务", Path("/tmp/out"))
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", cmd)
        self.assertNotIn("-s", cmd)


if __name__ == "__main__":
    unittest.main()
