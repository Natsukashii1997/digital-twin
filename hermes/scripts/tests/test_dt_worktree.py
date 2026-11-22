"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

ops/dt_worktree.py（工人侧启动脚本，#205 批 0C）：凭证镜像和任务单由真实的 0B-1 网关（autoloop_grants）在临时目录里写；
「GitHub」是临时目录里的本地裸仓库（file://），CLI 是记录参数的假程序。启动脚本用 macOS 自带的 /usr/bin/python3（3.9）
以子进程跑，和 dt-worker 上一样。不联网、不碰 ~/.agent-autoloop、~/dt-work、/Users/Shared/dt-autoloop。

验收（方案第 9 节 0C）：续接后范围不扩大；凭证失效不再继续；只交本单分支（推分支在受信发布，test_autoloop_publish.py）。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import shutil
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_grants as G  # noqa: E402
from test_autoloop_grants import Base  # noqa: E402
from prod_guard import setUpModule, tearDownModule  # noqa: E402,F401  批 0D：整个模块指到临时目录

SCRIPT = HERE.parents[2] / "ops/dt_worktree.py"
PY = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

FAKE_CLI = r'''#!{py}
import json, os, signal, subprocess, sys, time
from pathlib import Path
log = Path(os.environ["FAKE_LOG"])
with log.open("a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid(),
                        "cache": os.environ.get("XDG_CACHE_HOME"), "npm": os.environ.get("npm_config_cache")}}) + "\n")
if sys.argv[1:3] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True}}))
    sys.exit(0)
mode = os.environ.get("FAKE_MODE", "edit")
if mode in ("orphan", "orphan_sleep"):          # 留一个不理 SIGTERM 的后代进程，pid 写进 FAKE_PID
    code = ("import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "open(os.environ['FAKE_PID'], 'w').write(str(os.getpid())); time.sleep(120)")
    subprocess.Popen([sys.executable, "-c", code])
    for _ in range(100):
        if os.path.exists(os.environ["FAKE_PID"]):
            break
        time.sleep(0.05)
if mode == "orphan_sleep":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode in ("edit", "revoke", "branch", "sleep", "orphan", "orphan_sleep"):
    with open("README.md", "a") as f:
        f.write("一行改动 %d\n" % time.time_ns())
if mode == "workflow":
    Path(".github/workflows").mkdir(parents=True, exist_ok=True)
    Path(".github/workflows/ci.yml").write_text("on: push\n")
if mode == "revoke":
    p = Path(os.environ["DT_GRANTS_STATUS"])
    doc = json.loads(p.read_text())
    for t in doc["tasks"].values():
        t["status"] = "revoked"
    p.write_text(json.dumps(doc))
if mode == "branch":
    subprocess.run(["git", "checkout", "-q", "-b", "other"], check=True)
if os.environ.get("FAKE_MODEL_LOG"):            # 像真的 CLI 一样往家目录里写会话记录（写法见 MODEL_WRITER）
    exec(Path(os.environ["FAKE_MODEL_WRITER"]).read_text())
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s-1"}}), flush=True)
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "result": "改好了"}}),
      flush=True)
if mode in ("sleep", "orphan_sleep"):           # 已经交了结果还没退出（中台会把本脚本追加的停下消息当最后一条）
    time.sleep(30)
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
'''


MODEL_WRITER = r'''
import datetime as _dt
_now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
_home, _here = Path(os.environ["HOME"]), os.getcwd()
_model = os.environ.get("FAKE_MODEL", "claude-opus-5-5")
if os.environ["FAKE_MODEL_LOG"] == "claude":     # ~/.claude/projects/<目录名>/<会话号>.jsonl，只有本轮、本单目录的回复算数
    _a = sys.argv[1:]
    _sid = _a[_a.index("--session-id") + 1] if "--session-id" in _a else _a[_a.index("--resume") + 1]
    _d = _home / ".claude/projects/-whatever-the-cli-names-it"
    _d.mkdir(parents=True, exist_ok=True)
    _rows = [{"type": "assistant", "timestamp": "2020-01-01T00:00:00.000Z", "cwd": _here, "message": {"model": "old-model"}},
             {"type": "assistant", "timestamp": _now, "cwd": "/elsewhere", "message": {"model": "elsewhere-model"}},
             {"type": "assistant", "timestamp": _now, "cwd": _here, "message": {"model": "<synthetic>"}},
             {"type": "user", "timestamp": _now, "cwd": _here, "message": {"model": "user-row"}},
             "这一行不是 JSON",
             *[{"type": "assistant", "timestamp": _now, "cwd": _here, "message": {"model": _m}} for _m in _model.split("|")]]
    with (_d / (_sid + ".jsonl")).open("a") as _f:
        for _r in _rows:
            _f.write((_r if isinstance(_r, str) else json.dumps(_r)) + "\n")
else:                                            # ~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl 里的 turn_context
    _d = _home / ".codex/sessions" / os.environ.get("FAKE_CODEX_DAY", time.strftime("%Y/%m/%d"))
    _d.mkdir(parents=True, exist_ok=True)
    _a = sys.argv[1:]
    _thread = _a[-2] if "resume" in _a[:2] else "mine"
    (_d / ("rollout-x-" + _thread + ".jsonl")).write_text("\n".join(json.dumps(x) for x in [
        {"type": "session_meta", "timestamp": _now, "payload": {"cwd": _here, "id": "x"}},
        {"type": "turn_context", "timestamp": _now, "payload": {"cwd": _here, "model": _model}}]) + "\n")
    (_d / "rollout-other.jsonl").write_text(json.dumps(
        {"type": "turn_context", "timestamp": _now, "payload": {"cwd": "/elsewhere", "model": "elsewhere-model"}}) + "\n")
'''


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT_ENV}).stdout.strip()


