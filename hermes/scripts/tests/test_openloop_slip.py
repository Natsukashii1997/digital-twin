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
import openloop as ol  # noqa: E402
import openloop_slip as sl  # noqa: E402
from test_aihot_hot import FAKE  # noqa: E402  假 lark-cli：按 replies.json 回话，调用记进 calls.jsonl

SCRIPT = SCRIPTS / "openloop_slip.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "openloop_slip"
CHAT = "oc_testchat000001"
EVENING = "2026-11-20T21:00:00+08:00"
NOON = "2026-11-21T12:30:00+08:00"
REPOS = {"dt": "o/dt", "skills": "o/skills"}


def said(word):
    return ol.SAID.format(word) + "：做到一半"


def row(id_, what, next_, state="等你", source="session", evidence=None, at="2026-11-20T10:00:00+08:00", project="dt"):
    return {"id": id_, "what": what, "state": state, "source": source, "owner": "常浩", "project": project, "next": next_,
            "evidence": evidence or said("等你操作"), "evidence_at": at, "links": [], "stalled": False}


def ledger(rows, at="2026-11-20T20:55:00+08:00"):
    return {"version": 1, "generated_at": at, "rows": rows, "parked": [], "mismatches": [], "unparsed": [], "counts": {}, "coverage": {}}


MERGE = row("pr:dt#7", "加一个开关", ol.MERGE_NEXT, source="pr", evidence="11-20 10:00 最近一次提交，检查全过")
CARD = row("session:local_a", "【交接】收尾", "你点下方交接卡开始新会话", evidence=said("等你操作"), at="2026-11-18T09:00:00+08:00")
HANDS = row("session:local_b", "机器人权限", "审批通过后你回复「已开通」", at="2026-11-07T09:00:00+08:00", project="home")
PR_HANDS = row("pr:skills#81", "月度策略", "你打开 report.md 看哪里不像", source="pr", project="skills", at="2026-11-10T09:00:00+08:00")
DECIDE = row("session:local_c", "选方案", "回「按推荐」", evidence=said("等你决定"))
ASK = row("mainline:dt/底座/选哪条路", "选哪条路", "常浩定：选哪条路", source="mainline", evidence="11-20 09:00 CURRENT 这一项最近一次改动")
GONE = row("session:local_d", "旧事", "你点一下", evidence=said("等你操作") + ol.GONE_NOTE, at="2026-11-01T09:00:00+08:00")
BUSY = row("session:local_e", "在跑", "等测试", state="在做")
ALL = [HANDS, DECIDE, MERGE, BUSY, GONE, PR_HANDS, CARD, ASK]


def at(text):
    return dt.datetime.fromisoformat(text)


def table_of(card):
    return next(e for e in card["body"]["elements"] if e["tag"] == "table")


def panels(card):
    return [e["header"]["title"]["content"] for e in card["body"]["elements"] if e["tag"] == "collapsible_panel"]


class Pick(unittest.TestCase):
    def test_what_each_row_asks_of_him(self):
        self.assertEqual([sl.kind(r) for r in (MERGE, CARD, HANDS, PR_HANDS, DECIDE, ASK)], ["合并", "点卡", "亲手做", "亲手做", "拍板", "拍板"])

    def test_only_hands_on_rows_go_on_the_slip(self):
        todo, decide, gone = sl.pick(ledger(ALL))
        self.assertEqual([r["id"] for r in todo], ["pr:dt#7", "session:local_a", "session:local_b", "pr:skills#81"])   # 合并 → 点卡 → 亲手做，同类里挂得久的在前
        self.assertEqual(sorted(r["id"] for r in decide), ["mainline:dt/底座/选哪条路", "session:local_c"])
        self.assertEqual([r["id"] for r in gone], ["session:local_d"])


