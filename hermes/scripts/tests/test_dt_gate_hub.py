"""中台和网关搬进 dt-gate（ADR-015 批 0E-2）：ops/dt_gate_hub.py、ops/dt_gate_user.py 的自测。

真机要 root、要真账号，这里全换成替身：账号是临时目录里的几个「家目录」（进程都是当前用户），launchctl 是按 plist 起停子进程的
假的，root 路径核对（dt_root_guard.sh）按测试给的结果。端到端用例在临时目录里起真的 agent-autoloop 中台（随机端口），
走 stage → preflight → migrate（含中途出错自动退回）→ windows → rotate-worker → status → rollback；
本机没有 agent-autoloop 命令行或引擎不是 git 检出（如 CI）时跳过，DT_HUB_E2E=0 也跳过。
不碰 ~/.agent-autoloop、正式中台 127.0.0.1:3350、/Library、/Users/Shared。真跨账号、真 launchd 由常浩按命令清单在真机验。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import hashlib
import io
import json
import os
import plistlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "ops"))
sys.path.insert(0, str(HERE.parent))
import dt_gate_hub as H  # noqa: E402
import dt_gate_user as U  # noqa: E402

CLI = os.environ.get("DT_AUTOLOOP_CLI", "agent-autoloop")


def engine_checkout():
    """本机引擎的 git 检出（agent-autoloop 命令行是 app/bin/agent-autoloop.ts 的链接）；找不到返回 None。"""
    found = shutil.which(CLI)
    if not found:
        return None
    root = Path(os.path.realpath(found)).parent.parent
    return root if (root / ".git").exists() and (root / "bin/agent-autoloop.ts").exists() else None


ENGINE = engine_checkout()
NODE = shutil.which("node")
SKIP_E2E = os.environ.get("DT_HUB_E2E") == "0" or not ENGINE or not NODE


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeHost(H.Host):
    """账号、launchctl、root 路径核对的替身；子进程都以当前用户跑，环境按真的给（env -i、只有系统 PATH）。"""

    def __init__(self, users):
        self.users = users                     # 名字 → 家目录
        self.procs = {}                        # 标签 → (Popen, plist)
        self.calls = []
        self.guard_fail = set()                # 要判不过的 (函数, 参数)
        self.chowns = []

    def is_root(self):
        return True

    def account(self, name):
        if name not in self.users:
            raise KeyError(name)
        return {"name": name, "uid": os.getuid(), "gid": os.getgid(), "home": str(self.users[name])}

    def _argv(self, name, argv, env):
        a = self.account(name)
        base = {"HOME": a["home"], "PATH": H.SAFE_PATH, "USER": name, "LOGNAME": name, "LC_ALL": "en_US.UTF-8"}
        base.update(env or {})
        return ["/usr/bin/env", "-i", *[f"{k}={v}" for k, v in base.items()], *argv]

    def guard(self, fn, *args):
        self.calls.append(("guard", fn, *args))
        if (fn, *args) in self.guard_fail:
            return 0 if fn == "is_member" else 1
        return 1 if fn == "is_member" else 0

    def chown(self, path, uid, gid):
        self.chowns.append(str(path))

    def node_signed(self, path, team):
        return H.Host.node_signed(self, path, team)

    def sleep(self, s):
        time.sleep(min(s, 0.2))

    def launchctl(self, *args):
        self.calls.append(("launchctl", *args))
        verb = args[0]
        if verb == "bootstrap":
            pl = plistlib.loads(Path(args[2]).read_bytes())
            label = pl["Label"]
            if label in self.procs and self.procs[label][0].poll() is None:
                return 5, "already loaded"
            out = open(pl.get("StandardOutPath", os.devnull), "ab")
            p = subprocess.Popen(pl["ProgramArguments"], env=pl.get("EnvironmentVariables", {}),
                                 cwd=pl.get("WorkingDirectory", "/"), stdout=out, stderr=subprocess.STDOUT)
            out.close()
            self.procs[label] = (p, pl)
            return 0, ""
        label = args[1].rsplit("/", 1)[-1] if len(args) > 1 else ""
        if verb == "bootout":
            p = self.procs.pop(label, (None,))[0]
            if p is None:
                return 3, "not loaded"
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            return 0, ""
        if verb == "print":
            p = self.procs.get(label, (None,))[0]
            return (0, f"state = running\n\tpid = {p.pid}") if p and p.poll() is None else (113, "not found")
        return 0, ""                            # enable / disable

    def stop_all(self):
        for label in list(self.procs):
            self.launchctl("bootout", f"system/{label}")


# ---------- 账号侧小工具 ----------
class UserHelperTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-gate-user-")
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)

    def test_put_is_atomic_600_and_refuses_link_dir(self):
        U.put(self.d / "a.key", b"secret")
        self.assertEqual((self.d / "a.key").read_bytes(), b"secret")
        self.assertEqual((self.d / "a.key").stat().st_mode & 0o777, 0o600)
        (self.d / "real").mkdir()
        (self.d / "link").symlink_to(self.d / "real")
        with self.assertRaises(U.Refused):
            U.put(self.d / "link/x", b"1")
        self.assertFalse((self.d / "real/x").exists())

    def test_put_replaces_a_symlink_instead_of_following_it(self):
        target = self.d / "victim"
        target.write_text("keep")
        (self.d / "f").symlink_to(target)
        U.put(self.d / "f", b"new")
        self.assertEqual(target.read_text(), "keep")
        self.assertFalse((self.d / "f").is_symlink())

    def test_prepare_keeps_only_url_and_hub_and_replaces_master_key(self):
        s = self.d / "stage"
        s.mkdir(mode=0o700)
        (s / "config.json").write_text(json.dumps({"url": "http://127.0.0.1:1", "hub": {"listen": "127.0.0.1:1"},
                                                   "owner": "o" * 43, "worker": {"name": "w", "key": "k"},
                                                   "source": {"name": "local", "key": "s"}, "channels": {"desktop": {}}}))
        (s / "master.key").write_text("m" * 43)
        out = U.prepare(s)
        self.assertEqual(out["dropped"], ["channels", "owner", "source", "worker"])
        self.assertEqual(set(json.loads((s / "config.json").read_text())), {"url", "hub"})
        new = (s / "master.key").read_text()
        self.assertNotEqual(new, "m" * 43)
        self.assertRegex(new, U.KEY_SHAPE)

    def test_prepare_refuses_non_hub_config(self):
        s = self.d / "stage"
        s.mkdir(mode=0o700)
        (s / "config.json").write_text(json.dumps({"url": "x"}))
        with self.assertRaises(U.Refused):
            U.prepare(s)

    def test_token_made_once_600_and_refuses_link(self):
        f = self.d / "sub/t.token"
        self.assertTrue(U.token(f)["made"])
        first = f.read_text()
        self.assertFalse(U.token(f)["made"])
        self.assertEqual(f.read_text(), first)
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        (self.d / "l.token").symlink_to(f)
        with self.assertRaises(U.Refused):
            U.token(self.d / "l.token")

    def test_set_worker_key(self):
        c = self.d / "config.json"
        c.write_text(json.dumps({"url": "u", "worker": {"name": "dt-worker", "key": "old"}}))
        with self.assertRaises(U.Refused):
            U.set_worker_key(c, "bad key")
        U.set_worker_key(c, "k" * 43 + "\n")
        self.assertEqual(json.loads(c.read_text())["worker"]["key"], "k" * 43)

    def test_main_prints_error_json_not_traceback(self):
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            code = U.main(["prepare", str(self.d / "nope")])
        self.assertEqual(code, 1)
        self.assertIn("error", json.loads(out.getvalue()))


# ---------- 取代码 ----------
def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


class ExtractTest(unittest.TestCase):
    """按 ls-tree 的对象号取原始内容（真 git，临时仓库）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-gate-arc-")
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        self.repo = self.d / "src"
        (self.repo / "hermes/scripts/tests").mkdir(parents=True)
        (self.repo / "hermes/scripts/a.py").write_text("print(1)\n")
        (self.repo / "hermes/scripts/run.sh").write_text("#!/bin/sh\n")
        os.chmod(self.repo / "hermes/scripts/run.sh", 0o755)
        (self.repo / "hermes/scripts/tests/t.py").write_text("x")
        (self.repo / "projects.json").write_text("{}")
        (self.repo / "other.md").write_text("$Format:%H$")
        (self.repo / ".gitattributes").write_text("*.py text eol=crlf\nother.md export-subst\nprojects.json export-ignore\n")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "x")
        self.sha = git(self.repo, "rev-parse", "HEAD").decode().strip()
        self.host = FakeHost({"nat": self.d})

    def extract(self, keep, dest="out"):
        return H.extract_archive(self.host, "nat", self.repo, self.sha, self.d / dest, keep)

    def test_raw_content_modes_and_filter(self):
        dest = self.extract(H.repo_keep)
        self.assertEqual((dest / "hermes/scripts/a.py").read_bytes(), b"print(1)\n")   # 提交里的原样，不按属性文件转成 CRLF
        self.assertTrue((dest / "projects.json").is_file())                              # export-ignore 不起作用
        self.assertEqual((dest / "hermes/scripts/run.sh").stat().st_mode & 0o777, 0o755)
        self.assertFalse((dest / "hermes/scripts/tests").exists())
        self.assertFalse((dest / "other.md").exists())
        self.assertEqual(self.extract(H.repo_keep), dest)                                # 已有就不重取

    def test_dirs_are_755_even_under_strict_umask(self):
        old = os.umask(0o077)
        try:
            dest = self.extract(lambda n: True)
        finally:
            os.umask(old)
        for d in (dest, dest / "hermes", dest / "hermes/scripts"):
            self.assertEqual(d.stat().st_mode & 0o777, 0o755, d)

    def test_symlink_in_commit_refused_and_nothing_left(self):
        (self.repo / "link").symlink_to("/etc/passwd")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "link")
        self.sha = git(self.repo, "rev-parse", "HEAD").decode().strip()
        with self.assertRaisesRegex(H.Stop, "链接"):
            self.extract(lambda n: True, "out2")
        self.assertFalse((self.d / "out2").exists())
        self.assertEqual([p.name for p in self.d.iterdir() if p.name.startswith(".out")], [])

    def test_tampered_object_content_refused(self):
        obj = H.blob_sha(b"real")
        good = f"{obj} blob 4\n".encode() + b"real\n"
        self.assertEqual(H.parse_batch(good, [obj]), {obj: b"real"})
        for bad in (f"{obj} blob 4\n".encode() + b"fake\n", f"{obj} blob 4\n".encode() + b"real\nextra",
                    f"{obj} tree 4\n".encode() + b"real\n", f"{obj} missing\n".encode()):
            with self.subTest(bad=bad), self.assertRaises(H.Stop):
                H.parse_batch(bad, [obj])

    def test_unsafe_paths_refused(self):
        for name in ("../x", "/abs", "a/../../x", "a/./b"):
            with self.subTest(name=name), self.assertRaises(H.Stop):
                H._safe_name(name)


