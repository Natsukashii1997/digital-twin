"""飞书桥接与本人点击（#195 批 0B-4 第 4a 批）自测。
单元部分不起中台、不发飞书；端到端部分在临时目录起真中台（同 test_autoloop_hub_e2e，本机没有命令行时跳过），
走「中台出卡 → 桥接领卡、发卡、报送达 → 常浩点击 → 核对、本人在途、答中台 → 只读回读 → 桥接改卡」。
飞书发送一律换成记录器；不碰 ~/.agent-autoloop 和正式中台。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import json
import io
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_answer as A  # noqa: E402
import autoloop_config as C  # noqa: E402
import autoloop_exits as E  # noqa: E402
import autoloop_feishu as F  # noqa: E402
import autoloop_recovery as Rv  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401
from test_autoloop_hub_e2e import CLI, OWNER, SKIP, HubFixture  # noqa: E402

CHAT = "oc_fixture_home"
CARD = {"id": 7, "version": 1, "ref": None, "kind": "ask", "state": "open", "title": "选一个", "text": "说明",
        "options": ["甲", "乙"], "recommend": 1, "choice": None, "answer": None, "by": None, "result": None, "rule": "规则A"}


class Lark:
    """lark-cli 记录器：发卡返回固定消息号，改卡记下来；fail_patch 时改卡报错。"""
    def __init__(self, mid="om_fixture_1", fail_patch=False):
        self.calls, self.mid, self.fail_patch = [], mid, fail_patch

    def __call__(self, args, what, cwd=None):
        self.calls.append(args)
        if args[:2] == ["im", "+messages-send"]:
            return {"message_id": self.mid}
        if args[:3] == ["im", "messages", "patch"] and self.fail_patch:
            raise F.SendError("改不了")
        return {}

    def sent(self):
        return [json.loads(a[a.index("--content") + 1]) for a in self.calls if a[:2] == ["im", "+messages-send"]]

    def keys(self):
        return [a[a.index("--idempotency-key") + 1] for a in self.calls if a[:2] == ["im", "+messages-send"]]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": self.tmp.name + "/dt/autoloop/cards.db",
                                                "DT_EXITS_DB": self.tmp.name + "/dt/autoloop/exits.db",
                                                "DT_OWNER_OPEN_ID": OWNER, "DT_OWNER_CHAT_ID": CHAT})
        self.env.start()
        self.addCleanup(self.env.stop)
        for gate in (mock.patch.object(Rv, "ensure_open"), mock.patch.object(Rv, "admit")):   # 恢复闸放行（关着见 RecoveryGate）
            gate.start()
            self.addCleanup(gate.stop)
        self.lark = Lark()
        p = mock.patch.object(F, "lark", self.lark)
        p.start()
        self.addCleanup(p.stop)

    def register(self, card=CARD):
        return F.deliver(dict(card))

    def token(self):
        btn = self.lark.sent()[-1]["body"]["elements"][1]
        return btn["behaviors"][0]["value"]["autoloop"]


class Render(Base):
    def test_open_card_buttons_carry_only_token_option_and_version(self):
        c = F.render({**CARD, "version": 3}, "0123456789abcdef")
        buttons = [e for e in c["body"]["elements"] if e["tag"] == "button"]
        self.assertEqual([b["behaviors"][0]["value"] for b in buttons], [{"autoloop": "0123456789abcdef", "o": 0, "v": 3},
                                                                         {"autoloop": "0123456789abcdef", "o": 1, "v": 3}])
        self.assertEqual([b["type"] for b in buttons], ["default", "primary"])            # 推荐项高亮
        self.assertEqual(c["header"]["subtitle"]["content"], "来源：规则A")
        self.assertNotIn("form", json.dumps(c))                                           # 不放文字输入框

    def test_settled_and_fyi_cards_have_no_buttons(self):
        for card in ({**CARD, "state": "decided", "choice": 0, "by": "秘书"}, {**CARD, "kind": "fyi", "options": []},
                     {**CARD, "state": "closed", "result": "不用了"}):
            c = F.render(card, "0123456789abcdef")
            self.assertFalse([e for e in c["body"]["elements"] if e["tag"] == "button"])
        self.assertIn("秘书替你选了「甲」", json.dumps(F.render({**CARD, "state": "decided", "choice": 0, "by": "秘书"}, "x"),
                                                     ensure_ascii=False))


class Deliver(Base):
    def test_first_send_registers_token_and_resend_reuses_it(self):
        self.assertEqual(self.register(), "om_fixture_1")
        tok = self.token()
        self.assertRegex(tok, r"^[0-9a-f]{16}$")
        self.assertEqual(self.register(), "om_fixture_1")           # 送达报告丢了，中台再给一次（ref 仍为空）
        self.assertEqual(self.token(), tok)
        self.assertEqual(self.lark.keys(), ["autoloop-card-7"])     # 批 0D：出口账本记着已发，不再调飞书，用原消息号
        row = F.card_by_token(tok)
        self.assertEqual((row["card_id"], row["message_id"], row["chat_id"], row["options"]), (7, "om_fixture_1", CHAT, 2))
        self.assertTrue(row["delivered_at"])
        self.assertEqual(Path(os.environ["DT_AUTOLOOP_CARDS_DB"]).stat().st_mode & 0o777, 0o600)

    def test_update_patches_and_falls_back_only_for_settled_cards(self):
        self.register()
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 2, "state": "decided", "choice": 1, "by": OWNER})
        patch = [a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]][-1]
        self.assertEqual(patch[patch.index("--message-id") + 1], "om_fixture_1")
        self.lark.fail_patch = True
        with self.assertRaises(F.SendError):                         # 开着的卡改不了：报失败，不另发
            F.deliver({**CARD, "ref": "om_fixture_1", "version": 3})
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 4, "state": "closed", "result": "不用了"})
        self.assertEqual(self.lark.keys()[-1], "autoloop-card-7-v4")

    def test_delivered_at_follows_the_version_that_reached_the_owner(self):
        """#218 审查 P1-02：送达时间按版本记——开着的卡改版成功才换成新版本的送达时刻，改不了就留在旧版本；同一版重送不变。"""
        def row():
            with F.db() as con:
                return dict(con.execute("SELECT version, delivered_at FROM feishu_cards WHERE card_id = 7").fetchone())
        times = iter(["2026-11-19T10:00:00+08:00", "2026-11-19T10:05:00+08:00", "2026-11-19T10:40:00+08:00",
                      "2026-11-19T10:50:00+08:00", "2026-11-19T11:00:00+08:00"])
        with mock.patch.object(F, "now", lambda: next(times)):
            self.register()
            self.assertEqual(row(), {"version": 1, "delivered_at": "2026-11-19T10:00:00+08:00"})
            self.register()                                          # 同一版重送（送达报告丢了）：不重算
            self.assertEqual(row()["delivered_at"], "2026-11-19T10:00:00+08:00")
            self.lark.fail_patch = True
            with self.assertRaises(F.SendError):                     # 改版没送到：仍是第 1 版和它的时刻
                F.deliver({**CARD, "ref": "om_fixture_1", "version": 2})
            self.assertEqual(row(), {"version": 1, "delivered_at": "2026-11-19T10:00:00+08:00"})
            self.lark.fail_patch = False
            F.deliver({**CARD, "ref": "om_fixture_1", "version": 2})  # 改版送到：从这一版的送达起算
            self.assertEqual(row(), {"version": 2, "delivered_at": "2026-11-19T10:40:00+08:00"})
            F.deliver({**CARD, "ref": "om_fixture_1", "version": 2})  # 同一版再改一次：不重算
            self.assertEqual(row()["delivered_at"], "2026-11-19T10:40:00+08:00")
            F.deliver({**CARD, "ref": "om_fixture_1", "version": 3, "state": "decided", "choice": 1, "by": OWNER})
            self.assertEqual(row(), {"version": 3, "delivered_at": "2026-11-19T10:40:00+08:00"})   # 结束的卡不再计时

    def test_first_send_of_a_newer_version_restarts_the_clock(self):
        """第 1 版的送达报告没被中台收下，中台再给时已是第 2 版（ref 仍为空）：送达时间按第 2 版重算。"""
        times = iter(["2026-11-19T10:00:00+08:00", "2026-11-19T10:30:00+08:00"])
        with mock.patch.object(F, "now", lambda: next(times)):
            self.register()
            self.register({**CARD, "version": 2})
        with F.db() as con:
            r = con.execute("SELECT version, delivered_at FROM feishu_cards WHERE card_id = 7").fetchone()
        self.assertEqual((r["version"], r["delivered_at"]), (2, "2026-11-19T10:30:00+08:00"))

    def test_ref_mismatch_refuses_to_patch(self):
        self.register()
        with self.assertRaisesRegex(F.Fail, "对不上"):
            F.deliver({**CARD, "ref": "om_other", "version": 2, "state": "decided", "choice": 1, "by": OWNER})
        self.assertFalse([a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]])

    def test_once_reports_delivered_or_failed(self):
        posts = []

        def hub(method, path, body=None, timeout=20):
            if method == "GET":
                return {"card": dict(CARD)}
            posts.append((path, body))
            return {}
        with mock.patch.object(F, "hub", hub), mock.patch.object(A, "flush") as flush, \
                mock.patch.object(F, "hub_readable", lambda: True), \
                mock.patch("autoloop_publish.hub_card", lambda cid: {"id": cid, "version": 1, "state": "open"}):
            self.assertTrue(F.once()["ok"])
            flush.assert_called_once_with(7)                                   # 送达确认后提交排队的本人回答
            self.assertTrue(F.card_by_token(self.token())["hub_delivered_at"])
            self.lark.mid = None                                     # 另一张卡发卡没拿到消息号：结果不明
            with mock.patch.object(F, "hub", lambda m, p, b=None, timeout=20: {"card": {**CARD, "id": 8}} if m == "GET"
                                   else posts.append((p, b)) or {}):
                got = F.once()
            self.lark.mid = "om_fixture_1"                           # 本地 lark-cli 根本没跑起来：确定没发，报 failed
            with mock.patch.object(F, "hub", lambda m, p, b=None, timeout=20: {"card": {**CARD, "id": 9}} if m == "GET"
                                   else posts.append((p, b)) or {}), \
                    mock.patch.object(F, "lark", side_effect=F.SendError("发卡片：lark-cli 跑不起来（FileNotFoundError）")):
                F.once()
        self.assertEqual(posts[0], ("/v1/cards/7/delivered", {"version": 1, "ref": "om_fixture_1"}))
        self.assertTrue(got["deferred"])                             # 结果不明是暂缓：不报 failed（第 2 轮预审 P2-B）
        self.assertEqual([p for p, _ in posts], ["/v1/cards/7/delivered", "/v1/cards/9/failed"])


