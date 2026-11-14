"""运行前身份闸（#174，PR #175 第 2 轮 P2-01）采集层集成测试：用假的 gh、git 可执行文件真跑 IDENT_SCRIPT，
再交给 identity_problems 判，覆盖分页、部分查询失败、权限字段缺失、多账号、不同结构的 gh 登录输出、git 配置读取出错和备用凭据。
不联网，不碰真实容器、~/.config 和 ~/Library：HOME 指向临时目录，PATH 只放假 gh、git 和系统目录。"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("multica_night_ident", HERE.parent / "multica_night.py")
mn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mn)

REPO, LOGIN, IDS = "owner/dt-sandbox", "bot", [1, 2]
SECRET = "SECRETVALUE0123456789abcdefghijklmnop"
LEGACY_HOSTS = "github.com:\n    oauth_token: gho_" + "x" * 36 + "\n    user: bot\n    git_protocol: https\n"
HELPERS = [["global", "file:/home/node/.gitconfig", "credential.https://github.com.helper", ""],
           ["global", "file:/home/node/.gitconfig", "credential.https://github.com.helper", "!/usr/bin/gh auth git-credential"],
           ["global", "file:/home/node/.gitconfig", "credential.https://gist.github.com.helper", ""],
           ["global", "file:/home/node/.gitconfig", "credential.https://gist.github.com.helper", "!/usr/bin/gh auth git-credential"],
           ["system", "file:/etc/gitconfig", "init.defaultbranch", "main"]]


def gh_scenario(**kw):
    s = {"version": "gh version 2.23.0 (2023-02-07)", "auth_json": None, "user": {"login": LOGIN},
         "repo": {"full_name": REPO, "permissions": {"admin": False, "maintain": False, "push": True, "triage": True, "pull": True}},
         "rulesets_pages": [[{"id": 1}, {"id": 2}]], "rulesets": {"1": {"current_user_can_bypass": "never"}, "2": {"current_user_can_bypass": "never"}},
         "repos_pages": [[{"full_name": REPO}]]}
    s.update(kw)
    return s


class IdentScript(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-ident-"))
        self.home = self.tmp / "home"
        self.ws = self.home / "multica_workspaces"
        self.repo_dir = self.ws / "ws1" / "task1" / "dt-sandbox"
        (self.repo_dir / ".git").mkdir(parents=True)
        (self.home / ".config" / "gh").mkdir(parents=True)
        self.write_hosts(LEGACY_HOSTS)
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        for name, target in (("gh", "fake_gh_exec.py"), ("git", "fake_git.py")):
            f = bin_ / name
            f.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{HERE / target}' \"$@\"\n", encoding="utf-8")
            f.chmod(0o755)
        (bin_ / "python3").symlink_to(sys.executable)
        self.gh_file, self.git_file = self.tmp / "gh.json", self.tmp / "git.json"
        self.set_gh()
        self.set_git()
        self.env = {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(self.home), "LANG": "C.UTF-8",
                    "MULTICA_WORKSPACES_ROOT": str(self.ws), "FAKE_GH_SCENARIO": str(self.gh_file), "FAKE_GIT_SCENARIO": str(self.git_file),
                    "GIT_AUTHOR_NAME": "Multica agent", "GIT_AUTHOR_EMAIL": "a@x.invalid",
                    "GIT_COMMITTER_NAME": "Multica agent", "GIT_COMMITTER_EMAIL": "a@x.invalid"}

    def tearDown(self):
        subprocess.run(["rm", "-rf", str(self.tmp)], check=False)

    def write_hosts(self, text):
        (self.home / ".config" / "gh" / "hosts.yml").write_text(text, encoding="utf-8")

    def set_gh(self, **kw):
        self.gh_file.write_text(json.dumps(gh_scenario(**kw)), encoding="utf-8")

    def set_git(self, global_=None, local=None, fail=None):
        local = [["local", "file:.git/config", "remote.origin.url", "https://github.com/owner/dt-sandbox.git"]] if local is None else local
        self.git_file.write_text(json.dumps({"global": HELPERS if global_ is None else global_, "repos": {str(self.repo_dir): local},
                                             "fail": fail or {}}), encoding="utf-8")

    def collect(self, env=None, root=None):
        r = subprocess.run(["bash", "-c", mn.IDENT_SCRIPT, "ident", REPO, root or str(self.ws)], capture_output=True, text=True, timeout=60,
                           env={**self.env, **(env or {})})
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def problems(self, env=None, root=None):
        facts = self.collect(env, root)
        return facts, mn.identity_problems(facts, REPO, LOGIN, IDS, str(self.ws))

    def gh_calls(self):
        return json.loads(self.gh_file.read_text(encoding="utf-8")).get("calls", [])

    # ---- 正向 ----
    def test_good_container_passes_and_lists_are_paginated(self):
        facts, why = self.problems()
        self.assertEqual(why, [])
        self.assertTrue(facts["gh_json_unsupported"], "gh 2.23 不支持 --json，退回解析 hosts.yml")
        self.assertEqual(facts["git_repos"], 1)
        calls = self.gh_calls()
        self.assertIn(["api", f"repos/{REPO}/rulesets", "--paginate"], calls)
        self.assertIn(["api", "user/repos", "--paginate"], calls)

    def test_newer_gh_json_output_passes(self):
        self.set_gh(version="gh version 2.90.0", auth_json={"hosts": {"github.com": [
            {"state": "success", "active": True, "host": "github.com", "login": LOGIN, "tokenSource": "oauth_token", "scopes": "repo"}]}})
        self.write_hosts("github.com:\n    users:\n        bot:\n            oauth_token: gho_x\n    git_protocol: https\n    user: bot\n")
        facts, why = self.problems()
        self.assertEqual(why, [])
        self.assertNotIn("scopes", json.dumps(facts["gh_json"]), "只取判定要用的字段")

    # ---- P1-02：规则集分页 ----
    def test_bypass_on_second_page_of_rulesets_is_found(self):
        ids = list(range(1, 33))
        self.set_gh(rulesets_pages=[[{"id": i} for i in ids[:30]], [{"id": i} for i in ids[30:]]],
                    rulesets={**{str(i): {"current_user_can_bypass": "never"} for i in ids}, "32": {"current_user_can_bypass": "always"}})
        facts, why = self.problems()
        self.assertEqual(len(facts["rulesets"]), 32, "第二页也收到了")
        text = "；".join(why)
        self.assertIn("32=always", text)
        self.assertIn("规则集和基线不一致", text)

    # ---- P1-03：权限字段严格 ----
    def test_missing_or_mistyped_permission_fields_are_refused(self):
        for perms, expect in (({"admin": False, "push": True, "triage": True, "pull": True}, "maintain"),
                              ({"admin": "false", "maintain": False, "push": True, "triage": True, "pull": True}, "admin"),
                              ({"admin": False, "maintain": False, "push": 1, "triage": True, "pull": True}, "push 不是 true"),
                              ({"admin": None, "maintain": None, "push": True, "triage": True, "pull": True}, "admin / maintain 不是 false")):
            with self.subTest(perms=perms):
                self.set_gh(repo={"permissions": perms})
                why = "；".join(self.problems()[1])
                self.assertIn(expect, why)

    # ---- 部分查询失败 ----
    def test_partial_query_failure_fails_closed_and_is_scrubbed(self):
        self.set_gh(fail={"user/repos": [1, f"HTTP 502 Bad Gateway token={SECRET} Authorization: bearer {SECRET}"]})
        facts, why = self.problems()
        text = "；".join(why)
        self.assertTrue(why)
        self.assertIn("gh api user/repos 失败（退出码 1）", text)
        self.assertNotIn(SECRET, json.dumps(facts) + text, "报错里的凭据样式片段要先脱敏")

    def test_ruleset_detail_unreadable_fails_closed(self):
        self.set_gh(rulesets={"1": {"current_user_can_bypass": "never"}})
        self.assertIn("规则集 2 失败", "；".join(self.problems()[1]))

    # ---- P1-04：gh 登录状态 ----
    def test_multiple_accounts_in_hosts_file_are_refused(self):
        self.write_hosts("github.com:\n    users:\n        bot:\n            oauth_token: a\n        Natsukashii1997:\n"
                         "            oauth_token: b\n    git_protocol: https\n    user: bot\n")
        self.assertIn("其他账号", "；".join(self.problems()[1]))

    def test_other_host_in_hosts_file_is_refused(self):
        self.write_hosts(LEGACY_HOSTS + "ghe.example.com:\n    oauth_token: c\n    user: bot\n")
        self.assertIn("其他主机", "；".join(self.problems()[1]))

    def test_unrecognized_hosts_file_structure_is_refused(self):
        for text in ("- github.com\n", "github.com:\n\tuser: bot\n", "github.com:\n    user: bot\n  stray: 1\n", "github.com: bot\n"):
            with self.subTest(text=text):
                self.write_hosts(text)
                self.assertIn("结构不认识", "；".join(self.problems()[1]))

    def test_unknown_key_in_hosts_file_is_refused(self):
        self.write_hosts(LEGACY_HOSTS + "    api_key: z\n")
        self.assertIn("不认识的键", "；".join(self.problems()[1]))

    def test_gh_json_shapes(self):
        ok = {"state": "success", "active": True, "host": "github.com", "login": LOGIN, "tokenSource": "oauth_token"}
        for hosts, expect in (({"github.com": [{**ok, "tokenSource": "GH_TOKEN"}]}, "令牌来源不认识"),
                              ({"github.com": [ok, {**ok, "login": "Natsukashii1997", "active": False}]}, "其他主机或账号"),
                              ({"github.com": [ok], "ghe.example.com": [ok]}, "其他主机或账号"),
                              ({"github.com": [{**ok, "state": "error"}]}, "应为 bot"),
                              ({"github.com": [{**ok, "host": "ghe.example.com"}]}, "主机和外层不一致")):
            with self.subTest(hosts=hosts):
                self.set_gh(auth_json={"hosts": hosts})
                self.assertIn(expect, "；".join(self.problems()[1]))
        for entry in ("oops", {**ok, "active": "true"}, {k: v for k, v in ok.items() if k != "tokenSource"}):
            with self.subTest(entry=entry):       # 第 2 轮 P2-02：账号记录字段缺失或类型不对，判为看不清
                self.set_gh(auth_json={"hosts": {"github.com": [entry]}})
                with self.assertRaises(mn.Unclear):
                    self.problems()

    def test_gh_auth_status_nonzero_exit_is_unclear_unless_old_gh(self):
        ok = {"state": "success", "active": True, "host": "github.com", "login": LOGIN, "tokenSource": "oauth_token"}
        self.set_gh(auth_json={"hosts": {"github.com": [ok]}}, auth_code=1)
        self.assertIn("gh auth status --json hosts 失败（退出码 1）", "；".join(self.problems()[1]))

    def test_gh_auth_status_other_failure_is_unclear(self):
        self.set_gh(auth_json="not-a-dict")
        self.assertIn("gh auth status --json hosts 失败", "；".join(self.problems()[1]))

    def test_no_gh_login_file_is_refused(self):
        (self.home / ".config" / "gh" / "hosts.yml").unlink()
        self.assertIn("没有 gh 登录文件", "；".join(self.problems()[1]))

    # ---- P1-04：git 配置 ----
    def test_git_config_read_error_is_unclear(self):
        for fail, expect in (({"global": [128, "fatal: bad config line 3 in file /home/node/.gitconfig"]}, "git config（system / global） 失败"),
                             ({str(self.repo_dir): [128, "fatal: detected dubious ownership"]}, "git config（")):
            with self.subTest(fail=fail):
                self.set_git(fail=fail)
                self.assertIn(expect, "；".join(self.problems()[1]))

    def test_backup_credentials_are_each_refused_without_echoing_values(self):
        home_ = str(self.home)
        cases = [
            ("file", ".git-credentials", "其他凭据文件：~/.git-credentials"),
            ("file", ".netrc", "其他凭据文件：~/.netrc"),
            ("file", ".ssh/id_ed25519", "~/.ssh/id_ed25519"),
            ("env", {"GITHUB_TOKEN": SECRET}, "GITHUB_TOKEN"),
            ("env", {"GH_CONFIG_DIR": home_}, "GH_CONFIG_DIR"),
            ("env", {"XDG_CONFIG_HOME": home_}, "XDG_CONFIG_HOME"),
            ("env", {"GIT_ASKPASS": "/tmp/x"}, "GIT_ASKPASS"),
            ("env", {"GIT_SSH_COMMAND": "ssh -i k"}, "GIT_SSH_COMMAND"),
            ("env", {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper", "GIT_CONFIG_VALUE_0": SECRET}, "GIT_CONFIG_COUNT"),
            ("env", {"MY_DEPLOY_TOKEN": SECRET}, "MY_DEPLOY_TOKEN"),
            ("global", ["global", "file:/home/node/.gitconfig", "credential.helper", "store"], "git 凭据配置不认识：credential.helper（global"),
            ("global", ["global", "file:/home/node/.gitconfig", "credential.helper", f"!f() {{ echo password={SECRET}; }}; f"], "credential.helper"),
            ("global", ["global", "file:/home/node/.gitconfig", "credential.https://github.com.username", "bot"], "credential.<…>.username"),
            ("global", ["global", "file:/home/node/.gitconfig", f"url.https://bot:{SECRET}@github.com/.insteadof", "https://github.com/"],
             "改写了远端地址：url.<…>.insteadof"),
            ("global", ["global", "file:/home/node/.gitconfig", f"credential.https://bot:{SECRET}@github.com.helper", "store"],
             "git 凭据配置不认识：credential.<…>.helper"),
            ("local", ["local", "file:.git/config", f"http.https://bot:{SECRET}@github.com/.extraheader", "x"], "http.<…>.extraheader"),
            ("global", ["system", "file:/etc/gitconfig", "include.path", "/opt/x.inc"], "引入了其他文件：include.path（system"),
            ("global", ["global", "file:/home/node/.gitconfig", "url.https://evil.example/.insteadof", "https://github.com/"], "改写了远端地址"),
            ("global", ["global", "file:/home/node/.gitconfig", "core.sshcommand", "ssh -i /k"], "认证程序：core.sshcommand"),
            ("global", ["command", "command line:", "credential.helper", "store"], "配置来源不认识"),
            ("local", ["local", "file:.git/config", "http.https://github.com/.extraheader", f"AUTHORIZATION: basic {SECRET}"], "extraheader"),
            ("local", ["local", "file:.git/config", "remote.origin.url", f"https://bot:{SECRET}@github.com/owner/dt-sandbox.git"], "带了账号"),
            ("local", ["local", "file:.git/config", "remote.origin.url", "https://github.com/owner/digital-twin.git"], "远端不是 owner/dt-sandbox"),
            ("local", ["worktree", "file:.git/config.worktree", "remote.fork.pushurl", "git@github.com:owner/dt-sandbox.git"], "remote.<…>.pushurl"),
            ("local", ["local", "file:.git/config", f"remote.{SECRET}.url", "https://github.com/owner/digital-twin"], "remote.<…>.url"),
        ]
        for kind, val, expect in cases:
            with self.subTest(kind=kind, val=val):
                self.set_git()
                env = {}
                made = None
                if kind == "file":
                    made = self.home / val
                    made.parent.mkdir(parents=True, exist_ok=True)
                    made.write_text(SECRET, encoding="utf-8")
                elif kind == "env":
                    env = val
                elif kind == "global":
                    self.set_git(global_=HELPERS + [val])
                else:
                    self.set_git(local=[["local", "file:.git/config", "remote.origin.url", "https://github.com/owner/dt-sandbox.git"], val])
                try:
                    facts, why = self.problems(env)
                finally:
                    if made:
                        made.unlink()
                text = "；".join(why)
                self.assertIn(expect, text)
                self.assertNotIn(SECRET, text, "拒绝原因不回显凭据值")

    def test_multica_identity_include_is_the_only_include_allowed(self):
        """2026-11-14 真机：Multica 在每个工作树的 config.worktree 里 include 一份提交身份，只放行这一种写法。"""
        wt = f"file:{self.ws}/.repos/ws/dt-sandbox.git/worktrees/dt-sandbox/"
        origin, ident = wt + "config.worktree", wt + "multica-identity.config"
        base = [["local", "file:.git/config", "remote.origin.url", "https://github.com/owner/dt-sandbox.git"],
                ["worktree", origin, "include.path", "multica-identity.config"],
                ["worktree", ident, "user.name", "Multica agent"], ["worktree", ident, "committer.email", "a@x.invalid"]]
        self.set_git(local=base)
        self.assertEqual(self.problems()[1], [])
        for row, expect in ((["worktree", origin, "include.path", "/home/node/other.config"], "引入了其他文件"),
                            (["worktree", "file:/x/config", "include.path", "multica-identity.config"], "引入了其他文件"),
                            (["worktree", ident, "credential.helper", "store"], "Multica 身份文件里有身份以外的配置"),
                            (["worktree", ident, "core.hookspath", "/tmp/h"], "Multica 身份文件里有身份以外的配置"),
                            (["worktree", ident, "http.sslverify", "false"], "Multica 身份文件里有身份以外的配置"),
                            (["worktree", f"file:{self.ws}/.repos/ws/x.git/worktrees/other/multica-identity.config", "user.name", "x"],
                             "来源不对"),
                            (["worktree", "file:/elsewhere/x.git/worktrees/dt-sandbox/config.worktree", "include.path",
                              "multica-identity.config"], "引入了其他文件"),
                            (["worktree", origin, "includeif.onbranch:main.path", "multica-identity.config"], "includeif")):
            with self.subTest(row=row):
                self.set_git(local=base + [row])
                self.assertIn(expect, "；".join(self.problems()[1]))
        self.set_git(global_=HELPERS + [["global", "file:/home/node/.gitconfig", "include.path", "multica-identity.config"]])
        self.assertIn("引入了其他文件：include.path（global", "；".join(self.problems()[1]))

    def test_bare_repo_cache_is_also_checked(self):
        bare = self.ws / ".repos" / "dt-sandbox.git"
        for d in ("objects", "refs"):
            (bare / d).mkdir(parents=True)
        (bare / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        w = json.loads(self.git_file.read_text(encoding="utf-8"))
        w["repos"][str(bare)] = [["local", "file:config", "remote.origin.url", "https://github.com/someone/else"]]
        self.git_file.write_text(json.dumps(w), encoding="utf-8")
        facts, why = self.problems()
        self.assertEqual(facts["git_repos"], 2)
        self.assertIn("远端不是", "；".join(why))


    # ---- 第 2 轮 P1-04：工作区扫描要能证明扫全了 ----
    def test_workspace_root_missing_or_mismatched_is_refused(self):
        self.assertIn("工作区根目录不存在", "；".join(self.problems(root=str(self.tmp / "nowhere"))[1]))
        self.assertIn("工作区目录和名册不一致", "；".join(self.problems(env={"MULTICA_WORKSPACES_ROOT": "/elsewhere"})[1]))
        env = dict(self.env)
        del env["MULTICA_WORKSPACES_ROOT"]
        r = subprocess.run(["bash", "-c", mn.IDENT_SCRIPT, "ident", REPO, str(self.ws)], capture_output=True, text=True, env=env)
        facts = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertIn("MULTICA_WORKSPACES_ROOT 没设", "；".join(mn.identity_problems(facts, REPO, LOGIN, IDS, str(self.ws))))

    def test_zero_repos_is_refused(self):
        subprocess.run(["rm", "-rf", str(self.ws / "ws1")], check=True)
        self.assertIn("一个仓库都没扫到", "；".join(self.problems()[1]))

    def test_symlinks_deep_trees_and_huge_trees(self):
        (self.ws / "ws1" / "task1" / "codex-home" / "plugins").mkdir(parents=True)
        (self.tmp / "outside").mkdir()
        (self.ws / "ws1" / "task1" / "codex-home" / "plugins" / "cache").symlink_to(self.tmp / "outside", target_is_directory=True)
        deep = self.ws / "ws1" / "task2" / "codex-home" / "sessions" / "2026" / "11" / "14" / "a" / "b"
        deep.mkdir(parents=True)
        facts, why = self.problems()
        self.assertEqual(why, [], "指向工作区外非仓库目录的符号链接、很深的非仓库目录都正常（真机就是这样）")
        (deep / "repo" / ".git").mkdir(parents=True)
        self.assertEqual(self.collect()["git_repos"], 2, "不限深度，深处的仓库也扫到")
        other = self.tmp / "elsewhere"
        (other / ".git").mkdir(parents=True)
        (self.ws / "ws1" / "linked").symlink_to(other, target_is_directory=True)
        self.assertIn("指向仓库的目录符号链接", "；".join(self.problems()[1]))
        (self.ws / "ws1" / "linked").unlink()
        many = self.ws / "many"
        for i in range(20001):
            (many / str(i)).mkdir(parents=True)
        self.assertIn("目录超过 20000 个", "；".join(self.problems()[1]))


class FactsSchema(unittest.TestCase):
    """P2-02：事实格式不对一律抛 Unclear，判定函数不让别的异常逃出去。"""

    def good(self):
        return {"v": 2, "errors": [], "login": "bot", "ws_root": "/w", "ws_env": "/w", "git_repos": 1,
                "permissions": {"admin": False, "maintain": False, "push": True, "triage": True, "pull": True},
                "rulesets": [{"id": 1, "bypass": "never"}, {"id": 2, "bypass": "never"}], "repos": [REPO], "gh_version": "x",
                "gh_json": None, "gh_json_unsupported": True,
                "gh_file": {"exists": True, "hosts": {"github.com": {"user": "bot", "users": [], "keys": ["user"], "user_keys": []}}},
                "env_names": [], "cred_files": [], "git": []}

    def test_good_passes(self):
        self.assertEqual(mn.identity_problems(self.good(), REPO, LOGIN, IDS, "/w"), [])

    def test_malformed_facts_raise_unclear(self):
        bads = [None, [], {"v": 1}, {**self.good(), "errors": "x"}, {**self.good(), "login": None}, {**self.good(), "permissions": []},
                {**self.good(), "rulesets": {"1": "never"}}, {**self.good(), "rulesets": ["never"]}, {**self.good(), "repos": "owner/dt-sandbox"},
                {**self.good(), "gh_json": {"hosts": []}}, {**self.good(), "gh_json": {"hosts": {"github.com": {}}}},
                {**self.good(), "gh_json_unsupported": "yes"}, {**self.good(), "gh_file": {"exists": True}},
                {**self.good(), "gh_file": {"exists": True, "hosts": {"github.com": {"user": 1, "users": [], "keys": [], "user_keys": []}}}},
                {**self.good(), "env_names": "GH_TOKEN"}, {**self.good(), "cred_files": [1]},
                {**self.good(), "git": [{"scope": "global", "key": "x"}]}, {**self.good(), "ws_root": 1}, {**self.good(), "git_repos": "1"},
                {**self.good(), "gh_json": {"hosts": {"github.com": [{"host": "github.com", "login": "bot", "state": "success",
                                                                        "active": True}]}}}]
        for f in bads:
            with self.subTest(f=f):
                with self.assertRaises(mn.Unclear):
                    mn.identity_problems(f, REPO, LOGIN, IDS, "/w")


if __name__ == "__main__":
    unittest.main()
