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
                     project("disk-slim", "磁盘瘦身", commits={})],
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
        "coverage": {"projects": {"projects": 3, "read_ok": 3, "errors": {}},
                     "actions": {"合并官": {"ok": True, "count": 1, "error": None},
                                 "定时任务库": {"ok": True, "count": 1, "error": None}},
                     "action_notes": [], "stale": [], "not_connected": ["Hermes 对话记录：没接"], "failed_parts": []},
        "notes": [],
    }


def card_fields(**over):
    c = {
        "headline": "牛马之家证据口径合并；今天先收尾夜跑",
        "progress_projects": ["work-desk"],
        "decisions": [{"type": "限时", "project": "猛鬼兽", "item": "夜跑收尾", "recommendation": "按授权 72 收尾",
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
        return pp.check(f or facts(), card or card_fields(), report or report_text())

    def test_good_passes(self):
        self.assertEqual(self.errs(), [])

    def test_project_not_in_roster(self):
        e = self.errs(card_fields(achievements=[{"project": "不存在的项目", "text": "做了"}]))
        self.assertTrue(any("不在名册里" in x for x in e), e)

    def test_dependency_pair_names_checked(self):
        ok = card_fields(risks=[{"level": "中", "category": "管理失真", "project": "牛马之家 ↔ 数字分身", "text": "x"}])
        self.assertEqual(self.errs(ok), [])
        bad = card_fields(risks=[{"level": "中", "category": "管理失真", "project": "牛马之家 ↔ 外星项目", "text": "x"}])
        self.assertTrue(any("外星项目" in x for x in self.errs(bad)))

    def test_fabricated_ref(self):
        e = self.errs(report=report_text(extra="另外合并了 #999"))
        self.assertTrue(any("#999" in x for x in e), e)

    def test_deadline_needs_source(self):
        bad = card_fields(decisions=[dict(card_fields()["decisions"][0], deadline="11-10 09:00")])
        self.assertTrue(any("最晚时间" in x for x in self.errs(bad)))

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
        self.assertTrue(any("没有任何增量" in x for x in e), e)

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
        self.assertEqual(r["有推进的项目"][0], "**1** / 3")
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

    def test_more_than_five_decisions(self):
        d = card_fields()["decisions"][0]
        c = self.card(card_fields(decisions=[dict(d, item=f"事{i}", type="普通") for i in range(7)] + [d]))
        t = self.text(c)
        self.assertIn("另 3 条见完整报告", t)
        self.assertLess(t.index("限时"), t.index("事0"))      # 限时排在普通前

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