class RecoveryGate(Base):
    """批 0D：领卡前过恢复闸；发新卡走出口账本，结果不明不重发、冻结这张卡，对账按令牌在私聊里找到那条消息。"""
    def test_closed_gate_never_takes_a_card(self):
        with mock.patch.object(Rv, "ensure_open", side_effect=E.Refused("恢复闸关着：中台重启过，要先对账")), \
                mock.patch.object(F, "hub") as hub:
            out = F.once()
        self.assertEqual((out["ok"], "中台重启过" in out["paused"]), (False, True))   # 和中台库读不到一样：歇一会再看
        hub.assert_not_called()

    def test_card_taken_then_gate_closes_is_deferred_not_failed(self):
        """审查 P1-04：领到卡以后闸关了（发新卡的出口账本拒绝），是「暂缓」：不发、不向中台报 failed，中台过一会儿再递。"""
        calls, checks = [], []

        def hub(method, path, body=None, timeout=20, channel=F.CHANNEL):
            calls.append((method, path))
            return {"card": dict(CARD)} if path.startswith("/v1/cards/next") else {}

        def ensure(readers=None):                                    # 领卡前看一眼：开着；发卡时：刚关
            checks.append(1)
            if len(checks) > 1:
                raise E.Refused("恢复闸关着：中台重启过，要先对账")
        with mock.patch.object(Rv, "ensure_open", ensure), mock.patch.object(F, "hub", hub), \
                mock.patch.object(F, "hub_readable", return_value=True), \
                mock.patch.object(F, "newer_elsewhere", return_value=None):
            out = F.once()
        self.assertEqual((out["ok"], out.get("deferred")), (False, True))
        self.assertEqual([c for c in calls if c[0] == "POST"], [])    # 没报 failed、没报送达
        self.assertEqual(self.lark.keys(), [])                        # 没发飞书

    def test_gate_closing_before_the_bridge_lock_writes_nothing(self):
        """第 2 轮预审 P2-C：入口看过闸、拿到桥接锁之前停写了（维护 hold）——改卡、报送达都不做，暂缓不报 failed；
        排空时看得到桥接正拿着锁。"""
        self.register()
        calls = []

        def hub(method, path, body=None, timeout=20, channel=F.CHANNEL):
            calls.append((method, path))
            return {"card": {**CARD, "version": 2, "ref": "om_fixture_1"}} if path.startswith("/v1/cards/next") else {}
        held = E.Refused("恢复闸关着：维护中（恢复点 p1）")
        with mock.patch.object(Rv, "admit", side_effect=held), mock.patch.object(F, "hub", hub), \
                mock.patch.object(F, "hub_readable", return_value=True), \
                mock.patch.object(F, "newer_elsewhere", return_value=None):
            out = F.once()
            with self.assertRaisesRegex(F.Deferred, "维护中"):
                F.report_delivered(7)
        self.assertEqual((out["ok"], out.get("deferred")), (False, True))
        self.assertEqual([c for c in calls if c[0] == "POST"], [])                    # 没报送达、没报 failed
        self.assertFalse([a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]])   # 没改卡
        self.assertFalse(F.bridge_busy())
        with F.bridge_lock():
            self.assertTrue(F.bridge_busy())
            self.assertIn("飞书桥接或秘书领卡正持桥接锁", Rv.in_flight_writes())
        self.assertNotIn("飞书桥接或秘书领卡正持桥接锁", Rv.in_flight_writes())

    def test_settled_send_without_message_id_is_deferred(self):
        """第 2 轮预审 P2-B2：出口账本记这张卡已发、却没有消息号（旧数据或人工结算没填）：不猜、不 KeyError，暂缓。"""
        with mock.patch.object(E, "_begin", return_value={"done": True, "again": True, "manual": "x"}), \
                self.assertRaisesRegex(F.Deferred, "没有消息号"):
            self.register()

    def test_unknown_send_is_frozen_then_reconciled_without_resending(self):
        def timeout(args, what, cwd=None):
            self.lark.calls.append(args)
            raise F.SendError("发卡片：lark-cli 跑不起来（TimeoutExpired）")   # 其实飞书已经收下了
        with mock.patch.object(F, "lark", timeout), self.assertRaisesRegex(F.Deferred, "发卡结果不明"):
            self.register()                                          # 结果不明是暂缓，不是失败（第 2 轮预审 P2-B）
        with self.assertRaisesRegex(F.Deferred, "结果不明"):
            self.register()                                          # 中台再给：冻结，不重发
        self.assertEqual(len([a for a in self.lark.calls if a[:2] == ["im", "+messages-send"]]), 1)
        with F.db() as con:                                          # 取登记的令牌
            tok = con.execute("SELECT token FROM feishu_cards WHERE card_id = 7").fetchone()[0]
        card = {"body": {"elements": [{"tag": "button", "behaviors": [{"value": {"autoloop": tok, "o": 0}}]}]}}
        msg = {"message_id": "om_found", "msg_type": "interactive", "sender": {"sender_type": "app"},
               "body": {"content": json.dumps(card)}}
        with mock.patch.object(E, "lark_read", return_value={"messages": [msg], "has_more": False}):
            notes = E.reconcile()
        self.assertEqual(notes[0]["state"], "done")
        self.assertEqual(self.register(), "om_found")                # 用对账找到的消息号，不再发
        self.assertEqual(len([a for a in self.lark.calls if a[:2] == ["im", "+messages-send"]]), 1)
        self.assertEqual(F.card_by_token(tok)["message_id"], "om_found")

    def test_definite_send_failure_can_be_retried(self):
        """审查 P1-05：只有 lark-cli 在本机根本没跑起来才算确定没发，可以重发。"""
        def missing(args, what, cwd=None):
            raise F.SendError("发卡片：lark-cli 跑不起来（FileNotFoundError）")
        with mock.patch.object(F, "lark", missing), self.assertRaises(F.SendError):
            self.register()
        self.assertEqual(self.register(), "om_fixture_1")

    def test_feishu_error_reply_is_unknown_not_retried(self):
        """审查 P1-05：飞书回了错误（ok=false）不能证明请求没到，记结果不明、冻结这张卡，不重发。"""
        def refused(args, what, cwd=None):
            self.lark.calls.append(args)
            raise F.SendError("发卡片失败：机器人不在会话里")
        with mock.patch.object(F, "lark", refused), self.assertRaisesRegex(F.Deferred, "发卡结果不明"):
            self.register()
        with self.assertRaisesRegex(F.Deferred, "结果不明"):
            self.register()
        self.assertEqual(len([a for a in self.lark.calls if a[:2] == ["im", "+messages-send"]]), 1)


class MaintenanceQuiet(Base):
    """L3 第 4 轮 P1-06（常浩 11-21 选甲）：维护停写时，给常浩的提醒（放弃送达、对账回话）也停，发的时候持桥接锁
    （排空看得见）；手动入口 deliver / confirm 也过恢复闸；桥接常驻循环在维护期间什么都不跑。"""

    def setUp(self):
        super().setUp()
        self.sent = []

        def send(chat, kind, body, key):
            self.sent.append((body["text"], Rv.in_flight_writes()))
        p = mock.patch("tidings_weekly.send", send)
        p.start()
        self.addCleanup(p.stop)

    def hold(self):
        Rv.hold("p1", Path(self.tmp.name) / "snap", "a" * 40, 0)

    def test_alert_and_tell_are_quiet_during_maintenance_then_resume(self):
        self.hold()
        self.assertFalse(F.alert("k", "中台放弃送达了"))
        A.tell(CHAT, "对账结果")
        self.assertEqual(self.sent, [])
        with F.db() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM alerts WHERE key = 'k'").fetchone())   # 本地提醒表也不写
        Rv.release("p1", "retired")
        self.assertTrue(F.alert("k", "中台放弃送达了"))
        A.tell(CHAT, "对账结果")
        self.assertEqual([s[0] for s in self.sent], ["中台放弃送达了", "对账结果"])

    def test_alert_and_tell_send_inside_the_bridge_lock(self):
        """发的那一刻排空看得见（持桥接锁）：维护在发之前开始就不发，在发之后开始就等它发完。"""
        F.alert("k", "x")
        A.tell(CHAT, "y")
        self.assertEqual([s[1] for s in self.sent], [["飞书桥接或秘书领卡正持桥接锁"]] * 2)

    def test_maintenance_is_checked_inside_the_bridge_lock(self):
        """维护恰好在拿到桥接锁之后才开始：锁里查维护的就不发（查在锁外就会漏发）。"""
        real, point = F.bridge_lock, []

        @contextlib.contextmanager
        def lock():
            with real():
                Rv.hold(point[-1], Path(self.tmp.name) / "snap", "a" * 40, 0)
                yield
        for name, call in (("alert", lambda: F.alert("k", "x")), ("tell", lambda: A.tell(CHAT, "y"))):
            point.append(f"p-{name}")
            with self.subTest(name), mock.patch.object(F, "bridge_lock", lock):
                self.assertFalse(call())
            Rv.release(point[-1], "retired")
        self.assertEqual(self.sent, [])

    def test_unreadable_gate_means_quiet(self):
        class Stop(BaseException):
            pass
        with mock.patch.object(Rv, "gate_row", side_effect=sqlite3.OperationalError("坏了")):
            self.assertEqual(F.maintenance(), "?")
            self.assertFalse(F.alert("k", "x"))
            with mock.patch.object(F.time, "sleep", side_effect=Stop), contextlib.redirect_stdout(io.StringIO()) as out, \
                    self.assertRaises(Stop):
                F.run()
        self.assertEqual(self.sent, [])
        self.assertIn("读不了出口账本的闸状态", out.getvalue())                   # 日志不写成「维护中（恢复点 ?）」
        self.assertNotIn("恢复点 ?", out.getvalue())

    def test_run_loop_resends_replies_held_back_when_maintenance_began_mid_round(self):
        """对账改了回答状态之后维护才开始：这句回话不会再生成，留在内存里，维护结束后补发（预审 P2）。"""
        class Stop(BaseException):
            pass
        rounds = []

        def rec():
            rounds.append(1)
            if len(rounds) == 1:
                self.hold()                                                     # 对账做完、回话之前维护开始
                return [(7, "上次点击发送到一半中断了，结果不明")]
            return []
        with mock.patch.object(A, "reconcile", rec), mock.patch.object(F, "check_given_up", return_value=[]), \
                mock.patch.object(F, "once", side_effect=[None, Stop()]), mock.patch.object(A, "identities", return_value=("ou", CHAT)), \
                mock.patch.object(F.time, "sleep", lambda _: Rv.release("p1", "retired")), \
                contextlib.redirect_stdout(io.StringIO()), self.assertRaises(Stop):
            F.run()
        self.assertEqual(len(rounds), 2)                                        # 维护那一轮什么都没跑
        self.assertEqual([x[0] for x in self.sent], ["上次点击发送到一半中断了，结果不明"])

    def test_run_loop_does_nothing_during_maintenance(self):
        class Stop(BaseException):
            pass
        self.hold()
        with mock.patch.object(A, "reconcile") as rec, mock.patch.object(F, "check_given_up") as gu, \
                mock.patch.object(F, "once") as once, mock.patch.object(F.time, "sleep", side_effect=Stop), \
                contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(Stop):
            F.run()
        for m in (rec, gu, once):
            m.assert_not_called()
        self.assertIn("维护中", out.getvalue())

    def test_deliver_and_confirm_check_the_gate(self):
        closed = E.Refused("恢复闸关着：维护中（恢复点 p1）")
        with mock.patch.object(Rv, "ensure_open", side_effect=closed), mock.patch.object(F, "hub") as hub:
            with self.assertRaises(F.Deferred):
                F.deliver(dict(CARD))
            with self.assertRaises(F.Deferred):
                F.confirm(7)
        with mock.patch.object(Rv, "admit", side_effect=closed), mock.patch.object(F, "hub") as hub2:   # 看一眼开着、锁里关了
            with self.assertRaises(F.Deferred):
                F.deliver(dict(CARD))
            with self.assertRaises(F.Deferred):
                F.confirm(7)
        hub.assert_not_called()
        hub2.assert_not_called()
        self.assertEqual(self.lark.sent(), [])
        with F.db() as con:                                       # 锁里就停了：连令牌都没登记（不是靠出口账本那道才挡住）
            self.assertIsNone(con.execute("SELECT 1 FROM feishu_cards WHERE card_id = 7").fetchone())


