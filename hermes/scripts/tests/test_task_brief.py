"""#358：共享任务说明的静态合同；模型是否照做须安装后另验，不能由文字断言推出。

后续的执行回归只用隔离凭证库、本地 Git 和假 CLI，不访问真实 Hermes。
"""
import _isolation  # noqa: F401
import datetime as dt
import re
import unittest
from pathlib import Path

from prod_guard import setUpModule, tearDownModule  # noqa: F401
from test_dt_worktree import WorkerBase, G, scope_block

ROOT = Path(__file__).resolve().parents[3]
SKILLS = ROOT / "hermes/skills"
MODULE = SKILLS / "dt-shared-reference/references/task-brief.md"
POINTER = 'skill_view(name="dt-shared-reference", file_path="references/task-brief.md")'
NEW = "\n".join(("目标：为 dt-sandbox 补测试说明；原任务 #358，依据 README.md 当前版本",
                 "交付物：README.md 改动和交接，最终由受信发布开一个 PR",
                 "验收：README.md 有准确测试命令；交回实际执行输出",
                 "边界：仅改 README.md，不推分支、不开 PR、不合并、不安装",
                 "交给谁：Claude"))
OLD = "\n".join(("目标：给 dt-sandbox 的 README 加一节「怎么跑测试」",
                 "交付物：只改 README.md，开一个 PR",
                 "验收标准：PR 里只有 README.md，新增一节写明测试命令",
                 "边界：不改别的文件，不合并", "交给谁：Claude"))


class BriefContract(unittest.TestCase):
    def test_canonical_format_and_legacy_alias(self):
        text = MODULE.read_text(encoding="utf-8")
        template = re.search(r"```text\n(.*?)\n```", text, re.S).group(1)
        self.assertEqual([line.split("：", 1)[0] for line in template.splitlines()],
                         ["目标", "交付物", "验收", "边界", "交给谁"])
        self.assertIn("旧写法「验收标准」等同于「验收」", text)
        for word in ("原任务号", "上下文出处", "历史决定", "验收要求", "最多 3000 字"):
            self.assertIn(word, text)

    def test_missing_blank_and_placeholder_have_stop_rule_for_each_element(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn("占位词不算齐全", text)
        rows = [line for line in text.splitlines() if line.startswith("| ") and "停下，" in line]
        self.assertEqual([r.split("|")[1].strip() for r in rows],
                         ["目标", "交付物", "验收", "边界", "交给谁"])
        for row in rows:
            self.assertIn("不递提议、不新建单、不启动工人", row)

    def test_filter_is_before_writing_and_covers_all_worker_visible_parts(self):
        text = MODULE.read_text(encoding="utf-8")
        for rule in ("筛过材料后才写临时任务文件", "受限内容不进入工人任务单", "没有 `sensitivity` 标注",
                     "私人路径", "能还原它的转述", "普通 / 内部", "去公开位置的内部内容同样不能带出",
                     "包括目标、边界、引用、日志摘录、命令和示例", "不擅自降级"):
            self.assertIn(rule, text)
        # 调用方后续映射不能重新要求无条件抄入原文。
        scene = (SKILLS / "groupdigest-act/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("过滤后可交给工人的 `task` 内容", scene)
        self.assertIn("`snippet` 先核信息边界", scene)
        self.assertNotIn("有 `snippet` 就原样写进去", scene)

    def test_permission_and_verification_limits_are_explicit(self):
        text = MODULE.read_text(encoding="utf-8")
        for rule in ("D 档", "新底座没有有效凭证，不启动工人", "不直接改账本", "不改闸门",
                     "不做五项语义或敏感内容分类", "真实 Hermes 案例", "分别删去五个要素"):
            self.assertIn(rule, text)

    def test_unaffected_consumers_load_shared_file_before_task_file(self):
        for skill in ("autoloop", "groupdigest-act"):
            with self.subTest(skill=skill):
                text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
                self.assertIn(POINTER, text)
                self.assertNotIn("按 workshop 技能第 1 节写任务说明", text)
        text = (SKILLS / "autoloop/SKILL.md").read_text(encoding="utf-8")
        part = text.split("## 2.", 1)[1].split("## 3.", 1)[0]
        self.assertNotIn("```", part, "统一格式只在共享模块维护")


class BriefReachesWorker(WorkerBase):
    """用既有网关、任务文件和启动脚本；恢复闸放行、门卫点击和模型为替身。"""

    def make_task(self, scope, executor="claude"):
        code = G.propose("dt-sandbox", executor, scope, now=self.now)["code"]
        return self.click(code, now=self.now)["task_id"]

    def test_new_format_keeps_context_and_reaches_prompt_verbatim(self):
        tid = self.make_task(NEW)
        r, calls = self.run_turn([self.dispatch_event(tid)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(calls), 1, r.stdout)
        self.assertIn(scope_block(NEW), calls[0]["argv"][1])
        self.assertIn(NEW, (self.tasks / (tid + ".md")).read_text())
        self.assertIsNotNone(self.handoff(tid))

    def test_old_format_with_acceptance_alias_reaches_prompt_verbatim(self):
        old = OLD.replace("交给谁：Claude", "交给谁：Codex")
        tid = self.make_task(old, executor="codex")
        r, calls = self.run_turn([self.dispatch_event(tid, executor="codex")], executor="codex",
                                 args=("exec", "按旧单继续"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(calls), 1, r.stdout)
        self.assertIn(scope_block(old), calls[0]["argv"][-1])
        self.assertIsNotNone(self.handoff(tid))

    def test_complete_brief_and_proposal_do_not_start_worker_without_grant(self):
        G.propose("dt-sandbox", "claude", NEW, now=self.now)
        # 即便任务正文已在手中，未点卡就没有凭证或工人任务单。
        tid = "t-20261125-abcdef"
        self.tasks.mkdir(parents=True, exist_ok=True)
        (self.tasks / (tid + ".md")).write_text(NEW)
        r, calls = self.run_turn([self.dispatch_event(tid)])
        self.assert_stopped(r, calls, "读不到凭证镜像")
        self.assertIsNone(self.handoff(tid))

    def test_complete_brief_does_not_override_expired_grant(self):
        tid = self.make_task(NEW)
        self.patch_mirror(tid, valid_until=G.iso(self.now - dt.timedelta(minutes=1)))
        r, calls = self.run_turn([self.dispatch_event(tid)])
        self.assert_stopped(r, calls, "凭证已过期")
        self.assertIsNone(self.handoff(tid))


if __name__ == "__main__":
    unittest.main()
