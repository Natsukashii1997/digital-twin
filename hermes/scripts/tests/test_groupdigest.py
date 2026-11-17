"""AI 前沿群学习报告（#192）自测：假 lark-cli、假源头、现成模型回复，不联网、不调模型、不碰真实目录。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import groupdigest as gd  # noqa: E402

DAY = "2026-11-13"
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "cwd": os.getcwd(), "hermes_env": sorted(k for k in os.environ if k.startswith("HERMES"))}, ensure_ascii=False) + "\n")
replies = json.loads((root / "replies.json").read_text(encoding="utf-8"))
cmd = args[3] if args[2] == "im" else " ".join(args[2:4])
r = replies.get(cmd, {"ok": False, "error": {"message": "fake: 没有这条回话 " + cmd}})
if r == "NOT_JSON":
    print("dial tcp: connection refused")
else:
    print(json.dumps(r, ensure_ascii=False))
'''
CARD1 = ('<card title="ClaudeDevs @ClaudeDevs" subtitle="Claude 开发者">\n「2条新帖」 「新功能」\n'
         "**What's going on · 1/2**\nClaude 托管智能体出了自托管私测版。\n▶ 原文\n    Self-hosted Managed Agents. "
         "[https://claude.com/resources/articles/self-hosted](https://claude.com/resources/articles/self-hosted)\n▲\n"
         "[查看第 1 条原帖 ↗](https://x.com/ClaudeDevs/status/111)\n</card>")
CARD2 = ('<card title="Lydia Hallie @lydiahallie" subtitle="Claude Code">\n「新帖」\n**What\'s going on**\n'
         "Claude Code 小技巧：跑 /doctor prompt-audit 查提示词配置。顺手提一下 claude.ai 和 https://feishu.cn/x\n"
         "[查看 X 原帖 ↗](https://x.com/lydiahallie/status/222)\n</card>")


def raw_msgs():
    return [{"msg_type": "interactive", "content": CARD1, "create_time": "2026-11-13 07:17", "message_id": "om_1",
             "message_app_link": "https://applink.feishu.cn/x?position=1", "sender": {"name": "X.COM", "sender_type": "app"}},
            {"msg_type": "interactive", "content": CARD2, "create_time": "2026-11-13 08:01", "message_id": "om_2",
             "message_app_link": "https://applink.feishu.cn/x?position=2", "sender": {"name": "X.COM", "sender_type": "app"}},
            {"msg_type": "text", "content": "这个 prompt-audit 我试了，挺有用", "create_time": "2026-11-13 09:30",
             "message_id": "om_3", "message_app_link": "https://applink.feishu.cn/x?position=3",
             "sender": {"name": "张三", "sender_type": "user"}},
            {"msg_type": "text", "content": "撤回了", "deleted": True, "create_time": "2026-11-13 09:31"}]


def good_fields(**kw):
    f = {"highlights": [{"title": "Anthropic 开放托管智能体自托管私测", "detail": "Managed Agents 可以放在自己的机器上跑，沙箱和会话历史分开放。",
                         "quote": "模型多写的字段会被丢掉", "read": True, "refs": ["M1", "S1"]},
                        {"title": "Claude Code 出了提示词体检命令", "detail": "跑 /doctor prompt-audit 能查出提示词配置的问题。",
                         "read": "yes", "refs": ["M2"]}],
         "todo": [{"task": "在数字分身仓库跑 /doctor prompt-audit", "where": "digital-twin", "minutes": 15,
                   "done": "拿到问题清单", "snippet": "claude /doctor `prompt-audit`", "owner": "Claude",
                   "projects": ["digital-twin", "agent-kit", "nope"], "refs": ["M2"]},
                  {"task": "打开 Codex 用量页看额度", "where": "Codex", "minutes": 5, "done": "知道到账没有",
                   "owner": "you", "projects": ["digital-twin"], "refs": ["M3"]},
                  {"task": "给 agent-kit 加一条规则", "where": "agent-kit", "minutes": 10, "done": "规则写进去",
                   "owner": "claude", "projects": ["nope"], "refs": ["M2"]}],
         "consider": [{"idea": "每月跑一次 prompt-audit", "why": "规则会过期", "question": "和每月 skill 体检要不要合并",
                       "where": "全局 CLAUDE.md", "refs": ["M2", "M3"]}]}
    f.update(kw)
    return f


def good_report(extra=""):
    secs = {s: f"内容 [M1]{extra}" for s in gd.SECTIONS}
    secs["一、昨日信息梳理"] = "### 智能体\n托管智能体自托管 [M1][S1]。\n### Claude Code\n体检命令 [M2]，群友说好用 [M3]。"
    return "\n\n".join(f"## {k}\n{v}" for k, v in secs.items())


def reply_text(fields=None, report=None):
    return (f"{gd.CARD_MARK}\n```json\n{json.dumps(fields or good_fields(), ensure_ascii=False)}\n```\n"
            f"{gd.REPORT_MARK}\n{report if report is not None else good_report()}")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-gd-"))
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        (self.fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (self.fake / "lark-cli").chmod(0o755)
        self.dir = self.tmp / "gd"
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        (self.vault / "关于我.md").write_text("---\nsensitivity: 受限\n---\n常浩，一个人推进十几个个人项目。", encoding="utf-8")
        (self.tmp / "CLAUDE.md").write_text("# 规则\n## 0. 回复语言\n## 2. Simplicity First\n", encoding="utf-8")
        self.reply({"+chat-messages-list": {"ok": True, "data": {"messages": raw_msgs()}},
                    "docs +create": {"ok": True, "data": {"document": {"url": "https://x.feishu.cn/docx/abc"}}},
                    "+messages-send": {"ok": True, "data": {"message_id": "om_sent"}},
                    "+messages-mget": {"ok": True, "data": {"messages": [{"msg_type": "interactive",
                                                                           "content": f"<card>{gd.title_of(DAY)}</card>"}]}}})
        self.patches = [mock.patch.multiple(gd, DIR=self.dir, LARK=str(self.fake / "lark-cli"), VAULT=self.vault,
                                            RULES=self.tmp / "CLAUDE.md", WAIT=0, SEND_WAIT=0, RATE_WAIT=0),
                        mock.patch.dict(os.environ, {"FAKE_DIR": str(self.fake), "FEISHU_HOME_CHANNEL": "oc_home",
                                                     "DT_NOW": "2026-11-14T10:00:00+08:00", "HERMES_HOME": "/x"}),
                        mock.patch.object(gd, "read_source", side_effect=self.fake_source),
                        mock.patch.object(gd, "coordinates", wraps=self.coords),
                        mock.patch.object(gd, "roster_ids", return_value={"digital-twin", "agent-kit"})]
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
        for p in self.patches:   # addCleanup：子类 setUp 出错时补丁也会撤掉，不漏到别的测试
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def fake_source(url):
        return ("自托管文章", "正文" * 200, "direct", None) if "self-hosted" in url else (None, None, None, "HTTP 403")

    def coords(self):
        return {"projects": [{"name": "数字分身", "type": "product", "goal": "执行底座换 agent-autoloop"}],
                "vault": [{"note": "个人上下文/关于我.md", "text": "常浩"}], "rules": ["0. 回复语言"], "errors": []}

    def reply(self, replies):
        (self.fake / "replies.json").write_text(json.dumps(replies, ensure_ascii=False), encoding="utf-8")

    def calls(self):
        p = self.fake / "calls.jsonl"
        return [json.loads(l)["args"] for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def collected(self):
        gd.collect(gd.dt.date.fromisoformat(DAY))
        return json.loads((self.dir / f"{DAY}.material.json").read_text(encoding="utf-8"))


class Collect(Base):
    def test_取群消息_卡片摘要多帖也取到_去掉撤回_X和飞书和首页链接不抓(self):
        mat = self.collected()
        items = mat["items"]
        self.assertEqual([i["id"] for i in items], ["M1", "M2", "M3"])
        self.assertEqual(items[0]["title"], "ClaudeDevs @ClaudeDevs")
        self.assertEqual(items[0]["gist"], "Claude 托管智能体出了自托管私测版。")
        self.assertEqual(items[0]["x_url"], "https://x.com/ClaudeDevs/status/111")
        self.assertEqual(items[2]["kind"], "text")
        self.assertEqual(items[2]["sender"], "张三")
        self.assertEqual([s["url"] for s in mat["sources"]], ["https://claude.com/resources/articles/self-hosted"])
        self.assertEqual(mat["coverage"]["cards"], 2)
        self.assertEqual(mat["coverage"]["member_posts"], 1)
        args = self.calls()[0]
        self.assertIn("--as", args)
        self.assertEqual(args[args.index("--as") + 1], "user")
        self.assertEqual(args[args.index("--start") + 1], "2026-11-13T00:00:00+08:00")
        self.assertEqual(args[args.index("--end") + 1], "2026-11-14T00:00:00+08:00")
        self.assertEqual(oct((self.dir / f"{DAY}.material.json").stat().st_mode & 0o777), "0o600")

    def test_源头超过上限的记成没抓_同一链接合并出处(self):
        items = [{"id": f"M{n}", "links": [f"https://e.com/a{n}", "https://e.com/a1#x"]} for n in range(1, 5)]
        with mock.patch.object(gd, "MAX_SOURCES", 2):
            srcs, skipped = gd.fetch_sources(items)
        self.assertEqual([s["url"] for s in srcs], ["https://e.com/a1", "https://e.com/a2"])
        self.assertEqual(srcs[0]["from"], ["M1", "M2", "M3", "M4"])
        self.assertEqual([s["url"] for s in skipped], ["https://e.com/a3", "https://e.com/a4"])

    def test_飞书输出不是JSON_等到期限还不通就报错(self):
        self.reply({"+chat-messages-list": "NOT_JSON"})
        with self.assertRaises(gd.Fail) as e:
            self.collected()
        self.assertIn("不是 JSON", str(e.exception))


class Sources(unittest.TestCase):
    def test_先直接抓_抓到就不调agent_reach(self):
        with mock.patch.object(gd, "read_direct", return_value=("标题", "正文" * 200)), \
                mock.patch.object(gd, "read_once") as once:
            got = gd.read_source("https://e.com/a")
        self.assertEqual((got[0], got[2], got[3]), ("标题", "direct", None))
        once.assert_not_called()

    def test_直接抓不到交给agent_reach_被限流隔一会再试(self):
        seq = [(None, None, "jina", "HTTP 429"), (None, None, "jina", "Platform reported a rate limit"),
               ("t", "正文" * 200, "jina", None)]
        with mock.patch.object(gd, "read_direct", return_value=None), mock.patch.object(gd, "RATE_WAIT", 0), \
                mock.patch.object(gd, "read_once", side_effect=seq) as once:
            got = gd.read_source("https://e.com/a")
        self.assertEqual(once.call_count, 3)
        self.assertIsNone(got[3])

    def test_网页转文本去掉脚本和导航(self):
        p = gd._Text()
        p.feed("<html><head><title>T</title><script>var x=1</script></head><body><nav>菜单</nav>"
               "<article><p>第一段</p><p>第二段</p></article><footer>版权</footer></body></html>")
        text = "".join(p.parts)
        self.assertEqual(p.title, "T")
        self.assertIn("第一段", text)
        for bad in ("var x", "菜单", "版权"):
            self.assertNotIn(bad, text)


class Check(Base):
    def setUp(self):
        super().setUp()
        self.mat = self.collected()

    def test_合格的稿子通过_多写的字段丢掉_精读只认true(self):
        errs, warns, out = gd.check(self.mat, good_fields(), good_report())
        self.assertEqual(errs, [])
        self.assertEqual(out["todo"][0]["minutes"], 15)
        self.assertEqual(out["todo"][0]["snippet"], "claude /doctor prompt-audit")
        h1, h2 = out["highlights"]
        self.assertNotIn("quote", h1)
        self.assertEqual((h1["read"], h2["read"]), (True, False))

    def test_谁做_只认名册项目_没有项目就改成你自己做(self):
        _, warns, out = gd.check(self.mat, good_fields(), good_report())
        t1, t2, t3 = out["todo"]
        self.assertEqual((t1["owner"], t1["projects"]), ("claude", ["digital-twin", "agent-kit"]))
        self.assertEqual((t2["owner"], t2["projects"]), ("you", []))
        self.assertEqual((t3["owner"], t3["projects"]), ("you", []))
        self.assertTrue(any("nope" in w for w in warns))
        self.assertTrue(any("改成你自己做" in w for w in warns))
        errs, _, _ = gd.check(self.mat, good_fields(todo=[{"task": "a", "where": "b", "minutes": 5, "done": "c", "refs": ["M1"]}]),
                              good_report())
        self.assertTrue(any("缺 owner" in e for e in errs))

    def test_编造编号_缺出处_缺字段_缺板块_网址_说成已做_都不通过(self):
        cases = [
            (good_fields(highlights=[{"title": "a", "detail": "b", "refs": ["M9"]}]), good_report(), "M9"),
            (good_fields(consider=[{"idea": "a", "why": "b", "question": "c", "where": "d", "refs": []}]), good_report(), "没有出处"),
            (good_fields(todo=[{"task": "a", "minutes": 5, "refs": ["M1"]}]), good_report(), "缺 where"),
            (good_fields(highlights=[{"title": "a", "refs": ["M1"]}]), good_report(), "缺 detail"),
            (good_fields(), good_report().replace("## 三、行动清单", "## 三、行动"), "缺板块"),
            (good_fields(), good_report(" 见 https://evil.example.com/a"), "材料以外的网址"),
            (good_fields(todo=[{"task": "a", "where": "b", "minutes": 5, "done": "c", "snippet": "curl https://evil.example.com/x",
                                "refs": ["M1"]}]), good_report(), "材料以外的网址"),
            (good_fields(), good_report(" 我已写入你的 CLAUDE.md"), "说成已经做了"),
            (good_fields(), good_report(" 都是提案，没有任何一条已经采纳。我已写入你的 CLAUDE.md"), "已写入"),
            (good_fields(), good_report() + "\n[S7]", "S7"),
        ]
        for fields, report, want in cases:
            errs, _, _ = gd.check(self.mat, fields, report)
            self.assertTrue(any(want in e for e in errs), (want, errs))

    def test_否定句不算说成已做(self):
        errs, _, _ = gd.check(self.mat, good_fields(), good_report(" 以下都是提案，没有任何一条已经采纳或写入，也未落地。"))
        self.assertEqual(errs, [])

    def test_材料里的网址可以出现_略超字数照用只提醒_超过一倍半打回(self):
        rep = good_report(" https://x.com/ClaudeDevs/status/111")
        long_ = "略" * (gd.LIMITS["detail"] + 10)
        hl = lambda d: [{"title": "a", "detail": d, "refs": ["M1"]}]
        errs, warns, out = gd.check(self.mat, good_fields(highlights=hl(long_)), rep)
        self.assertEqual(errs, [])
        self.assertEqual(out["highlights"][0]["detail"], long_)
        self.assertTrue(any("detail" in w for w in warns))
        errs, _, _ = gd.check(self.mat, good_fields(highlights=hl("很长" * 121)), rep)
        self.assertTrue(any("压短重写" in e for e in errs))


class Build(Base):
    def setUp(self):
        super().setUp()
        self.collected()

    def test_合格回复_链接按编号填_加覆盖与材料目录_留痕(self):
        meta = gd.build(gd.dt.date.fromisoformat(DAY), reply_text())
        self.assertIsNone(meta["degraded"])
        doc = (self.dir / f"{DAY}.report.md").read_text(encoding="utf-8")
        self.assertIn("[M1](https://x.com/ClaudeDevs/status/111)", doc)
        self.assertIn("[S1](https://claude.com/resources/articles/self-hosted)", doc)
        self.assertIn("[M3](https://applink.feishu.cn/x?position=3)", doc)
        self.assertIn("## " + gd.COVERAGE, doc)
        self.assertIn("## 附：材料目录", doc)
        self.assertTrue(doc.startswith("生成：Hermes"))

    def test_回复核对不过_出事实版(self):
        meta = gd.build(gd.dt.date.fromisoformat(DAY), reply_text(report="没有板块"))
        self.assertEqual(meta["degraded"], "核对没通过")
        fields = json.loads((self.dir / f"{DAY}.card-fields.json").read_text(encoding="utf-8"))
        self.assertEqual((fields["todo"], fields["consider"]), ([], []))
        card = json.dumps(gd.build_card(DAY, fields, meta, json.loads((self.dir / f"{DAY}.material.json").read_text(encoding="utf-8"))),
                          ensure_ascii=False)
        self.assertIn("没写成分析（核对没通过）", card)
        self.assertEqual([h["refs"] for h in fields["highlights"]], [["M1"], ["M2"]])
        self.assertTrue((self.dir / f"{DAY}.report.md").read_text(encoding="utf-8").startswith("生成：脚本事实版"))

    def test_第一稿不过_带着问题重写一次_第二稿通过(self):
        bad, good = reply_text(report="没有板块"), reply_text()
        with mock.patch.object(gd, "generate", side_effect=[(bad, "claude-opus-5-5"), (good, "claude-opus-5-5")]) as g:
            meta = gd.build(gd.dt.date.fromisoformat(DAY))
        self.assertEqual(g.call_count, 2)
        self.assertTrue(any("缺板块" in f for f in g.call_args_list[1].args[1]))
        self.assertIsNone(meta["degraded"])
        self.assertIn("claude-opus-5-5", meta["trace"])

    def test_模型跑不起来两次_出事实版(self):
        with mock.patch.object(gd, "generate", side_effect=gd.Fail("Hermes 退出码 1")):
            meta = gd.build(gd.dt.date.fromisoformat(DAY))
        self.assertEqual(meta["degraded"], "模型没写成")

    def test_没有消息的日子不调模型(self):
        self.reply({"+chat-messages-list": {"ok": True, "data": {"messages": []}}})
        self.collected()
        with mock.patch.object(gd, "generate") as g:
            meta = gd.build(gd.dt.date.fromisoformat(DAY))
        g.assert_not_called()
        self.assertTrue(meta.get("empty"))


class Send(Base):
    def setUp(self):
        super().setUp()
        self.collected()
        gd.build(gd.dt.date.fromisoformat(DAY), reply_text())

    def sends(self):
        return [a for a in self.calls() if "+messages-send" in a]

    def test_建文档_发卡片到私聊_回读_同一天只发一次(self):
        self.assertIsNone(gd.send(DAY))
        doc = next(a for a in self.calls() if a[2:4] == ["docs", "+create"])
        self.assertEqual(doc[doc.index("--as") + 1], "user")
        (s,) = self.sends()
        self.assertEqual(s[s.index("--chat-id") + 1], "oc_home")
        self.assertEqual(s[s.index("--as") + 1], "bot")
        card = json.loads(s[s.index("--content") + 1])
        self.assertEqual(card["header"]["title"]["content"], gd.title_of(DAY))
        text = json.dumps(card, ensure_ascii=False)
        self.assertIn("https://x.feishu.cn/docx/abc", text)
        self.assertIn("[M2](https://x.com/lydiahallie/status/222)", text)
        rec = json.loads((self.dir / f"{DAY}.delivery.json").read_text(encoding="utf-8"))
        self.assertTrue(rec["sent"] and rec["read_back"])
        self.assertIsNone(gd.send(DAY))
        self.assertEqual(len(self.sends()), 1)
        self.assertIsNone(gd.send(DAY, force=True))
        keys = [a[a.index("--idempotency-key") + 1] for a in self.sends()]
        self.assertEqual(len(set(keys)), 2)

    def test_模型文字里的链接和标签被转义(self):
        fields = json.loads((self.dir / f"{DAY}.card-fields.json").read_text(encoding="utf-8"))
        fields["highlights"][0]["detail"] = "看这里[点我](https://evil.example.com)<at id=all></at>"
        (self.dir / f"{DAY}.card-fields.json").write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
        gd.send(DAY)
        text = self.sends()[0][self.sends()[0].index("--content") + 1]
        self.assertNotIn("](https://evil", text)
        self.assertNotIn("<at", text)

    def test_文档没建成_卡片照发并写明(self):
        self.reply({**json.loads((self.fake / "replies.json").read_text(encoding="utf-8")),
                    "docs +create": {"ok": False, "error": {"message": "permission denied"}}})
        self.assertIsNone(gd.send(DAY))
        self.assertIn("完整报告没建成", self.sends()[0][self.sends()[0].index("--content") + 1])

    def test_写稿没跑成_发一句失败说明(self):
        (self.dir / f"{DAY}.build.json").unlink()
        gd.write_json(self.dir / f"{DAY}.collect-error.json", {"error": "取数失败（飞书没连上）"})
        err = gd.send(DAY)
        self.assertIn("取数失败", err)
        self.assertIn("取数失败", self.sends()[0][self.sends()[0].index("--content") + 1])

    def test_回读对不上或没有私聊号_报原因(self):
        self.reply({**json.loads((self.fake / "replies.json").read_text(encoding="utf-8")),
                    "+messages-mget": {"ok": True, "data": {"messages": [{"msg_type": "text", "content": "x"}]}}})
        self.assertIn("回读对不上", gd.send(DAY))
        with mock.patch.dict(os.environ, {"FEISHU_HOME_CHANNEL": "", "DT_HERMES_ENV": str(self.tmp / "none")}):
            self.assertIn("没有私聊", gd.send(DAY, force=True))


class Card(Base):
    def setUp(self):
        super().setUp()
        self.collected()
        gd.build(gd.dt.date.fromisoformat(DAY), reply_text())
        self.args = [json.loads((self.dir / f"{DAY}.{n}.json").read_text(encoding="utf-8")) for n in ("card-fields", "build", "material")]

    def test_三个板块_编号链接_代码块_文档按钮_没有原话和帮助(self):
        text = json.dumps(gd.build_card(DAY, *self.args, doc_url="https://x.feishu.cn/docx/abc"), ensure_ascii=False)
        for want in ("昨日信息梳理", "今日行动清单", "思考采纳建议", "点头才落地", "值得精读", "条待你想",
                     "谁做 · 在哪", ">Claude</text_tag>", ">你</text_tag>", "**1. 在数字分身仓库跑", "❓ **要我把第 1 条写成任务派给 Claude 吗？**", "回「**派**」就开单",
                     "[M2](https://x.com/lydiahallie/status/222)", "[S1](https://claude.com/resources/articles/self-hosted)",
                     "`claude /doctor prompt-audit`", "https://x.feishu.cn/docx/abc"):
            self.assertIn(want, text, want)
        for gone in ("这些对你的帮助", "模型多写的字段会被丢掉", "要用到"):
            self.assertNotIn(gone, text)

    def test_多条可派_结尾给都派和逐条的写法(self):
        todo = [dict(r, owner="claude", projects=["digital-twin"]) for r in self.args[0]["todo"]]
        text = json.dumps(gd.build_card(DAY, dict(self.args[0], todo=todo), *self.args[1:]), ensure_ascii=False)
        self.assertIn("要我把第 1、2、3 条写成任务派给 Claude 吗？", text)
        self.assertIn("「**都派**」，或「**派 1 2，3 不做**」", text)

    def test_思考采纳在行动清单前_派单提问在最后且显眼(self):
        els = gd.build_card(DAY, *self.args, doc_url="https://x.feishu.cn/docx/abc")["body"]["elements"]
        flat = [json.dumps(e, ensure_ascii=False) for e in els]
        at = lambda word: next(i for i, t in enumerate(flat) if word in t)
        self.assertLess(at("昨日信息梳理"), at("思考采纳建议"))
        self.assertLess(at("思考采纳建议"), at("今日行动清单"))
        ask = at("❓")
        self.assertGreater(ask, at("今日行动清单"))
        self.assertEqual(els[ask + 1]["tag"], "button")  # 提问后面紧跟完整报告按钮
        col = els[ask]["columns"][0]
        self.assertEqual(col["background_style"], "red-50")
        self.assertEqual(col["elements"][0].get("text_size"), "heading-4")

    def test_板块没内容就不出现_没消息写一句(self):
        f = dict(self.args[0], todo=[r for r in self.args[0]["todo"] if r["owner"] == "you"])
        self.assertNotIn("都派", json.dumps(gd.build_card(DAY, f, *self.args[1:]), ensure_ascii=False))
        f = dict(self.args[0], todo=[], consider=[])
        text = json.dumps(gd.build_card(DAY, f, *self.args[1:]), ensure_ascii=False)
        self.assertNotIn("今日行动清单", text)
        self.assertNotIn("思考采纳建议", text)
        text = json.dumps(gd.build_card(DAY, dict(gd.EMPTY_FIELDS), *self.args[1:]), ensure_ascii=False)
        self.assertIn("昨天群里的消息都是资讯，没有值得学的内容", text)   # 常浩 11-17：一天全是资讯就一条不写
        empty = dict(self.args[2], items=[], sources=[])
        text = json.dumps(gd.build_card(DAY, dict(gd.EMPTY_FIELDS), self.args[1], empty), ensure_ascii=False)
        self.assertIn("昨天群里没有新消息", text)

    def test_全是资讯可以一条信息都不写(self):
        errs, _, out = gd.check(self.args[2], good_fields(highlights=[], todo=[], consider=[]), good_report())
        self.assertEqual(errs, [])
        self.assertEqual(out["highlights"], [])

    def test_每条信息_原文折叠面板在前_原地址在后(self):
        els = gd.build_card(DAY, *self.args)["body"]["elements"]
        first = next(e for e in els if e["tag"] == "column_set" and "number_tag" in json.dumps(e, ensure_ascii=False))
        parts = first["columns"][0]["elements"]
        tags = [p["tag"] for p in parts]
        self.assertEqual(tags[-2:], ["collapsible_panel", "markdown"])
        panel, link = parts[-2], parts[-1]
        self.assertFalse(panel["expanded"])
        self.assertEqual(panel["header"]["title"]["content"], "原文与译文 · 展开")
        self.assertIn("原地址", link["content"])
        self.assertIn("](https://", link["content"])
        self.assertNotIn("原地址", parts[1]["content"])   # 总结里不再接链接

    def test_思考和行动之间有分割线(self):
        els = gd.build_card(DAY, *self.args)["body"]["elements"]
        flat = [json.dumps(e, ensure_ascii=False) for e in els]
        a = next(i for i, t in enumerate(flat) if "思考采纳建议" in t)
        b = next(i for i, t in enumerate(flat) if "今日行动清单" in t)
        self.assertIn({"tag": "hr"}, els[a:b])

    def test_原文太长时收紧_卡片不超上限(self):
        mat = json.loads(json.dumps(self.args[2]))
        for i in mat["items"]:
            i["text"] = "▶ 原文与译文 · 展开\n    **中文**\n    " + "很长的中文" * 400 + "\n    **Original**\n    " + "long " * 900 + "\n▲"
        hl = [{"title": f"第{n}条", "detail": "说明", "read": False, "refs": ["M1", "M2", "M3"]} for n in range(6)]
        card = gd.build_card(DAY, dict(self.args[0], highlights=hl), self.args[1], mat)
        self.assertLessEqual(len(json.dumps(card["body"]["elements"], ensure_ascii=False).encode()), gd.CARD_MAX)
        self.assertIn("原文与译文", json.dumps(card, ensure_ascii=False))


    def test_预览发到私聊_不算当天已发(self):
        mid = gd.preview(DAY)
        self.assertTrue(mid)
        (send,) = [a for a in self.calls() if "+messages-send" in a]
        self.assertTrue(json.loads(send[send.index("--content") + 1])["header"]["title"]["content"].startswith("【预览】"))
        self.assertFalse((self.dir / f"{DAY}.delivery.json").exists())


class Original(unittest.TestCase):
    MAT = {"items": [
        {"id": "M1", "kind": "card", "sender": "X.COM", "title": "Lydia @lydia", "gist": "摘要",
         "text": "「新帖」\n**What's going on**\n摘要\n▶ 原文与译文 · 展开\n    **中文**\n    自定义主题就是 mod\n    **Original**\n"
                 "    Custom themes are just mods\n▲\n📝 时间\n[查看 X 原帖 ↗](https://x.com/a)"},
        {"id": "M2", "kind": "card", "sender": "X.COM", "title": "Bob", "gist": "只有摘要", "text": "只有摘要"},
        {"id": "M3", "kind": "text", "sender": "张三", "title": "", "gist": "", "text": "这个 [技巧](https://e.com) 我试了"}],
        "sources": [{"id": "S1", "ok": True, "title": "文章", "text": "正文 " * 500},
                    {"id": "S2", "ok": False, "title": None, "text": None}]}

    def test_X卡片取中文和Original_不带原帖链接(self):
        t = gd.original_md("M1", self.MAT, 700)
        for want in ("M1 · Lydia @lydia", "**中文**", "自定义主题就是 mod", "**Original**", "Custom themes are just mods"):
            self.assertIn(want, t)
        self.assertNotIn("查看 X 原帖", t)
        self.assertNotIn("](", t)

    def test_没有原文段取摘要_成员发言取原话并转义(self):
        self.assertIn("只有摘要", gd.original_md("M2", self.MAT, 700))
        t = gd.original_md("M3", self.MAT, 700)
        self.assertIn("张三", t)
        self.assertNotIn("](", t)

    def test_源头取节选_没读到的不放(self):
        self.assertIn("节选", gd.original_md("S1", self.MAT, 50))
        self.assertIsNone(gd.original_md("S2", self.MAT, 50))
        panel = gd.orig_panel(["S2", "S1", "M3", "M2", "M1"], self.MAT, 100)
        self.assertEqual(len(panel["elements"]), gd.ORIG_REFS)   # 群消息在前，最多 3 条
        self.assertTrue(panel["elements"][0]["content"].startswith("<font color='grey'>M3"))
        self.assertIsNone(gd.orig_panel(["S2"], self.MAT, 100))


class Dispatch(Base):
    def setUp(self):
        super().setUp()
        self.collected()
        gd.build(gd.dt.date.fromisoformat(DAY), reply_text())

    def test_没发过卡片就没有清单(self):
        with self.assertRaises(gd.Fail):
            gd.todos()

    def test_清单_派单记账_不重复派_你做的不派_不做的记下(self):
        gd.send(DAY)
        info = gd.todos()
        self.assertEqual((info["day"], info["card_message_id"], info["pending"]), (DAY, "om_sent", [1]))
        self.assertEqual(info["items"][0]["projects"], ["digital-twin", "agent-kit"])
        self.assertEqual(info["items"][0]["refs"], [{"id": "M2", "url": "https://x.com/lydiahallie/status/222"}])
        self.assertIn("已记下", gd.mark(None, 1, "digital-twin", "T-1"))
        self.assertEqual(gd.todos()["pending"], [])
        for args, want in [((None, 1, "digital-twin", "T-2"), "已经派过"), ((None, 1, "work-desk", "T-3"), "只能派到"),
                           ((None, 2, "digital-twin", "T-4"), "你自己做"), ((None, 9, "digital-twin", "T-5"), "没有第 9 条"),
                           ((None, 1, None, None, True), "不能再记成不做")]:
            with self.assertRaises(gd.Fail) as cm:
                gd.mark(*args)
            self.assertIn(want, str(cm.exception))
        gd.mark(None, 1, "agent-kit", "T-6")
        self.assertIn("不做", gd.mark(DAY, 3, skip=True))
        state = json.loads((self.dir / f"{DAY}.dispatch.json").read_text(encoding="utf-8"))
        self.assertEqual([o["order"] for o in state["items"]["1"]["orders"]], ["T-1", "T-6"])
        self.assertEqual(state["items"]["3"]["decision"], "skipped")

    def test_命令行_出错给一句话(self):
        gd.send(DAY)
        with mock.patch("sys.stdout") as out:
            self.assertEqual(gd.main(["mark", "--item", "2", "--project", "digital-twin", "--order", "T"]), 1)
        self.assertIn("你自己做", "".join(c.args[0] for c in out.write.call_args_list))


class Jobs(unittest.TestCase):
    def test_两个定时任务入口能导入(self):
        for name in ("groupdigest_build_job.py", "groupdigest_send_job.py"):
            r = subprocess.run([sys.executable, "-B", "-c", f"import ast,sys; ast.parse(open({str(SCRIPTS / name)!r}).read())"],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
