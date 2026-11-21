"""隔离自测：秘书汇报卡与推翻（secretary_report.py，#195 批 0B-5）。

库（DT_STATE_DB）、原文目录（DT_SECRETARY_DIR）放临时目录；秘书状态（secretary.load）、凭证库的冻结 / 记更正 / 续接
（autoloop_grants.freeze / record_resolution / resume_dispatch）、中台库（autoloop_publish._hub）打桩；飞书发送和回读用
test_custody_report 的桩（回读只给渲染后的文字，按钮显示成「[按钮字]」）。不联网、不碰 ~/Library/Application Support/DigitalTwin。
账本记账的前提（准入、额度、名册）沿用 test_secretary_ledger 的 Base。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_secretary_report.py -v
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import fcntl
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import custody as C  # noqa: E402
import custody_ledger as L  # noqa: E402
import custody_report as CR  # noqa: E402
import secretary_ledger as SL  # noqa: E402
import secretary_report as SR  # noqa: E402
import test_custody_report as TCR  # noqa: E402  只借飞书桩的两个函数，不导入它的测试类
import test_secretary_ledger as TSL  # noqa: E402

T0, T1 = TSL.T0, TSL.T0 + dt.timedelta(hours=7)
SID, SID2, TASK, TASK2, REPO = TSL.SID, TSL.SID2, TSL.TASK, TSL.TASK2, TSL.REPO
CHAT, OWNER = "oc_fixture_home", "ou_fixture_owner"
MC = "c" * 40


class Base(TSL.Base):
    def setUp(self):
        super().setUp()
        os.environ.update(DT_OWNER_CHAT_ID=CHAT, DT_OWNER_OPEN_ID=OWNER, DT_PUBLISH_LOG=str(self.root / "publish.jsonl"),
                          DT_GRANTS_DB=str(self.root / "grants.db"))
        self.doc = {"state": "on", "session": {"id": SID, "started_at": C.iso(T0 - dt.timedelta(hours=4)), "ledger": {"id": self.lid},
                                               "frozen": None},
                    "history": [{"id": SID2, "started_at": C.iso(T0 - dt.timedelta(days=2)), "ledger": {"id": self.lid},
                                 "ended": {"at": C.iso(T0 - dt.timedelta(days=1, hours=20))}}]}
        for target, value in ((SR.S, "load"), ):
            p = mock.patch.object(target, value, side_effect=lambda *a, **k: self.doc)
            p.start()
            self.addCleanup(p.stop)
        self.sent, self.mget = [], {}
        p = mock.patch.object(CR, "lark", side_effect=self.fake_lark)
        p.start()
        self.addCleanup(p.stop)
        # 凭证库桩：冻结给第 1 代和冻结码；记更正记下参数
        self.freeze = mock.Mock(return_value={"task_id": TASK, "status": "frozen", "generation": 1, "freeze_code": "a1b2c3"})
        self.resolve = mock.Mock(return_value={"recorded": True})
        self.resume = mock.Mock(return_value={"event_key": f"{TASK}:g2:correction:1"})
        for name, m in (("freeze", self.freeze), ("record_resolution", self.resolve), ("resume_dispatch", self.resume)):
            p = mock.patch.object(SR.G, name, m)
            p.start()
            self.addCleanup(p.stop)
        # 中台库桩：runs 表（事项 lane = 任务号）
        self.hub_db = self.root / "hub.db"
        con = sqlite3.connect(self.hub_db)
        con.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, lane TEXT, rule TEXT, state TEXT, created_at TEXT)")
        con.commit()
        con.close()
        self.hub_ok = True
        p = mock.patch.object(SR.P, "_hub", side_effect=self.hub)
        p.start()
        self.addCleanup(p.stop)
        self.cancelled = []
        self.addCleanup(self.no_jargon)

    # ---- 桩 ----
    def hub(self):
        if not self.hub_ok:
            raise sqlite3.OperationalError("unable to open database file")
        con = sqlite3.connect(self.hub_db)
        con.row_factory = sqlite3.Row
        return con

    def add_run(self, state, lane=TASK, at=T0 + dt.timedelta(minutes=5)):
        con = sqlite3.connect(self.hub_db)
        rid = con.execute("INSERT INTO runs (lane, rule, state, created_at) VALUES (?,?,?,?)",
                          (lane, "dispatch-dt-sandbox-claude", state, at.isoformat())).lastrowid
        con.commit()
        con.close()
        return rid

    def set_run(self, rid, state):
        con = sqlite3.connect(self.hub_db)
        con.execute("UPDATE runs SET state=? WHERE id=?", (state, rid))
        con.commit()
        con.close()

    def cancel(self, rid):
        """中台取消接口的样子：queued 直接取消；claimed 只记下取消、轮次还在跑（等工人收手）。"""
        self.cancelled.append(rid)
        con = sqlite3.connect(self.hub_db)
        state = con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()[0]
        con.close()
        if state == "queued":
            self.set_run(rid, "cancelled")
        elif state != "claimed":
            raise SR.AC.Rejected("中台回 HTTP 400（POST /v1/runs/x/cancel）")

    def no_jargon(self):
        """写给常浩看的私聊文字和卡片文字不许有内部术语（托管账本的 JARGON 规则，验收第 7 条）。"""
        for x in self.sent:
            shown = x["content"]["text"] if x["type"] == "text" else "\n".join(
                TCR._walk_text(x["content"]) + [b["text"]["content"] for b in TCR._button_nodes(x["content"])] +
                [x["content"]["header"]["title"]["content"]])
            m = L.JARGON.search(shown)
            self.assertIsNone(m, f"写给常浩的话里有内部术语「{m and m.group(0)}」：{shown[:300]}")

    fake_send = TCR.Base.fake_send
    fake_lark = TCR.Base.fake_lark

    def texts(self, since=0):
        return [x["content"]["text"] for x in self.sent[since:] if x["type"] == "text"]

    def cards(self, since=0):
        return [x["content"] for x in self.sent[since:] if x["type"] == "interactive"]

    # ---- 账本数据 ----
    def decided(self, action, status="done", now=T0, **kw):
        d = self.issue(action, now=now, **kw)
        if action == "handoff" or status == "issued":
            return d
        SL.begin(self.st, d["id"], now=now, status_reader=self.reader)
        if status == "executing":
            return SL.get(self.st, d["id"])
        if status in ("unknown", "unreconciled"):
            SL.finish(self.st, d["id"], "unknown", now=now)
            if status == "unreconciled":
                SL.reconcile(self.st, d["id"], "unreconciled", {"checked": "回读：查不到"}, now=now)
            return SL.get(self.st, d["id"])
        return SL.finish(self.st, d["id"], status, outcome={"merge_commit": MC} if action == "merge" and status == "done" else None,
                         now=now)

    def handoff(self, **kw):
        kw.setdefault("category", "ask")
        kw.setdefault("reason", "decided")
        kw.setdefault("card_version", 1)
        return self.issue("handoff", **kw)

    # ---- 汇报与按钮 ----
    def day(self, **kw):
        return SR.send_report("day", "2026-11-18", now=T1, sender=self.fake_send, store=self.st, doc=self.doc, **kw)

    def token_for(self, ref):
        """推翻按钮上的令牌：哪份汇报最新一张核实过的卡列了它。"""
        for f in sorted(SR.reports_dir().glob("*.json")):
            ok = [x for x in json.loads(f.read_text(encoding="utf-8"))["sent"] if x["status"] == "verified"]
            if ok and ref in ok[-1]["actionable"]:
                return ok[-1]["card"]
        return None

    def click(self, verb, ref, card=None, option=None, operator=OWNER, chat=CHAT, now=T1, **kw):
        n = len(self.sent)
        kw.setdefault("cancel", self.cancel)
        code = SR.cmd_button(verb, ref, chat, operator, card=card, option=option, now=now, sender=self.fake_send, **kw)
        return code, self.texts(n)

    def overturn(self, d, **kw):
        if not self.token_for(d["id"]):
            self.day(force=True)
        return self.click("overturn", d["id"], card=self.token_for(d["id"]), **kw)

    def progress(self, d):
        return CR.read_overturn(d, SL.SECRETARY)


def buttons(card):
    return [b["behaviors"][0]["value"] for b in TCR._button_nodes(card)]


# ================= 汇报 =================
class CardTests(Base):
    def test_day_card_groups_rows_and_only_puts_overturn_on_decided(self):
        done = self.decided("redispatch", target={"task": TASK, "generation": 1})
        failed = self.decided("ask", "failed")
        unknown = self.decided("ask", "unknown")
        hand = self.handoff()
        live = self.decided("ask", "executing")
        r = self.day(card_only=True)
        self.assertEqual(r["name"], "day-2026-11-18")
        self.assertEqual(r["decisions"], 5)
        card = r["card"]
        self.assertEqual(self.sent, [])                                      # 只出卡片，不发
        text = "".join(TCR._walk_text(card))
        self.assertIn("等你处理 · 3 件", text)                               # 交常浩、结果不明、还在途
        self.assertIn("替你定了的 · 2 件", text)                             # 做成、没做成
        self.assertIn("重派 1 次", text)
        self.assertIn("交给你 1 件", text)
        refs = [v["ref"] for v in buttons(card)]
        self.assertEqual(sorted(refs), sorted([done["id"], failed["id"], unknown["id"]]))   # 交常浩、在途的不能推翻
        self.assertNotIn(hand["id"], refs)
        self.assertNotIn(live["id"], refs)
        self.assertTrue(all(v == {"secretary": "overturn", "ref": v["ref"]} for v in buttons(card)))   # 预览没有令牌
        self.assertEqual(card["header"]["template"], "blue")
        self.assertIn("没替你定", text)
        self.assertIn(SR.PLAIN_REASON["decided"], text)                    # 卡片上用白话原因

    def test_empty_day_says_nothing_happened(self):
        card = self.day(card_only=True)["card"]
        self.assertIn("没替你做任何决定", "".join(TCR._walk_text(card)))
        self.assertEqual(buttons(card), [])

    def test_other_days_are_not_in_this_day(self):
        self.decided("ask", now=T0 + dt.timedelta(days=1))
        self.assertEqual(self.day(card_only=True)["decisions"], 0)

    def test_frozen_secretary_turns_card_red(self):
        self.doc["state"] = "frozen"
        self.doc["session"]["frozen"] = {"at": C.iso(T0), "reason": "合并结果不明"}
        card = self.day(card_only=True)["card"]
        self.assertEqual(card["header"]["template"], "red")
        self.assertIn("秘书已经冻结", "".join(TCR._walk_text(card)))

    def test_day_window_is_last_21_to_21_without_custody(self):
        late = self.decided("ask", now=T0.replace(hour=22))                       # 11-18 22:00：进 11-19 那份
        early = self.decided("ask", now=T0 - dt.timedelta(hours=16, minutes=30))  # 11-17 21:30：进 11-18 那份
        night = self.decided("ask", now=T0 - dt.timedelta(hours=10))              # 11-18 04:00：在托管时段里
        cdoc = {"state": "off", "session": None, "history": [
            {"id": "c-20261118-010000-0123456789ab", "started_at": C.iso(T0 - dt.timedelta(hours=13)),
             "ended": {"at": C.iso(T0 - dt.timedelta(hours=7))}, "frozen": None}]}
        r = SR.send_report("day", "2026-11-18", store=self.st, doc=self.doc, card_only=True, cdoc=cdoc)
        self.assertEqual([v["ref"] for v in buttons(r["card"])], [early["id"]])
        self.assertIn("不含托管时段", "".join(TCR._walk_text(r["card"])))
        r = SR.send_report("day", "2026-11-19", store=self.st, doc=self.doc, card_only=True, cdoc=cdoc)
        self.assertEqual([v["ref"] for v in buttons(r["card"])], [late["id"]])
        self.assertNotIn(night["id"], json.dumps(r["card"]))

    def test_day_not_sent_before_21(self):
        with self.assertRaises(SR.ReportError) as cm:
            SR.send_report("day", "2026-11-18", now=T1 - dt.timedelta(minutes=1), sender=self.fake_send, store=self.st, doc=self.doc)
        self.assertEqual(cm.exception.code, "NotYet")
        self.assertEqual(self.sent, [])

    def test_custody_unreadable_blocks_day(self):
        with mock.patch.object(SR.C, "load", side_effect=ValueError("custody.json 坏了")):
            with self.assertRaises(SR.ReportError) as cm:
                self.day(card_only=True)
        self.assertEqual(cm.exception.code, "Corrupt")

    def test_bad_day_rejected(self):
        with self.assertRaises(SR.ReportError) as cm:
            SR.send_report("day", "11-18", store=self.st, doc=self.doc, card_only=True)
        self.assertEqual(cm.exception.code, "BadInput")


class SendTests(Base):
    def test_send_reads_back_and_does_not_send_twice(self):
        d = self.decided("ask")
        e = self.day()
        self.assertEqual(e["status"], "verified")
        self.assertEqual(e["actionable"], [d["id"]])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["chat"], CHAT)
        self.assertEqual(buttons(self.sent[0]["content"]), [{"secretary": "overturn", "ref": d["id"], "card": e["card"]}])
        self.assertEqual(self.day(), {"unchanged": True, "name": "day-2026-11-18"})
        self.assertEqual(len(self.sent), 1)
        e2 = self.day(force=True)
        self.assertNotEqual(e2["card"], e["card"])
        self.assertEqual(len(self.sent), 2)

    def test_unverified_send_rechecked_by_message_id_before_resending(self):
        self.decided("ask")
        with mock.patch.object(CR, "lark", side_effect=CR.SendError("回读超时")):
            with self.assertRaises(SR.ReportError) as cm:
                self.day()
        self.assertEqual(cm.exception.code, "SendFailed")
        rec = SR.read_report("day-2026-11-18")
        self.assertEqual(rec["sent"][0]["status"], "unverified")
        self.assertTrue(rec["sent"][0]["message_id"])
        e = self.day()                                                       # 回读这次通了：核实原消息，不重发
        self.assertTrue(e["rechecked"])
        self.assertEqual(len(self.sent), 1)

    def test_send_failure_recorded_and_raised(self):
        self.decided("ask")

        def boom(*a):
            raise SR.SendError("飞书 500")
        with self.assertRaises(SR.ReportError) as cm:
            SR.send_report("day", "2026-11-18", now=T1, sender=boom, store=self.st, doc=self.doc)
        self.assertIn("没发出去", str(cm.exception))
        self.assertEqual(SR.read_report("day-2026-11-18")["sent"][0]["error"], "飞书 500")

    def test_no_owner_chat_no_send(self):
        os.environ.pop("DT_OWNER_CHAT_ID")
        with mock.patch.object(SR.T, "owner_chat_id", side_effect=FileNotFoundError("x")):
            with self.assertRaises(SR.ReportError) as cm:
                self.day()
        self.assertEqual(cm.exception.code, "NoChat")
        self.assertEqual(self.sent, [])

    def test_ledger_identity_mismatch_blocks_report(self):
        self.decided("ask")
        self.doc["session"]["ledger"]["id"] = "L-0000000000000000"
        with self.assertRaises(SR.ReportError) as cm:
            self.day()
        self.assertEqual(cm.exception.code, "LedgerMismatch")
        self.assertEqual(self.sent, [])

    def test_empty_report_still_checks_ledger(self):
        """一个决定都没有也核账本身份：库被换成新建的，不能当成「秘书没做决定」发出去。"""
        self.doc["session"]["ledger"]["id"] = "L-0000000000000000"
        with self.assertRaises(SR.ReportError) as cm:
            self.day()
        self.assertEqual(cm.exception.code, "LedgerMismatch")
        self.assertEqual(self.sent, [])

    def test_readback_failing_stops_resending(self):
        self.decided("ask")
        with mock.patch.object(CR, "lark", side_effect=CR.SendError("回读超时")):
            for _ in range(SR.RESEND_MAX):
                with self.assertRaises(SR.ReportError):
                    self.day()
            with self.assertRaises(SR.ReportError) as cm:
                self.day()
        self.assertEqual(cm.exception.code, "ReadbackFailing")
        self.assertEqual(len(self.sent), SR.RESEND_MAX)
        with mock.patch.object(CR, "lark", side_effect=CR.SendError("回读超时")):
            with self.assertRaises(SR.ReportError) as cm:
                self.day(force=True)                                             # --force 才再发
        self.assertEqual(len(self.sent), SR.RESEND_MAX + 1)

    def test_one_delivery_at_a_time(self):
        self.decided("ask")
        SR.reports_dir().mkdir(parents=True, exist_ok=True)
        with open(SR.reports_dir() / ".deliver.lock", "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            with self.assertRaises(SR.ReportError) as cm:
                self.day()
        self.assertEqual(cm.exception.code, "Busy")
        self.assertEqual(self.sent, [])

    def test_raw_without_ledger_row_blocks_report(self):
        d = self.decided("ask")
        orphan = Path(d["raw_path"]).with_name("d-261118-ffffff.json")
        orphan.write_text("{}", encoding="utf-8")
        with self.assertRaises(SR.ReportError) as cm:
            self.day()
        self.assertEqual(cm.exception.code, "LedgerMismatch")


class CustodyWindowTests(Base):
    def custody_doc(self, ended=True):
        s = {"id": "c-20261118-130000-0123456789ab", "started_at": C.iso(T0 - dt.timedelta(minutes=30)),
             "ended": {"at": C.iso(T0 + dt.timedelta(minutes=30))} if ended else None, "frozen": None}
        return {"state": "off", "session": None, "history": [s]} if ended else {"state": "on", "session": s, "history": []}

    def test_custody_report_takes_decisions_inside_window(self):
        inside = self.decided("ask")
        self.decided("ask", now=T0 + dt.timedelta(hours=2))
        with mock.patch.object(CR.C, "load", return_value=self.custody_doc()):
            r = SR.send_report("custody", "c-20261118-130000-0123456789ab", store=self.st, doc=self.doc, card_only=True)
        self.assertEqual(r["name"], "custody-c-20261118-130000-0123456789ab")
        self.assertEqual([v["ref"] for v in buttons(r["card"])], [inside["id"]])
        self.assertIn("托管时段", "".join(TCR._walk_text(r["card"])))

    def test_custody_still_open_rejected(self):
        with mock.patch.object(CR.C, "load", return_value=self.custody_doc(ended=False)):
            with self.assertRaises(SR.ReportError) as cm:
                SR.send_report("custody", "c-20261118-130000-0123456789ab", store=self.st, doc=self.doc, card_only=True)
        self.assertEqual(cm.exception.code, "NotEnded")

    def test_bad_custody_id_rejected(self):
        with self.assertRaises(SR.ReportError) as cm:
            SR.send_report("custody", "s-20261118-100000-0123456789ab", store=self.st, doc=self.doc, card_only=True)
        self.assertEqual(cm.exception.code, "BadInput")


class AlertTests(Base):
    def alert(self, **kw):
        return SR.send_alert(now=T1, sender=self.fake_send, store=self.st, doc=self.doc, **kw)

    def test_alert_reports_each_item_once(self):
        self.assertIsNone(self.alert())                                      # 没事不报
        a = self.decided("ask", "unknown")
        e = self.alert()
        self.assertEqual(e["items"], [f"{a['id']}:unknown"])
        self.assertIn("不确定做成没有", "".join(TCR._walk_text(self.sent[-1]["content"])))
        self.assertIsNone(self.alert())                                      # 报过不再报
        SL.reconcile(self.st, a["id"], "unreconciled", {"checked": "回读：查不到"}, now=T1)
        e = self.alert()                                                     # 变成对账不清是新的一项
        self.assertEqual(e["items"], [f"{a['id']}:unreconciled"])
        self.assertEqual(len(self.sent), 2)

    def test_alert_on_frozen(self):
        self.doc["state"] = "frozen"
        self.doc["session"]["frozen"] = {"at": C.iso(T0), "reason": "额度对不上"}
        r = self.alert(card_only=True)
        self.assertEqual(r["items"], [f"frozen:{SID}:{C.iso(T0)}"])
        self.assertIn("秘书已经冻结", "".join(TCR._walk_text(r["card"])))
        self.assertEqual(self.sent, [])

    def test_old_session_debt_does_not_block_new_alert(self):
        self.session = SID2                                                  # 记账时开着的是老会话
        old = self.decided("ask", "unknown", session=SID2, now=T0 - dt.timedelta(days=1, hours=21))
        self.session = SID
        self.assertEqual(self.alert()["items"], [f"{old['id']}:unknown"])
        self.doc["history"] = []                                             # 老会话从 history 里滚出去了
        new = self.decided("ask", "unknown")
        e = self.alert()
        self.assertEqual(e["items"], [f"{new['id']}:unknown"])

    def test_unverified_alert_is_not_counted_as_told(self):
        self.decided("ask", "unknown")
        with mock.patch.object(CR, "lark", side_effect=CR.SendError("回读超时")):
            with self.assertRaises(SR.ReportError):
                self.alert()
        self.assertIsNotNone(self.alert())                                   # 没核实的不算报过


# ================= 按钮：身份与令牌 =================
class ButtonGateTests(Base):
    def test_only_owner_in_home_chat(self):
        d = self.decided("ask", "failed")
        tok = self.day()["card"]
        for kw in ({"operator": "ou_someone_else"}, {"operator": ""}, {"chat": "oc_group"}):
            code, texts = self.click("overturn", d["id"], card=tok, **kw)
            self.assertEqual(code, 1)
            self.assertEqual(texts, [])                                      # 别人点的不回话
        self.assertEqual(SL.get(self.st, d["id"])["disposition"], "open")

    def test_unknown_verb_or_bad_ref(self):
        self.assertEqual(self.click("delete", "d-261118-abcdef")[0], 1)
        code, texts = self.click("overturn", "../x")
        self.assertEqual(code, 1)
        self.assertIn("看不懂", texts[0])

    def test_button_must_be_on_latest_verified_card(self):
        d = self.decided("ask", "failed")
        old = self.day()["card"]
        new = self.day(force=True)["card"]
        code, texts = self.click("overturn", d["id"], card=old)
        self.assertEqual(code, 1)
        self.assertIn("最新一张", texts[0])
        self.assertEqual(self.click("overturn", d["id"], card="0" * 16)[0], 1)
        self.assertEqual(self.click("overturn", d["id"], card=None)[0], 1)
        self.assertEqual(SL.get(self.st, d["id"])["disposition"], "open")
        self.assertEqual(self.click("overturn", d["id"], card=new)[0], 0)

    def test_handoff_cannot_be_overturned(self):
        h = self.handoff()
        tok = self.day()["card"]
        code, texts = self.click("overturn", h["id"], card=tok)
        self.assertEqual(code, 1)
        self.assertEqual(SL.get(self.st, h["id"])["disposition"], "open")

    def test_concurrent_click_refused(self):
        d = self.decided("ask", "failed")
        tok = self.day()["card"]
        lock = SL.raw_dir() / SID / f".{d['id']}.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        with open(lock, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            code, texts = self.click("overturn", d["id"], card=tok)
        self.assertEqual(code, 1)
        self.assertIn("还在处理", texts[0])
        self.assertEqual(SL.get(self.st, d["id"])["disposition"], "open")

    def test_ledger_mismatch_refused_at_click(self):
        d = self.decided("ask", "failed")
        tok = self.day()["card"]
        self.doc["session"]["ledger"]["id"] = "L-0000000000000000"
        code, texts = self.click("overturn", d["id"], card=tok)
        self.assertEqual(code, 1)
        self.assertIn("账本对不上", texts[0])
        self.assertEqual(SL.get(self.st, d["id"])["disposition"], "open")

    def test_resume_needs_existing_progress(self):
        d = self.decided("ask", "failed")
        code = SR.cmd_button("overturn", d["id"], CHAT, resume=True, now=T1, sender=self.fake_send)
        self.assertEqual(code, 1)
        self.assertIn("没有进行到一半", self.texts()[0])


# ================= 推翻 =================
class OverturnTests(Base):
    def test_failed_decision_only_registers_and_writes_candidate(self):
        d = self.decided("ask", "failed")
        code, texts = self.overturn(d)
        self.assertEqual(code, 0)
        self.assertEqual(SL.get(self.st, d["id"])["disposition"], "overturned")
        self.freeze.assert_not_called()                                      # 没做成的什么都没发生：不冻结、不停轮次
        self.resolve.assert_not_called()
        rec = self.progress(d)
        self.assertEqual(sorted(rec["stages"]), ["candidate", "impacts", "registered", "result"])
        cand = [json.loads(x) for x in (SL.raw_dir() / "rule-candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([(c["decision"], c["by"]) for c in cand], [(d["id"], "常浩（秘书汇报卡「推翻」）")])
        self.assertIn("已推翻", texts[-1])
        code, texts = self.overturn(d)                                       # 再点：已推翻过
        self.assertEqual(code, 0)
        self.assertIn("推翻过了", texts[-1])
        self.assertEqual(len((SL.raw_dir() / "rule-candidates.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_already_handled_elsewhere_not_overturned_again(self):
        d = self.decided("ask", "failed")
        tok = self.day()["card"]
        L.set_disposition(self.st, d["id"], "overturned", "常浩在电脑上处理过", by="user", now=T1, use=SL.SECRETARY)
        code, texts = self.click("overturn", d["id"], card=tok)
        self.assertEqual(code, 0)
        self.assertIn("已经处理过", texts[-1])
        self.assertIsNone(self.progress(d))
        self.assertFalse((SL.raw_dir() / "rule-candidates.jsonl").exists())

    def test_done_redispatch_freezes_stops_lists_and_records_resolution(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        q, c = self.add_run("queued"), self.add_run("claimed")
        self.add_run("done", at=T0 - dt.timedelta(hours=1))                  # 决定之前的：不算下游
        self.add_run("queued", lane=TASK2)                                    # 别的任务：不碰
        code, texts = self.overturn(d)
        self.assertEqual(code, 1)                                            # claimed 的还没停下
        self.assertEqual(self.freeze.call_args[0][0], TASK)
        self.assertIn(d["id"], self.freeze.call_args[0][1])
        self.assertEqual(sorted(self.cancelled), [q, c])
        rec = self.progress(d)
        self.assertEqual(rec["stop"]["status"], "stopping")
        self.assertNotIn("stop", rec["stages"])
        self.assertIn("还有轮次没确认停下", texts[-1])
        self.assertIn("任务 t-20261118-abcdef 已冻结", texts[-1])
        # 规则 1：决定之后的轮次都列出来
        self.assertEqual(sorted(i["ref"] for i in rec["impacts"]["items"] if i["rule"] == 1), sorted([str(q), str(c)]))
        self.set_run(c, "cancelled")                                         # 工人收手了，再点一次
        code, texts = self.overturn(d)
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.cancelled), [q, c])                     # 问过的不再问
        self.assertEqual(self.freeze.call_count, 1)
        task, gen, did, text = self.resolve.call_args[0]
        self.assertEqual((task, gen, did), (TASK, 1, d["id"]))
        self.assertIn("下游", text)
        rec = self.progress(d)
        self.assertTrue(SR.finished(SL.get(self.st, d["id"]), rec))
        self.assertIn("恢复 t-20261118-abcdef a1b2c3", texts[-1])
        self.assertIn("2 个轮次已停下", texts[-1])

    def test_stop_never_touches_runs_after_resume(self):
        """第一次停时记下的那一批才管：常浩恢复任务后再点「推翻」，恢复后的新轮次不能被停掉。"""
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        c = self.add_run("claimed")
        self.assertEqual(self.overturn(d)[0], 1)
        new = self.add_run("queued", at=T1 + dt.timedelta(hours=1))         # 恢复以后排进来的
        self.set_run(c, "cancelled")
        self.assertEqual(self.overturn(d)[0], 0)
        self.assertEqual(self.cancelled, [c])
        self.assertNotIn(new, self.progress(d)["stop"]["targets"])

    def test_stop_targets_fixed_late_exclude_runs_after_freeze(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        self.hub_ok = False
        self.overturn(d)                                                     # 冻结了，但调度记录读不了：这次没记下要停哪些
        self.hub_ok = True
        old = self.add_run("queued")
        new = self.add_run("queued", at=T1 + dt.timedelta(hours=1))
        self.assertEqual(self.overturn(d, now=T1 + dt.timedelta(hours=2))[0], 0)
        self.assertEqual(self.cancelled, [old])
        self.assertNotIn(new, self.cancelled)

    def test_cancel_error_is_retried(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        q = self.add_run("queued")
        calls = []

        def flaky(rid):
            calls.append(rid)
            if len(calls) == 1:
                raise SR.AC.Rejected("连不上中台：OSError")
            self.cancel(rid)
        self.assertEqual(self.overturn(d, cancel=flaky)[0], 1)
        self.assertEqual(self.progress(d)["stop"]["asked"], [])
        self.assertEqual(self.overturn(d, cancel=flaky)[0], 0)
        self.assertEqual(calls, [q, q])

    def test_freeze_code_only_after_resolution(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        self.resolve.side_effect = SR.G.Rejected("凭证库被锁")
        code, texts = self.overturn(d)
        self.assertEqual(code, 1)
        self.assertNotIn("a1b2c3", texts[-1])
        self.assertIn("更正没记进去", texts[-1])
        self.resolve.side_effect = None
        code, texts = self.overturn(d)
        self.assertEqual(code, 0)
        self.assertIn("恢复 t-20261118-abcdef a1b2c3", texts[-1])

    def test_unknown_run_noted_but_does_not_block(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        u = self.add_run("unknown")
        code, texts = self.overturn(d)
        self.assertEqual(code, 0)
        self.assertEqual(self.cancelled, [])                                 # 中台不收「不确定」轮次的取消
        self.assertEqual(self.progress(d)["stop"]["doubt"], [u])
        self.assertIn("调度那边也不确定", texts[-1])

    def test_hub_unreadable_means_not_stopped(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        self.hub_ok = False
        code, texts = self.overturn(d)
        self.assertEqual(code, 1)
        rec = self.progress(d)
        self.assertEqual(rec["stop"]["status"], "unknown")
        self.assertIn("freeze", rec["stages"])
        self.assertIn("resolution", rec["stages"])                           # 冻结了就能记更正；停没停下另算
        self.assertTrue(any(g["rule"] == 1 for g in rec["impacts"]["gaps"]))
        self.hub_ok = True
        code, _ = self.overturn(d)
        self.assertEqual(code, 0)

    def test_terminal_grant_needs_no_freeze_or_resolution(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        self.freeze.side_effect = SR.G.Rejected(f"{TASK} 已经是终态（revoked），不能再改")
        code, texts = self.overturn(d)
        self.assertEqual(code, 0)
        rec = self.progress(d)
        self.assertEqual((rec["freeze"]["status"], rec["resolution"]["status"]), ("not_needed", "not_needed"))
        self.resolve.assert_not_called()
        self.assertNotIn("恢复", texts[-1])

    def test_freeze_failure_retried_on_next_click(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        self.freeze.side_effect = sqlite3.OperationalError("database is locked")
        code, texts = self.overturn(d)
        self.assertEqual(code, 1)
        self.assertIn("冻结没成功", texts[-1])
        self.resolve.assert_not_called()                                     # 没冻结就不记更正
        self.freeze.side_effect = None
        code, _ = self.overturn(d)
        self.assertEqual(code, 0)
        self.assertEqual(self.freeze.call_count, 2)
        self.resolve.assert_called_once()

    def test_resolution_already_recorded_counts_as_done(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        self.resolve.side_effect = SR.G.Rejected("这次冻结已经记过更正")
        self.assertEqual(self.overturn(d)[0], 0)
        self.assertEqual(self.progress(d)["resolution"]["status"], "recorded_before")

    def test_unknown_decision_freezes_too(self):
        d = self.decided("redispatch", "unknown", target={"task": TASK, "generation": 1})
        code, _ = self.overturn(d)
        self.assertEqual(code, 0)
        self.freeze.assert_called_once()
        self.resolve.assert_called_once()

    def test_resume_continues_without_card(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        c = self.add_run("claimed")
        self.assertEqual(self.overturn(d)[0], 1)
        self.set_run(c, "cancelled")
        code = SR.cmd_button("overturn", d["id"], CHAT, resume=True, now=T1, sender=self.fake_send, cancel=self.cancel)
        self.assertEqual(code, 0)
        self.assertIn("stop", self.progress(d)["stages"])

    def test_downstream_lists_publishes_and_citing_decisions(self):
        d = self.decided("redispatch", target={"task": TASK, "generation": 1})
        log = [{"at": C.iso(T0 + dt.timedelta(minutes=9)), "task_id": TASK, "what": "发布", "pr": f"https://github.com/{REPO}/pull/9",
                "head": "f" * 40},
               {"at": C.iso(T0 - dt.timedelta(minutes=9)), "task_id": TASK, "what": "发布", "pr": f"https://github.com/{REPO}/pull/8"},
               {"at": C.iso(T0 + dt.timedelta(minutes=9)), "task_id": TASK2, "what": "发布", "pr": f"https://github.com/{REPO}/pull/7"}]
        Path(os.environ["DT_PUBLISH_LOG"]).write_text("\n".join(json.dumps(x) for x in log) + "\nnot json\n", encoding="utf-8")
        later = self.decided("ask", now=T0 + dt.timedelta(minutes=20), cites=[d["id"]])
        self.overturn(d)
        imp = self.progress(d)["impacts"]
        self.assertEqual([(i["rule"], i["ref"]) for i in imp["items"] if i["rule"] in (2, 4)],
                         [(2, f"https://github.com/{REPO}/pull/9"), (4, later["id"])])
        self.assertTrue(any(g["rule"] == 2 and "坏行" in g["why"] for g in imp["gaps"]))


class AskOverturnTests(Base):
    def setUp(self):
        super().setUp()
        self.d = self.decided("ask")
        self.code, self.texts_ = self.overturn(self.d)

    def reselect_token(self):
        return self.progress(self.d)["reselect_card"]["card"]

    def test_overturn_sends_reselect_card_and_waits(self):
        self.assertEqual(self.code, 0)
        rc = self.progress(self.d)["reselect_card"]
        self.assertEqual(rc["status"], "verified")
        card = self.cards()[-1]
        self.assertEqual([b for b in buttons(card)],
                         [{"secretary": "reselect", "ref": self.d["id"], "card": rc["card"], "o": i} for i in (1, 2)])
        self.assertIn("改选：30 秒", json.dumps(card, ensure_ascii=False))
        self.resolve.assert_not_called()                                     # 常浩选之前不记更正，任务恢复不了
        self.assertNotIn("恢复 t-", self.texts_[-1])                          # 冻结码等改选记过更正才给
        self.assertIn("同样选项的卡", self.texts_[-1])

    def test_reselect_records_resolution_and_marks_corrected(self):
        code, texts = self.click("reselect", self.d["id"], card=self.reselect_token(), option=2)
        self.assertEqual(code, 0)
        task, gen, did, text = self.resolve.call_args[0]
        self.assertEqual((task, gen, did), (TASK, 1, self.d["id"]))
        self.assertIn("改选第 2 项「60 秒」", text)
        self.assertEqual(SL.get(self.st, self.d["id"])["disposition"], "corrected")
        self.assertIn("恢复 t-20261118-abcdef a1b2c3", texts[-1])
        code, texts = self.click("reselect", self.d["id"], card=self.reselect_token(), option=1)
        self.assertEqual(code, 0)
        self.assertIn("已经改选过了", texts[-1])
        self.resolve.assert_called_once()

    def test_reselect_rejects_wrong_card_or_option(self):
        for card, option in (("0" * 16, 1), (None, 1), (self.reselect_token(), 3), (self.reselect_token(), 0),
                             (self.reselect_token(), None)):
            code, texts = self.click("reselect", self.d["id"], card=card, option=option)
            self.assertEqual(code, 1, (card, option))
            self.assertIn("没记下", texts[-1])
        self.resolve.assert_not_called()
        self.assertEqual(SL.get(self.st, self.d["id"])["disposition"], "overturned")

    def test_reselect_resolution_failure_can_retry(self):
        self.resolve.side_effect = SR.G.Rejected("凭证库被锁")
        code, texts = self.click("reselect", self.d["id"], card=self.reselect_token(), option=1)
        self.assertEqual(code, 1)
        self.assertIn("再点一次会重试", texts[-1])
        self.assertEqual(SL.get(self.st, self.d["id"])["disposition"], "overturned")
        self.resolve.side_effect = None
        self.assertEqual(self.click("reselect", self.d["id"], card=self.reselect_token(), option=1)[0], 0)

    def test_correction_event_stays_off(self):
        self.click("reselect", self.d["id"], card=self.reselect_token(), option=2)
        with mock.patch.object(SR, "grant_ro", return_value={"status": "active", "generation": 2}):
            self.overturn(self.d)                                            # 恢复以后再点：开关关着只记一笔
            self.assertEqual(self.progress(self.d)["correction"]["status"], "switch_off")
            self.resume.assert_not_called()
            with mock.patch.object(SR, "TEST_CORRECTION", True):
                self.overturn(self.d)
        self.resume.assert_called_once()
        self.assertEqual(self.resume.call_args[0][:2], (TASK, "correction"))
        self.assertEqual(self.progress(self.d)["correction"]["status"], "sent")

    def test_correction_not_sent_before_unfreeze(self):
        self.click("reselect", self.d["id"], card=self.reselect_token(), option=2)
        with mock.patch.object(SR, "grant_ro", return_value={"status": "frozen", "generation": 1}), \
                mock.patch.object(SR, "TEST_CORRECTION", True):
            self.overturn(self.d)
        self.resume.assert_not_called()
        self.assertNotIn("correction", self.progress(self.d))

    def test_terminal_grant_sends_no_reselect_card(self):
        d = self.decided("ask", target={"card": 9, "task": TASK2, "generation": 1})
        self.freeze.side_effect = SR.G.Rejected(f"{TASK2} 已经是终态（done），不能再改")
        n = len(self.cards())
        code, texts = self.overturn(d)
        self.assertEqual(code, 0)
        self.assertFalse(any(v.get("secretary") == "reselect" for c in self.cards(n) for v in buttons(c)))
        self.assertIn("不用改选", texts[-1])
        self.assertTrue(SR.finished(SL.get(self.st, d["id"]), self.progress(d)))

    def test_reselect_after_task_thawed_kept_in_record(self):
        self.resolve.side_effect = SR.G.Rejected(f"{TASK} 不是冻结状态，没有要更正的")
        code, texts = self.click("reselect", self.d["id"], card=self.reselect_token(), option=2)
        self.assertEqual(code, 0)
        rec = self.progress(self.d)
        self.assertEqual((rec["resolution"]["status"], rec["reselected"]["o"]), ("after_thaw", 2))
        self.assertEqual(SL.get(self.st, self.d["id"])["disposition"], "corrected")
        self.assertIn("只记在推翻记录里", texts[-1])
        self.assertNotIn("恢复 t-", texts[-1])

    def test_reselect_recorded_before_said_plainly(self):
        self.resolve.side_effect = SR.G.Rejected("这次冻结已经记过更正")
        code, texts = self.click("reselect", self.d["id"], card=self.reselect_token(), option=1)
        self.assertEqual(code, 0)
        self.assertIn("已经记过一条更正", texts[-1])

    def test_interrupted_reselect_continues_with_first_choice(self):
        rec = self.progress(self.d)
        rec["reselect_pending"] = {"o": 1, "at": C.iso(T1)}
        CR.write_json(CR.overturn_path(self.d, SL.SECRETARY), rec)
        code, texts = self.click("reselect", self.d["id"], card=self.reselect_token(), option=2)
        self.assertEqual(code, 0)
        self.assertIn("按第 1 项接着记", texts[-1])
        self.assertIn("改选第 1 项", self.resolve.call_args[0][3])
        self.assertEqual(self.progress(self.d)["reselected"]["o"], 1)
        self.assertNotIn("reselect_pending", self.progress(self.d))

    def test_disposition_repaired_on_next_click(self):
        with mock.patch.object(SR.L, "set_disposition", side_effect=L.LedgerError("库忙")):
            self.assertEqual(self.click("reselect", self.d["id"], card=self.reselect_token(), option=1)[0], 0)
        self.assertEqual(SL.get(self.st, self.d["id"])["disposition"], "overturned")
        code, texts = self.click("reselect", self.d["id"], card=self.reselect_token(), option=1)
        self.assertEqual(code, 0)
        self.assertEqual(SL.get(self.st, self.d["id"])["disposition"], "corrected")

    def test_reselect_card_resent_when_not_verified(self):
        d = self.decided("ask", target={"card": 8, "task": TASK2, "generation": 1})
        self.freeze.return_value = {"task_id": TASK2, "status": "frozen", "generation": 1, "freeze_code": "d4e5f6"}
        self.day(force=True)
        with mock.patch.object(CR, "lark", side_effect=CR.SendError("回读超时")):
            code, texts = self.overturn(d)
        self.assertEqual(code, 1)
        self.assertIn("改选卡没发成", texts[-1])
        first = self.progress(d)["reselect_card"]["card"]
        code, _ = self.overturn(d)                                            # 回读通了：按原消息号核实，不重发
        self.assertEqual(code, 0)
        self.assertEqual(self.progress(d)["reselect_card"]["card"], first)


class MergeOverturnTests(Base):
    def revert(self, d):
        def run(repo, mc, did, title, on_head):
            on_head("9" * 40)
            return {"status": "opened", "repo": repo, "pr": 41, "url": f"https://github.com/{repo}/pull/41", "head": "9" * 40,
                    "branch": f"revert/{did}"}
        return run

    def test_merge_opens_revert_pr_and_revert_card(self):
        d = self.decided("merge")
        code, texts = self.overturn(d, revert=self.revert(d))
        self.assertEqual(code, 0)
        self.assertEqual(SL.get(self.st, d["id"])["disposition"], "reverted")
        rec = self.progress(d)
        self.assertEqual(rec["revert_card"]["status"], "verified")
        card = self.cards()[-1]
        self.assertEqual(buttons(card), [{"secretary": "merge_revert", "ref": d["id"], "card": rec["revert_card"]["card"]}])
        self.assertIn("撤销秘书合并", json.dumps(card, ensure_ascii=False))
        text = self.resolve.call_args[0][3]
        self.assertIn("pull/41", text)
        self.assertIn("合并撤销 PR", texts[-1])
        # 「合并撤销 PR」按钮：令牌对才交给合并执行器
        merge = mock.Mock(return_value={"status": "merged", "merge_commit": "8" * 40})
        self.assertEqual(self.click("merge_revert", d["id"], card="0" * 16, merge=merge)[0], 1)
        merge.assert_not_called()
        code, texts = self.click("merge_revert", d["id"], card=rec["revert_card"]["card"], merge=merge)
        self.assertEqual(code, 0)
        merge.assert_called_once_with(REPO, 41, "9" * 40)
        self.assertIn("撤销 PR 已合并", texts[-1])

    def test_unknown_merge_waits_and_records_nothing(self):
        d = self.decided("merge", "unknown")
        code, texts = self.overturn(d, revert=lambda *a, **k: self.fail("没核清不能开撤销 PR"))
        self.assertEqual(code, 0)                                            # 等核清不算失败（同托管），但推翻没做完
        rec = self.progress(d)
        self.assertEqual(rec["revert"]["status"], "waiting")
        self.assertFalse(SR.finished(SL.get(self.st, d["id"]), rec))
        self.freeze.assert_called_once()
        self.resolve.assert_not_called()
        self.assertIn("还没核清", texts[-1])

    def test_unknown_merge_settled_failed_finishes_with_resolution(self):
        """结果不明时推翻（冻结了）→ 后来核清没合上：再点「推翻」不用撤销，补记更正，冻结码才给（第 1 轮自审 P1-1）。"""
        d = self.decided("merge", "unknown")
        self.overturn(d, revert=lambda *a, **k: self.fail("没核清不能开撤销 PR"))
        SL.reconcile(self.st, d["id"], "failed", {"checked": "gh pr view", "pr_state": "OPEN"}, now=T1)
        code, texts = self.overturn(d, revert=lambda *a, **k: self.fail("没合上不用撤销"))
        self.assertEqual(code, 0)
        rec = self.progress(d)
        self.assertEqual(rec["revert"]["status"], "not_needed")
        self.resolve.assert_called_once()
        self.assertTrue(SR.finished(SL.get(self.st, d["id"]), rec))
        self.assertIn("恢复 t-20261118-abcdef a1b2c3", texts[-1])

    def test_unknown_merge_settled_done_opens_revert(self):
        d = self.decided("merge", "unknown")
        self.overturn(d, revert=lambda *a, **k: self.fail("没核清不能开撤销 PR"))
        SL.reconcile(self.st, d["id"], "done", {"checked": "gh pr view", "pr_state": "MERGED", "merge_commit": MC}, now=T1)
        code, _ = self.overturn(d, revert=self.revert(d))
        self.assertEqual(code, 0)
        self.assertEqual(self.progress(d)["revert"]["status"], "opened")
        self.assertEqual(SL.get(self.st, d["id"])["disposition"], "reverted")

    def test_failed_merge_needs_no_revert(self):
        d = self.decided("merge", "failed")
        code, _ = self.overturn(d, revert=lambda *a, **k: self.fail("没合上不用撤销"))
        self.assertEqual(code, 0)
        self.freeze.assert_not_called()


class CustodyDefaultsTests(unittest.TestCase):
    """托管零件加了用途参数，默认仍是托管：按钮键、进度文件目录、撤销卡标题。"""

    def test_defaults_stay_custody(self):
        d = {"id": "d-261118-abcdef", "session_id": "c-20261118-130000-0123456789ab", "summary": "合并 #3", "context": "x",
             "disposition": "open", "status": "done", "action": "merge", "target": {"repo": REPO, "pr": 3}}
        self.assertEqual(CR.overturn_buttons([d], "a" * 16)[0]["columns"][0]["elements"][0]["behaviors"][0]["value"],
                         {"custody": "overturn", "ref": d["id"], "card": "a" * 16})
        self.assertEqual(CR.overturn_path(d).parent.parent, L.raw_dir())
        card = CR.revert_card(d, {"repo": REPO, "pr": 4, "url": "u"}, "b" * 16)
        self.assertIn("撤销托管合并", card["header"]["title"]["content"])
        self.assertEqual(buttons(card)[0]["custody"], "merge_revert")
        self.assertEqual(CR.card_refs(card, "merge_revert"), [d["id"]])
        self.assertEqual(CR.card_refs(card, "merge_revert", "secretary"), [])


if __name__ == "__main__":
    unittest.main()
