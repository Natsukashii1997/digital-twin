"""AI HOT 热点榜推送（#163）自测：接口用 file:// 假数据，假 lark-cli 记下每次调用；不联网、不碰真实记录目录。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "aihot_hot.py"
CHAT = "oc_testchat000001"
NOW = "2026-11-12T09:45:00+08:00"
TITLE = "🔥 AI 热点榜 TOP10 · 11-12（周四）"
# 假 lark-cli：按 FAKE_DIR/replies.json 里的 {子命令: 回话} 回答，每次调用（参数、工作目录、环境里的 HERMES 变量）记进 calls.jsonl。
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "cwd": os.getcwd(), "hermes_env": sorted(k for k in os.environ if "HERMES" in k)}, ensure_ascii=False) + "\n")
replies = json.loads((root / "replies.json").read_text(encoding="utf-8"))
cmd = args[3] if args[2] == "im" and args[3].startswith("+") else " ".join(args[2:5])
print(json.dumps(replies.get(cmd, {"ok": False, "error": {"message": "fake: 没有这条回话 " + cmd}}), ensure_ascii=False))
'''
OK_UPLOAD = {"ok": True, "data": {"image_key": "img_banner_1"}}
OK_SEND = {"ok": True, "data": {"message_id": "om_sent_1"}}
OK_MGET = {"ok": True, "data": {"messages": [{"msg_type": "interactive", "content": "<card>🖼️ 当日 AI HOT 热门榜 🔥 **TOP 3** … 👉 点标题进 AI HOT 看详情｜数据：aihot.news</card>"}]}}
TITLES = ["Anthropic 发布 Fable 5.2 与 Mythos 5.2，多项评测登顶", "Google 开放 Gemini 4 Carbon", "英伟达董事会调查黄仁勋涉情报事件",
          "Anthropic 启动 IPO 路演", "Claude 新增白板、表格与数据三类 artifact", "MiniMax 开源 H3-Image", "Meta 发布轻量模型 Muse Ember",
          "字节发布 Seedream 5.5", "Google 一年内交付四款第八代 TPU", "OpenAI 在 ChatGPT 测试企业智能体"]
DIGEST = ("某公司今天发布了新东西，两者基于同一底层模型。第二句话说明价格和上线平台，再补一些说明文字，让前两句合计超过七十字、但不到一百二十字，用来检查后七条只取第一句。"
          "第三句话比较长，加上它会超过前三条 120 字的上限，所以不该出现在概括里" + "很长" * 30 + "。\n\n第二段不该出现。")
LONG_FIRST = ("OpenAI 推出 ChatGPT Businesses 测试版：商家可在 Business Hub 认领并验证企业身份、管理展示信息，"
              "符合条件的商家还能搭建并发布 Business Agent，让顾客在清晰标注、与 ChatGPT 独立回答分开的对话中提问、比价、了解产品。")


def item(rank, title=None, **kw):
    sid = f"0000000{rank:01d}-aaaa-bbbb-cccc-{rank:012d}"
    it = {"rank": rank, "id": f"item{rank}", "title": title or TITLES[rank - 1],
          "links": {"aihot": f"https://aihot.news/items/item{rank}", "original": f"https://example.com/{rank}",
                    "story": f"https://aihot.news/story/{sid}"},
          "sourceCount": 9, "signalCount": 9, "participantCount": 9, "sourceNames": ["X：某人 (@a)"],
          "latestAt": "2026-11-11T22:12:54.000Z"}
    it.update(kw)
    return it


class AihotHot(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-aihot-"))
        self.api = self.tmp / "api"
        (self.api / "stories").mkdir(parents=True)
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        (self.fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (self.fake / "lark-cli").chmod(0o755)
        self.banner = self.tmp / "assets" / "aihot-banner.png"
        self.banner.parent.mkdir()
        self.banner.write_bytes(b"png-v1")
        self.dir = self.tmp / "aihot"
        self.reply({"im images create": OK_UPLOAD, "+messages-send": OK_SEND, "+messages-mget": OK_MGET})
        self.hot([item(r) for r in range(1, 11)])

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def reply(self, replies):
        (self.fake / "replies.json").write_text(json.dumps(replies, ensure_ascii=False), encoding="utf-8")

    def hot(self, items, digest=DIGEST):
        (self.api / "hot-topics").write_text(json.dumps({"schemaVersion": 1, "count": len(items), "items": items},
                                                        ensure_ascii=False), encoding="utf-8")
        for it in items:
            if "links" not in it:
                continue
            sid = it["links"]["story"].rsplit("/", 1)[1]
            (self.api / "stories" / sid).write_text(json.dumps({"story": {"digest": digest}}, ensure_ascii=False), encoding="utf-8")

    def run_script(self, *args, now=NOW, chat=CHAT):
        env = {k: v for k, v in os.environ.items() if k != "FEISHU_HOME_CHANNEL"}
        env.update(DT_AIHOT_BASE=self.api.as_uri() + "/", DT_AIHOT_DIR=str(self.dir), DT_AIHOT_BANNER=str(self.banner),
                   DT_LARK_CLI=str(self.fake / "lark-cli"), FAKE_DIR=str(self.fake), DT_NOW=now,
                   DT_AIHOT_RETRY_WAIT="0", HERMES_HOME="/x")
        if chat:
            env["FEISHU_HOME_CHANNEL"] = chat
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def calls(self):
        p = self.fake / "calls.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def sent_cards(self):
        out = []
        for c in self.calls():
            a = c["args"]
            if "+messages-send" in a:
                out.append((json.loads(a[a.index("--content") + 1]), a[a.index("--idempotency-key") + 1], a[a.index("--chat-id") + 1]))
        return out

    def runs(self):
        p = self.dir / "runs.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    @staticmethod
    def texts(card):
        out = []

        def walk(e):
            if isinstance(e, dict):
                if e.get("tag") == "markdown":
                    out.append(e["content"])
                for v in e.values():
                    walk(v)
            elif isinstance(e, list):
                for v in e:
                    walk(v)
        walk(card)
        return out

    def test_正常发送_横幅在最前_名次栏样式_成功不输出(self):
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        upload, send, mget = self.calls()
        self.assertEqual(upload["args"][:5], ["--profile", "cli_a96f5f9ac63adcd4", "im", "images", "create"])
        self.assertIn("image=aihot-banner.png", upload["args"])
        self.assertEqual(upload["cwd"], str(self.banner.parent.resolve()))
        self.assertEqual(upload["hermes_env"], [])              # 去掉 HERMES_* 再调 lark-cli
        self.assertEqual(send["args"][-4:], ["--as", "bot", "--format", "json"])
        self.assertIn("+messages-mget", mget["args"])
        (card, idem, chat), = self.sent_cards()
        self.assertEqual((idem, chat), ("aihot-hot-2026-11-12", CHAT))
        self.assertNotIn("header", card)                      # 不要表头，横幅就是标题
        self.assertEqual(card["config"]["summary"]["content"], TITLE)
        els = card["body"]["elements"]
        self.assertEqual(els[0]["tag"], "img")
        self.assertEqual(els[0]["img_key"], "img_banner_1")
        rows = [e for e in els if e["tag"] == "column_set"]
        self.assertEqual(len(rows), 10)
        self.assertEqual([r.get("background_style") for r in rows], ["grey"] * 3 + [None] * 7)
        self.assertEqual(rows[0]["columns"][0]["width"], "48px")
        self.assertIn("🥇", rows[0]["columns"][0]["elements"][0]["content"])
        self.assertIn("**<font color='grey'>04</font>**", rows[3]["columns"][0]["elements"][0]["content"])
        allmd = "\n".join(self.texts(card))
        self.assertIn("🔥 **TOP 3**", allmd)
        self.assertIn("📈 **No.04–10**", allmd)
        self.assertIn("[Anthropic 启动 IPO 路演](https://aihot.news/items/item4)", allmd)
        self.assertIn("<text_tag color='orange'>💰 资本</text_tag>", allmd)
        self.assertIn("<text_tag color='red'>⚠️ 风波</text_tag>", allmd)
        for gone in ("example.com", "来源", "参与者", "…", "第二段"):
            self.assertNotIn(gone, json.dumps(card, ensure_ascii=False))
        # 记录：当天卡片、状态、运行记录
        self.assertEqual(json.loads((self.dir / "2026-11-12.card.json").read_text(encoding="utf-8")), card)
        state = json.loads((self.dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["sent"]["2026-11-12"]["message_id"], "om_sent_1")
        self.assertEqual(state["banner"]["image_key"], "img_banner_1")
        (run,) = self.runs()
        self.assertEqual((run["ok"], run["message_id"], run["warnings"]), (True, "om_sent_1", []))

    def test_概括_前三整句到上限_后七整句_首句超长按分句收成一句(self):
        self.hot([item(r) for r in range(1, 10)] + [item(10)], digest=DIGEST)
        (self.api / "stories" / item(10)["links"]["story"].rsplit("/", 1)[1]).write_text(
            json.dumps({"story": {"digest": LONG_FIRST}}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(self.run_script().returncode, 0)
        (card, _, _), = self.sent_cards()
        rows = [e for e in card["body"]["elements"] if e["tag"] == "column_set"]
        greys = [r["columns"][1]["elements"][1]["content"] for r in rows]
        self.assertEqual(greys[0], "<font color='grey'>某公司今天发布了新东西，两者基于同一底层模型。第二句话说明价格和上线平台，再补一些说明文字，让前两句合计超过七十字、但不到一百二十字，用来检查后七条只取第一句。</font>")
        self.assertEqual(greys[3], "<font color='grey'>某公司今天发布了新东西，两者基于同一底层模型。</font>")
        self.assertEqual(greys[9], "<font color='grey'>OpenAI 推出 ChatGPT Businesses 测试版：商家可在 Business Hub 认领并验证企业身份、管理展示信息。</font>")

    def test_同一天第二次跑不再发_force可以再发且换幂等键(self):
        self.assertEqual(self.run_script().returncode, 0)
        r = self.run_script(now="2026-11-12T11:00:00+08:00")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(len(self.sent_cards()), 1)
        self.assertEqual(self.run_script("--force", now="2026-11-12T11:00:00+08:00").returncode, 0)
        cards = self.sent_cards()
        self.assertEqual(len(cards), 2)
        self.assertNotEqual(cards[0][1], cards[1][1])
        self.assertEqual(sum(1 for c in self.calls() if "images" in c["args"]), 1)   # 横幅内容没变不重传

    def test_接口取不到_输出一句原因_退出码1_不发卡片(self):
        (self.api / "hot-topics").unlink()
        r = self.run_script()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "AI 热点榜今天没发成：接口取不到（URLError）。\n")
        self.assertEqual(self.calls(), [])
        (run,) = self.runs()
        self.assertFalse(run["ok"])
        self.assertNotIn("2026-11-12", json.loads((self.dir / "state.json").read_text(encoding="utf-8")).get("sent", {}))

    def test_格式不对或不到10条_报原因(self):
        (self.api / "hot-topics").write_text("[]", encoding="utf-8")
        self.assertIn("接口返回的格式不对", self.run_script().stdout)
        self.hot([item(r) for r in range(1, 9)] + [item(8, title="重复名次")] + [{"rank": "10", "title": "名次不是整数"}])
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout), (1, "AI 热点榜今天没发成：榜单只拿到 8 条，不到 10 条。\n"))
        self.assertEqual(self.sent_cards(), [])

    def test_榜单超过48小时没更新_报原因(self):
        self.hot([item(r, latestAt="2026-11-09T01:00:00.000Z") for r in range(1, 11)])
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout), (1, "AI 热点榜今天没发成：榜单超过 48 小时没更新。\n"))
        self.assertEqual(self.sent_cards(), [])

    def test_坏链接和注入文字_不出链接_语法字符被转义(self):
        bad = [item(1, title="[点我](javascript:alert(1)) <at id=all></at> **加粗**",
                    links={"aihot": "javascript:alert(1)", "story": "https://evil.example.com/story/abcdefgh-1"}),
               item(2, links={"aihot": "https://evil.example.com/items/x", "story": "https://aihot.news/story/x)(y"}),
               item(3, links={"aihot": "ftp://aihot.news/x", "story": "https://aihot.news.evil.com/story/abcdefgh-3"}),
               *[item(r) for r in range(4, 11)]]
        (self.api / "hot-topics").write_text(json.dumps({"items": bad}, ensure_ascii=False), encoding="utf-8")
        for r in range(4, 11):
            sid = item(r)["links"]["story"].rsplit("/", 1)[1]
            (self.api / "stories" / sid).write_text(json.dumps({"story": {"digest": "正常概括。<font color='red'>不该变红</font>"}}), encoding="utf-8")
        self.assertEqual(self.run_script().returncode, 0)
        (card, _, _), = self.sent_cards()
        s = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("javascript", s.replace("［点我］(javascript:alert(1))", ""))
        self.assertNotIn("evil.example.com", s)
        self.assertNotIn("<at", s)
        self.assertIn("＜at id=all＞＜/at＞", s)
        self.assertIn("＜font color='red'＞不该变红＜/font＞", s)
        rows = [e for e in card["body"]["elements"] if e["tag"] == "column_set"]
        first = rows[0]["columns"][1]["elements"]
        self.assertTrue(first[0]["content"].startswith("**［点我］(javascript:alert(1)) ＜at id=all＞＜/at＞ ＊＊加粗＊＊**"))
        self.assertEqual(len(first), 1)                      # 详情取不到（story 链接不合法）就不放概括
        self.assertTrue(rows[1]["columns"][1]["elements"][0]["content"].startswith(
            f"**[{TITLES[1]}](https://aihot.news/story/x%29%28y)**"))   # aihot 链接不合法时退到事件链接，括号做百分号编码
        self.assertTrue(rows[2]["columns"][1]["elements"][0]["content"].startswith(f"**{TITLES[2]}**"))   # 两个都不合法就不加链接

    def test_单条详情取不到_那条只有标题_其余照发(self):
        (self.api / "stories" / item(5)["links"]["story"].rsplit("/", 1)[1]).unlink()
        self.assertEqual(self.run_script().returncode, 0)
        (card, _, _), = self.sent_cards()
        rows = [e for e in card["body"]["elements"] if e["tag"] == "column_set"]
        self.assertEqual([len(r["columns"][1]["elements"]) for r in rows], [2, 2, 2, 2, 1, 2, 2, 2, 2, 2])

    def test_横幅上传失败_照发不带图并提醒(self):
        self.reply({"im images create": {"ok": False, "error": {"message": "图片太大"}}, "+messages-send": OK_SEND, "+messages-mget": OK_MGET})
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "AI 热点榜已发出，但上传横幅失败：图片太大，今天卡片没带图。\n")
        (card, _, _), = self.sent_cards()
        self.assertNotEqual(card["body"]["elements"][0]["tag"], "img")

    def test_换横幅才重传(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.banner.write_bytes(b"png-v2")
        self.assertEqual(self.run_script(now="2026-11-13T09:45:00+08:00").returncode, 0)
        self.assertEqual(sum(1 for c in self.calls() if "images" in c["args"]), 2)

    def test_dry_run_只打印卡片_不调飞书不记状态(self):
        r = self.run_script("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)["config"]["summary"]["content"], TITLE)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.dir.exists())

    def test_发送失败或回读对不上_报原因(self):
        self.reply({"im images create": OK_UPLOAD, "+messages-send": {"ok": False, "error": {"message": "机器人不在会话里"}}})
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout), (1, "AI 热点榜今天没发成：发卡片失败：机器人不在会话里。\n"))
        self.reply({"im images create": OK_UPLOAD, "+messages-send": OK_SEND,
                    "+messages-mget": {"ok": True, "data": {"messages": [{"msg_type": "text", "content": "别的"}]}}})
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout), (1, "AI 热点榜今天没发成：卡片已发（om_sent_1），但回读对不上卡片内容。\n"))
        self.assertEqual(self.run_script().stdout, "")          # 已发出就记为已发，不重发
        self.assertEqual(len(self.sent_cards()), 2)

    def test_没有私聊chat_id_报原因(self):
        r = self.run_script(chat="")
        self.assertEqual((r.returncode, r.stdout), (1, "AI 热点榜今天没发成：没有私聊 chat_id（FEISHU_HOME_CHANNEL）。\n"))


if __name__ == "__main__":
    unittest.main()
