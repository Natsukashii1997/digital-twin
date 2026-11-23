"""出口账本（#195 批 0D 恢复门）自测：先记账再动手、做成的不重做、结果不明冻结、强杀后扫描、和凭证冻结串行、只读对账。
全部在临时目录；恢复闸换成放行（闸本身在 test_autoloop_recovery 测）；不碰正式中台、正式状态目录。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
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
sys.path.insert(0, str(HERE))
import autoloop_exits as E  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_recovery as R  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401

TASK = "t-20261118-abcdef"


def isolate(case, root, open_gate=True):
    """出口账本、凭证库、镜像、锁定版本都指到临时目录；open_gate 时恢复闸放行。"""
    env = {"DT_EXITS_DB": str(root / "dt/autoloop/exits.db"), "DT_GRANTS_DB": str(root / "dt/autoloop/grants.db"),
           "DT_GRANTS_STATUS": str(root / "shared/grants/status.json"), "DT_GRANTS_TASKS": str(root / "shared/tasks"),
           "DT_HUB_PIN": str(root / "dt/autoloop/hub-pin.json"), "DT_AUTOLOOP_CARDS_DB": str(root / "dt/autoloop/cards.db"),
           "DT_HUB": "http://127.0.0.1:9", "AGENT_AUTOLOOP_HOME": str(root / "hub")}
    patches = [mock.patch.dict(os.environ, env)]
    if open_gate:
        patches.append(mock.patch.object(R, "ensure_open", lambda readers=None: None))
        patches.append(mock.patch.object(R, "admit", lambda con, epoch: None))
    for p in patches:
        p.start()
        case.addCleanup(p.stop)


def add_grant(task=TASK, status="active", generation=1, hours=2):
    now = G.now_tz()
    with G.tx() as con:
        con.execute("INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task, "dt-sandbox", "o/dt-sandbox", "五要素", G.sha256("五要素"), "claude",
                     G.rule_name("dt-sandbox", "claude"), json.dumps(list(G.ACTIONS)),
                     G.iso(now + dt.timedelta(hours=hours)), status, generation, G.iso(now), None, "om_x", G.iso(now),
                     G.iso(now)))


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-exits-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        isolate(self, self.root)
        self.calls = []

    def act(self, out=None, exc=None):
        def do():
            self.calls.append(1)
            if exc:
                raise exc
            return out or {"ok": 1}
        return do

    def row(self, key):
        with E.db(write=False) as con:
            r = con.execute("SELECT * FROM exits WHERE key = ?", (key,)).fetchone()
            return dict(r) if r else None

    def dirty(self):
        with E.db(write=False) as con:
            r = con.execute("SELECT dirty FROM gate WHERE id = 1").fetchone()
            return r[0] if r else 0


class Guard(Base):
    def test_done_is_recorded_and_never_redone(self):
        out = E.guard("open_pr", "open_pr:t1:b:h1", "task:t1", {"x": 1}, self.act({"pr": "u"}))
        self.assertEqual((out["pr"], out["again"]), ("u", False))
        again = E.guard("open_pr", "open_pr:t1:b:h1", "task:t1", {"x": 1}, self.act({"pr": "other"}))
        self.assertEqual((again["pr"], again["again"]), ("u", True))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.row("open_pr:t1:b:h1")["state"], "done")

    def test_definite_failure_can_be_retried(self):
        with self.assertRaises(E.Definite):
            E.guard("merge", "merge:o/r:1:h", "repo:o/r", {}, self.act(exc=E.Definite("拒绝")))
        self.assertEqual(self.row("merge:o/r:1:h")["state"], "failed")
        E.guard("merge", "merge:o/r:1:h", "repo:o/r", {}, self.act())
        r = self.row("merge:o/r:1:h")
        self.assertEqual((r["state"], r["attempts"]), ("done", 2))
        self.assertEqual(self.dirty(), 0)

    def test_unknown_freezes_key_and_scope_and_closes_gate(self):
        with self.assertRaises(TimeoutError):
            E.guard("merge", "merge:o/r:1:h", "repo:o/r", {}, self.act(exc=TimeoutError("慢")))
        self.assertEqual(self.row("merge:o/r:1:h")["state"], "unknown")
        self.assertGreater(self.dirty(), 0)
        with self.assertRaisesRegex(E.Refused, "结果不明"):
            E.guard("merge", "merge:o/r:1:h", "repo:o/r", {}, self.act())
        with self.assertRaisesRegex(E.Refused, "已冻结"):
            E.guard("merge", "merge:o/r:2:h", "repo:o/r", {}, self.act())
        E.guard("merge", "merge:o/r2:3:h", "repo:o/r2", {}, self.act())          # 别的仓库照常
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(E.show()["frozen"], ["repo:o/r"])

    def test_same_scope_runs_one_at_a_time(self):
        now = E.now_tz()
        with E.db() as con:                                       # 另一个还活着的进程正在做（用本进程的号和启动时间）
            con.execute("INSERT INTO exits (key, kind, scope, target, state, pid, proc, boot, created_at, started_at, "
                        "updated_at) VALUES ('push_branch:t1:b:h0','push_branch','task:t1','{}','executing',?,?,?,?,?,?)",
                        (os.getpid(), E.proc_start(os.getpid()), E.boot_id(), E.iso(now), E.iso(now), E.iso(now)))
        with self.assertRaisesRegex(E.Refused, "正在进行"):
            E.guard("open_pr", "open_pr:t1:b:h0", "task:t1", {}, self.act())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.row("push_branch:t1:b:h0")["state"], "executing")    # 活着的不被当成强杀

    def test_killed_executor_is_swept_to_unknown(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        now = E.now_tz()
        with E.db() as con:
            con.execute("INSERT INTO exits (key, kind, scope, target, state, pid, proc, boot, created_at, started_at, "
                        "updated_at) VALUES ('push_branch:t1:b:h0','push_branch','task:t1','{}','executing',?,?,?,?,?,?)",
                        (p.pid, "Thu Jan  1 00:00:00 1970", E.boot_id(), E.iso(now), E.iso(now), E.iso(now)))
        with self.assertRaisesRegex(E.Refused, "已冻结"):
            E.guard("open_pr", "open_pr:t1:b:h0", "task:t1", {}, self.act())
        self.assertEqual(self.row("push_branch:t1:b:h0")["state"], "unknown")
        self.assertGreater(self.dirty(), 0)

    def test_only_confirmed_end_counts_as_dead(self):
        """审查 P1-02：只有确认结束（换了开机、进程不在或启动时间对不上）才算死；跑得久、查不了都当还活着。"""
        now = E.now_tz()
        row = {"boot": "1", "started_at": E.iso(now), "pid": os.getpid(), "proc": E.proc_start(os.getpid())}
        self.assertFalse(E.alive(row, now, boot="2"))
        self.assertTrue(E.alive(row, now, boot="1"))
        self.assertTrue(E.alive(row, now + E.HARD_STALE + dt.timedelta(seconds=1), boot="1"))   # 跑得久 ≠ 结束
        self.assertTrue(E.alive({**row, "boot": "?"}, now, boot="?"))                             # 开机读不到：看进程
        self.assertFalse(E.alive({**row, "proc": "Thu Jan  1 00:00:00 1970"}, now, boot="1"))
        with mock.patch.object(E, "proc_start", return_value="?"):
            self.assertTrue(E.alive({**row, "proc": "x"}, now, boot="1"))                         # 查不了 ≠ 不在

    def test_long_running_live_action_stays_frozen_and_is_reported(self):
        old = E.iso(E.now_tz() - E.HARD_STALE - dt.timedelta(minutes=1))
        with E.db() as con:
            con.execute("INSERT INTO exits (key, kind, scope, target, state, pid, proc, boot, created_at, started_at, "
                        "updated_at) VALUES ('push_branch:t1:b:h0','push_branch','task:t1','{}','executing',?,?,?,?,?,?)",
                        (os.getpid(), E.proc_start(os.getpid()), E.boot_id(), old, old, old))
        with self.assertRaisesRegex(E.Refused, "正在进行"):
            E.guard("open_pr", "open_pr:t1:b:h0", "task:t1", {}, self.act())
        E.reconcile()
        self.assertEqual(self.row("push_branch:t1:b:h0")["state"], "executing")       # 不扫成不明、不给重做资格
        self.assertEqual(E.show()["stalled"], ["push_branch:t1:b:h0"])
        self.assertEqual(self.calls, [])

    def test_same_key_cannot_change_target(self):
        with self.assertRaises(E.Definite):
            E.guard("merge", "merge:o/r:1:h", "repo:o/r", {"number": 1}, self.act(exc=E.Definite("拒")))
        with self.assertRaisesRegex(E.Refused, "换了目标"):
            E.guard("merge", "merge:o/r:1:h", "repo:o/r", {"number": 2}, self.act())
        E.guard("merge", "merge:o/r:1:h", "repo:o/r", {"number": 1}, self.act())
        with self.assertRaisesRegex(E.Refused, "换了目标"):                           # 做成以后也不许换
            E.guard("merge", "merge:o/r:1:h", "repo:o/r", {"number": 3}, self.act())
        self.assertEqual(len(self.calls), 2)

    def test_boot_id_falls_back_to_linux_proc(self):
        """CI 在 Linux 上跑：没有 kern.boottime 时读 /proc 的 boot_id；都没有才是 '?'（当成进程不在）。"""
        with mock.patch.object(E.subprocess, "run", side_effect=OSError), \
                mock.patch.object(E.Path, "read_text", return_value="3f2a-boot\n"):
            self.assertEqual(E.boot_id(), "3f2a-boot")
        with mock.patch.object(E.subprocess, "run", side_effect=OSError), \
                mock.patch.object(E.Path, "read_text", side_effect=OSError):
            self.assertEqual(E.boot_id(), "?")

    def test_bad_key_or_scope_is_refused(self):
        for kind, key, scope in (("push_branch", "open_pr:x", "task:t"), ("nuke", "nuke:x", "task:t"),
                                 ("merge", "merge:x y", "task:t"), ("merge", "merge:x", "everything")):
            with self.assertRaises(E.Refused):
                E.guard(kind, key, scope, {}, self.act())
        self.assertEqual(self.calls, [])

    def test_gate_is_checked_before_anything(self):
        with mock.patch.object(R, "ensure_open", side_effect=E.Refused("恢复闸关着：电脑睡醒过")):
            with self.assertRaisesRegex(E.Refused, "睡醒"):
                E.guard("merge", "merge:o/r:1:h", "repo:o/r", {}, self.act())
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.row("merge:o/r:1:h"))

    def test_other_schema_is_refused(self):
        E.db_path().parent.mkdir(parents=True)
        import sqlite3
        con = sqlite3.connect(E.db_path())
        con.execute("PRAGMA user_version = 9")
        con.execute("CREATE TABLE x (a)")
        con.close()
        with self.assertRaisesRegex(E.Refused, "第 9 版"):
            E.show()

    def test_db_is_private(self):
        E.show()
        self.assertEqual(E.db_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(E.db_path().parent.stat().st_mode & 0o777, 0o700)


class WithGrant(Base):
    def test_valid_grant_passes_and_frozen_grant_stops(self):
        add_grant()
        E.guard("push_branch", f"push_branch:{TASK}:b:h1", f"task:{TASK}", {"github": "o/dt-sandbox", "branch": f"claude/{TASK}"}, self.act(),
                task=(TASK, "push_branch", 1))
        G.freeze(TASK, "测试")
        with self.assertRaisesRegex(E.Refused, "冻结"):
            E.guard("open_pr", f"open_pr:{TASK}:b:h1", f"task:{TASK}", {"github": "o/dt-sandbox", "branch": f"claude/{TASK}"}, self.act(), task=(TASK, "open_pr", 1))
        with self.assertRaisesRegex(E.Refused, "没有"):
            E.guard("open_pr", "open_pr:t-20261118-000000:b:h1", "task:t-20261118-000000", {}, self.act(),
                    task=("t-20261118-000000", "open_pr", 1))
        self.assertEqual(len(self.calls), 1)

    def test_old_generation_and_missing_action_stop(self):
        add_grant(generation=2)
        with self.assertRaisesRegex(E.Refused, "代次"):
            E.guard("push_branch", f"push_branch:{TASK}:b:h1", f"task:{TASK}", {}, self.act(),
                    task=(TASK, "push_branch", 1))
        with self.assertRaisesRegex(E.Refused, "不允许 merge"):
            E.guard("merge", f"merge:{TASK}:1:h", f"task:{TASK}", {}, self.act(), task=(TASK, "merge", 2))
        self.assertEqual(self.calls, [])

    def test_freeze_reports_actions_already_started(self):
        """executing 在凭证库写锁里落盘：冻结排在它后面，输出里列出这个撤不回的动作。"""
        add_grant()
        seen = {}

        def do():
            seen["freeze"] = G.freeze(TASK, "常浩推翻")       # 动作进行中常浩冻结（拿得到写锁，说明动手时已放锁）
            return {"ok": 1}
        E.guard("push_branch", f"push_branch:{TASK}:b:h1", f"task:{TASK}", {"github": "o/dt-sandbox", "branch": f"claude/{TASK}"}, do, task=(TASK, "push_branch", 1))
        self.assertEqual(seen["freeze"]["in_flight"], [{"key": f"push_branch:{TASK}:b:h1", "state": "executing"}])
        self.assertNotIn("in_flight", G.revoke(TASK, "收尾"))     # 做完以后就没有在途的了

    def test_target_must_belong_to_the_grant(self):
        """审查 P1-07：有效凭证配上别的仓库、别的分支、别的单号，一律拒绝。"""
        add_grant()
        ok = {"github": "o/dt-sandbox", "branch": f"claude/{TASK}"}
        for key, scope, target, why in (
                (f"push_branch:{TASK}:b:h1", f"task:{TASK}", {**ok, "github": "o/other"}, "目标仓库"),
                (f"push_branch:{TASK}:b:h1", f"task:{TASK}", {**ok, "branch": "claude/t-20261118-111111"}, "分支"),
                (f"push_branch:{TASK}:b:h1", f"task:{TASK}", {"branch": ok["branch"]}, "目标仓库"),
                ("push_branch:t-20261118-222222:b:h1", f"task:{TASK}", ok, "不是这张单"),
                (f"push_branch:{TASK}:b:h1", "task:t-20261118-222222", ok, "不是这张单")):
            with self.assertRaisesRegex(E.Refused, why):
                E.guard("push_branch", key, scope, target, self.act(), task=(TASK, "push_branch", 1))
        self.assertEqual(self.calls, [])

    def test_freeze_without_ledger_does_not_create_it(self):
        add_grant()
        G.freeze(TASK, "测试")
        self.assertFalse(E.db_path().exists())


class Reconcile(Base):
    def unknown(self, key, kind="push_branch", scope="task:t1", target=None):
        with self.assertRaises(RuntimeError):
            E.guard(kind, key, scope, target or {}, self.act(exc=RuntimeError("断")))

    def test_verdicts(self):
        self.unknown("push_branch:t1:b:h1", scope="task:t1")
        self.unknown("push_branch:t2:b:h1", scope="task:t2")
        self.unknown("push_branch:t3:b:h1", scope="task:t3")
        self.unknown("push_branch:t4:b:h1", scope="task:t4")

        def reader(t, row, now):
            tid = row["key"].split(":")[1]
            if tid == "t1":
                return "done", {"remote_head": "h1"}
            if tid == "t2":
                return "failed", {"remote_head": None}
            if tid == "t3":
                return None, {"remote_head": "zz"}
            raise E.Unreachable("断网")
        notes = {n["key"].split(":")[1]: n["state"] for n in E.reconcile({"push_branch": reader})}
        self.assertEqual(notes, {"t1": "done", "t2": "failed", "t3": "unreconciled", "t4": "unknown"})
        self.assertEqual(E.show()["frozen"], ["task:t3", "task:t4"])
        E.reconcile({"push_branch": lambda t, row, now: ("failed", {})})          # 审查 P1-01：核不清的不再自动改
        self.assertEqual(self.row("push_branch:t3:b:h1")["state"], "unreconciled")
        with self.assertRaisesRegex(E.Refused, "核不清"):
            E.guard("push_branch", "push_branch:t3:b:h1", "task:t3", {}, self.act())
        again = E.guard("push_branch", "push_branch:t1:b:h1", "task:t1", {}, self.act())
        self.assertTrue(again["again"])                                   # 对账确认做成的：不重做
        E.guard("push_branch", "push_branch:t2:b:h1", "task:t2", {}, self.act())   # 确认没做成的：可以重做
        self.assertEqual(len(self.calls), 5)

    def test_settle_needs_confirm_and_only_unreconciled(self):
        self.unknown("merge:o/r:1:h", kind="merge", scope="repo:o/r")
        with self.assertRaisesRegex(E.Refused, "confirm"):
            E.settle("merge:o/r:1:h", "done", "看过", confirm=False)
        with self.assertRaisesRegex(E.Refused, "先对账"):
            E.settle("merge:o/r:1:h", "done", "看过", confirm=True)
        E.reconcile({"merge": lambda t, r, n: (None, {})})
        with self.assertRaisesRegex(E.Refused, "核不清.*settle --key merge:o/r:1:h"):     # 预审 P2：不再叫人「先对账」
            E.guard("merge", "merge:o/r:1:h", "repo:o/r", {}, self.act())
        with self.assertRaisesRegex(E.Refused, "核不清（等常浩 settle 结算）"):
            E.guard("merge", "merge:o/r:2:h", "repo:o/r", {}, self.act())
        with self.assertRaisesRegex(E.Refused, "依据"):
            E.settle("merge:o/r:1:h", "done", " ", confirm=True)
        self.assertEqual(E.settle("merge:o/r:1:h", "failed", "GitHub 上看过没合并", confirm=True)["state"], "failed")
        self.assertEqual(E.show()["frozen"], [])

    def test_too_many_rounds_can_be_settled(self):
        """#338 乙：真走一遍对账 → 核不清 → 常浩 settle 结算；范围在结算前冻结。"""
        key = "send_feishu:card-9"
        t = {"chat": "oc_x", "marker": "abcdef0123456789", "msg_type": "interactive"}
        self.unknown(key, kind="send_feishu", scope="card:9", target=t)
        n = iter(range(10 ** 6))
        with mock.patch.object(E, "lark_read", side_effect=lambda a, w: {"messages": [], "has_more": True, "page_token": f"t{next(n)}"}):
            notes = E.reconcile(now=E.now_tz() + dt.timedelta(minutes=10))
        self.assertEqual([x["state"] for x in notes if x["key"] == key], ["unreconciled"])
        with self.assertRaisesRegex(E.Refused, "核不清"):
            E.guard("send_feishu", "send_feishu:card-9b", "card:9", {}, self.act())
        self.assertEqual(E.settle(key, "failed", "私聊里核过没发出", confirm=True)["state"], "failed")

    def test_settle_done_writes_the_ref_and_feishu_needs_one(self):
        """第 2 轮预审 P2-B2：发飞书核不清、常浩结算成做成了，必须写核到的消息号，同一动作之后直接回它（桥接要拿它报送达）。"""
        key = "send_feishu:card-7"
        self.unknown(key, kind="send_feishu", scope="card:7")
        E.reconcile({"send_feishu": lambda t, r, n: (None, {})})
        with self.assertRaisesRegex(E.Refused, "--ref"):
            E.settle(key, "done", "私聊里看到了", confirm=True)
        with self.assertRaisesRegex(E.Refused, "不写 --ref"):
            E.settle(key, "failed", "没看到", confirm=True, ref="om_x")
        self.assertEqual(E.settle(key, "done", "私聊里看到了", confirm=True, ref="om_x")["ref"], "om_x")
        got = E.guard("send_feishu", key, "card:7", {}, self.act())
        self.assertEqual((got["again"], got["message_id"]), (True, "om_x"))
        self.unknown("open_pr:t1:b:h", kind="open_pr", scope="task:t1")
        E.reconcile({"open_pr": lambda t, r, n: (None, {})})
        with self.assertRaisesRegex(E.Refused, "数字"):
            E.settle("open_pr:t1:b:h", "done", "看过", confirm=True, ref="x")
        E.settle("open_pr:t1:b:h", "done", "GitHub 上看到了", confirm=True, ref="12")
        self.assertEqual(E.guard("open_pr", "open_pr:t1:b:h", "task:t1", {}, self.act())["pr"], 12)

    def test_reconcile_sweeps_killed_first(self):
        now = E.now_tz()
        with E.db() as con:
            con.execute("INSERT INTO exits (key, kind, scope, target, state, pid, proc, boot, created_at, started_at, "
                        "updated_at) VALUES ('merge:o/r:1:h','merge','repo:o/r','{}','executing',1,'never',?,?,?,?)",
                        (E.boot_id(), E.iso(now), E.iso(now), E.iso(now)))
        notes = E.reconcile({"merge": lambda t, r, n: ("done", {"merge_commit": "c"})})
        self.assertEqual(notes[0]["state"], "done")


