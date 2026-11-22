"""隔离自测：openloop.py（未闭环账本，#266 批 5a）。

临时 git 仓库当名册项目，假的 GitHub（进程内按仓库回话；另有一条走假 gh 命令），不联网，账本写进临时目录。
现在 = 2026-11-20 16:00（北京时间）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import datetime as dt
import io
import json
import re
import sqlite3
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

    def test_changes_requested_is_rework_for_four_hours_then_stuck(self):
        at = lambda h, m: dt.datetime(2026, 11, 20, h, m, tzinfo=ol.TZ)   # noqa: E731
        node = pr(checks=[run("test")], reviews=[review("CHANGES_REQUESTED")])   # 14:30 要求修改
        r = ol.pr_row("o/dt", node, RULES, now=at(15, 30))
        self.assertEqual((r["state"], r["blocked"], r["next"], r["check_at"]), ("在做", "", "Claude返修后重新送审", "2026-11-20T18:30:00+08:00"))
        r = ol.pr_row("o/dt", node, RULES, now=at(18, 30))
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "审查要求修改（GitHub 审查）"))

    def test_review_in_flight_is_external_even_with_a_conflict_but_not_with_a_red_check(self):
        flying = {"verdict": None, "flying": True, "at": ol.parse_ts("2026-11-20T07:00:00Z"), "by": "驿窗 第 4 轮", "label": "第 4 轮"}
        r = ol.pr_row("o/dt", pr(checks=[run("test")], mergeable="CONFLICTING"), RULES, reviews=[flying])
        self.assertEqual((r["state"], r["owner"], r["next"], r["blocked"]), ("等外部", "ChatGPT", "等 第 4 轮回信", ""))
        self.assertIn("和主分支有冲突，审完再处理", r["evidence"])
        self.assertEqual(r["active_at"], "2026-11-20T15:00:00+08:00")
        r = ol.pr_row("o/dt", pr(checks=[run("test", "FAILURE")]), RULES, reviews=[flying])
        self.assertEqual(r["state"], "卡住")

    def test_conflict_with_recent_activity_is_in_progress_and_noted(self):
        """11-22 #211：10:12 有提交、10:41 收到「需修改」，主分支一动就有了冲突；在返修、在改的冲突不算卡住，4 小时没动静才算。"""
        at = lambda h, m: dt.datetime(2026, 11, 20, h, m, tzinfo=ol.TZ)   # noqa: E731
        node = pr(checks=[run("test")], mergeable="CONFLICTING", reviews=[review("CHANGES_REQUESTED")])   # 14:00 提交，14:30 要求修改
        r = ol.pr_row("o/dt", node, RULES, now=at(15, 0))
        self.assertEqual((r["state"], r["blocked"]), ("在做", ""))
        self.assertEqual(r["evidence"], "11-20 14:30 GitHub 审查要求修改，Claude返修中（还没有新提交）；和主分支有冲突")
        r = ol.pr_row("o/dt", pr(checks=[run("test")], mergeable="CONFLICTING"), RULES, now=at(15, 0))
        self.assertEqual(r["state"], "在做")
        self.assertIn("；和主分支有冲突", r["evidence"])
        r = ol.pr_row("o/dt", pr(checks=[run("test")], mergeable="CONFLICTING", reviews=[review("APPROVED")]), RULES, now=at(15, 0))
        self.assertEqual((r["state"], r["next"]), ("在做", "Claude变基解决冲突，之后等常浩合并"))   # 审过但有冲突：合不了，不是等你
        r = ol.pr_row("o/dt", node, RULES, now=at(18, 30))
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "和主分支有冲突"))

    def test_fresh_commit_without_review_is_in_progress_then_unverified(self):
        at = lambda h, m: dt.datetime(2026, 11, 20, h, m, tzinfo=ol.TZ)   # noqa: E731
        r = ol.pr_row("o/dt", pr(checks=[run("test")]), RULES, now=at(17, 59))   # 14:00 提交
        self.assertEqual((r["state"], r["owner"], r["check_at"]), ("在做", "Claude", "2026-11-20T18:00:00+08:00"))
        self.assertEqual(ol.pr_row("o/dt", pr(checks=[run("test")]), RULES, now=at(18, 0))["state"], "待验证")

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
        self.assertIn("沿用上一份 3 条｜没取到 ", text.splitlines()[0])
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
        for part in ("未闭环账本｜11-20 16:00 扫描", "【卡住】1", "pr:dt#2｜红的", "卡在：CI 失败：test", "（刚提交，还在改或等送审）", "【在做】",
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


# ====================== 5a-2：桌面会话、驿窗送审、并行 ======================

def ms(s):
    return int(ol.parse_ts(s).timestamp() * 1000)


def step(kind, at, **kw):
    return {"at": ms(at), "kind": kind, **kw}


def handoff(id_="h1", status="open", phase="working", steps=(), pr_=None, cwd=None, session="cli-1", risk="L3", head=HEAD,
            halt=None, error=None, carried=0, rounds_from=0, title="送审｜改点东西"):
    """驿窗 handoffs.json 里的一条接力（字段名照 handoffs.rs 的 camelCase）。"""
    steps = [dict(x) for x in steps]   # 各条接力各拿一份，用例改其中一条不会动到共用的步骤
    last = steps[-1]["at"] if steps else ms("2026-11-20T14:00:00+08:00")
    h = {"id": id_, "title": title, "claudeSession": session, "cwd": cwd, "chatgptConversation": "c", "status": status,
         "createdAt": steps[0]["at"] if steps else last, "updatedAt": last, "rounds": 2 * len(steps), "phase": phase, "phaseAt": last,
         "steps": steps, "attempt": 0, "auto": True}
    for k, v in (("pr", pr_), ("risk", risk), ("head", head), ("halt", halt), ("error", error), ("carried", carried or None),
                 ("roundsFrom", rounds_from or None)):   # rounds 是驿窗记的「话来回了几次」，不是第几轮：故意给个对不上的数
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

    def test_estimates_in_the_sessions_own_words(self):
        cases = {"全量测试在跑，约 40 分钟后出结果。": 40, "再等 2 小时。": 120, "大约半小时出结果。": 30, "预计 1.5 个小时。": 90,
                 "已跑了约 30 分钟，还没完。": 120, "跑了大约 30 分钟。": 120, "后台会话在装。": 120}
        rows = [session_row(chr(ord("a") + i), "后台进行中", f"会话{i}", status) for i, status in enumerate(cases)]
        for x in rows:
            self.meta_of(x)
        self.inbox(FakeInbox(rows))
        got, _ = self.rows()
        for x, minutes in zip(rows, cases.values()):
            due = ol.parse_ts("2026-11-20T14:00:00+08:00") + dt.timedelta(minutes=minutes)
            self.assertEqual(got[f"session:{x['id']}"]["check_at"], ol.iso(due), x["status"])

    def test_odd_sessions_are_counted_or_skipped_not_fatal(self):
        a, b, c = session_row("a"), session_row("b"), session_row("c", "暂停中")
        self.meta_of(a)
        self.meta_of(b)
        (self.meta / f"{b['id']}.json").write_text(json.dumps({"sessionId": b["id"], "cwd": self.p["path"], "prs": ["不是字典"], "originCwd": 5}))
        self.meta_of(c)
        self.inbox(FakeInbox([a, b, c]))
        rows, led = self.rows()
        self.assertEqual(sorted(rows), [f"session:{a['id']}"])
        cov = led["coverage"]["sessions"]
        self.assertEqual((cov["bad"], cov["other_words"]), (1, 1))

    def test_a_crash_inside_the_inbox_script_only_loses_this_source(self):
        fake = FakeInbox([])
        fake.scan = mock.Mock(side_effect=KeyError("lastAssistantCliSessionId"))
        self.inbox(fake)
        rows, led = self.rows(Hub(prs={"o/dt": [pr(1, checks=[run("test")])]}))
        self.assertEqual(list(rows), ["pr:dt#1"])
        self.assertEqual(led["coverage"]["sessions"]["failed"], "出错（KeyError：'lastAssistantCliSessionId'）")
        self.assertIn("上一份账本里也没有，这一类这次是空的", "\n".join(ol.gaps(led)))

    def test_missing_script_is_reported_and_previous_rows_are_kept(self):
        a = session_row("a")
        self.meta_of(a)
        self.inbox(FakeInbox([a]))
        _, first = self.rows()
        with mock.patch.object(ol, "inbox_module", side_effect=ol.Fail("找不到拍板清单脚本")):
            rows, led = self.rows(prev=first, now=NOW + dt.timedelta(minutes=5))
        self.assertTrue(rows[f"session:{a['id']}"]["carried"])
        self.assertEqual(led["coverage"]["sessions"]["failed"], "找不到拍板清单脚本")
        self.assertIn("会话：找不到拍板清单脚本（沿用上一份账本里的 1 条）", "\n".join(ol.gaps(led)))


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
        r, _, _ = self.relay_row(self.h(steps=[WORK, ASK], carried=1))
        self.assertEqual((r["state"], r["owner"], r["next"], r["project"]), ("等外部", "ChatGPT", "等 L3 第 2 轮回信", "dt"))
        self.assertEqual(r["evidence"], "11-20 14:10 已送 ChatGPT 审查，对方在看")
        self.assertEqual(r["check_at"], "2026-11-20T16:10:00+08:00")

    def test_round_number_follows_postpane_counting(self):
        """第几轮照驿窗的算法：前面接力带过来的轮数加上这条里算数的回信；常浩发话后重新起算；只补结论的回信不算。"""
        again = [step("work", "2026-11-20T15:00:00+08:00"), step("ask", "2026-11-20T15:00:00+08:00")]
        fmt = [step("format", "2026-11-20T14:41:00+08:00"), step("reply", "2026-11-20T14:42:00+08:00", verdict="revise")]
        cases = [(dict(steps=[WORK, ASK]), "等 L3 第 1 轮回信"),
                 (dict(steps=[WORK, ASK], carried=3), "等 L3 第 4 轮回信"),
                 (dict(steps=[WORK, ASK, REVISE, BACK] + again), "等 L3 第 2 轮回信"),
                 (dict(steps=[WORK, ASK, REVISE] + fmt + [BACK] + again), "等 L3 第 2 轮回信"),
                 (dict(steps=[WORK, ASK, REVISE, BACK] + again, carried=3, rounds_from=4), "等 L3 第 1 轮回信"),
                 (dict(steps=[WORK, ASK], risk=None), "等 第 1 轮回信")]
        for kw, want in cases:
            self.assertEqual(self.relay_row(self.h(**kw))[0]["next"], want, kw)
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        _, rows, _ = self.relay_row(self.h(steps=[WORK, ASK, REVISE, BACK], carried=3, pr_="O/dt#1"), hub=hub)
        self.assertIn("驿窗 L3 第 4 轮要求修改", rows["pr:dt#1"]["evidence"])

    def test_asking_chatgpt_to_mend_its_last_lines_is_still_waiting(self):
        noverdict = step("reply", "2026-11-20T14:40:00+08:00")
        r, _, _ = self.relay_row(self.h(steps=[WORK, ASK, noverdict, step("format", "2026-11-20T14:41:00+08:00")], phase="working"))
        self.assertEqual((r["state"], r["owner"], r["next"]), ("等外部", "ChatGPT", "等 L3 第 1 轮补上结论"))
        self.assertEqual(r["check_at"], "2026-11-20T16:41:00+08:00")

    def test_one_odd_handoff_is_skipped_not_fatal(self):
        odd = [self.h("h2", steps=[WORK, ASK]), self.h("h3", steps=[WORK, ASK], carried="三"), self.h("h4", steps=[WORK, ASK])]
        odd[0]["updatedAt"] = "2026-11-20"
        odd[2]["steps"][1]["at"] = "刚才"
        r, rows, led = self.relay_row(self.h(steps=[WORK, ASK]), *odd)
        self.assertEqual((r["state"], sorted(rows)), ("等外部", ["relay:h1"]))   # 好的那条照收，三条坏的各自跳过
        self.assertEqual(led["coverage"]["relays"]["bad"], 3)
        self.assertIn("驿窗送审：有 3 条的数据不是预想的样子，跳过了", "\n".join(ol.gaps(led)))

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

    def test_returned_revise_is_rework_for_four_hours_then_stuck_and_merges_into_it(self):
        """11-22 常浩「修账本判法」：回信要求修改后 4 小时内算在做（在返修），过了还没新提交才卡住。"""
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        relay, rows, _ = self.relay_row(self.h(steps=[WORK, ASK, REVISE, BACK], pr_="O/dt#1"), hub=hub)
        self.assertIsNone(relay)                                   # 并进 PR 那一行了
        r = rows["pr:dt#1"]
        self.assertEqual((r["state"], r["blocked"], r["owner"]), ("在做", "", "Claude"))     # 16:00，回信 14:40
        self.assertEqual(r["evidence"], "11-20 14:40 驿窗 L3 第 1 轮要求修改，Claude返修中（还没有新提交）")
        self.assertEqual(r["check_at"], "2026-11-20T18:40:00+08:00")
        self.assertEqual([(x["id"], x["state"]) for x in r["links"]], [("relay:h1", "在做")])
        rows, _ = self.rows(hub, now=dt.datetime(2026, 11, 20, 18, 41, tzinfo=ol.TZ))
        r = rows["pr:dt#1"]
        self.assertEqual((r["state"], r["blocked"], r["stalled"]), ("卡住", "审查要求修改（驿窗 L3 第 1 轮）", True))

    def test_review_of_an_older_head_leaves_the_pr_unverified(self):
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        _, rows, _ = self.relay_row(self.h(steps=[WORK, ASK, REVISE, BACK], head=OLD, pr_="O/dt#1"), hub=hub)
        self.assertEqual(rows["pr:dt#1"]["state"], "在做")              # 14:00 的新提交，16:00 还在 4 小时内
        self.assertIn("上一次审的是旧版本（驿窗 L3 第 1 轮）", next(x for x in rows["pr:dt#1"]["parts"] if x["source"] == "pr")["evidence"])
        rows, _ = self.rows(hub, now=dt.datetime(2026, 11, 20, 18, 1, tzinfo=ol.TZ))
        self.assertEqual(rows["pr:dt#1"]["state"], "待验证")

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
        hub = Hub(prs={"o/dt": [pr(1, "恢复门", checks=[run("test")], pushed="2026-11-20T00:00:00Z"), pr(2, "派活", draft=True, checks=[run("test")])]})
        rows, led = self.rows(hub)
        self.assertEqual(sorted(rows), ["mainline:dt/#188/批2", "pr:dt#1", "pr:dt#2"])
        one = rows["pr:dt#1"]
        self.assertEqual((one["state"], one["owner"], one["what"]), ("等你", "常浩", "恢复门"))   # PR 08:00 之后是待验证，主线项 09:00 写的等你：取更需要人的
        self.assertEqual(one["next"], "常浩定：PR #1 等常浩看")
        self.assertEqual([(x["id"], x["state"]) for x in one["links"]], [("mainline:dt/#188/0D恢复门", "等你"), ("mainline:dt/#188/批2", "在做")])
        self.assertTrue(one["links"][1]["related"])                 # 提到两个 PR 的那一项没并进来，只是互相记一笔
        self.assertEqual([(x["id"], x.get("related")) for x in rows["mainline:dt/#188/批2"]["links"]], [("pr:dt#1", True), ("pr:dt#2", True)])
        self.assertEqual(led["counts"]["等你"], 1)

    def test_sayings_older_than_the_pr_activity_do_not_set_the_state(self):
        """11-22 #211：旧会话的〔等你决定〕比 PR 新的提交、送审还旧，不该把整行变成「等你」；比 PR 新的照样算。"""
        old = session_row("a", "等你决定", "0D 交接", "要你定在哪里接着做。", at="2026-11-20T05:00:00.000Z")    # 13:00
        new = session_row("b", "等你决定", "0D 收尾", "要你定合不合。", at="2026-11-20T07:00:00.000Z")       # 15:00
        self.meta_of(old, prs=[("o/dt", 1)], cli="cli-1")
        self.meta_of(new, prs=[("o/dt", 2)], cli="cli-2")
        self.inbox(FakeInbox([old, new]))
        rows, _ = self.rows(Hub(prs={"o/dt": [pr(1, checks=[run("test")]), pr(2, checks=[run("test")])]}))   # 两个 PR 都是 14:00 提交
        one, two = rows["pr:dt#1"], rows["pr:dt#2"]
        self.assertEqual((one["state"], one["owner"]), ("在做", "Claude"))
        self.assertEqual([(x["id"], x.get("old")) for x in one["links"]], [(f"session:{old['id']}", True)])
        self.assertEqual((two["state"], two["owner"]), ("等你", "常浩"))
        self.assertNotIn("old", two["links"][0])

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
        self.assertEqual(r["blocked"], "回信没送回会话")   # PR 自己在 4 小时返修期内（在做），卡住的是回信没送回会话
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

    def test_one_row_never_holds_two_prs(self):
        """会话记着 #2，它发起的接力写的是 #1：对不上，分开列，互相记一笔。"""
        a = session_row("a", "等你操作", "两头的会话")
        self.meta_of(a, prs=[("o/dt", 2)], cli="cli-1")
        self.inbox(FakeInbox([a]))
        self.relays(handoff(steps=[WORK, ASK], pr_="O/dt#1", session="cli-1"))
        rows, _ = self.rows(Hub(prs={"o/dt": [pr(1, checks=[run("test")]), pr(2, checks=[run("test")])]}))
        self.assertEqual(sorted(rows), ["pr:dt#1", "pr:dt#2"])
        self.assertEqual([(x["id"], x.get("related", False)) for x in rows["pr:dt#1"]["links"]], [("relay:h1", False), (f"session:{a['id']}", True)])
        self.assertEqual([(x["id"], x.get("related", False)) for x in rows["pr:dt#2"]["links"]], [(f"session:{a['id']}", False), ("relay:h1", True)])
        two = session_row("b", "等你操作", "记着两个 PR 的会话")
        self.meta_of(two, prs=[("o/dt", 1), ("o/dt", 2)], cli="cli-2")
        self.inbox(FakeInbox([two]))
        self.relays(handoff(steps=[WORK, ASK], pr_="O/dt#1", session="cli-2"))
        rows, _ = self.rows(Hub(prs={"o/dt": [pr(1, checks=[run("test")]), pr(2, checks=[run("test")])]}))
        self.assertEqual(sorted(rows), ["pr:dt#1", "pr:dt#2"])                 # 接力和会话并到 #1 那一行，#2 只是相关
        self.assertNotIn("pr:dt#1", [x["id"] for x in rows["pr:dt#1"]["links"]])   # 不会记一笔指向自己

    def test_a_source_that_fails_keeps_its_part_of_merged_rows(self):
        a = session_row("a", "等你操作", "0D", "PR #1 等你批准。", "回复「合并」。")
        self.meta_of(a, prs=[("o/dt", 1)], cli="cli-1")
        self.inbox(FakeInbox([a]))
        self.relays(handoff(steps=[WORK, ASK, REVISE, BACK], pr_="O/dt#1", session="cli-1"))
        hub = Hub(prs={"o/dt": [pr(1, checks=[run("test")])]})
        rows, first = self.rows(hub)
        self.assertEqual((list(rows), rows["pr:dt#1"]["state"]), (["pr:dt#1"], "在做"))   # 会话 14:00 的〔等你操作〕比 14:40 的回信旧，不参与定状态
        later = NOW + dt.timedelta(minutes=5)
        with mock.patch.object(ol, "inbox_module", side_effect=ol.Fail("找不到拍板清单脚本")):      # 会话这一类取不到
            rows, led = self.rows(hub, prev=first, now=later)
        r = rows["pr:dt#1"]
        self.assertEqual(sorted(x["id"] for x in r["links"]), ["relay:h1", f"session:{a['id']}"])   # 会话那部分还在这一行里
        self.assertEqual((r["carried"], r["carried_from"], led["coverage"]["sessions"]["carried"]), (True, "2026-11-20T16:00:00+08:00", 1))
        self.handoffs.write_text("{坏的", encoding="utf-8")                                          # 驿窗记录读坏
        rows, led = self.rows(hub, prev=first, now=NOW + dt.timedelta(hours=3))                       # 过了 4 小时返修期
        r = rows["pr:dt#1"]
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "审查要求修改（驿窗 L3 第 1 轮）"))        # 照上一份记下的送审记录判，不说成没人审
        self.assertEqual(sorted(x["id"] for x in r["links"]), ["relay:h1", f"session:{a['id']}"])
        self.inbox(FakeInbox([]))
        _, _, = self.rows(hub)                                                                      # 没有上一份可用时
        rows, _ = self.rows(Hub(prs={"o/dt": [pr(1, checks=[run("test")])]}))
        self.assertIn("驿窗的送审记录这次没读到", rows["pr:dt#1"]["evidence"])
        self.assertNotIn("没有审查或送审记录", rows["pr:dt#1"]["evidence"])

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


