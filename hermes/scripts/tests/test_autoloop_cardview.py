"""中台卡片渲染模块（#195 批 1「飞书拍板」）自测：定稿的丙版「推荐优先」× 开着 / 已定 / 已办完 / 已失效，和定稿样例逐字比对；
批 1 验收里和渲染相关的三条，本模块负责的那一段：重复点只算一次（渲染出的按钮值原样交给 autoloop_answer，去重是它的），
引用回答可用（只测解析，调用方还没接），原有合并 / 推翻按钮照常（插件原有测试原样再跑一遍）。
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
        self.assertEqual(head(DONE), "**🏁 你选了「保留 pip」，已办完。**")
        self.assertEqual(head({**DONE, "choice": 0}), "**🏁 已按推荐：统一写 uv**")
        self.assertEqual(head(CLOSED), "**⏹ 这件事不用你定了**")
        self.assertEqual(head({**NOTICE, "state": "closed", "result": "后来跑成了"}), "**⏹ 后来跑成了**")  # 通知卡不说「不用你定」
        self.assertEqual(head({**NOTICE, "state": "closed", "text": ""}), "**⏹ 已结束。**")
        self.assertEqual(head({**DECIDED, "choice": None, "answer": None}), "**✅ 你定了，已记下，等 Agent 接着做。**")

    def test_agent_summary_stays_out_of_the_headline(self):
        """已办完时 result 是 Agent 这一轮的整段总结：放在大字下面的普通字号里，大字只写一行。"""
        c = V.render({**DONE, "result": "第一行\n第二行"}, bridge())
        els = c["body"]["elements"]
        self.assertNotIn("第一行", els[0]["content"])
        self.assertIn({"tag": "div", "text": {"tag": "plain_text", "content": "第一行\n第二行"}}, els)   # 纯文本，换行保留
        c = V.render({**DECIDED, "choice": None, "answer": "多行\n回答"}, bridge())
        self.assertEqual(c["body"]["elements"][0]["content"], "**✅ 你选了「多行 回答」，已记下，等 Agent 接着做。**")
        c = V.render({**ASK, "options": ["统一\n写 uv", "保留 pip"]}, bridge())
        self.assertEqual(c["body"]["elements"][0]["content"], "**我建议：统一 写 uv**")

    def test_closed_notice_shows_the_handling_note(self):
        """普通通知卡：常浩点「已处理」时写的处理说明，中台 closeCard 存在 answer 里，要显示出来（L3 #256-P1-01）。"""
        handled = {**NOTICE, "state": "closed", "options": [], "result": "已处理", "answer": "已联系对方，约定下周交付"}
        els = V.render(handled, bridge())["body"]["elements"]
        self.assertEqual(els[0]["content"], "**⏹ 已处理**")
        self.assertIn({"tag": "div", "text": {"tag": "plain_text", "content": "处理说明：已联系对方，约定下周交付"}}, els)
        for empty in (None, ""):                                       # 没写处理说明：不出空行
            self.assertNotIn("处理说明", text(V.render({**handled, "answer": empty}, bridge())))

    def test_plain_fields_cannot_turn_into_markdown(self):
        """选项、回答、结果、规则名是纯文本：进大字和页脚的换成全角，单独成行的用纯文本组件；card.text 仍是 markdown（L3 #256-P2-01）。"""
        evil = "[重新验证权限](https://example.invalid) <at id=all></at> **粗** <font color='red'>x</font>"
        c = V.render({**ASK, "options": [evil, "保留 pip"], "rule": "r_[x](http://e)"}, bridge(), mark="尾号 <b>")
        md_text = " ".join(e["content"] for e in c["body"]["elements"] if e["tag"] == "markdown" and e is not c["body"]["elements"][1])
        for bad in ("[重新验证权限](", "<at", "**粗**", "<font color='red'>", "<b>", "[x]("):
            self.assertNotIn(bad, md_text, bad)
        self.assertIn("［重新验证权限］", c["body"]["elements"][0]["content"])
        big = next(e for e in c["body"]["elements"] if e["tag"] == "button")
        self.assertIn(evil, big["text"]["content"])                       # 按钮本来就是纯文本，原样显示
        self.assertEqual(c["body"]["elements"][1], {"tag": "markdown", "content": ASK["text"]})   # 正文照旧是 markdown
        for card in ({**CLOSED, "result": evil}, {**DONE, "result": evil}, {**DECIDED, "choice": None, "answer": evil},
                     {**NOTICE, "state": "closed", "options": [], "result": evil, "answer": evil}):
            els = V.render(card, bridge())["body"]["elements"]
            for e in els:
                if e["tag"] == "markdown":
                    self.assertNotIn("[重新验证权限](", e["content"], card["state"])
                    self.assertNotIn("<at", e["content"], card["state"])
            plains = [e["text"]["content"] for e in els if e["tag"] == "div"]
            if card["state"] in ("closed", "done") and card.get("kind") == "ask":
                self.assertTrue(any(evil in p for p in plains), card["state"])   # 原因 / 总结原样进纯文本组件
        self.assertEqual(V.esc("a\\b*_~`[]<>&\n下一行"), "a＼b＊＿～｀［］＜＞＆ 下一行")

    def test_closed_notice_shows_its_text_unfolded(self):
        """中台的只读通知（fyi）一建出来就是 closed、没有 result：正文直接摊开，不收进折叠面板（审查 P2-1）。"""
        fyi = {**NOTICE, "state": "closed", "options": [], "result": None, "text": "第 3 轮跑完了：改好了 README"}
        c = V.render(fyi, bridge())
        self.assertEqual(c["body"]["elements"][0], {"tag": "markdown", "content": "第 3 轮跑完了：改好了 README"})
        self.assertNotIn("collapsible_panel", text(c))
        self.assertNotIn("当时问的是什么", text(c))
        handled = V.render({**fyi, "result": "已处理"}, bridge())
        self.assertEqual(handled["body"]["elements"][0]["content"], "**⏹ 已处理**")
        self.assertIn("第 3 轮跑完了", handled["body"]["elements"][1]["content"])

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
        self.assertIn(V.DEFAULT_HINT, text(V.render(ASK, bridge())))
        self.assertNotIn("引用", V.DEFAULT_HINT)                         # 引用回答在 ADR-015 下只算提议：默认不教
        self.assertIn("自定提示", text(V.render(ASK, bridge(), hint="自定提示")))
        self.assertNotIn("引用这张卡片", text(V.render(DECIDED, bridge())))  # 结束的卡不再教怎么回

    def test_custom_hint_is_plain_text(self):
        """调用方自定的「怎么回」提示是纯文本：不能闭合灰字标签、造链接或加粗（L3 第 2 轮 #256-P2-01）。"""
        evil = "</font>\n[查看权限](https://example.invalid) <at id=all></at> **粗**"
        foot = V.render(ASK, bridge(), hint=evil)["body"]["elements"][-1]["content"]
        self.assertTrue(foot.startswith("<font color='grey'>") and foot.endswith("</font>"))
        self.assertEqual(foot.count("</font>"), 1)                      # 只有外层自己的那个结束标签
        for bad in ("[查看权限](", "<at", "**粗**", "\n"):
            self.assertNotIn(bad, foot, bad)
        self.assertIn("＜／font＞".replace("／", "/"), foot)
        self.assertIn("［查看权限］", foot)
        self.assertIn(V.DEFAULT_HINT, text(V.render(ASK, bridge())))     # 默认提示和普通自定提示照常
        self.assertIn("自定提示", text(V.render(ASK, bridge(), hint="自定提示")))


