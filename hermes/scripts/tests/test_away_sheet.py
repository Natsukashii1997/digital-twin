# -*- coding: utf-8 -*-
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import away_sheet as A  # noqa: E402
from test_aihot_hot import FAKE  # noqa: E402  假 lark-cli：按 replies.json 回话，调用记进 calls.jsonl

SCRIPT = SCRIPTS / "away_sheet.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "away_sheet"
CHAT = "oc_testchat000001"
T0 = "2026-11-21T12:00:00+08:00"
NOW = dt.datetime.fromisoformat("2026-11-21T15:50:00+08:00")
ASK = {"task": "磁盘瘦身 · 第三批整改", "what": "删 2 个用完的工作目录", "about": "留着只占地方。",
       "progress": "核对过可以安全删。", "todo": "打开「磁盘瘦身」会话，回「删掉」"}


def blank(rows=()):
    return {"version": 1, "id": "2026-11-21-1200", "by": "Claude 会话「测试」", "started_at": T0, "ended_at": None,
            "message_id": None, "pushed_at": None, "reminders": [], "rows": list(rows), "doing": []}


def done(hhmm, text="装好了。", project="磁盘瘦身"):
    return {"kind": "done", "at": f"2026-11-21T{hhmm}:00+08:00", "project": project, "text": text}


