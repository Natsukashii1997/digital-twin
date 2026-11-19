"""隔离自测：ops/check_repos.py（名册活跃项目的 GitHub 设置和派单前提自检）。

每个项目一个临时裸仓库当远端、一个临时主目录；PATH 最前面放假 gh（fake_gh.py），远端设置从 api.json 回话。
git 用临时全局配置。不联网，不碰真实仓库。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import fake_gh  # noqa: E402

SCRIPT = HERE.parents[2] / "ops/check_repos.py"
LABELS = ["needs-triage", "needs-info", "ready-for-agent", "ready-for-human", "wontfix", "bug"]


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class CheckReposTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-check-repos-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "gh-state"
        fake_gh.install(self.root / "bin", self.state)
        gitconfig = self.root / "gitconfig"
        gitconfig.write_text("[init]\n\tdefaultBranch = main\n")
        self.vault = self.root / "vault"
        (self.vault / "10 项目").mkdir(parents=True)
        self.env = {**os.environ, "PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}",
                    "FAKE_GH_STATE": str(self.state), "DT_VAULT": str(self.vault), "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        p = patch.dict(os.environ, self.env)
        p.start()
        self.addCleanup(p.stop)
        self.projects, self.api = [], {}
        self.add("a")
        self.add("b")

    def add(self, pid):
        """一个全部合规的项目：私有、默认 main、只 squash、标签齐、有 PR 模板和工程流程指针，主目录在 main 且和远端一致，有有效的项目卡。"""
        gh = f"o/{pid}"
        bare, repo = self.root / f"{pid}.git", self.root / pid
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        repo.mkdir()
        sh(repo, "init", "-q", "-b", "main")
        sh(repo, "commit", "-q", "--allow-empty", "-m", "起步")
        sh(repo, "remote", "add", "origin", str(bare))
        sh(repo, "push", "-q", "origin", "main")
        self.api[f"repos/{gh}"] = {"private": True, "default_branch": "main", "allow_squash_merge": True,
                                   "allow_merge_commit": False, "allow_rebase_merge": False,
                                   "delete_branch_on_merge": True, "has_issues": True}
        self.api[f"repos/{gh}/labels?per_page=100"] = [{"name": n} for n in LABELS]
        self.api[f"repos/{gh}/contents/.github/pull_request_template.md?ref=main"] = "## 改了什么\n"
        self.api[f"repos/{gh}/contents/AGENTS.md?ref=main"] = "改动怎么走见《工程流程》\n"
        (self.vault / f"10 项目/{pid}.md").write_text(f"---\ntype: 项目\nstatus: 有效\n---\n# {pid}\n", encoding="utf-8")
        self.projects.append({"id": pid, "name": f"项目{pid}", "path": str(repo), "main": "main",
                              "agents": "AGENTS.md", "card": f"10 项目/{pid}.md", "test": "true", "github": gh})

    def run_script(self):
        roster = self.root / "projects.json"
        roster.write_text(json.dumps({"projects": self.projects + [{"id": "c", "path": "/没有", "main": "main"}]},
                                     ensure_ascii=False), encoding="utf-8")
        (self.state / "api.json").write_text(json.dumps(self.api, ensure_ascii=False))
        r = subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True, text=True,
                           env={**self.env, "DT_ROSTER": str(roster)})
        return r, json.loads(r.stdout.strip().splitlines()[-1])

    def test_all_pass(self):
        before = sh(self.root / "a", "for-each-ref")
        r, res = self.run_script()
        self.assertEqual((r.returncode, res), (0, {"ok": True, "failed": [], "warnings": []}), r.stdout)
        self.assertIn("【a】", r.stdout)
        self.assertNotIn("【c】", r.stdout)  # 没有 github 的项目不查
        self.assertNotIn("不通过", r.stdout)
        self.assertEqual(sh(self.root / "a", "for-each-ref"), before)  # 只读：不 fetch，引用不变

    def test_remote_settings_wrong(self):
        self.api["repos/o/a"].update(private=False, default_branch="master", allow_rebase_merge=True,
                                     delete_branch_on_merge=False, has_issues=False)
        self.api["repos/o/a/labels?per_page=100"] = [{"name": "needs-triage"}]
        del self.api["repos/o/a/contents/.github/pull_request_template.md?ref=main"]
        self.api["repos/o/a/contents/AGENTS.md?ref=main"] = "没有指针\n"
        self.projects[0]["test"] = None
        r, res = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertFalse(res["ok"])
        failed = "\n".join(res["failed"])
        for word in ("不是私有", "默认分支是 master", "rebase没关", "不会自动删分支", "Issues 没打开",
                     "缺分流标签：needs-info", "没有 .github/pull_request_template.md", "没有「工程流程」"):
            self.assertIn(word, failed)
        self.assertTrue(all(f.startswith("a：") for f in res["failed"]))
        self.assertEqual(res["warnings"], ["a：名册没写 test，合并前不跑测试"])

    def test_main_dir_not_on_main(self):
        sh(self.root / "a", "switch", "-q", "-c", "claude/v1")
        r, res = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(res["failed"], ["a：主目录检出的是 claude/v1，不是 main"])
        self.assertEqual(sh(self.root / "a", "branch", "--show-current"), "claude/v1")  # 不切分支

    def test_local_main_ahead_and_behind(self):
        sh(self.root / "a", "commit", "-q", "--allow-empty", "-m", "没推")
        other = self.root / "other"
        subprocess.run(["git", "clone", "-q", str(self.root / "b.git"), str(other)], check=True)
        sh(other, "commit", "-q", "--allow-empty", "-m", "别处推的")
        sh(other, "push", "-q", "origin", "main")
        r, res = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(res["failed"], ["a：本地 main 比远端多 1 个提交没推"])
        self.assertEqual(len(res["warnings"]), 1)
        self.assertTrue(res["warnings"][0].startswith("b：本地落后，需拉取"))

    def test_project_card(self):
        del self.projects[0]["card"]
        (self.vault / "10 项目/b.md").write_text("---\nstatus: 草稿\n---\n正文里的 status: 有效 不算\n", encoding="utf-8")
        self.add("d")
        (self.vault / "10 项目/d.md").unlink()
        r, res = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(res["failed"], ["a：名册没写项目卡（card）", "d：项目卡不存在：10 项目/d.md"])
        self.assertEqual(res["warnings"], ["b：项目卡 10 项目/b.md 状态是 草稿，不是有效"])

    def test_gh_failure_only_marks_that_project(self):
        (self.state / "fail_match").write_text("o/a")
        r, res = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(len(res["failed"]), 1)
        self.assertIn("a：取不到远端仓库信息", res["failed"][0])
        self.assertIn("【b】", r.stdout)
        # 仓库信息取不到就不再逐项调 gh
        self.assertEqual([c for c in fake_gh.calls(self.state) if any("o/a" in x for x in c)], [["api", "repos/o/a"]])


if __name__ == "__main__":
    unittest.main()
