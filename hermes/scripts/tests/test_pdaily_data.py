"""隔离自测：pdaily_data.py（个人项目日报取数，#110 批 1a）和 progress_data.next_step 的修正。

临时 git 仓库 + 假 gh（fake_gh.py），不联网。统计日 D = 2026-11-08（04:00 到 11-09 04:00，北京时间）。
覆盖 V2 第 7.1 节的场景：跨凌晨统计、CURRENT 过期、取数失败、昨天没有新动作、PR 已合并但没安装。
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import fake_gh  # noqa: E402
import pdaily_data as pd  # noqa: E402
import progress_data  # noqa: E402

D = dt.date(2026, 11, 8)
GOAL_LINE = "当前阶段目标：做完甲（2026-11-08 常浩确认，digital-twin#114）"


def sh(cwd, *args, at="2026-11-08 10:00:00 +0800"):
    env = {**os.environ, "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout


def make_repo(root, name, files=None, at="2026-11-06 10:00:00 +0800"):
    r = root / name
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    for fname, text in (files or {}).items():
        (r / fname).write_text(text, encoding="utf-8")
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "--allow-empty", "-m", "起步", at=at)
    return r


def commit(r, msg, at, fname=None, text=None):
    if fname:
        (r / fname).write_text(text, encoding="utf-8")
        sh(r, "add", fname)
    sh(r, "commit", "-q", "--allow-empty", "-m", msg, at=at)


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-pdaily-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "gh"
        fake_gh.install(self.root / "bin", self.state)
        self.old = {k: os.environ.get(k) for k in ("PATH", "FAKE_GH_STATE")}
        os.environ["PATH"] = f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}"
        os.environ["FAKE_GH_STATE"] = str(self.state)
        self.addCleanup(self.restore)

    def restore(self):
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    NO_MILESTONES = {"graphql": {"data": {"repository": {"issues": {"nodes": []}}}}}

    def gh(self, prs=(), issues=(), api=None):
        (self.state / "pr_list.json").write_text(json.dumps(list(prs)))
        (self.state / "issue_list.json").write_text(json.dumps(list(issues)))
        (self.state / "api.json").write_text(json.dumps(self.NO_MILESTONES if api is None else api))

    def run_one(self, r, github="o/fx"):
        p = {"id": r.name, "name": r.name, "path": str(r), "main": "main", "github": github}
        return pd.collect(D, [p])


class TimeTests(unittest.TestCase):
    def test_window_is_0400_to_0400_shanghai(self):
        s, e = pd.window(D)
        self.assertEqual(s.isoformat(), "2026-11-08T04:00:00+08:00")
        self.assertEqual(e.isoformat(), "2026-11-09T04:00:00+08:00")

    def test_default_day_is_last_finished_stat_day(self):
        at = lambda h: dt.datetime(2026, 11, 9, h, 0, tzinfo=pd.TZ)
        self.assertEqual(pd.default_day(at(8)), D)                       # 11-09 08:00 发报：报 11-08
        self.assertEqual(pd.default_day(at(3)), D - dt.timedelta(days=1))  # 04:00 前 11-08 还没结束

    def test_inside_handles_utc_z(self):
        w = pd.window(D)
        self.assertTrue(pd.inside("2026-11-08T16:09:00Z", w))   # 北京 11-09 00:09，算 11-08
        self.assertFalse(pd.inside("2026-11-08T19:59:59Z", (w[1], w[1])))
        self.assertFalse(pd.inside("2026-11-07T19:59:00Z", w))  # 北京 11-08 03:59，算 11-07


class RepoTests(Base):
    def test_cross_midnight_commit_counts_for_previous_day(self):
        r = make_repo(self.root, "a", {"CURRENT.md": f"# 当前状态\n\n{GOAL_LINE}\n\n## 一节（2026-11-06）\n- 下一步：继续\n"})
        commit(r, "凌晨前一刻", "2026-11-08 03:59:00 +0800")
        commit(r, "白天", "2026-11-08 15:00:00 +0800")
        commit(r, "跨凌晨", "2026-11-09 00:09:00 +0800")
        commit(r, "下一个统计日", "2026-11-09 04:00:00 +0800")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["yesterday_delta"]["commits"], {"main": 2})

    def test_goal_kinds_and_readme_fallback(self):
        cases = {
            "g": ({"CURRENT.md": f"# 当前状态\n\n{GOAL_LINE}\n\n## 节\n"}, ("目标", "做完甲", "CURRENT.md")),
            "m": ({"README.md": "# x\n\n当前阶段目标：常态维护（不设阶段目标）。（2026-11-08 常浩确认，digital-twin#114）\n"},
                  ("常态维护", None, "README.md")),
            "p": ({"CURRENT.md": "# 当前状态\n\n当前阶段目标：暂停，等常浩定。（2026-11-08 常浩确认，digital-twin#114）\n"},
                  ("暂停", None, "CURRENT.md")),
            "n": ({"CURRENT.md": "# 当前状态\n\n## 节\n"}, ("未设", None, None)),
        }
        for name, (files, (kind, text, source)) in cases.items():
            with self.subTest(name):
                g = pd.goal(make_repo(self.root, name, files), "main")
                self.assertEqual((g["kind"], g["source"]), (kind, source))
                if text:
                    self.assertEqual(g["text"], text)

    def test_reads_main_branch_not_working_tree(self):
        r = make_repo(self.root, "w", {"CURRENT.md": f"# 当前状态\n\n{GOAL_LINE}\n\n## 主分支上的节\n- 下一步：主分支的\n"})
        sh(r, "checkout", "-q", "-b", "feat/x")
        commit(r, "分支上改了", "2026-11-08 11:00:00 +0800", "CURRENT.md", "# 当前状态\n\n## 分支的节\n- 下一步：分支的\n")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["objective"]["kind"], "目标")
        self.assertEqual(rec["current_state"]["next_step"], "主分支的")
        self.assertEqual(rec["current_state"]["checkout_branch"], "feat/x")

    def test_no_new_action_still_reports_current_state(self):
        r = make_repo(self.root, "q", {"CURRENT.md": f"# 当前状态\n\n{GOAL_LINE}\n\n## 等验收（2026-11-06）\n- 下一步：常浩验收\n"})
        self.gh()
        rec = self.run_one(r)["projects"][0]
        d = rec["yesterday_delta"]
        self.assertEqual((d["merged_prs"], d["closed_issues"], d["commits"], d["current_changed"]), ([], [], {}, False))
        self.assertEqual(rec["current_state"]["top_section"], "等验收（2026-11-06）")
        self.assertEqual(rec["current_state"]["next_step"], "常浩验收")
        self.assertTrue(rec["current_state"]["next_reliable"])
        self.assertEqual(rec["errors"], [])

    def test_no_current_is_a_note_not_an_error(self):
        r = make_repo(self.root, "k", {"README.md": "# k\n\n当前阶段目标：常态维护。（2026-11-08 常浩确认，digital-twin#114）\n"})
        self.gh()
        out = self.run_one(r)
        rec = out["projects"][0]
        self.assertEqual(rec["errors"], [])
        self.assertTrue(rec["notes"])
        self.assertFalse(rec["current_state"]["next_reliable"])
        self.assertEqual(out["coverage"]["read_ok"], 1)


class GitHubTests(Base):
    PR = {"number": 5, "title": "做甲（#4）", "url": "u5", "body": "Closes #4", "mergedAt": "2026-11-08T02:00:00Z"}

    def test_fetch_failure_is_unknown_not_zero(self):
        r = make_repo(self.root, "f", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        (self.state).mkdir(parents=True, exist_ok=True)
        (self.state / "fail").write_text("error connecting to api.github.com\n")
        out = self.run_one(r)
        rec = out["projects"][0]
        self.assertIsNone(rec["yesterday_delta"]["merged_prs"])
        self.assertIsNone(rec["yesterday_delta"]["closed_issues"])
        self.assertIsNone(rec["current_state"]["open_prs"])
        self.assertIsNone(rec["milestones"])
        self.assertTrue(any("GitHub" in e for e in rec["errors"]))
        self.assertEqual(out["coverage"]["read_ok"], 0)
        self.assertIn(r.name, out["coverage"]["errors"])

    def test_merged_but_not_installed_is_unknown_delivery(self):
        r = make_repo(self.root, "d", {"CURRENT.md": "# 当前状态\n\n## 别的事（2026-11-08）\n- 正文顺带提到 #5 已安装\n"})
        commit(r, "记 CURRENT", "2026-11-08 11:00:00 +0800")
        self.gh(prs=[self.PR, {**self.PR, "number": 3, "mergedAt": "2026-11-07T10:00:00Z"}])  # #3 不在统计日
        rec = self.run_one(r)["projects"][0]
        prs = rec["yesterday_delta"]["merged_prs"]
        self.assertEqual([p["number"] for p in prs], [5])
        self.assertEqual((prs[0]["engineering"], prs[0]["delivery"], prs[0]["acceptance"]), ("已合并", "未知", "未知"))

    def test_installed_and_accepted_need_explicit_record(self):
        cur = ("# 当前状态\n\n## 做甲（#4）（2026-11-08，Claude）\n- 已安装：从 main 复制，cmp 一致\n"
               "\n## 更早的（#6）（2026-11-07）\n- 已安装\n")
        r = make_repo(self.root, "i", {"CURRENT.md": cur})
        acc = {"number": 7, "title": "交接补记：常浩实机验收乙通过（#6）", "url": "u7", "body": "", "mergedAt": "2026-11-08T05:00:00Z"}
        six = {"number": 8, "title": "做乙（#6）", "url": "u8", "body": "", "mergedAt": "2026-11-08T04:00:00Z"}
        self.gh(prs=[self.PR, acc, six])
        prs = {p["number"]: p for p in self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]}
        self.assertEqual(prs[5]["delivery"], "已安装或已发布")   # 当天的节、标题带 #4
        self.assertEqual(prs[5]["acceptance"], "未知")
        self.assertEqual(prs[8]["delivery"], "未知")             # #6 的安装记在 11-07 的节里，不算这一天
        self.assertEqual(prs[8]["acceptance"], "已通过")         # PR #7 标题明写常浩验收通过（#6）
        self.assertIn("PR #7 标题", prs[8]["evidence"][-1])

    def test_stale_current_makes_next_step_unreliable(self):
        r = make_repo(self.root, "s", {"CURRENT.md": "# 当前状态\n\n## 老的一节（2026-11-06）\n- 下一步：老的\n"},
                      at="2026-11-06 10:00:00 +0800")
        self.gh(prs=[{**self.PR, "mergedAt": "2026-11-08T10:00:00Z"}])
        rec = self.run_one(r)["projects"][0]
        self.assertTrue(rec["freshness"]["stale"])
        self.assertEqual(rec["current_state"]["next_step"], "老的")
        self.assertFalse(rec["current_state"]["next_reliable"])

    def test_milestones_native_and_checklist_but_not_acceptance_lists(self):
        api = {"graphql": {"data": {"repository": {"issues": {"nodes": [
            {"number": 1, "title": "原生父单", "body": "", "subIssues": {"nodes": [{"state": "CLOSED"}, {"state": "OPEN"}]}},
            {"number": 2, "title": "清单父单", "body": "- [x] #10 批 0\n- [ ] #11 批 1\n", "subIssues": {"nodes": []}},
            {"number": 3, "title": "验收清单", "body": "- [ ] 跑通，见 #9\n- [ ] 截图\n", "subIssues": {"nodes": []}},
            {"number": 4, "title": "只有一条", "body": "- [ ] #12 唯一一条\n", "subIssues": {"nodes": []}},
        ]}}}}}
        r = make_repo(self.root, "m", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        self.gh(api=api)
        ms = self.run_one(r)["projects"][0]["milestones"]
        self.assertEqual([(m["number"], m["done"], m["total"], m["source"]) for m in ms],
                         [(1, 1, 2, "原生子任务"), (2, 1, 2, "正文清单")])

    def test_project_without_github_has_empty_lists(self):
        r = make_repo(self.root, "l", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        rec = self.run_one(r, github=None)["projects"][0]
        self.assertEqual(rec["yesterday_delta"]["merged_prs"], [])
        self.assertEqual(rec["milestones"], [])


class NextStepTests(unittest.TestCase):
    def test_top_section_wins_over_stale_header(self):
        lines = ("# 当前状态\n\n更新：2026-10-23。唯一下一步：旧的\n\n## 最新（2026-11-08）\n- **下一步**：新的\n"
                 "\n## 更早\n- 下一步：更早的\n").splitlines()
        self.assertEqual(progress_data.next_step(lines), "新的")

    def test_header_used_when_top_section_has_none(self):
        lines = ("# 当前状态\n\n更新：2026-11-06。" + "很长的说明" * 60 + "。唯一下一步：头部的\n\n## 最新\n- 没写\n"
                 "\n## 更早\n- 下一步：更早的\n").splitlines()
        self.assertEqual(progress_data.next_step(lines), "头部的")   # 长行先去前缀再截断

    def test_older_sections_are_not_used(self):
        lines = "# 当前状态\n\n## 最新\n- 没写\n\n## 更早\n- 下一步：更早的\n".splitlines()
        self.assertIsNone(progress_data.next_step(lines))


if __name__ == "__main__":
    unittest.main()
