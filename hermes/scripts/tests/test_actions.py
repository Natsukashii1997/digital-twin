"""actions.py 自测：临时目录假造六个来源，不读真实数据。统计日 D = 2026-11-08（04:00 到 11-09 04:00，北京时间）。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
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
                   mock.patch.object(actions, "MERGE_BOOK", self.root / "merge" / "auto-merges.jsonl"),
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
                    "last_seen_at text, alerted_at text, closed_at text, error text)")
        con.executemany("insert into executions values (?,?,?,?,?,?)", execs)
        con.executemany("insert into cron_incidents values (?,?,?,?,?,?,?,?,?)", incs)
        con.commit()
        con.close()
        (self.p["cron"] / "jobs.json").write_text(json.dumps(jobs if jobs is not None else {"jobs": [{"id": "j1", "name": "巡检"}]}))

    def order(self, no, created, mode="read", receipt=None, task="查一下 X\n细节", status="done", started=None):
        d = self.p["dispatch"] / no
        (d / "out").mkdir(parents=True)
        (d / "order.json").write_text(json.dumps({"no": no, "project": "twin", "tool": "codex", "mode": mode,
                                                  "status": status, "created": created, "run_started": started}))
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

    def test_partial_line_null_id_and_duplicates(self):
        self.actions_file.write_text('{"kind": "oth')                      # 上一个写的人被杀，留下半行
        a = actions.record(action_id=None, kind="other", request="r", actor="Hermes", decision="执行", result="调用成功",
                           ts="2026-11-08T10:00:00+08:00")
        self.assertTrue(a["action_id"].startswith("other-"))
        actions.record(action_id=a["action_id"], kind="other", request="又一条", actor="Hermes", decision="执行",
                       result="调用成功", ts="2026-11-08T11:00:00+08:00")
        out = self.run_day()
        self.assertEqual([x["request"] for x in self.by_origin(out, "actions.jsonl")], ["r"])
        self.assertTrue(any("action_id 重复" in n for n in out["notes"]))
        self.assertTrue(any(n.startswith("actions.jsonl 第 1 行跳过") for n in out["notes"]))
        with self.assertRaises(ValueError):
            actions.record(kind="other", request="r", actor="Hermes", decision="执行", result="调用成功", ts=123)
        with self.assertRaises(ValueError):
            actions.record(action_id=["x"], kind="other", request="r", actor="Hermes", decision="执行", result="调用成功")
        for bad in (dict(project_id=["twin"]), dict(verification={"a": 1}), dict(follow_up=3), dict(actor=["Hermes"])):
            with self.assertRaises(ValueError):
                actions.record(**{**dict(kind="other", request="r", actor="Hermes", decision="执行", result="调用成功"), **bad})
        with self.actions_file.open("a") as f:
            f.write(json.dumps({"action_id": ["x"], "ts": "2026-11-08T12:00:00+08:00", "kind": "other", "request": "r",
                                "actor": "Hermes", "decision": "执行", "result": "调用成功"}) + "\n")
        out = self.run_day()
        self.assertTrue(out["coverage"]["actions.jsonl"]["ok"])            # 一条坏记录不让整个文件永久取不到
        self.assertEqual(out["coverage"]["actions.jsonl"]["count"], 1)

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
        self.assertIsNone(a["follow_up"])                        # 问常浩，但 10:00 有了新版本：旧版本不再要常浩决定
        self.assertEqual(rs[1]["request"].split("@")[1][:7], "bbbbbbb")
        prev = [a for a in self.by_origin(self.run_day(D - dt.timedelta(days=1)), "合并官") if a["action_id"].startswith("shadow:")]
        self.assertEqual([a["request"].split("#")[1][:1] for a in prev], ["6"])   # PR 6 这个版本只出现在 11-07

    def test_follow_up_only_for_latest_version_without_outcome(self):
        self.ledger([
            self.row("2026-11-08T09:00:00+08:00", suggestion="问常浩"),
            self.row("2026-11-08T09:00:00+08:00", pr="6", tier="L3"),
            self.row("2026-11-08T09:30:00+08:00", pr="6", tier="L3", outcome="合并", outcome_at="2026-11-08T03:00:00Z"),
            self.row("2026-11-08T09:00:00+08:00", pr="7", suggestion="问常浩", owner="退回", owner_at="2026-11-08T09:10:00+08:00"),
        ])
        rs = {a["request"].split("#")[1][:1]: a for a in self.by_origin(self.run_day(), "合并官") if a["kind"] == "ruling"}
        self.assertEqual(rs["5"]["follow_up"], "常浩决定合不合并")
        self.assertIsNone(rs["6"]["follow_up"])
        self.assertIn("PR 结局：合并", rs["6"]["verification"])
        self.assertIsNone(rs["7"]["follow_up"])                     # 常浩点过退回：PR 还开着，但不再要他决定

    def test_bad_rows_are_skipped_not_fatal(self):
        path = self.p["shadow"] / "agreement.csv"
        self.ledger([self.row("2026-11-08T09:00:00+08:00")])
        with path.open("a", encoding="utf-8") as f:
            f.write("2026-11-08T09:00:00+08:00,live,o/x\n")                  # 列数不够
            f.write("坏时间,live,o/x,9,t," + SHA + "\n")
        out = self.run_day()
        self.assertTrue(out["coverage"]["合并官"]["ok"])
        self.assertEqual(out["coverage"]["合并官"]["count"], 1)
        self.assertEqual(len([n for n in out["notes"] if n.startswith("agreement.csv 第")]), 2)

    def test_owner_clicks_are_tier_b(self):
        self.ledger([
            self.row("2026-11-08T09:00:00+08:00", owner="合并", owner_at="2026-11-08T09:05:00+08:00",
                     outcome="合并", outcome_at="2026-11-08T01:06:00Z", outcome_head="d" * 40),
            self.row("2026-11-08T09:00:00+08:00", pr="7", owner="合并", owner_at="2026-11-08T09:10:00+08:00"),
            self.row("2026-11-08T09:00:00+08:00", pr="8", owner="退回", owner_at="2026-11-08T09:20:00+08:00"),
        ])
        own = [a for a in self.by_origin(self.run_day(), "合并官") if a["action_id"].startswith("owner:")]
        self.assertEqual([(a["kind"], a["auth_tier"], a["result"], a["auth_gap"]) for a in own],
                         [("merge", "B", "业务已验证", None), ("merge", "B", "业务已验证", None), ("other", "B", "调用成功", None)])
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


class PolicyMergeTests(Base):
    """按策略合并的账本（#257）：合上的记 A 档 merge，结果不明的带后续，绕过的只进事实快照。"""

    def book(self, *recs):
        f = actions.MERGE_BOOK
        f.parent.mkdir(exist_ok=True)
        f.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")

    def intent(self, mid, at, **kw):
        return {"event": "intent", "id": mid, "at": at, "repo": "o/digital-twin", "pr": "7", "title": "改文档", "head": SHA,
                "tier": "L1", "suggestion": "退回", "reasons": "正文没有 Closes #号", "ready": {"by": "会话"},
                "auth": {"type": "owner_approved_policy", "scope_sha256": "ab" * 32, "message_id": "om_x",
                         "signed_at": "2026-11-07T09:00:00+08:00"}, **kw}

    def test_merged_is_tier_a_without_gap(self):
        self.book(self.intent("m1", "2026-11-08T10:00:00+08:00"),
                  {"event": "merged", "id": "m1", "at": "2026-11-08T10:00:05+08:00", "merge_commit": "f" * 40},
                  self.intent("m0", "2026-11-07T10:00:00+08:00", pr="6"),                      # 前一天合的不算
                  {"event": "merged", "id": "m0", "at": "2026-11-07T10:00:05+08:00", "merge_commit": "e" * 40},
                  self.intent("m2", "2026-11-08T11:00:00+08:00", pr="8"),                      # 没合上的不算行动
                  {"event": "failed", "id": "m2", "at": "2026-11-08T11:00:05+08:00", "why": "x"})
        (a,) = [x for x in self.run_day()["actions"] if x["origin"] == "按策略合并"]
        self.assertEqual((a["action_id"], a["kind"], a["auth_tier"], a["result"], a["auth_gap"], a["project_id"]),
                         (f"policy:o/digital-twin#7@{SHA[:12]}", "merge", "A", "业务已验证", None, "twin"))
        self.assertIn("owner_approved_policy", a["auth_source"])
        for word in ("合并提交 fffffff", "L1 退回（正文没有 Closes #号）", "会话交出", "审查结论未核实", "撤回 digital-twin#7"):
            self.assertIn(word, a["verification"])

    def test_unsettled_has_follow_up(self):
        self.book(self.intent("m1", "2026-11-08T10:00:00+08:00"),
                  {"event": "unsure", "id": "m1", "at": "2026-11-08T10:00:05+08:00", "why": "回读失败"})
        (a,) = [x for x in self.run_day()["actions"] if x["origin"] == "按策略合并"]
        self.assertEqual((a["result"], a["follow_up"]), ("不明", "合并官下一轮巡视只读核对，不重发"))

    def test_merged_of_unknown_source_is_not_a_policy_merge(self):
        """事后只读核对到 PR 已合并、证明不了是这次合的（#260-P1-02）：不算合上的，只在核对那天报一条「不明」。"""
        self.book(self.intent("m1", "2026-11-08T10:00:00+08:00"),
                  {"event": "unsure", "id": "m1", "at": "2026-11-08T10:00:05+08:00", "why": "断网"},
                  {"event": "merged_unknown", "id": "m1", "at": "2026-11-08T10:30:00+08:00", "merge_commit": "a" * 40})
        book = actions.policy_book(actions.pdaily_data.window(D))
        self.assertEqual((book["merged"], [m.get("unknown_merge") for m in book["unsettled"]]), ([], [True]))
        (a,) = [x for x in self.run_day()["actions"] if x["origin"] == "按策略合并"]
        self.assertEqual((a["result"], a["follow_up"]), ("不明", "要撤回在电脑上处理"))
        self.assertIn("来源不明", a["verification"])

    def test_policy_owner_rows_not_reported_as_card_clicks(self):
        self.ledger([self.row("2026-11-08T09:00:00+08:00", owner="策略合并", owner_at="2026-11-08T10:00:05+08:00")])
        ids = [x["action_id"] for x in self.run_day()["actions"]]
        self.assertFalse([i for i in ids if i.startswith("owner:")], ids)

    def test_book_for_daily_report(self):
        self.book(self.intent("m1", "2026-11-08T10:00:00+08:00"),
                  {"event": "merged", "id": "m1", "at": "2026-11-08T10:00:05+08:00", "merge_commit": "f" * 40},
                  {"event": "revert_opened", "id": "m1", "at": "2026-11-08T12:00:00+08:00", "revert_pr": "9",
                   "revert_url": "https://github.com/o/digital-twin/pull/9", "reason": "合错了"},
                  {"event": "bypass", "at": "2026-11-08T13:00:00+08:00", "repo": "o/digital-twin", "pr": "3", "title": "直接合的",
                   "head": SHA, "tier": "L2", "suggestion": "建议合并", "merged_at": "2026-11-08T04:50:00Z",
                   "policy_signed_at": "2026-11-08T09:00:00+08:00"},
                  {"event": "bypass", "at": "2026-11-08T13:00:00+08:00", "repo": "o/digital-twin", "pr": "4", "title": "签之前合的",
                   "head": SHA, "tier": "L1", "suggestion": "建议合并", "merged_at": "2026-11-08T00:59:00Z",
                   "policy_signed_at": "2026-11-08T09:00:00+08:00"},
                  {"event": "bypass", "at": "2026-11-08T13:00:00+08:00", "repo": "o/digital-twin", "pr": "6", "title": "旧版记的",
                   "head": SHA, "tier": "L1", "suggestion": "建议合并", "merged_at": "2026-11-08T04:50:00Z"})   # #322 前没有签署时间
        book = actions.policy_book(actions.pdaily_data.window(D))
        (m,) = book["merged"]
        self.assertEqual((m["pr"], m["tier"], m["revert_cmd"], m["revert"]["revert_pr"]), ("7", "L1", "撤回 digital-twin#7", "9"))
        self.assertEqual([(b["pr"], b["tier"]) for b in book["bypass"]], [("3", "L2")])
        self.assertEqual(book["unsettled"], [])

    def test_no_book_yet_is_zero_not_error(self):
        out = self.run_day()
        self.assertEqual(out["coverage"]["按策略合并"], {"ok": True, "count": 0, "error": None})


