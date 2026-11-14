"""隔离自测：托管账本（custody_ledger.py，#133）。

库（DT_STATE_DB）、原文目录（DT_CUSTODY_DIR）放临时目录；托管准入用桩 status_reader（真 custody.read_status 的用法见
test_custody 的 P1-08 用例）。并发预占用多个真进程抢同一个库。不联网、不碰 ~/Library/Application Support/DigitalTwin。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_custody_ledger.py -v
"""
import datetime as dt
import json
import multiprocessing as mp
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import custody as C  # noqa: E402
import custody_ledger as L  # noqa: E402

TZ = C.TZ
T0 = dt.datetime(2026, 11, 14, 23, 40, tzinfo=TZ)
SID = "c-20261114-233000-0123456789ab"
SID2 = "c-20261115-233000-ba9876543210"
LIMITS = {"reply": {"allowed": True, "per_issue": 2, "per_session": 10}, "merge": {"allowed": True, "per_session": 5}}


def status(state="on", session=SID, limits=None, eligible=None):
    """custody.read_status 的形状（只取账本用到的字段）。"""
    ok = state == "on" if eligible is None else eligible
    return {"state": state, "session": {"id": session} if session else None,
            "policy": {"ok": True, "limits": limits or LIMITS},
            "admission": {"eligible": ok, "reasons": [] if ok else [f"托管不是开着（{state}）"]}}


def race_worker(db, raw, start, out, n):
    """子进程：所有进程到齐后一起出发，抢预占这次托管的最后一个合并额度。
    数完额度后故意停 0.3 秒再写（拉长「数」和「写」之间的空档）：没有写锁保护时几个进程都会数到「还剩一个」。"""
    import time
    os.environ["DT_CUSTODY_DIR"] = raw
    lim = {"reply": LIMITS["reply"], "merge": {"allowed": True, "per_session": 3}}
    real_used = L.used

    def slow_used(*a):
        n_used = real_used(*a)
        time.sleep(0.3)
        return n_used

    L.used = slow_used
    start.wait()
    try:
        with L.TS.Store(db) as st:
            d = L.issue(st, session_id=SID, project="dt-sandbox", action="merge", basis="合并官建议合并",
                        target={"repo": "o/dt-sandbox", "pr": 100 + n, "head": "b" * 40}, revocability="revertible",
                        expires_at=T0 + dt.timedelta(hours=1), now=T0, status_reader=lambda t: status(limits=lim))
        out.put(("ok", d["id"]))
    except L.LedgerError as e:
        out.put((e.code, str(e)))
    except Exception as e:  # noqa: BLE001
        out.put(("crash", repr(e)))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-ledger-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "twin-state.db"
        self.raw = self.root / "custody"
        p = mock.patch.dict(os.environ, {"DT_STATE_DB": str(self.db), "DT_CUSTODY_DIR": str(self.raw)})
        p.start()
        self.addCleanup(p.stop)
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()
        self.addCleanup(self.st.close)
        self.reader = lambda t: status()

    def reply(self, issue="DTS-7", session=SID, now=T0, **kw):
        kw.setdefault("status_reader", self.reader)
        return L.issue(self.st, session_id=session, project="dt-sandbox", action="reply", target={"issue": issue, "repo": "o/dt-sandbox"},
                       question="要不要把超时调到 30 秒", summary="调到 30 秒", basis="沙盒范围内的参数取舍",
                       revocability="correctable", expires_at=now + dt.timedelta(minutes=30), now=now,
                       received={"issue": issue, "comment": "agent 问：超时调多少？"}, reply="调到 30 秒〔托管·猛鬼兽代发〕", **kw)

    def merge(self, pr=7, **kw):
        kw.setdefault("status_reader", self.reader)
        return L.issue(self.st, session_id=SID, project="dt-sandbox", action="merge",
                       target={"repo": "o/dt-sandbox", "pr": pr, "head": "a" * 40}, summary=f"合并 PR #{pr}",
                       basis="合并官建议合并，CI 通过", revocability="revertible", expires_at=T0 + dt.timedelta(hours=1),
                       now=T0, **kw)

    def rejects(self, code, fn, *a, **kw):
        with self.assertRaises(L.LedgerError) as cm:
            fn(*a, **kw)
        self.assertEqual(cm.exception.code, code, str(cm.exception))
        return str(cm.exception)

    def quota_states(self, did):
        return sorted((q["kind"], q["state"]) for q in L.quotas(self.st, did))

    def events(self, did):
        return [e["kind"] for e in self.st.events(object_type=L.OBJ, object_id=did)]


