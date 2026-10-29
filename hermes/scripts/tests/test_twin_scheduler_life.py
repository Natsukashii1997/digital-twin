"""隔离自测：B2.2 S3 生命体征、S4 取消闭环、S5 重试建议、S6 低噪声报告。

真实子进程：dispatch_harness.py 在临时目录里跑真实 dispatch.py，车间是 fake_worker_proc.py；
只对测试自己拉起、身份全对上的车间进程组发 SIGTERM。不碰 ~/.hermes、正式状态库、真实工单与外部服务。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_scheduler_life.py -v
"""
import datetime as dt
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from test_twin_scheduler import Base, sched, ts, sleeper, ident_of, dead_identity, HERE, SCRIPTS

HARNESS = HERE / "dispatch_harness.py"
FAIL_RECEIPT = {"exit_code": 1, "timed_out": False, "worker_error": "claude 退出码 1", "started": "2000-01-01T09:00:00",
                "ended": "2000-01-01T09:30:00", "need_decision": None, "guard_main_changed": False, "guard_remote_changed": False}


class Life(Base):
    def setUp(self):
        super().setUp()
        (self.root / "repo").mkdir()
        (self.root / "roster.json").write_text(json.dumps({"projects": [
            {"id": p, "name": p, "path": str(self.root / "repo"), "main": "main", "write_allowed": False}
            for p in ("alpha", "beta")]}))
        (self.root / "task.md").write_text("# 目标：隔离\n", encoding="utf-8")
        self.procs = []
        self.addCleanup(self.reap)

    def reap(self):
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    p.kill()
                p.wait()
        for f in self.ledger.glob("D*/out/worker.json"):    # 测试里拉起的车间进程组全部收掉
            try:
                os.killpg(json.loads(f.read_text())["pgid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError, ValueError, KeyError, OSError):
                pass

    def env(self, **kw):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("H_", "DT_STATE_NOTIFY", "DT_SCHED"))}
        e.update(H_ROOT=str(self.root), **{k: str(v) for k, v in kw.items()})
        return e

    def dnew(self, project="alpha"):
        r = subprocess.run([sys.executable, "-B", str(HARNESS), "new", "--project", project, "--tool", "claude", "--mode",
                            "read", "--task-file", str(self.root / "task.md")], env=self.env(), capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])["order"]["no"]

    def drun(self, no, **kw):
        p = subprocess.Popen([sys.executable, "-B", str(HARNESS), "run", no], env=self.env(**kw), stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.procs.append(p)
        return p

    def wait_for(self, cond, timeout=20):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return True
            time.sleep(0.05)
        self.fail("等不到条件成立")

    def worker(self, no):
        f = self.ledger / no / "out" / "worker.json"
        return json.loads(f.read_text()) if f.exists() else None

    def ledger_order(self, no):
        return json.loads((self.ledger / no / "order.json").read_text())

    def started_task(self, project="alpha", **run_kw):
        """建任务 → 真实开单 → 登记成当前 Run → 后台真实开跑，等车间写出身份。"""
        t = self.task(self.goal(project))
        no = self.dnew(project)
        rid = self.store.start_run(t, agent="claude", by="hermes", order_no=no, status="new")["run"]["id"]
        p = self.drun(no, **run_kw)
        self.wait_for(lambda: self.worker(no) and self.ledger_order(no).get("status") == "running")
        return t, no, rid, p

    def vital(self, no):
        return next(v for v in sched.observe(self.store) if v["order_no"] == no)


class VitalTests(Life):
    """S3 / A05 / S7-5：不因心跳旧判停；进程已退出无回执也只判 lost、不自动失败。"""

    def test_alive_despite_old_heartbeat_then_finished(self):
        t, no, rid, p = self.started_task(H_WORK=3)
        self.store.sync_orders()                           # Run 跟上台账的 running，心跳记在 T0
        self.t += dt.timedelta(days=3)                     # 之后心跳早就“过期”了
        v = self.vital(no)
        self.assertEqual(v["vital"], "alive", v)
        self.assertGreater(v["evidence"]["heartbeat_age"], ts.STALE_AFTER)
        rec = self.store.recover()
        self.assertIn(rid, rec["suspect"])                 # B1 recover 只提示，不判过期
        self.assertEqual(self.store.get_task(t)["status"], "running")
        self.assertEqual(sched.decide(self.store)["counts"]["dispatch"], 0)
        p.communicate(timeout=30)
        self.assertEqual(self.vital(no)["vital"], "finished")
        self.store.sync_orders()
        self.assertEqual(self.store.get_task(t)["status"], "review")   # 跑完只到待验收，不会自动完成

    def test_dispatch_and_worker_killed_is_lost_not_failed(self):
        t, no, rid, p = self.started_task(H_WORK=30)
        os.kill(self.ledger_order(no)["dispatch_pid"], signal.SIGKILL)
        p.wait()
        os.killpg(self.worker(no)["pgid"], signal.SIGKILL)
        self.wait_for(lambda: sched.check_identity(self.worker(no)) == "gone")
        self.assertEqual(self.ledger_order(no)["status"], "running")   # 残留 running
        v = self.vital(no)
        self.assertEqual((v["vital"], v["evidence"]["worker"], v["evidence"]["dispatch"]), ("lost", "gone", "gone"), v)
        self.store.sync_orders()
        self.store.recover()
        self.assertEqual((self.store.get_run(rid)["status"], self.store.get_task(t)["status"]), ("running", "running"))
        self.assertIn(("error", "run", "lost"), [(f["level"], f["kind"], f["reason"]) for f in sched.report(self.store)])

    def test_dispatch_killed_worker_keeps_running_then_late_receipt(self):
        t, no, rid, p = self.started_task(H_WORK=2)
        os.kill(self.ledger_order(no)["dispatch_pid"], signal.SIGKILL)
        p.wait()
        v = self.vital(no)
        self.assertEqual((v["vital"], v["evidence"]["dispatch"]), ("alive", "gone"), v)
        self.wait_for(lambda: sched.check_identity(self.worker(no)) == "gone", timeout=30)
        v = self.vital(no)
        self.assertEqual((v["vital"], v["evidence"]["receipt"]), ("finished", True), v)
        self.assertIn("dispatch 已不在", v["why"])
        self.assertEqual(self.ledger_order(no)["status"], "running")   # 调度器不替 dispatch 改台账

    def fake_running(self, worker=None, dispatch_pid=None):
        no = self.order(status="running", run_started="2000-01-01T09:00:00", run_host=sched.socket.gethostname(),
                        **({"dispatch_pid": dispatch_pid} if dispatch_pid else {}))
        out = self.ledger / no / "out"
        out.mkdir()
        if worker is not None:
            (out / "worker.json").write_text(json.dumps({"out": str(out), "at": "2000-01-01T09:00:01", **worker}))
        return no

    def test_no_identity_is_unknown(self):
        self.assertEqual(self.vital(self.fake_running())["vital"], "unknown")

    def test_pid_reuse_and_foreign_process(self):
        p = sleeper()
        self.addCleanup(lambda: (p.kill(), p.wait()))
        ident = {**ident_of(p), "pgid": p.pid, "sid": p.pid}
        reused = self.fake_running({**ident, "started": "Mon Jan  1 00:00:00 2001"})
        self.assertEqual(self.vital(reused)["vital"], "lost")          # 同 PID、启动时间对不上：原车间早就没了
        foreign = self.fake_running(ident)                              # 身份对上了，但命令行不是这张单的车间
        v = self.vital(foreign)
        self.assertEqual((v["vital"], v["evidence"]["worker"]), ("unknown", "unknown"))

    def test_previous_round_identity_is_not_trusted(self):
        p = sleeper()
        self.addCleanup(lambda: (p.kill(), p.wait()))
        no = self.fake_running({**ident_of(p), "at": "1999-12-31T23:00:00"})
        self.assertEqual(self.vital(no)["evidence"]["worker"], "previous")

    def test_observe_cli_after_restart(self):
        t, no, rid, p = self.started_task(H_WORK=3)
        self.store.close()
        env = {**os.environ, "DT_STATE_DB": str(self.db), "DT_DISPATCH": str(self.ledger)}
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "twin_scheduler.py"), "observe"], env=env,
                           capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual([(v["run"], v["vital"]) for v in out["vitals"]], [(rid, "alive")])
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()


