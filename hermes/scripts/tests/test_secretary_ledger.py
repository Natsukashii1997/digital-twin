"""隔离自测：秘书账本（secretary_ledger.py，#195 批 0B-4b）和它复用的 custody_ledger 通用函数。

库（DT_STATE_DB）、原文目录（DT_SECRETARY_DIR、DT_CUSTODY_DIR）放临时目录；秘书准入用桩 status_reader（真 secretary.read_status
的用法见 test_secretary 的 EndToEndTests）；名册打桩 secretary.dispatch_projects。并发预占用两个线程、两个连接抢同一个库。
不联网、不碰 ~/Library/Application Support/DigitalTwin、不碰 ~/.agent-autoloop。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_secretary_ledger.py -v
"""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
import custody as C  # noqa: E402
import custody_ledger as L  # noqa: E402
import secretary as S  # noqa: E402
import secretary_ledger as SL  # noqa: E402

TZ = C.TZ
T0 = dt.datetime(2026, 11, 18, 14, 0, tzinfo=TZ)
SID = "s-20261118-100000-0123456789ab"
SID2 = "s-20261119-100000-ba9876543210"
REPO = "Natsukashii1997/dt-sandbox"
ROSTER = {"dt-sandbox": REPO, "niuma-home": "Natsukashii1997/niuma-home"}
TASK = "t-20261118-abcdef"
TASK2 = "t-20261118-123456"
HEAD = "a" * 40
LIMITS = {"day": 10, "task": 3, "merge_day": 2, "redispatch": 1}
CATS = {"ask": True, "redispatch": True, "reject": True, "merge": True}
PSHA = "e" * 64                                   # 桩里的清单哈希
RECEIVED = {"card": 7, "question": "工人问：超时调多少？", "options": ["30 秒", "60 秒"]}
TEXT = dict(context="给示例服务加超时", question="超时设 30 秒还是 60 秒", summary="超时 30 秒", basis="只影响沙盒里的示例服务，和现有配置一致")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-secretary-ledger-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "twin-state.db"
        self.raw = self.root / "secretary"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DT_SECRETARY", "DT_CUSTODY", "DT_STATE_DB"))}
        env.update(DT_STATE_DB=str(self.db), DT_SECRETARY_DIR=str(self.raw), DT_CUSTODY_DIR=str(self.root / "custody"),
                   DT_SECRETARY_FILE=str(self.root / "secretary.json"), DT_SECRETARY_POLICY=str(self.root / "policy.md"),
                   DT_CUSTODY_FILE=str(self.root / "custody.json"), DT_CUSTODY_POLICY=str(self.root / "custody-policy.md"))
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.assertTrue(str(SL.raw_dir()).startswith(str(self.root)))
        p = mock.patch.object(S, "dispatch_projects", return_value=dict(ROSTER))
        p.start()
        self.addCleanup(p.stop)
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()
        self.addCleanup(self.st.close)
        self.lid = SL.register_session(self.st, SID, T0)
        SL.register_session(self.st, SID2, T0)
        self.limits, self.cats = dict(LIMITS), dict(CATS)
        self.state, self.session = "on", SID
        self.reader = self.status

    def status(self, now=None, eligible=None):
        """secretary.read_status 的形状（只取账本用到的字段）。"""
        ok = self.state == "on" if eligible is None else eligible
        return {"state": self.state,
                "session": {"id": self.session, "ledger": {"id": self.lid}, "policy": {"version": 3, "sha256": PSHA}} if self.session else None,
                "policy": {"ok": True, "version": 3, "sha256": PSHA, "categories": self.cats, "projects": ["dt-sandbox"], "repos": {"dt-sandbox": REPO},
                           "limits": self.limits, "whitelist": {REPO: "只有 CI"}},
                "admission": {"eligible": ok, "reasons": [] if ok else [f"秘书不是开着（{self.state}）"]}}

    def issue(self, action, now=T0, session=SID, **kw):
        kw.setdefault("status_reader", self.reader)
        target = kw.pop("target", None) or {
            "ask": {"card": 7, "task": TASK, "generation": 1}, "redispatch": {"task": TASK, "generation": 1},
            "reject": {"task": TASK, "repo": REPO, "pr": 7, "head": HEAD}, "merge": {"task": TASK, "repo": REPO, "pr": 7, "head": HEAD},
        }[kw.get("category") or action]
        text = dict(TEXT)
        if action == "handoff":
            text.update(summary="", ask_owner="回复我选哪个")
        text.update({k: kw.pop(k) for k in list(kw) if k in ("context", "question", "summary", "basis", "ask_owner")})
        rev = kw.pop("revocability", {"merge": "revertible", "handoff": "irrevocable"}.get(action, "correctable"))
        exp = kw.pop("expires_at", None if action == "handoff" else now + dt.timedelta(minutes=30))
        kw.setdefault("received", dict(RECEIVED))
        kw.setdefault("reply", None if action == "handoff" else {"choice": 1})
        if action == "ask":
            kw.setdefault("card_version", kw.pop("version", 1) if "version" in kw else self.next_version())
        return SL.issue(self.st, session_id=session, project=kw.pop("project", "dt-sandbox"), action=action, target=target,
                        revocability=rev, expires_at=exp, now=now, **text, **kw)

    def next_version(self):
        """① 默认每次用一个新的卡片版本（v6 起同卡同版本只许一个可送达的决定；唯一约束本身在 test_secretary_gate 测）。"""
        self._version = getattr(self, "_version", 0) + 1
        return self._version

    def rejects(self, code, fn, *a, **kw):
        with self.assertRaises(L.LedgerError) as cm:
            fn(*a, **kw)
        self.assertEqual(cm.exception.code, code, str(cm.exception))
        return str(cm.exception)

    def holds(self, did):
        return sorted((q["kind"], q["scope"], q["state"]) for q in SL.quotas(self.st, did))

    def counts(self):
        c = self.st.conn
        return (c.execute("SELECT COUNT(*) FROM secretary_decisions").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM secretary_quota").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM events WHERE object_type='secretary_decision'").fetchone()[0],
                sorted(p.name for p in self.raw.rglob("d-*.json")))


