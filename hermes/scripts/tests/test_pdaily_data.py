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
AT8 = "2026-11-08 10:00:00 +0800"   # 统计日 D 当天：这时写的节才算当天的
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
        self.assertFalse(pd.inside("2026-11-08T20:00:00Z", w))   # 北京 11-09 04:00 整：窗口右端不含
        self.assertTrue(pd.inside("2026-11-08T19:59:59Z", w))
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
        self.assertEqual(rec["current_state"]["next_source"], "最上面一节")
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
        self.assertEqual(rec["yesterday_delta"]["merged_prs"][0]["claims"][0]["quotes"], ["- 已安装：cmp 一致"])   # D+1 凌晨的节算 D
        self.assertFalse(any("文件头" in n for n in rec["notes"]))

    def test_early_morning_section_is_not_listed_again_next_day(self):
        r = make_repo(self.root, "early", {"README.md": "x", "CURRENT.md": "# 当前状态\n\n## 旧（2026-11-07）\n"},
                      at="2026-11-07 10:00:00 +0800")
        commit(r, "凌晨记一节", "2026-11-09 02:00:00 +0800", "CURRENT.md",
               "# 当前状态\n\n## 凌晨（#70）（2026-11-09）\n- 已安装：七十\n\n## 旧（2026-11-07）\n")
        pr = {"number": 71, "title": "做（#70）", "url": "", "body": "", "mergedAt": "2026-11-08T18:00:00Z"}   # 北京 11-09 02:00
        self.gh(prs=[pr])
        p = {"id": r.name, "name": r.name, "path": str(r), "main": "main", "github": "o/fx"}
        d8 = pd.collect(D, [p])["projects"][0]["yesterday_delta"]
        self.assertEqual(d8["merged_prs"][0]["claims"][0]["quotes"], ["- 已安装：七十"])   # 属于统计日 11-08
        self.gh(prs=[{**pr, "number": 75, "title": "撤回（#70）", "mergedAt": "2026-11-09T06:00:00Z"}])
        d9 = pd.collect(D + dt.timedelta(days=1), [p])["projects"][0]["yesterday_delta"]
        self.assertEqual(d9["merged_prs"][0]["claims"], [])       # 11-09 不再列、也不挂给同号的新 PR
        self.assertEqual(d9["unmatched_sections"], [])

    def test_section_written_on_branch_and_merged_later_counts_on_merge_day(self):
        r = make_repo(self.root, "late", {"CURRENT.md": "# 当前状态\n\n## 旧节（2026-10-20）\n旧\n", "x.txt": "0"})
        sh(r, "checkout", "-q", "-b", "feat")
        commit(r, "分支写节", "2026-11-07 22:00:00 +0800", "CURRENT.md",
               "# 当前状态\n\n## 修 X（#50）（2026-11-07，Claude）\n- 已安装到本机\n\n## 更早（#40）（2026-10-30）\n- 已安装：四十\n\n"
               "## 旧节（2026-10-20）\n旧\n")
        sh(r, "checkout", "-q", "main")
        sh(r, "merge", "-q", "--no-ff", "feat", "-m", "Merge PR #51", at="2026-11-08 10:00:00 +0800")
        self.gh(prs=[{"number": 51, "title": "修 X", "url": "", "body": "Closes #50", "mergedAt": "2026-11-08T02:00:00Z"}])
        rec = self.run_one(r)["projects"][0]
        pr = rec["yesterday_delta"]["merged_prs"][0]
        self.assertEqual([c["quotes"] for c in pr["claims"]], [["- 已安装到本机"]])   # 标着前一天、当天才进主分支：算当天
        self.assertEqual(rec["yesterday_delta"]["unmatched_sections"], [])          # 早于 7 天的「更早」不认
        self.assertFalse(any("只改了文件头" in n for n in rec["notes"]))

    def test_section_appended_after_0400_counts_again(self):
        r = make_repo(self.root, "app", {"CURRENT.md": "# 当前状态\n\n## 旧节（2026-11-01）\n旧\n"})
        commit(r, "凌晨", "2026-11-08 02:00:00 +0800", "CURRENT.md",
               "# 当前状态\n\n## 修 Y（#60）（2026-11-08）\n- 开工\n\n## 旧节（2026-11-01）\n旧\n")
        commit(r, "白天", "2026-11-08 10:00:00 +0800", "CURRENT.md",
               "# 当前状态\n\n## 修 Y（#60）（2026-11-08）\n- 开工\n- 已安装、回读一致\n\n## 旧节（2026-11-01）\n旧\n")
        self.gh(prs=[{"number": 61, "title": "修 Y", "url": "", "body": "Closes #60", "mergedAt": "2026-11-08T03:00:00Z"}])
        pr = self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"][0]
        self.assertEqual(pr["claims"][0]["quotes"], ["- 已安装、回读一致"])

    def test_non_utf8_current_is_an_error_for_that_project_only(self):
        bad = make_repo(self.root, "gbk", {"README.md": "x"})
        (bad / "CURRENT.md").write_bytes("# 当前状态\n\n## 节（2026-11-08）\n- 下一步：甲\n".encode("gbk"))
        sh(bad, "add", "-A")
        sh(bad, "commit", "-q", "-m", "gbk")
        good = make_repo(self.root, "ok", {"CURRENT.md": "# 当前状态\n\n## 节（2026-11-08）\n- 下一步：乙\n"})
        self.gh()
        out = pd.collect(D, [{"id": r.name, "name": r.name, "path": str(r), "main": "main", "github": None} for r in (bad, good)])
        b, g = out["projects"]
        self.assertTrue(any("不是 UTF-8" in e for e in b["errors"]))
        self.assertIsNone(b["current_state"]["next_step"])
        self.assertEqual(g["current_state"]["next_step"], "乙")

    def test_commits_count_main_from_snapshot_when_local_is_behind(self):
        up = make_repo(self.root, "up2", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        sh(self.root, "clone", "-q", str(up), "down2")
        commit(up, "当天合并", "2026-11-08 12:00:00 +0800", "a.py", "x")
        down = self.root / "down2"
        sh(down, "fetch", "-q")                                   # 本机 main 没拉，origin/main 有当天的提交
        self.gh()
        self.assertEqual(self.run_one(down)["projects"][0]["yesterday_delta"]["commits"], {"main": 1})

    def test_main_without_commits_before_day_end(self):
        r = make_repo(self.root, "late", {"CURRENT.md": f"# 当前状态\n\n{GOAL_LINE}\n"}, at="2026-11-10 10:00:00 +0800")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["objective"]["kind"], "未知")
        self.assertTrue(any("统计日结束前还没有提交" in e for e in rec["errors"]))
        self.assertIsNone(rec["freshness"]["stale"])

    def test_no_ff_merge_counts_as_current_change(self):
        r = make_repo(self.root, "noff", {"README.md": "x", "CURRENT.md": "# 当前状态\n\n## 旧（2026-11-05）\n- 下一步：旧\n"},
                      at="2026-11-05 10:00:00 +0800")
        sh(r, "checkout", "-q", "-b", "feat")
        commit(r, "分支上记 CURRENT", "2026-11-07 10:00:00 +0800", "CURRENT.md", "# 当前状态\n\n## 新（2026-11-08）\n- 下一步：新\n")
        sh(r, "checkout", "-q", "main")
        sh(r, "merge", "-q", "--no-ff", "-m", "普通合并", "feat", at="2026-11-08 20:00:00 +0800")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["freshness"]["current_last_commit"], "2026-11-08T20:00:00+08:00")
        self.assertIs(rec["yesterday_delta"]["current_changed"], True)
        self.assertIs(rec["freshness"]["stale"], False)

    def test_next_step_from_header_needs_current_update_date(self):
        cases = {
            "old": ("更新：2026-11-06。唯一下一步：头部的", "文件头", False),    # 文件头比最上面一节旧
            "cur": ("更新：2026-11-08。唯一下一步：头部的", "文件头", True),     # 文件头和最上面一节同一天
            "nod": ("唯一下一步：头部的", "文件头", False),                     # 文件头没写更新日期
        }
        self.gh()
        for name, (head, source, reliable) in cases.items():
            with self.subTest(name):
                r = make_repo(self.root, f"hdr-{name}", {"CURRENT.md": f"# 当前状态\n\n{head}\n\n## 最新（2026-11-08）\n- 没写\n"})
                st = self.run_one(r)["projects"][0]["current_state"]
                self.assertEqual((st["next_step"], st["next_source"]), ("头部的", source))
                self.assertIs(st["next_reliable"], reliable)
        r = make_repo(self.root, "hdr-sec", {"CURRENT.md": "# 当前状态\n\n## 最新（2026-11-08）\n- 没写\n\n## 唯一下一步\n老的\n"})
        st = self.run_one(r)["projects"][0]["current_state"]
        self.assertEqual((st["next_step"], st["next_source"], st["next_reliable"]), ("老的", "「唯一下一步」节", False))

    def test_missing_repo_dir_and_null_issue_list(self):
        rec = pd.project({"id": "x", "name": "x", "path": str(self.root / "没有"), "github": None}, pd.window(D), pd.GitHub(pd.window(D)))
        self.assertEqual(rec["objective"]["kind"], "未知")
        r = make_repo(self.root, "nul", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        self.gh(api={"graphql": {"data": {"repository": {"issues": None}}}})
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["milestones"], [])

    def test_bad_date_argument_is_a_clean_failure(self):
        out = subprocess.run([sys.executable, "-B", str(HERE.parent / "pdaily_data.py"), "--date", "2026-13-01"],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 1)
        self.assertTrue(out.stdout.startswith("取数失败：--date"))

    def test_bad_section_date_does_not_crash(self):
        r = make_repo(self.root, "bd", {"README.md": "x", "CURRENT.md": "# 当前状态\n\n## 记（2026-11-31）\n- 下一步：x\n"})
        self.gh()
        out = self.run_one(r)
        rec = out["projects"][0]
        self.assertIsNone(rec["freshness"]["top_section_date"])
        self.assertTrue(any("没有日期" in n for n in rec["notes"]))
        self.assertEqual(rec["errors"], [])

    def test_show_tells_missing_file_from_git_failure(self):
        r = make_repo(self.root, "sh", {"CURRENT.md": "x"})
        self.assertIsNone(pd.show(r, "main", "README.md"))
        self.assertEqual(pd.show(r, "main", "CURRENT.md"), "x")
        with self.assertRaises(pd.Fail):
            pd.show(r, "没有这个分支", "CURRENT.md")
        with mock.patch.object(pd, "goal", side_effect=pd.Fail("坏了")):
            self.gh()
            rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["objective"]["kind"], "未知")
        self.assertTrue(any("读阶段目标失败" in e for e in rec["errors"]))

    def test_branch_named_like_a_directory(self):
        r = make_repo(self.root, "amb", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        (r / "docs").mkdir()
        commit(r, "加 docs", "2026-11-08 10:00:00 +0800", "docs/a.md", "x")
        sh(r, "branch", "docs")
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["yesterday_delta"]["commits"], {"main": 1})
        self.assertEqual(rec["errors"], [])

    def test_branch_deleted_mid_run(self):
        r = make_repo(self.root, "del", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        sh(r, "checkout", "-q", "-b", "gone")
        commit(r, "分支上", "2026-11-08 11:00:00 +0800")
        sh(r, "checkout", "-q", "main")
        real = pd.git

        def git(root, *args):   # 列完分支，别的会话马上把 gone 删了
            out = real(root, *args)
            if args[0] == "for-each-ref":
                sh(r, "branch", "-q", "-D", "gone")
            return out
        self.gh()
        with mock.patch.object(pd, "git", side_effect=git):
            rec = self.run_one(r)["projects"][0]
        self.assertEqual(rec["errors"], [])
        self.assertEqual(rec["yesterday_delta"]["commits"], {"gone": 1})

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

    def test_merged_prs_are_unverified_with_quotes_only(self):
        old = "\n## 更早（#4）（2026-11-07）\n- 已安装：旧的\n"      # 统计日开始前就在主分支上
        r = make_repo(self.root, "u", {"CURRENT.md": "# 当前状态\n" + old}, at="2026-11-07 10:00:00 +0800")
        commit(r, "当天", AT8, "CURRENT.md",
               "# 当前状态\n\n## 做甲（#4）（2026-11-08，Claude）\n- **已安装**：cmp 一致\n- 常浩实机验收通过\n- 改了 x.py\n" + old)
        self.gh(prs=[self.PR, {**self.PR, "number": 3, "mergedAt": "2026-11-07T10:00:00Z"}])   # #3 不在统计日
        prs = self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]
        self.assertEqual([p["number"] for p in prs], [5])
        p = prs[0]
        self.assertEqual((p["engineering"], p["delivery"], p["acceptance"]), ("已合并", "未核实", "未核实"))
        self.assertEqual(p["claims"], [{"source": "CURRENT「做甲（#4）（2026-11-08，Claude）」",
                                        "quotes": ["- **已安装**：cmp 一致", "- 常浩实机验收通过"], "omitted": 0}])   # 只摘当天那一节
        self.assertEqual(p["matched_sections"], ["做甲（#4）（2026-11-08，Claude）"])
        self.assertEqual(p["claims_warnings"], [])
        self.assertEqual(p["claims_note"], pd.CLAIMS_NOTE)
        self.assertEqual(p["evidence"], ["PR #5 merged"])

    def test_negative_wording_is_quoted_verbatim_not_judged(self):
        lines = ["常浩验收未通过", "等常浩验收通过后再装", "- **已安装**：**否**", "已发布 `abcdef1` 回读未通过",
                 "下一步：常浩实机验收通过", "已安装，但回读不一致", "回读：未做", "已安装；后发现没生效，已卸载"]
        body = "".join(f"- {x}\n" for x in lines)
        r = make_repo(self.root, "neg", {"CURRENT.md": f"# 当前状态\n\n## 事（#100）（2026-11-08）\n{body}- 改了别的\n"}, at=AT8)
        pr = lambda n, title: {"number": n, "title": title, "url": "", "body": "", "mergedAt": "2026-11-08T03:00:00Z"}
        self.gh(prs=[pr(101, "做（#100）"), pr(102, "无关（#99）")])
        prs = {p["number"]: p for p in self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]}
        self.assertEqual((prs[101]["delivery"], prs[101]["acceptance"]), ("未核实", "未核实"))
        self.assertEqual(prs[101]["claims"][0]["quotes"], [f"- {x}" for x in lines])   # 原样摘出，不判断
        self.assertEqual((prs[102]["claims"], prs[102]["claims_note"]), ([], pd.NO_CLAIMS_NOTE))   # 没摘录也说明原因

    def test_pr_title_claim_is_quoted_for_that_pr_only(self):
        r = make_repo(self.root, "t", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        acc = {"number": 7, "title": "交接补记：常浩实机验收乙通过（#6）", "url": "", "body": "", "mergedAt": "2026-11-08T05:00:00Z"}
        six = {"number": 8, "title": "做乙（#6）", "url": "", "body": "", "mergedAt": "2026-11-08T04:00:00Z"}
        self.gh(prs=[acc, six])
        prs = {p["number"]: p for p in self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]}
        self.assertEqual(prs[7]["claims"], [{"source": "PR #7 标题", "quotes": ["交接补记：常浩实机验收乙通过（#6）"], "omitted": 0}])
        self.assertEqual(prs[7]["acceptance"], "未核实")
        self.assertEqual(prs[8]["claims"], [])

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

    def test_only_own_title_and_closing_refs_link_claims(self):
        cur = ("# 当前状态\n\n## 别的（#60）（2026-11-08）\n- 已安装：六十\n\n"
               "## 甲（#49）（2026-11-08）\n- 已安装：四九\n\n## 自己（#52）（2026-11-08）\n- 已安装：五二\n")
        r = make_repo(self.root, "refs", {"CURRENT.md": cur}, at=AT8)
        pr = lambda n, title, body: {"number": n, "title": title, "url": "", "body": body, "mergedAt": "2026-11-08T03:00:00Z"}
        self.gh(prs=[pr(50, "做乙", "参考 #60 的做法"), pr(53, "做甲", "Closes #49"), pr(52, "做丙", "")])
        prs = {p["number"]: p for p in self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]}
        self.assertEqual(prs[50]["claims"], [])                                  # 正文顺带提到 #60 不挂
        self.assertEqual(prs[53]["claims"][0]["quotes"], ["- 已安装：四九"])       # Closes #49 算
        self.assertEqual(prs[52]["claims"][0]["quotes"], ["- 已安装：五二"])       # 自己的号

    def test_incidental_refs_pr_numbers_in_title_and_shared_issue(self):
        cur = ("# 当前状态\n\n## 修 #196 引入的问题（#200）（2026-11-08，常浩实点 #196 后说点不动）\n- 已安装：二百\n\n"
               "## #110 1a 收尾（#110，PR #115 `abcdef1`）（2026-11-08）\n- 已安装：一一五\n\n"
               "## 1c（#120）（2026-11-08）\n- 已安装：一二零\n")
        r = make_repo(self.root, "inc", {"CURRENT.md": cur}, at=AT8)
        pr = lambda n, title, body="": {"number": n, "title": title, "url": "", "body": body, "mergedAt": "2026-11-08T03:00:00Z"}
        self.gh(prs=[pr(197, "做（#196）"), pr(201, "修（#200）"), pr(115, "1a（#110）"), pr(116, "1b", "Closes #110"),
                     pr(121, "1c 上（#120）"), pr(122, "1c 下（#120）")])
        prs = {p["number"]: p for p in self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]}
        quotes = {n: [q for c in p["claims"] for q in c["quotes"]] for n, p in prs.items()}
        self.assertEqual(quotes[197], [])                       # #196 只是顺带提到
        self.assertEqual(quotes[201], ["- 已安装：二百"])
        self.assertEqual(quotes[115], ["- 已安装：一一五"])      # 节标题写了 PR #115：只挂给它
        self.assertEqual(quotes[116], [])
        for n in (121, 122):                                    # 同一个 #120 当天两个 PR：照挂，注明分不清
            self.assertEqual(quotes[n], ["- 已安装：一二零"])
            self.assertTrue(any("分不清" in e for e in prs[n]["claims_warnings"]))
        self.assertEqual(prs[115]["claims_warnings"], [])
        self.assertEqual(prs[115]["evidence"], ["PR #115 merged"])

    def test_quotes_are_capped_and_truncated_visibly(self):
        long = "- 已发布" + "长" * 500 + "，但回读不一致，已回滚"
        body = long + "\n" + "".join(f"- 已安装第 {i} 条\n" for i in range(15))
        r = make_repo(self.root, "cap2", {"CURRENT.md": f"# 当前状态\n\n## 长（#130）（2026-11-08）\n{body}"}, at=AT8)
        self.gh(prs=[{"number": 131, "title": "做（#130）", "url": "", "body": "", "mergedAt": "2026-11-08T03:00:00Z"}])
        c = self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"][0]["claims"][0]
        self.assertEqual(len(c["quotes"]), pd.CLAIM_MAX)
        self.assertEqual(c["omitted"], 16 - pd.CLAIM_MAX)                 # 没列出的句数要说出来
        self.assertTrue(c["quotes"][0].endswith("（后文截断）"))            # 截断要看得出来：后半句可能是否定
        self.assertEqual(len(c["quotes"][0]), pd.QUOTE_MAX + len(pd.CUT_MARK))

    def test_named_pr_must_match_section_issue(self):
        cur = ("# 当前状态\n\n## #42 A 级查询（2026-11-08，Claude；常浩回复「合并」（PR #58）后发布）\n- 已发布：四二\n\n"
               "## #41 B 级查询（2026-11-08，Claude）\n- 已发布：四一\n\n"
               "## 修 PR #196 引入的回归（#200）（2026-11-08）\n- 已发布：二百\n\n"
               "## 批次 A 合并（#78、#79）（2026-11-08）\n- 已发布：七八七九\n")
        r = make_repo(self.root, "named", {"CURRENT.md": cur}, at=AT8)
        pr = lambda n, title, body="": {"number": n, "title": title, "url": "", "body": body, "mergedAt": "2026-11-08T03:00:00Z"}
        self.gh(prs=[pr(58, "B 级查询", "Closes #41"), pr(59, "A 级查询（#42）"), pr(196, "旧（#195）"), pr(201, "修（#200）"),
                     pr(80, "甲（#78）"), pr(81, "乙（#79）")])
        prs = {p["number"]: p for p in self.run_one(r)["projects"][0]["yesterday_delta"]["merged_prs"]}
        quotes = {n: [q for c in p["claims"] for q in c["quotes"] if q.startswith("- ")] for n, p in prs.items()}
        self.assertEqual(quotes[59], ["- 已发布：四二"])       # #42 那节点名的 PR #58 是 #41 的：不信点名，按 #42 挂
        self.assertEqual(quotes[58], ["- 已发布：四一"])
        self.assertEqual(quotes[196], [])                      # 「修 PR #196 引入的」是顺带提到
        self.assertEqual(quotes[201], ["- 已发布：二百"])
        for n in (80, 81):                                      # 一节写了两件交付：照挂，注明分不清
            self.assertEqual(quotes[n], ["- 已发布：七八七九"])
            self.assertTrue(any("同时挂给" in w for w in prs[n]["claims_warnings"]))

    def test_title_ranges_and_unmatched_sections(self):
        cur = ("# 当前状态\n\n## 第二批 9 张（#257–#265）（2026-11-08；已发布 `b977cfc5aa5e` 回读通过）\n- 改了九处\n\n"
               "## 写错的范围（#1–#999）（2026-11-08）\n- 已发布：不展开\n\n"
               "## 没写编号的一节（2026-11-08，Claude）\n- 已安装：没编号\n- 改了别的\n")
        r = make_repo(self.root, "rng", {"CURRENT.md": cur}, at=AT8)
        pr = lambda n, title: {"number": n, "title": title, "url": "", "body": "", "mergedAt": "2026-11-08T03:00:00Z"}
        self.gh(prs=[pr(266, "甲（#257）"), pr(267, "乙（#258）"), pr(500, "丙（#500）")])
        delta = self.run_one(r)["projects"][0]["yesterday_delta"]
        prs = {p["number"]: p for p in delta["merged_prs"]}
        for n in (266, 267):                                     # 范围展开：两个 PR 都挂上，并注明分不清
            self.assertEqual(prs[n]["claims"][0]["quotes"], ["第二批 9 张（#257–#265）（2026-11-08；已发布 `b977cfc5aa5e` 回读通过）"])
            self.assertTrue(prs[n]["claims_warnings"])
        self.assertEqual(prs[500]["claims"], [])                 # 「#1–#999」超过上限不展开
        self.assertEqual([u["section"] for u in delta["unmatched_sections"]],
                         ["写错的范围（#1–#999）（2026-11-08）", "没写编号的一节（2026-11-08，Claude）"])
        self.assertEqual(delta["unmatched_sections"][1]["quotes"], ["- 已安装：没编号"])
        self.assertEqual(pd.short("甲" * 41), "甲" * 40 + "…")   # 截短的标题要看得出截了
        (self.state / "fail").write_text("error connecting to api.github.com\n")   # 合并 PR 取不到：当天的节全列为没挂上
        delta = self.run_one(r)["projects"][0]["yesterday_delta"]
        self.assertIsNone(delta["merged_prs"])
        self.assertEqual(len(delta["unmatched_sections"]), 3)

    def test_section_date_prefers_title_start_or_first_bracket(self):
        secs = pd.sections("## 复盘 2026-11-01 事故（2026-11-08，Claude）\n\n## 2026-11-07 · 甲（#1）\n\n## 乙 2026-11-05\n")
        self.assertEqual([d for _, d, _ in secs], ["2026-11-08", "2026-11-07", "2026-11-05"])

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

    def test_gh_that_cannot_start_is_an_error_once(self):
        real, calls = subprocess.run, []

        def fake(argv, *a, **k):
            if argv[0].endswith("gh"):
                calls.append(argv)
                raise PermissionError("gh")
            return real(argv, *a, **k)
        r = make_repo(self.root, "os", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        self.gh()
        with mock.patch.object(pd.subprocess, "run", side_effect=fake):
            rec = self.run_one(r)["projects"][0]
        self.assertEqual(len(calls), 1)
        self.assertTrue(all("gh 启动失败" in e for e in rec["errors"] if e.startswith("GitHub")))
        self.assertIsNone(rec["milestones"])

    def test_bad_github_field_is_an_error_not_a_crash(self):
        r = make_repo(self.root, "bg", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        self.gh()
        rec = self.run_one(r, github="fx")["projects"][0]
        self.assertTrue(any("所有者/仓库" in e for e in rec["errors"]))
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

    def test_checklist_milestone_counts_closed_refs_and_flags_conflicts(self):
        api = {"graphql": {"data": {"repository": {
            "issues": {"nodes": [{"number": 2, "title": "清单父单", "body": "- [x] #10 甲\n- [ ] #11 乙\n- [ ] #12 丙\n",
                                  "subIssues": {"nodes": []}}]},
            "i10": {"state": "OPEN"}, "i11": {"state": "CLOSED"}}}}}    # 假 gh 两次 graphql 回同一份：两种字段都放
        r = make_repo(self.root, "ms", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        self.gh(api=api)
        m = self.run_one(r)["projects"][0]["milestones"][0]
        self.assertEqual((m["done"], m["total"]), (2, 3))       # 勾了的 #10 + 已关的 #11
        self.assertEqual(m["notes"], ["清单勾了但 #10 还开着", "清单没勾但 #11 已关", "#12 的状态没取到"])
        (self.state / "fail_match").write_text("issueOrPullRequest")   # 状态查询失败且没有 data：只按勾选算，并注明
        m = self.run_one(r)["projects"][0]["milestones"][0]
        self.assertEqual((m["done"], m["notes"]), (1, ["清单引用的 issue 状态没取到，只按勾选算"]))

    def test_checklist_states_partial_success_uses_what_was_found(self):
        api = {"graphql": {"data": {"repository": {"issues": {"nodes": [
            {"number": 2, "title": "清单父单", "body": "- [x] #10 甲\n- [ ] #11 乙\n- [ ] #12 丙\n", "subIssues": {"nodes": []}}]}}}}}
        real = subprocess.run

        def fake(argv, *a, **k):   # 状态查询：#12 查不到，gh 退出码 1，但 stdout 带着查到的 #10、#11
            if argv[0].endswith("gh") and any("issueOrPullRequest" in x for x in argv):
                out = {"data": {"repository": {"i10": {"state": "OPEN"}, "i11": {"state": "CLOSED"}, "i12": None}},
                       "errors": [{"message": "Could not resolve to an issue or pull request with the number of 12."}]}
                return subprocess.CompletedProcess(argv, 1, json.dumps(out), "gh: Could not resolve to an issue\n")
            return real(argv, *a, **k)
        r = make_repo(self.root, "mp", {"CURRENT.md": "# 当前状态\n\n## 节\n"})
        self.gh(api=api)
        with mock.patch.object(pd.subprocess, "run", side_effect=fake):
            rec = self.run_one(r)["projects"][0]
        m = rec["milestones"][0]
        self.assertEqual((m["done"], m["total"]), (2, 3))
        self.assertEqual(m["notes"], ["清单勾了但 #10 还开着", "清单没勾但 #11 已关", "#12 的状态没取到"])
        self.assertFalse([e for e in rec["errors"] if e.startswith("GitHub")])

    def test_empty_next_step_and_bad_roster_entry_are_explained(self):
        r = make_repo(self.root, "nn", {"CURRENT.md": "# 当前状态\n\n## 节（2026-11-08）\n- 没写\n"})
        self.gh()
        rec = self.run_one(r)["projects"][0]
        self.assertTrue(any("都没写下一步" in n for n in rec["notes"]))
        out = pd.collect(D, [{"id": "坏", "name": "坏"}, {"id": "空", "name": "空", "path": None}])
        self.assertEqual(out["coverage"]["errors"], {"坏": ["名册这一项缺字段：path"], "空": ["名册这一项缺字段：path"]})
        self.assertEqual(set(out["projects"][0]), set(rec))          # 坏项的记录结构和正常记录一样

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

    def test_only_whole_line_next_step_titles_count(self):
        lines = ("# 当前状态\n\n更新：2026-11-08。唯一下一步：头部的\n\n"
                 "## #38 命令：成果、评审、下一步行动、认定（2026-11-08）\n- 时间：开工\n").splitlines()
        self.assertEqual(progress_data.next_step(lines), "头部的")           # 标题里的「下一步行动」不是下一步
        lines = "# 当前状态\n\n## 最新\n- 没写\n\n## 历史下一步（157快照）\n旧的\n".splitlines()
        self.assertIsNone(progress_data.next_step(lines))
        lines = "# 当前状态\n\n## 最新\n- 没写\n\n## 唯一下一步（11-07）\n老的\n".splitlines()
        self.assertEqual(progress_data.next_step(lines), "老的")

    def test_older_sections_are_not_used(self):
        lines = "# 当前状态\n\n## 最新\n- 没写\n\n## 更早\n- 下一步：更早的\n".splitlines()
        self.assertIsNone(progress_data.next_step(lines))


if __name__ == "__main__":
    unittest.main()
