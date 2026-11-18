"""秘书代批控制面（secretary_gate.py，#195 批 0B-4c 第 1 小批）自测。

单元部分：中台库用临时目录里的同名表（cards / runs / run_events / events，列和中台视图一样），中台 HTTP 换成假的；
凭证库、账本、cards.db、受信发布记录都在临时目录；秘书准入、托管准入、名册打桩。
端到端部分：临时目录里起真中台（同 test_autoloop_hub_e2e 的 HubFixture，本机没有命令行时跳过），装配置、签三把钥匙，
网关发派单事件 → 工人（假接单命令）提问 → 飞书桥接和秘书各领一份 → 秘书代答 / 本人点击 / 伪造 / 强杀对账。
隔离：开始和结束各算一次正式文件的 sha256（twin-state.db、DigitalTwin/autoloop/ 下全部文件、~/.agent-autoloop 的
rules.json、config.json、hub.db），必须一致。不联网、不发飞书。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_secretary_gate.py -v
"""
import contextlib
import datetime as dt
import hashlib
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
sys.path.insert(0, str(HERE))
import autoloop_answer as A  # noqa: E402
import autoloop_config as AC  # noqa: E402
import autoloop_feishu as F  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_publish as P  # noqa: E402
import custody as CU  # noqa: E402
import custody_ledger as L  # noqa: E402
import secretary as S  # noqa: E402
import secretary_gate as SG  # noqa: E402
import secretary_ledger as SL  # noqa: E402
from test_autoloop_hub_e2e import CLI, OWNER, SKIP, HubFixture  # noqa: E402

TZ = CU.TZ
T = dt.datetime(2026, 11, 18, 14, 0, tzinfo=TZ)        # 过去的时刻：对账判「在途过久」按真实时钟
SID = "s-20261119-100000-0123456789ab"
REPO = "Natsukashii1997/dt-sandbox"
TASK = "t-20261119-abcdef"
RULE = "dispatch-dt-sandbox-claude"
HEAD = "b" * 40
PSHA = "e" * 64
CHAT = "oc_fixture_home"
TEXT = {"context": "给示例服务加超时", "question": "超时设 30 秒还是 60 秒", "summary": "超时 30 秒",
        "basis": "只影响沙盒里的示例服务，和现有配置一致"}

# ---------- 正式文件：开始、结束各算一次 sha256 ----------
SUPPORT = Path("~/Library/Application Support/DigitalTwin").expanduser()
HUB_HOME = Path("~/.agent-autoloop").expanduser()


def real_files():
    files = [SUPPORT / "twin-state.db", HUB_HOME / "rules.json", HUB_HOME / "config.json", HUB_HOME / "hub.db"]
    if (SUPPORT / "autoloop").is_dir():
        files += sorted(p for p in (SUPPORT / "autoloop").rglob("*") if p.is_file())
    out = {}
    for f in files:
        try:
            out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            out[str(f)] = None
    return out


BEFORE = {}


def setUpModule():
    BEFORE.update(real_files())


def tearDownModule():
    after = real_files()
    changed = [k for k in set(BEFORE) | set(after) if BEFORE.get(k) != after.get(k)]
    if changed:
        raise AssertionError(f"测试改动了正式文件：{changed}")


# ---------- 假中台库 ----------
HUB_SCHEMA = """
CREATE TABLE cards (id INTEGER, key TEXT, kind TEXT, state TEXT, version INTEGER, title TEXT, text TEXT, options TEXT,
  choice INTEGER, "by" TEXT, rule TEXT, lane TEXT, source TEXT, run_id INTEGER, created_at TEXT, decided_at TEXT, closed_at TEXT);
CREATE TABLE runs (id INTEGER, rule TEXT, agent TEXT, lane TEXT, state TEXT, created_at TEXT, summary TEXT, attempts INTEGER);
CREATE TABLE run_events (run_id INTEGER, event_id INTEGER);
CREATE TABLE events (id INTEGER, source TEXT, "key" TEXT, type TEXT, subject TEXT, detail TEXT);
"""


