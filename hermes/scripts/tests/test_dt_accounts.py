"""控制面 / 秘书账号初始化（ADR-015 批 0E-1）：门卫规则生成、探针输出、初始化脚本的护栏。

不用 sudo、不改 pf、不装 LaunchDaemon：门卫规则只测 `rules` 子命令打印出的内容；
探针用环境变量把路径指到临时目录，以当前账号跑一次；初始化脚本只测非 root 时拒绝。
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).parents[3] / "ops"
PF = OPS / "dt_worker_pf.sh"
PROBE = OPS / "dt_account_probe.sh"
SETUP = OPS / "dt_accounts_setup.sh"


def rules(*users):
    return subprocess.run(["bash", str(PF), "rules", *users], capture_output=True, text=True)


def allowed_ports(text, user):
    m = re.search(r"pass out quick inet proto tcp from any to 127\.0\.0\.1 port \{ ([0-9, ]+) \} user " + re.escape(user) + r"$",
                  text, re.M)
    return {int(p) for p in m.group(1).split(",")} if m else None


class PfRules(unittest.TestCase):
    def test_worker_only_unchanged(self):
        r = rules("dt-worker")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(allowed_ports(r.stdout, "dt-worker"), {3350, 8090})
        self.assertNotIn("dt-secretary", r.stdout)
        self.assertEqual(r.stdout.count("user dt-worker"), 3)

    def test_secretary_cannot_reach_hub(self):
        r = rules("dt-worker", "dt-secretary")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(allowed_ports(r.stdout, "dt-worker"), {3350, 8090})
        self.assertEqual(allowed_ports(r.stdout, "dt-secretary"), {3352, 3353, 8090})
        # 两个账号都要有本机 / 局域网的兜底拒绝，且拒绝排在放行之后（quick 按顺序生效）
        for u in ("dt-worker", "dt-secretary"):
            lines = [l for l in r.stdout.splitlines() if l.endswith(f"user {u}")]
            self.assertEqual(len(lines), 3)
            self.assertTrue(lines[0].startswith("pass out quick"))
            self.assertIn("to <dt_local> user", lines[1])
            self.assertIn("to <dt_local6> user", lines[2])
            self.assertTrue(all(l.startswith("block return") for l in lines[1:]))

    def test_gate_and_unknown_refused(self):
        """控制面不在这里限制；不认识的账号不生成规则、退出码非 0。"""
        for u in ("dt-gate", "nat", "dt-worker2"):
            r = rules("dt-worker", u)
            self.assertNotEqual(r.returncode, 0, u)
            self.assertIn("不认识的账号", r.stderr)

    def test_install_needs_root(self):
        if os.geteuid() == 0:
            self.skipTest("以 root 运行")
        for cmd in ("install", "apply", "status", "uninstall"):
            r = subprocess.run(["bash", str(PF), cmd], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("sudo", r.stdout)


@unittest.skipUnless(sys.platform == "darwin", "探针用 macOS 的 stat -f、dscl、security，只在 Mac 上测")
class Probe(unittest.TestCase):
    def run_probe(self, secret_text=None):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        out, secret = Path(d.name) / "r.txt", Path(d.name) / "s"
        if secret_text is not None:
            secret.write_text(secret_text)
            secret.chmod(0o600)
        env = dict(os.environ, DT_PROBE_OUT=str(out), DT_PROBE_SECRET=str(secret),
                   DT_PROBE_KEYCHAIN=str(Path(d.name) / "nokc"),
                   PATH=f"{self.fakebin()}:{os.environ['PATH']}")
        r = subprocess.run(["bash", str(PROBE)], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.stat().st_mode & 0o777, 0o644)
        text = out.read_text()
        return text, dict(l.split("=", 1) for l in text.splitlines())

    def fakebin(self):
        """curl 打桩：不联网，按目标给固定 HTTP 码。"""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "curl"
        p.write_text('#!/bin/bash\ncase "$*" in *8086*) printf 000;; *) printf 200;; esac\n')
        p.chmod(0o755)
        return d.name

    def test_reads_secret_without_leaking_it(self):
        text, f = self.run_probe("TOPSECRET-0123456789abcdef")
        self.assertEqual(f["secret_read"], "yes")
        self.assertEqual(f["secret_mode"], "600")
        self.assertNotIn("TOPSECRET", text)
        self.assertEqual(f["keychain_exists"], "no")
        self.assertNotEqual(f["keychain_info_rc"], "0")
        self.assertTrue(f["boot"].isdigit(), f["boot"])
        self.assertEqual(f["net_github_via_8090"], "200")
        self.assertEqual(f["net_github_via_8086"], "000")
        for key in ("at", "user", "uid", "real_home", "console_user", "self_logged_in", "home_mode",
                    "keychain_find_rc", "env_https_proxy", "sys_proxy", "net_feishu_direct", "net_github_direct"):
            self.assertIn(key, f)

    def test_missing_secret(self):
        _, f = self.run_probe(None)
        self.assertEqual(f["secret_read"], "no")

    def test_one_line_per_key(self):
        """多行的错误信息压成一行，结果文件每行都是 key=value。"""
        text, _ = self.run_probe("x")
        for line in text.splitlines():
            self.assertRegex(line, r"^[a-z0-9_]+=")


class Setup(unittest.TestCase):
    def test_syntax(self):
        for p in (PF, PROBE, SETUP, OPS / "dt_account_init_user.sh"):
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{p.name}: {r.stderr}")

    def test_needs_root(self):
        if os.geteuid() == 0:
            self.skipTest("以 root 运行")
        for cmd in ("install", "status", "remove-probe"):
            r = subprocess.run(["bash", str(SETUP), cmd], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("sudo", r.stdout)

    def test_probe_daemon_runs_as_account_not_root(self):
        """探针服务必须以账号身份跑（UserName），不能以 root 跑出一个「读得到」的假结论。"""
        s = SETUP.read_text()
        self.assertIn("<key>UserName</key><string>$u</string>", s)
        self.assertIn('ACCOUNTS="dt-gate dt-secretary"', s)
        self.assertNotIn("dt-worker", re.sub(r"#.*", "", s))


def code(path):
    """去掉注释，只留代码行。"""
    return "\n".join(l for l in (re.sub(r"(^|\s)#.*", "", x) for x in path.read_text().splitlines()) if l.strip())


class RootNeverTouchesAccountFiles(unittest.TestCase):
    """L3 P1-01：root 不按路径碰账号能改的任何文件。账号目录里的写入一律以账号身份做（sudo -u），
    这样账号同时偷换链接、硬链接、目录，能改坏的也只有它本来就能改的东西。"""

    def test_no_root_path_under_account_home(self):
        s = code(SETUP)
        self.assertNotIn('$home/', s)
        self.assertNotIn('${home}/', s)
        # 家目录本身只做核对、chown、chmod、看权限；它的上一级必须是只有 root 能改的目录
        for line in s.splitlines():
            if '"$home"' in line:
                self.assertRegex(line.strip(), r'^(case|root_dir|\[ -d|\[ -n \"\$home\" \] && echo|chown "\$u:staff" "\$home"|chmod 700 "\$home"|echo|sudo -u "\$u")', line)
        self.assertIn('root_dir "$(dirname "$home")"', s)
        self.assertIn('sudo -u "$u" -H /bin/bash -s -- "$home" "$MARK" < "$INIT_USER"', s)

    def test_result_file_read_as_account(self):
        """探针结果在账号自己的目录里：root 只看 mtime，内容以账号身份读。"""
        s = code(SETUP)
        for line in s.splitlines():
            if "result.txt" in line or '"$res"' in line:
                self.assertRegex(line, r'stat -f %m "\$res"|sudo -u "\$1" /bin/cat "\$res"|res="\$SHARED', line)

    def test_shared_dirs_checked_before_use(self):
        s = code(SETUP)
        self.assertIn('mkdir "$SHARED"', s)
        self.assertLess(s.index('root_dir "$SHARED"'), s.index('chmod 755 "$SHARED"'))
        self.assertLess(s.index('root_dir "$SHARED/probe"'), s.index('install -d -o "$u" -g staff -m 755 "$SHARED/probe/$u"'))

    def root_dir(self, path):
        fn = re.search(r"^root_dir\(\) \{\n.*?^\}", SETUP.read_text(), re.M | re.S).group(0)
        return subprocess.run(["bash", "-c", f"{fn}\nroot_dir \"$1\"", "x", str(path)]).returncode == 0

    @unittest.skipUnless(sys.platform == "darwin", "root_dir 用 macOS 的 stat -f，只在 Mac 上测")
    def test_root_dir(self):
        self.assertTrue(self.root_dir("/"))            # root 的、别人不能写
        self.assertFalse(self.root_dir("/tmp"))        # 谁都能写（粘滞位也不算）
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        if os.geteuid() != 0:
            self.assertFalse(self.root_dir(d.name))    # 不是 root 的目录
        link = Path(d.name) / "l"
        link.symlink_to("/")
        self.assertFalse(self.root_dir(link))          # 指向 root 目录的链接也不算


class InitAsAccount(unittest.TestCase):
    """dt_account_init_user.sh：以账号身份经标准输入跑（和 setup 里一样），可重复跑。"""
    MARK = "# umask（测试标记）"

    def run_init(self, home):
        with open(OPS / "dt_account_init_user.sh") as f:
            return subprocess.run(["bash", "-s", "--", str(home), self.MARK], stdin=f,
                                  capture_output=True, text=True, cwd="/")

    def test_fresh_and_rerun(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        home = Path(d.name)
        r = self.run_init(home)
        self.assertEqual(r.returncode, 0, r.stderr)
        secret = home / ".dt/probe.secret"
        self.assertEqual((home / ".dt").stat().st_mode & 0o777, 0o700)
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)
        self.assertRegex(secret.read_text(), r"^[0-9a-f]{32}\n$")
        first = secret.read_text()
        r = self.run_init(home)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(secret.read_text(), first)                     # 已有就不换
        self.assertEqual((home / ".zprofile").read_text().count(self.MARK), 1)
        self.assertIn("umask 077", (home / ".zprofile").read_text())

    def test_keeps_existing_zprofile(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        home = Path(d.name)
        (home / ".zprofile").write_text("export A=1\n")
        self.assertEqual(self.run_init(home).returncode, 0)
        self.assertTrue((home / ".zprofile").read_text().startswith("export A=1\n"))


if __name__ == "__main__":
    unittest.main()
