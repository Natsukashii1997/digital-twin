"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

autoloop_grants.py（任务凭证与派单网关，#195 批 0B-1）：两步批准（随机确认码、脚本自己发确认消息、内容和名册映射绑定）、
凭证判定、代次作废、冻结 / 恢复（冻结码 + 先记更正）/ 撤销、状态镜像（收紧先写镜像、放宽后写）、任务单、
只在凭证有效时续接、逐个现读领取后发事件。凭证库、镜像、任务单、名册都放临时目录；飞书回读打桩 fetch_message
（走真实的 twin_identity.verify_message），发私聊打桩 notify_owner；中台用假的 post。不联网、不碰 ~/Library/Application Support/DigitalTwin。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import fcntl
import io
import json
import os
import re
import sqlite3
import stat
import subprocess
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

OWNER = "ou_fixture_owner_0001"
CHAT = "oc_fixture_p2p_0001"
T0 = dt.datetime(2026, 11, 17, 10, 0, tzinfo=G.TZ)
LATER = T0 + dt.timedelta(seconds=61)          # 过了在途窗口
SCOPE = "目标：给 dt-sandbox 的 README 加一节\n范围：只改 README.md\n验收：PR 里只有 README.md"


def raw_msg(mid, text, when, sender=OWNER, chat=CHAT):
    return {"message_id": mid, "chat_id": chat, "content": text, "create_time": when.astimezone().strftime("%Y-%m-%d %H:%M"),
            "deleted": False, "msg_type": "text", "sender": {"id": sender, "id_type": "open_id", "sender_type": "user"}}


def down(body):
    raise G.Rejected("连不上中台")


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-grants-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "dt/autoloop/grants.db"
        self.status = self.root / "shared/grants/status.json"
        self.tasks = self.root / "shared/tasks"
        self.roster = self.root / "projects.json"
        self.write_roster("o/dt-sandbox")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DT_OWNER", "DT_GRANTS", "HERMES_SESSION"))}
        env.update(DT_GRANTS_DB=str(self.db), DT_GRANTS_STATUS=str(self.status), DT_GRANTS_TASKS=str(self.tasks),
                   DT_OWNER_OPEN_ID=OWNER, DT_OWNER_CHAT_ID=CHAT)
        for p in (mock.patch.dict(os.environ, env, clear=True), mock.patch.object(G, "ROSTER", self.roster),
                  mock.patch.object(G, "fetch_message", side_effect=self._fetch),
                  mock.patch.object(G, "notify_owner", side_effect=self._notify),
                  mock.patch.object(R, "ensure_open"), mock.patch.object(R, "admit")):   # 恢复闸放行（关着见 RecoveryGate）
            p.start()
            self.addCleanup(p.stop)
        self.msgs, self.n, self.posted, self.notices = {}, 0, [], []

    def write_roster(self, sandbox, twin_write=True):
        inv = [{"path": "github:o/old", "category": "待删"}]
        if sandbox:
            inv.insert(0, {"path": f"github:{sandbox}", "category": "沙盒", "sandbox": True})
        twin = {"id": "digital-twin", "github": "o/digital-twin", "category": "活跃"}
        if twin_write is not None:
            twin["write_allowed"] = twin_write
        self.roster.write_text(json.dumps({
            "projects": [twin, {"id": "agent-autoloop", "github": "o/agent-autoloop", "category": "活跃", "write_allowed": False},
                         {"id": "little-steps", "github": "o/little-steps", "category": "活跃"}],     # 没写 write_allowed
            "inventory": inv}), encoding="utf-8")

    def _fetch(self, mid):
        m = self.msgs.get(mid)
        return None if m is None else G.I.normalize(m) | {"text": m["content"]}

    def _notify(self, text, key):
        self.notices.append((text, key))
        return f"om_notice{len(self.notices):04d}"

    def say(self, text, when=T0, **kw):
        self.n += 1
        mid = f"om_fixturemsg{self.n:04d}"
        self.msgs[mid] = raw_msg(mid, text, when, **kw)
        return mid

    def post(self, body):
        d = json.loads(body["detail"])                    # 发出的每个事件，任务和代次都已在工人镜像里且有效
        t = self.mirror().get(d["task_id"]) if self.status.exists() else None
        self.assertTrue(t and t["status"] == "active" and t["generation"] == d["generation"],
                        f"发出 {body['id']} 时工人镜像里查不到这一代：{t}")
        self.posted.append(body)
        return 100 + len(self.posted)

    def new_task(self, when=T0, repo="dt-sandbox", executor="claude", post=None):
        code = G.propose(repo, executor, SCOPE, hours=12, now=when)["code"]
        return G.approve(self.say(f"派单 {code}", when), now=when, post=post or self.post)["task_id"]

    def mirror(self):
        return json.loads(self.status.read_text(encoding="utf-8"))["tasks"]

    def sql(self, q, args=()):
        con = sqlite3.connect(self.db)
        try:
            rows = con.execute(q, args).fetchall()
            con.commit()
            return rows
        finally:
            con.close()

    def sent_states(self, tid):
        return dict(self.sql("SELECT kind, sent FROM hub_events WHERE task_id = ?", (tid,)))

    def unfreeze_ok(self, tid, code, when=T0):
        G.record_resolution(tid, G.show(tid)[0]["generation"], "d-1", "常浩重选 B，旧 PR 关掉", now=when)
        return G.unfreeze(self.say(f"恢复 {tid} {code}", when), now=when)