def utc(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-secretary-gate-")
        self.addCleanup(tmp.cleanup)
        self.root = root = Path(tmp.name)
        (root / "hub").mkdir()
        self.hubdb = root / "hub/hub.db"
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DT_", "AGENT_AUTOLOOP"))}
        env.update(DT_STATE_DB=str(root / "twin-state.db"), DT_SECRETARY_DIR=str(root / "secretary"),
                   DT_SECRETARY_FILE=str(root / "secretary.json"), DT_SECRETARY_POLICY=str(root / "policy.md"),
                   DT_CUSTODY_FILE=str(root / "custody.json"), DT_CUSTODY_POLICY=str(root / "custody-policy.md"),
                   DT_CUSTODY_DIR=str(root / "custody"), DT_AUTOLOOP_CARDS_DB=str(root / "dt/autoloop/cards.db"),
                   DT_AUTOLOOP_CHANNEL_KEY_DIR=str(root / "dt/autoloop"), AGENT_AUTOLOOP_HOME=str(root / "hub"),
                   DT_GRANTS_DB=str(root / "dt/autoloop/grants.db"), DT_GRANTS_STATUS=str(root / "shared/status.json"),
                   DT_GRANTS_TASKS=str(root / "shared/tasks"), DT_PUBLISH_LOG=str(root / "dt/autoloop/publish.jsonl"),
                   DT_PUBLISH_OUTBOX=str(root / "outbox"), DT_PUBLISH_MIRRORS=str(root / "mirrors"),
                   DT_HUB="http://127.0.0.1:9", DT_HERMES_SOURCE_KEY_FILE=str(root / "dt/autoloop/hermes-source.key"),
                   DT_OWNER_OPEN_ID=OWNER, DT_OWNER_CHAT_ID=CHAT, DT_AUTOLOOP_EVIDENCE=str(root / "evidence"),
                   DT_AUTOLOOP_LOCK=str(root / "dt/autoloop/config.lock"), DT_READONLY_TOKEN_FILE=str(root / "ro.token"))
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.assertTrue(str(L.db_path()).startswith(str(root)))
        with contextlib.closing(sqlite3.connect(self.hubdb)) as con, con:
            con.executescript(HUB_SCHEMA)
        st = L.TS.Store(L.db_path(), clock=lambda: T).open()
        self.lid = SL.register_session(st, SID, T)
        st.close()
        self.state, self.cats, self.custody_on = "on", {"ask": True, "redispatch": True, "reject": True, "merge": True}, False
        self.limits = {"day": 10, "task": 3, "merge_day": 2, "redispatch": 1}
        for target, kw in ((S, {"read_status": self.status, "dispatch_projects": lambda: {"dt-sandbox": REPO}}),
                           (CU, {"read_status": lambda now=None: {"admission": {"eligible": self.custody_on}}})):
            p = mock.patch.multiple(target, **kw)
            p.start()
            self.addCleanup(p.stop)
        self.calls, self.hub_error, self.hub_side = [], None, None
        p = mock.patch.object(F, "hub", self.fake_hub)
        p.start()
        self.addCleanup(p.stop)
        self.posted = []
        p = mock.patch.object(G, "post_event", self.fake_post)
        p.start()
        self.addCleanup(p.stop)
        self.make_grant()
        self.ask_card()
        self.feishu(7, T - dt.timedelta(minutes=40))
        self.leased(7)

    # ----- 桩 -----
    def status(self, now=None):
        ok = self.state == "on"
        return {"state": self.state, "error": None,
                "session": {"id": SID, "ledger": {"id": self.lid}, "policy": {"version": 3, "sha256": PSHA}},
                "policy": {"ok": True, "version": 3, "sha256": PSHA, "categories": self.cats, "projects": ["dt-sandbox"],
                           "repos": {"dt-sandbox": REPO}, "limits": self.limits, "whitelist": {}},
                "admission": {"eligible": ok, "reasons": [] if ok else [f"秘书不是开着（{self.state}）"]}}

    def fake_hub(self, method, path, body=None, timeout=20, channel="feishu"):
        self.calls.append((method, path, body, channel))
        if self.hub_side:
            self.hub_side(method, path, body)
        if self.hub_error:
            raise self.hub_error
        if path.endswith("/answer"):
            self.assertEqual((channel, body["by"]), ("secretary", "秘书"))
            cid = int(path.split("/")[3])
            with contextlib.closing(sqlite3.connect(self.hubdb)) as con, con:
                st = con.execute("SELECT state FROM cards WHERE id = ?", (cid,)).fetchone()[0]
                if st != "open":
                    return {"changed": False, "state": st}
                con.execute("UPDATE cards SET state = 'decided', choice = ?, \"by\" = ?, version = version + 1 WHERE id = ?",
                            (body["choice"], body["by"], cid))
            return {"changed": True, "state": "decided"}
        return {}

    def fake_post(self, body):
        self.posted.append(body)
        return 100 + len(self.posted)

    # ----- 数据 -----
    def make_grant(self, status="active", generation=1, event_gen=None, hub_event=11):
        with G.tx() as con:
            con.execute("DELETE FROM grants")
            con.execute("DELETE FROM hub_events")
            con.execute("INSERT INTO grants (task_id, repo, github, scope_text, scope_hash, executor, rule, actions, valid_until, "
                        "status, generation, generation_started_at, approval_message, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (TASK, "dt-sandbox", REPO, "五要素", G.sha256("五要素"), "claude", RULE, json.dumps(list(G.ACTIONS)),
                         G.iso(T + dt.timedelta(hours=12)), status, generation, G.iso(T - dt.timedelta(hours=2)), "om_x",
                         G.iso(T - dt.timedelta(hours=2)), G.iso(T - dt.timedelta(hours=2))))
            con.execute("INSERT INTO hub_events (event_key, task_id, rule, executor, generation, kind, hub_event, at, sent) "
                        "VALUES (?,?,?,?,?,?,?,?,1)", (f"{TASK}:g1:start", TASK, RULE, "claude", event_gen or generation, "start",
                                                        hub_event, G.iso(T - dt.timedelta(hours=2))))

    def hub(self, sql, args=()):
        with contextlib.closing(sqlite3.connect(self.hubdb)) as con, con:
            return con.execute(sql, args).fetchall()

    def ask_card(self, cid=7, run=5, title="选一个超时", text="工人问：示例服务的超时设多少？", options=("30 秒", "60 秒"),
                 gen=1, run_state="done"):
        self.hub("DELETE FROM runs WHERE id = ?", (run,))
        self.hub("INSERT INTO events VALUES (11, 'hermes', ?, ?, ?, ?)",
                 (f"{TASK}:g1:start", G.event_type("dt-sandbox", "claude"), TASK,
                  json.dumps({"task_id": TASK, "generation": gen, "kind": "start"})))
        self.hub("INSERT INTO runs VALUES (?, ?, 'dt-claude', ?, ?, ?, '问了一个问题', 0)",
                 (run, RULE, TASK, run_state, utc(T - dt.timedelta(hours=1))))
        self.hub("INSERT INTO run_events VALUES (?, 11)", (run,))
        self.hub("INSERT INTO cards VALUES (?, ?, 'ask', 'open', 1, ?, ?, ?, NULL, NULL, ?, ?, 'hermes', ?, ?, NULL, NULL)",
                 (cid, f"thread:{RULE}:{TASK}:x", title, text, json.dumps(list(options), ensure_ascii=False), RULE, TASK, run,
                  utc(T - dt.timedelta(hours=1))))

    def stop_card(self, cid=8, run=9, attempts=10, state="cancelled", summary="连续 10 次没跑成，不再自动重试"):
        self.hub("INSERT INTO runs VALUES (?, ?, 'dt-claude', ?, ?, ?, ?, ?)",
                 (run, RULE, TASK, state, utc(T - dt.timedelta(hours=1)), summary, attempts))
        self.hub("INSERT INTO run_events VALUES (?, 11)", (run,))
        self.hub("INSERT INTO cards VALUES (?, ?, 'notice', 'open', 1, ?, ?, '[]', NULL, NULL, ?, ?, NULL, NULL, ?, NULL, NULL)",
                 (cid, f"run:{run}:stopped", RULE, "连续 10 次自动处理没跑完（卡在：网络），不再自动重试。", RULE, TASK,
                  utc(T - dt.timedelta(minutes=50))))
        self.feishu(cid, T - dt.timedelta(minutes=40))

    def feishu(self, cid, at):
        with F.db(write=True) as con:
            con.execute("INSERT OR REPLACE INTO feishu_cards (card_id, token, message_id, chat_id, kind, options, version, "
                        "delivered_at, hub_delivered_at) VALUES (?, ?, ?, ?, 'ask', 2, 1, ?, ?)",
                        (cid, f"{cid:016x}", f"om_{cid}", CHAT, at.isoformat(), at.isoformat()))

    def leased(self, cid, ref="sec-0123456789abcdef"):
        with F.db(write=True) as con:
            con.execute("INSERT OR REPLACE INTO secretary_cards VALUES (?, 1, ?, ?, ?)", (cid, ref, T.isoformat(), T.isoformat()))

    # ----- 提议、断言 -----
    def prop(self, **kw):
        p = {"schema": 1, "category": "ask", "card": 7, "card_version": 1, "choice": 1, "text": dict(TEXT),
             "model": {"name": "claude-opus-5-5", "fallback": False}, "error": None, "abstain": None}
        for k, v in kw.items():
            if v is DEL:
                p.pop(k, None)
            else:
                p[k] = v
        return p

    def submit(self, p=None, now=T):
        return SG.submit(p or self.prop(), now=now)

    def get(self, did):
        with L.TS.Store(L.db_path()) as st:
            return SL.get(st, did)

    def ledger(self, sql, args=()):
        with contextlib.closing(sqlite3.connect(L.db_path())) as con, con:
            return con.execute(sql, args).fetchall()

    def decisions(self):
        return self.ledger("SELECT id, action, category, status, reason FROM secretary_decisions ORDER BY created_at, rowid")

    def held(self):
        return self.ledger("SELECT kind, state FROM secretary_quota WHERE state IN ('reserved','used','frozen')")

    def answers(self):
        return [c for c in self.calls if c[1].endswith("/answer")]

    def claim(self, cid=7):
        with F.db() as con:
            r = con.execute("SELECT * FROM claims WHERE card_id = ? AND who = 'secretary'", (cid,)).fetchone()
        return dict(r) if r else None

    def handoff(self, out, reason):
        self.assertEqual((out["result"], out.get("reason")), ("handoff", reason), out["msg"])
        rows = self.decisions()
        self.assertEqual([(r[1], r[3], r[4]) for r in rows], [("handoff", "handoff", reason)])
        self.assertEqual(self.held(), [])                                   # 交常浩不占额度
        self.assertEqual(self.answers(), [])
        d = self.get(out["decision"])
        raw = L.read_raw(d)
        self.assertEqual(raw["received"]["source"][:2], "中台" if d["category"] != "reject" else "受信")   # 可信快照
        return d

    def nothing(self, out, result):
        self.assertEqual(out["result"], result, out["msg"])
        self.assertEqual(self.decisions(), [])
        self.assertEqual(self.answers(), [])


