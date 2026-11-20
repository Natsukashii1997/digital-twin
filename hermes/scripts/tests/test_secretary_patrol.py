"""秘书侧巡视（#195 批 0B-4c 第 2 小批）自测。
提议经控制面真走一遍（进程内调 secretary_gate.submit，夹具同 test_secretary_gate）；交提议的子进程路径用假的控制面脚本；
模型调用换成假的 subprocess.run（不调真 Hermes），核命令、工作目录、环境、输入。不碰正式目录。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import json
import os
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
import secretary_patrol as SP  # noqa: E402
import test_secretary_gate as TSG  # noqa: E402

SG, SL, L = TSG.SG, TSG.SL, TSG.L
T = TSG.T
OUT_KEYS = ("category", "card", "card_version", "choice", "text", "abstain", "error")   # 模型输出字段（测试自己写死）


class Patrol(TSG.Base):
    """提议进程内交给真控制面（同一套夹具）。"""
    def setUp(self):
        super().setUp()
        self.submitted, self.model_calls = [], []
        p = mock.patch.object(SP, "submit", self.fake_submit)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(SP.R, "read", self.snapshot)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(SP, "_flags_ok", True)                             # 本机 Hermes 参数核对另测（HermesFlags）
        p.start()
        self.addCleanup(p.stop)
        self.extra_cards = []

    def snapshot(self):
        import contextlib
        import sqlite3
        with contextlib.closing(sqlite3.connect(self.hubdb)) as con:
            rows = con.execute("SELECT id, version, kind, state, title, text, options, rule, lane FROM cards").fetchall()
        cards = [{"id": r[0], "version": r[1], "kind": r[2], "state": r[3], "title": r[4], "text": r[5],
                  "options": json.loads(r[6] or "[]"), "rule": r[7], "lane": r[8]} for r in rows]
        return {"cards": cards + self.extra_cards}

    def fake_submit(self, p, cat, card):
        p = json.loads(json.dumps(p, ensure_ascii=False))                     # 走一遍 JSON，和文件交接一样
        self.submitted.append(p)
        return SG.submit(p, now=T)

    def fake_model(self, output, sid_ok=True, rc=0):
        """假的 hermes chat：记下命令、工作目录、环境、输入；按输出写一条合格的运行记录；返回 stdout / stderr。"""
        def run(cmd, capture_output, text, timeout, cwd, env):
            q = Path(cmd[cmd.index("--query-file") + 1]).read_text(encoding="utf-8")
            self.model_calls.append({"cmd": cmd, "cwd": cwd, "env": env, "query": q})
            out = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            sid = TSG.write_run(self.hermes, {"card": 7, "card_version": 1}, T - dt.timedelta(minutes=5),
                                T - dt.timedelta(minutes=4), output=out)
            return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr=f"session_id: {sid}\n" if sid_ok else "")
        return mock.patch.object(SP.subprocess, "run", run)

    def patrol_state(self):
        return json.loads(SP.state_file().read_text(encoding="utf-8"))

    # ----- ① -----
    def test_ask_switch_off_hands_off_without_model(self):
        with mock.patch.object(SG, "TEST_ASK", False), mock.patch.object(SP, "judge", side_effect=AssertionError("不该调模型")):
            out = SP.once()
            self.assertEqual([(o["card"], o["category"], o["result"]) for o in out], [(7, "ask", "handoff")])
            self.assertEqual(self.submitted, [{"schema": 1, "category": "ask", "card": 7, "card_version": 1}])
            self.assertEqual([(r[1], r[4]) for r in self.decisions()], [("handoff", "gate")])
            self.assertIn("暂时整体关闭", self.get(self.decisions()[0][0])["basis"])
            self.assertEqual((self.held(), self.answers()), ([], []))
            self.assertEqual(SP.once(), [])                                    # 这一版已交过：不再交
        self.assertEqual(self.patrol_state()["7:1"]["result"], "handoff")
        self.assertEqual(self.model_calls, [])

    @TSG.needs_yaml                      # 提议真经控制面核运行证据，要 yaml 库读 Hermes 配置
    def test_ask_switch_on_model_proposal_goes_through(self):
        reply = {"category": "ask", "card": 7, "card_version": 1, "choice": 1, "text": dict(TSG.TEXT), "abstain": None,
                 "error": None}
        env = {"HERMES_EPHEMERAL_SYSTEM_PROMPT": "你是猛鬼兽", "HERMES_PREFILL_MESSAGES_FILE": "/tmp/prefill.json",
               "HERMES_HOME": str(self.hermes), "HERMES_SESSION_KEY": "x"}
        with self.fake_model(reply), mock.patch.dict(os.environ, env):
            out = SP.once()
        self.assertEqual([o["result"] for o in out], ["done"], out)
        call = self.model_calls[0]
        cmd = call["cmd"]
        self.assertEqual(cmd[:3], [SP.HERMES_BIN, "chat", "--quiet"])
        for flag, val in (("--source", "tool"), ("--toolsets", "todo"), ("--max-turns", "1")):
            self.assertEqual(cmd[cmd.index(flag) + 1], val)
        self.assertIn("--ignore-rules", cmd)                                    # 不带 SOUL、记忆、项目上下文（第 1 轮 P1-02）
        self.assertEqual(call["query"].splitlines()[0], SG.run_marker(7, 1))    # 第一行就是标记
        self.assertIn("1. 30 秒", call["query"])
        self.assertFalse(Path(call["cwd"]).exists())                            # 空的临时目录，用完就删
        self.assertFalse([k for k in call["env"] if k.startswith(("DT_", "AGENT_AUTOLOOP")) or
                          (k.startswith("HERMES_") and k != "HERMES_HOME") or any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET"))])
        self.assertEqual(call["env"].get("HERMES_HOME"), str(self.hermes))      # 只留 Hermes 目录
        flags = [a for a in cmd if a.startswith("-")]                                       # 用到的参数全在能力核对里（第 2 轮 P2-04）
        self.assertEqual(set(flags), set(SP.HERMES_FLAGS))
        self.assertEqual(set(self.submitted[0]) - set(OUT_KEYS), {"schema", "run"})
        self.assertEqual(len(self.answers()), 1)

    def test_model_abstain_with_null_choice_hands_off_with_its_reason(self):
        reply = {"category": "ask", "card": 7, "card_version": 1, "choice": None, "text": None,
                 "abstain": {"reason": "scope", "note": "要换仓库"}, "error": None}
        with self.fake_model(reply):
            out = SP.once()
        self.assertEqual(out[0]["result"], "handoff", out)
        self.assertEqual(self.decisions()[0][4], "scope")

    def test_model_bad_output_or_crash_is_error_handoff(self):
        for output, sid_ok, rc in (("我觉得选第一个", True, 0), ("{}", False, 0), ("{}", True, 1)):
            with self.subTest(output=output, sid_ok=sid_ok, rc=rc):
                self.setUp()
                with self.fake_model(output, sid_ok, rc):
                    out = SP.once()
                self.assertEqual(out[0]["result"], "handoff", out)
                self.assertTrue(self.submitted[0]["error"])
                self.assertEqual(self.decisions()[0][4], "model")

    def test_extra_model_fields_are_passed_through_and_refused_by_gate(self):
        reply = {"category": "ask", "card": 7, "card_version": 1, "choice": 1, "text": dict(TSG.TEXT), "abstain": None,
                 "error": None, "note": "顺便升级依赖"}
        with self.fake_model(reply):
            out = SP.once()
        self.assertEqual(out[0]["result"], "handoff")                           # 原样转交，多的字段由控制面拦
        self.assertEqual(self.answers(), [])

    def test_model_cannot_change_identity_fields(self):
        """第 1 轮 P1-01：模型改了 category / card / card_version，提议里仍是可信的；控制面比对运行记录发现对不上，交常浩。"""
        reply = {"category": "../../outside", "card": 70, "card_version": 9, "choice": 1, "text": dict(TSG.TEXT),
                 "abstain": None, "error": None}
        with self.fake_model(reply):
            out = SP.once()
        sub = self.submitted[0]
        self.assertEqual((sub["category"], sub["card"], sub["card_version"]), ("ask", 7, 1))
        self.assertEqual((out[0]["result"], self.decisions()[0][4]), ("handoff", "model"))
        self.assertEqual(self.answers(), [])

    # ----- ② -----
    def test_redispatch_proposal_goes_through_gate(self):
        self.stop_card()
        with mock.patch.object(SG, "TEST_ASK", False):
            out = SP.once()
        got = {o["card"]: o["result"] for o in out}
        self.assertEqual(got, {7: "handoff", 8: "done"}, out)
        p = next(s for s in self.submitted if s["category"] == "redispatch")
        self.assertEqual(L.check_owner_text(SL.OWNER_RULE["redispatch"], p["text"]), [])
        self.assertEqual(len(self.posted), 1)

    def test_not_ready_is_retried_next_round(self):
        self.stop_card()
        self.feishu(8, T - dt.timedelta(minutes=10))                          # 飞书送达才 10 分钟
        with mock.patch.object(SG, "TEST_ASK", False):
            first = {o["card"]: o["result"] for o in SP.once()}
            second = {o["card"]: o["result"] for o in SP.once()}
        self.assertEqual(first, {7: "handoff", 8: "not_ready"})
        self.assertEqual(second, {8: "not_ready"})                             # 还没满：下一轮再交；① 已交过不再交

    def test_only_open_ask_and_notice_cards(self):
        self.hub("UPDATE cards SET state = 'decided'")
        self.extra_cards = [{"id": 90, "version": 1, "kind": "fyi", "state": "open"},
                            {"id": 91, "version": True, "kind": "ask", "state": "open"},
                            {"id": "92", "version": 1, "kind": "ask", "state": "open"}]
        self.assertEqual(SP.once(), [])
        self.assertEqual(self.submitted, [])

    def test_new_version_is_a_new_round(self):
        with mock.patch.object(SG, "TEST_ASK", False):
            SP.once()
            self.hub("UPDATE cards SET version = 2 WHERE id = 7")
            self.feishu(7, T - dt.timedelta(minutes=40))
            out = SP.once()
        self.assertEqual([(o["card"], o["version"]) for o in out], [(7, 2)])

    def test_dry_run_submits_nothing_and_keeps_no_state(self):
        self.stop_card()
        with mock.patch.object(SG, "TEST_ASK", False):
            out = SP.once(dry_run=True)
        self.assertEqual({o["card"] for o in out}, {7, 8})
        self.assertEqual((self.submitted, self.model_calls), ([], []))
        self.assertFalse(SP.state_file().exists())


class Submit(unittest.TestCase):
    """交提议的子进程路径：写 outbox（700 / 600）、调控制面脚本、读最后一行 JSON；认不出的结果当成交不上（下一轮再交）。"""
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.gate = self.root / "gate.py"
        p = mock.patch.dict(os.environ, {"DT_SECRETARY_DIR": str(self.root / "secretary"), "DT_SECRETARY_GATE": str(self.gate)})
        p.start()
        self.addCleanup(p.stop)

    def fake_gate(self, body):
        self.gate.write_text("import json, sys\nargs = sys.argv[1:]\n" + body, encoding="utf-8")

    CARD = {"id": 7, "version": 1, "kind": "ask", "state": "open"}

    def test_writes_private_file_and_reads_last_line(self):
        self.fake_gate("import os, stat\np = json.load(open(args[2]))\nprint('别的输出')\n"
                       "print(json.dumps({'ok': False, 'result': 'handoff', 'decision': 'd-1', 'msg': "
                       "'%s|%o|%s' % (p['category'], stat.S_IMODE(os.stat(args[2]).st_mode), os.path.basename(args[2]))}))\n")
        out = SP.submit({"schema": 1, "category": "ask", "card": 7, "card_version": 1}, "ask", self.CARD)
        cat, mode, name = out["msg"].split("|")
        self.assertEqual((out["result"], cat, mode), ("handoff", "ask", "600"))
        self.assertTrue(name.startswith("ask-7-v1-"))
        box = self.root / "secretary/outbox"
        self.assertEqual(stat.S_IMODE(box.stat().st_mode), 0o700)
        self.assertEqual(list(box.iterdir()), [])                              # 控制面给了结论：投递区不留（第 1 轮 P2-03）

    def test_file_name_never_comes_from_the_proposal(self):
        """第 1 轮 P1-01：提议里的类别、卡片号、版本带 ../ 也不进路径；文件名只用巡视侧可信的值。"""
        self.fake_gate("import os\nprint(json.dumps({'result': 'handoff', 'msg': os.path.abspath(args[2])}))\n")
        evil = {"schema": 1, "category": "../../../evil", "card": "../x", "card_version": "/tmp/y"}
        out = SP.submit(evil, "ask", self.CARD)
        path = Path(out["msg"])
        self.assertEqual(path.parent.resolve(), (self.root / "secretary/outbox").resolve())
        self.assertTrue(path.name.startswith("ask-7-v1-"))
        self.assertFalse(any(p.name == "evil" or p.name.startswith("evil") for p in self.root.rglob("*")))

    def test_oversized_proposal_becomes_error(self):
        self.fake_gate("p = json.load(open(args[2]))\nprint(json.dumps({'result': 'handoff', 'msg': p.get('error') or ''}))\n")
        big = {"schema": 1, "category": "ask", "card": 7, "card_version": 1, "text": {"basis": "长" * SG.MAX_PROPOSAL}}
        self.assertIn("太长", SP.submit(big, "ask", self.CARD)["msg"])

    def test_unreachable_keeps_file_and_old_files_are_pruned(self):
        self.fake_gate("print('坏了')\n")
        self.assertEqual(SP.submit({"category": "ask"}, "ask", self.CARD)["result"], "unreachable")
        (f,) = list((self.root / "secretary/outbox").iterdir())                 # 交不上：留着
        old = 1_000_000.0
        os.utime(f, (old, old))
        SP.prune_outbox(now=old + SP.OUTBOX_KEEP - 1)
        self.assertTrue(f.exists())
        SP.prune_outbox(now=old + SP.OUTBOX_KEEP + 1)
        self.assertFalse(f.exists())

    def test_unrecognised_or_broken_gate_is_unreachable(self):
        for body in ("print('不是 JSON')\n", "print(json.dumps({'result': 'maybe'}))\n", "sys.exit(3)\n", ""):
            with self.subTest(body=body):
                self.fake_gate(body)
                self.assertEqual(SP.submit({"category": "ask", "card": 7, "card_version": 1}, "ask", self.CARD)["result"],
                                 "unreachable")

    def test_unreachable_is_retried(self):
        self.fake_gate("print('坏了')\n")
        card = {"id": 7, "version": 1, "kind": "ask", "state": "open"}
        with mock.patch.object(SP.R, "read", lambda: {"cards": [card]}), mock.patch.object(SG, "TEST_ASK", False):
            self.assertEqual([o["result"] for o in SP.once()], ["unreachable"])
            self.assertEqual([o["result"] for o in SP.once()], ["unreachable"])

    def test_unreadable_state_file_is_not_overwritten(self):
        """读不懂、或是合法 JSON 但不是「卡片版本 → 记录」的对象（第 1 轮 P2-02）：退出、不覆盖。"""
        f = SP.state_file()
        f.parent.mkdir(parents=True)
        self.fake_gate("print(json.dumps({'result': 'handoff'}))\n")
        for bad in ("{坏", "[]", "null", '{"7:1": "handoff"}', '{"7:1": {"msg": "x"}}'):
            with self.subTest(bad=bad):
                f.write_text(bad, encoding="utf-8")
                with self.assertRaises(SystemExit):
                    SP.load_state()
                with mock.patch.object(SP.R, "read", lambda: {"cards": [self.CARD]}), self.assertRaises(SystemExit):
                    SP.once()
                self.assertEqual(f.read_text(encoding="utf-8"), bad)

    def test_only_one_round_at_a_time(self):
        """第 1 轮 P1-04 第二层：另一轮巡视拿着锁时，这一轮不跑。"""
        import fcntl
        SP.secretary_dir().mkdir(parents=True)
        fd = os.open(SP.secretary_dir() / "patrol.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with mock.patch.object(SP.R, "read", side_effect=AssertionError("不该读卡")), self.assertRaises(SystemExit):
            SP.once()


class HermesFlags(unittest.TestCase):
    """第 1 轮 P2-04：本机 Hermes 不认得要用的参数就不调模型，按出错交常浩。"""
    def setUp(self):
        p = mock.patch.object(SP, "_flags_ok", None)
        p.start()
        self.addCleanup(p.stop)

    def help_says(self, text, rc=0):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, rc, stdout=text, stderr="")
        return calls, mock.patch.object(SP.subprocess, "run", run)

    def test_missing_flag_means_no_model_call(self):
        calls, patch = self.help_says("usage: hermes chat --quiet --query-file --source --toolsets --max-turns --run-budget")
        with patch:
            out = SP.judge({"id": 7, "version": 1})
        self.assertIn("--ignore-rules", out["error"])
        self.assertEqual([c[:3] for c in calls], [[SP.HERMES_BIN, "chat", "--help"]])   # 只查了参数，没调模型

    def test_all_flags_known(self):
        calls, patch = self.help_says(" ".join(SP.HERMES_FLAGS))
        with patch:
            self.assertTrue(SP.hermes_supported())
            self.assertTrue(SP.hermes_supported())
        self.assertEqual(len(calls), 1)                                          # 每个进程只查一次

    def test_flags_match_whole_words(self):
        """第 3 轮 P2-04 建议：--quiet-mode 这种只是包含的不算认得 --quiet。"""
        words = " ".join(f + "-mode" if f == "--quiet" else f for f in SP.HERMES_FLAGS)
        _, patch = self.help_says(words)
        with patch:
            self.assertFalse(SP.hermes_supported())

    def test_help_fails(self):
        _, patch = self.help_says(" ".join(SP.HERMES_FLAGS), rc=2)
        with patch:
            self.assertFalse(SP.hermes_supported())


if __name__ == "__main__":
    unittest.main()
