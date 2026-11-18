"""隔离自测：统一事件接入协议（twin_events.py，B2.1 T02）。

临时库、固定时钟、注入的飞书回读结果（不联网）；命令行用假 lark-cli（fake_lark.py）和临时的 600 配置文件。
覆盖：信封校验、正文不进库、信封自带的身份结论不信、重复只应用一次、user 事件必须经核实、一条消息只换一次动作、
被拒绝的动作不消耗消息、迟到与回退不覆盖已确认事实、order.sync 用内容哈希键（同秒连写不漏）、没有 order.json 的目录只计 ignored。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_events.py -v
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
import twin_events as E  # noqa: E402
import twin_state as T  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
T0 = dt.datetime(2000, 1, 1, 9, 0, tzinfo=TZ)
OWNER = "ou_fixture_owner_0001"
CHAT = "oc_fixture_p2p_0001"
OK_RECEIPT = {"exit_code": 0, "timed_out": False, "worker_error": False, "session_id": "sess-1",
              "started": "2000-01-01T09:00:00", "ended": "2000-01-01T09:30:00",
              "branch": "hermes/D20000101-1", "head_before": "aaa", "head_after": "bbb",
              "committed_files": ["hermes/scripts/x.py"], "uncommitted": [], "guard_main_changed": False,
              "guard_remote_changed": False, "need_decision": None, "summary": "改好了。"}


def feishu(mid, sender=OWNER, chat=CHAT, when="2000-01-01 08:58"):
    return {"message_id": mid, "sender_open_id": sender, "sender_type": "user", "chat_id": chat, "chat_type": None,
            "create_time": when, "deleted": False}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-events-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "state" / "twin-state.db"
        self.ledger = self.root / "dispatch"
        self.t = T0
        self.store = T.Store(self.db, clock=lambda: self.t, dispatch_dir=self.ledger).open()
        self.addCleanup(self.store.close)
        self.messages = {}
        self.fetch_calls = 0

    def fetch(self, mid):
        self.fetch_calls += 1
        return self.messages.get(mid)

    def ingest(self, env):
        return E.ingest(self.store, env, fetch=self.fetch, now=self.t, owner=OWNER, owner_chat_id=CHAT)

    def user_env(self, typ, event_id, mid, **fields):
        self.messages.setdefault(mid, feishu(mid))
        return {"source": "user", "source_ref": f"feishu:{mid}", "event_id": event_id, "type": typ,
                "identity": {"role": "user", "method": "feishu_message", "message_id": mid}, **fields}

    @staticmethod
    def agent_env(typ, event_id, role="claude", **fields):
        return {"source": "agent", "source_ref": "worker", "event_id": event_id, "type": typ, "identity": {"role": role}, **fields}

    def running_task(self, order_no="D20000101-1"):
        g = self.store.create_goal("目标", by="user", status="authorized")
        t = self.store.create_task(g["id"], "任务", by="hermes", acceptance="能跑")
        self.store.set_ready(t["id"], by="hermes")
        r = self.store.start_run(t["id"], "claude", by="hermes", order_no=order_no)["run"]
        return self.store.get_task(t["id"]), r

    def uses(self):
        return [dict(r) for r in self.store.conn.execute("SELECT * FROM identity_uses")]

    def dump(self):
        return json.dumps(self.store.events(), ensure_ascii=False)

    def write_order(self, no, status, receipt=OK_RECEIPT, **extra):
        d = self.ledger / no
        (d / "out").mkdir(parents=True, exist_ok=True)
        o = {"no": no, "project": "digital-twin", "tool": "claude", "mode": "write", "status": status,
             "created": "2000-01-01T10:00:00", "updated": "2000-01-01T10:30:00", "cwd": "/wt", "exit_code": 0,
             "branch": f"hermes/{no}", "base": "aaa", **extra}
        (d / "order.json").write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding="utf-8")
        if receipt is not None:
            (d / "out" / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        return d


class NormalizeTests(Base):
    def bad(self, env, text):
        with self.assertRaisesRegex(E.EventError, text):
            E.normalize(env)

    def test_missing_or_mismatched_fields(self):
        self.bad({}, "source")
        self.bad({"source": "mail"}, "source")
        self.bad({"source": "agent", "type": "x"}, "不认识")
        self.bad({"source": "agent", "type": "note"}, "必须写明")
        self.bad({"source": "agent", "type": "note", "identity": {"role": "claude"}}, "event_id")
        self.bad({"source": "agent", "type": "note", "event_id": "e", "identity": {"role": "user"}}, "不能以 user")
        self.bad({"source": "user", "type": "note", "event_id": "e", "identity": {"role": "claude"}}, "只能是 user")
        self.bad({"source": "user", "type": "note", "event_id": "e"}, "message_id")
        self.bad({"source": "user", "type": "note", "event_id": "e", "identity": {"role": "user", "method": "prompt", "message_id": "om_x"}}, "feishu_message")
        self.bad({"source": "agent", "type": "task.block", "event_id": "e", "task_id": "T1", "identity": {"role": "claude"}, "payload": {"kind": "technical"}}, "缺字段")
        self.bad({"source": "agent", "type": "note", "event_id": "e", "identity": {"role": "claude"}, "occurred_at": 5}, "ISO")
        self.bad({"source": "agent", "type": "order.sync", "identity": {"role": "claude"}, "payload": {"order_dir": "/x"}}, "source=dispatch")
        self.bad("not an object", "JSON 对象")

    def test_non_object_identity_payload_refs_rejected_structurally(self):
        # P2（ChatGPT 评审 2026-10-29）：类型不对给 EventError，不漏 AttributeError / TypeError
        base = {"source": "agent", "type": "note", "event_id": "n", "payload": {"what": "x"}, "identity": {"role": "claude"}}
        for k, bad in (("identity", "claude"), ("identity", ["claude"]), ("payload", [1]), ("payload", "x"), ("refs", "pr"),
                       ("refs", {"kind": "x"}), ("refs", [1, "x"])):
            with self.assertRaisesRegex(E.EventError, k):
                E.normalize({**base, k: bad})
        self.assertEqual(E.normalize({**base, "refs": None})["refs"], [])
        self.assertEqual(E.normalize({**base, "payload": None})["payload"], {})

    def test_body_dropped_and_self_declared_identity_ignored(self):
        e = E.normalize({"source": "user", "type": "goal.authorize", "event_id": "e1", "goal_id": "G1", "text": "绝密内容",
                         "payload": {"content": "也是正文", "note": "留"},
                         "identity": {"role": "user", "method": "feishu_message", "message_id": "om_x", "verified": True,
                                      "open_id": OWNER, "sender": "常浩"}})
        dumped = json.dumps(e, ensure_ascii=False)
        self.assertNotIn("绝密内容", dumped)
        self.assertNotIn("也是正文", dumped)
        self.assertNotIn(OWNER, dumped)
        self.assertEqual(len(e["text_sha1"]), 16)
        self.assertEqual(e["identity"], {"role": "user", "method": "feishu_message", "message_id": "om_x"})
        self.assertEqual(e["payload"], {"note": "留"})
        self.assertEqual((e["idem_key"], e["object_type"], e["object_id"], e["db_source"]), ("evt:user:e1", "goal", "G1", "user"))

    def test_sources_map_and_defaults(self):
        self.assertEqual(E.normalize({"source": "external", "type": "note", "event_id": "gh-1"})["db_source"], "discovery")
        self.assertEqual(E.normalize({"source": "external", "type": "note", "event_id": "gh-1"})["identity"]["role"], "hermes")
        self.assertEqual(E.normalize({"source": "system", "type": "note", "event_id": "cron-1"})["identity"]["role"], "system")
        e = E.normalize({"source": "dispatch", "type": "order.sync", "payload": {"order_dir": "/x"}})
        self.assertEqual((e["idem_key"], e["identity"]["role"]), (None, "system"))


class IngestTests(Base):
    def test_verified_owner_message_applies_and_claims(self):
        out = self.ingest(self.user_env("goal.create", "e1", "om_msg000001", payload={"title": "新目标", "status": "authorized", "text": "请把这个做了"}))
        self.assertTrue(out["applied"], out)
        gid = out["result"]["id"]
        self.assertEqual(self.store.get_goal(gid)["status"], "authorized")
        self.assertEqual([(u["message_id"], u["purpose"]) for u in self.uses()], [("om_msg000001", "goal.create")])
        ev = [e for e in self.store.events(kind="in.goal.create") if e["idem_key"] == "evt:user:e1"][0]
        self.assertEqual(ev["payload"]["identity"]["verified"], {"method": "feishu_message", "age_s": 120, "chat": "p2p"})
        self.assertEqual(len(ev["payload"]["text_sha1"]), 16)
        for secret in (OWNER, CHAT, "请把这个做了"):
            self.assertNotIn(secret, self.dump())

    def test_unverified_or_foreign_message_rejected_and_recorded(self):
        self.messages["om_bad000001"] = feishu("om_bad000001", sender="ou_someone_else")
        out = self.ingest(self.user_env("goal.create", "e2", "om_bad000001", payload={"title": "x", "status": "authorized"}))
        self.assertFalse(out["applied"])
        self.assertIn("不是常浩本人", out["rejected"])
        self.messages["om_group00001"] = feishu("om_group00001", chat="oc_group")
        self.assertIn("私聊", self.ingest(self.user_env("goal.create", "e3", "om_group00001", payload={"title": "x"}))["rejected"])
        out = E.ingest(self.store, {"source": "user", "event_id": "e4", "type": "goal.create", "payload": {"title": "x"},
                                    "identity": {"role": "user", "method": "feishu_message", "message_id": "om_none000001"}},
                       fetch=self.fetch, now=self.t, owner=OWNER, owner_chat_id=CHAT)
        self.assertIn("查不到", out["rejected"])
        self.assertEqual(self.store.list_goals(), [])
        self.assertEqual(self.uses(), [])
        rejected = [e for e in self.store.events() if not e["applied"]]
        self.assertEqual(len(rejected), 3)
        self.assertNotIn("ou_someone_else", self.dump())

    def test_one_message_one_action(self):
        first = self.ingest(self.user_env("goal.create", "e1", "om_msg000001", payload={"title": "目标", "status": "authorized"}))
        gid = first["result"]["id"]
        again = self.ingest(self.user_env("task.create", "e2", "om_msg000001", goal_id=gid, payload={"title": "任务"}))
        self.assertFalse(again["applied"])
        self.assertIn("已经用过", again["rejected"])
        self.assertEqual(self.store.list_tasks(), [])
        calls = self.fetch_calls
        dup = self.ingest(self.user_env("goal.create", "e1", "om_msg000001", payload={"title": "目标", "status": "authorized"}))
        self.assertEqual((dup["applied"], dup["first_applied"]), (False, True))
        self.assertEqual(self.fetch_calls, calls)              # 重复不再回读飞书
        self.assertEqual(self.store.events(kind="in.goal.create")[0]["dup_count"], 1)
        self.assertEqual(len(self.store.list_goals()), 1)

    def test_rejected_action_does_not_consume_message(self):
        t, r = self.running_task()
        out = self.ingest(self.user_env("task.unblock", "e1", "om_msg000005", task_id=t["id"]))
        self.assertFalse(out["applied"])
        self.assertEqual(self.uses(), [])
        self.assertEqual(self.store.get_task(t["id"])["status"], "running")
        ok = self.ingest(self.user_env("goal.create", "e2", "om_msg000005", payload={"title": "再来", "status": "authorized"}))
        self.assertTrue(ok["applied"])
        self.assertEqual(len(self.uses()), 1)

    def test_duplicate_agent_receipt_applies_once(self):
        t, r = self.running_task()
        env = self.agent_env("run.receipt", "receipt-1", run_id=r["id"], payload={"receipt": OK_RECEIPT},
                             refs=[{"kind": "github_pr", "ref": "26", "url": "https://example/26"}])
        self.assertTrue(self.ingest(env)["applied"])
        self.assertEqual(self.store.get_task(t["id"])["status"], "review")
        self.assertEqual(self.ingest(env)["applied"], False)
        self.assertEqual(self.store.events(kind="in.run.receipt")[0]["dup_count"], 1)
        self.assertEqual(len(self.store.events(kind="run.done")), 1)
        self.assertEqual([x["ref"] for x in self.store.refs("run", r["id"])], ["26"])
        self.assertEqual(self.fetch_calls, 0)                   # 非 user 事件不碰飞书

    def test_late_receipt_of_old_run_only_records(self):
        t, a = self.running_task(order_no=None)          # 没有工单号、也查不到台账的 Run 才按时间判过期
        self.t += dt.timedelta(hours=3)
        self.store.recover()                                     # 没有台账的 Run 过期 → stale，任务 blocked(technical)
        self.assertEqual(self.store.get_run(a["id"])["status"], "stale")
        self.store.retry_task(t["id"], by="hermes")
        b = self.store.start_run(t["id"], "codex", by="hermes", order_no="D20000101-2")["run"]
        out = self.ingest(self.agent_env("run.receipt", "late-a", run_id=a["id"], payload={"receipt": OK_RECEIPT}))
        self.assertTrue(out["applied"])
        task = self.store.get_task(t["id"])
        self.assertEqual((task["status"], task["current_run_id"]), ("running", b["id"]))
        self.assertEqual(len(self.store.events(object_id=a["id"], kind="run.late_result")), 1)

    def test_ledger_regression_is_conflict_not_applied(self):
        d = self.write_order("D20000101-3", "cleaned", merged_to="main")
        env = {"source": "dispatch", "type": "order.sync", "payload": {"order_dir": str(d)}}
        self.assertEqual(self.ingest(env)["created"], 1)
        self.assertEqual(self.ingest(env)["unchanged"], 1)       # 同一份内容只处理一次
        self.write_order("D20000101-3", "running", receipt={**OK_RECEIPT, "summary": "别的"})
        out = self.ingest(env)
        self.assertEqual(out["conflicts"], ["D20000101-3"])
        run = self.store.get_run("D20000101-3")
        self.assertEqual((run["status"], run["receipt"]["summary"], run["evidence"]["merged_to"]), ("cleaned", "改好了。", "main"))

    def test_same_second_double_write_is_not_lost(self):
        self.write_order("D20000101-4", "merged", pr=9, pr_url="https://example/9")
        self.assertEqual(self.store.sync_orders()["created"], 1)
        self.write_order("D20000101-4", "merged", pr=9, pr_url="https://example/9", merged_to="main")   # updated 一个字没变
        st = self.store.sync_orders()
        self.assertEqual((st["updated"], st["unchanged"]), (1, 0))
        self.assertEqual(self.store.get_run("D20000101-4")["evidence"]["merged_to"], "main")
        self.assertEqual(self.store.sync_orders()["unchanged"], 1)

    def test_dirs_without_order_json_are_counted_not_imported(self):
        self.write_order("D20000101-5", "done")
        (self.ledger / "D20000101-9").mkdir()
        (self.ledger / "D20000101-9" / "task.md").write_text("早期痕迹", encoding="utf-8")
        st = self.store.sync_orders()
        self.assertEqual((st["seen"], st["created"], st["ignored"], st["skipped"]), (1, 1, 1, []))
        rec = self.store.reconcile()
        self.assertEqual((rec["ignored_dirs"], rec["missed"], rec["unchanged"]), (["D20000101-9"], [], 1))
        self.assertEqual(len(self.store.list_runs()), 1)

    def test_reconcile_recovers_missed_orders(self):
        self.write_order("D20000101-6", "running", receipt=None)
        self.write_order("D20000101-7", "done")
        rec = self.store.reconcile()
        self.assertEqual((rec["missed"], rec["created"]), (["D20000101-6", "D20000101-7"], 2))
        self.write_order("D20000101-6", "done")
        rec = self.store.reconcile()
        self.assertEqual((rec["missed"], rec["updated"], rec["unchanged"]), (["D20000101-6"], 1, 1))
        self.assertEqual(self.store.get_run("D20000101-6")["status"], "done")

    def test_note_only_records(self):
        out = self.ingest(self.agent_env("note", "n1", payload={"what": "看到 PR 评论"}, task_id="T20000101-1"))
        self.assertTrue(out["applied"])
        self.assertIsNone(out["result"])
        self.assertEqual(self.store.events(kind="in.note")[0]["object_id"], "T20000101-1")
        self.assertEqual(self.store.summary()["tasks"], {})

    def test_store_rules_still_apply_through_envelopes(self):
        g = self.store.create_goal("目标", by="hermes")
        out = self.ingest(self.agent_env("goal.authorize", "a1", role="hermes", goal_id=g["id"]))
        self.assertFalse(out["applied"])
        self.assertIn("常浩", out["rejected"])
        self.assertEqual(self.store.get_goal(g["id"])["status"], "observed")
        ok = self.ingest(self.user_env("goal.authorize", "a2", "om_msg000009", goal_id=g["id"]))
        self.assertTrue(ok["applied"])
        self.assertEqual(self.store.get_goal(g["id"])["status"], "authorized")


class RetryPolicyTests(Base):
    """P1-2（ChatGPT 评审 2026-10-29）：临时失败（回读失败 / 出错、本机配置缺失）不记事件、同一 event_id 可重试；
    永久拒绝（不是本人、群聊、过时、重放、Store 拒绝）记 applied=0，同一 event_id 再来返回 duplicate_of + rejected_before，
    不重新核实、不执行动作，要重新授权必须是新的消息和新的 event_id。"""

    def rejected_events(self):
        return [e for e in self.store.events() if not e["applied"]]

    def test_missing_local_config_is_retryable_then_same_event_applies(self):
        env = self.user_env("goal.create", "r1", "om_retry00001", payload={"title": "x", "status": "authorized"})
        out = E.ingest(self.store, env, fetch=self.fetch, now=self.t, owner=OWNER, owner_chat_id="")    # 还没配 owner_chat_id
        self.assertEqual((out["ok"], out["applied"], out["retryable"], out["recorded"]), (False, False, True, False))
        self.assertIn("owner_chat_id", out["reason"])
        self.assertEqual((self.store.events(), self.uses(), self.store.list_goals()), ([], [], []))
        out = E.ingest(self.store, env, fetch=self.fetch, now=self.t, owner="", owner_chat_id=CHAT)      # 还没配 owner_open_id
        self.assertEqual((out["retryable"], out["recorded"]), (True, False))
        self.assertEqual(self.store.events(), [])
        out = self.ingest(env)                                                                            # 配好了：同一 event_id 重试成功
        self.assertTrue(out["applied"], out)
        self.assertEqual(len(self.store.list_goals()), 1)
        self.assertEqual(len(self.uses()), 1)

    def test_fetch_failure_is_retryable_then_same_event_applies(self):
        env = self.user_env("goal.create", "r2", "om_retry00002", payload={"title": "x", "status": "authorized"})

        def down(_mid):
            raise E.I.IdentityError("回读失败：timeout")

        def crash(_mid):
            raise RuntimeError("socket")

        for fetch in (down, crash):
            out = E.ingest(self.store, env, fetch=fetch, now=self.t, owner=OWNER, owner_chat_id=CHAT)
            self.assertEqual((out["applied"], out["retryable"], out["recorded"]), (False, True, False), out)
            self.assertIn("回读飞书", out["reason"])
        self.assertEqual((self.store.events(), self.uses()), ([], []))
        self.assertTrue(self.ingest(env)["applied"])
        self.assertEqual(len(self.store.list_goals()), 1)

    def test_permanent_rejection_locks_event_id_even_after_fix(self):
        self.messages["om_bad000002"] = feishu("om_bad000002", sender="ou_someone_else")
        env = self.user_env("goal.create", "p1", "om_bad000002", payload={"title": "x", "status": "authorized"})
        out = self.ingest(env)
        self.assertEqual((out["applied"], out.get("retryable", False)), (False, False))
        self.assertIn("不是常浩本人", out["rejected"])
        self.assertEqual(len(self.rejected_events()), 1)
        self.messages["om_bad000002"] = feishu("om_bad000002")          # 哪怕现在回读变成本人
        calls = self.fetch_calls
        again = self.ingest(env)
        self.assertEqual((again["applied"], again["rejected_before"], again["duplicate_of"]), (False, True, out["event_id"]))
        self.assertIn("新", again["hint"])                                # 要新的消息和新的 event_id
        self.assertEqual(self.fetch_calls, calls)                          # 不再回读
        self.assertEqual((self.store.list_goals(), self.uses()), ([], []))
        self.assertEqual(self.rejected_events()[0]["dup_count"], 1)
        # 新消息 + 新 event_id 才能重新授权
        ok = self.ingest(self.user_env("goal.create", "p2", "om_good00002", payload={"title": "x", "status": "authorized"}))
        self.assertTrue(ok["applied"])

    def test_store_rejection_is_permanent_too(self):
        t, r = self.running_task()
        env = self.user_env("task.unblock", "p3", "om_msg000009", task_id=t["id"])
        out = self.ingest(env)
        self.assertFalse(out["applied"])
        self.assertNotIn("retryable", out)
        again = self.ingest(env)
        self.assertEqual((again["applied"], again["rejected_before"]), (False, True))
        dup_ok = self.ingest(self.user_env("goal.create", "p4", "om_msg000010", payload={"title": "x", "status": "authorized"}))
        twice = self.ingest(self.user_env("goal.create", "p4", "om_msg000010", payload={"title": "x", "status": "authorized"}))
        self.assertEqual((dup_ok["applied"], twice["applied"], twice["first_applied"]), (True, False, True))
        self.assertNotIn("rejected_before", twice)                         # 成功事件的重复不是“先前被拒”


class OrderDirBoundaryTests(Base):
    """P1-3（ChatGPT 评审 2026-10-29）：order.sync 的 order_dir 必须是派单根目录的直接子目录（实路径）、本身不是符号链接、
    目录名等于 order.json 的 no；source="dispatch" 字符串本身不算来源证明。"""

    def order_env(self, d):
        return {"source": "dispatch", "type": "order.sync", "payload": {"order_dir": str(d)}}

    def runs(self):
        return [r[0] for r in self.store.conn.execute("SELECT order_no FROM runs ORDER BY order_no")]

    def test_outside_root_rejected(self):
        outside = self.root / "elsewhere" / "D20000101-9"
        (outside / "out").mkdir(parents=True)
        (outside / "order.json").write_text(json.dumps({"no": "D20000101-9", "status": "done"}), encoding="utf-8")
        with self.assertRaisesRegex(T.NotAuthorized, "派单根目录"):
            self.ingest(self.order_env(outside))
        with self.assertRaisesRegex(T.NotAuthorized, "派单根目录"):
            self.store.sync_order(outside)
        self.assertEqual((self.runs(), self.store.events()), ([], []))

    def test_symlink_rejected(self):
        self.ledger.mkdir(parents=True, exist_ok=True)
        real = self.root / "elsewhere" / "D20000101-8"
        (real / "out").mkdir(parents=True)
        (real / "order.json").write_text(json.dumps({"no": "D20000101-8", "status": "done"}), encoding="utf-8")
        (self.ledger / "D20000101-8").symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(T.NotAuthorized, "符号链接"):
            self.ingest(self.order_env(self.ledger / "D20000101-8"))
        legit = self.write_order("D20000101-7", "done")
        (self.ledger / "D20000101-6").symlink_to(legit, target_is_directory=True)       # 根目录内的链接也不认
        with self.assertRaisesRegex(T.NotAuthorized, "符号链接"):
            self.ingest(self.order_env(self.ledger / "D20000101-6"))
        self.assertEqual(self.runs(), [])

    def test_dir_name_must_match_order_no(self):
        d = self.write_order("D20000101-5", "done")
        o = json.loads((d / "order.json").read_text(encoding="utf-8"))
        (d / "order.json").write_text(json.dumps({**o, "no": "D20000101-6"}), encoding="utf-8")
        with self.assertRaisesRegex(T.NotAuthorized, "单号"):
            self.ingest(self.order_env(d))
        self.assertEqual(self.runs(), [])
        with self.assertRaisesRegex(T.NotAuthorized, "派单根目录"):                         # 根目录本身也不是工单目录
            self.ingest(self.order_env(self.ledger))
        with self.assertRaisesRegex(T.NotAuthorized, "派单根目录"):                         # 子目录再往下也不行
            self.ingest(self.order_env(d / "out"))

    def test_legit_dir_and_notify_order_still_work(self):
        d = self.write_order("D20000101-4", "done")
        self.assertEqual(self.ingest(self.order_env(d))["created"], 1)
        self.assertEqual(self.ingest(self.order_env(str(d) + "/"))["unchanged"], 1)       # 写法差异不影响
        self.store.close()
        self.write_order("D20000101-3", "done")
        out = T.notify_order(self.ledger / "D20000101-3", db=self.db, dispatch_dir=self.ledger)   # dispatch.py 的内部通知传自己的 ROOT
        self.assertEqual(out["created"], 1)
        missing = T.notify_order(self.ledger / "D20000101-2", db=self.db, dispatch_dir=self.ledger)
        self.assertEqual((missing["created"], missing["ignored"]), (0, 1))


class CliTests(Base):
    def setUp(self):
        super().setUp()
        self.store.close()
        self.fake = self.root / "fake"
        self.fake.mkdir()
        recent = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        raw = {"chat_id": CHAT, "content": "{\"text\":\"授权\"}", "create_time": recent, "deleted": False,
               "message_id": "om_fixturecli0001", "msg_type": "text",
               "sender": {"id": OWNER, "id_type": "open_id", "name": "虚构", "sender_type": "user"}, "updated": False}
        (self.fake / "state.json").write_text(json.dumps({"mget": {"om_fixturecli0001": raw}}), encoding="utf-8")
        owner, chat = self.root / "owner", self.root / "chat"
        owner.write_text(OWNER + "\n", encoding="utf-8")
        owner.chmod(0o600)
        chat.write_text(CHAT + "\n", encoding="utf-8")
        chat.chmod(0o600)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("DT_", "HERMES_SESSION"))}
        self.env.update(DT_LARK_CLI=str(HERE / "fake_lark.py"), FAKE_LARK_DIR=str(self.fake), DT_OWNER_FILE=str(owner),
                        DT_OWNER_CHAT_FILE=str(chat), DT_STATE_DB=str(self.db), DT_DISPATCH=str(self.ledger))

    def cli(self, script, *args, ok=True):
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / script), *args], capture_output=True, text=True, env=self.env)
        self.assertEqual(r.returncode, 0 if ok else 1, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["ok"], ok, out)
        for secret in (OWNER, CHAT):
            self.assertNotIn(secret, r.stdout + r.stderr)
        return out

    def test_ingest_validate_types_roundtrip(self):
        self.cli("twin_state.py", "init")
        env = {"source": "user", "source_ref": "feishu:om_fixturecli0001", "event_id": "om_fixturecli0001", "type": "goal.create",
               "payload": {"title": "命令行目标", "status": "authorized", "text": "授权"},
               "identity": {"role": "user", "method": "feishu_message", "message_id": "om_fixturecli0001"}}
        f = self.root / "env.json"
        f.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.cli("twin_events.py", "validate", "--file", str(f))["envelope"]["idem_key"], "evt:user:om_fixturecli0001")
        out = self.cli("twin_events.py", "ingest", "--file", str(f))
        self.assertTrue(out["applied"])
        gid = out["result"]["id"]
        self.assertEqual(self.cli("twin_events.py", "ingest", "--file", str(f))["duplicate_of"], out["event_id"])
        env2 = {**env, "event_id": "second", "type": "task.create", "goal_id": gid, "payload": {"title": "任务"}}
        f.write_text(json.dumps(env2, ensure_ascii=False), encoding="utf-8")
        self.assertIn("已经用过", self.cli("twin_events.py", "ingest", "--file", str(f), ok=False)["msg"])
        bad = {**env, "identity": {"role": "user"}}
        f.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        self.assertIn("message_id", self.cli("twin_events.py", "validate", "--file", str(f), ok=False)["msg"])
        self.assertIn("goal.authorize", self.cli("twin_events.py", "types")["types"])
        with T.Store(self.db) as s:
            self.assertEqual(s.get_goal(gid)["status"], "authorized")
            self.assertEqual(s.list_tasks(), [])

    def test_cli_distinguishes_retryable_rejected_and_needs_new_message(self):
        # P1-2：命令行三种文案——暂时失败可重试 / 已留痕被拒绝 / 先前被拒需要新消息
        self.cli("twin_state.py", "init")
        state = json.loads((self.fake / "state.json").read_text(encoding="utf-8"))
        state["mget"]["om_fixturecli0002"] = {**state["mget"]["om_fixturecli0001"], "message_id": "om_fixturecli0002",
                                              "sender": {"id": "ou_other_0009", "id_type": "open_id", "sender_type": "user"}}
        (self.fake / "state.json").write_text(json.dumps(state), encoding="utf-8")
        env = {"source": "user", "source_ref": "feishu:om_fixturecli0001", "event_id": "cli-r1", "type": "goal.create",
               "payload": {"title": "命令行目标", "status": "authorized"},
               "identity": {"role": "user", "method": "feishu_message", "message_id": "om_fixturecli0001"}}
        f = self.root / "env.json"
        f.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
        chat_file = self.env["DT_OWNER_CHAT_FILE"]
        self.env["DT_OWNER_CHAT_FILE"] = str(self.root / "missing")                         # 本机配置缺失 → 临时失败
        out = self.cli("twin_events.py", "ingest", "--file", str(f), ok=False)
        self.assertEqual((out["retryable"], out["recorded"]), (True, False))
        self.assertIn("可重试", out["msg"])
        self.env["DT_OWNER_CHAT_FILE"] = chat_file
        self.assertTrue(self.cli("twin_events.py", "ingest", "--file", str(f))["applied"])    # 同一 event_id 重试成功
        bad = {**env, "event_id": "cli-p1", "identity": {**env["identity"], "message_id": "om_fixturecli0002"}}
        f.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        out = self.cli("twin_events.py", "ingest", "--file", str(f), ok=False)
        self.assertIn("已留痕", out["msg"])
        self.assertIn("不能再用", out["msg"])
        out = self.cli("twin_events.py", "ingest", "--file", str(f), ok=False)
        self.assertEqual(out["rejected_before"], True)
        self.assertIn("新的消息", out["msg"])
        with T.Store(self.db) as s:
            self.assertEqual(len(s.list_goals()), 1)


if __name__ == "__main__":
    unittest.main()
