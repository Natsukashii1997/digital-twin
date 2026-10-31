"""relay_loop 单测：替换和外部打交道的五个函数（终端、页面跳转、relay、飞书、查已合并 PR），不连真实 ORCA / ChatGPT / 飞书。

运行：python3 -B -m unittest discover -s orca/tests
"""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["ORCA_BIN"] = os.path.join(HERE, "fake_orca.py")
import chatgpt_relay as relay  # noqa: E402
import relay_loop as loop  # noqa: E402

PROJECT = "https://chatgpt.com/g/g-p-x/project"
REAL_TERMINAL_SEND, REAL_RUN_RELAY, REAL_LARK_SEND = loop.terminal_send, loop.run_relay, loop.lark_send

# 两个真实进程同时跑同一运行目录（P1-02）：假 Claude 收到任务后慢 1 秒才写送审包，把并发窗口拉开
TWIN = r"""
import json, os, re, sys, time
sys.path.insert(0, %(orca)r)
import chatgpt_relay as relay, relay_loop as loop
log = lambda name, text: open(os.path.join(%(dir)r, name), "a").write(json.dumps(text, ensure_ascii=False) + "\n")
def terminal(term, text):
    log("claude.log", text)
    time.sleep(1)
    with open(re.search(r"写到 (\S+)", text).group(1), "w") as f:
        f.write("送审包\n")
    open(re.search(r"空文件 (\S+) ", text).group(1), "w").close()
def fake_relay(argv):
    get = lambda k: argv[argv.index(k) + 1]
    with open(get("--packet")) as f:
        packet = f.read()
    relay.write_record(get("--out"), packet, "意见。\nVERDICT: PASS", "https://chatgpt.com/g/g-p-x/c/new1", "PASS")
    return 0
loop.terminal_send, loop.run_relay, loop.goto_chat = terminal, fake_relay, lambda a, url: None
loop.lark_send = lambda text, key: log("notes.log", text)
loop.merged_prs = lambda a, since: []
sys.exit(loop.main(["run", "--run-dir", %(run)r, "--task", %(task)r, "--repo", "/repo", "--worktree", "path:/x",
                    "--page", "p1", "--project", "https://chatgpt.com/g/g-p-x/project", "--claude-term", "term_c",
                    "--poll", "0", "--claude-timeout", "10"]))
"""


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
        self.merged = []        # 任务开工后被合并的 PR
        self.patch(loop, "merged_prs", lambda a, since: self.merged)

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
        relay.Marker("上次发出去结果不明的送审包\n").create("abc", PROJECT)
        self.collect_misses = 2                        # 前两次回复还没写完
        self.assertEqual(self.go(), 0)
        self.assertEqual([r[0] for r in self.relays], ["collect"] * 3)
        self.assertEqual(self.gotos, ["https://chatgpt.com/g/g-p-x/c/abc"])

    def test_temporary_or_missing_conversation_is_not_collected(self):
        for i, conv in enumerate(("local-chatgpt%3A9ad4", None)):
            with self.subTest(conv=conv):
                self.run_dir = os.path.join(self.dir, "run-%d" % i)
                os.makedirs(self.run_dir)
                packet = os.path.join(self.run_dir, "round-1-packet.md")
                body = "送审包 %s\n" % conv
                with open(packet, "w") as f:
                    f.write(body)
                open(os.path.join(self.run_dir, "round-1.ready"), "w").close()
                relay.Marker(body).create(conv, PROJECT)
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

    def errors(self):
        with open(os.path.join(self.run_dir, "errors.jsonl")) as f:
            return [json.loads(l) for l in f]

    def test_stale_pass_record_is_not_reused(self):
        # P1-01：运行目录里留着旧任务 / 旧版本的有效 PASS 回执，当前送审包已经变了：拒绝复用，不发 PASS 通知
        os.makedirs(self.run_dir)
        relay.write_record(os.path.join(self.run_dir, "round-1-回执.md"), "旧版本的送审包\n", "好。\nVERDICT: PASS",
                           "https://chatgpt.com/g/g-p-x/c/old", "PASS")
        self.assertEqual(self.go(), 2)
        self.assertEqual(self.relays, [])
        self.assertFalse(any("PASS" in n[0] for n in self.notes))
        self.assertIn("对不上", self.notes[-1][0])
        self.assertIn("回执里的送审包摘要", self.notes[-1][0])

    def test_record_from_another_run_with_same_packet_is_not_reused(self):
        # P1-01：送审包内容碰巧一样、但回执不是本运行目录送出去的（账上没有这一轮），同样不认
        os.makedirs(self.run_dir)
        self.claude_writes = False
        with open(os.path.join(self.run_dir, "round-1-packet.md"), "w") as f:
            f.write("同样的送审包\n")
        relay.write_record(os.path.join(self.run_dir, "round-1-回执.md"), "同样的送审包\n",
                           "好。\nVERDICT: PASS", "https://chatgpt.com/g/g-p-x/c/old", "PASS")
        self.assertEqual(self.go(), 2)
        self.assertNotIn("回执里的送审包摘要", self.notes[-1][0])
        self.assertIn("本运行目录记下的送审包", self.notes[-1][0])
        self.assertFalse(any("PASS" in n[0] for n in self.notes))

    def test_packet_changed_after_pass_is_not_reused(self):
        # P1-01：收口后送审包被改，重跑不能再认旧回执
        self.verdicts = ["PASS"]
        self.assertEqual(self.go(), 0)
        with open(os.path.join(self.run_dir, "round-1-packet.md"), "a") as f:
            f.write("事后追加的改动\n")
        self.assertEqual(self.go(), 2)
        self.assertEqual([n[0].count("审核 PASS") for n in self.notes], [1, 0])
        self.assertIn("对不上", self.notes[-1][0])

    def test_rerun_with_another_task_is_refused(self):
        # P1-01：同一运行目录换了任务说明，拒绝续跑
        self.verdicts = ["REWORK"]
        self.fail_relay_at = 1
        self.assertEqual(self.go(), 2)
        with open(self.task, "w") as f:
            f.write("# 另一个任务\n")
        self.assertEqual(self.go(), 2)
        self.assertIn("属于另一个任务", self.notes[-1][0])
        self.assertEqual(len(self.sent), 1)

    def test_two_processes_on_one_run_dir(self):
        # P1-02：两个进程同时用同一 --run-dir 启动：只有一个干活，Claude 只收到一次任务，PASS 只通知一次
        code = TWIN % {"orca": os.path.dirname(HERE), "dir": self.dir, "run": self.run_dir, "task": self.task}
        procs = [subprocess.Popen([sys.executable, "-B", "-c", code], stdout=subprocess.DEVNULL) for _ in range(2)]
        codes = sorted(p.wait(timeout=60) for p in procs)
        self.assertEqual(codes, [0, 3])
        with open(os.path.join(self.dir, "claude.log")) as f:
            self.assertEqual(len(f.readlines()), 1)
        with open(os.path.join(self.dir, "notes.log")) as f:
            notes = f.readlines()
        self.assertEqual(len(notes), 1)
        self.assertIn("PASS", notes[0])

    def test_held_lock_refuses_without_side_effects(self):
        # P1-02：锁被别的进程拿着时，本进程不交付、不送审、不通知
        os.makedirs(self.run_dir)
        holder = subprocess.Popen([sys.executable, "-c", "import fcntl,sys,time\nf=open(sys.argv[1],'a')\n"
                                   "fcntl.flock(f,fcntl.LOCK_EX)\nprint('ok',flush=True)\ntime.sleep(30)",
                                   os.path.join(self.run_dir, ".lock")], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "ok")
        self.verdicts = ["PASS"]
        self.assertEqual(self.go(), 3)
        self.assertEqual((self.sent, self.relays, self.notes), ([], [], []))
        holder.kill()
        holder.wait()
        self.assertEqual(self.go(), 0)

    def test_corrupt_state_stops_and_is_recorded(self):
        # P1-05：state.json 损坏不再直接抛 JSONDecodeError：停下、通知、落盘
        self.verdicts = ["REWORK"]
        self.fail_relay_at = 1
        self.assertEqual(self.go(), 2)
        with open(os.path.join(self.run_dir, "state.json"), "w") as f:
            f.write("{坏的")
        self.assertEqual(self.go(), 2)
        self.assertIn("state.json 损坏", self.notes[-1][0])
        self.assertTrue(self.errors()[-1]["notified"])

    def test_notification_timeout_is_recorded(self):
        # P1-05：飞书命令超时不再逃逸；通知发不出去时，出错状态仍落盘
        owner = os.path.join(self.dir, "owner")
        with open(owner, "w") as f:
            f.write("ou_x\n")
        self.patch(loop, "OWNER_FILE", owner)
        self.patch(loop, "lark_send", REAL_LARK_SEND)

        def timeout(*args, **kw):
            raise subprocess.TimeoutExpired(args[0], 60)
        self.patch(loop.subprocess, "run", timeout)
        self.verdicts = ["PASS"]
        self.assertEqual(self.go(), 2)
        last = self.errors()[-1]
        self.assertIn("60 秒没有返回", last["error"])
        self.assertFalse(last["notified"])
        self.assertIn("60 秒没有返回", last["notify_error"])

    def test_unexpected_exception_and_init_error_are_handled(self):
        # P1-05：没预料到的异常、任务文件读不到（初始化阶段），都走统一出错处理，不直接抛出
        def bug(argv):
            raise ValueError("模拟程序缺陷")
        self.patch(loop, "run_relay", bug)
        self.assertEqual(self.go(), 2)
        self.assertIn("模拟程序缺陷", self.notes[-1][0])
        self.assertIn("ValueError", self.errors()[-1]["error"])
        os.unlink(self.task)
        self.assertEqual(self.go(), 2)
        self.assertIn("FileNotFoundError", self.errors()[-1]["error"])

    def test_max_rework_cannot_exceed_policy(self):
        # P2-01：D3-5 上限 2 轮是政策，命令行不能调大
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.go("--max-rework", "3")
        self.assertFalse(os.path.exists(self.run_dir))
        self.verdicts = ["REWORK", "REWORK"]
        self.assertEqual(self.go("--max-rework", "1"), 20)

    def test_prompt_blocked_fails_fast_and_can_be_resent(self):
        # P2-04：Claude 停在确认菜单时 orca 拒收（确定没发出）：立刻停下通知，不等就绪超时；处理菜单后重跑直接重发
        def blocked(*args, **kw):
            e = relay.RelayError("orca terminal 失败")
            e.code = "agent_prompt_blocked"
            raise e
        self.patch(relay, "orca", blocked)
        self.patch(loop, "terminal_send", REAL_TERMINAL_SEND)
        self.assertEqual(self.go("--claude-timeout", "3600"), 2)
        self.assertIn("确认菜单", self.notes[-1][0])
        self.assertFalse(os.path.exists(os.path.join(self.run_dir, "handoff", "kickoff.json")))
        self.patch(loop, "terminal_send", self.fake_terminal)
        self.verdicts = ["PASS"]
        self.assertEqual(self.go(), 0)
        self.assertEqual(len(self.sent), 1)

    def test_merged_pr_during_task_stops_the_loop(self):
        # P1-03：执行方不许合并：送审前或 PASS 收口前发现开工后有 PR 被合并，就停下等常浩，不发 PASS
        self.merged = [41]
        self.verdicts = ["PASS"]
        self.assertEqual(self.go(), 20)
        self.assertEqual(self.relays, [])
        self.assertIn("#41", self.notes[-1][0])
        seq = [[], [42]]
        self.run_dir += "-2"
        self.patch(loop, "merged_prs", lambda a, since: seq.pop(0))
        self.verdicts = ["PASS"]
        self.assertEqual(self.go(), 20)
        self.assertEqual(len(self.relays), 1)            # 送审了，但 PASS 收口前查到合并
        self.assertIn("#42", self.notes[-1][0])
        self.assertFalse(any("审核 PASS" in n[0] for n in self.notes))

    def test_interrupted_bind_is_not_resent(self):
        # P1-06：新会话发出后、绑定真实会话编号时进程中断：发送标记仍是完整的 JSON；驱动停下通知，重跑不重发
        state = os.path.join(self.dir, "orca.json")
        os.environ["FAKE_ORCA_STATE"] = state
        with open(state, "w") as f:
            json.dump({"url_before": PROJECT, "reply": "VERDICT: PASS"}, f)
        self.patch(loop, "run_relay", REAL_RUN_RELAY)
        self.patch(relay.time, "sleep", lambda s: None)
        real_bind = relay.Marker.bind

        def interrupted(marker, conv):
            real_replace = os.replace

            def boom(*a):
                raise OSError("模拟绑定时进程中断")
            os.replace = boom
            try:
                real_bind(marker, conv)
            finally:
                os.replace = real_replace
        self.patch(relay.Marker, "bind", interrupted)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.go(), 2)
            self.assertIn("没有拿到回执", self.notes[-1][0])
            with open(os.path.join(self.run_dir, "round-1-packet.md")) as f:
                marker = relay.Marker(f.read())
            self.assertIsNone(marker.load()["conversation"])     # 标记完整可读，只是没绑上
            self.assertEqual(self.go(), 2)
        self.assertIn("没有绑定会话", self.notes[-1][0])
        with open(state) as f:
            sends = [c for c in json.load(f)["calls"] if c[0] == "eval" and "/*relay:send*/" in c[-1]]
        self.assertEqual(len(sends), 1)


if __name__ == "__main__":
    unittest.main()