class IssueTests(Base):
    def test_ask_records_everything(self):
        d = self.issue("ask")
        self.assertRegex(d["id"], L.DECISION_ID)
        self.assertEqual((d["status"], d["action"], d["category"], d["task_id"], d["revocability"]),
                         ("issued", "ask", "ask", TASK, "correctable"))
        self.assertEqual(self.holds(d["id"]), [("day", "2026-11-18", "reserved"), ("task", TASK, "reserved")])
        self.assertEqual([e["kind"] for e in self.st.events(object_type="secretary_decision", object_id=d["id"])],
                         ["secretary.decision.issue"])
        raw = Path(d["raw_path"])
        self.assertEqual(raw.parent, self.raw / SID)
        self.assertEqual(oct(raw.stat().st_mode & 0o777), "0o600")
        self.assertEqual(json.loads(raw.read_text(encoding="utf-8"))["reply"], {"choice": 1})
        self.assertEqual((d["policy_version"], d["policy_sha256"]), (3, PSHA))
        ev = self.st.events(object_type="secretary_decision", object_id=d["id"])[0]
        payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])
        self.assertEqual((payload["policy_version"], payload["policy_sha256"]), (3, PSHA))

    def test_each_action_reserves_its_quota_kinds(self):
        r = self.issue("redispatch")
        self.assertEqual(self.holds(r["id"]), [("day", "2026-11-18", "reserved"), ("redispatch", TASK, "reserved"),
                                               ("task", TASK, "reserved")])
        m = self.issue("merge", target={"task": TASK2, "repo": REPO, "pr": 8, "head": HEAD})
        self.assertEqual(self.holds(m["id"]), [("day", "2026-11-18", "reserved"), ("merge_day", "2026-11-18", "reserved"),
                                               ("task", TASK2, "reserved")])
        j = self.issue("reject", target={"task": TASK2, "repo": REPO, "pr": 8, "head": HEAD})
        self.assertEqual(self.holds(j["id"]), [("day", "2026-11-18", "reserved"), ("task", TASK2, "reserved")])

    def test_day_quota(self):
        self.limits["day"] = 2
        self.issue("ask")
        self.issue("ask", target={"card": 8, "task": TASK2, "generation": 1})
        before = self.counts()
        msg = self.rejects("QuotaExhausted", self.issue, "reject", target={"task": "t-20261118-0000aa", "repo": REPO, "pr": 9, "head": HEAD})
        self.assertIn("2026-11-18 的代批总数已用完（2/2）", msg)
        self.assertEqual(self.counts(), before)
        self.issue("ask", now=T0 + dt.timedelta(hours=10))                  # 北京时间第二天 00:00 起重新算

    def test_day_boundary_is_shanghai(self):
        self.limits["day"] = 1
        self.issue("ask", now=dt.datetime(2026, 11, 18, 15, 59, tzinfo=dt.timezone.utc))      # 上海 11-18 23:59
        self.rejects("QuotaExhausted", self.issue, "ask", now=dt.datetime(2026, 11, 18, 15, 59, 30, tzinfo=dt.timezone.utc))
        self.issue("ask", now=dt.datetime(2026, 11, 18, 16, 0, tzinfo=dt.timezone.utc))        # 上海 11-19 00:00

    def test_task_quota_counts_across_sessions(self):
        self.limits["task"] = 2
        self.issue("ask")
        self.session = SID2
        self.issue("ask", session=SID2, target={"card": 8, "task": TASK, "generation": 1})
        self.assertIn(f"任务 {TASK} 的代批次数已用完（2/2）",
                      self.rejects("QuotaExhausted", self.issue, "ask", session=SID2, target={"card": 9, "task": TASK, "generation": 1}))
        self.issue("ask", session=SID2, target={"card": 9, "task": TASK2, "generation": 1})

    def test_merge_day_quota(self):
        self.limits["merge_day"] = 1
        self.issue("merge")
        self.assertIn("代批合并数已用完（1/1）",
                      self.rejects("QuotaExhausted", self.issue, "merge", target={"task": TASK2, "repo": REPO, "pr": 8, "head": HEAD}))
        self.issue("reject", target={"task": TASK2, "repo": REPO, "pr": 8, "head": HEAD})      # 别的类不受影响

    def test_redispatch_once_per_task(self):
        self.issue("redispatch")
        self.assertIn("放弃后重派已用完（1/1）", self.rejects("QuotaExhausted", self.issue, "redispatch"))
        self.issue("redispatch", target={"task": TASK2, "generation": 1})
        self.issue("ask")                                                      # 同任务别的类照常

    def test_void_releases_and_unknown_keeps_quota(self):
        self.limits["day"] = 1
        d = self.issue("ask")
        SL.void(self.st, d["id"], "测试作废", now=T0)
        self.assertEqual({q["state"] for q in SL.quotas(self.st, d["id"])}, {"released"})
        d2 = self.issue("ask")
        SL.begin(self.st, d2["id"], now=T0, status_reader=self.reader)
        SL.finish(self.st, d2["id"], "unknown", now=T0)
        self.assertEqual({q["state"] for q in SL.quotas(self.st, d2["id"])}, {"frozen"})
        self.rejects("QuotaExhausted", self.issue, "ask")                     # 结果不明的照样占着
        SL.reconcile(self.st, d2["id"], "failed", {"checked": "看了中台", "found": False}, now=T0)
        self.issue("ask")

    def test_concurrent_last_quota_only_one_wins(self):
        """两个线程、两个连接同时抢最后一个当天额度：BEGIN IMMEDIATE 让它们排队，只成一个。
        数额度后故意停 0.3 秒（拉长「数」和「写」之间的空档）：没有写锁时两个都会数到「还剩一个」。"""
        self.limits["day"] = 1
        real_used = L.used
        start = threading.Barrier(2, timeout=10)         # 有一个线程没到齐也不会永远等（改坏验证时出过卡死）
        out = []

        def slow_used(*a):
            n = real_used(*a)
            time.sleep(0.3)
            return n

        def worker(i):
            with L.TS.Store(self.db) as st:
                start.wait()
                try:
                    d = SL.issue(st, session_id=SID, project="dt-sandbox", action="ask", target={"card": 10 + i, "task": TASK, "generation": 1},
                                 revocability="correctable", expires_at=T0 + dt.timedelta(hours=1), now=T0, status_reader=self.reader,
                                 received=dict(RECEIVED), reply={"choice": 1}, card_version=1, **TEXT)
                    out.append(("ok", d["id"]))
                except L.LedgerError as e:
                    out.append((e.code, str(e)))
                except Exception as e:  # noqa: BLE001  库被锁、栅栏超时等：记下来让断言失败，不让线程悄悄死掉
                    out.append(("crash", repr(e)))

        with mock.patch.object(L, "used", side_effect=slow_used):
            ts = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(30)
        self.assertEqual(sorted(c for c, _ in out), ["QuotaExhausted", "ok"], out)
        self.assertEqual(self.st.conn.execute("SELECT COUNT(*) FROM secretary_quota WHERE kind='day'").fetchone()[0], 1)


