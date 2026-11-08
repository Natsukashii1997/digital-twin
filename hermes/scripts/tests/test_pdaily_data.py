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
from unittest import mock

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

    def run_one(self, r, github="o/fx", main="main"):
        p = {"id": r.name, "name": r.name, "path": str(r), "main": main, "github": github}
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

    def test_current_changed_uses_window_not_last_commit(self):
        r = make_repo(self.root, "cc", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        commit(r, "当天记", "2026-11-08 15:00:00 +0800", "CURRENT.md", "# 当前状态\n\n## 当天\n")
        commit(r, "第二天 06:00 又记", "2026-11-09 06:00:00 +0800", "CURRENT.md", "# 当前状态\n\n## 次日\n")
        self.gh()
        self.assertIs(self.run_one(r)["projects"][0]["yesterday_delta"]["current_changed"], True)
        r = make_repo(self.root, "cn", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        commit(r, "只改了代码", "2026-11-08 15:00:00 +0800", "a.py", "x")
        commit(r, "窗口外记", "2026-11-09 06:00:00 +0800", "CURRENT.md", "# 当前状态\n\n## 次日\n")
        self.assertIs(self.run_one(r)["projects"][0]["yesterday_delta"]["current_changed"], False)

    def test_origin_main_newer_than_local_main_wins(self):
        up = make_repo(self.root, "up", {"CURRENT.md": "# 当前状态\n\n## 旧\n- 下一步：旧的\n"})
        sh(self.root, "clone", "-q", str(up), "down")
        down = self.root / "down"
        commit(up, "上游更新", "2026-11-08 12:00:00 +0800", "CURRENT.md", "# 当前状态\n\n## 新\n- 下一步：新的\n")
        sh(down, "fetch", "-q")
        self.gh()
        rec = self.run_one(down)["projects"][0]
        self.assertEqual(rec["current_state"]["next_step"], "新的")
        self.assertEqual(rec["current_state"]["checkout_branch"], "main")

    def test_missing_main_branch_is_error_not_unset(self):
        r = self.root / "mm"
        r.mkdir()
        sh(r, "init", "-q", "-b", "master")
        (r / "CURRENT.md").write_text(f"# 当前状态\n\n{GOAL_LINE}\n\n## 节\n- 下一步：x\n", encoding="utf-8")
        sh(r, "add", "-A")
        sh(r, "commit", "-q", "-m", "起步")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["objective"]["kind"], "未知")
        self.assertTrue(any("找不到主分支" in e for e in rec["errors"]))
        self.assertFalse(any("没有 CURRENT" in n for n in rec["notes"]))
        self.assertIsNone(rec["freshness"]["stale"])
        self.assertIsNone(rec["current_state"]["next_reliable"])
        self.assertIsNone(rec["yesterday_delta"]["current_changed"])

    def test_non_main_trunk_and_cross_branch_dedupe(self):
        r = self.root / "tr"
        r.mkdir()
        sh(r, "init", "-q", "-b", "trunk")
        (r / "CURRENT.md").write_text(f"# 当前状态\n\n{GOAL_LINE}\n\n## 节\n", encoding="utf-8")
        sh(r, "add", "-A")
        sh(r, "commit", "-q", "-m", "起步", at="2026-11-06 10:00:00 +0800")
        commit(r, "主干上", "2026-11-08 10:00:00 +0800")
        sh(r, "checkout", "-q", "-b", "feat")
        commit(r, "分支上", "2026-11-08 11:00:00 +0800")
        self.gh()
        rec = self.run_one(r, main="trunk")["projects"][0]
        self.assertEqual(rec["yesterday_delta"]["commits"], {"trunk": 1, "feat": 1})   # 主干上那条不重复算给 feat
        self.assertEqual(rec["objective"]["kind"], "目标")
        self.assertEqual(rec["errors"], [])

    def test_reads_main_as_of_stat_day_end(self):
        r = make_repo(self.root, "asof", {"README.md": "x", "CURRENT.md": f"# 当前状态\n\n{GOAL_LINE}\n\n## 当天（2026-11-08）\n- 下一步：A\n"},
                      at="2026-11-08 10:00:00 +0800")
        commit(r, "凌晨补一节", "2026-11-09 00:30:00 +0800", "CURRENT.md",
               f"# 当前状态\n\n{GOAL_LINE}\n\n## 凌晨（#70）（2026-11-09）\n- 已安装：cmp 一致\n- 下一步：B\n\n## 当天（2026-11-08）\n- 下一步：A\n")
        commit(r, "第二天白天", "2026-11-09 10:00:00 +0800", "CURRENT.md", "# 当前状态\n\n## 次日（2026-11-09）\n- 下一步：C\n")
        commit(r, "第二天合并", "2026-11-09 12:00:00 +0800", "a.py", "x")
        self.gh(prs=[{"number": 70, "title": "做（#70）", "url": "", "body": "", "mergedAt": "2026-11-08T16:00:00Z"}])
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["current_state"]["next_step"], "B")              # 不是 04:00 以后的 C
        self.assertEqual(rec["objective"]["kind"], "目标")
        self.assertEqual(rec["freshness"]["current_last_commit"], "2026-11-09T00:30:00+08:00")
        self.assertEqual(rec["freshness"]["last_merge"], "2026-11-08T10:00:00+08:00")   # 11-09 12:00 的合并不算
        self.assertEqual(rec["yesterday_delta"]["merged_prs"][0]["delivery"], "已安装或已发布")   # D+1 凌晨的节算 D
        self.assertFalse(any("文件头" in n for n in rec["notes"]))

    def test_main_without_commits_before_day_end(self):
        r = make_repo(self.root, "late", {"CURRENT.md": f"# 当前状态\n\n{GOAL_LINE}\n"}, at="2026-11-10 10:00:00 +0800")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["objective"]["kind"], "未知")
        self.assertTrue(any("统计日结束前还没有提交" in e for e in rec["errors"]))
        self.assertIsNone(rec["freshness"]["stale"])

    def test_branch_named_like_a_directory(self):
        r = make_repo(self.root, "amb", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        (r / "docs").mkdir()
        commit(r, "加 docs", "2026-11-08 10:00:00 +0800", "docs/a.md", "x")
        sh(r, "branch", "docs")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["yesterday_delta"]["commits"], {"main": 1})
        self.assertEqual(rec["errors"], [])

    def test_local_git_failure_is_unknown(self):
        r = self.root / "notgit"
        r.mkdir()
        (r / "CURRENT.md").write_text("# 当前状态\n", encoding="utf-8")
        self.gh()
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": str(self.root)}):
            out = self.run_one(r)
        rec = out["projects"][0]
        self.assertEqual(rec["objective"]["kind"], "未知")
        self.assertIsNone(rec["yesterday_delta"]["commits"])
        self.assertIsNone(rec["freshness"]["stale"])
        self.assertEqual(len(rec["errors"]), 1)
        self.assertIn("读不了仓库", rec["errors"][0])
        self.assertEqual(out["coverage"]["read_ok"], 0)
        with mock.patch.object(pd.subprocess, "run", side_effect=FileNotFoundError("git")):   # 机器上没有 git：不崩
            rec = pd.project({"id": "x", "name": "x", "path": str(r), "github": None}, pd.window(D), pd.GitHub(pd.window(D)))
        self.assertIn("读不了仓库", rec["errors"][0])

    def test_last_change_failure_leaves_freshness_unknown(self):
        r = make_repo(self.root, "lf", {"CURRENT.md": "# 当前状态\n\n## 节\n- 下一步：x\n"})
        self.gh()
        with mock.patch.object(pd, "last_change", side_effect=pd.Fail("坏了")):
            rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["current_state"]["next_step"], "x")
        self.assertIsNone(rec["freshness"]["stale"])
        self.assertIsNone(rec["current_state"]["next_reliable"])
        self.assertTrue(any("坏了" in e for e in rec["errors"]))

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
        commit(r, "合并 #5", "2026-11-08 18:00:00 +0800", "a.py", "x")
        self.gh(prs=[{**self.PR, "mergedAt": "2026-11-08T10:00:00Z"}])
        rec = self.run_one(r)["projects"][0]
        self.assertTrue(rec["freshness"]["stale"])
        self.assertEqual(rec["freshness"]["last_merge"], "2026-11-08T18:00:00+08:00")
        self.assertEqual(rec["current_state"]["next_step"], "老的")
        self.assertFalse(rec["current_state"]["next_reliable"])

    def test_stale_does_not_depend_on_gh_or_window(self):
        cur = {"CURRENT.md": "# 当前状态\n\n## 老的一节（2026-11-05）\n- 下一步：老的\n"}
        # gh 连不上：新鲜度照样从本机主分支算出来，不是 False 也不是 None
        r = make_repo(self.root, "sg", cur, at="2026-11-05 10:00:00 +0800")
        commit(r, "合并", "2026-11-08 12:00:00 +0800", "a.py", "x")
        self.gh()
        (self.state / "fail").write_text("error connecting to api.github.com\n")
        rec = self.run_one(r)["projects"][0]
        self.assertTrue(any("GitHub" in e for e in rec["errors"]))
        self.assertIs(rec["freshness"]["stale"], True)
        self.assertIs(rec["current_state"]["next_reliable"], False)
        (self.state / "fail").unlink()
        # 合并在统计日之前（11-07），当天没有合并：照样算过期
        r = make_repo(self.root, "sw", cur, at="2026-11-05 10:00:00 +0800")
        commit(r, "合并", "2026-11-07 20:00:00 +0800", "a.py", "x")
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["yesterday_delta"]["merged_prs"], [])
        self.assertIs(rec["freshness"]["stale"], True)
        # 只改 CURRENT 的提交不算合并；CURRENT 在最近一次合并之后更新过：不过期
        r = make_repo(self.root, "sf", cur, at="2026-11-05 10:00:00 +0800")
        commit(r, "合并", "2026-11-07 20:00:00 +0800", "a.py", "x")
        commit(r, "记 CURRENT", "2026-11-08 09:00:00 +0800", "CURRENT.md", "# 当前状态\n\n## 新（2026-11-08）\n- 下一步：新的\n")
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["freshness"]["last_merge"], "2026-11-07T20:00:00+08:00")
        self.assertIs(rec["freshness"]["stale"], False)
        self.assertIs(rec["current_state"]["next_reliable"], True)

    def test_negated_and_future_forms_do_not_count(self):
        bad = ["常浩验收未通过", "常浩验收还没通过", "等常浩验收通过后再装", "未装机", "待装机", "已安装失败，回滚了",
               # 第二轮审查：条件、前提、将来时、转折、撤回
               "下一步：常浩装机", "等合并后装机", "需要重新装机", "装机命令见下", "是否已安装要再确认",
               "如果已安装就重启网关", "撤掉已安装的旧版", "等常浩验收通过", "需要常浩实机验收通过才能装",
               "常浩验收通过后合并", "若常浩验收通过即可装", "交接：常浩实机验收通过后装机。",
               "已安装，但回读不一致", "已安装，随后回滚", "已安装；后发现没生效，已卸载"]
        secs = "".join(f"## 事{i}（#{100 + i}）（2026-11-08）\n- {t}\n\n" for i, t in enumerate(bad))
        secs += ("## 自述（#30）（2026-11-08）\n- 已安装，没回读\n- 另一行：cmp 过别的文件\n\n"
                 "## 回读（#31）（2026-11-08）\n- 已安装：从 main 复制，cmp 一致\n- 常浩实机验收通过\n")
        r = make_repo(self.root, "neg", {"CURRENT.md": f"# 当前状态\n\n{secs}"})
        pr = lambda n, title=None: {"number": n, "title": title or f"做（#{n}）", "url": "", "body": "",
                                    "mergedAt": "2026-11-08T03:00:00Z"}
        self.gh(prs=[pr(100 + i) for i in range(len(bad))] + [pr(30), pr(31), pr(40, "交接补记：常浩实机验收未通过（#41）"), pr(41)])
        prs = {p["number"]: p for p in self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]}
        for i, text in enumerate(bad):
            with self.subTest(text):
                self.assertEqual((prs[100 + i]["delivery"], prs[100 + i]["acceptance"]), ("未知", "未知"))
                self.assertIsNone(prs[100 + i]["delivery_basis"])
        self.assertEqual(prs[41]["acceptance"], "未知")                       # PR 标题写的是未通过
        self.assertEqual((prs[30]["delivery"], prs[30]["delivery_basis"]), ("已安装或已发布", "仅自述"))
        self.assertEqual((prs[31]["delivery_basis"], prs[31]["acceptance"]), ("有回读或 cmp 记录", "已通过"))
        self.assertEqual(prs[31]["acceptance_basis"], "交接记录（非本人回执）")
        self.assertIsNone(prs[30]["acceptance_basis"])

    def test_only_own_title_and_closing_refs_link_evidence(self):
        cur = ("# 当前状态\n\n## 别的（#60）（2026-11-08）\n- 已安装：cmp 一致\n\n"
               "## 甲（#49）（2026-11-08）\n- 已安装：cmp 一致\n\n"
               "## 自己（#52）（2026-11-08）\n- 已安装，cmp 一致\n\n## 关联（#51）（2026-11-08）\n- 已安装\n")
        r = make_repo(self.root, "refs", {"CURRENT.md": cur})
        pr = lambda n, title, body: {"number": n, "title": title, "url": "", "body": body, "mergedAt": "2026-11-08T03:00:00Z"}
        self.gh(prs=[pr(50, "做乙", "参考 #60 的做法"), pr(53, "做甲", "Closes #49"), pr(52, "做丙（#51）", "")])
        prs = {p["number"]: p for p in self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]}
        self.assertEqual(prs[50]["delivery"], "未知")                       # 正文顺带提到 #60 不继承
        self.assertEqual(prs[53]["delivery"], "已安装或已发布")               # Closes #49 算
        self.assertEqual(prs[52]["delivery_basis"], "有回读或 cmp 记录")      # 自己的号优先，不随 set 顺序变
        self.assertIn("自己（#52）", prs[52]["evidence"][1])
        self.assertIn("节级证据", prs[52]["evidence"][1])

    def test_header_only_commit_does_not_refresh_stale_current(self):
        r = make_repo(self.root, "hd", {"CURRENT.md": "# 当前状态\n\n## 旧节（2026-11-05）\n- 下一步：旧的\n"},
                      at="2026-11-05 10:00:00 +0800")
        commit(r, "合并", "2026-11-07 20:00:00 +0800", "a.py", "x")
        commit(r, "只加目标行", "2026-11-08 09:00:00 +0800", "CURRENT.md",
               f"# 当前状态\n\n{GOAL_LINE}\n\n## 旧节（2026-11-05）\n- 下一步：旧的\n")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["freshness"]["current_as_of"], "2026-11-06T04:00:00+08:00")
        self.assertIs(rec["freshness"]["stale"], True)
        self.assertIs(rec["current_state"]["next_reliable"], False)
        self.assertIs(rec["yesterday_delta"]["current_changed"], True)
        self.assertTrue(any("可能只改了文件头" in n for n in rec["notes"]))

    def test_last_merge_follows_first_parent(self):
        r = make_repo(self.root, "fp", {"CURRENT.md": "# 当前状态\n\n## 节（2026-11-05）\n- 下一步：x\n"},
                      at="2026-11-05 10:00:00 +0800")
        sh(r, "checkout", "-q", "-b", "old")
        commit(r, "很早写好的分支", "2026-11-01 10:00:00 +0800", "a.py", "x")
        sh(r, "checkout", "-q", "main")
        sh(r, "merge", "-q", "--no-ff", "-m", "普通合并", "old", at="2026-11-08 10:00:00 +0800")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["freshness"]["last_merge"], "2026-11-08T10:00:00+08:00")
        self.assertIs(rec["freshness"]["stale"], True)

    def test_gh_timeout_skips_remaining_repos(self):
        real, calls = subprocess.run, []

        def fake(argv, *a, **k):
            if argv[0].endswith("gh"):
                calls.append(argv)
                raise subprocess.TimeoutExpired(argv, 60)
            return real(argv, *a, **k)
        repos = [make_repo(self.root, n, {"CURRENT.md": "# 当前状态\n\n## 节\n"}) for n in ("t1", "t2")]
        self.gh()
        with mock.patch.object(pd.subprocess, "run", side_effect=fake):
            out = pd.collect(D, [{"id": r.name, "name": r.name, "path": str(r), "main": "main", "github": "o/fx"}
                                 for r in repos])
        self.assertEqual(len(calls), 1)
        for rec in out["projects"]:
            self.assertEqual(len([e for e in rec["errors"] if "60 秒" in e]), 4)
            self.assertIsNone(rec["yesterday_delta"]["merged_prs"])

    def test_non_json_output_is_a_fetch_error(self):
        r = make_repo(self.root, "nj", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        self.gh()
        (self.state / "pr_list.json").write_text("gh: 出错了\n")
        rec = self.run_one(r)["projects"][0]
        self.assertIsNone(rec["yesterday_delta"]["merged_prs"])
        self.assertTrue(any("不是 JSON" in e for e in rec["errors"]))

    def test_capped_list_gets_a_note(self):
        r = make_repo(self.root, "cap", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        self.gh(prs=[{"number": n, "title": "t", "createdAt": "2026-11-01T00:00:00Z"} for n in range(100)])
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(len(rec["current_state"]["open_prs"]), 100)
        self.assertEqual([n for n in rec["notes"] if "上限" in n], ["GitHub open_prs 到了 100 条上限，可能截断"])

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
        self.assertTrue(any("没有 GitHub" in n for n in rec["notes"]))


class NextStepTests(unittest.TestCase):
    def test_top_section_wins_over_stale_header(self):
        lines = ("# 当前状态\n\n更新：2026-10-23。唯一下一步：旧的\n\n## 最新（2026-11-08）\n- **下一步**：新的\n"
                 "\n## 更早\n- 下一步：更早的\n").splitlines()
        self.assertEqual(progress_data.next_step(lines), "新的")

    def test_header_used_when_top_section_has_none(self):
        lines = ("# 当前状态\n\n更新：2026-11-06。" + "很长的说明" * 60 + "。唯一下一步：头部的\n\n## 最新\n- 没写\n"
                 "\n## 更早\n- 下一步：更早的\n").splitlines()
        self.assertEqual(progress_data.next_step(lines), "头部的")   # 长行先去前缀再截断

    def test_touched_on_uses_file_time_when_current_is_dirty(self):
        with tempfile.TemporaryDirectory(prefix="digital-twin-pdaily-test-") as tmp:
            r = make_repo(Path(tmp), "dirty", {"CURRENT.md": "# 当前状态\n"})   # 11-06 提交
            f = r / "CURRENT.md"
            f.write_text("# 当前状态\n\n## 还没提交的一节\n", encoding="utf-8")
            t = dt.datetime(2026, 11, 8, 12, 0).timestamp()
            os.utime(f, (t, t))
            self.assertTrue(progress_data.touched_on(r, f, D))
            sh(r, "checkout", "-q", "--", "CURRENT.md")
            os.utime(f, (t, t))
            self.assertFalse(progress_data.touched_on(r, f, D))   # 干净时按提交日期 11-06

    def test_older_sections_are_not_used(self):
        lines = "# 当前状态\n\n## 最新\n- 没写\n\n## 更早\n- 下一步：更早的\n".splitlines()
        self.assertIsNone(progress_data.next_step(lines))


if __name__ == "__main__":
    unittest.main()
