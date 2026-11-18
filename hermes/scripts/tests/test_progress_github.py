"""隔离自测：progress_data.py 的当天 PR 和 issue（F6.3）。

PATH 最前面放假 gh（fake_gh.py）喂固定数据；时区固定北京时间，检查按北京时间算“当天”。不联网。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import fake_gh  # noqa: E402
from test_progress_data import DAY, SCRIPT, repo  # noqa: E402

PRS = [  # DAY 是 2000-01-02；时间是 UTC，北京时间 +8
    {"number": 3, "title": "前一天北京时间开的", "createdAt": "2000-01-01T10:00:00Z", "mergedAt": None, "closedAt": None},
    {"number": 4, "title": "今天开今天合", "createdAt": "2000-01-01T17:00:00Z", "mergedAt": "2000-01-02T03:00:00Z",
     "closedAt": "2000-01-02T03:00:00Z"},
    {"number": 5, "title": "今天没合就关", "createdAt": "2000-01-01T09:00:00Z", "mergedAt": None, "closedAt": "2000-01-02T05:00:00Z"},
    {"number": 6, "title": "北京时间第二天才开", "createdAt": "2000-01-02T17:00:00Z", "mergedAt": None, "closedAt": None},
]
ISSUES = [
    {"number": 7, "title": "今天开的 issue", "createdAt": "2000-01-02T01:00:00Z", "closedAt": None},
    {"number": 2, "title": "今天关的 issue", "createdAt": "1999-12-30T01:00:00Z", "closedAt": "2000-01-02T02:00:00Z"},
]


class ProgressGitHubTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-progress-gh-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "gh-state"
        fake_gh.install(self.root / "bin", self.state)
        (self.root / "dispatch").mkdir()
        self.projects = []

    def add(self, pid, github=None):
        r = repo(self.root, pid, "## 一节\n")
        self.projects.append({"id": pid, "name": f"项目{pid}", "path": str(r), "main": "main",
                              **({"github": github} if github else {})})

    def run_script(self):
        roster = self.root / "projects.json"
        roster.write_text(json.dumps({"projects": self.projects}, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "DT_ROSTER": str(roster), "DT_DISPATCH": str(self.root / "dispatch"), "TZ": "Asia/Shanghai",
               "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(self.state)}
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--date", DAY], capture_output=True, text=True, env=env)

    def test_todays_prs_and_issues_by_beijing_day(self):
        (self.state / "pr_list.json").write_text(json.dumps(PRS, ensure_ascii=False))
        (self.state / "issue_list.json").write_text(json.dumps(ISSUES, ensure_ascii=False))
        self.add("a", "o/a")  # 本地没动静，只有 GitHub 上有：也要列出来
        out = self.run_script().stdout
        self.assertNotIn("今天没动静", out)
        self.assertIn("PR 新开：#4 今天开今天合", out)
        self.assertIn("PR 合并：#4 今天开今天合", out)
        self.assertIn("PR 关闭（未合并）：#5 今天没合就关", out)
        self.assertIn("issue 新开：#7 今天开的 issue", out)
        self.assertIn("issue 关闭：#2 今天关的 issue", out)
        self.assertNotIn("#3", out)
        self.assertNotIn("#6", out)
        self.assertIn("与 GitHub", out)
        (call,) = [c for c in fake_gh.calls(self.state) if c[:2] == ["pr", "list"]]
        self.assertEqual(call[call.index("-R") + 1], "o/a")
        self.assertIn("updated:>=2000-01-01", call)

    def test_github_down_marks_only_that_project(self):
        (self.state / "fail").write_text("error connecting to api.github.com\n")
        self.add("a", "o/a")
        sh_repo = Path(self.projects[0]["path"])
        subprocess.run(["git", "-C", str(sh_repo), "commit", "-q", "--allow-empty", "-m", "今天"], check=True,
                       env={**os.environ, "GIT_AUTHOR_DATE": f"{DAY} 10:00", "GIT_COMMITTER_DATE": f"{DAY} 10:00",
                            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                            "GIT_COMMITTER_EMAIL": "t@t"})
        self.add("b", "o/b")  # 本地没动静的项目：并进“没动静”一行，带上取不到的标记
        self.add("c")  # 没有 github 的项目不调 gh
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertIn("今天提交：1 个", r.stdout)
        self.assertIn("GitHub 取不到：error connecting to api.github.com", r.stdout)
        self.assertIn("项目b（GitHub 取不到）", r.stdout)
        self.assertEqual(len([c for c in fake_gh.calls(self.state) if c[1] == "list"]), 1)  # 连不上一次后不再等


if __name__ == "__main__":
    unittest.main()
