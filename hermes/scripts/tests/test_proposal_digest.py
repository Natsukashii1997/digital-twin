"""每周提案收口（#61）自测：回复解析、四类来源汇总、卡片发送（假 lark-cli）和写回；全部用临时目录，不碰真实提案和飞书。
再犯与待提升（#216）用假 node 回放 agent-kit 每周汇总 --json 的输出，不跑真的 agent-kit。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import proposal_digest as pd  # noqa: E402

SCRIPT = HERE.parent / "proposal_digest.py"
CHAT = "oc_testchat000001"
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(args, ensure_ascii=False) + "\n")
if (root / "fail").exists():
    print(json.dumps({"ok": False, "error": {"message": "bot 不在会话里"}}, ensure_ascii=False)); sys.exit(1)
print(json.dumps({"ok": True, "data": {"message_id": "om_digest_1"}}))
'''

# 假 node：只认「<脚本> --json」，回放 FAKE_DIR/weekly.json；有 weekly-fail 就像脚本出错那样退出 1
FAKE_NODE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ["FAKE_DIR"])
with open(root / "node-calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
if (root / "weekly-fail").exists():
    sys.stderr.write("每周汇总中断：ENOENT: no such file\n"); sys.exit(1)
sys.stdout.write((root / "weekly.json").read_text(encoding="utf-8"))
'''

EMPTY_WEEKLY = {"schema": 1, "day": "2026-11-09", "scanned": {"projects": 0, "files": 0}, "recurrences": [], "promotions": [],
                "noUpgradeLine": 0}


def promo(project, lid, proposed, upgrade):
    return {"project": project, "file": "docs/LESSONS.md", "id": lid, "title": f"{project} 的坑 {lid}", "proposed": proposed,
            "upgrade": upgrade}


# 假项目坑记录里四种条目经 agent-kit weekly-review.mjs --json 汇总后的样子（字段照 agent-kit#80）：
# 再犯 3 条（G-09 再犯次数最多但在〔机检〕层，不是升级首选，要排在两条〔文字〕〔提醒〕之后）；
# 等批 alpha L-01 再犯行所在的条目只写「未升级」、L-03 只写未升级（都不列），L-02 和 delta 的 6 条写了提议提升；
# 没有升级行的只体现在 noUpgradeLine 里，本来就不在 promotions。合计可列 3 + 7 = 10 条，超出 7 条。
WEEKLY = {"schema": 1, "day": "2026-11-09", "scanned": {"projects": 4, "files": 4}, "noUpgradeLine": 1,
          "recurrences": [
              {"id": "G-04", "title": "并行会话共用一个工作区", "level": "文字", "status": "已升级", "prime": True, "count": 2,
               "projects": ["alpha", "beta"]},
              {"id": "G-11", "title": "截图验证不等页面画完", "level": "提醒", "status": "未升级", "prime": True, "count": 1,
               "projects": ["gamma"]},
              {"id": "G-09", "title": "强调色写死", "level": "机检", "status": "已升级", "prime": False, "count": 5,
               "projects": ["beta", "gamma"]}],
          "promotions": [promo("alpha", "L-01", False, "未升级。"),
                         promo("alpha", "L-02", True, "未升级。换个项目也成立，提议提升为全局 G-xx。"),
                         promo("alpha", "L-03", False, "未升级。"),
                         *[promo("delta", f"L-1{k}", True, "未升级，可提议提升为全局坑。") for k in range(1, 7)]]}

RETRO = """# 会话复盘提案：2026-10-30 至 2026-11-05

- 结论：A 2 条、B 1 条

## A 规则提案

### A1 给参考物时只借鉴点名的维度 · 部分已有 · 多次 / 跨会话
- 落点：CLAUDE.md

### A2 只让审核时不开工 · 单次
- 结论：否决（2026-11-01，旧结论）

## B 坑提案

### B1 Edit 未先 Read 会连续失败 · 新增
- 落点：LESSONS

## 已覆盖（不出提案）

### X9 这里不是提案
"""

NIUMA = """# 每周整理 2026-10-30

## 上次未决

