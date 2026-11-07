"""Multica 夜跑主控（#92，DT-ORC M5 / 5.3a）自测：假 multica 用一个 JSON 世界模拟 issue、run、消息、Wakeup、Autopilot，
不联网、不碰真实 Multica、容器和 ~/Library。每次调用脚本都是新进程，等于每次都“重启”，计数只能从持久状态恢复。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "multica_night.py"
FAKE = HERE / "fake_multica.py"
OWNER = "member-owner"
T0 = "2026-11-08T14:00:00Z"          # 北京时间 22:00


def at(minutes):
    """T0 之后若干分钟的 UTC 时间串。"""
    import datetime as dt
    t = dt.datetime.fromisoformat(T0.replace("Z", "+00:00")) + dt.timedelta(minutes=minutes)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


PLAN = {
    "id": "night-test",
    "workspace_id": "ws-sandbox",
    "project_id": "proj-1",
    "repo": "owner/dt-sandbox",
    "owner_member_id": OWNER,
    "agents": {"claude": "agent-claude", "codex": "agent-codex"},
    "limits": {"hours": 4, "max_runs": 20, "max_consecutive_failures": 2, "stall_minutes": 30},
    "parent": {"title": "夜跑父单", "description": "父单说明"},
    "children": [
        {"key": "A", "stage": 1, "agent": "claude", "title": "A 导出", "description": "做 A"},
        {"key": "B", "stage": 1, "agent": "claude", "title": "B 搜索", "description": "做 B"},
        {"key": "V", "stage": 2, "agent": "codex", "title": "验证 {A} {B}", "description": "验证 {A} 和 {B}，父单 {PARENT}"},
    ],
    "common": "通用要求",
}


class MulticaNight(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-night-"))
        self.root = self.tmp / "runs"
        self.world = self.tmp / "world.json"
        self.freeze_log = self.tmp / "freeze.log"
        plan = json.loads(json.dumps(PLAN))
        plan["freeze_cmds"] = [["sh", "-c", f"echo stop >> '{self.freeze_log}'"]]
        self.plan_file = self.tmp / "plan.json"
        self.plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        wrapper = self.tmp / "multica"
        wrapper.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' \"$@\"\n", encoding="utf-8")
        wrapper.chmod(0o755)
        gh = self.tmp / "gh"
        gh.write_text("#!/bin/sh\n"
                      "case \"$1\" in api) printf 'main\\nagent/claude/abc\\n';; "
                      "pr) echo '[{\"number\":7,\"title\":\"SBX-12 导出\",\"headRefName\":\"agent/claude/abc\",\"url\":\"u\",\"state\":\"OPEN\",\"createdAt\":\"2026-11-08T14:30:00Z\",\"mergedAt\":null}]';; esac\n",
                      encoding="utf-8")
        gh.chmod(0o755)
        self.roster = self.tmp / "projects.json"
        self.roster.write_text(json.dumps({"projects": [], "inventory": [
            {"path": "github:owner/dt-sandbox", "sandbox": True, "multica_workspace": "ws-sandbox"}]}), encoding="utf-8")
        self.env = {**os.environ, "DT_NIGHT_ROOT": str(self.root), "DT_ROSTER": str(self.roster), "DT_MULTICA_CLI": str(wrapper), "DT_GH_CLI": str(gh),
                    "FAKE_MULTICA_WORLD": str(self.world), "DT_NIGHT_READBACK": "2,0"}
        self.put({"owner": OWNER, "now": "2026-11-08T13:00:00Z", "issues": {
            "SBX-1": {"id": "id-SBX-1", "identifier": "SBX-1", "status": "backlog", "assignee_type": None,
                      "updated_at": "2026-11-07T00:00:00Z"}}, "runs": [], "messages": {}, "wakeups": {}, "autopilots": []})

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    # ---- 世界 ----
    def get(self):
        return json.loads(self.world.read_text(encoding="utf-8"))

    def put(self, w):
        self.world.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")

    def edit(self, fn):
        w = self.get()
        fn(w)
        self.put(w)

    def run_cli(self, *args, now=T0):
        self.edit(lambda w: w.update(now=now))
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True,
                           env={**self.env, "DT_NOW": now})
        return r.returncode, r.stdout + r.stderr

    def ident(self, key):
        st = json.loads((self.root / "night-test" / "state.json").read_text(encoding="utf-8"))
        return st["issues"][key]["identifier"]

    def state(self):
        return json.loads((self.root / "night-test" / "state.json").read_text(encoding="utf-8"))

    def log(self):
        return [json.loads(x) for x in (self.root / "night-test" / "run-log.jsonl").read_text(encoding="utf-8").splitlines()]

    def issue(self, key):
        return self.get()["issues"][self.ident(key)]

    def finish(self, key, run_status="completed", issue_status="in_review", minutes=10, msgs=2):
        ident = self.ident(key)

        def f(w):
            for r in w["runs"]:
                if r["issue"] == ident and r["status"] not in ("completed", "failed", "cancelled"):
                    r.update(status=run_status, started_at=at(minutes - 5), completed_at=at(minutes),
                             result={"output": f"{ident} 做完了", "pr_url": ""})
                    w["messages"][r["id"]] = [{"seq": i + 1, "type": "text", "content": "x"} for i in range(msgs)]
            w["issues"][ident]["status"] = issue_status
        self.edit(f)

    def add_run(self, key, status, minutes=5):
        ident = self.ident(key)

        def f(w):
            rid = f"run-{ident}-x{len(w['runs']) + 1}"
            w["runs"].append({"id": rid, "issue": ident, "status": status, "created_at": at(minutes),
                              "started_at": at(minutes), "completed_at": at(minutes + 1) if status in ("completed", "failed") else None,
                              "attempt": 1, "agent_id": "agent-claude", "error": "boom" if status == "failed" else None, "result": {}})
        self.edit(f)

    def started(self):
        self.assertEqual(self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")[0], 0)
        code, out = self.run_cli("start", "--approved-by", "Hermes（常浩委托）")
        self.assertEqual(code, 0, out)
        return out

    # ---- 建单 ----
    def test_plan_creates_family_and_is_idempotent(self):
        code, out = self.run_cli("plan", str(self.plan_file))
        self.assertEqual(code, 0, out)
        w = self.get()
        parent = w["issues"][self.ident("PARENT")]
        self.assertEqual((parent["assignee_type"], parent["status"]), ("member", "in_progress"))
        for key, stage, agent in (("A", 1, "agent-claude"), ("B", 1, "agent-claude"), ("V", 2, "agent-codex")):
            i = self.issue(key)
            self.assertEqual((i["status"], i["stage"], i["assignee_id"], i["parent_issue_id"]),
                             ("backlog", stage, agent, parent["id"]))
        v = self.issue("V")
        self.assertIn(self.ident("A"), v["description"])
        self.assertIn(self.ident("B"), v["title"])
        self.assertIn(self.ident("PARENT"), v["description"])
        self.assertTrue(v["description"].endswith("通用要求"))
        creates = sum(c["args"][:2] == ["issue", "create"] for c in self.get()["calls"])
        self.assertEqual(creates, 4)
        self.assertTrue(all("ws-sandbox" in c["args"] for c in self.get()["calls"]), "每条命令都钉死在沙盒工作区")
        self.assertEqual(self.run_cli("plan", str(self.plan_file))[0], 0)
        self.assertEqual(sum(c["args"][:2] == ["issue", "create"] for c in self.get()["calls"]), 4, "再跑一次不重复建单")

    def test_plan_rejects_reference_to_later_stage(self):
        plan = json.loads(self.plan_file.read_text(encoding="utf-8"))
        plan["children"][0]["description"] = "依赖 {V}"
        self.plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        code, out = self.run_cli("plan", str(self.plan_file))
        self.assertEqual(code, 1)
        self.assertIn("不合规", out)
        self.assertNotIn("calls", self.get())

    # ---- 开跑 ----
    def test_start_refuses_when_preflight_fails_and_changes_nothing(self):
        self.run_cli("plan", str(self.plan_file))
        self.edit(lambda w: w["issues"][self.ident("B")].update(status="todo"))
        code, out = self.run_cli("start", "--approved-by", "Hermes")
        self.assertEqual(code, 1)
        self.assertIn("不在 backlog", out)
        self.assertNotIn("auth", self.state())
        self.assertEqual(self.issue("A")["status"], "backlog")

    def test_start_refuses_repo_not_marked_sandbox(self):
        self.run_cli("plan", str(self.plan_file))
        self.roster.write_text(json.dumps({"projects": [], "inventory": [
            {"path": "github:owner/dt-sandbox", "multica_workspace": "ws-sandbox"}]}), encoding="utf-8")
        code, out = self.run_cli("start", "--approved-by", "Hermes")
        self.assertEqual(code, 1)
        self.assertIn("sandbox: true", out)
        self.assertEqual(self.issue("A")["status"], "backlog")

    def test_real_roster_marks_dt_sandbox(self):
        real = json.loads((HERE.parents[2] / "projects.json").read_text(encoding="utf-8"))
        hits = [e for e in real["inventory"] if e.get("path") == "github:Natsukashii1997/dt-sandbox"]
        self.assertEqual(len(hits), 1)
        self.assertIs(hits[0]["sandbox"], True)
        self.assertNotIn("github:Natsukashii1997/dt-sandbox", ["github:" + str(p.get("github")) for p in real["projects"]],
                         "沙盒不进 projects：不派单、合并官不扫")

    def test_start_twice_is_refused(self):
        self.started()
        self.assertEqual(self.run_cli("start", "--approved-by", "Hermes")[0], 1)

    def test_tick_without_current_night_is_silent(self):
        code, out = self.run_cli("tick")
        self.assertEqual((code, out), (0, ""))

    # ---- 推进 ----
    def test_full_night_advances_stage_and_finishes(self):
        out = self.started()
        self.assertIn("stage 1", out)
        self.assertEqual({self.issue("A")["status"], self.issue("B")["status"]}, {"todo"})
        self.assertEqual(self.issue("V")["status"], "backlog")
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""), "run 还在排队：不出声、不推进")
        self.finish("A")
        self.assertEqual(self.run_cli("tick", now=at(10)), (0, ""), "B 没交付：不推进")
        self.finish("B", minutes=15)
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 0, out)
        self.assertIn("进入 stage 2", out)
        self.assertEqual(self.issue("V")["status"], "todo")
        self.finish("V", minutes=30)
        code, out = self.run_cli("tick", now=at(35))
        self.assertEqual(code, 0, out)
        self.assertIn("全部 stage 已交付", out)
        self.assertEqual(self.state()["outcome"], "FINISHED")
        runs = [r for r in self.log() if r["event"] == "run"]
        self.assertEqual(len(runs), 3)
        self.assertTrue(all(r["message_count"] == 2 and r["incomplete"] is False for r in runs))
        self.assertEqual(runs[0]["duration_s"], 300)
        msgs = (self.root / "night-test" / runs[0]["messages_ref"]).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(msgs), 2)
        self.assertEqual(self.run_cli("tick", now=at(40)), (0, ""), "结束后不再出声")
        code, out = self.run_cli("report", now=at(41))
        self.assertEqual(code, 0, out)
        self.assertIn("FINISHED", out)
        self.assertIn("远端只有 main 和 agent/ 分支", out)
        self.assertIn("#7", out)

    def test_stage_waits_while_a_run_is_still_active(self):
        self.started()
        self.finish("A")
        self.finish("B")
        self.add_run("B", "running", minutes=12)
        self.assertEqual(self.run_cli("tick", now=at(15)), (0, ""))
        self.assertEqual(self.issue("V")["status"], "backlog")

    # ---- 停机 ----
    def assert_stopped(self, out, reason):
        self.assertIn("已停机", out)
        self.assertIn(reason, out)
        st = self.state()
        self.assertEqual(st["outcome"], "STOPPED")
        self.assertTrue(all(r["status"] in ("completed", "failed", "cancelled") for r in self.get()["runs"]))
        self.assertEqual(self.issue("V")["status"], "backlog", "后面的 stage 留在 backlog")
        self.assertFalse(self.freeze_log.exists())

    def test_deadline_stops_cancels_runs_and_blocks_without_commenting_children(self):
        self.started()
        self.edit(lambda w: [r.update(status="running") for r in w["runs"]])
        code, out = self.run_cli("tick", now=at(241))
        self.assertEqual(code, 0, out)
        self.assert_stopped(out, "时长上限")
        self.assertEqual({self.issue("A")["status"], self.issue("B")["status"]}, {"blocked"})
        blocked_calls = [c["args"] for c in self.get()["calls"] if c["args"][:2] == ["issue", "status"] and "blocked" in c["args"]]
        self.assertTrue(blocked_calls and all("--no-start" in a for a in blocked_calls))
        comments = self.get()["comments"]
        self.assertEqual([c["issue"] for c in comments], [self.ident("PARENT")], "只在父单留言，子单留言会唤醒 agent")
        self.finish("A")
        self.finish("B")
        self.assertEqual(self.run_cli("tick", now=at(250)), (0, ""))
        self.assertEqual(self.issue("V")["status"], "backlog", "停机后不再推进")

    def test_two_failures_across_restarts_stop(self):
        """计数不清零：两次失败分别在两个进程里看到，第二次就停。"""
        self.started()
        self.edit(lambda w: w["runs"].clear())
        self.add_run("A", "failed", minutes=3)
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        self.assertEqual(self.state()["runs"][next(iter(self.state()["runs"]))]["status"], "failed")
        self.add_run("A", "failed", minutes=6)
        code, out = self.run_cli("tick", now=at(10))
        self.assert_stopped(out, "连续失败 2 次")

    def test_success_between_failures_resets_streak(self):
        self.started()
        self.edit(lambda w: w["runs"].clear())
        self.add_run("A", "failed", minutes=1)
        self.add_run("A", "completed", minutes=3)
        self.add_run("A", "failed", minutes=6)
        self.edit(lambda w: w["issues"][self.ident("A")].update(status="in_progress"))
        self.assertEqual(self.run_cli("tick", now=at(10)), (0, ""))

    def test_max_runs_stops(self):
        self.started()
        for i in range(19):
            self.add_run("A", "completed", minutes=i)
        code, out = self.run_cli("tick", now=at(25))
        self.assert_stopped(out, "run 总数达到上限 20")

    def test_blocked_child_stops(self):
        self.started()
        self.finish("A", issue_status="blocked")
        self.assert_stopped(self.run_cli("tick", now=at(12))[1], "被 agent 标为 blocked")

    def test_vanished_run_is_unreconciled(self):
        """重启后发现以前见过的 run 不见了：按核不清处理，停机。"""
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        self.edit(lambda w: w["runs"].pop(0))
        self.assert_stopped(self.run_cli("tick", now=at(10))[1], "UNRECONCILED")

    def test_message_gap_is_unreconciled(self):
        self.started()
        self.finish("A")
        self.edit(lambda w: w["messages"].update({w["runs"][0]["id"]: [{"seq": 1}, {"seq": 3}]}))
        self.assert_stopped(self.run_cli("tick", now=at(12))[1], "序号有缺口")

    def test_out_of_family_change_stops(self):
        self.started()
        self.edit(lambda w: w["issues"]["SBX-1"].update(status="todo", updated_at=at(3)))
        self.assert_stopped(self.run_cli("tick", now=at(5))[1], "SBX-1")

    def test_unauthorized_wakeup_is_disabled_on_stop(self):
        self.started()
        self.edit(lambda w: w["wakeups"].update({self.ident("A"): [{"id": "wk1", "status": "active"}]}))
        self.assert_stopped(self.run_cli("tick", now=at(5))[1], "未授权的 Wakeup")
        self.assertEqual(self.get()["wakeups"][self.ident("A")][0]["status"], "disabled")

    def test_active_autopilot_is_paused_on_stop(self):
        self.started()
        self.edit(lambda w: w["autopilots"].append({"id": "ap1", "status": "active"}))
        self.assert_stopped(self.run_cli("tick", now=at(5))[1], "Autopilot")
        self.assertEqual(self.get()["autopilots"][0]["status"], "paused")

    def test_stall_stops(self):
        self.edit(lambda w: w.update(start_runs=False))
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(10)), (0, ""))
        self.assertEqual(self.run_cli("tick", now=at(20)), (0, ""))
        self.assert_stopped(self.run_cli("tick", now=at(31))[1], "停滞")

    def test_waiting_local_directory_run_is_cancelled_on_stop(self):
        """反例 2：有 run 停在 waiting_local_directory（等目录锁）时触发停机，它也要被取消。"""
        self.started()
        self.edit(lambda w: w["runs"][0].update(status="waiting_local_directory"))
        code, out = self.run_cli("stop", "--reason", "演练", now=at(5))
        self.assertEqual(code, 0, out)
        self.assert_stopped(out, "人工停机：演练")
        cancelled = [c["args"][2] for c in self.get()["calls"] if c["args"][:2] == ["issue", "cancel-task"]]
        self.assertEqual(len(cancelled), 2)

    # ---- 冻结 ----
    def frozen(self):
        self.started()
        stuck = self.get()["runs"][0]["id"]
        self.edit(lambda w: (w["runs"][0].update(status="waiting_local_directory"), w.update(stuck=[stuck])))
        code, out = self.run_cli("tick", now=at(241))
        self.assertEqual(code, 1, out)
        self.assertIn("已冻结", out)
        self.assertIn("waiting_local_directory", out)
        self.assertEqual(self.freeze_log.read_text().split(), ["stop"])
        st = self.state()
        self.assertEqual((st["outcome"], st["frozen"]), ("UNRECONCILED", True))
        return stuck

    def test_unstoppable_run_freezes(self):
        self.frozen()
        self.assertIn("freeze", [r["event"] for r in self.log()])

    def test_frozen_never_advances_and_cancels_runs_after_container_restart(self):
        """反例 3：冻结后容器被重启、run 又跑起来，主控不续跑：再次取消并告警，绝不推进下一 stage。"""
        stuck = self.frozen()
        self.edit(lambda w: w.update(stuck=[]))
        self.finish("A")
        self.finish("B")
        self.assertEqual(self.run_cli("tick", now=at(250)), (0, ""))
        self.add_run("A", "queued", minutes=255)
        code, out = self.run_cli("tick", now=at(260))
        self.assertIn("冻结状态，却发现有 run 在跑", out)
        self.assertEqual(self.issue("V")["status"], "backlog")
        self.assertTrue(all(r["status"] != "queued" for r in self.get()["runs"]))
        self.assertTrue(stuck)

    def test_command_failure_during_stop_freezes(self):
        self.started()
        self.edit(lambda w: w.update(fail=["issue runs"]))
        code, out = self.run_cli("tick", now=at(5))
        self.assertEqual(code, 1, out)
        self.assertIn("已冻结", out)
        self.assertTrue(self.state()["frozen"])

    def test_script_crash_still_stops(self):
        self.started()
        self.edit(lambda w: w.update(weird_get=True))
        code, out = self.run_cli("tick", now=at(5))
        self.assertEqual(code, 1)
        self.assertIn("主控脚本异常：AttributeError", out)
        self.assertEqual(self.state()["outcome"], "STOPPED")
        self.assertTrue(all(r["status"] == "cancelled" for r in self.get()["runs"]))
        self.assertIn("crash", [r["event"] for r in self.log()])

    def test_release_clears_current_night(self):
        self.frozen()
        code, out = self.run_cli("release", "--by", "常浩", now=at(300))
        self.assertEqual(code, 0, out)
        self.assertFalse((self.root / "night-current").exists())
        self.assertEqual(self.run_cli("tick", now=at(305)), (0, ""))

    # ---- 定时开跑 ----
    def approved(self, start_at="2026-11-08T22:00:00+08:00"):
        self.assertEqual(self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")[0], 0)
        code, out = self.run_cli("approve", "--by", "Hermes（常浩委托）", "--start-at", start_at, "--hours", "3",
                                 now="2026-11-08T13:30:00Z")
        self.assertEqual(code, 0, out)
        self.assertIn("11-08 22:00", out)

    def test_approved_night_starts_on_time_via_tick_wrapper(self):
        self.approved()
        self.assertEqual(self.run_cli("tick", now="2026-11-08T13:55:00Z"), (0, ""), "没到点：不出声、不动")
        self.assertEqual(self.issue("A")["status"], "backlog")
        self.edit(lambda w: w.update(now=at(2)))
        r = subprocess.run([sys.executable, "-B", str(HERE.parent / "multica_night_tick.py")], capture_output=True,
                           text=True, env={**self.env, "DT_NOW": at(2)})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("已开跑", r.stdout)
        self.assertEqual(self.state()["auth"]["limits"]["hours"], 3)
        self.assertEqual(self.state()["auth"]["approved_by"], "Hermes（常浩委托）")
        self.assertEqual(self.issue("A")["status"], "todo")
        self.assertEqual(self.run_cli("tick", now=at(7)), (0, ""))

    def test_missed_start_window_does_not_start(self):
        self.approved()
        code, out = self.run_cli("tick", now=at(45))
        self.assertEqual(code, 1)
        self.assertIn("已过开跑窗口", out)
        self.assertEqual(self.issue("A")["status"], "backlog")
        self.assertEqual(self.run_cli("tick", now=at(50)), (0, ""), "只说一次")

    def test_scheduled_start_refused_by_preflight_says_once(self):
        self.approved()
        self.edit(lambda w: w["issues"][self.ident("B")].update(status="todo"))
        code, out = self.run_cli("tick", now=at(1))
        self.assertEqual(code, 1)
        self.assertIn("开跑前核对没通过", out)
        self.assertEqual(self.run_cli("tick", now=at(6)), (0, ""))
        self.assertNotIn("auth", self.state())

    # ---- 其他 ----
    def test_tick_gap_is_noted(self):
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        code, out = self.run_cli("tick", now=at(45))
        self.assertIn("相隔 40 分钟", out)
        self.assertIn("tick_gap", [r["event"] for r in self.log()])

    def test_status(self):
        self.started()
        code, out = self.run_cli("status", now=at(1))
        self.assertEqual(code, 0, out)
        self.assertIn("进行中", out)
        self.assertIn("11-09 02:00", out)      # 北京时间显示的截止时刻


if __name__ == "__main__":
    unittest.main()
