"""记忆日报（亚古兽）：memdaily_data / memdaily_publish。全部用假的 run，不联网、不碰真实文档和数据目录。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import json
import sys
import tempfile
import os
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memdaily_data as md  # noqa: E402
import memdaily_publish as mp  # noqa: E402

ME = md.ME
md.REGISTRY = Path(tempfile.mkdtemp()) / "docs.json"   # 不读本机真实的文档登记
D = dt.date(2026, 11, 10)   # 周二
OLD = "## 📅 2026-11-08（周日）\n\n### 四、⚡ 行动 & 阻塞\n\n|  |  |\n|-|-|\n\n1\n\n⏩ 行动\n\n旧事项\n\n### 五、📚 方法论沉淀\n\n无\n"


def msg(chat, ctype, sender, name, t, text, partner=None, chat_name=None, mentions=None):
    m = {"chat_id": chat, "chat_type": ctype, "sender": {"id": sender, "name": name, "sender_type": "user"},
         "create_time": f"2026-11-10 {t}", "msg_type": "text", "content": text, "mentions": mentions or []}
    if partner:
        m["chat_partner"] = {"open_id": partner}
    if chat_name:
        m["chat_name"] = chat_name
    return m


class FakeRun:
    """按命令回话，记下每次调用。"""
    def __init__(self, doc_md="", doc_xml="", day=D):
        self.calls, self.doc_md, self.doc_xml = [], doc_md, doc_xml
        self.search = [
            msg("oc_p1", "p2p", ME, "常浩", "10:24", "这个今天要", partner="ou_hang"),
            msg("oc_p1", "p2p", "ou_hang", "杭添", "10:28", "这个算双十一的吗", partner="ou_hang"),
            msg("oc_bot", "p2p", ME, "常浩", "11:00", "帮我查下", partner="ou_bot"),
            msg("oc_g1", "group", ME, "常浩", "10:25", "营销物料新增", chat_name="营销物料同步"),
            msg("oc_g2", "group", "ou_x", "某人", "09:00", "闲聊", chat_name="别的群"),
        ]
        self.white = [msg("oc_w", "group", "ou_y", "许铭凯", "14:03", "先确认数据准确性", chat_name="AI实验室")]
        self.widths = [55, 411, 107, 190, 168, 207]

    def __call__(self, *args, **kw):
        self.calls.append((args, kw))
        cmd = " ".join(args[:2])
        if cmd == "docs +fetch":
            return {"document": {"content": self.doc_xml if "xml" in args else self.doc_md}}
        if cmd == "im +messages-search":
            return {"messages": self.search, "has_more": False}
        if cmd == "contact +get-user":
            return {"user": {} if args[3] == "ou_bot" else {"name": "杭添"}}
        if cmd == "im +chat-messages-list":
            return {"messages": self.white, "has_more": False}
        if cmd in ("vc +search", "minutes +search"):
            return {"items": [], "has_more": False, "page_token": "end"}   # 真的 lark-cli 读完了也带 page_token
        if cmd == "calendar +agenda":
            return [{"summary": "舀水群每周同频共识会", "start_time": {"datetime": "2026-11-10T18:00:00+08:00"},
                     "end_time": {"datetime": "2026-11-10T19:00:00+08:00"}}]
        if cmd == "task +get-related-tasks":
            return {"items": [], "has_more": False, "page_token": "end"}
        if cmd == "docs +update":
            content = Path(kw["cwd"], args[args.index("--content") + 1][1:]).read_text(encoding="utf-8")
            self.doc_xml = "<title>记忆日报</title>" + content + self.doc_xml.replace("<title>记忆日报</title>", "")
            return {}
        if cmd == "api GET":
            return {"items": [{"block_type": 31, "table": {"property": {"column_width": self.widths}}}]}
        if cmd == "im +messages-send":
            return {}
        raise AssertionError(args)


class PagedRun(FakeRun):
    """像真的 lark-cli 那样分轮回话（#312）：列表一轮只给 per 条、has_more=true 并给 page_token，后面的要带 --page-token 才给。
    lose_token 写成命令名（如 "im +messages-search"）：那个命令还有下一页却不给标记。"""
    def __init__(self, per, lose_token=None, **kw):
        super().__init__(**kw)
        self.per, self.lose_token = per, lose_token
        self.meetings = [{"display_info": f"会议{i}\n09:00"} for i in range(5)]
        self.tasks = [{"summary": f"任务{i}", "members": [{"id": ME, "role": "assignee"}], "created_at": "2026-11-10 09:00"} for i in range(5)]

    def __call__(self, *args, **kw):
        cmd = " ".join(args[:2])
        data = super().__call__(*args, **kw)
        if cmd == "vc +search":
            data = {"items": self.meetings}
        if cmd == "task +get-related-tasks":
            data = {"items": self.tasks}
        key = next((k for k in ("messages", "items") if isinstance(data, dict) and k in data), None)
        if not key or cmd in ("im +messages-send", "api GET", "docs +fetch"):
            return data
        start = int(args[args.index("--page-token") + 1]) if "--page-token" in args else 0
        page = {key: data[key][start:start + self.per], "has_more": start + self.per < len(data[key])}
        if page["has_more"] and cmd != self.lose_token:
            page["page_token"] = str(start + self.per)
        return page

    def tokens(self, cmd):
        return [a[a.index("--page-token") + 1] if "--page-token" in a else None for a, _ in self.calls if " ".join(a[:2]) == cmd]


CONFIG = {"whitelist_groups": [{"chat_id": "oc_w", "name": "AI实验室"}], "excluded_meetings": ["舀水群每周同频"],
          "priority_people": ["许铭凯"], "dropped_topics": ["CRM"]}


def facts(run=None):
    return md.collect(D, run=run or FakeRun(doc_md=OLD), config=CONFIG)


def good_draft():
    return {
        "summary": ["澳洲赠课图交付", "看板转数据核对", "名片定橙色"],
        "items": [{"name": "澳洲新生赠课图", "status": "✅ 已完成", "people": "杭添", "source": "私聊", "topic": "T线-定价"}],
        "p2p": [{"party": "杭添", "points": ["10:24-10:28 常浩说「这个今天要」，杭添问是否算双十一"], "conclusion": "当天交图"}],
        "groups": [{"name": "AI实验室", "points": ["14:03 许铭凯「先确认数据准确性」"], "conclusion": "先核数据"}],
        "meetings": [], "skipped_meetings": "已确认跳过的日程：18:00-19:00 舀水群每周同频共识会 — 永久不去",
        "decisions": [{"item": "看板", "choice": "先核数据", "background": "数据不准", "logic": "先准后快", "exclude": "直接上线", "status": "待验证"}],
        "insights": [{"type": "新洞察", "content": "「先确认数据准确性」"}],
        "actions": [{"type": "⏩ 行动", "task": "旧事项", "owner": "常浩", "due": "待定", "status": "🔄 进行中"}],
        "methods": [], "suggestions": [],
    }


class Collect(unittest.TestCase):
    def setUp(self):
        # 测试隔离默认 DT_NET_WAIT_SECONDS=0（不等网络）；这里要测 lark 调用的网络重试，按正式的 300 秒预算
        p = mock.patch.dict(os.environ, {"DT_NET_WAIT_SECONDS": "300"})
        p.start()
        self.addCleanup(p.stop)

    def test_sources_and_filters(self):
        f = facts()
        self.assertIsNone(f["skip"])
        self.assertEqual([p["name"] for p in f["private"]], ["杭添"])          # 机器人私聊排除
        self.assertEqual(f["coverage"]["bot_chats"], 1)
        names = {g["name"]: g["whitelist"] for g in f["groups"]}
        self.assertEqual(names, {"营销物料同步": False, "AI实验室": True})      # 常浩发言的群 + 白名单群；闲聊群略过
        self.assertEqual(f["coverage"]["skipped_groups"], 1)
        self.assertTrue(f["agenda"][0]["excluded"])
        self.assertIn("旧事项", f["prev"]["actions"])
        self.assertNotIn("|  |", f["prev"]["actions"])                         # 空壳表格行已整理掉

    def test_lists_are_read_past_the_first_round(self):
        """#312：lark-cli 到了页数上限（或只回第一页）不报错，只留 has_more=true。原来只取第一轮：5 条消息只剩 2 条，
        私聊、常浩发言的群、会议、任务悄悄没有。"""
        whole, run = PagedRun(50, doc_md=OLD), PagedRun(2, doc_md=OLD)
        run.white = whole.white = [msg("oc_w", "group", "ou_y", "许铭凯", f"14:0{i}", f"白名单第{i}条", chat_name="AI实验室") for i in range(5)]
        f = md.collect(D, run=run, config=CONFIG)
        self.assertEqual(f, md.collect(D, run=whole, config=CONFIG))          # 分轮读和一轮读完结果一样
        self.assertEqual({g["name"] for g in f["groups"]}, {"营销物料同步", "AI实验室"})   # 营销物料同步在第 2 轮
        self.assertEqual(len(next(g for g in f["groups"] if g["whitelist"])["messages"]), 5)
        self.assertEqual((f["meetings"], [t["summary"] for t in f["tasks"]]),
                         ([f"会议{i}" for i in range(5)], [f"任务{i}" for i in range(5)]))
        self.assertEqual(f["coverage"]["messages"], 10)
        for cmd in ("im +messages-search", "im +chat-messages-list", "vc +search", "task +get-related-tasks"):
            self.assertEqual(run.tokens(cmd), [None, "2", "4"], cmd)

    def test_list_that_cannot_be_read_to_the_end_fails(self):
        for cmd, what in (("im +messages-search", "读当天消息"), ("im +chat-messages-list", "读白名单群「AI实验室」"),
                          ("vc +search", "读会议记录"), ("task +get-related-tasks", "读任务")):
            run = PagedRun(2, lose_token=cmd, doc_md=OLD)
            run.white = run.white * 3
            with self.assertRaisesRegex(md.Fail, f"{what}：还有下一页，但飞书没给翻页标记，没读全"):   # 不拿半截材料写日报
                md.collect(D, run=run, config=CONFIG)

    def test_already_written_skips(self):
        f = md.collect(D, run=FakeRun(doc_md="## 📅 2026-11-10（周二）\n"), config=CONFIG)
        self.assertIn("已经写在记忆日报里", f["skip"])

    def test_quiet_weekend_skips(self):
        run = FakeRun(doc_md=OLD)
        run.search = [msg("oc_p1", "p2p", "ou_hang", "杭添", "10:28", "周末好", partner="ou_hang")]
        f = md.collect(dt.date(2026, 11, 15), run=run, config=CONFIG)   # 周日
        self.assertIn("周末", f["skip"])

    def test_material_mentions_rules(self):
        text = md.material(facts())
        for s in ("前一篇的行动 & 阻塞", "私聊 杭添", "群「AI实验室」（白名单）", "永久不去，跳过", "CRM", "许铭凯"):
            self.assertIn(s, text)

    def test_work_lines_only_from_work_table(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t, "v.md")
            p.write_text("## 工作线\n\n| 名称 | 旧 |\n|---|---|\n| 双11 | x |\n| 中台 | y |\n\n## 个人项目\n\n| 名称 |\n|---|\n| 数码世界 |\n", encoding="utf-8")
            self.assertEqual(md.work_lines(p), ["双11", "中台"])

    def test_lark_retries_only_retryable(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            body = {"ok": False, "error": {"retryable": len(calls) < 2, "message": "internal"}} if len(calls) < 2 else {"ok": True, "data": {"x": 1}}
            return type("R", (), {"stdout": json.dumps(body), "stderr": ""})()
        orig = md.subprocess.run
        md.subprocess.run = fake
        try:
            self.assertEqual(md.lark("im", "+x", sleep=lambda s: None), {"x": 1})
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][-2:], ["--as", "user"])
        finally:
            md.subprocess.run = orig


    def test_lark_retries_network_errors_but_not_others(self):
        msgs = ['API call failed: Post "https://open.feishu.cn/...": read tcp 1.2.3.4:5->6.7.8.9:443: read: operation timed out',
                "permission denied",
                "The document engine could not export the document as DocxXML. Retry the export; if it keeps failing, contact support"]
        for msg, want_calls in ((msgs[0], 2), (msgs[1], 1), (msgs[2], 2)):
            calls, waits = [], []

            def fake(cmd, **kw):
                calls.append(cmd)
                body = {"ok": False, "error": {"retryable": False, "message": msg}} if len(calls) == 1 else {"ok": True, "data": {}}
                return type("R", (), {"stdout": json.dumps(body), "stderr": ""})()
            orig = md.subprocess.run
            md.subprocess.run = fake
            try:
                if want_calls == 2:
                    self.assertEqual(md.lark("docs", "+fetch", sleep=waits.append), {})
                    self.assertEqual(waits, [md.netwait.INTERVAL])
                else:
                    with self.assertRaises(md.Fail):
                        md.lark("docs", "+fetch", sleep=waits.append)
                self.assertEqual(len(calls), want_calls)
            finally:
                md.subprocess.run = orig

    def test_lark_retries_non_json_network_output(self):
        # 11-14 07:45 真实输出：唤醒后网络没通，lark-cli 刷新令牌失败，stdout 空、stderr 不是 JSON
        wake = ('[lark-cli] [WARN] uat-client: refresh attempt 1/2 failed for ou_x: token refresh request failed: '
                'Post "https://open.feishu.cn/open-apis/authen/v2/oauth/token": dial tcp: lookup open.feishu.cn: no such host')
        for raw, fails, want in ((wake, 2, {"x": 1}), (wake, 30, None), ("panic: something else", 1, None)):
            calls, waits = [], []

            def fake(cmd, **kw):
                calls.append(cmd)
                if len(calls) <= fails:
                    return type("R", (), {"stdout": "", "stderr": raw})()
                return type("R", (), {"stdout": json.dumps({"ok": True, "data": {"x": 1}}), "stderr": ""})()
            orig = md.subprocess.run
            md.subprocess.run = fake
            try:
                if want is not None:
                    self.assertEqual(md.lark("docs", "+fetch", sleep=waits.append), want)
                    self.assertEqual(waits, [md.netwait.INTERVAL] * 2)
                else:
                    with self.assertRaises(md.Fail) as cm:
                        md.lark("docs", "+fetch", sleep=waits.append)
                    self.assertIn("输出不是 JSON", str(cm.exception))
                    self.assertEqual(len(calls), 21 if fails > 1 else 1)             # 300 秒预算，每 15 秒一次（netwait）
                    self.assertEqual(sum(waits), 300 if fails > 1 else 0)
            finally:
                md.subprocess.run = orig

    def test_write_call_is_not_retried(self):
        calls = []

        def fake(cmd, **kw):
            calls.append(cmd)
            return type("R", (), {"stdout": json.dumps({"ok": False, "error": {"message": "read: operation timed out"}}), "stderr": ""})()
        orig = md.subprocess.run
        md.subprocess.run = fake
        try:
            with self.assertRaises(md.Fail):
                md.lark("docs", "+update", tries=1, sleep=lambda s: None)
            self.assertEqual(len(calls), 1)
        finally:
            md.subprocess.run = orig


class Validate(unittest.TestCase):
    def setUp(self):
        self.f = facts()
        self.f["work_lines"] = ["定价", "中台"]

    def test_good_draft_passes(self):
        out, problems, notes = mp.validate(good_draft(), self.f)
        self.assertEqual(problems, [])
        self.assertEqual(out["p2p"][0]["points"][0][:5], "10:24")

    def test_catches_bad_fields(self):
        d = good_draft()
        d["items"][0]["status"] = "进行中"
        d["items"][0]["topic"] = "T线-火星计划"
        d["actions"][0]["type"] = "行动"
        d["decisions"][0]["status"] = "定了"
        problems = mp.validate(d, self.f)[1]
        self.assertEqual(len(problems), 4, problems)

    def test_catches_invented_people_and_times(self):
        d = good_draft()
        d["p2p"].append({"party": "王五", "points": ["09:00 编的"], "conclusion": "x"})
        d["groups"][0]["points"] = ["15:30 时间对不上"]
        d["p2p"][0]["points"].append("没有时间开头")
        problems = mp.validate(d, self.f)[1]
        self.assertTrue(any("王五" in p for p in problems))
        self.assertTrue(any("15:30" in p for p in problems))
        self.assertTrue(any("没有以时间开头" in p for p in problems))

    def test_private_life_row_dropped(self):
        d = good_draft()
        d["insights"].append({"type": "新洞察", "content": "某人要控制胎儿体重"})
        out, problems, notes = mp.validate(d, self.f)
        self.assertEqual(problems, [])
        self.assertEqual(len(out["insights"]), 1)
        self.assertTrue(notes)

    def test_strips_model_numbering_and_matches_alias(self):
        d = good_draft()
        d["p2p"][0]["party"] = "杭添（设计）"
        d["p2p"][0]["points"] = ["1️⃣ 10:24 常浩说今天要"]
        out, problems, _ = mp.validate(d, self.f)
        self.assertEqual(problems, [])
        self.assertTrue(out["p2p"][0]["points"][0].startswith("10:24"))

    def test_empty_day(self):
        d = {k: [] for k in mp.FIELDS}
        d["summary"] = ["⚠️ 今日无工作互动记录"]
        out, problems, _ = mp.validate(d, self.f)
        self.assertEqual(problems, [])
        self.assertIn("⚠️ 今日无工作互动记录", mp.render(out, self.f, {"model": "m", "time": "t"}))


class Render(unittest.TestCase):
    def test_template_shape(self):
        f = facts()
        f["work_lines"] = ["定价"]
        out = mp.validate(good_draft(), f)[0]
        x = mp.render(out, f, {"model": "gpt-6.1-sol", "time": "2026-11-11 07:31"})
        self.assertTrue(x.startswith("<h2>📅 2026-11-10（周二）</h2>"))
        self.assertIn("生成：亚古兽（gpt-6.1-sol）｜接入：Hermes 派单", x)
        self.assertIn('<col width="55"/><col width="411"/><col width="107"/>', x)
        self.assertIn("<h4><b>序号</b></h4>", x)                          # 今日事项表头加粗，同 7/15
        self.assertIn("<p>1️⃣ 10:24-10:28", x)                            # 每个议题独立一行
        self.assertIn("D-20261110-1", x)
        self.assertIn("I-20261110-1", x)
        self.assertIn("AI实验室 ⭐", x)                                    # 白名单群加星
        self.assertIn("（今日无新增高价值建议，跳过。）", x)
        self.assertIn("&amp; 洞察", x)
        self.assertEqual(x.count("<h3>"), 6)
        self.assertEqual(x.count("<table>"), mp.table_count(out))

    def test_escapes(self):
        f = facts()
        f["work_lines"] = ["定价"]
        d = good_draft()
        d["items"][0]["name"] = "A<B & C"
        x = mp.render(mp.validate(d, f)[0], f, {})
        self.assertIn("A&lt;B &amp; C", x)


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = mp.DIR
        mp.DIR = Path(self.tmp.name)

    def tearDown(self):
        mp.DIR = self.orig
        self.tmp.cleanup()

    def collect(self, d):
        f = facts()
        f["work_lines"] = ["定价"]
        return f

    def test_build_retries_once_then_writes_and_pushes(self):
        replies = [json.dumps({"summary": []}), json.dumps(good_draft(), ensure_ascii=False)]
        seen = []

        def gen(text, workdir):
            seen.append(text)
            return replies.pop(0), "sid", "gpt-6.1-sol"
        rec = mp.build("2026-11-10", collect=self.collect, gen=gen)
        self.assertEqual(rec["attempt"], 2)
        self.assertIn("上一稿没通过核对", seen[1])
        run = FakeRun(doc_xml="<title>记忆日报</title><h2>📅 2026-11-08（周日）</h2><p>旧</p>")
        self.assertEqual(mp.write("2026-11-10", "DOC", run=run), "written")
        upd = [a for a, kw in run.calls if a[:2] == ("docs", "+update")][0]
        self.assertEqual(upd[upd.index("--block-id") + 1], "0")
        self.assertEqual(upd[upd.index("--command") + 1], "block_insert_after")
        self.assertNotIn("overwrite", upd)
        self.assertTrue(list((mp.DIR / "backup").glob("2026-11-10.DOC.before.xml")))
        self.assertTrue(run.doc_xml.index("2026-11-10") < run.doc_xml.index("2026-11-08"))
        self.assertEqual(mp.write("2026-11-10", "DOC", run=run), "exists")   # 再跑不重写
        self.assertEqual(sum(1 for a, _ in run.calls if a[:2] == ("docs", "+update")), 1)
        self.assertEqual(mp.push("2026-11-10", "DOC", run=run), "pushed")
        send = [(a, kw) for a, kw in run.calls if a[:2] == ("im", "+messages-send")][0]
        self.assertEqual(send[1]["identity"], "bot")
        text = send[0][send[0].index("--text") + 1]
        self.assertTrue(text.startswith("📅 2026-11-10 日报已写入\n飞书文档：https://"))
        self.assertIn("今日关键摘要：\n1️⃣ 澳洲赠课图交付", text)
        self.assertEqual(mp.push("2026-11-10", "DOC", run=run), "pushed-before")

    def test_build_gives_up_after_two_bad_drafts(self):
        with self.assertRaises(mp.GenError):
            mp.build("2026-11-10", collect=self.collect, gen=lambda t, w: ("不是 JSON", "s", "m"))
        self.assertFalse((mp.DIR / "2026-11-10.xml").exists())

    def test_verify_flags_empty_cells_and_widths(self):
        mp.build("2026-11-10", collect=self.collect, gen=lambda t, w: (json.dumps(good_draft(), ensure_ascii=False), "s", "m"))
        run = FakeRun(doc_xml="<title>记忆日报</title>")
        run.widths = [1, 2]
        orig = run.__call__

        def broken(*a, **kw):
            r = orig(*a, **kw)
            if a[:2] == ("docs", "+update"):
                run.doc_xml = run.doc_xml.replace("<p>澳洲新生赠课图</p>", "<p></p>")
            return r
        with self.assertRaises(mp.GenError) as e:
            mp.write("2026-11-10", "DOC", run=broken)
        self.assertIn("空单元格", str(e.exception))
        self.assertIn("列宽", str(e.exception))

    def test_skip_day_writes_nothing(self):
        def collect(d):
            return {"skip": "周末没有工作往来"}
        rec = mp.build("2026-11-08", collect=collect, gen=lambda *a: self.fail("不该写稿"))
        self.assertEqual(rec["skip"], "周末没有工作往来")


class Docs(FakeRun):
    """多份文档的假飞书：按 --doc 分开存正文，能建新文档，能设块数和写入时报超限。"""
    def __init__(self, docs=None, counts=None, limit_on=()):
        super().__init__()
        self.docs = docs or {}            # token -> {"md": ..., "xml": ...}
        self.counts = counts or {}        # token -> 列块接口数出来的块数
        self.limit_on = set(limit_on)     # 往这些文档写时报飞书超限
        self.created = []

    def __call__(self, *args, **kw):
        cmd = " ".join(args[:2])
        doc = args[args.index("--doc") + 1] if "--doc" in args else None
        if cmd == "docs +fetch":
            self.calls.append((args, kw))
            d = self.docs.setdefault(doc, {"md": "", "xml": ""})
            return {"document": {"content": d["xml"] if "xml" in args else d["md"]}}
        if cmd == "docs +create":
            self.calls.append((args, kw))
            token = f"NEW{len(self.created) + 1}"
            self.created.append((token, args[args.index("--title") + 1]))
            self.docs[token] = {"md": "", "xml": ""}
            return {"document": {"document_id": token, "url": f"https://x.feishu.cn/docx/{token}"}}
        if cmd == "docs +update":
            self.calls.append((args, kw))
            if doc in self.limit_on:
                raise md.Fail('docs +update：{"code":"DOC_TOTAL_BLOCK_LIMIT","operation":"append","actual":40138,"limit":40000}.')
            content = Path(kw["cwd"], args[args.index("--content") + 1][1:]).read_text(encoding="utf-8")
            self.docs[doc]["xml"] = content + self.docs[doc]["xml"]
            return {}
        if cmd == "api GET" and '"page_size": 500' in args[args.index("--params") + 1]:
            self.calls.append((args, kw))
            token = args[2].split("/documents/")[1].split("/")[0]
            return {"items": [{}] * self.counts.get(token, 0), "has_more": False}
        return super().__call__(*args, **kw)


class MonthlyDocs(unittest.TestCase):
    """11-17 常浩选方案甲：旧汇总文档到了飞书 4 万块上限，按月一份新文档，写前查块数。"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = mp.DIR, md.REGISTRY
        mp.DIR = Path(self.tmp.name)
        md.REGISTRY = mp.DIR / "docs.json"
        mp.build("2026-11-16", collect=lambda d: dict(facts(), work_lines=["定价"], date="2026-11-16", title="📅 2026-11-16（周一）"),
                 gen=lambda t, w: (json.dumps(good_draft(), ensure_ascii=False), "s", "m"))

    def tearDown(self):
        mp.DIR, md.REGISTRY = self.orig
        self.tmp.cleanup()

    def updates(self, run):
        return [a[a.index("--doc") + 1] for a, _ in run.calls if a[:2] == ("docs", "+update")]

    def test_no_registry_means_only_legacy_doc(self):
        reg = md.load_registry()
        self.assertEqual([x["token"] for x in reg["docs"]], [md.DOC])
        self.assertIsNone(reg["docs"][0]["month"])

    def test_first_write_of_month_opens_new_doc_and_leaves_legacy_alone(self):
        run = Docs(docs={md.DOC: {"md": "", "xml": "<h2>📅 2026-11-13（周五）</h2>"}})
        self.assertEqual(mp.write("2026-11-16", run=run), "written")
        self.assertEqual(run.created, [("NEW1", "常浩-记忆日报汇总 · 2026-11")])
        self.assertEqual(self.updates(run), ["NEW1"])                     # 旧文档一个字不动
        self.assertNotIn("2026-11-16", run.docs[md.DOC]["xml"])
        reg = json.loads(md.REGISTRY.read_text(encoding="utf-8"))
        self.assertEqual([(x["token"], x["month"]) for x in reg["docs"]], [(md.DOC, None), ("NEW1", "2026-11")])
        deliv = json.loads((mp.DIR / "2026-11-16.delivery.json").read_text(encoding="utf-8"))
        self.assertEqual((deliv["doc"], deliv["opened"]["reason"]), ("NEW1", "new_month"))
        mp.push("2026-11-16", run=run)
        send = [a for a, _ in run.calls if a[:2] == ("im", "+messages-send")][0]
        text = send[send.index("--text") + 1]
        self.assertIn("飞书文档：https://vm94j8bzy7.feishu.cn/docx/NEW1", text)
        self.assertIn("本月第一篇，新开了文档《常浩-记忆日报汇总 · 2026-11》", text)

    def test_month_doc_with_room_is_reused(self):
        md.save_registry({"folder": "", "docs": [md.legacy(), {"token": "NOV", "title": "常浩-记忆日报汇总 · 2026-11", "month": "2026-11"}]})
        run = Docs(counts={"NOV": 20000})
        self.assertEqual(mp.write("2026-11-16", run=run), "written")
        self.assertEqual((run.created, self.updates(run)), ([], ["NOV"]))
        self.assertIsNone(json.loads((mp.DIR / "2026-11-16.delivery.json").read_text(encoding="utf-8"))["opened"])

    def test_nearly_full_doc_opens_continuation_before_writing(self):
        md.save_registry({"folder": "FOLDER", "docs": [md.legacy(), {"token": "NOV", "title": "常浩-记忆日报汇总 · 2026-11", "month": "2026-11"}]})
        run = Docs(counts={"NOV": mp.SAFE_BLOCKS - 100})
        self.assertEqual(mp.write("2026-11-16", run=run), "written")
        self.assertEqual(run.created, [("NEW1", "常浩-记忆日报汇总 · 2026-11（续 2）")])
        create = [a for a, _ in run.calls if a[:2] == ("docs", "+create")][0]
        self.assertEqual(create[create.index("--parent-token") + 1], "FOLDER")
        self.assertEqual(self.updates(run), ["NEW1"])
        deliv = json.loads((mp.DIR / "2026-11-16.delivery.json").read_text(encoding="utf-8"))
        self.assertEqual(deliv["opened"]["reason"], "full")
        self.assertIn("快到飞书单文档 4 万块上限", mp.push_text("2026-11-16", "NEW1", good_draft(), {}, deliv))

    def test_feishu_limit_error_opens_continuation_and_writes_once_more(self):
        md.save_registry({"folder": "", "docs": [md.legacy(), {"token": "NOV", "title": "常浩-记忆日报汇总 · 2026-11", "month": "2026-11"}]})
        run = Docs(counts={"NOV": 100}, limit_on={"NOV"})
        self.assertEqual(mp.write("2026-11-16", run=run), "written")
        self.assertEqual(self.updates(run), ["NOV", "NEW1"])
        self.assertIn("2026-11-16", run.docs["NEW1"]["xml"])

    def test_explicit_doc_reports_limit_in_plain_words(self):
        run = Docs(limit_on={"SAMPLE"})
        with self.assertRaises(mp.GenError) as e:
            mp.write("2026-11-16", "SAMPLE", run=run)
        self.assertIn("4 万块上限", str(e.exception))
        self.assertEqual(run.created, [])

    def test_continuation_limit_reports_chinese_error_and_stops_after_one_retry(self):
        md.save_registry({"folder": "", "docs": [md.legacy(), {"token": "NOV", "title": "t", "month": "2026-11"}]})
        run = Docs(counts={"NOV": 100}, limit_on={"NOV", "NEW1"})
        with self.assertRaisesRegex(mp.GenError, "续篇.*4 万块上限.*没写进去.*不再重试"):
            mp.write("2026-11-16", run=run)
        self.assertEqual(self.updates(run), ["NOV", "NEW1"])
        self.assertEqual(len(run.created), 1)
        self.assertFalse(mp.paths("2026-11-16")["delivery.json"].exists())

    def test_existing_day_in_nearly_full_month_does_not_open_continuation(self):
        md.save_registry({"folder": "", "docs": [md.legacy(), {"token": "NOV", "title": "t", "month": "2026-11"}]})
        old_xml = "<h2>📅 2026-11-16（周一）</h2><p>已写过</p>"
        run = Docs(docs={"NOV": {"md": "", "xml": old_xml}}, counts={"NOV": 35999})
        self.assertEqual(mp.write("2026-11-16", run=run), "exists")
        self.assertEqual((run.created, self.updates(run)), ([], []))
        self.assertEqual(run.docs["NOV"]["xml"], old_xml)

    def test_incomplete_block_count_stops_before_creating_or_writing(self):
        md.save_registry({"folder": "", "docs": [md.legacy(), {"token": "NOV", "title": "t", "month": "2026-11"}]})
        run = Docs()

        def incomplete(*args, **kw):
            if args[:2] == ("api", "GET"):
                return {"items": [{}], "has_more": True}
            return run(*args, **kw)

        with self.assertRaisesRegex(mp.GenError, "块数.*没读全"):
            mp.write("2026-11-16", run=incomplete)
        self.assertEqual((run.created, self.updates(run)), ([], []))
        self.assertFalse(mp.paths("2026-11-16")["delivery.json"].exists())

    def test_block_count_reads_all_pages_before_opening_continuation(self):
        md.save_registry({"folder": "", "docs": [md.legacy(), {"token": "NOV", "title": "t", "month": "2026-11"}]})
        run, pages = Docs(), []

        def paged(*args, **kw):
            if args[:2] == ("api", "GET"):
                params = json.loads(args[args.index("--params") + 1])
                if params["page_size"] == 500:
                    page = int(params.get("page_token", "0"))
                    pages.append(page)
                    return {"items": [{}] * 500, "has_more": page < 71, "page_token": str(page + 1)}
            return run(*args, **kw)

        self.assertEqual(mp.write("2026-11-16", run=paged), "written")
        self.assertEqual(pages, list(range(72)))  # 36,000 块，不能只数第一页的 500 块
        self.assertEqual(self.updates(run), ["NEW1"])

    def test_invalid_block_pages_never_allow_a_write(self):
        md.save_registry({"folder": "", "docs": [md.legacy(), {"token": "NOV", "title": "t", "month": "2026-11"}]})
        for page in (None, {}, {"items": [], "has_more": "false"},
                     {"items": {}, "has_more": False},
                     {"items": [], "has_more": True, "page_token": "same"}):
            with self.subTest(page=page):
                run = Docs()

                def broken(*args, **kw):
                    if args[:2] == ("api", "GET"):
                        return page
                    return run(*args, **kw)

                with self.assertRaises(mp.GenError):
                    mp.write("2026-11-16", run=broken)
                self.assertEqual((run.created, self.updates(run)), ([], []))

    def test_existing_day_in_earlier_continuation_is_not_written_again(self):
        md.save_registry({"folder": "", "docs": [md.legacy(),
            {"token": "NOV", "title": "t", "month": "2026-11"},
            {"token": "NOV2", "title": "t2", "month": "2026-11"}]})
        run = Docs(docs={"NOV": {"md": "", "xml": "<h2>📅 2026-11-16（周一）</h2>"}})
        self.assertEqual(mp.write("2026-11-16", run=run), "exists")
        self.assertEqual((run.created, self.updates(run)), ([], []))

    def test_month_changes_follow_report_date_and_keep_previous_month_untouched(self):
        run = Docs()
        self.assertEqual(mp.write("2026-11-16", run=run), "written")
        november_xml = run.docs["NEW1"]["xml"]
        mp.build("2026-12-01", collect=lambda d: dict(facts(), work_lines=["定价"], date=d.isoformat(), title=f"📅 {md.wd(d)}"),
                 gen=lambda t, w: (json.dumps(good_draft(), ensure_ascii=False), "s", "m"))
        self.assertEqual(mp.write("2026-12-01", run=run), "written")
        self.assertEqual(run.created, [("NEW1", "常浩-记忆日报汇总 · 2026-11"), ("NEW2", "常浩-记忆日报汇总 · 2026-12")])
        self.assertEqual(run.docs["NEW1"]["xml"], november_xml)
        self.assertTrue(run.docs["NEW2"]["xml"].startswith("<h2>📅 2026-12-01（周二）</h2>"))
        self.assertEqual([(x["token"], x["month"]) for x in md.load_registry()["docs"]],
                         [(md.DOC, None), ("NEW1", "2026-11"), ("NEW2", "2026-12")])
        self.assertEqual(mp.write("2026-11-16", run=run), "exists")
        self.assertEqual(self.updates(run), ["NEW1", "NEW2"])

    def test_collect_reads_month_doc_then_falls_back_to_legacy(self):
        md.save_registry({"folder": "", "docs": [md.legacy(), {"token": "NOV", "title": "t", "month": "2026-11"},
                                                 {"token": "DEC", "title": "t", "month": "2026-12"}]})
        run = Docs(docs={"NOV": {"md": "", "xml": ""}, md.DOC: {"md": OLD, "xml": ""}})
        f = md.collect(D, run=run, config=CONFIG)
        self.assertEqual(f["prev"]["date"], "2026-11-08")                  # 新文档还空着，前一篇从旧文档读
        fetched = [a[a.index("--doc") + 1] for a, _ in run.calls if a[:2] == ("docs", "+fetch")]
        self.assertEqual(fetched, ["NOV", md.DOC])                         # 不读以后月份的文档
        run = Docs(docs={"NOV": {"md": "## 📅 2026-11-10（周二）\n", "xml": ""}})
        self.assertTrue(md.collect(D, run=run, config=CONFIG)["skip"])

    def test_estimate_matches_feishu_block_count(self):
        # 11-11、11-12、11-13 三篇实测：单元格 + 段落 + 标题 + 表格，正好等于列块接口数出的块数
        xml = "<h2>t</h2><h3>a</h3><table><thead><tr><th><p>x</p></th></tr></thead><tbody><tr><td><p>y</p><p>z</p></td></tr></tbody></table>"
        self.assertEqual(mp.estimate_blocks(xml), 2 + 1 + 2 + 3)


if __name__ == "__main__":
    unittest.main()
