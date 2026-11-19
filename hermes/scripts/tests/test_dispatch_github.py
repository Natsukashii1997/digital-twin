"""隔离自测：派单接 GitHub（开单建 issue、开 PR、squash 合并、清理）和车间远端护栏。

用临时裸仓库当 GitHub 背后的远端、临时主仓库、临时名册和台账，PATH 最前面放假 gh（fake_gh.py）。
不调用真实 gh、模型或外部服务，不碰真实仓库和 ~/.hermes。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import importlib.util
import io
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


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE.parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dispatch = load("dispatch")
run_worker = load("run_worker")


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class DispatchGitHubTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-dispatch-gh-test-")
        self.addCleanup(tmp.cleanup)
        self.root = root = Path(tmp.name)
        self.state = root / "gh-state"
        fake_gh.install(root / "bin", self.state)
        gitconfig = root / "gitconfig"
        gitconfig.write_text("[init]\n\tdefaultBranch = main\n")
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {
            "PATH": f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(self.state),
            "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        }))
        self.bare = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True)
        (self.state / "remote").write_text(str(self.bare))
        self.repo = root / "repo"
        self.repo.mkdir()
        sh(self.repo, "init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("第一行\n")
        sh(self.repo, "add", "README.md")
        sh(self.repo, "commit", "-q", "-m", "起步")
        sh(self.repo, "remote", "add", "origin", str(self.bare))
        sh(self.repo, "push", "-q", "origin", "main")
        self.roster = root / "roster.json"
        self.write_roster()
        for name, value in {"ROOT": root / "dispatch", "WT_ROOT": root / "worktrees", "ROSTER": self.roster}.items():
            stack.enter_context(patch.object(dispatch, name, value))
        self.task = root / "task.md"
        self.task.write_text("目标：README 加一行说明\n交付物：README.md\n验收：有新行\n边界：只改 README\n交给谁：Claude\n",
                             encoding="utf-8")

    def write_roster(self, **extra):
        self.roster.write_text(json.dumps({"projects": [{
            "id": "fx", "name": "夹具", "path": str(self.repo), "main": "main", "write_allowed": True,
            "github": "o/fx", **extra}]}, ensure_ascii=False))

    def invoke(self, *argv):
        out = io.StringIO()
        with patch.object(sys, "argv", ["dispatch.py", *argv]), contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as stopped:
                dispatch.main()
        result = json.loads(out.getvalue().strip().splitlines()[-1])
        self.assertEqual(stopped.exception.code, 0 if result["ok"] else 1)
        return result

    def gh_calls(self, kind, verb):
        return [c for c in fake_gh.calls(self.state) if c[:2] == [kind, verb]]

    def local_unpushed(self, subject="别的会话没推的提交"):
        (self.repo / "other.md").write_text("别的会话\n")
        sh(self.repo, "add", "other.md")
        sh(self.repo, "commit", "-q", "-m", subject)
        return sh(self.repo, "rev-parse", "HEAD")

    def new_done(self, content="第二行\n"):
        """开写入单，模拟车间在 worktree 里提交并跑完。"""
        o = self.invoke("new", "--project", "fx", "--tool", "claude", "--mode", "write", "--task-file", str(self.task))["order"]
        wt = Path(o["cwd"])
        with open(wt / "README.md", "a") as f:
            f.write(content)
        sh(wt, "commit", "-qam", "README 加一行")
        order = self.root / "dispatch" / o["no"] / "order.json"
        order.write_text(json.dumps({**json.loads(order.read_text()), "status": "done"}))
        (order.parent / "out").mkdir()
        (order.parent / "out/receipt.json").write_text(json.dumps({"summary": "车间总结：README 加了一行"}, ensure_ascii=False))
        return o["no"], wt

    def remote_main(self):
        return sh(self.bare, "rev-parse", "refs/heads/main")

    def test_new_write_order_branches_from_origin_and_opens_issue(self):
        self.local_unpushed()
        r = self.invoke("new", "--project", "fx", "--tool", "claude", "--mode", "write", "--task-file", str(self.task))
        o = r["order"]
        self.assertEqual(o["issue"], 7)
        self.assertIn("issue #7", r["msg"])
        self.assertEqual(o["base"], self.remote_main())
        self.assertFalse((Path(o["cwd"]) / "other.md").exists())  # 本地 main 没推的提交不进本单分支
        (call,) = self.gh_calls("issue", "create")
        self.assertEqual(call[call.index("-t") + 1], f"README 加一行说明（派单 {o['no']}）")
        self.assertEqual(call[call.index("-l") + 1], "ready-for-agent")
        self.assertIn("边界：只改 README", call[call.index("-b") + 1])

    def test_read_order_does_not_touch_github(self):
        o = self.invoke("new", "--project", "fx", "--tool", "codex", "--mode", "read", "--task-file", str(self.task))["order"]
        self.assertNotIn("issue", o)
        self.assertEqual(fake_gh.calls(self.state), [])

    def test_issue_failure_does_not_block_new(self):
        (self.state / "fail").write_text("error connecting to api.github.com\n")
        r = self.invoke("new", "--project", "fx", "--tool", "claude", "--mode", "write", "--task-file", str(self.task))
        self.assertIn("issue 没建成", r["msg"])
        self.assertTrue(Path(r["order"]["cwd"]).is_dir())

    def test_pr_pushes_only_this_order_and_opens_pr(self):
        before = self.remote_main()
        no, wt = self.new_done()
        self.local_unpushed()
        r = self.invoke("pr", no)
        self.assertEqual(r["pr_url"], "https://github.com/o/fx/pull/8")
        self.assertEqual(sh(self.bare, "rev-parse", f"refs/heads/hermes/{no}"), sh(wt, "rev-parse", "HEAD"))
        self.assertEqual(self.remote_main(), before)  # 本地 main 没推的提交没被带上去
        (call,) = self.gh_calls("pr", "create")
        body = call[call.index("-b") + 1]
        self.assertTrue(body.startswith("Closes #7"))
        self.assertIn("车间总结：README 加了一行", body)
        self.assertIn("`README.md`", body)
        self.assertEqual(json.loads((self.root / "dispatch" / no / "order.json").read_text())["pr"], 8)

    def test_pr_stops_on_secret(self):
        no, wt = self.new_done(content="api_key = abcdefghijklmnop123456\n")
        r = self.invoke("pr", no)
        self.assertFalse(r["ok"])
        self.assertIn("密钥", r["msg"])
        self.assertNotIn("abcdefghijklmnop123456", r["msg"])
        self.assertEqual(sh(self.bare, "for-each-ref", "refs/heads/hermes"), "")
        self.assertEqual(self.gh_calls("pr", "create"), [])

    def test_merge_lists_unpushed_commits_then_pushes_after_confirm(self):
        no, wt = self.new_done()
        self.invoke("pr", no)
        tip = self.local_unpushed("别的会话的提交 A")
        r = self.invoke("merge", no)
        self.assertFalse(r["ok"])
        self.assertTrue(r["need_confirm"])
        self.assertIn("别的会话的提交 A", r["msg"])
        self.assertEqual(r["push_main"], tip[:12])
        self.assertEqual(self.gh_calls("pr", "merge"), [])
        self.assertNotEqual(self.remote_main(), tip)

        r = self.invoke("merge", no, "--push-main", r["push_main"])
        self.assertTrue(r["ok"], r["msg"])
        self.assertTrue(r["synced"])
        self.assertEqual(sh(self.bare, "rev-parse", "refs/heads/main~1"), tip)  # 先推了本地 main，squash 接在后面
        self.assertEqual(sh(self.repo, "rev-parse", "main"), self.remote_main())  # 主目录拉齐
        self.assertIn("第二行", (self.repo / "README.md").read_text())

        r = self.invoke("clean", no)
        self.assertTrue(r["ok"], r["msg"])
        self.assertEqual(sh(self.repo, "branch", "--list", f"hermes/{no}"), "")
        self.assertFalse(wt.exists())

    def test_push_main_rejects_stale_confirmation(self):
        no, _ = self.new_done()
        self.invoke("pr", no)
        old = self.local_unpushed("A")[:12]
        (self.repo / "more.md").write_text("又一个\n")
        sh(self.repo, "add", "more.md")
        sh(self.repo, "commit", "-q", "-m", "确认之后又来的提交")
        r = self.invoke("merge", no, "--push-main", old)
        self.assertFalse(r["ok"])
        self.assertIn("确认之后", r["msg"])
        self.assertEqual(self.gh_calls("pr", "merge"), [])

    def test_merge_stops_when_diverged(self):
        no, _ = self.new_done()
        self.invoke("pr", no)
        other = self.root / "other-clone"
        subprocess.run(["git", "clone", "-q", str(self.bare), str(other)], check=True)
        sh(other, "commit", "-q", "--allow-empty", "-m", "远端新提交")
        sh(other, "push", "-q", "origin", "main")
        self.local_unpushed()
        r = self.invoke("merge", no)
        self.assertFalse(r["ok"])
        self.assertIn("分叉", r["msg"])
        self.assertEqual(self.gh_calls("pr", "merge"), [])

    def test_merge_test_failure_does_not_merge(self):
        self.write_roster(test="exit 3")
        no, _ = self.new_done()
        self.invoke("pr", no)
        r = self.invoke("merge", no)
        self.assertFalse(r["ok"])
        self.assertIn("测试没过", r["msg"])
        self.assertEqual(self.gh_calls("pr", "merge"), [])

    def test_merged_on_github_but_local_sync_blocked(self):
        no, _ = self.new_done()
        self.invoke("pr", no)
        (self.repo / "README.md").write_text("主目录没提交的改动\n")
        r = self.invoke("merge", no)
        self.assertTrue(r["ok"], r["msg"])
        self.assertFalse(r["synced"])
        self.assertIn("没拉下来", r["msg"])
        self.assertEqual((self.repo / "README.md").read_text(), "主目录没提交的改动\n")  # 不 stash、不 reset

    def test_clean_refuses_when_pr_not_merged(self):
        no, wt = self.new_done()
        self.invoke("pr", no)
        order = self.root / "dispatch" / no / "order.json"
        order.write_text(json.dumps({**json.loads(order.read_text()), "status": "merged"}))
        r = self.invoke("clean", no)
        self.assertFalse(r["ok"])
        self.assertTrue(wt.exists())
        self.assertNotEqual(sh(self.repo, "branch", "--list", f"hermes/{no}"), "")

    def test_abandon_closes_pr_issue_and_remote_branch(self):
        no, _ = self.new_done()
        self.invoke("pr", no)
        r = self.invoke("clean", no)
        self.assertTrue(r["ok"], r["msg"])
        self.assertIn("abandoned", r["msg"])
        self.assertEqual(len(self.gh_calls("pr", "close")), 1)
        self.assertEqual(len(self.gh_calls("issue", "close")), 1)
        self.assertEqual(sh(self.bare, "for-each-ref", "refs/heads/hermes"), "")


class RemoteGuardTests(unittest.TestCase):
    def snap(self, branch, *refs):
        return {"branch": branch, "remotes": "\n".join(refs)}

    def test_other_remote_refs_only_noted(self):
        before = self.snap("hermes/D1", "refs/remotes/origin/main aaa")
        after = self.snap("hermes/D1", "refs/remotes/origin/main bbb", "refs/remotes/origin/claude/3-x ccc")
        violation, note = run_worker.remote_guard(before, after, "write")
        self.assertFalse(violation)
        self.assertIn("2 个远端引用有变化", note)

    def test_own_branch_pushed_is_violation(self):
        before = self.snap("hermes/D1", "refs/remotes/origin/main aaa")
        after = self.snap("hermes/D1", "refs/remotes/origin/main aaa", "refs/remotes/origin/hermes/D1 ddd")
        self.assertEqual(run_worker.remote_guard(before, after, "write"), (True, None))

    def test_read_mode_never_violates(self):
        before = self.snap("main", "refs/remotes/origin/main aaa")
        after = self.snap("main", "refs/remotes/origin/main bbb")
        violation, note = run_worker.remote_guard(before, after, "read")
        self.assertFalse(violation)
        self.assertIsNotNone(note)


if __name__ == "__main__":
    unittest.main()