class CancelTests(Life):
    """S4 / A06 / A07 / S7-6：只停身份全对上的自有车间；身份不明不发信号；取消与完成竞争结果一致。"""

    def test_cancel_stops_owned_worker_and_task_stays_canceled(self):
        t, no, rid, p = self.started_task(H_WORK=30)
        pgid = self.worker(no)["pgid"]
        self.store.cancel_task(t, by="user", reason="不做了")
        res = sched.cancel(self.store, send=True, wait=10)
        self.assertEqual([(c["run"], c["action"], c["result"]) for c in res], [(rid, "signal", "stopped")], res)
        p.communicate(timeout=30)
        o = self.ledger_order(no)
        self.assertEqual((o["status"], o["exit_code"]), ("failed", -signal.SIGTERM))
        self.store.sync_orders()
        self.assertEqual((self.store.get_run(rid)["status"], self.store.get_task(t)["status"]), ("failed", "canceled"))
        kinds = [e["kind"] for e in self.store.events("run", rid)]
        self.assertEqual(kinds.count("run.cancel_signal"), 1)
        self.assertIn("run.result_not_applied", kinds)
        self.assertEqual(sched.cancel(self.store, send=True), [])         # 已停，不再是 cancel_pending
        with self.assertRaises(ProcessLookupError):
            os.killpg(pgid, 0)

    def test_unverified_or_foreign_processes_never_signalled(self):
        p = sleeper()
        self.addCleanup(lambda: (p.kill(), p.wait()))
        g = self.goal()
        for i, worker in enumerate([{**ident_of(p), "pgid": p.pid, "sid": p.pid},                       # 命令行对不上
                                    {**ident_of(p), "started": "Mon Jan  1 00:00:00 2001"},           # PID 复用
                                    None]):                                                           # 没有身份
            t = self.task(g, f"t{i}")
            no = VitalTests.fake_running(self, worker)
            self.store.start_run(t, agent="claude", by="hermes", order_no=no)
            self.store.cancel_task(t, by="user")
        res = sched.cancel(self.store, send=True, wait=0.5)
        self.assertEqual([c["action"] for c in res], ["advice"] * 3, res)
        self.assertIsNone(p.poll())                                        # 那个不相干的进程还活着
        self.assertEqual([e for e in self.store.events() if e["kind"] == "run.cancel_signal"], [])

    def test_cancel_racing_natural_completion(self):
        t, no, rid, p = self.started_task(H_WORK=0.5)
        self.store.cancel_task(t, by="user")
        p.communicate(timeout=30)                                          # 车间先自然跑完
        res = sched.cancel(self.store, send=True)
        self.assertEqual([(c["vital"], c["action"]) for c in res], [("finished", "none")])
        self.store.sync_orders()
        self.store.sync_orders()                                           # 同一回执反复输入
        self.assertEqual((self.store.get_run(rid)["status"], self.store.get_task(t)["status"]), ("done", "canceled"))
        self.assertEqual([e for e in self.store.events() if e["kind"] == "run.cancel_signal"], [])

    def test_worker_ignoring_sigterm_is_reported_not_killed(self):
        t, no, rid, p = self.started_task(H_WORK=30, H_IGNORE_TERM=1)
        self.store.cancel_task(t, by="user")
        res = sched.cancel(self.store, send=True, wait=1)
        self.assertEqual(res[0]["result"], "still_running")
        self.assertIn("不升级 SIGKILL", res[0]["advice"])
        self.assertEqual(sched.check_identity(self.worker(no)), "match")

    def test_production_dir_refuses_signals_and_cli_only_advises(self):
        with ts.Store(self.root / "p.db", dispatch_dir=Path.home() / ".hermes/dispatch") as s:
            with self.assertRaises(ts.NotAuthorized):
                sched.cancel(s, send=True)
        t, no, rid, p = self.started_task(H_WORK=30)
        self.store.cancel_task(t, by="user")
        env = {**os.environ, "DT_STATE_DB": str(self.db), "DT_DISPATCH": str(self.ledger)}
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "twin_scheduler.py"), "cancel"], env=env,
                           capture_output=True, text=True, timeout=60)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["cancels"][0]["action"], "advice")
        self.assertIn("SIGTERM", out["cancels"][0]["advice"])
        self.assertEqual(sched.check_identity(self.worker(no)), "match")    # 命令行没有发信号


