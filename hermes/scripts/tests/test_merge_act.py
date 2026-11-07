"""飞书一句话合并（#67）自测：发卡片、合并前的每一种停下、测试关、CI 关（#71）、HEAD 绑定、退回留言。
真 git（临时裸仓库当 GitHub、临时主目录）、假 gh、假 lark-cli，不联网、不碰真实仓库和记录。"""
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFICER = HERE.parent / "merge_officer.py"
ACT = HERE.parent / "merge_act.py"

# 假 gh：PR 状态在 FAKE_DIR/prs.json（{仓库: {号: PR}}），每次调用记进 calls.jsonl。
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(args, ensure_ascii=False) + "\n")
prs = json.loads((root / "prs.json").read_text(encoding="utf-8"))
repo = args[args.index("--repo") + 1]
mine = prs.get(repo, {})
if args[:2] == ["pr", "list"]:
    state = args[args.index("--state") + 1]
    print(json.dumps([p for p in mine.values() if state == "all" or p["state"] == state.upper()])); sys.exit(0)
pr = mine.get(args[2])
if pr is None:
    sys.stderr.write("no pull requests found\n"); sys.exit(1)
if args[:2] == ["pr", "view"]:
    print(json.dumps(pr)); sys.exit(0)
if args[:2] == ["pr", "merge"]:
    if (root / "merge_fail").exists() or args[args.index("--match-head-commit") + 1] != pr["headRefOid"]:
        sys.stderr.write("Head branch was modified. Review and try the merge again.\n"); sys.exit(1)
    pr["state"], pr["mergeCommit"] = "MERGED", {"oid": "f" * 40}
    (root / "prs.json").write_text(json.dumps(prs)); sys.exit(0)
if args[:2] == ["pr", "comment"]:
    sys.exit(0)