class WorkerBase(Base):
    def setUp(self):
        super().setUp()
        r = self.root
        self.remote = r / "remote/o/dt-sandbox.git"
        seed = r / "seed"
        git("init", "-q", "-b", "main", str(seed))
        (seed / "README.md").write_text("# dt-sandbox\n")
        (seed / "ops").mkdir()
        (seed / "ops/run.sh").write_text("echo hi\n")               # 高风险路径上的已有文件（发布侧历史检查用）
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "init", cwd=seed)
        git("clone", "-q", "--bare", str(seed), str(self.remote))
        self.work = r / "work"
        self.cwd = self.work / "dt-sandbox"
        self.cwd.mkdir(parents=True)
        self.outbox = r / "outbox"
        self.fake_log = r / "fake.log"
        fake = r / "bin/fake-claude"
        fake.parent.mkdir()
        fake.write_text(FAKE_CLI.format(py=sys.executable))
        fake.chmod(0o755)
        self.wenv = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(r / "home"),
                     "DT_GRANTS_STATUS": str(self.status), "DT_GRANTS_TASKS": str(self.tasks),
                     "DT_WORK_ROOT": str(self.work), "DT_OUTBOX": str(self.outbox),
                     "DT_WORKTREE_REAL_CLAUDE": str(fake), "DT_WORKTREE_REAL_CODEX": str(fake),
                     "DT_WORKTREE_REMOTE": f"file://{r}/remote/{{github}}.git", "FAKE_LOG": str(self.fake_log),
                     "AGENT_AUTOLOOP_RUN": "7", **GIT_ENV}
        self.now = G.now_tz()

    def new_live_task(self, executor="claude"):
        return self.new_task(when=self.now, executor=executor)

    def dispatch_event(self, tid, gen=1, executor="claude", repo="dt-sandbox", kind="start", source="hermes"):
        return {"id": f"{tid}:g{gen}:{kind}", "source": source, "type": f"dispatch.{repo}.{executor}.write",
                "subject": tid, "detail": json.dumps({"task_id": tid, "generation": gen, "kind": kind})}

    def answer_event(self, tid):
        return {"id": "card:9", "source": "hermes", "type": "card.answered", "subject": tid}

    def turn_env(self, events, executor, env):
        """每一轮一个新的轮次号（像中台一样），记下它属于哪张单、什么事件、什么时候排进来（发布侧测试按它假装中台）。"""
        self.turn_no = getattr(self, "turn_no", 100) + 1
        ctx = self.root / f"ctx-{self.turn_no}.json"
        ctx.write_text(json.dumps({"events": events, "answers": []}))
        run = env.pop("AGENT_AUTOLOOP_RUN", str(self.turn_no))
        self.__dict__.setdefault("turn_runs", {})[run] = {
            "executor": executor, "events": events,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
        return {**self.wenv, "AGENT_AUTOLOOP_CONTEXT": str(ctx), "AGENT_AUTOLOOP_RUN": run, **env}

    def run_turn(self, events, executor="claude", args=("-p", "做事", "--session-id", "s-1"), cwd=None, **env):
        full = self.turn_env(events, executor, env)
        before = self.calls()
        r = subprocess.run([PY, "-B", str(SCRIPT), executor, *args], cwd=cwd or self.cwd, capture_output=True,
                           text=True, timeout=120, env=full)
        self.turn_runs[full["AGENT_AUTOLOOP_RUN"]]["summary"] = self.summary_of(r.stdout)
        return r, self.calls()[len(before):]

    @staticmethod
    def summary_of(stdout):
        """像中台一样取这一轮最后一条消息的首行（claude 格式）。"""
        texts = [json.loads(x).get("result") for x in stdout.splitlines() if x.strip().startswith("{")]
        texts = [t for t in texts if isinstance(t, str)]
        return next((x.strip() for x in (texts[-1] if texts else "").splitlines() if x.strip()), "")

    def start_turn(self, events, executor="claude", **env):
        """后台起一轮（取消测试用），等假 CLI 开跑后返回进程。"""
        full = self.turn_env(events, executor, env)
        n = len(self.calls())
        proc = subprocess.Popen([PY, "-B", str(SCRIPT), executor, "-p", "做事"], cwd=self.cwd, env=full,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(300):
            if len(self.calls()) > n and (not env.get("FAKE_PID") or Path(env["FAKE_PID"]).exists()):
                break
            time.sleep(0.05)
        time.sleep(0.5)
        return proc

    def calls(self):
        if not self.fake_log.exists():
            return []
        return [json.loads(x) for x in self.fake_log.read_text().splitlines()]

    def final(self, r):
        return json.loads(r.stdout.strip().splitlines()[-1])

    def assert_stopped(self, r, calls, words):
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(calls, [], "准入不过就不该启动 CLI")
        msg = self.final(r)
        self.assertEqual((msg["type"], msg["subtype"]), ("result", "success"))
        self.assertTrue(msg["result"].startswith("停下了："), msg["result"])
        self.assertIn(words, msg["result"])
        self.assertIn('"key": "dt-worktree-stopped"', msg["result"])

    def handoff(self, tid):
        p = self.outbox / tid / "handoff.json"
        return json.loads(p.read_text()) if p.exists() else None

    def wt(self, tid):
        return self.work / "dt-sandbox/wt" / tid

    def with_model_log(self, kind, model=None):
        """让假 CLI 写一份会话记录：kind 是 claude 或 codex。"""
        w = self.root / "model_writer.py"
        w.write_text(MODEL_WRITER)
        return {"FAKE_MODEL_LOG": kind, "FAKE_MODEL_WRITER": str(w), **({"FAKE_MODEL": model} if model else {})}

    def patch_mirror(self, tid, **fields):
        doc = json.loads(self.status.read_text())
        doc["tasks"][tid].update(fields)
        self.status.write_text(json.dumps(doc))


class FirstTurnAndResume(WorkerBase):
    def test_first_turn_builds_worktree_runs_cli_commits_and_hands_off(self):
        tid = self.new_live_task()
        r, calls = self.run_turn([self.dispatch_event(tid)])
        self.assertEqual(r.returncode, 0, r.stderr)
        (c,) = calls
        self.assertEqual(Path(c["cwd"]).resolve(), self.wt(tid).resolve())
        self.assertEqual(c["argv"][0], "-p")                                       # 参数原样交给 CLI，提示词末尾补说明
        self.assertTrue(c["argv"][1].startswith("做事\n\n【dt-worktree 启动脚本】"))
        self.assertIn("不要 git push", c["argv"][1])
        self.assertEqual(c["argv"][2:], ["--session-id", "s-1"])
        self.assertIn(f"/.cache/{tid}/", c["cache"])                               # 每张单缓存分开
        self.assertIn(f"/.cache/{tid}/", c["npm"])
        lines = r.stdout.strip().splitlines()                                     # 输出原样，不混本脚本的话
        self.assertEqual([json.loads(x)["type"] for x in lines], ["system", "result"])
        branch = f"claude/{tid}"
        self.assertEqual(git("symbolic-ref", "HEAD", cwd=self.wt(tid)), f"refs/heads/{branch}")
        self.assertIn(tid, git("log", "-1", "--format=%s", cwd=self.wt(tid)))
        m = self.handoff(tid)
        main_sha = git("rev-parse", "main", cwd=self.remote)
        self.assertEqual((m["task_id"], m["generation"], m["branch"], m["base"], m["executor"], m["repo"]),
                         (tid, 1, branch, main_sha, "claude", "dt-sandbox"))
        self.assertEqual(m["head"], git("rev-parse", "HEAD", cwd=self.wt(tid)))
        heads = git("bundle", "list-heads", str(self.outbox / tid / m["bundle"]), cwd=self.wt(tid)).splitlines()
        self.assertEqual(heads, [f"{m['head']} refs/heads/{branch}"])             # 包里只有本单分支
        state = json.loads((self.work / ".state" / f"{tid}.json").read_text())
        self.assertEqual((state["generation"], state["running"]), (1, None))
        self.assertEqual(oct((self.work / ".state" / f"{tid}.json").stat().st_mode & 0o777), "0o600")

    def test_exit_code_is_passed_through(self):
        tid = self.new_live_task()
        r, _ = self.run_turn([self.dispatch_event(tid)], FAKE_EXIT="3")
        self.assertEqual(r.returncode, 3)

    def test_resume_goes_back_to_same_folder_and_branch(self):
        tid = self.new_live_task()
        self.run_turn([self.dispatch_event(tid)])
        first = self.handoff(tid)["head"]
        r, calls = self.run_turn([self.answer_event(tid)], args=("-p", "接着做", "--resume", "s-1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        (c,) = calls
        self.assertEqual(Path(c["cwd"]).resolve(), self.wt(tid).resolve())
        self.assertEqual(c["argv"][-2:], ["--resume", "s-1"])
        m = self.handoff(tid)
        self.assertNotEqual(m["head"], first)
        self.assertEqual(git("rev-parse", "HEAD~1", cwd=self.wt(tid)), first)   # 接着上一轮，不换基线
        self.assertEqual(m["base"], git("rev-parse", "main", cwd=self.remote))

    def test_resume_keeps_base_even_if_main_moved(self):
        tid = self.new_live_task()
        self.run_turn([self.dispatch_event(tid)])
        base = self.handoff(tid)["base"]
        seed = self.root / "seed"
        (seed / "other.txt").write_text("x")
        git("add", "-A", cwd=seed)
        git("commit", "-q", "-m", "main 前进", cwd=seed)
        git("push", "-q", str(self.remote), "main", cwd=seed)
        self.run_turn([self.answer_event(tid)])
        self.assertEqual(self.handoff(tid)["base"], base)

    def test_no_change_no_handoff(self):
        tid = self.new_live_task()
        r, _ = self.run_turn([self.dispatch_event(tid)], FAKE_MODE="none")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(self.handoff(tid))

    def test_codex_prompt_note_goes_to_last_argument(self):
        tid = self.new_live_task(executor="codex")
        args = ("exec", "--json", "-c", 'sandbox_mode="workspace-write"', "做事")
        r, (c,) = self.run_turn([self.dispatch_event(tid, executor="codex")], executor="codex", args=args)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(c["argv"][:-1], list(args[:-1]))
        self.assertTrue(c["argv"][-1].startswith("做事\n\n【dt-worktree 启动脚本】"))
        self.assertIn(f"codex/{tid}", c["argv"][-1])

    def test_codex_wrapper_uses_codex_message_format_when_stopping(self):
        tid = self.new_live_task(executor="codex")
        G.revoke(tid, now=self.now)
        r, calls = self.run_turn([self.dispatch_event(tid, executor="codex")], executor="codex")
        self.assertEqual(calls, [])
        lines = [json.loads(x) for x in r.stdout.strip().splitlines()]
        self.assertEqual([x["type"] for x in lines], ["item.completed", "turn.completed"])
        self.assertTrue(lines[0]["item"]["text"].startswith("停下了：凭证已撤销"))


class WorkerModel(WorkerBase):
    """P-13 留痕（#195 缺口 2）：这一轮实际用的模型从 CLI 自己的会话记录读出，跟着交接清单走。"""

    def test_claude_model_comes_from_its_own_session_log(self):
        tid = self.new_live_task()
        r, _ = self.run_turn([self.dispatch_event(tid)], args=("-p", "做事", "--session-id", "s-1", "--model", "opus"),
                             **self.with_model_log("claude", "claude-opus-5-5[1m]"))
        self.assertEqual(r.returncode, 0, r.stderr)
        m = self.handoff(tid)                          # 旧的、别的目录的、<synthetic>、不是回复的、坏行都不算；会话记录优先于参数
        self.assertEqual((m["schema"], m["model"], m["model_from"]), (2, "claude-opus-5-5[1m]", "transcript"))
        self.assertIn("这一轮用的模型：claude-opus-5-5[1m]", (self.work / ".state" / f"{tid}.log").read_text())

    def test_codex_model_comes_from_turn_context_of_this_folder(self):
        tid = self.new_live_task(executor="codex")
        r, _ = self.run_turn([self.dispatch_event(tid, executor="codex")], executor="codex",
                             args=("exec", "--json", "做事"), **self.with_model_log("codex", "gpt-6.1-sol"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.handoff(tid)["model"], self.handoff(tid)["model_from"]), ("gpt-6.1-sol", "transcript"))

    def test_falls_back_to_the_launch_argument_then_to_not_recorded(self):
        tid = self.new_live_task()
        self.run_turn([self.dispatch_event(tid)], args=("-p", "做事", "--session-id", "s-1", "--model", "claude-sonnet-5-5"))
        self.assertEqual((self.handoff(tid)["model"], self.handoff(tid)["model_from"]), ("claude-sonnet-5-5", "args"))
        other = self.new_live_task(executor="codex")
        self.run_turn([self.dispatch_event(other, executor="codex")], executor="codex", args=("exec", "--json", "做事"))
        self.assertEqual((self.handoff(other)["model"], self.handoff(other)["model_from"]), (None, None))   # 不猜

    def test_resumed_sessions_and_several_models(self):
        tid = self.new_live_task()
        self.run_turn([self.dispatch_event(tid)], args=("-p", "做事", "--resume", "s-9"),
                      **self.with_model_log("claude", "m1|m2|m3|m4"))
        self.assertEqual((self.handoff(tid)["model"], self.handoff(tid)["model_from"]), ("m1,m2,m3", "transcript"))
        self.assertIn("只记了前 3 个", (self.work / ".state" / f"{tid}.log").read_text())
        other = self.new_live_task(executor="codex")                 # Codex 续接：写回会话开始那天的旧文件
        self.run_turn([self.dispatch_event(other, executor="codex")], executor="codex",
                      args=("exec", "resume", "--json", "th-123", "做事"),
                      **self.with_model_log("codex", "gpt-6.1-sol"), FAKE_CODEX_DAY="2026/05/28")
        self.assertEqual((self.handoff(other)["model"], self.handoff(other)["model_from"]), ("gpt-6.1-sol", "transcript"))

    def test_odd_model_names_are_not_recorded(self):
        tid = self.new_live_task()
        r, _ = self.run_turn([self.dispatch_event(tid)], **self.with_model_log("claude", "x; rm -rf / `whoami`"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.handoff(tid)["model"], self.handoff(tid)["model_from"]), (None, None))


class ModelReading(unittest.TestCase):
    """读会话记录本身：工人侧的文件只当资料，管道、链接、超长行、超大文件都不能卡住或撑爆收尾。"""

    def setUp(self):
        import importlib.util
        import tempfile
        tmp = tempfile.TemporaryDirectory(prefix="dt-model-read-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        spec = importlib.util.spec_from_file_location("dt_worktree_models", SCRIPT)
        self.w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.w)
        self.place = self.root / "wt"
        self.place.mkdir()
        self.logs = self.root / "claude/projects/-x"
        self.logs.mkdir(parents=True)
        p = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root / "claude")})
        p.start()
        self.addCleanup(p.stop)
        self.since = time.time()

    def row(self, model, cwd=None):
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return json.dumps({"type": "assistant", "timestamp": now, "cwd": str(cwd or self.place), "message": {"model": model}})

    def used(self):
        return self.w.model_used("claude", ["-p", "x", "--session-id", "s-1", "--model", "fallback"], str(self.place), self.since)

    def test_fifo_and_symlink_are_skipped_without_hanging(self):
        os.mkfifo(self.logs / "s-1.jsonl")
        t = time.time()
        self.assertEqual(self.used(), ("fallback", "args"))
        self.assertLess(time.time() - t, 2)
        os.unlink(self.logs / "s-1.jsonl")
        real = self.root / "real.jsonl"
        real.write_text(self.row("linked") + "\n")
        os.symlink(real, self.logs / "s-1.jsonl")
        self.assertEqual(self.used(), ("fallback", "args"))

    def test_subfolder_long_lines_and_only_the_tail(self):
        sub = self.place / "pkg"
        sub.mkdir()
        f = self.logs / "s-1.jsonl"
        f.write_text(self.row("early") + "\n" + "x" * 5000 + "\n" + self.row("late", sub) + "\n")
        with mock.patch.object(self.w, "LOG_LINE", 1024):
            self.assertEqual(self.used(), ("early,late", "transcript"))       # 超长行跳过；子目录里的也算
        with mock.patch.object(self.w, "LOG_TAIL", 600):
            self.assertEqual(self.used(), ("late", "transcript"))             # 只读最后一段


class ScopeDoesNotGrow(WorkerBase):
    """验收 1：续接后范围不扩大。"""

    def setUp(self):
        super().setUp()
        self.tid = self.new_live_task()
        self.run_turn([self.dispatch_event(self.tid)])

    def test_task_file_scope_changed(self):
        p = self.tasks / f"{self.tid}.md"
        p.write_text(p.read_text().replace("只改 README.md", "顺便升级依赖"))
        r, calls = self.run_turn([self.answer_event(self.tid)])
        self.assert_stopped(r, calls, "五要素和凭证记的范围对不上")

    def test_scope_rewritten_consistently_still_differs_from_first_turn(self):
        p = self.tasks / f"{self.tid}.md"
        new_scope = "目标：改 CI\n范围：.github/workflows"
        text = p.read_text()
        head, _, _ = text.partition("\n## 五要素\n\n")
        h = G.sha256(new_scope)
        head = "\n".join(f"范围哈希：{h}" if x.startswith("范围哈希：") else x for x in head.splitlines())
        p.write_text(f"{head}\n## 五要素\n\n{new_scope}\n")
        self.patch_mirror(self.tid, scope_hash=h)
        r, calls = self.run_turn([self.answer_event(self.tid)])
        self.assert_stopped(r, calls, "续接时范围、仓库或执行方和第一轮不一样")

    def test_other_executor_event_is_not_accepted(self):
        r, calls = self.run_turn([self.dispatch_event(self.tid, executor="codex")])
        self.assert_stopped(r, calls, "不认识的事件类型")

    def test_other_repo_folder_is_refused(self):
        other = self.work / "agent-autoloop"
        other.mkdir()
        r, calls = self.run_turn([self.dispatch_event(self.tid, repo="agent-autoloop")], cwd=other)
        self.assert_stopped(r, calls, "凭证的执行方、仓库或规则对不上")

    def test_folder_outside_work_root_is_refused(self):
        r, calls = self.run_turn([self.dispatch_event(self.tid)], cwd=self.root / "seed")
        self.assert_stopped(r, calls, "派单目录不对")

    def test_agent_switching_branch_is_not_handed_off_and_next_turn_stops(self):
        before = self.handoff(self.tid)["head"]
        r, _ = self.run_turn([self.answer_event(self.tid)], FAKE_MODE="branch")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.handoff(self.tid)["head"], before)        # 别的分支上的提交不进交接包
        r, calls = self.run_turn([self.answer_event(self.tid)])
        self.assert_stopped(r, calls, "工作目录不在本单分支上")


