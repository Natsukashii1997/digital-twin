"""已修未合并（复盘 N3）自测：修复类标题、开了几天、同一段改动的判定，以及查 GitHub 的每条失败路径（全局坑 G-39）。
全部用假 gh（临时目录里的脚本，按夹具回放 pr list / pr diff），不联网。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import unmerged_fixes as uf  # noqa: E402

NOW = dt.datetime.fromisoformat("2026-11-19T10:40:00+08:00")

# 假 gh：FAKE_GH_DIR/mode-<仓库短名> 决定这个仓库怎么失败；pr list 回放 <短名>.json，pr diff 回放 <短名>-<编号>.diff，
# diff 夹具不在就像 GitHub 那样报改动太大。每次调用记进 calls.jsonl。
FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
root = Path(os.environ["FAKE_GH_DIR"])
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")
repo = args[args.index("-R") + 1].split("/")[-1]
mode = (root / f"mode-{repo}").read_text().strip() if (root / f"mode-{repo}").exists() else ""
fail = {"auth": "To get started with GitHub CLI, please run:  gh auth login",
        "rate": "GraphQL: API rate limit exceeded for user ID 1. (rateLimit)",
        "gone": "GraphQL: Could not resolve to a Repository with the name 'o/" + repo + "'. (repository)",
        "net": 'Post "https://api.github.com/graphql": read tcp 10.0.0.2:5->1.2.3.4:443: read: connection reset by peer'}
if mode == "slow":
    time.sleep(5)
if mode in fail:
    sys.stderr.write(fail[mode] + "\n"); sys.exit(1)
if mode == "notjson":
    print("<html>oops</html>"); sys.exit(0)
if args[:2] == ["pr", "list"]:
    sys.stdout.write((root / f"{repo}.json").read_text(encoding="utf-8")); sys.exit(0)
if args[:2] == ["pr", "diff"]:
    f = root / f"{repo}-{args[2]}.diff"
    if not f.exists():
        sys.stderr.write("HTTP 406: Sorry, the diff exceeded the maximum number of lines (20000)\n"); sys.exit(1)
    sys.stdout.write(f.read_text(encoding="utf-8")); sys.exit(0)
sys.stderr.write("假 gh 不认这个命令\n"); sys.exit(2)
'''


def pr(n, title, created):
    return {"number": n, "title": title, "createdAt": created}


def diff(*files):
    """files：(路径, [hunk 行…]) → 统一 diff 文本。"""
    out = []
    for path, lines in files:
        out += [f"diff --git a/{path} b/{path}", "index 1111111..2222222 100644", f"--- a/{path}", f"+++ b/{path}",
                "@@ -1,5 +1,5 @@", *lines]
    return "\n".join(out) + "\n"


CI = (".github/workflows/ci.yml", ["   test:", "-    runs-on: ubuntu-latest", "+    runs-on: ubuntu-24.04", "     steps:"])
CURRENT = ("CURRENT.md", ["-- 下一步：等 CI", "+- 下一步：CI 已固定 ubuntu-24.04，等合并"])
TINY = ("src/a.ts", ["   if (x) {", "+  }", "   return"])

# 照 11-19 上午 trainee-hub 的样子：#77～#80 各带一份同一行 CI 改动，交接文件也一样（不该算），#79 还有一块只有「}」
TRAINEE = [pr(77, "docs: PRD 升 v3.0", "2026-11-18T13:31:22Z"), pr(78, "feat: #45 第 1 页里程碑详情", "2026-11-18T13:31:43Z"),
           pr(79, "feat: #75 第 5 页管培生首页", "2026-11-18T13:42:00Z"), pr(80, "LESSONS 升级行补层级词", "2026-11-19T02:26:06Z"),
           pr(72, "安全审计 11-18｜修复初始化原子性、异常泄露与编码错误", "2026-11-17T10:18:04Z")]
AUDIT = [pr(7, "收藏存储：并发收藏不再互相覆盖（审计高危）", "2026-11-12T03:29:30Z"),
         pr(10, "README 补截图", "2026-11-10T03:29:30Z")]


class Judge(unittest.TestCase):
    def test_fix_titles(self):
        for t in ("收藏存储：并发收藏不再互相覆盖；更新整组提交、失败可恢复（审计高危）", "脚本安全：备份清理限定范围（审计高危 3 项）",
                  "fix: 空列表崩溃", "Fix login", "修 CI：固定 ubuntu-24.04", "#172 返修：第 2 轮", "hotfix 发布脚本", "补改审查意见",
                  "回滚 #12", "修正统计口径", "BugFix: x"):
            self.assertTrue(uf.is_fix(t), t)
        for t in ("修改 README", "修订 PRD", "docs: PRD 升 v3.0", "fixture 整理", "feat: 新页面", "LESSONS 升级行补层级词", ""):
            self.assertFalse(uf.is_fix(t), t)

    def test_stale_boundary(self):
        exact = (NOW - dt.timedelta(days=3)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        over = (NOW - dt.timedelta(days=3, minutes=1)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        got = uf.stale_fixes([pr(1, "修复 a", exact), pr(2, "修复 b", over), pr(3, "新功能", "2026-11-01T00:00:00Z"), *AUDIT], NOW)
        self.assertEqual([(s["number"], s["days"]) for s in got], [(7, 6), (2, 3)])     # 正好 3 天不算；6 天 23 小时记 6 天；开得最久的在前
        with self.assertRaises(ValueError):
            uf.stale_fixes([pr(1, "修复", "昨天")], NOW)

    def test_same_change_across_prs(self):
        diffs = {77: diff(CI, CURRENT), 78: diff(("web/a.tsx", ["+const a = '第一页里程碑详情';"]), CI, CURRENT),
                 79: diff(CI, TINY, CURRENT), 80: diff(CURRENT, CI), 81: diff(TINY)}
        got = uf.duplicate_changes(diffs)
        self.assertEqual(got, [{"prs": [77, 78, 79, 80], "files": [
            {"file": ".github/workflows/ci.yml", "blocks": 1, "example": "runs-on: ubuntu-24.04"}]}])   # 交接文件、「}」都不算

    def test_not_too_broad(self):
        other_base = (".github/workflows/ci.yml", ["-    runs-on: ubuntu-22.04", "+    runs-on: ubuntu-24.04"])
        split = (".github/workflows/ci.yml", ["-    runs-on: ubuntu-latest", "     # 中间隔了一行上下文", "+    runs-on: ubuntu-24.04"])
        self.assertEqual(uf.duplicate_changes({1: diff(CI), 2: diff(other_base), 3: diff(split)}), [])  # 增删行要整段一样
        self.assertEqual(uf.duplicate_changes({1: diff(CI)}), [])
        moved = (".github/workflows/ci.yml", ["-    runs-on: ubuntu-latest", "+    runs-on: ubuntu-24.04"])
        txt = diff(CI).replace("@@ -1,5 +1,5 @@", "@@ -40,5 +40,5 @@")
        self.assertEqual(len(uf.duplicate_changes({1: txt, 2: diff(moved)})), 1)       # 不比行号

    def test_diff_parsing_edges(self):
        quoted = ('diff --git "a/docs/\\351\\241\\265.md" "b/docs/\\351\\241\\265.md"\n--- "a/docs/\\351\\241\\265.md"\n'
                  '+++ "b/docs/\\351\\241\\265.md"\n@@ -1 +1 @@\n-旧的一句话说明页面清单\n+新的一句话说明页面清单和顺序\n'
                  '\\ No newline at end of file\n')
        deleted = ("diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n"
                   "-def legacy_entry_point():\n-    return 1\n")
        handoff = diff(("docs/交接-第3轮.md", ["+这一轮交接写了很长的一段说明文字"]))
        blocks = uf.change_blocks(quoted + deleted + handoff)
        self.assertEqual(sorted(p for p, _ in blocks), ["docs/页.md", "old.py"])

    def test_render_compact(self):
        res = {"repos": ["a", "b"], "failed": {}, "partial": {},
               "stale": [{"repo": "a", "number": 7, "title": "收藏存储：并发收藏不再互相覆盖；更新整组提交、失败可恢复（审计高危 2 项，Refs #3，第 2 轮）", "days": 7}],
               "dups": [{"repo": "b", "prs": [78, 79], "files": [{"file": f"f{k}.md", "blocks": 2, "example": "x"} for k in range(5)]}]}
        lines, has = uf.render(res)
        self.assertTrue(has)
        self.assertEqual(lines[0], "**已修未合并**（只提醒，不用回复）")
        self.assertIn("- a#7 收藏存储：并发收藏不再互相覆盖；更新整组提交、失败可恢复（审计高危 2 项，R…（开了 7 天）", lines)
        self.assertIn("- b #78、#79：f0.md、f1.md、f2.md 等 5 个文件，共 10 处", lines)


class Scan(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-unmerged-"))
        self.gh = self.tmp / "gh"
        self.gh.write_text(FAKE_GH, encoding="utf-8")
        self.gh.chmod(0o755)
        self.env = mock.patch.dict("os.environ", {"FAKE_GH_DIR": str(self.tmp)})
        self.env.start()
        self.put("trainee-hub", TRAINEE)
        for n, d in {77: diff(CI, CURRENT), 78: diff(CI), 79: diff(CI, TINY), 80: diff(CURRENT, CI), 72: diff(TINY)}.items():
            (self.tmp / f"trainee-hub-{n}.diff").write_text(d, encoding="utf-8")
        self.put("chatgpt-clipper", AUDIT)
        (self.tmp / "chatgpt-clipper-7.diff").write_text(diff(("a.js", ["+const lock = await acquire(key);"])), encoding="utf-8")
        (self.tmp / "chatgpt-clipper-10.diff").write_text(diff(("README.md", ["+![截图](docs/shot.png)"])), encoding="utf-8")
        self.put("get-index", [pr(1, "增量索引：防符号链接越界写（审计高危 F-08）", "2026-11-12T03:29:03Z")])
        self.repos = [("chatgpt-clipper", "o/chatgpt-clipper"), ("get-index", "o/get-index"), ("trainee-hub", "o/trainee-hub")]

    def tearDown(self):
        self.env.stop()
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def put(self, repo, prs):
        (self.tmp / f"{repo}.json").write_text(json.dumps(prs, ensure_ascii=False), encoding="utf-8")

    def mode(self, repo, m):
        (self.tmp / f"mode-{repo}").write_text(m, encoding="utf-8")

    def calls(self):
        f = self.tmp / "calls.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def scan(self, exe="default", timeout=None):
        return uf.scan(self.repos, NOW, uf.Gh(str(self.gh) if exe == "default" else exe, timeout))

    def test_all_ok(self):
        res = self.scan()
        self.assertEqual(res["failed"], {})
        self.assertEqual([(s["repo"], s["number"]) for s in res["stale"]], [("chatgpt-clipper", 7), ("get-index", 1)])
        self.assertEqual([(d["repo"], d["prs"]) for d in res["dups"]], [("trainee-hub", [77, 78, 79, 80])])
        lines, has = uf.render(res)
        self.assertTrue(has)
        self.assertIn("- chatgpt-clipper#7 收藏存储：并发收藏不再互相覆盖（审计高危）（开了 6 天）", lines)
        self.assertIn("- trainee-hub #77、#78、#79、#80：.github/workflows/ci.yml 1 处（如「runs-on: ubuntu-24.04」）", lines)
        self.assertFalse([l for l in lines if "⚠️" in l])
        # trainee-hub#72 开了不到 3 天，不列；只有一个开着 PR 的仓库不取 diff
        self.assertNotIn("trainee-hub#72", "\n".join(lines))
        self.assertFalse([c for c in self.calls() if c[:2] == ["pr", "diff"] and "o/get-index" in c])

    def test_nothing_found_is_not_failure(self):
        self.repos = [("get-index", "o/get-index")]
        self.put("get-index", [pr(2, "新功能", "2026-11-01T00:00:00Z")])
        lines, has = uf.render(self.scan())
        self.assertFalse(has)
        self.assertEqual(lines[1], "名册 1 个仓库都查了：没有开着超过 3 天的修复类 PR，也没有重复的同一处改动。")

    # ---------- 失败路径：每条一个测试，都不能当成 0 条 ----------

    def test_no_gh(self):
        lines, has = uf.render(self.scan(exe=None))
        self.assertTrue(has)
        self.assertEqual(lines[1:], ["⚠️ 没查到（不是 0 条）：全部 3 个仓库——找不到 gh 命令"])

    def test_not_logged_in_stops_after_first(self):
        for r in ("chatgpt-clipper", "get-index", "trainee-hub"):
            self.mode(r, "auth")
        res = self.scan()
        self.assertEqual(len(self.calls()), 1)                       # 没登录：后面的仓库不再查
        self.assertEqual(set(res["failed"]), {"chatgpt-clipper", "get-index", "trainee-hub"})
        lines, has = uf.render(res)
        self.assertTrue(has)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("⚠️ 没查到（不是 0 条）：全部 3 个仓库——gh 没登录或登录失效（To get started"))

    def test_rate_limited(self):
        self.mode("get-index", "rate")
        res = self.scan()
        self.assertEqual(sorted(res["failed"]), ["get-index", "trainee-hub"])   # 限流之前查到的照列，之后的标同一原因
        self.assertEqual([s["number"] for s in res["stale"]], [7])
        lines, _ = uf.render(res)
        self.assertIn("⚠️ 没查到（不是 0 条）：get-index、trainee-hub——被 GitHub 限流（GraphQL: API rate limit exceeded for user ID 1. (rateLimit)）",
                      lines)
        self.assertIn("- chatgpt-clipper#7 收藏存储：并发收藏不再互相覆盖（审计高危）（开了 6 天）", lines)

    def test_network_down(self):
        self.mode("chatgpt-clipper", "net")
        res = self.scan()
        self.assertEqual(len(self.calls()), 1)
        self.assertTrue(all(w.startswith("连不上 GitHub（") for w in res["failed"].values()))
        self.assertEqual(len(res["failed"]), 3)

    def test_timeout(self):
        self.mode("chatgpt-clipper", "slow")
        self.mode("get-index", "slow")
        with mock.patch.object(uf, "MAX_TIMEOUTS", 2):
            res = self.scan(timeout=1)
        self.assertEqual(res["failed"]["chatgpt-clipper"], "gh 超过 1 秒没回应")     # 第 1 次超时只算这个仓库
        self.assertEqual(res["failed"]["get-index"], "gh 已有 2 次超过 1 秒没回应")    # 第 2 次起不再查
        self.assertEqual(res["failed"]["trainee-hub"], "gh 已有 2 次超过 1 秒没回应")
        self.assertFalse([c for c in self.calls() if "o/trainee-hub" in c])        # 第 3 个仓库没再调 gh
        lines, has = uf.render(res)
        self.assertTrue(has)
        self.assertIn("⚠️ 没查到（不是 0 条）：chatgpt-clipper——gh 超过 1 秒没回应", lines)

    def test_one_repo_fails_others_still_checked(self):
        self.mode("chatgpt-clipper", "gone")
        res = self.scan()
        self.assertEqual(list(res["failed"]), ["chatgpt-clipper"])
        self.assertIn("Could not resolve to a Repository", res["failed"]["chatgpt-clipper"])   # 不当成网络问题停下
        self.assertEqual([s["repo"] for s in res["stale"]], ["get-index"])
        self.assertEqual(len(res["dups"]), 1)
        lines, _ = uf.render(res)
        self.assertIn("开着超过 3 天的修复类 PR：", lines)
        self.assertTrue(any(l.startswith("⚠️ 没查到（不是 0 条）：chatgpt-clipper——gh 出错（GraphQL: Could not") for l in lines))

    def test_output_not_json(self):
        self.mode("get-index", "notjson")
        res = self.scan()
        self.assertEqual(list(res["failed"]), ["get-index"])
        self.assertTrue(res["failed"]["get-index"].startswith("gh 输出格式不对（"))
        self.put("get-index", [{"number": 1, "title": "修复"}])                    # 缺 createdAt 也不当 0 条
        (self.tmp / "mode-get-index").unlink()
        self.assertTrue(self.scan()["failed"]["get-index"].startswith("gh 输出格式不对（KeyError"))

    def test_one_diff_missing(self):
        (self.tmp / "trainee-hub-79.diff").unlink()
        res = self.scan()
        self.assertEqual(res["failed"], {})
        self.assertEqual(res["dups"][0]["prs"], [77, 78, 80])
        lines, _ = uf.render(res)
        self.assertIn("⚠️ trainee-hub 只查了一部分：#79 的改动没取到（gh 出错（HTTP 406: Sorry, the diff exceeded the maximum "
                      "number of lines (20000)）），重复改动没比它", lines)

    def test_list_limit_reached(self):
        self.repos = [("get-index", "o/get-index")]
        self.put("get-index", [pr(n, "新功能", "2026-11-18T00:00:00Z") for n in range(1, 4)])
        with mock.patch.object(uf, "LIST_LIMIT", 3):
            res = self.scan()
        self.assertEqual(res["partial"]["get-index"][0], "开着的 PR 到了 3 个上限，可能没列全")
        self.assertTrue(uf.render(res)[1])

    def test_roster(self):
        roster = self.tmp / "projects.json"
        roster.write_text(json.dumps({"projects": [{"id": "x", "github": "o/b"}, {"id": "y"}, {"id": "z", "github": "o/a"},
                                                   {"id": "x2", "github": "o/b"}]}), encoding="utf-8")
        self.assertEqual(uf.roster_repos(roster), ([("a", "o/a"), ("b", "o/b")], None))
        roster.write_text("{坏的", encoding="utf-8")
        res = uf.collect(roster, NOW, uf.Gh(str(self.gh)))
        self.assertEqual(self.calls(), [])
        lines, has = uf.render(res)
        self.assertTrue(has)
        self.assertTrue(lines[1].startswith(f"⚠️ 本期没查到（不是 0 条）：项目名册 {roster} 读不了（JSONDecodeError"))
        self.assertEqual(uf.render(uf.collect(self.tmp / "没有.json", NOW, uf.Gh(str(self.gh))))[1], True)


if __name__ == "__main__":
    unittest.main()
