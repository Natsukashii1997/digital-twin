"""端到端自测（#205 批 0C 执行门）：临时目录里起一个真的 agent-autoloop 中台（--no-service，随机端口，带它自己的接单程序），
按正式的顺序走一遍：装中台配置（Agent 挂上启动脚本）→ 签 hermes 来源钥匙 → 在临时中台里恢复派单规则 → 网关两步批准、发派单事件
→ 中台的接单程序用 claude 插件启动 dt-worktree（假 claude）→ 中台记本轮结束 → 交接包 → 受信发布推到本地「GitHub」裸仓库。
再撤销凭证后补一个旧派单事件：启动脚本不启动 CLI，中台按「本轮结束」收下停下通知，不自动重派。

只改临时目录里的东西；规则里的 cwd 和接单程序名在测试里换成临时的。每项测试前后核对正式中台（~/.agent-autoloop）的
rules.json、config.json 和库里的轮次 / 卡片 / 事件数没有变（11-17 事故的教训：测试只隔离锁文件，配置误装进了正式中台）。
本机没有 agent-autoloop 命令行（如 CI）时跳过；DT_HUB_E2E=0 也跳过。

L3 第 1 轮返修补的：受信发布按真中台库核交接包对应的那一轮（P1-02）；第一轮取不到仓库时中台把这一轮放回排队、
5 分钟后重派，不当成做完（P1-04，上游 kernel/decide.ts retryLater）；用上游真解析器（claude / codex 插件的 read、
worker/contract.ts 的 readMessage）验证 CLI 已经交过结果后再追加的停下消息、没跑成消息怎么被解析（P2-02）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import shutil
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_config as C  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_publish as P  # noqa: E402
import fake_gh  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401  批 0D：整个模块指到临时目录
import autoloop_recovery as Rv  # noqa: E402
from test_autoloop_grants import CHAT, OWNER as GRANT_OWNER, raw_msg  # noqa: E402
from test_autoloop_hub_e2e import OWNER, SKIP, HubFixture, raw_post  # noqa: E402
from test_dt_worktree import FAKE_CLI, GIT_ENV, SCRIPT, git  # noqa: E402

# 正式中台按系统账号的家目录取：统一隔离（_isolation，#212）把 HOME 换成了临时目录，用 ~ 会指到空目录，
# 前后核对就成了空核对、上游源码也找不到（UpstreamParser 会被悄悄跳过）
PROD = _isolation.REAL_HOME / ".agent-autoloop"


def prod_snapshot():
    """正式中台：配置文件的大小和修改时间（不读内容：config.json 里有钥匙）、库里几张表的行数（只读打开）。
    没有正式中台时返回 None。"""
    if not (PROD / "rules.json").exists():
        return None
    snap = {f: [(PROD / f).stat().st_size, (PROD / f).stat().st_mtime_ns] for f in ("rules.json", "config.json")}
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(PROD / 'hub.db'))}?mode=ro", uri=True, timeout=5)
    try:
        for t in ("runs", "cards", "events"):
            try:
                snap[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                snap[t] = None
    finally:
        con.close()
    return snap


def click_card(G, code):
    """模拟门卫：发确认卡（登记消息号）、常浩点「批准派单」（0E-3b 起批准只认门卫卡上的点击）。"""
    (p,) = [x for x in G.cards_to_send() if x["code"] == code]
    G.mark_card(code, f"om_card_{code}")
    return G.decide(p["card_token"], "ok", f"om_card_{code}", f"ev-{code}")


@unittest.skipIf(SKIP, "本机没有 agent-autoloop 命令行")
class ExecGateEndToEnd(HubFixture):
    def setUp(self):
        self.prod_before = prod_snapshot()
        import tempfile
        tmp = tempfile.TemporaryDirectory(prefix="dt-0c-e2e-")
        self.addCleanup(tmp.cleanup)
        r = self.r = Path(tmp.name)
        self.remote = r / "remote/o/dt-sandbox.git"
        seed = r / "seed"
        git("init", "-q", "-b", "main", str(seed))
        (seed / "README.md").write_text("# dt-sandbox\n")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "init", cwd=seed)
        git("clone", "-q", "--bare", str(seed), str(self.remote))
        self.work, self.outbox, self.fake_log = r / "work", r / "outbox", r / "fake.log"
        (self.work / "dt-sandbox").mkdir(parents=True)
        fake = r / "bin/fake-claude"
        fake.parent.mkdir()
        fake.write_text(FAKE_CLI.format(py=sys.executable))
        fake.chmod(0o755)
        self.program_dir = r / "shared-bin"
        self.program_dir.mkdir()
        for ex in G.EXECUTORS:
            (self.program_dir / f"dt-worktree-{ex}").symlink_to(SCRIPT)
        self.status = r / "shared/grants/status.json"
        pf_status = r / "pf-dt-worker.status"                          # 门卫规则在生效（L3 P1-03 的开工闸）
        pf_status.write_text("at=2026-11-20T10:00:00+0800 boot=1795125811 state=ok enabled=yes rules=15 users=dt-worker reloaded=no\n")
        env = {"DT_GRANTS_DB": str(r / "dt/autoloop/grants.db"), "DT_GRANTS_STATUS": str(self.status),
               "DT_PF_STATUS": str(pf_status), "DT_BOOT_TIME": "1795125811",
               "DT_GRANTS_TASKS": str(r / "shared/tasks"), "DT_WORK_ROOT": str(self.work), "DT_OUTBOX": str(self.outbox),
               "DT_WORKTREE_REAL_CLAUDE": str(fake), "DT_WORKTREE_REMOTE": f"file://{r}/remote/{{github}}.git",
               "FAKE_LOG": str(self.fake_log), "DT_OWNER_CHAT_ID": CHAT,
               "DT_PUBLISH_OUTBOX": str(self.outbox), "DT_PUBLISH_MIRRORS": str(r / "mirrors"),
               "DT_PUBLISH_LOG": str(r / "publish.jsonl"), "FAKE_GH_STATE": str(r / "gh"), **GIT_ENV}
        backend = {"remote": f"file://{r}/remote/{{github}}.git", "gh": str(fake_gh.install(r / "ghbin", r / "gh"))}
        p = mock.patch.dict(os.environ, env)          # 先放进环境：临时中台的接单程序（up 起的）要继承这些
        p.start()
        self.addCleanup(p.stop)
        super().setUp()
        self.assertNotEqual(self.home.resolve(), PROD.resolve())
        worker = json.loads((self.home / "config.json").read_text())["worker"]["name"]
        real_rule = C.dispatch_rule

        def rule_here(repo, executor):
            return {**real_rule(repo, executor), "cwd": str(self.work / repo)}
        self.msgs, self.n = {}, 0
        for q in (mock.patch.object(C, "WORKER", worker), mock.patch.object(C, "PROGRAM_DIR", str(self.program_dir)),
                  mock.patch.object(C, "dispatch_rule", side_effect=rule_here),
                  mock.patch.dict(os.environ, {"DT_OWNER_OPEN_ID": GRANT_OWNER}),
                  mock.patch.object(G, "fetch_message", side_effect=self._fetch),
                  mock.patch.object(Rv, "ensure_open"), mock.patch.object(Rv, "admit"),   # 恢复闸放行（闸本身见 0D 测试）
                  mock.patch.object(P, "GATE_USER", __import__("pwd").getpwuid(os.geteuid()).pw_name),
                  mock.patch.object(P, "TEST_BACKEND", backend)):
            q.start()
            self.addCleanup(q.stop)

    def tearDown(self):
        self.assertEqual(prod_snapshot(), self.prod_before, "正式中台被动过")

    def _fetch(self, mid):
        m = self.msgs.get(mid)
        return None if m is None else G.I.normalize(m) | {"text": m["content"]}

    def say(self, text):
        self.n += 1
        mid = f"om_e2emsg{self.n:04d}"
        self.msgs[mid] = raw_msg(mid, text, G.now_tz())
        return mid

    def hub_rows(self, sql, args=()):
        con = sqlite3.connect(f"file:{urllib.parse.quote(str(self.home / 'hub.db'))}?mode=ro", uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            return [dict(x) for x in con.execute(sql, args)]
        finally:
            con.close()

    def setup_dispatch(self):
        C.install(confirm=True, target=self.home)
        C.issue_source_key(confirm=True, target=self.home)
        C.hub("POST", "/v1/rules/dispatch-dt-sandbox-claude/resume", {})      # 只在临时中台里恢复
        code = G.propose("dt-sandbox", "claude", "目标：README 加一行\n交付物：README.md\n验收：多一行\n范围：只改 README.md\n交给谁：Claude", hours=2)["code"]
        return click_card(G, code)["task_id"]

    def test_unreachable_repo_is_queued_for_retry_not_done(self):
        """P1-04：第一轮取不到仓库，启动脚本写「没跑成」→ 中台判 failed、放回排队、5 分钟后重派，不是 done。"""
        hidden = self.remote.with_name("hidden.git")
        self.remote.rename(hidden)
        tid = self.setup_dispatch()
        end = time.monotonic() + 90
        while time.monotonic() < end:
            runs = self.hub_rows("SELECT id, state, attempts, error, not_before FROM runs WHERE lane = ?", (tid,))
            if runs and runs[0]["attempts"] >= 1:
                break
            time.sleep(0.5)
        (run,) = runs
        self.assertEqual((run["state"], run["attempts"]), ("queued", 1), run)
        self.assertIn("取不到", run["error"])
        self.assertGreater(run["not_before"], G.dt.datetime.now(G.dt.timezone.utc).isoformat())   # 排在以后重派
        self.assertEqual([x for x in self.calls() if x["argv"][:1] == ["-p"]], [])               # CLI 没启动
        self.assertFalse((self.outbox / tid).exists())

    def test_in_flight_event_of_a_voided_generation_reaches_the_real_hub_late(self):
        """0D 续审第 4 轮 P2-07（真中台）：第 1 代的续接事件由网关真领走、POST 停在半路（sent=2），这时冻结、记更正、
        常浩私聊恢复到第 2 代，再放行旧 POST。中台本身不核代次，照样收下、排一轮；启动脚本按刷新过的镜像拦下：
        不启动 CLI、本轮以「停下了」结束、不重派；网关把旧事件记成已送到。之后第 2 代的更正照常续接、跑完、交接。"""
        tid = self.setup_dispatch()
        (run,) = self.wait_runs(tid, 1)
        self.assertEqual(run["state"], "done", run)
        gate, posting, box = threading.Event(), threading.Event(), []

        def paused(body):
            posting.set()
            self.assertTrue(gate.wait(60))
            return G.post_event(body)

        t = threading.Thread(target=lambda: box.append(G.resume_dispatch(tid, "redispatch", "中台放弃了", post=paused)))
        t.start()
        self.assertTrue(posting.wait(30))                                      # 已领走、请求停在半路
        with G.tx() as con:
            (old, sent) = con.execute("SELECT event_key, sent FROM hub_events WHERE task_id = ? AND kind = 'redispatch'",
                                      (tid,)).fetchone()
        self.assertEqual(sent, 2)
        code = G.freeze(tid, "推翻")["freeze_code"]
        G.record_resolution(tid, 1, "d-1", "常浩重选，旧的不要了")
        G.unfreeze(self.say(f"恢复 {tid} {code}"))
        self.assertEqual(json.loads(self.status.read_text())["tasks"][tid]["generation"], 2)
        gate.set()                                                             # 旧 POST 放行，到达真中台
        t.join(60)
        self.assertTrue(box and box[0]["send"][0].get("sent"), box)
        with G.tx() as con:
            self.assertEqual(con.execute("SELECT sent FROM hub_events WHERE event_key = ?", (old,)).fetchone()[0], 1)
        runs = self.wait_runs(tid, 2)
        self.assertEqual(runs[-1]["state"], "done", runs[-1])                  # 本轮结束，不重派
        self.assertEqual(runs[-1]["attempts"], runs[0]["attempts"])
        self.assertTrue(runs[-1]["summary"].startswith("停下了"), runs[-1])
        self.assertEqual(len([x for x in self.calls() if x["argv"][:1] == ["-p"]]), 1)   # CLI 没再启动
        cards = self.hub_rows("SELECT title, text FROM cards WHERE lane = ?", (tid,))
        self.assertTrue(any("第 1 代已作废" in (x["text"] or "") for x in cards), cards)
        self.assertEqual(json.loads((self.outbox / tid / "handoff.json").read_text())["generation"], 1)   # 没有新交接
        G.resume_dispatch(tid, "correction", "换代后更正")                     # 第 2 代：不被旧事件挡
        runs = self.wait_runs(tid, 3)
        self.assertEqual(runs[-1]["state"], "done", runs[-1])
        self.assertEqual(len([x for x in self.calls() if x["argv"][:1] == ["-p"]]), 2)
        self.assertEqual(json.loads((self.outbox / tid / "handoff.json").read_text())["generation"], 2)

    def wait_runs(self, lane, n, timeout=90):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            runs = self.hub_rows("SELECT id, state, summary, error, attempts FROM runs WHERE lane = ? ORDER BY id", (lane,))
            if len(runs) >= n and all(x["state"] not in ("queued", "claimed", "retrying") for x in runs):
                return runs
            time.sleep(0.5)
        self.fail(f"中台的轮次没跑完：{self.hub_rows('SELECT id, lane, state, error FROM runs')}；"
                  f"接单日志：{(self.home.parent / 'up.log').read_text()[-1500:]}")

    def calls(self):
        return [json.loads(x) for x in self.fake_log.read_text().splitlines()] if self.fake_log.exists() else []

    def test_dispatch_runs_through_startup_script_and_trusted_publish(self):
        C.install(confirm=True, target=self.home)
        live = C.loaded_rules()["agents"]["dt-claude"]
        self.assertEqual(live["program"], str(self.program_dir / "dt-worktree-claude"))
        C.issue_source_key(confirm=True, target=self.home)
        C.hub("POST", "/v1/rules/dispatch-dt-sandbox-claude/resume", {})      # 只在临时中台里恢复

        code = G.propose("dt-sandbox", "claude", "目标：README 加一行\n交付物：README.md\n验收：多一行\n范围：只改 README.md\n交给谁：Claude", hours=2)["code"]
        tid = click_card(G, code)["task_id"]
        (run,) = self.wait_runs(tid, 1)
        self.assertEqual(run["state"], "done", run)
        (c,) = [x for x in self.calls() if x["argv"][:1] == ["-p"]]
        self.assertEqual(Path(c["cwd"]).resolve(), (self.work / "dt-sandbox/wt" / tid).resolve())
        self.assertIn("【dt-worktree 启动脚本】", c["argv"][1])
        m = json.loads((self.outbox / tid / "handoff.json").read_text())
        self.assertEqual((m["branch"], m["generation"]), (f"claude/{tid}", 1))

        main_before = git("rev-parse", "main", cwd=self.remote)
        self.assertEqual(m["run"], str(run["id"]))                         # 交接包落到的就是中台记的这一轮
        out = P.publish(tid)                                              # 受信发布按真中台库核这一轮（P1-02）
        self.assertTrue(out["pushed"])
        self.assertEqual(git("rev-parse", f"refs/heads/claude/{tid}", cwd=self.remote), m["head"])
        self.assertEqual(git("rev-parse", "main", cwd=self.remote), main_before)

        G.revoke(tid)
        key = (C.source_key_file()).read_text().strip()
        body = G.event_body({"event_key": f"{tid}:g1:rework:9", "kind": "rework", "generation": 1},
                            {"task_id": tid, "repo": "dt-sandbox", "executor": "claude"})
        raw_post(self.url, key, body)                                          # 撤销前残留的事件晚到
        runs = self.wait_runs(tid, 2)
        self.assertEqual(runs[-1]["state"], "done", runs[-1])                 # 本轮结束，不重派
        self.assertEqual(runs[-1]["attempts"], runs[0]["attempts"])            # 没有重试
        self.assertEqual(len([x for x in self.calls() if x["argv"][:1] == ["-p"]]), 1)   # CLI 没再启动
        cards = self.hub_rows("SELECT title, kind, text FROM cards WHERE lane = ?", (tid,))
        self.assertTrue(any(x["title"] == f"任务 {tid} 停下了" and "凭证已撤销" in x["text"] for x in cards), cards)
        with self.assertRaises(P.Rejected):
            P.publish(tid)
        (stopped,) = self.hub_rows("SELECT summary FROM runs WHERE id = ?", (runs[-1]["id"],))
        self.assertTrue(stopped["summary"].startswith("停下了"), stopped)            # 真中台记下的首行
        with G.tx() as con:
            row = dict(G.grant_row(con, tid))
        self.assertIn("挡回去", P.check_run(str(runs[-1]["id"]), row, 1))           # 受信发布不认这一轮（第 2 轮 P1-02）


NODE = shutil.which("node")
APP = PROD / "app"


@unittest.skipIf(not NODE or not (APP / "worker/contract.ts").exists(), "本机没有 node 或上游中台源码")
class UpstreamParser(unittest.TestCase):
    """P2-02：用上游真解析器读启动脚本的输出。CLI 已经交过结果（claude 的 result、codex 的 turn.completed）后，
    启动脚本再追加的停下消息要成为这一轮的结论（done + 通知卡），「没跑成」要判 failed（中台据此重派）。"""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dt_worktree_parse", SCRIPT)
        cls.w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.w)

    def appended(self, executor, text):
        buf = io.StringIO()
        real, sys.stdout = sys.stdout, buf
        try:
            self.w.final_message(executor, text)
        finally:
            sys.stdout = real
        return buf.getvalue()

    def parse(self, executor, stdout, code=0):
        js = ("const [ex, out, code] = JSON.parse(await new Promise(r => { let d = ''; process.stdin.on('data', c => d += c);"
              " process.stdin.on('end', () => r(d)); }));"
              f"const a = await import('{APP}/plugins/' + ex + '/agent.ts');"
              f"const w = await import('{APP}/worker/contract.ts');"
              "const r = a.default.read(out, code, '');"
              "console.log(JSON.stringify({read: r, message: r.text === undefined ? null : w.readMessage(r.text)}));")
        r = subprocess.run([NODE, "--disable-warning=ExperimentalWarning", "--input-type=module", "-e", js],
                           input=json.dumps([executor, stdout, code]), capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    CLAUDE_DONE = "\n".join(json.dumps(x, ensure_ascii=False) for x in (
        {"type": "system", "subtype": "init", "session_id": "s-1"},
        {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "result": "改好了"})) + "\n"
    CODEX_DONE = "\n".join(json.dumps(x, ensure_ascii=False) for x in (
        {"type": "thread.started", "thread_id": "th-1"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "改好了"}},
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}})) + "\n"

    def test_stop_message_appended_after_cli_result_wins(self):
        stop = self.w.stop_text("这张单累计用时到了 120 分钟上限，本轮已停", "t-20261118-abcdef")
        for ex, done in (("claude", self.CLAUDE_DONE), ("codex", self.CODEX_DONE)):
            out = self.parse(ex, done + self.appended(ex, stop))
            self.assertTrue(out["read"]["text"].startswith("停下了："), (ex, out))
            msg = out["message"]
            self.assertEqual(msg["outcome"], "done", ex)
            self.assertNotIn("problem", msg, ex)
            self.assertEqual(msg["receipt"]["notice"][0]["key"], "dt-worktree-stopped", ex)

    def test_stop_and_retry_messages_alone(self):
        for ex in ("claude", "codex"):
            out = self.parse(ex, self.appended(ex, self.w.stop_text("凭证已撤销", "t-20261118-abcdef")))
            self.assertEqual(out["message"]["outcome"], "done", ex)
            out = self.parse(ex, self.appended(ex, "没跑成：取不到 o/dt-sandbox（CalledProcessError）"))
            self.assertEqual(out["message"]["outcome"], "failed", ex)        # 中台据此放回排队重派


if __name__ == "__main__":
    unittest.main()
