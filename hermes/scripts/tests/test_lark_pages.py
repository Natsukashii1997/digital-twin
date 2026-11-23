"""lark_pages.read_all：读列表翻到底，翻不完就报错（#312）。全部用假的 fetch，不联网。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lark_pages as lp  # noqa: E402


class Boom(Exception):
    pass


def paged(rows, per, key="messages", lose_token=False, keep_token_at_end=False):
    """像真的 lark-cli 那样一轮只给 per 条：还有下一页时 has_more=true 并给 page_token，接着读要带 --page-token。"""
    calls = []

    def fetch(extra):
        calls.append(list(extra))
        start = int(extra[1]) if extra else 0
        data = {key: rows[start:start + per], "has_more": start + per < len(rows)}
        if (data["has_more"] and not lose_token) or keep_token_at_end:
            data["page_token"] = str(start + per)
        return data
    return fetch, calls


class ReadAll(unittest.TestCase):
    def test_follows_page_token_until_has_more_is_false(self):
        rows = [{"message_id": f"om_{i}"} for i in range(7)]
        fetch, calls = paged(rows, 3)
        self.assertEqual(lp.read_all(fetch, "读群消息")["messages"], rows)
        self.assertEqual(calls, [[], ["--page-token", "3"], ["--page-token", "6"]])

    def test_single_round_makes_one_call(self):
        fetch, calls = paged([1, 2], 50)
        self.assertEqual(lp.read_all(fetch, "读")["messages"], [1, 2])
        self.assertEqual(calls, [[]])

    def test_only_has_more_decides_not_page_token(self):
        """task +get-related-tasks 读完了也带着 page_token（2026-11-22 实测）：只看 has_more，不因为有标记就接着翻。"""
        fetch, calls = paged([1, 2], 50, key="items", keep_token_at_end=True)
        self.assertEqual(lp.read_all(fetch, "读任务", keys=("items",))["items"], [1, 2])
        self.assertEqual(len(calls), 1)

    def test_more_pages_without_token_is_an_error(self):
        fetch, _ = paged(list(range(7)), 3, lose_token=True)
        with self.assertRaisesRegex(Boom, "读群消息：还有下一页，但飞书没给翻页标记，没读全"):
            lp.read_all(fetch, "读群消息", Boom)

    def test_repeated_token_is_an_error(self):
        with self.assertRaisesRegex(Boom, "翻页标记和前面重复"):
            lp.read_all(lambda extra: {"messages": [1], "has_more": True, "page_token": "same"}, "读", Boom)

    def test_gives_up_after_rounds_instead_of_truncating(self):
        fetch, calls = paged(list(range(100)), 1)
        with self.assertRaisesRegex(Boom, "翻了 5 轮还没到底，没读全"):
            lp.read_all(fetch, "读", Boom, rounds=5)
        self.assertEqual(len(calls), 5)

    def test_exhausted_can_raise_its_own_error(self):
        """#338：调用方可以把「翻到上限」单独认出来（连得上、确定读不全），其他读不全照旧抛 error。"""
        class Exhausted(Exception):
            pass
        fetch, calls = paged(list(range(100)), 1)
        with self.assertRaisesRegex(Exhausted, "翻了 5 轮还没到底，没读全"):
            lp.read_all(fetch, "读", Boom, rounds=5, exhausted=Exhausted)
        self.assertEqual(len(calls), 5)
        for bad in ({"messages": [1]}, {"messages": [1], "has_more": True}, None):
            with self.subTest(bad=bad), self.assertRaises(Boom):
                lp.read_all(lambda extra: bad, "读", Boom, rounds=5, exhausted=Exhausted)
        with self.assertRaisesRegex(Boom, "翻页标记和前面重复"):
            lp.read_all(lambda extra: {"messages": [1], "has_more": True, "page_token": "same"}, "读", Boom, exhausted=Exhausted)

    def test_empty_result_with_has_more_false_is_complete(self):
        """空结果：真的 lark-cli 也带 has_more=false 和空列表（2026-11-22 实测六个命令），算读完。"""
        self.assertEqual(lp.read_all(lambda extra: {"messages": [], "has_more": False, "page_token": ""}, "读"), {"messages": []})

    def test_missing_or_invalid_has_more_is_an_error(self):
        """PR #335 审查 P1-01：has_more 没有、是 null、是 0 这类「假值」都不能证明读完，原来被当成读完直接返回半截列表。"""
        for bad in ({}, {"has_more": None}, {"has_more": 0}, {"has_more": 1}, {"has_more": "false"}, {"has_more": "true"}, {"has_more": []}):
            with self.subTest(bad=bad), self.assertRaisesRegex(Boom, "读群消息：返回里没有有效的 has_more，没法确认读没读全"):
                lp.read_all(lambda extra: {"messages": [{"message_id": "om_1"}], **bad}, "读群消息", Boom)

    def test_invalid_has_more_on_a_later_round_is_an_error(self):
        pages = {(): {"messages": [1], "has_more": True, "page_token": "t"}, ("--page-token", "t"): {"messages": [2]}}
        with self.assertRaisesRegex(Boom, "没有有效的 has_more"):
            lp.read_all(lambda extra: pages[tuple(extra)], "读", Boom)

    def test_required_list_must_be_a_list_every_round(self):
        for bad in ({}, {"messages": None}, {"messages": {}}, {"messages": "x"}, {"items": [1]}):
            with self.subTest(bad=bad), self.assertRaisesRegex(Boom, "读群消息：返回里没有 messages 列表，没法确认读没读全"):
                lp.read_all(lambda extra: {"has_more": False, **bad}, "读群消息", Boom)
        pages = {(): {"messages": [1], "has_more": True, "page_token": "t"}, ("--page-token", "t"): {"has_more": False}}
        with self.assertRaisesRegex(Boom, "没有 messages 列表"):
            lp.read_all(lambda extra: pages[tuple(extra)], "读", Boom)

    def test_optional_list_may_be_absent_but_must_be_a_list(self):
        pages = {(): {"users": ["a"], "truncations": [], "has_more": True, "page_token": "t"},
                 ("--page-token", "t"): {"users": ["b"], "truncations": ["users"], "has_more": False}}
        got = lp.read_all(lambda extra: pages[tuple(extra)], "读群成员", keys=("users",), optional=("truncations", "bots"))
        self.assertEqual(got, {"users": ["a", "b"], "truncations": ["users"], "bots": []})
        self.assertEqual(lp.read_all(lambda extra: {"users": [], "truncations": None, "has_more": False}, "读", keys=("users",), optional=("truncations",)),
                         {"users": [], "truncations": []})
        with self.assertRaisesRegex(Boom, "读群成员：返回里的 truncations 不是列表"):
            lp.read_all(lambda extra: {"users": [], "truncations": "users", "has_more": False}, "读群成员", Boom, ("users",), ("truncations",))

    def test_page_token_must_be_a_non_empty_string(self):
        for bad in ({}, {"page_token": ""}, {"page_token": None}, {"page_token": 123}, {"page_token": ["t"]}):
            with self.subTest(bad=bad), self.assertRaisesRegex(Boom, "还有下一页，但飞书没给翻页标记"):
                lp.read_all(lambda extra: {"messages": [1], "has_more": True, **bad}, "读", Boom)

    def test_none_and_other_shapes_are_errors(self):
        for bad in (None, [1, 2], "x", 0):
            with self.subTest(bad=bad), self.assertRaisesRegex(Boom, "不是预想的样子"):
                lp.read_all(lambda extra: bad, "读", Boom)

    def test_default_error_type(self):
        with self.assertRaises(lp.Incomplete):
            lp.read_all(lambda extra: {"messages": [], "has_more": True}, "读")

    def test_fetch_errors_pass_through(self):
        def fetch(extra):
            raise Boom("断网")
        with self.assertRaisesRegex(Boom, "断网"):
            lp.read_all(fetch, "读")


if __name__ == "__main__":
    unittest.main()
