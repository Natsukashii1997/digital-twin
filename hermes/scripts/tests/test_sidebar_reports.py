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
    def __init__(self, members=None, content="📅<u>**{date} 需求反馈**</u>", mentions=None, group=None):
        self.calls, self.n = [], 0
        self.members, self.content, self.mentions, self.group = members or [], content, mentions, group or []

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
        if verb == "+chat-messages-list":
            if isinstance(self.group, Exception):
                raise self.group
            return {"messages": self.group, "has_more": False}
        return {}

    def verbs(self):
        return [a[1] for a, _ in self.calls]


class PagedRun(FakeRun):
    """像真的 lark-cli 那样分轮回话（#312）：列表一轮只给 per 条、has_more=true 并给 page_token，后面的要带 --page-token 才给。
    lose_token：还有下一页却不给标记。truncations：群成员名单被飞书按安全设置截断时带的字段。"""
    def __init__(self, per, lose_token=False, truncations=None, **kw):
        super().__init__(**kw)
        self.per, self.lose_token, self.truncations = per, lose_token, truncations

    def __call__(self, *args, **kw):
        data = super().__call__(*args, **kw)
        key = next((k for k in ("messages", "users") if k in data and args[1] in ("+chat-messages-list", "+chat-members-list")), None)
        if not key:
            return data
        start = int(args[args.index("--page-token") + 1]) if "--page-token" in args else 0
        page = {key: data[key][start:start + self.per], "has_more": start + self.per < len(data[key])}
        if page["has_more"] and not self.lose_token:
            page["page_token"] = str(start + self.per)
        if self.truncations is not None:
            page["truncations"] = self.truncations
        return page


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
        self.assertIn("确认上线就改列已上线", self.errs(d))

    def launch(self, title_prefix):
        """把一条上期待上线原样改列已上线，放到已修复之后、待解决之前。"""
        d = copy.deepcopy(self.cur)
        it = next(i for i in d["items"] if i.get("title", "").startswith(title_prefix))
        d["items"].remove(it)
        it = {**it, "type": "launched"}
        at = max(n for n, i in enumerate(d["items"]) if i["type"] == "fixed") + 1
        d["items"].insert(at, it)
        return d, it

    def test_old_planned_moved_to_launched_passes(self):
        d, _ = self.launch("侧边栏支持编辑学员信息")
        self.assertEqual(sr.fb_check(d, self.prev), [])

    def test_launched_must_keep_old_quotes(self):
        d, it = self.launch("侧边栏支持编辑学员信息")
        it["quotes"] = it["quotes"][:1]
        self.assertIn("原话要照旧", self.errs(d))

    def test_launched_needs_title_and_order(self):
        q = [{"text": "x", "name": "a", "open_id": "ou_1"}]
        e = "\n".join(sr.fb_check({"date": "2026-11-19", "cutoff": "x", "items": [
            {"type": "pending", "title": "p", "quotes": q}, {"type": "launched", "quotes": q}]}))
        self.assertIn("要写 title", e)
        self.assertIn("已修复 → 已上线 → 待解决 → 待上线", e)

    def test_launched_renders_as_own_sagittarius_group(self):
        """常浩 2026-11-19 定：已上线写「♐ 已上线：<功能>」，单独一组（前后各空一行）。"""
        d, _ = self.launch("侧边栏支持编辑学员信息")
        rows = sr.fb_render(d)["zh_cn"]["content"]
        heads = [r for r in rows if len(r) == 2 and r[1].get("style") == ["bold"]]
        launched = [r for r in heads if r[0]["text"] == "♐ "]
        self.assertEqual([r[1]["text"] for r in launched], ["已上线：侧边栏支持编辑学员信息（AI 从会话提取，人工辅助修改）"])
        n = rows.index(launched[0])
        blank = [{"tag": "text", "text": ""}]
        self.assertEqual(rows[n - 1], blank)
        self.assertEqual(rows[n + 2], blank)
        self.assertNotIn("⬆️ ", [r[0]["text"] for r in heads if r[1]["text"].startswith("已上线")])

    def test_launched_not_carried_to_next_period(self):
        d, _ = self.launch("侧边栏支持编辑学员信息")
        # 已上线算完成：下一期不再要求带上
        nxt = {"date": "2026-11-20", "cutoff": "x", "items": [i for i in d["items"] if i["type"] in ("pending", "planned")]}
        self.assertEqual(sr.fb_check(nxt, d), [])

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
        self.assertEqual(run.verbs(), ["+chat-messages-list"])   # 只读了群里上一期，没发也没改

    def test_readback_without_title_fails(self):
        with self.assertRaises(sr.md.Fail):
            sr.fb_send(self.draft_file(fx("feedback-2026-11-18.draft.json")), run=FakeRun(content="别的"))
        self.assertFalse((sr.HOME / "feedback/2026-11-18.json").exists())

    def test_status_lists_carry_and_read_from(self):
        import datetime as dt
        day = dt.date(2026, 11, 19)
        self.assertIsNone(sr.fb_status(day, run=FakeRun())["previous"])
        sr.save(sr.HOME / "feedback/2026-11-18.json", fx("feedback-2026-11-18.draft.json"))
        st = sr.fb_status(day, run=FakeRun())
        self.assertEqual((st["previous"], st["read_from"], st["today_sent"]), ("2026-11-18", "2026-11-18 18:41", None))
        self.assertEqual(st["previous_source"], "私聊存档（群里没找到常浩发的这期）")
        self.assertEqual([c["type"] for c in st["carry"]], ["pending"] * 2 + ["planned"] * 4)

    def test_status_same_day_rerun_still_reads_from_previous_period(self):
        """当天试跑过一期，18:00 再跑仍从上一期截止时间读，原地改当天那条（不漏早上的反馈）。"""
        import datetime as dt
        sr.save(sr.HOME / "feedback/2026-11-18.json", fx("feedback-2026-11-18.draft.json"))
        sr.save(sr.HOME / "feedback/2026-11-19.json", {"date": "2026-11-19", "cutoff": "2026-11-19 11:00",
                                                         "items": [], "dm_message_id": "om_trial"})
        st = sr.fb_status(dt.date(2026, 11, 19), run=FakeRun())
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


