"""飞书一句话合并（#67）自测：发卡片、合并前的每一种停下、测试关、CI 关（#71）、HEAD 绑定、退回留言。
真 git（临时裸仓库当 GitHub、临时主目录）、假 gh、假 lark-cli，不联网、不碰真实仓库和记录。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFICER = HERE.parent / "merge_officer.py"
ACT = HERE.parent / "merge_act.py"

# 假 gh：PR 状态在 FAKE_DIR/prs.json（{仓库: {号: PR}}），每次调用记进 calls.jsonl。
# FAKE_DIR/fail.json 模拟网络或 GitHub 报错：{种类: {"n": 次数, "msg": 报错}}，每次调用用掉一次；带 "skip": k 的先放过前 k 次。
#   view：任何 pr view；view_merged：合并之后的 pr view；merge：合并请求没到 GitHub；merge_lost：合上了但回应丢了；
#   comment：留言。
# pr view / list 没写 changedFiles 的按 files 的个数补上；FAKE_DIR/off_on_view 存在时，pr view 顺手把策略记录关掉（#257）。
# api -X PUT repos/<仓库>/pulls/<号>/merge（按策略合并的 REST 合并接口，#260）：合上就打印 {sha, merged}；版本不对 409、
# 已不是 OPEN 405；fail.json 的 merge 在合之前断网、merge_lost 合上之后断网；FAKE_DIR/other_merges_first 存在时先被别处合掉。
# FAKE_DIR/label_on_view2 存在时，第二次 pr view 起 PR 带 needs-info。
# pr list 和真 gh 一样只给 --limit 个（按 prs.json 里的顺序）；FAKE_DIR/hide_from_list 里写的 PR 号不出现在列表里（pr view 照常）。
# FAKE_DIR/merge_deny 存在时合并接口报 403（权限不够）；FAKE_DIR/merge_error 里写报错原文：合并接口照这句话拒绝（限流、要审批等）。
# FAKE_DIR/real_git 里写裸仓库路径时（test_merge_auto 用，#260-P1-07），合并和撤回像 GitHub 那样真的动 Git 对象：
#   合并接口在裸仓库 main 上做出 squash 提交，回执和 mergeCommit 是它的提交号（PR 在裸仓库里没有 refs/pull/<号>/head 的照旧用 f×40）；
#   pr revert 在 main 最新提交上做出撤回提交（以原合并提交为基的三方合并），推成 refs/pull/<新号>/head，headRefOid 是它；
#   FAKE_DIR/revert_head 里写了提交号就用它当撤回 PR 的版本（模拟开出后、第一次读取前被人换了提交）。
# 撤回 PR 的 createdAt 取环境里的 DT_NOW。
# FAKE_DIR/merge_hang 里写秒数：合并接口先挂住这么久再处理（请求发出去、迟迟没回应）；revert_hang 同理，挂在开撤回 PR 上。
# pr revert（#257）：在 prs.json 里新开一个撤回 PR（号 = 原号 + 100，文件同原 PR、增删行数对调），打印它的网址；
# FAKE_DIR/revert_fail 存在就失败。fail.json 的 view_revert 只让「号大于 100 的 PR」的 pr view 失败。
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
real = (root / "real_git").read_text().strip() if (root / "real_git").exists() else ""
def git(*a):
    if not real:
        return ""
    r = subprocess.run(["git", "-C", real, "-c", "user.name=gh", "-c", "user.email=gh@t", *a], capture_output=True, text=True)
    return r.stdout.strip().split("\n")[0] if r.returncode == 0 else ""
def land(n):                                          # 真的 squash 合并进裸仓库的 main；做不了返回空
    tree = git("merge-tree", "--write-tree", "--no-messages", "refs/heads/main", "refs/pull/%s/head" % n)
    sha = tree and git("commit-tree", tree, "-p", "refs/heads/main", "-m", "squash #%s" % n)
    if sha:
        git("update-ref", "refs/heads/main", sha)
    return sha or "f" * 40
def undo(merged, n):                                  # 真的撤回提交：以原合并提交为基，把它的父提交合到 main 最新提交上
    tree = git("merge-tree", "--write-tree", "--no-messages", "--merge-base=" + merged, "refs/heads/main", merged + "^")
    sha = tree and git("commit-tree", tree, "-p", "refs/heads/main", "-m", "Revert #%s" % n)
    if sha:
        git("update-ref", "refs/pull/%s/head" % n, sha)
    return sha or "e" * 40
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(args, ensure_ascii=False) + "\n")
def hang(name):
    if (root / name).exists():
        time.sleep(float((root / name).read_text()))
if args[:3] == ["api", "-X", "PUT"]:
    hang("merge_hang")
if args[:2] == ["pr", "revert"]:
    hang("revert_hang")
prs = json.loads((root / "prs.json").read_text(encoding="utf-8"))
if args[:3] == ["api", "-X", "PUT"]:                 # REST 合并接口（按策略合并用，#260）：换成内部写法 pr apimerge <号> --repo <仓库> sha=…
    parts = args[3].split("/")
    args = ["pr", "apimerge", parts[4], "--repo", parts[1] + "/" + parts[2]] + args[4:]
repo = args[args.index("--repo") + 1]
mine = prs.get(repo, {})
def full(p):      # 没写的字段按「本仓库分支开的 PR」补：改动文件数、来源仓库
    return {"changedFiles": len(p.get("files") or []), "isCrossRepository": False,
            "headRepository": {"name": repo.split("/")[1]}, "headRepositoryOwner": {"login": repo.split("/")[0]}, **p}
if args[:2] == ["pr", "list"]:
    state = args[args.index("--state") + 1]
    limit = int(args[args.index("--limit") + 1]) if "--limit" in args else 30
    hidden = (root / "hide_from_list").read_text().split() if (root / "hide_from_list").exists() else []
    print(json.dumps([full(p) for p in mine.values()
                      if (state == "all" or p["state"] == state.upper()) and str(p["number"]) not in hidden][:limit])); sys.exit(0)
pr = mine.get(args[2])
if pr is None:
    sys.stderr.write("no pull requests found\n"); sys.exit(1)
def fail(kind):
    f = root / "fail.json"
    plan = json.loads(f.read_text()) if f.exists() else {}
    if plan.get(kind, {}).get("skip", 0) > 0:        # 先放过前 skip 次
        plan[kind]["skip"] -= 1
        f.write_text(json.dumps(plan))
        return None
    if plan.get(kind, {}).get("n", 0) <= 0:
        return None
    plan[kind]["n"] -= 1
    f.write_text(json.dumps(plan))
    return plan[kind]["msg"]
def die(msg):
    sys.stderr.write(msg + "\n"); sys.exit(1)
if args[:2] == ["pr", "view"]:
    msg = fail("view") or (pr["state"] == "MERGED" and fail("view_merged")) or (pr["number"] > 100 and fail("view_revert"))
    if msg:
        die(msg)
if args[:2] == ["pr", "merge"] and fail("merge"):
    die('Post "https://api.github.com/graphql": EOF')
if args[:2] == ["pr", "comment"]:
    msg = fail("comment")
    if msg:
        die(msg)
if args[:2] == ["pr", "view"] and (root / "label_on_view2").exists():     # 第二次读取时已经被加了 needs-info
    n = int((root / "label_on_view2").read_text() or 0) + 1
    (root / "label_on_view2").write_text(str(n))
    if n == 2:
        pr["labels"] = [{"name": "needs-info"}]
        (root / "prs.json").write_text(json.dumps(prs))
if args[:2] == ["pr", "apimerge"]:
    if fail("merge"):
        die('Put "https://api.github.com/repos/x": EOF')
    if (root / "other_merges_first").exists():       # 别处抢先合了：这次请求被拒，但 PR 已合并
        pr["state"], pr["mergeCommit"] = "MERGED", {"oid": "a" * 40}
        (root / "prs.json").write_text(json.dumps(prs))
    if pr["state"] != "OPEN":
        die("gh: Pull Request is not mergeable (HTTP 405)")
    if (root / "merge_deny").exists():               # 权限不够：等多久都不会自己好
        die("gh: Resource not accessible by personal access token (HTTP 403)")
    if (root / "merge_error").exists():
        die((root / "merge_error").read_text().strip())
    if (root / "merge_fail").exists() or [a for a in args if a.startswith("sha=")] != ["sha=" + pr["headRefOid"]]:
        die("gh: Head branch was modified. Review and try the merge again. (HTTP 409)")
    sha = land(pr["number"])
    pr["state"], pr["mergeCommit"] = "MERGED", {"oid": sha}
    (root / "prs.json").write_text(json.dumps(prs))
    if fail("merge_lost"):
        die('Put "https://api.github.com/repos/x": EOF')
    print(json.dumps({"sha": sha, "merged": True, "message": "Pull Request successfully merged"})); sys.exit(0)
if args[:2] == ["pr", "view"]:
    if (root / "off_on_view").exists():           # #257：取 PR 状态的当口常浩关了自动合并
        pf = Path(os.environ["DT_MERGE_DIR"]) / "policy.json"
        pf.write_text(json.dumps(dict(json.loads(pf.read_text()), enabled=False)))
    print(json.dumps(full(pr))); sys.exit(0)
if args[:2] == ["pr", "merge"]:
    if (root / "merge_fail").exists() or args[args.index("--match-head-commit") + 1] != pr["headRefOid"]:
        sys.stderr.write("Head branch was modified. Review and try the merge again.\n"); sys.exit(1)
    pr["state"], pr["mergeCommit"] = "MERGED", {"oid": "f" * 40}
    (root / "prs.json").write_text(json.dumps(prs))
    if fail("merge_lost"):
        die('Post "https://api.github.com/graphql": EOF')
    sys.exit(0)
if args[:2] == ["pr", "comment"]:
    sys.exit(0)
if args[:2] == ["pr", "revert"]:
    if (root / "revert_fail").exists():
        die("GraphQL: Something went wrong (revertPullRequest)")
    n = str(int(args[2]) + 100)
    head = undo((pr.get("mergeCommit") or {}).get("oid") or "", n)
    if (root / "revert_head").exists():
        head = (root / "revert_head").read_text().strip()
    mine[n] = dict(pr, number=int(n), title="Revert " + pr["title"], state="OPEN", headRefOid=head, mergeCommit=None,
                   createdAt=os.environ.get("DT_NOW"),
                   body=args[args.index("--body") + 1], url="https://github.com/" + repo + "/pull/" + n,
                   files=[dict(f, additions=f.get("deletions", 0), deletions=f.get("additions", 0)) for f in pr.get("files") or []])
    (root / "prs.json").write_text(json.dumps(prs))
    if fail("revert_lost"):
        die('Post "https://api.github.com/graphql": EOF')
    print("https://github.com/" + repo + "/pull/" + n); sys.exit(0)
sys.stderr.write("fake gh: 不该调用 " + " ".join(args) + "\n"); sys.exit(2)
'''
FAKE_LARK = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
if "--idempotency-key" in args and len(args[args.index("--idempotency-key") + 1]) > 50:   # 和真 lark-cli 一样
    print(json.dumps({"ok": False, "error": {"message": "--idempotency-key exceeds the maximum of 50 characters"}})); sys.exit(1)
with open(root / "lark.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
n = sum(1 for _ in open(root / "lark.jsonl"))
print(json.dumps({"ok": True, "data": {"message_id": f"om_card_{n}"}}))
'''


def sh(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class Fixture:
    """真 git 的临时仓库 + 假 gh / lark-cli；test_merge_auto.py 也用（#257）。"""

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
        rules["act"] = {"repos": {"app": {"ci": True}}}
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
             "labels": [], "files": [{"path": x} for x in paths], "body": "Closes #1",
             "statusCheckRollup": [{"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
             "closingIssuesReferences": [], "url": "u"}
        p.update(kw)
        return p

    def set_prs(self, data):
        (self.fake / "prs.json").write_text(json.dumps(data), encoding="utf-8")

    def update_pr(self, _number=5, **kw):
        data = json.loads((self.fake / "prs.json").read_text())
        data["o/app"][str(_number)].update(kw)
        self.set_prs(data)

    def env(self, extra=None):
        return {**os.environ, "FAKE_DIR": str(self.fake), "DT_GH": str(self.fake / "gh"), "DT_LARK_CLI": str(self.fake / "lark-cli"),
                "DT_ROSTER": str(self.roster), "DT_SHADOW_DIR": str(self.shadow), "DT_MERGE_RULES": str(self.rules),
                "DT_MERGE_RUNS": str(self.tmp / "runs"), "DT_MERGE_DIR": str(self.tmp / "merge"),
                "FEISHU_HOME_CHANNEL": "oc_test0001", "DT_NOW": "2026-11-07T10:00:00+08:00",
                "DT_HERMES_ENV": str(self.tmp / "no.env"), "DT_GH_RETRY_WAITS": "0,0", **(extra or {})}

    def cli(self, script, *args, env=None):
        return subprocess.run([sys.executable, "-B", str(script), *args], capture_output=True, text=True, env=self.env(env), timeout=120)

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


class MergeAct(Fixture, unittest.TestCase):
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
        self.assertIn(f"已合并 {self.ref}（PR 5）→ fffffff。CI 通过。", r.stdout)
        self.assertIn("主目录已拉齐到", r.stdout)
        merge = [c for c in self.calls() if c[:2] == ["pr", "merge"]]
        self.assertEqual(merge, [["pr", "merge", "5", "--repo", "o/app", "--squash", "--match-head-commit", self.head]])
        self.assertEqual(self.ledger()[0]["owner"], "合并")
        self.assertEqual(len(sh(self.main, "worktree", "list").splitlines()), 1)   # 没为跑测试建 worktree
        self.assertFalse((self.tmp / "runs").exists())                    # 没在本机跑测试
        self.assertNotIn(self.ref, self.cli(ACT, "cards").stdout)

    def assert_four(self, text, step, why, state, todo, head="没合并"):
        """失败提示四段都在：卡在哪一步、为什么、现在的状态、怎么办。"""
        text = text.strip()
        self.assertTrue(text.startswith(f"{head}：在『{step}』这一步"), text)
        for part in (why, state, todo):
            self.assertIn(part, text)
        self.assertLess(text.index(why), text.index(state), text)
        self.assertLess(text.index(state), text.rindex(todo), text)
        self.assertTrue(text.endswith("。"), text)

    def assert_stopped(self, word, *, step="核对版本", todo="", before=None):
        self.scan()
        if before:
            before()
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assert_four(r.stdout, step, word, "PR 没有任何改动", todo)
        self.assertNotIn(["pr", "merge"], [c[:2] for c in self.calls()])
        self.assertEqual(self.ledger()[0]["owner"], "")
        return r

    def test_stop_when_head_moved(self):
        self.assert_stopped("又有提交", todo="等新卡片", before=lambda: self.update_pr(headRefOid="1" * 40))

    def test_stop_when_behind_or_conflict(self):
        self.assert_stopped("落后主分支", todo="变基", before=lambda: self.update_pr(mergeStateStatus="BEHIND"))

    def test_stop_when_conflict(self):
        self.assert_stopped("有冲突", todo="变基", before=lambda: self.update_pr(mergeable="CONFLICTING", mergeStateStatus="DIRTY"))

    def test_stop_when_not_clean(self):
        self.assert_stopped("BLOCKED", todo="隔几分钟再点一次合并", before=lambda: self.update_pr(mergeStateStatus="BLOCKED"))

    def test_stop_when_draft_or_closed(self):
        self.assert_stopped("草稿", before=lambda: self.update_pr(isDraft=True))

    def test_stop_when_l3(self):
        self.assert_stopped("L3", todo="在电脑上交给 Claude 会话处理", before=lambda: self.update_pr(files=[{"path": ".github/workflows/x.yml"}]))

    def test_stop_when_local_main_unpushed(self):
        def local_commit():
            (self.main / "local.md").write_text("x")
            sh(self.main, "add", ".")
            sh(self.main, "commit", "-qm", "没推的")
        self.assert_stopped("没推的提交", todo="决定推不推", before=local_commit)

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
        self.assert_stopped("CI 还在跑：test", step="跑测试", todo="等 CI 跑完再点一次合并", before=lambda: self.ci_mode(
            [{"name": "test", "status": "IN_PROGRESS", "conclusion": ""},
             {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"}], state="UNSTABLE"))

    def test_ci_failed_stops(self):
        r = self.assert_stopped("CI 没过：test（FAILURE）", step="跑测试", todo="交回开这个 PR 的会话修", before=lambda: self.ci_mode(
            [{"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"},
             {"name": "build", "status": "QUEUED", "conclusion": ""}], state="UNSTABLE"))
        self.assertNotIn("还在跑", r.stdout)                              # 有没过的，先说没过

    def test_ci_no_checks_stops(self):
        self.assert_stopped("没有任何 CI 检查结果", step="跑测试", before=lambda: self.ci_mode([]))
        self.assertFalse((self.tmp / "runs").exists())

    def test_no_ci_never_runs_pr_code_on_host(self):
        """#190 不变量 2：没 CI 的仓库，哪怕登记了测试命令，主账号也不跑 PR 里的代码。"""
        marker = self.tmp / "ran"
        self.head = self.pr_commit(5, {"docs/a.md": "hi\n", "evil.py": f"open({str(marker)!r}, 'w').write('x')\n"})
        self.update_pr(headRefOid=self.head)
        for conf in ({"setup": f"{sys.executable} evil.py", "test": f"{sys.executable} evil.py", "timeout": 60}, {"timeout": 60}):
            with self.subTest(conf=conf):
                self.assert_stopped("没有 CI", step="跑测试", todo="先给这个仓库配 GitHub CI",
                                    before=lambda c=conf: self.set_act(c))
                self.assertFalse(marker.exists())
                self.assertFalse((self.tmp / "runs").exists())
                self.assertEqual(len(sh(self.main, "worktree", "list").splitlines()), 1)

    def test_stop_when_merge_rejected_by_server(self):
        self.scan()
        (self.fake / "merge_fail").write_text("1")
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 1)
        self.assert_four(r.stdout, "执行合并", "被 GitHub 拒绝：PR 版本变了（Head branch was modified",
                         "回读确认 PR 还是 OPEN，PR 没有任何改动", "等新卡片再决定")
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

    # ---------- 网络瞬断 ----------

    EOF = 'Post "https://api.github.com/graphql": EOF'

    def gh_fails(self, **plan):      # 不叫 fail：那会盖住 unittest 自己的 fail，断言失败会变成 TypeError
        (self.fake / "fail.json").write_text(json.dumps({k: {"n": n, "msg": m} for k, (n, m) in plan.items()}))

    def kinds(self):
        return [c[:2] for c in self.calls() if c[:2] != ["pr", "list"]]

    def test_read_retries_through_network_blip(self):
        self.scan()
        self.gh_fails(view=(2, self.EOF))
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"已合并 {self.ref}", r.stdout)
        self.assertEqual(self.kinds().count(["pr", "merge"]), 1)
        self.assertEqual(self.kinds()[:3], [["pr", "view"]] * 3)          # 失败两次，第三次取到

    def test_other_transient_errors_also_retried(self):
        self.scan()
        for msg in ("curl: (35) LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to api.github.com:443",
                    'Post "https://api.github.com/graphql": net/http: TLS handshake timeout',
                    "read tcp 127.0.0.1:50000->127.0.0.1:8086: read: connection reset by peer",
                    'Post "https://api.github.com/graphql": dial tcp: lookup api.github.com: no such host'):
            with self.subTest(msg=msg):
                self.gh_fails(view=(1, msg))
                r = self.cli(ACT, "return", self.ref, "试")                 # 退回不改 PR 状态，可以连试几种
                self.assertEqual(r.returncode, 0, r.stdout)

    def test_network_down_said_in_four_parts(self):
        self.scan()
        self.gh_fails(view=(3, self.EOF))                                     # 一次加两次重试都断
        r = self.cli(ACT, "button", "merge", self.ref, "--chat", "oc_test0001")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.texts()[-1], "没合并：在『取 PR 状态』这一步连不上 GitHub（网络中途断开：Post "
                         '"https://api.github.com/graphql": EOF）。PR 没有任何改动。已自动重试 2 次仍失败，隔一分钟再点一次合并。')
        self.assertNotIn("GhError", self.texts()[-1])
        self.assertEqual(self.kinds(), [["pr", "view"]] * 3)
        self.assertEqual(self.ledger()[0]["owner"], "")

    def test_github_refusals_not_retried(self):
        self.scan()
        for msg, why, todo in (
                ("HTTP 401: Bad credentials (https://api.github.com/graphql)", "被 GitHub 拒绝：gh 登录失效", "重新登录 gh"),
                ("HTTP 403: Resource not accessible by integration", "被 GitHub 拒绝：权限不足", "查 gh 的权限"),
                ("HTTP 403: API rate limit exceeded for user ID 1", "被 GitHub 拒绝：超出频率限制", "过一小时再点一次合并")):
            with self.subTest(msg=msg):
                before = len(self.kinds())
                self.gh_fails(view=(3, msg))
                r = self.cli(ACT, "merge", self.ref)
                self.assertEqual(r.returncode, 1)
                self.assert_four(r.stdout, "取 PR 状态", why, "PR 没有任何改动", todo)
                self.assertIn(msg[:20], r.stdout)                         # 括号里附原文
                self.assertEqual(len(self.kinds()) - before, 1)           # 不是网络问题，不重试

    def test_long_raw_error_clipped_to_80(self):
        self.scan()
        self.gh_fails(view=(1, "HTTP 401: " + "x" * 200))
        r = self.cli(ACT, "merge", self.ref)
        raw = r.stdout.split("gh 登录失效（", 1)[1].split("）。", 1)[0]
        self.assertEqual(len(raw), 80)
        self.assertTrue(raw.startswith("…"))

    def test_pr_gone_or_closed(self):
        self.scan()
        self.set_prs({"o/app": {}})
        self.assert_four(self.cli(ACT, "merge", self.ref).stdout, "取 PR 状态", "找不到这个 PR（no pull requests found）",
                         "PR 没有任何改动", "在电脑上看一下这个 PR 还在不在")
        self.set_prs({"o/app": {"5": self.pr(5, self.head, ["docs/a.md"], state="CLOSED")}})
        self.assert_four(self.cli(ACT, "merge", self.ref).stdout, "核对版本", "PR 已经是 CLOSED", "PR 没有任何改动", "不用再合")

    def test_merge_request_lost_is_not_retried(self):
        self.scan()
        self.gh_fails(merge=(1, self.EOF))
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 1)
        self.assert_four(r.stdout, "执行合并", "连不上 GitHub（网络中途断开：", "回读确认 PR 还是 OPEN，PR 没有任何改动",
                         "隔一分钟再点一次合并")
        self.assertNotIn("重试", r.stdout)                                # 合并命令本身不重试
        self.assertEqual(self.kinds().count(["pr", "merge"]), 1)
        self.assertEqual(self.kinds()[-1], ["pr", "view"])                # 先回读，确认还开着才说没改
        self.assertEqual(self.ledger()[0]["owner"], "")

    def test_merged_but_reply_lost_counts_as_merged(self):
        self.scan()
        self.gh_fails(merge_lost=(1, self.EOF))
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(f"已合并 {self.ref}（PR 5）→ fffffff", r.stdout)
        self.assertEqual(self.kinds().count(["pr", "merge"]), 1)
        self.assertEqual(self.ledger()[0]["owner"], "合并")

    def test_readback_down_after_merge_says_unsure(self):
        self.scan()
        self.gh_fails(view_merged=(3, self.EOF))
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 1)
        self.assert_four(r.stdout, "合并后回读", "连不上 GitHub", "合并命令已经发出，但回读没成功，不知道合上没有",
                         "不会重复合并", head="不确定合上没有")
        self.assertNotIn("没有任何改动", r.stdout)
        self.assertEqual(self.kinds().count(["pr", "merge"]), 1)
        r = self.cli(ACT, "merge", self.ref)                              # 再点一次：查到已合并，不重复合并
        self.assert_four(r.stdout, "核对版本", "PR 已经是 MERGED", "PR 没有任何改动", "不用再合")
        self.assertEqual(self.kinds().count(["pr", "merge"]), 1)

    def test_return_network_down_said_in_four_parts(self):
        self.scan()
        self.gh_fails(view=(3, self.EOF))
        r = self.cli(ACT, "return", self.ref, "标题不清楚")
        self.assertEqual(r.returncode, 1)
        self.assert_four(r.stdout, "取 PR 状态", "连不上 GitHub", "PR 没有任何改动",
                         "已自动重试 2 次仍失败，隔一分钟再说一次「退回 原因」", head="没退回")
        self.assertNotIn(["pr", "comment"], self.kinds())

    def test_return_comment_lost_says_unsure(self):
        self.scan()
        self.gh_fails(comment=(1, self.EOF))
        r = self.cli(ACT, "return", self.ref, "标题不清楚")
        self.assertEqual(r.returncode, 1)
        self.assert_four(r.stdout, "留言退回", "连不上 GitHub", "不确定留言发出去没有", "看一眼这条留言在不在", head="没退回")
        self.assertEqual(self.kinds().count(["pr", "comment"]), 1)        # 留言不重试，免得发两条
        self.assertEqual(self.ledger()[0]["owner"], "")

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

    # ---------- 卡片按钮（#78） ----------

    def texts(self):
        """卡片之后发到私聊的文字消息。"""
        return [json.loads(c[c.index("--content") + 1])["text"] for c in self.calls("lark.jsonl")
                if c[c.index("--msg-type") + 1] == "text"]

    def test_button_merge_says_checking_then_result(self):
        self.scan()
        r = self.cli(ACT, "button", "merge", self.ref, "--chat", "oc_test0001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        checking, result = self.texts()
        self.assertEqual(checking, f"⏳ 在核对 {self.ref}，稍后告诉你结果。")
        self.assertIn(f"已合并 {self.ref}（PR 5）→ fffffff", result)
        self.assertEqual(self.ledger()[0]["owner"], "合并")
        keys = [c[c.index("--idempotency-key") + 1] for c in self.calls("lark.jsonl")[1:]]
        self.assertEqual(len(set(keys)), 2)                                  # 两条消息各用各的幂等键

    def test_button_merge_stop_is_reported(self):
        self.scan()
        self.update_pr(headRefOid="b" * 40)
        r = self.cli(ACT, "button", "merge", self.ref, "--chat", "oc_test0001")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.texts()[-1].startswith("没合并："), self.texts())
        self.assertNotIn(["pr", "merge"], [c[:2] for c in self.calls()])

    def test_button_from_other_chat_does_nothing(self):
        self.scan()
        r = self.cli(ACT, "button", "merge", self.ref, "--chat", "oc_someone_else")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不是来自常浩私聊", r.stdout)
        self.assertEqual(self.texts(), [])
        self.assertNotIn(["pr", "merge"], [c[:2] for c in self.calls()])

    def test_button_unknown_card_is_refused(self):
        self.scan()
        r = self.cli(ACT, "button", "merge", "app#0@0000000", "--chat", "oc_test0001")
        self.assertEqual(r.returncode, 1)
        self.assertIn("没发过", self.texts()[-1])

    def test_button_busy_while_same_card_running(self):
        import fcntl
        self.scan()
        self.shadow.mkdir(parents=True, exist_ok=True)
        with open(self.shadow / f"button-app_5_{self.head[:7]}.lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = self.cli(ACT, "button", "merge", self.ref, "--chat", "oc_test0001")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.texts(), [f"{self.ref} 已经在处理了，等上一次的结果就好。"])

    def test_button_return_asks_reason_and_marks_card(self):
        self.scan()
        r = self.cli(ACT, "button", "return", self.ref, "--chat", "oc_test0001")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.texts(), [f"↩️ 退回 {self.ref} 的原因是？直接回「退回 原因」就行，不用引用卡片。"])
        self.assertIn("【等你说退回原因】", self.cli(ACT, "cards").stdout)
        self.assertNotIn(["pr", "comment"], [c[:2] for c in self.calls()])    # 没原因不留言
        self.assertEqual(self.ledger()[0]["owner"], "")


