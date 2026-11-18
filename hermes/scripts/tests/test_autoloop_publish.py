"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

hermes/scripts/autoloop_publish.py（受信发布最小版，#205 批 0C）：交接包由真实的工人侧启动脚本（ops/dt_worktree.py）
在临时目录里生成，凭证由真实的 0B-1 网关签；「GitHub」是临时目录里的本地裸仓库，gh 是假的（fake_gh.py）。
不联网、不碰 ~/Library/Application Support/DigitalTwin、不碰真实仓库。

验收 3（方案第 9 节 0C）：只能推非 main 分支——推送目标写死成本单 agent/ 分支，main 在每项测试后都不变。
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_grants as G  # noqa: E402
import autoloop_publish as P  # noqa: E402
import fake_gh  # noqa: E402
from test_dt_worktree import SCRIPT, WorkerBase, git  # noqa: E402


class PublishBase(WorkerBase):
    def setUp(self):
        super().setUp()
        r = self.root
        self.gh_state = r / "gh"
        exe = fake_gh.install(r / "ghbin", self.gh_state)
        p = mock.patch.dict(os.environ, {"DT_PUBLISH_OUTBOX": str(self.outbox), "DT_PUBLISH_MIRRORS": str(r / "mirrors"),
                                         "DT_PUBLISH_REMOTE": f"file://{r}/remote/{{github}}.git",
                                         "DT_PUBLISH_LOG": str(r / "publish.jsonl"), "DT_GH": str(exe),
                                         "FAKE_GH_STATE": str(self.gh_state), **{k: v for k, v in
                                                                                  self.wenv.items() if k.startswith("GIT_")}})
        p.start()
        self.addCleanup(p.stop)
        self.main_before = git("rev-parse", "main", cwd=self.remote)

    def tearDown(self):
        self.assertEqual(git("rev-parse", "main", cwd=self.remote), self.main_before, "main 被动过")

    def remote_branch(self, tid, executor="claude"):
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/agent/{executor}/{tid}"],
                           cwd=self.remote, capture_output=True, text=True)
        return r.stdout.strip() or None

    def gh_calls(self):
        return fake_gh.calls(self.gh_state)

    def worked(self, mode="edit"):
        tid = self.new_live_task()
        r, _ = self.run_turn([self.dispatch_event(tid)], FAKE_MODE=mode)
        self.assertEqual(r.returncode, 0, r.stderr)
        return tid

    def rejects(self, tid, words):
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn(words, str(c.exception))
        self.assertIsNone(self.remote_branch(tid))

    def rewrite_manifest(self, tid, **fields):
        p = self.outbox / tid / "handoff.json"
        m = json.loads(p.read_text())
        m.update(fields)
        p.write_text(json.dumps(m))


class Publish(PublishBase):
    def test_pushes_only_the_task_branch_and_opens_pr(self):
        tid = self.worked()
        out = P.publish(tid)
        head = self.handoff(tid)["head"]
        self.assertEqual(self.remote_branch(tid), head)
        self.assertEqual((out["pushed"], out["pr_opened"], out["branch"]), (True, True, f"agent/claude/{tid}"))
        create = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        (c,) = create
        self.assertEqual((P_opt(c, "-H"), P_opt(c, "-B"), P_opt(c, "-R")), (f"agent/claude/{tid}", "main", "o/dt-sandbox"))
        self.assertTrue(P_opt(c, "-t").startswith(f"{tid}："))
        refs = git("for-each-ref", "--format=%(refname)", "refs/heads", cwd=self.remote).split()
        self.assertEqual(sorted(refs), sorted(["refs/heads/main", f"refs/heads/agent/claude/{tid}"]))
        log = [json.loads(x) for x in (self.root / "publish.jsonl").read_text().splitlines()]
        self.assertEqual(log[-1]["what"], "发布")

    def test_second_publish_is_idempotent_and_resume_fast_forwards(self):
        tid = self.worked()
        P.publish(tid)
        (self.gh_state / "pr_list.json").write_text(json.dumps([{"number": 8, "url": "u8", "state": "OPEN"}]))
        out = P.publish(tid)
        self.assertEqual((out["pushed"], out["pr_opened"], out["pr"]), (False, False, "u8"))
        self.run_turn([self.answer_event(tid)])
        out = P.publish(tid)
        self.assertTrue(out["pushed"])
        self.assertEqual(self.remote_branch(tid), self.handoff(tid)["head"])

    def test_closed_pr_is_not_reopened(self):
        tid = self.worked()
        (self.gh_state / "pr_list.json").write_text(json.dumps([{"number": 8, "url": "u8", "state": "CLOSED"}]))
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn("已关闭或已合并", str(c.exception))

    def test_branch_names_agree_with_worker(self):
        spec = importlib.util.spec_from_file_location("dt_worktree", SCRIPT)
        w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(w)
        for ex in G.EXECUTORS:
            self.assertEqual(w.branch_name(ex, "t-20261118-abcdef"), P.branch_name(ex, "t-20261118-abcdef"))


