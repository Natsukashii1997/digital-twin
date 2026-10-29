"""隔离自测：B2.2 合并前定向返修（ChatGPT 评审 2026-10-29，F1 / F2）。

F1 迟到工单与旧关联键：开单子进程已启动但没来得及登记时预留方被杀、锁前 git/gh 拖过租约、旧关联键迟到开单、游离单。
F2 报告确认时机：输出不等于送达。
真实子进程跑 dispatch.py（dispatch_harness.py，可在拿锁前睡一会模拟慢 fetch）；临时状态库与派单根目录；不碰 ~/.hermes、正式状态库、
真实工单与外部服务。单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_scheduler_rework.py -v
"""
import contextlib
import datetime as dt
import io
import json
import os
import subprocess
import sys
import time
import unittest

from test_twin_scheduler import Base, sched, ts, dead_identity, SCRIPTS
from test_twin_scheduler_life import Life, HARNESS

CRASH_AFTER_SPAWN = r"""
import os, signal, subprocess, sys
sys.path.insert(0, sys.argv[1])
import twin_scheduler as sched, twin_state as ts
db, ledger, harness = sys.argv[2:5]
class Crash(sched.DispatchExecutor):
    def create(self, decision, sched_key, task_file, on_spawn, **kw):
        kw.get("before_spawn", lambda: None)()          # 真实执行器拉子进程前的那一步（返修前没有）
        ev = decision["evidence"]
        env = dict(os.environ)
        if kw.get("db"):
            env["DT_STATE_DB"] = str(kw["db"])
        subprocess.Popen([*self.argv, "new", "--project", ev["project"], "--tool", ev["tool"], "--mode", "read",
                          "--task-file", str(task_file), "--sched-key", sched_key], env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.kill(os.getpid(), signal.SIGKILL)            # 子进程已启动，on_spawn 还没来得及记身份
with ts.Store(db, dispatch_dir=ledger) as s:
    sched.tick(s, Crash("read", argv=[sys.executable, "-B", harness]))
"""