class QuotedReply(unittest.TestCase):
    """引用卡片打字回答：只认一种明确的意思。"""
    def test_recognised(self):
        for said, want in (("1", 0), ("２", None), ("2", 1), ("③", 2), ("选3", 2), ("第二个", 1), ("就选 1", 0),
                           ("按推荐", 0), ("就按推荐的", 0), ("推荐", 0), ("保留 pip", 1), ("「两种都写」", 2),
                           ("4", None), ("0", None), ("uv", None), ("1 和 2", None), ("", None), (None, None)):
            self.assertEqual(V.parse_reply(said, ASK), want, said)

    def test_two_readings_that_disagree_are_refused(self):
        """选项本身是数字、或有个选项叫「推荐」时，编号 / 原文 / 按推荐几种认法会指向不同选项：不挑，返回 None（审查 P2-2）。"""
        nums = {**ASK, "options": ["2", "1"], "recommend": None}
        self.assertIsNone(V.parse_reply("1", nums))
        self.assertIsNone(V.parse_reply("2", nums))
        self.assertEqual(V.parse_reply("第一个", nums), 0)
        self.assertEqual(V.parse_reply("4", {**ASK, "options": ["4", "5"]}), 0)         # 编号超范围，只剩原文一种认法
        self.assertEqual(V.parse_reply("2", {**ASK, "options": ["2", "x"]}), None)      # 原文是第 1 项，编号是第 2 项
        self.assertEqual(V.parse_reply("1", {**ASK, "options": ["1", "x"]}), 0)         # 两种认法一致
        named = {**ASK, "options": ["统一写 uv", "保留 pip", "推荐"]}
        self.assertIsNone(V.parse_reply("推荐", named))
        self.assertIsNone(V.parse_reply("同一", {**ASK, "options": ["同一", "同 一"]}))   # 规整后两个选项一样

    def test_option_text_normalised_the_same_way(self):
        self.assertEqual(V.parse_reply("UV", {**ASK, "options": ["uv", "pip"]}), 0)
        self.assertEqual(V.parse_reply("是的", {**ASK, "options": ["是的。", "不是"]}), 0)

    def test_no_recommendation_means_follow_recommendation_is_unclear(self):
        self.assertIsNone(V.parse_reply("按推荐", {**ASK, "recommend": None}))

    def test_ended_or_notice_cards_take_no_reply(self):
        for card in (DECIDED, CLOSED, NOTICE):
            self.assertIsNone(V.parse_reply("1", card))


class Sample(unittest.TestCase):
    """定稿样例逐字比对：改了样子这条就红，要先过 docs/DECIDED.md（全局规则第 15 节）。"""
    def test_render_matches_the_decided_sample(self):
        sample = json.loads((HERE.parents[1] / "samples" / "autoloop-card-c.json").read_text(encoding="utf-8"))
        self.assertEqual(set(sample["states"]), {"open", "decided", "closed"})
        for name, s in sample["states"].items():
            card = s["input"]
            got = V.render(card, lambda i: {"autoloop": "0123456789abcdef", "o": i, "v": card["version"]}, mark="尾号 cdef")
            self.assertEqual(got, s["card"], name)


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
        # 按钮里的版本号一路带到中台（#278 引擎批 C：中台按版本核对，点旧版的不收）
        self.assertEqual(answers, [("/v1/cards/7/answer", {"choice": 1, "by": TF.OWNER, "ref": "om_fixture_1",
                                                           "version": 1})])

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