- W2026-10-23-6　L-09 标「未升级」，但做法已经落地。**仍成立**
- W2026-10-23-8　CURRENT.md 超长。新范围见本次 W2026-10-30-3
- W2026-10-23-9　L-07 补上克隆的做法。**仍成立**

## A. 漏记的坑

### W2026-10-30-1
- **建议**：记一条新坑 L-17：bash 3.2 把全角标点并进变量名。后面还有话
- **证据**：x

## E. 归档

### W2026-10-30-3
- **建议**：CURRENT.md 归档较早的节。本条取代 W2026-10-23-8。

### W2026-10-30-4
- **建议**：已经采纳过的一条
"""

DECISIONS = """# 每周整理提案的决定

- W2026-10-23-6　待定　2026-10-23　用户未定
- W2026-10-30-4　采纳　2026-11-01　abc123
"""

LESSONS = """# 全局 LESSONS

## G-01 验证证据放 /tmp（2026-10-24 提升）
- 升级：〔提醒〕未升级。

## G-02 已经升级过的（2026-10-24 提升）
- 升级：〔机检〕写进 check-rules。

## G-03 只在一个项目碰到（2026-10-24 提升）
- 升级：未升级。

## G-04 常浩确认不升级的（2026-10-24 提升）
- 升级：〔提醒〕不升级（常浩确认）。
"""


class ParseUpgrade(unittest.TestCase):
    """LESSONS「- 升级：」行：新旧写法、五种层级、四种状态（agent-kit#57，#153）。"""

    def test_cases(self):
        cases = [
            ("未升级。", (None, "未升级")),
            ("不升级（2026-11-07，#15，常浩确认）：个例", (None, "不升级")),
            ("写进 check-rules。", (None, "已升级")),
            ("〔提醒〕未升级。可选去处：体检", ("提醒", "未升级")),
            ("〔提醒〕不升级（2026-11-07，#15，常浩确认）", ("提醒", "不升级")),
            ("〔体检〕部分升级（2026-11-07，#15）：缺一项", ("体检", "部分升级")),
            ("〔机检〕已升级：起步骨架", ("机检", "已升级")),
            ("〔机检〕机器拦截，hook 当场拒绝", ("机检", "已升级")),
            ("〔文字〕已升级（2026-11-07，#15）：全局指令一句", ("文字", "已升级")),
            ("〔结构〕删掉旧接口", ("结构", "已升级")),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(pd.parse_upgrade(text), want)


class ParseReply(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(pd.parse_reply("采纳 1 3，否 2", 5), {1: "采纳", 3: "采纳", 2: "否决"})

    def test_variants(self):
        self.assertEqual(pd.parse_reply("采纳1-3、5；否决4", 5), {1: "采纳", 2: "采纳", 3: "采纳", 5: "采纳", 4: "否决"})
        self.assertEqual(pd.parse_reply("不采纳 2", 3), {2: "否决"})
        self.assertEqual(pd.parse_reply("采纳 第1条，延期 其余", 3), {1: "采纳", 2: "待定", 3: "待定"})
        self.assertEqual(pd.parse_reply("同意 2 到 3", 3), {2: "采纳", 3: "采纳"})

    def test_errors(self):
        bad = {"1 3 采纳": "先写", "采纳 9": "没有第 9 条", "采纳 1 否 1": "既写了", "采纳 否 2": "后面没写编号",
               "采纳 1 好的": "看不懂", "": "没写任何编号", "采纳 3-1": "写反", "否 2 延期": "后面没写编号"}
        for text, word in bad.items():
            with self.assertRaises(pd.ReplyError, msg=text) as cm:
                pd.parse_reply(text, 5)
            self.assertIn(word, str(cm.exception), text)

    def test_same_verb_twice_ok(self):
        self.assertEqual(pd.parse_reply("采纳 1，采纳 1", 2), {1: "采纳"})

    def test_follow_recommendation(self):
        recs = {1: "采纳", 2: "采纳", 3: "否决"}
        for text in ("按推荐", "按照你的推荐完成", "都按推荐。", "全部按推荐来"):
            self.assertEqual(pd.parse_reply(text, 4, recs), recs, text)
        self.assertEqual(pd.parse_reply("按推荐，2 改否", 4, recs), {1: "采纳", 2: "否决", 3: "否决"})
        self.assertEqual(pd.parse_reply("按推荐，2号改成延期，采纳 4", 4, recs), {1: "采纳", 2: "待定", 3: "否决", 4: "采纳"})
        self.assertEqual(pd.parse_reply("按推荐，延期 其余", 4, recs), {1: "采纳", 2: "采纳", 3: "否决", 4: "待定"})
        with self.assertRaises(pd.ReplyError) as cm:
            pd.parse_reply("按推荐", 4, {})
        self.assertIn("没有推荐", str(cm.exception))
        with self.assertRaises(pd.ReplyError):
            pd.parse_reply("按推荐，9 改否", 4, recs)


class Digest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-digest-"))
        t = self.tmp
        (t / "retro/2026-10-30_2026-11-06").mkdir(parents=True)
        self.retro = t / "retro/2026-10-30_2026-11-06/提案.md"
        self.retro.write_text(RETRO, encoding="utf-8")
        (t / "niuma").mkdir()
        (t / "niuma/2026-10-23.md").write_text("# 旧一期\n\n### W2026-10-23-99\n- **建议**：旧的\n", encoding="utf-8")
        (t / "niuma/2026-10-23-试跑1.md").write_text("### W2026-10-23-98\n", encoding="utf-8")
        (t / "niuma/2026-10-30.md").write_text(NIUMA, encoding="utf-8")
        self.decisions = t / "niuma/decisions.md"
        self.decisions.write_text(DECISIONS, encoding="utf-8")
        (t / "kit").mkdir()
        (t / "LESSONS.md").write_text(LESSONS, encoding="utf-8")
        work = t / "work"
        rows = [("2026-11-06T01:00:00Z", work / "app", ["G-01", "G-02", "G-03", "G-04"]),
                ("2026-11-06T02:00:00Z", work / "app/.claude/worktrees/x", ["G-01"]),
                ("2026-11-05T02:00:00Z", work / "kit", ["G-01", "G-02", "G-04"]),
                ("2026-10-01T02:00:00Z", work / "kit", ["G-03"]),            # 超过 7 天，不算
                ("2026-11-06T03:00:00Z", t / "scratch", ["G-03"])]            # 不在名册，不算
        (t / "kit/lessons-hits.jsonl").write_text(
            "\n".join(json.dumps({"time": a, "cwd": str(c), "ids": i}) for a, c, i in rows) + "\nnot json\n", encoding="utf-8")
        (t / "projects.json").write_text(json.dumps({"projects": [
            {"id": "app", "path": str(work / "app")}, {"id": "kit", "path": str(work / "kit")}]}), encoding="utf-8")
        (t / "fake").mkdir()
        (t / "fake/lark-cli").write_text(FAKE, encoding="utf-8")
        (t / "fake/lark-cli").chmod(0o755)
        (t / "fake/node").write_text(FAKE_NODE, encoding="utf-8")
        (t / "fake/node").chmod(0o755)
        self.weekly(EMPTY_WEEKLY)
        (t / "agent-kit/tools").mkdir(parents=True)
        (t / "agent-kit/tools/weekly-review.mjs").write_text("// 替身，由假 node 回放\n", encoding="utf-8")
        self.shadow = t / "shadow"
        self.state = t / "prop/digest.json"

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def weekly(self, data):
        (self.tmp / "fake/weekly.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def env(self, now="2026-11-09T10:00:00+08:00", chat=CHAT, lessons="2"):
        t = self.tmp
        env = {"DT_RETRO_ROOT": str(t / "retro"), "DT_NIUMA_REVIEW": str(t / "niuma"),
               "DT_LESSONS": str(t / "LESSONS.md"), "AGENT_KIT_STATE_DIR": str(t / "kit"), "DT_ROSTER": str(t / "projects.json"),
               "DT_PROPOSAL_DIR": str(t / "prop"), "DT_SHADOW_DIR": str(self.shadow), "DT_LARK_CLI": str(t / "fake/lark-cli"),
               "FAKE_DIR": str(t / "fake"), "DT_NOW": now, "FEISHU_HOME_CHANNEL": chat,
               "DT_HERMES_ENV": str(t / "hermes.env"), "DT_WEEKLY_REVIEW": str(t / "agent-kit/tools/weekly-review.mjs"),
               "PATH": str(t / "fake") + os.pathsep + os.environ.get("PATH", "")}
        if lessons is not None:
            env["DT_LESSONS_MIN_PROJECTS"] = lessons
        return env

    def run_cli(self, *args, now="2026-11-09T10:00:00+08:00", chat=CHAT, lessons="2"):
        env = {k: v for k, v in os.environ.items() if k != "DT_LESSONS_MIN_PROJECTS"}
        env.update(self.env(now, chat, lessons))
        return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)

    def calls(self):
        f = self.tmp / "fake/calls.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def card_text(self, out):
        return json.loads(out)["content"]["body"]["elements"][0]["content"]

    def test_collect_three_sources(self):
        r = self.run_cli("send", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.card_text(r.stdout)
        ids = [line.split(". ", 1)[1].replace("⏰ ", "").split(" ")[0] for line in text.splitlines() if line[:1].isdigit()]
        # 复盘：A2 已有结论、X9 在「已覆盖」里；牛马：-23-8 被取代、-30-4 已采纳、-23-6 待定照列、只读最新一期；LESSONS：只有 G-01（G-02 已升级、G-04 常浩定了不升级，命中够数也不进）
        self.assertEqual(ids, ["A1", "B1", "W2026-10-23-6", "W2026-10-23-9", "W2026-10-30-1", "W2026-10-30-3", "G-01"])
        self.assertIn("1. A1 给参考物时只借鉴点名的维度 · 部分已有 · 多次 / 跨会话", text)
        self.assertIn("W2026-10-30-1 记一条新坑 L-17：bash 3.2 把全角标点并进变量名\n", text)
        self.assertIn("⏰ W2026-10-23-6", text)                       # 超过 7 天
        self.assertNotIn("⏰ A1", text)
        self.assertIn("G-01 验证证据放 /tmp（2 个项目：app、kit）", text)
        self.assertEqual(self.calls(), [])                            # dry-run 不发
        self.assertFalse(self.state.exists())

    def test_lessons_off_by_default(self):
        text = self.card_text(self.run_cli("send", "--dry-run", lessons=None).stdout)
        self.assertNotIn("G-01", text)
        self.assertNotIn("LESSONS", text)
        self.assertIn("W2026-10-30-3", text)

    def test_send_once_per_day_then_apply(self):
        r = self.run_cli()                                             # 定时任务不带参数调用 = send
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        (call,) = self.calls()
        self.assertEqual(call[2:4], ["im", "+messages-send"])
        self.assertEqual(call[call.index("--chat-id") + 1], CHAT)
        self.assertEqual(call[call.index("--idempotency-key") + 1], "proposal-digest-2026-11-09")
        card = json.loads(call[call.index("--content") + 1])
        self.assertIn("第 1 期（11/09）", card["header"]["title"]["content"])
        state = json.loads(self.state.read_text())
        self.assertEqual([(i["n"], i["id"]) for i in state["issues"][0]["items"]][:2], [(1, "A1"), (2, "B1")])
        self.assertEqual(self.run_cli("send").returncode, 0)
        self.assertEqual(len(self.calls()), 1)                         # 同一天不重发

        r = self.run_cli("apply", "采纳 1 6，否 2，延期 3 7", now="2026-11-09T21:00:00+08:00")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("已记下第 1 期：采纳 1（A1）、6（W2026-10-30-3）", r.stdout)
        self.assertIn("没提到的 2 条下周一再列：4、5", r.stdout)
        retro = self.retro.read_text(encoding="utf-8")
        self.assertIn("### A1 给参考物时只借鉴点名的维度 · 部分已有 · 多次 / 跨会话\n- 结论：采纳（2026-11-09，常浩飞书回复（提案收口第 1 期））\n- 落点", retro)
        self.assertIn("### B1 Edit 未先 Read 会连续失败 · 新增\n- 结论：否决（2026-11-09", retro)
        dec = self.decisions.read_text(encoding="utf-8").splitlines()
        self.assertEqual(dec[-3:], ["- W2026-10-23-6　待定　2026-11-09　常浩飞书回复（提案收口第 1 期）",
                                    "- W2026-10-30-3　采纳　2026-11-09　常浩飞书回复（提案收口第 1 期）",
                                    "- W2026-10-23-8　采纳　2026-11-09　随 W2026-10-30-3（已被它取代）"])
        ledger = (self.shadow / "lessons-decisions.md").read_text(encoding="utf-8")
        self.assertTrue(ledger.startswith("# LESSONS 升级候选的结论"))
        self.assertIn("- G-01　待定　2026-11-09　常浩飞书回复", ledger)
        self.assertEqual(json.loads(self.state.read_text())["issues"][0]["answers"][0]["reply"], "采纳 1 6，否 2，延期 3 7")

        # 改主意：复盘条目替换结论行，不叠加
        self.assertEqual(self.run_cli("apply", "延期 1").returncode, 0)
        retro = self.retro.read_text(encoding="utf-8")
        self.assertEqual(retro.count("- 结论："), 4)                   # 文件头摘要、A1、A2 旧结论、B1
        self.assertIn("### A1 给参考物时只借鉴点名的维度 · 部分已有 · 多次 / 跨会话\n- 结论：延期（2026-11-09", retro)
        self.assertIn("- 结论：A 2 条、B 1 条", retro)                 # 文件头摘要不动

        # 下周：已定的不再列，延期的再列，期号加一
        r = self.run_cli("send", "--dry-run", now="2026-11-16T10:00:00+08:00")
        text = self.card_text(r.stdout)
        self.assertIn("第 2 期", r.stdout)
        self.assertIn("A1", text)                                      # 刚改成延期
        self.assertNotIn("B1", text)
        self.assertNotIn("W2026-10-30-3", text)
        self.assertIn("W2026-10-23-6", text)

    def recommend(self, data):
        f = self.tmp / "rec.json"
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return str(f)

    def test_draft_for_hermes(self):
        r = self.run_cli("draft")
        d = json.loads(r.stdout)
        self.assertEqual([i["id"] for i in d["items"]][:3], ["A1", "B1", "W2026-10-23-6"])
        a1 = d["items"][0]
        self.assertEqual(a1["key"], "retro:2026-10-30_2026-11-06:A1")
        self.assertIn("- 落点：CLAUDE.md", a1["body"])
        self.assertIn("本条取代 W2026-10-23-8", d["items"][5]["body"])
        env_script = SCRIPT.with_name("proposal_digest_draft.py")          # 定时任务用的无参数入口
        r2 = subprocess.run([sys.executable, "-B", str(env_script)], capture_output=True, text=True,
                            env={**os.environ, **self.env()}, timeout=60)
        self.assertEqual(json.loads(r2.stdout), d)
        self.assertEqual(self.calls(), [])
        for dd in ("retro", "niuma"):
            subprocess.run(["rm", "-rf", str(self.tmp / dd)], check=True)
        self.assertEqual(self.run_cli("draft", lessons=None).stdout, "")

    def test_send_with_recommendation_then_follow(self):
        keys = json.loads(self.run_cli("draft").stdout)["items"]
        rec = self.recommend({keys[0]["key"]: {"verdict": "采纳", "why": "4 个会话 3 个项目都踩过"},
                              keys[1]["key"]: {"verdict": "否", "why": "工具层小坑，成本低"},
                              keys[5]["key"]: {"verdict": "延期", "why": "等 -1 先定"}})
        r = self.run_cli("send", "--recommend", rec)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        card = json.loads(self.calls()[0][self.calls()[0].index("--content") + 1])
        text = card["body"]["elements"][0]["content"]
        self.assertIn("1. A1 给参考物时只借鉴点名的维度 · 部分已有 · 多次 / 跨会话\n　→ 推荐**采纳**：4 个会话 3 个项目都踩过", text)
        self.assertIn("　→ 推荐**否**：工具层小坑，成本低", text)
        self.assertIn("直接回「按推荐」", text)
        self.assertIn("长按这张卡片选「回复」", text)
        items = json.loads(self.state.read_text())["issues"][0]["items"]
        self.assertEqual([i.get("rec") for i in items[:2]], ["采纳", "否决"])
        self.assertTrue(all("body" not in i for i in items))
        self.assertIn("→ 推荐采纳：4 个会话", self.run_cli("list").stdout)

        r = self.run_cli("apply", "按照你的推荐完成")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("已记下第 1 期：采纳 1（A1）；否 2（B1）；延期 6（W2026-10-30-3）。", r.stdout)
        self.assertIn("没提到的 4 条下周一再列：3、4、5、7", r.stdout)
        self.assertIn("- 结论：采纳（", self.retro.read_text(encoding="utf-8"))
        self.assertTrue(self.decisions.read_text(encoding="utf-8").rstrip().endswith("- W2026-10-23-8　待定　2026-11-09　随 W2026-10-30-3（已被它取代）"))
        self.assertIn("已回复 1 次，最近一次：按照你的推荐完成", self.run_cli("list").stdout)

    def test_bad_recommend_file_sends_nothing(self):
        keys = json.loads(self.run_cli("draft").stdout)["items"]
        bad = [{"retro:不存在:A9": {"verdict": "采纳", "why": "x"}},
               {keys[0]["key"]: {"verdict": "好", "why": "x"}},
               {keys[0]["key"]: {"verdict": "采纳"}}, ["不是对象"]]
        for data in bad:
            r = self.run_cli("send", "--recommend", self.recommend(data))
            self.assertEqual(r.returncode, 1, data)
            self.assertIn("推荐文件不对", r.stdout)
        r = self.run_cli("send", "--recommend", str(self.tmp / "没有.json"))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.state.exists())

    def test_follow_without_recommendation_rejected(self):
        self.run_cli("send")
        r = self.run_cli("apply", "按推荐")
        self.assertEqual(r.returncode, 1)
        self.assertIn("没有推荐", r.stdout)

    def test_bad_reply_writes_nothing(self):
        self.run_cli("send")
        before = (self.retro.read_text(encoding="utf-8"), self.decisions.read_text(encoding="utf-8"))
        for reply in ("采纳 1 99", "好的都行"):
            r = self.run_cli("apply", reply)
            self.assertEqual(r.returncode, 1)
            self.assertIn("一条都没写", r.stdout)
        r = self.run_cli("apply", "采纳 1", "--dry-run")
        self.assertIn("将记下", r.stdout)
        self.assertEqual((self.retro.read_text(encoding="utf-8"), self.decisions.read_text(encoding="utf-8")), before)
        self.assertFalse((self.shadow / "lessons-decisions.md").exists())

    def test_apply_before_any_issue(self):
        r = self.run_cli("apply", "采纳 1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("还没发过", r.stdout)

    def test_send_failures(self):
        r = self.run_cli("send", chat="")
        self.assertEqual(r.returncode, 1)
        self.assertIn("FEISHU_HOME_CHANNEL", r.stdout)
        (self.tmp / "hermes.env").write_text('FEISHU_APP_SECRET=xxx\nFEISHU_HOME_CHANNEL="oc_fromenvfile01"\n', encoding="utf-8")
        r = self.run_cli("send", "--dry-run", chat="")                  # dry-run 不读
        self.assertEqual(r.returncode, 0)
        subprocess.run(["rm", "-f", str(self.state)], check=False)
        (self.tmp / "fake/fail").write_text("1")
        self.run_cli("send", chat="")                                     # 环境变量没有，从 .env 只读这一项
        self.assertEqual(self.calls()[-1][self.calls()[-1].index("--chat-id") + 1], "oc_fromenvfile01")
        (self.tmp / "fake/fail").unlink()
        (self.tmp / "hermes.env").unlink()
        (self.tmp / "fake/calls.jsonl").unlink()
        (self.tmp / "fake/fail").write_text("1")
        r = self.run_cli("send")
        self.assertEqual(r.returncode, 1)
        self.assertIn("没发出", r.stdout)
        self.assertFalse(self.state.exists())                          # 没发出就不记期号

    def test_nothing_pending_sends_nothing(self):
        for d in ("retro", "niuma"):
            subprocess.run(["rm", "-rf", str(self.tmp / d)], check=True)
        (self.tmp / "kit/lessons-hits.jsonl").write_text("", encoding="utf-8")
        r = self.run_cli("send")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.calls(), [])

    # ---------- 再犯与待提升（#216） ----------

    def numbered(self, text):
        return [line.split(". ", 1)[1].replace("⏰ ", "").split(" ")[0] for line in text.splitlines() if line[:1].isdigit()]

    def test_guards_only_two_kinds_capped_at_seven(self):
        self.weekly(WEEKLY)
        r = self.run_cli("send", "--dry-run", lessons=None)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.card_text(r.stdout)
        ids = self.numbered(text)
        # 原三类照旧在前（LESSONS 默认不列）；再犯与待提升在最后：升级首选 G-04、G-11 最前，G-09 次之，再接提议提升，合计 7 条
        self.assertEqual(ids, ["A1", "B1", "W2026-10-23-6", "W2026-10-23-9", "W2026-10-30-1", "W2026-10-30-3",
                               "G-04", "G-11", "G-09", "alpha/L-02", "delta/L-11", "delta/L-12", "delta/L-13"])
        self.assertIn("**再犯与待提升**", text)
        self.assertIn("7. G-04 并行会话共用一个工作区 · 〔文字〕又再犯 2 次（alpha、beta） · 升级首选", text)
        self.assertIn("9. G-09 强调色写死 · 〔机检〕又再犯 5 次（beta、gamma）\n", text)
        self.assertIn("10. alpha/L-02 alpha 的坑 L-02 · 提议提升为全局坑", text)
        for not_listed in ("alpha/L-01", "alpha/L-03", "delta/L-14", "delta/L-16"):   # 只写未升级的不列；超出 7 条的不列
            self.assertNotIn(not_listed, text)
        self.assertIn("再犯与待提升另有 3 条没列（每期最多 7 条），清单在 DigitalTwin/proposals/digest.json 本期的 more", text)
        self.assertNotIn("LESSONS 升级候选", text)                       # DT_LESSONS_MIN_PROJECTS 为 0：hook 命中那类照旧不列
        self.assertEqual(json.loads((self.tmp / "fake/node-calls.jsonl").read_text().splitlines()[0])[1:], ["--json"])

    def test_guards_with_lessons_on_keep_both(self):
        self.weekly(WEEKLY)
        text = self.card_text(self.run_cli("send", "--dry-run").stdout)   # lessons="2"：LESSONS 候选照旧打开
        ids = self.numbered(text)
        self.assertEqual(ids[6:8], ["G-01", "G-04"])                       # LESSONS 候选在前，再犯与待提升接着编号
        self.assertEqual(len(ids), 6 + 1 + 7)

    def test_guards_end_to_end_apply_then_next_week(self):
        self.weekly(WEEKLY)
        keys = {i["id"]: i for i in json.loads(self.run_cli("draft", lessons=None).stdout)["items"]}
        self.assertEqual(keys["G-04"]["key"], "recur:G-04")
        self.assertEqual(keys["G-04"]["group"], "再犯与待提升")
        self.assertIn("升级首选：是", keys["G-04"]["body"])
        self.assertIn("现在的层级：〔提醒〕，状态：未升级", keys["G-11"]["body"])
        self.assertIn("升级行：未升级。换个项目也成立，提议提升为全局 G-xx。", keys["alpha/L-02"]["body"])
        draft = json.loads(self.run_cli("draft", lessons=None).stdout)
        self.assertEqual((draft["more"], len(draft["notes"])), (3, 1))
        rec = self.recommend({keys["G-04"]["key"]: {"verdict": "采纳", "why": "〔文字〕层两个项目又踩"},
                              keys["alpha/L-02"]["key"]: {"verdict": "延期", "why": "只有一个项目"}})
        r = self.run_cli("send", "--recommend", rec, lessons=None)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        issue = json.loads(self.state.read_text())["issues"][0]
        self.assertEqual([i["id"] for i in issue["more"]], ["delta/L-14", "delta/L-15", "delta/L-16"])
        listing = self.run_cli("list", lessons=None).stdout
        self.assertIn("13. [guards] delta/L-13", listing)
        self.assertIn("再犯与待提升超出没列的 3 条", listing)
        self.assertIn("- delta/L-16 delta 的坑 L-16 · 提议提升为全局坑", listing)

        # 「按推荐」+ 逐条 对新来源同样生效：7 G-04 采纳（推荐）、10 alpha/L-02 延期（推荐）、8 G-11 否、9 改采纳
        r = self.run_cli("apply", "按推荐，否 8，采纳 9", lessons=None)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("采纳 7（G-04）、9（G-09）", r.stdout)
        self.assertIn("否 8（G-11）", r.stdout)
        self.assertIn("延期 10（alpha/L-02）", r.stdout)
        ledger = (self.shadow / "guards-decisions.md").read_text(encoding="utf-8")
        self.assertTrue(ledger.startswith("# 再犯与待提升的结论"))
        self.assertIn("- G-04　采纳　2026-11-09　常浩飞书回复（提案收口第 1 期）", ledger)
        self.assertIn("- alpha/L-02　待定　2026-11-09", ledger)
        self.assertFalse((self.shadow / "lessons-decisions.md").exists())   # 不和 LESSONS 候选的结论混记
        r = self.run_cli("apply", "采纳 11 12 13", lessons=None)
        self.assertEqual(r.returncode, 0, r.stdout)

        # 下周：定了的不再列，延期的再列，超出的往上补
        r = self.run_cli("send", "--dry-run", now="2026-11-16T10:00:00+08:00", lessons=None)
        text = self.card_text(r.stdout)
        guards = self.numbered(text)[len(self.numbered(text)) - 4:]
        self.assertEqual(guards, ["alpha/L-02", "delta/L-14", "delta/L-15", "delta/L-16"])
        for gone in ("G-04", "G-11", "G-09", "delta/L-11"):
            self.assertNotIn(gone + " ", text)
        self.assertNotIn("另有", text)

    def test_guards_unavailable_other_sources_still_sent(self):
        cases = {"fail": "每周汇总出错（退出码 1：每周汇总中断：ENOENT: no such file）",
                 "notjson": "每周汇总的输出不是 JSON（agent-kit 主目录可能还没有 --json",
                 "schema": "每周汇总的 schema 不是 1（2）",
                 "noscript": "找不到 agent-kit 每周汇总脚本"}
        for case, why in cases.items():
            with self.subTest(case=case):
                self.weekly(WEEKLY)
                (self.tmp / "fake/weekly-fail").unlink(missing_ok=True)
                env = {}
                if case == "fail":
                    (self.tmp / "fake/weekly-fail").write_text("1")
                elif case == "notjson":
                    (self.tmp / "fake/weekly.json").write_text("# 每周整理输入（老版本只出 Markdown）\n", encoding="utf-8")
                elif case == "schema":
                    self.weekly({**WEEKLY, "schema": 2})
                script = self.tmp / "agent-kit/tools/weekly-review.mjs"
                moved = script.with_suffix(".bak")
                if case == "noscript":
                    script.rename(moved)
                try:
                    r = self.run_cli("send", "--dry-run", lessons=None)
                finally:
                    if moved.exists():
                        moved.rename(script)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                text = self.card_text(r.stdout)
                self.assertEqual(self.numbered(text)[:2], ["A1", "B1"])          # 其他来源照常
                self.assertIn("⚠️ 再犯与待提升本期没取到：" + why, text)
                self.assertNotIn("**再犯与待提升**", text)
        (self.tmp / "fake/weekly-fail").unlink(missing_ok=True)

    def test_guards_unavailable_alone_still_sends_notice(self):
        for d in ("retro", "niuma"):
            subprocess.run(["rm", "-rf", str(self.tmp / d)], check=True)
        (self.tmp / "fake/weekly-fail").write_text("1")
        d = json.loads(self.run_cli("draft", lessons=None).stdout)        # 草稿也带上，Hermes 不会回「无」
        self.assertEqual(d["items"], [])
        self.assertIn("再犯与待提升本期没取到", d["notes"][0])
        r = self.run_cli("send", lessons=None)
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        (call,) = self.calls()
        card = json.loads(call[call.index("--content") + 1])
        text = card["body"]["elements"][0]["content"]
        self.assertTrue(text.startswith("本期没有待决提案。\n\n⚠️ 再犯与待提升本期没取到：每周汇总出错"))
        self.assertNotIn("回复写法", text)


if __name__ == "__main__":
    unittest.main()
