"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

autoloop_grants.py（任务凭证与派单网关，#195 批 0B-1）：两步批准、凭证判定、代次作废、冻结 / 恢复 / 撤销、
状态镜像、只在凭证有效时续接、先记账后发事件。凭证库、镜像、名册都放临时目录；飞书回读打桩 fetch_message
（走真实的 twin_identity.verify_message）；中台用假的 post。不联网、不碰 ~/Library/Application Support/DigitalTwin。
"""
import datetime as dt
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import autoloop_grants as G  # noqa: E402

OWNER = "ou_fixture_owner_0001"
CHAT = "oc_fixture_p2p_0001"
T0 = dt.datetime(2026, 11, 17, 10, 0, tzinfo=G.TZ)
SCOPE = "目标：给 dt-sandbox 的 README 加一节\n范围：只改 README.md\n验收：PR 里只有 README.md"


def raw_msg(mid, text, when, sender=OWNER, chat=CHAT):
    return {"message_id": mid, "chat_id": chat, "content": text, "create_time": when.astimezone().strftime("%Y-%m-%d %H:%M"),
            "deleted": False, "msg_type": "text", "sender": {"id": sender, "id_type": "open_id", "sender_type": "user"}}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-grants-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "dt/autoloop/grants.db"
        self.status = self.root / "shared/grants/status.json"
        roster = self.root / "projects.json"
        roster.write_text(json.dumps({
            "projects": [{"id": "digital-twin", "github": "o/digital-twin", "category": "活跃", "write_allowed": True},
                         {"id": "agent-autoloop", "github": "o/agent-autoloop", "category": "活跃", "write_allowed": False}],
            "inventory": [{"path": "github:o/dt-sandbox", "category": "沙盒", "sandbox": True},
                          {"path": "github:o/old", "category": "待删"}]}), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DT_OWNER", "DT_GRANTS", "HERMES_SESSION"))}
        env.update(DT_GRANTS_DB=str(self.db), DT_GRANTS_STATUS=str(self.status), DT_OWNER_OPEN_ID=OWNER, DT_OWNER_CHAT_ID=CHAT)
        for p in (mock.patch.dict(os.environ, env, clear=True), mock.patch.object(G, "ROSTER", roster),
                  mock.patch.object(G, "fetch_message", side_effect=self._fetch)):
            p.start()
            self.addCleanup(p.stop)
        self.msgs, self.n, self.posted = {}, 0, []

    def _fetch(self, mid):
        m = self.msgs.get(mid)
        return None if m is None else G.I.normalize(m) | {"text": m["content"]}

    def say(self, text, when=T0, **kw):
        self.n += 1
        mid = f"om_fixturemsg{self.n:04d}"
        self.msgs[mid] = raw_msg(mid, text, when, **kw)
        return mid

    def post(self, body):
        self.posted.append(body)
        return 100 + len(self.posted)

    def new_task(self, when=T0, repo="dt-sandbox", executor="claude"):
        code = G.propose(repo, executor, SCOPE, hours=12, now=when)["code"]
        return G.approve(self.say(f"派单 {code}", when), now=when, post=self.post)["task_id"]

    def mirror(self):
        return json.loads(self.status.read_text(encoding="utf-8"))["tasks"]


class Approval(Base):
    def test_two_step_issues_grant_and_sends_start(self):
        tid = self.new_task()
        self.assertRegex(tid, G.TASK_ID)
        (g,) = G.show(tid)
        self.assertEqual((g["status"], g["generation"], g["actions"]), ("active", 1, list(G.ACTIONS)))
        self.assertEqual(g["github"], "o/dt-sandbox")
        self.assertEqual(g["rule"], "dispatch-dt-sandbox-claude")
        self.assertEqual(len(self.posted), 1)
        ev = self.posted[0]
        self.assertEqual((ev["subject"], ev["type"], ev["id"]), (tid, "dispatch.dt-sandbox.claude.write", f"{tid}:g1:start"))
        self.assertIn("只改 README.md", ev["detail"])

    def test_phrase_must_match_a_live_proposal(self):
        G.propose("dt-sandbox", "claude", SCOPE, now=T0)
        for text in ("派单 000000", "派单", "好的派吧", "派单 abcdef 顺便合并"):
            with self.subTest(text=text), self.assertRaises(G.Rejected):
                G.approve(self.say(text), now=T0, post=self.post)
        self.assertEqual(G.show(), [])

    def test_code_binds_content(self):
        a = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        b = G.propose("dt-sandbox", "codex", SCOPE, now=T0)["code"]
        c = G.propose("dt-sandbox", "claude", SCOPE + "\n顺便升级依赖", now=T0)["code"]
        self.assertEqual(len({a, b, c}), 3)

    def test_proposal_expires(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        later = T0 + dt.timedelta(minutes=31)
        with self.assertRaisesRegex(G.Rejected, "过期"):
            G.approve(self.say(f"派单 {code}", later), now=later, post=self.post)

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

    def test_same_message_cannot_approve_a_reproposed_identical_task(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]
        mid = self.say(f"派单 {code}")
        G.approve(mid, now=T0, post=self.post)
        self.assertEqual(G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"], code)   # 同样内容，同一个确认码
        with self.assertRaisesRegex(G.Rejected, "已经用过"):
            G.approve(mid, now=T0, post=self.post)
        self.assertEqual(len(G.show()), 1)

    def test_repo_and_executor_and_hours_checked(self):
        for repo in ("agent-autoloop", "old", "nope", "../x"):
            with self.subTest(repo=repo), self.assertRaisesRegex(G.Rejected, "名册"):
                G.propose(repo, "claude", SCOPE, now=T0)
        with self.assertRaises(G.Rejected):
            G.propose("dt-sandbox", "gpt", SCOPE, now=T0)
        with self.assertRaises(G.Rejected):
            G.propose("dt-sandbox", "claude", SCOPE, hours=100, now=T0)
        with self.assertRaises(G.Rejected):
            G.propose("dt-sandbox", "claude", "", now=T0)
        self.assertEqual(G.propose("digital-twin", "codex", SCOPE, now=T0)["repo"], "digital-twin")

    def test_files_are_private_and_mirror_is_readable(self):
        self.new_task()
        self.assertEqual(stat.S_IMODE(self.db.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.db.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.status.stat().st_mode), 0o644)
        text = self.status.read_text(encoding="utf-8")
        self.assertNotIn("只改 README.md", text)                 # 范围原文不进镜像，只给哈希
        self.assertNotIn("om_fixture", text)                     # 批准消息号不进镜像

    def test_hub_unreachable_keeps_event_for_resend(self):
        code = G.propose("dt-sandbox", "claude", SCOPE, now=T0)["code"]

        def down(body):
            raise G.Rejected("连不上中台")
        out = G.approve(self.say(f"派单 {code}"), now=T0, post=down)
        self.assertIn("error", out["send"])
        self.assertEqual(G.show(out["task_id"])[0]["status"], "active")
        sent = G.send_pending(post=self.post)
        self.assertEqual([s["sent"] for s in sent], [True])
        self.assertEqual(G.send_pending(post=self.post), [])      # 发过就不再发


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
        G.freeze(tid, "常浩推翻 d-1", now=T0)
        self.assertIn("冻结", G.check(tid, "edit", 1, now=T0))
        self.assertEqual(self.mirror()[tid]["status"], "frozen")
        with self.assertRaisesRegex(G.Rejected, "不能续接"):
            G.resume_dispatch(tid, "correction", "更正", now=T0, post=self.post)
        t1 = T0 + dt.timedelta(minutes=5)
        out = G.unfreeze(self.say(f"恢复 {tid}", t1), now=t1)
        self.assertEqual(out["generation"], 2)
        self.assertIn("作废", G.check(tid, "edit", 1, now=t1))          # 旧代次复活不了
        self.assertIsNone(G.check(tid, "edit", 2, now=t1))
        self.assertEqual(self.mirror()[tid]["voided_generations"], [1])

    def test_events_of_voided_generation_are_never_sent(self):
        tid = self.new_task()

        def down(body):
            raise G.Rejected("连不上中台")
        G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=down)
        G.freeze(tid, "推翻", now=T0)
        t1 = T0 + dt.timedelta(minutes=1)
        G.unfreeze(self.say(f"恢复 {tid}", t1), now=t1)
        self.assertEqual(G.send_pending(post=self.post), [])           # 冻结时作废的旧代次事件，恢复后也不发
        G.resume_dispatch(tid, "correction", "常浩重选了 B", now=t1, post=self.post)
        self.assertEqual(self.posted[-1]["id"], f"{tid}:g2:correction:1")

    def test_send_pending_rechecks_grant_even_if_ledger_says_unsent(self):
        """冻结会把没发的事件标作废；就算账本被弄回「未发」，发之前还要再核凭证。"""
        tid = self.new_task()

        def down(body):
            raise G.Rejected("连不上中台")
        G.resume_dispatch(tid, "rework", "审查没过", now=T0, post=down)
        G.freeze(tid, "推翻", now=T0)
        con = sqlite3.connect(self.db)
        con.execute("UPDATE hub_events SET sent = 0 WHERE task_id = ? AND kind = 'rework'", (tid,))
        con.commit()
        con.close()
        out = G.send_pending(post=self.post)
        self.assertEqual([o["sent"] for o in out], [False])
        self.assertEqual([p["id"] for p in self.posted], [f"{tid}:g1:start"])

    def test_unfreeze_needs_owner_phrase(self):
        tid = self.new_task()
        G.freeze(tid, "推翻", now=T0)
        for text in (f"恢复{tid}x", "恢复", f"恢复 {tid} 顺便合并"):
            with self.subTest(text=text), self.assertRaises(G.Rejected):
                G.unfreeze(self.say(text), now=T0)
        with self.assertRaisesRegex(G.Rejected, "身份核实不通过"):
            G.unfreeze(self.say(f"恢复 {tid}", sender="ou_someone_else_0001"), now=T0)

    def test_revoke_and_complete_are_terminal(self):
        a, b = self.new_task(), self.new_task(executor="codex")
        G.revoke(a, now=T0)
        G.complete(b, now=T0)
        self.assertIn("撤销", G.check(a, "edit", 1, now=T0))
        self.assertIn("完成", G.check(b, "edit", 1, now=T0))
        for tid in (a, b):
            with self.assertRaisesRegex(G.Rejected, "终态"):
                G.freeze(tid, "x", now=T0)
        with self.assertRaisesRegex(G.Rejected, "不是冻结"):
            G.unfreeze(self.say(f"恢复 {a}"), now=T0)


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

    def test_log_is_append_only_history(self):
        tid = self.new_task()
        G.freeze(tid, "推翻", now=T0)
        con = sqlite3.connect(self.db)
        whats = [r[0] for r in con.execute("SELECT what FROM grant_log WHERE task_id = ? ORDER BY id", (tid,))]
        con.close()
        self.assertEqual(whats, ["签发", "冻结"])


class Cli(Base):
    def test_cli_check_and_show(self):
        tid = self.new_task()
        with mock.patch("sys.stdout") as out:
            self.assertEqual(G.main(["check", "--task", tid, "--action", "edit", "--generation", "1"]), 0)
            self.assertEqual(G.main(["check", "--task", tid, "--action", "merge", "--generation", "1"]), 1)
            self.assertEqual(G.main(["show", "--task", tid]), 0)
        self.assertTrue(out.write.called)


if __name__ == "__main__":
    unittest.main()
