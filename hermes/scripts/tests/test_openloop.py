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

    def test_draft_with_a_red_check_is_stuck(self):
        r = self.row(draft=True, checks=[run("test", "FAILURE")])
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "CI 失败：test"))

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
        self.assertEqual(ol.mainlines("## 2026-11-19 · #239 工作台主线页\n- ▶ 甲\n主线暂停于：0D\n- ▶ 乙\n"), [])

    def test_more_header_styles(self):
        for head in ("## 主线", "## 主线清单：底座", "**主线：底座**", "## 当前主线：底座", "### 主线: 底座"):
            got = ol.mainlines(f"{head}\n\n1. ▶ 甲\n2、✅ 乙\n- ⏳ 丙\n")
            self.assertEqual([[i["mark"] + i["text"] for i in s["items"]] for s in got], [["▶甲", "✅乙", "⏳丙"]], head)

    def test_one_line_mainline_is_split_into_items(self):
        text = "- 别的\n  - 主线：接力根治｜完成 2/4：✅ S1（#423）✅ 全链路梳理 ⏳ 第 0 批装机（等 #442 合并）；▶️ 第 1 批\n- 主线「设计规范」（完成 1/2）：✅ 提炼；❓ 机检做不做\n"
        got = ol.mainlines(text)
        self.assertEqual([(s["title"], s["line"]) for s in got], [("接力根治｜完成 2/4", 2), ("「设计规范」（完成 1/2）", 3)])
        self.assertEqual([i["mark"] + i["text"] for i in got[0]["items"]], ["✅S1（#423）", "✅全链路梳理", "⏳第 0 批装机（等 #442 合并）", "▶第 1 批"])
        self.assertEqual([i["mark"] + i["text"] for i in got[1]["items"]], ["✅提炼", "❓机检做不做"])
        self.roster(self.project("pp", text))
        led = self.build(Hub())
        self.assertEqual(sorted((r["id"], r["state"]) for r in led["rows"]),
                         [("mainline:pp/接力根治/第0批装机", "等外部"), ("mainline:pp/接力根治/第1批", "在做"), ("mainline:pp/设计规范/机检做不做", "等你")])

    def test_what_cannot_be_parsed_is_reported_not_dropped(self):
        text = ("**主线**：底座｜✅2 ▶1 ⏸1\n\n"                      # 答复里的计数行，不是清单
                "## 11-12 · 主线推进｜完成 3/4（见下）\n\n"            # 像主线标题，认不出
                "## 主线：底座｜完成 0/2\n| ▶ | 表格里的一项 |\n- ▶ 正常的一项\n")
        self.roster(self.project("dt", text))
        led = self.build(Hub())
        self.assertEqual([r["id"] for r in led["rows"]], ["mainline:dt/底座/正常的一项"])
        self.assertEqual([(u["line"], u["mainline"]) for u in led["unparsed"]], [(1, "底座｜"), (3, "像主线、没认出来"), (6, "底座｜完成 0/2")])

    def test_only_the_top_section_of_a_repeated_mainline_counts(self):
        text = ("### 主线：审计修复｜完成 2/2\n✅ 第 2 项\n✅ 验证\n\n## 更早的一节\n\n"
                "### 主线：审计修复｜完成 0/2\n❓ 第 2 项：等常浩确认\n📌 验证\n")
        self.roster(self.project("cc", text))
        led = self.build(Hub())
        self.assertEqual((led["rows"], led["parked"]), ([], []))        # 下面那节是这条主线以前的样子：不收
        self.assertEqual(led["coverage"]["mainline"]["old"], [{"project": "cc", "title": "审计修复｜完成 0/2", "why": "同名主线的旧快照，只认最上面一节"}])

    def test_ids_do_not_shift_when_an_earlier_twin_is_done(self):
        for first in ("▶", "✅"):
            self.roster(self.project(f"p{ord(first)}", f"## 主线：落地｜完成 0/2\n- {first} 第 1 页：甲\n- ▶ 第 1 页：乙\n"))
            ids = [r["id"].split("/", 1)[1] for r in self.build(Hub())["rows"] if "乙" in r["next"]]
            self.assertEqual(ids, ["落地/第1页~2"], first)

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
        self.assertEqual(led["coverage"]["mainline"]["old"], [{"project": "dt", "title": "托管上线（#126）｜完成 3/8", "why": "标题写着旧主线"}])
        self.assertEqual((led["counts"]["只计数"], led["counts"]["未解析"]), (2, 1))

    def test_inline_question_mark_means_waiting_for_owner(self):
        text = "## 主线：落地｜完成 0/2\n- ▶ 第 1 页：已提交，PR #77、#78，❓ 等常浩合并\n- ▶ 第 1 页：重名的另一项\n"
        self.roster(self.project("th", text))
        rows = self.build(Hub())["rows"]
        self.assertEqual([(r["id"], r["state"]) for r in rows], [("mainline:th/落地/第1页", "等你"), ("mainline:th/落地/第1页~2", "在做")])
        self.assertEqual(rows[0]["next"], "常浩定：等常浩合并")

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

    def test_two_headings_about_the_same_pr_get_their_own_ids(self):
        text = "## 审计 PR #171 二次刷新（待合并）\n\n## 审计 PR #171 刷新（待合并）\n"
        self.roster(self.project("dt", text))
        hub = Hub(numbers={"o/dt": {171: {"__typename": "PullRequest", "state": "MERGED", "mergedAt": "2026-11-20T03:00:00Z"}}})
        self.assertEqual([m["id"] for m in self.build(hub)["mismatches"]],
                         ["mismatch:dt/CURRENT:#171@节标题", "mismatch:dt/CURRENT:#171@节标题~2"])

    def test_too_many_numbers_is_said_out_loud(self):
        self.roster(self.project("dt", "".join(f"## 第 {n} 件（PR #{n} 待合并）\n\n" for n in range(1, 6))))
        with mock.patch.object(ol, "LOOKUP_MAX", 3):
            led = self.build(Hub())
        self.assertEqual(led["coverage"]["mismatch_unchecked"], {"dt": "「待合并」里提到 5 个号，只核了前 3 个"})

    def test_parked_and_mismatches_survive_a_project_that_cannot_be_read(self):
        self.roster(self.project("dt", CURRENT))
        hub = Hub(numbers={"o/dt": {253: {"__typename": "PullRequest", "state": "MERGED", "mergedAt": "2026-11-20T03:00:00Z"}}})
        first = self.build(hub)
        with mock.patch.object(ol.pdaily_data, "show", side_effect=pdaily_data.Fail("CURRENT.md 不是 UTF-8 编码")):
            led = self.build(hub, prev=first, now=NOW + dt.timedelta(hours=1))
        self.assertEqual(led["coverage"]["mainline"]["failed"], {"dt": "CURRENT.md 不是 UTF-8 编码"})
        for kind in ("rows", "parked", "mismatches"):
            self.assertEqual([x["id"] for x in led[kind]], [x["id"] for x in first[kind]], kind)
            self.assertTrue(all(x["carried"] and x["since"] == "2026-11-20T16:00:00+08:00" for x in led[kind]), kind)

    def test_missing_main_branch_is_a_gap_not_a_project_without_current(self):
        """名册写的主分支在本机和远端都找不到（分支改过名、读到的名册是旧的）：算没取到、沿用上一份，不当成这个项目没有 CURRENT。"""
        p = self.project("dt", CURRENT)
        self.roster(p)
        first = self.build(Hub())
        self.assertTrue(first["rows"])
        self.roster({**p, "main": "改名前的分支"})
        led = self.build(Hub(), prev=first, now=NOW + dt.timedelta(hours=1))
        self.assertNotIn("dt", led["coverage"]["mainline"]["none"])
        self.assertIn("改名前的分支", led["coverage"]["mainline"]["failed"]["dt"])
        self.assertEqual([r["id"] for r in led["rows"]], [r["id"] for r in first["rows"]])
        self.assertTrue(all(r["carried"] for r in led["rows"]))
        self.assertIn("主线清单·dt：找不到主分支", "\n".join(ol.gaps(led)))

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
        self.assertIn("（沿用 11-20 16:00 那份）", text)
        self.assertIn("沿用上一份 3 条｜没取到 2 处", text.splitlines()[0])
        again = self.scan(Hub(prs={"o/dt": []}, down={"o/dt"}), now=NOW + dt.timedelta(hours=2))
        self.assertEqual({r["carried_from"] for r in again["rows"] if r.get("carried")}, {"2026-11-20T16:00:00+08:00"})   # 连着两次没取到：还是记最初那次

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
                     "【没收的旧主线】dt「托管上线（#126）｜完成 3/8」（标题写着旧主线）", "下次看：不催"):
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

    def test_scan_command_reports_failure_in_one_line(self):
        env = {"DT_OPENLOOP_DIR": str(self.out)}
        for boom, want in ((ol.Fail("读不了项目名册"), "账本没更新：读不了项目名册"), (KeyError("意外"), "账本没更新：出错（KeyError：'意外'）")):
            buf = io.StringIO()
            with mock.patch.dict(os.environ, env), mock.patch.object(ol, "scan", side_effect=boom), contextlib.redirect_stdout(buf):
                self.assertEqual(ol.main(["scan", "--no-fetch"]), 1)
            self.assertEqual(buf.getvalue().strip(), want)
        self.assertFalse(self.out.exists())

    def test_scan_command_writes_or_dry_runs(self):
        hub = Hub(prs={"o/dt": self.prs})
        real = ol.scan
        with mock.patch.dict(os.environ, {"DT_OPENLOOP_DIR": str(self.out), "DT_NOW": "2026-11-20T16:00:00+08:00"}), \
                mock.patch.object(ol.merge_officer, "load_rules", return_value=RULES), \
                mock.patch.object(ol, "scan", side_effect=lambda **kw: real(hub=hub, **{**kw, "fetch": False})):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(ol.main(["scan", "--dry-run"]), 0)
            self.assertEqual(json.loads(buf.getvalue())["counts"]["卡住"], 1)
            self.assertFalse(self.out.exists())                      # 试跑不写文件
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertEqual(ol.main(["scan"]), 0)
            self.assertIn("未闭环账本｜11-20 16:00 扫描", buf.getvalue())
            self.assertTrue((self.out / "ledger.json").exists())

    def test_bad_roster_fails_loudly_and_leaves_the_ledger_alone(self):
        self.scan(Hub(prs={"o/dt": self.prs}))
        before = (self.out / "ledger.json").read_text()
        with mock.patch.object(progress_data, "ROSTER", self.root / "没有名册.json"):
            with self.assertRaises(ol.Fail):
                self.scan(Hub())
        self.assertEqual((self.out / "ledger.json").read_text(), before)


