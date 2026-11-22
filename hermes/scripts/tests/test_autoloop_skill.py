"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

autoloop 技能（#195 批 2 派活入口，hermes/skills/autoloop/SKILL.md）的三类核对：
  1. 文档对得上脚本：技能里写的每条 G 命令都能被网关的命令行解析；技能表格里引用的脚本原话、字段名，脚本里确实有；
  2. 五要素文件 → propose → 常浩照确认消息回 → approve → 派单事件发出，全程走命令行（和猛鬼兽调用的方式一样），
     以及技能里写了回话的各条岔路（名单外、只回「可以」、别人回的、中台连不上后补送、续接、停、冻）。
     「把一句话整理成五要素」是模型做的，这里测不到，从整理好的五要素文件开始；
  3. 端到端：临时目录里起真的 agent-autoloop 中台（同 test_autoloop_hub_e2e 的 HubFixture，本机没有命令行时跳过），
     按技能第 1 节看通道（暂停不派、没规则不派、没钥匙不派），恢复后派一单，中台里出现这张单的轮次；
     提单后通道被暂停，中台收到但没有轮次（技能第 4 节第二步要查出来的情况）。
凭证库、镜像、任务单、名册、中台都在临时目录；飞书回读和发私聊打桩。不碰 DigitalTwin/autoloop/ 和正式中台。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import datetime as dt
import io
import json
import os
import inspect
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import autoloop_config as C  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_exits as EX  # noqa: E402
import autoloop_readonly as R  # noqa: E402
import autoloop_recovery as RV  # noqa: E402
from test_autoloop_grants import CHAT, LATER, OWNER, T0, Base, raw_msg  # noqa: E402
from test_autoloop_hub_e2e import SKIP, HubFixture  # noqa: E402

SKILL = HERE.parents[1] / "skills/autoloop/SKILL.md"
SCRIPTS = HERE.parent
FIVE = "\n".join(["目标：给 dt-sandbox 的 README 加一节「怎么跑测试」", "交付物：只改 README.md，开一个 PR",
                  "验收：PR 里只有 README.md，新增一节写明测试命令", "边界：不改别的文件，不合并", "交给谁：Claude"])


def cli(argv, now=T0):
    """像猛鬼兽那样跑一条 G 命令：返回 (退出码, 输出的 JSON)。"""
    buf = io.StringIO()
    with mock.patch.object(G, "now_tz", return_value=now), contextlib.redirect_stdout(buf):
        code = G.main(argv)
    return code, json.loads(buf.getvalue())


def reply_line(notice):
    """常浩照【派单确认】最后一行回的那句话：从网关发的确认消息里取，不经过模型。"""
    return next(line for line in notice.splitlines() if line.startswith("同意就回：")).split("：", 1)[1]