# ---------- 核对（不起中台） ----------
class ChecksTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-gate-chk-")
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        users = {n: self.d / "users" / n for n in ("nat", "dt-gate", "dt-worker")}
        for h in users.values():
            h.mkdir(parents=True)
        self.host = FakeHost(users)
        root = self.d / "gate"
        root.mkdir()
        (root / "current.json").write_text(json.dumps({"node": str(self.d), "cli": str(self.d), "engine": {"commit": "e" * 40},
                                                       "repo": {"commit": "r" * 40, "dir": str(self.d)}}))
        for k, v in {"DT_GATE_ROOT": root, "DT_GATE_SHARED": self.d / "shared/dt-gate", "DT_GATE_USERS": self.d / "users",
                     "DT_GATE_ANY_COPY": "1"}.items():
            p = mock.patch.dict(os.environ, {k: str(v)})
            p.start()
            self.addCleanup(p.stop)
        (users["nat"] / "Library/LaunchAgents").mkdir(parents=True)
        (users["nat"] / "Library/LaunchAgents/agent-autoloop.plist").write_text("x")
        self.users = users

    def a(self):
        return mock.Mock(owner_account="nat", confirm=True)

    def snap(self, **kw):
        base = {"live_runs": 0, "live_cards": 0, "dispatch_rules": ["dispatch-x"], "pauses": ["dispatch-x"],
                "url": "http://127.0.0.1:1", "listen": "127.0.0.1:1", "keys": ["owner:owner"], "secrets_env": []}
        base.update(kw)
        return base

    def run_checks(self, snap):
        with mock.patch.object(H, "snapshot_as", return_value=snap):
            return {msg: ok for ok, msg in H.checks(self.host, self.a())}

    def test_secrets_env_with_keys_blocks(self):
        r = self.run_checks(self.snap(secrets_env=["GITHUB_WEBHOOK_SECRET"]))
        self.assertEqual([m for m, ok in r.items() if not ok], [m for m in r if "secrets.env" in m])

    def test_finished_but_unarchived_progress_counts_as_done(self):
        """state.json 写完、进度没归档就被杀：认作已搬完，不引导回滚（复核 P2-b）。"""
        root = Path(os.environ["DT_GATE_ROOT"])
        (root / "state.json").write_text("{}")
        (root / "migrating.json").write_text(json.dumps({"stamp": "S"}))
        H.settle_progress()
        self.assertFalse((root / "migrating.json").exists())
        self.assertTrue((root / "migrating.done-S.json").exists())

    def test_hub_must_be_on_127_0_0_1(self):
        r = self.run_checks(self.snap(url="http://localhost:3350", listen="localhost:3350"))
        self.assertEqual(len([m for m, ok in r.items() if not ok]), 1)

    def test_half_done_migration_blocks(self):
        (Path(os.environ["DT_GATE_ROOT"]) / "migrating.json").write_text("{}")
        r = self.run_checks(self.snap())
        self.assertFalse(r["没有搬到一半的记录（有的话先跑 rollback --confirm 收拾）"])

    def test_only_stage_and_status_from_a_copy_the_owner_can_edit(self):
        """仓库里那份主账号能改：stage 以外要用 root 装的副本（独立审查 P1-1）。"""
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"DT_GATE_ANY_COPY": ""}), mock.patch("sys.stdout", out):
            self.assertEqual(H.main(["preflight"], host=self.host), 1)
            self.assertEqual(H.main(["migrate", "--confirm"], host=self.host), 1)
        self.assertIn("bin/dt-gate-hub", out.getvalue())
        self.assertFalse(any(c[0] == "launchctl" for c in self.host.calls))

    def test_run_cli_only_safe_subcommands(self):
        root = Path(os.environ["DT_GATE_ROOT"])
        (root / "state.json").write_text(json.dumps({"hub_url": "http://127.0.0.1:1"}))
        for args in (["key", "new", "source", "x"], ["backup", "/tmp/x"], ["restore", "x", "--yes"], ["init"], []):
            with self.subTest(args=args), self.assertRaisesRegex(H.Stop, "只放行"):
                H.cmd_run(self.host, mock.Mock(tool="agent-autoloop", args=args))

    def test_update_falls_back_to_previous_copy(self):
        root = Path(os.environ["DT_GATE_ROOT"])
        (root / "state.json").write_text(json.dumps({"hub_url": "http://127.0.0.1:1"}))
        new = json.loads((root / "current.json").read_text())
        old = {**new, "engine": {"commit": "o" * 40}, "repo": {"commit": "p" * 40, "dir": str(self.d)}}
        (root / "previous.json").write_text(json.dumps(old))
        seen = []

        def install(host, current, s, which):
            seen.append(current["engine"]["commit"][0])
            if current["engine"]["commit"].startswith("e"):
                raise H.Stop("新的起不来")
        backups = []
        with mock.patch.object(H, "installed", return_value=True), mock.patch.object(H, "_install_all", install), \
                mock.patch.object(H, "run_cli", lambda *a, **k: backups.append(a[4])), mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(H.Stop, "已退回上一份"):
                H.cmd_update(self.host, self.a())
        self.assertEqual(seen, ["e", "o"])
        self.assertEqual(backups[0][0], "backup")                                 # 换之前先备份中台库（复核 P2-d）
        self.assertEqual(json.loads((root / "current.json").read_text())["engine"]["commit"], "o" * 40)

    def test_all_good(self):
        r = self.run_checks(self.snap())
        self.assertTrue(all(r.values()), [m for m, ok in r.items() if not ok])

    def test_live_run_card_and_unpaused_dispatch_block(self):
        r = self.run_checks(self.snap(live_runs=1, live_cards=2, pauses=[]))
        bad = [m for m, ok in r.items() if not ok]
        self.assertEqual(len(bad), 3, bad)

    def test_admin_or_bad_uid_or_wrong_home_block(self):
        self.host.guard_fail |= {("is_member", "dt-gate", "admin"), ("uid_ok", "dt-gate")}
        self.host.users["dt-gate"] = self.d / "elsewhere"
        r = self.run_checks(self.snap())
        self.assertEqual(len([m for m, ok in r.items() if not ok]), 3)

    def test_root_paths_block(self):
        self.host.guard_fail.add(("sticky_dir", str(self.d / "shared")))
        r = self.run_checks(self.snap())
        self.assertIn(False, r.values())

    def test_already_migrated_blocks(self):
        (Path(os.environ["DT_GATE_ROOT"]) / "state.json").write_text("{}")
        r = self.run_checks(self.snap())
        self.assertFalse(r["还没搬过（gate/state.json 不存在）"])

    def test_migrate_needs_confirm_and_never_touches_anything_when_checks_fail(self):
        a = self.a()
        a.confirm = False
        with self.assertRaisesRegex(H.Stop, "--confirm"):
            H.cmd_migrate(self.host, a)
        with mock.patch.object(H, "snapshot_as", return_value=self.snap(live_runs=1)), mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(H.Stop, "没有动任何东西"):
                H.cmd_migrate(self.host, self.a())
        self.assertFalse(any(c[0] == "launchctl" for c in self.host.calls))

    def test_shared_dir_taken_by_someone_else_stops(self):
        (self.d / "shared").mkdir()
        self.host.guard_fail.add(("root_dir", str(self.d / "shared/dt-gate")))
        with self.assertRaisesRegex(H.Stop, "抢先"):
            H.shared_dir(self.host)

    def test_run_only_whitelisted_tools(self):
        a = mock.Mock(tool="bash", args=[])
        with self.assertRaisesRegex(H.Stop, "只能跑"):
            H.cmd_run(self.host, a)

    def test_needs_root(self):
        self.host.is_root = lambda: False
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(H.main(["preflight"], host=self.host), 1)

    def test_waits_refuse_a_listener_of_another_account(self):
        """端口被别的账号抢绑：健康检查、窗口检查都不过（独立审查 P1-2、P2-6）。"""
        with mock.patch.object(self.host, "listeners", return_value=[os.getuid() + 1]):
            with self.assertRaisesRegex(H.Stop, "不是 uid"):
                H.wait_health(self.host, "http://127.0.0.1:1", "e" * 40, os.getuid(), seconds=0.5)
            with self.assertRaisesRegex(H.Stop, "不是 uid"):
                H.wait_answering(self.host, 1, os.getuid(), seconds=0.5)
        with mock.patch.object(self.host, "listeners", return_value=[]):
            self.assertFalse(H.listened_by(self.host, 1, os.getuid()))

    def test_plist_runs_as_gate_with_system_path_only(self):
        current = {"cli": "/g/bin/agent-autoloop-x", "engine": {"commit": "e" * 40}, "repo": {"dir": "/g/repo/r"}}
        pl = H.plist_for(self.host, current, "hub", "http://127.0.0.1:3350")
        self.assertEqual(pl["UserName"], "dt-gate")
        self.assertEqual(pl["EnvironmentVariables"]["PATH"], H.SAFE_PATH)
        self.assertEqual(pl["ProgramArguments"], ["/g/bin/agent-autoloop-x", "up"])
        self.assertEqual(pl["EnvironmentVariables"]["DT_GRANTS_STATUS"], str(H.shared() / "grants/status.json"))
        self.assertEqual(pl["EnvironmentVariables"]["DT_HUB_LISTENER_UID"], str(os.getuid()))
        ro = H.plist_for(self.host, current, "readonly", "u")["ProgramArguments"]
        self.assertEqual(ro[:4], [H.PY, "-B", "-E", "-s"])
        self.assertTrue(ro[4].startswith("/g/repo/r/hermes/scripts/"))


