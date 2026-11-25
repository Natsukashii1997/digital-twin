"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

autoloop 技能（#195 批 2 派活入口，hermes/skills/autoloop/SKILL.md）的三类核对：
  1. 文档对得上脚本：技能里写的每条 G、P 命令都能被命令行解析；技能表格里引用的脚本原话、字段名、卡上的结果字样，
     脚本里（或真实渲染出的确认卡上）确实有；
  2. 五要素文件 → P（客户端 → 真的 3353 窗口 → 网关 propose，0E-3b）→ 门卫发确认卡、常浩点「批准派单」（模拟门卫：
     mark_card + decide，门卫本身见 test_gatekeeper_grants）→ 派单事件发出，以及技能里写了回话的各条岔路
     （名单外、点「不派」、过期、中台连不上后补送、续接、停、冻）。
     「把一句话整理成五要素」是模型做的，这里测不到，从整理好的五要素文件开始；
  3. 端到端：临时目录里起真的 agent-autoloop 中台（同 test_autoloop_hub_e2e 的 HubFixture，本机没有命令行时跳过），
     按技能第 1 节看通道（暂停不派、没规则不派、没钥匙不派），恢复后派一单，中台里出现这张单的轮次；
     提单后通道被暂停，中台收到但没有轮次（技能第 4 节第二步要查出来的情况）；
     续接后按技能第 6 节数这张单的事件条数：通道开着多一条，通道暂停时 sent 是 true 但条数不变。
凭证库、镜像、任务单、名册、中台都在临时目录；窗口在本进程里起（随机端口）。不碰 DigitalTwin/autoloop/ 和正式中台。

没覆盖的：第 2、3 类把批 0D 的恢复闸打桩放行（RV.ensure_open / RV.admit，同 test_autoloop_grants.Base），
「真中台 + 真恢复闸」这条路这里不走；恢复闸关着时的回话只用打桩的拒绝验（OneSentenceToDispatch 里那条）。
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
import autoloop_cardview as CV  # noqa: E402
import autoloop_config as C  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_exits as EX  # noqa: E402
import autoloop_readonly as R  # noqa: E402
import autoloop_recovery as RV  # noqa: E402
import gate_intake as I  # noqa: E402
from test_autoloop_grants import CHAT, LATER, T0, Base, hub  # noqa: E402
from test_autoloop_hub_e2e import SKIP, HubFixture  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401

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


def click(code, choice="ok", now=T0, post=None):
    """模拟门卫：发确认卡（登记消息号）、常浩在卡上点按钮。返回 decide 的结果。"""
    with G.tx() as con:
        token, mid = con.execute("SELECT card_token, card_message FROM proposals WHERE code = ?", (code,)).fetchone()
    if not mid:
        G.mark_card(code, f"om_card_{code}")
    return G.decide(token, choice, f"om_card_{code}", f"ev-{code}-{choice}-{now:%H%M%S}", now=now, post=post)


class Window:
    """真的 3353 窗口（本进程里起，随机端口），控制面工具换成在本进程里跑网关的命令行：和 dt-gate 里一样拿它打印的
    原文交给 gate_intake.last_json 解析，只是不起子进程。"""

    def __init__(self, test, token_dir, now=T0):
        import http.server
        import threading
        self.now = now
        token = "w" * 40
        f = Path(token_dir) / "intake.token"
        f.write_text(token, encoding="utf-8")
        os.chmod(f, 0o600)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), I.make_handler(token, self.tool))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        test.addCleanup(srv.server_close)
        test.addCleanup(srv.shutdown)
        p = mock.patch.dict(os.environ, {"DT_INTAKE_TOKEN_FILE": str(f), "DT_INTAKE_URL": f"http://127.0.0.1:{srv.server_address[1]}",
                                         "DT_INTAKE_INBOX": str(Path(token_dir) / "inbox")})
        p.start()
        test.addCleanup(p.stop)

    def tool(self, script, args):
        assert script == "autoloop_grants.py", script
        buf = io.StringIO()
        with mock.patch.object(G, "now_tz", return_value=self.now), contextlib.redirect_stdout(buf):
            code = G.main(args)
        return code, I.last_json(buf.getvalue())


