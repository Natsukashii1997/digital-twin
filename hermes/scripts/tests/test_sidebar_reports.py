"""销售 AI 侧边栏固定汇报（亚古兽）：sidebar_reports。全部用假的 run，不联网、不碰真实数据目录。

回归基准是常浩确认过的定稿（fixtures/sidebar/）：
- progress-2026-11-18.card.json：11-18 定稿卡片（DigitalTwin/evidence/progress-card-20261118/card_final.json）
- feedback-2026-11-17/18.post.json：两期需求反馈当时实际发出的 post
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sidebar_reports as sr  # noqa: E402

FIX = Path(__file__).parent / "fixtures" / "sidebar"


def fx(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


class FakeRun:
    """按命令回话，记下每次调用。"""
    def __init__(self, members=None, content="📅<u>**{date} 需求反馈**</u>", mentions=None):
        self.calls, self.n = [], 0
        self.members, self.content, self.mentions = members or [], content, mentions

    def __call__(self, *args, identity="user", tries=None, **kw):
        self.calls.append((args, identity))
        verb = args[1]
        if verb == "+messages-send":
            self.n += 1
            return {"message_id": f"om_{self.n}"}
        if verb == "+messages-mget":
            return {"messages": [{"content": self.content, "mentions": [{"id": i} for i in (self.mentions or [])]}]}
        if verb == "+chat-members-list":
            return {"users": [{"member_id": i} for i in self.members]}
        return {}

    def verbs(self):
        return [a[1] for a, _ in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._home, sr.HOME = sr.HOME, self.tmp / "sidebar"

    def tearDown(self):
        sr.HOME = self._home

    def draft_file(self, data, name="draft.json"):
        p = self.tmp / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return p


class TestGolden(unittest.TestCase):
    """版式必须和常浩确认过的定稿一模一样（11-19 常浩：确保生成的卡片跟确认版本一致）。"""

    def test_progress_card_matches_1118_final(self):
        got = sr.pg_render(fx("progress-2026-11-18.data.json"))
        want = fx("progress-2026-11-18.card.json")
        self.assertEqual(got, want)
        self.assertEqual(json.dumps(got, ensure_ascii=False), json.dumps(want, ensure_ascii=False))   # 连键的顺序都一样

    def test_feedback_posts_match_sent(self):
        for day in ("2026-11-17", "2026-11-18"):
            with self.subTest(day=day):
                d = fx(f"feedback-{day}.draft.json")
                self.assertEqual(sr.fb_check(d), [])
                self.assertEqual(sr.fb_render(d), fx(f"feedback-{day}.post.json"))

    def test_1118_passes_carry_check_against_1117(self):
        self.assertEqual(sr.fb_check(fx("feedback-2026-11-18.draft.json"), fx("feedback-2026-11-17.draft.json")), [])


class TestFeedbackCheck(unittest.TestCase):
    def setUp(self):
        self.prev = fx("feedback-2026-11-17.draft.json")
        self.cur = fx("feedback-2026-11-18.draft.json")

    def errs(self, d):
        return "\n".join(sr.fb_check(d, self.prev))

    def test_dropping_old_pending_is_rejected(self):
        d = copy.deepcopy(self.cur)
        d["items"] = [i for i in d["items"] if not i.get("title", "").startswith("中台手动创建")]
        self.assertIn("上期待解决「中台手动创建的线索", self.errs(d))

    def test_old_pending_moved_to_fixed_keeps_quotes(self):
        d = copy.deepcopy(self.cur)
        fixed = next(i for i in d["items"] if i["type"] == "fixed" and "0.5H" in i["quotes"][0]["text"])
        fixed["quotes"] = fixed["quotes"][:1]   # 删了一条原话，就不算原话照旧
        self.assertIn("上期待解决「试听时长", self.errs(d))

    def test_dropping_old_planned_asks_owner(self):
        d = copy.deepcopy(self.cur)
        d["items"] = [i for i in d["items"] if not i.get("title", "").startswith("侧边栏支持编辑学员信息")]
        self.assertIn("先问他，不要自己定", self.errs(d))

    def test_carried_quotes_must_not_change(self):
        d = copy.deepcopy(self.cur)
        it = next(i for i in d["items"] if i.get("title", "").startswith("侧边栏支持编辑学员信息"))
        it["quotes"][0]["text"] = "改过的原话"
        self.assertIn("原话要照旧", self.errs(d))

    def test_shape_errors(self):
        d = {"date": "11-19", "items": [
            {"type": "planned", "quotes": [{"text": "x", "name": "a", "open_id": "ou_1"}]},
            {"type": "fixed", "title": "多写了", "quotes": [{"text": "y", "name": "b", "open_id": "bad"}]}]}
        e = "\n".join(sr.fb_check(d))
        for s in ("YYYY-MM-DD", "cutoff", "类型顺序", "要写 title", "不要写 title", "open_id 不对"):
            self.assertIn(s, e)

    def test_empty_items_tells_what_to_say(self):
        self.assertIn("本期不出", "\n".join(sr.fb_check({"date": "2026-11-19", "cutoff": "x", "items": []})))

    def test_newline_in_quote_folded_into_space(self):
        d = {"date": "2026-11-19", "cutoff": "x", "items": [
            {"type": "pending", "title": "t", "quotes": [{"text": "现在加到了企微\n在我的中台", "name": "a", "open_id": "ou_1"}]}]}
        txt = sr.fb_render(d)["zh_cn"]["content"][3][0]["text"]
        self.assertEqual(txt.count("\n"), 0)
        self.assertIn("企微 在我的中台", txt)

    def test_zero_width_space_between_han_and_digits(self):
        d = {"date": "2026-11-19", "cutoff": "x", "items": [
            {"type": "fixed", "quotes": [{"text": "填写1H变成0.5H了", "name": "a", "open_id": "ou_1"}]}]}
        txt = sr.fb_render(d)["zh_cn"]["content"][3][0]["text"]
        self.assertIn("填写\u200b1H\u200b变成\u200b0.5H\u200b了", txt)


class TestFeedbackSend(Base):
    def test_send_then_same_day_edits_in_place(self):
        d = fx("feedback-2026-11-18.draft.json")
        run = FakeRun(content="📅<u>**2026-11-18 需求反馈**</u>")
        out = sr.fb_send(self.draft_file(d), run=run)
        self.assertEqual((out["message_id"], out["edited"]), ("om_1", False))
        args, who = run.calls[0]
        self.assertEqual((args[1], who), ("+messages-send", "bot"))
        self.assertIn(sr.OWNER, args)
        self.assertEqual(json.loads(args[args.index("--content") + 1]), fx("feedback-2026-11-18.post.json"))
        arch = json.loads((sr.HOME / "feedback/2026-11-18.json").read_text())
        self.assertEqual(arch["dm_message_id"], "om_1")
        out = sr.fb_send(self.draft_file(d), run=run)   # 常浩说要改：同一天原地改，不另发
        self.assertTrue(out["edited"])
        self.assertEqual(run.verbs().count("+messages-send"), 1)
        self.assertIn("+messages-edit", run.verbs())

    def test_send_checks_against_previous_archive(self):
        sr.save(sr.HOME / "feedback/2026-11-17.json", fx("feedback-2026-11-17.draft.json"))
        d = fx("feedback-2026-11-18.draft.json")
        d["items"] = [i for i in d["items"] if i["type"] != "planned"]
        run = FakeRun()
        with self.assertRaises(sr.Invalid):
            sr.fb_send(self.draft_file(d), run=run)
        self.assertEqual(run.calls, [])

    def test_readback_without_title_fails(self):
        with self.assertRaises(sr.md.Fail):
            sr.fb_send(self.draft_file(fx("feedback-2026-11-18.draft.json")), run=FakeRun(content="别的"))
        self.assertFalse((sr.HOME / "feedback/2026-11-18.json").exists())

    def test_status_lists_carry_and_read_from(self):
        import datetime as dt
        day = dt.date(2026, 11, 19)
        self.assertIsNone(sr.fb_status(day)["previous"])
        sr.save(sr.HOME / "feedback/2026-11-18.json", fx("feedback-2026-11-18.draft.json"))
        st = sr.fb_status(day)
        self.assertEqual((st["previous"], st["read_from"], st["today_sent"]), ("2026-11-18", "2026-11-18 18:41", None))
        self.assertEqual([c["type"] for c in st["carry"]], ["pending"] * 2 + ["planned"] * 4)

    def test_status_same_day_rerun_still_reads_from_previous_period(self):
        """当天试跑过一期，18:00 再跑仍从上一期截止时间读，原地改当天那条（不漏早上的反馈）。"""
        import datetime as dt
        sr.save(sr.HOME / "feedback/2026-11-18.json", fx("feedback-2026-11-18.draft.json"))
        sr.save(sr.HOME / "feedback/2026-11-19.json", {"date": "2026-11-19", "cutoff": "2026-11-19 11:00",
                                                         "items": [], "dm_message_id": "om_trial"})
        st = sr.fb_status(dt.date(2026, 11, 19))
        self.assertEqual((st["read_from"], st["today_sent"]), ("2026-11-18 18:41", "om_trial"))


class TestFeedbackGroup(Base):
    def setUp(self):
        super().setUp()
        self.d = fx("feedback-2026-11-18.draft.json")
        self.ids = sorted({q["open_id"] for i in self.d["items"] for q in i["quotes"]})

    def test_refuses_before_owner_confirmed(self):
        sr.save(sr.HOME / "feedback/2026-11-18.json", self.d)
        with self.assertRaises(sr.Invalid):
            sr.fb_group("2026-11-18", run=FakeRun(members=self.ids))

    def test_member_missing_stops_before_sending(self):
        sr.save(sr.HOME / "feedback/2026-11-18.json", {**self.d, "dm_message_id": "om_dm"})
        run = FakeRun(members=self.ids[1:])
        with self.assertRaises(sr.Invalid):
            sr.fb_group("2026-11-18", run=run)
        self.assertNotIn("+messages-send", run.verbs())

    def test_sends_as_user_once_and_checks_mentions(self):
        sr.save(sr.HOME / "feedback/2026-11-18.json", {**self.d, "dm_message_id": "om_dm"})
        run = FakeRun(members=self.ids, mentions=self.ids[:-1])
        out = sr.fb_group(run=run)
        send = next((a, w) for a, w in run.calls if a[1] == "+messages-send")
        self.assertEqual(send[1], "user")
        self.assertIn(sr.GROUP, send[0])
        self.assertEqual(json.loads(send[0][send[0].index("--content") + 1]), fx("feedback-2026-11-18.post.json"))
        self.assertFalse(out["mentions_ok"])
        self.assertEqual(len(out["mentions_missing"]), 1)
        with self.assertRaises(sr.Invalid):   # 用户身份消息不能改，只发一次
            sr.fb_group("2026-11-18", run=run)
        with self.assertRaises(sr.Invalid):   # 发过群就不再改私聊稿
            sr.fb_send(self.draft_file(self.d), run=run)


class TestProgress(Base):
    def setUp(self):
        super().setUp()
        self.d = fx("progress-2026-11-18.data.json")

    def test_week_blocks_need_same_count(self):
        d = copy.deepcopy(self.d)
        d["next_week"]["items"].pop()
        self.assertIn("两块条数要相同", "\n".join(sr.pg_check(d)))

    def test_shape_errors(self):
        e = "\n".join(sr.pg_check({"period": {"start": "x"}, "delivered": {}, "feedback": {"done": 3, "total": 2}}))
        for s in ("period.start", "period.end", "period.stage", "delivered 三类都空", "feedback", "next_node", "summary"):
            self.assertIn(s, e)

    def test_optional_usage_and_backlog(self):
        d = copy.deepcopy(self.d)
        d.pop("usage")
        d["backlog"] = []
        c = json.dumps(sr.pg_render(d), ensure_ascii=False)
        self.assertNotIn("使用数据", c)
        self.assertNotIn("待排期", c)

    def test_send_then_patch_same_period(self):
        run = FakeRun(content="<card title=\"销售 AI 侧边栏｜项目推进进度\">")
        out = sr.pg_send(self.draft_file(self.d), run=run)
        self.assertFalse(out["updated"])
        card, note = [a for a, _ in run.calls if a[1] == "+messages-send"]
        self.assertEqual(json.loads(card[card.index("--content") + 1]), fx("progress-2026-11-18.card.json"))
        self.assertIn(sr.CONFIRM_NOTE, note)
        out = sr.pg_send(self.draft_file(self.d), run=run)
        self.assertTrue(out["updated"])
        self.assertEqual(run.verbs().count("+messages-send"), 2)   # 改卡片不再发提示
        self.assertIn("messages", run.verbs()[-2:])

    def test_status_next_period(self):
        import datetime as dt
        self.assertIsNone(sr.pg_status()["previous"])
        sr.save(sr.HOME / "progress/2026-11-18.json", self.d)
        st = sr.pg_status(today=dt.date(2026, 11, 25))
        self.assertEqual((st["start"], st["end"], st["stage"]), ("2026-11-19", "2026-11-25", "全员试用第 2 周"))


class TestMain(Base):
    def test_render_invalid_exit_2(self):
        import contextlib
        import io
        p = self.draft_file({"date": "x"})
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sr.main(["feedback", "render", str(p)]), 2)

    def test_render_ok(self):
        import contextlib
        import io
        p = self.draft_file(fx("progress-2026-11-18.data.json"))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(sr.main(["progress", "render", str(p)]), 0)
        self.assertEqual(json.loads(out.getvalue()), fx("progress-2026-11-18.card.json"))


if __name__ == "__main__":
    unittest.main()
