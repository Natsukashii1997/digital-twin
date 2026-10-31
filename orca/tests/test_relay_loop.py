"""relay_loop 单测：替换和外部打交道的四个函数（终端、页面跳转、relay、飞书），不连真实 ORCA / ChatGPT / 飞书。

运行：python3 -B -m unittest discover -s orca/tests
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["ORCA_BIN"] = os.path.join(HERE, "fake_orca.py")
import chatgpt_relay as relay  # noqa: E402
import relay_loop as loop  # noqa: E402

PROJECT = "https://chatgpt.com/g/g-p-x/project"


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["RELAY_STATE_DIR"] = os.path.join(self.dir, "relay-state")
        self.run_dir = os.path.join(self.dir, "run")
        self.task = os.path.join(self.dir, "task.md")
        with open(self.task, "w") as f:
            f.write("# 合成任务：给 add 补测试\n内容\n")
        self.verdicts = []      # 每次送审依次给出的结论
        self.claude_writes = True
        self.sent, self.gotos, self.relays, self.notes = [], [], [], []
        self.fail_relay_at = None
        self.collect_misses = 0
        self.patch(loop, "terminal_send", self.fake_terminal)
        self.patch(loop, "goto_chat", lambda a, url: self.gotos.append(url))
        self.patch(loop, "run_relay", self.fake_relay)
        self.patch(loop, "lark_send", lambda text, key: self.notes.append((text, key)))

    def patch(self, mod, name, fn):
        old = getattr(mod, name)
        setattr(mod, name, fn)
        self.addCleanup(setattr, mod, name, old)

    def fake_terminal(self, terminal, text):
        # 假 Claude：照消息里的路径写送审包，最后建就绪文件
        self.sent.append(text)
        if self.claude_writes:
            packet = re.search(r"写到 (\S+)", text).group(1)
            ready = re.search(r"空文件 (\S+) ", text).group(1)
            with open(packet, "w") as f:
                f.write("送审包 %d：%s\n" % (len(self.sent), text[:20]))
            open(ready, "w").close()

    def fake_relay(self, argv):
        self.relays.append(argv)
        if argv[0] == "collect" and self.collect_misses:
            self.collect_misses -= 1
            return 2                                   # 回复还没写完，没有回执
        if self.fail_relay_at == len(self.relays):
            raise relay.RelayError("模拟 relay 中断")
        get = lambda k: argv[argv.index(k) + 1]
        with open(get("--packet")) as f:
            packet = f.read()
        chat = get("--chat") if "--chat" in argv else self.gotos[-1]
        url = chat if "/c/" in chat else "https://chatgpt.com/g/g-p-x/c/new1"
        verdict = self.verdicts.pop(0)
        relay.write_record(get("--out"), packet, "意见。\nVERDICT: %s" % verdict, url, verdict)
        relay.Marker(packet).clear()
        return relay.EXIT[verdict]

    def go(self, *extra):
        with contextlib.redirect_stdout(io.StringIO()):
            return loop.main(["run", "--run-dir", self.run_dir, "--task", self.task, "--repo", "/repo",
                              "--worktree", "path:/x", "--page", "p1", "--project", PROJECT, "--claude-term", "term_c",
                              "--poll", "0", "--claude-timeout", "1", *extra])

    def test_pass_on_first_round(self):
        self.verdicts = ["PASS"]
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("不要合并", self.sent[0])
        self.assertEqual(self.gotos, [PROJECT])
        self.assertEqual(len(self.notes), 1)
        self.assertIn("PASS", self.notes[0][0])
        self.assertIn("合成任务：给 add 补测试", self.notes[0][0])

    def test_rework_then_pass_stays_in_the_same_chat(self):
        self.verdicts = ["REWORK", "PASS"]
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("REWORK", self.sent[1])
        self.assertIn("round-1-回执.md", self.sent[1])
        chat = "https://chatgpt.com/g/g-p-x/c/new1"
        self.assertEqual(self.gotos, [PROJECT, chat])
        self.assertEqual(self.relays[1][self.relays[1].index("--chat") + 1], chat)
        self.assertEqual([n[0].count("PASS") for n in self.notes], [1])

    def test_rework_limit_stops_with_needs_human(self):
        self.verdicts = ["REWORK", "REWORK", "REWORK"]
        self.assertEqual(self.go(), 20)
        self.assertEqual(len(self.relays), 3)          # 首轮 + 2 轮返修
        self.assertEqual(len(self.sent), 3)            # 任务 + 2 次返修
        self.assertEqual(len(self.notes), 1)
        self.assertIn("上限", self.notes[0][0])

    def test_needs_human_stops_and_notifies(self):
        self.verdicts = ["NEEDS_HUMAN"]
        self.assertEqual(self.go(), 20)
        self.assertEqual(len(self.notes), 1)
        self.assertIn("NEEDS_HUMAN", self.notes[0][0])

    def test_resume_after_interruption_does_not_repeat(self):
        self.verdicts = ["REWORK", "PASS"]
        self.fail_relay_at = 2
        self.assertEqual(self.go(), 2)
        self.assertEqual(len(self.notes), 1)
        self.assertIn("模拟 relay 中断", self.notes[0][0])
        self.fail_relay_at = None
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.sent), 2)            # 任务和返修各交一次，重跑不重发
        self.assertEqual(len(self.relays), 3)          # 第 1 轮有回执，不再送审
        self.assertEqual(len(self.notes), 2)
        self.assertEqual(self.go(), 0)                 # 收口后再跑：读回执，不重复通知
        self.assertEqual(len(self.notes), 2)
        self.assertEqual(len(self.relays), 3)

    def test_unknown_handoff_needs_confirmation(self):
        self.verdicts = ["PASS"]
        os.makedirs(os.path.join(self.run_dir, "handoff"))
        with open(os.path.join(self.run_dir, "handoff", "kickoff.json"), "w") as f:
            json.dump({"status": "sending"}, f)
        self.assertEqual(self.go(), 2)
        self.assertEqual(self.sent, [])
        self.assertIn("结果不明", self.notes[0][0])
        self.assertEqual(self.go("--confirm-not-received"), 0)
        self.assertEqual(len(self.sent), 1)

    def test_claude_timeout_and_empty_packet_are_errors(self):
        self.claude_writes = False
        self.assertEqual(self.go(), 2)
        self.assertIn("等 Claude 超时", self.notes[0][0])
        open(os.path.join(self.run_dir, "round-1.ready"), "w").close()
        self.assertEqual(self.go(), 2)
        self.assertIn("不存在或为空", self.notes[1][0])
        self.assertEqual(self.relays, [])

    def test_unknown_send_is_collected_in_the_bound_chat(self):
        self.verdicts = ["PASS"]
        self.claude_writes = False                     # 送审包上次已经写好并发出，这次只差收回执
        os.makedirs(self.run_dir)
        packet = os.path.join(self.run_dir, "round-1-packet.md")
        with open(packet, "w") as f:
            f.write("上次发出去结果不明的送审包\n")
        open(os.path.join(self.run_dir, "round-1.ready"), "w").close()
        relay.Marker(open(packet).read()).create("abc", PROJECT)
        self.collect_misses = 2                        # 前两次回复还没写完
        self.assertEqual(self.go(), 0)
        self.assertEqual([r[0] for r in self.relays], ["collect"] * 3)
        self.assertEqual(self.gotos, ["https://chatgpt.com/g/g-p-x/c/abc"])

    def test_temporary_or_missing_conversation_is_not_collected(self):
        os.makedirs(self.run_dir)
        packet = os.path.join(self.run_dir, "round-1-packet.md")
        for conv in ("local-chatgpt%3A9ad4", None):
            with self.subTest(conv=conv):
                with open(packet, "w") as f:
                    f.write("送审包 %s\n" % conv)
                open(os.path.join(self.run_dir, "round-1.ready"), "w").close()
                relay.Marker(open(packet).read()).create(conv, PROJECT)
                self.claude_writes = False
                self.assertEqual(self.go(), 2)
                self.assertIn("没有绑定会话", self.notes[-1][0])
                self.assertEqual(self.relays, [])

    def test_notification_failure_does_not_hide_the_result(self):
        self.verdicts = ["PASS"]

        def broken(text, key):
            raise relay.RelayError("飞书挂了")
        self.patch(loop, "lark_send", broken)
        self.assertEqual(self.go(), 2)                 # PASS 的通知没发出去：按出错处理，重跑会补发
        self.patch(loop, "lark_send", lambda text, key: self.notes.append((text, key)))
        self.assertEqual(self.go(), 0)
        self.assertIn("PASS", self.notes[-1][0])


if __name__ == "__main__":
    unittest.main()