class HandoffTests(Base):
    def test_handoff_takes_no_quota_and_needs_no_admission(self):
        self.limits["day"] = 1
        self.issue("ask")
        self.state = "draining"
        h = self.issue("handoff", category="ask", reason="gate", target={"card": 8, "task": TASK, "generation": 1})
        self.assertEqual((h["status"], h["category"], h["reason"], SL.quotas(self.st, h["id"])), ("handoff", "ask", "gate", []))
        self.state = "frozen"
        self.issue("handoff", category="merge", reason="d_level")

    def test_handoff_only_into_current_session(self):
        self.session = SID2
        self.assertIn("不是现在这次", self.rejects("WrongSession", self.issue, "handoff", category="ask", reason="unsure"))
        self.session = None
        self.rejects("WrongSession", self.issue, "handoff", category="ask", reason="unsure")
        self.session = SID
        self.lid = "L-" + "0" * 16
        self.rejects("LedgerMismatch", self.issue, "handoff", category="ask", reason="unsure")

    def test_handoff_needs_category_and_reason(self):
        self.rejects("BadInput", self.issue, "handoff", reason="unsure", target={"card": 8, "task": TASK, "generation": 1})
        self.rejects("BadInput", self.issue, "handoff", category="ask", reason="其他")
        self.rejects("BadInput", self.issue, "handoff", category="ask")
        self.rejects("BadInput", self.issue, "ask", reason="unsure")                    # 非交常浩不写原因
        self.rejects("BadInput", self.issue, "ask", category="merge")
        self.rejects("BadInput", self.issue, "handoff", category="merge", reason="unsure",
                     target={"card": 8, "task": TASK, "generation": 1})                # 形状要按 category
        self.assertEqual(len(SL.HANDOFF_REASONS), 9)


