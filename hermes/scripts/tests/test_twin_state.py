"""隔离自测：持久任务状态与事件底座（twin_state.py），覆盖任务书 B1.5 的十项验收和 B1.3 的必处理情形。

每项测试用独立临时目录里的状态库、固定时钟和虚构的派单台账；不碰 ~/.hermes、不碰真实工单、不调用模型或外部服务。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_state.py -v
"""
import contextlib
import datetime as dt
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
MODULE = HERE.parent / "twin_state.py"
SPEC = importlib.util.spec_from_file_location("twin_state", MODULE)
ts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ts)

TZ = dt.timezone(dt.timedelta(hours=8))
T0 = dt.datetime(2000, 1, 1, 9, 0, tzinfo=TZ)
OK_RECEIPT = {"exit_code": 0, "timed_out": False, "worker_error": False, "session_id": "sess-1",
              "branch": "hermes/D20000101-1", "head_before": "aaa", "head_after": "bbb",
              "committed_files": ["hermes/scripts/x.py"], "uncommitted": [], "guard_main_changed": False,
              "guard_remote_changed": False, "need_decision": None, "summary": "改好了。"}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-state-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "state" / "twin-state.db"
        self.t = T0
        self.orders = 0
        self.store = ts.Store(self.db, clock=lambda: self.t).open()
        self.addCleanup(self.store.close)

    def tick(self, **delta):
        self.t += dt.timedelta(**delta)

    def goal(self, authorized=True, **kw):
        if authorized:
            return self.store.create_goal("把日报并跑验收做完", by="user", status="authorized", **kw)
        return self.store.create_goal("把日报并跑验收做完", by="hermes", **kw)

    def ready_task(self, goal_id=None, title="补台账状态机", **kw):
        goal_id = goal_id or self.goal()["id"]
        t = self.store.create_task(goal_id, title, by="hermes", acceptance="十项测试通过", **kw)
        self.store.set_ready(t["id"], by="hermes")
        return self.store.get_task(t["id"])

    def running_task(self, **kw):
        t = self.ready_task(**kw)
        self.orders += 1
        r = self.store.start_run(t["id"], "claude", by="hermes", order_no=f"D20000101-{self.orders}")["run"]
        return self.store.get_task(t["id"]), r

    def kinds(self, obj_id):
        return [e["kind"] for e in self.store.events(object_id=obj_id)]


