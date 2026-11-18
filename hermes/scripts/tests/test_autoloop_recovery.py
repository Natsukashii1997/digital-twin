"""恢复闸（#195 批 0D 恢复门）自测：什么时候关、对账顺序、对完才开、仍冻结的范围继续拒绝、对账期间新出的不明不被清掉。
纪元（开机、睡醒、中台）在单元测试里打桩；真中台的重启、升级、搬家在 test_autoloop_drill。不碰正式中台、正式状态目录。"""
import contextlib
import io
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
