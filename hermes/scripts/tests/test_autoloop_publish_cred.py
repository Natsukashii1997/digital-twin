"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

hermes/scripts/autoloop_publish.py 的发布凭据（批 0E-4 最小版）：真发布那条路（TEST_BACKEND 为空）整条走一遍。
「GitHub」是本机 127.0.0.1 上的假 HTTP 远端（git-http-backend）：读不用认证（像公开仓库），推必须带对的 Basic 认证；
gh 是装在「带空格的目录」里的假程序（记下每次调用拿到的环境；auth git-credential get 按环境里的 GH_TOKEN 回话，
其余转给 fake_gh.py）；令牌是测试自己编的假串，放在临时目录的文件里。运行账号、可信属主在进程内替换。
不联网、不读任何真实令牌、不碰 ~/.dt、/Library/Application Support/DigitalTwin、真实仓库。

替身说明：假 gh 对任何主机都回凭据，真 gh 只对 GH_HOST（github.com）回；真 GitHub 的认证、规则集、代理都没测到，
第一次真推送仍要按清单在真机上验。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import base64
import contextlib
import http.server
import io
import json
import os
import pwd
import sqlite3
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_publish as P  # noqa: E402
import fake_gh  # noqa: E402
from test_autoloop_publish import PublishBase  # noqa: E402
from test_dt_worktree import git  # noqa: E402

TOKEN = "ghp_" + "Zx9" * 12                      # 假令牌：只在本测试里用
BACKEND = Path(subprocess.run(["git", "--exec-path"], capture_output=True, text=True).stdout.strip()) / "git-http-backend"

FAKE_GH = r'''#!/bin/sh
# 假 gh：记下每次调用的参数和环境；凭据助手按环境里的 GH_TOKEN 回话；其余交给 fake_gh.py。
{{ echo "ARGS $*"; /usr/bin/env; echo "--"; }} >> "{state}/env.log"
if [ "$1" = auth ] && [ "$2" = git-credential ]; then
  cat > /dev/null
  [ "$3" = get ] && printf 'username=x-access-token\npassword=%s\n' "$GH_TOKEN"
  exit 0
fi
FAKE_GH_STATE="{state}" exec "{py}" -B "{fake}" "$@"
'''


class GitHttp(http.server.BaseHTTPRequestHandler):
    """最小的 git HTTP 远端：读不认证，推（git-receive-pack）要 Basic 认证对得上，否则 401。"""

    def log_message(self, *a):
        pass

    def do_GET(self):
        path, _, query = self.path.partition("?")
        srv = self.server
        if "git-receive-pack" in self.path:
            got = self.headers.get("Authorization")
            if got != srv.want:
                srv.denied.append(got)
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="fake-github"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            srv.accepted += 1
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GIT_PROJECT_ROOT": str(srv.root),
               "GIT_HTTP_EXPORT_ALL": "1", "REQUEST_METHOD": self.command, "PATH_INFO": path, "QUERY_STRING": query,
               "CONTENT_TYPE": self.headers.get("Content-Type") or "", "CONTENT_LENGTH": str(len(body)),
               "HTTP_CONTENT_ENCODING": self.headers.get("Content-Encoding") or "", "REMOTE_ADDR": "127.0.0.1",
               "REMOTE_USER": "bot", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
        r = subprocess.run([str(BACKEND)], input=body, env=env, capture_output=True, timeout=60)
        head, _, out = r.stdout.partition(b"\r\n\r\n")
        status, headers = 200, []
        for line in head.split(b"\r\n"):
            k, _, v = line.decode("latin-1").partition(": ")
            if k.lower() == "status":
                status = int(v.split()[0])
            elif k:
                headers.append((k, v))
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    do_POST = do_GET