class AcceptanceTests(Base):
    """任务书 B1.5 的十项验收，按编号。"""

    def test_01_create_goal_and_task(self):
        g = self.store.create_goal("目标甲", by="hermes", description="说明", scope="只改仓库", project="digital-twin",
                                   phase="B1", done_when="十项测试通过")
        self.assertEqual((g["id"], g["status"], g["project"], g["done_when"]), ("G20000101-1", "observed", "digital-twin", "十项测试通过"))
        t = self.store.create_task(g["id"], "任务一", by="hermes", acceptance="能跑", next_step="先写 schema", owner="claude")
        t2 = self.store.create_task(g["id"], "任务二", by="user", depends_on=[t["id"]])
        self.assertEqual((t["id"], t["goal_id"], t["status"], t["owner"]), ("T20000101-1", g["id"], "observed", "claude"))
        self.assertEqual(t2["depends_on"], [t["id"]])
        self.assertEqual([x["id"] for x in self.store.list_tasks(g["id"])], [t["id"], t2["id"]])
        self.assertEqual(self.kinds(g["id"]), ["goal.create"])
        self.assertEqual([e["source"] for e in self.store.events(object_id=t2["id"])], ["user"])
        self.assertEqual(self.store.show(t["id"])["title"], "任务一")
        with self.assertRaises(ts.NotFound):
            self.store.create_task("G20000101-9", "没有的目标", by="hermes")
        with self.assertRaises(ts.NotFound):
            self.store.create_task(g["id"], "依赖不存在", by="hermes", depends_on=["T20000101-9"])
        # 目标一天内编号递增，换一天重新计数
        self.assertEqual(self.store.create_goal("目标乙", by="hermes")["id"], "G20000101-2")
        self.tick(days=1)
        self.assertEqual(self.store.create_goal("目标丙", by="hermes")["id"], "G20000102-1")

    def test_02_unauthorized_task_cannot_run(self):
        g = self.goal(authorized=False)
        t = self.store.create_task(g["id"], "任务", by="hermes")
        with self.assertRaisesRegex(ts.NotAuthorized, "未经常浩授权"):
            self.store.set_ready(t["id"], by="hermes")
        with self.assertRaisesRegex(ts.IllegalTransition, "只有 ready 的任务能开始执行"):
            self.store.start_run(t["id"], "claude", by="hermes")
        with self.assertRaisesRegex(ts.NotAuthorized, "只有常浩本人能授权"):
            self.store.authorize_goal(g["id"], by="hermes")
        with self.assertRaises(ts.NotAuthorized):
            self.store.create_goal("直接授权", by="claude", status="authorized")
        self.assertEqual(self.store.get_task(t["id"])["status"], "observed")
        self.assertEqual(self.store.list_runs(), [])
        # 常浩授权后才能推进；撤销后又不能
        self.store.authorize_goal(g["id"], by="user")
        self.assertEqual(self.store.set_ready(t["id"], by="hermes")["status"], "ready")
        self.store.cancel_goal(g["id"], by="user", reason="不做了")
        self.assertEqual(self.store.get_task(t["id"])["status"], "canceled")
        with self.assertRaises(ts.IllegalTransition):
            self.store.start_run(t["id"], "claude", by="hermes")
        t3 = self.store.create_task(self.goal()["id"], "另一个目标的任务", by="hermes")
        self.store.set_ready(t3["id"], by="hermes")
        self.store.cancel_goal(t3["goal_id"], by="user")
        with self.assertRaises(ts.IllegalTransition):  # 目标撤销时任务已一起撤销
            self.store.start_run(t3["id"], "claude", by="hermes")

    def test_03_task_links_multiple_runs(self):
        t, r1 = self.running_task()
        with self.assertRaisesRegex(ts.IllegalTransition, "已有进行中的 Run"):
            self.store.start_run(t["id"], "codex", by="hermes")
        self.store.receive_receipt(r1["id"], {**OK_RECEIPT, "exit_code": 1, "worker_error": True})
        self.assertEqual(self.store.get_task(t["id"])["status"], "blocked")
        self.assertEqual(self.store.retry_task(t["id"], by="hermes")["retries"], 1)
        r2 = self.store.start_run(t["id"], "codex", by="hermes", order_no="D20000101-2")["run"]
        self.store.receive_receipt(r2["id"], OK_RECEIPT)
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        # 既有工单（未关联）也能挂到同一任务上
        self.store.sync_orders(make_dispatch(self.root / "dispatch", {"D20000101-3": order("done")}))
        self.assertEqual(self.store.link_run("D20000101-3", t["id"], by="hermes")["applied"], True)
        runs = self.store.list_runs(t["id"])
        self.assertEqual([r["order_no"] for r in runs], ["D20000101-1", "D20000101-2", "D20000101-3"])
        self.assertEqual([r["status"] for r in runs], ["failed", "done", "done"])
        self.assertEqual([r["agent"] for r in runs], ["claude", "codex", "claude"])
        self.assertEqual(self.store.get_run("D20000101-2")["session_id"], "sess-1")
        with self.assertRaisesRegex(ts.StateError, "已关联任务"):
            self.store.link_run("D20000101-3", self.ready_task(title="别的任务")["id"], by="hermes")

    def test_04_run_success_does_not_close_goal(self):
        t, r = self.running_task()
        out = self.store.receive_receipt(r["id"], OK_RECEIPT)
        self.assertEqual(out["result"]["task_status"], "review")
        self.assertEqual(self.store.get_run(r["id"])["status"], "done")
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.assertEqual(self.store.get_goal(t["goal_id"])["status"], "authorized")
        # 台账后续 merged / cleaned 也只改 Run，不动任务和目标
        self.store.update_run(r["id"], "merged", by="hermes", evidence={"merged_to": "abc1234"})
        self.store.update_run(r["id"], "cleaned", by="hermes")
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.assertEqual(self.store.get_run(r["id"])["evidence"]["merged_to"], "abc1234")
        self.assertEqual(self.store.complete_task(t["id"], by="hermes")["status"], "completed")
        self.assertEqual(self.store.get_goal(t["goal_id"])["status"], "authorized")
        with self.assertRaisesRegex(ts.NotAuthorized, "只有常浩本人能把目标标为完成"):
            self.store.complete_goal(t["goal_id"], by="hermes")
        t2 = self.store.create_task(t["goal_id"], "还开着的任务", by="hermes")
        with self.assertRaisesRegex(ts.IllegalTransition, "还有没收尾的任务"):
            self.store.complete_goal(t["goal_id"], by="user")
        self.store.cancel_task(t2["id"], by="hermes")
        self.assertEqual(self.store.complete_goal(t["goal_id"], by="user")["status"], "completed")
        with self.assertRaises(ts.IllegalTransition):
            self.store.create_task(t["goal_id"], "完成后不能再加", by="hermes")

    def test_05_duplicate_event_is_not_applied_twice(self):
        t = self.ready_task()
        key = "feishu:om_123:派单"
        first = self.store.start_run(t["id"], "claude", by="hermes", order_no="D20000101-1", idem_key=key)
        again = self.store.start_run(t["id"], "claude", by="hermes", order_no="D20000101-1", idem_key=key)
        self.assertTrue(first["applied"])
        self.assertEqual((again["applied"], again["run"]["id"]), (False, first["run"]["id"]))
        self.assertEqual(len(self.store.list_runs(t["id"])), 1)
        self.assertEqual(self.store.events(kind="run.start")[0]["dup_count"], 1)
        rid = first["run"]["id"]
        self.assertTrue(self.store.receive_receipt(rid, OK_RECEIPT)["applied"])
        for _ in range(3):
            self.assertFalse(self.store.receive_receipt(rid, OK_RECEIPT)["applied"])
        self.assertEqual(self.kinds(rid).count("run.done"), 1)
        self.assertEqual(self.kinds(t["id"]).count("task.review"), 1)
        self.assertEqual(self.store.events(kind="run.finish")[0]["dup_count"], 3)
        # 回执变了但 Run 已到终态：拒绝，不改状态
        with self.assertRaisesRegex(ts.IllegalTransition, "已是 done"):
            self.store.receive_receipt(rid, {**OK_RECEIPT, "exit_code": 1})
        self.assertEqual(self.store.get_run(rid)["status"], "done")
        # 通用入口：同一个幂等键的 handler 只跑一次
        calls = []
        for _ in range(2):
            self.store.ingest_event("user", "message", "feishu:om_456", "user", {"text": "可以"},
                                    handler=lambda s: calls.append(1))
        self.assertEqual(calls, [1])
        with self.assertRaisesRegex(ts.StateError, "必须带幂等键"):
            self.store.ingest_event("user", "message", "", "user")

    def test_06_state_survives_restart_and_recovers(self):
        t, r = self.running_task()
        dep = self.store.create_task(t["goal_id"], "等前一个做完", by="hermes", depends_on=[t["id"]])
        self.assertEqual(self.store.set_ready(dep["id"], by="hermes")["status"], "blocked")
        self.assertEqual(self.store.get_task(dep["id"])["blocked_kind"], "dependency")
        self.store.close()
        self.tick(hours=1)
        reopened = ts.Store(self.db, clock=lambda: self.t).open()
        self.addCleanup(reopened.close)
        self.store = reopened
        self.assertEqual(self.store.summary()["tasks"], {"running": 1, "blocked": 1})
        self.assertEqual(self.store.get_run(r["id"])["status"], "running")
        # 一小时内有心跳：保留
        self.assertEqual(self.store.recover()["kept"], [r["id"]])
        # 超过 stale_after 没心跳：Run 过期，任务转技术阻塞并写明原因
        self.tick(hours=3)
        out = self.store.recover()
        self.assertEqual(out["stale"], [r["id"]])
        task = self.store.get_task(t["id"])
        self.assertEqual((task["status"], task["blocked_kind"]), ("blocked", "technical"))
        self.assertIn("没有心跳", task["blocked_reason"])
        # 台账后来说它其实跑完了：stale → done，任务进 review；再把它完成，依赖它的任务被条件恢复放行
        self.store.finish_run(r["id"], "done", by="hermes", receipt=OK_RECEIPT)
        self.assertEqual(self.store.get_task(t["id"])["status"], "blocked")  # 任务已不在 running，Run 结果不回写
        self.store.unblock_task(t["id"], by="hermes", note="Run 实际已完成")
        self.store.rework_task  # noqa: B018 —— 只是确认接口存在
        t2 = self.store.start_run(t["id"], "claude", by="hermes")["run"]
        self.store.receive_receipt(t2["id"], OK_RECEIPT)
        self.store.complete_task(t["id"], by="hermes")
        self.assertEqual(self.store.recover()["released"], [dep["id"]])
        self.assertEqual(self.store.get_task(dep["id"])["status"], "ready")
        # running 却没有进行中 Run 的任务（中断留下的）：恢复成技术阻塞
        with self.store._tx():
            self.store.conn.execute("UPDATE tasks SET status='running' WHERE id=?", (dep["id"],))
        self.assertEqual(self.store.recover()["orphaned"], [dep["id"]])
        self.assertEqual(self.store.events(kind="recover")[-1]["payload"]["orphaned"], [dep["id"]])

    def test_06b_interrupted_write_leaves_nothing_behind(self):
        t, r = self.running_task()
        before = (self.store.summary(), self.store.get_task(t["id"]))

        def crash(store):
            store._touch("tasks", t["id"], next_step="写到一半")
            store._task_to(store.get_task(t["id"]), "review", "hermes", "agent")
            raise RuntimeError("进程在这里断了")

        with self.assertRaises(RuntimeError):
            self.store.ingest_event("agent", "receipt", "receipt:crash", "hermes", handler=crash)
        self.assertEqual((self.store.summary(), self.store.get_task(t["id"])), before)
        self.assertEqual(self.store.events(kind="receipt"), [])
        # 同一个键再来一次，这次正常处理：说明失败的那次没有留下“已处理”的痕迹
        self.assertTrue(self.store.ingest_event("agent", "receipt", "receipt:crash", "hermes",
                                                handler=lambda s: s.receive_receipt(r["id"], OK_RECEIPT))["applied"])
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")

    def test_07_failure_and_block_reasons_are_recorded(self):
        t, r = self.running_task()
        self.store.receive_receipt(r["id"], {**OK_RECEIPT, "exit_code": 1, "worker_error": "claude 退出码 1", "timed_out": False})
        task = self.store.get_task(t["id"])
        self.assertEqual((task["status"], task["blocked_kind"]), ("blocked", "technical"))
        self.assertIn("claude 退出码 1", task["blocked_reason"])
        self.assertEqual(self.store.get_run(r["id"])["evidence"]["worker_error"], "claude 退出码 1")
        self.assertEqual(self.store.events(object_id=t["id"], kind="task.blocked")[-1]["payload"]["reason"], task["blocked_reason"])
        self.store.block_task(t["id"], by="hermes", kind="external", reason="飞书任务接口 502")
        self.assertEqual(self.store.get_task(t["id"])["blocked_reason"], "飞书任务接口 502")
        self.assertEqual(self.store.block_task(t["id"], by="hermes", kind="external", reason="飞书任务接口 502")["applied"], False)
        with self.assertRaisesRegex(ts.StateError, "写明类型"):
            self.store.block_task(self.ready_task(title="没原因")["id"], by="hermes", kind="technical", reason="")
        with self.assertRaises(ts.StateError):
            self.store.block_task(self.ready_task(title="类型不对")["id"], by="hermes", kind="whatever", reason="x")
        # 超时、护栏、车间要决定：各自的原因
        t2, r2 = self.running_task(title="超时的")
        self.store.receive_receipt(r2["id"], {**OK_RECEIPT, "exit_code": -1, "timed_out": True})
        self.assertRegex(self.store.get_task(t2["id"])["blocked_reason"], "超时")
        t3, r3 = self.running_task(title="护栏")
        self.store.receive_receipt(r3["id"], {**OK_RECEIPT, "guard_main_changed": True})
        self.assertEqual((self.store.get_run(r3["id"])["status"], self.store.get_task(t3["id"])["blocked_kind"]), ("guard", "decision"))
        t4, r4 = self.running_task(title="要拍板")
        self.store.receive_receipt(r4["id"], {**OK_RECEIPT, "need_decision": "要不要加依赖 foo？"})
        task4 = self.store.get_task(t4["id"])
        self.assertEqual(task4["blocked_kind"], "decision")
        self.assertIn("要不要加依赖 foo？", task4["blocked_reason"])

    def test_08_decision_block_cannot_be_bypassed_by_retry(self):
        t, r = self.running_task()
        self.store.receive_receipt(r["id"], {**OK_RECEIPT, "need_decision": "合并到 main 还是先放着？"})
        for who in ("hermes", "claude", "codex", "system"):
            with self.assertRaisesRegex(ts.NotAuthorized, "不能用技术重试绕过"):
                self.store.retry_task(t["id"], by=who)
            with self.assertRaisesRegex(ts.NotAuthorized, "不能替他解开"):
                self.store.unblock_task(t["id"], by=who)
        with self.assertRaises(ts.IllegalTransition):
            self.store.start_run(t["id"], "claude", by="hermes")
        # 走事件入口的自动重试同样被拒，且拒绝有记录
        out = self.store.ingest_event("system", "cron.retry", "cron:retry:1", "hermes",
                                      handler=lambda s: s.retry_task(t["id"], by="hermes"))
        self.assertEqual(out["applied"], False)
        self.assertIn("不能用技术重试绕过", out["rejected"])
        self.assertEqual(self.store.events(kind="cron.retry")[0]["applied"], 0)
        self.assertEqual(self.store.get_task(t["id"])["status"], "blocked")
        self.assertEqual(self.store.get_task(t["id"])["retries"], 0)
        # 只有常浩能解；解开后回到 ready
        self.assertEqual(self.store.unblock_task(t["id"], by="user", note="先放着，继续")["status"], "ready")
        # technical 阻塞可以自动重试，但有上限
        t2, r2 = self.running_task(title="技术失败")
        for i in range(ts.MAX_RETRIES):
            self.store.receive_receipt(r2["id"], {**OK_RECEIPT, "exit_code": 1, "worker_error": f"第 {i} 次失败"})
            self.assertEqual(self.store.retry_task(t2["id"], by="hermes")["retries"], i + 1)
            r2 = self.store.start_run(t2["id"], "claude", by="hermes")["run"]
        self.store.receive_receipt(r2["id"], {**OK_RECEIPT, "exit_code": 1, "worker_error": "又失败"})
        with self.assertRaisesRegex(ts.StateError, "超过上限"):
            self.store.retry_task(t2["id"], by="hermes")
        self.assertEqual(self.store.get_task(t2["id"])["status"], "blocked")

    def test_09_unreachable_external_source_never_completes_task(self):
        t, r = self.running_task()
        self.store.receive_receipt(r["id"], {**OK_RECEIPT, "committed_files": []})
        self.store.add_ref("task", t["id"], "github_pr", "26", "https://github.com/o/r/pull/26")

        def down(kind, ref):
            raise ConnectionError("gh: could not connect")

        out = self.store.check_ref("task", t["id"], "github_pr", "26", probe=down)
        self.assertEqual(out["reachable"], False)
        self.assertIn("could not connect", out["why"])
        self.assertEqual(self.store.check_ref("task", t["id"], "github_pr", "26", probe=lambda k, r: None)["reachable"], False)
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.assertEqual(self.store.get_goal(t["goal_id"])["status"], "authorized")
        self.assertEqual([e["kind"] for e in self.store.events(object_id=t["id"])][-2:], ["ref.unreachable", "ref.unreachable"])
        ok = self.store.check_ref("task", t["id"], "github_pr", "26", probe=lambda k, r: {"state": "MERGED"})
        self.assertEqual(ok, {"reachable": True, "seen": {"state": "MERGED"}})
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")  # 核对到也不自动完成
        # 恢复时探针连不上：Run 不动
        t2, r2 = self.running_task(title="探针坏了")
        out = self.store.recover(probe=lambda run: (_ for _ in ()).throw(OSError("探针坏了")))
        self.assertEqual((out["kept"], out["settled"]), ([r2["id"]], []))
        self.assertEqual(self.store.get_task(t2["id"])["status"], "running")
        # 没有证据的任务不能标完成
        t3, r3 = self.running_task(title="没证据")
        with self.store._tx():
            self.store.conn.execute("UPDATE tasks SET status='review' WHERE id=?", (t3["id"],))
            self.store.conn.execute("UPDATE runs SET status='abandoned' WHERE id=?", (r3["id"],))
        with self.assertRaisesRegex(ts.EvidenceMissing, "没有证据"):
            self.store.complete_task(t3["id"], by="hermes")
        self.assertEqual(self.store.get_task(t3["id"])["status"], "review")

    def test_10_existing_dispatch_orders_stay_compatible(self):
        orders = {
            "D20000101-1": order("cleaned", merged_to="1234567abcd", receipt=OK_RECEIPT),
            "D20000101-2": order("abandoned", receipt={**OK_RECEIPT, "need_decision": "要不要继续？"}),
            "D20000101-3": {"no": "D20000101-3", "project": "github-center", "tool": "codex", "mode": "read", "status": "done",
                            "created": "2000-01-01T10:00:00", "updated": "2000-01-01T10:05:00", "cwd": "/x", "exit_code": 0},
            "D20000101-4": order("running", issue=7, issue_url="https://github.com/o/r/issues/7"),
            "D20000101-5": order("done", issue=11, issue_url="https://github.com/o/r/issues/11", pr=13,
                                 pr_url="https://github.com/o/r/pull/13", receipt=OK_RECEIPT),
        }
        root = make_dispatch(self.root / "dispatch", orders)
        (root / "D20000101-9").mkdir()
        (root / "D20000101-9" / "order.json").write_text("{坏掉的", encoding="utf-8")
        (root / "D20000101-5" / "out" / "receipt.json").write_text("[]", encoding="utf-8")  # 回执不是对象：当没有
        stats = self.store.sync_orders(root)
        self.assertEqual({k: stats[k] for k in ("seen", "created", "updated", "unchanged")},
                         {"seen": 5, "created": 5, "updated": 0, "unchanged": 0})
        self.assertEqual(self.store.summary()["goals"], {})
        self.assertEqual(self.store.summary()["tasks"], {})
        runs = {r["order_no"]: r for r in self.store.list_runs(unlinked=True)}
        self.assertEqual(sorted(runs), sorted(orders))
        self.assertEqual({k: v["status"] for k, v in runs.items()},
                         {k: v["status"] for k, v in orders.items()})
        self.assertEqual(runs["D20000101-1"]["evidence"]["merged_to"], "1234567abcd")
        self.assertEqual(runs["D20000101-1"]["session_id"], "sess-1")
        self.assertEqual(runs["D20000101-5"]["evidence"]["pr_url"], "https://github.com/o/r/pull/13")
        self.assertEqual(runs["D20000101-5"]["receipt"], {})
        self.assertEqual(runs["D20000101-3"]["agent"], "codex")
        self.assertEqual(runs["D20000101-4"]["ended_at"], None)
        # 再同步一遍：什么都不变
        stats = self.store.sync_orders(root)
        self.assertEqual((stats["created"], stats["updated"], stats["unchanged"]), (0, 0, 5))
        self.assertEqual(self.store.events(kind="order.sync")[0]["dup_count"], 1)
        # 关联到任务后，台账跑完才反映到任务上；台账跳过中间状态也能跟上
        t = self.ready_task()
        self.assertEqual(self.store.link_run("D20000101-4", t["id"], by="hermes")["task_status"], "running")
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")
        write_order(root, "D20000101-4", order("done", issue=7, updated="2000-01-01T11:00:00", receipt=OK_RECEIPT))
        stats = self.store.sync_orders(root)
        self.assertEqual((stats["updated"], stats["unchanged"]), (1, 4))
        self.assertEqual(self.store.get_run("D20000101-4")["status"], "done")
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        write_order(root, "D20000101-4", order("cleaned", issue=7, updated="2000-01-01T12:00:00", merged_to="deadbeef0"))
        self.store.sync_orders(root)
        self.assertEqual(self.store.get_run("D20000101-4")["status"], "cleaned")
        self.assertEqual(self.store.events(object_id="D20000101-4", kind="order.sync")[-1]["payload"]["status"], "cleaned")
        self.assertEqual([e["kind"] for e in self.store.events(object_id=self.store.get_run("D20000101-4")["id"])],
                         ["run.import", "run.link", "run.done", "run.resync"])
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        # 台账目录不存在 / 空：不报错
        self.assertEqual(self.store.sync_orders(self.root / "nowhere")["seen"], 0)
        # 老台账文件没被动过
        self.assertEqual(json.loads((root / "D20000101-1" / "order.json").read_text())["status"], "cleaned")


