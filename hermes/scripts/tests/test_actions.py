"""actions.py 自测：临时目录假造六个来源，不读真实数据。统计日 D = 2026-11-08（04:00 到 11-09 04:00，北京时间）。"""
import csv
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import actions  # noqa: E402
import progress_data  # noqa: E402

D = dt.date(2026, 11, 8)
SCRIPT = Path(__file__).resolve().parent.parent / "actions.py"
CSV_FIELDS = ("evaluated_at,source,repo,pr,title,head_sha,base_sha,rules_version,tier,suggestion,reasons,"
              "outcome,outcome_at,outcome_head,agree,owner,owner_at").split(",")
SHA = "a" * 40


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-actions-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.p = {k: self.root / k for k in ("shadow", "merge-runs", "dispatch", "cron", "runs", "evidence")}
        for d in self.p.values():
            d.mkdir()
        self.actions_file = self.root / "actions.jsonl"
        roster = self.root / "projects.json"
        roster.write_text(json.dumps({"projects": [{"id": "twin", "github": "o/digital-twin"}]}))
        patches = [mock.patch.object(actions, "ACTIONS", self.actions_file),
                   mock.patch.object(actions, "SHADOW", self.p["shadow"]),
                   mock.patch.object(actions, "MERGE_RUNS", self.p["merge-runs"]),
                   mock.patch.object(actions, "NIGHT_ROOT", self.p["runs"]),
                   mock.patch.object(actions, "EVIDENCE", self.p["evidence"]),
                   mock.patch.object(actions, "CRON", self.p["cron"]),
                   mock.patch.object(progress_data, "DISPATCH", self.p["dispatch"]),
                   mock.patch.object(progress_data, "ROSTER", roster)]
        for x in patches:
            x.start()
            self.addCleanup(x.stop)
        self.ledger([])
        self.cron_db()

    # ---- 假造来源 ----
    def ledger(self, rows):
        with (self.p["shadow"] / "agreement.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in CSV_FIELDS})

    def row(self, at, **kw):
        base = {"evaluated_at": at, "source": "live", "repo": "o/digital-twin", "pr": "5", "title": "做甲", "head_sha": SHA,
                "rules_version": "1", "tier": "L1", "suggestion": "建议合并"}
        return {**base, **kw}

    def cron_db(self, execs=(), incs=(), jobs=None):
        db = self.p["cron"] / "executions.db"
        if db.exists():
            db.unlink()
        con = sqlite3.connect(db)
        con.execute("create table executions (id text, job_id text, status text, claimed_at text, delivery_outcome text, error text)")
        con.execute("create table cron_incidents (id text, job_id text, state text, failure_type text, first_seen_at text, "
                    "last_seen_at text, alerted_at text, error text)")
        con.executemany("insert into executions values (?,?,?,?,?,?)", execs)
        con.executemany("insert into cron_incidents values (?,?,?,?,?,?,?,?)", incs)
        con.commit()
        con.close()
        (self.p["cron"] / "jobs.json").write_text(json.dumps(jobs if jobs is not None else {"jobs": [{"id": "j1", "name": "巡检"}]}))

    def order(self, no, created, mode="read", receipt=None, task="查一下 X\n细节"):
        d = self.p["dispatch"] / no
        (d / "out").mkdir(parents=True)
        (d / "order.json").write_text(json.dumps({"no": no, "project": "twin", "tool": "codex", "mode": mode,
                                                  "status": "done", "created": created}))
        (d / "task.md").write_text(task)
        if receipt is not None:
            (d / "out" / "receipt.json").write_text(json.dumps(receipt))

    def night_log(self, name, rows):
        d = self.p["runs"] / name
        d.mkdir()
        (d / "run-log.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    def run_day(self, day=D):
        return actions.collect(day)

    def by_origin(self, out, origin):
        return [a for a in out["actions"] if a["origin"] == origin]


class RecordTests(Base):
    def test_record_fills_id_and_time_and_appends_one_line(self):
        rec = actions.record(kind="install", request="装 pdaily_data.py", actor="Claude", auth_tier="D",
                             auth_source="飞书私聊 om_1", decision="执行", result="业务已验证", verification="cmp 一致",
                             ts="2026-11-08T10:00:00")
        self.assertEqual(rec["ts"], "2026-11-08T10:00:00+08:00")        # 没写时区按北京时间
        self.assertTrue(rec["action_id"].startswith("install-"))
        self.assertEqual(list(rec), list(actions.FIELDS))
        lines = self.actions_file.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0]), rec)

    def test_bad_records_are_refused_with_the_field_named(self):
        ok = dict(kind="other", request="r", actor="Hermes", decision="执行", result="调用成功")
        for bad, word in ((dict(ok, kind="安装"), "kind"), (dict(ok, result="成功"), "result"), (dict(ok, auth_tier="E"), "auth_tier"),
                          (dict(ok, result=None), "result"), (dict(ok, color="红"), "color"), (dict(ok, actor=""), "actor")):
            with self.assertRaises(ValueError) as e:
                actions.record(**bad)
            self.assertIn(word, str(e.exception))
        self.assertFalse(self.actions_file.exists())
        actions.record(**dict(ok, decision="拒绝", result=None))      # 拒绝可以没有 result

    def test_cli_record_and_bad_json(self):
        env = {**os.environ, "DT_ACTIONS": str(self.actions_file)}
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), "record", '{"kind":"other","request":"r","actor":"Hermes",'
                            '"decision":"执行","result":"调用成功"}'], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["kind"], "other")
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), "record", "不是 JSON"], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("没写", r.stdout)
        self.assertEqual(len(self.actions_file.read_text().splitlines()), 1)

    def test_file_records_cross_midnight_and_bad_lines(self):
        actions.record(kind="other", request="凌晨", actor="Hermes", decision="执行", result="调用成功", ts="2026-11-09T00:09:00+08:00")
        actions.record(kind="other", request="第二天", actor="Hermes", decision="执行", result="调用成功", ts="2026-11-09T04:00:00+08:00")
        with self.actions_file.open("a") as f:
            f.write("坏行\n" + json.dumps({"kind": "other"}) + "\n")
        out = self.run_day()
        self.assertEqual([a["request"] for a in self.by_origin(out, "actions.jsonl")], ["凌晨"])   # 00:09 算前一个统计日
        self.assertEqual(len([n for n in out["notes"] if n.startswith("actions.jsonl 第")]), 2)
        self.assertTrue(out["coverage"]["actions.jsonl"]["ok"])

    def test_c_and_d_authorization_cross(self):
        base = dict(request="改定时任务 / 安装", actor="Hermes", decision="执行", result="调用成功")
        actions.record(kind="cron", auth_tier="C", auth_source="evidence/x/hermes-72-开跑授权.md", ts="2026-11-08T10:00:00+08:00", **base)
        actions.record(kind="install", auth_tier=None, ts="2026-11-08T10:01:00+08:00", **base)
        actions.record(kind="install", auth_tier="D", auth_source="飞书私聊 om_2", ts="2026-11-08T10:02:00+08:00", **base)
        actions.record(kind="cron", auth_tier="C", decision="拒绝", result=None, ts="2026-11-08T10:03:00+08:00",
                       **{k: v for k, v in base.items() if k not in ("decision", "result")})
        gaps = [a["auth_gap"] for a in self.by_origin(self.run_day(), "actions.jsonl")]
        self.assertEqual(gaps, ["授权不足：这类动作要 D 档，记录是 C 档", "授权不足：这类动作要 D 档，记录是「不知道」", None, None])

    def test_file_record_wins_over_adapter_with_same_id(self):
        self.ledger([self.row("2026-11-08T10:00:00+08:00")])
        actions.record(action_id=f"shadow:o/digital-twin#5@{SHA[:12]}", kind="ruling", request="脚本直接写的", actor="Hermes",
                       decision="执行", result="调用成功", ts="2026-11-08T10:00:00+08:00")
        out = self.run_day()
        same = [a for a in out["actions"] if a["action_id"].startswith("shadow:")]
        self.assertEqual([(a["origin"], a["request"]) for a in same], [("actions.jsonl", "脚本直接写的")])
        self.assertEqual(out["coverage"]["合并官"]["count"], 0)


