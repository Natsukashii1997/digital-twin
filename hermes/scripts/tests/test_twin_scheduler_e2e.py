"""隔离端到端：B2.2 任务书 S7 的八个场景。真实 dispatch.py（dispatch_harness.py 换成临时根目录）+ 假车间子进程 +
临时状态库；不碰 ~/.hermes、正式状态库、真实工单、模型或外部服务。

场景与测试的对照（其余散在各文件里的覆盖见 docs/reviews/2026-10-29-B2.2-交付与测试报告.md 第 3 节）：
  1 依赖解除 → ready → 真实开单 → 关联 → 回执 → review，未验收不能 complete      test_s1_full_path_stops_at_review
  2 未授权 / decision 阻塞 / 依赖未完成绝不开单                                     test_s2_never_dispatches_ineligible
  3 两个调度器进程同时 tick 同一任务，只开一张单                                     test_s3_two_tick_processes_one_order
  4 开单后调度器被杀 → 重启按关联键恢复                                             test_twin_scheduler.CrashTests
  5 心跳停了进程还活着不重试不终止；进程已退出无回执按不确定处理                     test_twin_scheduler_life.VitalTests
  6 取消与自然完成竞争、不误杀非所属进程                                             test_twin_scheduler_life.CancelTests
  7 同一事件 / 回执 / 恢复反复输入不改状态、不重建工单                               test_s7_repeated_inputs_are_idempotent
  8 重启恢复、审计、脱敏与回退                                                       test_s8_restart_audit_redaction_rollback
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_scheduler_e2e.py -v
"""
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from test_twin_scheduler import OK_RECEIPT, sched, ts, SCRIPTS
from test_twin_scheduler_life import Life, HARNESS

TICKER = r"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import twin_scheduler as sched, twin_state as ts
db, ledger, harness, go = sys.argv[2:6]
while not Path(go).exists():
    time.sleep(0.005)
with ts.Store(db, dispatch_dir=ledger) as s:
    out = sched.tick(s, sched.DispatchExecutor("read", argv=[sys.executable, "-B", harness]))
