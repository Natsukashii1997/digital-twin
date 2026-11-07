"""影子合并官（#61）自测：判定、一致率、HEAD 绑定、回放；命令行用假 gh，不联网、不碰真实记录，并核对只调了只读的 gh 命令。"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import merge_officer as mo  # noqa: E402

SCRIPT = HERE.parent / "merge_officer.py"
RULES = mo.load_rules(HERE.parent / "merge_rules.json")
A, B, BASE = "a" * 40, "b" * 40, "c" * 40

# 假 gh：按 FAKE_DIR/prs.json 的 {repo: [PR...]} 回答 pr list / pr view，每次调用记进 calls.jsonl；FAKE_DIR/fail 里写的仓库一律失败。
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(args, ensure_ascii=False) + "\n")
repo = args[args.index("--repo") + 1]
if (root / "fail").exists() and repo in (root / "fail").read_text():
    sys.stderr.write("error connecting to api.github.com\n"); sys.exit(1)
prs = json.loads((root / "prs.json").read_text(encoding="utf-8")).get(repo, [])
if args[:2] == ["pr", "list"]:
    state = args[args.index("--state") + 1]
    print(json.dumps([p for p in prs if state == "all" or p["state"] == state.upper()], ensure_ascii=False))
elif args[:2] == ["pr", "view"]:
    hit = [p for p in prs if str(p["number"]) == args[2]]
    if not hit:
        sys.stderr.write("no pull requests found\n"); sys.exit(1)
    print(json.dumps(hit[0], ensure_ascii=False))
else:
    sys.stderr.write("fake gh: 不该调用 " + " ".join(args) + "\n"); sys.exit(2)
'''


def facts(paths, repo="o/app", **kw):
    f = {"repo": repo, "pr": 1, "title": "t", "head_sha": A, "base_sha": BASE, "draft": False,
         "mergeable": "MERGEABLE", "labels": [], "paths": paths, "checks": [], "closes": True}
    f.update(kw)
    return f


def pr(number, head, state="OPEN", paths=("docs/a.md",), body="Closes #1", **kw):
    p = {"number": number, "title": f"PR {number}", "state": state, "isDraft": False, "mergeable": "MERGEABLE",
         "headRefOid": head, "baseRefOid": BASE, "labels": [], "files": [{"path": x} for x in paths],
         "statusCheckRollup": [], "body": body, "closingIssuesReferences": [], "mergedAt": None, "closedAt": None}
    p.update(kw)
    return p


class Evaluate(unittest.TestCase):
    def test_docs_only_is_l0_merge(self):
        self.assertEqual(mo.evaluate(facts(["docs/a.md", "README.md"]), RULES), ("L0", mo.MERGE, []))

    def test_rule_docs_raise_to_l1(self):
        tier, sug, _ = mo.evaluate(facts(["AGENTS.md"]), RULES)
        self.assertEqual((tier, sug), ("L1", mo.MERGE))

    def test_file_count_splits_l1_l2(self):
        self.assertEqual(mo.evaluate(facts(["a.py", "b.py", "c.py"]), RULES)[0], "L1")
        self.assertEqual(mo.evaluate(facts(["a.py", "b.py", "c.py", "d.md"]), RULES)[0], "L2")

    def test_sensitive_path_asks_owner(self):
        tier, sug, why = mo.evaluate(facts(["ops/health_check.py", "docs/a.md"]), RULES)
        self.assertEqual((tier, sug), ("L3", mo.ASK))
        self.assertIn("ops/health_check.py", why[0])

    def test_repo_specific_sensitive(self):
        self.assertEqual(mo.evaluate(facts(["hermes/scripts/x.py"], repo="o/digital-twin"), RULES)[:2], ("L3", mo.ASK))
        self.assertEqual(mo.evaluate(facts(["hermes/scripts/x.py"], repo="o/other"), RULES)[:2], ("L1", mo.MERGE))

    def test_hard_problem_beats_ask(self):
        tier, sug, why = mo.evaluate(facts(["ops/x.sh"], draft=True), RULES)
        self.assertEqual((tier, sug), ("L3", mo.RETURN))
        self.assertTrue(any("草稿" in w for w in why) and any("敏感" in w for w in why))

    def test_each_return_reason(self):
        cases = {"还是草稿": {"draft": True}, "冲突": {"mergeable": "CONFLICTING"},
                 "检查没过": {"checks": ["SUCCESS", "FAILURE"]}, "Closes": {"closes": False},
                 "40 位": {"head_sha": "abc123"}, "不合法": {"paths": ["../x.md"]}, "没有改动": {"paths": []}}
        for word, kw in cases.items():
            paths = kw.pop("paths", ["docs/a.md"])
            tier, sug, why = mo.evaluate(facts(paths, **kw), RULES)
            self.assertEqual(sug, mo.RETURN, word)
            self.assertTrue(any(word in w for w in why), (word, why))

    def test_needs_info_label_asks(self):
        self.assertEqual(mo.evaluate(facts(["docs/a.md"], labels=["needs-info"]), RULES)[1], mo.ASK)

    def test_does_not_mutate_input(self):
        f = facts(["docs/a.md"])
        snap = json.dumps(f, sort_keys=True)
        mo.evaluate(f, RULES)
        self.assertEqual(json.dumps(f, sort_keys=True), snap)

    def test_facts_of_reads_closes_from_body_or_links(self):
        self.assertTrue(mo.facts_of("o/r", pr(1, A, body="说明\n\nCloses #12"))["closes"])
        self.assertTrue(mo.facts_of("o/r", pr(1, A, body="", closingIssuesReferences=[{"number": 3}]))["closes"])
        self.assertFalse(mo.facts_of("o/r", pr(1, A, body="见 #12"))["closes"])


class Agreement(unittest.TestCase):
    def test_judge(self):
        self.assertEqual(mo.judge(mo.MERGE, A, mo.MERGED, A), "是")
        self.assertEqual(mo.judge(mo.RETURN, A, mo.CLOSED, A), "是")
        self.assertEqual(mo.judge(mo.MERGE, A, mo.CLOSED, A), "否")
        self.assertEqual(mo.judge(mo.RETURN, A, mo.MERGED, A), "否")
        self.assertEqual(mo.judge(mo.ASK, A, mo.MERGED, A), "不计")
        self.assertEqual(mo.judge(mo.MERGE, A, mo.MERGED, B), "取代")
        self.assertEqual(mo.judge(mo.MERGE, A, "", ""), "")

    def rows(self, yes, no, tier="L0", source="live", sug=mo.MERGE):
        return [{"tier": tier, "source": source, "suggestion": sug, "agree": "是"}] * yes + \
               [{"tier": tier, "source": source, "suggestion": sug, "agree": "否"}] * no

    def test_threshold_boundary(self):
        th = RULES["threshold"]
        self.assertTrue(mo.rates(self.rows(18, 2), th)["L0"]["qualified"])       # 20 次、90%
        self.assertFalse(mo.rates(self.rows(17, 3), th)["L0"]["qualified"])      # 85%
        self.assertFalse(mo.rates(self.rows(19, 0), th)["L0"]["qualified"])      # 不到 20 次

    def test_backfill_and_ask_not_counted(self):
        rows = self.rows(30, 0, source="backfill") + [{"tier": "L0", "source": "live", "suggestion": mo.ASK, "agree": "不计"}] * 25
        r = mo.rates(rows, RULES["threshold"])["L0"]
        self.assertEqual((r["live"]["n"], r["live"]["ask"], r["qualified"]), (0, 25, False))
        self.assertEqual((r["backfill"]["n"], r["backfill"]["rate"]), (30, 1.0))

    def test_return_agree_counted_separately(self):
        rows = self.rows(3, 0) + self.rows(2, 0, sug=mo.RETURN)
        self.assertEqual(mo.rates(rows, RULES["threshold"])["L0"]["live"]["return_agree"], 2)


class Replay(unittest.TestCase):
    def test_before_after_and_changed(self):
        f1 = facts(["docs/a.md"], closes=False)
        f2 = facts(["docs/b.md"], pr=2)
        rows = [{"repo": "o/app", "pr": "1", "head_sha": A, "source": "live", "outcome": mo.MERGED, "outcome_head": A},
                {"repo": "o/app", "pr": "2", "head_sha": A, "source": "live", "outcome": mo.MERGED, "outcome_head": A},
                {"repo": "o/app", "pr": "3", "head_sha": A, "source": "live", "outcome": mo.MERGED, "outcome_head": A}]
        fx = {("o/app", "1", A): f1, ("o/app", "2", A): f2}          # 第 3 条没记状态，跳过
        new = json.loads(json.dumps(RULES))
        new["version"] = "2"
        new["return_when"]["missing_closes"] = False
        before, after, changed = mo.replay(rows, fx, RULES, new)
        self.assertEqual((before["L0"]["live"]["agree"], before["L0"]["live"]["n"]), (1, 2))
        self.assertEqual((after["L0"]["live"]["agree"], after["L0"]["live"]["n"]), (2, 2))
        self.assertEqual([(c["pr"], c["before"], c["after"]) for c in changed], [("1", ("L0", mo.RETURN), ("L0", mo.MERGE))])


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-shadow-"))
        self.fake = self.tmp / "fake"
        self.fake.mkdir()
        (self.fake / "gh").write_text(FAKE, encoding="utf-8")
        (self.fake / "gh").chmod(0o755)
        self.roster = self.tmp / "projects.json"
        self.roster.write_text(json.dumps({"projects": [
            {"id": "app", "category": "活跃", "github": "o/app"},
            {"id": "two", "category": "活跃", "github": "o/two"},
            {"id": "local", "category": "活跃", "github": None},
            {"id": "old", "category": "停用", "github": "o/old"}]}), encoding="utf-8")
        self.shadow = self.tmp / "shadow"

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def prs(self, data):
        (self.fake / "prs.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *args):
        env = {**os.environ, "FAKE_DIR": str(self.fake), "DT_GH": str(self.fake / "gh"), "DT_ROSTER": str(self.roster),
               "DT_SHADOW_DIR": str(self.shadow), "DT_NOW": "2026-11-07T10:00:00+08:00"}
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def ledger(self):
        with (self.shadow / "agreement.csv").open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def calls(self):
        return [json.loads(l) for l in (self.fake / "calls.jsonl").read_text().splitlines()]

    def test_scan_binds_head_and_settles_outcome(self):
        self.prs({"o/app": [pr(1, A), pr(2, A, paths=("ops/x.sh",))]})
        r = self.run_cli("scan")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)       # 成功不输出
        rows = self.ledger()
        self.assertEqual([(x["pr"], x["tier"], x["suggestion"], x["rules_version"]) for x in rows],
                         [("1", "L0", mo.MERGE, "1"), ("2", "L3", mo.ASK, "1")])
        self.assertEqual(self.run_cli("scan").returncode, 0)
        self.assertEqual(len(self.ledger()), 2)                               # 同一个 HEAD 不重复判
        self.prs({"o/app": [pr(1, B), pr(2, A, paths=("ops/x.sh",))]})       # PR 1 又推了提交
        self.run_cli("scan")
        self.assertEqual([(x["pr"], x["head_sha"][:1]) for x in self.ledger()], [("1", "a"), ("2", "a"), ("1", "b")])
        self.prs({"o/app": [pr(1, B, state="MERGED", mergedAt="2026-11-07T03:00:00Z"),
                            pr(2, A, state="CLOSED", paths=("ops/x.sh",), closedAt="2026-11-07T03:00:00Z")]})
        self.run_cli("scan")
        got = {(x["pr"], x["head_sha"][:1]): (x["outcome"], x["agree"]) for x in self.ledger()}
        self.assertEqual(got, {("1", "a"): (mo.MERGED, "取代"), ("1", "b"): (mo.MERGED, "是"), ("2", "a"): (mo.CLOSED, "不计")})
        facts_lines = (self.shadow / "facts.jsonl").read_text().splitlines()
        self.assertEqual(len(facts_lines), 3)
        for args in self.calls():                                            # 只读：只有 pr list / pr view
            self.assertIn(args[:2], (["pr", "list"], ["pr", "view"]), args)

    def test_no_subcommand_is_scan(self):
        self.prs({"o/app": [pr(1, A)]})
        r = self.run_cli()                                                   # 定时任务不带参数调用
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        self.assertEqual(len(self.ledger()), 1)

    def test_pr_beyond_list_falls_back_to_view(self):
        self.prs({"o/app": [pr(7, A)]})
        self.run_cli("scan")
        data = {"o/app": [pr(7, A, state="CLOSED", closedAt="x")]}
        self.prs(data)
        # 模拟结局列表里取不到：把 PR 7 藏起来，只让 pr view 找到
        (self.fake / "gh").write_text(FAKE.replace('state == "all" or ', 'state != "all" and '), encoding="utf-8")
        self.run_cli("scan")
        self.assertEqual(self.ledger()[0]["agree"], "否")
        self.assertIn(["pr", "view", "7"], [c[:3] for c in self.calls()])

    def test_one_repo_failing_reports_and_continues(self):
        self.prs({"o/app": [pr(1, A)], "o/two": [pr(5, A)]})
        (self.fake / "fail").write_text("o/app")
        r = self.run_cli("scan")
        self.assertEqual(r.returncode, 1)
        self.assertIn("o/app", r.stdout)
        self.assertEqual([x["repo"] for x in self.ledger()], ["o/two"])
        repos = {c[c.index("--repo") + 1] for c in self.calls()}
        self.assertEqual(repos, {"o/app", "o/two"})                          # 没 github 和停用的不扫

    def test_backfill_is_marked_and_idempotent(self):
        self.prs({"o/app": [pr(1, A, state="MERGED", body="无关联"), pr(2, A, state="CLOSED"), pr(3, A)]})
        r = self.run_cli("backfill")
        self.assertIn("回填 2 条", r.stdout)
        rows = self.ledger()
        self.assertEqual({(x["pr"], x["source"], x["agree"]) for x in rows}, {("1", "backfill", "否"), ("2", "backfill", "否")})
        self.assertIn("回填 0 条", self.run_cli("backfill").stdout)
        rep = self.run_cli("report", "--json")
        d = json.loads(rep.stdout)["tiers"]["L0"]
        self.assertEqual((d["live"]["n"], d["backfill"]["n"], d["qualified"]), (0, 2, False))
        self.assertIn("| L0 | 0 |", self.run_cli("report").stdout)

    def test_replay_cli(self):
        self.prs({"o/app": [pr(1, A, state="MERGED", body="")]})
        self.run_cli("backfill")
        new = json.loads((HERE.parent / "merge_rules.json").read_text(encoding="utf-8"))
        new["version"] = "2"
        new["return_when"]["missing_closes"] = False
        p = self.tmp / "v2.json"
        p.write_text(json.dumps(new, ensure_ascii=False), encoding="utf-8")
        r = self.run_cli("replay", "--rules", str(p))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("v1 → v2", r.stdout)
        self.assertIn("0/1（0%） | 1/1（100%）", r.stdout)
        self.assertIn("o/app#1（合并）：L0 退回 → L0 建议合并", r.stdout)
        self.assertEqual(len(self.ledger()), 1)                              # 回放只读，不改记录


if __name__ == "__main__":
    unittest.main()