class SkillDocMatchesScripts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text(encoding="utf-8")
        cls.src = {n: (SCRIPTS / f"{n}.py").read_text(encoding="utf-8")
                   for n in ("autoloop_grants", "autoloop_config", "autoloop_readonly", "ai_access")}

    def test_front_matter(self):
        head = self.text.split("---")[1]
        self.assertIn("name: autoloop", head)
        self.assertIn("related_skills: [workshop, progress, merge-officer]", head)

    def test_every_gateway_command_in_the_skill_parses(self):
        cmds = set(re.findall(r"`(G [a-z-]+[^`]*)`", self.text))
        cmds |= {line.strip() for line in self.text.splitlines() if re.match(r"^G [a-z-]+", line.strip())}
        self.assertGreaterEqual(len(cmds), 8, cmds)
        subs = next(a for a in G.build_parser()._actions if a.dest == "cmd").choices
        seen = set()
        for cmd in sorted(cmds):
            if "--" not in cmd and cmd not in ("G show", "G send-pending"):      # 只是提到名字（如「跑 `G approve`」）
                self.assertIn(cmd.split()[1], subs, cmd)
                continue
            filled = re.sub(r"<[^<>|]*\|[^<>]*>", lambda m: m.group(0)[1:-1].split("|")[0], cmd)   # <a|b> 取第一个
            filled = re.sub(r"<[^<>]*>", "x", filled)
            argv = shlex.split(filled)[1:]
            with self.subTest(cmd=cmd), mock.patch("sys.stderr"):
                a = G.build_parser().parse_args(argv)          # 解析不了会 SystemExit
                seen.add(a.cmd)
        self.assertEqual(seen, {"propose", "approve", "show", "send-pending", "resume-dispatch", "revoke", "freeze",
                                "complete"})                   # 放权方向的 unfreeze、出口用的 check 不归技能调

    def test_other_script_commands_exist(self):
        for script, sub in (("autoloop_config", "status"), ("autoloop_readonly", "show")):
            self.assertIn(f'add_parser("{sub}")', self.src[script])
        self.assertIn("ai_access.py status --json", self.text)
        self.assertIn('"default_worker"', self.src["ai_access"])

    def test_quoted_script_words_are_real(self):
        """「`why` 里有」两张表第一列引的脚本原话，在对应那条命令会走到的函数里一字不差地有：
        第 3 节的表对 propose，第 4 节的表对 approve。改了脚本的措辞，或把原话放错了表，这里会失败。"""
        tables, cur = [], None
        for line in self.text.splitlines():
            if line.startswith("| `why` 里有"):
                cur = []
                tables.append(cur)
            elif cur is not None and line.startswith("| `"):
                cur.append(re.match(r"\| `([^`]+)`", line).group(1))
            elif cur is not None and not line.startswith("|"):
                cur = None
        self.assertEqual([len(t) for t in tables], [6, 8], tables)
        reach = [inspect.getsource(G.propose) + inspect.getsource(G.notify_owner) + inspect.getsource(RV.ensure_open),
                 inspect.getsource(G.approve) + inspect.getsource(G.verify_owner)]
        for table, src in zip(tables, reach):
            for words in table:
                with self.subTest(words=words):
                    self.assertIn(words, src)
        for words, fn in (("已发到常浩私聊", G.propose), ("不能续接：", G.resume_dispatch), ("已经是终态", G._tighten),
                          ("凭证已过期", G.check_row), ("【派单确认】", G.notice_text),
                          ("还有没确认送到中台的派单事件", G.resume_dispatch), ("另有入口正在处理", G.task_lock),
                          ("开闸后 send-pending 补发", G.send_pending), ("恢复闸关着", RV.ensure_open)):
            with self.subTest(words=words):
                self.assertIn(words, self.text)
                self.assertIn(words, inspect.getsource(fn))

    def test_field_names_in_the_skill_are_real(self):
        for field, script in (("usable", "autoloop_grants"), ("why_not", "autoloop_grants"), ("events", "autoloop_grants"),
                              ("valid_until", "autoloop_grants"), ("freeze_code", "autoloop_grants"),
                              ("mirror_error", "autoloop_grants"), ("event_key", "autoloop_grants"),
                              ("in_flight", "autoloop_grants"),
                              ("task_id", "autoloop_grants"), ("dispatch_rules", "autoloop_config"),
                              ("source_hermes", "autoloop_config"), ("key_file", "autoloop_config"),
                              ("loaded", "autoloop_config"), ("paused", "autoloop_config"),
                              ("key", "autoloop_config"), ("executor", "autoloop_grants"), ("repo", "autoloop_grants"),
                              ("runs", "autoloop_readonly"), ("subject", "autoloop_readonly"),
                              ("cards", "autoloop_readonly"), ("recentCards", "autoloop_readonly"),
                              ("kind", "autoloop_readonly"), ("state", "autoloop_readonly"),
                              ("lane", "autoloop_readonly"), ("attempts", "autoloop_readonly")):
            with self.subTest(field=field):
                self.assertIn(field, self.text)
                self.assertIn(f'"{field}"', self.src[script])
        for word in G.SENT_WORDS.values():                    # 技能第 5 节的状态词取自这张表：三个原样引，两个作废的按开头认
            self.assertTrue(f"`{word}`" in self.text or word.startswith("作废"), word)
        self.assertIn("以 `作废` 开头", self.text)
        for kind in ("redispatch", "rework", "correction"):
            self.assertIn(kind, G.KINDS)
            self.assertIn(f"`{kind}`", self.text)

    def test_limits_in_the_skill_match_the_gateway(self):
        self.assertEqual((G.MAX_SCOPE, G.MAX_HOURS, int(G.PROPOSAL_TTL.total_seconds() // 60), int(G.STALE.total_seconds())),
                         (3000, 72, 30, 60))
        for words in ("最多 3000 字", "1 到 72", "30 分钟内有效", "过 60 秒"):
            self.assertIn(words, self.text)
        self.assertEqual(G.build_parser().parse_args(["propose", "--repo", "r", "--executor", "claude",
                                                      "--scope-file", "f"]).hours, 12)       # 技能写的默认 12 小时


class InstalledLayoutNeedsRosterPrefix(unittest.TestCase):
    """技能给 G、C 加的 DT_ROSTER 前缀不是摆设：脚本装到 ~/.hermes/scripts/ 之后，默认的名册位置（脚本目录的上上级）
    没有名册。不打桩 ROSTER，把脚本拷到 <临时目录>/scripts/ 真跑一次。"""

    def test_prefix_is_in_the_skill_and_is_what_makes_the_installed_copy_work(self):
        text = SKILL.read_text(encoding="utf-8")
        prefix = "DT_ROSTER=$HOME/Documents/ChatGPT/个人/work/digital-twin/projects.json /usr/bin/python3 ~/.hermes/scripts/"
        for script in ("autoloop_grants.py", "autoloop_config.py"):
            self.assertIn(f"`{prefix}{script}`", text)
        with tempfile.TemporaryDirectory(prefix="dt-skill-install-") as tmp:
            root = Path(tmp)
            shutil.copytree(SCRIPTS, root / "scripts", ignore=shutil.ignore_patterns("tests", "__pycache__"))
            roster = root / "elsewhere/projects.json"
            roster.parent.mkdir()
            roster.write_text(json.dumps({"projects": [], "inventory": []}), encoding="utf-8")
            scope = root / "五要素.md"
            scope.write_text(FIVE, encoding="utf-8")
            argv = [sys.executable, "-B", str(root / "scripts/autoloop_grants.py"), "propose", "--repo", "dt-sandbox",
                    "--executor", "claude", "--scope-file", str(scope)]
            env = {k: v for k, v in os.environ.items() if k != "DT_ROSTER"}
            bare = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env)
            self.assertNotEqual(bare.returncode, 0)                      # 不带前缀：找不到名册，输出是报错堆栈、不是 JSON
            self.assertIn("FileNotFoundError", bare.stderr)
            self.assertEqual(bare.stdout.strip(), "")
            ok = subprocess.run(argv, capture_output=True, text=True, timeout=60, env={**env, "DT_ROSTER": str(roster)})
            self.assertEqual((ok.returncode, json.loads(ok.stdout)),
                             (1, {"ok": False, "why": "dt-sandbox 不在名册能派写入单的仓库里"}))


class OneSentenceToDispatch(Base):
    """全程走命令行；中台用假的 post（self.post 另核：事件发出时任务和代次已在工人镜像里）。"""

    def setUp(self):
        super().setUp()
        self.scope = self.root / "五要素.md"
        self.scope.write_text(FIVE, encoding="utf-8")
        self.real_post = G.post_event
        p = mock.patch.object(G, "post_event", side_effect=self.post)
        p.start()
        self.addCleanup(p.stop)

    def propose(self, repo="dt-sandbox", executor="claude", now=T0):
        return cli(["propose", "--repo", repo, "--executor", executor, "--scope-file", str(self.scope), "--hours", "12"], now)

    def dispatch(self, now=T0):
        self.propose(now=now)
        mid = self.say(reply_line(self.notices[-1][0]), now)
        return cli(["approve", "--message-id", mid], now)[1]["task_id"]

    def test_five_elements_to_event(self):
        code, out = self.propose()
        self.assertEqual((code, out["notice"], out["repo"], out["executor"]), (0, "已发到常浩私聊", "dt-sandbox", "claude"))
        (notice, _key), = self.notices                         # 确认消息由网关自己发：五要素原文 + 怎么回
        self.assertTrue(notice.startswith("【派单确认】"))
        for line in FIVE.splitlines():
            self.assertIn(line, notice)
        said = reply_line(notice)
        self.assertEqual(said, f"派单 {out['code']}")
        self.assertEqual(G.show(), [])                         # 他没回之前什么都没签

        code, out = cli(["approve", "--message-id", self.say(said)])
        self.assertEqual(code, 0)
        tid = out["task_id"]
        self.assertRegex(tid, G.TASK_ID)
        self.assertEqual([(s["sent"], s["hub_event"]) for s in out["send"]], [(True, 101)])
        (ev,) = self.posted                                    # 中台收到的事件：对象是单号，类型对着这条派单规则
        self.assertEqual((ev["subject"], ev["type"]), (tid, "dispatch.dt-sandbox.claude.write"))
        self.assertNotIn("README", json.dumps(ev, ensure_ascii=False))
        self.assertIn(FIVE, (self.tasks / f"{tid}.md").read_text(encoding="utf-8"))      # 工人读的任务单 = 他确认的原文

        code, (g,) = cli(["show", "--task", tid])              # 技能第 5 节第 1 层、回执里的「有效到」
        self.assertEqual((code, g["usable"], g["why_not"], g["executor"], g["github"]), (0, True, None, "claude", "o/dt-sandbox"))
        self.assertEqual(g["valid_until"], G.iso(T0 + dt.timedelta(hours=12)))
        self.assertEqual([(e["kind"], e["state"]) for e in g["events"]], [("start", "已送到中台")])

    def test_outside_the_list_is_refused_before_anything_is_sent(self):
        for repo in ("agent-autoloop", "little-steps", "nope"):          # 明说不许写、没写许不许、名册里没有
            with self.subTest(repo=repo):
                self.assertEqual(self.propose(repo), (1, {"ok": False, "why": f"{repo} 不在名册能派写入单的仓库里"}))
        self.assertEqual(self.propose(executor="gpt")[1]["why"], "执行方只能是 claude 或 codex")
        self.assertEqual((self.notices, self.posted, G.show()), ([], [], []))

    def test_only_the_exact_phrase_from_the_owner_approves(self):
        self.propose()
        said = reply_line(self.notices[-1][0])
        for text, kw, why in (("可以", {}, "不是批准派单的口令"), ("派吧", {}, "不是批准派单的口令"),
                              (said + "，顺便把 CI 也加上", {}, "不是批准派单的口令"),
                              ("派单 0000000a", {}, "没有这个确认码的派单提议"),
                              (said, {"sender": "ou_someone_else_0001"}, "身份核实不通过"),
                              (said, {"chat": "oc_some_group_0001"}, "身份核实不通过")):
            with self.subTest(text=text, kw=kw):
                code, out = cli(["approve", "--message-id", self.say(text, **kw)])
                self.assertEqual((code, out["ok"]), (1, False))
                self.assertIn(why, out["why"])
        self.assertEqual((self.posted, G.show()), ([], []))
        late = T0 + dt.timedelta(minutes=31)                   # 过了 30 分钟
        self.assertIn("已过期（30 分钟）", cli(["approve", "--message-id", self.say(said, late)], late)[1]["why"])
        self.propose(now=late)                                 # 重新整理一次：新码，旧码作废
        new = reply_line(self.notices[-1][0])
        self.assertNotEqual(new, said)
        mid = self.say(new, late)
        tid = cli(["approve", "--message-id", mid], late)[1]["task_id"]
        again = cli(["approve", "--message-id", mid], late)    # 同一条确认再用一次
        self.assertEqual(again[0], 1)
        self.assertIn("已经签过凭证", again[1]["why"])
        self.assertIn(tid, again[1]["why"])
        self.assertEqual(len(self.posted), 1)

    def test_hub_down_then_resend_never_creates_a_second_task(self):
        self.propose()
        mid = self.say(reply_line(self.notices[-1][0]))
        with mock.patch.object(G, "post_event", side_effect=G.Rejected("连不上中台或回应不对：URLError")):
            code, out = cli(["approve", "--message-id", mid])
        self.assertEqual(code, 0)                              # 退出码还是 0：技能要看 send，不能只看退出码
        (s,) = out["send"]
        self.assertIsNone(s["sent"])
        self.assertIn("结果不明", s["why"])
        self.assertEqual(cli(["show", "--task", out["task_id"]])[1][0]["events"][0]["state"], "在途或结果不明")
        self.assertEqual(cli(["send-pending"])[1], [])         # 没到 60 秒：不抢
        code, sent = cli(["send-pending"], LATER)
        self.assertEqual((code, [x["sent"] for x in sent]), (0, [True]))
        self.assertEqual(len(G.show()), 1)
        self.assertEqual(len(self.posted), 1)

    def test_missing_source_key_is_reported_as_not_sent(self):
        self.propose()
        mid = self.say(reply_line(self.notices[-1][0]))
        with mock.patch.object(G, "post_event", self.real_post):       # 真的发送函数：隔离环境里没有钥匙文件
            out = cli(["approve", "--message-id", mid])[1]
        self.assertIsNone(out["send"][0]["sent"])
        self.assertIn("还没有 hermes 来源密钥", out["send"][0]["why"])
        self.assertEqual(len(G.show()), 1)                     # 凭证已签，只是没派出去

    def test_closed_recovery_gate_is_reported_not_retried(self):
        """批 0D 的恢复闸关着时：提单被拒、原因里有「恢复闸关着」；批准照样签凭证，但 send 是一句 error、事件留在账里；
        开闸后 send-pending 补发。技能第 3、4 节各有一行讲这种情况。"""
        closed = mock.patch.object(RV, "ensure_open", side_effect=EX.Refused("恢复闸关着：电脑睡醒过，要先对账"))
        with closed:
            code, out = self.propose()
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertIn("恢复闸关着", out["why"])
        self.assertEqual(self.notices, [])
        self.propose()
        mid = self.say(reply_line(self.notices[-1][0]))
        with closed:
            code, out = cli(["approve", "--message-id", mid])
        self.assertEqual(code, 0)                              # 退出码还是 0
        self.assertIn("恢复闸关着", out["send"]["error"])
        tid = out["task_id"]
        self.assertEqual((self.posted, cli(["show", "--task", tid])[1][0]["events"][0]["state"]), ([], "待发"))
        code, sent = cli(["send-pending"])                     # 开闸之后补送
        self.assertEqual([(s["event_key"], s["sent"]) for s in sent], [(f"{tid}:g1:start", True)])
        # 上一次派单还没送到时不能续接（技能第 6 节）
        other = self.dispatch()
        with mock.patch.object(G, "post_event", side_effect=G.Rejected("连不上中台或回应不对：URLError")):
            first = cli(["resume-dispatch", "--task", other, "--kind", "redispatch", "--reason", "再跑一次"])[1]
        self.assertIsNone(first["send"][0]["sent"])
        code, out = cli(["resume-dispatch", "--task", other, "--kind", "redispatch", "--reason", "再跑一次"])
        self.assertEqual(code, 1)
        self.assertIn("不能续接：这张单还有没确认送到中台的派单事件", out["why"])

    def test_resume_stop_freeze_complete(self):
        tid = self.dispatch()
        code, out = cli(["resume-dispatch", "--task", tid, "--kind", "redispatch", "--reason", "常浩说再跑一次"])
        self.assertEqual((code, out["event_key"], [s["sent"] for s in out["send"]]), (0, f"{tid}:g1:redispatch:1", [True]))
        self.assertEqual(json.loads(self.posted[-1]["detail"]), {"task_id": tid, "generation": 1, "kind": "redispatch"})
        self.assertNotIn("再跑一次", json.dumps(self.posted[-1], ensure_ascii=False))     # 续接不带新要求

        code, out = cli(["revoke", "--task", tid, "--reason", "常浩说停"])
        self.assertEqual((code, out["status"]), (0, "revoked"))
        code, out = cli(["resume-dispatch", "--task", tid, "--kind", "rework", "--reason", "返修"])
        self.assertEqual((code, out), (1, {"ok": False, "why": "不能续接：凭证已撤销"}))
        self.assertIn("已经是终态", cli(["revoke", "--task", tid])[1]["why"])
        self.assertEqual(cli(["show", "--task", tid])[1][0]["why_not"], "凭证已撤销")

        other = self.dispatch()
        code, out = cli(["freeze", "--task", other, "--reason", "常浩说先别动"])
        self.assertEqual((code, out["status"]), (0, "frozen"))
        self.assertRegex(out["freeze_code"], r"^[0-9a-f]{6}$")
        self.assertEqual(cli(["resume-dispatch", "--task", other, "--kind", "redispatch", "--reason", "x"])[1]["why"],
                         "不能续接：凭证已冻结")
        third = self.dispatch()
        self.assertEqual(cli(["complete", "--task", third])[1]["status"], "done")
        self.assertEqual(cli(["resume-dispatch", "--task", third, "--kind", "redispatch", "--reason", "x"])[1]["why"],
                         "不能续接：任务已完成")
        self.assertEqual(len(self.posted), 4)                  # 三次 start + 一次重派，停掉之后一个也没多发

    def test_no_grant_no_event(self):
        for argv in (["resume-dispatch", "--task", "t-20261122-000000", "--kind", "redispatch", "--reason", "x"],
                     ["show", "--task", "t-20261122-000000"], ["revoke", "--task", "t-20261122-000000"]):
            code, out = cli(argv)
            self.assertEqual((code, out["why"]), (1, "没有 t-20261122-000000 这张任务凭证"))
        self.assertEqual(self.posted, [])


@unittest.skipIf(SKIP, "本机没有 agent-autoloop 命令行")
class SkillOnRealHub(HubFixture):
    """临时中台上按技能走一遍：看通道 → 派一单 → 中台里有这张单的轮次。"""

    def setUp(self):
        super().setUp()
        root = self.roster.parent
        self.status_file, self.tasks = root / "shared/grants/status.json", root / "shared/tasks"
        self.msgs, self.notices, self.n = {}, [], 0
        for p in (mock.patch.dict(os.environ, {"DT_GRANTS_DB": str(root / "dt/autoloop/grants.db"), "DT_OWNER_CHAT_ID": CHAT,
                                               "DT_GRANTS_STATUS": str(self.status_file), "DT_GRANTS_TASKS": str(self.tasks)}),
                  mock.patch.object(G, "fetch_message", side_effect=self._fetch),
                  mock.patch.object(G, "notify_owner", side_effect=self._notify),
                  mock.patch.object(RV, "ensure_open"), mock.patch.object(RV, "admit")):   # 恢复闸放行，同 test_autoloop_grants.Base
            p.start()
            self.addCleanup(p.stop)
        self.scope = root / "五要素.md"
        self.scope.write_text(FIVE, encoding="utf-8")

    def _fetch(self, mid):
        m = self.msgs.get(mid)
        return None if m is None else G.I.normalize(m) | {"text": m["content"]}

    def _notify(self, text, key):
        self.notices.append(text)
        return f"om_notice{len(self.notices):04d}"

    def say(self, text, **kw):
        self.n += 1
        mid = f"om_fixturemsg{self.n:04d}"
        self.msgs[mid] = raw_msg(mid, text, T0, **kw)
        return mid

    def gate(self, repo="dt-sandbox", executor="claude"):
        """技能第 1 节第 3 步：这条通道现在能不能派。"""
        st = C.status()
        rule = st["dispatch_rules"].get(f"dispatch-{repo}-{executor}")
        if rule is None:
            return "没有通道"
        if not rule["loaded"] or rule["paused"]:
            return "通道暂停"
        return "能派" if st["source_hermes"]["key"] and st["source_hermes"]["key_file"] else "没有钥匙"

    def runs_of(self, tid):
        """技能第 4 节第二步、第 5 节第 2 层：中台里对象是这张单的轮次。"""
        return [r for r in R.read()["runs"] if any(e.get("subject") == tid for e in r["events"])]

    def dispatch(self, executor="claude", before_approve=lambda: None):
        code, out = cli(["propose", "--repo", "dt-sandbox", "--executor", executor, "--scope-file", str(self.scope)])
        self.assertEqual((code, out["notice"]), (0, "已发到常浩私聊"))
        before_approve()
        code, out = cli(["approve", "--message-id", self.say(reply_line(self.notices[-1]))])
        self.assertEqual(code, 0)
        (sent,) = [s for s in out["send"] if s["event_key"].startswith(out["task_id"])]
        return out["task_id"], sent

    def test_gate_then_dispatch_reaches_the_hub(self):
        self.roster.write_text(json.dumps({
            "projects": [{"id": "digital-twin", "github": "o/digital-twin", "category": "活跃", "write_allowed": True}],
            "inventory": [{"path": "github:o/dt-sandbox", "category": "沙盒", "sandbox": True}]}), encoding="utf-8")
        C.install(confirm=True, target=self.home)
        self.assertEqual(self.gate(), "通道暂停")                      # 装完默认暂停，钥匙也还没签
        C.hub("POST", "/v1/rules/dispatch-dt-sandbox-claude/resume", {})
        self.assertEqual(self.gate(), "没有钥匙")                      # 规则开着但钥匙没签：技能第 1 节表里第三行
        C.install(confirm=True, target=self.home)                      # 重装把它重新暂停，才能签钥匙
        C.issue_source_key(confirm=True, target=self.home)
        self.assertEqual(self.gate(), "通道暂停")
        # 网关的名单比中台的通道宽：digital-twin 网关会放行，但中台没有它的派单规则，技能在这一步停下
        self.assertIn("digital-twin", G.allowed_repos())
        self.assertEqual(self.gate("digital-twin"), "没有通道")
        C.hub("POST", "/v1/rules/dispatch-dt-sandbox-claude/resume", {})
        self.assertEqual((self.gate(), self.gate(executor="codex")), ("能派", "通道暂停"))

        tid, sent = self.dispatch()
        self.assertTrue(sent["sent"])
        self.assertIsInstance(sent["hub_event"], int)

        mine = self.runs_of(tid)
        self.assertEqual([(r["rule"], r["agent"]) for r in mine], [("dispatch-dt-sandbox-claude", "dt-claude")])
        self.assertEqual(mine[0]["attempts"], 0)                # 还没跑过是 0（技能：大于 0 才是重试过）
        self.assertIn(mine[0]["state"], ("queued", "claimed"))  # 临时中台没有工人接单
        self.assertEqual({e["source"] for e in mine[0]["events"]}, {"hermes"})
        self.assertEqual(json.loads(self.status_file.read_text(encoding="utf-8"))["tasks"][tid]["status"], "active")
        self.assertEqual(cli(["show", "--task", tid])[1][0]["events"][0]["hub_event"], sent["hub_event"])

        code, out = cli(["revoke", "--task", tid, "--reason", "常浩说停"])       # 停掉之后续接发不出去
        self.assertEqual((code, out["status"]), (0, "revoked"))
        self.assertEqual(cli(["resume-dispatch", "--task", tid, "--kind", "redispatch", "--reason", "x"])[0], 1)
        self.assertEqual(len(self.runs_of(tid)), 1)

    def test_hub_accepts_but_nobody_takes_it_when_the_lane_was_paused_after_propose(self):
        """提单时通道开着、他确认前被暂停：send 仍是 sent true，但中台里没有这张单的轮次。
        技能第 4 节第二步靠查轮次发现，不能只看 send。"""
        C.install(confirm=True, target=self.home)
        C.issue_source_key(confirm=True, target=self.home)
        C.hub("POST", "/v1/rules/dispatch-dt-sandbox-claude/resume", {})
        self.assertEqual(self.gate(), "能派")
        tid, sent = self.dispatch(
            before_approve=lambda: C.hub("POST", "/v1/rules/dispatch-dt-sandbox-claude/pause", {"reason": "测试"}))
        self.assertTrue(sent["sent"])                           # 中台收到了
        self.assertEqual(self.runs_of(tid), [])                 # 但没有通道接：没有轮次
        self.assertEqual(self.gate(), "通道暂停")
        self.assertEqual(cli(["show", "--task", tid])[1][0]["events"][0]["state"], "已送到中台")


if __name__ == "__main__":
    unittest.main()