class RetryTests(Base):
    """S5 / A08：只有已知可恢复、没到上限、进程已退出、不重复的技术失败才出重试建议；decision 阻塞从不出建议。"""

    def failed_task(self, receipt=FAIL_RECEIPT, ledger_status="failed", retries=0, kind="technical"):
        t = self.task(self.goal(f"p{len(self.store.list_goals())}"))
        no = self.order(status="new")
        self.store.start_run(t, agent="claude", by="hermes", order_no=no)
        (self.ledger / no / "out").mkdir()
        (self.ledger / no / "out" / "receipt.json").write_text(json.dumps(receipt))
        o = json.loads((self.ledger / no / "order.json").read_text())
        (self.ledger / no / "order.json").write_text(json.dumps({**o, "status": "failed"}))
        self.store.sync_orders()
        if ledger_status != "failed":
            (self.ledger / no / "order.json").write_text(json.dumps({**o, "status": ledger_status}))
        if retries:
            self.store.conn.execute("UPDATE tasks SET retries=? WHERE id=?", (retries, t))
        self.assertEqual(self.store.get_task(t)["blocked_kind"], kind)
        return t

    def test_classification(self):
        ok = self.failed_task()
        timeout = self.failed_task({**FAIL_RECEIPT, "timed_out": True, "worker_error": None})
        exhausted = self.failed_task(retries=2)
        incomplete = self.failed_task({"worker_error": "x"})
        decision = self.failed_task({**FAIL_RECEIPT, "need_decision": "选 A 还是 B"}, kind="decision")
        still_running = self.failed_task(ledger_status="running")   # 放最后：之后不再对账（对账会把它当续跑）
        d = self.by_task(sched.decide(self.store, max_running=10))
        self.assertEqual(d[ok]["reason"], "retry_suggested")
        self.assertEqual(d[ok]["evidence"]["failure"], "worker_failed")
        self.assertEqual(d[ok]["evidence"]["not_before"], "2000-01-01T09:10:00+08:00")
        self.assertEqual((d[timeout]["reason"], d[timeout]["evidence"]["failure"]), ("retry_suggested", "timeout"))
        self.assertEqual(d[exhausted]["reason"], "retry_exhausted")
        self.assertEqual((d[incomplete]["reason"], d[incomplete]["evidence"]["failure"]), ("blocked_technical", "unknown"))
        self.assertEqual(d[still_running]["reason"], "blocked_technical")
        self.assertEqual(d[decision]["reason"], "blocked_decision")
        self.assertTrue(all(v["decision"] != "dispatch" for v in d.values()))   # 建议不等于派单

    def test_repeated_same_failure_stops_suggesting(self):
        t = self.failed_task()
        self.store.retry_task(t, by="hermes")
        no = self.order(status="failed")
        (self.ledger / no / "out").mkdir()
        (self.ledger / no / "out" / "receipt.json").write_text(json.dumps(FAIL_RECEIPT))
        self.store.start_run(t, agent="claude", by="hermes", order_no=no)
        self.store.sync_orders()
        d = self.by_task(sched.decide(self.store, max_running=10))[t]
        self.assertEqual(d["reason"], "retry_repeated", d)

    def test_stale_run_is_not_retryable(self):
        t = self.task(self.goal())
        self.store.start_run(t, agent="claude", by="hermes")        # 没有工单号的 Run
        self.t += dt.timedelta(seconds=ts.STALE_AFTER + 1)
        self.store.recover()
        d = self.by_task()[t]
        self.assertEqual((d["reason"], d["evidence"]["failure"]), ("blocked_technical", "lost"))