@unittest.skipUnless(BACKEND.exists(), "这台机器的 git 没带 git-http-backend")
class CredBase(PublishBase):
    def setUp(self):
        super().setUp()
        r = self.root
        self.token_file = r / "gate-home/.dt/publish-github.token"
        self.token_file.parent.mkdir(parents=True, mode=0o700)
        self.put_token(TOKEN)
        self.conf = r / "Application Support/gate/publish.json"   # 默认不存在 = 直连
        self.conf.parent.mkdir(parents=True)
        self.gh_dir = r / "Application Support/bin"            # 带空格：凭据助手的路径要转义对
        self.gh_dir.mkdir(parents=True)
        self.fake = self.gh_dir / "gh"
        self.fake.write_text(FAKE_GH.format(state=self.gh_state, py=sys.executable, fake=HERE / "fake_gh.py"))
        self.fake.chmod(0o755)
        self.set_api(user="dt-bot", perms={"admin": False, "push": True})
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), GitHttp)
        srv.root, srv.denied, srv.accepted = r / "remote", [], 0
        srv.want = "Basic " + base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.srv = srv
        url = f"http://127.0.0.1:{srv.server_address[1]}/{{github}}.git"
        git_bin = P.GIT_BIN if os.access(P.GIT_BIN, os.X_OK) else subprocess.run(
            ["/bin/sh", "-c", "command -v git"], capture_output=True, text=True).stdout.strip()
        for q in (mock.patch.object(P, "TEST_BACKEND", None), mock.patch.object(P, "REAL_PUBLISH", True),
                  mock.patch.object(P, "GH", str(self.fake)), mock.patch.object(P, "GIT_BIN", git_bin),
                  mock.patch.object(P, "TOKEN_FILE", str(self.token_file)),
                  mock.patch.object(P, "PUBLISH_CONF", str(self.conf)),
                  mock.patch.object(P, "TRUST_UIDS", (0, os.geteuid())),
                  mock.patch.object(P, "PUBLISH_CRED", {"o/dt-sandbox": "dt-bot"}),
                  mock.patch.object(P, "remote_url", side_effect=lambda github: url.format(github=github))):
            q.start()
            self.addCleanup(q.stop)

    def put_token(self, text, mode=0o600):
        self.token_file.write_text(text)
        self.token_file.chmod(mode)

    def put_conf(self, doc):
        self.conf.write_text(doc if isinstance(doc, str) else json.dumps(doc))

    def set_api(self, user, perms):
        (self.gh_state / "api.json").write_text(json.dumps({"user": user, "repos/o/dt-sandbox": perms}))

    def gh_envs(self):
        """假 gh 每次调用拿到的 (参数, 环境)。"""
        f = self.gh_state / "env.log"
        out = []
        for block in (f.read_text().split("--\n") if f.exists() else []):
            lines = block.splitlines()
            if lines:
                out.append((lines[0][5:], dict(x.split("=", 1) for x in lines[1:] if "=" in x)))
        return out

    @contextlib.contextmanager
    def recorded(self):
        """记下被测脚本起的每个子进程的命令行和环境（照常执行）。"""
        real, seen = subprocess.run, []

        def run(cmd, *a, **kw):
            if cmd[0] != "/bin/ls":                      # has_acl 的 ls -led 不是 git / gh，不记
                seen.append((list(cmd), dict(kw.get("env") or {})))
            return real(cmd, *a, **kw)
        with mock.patch.object(P.subprocess, "run", side_effect=run):
            yield seen


