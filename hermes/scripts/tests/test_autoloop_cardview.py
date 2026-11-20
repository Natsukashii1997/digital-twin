"""中台卡片渲染模块（#195 批 1「飞书拍板」）自测：定稿的丙版「推荐优先」× 开着 / 已定 / 已办完 / 已失效；
批 1 验收里和渲染相关的三条：重复点只算一次、引用回答可用、原有合并 / 推翻按钮照常。
不发飞书、不起中台：桥接和插件都用 test_autoloop_feishu / test_merge_buttons_plugin 同样的假件。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_answer as A  # noqa: E402
import autoloop_cardview as V  # noqa: E402
import autoloop_feishu as F  # noqa: E402
import test_autoloop_feishu as TF  # noqa: E402
import test_merge_buttons_plugin as TP  # noqa: E402

NOW = 1_800_000_000
ASK = {"id": 7, "version": 3, "kind": "ask", "state": "open", "title": "安装命令用哪个", "text": "仓库里 uv 和 pip 都有",
       "options": ["统一写 uv", "保留 pip", "两种都写"], "recommend": 0, "choice": None, "answer": None, "by": None,
       "result": None, "rule": "dispatch-dt-sandbox-claude", "createdAt": (NOW - 25 * 60) * 1000,
       "decidedAt": None, "closedAt": None}
DECIDED = {**ASK, "state": "decided", "version": 4, "choice": 1, "by": "ou_owner", "decidedAt": (NOW - 60) * 1000}
DONE = {**DECIDED, "state": "done", "version": 5, "result": "已改好并开了 PR"}
CLOSED = {**ASK, "state": "closed", "version": 4, "result": "Agent 自己查到了仓库约定", "closedAt": (NOW - 60) * 1000}
NOTICE = {**ASK, "kind": "notice", "options": ["已处理"], "recommend": None, "title": "第 3 轮跑不起来"}


def bridge(token="0123456789abcdef", version=3):
    return lambda i: {"autoloop": token, "o": i, "v": version}


def text(content):
    return json.dumps(content, ensure_ascii=False)


class Render(unittest.TestCase):
    def test_open_ask_has_one_button_per_option_with_exactly_the_callers_values(self):
        c = V.render(ASK, bridge())
        self.assertEqual(sorted(V.button_values(c), key=lambda v: v["o"]),
                         [{"autoloop": "0123456789abcdef", "o": i, "v": 3} for i in range(3)])
        self.assertEqual(c["schema"], "2.0")
        self.assertTrue(c["config"]["update_multi"])                       # 结束后原地改这条消息
        self.assertNotIn("form", text(c))                                  # 不放文字输入框：网关转不来表单内容
        self.assertNotIn("input", text(c))
        self.assertEqual(c["header"]["template"], "indigo")

    def test_same_card_same_version_renders_identical_buttons(self):
        """重复点带的是同一个值：去重由收点击的一方按令牌 / 版本做。"""
        self.assertEqual(V.render(ASK, bridge()), V.render(ASK, bridge()))

    def test_recommendation_leads_with_one_big_button_and_others_folded(self):
        c = V.render(ASK, bridge())
        els = c["body"]["elements"]
        self.assertEqual(els[0]["content"], "**我建议：统一写 uv**")
        big = next(e for e in els if e["tag"] == "button")
        self.assertEqual(big["behaviors"][0]["value"]["o"], 0)               # 大按钮就是推荐项
        self.assertEqual((big["type"], big["width"], big["text"]["content"]), ("primary_filled", "fill", "✅ 按推荐：统一写 uv"))
        panel = next(e for e in els if e["tag"] == "collapsible_panel")
        self.assertFalse(panel["expanded"])
        self.assertEqual([v["o"] for v in V.button_values({"body": panel})], [1, 2])
        self.assertIn("改选：保留 pip", text(panel))

    def test_without_recommendation_does_not_pick_for_the_owner(self):
        for bad in (None, 5, -1, True, "0"):
            c = V.render({**ASK, "recommend": bad}, bridge())
            self.assertNotIn("我建议", text(c), bad)
            self.assertEqual(len(V.button_values(c)), 3)
            self.assertNotIn("primary_filled", text(c))
            self.assertNotIn("collapsible_panel", text(c))

    def test_single_option_with_recommendation_has_no_empty_panel(self):
        c = V.render({**ASK, "options": ["好"], "recommend": 0}, bridge())
        self.assertEqual(len(V.button_values(c)), 1)
        self.assertNotIn("collapsible_panel", text(c))

    def test_notice_card_has_single_done_button(self):
        c = V.render(NOTICE, bridge())
        self.assertEqual(V.button_values(c), [{"autoloop": "0123456789abcdef", "o": 0, "v": 3}])
        self.assertIn("已处理", text(c))

    def test_ended_cards_have_no_buttons_and_say_what_happened(self):
        for card, words, color in ((DECIDED, "保留 pip", "green"), (DONE, "已改好并开了 PR", "green"),
                                   (CLOSED, "Agent 自己查到了仓库约定", "grey")):
            calls = []
            c = V.render(card, lambda i: calls.append(i) or {"x": i})
            self.assertEqual(V.button_values(c), [], card["state"])
            self.assertEqual(calls, [])                                    # 结束的卡连按钮值都不要
            self.assertIn(words, text(c), card["state"])
            self.assertEqual(c["header"]["template"], color)
            self.assertIn("当时问的是什么", text(c))                          # 原问题收进折叠面板

    def test_headlines(self):
        head = lambda card: V.render(card, bridge())["body"]["elements"][0]["content"]  # noqa: E731
        self.assertEqual(head({**DECIDED, "choice": 0}), "**✅ 已按推荐：统一写 uv**")
        self.assertEqual(head(DECIDED), "**✅ 你选了「保留 pip」，已记下，等 Agent 接着做。**")
        self.assertEqual(head(DONE), "**🏁 你选了「保留 pip」，已办完：已改好并开了 PR**")
        self.assertEqual(head(CLOSED), "**⏹ 这件事不用你定了**")
        self.assertEqual(head({**NOTICE, "state": "closed", "result": "后来跑成了"}), "**⏹ 后来跑成了**")  # 通知卡不说「不用你定」
        self.assertEqual(head({**NOTICE, "state": "closed"}), "**⏹ 已结束。**")

    def test_secretary_decision_is_labelled(self):
        self.assertIn("秘书替你", text(V.render({**DECIDED, "by": "秘书"}, bridge())))
        self.assertIn("秘书替你", text(V.render({**DECIDED, "choice": 0, "by": "秘书"}, bridge())))
        self.assertEqual(V.outcome({**DECIDED, "by": "秘书"}), "秘书替你选了「保留 pip」，已记下，等 Agent 接着做。")
        self.assertIsNone(V.outcome(ASK))
        self.assertEqual(V.chosen({**DECIDED, "choice": 9, "answer": "原文"}), "原文")

    def test_footer_carries_mark_and_hint_for_readback(self):
        c = V.render(ASK, bridge(), mark="尾号 cdef")
        self.assertIn("卡片 #7 · 第 3 版", text(c))
        self.assertIn("尾号 cdef", text(c))
        self.assertIn("按推荐", text(V.render(ASK, bridge())))
        self.assertIn("自定提示", text(V.render(ASK, bridge(), hint="自定提示")))
        self.assertNotIn("引用这张卡片", text(V.render(DECIDED, bridge())))  # 结束的卡不再教怎么回


class QuotedReply(unittest.TestCase):
    """引用卡片打字回答：只认一种明确的意思。"""
    def test_recognised(self):
        for said, want in (("1", 0), ("２", None), ("2", 1), ("③", 2), ("选3", 2), ("第二个", 1), ("就选 1", 0),
                           ("按推荐", 0), ("就按推荐的", 0), ("推荐", 0), ("保留 pip", 1), ("「两种都写」", 2),
                           ("4", None), ("0", None), ("uv", None), ("1 和 2", None), ("", None), (None, None)):
            self.assertEqual(V.parse_reply(said, ASK), want, said)

    def test_no_recommendation_means_follow_recommendation_is_unclear(self):
        self.assertIsNone(V.parse_reply("按推荐", {**ASK, "recommend": None}))

    def test_ended_or_notice_cards_take_no_reply(self):
        for card in (DECIDED, CLOSED, NOTICE):
            self.assertIsNone(V.parse_reply("1", card))


class DuplicateClick(TF.Base):
    """验收「重复点只算一次」：用新模块渲染桥接卡片，取按钮值经 autoloop_answer 点两次，中台只收到一次回答。"""
    def setUp(self):
        super().setUp()
        self.register()
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = ? WHERE card_id = 7", (F.now(),))
        with F.db() as con:
            self.tok = con.execute("SELECT token FROM feishu_cards WHERE card_id = 7").fetchone()["token"]
        self.calls = []
        for target, name, fake in ((F, "hub", self.fake_hub), (F, "hub_target", lambda: ("http://x", "k")),
                                   (A, "read_card", lambda cid: {"id": 7, "state": "decided", "choice": 1, "by": TF.OWNER})):
            p = mock.patch.object(target, name, fake)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(A, "tell")
        p.start()
        self.addCleanup(p.stop)

    def fake_hub(self, method, path, body=None, timeout=20):
        self.calls.append((path, body))
        return {"changed": True} if path.endswith("/answer") else {}

    def twice(self):
        card = {**TF.CARD, "version": 1}
        content = V.render(card, lambda i: {"autoloop": self.tok, "o": i, "v": card["version"]})
        value = next(v for v in V.button_values(content) if v["o"] == 1)
        first = A.answer(value["autoloop"], value["o"], TF.CHAT, TF.OWNER, value["v"])
        again = A.answer(value["autoloop"], value["o"], TF.CHAT, TF.OWNER, value["v"])
        self.assertEqual(first[0], 0, first[1])
        self.assertEqual(again[0], 1)
        self.assertIn("已经点过", again[1])
        answers = [c for c in self.calls if c[0].endswith("/answer")]
        self.assertEqual(answers, [("/v1/cards/7/answer", {"choice": 1, "by": TF.OWNER, "ref": "om_fixture_1"})])

    def test_clicking_the_rendered_button_twice_answers_once(self):
        self.twice()


class ExistingButtonsUnchanged(TP.MergeButtonsPlugin):
    """验收「原有合并 / 推翻按钮照常」：继承插件原有的全部测试原样再跑一遍，另加：新模块渲染出的按钮值
    只走中台卡片那一支，不会被认成合并 / 推翻 / 日报按钮。"""
    def test_rendered_autoloop_values_route_only_to_autoloop_answer(self):
        for v in V.button_values(V.render(ASK, bridge())):
            out, popen = self.click("button " + json.dumps(v))
            self.assertIsNone(out)
            cmd = popen.call_args[0][0]
            self.assertEqual(cmd[:4], ["/x/python3", "/x/autoloop_answer.py", "0123456789abcdef", str(v["o"])])
            self.assertNotIn("/x/merge_act.py", cmd)
            self.assertNotIn("/x/custody_report.py", cmd)

    def test_rendered_values_never_carry_other_buttons_keys(self):
        for v in V.button_values(V.render(ASK, bridge())):
            self.assertFalse({"merge_officer", "custody", "pdaily", "probe", "ref", "card"} & set(v))


if __name__ == "__main__":
    unittest.main()