DEL = object()
REAL_ISSUE = SL.issue          # 打桩 SL.issue 的测试里，造别的决定要用原函数


class AskTests(Base):
    def test_ask_done_and_read_back(self):
        out = self.submit()
        self.assertEqual(out["result"], "done", out["msg"])
        self.assertEqual(self.answers(), [("POST", "/v1/cards/7/answer", {"choice": 0, "by": "秘书", "ref": "sec-0123456789abcdef"},
                                           "secretary")])
        d = self.get(out["decision"])
        self.assertEqual((d["status"], d["card_id"], d["card_version"]), ("done", 7, 1))
        self.assertEqual(d["outcome"]["waited_seconds"], 40 * 60)
        self.assertEqual(sorted(self.held()), [("day", "used"), ("task", "used")])
        self.assertEqual(self.claim()["state"], "delivered")
        with SL.open_store() as st:
            stats = SL.stats(st, "2026-11-18")["categories"]["ask"]
        self.assertEqual((stats["delivered"], stats["waited_seconds"], stats["waited_count"]), (1, 2400, 1))

    def test_bad_format_hands_off_as_model(self):
        for p in (self.prop(extra=1), self.prop(choice="1"), self.prop(model={"name": "x"}), self.prop(schema=2),
                  self.prop(text={**TEXT, "summary": ""}), self.prop(text={**TEXT, "basis": "按 HEAD 合并"})):
            with self.subTest(p=p):
                self.setUp()
                self.handoff(self.submit(p), "model")

    def test_model_error_fallback_and_abstain(self):
        for p, reason in ((self.prop(error="超时"), "model"), (self.prop(model={"name": "deepseek", "fallback": True}), "model"),
                          (self.prop(abstain={"reason": "scope", "note": "要换仓库"}), "scope")):
            with self.subTest(reason=reason):
                self.setUp()
                self.handoff(self.submit(p), reason)

    def test_wrong_category_claim_is_format_error(self):
        self.handoff(self.submit(self.prop(category="redispatch", choice=DEL)), "model")

    def test_option_out_of_range(self):
        self.handoff(self.submit(self.prop(choice=3)), "model")

    def test_card_missing_or_unbindable_only_refuses(self):
        self.nothing(self.submit(self.prop(card=99)), "refused")
        self.nothing(SG.submit("不是对象", now=T), "refused")
        self.nothing(self.submit(self.prop(card="7")), "refused")
        self.hub("UPDATE cards SET lane = 't-20261119-000000'")                 # 事项不是凭证库里的任务
        self.nothing(self.submit(), "refused")

    def test_secretary_off_without_session_only_refuses(self):
        with mock.patch.object(S, "read_status", lambda now=None: {**self.status(), "session": None}):
            self.nothing(self.submit(), "refused")

    def test_card_closed_or_version_changed_refuses(self):
        self.hub("UPDATE cards SET version = 2")
        self.nothing(self.submit(), "refused")
        self.hub("UPDATE cards SET version = 1, state = 'decided'")
        self.nothing(self.submit(), "refused")

    def test_wait_thirty_minutes_unless_custody(self):
        self.feishu(7, T - dt.timedelta(minutes=29))
        out = self.submit()
        self.nothing(out, "not_ready")
        self.assertIn("还差约 1 分钟", out["msg"])
        self.custody_on = True                                                 # 托管时段不等
        self.assertEqual(self.submit()["result"], "done")

    def test_no_feishu_record_hands_off(self):
        with F.db(write=True) as con:
            con.execute("DELETE FROM feishu_cards")
        d = self.handoff(self.submit(), "unsure")
        self.assertIn("你还没收到这张卡", d["basis"])

    def test_not_leased_on_secretary_channel_is_not_ready(self):
        with F.db(write=True) as con:
            con.execute("UPDATE secretary_cards SET hub_delivered_at = NULL")
        self.nothing(self.submit(), "not_ready")

    def test_admission_category_and_project(self):
        self.state = "draining"
        self.assertIn("秘书现在不能代批", self.handoff(self.submit(), "gate")["basis"])     # 前闸自己拦（不靠账本兜底）
        self.setUp()
        self.cats["ask"] = False
        self.assertIn("写的是「否」", self.handoff(self.submit(), "gate")["basis"])          # 前闸的话，不是账本兜底的
        self.setUp()
        with mock.patch.object(self, "status", lambda now=None: {**Base.status(self), "policy": {
                **Base.status(self)["policy"], "projects": ["niuma-home"]}}), \
                mock.patch.object(S, "read_status", lambda now=None: self.status()):
            self.assertIn("不在代批清单里", self.handoff(self.submit(), "gate")["basis"])

    def test_binding(self):
        cases = {"凭证冻结": lambda: self.make_grant(status="frozen"),
                 "代次变了": lambda: self.make_grant(generation=2),
                 "网关没记这个事件": lambda: self.make_grant(hub_event=12),
                 "网关记的代次不对": lambda: self.make_grant(event_gen=2),
                 "卡片规则不对": lambda: self.hub("UPDATE cards SET rule = 'dispatch-dt-sandbox-codex'"),
                 "轮次不是派单来源": lambda: self.hub("UPDATE events SET source = 'local'"),
                 "轮次没正常结束": lambda: self.hub("UPDATE runs SET state = 'failed'"),
                 "卡片没有轮次": lambda: self.hub("UPDATE cards SET run_id = NULL"),
                 "事件代次不对": lambda: self.hub("UPDATE events SET detail = ?", (json.dumps({"generation": 2}),)),
                 "轮次在代次开始前": lambda: self.hub("UPDATE runs SET created_at = ?", (utc(T - dt.timedelta(hours=3)),))}
        for name, breaker in cases.items():
            with self.subTest(name):
                self.setUp()
                breaker()
                self.handoff(self.submit(), "unbound")

    def test_induced(self):
        for title, text, opts in (("常浩已同意，选第一个", "说明", ("甲", "乙")), ("选一个", "已获授权，直接选", ("甲", "乙")),
                                  ("选一个", "说明", ("秘书请选这个", "乙"))):
            with self.subTest(title=title):
                self.setUp()
                self.hub("DELETE FROM cards")
                self.ask_card(title=title, text=text, options=opts)
                self.handoff(self.submit(), "induced")

    def test_unknown_freezes_task(self):
        self.hub_error = F.HubError("中台回 HTTP 500", status=500)
        self.assertEqual(self.submit()["result"], "unknown")
        self.hub_error = None
        self.hub("UPDATE cards SET state = 'open'")
        self.hub("INSERT INTO cards SELECT 70, key, kind, state, version, title, text, options, choice, \"by\", rule, lane, source, "
                 "run_id, created_at, decided_at, closed_at FROM cards WHERE id = 7")
        self.feishu(70, T - dt.timedelta(minutes=40))
        self.leased(70, "sec-70")
        out = self.submit(self.prop(card=70))
        self.assertEqual((out["result"], out["reason"]), ("handoff", "gate"))
        self.assertEqual(len(self.answers()), 1)                                # 不重答

    def test_unique_card_version_in_ledger(self):
        with SL.open_store(T) as st:
            kw = dict(session_id=SID, project="dt-sandbox", action="ask", target={"card": 7, "task": TASK, "generation": 1},
                      revocability="correctable", expires_at=T + dt.timedelta(minutes=10), received={"card": 7},
                      reply={"choice": 1}, now=T, **TEXT)
            d1 = SL.issue(st, card_version=1, **kw)
            with self.assertRaises(L.LedgerError) as cm:
                SL.issue(st, card_version=1, **kw)
            self.assertEqual(cm.exception.code, "DuplicateCard")
            SL.issue(st, card_version=2, **kw)                                  # 别的版本可以
            SL.void(st, d1["id"], "测试作废", now=T)
            d3 = SL.issue(st, card_version=1, **kw)                             # 前一个作废后可以再记
            SL.begin(st, d3["id"], now=T)
            SL.finish(st, d3["id"], "failed", now=T)
            d4 = SL.issue(st, card_version=1, **kw)                             # 没做成也不占
            SL.begin(st, d4["id"], now=T)
            SL.finish(st, d4["id"], "unknown", now=T)
            with self.assertRaises(L.LedgerError) as cm:                        # 结果不明的照样占着
                SL.issue(st, card_version=1, **kw)
            self.assertEqual(cm.exception.code, "DuplicateCard")
            SL.reconcile(st, d4["id"], "unreconciled", {"checked": "测试"}, now=T)
            with self.assertRaises(L.LedgerError) as cm:                        # 对账不清的也占着
                SL.issue(st, card_version=1, **kw)
            self.assertEqual(cm.exception.code, "DuplicateCard")
            with self.assertRaises(L.LedgerError) as cm:
                SL.issue(st, **kw)
            self.assertEqual(cm.exception.code, "BadInput")                     # ① 必须带卡片版本

    def test_duplicate_submit_refuses_second(self):
        with mock.patch.object(SG, "approve", return_value=("failed", "测试：停在获准前、没作废")):
            self.submit()
        self.assertEqual(self.decisions()[0][3], "issued")                        # 第一笔还在「已出未送达」
        out = self.submit()
        self.assertEqual(out["result"], "refused", out["msg"])
        self.assertIn("已经有一个可送达的秘书决定", out["msg"])

    def test_owner_holds_yields(self):
        with F.db(write=True) as con:
            con.execute("INSERT INTO claims (card_id, who, state, choice, at, attempt) VALUES (7, 'owner', 'queued', 1, 'x', 'a1')")
        out = self.submit()
        self.assertEqual(out["result"], "yielded", out["msg"])
        self.assertEqual(self.decisions()[0][3], "void")
        self.assertEqual(self.held(), [])
        self.assertEqual(self.answers(), [])
        with F.db() as con:
            log = con.execute("SELECT state, note FROM claim_log WHERE who = 'secretary'").fetchall()
        self.assertEqual([tuple(r) for r in log], [("yielded", "你已经在点了，秘书让出")])
        self.assertIsNone(self.claim())

    def test_post_gate_rereads(self):
        cases = {"卡片被答了": (lambda: self.hub("UPDATE cards SET state = 'decided'"), "卡片已经不开着"),
                 "凭证被撤销": (lambda: self.make_grant(status="revoked"), "任务凭证已经不能用"),
                 "准入变了": (lambda: setattr(self, "state", "draining"), "秘书已经不准入"),
                 "又冻结了": (self.unknown_sibling, "代批结果还不明"),
                 "秘书通道没了": (lambda: self.leased_gone(), "秘书通道还没领到")}
        for name, (breaker, words) in cases.items():
            with self.subTest(name):
                self.setUp()
                real = SL.issue

                def issue(*a, **kw):
                    d = real(*a, **kw)
                    breaker()                                                    # 记账之后、获准之前变了
                    return d
                with mock.patch.object(SL, "issue", issue):
                    out = self.submit()
                self.assertEqual(out["result"], "failed", out["msg"])
                self.assertIn(words, out["msg"])
                self.assertEqual(self.decisions()[0][3], "void")
                self.assertEqual(self.ledger("SELECT state FROM secretary_quota WHERE decision_id = ? AND state != 'released'",
                                             (out["decision"],)), [])
                self.assertEqual(self.answers(), [])

    def unknown_sibling(self):
        """同一张任务另有一个结果不明的秘书决定（直接写账本，模拟别的卡片那次结果不明）。"""
        with SL.open_store(T) as st:
            d = REAL_ISSUE(st, session_id=SID, project="dt-sandbox", action="ask", target={"card": 99, "task": TASK, "generation": 1},
                         revocability="correctable", expires_at=T + dt.timedelta(minutes=10), received={"card": 99},
                         reply={"choice": 1}, card_version=1, now=T, **TEXT)
            SL.begin(st, d["id"], now=T)
            SL.finish(st, d["id"], "unknown", now=T)

    def leased_gone(self):
        with F.db(write=True) as con:
            con.execute("DELETE FROM secretary_cards")

    def test_lock_order_cards_db_before_ledger(self):
        """获准在 cards.db 写锁里做：另一个连接占着 cards.db 写锁时，账本里的决定停在「已出未送达」。"""
        seen = []
        real = SL.issue

        def issue(*a, **kw):
            d = real(*a, **kw)
            con = sqlite3.connect(os.environ["DT_AUTOLOOP_CARDS_DB"], isolation_level=None, check_same_thread=False)
            con.execute("BEGIN IMMEDIATE")

            def hold():
                time.sleep(1)
                seen.append(self.ledger("SELECT status FROM secretary_decisions WHERE id = ?", (d["id"],))[0][0])
                con.execute("ROLLBACK")
                con.close()
            threading.Thread(target=hold).start()
            return d
        with mock.patch.object(SL, "issue", issue):
            out = self.submit()
        self.assertEqual(out["result"], "done", out["msg"])
        self.assertEqual(seen, ["issued"])

    def test_error_classes(self):
        cases = [(F.HubError("没钥匙", sent=False), "failed"), (F.HubError("HTTP 403", status=403), "failed"),
                 (F.HubError("HTTP 409", status=409), "failed"), (F.HubError("HTTP 500", status=500), "unknown"),
                 (F.HubError("超时"), "unknown"), (RuntimeError("怪"), "unknown")]
        for err, want in cases:
            with self.subTest(err=str(err)):
                self.setUp()
                self.hub_error = err
                out = self.submit()
                self.assertEqual(out["result"], want, out["msg"])
                self.assertEqual(self.decisions()[0][3], want)
                if want == "failed":
                    self.assertEqual(self.held(), [])
                    self.assertIsNone(self.claim())
                else:
                    self.assertEqual(sorted(self.held()), [("day", "frozen"), ("task", "frozen")])
                    self.assertEqual(self.claim()["state"], "unknown")

    def test_owner_answered_first(self):
        self.hub_side = lambda m, p, b: self.hub("UPDATE cards SET state = 'decided', choice = 1, \"by\" = ?", (OWNER,)) \
            if p.endswith("/answer") else None
        out = self.submit()
        self.assertEqual(out["result"], "failed")
        self.assertIn("你先答了", out["msg"])
        self.assertEqual(self.held(), [])

    def test_readback_mismatch_is_unknown(self):
        orig = self.fake_hub

        def hub(method, path, body=None, timeout=20, channel="feishu"):
            out = orig(method, path, body, timeout, channel)
            if path.endswith("/answer"):
                self.hub("UPDATE cards SET choice = 1")                           # 中台里是别的选项
            return out
        with mock.patch.object(F, "hub", hub):
            out = self.submit()
        self.assertEqual(out["result"], "unknown")


