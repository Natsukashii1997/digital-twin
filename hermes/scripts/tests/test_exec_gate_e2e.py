"""端到端自测（#205 批 0C 执行门）：临时目录里起一个真的 agent-autoloop 中台（--no-service，随机端口，带它自己的接单程序），
按正式的顺序走一遍：装中台配置（Agent 挂上启动脚本）→ 签 hermes 来源钥匙 → 在临时中台里恢复派单规则 → 网关两步批准、发派单事件
→ 中台的接单程序用 claude 插件启动 dt-worktree（假 claude）→ 中台记本轮结束 → 交接包 → 受信发布推到本地「GitHub」裸仓库。
再撤销凭证后补一个旧派单事件：启动脚本不启动 CLI，中台按「本轮结束」收下停下通知，不自动重派。

只改临时目录里的东西；规则里的 cwd 和接单程序名在测试里换成临时的。每项测试前后核对正式中台（~/.agent-autoloop）的
rules.json、config.json 和库里的轮次 / 卡片 / 事件数没有变（11-17 事故的教训：测试只隔离锁文件，配置误装进了正式中台）。
本机没有 agent-autoloop 命令行（如 CI）时跳过；DT_HUB_E2E=0 也跳过。
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
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
from test_autoloop_grants import CHAT, OWNER as GRANT_OWNER, raw_msg  # noqa: E402
from test_autoloop_hub_e2e import OWNER, SKIP, HubFixture, raw_post  # noqa: E402
from test_dt_worktree import FAKE_CLI, GIT_ENV, SCRIPT, git  # noqa: E402

PROD = Path("~/.agent-autoloop").expanduser()


def prod_snapshot():
    """正式中台：配置文件的哈希、库里几张表的行数（只读打开）。没有正式中台时返回 None。"""
    if not (PROD / "rules.json").exists():
        return None
    snap = {f: hashlib.sha256((PROD / f).read_bytes()).hexdigest() for f in ("rules.json", "config.json")}
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
        env = {"DT_GRANTS_DB": str(r / "dt/autoloop/grants.db"), "DT_GRANTS_STATUS": str(self.status),
               "DT_GRANTS_TASKS": str(r / "shared/tasks"), "DT_WORK_ROOT": str(self.work), "DT_OUTBOX": str(self.outbox),
               "DT_WORKTREE_REAL_CLAUDE": str(fake), "DT_WORKTREE_REMOTE": f"file://{r}/remote/{{github}}.git",
               "FAKE_LOG": str(self.fake_log), "DT_OWNER_CHAT_ID": CHAT,
               "DT_PUBLISH_OUTBOX": str(self.outbox), "DT_PUBLISH_MIRRORS": str(r / "mirrors"),
               "DT_PUBLISH_REMOTE": f"file://{r}/remote/{{github}}.git", "DT_PUBLISH_LOG": str(r / "publish.jsonl"),
               "DT_GH": str(fake_gh.install(r / "ghbin", r / "gh")), "FAKE_GH_STATE": str(r / "gh"), **GIT_ENV}
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
                  mock.patch.object(G, "notify_owner", return_value="om_notice0001")):
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

        code = G.propose("dt-sandbox", "claude", "目标：README 加一行\n范围：只改 README.md", hours=2)["code"]
        tid = G.approve(self.say(f"派单 {code}"))["task_id"]
        (run,) = self.wait_runs(tid, 1)
        self.assertEqual(run["state"], "done", run)
        (c,) = [x for x in self.calls() if x["argv"][:1] == ["-p"]]
        self.assertEqual(Path(c["cwd"]).resolve(), (self.work / "dt-sandbox/wt" / tid).resolve())
        self.assertIn("【dt-worktree 启动脚本】", c["argv"][1])
        m = json.loads((self.outbox / tid / "handoff.json").read_text())
        self.assertEqual((m["branch"], m["generation"]), (f"agent/claude/{tid}", 1))

        main_before = git("rev-parse", "main", cwd=self.remote)
        out = P.publish(tid)
        self.assertTrue(out["pushed"])
        self.assertEqual(git("rev-parse", f"refs/heads/agent/claude/{tid}", cwd=self.remote), m["head"])
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


if __name__ == "__main__":
    unittest.main()
