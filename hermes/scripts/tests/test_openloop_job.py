# -*- coding: utf-8 -*-
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
from test_aihot_hot import FAKE  # noqa: E402  假 lark-cli：按 replies.json 回话，调用记进 calls.jsonl
from test_openloop_slip import CHAT, DECIDE, EVENING, HANDS, MERGE, NOON, ledger  # noqa: E402

# 真扫描要连 GitHub、读各项目，测试里把 openloop.scan 换成假的：按 TEST_SCAN 写账本 / 抛 Fail / 抛别的错，并记一笔「扫过」。
# 用子进程跑，入口和午晚单在导入时读的环境变量（DT_LARK_CLI、DT_ROSTER）才指得到测试目录。
RUNNER = r"""
import json, os, sys
sys.path.insert(0, os.environ["TEST_SCRIPTS"])
import openloop as ol
book = ol.out_dir()

def scan(now=None, **kw):
    with open(book / "scans.log", "a", encoding="utf-8") as f:
        f.write(ol.iso(now) + "\n")
    how = os.environ["TEST_SCAN"]
    if how == "fail":
        raise ol.Fail("读不了项目名册 /x/projects.json")
    if how == "boom":
        raise KeyError("rows")
    (book / "ledger.json").write_text(os.environ["TEST_LEDGER"], encoding="utf-8")

ol.scan = scan
import openloop_job
sys.exit(openloop_job.main())
"""


class Job(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-openloop-job-"))
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        (self.fake / "lark-cli").write_text(FAKE, encoding="utf-8")
        (self.fake / "lark-cli").chmod(0o755)
        self.book = self.tmp / "openloop"
        self.book.mkdir()
        self.roster = self.tmp / "projects.json"
        self.roster.write_text(json.dumps({"projects": [{"id": "dt", "github": "o/dt", "category": "活跃", "path": str(self.tmp)}]}), encoding="utf-8")
        self.reply("🧾 晚间单 · 11-20")

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def reply(self, title, send=True):
        got = {"ok": True, "data": {"messages": [{"msg_type": "interactive", "content": f"<card>{title} …</card>"}]}}
        sent = {"ok": True, "data": {"message_id": "om_slip_1"}} if send else {"ok": False, "error": {"message": "发不出去"}}
        (self.fake / "replies.json").write_text(json.dumps({"+messages-send": sent, "+messages-mget": got}, ensure_ascii=False), encoding="utf-8")

    def run_job(self, scan="ok", rows=(MERGE, HANDS), now=EVENING, net_wait="0"):
        env = {k: v for k, v in os.environ.items() if k != "FEISHU_HOME_CHANNEL"}
        env.update(DT_OPENLOOP_DIR=str(self.book), DT_ROSTER=str(self.roster), DT_LARK_CLI=str(self.fake / "lark-cli"),
                   FAKE_DIR=str(self.fake), DT_NOW=now, HERMES_HOME="/x", DT_HERMES_ENV=str(self.tmp / "hermes.env"),
                   FEISHU_HOME_CHANNEL=CHAT, DT_NET_WAIT_SECONDS=net_wait, DT_NETWAIT_LOG=str(self.tmp / "netwait.jsonl"),
                   TEST_SCRIPTS=str(SCRIPTS), TEST_SCAN=scan,
                   TEST_LEDGER=json.dumps(ledger(list(rows), at=now), ensure_ascii=False))
        return subprocess.run([sys.executable, "-B", "-c", RUNNER], capture_output=True, text=True, env=env, timeout=120)

    def sends(self):
        p = self.fake / "calls.jsonl"
        calls = [json.loads(line)["args"] for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
        return [a for a in calls if "+messages-send" in a]

    def scans(self):
        p = self.book / "scans.log"
        return p.read_text(encoding="utf-8").split() if p.exists() else []

    def runs(self):
        p = self.book / "slips" / "runs.jsonl"
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def test_scans_then_sends_this_slot_silently(self):
        r = self.run_job()
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)        # 成功不输出
        self.assertEqual(self.scans(), [EVENING])                              # 先按「现在」扫了一遍
        (a,) = self.sends()
        card = json.loads(a[a.index("--content") + 1])
        self.assertEqual(card["header"]["title"]["content"], "🧾 晚间单 · 11-20")
        self.assertEqual([x["result"] for x in self.runs()], ["sent"])
        r = self.run_job()                                                     # 同一档再跑：照样扫，单不重发
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual((len(self.scans()), len(self.sends())), (2, 1))
        self.reply("🧾 午间单 · 11-21")
        self.assertEqual(self.run_job(now=NOON).returncode, 0)                 # 第二天中午是另一档
        self.assertEqual(len(self.sends()), 2)

    def test_nothing_hands_on_sends_nothing_and_says_nothing(self):
        r = self.run_job(rows=(DECIDE,))
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.runs()[-1]["result"], "nothing")

    def test_scan_failure_sends_no_slip_from_the_old_ledger(self):
        (self.book / "ledger.json").write_text(json.dumps(ledger([MERGE, HANDS])), encoding="utf-8")   # 上一份还在
        for how, words in (("fail", "读不了项目名册"), ("boom", "KeyError")):
            r = self.run_job(scan=how)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertEqual(len(r.stdout.strip().splitlines()), 1)            # 一句话
            self.assertIn("⚠️ 晚间单没发：账本没扫成", r.stdout)
            self.assertIn(words, r.stdout)
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.runs(), [])

    def test_send_failure_says_why_and_exits_1(self):
        self.reply("x", send=False)
        r = self.run_job()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "晚间单没发成：发卡片失败：发不出去")
        self.assertEqual(self.runs()[-1]["result"], "failed")

    def test_no_network_stops_before_scanning(self):
        env_dead = "http://127.0.0.1:9/"
        os.environ["DT_PROBE_URL_FEISHU"] = env_dead
        self.addCleanup(os.environ.pop, "DT_PROBE_URL_FEISHU", None)
        r = self.run_job(net_wait="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("⚠️ 这一档的晚间单没跑：网络 / 代理", r.stdout)
        self.assertEqual((self.scans(), self.sends()), ([], []))
        rec = json.loads((self.tmp / "netwait.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((rec["kind"], rec["ok"], rec["targets"]), ("openloop-slip", False, ["feishu", "github"]))   # 扫描要 GitHub，发单要飞书


if __name__ == "__main__":
    unittest.main()