class GrantMustBeValid(WorkerBase):
    """验收 2：凭证失效就不再继续。"""

    def test_frozen_revoked_expired_done(self):
        for how, words in (("freeze", "凭证已冻结"), ("revoke", "凭证已撤销"), ("complete", "任务已完成")):
            tid = self.new_live_task()
            {"freeze": lambda: G.freeze(tid, "推翻", now=self.now), "revoke": lambda: G.revoke(tid, now=self.now),
             "complete": lambda: G.complete(tid, now=self.now)}[how]()
            r, calls = self.run_turn([self.dispatch_event(tid)])
            self.assert_stopped(r, calls, words)
        tid = self.new_live_task()
        self.patch_mirror(tid, valid_until=G.iso(self.now - dt.timedelta(minutes=1)))
        r, calls = self.run_turn([self.dispatch_event(tid)])
        self.assert_stopped(r, calls, "凭证已过期")

    def test_no_edit_action_or_missing_task(self):
        tid = self.new_live_task()
        self.patch_mirror(tid, actions=["open_pr"])
        r, calls = self.run_turn([self.dispatch_event(tid)])
        self.assert_stopped(r, calls, "凭证不允许改文件")
        r, calls = self.run_turn([self.dispatch_event("t-20260101-abcdef")])
        self.assert_stopped(r, calls, "凭证镜像里没有这张单")

    def test_generation_bump_stops_old_work_and_new_generation_runs(self):
        tid = self.new_live_task()
        self.run_turn([self.dispatch_event(tid)])
        code = G.freeze(tid, "推翻", now=self.now)["freeze_code"]
        self.unfreeze_ok(tid, code, when=self.now)
        self.assertEqual(self.mirror()[tid]["generation"], 2)
        r, calls = self.run_turn([self.dispatch_event(tid, gen=1)])               # 旧代次的排队轮次
        self.assert_stopped(r, calls, "第 1 代已作废")
        r, calls = self.run_turn([self.answer_event(tid)])                        # 旧代次的卡片回答 / 复查
        self.assert_stopped(r, calls, "第 1 代已作废")
        r, calls = self.run_turn([self.dispatch_event(tid, gen=2, kind="correction")])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.handoff(tid)["generation"], 2)

    def test_revoked_during_run_commits_but_no_handoff(self):
        tid = self.new_live_task()
        r, calls = self.run_turn([self.dispatch_event(tid)], FAKE_MODE="revoke")
        self.assertEqual((r.returncode, len(calls)), (0, 1))
        self.assertIsNone(self.handoff(tid))
        self.assertIn(tid, git("log", "-1", "--format=%s", cwd=self.wt(tid)))    # 工作留在本地分支，不丢

    def test_events_must_come_from_gateway_for_one_task(self):
        tid = self.new_live_task()
        r, calls = self.run_turn([self.dispatch_event(tid, source="github")])
        self.assert_stopped(r, calls, "不是派单网关发来的事件")
        other = self.new_live_task()
        r, calls = self.run_turn([self.dispatch_event(tid), self.dispatch_event(other)])
        self.assert_stopped(r, calls, "不属于同一张任务单")
        r, calls = self.run_turn([self.answer_event(tid)])
        self.assert_stopped(r, calls, "没有派单记录")
        bad = self.dispatch_event(tid)
        bad["detail"] = json.dumps({"task_id": other, "generation": 1})
        r, calls = self.run_turn([bad])
        self.assert_stopped(r, calls, "派单事件的单号或代次不对")
        r, calls = self.run_turn([])
        self.assert_stopped(r, calls, "这一轮没有派单事件")