class ReconcileTests(Base):
    def test_unknown_reconciled_by_evidence_never_resent(self):
        self.hub_error = F.HubError("超时")
        out = self.submit()
        self.assertEqual(out["result"], "unknown")
        self.hub_error = None
        self.assertEqual(SG.reconcile(now=T), [(out["decision"], "unreconciled")])   # 卡还开着：核不清，不重答
        self.assertEqual(SG.reconcile(now=T), [])                                     # 已是对账不清：不重复记
        self.hub("UPDATE cards SET state = 'decided', choice = 0, \"by\" = '秘书'")
        self.assertEqual(SG.reconcile(now=T), [(out["decision"], "done")])
        self.assertEqual(len(self.answers()), 1)
        self.assertEqual(sorted(self.held()), [("day", "used"), ("task", "used")])
        self.assertEqual(self.claim()["state"], "delivered")

    def test_reconcile_owner_answer_means_failed(self):
        self.hub_error = F.HubError("HTTP 502", status=502)
        out = self.submit()
        self.hub("UPDATE cards SET state = 'decided', choice = 0, \"by\" = ?", (OWNER,))      # 常浩选了同一项也不算秘书答上
        self.assertEqual(SG.reconcile(card=7, now=T), [(out["decision"], "failed")])
        self.assertEqual(self.held(), [])
        self.assertIsNone(self.claim())

    def test_killed_mid_delivery_reconciles_once(self):
        orig = self.fake_hub

        def hub(method, path, body=None, timeout=20, channel="feishu"):
            out = orig(method, path, body, timeout, channel)
            if path.endswith("/answer"):
                raise KeyboardInterrupt                                           # 请求进了中台，进程没来得及记结果
            return out
        with mock.patch.object(F, "hub", hub), self.assertRaises(KeyboardInterrupt):
            self.submit()
        self.assertEqual(self.decisions()[0][3], "executing")
        self.assertEqual(self.claim()["state"], "approved")
        self.assertEqual(SG.reconcile(now=T)[0][1], "done")                       # 测试时间早于现在：算过期在途
        self.assertEqual(len(self.answers()), 1)
        self.assertEqual(self.claim()["state"], "delivered")