# ---------- 新通道派单（#195 缺口 3） ----------
TID = "t-20261120-0a1b2c"
TID2 = "t-20261120-0d0e0f"


def grants_schema():
    import autoloop_grants as G
    return G.SCHEMA


class BranchHub(Hub):
    """假 GitHub，另认派单分支查询：branches = {仓库: {分支: [PR]}}。"""

    def __init__(self, branches=None, **kw):
        super().__init__(**kw)
        self.branches = branches or {}

    def _run(self, *args, partial=False):
        f = dict(a.split("=", 1) for a in args if "=" in a)
        if "headRefName:" not in f["query"]:
            return super()._run(*args, partial=partial)
        repo = f"{f['o']}/{f['n']}"
        self.calls.append((repo, "branches"))
        if repo in self.down:
            raise pdaily_data.NetFail("error connecting to api.github.com")
        got = {}
        for i, b in re.findall(r'b(\d+): pullRequests\(headRefName:"([^"]+)"', f["query"]):
            got[f"b{i}"] = {"nodes": self.branches.get(repo, {}).get(b, [])}
        return {"data": {"repository": got}}


class Dispatch(Base):
    """派单网关签出的任务凭证一张一行：凭证库、中台库、交接目录都在临时目录，只读打开。
    不挂 prod_guard：这里只导入 autoloop_grants（拿建表语句、路径和单号写法）、autoloop_publish（拿路径），不调它们的任何
    会写库、发事件、连中台的函数；凭证库、中台库、交接目录都由环境变量指到临时目录，统一隔离 _isolation 兜底。"""

    def setUp(self):
        super().setUp()
        self.grants = self.root / "dt/autoloop/grants.db"
        self.hub_home = self.root / "hub"
        self.outbox = self.root / "outbox"
        p = mock.patch.dict(os.environ, {"DT_GRANTS_DB": str(self.grants), "AGENT_AUTOLOOP_HOME": str(self.hub_home),
                                         "DT_PUBLISH_OUTBOX": str(self.outbox)})
        p.start()
        self.addCleanup(p.stop)
        f = self.root / "projects.json"
        f.write_text(json.dumps({"projects": [], "inventory": [
            {"path": "github:o/dt-sandbox", "category": "沙盒", "sandbox": True}]}, ensure_ascii=False), encoding="utf-8")
        q = mock.patch.object(progress_data, "ROSTER", f)
        q.start()
        self.addCleanup(q.stop)

    def grant(self, tid=TID, status="active", executor="claude", repo="dt-sandbox", until="2026-11-21T04:00:00+08:00",
              events=(("start", 1, "2026-11-20T15:00:00+08:00"),), updated="2026-11-20T15:30:00+08:00"):
        self.grants.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.grants)
        con.executescript(grants_schema())
        con.execute("INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, repo, f"o/{repo}", "目标：给 README 加一节\n交付物：只改 README.md", "h" * 64, executor,
                     f"dispatch-{repo}-{executor}", '["edit"]', until, status, 1, "2026-11-20T15:00:00+08:00", None,
                     "om_x", "2026-11-20T15:00:00+08:00", updated))
        for i, (kind, sent, at) in enumerate(events):
            con.execute("INSERT INTO hub_events (event_key, task_id, rule, executor, generation, kind, at, sent, claimed_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)", (f"{tid}:g1:{kind}:{i}", tid, f"dispatch-{repo}-{executor}", executor,
                                                       1, kind, at, sent, at))
        con.commit()
        con.close()

    def runs(self, *rows):
        self.hub_home.mkdir(exist_ok=True)
        con = sqlite3.connect(self.hub_home / "hub.db")
        con.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, rule TEXT, agent TEXT, grp TEXT, lane TEXT, "
                    "state TEXT, attempts INTEGER DEFAULT 0, created_at TEXT, claimed_at TEXT, seen_at TEXT, finished_at TEXT, "
                    "summary TEXT)")
        for r in rows:
            r = {"rule": "dispatch-dt-sandbox-claude", "lane": TID, "attempts": 0, "created_at": "2026-11-20T15:01:00+08:00",
                 "claimed_at": None, "seen_at": None, "finished_at": None, "summary": None, **r}
            con.execute("INSERT INTO runs (id, rule, lane, state, attempts, created_at, claimed_at, seen_at, finished_at, summary) "
                        "VALUES (:id,:rule,:lane,:state,:attempts,:created_at,:claimed_at,:seen_at,:finished_at,:summary)", r)
        con.commit()
        con.close()

    def scan(self, hub=None, prev=None, now=NOW):
        return ol.build(now, ol.roster(), hub or BranchHub(), RULES, prev=prev, fetch=False)

    def row(self, ledger, id_=f"dispatch:{TID}"):
        return next((r for r in ledger["rows"] if r["id"] == id_ or any(x["id"] == id_ for x in r.get("links") or [])), None)

    def test_not_installed_is_not_a_gap_and_creates_nothing(self):
        led = self.scan()
        self.assertEqual(led["coverage"]["dispatch"]["installed"], False)
        self.assertFalse([g for g in ol.gaps(led) if "派单" in g])
        self.assertFalse(self.grants.exists())               # 只读打开：库不在不建库
        self.assertFalse((self.hub_home / "hub.db").exists())

    def test_worker_running_is_in_progress_and_other_repos_are_skipped(self):
        self.grant()
        self.grant(TID2, repo="not-in-roster")
        self.runs({"id": 7, "state": "claimed", "claimed_at": "2026-11-20T15:02:00+08:00"})
        before = self.grants.stat().st_mtime_ns
        led = self.scan()
        r = self.row(led)
        self.assertEqual((r["state"], r["owner"], r["source"], r["project"]), ("在做", "Claude", "dispatch", "dt-sandbox"))
        self.assertIn("中台轮次 7 在跑", r["evidence"])
        self.assertEqual(r["check_at"], "2026-11-20T17:32:00+08:00")          # 开跑 + 150 分钟
        self.assertTrue(r["what"].startswith(f"{TID}（Claude）：目标：给 README 加一节"))
        self.assertEqual(led["coverage"]["dispatch"]["skipped"], 1)
        self.assertIsNone(self.row(led, f"dispatch:{TID2}"))
        self.assertEqual(self.grants.stat().st_mtime_ns, before)            # 没写凭证库

    def test_hub_took_the_event_but_made_no_run(self):
        self.grant()
        self.runs()
        r = self.row(self.scan())
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "中台没有接这张单"))
        self.assertIn("通道暂停或没有这条派单规则", r["evidence"])

    def test_event_not_confirmed_at_the_hub(self):
        self.grant(events=(("start", 2, "2026-11-20T15:55:00+08:00"),))   # 5 分钟前发出、还没确认
        self.runs()
        r = self.row(self.scan())
        self.assertEqual((r["state"], r["owner"]), ("卡住", "猛鬼兽"))
        self.assertIn("send-pending", r["next"])
        r = self.row(self.scan(now=dt.datetime(2026, 11, 20, 15, 56, tzinfo=ol.TZ)))   # 刚发出 1 分钟：不催
        self.assertEqual((r["state"], r["evidence"].endswith("正在送往中台")), ("在做", True))

    def test_open_pr_on_the_branch_becomes_one_row_with_the_pr(self):
        self.grant()
        self.runs({"id": 7, "state": "done", "finished_at": "2026-11-20T15:20:00+08:00", "summary": "改好了"})
        node = {**pr(5, "给 README 加一节", branch=f"claude/{TID}", files=("README.md",), checks=[run("ci")]), "state": "OPEN"}
        led = self.scan(BranchHub({"o/dt-sandbox": {f"claude/{TID}": [node]}}))
        r = self.row(led)
        self.assertEqual((r["id"], r["state"], r["next"]), ("pr:dt-sandbox#5", "等你", ol.MERGE_NEXT))   # 只改文档：L0
        self.assertEqual([x["id"] for x in r["links"]], [f"dispatch:{TID}"])
        self.assertEqual(ol.ask_kind(r), "合并")

    def test_stopped_turn_is_stuck(self):
        self.grant()
        self.runs({"id": 7, "state": "done", "finished_at": "2026-11-20T15:20:00+08:00",
                   "summary": "停下了：凭证已撤销，没有交接\n\n回执"})
        r = self.row(self.scan())
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "停下了：凭证已撤销，没有交接"))

    def test_finished_turn_with_or_without_a_handoff(self):
        self.grant()
        self.runs({"id": 7, "state": "done", "finished_at": "2026-11-20T15:20:00+08:00", "summary": "改好了"})
        self.assertEqual(self.row(self.scan())["state"], "待验证")          # 没交接包：可能只提了问题
        (self.outbox / TID).mkdir(parents=True)
        (self.outbox / TID / "handoff.json").write_text(json.dumps({"head": HEAD}))
        r = self.row(self.scan())
        self.assertEqual((r["state"], r["owner"]), ("等外部", "受信发布"))
        self.assertIn("由常浩在场手动做", r["next"])

    def test_frozen_merged_closed_revoked(self):
        self.grant(status="frozen")
        self.runs()
        r = self.row(self.scan())
        self.assertEqual((r["state"], r["owner"], ol.ask_kind(r)), ("等你", "常浩", "拍板"))
        con = sqlite3.connect(self.grants)
        con.execute("UPDATE grants SET status = 'active'")
        con.commit()
        merged = {**pr(5, branch=f"claude/{TID}"), "state": "MERGED", "mergedAt": "2026-11-20T07:00:00Z"}
        r = self.row(self.scan(BranchHub({"o/dt-sandbox": {f"claude/{TID}": [merged]}})))
        self.assertEqual((r["state"], ol.ask_kind(r)), ("等你", "拍板"))
        self.assertIn("收掉", r["next"])
        closed = {**pr(5, branch=f"claude/{TID}"), "state": "CLOSED", "closedAt": "2026-11-20T07:00:00Z"}
        self.assertIn("续接返修还是停单", self.row(self.scan(BranchHub({"o/dt-sandbox": {f"claude/{TID}": [closed]}})))["next"])
        con.execute("UPDATE grants SET status = 'revoked'")
        con.commit()
        con.close()
        self.assertIsNone(self.row(self.scan()))                            # 停了、没有轮次和 PR：完了，不收
        self.runs({"id": 9, "state": "claimed"})
        r = self.row(self.scan())
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "停单后轮次还活着"))
        self.assertIn("agent-autoloop stop 9", r["next"])

    def test_expired_grant_without_pr_is_done(self):
        self.grant(until="2026-11-20T15:30:00+08:00")
        self.runs({"id": 7, "state": "done", "finished_at": "2026-11-20T15:20:00+08:00", "summary": "改好了"})
        self.assertIsNone(self.row(self.scan()))

    def test_hub_or_github_missing_still_lists_the_task_and_says_so(self):
        self.grant()
        led = self.scan(BranchHub(down={"o/dt-sandbox"}))
        r = self.row(led)
        self.assertIn("中台库这次没读到", r["evidence"])
        self.assertIn("PR 这次没查到", r["evidence"])
        gaps = ol.gaps(led)
        self.assertTrue(any("中台库不在" in g for g in gaps), gaps)
        self.assertTrue(any(g.startswith("新通道派单·dt-sandbox：PR 没查到") for g in gaps), gaps)

    def test_broken_grants_db_carries_last_ledger(self):
        self.grant()
        self.runs({"id": 7, "state": "claimed"})
        prev = self.scan()
        self.grants.write_bytes(b"not a database")
        led = self.scan(prev=prev)
        r = self.row(led)
        self.assertTrue(r["carried"])
        self.assertTrue(any(g.startswith("新通道派单：凭证库读不了") for g in ol.gaps(led)), ol.gaps(led))

    def test_paths_match_the_gateway_and_publisher(self):
        import autoloop_grants as G
        import autoloop_publish as P
        self.assertEqual((ol.grants_db(), ol.hub_db(), ol.dispatch_outbox()), (G.db_path(), P.hub_db(), P.outbox()))
        with mock.patch.dict(os.environ, {"DT_GRANTS_DB": "", "AGENT_AUTOLOOP_HOME": "", "DT_PUBLISH_OUTBOX": ""}):
            self.assertEqual((ol.grants_db(), ol.hub_db(), ol.dispatch_outbox()), (G.db_path(), P.hub_db(), P.outbox()))
        self.assertEqual(ol.TASK_ID.pattern, G.TASK_ID.pattern)

    # ---------- #327 审查后补 ----------

    def branch(self, *nodes):
        return BranchHub({"o/dt-sandbox": {f"claude/{TID}": list(nodes)}})

    def open_pr(self, files=("README.md",)):
        return {**pr(5, "给 README 加一节", branch=f"claude/{TID}", files=files, checks=[run("ci")]), "state": "OPEN"}

    def test_waiting_row_stalls_after_the_next_slip(self):
        self.grant(status="frozen", until="2026-11-22T04:00:00+08:00")      # 15:30 冻结
        r = self.row(self.scan(now=dt.datetime(2026, 11, 21, 10, 0, tzinfo=ol.TZ)))
        self.assertEqual(r["check_at"], "2026-11-20T21:00:00+08:00")        # 按证据时间算，不随扫描往后推
        self.assertTrue(r["stalled"])

    def test_frozen_with_open_pr_is_a_decision_not_hands_on(self):
        self.grant(status="frozen")
        led = self.scan(self.branch(self.open_pr(files=("hermes/scripts/a.py",))))
        r = self.row(led)
        self.assertEqual((r["id"], r["state"], r["source"]), ("pr:dt-sandbox#5", "等你", "pr"))
        self.assertEqual(ol.ask_kind(r), "拍板")
        self.assertNotIn(r, ol.waiting(led)[0])                             # 不上午晚单

    def test_pr_from_a_fork_is_not_the_workers(self):
        self.grant()
        self.runs({"id": 7, "state": "done", "finished_at": "2026-11-20T15:20:00+08:00", "summary": "改好了"})
        led = self.scan(self.branch({**self.open_pr(), "isCrossRepository": True}))
        r = self.row(led)
        self.assertEqual((r["id"], r["state"]), (f"dispatch:{TID}", "待验证"))
        self.assertIsNone(self.row(led, "pr:dt-sandbox#5"))

    def test_github_down_carries_the_task_with_its_pr(self):
        self.grant(status="revoked")
        prev = self.scan(self.branch(self.open_pr()))
        self.assertEqual(self.row(prev)["id"], "pr:dt-sandbox#5")
        led = self.scan(BranchHub(down={"o/dt-sandbox"}), prev=prev)
        r = self.row(led)
        self.assertEqual((r["id"], r["state"], r.get("carried")), ("pr:dt-sandbox#5", "等你", True))
        self.assertEqual(led["coverage"]["dispatch"]["carried"], 1)
        self.assertTrue(any("1 张单这次缺数据，沿用上一份账本里的" in g for g in ol.gaps(led)), ol.gaps(led))

    def test_merged_pr_with_github_down_is_not_rejudged(self):
        self.grant()
        self.runs()
        merged = {**pr(5, branch=f"claude/{TID}"), "state": "MERGED", "mergedAt": "2026-11-20T07:00:00Z"}
        prev = self.scan(self.branch(merged))
        led = self.scan(BranchHub(down={"o/dt-sandbox"}), prev=prev)
        r = self.row(led)
        self.assertEqual((r["state"], r.get("carried")), ("等你", True))
        self.assertIn("收掉", r["next"])

    def test_broken_hub_db_without_last_ledger_still_lists(self):
        self.grant()
        self.hub_home.mkdir()
        (self.hub_home / "hub.db").write_bytes(b"not a database")
        led = self.scan()
        r = self.row(led)
        self.assertIn("中台库这次没读到", r["evidence"])
        self.assertIn("派单已送到中台，轮次不明", r["evidence"])
        self.assertTrue(any(g.startswith("新通道派单：中台库读不了") for g in ol.gaps(led)), ol.gaps(led))

    def test_broken_grants_db_carries_the_sandbox_pr_row_too(self):
        self.grant(status="revoked")
        prev = self.scan(self.branch(self.open_pr()))
        self.grants.write_bytes(b"not a database")
        r = self.row(self.scan(self.branch(self.open_pr()), prev=prev))
        self.assertEqual((r["id"], r["state"], r.get("carried")), ("pr:dt-sandbox#5", "等你", True))
        self.assertEqual(ol.ask_kind(r), "合并")

    def test_grants_db_gone_after_it_was_read_is_not_uninstalled(self):
        self.grant()
        self.runs({"id": 7, "state": "claimed"})
        prev = self.scan()
        self.grants.unlink()
        led = self.scan(prev=prev)
        self.assertIn("凭证库不在了", led["coverage"]["dispatch"]["failed"])
        self.assertTrue(self.row(led)["carried"])
        self.grants.parent.mkdir(parents=True, exist_ok=True)
        self.assertIn("凭证库不在了", self.scan(prev=led)["coverage"]["dispatch"]["failed"])   # 再下一次也记着装过

    def test_event_states(self):
        self.grant(events=(("start", 0, "2026-11-20T15:50:00+08:00"),))   # 待发、10 分钟了
        self.runs()
        self.assertEqual(self.row(self.scan())["blocked"], "派单事件没送到中台")
        self.grants.unlink()
        self.grant(events=(("start", -2, "2026-11-20T15:50:00+08:00"),))  # 作废前已领取：send-pending 不会再发，不催补发
        self.assertNotIn("send-pending", self.row(self.scan())["next"])

    def test_unknown_and_queued_turns(self):
        self.grant()
        self.runs({"id": 7, "state": "unknown", "claimed_at": "2026-11-20T15:02:00+08:00", "seen_at": "2026-11-20T15:10:00+08:00"})
        r = self.row(self.scan())
        self.assertEqual((r["state"], r["blocked"]), ("卡住", "接单程序没消息"))
        con = sqlite3.connect(self.hub_home / "hub.db")
        con.execute("UPDATE runs SET state = 'queued', attempts = 1")
        con.commit()
        con.close()
        r = self.row(self.scan())
        self.assertEqual((r["state"], r["owner"]), ("在做", "Claude"))
        self.assertIn("没跑成 1 次", r["evidence"])

    def test_expired_frozen_grant_is_done(self):
        self.grant(status="frozen", until="2026-11-20T15:40:00+08:00")
        self.runs()
        self.assertIsNone(self.row(self.scan()))                            # 过期后恢复一律被拒：不再问恢复还是撤销

    def test_handoff_must_belong_to_the_last_turn(self):
        self.grant()
        self.runs({"id": 7, "state": "done", "claimed_at": "2026-11-20T15:05:00+08:00", "finished_at": "2026-11-20T15:20:00+08:00",
                   "summary": "改好了"})
        (self.outbox / TID).mkdir(parents=True)
        f = self.outbox / TID / "handoff.json"
        f.write_text(json.dumps({"head": HEAD}))
        old = dt.datetime(2026, 11, 20, 14, 0, tzinfo=ol.TZ).timestamp()
        os.utime(f, (old, old))                                             # 上一轮留下的
        self.assertEqual(self.row(self.scan())["state"], "待验证")
        new = dt.datetime(2026, 11, 20, 15, 18, tzinfo=ol.TZ).timestamp()
        os.utime(f, (new, new))
        self.assertEqual(self.row(self.scan())["state"], "等外部")

    def test_closed_pr_then_reworked_follows_the_new_turn(self):
        self.grant()
        self.runs({"id": 7, "state": "done", "claimed_at": "2026-11-20T15:05:00+08:00", "finished_at": "2026-11-20T15:20:00+08:00",
                   "summary": "改好了"})
        (self.outbox / TID).mkdir(parents=True)
        (self.outbox / TID / "handoff.json").write_text(json.dumps({"head": HEAD}))
        closed = {**pr(5, branch=f"claude/{TID}"), "state": "CLOSED", "closedAt": "2026-11-20T07:00:00Z"}   # 15:00 关掉，之后又做了一轮
        self.assertEqual(self.row(self.scan(self.branch(closed)))["state"], "等外部")

    def test_old_finished_grants_are_not_queried_unless_still_listed(self):
        self.grant(status="revoked", updated="2026-11-10T10:00:00+08:00")
        hub = self.branch(self.open_pr())
        led = self.scan(hub)
        self.assertIsNone(self.row(led))
        self.assertEqual(led["coverage"]["dispatch"]["old"], 1)
        self.assertFalse([c for c in hub.calls if c[1] == "branches"])
        r = self.row(self.scan(self.branch(self.open_pr()), prev={"rows": [{"id": f"dispatch:{TID}", "source": "dispatch"}]}))
        self.assertEqual((r["id"], r["state"]), ("pr:dt-sandbox#5", "等你"))  # 上一份还挂着：照查，PR 还开着就还在

    def test_databases_are_opened_read_only(self):
        self.grant()
        self.runs({"id": 7, "state": "claimed"})
        real = sqlite3.connect
        with mock.patch.object(ol.sqlite3, "connect", side_effect=real) as c:
            self.scan()
        mine = [x for x in c.call_args_list if "grants.db" in str(x.args[0]) or "hub.db" in str(x.args[0])]
        self.assertEqual(len(mine), 2)                                      # 凭证库、中台库各一次
        for call in mine:
            self.assertTrue(call.args[0].startswith("file:") and call.args[0].endswith("?mode=ro"), call)
            self.assertIs(call.kwargs.get("uri"), True)


if __name__ == "__main__":
    unittest.main()