class TimeBudget(WorkerBase):
    def test_run_is_stopped_at_budget_and_next_turn_refused(self):
        tid = self.new_live_task()
        r, calls = self.run_turn([self.dispatch_event(tid)], FAKE_MODE="sleep", DT_WORKTREE_BUDGET="5")
        self.assertEqual((r.returncode, len(calls)), (0, 1))
        lines = [json.loads(x) for x in r.stdout.strip().splitlines() if x.strip()]
        self.assertEqual(lines[-2]["result"], "改好了")                  # CLI 已经交过结果，停下消息追加在最后
        self.assertIn("累计用时到了", lines[-1]["result"])
        self.assertIsNone(self.handoff(tid))                              # 超时不交接（审查 P1-03）
        self.assertIn(tid, git("log", "-1", "--format=%s", cwd=self.wt(tid)))   # 改动在本地提交，不丢
        state = json.loads((self.work / ".state" / f"{tid}.json").read_text())
        self.assertEqual(state["turns"][-1]["outcome"], "over_budget")
        state = json.loads((self.work / ".state" / f"{tid}.json").read_text())
        self.assertGreaterEqual(state["seconds_used"], 5)
        r, calls = self.run_turn([self.answer_event(tid)], DT_WORKTREE_BUDGET="5")
        self.assert_stopped(r, calls, "上限")

    def test_killed_run_is_counted_from_heartbeat(self):
        tid = self.new_live_task()
        self.run_turn([self.dispatch_event(tid)])
        p = self.work / ".state" / f"{tid}.json"
        s = json.loads(p.read_text())
        s["running"] = {"started": 1000, "last_seen": 1000 + 7200, "run": "6"}   # 上一轮被强杀、没结账
        p.write_text(json.dumps(s))
        r, calls = self.run_turn([self.answer_event(tid)])
        self.assert_stopped(r, calls, "上限")