class Answer(Base):
    """本人点击：核对、排队、唯一发送资格、错误分类、对账（中台和只读窗口都换成假的）。"""
    def setUp(self):
        super().setUp()
        self.register()
        self.confirm()
        self.tok = self.token()
        self.hub_calls = []
        self.hub_reply = {"changed": True}
        self.hub_card = {"id": 7, "state": "decided", "choice": 1, "by": OWNER}
        self.gate = None                                                        # 设成 threading.Event 时，答中台的请求卡在这里
        for target, name, fake in ((F, "hub", self.fake_hub), (A, "read_card", lambda cid: self.hub_card),
                                   (F, "hub_target", lambda: ("http://x", "k"))):
            p = mock.patch.object(target, name, fake)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(A, "tell")
        self.tell = p.start()
        self.addCleanup(p.stop)

    def confirm(self, card_id=7):
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = ? WHERE card_id = ?", (F.now(), card_id))

    def fake_hub(self, method, path, body=None, timeout=20):
        self.hub_calls.append((path, body))
        if path.endswith("/answer") and self.gate is not None:
            self.entered.set()
            self.gate.wait(5)
        if path.endswith("/answer") and isinstance(self.hub_reply, Exception):
            raise self.hub_reply
        return self.hub_reply if path.endswith("/answer") else {}

    def answers(self):
        return [c for c in self.hub_calls if c[0].endswith("/answer")]

    def ver(self, card_id=7):
        """登记的当前版本（按钮回调带的版本号，#218 第 3 轮 P1-03）。"""
        with F.db() as con:
            r = con.execute("SELECT version FROM feishu_cards WHERE card_id = ?", (card_id,)).fetchone()
        return r["version"] if r else 1

    def claim(self):
        with F.db() as con:
            r = con.execute("SELECT * FROM claims WHERE card_id = 7 AND who = 'owner'").fetchone()
        return dict(r) if r else None

    def log(self):
        with F.db() as con:
            return [r["state"] for r in con.execute("SELECT state FROM claim_log WHERE card_id = 7 ORDER BY id")]

    def age(self, seconds, pid=None, state=None):
        """把本人这一行改老（模拟强杀后过了一段时间）。"""
        import datetime as dt
        at = (dt.datetime.now().astimezone() - dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")
        with F.db(write=True) as con:
            con.execute("UPDATE claims SET at = ?, pid = ?, state = COALESCE(?, state) WHERE card_id = 7 AND who = 'owner'",
                        (at, pid, state))

    def unknown_claim(self):
        self.hub_reply = F.HubError("没等到回应")
        A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        self.hub_reply = {"changed": True}
        self.assertEqual(self.claim()["state"], "unknown")
        return self.claim()["attempt"]

    def test_gate_closed_compensation_reports_nothing_to_hub(self):
        """审查 P1-04：恢复闸关着时，对账里的送达补报和排队提交都不碰中台；开闸后各做一次。"""
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = NULL WHERE card_id = 7")
        self.assertEqual(A.answer(self.tok, 1, CHAT, OWNER, self.ver())[0], 3)        # 只排队
        with mock.patch.object(Rv, "ensure_open", side_effect=E.Refused("恢复闸关着：电脑睡醒过，要先对账")):
            A.reconcile()
        self.assertEqual(self.hub_calls, [])
        self.assertEqual(self.claim()["state"], "queued")
        A.reconcile()
        self.assertEqual([c[0] for c in self.hub_calls], ["/v1/cards/7/delivered", "/v1/cards/7/answer"])
        self.assertEqual(self.claim()["state"], "answered")

    def patched_buttons(self):
        """最后一次改卡的按钮回调值。"""
        a = [c for c in self.lark.calls if c[:3] == ["im", "messages", "patch"]][-1]
        content = json.loads(json.loads(a[a.index("--data") + 1])["content"])
        return [e["behaviors"][0]["value"] for e in content["body"]["elements"] if e["tag"] == "button"]

    def test_late_click_on_old_version_button_is_refused(self):
        """#218 第 3 轮 P1-03：第 1 版按钮的点击迟到，卡片已改成第 2 版：直接拒、不排队；第 2 版的新按钮正常。"""
        v1 = self.lark.sent()[-1]["body"]["elements"][1]["behaviors"][0]["value"]
        self.assertEqual(v1["v"], 1)
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 2})                  # 飞书上已改成第 2 版
        self.confirm()
        code, said, _ = A.answer(v1["autoloop"], 1, CHAT, OWNER, v1["v"])
        self.assertEqual(code, 1)
        self.assertIn("第 1 版", said)
        self.assertIsNone(self.claim())
        self.assertEqual(self.log(), [])                                         # 不排队
        self.assertEqual(self.answers(), [])
        with self.assertRaisesRegex(F.Fail, "第 1 版"):                          # 点击一到就拒（不靠登记时那道再核）
            A.check(v1["autoloop"], 1, CHAT, OWNER, v1["v"])
        v2 = self.patched_buttons()
        self.assertEqual({b["v"] for b in v2}, {2})
        self.assertEqual(A.answer(v2[1]["autoloop"], 1, CHAT, OWNER, v2[1]["v"])[0], 0)
        self.assertEqual((self.claim()["version"], self.claim()["state"]), (2, "answered"))
        self.assertEqual(self.answers()[-1][1]["version"], 2)                    # 交给中台的是点的那一版（#278）

    def test_old_style_button_without_version_is_refused(self):
        code, said, _ = A.answer(self.tok, 1, CHAT, OWNER)
        self.assertEqual(code, 1)
        self.assertIn("旧样式", said)
        with mock.patch("sys.stdout"):
            self.assertEqual(A.main([self.tok, "1", "--chat", CHAT, "--operator", OWNER]), 1)
        self.assertIn("旧样式", self.tell.call_args[0][1])
        self.assertIsNone(self.claim())
        self.assertEqual(self.answers(), [])

    def test_newer_version_than_registered_is_refused(self):
        """按钮比登记的新（飞书改好了、登记还没写上）：也不算，请重点。"""
        code, said, _ = A.answer(self.tok, 1, CHAT, OWNER, 2)
        self.assertEqual(code, 1)
        self.assertIn("第 2 版卡片上的按钮", said)
        self.assertIn("这次不算", said)
        self.assertIsNone(self.claim())
        self.assertEqual(self.log(), [])                                         # 不排队（不是排了队再因改版作废）
        with self.assertRaisesRegex(F.Fail, "这次不算"):
            A.check(self.tok, 1, CHAT, OWNER, 2)

    def test_version_changed_between_check_and_claim_is_refused(self):
        """核对过了、登记前桥接刚改了版：写锁里再核一次，不按旧版本排队。"""
        card = A.check(self.tok, 1, CHAT, OWNER, 1)
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET version = 2 WHERE card_id = 7")
        with self.assertRaisesRegex(F.Fail, "第 1 版"):
            A.claim(card, 1, 1)
        self.assertIsNone(self.claim())

    def test_owner_click_answers_with_registered_ref(self):
        code, said, rec = A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        self.assertEqual(code, 0, said)
        self.assertEqual(self.answers(), [("/v1/cards/7/answer", {"choice": 1, "by": OWNER, "ref": "om_fixture_1", "version": 1})])
        self.assertEqual((self.claim()["state"], self.claim()["choice"], self.claim()["attempt"]), ("answered", 1, rec["attempt"]))
        self.assertEqual(self.log(), ["queued", "inflight", "answered"])

    def test_wrong_source_is_quiet_and_never_calls_hub(self):
        for args in ((self.tok, 0, CHAT, "ou_someone_else"), (self.tok, 0, "oc_group", OWNER), (self.tok, 0, CHAT, ""),
                     ("bad", 0, CHAT, OWNER)):
            code, said, rec = A.answer(*args, self.ver())
            self.assertEqual((code, said, rec["quiet"]), (1, None, True))
        with mock.patch("sys.stdout"):
            self.assertEqual(A.main([self.tok, "0", "--version", "1", "--chat", "oc_group", "--operator", OWNER]), 1)
        self.tell.assert_not_called()                                           # 别处来的点击不回话
        self.assertEqual(self.hub_calls, [])
        self.assertIsNone(self.claim())

    def test_owner_checks_that_reply(self):
        for args, why in ((("ffffffffffffffff", 0, CHAT, OWNER), "没有登记"), ((self.tok, 2, CHAT, OWNER), "选项号"),
                          ((self.tok, -1, CHAT, OWNER), "选项号")):
            code, said, _ = A.answer(*args, self.ver())
            self.assertEqual(code, 1)
            self.assertIn(why, said)
        with F.db(write=True) as con:                                           # 登记的私聊和现在的不一致
            con.execute("UPDATE feishu_cards SET chat_id = 'oc_old' WHERE card_id = 7")
        self.assertIn("私聊和现在的对不上", A.answer(self.tok, 0, CHAT, OWNER, self.ver())[1])
        self.assertEqual(self.hub_calls, [])

    def test_missing_identity_stops_everything(self):
        with mock.patch.dict(os.environ, {"DT_OWNER_CHAT_ID": "", "DT_OWNER_CHAT_FILE": self.tmp.name + "/none"}):
            self.assertEqual(A.answer(self.tok, 0, "", OWNER, self.ver())[:2], (1, None))
            with self.assertRaisesRegex(A.Quiet, "没配置"):
                A.identities()
            with self.assertRaisesRegex(F.Fail, "setup-chat"):
                F.owner_chat()
            self.assertEqual(A.reconcile(), [])
        with mock.patch.dict(os.environ, {"DT_OWNER_OPEN_ID": "", "DT_OWNER_FILE": self.tmp.name + "/none"}):
            with self.assertRaisesRegex(A.Quiet, "没配置"):
                A.identities()
        self.assertEqual(self.hub_calls, [])

    def test_click_before_message_id_or_delivery_is_queued_then_submitted_once(self):
        with F.db(write=True) as con:                                           # 令牌登记了、消息号还没落盘（审查 P2-05）
            con.execute("INSERT INTO feishu_cards (card_id, token, chat_id, kind, options, version) "
                        "VALUES (8, 'aaaaaaaaaaaaaaaa', ?, 'ask', 2, 1)", (CHAT,))
        self.assertEqual(A.answer("aaaaaaaaaaaaaaaa", 0, CHAT, OWNER, 1)[0], 3)
        with F.db(write=True) as con:
            con.execute("UPDATE claims SET card_id = card_id WHERE card_id = 8")
            con.execute("UPDATE feishu_cards SET message_id = 'om_8' WHERE card_id = 8")
        self.assertEqual(A.flush(8)[0], 3)                                      # 有消息号、中台还没确认送达：仍排队
        self.assertEqual(self.answers(), [])
        self.confirm(8)
        self.hub_card = {"id": 8, "state": "decided", "choice": 0, "by": OWNER}
        self.assertEqual(A.flush(8)[0], 0)
        self.assertEqual(self.answers(), [("/v1/cards/8/answer", {"choice": 0, "by": OWNER, "ref": "om_8", "version": 1})])
        self.assertIsNone(A.flush(8))                                           # 只发一次
        self.tell.assert_called_once()

    def test_queued_click_on_old_version_is_not_moved_to_new_version(self):
        """#218 第 2 轮 P1-03：在第 1 版点的、还在排队的选择，卡片改成第 2 版后作废、请常浩重点，不交到第 2 版。"""
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = NULL WHERE card_id = 7")
        self.assertEqual(A.answer(self.tok, 1, CHAT, OWNER, self.ver())[0], 3)                 # 第 1 版还没确认送达：排队
        self.assertEqual(self.claim()["version"], 1)
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 2})                  # 改成第 2 版、飞书改好了
        self.confirm()
        code, said = A.flush(7)
        self.assertEqual(code, 1)
        self.assertIn("改版", said)
        self.assertEqual(self.answers(), [])
        self.assertIsNone(self.claim())
        self.assertEqual(self.log()[-1], "released")
        self.hub_card = {"id": 7, "state": "decided", "choice": 0, "by": OWNER}
        self.assertEqual(A.answer(self.tok, 0, CHAT, OWNER, self.ver())[0], 0)                 # 在第 2 版上重点：照常交
        self.assertEqual(self.claim()["version"], 2)

    def test_version_change_clears_hub_confirmation_and_confirms_only_reported_version(self):
        """#218 第 2 轮 P1-03：改版时清掉中台确认；报第 1 版送达期间改成了第 2 版，第 1 版的确认不记到第 2 版头上。"""
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 2})
        self.assertIsNone(F.card_by_token(self.tok)["hub_delivered_at"])
        self.assertEqual(A.answer(self.tok, 1, CHAT, OWNER, self.ver())[0], 3)                 # 没确认第 2 版：只排队
        self.assertEqual(self.answers(), [])

        def hub(method, path, body=None, timeout=20):
            if path.endswith("/delivered"):
                with F.db(write=True) as con:                                    # 报送期间又改成第 3 版
                    con.execute("UPDATE feishu_cards SET version = 3 WHERE card_id = 7")
            return {}
        with mock.patch.object(F, "hub", hub):
            F.report_delivered(7)
        self.assertIsNone(F.card_by_token(self.tok)["hub_delivered_at"])

    def test_concurrent_submitters_send_at_most_once(self):
        import threading
        for second in (lambda: A.flush(7), lambda: A.reconcile(), lambda: A.submit(7)):
            with F.db(write=True) as con:
                con.execute("DELETE FROM claims")
            self.hub_calls.clear()
            self.gate, self.entered = threading.Event(), threading.Event()
            t = threading.Thread(target=A.answer, args=(self.tok, 1, CHAT, OWNER, self.ver()))    # 本人点击领到资格、请求卡在半路
            t.start()
            self.assertTrue(self.entered.wait(5))
            second()                                                            # 送达后的 flush / 对账 / 再一次提交
            self.gate.set()
            t.join(5)
            self.assertEqual(len(self.answers()), 1, second)
            self.assertEqual(self.claim()["state"], "answered")
        self.gate = None

    def test_queued_raced_by_flush_and_reconcile_sends_once(self):
        import threading
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = NULL WHERE card_id = 7")
        self.assertEqual(A.answer(self.tok, 1, CHAT, OWNER, self.ver())[0], 3)
        self.confirm()
        self.gate, self.entered = threading.Event(), threading.Event()
        t = threading.Thread(target=A.flush, args=(7,))
        t.start()
        self.assertTrue(self.entered.wait(5))
        self.assertEqual(A.reconcile(), [])                                     # 对账看到在途、进程还活着：不动
        self.gate.set()
        t.join(5)
        self.gate = None
        self.assertEqual(len(self.answers()), 1)

    def test_delivered_accepted_but_not_recorded_is_rereported_then_submitted(self):
        with F.db(write=True) as con:                                           # 中台已收下送达报告，本地没来得及记
            con.execute("UPDATE feishu_cards SET hub_delivered_at = NULL WHERE card_id = 7")
        self.assertEqual(A.answer(self.tok, 1, CHAT, OWNER, self.ver())[0], 3)
        self.assertEqual(self.hub_calls, [])
        notes = A.reconcile()                                                   # 重启后对账：按登记原样重报，再提交
        self.assertEqual([c[0] for c in self.hub_calls], ["/v1/cards/7/delivered", "/v1/cards/7/answer"])
        self.assertEqual(self.hub_calls[0][1], {"version": 1, "ref": "om_fixture_1"})
        self.assertEqual(notes, [(7, "收到，已交给中台。")])
        self.assertEqual(len(self.lark.sent()), 1)                              # 没有发新的飞书卡片

    def test_queued_on_card_that_already_ended_is_settled_without_sending(self):
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = NULL WHERE card_id = 7")
        A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        self.hub_card = {"id": 7, "state": "closed"}
        with mock.patch.object(F, "report_delivered", side_effect=F.HubError("中台回 HTTP 409", status=409)):
            A.reconcile()                                                       # 中台不收这份送达、卡已结束
        self.assertEqual(self.claim()["state"], "settled")
        self.assertEqual(self.answers(), [])

    def test_queued_on_open_card_stays_queued_when_delivery_refused(self):
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = NULL WHERE card_id = 7")
        A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        self.hub_card = {"id": 7, "state": "open"}
        with mock.patch.object(F, "report_delivered", side_effect=F.HubError("中台回 HTTP 409", status=409)):
            self.assertEqual(A.reconcile(), [])
        self.assertEqual(self.claim()["state"], "queued")                       # 卡还开着：排队的不结算
        self.assertEqual(self.answers(), [])

    def test_token_is_single_use(self):
        A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        code, said, _ = A.answer(self.tok, 0, CHAT, OWNER, self.ver())
        self.assertEqual(code, 1)
        self.assertIn("已经点过", said)
        self.assertEqual(len(self.answers()), 1)

    def test_secretary_rows_are_never_touched(self):
        with F.db(write=True) as con:
            con.execute("INSERT INTO claims (card_id, who, state, choice, at) VALUES (7, 'secretary', 'approved', 0, 'x')")
        code, said, _ = A.answer(self.tok, 1, CHAT, OWNER, self.ver())                      # 秘书已获准：本人照样提交，中台先受理的算
        self.assertEqual(code, 0)
        self.assertIn("以中台先受理的为准", said)
        with F.db() as con:
            self.assertEqual(con.execute("SELECT state FROM claims WHERE who = 'secretary'").fetchone()[0], "approved")
            self.assertTrue(F.owner_holds(con, 7))                              # 给 4c：本人有一行，秘书不许再获准

    def test_secretary_first_settles_owner(self):
        self.hub_reply, self.hub_card = {"changed": False}, {"id": 7, "state": "decided", "choice": 0, "by": "秘书"}
        code, said, _ = A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        self.assertEqual((code, self.claim()["state"]), (1, "settled"))
        self.assertIn("秘书选了第 0 项", said)

    def test_error_classes(self):
        for err, code, state in ((F.HubError("中台回 HTTP 401", status=401), 1, None),
                                 (F.HubError("中台回 HTTP 404", status=404), 1, None),
                                 (F.HubError("读不到中台配置", sent=False), 1, None),
                                 (F.HubError("中台回 HTTP 500", status=500), 2, "unknown"),
                                 (F.HubError("中台回 HTTP 503", status=503), 2, "unknown"),
                                 (F.HubError("没等到回应"), 2, "unknown"),
                                 (RuntimeError("意外"), 2, "unknown")):
            self.hub_reply = err
            got = A.answer(self.tok, 1, CHAT, OWNER, self.ver())
            self.assertEqual(got[0], code, (err, got))
            self.assertEqual((self.claim() or {}).get("state"), state, err)
            if state is None:
                self.assertEqual(self.log()[-1], "released")                   # 明确没受理：释放，可以再点
                self.assertIn("可以再点一次", got[1])
            else:
                self.assertIn("不会重答", got[1])
            with F.db(write=True) as con:
                con.execute("DELETE FROM claims")
        self.hub_reply, self.hub_card = {"changed": True}, {"id": 7, "state": "open"}
        self.assertEqual(A.answer(self.tok, 1, CHAT, OWNER, self.ver())[0], 2)             # 说收下了但回读对不上：结果不明
        self.assertEqual(self.claim()["state"], "unknown")

    def test_closed_gate_keeps_click_queued_then_submits_once(self):
        """批 0D：恢复闸关着时本人点击只排队，不交中台；开闸后提交一次。"""
        with mock.patch.object(Rv, "ensure_open", side_effect=E.Refused("恢复闸关着：电脑睡醒过，要先对账")):
            code, said, _ = A.answer(self.tok, 1, CHAT, OWNER, self.ver())
            self.assertEqual(code, 3)
            self.assertIn("睡醒", said)
            self.assertEqual(A.flush(7)[0], 3)
        self.assertEqual((self.answers(), self.claim()["state"]), ([], "queued"))
        self.assertEqual(A.flush(7)[0], 0)
        self.assertIsNone(A.flush(7))
        self.assertEqual(len(self.answers()), 1)

    def test_unknown_answer_closes_the_gate_before_recording_it(self):
        """同 L3 第 3 轮 P1-08：本人回答结果不明先关闸、再在卡片库记 unknown；记的那一刻闸已经关了。"""
        seen, real = [], F.log_claim

        def log_claim(con, card_id, who, state, *a, **kw):
            if state == "unknown":
                with E.db(write=False) as econ:
                    seen.append(econ.execute("SELECT dirty FROM gate WHERE id = 1").fetchone())
            return real(con, card_id, who, state, *a, **kw)
        with mock.patch.object(F, "log_claim", log_claim):
            self.unknown_claim()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0] and seen[0][0])

    def test_gate_close_failure_still_records_unknown(self):
        """L3 第 4 轮 P1-08：关闸两步都失败时，本人回答照样记成结果不明（不停在在途），再把错报出来。"""
        self.hub_reply = F.HubError("没等到回应")
        with mock.patch.object(E, "close_gate", side_effect=OSError("盘满了")), self.assertRaises(OSError):
            A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        self.assertEqual(self.claim()["state"], "unknown")

    def test_unknown_answer_closes_the_gate_and_gate_reconcile_skips_queued(self):
        def dirty():
            with E.db(write=False) as con:
                r = con.execute("SELECT dirty FROM gate WHERE id = 1").fetchone()
                return r[0] if r else 0
        self.assertEqual(dirty(), 0)
        self.unknown_claim()
        self.assertGreater(dirty(), 0)                                          # 本人回答结果不明：恢复闸关上
        with F.db(write=True) as con:
            con.execute("UPDATE claims SET state = 'queued'")
        with mock.patch.object(A, "submit") as submit:
            A.reconcile(queued=False)                                           # 恢复闸对账：不提交排队的
            submit.assert_not_called()

    def test_hub_409_means_card_changed_version(self):
        """#278：回答带着点的那一版；中台说版本对不上（409）→ 这次不算数、释放、请常浩在新版卡片上重点，不让他在旧卡上再点。"""
        for seen, want, known in (({"id": 7, "state": "open", "version": 2}, "你点的是第 1 版，中台已经是第 2 版", True),
                                  (None, "已经不是你点的第 1 版", False),             # 只读窗口读不到：不编版本号
                                  ({"id": 7, "state": "open"}, "已经不是你点的第 1 版", False)):
            with self.subTest(seen=seen):
                self.hub_reply, self.hub_card = F.HubError("中台回 HTTP 409", status=409), seen
                code, said, _ = A.answer(self.tok, 1, CHAT, OWNER, self.ver())
                self.assertEqual(code, 1, said)
                self.assertIsNone(self.claim())                                  # 中台明确没受理：释放
                self.assertEqual(self.log()[-1], "released")
                self.assertIn(want, said)
                self.assertIn("没有算数", said)
                self.assertIn("新版", said)
                self.assertNotIn("可以再点一次", said)                           # 旧卡上再点只会再被拒
                self.assertNotIn("HTTP", said)
                self.assertEqual("中台已经是" in said, known)                    # 读不到中台版本时不编数字
                self.assertEqual("没读到" in said, not known)
                self.assertEqual(self.answers()[-1][1]["version"], 1)

    def test_hub_409_released_before_reading_hub(self):
        """审查建议 4：先释放再读中台拼话；读中台时进程没了，也不留下一行在途（否则对账会记成结果不明、要常浩手动放开）。"""
        seen = []

        def read(cid):
            seen.append(self.claim())
            return {"id": 7, "state": "open", "version": 2}
        self.hub_reply = F.HubError("中台回 HTTP 409", status=409)
        with mock.patch.object(A, "read_card", read):
            self.assertEqual(A.answer(self.tok, 1, CHAT, OWNER, self.ver())[0], 1)
        self.assertEqual(seen, [None])

    def test_hub_409_with_same_version_is_plain_refusal(self):
        """中台回 409、回读版本却和点的一样（按引擎现在的规矩不会发生）：不说改版，照一般的没受理说。"""
        self.hub_reply, self.hub_card = F.HubError("中台回 HTTP 409", status=409), {"id": 7, "state": "open", "version": 1}
        code, said, _ = A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        self.assertEqual(code, 1)
        self.assertIn("可以再点一次", said)
        self.assertNotIn("改版", said)
        self.assertIsNone(self.claim())

    def test_hub_409_with_older_hub_version_asks_for_a_check(self):
        """审查建议 3：中台的版本比点的还旧（中台库被恢复成旧快照）：不会有新版送来，不说「新版卡片送到后重点」，请人工核查。"""
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 2})                  # 飞书上、登记里都是第 2 版
        self.confirm()
        self.hub_reply, self.hub_card = F.HubError("中台回 HTTP 409", status=409), {"id": 7, "state": "open", "version": 1}
        code, said, _ = A.answer(self.tok, 1, CHAT, OWNER, 2)
        self.assertEqual(code, 1, said)
        self.assertEqual(self.answers()[-1][1]["version"], 2)
        self.assertIn("中台里这张卡是第 1 版，比你点的第 2 版还旧", said)
        self.assertIn("人工核查", said)
        self.assertNotIn("新版", said)
        self.assertNotIn("可以再点一次", said)
        self.assertIsNone(self.claim())

    def test_unknown_with_open_card_is_never_released_automatically(self):
        self.unknown_claim()
        self.hub_card = {"id": 7, "state": "open"}
        self.age(10_000, pid=None)                                              # 进程早没了、过了很久、卡仍开着
        self.assertEqual(A.reconcile(), [])
        self.assertEqual(self.claim()["state"], "unknown")                      # 不放开（原请求可能晚到）
        self.assertIn("结果不明", A.answer(self.tok, 0, CHAT, OWNER, self.ver())[1])
        self.assertEqual(len(self.answers()), 1)                                # 也从不重答
        with mock.patch("sys.stdout"):
            self.assertEqual(A.main(["release", "--card", "7"]), 1)             # 没写 --confirm 不放
        self.assertIn("已放开", A.release_unknown(7))
        self.hub_card = {"id": 7, "state": "decided", "choice": 0, "by": OWNER}
        self.assertEqual(A.answer(self.tok, 0, CHAT, OWNER, self.ver())[0], 0)

    def test_release_unknown_settles_instead_if_card_has_result(self):
        self.unknown_claim()
        self.hub_card = {"id": 7, "state": "decided", "choice": 1, "by": OWNER}
        self.assertIn("没放开", A.release_unknown(7))
        self.assertEqual(self.claim()["state"], "answered")
        with self.assertRaisesRegex(F.Fail, "没有结果不明"):
            A.release_unknown(7)

    BAD_CARDS = ({"id": 7}, {"id": 7, "state": None}, {"id": 7, "state": "unexpected"},
                 {"id": 7, "state": "decided", "choice": None, "by": OWNER}, {"id": 7, "state": "done", "choice": 1, "by": None})

    def test_judge_only_trusts_the_four_hub_states(self):
        for c in self.BAD_CARDS:
            self.assertIsNone(A.judge(c, 1, OWNER), c)
            self.assertFalse(A.ended(c), c)
        self.assertEqual(A.judge({"state": "open"}, 1, OWNER), "open")
        self.assertEqual(A.judge({"state": "closed"}, 1, OWNER), "settled")
        self.assertEqual(A.judge({"state": "done", "choice": 1, "by": OWNER}, 1, OWNER), "answered")
        self.assertEqual(A.judge({"state": "decided", "choice": 0, "by": OWNER}, 1, OWNER), "settled")

    def test_unrecognised_state_never_releases_unknown(self):
        old = self.unknown_claim()
        self.age(10_000, pid=None)
        for c in self.BAD_CARDS:
            self.hub_card = c
            with self.assertRaisesRegex(F.Fail, "认不出"):
                A.release_unknown(7)                                            # 常浩同意了也不放：没证据说卡还开着
            self.assertEqual(A.reconcile(), [])
            self.assertEqual((self.claim()["attempt"], self.claim()["state"]), (old, "unknown"), c)
        self.assertEqual(len(self.answers()), 1)                                # 从不重答

    def test_unrecognised_state_turns_stale_inflight_unknown_not_released(self):
        for c in self.BAD_CARDS:
            with F.db(write=True) as con:
                con.execute("DELETE FROM claims")
            att = A.claim(F.card_by_token(self.tok), 1, self.ver())[0]
            self.age(10_000, pid=None, state="inflight")                        # 发到一半进程没了
            self.hub_card = c
            notes = A.reconcile()
            self.assertEqual((self.claim()["attempt"], self.claim()["state"]), (att, "unknown"), c)
            self.assertIn("认不出", notes[0][1])
        self.assertEqual(self.answers(), [])

    def test_unrecognised_state_never_settles_queued(self):
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = NULL WHERE card_id = 7")
        A.answer(self.tok, 1, CHAT, OWNER, self.ver())
        att = self.claim()["attempt"]
        for c in self.BAD_CARDS:
            self.hub_card = c
            with mock.patch.object(F, "report_delivered", side_effect=F.HubError("中台回 HTTP 409", status=409)):
                self.assertEqual(A.reconcile(), [])
            self.assertEqual((self.claim()["attempt"], self.claim()["state"]), (att, "queued"), c)
        self.assertEqual(self.answers(), [])

    def test_old_cards_db_without_attempt_is_refused(self):
        f = os.environ["DT_AUTOLOOP_CARDS_DB"]
        os.remove(f)
        import sqlite3
        con = sqlite3.connect(f)
        con.executescript("CREATE TABLE claims (card_id INTEGER NOT NULL, who TEXT NOT NULL, state TEXT NOT NULL, "
                          "choice INTEGER, at TEXT NOT NULL, pid INTEGER, note TEXT, PRIMARY KEY (card_id, who));")
        con.close()
        with self.assertRaisesRegex(F.Fail, "旧结构"):
            with F.db():
                pass
        con = sqlite3.connect(f)
        self.assertNotIn("attempt", [r[1] for r in con.execute("PRAGMA table_info(claims)")])  # 没有偷偷改旧库
        self.assertEqual([r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")],
                         ["claims"])                                            # 也没补建别的表
        con.close()

    def test_claims_without_version_column_is_refused(self):
        with contextlib.closing(sqlite3.connect(self.tmp.name + "/old.db")) as con, con:
            con.execute("CREATE TABLE claims (card_id INTEGER, who TEXT, state TEXT, choice INTEGER, at TEXT, pid INTEGER, "
                        "note TEXT, attempt TEXT, PRIMARY KEY (card_id, who))")
        with mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": self.tmp.name + "/old.db"}), \
                self.assertRaisesRegex(F.Fail, "旧结构"):
            with F.db():
                pass

    def test_stale_writes_never_touch_a_newer_attempt(self):
        old = self.unknown_claim()
        self.hub_card = {"id": 7, "state": "open"}
        self.assertTrue(A.release(7, old, "unknown", "常浩同意放开"))
        A.claim(F.card_by_token(self.tok), 0, self.ver())                                   # 常浩再点：新的尝试
        new = self.claim()["attempt"]
        self.assertNotEqual(new, old)
        self.assertFalse(A.release(7, old, "unknown", "过期的对账"))            # 拿着旧快照的对账：删不掉新尝试
        self.assertFalse(A.move(7, old, ("unknown", "inflight"), "settled"))   # 也改不了
        self.assertFalse(A.move(7, new, "inflight", "answered"))               # 预期状态不对也不改
        self.assertFalse(A.move(7, old, "queued", "settled"))                  # 状态碰巧一样、尝试号不对：也不改
        self.assertEqual((self.claim()["attempt"], self.claim()["state"]), (new, "queued"))
        with F.db() as con:
            self.assertTrue(F.owner_holds(con, 7))

    def test_reconcile_after_kill(self):
        A.claim(F.card_by_token(self.tok), 1, self.ver())
        self.age(30, pid=os.getpid(), state="inflight")                         # 领到资格后进程被杀
        self.assertEqual(A.reconcile(), [])                                     # 进程还在、没超时：不动
        self.age(30, pid=999999)
        self.assertEqual(A.reconcile(), [])                                     # 进程不在但还没过 2 分钟：不动
        self.age(200, pid=999999)
        self.hub_card = {"id": 7, "state": "open"}
        self.assertIn("结果不明", A.reconcile()[0][1])                          # 卡还开着：改成结果不明，不放开
        self.assertEqual(self.claim()["state"], "unknown")
        self.hub_card = {"id": 7, "state": "decided", "choice": 1, "by": OWNER}  # 其实中台已受理
        self.assertEqual(A.reconcile()[0][1], "对账结果：你上次的回答中台已经收下了。")
        with F.db(write=True) as con:
            con.execute("DELETE FROM claims")
        A.claim(F.card_by_token(self.tok), 1, self.ver())
        self.age(700, pid=os.getpid(), state="inflight")                        # 进程号像是活的但超过 10 分钟：照样对账
        self.hub_card = None                                                    # 读不到中台：改成结果不明
        A.reconcile()
        self.assertEqual(self.claim()["state"], "unknown")
        self.hub_card = {"id": 7, "state": "closed"}
        A.reconcile()
        self.assertEqual(self.claim()["state"], "settled")
        self.assertEqual(self.answers(), [])                                    # 对账从不重答

    def test_reconcile_checks_who_and_which_option(self):
        for card in ({"id": 7, "state": "decided", "choice": 1, "by": "秘书"},
                     {"id": 7, "state": "done", "choice": 0, "by": OWNER}):
            self.unknown_claim()
            self.hub_card = card
            A.reconcile()
            self.assertEqual(self.claim()["state"], "settled", card)
            with F.db(write=True) as con:
                con.execute("DELETE FROM claims")

    def test_main_replies_to_home_chat(self):
        with mock.patch("sys.stdout"):
            self.assertEqual(A.main([self.tok, "1", "--version", "1", "--chat", CHAT, "--operator", OWNER]), 0)
        self.assertEqual(self.tell.call_args[0][0], CHAT)
        with mock.patch("sys.stdout") as out:
            self.assertEqual(A.main(["reconcile"]), 0)
        self.assertIn("notes", "".join(str(c.args[0]) for c in out.write.call_args_list))


class BridgeLock(Base):
    """#218 第 3 轮 P2-01：桥接单写入者锁；领到的旧版本不处理，登记的版本不回退。"""
    def setUp(self):
        super().setUp()
        self.register()
        self.posts = []
        self.leases = {}
        self.hub_now = None                                                     # 中台库里这张卡现在的版本；None = 读不到
        self.hub_ok = True                                                      # 领卡前中台库能不能读
        self.gets = 0
        self.fail_post = None                                                   # 设成异常时，报 failed 抛它
        self.alerts = []                                                        # 私聊提醒的文字
        self.flushed = []                                                       # (卡片号, 提交时桥接锁是不是空着)
        for target, name, fake in ((F, "hub", self.fake_hub), (A, "flush", self.fake_flush),
                                   (F, "hub_readable", lambda: self.hub_ok)):
            p = mock.patch.object(target, name, fake)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch("autoloop_publish.hub_card",
                       lambda cid: None if self.hub_now is None else {"id": cid, "version": self.hub_now, "state": "open"})
        p.start()
        self.addCleanup(p.stop)

        p = mock.patch("tidings_weekly.send", lambda chat, kind, body, key: self.alerts.append(body["text"]))
        p.start()
        self.addCleanup(p.stop)

    def fake_flush(self, cid):
        import fcntl
        fd = os.open(f"{F.db_file()}.bridge.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            free = True
        except OSError:
            free = False
        finally:
            os.close(fd)
        self.flushed.append((cid, free))

    def fake_hub(self, method, path, body=None, timeout=20):
        if method == "GET":
            self.gets += 1
            return {"card": self.leases[threading.current_thread().name]}
        if path.endswith("/failed") and self.fail_post is not None:
            raise self.fail_post
        self.posts.append((path, body))
        return {}

    def lease(self, version, name=None):
        self.leases[name or threading.current_thread().name] = {**CARD, "ref": "om_fixture_1", "version": version}

    def registered(self):
        with F.db() as con:
            return con.execute("SELECT version FROM feishu_cards WHERE card_id = 7").fetchone()[0]

    def patches(self):
        return [json.loads(json.loads(a[a.index("--data") + 1])["content"]) for a in self.lark.calls
                if a[:3] == ["im", "messages", "patch"]]

    def test_out_of_order_old_lease_is_skipped(self):
        self.hub_now = 3
        self.lease(3)
        self.assertTrue(F.once()["ok"])
        self.lease(2)                                                            # 第 2 版的领取迟到
        out = F.once()
        self.assertIn("第 3 版", out["skipped"])
        self.assertEqual(self.registered(), 3)
        self.assertEqual(len(self.patches()), 1)                                 # 没把消息改回第 2 版
        self.assertEqual([b["version"] for _, b in self.posts], [3])             # 只报过第 3 版送达

    def test_hub_already_newer_skips_lease_then_next_poll_delivers_latest(self):
        self.hub_now = 3
        self.lease(2)
        out = F.once()
        self.assertIn("中台这张卡已经是第 3 版", out["skipped"])
        self.assertEqual((self.registered(), self.patches(), self.posts), (1, [], []))   # 不改消息、不报送达
        self.lease(3)                                                            # 中台过一会再给（最新版本）
        self.assertTrue(F.once()["ok"])
        self.assertEqual(self.registered(), 3)
        self.assertEqual([b["version"] for _, b in self.posts], [3])
        self.assertEqual({b["behaviors"][0]["value"]["v"] for b in self.patches()[0]["body"]["elements"] if b["tag"] == "button"}, {3})

    def test_local_newer_than_hub_reports_failed_with_reason(self):
        """第 6 轮 P2-01：本地已登记更高版本（中台库被恢复成旧快照）：中台下次还是递这一版，所以报 failed 写明冲突
        （走中台退避，不是 1 分钟一跳地用掉次数），不改消息，每张卡 1 小时最多提醒 1 次。"""
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 3})
        self.hub_now = 2
        for _ in range(2):
            self.lease(2)
            out = F.once()
            self.assertFalse(out["ok"])
        self.assertEqual(self.posts, [("/v1/cards/7/failed", {"version": 2, "error": "本地已登记第 3 版，比中台的第 2 版还新"
                                                              "（中台库可能被恢复成旧快照），这次没发"})] * 2)
        self.assertEqual(len(self.patches()), 1)                                 # 只有最早那次第 3 版
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("中台会稍后重试", self.alerts[0])
        self.assertEqual(self.flushed, [])

    def test_conflict_alert_not_suppressed_by_unreadable_alert(self):
        """「中台读不到」的提醒刚发过（1 小时限频）时，版本冲突的提醒照样发：各用各的提醒键。"""
        self.hub_ok = False
        F.once()                                                                # 领卡前读不到：提醒 1 次
        self.hub_ok = True
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 3})
        self.hub_now = 2
        self.lease(2)
        F.once()
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("比中台的第 2 版还新", self.alerts[1])

    def test_report_failed_error_still_alerts(self):
        """第 6 轮 P2-02：报 failed 本身出错（卡不在 404、中台 500、没回应），不抛出、照样提醒，写明请人工核查。"""
        for i, err in enumerate((F.HubError("HTTP 404", status=404), F.HubError("HTTP 500", status=500),
                                 F.HubError("没等到回应"))):
            with self.subTest(err=str(err)):
                self.fail_post = err
                self.hub_now = None
                self.lease(2)
                out = F.once()
                self.assertFalse(out["ok"])
                self.assertIn(str(err), out["report_error"])
                self.assertEqual(len(self.alerts), i + 1)
                self.assertIn("请人工核查", self.alerts[-1])
                self.assertIn(str(err), self.alerts[-1])
                with F.db(write=True) as con:                                   # 下一种错误当成另一次（提醒键按卡片 1 小时限频）
                    con.execute("DELETE FROM alerts")
        self.assertEqual((self.patches(), self.flushed), ([], []))

    def test_report_failed_alert_not_suppressed_by_unreadable_alert(self):
        """中台读不到的提醒刚发过（1 小时限频）时，报 failed 也失败的那条照样发：单独的提醒键。"""
        self.hub_now = None
        self.lease(2)
        F.once()                                                                # 报 failed 成功：发「中台会稍后重试」
        self.fail_post = F.HubError("HTTP 500", status=500)
        F.once()
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("请人工核查", self.alerts[1])

    def test_hub_unreadable_before_lease_does_not_lease(self):
        """第 5 轮 P1-01：领卡前中台库读不到，这轮不领（不用掉中台的尝试次数），私聊提醒，同一原因 1 小时最多 1 次。"""
        self.hub_ok = False
        for _ in range(3):
            out = F.once()
            self.assertIn("读不到中台库", out["paused"])
        self.assertEqual((self.gets, self.posts, self.patches()), (0, [], []))
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("不会用掉中台的重试次数", self.alerts[0])
        with F.db(write=True) as con:                                           # 过了 1 小时：再提醒一次
            con.execute("UPDATE alerts SET at = '2000-01-01T00:00:00+08:00'")
        F.once()
        self.assertEqual(len(self.alerts), 2)
        self.hub_ok, self.hub_now = True, 1                                     # 恢复：照常领、照常送
        self.lease(1)
        self.assertTrue(F.once()["ok"])
        self.assertEqual(self.gets, 1)

    def test_alert_not_recorded_when_send_fails(self):
        self.hub_ok = False
        with mock.patch("tidings_weekly.send", side_effect=F.SendError("发不出去")):
            F.once()
        F.once()                                                                # 上次没发出去：这次照样提醒
        self.assertEqual(len(self.alerts), 1)

    def test_hub_unreadable_after_lease_reports_failed_with_reason(self):
        """领到后才读不到这张卡的版本（None、True、"2"、2.0）：不发，向中台报 failed 写明原因（中台退避重试），并提醒。"""
        for hub_now in (None, True, "2", 2.0):
            with self.subTest(hub_now=hub_now):
                self.posts.clear()
                self.hub_now = hub_now
                self.lease(2)
                out = F.once()
                self.assertFalse(out["ok"])
                self.assertEqual(self.posts, [("/v1/cards/7/failed", {"version": 2, "error": "读不到中台库里这张卡的版本，"
                                                                      "核不了是不是最新版，这次没发"})])
                self.assertEqual((self.registered(), self.patches()), (1, []))
        self.assertEqual(len(self.lark.sent()), 1)                               # 也没另发新卡
        self.assertEqual(len(self.alerts), 1)                                    # 同一原因只提醒一次
        self.assertEqual(self.flushed, [])

    def test_flush_runs_after_lock_released(self):
        """第 5 轮 P2：报完送达就放锁，再提交排队的本人回答。"""
        self.hub_now = 2
        self.lease(2)
        self.assertTrue(F.once()["ok"])
        self.assertEqual(self.flushed, [(7, True)])
        F.confirm(7)
        self.assertEqual(self.flushed[-1], (7, True))

    def test_run_checks_given_up_and_rests_when_paused(self):
        """常驻每轮先扫一次已放弃的卡；中台库读不到（paused）时歇 30 秒再看，不空转。"""
        calls = []

        def once():
            calls.append("once")
            if calls.count("once") > 1:
                raise KeyboardInterrupt                                          # 第二轮停下
            return {"ok": False, "paused": "读不到中台库"}
        with mock.patch.object(A, "reconcile", lambda: []), mock.patch.object(F, "once", once), \
                mock.patch.object(F, "check_given_up", lambda: calls.append("given_up") or []), \
                mock.patch.object(F.time, "sleep") as sleep, mock.patch("sys.stdout"), self.assertRaises(KeyboardInterrupt):
            F.run()
        self.assertEqual(calls, ["given_up", "once", "given_up", "once"])
        sleep.assert_called_once_with(30)

    def test_given_up_cards_told_once_each(self):
        """中台已放弃送达的卡：每张私聊说一次（哪张、标题、试了几次、最后原因），并给出重新送的命令（#278）。"""
        cards = [{"card_id": 7, "title": "选一个", "tries": 9, "error": "读不到中台库里这张卡的版本",
                  "first_try_at": "2026-11-20T10:00:00.000Z", "state": "open"}]
        with mock.patch.object(F, "given_up_cards", lambda: cards):
            self.assertEqual(F.check_given_up(), [7])
            self.assertEqual(F.check_given_up(), [])
            cards.append({"card_id": 8, "title": None, "tries": 9, "error": None, "first_try_at": "2026-11-20T11:00:00.000Z",
                          "state": None})                                        # 卡片行查不到：按还开着说
            self.assertEqual(F.check_given_up(), [8])
            self.assertEqual(F.check_given_up(), [])
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("卡片 7「选一个」已放弃送达（试了 9 次", self.alerts[0])
        self.assertIn("agent-autoloop redeliver 7", self.alerts[0])
        self.assertNotIn("请手动处理", self.alerts[0])
        self.assertNotIn("又放弃", self.alerts[0])
        self.assertIn("没有记录原因", self.alerts[1])
        self.assertIn("agent-autoloop redeliver 8", self.alerts[1])
        with mock.patch.object(F, "given_up_cards", lambda: None):              # 中台库读不到：不提醒、不报错
            self.assertEqual(F.check_given_up(), [])

    def test_given_up_again_after_redeliver_is_told_again(self):
        """#278（常浩 11-20 定）：同一张卡重新送之后又被放弃，再提醒一次；同一轮放弃仍只说一次。"""
        first = {"card_id": 7, "title": "选一个", "tries": 9, "error": "飞书发不出去",
                 "first_try_at": "2026-11-20T10:00:00.000Z", "state": "open"}
        cards = [first]
        with mock.patch.object(F, "given_up_cards", lambda: cards):
            self.assertEqual(F.check_given_up(), [7])
            cards.clear()                                                        # 常浩跑了 redeliver：放弃标记清了
            self.assertEqual(F.check_given_up(), [])
            cards.append({**first, "tries": 3, "first_try_at": "2026-11-20T12:30:00.000Z", "error": "还是发不出去"})
            self.assertEqual(F.check_given_up(), [7])                            # 又放弃：再说一次
            self.assertEqual(F.check_given_up(), [])                             # 这一轮只说一次
        self.assertEqual(len(self.alerts), 2)
        self.assertIn("重新送之后又放弃了", self.alerts[1])
        self.assertIn("试了 3 次，最后原因：还是发不出去", self.alerts[1])
        self.assertIn("agent-autoloop redeliver 7", self.alerts[1])

    def test_given_up_again_is_told_even_if_never_seen_in_between(self):
        """重新送和再次放弃之间桥接一次都没扫到（停着、睡着）：照样再提醒（按每一轮第一次尝试的时间认，不靠中间看到过）。"""
        cards = [{"card_id": 7, "title": "选一个", "tries": 9, "error": "x", "first_try_at": "2026-11-20T10:00:00.000Z",
                  "state": "open"}]
        with mock.patch.object(F, "given_up_cards", lambda: cards):
            self.assertEqual(F.check_given_up(), [7])
            cards[0] = {**cards[0], "first_try_at": "2026-11-20T12:30:00.000Z"}
            self.assertEqual(F.check_given_up(), [7])
        self.assertIn("重新送之后又放弃了", self.alerts[1])

    def test_given_up_alert_not_recorded_when_send_fails(self):
        """提醒发不出去不记，下次再试（原有规矩，换了提醒键以后照旧）。"""
        cards = [{"card_id": 7, "title": "选一个", "tries": 9, "error": "x", "first_try_at": "2026-11-20T10:00:00.000Z",
                  "state": "open"}]
        with mock.patch.object(F, "given_up_cards", lambda: cards):
            with mock.patch("tidings_weekly.send", side_effect=F.SendError("发不出去")):
                self.assertEqual(F.check_given_up(), [])
            self.assertEqual(F.check_given_up(), [7])
        self.assertNotIn("又放弃", self.alerts[0])                               # 上次没发成，不算说过

    def test_given_up_words_follow_whether_redeliver_would_send(self):
        """审查必须改 1：话照中台 worthSending 分四种——开着 / 已处理完且送到过 / 没看到的仅告知通知 / 重新送也不会再发。"""
        import datetime as dt
        now = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)
        base = {"card_id": 7, "title": "t", "tries": 9, "error": "x", "first_try_at": "2026-11-20T10:00:00.000Z"}
        cmd = "agent-autoloop redeliver 7"
        cases = (
            ({"state": "open", "ref": None, "fyi": 0, "created_at": "2026-11-20T09:00:00.000Z"}, ("不会再自动送到你这里", cmd), ()),
            ({"state": "open", "ref": "om_1", "fyi": 0, "created_at": "2026-11-20T09:00:00.000Z"}, ("不会再自动送到你这里", cmd), ()),
            ({"state": "decided", "ref": "om_1", "fyi": 0, "created_at": "2026-11-20T09:00:00.000Z"},
             ("已经处理完", "可以不管", cmd), ("不会补发",)),
            ({"state": "closed", "ref": "om_1", "fyi": 1, "created_at": "2026-11-01T09:00:00.000Z"}, ("已经处理完", cmd), ()),
            ({"state": "closed", "ref": None, "fyi": 1, "created_at": "2026-11-20T09:00:00.000Z"},     # 通知一建就是 closed
             ("只告知你的通知", "还没送到你这里", cmd), ("已经处理完", "可以不管", "消息没更新")),
            ({"state": "closed", "ref": None, "fyi": 1, "created_at": "2026-11-19T11:00:00.000Z"},     # 过了 24 小时
             ("重新送也不会再发",), (cmd, "可以不管", "消息没更新")),
            ({"state": "decided", "ref": None, "fyi": 0, "created_at": "2026-11-20T09:00:00.000Z"},    # 没送到就在别处答了
             ("没送到你这里就已经结束", "重新送也不会再发"), (cmd, "消息没更新")),
            ({"state": "closed", "ref": None, "fyi": 1, "created_at": None},                            # 建卡时间读不到：不承诺会发
             ("重新送也不会再发",), (cmd,)),
        )
        for extra, has, lacks in cases:
            with self.subTest(**extra):
                said = F.given_up_todo({**base, **extra}, now=now)
                for w in has:
                    self.assertIn(w, said)
                for w in lacks:
                    self.assertNotIn(w, said)

    def test_given_up_alert_carries_the_right_words(self):
        """提醒正文用的就是上面那句（没看到的通知不说「可以不管」）。"""
        import datetime as dt
        made = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
        cards = [{"card_id": 7, "title": "跑完了", "tries": 9, "error": "x", "first_try_at": "2026-11-20T10:00:00.000Z",
                  "state": "closed", "ref": None, "fyi": 1, "created_at": made}]
        with mock.patch.object(F, "given_up_cards", lambda: cards):
            self.assertEqual(F.check_given_up(), [7])
        self.assertIn("还没送到你这里", self.alerts[0])
        self.assertIn("agent-autoloop redeliver 7", self.alerts[0])
        self.assertNotIn("可以不管", self.alerts[0])

    def test_direct_deliver_never_patches_back(self):
        """专项续审 P2-03：绕过 once 直接送旧版本，改飞书之前就拒：消息和登记都不回退。"""
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 3})
        with self.assertRaisesRegex(F.Fail, "不把消息改回第 2 版"):
            F.deliver({**CARD, "ref": "om_fixture_1", "version": 2})
        with self.assertRaisesRegex(F.Fail, "不把消息改回第 2 版"):
            F.deliver({**CARD, "ref": None, "version": 2})                        # 发新卡的路也拒
        self.assertEqual(self.registered(), 3)
        self.assertEqual(len(self.patches()), 1)
        self.assertEqual(len(self.lark.sent()), 1)

    def test_direct_deliver_waits_for_lock(self):
        """锁外直接调 deliver 也要等桥接锁：别的进程持锁时不改飞书。"""
        held, release = threading.Event(), threading.Event()

        def holder():
            with F.bridge_lock():
                held.set()
                release.wait(5)
        th = threading.Thread(target=holder)
        th.start()
        self.assertTrue(held.wait(5))
        td = threading.Thread(target=lambda: F.deliver({**CARD, "ref": "om_fixture_1", "version": 2}))
        td.start()
        time.sleep(0.3)
        self.assertEqual(self.patches(), [])                                    # 还在等锁
        release.set()
        th.join(5)
        td.join(5)
        self.assertEqual((len(self.patches()), self.registered()), (1, 2))

    def test_recovery_report_waits_for_lock_and_reports_latest(self):
        """专项续审 P2-02：第 2 版正在改卡（持锁）时，对账补报送达要等锁；拿到锁时按当时登记的版本报，不报旧的第 1 版。"""
        entered, release = threading.Event(), threading.Event()
        real = self.lark

        def lark(args, what, cwd=None):
            if args[:3] == ["im", "messages", "patch"]:
                entered.set()
                release.wait(5)
            return real(args, what, cwd)
        self.hub_now = 2
        self.lease(2, "v2")
        out = {}
        with mock.patch.object(F, "lark", lark):
            t2 = threading.Thread(target=lambda: out.setdefault("v2", F.once()), name="v2")
            t2.start()
            self.assertTrue(entered.wait(5))
            tr = threading.Thread(target=lambda: out.setdefault("report", F.report_delivered(7)), name="report")
            tr.start()
            time.sleep(0.3)
            self.assertNotIn("report", out)                                     # 补报在等锁
            self.assertEqual(self.posts, [])
            release.set()
            t2.join(5)
            tr.join(5)
        self.assertTrue(out["v2"]["ok"])
        self.assertIn("report", out)
        self.assertEqual([b["version"] for _, b in self.posts], [2, 2])         # 两次都是第 2 版，没有第 1 版

    def test_concurrent_v2_v3_are_serialised(self):
        """第 3 版的桥接正在改卡（持锁），第 2 版的桥接领到卡后只能等；等到时登记已是第 3 版，跳过。"""
        self.hub_now = 3
        entered, release = threading.Event(), threading.Event()
        real = self.lark

        def lark(args, what, cwd=None):
            if args[:3] == ["im", "messages", "patch"] and threading.current_thread().name == "v3":
                entered.set()
                release.wait(5)
            return real(args, what, cwd)
        self.lease(3, "v3")
        self.lease(2, "v2")
        out = {}
        with mock.patch.object(F, "lark", lark):
            t3 = threading.Thread(target=lambda: out.setdefault("v3", F.once()), name="v3")
            t3.start()
            self.assertTrue(entered.wait(5))
            t2 = threading.Thread(target=lambda: out.setdefault("v2", F.once()), name="v2")
            t2.start()
            time.sleep(0.3)
            self.assertNotIn("v2", out)                                         # 第 2 版在等锁
            release.set()
            t3.join(5)
            t2.join(5)
        self.assertTrue(out["v3"]["ok"])
        self.assertIn("skipped", out["v2"])
        self.assertEqual(self.registered(), 3)
        self.assertEqual(len(self.patches()), 1)
        self.assertEqual({b["v"] for e in self.patches()[0]["body"]["elements"] if e["tag"] == "button"
                          for b in [e["behaviors"][0]["value"]]}, {3})