class MergeOfficerTests(Base):
    def test_shadow_rulings_dedupe_by_pr_and_version(self):
        self.ledger([
            self.row("2026-11-08T09:00:00+08:00"),
            self.row("2026-11-08T09:30:00+08:00", rules_version="2", suggestion="问常浩", reasons="碰了 AGENTS"),
            self.row("2026-11-09T09:00:00+08:00", suggestion="建议合并"),                        # 统计日结束后的不看
            self.row("2026-11-08T10:00:00+08:00", head_sha="b" * 40),                            # 新版本另算一条
            self.row("2026-11-07T23:00:00+08:00", pr="6", head_sha="c" * 40),                    # 前一天第一次判的：不再算
            self.row("2026-11-08T11:00:00+08:00", pr="6", head_sha="c" * 40),
            self.row("2026-11-08T07:00:00+08:00", source="backfill", pr="1", suggestion="问常浩"),   # 回填：合成一条
            self.row("2026-11-08T07:00:01+08:00", source="backfill", pr="2", repo="o/other"),
            self.row("2026-11-07T07:00:00+08:00", source="backfill", pr="3"),
        ])
        out = self.run_day()
        rs = [a for a in self.by_origin(out, "合并官") if a["action_id"].startswith("shadow:")]
        self.assertEqual(len(rs), 2)
        bf = [a for a in self.by_origin(out, "合并官") if a["action_id"].startswith("shadow-backfill:")]
        self.assertEqual([(a["request"], a["follow_up"]) for a in bf], [("合并官回填历史 PR 判定 2 条（2 个仓库，事后判定）", None)])
        a = rs[0]
        self.assertEqual((a["ts"], a["project_id"], a["auth_tier"], a["decision"], a["result"]),
                         ("2026-11-08T09:00:00+08:00", "twin", "A", "执行", "调用成功"))
        self.assertIn("问常浩：碰了 AGENTS", a["verification"])
        self.assertIn("共判定 2 次", a["verification"])
        self.assertEqual(a["follow_up"], "常浩决定合不合并")
        self.assertEqual(rs[1]["request"].split("@")[1][:7], "bbbbbbb")
        prev = [a for a in self.by_origin(self.run_day(D - dt.timedelta(days=1)), "合并官") if a["action_id"].startswith("shadow:")]
        self.assertEqual([a["request"].split("#")[1][:1] for a in prev], ["6"])   # PR 6 这个版本只出现在 11-07

    def test_owner_clicks_are_tier_b(self):
        self.ledger([
            self.row("2026-11-08T09:00:00+08:00", owner="合并", owner_at="2026-11-08T09:05:00+08:00",
                     outcome="合并", outcome_at="2026-11-08T01:06:00Z", outcome_head="d" * 40),
            self.row("2026-11-08T09:00:00+08:00", pr="7", owner="合并", owner_at="2026-11-08T09:10:00+08:00"),
            self.row("2026-11-08T09:00:00+08:00", pr="8", owner="退回", owner_at="2026-11-08T09:20:00+08:00"),
        ])
        own = [a for a in self.by_origin(self.run_day(), "合并官") if a["action_id"].startswith("owner:")]
        self.assertEqual([(a["kind"], a["auth_tier"], a["result"], a["auth_gap"]) for a in own],
                         [("merge", "B", "业务已验证", None), ("merge", "B", "不明", None), ("other", "B", "调用成功", None)])
        self.assertIn("ddddddd", own[0]["verification"])

    def test_missing_ledger_is_unknown_not_zero_and_others_still_run(self):
        (self.p["shadow"] / "agreement.csv").unlink()
        actions.record(kind="other", request="r", actor="Hermes", decision="执行", result="调用成功", ts="2026-11-08T10:00:00+08:00")
        out = self.run_day()
        self.assertEqual(out["coverage"]["合并官"]["ok"], False)
        self.assertIsNone(out["coverage"]["合并官"]["count"])
        self.assertIn("找不到", out["coverage"]["合并官"]["error"])
        self.assertEqual(out["coverage"]["actions.jsonl"]["count"], 1)
        self.assertTrue(out["coverage"]["派单台账"]["ok"])