class CancelAndProcessTree(WorkerBase):
    """审查 P1-03：取消、超时不交接；CLI 整组（含不理 SIGTERM 的后代）清干净后才提交。"""

    def alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def test_cancel_commits_locally_but_never_hands_off(self):
        tid = self.new_live_task()
        proc = self.start_turn([self.dispatch_event(tid)], FAKE_MODE="sleep")
        cli_pid = self.calls()[-1]["pid"]
        proc.send_signal(signal.SIGTERM)                                  # 接单程序取消这一轮
        proc.communicate(timeout=30)
        self.assertFalse(self.alive(cli_pid))
        self.assertIsNone(self.handoff(tid))
        self.assertNotIn("这一轮用的模型", (self.work / ".state" / f"{tid}.log").read_text())   # 取消的轮次不读模型
        self.assertIn(tid, git("log", "-1", "--format=%s", cwd=self.wt(tid)))
        state = json.loads((self.work / ".state" / f"{tid}.json").read_text())
        self.assertEqual((state["turns"][-1]["outcome"], state["turns"][-1]["clean"]), ("cancelled", True))

    def test_descendant_ignoring_sigterm_is_killed_before_commit(self):
        tid = self.new_live_task()
        pidf = self.root / "orphan.pid"
        r, _ = self.run_turn([self.dispatch_event(tid)], FAKE_MODE="orphan", FAKE_PID=str(pidf))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(self.alive(int(pidf.read_text())))               # CLI 退出后整组清掉
        self.assertIsNotNone(self.handoff(tid))                            # 正常结束照常交接

    def test_cancel_with_stubborn_tree_is_killed_within_grace(self):
        tid = self.new_live_task()
        pidf = self.root / "orphan2.pid"
        proc = self.start_turn([self.dispatch_event(tid)], FAKE_MODE="orphan_sleep", FAKE_PID=str(pidf))
        cli_pid = self.calls()[-1]["pid"]
        t0 = time.time()
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=30)
        self.assertLess(time.time() - t0, 10)                              # 赶在接单程序 10 秒强杀之前
        self.assertFalse(self.alive(cli_pid))
        self.assertFalse(self.alive(int(pidf.read_text())))
        self.assertIsNone(self.handoff(tid))


