"""chatgpt_relay 单测：用假 orca，不连真实 ORCA / ChatGPT。

运行：python3 -B -m unittest discover -s orca/tests
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import time
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
        os.environ["RELAY_STATE_DIR"] = os.path.join(self.dir, "state")

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
                               "--out", self.out, "--poll", "0", "--timeout", "2", "--settle", "0", *extra])
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
            packet = f.read()
        self.assertIn(json.dumps(relay.message_for(packet)), inserted)
        self.assertTrue(relay.message_for(packet).startswith("【RELAY 送审编号 R-%s】" % relay.sha(packet)[:16]))

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

    def test_verdict_is_only_the_last_non_empty_line(self):
        # PR #36 评审 P1-03：结论不在最后一行就不算
        self.assertEqual(relay.parse_verdict("VERDICT: PASS\n以下问题还没有审完。"), "NEEDS_HUMAN")
        self.assertEqual(relay.parse_verdict("格式示例 VERDICT: PASS 写在句中不算"), "NEEDS_HUMAN")
        self.assertEqual(relay.parse_verdict(""), "NEEDS_HUMAN")
        self.assertEqual(relay.parse_verdict("VERDICT: MAYBE"), "NEEDS_HUMAN")
        # 正文举例写到的结论行不影响末行（PR #36 评审原文就是这种情况）
        self.assertEqual(relay.parse_verdict("例如：\nVERDICT: PASS\n会被误收。\nVERDICT: REWORK"), "REWORK")
        self.assertEqual(relay.parse_verdict("结论如下\n`VERDICT: REWORK`\n\n"), "REWORK")
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
                               "--out", self.out, "--deliver-to", "term_1", "--settle", "0"])
        return code, err.getvalue()

    def test_collect_takes_finished_reply_without_resending(self):
        with open(self.packet) as f:
            sent = relay.message_for(f.read())
        self.page(sent=True, polls=5, composer_text=sent.replace("```python", "").replace("`", "") + "\n…\n显示更多", reply="缺测试。\nVERDICT: REWORK")
        code, _ = self.collect()
        self.assertEqual(code, 10)
        calls = self.calls()["calls"]
        self.assertFalse(any("/*relay:send*/" in " ".join(c) for c in calls))
        self.assertEqual(sum(c[:2] == ["terminal", "send"] for c in calls), 1)
        with open(self.out) as f:
            self.assertIn("缺测试。", f.read())

    def test_bubble_match_accepts_only_a_long_enough_prefix(self):
        sent = relay.message_for("送审包" + "正文" * 400 + "`x`\n```python\nprint(1)\n```\n")
        shown = sent[:sent.index("正文") + 400] + "\n…\n显示更多"
        self.assertTrue(relay.bubble_matches(shown, sent))
        self.assertTrue(relay.bubble_matches(sent.replace("```python", "").replace("`", ""), sent))
        self.assertFalse(relay.bubble_matches(sent[:40] + "\n…\n显示更多", sent))  # 太短，不足以认定
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

    # ---- PR #36 评审返修 ----

    def marker(self):
        with open(self.packet) as f:
            return relay.Marker(f.read()).path

    def test_flickering_stop_button_does_not_return_partial_reply(self):
        # P1-02：停止按钮短暂消失时文本还在变，不能当成写完
        self.page(script=[[True, "第"], [False, "第一段"], [True, "第一段，第二段"],
                          [False, "第一段，第二段\nVERDICT: PASS"]])
        code, _ = self.review()
        self.assertEqual(code, 0)
        with open(self.out) as f:
            self.assertIn("第一段，第二段\nVERDICT: PASS", f.read())

    def test_partial_pass_must_stay_stable_before_accepted(self):
        # 读到一次带 PASS 的半截回复，紧接着又变了：最终按写完的内容判
        # 半截 PASS 连读两次都一样，第三次才追加：至少 3 次读取不变才算写完
        self.page(script=[[False, "VERDICT: PASS"], [False, "VERDICT: PASS"],
                          [False, "VERDICT: PASS\n补充：缺测试\nVERDICT: REWORK"]])
        self.assertEqual(self.review()[0], 10)

    def test_reply_must_stay_unchanged_for_settle_seconds(self):
        self.page(reply="VERDICT: PASS", stream_polls=0)
        t = time.monotonic()
        with contextlib.redirect_stdout(io.StringIO()):
            code = relay.main(["review", "--worktree", "path:/x", "--page", "p1", "--packet", self.packet,
                               "--out", self.out, "--poll", "0.05", "--timeout", "5", "--settle", "0.6"])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(time.monotonic() - t, 0.6)

    def test_conversation_switch_while_waiting_is_refused(self):
        # P1-01：等回复期间会话换了
        self.page(script=[[True, "审核中"], [False, "VERDICT: PASS", "https://chatgpt.com/c/other"]])
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("会话变了", err)
        self.assertFalse(os.path.exists(self.out))
        self.assertTrue(os.path.exists(self.marker()))  # 消息已发出，结果不明

    def test_reply_to_another_user_message_is_refused(self):
        # P1-01：最新回复前的用户消息不是这份送审包
        self.page(reply="VERDICT: PASS", foreign_user="别人插进来的一条消息" * 40)
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("不是这份送审包", err)
        self.assertFalse(os.path.exists(self.out))

    def test_unknown_send_result_blocks_resend_until_collect(self):
        # P1-04：点了发送但 orca 报错 → 留标记；再 review 拒绝重发；collect 收取后清标记
        self.page(fail_after_send=True, reply="VERDICT: REWORK", stream_polls=0)
        self.assertEqual(self.review()[0], 2)
        self.assertTrue(os.path.exists(self.marker()))
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("结果不明", err)
        self.assertEqual(sum("/*relay:send*/" in " ".join(c) for c in self.calls()["calls"]), 1)
        st = self.calls()
        st.pop("fail_after_send")
        self.page(**st)
        self.assertEqual(self.collect()[0], 10)
        self.assertFalse(os.path.exists(self.marker()))

    def test_disabled_send_button_clears_marker(self):
        self.page(send_disabled=True, reply="x")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("发送按钮不可用", err)
        self.assertFalse(os.path.exists(self.marker()))

    def test_delivery_failure_keeps_record_and_can_be_retried(self):
        # P1-06：回执已落盘、终端交付失败 → 退出码 3；deliver 只重试交付，不重发送审
        self.page(reply="缺测试。\nVERDICT: REWORK", fail="terminal")
        code, _ = self.review("--deliver-to", "term_1")
        self.assertEqual(code, 3)
        self.assertTrue(os.path.exists(self.out))
        self.assertFalse(os.path.exists(self.marker()))
        st = self.calls()
        st.pop("fail")
        self.page(**st)
        self.assertEqual(self.terminal_sends(), 1)  # 第一次交付已发出，但工具报失败
        code, msg = self.deliver()
        self.assertEqual(code, 2)  # 结果不明（终端可能已收到）：不确认就不重发
        self.assertIn("结果不明", msg)
        self.assertEqual(self.terminal_sends(), 1)
        self.assertEqual(self.deliver("--confirm-not-received")[0], 10)
        self.assertEqual(self.terminal_sends(), 2)
        code, msg = self.deliver()  # P1-04-R2：已交付的再调用不重发
        self.assertEqual(code, 10)
        self.assertIn('"already": true', msg)
        self.assertEqual(self.terminal_sends(), 2)
        self.assertEqual(sum("/*relay:send*/" in " ".join(c) for c in self.calls()["calls"]), 1)

    def deliver(self, *extra):
        err, out = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = relay.main(["deliver", "--record", self.out, "--deliver-to", "term_1", *extra])
        return code, err.getvalue() + out.getvalue()

    def terminal_sends(self):
        return sum(c[:2] == ["terminal", "send"] for c in self.calls()["calls"])

    def test_deliver_refuses_non_rework_record(self):
        self.page(reply="VERDICT: PASS")
        self.review()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = relay.main(["deliver", "--record", self.out, "--deliver-to", "term_1"])
        self.assertEqual(code, 2)
        self.assertIn("不是 REWORK", err.getvalue())

    def test_record_is_written_atomically_without_temp_leftovers(self):
        self.page(reply="VERDICT: PASS")
        self.review()
        self.assertEqual(sorted(os.listdir(self.dir)), sorted(["packet.md", "state", "state.json", "回执.md"]))

    def test_collect_refuses_reply_that_is_still_changing(self):
        # P1-05：collect 隔一段读两次，文本变了就不收
        with open(self.packet) as f:
            sent = relay.message_for(f.read())
        self.page(sent=True, composer_text=sent, script=[[False, "半截"], [False, "半截，又多了\nVERDICT: PASS"]])
        code, err = self.collect()
        self.assertEqual(code, 2)
        self.assertIn("还在变化", err)
        self.assertFalse(os.path.exists(self.out))

    def test_orca_nonzero_exit_with_ok_json_is_an_error(self):
        # P2-02：退出码非零时即使输出 ok:true 也不信
        self.page(rc_nonzero="eval", reply="x")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("退出码 1", err)
        self.assertNotIn("sent", self.calls())

    def test_orca_non_object_json_is_an_error(self):
        self.page(raw_stdout="eval", reply="x")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("orca eval 失败", err)

    # ---- PR #36 第 2 轮评审返修 ----

    def write_packet(self, text):
        with open(self.packet, "w") as f:
            f.write(text)

    def test_packets_sharing_a_long_prefix_are_told_apart(self):
        # P1-01-R2：A、B 共用 500 字以上模板，页面只显示开头；B 的 review / collect 不能认 A 的气泡
        common = "共同的项目说明与审核协议。" * 60
        a, b = common + "提交 A 的代码", common + "提交 B 的代码"
        shown_a = relay.message_for(a)[:600] + "\n…\n显示更多"  # 页面只显示到公共部分
        self.assertNotIn("提交 A", shown_a)
        self.assertFalse(relay.bubble_matches(shown_a, relay.message_for(b)))
        self.write_packet(b)
        self.page(reply="VERDICT: PASS", foreign_user=shown_a)
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn(relay.review_id(b), err)
        self.assertFalse(os.path.exists(self.out))
        self.page(sent=True, polls=5, composer_text=shown_a, reply="VERDICT: PASS")
        self.assertEqual(self.collect()[0], 2)

    def test_conversation_switch_before_first_reply_is_refused(self):
        # P1-02-R2：回复还没出现就换到了别的会话
        self.page(script=[[False, "", "https://chatgpt.com/c/other", 0], [False, "VERDICT: PASS", "https://chatgpt.com/c/other"]])
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("会话变了", err)
        self.assertFalse(os.path.exists(self.out))

    def test_new_chat_binds_to_first_conversation_after_send(self):
        # 从项目首页开新会话：发送前没有会话 ID，发送后第一次进入的会话就是绑定对象
        self.page(url_before="https://chatgpt.com/g/g-p-x/project",
                  script=[[True, "审核中", "https://chatgpt.com/c/new"], [False, "VERDICT: PASS", "https://chatgpt.com/c/new"]])
        self.assertEqual(self.review()[0], 0)
        with open(self.out) as f:
            self.assertIn('"conversation": "new"', f.readline())

    def test_changing_out_does_not_bypass_unknown_send(self):
        # P1-03-R2：同一送审包换个 --out 也不能重发
        self.page(fail_after_send=True, reply="VERDICT: PASS")
        self.assertEqual(self.review()[0], 2)
        self.out = os.path.join(self.dir, "回执-重试.md")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("结果不明", err)
        self.assertEqual(sum("/*relay:send*/" in " ".join(c) for c in self.calls()["calls"]), 1)

    def test_collect_checks_marker_conversation(self):
        # P2-01-R2：发送标记记下的会话和页面不一致就不收
        with open(self.packet) as f:
            packet = f.read()
        relay.Marker(packet).create("other", "https://chatgpt.com/c/other")
        self.page(sent=True, polls=5, composer_text=relay.message_for(packet), reply="VERDICT: PASS")
        code, err = self.collect()
        self.assertEqual(code, 2)
        self.assertIn("发送标记", err)
        self.assertTrue(os.path.exists(self.marker()))

    def test_failed_fsync_or_publish_leaves_no_record(self):
        # P2-02-R2：fsync、发布失败时不留正式回执，也不留临时文件
        real_fsync, real_link = relay.os.fsync, relay.os.link
        for name in ("fsync", "link"):
            def boom(*a):
                raise OSError("模拟 %s 失败" % name)
            setattr(relay.os, name, boom)
            try:
                with self.assertRaises(OSError):
                    relay.write_record(self.out, "包", "VERDICT: PASS", "https://chatgpt.com/c/fake", "PASS")
            finally:
                relay.os.fsync, relay.os.link = real_fsync, real_link
            self.assertEqual(sorted(os.listdir(self.dir)), ["packet.md"])

    def test_deliver_rejects_tampered_or_foreign_record(self):
        # P2-03-R2：回执正文被改、或不是本通道生成的，都不交付
        self.page(reply="缺测试。\nVERDICT: REWORK")
        self.review()
        with open(self.out) as f:
            text = f.read()
        with open(self.out, "w") as f:
            f.write(text.replace("缺测试。", "没问题。"))
        code, msg = self.deliver()
        self.assertEqual(code, 2)
        self.assertIn("被改过", msg)
        with open(self.out, "w") as f:
            f.write("# 手写的回执\n- 解析结论：**REWORK**\n")
        self.assertIn("不是本通道生成的回执", self.deliver()[1])
        self.assertEqual(self.terminal_sends(), 0)

    def test_orca_failure_is_reported(self):
        self.page(fail="eval", reply="x")
        code, err = self.review()
        self.assertEqual(code, 2)
        self.assertIn("orca eval 失败", err)


if __name__ == "__main__":
    unittest.main()