class IssueTests(Base):
    def test_reply_records_everything(self):
        d = self.reply()
        self.assertRegex(d["id"], L.DECISION_ID)
        self.assertEqual((d["status"], d["disposition"], d["revocability"]), ("issued", "open", "correctable"))
        self.assertEqual((d["issue_key"], d["target"]["issue"]), ("dt-sandbox:DTS-7", "DTS-7"))
        self.assertEqual(self.quota_states(d["id"]), [("issue", "reserved"), ("reply", "reserved")])
        self.assertEqual(self.events(d["id"]), ["custody.decision.issue"])
        raw = Path(d["raw_path"])
        self.assertEqual(raw.parent, self.raw / SID)
        self.assertEqual(oct(raw.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(raw.parent.stat().st_mode & 0o777), "0o700")
        self.assertEqual(L.read_raw(d)["reply"], "调到 30 秒〔托管·猛鬼兽代发〕")

    def test_suspend_needs_no_admission_and_takes_no_quota(self):
        d = L.issue(self.st, session_id=SID, project="dt-sandbox", action="suspend", target={"issue": "DTS-9"},
                    question="要改规则集", summary="挂起：D 档", basis="改规则集属于 D 档", revocability="irrevocable",
                    now=T0, status_reader=lambda t: status("draining", eligible=False))
        self.assertEqual((d["status"], L.quotas(self.st, d["id"])), ("suspended", []))

    def test_input_rejections_write_nothing(self):
        cases = [
            dict(action="reply", revocability="irrevocable"),                 # 撤不回的只能挂起
            dict(action="merge", revocability="correctable"),
            dict(action="sell", revocability="correctable"),
        ]
        for kw in cases:
            with self.subTest(kw):
                self.rejects("BadInput", L.issue, self.st, session_id=SID, project="p", target={"issue": "x", "repo": "r", "pr": 1, "head": "h"},
                             basis="b", expires_at=T0 + dt.timedelta(hours=1), now=T0, status_reader=self.reader, **kw)
        self.rejects("BadInput", L.issue, self.st, session_id=SID, project="p", action="reply", target={"issue": "x"}, basis="b",
                     revocability="correctable", expires_at=T0 - dt.timedelta(minutes=1), now=T0, status_reader=self.reader)
        self.rejects("BadInput", L.issue, self.st, session_id=SID, project="p", action="merge", target={"repo": "r"}, basis="b",
                     revocability="revertible", expires_at=T0 + dt.timedelta(hours=1), now=T0, status_reader=self.reader)
        self.rejects("BadInput", self.reply, cites=["d-261114-ffffff"])          # 引用不存在的决定
        self.rejects("BadInput", L.issue, self.st, session_id="c-坏的", project="p", action="suspend", target={}, basis="b",
                     revocability="irrevocable", now=T0)
        self.assertEqual(L.decisions(self.st), [])
        self.assertFalse(any(self.raw.rglob("*.json")))                      # 拒绝时原文也不留

    def test_cites_existing_decision(self):
        a = self.reply()
        b = self.reply(issue="DTS-8", cites=[a["id"]])
        self.assertEqual(b["cites"], [a["id"]])

    def test_admission_and_policy_gates(self):
        self.rejects("NotAdmitted", self.reply, status_reader=lambda t: status("draining"))
        self.rejects("NotAdmitted", self.reply, status_reader=lambda t: status("frozen"))
        self.rejects("WrongSession", self.reply, status_reader=lambda t: status(session=SID2))
        self.rejects("NotAllowed", self.merge, status_reader=lambda t: status(limits={**LIMITS, "merge": {"allowed": False, "per_session": 5}}))
        self.rejects("NoLimits", self.reply, status_reader=lambda t: status(limits={"merge": LIMITS["merge"]}))
        self.assertEqual(L.decisions(self.st), [])

    def test_admission_read_inside_write_lock(self):
        """准入复核要在写锁里做：复核时别的连接写不进来。"""
        seen = []

        def reader(t):
            other = sqlite3.connect(str(self.db), timeout=0)
            try:
                other.execute("BEGIN IMMEDIATE")
                seen.append("没锁住")
            except sqlite3.OperationalError:
                seen.append("锁住了")
            finally:
                other.close()
            return status()

        self.reply(status_reader=reader)
        self.assertEqual(seen, ["锁住了"])


class QuotaTests(Base):
    def test_session_and_issue_limits(self):
        self.reply()
        self.reply()
        self.assertIn("子单 DTS-7", self.rejects("QuotaExhausted", self.reply))     # 每张子单 2 次
        self.reply(issue="DTS-8")
        lim = {**LIMITS, "reply": {"allowed": True, "per_issue": 2, "per_session": 3}}
        self.rejects("QuotaExhausted", self.reply, issue="DTS-9", status_reader=lambda t: status(limits=lim))

    def test_issue_limit_counts_across_sessions(self):
        self.reply()
        self.reply()
        self.rejects("QuotaExhausted", self.reply, session=SID2, status_reader=lambda t: status(session=SID2))

    def test_concurrent_reservation_only_one_wins(self):
        """规格 3.3.1 第 6 项：多个进程同时预占最后一个额度 → 只有一个成功。"""
        lim = {"reply": LIMITS["reply"], "merge": {"allowed": True, "per_session": 3}}
        self.merge(pr=1, status_reader=lambda t: status(limits=lim))
        self.merge(pr=2, status_reader=lambda t: status(limits=lim))
        self.st.close()
        ctx = mp.get_context("spawn")
        start, out = ctx.Barrier(6), ctx.Queue()
        procs = [ctx.Process(target=race_worker, args=(str(self.db), str(self.raw), start, out, n)) for n in range(6)]
        for p in procs:
            p.start()
        results = [out.get(timeout=60) for _ in procs]
        for p in procs:
            p.join(60)
        codes = sorted(r[0] for r in results)
        self.assertEqual(codes, ["QuotaExhausted"] * 5 + ["ok"], results)
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()
        self.assertEqual(L.used(self.st, "merge", SID), 3)
        self.assertEqual(len(L.decisions(self.st)), 3)
        self.assertEqual(len(list((self.raw / SID).glob("*.json"))), 3)       # 输掉的进程不留原文

    def test_unknown_freezes_and_does_not_release(self):
        d = self.merge()
        L.begin(self.st, d["id"], now=T0, status_reader=self.reader)
        L.finish(self.st, d["id"], "unknown", now=T0)
        self.assertEqual(self.quota_states(d["id"]), [("merge", "frozen")])
        self.assertEqual(L.used(self.st, "merge", SID), 1)                    # 冻结的照样占着
        L.reconcile(self.st, d["id"], "unreconciled", "PR 状态查不到", now=T0)
        self.assertEqual(self.quota_states(d["id"]), [("merge", "frozen")])
        L.reconcile(self.st, d["id"], "unreconciled", "又查了一次，还是查不到", now=T0)   # 再核一次，留痕
        L.reconcile(self.st, d["id"], "done", "gh pr view：merged，合并提交 abc", now=T0)
        self.assertEqual(self.quota_states(d["id"]), [("merge", "used")])
        self.assertEqual(L.get(self.st, d["id"])["outcome"]["reconcile"], "gh pr view：merged，合并提交 abc")

    def test_unknown_reconciled_not_done_releases(self):
        d = self.merge()
        L.begin(self.st, d["id"], now=T0, status_reader=self.reader)
        L.finish(self.st, d["id"], "unknown", now=T0)
        L.reconcile(self.st, d["id"], "failed", "PR 仍是 open，HEAD 没变", now=T0)
        self.assertEqual(self.quota_states(d["id"]), [("merge", "released")])
        self.assertEqual(L.used(self.st, "merge", SID), 0)

    def test_void_releases(self):
        d = self.reply()
        L.void(self.st, d["id"], "出口闸改判挂起", now=T0)
        got = L.get(self.st, d["id"])
        self.assertEqual((got["status"], got["void_reason"]), ("void", "出口闸改判挂起"))
        self.assertEqual(self.quota_states(d["id"]), [("issue", "released"), ("reply", "released")])
        self.reply()
        self.reply()                                                          # 释放的额度能再用（每张子单 2 次）

    def test_failed_releases_done_uses(self):
        a, b = self.reply(), self.reply(issue="DTS-8")
        for d, r in ((a, "failed"), (b, "done")):
            L.begin(self.st, d["id"], now=T0, status_reader=self.reader)
            L.finish(self.st, d["id"], r, outcome={"comment_id": "c1"}, now=T0)
        self.assertEqual(self.quota_states(a["id"]), [("issue", "released"), ("reply", "released")])
        self.assertEqual(self.quota_states(b["id"]), [("issue", "used"), ("reply", "used")])
        self.assertEqual(L.get(self.st, b["id"])["outcome"], {"comment_id": "c1"})


class TransitionTests(Base):
    def test_status_moves(self):
        d = self.reply()
        self.rejects("BadTransition", L.finish, self.st, d["id"], "done", now=T0)        # 没 begin 不能记结果
        self.rejects("BadTransition", L.reconcile, self.st, d["id"], "done", "x", now=T0)
        L.begin(self.st, d["id"], now=T0, status_reader=self.reader)
        self.rejects("BadTransition", L.begin, self.st, d["id"], now=T0, status_reader=self.reader)   # 不重发
        self.rejects("BadTransition", L.void, self.st, d["id"], "排空", now=T0)          # 在途的不能作废
        L.finish(self.st, d["id"], "done", now=T0)
        for fn, a in ((L.finish, ("failed",)), (L.reconcile, ("failed", "x")), (L.void, ("x",))):
            self.rejects("BadTransition", fn, self.st, d["id"], *a, now=T0)
        self.rejects("BadInput", L.reconcile, self.st, d["id"], "done", " ", now=T0)
        self.assertEqual(self.events(d["id"]), ["custody.decision.issue", "custody.decision.executing", "custody.decision.done"])

    def test_begin_rechecks_admission(self):
        d = self.reply()
        self.rejects("NotAdmitted", L.begin, self.st, d["id"], now=T0, status_reader=lambda t: status("draining"))
        self.assertEqual(L.get(self.st, d["id"])["status"], "issued")

    def test_begin_after_expiry_voids_and_releases(self):
        d = self.reply()
        later = T0 + dt.timedelta(minutes=31)
        self.rejects("Expired", L.begin, self.st, d["id"], now=later, status_reader=self.reader)
        got = L.get(self.st, d["id"])
        self.assertEqual((got["status"], got["void_reason"]), ("void", "过期"))
        self.assertEqual(self.quota_states(d["id"]), [("issue", "released"), ("reply", "released")])

    def test_executing_crash_reconciles(self):
        d = self.merge()
        L.begin(self.st, d["id"], now=T0, status_reader=self.reader)
        L.reconcile(self.st, d["id"], "failed", "进程没了；PR 仍 open", now=T0)
        self.assertEqual(self.events(d["id"])[-2:], ["custody.decision.unknown", "custody.decision.failed"])

    def test_disposition_moves(self):
        r, m = self.reply(), self.merge()
        self.rejects("InFlight", L.set_disposition, self.st, r["id"], "overturned", "不同意", now=T0)
        for d in (r, m):
            L.begin(self.st, d["id"], now=T0, status_reader=self.reader)
            L.finish(self.st, d["id"], "done", now=T0)
        self.rejects("NotOwner", L.set_disposition, self.st, r["id"], "overturned", "x", by="hermes", now=T0)
        self.rejects("BadTransition", L.set_disposition, self.st, r["id"], "corrected", "x", now=T0)   # 要先推翻
        self.rejects("BadInput", L.set_disposition, self.st, r["id"], "overturned", "", now=T0)
        L.set_disposition(self.st, r["id"], "overturned", "应该调到 60 秒", now=T0)
        self.rejects("BadTransition", L.set_disposition, self.st, r["id"], "overturned", "再推一次", now=T0)
        self.rejects("BadTransition", L.set_disposition, self.st, r["id"], "reverted", "x", now=T0)    # 回复不能回退
        L.set_disposition(self.st, r["id"], "corrected", "常浩评论「撤回 …」", by="hermes", now=T0)
        L.set_disposition(self.st, m["id"], "overturned", "不该合", now=T0)
        self.rejects("BadTransition", L.set_disposition, self.st, m["id"], "corrected", "x", now=T0)   # 合并不能纠正
        L.set_disposition(self.st, m["id"], "revert_failed", "有冲突", by="hermes", now=T0)
        L.set_disposition(self.st, m["id"], "reverted", "revert PR #9", by="hermes", now=T0)
        self.rejects("BadTransition", L.set_disposition, self.st, m["id"], "revert_failed", "x", by="hermes", now=T0)
        self.assertEqual(L.get(self.st, m["id"])["disposition"], "reverted")
        self.assertEqual(self.events(m["id"])[-3:], ["custody.decision.disposition.overturned",
                                                     "custody.decision.disposition.revert_failed",
                                                     "custody.decision.disposition.reverted"])

    def test_suspended_can_be_overturned(self):
        d = L.issue(self.st, session_id=SID, project="p", action="suspend", target={"issue": "DTS-1"}, basis="拿不准",
                    revocability="irrevocable", now=T0)
        L.set_disposition(self.st, d["id"], "overturned", "这个其实可以答", now=T0)


class DrainTests(Base):
    def test_drain_voids_issued_and_reports_unsettled(self):
        issued, sent, unknown = self.reply(), self.reply(issue="DTS-8"), self.merge()
        L.begin(self.st, sent["id"], now=T0, status_reader=self.reader)
        L.finish(self.st, sent["id"], "done", now=T0)
        L.begin(self.st, unknown["id"], now=T0, status_reader=self.reader)
        L.finish(self.st, unknown["id"], "unknown", now=T0)
        other = self.reply(session=SID2, issue="DTS-20", status_reader=lambda t: status(session=SID2))
        blockers = L.drain(self.st, SID, now=T0)
        self.assertEqual(len(blockers), 1)
        self.assertIn(unknown["id"], blockers[0])
        self.assertIn("结果不明", blockers[0])
        self.assertEqual(L.get(self.st, issued["id"])["status"], "void")
        self.assertEqual(L.get(self.st, issued["id"])["void_reason"], "托管排空")
        self.assertEqual(L.get(self.st, other["id"])["status"], "issued")         # 别的托管不动
        L.reconcile(self.st, unknown["id"], "done", "已合并", now=T0)
        self.assertEqual(L.drain(self.st, SID, now=T0), [])
        self.assertEqual(L.drain(self.st, SID, now=T0), [])                      # 幂等

    def test_drain_blocks_on_executing_and_inconsistent_quota(self):
        d = self.reply()
        L.begin(self.st, d["id"], now=T0, status_reader=self.reader)
        self.assertIn("进行中", L.drain(self.st, SID, now=T0)[0])
        L.finish(self.st, d["id"], "done", now=T0)
        self.st.conn.execute("UPDATE custody_quota SET state='reserved' WHERE decision_id=?", (d["id"],))
        self.assertIn("对不上", L.drain(self.st, SID, now=T0)[0])

    def test_drain_check_cli_is_readonly(self):
        d = self.reply()
        self.st.close()
        import subprocess
        r = subprocess.run([sys.executable, "-B", str(HERE.parent / "custody_ledger.py"), "drain-check", "--session", SID],
                           capture_output=True, text=True, env={**os.environ})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(d["id"], json.loads(r.stdout.splitlines()[-1])["msg"])
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()
        self.assertEqual(L.get(self.st, d["id"])["status"], "issued")


class RawRetentionTests(Base):
    def test_prune_after_90_days(self):
        d = self.reply()
        stale = self.raw / "c-20260801-000000-aaaaaaaaaaaa"
        stale.mkdir(parents=True)
        (stale / "d-260801-aaaaaa.json").write_text("{}")
        keep = self.raw / "policy.md"
        keep.write_text("x")
        odd = self.raw / "c-手写的"
        odd.mkdir()
        self.assertEqual(L.prune_raw(T0), ["c-20260801-000000-aaaaaaaaaaaa"])
        self.assertTrue(Path(d["raw_path"]).exists() and keep.exists() and odd.exists())
        self.assertEqual(L.prune_raw(T0 + dt.timedelta(days=91)), [SID])
        self.assertIsNone(L.read_raw(L.get(self.st, d["id"])))


class MigrationTests(Base):
    def test_v3_db_migrates_to_v4_untouched(self):
        self.st.close()
        self.db.unlink()
        for f in self.root.glob("twin-state.db-*"):
            f.unlink()
        conn = sqlite3.connect(str(self.db))
        for step in L.TS.MIGRATIONS[:3]:
            conn.executescript(step)
        conn.execute("INSERT INTO meta VALUES ('schema_version', '3')")
        conn.commit()
        conn.close()
        with L.TS.Store(self.db, clock=lambda: T0) as s:
            self.assertEqual(s.version(), 4)
            self.assertEqual(L.decisions(s), [])


if __name__ == "__main__":
    unittest.main()