GIT_STOP = """#!/bin/sh
# 假 git：参数里出现 $FAKE_GIT_STOP_ON（commit 或 bundle）时，先给调用它的启动脚本发 SIGTERM（模拟中台在提交、打包时取消）
for a in "$@"; do
  if [ "$a" = "$FAKE_GIT_STOP_ON" ]; then kill -TERM "$PPID"; sleep 0.2; fi
done
exec {git} "$@"
"""


class LateCancel(WorkerBase):
    """审查第 2 轮 P1-03：CLI 已经退出、启动脚本正在提交或打交接包时才到的取消，也不交接，并给中台明确结论。"""

    def setUp(self):
        super().setUp()
        d = self.root / "gitbin"
        d.mkdir()
        (d / "git").write_text(GIT_STOP.format(git=shutil.which("git")))
        (d / "git").chmod(0o755)
        self.late = {"PATH": f"{d}:{self.wenv['PATH']}"}

    def assert_cancelled(self, r):
        self.assertEqual(r.returncode, 0, r.stderr)
        msg = self.final(r)["result"]
        self.assertTrue(msg.startswith("停下了：本轮被中台取消"), msg)
        self.assertNotIn("dt-worktree-stopped", msg)                     # 取消不发通知卡

    def test_cancel_during_commit_never_hands_off(self):
        tid = self.new_live_task()
        r, _ = self.run_turn([self.dispatch_event(tid)], FAKE_GIT_STOP_ON="commit", **self.late)
        self.assert_cancelled(r)
        self.assertIsNone(self.handoff(tid))
        self.assertIn(tid, git("log", "-1", "--format=%s", cwd=self.wt(tid)))   # 改动照样在本地提交

    def test_cancel_during_bundle_keeps_previous_handoff(self):
        tid = self.new_live_task()
        r, _ = self.run_turn([self.dispatch_event(tid)])
        old = self.handoff(tid)
        r, _ = self.run_turn([self.dispatch_event(tid, kind="rework")], FAKE_GIT_STOP_ON="bundle", **self.late)
        self.assert_cancelled(r)
        self.assertEqual(self.handoff(tid), old)                          # 清单没换
        d = self.outbox / tid
        self.assertEqual(hashlib.sha256((d / old["bundle"]).read_bytes()).hexdigest(), old["bundle_sha256"])   # 包没换
        self.assertEqual(sorted(x.name for x in d.iterdir()), sorted(["handoff.json", old["bundle"]]))   # 没留临时文件


