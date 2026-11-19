"""恢复门演练（#195 批 0D 验收）：断网、超时、强杀各演练，数每个外部动作实际发生了几次（必须正好一次或没有）；
结果不明会冻结、能对账；中台重启、睡醒后恢复闸先对账；升级后挂起的卡片和轮次照常恢复，升级失败用快照回滚；搬家后照常恢复。

外部系统：GitHub 用本地裸仓库（收到推送就记一笔）和假 gh（fixtures/drill_gh.py，创建 PR、合并各记一笔）；飞书用假的
lark-cli（消息落进一个文件）；中台是临时目录里起的真中台（同 test_autoloop_hub_e2e，本机没有命令行时跳过）。
强杀：子进程（fixtures/drill_child.py）真的做完动作、还没记结果时被 SIGKILL。
升级、回滚、搬家：ops/autoloop_drill.py 从本机中台程序只读克隆出 v1.8.4、v1.8.5 两个版本（没有就跳过）。
全部在临时目录；不碰 127.0.0.1:3350 和 ~/.agent-autoloop；模块前后由 prod_guard 核对正式环境。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_answer as A  # noqa: E402
import autoloop_config as C  # noqa: E402
import autoloop_exits as E  # noqa: E402
import autoloop_feishu as F  # noqa: E402
import autoloop_recovery as R  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401
from test_autoloop_hub_e2e import CLI, OWNER, SKIP, HubFixture  # noqa: E402

CHILD = HERE / "fixtures/drill_child.py"
GH = HERE / "fixtures/drill_gh.py"
TASK = "t-20261118-0d0d0d"
CHAT = "oc_drill_home"
_spec = importlib.util.spec_from_file_location("autoloop_drill", HERE.parents[2] / "ops/autoloop_drill.py")
D = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(D)


def git(*a, cwd=None):
    return subprocess.run(["git", "-c", "user.name=d", "-c", "user.email=d@d", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


def wait_for(cond, timeout=20, what="条件"):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(0.1)
    raise AssertionError(f"等不到{what}")


@unittest.skipIf(SKIP, "本机没有 agent-autoloop 命令行")
class Drill(HubFixture):
    """每个演练一个临时中台；恢复闸是真的（锁定这个临时中台的版本）。"""
    COMMAND = f"{sys.executable} {HERE / 'fixtures' / 'ask_receipt.py'} {{prompt}}"

    def setUp(self):
        super().setUp()
        self.root = Path(self.key_file).parents[2]
        a = self.root / "dt/autoloop"
        self.gh_state, self.gh_mode = self.root / "gh.json", self.root / "gh.mode"
        GH.chmod(0o755)
        for p in (mock.patch.dict(os.environ, {
                "DT_EXITS_DB": str(a / "exits.db"), "DT_HUB_PIN": str(a / "hub-pin.json"), "DT_GRANTS_DB": str(a / "grants.db"),
                "DT_GRANTS_STATUS": str(self.root / "shared/grants/status.json"),
                "DT_GRANTS_TASKS": str(self.root / "shared/tasks"), "DT_AUTOLOOP_CARDS_DB": str(a / "cards.db"),
                "DT_AUTOLOOP_CHANNEL_KEY_DIR": str(a), "DT_AUTOLOOP_LOCK": str(a / "config.lock"),
                "DT_OWNER_CHAT_ID": CHAT, "DT_GH": str(GH), "DRILL_GH_STATE": str(self.gh_state),
                "DRILL_GH_MODE": str(self.gh_mode)}),
                  mock.patch.object(C.G, "ROSTER", self.roster)):
            p.start()
            self.addCleanup(p.stop)
        R.pin(R.healthz(), confirm=True)
        R.ensure_open()
        self.children = []
        self.addCleanup(self._reap)

    def _reap(self):
        for p in self.children:
            if p.poll() is None:
                p.kill()
                p.wait(5)

    # ---------- 工具 ----------
    def child(self, mode, params):
        p = subprocess.Popen([sys.executable, str(CHILD), mode, json.dumps(params)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        self.children.append(p)
        return p

    def kill_when(self, proc, cond, what):
        wait_for(cond, what=what)
        proc.kill()
        proc.wait(5)

    def repo(self):
        """「GitHub」：本地裸仓库，收到推送就在计数文件里记一笔；再加一个工作区和一个提交。"""
        bare, work, self.pushes = self.root / "remote.git", self.root / "work", self.root / "pushes"
        git("init", "-q", "--bare", str(bare))
        hook = bare / "hooks/post-receive"
        hook.write_text(f"#!/bin/sh\necho x >> '{self.pushes}'\n")
        hook.chmod(0o755)
        git("init", "-q", str(work))
        git("commit", "-q", "--allow-empty", "-m", "改动", cwd=work)
        head = git("rev-parse", "HEAD", cwd=work)
        branch = f"claude/{TASK}"
        return bare, work, {"remote": f"file://{bare}", "branch": branch, "head": head}

    def push_count(self):
        return len(self.pushes.read_text().splitlines()) if self.pushes.exists() else 0

    def gh(self):
        return json.loads(self.gh_state.read_text()) if self.gh_state.exists() else {"prs": [], "creates": 0, "merges": 0}

    def mode(self, m):
        self.gh_mode.write_text(m)

    def restart_hub(self):
        self._stop()
        self.up = subprocess.Popen([CLI, "up"], env={**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home)},
                                   cwd=self.root, stdout=self.log, stderr=subprocess.STDOUT)
        self._wait_up()

    def gate_open(self):
        return R.status()["open"]


class PushBranch(Drill):
    """推分支：强杀、超时（推之前 / 推之后）、断网，各一次；远端只收到一次推送。"""

    def push(self, cwd, t):
        return lambda: subprocess.run(["git", "push", "-q", t["remote"], f"{t['head']}:refs/heads/{t['branch']}"],
                                      cwd=cwd, check=True, capture_output=True, timeout=30) and {"pushed": t["head"]}

    def key(self, t):
        return f"push_branch:{TASK}:{t['branch']}:{t['head']}"

    def test_kill_after_push_is_reconciled_not_redone(self):
        bare, work, t = self.repo()
        p = self.child("push", {"key": self.key(t), "scope": f"task:{TASK}", "target": t, "cwd": str(work)})
        self.kill_when(p, lambda: self.push_count() == 1, "推送落到远端")
        out = E.guard("push_branch", self.key(t), f"task:{TASK}", t, self.push(work, t))   # 重启后同一个动作再来
        self.assertEqual((out["again"], out["remote_head"]), (True, t["head"]))            # 对账确认已推：不重推
        self.assertEqual(self.push_count(), 1)
        self.assertTrue(self.gate_open())                    # 强杀留下的不明关过闸，领资格前已整体对账、重新开闸

    def test_timeout_before_push_then_retry_once(self):
        bare, work, t = self.repo()
        slow = lambda: subprocess.run(["sh", "-c", f"sleep 2; git push -q {t['remote']} {t['head']}:refs/heads/{t['branch']}"],  # noqa: E731
                                      cwd=work, check=True, timeout=0.5)
        with self.assertRaises(subprocess.TimeoutExpired):
            E.guard("push_branch", self.key(t), f"task:{TASK}", t, slow)
        self.assertEqual(E.show()["frozen"], [f"task:{TASK}"])
        time.sleep(2.5)                                                       # 被杀掉的那次不会晚到
        out = E.guard("push_branch", self.key(t), f"task:{TASK}", t, self.push(work, t))
        self.assertEqual(out["again"], False)                                 # 对账确认没推过：重做一次
        self.assertEqual(self.push_count(), 1)

    def test_timeout_after_push_is_not_redone(self):
        bare, work, t = self.repo()
        slow = lambda: subprocess.run(["sh", "-c", f"git push -q {t['remote']} {t['head']}:refs/heads/{t['branch']}; sleep 2"],  # noqa: E731
                                      cwd=work, check=True, timeout=1)
        with self.assertRaises(subprocess.TimeoutExpired):
            E.guard("push_branch", self.key(t), f"task:{TASK}", t, slow)
        out = E.guard("push_branch", self.key(t), f"task:{TASK}", t, self.push(work, t))
        self.assertTrue(out["again"])
        self.assertEqual(self.push_count(), 1)

    def test_network_down_stays_frozen_until_back(self):
        bare, work, t = self.repo()
        away = bare.with_name("remote.away")
        bare.rename(away)                                                     # 断网
        with self.assertRaises(subprocess.CalledProcessError):
            E.guard("push_branch", self.key(t), f"task:{TASK}", t, self.push(work, t))
        with self.assertRaisesRegex(E.Refused, "结果不明"):                  # 还断着：对不了账，保持冻结，不重推
            E.guard("push_branch", self.key(t), f"task:{TASK}", t, self.push(work, t))
        R.ensure_open()                                                       # 闸照常开，只冻这张单
        self.assertEqual(R.status()["frozen"], [f"task:{TASK}"])
        away.rename(bare)                                                     # 网通了
        out = E.guard("push_branch", self.key(t), f"task:{TASK}", t, self.push(work, t))
        self.assertEqual(out["again"], False)
        self.assertEqual(self.push_count(), 1)
        self.assertEqual(R.status()["frozen"], [])


    def test_live_executor_past_hard_stale_is_never_retried(self):
        """审查 P1-02：做的进程还活着、只是跑了超过 30 分钟（真进程，时钟拨快 31 分钟看）：只报疑似卡住，
        对账不改它、同一动作和同一范围的别的动作都拒绝；它做完以后再来，直接回原结果。远端只收到一次推送。"""
        bare, work, t = self.repo()
        go = self.root / "go"
        p = self.child("slow_push", {"key": self.key(t), "scope": f"task:{TASK}", "target": t, "cwd": str(work),
                                     "go": str(go)})
        wait_for(lambda: (E.in_flight(f"task:{TASK}") or [{}])[0].get("state") == "executing", what="子进程登记 executing")
        later = E.now_tz() + E.HARD_STALE + E.dt.timedelta(minutes=1)
        with mock.patch.object(E, "now_tz", lambda: later):
            self.assertEqual(R.status()["stalled"], [self.key(t)])          # 报疑似卡住
            self.assertEqual(E.reconcile(), [])                              # 对账不碰还活着的
            with self.assertRaisesRegex(E.Refused, "还在进行"):
                E.guard("push_branch", self.key(t), f"task:{TASK}", t, self.push(work, t))
            with self.assertRaisesRegex(E.Refused, "正在进行"):
                E.guard("open_pr", f"open_pr:{TASK}:x:y", f"task:{TASK}", {}, dict)
        self.assertEqual((p.poll(), self.push_count()), (None, 0))         # 它还活着，谁都没推
        go.write_text("x")
        self.assertEqual(p.wait(30), 0)
        out = E.guard("push_branch", self.key(t), f"task:{TASK}", t, self.push(work, t))
        self.assertEqual((out["again"], out["pushed"]), (True, t["head"]))
        self.assertEqual(self.push_count(), 1)


class OpenPrAndMerge(Drill):
    def pr_target(self, head="h" * 40):
        return {"github": "o/dt-sandbox", "branch": f"claude/{TASK}", "head": head}

    def create(self, t):
        env = {**os.environ, "DRILL_GH_HEAD": t["head"]}
        return lambda: subprocess.run([str(GH), "pr", "create", "-R", t["github"], "-B", "main", "-H", t["branch"], "-t", "t",
                                       "-F", "/dev/null"], check=True, capture_output=True, timeout=5, env=env) and {}

    def test_kill_after_pr_created_then_network_down_during_reconcile(self):
        t = self.pr_target()
        key = f"open_pr:{TASK}:{t['branch']}:{t['head']}"
        with mock.patch.dict(os.environ, {"DRILL_GH_HEAD": t["head"]}):
            p = self.child("pr", {"key": key, "scope": f"task:{TASK}", "target": t, "gh": str(GH)})
        self.kill_when(p, lambda: self.gh()["creates"] == 1, "PR 建出来")
        self.mode("down")                                                     # 对账时 GitHub 连不上
        with self.assertRaisesRegex(E.Refused, "结果不明"):
            E.guard("open_pr", key, f"task:{TASK}", t, self.create(t))
        self.mode("")
        out = E.guard("open_pr", key, f"task:{TASK}", t, self.create(t))
        self.assertEqual((out["again"], out["number"]), (True, 1))
        self.assertEqual(self.gh()["creates"], 1)

    def test_merge_timeout_freezes_the_repo_and_is_not_redone(self):
        t = self.pr_target()
        self.create(t)()
        key = f"merge:{t['github']}:1:{t['head']}"
        target = {"github": t["github"], "number": 1, "head": t["head"]}
        merge = lambda: subprocess.run([str(GH), "pr", "merge", "1", "-R", t["github"], "--match-head-commit", t["head"]],  # noqa: E731
                                       check=True, capture_output=True, timeout=1) and {}
        self.mode("hang-after")                                               # 合并已做，回应没回来
        with self.assertRaises(subprocess.TimeoutExpired):
            E.guard("merge", key, f"repo:{t['github']}", target, merge)
        self.mode("down")
        with self.assertRaisesRegex(E.Refused, "已冻结"):                     # 同仓库别的合并也停（规格第 7 节）
            E.guard("merge", f"merge:{t['github']}:2:{'2' * 40}", f"repo:{t['github']}", {}, merge)
        self.mode("")
        out = E.guard("merge", key, f"repo:{t['github']}", target, merge)
        self.assertEqual((out["again"], out["merge_commit"]), (True, "m" * 40))
        self.assertEqual(self.gh()["merges"], 1)


class Cards(Drill):
    """飞书卡片与本人点击：真中台出卡、桥接发卡、点击交中台。"""

    def setUp(self):
        super().setUp()
        self.sent = []
        p = mock.patch.object(F, "lark", self.fake_lark)
        p.start()
        self.addCleanup(p.stop)
        C.install(confirm=True, target=self.home)
        rules = json.loads(C.rules_file().read_text(encoding="utf-8"))
        next(r for r in rules["rules"] if r["name"] == "试一下")["channels"] = ["feishu"]
        C.rules_file().write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        self._wait_live(lambda live: next(r for r in live["rules"] if r["name"] == "试一下").get("channels") == ["feishu"])
        C.issue_channel_key("feishu", True, str(self.home))
        subprocess.run([CLI, "try"], env={**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home)}, capture_output=True,
                       timeout=60)

    def fake_lark(self, args, what, cwd=None):
        if args[:2] == ["im", "+messages-send"]:
            self.sent.append(args)
            return {"message_id": f"om_parent_{len(self.sent)}"}
        return {}

    def next_card(self):
        for _ in range(60):
            card = F.hub("GET", "/v1/cards/next?wait=1", timeout=30).get("card")
            if card:
                return card
        self.fail("中台没出卡片")

    def hub_card(self, cid):
        st = C.state()
        return next(c for c in st.get("cards", []) + st.get("recentCards", []) if c["id"] == cid)

    def delivered(self):
        out = None
        for _ in range(60):
            out = F.once(wait=1)
            if out:
                break
        self.assertTrue(out and out["ok"], out)
        with F.db() as con:
            tok = con.execute("SELECT token FROM feishu_cards WHERE card_id = ?", (out["card"],)).fetchone()[0]
        return out["card"], tok

    def claim(self, cid):
        with F.db() as con:
            r = con.execute("SELECT * FROM claims WHERE card_id = ? AND who = 'owner'", (cid,)).fetchone()
            return dict(r) if r else None

    def test_kill_after_card_sent_is_reconciled_not_resent(self):
        card = self.next_card()
        chat_file = self.root / "chat.json"
        p = self.child("send", {"card": card, "chat_file": str(chat_file)})
        self.kill_when(p, lambda: chat_file.exists() and len(json.loads(chat_file.read_text())) == 1, "卡片发到私聊")
        msgs = json.loads(chat_file.read_text())
        with mock.patch.object(E, "lark_read", return_value={"messages": msgs}):  # 对账：在私聊里按令牌找那条
            ref = F.deliver(card)
        self.assertEqual((ref, self.sent), ("om_drill_1", []))                  # 用找到的消息号，不重发
        F.confirm(card["id"])                                                  # 报送达用的也是这条
        self.assertEqual(F.card_by_token(json.loads(msgs[0]["body"]["content"])["body"]["elements"][1]["behaviors"][0]
                                         ["value"]["autoloop"])["message_id"], "om_drill_1")
        self.assertEqual(len(json.loads(chat_file.read_text())), 1)

    def test_kill_after_answer_accepted_then_hub_restart(self):
        cid, tok = self.delivered()
        p = self.child("answer", {"token": tok, "option": 1, "chat": CHAT, "owner": OWNER,
                                   "version": F.card_by_token(tok)["version"]})
        self.kill_when(p, lambda: self.hub_card(cid).get("state") != "open", "中台收下回答")
        self.assertEqual(self.claim(cid)["state"], "inflight")
        with F.db(write=True) as con:                                          # 过了在途窗口
            con.execute("UPDATE claims SET at = '2026-01-01T00:00:00+08:00' WHERE card_id = ?", (cid,))
        self.restart_hub()                                                     # 中台重启：闸关，对账后开
        self.assertIn("中台重启", R.status()["why"])
        R.ensure_open()
        self.assertEqual(self.claim(cid)["state"], "answered")
        c = self.hub_card(cid)
        self.assertEqual((c["choice"], c["by"]), (1, OWNER))
        with mock.patch.object(A, "tell"):
            self.assertEqual(A.answer(tok, 0, CHAT, OWNER, F.card_by_token(tok)['version'])[0], 1)              # 再点：中台只认第一次
        self.assertEqual(self.hub_card(cid)["choice"], 1)

    def test_hub_down_click_waits_in_queue_then_goes_once(self):
        cid, tok = self.delivered()
        self._stop()                                                           # 断网：中台连不上，恢复闸跟着关
        with mock.patch.object(A, "tell"):
            self.assertEqual(A.answer(tok, 1, CHAT, OWNER, F.card_by_token(tok)['version'])[0], 3)              # 只排队，不发注定不明的请求
        self.restart_hub()
        R.ensure_open()                                                        # 对账不提交排队的
        self.assertEqual(self.claim(cid)["state"], "queued")
        with mock.patch.object(A, "tell"):
            A.reconcile()                                                      # 桥接下一轮：开闸后提交一次
        self.assertEqual(self.claim(cid)["state"], "answered")
        self.assertEqual(self.hub_card(cid)["choice"], 1)

    def test_request_lost_stays_unknown_until_owner_releases(self):
        cid, tok = self.delivered()
        lost = F.HubError("连不上中台或没等到回应（ConnectionResetError）")     # 过了闸，请求在路上断了
        with mock.patch.object(F, "hub", side_effect=lambda m, p, b=None, timeout=20: (_ for _ in ()).throw(lost)
                               if p.endswith("/answer") else {}), mock.patch.object(A, "tell"):
            self.assertEqual(A.answer(tok, 1, CHAT, OWNER, F.card_by_token(tok)['version'])[0], 2)              # 结果不明，不重答
        R.ensure_open()                                                        # 对账：卡还开着 → 仍不明，不自动放开
        self.assertEqual(self.claim(cid)["state"], "unknown")
        self.assertEqual(self.hub_card(cid)["state"], "open")
        A.release_unknown(cid)                                                 # 常浩明确同意放开
        with mock.patch.object(A, "tell"):
            self.assertEqual(A.answer(tok, 1, CHAT, OWNER, F.card_by_token(tok)['version'])[0], 0)
        c = self.hub_card(cid)
        self.assertEqual((c["choice"], c["by"]), (1, OWNER))

    def test_answer_timeout_after_accept_is_reconciled(self):
        cid, tok = self.delivered()
        real = F.hub

        def slow(method, path, body=None, timeout=20):
            out = real(method, path, body, timeout)
            if path.endswith("/answer"):
                raise F.HubError("连不上中台或没等到回应（TimeoutError）")
            return out
        with mock.patch.object(F, "hub", slow), mock.patch.object(A, "tell"):
            self.assertEqual(A.answer(tok, 0, CHAT, OWNER, F.card_by_token(tok)['version'])[0], 2)
        self.assertFalse(self.gate_open())                                     # 结果不明关闸
        R.ensure_open()
        self.assertEqual(self.claim(cid)["state"], "answered")
        self.assertEqual(self.hub_card(cid)["choice"], 0)

    def test_wake_closes_gate_and_side_effects_wait_for_reconcile(self):
        real = R._sysctl_sec
        with mock.patch.object(R, "_sysctl_sec", lambda n: "1" if n == "kern.waketime" else real(n)):
            self.assertIn("睡醒", R.status()["why"])
            with mock.patch.object(R, "reconcile", side_effect=AssertionError("先对账")) as rec, \
                    self.assertRaises(AssertionError):
                F.once(wait=1)                                                 # 领卡前要先对账
            self.assertEqual(rec.call_count, 1)
            self.assertTrue(F.once(wait=1)["ok"])                              # 对完账照常领卡、发卡
            self.assertTrue(self.gate_open())


@unittest.skipUnless(D.available(), "本机没有可克隆的中台程序或 node")
class Upgrade(unittest.TestCase):
    """升级、升级失败回滚、搬家：真中台两个版本（v1.8.4 → v1.8.5），挂起的卡片和轮次要能接着跑完；恢复闸跟着关和开。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="dt-drill-upgrade-")
        cls.root = Path(cls.tmp.name)
        cls.old, cls.new = D.Engine(cls.root, "v1.8.4"), D.Engine(cls.root, "v1.8.5")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(dir=self.root))
        self.hub = D.Hub(self.case / "a/hub")
        self.addCleanup(lambda: self.hub.stop())
        self.hub.init(self.old)
        self.assertTrue(self.hub.start(self.old))
        self.point_at(self.hub)
        R.pin(self.old.commit, confirm=True)
        R.ensure_open()

    def point_at(self, hub):
        p = mock.patch.dict(os.environ, {"DT_HUB": hub.url, "AGENT_AUTOLOOP_HOME": str(hub.home),
                                         "DT_EXITS_DB": str(self.case / "exits.db"),
                                         "DT_HUB_PIN": str(self.case / "hub-pin.json"),
                                         "DT_AUTOLOOP_CARDS_DB": str(self.case / "cards.db"),
                                         "DT_GRANTS_DB": str(self.case / "grants.db"),
                                         "DT_STATE_DB": str(self.case / "twin-state.db")})
        p.start()
        self.addCleanup(p.stop)

    def refused(self, card=1):
        with self.assertRaises(E.Refused) as e:
            E.guard("send_feishu", f"send_feishu:card-{card}", f"card:{card}", {}, dict)
        return str(e.exception)

    def window(self, hub):
        """快照之后、停旧版之前（审查 P1-06）：停写还在，桥接不领卡、出口账本不发任何卡片。"""
        nxt = D.max_card(hub) + 1
        return {"bridge": F.once(wait=0), "send": self.refused(nxt)}

    def test_upgrade_holds_writes_then_retires_snapshot(self):
        card = self.hub.ask(self.old)
        out = D.upgrade(self.hub, self.old, self.new, self.case / "snap", window=self.window, point="up-1")
        self.assertTrue(out["ok"])
        self.assertIn("维护中", out["window"]["bridge"]["paused"])            # 停写期间桥接没领卡
        self.assertIn("维护中", out["window"]["send"])
        self.assertEqual(self.hub.pending(), out["pending_before"])
        self.assertEqual(R.pinned(), self.new.commit)                         # 核对通过才锁定新版本
        with self.assertRaisesRegex(E.Refused, "retired"):                   # 升级前的快照作废，不能再拿来回滚
            R.restorable("up-1")
        self.assertIn("维护结束", R.status()["why"])
        R.ensure_open()
        self.assertIn("试跑通过", self.hub.answer_and_finish(card))           # 挂起的轮次接着跑完

    def test_failed_upgrade_rolls_back_from_held_point(self):
        card = self.hub.ask(self.old)
        bad = self.new.broken_copy(self.root)
        out = D.upgrade(self.hub, self.old, bad, self.case / "snap", damage=D.corrupt, window=self.window, point="up-2")
        self.assertEqual((out["ok"], out["rolled_back"], out["on"]), (False, True, self.old.commit))
        self.assertEqual(out["pending_after"], out["pending_before"])
        self.assertEqual(out["mismatched"], [])                                # 快照之后没有发出过卡片：卡片号对得上
        self.assertTrue(list(self.hub.home.glob("restore-before-*")))         # 被替换的坏库留着备查
        self.assertEqual(R.pinned(), self.old.commit)
        with self.assertRaisesRegex(E.Refused, "rolled_back"):
            R.restorable("up-2")
        R.ensure_open()                                                       # 版本仍是锁定的旧版：对账后开
        self.assertIn("试跑通过", self.hub.answer_and_finish(card))

    def test_rollback_refused_when_a_card_went_out_after_snapshot(self):
        """停写没守住（快照后有卡片发出，本地卡片库里有比快照时最大号还大的卡）：回滚后保持停写，交常浩。"""

        def leaked(hub):
            with F.db(write=True) as con:
                con.execute("INSERT INTO feishu_cards (card_id, token, chat_id, kind, options, version) "
                            "VALUES (?, 'cccccccccccccccc', 'oc_x', 'ask', 2, 1)", (D.max_card(hub) + 1,))
        bad = self.new.broken_copy(self.root)
        with self.assertRaisesRegex(E.Refused, "错位"):
            D.upgrade(self.hub, self.old, bad, self.case / "snap", damage=D.corrupt, window=leaked, point="up-3")
        self.assertIn("维护中", R.status()["why"])

    def test_move_to_another_home_keeps_pending(self):
        card = self.hub.ask(self.old)
        p = mock.patch.dict(os.environ, {"AGENT_AUTOLOOP_HOME": str(self.case / "b/hub")})   # 恢复闸跟着搬到新目录
        p.start()
        self.addCleanup(p.stop)
        moved, out = D.move(self.hub, self.old, self.case / "b/hub", self.case / "snap", point="mv-1")
        self.hub = moved
        self.assertEqual(out["pending_after"], out["pending_before"])
        self.assertFalse(R.status()["open"])
        R.ensure_open()
        self.assertIn("试跑通过", moved.answer_and_finish(card))

    def test_refuses_real_hub_home_or_port(self):
        with self.assertRaises(D.DrillError):
            D.Hub(D.REAL_HOME)
        with self.assertRaises(D.DrillError):
            D.Hub(self.case / "x", port=D.REAL_PORT)


if __name__ == "__main__":
    unittest.main()
