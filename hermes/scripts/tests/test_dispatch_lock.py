"""隔离自测：dispatch.py 的派单锁、进程身份与调度关联键（B2.2 S2 / S3 采集 / A03 / A10）。

用 dispatch_harness.py 在临时目录里以真实子进程跑 dispatch.py（只换根目录、名册与 worker 路径），可注入延迟把竞态窗口拉大；
车间是 fake_worker_proc.py。不碰 ~/.hermes、真实工单、Git 远端或外部服务。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_dispatch_lock.py -v
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HARNESS = HERE / "dispatch_harness.py"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-dispatch-lock-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "repo").mkdir()
        (self.root / "roster.json").write_text(json.dumps({"projects": [
            {"id": "fixture", "name": "夹具", "path": str(self.root / "repo"), "main": "main", "write_allowed": False},
            {"id": "other", "name": "另一个", "path": str(self.root / "repo"), "main": "main", "write_allowed": False}]}))
        self.task = self.root / "task.md"
        self.task.write_text("# 目标：隔离测试\n", encoding="utf-8")
        self.ledger = self.root / "dispatch"

    def env(self, **kw):
        e = {k: v for k, v in os.environ.items() if not k.startswith(("H_", "DT_STATE_NOTIFY"))}
        e.update(H_ROOT=str(self.root), DT_STATE_DB=str(self.root / "state" / "twin-state.db"))   # 不碰默认正式库路径
        e.update({k: str(v) for k, v in kw.items()})
        return e

    def spawn(self, *args, **kw):
        return subprocess.Popen([sys.executable, "-B", str(HARNESS), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, env=self.env(**kw))

    def result(self, p, timeout=60):
        out, err = p.communicate(timeout=timeout)
        lines = out.strip().splitlines()
        return p.returncode, (json.loads(lines[-1]) if lines else {"stderr": err[-500:]})

    def run_cmd(self, *args, **kw):
        return self.result(self.spawn(*args, **kw))

    def new(self, project="fixture", **kw):
        code, out = self.run_cmd("new", "--project", project, "--tool", "claude", "--mode", "read", "--task-file",
                                 str(self.task), **kw)
        self.assertEqual(code, 0, out)
        return out["order"]["no"]

    def order(self, no):
        return json.loads((self.ledger / no / "order.json").read_text())

    def race(self, argvs, **kw):
        go = self.root / f"go-{time.monotonic_ns()}"
        procs = [self.spawn(*argv, H_GO=go, **kw) for argv in argvs]
        time.sleep(0.4)
        go.touch()
        return [self.result(p) for p in procs]

    def worker_runs(self):
        f = self.root / "worker-runs.log"
        return f.read_text().splitlines() if f.exists() else []


class LockTests(Base):
    def test_parallel_new_gets_distinct_numbers(self):
        res = self.race([["new", "--project", "fixture", "--tool", "claude", "--mode", "read", "--task-file", str(self.task)]] * 4,
                        H_DELAY=0.3)
        self.assertEqual([c for c, _ in res], [0, 0, 0, 0], res)
        nos = [o["order"]["no"] for _, o in res]
        self.assertEqual(len(set(nos)), 4, nos)
        self.assertEqual(sorted(p.name for p in self.ledger.glob("D*")), sorted(nos))

    def test_parallel_run_same_project_only_one_runs(self):
        a, b = self.new(), self.new()
        res = self.race([["run", a], ["run", b]], H_DELAY=0.3, H_WORK=1)
        codes = sorted(c for c, _ in res)
        self.assertEqual(codes, [0, 1], res)
        self.assertTrue(any(o.get("busy") for c, o in res if c == 1), res)
        self.assertEqual(sorted(self.order(n)["status"] for n in (a, b)), ["done", "queued"])
        self.assertEqual(len(self.worker_runs()), 1)

    def test_same_order_run_twice_only_once(self):
        a = self.new()
        res = self.race([["run", a], ["run", a]], H_DELAY=0.3, H_WORK=1)
        self.assertEqual(sorted(c for c, _ in res), [0, 1], res)
        self.assertEqual(len(self.worker_runs()), 1)
        self.assertEqual(self.order(a)["status"], "done")

    def test_lock_wait_is_bounded_and_does_nothing(self):
        self.ledger.mkdir()
        with open(self.ledger / ".dispatch.lock", "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            code, out = self.run_cmd("new", "--project", "fixture", "--tool", "claude", "--mode", "read",
                                     "--task-file", str(self.task), H_LOCK_WAIT=0.3)
        self.assertEqual(code, 1, out)
        self.assertIn("派单锁", out["msg"])
        self.assertEqual(list(self.ledger.glob("D*")), [])


class IdentityTests(Base):
    def test_run_records_dispatch_identity_and_worker_runs_in_own_session(self):
        a = self.new()
        code, out = self.run_cmd("run", a)
        self.assertEqual(code, 0, out)
        o = self.order(a)
        self.assertEqual(o["status"], "done")
        self.assertIsInstance(o["dispatch_pid"], int)
        self.assertTrue(o["run_host"] and o["run_started"])
        w = json.loads((self.ledger / a / "out" / "worker.json").read_text())
        self.assertEqual(w["pid"], w["pgid"])            # start_new_session：车间是自己进程组和会话的头
        self.assertEqual(w["pid"], w["sid"])
        self.assertNotEqual(w["pid"], o["dispatch_pid"])
        self.assertTrue(w["started"])
        self.assertEqual(Path(w["out"]), (self.ledger / a / "out").resolve())

    def test_write_identity_matches_ps(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("run_worker", HERE.parent / "run_worker.py")
        rw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rw)
        out = self.root / "out"
        out.mkdir()
        ident = rw.write_identity(out)
        ps = subprocess.run(["ps", "-o", "lstart=", "-p", str(os.getpid())], capture_output=True, text=True).stdout
        self.assertEqual(ident["started"], " ".join(ps.split()))
        self.assertEqual(json.loads((out / "worker.json").read_text())["pid"], os.getpid())


class SchedKeyTests(Base):
    def test_new_with_sched_key_writes_field_and_marker(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("twin_state", HERE.parent / "twin_state.py")
        ts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ts)
        with ts.Store(self.root / "state" / "twin-state.db") as s:      # 调度单要先有仍是 claimed 的预留（F1）
            g = s.create_goal("隔离", by="user", status="authorized", project="fixture")
            t = s.create_task(g["id"], "t", by="hermes", owner="claude")["id"]
            s.set_ready(t, by="hermes")
            s.claim_task(t, "S20000101-T1-abc", {"pid": 0}, lambda st: {"decision": "dispatch", "task_id": t,
                                                                        "evidence": {"project": "fixture"}})
        code, out = self.run_cmd("new", "--project", "fixture", "--tool", "claude", "--mode", "read", "--task-file",
                                 str(self.task), "--sched-key", "S20000101-T1-abc")
        self.assertEqual(code, 0, out)
        no = out["order"]["no"]
        self.assertEqual(self.order(no)["sched_key"], "S20000101-T1-abc")
        self.assertEqual((self.ledger / no / "sched_key").read_text(), "S20000101-T1-abc")
        self.assertEqual(self.order(no)["tool_source"], "调度器任务负责方")

    def test_manual_new_unchanged(self):
        no = self.new()
        o = self.order(no)
        self.assertNotIn("sched_key", o)
        self.assertFalse((self.ledger / no / "sched_key").exists())
        self.assertEqual(set(o), {"no", "project", "tool", "tool_source", "mode", "status", "created", "cwd", "updated"})
        self.assertEqual(o["tool_source"], "点名")   # AI 接入留痕：手工带 --tool 记为点名

    def test_bad_sched_key_rejected_before_numbering(self):
        code, out = self.run_cmd("new", "--project", "fixture", "--tool", "claude", "--mode", "read", "--task-file",
                                 str(self.task), "--sched-key", "../x")
        self.assertEqual(code, 1, out)
        self.assertEqual(list(self.ledger.glob("D*")), [])


if __name__ == "__main__":
    unittest.main()