class RedispatchTests(Base):
    def setUp(self):
        super().setUp()
        self.stop_card()

    def prop(self, **kw):
        kw.setdefault("category", "redispatch")
        kw.setdefault("card", 8)
        kw.setdefault("choice", DEL)
        kw.setdefault("text", {"context": "给示例服务加超时", "summary": "重派一次", "basis": "中台连续没跑成，重派一次试试"})
        return super().prop(**kw)

    def test_redispatch_done_once(self):
        out = self.submit()
        self.assertEqual(out["result"], "done", out["msg"])
        self.assertEqual([json.loads(b["detail"])["kind"] for b in self.posted], ["redispatch"])
        self.assertEqual(sorted(self.held()), [("day", "used"), ("redispatch", "used"), ("task", "used")])
        self.hub("DELETE FROM cards WHERE id = 8")
        self.hub("DELETE FROM runs WHERE id = 9")
        self.stop_card(cid=18, run=19)                                            # 又放弃了一次：每张任务最多 1 次
        self.handoff_quota(self.submit(self.prop(card=18)))

    def handoff_quota(self, out):
        self.assertEqual((out["result"], out["reason"]), ("handoff", "gate"), out["msg"])
        self.assertIn("已用完", out["msg"])

    def test_not_given_up(self):
        for name, kw in (("还在重试", {"state": "queued"}), ("次数不够", {"attempts": 3}), ("不是放弃", {"summary": "已停止"})):
            with self.subTest(name):
                Base.setUp(self)
                self.stop_card(**kw)
                out = self.submit()
                self.assertEqual((out["result"], out.get("reason")), ("handoff", "unsure" if name != "还在重试" else "unbound"),
                                 out["msg"])

    def test_live_run_or_publish_in_flight(self):
        self.hub("INSERT INTO runs VALUES (20, ?, 'dt-claude', ?, 'queued', ?, '', 0)", (RULE, TASK, utc(T)))
        self.assertEqual(self.submit()["reason"], "unsure")
        Base.setUp(self)
        self.stop_card()
        with P.task_lock(TASK):
            out = self.submit()
        self.assertEqual(out["reason"], "unsure")
        self.assertIn("受信发布正在进行", self.get(out["decision"])["basis"])

    def test_gateway_refuses_is_failed(self):
        with mock.patch.object(G, "resume_dispatch", side_effect=G.Rejected("不能续接：凭证已冻结")):
            out = self.submit()
        self.assertEqual(out["result"], "failed")
        self.assertEqual(self.held(), [])

    def test_send_unknown_then_reconcile(self):
        with mock.patch.object(G, "post_event", side_effect=G.Rejected("连不上中台或回应不对：URLError")):
            out = self.submit()
        self.assertEqual(out["result"], "unknown", out["msg"])
        self.assertEqual(SG.reconcile(now=T), [])                                 # 网关事件还在途：不动
        with G.tx() as con:
            con.execute("UPDATE hub_events SET sent = 1, hub_event = 77 WHERE kind = 'redispatch'")
        self.assertEqual(SG.reconcile(now=T), [(out["decision"], "done")])


