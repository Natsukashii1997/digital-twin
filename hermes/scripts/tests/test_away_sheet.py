# -*- coding: utf-8 -*-
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import datetime as dt
import io
import json
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import away_sheet as A  # noqa: E402

SCRIPT = SCRIPTS / "away_sheet.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "away_sheet"
CHAT = "oc_testchat000001"
OWNER = "ou_owner000001"
T0 = "2026-11-21T12:00:00+08:00"
NOW = dt.datetime.fromisoformat("2026-11-21T15:50:00+08:00")
ASK = {"task": "磁盘瘦身 · 第三批整改", "what": "删 2 个用完的工作目录", "about": "留着只占地方。",
       "progress": "核对过可以安全删。", "todo": "打开「磁盘瘦身」会话，回「删掉」"}


def blank(rows=()):
    return {"version": 1, "id": "2026-11-21-1200", "by": "Claude 会话「测试」", "started_at": T0, "ended_at": None,
            "message_id": None, "pushed_at": None, "rev": 1, "reminders": [], "rows": list(rows), "doing": []}


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

    def test_hhmm_later_than_now_means_yesterday(self):
        now = dt.datetime.fromisoformat("2026-11-22T00:05:00+08:00")
        self.assertEqual(A.when("23:58", now), "2026-11-21T23:58:00+08:00")
        self.assertEqual(A.when("00:03", now), "2026-11-22T00:03:00+08:00")
        self.assertEqual(A.when(None, now), "2026-11-22T00:05:00+08:00")
        for bad in ("25:00", "12:60", "下午三点"):
            with self.assertRaises(A.Fail):
                A.when(bad, now)

    def test_ask_cannot_be_backdated_before_an_existing_number(self):
        # 编号按先后发、流水按时间排：补记一件更早的事会让 ② 排到 ① 前面，常浩指出过序号要清楚
        sheet = blank()
        A.add_ask(sheet, ASK, "2026-11-21T12:35:00+08:00")
        with self.assertRaises(A.Fail) as e:
            A.add_ask(sheet, ASK, "2026-11-21T12:10:00+08:00")
        self.assertIn("不能排到已有编号的前面", str(e.exception))
        self.assertEqual(A.add_ask(sheet, ASK, "2026-11-21T12:35:30+08:00"), 2)

    def test_inner_jargon_is_spotted_next_to_chinese(self):
        for text in ("合并PR吗", "看issue", "PR #300 合不合", "修 #12"):
            self.assertTrue(A.INNER.search(text), text)
        for text in ("PRD 改不改", "发不发布到正式版", "SPRING 版"):
            self.assertFalse(A.INNER.search(text), text)

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


# 假飞书：记着这个会话里的消息（chat.json），回读、列消息给的就是发上去的内容；ctl.json 里按命令名注入故障：
#   "fail" 明确失败、没发出去；"lost" 发出去了但不给消息号；"ignored"（patch）说成功但没改；"empty"（mget）什么都读不到。
FAKE = r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
root = Path(__file__).resolve().parent
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "env": sorted(os.environ)}, ensure_ascii=False) + "\n")
ctl = json.loads((root / "ctl.json").read_text(encoding="utf-8"))
store = root / "chat.json"
msgs = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
cmd = args[3] if args[3].startswith("+") else " ".join(args[2:5])
mode = ctl.get(cmd, "ok")


def opt(name):
    return args[args.index(name) + 1]


def out(data=None, error=None):
    print(json.dumps({"ok": False, "error": {"message": error}} if error else {"ok": True, "data": data or {}}, ensure_ascii=False))
    sys.exit(0)


def keep():
    store.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")


