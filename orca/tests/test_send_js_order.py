"""SEND_JS 顺序锁（#41，P2-02-R5）：所有拒绝分支都必须在点击之前。

否则可能先点了发送再返回「拒绝」，调用方误以为没发出而清掉发送标记、导致重发。

运行：python3 -B -m unittest discover -s orca/tests
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import chatgpt_relay as relay  # noqa: E402

CLICK_RE = re.compile(r"\.click\(\)")
RETURN_RE = re.compile(r"\breturn\b")
RETURN_STR_RE = re.compile(r"""\breturn\s+(['"])(\w+)\1""")


class SendJsOrderTest(unittest.TestCase):
    def setUp(self):
        self.js = relay.SEND_JS
        clicks = [m.start() for m in CLICK_RE.finditer(self.js)]
        self.assertEqual(len(clicks), 1, "SEND_JS 里必须恰好有一处 .click()，实际 %d 处" % len(clicks))
        self.click = clicks[0]
        self.returns = [(m.group(2), m.start()) for m in RETURN_STR_RE.finditer(self.js)]

    def test_every_return_is_a_string_literal(self):
        # 防止新增的 return 写成别的形式而漏过下面的顺序检查
        self.assertEqual(len(RETURN_RE.findall(self.js)), len(self.returns),
                         "SEND_JS 里每个 return 都必须直接返回带引号的字符串")

    def test_sent_returned_once_after_click(self):
        sent = [pos for value, pos in self.returns if value == "sent"]
        self.assertEqual(len(sent), 1, "SEND_JS 里必须恰好有一处 return 'sent'")
        self.assertGreater(sent[0], self.click, "return 'sent' 必须在点击之后")

    def test_all_refusals_before_click(self):
        refusals = [(value, pos) for value, pos in self.returns if value != "sent"]
        self.assertTrue(refusals, "SEND_JS 里没找到拒绝分支，正则可能失效")
        late = [value for value, pos in refusals if pos > self.click]
        self.assertEqual(late, [], "这些拒绝分支出现在点击之后：%s" % late)

    def test_all_refusals_documented(self):
        refusals = {value for value, _ in self.returns if value != "sent"}
        missing = sorted(refusals - set(relay.SEND_REFUSED))
        self.assertEqual(missing, [], "这些拒绝返回值在 SEND_REFUSED 里没有说明：%s" % missing)
        self.assertNotIn("sent", relay.SEND_REFUSED, "'sent' 不能出现在 SEND_REFUSED 里")


if __name__ == "__main__":
    unittest.main()