class F1SpawnWindowTests(Life):
    """F1：子进程可能已启动却没登记时绝不 void；迟到的旧关联键开不出单。"""

    def wait_file(self, f, timeout=30):
        self.wait_for(lambda: f.exists(), timeout)

    def test_child_started_unregistered_owner_killed_never_voids(self):
        t = self.task(self.goal("alpha"))
        done = self.root / "late-new.done"
        env = self.env(H_PRELOCK_DELAY=3, H_DONE_FILE=done)        # 迟到的 dispatch new：拿锁前拖 3 秒
        p = subprocess.run([sys.executable, "-B", "-c", CRASH_AFTER_SPAWN, str(SCRIPTS), str(self.db), str(self.ledger),
                            str(HARNESS)], env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, -9, p.stderr[-500:])
        cl = self.store.claims(task_id=t)[0]
        self.assertNotIn("child", cl["owner"])
        self.t = dt.datetime.now().astimezone() + dt.timedelta(seconds=ts.SCHED_LEASE + 1)   # 租约早就过了
        r = sched.reconcile_claim(self.store, cl["sched_key"], self.ledger)
        self.assertNotEqual(r["result"], "void", r)                 # 缺子进程登记 ≠ 没启动子进程
        self.assertEqual(r["result"], "unsafe", r)
        self.wait_file(done)                                        # 迟到的开单结束
        self.assertEqual(self.orders(), [], "迟到的旧关联键不该开出单")
        d = self.by_task()[t]
        self.assertNotEqual(d["decision"], "dispatch", d)
        self.assertEqual(sched.tick(self.store, sched.DispatchExecutor("read", argv=[sys.executable, "-B", str(HARNESS)]),
                                    self.ledger)["claimed"], [])

    def orders(self):
        return sorted(p.name for p in self.ledger.glob("D*"))

    def put_claim(self, t, key, project="alpha"):
        res = self.store.claim_task(t, key, {"pid": 0}, lambda s: {"decision": "dispatch", "task_id": t,
                                                                    "evidence": {"project": project}})
        self.assertTrue(res["applied"], res)

    def dnew_key(self, key, project="alpha", **kw):
        env = self.env(**{"DT_STATE_DB": self.db, **kw})
        r = subprocess.run([sys.executable, "-B", str(HARNESS), "new", "--project", project, "--tool", "claude", "--mode",
                            "read", "--task-file", str(self.root / "task.md"), "--sched-key", key], env=env,
                           capture_output=True, text=True, timeout=60)
        lines = r.stdout.strip().splitlines()
        return r.returncode, (json.loads(lines[-1]) if lines else {"stderr": r.stderr[-300:]})

    def test_late_key_after_slow_fetch_is_rejected_inside_lock(self):
        t = self.task(self.goal("alpha"))
        self.put_claim(t, "S-late")
        done = self.root / "slow.done"
        p = subprocess.Popen([sys.executable, "-B", str(HARNESS), "new", "--project", "alpha", "--tool", "claude", "--mode",
                              "read", "--task-file", str(self.root / "task.md"), "--sched-key", "S-late"],
                             env=self.env(DT_STATE_DB=self.db, H_PRELOCK_DELAY=2, H_DONE_FILE=done),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(p)
        time.sleep(0.5)                                             # 开单还卡在锁前（慢 fetch）
        self.store.claim_close("S-late", "void", by="user", note="常浩核对后作废")
        out, _ = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 1, out)
        self.assertIn("S-late", json.loads(out.strip().splitlines()[-1])["msg"])
        self.assertEqual(self.orders(), [])

    def test_key_check_inside_lock_not_blocked_by_open_db_write(self):
        """锁顺序：调度器持状态库写事务时从不等派单锁；dispatch 持派单锁只读状态库（WAL 读不被写阻塞），所以不会死锁。"""
        t = self.task(self.goal("alpha"))
        self.put_claim(t, "S-tx")
        with self.store._tx():                                      # 模拟另一个调度器正持着写事务
            start = time.monotonic()
            code, out = self.dnew_key("S-tx")
            took = time.monotonic() - start
        self.assertEqual(code, 0, out)
        self.assertLess(took, 20)
        r = sched.reconcile_claim(self.store, "S-tx", self.ledger)   # 派单锁 → 状态库写：之后照常关联
        self.assertEqual(r["result"], "linked", r)

    def test_only_claimed_keys_can_open_orders(self):
        g = self.goal("alpha")
        cases = {}
        for state in ("void", "released", "unsafe", "linked", "claimed"):
            t = self.task(g, state)
            key = f"S-{state}"
            self.put_claim(t, key)
            if state in ("void", "released", "unsafe"):
                self.store.claim_close(key, state)
            elif state == "linked":
                no = self.order(status="new", project="alpha", sched_key=key)
                self.store.claim_ordered(key, no)
                self.store.claim_link(key, agent="claude")
            cases[state] = key
        before = set(self.orders())
        for state, key in cases.items():
            code, out = self.dnew_key(key)
            if state == "claimed":
                self.assertEqual(code, 0, out)
            else:
                self.assertEqual(code, 1, (state, out))
                self.assertIn(state, out["msg"])
        self.assertEqual(len(set(self.orders()) - before), 1)       # 只有 claimed 的那个开出了单
        t = self.task(self.goal("beta"), "项目不符")
        self.put_claim(t, "S-proj", project="beta")
        self.assertEqual(self.dnew_key("S-proj", project="alpha")[0], 1)
        self.assertEqual(self.dnew_key("S-nobody")[0], 1)            # 状态库里没有这个预留
        code, out = self.dnew_key("S-claimed2", DT_STATE_DB=self.root / "missing.db")
        self.assertEqual(code, 1, out)                              # 状态库不存在：拒绝开调度单
        self.assertEqual(len(set(self.orders()) - before), 1)
        self.assertEqual(self.dnew(), self.orders()[-1])            # 不带关联键的手工开单照常


