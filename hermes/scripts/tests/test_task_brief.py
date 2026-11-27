"""隔离自测：五要素任务说明的核对（task_brief.py，#358，docs/技能架构.md 第 9 节 5b-1）。

单元部分只调函数；派单部分用 dispatch_harness.py 在临时目录里以真实子进程跑 dispatch.py new，不碰 ~/.hermes、真实工单。
新底座 propose 的拦截在 test_autoloop_grants.Approval 里（要那边的中台夹具）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import sys
import time
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

    def test_heading_followed_by_another_item_is_empty(self):
        """#405 审查 P1-01：「## 目标」下面紧跟着另一项（不管哪种写法），目标算空，不拿下一项冒充。"""
        rest = FULL.split("\n", 1)[1]
        for nxt in ("- **交付物**：README.md", "**交付物**", "交付物", "验收：PR 只有 README.md", "# 交付物：README.md", "## 交付物"):
            with self.subTest(nxt):
                self.assertEqual(B.missing(f"## 目标\n\n{nxt}\n\n{rest}"), ["目标"])
        self.assertEqual(B.missing(f"## 目标\n\n改 README，补一节测试说明\n\n{rest}"), [])   # 标题下一行写正文照旧算数
        self.assertEqual(B.missing("## 目标\n交付物是 README 里新的一节\n" + rest), [])          # 正文里提到某项的名字不算另一项

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

    def test_sensitivity_marker_with_list_or_bold_prefix(self):
        for line in ("- sensitivity: 受限", "**sensitivity**: restricted", "> sensitivity：受限", "sensitivity: 受限"):
            with self.subTest(line):
                self.assertEqual(len(B.leaks(FULL + "\n" + line)), 1)

    def test_executor_must_match_who(self):
        """#405 审查 P2-01：「交给谁」只点名一家时要和实际派的一致；两家都写或都没写不判。"""
        self.assertEqual(B.problems(FULL, executor="claude"), [])
        (msg,) = B.problems(FULL, executor="codex")
        self.assertEqual(msg, "任务说明写交给 Claude，实际要派给 Codex")
        for who in ("Claude 或 Codex", "跟随默认执行方", "CLAUDE CODE"):
            with self.subTest(who):
                text = FULL.replace("交给谁：Claude", f"交给谁：{who}")
                self.assertEqual(B.problems(text, executor="claude"), [])
        self.assertEqual(B.problems(FULL.replace("交给谁：Claude", "交给谁：Codex"), executor="codex"), [])
        self.assertIsNone(B.mismatch(FULL, None))

    def test_negation_and_both_named(self):
        """#405 第 2 轮 P2-01：否定写法认出正向接收方；两家都写又没说哪家都行的，看不出交给谁、不放。"""
        cases = (("不要 Claude，给 Codex", "claude", "不交给 Claude"), ("不要 Claude，给 Codex", "codex", None),
                 ("不要 Codex，给 Claude", "codex", "不交给 Codex"), ("不要 Codex，给 Claude", "claude", None),
                 ("Claude 或 Codex", "claude", None), ("Claude 或 Codex", "codex", None), ("Claude / Codex 都行", "codex", None),
                 ("跟随默认执行方", "codex", None), ("按派单参数里的执行方", "claude", None),
                 ("Claude 和 Codex", "claude", "看不出交给哪家"), ("先给 Claude 再给 Codex", "codex", "看不出交给哪家"),
                 ("除了 Claude", "claude", "不交给 Claude"), ("别给 Codex", "claude", None),
                 ("Codex（不是 Claude）", "claude", "不交给 Claude"), ("Codex（不是 Claude）", "codex", None))
        for who, executor, want in cases:
            with self.subTest(who=who, executor=executor):
                got = B.mismatch(FULL.replace("交给谁：Claude", f"交给谁：{who}"), executor)
                if want is None:
                    self.assertIsNone(got)
                else:
                    self.assertIn(want, got)

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

    def test_who_must_match_the_tool(self):
        self.task.write_text(FULL.replace("交给谁：Claude", "交给谁：Codex"), encoding="utf-8")
        code, out = self.run_cmd("new", "--project", "fixture", "--tool", "claude", "--mode", "read", "--task-file", str(self.task))
        self.assertNotEqual(code, 0, out)
        self.assertIn("任务说明写交给 Codex，实际要派给 Claude", out.get("msg", ""), out)
        self.assertEqual(list(self.ledger.glob("D*/order.json")), [])

    def test_order_gets_the_text_that_was_checked(self):
        """#405 审查 P1-03：核完之后源文件被改掉，交给车间的仍是核过的那份；权限位照源文件带上（原来 shutil.copy 也这样）。"""
        self.task.write_text(FULL, encoding="utf-8")
        self.task.chmod(0o600)
        go = self.root / "go"
        p = self.spawn("new", "--project", "fixture", "--tool", "claude", "--mode", "read", "--task-file", str(self.task),
                       H_GO=go, H_PRELOCK_DELAY=3)   # 进程起好后在起跑线等；放行后马上读、核，拿派单锁之前睡 3 秒
        time.sleep(1.5)
        go.touch()
        time.sleep(1)                                # 这时已经核完、正在锁前睡：把源文件换掉
        self.task.write_text("目标：被换掉的内容\n", encoding="utf-8")
        code, out = self.result(p)
        self.assertEqual(code, 0, out)
        saved = self.ledger / out["order"]["no"] / "task.md"
        self.assertEqual(saved.read_text(encoding="utf-8"), FULL)
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
