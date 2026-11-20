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


if __name__ == "__main__":
    unittest.main()