class Card(unittest.TestCase):
    def test_version_b_is_one_table_top_to_bottom(self):
        card = sl.build_card(ledger(ALL), at(EVENING), REPOS)
        self.assertEqual(card["header"]["title"]["content"], "🧾 晚间单 · 11-20")
        self.assertEqual(card["header"]["subtitle"]["content"], "要你亲手做 4 件｜一张表从上往下做")
        self.assertEqual(card["header"]["template"], "indigo")
        table = table_of(card)
        self.assertEqual([(c["display_name"], c["width"]) for c in table["columns"]], [("#", "8%"), ("哪件事", "37%"), ("你要做什么", "42%"), ("已挂", "13%")])
        self.assertEqual([r["n"] for r in table["rows"]], ["1", "2", "3", "4"])
        self.assertEqual(table["rows"][0]["what"], "[dt#7](https://github.com/o/dt/pull/7) 加一个开关")
        self.assertEqual(table["rows"][0]["do"], "**合并**：常浩决定合并")
        self.assertEqual([r["age"] for r in table["rows"]], ["今天", "2 天", "13 天", "10 天"])
        self.assertEqual(table["rows"][2]["what"], "home｜机器人权限")
        self.assertEqual(panels(card), ["可能已过期的 1 件 · 展开"])
        foot = card["body"]["elements"][-1]["content"]
        self.assertIn("另有 2 件等你拍板（不在这张单上）｜1 件可能已过期（它的 PR 已合并或关闭）｜账本 20:55 扫描", foot)
        self.assertNotIn("在跑", json.dumps(card, ensure_ascii=False))

    def test_noon_slot_and_nothing_to_do(self):
        self.assertEqual(sl.build_card(ledger([MERGE], at="2026-11-21T12:25:00+08:00"), at(NOON), REPOS)["header"]["title"]["content"], "🧾 午间单 · 11-21")
        self.assertIsNone(sl.build_card(ledger([DECIDE, GONE, BUSY]), at(EVENING), REPOS))

    def test_at_most_seven_rows_the_rest_fold(self):
        many = [row(f"pr:dt#{i}", f"第 {i} 个", ol.MERGE_NEXT, source="pr", at=f"2026-11-{i:02d}T09:00:00+08:00") for i in range(1, 11)]
        card = sl.build_card(ledger(many), at(EVENING), REPOS)
        self.assertEqual(len(table_of(card)["rows"]), sl.MAX_ROWS)
        self.assertEqual(card["header"]["subtitle"]["content"], "要你亲手做 10 件｜一张表从上往下做")
        self.assertEqual(panels(card), ["其余 3 件 · 展开"])
        rest = next(e for e in card["body"]["elements"] if e["tag"] == "collapsible_panel")["elements"][0]["content"]
        self.assertIn("dt#10", rest)

    def test_old_ledger_says_so_on_the_card(self):
        fresh = sl.build_card(ledger([MERGE]), at(EVENING), REPOS)
        self.assertFalse(any("没更新" in json.dumps(e, ensure_ascii=False) for e in fresh["body"]["elements"]))
        old = sl.build_card(ledger([MERGE], at="2026-11-20T12:20:00+08:00"), at(EVENING), REPOS)
        self.assertIn("账本 11-20 12:20 之后没更新", old["body"]["elements"][0]["content"])

    def test_the_card_he_picked_stays_the_same(self):
        """常浩 2026-11-20 选定的乙版：样例账本出的卡片和登记的基线逐字相同。改版式先走「改动确认」，再更新基线。"""
        led = json.loads((FIXTURES / "ledger.json").read_text(encoding="utf-8"))
        want = json.loads((FIXTURES / "card.json").read_text(encoding="utf-8"))
        self.assertEqual(sl.build_card(led, at(EVENING), REPOS), want)