sys.stderr.write("fake gh: 不该调用 " + " ".join(args) + "\n"); sys.exit(2)
'''
FAKE_LARK = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
with open(root / "lark.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
n = sum(1 for _ in open(root / "lark.jsonl"))
print(json.dumps({"ok": True, "data": {"message_id": f"om_card_{n}"}}))
'''


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class MergeAct(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-act-"))
        t = self.tmp
        self.fake = t / "fake"
        self.fake.mkdir()
        for name, body in (("gh", FAKE_GH), ("lark-cli", FAKE_LARK)):
            (self.fake / name).write_text(body, encoding="utf-8")
            (self.fake / name).chmod(0o755)
        # 「GitHub」= 裸仓库；主目录 = 它的克隆，main 上有测试脚本
        self.remote = t / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.remote)], check=True)
        self.main = t / "work/app"
        subprocess.run(["git", "clone", "-q", str(self.remote), str(self.main)], check=True, capture_output=True)
        sh(self.main, "config", "user.email", "t@t")
        sh(self.main, "config", "user.name", "t")
        (self.main / "check.py").write_text("import pathlib, sys\nsys.exit(1 if pathlib.Path('fail').exists() else 0)\n")
        (self.main / "README.md").write_text("v1\n")
        sh(self.main, "add", ".")
        sh(self.main, "commit", "-qm", "init")
        sh(self.main, "push", "-q", "origin", "main")
        self.base = sh(self.main, "rev-parse", "HEAD")
        self.head = self.pr_commit(5, {"docs/a.md": "hi\n"})
        rules = json.loads((HERE.parent / "merge_rules.json").read_text(encoding="utf-8"))
        rules["act"] = {"repos": {"app": {"test": f"{sys.executable} check.py", "timeout": 60}}}
        self.rules = t / "rules.json"
        self.rules.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        self.roster = t / "projects.json"
        self.roster.write_text(json.dumps({"projects": [
            {"id": "app", "path": str(self.main), "main": "main", "category": "活跃", "github": "o/app"},
            {"id": "other", "path": str(t / "other"), "main": "main", "category": "活跃", "github": "o/other"}]}))
        self.shadow = t / "shadow"
        self.set_prs({"o/app": {"5": self.pr(5, self.head, ["docs/a.md"])}, "o/other": {"9": self.pr(9, "9" * 40, ["x.md"])}})

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def pr_commit(self, number, files, parent="main"):
        """在一个分支上做提交、推到裸仓库，并建 GitHub 那样的 refs/pull/<号>/head。"""
        sh(self.main, "checkout", "-q", "-B", f"pr{number}", parent)
        for name, text in files.items():
            p = self.main / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        sh(self.main, "add", ".")
        sh(self.main, "commit", "-qm", f"pr {number}")
        head = sh(self.main, "rev-parse", "HEAD")
        sh(self.main, "push", "-q", "-f", "origin", f"HEAD:refs/pull/{number}/head")
        sh(self.main, "checkout", "-q", "main")
        return head

    def pr(self, number, head, paths, **kw):
        p = {"number": number, "title": f"PR {number}", "state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
             "mergeStateStatus": "CLEAN", "headRefOid": head, "baseRefOid": self.base, "baseRefName": "main",
             "labels": [], "files": [{"path": x} for x in paths], "statusCheckRollup": [], "body": "Closes #1",
             "closingIssuesReferences": [], "url": "u"}
        p.update(kw)
        return p

    def set_prs(self, data):
        (self.fake / "prs.json").write_text(json.dumps(data), encoding="utf-8")

    def update_pr(self, **kw):
        data = json.loads((self.fake / "prs.json").read_text())
        data["o/app"]["5"].update(kw)
        self.set_prs(data)

    def cli(self, script, *args, env=None):
        e = {**os.environ, "FAKE_DIR": str(self.fake), "DT_GH": str(self.fake / "gh"), "DT_LARK_CLI": str(self.fake / "lark-cli"),
             "DT_ROSTER": str(self.roster), "DT_SHADOW_DIR": str(self.shadow), "DT_MERGE_RULES": str(self.rules),
             "DT_MERGE_RUNS": str(self.tmp / "runs"), "FEISHU_HOME_CHANNEL": "oc_test0001", "DT_NOW": "2026-11-07T10:00:00+08:00",
             "DT_HERMES_ENV": str(self.tmp / "no.env"), **(env or {})}
        return subprocess.run([sys.executable, "-B", str(script), *args], capture_output=True, text=True, env=e, timeout=120)

    def calls(self, name="calls.jsonl"):
        f = self.fake / name
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def ledger(self):
        with (self.shadow / "agreement.csv").open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def scan(self):
        r = self.cli(OFFICER, "scan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    @property
    def ref(self):
        return f"app#5@{self.head[:7]}"

    # ---------- 卡片 ----------

    def test_scan_sends_one_card_per_covered_pr_head(self):
        self.assertEqual(self.scan().stdout, "")
        (card,) = self.calls("lark.jsonl")                          # o/other 不在 act.repos，不发
        content = json.loads(card[card.index("--content") + 1])
        self.assertEqual(content["header"]["title"]["content"], f"🔀 待你决定 · {self.ref}")
        text = content["body"]["elements"][0]["content"]
        self.assertIn("风险档 **L0** · Hermes 建议 **建议合并**", text)
        self.assertIn("点 **合并**", text)
        buttons = [col["elements"][0] for col in content["body"]["elements"][1]["columns"]]
        self.assertEqual([b["text"]["content"] for b in buttons], ["✅ 合并", "↩️ 退回"])
        self.assertEqual([b["behaviors"] for b in buttons],
                         [[{"type": "callback", "value": {"merge_officer": v, "ref": self.ref}}] for v in ("merge", "return")])
        self.assertEqual(card[card.index("--idempotency-key") + 1], f"merge-card-app-5-{self.head[:12]}")
        self.scan()
        self.assertEqual(len(self.calls("lark.jsonl")), 1)            # 同一个版本不重发
        self.assertIn(self.ref, self.cli(ACT, "cards").stdout)

    def test_l3_card_says_not_in_feishu(self):
        self.update_pr(files=[{"path": "ops/x.sh"}])
        self.scan()
        card = self.calls("lark.jsonl")[0]
        elements = json.loads(card[card.index("--content") + 1])["body"]["elements"]
        self.assertIn("飞书里不合并", elements[0]["content"])
        self.assertNotIn("点 **合并**", elements[0]["content"])
        self.assertEqual(len(elements), 1)                              # L3 不带按钮

    # ---------- 合并 ----------

    def test_merge_happy_path(self):
        self.scan()
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"已合并 {self.ref}（PR 5）→ fffffff。测试通过", r.stdout)
        self.assertIn("主目录已拉齐到", r.stdout)
        merge = [c for c in self.calls() if c[:2] == ["pr", "merge"]]
        self.assertEqual(merge, [["pr", "merge", "5", "--repo", "o/app", "--squash", "--match-head-commit", self.head]])
        self.assertEqual(self.ledger()[0]["owner"], "合并")
        self.assertEqual(len(sh(self.main, "worktree", "list").splitlines()), 1)   # 测试用的 worktree 已删
        (log,) = (self.tmp / "runs").glob("*/test.log")
        self.assertIn("check.py", log.read_text())
        self.assertNotIn(self.ref, self.cli(ACT, "cards").stdout)

    def assert_stopped(self, word, *, before=None):
        self.scan()
        if before:
            before()
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue(r.stdout.startswith("没合并："), r.stdout)
        self.assertIn(word, r.stdout)
        self.assertNotIn(["pr", "merge"], [c[:2] for c in self.calls()])
        self.assertEqual(self.ledger()[0]["owner"], "")
        return r

    def test_stop_when_head_moved(self):
        self.assert_stopped("又有提交", before=lambda: self.update_pr(headRefOid="1" * 40))

    def test_stop_when_behind_or_conflict(self):
        self.assert_stopped("落后主分支", before=lambda: self.update_pr(mergeStateStatus="BEHIND"))

    def test_stop_when_conflict(self):
        self.assert_stopped("有冲突", before=lambda: self.update_pr(mergeable="CONFLICTING", mergeStateStatus="DIRTY"))

    def test_stop_when_not_clean(self):
        self.assert_stopped("BLOCKED", before=lambda: self.update_pr(mergeStateStatus="BLOCKED"))

    def test_stop_when_draft_or_closed(self):
        self.assert_stopped("草稿", before=lambda: self.update_pr(isDraft=True))

    def test_stop_when_l3(self):
        self.assert_stopped("L3", before=lambda: self.update_pr(files=[{"path": ".github/workflows/x.yml"}]))

    def test_stop_when_local_main_unpushed(self):
        def local_commit():
            (self.main / "local.md").write_text("x")
            sh(self.main, "add", ".")
            sh(self.main, "commit", "-qm", "没推的")
        self.assert_stopped("没推的提交", before=local_commit)

    def test_stop_when_tests_fail(self):
        self.head = self.pr_commit(5, {"docs/a.md": "hi\n", "fail": "x"})
        self.update_pr(headRefOid=self.head)
        r = self.assert_stopped("测试没过")
        self.assertIn("test.log", r.stdout)
        self.assertEqual(len(sh(self.main, "worktree", "list").splitlines()), 1)

    # ---------- CI 关（#71） ----------

    def set_act(self, conf):
        rules = json.loads(self.rules.read_text())
        rules["act"]["repos"] = {"app": conf}
        self.rules.write_text(json.dumps(rules))

    def ci_mode(self, checks, state="CLEAN"):
        """仓库登记 ci；PR 带这些检查。测试命令故意会失败，跑了就露馅。"""
        self.set_act({"ci": True, "test": f"{sys.executable} -c 'raise SystemExit(1)'"})
        self.update_pr(statusCheckRollup=checks, mergeStateStatus=state)

    def test_ci_passed_merges_without_local_tests(self):
        self.ci_mode([{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"},
                      {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "SKIPPED"},
                      {"__typename": "StatusContext", "context": "deploy", "state": "SUCCESS"}])
        self.scan()
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"已合并 {self.ref}（PR 5）→ fffffff。CI 通过。", r.stdout)
        self.assertFalse((self.tmp / "runs").exists())                    # 没在本机跑测试
        self.assertEqual(self.ledger()[0]["owner"], "合并")

    def test_ci_running_stops(self):
        self.assert_stopped("CI 还在跑：test", before=lambda: self.ci_mode(
            [{"name": "test", "status": "IN_PROGRESS", "conclusion": ""},
             {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"}], state="UNSTABLE"))

    def test_ci_failed_stops(self):
        r = self.assert_stopped("CI 没过：test（FAILURE）", before=lambda: self.ci_mode(
            [{"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"},
             {"name": "build", "status": "QUEUED", "conclusion": ""}], state="UNSTABLE"))
        self.assertNotIn("还在跑", r.stdout)                              # 有没过的，先说没过

    def test_ci_no_checks_stops(self):
        self.assert_stopped("没有任何 CI 检查结果", before=lambda: self.ci_mode([]))
        self.assertFalse((self.tmp / "runs").exists())

    def test_local_fallback_runs_setup_before_test(self):
        self.set_act({"setup": f"{sys.executable} -c \"open('deps','w').write('ok')\"",
                      "test": f"{sys.executable} -c \"import sys; sys.exit(0 if open('deps').read() == 'ok' else 1)\"",
                      "timeout": 60})
        self.scan()
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("测试通过，日志在", r.stdout)

    def test_no_ci_and_no_test_stops(self):
        self.assert_stopped("没登记 CI，也没登记测试命令", before=lambda: self.set_act({"timeout": 60}))

    def test_stop_when_merge_rejected_by_server(self):
        self.scan()
        (self.fake / "merge_fail").write_text("1")
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 1)
        self.assertIn("合并没成", r.stdout)
        self.assertIn("Head branch was modified", r.stdout)
        self.assertEqual(self.ledger()[0]["owner"], "")

    def test_only_cards_that_were_sent(self):
        self.scan()
        for ref, word in ((f"app#5@{'1' * 7}", "没发过"), ("app#6@" + self.head[:7], "没发过"), ("随便", "看不懂")):
            r = self.cli(ACT, "merge", ref)
            self.assertEqual(r.returncode, 1)
            self.assertIn(word, r.stdout)

    def test_repo_not_enabled(self):
        self.scan()
        rules = json.loads(self.rules.read_text())
        rules["act"]["repos"] = {}
        self.rules.write_text(json.dumps(rules))
        self.assertIn("还没开飞书合并", self.cli(ACT, "merge", self.ref).stdout)

    def test_dirty_main_dir_not_pulled(self):
        self.scan()
        (self.main / "README.md").write_text("改了没提交\n")
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("主目录没拉齐：有未提交的改动", r.stdout)
        self.assertEqual((self.main / "README.md").read_text(), "改了没提交\n")

    # ---------- 退回 ----------

    def test_return_posts_comment(self):
        self.scan()
        r = self.cli(ACT, "return", self.ref, "标题", "写得不清楚")
        self.assertEqual(r.returncode, 0, r.stdout)
        (c,) = [c for c in self.calls() if c[:2] == ["pr", "comment"]]
        body = c[c.index("--body") + 1]
        self.assertIn("常浩在飞书退回（Hermes 代发）：标题 写得不清楚", body)
        self.assertIn(self.head, body)
        self.assertEqual(self.ledger()[0]["owner"], "退回")
        self.assertIn("没有等你决定", self.cli(ACT, "cards").stdout)


if __name__ == "__main__":
    unittest.main()