class MergeRunTests(Base):
    def test_merge_run_dirs(self):
        d = self.p["merge-runs"] / "digital-twin-5-aaaaaaa-20261108-100000"
        d.mkdir()
        (d / "test.log").write_text("$ make test\n...\nOK\n")
        (self.p["merge-runs"] / "怪目录").mkdir()
        (self.p["merge-runs"] / "digital-twin-6-bbbbbbb-20261109-050000").mkdir()   # 第二天
        out = self.run_day()
        rs = self.by_origin(out, "merge-runs")
        self.assertEqual([(a["ts"], a["project_id"], a["kind"], a["result"]) for a in rs],
                         [("2026-11-08T10:00:00+08:00", "twin", "supervise", "不明")])     # 合并本身看 owner 那条，不重复算
        self.assertIn("最后一行：OK", rs[0]["verification"])
        self.assertIn("merge-runs 里认不出的目录：怪目录", out["notes"])


class DispatchTests(Base):
    def test_refused_dispatch_has_request_reason_and_source(self):
        self.order("D20261108-1", "2026-11-08T10:00:00", mode="write", started="2026-11-08T10:00:00", status="failed",
                   receipt={"tool": "claude", "executor": "Claude Code", "refused": "executor_unavailable", "reason": "claude 没登录"})
        rs = self.by_origin(self.run_day(), "派单台账")
        self.assertEqual([x["action_id"] for x in rs], ["dispatch:D20261108-1:open", "dispatch:D20261108-1"])   # 先开单、后开跑
        a = rs[1]
        self.assertEqual((a["kind"], a["actor"], a["decision"], a["result"], a["auth_gap"]), ("refuse", "Hermes", "拒绝", None, None))
        self.assertIn("查一下 X", a["request"])
        self.assertIn("executor_unavailable：claude 没登录", a["verification"])
        self.assertIn("D20261108-1", a["auth_source"])
        self.order("D20261108-2", "2026-11-08T11:00:00", mode="write", receipt={"exit_code": 0})   # 老单子：跑完了但没记开跑时间
        b = next(a for a in self.by_origin(self.run_day(), "派单台账") if a["action_id"] == "dispatch:D20261108-2")
        self.assertEqual((b["decision"], b["result"], bool(b["auth_gap"])), ("执行", "调用成功", True))
        self.assertIn("开跑时间没记", b["verification"])

    def test_dispatch_results_and_tiers(self):
        ok = {"executor": "Codex", "exit_code": 0, "timed_out": False, "worker_error": False, "committed_files": ["a"], "uncommitted": []}
        self.order("D20261108-2", "2026-11-08T10:00:00", receipt=ok, started="2026-11-08T10:00:00")
        self.order("D20261108-3", "2026-11-08T11:00:00", mode="write", started="2026-11-08T11:00:00",
                   receipt={**ok, "exit_code": 1, "need_decision": "要不要删旧表", "guard_main_changed": True})
        self.order("D20261108-4", "2026-11-08T12:00:00", started="2026-11-08T12:00:00", status="running")   # 没回执
        self.order("D20261109-1", "2026-11-09T05:00:00", receipt=ok, started="2026-11-09T05:00:00")         # 第二天
        self.order("D20261107-9", "2026-11-07T23:50:00", receipt=ok, started="2026-11-08T10:30:00")         # 隔天才开跑
        self.order("D20261108-8", "2026-11-08T12:30:00", started="2026-11-08T12:30:00", status="running", receipt=ok)  # 重跑中，旧回执
        self.order("D20261108-5", "2026-11-08T13:00:00", mode="write", status="new")                       # 写入单等常浩确认
        self.order("D20261108-6", "2026-11-08T14:00:00", status="new")                                      # 只读单不用确认
        self.order("D20261108-7", "2026-11-08T23:00:00", mode="write", started="2026-11-09T05:00:00", receipt=ok)  # 隔天才开跑
        rs = self.by_origin(self.run_day(), "派单台账")
        self.assertEqual([(a["action_id"][9:], a["decision"], a["auth_tier"], a["result"], bool(a["auth_gap"])) for a in rs],
                         [("D20261108-2", "执行", "A", "调用成功", False), ("D20261107-9", "执行", "A", "调用成功", False),
                          ("D20261108-3:open", "升级给常浩", None, None, False), ("D20261108-3", "执行", None, "失败", True),
                          ("D20261108-4", "执行", "A", "已发起", False), ("D20261108-8", "执行", "A", "已发起", False),
                          ("D20261108-5:open", "升级给常浩", None, None, False), ("D20261108-6:open", "执行", "A", "已发起", False),
                          ("D20261108-7:open", "升级给常浩", None, None, False)])
        self.assertEqual([rs[6]["follow_up"], rs[8]["follow_up"]], ["常浩确认开跑或取消", None])
        self.assertIn("2026-11-09T05:00:00+08:00 开跑", rs[8]["verification"])
        nxt = self.by_origin(self.run_day(D + dt.timedelta(days=1)), "派单台账")
        self.assertEqual([(a["action_id"][9:], a["result"], bool(a["auth_gap"])) for a in nxt],
                         [("D20261108-7", "调用成功", True), ("D20261109-1", "调用成功", False)])   # 同一时刻按单号排
        self.assertEqual(rs[3]["auth_gap"], "授权不足：这类动作要 D 档，记录是「不知道」")
        self.assertEqual(rs[3]["follow_up"], "常浩决定：要不要删旧表")
        self.assertIn("护栏", rs[3]["verification"])
        self.assertEqual(rs[0]["actor"], "Codex")                                       # 执行者取回执里的 executor
        self.order("D20261108-9", "2026-11-08T15:00:00", mode="write", started="2026-11-08T15:00:00",
                   receipt={**ok, "executor": "Claude Code", "guard_remote_changed": True})
        g = next(a for a in self.by_origin(self.run_day(), "派单台账") if a["action_id"] == "dispatch:D20261108-9")
        self.assertEqual((g["actor"], g["follow_up"]), ("Claude Code", "核对主分支、远端的改动"))

    def test_cleaned_queued_and_pending_orders(self):
        ok = {"executor": "Codex", "exit_code": 0}
        self.order("D20261108-1", "2026-11-08T10:00:00", mode="write", status="abandoned", receipt=ok)   # 跑过、被 clean 成 abandoned
        self.order("D20261108-2", "2026-11-08T11:00:00", mode="write", status="abandoned")              # 没跑就放弃
        self.order("D20261108-3", "2026-11-08T12:00:00", status="queued", started="2026-11-08T12:00:00",
                   receipt={**ok, "exit_code": 1})                                                     # 跑失败后重跑在排队
        self.order("D20261108-4", "2026-11-08T13:00:00", mode="write", status="queued")                 # 确认了、排队，还没开跑
        self.order("D20261108-5", "2026-11-08T14:00:00", status="queued")                               # 只读单排队：只记一条
        for no, code in (("D20261108-6", "0"), ("D20261108-7", "1")):                                    # 没回执，看单子上的退出码
            self.order(no, "2026-11-08T15:00:00" if code == "0" else "2026-11-08T16:00:00", started=None)
            o = self.p["dispatch"] / no / "order.json"
            o.write_text(json.dumps({**json.loads(o.read_text()), "exit_code": code}))
        rs = self.by_origin(self.run_day(), "派单台账")
        self.assertEqual([(a["action_id"][9:], a["decision"], a["result"], bool(a["auth_gap"])) for a in rs],
                         [("D20261108-1:open", "升级给常浩", None, False), ("D20261108-1", "执行", "调用成功", True),
                          ("D20261108-2:open", "不明", "不明", False), ("D20261108-3", "执行", "失败", False),
                          ("D20261108-4:open", "升级给常浩", None, False), ("D20261108-4:queued", "执行", "已发起", True),
                          ("D20261108-5:open", "执行", "已发起", False), ("D20261108-6", "执行", "调用成功", False),
                          ("D20261108-7", "执行", "失败", False)])
        self.assertIn("开跑时间没记", rs[1]["verification"])
        self.assertIn("在排队", rs[3]["verification"])
        self.assertIsNone(rs[4]["follow_up"])                         # 已经确认了，不再要常浩确认
        self.assertEqual(rs[5]["actor"], "Codex")                      # 排队那条记执行方，不是 Hermes
        self.assertIn("单子记的退出码 1", rs[8]["verification"])

    def test_bad_orders_are_skipped_not_fatal(self):
        ok = {"executor": "Codex", "exit_code": 0}
        self.order("D20261108-1", "2026-11-08T10:00:00", receipt=ok, started="2026-11-08T10:00:00")
        self.order("D20261108-2", "2026-11-08T10:00:00", receipt=["坏"], started="2026-11-08T10:00:00")
        self.order("D20261108-3", "2026-11-08T10:00:00", receipt=ok, started="2026-11-08T10:00:00")
        (self.p["dispatch"] / "D20261108-3" / "order.json").write_text("[]")
        self.order("D20261108-4", "2026-11-08T10:00:00", receipt=ok, started="2026-11-08T10:00:00")
        (self.p["dispatch"] / "D20261108-4" / "task.md").write_bytes("坏编码".encode("gbk"))
        for no, when in (("D20261108-9", "2026-11-08T10:00:00+08:00"), ("D20261026-1", "2026-10-26T10:00:00+08:00")):
            d = self.p["dispatch"] / no
            d.mkdir()
            (d / "task.md").write_text("残单")
            t = dt.datetime.fromisoformat(when).timestamp()
            os.utime(d / "task.md", (t, t))
        out = self.run_day()
        rs = self.by_origin(out, "派单台账")
        self.assertTrue(out["coverage"]["派单台账"]["ok"])
        self.assertEqual([a["action_id"] for a in rs], ["dispatch:D20261108-1", "dispatch:D20261108-2", "dispatch:D20261108-4"])
        self.assertEqual(rs[1]["result"], "不明")                       # 回执坏了：当成还没有回执
        self.assertTrue(any("D20261108-2 的回执读不了" in n for n in out["notes"]))
        self.assertTrue(any("D20261108-3 跳过" in n for n in out["notes"]))
        self.assertTrue(any("D20261108-9 只有 task.md" in n for n in out["notes"]))
        self.assertFalse(any("D20261026-1" in n for n in out["notes"]))          # 旧残单不天天提