class Readers(Base):
    def git(self, *a, cwd=None):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    def test_read_push_against_a_real_bare_repo(self):
        bare, work = self.root / "remote.git", self.root / "work"
        self.git("init", "-q", "--bare", str(bare))
        self.git("init", "-q", str(work))
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a", cwd=work)
        head = self.git("rev-parse", "HEAD", cwd=work)
        t = {"remote": f"file://{bare}", "branch": "claude/t-20261118-abcdef", "head": head}
        verdict, ev = E.read_push(t, {}, None)                       # 远端没有这条分支：分不清没推还是推了又删（P1-10）
        self.assertIsNone(verdict)
        self.assertIn("分不清", ev["note"])
        self.git("push", "-q", f"file://{bare}", f"HEAD:refs/heads/{t['branch']}", cwd=work)
        self.assertEqual(E.read_push(t, {}, None)[0], "done")
        self.assertIsNone(E.read_push({**t, "head": "0" * 40}, {}, None)[0])
        self.git("push", "-q", "--delete", f"file://{bare}", t["branch"], cwd=work)   # 推上之后分支被删：不判没推
        self.assertIsNone(E.read_push(t, {}, None)[0])
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "b", cwd=work)
        before = self.git("rev-parse", "HEAD~1", cwd=work)
        new = self.git("rev-parse", "HEAD", cwd=work)
        self.git("push", "-q", f"file://{bare}", f"HEAD~1:refs/heads/{t['branch']}", cwd=work)
        self.assertIsNone(E.read_push({**t, "head": new, "before": before}, {}, None)[0])   # 续推前的旧提交：同样分不清
        with self.assertRaises(E.Unreachable):
            E.read_push({**t, "remote": f"file://{self.root}/nowhere.git"}, {}, None)

    def fake_gh(self, payload, code=0):
        f = self.root / "gh"
        f.write_text(f"#!/bin/sh\necho '{json.dumps(payload)}'\nexit {code}\n")
        f.chmod(0o755)
        return mock.patch.dict(os.environ, {"DT_GH": str(f)})

    def test_read_pr(self):
        t = {"github": "o/r", "branch": "claude/x", "head": "h"}
        ours = {"number": 3, "url": "u", "state": "OPEN", "headRefOid": "h", "baseRefName": "main", "headRefName": t["branch"],
                "isCrossRepository": False, "headRepository": {"name": "r"}, "headRepositoryOwner": {"login": "o"}}
        with self.fake_gh([ours]):
            self.assertEqual(E.read_pr(t, {}, None), ("done", {"pr": "u", "number": 3}))
        fork = {**ours, "isCrossRepository": True, "headRepositoryOwner": {"login": "someone"}}   # P2-04：同 SHA 同分支的 fork PR
        for bad in (fork, {**ours, "headRepositoryOwner": {"login": "someone"}}, {**ours, "headRefName": "other"}):
            with self.subTest(bad=bad), self.fake_gh([bad]):
                self.assertIsNone(E.read_pr(t, {}, None)[0])
        with self.fake_gh([]):
            self.assertEqual(E.read_pr(t, {}, None)[0], "failed")
        with self.fake_gh([{"headRefOid": "other", "baseRefName": "main"}]):
            self.assertIsNone(E.read_pr(t, {}, None)[0])
        with self.fake_gh([], code=1), self.assertRaises(E.Unreachable):
            E.read_pr(t, {}, None)

    def test_read_merge(self):
        t = {"github": "o/r", "number": 3, "head": "h"}
        with self.fake_gh({"state": "MERGED", "headRefOid": "h", "mergeCommit": {"oid": "m"}}):
            self.assertEqual(E.read_merge(t, {}, None), ("done", {"merge_commit": "m"}))
        with self.fake_gh({"state": "OPEN", "headRefOid": "h"}):
            self.assertEqual(E.read_merge(t, {}, None)[0], "failed")
        with self.fake_gh({"state": "MERGED", "headRefOid": "other", "mergeCommit": {"oid": "m"}}):
            self.assertIsNone(E.read_merge(t, {}, None)[0])

    def test_read_send(self):
        now = E.now_tz()
        row = {"started_at": E.iso(now - dt.timedelta(minutes=10))}
        t = {"chat": "oc_x", "marker": "abcdef0123456789", "msg_type": "interactive"}
        card = {"body": {"elements": [{"tag": "button", "behaviors": [{"value": {"autoloop": "abcdef0123456789", "o": 0}}]}]}}
        msg = {"message_id": "om_1", "msg_type": "interactive", "sender": {"sender_type": "app"},
               "body": {"content": json.dumps(card)}}
        with mock.patch.object(E, "lark_read", return_value={"messages": [msg], "has_more": False}):
            self.assertEqual(E.read_send(t, row, now), ("done", {"message_id": "om_1"}))
        with mock.patch.object(E, "lark_read", return_value={"messages": [msg, msg], "has_more": False}):
            self.assertIsNone(E.read_send(t, row, now)[0])
        with mock.patch.object(E, "lark_read", return_value={"messages": [], "has_more": False}):
            self.assertIsNone(E.read_send(t, row, now)[0])         # 审查 P1-05：查不到 ≠ 没发，交常浩，不给重发资格
            with self.assertRaises(E.Unreachable):                 # 刚发不久：列表可能滞后，先保持不明
                E.read_send(t, {"started_at": E.iso(now)}, now)
        with mock.patch.object(E, "lark_read", side_effect=E.Unreachable("断网")), self.assertRaises(E.Unreachable):
            E.read_send(t, row, now)

    def test_read_send_reads_every_page(self):
        """#312：--page-all 默认只翻 10 页，到顶不报错、只留 has_more=true。只看第一轮会把发了两条的认成一条、记成做成。"""
        now = E.now_tz()
        row = {"started_at": E.iso(now - dt.timedelta(minutes=10))}
        t = {"chat": "oc_x", "marker": "abcdef0123456789", "msg_type": "interactive"}
        card = {"body": {"elements": [{"tag": "button", "behaviors": [{"value": {"autoloop": "abcdef0123456789", "o": 0}}]}]}}
        msg = {"message_id": "om_1", "msg_type": "interactive", "sender": {"sender_type": "app"},
               "body": {"content": json.dumps(card)}}
        pages = [{"messages": [msg], "has_more": True, "page_token": "t2"}, {"messages": [{**msg, "message_id": "om_2"}], "has_more": False}]
        with mock.patch.object(E, "lark_read", side_effect=pages) as m:
            self.assertEqual(E.read_send(t, row, now), (None, {"hits": 2}))
        self.assertEqual([c[0][0][-2:] == ["--page-token", "t2"] for c in m.call_args_list], [False, True])
        with mock.patch.object(E, "lark_read", return_value={"messages": [msg], "has_more": True}):
            with self.assertRaisesRegex(E.Unreachable, "对账：还有下一页，但飞书没给翻页标记，没读全"):   # 保持不明，不记成做成
                E.read_send(t, row, now)

    def test_read_send_needs_proof_that_the_list_is_complete(self):
        """PR #335 审查 P1-01：返回里没有 has_more（或是 null）时没法证明读全了。正好看到一条也不能记成做成，保持不明。"""
        now = E.now_tz()
        row = {"started_at": E.iso(now - dt.timedelta(minutes=10))}
        t = {"chat": "oc_x", "marker": "abcdef0123456789", "msg_type": "interactive"}
        card = {"body": {"elements": [{"tag": "button", "behaviors": [{"value": {"autoloop": "abcdef0123456789", "o": 0}}]}]}}
        msg = {"message_id": "om_1", "msg_type": "interactive", "sender": {"sender_type": "app"},
               "body": {"content": json.dumps(card)}}
        for partial in ({"messages": [msg]}, {"messages": [msg], "has_more": None}, {"messages": None, "has_more": False}, {}, None):
            with self.subTest(partial=partial), mock.patch.object(E, "lark_read", return_value=partial):
                with self.assertRaisesRegex(E.Unreachable, "对账：.*没法确认读没读全"):
                    E.read_send(t, row, now)

    def test_read_send_too_many_rounds_goes_to_owner(self):
        """#338 乙：翻到轮数上限 = 连得上、确定读不全。记核不清交常浩结算，不再永远停在结果不明；其他读不全照旧保持不明。"""
        now = E.now_tz()
        row = {"started_at": E.iso(now - dt.timedelta(minutes=10))}
        t = {"chat": "oc_x", "marker": "abcdef0123456789", "msg_type": "interactive"}
        n = iter(range(10 ** 6))
        endless = lambda args, what: {"messages": [], "has_more": True, "page_token": f"t{next(n)}"}
        with mock.patch.object(E, "lark_read", side_effect=endless) as m:
            verdict, evidence = E.read_send(t, row, now)
        self.assertIsNone(verdict)
        self.assertRegex(evidence["note"], "翻了 100 轮还没到底.*交常浩核实")
        self.assertEqual(m.call_count, 100)
        with mock.patch.object(E, "lark_read", return_value={"messages": [], "has_more": True}), \
                self.assertRaises(E.Unreachable):                                  # 没给翻页标记：仍按连不上
            E.read_send(t, row, now)

    def test_only_the_real_message_counts(self):
        """审查 P1-05：机器人发的、类型对、有消息号、令牌在按钮回调值里，才算这条；只在文字里搜到令牌不算。"""
        t = {"marker": "abcdef0123456789", "msg_type": "interactive"}
        card = json.dumps({"elements": [{"behaviors": [{"value": {"autoloop": "abcdef0123456789"}}]}]})
        good = {"message_id": "om_1", "msg_type": "interactive", "sender": {"sender_type": "app"}, "body": {"content": card}}
        self.assertTrue(E.is_ours(good, t))
        for bad in ({**good, "sender": {"sender_type": "user"}}, {**good, "msg_type": "text"}, {**good, "message_id": ""},
                    {**good, "body": {"content": json.dumps({"text": "abcdef0123456789"})}},
                    {**good, "body": {"content": json.dumps({"autoloop": "other"})}}, {**good, "body": {"content": "{坏"}}):
            self.assertFalse(E.is_ours(bad, t), bad)
        text = {"message_id": "om_2", "msg_type": "text", "sender": {"sender_type": "app"},
                "body": {"content": json.dumps({"text": "同意就回：派单 0a1b2c3d"})}}
        self.assertTrue(E.is_ours(text, {"marker": "派单 0a1b2c3d", "msg_type": "text"}))
        self.assertFalse(E.is_ours(text, {"marker": "派单 ffffffff", "msg_type": "text"}))

    def test_lark_read_uses_the_configured_bot_profile(self):
        """审查 P2-02：对账读飞书用哪个机器人的登录配置可配（0E 后是门卫机器人）。"""
        seen = {}

        def run(cmd, **kw):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True, "data": {"messages": []}}), stderr="")
        with mock.patch.dict(os.environ, {"DT_EXITS_LARK_PROFILE": "cli_gatekeeper"}), \
                mock.patch.object(E.subprocess, "run", run):
            self.assertEqual(E.lark_read(["im", "x"], "对账"), {"messages": []})
        self.assertEqual(seen["cmd"][1:3], ["--profile", "cli_gatekeeper"])


class Cli(Base):
    def test_show_and_settle(self):
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(E.main(["show"]), 0)
            self.assertEqual(E.main(["settle", "--key", "merge:x", "--result", "done", "--note", "n"]), 1)
        self.assertIn("--confirm", out.getvalue())


if __name__ == "__main__":
    unittest.main()
