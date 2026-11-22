# -*- coding: utf-8 -*-
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import openloop as ol  # noqa: E402
import openloop_ask as oa  # noqa: E402

SCRIPT = SCRIPTS / "openloop_ask.py"
TZ = ol.TZ
PROJECTS = [{"id": "dt", "github": "o/digital-twin", "category": "活跃", "path": "/x/digital-twin"}]


def at(text):
    return dt.datetime.fromisoformat(text)


def row(id_, what, state="等你", project="digital-twin", stalled=False, links=()):
    return {"id": id_, "what": what, "state": state, "project": project, "owner": "常浩", "next": "常浩定", "stalled": stalled,
            "evidence": "", "evidence_at": "2026-11-20T10:00:00+08:00", "links": [{"id": i, "state": "在做"} for i in links],
            "source": id_.split(":")[0]}


PR = row("pr:digital-twin#211", "0D 恢复门", state="卡住", stalled=True, links=["session:local_s1"])
SLIPPED = row("session:local_s2", "交接卡收尾")
QUIET = row("mainline:digital-twin/#188/选哪条路", "选哪条路")
DAILY = row("session:local_s3", "选方案")
RISKY = row("relay:h1", "#439 智能体列出与结束接力", state="卡住")
PARKED = {"id": "mainline:digital-twin/#188/先放着的", "project": "digital-twin", "what": "先放着的", "mark": "⏸"}