class EndOfTurn(WorkerBase):
    """审查第 2 轮 P1-03 / P2-06：在进程里直接调，覆盖不好用子进程造出来的边界。"""

    def setUp(self):
        super().setUp()
        import importlib.util
        spec = importlib.util.spec_from_file_location("dt_worktree_inproc", SCRIPT)
        self.w = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.w)
        p = mock.patch.dict(os.environ, {**self.wenv, "DT_WORKTREE_BUDGET": "60"})
        p.start()
        self.addCleanup(p.stop)

    def admitted(self):
        tid = self.new_live_task()
        a = self.w.admit("claude", "dt-sandbox", {"events": [self.dispatch_event(tid)]}, self.w.now_tz())
        return a, {"repo": "dt-sandbox", **self.w.prepare_worktree("claude", "dt-sandbox", a)}

    def test_exit_right_at_budget_counts_as_over_budget(self):
        a, place = self.admitted()

        class Clock:
            t = 1000.0

            def time(self):
                return self.t

            def sleep(self, _):
                pass

        clock = Clock()

        class Child:                                  # CLI 掐着上限自己退出：wait 正常返回 0，但时间已经过了上限
            pid = 999999

            def wait(self, timeout=None):
                clock.t += 61
                return 0

        with mock.patch.object(self.w, "time", clock), mock.patch.object(self.w.subprocess, "Popen", return_value=Child()), \
                mock.patch.object(self.w, "reap_group", return_value=True):
            code, secs, outcome, clean = self.w.run_cli("claude", ["-p", "做事"], a, place)
        self.assertEqual((code, outcome, clean), (0, "over_budget", True))
        ended = self.w.after_run("claude", "dt-sandbox", {"events": [self.dispatch_event(a["task_id"])]}, a, place,
                                 outcome, clean)
        self.assertEqual(ended[0], "stopped")
        self.assertIn("累计用时到了", ended[1])
        self.assertIsNone(self.handoff(a["task_id"]))

    def test_cancel_at_manifest_commit_point_still_ends_stopped(self):
        """审查第 3 轮 P1-03：取消正好落在换清单那一下。换清单时取消信号是屏蔽的（挂起），换上后照样给中台「停下了」。"""
        a, place = self.admitted()
        (Path(place["worktree"]) / "README.md").write_text("改了\n")
        saved = {s: signal.getsignal(s) for s in self.w.STOP_SIGNALS}
        self.addCleanup(lambda: [signal.signal(s, h) for s, h in saved.items()])
        self.w.install_stop_handler()
        real_replace, seen = os.replace, {}

        def replace(src, dst):
            if Path(dst).name == "handoff.json":
                seen["blocked"] = signal.SIGTERM in signal.pthread_sigmask(signal.SIG_BLOCK, [])
                os.kill(os.getpid(), signal.SIGTERM)          # 中台的取消在换清单这一下到达
                seen["stopped_inside"] = self.w.stopped()
            return real_replace(src, dst)

        with mock.patch.object(self.w.os, "replace", side_effect=replace):
            ended = self.w.after_run("claude", "dt-sandbox", {"events": [self.dispatch_event(a["task_id"])]}, a, place)
        self.assertEqual(seen, {"blocked": True, "stopped_inside": False})   # 换清单时取消被挂起，没插进来
        self.assertEqual(ended, ("cancelled", "本轮被中台取消；交接清单在取消前已写出，这一轮不算交接"))
        self.assertIsNotNone(self.handoff(a["task_id"]))                    # 清单换上了，但这一轮结论是停下了
        self.assertTrue(self.w.stopped())
        self.assertNotIn(signal.SIGTERM, signal.pthread_sigmask(signal.SIG_BLOCK, []))   # 屏蔽已解除

    def test_unclean_tree_gets_an_explicit_stop(self):
        a, place = self.admitted()
        (Path(place["worktree"]) / "README.md").write_text("改了\n")
        ended = self.w.after_run("claude", "dt-sandbox", {"events": [self.dispatch_event(a["task_id"])]}, a, place,
                                 "finished", False)
        self.assertEqual(ended, ("stopped", "CLI 的进程没清干净，本轮没提交、没交接"))
        self.assertIsNone(self.handoff(a["task_id"]))
        self.assertTrue(self.w.stop_text(ended[1], a["task_id"]).startswith("停下了："))