class RealPath(CredBase):
    def test_token_reaches_only_push_and_gh_through_environment(self):
        tid = self.worked()
        leak = {"GH_ENTERPRISE_TOKEN": "leak-1", "GIT_SSH_COMMAND": "leak-2", "HTTPS_PROXY": "http://127.0.0.1:9",
                "GH_TOKEN": "leak-3", "GITHUB_TOKEN": "leak-4", "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "url.http://127.0.0.1:9/.insteadOf", "GIT_CONFIG_VALUE_0": "http://127.0.0.1"}
        with mock.patch.dict(os.environ, leak), self.recorded() as seen:
            out = P.publish(tid)
        self.assertEqual(self.remote_branch(tid), self.handoff(tid)["head"])
        self.assertEqual((out["pushed"], out["pr_opened"]), (True, True))
        self.assertTrue(self.srv.denied and self.srv.accepted, "推送应当先被要求认证、带对凭据后放行")
        self.assertEqual(set(self.srv.denied), {None}, "没带凭据的那次之外不该有错的凭据")
        pushes = [c for c, _ in seen if "push" in c]
        self.assertEqual(len(pushes), 1)
        for cmd, env in seen:
            self.assertFalse(any(TOKEN in str(x) for x in cmd), f"令牌进了命令行：{cmd[:3]}")
            self.assertFalse(set(env) & set(leak) - {"GH_TOKEN"}, "运行账号的变量漏进了子进程")
            self.assertNotIn(env.get("GH_TOKEN"), ("leak-3",))
            self.assertEqual((env["GIT_CONFIG_GLOBAL"], env["GIT_CONFIG_NOSYSTEM"], env["PATH"]),
                             (os.devnull, "1", P.SAFE_PATH))
            self.assertIn("dt-publish-cred-", env["HOME"])
            is_gh, is_push = cmd[0] == str(self.fake), "push" in cmd
            self.assertEqual(env.get("GH_TOKEN"), TOKEN if (is_gh or is_push) else None,
                             f"令牌只给 git push 和 gh：{cmd[:2]} {[a for a in cmd if not a.startswith('-')][1:3]}")
        helper = [(a, e) for a, e in self.gh_envs() if a.startswith("auth git-credential")]
        self.assertIn("auth git-credential get", [a for a, _ in helper])
        for args, env in self.gh_envs():
            self.assertEqual((env.get("GH_TOKEN"), env.get("GH_HOST")), (TOKEN, "github.com"), args)
            self.assertIn("dt-publish-cred-", env["GH_CONFIG_DIR"])
            self.assertFalse({"GH_ENTERPRISE_TOKEN", "GITHUB_TOKEN", "HTTPS_PROXY", "GIT_SSH_COMMAND"} & set(env))
        self.assertIsNone(P.CRED)
        self.assertFalse(Path(next(e["HOME"] for _, e in seen)).exists(), "临时目录要收掉")
        self.assertNotIn(TOKEN, (self.root / "publish.jsonl").read_text())

    def test_wrong_token_cannot_push_and_nothing_else_supplies_one(self):
        tid = self.worked()
        self.put_token("ghp_" + "Qq7" * 12)
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn("git push 失败", str(c.exception))
        self.assertIsNone(self.remote_branch(tid))
        self.assertEqual(self.srv.accepted, 0)
        self.assertFalse([c for c in self.gh_calls() if c[:2] == ["pr", "create"]])

    def test_second_publish_reuses_branch_and_pr(self):
        tid = self.worked()
        P.publish(tid)
        head = self.handoff(tid)["head"]
        (self.gh_state / "pr_list.json").write_text(json.dumps([{
            "number": 8, "url": "https://github.com/o/dt-sandbox/pull/8", "state": "OPEN", "baseRefName": "main",
            "headRefName": f"claude/{tid}", "headRefOid": head, "isCrossRepository": False,
            "headRepository": {"name": "dt-sandbox"}, "headRepositoryOwner": {"login": "o"}}]))
        accepted = self.srv.accepted
        out = P.publish(tid)
        self.assertEqual((out["pushed"], out["pr_opened"]), (False, False))
        self.assertEqual(self.srv.accepted, accepted, "不用推的时候不该再带令牌去远端")


class TokenFile(CredBase):
    def refused(self, tid, words):
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn(words, str(c.exception))
        self.assertNotIn(TOKEN, str(c.exception))
        self.assertIsNone(self.remote_branch(tid))
        self.assertEqual(self.gh_calls(), [], "令牌不合格时一条 gh 都不该发")
        self.assertIsNone(P.CRED)

    def test_missing_symlink_loose_mode_or_garbage(self):
        tid = self.worked()
        real = self.token_file.with_name("elsewhere")
        self.token_file.rename(real)
        self.refused(tid, "读不到发布令牌")
        self.token_file.symlink_to(real)                           # 链接：内容和权限都对也不认
        self.refused(tid, "读不到发布令牌")
        self.token_file.unlink()
        for mode in (0o640, 0o604, 0o660):
            self.put_token(TOKEN, mode)
            self.refused(tid, "权限 600")
        for text in ("", "two words", TOKEN + "\n" + TOKEN, "短", "x" * 2000, "ghp_有中文" + "a" * 20):
            self.put_token(text)
            self.refused(tid, "不像" if len(text) < 1024 else "太大")
        self.put_token(TOKEN + "\n")                               # 末尾换行可以
        P.publish(tid)
        self.assertIsNotNone(self.remote_branch(tid))

    def test_token_must_belong_to_running_account(self):
        st = os.stat(self.token_file)
        other = os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink, st.st_uid + 1, st.st_gid, st.st_size,
                                st.st_atime, st.st_mtime, st.st_ctime))
        with mock.patch.object(P.os, "fstat", return_value=other):
            with self.assertRaises(P.Rejected) as c:
                P.read_token()
        self.assertIn("本账号", str(c.exception))

    def test_acl_on_token_is_refused(self):
        with mock.patch.object(P, "has_acl", side_effect=lambda p: Path(p) == self.token_file):
            with self.assertRaises(P.Rejected) as c:
                P.read_token()
        self.assertIn("ACL", str(c.exception))

    def test_test_backend_never_reads_token(self):
        """测试后端（本地裸仓库 + 假 gh）不读令牌、不换环境：原有测试的行为不变。"""
        tid = self.worked()
        self.token_file.unlink()
        with mock.patch.object(P, "TEST_BACKEND", self.backend), \
                mock.patch.object(P, "remote_url", side_effect=lambda g: self.backend["remote"].format(github=g)):
            out = P.publish(tid)
        self.assertTrue(out["pushed"])
        self.assertEqual(self.gh_envs(), [])                       # 用的是测试后端的假 gh，不是凭据层的


