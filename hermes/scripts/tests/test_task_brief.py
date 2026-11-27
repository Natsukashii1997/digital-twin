"""隔离自测：五要素任务说明的核对（task_brief.py，#358，docs/技能架构.md 第 9 节 5b-1）。

单元部分只调函数；派单部分用 dispatch_harness.py 在临时目录里以真实子进程跑 dispatch.py new，不碰 ~/.hermes、真实工单。
新底座 propose 的拦截在 test_autoloop_grants.Approval 里（要那边的中台夹具）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import task_brief as B  # noqa: E402
from test_dispatch_lock import Base as DispatchBase  # noqa: E402

FULL = "目标：给 README 加一节\n交付物：README.md 里新的一节\n验收：PR 里只有 README.md\n边界：只改 README.md\n交给谁：Claude\n"


class Fields(unittest.TestCase):
    def test_full_brief_has_nothing_missing(self):
        self.assertEqual(B.missing(FULL), [])
        self.assertEqual(B.problems(FULL, worker=True), [])

    def test_accepted_spellings(self):
        texts = {
            "半角冒号": FULL.replace("：", ": "),
            "列表加粗（pdaily_act）": "\n".join(f"- **{x.split('：')[0]}**：{x.split('：')[1]}" for x in FULL.splitlines()),
            "标题行": "\n".join("# " + x for x in FULL.splitlines()),
            "二级标题下一行": "\n\n".join(f"## {x.split('：')[0]}\n{x.split('：')[1]}" for x in FULL.splitlines()),
            "旧车间别名": FULL.replace("验收：", "验收标准：").replace("边界：", "范围："),
        }
        for name, text in texts.items():
            with self.subTest(name):
                self.assertEqual(B.missing(text), [], text)

    def test_missing_and_empty_items_are_named_in_fixed_order(self):
        self.assertEqual(B.missing(""), list(B.FIELDS))
        self.assertEqual(B.missing(None), list(B.FIELDS))
        self.assertEqual(B.missing(FULL.replace("交给谁：Claude", "交给谁：")), ["交给谁"])
        self.assertEqual(B.missing(FULL.replace("交付物：README.md 里新的一节\n", "").replace("边界：只改 README.md\n", "")),
                         ["交付物", "边界"])
        self.assertEqual(B.missing("## 目标\n## 交付物\nx"), ["目标", "验收", "边界", "交给谁"])   # 标题下紧跟标题算空

    def test_names_must_start_the_line(self):
        """正文里顺口提到「目标：」不算写了这一项。"""
        self.assertEqual(B.missing("说明：我们的目标：改 README\n" + FULL.split("\n", 1)[1]), ["目标"])

    def test_first_value_wins_and_aliases_merge(self):
        self.assertEqual(B.fields(FULL + "目标：后来又写一遍")["目标"], "给 README 加一节")
        self.assertEqual(B.fields("范围：只改 README.md")["边界"], "只改 README.md")

    def test_problems_text_names_what_is_missing(self):
        (msg,) = B.problems("目标：x")
        self.assertIn("任务说明缺交付物、验收、边界、交给谁", msg)


class Leaks(unittest.TestCase):
    def test_restricted_text_only_checked_for_worker(self):
        key = "sk-" + "Ab1_" * 6
        for extra, why in ((f"\n令牌 {key}", "疑似密钥"), ("\n-----BEGIN OPENSSH PRIVATE KEY-----", "疑似密钥"),
                           ("\n见 /Users/nat/Documents/Obsidian Vault/a.md", "Vault"), ("\nsensitivity: 受限", "sensitivity")):
            with self.subTest(why=why):
                self.assertEqual(B.problems(FULL + extra), [])                 # 旧车间（主账号）不核
                (msg,) = B.problems(FULL + extra, worker=True)
                self.assertIn(why, msg)
                self.assertNotIn(key, msg)

    def test_ordinary_words_are_not_flagged(self):
        """task-brief、ghp 开头的短词、正文里的 sensitivity 都不算。"""
        text = FULL + "\n参考 references/task-brief.md；讨论 sensitivity 分级怎么写；ghp_short"
        self.assertEqual(B.leaks(text), [])


class DispatchNew(DispatchBase):
    """真实 dispatch.py new：缺要素不开单、说缺哪项；补齐照常开单。"""

    def test_missing_parts_refuse_without_order(self):
        self.task.write_text("# 目标：隔离测试\n验收：能开单\n", encoding="utf-8")
        code, out = self.run_cmd("new", "--project", "fixture", "--tool", "claude", "--mode", "read", "--task-file", str(self.task))
        self.assertNotEqual(code, 0, out)
        self.assertIn("任务说明缺交付物、边界、交给谁", out.get("msg", ""), out)
        self.assertEqual(list(self.ledger.glob("D*/order.json")), [])

    def test_unreadable_task_file_refuses(self):
        code, out = self.run_cmd("new", "--project", "fixture", "--tool", "claude", "--mode", "read",
                                 "--task-file", str(self.root / "nope.md"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("任务文件读不了", out.get("msg", ""), out)

    def test_full_brief_opens_order(self):
        self.assertTrue(self.new())


if __name__ == "__main__":
    unittest.main()