class Plumbing(WorkerBase):
    def test_same_task_running_elsewhere_is_retried_later(self):
        tid = self.new_live_task()
        self.run_turn([self.dispatch_event(tid)])
        fd = os.open(self.work / ".state" / f"{tid}.lock", os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            r, calls = self.run_turn([self.answer_event(tid)])
        finally:
            os.close(fd)
        self.assertEqual(calls, [])
        self.assertTrue(self.final(r)["result"].startswith("没跑成："))

    def test_unreachable_repo_on_first_turn_is_retried(self):
        tid = self.new_live_task()
        r, calls = self.run_turn([self.dispatch_event(tid)], DT_WORKTREE_REMOTE=f"file://{self.root}/nowhere/{{github}}")
        self.assertEqual(calls, [])
        self.assertTrue(self.final(r)["result"].startswith("没跑成：取不到"))

    def test_without_hub_context_only_login_checks_pass_through(self):
        env = {k: v for k, v in self.wenv.items()}
        r = subprocess.run([PY, "-B", str(SCRIPT), "claude", "auth", "status"], capture_output=True, text=True,
                           env=env, cwd=self.cwd, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[-1]["argv"], ["auth", "status"])
        r = subprocess.run([PY, "-B", str(SCRIPT), "claude", "-p", "随便做"], capture_output=True, text=True,
                           env=env, cwd=self.cwd, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(len(self.calls()), 1)

    def test_name_selects_executor(self):
        link = self.root / "bin/dt-worktree-codex"
        link.symlink_to(SCRIPT)
        r = subprocess.run([PY, "-B", str(link), "login", "status"], capture_output=True, text=True,
                           env=self.wenv, cwd=self.cwd, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.calls()[-1]["argv"], ["login", "status"])


if __name__ == "__main__":
    unittest.main()