class GhBinary(CredBase):
    def test_gh_must_be_changeable_by_root_only(self):
        tid = self.worked()
        with mock.patch.object(P, "TRUST_UIDS", (0,)):             # 属主是当前用户、不是 root
            with self.assertRaises(P.Rejected) as c:
                P.publish(tid)
        self.assertIn("不是只有 root 能改", str(c.exception))
        for target, mode in ((self.fake, 0o775), (self.gh_dir, 0o757), (self.gh_dir.parent, 0o775)):
            old = target.stat().st_mode & 0o7777
            target.chmod(mode)
            with self.assertRaises(P.Rejected) as c:
                P.publish(tid)
            self.assertIn("不是只有 root 能改", str(c.exception), target)
            target.chmod(old)
        with mock.patch.object(P, "has_acl", side_effect=lambda p: Path(p) == self.gh_dir):
            with self.assertRaises(P.Rejected):
                P.publish(tid)
        with mock.patch.object(P, "GH", str(self.gh_dir / "nope")):
            with self.assertRaises(P.Rejected) as c:
                P.publish(tid)
        self.assertIn("找不到", str(c.exception))
        self.fake.chmod(0o644)
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn("不是可执行文件", str(c.exception))
        self.assertIsNone(self.remote_branch(tid))
        self.assertEqual(self.gh_calls(), [])

    def test_symlinked_gh_is_judged_by_its_real_path(self):
        tid = self.worked()
        loose = self.root / "loose"
        loose.mkdir(mode=0o777)
        loose.chmod(0o777)
        moved = loose / "gh"
        self.fake.rename(moved)
        self.fake.symlink_to(moved)
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn(str(loose.resolve()), str(c.exception))

    def test_environment_cannot_redirect_gh_or_token(self):
        tid = self.worked()
        evil = self.root / "evil"
        fake_gh.install(evil, self.root / "evil-state")
        with mock.patch.dict(os.environ, {"PATH": f"{evil}:{os.environ['PATH']}", "DT_GH": str(evil / "gh"),
                                          "DT_PUBLISH_GH": str(evil / "gh"), "DT_PUBLISH_TOKEN_FILE": "/dev/null",
                                          "GH_PATH": str(evil / "gh"), "FAKE_GH_STATE": str(self.root / "evil-state")}):
            P.publish(tid)
        self.assertEqual(fake_gh.calls(self.root / "evil-state"), [])
        self.assertTrue(self.gh_calls())