class Send(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-slip-"))
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        (self.fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (self.fake / "lark-cli").chmod(0o755)
        self.book = self.tmp / "openloop"
        self.book.mkdir()
        self.roster = self.tmp / "projects.json"
        self.roster.write_text(json.dumps({"projects": [{"id": "dt", "github": "o/dt", "category": "活跃", "path": str(self.tmp)}]}), encoding="utf-8")
        self.reply("🧾 晚间单 · 11-20")
        self.write([MERGE, HANDS])

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def write(self, rows, at="2026-11-20T20:55:00+08:00"):
        (self.book / "ledger.json").write_text(json.dumps(ledger(rows, at), ensure_ascii=False), encoding="utf-8")

    def reply(self, title, send=True):
        got = {"ok": True, "data": {"messages": [{"msg_type": "interactive", "content": f"<card>{title} …</card>"}]}}
        sent = {"ok": True, "data": {"message_id": "om_slip_1"}} if send else {"ok": False, "error": {"message": "发不出去"}}
        (self.fake / "replies.json").write_text(json.dumps({"+messages-send": sent, "+messages-mget": got}, ensure_ascii=False), encoding="utf-8")

    def run_script(self, *args, now=EVENING, chat=CHAT):
        env = {k: v for k, v in os.environ.items() if k != "FEISHU_HOME_CHANNEL"}
        env.update(DT_OPENLOOP_DIR=str(self.book), DT_ROSTER=str(self.roster), DT_LARK_CLI=str(self.fake / "lark-cli"), FAKE_DIR=str(self.fake),
                   DT_NOW=now, HERMES_HOME="/x", DT_HERMES_ENV=str(self.tmp / "hermes.env"))
        if chat:
            env["FEISHU_HOME_CHANNEL"] = chat
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def sends(self):
        p = self.fake / "calls.jsonl"
        calls = [json.loads(line)["args"] for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return [a for a in calls if "+messages-send" in a]

    def runs(self):
        return [json.loads(line) for line in (self.book / "slips" / "runs.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_sends_once_per_slot_to_his_private_chat(self):
        r = self.run_script("send")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""), r.stderr)   # 成功不输出
        (a,) = self.sends()
        self.assertEqual((a[a.index("--chat-id") + 1], a[a.index("--as") + 1], a[a.index("--msg-type") + 1]), (CHAT, "bot", "interactive"))
        card = json.loads(a[a.index("--content") + 1])
        self.assertEqual(len(table_of(card)["rows"]), 2)
        self.assertEqual(json.loads((self.book / "slips" / "2026-11-20-晚.card.json").read_text(encoding="utf-8")), card)
        self.assertEqual(self.run_script("send").returncode, 0)
        self.assertEqual(len(self.sends()), 1)                                    # 同一档只发一次
        self.reply("🧾 午间单 · 11-21")
        self.assertEqual(self.run_script("send", now=NOON).returncode, 0)
        self.assertEqual(len(self.sends()), 2)                                    # 第二天中午是另一档
        self.assertEqual([x["result"] for x in self.runs()], ["sent", "already", "sent"])

    def test_nothing_to_do_sends_nothing(self):
        self.write([DECIDE, GONE])
        r = self.run_script("send")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, ""))
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.runs()[-1]["result"], "nothing")
        self.write([MERGE])                                                       # 同一档后来有事了：还能发
        self.assertEqual(self.run_script("send").returncode, 0)
        self.assertEqual(len(self.sends()), 1)

    def test_failures_say_why_and_exit_1(self):
        (self.book / "ledger.json").unlink()
        r = self.run_script("send")
        self.assertEqual(r.returncode, 1)
        self.assertIn("还没有账本", r.stdout)
        self.write([MERGE])
        r = self.run_script("send", chat=None)
        self.assertEqual(r.returncode, 1)
        self.assertIn("没有私聊 chat_id", r.stdout)
        self.reply("x", send=False)
        r = self.run_script("send")
        self.assertEqual(r.returncode, 1)
        self.assertIn("发不出去", r.stdout)
        self.reply("对不上的标题")
        r = self.run_script("send")
        self.assertEqual(r.returncode, 1)
        self.assertIn("回读对不上", r.stdout)
        self.assertEqual(self.run_script("send").returncode, 0)                   # 已经发出去的那张不重发
        self.assertEqual(len(self.sends()), 2)

    def test_build_prints_the_card_without_sending(self):
        r = self.run_script("build")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["header"]["title"]["content"], "🧾 晚间单 · 11-20")
        self.assertEqual(self.sends(), [])
        self.assertFalse((self.book / "slips").exists())


if __name__ == "__main__":
    unittest.main()