def ledger(rows, generated, parked=()):
    return {"version": 1, "generated_at": generated, "rows": rows, "parked": list(parked), "mismatches": [], "unparsed": [],
            "counts": {}, "coverage": {}}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.book, self.daily, self.merge = self.tmp / "openloop", self.tmp / "daily", self.tmp / "merge"
        (self.book / "history").mkdir(parents=True)
        (self.book / "slips").mkdir()
        self.daily.mkdir()
        env = mock.patch.dict(os.environ, {"DT_OPENLOOP_DIR": str(self.book), "DT_DAILY": str(self.daily),
                                           "DT_MERGE_DIR": str(self.merge), "DT_NOW": "2026-11-22T12:00:00+08:00"})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)], check=False)
        rows = [PR, SLIPPED, QUIET, DAILY, RISKY]
        self.snap("2026-11-20", ledger(rows, "2026-11-20T21:00:00+08:00", [PARKED]))
        self.snap("2026-11-21", ledger(rows, "2026-11-21T21:00:00+08:00", [PARKED]))
        (self.book / "ledger.json").write_text(json.dumps(ledger(rows, "2026-11-21T21:00:00+08:00", [PARKED]), ensure_ascii=False), encoding="utf-8")
        # 11-20 晚间单列了 SLIPPED；11-21 午间单是旧记录（没有 ids），卡片上写着 PR 的「仓库#号」
        self.slip({"slot": "2026-11-20-晚", "at": "2026-11-20T21:00:05+08:00", "result": "sent", "ids": [SLIPPED["id"]], "ids_gone": []})
        self.slip({"slot": "2026-11-21-午", "at": "2026-11-21T12:30:05+08:00", "result": "sent"},
                  card={"body": {"elements": [{"tag": "table", "rows": [{"what": "[digital-twin#211](https://x) 0D 恢复门"}]}]}})
        self.slip({"slot": "2026-11-21-晚", "at": "2026-11-21T21:00:05+08:00", "result": "failed", "ids": [QUIET["id"]]})   # 没发出去的不算
        # 11-21 10:00 的日报：拍板 2 的出处是账本里的 DAILY；风险拿 RISKY 当了例子
        self.write(self.daily / "2026-11-20.delivery.json", {"sent": True, "at": "2026-11-21T10:03:29+08:00"})
        self.write(self.daily / "2026-11-20.decisions.sent.json", {"decisions": [
            {"n": 1, "item": "别的事", "sources": [{"project_id": "dt", "source": "CURRENT", "item": "x"}]},
            {"n": 2, "item": "选方案", "sources": [{"project_id": "dt", "source": f"未闭环账本 {DAILY['id']}", "item": "选方案"}]}]})
        self.write(self.daily / "2026-11-20.facts.json", {"risks": [{"item": "未闭环账本：卡住 2 件、到点没动静（停了）1 件",
                                                                    "evidence": ["digital-twin｜#439 智能体列出与结束接力｜卡在：没写"]}]})

    def snap(self, day, led):
        self.write(self.book / "history" / f"{day}.json", led)

    def write(self, path, data):
        Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def slip(self, rec, card=None):
        with open(self.book / "slips" / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if card:
            self.write(self.book / "slips" / f"{rec['slot']}.card.json", card)

    def asks(self):
        p = self.book / "asks.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


class Judge(Base):
    def verdict(self, target, when="2026-11-22T09:00:00+08:00"):
        return oa.judge(target, at(when), PROJECTS)

    def test_not_in_ledger_is_missed(self):
        got = self.verdict("pr:digital-twin#999")
        self.assertEqual((got["verdict"], got["row"], got["ledger_at"]), ("漏收", None, "2026-11-21T21:00:00+08:00"))
        self.assertEqual(self.verdict(None)["verdict"], "漏收")

    def test_in_ledger_never_reported_is_an_ask(self):
        got = self.verdict(QUIET["id"])
        self.assertEqual((got["verdict"], got["state"], got["reported"]), ("追问", "等你", []))   # 11-21 晚间单列了它，但没发出去

    def test_parked_items_are_never_pushed_so_asking_is_an_ask(self):
        got = self.verdict(PARKED["id"])
        self.assertEqual((got["verdict"], got["state"], got["parked"]), ("追问", "只计数", True))

    def test_slip_with_ids_counts_as_reported(self):
        got = self.verdict(SLIPPED["id"])
        self.assertEqual(got["verdict"], "已报过")
        self.assertEqual(got["reported"][0]["where"], "晚间单 2026-11-20-晚")

    def test_old_slip_without_ids_is_matched_by_card_text_and_merged_ids_count(self):
        got = self.verdict("session:local_s1")   # 会话并在 PR 那一行里：问会话就是问这一行
        self.assertEqual((got["verdict"], got["row"], got["stalled"]), ("已报过", PR["id"], True))
        self.assertEqual([x["where"] for x in got["reported"]], ["午间单 2026-11-21-午"])

    def test_daily_decision_source_and_risk_example_count_as_reported(self):
        self.assertEqual(self.verdict(DAILY["id"])["reported"][0]["where"], "日报 2026-11-20（拍板 2）")
        self.assertEqual(self.verdict(RISKY["id"])["reported"][0]["where"], "日报 2026-11-20（风险例子）")

    def test_daily_pr_matched_by_project_and_number(self):
        self.write(self.daily / "2026-11-20.decisions.sent.json", {"decisions": [
            {"n": 3, "item": "0D 合并", "sources": [{"project_id": "dt", "source": "PR", "item": "PR #211 等合并"}]}]})
        got = self.verdict("pr:digital-twin#211", "2026-11-21T11:00:00+08:00")   # 午间单还没发，日报先报了
        self.assertEqual([x["where"] for x in got["reported"]], ["日报 2026-11-20（拍板 3）"])
        self.assertEqual(self.verdict("pr:digital-twin#21", "2026-11-21T11:00:00+08:00")["verdict"], "漏收")

    def test_only_things_sent_before_the_ask_count(self):
        got = self.verdict(SLIPPED["id"], "2026-11-20T21:00:30+08:00")   # 用的是 11-20 21:00 那份账本，单子 21:00:05 已发
        self.assertEqual(got["verdict"], "已报过")
        got = self.verdict(DAILY["id"], "2026-11-21T09:00:00+08:00")      # 日报 10:03 才发
        self.assertEqual((got["verdict"], got["ledger_at"]), ("追问", "2026-11-20T21:00:00+08:00"))

    def test_no_ledger_before_the_ask(self):
        self.assertIsNone(self.verdict(QUIET["id"], "2026-11-19T09:00:00+08:00"))


class Record(Base):
    def run_script(self, *args):
        env = {**os.environ, "DT_ROSTER": str(self.tmp / "projects.json")}
        self.write(self.tmp / "projects.json", {"projects": PROJECTS})
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def test_feishu_ask_adds_one_line_with_the_right_kind(self):
        r = self.run_script("record", "--question", "0D 怎么样了", "--pr", "digital-twin#211")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已报过", r.stdout)
        r = self.run_script("record", "--question", "选哪条路定了吗", "--id", QUIET["id"])
        self.assertIn("记了：追问", r.stdout)
        r = self.run_script("record", "--question", "那个周报呢", "--text", "工作周报")
        self.assertIn("记了：漏收", r.stdout)
        a = self.asks()
        self.assertEqual([x["verdict"] for x in a], ["已报过", "追问", "漏收"])
        self.assertEqual({x["via"] for x in a} | {x["status"] for x in a}, {"飞书", "确认"})
        self.assertEqual((a[0]["at"], a[0]["target"], a[0]["question"]), ("2026-11-22T12:00:00+08:00", "pr:digital-twin#211", "0D 怎么样了"))
        self.assertEqual((a[2]["target"], a[2]["text"]), (None, "工作周报"))

    def test_bad_input_says_why_and_writes_nothing(self):
        r = self.run_script("record", "--question", "怎么样了")
        self.assertEqual(r.returncode, 1)
        self.assertIn("要么给账本编号", r.stdout)
        r = self.run_script("record", "--question", "x", "--pr", "211")
        self.assertIn("仓库#号", r.stdout)
        r = self.run_script("record", "--question", "x", "--id", QUIET["id"], "--at", "2026-11-19T09:00:00+08:00")
        self.assertIn("之前还没有账本", r.stdout)
        self.assertEqual(self.asks(), [])

    def test_find_lists_matching_rows(self):
        r = self.run_script("find", "选")
        self.assertEqual(r.returncode, 0)
        self.assertIn(QUIET["id"], r.stdout)
        self.assertIn(DAILY["id"], r.stdout)
        self.assertNotIn(PR["id"], r.stdout)
        self.assertIn("没找到", self.run_script("find", "不存在的事").stdout)
        self.assertIn(PR["id"], self.run_script("find", "local_s1").stdout)   # 并进来的会话号也能找到那一行


class FakeInbox(types.SimpleNamespace):
    """decision_inbox 的替身：只要 user_words、PEER、两个目录。"""
    PEER = "Another Claude session sent a message"
    META_DIR = PROJECTS_DIR = "/不存在"

    @staticmethod
    def user_words(entry):
        if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
            return None
        c = entry.get("message", {}).get("content")
        text = c if isinstance(c, str) else "\n".join(b.get("text", "") for b in c or [] if b.get("type") == "text")
        return text.strip() if text.strip() and not text.startswith("<system-reminder>") else None


class Screen(Base):
    def setUp(self):
        super().setUp()
        self.meta, self.logs = self.tmp / "meta", self.tmp / "projects"
        self.meta.mkdir()
        (self.logs / "-x").mkdir(parents=True)
        env = mock.patch.dict(os.environ, {"DT_OPENLOOP_META_DIR": str(self.meta), "DT_OPENLOOP_LOGS_DIR": str(self.logs)})
        env.start()
        self.addCleanup(env.stop)

    def session(self, sid, msgs, cwd="/x/digital-twin/.claude/worktrees/w", spawned=False):
        cli = f"cli-{sid}"
        self.write(self.meta / f"{sid}.json", {"sessionId": sid, "cwd": cwd, "cliSessionId": cli,
                                               **({"spawnedFrom": {"sessionId": "local_parent"}} if spawned else {})})
        lines = [{"type": "user", "uuid": f"u{i}", "timestamp": t, "message": {"content": text}} for i, (t, text) in enumerate(msgs)]
        log = self.logs / "-x" / f"{cli}.jsonl"
        log.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
        t = at("2026-11-22T20:00:00+08:00").timestamp()
        os.utime(log, (t, t))   # 记录最后修改在窗口里（早于窗口起点的不读）

    def screen(self, write=True):
        return oa.screen(at("2026-11-22T00:00:00+08:00"), at("2026-11-23T00:00:00+08:00"), PROJECTS, FakeInbox(), write=write)

    def test_asks_phrases(self):
        for text in ("0D 做到哪了", "进度怎么样", "怎么还没合并", "PR 合并了吗", "还差什么", "这事怎么样了", "卡在哪了",
                     "托管这个任务的最新进展情况，还有什么没做", "汇报工作台的最新进度", "驿窗最近的任务完成情况，都完成了么",
                     "我也不记得进展，向我汇总汇报", "目前这个进度是什么样", "什么进度"):
            self.assertTrue(oa.asks_in(text), text)
        for text in ("帮我把按钮改成蓝色", "删除这件事，进展这两个小标题，直接说就行。", "点击了，目前没反应，你看下呢", "「做到哪了」这句话要加进规程", "```\n做到哪了\n```", "做到哪了" + "。" * 130):
            self.assertFalse(oa.asks_in(text), text)

    def test_screen_marks_unchecked_and_does_not_repeat(self):
        self.session("local_s2", [("2026-11-22T01:00:00Z", "这个做到哪了"), ("2026-11-22T01:05:00Z", "好的，继续"),
                                  ("2026-11-21T01:00:00Z", "进度怎么样")])   # 窗口外
        self.session("local_s9", [("2026-11-22T02:00:00Z", "#211 合并了吗")])
        self.session("local_out", [("2026-11-22T02:00:00Z", "做到哪了")], cwd="/别的目录")   # 不是个人项目
        self.session("local_kid", [("2026-11-22T02:00:00Z", "做到哪了？先汇报一下"), ("2026-11-22T03:00:00Z", "还差什么")], spawned=True)
        got, cover = self.screen()
        self.assertEqual(sorted((x["target"], x["verdict"]) for x in got),
                         [("pr:digital-twin#211", "已报过"), ("session:local_kid", "漏收"), ("session:local_s2", "已报过")])
        self.assertEqual({x["status"] for x in got} | {x["via"] for x in got}, {"待核", "Claude 会话"})
        self.assertEqual(next(x for x in got if x["session"] == "local_kid")["question"], "还差什么")   # 第一条是交来的任务，不算
        self.assertEqual((cover["sessions"], cover["not_personal"]), (3, 1))
        got, cover = self.screen()
        self.assertEqual((got, cover["already"]), ([], 3))
        self.assertEqual(len(self.asks()), 3)

    def test_logs_last_written_before_the_window_are_not_read(self):
        self.session("local_s2", [("2026-11-22T01:00:00Z", "做到哪了")])
        t = at("2026-11-21T20:00:00+08:00").timestamp()
        os.utime(self.logs / "-x" / "cli-local_s2.jsonl", (t, t))
        self.assertEqual(self.screen(write=False)[0], [])

    def test_dry_run_writes_nothing(self):
        self.session("local_s2", [("2026-11-22T01:00:00Z", "做到哪了")])
        got, _ = self.screen(write=False)
        self.assertEqual(len(got), 1)
        self.assertEqual(self.asks(), [])


class Week(Base):
    def test_baseline_numbers(self):
        for i, (verdict, status, stalled) in enumerate([("追问", "确认", True), ("漏收", "确认", False), ("已报过", "确认", False),
                                                        ("追问", "待核", False), ("追问", "确认", False)]):
            with open(self.book / "asks.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": f"2026-11-2{1 if i < 4 else 9}T10:00:00+08:00", "verdict": verdict, "status": status,
                                    "stalled": stalled}, ensure_ascii=False) + "\n")
        self.merge.mkdir()
        (self.merge / "auto-merges.jsonl").write_text(json.dumps({"at": "2026-11-21T10:00:00+08:00", "review": "未核实（第一版）"}) + "\n", encoding="utf-8")
        with mock.patch.object(oa, "evidence_coverage", return_value={"done": 4, "with_evidence": 3, "rate": 0.75, "projects": {}, "failed": {}}):
            w = oa.week(dt.date(2026, 11, 21), PROJECTS)
        m = w["metrics"]
        self.assertEqual(w["window"], ["2026-11-15", "2026-11-21"])
        self.assertEqual((w["ledger"]["snapshots"], w["ledger"]["items"], w["ledger"]["stalled_items"], len(w["ledger"]["missing_days"])),
                         (2, 6, 1, 5))   # ledger.json 和 11-21 快照是同一次扫描，只算一份
        self.assertEqual((m["你主动追问率"]["defects"], m["你主动追问率"]["value"]), (2, round(2 / 6, 3)))   # 11-29 那条在窗口外
        self.assertEqual(m["你主动追问率"]["defects_with_unchecked"], 3)
        self.assertEqual((m["无声中断率"]["silent"], m["无声中断率"]["value"]), (1, 1.0))
        self.assertEqual(m["没核实审查就合并的"]["count"], 1)
        text = oa.render_week(w)
        self.assertIn("你主动追问率：33.3%（2 ÷ 6）", text)
        self.assertIn("结果证据覆盖率：75.0%", text)
        self.assertIn("这几天没有账本快照", text)

    def test_no_merge_ledger_yet(self):
        with mock.patch.object(oa, "evidence_coverage", return_value={"done": 0, "with_evidence": 0, "rate": None, "projects": {}, "failed": {}}):
            w = oa.week(dt.date(2026, 11, 21), PROJECTS)
        self.assertEqual(w["metrics"]["没核实审查就合并的"]["note"], "合并放权还没上线（合并账本不存在）")
        self.assertEqual(w["metrics"]["你主动追问率"]["value"], 0.0)   # 一条追问都没有：0，不是「算不出」
        self.assertIn("一条追问登记都没有", oa.render_week(w))   # 0% 要说清楚可能是入口没装

    def test_evidence_coverage_reads_done_items(self):
        text = "# x\n\n## 主线：甲｜完成 3/4\n- ✅ 一（PR #12）\n- ✅ 二 abc1234\n- ✅ 三\n- ▶ 四\n\n## 主线：甲\n- ✅ 旧的 #1\n"
        with mock.patch.object(ol.pdaily_data, "main_ref", return_value="origin/main"), \
                mock.patch.object(ol.pdaily_data, "show", return_value=text):
            got = oa.evidence_coverage(PROJECTS)
        self.assertEqual((got["done"], got["with_evidence"], got["rate"]), (3, 2, 0.667))