class CustodyCredential(unittest.TestCase):
    """托管合并入口（#132、PR #146 审查返修）：凭据在函数里强制取、取不到不发写命令、最小环境不继承宿主凭据、
    结果分 merged / failed / unsure、输出里的令牌打码。用假的 subprocess.run，不碰钥匙串和 GitHub。"""
    REPO = "Natsukashii1997/dt-sandbox"
    TOKEN = "github_pat_TESTSECRET"

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HERE.parent))
        import merge_act
        cls.ma = merge_act

    def card(self, repo=None):
        return {"pr": 5, "repo": repo or self.REPO, "head": "a" * 40}

    MERGED_REPLY = {"sha": "m" * 40, "merged": True, "message": "Pull Request successfully merged"}

    def fake(self, sec_code=0, sec_out=None, sec_exc=None, merge_code=0, merge_err="", merge_timeout=False,
             view=None, view_code=0, gh_exc=None, reply=None, view_raw=None):
        """按命令分派：security 读钥匙串、gh pr merge、gh pr view。记下每次调用的命令和 env。"""
        calls = []
        view = {"state": "MERGED", "headRefOid": "a" * 40, "mergeCommit": {"oid": "m" * 40}} if view is None else view

        def run(cmd, **kw):
            calls.append((cmd, kw.get("env")))
            if cmd[0] == self.ma.SECURITY:
                if sec_exc:
                    raise sec_exc
                return subprocess.CompletedProcess(cmd, sec_code, stdout=self.TOKEN + "\n" if sec_out is None else sec_out, stderr="")
            if gh_exc:
                raise gh_exc
            if cmd[1:3] in (["pr", "merge"], ["api", "-X"]):
                if merge_timeout:
                    raise subprocess.TimeoutExpired(cmd, 1)
                body = reply if reply is not None else (self.MERGED_REPLY if merge_code == 0 else None)
                return subprocess.CompletedProcess(cmd, merge_code, stdout=json.dumps(body) if body else "", stderr=merge_err)
            if cmd[1:3] == ["pr", "view"]:
                raw = view_raw if view_raw is not None else json.dumps(view)
                return subprocess.CompletedProcess(cmd, view_code, stdout=raw, stderr="")
            raise AssertionError(cmd)
        return run, calls

    def go(self, run, card=None, env=None):
        with unittest.mock.patch.dict(os.environ, env or {"PATH": "/usr/bin"}, clear=True), \
                unittest.mock.patch.object(self.ma.subprocess, "run", run):
            return self.ma.run_custody_merge(card or self.card())

    def gh_calls(self, calls):
        return [c for c in calls if c[0][0] != self.ma.SECURITY]

    def test_reads_keychain_by_full_repo_and_merges(self):
        run, calls = self.fake()
        out = self.go(run)
        self.assertEqual(calls[0][0], [self.ma.SECURITY, "find-generic-password", "-s", "dt-merge-executor",
                                       "-a", "dt-sandbox", "-w"])
        self.assertEqual(out["status"], "merged")
        self.assertEqual(out["identity"], "custody")
        self.assertEqual([c[0][1:5] for c in self.gh_calls(calls)],
                         [["api", "-X", "PUT", f"repos/{self.REPO}/pulls/5/merge"], ["pr", "view", "5", "--repo"]])
        self.assertIn(f"sha={'a' * 40}", calls[1][0])          # 合并请求绑定卡片上的 HEAD
        self.assertEqual(out["merge_commit"], "m" * 40)

    def test_unregistered_or_other_owner_repo_stops_before_keychain(self):
        for repo in ("Natsukashii1997/digital-twin", "someone-else/dt-sandbox"):
            run, calls = self.fake()
            with self.assertRaises(self.ma.Stop) as c:
                self.go(run, self.card(repo))
            self.assertEqual(calls, [])
            self.assertIn("没有托管合并凭据", c.exception.why)

    def test_keychain_failures_stop_without_any_gh_call(self):
        cases = [dict(sec_code=44, sec_out=""), dict(sec_out="   \n"),
                 dict(sec_exc=FileNotFoundError()), dict(sec_exc=subprocess.TimeoutExpired("security", 30))]
        for kw in cases:
            run, calls = self.fake(**kw)
            with self.assertRaises(self.ma.Stop):
                self.go(run)
            self.assertEqual(self.gh_calls(calls), [], kw)

    def test_minimal_env_ignores_host_credentials_and_hosts(self):
        host = {"PATH": "/evil/bin:/usr/bin", "GH_HOST": "evil.example.com", "GH_ENTERPRISE_TOKEN": "e1",
                "GITHUB_ENTERPRISE_TOKEN": "e2", "GITHUB_TOKEN": "host", "GH_TOKEN": "host2", "GH_CONFIG_DIR": "/host/gh",
                "GH_DEBUG": "api", "HERMES_HOME": "/h", "HTTPS_PROXY": "http://127.0.0.1:8086", "HOME": "/Users/x"}
        run, calls = self.fake()
        self.go(run, env=host)
        envs = [e for _, e in self.gh_calls(calls)]
        self.assertEqual(len(envs), 2)
        self.assertIs(envs[0], envs[1])                       # 写和回读用同一个受控环境
        e = envs[0]
        self.assertEqual(e["GH_TOKEN"], self.TOKEN)
        self.assertEqual(e["GH_HOST"], "github.com")
        self.assertEqual(e["PATH"], self.ma.CUSTODY_PATH)
        self.assertNotEqual(e["GH_CONFIG_DIR"], "/host/gh")
        self.assertFalse(Path(e["GH_CONFIG_DIR"]).exists())    # 临时空配置目录用完即删
        for k in ("GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GITHUB_TOKEN", "GH_DEBUG", "HERMES_HOME"):
            self.assertNotIn(k, e)
        self.assertEqual(e["HTTPS_PROXY"], "http://127.0.0.1:8086")
        self.assertEqual(e["HOME"], "/Users/x")

    def test_status_matrix(self):
        merged = {"state": "MERGED", "headRefOid": "a" * 40, "mergeCommit": {"oid": "m" * 40}}
        open_ = {"state": "OPEN", "headRefOid": "a" * 40, "mergeCommit": None}
        cases = [
            (dict(merge_code=0, view=merged), "merged"),
            (dict(merge_code=1, merge_err="Cannot update this protected ref", view=open_), "failed"),
            (dict(merge_code=1, view=merged), "unsure"),                     # 命令失败却已合并：可能是别人合的
            (dict(merge_code=0, view=open_), "unsure"),
            (dict(merge_timeout=True, view=merged), "unsure"),
            (dict(merge_timeout=True, view=open_), "unsure"),
            (dict(merge_code=0, view_code=1), "unsure"),                     # 回读失败
            (dict(merge_code=0, view={**merged, "headRefOid": "b" * 40}), "unsure"),
            (dict(merge_code=1, merge_err='Post "https://api.github.com/graphql": EOF', view=open_), "unsure"),
            (dict(merge_code=1, merge_err="read: connection reset by peer", view=open_), "unsure"),
            (dict(merge_code=1, merge_err="something unclassified", view=open_), "unsure"),
            (dict(merge_code=1, merge_err="X Pull request #5 is not mergeable: the base branch policy prohibits the merge.",
                  view=open_), "failed"),
            (dict(merge_code=1, merge_err="Head branch was modified. Review and try the merge again.", view=open_), "failed"),
            # PR 已被别人合上：接口拒绝、回读 MERGED 且 HEAD 对得上，不能记成本次合并
            (dict(merge_code=1, merge_err="gh: Pull Request is not mergeable (HTTP 405)", view=merged), "unsure"),
            (dict(merge_code=0, reply={"merged": False, "message": "x"}, view=merged), "unsure"),
            (dict(merge_code=0, reply={"merged": True}, view=merged), "unsure"),                    # 没有合并提交号
            (dict(merge_code=0, reply={**self.MERGED_REPLY, "sha": "z" * 40}, view=merged), "unsure"),  # 提交号对不上
            # 多行报错：最后一行像拒绝，前面有网络中断 → 网络优先，判 unsure
            (dict(merge_code=1, merge_err='Post "https://api.github.com/...": EOF\ngh: Pull Request is not mergeable (HTTP 405)',
                  view=open_), "unsure"),
            (dict(merge_code=0, view_raw="null"), "unsure"),                                        # 回读不是对象
            (dict(merge_code=0, view_raw="[]"), "unsure"),
            (dict(merge_code=0, view_raw="not json"), "unsure"),
        ]
        for kw, want in cases:
            run, calls = self.fake(**kw)
            out = self.go(run)
            self.assertEqual(out["status"], want, kw)
            self.assertEqual([c[0][1:3] for c in self.gh_calls(calls)].count(["api", "-X"]), 1, kw)  # 不重发

    def test_gh_launch_failure_returns_failed_not_exception(self):
        for exc in (FileNotFoundError(), PermissionError()):
            run, calls = self.fake(gh_exc=exc)
            out = self.go(run)
            self.assertEqual(out["status"], "failed", exc)
            self.assertFalse(out["launched"])
            self.assertIn(type(exc).__name__, out["why"])

    def test_token_in_gh_output_is_redacted(self):
        run, _ = self.fake(merge_code=1, merge_err=f"bad credentials for {self.TOKEN}",
                           view={"state": "OPEN", "headRefOid": "a" * 40})
        out = self.go(run)
        self.assertNotIn(self.TOKEN, json.dumps(out))
        self.assertIn("***", out["why"])

    def test_owner_merge_uses_local_login(self):
        run, calls = self.fake()
        with unittest.mock.patch.object(self.ma.subprocess, "run", run):
            out = self.ma.run_owner_merge(self.card())
        self.assertEqual(out["identity"], "owner")
        self.assertEqual([(c[0][1:3], c[1]) for c in calls], [(["pr", "merge"], None)])
        self.assertEqual(out["returncode"], 0)


if __name__ == "__main__":
    unittest.main()
