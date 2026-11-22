"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

report_data.py / report_publish.py 用假 lark-cli（fake_lark.py）和临时目录跑，不联网、不碰真实文档和证据目录。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE.parent / "report_data.py"
PUBLISH = HERE.parent / "report_publish.py"
ME = "ou_5ad2ac4359de95dc41b5e89edd68ae6a"
BOT = "ou_12bf9332a4a8c513c2b30777ca9def28"
DAILY, WEEKLY = "daily_doc", "weekly_doc"
INTRO = "# 常浩-日报（新结构·并跑）\n\n说明段。\n"


def msg(chat, ctype, sender, text, at="2026-10-27 10:00", partner=None, name=None, mentions=None):
    m = {"chat_id": chat, "chat_type": ctype, "content": text, "create_time": at, "msg_type": "text",
         "sender": {"id": sender, "name": name or ("常浩" if sender == ME else "某某")}}
    if partner:
        m["chat_partner"] = {"open_id": partner}
    if ctype != "p2p":
        m["chat_name"] = f"群{chat}"
    if mentions:
        m["mentions"] = mentions
    return m


DAILY_OK = """## 📅 2026-10-27（周二）
> 今天一句话：测试

### 一、今天的变化
- 🆕 [A1027-1] 事项｜负责：常浩｜截止：无｜状态：进行中
  依据：私聊 某某 10:00「好」

### 二、还开着的事（1 件）
- [A1027-1] 事项｜其他｜常浩｜进行中｜开 0 天

### 三、卡点
无

### 四、今天的决定
无

### 五、项目进展（自动）
- 没动静：全部

### 六、下个工作日先推（≤3）
1. [A1027-1] 事项 — 新开｜下一步：做

---
来源与覆盖：私聊 1 人｜读取失败：无
"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.seed = self.root / "seed.md"
        self.seed.write_text("### 二、还开着的事（1 件）\n- [A1026-1] 种子事项\n\n### 三、卡点\n无\n", encoding="utf-8")
        progress = self.root / "progress.py"
        progress.write_text("print('# 项目进展（假）')\n", encoding="utf-8")
        self.env = {**os.environ, "FAKE_LARK_DIR": str(self.root), "DT_LARK_CLI": str(HERE / "fake_lark.py"),
                    "DT_DAILY_DOC": DAILY, "DT_WEEKLY_DOC": WEEKLY, "DT_LEDGER_SEED": str(self.seed),
                    "DT_PROGRESS": str(progress), "DT_REPORT_EVIDENCE": str(self.root / "evidence"),
                    "HERMES_HOME": str(self.root / "hermes"),   # 留痕读的 Hermes 会话库换成临时目录
                    "DT_NET_WAIT_SECONDS": "0"}   # 默认不探网络，不联网（#140 / P9 的用例单独打开）
        self.state({})

    def tearDown(self):
        self.tmp.cleanup()

    def state(self, extra, daily=INTRO, weekly=INTRO.replace("日报", "周报")):
        st = {"docs": {DAILY: daily, WEEKLY: weekly}, "im": [], "vc": [], "minutes": [], "agenda": [], "tasks": []}
        st.update(extra)
        (self.root / "state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")

    def run_py(self, script, *args):
        return subprocess.run([sys.executable, "-B", str(script), *args], capture_output=True, text=True, env=self.env)

    def doc(self, token):
        return json.loads((self.root / "state.json").read_text(encoding="utf-8"))["docs"][token]


class DailyData(Base):
    def test_chats_kept_and_skipped(self):
        self.state({"im": [
            msg("oc_bot", "p2p", ME, "和猛鬼兽说的话", partner=BOT),
            msg("oc_self", "p2p", ME, "给自己的备忘", partner=ME),
            msg("oc_p2p", "p2p", "ou_x", "私聊内容", partner="ou_x", name="杭添"),
            msg("oc_g1", "group", "ou_y", "没人理的群消息"),
            msg("oc_g2", "group", ME, "常浩在群里说话"),
            msg("oc_g3", "group", "ou_y", "@常浩 看下", mentions=[{"id": ME}]),
        ]})
        r = self.run_py(DATA, "daily", "--date", "2026-10-27")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("### 私聊 杭添", out)
        self.assertIn("### 常浩发给自己的备忘", out)
        self.assertIn("群「群oc_g2」", out)
        self.assertIn("群「群oc_g3」", out)
        self.assertNotIn("和猛鬼兽说的话", out)
        self.assertNotIn("没人理的群消息", out)
        self.assertIn("群聊 2 个（略过常浩没发言的群 1 个）", out)
        self.assertIn("种子事项", out)   # 文档里没有前一篇，用种子

    def test_tasks_only_mine_and_near(self):
        mine = [{"id": ME, "role": "assignee"}]
        ts = 1000 * int(__import__("datetime").datetime(2026, 10, 30, 18).timestamp())
        far = 1000 * int(__import__("datetime").datetime(2027, 4, 24, 18).timestamp())
        self.state({"im": [msg("oc_p2p", "p2p", "ou_x", "hi", partner="ou_x")], "tasks": [
            {"summary": "近期我的", "members": mine, "due": {"timestamp": str(ts)}, "created_at": "2026-10-01 10:00"},
            {"summary": "远期我的", "members": mine, "due": {"timestamp": str(far)}, "created_at": "2026-04-24 10:00"},
            {"summary": "别人负责", "members": [{"id": "ou_x", "role": "assignee"}, {"id": ME, "role": "follower"}],
             "due": {"timestamp": str(ts)}, "created_at": "2026-10-01 10:00"},
            {"summary": "今天新建没截止", "members": mine, "created_at": "2026-10-27 09:00"},
        ]})
        out = self.run_py(DATA, "daily", "--date", "2026-10-27").stdout
        self.assertIn("近期我的｜截止：2026-10-30", out)
        self.assertIn("今天新建没截止｜截止：无", out)
        self.assertNotIn("远期我的", out)
        self.assertNotIn("别人负责", out)

    def test_lark_called_without_agent_env(self):
        for k in ("HERMES_HOME", "HERMES_QUIET", "HERMES_EXEC_ASK", "_HERMES_GATEWAY", "OPENCLAW_HOME"):
            self.env[k] = "1"   # 网关里有这些变量，lark-cli 见了会换配置目录
        self.state({"im": [msg("oc_p2p", "p2p", "ou_x", "hi", partner="ou_x")]})
        self.assertEqual(self.run_py(DATA, "daily", "--date", "2026-10-27").returncode, 0)
        calls = [json.loads(x) for x in (self.root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(calls)
        self.assertTrue(all(c["agent_env"] == [] for c in calls), calls[0])

    def test_prior_daily_ledger_used(self):
        daily = INTRO + "\n## 📅 2026-10-26（周一）\n\n### 二、还开着的事（1 件）\n- [A1026-9] 文档里的事\n\n### 三、卡点\n无\n"
        self.state({"im": [msg("oc_p2p", "p2p", "ou_x", "hi", partner="ou_x")]}, daily=daily)
        out = self.run_py(DATA, "daily", "--date", "2026-10-27").stdout
        self.assertIn("依据并跑文档 2026-10-26", out)
        self.assertIn("文档里的事", out)
        self.assertNotIn("种子事项", out)

    def test_skip_when_already_written(self):
        self.state({}, daily=INTRO + "\n## 📅 2026-10-27（周二）\n\n正文\n")
        r = self.run_py(DATA, "daily", "--date", "2026-10-27")
        self.assertTrue(r.stdout.startswith("SKIP："), r.stdout)

    def test_weekend_without_activity_skips(self):
        self.state({"im": [msg("oc_g1", "group", "ou_y", "周末群消息", at="2026-10-25 10:00")]})
        r = self.run_py(DATA, "daily", "--date", "2026-10-25")
        self.assertTrue(r.stdout.startswith("SKIP："), r.stdout)

    def test_lists_are_read_past_the_first_round(self):
        """#312：lark-cli 到了页数上限（或只回第一页）不报错，只留 has_more=true。原来只取第一轮，后面的消息、会议、任务悄悄没有。"""
        mine = [{"id": ME, "role": "assignee"}]
        self.state({"page": 2,
                    "im": [msg(f"oc_p{i}", "p2p", f"ou_{i}", f"第{i}条私聊", partner=f"ou_{i}", name=f"同事{i}") for i in range(5)],
                    "vc": [{"display_info": f"会议{i}\n09:00"} for i in range(5)],
                    "tasks": [{"summary": f"任务{i}", "members": mine, "created_at": "2026-10-27 09:00"} for i in range(5)]})
        r = self.run_py(DATA, "daily", "--date", "2026-10-27")
        self.assertEqual(r.returncode, 0, r.stderr)
        for i in range(5):
            self.assertIn(f"第{i}条私聊", r.stdout)
            self.assertIn(f"- 会议：会议{i}", r.stdout)
            self.assertIn(f"任务{i}｜截止：无", r.stdout)
        self.assertIn("会议（5 场）", r.stdout)
        calls = [json.loads(ln)["args"] for ln in (self.root / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        search = [a for a in calls if "+messages-search" in a]
        self.assertEqual([a[a.index("--page-token") + 1] if "--page-token" in a else None for a in search], [None, "2", "4"])

    def test_list_that_cannot_be_read_to_the_end_fails(self):
        self.state({"page": 2, "lose_token": True,
                    "im": [msg(f"oc_p{i}", "p2p", f"ou_{i}", f"第{i}条私聊", partner=f"ou_{i}") for i in range(5)]})
        r = self.run_py(DATA, "daily", "--date", "2026-10-27")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("取数失败："), r.stdout)
        self.assertIn("读当天消息：还有下一页，但飞书没给翻页标记，没读全", r.stdout)
        self.assertNotIn("第0条私聊", r.stdout)   # 不拿半截材料出稿

    def test_task_list_failure_leaves_no_partial_material(self):
        """PR #335 审查：任务在开始输出之前就取完，读不全时不留前半截材料。"""
        mine = [{"id": ME, "role": "assignee"}]
        self.state({"page": 2, "lose_token": "task +get-related-tasks",
                    "im": [msg("oc_p0", "p2p", "ou_0", "私聊内容", partner="ou_0")],
                    "tasks": [{"summary": f"任务{i}", "members": mine, "created_at": "2026-10-27 09:00"} for i in range(5)]})
        r = self.run_py(DATA, "daily", "--date", "2026-10-27")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("取数失败：读任务：还有下一页，但飞书没给翻页标记，没读全"), r.stdout)
        self.assertNotIn("日报材料", r.stdout)

    def test_lark_failure_exit_1(self):
        self.env["DT_LARK_CLI"] = "false"
        r = self.run_py(DATA, "daily", "--date", "2026-10-27")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("取数失败："), r.stdout)


