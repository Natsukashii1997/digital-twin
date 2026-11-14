"""隔离自测：托管汇报卡、推翻、下游影响清单、托管回退（custody_report.py、custody_impact.py、merge_act.run_custody_revert，#133）。

库、原文目录、夜跑记录都放临时目录；飞书发送 / 回读打桩（不联网）；GitHub 用假对象；revert 用本地裸仓库跑真 git，
gh 和钥匙串打桩。不碰 ~/Library/Application Support/DigitalTwin、不碰 ~/.hermes。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_custody_report.py -v
"""
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
LIMITS = {"reply": {"allowed": True, "per_issue": 2, "per_session": 10}, "merge": {"allowed": True, "per_session": 5}}
ON = {"state": "on", "session": {"id": SID}, "policy": {"ok": True, "limits": LIMITS}, "admission": {"eligible": True, "reasons": []}}


def session(**kw):
    s = {"id": SID, "started_at": C.iso(T0 - dt.timedelta(minutes=10)), "until": None, "policy": {"version": 3, "sha256": "0" * 64},
         "drain": None, "frozen": None, "events": []}
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
    def __init__(self, pulls=(), branches=None, ancestors=(), refs=None, down=False):
        self._pulls, self._branches, self._anc, self._refs, self.down = list(pulls), branches or {}, set(ancestors), refs or {}, down

    def _check(self):
        if self.down:
            raise IMP.Unavailable("gh api 失败（EOF）")

    def pulls(self, repo):
        self._check()
        return self._pulls

    def pull(self, repo, n):
        self._check()
        return next(p for p in self._pulls if p["number"] == n)

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
        self.sent = []
        self.mget = {}

    # ---- 账本数据 ----
    def reply(self, issue="DTS-9", status="done", now=T0, **kw):
        d = L.issue(self.st, session_id=SID, project="dt-sandbox", action="reply", now=now, status_reader=lambda t: ON,
                    target={"issue": issue, "repo": REPO, "night": "night-1114"}, question="重试次数上限设 3 还是 5",
                    summary="设 3", basis="和现有配置一致", revocability="correctable",
                    expires_at=now + dt.timedelta(minutes=30), **kw)
        return self.drive(d, status, now)

    def merge(self, pr=38, status="done", merge_commit="m" * 40, now=T0):
        d = L.issue(self.st, session_id=SID, project="dt-sandbox", action="merge", now=now, status_reader=lambda t: ON,
                    target={"repo": REPO, "pr": pr, "head": "a" * 40}, question="合并官判建议合并（L1）", summary=f"托管合并 #{pr}",
                    basis="CI 通过，审核凭证对得上", revocability="revertible", expires_at=now + dt.timedelta(minutes=30))
        return self.drive(d, status, now, {"merge_commit": merge_commit} if status == "done" else {})

    def suspend(self, issue="DTS-12"):
        return L.issue(self.st, session_id=SID, project="dt-sandbox", action="suspend", target={"issue": issue},
                       question="要不要改 main 规则集", summary="挂起", basis="改规则集属 D 档", revocability="irrevocable", now=T0)

    def drive(self, d, status, now, outcome=None):
        if status in ("issued", "void"):
            return L.void(self.st, d["id"], "托管排空", now=now) if status == "void" else d
        L.begin(self.st, d["id"], now=now, status_reader=lambda t: ON)
        if status == "executing":
            return L.get(self.st, d["id"])
        if status in ("unknown", "unreconciled"):
            L.finish(self.st, d["id"], "unknown", now=now)
            return L.reconcile(self.st, d["id"], "unreconciled", "查不到", now=now) if status == "unreconciled" else L.get(self.st, d["id"])
        return L.finish(self.st, d["id"], status, outcome=outcome, now=now)

    # ---- 飞书桩 ----
    def fake_send(self, chat, msg_type, content, key):
        self.sent.append({"chat": chat, "type": msg_type, "content": content, "key": key})
        mid = f"om_fixture{len(self.sent):04d}"
        self.mget[mid] = {"msg_type": msg_type, "content": json.dumps(content, ensure_ascii=False)}
        return mid

    def fake_lark(self, args, what):
        return {"messages": [self.mget[args[args.index("--message-ids") + 1]]]}

    def send_report(self, doc, **kw):
        with mock.patch.object(R, "send", side_effect=self.fake_send), mock.patch.object(R, "lark", side_effect=self.fake_lark):
            return R.send_report(SID, now=T0 + dt.timedelta(hours=8), doc=doc, store=self.st, **kw)

    def click(self, verb, ref, chat=CHAT, **kw):
        texts = []

        def sender(chat_, msg_type, content, key):
            texts.append(content["text"] if msg_type == "text" else content)
        code = R.cmd_button(verb, ref, chat, now=T0 + dt.timedelta(hours=9), sender=sender, **kw)
        return code, texts