def render(content):
    card, texts = json.loads(content), []

    def walk(o):
        if isinstance(o, dict):
            if o.get("tag") in ("markdown", "plain_text"):
                texts.append(o["content"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(card["body"])
    h = card["header"]
    body = re.sub(r"</?font[^>]*>", "", "\n".join(texts))          # 真飞书回读给的是去掉颜色标签的文字（11-22 实测）
    return '<card title="%s" subtitle="%s">\n%s\n</card>' % (h["title"]["content"], h["subtitle"]["content"], body)


if cmd.startswith("api GET /open-apis/im/v1/messages/"):          # 原始接口：更新时间到毫秒
    m = [x for x in msgs if x["message_id"] == cmd.rsplit("/", 1)[1]]
    if not m:
        out(error="fake: 没有这条消息")
    out({"items": [{"message_id": m[0]["message_id"], "deleted": m[0]["deleted"], "updated": m[0]["updated"],
                    "update_time": str(m[0].get("update_ms", 1795000000000))}]})
if mode == "fail":
    out(error="fake: 不通")
if cmd == "im chats get":
    out({"chat_mode": ctl.get("chat_mode", "p2p")})
if cmd in ("+messages-send", "+messages-reply"):
    key = opt("--idempotency-key")
    old = [m for m in msgs if m.get("key") == key] if ctl.get("dedup", True) else []
    if old:
        m = old[0]
    else:
        if cmd == "+messages-send":
            m = {"message_id": "om_sheet_%d" % (1 + sum(x["msg_type"] == "interactive" for x in msgs)),
                 "chat_id": opt("--chat-id"), "msg_type": "interactive", "content": render(opt("--content"))}
        else:
            parent = [x for x in msgs if x["message_id"] == opt("--message-id")][0]
            m = {"message_id": "om_remind_%d" % (1 + sum(x["message_id"].startswith("om_remind") for x in msgs)),
                 "chat_id": parent["chat_id"], "msg_type": "text", "content": opt("--text"), "reply_to": parent["message_id"]}
        m.update(sender={"sender_type": "app", "id_type": "app_id", "id": "cli_bot"}, key=key,
                 create_time="2026-11-21 12:40", deleted=False, updated=False)
        msgs.append(m)
        keep()
    out({} if mode == "lost" else {"message_id": m["message_id"]})
if cmd == "im messages patch":
    m = [x for x in msgs if x["message_id"] == opt("--message-id")][0]
    if mode != "ignored":
        m.update(content=render(json.loads(opt("--data"))["content"]), updated=True, update_time="2026-11-21 12:41")
        keep()
    out({})
if cmd == "+messages-mget":
    ids = opt("--message-ids").split(",")
    out({"messages": [] if mode == "empty" else [m for m in msgs if m["message_id"] in ids]})
if cmd == "+chat-messages-list":
    size, i = ctl.get("page_size", 50), int(opt("--page-token")) if "--page-token" in args else 0
    if ctl.get("endless"):
        out({"messages": [], "has_more": True, "page_token": str(i + 1)})
    mine = [m for m in msgs if m["chat_id"] == opt("--chat-id")]
    out({"messages": mine[i:i + size], "has_more": i + size < len(mine), "page_token": str(i + size)})
out(error="fake: 没有这条回话 " + cmd)
'''
# 身份文件、账本目录、lark-cli 的位置、现在几点在脚本里都不认环境变量，测试只能在同一个进程里把它们指到临时目录
RUN = ("import sys; sys.path.insert(0, sys.argv[1]); import away_sheet as A; from pathlib import Path; "
       "A.OWNER_DIR = Path(sys.argv[2]); A.AWAY_DIR = Path(sys.argv[3]); A.LARK = sys.argv[4]; A.NOW = sys.argv[5]; "
       "A.PAGE_MAX = int(sys.argv[6]); sys.exit(A.main(sys.argv[7:]))")
NOON = "2026-11-21T12:40:00+08:00"
LATER = "2026-11-21T12:45:00+08:00"          # 离 12:40 那次发送已过 120 秒
REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)


SHOW_ENV = r'''#!/bin/sh
printf '{"ok": true, "data": {"env": "%s", "home": "%s", "path": "%s"}}\n' "$(env | cut -d= -f1 | sort | tr '\n' ' ')" "$HOME" "$PATH"
'''


class Fixed(unittest.TestCase):
    """账本、时钟、lark-cli 都不听环境变量的（L3 审查 P1-03、P1-08）。"""

    def test_ledger_and_clock_ignore_the_environment(self):
        want = REAL_HOME / "Library" / "Application Support" / "DigitalTwin"
        with mock.patch.dict(os.environ, {"DT_AWAY_DIR": "/x/away", "HOME": "/y", "DT_NOW": "2000-01-01T00:00:00+08:00"}):
            self.assertEqual((A.OWNER_DIR, A.base()), (want, want / "away"))
            self.assertNotEqual(A.current().year, 2000)

    def test_lark_cli_comes_from_the_protected_file_and_gets_a_bare_environment(self):
        tmp = Path(tempfile.mkdtemp(prefix="dt-away-cli-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)], check=False)
        good, loose = tmp / "bin" / "lark-cli", tmp / "loose" / "lark-cli"
        for f in (good, loose):
            f.parent.mkdir()
            f.write_text(SHOW_ENV, encoding="utf-8")
            f.chmod(0o755)
        loose.chmod(0o775)                                               # 同组的人写得了
        with mock.patch.object(A, "OWNER_DIR", tmp), mock.patch.object(A, "LARK", None), \
                mock.patch.dict(os.environ, {"DT_LARK_CLI": str(loose), "DT_SECRET": "x", "PATH": f"{loose.parent}:/usr/bin:/bin", "HOME": "/y"}):
            with self.assertRaises(A.Fail) as e:
                A.lark(["im", "x"], "试")
            self.assertIn("没配置lark-cli 的位置", str(e.exception))        # 环境变量和 PATH 里的那个不算
            (tmp / "lark_cli").write_text(str(loose) + "\n", encoding="utf-8")
            (tmp / "lark_cli").chmod(0o600)
            with self.assertRaises(A.Fail) as e:
                A.lark(["im", "x"], "试")
            self.assertIn("别人写不了", str(e.exception))
            (tmp / "lark_cli").write_text(str(good) + "\n", encoding="utf-8")
            got = A.lark(["im", "x"], "试")
        self.assertEqual(got["home"], str(REAL_HOME))                    # 它读哪套配置也不由调用方的 HOME 定
        self.assertEqual(got["path"], f"{good.parent}:/usr/bin:/bin:/usr/sbin:/sbin")
        self.assertNotIn("DT_SECRET", got["env"].split())
        self.assertNotIn("DT_LARK_CLI", got["env"].split())


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-away-"))
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        (self.fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (self.fake / "lark-cli").chmod(0o755)
        self.dir = self.tmp / "away"
        self.who = self.tmp / "who"
        self.who.mkdir()
        for name, value in (("owner_open_id", OWNER), ("owner_chat_id", CHAT)):
            (self.who / name).write_text(value + "\n", encoding="utf-8")
            (self.who / name).chmod(0o600)
        self.ctl()
        self.page_max = 200

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def ctl(self, **knobs):
        (self.fake / "ctl.json").write_text(json.dumps(knobs, ensure_ascii=False), encoding="utf-8")

    def chat(self):
        p = self.fake / "chat.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def put(self, msgs):
        (self.fake / "chat.json").write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")

    def say(self, mid, text, quote="om_sheet_1", who="user", sender=OWNER, id_type="open_id", msg_type="text", **more):
        m = {"message_id": mid, "chat_id": CHAT, "msg_type": msg_type, "content": text, "create_time": "2026-11-21 12:50",
             "deleted": False, "updated": False, "sender": {"sender_type": who, "id_type": id_type, "id": sender, "name": "x"}}
        if quote:
            m["reply_to"] = quote
        m.update(more)
        self.put(self.chat() + [m])

    def change(self, mid, **fields):
        msgs = self.chat()
        [m for m in msgs if m["message_id"] == mid][0].update(fields)
        self.put(msgs)

    def cmd(self, *args, now=NOON):
        return [sys.executable, "-B", "-c", RUN, str(SCRIPTS), str(self.who), str(self.dir), str(self.fake / "lark-cli"), now,
                str(self.page_max), *args]

    def run_script(self, *args, now=NOON, **env):
        return subprocess.run(self.cmd(*args, now=now), capture_output=True, text=True, env=dict(os.environ, **env), timeout=120)

    def ok(self, *args, **kw):
        r = self.run_script(*args, **kw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout.strip()

    def bad(self, word, *args, **kw):
        r = self.run_script(*args, **kw)
        self.assertEqual((r.returncode, r.stderr), (1, ""), r.stdout)      # 一句话，不抛栈
        self.assertIn(word, r.stdout)
        return r.stdout

    def calls(self, word):
        return [c["args"] for c in self.logged() if word in " ".join(c["args"])]

    def logged(self):
        p = self.fake / "calls.jsonl"
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def ask(self, **kw):
        a = dict(ASK, **kw)
        return self.ok("ask", *[x for k, v in a.items() for x in ("--" + k, v)])

    def open_sheet(self):
        self.assertEqual(self.ok("start", "--by", "Claude 会话「测试」", "--at", "12:00"), "2026-11-21-1200")
        self.assertEqual(self.ask(), "1")

    def sheet(self):
        return json.loads((self.dir / "2026-11-21-1200" / "sheet.json").read_text(encoding="utf-8"))

    def runs(self):
        lines = (self.dir / "2026-11-21-1200" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(x) for x in lines]

    def next_sheet(self):
        return self.run_script("start", "--by", "Claude 会话「测试」", now="2026-11-21T13:00:00+08:00")

    # ---------- 一张单子、原地更新、提醒 ----------

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
        self.assertIn("｜单号 2026-11-21-1200 · 第 2 版</font>", texts(body)[-1])    # 每推一次版本加一
        self.assertEqual(self.calls("+messages-reply"), [])              # 没有新冒出要他做的事，不另发提醒
        self.assertEqual([(r["rev"], r["card"], r["remind"], r["readback"], r["result"]) for r in self.runs()],
                         [(1, "sent", "none", "ok", "ok"), (2, "patched", "none", "ok", "ok")])
        self.assertFalse(self.sheet()["dirty"])

    def test_new_ask_after_the_card_exists_gets_one_reminder(self):
        self.open_sheet()
        self.ok("push")                                                   # 第一件随卡片一起到，不另提醒
        self.assertEqual(self.ask(task="数字分身 · 合并官", what="定时巡视什么时候恢复"), "2")
        self.ok("push")
        self.ok("push")                                                   # 再推一次不重复提醒
        reminders = self.calls("+messages-reply")
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0][reminders[0].index("--message-id") + 1], "om_sheet_1")   # 回复在单子下面，能跳回去
        text = reminders[0][reminders[0].index("--text") + 1]
        self.assertIn("托管单多了 1 件要你做的：② 数字分身 · 合并官｜定时巡视什么时候恢复", text)
        batch = self.sheet()["reminders"][0]
        self.assertEqual((batch["batch"], batch["asks"], batch["message_id"]), (1, [2], "om_remind_1"))
        self.assertEqual(self.runs()[1]["remind"], "sent")

    def test_resolve_updates_the_same_card(self):
        self.open_sheet()
        self.ok("push")
        self.ok("resolve", "1", "--result", "已删掉（你 12:38 回的）")
        self.ok("push")
        patch = self.calls("messages patch")[0]
        body = json.loads(json.loads(patch[patch.index("--data") + 1])["content"])
        self.assertEqual(body["header"]["title"]["content"], "托管流水｜现在没有等你的事")
        self.assertIn("<font color='grey'>✅ ① **磁盘瘦身 · 第三批整改**｜已删掉（你 12:38 回的）</font>", texts(body))

    def test_ask_added_right_before_end_still_gets_a_reminder(self):
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ok("end", "--at", "12:40")
        self.ok("push")
        self.assertEqual(len(self.calls("+messages-reply")), 1)           # 原地更新不弹通知，这件事他否则看不到

    def test_failed_update_does_not_swallow_the_reminder(self):
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ctl(**{"im messages patch": "fail"})
        self.bad("更新托管单失败", "push")
        self.assertEqual(self.calls("+messages-reply"), [])
        self.assertFalse(self.sheet()["rows"][1]["notified"])
        self.assertEqual((self.runs()[-1]["card"], self.runs()[-1]["result"]), ("failed", "failed"))
        self.ctl()
        self.ok("push")                                                   # 下次推上去了，提醒补发
        self.assertEqual(len(self.calls("+messages-reply")), 1)

    # ---------- 回读（P1-01、P2-01） ----------

    def test_readback_mismatch_is_reported(self):
        self.open_sheet()
        self.ctl(**{"+messages-mget": "empty"})
        self.bad("回读对不上", "push")
        run = self.runs()[-1]
        self.assertEqual((run["card"], run["readback"], run["result"]), ("sent", "mismatch", "partial"))
        self.assertEqual(self.sheet()["message_id"], "om_sheet_1")        # 发过就记下，下次是更新不是再发一张
        self.assertTrue(self.sheet()["dirty"])

    def test_update_that_did_not_land_is_caught_even_within_the_same_minute(self):
        # L3 审查 P2-01：同一分钟里推第二次，标题、副标题都没变；更新其实没生效，回读还是上一版，要认得出来
        self.open_sheet()
        self.ok("push")
        self.ok("done", "--project", "磁盘瘦身", "--text", "装好了。")
        self.ctl(**{"im messages patch": "ignored"})
        self.bad("回读对不上", "push")                                     # 飞书上还是「第 1 版」
        self.assertTrue(self.sheet()["dirty"])
        self.ctl()
        self.ok("push")
        self.assertIn("单号 2026-11-21-1200 · 第 3 版", self.chat()[0]["content"])
        self.assertFalse(self.sheet()["dirty"])

    def test_new_sheet_waits_until_the_old_one_is_ended_and_pushed(self):
        self.open_sheet()
        self.ok("push")
        r = self.next_sheet()
        self.assertEqual(r.returncode, 1)
        self.assertIn("还没结束", r.stdout)
        self.ok("end", "--at", "12:40")
        r = self.next_sheet()
        self.assertEqual(r.returncode, 1)
        self.assertIn("结束状态还没推", r.stdout)                          # 不然旧卡永远显示「托管流水」
        self.ok("push")
        self.assertEqual(self.next_sheet().stdout.strip(), "2026-11-21-1300")

    def test_unconfirmed_end_does_not_unlock_the_next_sheet(self):
        # L3 审查 P1-01：结束状态改上去了但回读没对上（读回来还是旧卡片），不能当成已推到飞书、放行下一张
        self.open_sheet()
        self.ok("push")
        self.ok("end", "--at", "12:40")
        self.ctl(**{"im messages patch": "ignored"})                      # 回读到的还是没结束的那一版
        self.bad("回读对不上", "push")
        self.assertTrue(self.sheet()["dirty"])
        r = self.next_sheet()
        self.assertEqual(r.returncode, 1)
        self.assertIn("结束状态还没推", r.stdout)
        self.assertIn("有改动还没推", self.ok("show"))
        self.ctl()
        self.ok("push")                                                   # 重推、这次回读对上了
        self.assertFalse(self.sheet()["dirty"])
        self.assertEqual(self.next_sheet().stdout.strip(), "2026-11-21-1300")

    def test_marker_typed_into_the_text_does_not_fool_the_readback(self):
        # L3 审查 P2-01：托管会话填的文字里出现「单号 … · 第 3 版」，更新又没生效；回读只认卡片最后一行
        self.open_sheet()
        self.ok("push")
        self.ok("done", "--project", "磁盘瘦身", "--text", "正文里写着｜单号 2026-11-21-1200 · 第 3 版")
        self.ok("push")                                                   # 第 2 版，这行字已经在卡片上了
        self.ok("done", "--project", "磁盘瘦身", "--text", "又一件。")
        self.ctl(**{"im messages patch": "ignored"})
        self.bad("回读对不上", "push")                                     # 第 3 版没上去：最后一行还是「第 2 版」
        self.assertTrue(self.sheet()["dirty"])

    def test_reminder_waits_until_the_card_is_confirmed(self):
        # L3 审查 P2-09：更新说成功但其实没上去，这时提醒不能先发（他点回卡片看不到新加的事）
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ctl(**{"im messages patch": "ignored"})
        self.bad("回读对不上", "push")
        self.assertEqual(self.calls("+messages-reply"), [])
        self.assertEqual((self.runs()[-1]["remind"], self.sheet()["rows"][1]["notified"], self.sheet()["reminders"]), ("held", False, []))
        self.ctl()
        self.ok("push")
        self.assertEqual(len(self.calls("+messages-reply")), 1)

    # ---------- 开下一张的门（P1-07） ----------

    def test_state_that_cannot_be_read_is_not_treated_as_no_sheet(self):
        self.open_sheet()
        self.ok("push")
        self.ok("end", "--at", "12:40")
        self.ok("push")                                                   # 上一张干干净净结束了，本来可以开下一张
        state, sheet = self.dir / "state.json", self.dir / "2026-11-21-1200" / "sheet.json"
        good_state, good_sheet = state.read_text(encoding="utf-8"), sheet.read_text(encoding="utf-8")
        for path, broken, word in ((state, "{", "读不了或者坏了"), (state, '{"current": "../x"}', "单号不对"), (state, "[]", "读不了或者坏了"),
                                   (sheet, "{", "读不了"), (sheet, good_sheet.replace('"version": 1', '"version": 9'), "不是这个脚本写的格式")):
            path.write_text(broken, encoding="utf-8")
            r = self.next_sheet()
            self.assertEqual((r.returncode, r.stderr), (1, ""), broken)
            self.assertIn(word, r.stdout)
            path.write_text(good_state if path == state else good_sheet, encoding="utf-8")
        state.unlink()                                                    # 指针没了，可以前的单子还在
        r = self.next_sheet()
        self.assertEqual(r.returncode, 1)
        self.assertIn("state.json 不见了", r.stdout)
        self.assertEqual(sorted(d.name for d in self.dir.iterdir() if d.is_dir()), ["2026-11-21-1200"])   # 一张都没多开
        state.write_text(good_state, encoding="utf-8")
        self.assertEqual(self.next_sheet().stdout.strip(), "2026-11-21-1300")

    def test_ended_sheet_that_never_reached_him_keeps_the_next_one_closed(self):
        # L3 审查第 4 轮 P1-07：第一次 push 就没发成（私聊没配好，连「要发」都没记下），接着 end：
        # 消息号是空的、也没有结果不明，可这张单子他一次都没收到，不能悄悄丢下去开下一张
        self.open_sheet()
        chat_file = self.who / "owner_chat_id"
        chat_file.unlink()
        self.bad("没配置", "push")
        s = self.sheet()
        self.assertEqual((s["message_id"], s["sending"], s["dirty"]), (None, None, True))
        self.ok("end", "--at", "12:40")
        r = self.next_sheet()
        self.assertEqual((r.returncode, r.stderr), (1, ""))
        self.assertIn("一次都没发到飞书，先 push", r.stdout)
        self.assertEqual(sorted(d.name for d in self.dir.iterdir() if d.is_dir()), ["2026-11-21-1200"])
        chat_file.write_text(CHAT + "\n", encoding="utf-8")
        chat_file.chmod(0o600)
        self.ok("push")                                                   # 出路：配好了再 push，他收到的是结束状态
        self.assertIn("托管结束｜还等你 1 件", self.chat()[0]["content"])
        self.assertEqual(self.next_sheet().stdout.strip(), "2026-11-21-1300")
        self.ok("end", now="2026-11-21T13:10:00+08:00")                   # 第二张开了就结束，什么都没记：同样要先 push
        r = self.run_script("start", "--by", "Claude 会话「测试」", now="2026-11-21T14:00:00+08:00")
        self.assertEqual(r.returncode, 1)
        self.assertIn("托管单 2026-11-21-1300 结束了，可是一次都没发到飞书", r.stdout)
        self.assertEqual(len(self.calls("+messages-send")), 1)

    def ended_clean(self):
        """一张干干净净结束、本来可以开下一张的单子（两件要他做的事、一批提醒、一条正在办、一句记过的回话）。
        返回 sheet.json 的路径、内容，和一个检查：把文件改成这样，开下一张（every=True 时别的命令也）必须拒绝、不抛栈。"""
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ok("doing", "--item", "磁盘瘦身 · 第三批｜清工作目录｜在核对")
        self.ok("push", now=LATER)
        self.say("om_a", "① 删掉")
        version = json.loads(self.ok("replies"))[0]["version"]
        self.ok("ack", "om_a", "--version", version, "--result", "已删掉")
        self.ok("end", now="2026-11-21T12:50:00+08:00")
        self.ok("push", now="2026-11-21T12:50:00+08:00")
        path = self.dir / "2026-11-21-1200" / "sheet.json"
        good = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(good), sorted(list(A.SHAPE) + ["version"]))   # 脚本自己写出来的正好是这些字段
        self.assertEqual((good["dirty"], good["sending"], len(good["reminders"]), list(good["acks"])), (False, None, 1, ["om_a"]))

        def refused(broken, word, every=False):
            path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
            more = [self.run_script(c, now=LATER) for c in ("show", "push", "replies", "build")] if every else []
            for r in [self.next_sheet()] + more:
                self.assertEqual((r.returncode, r.stderr), (1, ""), word)
                self.assertIn("缺字段或者不对", r.stdout)
                self.assertIn(f"（{word}" if word in A.SHAPE else word, r.stdout)
            self.assertEqual(sorted(d.name for d in self.dir.iterdir() if d.is_dir()), ["2026-11-21-1200"])   # 一张都没多开

        return path, good, refused

    def test_sheet_with_a_missing_or_wrong_field_is_not_treated_as_clean(self):
        # L3 审查第 4 轮 P1-07：sheet.json 是合法 JSON、version 也对，但少了 dirty / sending 这类字段：
        # 以前补成「没改动」「没在发」就放行了。缺了、类型不对、单号和目录对不上，都拒绝，不补默认值
        path, good, refused = self.ended_clean()
        for key in A.SHAPE:                                               # 每个字段缺了都不行
            refused({k: v for k, v in good.items() if k != key}, key, every=key in ("dirty", "sending"))
        for key, value in (("dirty", None), ("dirty", 0), ("rev", True), ("rev", "3"), ("sending", False), ("reminders", {}),
                           ("message_id", 7), ("started_at", "中午"), ("ended_at", "12:50"), ("acks", [])):
            refused(dict(good, **{key: value}), key)
        refused(dict(good, id="2026-11-21-1201"), "和所在目录的单号不一样")
        sending = {"first": NOON, "last": NOON, "asks": [1], "tries": 1}
        refused(dict(good, sending=sending), "已经有消息号了，还记着要发")
        refused(dict(good, message_id=None, sending={"first": NOON}), "sending.last")
        refused(dict(good, message_id=None, sending=dict(sending, last="刚才")), "sending.last")
        refused(dict(good, chat_id=None), "却没记发到哪个会话")
        first, ask = good["rows"][0], dict(good["rows"][0])
        del ask["notified"]
        refused(dict(good, rows=[ask] + good["rows"][1:]), "rows[0].notified")
        refused(dict(good, rows=[dict(first, kind="别的")]), "rows[0].kind")
        refused(dict(good, rows=[dict(first, at="刚才")]), "rows[0].at")
        refused(dict(good, rows=["一行字"]), "rows[0].（不是一条记录）")
        refused(dict(good, reminders=[{k: v for k, v in good["reminders"][0].items() if k != "message_id"}]), "reminders[0].message_id")
        refused(dict(good, doing=[{"task": "x"}]), "doing[0].about")
        path.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.next_sheet().stdout.strip(), "2026-11-21-1300")

    def test_state_whose_values_do_not_add_up_is_refused(self):
        # L3 审查第 5 轮 P1-07 问题 3、P2-11：字段都在、类型也对，可这些值合在一起不是脚本会写出来的状态
        # （写着已推到却没有消息号、提醒标成「不用发」可事还没办完、办完时间是一串乱字……）。
        # 开单的门、认领、对账、读回话都会把这些值当真，所以读单子时就拒绝，所有命令一句话报错、不抛栈
        path, good, refused = self.ended_clean()

        def row(i, **change):
            return dict(good, rows=[dict(r, **change) if j == i else r for j, r in enumerate(good["rows"])])

        def batch(**change):
            return dict(good, reminders=[dict(good["reminders"][0], **change)])

        def ack(**change):
            return dict(good, acks={"om_a": dict(good["acks"]["om_a"], **change)})

        r = self.run_script("show")                                        # 对照：没动过的时候读得了
        self.assertEqual(r.returncode, 0, r.stdout)
        path.write_text(json.dumps(dict(good, version=True), ensure_ascii=False), encoding="utf-8")
        self.assertIn("不是这个脚本写的格式", self.next_sheet().stdout)      # True == 1，也不算第 1 版
        # 「已推到飞书」必须真有消息号、推送时间、版本号
        refused(dict(good, message_id=None, reminders=[], rows=[dict(x, notified=False) for x in good["rows"]]),
                "写着已经推到飞书", every=True)
        refused(dict(good, pushed_at=None), "写着已经推到飞书")
        refused(dict(good, rev=0), "写着已经推到飞书")
        for bad_id in ("", "abc", "om_"):
            refused(dict(good, message_id=bad_id), "message_id")
        refused(dict(good, chat_id="x"), "chat_id")
        refused(dict(good, pushed_at="刚才"), "pushed_at")
        refused(dict(good, rev=-1), "rev")
        # 提醒：「不用发」只能是 true / false，而且那批的事得真的都办完了；消息号、编号、时间都得是正经的值
        refused(batch(message_id=None, dropped="false"), "reminders[0].dropped", every=True)
        done = row(1, done_at="2026-11-21T12:46:00+08:00", result="办了")       # 那批的事真办完了，"false" 也不是正经的标记
        refused(dict(done, reminders=[dict(good["reminders"][0], message_id=None, dropped="false")]), "reminders[0].dropped")
        refused(batch(message_id=None, dropped=True), "这批的事还没办完，不能标成不用发")
        refused(batch(dropped=True), "发到了又标成不用发")
        for bad_id in ("sent", ""):
            refused(batch(message_id=bad_id), "reminders[0].message_id")
        for bad_asks in ([True], [], ["2"], [0]):
            refused(batch(asks=bad_asks), "reminders[0].asks")
        refused(batch(asks=[9]), "没有第 9 件")
        refused(batch(tried="刚才"), "reminders[0].tried", every=True)
        refused(batch(also="om_x"), "reminders[0].also", every=True)
        refused(batch(also=["x"]), "reminders[0].also")
        refused(batch(message_id=None, also=["om_x"]), "却记着重复的那几句")
        refused(batch(batch=5), "批次号和先后对不上")
        refused(row(1, notified=False), "这批的事没标成提醒过")
        # 要他做的事：办完时间得是时间，和结果要么都有要么都没有；编号是正整数、不重复
        refused(row(0, done_at="not-a-time"), "rows[0].done_at", every=True)
        refused(row(0, done_at="not-a-time", result="办了"), "rows[0].done_at")   # 有结果也不行：随便一串字不能把事算成办完
        refused(row(0, done_at="2026-11-21T12:45:00+08:00"), "办完时间和结果要么都有")
        refused(row(0, result="已删掉"), "办完时间和结果要么都有")
        refused(row(0, n=True), "rows[0].n")
        refused(row(0, n=0), "rows[0].n")
        refused(row(1, n=1), "编号重了")
        # 发送意图里的编号
        send = {"first": NOON, "last": NOON, "asks": [1], "tries": 1}
        unsent = dict(good, message_id=None, dirty=True, pushed_at=None, reminders=[], acks={},
                      rows=[dict(x, notified=False) for x in good["rows"]])
        refused(dict(unsent, sending=dict(send, asks=[True])), "sending.asks")
        refused(dict(unsent, sending=dict(send, asks=[7])), "记着的编号单子上没有")
        refused(dict(unsent, sending=dict(send, tries=0)), "sending.tries")
        refused(dict(unsent, rows=[dict(good["rows"][0], notified=True)]), "单子还没发出去，却写着提醒过了")
        refused(dict(unsent, reminders=good["reminders"]), "单子还没发出去，却有提醒")
        # 回话的处理记录（P2-11）：每条都得是完整的一条
        refused(dict(good, acks={"om_a": "已办"}), "acks[om_a].（不是一条记录）", every=True)
        refused(dict(good, acks={"om_a": {k: v for k, v in good["acks"]["om_a"].items() if k != "version"}}), "acks[om_a].version", every=True)
        refused(ack(version="abc"), "acks[om_a].version")
        refused(ack(how="yes"), "acks[om_a].how")
        refused(ack(was="不知道"), "acks[om_a].was")
        refused(ack(at="刚才"), "acks[om_a].at")
        refused(ack(result=" "), "acks[om_a].result")
        refused(ack(was="edited"), "照办了的只能是")
        refused(ack(version="missing"), "读不到的那句只能记成 dismiss")
        refused(dict(good, acks={"x": good["acks"]["om_a"]}), "消息号不对")
        path.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.next_sheet().stdout.strip(), "2026-11-21-1300")

    def test_unconfirmed_reminder_keeps_the_next_sheet_closed(self):
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ok("end", "--at", "12:40")
        self.ctl(**{"+messages-reply": "fail"})
        self.bad("第 1 批提醒结果不明", "push")                             # 卡片推到了，提醒没确认发到
        self.assertFalse(self.sheet()["dirty"])
        self.ctl()
        r = self.next_sheet()
        self.assertEqual(r.returncode, 1)
        self.assertIn("还有提醒没确认发到", r.stdout)
        self.ok("push", now=LATER)                                        # 出路：再 push，对账后补发
        self.assertEqual(self.next_sheet().stdout.strip(), "2026-11-21-1300")

    # ---------- 首发结果不明（P1-02） ----------

    def test_lost_first_send_freezes_the_sheet_then_adopts_the_card_already_there(self):
        # 飞书收到了卡片，本机没拿到消息号：不能开下一张，也不能再发一张；下次 push 去飞书认回那一张
        self.open_sheet()
        self.ctl(**{"+messages-send": "lost"})
        self.bad("没拿到消息号", "push")
        s = self.sheet()
        self.assertEqual((s["message_id"], s["sending"]["tries"], s["sending"]["asks"]), (None, 1, [1]))   # 要发，先记下了
        self.assertEqual(self.runs()[-1]["card"], "unknown")
        self.assertIn("结果不明", self.ok("show"))
        self.ask(task="数字分身 · 合并官")                                 # 卡片发出之后才加的
        self.ok("end", "--at", "12:40")
        r = self.next_sheet()
        self.assertEqual(r.returncode, 1)                                 # message_id 是空的也不放行
        self.assertIn("结果不明", r.stdout)
        self.ctl()
        self.ok("push", now=LATER)
        self.assertEqual(len(self.calls("+messages-send")), 1)            # 没有发第二张
        self.assertEqual([m["message_id"] for m in self.chat() if m["msg_type"] == "interactive"], ["om_sheet_1"])
        s = self.sheet()
        self.assertEqual((s["message_id"], s["sending"], s["dirty"]), ("om_sheet_1", None, False))
        self.assertEqual((self.runs()[-1]["adopted"], self.runs()[-1]["card"]), ("om_sheet_1", "patched"))
        self.assertIn("托管结束｜还等你 2 件", self.chat()[0]["content"])   # 认回来之后原地更新成了最新的
        reminders = self.calls("+messages-reply")
        self.assertEqual(len(reminders), 1)                               # ① 随那张卡到了；② 是之后加的，另发一句
        self.assertIn("②", reminders[0][reminders[0].index("--text") + 1])
        self.assertNotIn("①", reminders[0][reminders[0].index("--text") + 1])
        self.assertEqual(self.next_sheet().stdout.strip(), "2026-11-21-1300")

    def test_send_that_never_arrived_is_resent_only_after_checking_and_waiting(self):
        self.open_sheet()
        self.ctl(**{"+messages-send": "fail"})
        self.bad("发托管单失败", "push")
        self.assertIsNotNone(self.sheet()["sending"])
        self.ctl()
        self.bad("秒后再 push", "push", now="2026-11-21T12:41:00+08:00")   # 飞书上没找到，但才过 60 秒：先不重发
        self.assertEqual(len(self.calls("+messages-send")), 1)
        self.ok("push", now=LATER)                                        # 满 120 秒、对过账还是没有：重发
        self.assertEqual(len(self.calls("+messages-send")), 2)
        self.assertEqual(len(self.calls("+chat-messages-list")), 2)       # 两次都先去飞书找过
        self.assertEqual((self.sheet()["message_id"], self.sheet()["sending"]), ("om_sheet_1", None))
        self.assertEqual(len(self.chat()), 1)

    def test_unknown_send_is_not_retried_into_another_chat(self):
        self.open_sheet()
        self.ctl(**{"+messages-send": "fail"})
        self.bad("发托管单失败", "push")
        self.ctl()
        (self.who / "owner_chat_id").write_text("oc_anotherchat0001\n", encoding="utf-8")
        self.bad("和上次发卡片时不一样了", "push", now=LATER)
        self.assertEqual(len(self.calls("+messages-send")), 1)

    def test_two_cards_on_feishu_need_a_human_choice(self):
        self.open_sheet()
        self.ctl(**{"+messages-send": "lost"})
        self.bad("没拿到消息号", "push")
        self.put(self.chat() + [dict(self.chat()[0], message_id="om_sheet_dup")])     # 超过一小时去重期又发出过一张
        self.say("om_note", '<card title="托管结束｜没有等你的事" subtitle="x">\n正文里提到｜单号 2026-11-21-1200 · 第 1 版\n'
                 '单子只有这一张｜单号 2026-11-20-0900 · 第 4 版\n</card>',
                 quote=None, who="app", sender="cli_bot", id_type="app_id", msg_type="interactive")      # 以前那张托管单，只是正文里提到这张的单号
        self.ctl()
        out = self.bad("adopt --message-id", "push", now=LATER)
        self.assertIn("om_sheet_1、om_sheet_dup", out)
        self.assertEqual(len(self.calls("+messages-send")), 1)
        self.bad("不是这张单子的卡片", "adopt", "--message-id", "om_note")
        self.ok("adopt", "--message-id", "om_sheet_1")
        self.assertEqual((self.sheet()["message_id"], self.sheet()["sending"]), ("om_sheet_1", None))
        self.bad("已经有消息号", "adopt", "--message-id", "om_sheet_dup")
        self.ok("push", now=LATER)
        self.assertIn("第 3 版", self.chat()[0]["content"])

    def test_another_card_with_the_same_last_line_is_not_adopted_or_overwritten(self):
        # L3 审查第 4 轮 P2-10：首发结果不明时，私聊里有一张别的工作流的卡片，最后一行和这张单子的页脚一模一样、
        # 标题不是托管单：只看最后一行就会认领它，下一步把它原地改成托管单
        self.open_sheet()
        self.ctl(**{"+messages-send": "fail"})
        self.bad("发托管单失败", "push")                                   # 没发出去，飞书上没有这张单子
        other = ('<card title="GitHub 周榜" subtitle="本周">\n榜单正文\n单子只有这一张｜单号 2026-11-21-1200 · 第 1 版\n</card>')
        self.say("om_other", other, quote=None, who="app", sender="cli_bot", id_type="app_id", msg_type="interactive")
        self.assertTrue(A.is_card({"id": "2026-11-21-1200"}, dict(self.chat()[0], content=other.replace("GitHub 周榜", "托管流水｜还等你 1 件"))))
        self.assertFalse(A.is_card({"id": "2026-11-21-1200"}, self.chat()[0]))
        self.ctl()
        self.bad("不是这张单子的卡片", "adopt", "--message-id", "om_other")   # 人工指定也不认
        self.ok("push", now=LATER)                                        # 对账没找到自己的卡，等够了重发一张新的
        self.assertEqual(len(self.calls("+messages-send")), 2)
        self.assertEqual(self.calls("im messages patch"), [])
        s = self.sheet()
        self.assertEqual((s["message_id"], s["sending"]), ("om_sheet_2", None))
        self.assertNotIn("adopted", self.runs()[-1])
        self.assertEqual(self.chat()[0]["content"], other)                 # 那张别的卡片一个字没动
        self.ok("resolve", "1", "--result", "已删掉", now=LATER)
        self.ok("push", now=LATER)
        self.assertEqual(self.chat()[0]["content"], other)
        self.assertIn("托管流水｜现在没有等你的事", self.chat()[1]["content"])

    def test_adopt_is_only_for_a_send_with_unknown_outcome(self):
        self.open_sheet()
        self.bad("还没发过", "adopt", "--message-id", "om_sheet_1")

    # ---------- 提醒按批记账（P2-02） ----------

    def test_lost_reminder_is_found_again_instead_of_repeated(self):
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ctl(**{"+messages-reply": "lost"})
        out = self.bad("第 1 批提醒结果不明", "push")
        self.assertIn("托管单已更新", out)
        run = self.runs()[-1]
        self.assertEqual((run["card"], run["remind"], run["readback"], run["result"]), ("patched", "unknown", "ok", "partial"))
        self.assertIn("有提醒还没确认发到", self.ok("show"))
        self.ctl()
        self.ask(task="数字分身 · 秘书代批")                               # 又冒出一件
        self.ok("push")
        texts_sent = [a[a.index("--text") + 1] for a in self.calls("+messages-reply")]
        self.assertEqual(len(texts_sent), 2)                              # ② 没有再提醒一遍
        self.assertIn("②", texts_sent[0])
        self.assertTrue("③" in texts_sent[1] and "②" not in texts_sent[1])
        self.assertEqual([(b["batch"], b["asks"], b["message_id"]) for b in self.sheet()["reminders"]],
                         [(1, [2], "om_remind_1"), (2, [3], "om_remind_2")])
        self.assertEqual(self.runs()[-1]["remind"], "sent")               # 一批找回、一批新发：记新发
        self.ok("push")
        self.assertEqual(self.runs()[-1]["remind"], "none")

    def test_duplicate_reminders_are_all_recorded_and_reported(self):
        # 同一批提醒在飞书上不止一句：不悄悄认第一句了事，报出来，两句都记下（引用哪一句回话都认）
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ctl(**{"+messages-reply": "lost"})
        self.bad("第 1 批提醒结果不明", "push")
        self.put(self.chat() + [dict(self.chat()[-1], message_id="om_remind_dup")])
        self.ctl()
        out = self.bad("重复发出过", "push")
        self.assertIn("om_remind_1、om_remind_dup", out)
        batch = self.sheet()["reminders"][0]
        self.assertEqual((batch["message_id"], batch["also"]), ("om_remind_1", ["om_remind_dup"]))
        self.say("om_a", "2 开了再恢复", quote="om_remind_dup")
        self.assertEqual(self.got()["om_a"]["state"], "pending")
        self.ok("push")                                                   # 报过一次，之后照常

    def test_reminder_that_never_arrived_waits_then_goes_out_once(self):
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ctl(**{"+messages-reply": "fail"})
        self.bad("第 1 批提醒结果不明", "push")
        self.ctl()
        self.bad("秒后再 push", "push", now="2026-11-21T12:41:00+08:00")
        self.assertEqual(len(self.chat()), 1)                             # 还只有那张卡片
        self.ok("push", now=LATER)
        self.assertEqual([m["message_id"] for m in self.chat()], ["om_sheet_1", "om_remind_1"])
        self.ok("push", now=LATER)
        self.assertEqual(len(self.chat()), 2)

    def test_reminder_for_something_already_done_is_dropped(self):
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ctl(**{"+messages-reply": "fail"})
        self.bad("第 1 批提醒结果不明", "push")
        self.ctl()
        self.ok("resolve", "2", "--result", "他回来自己办了")
        self.ok("push", now=LATER)
        self.assertEqual(len(self.chat()), 1)
        self.assertTrue(self.sheet()["reminders"][0]["dropped"])
        self.assertEqual(self.runs()[-1]["remind"], "dropped")

    # ---------- 发到哪、认谁（P1-03） ----------

    def two_asks_pushed(self):
        self.open_sheet()
        self.ok("push")
        self.ask(task="数字分身 · 合并官")
        self.ok("push")                                                   # 提醒的消息号 om_remind_1 也算这张单子的

    def talk(self):
        self.say("om_a", "1 删掉")                                                       # 他引用单子回的
        self.say("om_b", "2 开了再恢复", quote="om_remind_1", msg_type="post")            # 他引用提醒回的
        self.say("om_c", "按推荐", quote=None)                                           # 没引用：是回猛鬼兽别的事的
        self.say("om_d", "合并", quote="om_other_card")                                  # 引用的是别的卡片
        self.say("om_e", "机器人的话", who="app", sender="cli_bot", id_type="app_id")      # 机器人发的
        self.say("om_f", "1 留着", sender="ou_someone")                                  # 不是他本人
        self.say("om_g", "1 留着", id_type="user_id")                                    # ID 类型不对
        self.say("om_h", "撤回的", deleted=True)                                          # 他撤回了
        self.say("om_i", "<forwarded>别人说：合并</forwarded>", msg_type="merge_forward")  # 合并转发，里面是别人的话

    def got(self, **kw):
        return {m["message_id"]: m for m in json.loads(self.ok("replies", **kw))}

    def test_replies_are_only_his_own_messages_quoting_this_sheet(self):
        self.two_asks_pushed()
        self.talk()
        got = self.got()
        self.assertEqual({k: (m["state"], m["text"]) for k, m in got.items()},
                         {"om_a": ("pending", "1 删掉"), "om_b": ("pending", "2 开了再恢复"),
                          "om_h": ("recalled", None), "om_i": ("other_type", None)})      # 不是文字的不当指令，内容不给

    def test_identity_cannot_be_swapped_through_the_environment(self):
        # L3 审查 P1-03：调用方用环境变量把「常浩」换成群里另一个人、把私聊换成群
        swapped = {"DT_OWNER_OPEN_ID": "ou_someone", "DT_OWNER_CHAT_ID": "oc_group000000001", "FEISHU_HOME_CHANNEL": "oc_group000000001",
                   "DT_OWNER_FILE": str(self.tmp / "x"), "DT_OWNER_CHAT_FILE": str(self.tmp / "y"), "HOME": str(self.tmp),
                   "DT_LARK_CLI": "/usr/bin/false", "DT_AWAY_DIR": str(self.tmp / "other"), "DT_NOW": "2000-01-01T00:00:00+08:00",
                   "DT_SECRET": "x"}
        self.open_sheet()
        self.ok("push", **swapped)
        self.assertEqual(self.chat()[0]["chat_id"], CHAT)                 # 还是发到身份文件里的私聊
        self.say("om_a", "1 删掉")
        self.say("om_f", "1 留着", sender="ou_someone")
        self.assertEqual(list(self.got(**swapped)), ["om_a"])
        self.assertFalse((self.tmp / "other").exists())                   # 没有另起一套账本
        self.assertEqual(self.sheet()["pushed_at"], NOON)                 # 时间也没被换掉
        for call in self.logged():                                        # 给 lark-cli 的环境是最小的，调用方的变量带不进去
            self.assertFalse([k for k in call["env"] if k.startswith(("DT_", "FEISHU_", "HERMES_"))], call["env"])

    def test_another_environment_cannot_open_a_second_ledger(self):
        # L3 审查 P1-08：换 HOME、DT_AWAY_DIR 再起一个，不能得到另一套账本和锁，开出第二张单子
        self.open_sheet()
        self.ok("push")
        other = {"HOME": str(self.tmp / "home2"), "DT_AWAY_DIR": str(self.tmp / "away2")}
        self.bad("还没结束", "start", "--by", "另一个会话", **other)
        self.assertEqual(len(self.calls("+messages-send")), 1)
        self.assertFalse((self.tmp / "away2").exists() or (self.tmp / "home2").exists())

    def test_a_group_is_never_treated_as_his_private_chat(self):
        self.open_sheet()
        self.ctl(chat_mode="group")
        self.bad("不是单聊", "push")
        self.assertEqual(self.calls("+messages-send"), [])
        self.ctl()
        self.ok("push", now=LATER)
        self.ctl(chat_mode="group")
        self.bad("不是单聊", "replies")
        self.assertEqual(self.calls("+chat-messages-list"), [])

    def test_identity_files_must_be_his_own_plain_600_files(self):
        self.two_asks_pushed()
        chat_file = self.who / "owner_chat_id"
        chat_file.chmod(0o644)
        self.bad("权限 600", "replies")
        chat_file.unlink()
        self.bad("没配置常浩的私聊号", "replies")
        (self.tmp / "elsewhere").write_text(CHAT + "\n", encoding="utf-8")
        (self.tmp / "elsewhere").chmod(0o600)
        chat_file.symlink_to(self.tmp / "elsewhere")
        self.bad("普通文件", "replies")
        chat_file.unlink()
        chat_file.write_text("oc_anotherchat0001\n", encoding="utf-8")
        chat_file.chmod(0o600)
        self.bad("不是发在常浩私聊里的", "replies")                         # 单子发到的会话和现在配置的对不上
        (self.who / "owner_open_id").unlink()
        self.bad("没配置常浩的 open_id", "replies")

    def test_no_private_chat_no_send(self):
        self.open_sheet()
        (self.who / "owner_chat_id").unlink()
        self.bad("没配置常浩的私聊号", "push")
        self.assertEqual(self.calls("+messages-send"), [])
        self.assertIsNone(self.sheet()["sending"])                        # 没到要发那一步，不算结果不明

    # ---------- 读了不算办了；改过、撤回要报（P1-04、P1-05） ----------

    def test_reading_is_not_handling_until_acked(self):
        self.two_asks_pushed()
        self.say("om_a", "1 合并")
        before = (self.dir / "2026-11-21-1200" / "sheet.json").read_bytes()
        first = self.got()["om_a"]
        self.assertEqual((first["state"], first["ack"]), ("pending", None))
        self.assertEqual(self.got()["om_a"]["state"], "pending")          # 会话读完就断了：下次还在
        self.assertEqual((self.dir / "2026-11-21-1200" / "sheet.json").read_bytes(), before)   # replies 只读
        self.bad("不是常浩引用这张单子回的话", "ack", "om_nobody", "--version", first["version"], "--result", "x")
        self.bad("要写一句怎么办的", "ack", "om_a", "--version", first["version"], "--result", " ")
        self.ok("ack", "om_a", "--version", first["version"], "--result", "已合并", now=LATER)
        done = self.got()["om_a"]
        self.assertEqual((done["state"], done["ack"]), ("acked", {"version": first["version"], "at": LATER, "result": "已合并",
                                                                  "how": "ack", "was": "pending"}))
        self.bad("已经记过了", "ack", "om_a", "--version", first["version"], "--result", "再记一次")
        self.assertEqual(self.runs()[-1]["ack"], "om_a")

    def test_someone_elses_message_cannot_be_acked(self):
        self.two_asks_pushed()
        self.say("om_f", "1 留着", sender="ou_someone")
        self.bad("不是常浩引用这张单子回的话", "ack", "om_f", "--version", "x", "--result", "办了")
        self.assertEqual(self.sheet()["acks"], {})

    def test_edit_before_handling_is_not_an_approval(self):
        self.two_asks_pushed()
        self.say("om_a", "1 合并")
        seen = self.got()["om_a"]
        self.change("om_a", content="1 不合并", updated=True, update_time="2026-11-21 12:52", update_ms=1795000100000)
        self.bad("读过之后变了", "ack", "om_a", "--version", seen["version"], "--result", "已合并")   # 办之前他改了
        now = self.got()["om_a"]
        self.assertEqual((now["state"], now["text"]), ("edited", "1 不合并"))                       # 改过的不当授权
        self.assertNotEqual(now["version"], seen["version"])
        # L3 审查 P1-05：拿改过之后的新版本去 ack，也不能把它记成「照办了」
        self.bad("不能记成照办了", "ack", "om_a", "--version", now["version"], "--result", "已合并")
        self.assertEqual(self.sheet()["acks"], {})
        self.ok("dismiss", "om_a", "--version", now["version"], "--result", "改过的不算数，已请他重发一条")
        after = self.got()["om_a"]
        self.assertEqual((after["state"], after["ack"]["how"], after["ack"]["was"]), ("dismissed", "dismiss", "edited"))

    def test_recalled_or_non_text_message_cannot_be_acked_as_done(self):
        self.two_asks_pushed()
        self.say("om_a", "1 合并")
        self.say("om_i", "<forwarded>别人说：合并</forwarded>", msg_type="merge_forward")
        self.change("om_a", deleted=True, content="This message was recalled")
        got = self.got()
        self.assertEqual((got["om_a"]["state"], got["om_i"]["state"]), ("recalled", "other_type"))
        for mid in ("om_a", "om_i"):
            self.bad("不能记成照办了", "ack", mid, "--version", got[mid]["version"], "--result", "已合并")
        self.assertEqual(self.sheet()["acks"], {})
        self.ok("dismiss", "om_a", "--version", got["om_a"]["version"], "--result", "他撤回了，没办")
        self.assertEqual((self.got()["om_a"]["state"], self.got()["om_a"]["ack"]["was"]), ("dismissed", "recalled"))

    def test_edit_and_recall_after_handling_are_reported(self):
        self.two_asks_pushed()
        self.say("om_a", "1 合并")
        v1 = self.got()["om_a"]["version"]
        self.ok("ack", "om_a", "--version", v1, "--result", "已合并")
        self.change("om_a", content="1 不合并", updated=True, update_time="2026-11-21 12:52", update_ms=1795000100000)
        changed = self.got()["om_a"]
        self.assertEqual((changed["state"], changed["text"], changed["ack"]["result"]), ("edited_after_ack", "1 不合并", "已合并"))
        self.bad("不能记成照办了", "ack", "om_a", "--version", changed["version"], "--result", "照新的办了")
        self.ok("dismiss", "om_a", "--version", changed["version"], "--result", "已经合并了，撤不回，告诉他了")
        self.assertEqual((self.got()["om_a"]["state"], self.got()["om_a"]["ack"]["was"]), ("dismissed", "edited_after_ack"))
        self.change("om_a", deleted=True, content="This message was recalled", reply_to=None)   # 撤回后引用关系也可能没了
        recalled = self.got()["om_a"]
        self.assertEqual((recalled["state"], recalled["text"]), ("recalled_after_ack", None))
        self.ok("dismiss", "om_a", "--version", recalled["version"], "--result", "知道了")
        self.assertEqual(self.got()["om_a"]["state"], "dismissed")
        self.put([m for m in self.chat() if m["message_id"] != "om_a"])
        gone = self.got()["om_a"]
        self.assertEqual((gone["state"], gone["version"]), ("missing_after_ack", "missing"))
        self.bad("只有记过、现在读不到的那句", "dismiss", "om_zzz", "--version", "missing", "--result", "x")
        self.bad("只有记过、现在读不到的那句", "ack", "om_a", "--version", "missing", "--result", "x")
        self.ok("dismiss", "om_a", "--version", "missing", "--result", "知道了")
        self.assertEqual(self.got()["om_a"]["state"], "dismissed")

    def test_edit_then_revert_within_the_same_minute_is_still_seen(self):
        # L3 审查 P1-05：办过之后他改了又改回原文，列消息给的更新时间只到分钟，内容也一样；靠原始接口的毫秒值认出来
        self.two_asks_pushed()
        self.say("om_a", "1 合并", updated=True, update_time="2026-11-21 12:52", update_ms=1795000100000)   # 办之前就改过一次
        self.say("om_b", "2 合并")
        got = self.got()
        self.ok("dismiss", "om_a", "--version", got["om_a"]["version"], "--result", "改过的，请他重发")
        self.ok("ack", "om_b", "--version", got["om_b"]["version"], "--result", "已合并")
        self.change("om_a", update_ms=1795000130000)                       # 同一分钟里又改了一次、改回原文：别的字段都没变
        self.change("om_b", updated=True, update_time="2026-11-21 12:50", update_ms=1795000030000)   # 没改过的那句：改了又改回
        again = self.got()
        self.assertEqual((again["om_a"]["state"], again["om_b"]["state"]), ("edited_after_ack", "edited_after_ack"))
        self.assertEqual((again["om_a"]["text"], again["om_b"]["text"]), ("1 合并", "2 合并"))

    # ---------- 翻页（P2-05） ----------

    def test_every_page_is_read(self):
        self.two_asks_pushed()
        for i in range(7):
            self.say(f"om_{i}", f"第 {i} 句")
        self.ctl(page_size=2)
        self.assertEqual(list(self.got()), [f"om_{i}" for i in range(7)])
        self.assertEqual(len(self.calls("+chat-messages-list")), 5)       # 9 条消息、每页 2 条

    def test_too_many_pages_is_an_error_not_a_silent_cut(self):
        self.two_asks_pushed()
        self.page_max = 3
        self.ctl(endless=True)
        self.bad("还没到底", "replies")

    # ---------- 其余 ----------

    def test_full_sheet_refuses_the_extra_item_and_can_still_be_ended(self):
        self.ok("start", "--by", "Claude 会话「测试」", "--at", "12:00")
        n = 0
        while True:
            r = self.run_script("ask", *[x for k, v in ASK.items() for x in ("--" + k, v)])
            if r.returncode:
                break
            n += 1
            self.assertLess(n, 40)
        self.assertIn("装不下", r.stdout)
        self.assertIn("这一条没记进去", r.stdout)
        self.assertEqual(len(self.sheet()["rows"]), n)                    # 没收的那条不占编号、不留在单子里
        self.ok("end", "--at", "12:40")                                   # 报错给的出路走得通
        self.ok("push")
        card = json.loads((self.dir / "2026-11-21-1200" / "card.json").read_text(encoding="utf-8"))
        self.assertLess(A.tags(card["body"]), 199)

    def test_commands_sent_in_parallel_all_land(self):
        self.ok("start", "--by", "Claude 会话「测试」", "--at", "12:00")
        procs = [subprocess.Popen(self.cmd("done", "--project", "磁盘瘦身", "--text", f"第 {i} 件。"),
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(8)]
        self.assertEqual([p.wait(timeout=60) for p in procs], [0] * 8)
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(sorted(r["text"] for r in self.sheet()["rows"]), [f"第 {i} 件。" for i in range(8)])

    def test_bad_input_is_refused_in_one_sentence(self):
        self.open_sheet()
        for args, word in ((("done", "--project", "磁盘瘦身", "--text", " "), "不能空着"),
                           (("note", "--text", ""), "不能空着"),
                           (("done", "--project", "磁盘瘦身", "--text", "x", "--at", "25:00"), "时间看不懂"),
                           (("done", "--project", "磁盘瘦身", "--text", "x", "--at", "11:00"), "比开单"),
                           (("resolve", "9", "--result", "x"), "没有第 9 件"),
                           (("replies",), "还没发")):
            self.bad(word, *args)
        self.assertEqual(len(self.sheet()["rows"]), 1)

    def test_ended_sheet_refuses_changes(self):
        self.open_sheet()
        self.ok("end", "--at", "16:45")
        self.bad("已经结束", "done", "--project", "磁盘瘦身", "--text", "又一件。")
        self.assertIn("托管结束", self.ok("build"))

    def test_build_previews_the_next_version_without_changing_anything(self):
        self.open_sheet()
        self.ok("push")
        self.assertIn("单号 2026-11-21-1200 · 第 2 版", self.ok("build"))
        self.assertEqual(self.sheet()["rev"], 1)

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


class Stop(BaseException):
    """模拟进程在这里被杀掉：不是 Fail、也不是 Exception，脚本里哪个 except 都接不住。"""


class Power(unittest.TestCase):
    """程序断在两次存盘之间（L3 审查第 5 轮 P1-07）。别的测试注入的都是「命令失败」，这里是「前一步成功了、后一步没来得及」：
    在同一个进程里跑脚本，轮到第 N 次写文件之前抛 Stop；N 从 1 试到这条命令写完为止，每个存盘点都断一次。
    假飞书不去重（dedup=False）：断了以后再推，只要重发就真的多出一条，看得出来。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-away-power-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
        self.who = self.tmp / "who"
        self.who.mkdir()
        for name, value in (("owner_open_id", OWNER), ("owner_chat_id", CHAT)):
            (self.who / name).write_text(value + "\n", encoding="utf-8")
            (self.who / name).chmod(0o600)
        self.rounds = 0
        self.fresh()
        for name, value in (("OWNER_DIR", self.who), ("PAGE_MAX", 200)):
            patch = mock.patch.object(A, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        for name in ("AWAY_DIR", "LARK", "NOW"):                          # fresh() 和 at() 里直接改，测完还原
            self.addCleanup(setattr, A, name, getattr(A, name))

    def fresh(self):
        """换一套空的账本和空的假飞书。"""
        self.rounds += 1
        self.fake = self.tmp / f"fake-{self.rounds}"
        self.fake.mkdir()
        (self.fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (self.fake / "lark-cli").chmod(0o755)
        (self.fake / "ctl.json").write_text(json.dumps({"dedup": False}), encoding="utf-8")
        self.dir = self.tmp / f"away-{self.rounds}"
        A.AWAY_DIR, A.LARK = self.dir, str(self.fake / "lark-cli")

    def at(self, hhmm, *args, cut=None):
        """在 hhmm 这个时刻跑一条命令，返回（退出码，输出）。cut=N：轮到它第 N 次写文件之前断掉，断了返回（None, None）。"""
        real, count, out = A.write_json, [0], io.StringIO()

        def write(path, data):
            count[0] += 1
            if count[0] == cut:
                raise Stop()
            real(path, data)

        A.NOW = f"2026-11-21T{hhmm}:00+08:00"
        with mock.patch.object(A, "write_json", write), contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            try:
                return A.main(list(args)), out.getvalue()
            except Stop:
                return None, None

    def ok(self, hhmm, *args):
        code, out = self.at(hhmm, *args)
        self.assertEqual(code, 0, out)
        return out.strip()

    def chat(self):
        p = self.fake / "chat.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def sheets(self):
        return sorted(p.parent.name for p in self.dir.glob("*/sheet.json"))

    def never_sent(self):
        self.ok("12:00", "start", "--by", "Claude 会话「测试」")
        self.ok("12:05", "ask", *[x for k, v in ASK.items() for x in ("--" + k, v)])
        self.ok("12:30", "end")

    def card_then_one_more(self):
        self.ok("12:00", "start", "--by", "Claude 会话「测试」")
        self.ok("12:05", "ask", *[x for k, v in ASK.items() for x in ("--" + k, v)])
        self.ok("12:10", "push")                                          # ① 随卡片到了
        self.ok("12:20", "ask", *[x for k, v in dict(ASK, task="数字分身 · 合并官").items() for x in ("--" + k, v)])
        self.ok("12:30", "end")                                           # ② 是卡片发出之后冒出来的，要另发一句提醒

    def test_push_cut_at_any_save_never_unlocks_the_next_sheet_and_recovers_without_repeats(self):
        for case, prepare, reminders, waiting in (("一次都没发过", self.never_sent, 0, 1), ("卡片在，又冒出一件", self.card_then_one_more, 1, 2)):
            n = 0
            while True:
                n += 1
                self.fresh()
                prepare()
                if self.at("12:40", "push", cut=n)[0] is not None:        # 这条命令没写到第 n 次就做完了：存盘点都试过了
                    break
                with self.subTest(case=case, cut=n):
                    self.assertIn("已结束", self.ok("12:41", "show"))      # 断在哪一步留下的状态，脚本自己都读得了
                    code, out = self.at("12:41", "start", "--by", "下一张")
                    self.assertEqual(code, 1, out)                        # 没完整送到，不开下一张
                    self.assertIn("先 push", out)
                    self.assertEqual(self.sheets(), ["2026-11-21-1200"])
                    self.ok("12:50", "push")                              # 出路就是再 push：对账、补推、补提醒
                    cards = [m for m in self.chat() if m["msg_type"] == "interactive"]
                    said = [m for m in self.chat() if m["msg_type"] == "text"]
                    self.assertEqual((len(cards), len(said)), (1, reminders))            # 卡片只有一张，提醒不多不少
                    self.assertIn(f'<card title="托管结束｜还等你 {waiting} 件"', cards[0]["content"])
                    self.assertTrue(cards[0]["content"].rstrip("</card>").rstrip().endswith("版"))
                    if reminders:
                        self.assertIn("②", said[0]["content"])
                        self.assertEqual(said[0]["reply_to"], cards[0]["message_id"])
                    self.assertEqual(self.ok("13:00", "start", "--by", "下一张"), "2026-11-21-1300")
            self.assertGreaterEqual(n - 1, 5, case)                       # 确实切到了每个存盘点，不是一个都没切到

    def test_start_cut_between_its_two_writes_stops_until_someone_checks(self):
        # 开单先改 state.json、再写单子：断在中间，下次什么命令都读不到这张，start 也不再开，等人核对。
        # 反过来写（先单子后指针）断了会留下一张没人管的单子，下次 start 看不见它、再开一张
        self.never_sent()
        self.ok("12:40", "push")
        self.assertEqual(self.at("13:00", "start", "--by", "下一张", cut=1), (None, None))     # 一个字都没写就断了：和没跑一样
        self.assertEqual((self.sheets(), json.loads((self.dir / "state.json").read_text())), (["2026-11-21-1200"], {"current": "2026-11-21-1200"}))
        self.assertEqual(self.at("13:00", "start", "--by", "下一张", cut=2), (None, None))     # 改了指针、单子没写
        self.assertEqual((self.sheets(), json.loads((self.dir / "state.json").read_text())), (["2026-11-21-1200"], {"current": "2026-11-21-1300"}))
        code, out = self.at("13:05", "start", "--by", "下一张")
        self.assertEqual(code, 1)
        self.assertIn("没有 sheet.json", out)
        self.assertIn("目录里最后一张是 2026-11-21-1200", out)             # 告诉人怎么恢复
        for cmd in (["show"], ["push"], ["ask", *[x for k, v in ASK.items() for x in ("--" + k, v)]]):
            code, out = self.at("13:05", *cmd)
            self.assertEqual(code, 1, cmd)
            self.assertIn("读不了", out)
        self.assertEqual(self.sheets(), ["2026-11-21-1200"])
        A.write_json(self.dir / "state.json", {"current": "2026-11-21-1200"})                   # 人核对过，改回上一张
        self.assertEqual(self.ok("13:10", "start", "--by", "下一张"), "2026-11-21-1310")

    def test_a_sheet_nobody_points_at_still_blocks_the_next_one(self):
        # 账本里有一张没送达的单子、state.json 却指着别的（按旧的先后写单子时断了，或者指针被改回去了）：
        # 开单看的是账本里的每一张，不只是指针指的那张
        self.never_sent()
        self.ok("12:40", "push")
        self.ok("12:50", "start", "--by", "第二张")
        self.ok("12:55", "ask", *[x for k, v in ASK.items() for x in ("--" + k, v)])
        A.write_json(self.dir / "state.json", {"current": "2026-11-21-1200"})                   # 指针回到上一张，第二张没人管了
        code, out = self.at("13:00", "start", "--by", "第三张")
        self.assertEqual(code, 1)
        self.assertIn("托管单 2026-11-21-1250 还没结束", out)
        self.assertEqual(self.sheets(), ["2026-11-21-1200", "2026-11-21-1250"])
        (self.dir / "2026-11-21-1250" / "sheet.json").write_text("{", encoding="utf-8")         # 那张读不了也一样不开
        code, out = self.at("13:00", "start", "--by", "第三张")
        self.assertEqual(code, 1)
        self.assertIn("托管单 2026-11-21-1250 读不了", out)


if __name__ == "__main__":
    unittest.main()
