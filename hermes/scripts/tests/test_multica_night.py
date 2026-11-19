"""Multica 夜跑主控（#92，DT-ORC M5 / 5.3a）自测：假 multica 用一个 JSON 世界模拟 issue、run、消息、Wakeup、Autopilot，
不联网、不碰真实 Multica、容器和 ~/Library。每次调用脚本都是新进程，等于每次都“重启”，计数只能从持久状态恢复。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
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
FAKE_DOCKER = HERE / "fake_docker.py"
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


GOOD_IDENT = {"v": 2, "errors": [], "login": "bot", "ws_root": "/home/node/multica_workspaces", "ws_env": "/home/node/multica_workspaces",
              "permissions": {"admin": False, "maintain": False, "push": True, "triage": True, "pull": True},
              "rulesets": [{"id": 1, "bypass": "never"}, {"id": 2, "bypass": "never"}], "repos": ["owner/dt-sandbox"],
              "gh_version": "gh version 2.23.0 (2023-02-07)", "gh_json": None, "gh_json_unsupported": True,
              "gh_file": {"exists": True, "hosts": {"github.com": {"user": "bot", "users": [], "keys": ["oauth_token", "user", "git_protocol"],
                                                                   "user_keys": []}}},
              "env_names": ["GIT_AUTHOR_EMAIL", "GIT_AUTHOR_NAME", "GIT_COMMITTER_EMAIL", "GIT_COMMITTER_NAME"], "cred_files": [],
              "git": [{"scope": "global", "origin": "file:/home/node/.gitconfig", "key": "credential.https://github.com.helper", "where": "", "value": ""},
                      {"scope": "global", "origin": "file:/home/node/.gitconfig", "key": "credential.https://github.com.helper", "where": "",
                       "value": "!/usr/bin/gh auth git-credential"},
                      {"scope": "local", "origin": "file:.git/config", "key": "remote.origin.url", "where": "/home/node/multica_workspaces/w/r",
                       "value": "https://github.com/owner/dt-sandbox.git", "userinfo": False}],
              "git_repos": 1}
# dt-sandbox 的保护基线（名册 executor_gate.protection_baseline）和宿主读到的现状（多出 GitHub 附带的字段，比对时忽略）
RULESETS = [{"id": 1, "name": "main-merge-reserved-to-admin", "target": "branch", "source_type": "Repository", "source": "owner/dt-sandbox",
             "enforcement": "active", "conditions": {"ref_name": {"exclude": [], "include": ["refs/heads/main"]}}, "rules": [{"type": "update"}],
             "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "exempt"}]},
            {"id": 2, "name": "only-main-and-agent-branches", "target": "branch", "source_type": "Repository", "source": "owner/dt-sandbox",
             "enforcement": "active", "conditions": {"ref_name": {"exclude": ["refs/heads/main", "refs/heads/agent/**"], "include": ["~ALL"]}},
             "rules": [{"type": "creation"}, {"type": "update"}, {"type": "deletion"}], "bypass_actors": []}]
PROTECTION = {"required_pull_request_reviews": {"dismiss_stale_reviews": False, "required_approving_review_count": 0},
              "enforce_admins": {"enabled": True}, "allow_force_pushes": {"enabled": False}}


class MulticaNight(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-night-"))
        self.root = self.tmp / "runs"
        self.world = self.tmp / "world.json"
        self.freeze_log = self.tmp / "freeze.log"
        plan = json.loads(json.dumps(PLAN))
        # 计划里的冻结命令不再被采用（#174 第 2 轮 P1-01：冻结由名册生成），这里故意写成什么都不做、回读恒为「已停」
        plan["freeze_cmds"] = [["true"]]
        plan["freeze_check"] = {"cmd": ["echo", "false no"], "expect": "false no"}
        docker = self.tmp / "docker"                           # 假 docker exec：执行器里的进程（M5 #25、#27）
        docker.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE_DOCKER}' \"$@\"\n", encoding="utf-8")
        docker.chmod(0o755)
        self.procs = self.tmp / "procs.json"
        plan["executor"] = {"docker": [str(docker), "--context", "colima-multica"], "container": "agent-1", "kill": True,
                            "github_login": "bot"}
        self.plan_obj = plan
        self.write_plan()
        self.set_procs(ident=dict(GOOD_IDENT), freeze_log=str(self.freeze_log))
        wrapper = self.tmp / "multica"
        wrapper.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{FAKE}' \"$@\"\n", encoding="utf-8")
        wrapper.chmod(0o755)
        self.branches = self.tmp / "branches.txt"              # 远端分支「名字 提交」，测试可改
        self.branches.write_text("main aaa111\nagent/claude/abc bbb222\n", encoding="utf-8")
        self.prs = self.tmp / "prs.json"
        self.prs.write_text(json.dumps([{"number": 7, "title": "SBX-12 导出", "headRefName": "agent/claude/abc", "headRefOid": "bbb222",
                                         "url": "u", "state": "OPEN", "createdAt": "2026-11-08T14:30:00Z", "mergedAt": None}],
                                       ensure_ascii=False), encoding="utf-8")
        self.host = self.tmp / "host"                          # 宿主 gh 只读到的规则集和分支保护（#174），删文件 = 读不到
        self.host.mkdir()
        self.set_host()
        gh = self.tmp / "gh"
        gh.write_text("#!/bin/sh\n"
                      f"case \"$*\" in *commit.sha*) cat '{self.branches}';; "
                      f"*'/rulesets --paginate'*) cat '{self.host}/rulesets.json';; "
                      f"*/rulesets/*) cat '{self.host}/ruleset-'\"${{2##*/}}\".json;; "
                      f"*/protection*) cat '{self.host}/protection.json';; "
                      f"api*) cut -d' ' -f1 '{self.branches}';; "
                      f"pr*) cat '{self.prs}';; esac\n", encoding="utf-8")
        gh.chmod(0o755)
        self.gate = {"github_login": "bot", "docker": plan["executor"]["docker"], "container": "agent-1", "compose_project": "multica",
                     "compose_service": "isolated-agent", "protection_baseline": "baselines/dt-sandbox.json",
                     "workspaces_root": "/home/node/multica_workspaces"}
        self.repo = self.tmp / "roster-repo"                   # 名册仓库：只认提交到 origin/main 的内容（第 2 轮 P1-03）
        self.repo.mkdir()
        self.git("init", "-q")
        self.write_roster()
        self.env = {**os.environ, "DT_NIGHT_ROOT": str(self.root), "DT_ROSTER_REPO": str(self.repo), "DT_MULTICA_CLI": str(wrapper), "DT_GH_CLI": str(gh),
                    "FAKE_MULTICA_WORLD": str(self.world), "DT_NIGHT_READBACK": "2,0",
                    "FAKE_DOCKER_PROCS": str(self.procs), "DT_NIGHT_PROCWAIT": "2,0"}
        self.put({"owner": OWNER, "now": "2026-11-08T13:00:00Z", "issues": {
            "SBX-1": {"id": "id-SBX-1", "identifier": "SBX-1", "status": "backlog", "assignee_type": None,
                      "updated_at": "2026-11-07T00:00:00Z"}}, "runs": [], "messages": {}, "wakeups": {}, "autopilots": []})

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@x.invalid", *args],
                       check=True, capture_output=True)

    def commit(self, files, merged=True):
        """把文件提交进名册仓库；merged 为真时 origin/main 也指向这个提交（模拟已合并）。"""
        for rel, text in files.items():
            f = self.repo / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            self.git("add", rel)
        self.git("commit", "-q", "--allow-empty", "-m", "x")
        if merged:
            self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def write_roster(self, raw=None, baseline=None, merged=True, **entry):
        e = {"path": "github:owner/dt-sandbox", "sandbox": True, "multica_workspace": "ws-sandbox", "executor_gate": self.gate, **entry}
        base = {"repo": "owner/dt-sandbox", "rulesets": RULESETS, "branch_protection": {"main": PROTECTION}}
        self.commit({"projects.json": raw if raw is not None else json.dumps({"projects": [], "inventory": [e]}),
                     "baselines/dt-sandbox.json": baseline if baseline is not None else json.dumps(base)}, merged)

    def set_host(self, rulesets=None, protection=None):
        rulesets = RULESETS if rulesets is None else rulesets
        for f in self.host.glob("*.json"):
            f.unlink()
        (self.host / "rulesets.json").write_text(json.dumps([{"id": r["id"], "name": r["name"]} for r in rulesets]), encoding="utf-8")
        for r in rulesets:
            (self.host / f"ruleset-{r['id']}.json").write_text(json.dumps({**r, "node_id": "x", "current_user_can_bypass": "exempt",
                                                                          "updated_at": "2026-11-10T18:16:16Z"}), encoding="utf-8")
        prot = PROTECTION if protection is None else protection
        (self.host / "protection.json").write_text(json.dumps({"url": "https://api.github.com/x", **{
            k: ({"url": "u", **v} if isinstance(v, dict) else v) for k, v in prot.items()}}), encoding="utf-8")

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def write_plan(self):
        self.plan_file = self.tmp / "plan.json"
        self.plan_file.write_text(json.dumps(self.plan_obj, ensure_ascii=False), encoding="utf-8")

    def set_procs(self, **kw):
        w = json.loads(self.procs.read_text(encoding="utf-8")) if self.procs.exists() else {}
        w.update(kw)
        self.procs.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")

    def docker_calls(self):
        return [c["action"] for c in json.loads(self.procs.read_text(encoding="utf-8")).get("calls", [])] if self.procs.exists() else []

    # ---- 世界 ----
    def get(self):
        return json.loads(self.world.read_text(encoding="utf-8"))

    def put(self, w):
        self.world.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")

    def edit(self, fn):
        w = self.get()
        fn(w)
        self.put(w)

    def run_cli(self, *args, now=T0, env=None):
        self.edit(lambda w: w.update(now=now))
        r = subprocess.run([sys.executable, "-B", str(SCRIPT), *args], capture_output=True, text=True,
                           env={**self.env, "DT_NOW": now, **(env or {})})
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
        self.write_roster(raw=json.dumps({"projects": [], "inventory": [
            {"path": "github:owner/dt-sandbox", "multica_workspace": "ws-sandbox"}]}))
        code, out = self.run_cli("start", "--approved-by", "Hermes")
        self.assertEqual(code, 1)
        self.assertIn("sandbox: true", out)
        self.assertEqual(self.issue("A")["status"], "backlog")

    # ---- 运行前身份闸（#172）----
    def test_identity_gate_checked_before_start(self):
        self.run_cli("plan", str(self.plan_file))
        code, out = self.run_cli("start", "--approved-by", "Hermes")
        self.assertEqual(code, 0, out)
        self.assertIn("ident", self.docker_calls())

    def refused(self, expect, now=T0):
        """开跑被拒、什么都没动。"""
        code, out = self.run_cli("start", "--approved-by", "Hermes", now=now)
        self.assertEqual(code, 1, out)
        self.assertIn("开跑前核对没通过", out)
        self.assertIn(expect, out)
        self.assertNotIn("auth", self.state())
        self.assertEqual(self.issue("A")["status"], "backlog")
        return out

    def test_identity_gate_refuses_each_problem_and_changes_nothing(self):
        perm = GOOD_IDENT["permissions"]
        gfile = GOOD_IDENT["gh_file"]["hosts"]["github.com"]
        cases = [
            ("login", "someone-else", "身份是 someone-else"),
            ("permissions", {**perm, "admin": True}, "admin / maintain 不是 false"),
            ("permissions", {**perm, "maintain": True}, "admin / maintain 不是 false"),
            ("permissions", {**perm, "push": False}, "push 不是 true"),
            ("permissions", {k: v for k, v in perm.items() if k != "maintain"}, "权限字段缺失或类型不对：maintain"),
            ("permissions", {**perm, "admin": "false"}, "权限字段缺失或类型不对：admin"),
            ("rulesets", [{"id": 1, "bypass": "never"}, {"id": 2, "bypass": "always"}], "能绕过规则集：2=always"),
            ("rulesets", [{"id": 1, "bypass": "pull_requests_only"}, {"id": 2, "bypass": "never"}], "能绕过规则集"),
            ("rulesets", [{"id": 1, "bypass": "never"}], "执行器看到的规则集和基线不一致"),
            ("rulesets", [], "执行器读不到 owner/dt-sandbox 的规则集"),
            ("repos", ["owner/dt-sandbox", "owner/digital-twin"], "能访问的仓库不止"),
            ("repos", [], "能访问的仓库不止"),
            ("gh_file", {"exists": True, "hosts": {"github.com": {**gfile, "users": ["bot", "Natsukashii1997"]}}}, "其他账号"),
            ("gh_file", {"exists": True, "hosts": {"github.com": gfile, "ghe.example.com": gfile}}, "其他主机"),
            ("env_names", ["GH_TOKEN"], "不认识的凭据相关变量：GH_TOKEN"),
            ("cred_files", ["~/.git-credentials"], "其他凭据文件"),
            ("git", GOOD_IDENT["git"] + [{"scope": "global", "origin": "file:/home/node/.gitconfig", "key": "credential.helper", "where": "",
                                          "value": "store"}], "git 凭据配置不认识：credential.helper"),
            ("errors", [{"what": "gh api user/repos", "code": 1, "msg": "HTTP 502"}], "gh api user/repos 失败"),
        ]
        self.run_cli("plan", str(self.plan_file))
        for field, value, expect in cases:
            with self.subTest(field=field, value=value):
                self.set_procs(ident={**GOOD_IDENT, field: value})
                self.refused(expect)

    def test_identity_gate_fails_closed_when_query_fails_and_scrubs_secrets(self):
        self.run_cli("plan", str(self.plan_file))
        self.set_procs(ident_fail=True)
        out = self.refused("核对执行器 GitHub 身份失败")
        self.assertNotIn("ghp_", out, "外部命令报错里的令牌样式片段要脱敏")

    def test_identity_gate_refuses_malformed_facts(self):
        self.run_cli("plan", str(self.plan_file))
        for raw, expect in (("not json", "输出读不懂"), (json.dumps({**GOOD_IDENT, "permissions": [1]}), "格式不对：permissions"),
                            (json.dumps({**GOOD_IDENT, "v": 1}), "格式不对：版本"), (json.dumps([1, 2]), "格式不对：版本")):
            with self.subTest(raw=raw):
                self.set_procs(ident_raw=raw)
                self.refused(expect)

    def test_plan_requires_github_login(self):
        del self.plan_obj["executor"]["github_login"]
        self.write_plan()
        code, out = self.run_cli("plan", str(self.plan_file))
        self.assertNotEqual(code, 0)
        self.assertIn("github_login", out)

    def test_identity_problems_accepts_good_facts_case_insensitively(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("mn", SCRIPT)
        mn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mn)
        facts = json.loads(json.dumps(GOOD_IDENT))
        facts["git"][-1]["value"] = "https://github.com/Owner/DT-Sandbox"
        self.assertEqual(mn.identity_problems(facts, "Owner/DT-Sandbox", "bot", [1, 2], "/home/node/multica_workspaces"), [])

    # ---- 信任基准在名册，计划改不了（P1-01）----
    def test_plan_values_must_match_trusted_roster(self):
        self.run_cli("plan", str(self.plan_file))
        for field, value in (("github_login", "Natsukashii1997"), ("container", "other-1"), ("docker", ["docker", "--context", "elsewhere"])):
            with self.subTest(field=field):
                self.write_roster(executor_gate={**self.gate, field: value})
                self.refused(f"计划的 executor.{field} 和名册 executor_gate 不一致")
                self.assertNotIn("ident", self.docker_calls(), "计划对不上名册就不进容器查")
        self.write_roster()

    def test_roster_without_trusted_gate_is_refused(self):
        self.run_cli("plan", str(self.plan_file))
        self.write_roster(raw=json.dumps({"projects": [], "inventory": [
            {"path": "github:owner/dt-sandbox", "sandbox": True, "multica_workspace": "ws-sandbox"}]}))
        self.refused("名册里没有 executor_gate")
        self.write_roster(executor_gate={**self.gate, "docker": "docker"})
        self.refused("executor_gate.docker 缺失或类型不对")

    def test_container_must_be_the_compose_isolated_executor(self):
        self.run_cli("plan", str(self.plan_file))
        good = {"com.docker.compose.project": "multica", "com.docker.compose.service": "isolated-agent", "com.docker.compose.oneoff": "False"}
        for kw, expect in (({"labels": {**good, "com.docker.compose.service": "backend"}}, "标签 com.docker.compose.service 不是 isolated-agent"),
                           ({"labels": {**good, "com.docker.compose.project": "other"}}, "标签 com.docker.compose.project 不是 multica"),
                           ({"labels": {**good, "com.docker.compose.oneoff": "True"}}, "com.docker.compose.oneoff"),
                           ({"labels": None}, "核对执行器容器标签失败：输出读不懂"),
                           ({"labels": good, "service": ["agent-1", "agent-2"]}, "的容器不是只有 agent-1"),
                           ({"labels": good, "service": ["agent-1"], "inspect_fail": True}, "核对执行器容器标签失败")):
            with self.subTest(kw=kw):
                self.set_procs(**kw)
                self.refused(expect)
                self.set_procs(labels=good, service=["agent-1"], inspect_fail=False)

    # ---- 宿主只读核对规则集和分支保护（P1-02）----
    def test_protection_drift_or_unreadable_is_refused(self):
        self.run_cli("plan", str(self.plan_file))
        r1, r2 = RULESETS
        cases = [
            ({"rulesets": [{**r1, "bypass_actors": r1["bypass_actors"] + [{"actor_id": 9, "actor_type": "User", "bypass_mode": "always"}]}, r2]},
             "规则集 1 的 bypass_actors 和基线不一致"),
            ({"rulesets": [{**r1, "enforcement": "evaluate"}, r2]}, "规则集 1 的 enforcement"),
            ({"rulesets": [r1, {**r2, "conditions": {"ref_name": {"exclude": [], "include": ["~ALL"]}}}]}, "规则集 2 的 conditions"),
            ({"rulesets": [r1, {**r2, "rules": [{"type": "creation"}]}]}, "规则集 2 的 rules"),
            ({"rulesets": [r1]}, "的规则集和基线不一致"),
            ({"rulesets": [r1, r2, {**r2, "id": 3}]}, "的规则集和基线不一致"),
            ({"protection": {**PROTECTION, "enforce_admins": {"enabled": False}}}, "main 分支保护的 enforce_admins"),
            ({"protection": {k: v for k, v in PROTECTION.items() if k != "required_pull_request_reviews"}}, "required_pull_request_reviews"),
        ]
        for kw, expect in cases:
            with self.subTest(expect=expect):
                self.set_host(**kw)
                self.refused(expect)
        self.set_host()
        (self.host / "protection.json").unlink()
        self.refused("宿主读 repos/owner/dt-sandbox/branches/main/protection 失败")
        self.set_host()
        (self.host / "ruleset-2.json").unlink()
        self.refused("宿主读 repos/owner/dt-sandbox/rulesets/2 失败")
        self.set_host()
        self.write_roster(baseline="{")
        self.refused("读不了保护基线")

    # ---- 计划摘要（P1-01）----
    def test_plan_changed_after_approval_is_refused_at_scheduled_start(self):
        self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")
        self.assertEqual(self.run_cli("approve", "--by", "Hermes", "--start-at", "2026-11-08T22:00", now="2026-11-08T13:00:00Z")[0], 0)
        p = self.root / "night-test" / "plan.json"
        plan = json.loads(p.read_text(encoding="utf-8"))
        plan["limits"]["max_runs"] = 999
        p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        code, out = self.run_cli("tick", now=T0)
        self.assertEqual(code, 1, out)
        self.assertIn("计划在批准之后被改过", out)
        self.assertNotIn("auth", self.state())
        self.assertEqual(self.state()["approval"]["result"], "refused")
        self.assertEqual(self.issue("A")["status"], "backlog")

    def test_scheduled_start_with_broken_gate_input_refuses_once(self):
        """P2-02：定时开跑入口里身份闸的输入坏了（事实格式不对、名册读不懂），也只是拒绝开跑，不抛异常、不重来。"""
        for broken in ("facts", "roster"):
            with self.subTest(broken=broken):
                self.tearDown()       # 每种情形一个干净的世界
                self.setUp()
                self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")
                self.run_cli("approve", "--by", "Hermes", "--start-at", "2026-11-08T22:00", now="2026-11-08T13:00:00Z")
                if broken == "facts":
                    self.set_procs(ident_raw=json.dumps({**GOOD_IDENT, "rulesets": "never"}))
                else:
                    self.write_roster(raw="{not json")
                code, out = self.run_cli("tick", now=T0)
                self.assertEqual(code, 1, out)
                self.assertIn("开跑前核对没通过", out)
                self.assertNotIn("Traceback", out)
                self.assertEqual(self.state()["approval"]["result"], "refused")
                self.assertEqual(self.issue("A")["status"], "backlog")
                self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""), "拒绝过就不再每 5 分钟重来")

    def test_approval_without_plan_digest_is_refused(self):
        self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")
        self.run_cli("approve", "--by", "Hermes", "--start-at", "2026-11-08T22:00", now="2026-11-08T13:00:00Z")
        st = self.state()
        del st["approval"]["plan_digest"]
        (self.root / "night-test" / "state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        code, out = self.run_cli("tick", now=T0)
        self.assertEqual(code, 1)
        self.assertIn("批准里没记计划摘要", out)
        self.assertNotIn("auth", self.state())

    # ---- 每次放 stage 前都重跑（P1-05）----
    def test_gate_reruns_before_each_stage_and_failure_stops_and_freezes(self):
        self.started()
        self.assertEqual(self.docker_calls().count("ident"), 1)
        self.set_procs(ident={**GOOD_IDENT, "login": "Natsukashii1997"})   # 夜里有人把常浩账号登进了容器
        self.finish("A")
        self.finish("B", minutes=15)
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 1, out)
        self.assertIn("放 stage 2 前身份闸没通过", out)
        self.assertIn("身份是 Natsukashii1997", out)
        self.assertIn("已冻结", out)
        self.assertEqual(self.docker_calls().count("ident"), 2)
        self.assertEqual(self.issue("V")["status"], "backlog", "不放下一个 stage")
        self.assertTrue(self.state()["frozen"])
        self.assertIn("stop", self.freeze_log.read_text(encoding="utf-8"), "执行器不可信：停容器")
        self.run_cli("tick", now=at(25))      # 已冻结且确认停住：之后的检查不出声，也不推进
        self.assertEqual(self.issue("V")["status"], "backlog", "冻结后不再推进")

    def test_gate_reruns_before_each_stage_and_passes(self):
        self.started()
        self.finish("A")
        self.finish("B", minutes=15)
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.issue("V")["status"], "todo")
        self.assertEqual(self.docker_calls().count("ident"), 2)
        self.assertEqual([e["ok"] for e in self.log() if e["event"] == "identity_gate"], [True])

    def test_gate_reruns_before_resumed_advance(self):
        self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")
        self.assertEqual(self.run_cli("start", "--approved-by", "Hermes")[0], 0)
        self.edit(lambda w: w["issues"][self.ident("B")].update(status="backlog"))    # 推进做到一半中断
        self.set_procs(ident={**GOOD_IDENT, "env_names": ["GITHUB_TOKEN"]})
        code, out = self.run_cli("tick", now=at(5))
        self.assertEqual(code, 1, out)
        self.assertIn("放 stage 1 前身份闸没通过", out)
        self.assertEqual(self.issue("B")["status"], "backlog")
        self.assertTrue(self.state()["frozen"])

    def test_plan_changed_mid_night_stops_before_next_stage(self):
        self.started()
        p = self.root / "night-test" / "plan.json"
        plan = json.loads(p.read_text(encoding="utf-8"))
        plan["executor"]["kill"] = False
        p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        self.finish("A")
        self.finish("B", minutes=15)
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 1, out)
        self.assertIn("计划在开跑之后被改过", out)
        self.assertEqual(self.issue("V")["status"], "backlog")

    # ---- 第 2 轮审查：冻结不信计划、distrust 不降级、名册只认已合并版本 ----
    def load_module(self, now):
        """在本进程里加载主控（用于故障注入）；环境变量按本测试的假命令设好，测试结束恢复。"""
        import importlib.util
        saved = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(saved)))
        os.environ.update({k: v for k, v in self.env.items() if k.startswith(("DT_", "FAKE_"))}, DT_NOW=now)
        self.edit(lambda w: w.update(now=now))
        spec = importlib.util.spec_from_file_location("mn_inproc", SCRIPT)
        mn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mn)
        return mn

    def tamper_plan(self, **fields):
        p = self.root / "night-test" / "plan.json"
        plan = json.loads(p.read_text(encoding="utf-8"))
        plan.update(fields)
        p.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    def test_tampered_plan_cannot_fake_the_freeze(self):
        """P1-01：夜里计划被改（含把冻结命令改成什么都不做、回读恒为已停），身份闸发现后照样按名册停容器，
        容器没停住就不能记为已确认冻结。"""
        self.started()
        self.set_procs(unstoppable=True)
        self.tamper_plan(freeze_cmds=[["true"]], freeze_check={"cmd": ["echo", "false no"], "expect": "false no"},
                         limits={**PLAN["limits"], "max_runs": 999})
        self.finish("A")
        self.finish("B", minutes=15)
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 1, out)
        self.assertIn("计划在开跑之后被改过", out)
        calls = self.docker_calls()
        self.assertIn("update", calls)
        self.assertIn("stop", calls, "按名册停容器")
        self.assertIn("inspect-state", calls, "按名册回读")
        st = self.state()
        self.assertEqual((st["frozen"], st["freeze_verified"]), (True, False), "计划里的假回读不算数")
        self.assertIn("执行器没确认停住", out)
        self.assertEqual(self.issue("V")["status"], "backlog")

    def test_normal_freeze_also_uses_roster_commands(self):
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        self.edit(lambda w: w["runs"].pop(0))                     # 见过的 run 不见了：核不清，冻结
        code, out = self.run_cli("tick", now=at(10))
        self.assertIn("已停隔离执行器容器", out)
        self.assertEqual([c for c in self.docker_calls() if c in ("update", "stop", "inspect-state")],
                         ["update", "stop", "inspect-state"])

    def test_plan_commands_never_run_even_when_roster_lookup_by_plan_fails(self):
        """第 3 轮 P1-01：夜里计划被改成名册里没有的 repo、冻结命令换成别的宿主命令。身份闸照样拒绝放 stage，
        冻结只按名册里唯一的沙盒执行器停，计划里的命令一条都不执行。"""
        marker = self.tmp / "plan-cmd-ran.txt"
        self.started()
        self.tamper_plan(repo="owner/not-in-roster", freeze_cmds=[["sh", "-c", f"echo ran >> '{marker}'"]],
                         freeze_check={"cmd": ["sh", "-c", f"echo ran >> '{marker}'; echo false no"], "expect": "false no"})
        self.finish("A")
        self.finish("B", minutes=15)
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 1, out)
        self.assertIn("名册里没有 owner/not-in-roster", out)
        self.assertFalse(marker.exists(), "计划里的命令不能在宿主机执行")
        self.assertEqual([c for c in self.docker_calls() if c in ("update", "stop", "inspect-state")], ["update", "stop", "inspect-state"])
        self.assertTrue(self.state()["freeze_verified"])
        self.assertEqual(self.issue("V")["status"], "backlog")

    def test_unreadable_roster_runs_no_freeze_command_and_alarms(self):
        marker = self.tmp / "plan-cmd-ran.txt"
        self.started()
        self.tamper_plan(freeze_cmds=[["sh", "-c", f"echo ran >> '{marker}'"]])
        self.write_roster(raw="{not json")
        self.finish("A")
        self.finish("B", minutes=15)
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 1, out)
        self.assertIn("没执行任何停容器命令", out)
        self.assertIn("请立刻手动停隔离执行器容器", out)
        self.assertFalse(marker.exists(), "名册读不到也不退回计划里的命令")
        self.assertNotIn("stop", self.docker_calls())
        st = self.state()
        self.assertEqual((st["frozen"], st["freeze_verified"]), (True, False))
        code, out = self.run_cli("tick", now=at(25))                 # 之后每次检查都再试，仍不执行计划命令
        self.assertIn("没执行任何停容器命令", out)
        self.assertFalse(marker.exists())

    def test_multiple_sandbox_gates_in_roster_run_no_freeze_command(self):
        self.started()
        e = {"path": "github:owner/dt-sandbox", "sandbox": True, "multica_workspace": "ws-sandbox", "executor_gate": self.gate}
        self.write_roster(raw=json.dumps({"projects": [], "inventory": [e, {**e, "path": "github:owner/other"}]}))
        mn = self.load_module(at(5))
        self.assertEqual(mn.freeze_spec(), ([], {}, False), "不止一个沙盒执行器：不知道停哪个，就不执行")

    def test_distrust_survives_crash_in_stop(self):
        """P1-02：身份闸不通过的停机中途崩了，兜底重来的停机仍按执行器不可信处理：回读通过也冻结（停容器）。"""
        self.started()
        self.set_procs(ident={**GOOD_IDENT, "login": "Natsukashii1997"})
        self.finish("A")
        self.finish("B", minutes=15)
        mn = self.load_module(at(20))
        real, calls = mn.readback, []

        def flaky(night, errs):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("写状态时出错（注入）")
            return real(night, errs)
        mn.readback = flaky
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = mn.main(["tick"])
        out = buf.getvalue()
        self.assertEqual(code, 1, out)
        self.assertGreaterEqual(len(calls), 2, "第一次停机崩了，兜底又停了一次")
        st = self.state()
        self.assertEqual((st["outcome"], st["frozen"]), ("UNRECONCILED", True), "不能降级成普通 STOPPED")
        self.assertIn("执行器身份闸没通过", json.dumps(self.log(), ensure_ascii=False))
        self.assertIn("stop", self.freeze_log.read_text(encoding="utf-8"))
        self.assertEqual(self.issue("V")["status"], "backlog")

    def test_roster_and_baseline_only_count_once_merged(self):
        """P1-03：名册和保护基线只认已合并（origin/main）的内容：工作区里改了、提交了没合并、用旧的 DT_ROSTER 指别的文件，都不算数。"""
        self.plan_obj["executor"]["github_login"] = "someone"
        self.write_plan()
        self.run_cli("plan", str(self.plan_file))
        loose = json.dumps({"projects": [], "inventory": [{"path": "github:owner/dt-sandbox", "sandbox": True, "multica_workspace": "ws-sandbox",
                                                           "executor_gate": {**self.gate, "github_login": "someone"}}]})
        (self.repo / "projects.json").write_text(loose, encoding="utf-8")          # 工作区改了没提交
        self.refused("计划的 executor.github_login 和名册 executor_gate 不一致")
        self.commit({"projects.json": loose}, merged=False)                         # 提交了没合并
        self.refused("计划的 executor.github_login 和名册 executor_gate 不一致")
        other = self.tmp / "other.json"
        other.write_text(loose, encoding="utf-8")
        code, out = self.run_cli("start", "--approved-by", "Hermes", env={"DT_ROSTER": str(other)})   # 旧变量不再认
        self.assertEqual(code, 1, out)
        self.assertIn("和名册 executor_gate 不一致", out)

    def test_baseline_edited_to_match_drift_is_not_trusted_until_merged(self):
        self.run_cli("plan", str(self.plan_file))
        r1, r2 = RULESETS
        drift = [{**r1, "enforcement": "disabled"}, r2]
        self.set_host(rulesets=drift)
        (self.repo / "baselines" / "dt-sandbox.json").write_text(json.dumps(
            {"repo": "owner/dt-sandbox", "rulesets": drift, "branch_protection": {"main": PROTECTION}}), encoding="utf-8")
        self.refused("规则集 1 的 enforcement 和基线不一致")

    def test_real_roster_marks_dt_sandbox(self):
        real = json.loads((HERE.parents[2] / "projects.json").read_text(encoding="utf-8"))
        hits = [e for e in real["inventory"] if e.get("path") == "github:Natsukashii1997/dt-sandbox"]
        self.assertEqual(len(hits), 1)
        self.assertIs(hits[0]["sandbox"], True)
        import importlib.util
        spec = importlib.util.spec_from_file_location("mn", SCRIPT)
        mn = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mn)
        g = mn.trusted_gate(hits[0])          # 信任基准齐全（#174）
        self.assertEqual((g["github_login"], g["container"], g["compose_project"], g["compose_service"]),
                         ("shanhao1997", "multica-isolated-agent-1", "multica", "isolated-agent"))
        self.assertEqual(g["workspaces_root"], "/home/node/multica_workspaces")
        # 正式运行读 origin/main；这里核对本分支工作区里的文件本身（合并前 origin/main 还没有）
        mn.committed_json = lambda rel: json.loads((HERE.parents[2] / rel).read_text(encoding="utf-8"))
        base = mn.load_baseline(g, "Natsukashii1997/dt-sandbox")
        self.assertEqual(sorted(r["id"] for r in base["rulesets"]), [26743254, 26932758])
        sample = json.loads((HERE.parents[1] / "samples" / "multica-night-2026-11-08.json").read_text(encoding="utf-8"))
        self.assertEqual(mn.plan_vs_trusted(sample, g), [], "样例计划和名册的信任基准一致")
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
    def assert_frozen(self, res, reason):
        code, out = res
        self.assertEqual(code, 1, out)
        self.assertIn("已冻结", out)
        self.assertIn(reason, out)
        st = self.state()
        self.assertEqual((st["outcome"], st["frozen"], st["freeze_verified"]), ("UNRECONCILED", True, True))
        self.assertTrue(self.freeze_log.exists(), "冻结命令确实执行了")

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

    def test_max_runs_boundary(self):
        self.started()                      # 开跑时 A、B 各一个 run
        self.edit(lambda w: w["issues"][self.ident("A")].update(status="in_progress"))
        for i in range(17):
            self.add_run("A", "completed", minutes=1)
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""), "19 个 run：不停")
        self.add_run("A", "completed", minutes=6)
        code, out = self.run_cli("tick", now=at(10))
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
        self.assert_frozen(self.run_cli("tick", now=at(10)), "不见了")

    def test_message_gap_is_unreconciled(self):
        self.started()
        self.finish("A")
        self.edit(lambda w: w["messages"].update({w["runs"][0]["id"]: [{"seq": 1}, {"seq": 3}]}))
        self.assert_frozen(self.run_cli("tick", now=at(12)), "序号有缺口")

    def test_out_of_family_change_stops(self):
        self.started()
        self.edit(lambda w: w["issues"]["SBX-1"].update(status="todo", updated_at=at(3)))
        self.assert_stopped(self.run_cli("tick", now=at(5))[1], "SBX-1")

    def test_unauthorized_wakeup_is_disabled_on_stop(self):
        self.started()
        self.edit(lambda w: w["wakeups"].update({self.ident("A"): [{"id": "wk1", "enabled": True}]}))
        self.assert_stopped(self.run_cli("tick", now=at(5))[1], "未授权的 Wakeup")
        self.assertIs(self.get()["wakeups"][self.ident("A")][0]["enabled"], False)

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
        self.assertEqual(code, 1)
        self.assertIn("还没 release 却有 run 开跑", out)
        self.assertIn("容器是不是被重启了", out)
        self.assertEqual(self.issue("V")["status"], "backlog")
        self.assertTrue(all(r["status"] != "queued" for r in self.get()["runs"]))
        self.assertIn(stuck, [r["id"] for r in self.get()["runs"]])

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
        self.assertIn("已冻结", out, "issue get 的输出读不懂，父单核不清：回读不过就冻结")
        self.assertEqual(self.state()["outcome"], "UNRECONCILED")
        self.assertTrue(all(r["status"] == "cancelled" for r in self.get()["runs"]), "读不懂也照样把 run 取消掉")
        self.assertIn("crash", [r["event"] for r in self.log()])

    def test_frozen_night_cannot_be_released_non_interactively(self):
        """P1-07：冻结的夜跑只能由常浩在交互式终端里 release；agent 的非交互调用拒绝。"""
        self.frozen()
        code, out = self.run_cli("release", "--by", "常浩", "--confirm", "night-test:UNRECONCILED", now=at(300))
        self.assertEqual(code, 1)
        self.assertIn("交互式终端", out)
        self.assertTrue((self.root / "night-current").exists())
        self.assertNotIn("released", self.state())

    def test_release_needs_binding_confirm(self):
        self.started()
        self.run_cli("stop", "--reason", "演练", now=at(5))
        code, out = self.run_cli("release", "--by", "常浩", "--confirm", "night-test:FINISHED", now=at(6))
        self.assertEqual(code, 1)
        self.assertIn("night-test:STOPPED", out)
        self.assertEqual(self.run_cli("release", "--by", "常浩", "--confirm", "night-test:STOPPED", now=at(7))[0], 0)
        self.assertFalse((self.root / "night-current").exists())
        self.assertEqual(self.run_cli("tick", now=at(10)), (0, ""))

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

    # ---- 审查补充（#93 独立审查第 1–9 条）----
    def test_stop_continues_past_unreadable_step_and_freezes(self):
        """停机途中某一步读不懂：记下来、接着取消 run，最后回读不过就冻结，不会卡在半路。"""
        self.started()
        self.edit(lambda w: w.update(weird_wakeups=True))
        code, out = self.run_cli("tick", now=at(241))
        self.assertEqual(code, 1, out)
        self.assertIn("已冻结", out)
        self.assertTrue(all(r["status"] == "cancelled" for r in self.get()["runs"]))
        self.assertEqual(self.freeze_log.read_text().split(), ["stop"])

    def test_stray_run_outside_family_stops_and_is_cancelled(self):
        self.started()
        self.edit(lambda w: w["runs"].append({"id": "run-stray", "issue": "SBX-1", "status": "running", "created_at": at(2),
                                              "agent_id": "agent-claude", "attempt": 1, "result": {}}))
        code, out = self.run_cli("tick", now=at(5))
        self.assert_stopped(out, "家族外开了 run")
        self.assertEqual(next(r for r in self.get()["runs"] if r["id"] == "run-stray")["status"], "cancelled")

    def test_chat_run_counts_as_stray(self):
        self.started()
        self.edit(lambda w: w["runs"].append({"id": "run-chat", "issue": None, "status": "queued", "created_at": at(2),
                                              "agent_id": "agent-codex", "attempt": 1, "result": {}}))
        self.assert_stopped(self.run_cli("tick", now=at(5))[1], "家族外开了 run")

    def test_old_runs_outside_family_before_start_are_ignored(self):
        self.started()
        self.edit(lambda w: w["runs"].append({"id": "run-old", "issue": "SBX-1", "status": "completed",
                                              "created_at": "2026-11-07T06:00:00Z", "agent_id": "agent-claude", "result": {}}))
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))

    def test_stop_revokes_pending_scheduled_start(self):
        self.approved()
        code, out = self.run_cli("stop", "--reason", "发现问题", now="2026-11-08T13:45:00Z")
        self.assertEqual(code, 0, out)
        self.assertIn("已撤销", out)
        self.assertEqual(self.run_cli("tick", now=at(1)), (0, ""))
        self.assertEqual(self.issue("A")["status"], "backlog")
        self.assertNotIn("auth", self.state())

    def test_plan_refuses_to_replace_unreleased_night(self):
        self.started()
        other = json.loads(self.plan_file.read_text(encoding="utf-8"))
        other["id"] = "night-drill"
        f = self.tmp / "drill.json"
        f.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
        code, out = self.run_cli("plan", str(f), now=at(3))
        self.assertEqual(code, 1)
        self.assertIn("没人看守", out)
        self.assertEqual((self.root / "night-current").read_text(), "night-test")
        self.run_cli("stop", "--reason", "演练结束", now=at(4))
        self.assertEqual(self.run_cli("release", "--by", "常浩", "--confirm", "night-test:STOPPED", now=at(5))[0], 0)
        self.assertEqual(self.run_cli("plan", str(f), now=at(6))[0], 0)

    def test_later_stage_started_early_stops(self):
        self.started()
        self.edit(lambda w: w["issues"][self.ident("V")].update(status="todo"))
        code, out = self.run_cli("tick", now=at(5))
        self.assertIn("被提前移出 backlog", out)
        self.assertEqual(self.state()["outcome"], "STOPPED")
        self.assertEqual(self.issue("V")["status"], "blocked")

    def test_interrupted_advance_is_resumed(self):
        """上次推进只移了 A 就被杀：下次 tick 补推进 B，不会一声不响等到时长上限。"""
        self.started()
        b = self.ident("B")
        self.edit(lambda w: (w["issues"][b].update(status="backlog"), w.update(runs=[r for r in w["runs"] if r["issue"] != b])))
        code, out = self.run_cli("tick", now=at(5))
        self.assertEqual(code, 0, out)
        self.assertIn("已补上", out)
        self.assertEqual(self.issue("B")["status"], "todo")
        self.assertEqual(self.state()["advanced"]["1"], T0, "停滞仍按最初推进时刻算")

    def test_half_done_stop_is_resumed(self):
        """停机做到一半进程被杀（state 里留着 stopping）：下次 tick 接着停，不推进。"""
        self.started()
        st = self.state()
        st["stopping"] = {"at": at(3), "reasons": ["到达时长上限（演练）"], "finished": False}
        (self.root / "night-test" / "state.json").write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        code, out = self.run_cli("tick", now=at(5))
        self.assert_stopped(out, "上次停机没做完")

    def test_parent_reassigned_is_restored_on_stop(self):
        self.started()
        self.edit(lambda w: w["issues"][self.ident("PARENT")].update(assignee_type="agent", assignee_id="agent-claude"))
        self.assert_stopped(self.run_cli("tick", now=at(5))[1], "负责人不是计划里的常浩")
        self.assertEqual(self.issue("PARENT")["assignee_id"], OWNER)

    def test_runs_after_finish_are_cancelled_until_release(self):
        self.started()
        for k in ("A", "B"):
            self.finish(k)
        self.run_cli("tick", now=at(15))
        self.finish("V", minutes=20)
        self.assertIn("全部 stage 已交付", self.run_cli("tick", now=at(25))[1])
        self.add_run("A", "running", minutes=30)
        code, out = self.run_cli("tick", now=at(35))
        self.assertEqual(code, 1)
        self.assertIn("还没 release 却有 run 开跑", out)
        self.assertEqual(self.get()["runs"][-1]["status"], "cancelled", "P1-04：release 前结束的夜跑不让新 run 跑")
        self.assertEqual(self.run_cli("tick", now=at(40)), (0, ""))

    def test_stuck_tick_triggers_emergency_freeze(self):
        self.started()
        import fcntl
        lock = open(self.root / "night-test" / "tick.lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        lock.write(at(1))
        lock.flush()
        try:
            self.assertEqual(self.run_cli("tick", now=at(10)), (0, ""), "持锁不到 15 分钟：静默跳过")
            code, out = self.run_cli("tick", now=at(20))
            self.assertEqual(code, 1)
            self.assertIn("应急冻结", out)
            self.assertIn("持锁 19 分钟", out)
            self.assertEqual(self.freeze_log.read_text().split(), ["stop"], "不等卡住的主控，直接硬停")
            self.assertTrue((self.root / "night-test" / "emergency.json").exists())
            self.assertNotIn("frozen", self.state(), "不碰持锁进程的 state.json")
        finally:
            lock.close()
        code, out = self.run_cli("tick", now=at(25))
        self.assertEqual(code, 1)
        self.assertIn("已触发应急冻结", out)
        self.assertEqual((self.state()["outcome"], self.state()["frozen"]), ("UNRECONCILED", True))
        self.finish("A")
        self.finish("B")
        self.run_cli("tick", now=at(30))
        self.assertEqual(self.issue("V")["status"], "backlog", "应急冻结后绝不推进")

    # ---- ChatGPT High 第 1 轮（#93）----
    def test_p0_vanished_run_with_others_completed_still_freezes(self):
        """P0-01：历史 run A 消失，API 只返回已结束的 B：不能判 STOPPED，必须冻结。"""
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        a = self.ident("A")
        self.edit(lambda w: (w.update(runs=[r for r in w["runs"] if r["issue"] != a]),
                             [r.update(status="completed", completed_at=at(6)) for r in w["runs"]]))
        self.assert_frozen(self.run_cli("tick", now=at(10)), "不见了")

    def test_p1_01_start_refuses_identity_drift(self):
        self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")
        self.edit(lambda w: w["issues"][self.ident("V")].update(assignee_id="agent-other"))
        code, out = self.run_cli("start", "--approved-by", "Hermes")
        self.assertEqual(code, 1)
        self.assertIn("负责人不是计划里的 codex", out)
        self.assertEqual(self.issue("A")["status"], "backlog")

    def test_p1_01_parent_owned_by_other_member_refused(self):
        self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")
        self.edit(lambda w: w["issues"][self.ident("PARENT")].update(assignee_id="member-someone"))
        code, out = self.run_cli("start", "--approved-by", "Hermes")
        self.assertEqual(code, 1)
        self.assertIn("负责人不是计划里的常浩", out)

    def test_p1_01_reassigned_backlog_child_stops_night(self):
        self.started()
        self.edit(lambda w: w["issues"][self.ident("V")].update(assignee_id="agent-other"))
        self.assert_stopped(self.run_cli("tick", now=at(5))[1], "负责人不是计划里的 codex")
        self.assertEqual(self.issue("V")["status"], "backlog")

    def test_p1_03_unverified_freeze_is_retried(self):
        self.set_procs(state="true unless-stopped")
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        self.edit(lambda w: w["runs"].pop(0))
        code, out = self.run_cli("tick", now=at(10))
        self.assertEqual(code, 1)
        self.assertIn("执行器没确认停住", out)
        self.assertIs(self.state()["freeze_verified"], False)
        code, out = self.run_cli("tick", now=at(15))
        self.assertEqual(code, 1)
        self.assertIn("执行器没确认停住", out)
        self.assertEqual(len(self.freeze_log.read_text().split()), 2, "每次检查都再试一次硬停")
        self.set_procs(state="false no")
        code, out = self.run_cli("tick", now=at(20))
        self.assertIn("已停隔离执行器容器", out)
        self.assertIs(self.state()["freeze_verified"], True)
        self.assertEqual(self.run_cli("tick", now=at(25)), (0, ""))

    def test_p1_05_earlier_stage_reentry_blocks_advance(self):
        self.started()
        self.finish("A")
        self.finish("B")
        self.assertIn("进入 stage 2", self.run_cli("tick", now=at(15))[1])
        self.finish("V", minutes=20)
        self.add_run("A", "running", minutes=21)
        code, out = self.run_cli("tick", now=at(25))
        self.assertIn("异常重入", out)
        self.assertNotIn("全部 stage 已交付", out)
        self.assertEqual(self.state()["outcome"], "STOPPED")
        self.assertTrue(all(r["status"] in ("completed", "cancelled") for r in self.get()["runs"]))

    def test_p1_06_cancelled_runs_get_final_run_log_lines(self):
        self.started()
        self.edit(lambda w: [r.update(status="running") for r in w["runs"]])
        self.run_cli("tick", now=at(241))
        lines = [r for r in self.log() if r["event"] == "run"]
        self.assertEqual(sorted(r["status"] for r in lines), ["cancelled", "cancelled"])
        self.assertEqual({r["run_id"] for r in lines}, {r["id"] for r in self.get()["runs"]})

    def test_p2_01_manual_stop_crash_falls_back_to_freeze(self):
        self.started()
        code, out = self.run_cli("stop", "--reason", "演练", now=at(5), env={"DT_NIGHT_READBACK": "坏"})
        self.assertEqual(code, 1, out)
        self.assertIn("已冻结", out)
        self.assertIn("停机过程异常", out)
        self.assertTrue(self.freeze_log.exists())

    def test_p2_02_cancelled_child_does_not_release_next_stage(self):
        self.started()
        self.finish("A", run_status="cancelled", issue_status="cancelled")
        self.finish("B")
        self.assert_stopped(self.run_cli("tick", now=at(15))[1], "被取消")
        self.assertEqual(self.issue("V")["status"], "backlog")

    def test_p2_03_invalid_seq_is_evidence_gap(self):
        self.started()
        self.finish("A")
        self.edit(lambda w: w["messages"].update({w["runs"][0]["id"]: [{"seq": 1}, {"seq": "2"}]}))
        self.assert_frozen(self.run_cli("tick", now=at(12)), "序号有缺口")

    def test_p2_03_no_duplicate_messages_after_crash(self):
        """追加消息后、存状态前进程被杀：重启后按文件里的序号去重。"""
        self.started()
        self.finish("A")
        rid = self.get()["runs"][0]["id"]
        d = self.root / "night-test" / "messages"
        (d / f"{rid}.jsonl").write_text('{"seq": 1}\n{"seq": 2}\n', encoding="utf-8")
        self.assertEqual(self.run_cli("tick", now=at(12)), (0, ""))
        self.assertEqual(len((d / f"{rid}.jsonl").read_text().splitlines()), 2)

    # ---- ChatGPT High 第 2 轮（#93）----
    def test_r2_p0_gap_found_only_at_final_export_freezes(self):
        """回读通过，最后一次导出才发现消息序号缺口：不能写 STOPPED，要冻结。"""
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        self.edit(lambda w: w["messages"].update({w["runs"][0]["id"]: [{"seq": 1}, {"seq": 3}]}))
        code, out = self.run_cli("stop", "--reason", "演练", now=at(6))
        self.assertEqual(code, 1, out)
        self.assertIn("最后一次导出时发现运行记录核不清", out)
        self.assertEqual((self.state()["outcome"], self.state()["frozen"]), ("UNRECONCILED", True))

    def spawn_after_readback(self, stuck=False):
        """停机时：第 ⑤ 步读过一次 A，回读之后最后导出再读 A 时，A 上冒出一个 running 的 run。"""
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        a = self.ident("A")
        run = {"id": "run-late", "issue": a, "status": "running", "created_at": at(6), "agent_id": "agent-claude",
               "attempt": 1, "result": {}}
        self.edit(lambda w: (w.update(spawn={"on": f"issue get {a}", "nth": 2, "run": run}),
                             w.update(stuck=["run-late"] if stuck else [])))
        return self.run_cli("stop", "--reason", "演练", now=at(6))

    def test_r2_p0_run_appearing_after_readback_is_cancelled_and_rechecked(self):
        code, out = self.spawn_after_readback()
        self.assertEqual(code, 0, out)
        self.assertIn("已停机", out)
        self.assertEqual(next(r for r in self.get()["runs"] if r["id"] == "run-late")["status"], "cancelled")
        self.assertIn("stop_readback_again", [r["event"] for r in self.log()])
        self.assertIn("run-late", {r["run_id"] for r in self.log() if r["event"] == "run"})

    def test_r2_p0_run_appearing_after_readback_that_will_not_stop_freezes(self):
        code, out = self.spawn_after_readback(stuck=True)
        self.assertEqual(code, 1, out)
        self.assertIn("已冻结", out)
        self.assertEqual(self.state()["outcome"], "UNRECONCILED")

    def test_r2_p1_emergency_marker_is_written_before_hard_stop(self):
        """应急冻结：阻断标记先落盘，再停执行器（停容器那一刻标记已经在了）。"""
        marker = self.root / "night-test" / "emergency.json"
        seen = self.tmp / "marker-seen.log"
        self.set_procs(stop_probe=[str(marker), str(seen)])
        self.started()
        import fcntl
        lock = open(self.root / "night-test" / "tick.lock", "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
        lock.write(at(1))
        lock.flush()
        try:
            code, out = self.run_cli("tick", now=at(20))
        finally:
            lock.close()
        self.assertEqual(code, 1, out)
        self.assertEqual(seen.read_text().split(), ["yes"], "停容器时标记已经写好")
        self.assertEqual(json.loads(marker.read_text())["status"], "EMERGENCY_FROZEN")

    def test_r2_p1_requested_marker_alone_blocks_advance(self):
        """应急进程写完标记就崩了（还没停容器）：主控也不再推进，接管为冻结，并补做硬停。"""
        self.started()
        self.finish("A")
        self.finish("B")
        (self.root / "night-test" / "emergency.json").write_text(json.dumps({"status": "EMERGENCY_REQUESTED", "at": at(9)}))
        code, out = self.run_cli("tick", now=at(10))
        self.assertEqual(code, 1)
        self.assertEqual(self.issue("V")["status"], "backlog")
        self.assertIs(self.state()["freeze_verified"], False)
        code, out = self.run_cli("tick", now=at(15))
        self.assertIn("已停隔离执行器容器", out)
        self.assertIs(self.state()["freeze_verified"], True)
        self.assertEqual(self.issue("V")["status"], "backlog")

    def test_r2_p1_stop_and_freeze_both_failing_exits_nonzero(self):
        self.started()
        d = self.root / "night-test"
        os.chmod(d, 0o500)            # 状态写不进去：停机和冻结都会失败
        try:
            code, out = self.run_cli("stop", "--reason", "演练", now=at(5))
        finally:
            os.chmod(d, 0o700)
        self.assertEqual(code, 1, out)
        self.assertIn("停机和冻结都失败了", out)

    def test_r2_p2_existing_file_gap_detected_on_restart(self):
        """导出文件里已有 1、3，state 还没存缺口就被杀：重启后要查出缺口，不能接着从 3 往后导。"""
        self.started()
        self.finish("A")
        rid = self.get()["runs"][0]["id"]
        self.edit(lambda w: w["messages"].update({rid: [{"seq": 1}, {"seq": 2}, {"seq": 3}]}))
        (self.root / "night-test" / "messages" / f"{rid}.jsonl").write_text('{"seq": 1}\n{"seq": 3}\n', encoding="utf-8")
        self.assert_frozen(self.run_cli("tick", now=at(12)), "序号有缺口")

    # ---- ChatGPT High 第 3 轮（#93，驿窗 h1a1b8d23ccb01）----
    def logged_then_stop(self, tamper):
        """A 已记账（终态、消息已导出、logged），之后 tamper 造出缺口，再人工停机。"""
        self.started()
        self.finish("A")
        self.assertEqual(self.run_cli("tick", now=at(12)), (0, ""))
        rid = next(r["id"] for r in self.get()["runs"] if r["issue"] == self.ident("A"))
        self.assertTrue(self.state()["runs"][rid]["logged"])
        tamper(rid)
        return self.run_cli("stop", "--reason", "演练", now=at(13))

    def test_r3_p0_logged_run_remote_gap_at_final_export_freezes(self):
        """已记账的 run 远端后来冒出跳号消息（2 之后是 4）：最后一次导出要重新核对，不能写 STOPPED。"""
        res = self.logged_then_stop(lambda rid: self.edit(lambda w: w["messages"][rid].append({"seq": 4})))
        self.assert_frozen(res, "最后一次导出时发现运行记录核不清")

    def test_r3_p0_logged_run_file_gap_at_final_export_freezes(self):
        """已记账的 run 的消息文件后来缺了中间一行：最后一次导出要重新核对文件，不能写 STOPPED。"""
        def tamper(rid):
            p = self.root / "night-test" / "messages" / f"{rid}.jsonl"
            p.write_text('{"seq": 1}\n{"seq": 3}\n', encoding="utf-8")
        self.assert_frozen(self.logged_then_stop(tamper), "最后一次导出时发现运行记录核不清")

    def test_r3_p1_marker_between_get_and_status_blocks_advance(self):
        """应急标记恰好在 advance 读完 V（issue get）之后、改 todo 之前写出：不推进，转为冻结。"""
        self.started()
        self.finish("A")
        self.finish("B")
        v = self.ident("V")
        marker = self.root / "night-test" / "emergency.json"
        text = json.dumps({"status": "EMERGENCY_REQUESTED", "at": at(19)})
        # 这次 tick 里 issue get V：第 1 次在对账（collect），第 2 次在 advance
        self.edit(lambda w: w.update(spawn={"on": f"issue get {v}", "nth": 2, "write": {"path": str(marker), "text": text}}))
        code, out = self.run_cli("tick", now=at(20))
        w = self.get()
        fired = w["spawn"].get("fired_at")
        self.assertIsNotNone(fired, "标记确实是在 advance 读 V 时写出的")
        self.assertNotEqual(w["calls"][fired + 1]["args"][:3], ["issue", "runs", v], "触发点不是对账里的那次读取")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.issue("V")["status"], "backlog")
        self.assertFalse(any(c["args"][:4] == ["issue", "status", v, "todo"] for c in w["calls"]), "从没把 V 改成 todo")
        self.assertEqual(self.state()["outcome"], "UNRECONCILED")

    def test_r3_p1_marker_during_stop_readback_freezes_and_release_refused(self):
        """停机回读期间应急标记出现：不写 STOPPED，以冻结为准；非交互 release 被拒。"""
        self.started()
        self.assertEqual(self.run_cli("tick", now=at(5)), (0, ""))
        marker = self.root / "night-test" / "emergency.json"
        text = json.dumps({"status": "EMERGENCY_REQUESTED", "at": at(6)})
        # 停机里 autopilot list：第 1 次在 ② 暂停 Autopilot，第 2 次在 ⑥ 回读
        self.edit(lambda w: w.update(spawn={"on": "autopilot list", "nth": 2, "write": {"path": str(marker), "text": text}}))
        code, out = self.run_cli("stop", "--reason", "演练", now=at(6))
        self.assertIsNotNone(self.get()["spawn"].get("fired_at"))
        self.assertEqual(code, 1, out)
        self.assertIn("应急冻结标记", out)
        self.assertNotEqual(self.state()["outcome"], "STOPPED")
        self.assertEqual(self.state()["outcome"], "UNRECONCILED")
        code, out = self.run_cli("release", "--by", "常浩", "--confirm", "night-test:UNRECONCILED", now=at(30))
        self.assertEqual(code, 1, out)
        self.assertIn("交互式终端", out)
        self.assertNotIn("released", self.state())

    def test_r3_p1_marker_after_clean_stop_blocks_non_interactive_release(self):
        """已正常 STOPPED，之后才出现应急标记：release 也按冻结处理，非交互调用拒绝。"""
        self.started()
        code, out = self.run_cli("stop", "--reason", "演练", now=at(5))
        self.assertEqual((code, self.state()["outcome"]), (0, "STOPPED"), out)
        (self.root / "night-test" / "emergency.json").write_text(json.dumps({"status": "EMERGENCY_REQUESTED", "at": at(6)}))
        code, out = self.run_cli("release", "--by", "常浩", "--confirm", "night-test:STOPPED", now=at(7))
        self.assertEqual(code, 1, out)
        self.assertIn("交互式终端", out)
        self.assertNotIn("released", self.state())
        self.assertTrue((self.root / "night-current").exists())

    def test_r3_p2_blank_line_in_message_file_is_gap(self):
        """消息文件中间夹一个空行（1、空、2）：算读不懂，不能当作连续。"""
        self.started()
        self.finish("A")
        rid = self.get()["runs"][0]["id"]
        (self.root / "night-test" / "messages").mkdir(exist_ok=True)
        (self.root / "night-test" / "messages" / f"{rid}.jsonl").write_text('{"seq": 1}\n\n{"seq": 2}\n', encoding="utf-8")
        self.assert_frozen(self.run_cli("tick", now=at(12)), "序号有缺口")

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

    # ---- M5 首夜演练暴露的问题（#97；证据 2026-11-08-M5首夜 00、18、22、24 号）----
    RESIDUE = ["326726 1 /bin/bash -c source /home/node/.claude/shell-snapshots/snap", "326729 326726 python -c import time; time.sleep(900)"]

    def stop_now(self, minutes=13, **procs):
        """开跑（开跑前执行器是干净的），之后执行器里出现 procs 描述的进程，再人工停机。"""
        self.started()
        if procs:
            self.set_procs(**procs)
        return self.run_cli("stop", "--reason", "演练", now=at(minutes))

    def test_m5_25_slow_exiting_procs_are_waited_for_then_stopped(self):
        """取消后 agent 进程收尾慢一点：等它们自己退出再写 STOPPED，不动手结束。"""
        code, out = self.stop_now(procs=self.RESIDUE, exit_after=1)
        self.assertEqual(code, 0, out)
        self.assert_stopped(out, "人工停机")
        self.assertIn("执行器里没有 agent 进程", out)
        self.assertNotIn("kill", self.docker_calls())

    def test_m5_25_residue_without_kill_auth_is_not_stopped_and_freezes(self):
        """演练 2 的反例：Multica 回读全部终态，但容器里后台进程还活着。没授权结束 → 不写 STOPPED，冻结（停容器）。"""
        self.plan_obj["executor"]["kill"] = False
        self.write_plan()
        res = self.stop_now(procs=self.RESIDUE, stubborn=True)
        self.assert_frozen(res, "计划没授权结束它们")
        self.assertNotIn("kill", self.docker_calls())
        self.assertIn("proc_residue", [r["event"] for r in self.log()])

    def test_m5_27_residue_is_killed_then_rechecked_then_stopped(self):
        """授权了结束（executor.kill）：结束残留进程，另起一次独立回读确认为空，才写 STOPPED。"""
        code, out = self.stop_now(procs=self.RESIDUE)
        self.assertEqual(code, 0, out)
        self.assert_stopped(out, "人工停机")
        calls = self.docker_calls()
        self.assertIn("kill", calls)
        self.assertEqual(calls[-1], "list", "结束之后还要独立再列一次")
        kill = [r for r in self.log() if r["event"] == "proc_kill"]
        self.assertEqual((len(kill[0]["before"]), kill[0]["after"]), (2, []))

    def test_m5_27_unkillable_residue_freezes(self):
        self.assert_frozen(self.stop_now(procs=self.RESIDUE, stubborn=True), "结束后执行器里仍有 2 个 agent 进程")

    def test_m5_25_unreadable_executor_freezes(self):
        """docker exec 失败（容器不在、docker 不通）：看不清不等于没有，冻结。"""
        self.started()
        self.set_procs(fail=True)
        self.assert_frozen(self.run_cli("stop", "--reason", "演练", now=at(13)), "执行器进程回读不清")

    def test_m5_25_plan_without_executor_is_rejected(self):
        del self.plan_obj["executor"]
        self.write_plan()
        code, out = self.run_cli("plan", str(self.plan_file))
        self.assertEqual(code, 1)
        self.assertIn("缺 executor", out)

    def test_m5_25_start_refuses_dirty_executor(self):
        self.assertEqual(self.run_cli("plan", str(self.plan_file), now="2026-11-08T13:00:00Z")[0], 0)
        self.set_procs(procs=self.RESIDUE)
        code, out = self.run_cli("start", "--approved-by", "Hermes")
        self.assertEqual(code, 1, out)
        self.assertIn("执行器里已有 2 个非守护进程", out)
        self.assertFalse(self.state().get("auth"))

    def test_m5_25_procs_reappearing_after_stop_are_killed_and_alarmed(self):
        code, out = self.stop_now()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.run_cli("tick", now=at(15)), (0, ""))
        self.set_procs(procs=self.RESIDUE)
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 1)
        self.assertIn("执行器里又出现 2 个 agent 进程，已结束并独立回读确认", out)
        self.assertEqual(self.docker_calls()[-2:], ["kill", "list"], "结束之后另起一次独立回读")
        self.assertEqual(self.state()["outcome"], "STOPPED", "结束干净、回读为空：保持 STOPPED")
        self.assertEqual(self.run_cli("tick", now=at(25)), (0, ""))

    def test_m5_25_unkillable_procs_after_stop_revoke_stopped_and_freeze(self):
        """Hermes 第 4 轮：STOPPED 之后冒出的进程结束不掉，不能只提示「请手动停容器」——撤销 STOPPED，冻结（停容器）。"""
        code, out = self.stop_now()
        self.assertEqual(code, 0, out)
        self.set_procs(procs=self.RESIDUE, stubborn=True)
        res = self.run_cli("tick", now=at(20))
        self.assert_frozen(res, "结束后仍剩 2 个")
        self.assertIn("撤销原结果", res[1])
        self.assertIn("outcome_revoked", [r["event"] for r in self.log()])
        self.assertEqual(self.run_cli("release", "--by", "Hermes", "--confirm", "night-test:STOPPED", now=at(21))[0], 1,
                         "原结果已撤销，不能再按 STOPPED release")

    def test_m5_25_unreadable_executor_after_stop_revokes_and_freezes(self):
        """Hermes 第 4 轮：STOPPED 之后核对执行器读不清（docker 不通），看不清不等于干净——撤销 STOPPED，冻结。"""
        code, out = self.stop_now()
        self.assertEqual(code, 0, out)
        self.set_procs(fail=True)
        self.assert_frozen(self.run_cli("tick", now=at(20)), "核对执行器进程失败")

    def test_m5_25_unauthorized_procs_after_stop_revoke_and_freeze(self):
        self.plan_obj["executor"]["kill"] = False
        self.write_plan()
        code, out = self.stop_now()
        self.assertEqual(code, 0, out)
        self.set_procs(procs=self.RESIDUE)
        self.assert_frozen(self.run_cli("tick", now=at(20)), "计划没授权结束它们")
        self.assertNotIn("kill", self.docker_calls())

    def test_m5_25_verified_freeze_skips_executor_check(self):
        """已冻结且确认容器停住：不再 docker exec 进去查（容器停着，exec 必然失败），也不重复告警。"""
        self.frozen()
        self.edit(lambda w: (w.update(stuck=[]), [r.update(status="cancelled") for r in w["runs"]]))
        n = len(self.docker_calls())
        self.assertEqual(self.run_cli("tick", now=at(250)), (0, ""))
        self.assertEqual(len(self.docker_calls()), n)

    def test_m5_25_remote_snapshot_after_stop_and_change_alarms_once(self):
        """停机后给远端拍快照；之后远端还在变（后台进程推了提交）就告警一次，晨报里也写出来。"""
        code, out = self.stop_now()
        self.assertEqual(code, 0, out)
        snap = self.state()["remote_at_stop"]
        self.assertEqual(snap["branches"], {"main": "aaa111", "agent/claude/abc": "bbb222"})
        self.assertIn("远端：停机（", self.run_cli("report", now=at(14))[1])
        self.assertIn("后没有变化", self.run_cli("report", now=at(14))[1])
        self.branches.write_text("main aaa111\nagent/claude/abc ccc333\n", encoding="utf-8")
        code, out = self.run_cli("tick", now=at(20))
        self.assertEqual(code, 1)
        self.assertIn("停机后远端有变化：分支 agent/claude/abc：bbb222 → ccc333", out)
        self.assertEqual(self.run_cli("tick", now=at(25)), (0, ""), "同样的变化不重复告警")
        self.assertIn("⚠️ 停机后有变化", self.run_cli("report", now=at(26))[1])

    def test_m5_25_frozen_night_also_snapshots_remote(self):
        self.frozen()
        self.assertIn("branches", self.state()["remote_at_stop"])

    def test_m5_27_orphans_mid_night_are_killed_and_night_continues(self):
        """夜跑途中：A 的 run 结束了，它放到后台的进程被 docker-init 收养（PPID 1）。每次检查都清掉孤儿、告警，夜跑照常推进；
        还挂在活着的 agent 底下的进程（PPID 不是 1）不动。"""
        self.started()
        live = "500 480 node /usr/local/bin/claude"
        self.set_procs(procs=[self.RESIDUE[0], live])
        code, out = self.run_cli("tick", now=at(5))
        self.assertEqual(code, 0, out)
        self.assertIn("1 个失去 agent 的后台进程", out)
        self.assertEqual(json.loads(self.procs.read_text(encoding="utf-8"))["procs"], [live])
        self.assertFalse(self.state().get("outcome"))

    def test_m5_27_orphans_without_kill_auth_stop_the_night(self):
        self.plan_obj["executor"]["kill"] = False
        self.write_plan()
        self.started()
        self.set_procs(procs=[self.RESIDUE[0]], stubborn=True)
        code, out = self.run_cli("tick", now=at(5))
        self.assertIn("计划没授权结束它们", out)
        self.assertEqual(self.state()["outcome"], "UNRECONCILED", "停机时残留还在、又没授权结束 → 冻结")

    def test_m5_27_unreadable_executor_mid_night_stops(self):
        self.started()
        self.set_procs(fail=True)
        code, out = self.run_cli("tick", now=at(5))
        self.assertIn("执行器孤儿进程", out)
        self.assertTrue(self.state().get("outcome"))

    def test_m5_21_truncated_tail_of_logged_run_is_gap(self):
        """已记账的 run，消息文件尾部被截掉（文件里最大序号小于已记账的 last_seq）：要查出缺口，不能写 STOPPED。"""
        def tamper(rid):
            p = self.root / "night-test" / "messages" / f"{rid}.jsonl"
            p.write_text('{"seq": 1}\n', encoding="utf-8")
        self.assert_frozen(self.logged_then_stop(tamper), "最后一次导出时发现运行记录核不清")

    def test_m5_21_deleted_message_file_of_logged_run_is_gap(self):
        self.assert_frozen(self.logged_then_stop(
            lambda rid: (self.root / "night-test" / "messages" / f"{rid}.jsonl").unlink()), "最后一次导出时发现运行记录核不清")


if __name__ == "__main__":
    unittest.main()
