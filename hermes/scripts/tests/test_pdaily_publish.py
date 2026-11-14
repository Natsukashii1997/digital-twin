"""pdaily_publish.py 自测（#111 批 2a）：解析模型回复、核对、核心数据、甲版卡片、事实版降级，外加 V2 7.1 归批 2 的场景
（取数失败写未知、模型生成失败出事实版、昨天没有新动作照发）。

事实快照用手造的小样（结构同 pdaily_facts.build 的输出）；Hermes 用临时目录里的假命令，不调模型；会话库用临时 SQLite。
"""
import copy
import json
import os
import sqlite3
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import pdaily_publish as pp  # noqa: E402

DAY = "2026-11-08"


def project(pid, name, prs=(), commits=None, stale=False):
    return {
        "project_id": pid, "name": name, "objective": {"kind": "目标", "text": f"{name}的目标", "source": "CURRENT.md"},
        "milestones": [], "freshness": {"stale": stale, "current_as_of": "2026-11-08T20:00:00+08:00"},
        "current_state": {"top_section": f"{name}最新一节", "next_step": "接着做", "next_reliable": True,
                          "open_prs": [], "dirty_files": 0},
        "yesterday_delta": {"merged_prs": [{"number": n, "title": t, "engineering": "已合并", "delivery": "未核实",
                                            "acceptance": "未核实", "claims": []} for n, t in prs],
                            "closed_issues": [], "commits": commits if commits is not None else {"main": len(prs)},
                            "current_changed": bool(prs)},
        "notes": [], "errors": [], "installs": [],
    }


def facts():
    return {
        "stat_day": DAY, "timezone": "Asia/Shanghai",
        "window": {"start": "2026-11-08T04:00:00+08:00", "end": "2026-11-09T04:00:00+08:00"},
        "fetched_at": "2026-11-09T07:52:00+08:00",
        "projects": [project("work-desk", "牛马之家", [(43, "月度策略证据口径")]),
                     project("digital-twin", "数字分身", [(102, "M5 小修")]),
                     project("disk-slim", "磁盘瘦身", commits={"main": 2}),
                     project("personal-ai-workbench", "个人 AI 工作台（数码世界）", commits={})],
        "actions": [
            {"action_id": "c1", "ts": "2026-11-08T05:00:00+08:00", "project_id": "hermes", "kind": "cron",
             "request": "定时任务「Tidings」", "actor": "Hermes", "auth_tier": None, "auth_source": "已登记的定时任务",
             "decision": "执行", "result": "调用成功", "verification": "状态 completed", "follow_up": None,
             "origin": "定时任务库", "auth_gap": None},
            {"action_id": "m1", "ts": "2026-11-08T21:00:00+08:00", "project_id": "work-desk", "kind": "merge",
             "request": "常浩点 PR 卡片「合并」：niuma-home#43", "actor": "Hermes", "auth_tier": "B",
             "auth_source": "常浩点卡片", "decision": "执行", "result": "调用成功", "verification": "PR 已合并",
             "follow_up": None, "origin": "合并官", "auth_gap": None},
        ],
        "decisions": [{"kind": "night", "project_id": "hermes", "item": "夜跑停机后收尾，授权 72 到期 2026-11-09T06:00",
                       "why_owner": "主控还开着", "source": "run-log", "evidence": [], "type": None,
                       "recommendation": None, "impact": None, "reply": None, "deadline": None,
                       "status": "待你决定", "since": None}],
        "risks": [{"category": "管理失真", "project_id": "work-desk", "item": "主目录有 15 处未提交改动",
                   "evidence": ["本机 git status"], "level": "待识别"}],
        "cross_refs": [], "observations": [],
        "counts": {"projects_with_delta": ["work-desk", "digital-twin"], "projects_delta_unknown": [],
                   "decision_candidates": 1, "risk_projects": ["work-desk"], "non_project_risks": 0},
        "activity": {"merged_prs": 2, "closed_issues": 1, "commits": 2, "actions_by_kind": {"cron": 1, "merge": 1}},
        "coverage": {"projects": {"projects": 4, "read_ok": 4, "errors": {}},
                     "actions": {"合并官": {"ok": True, "count": 1, "error": None},
                                 "定时任务库": {"ok": True, "count": 1, "error": None}},
                     "action_notes": [], "stale": [], "not_connected": ["Hermes 对话记录：没接"], "failed_parts": []},
        "notes": [],
    }


def card_fields(**over):
    c = {
        "headline": "牛马之家证据口径合并；今天先收尾夜跑",
        "progress_projects": ["work-desk"],
        "decisions": [{"type": "限时", "candidate": 0, "project": "猛鬼兽", "item": "夜跑收尾", "recommendation": "按授权 72 收尾",
                       "deadline": "11-09 06:00", "reply": "按授权 72 收尾"}],
        "achievements": [{"project": "牛马之家", "text": "证据口径对齐合并（#43），未发布"}],
        "risks": [{"level": "待识别", "category": "管理失真", "project": "牛马之家", "text": "主目录 15 处未提交"}],
        "today": [{"priority": "P0", "item": "夜跑收尾", "owner_action": "猛鬼兽：你回复后按授权 72 收尾"}],
    }
    c.update(over)
    return c


def report_text(n=("1", "1", "1"), sections=None, extra=""):
    secs = sections or pp.SECTIONS
    body = "\n\n".join(f"## {s}\n- 内容" for s in secs)
    return f"一句话：牛马之家证据口径合并\n实质推进 {n[0]} 个项目｜待你决策 {n[1]} 件｜阻塞 / 风险 {n[2]} 个项目\n\n{body}\n{extra}"


def reply_text(card=None, report=None):
    return f"{pp.CARD_MARK}\n{json.dumps(card or card_fields(), ensure_ascii=False)}\n{pp.REPORT_MARK}\n{report or report_text()}"