class RejectTests(Base):
    def setUp(self):
        super().setUp()
        rec = {"at": G.iso(T), "what": "发布", "task_id": TASK, "generation": 1, "github": REPO, "branch": f"claude/{TASK}",
               "head": HEAD, "run": "5", "pushed": True, "pr": f"https://github.com/{REPO}/pull/3", "pr_opened": True}
        Path(os.environ["DT_PUBLISH_LOG"]).parent.mkdir(parents=True, exist_ok=True)
        Path(os.environ["DT_PUBLISH_LOG"]).write_text("坏行\n" + json.dumps(rec) + "\n", encoding="utf-8")
        gh = self.root / "gh"
        self.ghlog = self.root / "gh.log"
        gh.write_text(f"#!{sys.executable}\nimport json, sys\nopen({str(self.ghlog)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
                      f"if sys.argv[1:3] == ['pr', 'view']:\n    print(json.dumps({{'state': 'OPEN', 'headRefOid': {HEAD!r}}}))\n")
        gh.chmod(0o755)
        self.gh = str(gh)

    def prop(self, **kw):
        p = {"schema": 1, "category": "reject", "task": TASK, "pr": 3, "head": HEAD,
             "text": {"context": "给示例服务加超时", "summary": "退回返修", "basis": "审查说超时没加测试，返修一轮补上"},
             "model": {"name": "claude-opus-5-5", "fallback": False}, "error": None, "abstain": None}
        p.update(kw)
        return p

    def review(self, github, pr, head):
        return {"verdict": "REWORK", "repo": github, "pr": pr, "head": head, "by": "测试"}

    def test_no_trusted_review_always_hands_off(self):
        d = self.handoff(self.submit(), "unsure")
        self.assertEqual(d["target"], {"task": TASK, "repo": REPO, "pr": 3, "head": HEAD})
        self.assertIn("审查记录", d["basis"])

    def test_comment_switch_off_hands_off(self):
        with mock.patch.object(SG, "REVIEW_SOURCE", self.review):
            d = self.handoff(self.submit(), "gate")                              # REAL_COMMENT 关着、没注入 gh
        self.assertIn("机器账号还没在 dt-gate 配好", d["basis"])

    def test_no_publish_record_only_refuses(self):
        self.nothing(self.submit(self.prop(pr=4)), "refused")

    def test_reject_done_with_fake_gh(self):
        with mock.patch.multiple(SG, REVIEW_SOURCE=self.review, TEST_GH=self.gh):
            out = self.submit()
        self.assertEqual(out["result"], "done", out["msg"])
        calls = [json.loads(x) for x in self.ghlog.read_text().splitlines()]
        self.assertEqual([c[:2] for c in calls], [["pr", "view"], ["pr", "comment"]])
        self.assertEqual([json.loads(b["detail"])["kind"] for b in self.posted], ["rework"])

    def test_head_moved_or_not_ours(self):
        with mock.patch.multiple(SG, REVIEW_SOURCE=self.review, TEST_GH=self.gh):
            self.handoff(self.submit(self.prop(head="c" * 40)), "unsure")
            self.setUp()
            with mock.patch.object(SG, "pr_now", return_value=("OPEN", "d" * 40)):
                self.handoff(self.submit(), "unsure")

    def test_comment_sent_but_rework_refused_is_unknown(self):
        with mock.patch.multiple(SG, REVIEW_SOURCE=self.review, TEST_GH=self.gh), \
                mock.patch.object(G, "resume_dispatch", side_effect=G.Rejected("不能续接：凭证已冻结")):
            out = self.submit()
        self.assertEqual(out["result"], "unknown", out["msg"])                    # 留言撤不回：不能当没做
        self.assertEqual(sorted(self.held()), [("day", "frozen"), ("task", "frozen")])

    def test_comment_unknown_is_not_resent(self):
        bad = self.root / "badgh"
        bad.write_text(f"#!{sys.executable}\nimport json, sys\nif sys.argv[1:3] == ['pr', 'view']:\n"
                       f"    print(json.dumps({{'state': 'OPEN', 'headRefOid': {HEAD!r}}}))\nelse:\n    sys.exit(1)\n")
        bad.chmod(0o755)
        with mock.patch.multiple(SG, REVIEW_SOURCE=self.review, TEST_GH=str(bad)):
            out = self.submit()
        self.assertEqual(out["result"], "unknown")
        self.assertEqual(self.posted, [])
        self.assertEqual(SG.reconcile(now=T), [(out["decision"], "unreconciled")])