class MoreTests(Base):
    """B1.3 其余必处理情形、备份恢复、权限、命令行。"""

    def test_illegal_transitions(self):
        t = self.ready_task()
        g = self.store.get_goal(t["goal_id"])
        with self.assertRaises(ts.IllegalTransition):
            self.store.complete_task(t["id"], by="user")  # ready → completed 不行
        with self.assertRaises(ts.IllegalTransition):
            self.store.unblock_task(t["id"], by="user")  # 没阻塞
        with self.assertRaises(ts.IllegalTransition):
            self.store.rework_task(t["id"], by="user", note="x")  # 不在 review
        self.store.cancel_task(t["id"], by="user")
        with self.assertRaises(ts.IllegalTransition):
            self.store.set_ready(t["id"], by="user")  # 终态不能再动
        self.assertEqual(self.store.cancel_task(t["id"], by="user")["applied"], False)  # 重复撤销是空操作
        self.assertEqual(self.store.authorize_goal(g["id"], by="user")["applied"], False)  # 已授权：空操作
        with self.assertRaises(ts.IllegalTransition):
            self.store.cancel_goal(self.completed_goal()["id"], by="user")
        with self.assertRaises(ts.StateError):
            self.store.create_goal("状态不对", by="user", status="completed")
        with self.assertRaises(ts.StateError):
            self.store.update_task(t["id"], by="user", status="ready")  # 不能绕过状态机改状态
        with self.assertRaises(ts.StateError):
            self.store.start_run(self.ready_task(title="y")["id"], "claude", by="hermes", status="done")

    def completed_goal(self):
        t, r = self.running_task(title="z")
        self.store.receive_receipt(r["id"], OK_RECEIPT)
        self.store.complete_task(t["id"], by="hermes")
        self.store.complete_goal(t["goal_id"], by="user")
        return self.store.get_goal(t["goal_id"])

    def test_cancel_rules(self):
        g = self.goal()
        t = self.store.create_task(g["id"], "刚发现的", by="hermes")
        self.assertEqual(self.store.cancel_task(t["id"], by="hermes")["status"], "canceled")  # observed 的 Hermes 可撤
        t2 = self.ready_task(g["id"], title="已授权的")
        with self.assertRaisesRegex(ts.NotAuthorized, "只有常浩能撤销"):
            self.store.cancel_task(t2["id"], by="hermes")
        r = self.store.start_run(t2["id"], "claude", by="hermes")["run"]
        self.store.cancel_task(t2["id"], by="user", reason="方向变了")
        self.assertEqual(self.store.get_run(r["id"])["status"], "abandoned")
        with self.assertRaises(ts.NotAuthorized):
            self.store.cancel_goal(g["id"], by="hermes")

    def test_rework_and_update(self):
        t, r = self.running_task()
        self.store.receive_receipt(r["id"], OK_RECEIPT)
        self.assertEqual(self.store.rework_task(t["id"], by="user", note="测试没过")["status"], "ready")
        self.assertEqual(self.store.update_task(t["id"], by="hermes", next_step="补测试")["next_step"], "补测试")
        r2 = self.store.start_run(t["id"], "claude", by="hermes")["run"]
        with self.assertRaises(ts.IllegalTransition):
            self.store.heartbeat(r["id"])  # 旧 Run 已结束
        self.tick(minutes=5)
        self.store.heartbeat(r2["id"])
        self.assertEqual(self.store.get_run(r2["id"])["heartbeat_at"], self.t.isoformat(timespec="seconds"))
        self.assertEqual(self.store.add_ref("run", r2["id"], "file", "/tmp/x.log")["applied"], True)
        self.assertEqual(self.store.add_ref("run", r2["id"], "file", "/tmp/x.log")["applied"], False)
        with self.assertRaises(ts.NotFound):
            self.store.add_ref("task", "T20000101-9", "file", "x")

    def test_backup_restore_and_migration_guard(self):
        t, r = self.running_task()
        backup = self.store.backup()
        self.assertEqual(backup.parent, self.db.parent / "backups")
        self.store.receive_receipt(r["id"], OK_RECEIPT)
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.store.restore(backup)
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")
        self.assertEqual(self.store.version(), ts.SCHEMA_VERSION)
        with self.assertRaises(ts.NotFound):
            self.store.restore(self.root / "nope.db")
        newer = self.root / "newer.db"
        with ts.Store(newer) as s:
            with s._tx():
                s.conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
        with self.assertRaisesRegex(ts.StateError, "比程序支持的"):
            ts.Store(newer).open()

    @unittest.skipUnless(os.name == "posix", "只在 POSIX 上查权限")
    def test_file_permissions(self):
        self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.db.parent.stat().st_mode), 0o700)

    def test_cli_roundtrip(self):
        env = {**os.environ, "DT_STATE_DB": str(self.db), "DT_DISPATCH": str(self.root / "no-dispatch")}

        def cli(*args, ok=True):
            r = subprocess.run([sys.executable, "-B", str(MODULE), *args], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0 if ok else 1, r.stdout + r.stderr)
            out = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(out["ok"], ok)
            return out

        self.store.close()
        self.assertEqual(cli("init")["schema_version"], 1)
        g = cli("goal-new", "--title", "命令行目标", "--by", "user", "--status", "authorized")["goal"]
        self.assertIn("只有常浩本人能授权", cli("goal-new", "--title", "x", "--by", "hermes", "--status", "authorized", ok=False)["msg"])
        t = cli("task-new", "--goal", g["id"], "--title", "命令行任务", "--by", "hermes", "--acceptance", "能跑")["task"]
        self.assertEqual(cli("task-ready", t["id"], "--by", "hermes")["status"], "ready")
        r = cli("run-start", "--task", t["id"], "--agent", "claude", "--by", "hermes", "--order", "D20000101-7", "--key", "k1")
        self.assertTrue(r["applied"])
        self.assertEqual(cli("run-start", "--task", t["id"], "--agent", "claude", "--by", "hermes", "--key", "k1")["applied"], False)
        self.assertIn("已有进行中的 Run", cli("run-start", "--task", t["id"], "--agent", "codex", "--by", "hermes", ok=False)["msg"])
        receipt = self.root / "receipt.json"
        receipt.write_text(json.dumps(OK_RECEIPT), encoding="utf-8")
        self.assertEqual(cli("run-receipt", r["run"]["id"], "--receipt", str(receipt))["result"]["task_status"], "review")
        self.assertEqual(cli("run-receipt", r["run"]["id"], "--receipt", str(receipt))["applied"], False)
        cli("ref-add", "--type", "task", "--id", t["id"], "--kind", "github_pr", "--ref", "26")
        self.assertEqual(cli("task-complete", t["id"], "--by", "hermes")["status"], "completed")
        self.assertEqual(cli("show", t["id"])["object"]["status"], "completed")
        self.assertEqual(cli("show", "D20000101-7")["object"]["status"], "done")
        self.assertEqual(len(cli("show", t["id"])["refs"]), 1)
        self.assertIn("没有这个对象", cli("show", "T19990101-1", ok=False)["msg"])
        self.assertEqual(len(cli("list")["tasks"]), 1)
        self.assertGreater(len(cli("events", t["id"])["events"]), 3)
        self.assertEqual(cli("goal-complete", g["id"], "--by", "user")["status"], "completed")
        rec = cli("recover")
        self.assertEqual((rec["msg"], rec["synced"]["seen"], rec["kept"]), ("恢复完成：没有需要处理的", 0, []))
        self.assertIn("--yes", cli("restore", "--from", str(receipt), ok=False)["msg"])
        bak = cli("backup", "--to", str(self.root / "b.db"))
        self.assertIn("b.db", bak["msg"])
        self.assertEqual(cli("restore", "--from", str(self.root / "b.db"), "--yes")["goals"], {"completed": 1})
        with ts.Store(self.db) as s:
            self.assertEqual(s.get_task(t["id"])["status"], "completed")
            self.assertEqual(s.get_goal(g["id"])["status"], "completed")


def order(status, receipt=None, **extra):
    """造一张和真实台账同形状的 order.json（键名和 dispatch.py 一致）。"""
    o = {"no": None, "project": "digital-twin", "tool": "claude", "mode": "write", "status": status,
         "created": "2000-01-01T10:00:00", "updated": "2000-01-01T10:30:00", "cwd": "/wt", "exit_code": 0,
         "branch": "hermes/D", "base": "aaa", **extra}
    if receipt is not None:
        o["_receipt"] = receipt
    return o


def write_order(root, no, o):
    d = root / no
    (d / "out").mkdir(parents=True, exist_ok=True)
    o = {**o, "no": no, "branch": f"hermes/{no}"}
    receipt = o.pop("_receipt", None)
    (d / "order.json").write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding="utf-8")
    if receipt is not None:
        (d / "out" / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")


def make_dispatch(root, orders):
    root.mkdir(parents=True, exist_ok=True)
    for no, o in orders.items():
        write_order(root, no, o)
    return root


if __name__ == "__main__":
    unittest.main()