# ---------- 端到端：真中台、假账号、假 launchd ----------
def repo_fixture(root):
    """把工作区里要装的文件提交进一个临时仓库（工作区还没提交的改动也算进去）。"""
    r = root / "repo-src"
    (r / "ops").mkdir(parents=True)
    (r / "hermes/scripts").mkdir(parents=True)
    for f in H.REPO_FILES:
        shutil.copy2(REPO / f, r / f)
    for f in (REPO / "hermes/scripts").glob("*.py"):
        shutil.copy2(f, r / "hermes/scripts" / f.name)
    shutil.copy2(REPO / "hermes/scripts/merge_rules.json", r / "hermes/scripts/merge_rules.json")
    for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "fixture"]):
        subprocess.run(["git", "-C", str(r), *cmd], check=True, capture_output=True)
    return r


@unittest.skipIf(SKIP_E2E, "本机没有 agent-autoloop 引擎检出或 node（或 DT_HUB_E2E=0）")
class MigrateEndToEndTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="dt-gate-e2e-")
        self.addCleanup(tmp.cleanup)
        d = self.d = Path(tmp.name).resolve()
        self.users = {n: d / "Users" / n for n in ("nat", "dt-gate", "dt-worker")}
        for h in self.users.values():
            h.mkdir(parents=True, mode=0o700)
        (d / "Shared").mkdir()
        (d / "LaunchDaemons").mkdir()
        self.ports = {"hub": free_port(), "ro": free_port(), "in": free_port()}
        env = {"DT_GATE_ROOT": d / "Library/DigitalTwin/gate", "DT_GATE_SHARED": d / "Shared/dt-gate",
               "DT_GATE_LOGS": d / "Library/Logs/DigitalTwin/gate", "DT_GATE_DAEMONS": d / "LaunchDaemons",
               "DT_GATE_READONLY_PORT": self.ports["ro"], "DT_GATE_INTAKE_PORT": self.ports["in"],
               "DT_GATE_USERS": d / "Users", "DT_GATE_ANY_COPY": "1"}
        (d / "Library/DigitalTwin").mkdir(parents=True)
        (d / "Library/Logs").mkdir(parents=True)
        p = mock.patch.dict(os.environ, {k: str(v) for k, v in env.items()})
        p.start()
        self.addCleanup(p.stop)
        self.host = FakeHost(self.users)
        self.addCleanup(self.host.stop_all)
        self.out = io.StringIO()
        self.start_owner_hub()
        self.repo = repo_fixture(d)

    def cli(self, home, *args, check=True):
        env = {**os.environ, "AGENT_AUTOLOOP_HOME": str(home)}
        r = subprocess.run([CLI, *args], capture_output=True, text=True, timeout=60, env=env, cwd=str(self.d))
        if check:
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return r.stdout.strip()

    def start_owner_hub(self):
        nat = self.users["nat"]
        self.ohub = nat / ".agent-autoloop"
        self.cli(self.ohub, "init", "--no-service", "--port", str(self.ports["hub"]), "--command", "echo {prompt}")
        self.cli(self.ohub, "key", "new", "worker", "dt-worker")
        self.cli(self.ohub, "key", "new", "source", "hermes")
        rules = json.loads((self.ohub / "rules.json").read_text())                # 加一条派单样子的规则（复核 P1-A）
        rules["rules"].append({**rules["rules"][0], "name": "dispatch-e2e", "when": {"source": "local", "types": ["dispatch.*"]}})
        (self.ohub / "rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2))
        (nat / "Library/LaunchAgents").mkdir(parents=True)
        self.oplist = nat / "Library/LaunchAgents/agent-autoloop.plist"
        self.oplist.write_bytes(plistlib.dumps({
            "Label": "agent-autoloop", "ProgramArguments": [CLI, "up"],
            "EnvironmentVariables": {"AGENT_AUTOLOOP_HOME": str(self.ohub), "PATH": os.environ["PATH"],
                                     "HOME": str(nat)},
            "StandardOutPath": str(self.d / "owner-up.log")}))
        self.assertEqual(self.host.launchctl("bootstrap", f"gui/{os.getuid()}", str(self.oplist))[0], 0)
        self.url = f"http://127.0.0.1:{self.ports['hub']}"
        self.wait_healthy()
        self.cli(self.ohub, "pause", "试一下", "--reason", "搬家测试")
        snap = U.snapshot(self.ohub)
        self.assertEqual(snap["dispatch_rules"], ["dispatch-e2e"])
        self.assertNotIn("dispatch-e2e", snap["pauses"])
        self.assertIn("暂停", self.cli(self.ohub, "emit", "try.migrate"))
        self.old_master = (self.ohub / "master.key").read_text()
        self.old_owner = json.loads((self.ohub / "config.json").read_text())["owner"]

    def wait_healthy(self, commit=None):
        for _ in range(100):
            try:
                code, body = H.http_get(self.url + "/healthz")
                if code == 200 and (commit is None or json.loads(body)["commit"] == commit):
                    return json.loads(body)
            except OSError:
                pass
            time.sleep(0.2)
        self.fail("中台没起来")

    def main(self, *args):
        with mock.patch("sys.stdout", self.out):
            return H.main(["--owner-account", "nat", *args], host=self.host)

    def keys(self, home):
        db = sqlite3.connect(f"file:{home / 'hub.db'}?mode=ro", uri=True)
        try:
            return {f"{k}:{n}": h for k, n, h in db.execute("SELECT kind, name, hash FROM keys")}
        finally:
            db.close()

    def stage(self):
        engine_commit = git(ENGINE, "rev-parse", "HEAD").decode().strip()
        repo_commit = git(self.repo, "rev-parse", "HEAD").decode().strip()
        self.assertEqual(self.main("stage", "--engine-repo", str(ENGINE), "--engine-commit", "HEAD", "--repo", str(self.repo),
                                   "--repo-commit", repo_commit, "--node", os.path.realpath(NODE)), 1)   # 只认 40 位提交号
        code = self.main("stage", "--engine-repo", str(ENGINE), "--engine-commit", engine_commit, "--repo", str(self.repo),
                         "--repo-commit", repo_commit, "--node", os.path.realpath(NODE))
        self.assertEqual(code, 0, self.out.getvalue())
        return engine_commit

    def test_full_flow(self):
        commit = self.stage()
        current = H.load_current()
        self.assertEqual(current["engine"]["commit"], commit)
        self.assertEqual(self.main("preflight"), 1)                                 # 派单规则没暂停：不过
        self.assertIn("没暂停的：['dispatch-e2e']", self.out.getvalue())
        self.cli(self.ohub, "pause", "dispatch-e2e", "--reason", "搬家测试")
        self.assertEqual(self.main("preflight"), 0, self.out.getvalue())
        gate = self.users["dt-gate"]

        # 中途出错：自动退回，主账号中台照常
        with mock.patch.object(H, "wait_health", side_effect=H.Stop("故意让健康检查不过")):
            self.assertEqual(self.main("migrate", "--confirm"), 1)
        self.assertIn("退回主账号中台", self.out.getvalue())
        self.wait_healthy()
        self.assertTrue((self.ohub / "config.json").exists())
        self.assertTrue(self.oplist.exists())
        self.assertFalse((gate / ".agent-autoloop").exists())
        self.assertEqual(len(list(gate.glob(".agent-autoloop.failed-*"))), 1)
        self.assertIsNone(H.load_state())
        self.assertNotIn("cn.digital-twin.gate.hub", self.host.procs)
        self.assertTrue(list(H.gate_root().glob("migrating.undone-*.json")))

        # 被打断（进程被杀、没来得及退回）：留下 migrating.json，preflight 不过；rollback 照记录收拾
        with mock.patch.object(H, "wait_health", side_effect=H.Stop("模拟被杀")), mock.patch.object(H, "undo"), \
                mock.patch.object(H.Progress, "close"):
            self.assertEqual(self.main("migrate", "--confirm"), 1)
        self.assertTrue((H.gate_root() / "migrating.json").exists())
        self.assertEqual(self.main("preflight"), 1)
        self.assertEqual(self.main("rollback", "--confirm"), 0, self.out.getvalue())
        self.assertIn("收拾好了", self.out.getvalue())
        self.wait_healthy()
        self.assertTrue((self.ohub / "config.json").exists())
        self.assertFalse((gate / ".agent-autoloop").exists())
        self.assertNotIn("cn.digital-twin.gate.hub", self.host.procs)
        self.assertFalse((H.daemons() / "cn.digital-twin.gate.hub.plist").exists())

        # 正式搬
        self.assertEqual(self.main("migrate", "--confirm"), 0, self.out.getvalue())
        self.wait_healthy(commit)
        state = H.load_state()
        ghub = gate / ".agent-autoloop"
        cfg = json.loads((ghub / "config.json").read_text())
        self.assertEqual(set(cfg), {"url", "hub", "owner"})
        self.assertNotEqual(cfg["owner"], self.old_owner)
        self.assertNotEqual((ghub / "master.key").read_text(), self.old_master)
        keys = self.keys(ghub)
        self.assertEqual(sorted(keys), sorted([f"owner:{state['owner_key']}", "worker:dt-worker"]))
        self.assertEqual(keys[f"owner:{state['owner_key']}"], hashlib.sha256(cfg["owner"].encode()).hexdigest())
        self.assertEqual(sorted(state["keys_removed"]), ["channel:desktop", "owner:owner", "source:hermes",
                                                         "source:local", f"worker:{json.loads((Path(state['owner_moved']) / 'config.json').read_text())['worker']['name']}"])
        facts = U.snapshot(ghub)
        self.assertEqual(facts["pauses"], ["dispatch-e2e", "试一下"])
        self.assertGreaterEqual(facts["max_event"], 1)
        self.assertTrue(self.ohub.is_file())                             # 原位置是说明文件，主账号命令行不会新建空中台
        self.assertTrue(self.ohub.read_text().startswith(H.MARK))
        self.assertFalse((H.gate_root() / "migrating.json").exists())
        self.assertTrue(list(H.gate_root().glob("migrating.done-*.json")))
        self.assertTrue((H.entry()).exists())
        self.assertTrue(Path(state["owner_moved"]).is_dir())
        self.assertFalse(self.oplist.exists())
        self.assertTrue(Path(state["plist_moved"]).exists())
        bk = Path(state["backup"])
        self.assertEqual(sorted(p.name for p in (bk / "owner-hub").iterdir()), ["config.json", "hub.db", "master.key", "rules.json"])
        self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in (bk / "owner-hub").iterdir()))
        self.assertEqual(json.loads((bk / "manifest.json").read_text())["files"]["master.key"],
                         hashlib.sha256(self.old_master.encode()).hexdigest())
        evid = self.users["nat"] / "Library/Application Support/DigitalTwin/evidence/gate-migrate"
        self.assertFalse(list(evid.glob("*/hub")))                       # 主账号里的临时备份删掉了
        self.assertTrue((H.shared() / "grants").is_dir() and (H.shared() / "tasks").is_dir())
        self.assertEqual(self.host.procs["cn.digital-twin.gate.hub"][1]["UserName"], "dt-gate")
        self.assertEqual(self.main("migrate", "--confirm"), 1)           # 搬过就不再搬

        # 主账号见过的本人密钥已经不认
        req = urllib.request.Request(self.url + "/v1/state", headers={"Authorization": f"Bearer {self.old_owner}"})
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=5)

        # 两个窗口
        self.assertEqual(self.main("windows", "--confirm"), 0, self.out.getvalue())
        osup = self.users["nat"] / "Library/Application Support/DigitalTwin/autoloop"
        gsup = gate / "Library/Application Support/DigitalTwin/autoloop"
        for t in ("readonly.token", "intake.token"):
            self.assertEqual((osup / t).read_text(), (gsup / t).read_text())
            self.assertEqual((osup / t).stat().st_mode & 0o777, 0o600)
        tok = (osup / "readonly.token").read_text().strip()
        req = urllib.request.Request(f"http://127.0.0.1:{self.ports['ro']}/v1/snapshot",
                                     headers={"Authorization": f"Bearer {tok}"})
        snap = json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10).read())
        self.assertIn("cards", snap)
        tok = (osup / "intake.token").read_text().strip()
        req = urllib.request.Request(f"http://127.0.0.1:{self.ports['in']}/v1/secretary/off", data=b"{}",
                                     method="POST", headers={"Authorization": f"Bearer {tok}"})
        res = json.loads(urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30).read())
        self.assertIn("code", res)

        # dt-worker 换接单钥匙
        wcfg = self.users["dt-worker"] / ".agent-autoloop/config.json"
        wcfg.parent.mkdir(mode=0o700)
        wcfg.write_text(json.dumps({"url": self.url, "worker": {"name": "dt-worker", "key": "old"}}))
        old_hash = self.keys(ghub)["worker:dt-worker"]
        pf = self.d / "pf"
        pf.mkdir()
        (pf / "pf-dt-worker.status").write_text("at=x state=ok enabled=yes\n")
        (pf / "pf-dt-worker.rules").write_text("pass out quick inet proto tcp from any to 127.0.0.1 port { 3350 } user dt-worker\n")
        with mock.patch.dict(os.environ, {"DT_PF_DIR": str(pf)}):
            self.assertEqual(self.main("rotate-worker", "--confirm"), 1)            # 门卫还没按监听账号保护 3350：不换
            self.assertIn("监听账号", self.out.getvalue())
            self.assertEqual(self.keys(ghub)["worker:dt-worker"], old_hash)
            (pf / "pf-dt-worker.rules").write_text("block return in quick proto tcp from any to 127.0.0.1 port 3350 user != dt-gate\n")
            self.assertEqual(self.main("rotate-worker", "--confirm"), 0, self.out.getvalue())
        newkey = json.loads(wcfg.read_text())["worker"]["key"]
        self.assertEqual(self.keys(ghub)["worker:dt-worker"], hashlib.sha256(newkey.encode()).hexdigest())
        self.assertNotEqual(self.keys(ghub)["worker:dt-worker"], old_hash)
        self.assertNotIn(newkey, self.out.getvalue())

        # 以 dt-gate 身份跑控制面工具
        self.assertEqual(self.main("run", "agent-autoloop", "key", "list"), 0)
        self.assertEqual(self.main("run", "agent-autoloop", "status"), 0)
        with mock.patch.object(self.host, "listeners", return_value=[os.getuid() + 1]):     # 端口被别人占：不带本人密钥去
            self.assertEqual(self.main("run", "agent-autoloop", "status"), 1)
        self.assertEqual(self.main("status"), 0)
        self.assertIn("在跑", self.out.getvalue())

        # 回滚
        self.assertEqual(self.main("rollback", "--confirm"), 0, self.out.getvalue())
        for label in H.LABEL.values():
            self.assertNotIn(label, self.host.procs)
            self.assertFalse((H.daemons() / f"{label}.plist").exists())
        self.wait_healthy()
        self.assertEqual(json.loads((self.ohub / "config.json").read_text())["owner"], self.old_owner)
        self.assertTrue(self.oplist.exists())
        self.assertIsNone(H.load_state())
        self.assertTrue(list(gate.glob(".agent-autoloop.rolled-back-*")))
        self.assertTrue(list(gate.glob(".agent-autoloop-rollback-*/hub.db")))
        self.assertIn("接单钥匙在 dt-gate 里换过", self.out.getvalue())
        self.assertEqual(self.main("preflight"), 0, self.out.getvalue())   # 回滚后可以重新搬


if __name__ == "__main__":
    unittest.main()
