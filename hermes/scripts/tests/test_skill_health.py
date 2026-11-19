"""隔离自测：hermes/scripts/skill_health.py（skill 月度体检推送，#151）。

node 换成临时 shell 脚本（打印固定报告、或按要求失败），my-skills 换成临时目录。不碰真实 my-skills、会话记录和 ~/.hermes。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import importlib.util
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "skill_health.py"
spec = importlib.util.spec_from_file_location("skill_health", SCRIPT)
sh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sh)

REPORT = """# skill 月度体检（2026-12-01，近 30 天）

## 数据来源

- Claude：命中 3 次。

## 每个 skill

| skill | 链接到 | Claude 次数 | 最近使用 |
|---|---|---:|---|
| matt-flow | Claude、Codex | 3 | 2026-11-20 |
| vault-backup | Claude | 0 | — |

「最近使用」只看这次扫描的窗口。

## 提案（只是提案，常浩同意后才动）

1. **观察去留**：`vault-backup` 近 30 天 0 调用。
"""


class ToFeishu(unittest.TestCase):
    def test_headings_become_bold_and_table_rows_become_list(self):
        out = sh.to_feishu(REPORT)
        self.assertTrue(out.startswith("**skill 月度体检（2026-12-01，近 30 天）**"))
        self.assertIn("**每个 skill**", out)
        self.assertIn("- **matt-flow**：链接到 Claude、Codex，Claude 次数 3，最近使用 2026-11-20", out)
        self.assertIn("- **vault-backup**：链接到 Claude，Claude 次数 0，最近使用 —", out)
        self.assertNotIn("|", out)
        self.assertNotRegex(out, r"(?m)^#")

    def test_other_lines_unchanged(self):
        out = sh.to_feishu(REPORT)
        self.assertIn("- Claude：命中 3 次。", out)
        self.assertIn("「最近使用」只看这次扫描的窗口。", out)
        self.assertIn("1. **观察去留**：`vault-backup` 近 30 天 0 调用。", out)

    def test_two_tables_keep_their_own_headers(self):
        out = sh.to_feishu("| a | b |\n|---|---|\n| x | 1 |\n\n| c | d |\n|---|---|\n| y | 2 |\n")
        self.assertIn("- **x**：b 1", out)
        self.assertIn("- **y**：d 2", out)


class Main(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "my-skills"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / "scripts/monthly-health.mjs").write_text("// 假脚本，不会被执行\n")
        (self.root / "report.md").write_text(REPORT)

    def tearDown(self):
        self.tmp.cleanup()

    def fake_node(self, body):
        node = self.root / "node"
        node.write_text("#!/bin/sh\n" + body)
        node.chmod(0o755)
        return str(node)

    def run_main(self, node, argv=()):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(sh, "REPO", self.repo), patch.object(sh, "NODE", node), \
                redirect_stdout(out), redirect_stderr(err):
            code = sh.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_success_prints_converted_report_and_footer(self):
        node = self.fake_node(f'echo "$@" > "{self.root}/args"\ncat "{self.root}/report.md"\n')
        code, out, err = self.run_main(node)
        self.assertEqual(code, 0, err)
        self.assertIn("- **matt-flow**：", out)
        self.assertTrue(out.rstrip().endswith(sh.FOOTER))
        self.assertEqual((self.root / "args").read_text().split(), ["scripts/monthly-health.mjs", "--days", "30"])

    def test_days_passed_through(self):
        node = self.fake_node(f'echo "$@" > "{self.root}/args"\ncat "{self.root}/report.md"\n')
        code, _, _ = self.run_main(node, ["--days", "90"])
        self.assertEqual(code, 0)
        self.assertEqual((self.root / "args").read_text().split()[-1], "90")

    def test_bad_days_rejected(self):
        with self.assertRaises(SystemExit):
            sh.days_arg(["--days", "0"])
        with self.assertRaises(SystemExit):
            sh.days_arg(["--days"])

    def test_node_failure_exits_1_with_reason_and_no_stdout(self):
        node = self.fake_node('echo "缺少 skills 目录" >&2\nexit 2\n')
        code, out, err = self.run_main(node)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("缺少 skills 目录", err)

    def test_empty_output_counts_as_failure(self):
        code, out, err = self.run_main(self.fake_node("exit 0\n"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("没有输出", err)

    def test_missing_node_exits_1(self):
        code, out, err = self.run_main(str(self.root / "no-such-node"))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("没跑起来", err)


if __name__ == "__main__":
    unittest.main()