def card_text(card):
    return json.dumps(card, ensure_ascii=False)


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


class CardTests(Base):
    def test_final_card_groups_and_pins(self):
        ok = self.reply()
        merged = self.merge()
        unrec = self.merge(pr=41, status="unreconciled")
        susp = self.suspend()
        void = self.reply(issue="DTS-11", status="void")
        s, kind, rows, card = R.build(SID, doc=ended_doc(), store=self.st)
        self.assertEqual(kind, "final")
        self.assertEqual(card["header"]["title"]["content"], "🌙 托管汇报 · 已安全收尾")
        self.assertEqual(card["header"]["template"], "blue")
        text = card_text(card)
        self.assertIn(SID, text)                                              # 回读靠托管号
        for d in (unrec, susp):                                               # 置顶的在「要你先看」之后、项目分组之前
            self.assertLess(text.index("要你先看"), text.index(d["id"]))
            self.assertLess(text.index(d["id"]), text.index("dt-sandbox · 2 条"))
        for d in (ok, merged):
            self.assertGreater(text.index(d["id"]), text.index("dt-sandbox · 2 条"))
        for want in ("问：重试次数上限设 3 还是 5", "定：**设 3**", "依据：和现有配置一致", "已送达", "可纠正",
                     "已合并（合并提交 mmmmmmm）", "可回退", "UNRECONCILED", "挂起，等你定", "作废 1 条", "托管排空"):
            self.assertIn(want, text)
        self.assertEqual(sorted(b["ref"] for b in buttons(card)), sorted(d["id"] for d in (ok, merged, unrec, susp)))
        self.assertNotIn(void["id"], [b["ref"] for b in buttons(card)])     # 作废的没有推翻按钮

    def test_frozen_title_and_explanation(self):
        self.merge(pr=41, status="unreconciled")
        _, kind, _, card = R.build(SID, doc=frozen_doc(), store=self.st)
        self.assertEqual((kind, card["header"]["title"]["content"], card["header"]["template"]),
                         ("frozen", "⚠️ 托管未能安全收尾", "red"))
        self.assertIn("解除冻结", card_text(card))
        _, kind, _, card = R.build(SID, doc=ended_doc(frozen=frozen_doc()["session"]["frozen"]), store=self.st)
        self.assertEqual((kind, card["header"]["title"]["content"]), ("final", "⚠️ 托管未能安全收尾"))   # 冻结过，解除后汇报照样写
        self.assertIn("已解除冻结", card_text(card))

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
        self.assertIn("处置：已推翻·已记录", card_text(card))
        self.assertEqual(buttons(card), [])

    def test_big_card_is_trimmed(self):
        for i in range(60):
            L.issue(self.st, session_id=SID, project="dt-sandbox", action="suspend", target={"issue": f"DTS-{i}"},
                    question="很长的问题" * 10, summary="挂起", basis="拿不准" * 10, revocability="irrevocable", now=T0)
        _, _, _, card = R.build(SID, doc=ended_doc(), store=self.st)
        self.assertLessEqual(len(card_text(card).encode()), R.CARD_LIMIT + 200)
        self.assertIn("卡片放不下", card_text(card))


