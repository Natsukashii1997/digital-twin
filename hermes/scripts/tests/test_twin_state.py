"""隔离自测：持久任务状态与事件底座（twin_state.py），覆盖任务书 B1.5 的十项验收、B1.3 的必处理情形，
以及 2026-10-29 独立评审返修要求（R1～R4 与工程契约）的负向用例。

每项测试用独立临时目录里的状态库、固定时钟和虚构的派单台账；不碰 ~/.hermes、不碰真实工单、不调用模型或外部服务。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_state.py -v
"""
import datetime as dt
import importlib.util
import json
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
MODULE = HERE.parent / "twin_state.py"
SPEC = importlib.util.spec_from_file_location("twin_state", MODULE)
ts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ts)

TZ = dt.timezone(dt.timedelta(hours=8))
T0 = dt.datetime(2000, 1, 1, 9, 0, tzinfo=TZ)
OK_RECEIPT = {"exit_code": 0, "timed_out": False, "worker_error": False, "session_id": "sess-1",
              "started": "2000-01-01T09:00:00", "ended": "2000-01-01T09:30:00",
              "branch": "hermes/D20000101-1", "head_before": "aaa", "head_after": "bbb",
              "committed_files": ["hermes/scripts/x.py"], "uncommitted": [], "guard_main_changed": False,
              "guard_remote_changed": False, "need_decision": None, "summary": "改好了。"}
FAIL_RECEIPT = {**OK_RECEIPT, "exit_code": 1, "worker_error": "claude 退出码 1"}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-state-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "state" / "twin-state.db"
        self.ledger = self.root / "dispatch"   # 默认不存在：台账“缺失”
        self.t = T0
        self.orders = 0
        self.store = self.open_store()
        self.addCleanup(lambda: self.store.close())

    def open_store(self):
        return ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()

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

    def running_task(self, with_order=True, agent="claude", **kw):
        t = self.ready_task(**kw)
        self.orders += 1
        order_no = f"D20000101-{self.orders}" if with_order else None
        r = self.store.start_run(t["id"], agent, by="hermes", order_no=order_no)["run"]
        return self.store.get_task(t["id"]), r

    def reviewed_task(self, **kw):
        t, r = self.running_task(**kw)
        self.store.receive_receipt(r["id"], OK_RECEIPT)
        return self.store.get_task(t["id"]), r

    def accepted(self, task_id, run_id, by="codex"):
        return self.store.accept_task(task_id, by=by, basis="回执与提交核对无误", cites=[run_id])

    def kinds(self, obj_id):
        return [e["kind"] for e in self.store.events(object_id=obj_id)]

    def cli(self, env, *args, ok=True):
        r = subprocess.run([sys.executable, "-B", str(MODULE), *args], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0 if ok else 1, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["ok"], ok)
        for secret in ("ou_test_owner", "ou_env_owner", "ou_someone_else", "oc_test_chat"):
            self.assertNotIn(secret, r.stdout + r.stderr)      # 身份值不进输出
        return out

    def lark_env(self, env, messages):
        """假 lark-cli（fake_lark.py）：messages = {message_id: 发送者 open_id}，都在常浩和 Hermes 的私聊里、时间是现在；
        私聊 chat_id 写进临时的 600 配置文件。B2.1 起 --by user 只认这样回读核实过的消息。"""
        fake = self.root / "fake-lark"
        fake.mkdir(exist_ok=True)
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        state = {"mget": {mid: {"chat_id": "oc_test_chat", "content": "{}", "create_time": now, "deleted": False, "message_id": mid,
                                "msg_type": "text", "sender": {"id": sender, "id_type": "open_id", "sender_type": "user"},
                                "updated": False} for mid, sender in messages.items()}}
        (fake / "state.json").write_text(json.dumps(state), encoding="utf-8")
        chat = self.root / "owner_chat_id"
        chat.write_text("oc_test_chat\n", encoding="utf-8")
        chat.chmod(0o600)
        return {**env, "DT_LARK_CLI": str(HERE / "fake_lark.py"), "FAKE_LARK_DIR": str(fake), "DT_OWNER_CHAT_FILE": str(chat)}


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
        other = self.store.create_goal("目标乙", by="hermes")
        with self.assertRaisesRegex(ts.StateError, "不能跨目标依赖"):
            self.store.create_task(other["id"], "跨目标依赖", by="hermes", depends_on=[t["id"]])
        self.assertEqual(other["id"], "G20000101-2")  # 同一天编号递增，换一天重新计数
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
        self.store.authorize_goal(g["id"], by="user")
        self.assertEqual(self.store.set_ready(t["id"], by="hermes")["status"], "ready")
        self.store.cancel_goal(g["id"], by="user", reason="不做了")
        self.assertEqual(self.store.get_task(t["id"])["status"], "canceled")
        with self.assertRaises(ts.IllegalTransition):
            self.store.start_run(t["id"], "claude", by="hermes")

    def test_03_task_links_multiple_runs(self):
        t, r1 = self.running_task()
        with self.assertRaisesRegex(ts.IllegalTransition, "已有进行中的 Run"):
            self.store.start_run(t["id"], "codex", by="hermes")
        self.store.receive_receipt(r1["id"], FAIL_RECEIPT)
        self.assertEqual(self.store.get_task(t["id"])["status"], "blocked")
        self.assertEqual(self.store.retry_task(t["id"], by="hermes")["retries"], 1)
        r2 = self.store.start_run(t["id"], "codex", by="hermes", order_no="D20000101-2")["run"]
        self.store.receive_receipt(r2["id"], OK_RECEIPT)
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        # 既有工单（未关联、已结束）也能挂到同一任务上作证据
        self.store.sync_orders(make_dispatch(self.ledger, {"D20000101-3": order("done", receipt=OK_RECEIPT)}))
        self.assertEqual(self.store.link_run("D20000101-3", t["id"], by="hermes")["applied"], True)
        runs = self.store.list_runs(t["id"])
        self.assertEqual([r["order_no"] for r in runs], ["D20000101-1", "D20000101-2", "D20000101-3"])
        self.assertEqual([r["status"] for r in runs], ["failed", "done", "done"])
        self.assertEqual([r["agent"] for r in runs], ["claude", "codex", "claude"])
        self.assertEqual(self.store.get_run("D20000101-2")["session_id"], "sess-1")
        self.assertEqual(self.store.current_run(t["id"])["order_no"], "D20000101-2")   # 后挂的旧单只是证据，不改当前 Run
        with self.assertRaisesRegex(ts.StateError, "已关联任务"):
            self.store.link_run("D20000101-3", self.ready_task(title="别的任务")["id"], by="hermes")

    def test_04_run_success_does_not_close_goal(self):
        t, r = self.running_task()
        out = self.store.receive_receipt(r["id"], OK_RECEIPT)
        self.assertEqual(out["result"]["task_status"], "review")
        self.assertEqual(self.store.get_run(r["id"])["status"], "done")
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.assertEqual(self.store.get_goal(t["goal_id"])["status"], "authorized")
        # 台账后续 merged / cleaned 只改 Run，不动任务和目标
        self.store.update_run(r["id"], "merged", by="hermes", evidence={"merged_to": "abc1234"})
        self.store.update_run(r["id"], "cleaned", by="hermes")
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.assertEqual(self.store.get_run(r["id"])["evidence"]["merged_to"], "abc1234")
        self.accepted(t["id"], r["id"])
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
        with self.assertRaisesRegex(ts.IllegalTransition, "已是 done"):
            self.store.receive_receipt(rid, FAIL_RECEIPT)
        self.assertEqual(self.store.get_run(rid)["status"], "done")
        calls = []
        for _ in range(2):
            self.store.ingest_event("user", "message", "feishu:om_456", "user", {"text": "可以"},
                                    handler=lambda s: calls.append(1))
        self.assertEqual(calls, [1])
        with self.assertRaisesRegex(ts.StateError, "必须带幂等键"):
            self.store.ingest_event("user", "message", "", "user")

    def test_06_state_survives_restart_and_recovers(self):
        t, r = self.running_task(with_order=False)   # 没有工单号：只能按心跳判过期
        dep = self.store.create_task(t["goal_id"], "等前一个做完", by="hermes", depends_on=[t["id"]])
        self.assertEqual(self.store.set_ready(dep["id"], by="hermes")["status"], "blocked")
        self.assertEqual(self.store.get_task(dep["id"])["blocked_kind"], "dependency")
        self.store.close()
        self.tick(hours=1)
        self.store = self.open_store()
        self.assertEqual(self.store.summary()["tasks"], {"running": 1, "blocked": 1})
        self.assertEqual(self.store.get_run(r["id"])["status"], "running")
        self.assertEqual(self.store.recover()["kept"], [r["id"]])          # 一小时内有心跳：保留
        self.tick(hours=3)
        out = self.store.recover()                                           # 超过 stale_after 没心跳：过期
        self.assertEqual((out["ledger"], out["stale"]), ("missing", [r["id"]]))
        task = self.store.get_task(t["id"])
        self.assertEqual((task["status"], task["blocked_kind"]), ("blocked", "technical"))
        self.assertIn("没有心跳", task["blocked_reason"])
        # 迟来的成功结果：Run 自己记为 done，任务已不在 running，不回写（留事件）
        self.store.finish_run(r["id"], "done", by="hermes", receipt=OK_RECEIPT)
        self.assertEqual(self.store.get_run(r["id"])["status"], "done")
        self.assertEqual(self.store.get_task(t["id"])["status"], "blocked")
        self.assertIn("run.result_not_applied", self.kinds(r["id"]))
        # Hermes 解技术阻塞算一次重试；第二个 Run 跑完、验收、完成后，依赖它的任务被条件恢复放行
        self.assertEqual(self.store.unblock_task(t["id"], by="hermes")["retries"], 1)
        r2 = self.store.start_run(t["id"], "claude", by="hermes")["run"]
        self.store.receive_receipt(r2["id"], OK_RECEIPT)
        self.accepted(t["id"], r2["id"])
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
        self.assertTrue(self.store.ingest_event("agent", "receipt", "receipt:crash", "hermes",
                                                handler=lambda s: s.receive_receipt(r["id"], OK_RECEIPT))["applied"])
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")

    def test_07_failure_and_block_reasons_are_recorded(self):
        t, r = self.running_task()
        self.store.receive_receipt(r["id"], FAIL_RECEIPT)
        task = self.store.get_task(t["id"])
        self.assertEqual((task["status"], task["blocked_kind"]), ("blocked", "technical"))
        self.assertIn("claude 退出码 1", task["blocked_reason"])
        self.assertEqual(self.store.get_run(r["id"])["evidence"]["worker_error"], "claude 退出码 1")
        self.assertEqual(self.store.events(object_id=t["id"], kind="task.blocked")[-1]["payload"]["reason"], task["blocked_reason"])
        # 同类更新原因可以；重复设置是空操作
        self.store.block_task(t["id"], by="hermes", kind="technical", reason="重跑还是报错，怀疑依赖没装")
        self.assertEqual(self.store.get_task(t["id"])["blocked_reason"], "重跑还是报错，怀疑依赖没装")
        self.assertEqual(self.store.block_task(t["id"], by="hermes", kind="technical", reason="重跑还是报错，怀疑依赖没装")["applied"], False)
        with self.assertRaisesRegex(ts.StateError, "写明类型"):
            self.store.block_task(self.ready_task(title="没原因")["id"], by="hermes", kind="technical", reason="")
        with self.assertRaises(ts.StateError):
            self.store.block_task(self.ready_task(title="类型不对")["id"], by="hermes", kind="whatever", reason="x")
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
        out = self.store.ingest_event("system", "cron.retry", "cron:retry:1", "hermes",
                                      handler=lambda s: s.retry_task(t["id"], by="hermes"))
        self.assertEqual(out["applied"], False)
        self.assertIn("不能用技术重试绕过", out["rejected"])
        self.assertEqual(self.store.events(kind="cron.retry")[0]["applied"], 0)
        self.assertEqual((self.store.get_task(t["id"])["status"], self.store.get_task(t["id"])["retries"]), ("blocked", 0))
        self.assertEqual(self.store.unblock_task(t["id"], by="user", note="先放着，继续")["status"], "ready")
        # technical 阻塞可以自动重试，但有上限
        t2, r2 = self.running_task(title="技术失败")
        for i in range(ts.MAX_RETRIES):
            self.store.receive_receipt(r2["id"], {**FAIL_RECEIPT, "worker_error": f"第 {i} 次失败"})
            self.assertEqual(self.store.retry_task(t2["id"], by="hermes")["retries"], i + 1)
            r2 = self.store.start_run(t2["id"], "claude", by="hermes")["run"]
        self.store.receive_receipt(r2["id"], {**FAIL_RECEIPT, "worker_error": "又失败"})
        with self.assertRaisesRegex(ts.StateError, "超过上限"):
            self.store.retry_task(t2["id"], by="hermes")
        with self.assertRaisesRegex(ts.StateError, "超过上限"):
            self.store.unblock_task(t2["id"], by="hermes")   # 解技术阻塞和重试共用同一个上限
        self.assertEqual(self.store.get_task(t2["id"])["status"], "blocked")

    def test_09_unreachable_external_source_never_completes_task(self):
        t, r = self.reviewed_task()
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
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")   # 核对到也不自动完成
        # 恢复时探针坏了、台账缺失：Run 不动，列进 unknown
        t2, r2 = self.running_task(title="探针坏了")
        out = self.store.recover(probe=lambda run: (_ for _ in ()).throw(OSError("探针坏了")))
        self.assertEqual((out["unknown"], out["stale"], out["settled"]), ([r2["id"]], [], []))
        self.assertEqual(self.store.recover()["unknown"], [r2["id"]])      # 台账目录不存在：同样不下判断
        self.assertEqual(self.store.get_task(t2["id"])["status"], "running")
        # 没有验收结论的任务不能标完成，哪怕有 PR 引用和成功的 Run
        with self.assertRaisesRegex(ts.EvidenceMissing, "还没有通过的验收结论"):
            self.store.complete_task(t["id"], by="hermes")
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")

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
        root = make_dispatch(self.ledger, orders)
        (root / "D20000101-9").mkdir()
        (root / "D20000101-9" / "order.json").write_text("{坏掉的", encoding="utf-8")
        (root / "D20000101-5" / "out" / "receipt.json").write_text("[]", encoding="utf-8")  # 回执不是对象：当没有
        stats = self.store.sync_orders()
        self.assertEqual({k: stats[k] for k in ("ledger", "seen", "created", "updated", "unchanged", "conflicts")},
                         {"ledger": "ok", "seen": 5, "created": 5, "updated": 0, "unchanged": 0, "conflicts": []})
        self.assertEqual(stats["skipped"], ["D20000101-9：JSONDecodeError"])
        self.assertEqual((self.store.summary()["goals"], self.store.summary()["tasks"]), ({}, {}))
        runs = {r["order_no"]: r for r in self.store.list_runs(unlinked=True)}
        self.assertEqual(sorted(runs), sorted(orders))
        self.assertEqual({k: v["status"] for k, v in runs.items()}, {k: v["status"] for k, v in orders.items()})
        self.assertEqual(runs["D20000101-1"]["evidence"]["merged_to"], "1234567abcd")
        self.assertEqual(runs["D20000101-1"]["session_id"], "sess-1")
        self.assertEqual(runs["D20000101-5"]["evidence"]["pr_url"], "https://github.com/o/r/pull/13")
        self.assertEqual(runs["D20000101-5"]["receipt"], {})
        self.assertEqual(runs["D20000101-3"]["agent"], "codex")
        self.assertEqual(runs["D20000101-4"]["ended_at"], None)
        stats = self.store.sync_orders()                                   # 再同步一遍：什么都不变
        self.assertEqual((stats["created"], stats["updated"], stats["unchanged"]), (0, 0, 5))
        self.assertEqual(self.store.events(kind="order.sync")[0]["dup_count"], 1)
        # 关联到任务后，台账跑完才反映到任务上；台账跳过中间状态也能跟上
        t = self.ready_task()
        self.assertEqual(self.store.link_run("D20000101-4", t["id"], by="hermes")["task_status"], "running")
        write_order(root, "D20000101-4", order("done", issue=7, updated="2000-01-01T11:00:00", receipt=OK_RECEIPT))
        stats = self.store.sync_orders()
        self.assertEqual((stats["updated"], stats["unchanged"]), (1, 4))
        self.assertEqual(self.store.get_run("D20000101-4")["status"], "done")
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        write_order(root, "D20000101-4", order("cleaned", issue=7, updated="2000-01-01T12:00:00", merged_to="deadbeef0"))
        self.store.sync_orders()
        self.assertEqual(self.store.get_run("D20000101-4")["status"], "cleaned")
        self.assertEqual([e["kind"] for e in self.store.events(object_id=self.store.get_run("D20000101-4")["id"])],
                         ["run.import", "run.link", "run.done", "run.resync"])
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        # 台账目录不存在：不是“没有工单”，是“缺失”
        missing = self.store.sync_orders(self.root / "nowhere")
        self.assertEqual((missing["ledger"], missing["seen"]), ("missing", 0))
        self.assertEqual(json.loads((root / "D20000101-1" / "order.json").read_text())["status"], "cleaned")  # 老台账原样


