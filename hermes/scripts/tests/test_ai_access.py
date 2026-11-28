"""隔离自测：AI 接入（P-13）——配置与切换、执行方可用性检查、状态打印、派单按配置选车间、执行方不可用时报错不换家、回执留痕。

假的 claude / codex 命令放在临时 bin 目录，排在 PATH 最前；Hermes 目录、名册、派单根目录、选择文件都换成临时路径。
不碰 ~/.hermes、真实工单、真实模型和外部服务。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_ai_access.py -v
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
REPO_CONFIG = SCRIPTS.parent.parent / "ai_access.json"
HARNESS = HERE / "dispatch_harness.py"

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
if sys.argv[1:3] == ["auth", "status"]:
    ok = os.environ.get("FAKE_CLAUDE_LOGGED", "1") == "1"
    print(json.dumps({"loggedIn": ok}, indent=2))
    sys.exit(0 if ok else 1)
open(os.environ["FAKE_CALLS"], "a").write("claude run\\n")
print(json.dumps({"type": "result", "session_id": "S-fake", "result": "做完了", "is_error": False,
                  "modelUsage": {"claude-fake-model": {}}}))
"""
FAKE_CODEX = """#!/usr/bin/env python3
import os, sys
if sys.argv[1:3] == ["login", "status"]:
    ok = os.environ.get("FAKE_CODEX_LOGGED", "1") == "1"
    print("Logged in using ChatGPT" if ok else "Not logged in", file=sys.stderr)
    sys.exit(0 if ok else 1)
open(os.environ["FAKE_CALLS"], "a").write("codex run\\n")
print('{"type":"thread.started","thread_id":"T-fake"}')
"""


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-ai-access-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
            (self.bin / name).write_text(body)
            (self.bin / name).chmod(0o755)
        self.calls = self.root / "calls.log"
        self.calls.write_text("")
        self.choice = self.root / "choice" / "ai-access.json"
        self.hermes = self.root / "hermes"
        self.hermes.mkdir()
        self.envs = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "DT_AI_ACCESS": str(REPO_CONFIG),
                     "DT_AI_CHOICE": str(self.choice), "HERMES_HOME": str(self.hermes), "FAKE_CALLS": str(self.calls)}

    def module(self, **extra):
        patcher = mock.patch.dict(os.environ, {**self.envs, **extra})
        patcher.start()
        self.addCleanup(patcher.stop)
        return load("ai_access")


class ConfigTest(Base):
    def test_repo_config_is_consistent(self):
        cfg = json.loads(REPO_CONFIG.read_text(encoding="utf-8"))
        allowed = {"本机 CLI", "官方 API", "网页桥", "Hermes 派单", "MCP 开放"}
        for eid, ex in cfg["executors"].items():
            self.assertIn(ex["access"], allowed, eid)
        self.assertIn(cfg["default_worker"], ("claude", "codex"))
        for f in cfg["features"]:
            self.assertTrue(f["reads"] and f["writes"], f["id"])
            if f["executor"] not in ("default", "per-task"):
                self.assertIn(f["executor"], cfg["executors"], f["id"])
        self.assertTrue(any(f["id"] == "workshop" and f["executor"] == "default" for f in cfg["features"]))

    def test_agents_md_lists_every_feature(self):
        agents = (SCRIPTS.parent.parent / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## AI 接入", agents)
        for f in json.loads(REPO_CONFIG.read_text(encoding="utf-8"))["features"]:
            self.assertIn(f"`{f['id']}`", agents, f"AGENTS.md「AI 接入」缺功能 {f['id']}")

    def test_default_then_switch(self):
        aa = self.module()
        self.assertEqual(aa.resolve("workshop")[0], "codex")
        self.assertIn("出厂默认", aa.resolve("workshop")[1])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(aa.main(["set", "default", "claude"]), 0)
        tool, why = aa.resolve("workshop")
        self.assertEqual(tool, "claude")
        self.assertIn("常浩切换过", why)
        saved = json.loads(self.choice.read_text())
        self.assertEqual(saved["default_worker"], "claude")
        self.assertIn("updated_at", saved)

    def test_feature_override_and_back_to_default(self):
        aa = self.module()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(aa.main(["set", "feature", "workshop", "claude"]), 0)
            self.assertEqual(aa.resolve("workshop"), ("claude", "AI 接入里单独指定"))
            self.assertEqual(aa.main(["set", "feature", "workshop", "default"]), 0)
        self.assertEqual(aa.resolve("workshop")[0], "codex")

    def test_set_rejects_bad_values(self):
        aa = self.module()
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()) as err:
            self.assertEqual(aa.main(["set", "default", "deepseek"]), 2)
            self.assertEqual(aa.main(["set", "feature", "report", "claude"]), 2)
            self.assertEqual(aa.main(["set", "feature", "scheduler", "claude"]), 2)
        self.assertIn("不能在这里切换", err.getvalue())
        self.assertFalse(self.choice.exists())

    def test_broken_choice_file_is_an_error_not_a_silent_default(self):
        self.choice.parent.mkdir()
        self.choice.write_text("{坏")
        aa = self.module()
        with self.assertRaises(aa.AccessError):
            aa.resolve("workshop")