class MergeRunTests(Base):
    def test_merge_run_dirs(self):
        d = self.p["merge-runs"] / "digital-twin-5-aaaaaaa-20261108-100000"
        d.mkdir()
        (d / "test.log").write_text("$ make test\n...\nOK\n")
        (self.p["merge-runs"] / "怪目录").mkdir()
        (self.p["merge-runs"] / "digital-twin-6-bbbbbbb-20261109-050000").mkdir()   # 第二天
        out = self.run_day()
        rs = self.by_origin(out, "merge-runs")
        self.assertEqual([(a["ts"], a["project_id"], a["result"]) for a in rs], [("2026-11-08T10:00:00+08:00", "twin", "不明")])
        self.assertIn("最后一行：OK", rs[0]["verification"])
        self.assertIn("merge-runs 里认不出的目录：怪目录", out["notes"])


class DispatchTests(Base):
    def test_refused_dispatch_has_request_reason_and_source(self):
        self.order("D20261108-1", "2026-11-08T10:00:00", mode="write",
                   receipt={"tool": "claude", "executor": "Claude Code", "refused": "executor_unavailable", "reason": "claude 没登录"})
        rs = self.by_origin(self.run_day(), "派单台账")
        a = rs[0]
        self.assertEqual((a["kind"], a["actor"], a["decision"], a["result"], a["auth_gap"]), ("refuse", "Hermes", "拒绝", None, None))
        self.assertIn("查一下 X", a["request"])
        self.assertIn("executor_unavailable：claude 没登录", a["verification"])
        self.assertIn("D20261108-1", a["auth_source"])

    def test_dispatch_results_and_tiers(self):
        ok = {"executor": "Codex", "exit_code": 0, "timed_out": False, "worker_error": False, "committed_files": ["a"], "uncommitted": []}
        self.order("D20261108-2", "2026-11-08T10:00:00", receipt=ok)
        self.order("D20261108-3", "2026-11-08T11:00:00", mode="write",
                   receipt={**ok, "exit_code": 1, "need_decision": "要不要删旧表", "guard_main_changed": True})
        self.order("D20261108-4", "2026-11-08T12:00:00")                              # 没回执
        self.order("D20261109-1", "2026-11-09T05:00:00", receipt=ok)                   # 第二天
        rs = self.by_origin(self.run_day(), "派单台账")
        self.assertEqual([(a["actor"], a["auth_tier"], a["result"]) for a in rs],
                         [("Codex", "A", "调用成功"), ("Codex", None, "失败"), ("Codex", "A", "不明")])
        self.assertEqual(rs[1]["follow_up"], "常浩决定：要不要删旧表")
        self.assertIn("护栏", rs[1]["verification"])