class F1OrphanTests(Base):
    """F1：已收口的关联键如果仍对应一张单（或半截目录），显式报 unsafe / orphan，不自动关联、不让任务再派。"""

    def test_orphan_order_blocks_task_and_is_reported(self):
        t = self.task(self.goal("alpha"))
        sched.claim(self.store, t, "S-1", owner=dead_identity())
        self.store.claim_close("S-1", "void", by="user")
        self.assertEqual(self.by_task()[t]["decision"], "dispatch")    # 还没有游离单时：可以再派
        no = self.order(status="new", sched_key="S-1")
        d = self.by_task()[t]
        self.assertEqual((d["decision"], d["reason"]), ("unsafe", "orphan_order"), d)
        self.assertEqual(d["evidence"]["orphans"][0]["order_no"], no)
        self.assertFalse(sched.claim(self.store, t, "S-2")["applied"])
        found = [f for f in sched.findings(self.store) if f["kind"] == "order"]
        self.assertEqual([(f["level"], f["object"]) for f in found], [("error", no)])
        rec = sched.recover(self.store, self.ledger)
        self.assertEqual([o["order_no"] for o in rec["orphans"]], [no])
        self.assertEqual(self.store.list_runs(task_id=t), [])             # 不自动挂到任务上
        self.assertTrue((self.ledger / no / "order.json").exists())      # 也不删单

    def test_unknown_key_and_half_dir_are_orphans(self):
        no = self.order(status="new", sched_key="S-ghost")
        half = self.ledger / "D20000101-50"
        half.mkdir()
        (half / "sched_key").write_text("S-ghost2")
        objs = sorted(f["object"] for f in sched.findings(self.store) if f["kind"] == "order")
        self.assertEqual(objs, sorted([no, "D20000101-50"]))

    def test_recover_cli_fails_on_orphans(self):
        self.order(status="new", sched_key="S-ghost")
        self.store.close()
        env = {**os.environ, "DT_STATE_DB": str(self.db), "DT_DISPATCH": str(self.ledger)}
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "twin_scheduler.py"), "recover"], env=env,
                           capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual((r.returncode, len(out["orphans"])), (1, 1), out)
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()


class F1VoidEvidenceTests(Base):
    """F1：只有两种情形能证明没开出单：从没到拉子进程那一步；或子进程登记过且确认已退出。"""

    def lease_over(self):
        self.t += dt.timedelta(seconds=ts.SCHED_LEASE + 1)

    def test_spawn_marked_without_child_is_unsafe(self):
        t = self.task(self.goal())
        sched.claim(self.store, t, "S-1", owner=dead_identity())
        self.store.claim_mark_spawning("S-1")
        self.lease_over()
        r = sched.reconcile_claim(self.store, "S-1", self.ledger)
        self.assertEqual(r["result"], "unsafe", r)
        self.assertIn("没登记", r["why"])
        self.assertEqual(self.by_task()[t]["reason"], "claim_unsafe")

    def test_spawn_marked_with_dead_child_can_void(self):
        t = self.task(self.goal())
        sched.claim(self.store, t, "S-1", owner=dead_identity())
        self.store.claim_mark_spawning("S-1")
        self.store.claim_set_child("S-1", dead_identity())
        self.lease_over()
        self.assertEqual(sched.reconcile_claim(self.store, "S-1", self.ledger)["result"], "void")

    def test_never_reached_spawn_can_void(self):
        t = self.task(self.goal())
        sched.claim(self.store, t, "S-1", owner=dead_identity())
        self.lease_over()
        self.assertEqual(sched.reconcile_claim(self.store, "S-1", self.ledger)["result"], "void")


