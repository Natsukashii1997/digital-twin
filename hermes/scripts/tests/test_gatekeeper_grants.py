"""门卫派单确认卡（ADR-015 批 0E-3b）自测：提议由网关登记 → 门卫发确认卡（走出口账本）→ 绑定的常浩点「批准派单」→
网关签凭证、发中台事件 → 卡片原地改成结果；「不派」、过期、别人点、点错消息、重放都不签。
飞书换成 test_gatekeeper 的记录器；中台事件换成记录器；凭证库、名册、出口账本都在临时目录（prod_guard 整个模块隔离）。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_cardview as CV  # noqa: E402
import autoloop_config as C  # noqa: E402
import autoloop_exits as E  # noqa: E402
import autoloop_feishu as F  # noqa: E402
import autoloop_grants as AG  # noqa: E402
import gatekeeper as G  # noqa: E402
import test_gatekeeper as TG  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401

SCOPE = "目标：给 dt-sandbox 的 README 加一节\n范围：只改 README.md\n验收：PR 里只有 README.md"


def hub(*pairs):
    """中台在用的配置（只含派单这一块）：pairs 里每个 (仓库, 执行方) 一条生成的派单规则，加两个工人 Agent 的定义。"""
    return {"rules": [C.dispatch_rule(r, x) for r, x in pairs],
            "agents": {C.AGENTS[x][0]: C.agent_spec(x) for x in C.AGENTS}}


class Base(TG.Base):
    def setUp(self):
        super().setUp()
        root = Path(self.tmp.name)
        roster = root / "projects.json"
        roster.write_text(json.dumps({"projects": [], "inventory": [
            {"path": "github:o/dt-sandbox", "category": "沙盒", "sandbox": True}]}), encoding="utf-8")
        self.hub_rules = hub(*[("dt-sandbox", x) for x in AG.EXECUTORS])
        self.posted, self.signed = [], 0
        env = {"DT_GRANTS_DB": str(root / "dt/autoloop/grants.db"), "DT_GRANTS_STATUS": str(root / "shared/grants/status.json"),
               "DT_GRANTS_TASKS": str(root / "shared/tasks")}
        for p in (mock.patch.dict(os.environ, env), mock.patch.object(AG, "ROSTER", roster),
                  mock.patch.object(AG, "post_event", side_effect=self.post),
                  mock.patch.object(C, "loaded_rules", side_effect=lambda: self.hub_rules)):
            p.start()
            self.addCleanup(p.stop)
        G._GRANT_TRIED.clear()
        self.addCleanup(G._GRANT_TRIED.clear)
        self.bind()
        G.LISTENING.set()
        self.addCleanup(G.LISTENING.clear)

    def post(self, body):
        self.posted.append(body)
        return 100 + len(self.posted)

    def propose(self, now=None):
        return AG.propose("dt-sandbox", "claude", SCOPE, now=now)["code"]

    def row(self, code):
        con = sqlite3.connect(os.environ["DT_GRANTS_DB"])
        con.row_factory = sqlite3.Row
        try:
            return dict(con.execute("SELECT * FROM proposals WHERE code = ?", (code,)).fetchone())
        finally:
            con.close()

    def sent_cards(self):
        """门卫发出的派单确认卡（按顺序）。"""
        return [c for c in self.lark.cards() if c["header"]["title"]["content"].startswith("派单确认")]

    def patches(self):
        return [json.loads(json.loads(a[a.index("--data") + 1])["content"]) for a in self.lark.calls
                if a[:3] == ["im", "messages", "patch"] and json.loads(json.loads(a[a.index("--data") + 1])["content"])
                ["header"]["title"]["content"].startswith("派单确认")]

    def sent_card(self):
        code = self.propose()
        G.grant_cards_once()
        r = self.row(code)
        return code, r["card_token"], r["card_message"]

    def click(self, token, mid, choice="ok", eid="ev_g1", **kw):
        return G.handle_event(TG.event({"gate_grant": token, "a": choice}, eid=eid, mid=mid, **kw))


class Sending(Base):
    def test_loop_sends_one_card_to_the_bound_chat(self):
        code, token, mid = self.sent_card()
        (card,) = self.sent_cards()
        send = [a for a in self.lark.calls if a[:2] == ["im", "+messages-send"]][-1]
        self.assertEqual(send[send.index("--chat-id") + 1], TG.CHAT)
        self.assertEqual(send[send.index("--idempotency-key") + 1], f"dt-grant-card-{code}")
        self.assertEqual(CV.button_values(card), [{"gate_grant": token, "a": "ok"}, {"gate_grant": token, "a": "no"}])
        self.assertEqual(card["header"]["template"], "indigo")
        self.assertIn("只改 README.md", json.dumps(card, ensure_ascii=False))
        self.assertEqual((self.row(code)["card_state"], mid), ("sent", f"om_gate_msg_{self.lark.n:04d}"))
        G._GRANT_TRIED.clear()
        G.grant_cards_once()                                      # 登记过消息号的不再发
        self.assertEqual(len(self.sent_cards()), 1)
        with E.db() as con:                                       # 走了出口账本
            (state,) = con.execute("SELECT state FROM exits WHERE key = ?", (f"send_feishu:grant-card-{code}",)).fetchone()
        self.assertEqual(state, "done")

    def test_nothing_is_sent_while_unbound_or_offline(self):
        self.propose()
        G.LISTENING.clear()
        self.assertEqual(G.grant_cards_once(), [])
        with G.db(write=True) as con:
            con.execute("DELETE FROM binding")
        G.LISTENING.set()
        self.assertEqual(G.grant_cards_once(), [])
        self.assertEqual(self.sent_cards(), [])

    def test_unsure_send_is_never_sent_twice(self):
        code = self.propose()
        self.lark.fail.add("发卡片")                               # 飞书报错：请求可能已到飞书，结果不明
        (rec,) = G.grant_cards_once()
        self.assertTrue(rec["unsure"], rec)
        self.assertEqual(self.row(code)["card_state"], "unsure")
        self.lark.fail.clear()
        n = len(self.lark.calls)
        G._GRANT_TRIED.clear()
        with mock.patch.object(E, "reconcile", lambda **kw: []):  # 对账读不到私聊：保持不明
            (rec,) = G.grant_cards_once()
        self.assertFalse(rec["ok"])
        self.assertEqual(len(self.lark.calls), n)                 # 没有第二次发
        self.assertIsNone(self.row(code)["card_message"])

    def test_reconcile_recognises_the_grant_card_by_its_token(self):
        msg = {"sender": {"sender_type": "app"}, "message_id": "om_x", "msg_type": "interactive",
               "content": json.dumps(G.grant_content({**AG.proposal_view(_fake_row()), "card_token": "a" * 16}))}
        self.assertTrue(E.is_ours(msg, {"marker": "a" * 16, "marker_key": "gate_grant", "msg_type": "interactive"}))
        self.assertFalse(E.is_ours(msg, {"marker": "a" * 16, "msg_type": "interactive"}))       # 中台卡片的认法不认它
        self.assertFalse(E.is_ours(msg, {"marker": "a" * 16, "marker_key": "a", "msg_type": "interactive"}))

    def test_expired_card_is_turned_grey_by_the_loop(self):
        old = AG.now_tz() - dt.timedelta(minutes=31)
        code = self.propose(now=old)
        AG.mark_card(code, "om_gate_old_0001")
        G.grant_cards_once()
        self.assertEqual(self.row(code)["decision"], "expired")
        (p,) = self.patches()
        self.assertEqual(p["header"]["template"], "grey")
        self.assertEqual(CV.button_values(p), [])
        self.assertIn("已作废", json.dumps(p, ensure_ascii=False))
        self.assertIsNotNone(self.row(code)["closed_at"])


    def later(self, minutes=31):
        real = AG.now_tz
        return mock.patch.object(AG, "now_tz", lambda: real() + dt.timedelta(minutes=minutes))

    def test_card_sent_but_not_registered_is_found_and_turned_grey_after_expiry(self):
        """#369 P2-01：飞书收到了卡，门卫在登记消息号前退出，过了 30 分钟再起来：按出口账本找回原消息号、改成灰卡，
        不重发、不签。"""
        code = self.propose()
        with mock.patch.object(AG, "mark_card"):                 # 发成了，登记消息号之前进程没了
            G.grant_cards_once()
        self.assertIsNone(self.row(code)["card_message"])
        G._GRANT_TRIED.clear()
        with self.later():
            recs = G.grant_cards_once()
        self.assertEqual([r.get("settled") for r in recs if "settled" in r], ["found"])
        self.assertEqual(len(self.sent_cards()), 1)              # 没有第二次发
        r = self.row(code)
        self.assertEqual((r["decision"], r["card_message"]), ("expired", f"om_gate_msg_{self.lark.n:04d}"))
        self.assertIsNotNone(r["closed_at"])
        (p,) = self.patches()
        self.assertEqual((p["header"]["template"], CV.button_values(p)), ("grey", []))
        self.assertEqual((AG.show(), self.posted), ([], []))

    def test_unsure_card_after_expiry_waits_and_unreconciled_is_told_once(self):
        code = self.propose()
        self.lark.fail.add("发卡片")
        G.grant_cards_once()
        self.lark.fail.clear()
        G._GRANT_TRIED.clear()
        with self.later(), mock.patch.object(E, "reconcile", lambda **kw: []):   # 对账读不到私聊：保持不明，不收尾
            (rec,) = G.grant_cards_once()
        self.assertEqual(rec["state"], "unknown")
        self.assertIsNone(self.row(code)["closed_at"])
        with E.db() as con:
            con.execute("UPDATE exits SET state = 'unreconciled' WHERE key = ?", (f"send_feishu:grant-card-{code}",))
        for _ in range(2):
            G._GRANT_TRIED.clear()
            with self.later():
                (rec,) = G.grant_cards_once()
            self.assertTrue(rec["unreconciled"])
        told = [t for t in self.lark.texts() if "核不清" in t and code in t]
        self.assertEqual(len(told), 1)                           # 只提醒一次
        self.assertIsNone(self.row(code)["closed_at"])           # 留着等常浩结算，不当成已收尾
        self.assertEqual((len(self.sent_cards()), AG.show(), self.posted), (1, [], []))   # 只有最初那一次（结果不明）

    def test_never_sent_card_is_just_closed_after_expiry(self):
        G.LISTENING.clear()
        code = self.propose()
        G.LISTENING.set()
        with self.later():
            (rec,) = G.grant_cards_once()
        self.assertEqual(rec["settled"], "never_sent")
        self.assertIsNotNone(self.row(code)["closed_at"])
        self.assertEqual((self.sent_cards(), self.patches()), ([], []))


class Clicking(Base):
    def test_owner_click_signs_sends_and_turns_card_green(self):
        code, token, mid = self.sent_card()
        rec = self.click(token, mid)
        self.assertTrue(rec["ok"], rec)
        self.assertRegex(rec["task_id"], AG.TASK_ID)
        (g,) = AG.show(rec["task_id"])
        self.assertEqual((g["status"], g["executor"], g["repo"]), ("active", "claude", "dt-sandbox"))
        self.assertEqual([b["id"] for b in self.posted], [f"{rec['task_id']}:g1:start"])
        (p,) = self.patches()
        self.assertEqual(p["header"]["template"], "green")
        self.assertEqual(CV.button_values(p), [])
        self.assertIn(rec["task_id"], json.dumps(p, ensure_ascii=False))
        self.assertIn("已送到中台", json.dumps(p, ensure_ascii=False))
        self.assertEqual(AG.cards_to_close(), [])
        with sqlite3.connect(os.environ["DT_GRANTS_DB"]) as con:
            (detail,) = con.execute("SELECT detail FROM grant_log WHERE what = '签发'").fetchone()
        self.assertEqual(json.loads(detail)["event"], "ev_g1")

    def test_no_button_voids_and_turns_card_grey(self):
        code, token, mid = self.sent_card()
        rec = self.click(token, mid, "no")
        self.assertEqual(rec["decision"], "rejected")
        self.assertEqual(AG.show(), [])
        (p,) = self.patches()
        self.assertEqual(p["header"]["template"], "grey")
        self.assertIn("不派了", json.dumps(p, ensure_ascii=False))

    def test_wrong_person_chat_message_or_value_never_signs(self):
        code, token, mid = self.sent_card()
        texts = len(self.lark.texts())
        cases = [dict(eid="w1", operator=TG.OTHER), dict(eid="w2", chat="oc_group_chat_0001"),
                 dict(eid="w3", mid_override="om_gate_msg_0001"), dict(eid="w4", mid_override="")]
        for kw in cases:
            m = kw.pop("mid_override", mid)
            rec = self.click(token, m, **kw)
            self.assertTrue(rec.get("quiet"), rec)
        for value in ({"gate_grant": token, "a": "yes"}, {"gate_grant": token[:-1], "a": "ok"},
                      {"gate_grant": token, "a": "ok", "x": 1}, {"gate_grant": "0" * 16, "a": "ok"}):
            rec = G.handle_event(TG.event(value, eid=f"v{len(value)}{value['a']}{value['gate_grant'][-2:]}", mid=mid))
            self.assertTrue(rec.get("quiet"), (value, rec))
        self.assertEqual(len(self.lark.texts()), texts)            # 来源不对不回话
        self.assertEqual((AG.show(), self.posted), ([], []))
        self.assertIsNone(self.row(code)["decision"])

    def test_click_before_message_id_is_registered_asks_to_retry(self):
        """卡已送到、门卫还没登记消息号时被点：不签、不当成别的卡，只请他几秒后再点；登记后再点照常签。"""
        code = self.propose()
        token = self.row(code)["card_token"]
        rec = self.click(token, "om_gate_msg_9999", eid="early")
        self.assertFalse(rec["ok"])
        self.assertIn("还在登记", self.lark.texts()[-1])
        self.assertIsNone(self.row(code)["decision"])
        G.grant_cards_once()
        rec = self.click(token, self.row(code)["card_message"], eid="later")
        self.assertTrue(rec["ok"], rec)

    def test_replay_is_handled_once_and_a_second_click_does_not_sign_again(self):
        code, token, mid = self.sent_card()
        first = self.click(token, mid, eid="same")
        self.assertIn("已经处理过", self.click(token, mid, eid="same")["why"])
        again = self.click(token, mid, eid="other")
        self.assertTrue(again["already"])
        self.assertIn("已经定过了", self.lark.texts()[-1])
        self.assertIn(first["task_id"], self.lark.texts()[-1])
        self.assertEqual(len(AG.show()), 1)
        self.assertEqual(len(self.posted), 1)

    def test_two_clicks_at_the_same_time_sign_once(self):
        """#369 P3-02：两次点击真并发（同一张卡、不同回调事件；批准对批准、批准对不派）：只签一张凭证、只记一个 start 事件、
        只发一次中台，另一次返回 already；批准和不派抢的时候，先提交的那个决定算数。"""
        for second in ("ok", "no"):
            with self.subTest(second=second):
                code, token, mid = self.sent_card()
                gate, out = threading.Barrier(2), {}

                def go(choice, eid):
                    gate.wait()
                    out[eid] = AG.decide(token, choice, mid, eid)
                ts = [threading.Thread(target=go, args=a) for a in (("ok", f"a-{second}"), (second, f"b-{second}"))]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
                first, other = sorted(out.values(), key=lambda r: bool(r.get("already")))
                self.assertTrue(other["already"])
                self.assertEqual(other["decision"], first["decision"])
                self.assertEqual(self.row(code)["decision"], first["decision"])
                con = sqlite3.connect(os.environ["DT_GRANTS_DB"])
                try:
                    grants = con.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
                    starts = con.execute("SELECT COUNT(*) FROM hub_events WHERE kind = 'start'").fetchone()[0]
                finally:
                    con.close()
                n = sum(1 for r in out.values() if r["decision"] == "approved" and not r.get("already"))
                self.assertEqual(n, 1 if first["decision"] == "approved" else 0)
                self.assertEqual((grants, starts, len(self.posted)), (self.signed + n,) * 3)
                self.signed += n

    def test_hub_rule_changed_after_propose_voids_without_signing(self):
        """P1-01：提议后中台的派单规则被改成交给别的 Agent，再点批准：记 void，不签凭证、不写任务单、不发事件。"""
        code, token, mid = self.sent_card()
        live = hub(*[("dt-sandbox", x) for x in AG.EXECUTORS])
        live["rules"][0] = live["rules"][0] | {"to": "dt-codex"}
        self.hub_rules = live
        rec = self.click(token, mid)
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["decision"], "void")
        self.assertIn("和应有的不一样（to）", self.row(code)["decided_note"])
        self.assertEqual((AG.show(), self.posted), ([], []))
        self.assertEqual(list(Path(os.environ["DT_GRANTS_TASKS"]).glob("*.md")) if Path(os.environ["DT_GRANTS_TASKS"]).exists()
                         else [], [])
        self.assertIn("已作废", json.dumps(self.patches()[-1], ensure_ascii=False))

    def test_patch_failure_is_said_in_text_and_retried_by_the_loop(self):
        code, token, mid = self.sent_card()
        self.lark.fail.add("改派单确认卡")
        rec = self.click(token, mid)
        self.assertTrue(rec["ok"])
        self.assertIn("卡片没改成", self.lark.texts()[-1])
        self.lark.fail.clear()
        G._GRANT_TRIED.clear()
        (closed,) = G.grant_cards_once()
        self.assertEqual(closed["closed"], "approved")
        self.assertEqual(AG.cards_to_close(), [])


def _fake_row():
    return {"code": "abcd1234", "repo": "dt-sandbox", "github": "o/dt-sandbox", "executor": "codex", "scope_text": SCOPE,
            "valid_until": "2026-11-24T10:00:00+08:00", "expires_at": "2026-11-23T22:30:00+08:00", "card_token": "b" * 16,
            "card_state": "pending", "card_message": None, "decision": None, "decided_at": None, "decided_note": None,
            "task_id": None, "actions": json.dumps(list(AG.ACTIONS))}


if __name__ == "__main__":
    unittest.main()