class CronTests(Base):
    def test_executions_and_incidents(self):
        self.cron_db(execs=[("e1", "j1", "completed", "2026-11-08T10:00:00.123456+08:00", "suppressed", None),
                            ("e0", "j1", "completed", "2026-11-08T10:00:00.1+08:00", "suppressed", None),   # 3.9 认不出：跳过
                            ("e2", "j1", "failed", "2026-11-08T11:00:00+08:00", "delivered", "Script exited with code 1\nstdout"),
                            ("e3", "jx", "unknown", "2026-11-08T12:00:00+08:00", None, None),
                            ("e4", "j1", "completed", "2026-11-09T04:00:00+08:00", "delivered", None)],
                     incs=[("i1", "j1", "open", "config", "2026-11-08T11:00:00+08:00", "2026-11-08T11:00:00+08:00",
                            "2026-11-08T11:01:00+08:00", "Script exited with code 1"),
                           ("i2", "j1", "closed", "config", "2026-11-07T11:00:00+08:00", "2026-11-08T11:00:00+08:00", None, None)])
        out = self.run_day()
        self.assertTrue(any(n.startswith("定时任务执行 e0 时间认不出") for n in out["notes"]) or sys.version_info >= (3, 11))
        rs = self.by_origin(out, "定时任务库")
        runs = [a for a in rs if a["kind"] == "cron" and a["action_id"] != "cron:e0"]
        self.assertEqual([(a["result"], a["auth_gap"]) for a in runs], [("调用成功", None), ("失败", None), ("不明", None)])
        self.assertIn("「巡检」", runs[0]["request"])
        self.assertIn("jx", runs[2]["request"])
        self.assertIn("Script exited with code 1", runs[1]["verification"])
        self.assertNotIn("stdout", runs[1]["verification"])
        inc = [a for a in rs if a["kind"] == "supervise"]
        self.assertEqual([(a["decision"], a["result"], a["follow_up"]) for a in inc], [("升级给常浩", None, "处理这张故障单")])
        self.assertEqual(sorted(os.listdir(self.p["cron"])), ["executions.db", "jobs.json"])   # 只读：没留下 -wal、-shm

    def test_bad_jobs_file_is_a_note(self):
        self.cron_db(execs=[("e1", "j1", "completed", "2026-11-08T10:00:00+08:00", "delivered", None)], jobs=["坏"])
        out = self.run_day()
        self.assertIn("j1", self.by_origin(out, "定时任务库")[0]["request"])
        self.assertTrue(any("定时任务名读不了" in n for n in out["notes"]))

    def test_broken_db_is_unknown(self):
        (self.p["cron"] / "executions.db").write_text("不是数据库")
        out = self.run_day()
        self.assertEqual((out["coverage"]["定时任务库"]["ok"], out["coverage"]["定时任务库"]["count"]), (False, None))