class LateOwnerNoteTests(unittest.TestCase):
    def test_note_names_the_secretary_choice(self):
        self.assertEqual(A.secretary_note({"by": "秘书", "choice": 1, "options": ["甲", "乙"]}),
                         "秘书替你选了「乙」，不同意可以推翻（入口在秘书代批汇报卡上）。")
        self.assertIn("「乙」", A.secretary_note({"by": "秘书", "choice": 1, "options": json.dumps(["甲", "乙"])}))
        self.assertEqual(A.secretary_note({"by": OWNER, "choice": 1, "options": ["甲", "乙"]}), "")
        self.assertEqual(A.secretary_note(None), "")


class LeaseTests(Base):
    def test_lease_registers_ref_and_reports_once_per_version(self):
        card = {"id": 30, "version": 1, "ref": None, "state": "open"}
        got = []

        def hub(method, path, body=None, timeout=20, channel="feishu"):
            got.append((method, path.split("?")[0], body, channel))
            return {"card": dict(card)} if method == "GET" else {}
        with mock.patch.object(F, "hub", hub):
            out = SG.lease(wait=1)
            ref = out["ref"]
            self.assertRegex(ref, r"^sec-[0-9a-f]{16}$")
            card.update(version=2, ref=ref, state="decided")
            self.assertEqual(SG.lease(wait=1)["ref"], ref)                          # 状态变了再领：同一个 ref
            card.update(version=3, ref="sec-other")
            self.assertFalse(SG.lease(wait=1)["ok"])
        self.assertTrue(all(c[3] == "secretary" for c in got))
        posts = [(p, b) for m, p, b, _ in got if m == "POST"]
        self.assertEqual(posts[:2], [("/v1/cards/30/delivered", {"version": 1, "ref": ref}),
                                     ("/v1/cards/30/delivered", {"version": 2, "ref": ref})])
        self.assertEqual(posts[2][0], "/v1/cards/30/failed")
        with F.db() as con:
            r = con.execute("SELECT * FROM secretary_cards WHERE card_id = 30").fetchone()
        self.assertEqual((r["ref"], r["version"], bool(r["hub_delivered_at"])), (ref, 2, True))


class CliTests(Base):
    def run_cli(self, *args):
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "secretary_gate.py"), *args], capture_output=True, text=True,
                           env=dict(os.environ), timeout=60)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_bad_file_refuses_and_show_is_read_only(self):
        f = self.root / "p.json"
        f.write_text("{不是 JSON", encoding="utf-8")
        code, out = self.run_cli("submit", "--file", str(f))
        self.assertEqual((code, out["result"]), (1, "refused"))
        code, out = self.run_cli("show")
        self.assertEqual((code, out["ok"]), (0, True))
        self.assertEqual(self.decisions(), [])