class WeeklyData(Base):
    def test_week_collects_dailies(self):
        daily = (INTRO + "\n## 📅 2026-11-02（周一）\n\n下周的\n\n## 📅 2026-10-27（周二）\n\n本周二\n"
                 "\n## 📅 2026-10-26（周一）\n\n本周一\n\n## 📅 2026-10-23（周五）\n\n上周五\n")
        self.state({}, daily=daily)
        out = self.run_py(DATA, "weekly", "--week-start", "2026-10-26").stdout
        self.assertIn("第44周 2026.44（2026.10.26 - 11.01）", out)
        self.assertIn("本周日报（2 篇", out)
        self.assertLess(out.index("本周一"), out.index("本周二"))
        self.assertNotIn("上周五", out)
        self.assertNotIn("下周的", out)
        self.assertIn("第一篇", out)


class Publish(Base):
    def write(self, text):
        p = self.root / "draft.md"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_publish_inserts_on_top_and_saves_evidence(self):
        old = INTRO + "\n## 📅 2026-10-26（周一）\n\n旧的\n"
        self.state({}, daily=old)
        r = self.run_py(PUBLISH, "daily", self.write(DAILY_OK))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout)["ok"])
        doc = self.doc(DAILY)
        self.assertLess(doc.index("2026-10-27"), doc.index("2026-10-26"))
        self.assertTrue(any((self.root / "evidence").iterdir()))

    def test_publish_writes_trace_line_under_title(self):
        hermes = self.root / "hermes"
        hermes.mkdir()
        con = sqlite3.connect(hermes / "state.db")
        con.execute("create table sessions (id text, source text, title text, started_at real)")
        con.execute("create table session_model_usage (session_id text, model text, billing_provider text, task text, "
                    "first_seen real)")
        t = time.time()
        con.executemany("insert into sessions values (?,?,?,?)", [
            ("cron_d", "cron", "日报（新结构·并跑） · Nov 07 07:34", t - 120),
            ("cron_w", "cron", "周报（新结构·并跑） · Nov 07 07:45", t - 60)])
        con.executemany("insert into session_model_usage values (?,?,?,?,?)", [
            ("cron_d", "gpt-main", "openai-codex", "", t - 100), ("cron_d", "deepseek-flash", "deepseek", "", t - 90),
            ("cron_d", "gpt-aux", "openai-codex", "compression", t - 80), ("cron_w", "gpt-week", "openai-codex", "", t - 50)])
        con.commit()
        con.close()
        r = self.run_py(PUBLISH, "daily", self.write(DAILY_OK))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = self.doc(DAILY)
        self.assertRegex(doc, r"## 📅 2026-10-27（周二）\n\n生成：Hermes（gpt-main · openai-codex、deepseek-flash · deepseek）"
                              r"｜接入：Hermes 派单｜\d{4}-\d{2}-\d{2} \d{2}:\d{2}\n")
        self.assertNotIn("gpt-aux", doc)
        self.assertNotIn("gpt-week", doc)

    def test_publish_trace_without_cron_session(self):
        r = self.run_py(PUBLISH, "daily", self.write(DAILY_OK))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("生成：Hermes（模型查不到：不是这份报告的定时任务写的）｜接入：Hermes 派单｜", self.doc(DAILY))

    def test_project_label_gets_blank_lines(self):
        text = DAILY_OK.replace("### 一、今天的变化\n", "### 一、今天的变化\n**中台**\n").replace(
            "依据：私聊 某某 10:00「好」\n", "依据：私聊 某某 10:00「好」\n**管培生**\n- ⏩ [A1021-1] 推进\n")
        r = self.run_py(PUBLISH, "daily", self.write(text))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("「好」\n\n**管培生**\n\n- ⏩", self.doc(DAILY))

    def test_publish_refuses_duplicate(self):
        self.state({}, daily=INTRO + "\n" + DAILY_OK)
        r = self.run_py(PUBLISH, "daily", self.write(DAILY_OK))
        self.assertEqual(r.returncode, 1)
        self.assertIn("已经有", r.stdout)

    def test_publish_rejects_bad_drafts(self):
        cases = {
            "星期": DAILY_OK.replace("（周二）", "（周三）"),
            "缺段": DAILY_OK.replace("### 四、今天的决定\n无\n", ""),
            "表格": DAILY_OK.replace("无\n\n### 四", "| a | b |\n\n### 四"),
            "标题格式": DAILY_OK.replace("## 📅 ", "## "),
        }
        for why, text in cases.items():
            with self.subTest(why):
                r = self.run_py(PUBLISH, "daily", self.write(text))
                self.assertEqual(r.returncode, 1, why)
                self.assertFalse(json.loads(r.stdout)["ok"])
        calls = (self.root / "calls.jsonl").read_text(encoding="utf-8") if (self.root / "calls.jsonl").exists() else ""
        self.assertNotIn("+update", calls)

    def test_weekly_title_week_number_checked(self):
        bad = "## 第45周 2026.45（2026.10.26 - 11.01）\n" + "".join(f"### {n}、x\n无\n" for n in "一二三四五六")
        r = self.run_py(PUBLISH, "weekly", self.write(bad))
        self.assertEqual(r.returncode, 1)
        self.assertIn("周号", r.stdout)