class F2ReportDeliveryTests(Base):
    """F2：输出不等于送达；只有投递端确认送达才 ack；失败、超时、崩溃都保留待报。"""

    def blocked(self):
        a = self.task(self.goal())
        self.store.block_task(a, by="hermes", kind="decision", reason="要选方案")
        return a

    def test_dropped_stdout_keeps_report_pending(self):
        self.blocked()
        self.assertEqual(len(sched.report(self.store)), 1)
        with contextlib.redirect_stdout(io.StringIO()):            # 输出被丢掉（投递没发生）
            try:
                sched.main(["--db", str(self.db), "--dispatch", str(self.ledger), "report", "--quiet"])
            except SystemExit:
                pass
        self.assertEqual(len(sched.report(self.store)), 1)

    def test_deliver_acks_only_on_confirmed_delivery(self):
        self.blocked()

        def boom(items):
            raise TimeoutError("飞书超时")

        for sender in (lambda items: False, lambda items: None, lambda items: "ok", boom):
            r = sched.deliver(self.store, sender)
            self.assertEqual((r["delivered"], r["pending"]), (0, 1), r)
        fp = sched.report(self.store)[0]["fingerprint"]
        r = sched.deliver(self.store, lambda items: [fp])          # 投递端逐条确认
        self.assertEqual((r["delivered"], r["pending"]), (1, 0), r)
        n = len(self.store.events())
        sched.ack(self.store, [{"fingerprint": fp, "level": "needs_user", "kind": "task", "object": "x", "reason": "y"}])
        self.assertEqual(len(self.store.events()), n)              # 重复确认幂等
        self.assertEqual(sched.deliver(self.store, lambda items: True), {"pending": 0, "delivered": 0})

    def test_partial_confirmation_keeps_the_rest(self):
        g = self.goal()
        for i in range(2):
            t = self.task(g, f"t{i}")
            self.store.block_task(t, by="hermes", kind="decision", reason=f"选{i}")
        items = sched.report(self.store)
        r = sched.deliver(self.store, lambda got: [got[0]["fingerprint"], "not-a-real-fp"])
        self.assertEqual((r["delivered"], r["pending"]), (1, 1), r)
        self.assertEqual([f["fingerprint"] for f in sched.report(self.store)], [items[1]["fingerprint"]])

    def cli(self, *args):
        env = {**os.environ, "DT_STATE_DB": str(self.db), "DT_DISPATCH": str(self.ledger)}
        return subprocess.run([sys.executable, "-B", str(SCRIPTS / "twin_scheduler.py"), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_cli_report_repeats_until_ack_then_quiet(self):
        a = self.blocked()
        self.store.close()
        r1, r2 = self.cli("report", "--quiet"), self.cli("report", "--quiet")    # 第一次输出后“投递失败”
        f1, f2 = json.loads(r1.stdout)["findings"], json.loads(r2.stdout)["findings"]
        self.assertEqual([f["object"] for f in f1], [a])
        self.assertEqual(f1, f2)                                    # 下一轮重新拿到同一条
        fp = f1[0]["fingerprint"]
        bad = self.cli("ack", "--fingerprint", "0000000000000000")
        self.assertEqual(bad.returncode, 1)
        ok = self.cli("ack", "--fingerprint", fp)
        self.assertEqual((ok.returncode, json.loads(ok.stdout)["acked"]), (0, [fp]))
        self.assertEqual(self.cli("ack", "--fingerprint", fp).returncode, 0)   # 重复确认不报错
        r3 = self.cli("report", "--quiet")
        self.assertEqual((r3.returncode, r3.stdout), (0, ""))
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()
        self.assertEqual(len([e for e in self.store.events() if e["kind"] == "sched.report"]), 1)
        self.assertEqual(self.store.list_tasks(), [self.store.get_task(a)])     # 报告不生成任务或工单
        self.assertEqual(list(self.ledger.glob("D*")), [])


if __name__ == "__main__":
    unittest.main()
