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

    def test_several_keys_and_missing_keys(self):
        pages = {(): {"users": ["a"], "truncations": [], "has_more": True, "page_token": "t"},
                 ("--page-token", "t"): {"users": ["b"], "truncations": ["users"]}}
        got = lp.read_all(lambda extra: pages[tuple(extra)], "读群成员", keys=("users", "truncations", "bots"))
        self.assertEqual(got, {"users": ["a", "b"], "truncations": ["users"], "bots": []})

    def test_none_is_empty_and_other_shapes_are_errors(self):
        self.assertEqual(lp.read_all(lambda extra: None, "读"), {"messages": []})
        with self.assertRaisesRegex(Boom, "不是预想的样子"):
            lp.read_all(lambda extra: [1, 2], "读", Boom)

    def test_default_error_type(self):
        with self.assertRaises(lp.Incomplete):
            lp.read_all(lambda extra: {"has_more": True}, "读")

    def test_fetch_errors_pass_through(self):
        def fetch(extra):
            raise Boom("断网")
        with self.assertRaisesRegex(Boom, "断网"):
            lp.read_all(fetch, "读")


if __name__ == "__main__":
    unittest.main()