# ---------- 端到端：真中台 ----------
@unittest.skipIf(SKIP, "本机没有 agent-autoloop 命令行")
class SecretaryEndToEnd(HubFixture):
    COMMAND = f"{sys.executable} {HERE / 'fixtures' / 'ask_receipt.py'} {{prompt}}"

    def setUp(self):
        super().setUp()
        root = Path(self.key_file).parents[2]
        self.repo_full = "o/dt-sandbox"
        env = {"DT_AUTOLOOP_CARDS_DB": str(root / "dt/autoloop/cards.db"), "DT_AUTOLOOP_CHANNEL_KEY_DIR": str(root / "dt/autoloop"),
               "DT_AUTOLOOP_LOCK": str(root / "dt/autoloop/config.lock"), "DT_OWNER_CHAT_ID": CHAT,
               "DT_STATE_DB": str(root / "twin-state.db"), "DT_SECRETARY_DIR": str(root / "secretary"),
               "DT_SECRETARY_FILE": str(root / "secretary.json"), "DT_SECRETARY_POLICY": str(root / "policy.md"),
               "DT_CUSTODY_FILE": str(root / "custody.json"), "DT_CUSTODY_POLICY": str(root / "custody-policy.md"),
               "DT_GRANTS_DB": str(root / "dt/autoloop/grants.db"), "DT_GRANTS_STATUS": str(root / "shared/status.json"),
               "DT_GRANTS_TASKS": str(root / "shared/tasks"), "DT_PUBLISH_LOG": str(root / "dt/autoloop/publish.jsonl"),
               "DT_PUBLISH_OUTBOX": str(root / "outbox"), "DT_PUBLISH_MIRRORS": str(root / "mirrors")}
        for p in (mock.patch.dict(os.environ, env), mock.patch.object(AC.G, "ROSTER", self.roster)):
            p.start()
            self.addCleanup(p.stop)
        from test_autoloop_feishu import Lark
        self.lark = Lark(mid="om_e2e_1")
        for target, kw in ((F, {"lark": self.lark}), (A, {"tell": lambda chat, text: None}),
                           (S, {"read_status": self.status, "dispatch_projects": lambda: {"dt-sandbox": self.repo_full}})):
            p = mock.patch.multiple(target, **kw)
            p.start()
            self.addCleanup(p.stop)
        st = L.TS.Store(L.db_path()).open()
        self.lid = SL.register_session(st, SID)
        st.close()
        self.workdir = root / "work"
        self.workdir.mkdir()
        self.prepare_hub()

    def status(self, now=None):
        return {"state": "on", "error": None,
                "session": {"id": SID, "ledger": {"id": self.lid}, "policy": {"version": 3, "sha256": PSHA}},
                "policy": {"ok": True, "version": 3, "sha256": PSHA, "categories": {"ask": True, "redispatch": True,
                                                                                     "reject": True, "merge": True},
                           "projects": ["dt-sandbox"], "repos": {"dt-sandbox": self.repo_full},
                           "limits": {"day": 10, "task": 3, "merge_day": 2, "redispatch": 1}, "whitelist": {}},
                "admission": {"eligible": True, "reasons": []}}

    def prepare_hub(self):
        AC.install(confirm=True, target=self.home)
        AC.issue_source_key(confirm=True, target=self.home)
        AC.issue_channel_key("feishu", True, str(self.home))
        AC.issue_channel_key("secretary", True, str(self.home))
        rules = json.loads(AC.rules_file().read_text(encoding="utf-8"))           # 工人换成假接单命令，跑在本机
        rules["agents"]["dt-claude"] = {"name": "假工人", "worker": "this-machine", "use": "command",
                                        "command": [sys.executable, str(HERE / "fixtures" / "ask_receipt.py"), "{prompt}"]}
        for r in rules["rules"]:
            if r["name"] == RULE:
                r["cwd"] = str(self.workdir)
        AC.rules_file().write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        self._wait_live(lambda live: live["agents"].get("dt-claude", {}).get("use") == "command")
        AC.hub("POST", f"/v1/rules/{RULE}/resume", {})
        now = G.now_tz()
        self.task = f"t-{now:%Y%m%d}-{os.urandom(3).hex()}"
        with G.tx() as con:
            con.execute("INSERT INTO grants (task_id, repo, github, scope_text, scope_hash, executor, rule, actions, valid_until, "
                        "status, generation, generation_started_at, approval_message, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,'active',1,?,?,?,?)",
                        (self.task, "dt-sandbox", self.repo_full, "五要素", G.sha256("五要素"), "claude", RULE,
                         json.dumps(list(G.ACTIONS)), G.iso(now + dt.timedelta(hours=2)),
                         G.iso(now - dt.timedelta(minutes=5)), "om_x", G.iso(now), G.iso(now)))
            G.record_event(con, G.grant_row(con, self.task), "start", f"{self.task}:g1:start", now)
        sent = G.send_pending()
        self.assertTrue(sent and sent[0]["sent"], sent)
        self.card = self.wait_card()
        with F.db(write=True) as con:                                            # 飞书送达是 40 分钟前
            con.execute("UPDATE feishu_cards SET delivered_at = ? WHERE card_id = ?",
                        ((dt.datetime.now().astimezone() - dt.timedelta(minutes=40)).isoformat(), self.card))
        for _ in range(30):
            out = SG.lease(wait=1)
            if out and out["card"] == self.card:
                break
        else:
            self.fail("秘书通道没领到卡片")
        self.token = self.lark.sent()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["autoloop"]

    def wait_card(self):
        for _ in range(60):
            out = F.once(wait=1)
            if out:
                self.assertTrue(out["ok"], out)
                return out["card"]
        self.fail("中台没出卡片")

    def hub_card(self):
        return P.hub_card(self.card)

    def prop(self, choice=2):
        return {"schema": 1, "category": "ask", "card": self.card, "card_version": self.hub_card()["version"], "choice": choice,
                "text": {"context": "试跑选颜色", "question": "选蓝色还是绿色", "summary": "选绿色", "basis": "试跑，选哪个都行"},
                "model": {"name": "claude-opus-5-5", "fallback": False}, "error": None, "abstain": None}

    def test_secretary_answers_then_owner_click_is_told(self):
        out = SG.submit(self.prop())
        self.assertEqual(out["result"], "done", out["msg"])
        c = self.hub_card()
        self.assertEqual((c["state"], c["by"], c["choice"]), ("decided", "秘书", 1))
        code, said, _ = A.answer(self.token, 0, CHAT, OWNER)                      # 常浩迟到的点击
        self.assertEqual(code, 1)
        self.assertIn("秘书替你选了「绿色」", said)
        self.assertIn("推翻", said)
        self.assertEqual(self.hub_card()["by"], "秘书")

    def test_forged_answerers_are_refused_by_hub(self):
        with F.db() as con:
            ref = con.execute("SELECT ref FROM secretary_cards WHERE card_id = ?", (self.card,)).fetchone()[0]
            mid = con.execute("SELECT message_id FROM feishu_cards WHERE card_id = ?", (self.card,)).fetchone()[0]
        for body, channel in (({"choice": 0, "by": OWNER, "ref": ref}, "secretary"),        # 秘书钥匙冒充常浩
                              ({"choice": 0, "by": "秘书", "ref": mid}, "feishu"),          # 飞书钥匙冒充秘书
                              ({"choice": 0, "by": "秘书", "ref": "sec-wrong"}, "secretary")):   # ref 不对
            with self.subTest(channel=channel, by=body["by"]):
                with self.assertRaises(F.HubError) as cm:
                    F.hub("POST", f"/v1/cards/{self.card}/answer", body, channel=channel)
                self.assertIn(cm.exception.status, A.DEFINITE)
        self.assertEqual(self.hub_card()["state"], "open")

    def test_owner_click_already_here_wins(self):
        card = F.card_by_token(self.token)
        attempt, _ = A.claim(card, 0)                                             # 常浩的点击已到本机（排队）
        out = SG.submit(self.prop())
        self.assertEqual(out["result"], "yielded", out["msg"])
        self.assertEqual(A.submit(self.card)[0], 0)
        c = self.hub_card()
        self.assertEqual((c["state"], c["by"], c["choice"]), ("decided", OWNER, 0))
        self.assertTrue(attempt)

    def test_killed_at_delivery_reconciles_without_second_answer(self):
        real, answers = F.hub, []

        def hub(method, path, body=None, timeout=20, channel="feishu"):
            out = real(method, path, body, timeout, channel)
            if path.endswith("/answer"):
                answers.append(body)
                raise KeyboardInterrupt                                           # 中台受理了，进程被杀
            return out
        with mock.patch.object(F, "hub", hub), self.assertRaises(KeyboardInterrupt):
            SG.submit(self.prop())
        with SL.open_store() as st:
            d = SL.decisions(st)[-1]
        self.assertEqual(d["status"], "executing")
        with mock.patch.object(SG, "STALE", -1), mock.patch.object(F, "hub", hub):
            self.assertEqual(SG.reconcile(), [(d["id"], "done")])
            out = SG.submit(self.prop())                                         # 再交一次：卡已不开着，不重答
        self.assertEqual(out["result"], "refused")
        self.assertEqual(len(answers), 1)
        c = self.hub_card()
        self.assertEqual((c["state"], c["by"]), ("decided", "秘书"))


if __name__ == "__main__":
    unittest.main()