class CheckTest(Base):
    def test_states(self):
        aa = self.module()
        self.assertEqual(aa.check("claude")["state"], "可用")
        self.assertEqual(aa.check("codex")["state"], "可用")
        with mock.patch.dict(os.environ, {"FAKE_CLAUDE_LOGGED": "0", "FAKE_CODEX_LOGGED": "0"}):
            self.assertEqual(aa.check("claude")["state"], "未登录")
            self.assertEqual(aa.check("codex")["state"], "未登录")
        with mock.patch.object(aa, "find_bin", return_value=None):
            st = aa.check("claude")
        self.assertEqual(st["state"], "未安装")
        self.assertIn("没有换成另一家", aa.unavailable_msg(st))
        self.assertIn("checked_at", st)

    def test_check_command_exit_code(self):
        aa = self.module()
        with redirect_stdout(io.StringIO()):
            self.assertEqual(aa.main(["check", "codex"]), 0)
            with mock.patch.dict(os.environ, {"FAKE_CODEX_LOGGED": "0"}):
                self.assertEqual(aa.main(["check", "codex"]), 1)


class StatusTest(Base):
    def fake_hermes(self):
        (self.hermes / "config.yaml").write_text(
            "model:\n  default: main-model\n  provider: prov\n  base_url: https://x\n"
            "auxiliary:\n  compression:\n    provider: prov\n    model: aux-model\n"
            "fallback_model:\n  provider: deepseek\n  model: fb-model\n")
        (self.hermes / "gateway_state.json").write_text(json.dumps(
            {"gateway_state": "running", "platforms": {"feishu": {"state": "connected"}}, "updated_at": "2026-11-07T00:00:00"}))
        con = sqlite3.connect(self.hermes / "state.db")
        con.execute("create table sessions (id text, source text, title text, started_at real)")
        con.execute("create table session_model_usage (session_id text, model text, billing_provider text, task text, "
                    "api_call_count int, first_seen real, last_seen real)")
        t = time.time()
        con.execute("insert into sessions values ('cron_x', 'cron', '日报 · x', ?)", (t - 60,))
        con.executemany("insert into session_model_usage values (?,?,?,?,?,?,?)", [
            ("cron_x", "main-model", "prov", "", 3, t - 50, t - 40),
            ("cron_x", "fb-model", "deepseek", "", 2, t - 30, t - 20)])
        con.commit()
        con.close()
        (self.hermes / "cron").mkdir()
        (self.hermes / "cron/usage_audit.jsonl").write_text(json.dumps({"ts": "2026-11-07T00:00", "job_id": "j1",
                                                                         "model": "main-model"}) + "\n")

    def test_status_prints_four_blocks(self):
        self.fake_hermes()
        aa = self.module()
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(aa.main(["status"]), 0)
        out = buf.getvalue()
        for head in ("## 1. 默认执行方与各执行方状态", "## 2. 功能清单", "## 3. 对外开放", "## 4. 数据对象"):
            self.assertIn(head, out)
        self.assertIn("车间默认执行方：**Codex**", out)
        self.assertIn("主模型 main-model（提供方 prov）；回退 fb-model", out)
        self.assertIn("compression aux-model", out)
        self.assertIn("回退 deepseek 调用 2 次", out)
        self.assertIn("已登记的例外", out)
        self.assertNotRegex(out, r"sk-[A-Za-z0-9_-]{20,}")   # 不打印密钥；只认像密钥的写法（worktree 路径里可能有 task-brief 这种字）

    def test_status_json_and_session_models(self):
        self.fake_hermes()
        aa = self.module()
        buf = io.StringIO()
        with redirect_stdout(buf):
            aa.main(["status", "--json"])
        st = json.loads(buf.getvalue())
        self.assertEqual(st["workers"]["claude"]["state"], "可用")
        self.assertEqual(st["hermes"]["state"], "可用")
        self.assertEqual(aa.session_models("日报"), ["main-model · prov", "fb-model · deepseek"])
        self.assertEqual(aa.session_models("周报"), [])