class Fetch(Base):
    """scan 默认先拉各项目的远端主分支：读到的是远端现在的 CURRENT，不是本机旧的。"""

    def setUp(self):
        super().setUp()
        self.p = self.project("dt", "## 主线：底座｜完成 0/1\n- ▶ 旧的一项\n")
        self.repo = Path(self.p["path"])
        self.bare = self.root / "remote.git"
        sh(self.root, "clone", "-q", "--bare", str(self.repo), str(self.bare))
        sh(self.repo, "remote", "add", "origin", str(self.bare))
        sh(self.repo, "fetch", "-q", "origin")
        other = self.root / "other"                                   # 别的会话在远端推了新的 CURRENT，本机没拉
        sh(self.root, "clone", "-q", str(self.bare), str(other))
        commit_current(other, "## 主线：底座｜完成 0/1\n- ▶ 远端新写的一项\n", "2026-11-20 15:00:00 +0800")
        sh(other, "push", "-q", "origin", "main")
        self.roster(self.p)

    def whats(self, fetch):
        led = ol.build(NOW, ol.roster(), Hub(), RULES, fetch=fetch)
        return [r["what"] for r in led["rows"]], led["coverage"]["mainline"]["fetch_failed"]

    def test_fetch_reads_what_the_remote_has_now(self):
        self.assertEqual(self.whats(False), (["旧的一项"], {}))
        before = sh(self.repo, "rev-parse", "main"), sh(self.repo, "status", "--porcelain")
        self.assertEqual(self.whats(True), (["远端新写的一项"], {}))
        self.assertEqual((sh(self.repo, "rev-parse", "main"), sh(self.repo, "status", "--porcelain")), before)   # 本地分支和工作区没动

    def test_fetch_failure_and_timeout_are_reported(self):
        sh(self.repo, "remote", "set-url", "origin", str(self.root / "没有这个远端"))
        rows, failed = self.whats(True)
        self.assertEqual(rows, ["旧的一项"])
        self.assertIn("dt", failed)
        real = subprocess.run

        def slow(cmd, **kw):
            if "fetch" in cmd:
                self.assertEqual((kw["timeout"], kw["env"]["GIT_TERMINAL_PROMPT"]), (ol.FETCH_TIMEOUT, "0"))
                raise subprocess.TimeoutExpired(cmd, kw["timeout"])
            return real(cmd, **kw)
        with mock.patch.object(ol.subprocess, "run", side_effect=slow):
            self.assertEqual(self.whats(True)[1], {"dt": "60 秒没拉完"})


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
