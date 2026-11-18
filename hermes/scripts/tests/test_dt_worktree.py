"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

ops/dt_worktree.py（工人侧启动脚本，#205 批 0C）：凭证镜像和任务单由真实的 0B-1 网关（autoloop_grants）在临时目录里写；
「GitHub」是临时目录里的本地裸仓库（file://），CLI 是记录参数的假程序。启动脚本用 macOS 自带的 /usr/bin/python3（3.9）
以子进程跑，和 dt-worker 上一样。不联网、不碰 ~/.agent-autoloop、~/dt-work、/Users/Shared/dt-autoloop。

验收（方案第 9 节 0C）：续接后范围不扩大；凭证失效不再继续；只交本单分支（推分支在受信发布，test_autoloop_publish.py）。
"""
import datetime as dt
import fcntl
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_grants as G  # noqa: E402
from test_autoloop_grants import Base  # noqa: E402

SCRIPT = HERE.parents[2] / "ops/dt_worktree.py"
PY = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

FAKE_CLI = r'''#!{py}
import json, os, subprocess, sys, time
from pathlib import Path
log = Path(os.environ["FAKE_LOG"])
with log.open("a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd(),
                        "cache": os.environ.get("XDG_CACHE_HOME"), "npm": os.environ.get("npm_config_cache")}}) + "\n")
mode = os.environ.get("FAKE_MODE", "edit")
if mode == "sleep":
    time.sleep(30)
if mode in ("edit", "revoke", "branch"):
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
print(json.dumps({{"type": "system", "subtype": "init", "session_id": "s-1"}}))
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "result": "改好了"}}))
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
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

    def run_turn(self, events, executor="claude", args=("-p", "做事", "--session-id", "s-1"), cwd=None, **env):
        ctx = self.root / f"ctx-{len(list(self.root.glob('ctx-*')))}.json"
        ctx.write_text(json.dumps({"events": events, "answers": []}))
        before = self.calls()
        r = subprocess.run([PY, "-B", str(SCRIPT), executor, *args], cwd=cwd or self.cwd, capture_output=True,
                           text=True, timeout=120, env={**self.wenv, "AGENT_AUTOLOOP_CONTEXT": str(ctx), **env})
        return r, self.calls()[len(before):]

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
        self.assertEqual(c["argv"], ["-p", "做事", "--session-id", "s-1"])        # 参数原样交给 CLI
        self.assertIn(f"/.cache/{tid}/", c["cache"])                               # 每张单缓存分开
        self.assertIn(f"/.cache/{tid}/", c["npm"])
        lines = r.stdout.strip().splitlines()                                     # 输出原样，不混本脚本的话
        self.assertEqual([json.loads(x)["type"] for x in lines], ["system", "result"])
        branch = f"agent/claude/{tid}"
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

    def test_codex_wrapper_uses_codex_message_format_when_stopping(self):
        tid = self.new_live_task(executor="codex")
        G.revoke(tid, now=self.now)
        r, calls = self.run_turn([self.dispatch_event(tid, executor="codex")], executor="codex")
        self.assertEqual(calls, [])
        lines = [json.loads(x) for x in r.stdout.strip().splitlines()]
        self.assertEqual([x["type"] for x in lines], ["item.completed", "turn.completed"])
        self.assertTrue(lines[0]["item"]["text"].startswith("停下了：凭证已撤销"))


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
        self.assertIn("累计用时到了", self.final(r)["result"])
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