class Status403(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(403)
        self.end_headers()

    def log_message(self, *a):
        pass


class NetGateEndToEnd(Base):
    """真起子进程：主模型或飞书不通时明确失败（P9 起不再放行交给回退）；回 403 算连通，照常出材料。"""
    def setUp(self):
        super().setUp()
        self.state({"im": [msg("oc_p2p", "p2p", "ou_x", "hi", partner="ou_x")]})
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            self.env.pop(k, None)
        self.env.update({"no_proxy": "*", "NO_PROXY": "*", "DT_NETWAIT_LOG": str(self.root / "netwait.jsonl")})   # 本机端口直连

    def log(self):
        p = self.root / "netwait.jsonl"
        return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def test_unreachable_fails_with_reason(self):
        self.env.update({"DT_NET_WAIT_SECONDS": "1", "DT_MODEL_PROBE_URL": "http://127.0.0.1:9/",
                         "DT_PROBE_URL_FEISHU": "http://127.0.0.1:9/"})
        r = self.run_py(DATA, "daily", "--date", "2026-10-27")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(r.stdout.startswith("取数失败：网络 / 代理"), r.stdout)
        rec, = self.log()
        self.assertEqual((rec["kind"], rec["ok"], rec["attempts"]), ("report-daily", False, 1))
        self.assertIn("refused", rec["error"].lower())

    def test_http_403_counts_as_reachable(self):
        srv = HTTPServer(("127.0.0.1", 0), Status403)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        url = f"http://127.0.0.1:{srv.server_port}/"
        self.env.update({"DT_NET_WAIT_SECONDS": "60", "DT_MODEL_PROBE_URL": url, "DT_PROBE_URL_FEISHU": url})
        self.state({}, daily=INTRO + "\n## 📅 2026-10-27（周二）\n\n本周二\n")
        t = time.monotonic()
        r = self.run_py(DATA, "weekly", "--week-start", "2026-10-26")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertLess(time.monotonic() - t, 14)   # 一次就通，没睡 15 秒
        rec, = self.log()
        self.assertEqual((rec["kind"], rec["ok"], rec["attempts"]), ("report-weekly", True, 1))


if __name__ == "__main__":
    unittest.main()