class ReviewFixTests(Base):
    """2026-10-29 独立评审的返修项：R1～R4 与工程契约，每项先有能复现问题的用例。"""

    def test_r1_decision_block_cannot_be_downgraded_then_retried(self):
        t, r = self.running_task()
        self.store.receive_receipt(r["id"], {**OK_RECEIPT, "need_decision": "合并还是先放着？"})
        self.assertEqual(self.store.get_task(t["id"])["blocked_kind"], "decision")
        for who in ("hermes", "claude", "codex", "system"):
            with self.assertRaisesRegex(ts.NotAuthorized, "不能改成 technical 类阻塞"):
                self.store.block_task(t["id"], by=who, kind="technical", reason="当成技术问题重跑")
            for kind in ("external", "dependency"):
                with self.assertRaises(ts.NotAuthorized):
                    self.store.block_task(t["id"], by=who, kind=kind, reason="换个类型")
        self.assertEqual(self.store.get_task(t["id"])["blocked_kind"], "decision")
        with self.assertRaises(ts.NotAuthorized):
            self.store.retry_task(t["id"], by="hermes")
        # 非 user 可以补充原因（类型不变），也可以把别的类型升级成 decision；都不能降级
        self.store.block_task(t["id"], by="hermes", kind="decision", reason="合并还是先放着？常浩还没回")
        self.assertEqual(self.store.get_task(t["id"])["blocked_reason"], "合并还是先放着？常浩还没回")
        t2, r2 = self.running_task(title="技术失败")
        self.store.receive_receipt(r2["id"], FAIL_RECEIPT)
        with self.assertRaisesRegex(ts.NotAuthorized, "只能把阻塞升级成 decision"):
            self.store.block_task(t2["id"], by="hermes", kind="external", reason="改成外部")
        self.store.block_task(t2["id"], by="hermes", kind="decision", reason="重跑两次都挂，要常浩定")
        with self.assertRaises(ts.NotAuthorized):
            self.store.retry_task(t2["id"], by="hermes")
        # 已阻塞任务改类型时非法类型、空原因同样被拒
        with self.assertRaises(ts.StateError):
            self.store.block_task(t2["id"], by="user", kind="whatever", reason="x")
        with self.assertRaises(ts.StateError):
            self.store.block_task(t2["id"], by="user", kind="technical", reason="")
        # 常浩可以改类型；改成 technical 后 Hermes 才能重试
        self.store.block_task(t2["id"], by="user", kind="technical", reason="是环境问题，让它再试")
        self.assertEqual(self.store.events(object_id=t2["id"], kind="task.block.update")[-1]["payload"]["from"], "decision")
        self.assertEqual(self.store.retry_task(t2["id"], by="hermes")["status"], "ready")

    def test_r2_late_result_of_old_run_does_not_override_current_run(self):
        t, a = self.running_task(with_order=False)
        self.tick(hours=3)
        self.assertEqual(self.store.recover()["stale"], [a["id"]])
        self.store.retry_task(t["id"], by="hermes")
        b = self.store.start_run(t["id"], "claude", by="hermes")["run"]
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")
        # A 迟到的成功回执：A 自己变 done，任务仍由 B 推进
        out = self.store.receive_receipt(a["id"], OK_RECEIPT)
        self.assertEqual((out["applied"], out["result"]["task_status"]), (True, "late"))
        self.assertEqual(self.store.get_run(a["id"])["status"], "done")
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")
        late = self.store.events(object_id=a["id"], kind="run.late_result")[-1]["payload"]
        self.assertEqual((late["current_run"], late["status"]), (b["id"], "done"))
        # 再来一个过期 + 迟到失败的：同样不碰任务
        self.store.receive_receipt(b["id"], FAIL_RECEIPT)
        self.store.retry_task(t["id"], by="hermes")
        c = self.store.start_run(t["id"], "codex", by="hermes")["run"]
        with self.store._tx():  # 把 B 人为退回 stale，模拟“B 过期后又迟到失败”
            self.store.conn.execute("UPDATE runs SET status='stale' WHERE id=?", (b["id"],))
        self.store.finish_run(b["id"], "failed", by="hermes", receipt={**FAIL_RECEIPT, "worker_error": "迟到的失败"})
        self.assertEqual(self.store.get_run(b["id"])["status"], "failed")
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")
        self.assertEqual(self.store.current_run(t["id"])["id"], c["id"])
        # 台账路径一样：旧工单失败后任务换了新 Run，旧工单再续跑、再跑完，都只改它自己
        self.store.sync_orders(make_dispatch(self.ledger, {"D20000101-7": order("running")}))
        t3 = self.ready_task(title="台账任务")["id"]
        self.assertEqual(self.store.link_run("D20000101-7", t3, by="hermes")["task_status"], "running")
        write_order(self.ledger, "D20000101-7", order("failed", updated="2000-01-01T15:00:00", receipt=FAIL_RECEIPT))
        self.store.sync_orders()
        self.assertEqual(self.store.get_task(t3)["blocked_kind"], "technical")
        self.store.retry_task(t3, by="hermes")
        b3 = self.store.start_run(t3, "claude", by="hermes")["run"]
        write_order(self.ledger, "D20000101-7", order("running", updated="2000-01-01T15:30:00"))
        self.store.sync_orders()                                         # 旧工单续跑：不是当前 Run，任务不动
        self.assertEqual(self.store.get_run("D20000101-7")["status"], "running")
        self.assertEqual(self.kinds(self.store.get_run("D20000101-7")["id"])[-1], "run.resume_conflict")
        write_order(self.ledger, "D20000101-7", order("done", updated="2000-01-01T16:00:00", receipt=OK_RECEIPT))
        self.store.sync_orders()                                         # 旧工单跑完：迟到结果，任务不动
        self.assertEqual(self.store.get_run("D20000101-7")["status"], "done")
        self.assertIn("run.late_result", self.kinds(self.store.get_run("D20000101-7")["id"]))
        self.assertEqual((self.store.get_task(t3)["status"], self.store.current_run(t3)["id"]), ("running", b3["id"]))

    def test_r3_execution_result_is_not_acceptance(self):
        # 空回执、缺字段、exit_code 不是整数：都不算成功
        for bad, why in (({}, "缺 exit_code、ended"), ({"exit_code": 0}, "缺 ended"),
                         ({"exit_code": "0", "ended": "x"}, "不是整数"), ({"exit_code": None, "ended": "x"}, "不是整数")):
            t, r = self.running_task()
            out = self.store.receive_receipt(r["id"], bad)
            self.assertEqual(out["result"]["status"], "failed", bad)
            task = self.store.get_task(t["id"])
            self.assertEqual((task["status"], task["blocked_kind"]), ("blocked", "technical"), bad)
            self.assertIn(why, self.store.get_run(r["id"])["note"])
            self.assertIn(why, task["blocked_reason"])
        self.assertEqual(ts.classify_receipt({"exit_code": True, "ended": "x"})[0], "failed")
        self.assertEqual(ts.classify_receipt({"exit_code": 0, "ended": "x", "guard_remote_changed": True})[0], "guard")
        # 只有 PR 引用、只有成功 Run：都不等于验收通过
        t, r = self.reviewed_task()
        self.store.add_ref("task", t["id"], "github_pr", "26", "https://github.com/o/r/pull/26")
        with self.assertRaisesRegex(ts.EvidenceMissing, "有证据不等于验收通过"):
            self.store.complete_task(t["id"], by="user")
        self.assertEqual(self.store.task_evidence(t["id"]), {"refs": ["github_pr:26"], "runs": [r["id"]]})
        # 验收结论必须写依据、引用本任务已有的证据；执行方不能自验
        with self.assertRaisesRegex(ts.EvidenceMissing, "引用至少一项证据"):
            self.store.accept_task(t["id"], by="codex", basis="看过了", cites=[])
        with self.assertRaisesRegex(ts.EvidenceMissing, "不在任务"):
            self.store.accept_task(t["id"], by="codex", basis="看过了", cites=["github_pr:99"])
        with self.assertRaisesRegex(ts.NotAuthorized, "执行方"):
            self.store.accept_task(t["id"], by="claude", basis="我自己测过", cites=[r["id"]])
        with self.assertRaises(ts.NotAuthorized):
            self.store.accept_task(t["id"], by="system", basis="定时任务", cites=[r["id"]])
        self.assertEqual(self.store.get_task(t["id"])["verdict"], None)
        # 验收不通过 = 退回 ready；验收通过后才能完成；通过后续跑会清掉结论
        self.assertEqual(self.store.accept_task(t["id"], by="codex", basis="测试没跑", cites=[r["id"]], passed=False)["status"], "ready")
        self.assertEqual(self.store.resume_run(r["id"], by="hermes")["task_status"], "running")
        self.store.receive_receipt(r["id"], {**OK_RECEIPT, "ended": "2000-01-01T10:00:00"})
        out = self.store.accept_task(t["id"], by="codex", basis="105 项测试通过，PR #26 与回执一致", cites=[r["id"], "github_pr:26"])
        self.assertEqual((out["verdict"], self.store.get_task(t["id"])["verdict_by"]), ("passed", "codex"))
        self.store.resume_run(r["id"], by="hermes")                       # 又续跑：结论作废
        self.assertEqual(self.store.get_task(t["id"])["verdict"], None)
        self.store.receive_receipt(r["id"], {**OK_RECEIPT, "ended": "2000-01-01T10:30:00"})
        with self.assertRaises(ts.EvidenceMissing):
            self.store.complete_task(t["id"], by="hermes")
        self.store.accept_task(t["id"], by="user", basis="我看过 PR", cites=["github_pr:26"])
        done = self.store.complete_task(t["id"], by="hermes")
        self.assertEqual((done["status"], done["verdict_by"], done["cites"]), ("completed", "user", ["github_pr:26"]))
        # 常浩自己可以给自己执行的任务下结论（他不是车间）；当前 Run 还在跑时不能完成
        t2, r2 = self.reviewed_task(title="还在跑")
        self.store.accept_task(t2["id"], by="user", basis="看过", cites=[r2["id"]])
        with self.store._tx():
            self.store.conn.execute("UPDATE runs SET status='running' WHERE id=?", (r2["id"],))
        with self.assertRaisesRegex(ts.IllegalTransition, "还在 running"):
            self.store.complete_task(t2["id"], by="hermes")

    def test_r4_link_and_cancel_consistency(self):
        g = self.goal(project="digital-twin")
        self.store.sync_orders(make_dispatch(self.ledger, {
            "D20000101-1": order("running"), "D20000101-2": order("running"), "D20000101-3": order("done", receipt=OK_RECEIPT),
            "D20000101-4": order("running", project="work-desk")}))
        observed = self.store.create_task(g["id"], "未授权", by="hermes")
        with self.assertRaisesRegex(ts.IllegalTransition, "只能关联 ready 的任务"):
            self.store.link_run("D20000101-1", observed["id"], by="hermes")
        unauthorized = self.store.create_task(self.goal(authorized=False)["id"], "目标未授权", by="hermes")
        with self.assertRaises(ts.IllegalTransition):
            self.store.link_run("D20000101-1", unauthorized["id"], by="hermes")
        done_task, _ = self.reviewed_task(goal_id=g["id"], title="已验收", with_order=False)
        self.accepted(done_task["id"], self.store.current_run(done_task["id"])["id"])
        self.store.complete_task(done_task["id"], by="hermes")
        canceled = self.store.create_task(g["id"], "撤销的", by="hermes")
        self.store.cancel_task(canceled["id"], by="user")
        for bad in (done_task["id"], canceled["id"]):
            with self.assertRaisesRegex(ts.IllegalTransition, "不能再关联 Run"):
                self.store.link_run("D20000101-1", bad, by="hermes")
            with self.assertRaisesRegex(ts.IllegalTransition, "不能再关联 Run"):
                self.store.link_run("D20000101-3", bad, by="hermes")     # 已结束的 Run 也不能挂到终态任务
        reviewing, _ = self.reviewed_task(goal_id=g["id"], title="在 review", with_order=False)
        with self.assertRaises(ts.IllegalTransition):
            self.store.link_run("D20000101-1", reviewing["id"], by="hermes")
        running, _ = self.running_task(goal_id=g["id"], title="已有活动 Run", with_order=False)
        with self.assertRaisesRegex(ts.IllegalTransition, "只能关联 ready 的任务"):
            self.store.link_run("D20000101-1", running["id"], by="hermes")
        ready = self.ready_task(g["id"], title="项目对不上")
        with self.assertRaisesRegex(ts.StateError, "不能关联"):
            self.store.link_run("D20000101-4", ready["id"], by="hermes")
        self.assertEqual(self.store.link_run("D20000101-1", ready["id"], by="hermes")["task_status"], "running")
        with self.assertRaisesRegex(ts.IllegalTransition, "只能关联 ready 的任务"):
            self.store.link_run("D20000101-2", ready["id"], by="hermes")  # 不会形成两个活动 Run
        self.assertEqual(len([r for r in self.store.list_runs(ready["id"]) if r["status"] in ts.RUN_ACTIVE]), 1)
        # 已结束的 Run 可以挂到 review 的任务上作证据，但不能挂到终态任务
        self.assertEqual(self.store.link_run("D20000101-3", reviewing["id"], by="hermes")["task_status"], "review")
        # 请求取消 ≠ 已终止：Run 仍 running、带取消标记；执行层真正停下来后结果只记事件
        out = self.store.cancel_task(ready["id"], by="user", reason="方向变了")
        rid = self.store.get_run("D20000101-1")["id"]
        self.assertEqual(out["cancel_pending"], [rid])
        run = self.store.get_run(rid)
        self.assertEqual((run["status"], run["cancel_requested_by"]), ("running", "user"))
        self.assertEqual([r["id"] for r in self.store.list_runs(cancel_pending=True)], [rid])
        write_order(self.ledger, "D20000101-1", order("abandoned", updated="2000-01-01T12:00:00"))
        self.store.sync_orders()
        self.assertEqual(self.store.get_run(rid)["status"], "abandoned")
        self.assertEqual(self.store.list_runs(cancel_pending=True), [])
        self.assertEqual(self.store.events(object_id=rid, kind="run.result_not_applied")[-1]["payload"]["task_status"], "canceled")
        # 撤销目标时还在跑的工单：一并记“已请求取消”，不留无解释的运行中 Run
        t2, r2 = self.running_task(goal_id=g["id"], title="目标撤销时在跑", with_order=False)
        out = self.store.cancel_goal(g["id"], by="user", reason="整个方向不做了")
        still_running = self.store.current_run(running["id"])["id"]            # 上面那个“已有活动 Run”的任务也在这个目标下
        self.assertEqual(sorted(out["cancel_pending"]), sorted([still_running, r2["id"]]))
        for rid in out["cancel_pending"]:
            self.assertEqual(self.store.get_run(rid)["status"], "running")
            self.assertEqual(self.kinds(rid)[-1], "run.cancel_requested")
        self.assertEqual({t["status"] for t in self.store.list_tasks(g["id"])}, {"completed", "canceled"})

    def test_create_with_duplicate_idempotent_key_returns_existing(self):
        g1 = self.store.create_goal("目标", by="hermes", idem_key="feishu:om_1")
        g2 = self.store.create_goal("目标（重发）", by="hermes", idem_key="feishu:om_1")
        self.assertEqual((g1["id"], g2["id"], g2["title"]), ("G20000101-1", "G20000101-1", "目标"))
        self.assertEqual(len(self.store.list_goals()), 1)
        t1 = self.store.create_task(g1["id"], "任务", by="hermes", idem_key="feishu:om_2")
        t2 = self.store.create_task(g1["id"], "任务（重发）", by="hermes", idem_key="feishu:om_2")
        self.assertEqual((t1["id"], t2["id"]), ("T20000101-1", "T20000101-1"))
        self.assertEqual(self.store.events(kind="task.create")[0]["dup_count"], 1)
        self.assertEqual(len(self.store.list_tasks()), 1)

    def test_ledger_regressions_are_conflicts_not_applied(self):
        root = make_dispatch(self.ledger, {"D20000101-1": order("cleaned"), "D20000101-2": order("guard"),
                                           "D20000101-3": order("done", receipt=OK_RECEIPT), "D20000101-4": order("abandoned")})
        self.store.sync_orders()
        cases = {"D20000101-1": "running", "D20000101-2": "merged", "D20000101-4": "done"}   # 终态回退、护栏降级、放弃后又完成
        for no, back in cases.items():
            write_order(root, no, order(back, updated=f"2000-01-01T13:00:00+{no}"))
        stats = self.store.sync_orders()
        self.assertEqual(sorted(stats["conflicts"]), sorted(cases))
        self.assertEqual({no: self.store.get_run(no)["status"] for no in cases},
                         {"D20000101-1": "cleaned", "D20000101-2": "guard", "D20000101-4": "abandoned"})
        for no in cases:
            ev = self.store.events(object_id=self.store.get_run(no)["id"], kind="run.conflict")[-1]["payload"]
            self.assertEqual(ev["ledger"], cases[no])
        # 护栏后的续跑不自动应用；done 之后的续跑是正常的 followup；done → cleaned 是允许的跳过
        write_order(root, "D20000101-2", order("running", updated="2000-01-01T14:00:00"))
        write_order(root, "D20000101-3", order("running", updated="2000-01-01T14:00:00"))
        stats = self.store.sync_orders()
        self.assertEqual(stats["conflicts"], ["D20000101-2"])
        self.assertEqual((self.store.get_run("D20000101-2")["status"], self.store.get_run("D20000101-3")["status"]), ("guard", "running"))
        self.assertEqual(self.kinds(self.store.get_run("D20000101-3")["id"])[-1], "run.followup")
        write_order(root, "D20000101-3", order("cleaned", updated="2000-01-01T15:00:00"))
        self.store.sync_orders()
        self.assertEqual(self.store.get_run("D20000101-3")["status"], "cleaned")
        # 同一台账状态再变（updated 变了但状态没变）：只刷新证据，不算冲突
        write_order(root, "D20000101-3", order("cleaned", updated="2000-01-01T16:00:00", merged_to="abc"))
        stats = self.store.sync_orders()
        self.assertEqual((stats["updated"], stats["conflicts"]), (1, []))
        self.assertEqual(self.store.get_run("D20000101-3")["evidence"]["merged_to"], "abc")

    def test_followup_resume_rules(self):
        t, r = self.reviewed_task()
        self.assertEqual(self.store.resume_run(r["id"], by="hermes")["task_status"], "running")   # review → running
        self.assertEqual((self.store.get_run(r["id"])["status"], self.store.get_task(t["id"])["status"]), ("running", "running"))
        self.assertEqual(self.store.resume_run(r["id"], by="hermes")["applied"], False)
        self.store.receive_receipt(r["id"], FAIL_RECEIPT)
        self.assertEqual(self.store.resume_run(r["id"], by="hermes")["task_status"], "running")   # failed 续跑算一次重试
        self.assertEqual(self.store.get_task(t["id"])["retries"], 1)
        self.store.receive_receipt(r["id"], {**FAIL_RECEIPT, "ended": "2000-01-01T11:00:00"})
        self.store.resume_run(r["id"], by="hermes")
        self.store.receive_receipt(r["id"], {**FAIL_RECEIPT, "ended": "2000-01-01T12:00:00"})
        with self.assertRaisesRegex(ts.StateError, "超过上限"):                                  # 第三次：到上限，拒绝，Run 不动
            self.store.resume_run(r["id"], by="hermes")
        self.assertEqual((self.store.get_run(r["id"])["status"], self.store.get_task(t["id"])["status"]), ("failed", "blocked"))
        # guard 的续跑只有常浩能定；decision 阻塞的任务靠台账续跑解不开
        t2, r2 = self.running_task(title="护栏")
        self.store.receive_receipt(r2["id"], {**OK_RECEIPT, "guard_main_changed": True})
        with self.assertRaisesRegex(ts.NotAuthorized, "续跑要常浩定"):
            self.store.resume_run(r2["id"], by="hermes")
        self.assertEqual(self.store.resume_run(r2["id"], by="user")["task_status"], "running")
        t3, r3 = self.running_task(title="等决定")
        self.store.receive_receipt(r3["id"], {**OK_RECEIPT, "need_decision": "要不要？"})
        with self.assertRaisesRegex(ts.NotAuthorized, "不能靠续跑解开"):                          # 主动续跑先查任务：拒绝
            self.store.resume_run(r3["id"], by="hermes")
        self.assertEqual((self.store.get_run(r3["id"])["status"], self.store.get_task(t3["id"])["status"]), ("done", "blocked"))
        self.assertNotIn("run.resume_conflict", self.kinds(r3["id"]))
        done_run = self.reviewed_task(title="已完成")[1]
        self.store.update_run(done_run["id"], "merged", by="hermes")
        with self.assertRaisesRegex(ts.IllegalTransition, "不能续跑"):
            self.store.resume_run(done_run["id"], by="user")

    def test_recover_uses_ledger_before_time(self):
        root = make_dispatch(self.ledger, {"D20000101-1": order("running"), "D20000101-2": order("running")})
        self.store.sync_orders()
        t1 = self.ready_task(title="一"), self.ready_task(title="二")
        self.store.link_run("D20000101-1", t1[0]["id"], by="hermes")
        self.store.link_run("D20000101-2", t1[1]["id"], by="hermes")
        self.tick(hours=3)
        out = self.store.recover()                      # 台账说还在跑：不按时间判过期，只列 suspect
        self.assertEqual((out["ledger"], out["stale"], sorted(out["suspect"])),
                         ("ok", [], sorted([self.store.get_run("D20000101-1")["id"], self.store.get_run("D20000101-2")["id"]])))
        self.assertEqual({t["status"] for t in self.store.list_tasks()}, {"running"})
        write_order(root, "D20000101-1", order("done", updated="2000-01-01T13:00:00", receipt=OK_RECEIPT))
        write_order(root, "D20000101-2", order("failed", updated="2000-01-01T13:00:00", receipt=FAIL_RECEIPT))
        out = self.store.recover()                      # 台账给了结果：按同步规则结算
        self.assertEqual(sorted(out["settled"]), sorted([f"{self.store.get_run('D20000101-1')['id']}→done",
                                                         f"{self.store.get_run('D20000101-2')['id']}→failed"]))
        self.assertEqual((self.store.get_task(t1[0]["id"])["status"], self.store.get_task(t1[1]["id"])["status"]), ("review", "blocked"))
        # 台账读不了：不下判断
        for f in root.glob("D*/order.json"):
            f.write_text("{坏", encoding="utf-8")
        t3 = self.ready_task(title="台账坏了")
        r3 = self.store.start_run(t3["id"], "claude", by="hermes", order_no="D20000101-9")["run"]
        self.tick(hours=5)
        out = self.store.recover()
        self.assertEqual((out["ledger"], out["unknown"], out["stale"]), ("unreadable", [r3["id"]], []))
        self.assertEqual(self.store.get_task(t3["id"])["status"], "running")

    def test_receipt_whitelist(self):
        t, r = self.running_task()
        self.store.receive_receipt(r["id"], {**OK_RECEIPT, "summary": "x" * 5000, "env": {"TOKEN": "不该进库"}, "raw_stdout": "…"})
        rc = self.store.get_run(r["id"])["receipt"]
        self.assertNotIn("env", rc)
        self.assertNotIn("raw_stdout", rc)
        self.assertEqual(len(rc["summary"]), ts.SUMMARY_CLIP)
        self.assertEqual(ts.clip_receipt("不是对象"), {})