class DispatchTest(Base):
    """真实 dispatch.py（经 dispatch_harness）+ 真实 run_worker.py + 假 claude / codex。"""

    def setUp(self):
        super().setUp()
        repo = self.root / "repo"
        repo.mkdir()
        for args in (["init", "-q", "-b", "main"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                                                     "--allow-empty", "-m", "init"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        (self.root / "roster.json").write_text(json.dumps({"projects": [
            {"id": "fixture", "name": "夹具", "path": str(repo), "main": "main", "write_allowed": False}]}))
        self.task = self.root / "task.md"
        self.task.write_text("# 目标：AI 接入测试\n交付物：无（只读）\n验收：能开单\n边界：不改文件\n交给谁：按派单参数里的执行方\n", encoding="utf-8")

    def dispatch(self, *args, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("H_", "DT_STATE_NOTIFY"))}
        env.update(self.envs, H_ROOT=str(self.root), H_WORKER=str(SCRIPTS / "run_worker.py"),
                   DT_ROSTER=str(self.root / "roster.json"), DT_STATE_DB=str(self.root / "state.db"),
                   DT_PROXY="http://127.0.0.1:9", **extra)
        r = subprocess.run([sys.executable, "-B", str(HARNESS), *args], capture_output=True, text=True, env=env,
                           timeout=120)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def new(self, *extra):
        code, out = self.dispatch("new", "--project", "fixture", "--mode", "read", "--task-file", str(self.task), *extra)
        self.assertEqual(code, 0, out)
        return out["order"]

    def test_no_tool_uses_default_and_switch_takes_effect_next_time(self):
        o = self.new()
        self.assertEqual((o["tool"], o["tool_source"]), ("codex", "AI 接入默认执行方（出厂默认）"))
        with redirect_stdout(io.StringIO()):
            self.module().main(["set", "default", "claude"])
        o = self.new()
        self.assertEqual(o["tool"], "claude")
        self.assertIn("常浩切换过", o["tool_source"])
        code, out = self.dispatch("run", o["no"])
        self.assertEqual(code, 0, out)
        rc = out["receipt"]
        self.assertEqual((rc["tool"], rc["executor"], rc["access"], rc["model"]),
                         ("claude", "Claude Code", "本机 CLI", "claude-fake-model"))
        self.assertIn("常浩切换过", rc["chosen_by"])
        self.assertEqual(self.calls.read_text(), "claude run\n")

    def test_named_tool_wins(self):
        o = self.new("--tool", "claude")
        self.assertEqual((o["tool"], o["tool_source"]), ("claude", "点名"))

    def test_unavailable_executor_stops_without_switching(self):
        o = self.new("--tool", "claude")
        code, out = self.dispatch("run", o["no"], FAKE_CLAUDE_LOGGED="0")
        self.assertEqual(code, 1)
        self.assertTrue(out["unavailable"])
        self.assertIn("没有换成另一家", out["msg"])
        self.assertEqual(out["receipt"]["refused"], "executor_unavailable")
        self.assertEqual(self.calls.read_text(), "", "不可用时不能跑任何车间，也不能换成 Codex")
        order = json.loads((self.root / "dispatch" / o["no"] / "order.json").read_text())
        self.assertEqual(order["exit_code"], 4)

    def test_unreadable_config_does_not_guess(self):
        code, out = self.dispatch("new", "--project", "fixture", "--mode", "read", "--task-file", str(self.task),
                                  DT_AI_ACCESS=str(self.root / "missing.json"))
        self.assertEqual(code, 1)
        self.assertIn("没有开单", out["msg"])
        self.assertFalse((self.root / "dispatch").exists() and any((self.root / "dispatch").glob("D*")))


if __name__ == "__main__":
    unittest.main()