class ParseTests(unittest.TestCase):
    def test_ok(self):
        card, report = pp.parse_reply(reply_text())
        self.assertEqual(card["progress_projects"], ["work-desk"])
        self.assertIn("一、需要你拍板", report)

    def test_fenced_json_ok(self):
        card, _ = pp.parse_reply(f"{pp.CARD_MARK}\n```json\n{{\"a\": 1}}\n```\n{pp.REPORT_MARK}\n正文")
        self.assertEqual(card, {"a": 1})

    def test_missing_marks(self):
        with self.assertRaises(pp.GenError):
            pp.parse_reply("我写好了日报")

    def test_bad_json(self):
        with self.assertRaises(pp.GenError):
            pp.parse_reply(f"{pp.CARD_MARK}\n{{坏的\n{pp.REPORT_MARK}\n正文")


class CheckTests(unittest.TestCase):
    def errs(self, card=None, report=None, f=None):
        f, card = f or facts(), card or card_fields()
        self.warns = pp.normalize(f, card)
        return pp.check(f, card, report or report_text())

    def test_good_passes(self):
        self.assertEqual(self.errs(), [])

    def test_project_not_in_roster(self):
        e = self.errs(card_fields(achievements=[{"project": "不存在的项目", "text": "做了"}]))
        self.assertTrue(any("不在名册里" in x for x in e), e)

    def test_dependency_pair_names_checked(self):
        ok = card_fields(risks=[{"level": "中", "category": "管理失真", "project": "牛马之家 ↔ 数字分身", "text": "x"}])
        self.assertEqual(self.errs(ok, report_text(n=("1", "1", "2"))), [])   # 依赖两头各算一个项目
        bad = card_fields(risks=[{"level": "中", "category": "管理失真", "project": "牛马之家 ↔ 外星项目", "text": "x"}])
        self.assertTrue(any("外星项目" in x for x in self.errs(bad)))

    def test_fabricated_ref(self):
        e = self.errs(report=report_text(extra="另外合并了 #999"))
        self.assertTrue(any("#999" in x for x in e), e)

    def test_deadline_needs_source(self):
        """最晚时间只认对应候选里写着的时间：编的、只写时刻的、不连候选的都去掉（不显示），不让整份降级。"""
        d0 = card_fields()["decisions"][0]
        for dl, cand in (("11-10 09:00", 0), ("06:00", 0), ("11-09 06:00", None), ("今天 18:00", 0)):
            card = card_fields(decisions=[dict(d0, deadline=dl, candidate=cand)])
            self.assertEqual(self.errs(card), [], dl)
            self.assertIsNone(card["decisions"][0]["deadline"], dl)
            self.assertTrue(any("最晚时间" in w for w in self.warns), dl)

    def test_deadline_formats_normalized(self):
        for dl in ("2026-11-09 06:00", "11-09 06:00", "11月9日 06:00", "11-9 6:00"):
            card = card_fields(decisions=[dict(card_fields()["decisions"][0], deadline=dl)])
            self.assertEqual(self.errs(card), [])
            self.assertEqual(card["decisions"][0]["deadline"], "11-09 06:00", dl)

    def test_wrong_types_do_not_crash(self):
        """模型把 project 写成列表、progress 套了一层：报错，不抛异常。"""
        bad = card_fields(achievements=[{"project": ["牛马之家", "数字分身"], "text": "x"}],
                          progress_projects=[["work-desk"]], decisions="无")
        e = self.errs(bad)
        self.assertTrue(any("achievements[0] 缺 project" in x for x in e), e)
        self.assertTrue(any("progress_projects" in x for x in e), e)
        self.assertTrue(any("缺 decisions" in x for x in e), e)

    def test_today_matches_alias(self):
        """前面写全名、今日推进写简称（11-07 真实稿误拦过）。"""
        c = card_fields(risks=[{"level": "低", "category": "协作阻塞", "project": "个人 AI 工作台（数码世界）", "text": "x"}],
                        today=[{"priority": "P1", "item": "数码世界终验收口", "owner_action": "常浩终验"}])
        self.assertEqual(self.errs(c, report_text(n=("1", "1", "1"))), [])

    def test_alias_names(self):
        ok = card_fields(risks=[{"level": "低", "category": "协作阻塞", "project": "数码世界", "text": "PR 开着"}])
        self.assertEqual(self.errs(ok), [])

    def test_install_and_acceptance_claims(self):
        for text, bad in (("证据口径已安装到 5280", True), ("已上线并经常浩验收通过", True), ("发布成功", True),
                          ("已合并，未安装", False), ("合并了，交付、验收未核实", False), ("批 1 上线前的最后一步合并", False),
                          ("不能以自述的已发布替代", False)):
            e = self.errs(card_fields(achievements=[{"project": "牛马之家", "text": text}]))
            self.assertEqual(any("安装 / 验收" in x for x in e), bad, (text, e))
        f = facts()
        f["projects"][0]["installs"] = [{"ts": "2026-11-08T22:00:00+08:00"}]
        self.assertEqual(self.errs(card_fields(achievements=[{"project": "牛马之家", "text": "已安装到 5280"}]), f=f), [])

    def test_claim_bypasses_caught(self):
        """复审找到的绕过写法：宽否定词（无 / 待 / 需）和没列全的完成态。"""
        for text in ("月度策略已安装且运行无报错", "常浩验收已通过待合并", "常浩已实机验收", "已部署到 5280",
                     "页面已交付常浩", "发布到 5280 了", "装好了并在跑"):
            e = self.errs(card_fields(achievements=[{"project": "牛马之家", "text": text}]))
            self.assertTrue(any("安装 / 验收" in x for x in e), (text, e))
        e = self.errs(card_fields(headline="月度策略已安装无报错"))
        self.assertTrue(any("headline" in x for x in e), e)
        for text in ("目标是批 1 上线到 5280", "安装与验收待核", "无法确认是否已安装"):
            e = self.errs(card_fields(achievements=[{"project": "牛马之家", "text": text}]))
            self.assertFalse(any("安装 / 验收" in x for x in e), (text, e))

    def test_claims_in_report_section_two(self):
        secs = [f"## {x}\n- 内容" for x in pp.SECTIONS]
        secs[1] = "## 二、昨日关键成果\n- 牛马之家：证据口径已安装到 5280\n- 数字分身：合并，交付未核实"
        report = "一句话：x\n实质推进 1 个项目｜待你决策 1 件｜阻塞 / 风险 1 个项目\n\n" + "\n\n".join(secs)
        e = self.errs(report=report)
        self.assertTrue(any("完整报告二段" in x for x in e), e)

    def test_ref_spellings(self):
        self.assertEqual(self.errs(report=report_text(extra="PR#43 和 issue#102、PR #43")), [])
        for bad in ("驿窗#999", "牛马之家#999", "Natsukashii1997/niuma-home#999", "pr#999"):
            e = self.errs(report=report_text(extra=f"见 {bad}"))
            self.assertTrue(any("999" in x for x in e), (bad, e))

    def test_reply_shells_and_cut(self):
        c = card_fields(decisions=[dict(card_fields()["decisions"][0], reply="回复「合并」即可")])
        self.errs(c)
        self.assertEqual(c["decisions"][0]["reply"], "合并")
        self.assertEqual(pp.cut("材料记录了 HTTP 业务链，以及授权前置条件的反例测试，还有更多", 20),
                         "材料记录了 HTTP 业务链…")       # 停在标点上，不切成「授…」

    def test_pr_count(self):
        e = self.errs(card_fields(achievements=[{"project": "牛马之家", "text": "合并 9 个 PR"}]))
        self.assertTrue(any("合并 9 个 PR" in x for x in e), e)
        self.assertEqual(self.errs(card_fields(achievements=[{"project": "牛马之家", "text": "合并 1 个 PR"}])), [])

    def test_qualified_refs(self):
        e = self.errs(report=report_text(extra="见 my-skills#999"))
        self.assertTrue(any("my-skills#999" in x for x in e), e)
        self.assertEqual(self.errs(report=report_text(extra="见 work-desk#43")), [])

    def test_percent_must_be_in_material(self):
        f = facts()
        f["projects"][0]["current_state"]["top_section"] = "压到原来 70%"
        self.assertEqual(self.errs(report=report_text(extra="压到 70%"), f=f), [])
        e = self.errs(report=report_text(extra="完成 85%"), f=f)
        self.assertTrue(any("85%" in x for x in e), e)

    def test_progress_dedup(self):
        self.assertEqual(pp.header_numbers(card_fields(progress_projects=["work-desk", "work-desk"])), ("1", "1", "1"))

    def test_no_false_alarms(self):
        """正常写法不误拦：正文提前提到段名、第六段带缩进子条目、开头数字加粗、｜两侧空格。"""
        secs = "\n\n".join(f"## {x}\n- 内容" for x in pp.SECTIONS)
        secs = secs.replace("## 一、需要你拍板\n- 内容", "## 一、需要你拍板\n- 内容，细节见六、今日推进计划")
        secs = secs.replace("## 六、今日推进计划\n- 内容", "## 六、今日推进计划\n" + "\n".join(
            f"{i}. 目标{i}\n   - 负责人：Hermes\n   - 动作：做\n   - 完成标准：有证据" for i in range(1, 6)))
        report = f"一句话：x\n实质推进 **1** 个项目 ｜ 待你决策 **1** 件 ｜ 阻塞 / 风险 **1** 个项目\n\n{secs}"
        self.assertEqual(self.errs(report=report), [])

    def test_header_numbers_must_match_card(self):
        e = self.errs(report=report_text(n=("3", "1", "1")))
        self.assertTrue(any("三个数" in x for x in e), e)

    def test_missing_and_misordered_sections(self):
        e = self.errs(report=report_text(sections=pp.SECTIONS[:-1]))
        self.assertTrue(any("覆盖与可信度" in x for x in e), e)
        swapped = pp.SECTIONS[:]
        swapped[0], swapped[1] = swapped[1], swapped[0]
        self.assertTrue(any("顺序" in x for x in self.errs(report=report_text(sections=swapped))))

    def test_progress_needs_delta(self):
        e = self.errs(card_fields(progress_projects=["disk-slim"]), report_text())
        self.assertTrue(any("不算实质推进" in x for x in e), e)

    def test_progress_not_judged_when_github_unknown(self):
        """GitHub 取不到（null）不等于没合并：核不了就不判错（#176，11-12 那期误判 digital-twin）。"""
        for key in ("merged_prs", "closed_issues"):
            f = facts()
            f["projects"][2]["yesterday_delta"][key] = None          # disk-slim：只有提交，这一项取不到
            e = self.errs(card_fields(progress_projects=["disk-slim"]), report_text(), f)
            self.assertFalse(any("不算实质推进" in x for x in e), (key, e))

    def test_today_limit_and_link(self):
        t = card_fields()["today"][0]
        e = self.errs(card_fields(today=[t] * 4))
        self.assertTrue(any("最多 3 条" in x for x in e))
        e = self.errs(card_fields(today=[{"priority": "P1", "item": "整理桌面", "owner_action": "常浩"}]))
        self.assertTrue(any("对不上" in x for x in e), e)

    def test_bad_enum(self):
        e = self.errs(card_fields(decisions=[dict(card_fields()["decisions"][0], type="紧急")]))
        self.assertTrue(any("type" in x for x in e))

    def test_percent_without_source(self):
        e = self.errs(report=report_text(extra="牛马之家完成 80%"))
        self.assertTrue(any("百分比" in x for x in e))

    def test_unknown_merged_not_zero(self):
        f = facts()
        f["activity"]["merged_prs"] = None
        e = self.errs(report=report_text(extra="昨天合并 0 个 PR"), f=f)
        self.assertTrue(any("不能写 0" in x for x in e), e)