class NightTests(Base):
    def test_events_tiers_and_readback(self):
        self.night_log("2026-11-08-夜跑", [
            {"ts": "2026-11-08T13:00:00Z", "event": "plan", "issues": {}},
            {"ts": "2026-11-08T13:01:00Z", "event": "approve", "by": "Hermes（常浩开跑授权）", "start_at": "x", "hours": 2, "max_runs": 3},
            {"ts": "2026-11-08T14:00:00Z", "event": "start", "approved_by": "Hermes（常浩开跑授权）", "deadline": "y", "limits": {"runs": 3}},
            {"ts": "2026-11-08T14:05:00Z", "event": "run", "issue": "SBX-30", "stage": 1, "attempt": 1, "status": "completed",
             "issue_status": "in_review", "pr_url": "https://x/pull/1", "error": None},
            {"ts": "2026-11-08T14:06:00Z", "event": "run", "issue": "SBX-31", "stage": 2, "attempt": 1, "status": "cancelled",
             "issue_status": "blocked", "error": "停机取消"},
            {"ts": "2026-11-08T14:10:00Z", "event": "stop_readback", "ok": True, "errors": [], "left": 0},
            {"ts": "2026-11-08T14:11:00Z", "event": "stopped", "outcome": "STOPPED", "reasons": ["到上限"]},
            {"ts": "2026-11-08T15:00:00Z", "event": "release", "by": "Claude（按 hermes-83）"},
        ])
        rs = self.by_origin(self.run_day(), "夜跑")
        self.assertEqual([(a["kind"], a["auth_tier"], a["result"]) for a in rs], [
            ("supervise", "C", "调用成功"), ("supervise", "C", "已发起"), ("dispatch", "C", "调用成功"),
            ("dispatch", "C", "不明"), ("supervise", "C", "业务已验证"), ("supervise", None, "调用成功")])
        self.assertTrue(all(a["auth_gap"] is None for a in rs))
        self.assertEqual(rs[3]["follow_up"], "常浩处理受阻的子单")
        self.assertIn("停机取消", rs[3]["verification"])

    def test_start_without_approver_is_a_gap_and_old_format_is_kept(self):
        self.night_log("无批准", [{"ts": "2026-11-08T14:00:00Z", "event": "start", "deadline": "y", "limits": {}},
                                {"ts": "2026-11-08T14:11:00Z", "event": "stopped", "outcome": "STOPPED", "reasons": []}])
        self.night_log("SBX-9", [{"issue": "SBX-9", "stage": None, "attempt": 1, "status": "completed", "issue_status": "done",
                                  "initiator": "常浩（本地体验）", "created_at": "2026-11-08T03:00:00Z", "completed_at": "2026-11-08T03:01:00Z"}])
        with (self.p["runs"] / "SBX-9" / "run-log.jsonl").open("a") as f:
            f.write("坏行\n")
        out = self.run_day()
        rs = self.by_origin(out, "夜跑")
        self.assertEqual([(a["auth_tier"], a["result"], bool(a["auth_gap"])) for a in rs],
                         [(None, "调用成功", False), (None, "已发起", True), (None, "不明", True)])
        self.assertEqual(rs[0]["auth_source"], "常浩（本地体验）")
        self.assertIn("夜跑 SBX-9 第 2 行不是 JSON，跳过", out["notes"])


