"""隔离自测：openloop.py（未闭环账本，#266 批 5a）。

临时 git 仓库当名册项目，假的 GitHub（进程内按仓库回话；另有一条走假 gh 命令），不联网，账本写进临时目录。
现在 = 2026-11-20 16:00（北京时间）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import datetime as dt
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import fake_gh  # noqa: E402
import openloop as ol  # noqa: E402
import pdaily_data  # noqa: E402
import progress_data  # noqa: E402

NOW = dt.datetime(2026, 11, 20, 16, 0, tzinfo=ol.TZ)
HEAD = "a" * 40
OLD = "b" * 40
RULES = {"sensitive": [r"^ops/"], "l0_paths": [r"\.md$"], "rule_docs": [r"(^|/)AGENTS\.md$"], "l1_max_files": 3}


def run(name, conclusion="SUCCESS", status="COMPLETED", at="2026-11-20T06:10:00Z"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion, "completedAt": at}


def review(state, oid=HEAD, at="2026-11-20T06:30:00Z"):
    return {"state": state, "submittedAt": at, "commit": {"oid": oid}}


def pr(number=1, title="改点东西", branch="claude/1-x", draft=False, checks=None, reviews=(), files=("hermes/scripts/a.py",),
       changed=None, mergeable="MERGEABLE", pushed="2026-11-20T06:00:00Z"):
    """GitHub 回的一个开着的 PR。pushed 06:00Z = 北京时间 14:00。checks=None 表示仓库没有 CI。"""
    rollup = None if checks is None else {"contexts": {"nodes": list(checks)}}
    return {
        "number": number, "title": title, "url": f"https://github.com/o/dt/pull/{number}", "isDraft": draft,
        "createdAt": pushed, "updatedAt": pushed, "headRefName": branch, "headRefOid": HEAD, "mergeable": mergeable,
        "changedFiles": len(files) if changed is None else changed, "files": {"nodes": [{"path": f} for f in files]},
        "commits": {"nodes": [{"commit": {"oid": HEAD, "committedDate": pushed, "statusCheckRollup": rollup}}]},
        "reviews": {"nodes": list(reviews)},
    }


class Hub:
    """进程内的假 GitHub：prs = {仓库: [PR]}，numbers = {仓库: {号: 这个号的样子}}，down = 连不上的仓库。"""

    def __init__(self, prs=None, numbers=None, down=(), page=None):
        self.prs, self.numbers, self.down, self.page, self.calls = prs or {}, numbers or {}, set(down), page, []

    def _run(self, *args, partial=False):
        f = dict(a.split("=", 1) for a in args if "=" in a)
        repo = f"{f['o']}/{f['n']}"
        self.calls.append((repo, "prs" if "pullRequests" in f["query"] else "lookup"))
        if repo in self.down:
            raise pdaily_data.NetFail("error connecting to api.github.com")
        if "pullRequests" in f["query"]:
            rows = self.prs.get(repo, [])
            if self.page:   # 分页：每页 page 个，游标就是下一页的起点
                start = int(f.get("c") or 0)
                more = start + self.page < len(rows)
                return {"data": {"repository": {"pullRequests": {
                    "nodes": rows[start:start + self.page], "pageInfo": {"hasNextPage": more, "endCursor": str(start + self.page)}}}}}
            return {"data": {"repository": {"pullRequests": {"nodes": rows, "pageInfo": {"hasNextPage": False}}}}}
        got = self.numbers.get(repo, {})
        return {"data": {"repository": {f"i{n}": v for n, v in got.items() if f"i{n}:" in f["query"]}}}


def sh(cwd, *args, at="2026-11-18 10:00:00 +0800"):
    env = {**os.environ, "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout


def make_repo(root, name, current=None, at="2026-11-18 10:00:00 +0800"):
    r = root / name
    r.mkdir()
    sh(r, "init", "-q", "-b", "main")
    if current is not None:
        (r / "CURRENT.md").write_text(current, encoding="utf-8")
        sh(r, "add", "-A")
    sh(r, "commit", "-q", "--allow-empty", "-m", "起步", at=at)
    return r


def commit_current(r, text, at):
    (r / "CURRENT.md").write_text(text, encoding="utf-8")
    sh(r, "add", "-A")
    sh(r, "commit", "-q", "-m", "更新 CURRENT", at=at)


CURRENT = """# 数字分身 · 当前状态

## #253 对齐驿窗 postpane#466：没送成的不算（2026-11-20，Claude；PR 待常浩定合并）

- 正文

## 主线：执行底座（#188）｜完成 1/7

说明文字，不是清单项。

- ✅ 方案：PR #189 已合并 `832d7ca`
- ▶ 批 0B 授权门（#195）：秘书代批规格定稿，PR #218 在审
- ⏳ 0D 恢复门
- ❓ 批 1 飞书拍板通道：卡片样子等常浩选
- ⏸️ #136 托管合并：常浩 11-18 定「停在这里」
- 📌 批 6 驿窗只显示（先做改动确认）
- ✖ #134 已停
- 没有标记的一项
  - 缩进的补充说明，不算一项

主线后面的一段话。

## 旧主线：托管上线（#126）｜完成 3/8

- ▶ #135 执行防线反向测试

---

## 2026-11-19 · #246 工作台分身页（PR 待常浩合并）

- ▶ 不在主线节里的标记不算

