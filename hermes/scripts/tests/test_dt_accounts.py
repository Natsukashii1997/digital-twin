"""控制面 / 秘书账号初始化（ADR-015 批 0E-1）：门卫规则生成、探针输出、初始化脚本的护栏。

不用 sudo、不改 pf、不装 LaunchDaemon：门卫规则只测 `rules` 子命令打印出的内容；
探针用环境变量把路径指到临时目录，以当前账号跑一次；初始化脚本只测非 root 时拒绝。
"""
import os
import re
import subprocess
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
        for p in (PF, PROBE, SETUP):
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


if __name__ == "__main__":
    unittest.main()
