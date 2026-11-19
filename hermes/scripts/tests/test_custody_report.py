"""隔离自测：托管汇报卡、推翻、下游影响清单、托管回退（custody_report.py、custody_impact.py、merge_act.run_custody_revert，#133）。

库、原文目录、夜跑记录都放临时目录；飞书发送 / 回读打桩（不联网）；GitHub 用假对象；revert 用本地裸仓库跑真 git，
gh 和钥匙串打桩。不碰 ~/Library/Application Support/DigitalTwin、不碰 ~/.hermes。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_custody_report.py -v
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import custody as C  # noqa: E402
import custody_impact as IMP  # noqa: E402
import custody_ledger as L  # noqa: E402
import custody_report as R  # noqa: E402
import merge_act as MA  # noqa: E402

TZ = C.TZ
T0 = dt.datetime(2026, 11, 14, 23, 40, tzinfo=TZ)
SID = "c-20261114-233000-0123456789ab"
CHAT = "oc_fixture_home"
REPO = "Natsukashii1997/dt-sandbox"
LIMITS = {"reply": {"allowed": True, "per_issue": 2, "per_session": 10}, "merge": {"allowed": True, "per_session": 20}}
LEDGER = {}                 # 测试库的账本身份（setUp 里登记托管后填）


def on(t=None):
    return {"state": "on", "session": {"id": SID, "ledger": {"id": LEDGER.get("id")}}, "policy": {"ok": True, "limits": LIMITS},
            "admission": {"eligible": True, "reasons": []}}


def session(**kw):
    s = {"id": SID, "started_at": C.iso(T0 - dt.timedelta(minutes=10)), "until": None, "policy": {"version": 3, "sha256": "0" * 64},
         "ledger": {"id": LEDGER.get("id")}, "drain": None, "frozen": None, "events": []}
    s.update(kw)
    return s


def ended_doc(**kw):
    return {"state": "off", "session": None, "history": [session(ended={"at": C.iso(T0 + dt.timedelta(hours=8)), "how": "排空完成"}, **kw)]}


def frozen_doc():
    s = session(drain={"started_at": C.iso(T0), "deadline": C.iso(T0 + dt.timedelta(hours=2)), "source": "feishu"},
                frozen={"at": C.iso(T0 + dt.timedelta(hours=2)), "reason": "排空截止已过，没有收完", "items": ["dt-sandbox#41 对账不清"],
                        "source": "到点（排空截止）"})
    return {"state": "frozen", "session": s, "history": []}


class FakeGitHub:
    def __init__(self, pulls=(), branches=None, ancestors=(), refs=None, down=False, commits=None, forced=()):
        self._pulls, self._branches, self._anc, self._refs, self.down = list(pulls), branches or {}, set(ancestors), refs or {}, down
        self._commits, self._forced = commits or {}, set(forced)

    def _check(self):
        if self.down:
            raise IMP.Unavailable("gh api 失败（EOF）")

    def pulls(self, repo):
        self._check()
        return self._pulls

    def pull(self, repo, n):
        self._check()
        return next(p for p in self._pulls if p["number"] == n)

    def pr_commits(self, repo, n):
        self._check()
        return self._commits.get(n) or [self.pull(repo, n)["head"]["sha"]]

    def force_pushed(self, repo, n):
        self._check()
        return n in self._forced

    def branches(self, repo):
        self._check()
        return self._branches

    def is_ancestor(self, repo, base, head):
        self._check()
        return base == head or (base, head) in self._anc

    def cross_refs(self, repo, n):
        self._check()
        return self._refs.get(n, [])


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-custody-report-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "twin-state.db"
        self.raw = self.root / "custody"
        self.nights = self.root / "runs"
        p = mock.patch.dict(os.environ, {"DT_STATE_DB": str(self.db), "DT_CUSTODY_DIR": str(self.raw),
                                         "FEISHU_HOME_CHANNEL": CHAT, "DT_HERMES_ENV": str(self.root / "no.env")})
        p.start()
        self.addCleanup(p.stop)
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()
        self.addCleanup(self.st.close)
        sp = mock.patch.object(L, "sandbox_projects", return_value={"dt-sandbox": REPO})
        sp.start()
        self.addCleanup(sp.stop)
        LEDGER["id"] = L.register_session(self.st, SID, T0)
        self.doc = ended_doc()                                               # 按钮核账本身份时读的 custody.json
        cl = mock.patch.object(R.C, "load", side_effect=lambda *a, **k: self.doc)
        cl.start()
        self.addCleanup(cl.stop)
        self.sent = []
        self.mget = {}

    # ---- 账本数据 ----
    def reply(self, issue="DTS-9", status="done", now=T0, **kw):
        d = L.issue(self.st, session_id=SID, project="dt-sandbox", action="reply", now=now, status_reader=on,
                    target={"issue": issue, "repo": REPO, "night": "night-1114"}, question="失败后最多重试几次：3 次还是 5 次",
                    context="给示例服务加失败重试", summary="重试 3 次", basis="仓库现有配置就是 3 次", revocability="correctable",
                    expires_at=now + dt.timedelta(minutes=30), **kw)
        return self.drive(d, status, now)

    def merge(self, pr=38, status="done", merge_commit="e" * 40, now=T0):
        d = L.issue(self.st, session_id=SID, project="dt-sandbox", action="merge", now=now, status_reader=on,
                    target={"repo": REPO, "pr": pr, "head": "a" * 40}, context=f"合并 PR #{pr}「重试次数改成可配置」", summary=f"合并 #{pr}",
                    basis="测试全部通过，审查结论是通过", revocability="revertible", expires_at=now + dt.timedelta(minutes=30))
        return self.drive(d, status, now, {"merge_commit": merge_commit} if status == "done" else {})

    def suspend(self, issue="DTS-12"):
        return L.issue(self.st, session_id=SID, project="dt-sandbox", action="suspend", target={"issue": issue},
                       context="让 agent 开的 PR 能自动合并", question="要不要把 main 分支规则改成 1 个人批准就能合并",
                       ask_owner="回复我「同意改」或「不改」", basis="改仓库的合并规则只能你本人决定", revocability="irrevocable", now=T0,
                       status_reader=on)

    def drive(self, d, status, now, outcome=None):
        if status in ("issued", "void"):
            return L.void(self.st, d["id"], "托管排空", now=now) if status == "void" else d
        L.begin(self.st, d["id"], now=now, status_reader=on)
        if status == "executing":
            return L.get(self.st, d["id"])
        if status in ("unknown", "unreconciled"):
            L.finish(self.st, d["id"], "unknown", now=now)
            return (L.reconcile(self.st, d["id"], "unreconciled", {"checked": "gh pr view：查不到"}, now=now) if status == "unreconciled"
                    else L.get(self.st, d["id"]))
        return L.finish(self.st, d["id"], status, outcome=outcome, now=now)

    # ---- 飞书桩 ----
    def fake_send(self, chat, msg_type, content, key):
        self.sent.append({"chat": chat, "type": msg_type, "content": content, "key": key})
        mid = f"om_fixture{len(self.sent):04d}"
        # 飞书回读只给渲染后的文字：正文照抄，按钮显示成「[按钮字]」（11-14 试发回读的样子），读不到按钮的 value
        shown = json.dumps([e for e in _walk_text(content)], ensure_ascii=False) + "".join(
            f"[{b['text']['content']}]" for b in _button_nodes(content)) if msg_type == "interactive" else json.dumps(content)
        self.mget[mid] = {"msg_type": msg_type, "content": shown}
        return mid

    def fake_lark(self, args, what):
        m = self.mget.get(args[args.index("--message-ids") + 1])
        return {"messages": [m] if m else []}

    def send_report(self, doc, **kw):
        with mock.patch.object(R, "send", side_effect=self.fake_send), mock.patch.object(R, "lark", side_effect=self.fake_lark):
            return R.send_report(SID, now=T0 + dt.timedelta(hours=8), doc=doc, store=self.st, **kw)

    def token_for(self, verb, ref):
        """按钮上的卡片令牌：推翻按钮取最近一张列了它的汇报卡；合并撤销 PR 取最近一张撤销卡。"""
        if verb == "overturn":
            p = self.raw / SID / "report.json"
            sent = json.loads(p.read_text())["sent"] if p.exists() else []
            return next((x["card"] for x in reversed(sent) if ref in x.get("actionable", [])), None)
        p = self.raw / SID / f"{ref}.overturn.json"
        return (json.loads(p.read_text()).get("revert_card") or {}).get("card") if p.exists() else None

    def click(self, verb, ref, chat=CHAT, card="auto", **kw):
        texts = []

        def sender(chat_, msg_type, content, key):
            texts.append(content["text"] if msg_type == "text" else content)
            return self.fake_send(chat_, msg_type, content, key)
        card = self.token_for(verb, ref) if card == "auto" else card
        with mock.patch.object(R, "lark", side_effect=self.fake_lark):
            code = R.cmd_button(verb, ref, chat, card=card, now=T0 + dt.timedelta(hours=9), sender=sender, **kw)
        return code, texts


def card_text(card):
    return json.dumps(card, ensure_ascii=False)


def _walk_text(card):
    """卡片里给人看的文字（markdown 和表格单元格），不含按钮的 value。"""
    out = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("tag") == "markdown":
                out.append(x["content"])
            for r in x.get("rows") or []:
                out.extend(str(v) for v in r.values())
            for k, v in x.items():
                if k not in ("behaviors", "rows"):
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(card)
    return out


def _button_nodes(card):
    out = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("tag") == "button":
                out.append(x)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(card)
    return out


def buttons(card):
    out = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("tag") == "button":
                out.append(x["behaviors"][0]["value"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(card)
    return out


def tables_of(card):
    return [e for e in card["body"]["elements"] if e["tag"] == "table"]


def cells(t, col):
    return [r[col] for r in t["rows"]]


class CardTests(Base):
    """乙版表格 + 白话改写（常浩 11-14）：每行先说是哪件事，写清问了什么、怎么定、为什么、后来怎样、他要做什么，不出现内部术语。"""

    def night_log(self, *runs):
        night = self.nights / "night-1114"
        night.mkdir(parents=True, exist_ok=True)
        (night / "run-log.jsonl").write_text("\n".join(json.dumps(r) for r in runs) + "\n")

    def test_final_card_two_tables_in_plain_words(self):
        ok = self.reply()
        merged = self.merge()
        unrec = self.merge(pr=41, status="unreconciled")
        susp = self.suspend()
        void = self.reply(issue="DTS-11", status="void")
        self.night_log({"event": "run", "issue": "DTS-9", "run_id": "r-1", "status": "completed",
                        "created_at": C.iso(T0 + dt.timedelta(minutes=3)), "pr_url": f"https://github.com/{REPO}/pull/50"})
        s, kind, rows, card = R.build(SID, doc=ended_doc(), store=self.st, night_root=self.nights)
        self.assertEqual(kind, "final")
        self.assertEqual(card["header"]["title"]["content"], "🌙 托管汇报：替你定了 2 件，2 件等你处理")
        self.assertEqual(card["header"]["template"], "blue")
        text = card_text(card)
        self.assertIn(SID, text)                                              # 回读靠托管号（页脚小字）
        self.assertIn("替你回答了 agent 的 1 个问题、合并了 1 个 PR；有 2 件要你处理", text)
        wait, done = tables_of(card)
        self.assertEqual([c["display_name"] for c in wait["columns"]],
                         ["哪件事", "agent 问你什么", "我怎么处理的", "为什么", "后来怎样", "你要做什么"])
        self.assertEqual([c["display_name"] for c in done["columns"]],
                         ["哪件事", "agent 问你什么", "我替你定的", "为什么", "后来怎样", "你不同意怎么办"])
        self.assertTrue(all(c["data_type"] == "lark_md" for c in wait["columns"]))
        for c in wait["columns"]:                                             # 飞书自定义列宽只收 80–600px（11-14 试发 70px 被拒）
            if c["width"] != "auto":
                self.assertTrue(c["width"].endswith("px") and 80 <= int(c["width"][:-2]) <= 600, c)
        self.assertEqual(cells(wait, "what"), ["**合并 PR #41「重试次数改成可配置」**\ndt-sandbox PR #41",
                                               "**让 agent 开的 PR 能自动合并**\n任务 DTS-12"])
        self.assertEqual(cells(wait, "did"), ["**合并 #41**，但不确定做成没有", "**没替你定**"])
        self.assertEqual(cells(wait, "todo"), ["到 GitHub 看 PR #41 合上没有，回复我「#41 已合并」或「#41 没合并」",
                                               "回复我「同意改」或「不改」"])
        self.assertEqual(cells(done, "ask"), ["失败后最多重试几次：3 次还是 5 次", "（不是 agent 问的：按托管规则可以直接合）"])
        self.assertEqual(cells(done, "did"), ["**重试 3 次**", "**合并 #38**"])
        self.assertEqual(cells(done, "after"), ["agent 后来又跑了 1 次，最近一次跑完了，开了 PR #50", "已合进 main"])
        self.assertEqual(cells(done, "todo"), ["点「推翻」，再到任务 DTS-9 上写你要的答案",
                                               "点「推翻」：我开一个撤销这次合并的 PR，你点合并才生效"])
        self.assertIn("另有 1 个回复没发出去：任务 DTS-11", text)
        self.assertEqual(sorted(b["ref"] for b in buttons(card)), sorted(d["id"] for d in (ok, merged, unrec, susp)))
        self.assertNotIn(void["id"], [b["ref"] for b in buttons(card)])     # 没发出去的没有推翻按钮
        labels = [b["text"]["content"] for b in _button_nodes(card)]
        self.assertIn("推翻：重试 3 次", labels)
        self.assertIn("推翻：合并 #38", labels)
        hit = L.JARGON.search(json.dumps([e for e in card["body"]["elements"][:-1]], ensure_ascii=False))
        self.assertIsNone(hit, hit and hit.group(0))                          # 除了末尾编号，卡上没有内部术语

    def test_after_column_says_unknown_when_night_log_missing(self):
        self.reply()
        _, _, _, card = R.build(SID, doc=ended_doc(), store=self.st, night_root=self.nights)
        self.assertEqual(cells(tables_of(card)[0], "after"), ["还不知道（夜跑记录读不到）"])
        self.night_log({"event": "run", "issue": "DTS-9", "run_id": "r-0", "created_at": C.iso(T0 - dt.timedelta(minutes=1))})
        _, _, _, card = R.build(SID, doc=ended_doc(), store=self.st, night_root=self.nights)
        self.assertEqual(cells(tables_of(card)[0], "after"), ["agent 还没开始处理"])    # 决定之前的运行不算

    def test_frozen_title_and_explanation(self):
        self.merge(pr=41, status="unreconciled")
        _, kind, _, card = R.build(SID, doc=frozen_doc(), store=self.st)
        self.assertEqual((kind, card["header"]["title"]["content"], card["header"]["template"]),
                         ("frozen", "⚠️ 托管未能安全收尾", "red"))
        self.assertIn("托管已经锁住", card_text(card))
        self.assertIn("在私聊说「解除冻结」", card_text(card))
        _, kind, _, card = R.build(SID, doc=ended_doc(frozen=frozen_doc()["session"]["frozen"]), store=self.st)
        self.assertEqual((kind, card["header"]["title"]["content"]), ("final", "⚠️ 托管未能安全收尾"))   # 冻结过，解除后汇报照样写
        self.assertIn("你已经解除", card_text(card))

    def test_only_after_session_ends(self):
        for state in ("on", "draining"):
            doc = {"state": state, "session": session(), "history": []}
            with self.assertRaises(R.ReportError) as cm:
                R.build(SID, doc=doc, store=self.st)
            self.assertEqual(cm.exception.code, "NotEnded")
        with self.assertRaises(R.ReportError):
            R.build(SID, doc={"state": "off", "session": None, "history": []}, store=self.st)

    def test_disposition_shown_and_button_removed(self):
        d = self.reply()
        L.set_disposition(self.st, d["id"], "overturned", "x", now=T0)
        _, _, _, card = R.build(SID, doc=ended_doc(), store=self.st)
        self.assertEqual(cells(tables_of(card)[0], "todo"), ["你已处理：已推翻·已记录"])
        self.assertEqual(buttons(card), [])

    def test_empty_session(self):
        _, _, _, card = R.build(SID, doc=ended_doc(), store=self.st)
        self.assertIn("agent 没来问过问题", card_text(card))
        self.assertEqual(tables_of(card), [])

    def test_big_card_is_trimmed(self):
        for i in range(60):
            L.issue(self.st, session_id=SID, project="dt-sandbox", action="suspend", target={"issue": f"DTS-{i + 1}"},
                    context="很长的事情名字" * 5, question="很长的问题" * 10, ask_owner="回复我怎么定", basis="拿不准" * 30,
                    revocability="irrevocable", now=T0, status_reader=on)
        _, _, _, card = R.build(SID, doc=ended_doc(), store=self.st)
        self.assertLessEqual(len(card_text(card).encode()), R.CARD_LIMIT + 300)
        self.assertIn("还有 40 件没列", card_text(card))
        self.assertEqual(len(tables_of(card)[0]["rows"]), R.MAX_ROWS)
        self.assertEqual(len(buttons(card)), R.MAX_ROWS)                     # 按钮只给列出来的
        self.assertIn(SID, card_text(card))                                   # 截断也保留末尾编号（回读要用）


class SendTests(Base):
    def report(self):
        return json.loads((self.raw / SID / "report.json").read_text())

    def test_send_reads_back_and_records_once(self):
        d = self.reply()
        r = self.send_report(ended_doc())
        self.assertEqual(r["status"], "verified")
        self.assertRegex(r["card"], R.CARD_TOKEN)
        self.assertEqual((self.sent[0]["chat"], self.sent[0]["type"]), (CHAT, "interactive"))
        rec = self.report()
        self.assertEqual((rec["sent"][0]["decisions"], rec["sent"][0]["actionable"]), ([d["id"]], [d["id"]]))
        self.assertEqual(buttons(self.sent[0]["content"]), [{"custody": "overturn", "ref": d["id"], "card": r["card"]}])
        self.assertEqual(oct((self.raw / SID / "report.json").stat().st_mode & 0o777), "0o600")
        self.assertTrue(self.send_report(ended_doc())["unchanged"])        # 同一种收尾只发一次
        self.assertEqual(len(self.sent), 1)
        r2 = self.send_report(ended_doc(), force=True)
        self.assertEqual(len(self.sent), 2)
        self.assertNotEqual(self.sent[0]["key"], self.sent[1]["key"])
        self.assertNotEqual(r["card"], r2["card"])                          # 每张卡一个新令牌

    def test_frozen_then_final_are_two_reports(self):
        self.merge(pr=41, status="unreconciled")
        self.send_report(frozen_doc())
        self.send_report(ended_doc(frozen=frozen_doc()["session"]["frozen"]))
        self.assertEqual([x["kind"] for x in self.report()["sent"]], ["frozen", "final"])

    def test_readback_mismatch_is_error_but_recorded(self):
        self.reply()
        with mock.patch.object(R, "send", side_effect=self.fake_send), \
                mock.patch.object(R, "lark", return_value={"messages": [{"msg_type": "text", "content": "x"}]}):
            with self.assertRaises(R.ReportError) as cm:
                R.send_report(SID, now=T0, doc=ended_doc(), store=self.st)
        self.assertEqual(cm.exception.code, "SendFailed")
        x = self.report()["sent"][0]
        self.assertEqual((x["status"], x["message_id"]), ("unverified", "om_fixture0001"))

    def test_readback_failure_rechecks_then_resends(self):
        """P1-04：回读没成功不算发过。下次先按原消息号回读，核实了就不重发；核实不了才换新令牌重发。"""
        self.reply()
        with mock.patch.object(R, "send", side_effect=self.fake_send), \
                mock.patch.object(R, "lark", side_effect=R.SendError("回读：网络断了")):
            with self.assertRaises(R.ReportError):
                R.send_report(SID, now=T0, doc=ended_doc(), store=self.st)
        self.assertEqual(self.report()["sent"][0]["status"], "unverified")
        r = self.send_report(ended_doc())                                    # 这次回读通了：原消息核实，不重发
        self.assertTrue(r["rechecked"])
        self.assertEqual((len(self.sent), self.report()["sent"][0]["status"]), (1, "verified"))
        self.assertTrue(self.send_report(ended_doc())["unchanged"])

        self.mget["om_fixture0001"] = {"msg_type": "text", "content": "被撤回了"}   # 原消息对不上 → 换新令牌重发
        rec = self.report()
        rec["sent"][0]["status"] = "unverified"
        R.write_json(self.raw / SID / "report.json", rec)
        r = self.send_report(ended_doc())
        self.assertEqual((len(self.sent), r["status"]), (2, "verified"))
        self.assertNotEqual(r["card"], rec["sent"][0]["card"])

    def test_unverified_card_buttons_rejected(self):
        d = self.reply()
        with mock.patch.object(R, "send", side_effect=self.fake_send), \
                mock.patch.object(R, "lark", side_effect=R.SendError("回读：网络断了")):
            with self.assertRaises(R.ReportError):
                R.send_report(SID, now=T0, doc=ended_doc(), store=self.st)
        token = self.report()["sent"][0]["card"]
        code, texts = self.click("overturn", d["id"], card=token)
        self.assertEqual(code, 1)
        self.assertIn("核对过的汇报卡上", texts[0])
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "open")

    def test_actionable_only_buttons_on_card(self):
        """P2-03：卡片截断后，没出现在卡上的决定不算可操作，点不了。"""
        rows = [L.issue(self.st, session_id=SID, project="dt-sandbox", action="suspend", target={"issue": f"DTS-{i + 1}"},
                        context="很长的事情名字" * 5, question="很长的问题" * 10, ask_owner="回复我怎么定", basis="拿不准" * 30,
                        revocability="irrevocable", now=T0, status_reader=on) for i in range(30)]
        r = self.send_report(ended_doc())
        self.assertEqual(r["actionable"], [b["ref"] for b in buttons(self.sent[0]["content"])])
        self.assertEqual(len(r["actionable"]), R.MAX_ROWS)
        hidden = rows[-1]
        self.assertNotIn(hidden["id"], r["actionable"])
        code, texts = self.click("overturn", hidden["id"], card=r["card"])
        self.assertEqual(code, 1)
        self.assertIn("核对过的汇报卡上", texts[0])

    def test_no_chat(self):
        self.reply()
        with mock.patch.dict(os.environ, {"FEISHU_HOME_CHANNEL": ""}):
            with self.assertRaises(R.ReportError) as cm:
                self.send_report(ended_doc())
        self.assertEqual(cm.exception.code, "NoChat")
        self.assertEqual(self.sent, [])

    def test_only_home_chat(self):
        """P2-04：汇报卡只发常浩私聊。"""
        self.reply()
        with self.assertRaises(R.ReportError) as cm:
            self.send_report(ended_doc(), chat="oc_some_group")
        self.assertEqual(cm.exception.code, "WrongChat")
        self.assertEqual(self.sent, [])

    def test_drains_before_sending(self):
        """P1-07：发卡前在账本里清掉已出未送达的决定，卡上算作「没发出去」。"""
        d = self.reply(status="issued")
        self.send_report(ended_doc())
        got = L.get(self.st, d["id"])
        self.assertEqual((got["status"], got["void_reason"]), ("void", "托管排空"))
        self.assertIn("另有 1 个回复没发出去", card_text(self.sent[0]["content"]))

    def test_early_return_still_checks_ledger(self):
        """第 2 轮 P1-02：已经发过（verified）时也先核账本：库不见了、换了身份都报错，不报「已经发过」。"""
        self.reply()
        self.send_report(ended_doc())
        doc = ended_doc()
        doc["history"][0]["ledger"] = {"id": "L-" + "0" * 16}
        with self.assertRaises(R.ReportError) as cm:
            self.send_report(doc)
        self.assertEqual(cm.exception.code, "LedgerMismatch")
        self.st.close()
        self.db.rename(self.root / "moved.db")
        with mock.patch.object(R, "send", side_effect=self.fake_send), mock.patch.object(R, "lark", side_effect=self.fake_lark):
            with self.assertRaises(R.ReportError) as cm:
                R.send_report(SID, now=T0, doc=ended_doc())
        self.assertEqual(cm.exception.code, "LedgerMissing")
        self.assertFalse(self.db.exists())
        (self.root / "moved.db").rename(self.db)
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()

    def test_readback_checks_token_and_buttons(self):
        """第 2 轮 P2-02：回读要有这张卡的令牌、推翻按钮数对得上，才算核实。"""
        self.reply()
        bad = {"没有令牌": lambda c: c.replace(self.sent[-1]["key"].rsplit("-", 1)[-1], ""),
               "少了按钮": lambda c: c.replace("[推翻：", "[")}
        for name, mangle in bad.items():
            with self.subTest(name):
                def lark(args, what, mangle=mangle):
                    m = dict(self.mget[args[args.index("--message-ids") + 1]])
                    m["content"] = mangle(m["content"])
                    return {"messages": [m]}
                with mock.patch.object(R, "send", side_effect=self.fake_send), mock.patch.object(R, "lark", side_effect=lark):
                    with self.assertRaises(R.ReportError) as cm:
                        R.send_report(SID, now=T0, doc=ended_doc(), store=self.st, force=True)
                self.assertEqual(cm.exception.code, "SendFailed")

    def test_ledger_identity_checked(self):
        """P0-02：账本身份对不上（库被换过）不发汇报。"""
        self.reply()
        for led, code in (({"id": "L-" + "0" * 16}, "LedgerMismatch"), (None, "LedgerMismatch")):
            doc = ended_doc()
            doc["history"][0]["ledger"] = led
            with self.assertRaises(R.ReportError) as cm:
                self.send_report(doc)
            self.assertEqual(cm.exception.code, code)
        self.assertEqual(self.sent, [])


class ButtonTests(Base):
    def reported(self, *ds):
        self.send_report(ended_doc(), force=True)
        return ds

    def test_rejects_other_chat_unknown_and_unreported(self):
        d = self.reply()
        with mock.patch("sys.stdout"):
            self.assertEqual(self.click("overturn", d["id"], chat="oc_someone_else")[0], 1)
        code, texts = self.click("overturn", d["id"])
        self.assertEqual(code, 1)
        self.assertIn("核对过的汇报卡上", texts[0])
        code, texts = self.click("overturn", "d-261115-ffffff")
        self.assertIn("没有决定", texts[0])
        self.reported(d)
        for bad in (None, "", "0" * 16, "xyz"):                               # P1-04：令牌不对的点击不认
            code, texts = self.click("overturn", d["id"], card=bad)
            self.assertEqual(code, 1)
            self.assertIn("核对过的汇报卡上", texts[0])
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "open")

    def test_only_latest_card_after_resend(self):
        """第 2 轮 P2-02：--force 重发后只认最新一张，旧卡上的按钮不认。"""
        d = self.reply()
        self.reported(d)
        old = self.token_for("overturn", d["id"])
        self.reported(d)
        code, texts = self.click("overturn", d["id"], card=old, gh=FakeGitHub(down=True), night_root=self.nights)
        self.assertEqual(code, 1)
        self.assertIn("最新一张", texts[0])
        code, _ = self.click("overturn", d["id"], gh=FakeGitHub(down=True), night_root=self.nights)
        self.assertEqual(code, 0)

    def test_buttons_check_ledger_identity(self):
        """第 2 轮 P1-02：库被换成旧备份、或身份对不上时，推翻和合并撤销 PR 都不处理。"""
        d = self.merge()
        self.reported(d)
        good = json.loads(json.dumps(self.doc))
        self.doc["history"][0]["ledger"] = {"id": "L-" + "0" * 16}
        code, texts = self.click("overturn", d["id"], gh=FakeGitHub(), revert=lambda *a, **k: self.fail("账本对不上不能开"))
        self.assertEqual(code, 1)
        self.assertIn("托管账本对不上", texts[0])
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "open")
        self.doc = good
        (self.raw / SID / "d-261114-abcdef.json").write_text("{}")         # 有原文、账本里没有
        code, texts = self.click("overturn", d["id"], gh=FakeGitHub(), revert=lambda *a, **k: self.fail("账本对不上不能开"))
        self.assertIn("有原文、账本里却没有", texts[0])
        (self.raw / SID / "d-261114-abcdef.json").unlink()
        self.overturn_merge(d)
        self.doc["history"][0]["ledger"] = {"id": "L-" + "0" * 16}
        code, texts = self.click("merge_revert", d["id"], merge=lambda *a: self.fail("账本对不上不能合"))
        self.assertEqual(code, 1)
        self.assertIn("托管账本对不上", texts[0])

    def test_overturn_reply(self):
        d = self.reply()
        later = self.reply(issue="DTS-10", cites=[d["id"]])
        self.reported(d)
        night = self.nights / "night-1114"
        night.mkdir(parents=True)
        (night / "run-log.jsonl").write_text("\n".join(json.dumps(r) for r in [
            {"event": "run", "issue": "DTS-9", "run_id": "r-old", "created_at": C.iso(T0 - dt.timedelta(minutes=5))},
            {"event": "run", "issue": "DTS-9", "run_id": "r-1", "created_at": C.iso(T0 + dt.timedelta(minutes=2)), "status": "running"},
            {"event": "run", "issue": "DTS-9", "run_id": "r-1", "created_at": C.iso(T0 + dt.timedelta(minutes=2)), "status": "completed",
             "pr_url": f"https://github.com/{REPO}/pull/50"},
            {"event": "run", "issue": "DTS-8", "run_id": "r-x", "created_at": C.iso(T0 + dt.timedelta(minutes=3))},
        ]) + "\n")
        gh = FakeGitHub(pulls=[{"number": 50, "head": {"sha": "h50"}, "created_at": "2026-11-14T15:45:00Z", "html_url": "u50"},
                               {"number": 51, "head": {"sha": "h51"}, "created_at": "2026-11-14T15:50:00Z", "html_url": "u51"},
                               {"number": 52, "head": {"sha": "h52"}, "created_at": "2026-11-14T15:50:00Z", "html_url": "u52"}],
                        branches={"main": "hm", "agent/dts-9": "h50", "agent/stack": "h51", "agent/other": "h99"},
                        ancestors={("h50", "h51")})
        code, texts = self.click("overturn", d["id"], gh=gh, night_root=self.nights)
        self.assertEqual(code, 0, texts)
        got = L.get(self.st, d["id"])
        self.assertEqual(got["disposition"], "overturned")
        msg = texts[0]
        for want in ("已推翻：给示例服务加失败重试（任务 DTS-9），我当时定的是「重试 3 次」", "运行 r-1（跑完了）", "PR #50（这些运行开的）",
                     "分支 agent/dts-9", "分支 agent/stack", "PR #51（在这些运行的改动上接着改的）",
                     f"（{later['id']}）", "到任务 DTS-9 上评论「撤回 " + d["id"]):
            self.assertIn(want, msg)
        for unwanted in ("r-old", "r-x", "agent/other", "#52"):
            self.assertNotIn(unwanted, msg)
        cand = [json.loads(x) for x in (self.raw / "rule-candidates.jsonl").read_text().splitlines()]
        self.assertEqual([c["decision"] for c in cand], [d["id"]])
        rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
        self.assertEqual({i["rule"] for i in rec["impacts"]["items"]}, {1, 2, 4})
        code, texts = self.click("overturn", d["id"], gh=gh, night_root=self.nights)    # 再点一次
        self.assertIn("不用再推翻", texts[0])
        self.assertEqual(len((self.raw / "rule-candidates.jsonl").read_text().splitlines()), 1)

    def test_overturn_reply_with_missing_data_says_incomplete(self):
        d = self.reply()
        self.reported(d)
        code, texts = self.click("overturn", d["id"], gh=FakeGitHub(down=True), night_root=self.nights)
        self.assertEqual(code, 0)
        self.assertIn("没列全（规则 1", texts[0])
        self.assertIn("没列全（规则 2", texts[0])
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "overturned")      # 清单不全不影响推翻本身

    def gh_revert_pr(self, d, head="f" * 40, mc="e" * 40, **kw):
        """GitHub 上合并执行器开出的撤销 PR（gh pr list --json 的形状）。"""
        p = {"number": 70, "url": "u70", "headRefOid": head, "state": "OPEN", "baseRefName": "main", "isCrossRepository": False,
             "headRepository": {"name": REPO.split("/")[1]}, "headRepositoryOwner": {"login": REPO.split("/")[0]},
             "body": f"托管决定 {d['id']} 被常浩推翻，回退合并提交 {mc}。",
             "commits": [{"oid": head, "messageHeadline": 'Revert "托管合并 #38"', "messageBody": f"This reverts commit {mc}."}]}
        p.update(kw)
        return p

    def overturn_merge(self, d, revert=None, gh=None, **kw):
        revert = revert or (lambda repo, mc, dec, t, **k: {"status": "opened", "repo": repo, "pr": 70, "url": f"https://github.com/{repo}/pull/70",
                                                      "head": "f" * 40, "branch": f"custody-revert/{dec}"})
        return self.click("overturn", d["id"], gh=gh or FakeGitHub(), revert=revert, **kw)

    def test_overturn_merge_opens_revert_and_card(self):
        d = self.merge()
        follow = self.merge(pr=39)
        self.reported(d)
        gh = FakeGitHub(pulls=[{"number": 39, "merged_at": "2026-11-14T16:00:00Z", "merge_commit_sha": "m39", "html_url": "u39"},
                               {"number": 30, "merged_at": "2026-11-14T10:00:00Z", "merge_commit_sha": "m30", "html_url": "u30"}],
                        ancestors={("e" * 40, "m39")},
                        refs={38: [{"number": 60, "url": "u60", "title": "t", "is_pr": False, "at": "2026-11-14T16:10:00Z"},
                                   {"number": 20, "url": "u20", "title": "t", "is_pr": True, "at": "2026-11-14T09:00:00Z"}]})
        calls = []

        def revert(repo, mc, decision, title, **k):
            calls.append((repo, mc, decision, title))
            return {"status": "opened", "repo": repo, "pr": 70, "url": f"https://github.com/{repo}/pull/70", "head": "f" * 40}
        code, texts = self.click("overturn", d["id"], gh=gh, revert=revert)
        self.assertEqual(code, 0, texts)
        self.assertEqual(calls, [(REPO, "e" * 40, d["id"], f"Revert：托管决定 {d['id']}（{REPO}#38@aaaaaaa）")])
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "reverted")
        card, msg = texts                                                     # 先发撤销卡（回读核实），再发结果
        rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
        self.assertEqual(rec["revert_card"]["status"], "verified")
        self.assertEqual(buttons(card), [{"custody": "merge_revert", "ref": d["id"], "card": rec["revert_card"]["card"]}])
        for want in ("PR #39（之后合进 main", "issue #60 引用了 #38", "已开撤销 PR", "点上面卡片里的「合并撤销 PR」"):
            self.assertIn(want, msg)
        self.assertNotIn("#30", msg)
        self.assertNotIn("#20", msg)
        self.assertEqual(set(rec["stages"]), {"registered", "candidate", "impacts", "revert", "revert_card", "result"})
        code, texts = self.click("overturn", d["id"], gh=gh, revert=lambda *a, **k: self.fail("不该再开"))
        self.assertIn("不用再推翻", texts[0])
        del follow

    def test_overturn_merge_revert_fails_then_retry(self):
        for rv in ({"status": "failed", "why": "git revert 有冲突"}, MA.Stop("钥匙串里没有令牌", "x")):
            with self.subTest(rv=str(rv)):
                d = self.merge(pr=40 + len(self.sent))
                self.reported(d)

                def revert(*a, rv=rv, **k):
                    if isinstance(rv, Exception):
                        raise rv
                    return rv
                code, texts = self.overturn_merge(d, revert=revert)
                self.assertEqual(code, 1)
                self.assertEqual(L.get(self.st, d["id"])["disposition"], "revert_failed")
                self.assertIn("撤销 PR 没开成", texts[0])
                code, texts = self.overturn_merge(d)                          # 确定没开成：再点一次会重试
                self.assertEqual(code, 0, texts)
                self.assertEqual(L.get(self.st, d["id"])["disposition"], "reverted")

    def test_overturn_unsure_revert_only_reads_back(self):
        """P1-03：撤销 PR 开没开成不确定，之后只按分支只读查，不再推送。"""
        d = self.merge()
        self.reported(d)
        code, texts = self.overturn_merge(d, revert=lambda *a, **k: {"status": "unsure", "why": "推分支没拿到确定结果", "head": "f" * 40,
                                                                "branch": f"custody-revert/{d['id']}", "repo": REPO})
        self.assertEqual(code, 1)
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "overturned")      # 不确定不记成回退失败
        self.assertIn("我不会再推一次", texts[0])
        looked = []

        def lookup(repo, branch):
            looked.append((repo, branch))
            return []
        code, texts = self.overturn_merge(d, revert=lambda *a, **k: self.fail("不确定时不能再开"), lookup=lookup)
        self.assertEqual((code, looked), (1, [(REPO, f"custody-revert/{d['id']}")]))
        found = [self.gh_revert_pr(d)]
        code, texts = self.overturn_merge(d, revert=lambda *a, **k: self.fail("不确定时不能再开"), lookup=lambda r, b: found)
        self.assertEqual(code, 0, texts)
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "reverted")
        d2 = self.merge(pr=44)
        other = [self.gh_revert_pr(d2, head="0" * 40)]                      # 分支上的提交和记下的对不上的不认
        self.reported(d2)
        self.overturn_merge(d2, revert=lambda *a, **k: {"status": "unsure", "why": "x", "head": "f" * 40, "repo": REPO})
        code, _ = self.overturn_merge(d2, revert=lambda *a, **k: self.fail("x"), lookup=lambda r, b: other)
        self.assertEqual((code, L.get(self.st, d2["id"])["disposition"]), (1, "overturned"))

    def test_crash_after_pr_created_claims_only_verified_pr(self):
        """第 2 轮 P1-01、定向复核 P1-01：PR 已开、结果没落盘就崩了。续做时只认 HEAD 等于推送前记下的提交号、来源就是目标仓库的 PR
        （同名分支、说明和正文都写对但提交号不同的不认），对不上、不唯一、查不全就保持不确定，全程只推一次。"""
        class Crash(BaseException):
            pass
        d = self.merge()
        self.reported(d)
        pushes = []

        def revert(*a, on_head=None):
            on_head("f" * 40)                                                 # 推送前落盘提交号，之后推了、开了 PR，落盘结果前崩
            pushes.append(a)
            raise Crash()
        with self.assertRaises(Crash):
            self.overturn_merge(d, revert=revert)
        rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
        self.assertEqual({k: rec["revert"][k] for k in ("status", "decision", "repo", "branch", "merge_commit", "head")},
                         {"status": "opening", "decision": d["id"], "repo": REPO, "branch": f"custody-revert/{d['id']}",
                          "merge_commit": "e" * 40, "head": "f" * 40})
        forged = "1" * 40
        bad = {"同名分支、说明和正文都对、提交号不同": [self.gh_revert_pr(d, head=forged, commits=[
                    {"oid": forged, "messageHeadline": "Revert", "messageBody": f"This reverts commit {'e' * 40}."}])],
               "fork 开过来的同名分支": [self.gh_revert_pr(d, isCrossRepository=True, headRepositoryOwner={"login": "someone"})],
               "来源仓库名不对": [self.gh_revert_pr(d, headRepository={"name": "other"})],
               "别的合并提交": [self.gh_revert_pr(d, mc="0" * 40)],
               "多了提交": [self.gh_revert_pr(d, commits=[{"oid": "1" * 40}, {"oid": "f" * 40}])],
               "正文不对": [self.gh_revert_pr(d, body="随便写的")],
               "已关闭": [self.gh_revert_pr(d, state="CLOSED")],
               "两个都对得上": [self.gh_revert_pr(d), self.gh_revert_pr(d, number=71)],
               "没有 PR": [],
               "查不全": MA.Stop("分支上的 PR 有 100 个以上，查不全", "x")}
        for name, found in bad.items():
            with self.subTest(name):
                def lookup(r, b, f=found):
                    if isinstance(f, Exception):
                        raise f
                    return f
                code, texts = self.overturn_merge(d, revert=revert, lookup=lookup)
                self.assertEqual(code, 1)
                self.assertIn("我不会再推一次", texts[-1])
                self.assertNotIn("revert_card", json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())["stages"])
        code, texts = self.overturn_merge(d, revert=revert, lookup=lambda r, b: [self.gh_revert_pr(d)])
        self.assertEqual(code, 0, texts)
        self.assertEqual(len(pushes), 1)
        rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
        self.assertEqual((rec["revert"]["status"], rec["revert"]["head"], rec["revert"]["claimed"]), ("opened", "f" * 40, "只读核验后认领"))
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "reverted")

    def test_crash_before_head_recorded_reopens(self):
        """定向复核 P1-01：推送在提交号落盘之后，所以中断在落盘之前＝一定没推过，续做时重新开（不去认领分支上的任何 PR）。"""
        class Crash(BaseException):
            pass
        d = self.merge()
        self.reported(d)
        calls = []

        def crash(*a, **k):
            calls.append("crash")
            raise Crash()
        with self.assertRaises(Crash):
            self.overturn_merge(d, revert=crash)
        rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
        self.assertEqual(rec["revert"]["status"], "opening")
        self.assertNotIn("head", rec["revert"])
        code, texts = self.overturn_merge(d, lookup=lambda r, b: self.fail("没推过就不该去认领"))
        self.assertEqual(code, 0, texts)
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "reverted")
        d2 = self.merge(pr=45)                                                # 执行器里抛普通异常、没记下提交号：同样重开
        self.reported(d2)
        code, _ = self.overturn_merge(d2, revert=lambda *a, **k: (_ for _ in ()).throw(OSError("断网")))
        self.assertEqual(code, 1)
        code, texts = self.overturn_merge(d2, lookup=lambda r, b: self.fail("没推过就不该去认领"))
        self.assertEqual(code, 0, texts)

    def test_head_recorded_before_executor_returns(self):
        """执行器调 on_head 时提交号已经写进操作凭证（落盘），之后抛的普通异常也保留它，续做只读认领。"""
        d = self.merge()
        self.reported(d)
        seen = []

        def revert(*a, on_head=None):
            on_head("f" * 40)
            seen.append(json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())["revert"]["head"])
            raise OSError("推完断网")
        code, _ = self.overturn_merge(d, revert=revert)
        self.assertEqual((code, seen), (1, ["f" * 40]))
        rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
        self.assertEqual((rec["revert"]["status"], rec["revert"]["head"]), ("unsure", "f" * 40))
        code, texts = self.overturn_merge(d, revert=lambda *a, **k: self.fail("记下过提交号就不能再推"),
                                          lookup=lambda r, b: [self.gh_revert_pr(d)])
        self.assertEqual(code, 0, texts)

    def test_find_revert_pr_limit(self):
        got = []

        def gh_read(args, what):
            got.append(args)
            return [{}] * R.REVERT_LOOKUP_LIMIT
        with mock.patch.object(MA, "gh_read", gh_read):
            with self.assertRaises(MA.Stop):
                R.find_revert_pr(REPO, "custody-revert/x")
        self.assertEqual(got[0][got[0].index("--limit") + 1], str(R.REVERT_LOOKUP_LIMIT))
        for f in ("isCrossRepository", "headRepository", "headRepositoryOwner", "commits", "body"):
            self.assertIn(f, got[0][got[0].index("--json") + 1])

    def test_overturn_unreconciled_merge_waits_then_resumes(self):
        """合并还没核清时记「等核清再开」，不记回退失败；核清确认合上后 resume 开撤销 PR。"""
        d = self.merge(pr=41, status="unreconciled")
        self.reported(d)
        code, texts = self.overturn_merge(d, revert=lambda *a, **k: self.fail("不该开 revert"))
        self.assertEqual(code, 0, texts)
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "overturned")
        self.assertIn("等核清再开", texts[0])
        self.assertIn(f"custody_report.py resume {d['id']}", texts[0])
        code, texts = self.overturn_merge(d, revert=lambda *a, **k: self.fail("还没核清"))     # 再点一次：照样告诉现在到哪了
        self.assertEqual(code, 0)
        self.assertIn("等核清再开", texts[0])
        L.reconcile(self.st, d["id"], "done", {"checked": "gh pr view", "pr_state": "MERGED", "merge_commit": "e" * 40}, now=T0)
        texts = []
        opened = []

        def sender(chat, msg_type, content, key):
            texts.append(content["text"] if msg_type == "text" else content)
            return self.fake_send(chat, msg_type, content, key)
        with mock.patch.object(R, "lark", side_effect=self.fake_lark), mock.patch("sys.stdout"):
            code = R.cmd_button("overturn", d["id"], CHAT, resume=True, now=T0 + dt.timedelta(hours=10), sender=sender,
                                revert=lambda repo, mc, dec, t, **k: opened.append(mc) or {"status": "opened", "repo": repo, "pr": 71,
                                                                                       "url": "u71", "head": "f" * 40},
                                gh=FakeGitHub())
        self.assertEqual((code, opened), (0, ["e" * 40]), texts)
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "reverted")
        self.assertIn("已开撤销 PR：u71", texts[-1])
        self.assertEqual(len((self.raw / "rule-candidates.jsonl").read_text().splitlines()), 1)

    def test_click_when_ledger_missing(self):
        d = self.reply()
        self.reported(d)
        self.st.close()
        self.db.rename(self.root / "moved.db")
        code, texts = self.click("overturn", d["id"])
        self.assertEqual(code, 1)
        self.assertIn("托管账本打不开", texts[0])
        self.assertFalse(self.db.exists())
        (self.root / "moved.db").rename(self.db)
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()

    def test_resume_needs_started_overturn(self):
        d = self.merge()
        with mock.patch("sys.stdout"):
            code = R.cmd_button("overturn", d["id"], CHAT, resume=True, now=T0, sender=lambda *a: "om_x")
        self.assertEqual(code, 1)
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "open")

    def test_overturn_failed_merge_needs_no_revert(self):
        d = self.merge(pr=42, status="failed")
        self.reported(d)
        code, texts = self.overturn_merge(d, revert=lambda *a, **k: self.fail("没合上不用撤"))
        self.assertEqual(code, 0, texts)
        self.assertIn("这次合并没合上，不用撤销", texts[0])
        self.assertIn("不用再推翻", self.overturn_merge(d)[1][0])

    def test_overturn_resumes_after_crash_at_each_stage(self):
        """P1-03：推翻在任一阶段中断，再点一次只补做没完成的，不重复记规则候选、不重复开撤销 PR。"""
        class Crash(BaseException):
            pass

        def boom(*a, **k):
            raise Crash()
        points = [("registered", mock.patch.object(L, "set_disposition", side_effect=boom)),
                  ("candidate", mock.patch.object(R, "append_candidate", side_effect=boom)),
                  ("impacts", mock.patch.object(IMP, "impacts", side_effect=boom)),
                  ("revert", None),
                  ("revert_card", mock.patch.object(R, "revert_card", side_effect=boom)),
                  ("result", mock.patch.object(R, "overturn_message", side_effect=boom))]
        for stage, patch in points:
            with self.subTest(stage=stage):
                d = self.merge(pr=100 + len(self.sent))
                self.reported(d)
                opened = []

                def revert(repo, mc, dec, t, opened=opened, on_head=None):
                    opened.append(dec)
                    if stage == "revert" and len(opened) == 1:
                        on_head("f" * 40)                             # 推送前记下了提交号，推完、PR 开了，结果落盘前崩
                        raise Crash()
                    return {"status": "opened", "repo": repo, "pr": 70, "url": "u70", "head": "f" * 40}
                with (patch or contextlib.nullcontext()):
                    with self.assertRaises(Crash):
                        self.overturn_merge(d, revert=revert)
                code, texts = self.overturn_merge(d, revert=revert, lookup=lambda r, b, d=d: [self.gh_revert_pr(d)])
                rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
                self.assertEqual(code, 0, texts)                              # 崩在开 PR 中途：只读核验认领，不再开第二次
                self.assertEqual(opened, [d["id"]])
                self.assertEqual(set(rec["stages"]), {"registered", "candidate", "impacts", "revert", "revert_card", "result"})
                self.assertEqual(L.get(self.st, d["id"])["disposition"], "reverted")
                cands = [json.loads(x)["decision"] for x in (self.raw / "rule-candidates.jsonl").read_text().splitlines()]
                self.assertEqual(cands.count(d["id"]), 1)

    def test_revert_card_resent_when_readback_fails(self):
        d = self.merge()
        self.reported(d)
        with mock.patch.object(R, "read_back", return_value=(False, "回读没成功")):
            code, texts = self.overturn_merge(d)
        self.assertEqual(code, 1)
        self.assertIn("卡片没发成，再点一次「推翻」我会重发", texts[-1])
        rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
        old = rec["revert_card"]["card"]
        code, texts = self.click("merge_revert", d["id"], card=old, merge=lambda *a: self.fail("没核实的卡不能合"))
        self.assertIn("不是最新一张撤销卡", texts[0])
        self.mget.clear()                                                     # 原消息回读不到 → 换新令牌重发
        code, texts = self.overturn_merge(d)
        self.assertEqual(code, 0, texts)
        rec = json.loads((self.raw / SID / f"{d['id']}.overturn.json").read_text())
        self.assertNotEqual(rec["revert_card"]["card"], old)
        self.assertEqual(rec["revert_card"]["status"], "verified")

    def merged_revert(self, d):
        self.reported(d)
        self.overturn_merge(d)

    def test_merge_revert_uses_owner_merge_with_checks(self):
        d = self.merge()
        self.merged_revert(d)
        calls = []
        code, texts = self.click("merge_revert", d["id"],
                                 merge=lambda repo, pr, head: calls.append((repo, pr, head)) or {"status": "merged", "merge_commit": "9" * 40})
        self.assertEqual(code, 0, texts)
        self.assertEqual(calls, [(REPO, 70, "f" * 40)])
        self.assertIn("撤销 PR 已合并", texts[0])
        code, texts = self.click("merge_revert", d["id"], merge=lambda *a: self.fail("合过了"))
        self.assertIn("已经合过了", texts[0])

    def test_merge_revert_stop_explained_without_jargon(self):
        d = self.merge()
        self.merged_revert(d)
        stop = MA.Stop("改动碰了敏感路径（L3），飞书里不合并", "在电脑上交给 Claude 会话处理")

        def merge(*a):
            raise stop
        code, texts = self.click("merge_revert", d["id"], merge=merge)
        self.assertEqual(code, 1)
        self.assertIn("改动碰了敏感路径，飞书里不合并", texts[0])
        self.assertIsNone(L.JARGON.search(texts[0].split("https://")[0]))

    def test_merge_revert_unattributed_and_failed(self):
        d = self.merge()
        self.merged_revert(d)
        code, texts = self.click("merge_revert", d["id"], merge=lambda *a: {"status": "failed", "why": "GitHub 拒绝"})
        self.assertEqual(code, 1)
        self.assertIn("撤销 PR 没合上（GitHub 拒绝）", texts[0])
        code, texts = self.click("merge_revert", d["id"], merge=lambda *a: {"status": "merged_unattributed", "merge_commit": None})
        self.assertEqual(code, 0)
        self.assertIn("不是这次点击合的", texts[0])

    def test_merge_revert_token_checked(self):
        d = self.merge()
        self.merged_revert(d)
        for bad in (None, "0" * 16):
            code, texts = self.click("merge_revert", d["id"], card=bad, merge=lambda *a: self.fail("令牌不对不能合"))
            self.assertEqual(code, 1)
            self.assertIn("不是最新一张撤销卡", texts[0])

    def test_merge_revert_without_revert_record(self):
        d = self.merge()
        self.reported(d)
        code, texts = self.click("merge_revert", d["id"], merge=lambda *a: self.fail("x"))
        self.assertEqual(code, 1)
        self.assertIn("没有开成的撤销 PR", texts[0])


class ImpactTests(Base):
    """#133 第 1 轮审查 P1-05、P1-06：夜跑记录不全要写进「没列全」，PR 要翻完所有页，种子用 PR 的全部提交，强推过要说明。"""

    def night_log(self, *lines):
        night = self.nights / "night-1114"
        night.mkdir(parents=True, exist_ok=True)
        (night / "run-log.jsonl").write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n")

    def after(self, minutes):
        return C.iso(T0 + dt.timedelta(minutes=minutes))

    def test_bad_runlog_lines_are_gaps(self):
        d = self.reply()
        self.night_log({"event": "run", "issue": "DTS-9", "run_id": "r-1", "created_at": self.after(2), "status": "completed"},
                       "{坏行", "[1, 2]", {"event": "run", "issue": "DTS-9", "created_at": self.after(3)},
                       {"event": "run", "issue": "DTS-9", "run_id": "r-2", "created_at": self.after(4), "incomplete": True}, "")
        runs, problems = IMP.later_runs(d, self.nights)
        self.assertEqual([r["run_id"] for r in runs], ["r-1", "r-2"])
        self.assertEqual(len(problems), 4, problems)
        imp = IMP.impacts(self.st, d, gh=FakeGitHub(), night_root=self.nights)
        self.assertEqual(len([g for g in imp["gaps"] if g["rule"] == 1]), 4)
        self.assertEqual(R.later(d, self.nights), "还不知道（夜跑记录不完整）")         # 汇报卡不把不完整的记录当成真相

    def test_pr_commits_seed_and_force_push_gap(self):
        d = self.reply()
        self.night_log({"event": "run", "issue": "DTS-9", "run_id": "r-1", "created_at": self.after(2), "status": "completed",
                        "pr_url": f"https://github.com/{REPO}/pull/50"})
        gh = FakeGitHub(pulls=[{"number": 50, "head": {"sha": "h50-now"}, "created_at": "2026-11-14T15:45:00Z", "html_url": "u50"}],
                        branches={"main": "hm", "agent/built-on-old": "hx"}, ancestors={("h50-old", "hx")},
                        commits={50: ["h50-old", "h50-now"]}, forced={50})
        imp = IMP.impacts(self.st, d, gh=gh, night_root=self.nights)
        self.assertIn("分支 agent/built-on-old", [i["what"] for i in imp["items"]])    # 以 PR 早先的提交为基础的分支也列出
        self.assertTrue(any(g["rule"] == 2 and "强推" in g["why"] for g in imp["gaps"]))

    def run_gh(self, pages):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(pages), stderr="")
        return run, calls

    def test_github_reads_all_pages(self):
        pages = [[{"number": i} for i in range(100, 0, -1)], [{"number": 0}]]
        run, calls = self.run_gh(pages)
        with mock.patch.object(IMP.subprocess, "run", side_effect=run):
            got = IMP.GitHub().pulls(REPO)
        self.assertEqual(len(got), 101)
        self.assertIn("--paginate", calls[0])
        self.assertIn("--slurp", calls[0])
        run, calls = self.run_gh([[{"sha": "a"}] * 100, [{"sha": "b"}]])
        with mock.patch.object(IMP.subprocess, "run", side_effect=run):
            self.assertEqual(len(IMP.GitHub().pr_commits(REPO, 50)), 101)
        run, calls = self.run_gh([[{"event": "committed"}] * 100, [{"event": "head_ref_force_pushed"}]])
        with mock.patch.object(IMP.subprocess, "run", side_effect=run):
            self.assertTrue(IMP.GitHub().force_pushed(REPO, 50))              # 强推记在第二页也看得到