class Job(Base):
    """每周一 09:00 的入口：先粗筛上一周、再出基线，打印给定时任务发私聊。"""

    def run_job(self, inbox):
        self.write(self.tmp / "projects.json", {"projects": PROJECTS})
        env = {**os.environ, "DT_ROSTER": str(self.tmp / "projects.json"), "DT_NOW": "2026-11-23T09:00:00+08:00",
               "DT_DECISION_INBOX": str(inbox), "DT_OPENLOOP_META_DIR": str(self.tmp / "meta"), "DT_OPENLOOP_LOGS_DIR": str(self.tmp / "logs")}
        return subprocess.run([sys.executable, "-B", str(SCRIPTS / "openloop_ask_job.py")], capture_output=True, text=True, env=env, timeout=60)

    def test_weekly_job_prints_baseline_and_saves_it(self):
        inbox = self.tmp / "decision_inbox.py"
        inbox.write_text("META_DIR = PROJECTS_DIR = '/不存在'\nPEER = 'x'\ndef user_words(e):\n    return None\n", encoding="utf-8")
        (self.tmp / "meta").mkdir()
        r = self.run_job(inbox)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("未闭环账本 · 一周基线｜2026-11-16 到 2026-11-22", r.stdout)
        self.assertIn("Claude 会话粗筛：像追问的 0 句", r.stdout)
        self.assertTrue((self.book / "weekly" / "2026-11-22.json").exists())

    def test_screen_failure_does_not_block_the_baseline(self):
        r = self.run_job(self.tmp / "没有这个脚本.py")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("一周基线", r.stdout)
        self.assertIn("⚠️ Claude 会话粗筛没跑成（找不到拍板清单脚本", r.stdout)


REAL_INBOX = _isolation.REAL_HOME / "Documents/ChatGPT/个人/work/my-skills/skills/decision-inbox/scripts/decision_inbox.py"


@unittest.skipUnless(REAL_INBOX.exists(), "本机没有拍板清单脚本（CI 上没有 my-skills），跳过对真脚本的核对")
class RealInbox(Screen):
    def screen(self, write=True):
        with mock.patch.dict(os.environ, {"DT_DECISION_INBOX": str(REAL_INBOX)}):
            return oa.screen(at("2026-11-22T00:00:00+08:00"), at("2026-11-23T00:00:00+08:00"), PROJECTS, write=write)

    def test_real_user_words_skips_injected_text(self):
        self.session("local_s2", [("2026-11-22T01:00:00Z", "<system-reminder>做到哪了</system-reminder>"),
                                  ("2026-11-22T01:01:00Z", "Another Claude session sent a message: 做到哪了"),
                                  ("2026-11-22T01:02:00Z", "做到哪了")])
        got, _ = self.screen()
        self.assertEqual([x["question"] for x in got], ["做到哪了"])


if __name__ == "__main__":
    unittest.main()
