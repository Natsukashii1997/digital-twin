"""隔离自测：B2.2 持久调度（twin_scheduler.py）与调度预留（twin_state.py 库 v3）。

每项测试用独立临时目录里的状态库、虚构的派单台账和受控子进程；不碰 ~/.hermes、正式状态库、真实工单，不调用模型或外部服务。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_scheduler.py -v
"""
import datetime as dt
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("twin_scheduler", SCRIPTS / "twin_scheduler.py")
sched = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sched)
ts = sched.ts

TZ = dt.timezone(dt.timedelta(hours=8))
T0 = dt.datetime(2000, 1, 1, 9, 0, tzinfo=TZ)
OK_RECEIPT = {"exit_code": 0, "timed_out": False, "worker_error": False, "session_id": "sess-1",
              "started": "2000-01-01T09:00:00", "ended": "2000-01-01T09:30:00", "need_decision": None,
              "guard_main_changed": False, "guard_remote_changed": False, "summary": "改好了。"}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-sched-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "state" / "twin-state.db"
        self.ledger = self.root / "dispatch"
        self.ledger.mkdir()
        self.t = T0
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()
        self.addCleanup(lambda: self.store.close())
        self.n = 0

    def goal(self, project="alpha", authorized=True):
        if authorized:
            return self.store.create_goal("隔离目标", by="user", status="authorized", project=project)["id"]
        return self.store.create_goal("未授权目标", by="hermes", project=project)["id"]

    def task(self, goal, title="任务", owner="claude", ready=True, depends_on=()):
        t = self.store.create_task(goal, title, by="hermes", owner=owner, depends_on=depends_on)["id"]
        if ready:
            self.store.set_ready(t, by="hermes")
        return t

    def order(self, status="running", project="alpha", **extra):
        self.n += 1
        no = f"D20000101-{self.n}"
        d = self.ledger / no
        d.mkdir()
        (d / "order.json").write_text(json.dumps({"no": no, "project": project, "tool": "claude", "mode": "write",
                                                  "status": status, "created": "2000-01-01T09:00:00", **extra}))
        return no

    def plan(self, store=None):
        return sched.decide(store or self.store)

    def by_task(self, out=None):
        return {d["task_id"]: d for d in (out or self.plan())["decisions"]}


