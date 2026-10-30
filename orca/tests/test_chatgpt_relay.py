"""chatgpt_relay 单测：用假 orca，不连真实 ORCA / ChatGPT。

运行：python3 -B -m unittest discover -s orca/tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ["ORCA_BIN"] = os.path.join(HERE, "fake_orca.py")
import chatgpt_relay as relay  # noqa: E402

relay.ORCA = os.environ["ORCA_BIN"]


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, "state.json")
        os.environ["FAKE_ORCA_STATE"] = self.state
        self.packet = os.path.join(self.dir, "packet.md")
        with open(self.packet, "w") as f:
            f.write("合成送审包：请审 `add(a, b)`。\n```python\ndef add(a, b):\n    return a + b\n```\n")
        self.out = os.path.join(self.dir, "回执.md")

    def page(self, **state):
        with open(self.state, "w") as f:
            json.dump(state, f)

    def calls(self):
        with open(self.state) as f:
            return json.load(f)

    def review(self, *extra):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = relay.main(["review", "--worktree", "path:/x", "--page", "p1", "--packet", self.packet,
                               "--out", self.out, "--poll", "0", "--timeout", "2", *extra])
        return code, err.getvalue()

    def test_pass_writes_record_and_does_not_wake_claude(self):
        self.page(reply="看过了，没问题。\nVERDICT: PASS", stream_polls=2)
        code, _ = self.review("--deliver-to", "term_1")
        self.assertEqual(code, 0)
        with open(self.out) as f:
            rec = f.read()
        self.assertIn("**PASS**", rec)
        self.assertIn("看过了，没问题。", rec)
        self.assertIn("https://chatgpt.com/c/fake", rec)
        self.assertFalse(any(c[:2] == ["terminal", "send"] for c in self.calls()["calls"]))

    def test_footer_and_packet_are_sent_verbatim(self):
        self.page(reply="VERDICT: PASS")
        self.review()
        inserted = self.calls()["inserted"]
        with open(self.packet) as f:
            self.assertIn(json.dumps(f.read() + relay.FOOTER), inserted)

    def test_rework_delivers_record_path_to_terminal(self):
        self.page(reply="测试和验收标准不一致。\nVERDICT: REWORK")
        code, _ = self.review("--deliver-to", "term_1")
        self.assertEqual(code, 10)
        sends = [c for c in self.calls()["calls"] if c[:2] == ["terminal", "send"]]
        self.assertEqual(len(sends), 1)
        self.assertIn("term_1", sends[0])
        self.assertIn(os.path.abspath(self.out), sends[0][sends[0].index("--text") + 1])

    def test_missing_verdict_is_needs_human(self):
        self.page(reply="我觉得还行吧")
        self.assertEqual(self.review("--deliver-to", "term_1")[0], 20)
        self.assertFalse(any(c[:2] == ["terminal", "send"] for c in self.calls()["calls"]))

    def test_conflicting_verdicts_are_needs_human(self):
        self.assertEqual(relay.parse_verdict("VERDICT: PASS\n...\nVERDICT: REWORK"), "NEEDS_HUMAN")
        self.assertEqual(relay.parse_verdict("格式示例 VERDICT: PASS 写在句中不算"), "NEEDS_HUMAN")
        self.assertEqual(relay.parse_verdict("结论如下\n`VERDICT: REWORK`\n"), "REWORK")
        self.assertEqual(relay.parse_verdict("VERDICT：PASS"), "PASS")

    def test_not_logged_in_sends_nothing(self):
        self.page(composer=False, reply="x")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("没登录", err)
        self.assertNotIn("sent", self.calls())
        self.assertFalse(os.path.exists(self.out))

    def test_composer_mismatch_sends_nothing(self):
        self.page(mangle=True, reply="VERDICT: PASS")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("不一致", err)
        self.assertNotIn("sent", self.calls())
        self.assertFalse(os.path.exists(self.out))

    def collect(self):
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = relay.main(["collect", "--worktree", "path:/x", "--page", "p1", "--packet", self.packet,
                               "--out", self.out, "--deliver-to", "term_1"])
        return code, err.getvalue()

    def test_collect_takes_finished_reply_without_resending(self):
        with open(self.packet) as f:
            sent = f.read() + relay.FOOTER
        self.page(sent=True, polls=5, composer_text=sent.replace("```python", "").replace("`", "") + "\n…\n显示更多", reply="缺测试。\nVERDICT: REWORK")
        code, _ = self.collect()
        self.assertEqual(code, 10)
        calls = self.calls()["calls"]
        self.assertFalse(any("/*relay:send*/" in " ".join(c) for c in calls))
        self.assertEqual(sum(c[:2] == ["terminal", "send"] for c in calls), 1)
        with open(self.out) as f:
            self.assertIn("缺测试。", f.read())

    def test_bubble_match_accepts_only_a_long_enough_prefix(self):
        sent = "送审包" + "正文" * 400 + "`x`\n```python\nprint(1)\n```\n" + relay.FOOTER
        shown = "送审包" + "正文" * 200 + "\n…\n显示更多"
        self.assertTrue(relay.bubble_matches(shown, sent))
        self.assertTrue(relay.bubble_matches(sent.replace("```python", "").replace("`", ""), sent))
        self.assertFalse(relay.bubble_matches("送审包正文\n…\n显示更多", sent))  # 太短，不足以认定
        self.assertFalse(relay.bubble_matches("别的" + "正文" * 200, sent))

    def test_collect_refuses_reply_to_another_packet(self):
        self.page(sent=True, polls=5, composer_text="别的送审包", reply="VERDICT: PASS")
        code, err = self.collect()
        self.assertEqual(code, 2)
        self.assertIn("不是这份送审包", err)
        self.assertFalse(os.path.exists(self.out))

    def test_refuses_to_interrupt_streaming(self):
        self.page(streaming_before=True, reply="x")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertNotIn("sent", self.calls())

    def test_timeout_writes_no_record(self):
        self.page(never_reply=True, reply="x")
        relay_timeout = self.review()
        self.assertEqual(relay_timeout[0], 2)
        self.assertIn("超时", relay_timeout[1])
        self.assertFalse(os.path.exists(self.out))

    def test_existing_record_is_never_overwritten(self):
        with open(self.out, "w") as f:
            f.write("旧回执")
        self.page(reply="VERDICT: PASS")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertNotIn("calls", self.calls())
        with open(self.out) as f:
            self.assertEqual(f.read(), "旧回执")

    def test_orca_failure_is_reported(self):
        self.page(fail="eval", reply="x")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("orca eval 失败", err)


if __name__ == "__main__":
    unittest.main()