class FinalFixTests(Base):
    """2026-10-29 第二轮评审的收口项 F1～F4 与 B3 审查接口，每项先在返修前的模块（2e496f1）上确认会失败。"""

    def test_f1_ledger_conflict_does_not_overwrite_confirmed_evidence(self):
        root = make_dispatch(self.ledger, {
            "D20000101-1": order("cleaned", merged_to="1234567abcd", pr=13, receipt=OK_RECEIPT),
            "D20000101-2": order("guard", receipt={**OK_RECEIPT, "guard_main_changed": True}),
            "D20000101-3": order("running")})
        self.store.sync_orders()
        t = self.ready_task()
        self.store.link_run("D20000101-3", t["id"], by="hermes")
        write_order(root, "D20000101-3", order("done", updated="2000-01-01T11:00:00", receipt=OK_RECEIPT))
        self.store.sync_orders()
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        keep = ("status", "receipt", "evidence", "session_id", "ended_at")
        before = {no: self.store.get_run(no) for no in ("D20000101-1", "D20000101-2", "D20000101-3")}
        self.assertEqual((before["D20000101-1"]["session_id"], before["D20000101-1"]["evidence"]["merged_to"]), ("sess-1", "1234567abcd"))
        # 终态回退（cleaned → running）、护栏后续跑（guard → running）、结果回到活动态（done → queued），
        # 台账还带来了不同的回执和证据：一律冲突，已确认的回执、证据、会话一个字段都不改，任务也不动
        bad = {**OK_RECEIPT, "session_id": "sess-9", "head_after": "fff", "summary": "冲突的回执", "committed_files": ["x"]}
        write_order(root, "D20000101-1", order("running", updated="2000-01-01T13:00:00", merged_to="bad", pr=99, receipt=bad))
        write_order(root, "D20000101-2", order("running", updated="2000-01-01T13:00:00", receipt={**OK_RECEIPT, "session_id": "sess-8"}))
        write_order(root, "D20000101-3", order("queued", updated="2000-01-01T13:00:00", receipt=bad))
        stats = self.store.sync_orders()
        self.assertEqual(sorted(stats["conflicts"]), ["D20000101-1", "D20000101-2", "D20000101-3"])
        self.assertEqual(stats["updated"], 0)
        for no, was in before.items():
            now = self.store.get_run(no)
            self.assertEqual({k: now[k] for k in keep}, {k: was[k] for k in keep}, no)
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        # 冲突内容独立留痕：事件里带台账状态和与本地不一样的回执 / 证据字段，没变的字段不进摘要
        ev = self.store.events(object_id=before["D20000101-1"]["id"], kind="run.conflict")[-1]["payload"]
        self.assertEqual((ev["local"], ev["ledger"], ev["order_no"]), ("cleaned", "running", "D20000101-1"))
        self.assertEqual((ev["ledger_receipt"]["session_id"], ev["ledger_receipt"]["summary"]), ("sess-9", "冲突的回执"))
        self.assertEqual((ev["ledger_evidence"]["merged_to"], ev["ledger_evidence"]["pr"]), ("bad", 99))
        self.assertNotIn("exit_code", ev["ledger_receipt"])
        ev2 = self.store.events(object_id=before["D20000101-2"]["id"], kind="run.conflict")[-1]["payload"]
        self.assertEqual((ev2["local"], ev2["ledger"], ev2["ledger_receipt"]["session_id"]), ("guard", "running", "sess-8"))
        self.assertIn("要常浩定", ev2["note"])
        # 同一状态下台账内容变了：不是冲突，照常刷新证据
        write_order(root, "D20000101-1", order("cleaned", updated="2000-01-01T14:00:00", merged_to="1234567abcd", pr=13,
                                                receipt={**OK_RECEIPT, "summary": "补充说明"}))
        stats = self.store.sync_orders()
        self.assertEqual((stats["updated"], stats["conflicts"]), (1, []))
        self.assertEqual(self.store.get_run("D20000101-1")["receipt"]["summary"], "补充说明")

    def test_f3_history_run_linked_later_does_not_take_over_current_run(self):
        t, b = self.running_task(with_order=False)                 # 任务正由 B 执行
        old = {**OK_RECEIPT, "session_id": "sess-old", "started": "2000-01-01T07:00:00", "ended": "2000-01-01T07:30:00"}
        root = make_dispatch(self.ledger, {"D20000101-5": order("done", created="2000-01-01T07:00:00",
                                                                updated="2000-01-01T07:30:00", receipt=old)})
        self.store.sync_orders()                                   # 更早跑完的旧工单，后导入
        self.assertEqual(self.store.link_run("D20000101-5", t["id"], by="hermes")["task_status"], "running")   # 后关联：只作证据
        a = self.store.get_run("D20000101-5")
        self.assertEqual((a["task_id"], a["started_at"]), (t["id"], "2000-01-01T07:00:00"))
        self.assertEqual([r["id"] for r in self.store.list_runs(t["id"])], [b["id"], a["id"]])   # 插入顺序上 A 在后
        self.assertEqual(self.store.current_run(t["id"])["id"], b["id"])                          # 归属仍是 B
        self.assertEqual(self.store.get_task(t["id"])["current_run_id"], b["id"])
        # 旧工单的迟到结果（台账把它改成 abandoned）：只记 run.late_result，任务仍由 B 推进
        write_order(root, "D20000101-5", order("abandoned", updated="2000-01-01T13:00:00", receipt=old))
        self.store.sync_orders()
        self.assertEqual(self.store.get_run(a["id"])["status"], "abandoned")
        late = self.store.events(object_id=a["id"], kind="run.late_result")[-1]["payload"]
        self.assertEqual((late["current_run"], late["status"]), (b["id"], "abandoned"))
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")
        self.assertNotIn("task.blocked", self.kinds(t["id"]))
        # B 跑完才结算；验收时“执行方”按当前 Run 判：codex 可以给 claude 跑的任务下结论
        self.store.receive_receipt(b["id"], OK_RECEIPT)
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.assertEqual(self.accepted(t["id"], b["id"])["verdict"], "passed")
        # 只有证据 Run 的任务（没开过跑）：字段为空，回退到最近开始的那个
        t2 = self.ready_task(title="只有证据")
        make_dispatch(root, {"D20000101-6": order("done", receipt={**OK_RECEIPT, "started": "2000-01-01T06:00:00"}),
                             "D20000101-7": order("done", receipt={**OK_RECEIPT, "started": "2000-01-01T08:00:00"}),
                             "D20000101-8": order("running")})
        self.store.sync_orders()
        self.store.link_run("D20000101-7", t2["id"], by="hermes")
        self.store.link_run("D20000101-6", t2["id"], by="hermes")
        self.assertEqual(self.store.get_task(t2["id"])["current_run_id"], None)
        self.assertEqual(self.store.current_run(t2["id"])["order_no"], "D20000101-7")
        # 关联进行中的 Run 才改归属
        self.assertEqual(self.store.link_run("D20000101-8", t2["id"], by="hermes")["task_status"], "running")
        self.assertEqual(self.store.get_task(t2["id"])["current_run_id"], self.store.get_run("D20000101-8")["id"])
        self.assertEqual(self.store.current_run(t2["id"])["order_no"], "D20000101-8")

    def test_f2_active_resume_checks_task_before_touching_run(self):
        # decision 阻塞 + Hermes 主动续跑：拒绝，Run 仍 done、没有 followup 事件，任务不动
        t, r = self.running_task()
        self.store.receive_receipt(r["id"], {**OK_RECEIPT, "need_decision": "要不要合并？"})
        before = (self.store.get_run(r["id"]), self.store.get_task(t["id"]), self.kinds(r["id"]), self.kinds(t["id"]))
        for who in ("hermes", "claude", "codex", "system"):
            with self.assertRaisesRegex(ts.NotAuthorized, "不能靠续跑解开"):
                self.store.resume_run(r["id"], by=who)
        self.assertEqual((self.store.get_run(r["id"]), self.store.get_task(t["id"]), self.kinds(r["id"]), self.kinds(t["id"])), before)
        self.assertEqual(self.store.get_run(r["id"])["status"], "done")
        out = self.store.ingest_event("system", "cron.resume", "cron:resume:1", "hermes",
                                      handler=lambda s: s.resume_run(r["id"], by="hermes"))   # 经事件入口：拒绝留痕，状态不变
        self.assertEqual(out["applied"], False)
        self.assertIn("不能靠续跑解开", out["rejected"])
        self.assertEqual(self.store.get_run(r["id"])["status"], "done")
        self.assertEqual(self.store.resume_run(r["id"], by="user")["task_status"], "running")   # 常浩本人可以
        # 已取消、已完成任务的 Run 续跑：拒绝，Run 不动
        t2, r2 = self.running_task(title="撤销的")
        self.store.receive_receipt(r2["id"], FAIL_RECEIPT)
        self.store.cancel_task(t2["id"], by="user", reason="不做了")
        with self.assertRaisesRegex(ts.IllegalTransition, "已 canceled"):
            self.store.resume_run(r2["id"], by="user")
        self.assertEqual(self.store.get_run(r2["id"])["status"], "failed")
        t3, r3 = self.reviewed_task(title="完成的")
        self.accepted(t3["id"], r3["id"])
        self.store.complete_task(t3["id"], by="hermes")
        with self.assertRaisesRegex(ts.IllegalTransition, "已 completed"):
            self.store.resume_run(r3["id"], by="hermes")
        self.assertEqual(self.store.get_run(r3["id"])["status"], "done")
        # 旧 Run（不是当前 Run）主动续跑：拒绝
        t4, a = self.running_task(title="换了 Run")
        self.store.receive_receipt(a["id"], FAIL_RECEIPT)
        self.store.retry_task(t4["id"], by="hermes")
        b = self.store.start_run(t4["id"], "claude", by="hermes")["run"]
        with self.assertRaisesRegex(ts.IllegalTransition, "不是任务 .* 的当前 Run"):
            self.store.resume_run(a["id"], by="hermes")
        self.assertEqual((self.store.get_run(a["id"])["status"], self.store.get_task(t4["id"])["status"],
                          self.store.current_run(t4["id"])["id"]), ("failed", "running", b["id"]))
        # 台账观察到的续跑（decision 阻塞的任务）：执行事实照记（Run → running），任务不动，记冲突并计入 conflicts
        root = make_dispatch(self.ledger, {"D20000101-9": order("running")})
        self.store.sync_orders()
        t5 = self.ready_task(title="台账续跑")
        self.store.link_run("D20000101-9", t5["id"], by="hermes")
        write_order(root, "D20000101-9", order("done", updated="2000-01-01T11:00:00",
                                                receipt={**OK_RECEIPT, "need_decision": "要不要？"}))
        self.store.sync_orders()
        self.assertEqual(self.store.get_task(t5["id"])["blocked_kind"], "decision")
        write_order(root, "D20000101-9", order("running", updated="2000-01-01T12:00:00"))
        stats = self.store.sync_orders()
        self.assertEqual((stats["conflicts"], stats["updated"]), (["D20000101-9"], 1))
        rid = self.store.get_run("D20000101-9")["id"]
        self.assertEqual(self.store.get_run(rid)["status"], "running")
        task5 = self.store.get_task(t5["id"])
        self.assertEqual((task5["status"], task5["blocked_kind"]), ("blocked", "decision"))
        self.assertEqual(self.kinds(rid)[-2:], ["run.followup", "run.resume_conflict"])
        ev = self.store.events(object_id=rid, kind="run.resume_conflict")[-1]["payload"]
        self.assertEqual((ev["task"], ev["task_status"], ev["blocked_kind"]), (t5["id"], "blocked", "decision"))
        self.assertIn("不能靠续跑解开", ev["why"])
        # 常浩解开后，台账里的工单跑完照常结算
        self.store.unblock_task(t5["id"], by="user", note="继续")
        self.assertEqual(self.store.get_task(t5["id"])["status"], "ready")
        self.assertEqual(self.store.resume_run(rid, by="hermes")["applied"], False)              # Run 已经在 running
        write_order(root, "D20000101-9", order("done", updated="2000-01-01T13:00:00", receipt=OK_RECEIPT))
        self.store.sync_orders()
        self.assertEqual(self.store.get_task(t5["id"])["status"], "ready")                      # 任务不在 running：结果只记事件
        self.assertEqual(self.kinds(rid)[-1], "run.result_not_applied")

    def test_f4_owner_open_id_comes_from_local_config_not_source(self):
        self.assertNotRegex(MODULE.read_text(encoding="utf-8"), r"ou_[0-9a-f]{16,}")   # 源码里不再写死个人身份标识
        self.assertFalse(hasattr(ts, "OWNER_OPEN_ID"))
        f = self.root / "owner_open_id"
        with mock.patch.dict(os.environ, {"DT_OWNER_FILE": str(f)}):
            os.environ.pop("DT_OWNER_OPEN_ID", None)
            self.assertEqual(ts.owner_open_id(), None)                        # 两处都没配置
            f.write_text("ou_test_owner\n", encoding="utf-8")
            with self.assertRaisesRegex(ts.NotAuthorized, "600"):            # 文件权限不是 600：不认
                ts.owner_open_id()
            f.chmod(0o600)
            self.assertEqual(ts.owner_open_id(), "ou_test_owner")
            f.write_text("\n  ou_test_owner  \n第二行忽略\n", encoding="utf-8")
            self.assertEqual(ts.owner_open_id(), "ou_test_owner")           # 只取第一个非空行
            f.write_text("", encoding="utf-8")
            self.assertEqual(ts.owner_open_id(), None)
            os.environ["DT_OWNER_OPEN_ID"] = "ou_env_owner"
            self.assertEqual(ts.owner_open_id(), "ou_env_owner")            # 环境变量优先
        # 命令行：没配置时 --by user 一律拒绝并提示怎么配；配好后只认配置里的那个——而且身份只从飞书回读来（B2.1 T03）
        env = {k: v for k, v in os.environ.items() if k not in ("DT_OWNER_OPEN_ID", "HERMES_SESSION_MESSAGE_ID")}
        env.update(DT_STATE_DB=str(self.db), DT_DISPATCH=str(self.root / "no-dispatch"), DT_OWNER_FILE=str(self.root / "nowhere"))
        env = self.lark_env(env, {"om_testowner0001": "ou_test_owner", "om_testowner0002": "ou_test_owner",
                                  "om_envowner00001": "ou_env_owner", "om_someoneelse01": "ou_someone_else"})
        self.store.close()
        new = ("goal-new", "--title", "x", "--by", "user", "--status", "authorized")
        out = self.cli(env, *new, "--message-id", "om_testowner0001", ok=False)
        self.assertEqual(out["error"], "NotAuthorized")
        self.assertIn("DT_OWNER_OPEN_ID", out["msg"])
        self.assertIn("owner_open_id", out["msg"])
        self.assertIn("只有常浩本人能授权", self.cli(env, "goal-new", "--title", "x", "--by", "hermes", "--status", "authorized", ok=False)["msg"])
        f.write_text("ou_test_owner\n", encoding="utf-8")
        f.chmod(0o644)
        env["DT_OWNER_FILE"] = str(f)
        self.assertIn("600", self.cli(env, *new, "--message-id", "om_testowner0001", ok=False)["msg"])
        f.chmod(0o600)
        self.assertEqual(self.cli(env, *new, "--message-id", "om_testowner0001")["goal"]["status"], "authorized")
        self.assertEqual(self.cli(env, *new, "--message-id", "om_someoneelse01", ok=False)["error"], "NotAuthorized")
        no_mid = self.cli(env, *new, ok=False)                                                      # 不指向消息也不行
        self.assertEqual(no_mid["error"], "NotAuthorized")
        self.assertIn("--message-id", no_mid["msg"])
        env["DT_OWNER_OPEN_ID"] = "ou_env_owner"
        self.assertEqual(self.cli(env, *new, "--message-id", "om_envowner00001")["goal"]["status"], "authorized")   # 环境变量优先
        self.assertEqual(self.cli(env, *new, "--message-id", "om_testowner0002", ok=False)["error"], "NotAuthorized")
        with ts.Store(self.db) as s:
            self.assertEqual(len(s.list_goals()), 2)

    def test_b3_review_run_is_evidence_not_execution(self):
        t, r = self.reviewed_task()                                  # claude 跑完，任务在 review 等验收
        with self.assertRaisesRegex(ts.IllegalTransition, "只有 ready 的任务能开始执行"):
            self.store.start_run(t["id"], "codex", by="hermes")      # 执行 Run 仍只接受 ready：不会把待验收任务退回开发态
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        with self.assertRaisesRegex(ts.NotAuthorized, "执行方"):
            self.store.start_run(t["id"], "claude", by="hermes", purpose="review")   # 审查方不能是执行方
        with self.assertRaisesRegex(ts.IllegalTransition, "只有 review"):
            self.store.start_run(self.ready_task(title="还没跑")["id"], "codex", by="hermes", purpose="review")
        with self.assertRaises(ts.StateError):
            self.store.start_run(t["id"], "codex", by="hermes", purpose="audit")
        rv = self.store.start_run(t["id"], "codex", by="hermes", order_no="D20000101-9", purpose="review")["run"]
        self.assertEqual((rv["purpose"], rv["task_id"], rv["agent"], rv["status"]), ("review", t["id"], "codex", "running"))
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")              # 任务状态不变
        self.assertEqual(self.store.current_run(t["id"])["id"], r["id"])                 # 不成为当前 Run
        self.assertEqual(self.store.events(object_id=rv["id"], kind="run.start")[0]["payload"]["purpose"], "review")
        with self.assertRaisesRegex(ts.IllegalTransition, "已有进行中的 Run"):
            self.store.start_run(t["id"], "codex", by="hermes", purpose="review")      # 一任务一活动 Run 的规则不变
        # 审查 Run 的回执只作证据：任务不结算、不阻塞、不进 review 之外的状态
        out = self.store.receive_receipt(rv["id"], {**OK_RECEIPT, "session_id": "sess-review", "summary": "核对了测试与 PR"})
        self.assertEqual((out["result"]["status"], out["result"]["task_status"]), ("done", "evidence"))
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.assertEqual(self.kinds(rv["id"])[-3:], ["run.done", "run.review_result", "run.finish"])
        self.assertNotIn("run.late_result", self.kinds(rv["id"]))
        self.assertEqual(self.store.task_evidence(t["id"])["runs"], [r["id"], rv["id"]])
        # 结论由审查方引用审查 Run 写；执行方仍不能自验；完成不受审查 Run 影响
        with self.assertRaisesRegex(ts.NotAuthorized, "执行方"):
            self.store.accept_task(t["id"], by="claude", basis="自己看过", cites=[rv["id"]])
        self.assertEqual(self.store.accept_task(t["id"], by="codex", basis="审查 Run 核对通过", cites=[rv["id"]])["verdict"], "passed")
        self.assertEqual(self.store.complete_task(t["id"], by="hermes")["cites"], [rv["id"]])
        # 审查 Run 失败或续跑也只是证据：任务不转阻塞、不动
        t2, r2 = self.reviewed_task(title="审查失败")
        rv2 = self.store.start_run(t2["id"], "codex", by="hermes", purpose="review")["run"]
        self.store.receive_receipt(rv2["id"], FAIL_RECEIPT)
        self.assertEqual((self.store.get_run(rv2["id"])["status"], self.store.get_task(t2["id"])["status"]), ("failed", "review"))
        self.assertEqual(self.store.resume_run(rv2["id"], by="hermes")["task_status"], None)
        self.assertEqual((self.store.get_run(rv2["id"])["status"], self.store.get_task(t2["id"])["status"]), ("running", "review"))
        self.assertEqual(self.store.get_task(t2["id"])["retries"], 0)