class CronTests(Base):
    def test_executions_and_incidents(self):
        self.cron_db(execs=[("e1", "j1", "completed", "2026-11-08T10:00:00.123456+08:00", "suppressed", None),
                            ("e0", "j1", "completed", "2026-11-08T10:00:00.1+08:00", "suppressed", None),   # 3.9 认不出：跳过
                            ("e2", "j1", "failed", "2026-11-08T11:00:00+08:00", "delivered", "Script exited with code 1\nstdout"),
                            ("e3", "jx", "unknown", "2026-11-08T12:00:00+08:00", None, None),
                            ("e4", "j1", "completed", "2026-11-09T04:00:00+08:00", "delivered", None)],
                     incs=[("i1", "j1", "open", "config", "2026-11-08T11:00:00+08:00", "2026-11-08T11:00:00+08:00",
                            "2026-11-08T11:01:00+08:00", None, "Script exited with code 1"),
                           ("i2", "j1", "closed", "config", "2026-11-07T11:00:00+08:00", "2026-11-08T11:00:00+08:00",
                            "2026-11-07T11:01:00+08:00", "2026-11-08T12:00:00+08:00", None),   # 前一天告警、当天关闭：列，不再算升级
                           ("i3", "j1", "closed", "config", "2026-11-07T11:00:00+08:00", "2026-11-07T11:00:00+08:00", None,
                            "2026-11-07T12:00:00+08:00", None)],
                     jobs={"jobs": [{"id": "j1", "name": "巡检", "created_at": "2026-10-01T10:00:00+08:00"},
                                    {"id": "j2", "name": "夜跑主控", "created_at": "2026-11-08T10:08:00+08:00", "enabled": True}]})
        out = self.run_day()
        self.assertTrue(any(n.startswith("定时任务执行 e0 时间认不出") for n in out["notes"]) or sys.version_info >= (3, 11))
        rs = self.by_origin(out, "定时任务库")
        runs = [a for a in rs if a["action_id"].startswith("cron:") and a["action_id"] != "cron:e0"]
        self.assertEqual([(a["result"], a["auth_gap"]) for a in runs], [("调用成功", None), ("失败", None), ("不明", None)])
        self.assertIn("「巡检」", runs[0]["request"])
        self.assertIn("jx", runs[2]["request"])
        self.assertIn("Script exited with code 1", runs[1]["verification"])
        self.assertNotIn("stdout", runs[1]["verification"])
        inc = [a for a in rs if a["kind"] == "supervise"]
        self.assertEqual([(a["decision"], a["result"], a["follow_up"]) for a in inc],
                         [("升级给常浩", None, "处理这张故障单"), ("执行", "调用成功", None)])
        new = [a for a in rs if a["action_id"].startswith("cron-job:")]
        self.assertEqual([(a["request"][:12], a["auth_gap"]) for a in new], [("新建定时任务「夜跑主控」", "授权不足：这类动作要 D 档，记录是「不知道」")])
        self.assertEqual(sorted(os.listdir(self.p["cron"])), ["executions.db", "jobs.json"])   # 只读：没留下 -wal、-shm

    def test_bad_alert_time_is_noted_once(self):
        self.cron_db(incs=[("i9", "j1", "open", "config", "2026-11-08T11:00:00+08:00", "2026-11-08T11:00:00+08:00", "坏", None, None)])
        out = self.run_day()
        self.assertEqual(len([n for n in out["notes"] if "定时任务故障单 i9" in n]), 1)
        self.assertEqual(self.by_origin(out, "定时任务库")[0]["decision"], "执行")

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
        self.assertEqual([a["actor"] for a in rs], ["Hermes", "Hermes", "Multica 执行器", "Multica 执行器", "Hermes", "Claude"])
        self.assertEqual(rs[3]["follow_up"], "常浩处理受阻的子单")

    def test_bad_night_lines_and_drill_run_by_claude(self):
        self.night_log("演练", [[], {"ts": "2026-11-08T14:00:00Z", "event": "start", "approved_by": "Hermes（常浩授权）"},   # 批准人不是执行人
                               {"event": "advance", "stage": 2}])
        d = self.p["runs"] / "坏编码"
        d.mkdir()
        (d / "run-log.jsonl").write_bytes(b'{"ts": "2026-11-08T15:00:00Z", "event": "release", "by": "\xff\xfe"}\n')
        out = self.run_day()
        rs = self.by_origin(out, "夜跑")
        self.assertTrue(out["coverage"]["夜跑"]["ok"])                 # 坏行、坏编码都不拖垮整个来源
        self.assertEqual([(a["request"], a["actor"]) for a in rs], [("夜跑 演练 开跑", "不明"), ("夜跑 坏编码 解除冻结", "不明")])
        self.assertIn("夜跑 演练 第 1 行不是 JSON 对象，跳过", out["notes"])
        self.assertTrue(any(n.startswith("夜跑 演练 第 3 行 时间认不出") for n in out["notes"]))

    def test_same_second_rows_keep_line_order(self):
        rows = [{"ts": "2026-11-08T14:00:00Z", "event": "plan"}] * 8 + [
            {"ts": "2026-11-08T14:05:00Z", "event": "run", "issue": "SBX-1", "status": "completed"},
            {"ts": "2026-11-08T14:05:00Z", "event": "advance", "stage": 2}]
        self.night_log("n1", rows)
        self.assertEqual([a["action_id"] for a in self.by_origin(self.run_day(), "夜跑")], ["night:n1:9", "night:n1:10"])

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
        self.assertIn("夜跑 SBX-9 第 2 行不是 JSON 对象，跳过", out["notes"])


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
                         [("ruling", "不明", "不明", None), ("ruling", "不明", "不明", None)])     # 带「授权」也不按标题分类
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
        self.assertEqual(list(out["coverage"]), ["actions.jsonl", "合并官", "按策略合并", "merge-runs", "派单台账", "定时任务库", "夜跑", "裁决文件"])
        self.assertEqual({k: v["error"] for k, v in out["coverage"].items() if not v["ok"]}, {})   # 编程错误不会藏成「取不到」

    def test_cli_bad_date(self):
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), "--date", "2026-13-01"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("日期写法不对", r.stdout)


if __name__ == "__main__":
    unittest.main()