class RulingTests(Base):
    def test_rulings_are_quoted_not_judged(self):
        d = self.p["evidence"] / "2026-11-08-M5首夜"
        d.mkdir()
        f1 = d / "hermes-90-时间预算复核裁决.md"
        f1.write_text("第4项：不批准 —— 请求来自 Claude 会话。\n\n额外注意：保持暂停。\n")
        f2 = d / "hermes-80-全权授权.md"
        f2.write_text("常浩授权……\n")
        f3 = d / "hermes-1-旧.md"
        f3.write_text("旧的\n")
        for f, when in ((f1, "2026-11-08T12:00:00+08:00"), (f2, "2026-11-08T13:00:00+08:00"), (f3, "2026-11-07T12:00:00+08:00")):
            t = dt.datetime.fromisoformat(when).timestamp()
            os.utime(f, (t, t))
        rs = self.by_origin(self.run_day(), "裁决文件")
        self.assertEqual([(a["kind"], a["decision"], a["result"], a["auth_gap"]) for a in rs],
                         [("ruling", "不明", "不明", None), ("other", "不明", "不明", None)])
        self.assertIn("时间预算复核裁决", rs[0]["request"])
        self.assertIn("首段原文：第4项：不批准", rs[0]["verification"])
        self.assertNotIn("额外注意", rs[0]["verification"])
        self.assertEqual(rs[0]["auth_source"], "2026-11-08-M5首夜/hermes-90-时间预算复核裁决.md")


class OutputTests(Base):
    def test_output_shape_and_order(self):
        actions.record(kind="other", request="晚", actor="Hermes", decision="执行", result="调用成功", ts="2026-11-08T20:00:00+08:00")
        self.ledger([self.row("2026-11-08T09:00:00+08:00")])
        out = self.run_day()
        self.assertEqual((out["stat_day"], out["window"]), ("2026-11-08", ["2026-11-08T04:00:00+08:00", "2026-11-09T04:00:00+08:00"]))
        self.assertEqual([a["origin"] for a in out["actions"]], ["合并官", "actions.jsonl"])        # 按时间排
        self.assertEqual(set(out["actions"][0]), set(actions.FIELDS) | {"origin", "auth_gap"})
        self.assertEqual(list(out["coverage"]), ["actions.jsonl", "合并官", "merge-runs", "派单台账", "定时任务库", "夜跑", "裁决文件"])

    def test_cli_bad_date(self):
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--date", "2026-13-01"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("日期写法不对", r.stdout)


if __name__ == "__main__":
    unittest.main()
