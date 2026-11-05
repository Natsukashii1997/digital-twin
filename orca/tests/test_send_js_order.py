"""SEND_JS 顺序锁（#41，RELAY-01 技术债 P2-02-R5）。

SEND_JS 返回 'sent' 才算点了发送，其余返回值都表示“没点”。如果某个拒绝分支
写到了点击之后，就可能先点了发送再返回拒绝，调用方误以为没发出、清掉发送标记后重发。
这里只读 chatgpt_relay.SEND_JS 的源码做静态检查，不连 ORCA / ChatGPT。

运行：python3 -B -m unittest discover -s orca/tests
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import chatgpt_relay as relay  # noqa: E402

CLICK_RE = re.compile(r"\.click\s*\(\s*\)")
ANY_RETURN_RE = re.compile(r"\breturn\b")
STR_RETURN_RE = re.compile(r"""\breturn\s+(['"])([^'"]*)\1""")


def _clicks():
    return [m.start() for m in CLICK_RE.finditer(relay.SEND_JS)]


def _returns():
    """SEND_JS 里所有 return '…'：[(返回值, 位置)]。"""
    return [(m.group(2), m.start()) for m in STR_RETURN_RE.finditer(relay.SEND_JS)]


class SendJsOrderTest(unittest.TestCase):
    def test_exactly_one_click(self):
        clicks = _clicks()
        self.assertEqual(len(clicks), 1, "SEND_JS 里必须恰好一处 .click()，实际 %d 处" % len(clicks))

    def test_every_return_is_a_string_literal(self):
        # 防止以后写成 return x / return cond ? 'a' : 'b' 之类，绕过下面按位置的检查
        total = len(ANY_RETURN_RE.findall(relay.SEND_JS))
        self.assertEqual(total, len(_returns()), "SEND_JS 里每个 return 都必须直接返回带引号的字符串")

    def test_refusals_before_click_and_sent_after(self):
        clicks = _clicks()
        self.assertEqual(len(clicks), 1, "SEND_JS 里必须恰好一处 .click()，实际 %d 处" % len(clicks))
        click = clicks[0]
        returns = _returns()

        sent = [pos for value, pos in returns if value == "sent"]
        self.assertEqual(len(sent), 1, "SEND_JS 里必须恰好一处 return 'sent'，实际 %d 处" % len(sent))
        self.assertGreater(sent[0], click, "return 'sent' 必须在点击之后")

        refusals = [(value, pos) for value, pos in returns if value != "sent"]
        self.assertTrue(refusals, "SEND_JS 里没找到任何拒绝分支，检查正则是否还匹配")
        late = [value for value, pos in refusals if pos > click]
        self.assertEqual(late, [], "这些拒绝分支在点击之后，点了发送还会被当成没发：%s" % late)

    def test_every_refusal_documented(self):
        refusals = {value for value, _ in _returns() if value != "sent"}
        missing = sorted(refusals - set(relay.SEND_REFUSED))
        self.assertEqual(missing, [], "这些拒绝返回值在 SEND_REFUSED 里没有说明：%s" % missing)
        self.assertNotIn("sent", relay.SEND_REFUSED, "'sent' 不能出现在 SEND_REFUSED 里")


if __name__ == "__main__":
    unittest.main()