class MetricTests(unittest.TestCase):
    def rows(self, f=None, card=None):
        return {k: (v, n) for k, v, n in pp.metrics(f or facts(), card or card_fields())}

    def test_normal(self):
        r = self.rows()
        self.assertEqual(r["有推进的项目"], ("**1** / 4", "其余 3 个无实质推进"))
        self.assertEqual(r["待你拍板"][1], "限时 1 件，最晚 11-09 06:00")
        self.assertEqual(r["合并 PR"][0], "2")
        self.assertIn("red", r["待你拍板"][0])
        self.assertIn("最晚 11-09 06:00", r["待你拍板"][1])
        self.assertEqual(r["Hermes 代理动作"], ("1", "合并 1、另定时任务 1 次"))

    def test_unknown_not_zero(self):
        f = facts()
        f["activity"]["merged_prs"] = None
        f["activity"]["actions_by_kind"] = None
        r = self.rows(f)
        self.assertEqual(r["合并 PR"][0], "未知")
        self.assertEqual(r["Hermes 代理动作"][0], "未知")

    def test_failed_action_source_marked(self):
        f = facts()
        f["coverage"]["actions"]["定时任务库"] = {"ok": False, "count": None, "error": "库锁了"}
        v, n = self.rows(f)["Hermes 代理动作"]
        self.assertEqual(v, "1+")
        self.assertIn("定时任务库取不到", n)