class SendTests(Base):
    def test_send_reads_back_and_records_once(self):
        d = self.reply()
        r = self.send_report(ended_doc())
        self.assertTrue(r["read_back"])
        self.assertEqual((self.sent[0]["chat"], self.sent[0]["type"]), (CHAT, "interactive"))
        rec = json.loads((self.raw / SID / "report.json").read_text())
        self.assertEqual(rec["sent"][0]["decisions"], [d["id"]])
        self.assertEqual(oct((self.raw / SID / "report.json").stat().st_mode & 0o777), "0o600")
        self.assertTrue(self.send_report(ended_doc())["unchanged"])        # 同一种收尾只发一次
        self.assertEqual(len(self.sent), 1)
        self.send_report(ended_doc(), force=True)
        self.assertEqual(len(self.sent), 2)
        self.assertNotEqual(self.sent[0]["key"], self.sent[1]["key"])

    def test_frozen_then_final_are_two_reports(self):
        self.merge(pr=41, status="unreconciled")
        self.send_report(frozen_doc())
        self.send_report(ended_doc(frozen=frozen_doc()["session"]["frozen"]))
        self.assertEqual([x["kind"] for x in json.loads((self.raw / SID / "report.json").read_text())["sent"]], ["frozen", "final"])

    def test_readback_mismatch_is_error_but_recorded(self):
        self.reply()
        with mock.patch.object(R, "send", side_effect=self.fake_send), \
                mock.patch.object(R, "lark", return_value={"messages": [{"msg_type": "text", "content": "x"}]}):
            with self.assertRaises(R.ReportError) as cm:
                R.send_report(SID, now=T0, doc=ended_doc(), store=self.st)
        self.assertEqual(cm.exception.code, "SendFailed")
        rec = json.loads((self.raw / SID / "report.json").read_text())
        self.assertEqual((rec["sent"][0]["read_back"], rec["sent"][0]["message_id"]), (False, "om_fixture0001"))

    def test_no_chat(self):
        self.reply()
        with mock.patch.dict(os.environ, {"FEISHU_HOME_CHANNEL": ""}):
            with self.assertRaises(R.ReportError) as cm:
                self.send_report(ended_doc())
        self.assertEqual(cm.exception.code, "NoChat")
        self.assertEqual(self.sent, [])


