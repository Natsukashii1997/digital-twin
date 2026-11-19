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
        self.extra_cards = []

    def snapshot(self):
        import contextlib
        import sqlite3
        with contextlib.closing(sqlite3.connect(self.hubdb)) as con:
            rows = con.execute("SELECT id, version, kind, state, title, text, options, rule, lane FROM cards").fetchall()
        cards = [{"id": r[0], "version": r[1], "kind": r[2], "state": r[3], "title": r[4], "text": r[5],
                  "options": json.loads(r[6] or "[]"), "rule": r[7], "lane": r[8]} for r in rows]
        return {"cards": cards + self.extra_cards}

    def fake_submit(self, p):
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

    def test_ask_switch_on_model_proposal_goes_through(self):
        reply = {"category": "ask", "card": 7, "card_version": 1, "choice": 1, "text": dict(TSG.TEXT), "abstain": None,
                 "error": None}
        with self.fake_model(reply):
            out = SP.once()
        self.assertEqual([o["result"] for o in out], ["done"], out)
        call = self.model_calls[0]
        cmd = call["cmd"]
        self.assertEqual(cmd[:3], [SP.HERMES_BIN, "chat", "-Q"])
        for flag, val in (("--source", "tool"), ("-t", "todo"), ("--max-turns", "1")):
            self.assertEqual(cmd[cmd.index(flag) + 1], val)
        self.assertEqual(call["query"].splitlines()[0], SG.run_marker(7, 1))    # 第一行就是标记
        self.assertIn("1. 30 秒", call["query"])
        self.assertFalse(Path(call["cwd"]).exists())                            # 空的临时目录，用完就删
        self.assertFalse([k for k in call["env"] if k.startswith(("DT_", "AGENT_AUTOLOOP", "HERMES_SESSION_"))
                          or any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET"))])
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

    def test_writes_private_file_and_reads_last_line(self):
        self.fake_gate("p = json.load(open(args[2]))\nprint('别的输出')\n"
                       "print(json.dumps({'ok': False, 'result': 'handoff', 'msg': p['category'], 'decision': 'd-1'}))\n")
        out = SP.submit({"schema": 1, "category": "ask", "card": 7, "card_version": 1})
        self.assertEqual((out["result"], out["msg"]), ("handoff", "ask"))
        box = self.root / "secretary/outbox"
        self.assertEqual(stat.S_IMODE(box.stat().st_mode), 0o700)
        (f,) = list(box.iterdir())
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertTrue(f.name.startswith("ask-7-v1-"))

    def test_unrecognised_or_broken_gate_is_unreachable(self):
        for body in ("print('不是 JSON')\n", "print(json.dumps({'result': 'maybe'}))\n", "sys.exit(3)\n", ""):
            with self.subTest(body=body):
                self.fake_gate(body)
                self.assertEqual(SP.submit({"category": "ask", "card": 7, "card_version": 1})["result"], "unreachable")

    def test_unreachable_is_retried(self):
        self.fake_gate("print('坏了')\n")
        card = {"id": 7, "version": 1, "kind": "ask", "state": "open"}
        with mock.patch.object(SP.R, "read", lambda: {"cards": [card]}), mock.patch.object(SG, "TEST_ASK", False):
            self.assertEqual([o["result"] for o in SP.once()], ["unreachable"])
            self.assertEqual([o["result"] for o in SP.once()], ["unreachable"])

    def test_unreadable_state_file_is_not_overwritten(self):
        f = SP.state_file()
        f.parent.mkdir(parents=True)
        f.write_text("{坏", encoding="utf-8")
        with self.assertRaises(SystemExit):
            SP.load_state()
        self.assertEqual(f.read_text(encoding="utf-8"), "{坏")


if __name__ == "__main__":
    unittest.main()