class CardTests(unittest.TestCase):
    def card(self, fields=None, f=None, **kw):
        return pp.build_card(f or facts(), fields or card_fields(), "生成：Hermes（m）", **kw)

    def text(self, c):
        return json.dumps(c, ensure_ascii=False)

    def test_layout_like_sample(self):
        c = self.card(doc_url="https://x.feishu.cn/docx/abc")
        self.assertEqual(c["schema"], "2.0")
        self.assertNotIn("width_mode", self.text(c))       # 默认宽度（常浩 11-09 定）
        self.assertEqual(c["header"]["title"]["content"], "📅 猛鬼兽日报 · 11-08（周日）")
        heads = [e["content"] for e in c["body"]["elements"] if e.get("tag") == "markdown" and e["content"].startswith("**")]
        self.assertEqual(heads, ["**📈 核心数据速览**", "**🎯 需要你拍板**", "**✅ 昨日关键成果**", "**⚠️ 风险与阻塞**", "**💡 今日推进**"])
        btn = [e for e in c["body"]["elements"] if e["tag"] == "button"][0]
        self.assertEqual(btn["behaviors"][0]["default_url"], "https://x.feishu.cn/docx/abc")

    def test_no_doc_drops_button_and_says_why(self):
        c = self.card(doc_error="云文档接口超时")
        self.assertFalse([e for e in c["body"]["elements"] if e["tag"] == "button"])
        self.assertIn("完整报告未生成：云文档接口超时", c["body"]["elements"][-1]["content"])

    def test_more_than_five_decisions_all_on_card(self):
        """常浩 11-10 定稿：各板块不折叠，全部放在卡片上。"""
        d = card_fields()["decisions"][0]
        c = self.card(card_fields(decisions=[dict(d, item=f"事{i}", type="普通") for i in range(7)] + [d]))
        t = self.text(c)
        self.assertNotIn("见完整报告", t)
        for i in range(7):
            self.assertIn(f"事{i}", t)
        self.assertLess(t.index("限时"), t.index("事0"))      # 限时排在普通前

    def test_table_like_restored_card(self):
        """速览表照 11-10 定稿：「昨日」窄、「说明」宽，自动行高、换行。"""
        tab = [e for e in self.card()["body"]["elements"] if e.get("tag") == "table"][0]
        self.assertEqual([c["width"] for c in tab["columns"]], ["25%", "15%", "60%"])
        self.assertEqual({c["vertical_align"] for c in tab["columns"]}, {"top"})
        self.assertEqual((tab["row_height"], tab["row_max_height"]), ("auto", "999px"))

    def test_subtitle_unread_projects(self):
        f = facts()
        f["coverage"]["projects"]["errors"] = {"disk-slim": "读不了仓库"}
        self.assertIn("1 个项目未读到", self.card(f=f)["header"]["subtitle"]["content"])

    def test_empty_sections(self):
        t = self.text(self.card(card_fields(decisions=[], achievements=[], risks=[], today=[])))
        for s in ("今天没有要你拍板的事", "昨天没有新成果", "没有风险和阻塞"):
            self.assertIn(s, t)


class FallbackTests(unittest.TestCase):
    def test_fact_version_complete(self):
        card, report = pp.fallback(facts(), "模型没写成：超时")
        self.assertIn("分析降级", report)
        pos = [report.find(s) for s in pp.SECTIONS]
        self.assertTrue(all(p >= 0 for p in pos) and pos == sorted(pos), pos)
        self.assertIn("实质推进 未知 个项目", report)
        self.assertEqual(card["degraded"], "模型没写成：超时")
        c = pp.build_card(facts(), card, pp.trace_line(facts(), None, card["degraded"]))
        t = json.dumps(c, ensure_ascii=False)
        self.assertIn("分析降级", c["header"]["subtitle"]["content"])
        self.assertIn("脚本事实版", t)
        self.assertIn("未知", t)                               # 有推进的项目没判断，写未知

    def test_fact_version_unknown_sources(self):
        f = facts()
        f["activity"] = {"merged_prs": None, "closed_issues": None, "commits": None, "actions_by_kind": None}
        f["coverage"]["projects"]["errors"] = {"disk-slim": "读不了"}
        _, report = pp.fallback(f, "x")
        self.assertIn("合并 PR 未知", report)
        self.assertIn("没读到：disk-slim", report)
        self.assertNotRegex(report, r"合并 PR 0")

    def test_quiet_day(self):
        """昨天没有新动作：照出，各项目当前状态都在。"""
        f = facts()
        for p in f["projects"]:
            p["yesterday_delta"].update(merged_prs=[], commits={}, current_changed=False)
        f["decisions"], f["risks"], f["actions"] = [], [], []
        f["counts"]["projects_with_delta"] = []
        f["activity"] = {"merged_prs": 0, "closed_issues": 0, "commits": 0, "actions_by_kind": {}}
        card, report = pp.fallback(f, "x")
        for p in f["projects"]:
            self.assertIn(p["name"], report)
        t = json.dumps(pp.build_card(f, card, "t"), ensure_ascii=False)
        self.assertIn("今天没有要你拍板的事", t)