class TestGroupMembersReadToTheEnd(Base):
    """#312：读群成员 --page-all 有页数上限，到顶不报错。只看第一轮会把在群里的人说成不在。"""
    def setUp(self):
        super().setUp()
        self.d = fx("feedback-2026-11-18.draft.json")
        self.ids = sorted({q["open_id"] for i in self.d["items"] for q in i["quotes"]})
        sr.save(sr.HOME / "feedback/2026-11-18.json", {**self.d, "dm_message_id": "om_dm"})
        self.everyone = [f"ou_filler{i}" for i in range(25)] + self.ids   # 被 @ 的人排在第一轮之后

    def test_member_beyond_first_round_is_in_the_group(self):
        run = PagedRun(10, members=self.everyone, mentions=self.ids)
        sr.fb_group("2026-11-18", run=run)
        self.assertIn("+messages-send", run.verbs())
        members = [a for a, _ in run.calls if a[1] == "+chat-members-list"]
        self.assertEqual([a[a.index("--page-token") + 1] if "--page-token" in a else None for a in members],
                         [None] + [str(n) for n in range(10, len(self.everyone), 10)])

    def test_member_list_not_read_to_the_end_does_not_send(self):
        run = PagedRun(10, lose_token=True, members=self.everyone)
        with self.assertRaisesRegex(sr.md.Fail, "读群成员：还有下一页，但飞书没给翻页标记，没读全"):
            sr.fb_group("2026-11-18", run=run)
        self.assertNotIn("+messages-send", run.verbs())

    def test_truncated_member_list_does_not_send(self):
        run = PagedRun(100, truncations=[{"bucket": "users"}], members=self.ids[1:])
        with self.assertRaisesRegex(sr.md.Fail, "读群成员：飞书按安全设置截断了名单"):   # 不报成「某某不在群里」
            sr.fb_group("2026-11-18", run=run)
        self.assertNotIn("+messages-send", run.verbs())

    def test_untruncated_list_still_reports_missing_member(self):
        run = PagedRun(10, truncations=[], members=self.everyone[:-1])
        with self.assertRaises(sr.Invalid):
            sr.fb_group("2026-11-18", run=run)