def texts(card):
    """卡片里所有 markdown 和纯文本的内容，按出现先后。"""
    out = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("tag") in ("markdown", "plain_text"):
                out.append(o["content"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(card)
    return out


class Card(unittest.TestCase):
    def test_settled_card_is_reproduced_word_for_word(self):
        # 常浩 2026-11-21 定稿的那张单子（乙一 · 对齐留白）：同样的内容，脚本排出来的卡片要和基线逐字一样
        sheet = json.loads((FIXTURES / "sheet.json").read_text(encoding="utf-8"))
        want = json.loads((FIXTURES / "card.json").read_text(encoding="utf-8"))
        self.assertEqual(A.build_card(sheet, NOW), want)

    def test_numbers_follow_arrival_and_never_change(self):
        sheet = blank()
        first = A.add_ask(sheet, dict(ASK, task="牛马之家 · 界面修复"), "2026-11-21T12:02:00+08:00")
        second = A.add_ask(sheet, ASK, "2026-11-21T12:35:00+08:00")
        self.assertEqual((first, second), (1, 2))
        A.resolve(sheet, 1, "等你回来再发（你 13:05 回的）", "2026-11-21T13:05:00+08:00")
        third = A.add_ask(sheet, dict(ASK, task="数字分身 · 合并官"), "2026-11-21T15:45:00+08:00")
        self.assertEqual(third, 3)                      # 办完的不让出编号
        card = A.build_card(sheet, NOW)
        seen = texts(card)
        self.assertIn("托管流水｜还等你 2 件", seen)
        self.assertIn("<font color='grey'>✅ ① **牛马之家 · 界面修复**｜等你回来再发（你 13:05 回的）</font>", seen)
        self.assertIn("**② 磁盘瘦身 · 第三批整改｜删 2 个用完的工作目录**", seen)
        self.assertIn("**③ 数字分身 · 合并官｜删 2 个用完的工作目录**", seen)
        self.assertEqual(card["header"]["template"], "red")

    def test_open_ask_is_four_lines_without_labels_and_red_last(self):
        sheet = blank()
        A.add_ask(sheet, ASK, "2026-11-21T12:35:00+08:00")
        row = A.build_card(sheet, NOW)["body"]["elements"][3]
        lines = [e["content"] for e in row["columns"][1]["elements"]]
        self.assertEqual(lines, ["**① 磁盘瘦身 · 第三批整改｜删 2 个用完的工作目录**", "留着只占地方。",
                                 "<font color='grey'>核对过可以安全删。</font>",
                                 "<font color='red'>**要你做：打开「磁盘瘦身」会话，回「删掉」**</font>"])
        self.assertEqual(row["columns"][0]["width"], "48px")       # 时间栏定宽，不用 weight
        self.assertEqual(row["margin"], "8px 0px 8px 0px")

    def test_nothing_waiting_turns_green(self):
        sheet = blank([done("12:17")])
        card = A.build_card(sheet, NOW)
        self.assertEqual((card["header"]["title"]["content"], card["header"]["template"]), ("托管流水｜现在没有等你的事", "green"))
        self.assertNotIn("还等你", "".join(texts(card)[2:]))

    def test_ask_needs_all_five_parts(self):
        with self.assertRaises(A.Fail) as e:
            A.add_ask(blank(), dict(ASK, about="  "), T0)
        self.assertIn("about", str(e.exception))

    def test_resolved_ask_cannot_be_edited_or_resolved_twice(self):
        sheet = blank()
        A.add_ask(sheet, ASK, T0)
        A.update_ask(sheet, 1, {"progress": "又核对了一遍。"})
        self.assertEqual(sheet["rows"][0]["progress"], "又核对了一遍。")
        A.resolve(sheet, 1, "已删掉", T0)
        for call in (lambda: A.resolve(sheet, 1, "再来一次", T0), lambda: A.update_ask(sheet, 1, {"todo": "改"})):
            with self.assertRaises(A.Fail):
                call()

    def test_long_sheet_folds_oldest_done_rows_and_keeps_asks_open(self):
        # 飞书卡片正文最多 199 个组件（11-21 实测）：装不下时把最早办完的收成一行折叠，要他做的永远摊开
        rows = [done("12:%02d" % i, f"第 {i} 件办完了。") for i in range(1, 41)]
        sheet = blank(rows)
        A.add_ask(sheet, ASK, "2026-11-21T12:05:00+08:00")
        A.add_ask(sheet, dict(ASK, task="数字分身 · 合并官"), "2026-11-21T13:00:00+08:00")
        card = A.build_card(sheet, NOW)
        self.assertLessEqual(A.tags(card["body"]), A.MAX_TAGS)
        panels = [e for e in card["body"]["elements"] if e.get("tag") == "collapsible_panel"]
        self.assertEqual(len(panels), 1)
        folded = panels[0]["elements"][0]["content"].count("\n") + 1
        self.assertIn(f"办完 {folded} 件（点开看）", panels[0]["header"]["title"]["content"])
        self.assertTrue(panels[0]["header"]["title"]["content"].startswith("✅ 12:01–"))
        seen = texts(card)
        self.assertIn("**① 磁盘瘦身 · 第三批整改｜删 2 个用完的工作目录**", seen)
        self.assertIn("**② 数字分身 · 合并官｜删 2 个用完的工作目录**", seen)
        shown = sum(1 for s in seen if s.startswith("<font color='grey'>✅ **磁盘瘦身**"))
        self.assertEqual(shown + folded, 40)             # 一件不丢：要么摊着，要么在折叠里
        self.assertIn("第 40 件办完了。", "".join(seen[seen.index(panels[0]["elements"][0]["content"]) + 1:]))   # 最新的还摊着

    def test_too_many_open_asks_says_so_instead_of_sending_a_broken_card(self):
        sheet = blank()
        for i in range(30):
            A.add_ask(sheet, ASK, T0)
        with self.assertRaises(A.Fail) as e:
            A.build_card(sheet, NOW)
        self.assertIn("装不下", str(e.exception))

    def test_ended_sheet_says_it_no_longer_listens(self):
        sheet = blank([done("12:17")])
        A.add_ask(sheet, ASK, "2026-11-21T12:35:00+08:00")
        sheet["doing"] = [{"task": "数字分身 · 秘书代批", "about": "", "text": "在审。"}]
        sheet["ended_at"] = "2026-11-21T16:45:00+08:00"
        card = A.build_card(sheet, NOW)
        seen = texts(card)
        self.assertEqual((card["header"]["title"]["content"], card["header"]["template"]), ("托管结束｜还等你 1 件", "grey"))
        self.assertEqual(card["header"]["subtitle"]["content"], "11-21 12:00 开始｜16:45 结束")
        self.assertIn("■ 托管结束", seen)
        self.assertIn(f"<font color='grey'>{A.HOW_ENDED}</font>", seen)
        self.assertFalse(any("正在办" in s or s == f"<font color='grey'>{A.HOW}</font>" for s in seen))

    def test_new_day_gets_a_date_line(self):
        sheet = blank([done("23:50"), {"kind": "done", "at": "2026-11-22T00:10:00+08:00", "project": "磁盘瘦身", "text": "又一件。"}])
        seen = texts(A.build_card(sheet, NOW))
        self.assertLess(seen.index("<font color='grey'>—— 11-22 ——</font>"), seen.index("<font color='grey'>✅ **磁盘瘦身**｜又一件。</font>"))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-away-"))
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        (self.fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (self.fake / "lark-cli").chmod(0o755)
        self.dir = self.tmp / "away"
        self.reply("托管流水｜还等你 1 件")

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def reply(self, title, listed=()):
        got = {"ok": True, "data": {"messages": [{"msg_type": "interactive", "content": f"<card title=\"{title}\">…</card>"}]}}
        (self.fake / "replies.json").write_text(json.dumps({
            "+messages-send": {"ok": True, "data": {"message_id": "om_sheet_1"}}, "+messages-mget": got,
            "im messages patch": {"ok": True, "data": {}}, "+messages-reply": {"ok": True, "data": {"message_id": "om_remind_1"}},
            "+chat-messages-list": {"ok": True, "data": {"messages": list(listed)}}}, ensure_ascii=False), encoding="utf-8")

    def run_script(self, *args, now="2026-11-21T12:40:00+08:00", chat=CHAT):
        env = {k: v for k, v in os.environ.items() if k != "FEISHU_HOME_CHANNEL"}
        env.update(DT_AWAY_DIR=str(self.dir), DT_LARK_CLI=str(self.fake / "lark-cli"), FAKE_DIR=str(self.fake),
                   DT_NOW=now, HERMES_HOME="/x", DT_HERMES_ENV=str(self.tmp / "hermes.env"))
        if chat:
            env["FEISHU_HOME_CHANNEL"] = chat
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def ok(self, *args, **kw):
        r = self.run_script(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def calls(self, word):
        p = self.fake / "calls.jsonl"
        calls = [json.loads(line)["args"] for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return [a for a in calls if word in " ".join(a)]

    def ask(self, **kw):
        a = dict(ASK, **kw)
        return self.ok("ask", *[x for k, v in a.items() for x in ("--" + k, v)])

    def open_sheet(self):
        self.assertEqual(self.ok("start", "--by", "Claude 会话「测试」", "--at", "12:00"), "2026-11-21-1200")
        self.assertEqual(self.ask(), "1")

    def sheet(self):
        return json.loads((self.dir / "2026-11-21-1200" / "sheet.json").read_text(encoding="utf-8"))

    def test_one_card_is_sent_once_then_updated_in_place(self):
        self.open_sheet()
        self.ok("push")
        self.ok("done", "--project", "磁盘瘦身", "--text", "第三批整改装好了。")
        self.ok("push")
        sent = self.calls("+messages-send")
        self.assertEqual(len(sent), 1)                                   # 只有一张单子
        self.assertEqual(sent[0][sent[0].index("--chat-id") + 1], CHAT)
        self.assertEqual(sent[0][sent[0].index("--msg-type") + 1], "interactive")
        patched = self.calls("messages patch")
        self.assertEqual(len(patched), 1)
        self.assertEqual(patched[0][patched[0].index("--message-id") + 1], "om_sheet_1")
        body = json.loads(json.loads(patched[0][patched[0].index("--data") + 1])["content"])
        self.assertIn("<font color='grey'>✅ **磁盘瘦身**｜第三批整改装好了。</font>", texts(body))
        self.assertEqual(self.calls("+messages-reply"), [])              # 没有新冒出要他做的事，不另发提醒
        runs = [json.loads(x) for x in (self.dir / "2026-11-21-1200" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["result"] for r in runs], ["sent", "patched"])

    def test_new_ask_after_the_card_exists_gets_one_reminder(self):
        self.open_sheet()
        self.ok("push")                                                   # 第一件随卡片一起到，不另提醒
        self.assertEqual(self.ask(task="数字分身 · 合并官", what="定时巡视什么时候恢复"), "2")
        self.reply("托管流水｜还等你 2 件")
        self.ok("push")
        self.ok("push")                                                   # 再推一次不重复提醒
        reminders = self.calls("+messages-reply")
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0][reminders[0].index("--message-id") + 1], "om_sheet_1")   # 回复在单子下面，能跳回去
        text = reminders[0][reminders[0].index("--text") + 1]
        self.assertIn("托管单多了 1 件要你做的：② 数字分身 · 合并官｜定时巡视什么时候恢复", text)
        self.assertEqual(self.sheet()["reminders"][0]["asks"], [2])

    def test_resolve_updates_the_same_card(self):
        self.open_sheet()
        self.ok("push")
        self.ok("resolve", "1", "--result", "已删掉（你 12:38 回的）")
        self.reply("托管流水｜现在没有等你的事")
        self.ok("push")
        patch = self.calls("messages patch")[0]
        body = json.loads(json.loads(patch[patch.index("--data") + 1])["content"])
        self.assertEqual(body["header"]["title"]["content"], "托管流水｜现在没有等你的事")
        self.assertIn("<font color='grey'>✅ ① **磁盘瘦身 · 第三批整改**｜已删掉（你 12:38 回的）</font>", texts(body))

    def test_replies_are_only_his_messages_quoting_this_sheet(self):
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.reply("托管流水｜还等你 2 件")
        self.ok("push")                                                   # 提醒的消息号 om_remind_1 也算这张单子的

        def msg(mid, who, text, quote=None):
            m = {"message_id": mid, "create_time": "2026-11-21 12:50", "content": text, "sender": {"sender_type": who, "name": "x"}}
            if quote:
                m["reply_to"] = quote
            return m
        self.reply("托管流水｜还等你 2 件", listed=[
            msg("om_a", "user", "1 删掉", "om_sheet_1"),          # 他引用单子回的
            msg("om_b", "user", "2 开了再恢复", "om_remind_1"),    # 他引用提醒回的
            msg("om_c", "user", "按推荐"),                         # 没引用：是回猛鬼兽别的事的
            msg("om_d", "user", "合并", "om_other_card"),          # 引用的是别的卡片
            msg("om_e", "app", "托管单多了 1 件", "om_sheet_1")])  # 机器人自己发的提醒
        got = json.loads(self.ok("replies"))
        self.assertEqual([(m["message_id"], m["text"]) for m in got], [("om_a", "1 删掉"), ("om_b", "2 开了再恢复")])

    def test_readback_mismatch_is_reported(self):
        self.open_sheet()
        self.reply("别的卡片")
        r = self.run_script("push")
        self.assertEqual(r.returncode, 1)
        self.assertIn("回读对不上", r.stdout)
        runs = (self.dir / "2026-11-21-1200" / "runs.jsonl").read_text(encoding="utf-8")
        self.assertIn("sent_unverified", runs)
        self.assertEqual(self.sheet()["message_id"], "om_sheet_1")        # 发过就记下，下次是更新不是再发一张

    def test_no_private_chat_no_send(self):
        self.open_sheet()
        r = self.run_script("push", chat=None)
        self.assertEqual(r.returncode, 1)
        self.assertIn("没有私聊 chat_id", r.stdout)
        self.assertEqual(self.calls("+messages-send"), [])

    def test_ended_sheet_refuses_changes(self):
        self.open_sheet()
        self.ok("end", "--at", "16:45")
        r = self.run_script("done", "--project", "磁盘瘦身", "--text", "又一件。")
        self.assertEqual(r.returncode, 1)
        self.assertIn("已经结束", r.stdout)
        self.assertIn("托管结束", self.ok("build"))

    def test_inner_jargon_in_title_gets_a_warning(self):
        self.ok("start", "--by", "Claude 会话「测试」", "--at", "12:00")
        r = self.run_script("ask", *[x for k, v in dict(ASK, what="PR #300 合不合").items() for x in ("--" + k, v)])
        self.assertEqual(r.returncode, 0)
        self.assertIn("换成人话", r.stderr)

    def test_doing_replaces_the_whole_list(self):
        self.open_sheet()
        self.ok("doing", "--item", "数字分身 · 秘书代批｜你不在时替你批小事的那套｜在审。", "--item", "数字分身 · 0D 恢复门｜｜在修。")
        seen = texts(json.loads(self.ok("build")))
        self.assertIn("**⏳ 正在办，不用你管**\n- **数字分身 · 秘书代批**（你不在时替你批小事的那套）：在审。\n- **数字分身 · 0D 恢复门**：在修。", seen)
        self.ok("doing")
        self.assertFalse(any("正在办" in s for s in texts(json.loads(self.ok("build")))))


if __name__ == "__main__":
    unittest.main()
