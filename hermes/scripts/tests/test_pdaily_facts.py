"""pdaily_facts.py 自测（#110 批 1c）：拍板候选、风险四类、跨项目引用、观察池、覆盖与可信度，外加 V2 7.1「项目之间有依赖」。

临时目录假造来源，假 gh（fake_gh.py）不联网；1a、1b 的取数用 mock 换成造好的记录，只测 1c 自己的汇总逻辑。
统计日 D = 2026-11-08（04:00 到 11-09 04:00，北京时间）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import csv
import datetime as dt
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
import actions  # noqa: E402
import fake_gh  # noqa: E402
import pdaily_data as pd  # noqa: E402
import pdaily_facts as pf  # noqa: E402
import progress_data  # noqa: E402

D = dt.date(2026, 11, 8)
W = pd.window(D)
IN_DAY = "2026-11-08T10:00:00+08:00"
CSV_FIELDS = ("evaluated_at,source,repo,pr,title,head_sha,base_sha,rules_version,tier,suggestion,reasons,"
              "outcome,outcome_at,outcome_head,agree,owner,owner_at").split(",")
ROSTER = [{"id": "skills", "github": "o/my-skills"}, {"id": "niuma", "github": "o/niuma-home"},
          {"id": "hub", "github": "o/github-center"}]


def rec(pid, **kw):
    """一条 1a 项目记录：结构照 pdaily_data.blank，按需填。"""
    r = pd.blank({"id": pid})
    r["objective"] = kw.pop("objective", {"kind": "目标", "text": "做完甲", "source": "CURRENT.md"})
    for part in ("yesterday_delta", "current_state", "freshness"):
        r[part].update(kw.pop(part, {}))
    assert not kw, kw
    return r


def merged(number, title, body=""):
    """一条合并 PR：走 1a 真实的 deliverables 输出（带 ref_lines，不带整段正文）。"""
    return pd.deliverables([{"number": number, "title": title, "body": body, "mergedAt": IN_DAY}], [])[0][0]


def act(**kw):
    """一条 1b 行动（collect 之后的样子：带 origin、auth_gap）。"""
    base = {"action_id": "a1", "ts": IN_DAY, "project_id": "skills", "kind": "other", "request": "做事",
            "actor": "Hermes", "decision": "执行", "result": "调用成功"}
    a = actions.act(**{**base, **kw})
    a["origin"], a["auth_gap"] = kw.get("origin", "actions.jsonl"), kw.get("auth_gap")
    return a


def sh(cwd, *args, at="2026-11-08 10:00:00 +0800"):
    env = {**os.environ, "GIT_AUTHOR_DATE": at, "GIT_COMMITTER_DATE": at,
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True, env=env).stdout


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-pfacts-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "gh"
        fake_gh.install(self.root / "bin", self.state)
        self.shadow = self.root / "shadow"
        self.shadow.mkdir()
        self.proposals = self.root / "proposals"
        self.proposals.mkdir()
        env = {"PATH": f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_STATE": str(self.state),
               "DT_PROPOSALS": str(self.proposals)}
        patches = [mock.patch.dict(os.environ, env),
                   mock.patch.object(actions, "SHADOW", self.shadow),
                   mock.patch.object(pf, "DAILY", self.root / "daily")]
        for x in patches:
            x.start()
            self.addCleanup(x.stop)
        self.gh_graphql({})
        self.notes = []

    def gh_graphql(self, states):
        """假 gh 的 graphql 回话：{号: OPEN / CLOSED / MERGED}。"""
        repo = {f"i{n}": {"state": s} for n, s in states.items()}
        (self.state / "api.json").write_text(json.dumps({"graphql": {"data": {"repository": repo}}}))

    def ledger(self, rows):
        with (self.shadow / "agreement.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in CSV_FIELDS})

    def sources(self, officer=(), dispatch=(), cron=()):
        """把 1b 的三个来源换成造好的 (记录, 应有档位) 列表；传异常就让它抛。"""
        def fake(items):
            def fn(w, notes):
                if isinstance(items, Exception):
                    raise items
                self.calls.append(w)
                return [(x, None) for x in items]
            return fn
        self.calls = []
        for name, items in (("merge_officer", officer), ("dispatch", dispatch), ("cron", cron)):
            p = mock.patch.object(actions, name, fake(items))
            p.start()
            self.addCleanup(p.stop)


# ---------- 拍板候选 ----------

class DecisionTests(Base):
    def kinds(self, out):
        return [c["kind"] for c in out]

    def test_merge_officer_asks_owner_from_wide_window(self):
        self.sources(officer=[actions.act(action_id="m1", ts="2026-11-01T10:00:00+08:00", project_id="skills",
                                          request="PR #5 做甲", verification="L3", follow_up="常浩决定合不合并"),
                              actions.act(action_id="m2", ts=IN_DAY, project_id="skills", request="PR #6",
                                          follow_up=None)])
        out = pf.decisions(W, [], [], self.notes)
        self.assertEqual(self.kinds(out), ["merge"])
        self.assertEqual(out[0]["since"], "2026-11-01T10:00:00+08:00")     # 一周前判的、还没点，照列
        self.assertEqual(self.calls[0][0], pf.EPOCH)                          # 宽窗口：从头看到统计日结束
        self.assertEqual(self.calls[0][1], W[1])

    def test_dispatch_confirm_always_but_receipt_decision_only_in_day(self):
        self.sources(dispatch=[
            actions.act(action_id="d1", ts="2026-11-01T10:00:00+08:00", project_id="niuma", request="写入单",
                        follow_up="常浩确认开跑或取消"),
            actions.act(action_id="d2", ts=IN_DAY, project_id="niuma", request="回执要决定", follow_up="常浩决定：选 A 还是 B"),
            actions.act(action_id="d3", ts="2026-11-07T10:00:00+08:00", project_id="niuma", request="老回执",
                        follow_up="常浩决定：老问题"),
            actions.act(action_id="d4", ts=IN_DAY, project_id="niuma", request="普通", follow_up=None)])
        out = pf.decisions(W, [], [], self.notes)
        self.assertEqual([c["source"] for c in out], ["派单台账 d1", "派单台账 d2"])
        self.assertEqual(out[0]["why_owner"], "写入单要常浩确认开跑")
        self.assertEqual(out[1]["why_owner"], "车间回执写了要常浩决定")

    def item(self, n, key, title, **kw):
        return {"key": key, "source": "retro", "id": key.split(":")[-1], "title": title, "n": n, "file": "/x/提案.md", **kw}

    def test_proposals_latest_issue_minus_answered(self):
        """结构照 proposal_digest.py：条目不回写 decided，常浩的回复在 answers[].applied；每期重列上期没定的。"""
        self.sources()
        (self.proposals / "digest.json").write_text(json.dumps({"issues": [
            {"no": 1, "date": "2026-11-02", "items": [self.item(1, "r:A1", "老一期"), self.item(2, "r:A2", "老一期没回")],
             "answers": []},
            {"no": 2, "date": "2026-11-07", "items": [
                self.item(1, "r:A2", "老一期没回", rec="采纳", why="两次"), self.item(2, "r:A3", "回了采纳"),
                self.item(3, "r:A4", "回了待定"), self.item(4, "r:A5", "统计日后才回"), self.item(5, "r:A2", "同 key 重复")],
             "answers": [{"at": "2026-11-07T08:38:02+08:00", "reply": "按推荐", "applied": {"2": "采纳", "3": "待定"}},
                         {"at": "2026-11-09T05:00:00+08:00", "reply": "4 否", "applied": {"4": "否决"}}]},
            {"no": 3, "date": "2026-11-09", "items": [self.item(1, "r:A9", "统计日后才发")], "answers": []}]}))
        out = pf.decisions(W, [], [], self.notes)
        self.assertEqual([(c["kind"], c["item"]) for c in out], [("proposal", "老一期没回"), ("proposal", "统计日后才回")])
        self.assertIn("Hermes 推荐：采纳（两次）", out[0]["evidence"])
        self.assertEqual(out[0]["source"], "proposals/digest.json 第 2 期第 1 条 A2")

    def test_no_issue_before_day_end(self):
        self.sources()
        (self.proposals / "digest.json").write_text(json.dumps({"issues": [
            {"no": 1, "date": "2026-11-09", "items": [self.item(1, "r:A1", "x")], "answers": []}]}))
        self.assertEqual(pf.decisions(W, [], [], self.notes), [])

    def test_broken_proposals_file_goes_to_notes(self):
        self.sources()
        (self.proposals / "digest.json").write_text("{坏")
        self.assertEqual(pf.decisions(W, [], [], self.notes), [])
        self.assertTrue(any("待决提案读不了" in n for n in self.notes))

    def test_auth_gap_and_blocked_night_and_current_next_step(self):
        self.sources()
        acts = [act(action_id="g1", kind="merge", auth_gap="授权不足：这类动作要 B 档，记录是「不知道」"),
                act(action_id="n1", kind="dispatch", origin="夜跑", follow_up="常浩处理受阻的子单"),
                act(action_id="x1")]
        recs = [rec("skills", current_state={"next_step": "等常浩说「合并」", "next_source": "CURRENT", "next_reliable": True}),
                rec("niuma", current_state={"next_step": "继续写测试"}),
                rec("hub", current_state={"next_step": "常浩验收", "next_reliable": False})]   # 过期的下一步不当依据
        out = pf.decisions(W, recs, acts, self.notes)
        self.assertEqual(self.kinds(out), ["auth", "blocked", "current"])
        self.assertIn("授权不足", out[0]["why_owner"])
        self.assertEqual(out[2]["item"], "等常浩说「合并」")

    def test_candidates_leave_type_recommendation_impact_empty(self):
        """常浩 2026-11-09 定：只给候选和出处，不定级、不排序、不推荐。"""
        self.sources(officer=[actions.act(action_id="m1", ts=IN_DAY, project_id="skills", request="PR",
                                          follow_up="常浩决定合不合并")])
        out = pf.decisions(W, [rec("skills", current_state={"next_step": "常浩定"})],
                           [act(auth_gap="授权不足")], self.notes)
        self.assertEqual(len(out), 3)
        for c in out:
            self.assertEqual((c["type"], c["recommendation"], c["impact"], c["reply"]), (None, None, None, None))
            self.assertEqual(c["status"], "待你决定")
            self.assertTrue(c["source"])

    def test_one_source_failing_keeps_the_others(self):
        self.sources(officer=RuntimeError("csv 坏了"),
                     dispatch=[actions.act(action_id="d1", ts=IN_DAY, project_id="niuma", request="写入单",
                                           follow_up="常浩确认开跑或取消")])
        out = pf.decisions(W, [], [], self.notes)
        self.assertEqual(self.kinds(out), ["dispatch"])
        self.assertTrue(any("合并官读不了" in n for n in self.notes))


# ---------- 风险四类 ----------

def cov(**actions_cov):
    return {"actions": {k: v for k, v in actions_cov.items()}, "projects": {"errors": {}}}


class RiskTests(Base):
    def cats(self, out):
        return [(x["category"], x["project_id"], x["level"]) for x in out]

    def test_missing_sources_and_project_errors_are_run_risks(self):
        c = cov(**{"派单台账": {"ok": False, "error": "读不了"}, "合并官": {"ok": True, "error": None}})
        c["projects"]["errors"] = {"niuma": ["gh 连不上"]}
        out = pf.risks(W, [], [], c)
        self.assertEqual(self.cats(out), [("运行风险", "hermes", None), ("运行风险", "niuma", None)])
        self.assertIn("派单台账", out[0]["item"])

    def test_action_failures_split_by_kind(self):
        acts = [act(action_id="f1", kind="dispatch", result="失败", project_id="niuma"),
                act(action_id="f2", kind="cron", result="失败", project_id="hermes"),
                act(action_id="s1", kind="supervise", follow_up="处理这张故障单", project_id="hermes"),
                act(action_id="u1", kind="dispatch", result="不明", origin="派单台账", project_id="skills",
                    verification="没有回执（单子状态 done，单子记的退出码 没记）"),
                act(action_id="u3:open", kind="dispatch", result="不明", origin="派单台账", verification="开了写入单，没开跑就放弃了"),
                act(action_id="g1", kind="dispatch", origin="派单台账", follow_up="核对主分支、远端的改动", verification="护栏"),
                act(action_id="u2", kind="dispatch", result="不明", origin="夜跑", project_id="skills",
                    verification="没有回执"),                       # 夜跑的「不明」不归派单台账口径
                act(action_id="ok", kind="dispatch", result="调用成功")]
        out = pf.risks(W, [], acts, cov())
        self.assertEqual([(x["category"], x["evidence"][-1]) for x in out],
                         [("协作阻塞", "actions.jsonl f1"), ("运行风险", "actions.jsonl f2"), ("运行风险", ""),
                          ("协作阻塞", "没有回执"), ("运行风险", "派单护栏报警")])

    def test_open_pr_over_two_days_blocks(self):
        r = rec("niuma", current_state={"open_prs": [
            {"number": 31, "title": "老 PR", "created": "2026-11-06T03:00:00+08:00"},
            {"number": 32, "title": "新 PR", "created": "2026-11-07T10:00:00+08:00"}]})
        out = [x for x in pf.risks(W, [r], [], cov()) if x["category"] == "协作阻塞"]
        self.assertEqual([x["item"].split("：")[0] for x in out], ["PR #31 开着超过 2 天"])

    def test_management_distortions(self):
        recs = [rec("stale", freshness={"stale": True, "top_section_date": "2026-11-01", "last_merge": "2026-11-08T10:00:00+08:00"}),
                rec("nogoal", objective={"kind": "未设", "text": None, "source": None}),
                rec("idle", freshness={"last_merge": "2026-11-02T03:59:00+08:00"}),
                rec("paused", objective={"kind": "暂停"}, freshness={"last_merge": "2026-10-01T10:00:00+08:00"},
                    current_state={"checkout_branch": "feat/x"}),
                rec("fresh", freshness={"last_merge": "2026-11-03T10:00:00+08:00"}),
                rec("branch", current_state={"checkout_branch": "feat/x"}),
                rec("dirty", current_state={"dirty_files": 10}),
                rec("clean", current_state={"dirty_files": 9, "checkout_branch": "main"})]
        out = pf.risks(W, recs, [], cov())
        self.assertEqual(self.cats(out), [("管理失真", "stale", None), ("管理失真", "nogoal", None),
                                          ("管理失真", "idle", None), ("管理失真", "branch", "待识别"),
                                          ("管理失真", "dirty", "待识别")])
        self.assertIn("下一步不当可信依据", out[0]["item"])

    def test_idle_uses_latest_of_merge_and_current_commit(self):
        r = rec("a", freshness={"last_merge": "2026-10-01T10:00:00+08:00", "current_last_commit": "2026-11-08T10:00:00+08:00"})
        self.assertEqual(pf.risks(W, [r], [], cov()), [])

    def test_delivery_risk_is_listed_as_not_connected(self):
        self.assertTrue(any(x.startswith("交付风险") for x in pf.NOT_CONNECTED))


# ---------- 跨项目引用 ----------

class CrossRefTests(Base):
    def test_only_explicit_roster_refs_with_target_state(self):
        self.gh_graphql({31: "OPEN", 83: "CLOSED", 7: "OPEN"})
        r = rec("skills", yesterday_delta={"merged_prs": [merged(74, "generate 模式（配合 niuma-home#31）",
                                                               "关联 o/niuma-home#31\n配合 github-center #83\n见github-center#7\n"
                                                               "本仓库 my-skills#9 不算\nother-repo#4 不在名册\n"
                                                               "xniuma-home#5 不是引用\nfork/niuma-home#6 别人的 fork")]})
        out = pf.cross_refs(W, [r], ROSTER)
        self.assertEqual([(x["to_project"], x["to_number"], x["from_state"], x["to_closed"]) for x in out],
                         [("niuma", 31, "已合并", False), ("hub", 83, "已合并", True), ("hub", 7, "已合并", False)])
        self.assertEqual(out[0]["where"], "合并的 PR #74")
        self.assertIn("配合 niuma-home#31", out[0]["quote"])

    def test_chinese_right_after_ref_and_long_lines(self):
        """编号后面紧跟汉字也要认；长句里编号在 200 字以后、正文里本仓库编号很多，都不能把跨仓库那句丢掉。"""
        self.gh_graphql({31: "OPEN", 32: "OPEN", 33: "OPEN", 34: "OPEN", 35: "OPEN"})
        body = "\n".join([f"- 第 {i} 步（#{i}）" for i in range(1, 40)]
                          + [f"见 PR #{i}" for i in range(1, 25)]                 # 本仓库「PR #i」很多也不能挤掉后面的
                          + ["配合niuma-home#31的接口", "niuma-home#32号", "甲" * 450 + "等niuma-home#33合并后" + "乙" * 300,
                             "niuma-home#340", "接着 PR #5 做，" + "丙" * 450 + "配合 niuma-home#35"])
        pr = merged(74, "做乙", body)
        self.assertFalse([x for x in pr["ref_lines"] if "步（#" in x])        # 「（#12）」这种本仓库编号不收，快照不带整段正文
        self.assertEqual(len(pr["ref_lines"]), 29)
        r = rec("skills", yesterday_delta={"merged_prs": [pr]})
        out = pf.cross_refs(W, [r], ROSTER)
        self.assertEqual([x["to_number"] for x in out], [31, 32, 33, 340, 35])
        self.assertIn("niuma-home#35", out[4]["quote"])                  # 摘录围绕引用本身，不只留句首
        long_line, = [x for x in pr["ref_lines"] if "niuma-home#33" in x]          # 1a 截长句：两头标截断
        self.assertTrue(long_line.startswith("…") and long_line.endswith(pd.CUT_MARK))
        self.assertIn("niuma-home#33", out[2]["quote"])

    def test_bad_roster_github_is_skipped(self):
        r = rec("skills", yesterday_delta={"merged_prs": [merged(1, "配合 niuma-home#31")]})
        self.assertEqual(pf.cross_refs(W, [r], [{"id": "skills", "github": "o/my-skills"}, {"id": "niuma", "github": "niuma-home"}]), [])

    def test_open_pr_titles_count_too(self):
        r = rec("hub", current_state={"open_prs": [{"number": 2, "title": "等 my-skills#74", "created": IN_DAY}]})
        self.gh_graphql({74: "MERGED"})
        out = pf.cross_refs(W, [r], ROSTER)
        self.assertEqual([(x["from_state"], x["to_ref"], x["to_closed"]) for x in out], [("开着", "my-skills#74", True)])

    def test_target_state_unknown_when_gh_fails(self):
        (self.state / "fail").write_text("error connecting to api.github.com")
        r = rec("skills", yesterday_delta={"merged_prs": [merged(74, "配合 niuma-home#31")]})
        out = pf.cross_refs(W, [r], ROSTER)
        self.assertEqual(len(out), 1)
        self.assertIsNone(out[0]["to_closed"])     # 取不到：不当成开着，也不当成关了

    def test_no_roster_github_no_refs(self):
        r = rec("skills", yesterday_delta={"merged_prs": [merged(1, "niuma-home#31")]})
        self.assertEqual(pf.cross_refs(W, [r], [{"id": "skills"}]), [])


# ---------- 观察池 ----------

class LessonTests(Base):
    def repo(self, main="main"):
        r = self.root / "proj"
        r.mkdir()
        sh(r, "init", "-q", "-b", main)
        (r / "docs").mkdir()
        f = r / "docs" / "LESSONS.md"
        f.write_text("# LESSONS\n\n## L-xx <一句话现象>\n")
        sh(r, "add", "-A")
        sh(r, "commit", "-q", "-m", "起步", at="2026-11-01 10:00:00 +0800")
        f.write_text(f.read_text() + "\n## L-01 老坑\n")
        sh(r, "commit", "-qam", "老坑", at="2026-11-03 10:00:00 +0800")
        f.write_text(f.read_text() + "\n## L-02 当天的坑\n\n## L-03 <一句话现象>\n")
        sh(r, "commit", "-qam", "新坑", at="2026-11-08 10:00:00 +0800")
        f.write_text(f.read_text() + "\n## L-04 下一个统计日\n")
        sh(r, "commit", "-qam", "明天", at="2026-11-09 04:00:00 +0800")
        return r

    def test_only_entries_added_in_stat_day(self):
        r = self.repo()
        out = pf.new_lessons(W, [{"id": "proj", "path": str(r), "main": "main"}], self.notes)
        self.assertEqual([x["what"] for x in out], ["proj docs/LESSONS.md：L-02 当天的坑"])
        self.assertFalse(out[0]["threshold_met"])
        self.assertEqual(self.notes, [])

    def test_observations_list_lessons_of_stat_day_only(self):
        """新坑只列统计日内的；14 天窗口会把整份新建的 LESSONS 都算进来（11-08 回放一下列出 84 条）。"""
        self.sources()
        r = self.repo()
        out = [x for x in pf.observations(W, [{"id": "proj", "path": str(r), "main": "main"}], self.notes) if x["kind"] == "新坑"]
        self.assertEqual([x["what"] for x in out], ["proj docs/LESSONS.md：L-02 当天的坑"])

    def test_renamed_or_moved_entries_are_not_new(self):
        r = self.repo()
        old = r / "docs" / "LESSONS.md"
        text = old.read_text().replace("## L-01 老坑", "## L-01 老坑（改了标题）")
        old.unlink()
        (r / "LESSONS.md").write_text(text)                       # 改标题 + 从 docs/ 挪到根目录
        sh(r, "add", "-A")
        sh(r, "commit", "-qm", "挪位", at="2026-11-08 11:00:00 +0800")
        out = pf.new_lessons(W, [{"id": "proj", "path": str(r), "main": "main"}], self.notes)
        self.assertEqual([x["what"] for x in out], ["proj docs/LESSONS.md：L-02 当天的坑"])

    def test_project_without_path_is_skipped(self):
        r = self.repo()
        old = os.getcwd()
        os.chdir(r)                                                # 没登记目录时不能拿当前目录顶替
        self.addCleanup(os.chdir, old)
        self.assertEqual(pf.new_lessons(W, [{"id": "nopath", "main": "main"}], self.notes), [])

    def test_missing_main_branch_is_a_note_not_a_crash(self):
        r = self.repo(main="trunk")
        out = pf.new_lessons(W, [{"id": "proj", "path": str(r), "main": "main"}], self.notes)
        self.assertEqual(out, [])
        self.assertTrue(any("proj 的 LESSONS 读不了" in n for n in self.notes))


class ObservationTests(Base):
    def row(self, at, **kw):
        base = {"evaluated_at": at, "source": "live", "repo": "o/my-skills", "pr": "5", "tier": "L1",
                "suggestion": "建议合并", "outcome": "退回", "outcome_at": at, "agree": "否"}
        return {**base, **kw}

    def test_officer_misses_grouped_and_threshold(self):
        self.ledger([self.row("2026-11-01T10:00:00+08:00"), self.row("2026-11-08T10:00:00+08:00", pr="6"),
                     self.row("2026-10-20T10:00:00+08:00", pr="7"),                 # 14 天以前
                     self.row("2026-11-08T10:00:00+08:00", pr="8", source="replay"),
                     self.row("2026-11-08T10:00:00+08:00", pr="9", agree="是"),
                     self.row("2026-11-08T10:00:00+08:00", pr="11", owner="策略合并"),   # 按策略合并的不算被推翻（#257）
                     self.row("2026-11-08T10:00:00+08:00", pr="10", tier="L2")])
        out = pf.officer_misses((W[1] - pf.LOOKBACK, W[1]))
        got = {x["what"]: (x["count"], x["threshold_met"], x["evidence"]) for x in out}
        self.assertEqual(got, {"L1 判「建议合并」，结局是「退回」": (2, True, ["o/my-skills#5", "o/my-skills#6"]),
                               "L2 判「建议合并」，结局是「退回」": (1, False, ["o/my-skills#10"])})
        self.ledger([self.row("2026-11-08T02:00:00Z"), self.row("2026-11-07T20:00:00+08:00")])
        x, = pf.officer_misses((W[1] - pf.LOOKBACK, W[1]))
        self.assertEqual((x["first"], x["last"]), ("2026-11-07T20:00:00+08:00", "2026-11-08T10:00:00+08:00"))

    def test_failures_grouped_by_job_id_not_name(self):
        fail = lambda i, name, job, ts: actions.act(action_id=f"cron:{i}", ts=ts, project_id="hermes",
                                                    request=f"定时任务「{name}」（{job}）", result="失败")
        self.sources(cron=[fail(2, "巡检（改名）", "j1", IN_DAY),       # 改名了，还是同一个任务；库里不保证按时间排
                           fail(1, "巡检", "j1", "2026-11-02T10:00:00+08:00"),
                           fail(3, "巡检", "j2", IN_DAY)],                # 同名，不是同一个任务
                     dispatch=[actions.act(action_id="d1", ts=IN_DAY, project_id="niuma", request="派单", result="失败"),
                               actions.act(action_id="d2", ts=IN_DAY, project_id="niuma", request="另一张单", result="失败")])
        out = [x for x in pf.observations(W, [], self.notes) if x["kind"] in ("失败", "重复失败")]
        got = {x["what"]: (x["kind"], x["count"], x["evidence"]) for x in out}
        self.assertEqual(got, {"定时任务库：j1": ("重复失败", 2, ["cron:2", "cron:1"]),
                               "定时任务库：j2": ("失败", 1, ["cron:3"]),
                               "派单台账：niuma d1": ("失败", 1, ["d1"]), "派单台账：niuma d2": ("失败", 1, ["d2"])})
        j1, = [x for x in out if x["what"] == "定时任务库：j1"]
        self.assertEqual((j1["first"], j1["last"]), ("2026-11-02T10:00:00+08:00", IN_DAY))
        self.assertEqual(self.calls[0], (W[1] - pf.LOOKBACK, W[1]))      # 失败往回扫 14 天现算

    def test_failure_source_error_goes_to_notes(self):
        self.sources(cron=RuntimeError("库锁了"))
        self.assertEqual(pf.observations(W, [], self.notes), [])
        self.assertTrue(any("失败记录读不了" in n for n in self.notes))


# ---------- 汇总、覆盖与可信度 ----------

class BuildTests(Base):
    def fake_collect(self, recs, acts, act_cov=None, act_notes=()):
        p1 = {"projects": recs, "coverage": {"projects": len(recs), "read_ok": len(recs), "errors": {}}}
        p2 = {"actions": acts, "coverage": act_cov or {"派单台账": {"ok": True, "count": len(acts), "error": None}},
              "notes": list(act_notes)}
        for target, val in ((pd, p1), (actions, p2)):
            p = mock.patch.object(target, "collect", lambda *a, _v=val: _v)
            p.start()
            self.addCleanup(p.stop)

    def test_coverage_marks_unknown_and_not_connected(self):
        self.sources()
        self.fake_collect([rec("skills", freshness={"stale": True}), rec("niuma")], [],
                          act_cov={"夜跑": {"ok": False, "count": None, "error": "OSError：读不了"}},
                          act_notes=["派单 #3 没有回执"])
        f = pf.build(D, ROSTER)
        c = f["coverage"]
        self.assertEqual(c["stale"], ["skills"])
        self.assertEqual(c["actions"]["夜跑"]["count"], None)                       # 取不到是 None，不是 0
        self.assertEqual(c["action_notes"], ["派单 #3 没有回执"])
        self.assertTrue(any(x.startswith("Hermes 对话记录") and "没接" in x for x in c["not_connected"]))
        self.assertIn("运行风险", [x["category"] for x in f["risks"]])
        self.assertEqual((f["stat_day"], f["window"]["start"]), ("2026-11-08", "2026-11-08T04:00:00+08:00"))

    def test_actions_attached_and_installs_only_when_verified(self):
        self.sources()
        recs = [rec("skills", yesterday_delta={"merged_prs": [{"number": 74, "title": "做甲"}]}), rec("niuma")]
        acts = [act(action_id="i1", kind="install", result="业务已验证"),
                act(action_id="i2", kind="install", result="调用成功"),
                act(action_id="c1", kind="cron", project_id="hermes")]
        self.fake_collect(recs, acts)
        f = pf.build(D, ROSTER)
        skills = f["projects"][0]
        self.assertEqual(skills["actions"], ["i1", "i2"])
        self.assertEqual(skills["installs"], ["i1"])
        self.assertIn("1 条回读验证过的安装记录", skills["yesterday_delta"]["merged_prs"][0]["delivery_note"])
        self.assertEqual(f["projects"][1]["installs"], [])
        self.assertEqual(f["other_actions"], ["c1"])
        self.assertEqual(f["counts"]["projects_with_delta"], ["skills"])
        self.assertEqual(f["activity"]["actions_by_kind"], {"install": 2, "cron": 1})

    def test_merged_pr_without_install_has_no_delivery_claim(self):
        """7.1「PR 已合并但没安装」：1c 不替它写「已交付」，没有安装记录就不加交付说明。"""
        self.sources()
        self.fake_collect([rec("skills", yesterday_delta={"merged_prs": [{"number": 74, "title": "做甲"}]})], [])
        pr = pf.build(D, ROSTER)["projects"][0]["yesterday_delta"]["merged_prs"][0]
        self.assertNotIn("delivery_note", pr)

    def test_dependency_between_projects(self):
        """7.1「项目之间有依赖」：skills 合并的 PR 明文写了配合 niuma-home#31；niuma 那边 #31 还开着、已卡了 3 天。
        快照里要同时有：这条引用（这边已合并、那边没关），和 niuma 那边的协作阻塞，两边能对上编号。"""
        self.sources()
        self.gh_graphql({31: "OPEN"})
        recs = [rec("skills", yesterday_delta={"merged_prs": [merged(74, "skill 的 generate 模式",
                                                                   "## 做了什么\n- 加 generate 模式\n配合 niuma-home#31，那边接上才算交付")]}),
                rec("niuma", current_state={"open_prs": [{"number": 31, "title": "接 generate 模式",
                                                          "created": "2026-11-05T10:00:00+08:00"}]})]
        self.fake_collect(recs, [])
        f = pf.build(D, ROSTER)
        self.assertEqual([(x["from_project"], x["from_state"], x["to_project"], x["to_number"], x["to_closed"])
                          for x in f["cross_refs"]], [("skills", "已合并", "niuma", 31, False)])
        blocked = [x for x in f["risks"] if x["category"] == "协作阻塞"]
        self.assertEqual([(x["project_id"], x["item"].split("：")[0]) for x in blocked], [("niuma", "PR #31 开着超过 2 天")])
        self.assertIn("niuma", f["counts"]["risk_projects"])

    def test_no_activity_day_still_reports_state(self):
        """7.1「昨天没有新动作」：没有增量也照样出每个项目的当前状态和关注事项。"""
        self.sources()
        self.fake_collect([rec("niuma", current_state={"next_step": "等常浩定方向"},
                               freshness={"last_merge": "2026-10-01T10:00:00+08:00"})], [])
        f = pf.build(D, ROSTER)
        self.assertEqual(f["counts"]["projects_with_delta"], [])
        self.assertEqual(f["projects"][0]["current_state"]["next_step"], "等常浩定方向")
        self.assertEqual([c["kind"] for c in f["decisions"]], ["current"])
        self.assertIn("7 天以上没有新动作", [x["item"] for x in f["risks"]])

    def test_unknown_delta_is_none_not_zero(self):
        """第 6 节：取数失败写「未知」，不伪造零进展。hermes 自己的风险不算进项目数。"""
        self.sources()
        bad = rec("niuma")                                        # 1a 取不到：增量各项都是 None
        self.fake_collect([rec("skills", yesterday_delta={"merged_prs": [merged(1, "x")], "closed_issues": [],
                                                          "commits": {"main": 3}}), bad],
                          [act(action_id="c1", kind="cron", project_id="hermes", result="失败"),
                           act(action_id="d9", kind="dispatch", project_id=None, result="失败")])
        f = pf.build(D, ROSTER)
        self.assertEqual((f["activity"]["merged_prs"], f["activity"]["closed_issues"], f["activity"]["commits"]), (None, None, None))
        self.assertEqual(f["counts"]["projects_delta_unknown"], ["niuma"])
        self.assertEqual(f["counts"]["projects_with_delta"], ["skills"])
        self.assertEqual((f["counts"]["risk_projects"], f["counts"]["non_project_risks"]), ([], 2))
        self.fake_collect([rec("skills", yesterday_delta={"merged_prs": [merged(1, "x")], "closed_issues": [],
                                                          "commits": {"main": 3, "dev": 1}})], [])
        f = pf.build(D, ROSTER)
        self.assertEqual((f["activity"]["merged_prs"], f["activity"]["closed_issues"], f["activity"]["commits"]), (1, 0, 4))

    def test_one_part_failing_is_unknown_not_empty(self):
        self.sources()
        self.fake_collect([rec("skills", current_state={"next_step": "常浩定"})], [act(action_id="x1")])
        with mock.patch.object(pf, "cross_refs", side_effect=RuntimeError("坏了")), \
                mock.patch.object(pf, "risks", side_effect=KeyError("x")):
            f = pf.build(D, ROSTER)
        self.assertEqual((f["cross_refs"], f["risks"], f["counts"]["risk_projects"]), (None, None, None))
        self.assertEqual(f["coverage"]["failed_parts"], ["风险", "跨项目引用"])
        self.assertTrue(any(n.startswith("跨项目引用算不出来") for n in f["notes"]))
        self.assertEqual([c["kind"] for c in f["decisions"]], ["current"])          # 其余照出
        self.assertEqual(f["projects"][0]["actions"], ["x1"])

    def test_main_reports_unknown_part_and_exits_zero(self):
        """一段降级成 None 时快照照写，main 正常结束，打印里写「未知」和算不出来的段。"""
        self.sources()
        self.fake_collect([rec("skills")], [])
        roster = self.root / "projects.json"
        roster.write_text(json.dumps({"projects": ROSTER[:1]}))
        with mock.patch.object(progress_data, "ROSTER", roster), mock.patch.object(pf, "risks", side_effect=KeyError("x")), \
                mock.patch.object(sys, "argv", ["pdaily_facts.py", "--date", "2026-11-08"]), mock.patch("builtins.print") as out:
            pf.main()
        self.assertIn("未知 条风险候选；算不出来的：风险", out.call_args[0][0])
        self.assertIsNone(json.loads((self.root / "daily" / "2026-11-08.facts.json").read_text())["risks"])

    def test_write_is_atomic_and_dry_run_writes_nothing(self):
        self.sources()
        self.fake_collect([rec("skills")], [])
        roster = self.root / "projects.json"
        roster.write_text(json.dumps({"projects": ROSTER[:1]}))
        with mock.patch.object(progress_data, "ROSTER", roster), \
                mock.patch.object(sys, "argv", ["pdaily_facts.py", "--date", "2026-11-08", "--dry-run"]), \
                mock.patch("builtins.print") as out:
            pf.main()
        self.assertEqual(json.loads(out.call_args[0][0])["stat_day"], "2026-11-08")
        self.assertFalse((self.root / "daily").exists())
        with mock.patch.object(progress_data, "ROSTER", roster), \
                mock.patch.object(sys, "argv", ["pdaily_facts.py", "--date", "2026-11-08"]), \
                mock.patch("builtins.print"):
            pf.main()
        files = sorted(p.name for p in (self.root / "daily").iterdir())
        self.assertEqual(files, ["2026-11-08.facts.json"])               # 没留下 .tmp
        self.assertEqual(json.loads((self.root / "daily" / files[0]).read_text())["projects"][0]["project_id"], "skills")


if __name__ == "__main__":
    unittest.main()
