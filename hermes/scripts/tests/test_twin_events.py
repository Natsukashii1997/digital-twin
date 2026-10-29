"""隔离自测：统一事件接入协议（twin_events.py，B2.1 T02）。

临时库、固定时钟、注入的飞书回读结果（不联网）；命令行用假 lark-cli（fake_lark.py）和临时的 600 配置文件。
覆盖：信封校验、正文不进库、信封自带的身份结论不信、重复只应用一次、user 事件必须经核实、一条消息只换一次动作、
被拒绝的动作不消耗消息、迟到与回退不覆盖已确认事实、order.sync 用内容哈希键（同秒连写不漏）、没有 order.json 的目录只计 ignored。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_events.py -v
"""
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


if __name__ == "__main__":
    unittest.main()