class BuildTests(unittest.TestCase):
    """build()：调假 Hermes，留痕取会话库里的模型；失败、核对不过都出事实版。"""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "hermes"
        self.home.mkdir()
        con = sqlite3.connect(self.home / "state.db")
        con.execute("create table session_model_usage (session_id, model, task, first_seen)")
        con.execute("insert into session_model_usage values ('s1', 'gpt-x', '', 1)")
        con.execute("insert into session_model_usage values ('s1', 'deepseek-flash', '', 2)")
        con.commit()
        con.close()
        self.reply = self.tmp / "reply.txt"
        self.cli = self.tmp / "fake_hermes.py"
        self.cli.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import os, sys
            rc = int(os.environ.get("FAKE_RC", "0"))
            if rc == 0:
                print(open({str(self.reply)!r}, encoding="utf-8").read())
            sys.stderr.write("模型报错了\\n" if rc else "")
            if not os.environ.get("FAKE_NOSID"):
                sys.stderr.write("\\nsession_id: s1\\n")
            sys.exit(rc)
            """), encoding="utf-8")
        self.cli.chmod(0o755)
        self.patches = [mock.patch.object(pp, "HERMES", str(self.cli)), mock.patch.object(pp, "HERMES_HOME", self.home)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def out(self, name):
        return (self.tmp / "out" / f"{DAY}.{name}").read_text(encoding="utf-8")

    def test_model_ok(self):
        self.reply.write_text(reply_text(), encoding="utf-8")
        meta = pp.build(facts(), self.tmp / "out")
        self.assertIsNone(meta["degraded"], meta)
        self.assertEqual(meta["model"], "gpt-x，回退 deepseek-flash")
        self.assertIn("生成：Hermes（gpt-x，回退 deepseek-flash）｜接入：Hermes 派单", self.out("report.md"))
        card = json.loads(self.out("card.json"))
        self.assertIn("预览，未发送", card["body"]["elements"][-1]["content"])
        self.assertTrue((self.tmp / "out" / f"{DAY}.query.md").exists())

    def test_model_fails_gives_fact_version(self):
        with mock.patch.dict(os.environ, {"FAKE_RC": "1"}):
            meta = pp.build(facts(), self.tmp / "out")
        self.assertIn("模型没写成", meta["degraded"])
        self.assertIn("分析降级", self.out("report.md"))
        self.assertIn("脚本事实版", self.out("card.json"))

    def test_check_fails_gives_fact_version(self):
        self.reply.write_text(reply_text(report=report_text(extra="#999")), encoding="utf-8")
        meta = pp.build(facts(), self.tmp / "out")
        self.assertIn("核对没过", meta["degraded"])
        self.assertTrue(any("#999" in e for e in meta["check_errors"]))
        self.assertIn("#999", self.out("reply.md"))           # 原稿留着，方便查为什么没过

    def test_given_reply_skips_model(self):
        meta = pp.build(facts(), self.tmp / "out", reply=reply_text())
        self.assertIsNone(meta["degraded"])
        self.assertIsNone(meta["session_id"])


class GenerateEnvTests(unittest.TestCase):
    def test_gives_model_time_and_drops_claude_code_vars(self):
        tmp = Path(tempfile.mkdtemp())
        cli = tmp / "fake_hermes.py"
        cli.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json, os
            import sys
            env = {{k: os.environ.get(k) for k in ("HERMES_API_CALL_STALE_TIMEOUT", "ANTHROPIC_BASE_URL", "CLAUDECODE")}}
            env["reasoning"] = sys.argv[sys.argv.index("--reasoning") + 1]
            print(json.dumps(env))
            """), encoding="utf-8")
        cli.chmod(0o755)
        with mock.patch.object(pp, "HERMES", str(cli)), mock.patch.object(pp, "BUDGET", 900), \
                mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://x", "CLAUDECODE": "1"}):
            os.environ.pop("HERMES_API_CALL_STALE_TIMEOUT", None)
            out, _, _ = pp.generate(facts(), tmp)
        self.assertEqual(json.loads(out), {"HERMES_API_CALL_STALE_TIMEOUT": "900", "ANTHROPIC_BASE_URL": None, "CLAUDECODE": None,
                                           "reasoning": "medium"})       # 常浩 11-11：Opus 思考降一档（#157）