class MoreTests(Base):
    """B1.3 其余必处理情形、备份恢复、权限、命令行。"""

    def test_illegal_transitions(self):
        t = self.ready_task()
        g = self.store.get_goal(t["goal_id"])
        with self.assertRaises(ts.IllegalTransition):
            self.store.complete_task(t["id"], by="user")   # ready → completed 不行
        with self.assertRaises(ts.IllegalTransition):
            self.store.unblock_task(t["id"], by="user")    # 没阻塞
        with self.assertRaises(ts.IllegalTransition):
            self.store.rework_task(t["id"], by="user", note="x")
        with self.assertRaises(ts.IllegalTransition):
            self.store.accept_task(t["id"], by="user", basis="x", cites=["a"])
        self.store.cancel_task(t["id"], by="user")
        with self.assertRaises(ts.IllegalTransition):
            self.store.set_ready(t["id"], by="user")       # 终态不能再动
        self.assertEqual(self.store.cancel_task(t["id"], by="user")["applied"], False)
        self.assertEqual(self.store.authorize_goal(g["id"], by="user")["applied"], False)
        with self.assertRaises(ts.IllegalTransition):
            self.store.cancel_goal(self.completed_goal()["id"], by="user")
        with self.assertRaises(ts.StateError):
            self.store.create_goal("状态不对", by="user", status="completed")
        with self.assertRaises(ts.StateError):
            self.store.update_task(t["id"], by="user", status="ready")
        with self.assertRaises(ts.StateError):
            self.store.start_run(self.ready_task(title="y")["id"], "claude", by="hermes", status="done")

    def completed_goal(self):
        t, r = self.reviewed_task(title="z")
        self.accepted(t["id"], r["id"])
        self.store.complete_task(t["id"], by="hermes")
        self.store.complete_goal(t["goal_id"], by="user")
        return self.store.get_goal(t["goal_id"])

    def test_cancel_rules(self):
        g = self.goal()
        t = self.store.create_task(g["id"], "刚发现的", by="hermes")
        self.assertEqual(self.store.cancel_task(t["id"], by="hermes")["status"], "canceled")
        t2 = self.ready_task(g["id"], title="已授权的")
        with self.assertRaisesRegex(ts.NotAuthorized, "只有常浩能撤销"):
            self.store.cancel_task(t2["id"], by="hermes")
        r = self.store.start_run(t2["id"], "claude", by="hermes")["run"]
        self.assertEqual(self.store.cancel_task(t2["id"], by="user", reason="方向变了")["cancel_pending"], [r["id"]])
        self.assertEqual(self.store.get_run(r["id"])["status"], "running")   # 只是请求取消，进程可能还在跑
        with self.assertRaises(ts.NotAuthorized):
            self.store.cancel_goal(g["id"], by="hermes")

    def test_rework_and_update(self):
        t, r = self.reviewed_task()
        self.assertEqual(self.store.rework_task(t["id"], by="user", note="测试没过")["status"], "ready")
        self.assertEqual(self.store.update_task(t["id"], by="hermes", next_step="补测试")["next_step"], "补测试")
        r2 = self.store.start_run(t["id"], "claude", by="hermes")["run"]
        with self.assertRaises(ts.IllegalTransition):
            self.store.heartbeat(r["id"])   # 旧 Run 已结束
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
        self.assertEqual(self.store.backup().name, backup.stem + "-2.db")   # 同一秒再备份不覆盖
        self.store.receive_receipt(r["id"], OK_RECEIPT)
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.store.restore(backup)
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")
        self.assertEqual(self.store.version(), ts.SCHEMA_VERSION)
        with self.assertRaises(ts.NotFound):
            self.store.restore(self.root / "nope.db")
        junk = self.root / "junk.db"
        junk.write_text("不是 SQLite", encoding="utf-8")
        with self.assertRaisesRegex(ts.StateError, "不是可用的状态库"):
            self.store.restore(junk)
        other = self.root / "other.db"
        import sqlite3
        sqlite3.connect(str(other)).execute("CREATE TABLE x(y)").connection.close()
        with self.assertRaisesRegex(ts.StateError, "没有 schema_version"):
            self.store.restore(other)
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")   # 失败的恢复不改当前库
        newer = self.root / "newer.db"
        with ts.Store(newer) as s:
            with s._tx():
                s.conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
        with self.assertRaisesRegex(ts.StateError, "比程序支持的"):
            ts.Store(newer).open()
        with self.assertRaisesRegex(ts.StateError, "比程序支持的"):
            self.store.restore(newer)

    @unittest.skipUnless(os.name == "posix", "只在 POSIX 上查权限")
    def test_file_permissions(self):
        self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.db.parent.stat().st_mode), 0o700)

    def test_cli_roundtrip(self):
        owner = self.root / "owner_open_id"                       # 常浩的 open_id 从本机受控配置来，测试用临时文件
        owner.write_text("ou_test_owner\n", encoding="utf-8")
        owner.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if k not in ("DT_OWNER_OPEN_ID", "HERMES_SESSION_MESSAGE_ID")}
        env.update(DT_STATE_DB=str(self.db), DT_DISPATCH=str(self.root / "no-dispatch"), DT_OWNER_FILE=str(owner))
        env = self.lark_env(env, {"om_cli000001": "ou_test_owner", "om_cli000002": "ou_test_owner", "om_cli000003": "ou_test_owner",
                                  "om_other00001": "ou_someone_else"})

        def cli(*args, ok=True):
            return self.cli(env, *args, ok=ok)

        self.store.close()
        self.assertEqual(cli("init")["schema_version"], 2)
        # --by user 必须指向常浩本人最近在私聊里发的一条飞书消息（回读核实）；角色名和参数都不是身份
        no_mid = cli("goal-new", "--title", "x", "--by", "user", "--status", "authorized", ok=False)
        self.assertEqual(no_mid["error"], "NotAuthorized")
        self.assertIn("--message-id", no_mid["msg"])
        self.assertEqual(cli("goal-new", "--title", "x", "--by", "user", "--message-id", "om_other00001", ok=False)["error"], "NotAuthorized")
        self.assertEqual(cli("goal-new", "--title", "x", "--by", "user", "--message-id", "om_nobody00001", ok=False)["error"], "NotAuthorized")
        self.assertIn("只有常浩本人能授权", cli("goal-new", "--title", "x", "--by", "hermes", "--status", "authorized", ok=False)["msg"])
        me = ["--message-id", "om_cli000001"]
        g = cli("goal-new", "--title", "命令行目标", "--by", "user", *me, "--status", "authorized")["goal"]
        # 一条消息只换一次动作；命令没成功时占用退回，同一条消息还能用
        replay = cli("task-new", "--goal", g["id"], "--title", "重放", "--by", "user", *me, ok=False)
        self.assertEqual(replay["error"], "NotAuthorized")
        self.assertIn("已经用过", replay["msg"])
        self.assertEqual(cli("goal-authorize", "G19990101-9", "--by", "user", "--message-id", "om_cli000002", ok=False)["error"], "NotFound")
        env["HERMES_SESSION_MESSAGE_ID"] = "om_cli000003"             # Hermes 会话里的指针：可省略 --message-id，真值仍从飞书回读
        t = cli("task-new", "--goal", g["id"], "--title", "命令行任务", "--by", "user", "--acceptance", "能跑")["task"]
        env.pop("HERMES_SESSION_MESSAGE_ID")
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
        self.assertIn("还没有通过的验收结论", cli("task-complete", t["id"], "--by", "hermes", ok=False)["msg"])
        self.assertIn("执行方", cli("task-accept", t["id"], "--by", "claude", "--basis", "自测", "--cites", r["run"]["id"], ok=False)["msg"])
        rv = cli("run-start", "--task", t["id"], "--agent", "codex", "--by", "hermes", "--purpose", "review")   # B3：审查 Run
        self.assertIn("审查", rv["msg"])
        self.assertEqual(cli("show", t["id"])["object"]["status"], "review")
        self.assertEqual(cli("run-receipt", rv["run"]["id"], "--receipt", str(receipt))["result"]["task_status"], "evidence")
        self.assertEqual(cli("task-accept", t["id"], "--by", "codex", "--basis", "核对过", "--cites", f"{rv['run']['id']},github_pr:26")["verdict"], "passed")
        self.assertEqual(cli("task-complete", t["id"], "--by", "hermes")["status"], "completed")
        self.assertEqual(cli("show", t["id"])["object"]["verdict_by"], "codex")
        self.assertEqual(cli("show", "D20000101-7")["object"]["status"], "done")
        self.assertEqual(len(cli("show", t["id"])["refs"]), 1)
        self.assertIn("没有这个对象", cli("show", "T19990101-1", ok=False)["msg"])
        self.assertEqual(len(cli("list")["tasks"]), 1)
        self.assertGreater(len(cli("events", t["id"])["events"]), 3)
        self.assertEqual(cli("goal-complete", g["id"], "--by", "user", "--message-id", "om_cli000002")["status"], "completed")
        q = cli("queue")
        self.assertEqual((q["msg"], q["counts"]["review"], q["ledger"]), ("没有待推进的对象", 0, "missing"))
        self.assertEqual(cli("reconcile")["ledger"], "missing")
        rec = cli("recover")
        self.assertEqual((rec["msg"], rec["ledger"], rec["synced"]["ledger"]), ("恢复完成（台账 missing）：没有需要处理的", "missing", "missing"))
        self.assertIn("--yes", cli("restore", "--from", str(receipt), ok=False)["msg"])
        bak = cli("backup", "--to", str(self.root / "b.db"))
        self.assertIn("b.db", bak["msg"])
        self.assertEqual(cli("restore", "--from", str(self.root / "b.db"), "--yes")["goals"], {"completed": 1})
        with ts.Store(self.db) as s:
            self.assertEqual(s.get_task(t["id"])["status"], "completed")
            self.assertEqual(s.get_goal(g["id"])["status"], "completed")