class Conf(CredBase):
    PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")

    def test_proxy_comes_only_from_root_owned_config(self):
        tid = self.worked()
        self.put_conf({"schema": 1, "proxy": "http://127.0.0.1:18086"})
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9"}), \
                self.recorded() as seen:
            P.publish(tid)                       # 远端在 127.0.0.1，NO_PROXY 放行本机：代理不存在也照样通
        self.assertIsNotNone(self.remote_branch(tid))
        for cmd, env in seen:
            self.assertEqual([env.get(k) for k in self.PROXY_KEYS], ["http://127.0.0.1:18086"] * 4, cmd[:2])
            self.assertEqual((env["NO_PROXY"], env["no_proxy"]), ("localhost,127.0.0.1,::1",) * 2)
            self.assertNotIn("ALL_PROXY", env)
        for _, env in self.gh_envs():
            self.assertEqual(env.get("HTTPS_PROXY"), "http://127.0.0.1:18086")

    def test_no_config_means_direct(self):
        with P.credentials() as cred:
            env = cred.env(token=True)
        self.assertFalse([k for k in env if "proxy" in k.lower()])

    def test_bad_config_is_refused_before_anything_is_sent(self):
        tid = self.worked()
        for doc in ("not json", [], {"proxy": "http://127.0.0.1:8086"}, {"schema": 2, "proxy": "http://127.0.0.1:8086"},
                    {"schema": 1, "proxy": "http://127.0.0.1:8086", "enabled": True},
                    {"schema": 1, "proxy": "http://evil.example:8086"}, {"schema": 1, "proxy": "https://127.0.0.1:8086"},
                    {"schema": 1, "proxy": "http://127.0.0.1:8086/x"}, {"schema": 1, "proxy": "http://user@127.0.0.1:8086"},
                    {"schema": 1, "proxy": 8086}, {"schema": 1, "proxy": "http://127.0.0.1:0"}):
            self.put_conf(doc)
            with self.assertRaises(P.Rejected) as c:
                P.publish(tid)
            self.assertIn("发布配置", str(c.exception), doc)
        self.assertIsNone(self.remote_branch(tid))
        self.assertEqual(self.gh_calls(), [])
        self.put_conf({"schema": 1})                                # 只有 schema：直连
        P.publish(tid)

    def test_config_must_be_changeable_by_root_only(self):
        tid = self.worked()
        self.put_conf({"schema": 1, "proxy": "http://127.0.0.1:18086"})
        with mock.patch.object(P, "TRUST_UIDS", (0,)):
            with self.assertRaises(P.Rejected) as c:
                P.load_conf()
        self.assertIn("不是只有 root 能改", str(c.exception))
        self.conf.chmod(0o664)
        with self.assertRaises(P.Rejected):
            P.publish(tid)
        self.conf.chmod(0o644)
        real = self.root / "loose-conf"
        real.mkdir()
        real.chmod(0o777)
        self.conf.rename(real / "publish.json")
        self.conf.symlink_to(real / "publish.json")                 # 链接到别人能改的目录：按真实路径判
        with self.assertRaises(P.Rejected):
            P.publish(tid)
        self.assertIsNone(self.remote_branch(tid))


