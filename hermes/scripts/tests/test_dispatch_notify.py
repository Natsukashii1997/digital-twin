"""隔离自测：派单台账写入后的状态通知钩子（dispatch.py notify_state，B2.1 T04）与定时对账兜底。

和 test_dispatch_followup 一样：importlib 加载 dispatch.py，把 ROOT / WT_ROOT / WORKER / ROSTER / NOTIFY_LOG 换成临时路径，
subprocess.run 由假 worker 接管；状态库是临时目录里的 twin_state 库。不碰 ~/.hermes、不碰真实工单与真实状态库。
覆盖：默认关闭什么都不碰；开着但库不存在绝不建库；库存在时单单同步；通知抛错不影响派单、stdout 仍是纯 JSON；
重复通知幂等；关掉通知时漏掉的变化由 reconcile 补上；need_decision 的“先解阻塞再续跑”顺序。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_dispatch_notify.py -v
"""
import contextlib
import datetime as dt
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
import twin_state as T  # noqa: E402

SPEC = importlib.util.spec_from_file_location("dispatch", SCRIPTS / "dispatch.py")
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)

TZ = dt.timezone(dt.timedelta(hours=8))
T0 = dt.datetime(2000, 1, 1, 9, 0, tzinfo=TZ)
OK_RECEIPT = {"exit_code": 0, "timed_out": False, "worker_error": False, "session_id": "sess-1",
              "started": "2000-01-01T09:00:00", "ended": "2000-01-01T09:30:00", "branch": "hermes/D20000101-1",
              "head_before": "aaa", "head_after": "bbb", "committed_files": [], "uncommitted": [],
              "guard_main_changed": False, "guard_remote_changed": False, "need_decision": None, "summary": "好了"}