print(json.dumps({k: len(v) for k, v in out.items()}))
"""


class EndToEnd(Life):
    def setUp(self):
        super().setUp()
        self.patch_env = mock.patch.dict(os.environ, {"H_ROOT": str(self.root)})
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        for k in ("DT_STATE_NOTIFY", "DT_SCHED_ENABLE"):
            os.environ.pop(k, None)

    def executor(self):
        return sched.DispatchExecutor("read", argv=[sys.executable, "-B", str(HARNESS)])

    def orders(self):
        return sorted(p.parent.name for p in self.ledger.glob("D*/order.json"))

    def test_s1_full_path_stops_at_review(self):
        g = self.goal("alpha")
        dep = self.task(g, "前置", owner="hermes")
        t = self.task(g, "主任务", depends_on=[dep])
        self.assertEqual(self.store.get_task(t)["blocked_kind"], "dependency")
        # 前置任务走完（人工执行路径），依赖解除由 recover 放行
        r0 = self.store.start_run(dep, agent="hermes", by="hermes")["run"]
        self.store.receive_receipt(r0["id"], OK_RECEIPT)
        self.store.accept_task(dep, by="codex", basis="隔离验收", cites=[r0["id"]])
        self.store.complete_task(dep, by="hermes")
        self.assertIn(t, self.store.recover()["released"])
        out = sched.tick(self.store, self.executor(), self.ledger)
        self.assertEqual((len(out["linked"]), self.orders()), (1, [out["linked"][0]["order_no"]]), out)
        no = out["linked"][0]["order_no"]
        o = self.ledger_order(no)
        self.assertEqual((o["status"], o["sched_key"]), ("new", out["linked"][0]["sched_key"]))   # 只开单，没开跑
        task = self.store.get_task(t)
        run = self.store.get_run(task["current_run_id"])
        self.assertEqual((task["status"], run["order_no"]), ("running", no))
        p = self.drun(no)              # 开跑是确认门之后的人工动作，这里模拟
        p.communicate(timeout=60)
        self.store.sync_orders()
        task = self.store.get_task(t)
        self.assertEqual(task["status"], "review")
        with self.assertRaises(ts.EvidenceMissing):
            self.store.complete_task(t, by="hermes")                   # 执行完成 ≠ 验收通过
        with self.assertRaises(ts.NotAuthorized):
            self.store.accept_task(t, by="claude", basis="自己验自己", cites=[run["id"]])
        self.assertEqual(self.store.get_task(t)["status"], "review")
        self.assertEqual(sched.decide(self.store)["counts"]["dispatch"], 0)

    def test_s2_never_dispatches_ineligible(self):
        self.task(self.goal("alpha", authorized=False), "未授权", ready=False)
        d = self.task(self.goal("beta"), "等决定")
        self.store.block_task(d, by="hermes", kind="decision", reason="选方案")
        g = self.goal("gamma")
        root = self.task(g, "前置", owner="hermes")
        self.task(g, "依赖没完成", depends_on=[root])
        for _ in range(3):
            out = sched.tick(self.store, self.executor(), self.ledger, limit=10)
            self.assertEqual(out["claimed"], [], out)
        self.assertEqual((self.orders(), self.store.claims()), ([], []))

    def test_s3_two_tick_processes_one_order(self):
        t = self.task(self.goal("alpha"))
        go = self.root / "go"
        env = {**os.environ, "H_ROOT": str(self.root)}
        procs = [subprocess.Popen([sys.executable, "-B", "-c", TICKER, str(SCRIPTS), str(self.db), str(self.ledger),
                                   str(HARNESS), str(go)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                 for _ in range(3)]
        time.sleep(0.4)
        go.touch()
        res = [json.loads(p.communicate(timeout=120)[0].strip().splitlines()[-1]) for p in procs]
        self.assertEqual(sum(r["linked"] for r in res), 1, res)
        self.assertEqual(sum(r["claimed"] for r in res), 1, res)
        self.assertEqual(len(self.orders()), 1)
        self.assertEqual(len(self.store.list_runs(task_id=t)), 1)

    def test_s7_repeated_inputs_are_idempotent(self):
        t = self.task(self.goal("alpha"))
        sched.tick(self.store, self.executor(), self.ledger)
        no = self.orders()[0]
        self.drun(no).communicate(timeout=60)
        self.store.sync_orders()
        snapshot = (self.store.get_task(t), self.store.list_runs(), self.store.claims())
        n = len(self.store.events())
        for _ in range(3):
            self.assertEqual(sched.tick(self.store, self.executor(), self.ledger)["claimed"], [])
            self.assertEqual(sched.recover_claims(self.store, self.ledger), {})
            self.store.sync_orders()
            self.store.reconcile()
            self.assertEqual(sched.cancel(self.store), [])
            run = self.store.get_task(t)["current_run_id"]
            try:   # 同一回执从另一条路再送：第一次拒绝（已是 done），之后按幂等键识别为重复；状态都不动
                self.assertFalse(self.store.receive_receipt(
                    run, json.loads((self.ledger / no / "out" / "receipt.json").read_text()))["applied"])
            except ts.IllegalTransition:
                pass
        self.assertEqual((self.store.get_task(t), self.store.list_runs(), self.store.claims()), snapshot)
        self.assertEqual(self.orders(), [no])
        new = self.store.events()[n:]
        self.assertFalse([e["kind"] for e in new if e["applied"] and e["kind"] != "recover"], new)   # 只多了留痕和拒绝记录

    def test_s8_restart_audit_redaction_rollback(self):
        t = self.task(self.goal("alpha"))
        backup_v3 = self.store.backup()
        sched.tick(self.store, self.executor(), self.ledger)
        self.store.close()
        # 重启：新进程只靠状态库 + 台账就能看到同样的状态（不依赖上一轮会话记忆）
        env = {**os.environ, "DT_STATE_DB": str(self.db), "DT_DISPATCH": str(self.ledger),
               "DT_OWNER_OPEN_ID": "ou_sentinel_should_not_leak", "DT_OWNER_CHAT_ID": "oc_sentinel_should_not_leak"}
        outs = []
        for cmd in ("plan", "observe", "recover", "cancel", "report"):
            r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "twin_scheduler.py"), cmd], env=env, capture_output=True,
                               text=True, timeout=60)
            outs.append(r.stdout + r.stderr)
        plan = json.loads(outs[0].strip().splitlines()[-1])
        self.assertEqual([(d["task_id"], d["reason"]) for d in plan["decisions"]], [(t, "running")])
        self.assertFalse([o for o in outs if "sentinel" in o])                   # 身份值不会出现在任何输出里
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()
        kinds = [e["kind"] for e in self.store.events("task", t)]
        for k in ("sched.claim", "sched.ordered", "sched.linked"):
            self.assertIn(k, kinds)                                              # 审计：预留每一步都有事件
        self.store.close()
        # 回退：回到 B2.1 的程序（只认到 v2）会拒绝 v3 库，不会误改；要回退就恢复升级前的备份
        with mock.patch.object(ts, "MIGRATIONS", ts.MIGRATIONS[:2]):
            with self.assertRaisesRegex(ts.StateError, "比程序支持的 2 新"):
                ts.Store(self.db).open()
        self.store = ts.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()
        self.store.restore(backup_v3)
        self.assertEqual((self.store.claims(), self.store.get_task(t)["status"]), ([], "ready"))


if __name__ == "__main__":
    unittest.main()