def p_cli(argv):
    """像猛鬼兽那样跑一条 P 命令（客户端）：返回 (退出码, 输出的 JSON)。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = I.main(["dispatch-propose", *argv])
    out = json.loads(buf.getvalue())
    assert re.match(r"^[0-9a-f]{16,64}$", out.pop("request_id")), out     # 每次输出都带请求号（#369 P2-02）
    return code, out


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
            if "--" not in cmd and cmd not in ("G show", "G send-pending"):      # 只是提到名字（如「跑 `G freeze`」）
                self.assertIn(cmd.split()[1], subs, cmd)
                continue
            filled = re.sub(r"<[^<>|]*\|[^<>]*>", lambda m: m.group(0)[1:-1].split("|")[0], cmd)   # <a|b> 取第一个
            filled = re.sub(r"<[^<>]*>", "x", filled)
            argv = shlex.split(filled)[1:]
            with self.subTest(cmd=cmd), mock.patch("sys.stderr"):
                a = G.build_parser().parse_args(argv)          # 解析不了会 SystemExit
                seen.add(a.cmd)
        self.assertEqual(seen, {"show", "send-pending", "resume-dispatch", "revoke", "freeze", "complete"})
        # 提单走 P（下一条测试）；批准没有命令（只认门卫卡上的点击，0E-3b）；放权方向的 unfreeze、出口用的 check 不归技能调
        self.assertNotIn("approve", subs)

    def test_every_propose_command_in_the_skill_parses(self):
        cmds = set(re.findall(r"`(P [^`]*)`", self.text))
        cmds |= {line.strip() for line in self.text.splitlines() if re.match(r"^P --", line.strip())}
        self.assertTrue(cmds)
        prefix = "`/usr/bin/python3 ~/.hermes/scripts/gate_intake.py dispatch-propose`"
        self.assertIn(f"`P` = {prefix}", self.text)
        for cmd in sorted(cmds):
            if "--" not in cmd:
                continue
            filled = re.sub(r"<[^<>|]*\|[^<>]*>", lambda m: m.group(0)[1:-1].split("|")[0], cmd)
            filled = re.sub(r"<[^<>]*>", "x", filled)
            with self.subTest(cmd=cmd), mock.patch.object(I, "propose_dispatch", return_value=(0, {})) as m, \
                    mock.patch("pathlib.Path.read_text", return_value="五要素"), mock.patch("sys.stdout"):
                self.assertEqual(I.main(["dispatch-propose", *shlex.split(filled)[1:]]), 0)
                self.assertEqual(m.call_args[0][:2], ("x", "claude"))

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
        self.assertEqual([len(t) for t in tables], [11], tables)
        reach = [inspect.getsource(G.propose) + inspect.getsource(RV.ensure_open) + inspect.getsource(G.require_hub_rule)
                 + inspect.getsource(I.propose_dispatch)]
        for table, src in zip(tables, reach):
            for words in table:
                with self.subTest(words=words):
                    self.assertIn(words, src)
        for words, fn in (("门卫会把确认卡发到常浩私聊，他点「批准派单」才签", G.propose), ("不能续接：", G.resume_dispatch),
                          ("已经是终态", G._tighten), ("凭证已过期", G.check_row), ("派单确认 · ", CV.render_grant),
                          ("连不上 dt-gate 的窗口", I.propose_dispatch), ("✅ 批准派单", CV.render_grant),
                          ("不同意？· 展开", CV.render_grant),
                          ("还有没确认送到中台的派单事件", G.resume_dispatch), ("另有入口正在处理", G.task_lock),
                          ("开闸后 send-pending 补发", G.send_pending), ("恢复闸关着", RV.ensure_open),
                          ("派单事件发出后没确认送到中台", G._stop_after), ("中台库读不了", G.stop_runs)):
            with self.subTest(words=words):
                self.assertIn(words, self.text)
                self.assertIn(words, inspect.getsource(fn))

    def test_field_names_in_the_skill_are_real(self):
        for field, script in (("usable", "autoloop_grants"), ("why_not", "autoloop_grants"), ("events", "autoloop_grants"),
                              ("valid_until", "autoloop_grants"), ("freeze_code", "autoloop_grants"),
                              ("mirror_error", "autoloop_grants"), ("event_key", "autoloop_grants"),
                              ("in_flight", "autoloop_grants"), ("runs_stopped", "autoloop_grants"),
                              ("runs_finished", "autoloop_grants"), ("runs_stop_error", "autoloop_grants"),
                              ("dispatch_rules", "autoloop_config"),
                              ("source_hermes", "autoloop_config"), ("key_file", "autoloop_config"),
                              ("loaded", "autoloop_config"), ("paused", "autoloop_config"),
                              ("key", "autoloop_config"), ("executor", "autoloop_grants"), ("repo", "autoloop_grants"),
                              ("runs", "autoloop_readonly"), ("subject", "autoloop_readonly"),
                              ("cards", "autoloop_readonly"), ("recentCards", "autoloop_readonly"),
                              ("kind", "autoloop_readonly"), ("state", "autoloop_readonly"),
                              ("lane", "autoloop_readonly"), ("attempts", "autoloop_readonly")):
            with self.subTest(field=field):
                self.assertIn(f"`{field}`", self.text)             # 技能里当字段名写（带反引号），不是碰巧出现的单词
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
        for words in ("最多 3000 字", "1 到 72", "30 分钟后自动作废", "过 60 秒"):
            self.assertIn(words, self.text)
        self.assertEqual(G.build_parser().parse_args(["propose", "--repo", "r", "--executor", "claude",
                                                      "--scope-file", "f"]).hours, 12)       # 技能写的默认 12 小时
        self.assertEqual((I.MAX_SCOPE, I.EXECUTORS), (G.MAX_SCOPE, G.EXECUTORS))            # 窗口先核的形状和网关一致


class InstalledLayoutRoster(unittest.TestCase):
    """#316：安装副本缺默认名册时明确拒绝；技能中的显式 DT_ROSTER 前缀仍可选定其他名册。"""

    def test_missing_default_rejects_cleanly_and_skill_prefix_still_works(self):
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
            env["HOME"] = str(root / "home")
            bare = subprocess.run(argv, capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(bare.returncode, 1)
            self.assertEqual(bare.stderr, "")
            missing = json.loads(bare.stdout)
            self.assertFalse(missing["ok"])
            self.assertIn(str(root / "home/Documents/ChatGPT/个人/work/digital-twin/projects.json"), missing["why"])
            self.assertIn("DT_ROSTER", missing["why"])
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
        self.window = Window(self, self.root)

    def propose(self, repo="dt-sandbox", executor="claude", now=T0):
        self.window.now = now
        return p_cli(["--repo", repo, "--executor", executor, "--scope-file", str(self.scope), "--hours", "12"])

    def dispatch(self, now=T0):
        return click(self.propose(now=now)[1]["code"], now=now, post=self.post)["task_id"]

    def test_project_without_dispatch_rule_is_refused_at_propose(self):
        """常浩 11-23 定（#346）：中台没有这条派单规则，网关提议时就拒绝，不登记提议；技能第 3 节的表认得这句原话。"""
        self.hub_rules = hub(("dt-sandbox", "claude"))
        code, out = self.propose(executor="codex")
        self.assertEqual(code, 1)
        self.assertIn("名册放行了写入，但中台没有接", out["why"])
        self.assertEqual(G.cards_to_send(), [])

    def test_five_elements_to_event(self):
        code, out = self.propose()                             # 客户端 → 窗口 → 网关（网关按缩进打印多行 JSON）
        self.assertEqual((code, out["card"], out["repo"], out["executor"]),
                         (0, "门卫会把确认卡发到常浩私聊，他点「批准派单」才签", "dt-sandbox", "claude"))
        (p,) = G.cards_to_send(now=T0)                         # 门卫要发的卡：五要素原文 + 两个按钮
        card = json.dumps(CV.render_grant(p, lambda a: {"gate_grant": p["card_token"], "a": a}), ensure_ascii=False)
        self.assertIn(json.dumps(FIVE, ensure_ascii=False)[1:-1], card)
        self.assertIn("派单确认 · dt-sandbox", card)
        self.assertEqual(G.show(), [])                         # 他没点之前什么都没签

        res = click(out["code"], post=self.post)
        tid = res["task_id"]
        self.assertRegex(tid, G.TASK_ID)
        self.assertEqual((res["decision"], res["note"]), ("approved", "已送到中台，等工人接单"))
        self.assertEqual([(s["sent"], s["hub_event"]) for s in res["send"]], [(True, 101)])
        (ev,) = self.posted                                    # 中台收到的事件：对象是单号，类型对着这条派单规则
        self.assertEqual((ev["subject"], ev["type"]), (tid, "dispatch.dt-sandbox.claude.write"))
        self.assertNotIn("README", json.dumps(ev, ensure_ascii=False))
        self.assertIn(FIVE, (self.tasks / f"{tid}.md").read_text(encoding="utf-8"))      # 工人读的任务单 = 他确认的原文

        code, (g,) = cli(["show", "--task", tid])              # 技能第 4 节回执、第 5 节第 1 层
        self.assertEqual((code, g["usable"], g["why_not"], g["executor"], g["github"]), (0, True, None, "claude", "o/dt-sandbox"))
        self.assertEqual(g["valid_until"], G.iso(T0 + dt.timedelta(hours=12)))
        self.assertEqual([(e["kind"], e["state"]) for e in g["events"]], [("start", "已送到中台")])

    def test_outside_the_list_is_refused_before_anything_is_sent(self):
        for repo in ("agent-autoloop", "little-steps", "nope"):          # 明说不许写、没写许不许、名册里没有
            with self.subTest(repo=repo):
                self.assertEqual(self.propose(repo), (1, {"ok": False, "why": f"{repo} 不在名册能派写入单的仓库里"}))
        self.assertEqual(self.propose(executor="gpt")[0], 1)            # 窗口先拒（形状不对），到不了网关
        self.assertEqual((G.cards_to_send(), self.posted, G.show()), ([], [], []))

    def test_only_a_click_on_the_card_approves(self):
        """打字批准没有了（技能第 0 节第 3 条）；「不派」、过期都不签；同一张卡点两次只签一次；改了重提是新卡。"""
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            G.main(["approve", "--message-id", "om_fixturemsg0001"])
        no = click(self.propose()[1]["code"], "no", post=self.post)
        self.assertEqual(no["decision"], "rejected")
        late = T0 + dt.timedelta(minutes=31)
        old = self.propose()[1]["code"]
        self.assertEqual(click(old, now=late, post=self.post)["decision"], "expired")
        self.assertEqual((self.posted, G.show()), ([], []))
        new = self.propose(now=late)[1]["code"]                # 重新整理一次：新提议、新卡
        self.assertNotEqual(new, old)
        tid = click(new, now=late, post=self.post)["task_id"]
        again = click(new, now=late + dt.timedelta(seconds=1), post=self.post)
        self.assertEqual((again["already"], again["task_id"]), (True, tid))
        self.assertEqual(len(self.posted), 1)

    def test_hub_down_then_resend_never_creates_a_second_task(self):
        def down(body):
            raise G.Rejected("连不上中台或回应不对：URLError")
        res = click(self.propose()[1]["code"], post=down)
        (s,) = res["send"]
        self.assertIsNone(s["sent"])
        self.assertIn("结果不明", s["why"])
        self.assertTrue(res["note"].startswith("凭证已签，还没确认送到中台"))          # 卡上那一行（技能第 4 节表）
        self.assertEqual(cli(["show", "--task", res["task_id"]])[1][0]["events"][0]["state"], "在途或结果不明")
        self.assertEqual(cli(["send-pending"])[1], [])         # 没到 60 秒：不抢
        code, sent = cli(["send-pending"], LATER)
        self.assertEqual((code, [x["sent"] for x in sent]), (0, [True]))
        self.assertEqual(len(G.show()), 1)
        self.assertEqual(len(self.posted), 1)

    def test_missing_source_key_is_reported_as_not_sent(self):
        res = click(self.propose()[1]["code"], post=self.real_post)     # 真的发送函数：隔离环境里没有钥匙文件
        self.assertIsNone(res["send"][0]["sent"])
        self.assertIn("还没有 hermes 来源密钥", res["send"][0]["why"])
        self.assertEqual(len(G.show()), 1)                     # 凭证已签，只是没派出去

    def test_closed_recovery_gate_is_reported_not_retried(self):
        """批 0D 的恢复闸关着时：提单被拒、原因里有「恢复闸关着」；点卡照样签凭证，但卡上写「还没送到中台」、事件留在账里；
        开闸后 send-pending 补发。技能第 3、4 节各有一行讲这种情况。"""
        closed = mock.patch.object(RV, "ensure_open", side_effect=EX.Refused("恢复闸关着：电脑睡醒过，要先对账"))
        with closed:
            code, out = self.propose()
        self.assertEqual((code, out["ok"]), (1, False))
        self.assertIn("恢复闸关着", out["why"])
        # 网关拒绝语后半句「事件已记账，开闸后 send-pending 补发」对提单不成立（技能第 3 节那行叫猛鬼兽别转）：
        # 钉住它，网关改了措辞这里会失败，技能那一行要跟着改
        self.assertIn("事件已记账，开闸后 send-pending 补发", out["why"])
        with G.tx() as con:                                    # 真的什么都没登记：没有提议、没有事件
            self.assertEqual([con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("proposals", "hub_events")],
                             [0, 0])
        self.assertEqual(cli(["send-pending"]), (0, []))       # 开闸后补发也补不出东西
        code = self.propose()[1]["code"]
        with closed:
            res = click(code, post=self.post)
        self.assertIn("恢复闸关着", res["send"]["error"])
        self.assertTrue(res["note"].startswith("凭证已签，还没送到中台："))
        tid = res["task_id"]
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

    def test_card_results_quoted_in_section_4_are_what_the_card_shows(self):
        """技能第 4 节「卡上写的」那张表：每一格引的字样，都出现在真实流程定出结果后门卫改出的卡片上。"""
        text = SKILL.read_text(encoding="utf-8")
        sec = text[text.index("## 4. 他点了卡之后"):text.index("## 5. 「t-… 怎么样了」")]
        rows = [re.findall(r"`([^`]+)`", line.split("|")[1]) for line in sec.splitlines() if line.startswith("| `")]
        self.assertEqual(len(rows), 7, rows)

        def down(body):
            raise G.Rejected("连不上中台或回应不对：URLError")
        click(self.propose()[1]["code"], post=self.post)                  # 已送到
        click(self.propose()[1]["code"], post=down)                       # 没确认送到
        shut = self.propose()[1]["code"]
        with mock.patch.object(RV, "ensure_open", side_effect=EX.Refused("恢复闸关着：x")):
            click(shut, post=self.post)                                   # 闸关着
        held = self.propose()[1]["code"]
        with mock.patch.object(G, "route_problem", lambda repo, ex: "中台的派单规则 x 和应有的不一样（to）"):
            click(held, post=self.post)                                   # 签了、发前核不过：暂缓（#369 P2-03）
        click(self.propose()[1]["code"], "no", post=self.post)            # 不派
        late = self.propose()[1]["code"]
        G.mark_card(late, f"om_card_{late}")
        G.expire_due(now=T0 + dt.timedelta(minutes=31))                  # 过期
        void = self.propose()[1]["code"]
        self.write_roster("o2/dt-sandbox")
        click(void, post=self.post)                                       # 名册变了
        shown = " ".join(json.dumps(CV.render_grant(p, lambda a: {}), ensure_ascii=False) for p in G.cards_to_close())
        self.assertEqual(len(G.cards_to_close()), 7)
        self.assertNotIn("作废没发", shown)                                 # 暂缓不说成作废
        for words in sum(rows, []):
            with self.subTest(words=words):
                self.assertIn(words, shown)

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

    def test_stop_while_a_step_is_running_and_resume_while_another_entry_holds_the_task(self):
        """技能第 7 节「输出里有 in_flight」、第 6 节「另有入口正在处理」两条回话对着的行为，走命令行。"""
        tid = self.dispatch()
        seen = {}

        def do():                                              # 推分支正在做的时候常浩说「停」
            seen["revoke"] = cli(["revoke", "--task", tid, "--reason", "常浩说停"])
            return {"ok": 1}
        key = f"push_branch:{tid}:b:h1"
        EX.guard("push_branch", key, f"task:{tid}", {"github": "o/dt-sandbox", "branch": f"claude/{tid}"}, do,
                 task=(tid, "push_branch", 1), now=T0)
        code, out = seen["revoke"]
        self.assertEqual((code, out["status"], out["in_flight"]), (0, "revoked", [{"key": key, "state": "executing"}]))

        other = self.dispatch()
        with G.task_lock(other):                               # 受信发布或秘书正持着这张单
            code, out = cli(["resume-dispatch", "--task", other, "--kind", "redispatch", "--reason", "再跑一次"])
        self.assertEqual(code, 1)
        self.assertIn("另有入口正在处理", out["why"])
        self.assertEqual(len(self.posted), 2)                  # 两次 start，续接没发

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
        for p in (mock.patch.dict(os.environ, {"DT_GRANTS_DB": str(root / "dt/autoloop/grants.db"), "DT_OWNER_CHAT_ID": CHAT,
                                               "DT_GRANTS_STATUS": str(self.status_file), "DT_GRANTS_TASKS": str(self.tasks)}),
                  mock.patch.object(RV, "ensure_open"), mock.patch.object(RV, "admit")):   # 恢复闸放行，同 test_autoloop_grants.Base
            p.start()
            self.addCleanup(p.stop)
        self.scope = root / "五要素.md"
        self.scope.write_text(FIVE, encoding="utf-8")

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
        self.assertEqual(code, 0)                              # 窗口里跑的就是这条（窗口本身见 OneSentenceToDispatch）
        before_approve()
        out = click(out["code"])                               # 常浩在卫士的卡上点「批准派单」（同 cli 用 T0）
        self.assertEqual(out["decision"], "approved")
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
        self.assertEqual((len(out["runs_stopped"]), out.get("runs_stop_error")), (1, None))   # 技能第 7 节：排着队的那一轮也叫停了
        self.assertEqual(self.runs_of(tid)[0]["state"], "cancelled")
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

    def events_of(self, tid):
        """技能第 6 节「续接后」数的数：这张单所有轮次里的事件条数。"""
        return sum(len(r["events"]) for r in self.runs_of(tid))

    def test_resume_counts_events_open_lane_adds_one_paused_lane_adds_none(self):
        """续接后不能只看 send 是 true，也不能只看「找得到轮次」（旧轮次本来就在）：
        通道开着条数加一；通道暂停时中台照样收下（sent true），但条数不变。"""
        C.install(confirm=True, target=self.home)
        C.issue_source_key(confirm=True, target=self.home)
        C.hub("POST", "/v1/rules/dispatch-dt-sandbox-claude/resume", {})
        tid, sent = self.dispatch()
        self.assertTrue(sent["sent"])
        before = self.events_of(tid)
        self.assertEqual(before, 1)

        code, out = cli(["resume-dispatch", "--task", tid, "--kind", "redispatch", "--reason", "再跑一次"])
        self.assertEqual((code, [s["sent"] for s in out["send"]]), (0, [True]))
        self.assertEqual(self.events_of(tid), before + 1)

        C.hub("POST", "/v1/rules/dispatch-dt-sandbox-claude/pause", {"reason": "测试"})
        self.assertEqual(self.gate(), "通道暂停")             # 技能第 6 节「续接前」看通道就该停在这里
        code, out = cli(["resume-dispatch", "--task", tid, "--kind", "rework", "--reason", "返修"])
        self.assertEqual((code, [s["sent"] for s in out["send"]]), (0, [True]))      # 中台收下了
        self.assertEqual(self.events_of(tid), before + 1)                            # 但没有轮次接：条数没变
        self.assertTrue(self.runs_of(tid))                                           # 旧轮次还在，「找得到」不算数


if __name__ == "__main__":
    unittest.main()
