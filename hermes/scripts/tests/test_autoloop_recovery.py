"""恢复闸（#195 批 0D 恢复门）自测：什么时候关、对账顺序、对完才开、仍冻结的范围继续拒绝、对账期间新出的不明不被清掉。
纪元（开机、睡醒、中台）在单元测试里打桩；真中台的重启、升级、搬家在 test_autoloop_drill。不碰正式中台、正式状态目录。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import io
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_exits as E  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_recovery as R  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401
from test_autoloop_exits import TASK, add_grant, isolate  # noqa: E402

C1, C2 = "a" * 40, "b" * 40


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-recovery-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        isolate(self, self.root, open_gate=False)
        self.epoch = {"boot": "1", "wake": "1", "hub_commit": C1, "hub_process": "10@x"}
        p = mock.patch.object(R, "current_epoch", side_effect=lambda: dict(self.epoch))
        p.start()
        self.addCleanup(p.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            R.pin(C1, confirm=True)

    def unknown(self, key="merge:o/r:1:h", scope="repo:o/r"):
        with self.assertRaises(RuntimeError):                    # 闸已开：走真的 ensure_open / admit
            E.guard(key.split(":")[0], key, scope, {}, lambda: (_ for _ in ()).throw(RuntimeError("断")))


class Closing(Base):
    def test_closed_until_first_reconcile_then_open(self):
        self.assertEqual(R.status()["why"], "还没对过账")
        with mock.patch.object(R, "reconcile", wraps=R.reconcile) as rec:
            R.ensure_open()
            R.ensure_open()
        self.assertEqual(rec.call_count, 1)                      # 开着时不再对账
        self.assertTrue(R.status()["open"])

    def test_each_epoch_change_closes_the_gate(self):
        R.ensure_open()
        for k, word in (("boot", "重启"), ("wake", "睡醒"), ("hub_commit", "换了版本"), ("hub_process", "中台重启")):
            self.epoch[k] = self.epoch[k] + "x" if k != "hub_commit" else C2
            self.assertIn(word, R.status()["why"])
            if k == "hub_commit":                                # 换了版本、锁定版本没改：对完也不开
                with self.assertRaisesRegex(E.Refused, "不是锁定的"):
                    R.ensure_open()
                with contextlib.redirect_stdout(io.StringIO()):
                    R.pin(C2, confirm=True)                     # 升级验证通过后才改锁定版本
            R.ensure_open()
            self.assertTrue(R.status()["open"])

    def test_hub_down_keeps_it_closed(self):
        R.ensure_open()
        with mock.patch.object(R, "current_epoch", side_effect=R.HubDown("中台连不上（ConnectionRefusedError）")):
            with self.assertRaisesRegex(E.Refused, "中台连不上"):
                R.ensure_open()
            self.assertIn("连不上", R.status()["why"])

    def test_no_pin_never_opens(self):
        R.pin_path().unlink()
        with self.assertRaisesRegex(E.Refused, "没有锁定"):
            R.ensure_open()

    def test_unknown_result_closes_and_reconcile_reopens_but_scope_stays_frozen(self):
        R.ensure_open()
        self.unknown()
        self.assertIn("结果不明", R.status()["why"])
        R.ensure_open()                                           # GitHub 连不上也开闸：只冻那个仓库
        st = R.status()
        self.assertEqual((st["open"], st["frozen"]), (True, ["repo:o/r"]))
        with self.assertRaisesRegex(E.Refused, "已冻结"):
            E.guard("merge", "merge:o/r:2:h", "repo:o/r", {}, dict)

    def test_unknown_during_reconcile_is_not_wiped(self):
        R.ensure_open()
        self.unknown("merge:o/r:1:h")

        def reader(t, row, now):                                 # 对账期间别的进程又出了一个结果不明
            with E.db() as con:
                E.mark_dirty(con, "另一个结果不明")
            raise E.Unreachable("连不上")
        with self.assertRaisesRegex(E.Refused, "再对一次"):
            R.ensure_open({"merge": reader})
        R.ensure_open({"merge": lambda t, r, n: ("failed", {})})
        self.assertTrue(R.status()["open"])

    def test_pin_needs_confirm_and_full_commit(self):
        with self.assertRaisesRegex(E.Refused, "confirm"):
            R.pin(C2)
        with self.assertRaisesRegex(E.Refused, "40 位"):
            R.pin("abc", confirm=True)
        self.assertEqual(R.pinned(), C1)
        self.assertEqual(R.pin_path().stat().st_mode & 0o777, 0o600)


class Order(Base):
    def test_mirror_mismatch_keeps_closed(self):
        add_grant()
        R.ensure_open()                                           # 镜像按凭证库重写、回读一致
        self.assertEqual(json.loads(G.status_path().read_text())["tasks"][TASK]["generation"], 1)
        with E.db() as con:
            E.mark_dirty(con, "测试")
        with mock.patch.object(G, "refresh_status"):              # 镜像没写成：回读对不上
            G.status_path().write_text(json.dumps({"tasks": {}}))
            with self.assertRaisesRegex(E.Refused, "对不上"):
                R.ensure_open()

    def test_mirror_unwritable_keeps_closed(self):
        add_grant()
        with mock.patch.object(G, "refresh_status", side_effect=OSError("只读")):
            with self.assertRaisesRegex(E.Refused, "写不成"):
                R.ensure_open()

    def test_no_grants_db_is_not_created(self):
        R.ensure_open()
        self.assertFalse(G.db_path().exists())

    def test_report_follows_the_five_steps(self):
        rep = R.reconcile()
        self.assertEqual([s["step"] for s in rep["steps"]], ["本机账本", "凭证与镜像", "中台", "外部回读"])
        self.assertTrue(rep["open"])
        self.assertEqual(R.status()["last_report"]["frozen"], [])

    def test_secretary_unknown_keeps_gate_closed(self):
        """常浩 11-19 定：秘书账本里还有结果不明、核不清、还在送的代批，恢复闸对账不开闸；秘书自己结掉之后才开。
        交常浩（handoff）的不算；状态库在、还没有秘书表的不算。"""
        import sqlite3
        db = self.root / "twin-state.db"
        with mock.patch.dict(os.environ, {"DT_STATE_DB": str(db)}):
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE other (x)")
            R.ensure_open()                                       # 没有秘书表：照常开
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE secretary_decisions (id TEXT, action TEXT, status TEXT, task_id TEXT DEFAULT 't', "
                            "outcome TEXT DEFAULT '{}', created_at TEXT, updated_at TEXT)")
                con.executemany("INSERT INTO secretary_decisions (id, action, status, created_at) VALUES (?, ?, ?, 'x')",
                                [("sd-1", "ask", "unknown"), ("sd-2", "handoff", "handoff"), ("sd-3", "ask", "done")])
            with E.db() as con:
                E.mark_dirty(con, "秘书代批 sd-1 结果不明")
            with self.assertRaisesRegex(E.Refused, "秘书账本里有 1 个"):
                R.ensure_open()
            for status in ("unreconciled", "executing"):
                with sqlite3.connect(db) as con:
                    con.execute("UPDATE secretary_decisions SET status = ? WHERE id = 'sd-1'", (status,))
                with self.assertRaisesRegex(E.Refused, "sd-1"):
                    R.ensure_open()
            with sqlite3.connect(db) as con:                      # 秘书自己的对账结掉了
                con.execute("UPDATE secretary_decisions SET status = 'done' WHERE id = 'sd-1'")
            R.ensure_open()
            self.assertTrue(R.status()["open"])

    def test_queued_owner_answer_is_not_submitted_during_reconcile(self):
        import autoloop_answer as A
        import autoloop_feishu as F
        with F.db(write=True) as con:
            con.execute("INSERT INTO feishu_cards (card_id, token, message_id, chat_id, kind, options, version, "
                        "hub_delivered_at) VALUES (5, 'aaaaaaaaaaaaaaaa', 'om_5', 'oc_x', 'ask', 2, 1, 'x')")
            con.execute("INSERT INTO claims (card_id, who, state, choice, at, attempt) VALUES "
                        "(5, 'owner', 'queued', 0, 'x', 'a1')")
        with mock.patch.dict(os.environ, {"DT_OWNER_OPEN_ID": "ou_o", "DT_OWNER_CHAT_ID": "oc_x"}), \
                mock.patch.object(A, "submit") as submit:
            R.ensure_open()
        submit.assert_not_called()


class AdmissionRace(Base):
    """审查 P1-03：ensure_open() 只是先看一眼；它放行之后闸立刻被关（别的进程登记了结果不明、或开始维护），
    领写入资格的那个写事务里的 admit 必须拦住，外部动作一次也不做。"""

    def close_right_after_check(self, how="dirty"):
        real = R.ensure_open

        def racing(readers=None):
            epoch = real(readers)                                # A 看过：开着
            with E.db() as con:                                  # B 抢在 A 领资格之前关了闸
                if how == "dirty":
                    E.mark_dirty(con, "别的进程刚出了一个结果不明")
                else:
                    con.execute("UPDATE gate SET hold = 'p-race' WHERE id = 1")
            return epoch
        return mock.patch.object(R, "ensure_open", racing)

    def test_exit_is_refused_when_gate_closes_after_check(self):
        R.ensure_open()
        done = []
        for how, word in (("dirty", "结果不明"), ("hold", "维护中")):
            with self.close_right_after_check(how), self.assertRaisesRegex(E.Refused, word):
                E.guard("merge", f"merge:o/r:{how}:h", "repo:o/r", {}, lambda: done.append(1) or {})
            with E.db() as con:
                con.execute("UPDATE gate SET hold = NULL WHERE id = 1")
            R.ensure_open()
        self.assertEqual(done, [])
        with E.db(write=False) as con:                           # 一条 executing 都没落盘
            self.assertEqual(con.execute("SELECT COUNT(*) FROM exits").fetchone()[0], 0)

    def test_owner_answer_stays_queued_when_gate_closes_after_check(self):
        import autoloop_answer as A
        import autoloop_feishu as F
        with F.db(write=True) as con:
            con.execute("INSERT INTO feishu_cards (card_id, token, message_id, chat_id, kind, options, version, "
                        "hub_delivered_at) VALUES (5, 'aaaaaaaaaaaaaaaa', 'om_5', 'oc_x', 'ask', 2, 1, 'x')")
            con.execute("INSERT INTO claims (card_id, who, state, choice, at, attempt, version) VALUES "
                        "(5, 'owner', 'queued', 0, 'x', 'a1', 1)")
        R.ensure_open()
        with self.close_right_after_check(), mock.patch.object(F, "hub") as hub, \
                mock.patch.dict(os.environ, {"DT_OWNER_OPEN_ID": "ou_o", "DT_OWNER_CHAT_ID": "oc_x"}):
            code, said = A.submit(5)
        self.assertEqual(code, 3)
        hub.assert_not_called()                                  # 没交中台
        with F.db() as con:
            self.assertEqual(con.execute("SELECT state FROM claims WHERE card_id = 5").fetchone()[0], "queued")

    def test_send_pending_stops_mid_loop_when_gate_closes(self):
        """长循环每发一个都在领取的写事务里再核：发完第一个后闸关了，第二个不领取、不发。"""
        sent = []

        def post(body):
            sent.append(body["id"])
            with E.db() as con:                                  # 发第一个的同时，别处出了结果不明
                E.mark_dirty(con, "别处结果不明")
            return len(sent)
        R.ensure_open()
        epoch = json.loads(R.gate_row()["epoch"])
        add_grant()
        add_grant("t-20261118-bbbbbb")
        with G.tx() as con:
            for tid in (TASK, "t-20261118-bbbbbb"):
                row = con.execute("SELECT * FROM grants WHERE task_id = ?", (tid,)).fetchone()
                G.record_event(con, row, "start", f"{tid}:g1:start", G.now_tz())
        with mock.patch.object(R, "ensure_open", lambda readers=None: epoch):   # 「看一眼」一直说开着：只靠 admit 拦
            out = G.send_pending(post=post)
        self.assertEqual(len(sent), 1)
        self.assertEqual(out[0]["sent"], True)
        self.assertIn("开闸后 send-pending 补发", out[-1]["why"])


class Maintenance(Base):
    """审查 P1-06：维护（升级、回滚、搬家）先停写、排空，再快照；回滚只能用仍在维护的恢复点，回滚后核对卡片号；
    新版本锁定后升级前的快照作废。"""

    def test_hold_stops_every_entry_until_release(self):
        R.ensure_open()
        R.hold("p1", self.root / "snap", C1, 7)
        with self.assertRaisesRegex(E.Refused, "维护中"):
            R.ensure_open()
        self.assertIn("维护中", R.reconcile()["why"])                 # 对账也不开
        with self.assertRaisesRegex(E.Refused, "维护中"):
            E.guard("merge", "merge:o/r:1:h", "repo:o/r", {}, dict)
        with self.assertRaisesRegex(E.Refused, "已经在维护"):
            R.hold("p2", self.root / "snap2", C1, 7)
        R.release("p1", "retired")
        self.assertIn("维护结束", R.status()["why"])               # 结束后要先对账
        R.ensure_open()
        self.assertTrue(R.status()["open"])

    def test_drain_lists_in_flight_writes(self):
        now = E.now_tz()
        with E.db() as con:                                       # 一个还活着的执行者（本进程）
            con.execute("INSERT INTO exits (key, kind, scope, target, state, pid, proc, boot, created_at, started_at, "
                        "updated_at) VALUES ('merge:o/r:1:h', 'merge', 'repo:o/r', '{}', 'executing', ?, ?, ?, ?, ?, ?)",
                        (os.getpid(), E.proc_start(os.getpid()), E.boot_id(), E.iso(now), E.iso(now), E.iso(now)))
        R.hold("p1", self.root / "snap", C1, 7)
        self.assertEqual(R.in_flight_writes(), ["出口 merge:o/r:1:h"])   # 排空前不许快照

    def test_drain_sees_bridge_lock_before_cards_db_exists(self):
        """L3 第 3 轮 P1-06 路径 A：桥接（或秘书领卡）已拿到桥接锁、cards.db 还没建时停写：排空照样看得见，放锁后才空。"""
        import threading
        import autoloop_feishu as F
        self.assertFalse(F.db_file().exists())
        got, go = threading.Event(), threading.Event()

        def bridge():
            with F.bridge_lock():
                got.set()
                go.wait(10)
        th = threading.Thread(target=bridge)
        th.start()
        self.addCleanup(th.join, 10)
        self.addCleanup(go.set)
        self.assertTrue(got.wait(10))
        R.hold("p1", self.root / "snap", C1, 7)
        self.assertFalse(F.db_file().exists())
        self.assertEqual(R.in_flight_writes(), ["飞书桥接或秘书领卡正持桥接锁"])   # 排空前不许快照
        go.set()
        th.join(10)
        self.assertEqual(R.in_flight_writes(), [])

    def test_drain_counts_only_freshly_claimed_dispatch_events(self):
        """第 2 轮预审疑点：派单事件结果不明（领走超过 60 秒、那次 POST 早已结束）不算在途——停写期间补发不了，
        算进去排空就永远完不成；刚领走的才算。"""
        now = G.now_tz()
        with G.tx() as con:
            for key, ago in (("t1:g1:start", 5), ("t2:g1:start", 600)):
                con.execute("INSERT INTO hub_events (event_key, task_id, rule, executor, generation, kind, at, sent, claimed_at) "
                            "VALUES (?, ?, 'r', 'claude', 1, 'start', ?, 2, ?)",
                            (key, key[:2], G.iso(now), G.iso(now - dt.timedelta(seconds=ago))))
        self.assertEqual(R.in_flight_writes(), ["派单事件 t1:g1:start"])
        import sqlite3                                            # 秘书在送的代批：同样只算 2 分钟内动过的
        db = self.root / "twin-state.db"
        with sqlite3.connect(db) as con:
            con.execute("CREATE TABLE secretary_decisions (id TEXT, status TEXT, updated_at TEXT)")
            con.executemany("INSERT INTO secretary_decisions VALUES (?, 'executing', ?)",
                            [("sd-new", G.iso(now)), ("sd-old", G.iso(now - dt.timedelta(minutes=10)))])
        with mock.patch.dict(os.environ, {"DT_STATE_DB": str(db)}):
            self.assertEqual(R.in_flight_writes(), ["派单事件 t1:g1:start", "秘书代批 sd-new"])

    def test_retired_snapshot_can_never_be_restored(self):
        R.hold("p1", self.root / "snap", C1, 7)
        self.assertEqual(R.restorable("p1")["max_card"], 7)
        R.release("p1", "retired")
        with self.assertRaisesRegex(E.Refused, "retired"):
            R.restorable("p1")
        with self.assertRaisesRegex(E.Refused, "没有登记"):
            R.restorable("nope")

    def test_rollback_checks_cards_sent_after_snapshot(self):
        import autoloop_feishu as F
        R.hold("p1", self.root / "snap", C1, 7)
        with F.db(write=True) as con:                             # 快照之后又发过卡片 8（停写没守住）
            con.execute("INSERT INTO feishu_cards (card_id, token, chat_id, kind, options, version) "
                        "VALUES (8, 'bbbbbbbbbbbbbbbb', 'oc_x', 'ask', 2, 1)")
        with self.assertRaisesRegex(E.Refused, "错位"):
            R.release("p1", "rolled_back")
        self.assertIn("维护中", R.status()["why"])                  # 保持停写，交常浩
        with F.db(write=True) as con:
            con.execute("DELETE FROM feishu_cards WHERE card_id = 8")
        R.release("p1", "rolled_back")
        R.ensure_open()
        self.assertTrue(R.status()["open"])


class Cli(Base):
    def test_status_reconcile_pin(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(R.main(["status"]), 2)              # 还没对过账：关着
            self.assertEqual(R.main(["reconcile"]), 0)
            self.assertEqual(R.main(["status"]), 0)
            self.assertEqual(R.main(["pin", "--commit", C2]), 1)
        self.assertIn("confirm", out.getvalue())


class Epoch(unittest.TestCase):
    def test_real_epoch_reads_boot_wake_and_hub(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"AGENT_AUTOLOOP_HOME": d,
                                                                             "DT_HUB": "http://127.0.0.1:9"}):
            with self.assertRaises(R.HubDown):
                R.current_epoch()
            with mock.patch.object(R, "healthz", return_value=C1):
                with self.assertRaisesRegex(R.HubDown, "读不到 up.pid"):          # 审查 P2-01：认不出中台进程不开闸
                    R.current_epoch()
                Path(d, "up.pid").write_text(str(os.getpid()))
                with self.assertRaisesRegex(R.HubDown, "不是中台"):               # 进程在，但不是 agent-autoloop
                    R.current_epoch()
                Path(d, "up.pid").write_text("999999")
                with self.assertRaisesRegex(R.HubDown, "不在或查不了"):
                    R.current_epoch()
        self.assertNotEqual(E.boot_id(), "?")                                    # 真中台的进程身份见 test_autoloop_drill


if __name__ == "__main__":
    unittest.main()