class Identity(CredBase):
    def refused(self, tid, words):
        with self.assertRaises(P.Rejected) as c:
            P.publish(tid)
        self.assertIn(words, str(c.exception))
        self.assertIsNone(self.remote_branch(tid))
        self.assertEqual(self.srv.accepted, 0)
        self.assertFalse([c for c in self.gh_calls() if c[0] == "pr"], "身份不对时不该碰 PR")

    def test_token_must_be_the_registered_machine_account_and_not_admin(self):
        tid = self.worked()
        self.set_api("owner-himself", {"admin": False, "push": True})
        self.refused(tid, "不是登记的机器账号 dt-bot")
        for perms in ({"admin": True, "push": True}, {"push": True}, {"admin": None, "push": True}):
            self.set_api("dt-bot", perms)
            self.refused(tid, "管理员权限")
        self.set_api("dt-bot", {"admin": False, "push": False})
        self.refused(tid, "没有写权限")
        self.set_api("dt-bot", "not json")
        self.refused(tid, "权限失败")
        (self.gh_state / "api.json").write_text(json.dumps({"user": "dt-bot"}))      # 查仓库 404
        self.refused(tid, "权限失败")
        (self.gh_state / "api.json").write_text("{}")                                # 查身份失败
        self.refused(tid, "查令牌身份失败")

    def test_unregistered_repo_has_no_publish_account(self):
        tid = self.worked()
        with mock.patch.object(P, "PUBLISH_CRED", {"o/other": "dt-bot"}):
            self.refused(tid, "没有登记发布用的机器账号")

    def test_identity_is_checked_before_any_write(self):
        tid = self.worked()
        P.publish(tid)
        calls = [c[:2] for c in self.gh_calls()]
        self.assertEqual(calls[:2], [["api", "user"], ["api", "repos/o/dt-sandbox"]])
        self.assertLess(calls.index(["api", "repos/o/dt-sandbox"]), calls.index(["pr", "list"]))


class Scrub(CredBase):
    def test_token_never_appears_in_errors_or_log(self):
        tid = self.worked()
        (self.gh_state / "fail").write_text(f"HTTP 401: Bad credentials ({TOKEN})")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(P.main(["publish", "--task", tid]), 1)
        log = (self.root / "publish.jsonl").read_text()
        for text in (buf.getvalue(), log):
            self.assertNotIn(TOKEN, text)
            self.assertIn("***", text)

    def test_git_output_is_scrubbed(self):
        with P.credentials() as cred:
            r = P.git("-c", f"alias.x=!echo {TOKEN} >&2; echo {TOKEN}; false", "x", check=False)
            self.assertEqual((r.stdout.strip(), r.stderr.strip()), ("***", "***"))
            with self.assertRaises(P.Rejected) as c:
                P.git("-c", f"alias.x=!echo boom {TOKEN} >&2; false", "x")
            self.assertNotIn(TOKEN, str(c.exception))
            self.assertEqual(cred.scrub(None), "")