class HubCall(unittest.TestCase):
    def test_unreadable_config_or_key_means_not_sent(self):
        with mock.patch.object(C, "hub_config", side_effect=FileNotFoundError):
            with self.assertRaises(F.HubError) as e:
                F.hub("POST", "/v1/cards/1/answer", {})
        self.assertFalse(e.exception.sent)
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"DT_AUTOLOOP_CHANNEL_KEY_DIR": d}), \
                mock.patch.object(C, "hub_config", return_value=("http://127.0.0.1:9", "o")):
            with self.assertRaises(F.HubError) as e:
                F.hub("POST", "/v1/cards/1/answer", {})
        self.assertFalse(e.exception.sent)


class ChannelKey(unittest.TestCase):
    def test_refuses_wrong_answerers_or_live_mismatch(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"DT_AUTOLOOP_CHANNEL_KEY_DIR": d, "DT_OWNER_OPEN_ID": OWNER,
                                             "DT_AUTOLOOP_LOCK": d + "/config.lock"}), \
                mock.patch.object(C, "check_target"), mock.patch.object(C, "key_names", return_value=set()), \
                mock.patch.object(C, "run_cli", return_value=(0, "k" * 40, "")) as cli:
            rules = {"channels": {"feishu": {"answerers": [OWNER, "ou_other"]}, "secretary": {"answerers": ["秘书"]}}}
            with mock.patch.object(C, "load_current", return_value=rules), mock.patch.object(C, "mismatches", return_value=[]):
                with self.assertRaisesRegex(C.Rejected, "回答人"):
                    C.issue_channel_key("feishu", True, "x")
                self.assertEqual(C.issue_channel_key("secretary", True, "x")["channel"], "secretary")
            rules["channels"]["feishu"]["answerers"] = [OWNER]
            with mock.patch.object(C, "load_current", return_value=rules), mock.patch.object(C, "mismatches", return_value=["通道 feishu"]):
                with self.assertRaisesRegex(C.Rejected, "不一致"):
                    C.issue_channel_key("feishu", True, "x")
            self.assertEqual(cli.call_count, 1)
            self.assertFalse((Path(d) / "feishu-channel.key").exists())

    def test_half_issued_key_leaves_no_file_and_says_how_to_recover(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"DT_AUTOLOOP_CHANNEL_KEY_DIR": d, "DT_OWNER_OPEN_ID": OWNER,
                                             "DT_AUTOLOOP_LOCK": d + "/config.lock"}), \
                mock.patch.object(C, "check_target"), mock.patch.object(C, "key_names", return_value=set()), \
                mock.patch.object(C, "mismatches", return_value=[]), \
                mock.patch.object(C, "load_current", return_value={"channels": {"feishu": {"answerers": [OWNER]}}}):
            with mock.patch.object(C, "run_cli", return_value=(1, "", "坏了")):
                with self.assertRaisesRegex(C.Rejected, "签钥匙失败"):
                    C.issue_channel_key("feishu", True, "x")
            with mock.patch.object(C, "run_cli", return_value=(0, "k" * 40, "")), \
                    mock.patch.object(C.os, "write", side_effect=OSError("磁盘满")):
                with self.assertRaisesRegex(C.Rejected, "key remove channel feishu"):
                    C.issue_channel_key("feishu", True, "x")
            self.assertEqual(os.listdir(d), ["config.lock"])                    # 没留下半截钥匙文件

    def test_refuses_unknown_channel_and_missing_confirm(self):
        with self.assertRaisesRegex(C.Rejected, "只签"):
            C.issue_channel_key("desktop", True, "~/.agent-autoloop")
        with self.assertRaisesRegex(C.Rejected, "--confirm"):
            C.issue_channel_key("feishu", False, "~/.agent-autoloop")