## 2026-11-19 · #239 工作台主线页（PR #240 已合并 `494a330`）
"""


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-openloop-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.out = self.root / "openloop"

    def roster(self, *projects):
        f = self.root / "projects.json"
        f.write_text(json.dumps({"projects": list(projects)}, ensure_ascii=False), encoding="utf-8")
        p = mock.patch.object(progress_data, "ROSTER", f)
        p.start()
        self.addCleanup(p.stop)

    def project(self, name, current=None, github=True, category="活跃"):
        r = make_repo(self.root, name, current)
        return {"id": f"id-{name}", "name": name, "path": str(r), "main": "main", "category": category,
                "github": f"o/{name}" if github else None}

    def build(self, hub, prev=None, now=NOW):
        return ol.build(now, ol.roster(), hub, RULES, prev=prev, fetch=False)


class PrState(unittest.TestCase):
    """规格第 5 节 GitHub 一列。"""

    def row(self, **kw):
        return ol.pr_row("o/dt", pr(**kw), RULES)

    def test_draft_is_in_progress_and_owned_by_branch_prefix(self):
        r = self.row(draft=True, checks=[run("test")], branch="codex/9-x")
        self.assertEqual((r["state"], r["owner"], r["id"]), ("在做", "Codex", "pr:dt#1"))
        self.assertEqual(r["check_at"], "2026-11-21T14:00:00+08:00")   # 草稿按「一直在做没进展」24 小时

    def test_failed_check_is_stuck_two_hours_from_the_failure(self):
        r = self.row(checks=[run("test", "FAILURE", at="2026-11-20T06:20:00Z"), run("lint")])
        self.assertEqual((r["state"], r["blocked"], r["owner"]), ("卡住", "CI 失败：test", "Claude"))
        self.assertEqual(r["evidence"], "11-20 14:20 检查失败：test")
        self.assertEqual(r["check_at"], "2026-11-20T16:20:00+08:00")
        self.assertEqual(r["done_when"], "PR 合并或关闭")

    def test_failed_check_wins_over_an_approval(self):
        r = self.row(checks=[run("test", "TIMED_OUT")], reviews=[review("APPROVED")])
        self.assertEqual(r["state"], "卡住")

    def test_conflict_is_stuck(self):
        r = self.row(checks=[run("test")], mergeable="CONFLICTING", reviews=[review("APPROVED")])
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "和主分支有冲突"))

    def test_changes_requested_on_head_is_stuck_four_hours_from_the_review(self):
        r = self.row(checks=[run("test")], reviews=[review("APPROVED", at="2026-11-20T06:05:00Z"), review("CHANGES_REQUESTED")])
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "审查要求修改（GitHub 审查）"))
        self.assertEqual(r["check_at"], "2026-11-20T18:30:00+08:00")

    def test_review_of_an_older_commit_does_not_count(self):
        for state in ("CHANGES_REQUESTED", "APPROVED"):
            r = self.row(checks=[run("test")], reviews=[review(state, oid=OLD)])
            self.assertEqual(r["state"], "待验证", state)
            self.assertIn("上一次审的是旧版本", r["evidence"])

    def test_running_checks_are_in_progress(self):
        r = self.row(checks=[run("test", None, status="IN_PROGRESS")], reviews=[review("APPROVED")])
        self.assertEqual((r["state"], r["next"]), ("在做", "等检查跑完"))

    def test_approved_and_green_waits_for_owner_until_next_slip(self):
        r = self.row(checks=[run("test")], reviews=[review("APPROVED")])
        self.assertEqual((r["state"], r["owner"], r["next"]), ("等你", "常浩", "常浩决定合并"))
        self.assertEqual(r["evidence"], "11-20 14:30 GitHub 审查通过，检查全过")
        self.assertEqual(r["check_at"], "2026-11-20T21:00:00+08:00")   # 14:30 之后的下一张单是 21:00

    def test_docs_only_needs_no_review(self):
        r = self.row(checks=[run("test")], files=("docs/a.md", "README.md"))
        self.assertEqual(r["state"], "等你")
        self.assertIn("只改文档（L0，不用审）", r["evidence"])

    def test_rule_docs_and_truncated_file_lists_are_not_l0(self):
        self.assertEqual(self.row(checks=[run("test")], files=("AGENTS.md",))["state"], "待验证")
        self.assertEqual(self.row(checks=[run("test")], files=("docs/a.md",), changed=150)["state"], "待验证")
        self.assertEqual(ol.pr_row("o/dt", pr(checks=[run("test")], files=("docs/a.md",)), None)["state"], "待验证")   # 规则读不了

    def test_green_without_any_review_is_unverified_with_unknown_owner(self):
        r = self.row(checks=[run("test")])
        self.assertEqual((r["state"], r["owner"], r["next"]), ("待验证", "未知", "送审或请人审"))
        self.assertEqual(r["evidence"], "11-20 14:00 最近一次提交，检查全过，没有审查或送审记录")
        self.assertEqual(r["check_at"], "2026-11-20T18:00:00+08:00")

    def test_repo_without_ci_says_so(self):
        r = self.row(checks=None, branch="feature/x")
        self.assertEqual(r["state"], "待验证")
        self.assertIn("仓库没有 CI", r["evidence"])

    def test_comment_only_review_is_not_a_verdict(self):
        self.assertEqual(self.row(checks=[run("test")], reviews=[review("COMMENTED")])["state"], "待验证")

    def test_status_context_failure_counts(self):
        ctx = {"__typename": "StatusContext", "context": "deploy", "state": "FAILURE", "createdAt": "2026-11-20T06:40:00Z"}
        r = self.row(checks=[run("test"), ctx])
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "CI 失败：deploy"))

    def test_review_records_from_other_sources_are_merged_by_time(self):
        """5a-2 的驿窗送审记录从 reviews 参数进来：比 GitHub 审查新的那条算数。"""
        later = {"verdict": "revise", "at": ol.parse_ts("2026-11-20T07:00:00Z"), "by": "驿窗 L3 第 2 轮", "current": True}
        r = ol.pr_row("o/dt", pr(checks=[run("test")], reviews=[review("APPROVED")]), RULES, reviews=[later])
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "审查要求修改（驿窗 L3 第 2 轮）"))


class Time(unittest.TestCase):
    def test_next_slip(self):
        def at(h, m, day=20):
            return dt.datetime(2026, 11, day, h, m, tzinfo=ol.TZ)
        self.assertEqual(ol.next_slip(at(11, 0)), at(12, 30))
        self.assertEqual(ol.next_slip(at(12, 30)), at(21, 0))      # 正好在点上：算下一张
        self.assertEqual(ol.next_slip(at(21, 30)), at(12, 30, 21))

    def test_parse_ts_reads_github_git_and_session_stamps(self):
        want = dt.datetime(2026, 11, 20, 14, 0, tzinfo=ol.TZ)
        for s in ("2026-11-20T06:00:00Z", "2026-11-20T14:00:00+08:00", "2026-11-20T06:00:00.123Z", "2026-11-20T14:00:00"):
            self.assertEqual(ol.parse_ts(s), want, s)
        self.assertIsNone(ol.parse_ts("昨天"))
        self.assertIsNone(ol.parse_ts(None))


class Mainline(Base):
    def test_sections_items_and_boundaries(self):
        got = ol.mainlines(CURRENT)
        self.assertEqual([(s["title"], s["old"]) for s in got],
                         [("执行底座（#188）｜完成 1/7", False), ("托管上线（#126）｜完成 3/8", True)])
        self.assertEqual("".join(i["mark"] for i in got[0]["items"]), "✅▶⏳❓⏸📌✖")
        self.assertEqual([u["text"] for u in got[0]["unparsed"]], ["- 没有标记的一项"])
        self.assertEqual(got[0]["items"][1]["line"], 12)

    def test_header_styles(self):
        text = "主线：TraineeOS 落地（v3.0）｜完成 4/8\n\n- ▶ 甲\n\n### 主线：本轮安全审计｜完成 4/6\n\n✅ 乙\n❓ 丙等常浩决定\n\n## 别的\n- ▶ 丁\n"
        got = ol.mainlines(text)
        self.assertEqual([s["title"] for s in got], ["TraineeOS 落地（v3.0）｜完成 4/8", "本轮安全审计｜完成 4/6"])
        self.assertEqual([[i["text"] for i in s["items"]] for s in got], [["甲"], ["乙", "丙等常浩决定"]])

    def test_mentions_of_the_word_are_not_sections(self):
        self.assertEqual(ol.mainlines("## 2026-11-19 · #239 工作台主线页\n- ▶ 甲\n**主线**：x｜✅2\n- ▶ 乙\n"), [])

    def test_rows_states_ids_and_parked(self):
        p = self.project("dt", CURRENT)
        commit_current(Path(p["path"]), CURRENT.replace("0D 恢复门", "0D 恢复门：开工了"), "2026-11-19 09:00:00 +0800")
        self.roster(p)
        led = self.build(Hub())
        rows = {r["id"]: r for r in led["rows"]}
        self.assertEqual(sorted(rows), ["mainline:dt/#188/0D恢复门", "mainline:dt/#188/批0B授权门", "mainline:dt/#188/批1飞书拍板通道"])
        doing, waiting, ask = rows["mainline:dt/#188/批0B授权门"], rows["mainline:dt/#188/0D恢复门"], rows["mainline:dt/#188/批1飞书拍板通道"]
        self.assertEqual((doing["state"], doing["owner"], doing["what"]), ("在做", "未知", "批 0B 授权门"))
        self.assertEqual(doing["evidence"], "11-18 10:00 CURRENT 这一项最近一次改动")
        self.assertEqual(doing["check_at"], "2026-11-19T10:00:00+08:00")
        self.assertTrue(doing["stalled"])                                   # 24 小时没动
        self.assertIn("PR #218 在审", doing["next"])
        self.assertEqual((waiting["state"], waiting["check_at"], waiting["stalled"]), ("等外部", None, False))
        self.assertEqual(waiting["evidence"], "11-19 09:00 CURRENT 这一项最近一次改动")   # 只有这一行改过
        self.assertEqual((ask["state"], ask["owner"], ask["next"]), ("等你", "常浩", "常浩定：卡片样子等常浩选"))
        self.assertEqual(ask["check_at"], "2026-11-18T12:30:00+08:00")
        self.assertEqual([(x["mark"], x["what"]) for x in led["parked"]], [("⏸", "#136 托管合并"), ("📌", "批 6 驿窗只显示")])
        self.assertEqual([u["line"] for u in led["unparsed"]], [18])
        self.assertEqual(led["coverage"]["mainline"]["old"], [{"project": "dt", "title": "托管上线（#126）｜完成 3/8"}])
        self.assertEqual((led["counts"]["只计数"], led["counts"]["未解析"]), (2, 1))

    def test_inline_question_mark_means_waiting_for_owner(self):
        text = "## 主线：落地｜完成 0/2\n- ▶ 第 1 页：已提交，PR #77、#78，❓ 等常浩合并\n- ▶ 第 1 页：重名的另一项\n"
        self.roster(self.project("th", text))
        rows = self.build(Hub())["rows"]
        self.assertEqual([(r["id"], r["state"]) for r in rows], [("mainline:th/落地/第1页", "等你"), ("mainline:th/落地/第1页~2", "在做")])
        self.assertEqual(rows[0]["next"], "常浩定：等常浩合并")

    def test_same_titles_in_two_sections_do_not_share_an_id(self):
        text = "### 主线：审计修复（v3.0）｜完成 0/1\n📌 全量验证\n▶ 修\n\n### 主线：审计修复（v3.0）｜完成 0/1\n📌 全量验证\n▶ 修\n"
        self.roster(self.project("cc", text))
        led = self.build(Hub())
        self.assertEqual([x["id"] for x in led["parked"]], ["mainline:cc/审计修复/全量验证", "mainline:cc/审计修复/全量验证~2"])
        self.assertEqual(sorted(r["id"] for r in led["rows"]), ["mainline:cc/审计修复/修", "mainline:cc/审计修复/修~2"])

    def test_project_without_current_or_repo_is_reported_not_fatal(self):
        gone = {"id": "gone", "name": "gone", "path": str(self.root / "不存在"), "main": "main", "category": "活跃", "github": None}
        self.roster(self.project("empty"), gone, self.project("old", CURRENT, category="归档"))
        led = self.build(Hub())
        self.assertEqual(led["rows"], [])
        self.assertEqual(led["coverage"]["mainline"]["none"], ["empty", "gone"])
        self.assertEqual(led["coverage"]["projects"], 2)   # 归档的项目不收


class Mismatch(Base):
    def test_which_numbers_a_pending_phrase_points_at(self):
        cases = {
            "2026-11-19 · #244 亚古兽「思考主线」技能、核验技能收窄（PR 待常浩合并，已安装）": [244],
            "#253 对齐驿窗 postpane#466：没送成的不算（2026-11-20，Claude；PR 待常浩定合并）": [253],
            "2026-11-20 · #99 M5 小修（PR #102 待常浩合并，无需安装）": [102],
            "第 1 页：常浩选 B 版；验收通过，已提交，PR #77（文档）、#78（代码，叠在 #77 上），❓ 等常浩合并": [77, 78],
            "PR #77 已合并，PR #78 待合并": [78],
            "#5 做完（PR #6 未合并）；#7 另一件事": [6],
            "没有号（PR 待合并）": [],
            "循环首次跑通（[PR #40](https://github.com/o/dt/pull/40) 送审中，[PR #39](https://github.com/o/dt/pull/39) PASS 待合并）": [39],
        }
        for text, want in cases.items():
            self.assertEqual(ol.pending_numbers(text), want, text)
        self.assertIsNone(ol.PENDING.search("PR #240 已合并"))

    def test_fact_of(self):
        merged = {"__typename": "PullRequest", "state": "MERGED", "mergedAt": "2026-11-20T03:00:00Z"}
        closed = {"__typename": "PullRequest", "state": "CLOSED", "closedAt": "2026-11-20T03:00:00Z"}
        self.assertEqual(ol.fact_of(8, merged), "PR #8 已合并（11-20 11:00）")
        self.assertEqual(ol.fact_of(8, closed), "PR #8 已关闭、没合并（11-20 11:00）")
        self.assertIsNone(ol.fact_of(8, {"__typename": "PullRequest", "state": "OPEN"}))

        def issue(state, *prs):
            return {"__typename": "Issue", "state": state, "closedAt": "2026-11-20T04:00:00Z",
                    "closedByPullRequestsReferences": {"nodes": [{"number": n, "state": s, "mergedAt": "2026-11-20T03:00:00Z"} for n, s in prs]}}
        self.assertEqual(ol.fact_of(7, issue("CLOSED", (9, "MERGED"))), "issue #7 的 PR #9 已合并（11-20 11:00）")
        self.assertIsNone(ol.fact_of(7, issue("OPEN", (9, "MERGED"), (10, "OPEN"))))   # 还有一个 PR 开着：不算对不上
        self.assertIsNone(ol.fact_of(7, issue("OPEN")))
        self.assertEqual(ol.fact_of(7, issue("CLOSED")), "issue #7 已关闭（11-20 12:00），没找到它的 PR")
        self.assertIsNone(ol.fact_of(7, issue("CLOSED", (9, "MERGED")), named=[7, 9]))   # PR #9 自己点了名，不重复说

    def test_stale_pending_headings_and_items_are_listed(self):
        text = CURRENT.replace("- ⏳ 0D 恢复门", "- ⏳ 0D 恢复门：PR #211 待合并")
        self.roster(self.project("dt", text))
        numbers = {
            253: {"__typename": "Issue", "state": "CLOSED", "closedByPullRequestsReferences": {"nodes": [
                {"number": 262, "state": "MERGED", "mergedAt": "2026-11-20T09:00:00Z"}]}},
            246: {"__typename": "Issue", "state": "OPEN", "closedByPullRequestsReferences": {"nodes": [{"number": 247, "state": "OPEN"}]}},
            211: {"__typename": "PullRequest", "state": "MERGED", "mergedAt": "2026-11-20T08:00:00Z"},
        }
        hub = Hub(numbers={"o/dt": numbers})
        led = self.build(hub)
        got = {m["where"]: m for m in led["mismatches"]}
        self.assertEqual(sorted(got), ["CURRENT.md 第 13 行（主线项）", "CURRENT.md 第 3 行（节标题）"])   # #246 的 PR 还开着，不算
        self.assertEqual(got["CURRENT.md 第 3 行（节标题）"]["fact"], "issue #253 的 PR #262 已合并（11-20 17:00）")
        self.assertIn("PR 待常浩定合并", got["CURRENT.md 第 3 行（节标题）"]["text"])
        self.assertEqual(got["CURRENT.md 第 13 行（主线项）"]["fact"], "PR #211 已合并（11-20 16:00）")
        self.assertEqual(led["counts"]["对不上"], 2)
        self.assertEqual(hub.calls.count(("o/dt", "lookup")), 1)   # 一个仓库只查一次

    def test_lookup_failure_is_reported_not_silently_clean(self):
        self.roster(self.project("dt", CURRENT))
        led = self.build(Hub(down={"o/dt"}))
        self.assertEqual(led["mismatches"], [])
        self.assertIn("dt", led["coverage"]["mismatch_unchecked"])
        self.assertIn("对不上·dt", "\n".join(ol.gaps(led)))


class Ledger(Base):
    def setUp(self):
        super().setUp()
        self.roster(self.project("dt", CURRENT), self.project("local-only", "## 主线：本地项目｜完成 0/1\n- ▶ 甲\n", github=False))
        self.prs = [pr(1, "没人审的", checks=[run("test")]), pr(2, "红的", checks=[run("test", "FAILURE")]),
                    pr(3, "草稿", draft=True, checks=[run("test")])]

    def scan(self, hub, now=NOW):
        with mock.patch.object(ol.merge_officer, "load_rules", return_value=RULES):
            return ol.scan(now=now, fetch=False, hub=hub, folder=self.out)

    def test_scan_writes_ledger_and_daily_snapshot(self):
        led = self.scan(Hub(prs={"o/dt": self.prs}))
        saved = json.loads((self.out / "ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(saved, led)
        self.assertEqual(json.loads((self.out / "history" / "2026-11-20.json").read_text(encoding="utf-8")), led)
        self.assertEqual(sorted(p.name for p in self.out.rglob("*") if p.is_file()), ["2026-11-20.json", "ledger.json"])
        ids = [r["id"] for r in led["rows"]]
        self.assertEqual(ids[0], "pr:dt#2")                       # 卡住的排最前
        self.assertEqual({i for i in ids if i.startswith("pr:")}, {"pr:dt#1", "pr:dt#2", "pr:dt#3"})
        self.assertIn("mainline:id-local-only/本地项目/甲", ids)   # 没有 GitHub 仓库的项目用名册 id
        self.assertEqual(led["coverage"]["github"], {"ok": ["dt"], "failed": {}, "no_repo": ["id-local-only"], "capped": []})
        self.assertEqual(led["counts"]["卡住"], 1)
        for r in led["rows"]:
            self.assertEqual(set(r) >= {"id", "what", "state", "owner", "evidence", "next", "check_at", "blocked", "done_when", "links", "since"}, True)
            self.assertEqual(r["since"], "2026-11-20T16:00:00+08:00")

    def test_since_survives_and_closed_prs_leave(self):
        self.scan(Hub(prs={"o/dt": self.prs}))
        later = NOW + dt.timedelta(hours=3)
        led = self.scan(Hub(prs={"o/dt": self.prs[:1] + [pr(4, "新开的", checks=[run("test")])]}), now=later)
        rows = {r["id"]: r for r in led["rows"]}
        self.assertNotIn("pr:dt#2", rows)
        self.assertEqual(rows["pr:dt#1"]["since"], "2026-11-20T16:00:00+08:00")
        self.assertEqual(rows["pr:dt#4"]["since"], "2026-11-20T19:00:00+08:00")
        self.assertTrue(rows["pr:dt#1"]["stalled"])     # 14:00 提交、4 小时没人审，19:00 已过点
        self.assertEqual(led["counts"]["停了"], sum(1 for r in led["rows"] if r["stalled"]))

    def test_not_stalled_before_check_time(self):
        led = self.scan(Hub(prs={"o/dt": self.prs}))
        self.assertFalse(next(r for r in led["rows"] if r["id"] == "pr:dt#1")["stalled"])   # 16:00 < 18:00

    def test_unreachable_repo_keeps_its_rows_marked_carried(self):
        self.scan(Hub(prs={"o/dt": self.prs}))
        led = self.scan(Hub(prs={"o/dt": []}, down={"o/dt"}), now=NOW + dt.timedelta(hours=1))
        carried = [r for r in led["rows"] if r.get("carried")]
        self.assertEqual({r["id"] for r in carried}, {"pr:dt#1", "pr:dt#2", "pr:dt#3"})
        self.assertIn("dt", led["coverage"]["github"]["failed"])
        text = ol.render(led, now=NOW + dt.timedelta(hours=1))
        self.assertIn("GitHub·dt：", text)
        self.assertIn("（沿用上一份）", text)

    def test_pagination_collects_every_page_and_flags_the_cap(self):
        many = [pr(n, checks=[run("test")]) for n in range(1, 6)]
        hub = Hub(prs={"o/dt": many}, page=2)
        self.assertEqual(len([r for r in self.scan(hub)["rows"] if r["source"] == "pr"]), 5)
        with mock.patch.object(ol, "PR_PAGES", 2):
            led = self.scan(Hub(prs={"o/dt": many}, page=2))
        self.assertEqual(led["coverage"]["github"]["capped"], ["dt"])

    def test_render_and_show(self):
        hub = Hub(prs={"o/dt": self.prs}, numbers={"o/dt": {253: {"__typename": "PullRequest", "state": "MERGED", "mergedAt": "2026-11-20T03:00:00Z"}}})
        text = ol.render(self.scan(hub), now=NOW)
        for part in ("未闭环账本｜11-20 16:00 扫描", "【卡住】1", "pr:dt#2｜红的", "卡在：CI 失败：test", "【待验证】1", "【在做】",
                     "【记录和事实对不上】1", "PR #253 已合并（11-20 11:00）", "【主线清单里没解析出来的】1", "【只计数，不催】⏸ 1｜📌 1",
                     "【没收的旧主线】dt「托管上线（#126）｜完成 3/8」", "下次看：不催"):
            self.assertIn(part, text)
        self.assertNotIn("已超过 24 小时", text)
        self.assertIn("已超过 24 小时", ol.render(json.loads((self.out / "ledger.json").read_text()), now=NOW + dt.timedelta(hours=25)))
        with mock.patch.dict(os.environ, {"DT_OPENLOOP_DIR": str(self.out), "DT_NOW": "2026-11-20T16:05:00+08:00"}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(ol.main(["show"]), 0)
            self.assertIn("【卡住】1", buf.getvalue())
        with mock.patch.dict(os.environ, {"DT_OPENLOOP_DIR": str(self.root / "没有")}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(ol.main(["show"]), 1)
            self.assertIn("先跑 scan", buf.getvalue())

    def test_bad_roster_fails_loudly_and_leaves_the_ledger_alone(self):
        self.scan(Hub(prs={"o/dt": self.prs}))
        before = (self.out / "ledger.json").read_text()
        with mock.patch.object(progress_data, "ROSTER", self.root / "没有名册.json"):
            with self.assertRaises(ol.Fail):
                self.scan(Hub())
        self.assertEqual((self.out / "ledger.json").read_text(), before)


class RealGhPath(Base):
    """走一遍真的子进程：pdaily_data.GitHub 调假 gh 命令。"""

    def test_scan_through_gh_command(self):
        self.roster(self.project("dt"))
        state = self.root / "gh"
        fake_gh.install(self.root / "bin", state)
        (state / "api.json").write_text(json.dumps({"graphql": {"data": {"repository": {"pullRequests": {
            "nodes": [pr(7, "经假 gh 来的", checks=[run("test")])], "pageInfo": {"hasNextPage": False}}}}}}), encoding="utf-8")
        env = {"PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(state), "DT_OPENLOOP_DIR": str(self.out)}
        with mock.patch.dict(os.environ, env):
            led = ol.scan(now=NOW, fetch=False)
        self.assertEqual([r["id"] for r in led["rows"]], ["pr:dt#7"])
        self.assertTrue((self.out / "ledger.json").exists())
        call = fake_gh.calls(state)[0]
        self.assertEqual(call[:2], ["api", "graphql"])
        self.assertIn("o=o", call)
        self.assertIn("n=dt", call)

    def test_gh_down_reports_every_repo_and_writes_no_rows(self):
        self.roster(self.project("dt"))
        state = self.root / "gh"
        fake_gh.install(self.root / "bin", state)
        (state / "fail").write_text("error connecting to api.github.com\n")
        env = {"PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(state),
               "DT_OPENLOOP_DIR": str(self.out), "DT_NET_WAIT_SECONDS": "0"}
        with mock.patch.dict(os.environ, env):
            led = ol.scan(now=NOW, fetch=False)
        self.assertEqual(led["rows"], [])
        self.assertIn("dt", led["coverage"]["github"]["failed"])


# ====================== 5a-2：桌面会话、驿窗送审、并行 ======================

def ms(s):
    return int(ol.parse_ts(s).timestamp() * 1000)


def step(kind, at, **kw):
    return {"at": ms(at), "kind": kind, **kw}


def handoff(id_="h1", status="open", phase="working", steps=(), pr_=None, cwd=None, session="cli-1", risk="L3", head=HEAD,
            halt=None, error=None, rounds=1, title="送审｜改点东西"):
    """驿窗 handoffs.json 里的一条接力（字段名照 handoffs.rs 的 camelCase）。"""
    steps = list(steps)
    last = steps[-1]["at"] if steps else ms("2026-11-20T14:00:00+08:00")
    h = {"id": id_, "title": title, "claudeSession": session, "cwd": cwd, "chatgptConversation": "c", "status": status,
         "createdAt": steps[0]["at"] if steps else last, "updatedAt": last, "rounds": rounds, "phase": phase, "phaseAt": last,
         "steps": steps, "attempt": 0, "auto": True}
    for k, v in (("pr", pr_), ("risk", risk), ("head", head), ("halt", halt), ("error", error)):
        if v is not None:
            h[k] = v
    return h


WORK, ASK = step("work", "2026-11-20T14:10:00+08:00"), step("ask", "2026-11-20T14:10:00+08:00")
REVISE = step("reply", "2026-11-20T14:40:00+08:00", verdict="revise")
PASS = step("reply", "2026-11-20T14:40:00+08:00", verdict="pass")
BACK = step("back", "2026-11-20T14:40:00+08:00", delivery="started", auto=True)


def session_row(tag, word="等你决定", title="某个会话", status="要选一版。", next_="回复「选甲」。", at="2026-11-20T06:00:00.000Z", **flags):
    """decision_inbox.scan() 返回的一行（字段照它的写法）；flags 是 sift() 补的标记。"""
    sid = f"local_{tag * 8}-0000-4000-8000-000000000001"
    return {"id": sid, "id8": tag * 8, "title": title, "project": "x", "word": word, "status": status, "next": next_,
            "statusAt": at, "stoppedDays": 0.1, "busy": False, "userAfter": [], "prs": [], "parent": "", "children": [], **flags}


class FakeInbox:
    """拍板清单脚本的替身：scan 给全部带状态行的会话，sift 只留等常浩的（drop 里的 id8 当作被筛掉）。"""
    WAITING = ("等你决定", "等你操作", "受阻")
    META_DIR = PROJECTS_DIR = INBOX_DIR = "/不存在"

    def __init__(self, rows, drop=(), skipped=None):
        self.rows, self.drop = rows, set(drop)
        self.skipped = skipped or {"已归档": 2, "太久没动": 3, "找不到记录": 0, "没写状态行": 4, "坏行": 0}

    def scan(self, meta_dir, projects_dir, days, now, only=None):
        return [dict(r) for r in self.rows], dict(self.skipped)

    def load_ignored(self, inbox_dir):
        return {}

    def listed_before(self, inbox_dir):
        return None, set()

    def sift(self, rows, ignored, before, now=None):
        kept = [r for r in rows if r["word"] in self.WAITING and r["id8"] not in self.drop]
        for r in kept:
            for k in ("replyUnanswered", "prDone", "peerAfter", "listedBefore"):
                r.setdefault(k, False)
        return kept, {"已交给下游会话": len(self.drop), "你已回复、还在做": 0, "在忽略名单里": 0}


class Sources(Base):
    """会话和接力要用的夹具：名册一个项目 dt（o/dt），会话元数据目录、handoffs.json 都在临时目录。"""

    def setUp(self):
        super().setUp()
        self.p = self.project("dt")
        self.roster(self.p)
        self.meta = self.root / "meta"
        self.meta.mkdir()
        self.handoffs = self.root / "handoffs.json"
        env = mock.patch.dict(os.environ, {"DT_OPENLOOP_META_DIR": str(self.meta), "DT_POSTPANE_HANDOFFS": str(self.handoffs)})
        env.start()
        self.addCleanup(env.stop)
        self.inbox(FakeInbox([]))

    def inbox(self, fake):
        p = mock.patch.object(ol, "inbox_module", return_value=fake)
        p.start()
        self.addCleanup(p.stop)

    def meta_of(self, row, cwd=None, prs=(), cli=None, origin=None):
        m = {"sessionId": row["id"], "title": row["title"], "cwd": cwd or self.p["path"], "originCwd": origin or cwd or self.p["path"],
             "cliSessionId": cli or f"cli-{row['id8'][0]}", "prs": [{"prNumber": n, "repo": repo, "state": "OPEN"} for repo, n in prs]}
        (self.meta / f"{row['id']}.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")

    def relays(self, *items):
        self.handoffs.write_text(json.dumps(list(items), ensure_ascii=False), encoding="utf-8")

    def rows(self, hub=None, prev=None, now=NOW):
        led = self.build(hub or Hub(), prev=prev, now=now)
        return {r["id"]: r for r in led["rows"]}, led


class Sessions(Sources):
    def test_waiting_session_waits_for_owner_until_next_slip(self):
        a = session_row("a", "等你操作", "周报｜卡片样式", "PR #12 等你批准。", "回复「合并」。")
        self.meta_of(a)
        self.inbox(FakeInbox([a]))
        rows, led = self.rows()
        r = rows[f"session:{a['id']}"]
        self.assertEqual((r["state"], r["owner"], r["what"], r["next"], r["project"]), ("等你", "常浩", "周报｜卡片样式", "回复「合并」。", "dt"))
        self.assertEqual(r["evidence"], "11-20 14:00 会话最后一次答复〔等你操作〕：PR #12 等你批准。")
        self.assertEqual((r["check_at"], r["stalled"]), ("2026-11-20T21:00:00+08:00", False))
        self.assertEqual(led["coverage"]["sessions"]["rows"], 1)

    def test_blocked_session_is_stuck(self):
        a = session_row("a", "受阻", status="缺 sudo 权限。", next_="常浩跑一下 sudo 那条命令。")
        self.meta_of(a)
        self.inbox(FakeInbox([a]))
        r = self.rows()[0][f"session:{a['id']}"]
        self.assertEqual((r["state"], r["owner"], r["blocked"], r["next"]), ("卡住", "未知", "缺 sudo 权限。", "常浩跑一下 sudo 那条命令。"))

    def test_background_session_uses_its_own_estimate_or_two_hours(self):
        a = session_row("a", "后台进行中", "全量测试", "全量测试在跑，约 40 分钟后出结果。")
        b = session_row("b", "后台进行中", "安装", "后台会话在装。", at="2026-11-20T05:00:00.000Z")
        for x in (a, b):
            self.meta_of(x)
        self.inbox(FakeInbox([a, b]))
        rows, _ = self.rows()
        ra, rb = rows[f"session:{a['id']}"], rows[f"session:{b['id']}"]
        self.assertEqual((ra["state"], ra["owner"], ra["check_at"], ra["stalled"]), ("在做", "会话「全量测试」", "2026-11-20T14:40:00+08:00", True))
        self.assertEqual((rb["check_at"], rb["stalled"]), ("2026-11-20T15:00:00+08:00", True))   # 13:00 + 2 小时，16:00 已过

    def test_reply_nobody_picked_up_is_stuck(self):
        a = session_row("a", replyUnanswered=True, userAfter=[{"at": "2026-11-20T05:30:00.000Z", "text": "选甲", "peer": False}])
        self.meta_of(a)
        self.inbox(FakeInbox([a]))
        r = self.rows()[0][f"session:{a['id']}"]
        self.assertEqual((r["state"], r["owner"]), ("卡住", "未知"))
        self.assertIn("常浩回过话", r["blocked"])
        self.assertEqual(r["evidence"], "11-20 13:30 常浩回过话，会话之后没有新状态")

    def test_only_roster_projects_and_worktrees_count(self):
        a, b, c = session_row("a"), session_row("b"), session_row("c", "已结束")
        self.meta_of(a, cwd=str(self.root / "scratch-workspaces" / "x"))
        self.meta_of(b, cwd=self.p["path"] + "/.claude/worktrees/273-x", origin=self.p["path"])
        self.meta_of(c)
        self.inbox(FakeInbox([a, b, c], drop=()))
        rows, led = self.rows()
        self.assertEqual(sorted(rows), [f"session:{b['id']}"])
        cov = led["coverage"]["sessions"]
        self.assertEqual((cov["not_personal"], cov["rows"], cov["skipped"]["没写状态行"], cov["dropped"]["已交给下游会话"]), (1, 1, 4, 0))

    def test_missing_script_is_reported_and_previous_rows_are_kept(self):
        a = session_row("a")
        self.meta_of(a)
        self.inbox(FakeInbox([a]))
        _, first = self.rows()
        with mock.patch.object(ol, "inbox_module", side_effect=ol.Fail("找不到拍板清单脚本")):
            rows, led = self.rows(prev=first, now=NOW + dt.timedelta(minutes=5))
        self.assertTrue(rows[f"session:{a['id']}"]["carried"])
        self.assertEqual(led["coverage"]["sessions"]["failed"], "找不到拍板清单脚本")
        self.assertIn("会话：找不到拍板清单脚本", "\n".join(ol.gaps(led)))


REAL_INBOX = _isolation.REAL_HOME / "Documents/ChatGPT/个人/work/my-skills/skills/decision-inbox/scripts/decision_inbox.py"


@unittest.skipUnless(REAL_INBOX.exists(), "本机没有拍板清单脚本（CI 上没有 my-skills），跳过对真脚本的核对")
class RealInbox(Base):
    """对着真的拍板清单脚本跑一遍：它的 scan / sift 返回的字段变了，这里先红。"""

    def test_real_scan_and_sift_feed_the_ledger(self):
        p = self.project("dt")
        self.roster(p)
        meta, logs = self.root / "meta", self.root / "projects" / "-x"
        meta.mkdir()
        logs.mkdir(parents=True)

        def session(tag, text, at, extra=()):
            sid, cli = f"local_{tag * 8}-0000-4000-8000-000000000001", f"cli-{tag}"
            (meta / f"{sid}.json").write_text(json.dumps({"sessionId": sid, "title": f"会话{tag}", "cwd": p["path"], "cliSessionId": cli,
                                                           "isArchived": False, "prs": []}), encoding="utf-8")
            lines = [{"type": "assistant", "timestamp": at, "message": {"content": [{"type": "text", "text": text}]}}, *extra]
            (logs / f"{cli}.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines), encoding="utf-8")
            return f"session:{sid}"
        wait = session("a", "做完了。\n\n**当前状态**：〔等你决定〕要选一版。\n**下一步**：回复「选甲」。", "2026-11-20T06:00:00.000Z")
        busy = session("b", "**当前状态**：〔后台进行中〕测试在跑，约 30 分钟。\n**下一步**：等结果。", "2026-11-20T07:00:00.000Z")
        session("c", "**当前状态**：〔已结束〕都好了。\n**下一步**：本任务已结束。", "2026-11-20T07:00:00.000Z")
        env = {"DT_DECISION_INBOX": str(REAL_INBOX), "DT_OPENLOOP_META_DIR": str(meta), "DT_OPENLOOP_LOGS_DIR": str(self.root / "projects"),
               "DT_OPENLOOP_INBOX_DIR": str(self.root / "inbox"), "DT_POSTPANE_HANDOFFS": str(self.root / "没有.json")}
        with mock.patch.dict(os.environ, env):
            led = self.build(Hub())
        rows = {r["id"]: r for r in led["rows"]}
        self.assertEqual(sorted(rows), sorted([wait, busy]))
        self.assertEqual((rows[wait]["state"], rows[wait]["next"]), ("等你", "回复「选甲」。"))
        self.assertEqual((rows[busy]["state"], rows[busy]["check_at"]), ("在做", "2026-11-20T15:30:00+08:00"))


class Relays(Sources):
    def h(self, id_="h1", **kw):
        """接力默认在项目目录里发起、没记 PR；要并到 PR 的用例自己写 pr_。"""
        kw.setdefault("cwd", self.p["path"])
        return handoff(id_, **kw)

    def relay_row(self, *items, hub=None):
        self.relays(*items)
        rows, led = self.rows(hub)
        return rows.get(f"relay:{items[0]['id']}"), rows, led

    def test_sent_and_waiting_is_external(self):
        r, _, _ = self.relay_row(self.h(steps=[WORK, ASK], rounds=2))
        self.assertEqual((r["state"], r["owner"], r["next"], r["project"]), ("等外部", "ChatGPT", "等 L3 第 2 轮回信", "dt"))
        self.assertEqual(r["evidence"], "11-20 14:10 已送 ChatGPT 审查，对方在看")
        self.assertEqual(r["check_at"], "2026-11-20T16:10:00+08:00")

    def test_reply_not_returned_is_stuck_after_thirty_minutes(self):
        expired = step("back", "2026-11-20T14:40:00+08:00", delivery="expired")
        for steps, phase in (([WORK, ASK, REVISE], "replied"), ([WORK, ASK, REVISE, expired], "replied"),
                             ([WORK, ASK, REVISE, step("back", "2026-11-20T14:41:00+08:00", delivery="waiting")], "returned")):
            r, _, _ = self.relay_row(self.h(steps=steps, phase=phase))
            self.assertEqual((r["state"], r["check_at"], r["stalled"]), ("卡住", "2026-11-20T15:10:00+08:00", True), steps[-1])
            self.assertIn("回信没送回会话", r["blocked"])
            self.assertIn("需修改", r["next"])

    def test_failed_handoff_is_stuck(self):
        r, _, _ = self.relay_row(self.h(status="failed", phase="failed", error="ChatGPT 插件没能完成：timeout",
                                        steps=[WORK, step("fail", "2026-11-20T14:11:00+08:00")]))
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "送审失败：ChatGPT 插件没能完成：timeout"))

    def test_halt_hands_it_back_and_counts_as_stuck(self):
        stop = step("stop", "2026-11-20T15:10:00+08:00", auto=True)
        r, _, _ = self.relay_row(self.h(steps=[WORK, ASK, stop], halt="ChatGPT 30 分钟没回信，自动往返停下，交还常浩"))
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "ChatGPT 30 分钟没回信，自动往返停下，交还常浩"))
        self.assertEqual(r["evidence"], "11-20 15:10 驿窗自动往返停下")

    def test_returned_revise_makes_the_pr_stuck_and_merges_into_it(self):
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        relay, rows, _ = self.relay_row(self.h(steps=[WORK, ASK, REVISE, BACK], pr_="O/dt#1"), hub=hub)
        self.assertIsNone(relay)                                   # 并进 PR 那一行了
        r = rows["pr:dt#1"]
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "审查要求修改（驿窗 L3 第 1 轮）"))
        self.assertEqual(r["check_at"], "2026-11-20T18:40:00+08:00")
        self.assertEqual([(x["id"], x["state"]) for x in r["links"]], [("relay:h1", "在做")])

    def test_review_of_an_older_head_leaves_the_pr_unverified(self):
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        _, rows, _ = self.relay_row(self.h(steps=[WORK, ASK, REVISE, BACK], head=OLD, pr_="O/dt#1"), hub=hub)
        self.assertEqual(rows["pr:dt#1"]["state"], "待验证")
        self.assertIn("上一次审的是旧版本（驿窗 L3 第 1 轮）", rows["pr:dt#1"]["evidence"])

    def test_closed_pass_on_head_means_reviewed(self):
        end = step("end", "2026-11-20T14:41:00+08:00", auto=True)
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        relay, rows, _ = self.relay_row(self.h(status="closed", phase="ended", steps=[WORK, ASK, PASS, BACK, end], pr_="O/dt#1"), hub=hub)
        self.assertIsNone(relay)
        self.assertEqual((rows["pr:dt#1"]["state"], rows["pr:dt#1"]["links"]), ("等你", []))
        self.assertIn("驿窗 L3 第 1 轮通过", rows["pr:dt#1"]["evidence"])

    def test_review_in_flight_makes_the_pr_external(self):
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        _, rows, _ = self.relay_row(self.h(steps=[WORK, ASK], pr_="O/dt#1"), hub=hub)
        r = rows["pr:dt#1"]
        self.assertEqual((r["state"], r["owner"], r["next"]), ("等外部", "ChatGPT", "等 L3 第 1 轮回信"))

    def test_closed_handoffs_only_count_when_the_reply_never_got_back(self):
        auto_end = step("end", "2026-11-20T14:41:00+08:00", auto=True, text="结论：通过，回信没能转回 Claude，请告诉它审查结论，自动结束接力")
        by_person = step("end", "2026-11-20T14:41:00+08:00")
        lost = handoff("h1", status="closed", phase="ended", steps=[WORK, ASK, PASS, step("fail", "2026-11-20T14:40:00+08:00"), auto_end], pr_=None, cwd=self.p["path"])
        r, _, _ = self.relay_row(lost)
        self.assertEqual(r["state"], "卡住")
        self.assertIn("通过", r["next"])
        self.assertIsNone(self.relay_row(handoff("h1", status="closed", phase="ended", steps=[WORK, ASK, PASS, by_person], pr_=None, cwd=self.p["path"]))[0])
        newer = handoff("h2", status="closed", phase="ended", steps=[step("work", "2026-11-20T15:00:00+08:00"), step("ask", "2026-11-20T15:00:00+08:00"),
                        step("reply", "2026-11-20T15:20:00+08:00", verdict="pass"), step("back", "2026-11-20T15:20:00+08:00", delivery="started"),
                        step("end", "2026-11-20T15:20:00+08:00", auto=True)], pr_=None, cwd=self.p["path"])
        self.assertIsNone(self.relay_row(lost, newer)[0])            # 同一个会话后来又送了一次：旧的不算
        old = handoff("h1", status="closed", phase="ended", pr_=None, cwd=self.p["path"], steps=[
            step("work", "2026-11-10T10:00:00+08:00"), step("ask", "2026-11-10T10:00:00+08:00"),
            step("reply", "2026-11-10T10:30:00+08:00", verdict="pass"), step("end", "2026-11-18T23:04:00+08:00", auto=True)])
        r, _, led = self.relay_row(old)
        self.assertIsNone(r)                                         # 回信是 10 天前的；驿窗两天前才自动收尾也不算新
        self.assertEqual(led["coverage"]["relays"]["too_old"], 1)

    def test_relay_of_a_pr_that_is_no_longer_open_is_done(self):
        lost = self.h(steps=[WORK, ASK, REVISE], phase="replied", pr_="O/dt#9")
        r, _, led = self.relay_row(lost, hub=Hub(prs={"o/dt": [pr(1, checks=[run("test")])]}))
        self.assertIsNone(r)
        self.assertEqual((led["coverage"]["relays"]["pr_done"], led["coverage"]["relays"]["rows"]), (1, 0))
        r, _, led = self.relay_row(lost, hub=Hub(down={"o/dt"}))     # GitHub 没取到：不知道 PR 还开不开，照收
        self.assertEqual(r["state"], "卡住")

    def test_only_roster_projects_count(self):
        r, _, led = self.relay_row(handoff(steps=[WORK, ASK], pr_="someone/else#3", cwd="/Users/x/scratch"))
        self.assertIsNone(r)
        self.assertEqual(led["coverage"]["relays"]["not_personal"], 1)

    def test_missing_or_broken_file(self):
        _, led = self.rows()
        self.assertEqual(led["coverage"]["relays"]["missing"], True)
        self.assertNotIn("驿窗送审", "\n".join(ol.gaps(led)))        # 没装驿窗不算出错
        self.relays(self.h(steps=[WORK, ASK]))
        _, first = self.rows()
        self.handoffs.write_text("{坏的", encoding="utf-8")
        rows, led = self.rows(prev=first)
        self.assertTrue(rows["relay:h1"]["carried"])
        self.assertIn("驿窗送审：", "\n".join(ol.gaps(led)))


class Merge(Sources):
    def test_mainline_item_naming_one_open_pr_joins_it(self):
        text = "## 主线：底座（#188）｜完成 0/2\n- ❓ 0D 恢复门：PR #1 等常浩看\n- ▶ 批 2：PR #1、#2 都在做\n"
        commit_current(Path(self.p["path"]), text, "2026-11-20 09:00:00 +0800")
        hub = Hub(prs={"o/dt": [pr(1, "恢复门", checks=[run("test")]), pr(2, "派活", draft=True, checks=[run("test")])]})
        rows, led = self.rows(hub)
        self.assertEqual(sorted(rows), ["mainline:dt/#188/批2", "pr:dt#1", "pr:dt#2"])
        one = rows["pr:dt#1"]
        self.assertEqual((one["state"], one["owner"], one["what"]), ("等你", "常浩", "恢复门"))   # PR 是待验证，主线项是等你：取更需要人的
        self.assertEqual(one["next"], "常浩定：PR #1 等常浩看")
        self.assertEqual([(x["id"], x["state"]) for x in one["links"]], [("mainline:dt/#188/0D恢复门", "等你"), ("mainline:dt/#188/批2", "在做")])
        self.assertTrue(one["links"][1]["related"])                 # 提到两个 PR 的那一项没并进来，只是互相记一笔
        self.assertEqual([(x["id"], x.get("related")) for x in rows["mainline:dt/#188/批2"]["links"]], [("pr:dt#1", True), ("pr:dt#2", True)])
        self.assertEqual(led["counts"]["等你"], 1)

    def test_session_relay_mainline_and_pr_become_one_row(self):
        text = "## 主线：底座（#188）｜完成 0/1\n- ▶ 0D 恢复门：PR #1 在审\n"
        commit_current(Path(self.p["path"]), text, "2026-11-20 09:00:00 +0800")
        a = session_row("a", "后台进行中", "0D 恢复门", "送审中，约 30 分钟。")
        self.meta_of(a, prs=[("o/dt", 1)], cli="cli-1")
        self.inbox(FakeInbox([a]))
        self.relays(handoff(steps=[WORK, ASK, REVISE], pr_="O/dt#1"))        # 回信没送回会话
        rows, led = self.rows(Hub(prs={"o/dt": [pr(1, "恢复门", checks=[run("test")])]}))
        self.assertEqual(list(rows), ["pr:dt#1"])
        r = rows["pr:dt#1"]
        self.assertEqual(r["state"], "卡住")
        self.assertEqual(r["blocked"], "回信没送回会话；审查要求修改（驿窗 L3 第 1 轮）")   # 两条都是卡住：都写上，先到点的在前
        self.assertEqual(r["check_at"], "2026-11-20T15:10:00+08:00")
        relay = next(x for x in r["links"] if x["id"] == "relay:h1")
        self.assertIn("需修改", relay["next"])                                # 并进来的那条自己的下一步还查得到
        self.assertIn("relay:h1（卡住，下一步：把「需修改」的回信转给发起的会话", ol.render(led, now=NOW))
        self.assertEqual(sorted(x["id"] for x in r["links"]), ["mainline:dt/#188/0D恢复门", "relay:h1", f"session:{a['id']}"])
        self.assertEqual(len(led["rows"]), 1)

    def test_relay_without_pr_joins_its_session(self):
        a = session_row("a", "等你决定", "思维评审")
        self.meta_of(a, cli="cli-9")
        self.inbox(FakeInbox([a]))
        self.relays(handoff(steps=[WORK, ASK], pr_=None, cwd=self.p["path"], session="cli-9", risk=None))
        rows, _ = self.rows()
        self.assertEqual(list(rows), [f"session:{a['id']}"])
        self.assertEqual([x["id"] for x in rows[f"session:{a['id']}"]["links"]], ["relay:h1"])
        self.assertEqual(rows[f"session:{a['id']}"]["state"], "等你")

    def test_since_is_the_earliest_of_the_merged_parts(self):
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        _, first = self.rows(hub)                                   # 16:00：只有 PR
        self.assertEqual([r["id"] for r in first["rows"]], ["pr:dt#1"])
        a = session_row("a", "等你操作", "0D")
        self.meta_of(a, prs=[("o/dt", 1)])
        self.inbox(FakeInbox([a]))                                   # 17:00：记着这个 PR 的会话才出现
        rows, second = self.rows(hub, prev=first, now=NOW + dt.timedelta(hours=1))
        self.assertEqual(list(rows), ["pr:dt#1"])
        self.assertEqual(rows["pr:dt#1"]["since"], "2026-11-20T16:00:00+08:00")              # 整行按最早进账本的那部分算
        self.assertEqual(rows["pr:dt#1"]["links"][0]["since"], "2026-11-20T17:00:00+08:00")  # 会话自己是 17:00 才进来的
        rows, _ = self.rows(hub, prev=second, now=NOW + dt.timedelta(hours=2))
        self.assertEqual(rows["pr:dt#1"]["links"][0]["since"], "2026-11-20T17:00:00+08:00")  # 并进去的部分下次还记得自己的时间


if __name__ == "__main__":
    unittest.main()