class Approval(Base):
    def test_two_step_issues_grant_and_sends_start(self):
        tid = self.new_task()
        self.assertRegex(tid, G.TASK_ID)
        (g,) = G.show(tid)
        self.assertEqual((g["status"], g["generation"], g["actions"]), ("active", 1, list(G.ACTIONS)))
        self.assertEqual(g["github"], "o/dt-sandbox")
        self.assertEqual(g["rule"], "dispatch-dt-sandbox-claude")
        (text, key), = self.notices                        # 确认消息由脚本自己发，内容就是要签的内容
        for want in ("o/dt-sandbox", "claude", "只改 README.md", "改文件", "派单 "):
            self.assertIn(want, text)
        self.assertTrue(key.startswith("dt-grant-"))
        self.assertEqual(len(self.posted), 1)
        ev = self.posted[0]
        self.assertEqual((ev["subject"], ev["type"], ev["id"]), (tid, "dispatch.dt-sandbox.claude.write", f"{tid}:g1:start"))
        self.assertEqual(json.loads(ev["detail"]), {"task_id": tid, "generation": 1, "kind": "start"})
        self.assertNotIn("README", json.dumps(ev, ensure_ascii=False))   # 凭证内容不进事件

    def test_phrase_must_match_a_live_proposal(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        self.assertRegex(code, r"^[0-9a-f]{8}$")
        for text in ("派单 00000000", "派单", "好的派吧", f"派单 {code} 顺便合并", f"派单 {code[:6]}"):
            with self.subTest(text=text), self.assertRaises(G.Rejected):
                G.approve(self.say(text), now=T0, post=self.post)
        self.assertEqual(G.show(), [])

    def test_codes_are_random_and_never_overwrite(self):
        a = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        b = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        self.assertNotEqual(a, b)                          # 同样内容也是新码
        with mock.patch.object(G.secrets, "token_hex", side_effect=["aaaaaaaa", "aaaaaaaa", "bbbbbbbb"]):
            G.propose("dt-sandbox", "claude", SCOPE, now=T0)
            c = G.propose("dt-sandbox", "codex", SCOPE + "\n顺便升级依赖", now=T0)["code"]
        self.assertEqual(c, "bbbbbbbb")                    # 撞号重抽，不覆盖
        tid = G.approve(self.say("派单 aaaaaaaa"), now=T0, post=self.post)["task_id"]
        self.assertEqual(G.show(tid)[0]["executor"], "claude")
        self.assertEqual(G.show(tid)[0]["scope_hash"], G.sha256(SCOPE))

    def test_proposal_expires(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        later = T0 + dt.timedelta(minutes=31)
        with self.assertRaisesRegex(G.Rejected, "过期"):
            G.approve(self.say(f"派单 {code}", later), now=later, post=self.post)

    def test_undelivered_notice_cannot_be_approved(self):
        with mock.patch.object(G, "notify_owner", side_effect=G.Rejected("确认消息没发出去")):
            with self.assertRaisesRegex(G.Rejected, "没发出去"):
                G.propose("dt-sandbox", "claude", SCOPE, now=T0)
        (code,), = self.sql("SELECT code FROM proposals")
        with self.assertRaisesRegex(G.Rejected, "确认消息没发到"):
            G.approve(self.say(f"派单 {code}"), now=T0, post=self.post)
        self.assertEqual(G.show(), [])

    def test_roster_mapping_change_rejects(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        self.write_roster("o2/dt-sandbox")                 # 短名一样，完整仓库变了
        with self.assertRaisesRegex(G.Rejected, "仓库变了"):
            G.approve(self.say(f"派单 {code}"), now=T0, post=self.post)
        self.write_roster(None)
        with self.assertRaisesRegex(G.Rejected, "仓库变了"):
            G.approve(self.say(f"派单 {code}"), now=T0, post=self.post)
        self.assertEqual(G.show(), [])

    def test_missing_write_allowed_is_not_allowed(self):
        code = G.propose("digital-twin", "codex", SCOPE, now=T0)["code"]
        self.write_roster("o/dt-sandbox", twin_write=None)  # 字段被删掉：不再算允许写入
        with self.assertRaisesRegex(G.Rejected, "仓库变了"):
            G.approve(self.say(f"派单 {code}"), now=T0, post=self.post)
        with self.assertRaisesRegex(G.Rejected, "名册"):
            G.propose("digital-twin", "codex", SCOPE, now=T0)
        self.assertEqual(G.show(), [])

    def test_tampered_proposal_rejected(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        self.sql("UPDATE proposals SET scope_text = ? WHERE code = ?", (SCOPE + "\n顺便合并", code))
        with self.assertRaisesRegex(G.Rejected, "对不上"):
            G.approve(self.say(f"派单 {code}"), now=T0, post=self.post)

    def test_not_owner_or_not_private_or_old_or_replayed(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        for kw in ({"sender": "ou_someone_else_0001"}, {"chat": "oc_some_group_0001"}):
            with self.subTest(kw=kw), self.assertRaisesRegex(G.Rejected, "身份核实不通过"):
                G.approve(self.say(f"派单 {code}", **kw), now=T0, post=self.post)
        old = self.say(f"派单 {code}", T0 - dt.timedelta(minutes=20))
        with self.assertRaisesRegex(G.Rejected, "身份核实不通过"):
            G.approve(old, now=T0, post=self.post)
        mid = self.say(f"派单 {code}")
        G.approve(mid, now=T0, post=self.post)
        with self.assertRaisesRegex(G.Rejected, "已经签过|已经用过"):
            G.approve(mid, now=T0, post=self.post)

    def test_reproposing_same_task_needs_a_new_approval(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        mid = self.say(f"派单 {code}")
        G.approve(mid, now=T0, post=self.post)
        self.assertNotEqual(G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"], code)
        with self.assertRaisesRegex(G.Rejected, "已经签过|已经用过"):
            G.approve(mid, now=T0, post=self.post)
        self.assertEqual(len(G.show()), 1)

    def test_repo_and_executor_and_hours_checked(self):
        for repo in ("agent-autoloop", "little-steps", "old", "nope", "../x"):
            with self.subTest(repo=repo), self.assertRaisesRegex(G.Rejected, "名册"):
                G.propose(repo, "claude", SCOPE, now=T0)
        with self.assertRaises(G.Rejected):
            G.propose("dt-sandbox", "gpt", SCOPE, now=T0)
        with self.assertRaises(G.Rejected):
            G.propose("dt-sandbox", "claude", SCOPE, hours=100, now=T0)
        with self.assertRaises(G.Rejected):
            G.propose("dt-sandbox", "claude", "", now=T0)
        with self.assertRaises(G.Rejected):
            G.propose("dt-sandbox", "claude", SCOPE, actions=("edit", "merge"), now=T0)
        self.assertEqual(G.propose("digital-twin", "codex", SCOPE, now=T0)["github"], "o/digital-twin")

    def test_files_are_private_and_shared_files_are_readable(self):
        old = os.umask(0o077)                                    # 严格 umask 下共享目录也要显式给 755
        self.addCleanup(os.umask, old)
        tid = self.new_task()
        self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.db.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.status.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.status.parent.stat().st_mode), 0o755)
        text = self.status.read_text(encoding="utf-8")
        self.assertNotIn("只改 README.md", text)                 # 范围原文不进镜像，只给哈希
        self.assertNotIn("om_", text)                            # 消息号不进镜像
        task = self.tasks / f"{tid}.md"
        self.assertEqual(stat.S_IMODE(task.stat().st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(self.tasks.stat().st_mode), 0o755)
        body = task.read_text(encoding="utf-8")
        self.assertIn("只改 README.md", body)                    # 工人从任务单取五要素
        self.assertIn(G.sha256(SCOPE), body)
        self.assertNotIn("om_", body)

    def test_old_schema_is_refused(self):
        self.db.parent.mkdir(parents=True)
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE grants (task_id TEXT)")
        con.close()
        with self.assertRaisesRegex(G.Rejected, "结构"):
            G.show()


class Sending(Base):
    def test_hub_unreachable_keeps_event_for_resend(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        out = G.approve(self.say(f"派单 {code}"), now=T0, post=down)
        self.assertEqual([s["sent"] for s in out["send"]], [None])         # 结果不明
        self.assertEqual(G.show(out["task_id"])[0]["status"], "active")
        self.assertEqual(G.send_pending(post=self.post, now=T0), [])       # 刚领走的不抢
        self.assertEqual([s["sent"] for s in G.send_pending(post=self.post, now=LATER)], [True])
        self.assertEqual(G.send_pending(post=self.post, now=LATER + dt.timedelta(minutes=5)), [])   # 送达就不再发

    def test_freeze_during_send_stops_the_rest_and_keeps_truth(self):
        tid = self.new_task(post=down)
        G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=down)

        def post_then_freeze(body):
            n = self.post(body)
            G.freeze(tid, "常浩推翻", now=LATER)                   # 第一个在途时冻结
            return n
        out = G.send_pending(post=post_then_freeze, now=LATER)
        self.assertEqual([p["id"] for p in self.posted], [f"{tid}:g1:start"])   # 只发出已领取的那个
        self.assertEqual([o["sent"] for o in out], [True, False])
        self.assertEqual(self.sent_states(tid), {"start": 1, "rework": -2})     # 送达的如实记 1，没发的作废

    def test_freeze_never_marks_inflight_or_delivered_as_unsent(self):
        tid = self.new_task()
        G.freeze(tid, "推翻", now=T0)
        self.assertEqual(self.sent_states(tid), {"start": 1})

    def test_expired_grant_events_are_not_sent(self):
        tid = self.new_task(post=down)
        late = T0 + dt.timedelta(hours=13)
        out = G.send_pending(post=self.post, now=late)
        self.assertEqual([o["sent"] for o in out], [False])
        self.assertIn("过期", out[0]["why"])
        self.assertEqual(self.posted, [])
        self.assertEqual(self.sent_states(tid), {"start": -2})

    def test_unknown_result_is_not_resent_after_freeze(self):
        tid = self.new_task(post=down)
        G.freeze(tid, "推翻", now=T0)
        self.assertEqual([o["sent"] for o in G.send_pending(post=self.post, now=LATER)], [False])
        self.assertEqual(self.posted, [])

    def test_send_pending_rechecks_grant_even_if_ledger_says_unsent(self):
        """冻结会把没发的事件标作废；就算账本被弄回「未发」，发之前还要再核凭证。"""
        tid = self.new_task()
        G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=down)
        G.freeze(tid, "推翻", now=T0)
        self.sql("UPDATE hub_events SET sent = 0 WHERE task_id = ? AND kind = 'rework'", (tid,))
        out = G.send_pending(post=self.post, now=LATER)
        self.assertEqual([o["sent"] for o in out], [False])
        self.assertEqual([p["id"] for p in self.posted], [f"{tid}:g1:start"])

    def test_resume_event_carries_no_scope_or_reason(self):
        tid = self.new_task()
        G.resume_dispatch(tid, "rework", "审查说 README 写错了", now=T0, post=self.post)
        ev = self.posted[-1]
        self.assertEqual(json.loads(ev["detail"]), {"task_id": tid, "generation": 1, "kind": "rework"})
        self.assertNotIn("README", json.dumps(ev, ensure_ascii=False))


class RecoveryGate(Base):
    """批 0D：恢复闸关着时不提议（不发确认消息）、不往中台发事件；事件留在账里，开闸后补发；冻结照常立刻生效。"""
    def closed(self):
        return mock.patch.object(R, "ensure_open", side_effect=E.Refused("恢复闸关着：电脑睡醒过，要先对账"))

    def test_closed_gate_blocks_propose_and_send_but_keeps_events(self):
        with self.closed():
            with self.assertRaisesRegex(G.Rejected, "睡醒"):
                G.propose("dt-sandbox", "claude", SCOPE, now=T0)
        self.assertEqual(self.notices, [])
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        with self.closed():
            out = G.approve(self.say(f"派单 {code}"), now=T0, post=self.post)
            self.assertIn("恢复闸关着", out["send"]["error"])
            with self.assertRaisesRegex(G.Rejected, "send-pending 补发"):
                G.send_pending(post=self.post, now=T0)
            self.assertEqual(G.freeze(out["task_id"], "常浩推翻", now=T0)["status"], "frozen")   # 收权不受闸影响
        self.assertEqual(self.posted, [])
        self.assertEqual(self.sent_states(out["task_id"]), {"start": -1})              # 冻结作废了没发的

    def test_events_wait_for_the_gate_then_go_once(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        with self.closed():
            tid = G.approve(self.say(f"派单 {code}"), now=T0, post=self.post)["task_id"]
        self.assertEqual(self.sent_states(tid), {"start": 0})
        self.assertEqual([s["sent"] for s in G.send_pending(post=self.post, now=T0)], [True])
        self.assertEqual(G.send_pending(post=self.post, now=LATER), [])
        self.assertEqual(len(self.posted), 1)


class Mirror(Base):
    def test_freeze_takes_effect_even_if_mirror_cannot_be_written(self):
        tid = self.new_task()
        G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=down)
        with mock.patch.object(G, "_write_shared", side_effect=OSError("磁盘满")):
            out = G.freeze(tid, "推翻", now=T0)
            self.assertIn("mirror_error", out)
            self.assertEqual(G.show(tid)[0]["status"], "frozen")         # 权威库已冻结
            self.assertIn("冻结", G.check(tid, "edit", 1, now=T0))       # 出口按库拒绝
            self.assertEqual(self.mirror()[tid]["status"], "active")      # 工人侧还是旧的……
            with self.assertRaisesRegex(G.Rejected, "镜像"):
                G.send_pending(post=self.post, now=LATER)                 # ……所以派发停下
            with mock.patch("sys.stdout"):
                self.assertEqual(G.main(["freeze", "--task", tid, "--reason", "再冻"]), 0)   # 已冻结的幂等返回
        self.assertEqual(self.posted[-1]["id"], f"{tid}:g1:start")
        G.send_pending(post=self.post, now=LATER)                         # 修好后重写镜像，旧代次事件作废
        self.assertEqual(self.mirror()[tid]["status"], "frozen")
        self.assertEqual(self.sent_states(tid), {"start": 1, "rework": -2})

    def test_cli_reports_mirror_failure_with_exit_code_2(self):
        tid = self.new_task()
        with mock.patch.object(G, "_write_shared", side_effect=OSError("磁盘满")), mock.patch("sys.stdout"):
            self.assertEqual(G.main(["revoke", "--task", tid]), 2)
        self.assertEqual(G.show(tid)[0]["status"], "revoked")

    def test_failed_freeze_commit_never_touches_mirror(self):
        """A 已在库里恢复、但镜像没写成；随后冻结 B 的事务失败：镜像对 A、B 都不变（不发布未提交的，也不夹带别人的）。"""
        a, b = self.new_task(), self.new_task(executor="codex")
        code = G.freeze(a, "推翻", now=T0)["freeze_code"]
        G.record_resolution(a, 1, "d-1", "关掉旧 PR", now=T0)
        with mock.patch.object(G, "_write_shared", side_effect=OSError("磁盘满")):
            self.assertIn("mirror_error", G.unfreeze(self.say(f"恢复 {a} {code}"), now=T0))
        before = self.status.read_text(encoding="utf-8")
        real_log = G.log

        def boom(con, task_id, what, now, **kw):
            if what == "冻结":
                raise RuntimeError("提交前出错")
            return real_log(con, task_id, what, now, **kw)
        with mock.patch.object(G, "log", side_effect=boom), self.assertRaises(RuntimeError):
            G.freeze(b, "推翻", now=T0)
        self.assertEqual(self.status.read_text(encoding="utf-8"), before)
        self.assertEqual((self.mirror()[a]["status"], self.mirror()[b]["status"]), ("frozen", "active"))
        self.assertEqual(G.show(b)[0]["status"], "active")

    def test_mirror_is_written_from_committed_state_only(self):
        tid = self.new_task()
        real, seen = G.write_status, []

        def spy(con, now):
            other = sqlite3.connect(self.db)              # 另一个连接只看得到已提交的
            seen.append(other.execute("SELECT status FROM grants WHERE task_id = ?", (tid,)).fetchone()[0])
            other.close()
            return real(con, now)
        with mock.patch.object(G, "write_status", side_effect=spy):
            G.freeze(tid, "推翻", now=T0)
        self.assertEqual(seen, ["frozen"])

    def test_approve_without_mirror_sends_nothing_and_stays_strict(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        with mock.patch.object(G, "write_status", side_effect=OSError("磁盘满")):
            out = G.approve(self.say(f"派单 {code}"), now=T0, post=self.post)
        self.assertIn("镜像", out["send"]["error"])
        self.assertFalse(self.status.exists())                     # 工人看不到这张单：比库更严
        self.assertEqual(self.posted, [])
        G.send_pending(post=self.post, now=T0)                     # 修好后补发：先写镜像再发
        self.assertIn(out["task_id"], self.mirror())
        self.assertEqual(len(self.posted), 1)

    def test_interleaved_senders_never_send_an_unpublished_task(self):
        """发送器 S1 正在发 Y 的事件时，另一路批准了 X 但自己的镜像没写成；S1 接着领取 X 时必须先把 X 写进镜像。"""
        y = self.new_task(post=down)
        x_code = G.propose("dt-sandbox", "codex", SCOPE, now=LATER)["code"]
        box = {}

        def s1_post(body):
            n = self.post(body)
            if not box:
                with mock.patch.object(G, "write_status", side_effect=OSError("磁盘满")):
                    box["x"] = G.approve(self.say(f"派单 {x_code}", LATER), now=LATER, post=self.post)
                self.assertIn("error", box["x"]["send"])
                self.assertNotIn(box["x"]["task_id"], self.mirror())
            return n
        G.send_pending(post=s1_post, now=LATER)
        x = box["x"]["task_id"]
        self.assertEqual([p["id"] for p in self.posted], [f"{y}:g1:start", f"{x}:g1:start"])   # post 里已核过镜像


class Check(Base):
    def test_check_rules(self):
        tid = self.new_task()
        self.assertIsNone(G.check(tid, "edit", 1, now=T0))
        self.assertIsNone(G.check(tid, "open_pr", 1, now=T0))
        self.assertIn("不允许", G.check(tid, "merge", 1, now=T0))
        self.assertIn("代次", G.check(tid, "edit", 2, now=T0))
        self.assertIn("过期", G.check(tid, "edit", 1, now=T0 + dt.timedelta(hours=13)))
        self.assertIn("没有", G.check("t-20261117-000000", "edit", 1, now=T0))
        self.assertIn("格式", G.check("../etc", "edit", 1, now=T0))


class Generations(Base):
    def test_freeze_voids_generation_and_unfreeze_bumps_it(self):
        tid = self.new_task()
        code = G.freeze(tid, "常浩推翻 d-1", now=T0)["freeze_code"]
        self.assertRegex(code, r"^[0-9a-f]{6}$")
        self.assertIn("冻结", G.check(tid, "edit", 1, now=T0))
        self.assertEqual(self.mirror()[tid]["status"], "frozen")
        self.assertNotIn(code, self.status.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(G.Rejected, "不能续接"):
            G.resume_dispatch(tid, "correction", "更正", now=T0, post=self.post)
        t1 = T0 + dt.timedelta(minutes=5)
        out = self.unfreeze_ok(tid, code, t1)
        self.assertEqual(out["generation"], 2)
        self.assertIn("作废", G.check(tid, "edit", 1, now=t1))          # 旧代次复活不了
        self.assertIsNone(G.check(tid, "edit", 2, now=t1))
        self.assertEqual(self.mirror()[tid]["voided_generations"], [1])

    def test_unfreeze_requires_recorded_resolution(self):
        tid = self.new_task()
        code = G.freeze(tid, "推翻", now=T0)["freeze_code"]
        with self.assertRaisesRegex(G.Rejected, "更正"):
            G.unfreeze(self.say(f"恢复 {tid} {code}"), now=T0)
        self.assertEqual(G.show(tid)[0]["status"], "frozen")

    def test_unfreeze_code_is_bound_to_this_freeze(self):
        tid = self.new_task()
        code1 = G.freeze(tid, "推翻一", now=T0)["freeze_code"]
        G.record_resolution(tid, 1, "d-1", "关掉旧 PR", now=T0)
        for text in (f"恢复 {tid}", f"恢复 {tid} 000000"):
            with self.subTest(text=text), self.assertRaises(G.Rejected):
                G.unfreeze(self.say(text), now=T0)
        G.unfreeze(self.say(f"恢复 {tid} {code1}"), now=T0)
        with mock.patch.object(G.secrets, "token_hex", return_value="bbbbbb"):
            code2 = G.freeze(tid, "推翻二", now=T0)["freeze_code"]
        G.record_resolution(tid, 2, "d-2", "保留", now=T0)
        if code1 != code2:
            with self.assertRaisesRegex(G.Rejected, "冻结码"):
                G.unfreeze(self.say(f"恢复 {tid} {code1}"), now=T0)   # 上一次冻结的码不能用
        G.unfreeze(self.say(f"恢复 {tid} {code2}"), now=T0)
        self.assertEqual(G.show(tid)[0]["generation"], 3)

    def test_record_resolution_rules(self):
        tid = self.new_task()
        with self.assertRaisesRegex(G.Rejected, "不是冻结"):
            G.record_resolution(tid, 1, "d-1", "x", now=T0)
        G.freeze(tid, "推翻", now=T0)
        with self.assertRaisesRegex(G.Rejected, "第 1 代"):
            G.record_resolution(tid, 2, "d-1", "x", now=T0)
        with self.assertRaises(G.Rejected):
            G.record_resolution(tid, 1, "", "x", now=T0)
        G.record_resolution(tid, 1, "d-1", "关掉旧 PR", now=T0)
        with self.assertRaisesRegex(G.Rejected, "记过"):
            G.record_resolution(tid, 1, "d-1", "改主意了", now=T0)

    def test_freeze_twice_keeps_the_same_code(self):
        tid = self.new_task()
        a = G.freeze(tid, "推翻", now=T0)
        b = G.freeze(tid, "又推翻", now=T0)
        self.assertEqual((b["freeze_code"], b.get("already")), (a["freeze_code"], True))

    def test_events_of_voided_generation_are_never_sent(self):
        tid = self.new_task()
        G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=down)
        code = G.freeze(tid, "推翻", now=T0)["freeze_code"]
        t1 = T0 + dt.timedelta(minutes=2)
        self.unfreeze_ok(tid, code, t1)
        self.assertEqual([o["sent"] for o in G.send_pending(post=self.post, now=t1)], [False])  # 旧代次的不发
        G.resume_dispatch(tid, "correction", "常浩重选了 B", now=t1, post=self.post)
        self.assertEqual(self.posted[-1]["id"], f"{tid}:g2:correction:1")

    def test_unfreeze_needs_owner_phrase(self):
        tid = self.new_task()
        code = G.freeze(tid, "推翻", now=T0)["freeze_code"]
        G.record_resolution(tid, 1, "d-1", "关掉旧 PR", now=T0)
        for text in (f"恢复{tid}x {code}", "恢复", f"恢复 {tid} {code} 顺便合并"):
            with self.subTest(text=text), self.assertRaises(G.Rejected):
                G.unfreeze(self.say(text), now=T0)
        with self.assertRaisesRegex(G.Rejected, "身份核实不通过"):
            G.unfreeze(self.say(f"恢复 {tid} {code}", sender="ou_someone_else_0001"), now=T0)

    def test_revoke_and_complete_are_terminal(self):
        a, b = self.new_task(), self.new_task(executor="codex")
        G.revoke(a, now=T0)
        G.complete(b, now=T0)
        self.assertIn("撤销", G.check(a, "edit", 1, now=T0))
        self.assertIn("完成", G.check(b, "edit", 1, now=T0))
        self.assertEqual((self.mirror()[a]["status"], self.mirror()[b]["status"]), ("revoked", "done"))
        for tid in (a, b):
            with self.assertRaisesRegex(G.Rejected, "终态"):
                G.freeze(tid, "x", now=T0)
        with self.assertRaisesRegex(G.Rejected, "不是冻结"):
            G.unfreeze(self.say(f"恢复 {a} abcdef"), now=T0)


class StopRuns(Base):
    """停单之后取消中台里这张单排队、在跑的轮次（方案 5.1 第 4 条）。中台库用临时 sqlite 文件，取消接口打桩。"""

    def setUp(self):
        super().setUp()
        import autoloop_config as C
        self.C = C
        self.hub_home = self.root / "hub"
        self.hub_home.mkdir()
        self.cancelled, self.fail = [], set()
        for p in (mock.patch.dict(os.environ, {"AGENT_AUTOLOOP_HOME": str(self.hub_home)}),
                  mock.patch.object(C, "hub", side_effect=self._hub)):
            p.start()
            self.addCleanup(p.stop)

    def _hub(self, method, path, body=None):
        rid = int(path.split("/")[3])
        self.assertEqual((method, path, body), ("POST", f"/v1/runs/{rid}/cancel", {}))
        if rid in self.fail:
            raise self.C.Rejected("中台回 HTTP 409")
        self.cancelled.append(rid)
        return {"changed": True}

    def hub_runs(self, rows):
        con = sqlite3.connect(self.hub_home / "hub.db")
        con.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, lane TEXT, rule TEXT, state TEXT)")
        con.executemany("INSERT INTO runs VALUES (?,?,?,?)", rows)
        con.commit()
        con.close()

    def test_only_this_tasks_live_runs_are_cancelled(self):
        a, b = self.new_task(), self.new_task()
        rule = "dispatch-dt-sandbox-claude"
        self.hub_runs([(1, a, rule, "done"), (2, a, rule, "claimed"), (3, a, rule, "queued"), (4, b, rule, "claimed"),
                       (5, a, "别的规则", "queued"), (6, a, rule, "cancelled"), (7, a, rule, "unknown")])
        with self.assertRaisesRegex(G.Rejected, "还有效"):
            G.stop_runs(a, now=T0)                             # 凭证有效的单不经这里停
        self.assertEqual(self.cancelled, [])
        G.revoke(a, now=T0)                                    # 库函数只收紧凭证，不自动停轮次
        self.assertEqual(self.cancelled, [])
        got = G.stop_runs(a, now=T0)
        self.assertEqual((got["stopped"], list(got["failed"])), ([2, 3], ["7"]))
        self.assertIn("中台不让取消", got["failed"]["7"])       # unknown：可能还在跑，中台的取消接口不收，报出来
        self.assertEqual(self.cancelled, [2, 3])               # 别的单、别的规则、已结束的都不碰
        self.assertEqual(self.sql("SELECT what FROM grant_log WHERE task_id = ? ORDER BY id", (a,))[-1][0], "停轮次")
        self.assertEqual(G.show(b)[0]["status"], "active")

    def test_cli_revoke_and_freeze_stop_runs_and_report(self):
        a, b, c = self.new_task(), self.new_task(executor="codex"), self.new_task()
        self.hub_runs([(11, a, "dispatch-dt-sandbox-claude", "claimed"), (12, b, "dispatch-dt-sandbox-codex", "queued"),
                       (13, b, "dispatch-dt-sandbox-codex", "claimed")])
        self.fail = {13}
        with mock.patch.object(G, "now_tz", return_value=T0):
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(G.main(["revoke", "--task", a, "--reason", "常浩说停"]), 0)
            got = json.loads(out.getvalue())
            self.assertEqual((got["status"], got["runs_stopped"]), ("revoked", [11]))
            self.assertNotIn("runs_stop_error", got)
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
                self.assertEqual(G.main(["freeze", "--task", b, "--reason", "先别动"]), 0)
            got = json.loads(out.getvalue())
            self.assertEqual((got["status"], got["runs_stopped"]), ("frozen", [12]))    # 取消不了的那一轮单独报，不改退出码
            self.assertIn("轮次 13", got["runs_stop_error"])
            self.assertRegex(got["freeze_code"], r"^[0-9a-f]{6}$")
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:           # 没有要停的：空列表
                self.assertEqual(G.main(["revoke", "--task", c]), 0)
            self.assertEqual(json.loads(out.getvalue())["runs_stopped"], [])
            with mock.patch("sys.stdout", new_callable=io.StringIO) as out:           # 完成不停轮次
                d = self.new_task()
                self.assertEqual(G.main(["complete", "--task", d]), 0)
            self.assertNotIn("runs_stopped", json.loads(out.getvalue()))
        self.assertEqual(self.cancelled, [11, 12])

    def test_hub_db_missing_is_reported_and_revoke_still_stands(self):
        a = self.new_task()
        with mock.patch.object(G, "now_tz", return_value=T0), mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(G.main(["revoke", "--task", a]), 0)       # 中台库不在：停单照样生效
        got = json.loads(out.getvalue())
        self.assertEqual(got["status"], "revoked")
        self.assertIn("中台库读不了", got["runs_stop_error"])
        self.assertNotIn("runs_stopped", got)
        self.assertEqual(G.show(a)[0]["status"], "revoked")
        self.assertEqual(self.cancelled, [])


class Resume(Base):
    def test_resume_only_while_valid(self):
        tid = self.new_task()
        out = G.resume_dispatch(tid, "redispatch", "中台放弃了", now=T0, post=self.post)
        self.assertEqual(out["event_key"], f"{tid}:g1:redispatch:1")
        self.assertEqual(self.posted[-1]["subject"], tid)
        with self.assertRaises(G.Rejected):
            G.resume_dispatch(tid, "merge", "x", now=T0, post=self.post)
        with self.assertRaises(G.Rejected):
            G.resume_dispatch(tid, "rework", " ", now=T0, post=self.post)
        with self.assertRaisesRegex(G.Rejected, "过期"):
            G.resume_dispatch(tid, "rework", "x", now=T0 + dt.timedelta(hours=13), post=self.post)

    def test_resume_binds_ref_and_generation(self):
        """L3 第 3 轮 P1-09：续接带决定号时记进同一条续接日志（同一个决定号只能续接一次）；发起方认定的代次不对就不续接。"""
        tid = self.new_task()
        with self.assertRaisesRegex(G.Rejected, "不是发起时的第 2 代"):
            G.resume_dispatch(tid, "rework", "x", now=T0, post=self.post, ref="d-1", generation=2)
        with self.assertRaisesRegex(G.Rejected, "决定号写法"):
            G.resume_dispatch(tid, "rework", "x", now=T0, post=self.post, ref="../x")
        out = G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=self.post, ref="d-1", generation=1)
        with self.assertRaisesRegex(G.Rejected, "已经续接过"):
            G.resume_dispatch(tid, "rework", "再来", now=T0, post=self.post, ref="d-1", generation=1)
        G.resume_dispatch(tid, "rework", "人工", now=T0, post=self.post)           # 不带决定号的照旧可以
        with G.tx() as con:
            refs = G.resume_refs(con, tid)
        self.assertEqual(list(refs), ["d-1"])
        self.assertEqual((refs["d-1"][0]["event"], refs["d-1"][0]["generation"]), (out["event_key"], 1))

    def test_resume_refuses_while_a_resume_event_is_unsent(self):
        """续审第 3 轮：这张单还有没确认送到中台的续接事件（sent 0 未发、2 发送中）就不再续接，
        锁放开以后同一张单也不会冒出第二个事件号；送到了（sent=1）才能再续接。开单的 start 不算。"""
        tid = self.new_task()

        def down(*a, **k):
            raise G.Rejected("连不上中台")
        first = G.resume_dispatch(tid, "redispatch", "中台放弃了", now=T0, post=down)["event_key"]
        for sent in (0, 2):
            with self.subTest(sent=sent):
                self.sql("UPDATE hub_events SET sent = ?, claimed_at = ? WHERE event_key = ?", (sent, G.iso(T0), first))
                with self.assertRaisesRegex(G.Rejected, "没确认送到中台.*" + re.escape(first)):
                    G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=self.post)
                n = self.sql("SELECT COUNT(*) FROM hub_events WHERE task_id = ? AND kind != 'start'", (tid,))[0][0]
                self.assertEqual(n, 1)
        self.sql("UPDATE hub_events SET sent = 1 WHERE event_key = ?", (first,))
        self.sql("UPDATE hub_events SET sent = 0 WHERE task_id = ? AND kind = 'start'", (tid,))   # 开单事件没送到不挡续接
        out = G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=self.post)
        self.assertEqual(out["event_key"], f"{tid}:g1:rework:1")

    def test_events_of_a_voided_generation_do_not_block_resume(self):
        """续审第 3 轮预审 P2-2：冻结时 sent=2 的事件原样留着；换到新代次后它已经发不出去（send-pending 会作废它），
        不算「没送到」，新代次照常续接。"""
        tid = self.new_task()

        def down(*a, **k):
            raise G.Rejected("连不上中台")
        first = G.resume_dispatch(tid, "redispatch", "中台放弃了", now=T0, post=down)["event_key"]
        self.sql("UPDATE hub_events SET sent = 2, claimed_at = ? WHERE event_key = ?", (G.iso(T0), first))
        G.freeze(tid, "推翻", now=T0)
        self.assertEqual(self.sql("SELECT sent FROM hub_events WHERE event_key = ?", (first,))[0][0], 2)
        self.sql("UPDATE grants SET status = 'active', generation = 2, freeze_code = NULL WHERE task_id = ?", (tid,))
        out = G.resume_dispatch(tid, "correction", "换代后更正", now=T0, post=self.post)    # 解冻要私聊口令，这里直接改库
        self.assertEqual(out["event_key"], f"{tid}:g2:correction:1")

    def test_lock_token_must_be_the_callers_own(self):
        """续审第 4 轮 P1-08-01：另一个进程持着这张单的任务锁时，本进程怎么传都续接不了——不传（自己拿被挡）、传布尔值、
        传伪造的凭证（锁文件开着但没拿到锁）、传别的单的凭证，一律拒绝，事件数不变；离开 with 的凭证作废；
        本进程自己拿到的凭证才认。「试着拿锁被挡」不再当成「调用方持锁」的证明。"""
        tid, other = self.new_task(), self.new_task()

        def events():
            return self.sql("SELECT COUNT(*) FROM hub_events WHERE task_id = ? AND kind != 'start'", (tid,))[0][0]
        with G.task_lock(tid) as expired:
            pass
        f = G._lock_file(tid)
        child = subprocess.Popen([sys.executable, "-c",                    # 独立进程持锁（原秘书 ② / ③ 或受信发布）
                                  "import fcntl, os, sys\n"
                                  "fd = os.open(sys.argv[1], os.O_RDWR)\n"
                                  "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                                  "print('held', flush=True)\n"
                                  "sys.stdin.read()\n", str(f)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            with self.assertRaisesRegex(G.Rejected, "另有入口正在处理"):
                G.resume_dispatch(tid, "redispatch", "x", now=T0, post=self.post)
            fd = os.open(f, os.O_RDWR)
            try:
                forged = G.TaskLock(tid, str(f), fd)
                with G.task_lock(other) as theirs:
                    for bad in (True, 1, "locked", forged, theirs, expired):
                        with self.subTest(bad=type(bad).__name__):
                            with self.assertRaisesRegex(G.Rejected, "不是调用方此刻持着"):
                                G.resume_dispatch(tid, "redispatch", "x", now=T0, post=self.post, lock=bad)
            finally:
                os.close(fd)
            self.assertEqual(events(), 0)
        finally:
            child.stdin.close()
            child.wait(10)
            child.stdout.close()                               # 不关会留一个 ResourceWarning: unclosed file
        with G.task_lock(tid) as mine:
            out = G.resume_dispatch(tid, "redispatch", "x", now=T0, post=self.post, lock=mine)
        self.assertEqual(out["event_key"], f"{tid}:g1:redispatch:1")
        self.assertEqual(events(), 1)

    def test_forged_or_expired_token_is_refused_without_taking_the_lock(self):
        """送审前预审：没人持锁时，手搓的凭证也不认，而且核实本身不会顺手拿锁（之后照常能拿）；离开 with 的凭证作废，
        哪怕它的描述符号被同一张单的新凭证复用、新凭证正持着锁，也不认；锁文件被删掉重建过（别人能在新文件上再拿一把），
        手里的凭证也不认。"""
        tid = self.new_task()
        f = G._lock_file(tid)
        with G.task_lock(tid):
            pass
        fd = os.open(f, os.O_RDWR)
        try:
            with self.assertRaisesRegex(G.Rejected, "不是调用方此刻持着"):
                G.resume_dispatch(tid, "redispatch", "x", now=T0, post=self.post, lock=G.TaskLock(tid, str(f), fd))
            with G.task_lock(tid):                                                # 没被顺手占住
                pass
        finally:
            os.close(fd)
        with G.task_lock(tid) as old:
            pass
        with G.task_lock(tid) as mine:
            self.assertEqual(old.fd, mine.fd)                                     # 描述符号复用了（最小可用号）
            with self.assertRaisesRegex(G.Rejected, "不是调用方此刻持着"):
                G.resume_dispatch(tid, "redispatch", "x", now=T0, post=self.post, lock=old)
            out = G.resume_dispatch(tid, "redispatch", "x", now=T0, post=self.post, lock=mine)
        self.assertEqual(out["event_key"], f"{tid}:g1:redispatch:1")
        self.sql("UPDATE hub_events SET sent = 1 WHERE event_key = ?", (out["event_key"],))
        with G.task_lock(tid) as mine:
            os.unlink(f)
            with G.task_lock(tid):                                                # 新文件上又拿到一把
                with self.assertRaisesRegex(G.Rejected, "不是调用方此刻持着"):
                    G.resume_dispatch(tid, "redispatch", "x", now=T0, post=self.post, lock=mine)

    def test_task_lock_lives_next_to_the_grants_db(self):
        """续审第 3 轮：任务锁文件在凭证库旁边的 task-locks/，写同一个凭证库的进程（续接、受信发布、秘书 ② / ③）必然拿同一把，
        不靠各自的环境变量对齐；被持着时续接拒绝、另一次拿锁抛 Busy；放了就能拿。"""
        tid = self.new_task()
        f = self.db.parent / "task-locks" / f"{tid}.lock"
        with G.task_lock(tid):
            self.assertTrue(f.is_file())
            fd = os.open(f, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)
            with self.assertRaises(G.Busy):
                with G.task_lock(tid):
                    pass
            with self.assertRaisesRegex(G.Rejected, "不能续接：.*正在处理"):
                G.resume_dispatch(tid, "redispatch", "x", now=T0, post=self.post)
        with G.task_lock(tid):
            pass
        with self.assertRaisesRegex(G.Rejected, "单号写法"):
            with G.task_lock("../x"):
                pass

    def test_log_is_append_only_history(self):
        tid = self.new_task()
        G.freeze(tid, "推翻", now=T0)
        G.record_resolution(tid, 1, "d-1", "关掉旧 PR", now=T0)
        whats = [r[0] for r in self.sql("SELECT what FROM grant_log WHERE task_id = ? ORDER BY id", (tid,))]
        self.assertEqual(whats, ["签发", "冻结", "更正"])


class Cli(Base):
    def test_cli_check_and_show(self):
        tid = self.new_task()
        # 命令行用真实的当前时间；凭证在 T0 签发、12 小时有效，固定「现在」，否则过了 11-17 这项就判过期（11-18 CI 失败）
        with mock.patch.object(G, "now_tz", return_value=T0 + dt.timedelta(minutes=1)), mock.patch("sys.stdout") as out:
            self.assertEqual(G.main(["check", "--task", tid, "--action", "edit", "--generation", "1"]), 0)
            self.assertEqual(G.main(["check", "--task", tid, "--action", "merge", "--generation", "1"]), 1)
            self.assertEqual(G.main(["show", "--task", tid]), 0)
        self.assertTrue(out.write.called)

    def test_cli_check_rejects_expired_grant(self):
        tid = self.new_task()
        expired = T0 + dt.timedelta(hours=13)
        with mock.patch.object(G, "now_tz", return_value=expired), mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            self.assertEqual(G.main(["check", "--task", tid, "--action", "edit", "--generation", "1"]), 1)
        self.assertEqual(json.loads(out.getvalue()), {"ok": False, "why": "凭证已过期"})

    def test_no_cli_entry_for_resolution(self):
        with self.assertRaises(SystemExit), mock.patch("sys.stderr"):
            G.main(["record-resolution", "--task", "t-20261117-000000"])


if __name__ == "__main__":
    unittest.main()