class Doctor(CredBase):
    def setUp(self):
        super().setUp()
        hub = Path(os.environ["AGENT_AUTOLOOP_HOME"]) / "hub.db"
        hub.parent.mkdir(parents=True, exist_ok=True)
        if not hub.exists():
            con = sqlite3.connect(hub)
            con.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY)")
            con.commit()
            con.close()
            self.addCleanup(hub.unlink)
        self.new_live_task()                                        # 让凭证库存在
        self.outbox.mkdir(parents=True, exist_ok=True)

    def by_name(self, out):
        return {c["name"]: c for c in out["checks"]}

    def test_all_green_offline_and_online(self):
        out = P.doctor()
        self.assertTrue(out["ok"], [c for c in out["checks"] if not c["ok"]])
        self.assertEqual(self.gh_calls(), [], "不带 --online 不联网、不调 gh")
        self.assertEqual(self.by_name(out)["真发布开关"]["detail"], "开着")
        out = P.doctor(online=True)
        self.assertTrue(out["ok"], [c for c in out["checks"] if not c["ok"]])
        names = self.by_name(out)
        self.assertIn("dt-bot，能写、不是管理员", names["联网：令牌身份"]["detail"])
        self.assertIn("o/dt-sandbox main", names["联网：读得到仓库（不带令牌）"]["detail"])
        self.assertNotIn(TOKEN, json.dumps(out, ensure_ascii=False))
        self.assertEqual(self.srv.accepted, 0)
        self.assertFalse([c for c in self.gh_calls() if c[0] != "api"], "自检只做只读查询")
        self.assertIsNone(P.CRED)

    def test_switch_closed_is_reported_not_failed(self):
        with mock.patch.object(P, "REAL_PUBLISH", False):
            out = P.doctor()
        self.assertTrue(out["ok"])
        self.assertIn("关着", self.by_name(out)["真发布开关"]["detail"])

    def test_each_missing_piece_is_named(self):
        with mock.patch.object(P, "GATE_USER", "dt-gate"):
            self.assertFalse(self.by_name(P.doctor())["运行账号是 dt-gate"]["ok"])
        self.token_file.chmod(0o644)
        self.assertFalse(self.by_name(P.doctor())["发布令牌文件"]["ok"])
        self.token_file.chmod(0o600)
        self.token_file.parent.chmod(0o755)
        c = self.by_name(P.doctor())["发布令牌文件"]
        self.assertFalse(c["ok"])
        self.assertIn("700", c["detail"])
        self.token_file.parent.chmod(0o700)
        with mock.patch.object(P, "TRUST_UIDS", (0,)):
            self.assertFalse(self.by_name(P.doctor())["gh 副本只有 root 能改"]["ok"])
        with mock.patch.dict(os.environ, {"DT_PUBLISH_OUTBOX": str(self.root / "no-outbox"),
                                          "DT_GRANTS_DB": str(self.root / "no.db"),
                                          "AGENT_AUTOLOOP_HOME": str(self.root / "no-hub")}):
            names = self.by_name(P.doctor())
        for n in ("交接目录读得到", "凭证库", "中台库（只读）"):
            self.assertFalse(names[n]["ok"], n)
        self.assertFalse((self.root / "no.db").exists(), "自检不建库")
        with mock.patch.object(P, "BRANCH", P.re.compile(r"^.*$")):
            self.assertFalse(self.by_name(P.doctor())["推送目标只能是 <执行方>/<单号>"]["ok"])
        self.set_api("someone", {"admin": False, "push": True})
        out = P.doctor(online=True)
        self.assertFalse(out["ok"])
        self.assertFalse(self.by_name(out)["联网：令牌身份"]["ok"])

    def test_cli_prints_lines_and_exit_code(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = P.main(["doctor"])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn("[通过] 发布令牌文件", buf.getvalue())
        self.assertIn("自检全过", buf.getvalue())
        self.assertNotIn(TOKEN, buf.getvalue())
        self.token_file.unlink()
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            self.assertEqual(P.main(["doctor"]), 1)
        self.assertIn("[不过] 发布令牌文件", buf.getvalue())


class UnitPieces(unittest.TestCase):
    def test_current_account_is_not_gate_by_default(self):
        self.assertEqual(P.GATE_USER, "dt-gate")
        self.assertNotEqual(pwd.getpwuid(os.geteuid()).pw_name, "dt-gate")

    def test_defaults_are_fixed_paths(self):
        self.assertEqual(P.GH, "/Library/Application Support/DigitalTwin/bin/gh")
        self.assertEqual(P.TOKEN_FILE, "~/.dt/publish-github.token")
        self.assertEqual(P.PUBLISH_CRED, {"Natsukashii1997/dt-sandbox": "shanhao1997"})
        self.assertEqual(P.PUBLISH_CONF, "/Library/Application Support/DigitalTwin/gate/publish.json")
        self.assertEqual(P.TRUST_UIDS, (0,))
        self.assertIs(P.REAL_PUBLISH, False)
        self.assertIsNone(P.CRED)


if __name__ == "__main__":
    unittest.main()