class SendTests(unittest.TestCase):
    """send()：建文档 → 现做卡片 → 拦工作内容 → 发 → 回读；假 lark-cli 记下每次调用。"""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "daily"
        self.out.mkdir()
        self.calls = self.tmp / "calls.jsonl"
        self.cli = self.tmp / "fake_lark.py"
        self.cli.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import json, os, sys
            a = sys.argv[1:]
            open({str(self.calls)!r}, "a").write(json.dumps(a, ensure_ascii=False) + "\\n")
            cmd = " ".join(a[2:4])
            if cmd == "docs +create":
                if os.environ.get("FAKE_DOC_FAIL"):
                    sys.stderr.write(json.dumps({{"ok": False, "error": {{"message": "没权限"}}}})); sys.exit(1)
                print(json.dumps({{"ok": True, "data": {{"document": {{"document_id": "d1", "url": "https://x.feishu.cn/docx/d1"}}}}}}))
            elif cmd == "im +messages-send":
                content = a[a.index("--content") + 1]
                open({str(self.tmp / "sent.json")!r}, "w").write(content)
                print(json.dumps({{"ok": True, "data": {{"message_id": "om_1"}}}}))
            elif cmd == "im +messages-mget":
                title = json.loads(open({str(self.tmp / "sent.json")!r}).read())["header"]["title"]["content"]
                print(json.dumps({{"ok": True, "data": {{"messages": [{{"msg_type": "interactive", "content": f'<card title="{{title}}">'}}]}}}}))
            """), encoding="utf-8")
        self.cli.chmod(0o755)
        self.patch = mock.patch.object(pp, "LARK", str(self.cli))
        self.patch.start()
        self.check = self.tmp / "self_check.py"           # 假工程自检：FAKE_CHECK_OUT 是输出，FAKE_CHECK_RC 是退出码
        self.check.write_text("import os, sys\nsys.stdout.write(os.environ.get('FAKE_CHECK_OUT', ''))\n"
                              "sys.exit(int(os.environ.get('FAKE_CHECK_RC', '0')))\n", encoding="utf-8")
        self.patch_check = mock.patch.object(pp, "SELF_CHECK", self.check)
        self.patch_check.start()
        (self.out / f"{DAY}.facts.json").write_text(json.dumps(facts(), ensure_ascii=False), encoding="utf-8")
        pp.build(facts(), self.out, reply=reply_text())

    def tearDown(self):
        self.patch.stop()
        self.patch_check.stop()

    def sent(self):
        return json.loads((self.tmp / "sent.json").read_text())

    def heads(self):
        return [e["content"] for e in self.sent()["body"]["elements"]
                if e.get("tag") == "markdown" and e["content"].startswith("**") and e["content"].endswith("**")]

    def test_self_check_ok_one_grey_line(self):
        """#159：自检正常，只在底部灰字写一句，不加板块。"""
        pp.send(DAY, self.out)
        self.assertNotIn("**🔧 工程自检**", self.heads())
        self.assertIn("工程自检：正常", self.sent()["body"]["elements"][-1]["content"])

    def test_self_check_problems_after_risks(self):
        with mock.patch.dict(os.environ, {"FAKE_CHECK_OUT": "🔧 工程自检\n\n🔁 安装副本不一致：1 项和仓库 main 对不上\n- hermes/x.py"}):
            pp.send(DAY, self.out)
        h = self.heads()
        self.assertEqual(h.index("**🔧 工程自检**"), h.index("**⚠️ 风险与阻塞**") + 1)
        self.assertEqual(h[-1], "**💡 今日推进**")
        t = json.dumps(self.sent(), ensure_ascii=False)
        self.assertIn("安装副本不一致", t)
        self.assertNotIn("工程自检：正常", t)

    def test_self_check_failed_said_in_card(self):
        with mock.patch.dict(os.environ, {"FAKE_CHECK_RC": "2"}):
            self.assertIsNone(pp.send(DAY, self.out))
        self.assertIn("自检没跑成（退出码 2", json.dumps(self.sent(), ensure_ascii=False))

    def cmds(self):
        if not self.calls.exists():
            return []
        return [" ".join(json.loads(l)[2:4]) for l in self.calls.read_text().splitlines()]

    def deliv(self):
        return json.loads((self.out / f"{DAY}.delivery.json").read_text())

    def test_sends_card_with_report_link_and_reads_back(self):
        self.assertIsNone(pp.send(DAY, self.out))
        self.assertEqual(self.cmds(), ["docs +create", "im +messages-send", "im +messages-mget"])
        sent = json.loads((self.tmp / "sent.json").read_text())
        self.assertEqual(sent["header"]["title"]["content"], "📅 猛鬼兽日报 · 11-08（周日）")
        btn = [e for e in sent["body"]["elements"] if e["tag"] == "button"][0]
        self.assertEqual(btn["behaviors"][0]["default_url"], "https://x.feishu.cn/docx/d1")
        d = self.deliv()
        self.assertTrue(d["sent"] and d["read_back"])
        self.assertEqual(d["message_id"], "om_1")
        send_args = json.loads(self.calls.read_text().splitlines()[1])
        self.assertEqual(send_args[send_args.index("--as") + 1], "bot")
        self.assertEqual(send_args[send_args.index("--chat-id") + 1], pp.CHAT)

    def test_same_day_not_sent_twice(self):
        pp.send(DAY, self.out)
        n = len(self.cmds())
        self.assertIsNone(pp.send(DAY, self.out))
        self.assertEqual(len(self.cmds()), n)

    def test_work_content_refused(self):
        """猛鬼兽只报个人项目：报告里混进工作台账编号就整份拒发，不建文档、不发卡片。"""
        rep = self.out / f"{DAY}.report.md"
        rep.write_text(rep.read_text() + "\n- 🆕 [A1110-1] 看板数据核对\n", encoding="utf-8")
        err = pp.send(DAY, self.out)
        self.assertIn("工作内容", err)
        self.assertEqual(self.cmds(), [])
        self.assertFalse(self.deliv()["sent"])

    def test_work_marks(self):
        for t in ("[A1027-1] 侧边栏", "[D-20261110-1] 选了", "### 二、还开着的事（35 件）", "docx/WZyhdQdM0oMk2jxSlOTcJPVKnQg"):
            self.assertTrue(pp.work_content(t), t)
        self.assertEqual(pp.work_content("牛马之家 #54 合并；驿窗 PR #12"), [])

    def test_doc_fails_card_still_sent(self):
        with mock.patch.dict(os.environ, {"FAKE_DOC_FAIL": "1"}):
            self.assertIsNone(pp.send(DAY, self.out))
        sent = json.loads((self.tmp / "sent.json").read_text())
        self.assertFalse([e for e in sent["body"]["elements"] if e["tag"] == "button"])
        self.assertIn("完整报告未生成", json.dumps(sent, ensure_ascii=False))

    def test_no_build_sends_failure_card(self):
        for f in self.out.glob(f"{DAY}.*"):
            if not f.name.endswith("facts.json"):
                f.unlink()
        self.assertIsNone(pp.send(DAY, self.out))
        self.assertIn("没出成", (self.tmp / "sent.json").read_text())
        self.assertNotIn("docs +create", self.cmds())

    def test_cli_send_quiet_on_success(self):
        from io import StringIO
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            rc = pp.main(["send", "--date", DAY, "--out", str(self.out)])
        self.assertEqual((rc, buf.getvalue()), (0, ""))