class DecideTests(Base):
    """S1 / A01 / A08：只有满足全部授权与状态条件的任务入选；决策可解释、无副作用、可复现。"""

    def fixture(self):
        g = self.goal("alpha")
        ids = {}
        ids["eligible"] = self.task(g, "可派")
        ids["dep_root"] = self.task(self.goal("beta"), "被依赖", owner="hermes")
        g_dep = self.store.get_task(ids["dep_root"])["goal_id"]
        ids["deps"] = self.task(g_dep, "依赖没完成", depends_on=[ids["dep_root"]])
        ids["decision"] = self.task(self.goal("gamma"), "等常浩定")
        self.store.block_task(ids["decision"], by="hermes", kind="decision", reason="要选方案")
        ids["review"] = self.task(self.goal("delta"), "待验收")
        no = self.order(status="done", project="delta")
        r = self.store.start_run(ids["review"], agent="claude", by="hermes", order_no=no)["run"]
        self.store.receive_receipt(r["id"], OK_RECEIPT)
        ids["running"] = self.task(self.goal("eps"), "在跑")
        self.store.start_run(ids["running"], agent="claude", by="hermes", order_no=self.order(project="eps"))
        ids["unauth"] = self.task(self.goal("zeta", authorized=False), "未授权", ready=False)
        ids["observed"] = self.task(self.goal("eta"), "授权了但没 ready", ready=False)
        ids["canceled"] = self.task(self.goal("theta"), "已撤销")
        self.store.cancel_task(ids["canceled"], by="user")
        ids["technical"] = self.task(self.goal("iota"), "技术失败")
        self.store.block_task(ids["technical"], by="hermes", kind="technical", reason="测试挂了")
        ids["hermes"] = self.task(self.goal("kappa"), "不是车间的活", owner="hermes")
        ids["stale"] = self.task(self.goal("lam"), "当前 Run 在台账里找不到")
        self.store.start_run(ids["stale"], agent="claude", by="hermes", order_no="D20000101-99")
        return ids

    def test_only_authorized_ready_task_is_dispatched(self):
        ids = self.fixture()
        d = self.by_task(sched.decide(self.store, max_running=10))   # 名额放宽，只看候选过滤（名额见 SlotTests）
        self.assertNotIn(ids["canceled"], d)   # 已收尾的任务不出现
        picked = [k for k, v in d.items() if v["decision"] == "dispatch"]
        self.assertEqual(picked, [ids["eligible"]])
        want = {"deps": ("skip", "deps_unmet"), "decision": ("skip", "blocked_decision"), "review": ("skip", "in_review"),
                "running": ("skip", "running"), "unauth": ("skip", "goal_not_authorized"),
                "observed": ("skip", "task_not_ready"), "technical": ("skip", "blocked_technical"),
                "hermes": ("skip", "no_executor"), "dep_root": ("skip", "no_executor"),
                "stale": ("unsafe", "queue_unsafe"), "eligible": ("dispatch", "eligible")}
        for k, (decision, reason) in want.items():
            self.assertEqual((d[ids[k]]["decision"], d[ids[k]]["reason"]), (decision, reason), k)
        for v in d.values():   # 每条都可审计
            self.assertTrue(v["why"] and v["next_action"] and isinstance(v["evidence"], dict))
        e = d[ids["eligible"]]["evidence"]
        self.assertEqual((e["project"], e["tool"], e["ledger"]), ("alpha", "claude", "ok"))
        self.assertEqual(d[ids["deps"]]["evidence"]["depends_on"], [ids["dep_root"]])
        self.assertIn("找不到", d[ids["stale"]]["evidence"]["unsafe"])

    def test_decide_has_no_side_effects_and_is_stable(self):
        self.fixture()
        self.store.close()
        before = (self.db.read_bytes(), sorted(p.name for p in self.ledger.rglob("*")))
        with ts.Store(self.db, dispatch_dir=self.ledger, readonly=True) as ro:
            a = sched.decide(ro)
            b = sched.decide(ro)
            self.assertEqual(ro.conn.total_changes, 0)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(before, (self.db.read_bytes(), sorted(p.name for p in self.ledger.rglob("*"))))
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()

    def test_readonly_store_rejects_writes_and_never_creates(self):
        missing = self.root / "nope" / "twin-state.db"
        with self.assertRaises(ts.NotFound):
            ts.Store(missing, readonly=True).open()
        self.assertFalse(missing.parent.exists())
        self.goal()
        with ts.Store(self.db, readonly=True) as ro:
            with self.assertRaises(sqlite3.OperationalError):
                ro.create_goal("x", by="hermes")

    def test_ledger_unavailable_holds_everything(self):
        t = self.task(self.goal())
        self.ledger.rename(self.root / "moved")
        d = self.by_task()[t]
        self.assertEqual((d["decision"], d["reason"], d["evidence"]["ledger"]), ("hold", "ledger_unavailable", "missing"))

    def test_retry_over_limit_and_no_project(self):
        t1 = self.task(self.goal())
        self.store.conn.execute("UPDATE tasks SET retries=9 WHERE id=?", (t1,))
        t2 = self.task(self.goal(project=None))
        d = self.by_task()
        self.assertEqual((d[t1]["decision"], d[t1]["reason"]), ("unsafe", "retry_over_limit"))
        self.assertEqual((d[t2]["decision"], d[t2]["reason"]), ("skip", "no_project"))


class SlotTests(Base):
    """A03：总并发 2、同项目 1，名额由预留 + 活动 Run + 台账在跑的单共同决定。"""

    def test_project_and_global_slots_in_creation_order(self):
        a1, a2 = self.task(self.goal("alpha"), "a1"), self.task(self.goal("alpha"), "a2")
        b1 = self.task(self.goal("beta"), "b1")
        c1 = self.task(self.goal("gamma"), "c1")
        d = self.by_task()
        self.assertEqual([d[x]["decision"] for x in (a1, a2, b1, c1)], ["dispatch", "hold", "dispatch", "hold"])
        self.assertEqual((d[a2]["reason"], d[c1]["reason"]), ("slot_full_project", "slot_full_global"))

    def test_foreign_running_order_takes_a_slot(self):
        self.order(status="running", project="alpha")   # 手工派的单，不在状态库里
        a = self.task(self.goal("alpha"))
        b = self.task(self.goal("beta"))
        d = self.by_task()
        self.assertEqual((d[a]["reason"], d[b]["reason"]), ("slot_full_project", "eligible"))
        self.order(status="running", project="zeta")
        self.assertEqual(self.by_task()[b]["reason"], "slot_full_global")

    def test_new_order_not_running_does_not_take_a_foreign_slot(self):
        self.order(status="new", project="alpha")   # 开了单没跑（等确认门）：不是调度器的，不占名额
        a = self.task(self.goal("alpha"))
        self.assertEqual(self.by_task()[a]["reason"], "eligible")