class ReportTests(Base):
    """S6 / A09：只报错误和要常浩决定的；同一情形只报一次（去重在状态库里，重启后仍有效）；没新情况就安静。"""

    def test_report_once_then_quiet_then_new(self):
        g = self.goal()
        a = self.task(g, "a")
        self.store.block_task(a, by="hermes", kind="decision", reason="要选方案")
        b = self.task(g, "b")                                              # 普通可派的任务：不报
        first = sched.report(self.store)
        self.assertEqual([(f["level"], f["object"], f["reason"]) for f in first], [("needs_user", a, "blocked_decision")])
        self.assertEqual(sched.report(self.store), first)                  # 没 ack 就还会报（至少一次）
        sched.ack(self.store, first)
        sched.ack(self.store, first)
        self.assertEqual(sched.report(self.store), [])
        self.store.close()
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()
        self.assertEqual(sched.report(self.store), [])                     # 重启后去重仍有效
        sched.claim(self.store, b, "S-1", owner={"pid": 0})
        self.store.claim_close("S-1", "unsafe", note="归属证明不了")
        new = sched.report(self.store)
        self.assertEqual([(f["level"], f["object"], f["reason"]) for f in new], [("error", b, "claim_unsafe")])

    def test_cli_quiet_prints_nothing_when_nothing_new(self):
        a = self.task(self.goal())
        self.store.block_task(a, by="hermes", kind="decision", reason="要选方案")
        self.store.close()
        env = {**os.environ, "DT_STATE_DB": str(self.db), "DT_DISPATCH": str(self.ledger)}
        cmd = [sys.executable, "-B", str(SCRIPTS / "twin_scheduler.py"), "report", "--quiet"]
        r1 = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(json.loads(r1.stdout)["findings"][0]["object"], a)
        r2 = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual((r2.returncode, r2.stdout), (0, ""))
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()


if __name__ == "__main__":
    unittest.main()
