"""记忆日报（亚古兽）：memdaily_data / memdaily_publish。全部用假的 run，不联网、不碰真实文档和数据目录。"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memdaily_data as md  # noqa: E402
import memdaily_publish as mp  # noqa: E402

ME = md.ME
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
            return {"messages": self.search}
        if cmd == "contact +get-user":
            return {"user": {} if args[3] == "ou_bot" else {"name": "杭添"}}
        if cmd == "im +chat-messages-list":
            return {"messages": self.white}
        if cmd in ("vc +search", "minutes +search"):
            return {"items": []}
        if cmd == "calendar +agenda":
            return [{"summary": "舀水群每周同频共识会", "start_time": {"datetime": "2026-11-10T18:00:00+08:00"},
                     "end_time": {"datetime": "2026-11-10T19:00:00+08:00"}}]
        if cmd == "task +get-related-tasks":
            return {"items": []}
        if cmd == "docs +update":
            content = Path(kw["cwd"], args[args.index("--content") + 1][1:]).read_text(encoding="utf-8")
            self.doc_xml = "<title>记忆日报</title>" + content + self.doc_xml.replace("<title>记忆日报</title>", "")
            return {}
        if cmd == "api GET":
            return {"items": [{"block_type": 31, "table": {"property": {"column_width": self.widths}}}]}
        if cmd == "im +messages-send":
            return {}
        raise AssertionError(args)


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


if __name__ == "__main__":
    unittest.main()