class ClaimTests(Base):
    """S2 / A02 / A04：预留原子、唯一，开单与关联的每一步都可补偿，归属不明进 unsafe。"""

    def test_claim_once_then_second_refused(self):
        t = self.task(self.goal())
        first = sched.claim(self.store, t, "S-1", owner={"pid": 1})
        self.assertTrue(first["applied"])
        self.assertEqual(first["claim"]["state"], "claimed")
        again = sched.claim(self.store, t, "S-2", owner={"pid": 2})
        self.assertEqual((again["applied"], again["reason"]), (False, "claim_open"))
        self.assertEqual(self.by_task()[t]["reason"], "claim_open")
        with self.assertRaises(sqlite3.IntegrityError):   # 绕过复核直接插也不行：部分唯一索引
            self.store.conn.execute("INSERT INTO sched_claims (sched_key, task_id, state, lease_until, created_at, updated_at)"
                                    " VALUES ('S-x', ?, 'ordered', 'x', 'x', 'x')", (t,))
        self.assertEqual(len(self.store.claims(task_id=t)), 1)
        self.assertEqual([e["kind"] for e in self.store.events("task", t) if e["kind"].startswith("sched.")], ["sched.claim"])

    def test_claim_counts_against_project_slot(self):
        g = self.goal("alpha")
        t1, t2 = self.task(g, "1"), self.task(g, "2")
        self.assertTrue(sched.claim(self.store, t1, "S-1")["applied"])
        r = sched.claim(self.store, t2, "S-2")
        self.assertEqual((r["applied"], r["reason"]), (False, "slot_full_project"))

    def test_refused_claim_writes_nothing(self):
        t = self.task(self.goal(), owner="hermes")
        n = len(self.store.events())
        r = sched.claim(self.store, t, "S-1")
        self.assertEqual((r["applied"], r["reason"]), (False, "no_executor"))
        self.assertEqual((len(self.store.events()), self.store.claims()), (n, []))

    def test_order_then_link_starts_current_run(self):
        t = self.task(self.goal())
        sched.claim(self.store, t, "S-1")
        no = self.order(status="new", sched_key="S-1")
        self.assertTrue(self.store.claim_ordered("S-1", no)["applied"])
        self.assertFalse(self.store.claim_ordered("S-1", no)["applied"])   # 重复调用不变
        res = self.store.claim_link("S-1", agent="claude")
        self.assertTrue(res["applied"])
        task = self.store.get_task(t)
        run = self.store.get_run(res["run"])
        self.assertEqual((task["status"], task["current_run_id"], run["order_no"], run["status"]), ("running", run["id"], no, "new"))
        self.assertEqual(self.store.get_claim("S-1")["state"], "linked")
        self.assertFalse(self.store.claim_link("S-1", agent="claude")["applied"])
        self.assertEqual(self.by_task()[t]["reason"], "running")   # 关联后由“一任务一活动 Run”防重复

    def test_link_adopts_order_already_imported_by_notify(self):
        t = self.task(self.goal())
        sched.claim(self.store, t, "S-1")
        no = self.order(status="new", sched_key="S-1")
        self.store.sync_orders()     # 写入后通知 / 定时对账先把单导入成未关联的 Run
        rid = self.store.list_runs(unlinked=True)[0]["id"]
        self.store.claim_ordered("S-1", no)
        res = self.store.claim_link("S-1", agent="claude")
        self.assertEqual((res["applied"], res["run"]), (True, rid))
        self.assertEqual(self.store.get_task(t)["current_run_id"], rid)

    def test_task_changed_while_ordering_goes_unsafe_and_only_user_closes(self):
        t = self.task(self.goal())
        sched.claim(self.store, t, "S-1")
        no = self.order(status="new", sched_key="S-1")
        self.store.claim_ordered("S-1", no)
        self.store.cancel_task(t, by="user")
        res = self.store.claim_link("S-1", agent="claude")
        self.assertFalse(res["applied"])
        cl = self.store.get_claim("S-1")
        self.assertEqual(cl["state"], "unsafe")
        self.assertIn(no, cl["note"])
        self.assertEqual(self.store.list_runs(task_id=t), [])
        with self.assertRaises(ts.NotAuthorized):
            self.store.claim_close("S-1", "void", by="hermes")
        self.assertTrue(self.store.claim_close("S-1", "void", by="user", note="常浩核对：单已放弃")["applied"])

    def test_second_order_number_for_same_claim_is_unsafe(self):
        t = self.task(self.goal())
        sched.claim(self.store, t, "S-1")
        self.store.claim_ordered("S-1", "D20000101-1")
        self.assertFalse(self.store.claim_ordered("S-1", "D20000101-2")["applied"])
        self.assertEqual(self.store.get_claim("S-1")["state"], "unsafe")

    def test_unsafe_claim_blocks_redispatch(self):
        t = self.task(self.goal())
        sched.claim(self.store, t, "S-1")
        self.store.claim_close("S-1", "unsafe", note="归属证明不了")
        d = self.by_task()[t]
        self.assertEqual((d["decision"], d["reason"]), ("unsafe", "claim_unsafe"))
        self.assertFalse(sched.claim(self.store, t, "S-2")["applied"])