class TestPreviousFromGroup(Base):
    """上一期以群里实际发出的版本为准（2026-11-20 常浩定）：11-19 他自己发群时删了 ✴️ 微信支付截图、加了两条 ♐，
    亚古兽只看私聊存档，把删掉的又带了回来。"""
    DM = {"date": "2026-11-19", "cutoff": "2026-11-19 18:28", "dm_message_id": "om_dm19", "items": [
        {"type": "fixed", "quotes": [{"text": "修好了", "name": "马文豪", "open_id": "ou_6d7f17803af57ebd15fb31675134a516"}]},
        {"type": "pending", "title": "上传微信支付截图后未识别出收款公司和支付方式（待补截图核查）",
         "quotes": [{"text": "现在微信支付好，上传付款截图还是识别不出收款公司和支付方式对吧", "name": "陈芳",
                     "open_id": "ou_ee415755a9d8164263f31631c985e434"}]}]}

    def setUp(self):
        super().setUp()
        self.msg = fx("feedback-2026-11-19.group-msg.json")
        sr.save(sr.HOME / "feedback/2026-11-19.json", self.DM)

    def status(self, group):
        import datetime as dt
        return sr.fb_status(dt.date(2026, 11, 20), run=FakeRun(group=group))

    def test_parse_real_1119_group_post(self):
        items = sr.parse_group_post(self.msg)
        self.assertEqual([i["type"] for i in items], ["fixed"] * 4 + ["launched"] * 3 + ["planned"] * 3)
        self.assertNotIn("pending", [i["type"] for i in items])
        edit = items[-1]
        self.assertEqual(edit["title"], "侧边栏支持编辑学员信息（AI 从会话提取，人工辅助修改）")
        self.assertEqual([(q["name"], q["open_id"]) for q in edit["quotes"]],
                         [("吴家慧", "ou_92c89f4852f9fbc312771ab63c0a2ea9"), ("马文豪", "ou_6d7f17803af57ebd15fb31675134a516")])
        self.assertEqual(items[6]["quotes"], [])          # 只有图片的 ♐
        self.assertEqual(len(items[3]["quotes"]), 6)      # 同一项多条原话

    def test_status_carries_from_group_version(self):
        st = self.status([self.msg])
        self.assertEqual([c["type"] for c in st["carry"]], ["planned"] * 3)
        self.assertTrue(st["previous_group_sent"])
        self.assertIn("群里", st["previous_source"])
        arch = json.loads((sr.HOME / "feedback/2026-11-19.json").read_text(encoding="utf-8"))
        self.assertEqual(arch["group_message_id"], self.msg["message_id"])
        self.assertEqual(arch["dm_items"], self.DM["items"])      # 私聊原版留底
        self.assertEqual([i["type"] for i in arch["items"]][-3:], ["planned"] * 3)
        self.assertEqual(self.status([self.msg])["carry"], st["carry"])   # 再跑一次结果不变，私聊原版不被覆盖
        arch2 = json.loads((sr.HOME / "feedback/2026-11-19.json").read_text(encoding="utf-8"))
        self.assertEqual(arch2["dm_items"], self.DM["items"])

    def test_send_checks_carry_against_group_version(self):
        planned = [i for i in sr.parse_group_post(self.msg) if i["type"] == "planned"]
        d = {"date": "2026-11-20", "cutoff": "2026-11-20 18:02", "items": [
            {"type": "fixed", "quotes": [{"text": "x", "name": "韩丹", "open_id": "ou_2091f26b7e50f2d9b0fb7b9619d4eabd"}]},
            {**planned[2], "type": "launched"}, planned[0], planned[1]]}
        run = FakeRun(content="📅<u>**2026-11-20 需求反馈**</u>", group=[self.msg])
        self.assertEqual(sr.fb_send(self.draft_file(d), run=run)["message_id"], "om_1")   # 没带微信支付也能过
        bad = {**d, "items": d["items"][:-1]}
        with self.assertRaises(sr.Invalid):                                        # 群里那版的待上线照样要带
            sr.fb_send(self.draft_file(bad), run=FakeRun(group=[self.msg]))

    def test_no_group_post_falls_back_to_private_archive(self):
        st = self.status([])
        self.assertEqual([c["type"] for c in st["carry"]], ["pending"])
        self.assertFalse(st["previous_group_sent"])
        self.assertIn("私聊存档", st["previous_source"])

    def test_ignores_other_senders_and_other_dates(self):
        other = {**self.msg, "sender": {**self.msg["sender"], "id": "ou_someone"}}
        old = {**self.msg, "content": self.msg["content"].replace("2026-11-19 需求反馈", "2026-11-18 需求反馈")}
        self.assertEqual([c["type"] for c in self.status([other, old])["carry"]], ["pending"])

    def test_latest_group_post_wins(self):
        first = {**self.msg, "message_id": "om_g1", "create_time": "2026-11-19 18:40",
                 "content": self.msg["content"].split("\n⬆️")[0]}
        st = self.status([first, self.msg])
        self.assertEqual(len(st["carry"]), 3)

    def test_group_post_beyond_first_round_is_found(self):
        """#312：从上一期那天读到现在，消息多了以后常浩发的那期排在第一轮之后。原来只取第一轮，认不到、退回私聊存档。"""
        import datetime as dt
        chatter = [{"message_id": f"om_c{i}", "msg_type": "text", "content": "闲聊", "sender": {"id": "ou_someone"}} for i in range(12)]
        run = PagedRun(5, group=chatter + [self.msg])
        st = sr.fb_status(dt.date(2026, 11, 20), run=run)
        self.assertTrue(st["previous_group_sent"])
        self.assertEqual([c["type"] for c in st["carry"]], ["planned"] * 3)
        reads = [a for a, _ in run.calls if a[1] == "+chat-messages-list"]
        self.assertEqual([a[a.index("--page-token") + 1] if "--page-token" in a else None for a in reads][:3], [None, "5", "10"])
        self.assertTrue(all(who == "user" for a, who in run.calls if a[1] == "+chat-messages-list"))   # 续翻也用常浩的用户身份

    def test_group_not_read_to_the_end_fails_closed(self):
        import datetime as dt
        chatter = [{"message_id": f"om_c{i}", "msg_type": "text", "content": "闲聊", "sender": {"id": "ou_someone"}} for i in range(12)]
        with self.assertRaisesRegex(sr.md.Fail, "读群里上一期：还有下一页，但飞书没给翻页标记，没读全"):   # 不退回私聊存档
            sr.fb_status(dt.date(2026, 11, 20), run=PagedRun(5, lose_token=True, group=chatter + [self.msg]))

    def test_group_read_failure_fails_closed(self):
        with self.assertRaises(sr.md.Fail):
            self.status(sr.md.Fail("im +chat-messages-list：网络断开"))

    def test_unparsable_group_post_fails_closed(self):
        with self.assertRaises(sr.md.Fail):
            self.status([{**self.msg, "content": "📅<u>**2026-11-19 需求反馈**</u>\n随便写的"}])

    def test_quote_mention_mismatch_fails_closed(self):
        with self.assertRaises(sr.md.Fail):
            self.status([{**self.msg, "mentions": self.msg["mentions"][:-1]}])

    def test_known_group_message_id_is_reread(self):
        """脚本自己发过群（存档有 group_message_id）也按群里现在的内容为准：常浩可能事后在飞书里改过。"""
        sr.save(sr.HOME / "feedback/2026-11-19.json", {**self.DM, "group_message_id": self.msg["message_id"]})
        st = self.status([self.msg])
        self.assertEqual([c["type"] for c in st["carry"]], ["planned"] * 3)


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