class SampleLayoutTests(unittest.TestCase):
    """逐行对齐甲版样例 hermes/samples/pdaily-card-2026-11-08.json 的写法（常浩 11-09 定稿）。"""
    def lines(self, fields):
        f = facts()
        pp.normalize(f, fields)
        c = pp.build_card(f, fields, "灰字")
        return [e["content"] for e in c["body"]["elements"] if e.get("tag") == "markdown"]

    def test_decision_line(self):
        d = dict(card_fields()["decisions"][0], item="夜跑收尾。", recommendation="按授权 72 收尾。",
                 reply="回「按授权 72 收尾」即可")
        body = self.lines(card_fields(decisions=[d, dict(d, type="普通", deadline=None, reply=None,
                                                                    item="PR #102", recommendation="复盘后再定，今天不用回")]))[2]
        self.assertEqual(body.split("\n")[0], "<font color='red'>**限时**</font>｜**猛鬼兽·夜跑收尾**：按授权 72 收尾。"
                                              "最晚 **11-09 06:00**，回「按授权 72 收尾」")
        self.assertEqual(body.split("\n")[1], "<font color='grey'>普通</font>｜**猛鬼兽·PR #102**：复盘后再定，今天不用回")
        self.assertNotIn("。。", body)
        self.assertNotIn("回「回「", body)

    def test_other_lines(self):
        ls = self.lines(card_fields(today=[{"priority": "P1", "item": "牛马之家盲测", "owner_action": "Hermes 协调"},
                                           {"priority": "P0", "item": "夜跑收尾", "owner_action": "你回拍板 1"}]))
        self.assertEqual(ls[4], "1. **牛马之家**：证据口径对齐合并（#43），未发布")
        self.assertEqual(ls[6], "⚪ **管理失真｜牛马之家**：主目录 15 处未提交")
        self.assertEqual(ls[8], "1. **P0 夜跑收尾**：你回拍板 1\n2. **P1 牛马之家盲测**：Hermes 协调")   # P0 在前

    def test_more_all_shown(self):
        """常浩 11-10：风险与阻塞等板块不折叠到完整报告，8 条全在卡片上。"""
        r = card_fields()["risks"][0]
        body = self.lines(card_fields(risks=[r] * 8))[6]
        self.assertEqual(body.count("管理失真｜牛马之家"), 8)
        self.assertNotIn("见完整报告", body)

    def test_overlong_clipped(self):
        f, c = facts(), card_fields(achievements=[{"project": "牛马之家", "text": "很长" * 40}])
        w = pp.normalize(f, c)
        self.assertTrue(any("已截断" in x for x in w))
        self.assertLessEqual(len(c["achievements"][0]["text"]), pp.LIMITS["text"] + 1)

    def test_trace_orders(self):
        f = facts()
        self.assertEqual(pp.trace_line(f, "m", None), "生成：Hermes（m）｜接入：Hermes 派单｜统计日 11-08 04:00 → 11-09 04:00｜取数 11-09 07:52")
        doc = pp.report_doc(f, "正文", "m", None)
        self.assertIn("生成：Hermes（m）｜接入：Hermes 派单｜取数 11-09 07:52｜统计日 11-08 04:00 → 11-09 04:00", doc)


class FallbackMoreTests(unittest.TestCase):
    def test_unknown_github_not_no_progress(self):
        """GitHub 取不到：成果段写未知，不写「昨天没有新成果」（7.1 取数失败写未知）。"""
        f = facts()
        for p in f["projects"]:
            p["yesterday_delta"]["merged_prs"] = None
        card, report = pp.fallback(f, "x")
        t = json.dumps(pp.build_card(f, card, "t"), ensure_ascii=False)
        self.assertIn("未知：4 个项目 GitHub 取不到", t)
        self.assertNotIn("昨天没有新成果", t)
        self.assertIn("未知：4 个项目 GitHub 取不到", report)
        self.assertIn("合并 未知", report)

    def test_latest_pr_by_merged_at(self):
        f = facts()
        f["projects"][0]["yesterday_delta"]["merged_prs"] = [
            {"number": 43, "title": "新的", "merged_at": "2026-11-08T12:45:31Z"},
            {"number": 23, "title": "旧的", "merged_at": "2026-11-08T01:00:00Z"}]
        card, _ = pp.fallback(f, "x")
        self.assertIn("最近：#43 新的", card["achievements"][0]["text"])

    def test_sources_kept(self):
        f = facts()
        f["decisions"][0]["evidence"] = ["run-log 第 3 行"]
        _, report = pp.fallback(f, "x")
        self.assertIn("出处：run-log；run-log 第 3 行", report)
        self.assertIn("出处：本机 git status", report)

    def test_odd_shapes_do_not_crash(self):
        f = facts()
        f["projects"][0]["objective"] = "直接是字符串"
        f["projects"][1]["current_state"] = {}
        f["risks"][0]["category"] = None
        del f["window"]
        card, report = pp.fallback(f, "x")
        self.assertIn("目标 直接是字符串", report)
        self.assertIn("待分类｜", report)
        self.assertNotIn("None", report)
        self.assertIn("窗口未知", json.dumps(pp.build_card(f, card, pp.trace_line(f, None, "x")), ensure_ascii=False))

    def test_degraded_metrics(self):
        card, _ = pp.fallback(facts(), "x")
        r = {k: (v, n) for k, v, n in pp.metrics(facts(), card)}
        self.assertEqual(r["待你拍板"], ("候选 1", "分析降级，类型未判断"))
        self.assertEqual(r["有推进的项目"][0], "未知")


