"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

用临时 git 仓库、临时名册和临时台账跑 progress_data.py，不碰真实仓库和 ~/.hermes。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "progress_data.py"
DAY = "2000-01-02"


def sh(cwd, *args, date=f"{DAY} 10:00"):
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)


def repo(root, name, current=None):
    r = root / name
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    if current is not None:
        (r / "CURRENT.md").write_text(current, encoding="utf-8")
    sh(r, "commit", "-q", "--allow-empty", "-m", "昨天的提交", date="2000-01-01 10:00")
    return r


class ProgressDataTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-progress-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.dispatch = self.root / "dispatch"
        self.dispatch.mkdir()
        self.projects = []

    def add(self, pid, path, aliases=()):
        self.projects.append({"id": pid, "name": f"项目{pid}", "aliases": list(aliases), "path": str(path), "main": "main"})

    def run_script(self, *args):
        roster = self.root / "projects.json"
        roster.write_text(json.dumps({"projects": self.projects}, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "DT_ROSTER": str(roster), "DT_DISPATCH": str(self.dispatch)}
        return subprocess.run([sys.executable, "-B", str(SCRIPT), "--date", DAY, *args],
                              capture_output=True, text=True, env=env)

    def test_commits_grouped_by_branch_main_first_and_other_days_excluded(self):
        r = repo(self.root, "a", "## 一节\n- 下一步：继续\n")
        sh(r, "commit", "-q", "--allow-empty", "-m", "主分支今天")
        sh(r, "switch", "-q", "-c", "hermes/D1")
        sh(r, "commit", "-q", "--allow-empty", "-m", "派单分支今天")
        sh(r, "switch", "-q", "main")
        self.add("a", r)
        out = self.run_script().stdout
        self.assertIn("今天提交：2 个", out)
        self.assertLess(out.index("分支 `main`"), out.index("分支 `hermes/D1`"))
        self.assertEqual(out.count("主分支今天"), 2)  # 一次在提交列表，一次在“主分支停在哪”
        self.assertEqual(out.count("派单分支今天"), 1)  # 只记在派单分支下，不重复
        self.assertNotIn("昨天的提交", out)

    def test_next_step_formats(self):
        cases = {
            "list": ("# 状态\n本文件只记唯一的下一步。\n\n## 最新\n- 遗留 / 下一步：① 做甲\n", "① 做甲"),
            "table": ("# 状态\n## 协作状态\n| 项 | 值 |\n|---|---|\n| 唯一下一步 | 做乙 |\n", "做乙"),
            "heading": ("# 状态\n## 交接\n见下文“唯一下一步”。\n\n## 唯一下一步\n\n做丙\n", "做丙"),
        }
        for pid, (text, want) in cases.items():
            r = repo(self.root, pid, text)
            sh(r, "commit", "-q", "--allow-empty", "-m", "今天")
            self.add(pid, r)
        out = self.run_script().stdout
        for _, want in cases.values():
            self.assertIn(f"CURRENT 下一步：{want}", out)
        self.assertNotIn("本文件只记", out)
        self.assertNotIn("见下文", out)

    def test_quiet_projects_and_orders(self):
        quiet = repo(self.root, "q", "## 一节\n")
        os.utime(quiet / "CURRENT.md", (946_600_000, 946_600_000))  # 改到 2000-01-01 前后，不算今天改过
        busy = repo(self.root, "b")
        self.add("q", quiet)
        self.add("b", busy)
        (self.dispatch / "D1").mkdir()
        (self.dispatch / "D1/order.json").write_text(json.dumps(
            {"no": "D1", "project": "b", "tool": "codex", "mode": "read", "status": "done", "created": f"{DAY}T09:00:00"}))
        (self.dispatch / "D0").mkdir()
        (self.dispatch / "D0/order.json").write_text(json.dumps(
            {"no": "D0", "project": "b", "tool": "claude", "mode": "write", "status": "cleaned", "created": "2000-01-01T09:00:00"}))
        out = self.run_script().stdout
        self.assertIn("今天没动静", out)
        self.assertIn("项目q：没有提交", out)
        self.assertIn("派单 D1：codex · 只读 · done", out)
        self.assertNotIn("D0", out)
        self.assertIn("无 CURRENT", out)

    def test_broken_repo_only_marks_that_project(self):
        good = repo(self.root, "g", "## 一节\n")
        sh(good, "commit", "-q", "--allow-empty", "-m", "今天")
        self.add("x", self.root / "不存在")
        self.add("g", good)
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertIn("读不了这个仓库", r.stdout)
        self.assertIn("今天提交：1 个", r.stdout)

    def test_project_filter_by_alias(self):
        for pid in ("a", "b"):
            r = repo(self.root, pid)
            sh(r, "commit", "-q", "--allow-empty", "-m", f"今天{pid}")
            self.add(pid, r, aliases=[f"别名{pid}"])
        out = self.run_script("--project", "别名b").stdout
        self.assertIn("今天b", out)
        self.assertNotIn("今天a", out)
        miss = self.run_script("--project", "没有这个")
        self.assertEqual(miss.returncode, 1)
        self.assertIn("取数失败", miss.stdout)

    def test_bad_roster_fails(self):
        roster = self.root / "projects.json"
        roster.write_text("不是 JSON", encoding="utf-8")
        env = {**os.environ, "DT_ROSTER": str(roster), "DT_DISPATCH": str(self.dispatch)}
        r = subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("取数失败", r.stdout)


if __name__ == "__main__":
    unittest.main()