class NotifyBase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-notify-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ledger = self.root / "dispatch"
        self.db = self.root / "state" / "twin-state.db"
        self.log = self.root / "state-notify.log"
        self.no = "D20000101-1"
        self.order_dir = self.ledger / self.no
        self.out = self.order_dir / "out"
        self.out.mkdir(parents=True)
        self.order_file = self.order_dir / "order.json"
        self.order_file.write_text(json.dumps({"no": self.no, "project": "fixture", "tool": "codex", "mode": "write",
                                               "status": "done", "cwd": str(self.root), "created": "2000-01-01T08:00:00"}))
        (self.order_dir / "task.md").write_text("虚构任务", encoding="utf-8")
        (self.out / "receipt.json").write_text(json.dumps(OK_RECEIPT), encoding="utf-8")
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        for name, value in {"ROOT": self.ledger, "WT_ROOT": self.root / "worktrees", "WORKER": self.root / "fake_worker.py",
                            "ROSTER": self.root / "roster.json", "NOTIFY_LOG": self.log}.items():
            self.patches.enter_context(patch.object(dispatch, name, value))
        env = {k: v for k, v in os.environ.items() if not k.startswith("DT_")}
        env["DT_STATE_DB"] = str(self.db)
        self.patches.enter_context(patch.dict(os.environ, env, clear=True))
        self.worker_calls = []
        self.patches.enter_context(patch.object(dispatch.subprocess, "run", side_effect=self.fake_worker))

    def fake_worker(self, cmd, **kwargs):
        self.assertEqual(cmd[1], str(self.root / "fake_worker.py"))
        self.worker_calls.append(cmd)
        (self.out / "receipt.json").write_text(json.dumps({**OK_RECEIPT, "summary": f"第 {len(self.worker_calls) + 1} 轮"}),
                                               encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def order(self):
        return json.loads(self.order_file.read_text())

    def init_db(self):
        T.Store(self.db).open().close()

    def store(self):
        return T.Store(self.db, clock=lambda: T0, dispatch_dir=self.ledger).open()

    def run_cmd(self, *argv, success=True):
        output = io.StringIO()
        with patch.object(sys, "argv", ["dispatch.py", *argv]), contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                dispatch.main()
        self.assertEqual(stopped.exception.code, 0 if success else 1, output.getvalue())
        result = json.loads(output.getvalue())      # 整段 stdout 必须还是一个 JSON：钩子不能往里写
        self.assertEqual(result["ok"], success)
        return result


class NotifyTests(NotifyBase):
    def test_default_off_touches_nothing(self):
        o = self.order()
        dispatch.save(o)
        self.assertEqual(self.order()["status"], "done")
        self.assertIn("updated", self.order())
        self.assertFalse(self.db.exists())
        self.assertFalse(self.log.exists())

    def test_on_but_state_db_missing_never_creates_it(self):
        os.environ["DT_STATE_NOTIFY"] = "1"
        dispatch.save(self.order())
        self.assertFalse(self.db.exists())
        self.assertFalse(self.db.parent.exists())
        self.assertIn("不存在", self.log.read_text(encoding="utf-8"))
        self.assertIn("没有同步", self.log.read_text(encoding="utf-8"))

    def test_on_with_db_syncs_this_order_only(self):
        os.environ["DT_STATE_NOTIFY"] = "1"
        self.init_db()
        other = self.ledger / "D20000101-2"
        (other / "out").mkdir(parents=True)
        (other / "order.json").write_text(json.dumps({"no": "D20000101-2", "project": "fixture", "tool": "claude",
                                                      "mode": "read", "status": "done", "cwd": "/x"}))
        dispatch.save(self.order())
        with self.store() as s:
            self.assertEqual([r["order_no"] for r in s.list_runs()], [self.no])
            self.assertEqual(s.get_run(self.no)["status"], "done")
            self.assertEqual(s.get_run(self.no)["receipt"]["summary"], "好了")
            self.assertEqual(len(s.events(kind="order.sync")), 1)
        self.assertFalse(self.log.exists())                       # 成功不记日志
        o = self.order()
        o["status"] = "merged"
        dispatch.save(o)
        with self.store() as s:
            self.assertEqual(s.get_run(self.no)["status"], "merged")
            self.assertEqual(s.reconcile()["missed"], ["D20000101-2"])   # 没通知过的那张由对账补上

    def test_notify_failure_never_breaks_dispatch(self):
        os.environ["DT_STATE_NOTIFY"] = "1"
        self.init_db()
        with patch.object(T, "notify_order", side_effect=RuntimeError("boom")):
            o = self.order()
            o["status"] = "merged"
            dispatch.save(o)
        self.assertEqual(self.order()["status"], "merged")
        self.assertIn("通知状态库失败：RuntimeError: boom", self.log.read_text(encoding="utf-8"))
        with self.store() as s:
            self.assertEqual(s.list_runs(), [])

    def test_run_command_keeps_pure_json_output_with_notify_on_and_failing(self):
        os.environ["DT_STATE_NOTIFY"] = "1"
        self.init_db()
        result = self.run_cmd("run", self.no)
        self.assertEqual(result["msg"], f"{self.no} 跑完：done")
        self.assertEqual(len(self.worker_calls), 1)
        with self.store() as s:
            run = s.get_run(self.no)
            self.assertEqual((run["status"], run["receipt"]["summary"]), ("done", "第 2 轮"))
            self.assertGreaterEqual(len(s.events(kind="order.sync")), 2)       # running 一次、done 一次
        with patch.object(T, "notify_order", side_effect=RuntimeError("boom")):
            result = self.run_cmd("run", self.no)
        self.assertEqual(result["msg"], f"{self.no} 跑完：done")
        self.assertEqual(self.log.read_text(encoding="utf-8").count("通知状态库失败"), 2)
        self.assertEqual(self.order()["status"], "done")

    def test_duplicate_notify_is_idempotent(self):
        os.environ["DT_STATE_NOTIFY"] = "1"
        self.init_db()
        first = dispatch.notify_state(self.no)
        second = dispatch.notify_state(self.no)
        self.assertEqual((first["created"], second["created"], second["unchanged"]), (1, 0, 1))
        with self.store() as s:
            ev = s.events(kind="order.sync")
            self.assertEqual((len(ev), ev[0]["dup_count"]), (1, 1))
            self.assertEqual(len(s.list_runs()), 1)

    def test_missed_notifications_recovered_by_reconcile(self):
        self.init_db()
        o = self.order()
        o["status"] = "merged"
        dispatch.save(o)                                          # 通知关着：状态库不知道
        with self.store() as s:
            self.assertEqual(s.list_runs(), [])
            rec = s.reconcile()
            self.assertEqual((rec["missed"], rec["created"]), ([self.no], 1))
            self.assertEqual(s.get_run(self.no)["status"], "merged")
            self.assertEqual(s.reconcile()["missed"], [])

    def test_conflict_is_logged_and_evidence_kept(self):
        os.environ["DT_STATE_NOTIFY"] = "1"
        self.init_db()
        o = self.order()
        o["status"] = "cleaned"
        dispatch.save(o)
        o["status"] = "running"
        dispatch.save(o)
        self.assertIn("冲突", self.log.read_text(encoding="utf-8"))
        with self.store() as s:
            self.assertEqual(s.get_run(self.no)["status"], "cleaned")
            self.assertEqual(len(s.events(kind="run.conflict")), 1)


class DecisionOrderTests(unittest.TestCase):
    """need_decision 之后的接入顺序：常浩在飞书答复 → 经核实的 user 事件解阻塞 → 再续跑；顺序反了只记 resume_conflict。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-notify-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ledger = self.root / "dispatch"
        self.store = T.Store(self.root / "twin-state.db", clock=lambda: T0, dispatch_dir=self.ledger).open()
        self.addCleanup(self.store.close)
        g = self.store.create_goal("目标", by="user", status="authorized")
        t = self.store.create_task(g["id"], "任务", by="hermes", acceptance="能跑")
        self.store.set_ready(t["id"], by="hermes")
        self.task = t["id"]
        self.run = self.store.start_run(self.task, "claude", by="hermes", order_no="D20000101-1")["run"]["id"]
        self.write("done", {**OK_RECEIPT, "need_decision": "要不要删旧表"})
        self.store.sync_orders()
        self.assertEqual(self.store.get_task(self.task)["blocked_kind"], "decision")

    def write(self, status, receipt):
        d = self.ledger / "D20000101-1"
        (d / "out").mkdir(parents=True, exist_ok=True)
        (d / "order.json").write_text(json.dumps({"no": "D20000101-1", "project": "fixture", "tool": "claude", "mode": "write",
                                                  "status": status, "cwd": "/x", "updated": f"2000-01-01T10:00:00-{status}"}))
        (d / "out" / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    def test_followup_before_unblock_is_a_conflict(self):
        self.write("running", OK_RECEIPT)
        st = self.store.sync_orders()
        self.assertEqual(st["conflicts"], ["D20000101-1"])
        self.assertEqual(self.store.get_task(self.task)["status"], "blocked")
        self.assertEqual(self.store.get_run(self.run)["status"], "running")
        self.assertEqual(len(self.store.events(kind="run.resume_conflict")), 1)

    def test_unblock_then_followup_is_normal(self):
        self.store.unblock_task(self.task, by="user", note="删")
        self.write("running", OK_RECEIPT)
        st = self.store.sync_orders()
        self.assertEqual(st["conflicts"], [])
        self.assertEqual(self.store.get_task(self.task)["status"], "running")
        self.write("done", OK_RECEIPT)
        self.store.sync_orders()
        self.assertEqual(self.store.get_task(self.task)["status"], "review")


if __name__ == "__main__":
    unittest.main()