class GateTests(Base):
    def test_admission_failure_writes_nothing(self):
        before = self.counts()
        for state, session, code in (("draining", SID, "NotAdmitted"), ("frozen", SID, "NotAdmitted"), ("off", None, "NotAdmitted"),
                                     ("on", SID2, "WrongSession")):
            with self.subTest(state=state, session=session):
                self.state, self.session = state, session
                self.rejects(code, self.issue, "ask")
                self.assertEqual(self.counts(), before)
        self.state, self.session, self.lid = "on", SID, "L-" + "0" * 16
        self.rejects("LedgerMismatch", self.issue, "ask")
        self.assertEqual(self.counts(), before)

    def test_policy_gates(self):
        for cat in CATS:
            with self.subTest(cat):
                self.cats = {**CATS, cat: False}
                self.assertIn("代批清单没允许", self.rejects("NotAllowed", self.issue, cat))
        self.cats = dict(CATS)
        self.assertIn("第 2 节", self.rejects("NotAllowed", self.issue, "ask", project="niuma-home",
                                              target={"card": 7, "task": TASK, "generation": 1}))
        self.assertIn("第 2 节", self.rejects("NotAllowed", self.issue, "ask", project="ghost"))       # 不在清单
        orig = self.status

        def no_whitelist(now=None):
            st = orig(now)
            st["policy"]["whitelist"] = {}
            return st

        self.assertIn("白名单", self.rejects("NotAllowed", self.issue, "merge", status_reader=no_whitelist))
        self.issue("reject", status_reader=no_whitelist)                                 # 白名单只管合并
        self.limits = {"day": 10}
        self.rejects("NoLimits", self.issue, "ask")
        S.dispatch_projects.side_effect = RuntimeError("git 不在")
        self.rejects("RosterUnavailable", self.issue, "ask")

    def test_input_rejections(self):
        before = self.counts()
        cases = {
            "动作": dict(action="approve", target={"card": 7, "task": TASK, "generation": 1}),
            "ask 不可撤回": dict(action="ask", revocability="irrevocable"),
            "合并可纠正": dict(action="merge", revocability="correctable"),
            "退回可回退": dict(action="reject", revocability="revertible"),
            "会话号": dict(action="ask", session="c-20261118-100000-0123456789ab"),
            "任务号": dict(action="ask", target={"card": 7, "task": "DTS-9", "generation": 1}),
            "卡片 0": dict(action="ask", target={"card": 0, "task": TASK, "generation": 1}),
            "代次是布尔": dict(action="ask", target={"card": 7, "task": TASK, "generation": True}),
            "多字段": dict(action="ask", target={"card": 7, "task": TASK, "generation": 1, "choice": 2}),
            "少字段": dict(action="redispatch", target={"task": TASK}),
            "HEAD 短": dict(action="merge", target={"task": TASK, "repo": REPO, "pr": 7, "head": "abc"}),
            "HEAD 大写": dict(action="merge", target={"task": TASK, "repo": REPO, "pr": 7, "head": "A" * 40}),
            "仓库不对": dict(action="reject", target={"task": TASK, "repo": "someone/dt-sandbox", "pr": 7, "head": HEAD}),
            "PR 是字符串": dict(action="reject", target={"task": TASK, "repo": REPO, "pr": "7", "head": HEAD}),
            "没有效期": dict(action="ask", expires_at=None),
            "有效期没时区": dict(action="ask", expires_at=dt.datetime(2026, 11, 18, 15)),
            "有效期已过": dict(action="ask", expires_at=T0 - dt.timedelta(minutes=1)),
            "依据不是决定号": dict(action="ask", cites=["DTS-9"]),
            "依据不在账本": dict(action="ask", cites=["d-261118-abcdef"]),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                kw = dict(kw)
                action = kw.pop("action")
                with self.assertRaises(L.LedgerError) as cm:
                    self.issue(action, **kw)
                self.assertIn(cm.exception.code, ("BadInput",), str(cm.exception))
                self.assertEqual(self.counts(), before)

    def test_owner_text_rules(self):
        self.assertIn("内部术语", self.rejects("BadText", self.issue, "ask", basis="按 L1 规则选的"))
        self.rejects("BadText", self.issue, "ask", summary="")
        self.rejects("BadText", self.issue, "ask", question="")                         # ①要写 agent 问了什么
        self.rejects("BadText", self.issue, "merge", summary="")                        # ②③④同合并的必填规则
        for action in ("redispatch", "reject", "merge"):                               # 没有提问可写：不要求
            self.assertEqual(self.issue(action, question="")["status"], "issued")
        self.rejects("BadText", self.issue, "handoff", category="ask", reason="unsure", ask_owner="")
        self.rejects("BadText", self.issue, "ask", context="x" * 41)


class RosterAndRawTests(Base):
    """P1-02 / P1-03 / P1-04。"""

    def test_roster_drift_blocks_decisions_not_handoff(self):
        S.dispatch_projects.return_value = {}                                  # 项目被移出名册
        before = self.counts()
        self.assertIn("现在名册里是 没有", self.rejects("RosterChanged", self.issue, "ask"))
        S.dispatch_projects.return_value = {"dt-sandbox": "someone/dt-sandbox"}   # 改指向
        self.rejects("RosterChanged", self.issue, "merge")
        S.dispatch_projects.side_effect = S.RosterConflict("名册里短名冲突")
        self.rejects("RosterUnavailable", self.issue, "ask")
        self.assertEqual(self.counts(), before)
        h = self.issue("handoff", category="ask", reason="gate")                # 名册坏了照样能交常浩
        self.assertEqual((h["status"], SL.quotas(self.st, h["id"])), ("handoff", []))
        S.dispatch_projects.side_effect = None
        S.dispatch_projects.return_value = dict(ROSTER)

    def test_handoff_still_checks_format_session_and_ledger(self):
        S.dispatch_projects.side_effect = RuntimeError("git 不在")
        bad = {
            "任务号": {"card": 7, "task": "DTS-9", "generation": 1},
            "卡片 0": {"card": 0, "task": TASK, "generation": 1},
            "仓库名": {"task": TASK, "repo": "dt-sandbox", "pr": 7, "head": HEAD},
            "PR 号": {"task": TASK, "repo": REPO, "pr": "7", "head": HEAD},
            "head": {"task": TASK, "repo": REPO, "pr": 7, "head": "abc"},
        }
        before = self.counts()
        for name, target in bad.items():
            with self.subTest(name):
                cat = "ask" if "card" in target else "merge"
                self.rejects("BadInput", self.issue, "handoff", category=cat, reason="unsure", target=target)
        self.rejects("BadInput", self.issue, "handoff", category="ask", reason="unsure", project="dt sandbox!")
        self.rejects("BadInput", self.issue, "handoff", category="ask", reason="unsure", received=None)
        self.session = SID2
        self.rejects("WrongSession", self.issue, "handoff", category="ask", reason="unsure")
        self.session, self.lid = SID, "L-" + "0" * 16
        self.rejects("LedgerMismatch", self.issue, "handoff", category="ask", reason="unsure")
        self.assertEqual(self.counts(), before)

    def test_reject_repo_must_match_signed_mapping(self):
        self.assertIn("签署时", self.rejects("BadInput", self.issue, "reject",
                                            target={"task": TASK, "repo": "someone/dt-sandbox", "pr": 7, "head": HEAD}))

    def test_policy_version_recorded(self):
        d = self.issue("ask")
        self.assertEqual((d["policy_version"], d["policy_sha256"]), (3, PSHA))
        orig = self.status

        def bound_v2(now=None):                       # 交常浩取会话绑定的清单，不取当前有效清单
            st = orig(now)
            st["session"]["policy"] = {"version": 2, "sha256": "d" * 64}
            return st

        h = self.issue("handoff", category="ask", reason="unsure", status_reader=bound_v2)
        self.assertEqual((h["policy_version"], h["policy_sha256"]), (2, "d" * 64))

        def no_bound(now=None):
            st = orig(now)
            st["session"].pop("policy")
            return st

        self.rejects("WrongSession", self.issue, "handoff", category="ask", reason="unsure", status_reader=no_bound)

    def test_raw_text_required_per_action(self):
        before = self.counts()
        for action in ("ask", "redispatch", "reject", "merge"):
            for name, kw in {"received 没写": dict(received=None), "received 空": dict(received={}),
                             "received 是字符串": dict(received="工人问"), "reply 没写": dict(reply=None),
                             "reply 空": dict(reply={}), "reply 是字符串": dict(reply="选 1")}.items():
                with self.subTest(action=action, case=name):
                    self.assertIn("原文不合格", self.rejects("BadInput", self.issue, action, **kw))
        for reply in ({"answer": "30 秒"}, {"choice": 0}, {"choice": True}, {"choice": "1"}):
            with self.subTest(reply=reply):
                self.assertIn("选项号", self.rejects("BadInput", self.issue, "ask", reply=reply))
        self.rejects("BadInput", self.issue, "handoff", category="ask", reason="unsure", received={})
        self.rejects("BadInput", self.issue, "handoff", category="ask", reason="unsure", reply="不选")
        self.assertEqual(self.counts(), before)
        self.issue("handoff", category="ask", reason="unsure", reply={})        # 交常浩的 reply 可以不写或空
        self.issue("redispatch", reply={"redispatch": 1})                       # ②③④ 不要求选项号


class FlowTests(Base):
    def test_begin_rechecks_admission_and_expiry(self):
        d = self.issue("ask")
        self.state = "draining"
        self.rejects("NotAdmitted", SL.begin, self.st, d["id"], now=T0, status_reader=self.reader)
        self.assertEqual(SL.get(self.st, d["id"])["status"], "issued")
        self.state = "on"
        self.rejects("Expired", SL.begin, self.st, d["id"], now=T0 + dt.timedelta(hours=1), status_reader=self.reader)
        self.assertEqual((SL.get(self.st, d["id"])["status"], {q["state"] for q in SL.quotas(self.st, d["id"])}), ("void", {"released"}))

    def test_finish_and_reconcile_evidence(self):
        d = self.issue("ask")
        SL.begin(self.st, d["id"], now=T0, status_reader=self.reader)
        SL.finish(self.st, d["id"], "unknown", now=T0)
        self.rejects("BadEvidence", SL.reconcile, self.st, d["id"], "done", {"checked": "x", "found": True}, now=T0)
        self.rejects("BadEvidence", SL.reconcile, self.st, d["id"], "failed", {"checked": "x"}, now=T0)
        got = SL.reconcile(self.st, d["id"], "done", {"checked": "回读中台卡片", "found": True, "ref": "decided by=秘书 choice=1"}, now=T0)
        self.assertEqual((got["status"], {q["state"] for q in SL.quotas(self.st, d["id"])}), ("done", {"used"}))
        m = self.issue("merge", target={"task": TASK2, "repo": REPO, "pr": 8, "head": HEAD})
        SL.begin(self.st, m["id"], now=T0, status_reader=self.reader)
        self.rejects("BadInput", SL.finish, self.st, m["id"], "done", {}, now=T0)
        SL.finish(self.st, m["id"], "done", {"merge_commit": "b" * 40}, now=T0)
        kinds = [e["kind"] for e in self.st.events(object_type="secretary_decision", object_id=m["id"])]
        self.assertEqual(kinds, ["secretary.decision.issue", "secretary.decision.executing", "secretary.decision.done"])

    def test_disposition(self):
        d = self.issue("ask")
        self.rejects("InFlight", SL.set_disposition, self.st, d["id"], "overturned", "常浩推翻", now=T0)
        SL.begin(self.st, d["id"], now=T0, status_reader=self.reader)
        SL.finish(self.st, d["id"], "done", {"ref": "x"}, now=T0)
        self.assertIn("只有常浩能推翻秘书决定", self.rejects("NotOwner", SL.set_disposition, self.st, d["id"], "overturned", "x", by="hermes", now=T0))
        SL.set_disposition(self.st, d["id"], "overturned", "常浩推翻", now=T0)
        self.rejects("BadTransition", SL.set_disposition, self.st, d["id"], "reverted", "x", by="system", now=T0)
        self.assertEqual(SL.set_disposition(self.st, d["id"], "corrected", "重选了 2", by="system", now=T0)["disposition"], "corrected")

    def test_drain_voids_issued_and_reports_unsettled(self):
        a = self.issue("ask")
        b = self.issue("ask", target={"card": 8, "task": TASK2, "generation": 1})
        SL.begin(self.st, b["id"], now=T0, status_reader=self.reader)
        h = self.issue("handoff", category="ask", reason="unsure")
        blockers = SL.drain(self.st, SID, self.lid, T0)
        self.assertEqual(SL.get(self.st, a["id"])["status"], "void")
        self.assertEqual(SL.get(self.st, h["id"])["status"], "handoff")
        self.assertEqual(len(blockers), 1)
        self.assertIn(b["id"], blockers[0])
        self.assertIn("进行中", blockers[0])
        SL.finish(self.st, b["id"], "unknown", now=T0)
        self.assertIn("结果不明", SL.drain(self.st, SID, self.lid, T0)[0])
        SL.reconcile(self.st, b["id"], "unreconciled", {"checked": "中台读不到"}, now=T0)
        self.assertIn("UNRECONCILED", SL.drain(self.st, SID, self.lid, T0)[0])
        SL.reconcile(self.st, b["id"], "done", {"checked": "补看到了", "found": True, "ref": "x"}, now=T0)
        self.assertEqual(SL.drain(self.st, SID, self.lid, T0), [])
        orphan = self.raw / SID / "d-261118-ffffff.json"
        orphan.write_text("{}", encoding="utf-8")
        self.assertIn("有原文、账本里却没有", SL.drain(self.st, SID, self.lid, T0)[0])
        orphan.unlink()
        self.st.conn.execute("UPDATE secretary_quota SET state='reserved' WHERE decision_id=?", (a["id"],))
        self.assertIn("额度还是 reserved", SL.drain(self.st, SID, self.lid, T0)[0])
        self.rejects("LedgerMismatch", SL.drain, self.st, SID, "L-" + "0" * 16, T0)

    def test_stats(self):
        self.issue("ask")
        r = self.issue("redispatch")
        SL.begin(self.st, r["id"], now=T0, status_reader=self.reader)
        SL.finish(self.st, r["id"], "done", {"ref": "派单事件 42"}, now=T0)
        SL.set_disposition(self.st, r["id"], "overturned", "常浩推翻", now=T0)
        self.issue("handoff", category="ask", reason="induced")
        self.issue("handoff", category="merge", reason="gate")
        self.issue("handoff", category="ask", reason="induced", now=T0 + dt.timedelta(days=1))      # 别的日子
        s = SL.stats(self.st, "2026-11-18")
        self.assertEqual((s["total"], s["reasons"]), (4, {"induced": 1, "gate": 1}))
        self.assertEqual(s["categories"]["ask"], {"total": 2, "decided": 1, "delivered": 0, "handoff": 1,
                                                  "reasons": {"induced": 1}, "overturned": 0, "waited_seconds": 0, "waited_count": 0})
        self.assertEqual(s["categories"]["redispatch"], {"total": 1, "decided": 1, "delivered": 1, "handoff": 0, "reasons": {},
                                                         "overturned": 1, "waited_seconds": 0, "waited_count": 0})
        self.assertEqual(s["categories"]["merge"]["handoff"], 1)
        self.assertEqual(SL.stats(self.st, "2026-11-19")["total"], 1)
        self.rejects("BadInput", SL.stats, self.st, "11-18")


class SeparationTests(Base):
    def test_custody_and_secretary_ledgers_are_separate(self):
        cid = L.register_session(self.st, "c-20261118-100000-0123456789ab", T0)
        self.assertNotEqual(cid, self.lid)
        self.assertEqual((L.ledger_id(self.st), SL.ledger_id(self.st)), (cid, self.lid))
        self.rejects("LedgerMismatch", L.check_session, self.st, SID, self.lid)                 # 托管账本里没有秘书会话
        self.rejects("BadInput", SL.register_session, self.st, "c-20261118-100000-0123456789ab", T0)
        self.issue("ask")
        self.assertEqual(L.decisions(self.st), [])
        self.assertEqual(SL.raw_dir(), self.raw)
        self.assertNotEqual(L.raw_dir(), self.raw)

    def test_v4_db_migrates_to_v5_keeping_custody_rows(self):
        self.st.close()
        db = self.root / "old.db"
        conn = sqlite3.connect(str(db))
        for step in L.TS.MIGRATIONS[:4]:
            conn.executescript(step)
        conn.execute("INSERT INTO meta VALUES ('schema_version', '4')")
        conn.execute("INSERT INTO custody_sessions VALUES ('c-20261118-100000-0123456789ab', 'L-0000000000000001', '2026-11-18T10:00:00+08:00')")
        conn.commit()
        conn.close()
        with L.TS.Store(db, clock=lambda: T0) as s:
            self.assertEqual(s.version(), L.TS.SCHEMA_VERSION)          # v5 秘书账本之后还有 v6（同卡同版本唯一）
            self.assertEqual(L.TS.SCHEMA_VERSION, len(L.TS.MIGRATIONS))
            self.assertEqual(s.conn.execute("SELECT ledger_id FROM custody_sessions").fetchone()[0], "L-0000000000000001")
            self.assertEqual(SL.decisions(s), [])
            with self.assertRaises(sqlite3.IntegrityError):         # 表约束：动作、额度种类只认规定的
                s.conn.execute("INSERT INTO secretary_quota (session_id, kind, scope, decision_id, state, created_at, updated_at)"
                               " VALUES ('x', 'reply', '', 'd', 'reserved', 'x', 'x')")
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()

    def test_missing_db_is_not_created(self):
        self.st.close()
        os.environ["DT_STATE_DB"] = str(self.root / "nope.db")
        self.rejects("LedgerMissing", SL.open_store)
        self.assertFalse((self.root / "nope.db").exists())
        self.st = L.TS.Store(self.db, clock=lambda: T0).open()


class CliTests(Base):
    def run_cli(self, *args):
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "secretary_ledger.py"), *args], capture_output=True, text=True,
                           env=dict(os.environ), timeout=60)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1]), r.stdout

    def test_read_only_commands(self):
        d = self.issue("ask")
        SL.begin(self.st, d["id"], now=T0, status_reader=self.reader)
        code, out, _ = self.run_cli("show", d["id"])
        self.assertEqual((code, out["ok"]), (0, True))
        self.assertIn("答提问卡", out["msg"])
        self.assertEqual((out["policy_version"], out["policy_sha256"]), (3, PSHA))
        self.assertIn("依据清单 v3", out["msg"])
        code, out, text = self.run_cli("list", "--session", SID)
        self.assertEqual((code, out["msg"]), (0, "共 1 个决定"))
        self.assertIn(f"清单 v3（{PSHA[:12]}）", text)
        code, out, _ = self.run_cli("drain-check", "--session", SID)
        self.assertEqual((code, len(out["blockers"])), (1, 1))
        code, out, _ = self.run_cli("stats", "--day", "2026-11-18")
        self.assertEqual((code, out["ok"]), (0, True))
        self.assertIn("① 提问卡 1", out["msg"])
        self.assertEqual(SL.get(self.st, d["id"])["status"], "executing")      # 只读：drain-check 不作废


if __name__ == "__main__":
    unittest.main()
