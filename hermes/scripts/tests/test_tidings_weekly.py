"""Tidings 周报推送（#55）自测：假 lark-cli 记下每次调用，不联网、不碰真实周报和状态文件。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "tidings_weekly.py"
CHAT = "oc_testchat000001"
# 假 lark-cli：按 FAKE_DIR/replies.json 里的 {子命令: 回话} 回答，每次调用（参数和工作目录）记进 calls.jsonl。
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
CARD = {"schema": "2.0", "header": {"title": {"tag": "plain_text", "content": "🏆 GitHub 本周涨星榜"}},
        "body": {"elements": [{"tag": "markdown", "content": "**morluto/rea**"}]}}
OK_UPLOAD = {"ok": True, "data": {"image_key": "img_banner_1"}}
OK_SEND = {"ok": True, "data": {"message_id": "om_sent_1"}}


class TidingsWeekly(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-tidings-"))
        self.repo = self.tmp / "github-center"
        (self.repo / "data/weekly").mkdir(parents=True)
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        (self.fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (self.fake / "lark-cli").chmod(0o755)
        self.banner = self.tmp / "assets" / "tidings-banner.png"
        self.banner.parent.mkdir()
        self.banner.write_bytes(b"png-v1")
        self.state = self.tmp / "state" / "tidings_weekly.json"
        self.reply({"im images create": OK_UPLOAD, "+messages-send": OK_SEND})

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def reply(self, replies):
        (self.fake / "replies.json").write_text(json.dumps(replies, ensure_ascii=False), encoding="utf-8")

    def week(self, day, card=CARD):
        (self.repo / "data/weekly" / f"{day}.feishu.md").write_text("## 周报\n", encoding="utf-8")
        if card is not None:
            (self.repo / "data/weekly" / f"{day}.card.json").write_text(card if isinstance(card, str) else json.dumps(card), encoding="utf-8")

    def run_script(self, *args, now="2026-11-09T11:30:00+08:00", chat=CHAT):
        env = {k: v for k, v in os.environ.items() if k != "FEISHU_HOME_CHANNEL"}
        env.update(TIDINGS_REPO=str(self.repo), DT_TIDINGS_STATE=str(self.state), DT_TIDINGS_BANNER=str(self.banner),
                   DT_LARK_CLI=str(self.fake / "lark-cli"), FAKE_DIR=str(self.fake), DT_NOW=now, HERMES_HOME="/x")
        if chat:
            env["FEISHU_HOME_CHANNEL"] = chat
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def calls(self):
        p = self.fake / "calls.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def sent(self):
        out = []
        for c in self.calls():
            a = c["args"]
            if "+messages-send" in a:
                out.append({k: a[a.index(k) + 1] for k in ("--chat-id", "--msg-type", "--content", "--idempotency-key")})
        return out

    def test_正常发送_横幅插在最前面_成功不输出(self):
        self.week("2026-11-01")
        self.week("2026-11-08")
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        upload, send = self.calls()
        self.assertEqual(upload["args"][:6], ["--profile", "cli_a96f5f9ac63adcd4", "im", "images", "create", "--data"])
        self.assertIn("image=tidings-banner.png", upload["args"])
        self.assertEqual(upload["cwd"], str(self.banner.parent.resolve()))
        self.assertEqual(upload["hermes_env"], [])           # 去掉 HERMES_* 再调 lark-cli
        self.assertEqual(send["args"][-4:], ["--as", "bot", "--format", "json"])
        (msg,) = self.sent()
        self.assertEqual([msg["--chat-id"], msg["--msg-type"], msg["--idempotency-key"]], [CHAT, "interactive", "tidings-weekly-2026-11-08"])
        content = json.loads(msg["--content"])
        self.assertEqual(content["body"]["elements"][0]["img_key"], "img_banner_1")
        self.assertEqual(content["body"]["elements"][1:], CARD["body"]["elements"])
        self.assertEqual(content["header"], CARD["header"])
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["sent"]["card:2026-11-08"]["message_id"], "om_sent_1")
        self.assertEqual(state["banner"]["image_key"], "img_banner_1")

    def test_同一期只发一次_横幅缓存复用_换图才重传_force再发(self):
        self.week("2026-11-08")
        self.run_script()
        r = self.run_script(now="2026-11-09T12:00:00+08:00")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(len(self.calls()), 2)                # 第二次什么都没调
        r = self.run_script("--force")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(self.calls()), 3)                # 只发送，没再上传横幅
        self.assertTrue(self.sent()[-1]["--idempotency-key"].startswith("tidings-weekly-2026-11-08-"))
        self.banner.write_bytes(b"png-v2")
        self.reply({"im images create": {"ok": True, "data": {"image_key": "img_banner_2"}}, "+messages-send": OK_SEND})
        self.run_script("--force")
        self.assertIn("images", self.calls()[-2]["args"])
        self.assertEqual(json.loads(self.sent()[-1]["--content"])["body"]["elements"][0]["img_key"], "img_banner_2")

    def test_数据过期或没有文件_只发一句说明_不发旧榜单(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        (msg,) = self.sent()
        self.assertEqual([msg["--msg-type"], json.loads(msg["--content"])["text"], msg["--idempotency-key"]],
                         ["text", "本周周报没有生成：没有找到任何周报文件，榜单可能没更新。", "tidings-weekly-miss-2026-11-08"])
        self.week("2026-11-07")
        self.run_script(now="2026-11-16T11:30:00+08:00")
        self.assertEqual(json.loads(self.sent()[-1]["--content"])["text"], "本周周报没有生成：最新数据停在 11 月 7 日，榜单可能没更新。")
        self.assertFalse(any(c["args"][2:4] == ["im", "images"] for c in self.calls()))
        self.run_script(now="2026-11-16T12:00:00+08:00")       # 同一天再跑不重复说明
        self.assertEqual(len(self.sent()), 2)

    def test_卡片文件为空或坏或缺_发一句说明(self):
        for day, card in (("2026-11-08", ""), ("2026-11-15", "{\"schema\": \"2.0\"}"), ("2026-11-22", None)):
            self.week(day, card)
            nxt = f"2026-11-{int(day[8:]) + 1:02d}T11:30:00+08:00"
            self.assertEqual(self.run_script(now=nxt).returncode, 0)
            self.assertEqual(json.loads(self.sent()[-1]["--content"])["text"], f"本周周报没有生成：11 月 {int(day[8:])} 日的卡片文件为空或读不出来。")

    def test_横幅上传失败_照发不带图并提醒(self):
        self.week("2026-11-08")
        self.reply({"im images create": {"ok": False, "error": {"message": "no permission"}}, "+messages-send": OK_SEND})
        r = self.run_script()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "Tidings 周报已发出，但上传横幅失败：no permission，本期卡片没带图。")
        self.assertEqual(json.loads(self.sent()[0]["--content"]), CARD)

    def test_发送失败_退出码1_下次还会再发_横幅缓存留着(self):
        self.week("2026-11-08")
        self.reply({"im images create": OK_UPLOAD, "+messages-send": {"ok": False, "error": {"message": "rate limited"}}})
        r = self.run_script()
        self.assertEqual((r.returncode, r.stdout.strip()), (1, "Tidings 周报没发出：发送失败：rate limited"))
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual((state["sent"], state["banner"]["image_key"]), ({}, "img_banner_1"))
        self.reply({"+messages-send": OK_SEND})
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(len(self.sent()), 2)

    def test_没有私聊_chat_id_报错不发(self):
        self.week("2026-11-08")
        r = self.run_script(chat="")
        self.assertEqual((r.returncode, r.stdout.strip()), (1, "Tidings 周报没发出：没有私聊 chat_id（FEISHU_HOME_CHANNEL）。"))
        self.assertEqual(self.calls(), [])

    def test_dry_run_只打印不上传不发不记状态(self):
        self.week("2026-11-08")
        r = self.run_script("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), {"msg_type": "interactive", "content": CARD})
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.state.exists())


if __name__ == "__main__":
    unittest.main()