class IdentityStoreTests(Base):
    """B2.1 T03：飞书消息占用登记与 v1 → v2 迁移。"""

    def test_v1_db_migrates_to_v2_with_identity_uses(self):
        self.store.close()
        self.db.unlink()
        conn = sqlite3.connect(str(self.db))
        conn.executescript(ts.MIGRATIONS[0])
        conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        conn.commit()
        conn.close()
        with self.open_store() as s:
            self.assertEqual(s.version(), 2)
            self.assertTrue(s.claim_message("om_a", "test")["claimed"])

    def test_claim_once_release_then_reuse(self):
        s = self.store
        s.claim_message("om_x", "goal.authorize")
        with self.assertRaisesRegex(ts.NotAuthorized, "已经用过"):
            s.claim_message("om_x", "task.create")
        self.assertEqual(s.release_message("om_x"), 1)
        self.assertTrue(s.claim_message("om_x")["claimed"])
        self.assertEqual(s.release_message("om_never"), 0)


class QueueTests(Base):
    """B2.1 T05：调度消费查询只读分桶。"""

    def test_buckets(self):
        self.ledger.mkdir()                                                   # 台账存在但空：有工单号的 Run 查不到
        g = self.goal()
        a = self.ready_task(g["id"], title="A 可派")
        b = self.store.create_task(g["id"], "B 等 A", by="hermes", acceptance="x", depends_on=[a["id"]])
        self.store.set_ready(b["id"], by="hermes")                             # 依赖没完成 → blocked(dependency)
        d, d_run = self.running_task(goal_id=g["id"], title="D 等决定")
        self.store.receive_receipt(d_run["id"], {**OK_RECEIPT, "need_decision": "删不删旧表"})
        e = self.ready_task(g["id"], title="E 等外部")
        self.store.block_task(e["id"], by="hermes", kind="external", reason="等 GitHub 恢复")
        f, f_run = self.running_task(goal_id=g["id"], title="F 失败一次")
        self.store.receive_receipt(f_run["id"], FAIL_RECEIPT)
        h, h_run = self.reviewed_task(goal_id=g["id"], title="H 待验收")
        k, k_run = self.running_task(goal_id=g["id"], title="K 在跑")        # 工单在台账里找不到 → unsafe
        c, c_run = self.running_task(goal_id=g["id"], title="C 撤销中")
        self.store.cancel_task(c["id"], by="user", reason="不要了")
        g2 = self.goal()
        stuck = self.ready_task(g2["id"], title="G 失败到上限")
        for i in range(3):
            r = self.store.start_run(stuck["id"], "claude", by="hermes", order_no=f"D20000101-9{i}")["run"]
            self.store.receive_receipt(r["id"], FAIL_RECEIPT)
            if i < 2:
                self.store.retry_task(stuck["id"], by="hermes")
        self.store.check_ref("task", a["id"], "github_pr", "26", probe=lambda kind, ref: None)   # 外部来源不可达
        write_order(self.ledger, "D20000101-77", order("cleaned", OK_RECEIPT, merged_to="main"))
        self.store.sync_orders()
        write_order(self.ledger, "D20000101-77", order("running", OK_RECEIPT))
        self.assertEqual(self.store.sync_orders()["conflicts"], ["D20000101-77"])

        q = self.store.queue()
        ids = {k: [x["id"] for x in q[k]] for k in ts.QUEUE_BUCKETS}
        self.assertEqual(ids["ready"], [a["id"]])
        self.assertEqual(ids["waiting_deps"], [b["id"]])
        self.assertEqual(q["waiting_deps"][0]["depends_on"], [a["id"]])
        self.assertEqual(ids["blocked_decision"], [d["id"]])
        self.assertEqual(ids["blocked_external"], [e["id"]])
        self.assertEqual(ids["blocked_technical"], [f["id"], stuck["id"]])
        self.assertEqual([(x["retryable"], x["needs_human"]) for x in q["blocked_technical"]], [(True, False), (False, True)])
        self.assertEqual(ids["review"], [h["id"]])
        self.assertEqual((q["review"][0]["has_receipt"], q["review"][0]["verdict"]), (True, None))
        self.assertEqual(ids["cancel_pending"], [c_run["id"]])
        self.assertEqual(ids["unsafe"], [a["id"], k["id"], self.store.get_run("D20000101-77")["id"]])
        self.assertIn("不可达", q["unsafe"][0]["why"])
        self.assertIn("找不到", q["unsafe"][1]["why"])
        self.assertEqual((q["unsafe"][2]["why"], q["unsafe"][2]["order_no"]), ("台账冲突没有处理：run.conflict", "D20000101-77"))
        self.assertEqual(q["ledger"], "ok")
        self.assertEqual(self.store.summary()["tasks"]["running"], 1)        # 查询不改状态

    def test_ledger_missing_marks_running_task_unsafe(self):
        t, r = self.running_task()
        q = self.store.queue()
        self.assertEqual((q["ledger"], [x["id"] for x in q["unsafe"]]), ("missing", [t["id"]]))
        self.assertIn("missing", q["unsafe"][0]["why"])
        t2, r2 = self.running_task(with_order=False)
        self.assertEqual([x["id"] for x in self.store.queue()["unsafe"]], [t["id"]])   # 没工单号的 Run 不查台账


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
