"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

hermes/scripts/gate_github.py（dt-gate 的 GitHub 发布凭据与真发布开关，ADR-015 批 0E-4 最小版）：令牌文件、gh 副本、
root 写的开关文件、最小环境、账号和权限核对、打码、只读自检。家目录、gh 副本、开关文件、可信属主都在进程内换成临时目录和
当前用户；gh 是写死状态目录的假脚本（最小环境里没有别的变量可传）；凭据助手用真 git 的 `git credential fill` 验，
ACL 用真 chmod +a 验。不联网、不碰真实家目录。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import io
import json
import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import autoloop_grants as G  # noqa: E402
import autoloop_publish as P  # noqa: E402
import gate_github as GG  # noqa: E402

TOKEN = "ghp_" + "A1b2C3d4E5" * 3 + "f6G7h8"          # 形如经典令牌的假值（36 位）
PAT = "github_pat_" + "Ab3_" * 20                      # 形如细粒度令牌的假值
ON = {"enabled": True, "repos": ["dt-sandbox"], "proxy": None, "at": "2026-11-22T20:00:00+08:00", "by": "常浩"}


def fake_gh(path, state, user="shanhao1997", admin=False, push=True, code=0, scopes="public_repo", maintain=False):
    """假 gh：状态目录写死在脚本里；记下参数和看到的环境变量名，令牌只记看到的值（给断言比）。"""
    state.mkdir(parents=True, exist_ok=True)
    (state / "user.json").write_text(json.dumps({"login": user}))
    head = "HTTP/2.0 200 OK\r\nContent-Type: application/json\r\n"
    head += f"X-Oauth-Scopes: {scopes}\r\n" if scopes is not None else ""
    (state / "user.head").write_text(head + "\r\n")
    perm = {"admin": admin, "push": push, "maintain": maintain}
    (state / "repo.json").write_text(json.dumps({"permissions": {k: v for k, v in perm.items() if v != "missing"}}))
    path.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> "{state}/calls"