class RobustBuildTests(BuildTests):
    def test_wrong_types_degrade_not_crash(self):
        bad = card_fields(achievements=[{"project": ["牛马之家"], "text": "x"}], progress_projects=[["work-desk"]])
        meta = pp.build(facts(), self.tmp / "out", reply=reply_text(card=bad))
        self.assertIn("核对没过", meta["degraded"])
        self.assertIn("分析降级", self.out("report.md"))

    def test_unexpected_error_degrades(self):
        with mock.patch.object(pp, "check", side_effect=RuntimeError("意外")):
            meta = pp.build(facts(), self.tmp / "out", reply=reply_text())
        self.assertEqual(meta["degraded"], "模型稿处理出错（RuntimeError）")
        self.assertIn("分析降级", self.out("report.md"))

    def test_fallback_error_gives_failure_card(self):
        with mock.patch.object(pp, "fallback", side_effect=KeyError("projects")):
            meta = pp.build(facts(), self.tmp / "out", force_fallback=True)
        self.assertIn("事实版也没出成", meta["degraded"])
        card = json.loads(self.out("card.json"))
        self.assertEqual(card["header"]["subtitle"]["content"], "今天的日报没出成")

    def test_session_id_from_stderr_only(self):
        self.reply.write_text(reply_text(report=report_text(extra="session_id: 假的")), encoding="utf-8")
        meta = pp.build(facts(), self.tmp / "out")
        self.assertEqual(meta["session_id"], "s1")

    def test_session_id_in_reply_ignored(self):
        """模型正文里出现「session_id:」不能当成会话号（-Q 模式会话号只在 stderr）。"""
        self.reply.write_text(reply_text(report=report_text(extra="session_id: 假的")), encoding="utf-8")
        with mock.patch.dict(os.environ, {"FAKE_NOSID": "1"}):
            meta = pp.build(facts(), self.tmp / "out")
        self.assertIsNone(meta["session_id"])

    def test_missing_state_db_not_created(self):
        with mock.patch.object(pp, "HERMES_HOME", self.tmp / "nowhere"):
            self.assertIsNone(pp.session_model("s1"))
        self.assertFalse((self.tmp / "nowhere" / "state.db").exists())


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.roster = self.tmp / "projects.json"
        self.roster.write_text(json.dumps({"projects": []}), encoding="utf-8")
        self.patches = [mock.patch.object(pp, "DAILY", self.tmp),
                        mock.patch.object(pp.pdaily_facts.progress_data, "ROSTER", self.roster),
                        mock.patch.object(pp.pdaily_facts, "write")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_complete_snapshot_reused(self):
        (self.tmp / f"{DAY}.facts.json").write_text(json.dumps(facts()), encoding="utf-8")
        with mock.patch.object(pp.pdaily_facts, "build") as b:
            self.assertEqual(pp.load_facts(None, pp.dt.date(2026, 11, 8))["stat_day"], DAY)
        b.assert_not_called()

    def test_partial_snapshot_refetched(self):
        """统计日没结束时取的快照不完整（样稿 22:35 取的那种），08:00 发报要重取。"""
        early = dict(facts(), fetched_at="2026-11-08T22:35:00+08:00")
        (self.tmp / f"{DAY}.facts.json").write_text(json.dumps(early), encoding="utf-8")
        with mock.patch.object(pp.pdaily_facts, "build", return_value=facts()) as b:
            pp.load_facts(None, pp.dt.date(2026, 11, 8))
        b.assert_called_once()

    def test_fetch_failure_writes_failure_card(self):
        with mock.patch.object(pp.pdaily_facts, "build", side_effect=RuntimeError("gh 挂了")):
            with mock.patch("sys.stdout"):
                rc = pp.main(["build", "--date", DAY, "--out", str(self.tmp / "o")])
        self.assertEqual(rc, 1)
        card = json.loads((self.tmp / "o" / f"{DAY}.card.json").read_text(encoding="utf-8"))
        self.assertIn("取数失败（RuntimeError：gh 挂了）", json.dumps(card, ensure_ascii=False))


class MaterialTests(unittest.TestCase):
    def test_routine_cron_folded(self):
        f = facts()
        f["actions"] += [dict(f["actions"][0], action_id=f"c{i}") for i in range(5)]
        m = pp.material(f)
        self.assertEqual(sum(m["routine_cron"].values()), 6)
        self.assertEqual([a["kind"] for a in m["actions"]], ["merge"])

    def test_cron_with_gap_kept(self):
        f = facts()
        f["actions"][0]["auth_gap"] = "应为 D 档"
        self.assertIn("cron", [a["kind"] for a in pp.material(f)["actions"]])

    def test_unknown_stays_none(self):
        f = facts()
        f["projects"][0]["yesterday_delta"]["merged_prs"] = None
        f["projects"][0]["yesterday_delta"]["commits"] = None
        p = pp.material(f)["projects"][0]
        self.assertIsNone(p["merged_prs"])
        self.assertIsNone(p["commits"])


if __name__ == "__main__":
    unittest.main()