class ButtonTests(Base):
    def reported(self, *ds):
        self.send_report(ended_doc())
        return ds

    def test_rejects_other_chat_unknown_and_unreported(self):
        d = self.reply()
        with mock.patch("sys.stdout"):
            self.assertEqual(self.click("overturn", d["id"], chat="oc_someone_else")[0], 1)
        code, texts = self.click("overturn", d["id"])
        self.assertEqual(code, 1)
        self.assertIn("不在发过的汇报卡上", texts[0])
        code, texts = self.click("overturn", "d-261115-ffffff")
        self.assertIn("没有决定", texts[0])
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "open")

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
        for want in ("已推翻", "run r-1", "PR #50（run 开的）", "分支 agent/dts-9", "分支 agent/stack", "PR #51（以 run 的提交为祖先）",
                     f"决定 {later['id']}", "撤回 " + d["id"]):
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

    def test_overturn_merge_opens_revert_and_card(self):
        d = self.merge()
        follow = self.merge(pr=39)
        self.reported(d)
        gh = FakeGitHub(pulls=[{"number": 39, "merged_at": "2026-11-14T16:00:00Z", "merge_commit_sha": "m39", "html_url": "u39"},
                               {"number": 30, "merged_at": "2026-11-14T10:00:00Z", "merge_commit_sha": "m30", "html_url": "u30"}],
                        ancestors={("m" * 40, "m39")},
                        refs={38: [{"number": 60, "url": "u60", "title": "t", "is_pr": False, "at": "2026-11-14T16:10:00Z"},
                                   {"number": 20, "url": "u20", "title": "t", "is_pr": True, "at": "2026-11-14T09:00:00Z"}]})
        calls = []

        def revert(repo, mc, decision, title):
            calls.append((repo, mc, decision, title))
            return {"status": "opened", "repo": repo, "pr": 70, "url": f"https://github.com/{repo}/pull/70", "head": "r" * 40}
        code, texts = self.click("overturn", d["id"], gh=gh, revert=revert)
        self.assertEqual(code, 0, texts)
        self.assertEqual(calls, [(REPO, "m" * 40, d["id"], f"Revert：托管决定 {d['id']}（{REPO}#38@aaaaaaa）")])
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "reverted")
        for want in ("PR #39（之后合并", "issue #60 引用了 #38", "已开 revert PR"):
            self.assertIn(want, texts[0])
        self.assertNotIn("#30", texts[0])
        self.assertNotIn("#20", texts[0])
        self.assertEqual(buttons(texts[1]), [{"custody": "merge_revert", "ref": d["id"]}])
        del follow

    def test_overturn_merge_revert_fails(self):
        for rv in ({"status": "failed", "why": "git revert 有冲突"}, MA.Stop("钥匙串里没有令牌", "x")):
            with self.subTest(rv=str(rv)):
                d = self.merge(pr=40 + len(self.sent))
                self.send_report(ended_doc(), force=True)

                def revert(*a, rv=rv):
                    if isinstance(rv, Exception):
                        raise rv
                    return rv
                code, texts = self.click("overturn", d["id"], gh=FakeGitHub(), revert=revert)
                self.assertEqual(code, 1)
                self.assertEqual(L.get(self.st, d["id"])["disposition"], "revert_failed")
                self.assertIn("回退失败", texts[0])

    def test_overturn_unreconciled_merge_does_not_revert(self):
        d = self.merge(pr=41, status="unreconciled")
        self.reported(d)
        code, texts = self.click("overturn", d["id"], gh=FakeGitHub(), revert=lambda *a: self.fail("不该开 revert"))
        self.assertEqual(code, 1)
        self.assertEqual(L.get(self.st, d["id"])["disposition"], "revert_failed")
        self.assertIn("没有合并提交号", texts[0])

    def merged_revert(self, d):
        self.reported(d)
        self.click("overturn", d["id"], gh=FakeGitHub(),
                   revert=lambda repo, mc, dec, t: {"status": "opened", "repo": repo, "pr": 70, "url": "u70", "head": "r" * 40})

    def test_merge_revert_checks_then_merges_as_owner(self):
        d = self.merge()
        self.merged_revert(d)
        views = iter([{"state": "OPEN", "headRefOid": "r" * 40, "baseRefName": "main", "mergeStateStatus": "CLEAN", "isDraft": False},
                      {"state": "MERGED"}])
        merged = []
        code, texts = self.click("merge_revert", d["id"], run_gh=lambda args: json.dumps(next(views)),
                                 owner_merge=lambda card: merged.append(card) or {"returncode": 0})
        self.assertEqual(code, 0, texts)
        self.assertEqual(merged, [{"repo": REPO, "pr": 70, "head": "r" * 40}])
        self.assertIn("revert PR 已合并", texts[0])

    def test_merge_revert_refuses_when_pr_changed(self):
        d = self.merge()
        self.merged_revert(d)
        for view, why in (({"state": "OPEN", "headRefOid": "x" * 40, "baseRefName": "main", "mergeStateStatus": "CLEAN"}, "HEAD"),
                          ({"state": "CLOSED", "headRefOid": "r" * 40, "baseRefName": "main", "mergeStateStatus": "CLEAN"}, "不是开着的"),
                          ({"state": "OPEN", "headRefOid": "r" * 40, "baseRefName": "main", "mergeStateStatus": "BLOCKED"}, "BLOCKED")):
            code, texts = self.click("merge_revert", d["id"], run_gh=lambda args, v=view: json.dumps(v),
                                     owner_merge=lambda card: self.fail("不该合并"))
            self.assertEqual(code, 1)
            self.assertIn(why, texts[0])

    def test_merge_revert_without_revert_record(self):
        d = self.merge()
        self.reported(d)
        code, texts = self.click("merge_revert", d["id"], run_gh=lambda a: self.fail("x"), owner_merge=lambda c: self.fail("x"))
        self.assertEqual(code, 1)
        self.assertIn("没有开成的 revert PR", texts[0])


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
                v = pr_view or {"number": 70, "url": "https://github.com/x/pull/70", "headRefOid": head, "state": "OPEN", "baseRefName": "main"}
                return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(v), stderr="")
            if cmd[0] == MA.GIT:
                return real(cmd, **kw)
            raise AssertionError(cmd)
        return run

    def go(self, merge_commit=None, **kw):
        with mock.patch.object(MA, "GIT_URL", str(self.bare)), mock.patch.object(MA.subprocess, "run", self.run_fake(**kw)), \
                mock.patch.dict(os.environ, {"PATH": os.environ["PATH"], "HOME": "/Users/should-not-be-used"}, clear=True):
            return MA.run_custody_revert(REPO, merge_commit or self.merge_commit, "d-261115-e4f590", "Revert：托管决定")

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