class MergeRevertAsOwnerTests(unittest.TestCase):
    """merge_act.merge_revert_as_owner（P0-01、P1-08）：和飞书卡片合并同一套检查，CI 必须通过、L3 拦下。gh 全部打桩。"""
    HEAD = "f" * 40

    def view(self, **kw):
        v = {"number": 70, "state": "OPEN", "headRefOid": self.HEAD, "baseRefName": "main", "isDraft": False, "mergeable": "MERGEABLE",
             "mergeStateStatus": "CLEAN", "files": [{"path": "a.txt"}],
             "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}]}
        v.update(kw)
        return v

    def go(self, view, after=None, merge_code=0, required=("test",)):
        reads = iter([view, after or {"state": "MERGED", "mergeCommit": {"oid": "9" * 40}}])
        merged = []
        rules = {**MA.mo.load_rules(), "revert_required_checks": {REPO: list(required)} if required else {}}
        with mock.patch.object(MA, "gh_read", side_effect=lambda args, what: next(reads)), \
                mock.patch.object(MA.mo, "load_rules", return_value=rules), \
                mock.patch.object(MA, "run_owner_merge", side_effect=lambda card: merged.append(card) or {"returncode": merge_code, "why": "w"}):
            try:
                return MA.merge_revert_as_owner(REPO, 70, self.HEAD), merged
            except MA.Stop as e:
                return e, merged

    def test_merges_after_all_checks(self):
        out, merged = self.go(self.view())
        self.assertEqual(out, {"status": "merged", "merge_commit": "9" * 40, "why": "w"})
        self.assertEqual(merged, [{"repo": REPO, "pr": 70, "head": self.HEAD}])

    def test_stops_before_merge(self):
        cases = {"没有 CI": dict(statusCheckRollup=[]), "CI 没过": dict(statusCheckRollup=[{"name": "t", "status": "COMPLETED", "conclusion": "FAILURE"}]),
                 "敏感路径": dict(files=[{"path": "ops/health_check.py"}]), "又有提交": dict(headRefOid="0" * 40),
                 "草稿": dict(isDraft=True), "不是 CLEAN": dict(mergeStateStatus="BLOCKED")}
        for name, kw in cases.items():
            with self.subTest(name):
                out, merged = self.go(self.view(**kw))
                self.assertIsInstance(out, MA.Stop)
                self.assertEqual(merged, [])

    def test_required_test_check_must_succeed(self):
        """第 2 轮 P1-03：全是跳过、全是中立、只有非测试检查成功、没登记必过检查，都不合并。"""
        done = lambda name, r: {"name": name, "status": "COMPLETED", "conclusion": r}  # noqa: E731
        cases = {"全跳过": dict(statusCheckRollup=[done("test", "SKIPPED")]),
                 "全中立": dict(statusCheckRollup=[done("test", "NEUTRAL"), done("lint", "NEUTRAL")]),
                 "只有非测试检查成功": dict(statusCheckRollup=[done("lint", "SUCCESS")]),
                 "同名检查一成一跳": dict(statusCheckRollup=[done("test", "SUCCESS"), done("test", "SKIPPED")])}
        for name, kw in cases.items():
            with self.subTest(name):
                out, merged = self.go(self.view(**kw))
                self.assertIsInstance(out, MA.Stop)
                self.assertEqual(merged, [])
        out, merged = self.go(self.view(), required=())
        self.assertIsInstance(out, MA.Stop)
        self.assertIn("没登记撤销 PR 必须通过的测试检查", out.why)
        self.assertEqual(merged, [])

    def test_unattributed_and_failed(self):
        out, _ = self.go(self.view(), merge_code=1)
        self.assertEqual(out["status"], "merged_unattributed")
        out, _ = self.go(self.view(), after={"state": "OPEN"})
        self.assertEqual(out["status"], "failed")


class RevertTests(unittest.TestCase):
    """run_custody_revert 用本地裸仓库跑真 git；gh（查账号、建 PR、回读 PR）和钥匙串打桩。"""
    TOKEN = "ghp_fixture_executor_token"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-revert-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bare = self.root / "origin.git"
        work = self.root / "seed"
        g = lambda *a, cwd=work: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@x", *a], cwd=cwd,  # noqa: E731
                                                 check=True, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.bare)], check=True)
        work.mkdir()
        g("init", "-q", "-b", "main")
        (work / "a.txt").write_text("1\n")
        g("add", ".")
        g("commit", "-q", "-m", "base")
        (work / "a.txt").write_text("2\n")
        g("commit", "-q", "-am", "托管合并 #38")
        self.merge_commit = g("rev-parse", "HEAD")
        (work / "b.txt").write_text("x\n")
        g("add", ".")
        g("commit", "-q", "-m", "之后的提交")
        g("remote", "add", "origin", str(self.bare))
        g("push", "-q", "origin", "main")
        self.g = g
        self.calls = []

    def run_fake(self, pr_view=None, create_code=0):
        real = subprocess.run

        def run(cmd, **kw):
            self.calls.append((cmd, kw.get("env")))
            if cmd[0] == MA.SECURITY:
                return subprocess.CompletedProcess(cmd, 0, stdout=self.TOKEN + "\n", stderr="")
            if cmd[0] == MA.mo.GH and cmd[1:3] == ["api", "user"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="dt-exec 12345\n", stderr="")
            if cmd[0] == MA.mo.GH and cmd[1:3] == ["pr", "create"]:
                return subprocess.CompletedProcess(cmd, create_code, stdout="", stderr="")
            if cmd[0] == MA.mo.GH and cmd[1:3] == ["pr", "view"]:
                head = real(["git", "rev-parse", f"refs/heads/{cmd[3]}"], cwd=self.bare, capture_output=True, text=True).stdout.strip()
                v = {"number": 70, "url": "https://github.com/x/pull/70", "headRefOid": head, "state": "OPEN", "baseRefName": "main",
                                "isCrossRepository": False, "headRepository": {"name": REPO.split("/")[1]},
                                "headRepositoryOwner": {"login": REPO.split("/")[0]}, **(pr_view or {})}   # 只覆盖给出的字段
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(v), stderr="")
            if cmd[0] == MA.GIT:
                return real(cmd, **kw)
            raise AssertionError(cmd)
        return run

    def go(self, merge_commit=None, on_head=None, **kw):
        with mock.patch.object(MA, "GIT_URL", str(self.bare)), mock.patch.object(MA.subprocess, "run", self.run_fake(**kw)), \
                mock.patch.dict(os.environ, {"PATH": os.environ["PATH"], "HOME": "/Users/should-not-be-used"}, clear=True):
            return MA.run_custody_revert(REPO, merge_commit or self.merge_commit, "d-261115-e4f590", "Revert：托管决定", on_head=on_head)

    def pushed(self):
        return [c for c, _ in self.calls if c[0] == MA.GIT and "push" in c]

    def test_head_recorded_before_push(self):
        """定向复核 P1-01：推送之前先把本地撤销提交号交给 on_head 落盘；落盘失败就不推。"""
        seen = []
        out = self.go(on_head=lambda h: seen.append((h, len(self.pushed()))))
        self.assertEqual(out["status"], "opened", out)
        self.assertEqual(seen, [(out["head"], 0)])                            # 记下时还没推
        branch = subprocess.run(["git", "rev-parse", "refs/heads/custody-revert/d-261115-e4f590"], cwd=self.bare,
                                capture_output=True, text=True).stdout.strip()
        self.assertEqual(branch, out["head"])

    def test_head_not_saved_no_push(self):
        self.calls.clear()

        def fail(h):
            raise OSError("磁盘满")
        out = self.go(on_head=fail)
        self.assertEqual(out["status"], "failed")
        self.assertIn("记不下撤销提交号", out["why"])
        self.assertFalse(self.pushed())

    def test_readback_from_fork_is_unsure(self):
        out = self.go(pr_view={"isCrossRepository": True, "headRepositoryOwner": {"login": "someone"}})   # 提交号对得上，但来自 fork
        self.assertEqual(out["status"], "unsure")

    def test_opens_revert_pr(self):
        out = self.go()
        self.assertEqual(out["status"], "opened", out)
        self.assertEqual((out["pr"], out["branch"]), (70, "custody-revert/d-261115-e4f590"))
        log = subprocess.run(["git", "log", "--format=%an <%ae>|%s", "-1", "refs/heads/custody-revert/d-261115-e4f590"],
                             cwd=self.bare, capture_output=True, text=True).stdout.strip()
        self.assertEqual(log, 'dt-exec <12345+dt-exec@users.noreply.github.com>|Revert "托管合并 #38"')
        show = subprocess.run(["git", "show", "refs/heads/custody-revert/d-261115-e4f590:a.txt"], cwd=self.bare,
                              capture_output=True, text=True).stdout
        self.assertEqual(show, "1\n")                                         # 内容回到合并前
        body = subprocess.run(["git", "log", "--format=%B", "-1", "refs/heads/custody-revert/d-261115-e4f590"], cwd=self.bare,
                              capture_output=True, text=True).stdout
        self.assertIn(f"This reverts commit {self.merge_commit}", body)     # 中断后认领撤销 PR 靠这句（第 2 轮 P1-01）
        create = next(c for c, _ in self.calls if c[1:3] == ["pr", "create"])
        pr_body = create[create.index("--body") + 1]
        self.assertTrue("d-261115-e4f590" in pr_body and self.merge_commit in pr_body)
        for cmd, env in self.calls:
            self.assertNotIn(self.TOKEN, " ".join(cmd))                     # 令牌不进命令行
            if cmd[0] == MA.GIT:
                self.assertEqual((env["GIT_CONFIG_GLOBAL"], env["GIT_CONFIG_NOSYSTEM"]), (os.devnull, "1"))
                self.assertNotEqual(env["HOME"], "/Users/should-not-be-used")   # 不读本机 git 配置
            if cmd[0] != MA.SECURITY:
                self.assertEqual(env["GH_TOKEN"], self.TOKEN)
        create = next(c for c, _ in self.calls if c[1:3] == ["pr", "create"])
        self.assertEqual(create[create.index("--base") + 1], "main")
        self.assertNotIn("merge", [c[1] for c, _ in self.calls if c[0] == MA.mo.GH and c[1] == "pr"][1:2])

    def test_commit_not_on_main_fails_before_push(self):
        out = self.go(merge_commit="f" * 40)
        self.assertEqual(out["status"], "failed")
        self.assertIn("不在 main 上", out["why"])
        self.assertFalse([c for c, _ in self.calls if "push" in c])

    def test_conflict_fails_without_push(self):
        work = self.root / "seed"
        (work / "a.txt").write_text("3\n")
        self.g("commit", "-q", "-am", "又改了 a")
        self.g("push", "-q", "origin", "main")
        out = self.go()
        self.assertEqual(out["status"], "failed")
        self.assertIn("冲突", out["why"])
        self.assertFalse([c for c, _ in self.calls if "push" in c])

    def test_pr_readback_mismatch_is_unsure(self):
        out = self.go(pr_view={"number": 70, "url": "u", "headRefOid": "0" * 40, "state": "OPEN", "baseRefName": "main"})
        self.assertEqual(out["status"], "unsure")


if __name__ == "__main__":
    unittest.main()