CLAIMER = r"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import twin_scheduler as sched, twin_state as ts
db, ledger, task, key, go = sys.argv[2:7]
while not Path(go).exists():
    time.sleep(0.005)
with ts.Store(db, dispatch_dir=ledger) as s:
    r = sched.claim(s, task, key, owner={"pid": 0, "who": key}, dispatch_dir=ledger)
print(json.dumps({"applied": r["applied"], "reason": r.get("reason")}))
"""


class ConcurrentClaimTests(Base):
    """S7-3 / A02：两个调度器进程同时预留同一任务，只有一个成功；失败方什么都不写。"""

    def race(self, tasks, n=2):
        go = self.root / "go"
        procs = [subprocess.Popen([sys.executable, "-c", CLAIMER, str(SCRIPTS), str(self.db), str(self.ledger),
                                   tasks[i % len(tasks)], f"S-{i}", str(go)], stdout=subprocess.PIPE, text=True)
                 for i in range(n)]
        time.sleep(0.3)
        go.touch()
        return [json.loads(p.communicate(timeout=60)[0]) for p in procs]

    def test_two_schedulers_same_task(self):
        t = self.task(self.goal())
        res = self.race([t], n=4)
        self.assertEqual(sum(r["applied"] for r in res), 1, res)
        self.assertTrue(all(r["reason"] == "claim_open" for r in res if not r["applied"]), res)
        self.assertEqual(len(self.store.claims(task_id=t)), 1)

    def test_two_schedulers_same_project_different_tasks(self):
        g = self.goal("alpha")
        a, b = self.task(g, "a"), self.task(g, "b")
        res = self.race([a, b], n=4)
        self.assertEqual(sum(r["applied"] for r in res), 1, res)
        self.assertEqual(len(self.store.claims(states=ts.CLAIM_OPEN)), 1)


class MigrationTests(Base):
    """v1 / v2 库追加迁移到 v3；只读打开 v2 库也能决策（当作没有预留）。"""

    def make_old(self, version):
        self.store.close()
        self.db.unlink()
        for f in self.db.parent.glob("twin-state.db-*"):
            f.unlink()
        conn = sqlite3.connect(str(self.db))
        for step in ts.MIGRATIONS[:version]:
            conn.executescript(step)
        conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(version),))
        conn.commit()
        conn.close()

    def test_v1_and_v2_migrate_to_v3(self):
        for v in (1, 2):
            self.make_old(v)
            with ts.Store(self.db) as s:
                self.assertEqual(s.version(), 3)
                self.assertTrue(s._has_claims())
                self.assertEqual(s.claims(), [])
            self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()

    def test_readonly_v2_plans_without_migrating(self):
        self.make_old(2)
        with ts.Store(self.db, dispatch_dir=self.ledger, readonly=True) as ro:
            self.assertEqual((ro.version(), ro.claims()), (2, []))
            self.assertEqual(sched.decide(ro)["decisions"], [])
        with ts.Store(self.db, readonly=True) as ro:
            self.assertEqual(ro.version(), 2)
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()


class CliTests(Base):
    def cli(self, *args, db=None):
        env = {**os.environ, "DT_STATE_DB": str(db or self.db), "DT_DISPATCH": str(self.ledger)}
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "twin_scheduler.py"), *args], capture_output=True,
                           text=True, env=env, timeout=60)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_plan_cli_is_readonly(self):
        t = self.task(self.goal())
        self.store.close()
        before = self.db.read_bytes()
        code, out = self.cli("plan")
        self.assertEqual((code, out["ok"], out["counts"]["dispatch"]), (0, True, 1))
        self.assertEqual(out["decisions"][0]["task_id"], t)
        self.assertEqual(before, self.db.read_bytes())
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()

    def test_plan_cli_missing_db_does_not_create(self):
        missing = self.root / "none" / "twin-state.db"
        code, out = self.cli("plan", db=missing)
        self.assertEqual((code, out["ok"], out["error"]), (1, False, "NotFound"))
        self.assertFalse(missing.parent.exists())


if __name__ == "__main__":
    unittest.main()