class OnlyNonMain(PublishBase):
    """验收 3：只能推非 main，只推本单分支。"""

    def test_manifest_naming_main_or_other_branch_is_refused(self):
        tid = self.worked()
        for branch in ("main", "agent/claude/t-20260101-abcdef", f"claude/{tid}", f"agent/codex/{tid}"):
            self.rewrite_manifest(tid, branch=branch)
            self.rejects(tid, "交接清单的内容不合格式")

    def test_bundle_with_extra_refs_is_refused(self):
        tid = self.worked()
        wt = self.wt(tid)
        m = self.handoff(tid)
        bundle = self.outbox / tid / m["bundle"]
        git("update-ref", "refs/heads/main", m["head"], cwd=wt)                  # 工人把 main 也指到自己的提交
        git("bundle", "create", "-q", str(bundle), f"refs/heads/agent/claude/{tid}", "refs/heads/main", f"^{m['base']}",
            cwd=wt)
        self.rewrite_manifest(tid, bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.rejects(tid, "不只有本单分支")

    def test_head_or_hash_mismatch_is_refused(self):
        tid = self.worked()
        self.rewrite_manifest(tid, bundle_sha256="0" * 64)
        self.rejects(tid, "哈希对不上")
        tid2 = self.worked()
        self.rewrite_manifest(tid2, head=self.handoff(tid2)["base"])
        self.rejects(tid2, "头提交对不上")

    def test_base_not_on_main_is_refused(self):
        tid = self.worked()
        head = self.handoff(tid)["head"]
        self.rewrite_manifest(tid, base=head)                                     # 基线写成自己的提交
        self.rejects(tid, "基线不是 main 上的提交")

    def test_symlinked_manifest_is_refused(self):
        tid = self.worked()
        p = self.outbox / tid / "handoff.json"
        real = self.root / "elsewhere.json"
        real.write_text(p.read_text())
        p.unlink()
        p.symlink_to(real)
        self.rejects(tid, "读不到交接清单")

    def test_existing_remote_branch_is_never_overwritten(self):
        tid = self.worked()
        seed = self.root / "seed"
        git("checkout", "-q", "-b", "x", cwd=seed)
        (seed / "foreign.txt").write_text("x")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "别人的提交", cwd=seed)
        git("push", "-q", str(self.remote), f"x:refs/heads/agent/claude/{tid}", cwd=seed)
        foreign = git("rev-parse", "HEAD", cwd=seed)
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn("不覆盖", str(c.exception))
        self.assertEqual(self.remote_branch(tid), foreign)

    def test_assert_not_main(self):
        for bad in ("main", "agent/claude/main", "claude/t-20261118-abcdef", "agent/../main"):
            with self.assertRaises(P.Rejected):
                P.assert_not_main(bad)
        P.assert_not_main("agent/claude/t-20261118-abcdef")

    def test_sensitive_paths_are_handed_to_owner(self):
        tid = self.worked(mode="workflow")
        self.rejects(tid, "高风险路径（.github/workflows/ci.yml）")


class GrantAtPublish(PublishBase):
    def test_revoked_or_frozen_after_handoff_is_not_published(self):
        tid = self.worked()
        G.revoke(tid, now=self.now)
        self.rejects(tid, "凭证已撤销")
        tid = self.worked()
        G.freeze(tid, "推翻", now=self.now)
        self.rejects(tid, "凭证已冻结")

    def test_old_generation_handoff_is_not_published(self):
        tid = self.worked()
        code = G.freeze(tid, "推翻", now=self.now)["freeze_code"]
        self.unfreeze_ok(tid, code, when=self.now)
        self.rejects(tid, "第 1 代已作废")

    def test_manifest_cannot_choose_repo_or_executor(self):
        tid = self.worked()
        self.rewrite_manifest(tid, executor="codex", branch=f"agent/codex/{tid}")
        self.rejects(tid, "仓库或执行方和凭证不一致")

    def test_grant_without_push_or_pr_action(self):
        tid = self.worked()
        self.sql("UPDATE grants SET actions = ? WHERE task_id = ?", (json.dumps(["edit"]), tid))
        self.rejects(tid, "凭证不允许 push_branch")

    def test_push_allowed_but_not_open_pr(self):
        tid = self.worked()
        self.sql("UPDATE grants SET actions = ? WHERE task_id = ?", (json.dumps(["edit", "push_branch"]), tid))
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn("凭证不允许 open_pr", str(c.exception))
        self.assertEqual(self.remote_branch(tid), self.handoff(tid)["head"])      # 分支推了，PR 没开
        self.assertEqual([x for x in self.gh_calls() if x[:2] == ["pr", "create"]], [])

    def test_refusals_are_logged_by_cli(self):
        tid = self.worked()
        G.revoke(tid, now=self.now)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(P.main(["publish", "--task", tid]), 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])
        log = [json.loads(x) for x in (self.root / "publish.jsonl").read_text().splitlines()]
        self.assertEqual((log[-1]["what"], log[-1]["task_id"]), ("拒发", tid))

    def test_scan_is_read_only(self):
        tid = self.worked()
        (item,) = P.scan()
        self.assertEqual((item["task_id"], item["check"]), (tid, "可以发"))
        self.assertIsNone(self.remote_branch(tid))
        self.assertEqual(self.gh_calls(), [])


def P_opt(args, name):
    return args[args.index(name) + 1] if name in args else None


if __name__ == "__main__":
    unittest.main()