env | cut -d= -f1 | sort > "{state}/envnames"
printf '%s' "$HTTPS_PROXY" > "{state}/proxy_seen"
test -n "$GH_TOKEN" && printf '%s' "$GH_TOKEN" > "{state}/token_seen"
[ {code} -ne 0 ] && {{ echo "HTTP 401: Bad credentials for $GH_TOKEN" >&2; exit {code}; }}
case "$2 $3" in
  "-i user") cat "{state}/user.head" "{state}/user.json" ;;
  user*) cat "{state}/user.json" ;;
  repos/*) cat "{state}/repo.json" ;;
  *) echo '[]' ;;
esac
""")
    path.chmod(0o755)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        # 真实路径（/var 是链接，gh 副本和开关文件的路径上不许有链接）
        self.root = Path(tempfile.mkdtemp(prefix="dt-gate-github-")).resolve()
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.root)])
        self.home = self.root / "home"
        self.home.mkdir()
        self.gate = self.root / "gate"
        (self.gate / "bin").mkdir(parents=True)
        for d in (self.gate / "bin", self.gate, self.root):
            os.chmod(d, 0o755)
        self.gh = self.gate / "bin/gh"
        self.switch = self.gate / "publish.json"
        self.state = self.root / "ghstate"
        roster = self.root / "projects.json"
        roster.write_text(json.dumps({"projects": [], "inventory": [
            {"path": "github:o/dt-sandbox", "sandbox": True}, {"path": "github:o/other", "sandbox": True}]}))
        for p in (mock.patch.object(GG, "home", return_value=self.home),
                  mock.patch.object(GG, "GH", str(self.gh)),
                  mock.patch.object(GG, "SWITCH", str(self.switch)),
                  mock.patch.object(GG, "TRUSTED_UIDS", (0, os.geteuid())),
                  mock.patch.object(G, "ROSTER", roster)):
            p.start()
            self.addCleanup(p.stop)

    def put_token(self, value=TOKEN, dmode=0o700, fmode=0o600):
        d = self.home / ".dt"
        d.mkdir(exist_ok=True)
        os.chmod(d, dmode)
        f = d / "github-publish.token"
        f.write_text(value + "\n")
        os.chmod(f, fmode)
        return f

    def put_switch(self, data=None, raw=None, mode=0o644):
        self.switch.write_text(raw if raw is not None else json.dumps(ON if data is None else data))
        os.chmod(self.switch, mode)
        return self.switch


class Token(Base):
    def test_reads_both_token_formats(self):
        for value in (TOKEN, PAT):
            with self.subTest(value=value[:12]):
                self.put_token(value)
                self.assertEqual(GG.read_token(), value)

    def test_missing_token_says_how_to_fix(self):
        with self.assertRaises(GG.NotReady) as c:
            GG.read_token()
        self.assertIn("put-publish-token", str(c.exception))

    def test_rejects_loose_or_odd_files(self):
        cases = {"文件 640": dict(fmode=0o640), "目录 755": dict(dmode=0o755), "内容不对": dict(value="hello"),
                 "多一段": dict(value=TOKEN + " " + TOKEN)}
        for name, kw in cases.items():
            with self.subTest(name):
                f = self.put_token(**kw)
                with self.assertRaises(GG.NotReady) as c:
                    GG.read_token()
                self.assertNotIn(TOKEN, str(c.exception))
                f.unlink()

    @unittest.skipUnless(sys.platform == "darwin", "macOS 的 ACL")
    def test_rejects_acl_that_lets_others_read(self):
        f = self.put_token()
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(f)], check=True)
        with self.assertRaises(GG.NotReady) as c:
            GG.read_token()
        self.assertIn("ACL", str(c.exception))
        subprocess.run(["/bin/chmod", "-N", str(f)], check=True)
        self.assertEqual(GG.read_token(), TOKEN)

    def test_rejects_symlinks(self):
        real = self.root / "elsewhere.token"
        real.write_text(TOKEN)
        os.chmod(real, 0o600)
        d = self.home / ".dt"
        d.mkdir(mode=0o700)
        (d / "github-publish.token").symlink_to(real)
        with self.assertRaises(GG.NotReady):
            GG.read_token()
        (d / "github-publish.token").unlink()
        d.rmdir()
        other = self.root / "otherdir"
        other.mkdir(mode=0o700)
        (other / "github-publish.token").write_text(TOKEN)
        os.chmod(other / "github-publish.token", 0o600)
        d.symlink_to(other)                                  # 目录本身是链接
        with self.assertRaises(GG.NotReady):
            GG.read_token()


class RootOnly(Base):
    """gh 副本和开关文件共用的「只有 root 能改」核对。"""
    def setUp(self):
        super().setUp()
        fake_gh(self.gh, self.state)

    def test_accepts_root_owned_readonly_copy(self):
        self.assertEqual(GG.check_gh(), str(self.gh))

    @unittest.skipIf(os.geteuid() == 0, "以 root 跑时临时文件本来就属 root")
    def test_rejects_wrong_owner(self):
        with mock.patch.object(GG, "TRUSTED_UIDS", (0,)), self.assertRaises(GG.NotReady):
            GG.check_gh()

    def test_rejects_writable_copy_or_parent(self):
        for target in (self.gh, self.gate / "bin", self.gate):
            with self.subTest(str(target.relative_to(self.root))):
                mode = os.stat(target).st_mode & 0o7777
                os.chmod(target, mode | 0o020)
                with self.assertRaises(GG.NotReady):
                    GG.check_gh()
                os.chmod(target, mode)

    @unittest.skipUnless(sys.platform == "darwin", "macOS 的 ACL")
    def test_rejects_acl_on_a_parent(self):
        subprocess.run(["/bin/chmod", "+a", "everyone allow add_file,delete_child", str(self.gate)], check=True)
        self.addCleanup(subprocess.run, ["/bin/chmod", "-N", str(self.gate)])
        with self.assertRaises(GG.NotReady):
            GG.check_gh()

    def test_sticky_writable_parent_is_ok(self):
        os.chmod(self.gate, 0o1777)
        self.assertEqual(GG.check_gh(), str(self.gh))

    def test_rejects_symlink_missing_or_not_executable(self):
        self.gh.rename(self.gate / "bin/gh.real")
        with self.assertRaises(GG.NotReady):
            GG.check_gh()
        self.gh.symlink_to(self.gate / "bin/gh.real")
        with self.assertRaises(GG.NotReady):
            GG.check_gh()
        self.gh.unlink()
        (self.gate / "bin/gh.real").rename(self.gh)
        os.chmod(self.gh, 0o644)
        with self.assertRaises(GG.NotReady):
            GG.check_gh()


class Switch(Base):
    def test_missing_file_is_off(self):
        with self.assertRaises(GG.NotReady) as c:
            GG.read_switch()
        self.assertIn("关着", str(c.exception))
        self.assertIsNone(GG.switch_proxy())

    def test_on_switch(self):
        self.put_switch({**ON, "proxy": "http://127.0.0.1:8086"})
        sw = GG.read_switch()
        self.assertEqual((sw["enabled"], sw["repos"], sw["proxy"], sw["login"]),
                         (True, ["dt-sandbox"], "http://127.0.0.1:8086", "shanhao1997"))
        self.assertEqual(GG.switch_proxy(), "http://127.0.0.1:8086")

    def test_anything_odd_counts_as_off(self):
        cases = {"enabled 是字符串": {**ON, "enabled": "true"}, "enabled 没写": {k: v for k, v in ON.items() if k != "enabled"},
                 "名单外的仓库不认": {**ON, "repos": ["digital-twin"]}, "repos 不是列表": {**ON, "repos": "dt-sandbox"},
                 "多了字段": {**ON, "remote": "x"}, "代理不是本机": {**ON, "proxy": "http://10.0.0.1:8086"},
                 "代理端口不对": {**ON, "proxy": "http://127.0.0.1:99999"}, "代理是 https": {**ON, "proxy": "https://127.0.0.1:1"},
                 "账号名不对": {**ON, "login": "a b"}, "不是对象": ["dt-sandbox"]}
        for name, data in cases.items():
            with self.subTest(name):
                self.put_switch(data)
                with self.assertRaises(GG.NotReady):
                    GG.read_switch()
        self.put_switch(raw="{not json")
        with self.assertRaises(GG.NotReady):
            GG.read_switch()

    def test_mixed_repos_keep_only_the_allowed_ones(self):
        self.put_switch({**ON, "repos": ["digital-twin", "dt-sandbox"]})
        self.assertEqual(GG.read_switch()["repos"], ["dt-sandbox"])

    @unittest.skipIf(os.geteuid() == 0, "以 root 跑时临时文件本来就属 root")
    def test_switch_file_must_be_root_only(self):
        self.put_switch(mode=0o664)
        with self.assertRaises(GG.NotReady):
            GG.read_switch()
        self.put_switch()
        with mock.patch.object(GG, "TRUSTED_UIDS", (0,)), self.assertRaises(GG.NotReady):
            GG.read_switch()
        self.switch.rename(self.gate / "real.json")
        self.switch.symlink_to(self.gate / "real.json")
        with self.assertRaises(GG.NotReady):
            GG.read_switch()


class MinimalEnv(Base):
    def test_env_does_not_inherit_caller_variables(self):
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9", "GIT_DIR": "/x", "GH_HOST": "evil",
                                          "DT_GH": "/x", "GH_TOKEN": "ghp_" + "x" * 36}):
            plain, authed = GG.env(), GG.env(TOKEN)
        for e in (plain, authed):
            self.assertFalse([k for k in e if "proxy" in k.lower() or k in ("GIT_DIR", "DT_GH")])
            self.assertEqual(e["PATH"], GG.SAFE_PATH)
            self.assertEqual(os.listdir(e["HOME"]), [])          # 空的家目录
            self.assertTrue(e["HOME"].startswith(str(self.home)))
            self.assertEqual(os.stat(e["GH_CONFIG_DIR"]).st_mode & 0o777, 0o700)
        self.assertNotIn("GH_TOKEN", plain)
        self.assertFalse([k for k in plain if k.startswith("GIT_CONFIG_KEY")])
        self.assertEqual((authed["GH_TOKEN"], authed["GH_HOST"]), (TOKEN, "github.com"))

    def test_proxy_only_local(self):
        e = GG.env(None, "http://127.0.0.1:8086")
        self.assertEqual((e["HTTPS_PROXY"], e["https_proxy"], e["HTTP_PROXY"]), ("http://127.0.0.1:8086",) * 3)
        self.assertIn("127.0.0.1", e["NO_PROXY"])
        with self.assertRaises(GG.NotReady):
            GG.env(None, "http://evil.example:80")

    def git_fill(self, e, host="github.com"):
        return subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost={host}\n\n", env=e,
                              capture_output=True, text=True, timeout=30)

    def test_credential_helper_gives_the_token_only_for_github(self):
        e = GG.env(TOKEN)
        # 空家目录里放一个会答别的密码的全局配置：最小环境不读它
        Path(e["HOME"], ".gitconfig").write_text('[credential]\n\thelper = "!f() { echo password=evil; }; f"\n')
        self.addCleanup(Path(e["HOME"], ".gitconfig").unlink)
        r = self.git_fill(e)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"password={TOKEN}\n", r.stdout)
        self.assertIn("username=x-access-token\n", r.stdout)
        self.assertNotIn("evil", r.stdout)
        r = self.git_fill(e, host="example.com")             # 别的主机拿不到令牌（也不弹提示）
        self.assertNotIn(TOKEN, r.stdout)
        r = self.git_fill(GG.env())                          # 不带令牌的环境没有凭据
        self.assertNotIn(TOKEN, r.stdout)

    def test_mask(self):
        self.assertEqual(GG.mask(f"fatal: x {TOKEN}@github.com {PAT} gho_" + "a" * 36), "fatal: x ***@github.com *** ***")
        self.assertEqual(GG.mask(None), "")


class Ready(Base):
    def setUp(self):
        super().setUp()
        self.put_token()
        self.put_switch()

    def test_ready_returns_both_envs_and_never_puts_token_on_command_line(self):
        fake_gh(self.gh, self.state)
        plain, authed = GG.ready("dt-sandbox", "o/dt-sandbox")
        self.assertNotIn("GH_TOKEN", plain)
        self.assertEqual(authed["GH_TOKEN"], TOKEN)
        calls = (self.state / "calls").read_text()
        self.assertEqual(calls.splitlines(), ["api -i user", "api repos/o/dt-sandbox"])
        self.assertNotIn(TOKEN, calls)
        self.assertEqual((self.state / "token_seen").read_text(), TOKEN)
        self.assertFalse([n for n in (self.state / "envnames").read_text().split() if "proxy" in n.lower()])

    def test_proxy_from_switch_reaches_gh(self):
        self.put_switch({**ON, "proxy": "http://127.0.0.1:8086"})
        fake_gh(self.gh, self.state)
        plain, authed = GG.ready("dt-sandbox", "o/dt-sandbox")
        self.assertEqual((self.state / "proxy_seen").read_text(), "http://127.0.0.1:8086")
        self.assertEqual(plain["HTTPS_PROXY"], "http://127.0.0.1:8086")

    def test_switch_is_checked_first(self):
        fake_gh(self.gh, self.state)
        for name, data in {"关着": {**ON, "enabled": False}, "没放开": {**ON, "repos": []}}.items():
            with self.subTest(name), self.assertRaises(GG.NotReady):
                self.put_switch(data)
                GG.ready("dt-sandbox", "o/dt-sandbox")
        self.switch.unlink()
        with self.assertRaises(GG.NotReady):
            GG.ready("dt-sandbox", "o/dt-sandbox")
        self.assertFalse((self.state / "calls").exists())

    def test_wrong_account_or_permissions_refused(self):
        for name, kw in {"别的账号": dict(user="Natsukashii1997"), "管理员": dict(admin=True),
                         "不能推": dict(push=False), "权限缺项": dict(admin="missing"), "维护者": dict(maintain=True),
                         "多勾了权限": dict(scopes="public_repo, repo, delete_repo"),
                         "经典令牌读不出权限范围": dict(scopes=None), "gh 报错": dict(code=1)}.items():
            with self.subTest(name):
                fake_gh(self.gh, self.state, **kw)
                with self.assertRaises(GG.NotReady) as c:
                    GG.ready("dt-sandbox", "o/dt-sandbox")
                self.assertNotIn(TOKEN, str(c.exception))      # 假 gh 报错时把令牌打进了 stderr：打码

    def test_fine_grained_token_has_no_scope_header(self):
        self.put_token(PAT)
        fake_gh(self.gh, self.state, scopes=None)
        GG.ready("dt-sandbox", "o/dt-sandbox")

    def test_messages_say_what_is_wrong(self):
        for kw, words in ((dict(admin="missing"), "读不出"), (dict(scopes="repo"), "只许 public_repo"),
                          (dict(admin=True), "管理员")):
            with self.subTest(words):
                fake_gh(self.gh, self.state, **kw)
                with self.assertRaises(GG.NotReady) as c:
                    GG.ready("dt-sandbox", "o/dt-sandbox")
                self.assertIn(words, str(c.exception))

    def test_login_from_switch(self):
        self.put_switch({**ON, "login": "other-bot"})
        fake_gh(self.gh, self.state, user="other-bot")
        GG.ready("dt-sandbox", "o/dt-sandbox")
        fake_gh(self.gh, self.state)
        with self.assertRaises(GG.NotReady):
            GG.ready("dt-sandbox", "o/dt-sandbox")

    def test_repo_outside_list_or_roster_refused_without_calling_gh(self):
        fake_gh(self.gh, self.state)
        self.put_switch({**ON, "repos": ["dt-sandbox", "other"]})
        for repo, github in (("other", "o/other"), ("dt-sandbox", "o/elsewhere")):
            with self.subTest(repo=repo, github=github), self.assertRaises(GG.NotReady):
                GG.ready(repo, github)
        self.assertFalse((self.state / "calls").exists())

    def test_readback_env_needs_no_switch_and_no_identity(self):
        fake_gh(self.gh, self.state, user="someone")
        self.put_switch({**ON, "enabled": False, "proxy": "http://127.0.0.1:8086"})
        authed, plain = GG.readback_env(True), GG.readback_env(False)
        self.assertEqual((authed["GH_TOKEN"], authed["HTTPS_PROXY"]), (TOKEN, "http://127.0.0.1:8086"))
        self.assertNotIn("GH_TOKEN", plain)
        self.assertFalse((self.state / "calls").exists())
        os.chmod(self.gh, 0o775)                               # gh 副本不可信：带令牌的不给，不带令牌的照给
        with self.assertRaises(GG.NotReady):
            GG.readback_env(True)
        GG.readback_env(False)

    def test_check_cli_prints_login_and_permissions_only(self):
        fake_gh(self.gh, self.state)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = GG.main(["check", "--repo", "dt-sandbox"])
        self.assertEqual(code, 0)
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertEqual(json.loads(out.getvalue()), {"repo": "dt-sandbox", "github": "o/dt-sandbox", "ok": True,
                                                      "login": "shanhao1997", "admin": False, "push": True,
                                                      "scopes": ["public_repo"]})
        self.switch.unlink()                                   # check 不要求开关
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(GG.main(["check", "--repo", "dt-sandbox"]), 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(GG.main(["check", "--repo", "nope"]), 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])


class Doctor(Base):
    def setUp(self):
        super().setUp()
        self.put_token()
        fake_gh(self.gh, self.state)
        outbox = self.root / "outbox"
        outbox.mkdir()
        import sqlite3
        for db, table in ((self.root / "grants.db", "grants"), (self.root / "hub.db", "runs")):
            sqlite3.connect(db).execute(f"CREATE TABLE {table} (id INTEGER)").connection.commit()
        self.git = self.root / "fakegit"
        self.git.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$HTTPS_PROXY\" >> '{self.root}/git_proxies'\n"
                            f"echo '{'a' * 40}\trefs/heads/main'\n")
        self.git.chmod(0o755)
        for p in (mock.patch.object(P, "GATE_USER", pwd.getpwuid(os.geteuid()).pw_name),
                  mock.patch.object(P, "outbox", return_value=outbox),
                  mock.patch.object(P, "hub_db", return_value=self.root / "hub.db"),
                  mock.patch.object(G, "db_path", return_value=self.root / "grants.db"),
                  mock.patch.object(GG, "GIT_BIN", str(self.git))):
            p.start()
            self.addCleanup(p.stop)

    def run_doctor(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = GG.main(["doctor", "--repo", "dt-sandbox", *args])
        self.assertNotIn(TOKEN, out.getvalue())
        return code, out.getvalue()

    def test_proxy_regex_is_whole_string(self):
        for bad in ("http://127.0.0.1:8086\n", "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:8086/x"):
            with self.subTest(bad=bad), self.assertRaises(GG.NotReady):
                GG.valid_proxy(bad)
        self.assertEqual(GG.valid_proxy("http://127.0.0.1:65535"), "http://127.0.0.1:65535")

    def test_offline_all_pass_with_switch_off(self):
        code, out = self.run_doctor()
        self.assertEqual(code, 0, out)
        self.assertIn("[通过] 真发布开关：关着（没有开关文件）", out)
        self.assertIn("[通过] 推送目标只能是 <执行方>/<单号>", out)
        self.assertFalse((self.state / "calls").exists())      # 不联网
        self.assertNotIn("联网", out)

    def test_online_checks_identity_and_both_routes(self):
        code, out = self.run_doctor("--online", "--proxy", "http://127.0.0.1:8086")
        self.assertEqual(code, 0, out)
        self.assertIn("[通过] 联网（直连）：令牌身份和权限", out)
        self.assertIn("[通过] 联网（经代理 http://127.0.0.1:8086）：不带令牌读得到 main", out)
        self.assertEqual((self.root / "git_proxies").read_text().splitlines(), ["", "http://127.0.0.1:8086"])

    def test_online_failure_on_the_used_route_fails(self):
        fake_gh(self.gh, self.state, admin=True)
        code, out = self.run_doctor("--online")
        self.assertEqual(code, 1)
        self.assertIn("[不过] 联网（直连）：令牌身份和权限", out)

    def test_broken_pieces_reported(self):
        self.put_switch(raw="{")
        os.chmod(self.gh, 0o775)
        code, out = self.run_doctor()
        self.assertEqual(code, 1)
        self.assertIn("[不过] 真发布开关", out)
        self.assertIn("[不过] gh 副本只有 root 能改", out)


if __name__ == "__main__":
    unittest.main()
