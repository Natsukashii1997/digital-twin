"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

report_data.py / report_publish.py 用假 lark-cli（fake_lark.py）和临时目录跑，不联网、不碰真实文档和证据目录。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
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
                    "DT_PROGRESS": str(progress), "DT_REPORT_EVIDENCE": str(self.root / "evidence")}
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


if __name__ == "__main__":
    unittest.main()