@unittest.skipIf(SKIP, "本机没有 agent-autoloop 命令行")
class FeishuEndToEnd(HubFixture):
    COMMAND = f"{sys.executable} {HERE / 'fixtures' / 'ask_receipt.py'} {{prompt}}"

    def setUp(self):
        super().setUp()
        root = Path(self.key_file).parents[2]
        for p in (mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": str(root / "dt/autoloop/cards.db"),
                                               "DT_EXITS_DB": str(root / "dt/autoloop/exits.db"),
                                               "DT_HUB_PIN": str(root / "dt/autoloop/hub-pin.json"),
                                               "DT_AUTOLOOP_CHANNEL_KEY_DIR": str(root / "dt/autoloop"),
                                               "DT_AUTOLOOP_LOCK": str(root / "dt/autoloop/config.lock"),
                                               "DT_OWNER_CHAT_ID": CHAT}),
                  mock.patch.object(C.G, "ROSTER", self.roster)):
            p.start()
            self.addCleanup(p.stop)
        self.lark = Lark(mid="om_e2e_1")
        p = mock.patch.object(F, "lark", self.lark)
        p.start()
        self.addCleanup(p.stop)
        Rv.pin(Rv.healthz(), confirm=True)                       # 真的恢复闸：锁定临时中台的版本，入口自己对账开闸

    def wait_card(self):
        for _ in range(60):
            out = F.once(wait=1)
            if out:
                return out
        self.fail("中台没出卡片")

    def test_hub_readonly_queries_match_real_schema(self):
        """hub_readable、given_up_cards 的只读查询对得上真中台库的视图；放弃送达的卡能查出来（在中台库的副本里写两行，
        视图定义原样来自中台）。"""
        import autoloop_publish as P
        self.assertTrue(F.hub_readable())
        self.assertEqual(F.given_up_cards(), [])
        copy = Path(self.home) / "hub-copy.db"
        with contextlib.closing(sqlite3.connect(P.hub_db())) as src, contextlib.closing(sqlite3.connect(copy)) as dst:
            src.backup(dst)
        with contextlib.closing(sqlite3.connect(copy)) as con, con:
            con.create_function("card_hash", 2, lambda *a: "", deterministic=True)   # 中台进程里注册的索引函数，副本里补一个假的
            con.execute("INSERT INTO projections (collection, key, data) VALUES ('deliveries', '99:feishu', ?)",
                        (json.dumps({"card_id": 99, "channel": "feishu", "version": 0, "ref": None, "tries": 9,
                                     "first_try_at": "2026-11-20T10:00:00.000Z", "not_before": None, "error": "坏了",
                                     "given_up": 1}),))
            con.execute("INSERT INTO projections (collection, key, data) VALUES ('deliveries', '97:feishu', ?)",   # 审查建议 2
                        (json.dumps({"card_id": 97, "channel": "feishu", "version": 1, "ref": "om_97", "tries": 0,
                                     "first_try_at": None, "not_before": None, "error": None, "given_up": 1}),))
            con.execute("INSERT INTO projections (collection, key, data) VALUES ('deliveries', '98:secretary', ?)",
                        (json.dumps({"card_id": 98, "channel": "secretary", "tries": 9, "error": "x", "given_up": 1}),))
        with mock.patch("autoloop_publish.hub_db", lambda: copy):
            self.assertEqual(F.given_up_cards(), [{"card_id": 99, "title": None, "tries": 9, "error": "坏了",
                                                   "first_try_at": "2026-11-20T10:00:00.000Z", "ref": None, "state": None,
                                                   "fyi": None, "created_at": None}])
        with mock.patch("autoloop_publish.hub_db", lambda: Path(self.home) / "none.db"):
            self.assertFalse(F.hub_readable())
            self.assertIsNone(F.given_up_cards())

    def test_maintenance_during_long_poll_costs_one_try_and_the_card_comes_back(self):
        """L3 第 4 轮 P1-06 证据：长轮询领到卡的同时开始维护（停写）——锁里再核闸，不发不报；真中台记这次尝试
        （tries 1，1 分钟后再送，没放弃），维护结束、对完账后这张卡照常送到，飞书只发一次。"""
        import autoloop_publish as P
        C.install(confirm=True, target=self.home)
        rules = json.loads(C.rules_file().read_text(encoding="utf-8"))
        next(r for r in rules["rules"] if r["name"] == "试一下")["channels"] = ["feishu"]
        C.rules_file().write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        self._wait_live(lambda live: next(r for r in live["rules"] if r["name"] == "试一下").get("channels") == ["feishu"])
        C.issue_channel_key("feishu", True, str(self.home))
        import subprocess
        subprocess.run([CLI, "try"], env={**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home)}, capture_output=True, timeout=60)
        real_hub = F.hub

        def hub_then_hold(method, path, *a, **kw):
            out = real_hub(method, path, *a, **kw)
            if method == "GET" and out.get("card"):
                Rv.hold("p1", Path(self.home) / "snap", Rv.pinned(), 0)
            return out

        def delivery(cid):
            con = P._hub()
            try:
                return con.execute("SELECT tries, not_before, given_up FROM deliveries WHERE card_id = ? AND channel = 'feishu'",
                                   (cid,)).fetchone()
            finally:
                con.close()
        with mock.patch.object(F, "hub", hub_then_hold):
            for _ in range(60):
                out = F.once(wait=1)
                if out:
                    break
        self.assertEqual((out["ok"], out.get("deferred")), (False, True), out)
        cid = out["card"]
        self.assertEqual(self.lark.sent(), [])                                    # 没发飞书
        with F.db() as con:
            self.assertIsNone(con.execute("SELECT 1 FROM feishu_cards WHERE card_id = ?", (cid,)).fetchone())
        tries, not_before, given_up = delivery(cid)
        self.assertEqual((tries, given_up), (1, 0))                                # 用掉一次尝试，没放弃
        self.assertIsNotNone(not_before)                                           # 中台 1 分钟后再送
        self.assertEqual(Rv.in_flight_writes(), [])
        Rv.release("p1", "retired")
        deadline = time.time() + 120
        while time.time() < deadline:                                              # 维护结束：对账开闸后照常领到、送到
            got = F.once(wait=1)
            if got and got.get("ok"):
                break
        else:
            self.fail("维护结束后这张卡没再送来")
        self.assertEqual((got["card"], got["ref"]), (cid, "om_e2e_1"))
        self.assertEqual(len(self.lark.sent()), 1)

    def test_click_to_answer_round_trip(self):
        C.install(confirm=True, target=self.home)
        rules = json.loads(C.rules_file().read_text(encoding="utf-8"))
        next(r for r in rules["rules"] if r["name"] == "试一下")["channels"] = ["feishu"]
        C.rules_file().write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        self._wait_live(lambda live: next(r for r in live["rules"] if r["name"] == "试一下").get("channels") == ["feishu"])
        with self.assertRaisesRegex(F.Fail, "没有 feishu 通道钥匙"):
            F.once(wait=1)
        out = C.issue_channel_key("feishu", True, str(self.home))
        self.assertEqual(Path(out["file"]).stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(C.Rejected, "已经有了"):
            C.issue_channel_key("feishu", True, str(self.home))

        env = {**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home)}
        import subprocess
        subprocess.run([CLI, "try"], env=env, capture_output=True, timeout=60)
        got = self.wait_card()
        self.assertEqual((got["ok"], got["ref"]), (True, "om_e2e_1"))
        val = self.lark.sent()[-1]["body"]["elements"][1]["behaviors"][0]["value"]
        tok, ver = val["autoloop"], val["v"]

        self.assertEqual(A.answer(tok, 1, CHAT, "ou_intruder", ver)[0], 1)           # 别人点：不交中台
        with self.assertRaises(F.HubError) as stale:                              # #278：真中台按版本核对，对不上回 409、卡照旧开着
            F.hub("POST", f"/v1/cards/{got['card']}/answer", {"choice": 1, "by": OWNER, "ref": got["ref"], "version": ver + 1})
        self.assertEqual(stale.exception.status, 409)
        self.assertEqual(next(c for c in C.state()["cards"] if c["id"] == got["card"])["state"], "open")
        with F.db(write=True) as con:                                             # 模拟「点的那一版和中台对不上」：登记和按钮都比中台新一版
            con.execute("UPDATE feishu_cards SET version = ? WHERE card_id = ?", (ver + 1, got["card"]))
        code, said, rec = A.answer(tok, 1, CHAT, OWNER, ver + 1)
        self.assertEqual(code, 1, said)
        self.assertIn(f"中台里这张卡是第 {ver} 版，比你点的第 {ver + 1} 版还旧", said)
        self.assertIn("人工核查", said)
        self.assertEqual(next(c for c in C.state()["cards"] if c["id"] == got["card"])["state"], "open")
        with F.db(write=True) as con:
            self.assertIsNone(con.execute("SELECT 1 FROM claims WHERE card_id = ?", (got["card"],)).fetchone())   # 释放了
            con.execute("UPDATE feishu_cards SET version = ? WHERE card_id = ?", (ver, got["card"]))
        code, said, rec = A.answer(tok, 1, CHAT, OWNER, ver)
        self.assertEqual(code, 0, said)
        st = C.state()
        card = next(c for c in st.get("cards", []) + st.get("recentCards", []) if c["id"] == got["card"])
        self.assertEqual((card["choice"], card["by"]), (1, OWNER))
        self.assertEqual(A.answer(tok, 0, CHAT, OWNER, ver)[0], 1)                    # 再点：以第一次为准

        for _ in range(30):                                                     # 中台把状态变了的卡再给桥接：改原消息
            if F.once(wait=1) and any(a[:3] == ["im", "messages", "patch"] for a in self.lark.calls):
                break
            time.sleep(0.2)
        patch = [a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]]
        self.assertTrue(patch)
        self.assertIn("你选了「绿色」", patch[0][patch[0].index("--data") + 1])

    def test_click_before_delivery_confirmed_is_submitted_once_after(self):
        C.install(confirm=True, target=self.home)
        rules = json.loads(C.rules_file().read_text(encoding="utf-8"))
        next(r for r in rules["rules"] if r["name"] == "试一下")["channels"] = ["feishu"]
        C.rules_file().write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        self._wait_live(lambda live: next(r for r in live["rules"] if r["name"] == "试一下").get("channels") == ["feishu"])
        C.issue_channel_key("feishu", True, str(self.home))
        import subprocess
        subprocess.run([CLI, "try"], env={**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home)}, capture_output=True, timeout=60)
        card = None
        for _ in range(60):
            card = F.hub("GET", "/v1/cards/next?wait=1", timeout=30).get("card")
            if card:
                break
        self.assertTrue(card, "中台没出卡片")
        ref = F.deliver(card)                                                   # 飞书已发，送达报告还没交
        val = self.lark.sent()[-1]["body"]["elements"][1]["behaviors"][0]["value"]
        tok, ver = val["autoloop"], val["v"]
        with self.assertRaises(F.HubError) as early:                            # 审查 P1-02 的前提：没报送达时中台明确拒绝
            F.hub("POST", f"/v1/cards/{card['id']}/answer", {"choice": 0, "by": OWNER, "ref": ref})
        self.assertIn(early.exception.status, A.DEFINITE)
        with mock.patch.object(A, "tell") as tell:
            self.assertEqual(A.answer(tok, 0, CHAT, OWNER, ver)[0], 3)              # 只排队，不先发注定失败的回答
            st = C.state()
            self.assertEqual(next(c for c in st["cards"] if c["id"] == card["id"])["state"], "open")
            F.confirm(card["id"])                                              # 送达报告交上：自动提交排队的那一答
            tell.assert_called_once()
        st = C.state()
        got = next(c for c in st.get("cards", []) + st.get("recentCards", []) if c["id"] == card["id"])
        self.assertEqual((got["choice"], got["by"]), (0, OWNER))
        self.assertIsNone(A.flush(card["id"]))                                  # 只提交一次
        with F.db() as con:
            self.assertEqual(con.execute("SELECT state FROM claims WHERE who = 'owner'").fetchone()[0], "answered")


    def test_delivered_but_crashed_before_recording_recovers_once(self):
        C.install(confirm=True, target=self.home)
        rules = json.loads(C.rules_file().read_text(encoding="utf-8"))
        next(r for r in rules["rules"] if r["name"] == "试一下")["channels"] = ["feishu"]
        C.rules_file().write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        self._wait_live(lambda live: next(r for r in live["rules"] if r["name"] == "试一下").get("channels") == ["feishu"])
        C.issue_channel_key("feishu", True, str(self.home))
        import subprocess
        subprocess.run([CLI, "try"], env={**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home)}, capture_output=True, timeout=60)
        card = None
        for _ in range(60):
            card = F.hub("GET", "/v1/cards/next?wait=1", timeout=30).get("card")
            if card:
                break
        self.assertTrue(card, "中台没出卡片")
        ref = F.deliver(card)
        F.hub("POST", f"/v1/cards/{card['id']}/delivered", {"version": card["version"], "ref": ref})   # 中台收下了，桥接随即崩溃
        val = self.lark.sent()[-1]["body"]["elements"][1]["behaviors"][0]["value"]
        tok, ver = val["autoloop"], val["v"]
        with mock.patch.object(A, "tell"):
            self.assertEqual(A.answer(tok, 1, CHAT, OWNER, ver)[0], 3)              # 本地不知道已送达：排队
            self.assertIsNone(F.hub("GET", "/v1/cards/next?wait=1", timeout=30).get("card"))   # 中台不会再把它给桥接
            notes = A.reconcile()                                               # 重启后对账：按登记原样重报 → 提交
        self.assertEqual(notes, [(card["id"], "收到，已交给中台。")])
        st = C.state()
        got = next(c for c in st.get("cards", []) + st.get("recentCards", []) if c["id"] == card["id"])
        self.assertEqual((got["choice"], got["by"]), (1, OWNER))
        self.assertEqual(len(self.lark.sent()), 1)                              # 没有新的飞书卡片


if __name__ == "__main__":
    unittest.main()
