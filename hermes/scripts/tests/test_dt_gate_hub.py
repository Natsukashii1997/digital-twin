"""中台和网关搬进 dt-gate（ADR-015 批 0E-2）：ops/dt_gate_hub.py、ops/dt_gate_user.py 的自测。

真机要 root、要真账号，这里全换成替身：账号是临时目录里的几个「家目录」（进程都是当前用户），launchctl 是按 plist 起停子进程的
假的，root 路径核对（dt_root_guard.sh）按测试给的结果。端到端用例在临时目录里起真的 agent-autoloop 中台（随机端口），
走 stage → preflight → migrate（含中途出错自动退回）→ windows → rotate-worker → status → rollback；
本机没有 agent-autoloop 命令行或引擎不是 git 检出（如 CI）时跳过，DT_HUB_E2E=0 也跳过。
不碰 ~/.agent-autoloop、正式中台 127.0.0.1:3350、/Library、/Users/Shared。真跨账号、真 launchd 由常浩按命令清单在真机验。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import hashlib
import errno
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

REAL_RECOVERY_AS = H.recovery_as        # ChecksTest 默认把它换成替身；测它自己时用原来的


def pf_fixture(d):
    """假门卫目录：root 副本门卫脚本（测试里属主当 root）、新版状态 guard=no、本次开机写的。"""
    d.mkdir(exist_ok=True)
    (d / "dt_worker_pf.sh").write_text("#!/bin/bash\n")
    os.chmod(d / "dt_worker_pf.sh", 0o755)
    (d / "pf-dt-worker.status").write_text("at=x state=ok enabled=yes boot=1795000000 guard=no\n")

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
        self.stuck = set()                     # launchctl print 一直说还在的标签（卸不掉，L3 第 2 轮 P1-01）

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
            if label in self.stuck:
                return 0, "state = running"
            p = self.procs.get(label, (None,))[0]
            return (0, f"state = running\n\tpid = {p.pid}") if p and p.poll() is None else (113, "not found")
        return 0, ""                            # enable / disable

    def boot_sec(self):
        return "1795000000"

    def pf_apply(self, script):
        """假门卫（#313 新版）：按标记在不在写状态 guard=yes / no；pf_fail 里还有次数就写 failed（模拟和定时 apply 撞车）。"""
        d = Path(script).parent
        self.calls.append(("pf_apply", str(script)))
        if getattr(self, "pf_fail", 0):
            self.pf_fail -= 1
            (d / "pf-dt-worker.status").write_text("at=x state=failed reason=对不上 boot=1795000000\n")
            return 1
        guard = "yes" if os.path.lexists(d / "hub-in-gate") else "no"
        (d / "pf-dt-worker.status").write_text(f"at=x state=ok enabled=yes boot=1795000000 guard={guard}\n")
        return 0

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
        pf_fixture(self.d / "pf")
        q = mock.patch.object(H, "ROOT_UID", os.getuid())
        q.start()
        self.addCleanup(q.stop)
        # 脚本里每个有真机默认值的目录都指到临时目录（11-22 漏了启动脚本目录：首单在真机装了 dt-worktree-*，这里就读到了）
        for k, v in {"DT_GATE_ROOT": root, "DT_GATE_SHARED": self.d / "shared/dt-gate", "DT_GATE_USERS": self.d / "users",
                     "DT_GATE_ANY_COPY": "1", "DT_PF_DIR": self.d / "pf", "DT_GATE_LAUNCHERS": self.d / "launchers",
                     "DT_GATE_LOGS": self.d / "logs", "DT_GATE_DAEMONS": self.d / "daemons",
                     "DT_GATE_READONLY_PORT": free_port(), "DT_GATE_INTAKE_PORT": free_port()}.items():
            p = mock.patch.dict(os.environ, {k: str(v)})
            p.start()
            self.addCleanup(p.stop)
        (users["nat"] / "Library/LaunchAgents").mkdir(parents=True)
        (users["nat"] / "Library/LaunchAgents/agent-autoloop.plist").write_text("x")
        self.users = users
        q = mock.patch.object(H, "recovery_as", self.fake_recovery())    # 主账号恢复闸默认干净；要测的用例自己再换
        q.start()
        self.addCleanup(q.stop)

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

    # ---------- 0D 恢复门：停写 → 排空 ----------
    def fake_recovery(self, hold=None, items=None, writes=None, log=None, fail=(), cards=()):
        """恢复闸命令行的替身：leftovers / writes 按给的返回（writes、items 可以是一串，逐次取，最后一个一直用），
        hold / release 改闸上的恢复点；fail 里的命令抛 Stop。"""
        st = {"hold": hold}
        seq = {"writes": list(writes) if isinstance(writes, list) and writes and isinstance(writes[0], list) else [writes or []],
               "items": list(items) if isinstance(items, list) and items and isinstance(items[0], list) else [items or []]}

        def take(k):
            return seq[k].pop(0) if len(seq[k]) > 1 else seq[k][0]

        def run(host, current, name, args):
            if log is not None:
                log.append((name, args[0], args))
            if args[0] in fail:
                raise H.Stop(f"故意让 {args[0]} 失败")
            if args[0] == "leftovers":
                return {"hold": st["hold"], "items": take("items")}
            if args[0] == "writes":
                return {"in_flight": take("writes")}
            if args[0] == "cards-after":
                return {"cards": list(cards)}
            if args[0] == "hold":
                st["hold"] = args[args.index("--point") + 1]
            if args[0] == "release":
                st["hold"] = None
            return {"ok": True}
        run.state = st
        return run

    def ledger_checks(self, **kw):
        with mock.patch.object(H, "recovery_as", self.fake_recovery(**kw)):
            return self.run_checks(self.snap())

    def test_ledger_clean_passes(self):
        r = self.ledger_checks()
        self.assertTrue(all(r.values()), [m for m, ok in r.items() if not ok])

    def test_ledger_hold_or_leftovers_each_block(self):
        """不管出口账本在不在都问 leftovers（主账号删掉 exits.db 也绕不过）：维护中、任何一类没结清的都拦。"""
        for kw, word in (({"hold": "upgrade-1"}, "维护停写"),
                         ({"items": ["出口 push_branch:x（unknown）"]}, "没结清"),
                         ({"items": ["本人点击 卡片 3（queued）"]}, "没结清"),
                         ({"items": ["秘书代批 d-1（unreconciled）"]}, "没结清"),
                         ({"items": ["派单事件 e-1（没送达）"]}, "没结清"),
                         ({"items": ["关闸标记 2 个"]}, "没结清")):
            with self.subTest(kw=kw):
                bad = [m for m, ok in self.ledger_checks(**kw).items() if not ok]
                self.assertEqual(len(bad), 1, bad)
                self.assertIn(word, bad[0])

    def test_ledger_unreadable_blocks(self):
        """读不了（副本里没有恢复闸、读不懂）一律不过。"""
        with mock.patch.object(H, "recovery_as", side_effect=H.Stop("读不懂")):
            r = self.run_checks(self.snap())
        self.assertFalse(r["读主账号 0D 恢复闸：读不懂"])
        with mock.patch.object(H, "recovery_as", return_value={"hold": None}):        # 没给 items：不算干净
            r = self.run_checks(self.snap())
        self.assertEqual(len([m for m, ok in r.items() if not ok]), 1)

    def test_recovery_as_reads_json_and_refuses(self):
        current = {"repo": {"dir": str(self.d)}}
        for code, out, ok in ((0, b'{"in_flight": []}', True), (2, b'{"open": false}', False),
                              (1, b'{"ok": false, "why": "x"}', False), (0, b"not json", False), (0, b"[]", False)):
            with self.subTest(code=code, out=out), mock.patch.object(self.host, "as_user", return_value=(code, out, "")):
                if ok:
                    self.assertIsInstance(REAL_RECOVERY_AS(self.host, current, "nat", ["writes"]), dict)
                else:
                    with self.assertRaises(H.Stop):
                        REAL_RECOVERY_AS(self.host, current, "nat", ["writes"])
        with mock.patch.object(self.host, "as_user", side_effect=subprocess.TimeoutExpired("x", 120)):
            with self.assertRaisesRegex(H.Stop, "超时"):
                REAL_RECOVERY_AS(self.host, current, "nat", ["writes"])

    def test_hold_then_drain_waits_for_in_flight_writes(self):
        log = []
        run = self.fake_recovery(writes=[["本人回答 卡片 3"], ["本人回答 卡片 3"], []], log=log)
        current = {"engine": {"commit": "e" * 40}}
        with mock.patch.object(H, "recovery_as", run):
            H.hold_and_drain(self.host, current, "nat", "gate-migrate-S", self.d / "bk", 7)
        self.assertEqual([c[1] for c in log], ["hold", "writes", "writes", "writes", "leftovers"])
        hold = log[0][2]
        self.assertEqual(hold[hold.index("--max-card") + 1], "7")
        self.assertEqual(hold[hold.index("--commit") + 1], "e" * 40)
        self.assertEqual(hold[hold.index("--point") + 1], "gate-migrate-S")
        self.assertIn("--confirm", hold)

    def test_drain_rechecks_after_last_sleep_then_times_out(self):
        log = []
        with mock.patch.object(H, "recovery_as", self.fake_recovery(writes=["出口 x"], log=log)), \
                mock.patch.object(H, "DRAIN_SECONDS", 4):
            with self.assertRaisesRegex(H.Stop, "还有在途的写"):
                H.hold_and_drain(self.host, {"engine": {"commit": "e" * 40}}, "nat", "p", self.d, 0)
        self.assertEqual([c[1] for c in log].count("writes"), 3)          # 2 次 + 睡完最后一次复查
        log = []
        with mock.patch.object(H, "recovery_as", self.fake_recovery(writes=[["出口 x"], ["出口 x"], []], log=log)), \
                mock.patch.object(H, "DRAIN_SECONDS", 4):
            H.hold_and_drain(self.host, {"engine": {"commit": "e" * 40}}, "nat", "p", self.d, 0)   # 最后一次空了就过

    def test_leftovers_after_drain_stop(self):
        """排空以后还有没结清的（hold 之前那一刻冒出来的结果不明）：停下。"""
        with mock.patch.object(H, "recovery_as", self.fake_recovery(items=["出口 x（unknown）"])):
            with self.assertRaisesRegex(H.Stop, "没结清"):
                H.hold_and_drain(self.host, {"engine": {"commit": "e" * 40}}, "nat", "p", self.d, 0)

    def test_release_only_our_own_hold(self):
        current = {}
        for hold, released in (("gate-migrate-S", True), ("upgrade-1", False), (None, False)):
            log = []
            with self.subTest(hold=hold), mock.patch.object(H, "recovery_as", self.fake_recovery(hold=hold, log=log)):
                self.assertTrue(H.release_hold(self.host, current, "nat", "gate-migrate-S"))
                self.assertEqual("release" in [c[1] for c in log], released)
        with mock.patch.object(H, "recovery_as", self.fake_recovery(hold="gate-migrate-S", fail=("release",))), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertFalse(H.release_hold(self.host, current, "nat", "gate-migrate-S"))
        self.assertIn("还在停写", out.getvalue())

    def migrate_failing(self, run, fail_at="unload"):
        """migrate 走到停中台那步故意出错；返回调用顺序。主账号中台目录在（退回时核它放回来了）。"""
        (self.users["nat"] / ".agent-autoloop").mkdir(exist_ok=True)
        (self.users["nat"] / ".agent-autoloop/config.json").write_text("{}")
        order = []

        def recovery(*a):
            order.append(a[3][0])
            return run(*a)

        def unload(*a):
            order.append("unload")
            raise H.Stop("故意停不下")
        with mock.patch.object(H, "recovery_as", recovery), \
                mock.patch.object(H, "snapshot_as", return_value=self.snap(max_card=5)), \
                mock.patch.object(H, "unload", unload), \
                mock.patch.object(H, "start_owner_agent"), mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(H.Stop, "故意停不下"):
                H.cmd_migrate(self.host, self.a())
        return order

    def test_migrate_holds_before_stopping_and_releases_when_undone(self):
        """停写在停中台之前；后面出错退回时用同一个恢复点结束维护。"""
        log = []
        run = self.fake_recovery(log=log)
        order = self.migrate_failing(run)
        self.assertLess(order.index("hold"), order.index("unload"))
        self.assertGreater(order.index("release"), order.index("unload"))
        hold = [c for c in log if c[1] == "hold"][0][2]
        self.assertEqual(hold[hold.index("--max-card") + 1], "5")
        rel = [c for c in log if c[1] == "release"][0][2]
        self.assertEqual(rel[rel.index("--point") + 1], hold[hold.index("--point") + 1])
        self.assertEqual(rel[rel.index("--outcome") + 1], "rolled_back")
        self.assertIsNone(run.state["hold"])
        self.assertTrue(list(H.gate_root().glob("migrating.undone-*.json")))

    def test_undo_keeps_hold_and_progress_when_release_fails(self):
        run = self.fake_recovery(fail=("release",))
        self.migrate_failing(run)
        self.assertTrue(run.state["hold"].startswith("gate-migrate-"))
        self.assertTrue((H.gate_root() / "migrating.json").exists())             # 没收拾干净：进度记录留着
        log = []                                                                  # rollback 照记录再收拾一次，这次结束停写
        run2 = self.fake_recovery(hold=run.state["hold"], log=log)
        with mock.patch.object(H, "recovery_as", run2), mock.patch.object(H, "start_owner_agent"), \
                mock.patch.object(H, "unmark_owner"), mock.patch.object(H, "exists_as", return_value=True), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(H.cmd_rollback(self.host, self.a()), 0)
        self.assertIsNone(run2.state["hold"])
        self.assertFalse((H.gate_root() / "migrating.json").exists())

    def test_undo_does_not_release_while_other_steps_not_restored(self):
        """主账号中台目录没放回之类：先保持停写（复核 P2：别的没收拾好就解除停写）。"""
        run = self.fake_recovery()
        real = H.exists_as
        with mock.patch.object(H, "exists_as",                                    # 主账号的中台目录「没放回来」
                               side_effect=lambda h, n, p: False if str(p).endswith(".agent-autoloop/config.json") else real(h, n, p)):
            self.migrate_failing(run)
        self.assertTrue(run.state["hold"].startswith("gate-migrate-"))

    def test_killed_after_hold_rollback_releases(self):
        """mark("held") 之后被杀（没来得及退回）：rollback 照进度记录结束这次停写。"""
        run = self.fake_recovery()
        with mock.patch.object(H, "undo"), mock.patch.object(H.Progress, "close"):
            self.migrate_failing(run)
        point = run.state["hold"]
        self.assertTrue(point.startswith("gate-migrate-"))
        self.assertTrue(json.loads((H.gate_root() / "migrating.json").read_text())["done"]["held"])
        with mock.patch.object(H, "recovery_as", run), mock.patch.object(H, "start_owner_agent"), \
                mock.patch.object(H, "unmark_owner"), mock.patch.object(H, "exists_as", return_value=True), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(H.cmd_rollback(self.host, self.a()), 0)
        self.assertIsNone(run.state["hold"])

    def test_full_rollback_releases_owner_hold_or_records_it(self):
        root = Path(os.environ["DT_GATE_ROOT"])
        cases = (((), (), None, 5), (("release",), (), "gate-migrate-S", 5),
                 ((), ("cards.db 卡片 9",), "gate-migrate-S", 5),     # dt-gate 搬家后发过卡片：保持停写（0D 指出的缺口）
                 ((), (), "gate-migrate-S", None),                    # 记录里没有最大卡片号：核不了，保持停写
                 (("cards-after",), (), "gate-migrate-S", 5))         # 查不了：保持停写
        for n, (fail, cards, left, max_card) in enumerate(cases):
            with self.subTest(fail=fail, cards=cards, max_card=max_card):
                w = self.d / f"rb{n}"
                (w / "moved").mkdir(parents=True)
                base = {"owner_account": "nat", "owner_hub": str(w / "hub"), "owner_moved": str(w / "moved"),
                        "plist_moved": str(w / "p"), "plist_home": str(w / "ph"), "hub_url": "http://127.0.0.1:1",
                        "owner_hold": "gate-migrate-S", "owner_hold_max_card": max_card}
                (root / "state.json").write_text(json.dumps(base))
                log = []
                run = self.fake_recovery(hold="gate-migrate-S", fail=fail, cards=cards, log=log)
                with mock.patch.object(H, "recovery_as", run), mock.patch.object(H, "start_owner_agent"), \
                        mock.patch.object(H, "unmark_owner"), mock.patch.object(H, "remove_daemon"), \
                        mock.patch.object(H, "installed", return_value=False), \
                        mock.patch.object(H, "now_stamp", return_value=f"R{n}"), \
                        mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(H.cmd_rollback(self.host, self.a()), 0)
                arch = json.loads((root / f"state.rolled-back-R{n}.json").read_text())
                self.assertEqual(arch.get("owner_hold_left"), left)
                self.assertEqual(run.state["hold"], left)
                if max_card is not None:
                    q = [c for c in log if c[1] == "cards-after"][0]
                    self.assertEqual(q[0], "dt-gate")                                 # 以 dt-gate 身份查它自己的账本
                    self.assertEqual(q[2][q[2].index("--max-card") + 1], "5")

    # ---------- L3 第 1 轮：max_card 在 hold 之前读，读完到停中台之间新建的卡片 ----------
    # 引擎（abc8d41 kernel/decide.ts:105）给卡片编号是 id = next[cards] + 1，只增不减：窗口里新建的卡片号都 > 读到的 max_card。
    def test_window_card_open_at_stop_aborts_migrate_with_pre_hold_max(self):
        """读完 max_card 后、停中台前中台新建了一张卡（还开着）：停下以后核出来、整条退回；hold 登记的是停写前读的号。"""
        (self.users["nat"] / ".agent-autoloop").mkdir(exist_ok=True)
        (self.users["nat"] / ".agent-autoloop/config.json").write_text("{}")
        stopped = {"v": False}
        log = []
        run = self.fake_recovery(log=log)

        def snap(*a):                                  # 停中台之前最大号 5；停下以后多出卡片 6，还开着
            return self.snap(max_card=6, live_cards=1) if stopped["v"] else self.snap(max_card=5)
        with mock.patch.object(H, "recovery_as", run), mock.patch.object(H, "snapshot_as", snap), \
                mock.patch.object(H, "unload", lambda *a: (stopped.update(v=True), True)[1]), \
                mock.patch.object(H, "wait_port_free"), mock.patch.object(H, "start_owner_agent"), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(H.Stop, "没答完的卡片"):
                H.cmd_migrate(self.host, self.a())
        hold = [c for c in log if c[1] == "hold"][0][2]
        self.assertEqual(hold[hold.index("--max-card") + 1], "5")
        self.assertIsNone(run.state["hold"])                                     # 退回：主账号原样，结束停写
        self.assertIsNone(H.load_state())

    def test_rollback_flags_a_window_card_with_the_real_cards_after(self):
        """回滚核卡片号用真的恢复闸脚本（以 dt-gate 身份、它自己的家目录）：dt-gate 发过窗口里那张卡（号 = 搬家时最大号 + 1）
        就保持主账号停写；只有号不大于最大号的卡片才放行。停写前读的号只会偏小，偏小只会多拦、不会漏。"""
        import autoloop_feishu as F
        gate_home = self.users["dt-gate"]
        cards = gate_home / "Library/Application Support/DigitalTwin/autoloop/cards.db"
        current = {"repo": {"dir": str(REPO)}}
        with mock.patch.object(H, "recovery_as", REAL_RECOVERY_AS):
            self.assertTrue(H.gate_cards_clean(self.host, current, 5))                # dt-gate 还没有账本：干净，也不建库
            self.assertFalse(cards.exists())
            with mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": str(cards)}):
                with F.db(write=True) as con:
                    con.execute("INSERT INTO feishu_cards (card_id, token, chat_id, kind, options, version) "
                                "VALUES (5, ?, 'oc_x', 'ask', 2, 1)", ("a" * 16,))
            self.assertTrue(H.gate_cards_clean(self.host, current, 5))                # 号 = 最大号：搬家前就有的
            with mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": str(cards)}):
                with F.db(write=True) as con:
                    con.execute("INSERT INTO feishu_cards (card_id, token, chat_id, kind, options, version) "
                                "VALUES (6, ?, 'oc_x', 'ask', 2, 1)", ("b" * 16,))
            with mock.patch("sys.stdout", io.StringIO()) as out:
                self.assertFalse(H.gate_cards_clean(self.host, current, 5))           # 窗口卡片 6：拦下
            self.assertIn("卡片 6", out.getvalue())
            with mock.patch("sys.stdout", io.StringIO()):
                self.assertFalse(H.gate_cards_clean(self.host, current, 4))           # 号读得偏小：多拦，不漏

    def test_worker_launchers_must_match_the_staged_copy(self):
        """搬家后凭证镜像在 /Users/Shared/dt-gate/：装着的工人启动脚本要换成副本里的新版；没装的不拦。"""
        (self.d / "ops").mkdir()
        (self.d / "ops/dt_worktree.py").write_text("new")
        bin_ = self.d / "launchers"
        bin_.mkdir()
        with mock.patch.dict(os.environ, {"DT_GATE_LAUNCHERS": str(bin_)}):
            r = self.run_checks(self.snap())
            self.assertTrue(all(r.values()), [m for m, ok in r.items() if not ok])          # 都没装
            (bin_ / "dt-worktree-claude").write_text("new")
            (bin_ / "dt-worktree-codex").write_text("old")                                 # 今晚装的 main 版
            bad = [m for m, ok in self.run_checks(self.snap()).items() if not ok]
            self.assertEqual(len(bad), 1, bad)
            self.assertIn("dt-worktree-codex", bad[0])
            (bin_ / "dt-worktree-codex").unlink()
            (bin_ / "dt-worktree-codex").symlink_to(self.d / "ops/dt_worktree.py")       # 链接不算
            self.assertEqual(len([m for m, ok in self.run_checks(self.snap()).items() if not ok]), 1)
            (bin_ / "dt-worktree-codex").unlink()
            (bin_ / "dt-worktree-codex").write_text("new")
            r = self.run_checks(self.snap())
            self.assertTrue(all(r.values()), [m for m, ok in r.items() if not ok])

    # ---------- 门卫监听者规则（#313） ----------
    def pf(self, line, age=0):
        f = Path(os.environ["DT_PF_DIR"]) / "pf-dt-worker.status"
        f.write_text(line)
        if age:
            t0 = time.time() - age
            os.utime(f, (t0, t0))

    def test_guard_problem_judges_state_guard_freshness_and_boot(self):
        ok = "at=x state=ok enabled=yes boot=1795000000 guard=yes\n"
        self.pf(ok)
        self.assertIsNone(H.pf_guard_problem(self.host, "yes"))
        self.assertIn("guard=yes", H.pf_guard_problem(self.host, "no"))
        for line, age, word in (("at=x state=failed boot=1795000000 guard=yes", 0, "不在生效"),
                                (ok, 11 * 60, "没刷新"),
                                ("at=x state=ok boot=1700000000 guard=yes", 0, "本次开机"),
                                ("at=x state=ok guard=yes", 0, "本次开机"),
                                ("at=x state=ok boot=1795000000", 0, "#313")):
            with self.subTest(line=line):
                self.pf(line, age)
                self.assertIn(word, H.pf_guard_problem(self.host, "yes"))
        self.pf("at=x state=ok enabled=yes boot=1795000000\n")                       # 旧版门卫不认标记：算关着
        self.assertIsNone(H.pf_guard_problem(self.host, "no"))
        (Path(os.environ["DT_PF_DIR"]) / "pf-dt-worker.status").unlink()
        self.assertIn("读不到", H.pf_guard_problem(self.host, "no"))

    def test_guard_on_writes_root_mark_and_retries_apply(self):
        self.host.pf_fail = 2                                                         # 前两次和定时 apply 撞车
        H.guard_on(self.host, "S")
        mark = H.hub_mark()
        self.assertEqual(mark.read_text(), "dt-gate S\n")
        self.assertEqual(mark.stat().st_mode & 0o777, 0o644)
        self.assertIn(str(mark), self.host.chowns)
        self.assertEqual(len([c for c in self.host.calls if c[0] == "pf_apply"]), 3)
        self.assertTrue(H.guard_off(self.host))
        self.assertFalse(os.path.lexists(mark))
        self.host.pf_fail = 3                                                         # 三次都撞：失败
        with self.assertRaisesRegex(H.Stop, "guard=yes"):
            H.guard_on(self.host, "S")
        self.host.guard_fail.add(("root_path", os.environ["DT_PF_DIR"]))
        with self.assertRaisesRegex(H.Stop, "只有 root 能改"):
            H.guard_on(self.host, "S")

    def test_guard_off_removes_a_symlink_itself_and_reports_failure(self):
        target = self.d / "elsewhere"
        target.write_text("keep")
        H.hub_mark().symlink_to(target)
        self.assertTrue(H.guard_off(self.host))
        self.assertTrue(target.exists())
        self.host.pf_fail = 3
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertFalse(H.guard_off(self.host))
        self.assertIn("先不启用", out.getvalue())

    def test_leftover_mark_blocks_preflight(self):
        H.hub_mark().write_text("dt-gate old\n")
        r = self.run_checks(self.snap())
        self.assertEqual([m for m, ok in r.items() if not ok], [m for m in r if "门卫标记" in m])

    def test_undo_turns_guard_off_before_starting_owner_hub(self):
        """第 4b 步之后出错：先关监听者规则再启用主账号中台；关不掉就不启用。"""
        order = []
        (self.users["nat"] / ".agent-autoloop").mkdir(exist_ok=True)
        (self.users["nat"] / ".agent-autoloop/config.json").write_text("{}")
        H.hub_mark().write_text("dt-gate S\n")
        real_apply = self.host.pf_apply
        self.host.pf_apply = lambda script: (order.append("pf_apply"), real_apply(script))[1]
        with mock.patch.object(H, "start_owner_agent", lambda *a: order.append("start")), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertTrue(H.undo(self.host, {"repo": {"dir": str(self.d)}}, "nat", "S",
                                   {"stopped": True, "guard_on": True}))
            self.assertEqual(order, ["pf_apply", "start"])
            order.clear()
            H.hub_mark().write_text("dt-gate S\n")
            self.host.pf_fail = 3
            self.assertFalse(H.undo(self.host, {"repo": {"dir": str(self.d)}}, "nat", "S",
                                    {"stopped": True, "guard_on": True}))
            self.assertNotIn("start", order)

    def test_preflight_needs_new_guard_loaded_and_listener_rule_off(self):
        """独立审查第 2 轮 P2-1：门卫不在生效、是旧版（没有 guard=）、监听者规则已经开着，preflight 各拦一条。"""
        for line, word in (("at=x state=failed reason=x boot=1795000000 guard=no\n", "不在生效"),
                           ("at=x state=ok enabled=yes boot=1795000000\n", "旧版"),
                           ("at=x state=ok enabled=yes boot=1795000000 guard=yes\n", "guard=yes")):
            with self.subTest(line=line):
                self.pf(line)
                bad = [m for m, ok in self.run_checks(self.snap()).items() if not ok]
                self.assertEqual(len(bad), 1, bad)
                self.assertIn("门卫规则在生效", bad[0])
                self.assertIn(word, bad[0])
        (Path(os.environ["DT_PF_DIR"]) / "pf-dt-worker.status").unlink()             # 读不到：也拦，不抛异常
        bad = [m for m, ok in self.run_checks(self.snap()).items() if not ok]
        self.assertEqual(len(bad), 1, bad)
        self.assertIn("读不到", bad[0])

    def test_pf_apply_refuses_a_guard_script_root_does_not_own(self):
        """独立审查第 2 轮 P2-4：门卫脚本不在、不是普通文件、不属 root、别人能写，都不以 root 跑它。"""
        pfd = Path(os.environ["DT_PF_DIR"])
        script, real = pfd / "dt_worker_pf.sh", pfd / "real.sh"
        cases = (("组能写", lambda: os.chmod(script, 0o775), os.getuid(), "root 所有"),
                 ("其他人能写", lambda: os.chmod(script, 0o757), os.getuid(), "root 所有"),
                 ("不属 root", lambda: None, os.getuid() + 1, "root 所有"),
                 ("是链接", lambda: (script.rename(real), script.symlink_to(real)), os.getuid(), "root 所有"),
                 ("不在", lambda: script.unlink(), os.getuid(), "没有门卫脚本"))
        for name, spoil, root_uid, word in cases:
            with self.subTest(name=name):
                for f in (script, real):
                    if os.path.lexists(f):
                        f.unlink()
                pf_fixture(pfd)
                spoil()
                self.host.calls.clear()
                with mock.patch.object(H, "ROOT_UID", root_uid), self.assertRaisesRegex(H.Stop, word):
                    H.pf_apply(self.host, "no")
                self.assertFalse([c for c in self.host.calls if c[0] == "pf_apply"])
        for f in (script, real):
            if os.path.lexists(f):
                f.unlink()
        pf_fixture(pfd)
        H.pf_apply(self.host, "no")                                                   # 复原后照常跑
        self.assertEqual(len([c for c in self.host.calls if c[0] == "pf_apply"]), 1)

    def test_hold_defers_a_stop_signal_until_it_is_recorded(self):
        """独立审查第 2 轮 P2-3：hold 这一下收到停止信号，等 hold 做完才抛，退回时能看到这次的恢复点。"""
        import signal
        run = self.fake_recovery()

        def hold_then_killed(host, current, name, args):
            res = run(host, current, name, args)
            if args[0] == "hold":
                os.kill(os.getpid(), signal.SIGTERM)                                  # 落在 hold 中间
                time.sleep(0.05)
                run.after_kill = True                                                 # 信号没在这里打断
            return res
        old = signal.signal(signal.SIGTERM, H._on_term)
        self.addCleanup(signal.signal, signal.SIGTERM, old)
        with mock.patch.object(H, "recovery_as", hold_then_killed):
            with self.assertRaisesRegex(H.Stop, "停止信号"):
                H.hold_and_drain(self.host, {"engine": {"commit": "e" * 40}}, "nat", "gate-migrate-S", self.d, 0)
        self.assertTrue(getattr(run, "after_kill", False))
        self.assertEqual(run.state["hold"], "gate-migrate-S")
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, set()) & {signal.SIGTERM, signal.SIGINT}, set())

    def test_undo_after_guard_on_releases_hold_only_when_guard_is_off(self):
        """第 4b 步之后出错：关门卫 → 启用主账号中台 → 结束停写；门卫关不掉就不启用、保持停写。"""
        (self.users["nat"] / ".agent-autoloop").mkdir(exist_ok=True)
        (self.users["nat"] / ".agent-autoloop/config.json").write_text("{}")
        done = {"held": True, "stopped": True, "guard_on": True}
        for pf_fail, started, held in ((0, True, None), (3, False, "gate-migrate-S")):
            with self.subTest(pf_fail=pf_fail):
                H.hub_mark().write_text("dt-gate S\n")
                self.host.pf_fail = pf_fail
                order = []
                run = self.fake_recovery(hold="gate-migrate-S")
                real_apply = self.host.pf_apply
                with mock.patch.object(self.host, "pf_apply", lambda s: (order.append("pf_apply"), real_apply(s))[1]), \
                        mock.patch.object(H, "recovery_as", lambda *a: (order.append(a[3][0]), run(*a))[1]), \
                        mock.patch.object(H, "start_owner_agent", lambda *a: order.append("start")), \
                        mock.patch("sys.stdout", io.StringIO()):
                    self.assertEqual(H.undo(self.host, {"repo": {"dir": str(self.d)}}, "nat", "S", done), started)
                self.assertEqual("start" in order, started)
                self.assertEqual(run.state["hold"], held)
                if started:
                    self.assertLess(order.index("pf_apply"), order.index("start"))
                    self.assertLess(order.index("start"), order.index("release"))
                    self.assertFalse(os.path.lexists(H.hub_mark()))
                else:
                    self.assertNotIn("release", order)

    def rollback_state(self, w):
        """搬完的样子：主账号原位置是说明文件，原目录改了名。"""
        (w / "moved").mkdir(parents=True)
        (w / "moved/config.json").write_text("{}")
        (w / "hub").write_text(f"{H.MARK}（S）\n")
        s = {"owner_account": "nat", "owner_hub": str(w / "hub"), "owner_moved": str(w / "moved"),
             "plist_moved": str(w / "p"), "plist_home": str(w / "ph"), "hub_url": "http://127.0.0.1:1",
             "owner_hold": "gate-migrate-S", "owner_hold_max_card": 5}
        (Path(os.environ["DT_GATE_ROOT"]) / "state.json").write_text(json.dumps(s))
        return s

    def test_rollback_keeps_note_and_dir_when_guard_off_fails_then_resumes(self):
        """独立审查第 2 轮 P2-2：关门卫失败时说明文件不删、目录不放回、state 留着、不结束停写；修好再跑一次接着来。"""
        w = self.d / "rbg"
        self.rollback_state(w)
        run = self.fake_recovery(hold="gate-migrate-S")
        unmark = mock.Mock(side_effect=lambda *a: (w / "hub").unlink())
        start = mock.Mock()
        with mock.patch.object(H, "recovery_as", run), mock.patch.object(H, "start_owner_agent", start), \
                mock.patch.object(H, "unmark_owner", unmark), mock.patch.object(H, "installed", return_value=False), \
                mock.patch.object(H, "now_stamp", return_value="G"), mock.patch("sys.stdout", io.StringIO()):
            self.host.pf_fail = 3
            with self.assertRaisesRegex(H.Stop, "门卫监听者规则没关掉"):
                H.cmd_rollback(self.host, self.a())
            unmark.assert_not_called()
            start.assert_not_called()
            self.assertTrue((w / "hub").read_text().startswith(H.MARK))
            self.assertTrue((w / "moved/config.json").exists())
            self.assertTrue((Path(os.environ["DT_GATE_ROOT"]) / "state.json").exists())
            self.assertEqual(run.state["hold"], "gate-migrate-S")
            self.assertEqual(H.cmd_rollback(self.host, self.a()), 0)                  # 门卫好了，再跑一次
        self.assertEqual((w / "hub/config.json").read_text(), "{}")
        start.assert_called_once()
        self.assertIsNone(run.state["hold"])
        self.assertIsNone(H.load_state())

    # ---------- L3 第 2 轮 P1-01：服务卸不掉、端口还有人听，回滚 / 退回 / 搬家都停下 ----------
    def plists(self):
        d = self.d / "daemons"
        d.mkdir(exist_ok=True)
        for which in ("hub", "readonly", "intake"):
            (d / f"{H.LABEL[which]}.plist").write_text("x")
        return d

    def guarded(self):
        """搬完的门卫：标记在、状态 guard=yes。"""
        H.hub_mark().write_text("dt-gate S\n")
        self.pf("at=x state=ok enabled=yes boot=1795000000 guard=yes\n")

    def test_remove_daemon_keeps_plist_when_launchctl_still_shows_it(self):
        d = self.plists()
        self.host.stuck.add(H.LABEL["hub"])
        self.host.sleep = lambda s: None
        with self.assertRaisesRegex(H.Stop, "卸不下来"):
            H.remove_daemon(self.host, "hub")
        self.assertTrue((d / f"{H.LABEL['hub']}.plist").exists())
        H.remove_daemon(self.host, "intake")                                          # 卸得掉的照常删 plist
        self.assertFalse((d / f"{H.LABEL['intake']}.plist").exists())

    def rollback_blocked(self, w, setup, word, plist_kept):
        """回滚被卡住：门卫不关、plist 和说明文件都在、目录不放回、不启用主账号中台、停写和 state 留着；修好再跑一次接着来。"""
        self.rollback_state(w)
        d = self.plists()
        self.guarded()
        run = self.fake_recovery(hold="gate-migrate-S")
        unmark = mock.Mock(side_effect=lambda *a: (w / "hub").unlink())
        start = mock.Mock()
        self.host.sleep = lambda s: None
        undo_setup = setup()
        with mock.patch.object(H, "recovery_as", run), mock.patch.object(H, "start_owner_agent", start), \
                mock.patch.object(H, "unmark_owner", unmark), mock.patch.object(H, "now_stamp", return_value="P"), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(H.Stop, word):
                H.cmd_rollback(self.host, self.a())
            self.assertFalse([c for c in self.host.calls if c[0] == "pf_apply"])
            self.assertTrue(os.path.lexists(H.hub_mark()))
            self.assertEqual((d / f"{H.LABEL['hub']}.plist").exists(), plist_kept)
            unmark.assert_not_called()
            start.assert_not_called()
            self.assertTrue((w / "hub").read_text().startswith(H.MARK))
            self.assertTrue((Path(os.environ["DT_GATE_ROOT"]) / "state.json").exists())
            self.assertEqual(run.state["hold"], "gate-migrate-S")
            undo_setup()                                                              # 修好了，再跑一次
            self.assertEqual(H.cmd_rollback(self.host, self.a()), 0)
        self.assertFalse(os.path.lexists(H.hub_mark()))
        self.assertFalse((d / f"{H.LABEL['hub']}.plist").exists())
        start.assert_called_once()
        self.assertIsNone(run.state["hold"])

    def test_rollback_stops_when_a_service_will_not_unload(self):
        def setup():
            self.host.stuck.add(H.LABEL["hub"])
            return self.host.stuck.clear
        self.rollback_blocked(self.d / "rbs", setup, "卸不下来", plist_kept=True)

    def test_rollback_stops_while_a_service_port_is_still_listened(self):
        def setup():
            srv = socket.socket()
            srv.bind(("127.0.0.1", 0))
            srv.listen(128)
            self.addCleanup(srv.close)
            os.environ["DT_GATE_INTAKE_PORT"] = str(srv.getsockname()[1])            # setUp 的 patch.dict 会还原
            return srv.close
        self.rollback_blocked(self.d / "rbp", setup, "还有人在听", plist_kept=False)

    def test_wait_port_free_only_trusts_a_refused_connection(self):
        """连上了、超时（监听队列满）都不算空；连接被拒才算。"""
        self.host.sleep = lambda s: None
        H.wait_port_free(self.host, f"127.0.0.1:{free_port()}")
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            with mock.patch.object(H.socket.socket, "connect_ex", return_value=errno.ETIMEDOUT), \
                    self.assertRaisesRegex(H.Stop, "还有人在听"):
                H.wait_port_free(self.host, "127.0.0.1:%d" % srv.getsockname()[1], seconds=1)
            with self.assertRaisesRegex(H.Stop, "还有人在听"):
                H.wait_port_free(self.host, "127.0.0.1:%d" % srv.getsockname()[1], seconds=1)

    def test_undo_with_a_stuck_daemon_keeps_guard_hold_and_progress(self):
        """搬家中途出错、dt-gate 的中台卸不掉：目录不挪、门卫不关、主账号中台不启用、停写保持，返回 False（进度记录由调用方留着）。"""
        (self.users["nat"] / ".agent-autoloop").mkdir(exist_ok=True)
        (self.users["nat"] / ".agent-autoloop/config.json").write_text("{}")
        d = self.plists()
        self.guarded()
        gate_hub = self.users["dt-gate"] / ".agent-autoloop"
        gate_hub.mkdir()
        self.host.stuck.add(H.LABEL["hub"])
        self.host.sleep = lambda s: None
        log = []
        run = self.fake_recovery(hold="gate-migrate-S", log=log)
        start = mock.Mock()
        done = {"held": True, "stopped": True, "gate_hub": True, "daemon": True, "guard_on": True}
        with mock.patch.object(H, "recovery_as", run), mock.patch.object(H, "start_owner_agent", start), \
                mock.patch("sys.stdout", io.StringIO()):
            self.assertFalse(H.undo(self.host, {"repo": {"dir": str(self.d)}}, "nat", "S", done))
        start.assert_not_called()
        self.assertTrue(gate_hub.is_dir())
        self.assertTrue((d / f"{H.LABEL['hub']}.plist").exists())
        self.assertTrue(os.path.lexists(H.hub_mark()))
        self.assertFalse([c for c in self.host.calls if c[0] == "pf_apply"])
        self.assertEqual(run.state["hold"], "gate-migrate-S")
        self.assertNotIn("release", [c[1] for c in log])

    def test_migrate_stops_before_backup_when_owner_hub_will_not_unload(self):
        (self.users["nat"] / ".agent-autoloop").mkdir(exist_ok=True)
        (self.users["nat"] / ".agent-autoloop/config.json").write_text("{}")
        run = self.fake_recovery()
        cli = mock.Mock()
        with mock.patch.object(H, "recovery_as", run), mock.patch.object(H, "snapshot_as", return_value=self.snap(max_card=5)), \
                mock.patch.object(H, "unload", return_value=False), mock.patch.object(H, "run_cli", cli), \
                mock.patch.object(H, "wait_port_free") as wpf, mock.patch.object(H, "start_owner_agent"), \
                mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(H.Stop, "主账号中台卸不下来"):
                H.cmd_migrate(self.host, self.a())
        cli.assert_not_called()
        wpf.assert_not_called()
        self.assertIsNone(run.state["hold"])                                           # 什么都没动：退回、结束停写
        self.assertTrue(list(H.gate_root().glob("migrating.undone-*.json")))

    def test_guard_off_checks_the_directory_before_deleting_the_mark(self):
        """L3 第 2 轮 P2-01：门卫目录不是只有 root 能改的，标记一个字节都不动、不跑门卫，返回 False。"""
        H.hub_mark().write_text("dt-gate S\n")
        self.host.guard_fail.add(("root_path", os.environ["DT_PF_DIR"]))
        with mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertFalse(H.guard_off(self.host))
        self.assertEqual(H.hub_mark().read_text(), "dt-gate S\n")
        self.assertFalse([c for c in self.host.calls if c[0] == "pf_apply"])
        self.assertIn("只有 root 能改", out.getvalue())

    def test_rollback_refuses_before_touching_anything_when_note_is_not_ours(self):
        w = self.d / "rbn"
        self.rollback_state(w)
        (w / "hub").write_text("别人的东西\n")
        remove = mock.Mock()
        with mock.patch.object(H, "remove_daemon", remove), mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaisesRegex(H.Stop, "不是搬家时放的说明文件"):
                H.cmd_rollback(self.host, self.a())
        remove.assert_not_called()
        self.assertFalse([c for c in self.host.calls if c[0] == "pf_apply"])
        self.assertEqual((w / "hub").read_text(), "别人的东西\n")

    def test_rotate_worker_needs_guard_loaded_not_just_the_rule_line(self):
        pfd = Path(os.environ["DT_PF_DIR"])
        (pfd / "pf-dt-worker.rules").write_text("block return in quick proto tcp from any to 127.0.0.1 port 3350 user != dt-gate\n")
        self.pf("at=x state=ok enabled=yes boot=1795000000 guard=no\n")
        with self.assertRaisesRegex(H.Stop, "没在生效"):
            H.pf_listener_rule(self.host)
        self.pf("at=x state=ok enabled=yes boot=1795000000 guard=yes\n")
        H.pf_listener_rule(self.host)

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
               "DT_GATE_USERS": d / "Users", "DT_GATE_ANY_COPY": "1", "DT_PF_DIR": d / "pf",
               "DT_GATE_LAUNCHERS": d / "launchers"}
        (d / "Library/DigitalTwin").mkdir(parents=True)
        pf_fixture(d / "pf")
        q = mock.patch.object(H, "ROOT_UID", os.getuid())
        q.start()
        self.addCleanup(q.stop)
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

    def owner_hold(self):
        """主账号真实出口账本（恢复闸）上的停写恢复点；账本还没建就是 None。"""
        f = self.users["nat"] / "Library/Application Support/DigitalTwin/autoloop/exits.db"
        if not f.exists():
            return None
        db = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        try:
            r = db.execute("SELECT hold FROM gate WHERE id = 1").fetchone()
            return r[0] if r else None
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
        self.assertIsNone(self.owner_hold())                                      # 退回时结束了这次停写

        # 第 4b 步打不开门卫监听者规则：整条退回，先关门卫再启用主账号中台、结束停写
        pf = self.d / "pf"
        self.host.pf_fail = H.PF_APPLY_TRIES
        self.assertEqual(self.main("migrate", "--confirm"), 1)
        self.assertIn("没切到 guard=yes", self.out.getvalue())
        self.wait_healthy()
        self.assertFalse(os.path.lexists(pf / "hub-in-gate"))
        self.assertIn("guard=no", (pf / "pf-dt-worker.status").read_text())
        self.assertTrue((self.ohub / "config.json").exists() and self.oplist.exists())
        self.assertFalse((gate / ".agent-autoloop").exists())
        self.assertEqual(len(list(gate.glob(".agent-autoloop.failed-*"))), 2)
        self.assertNotIn("cn.digital-twin.gate.hub", self.host.procs)
        self.assertIsNone(H.load_state())
        self.assertIsNone(self.owner_hold())
        self.assertFalse((H.gate_root() / "migrating.json").exists())

        # 被打断（进程被杀、没来得及退回）：留下 migrating.json，preflight 不过；rollback 照记录收拾
        with mock.patch.object(H, "wait_health", side_effect=H.Stop("模拟被杀")), mock.patch.object(H, "undo"), \
                mock.patch.object(H.Progress, "close"):
            self.assertEqual(self.main("migrate", "--confirm"), 1)
        self.assertTrue((H.gate_root() / "migrating.json").exists())
        self.assertTrue(self.owner_hold().startswith("gate-migrate-"))              # 没退回：停写还在
        self.assertEqual(self.main("preflight"), 1)
        self.assertEqual(self.main("rollback", "--confirm"), 0, self.out.getvalue())
        self.assertIn("收拾好了", self.out.getvalue())
        self.assertIsNone(self.owner_hold())
        self.wait_healthy()
        self.assertTrue((self.ohub / "config.json").exists())
        self.assertFalse((gate / ".agent-autoloop").exists())
        self.assertNotIn("cn.digital-twin.gate.hub", self.host.procs)
        self.assertFalse((H.daemons() / "cn.digital-twin.gate.hub.plist").exists())

        # 正式搬
        self.assertEqual(self.main("migrate", "--confirm"), 0, self.out.getvalue())
        self.wait_healthy(commit)
        state = H.load_state()
        self.assertTrue(state["owner_hold"].startswith("gate-migrate-"))
        self.assertEqual(self.owner_hold(), state["owner_hold"])                  # 搬完主账号控制面保持停写
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
        self.assertTrue((pf / "hub-in-gate").exists())                              # 搬家第 4b 步打开了监听者规则
        self.assertIn("guard=yes", (pf / "pf-dt-worker.status").read_text())
        (pf / "pf-dt-worker.rules").write_text("pass out quick inet proto tcp from any to 127.0.0.1 port { 3350 } user dt-worker\n")
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
        self.assertFalse(os.path.lexists(self.d / "pf/hub-in-gate"))                 # 先关门卫监听者规则再放回
        self.assertIn("guard=no", (self.d / "pf/pf-dt-worker.status").read_text())
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
        self.assertIsNone(self.owner_hold())                                      # 回滚结束了主账号停写
        self.assertEqual(self.main("preflight"), 0, self.out.getvalue())   # 回滚后可以重新搬


if __name__ == "__main__":
    unittest.main()
