"""控制面 / 秘书账号初始化（ADR-015 批 0E-1）：门卫规则生成与加载核对、探针输出、初始化脚本的护栏、真机越权测试的判定。

不用 sudo、不改 pf、不装 LaunchDaemon：门卫规则测 `rules` 子命令打印出的内容，apply 换上假 pfctl 做故障注入；
探针用环境变量把路径指到临时目录，以当前账号跑一次；root 脚本里的函数抽出来、把系统命令换成桩再测。
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import contextlib
import importlib.util
import io
import errno
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

OPS = Path(__file__).parents[3] / "ops"
PF = OPS / "dt_worker_pf.sh"
PROBE = OPS / "dt_account_probe.sh"
SETUP = OPS / "dt_accounts_setup.sh"
VERIFY = OPS / "dt_pf_verify.py"
GUARD = OPS / "dt_root_guard.sh"
HAVE_ACCOUNTS = all(subprocess.run(["id", u], capture_output=True).returncode == 0 for u in ("dt-worker", "dt-secretary"))


def func(path, name):
    """从 shell 脚本里抽出一个函数的定义。"""
    return re.search(rf"^{name}\(\) \{{\n.*?^\}}", path.read_text(), re.M | re.S).group(0)


def rules(*users):
    return subprocess.run(["bash", str(PF), "rules", *users], capture_output=True, text=True)


def allowed_ports(text, user):
    m = re.search(r"pass out quick inet proto tcp from any to 127\.0\.0\.1 port \{ ([0-9, ]+) \} user " + re.escape(user) + r"$",
                  text, re.M)
    return {int(p) for p in m.group(1).split(",")} if m else None


class PfRules(unittest.TestCase):
    def assert_whitelist(self, text, user):
        """白名单、默认拒绝（L3 P1-02）：先放行准连的本机端口，紧接着一条不分地址族、不分目标的 TCP / UDP 全拒。"""
        lines = [l for l in text.splitlines() if l.endswith(f"user {user}")]
        self.assertEqual(len(lines), 3, lines)
        self.assertTrue(lines[0].startswith("pass out quick inet proto tcp from any to 127.0.0.1 port {"))
        self.assertEqual(lines[1], f"block return out quick proto {{ tcp, udp }} from any to any user {user}")
        # 审查第 4 轮 P2-8：入站一律拒绝，账号不能占住别人准连的端口冒充服务
        self.assertEqual(lines[2], f"block return in quick proto {{ tcp, udp }} from any to any user {user}")

    def test_worker_only(self):
        r = rules("dt-worker")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(allowed_ports(r.stdout, "dt-worker"), {3350, 8090})
        self.assertNotIn("dt-secretary", r.stdout)
        self.assert_whitelist(r.stdout, "dt-worker")

    def test_secretary_cannot_reach_hub(self):
        r = rules("dt-worker", "dt-secretary")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(allowed_ports(r.stdout, "dt-worker"), {3350, 8090})
        self.assertEqual(allowed_ports(r.stdout, "dt-secretary"), {3352, 3353, 8090})
        for u in ("dt-worker", "dt-secretary"):
            self.assert_whitelist(r.stdout, u)
        self.assertNotIn("table", r.stdout)   # 不再靠一张「本机地址表」列举，公网直连也挡

    @unittest.skipUnless(sys.platform == "darwin" and HAVE_ACCOUNTS, "要 macOS 的 pfctl 和本机的两个账号")
    def test_pfctl_expands_both_families(self):
        """pfctl 解析后：IPv4、IPv6 的 TCP、UDP 全拒，放行只到 127.0.0.1 的指定端口。"""
        with tempfile.NamedTemporaryFile("w", suffix=".conf") as f:
            f.write(rules("dt-worker", "dt-secretary").stdout)
            f.flush()
            out = subprocess.run(["/sbin/pfctl", "-nvf", f.name], capture_output=True, text=True).stdout
        for uid in (subprocess.check_output(["id", "-u", u], text=True).strip() for u in ("dt-worker", "dt-secretary")):
            self.assertIn(f"block return out quick proto tcp all user = {uid}", out)
            self.assertIn(f"block return out quick proto udp all user = {uid}", out)
            self.assertIn(f"block return in quick proto tcp all user = {uid}", out)
            self.assertIn(f"block return in quick proto udp all user = {uid}", out)
        self.assertNotIn("inet6", out)

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
        self.last_out = str(out)
        if secret_text is not None:
            secret.write_text(secret_text)
            secret.chmod(0o600)
        env = dict(os.environ, DT_PROBE_OUT=str(out), DT_PROBE_SECRET=str(secret), DT_PROBE_NONCE="n0nce",
                   HTTPS_PROXY="http://user:pass@127.0.0.1:8090",
                   PATH=f"{self.fakebin()}:{os.environ['PATH']}")
        # 和 LaunchDaemon 一样在 umask 077 下跑（plist 里 Umask 63）
        r = subprocess.run(["bash", "-c", 'umask 077; exec bash "$0"', str(PROBE)], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.stat().st_mode & 0o777, 0o644)
        text = out.read_text()
        return text, dict(l.split("=", 1) for l in text.splitlines())

    def fakebin(self):
        """curl 打桩：不联网，按目标给固定 HTTP 码。"""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "curl"
        p.write_text('#!/bin/bash\ncase "$*" in *8086*) printf 000; exit 7;; *) printf 200;; esac\n')
        p.chmod(0o755)
        return d.name

    def test_reads_secret_without_leaking_it(self):
        import hashlib
        text, f = self.run_probe("TOPSECRET-0123456789abcdef")
        self.assertEqual(f["secret_read"], "yes")
        self.assertEqual(f["secret_mode"], "600")
        self.assertEqual(f["secret_sha256"], hashlib.sha256(b"TOPSECRET-0123456789abcdef").hexdigest())
        self.assertNotIn("TOPSECRET", text)
        self.assertNotIn("keychain", text)                       # 凭据存文件（常浩 11-19 定），不测钥匙串
        self.assertTrue(f["boot"].isdigit(), f["boot"])
        self.assertEqual(f["net_github_via_8090"], "200/0")      # HTTP 码/curl 退出码（L3 P2-03）
        self.assertEqual(f["net_github_via_8086"], "000/7")
        self.assertEqual(f["nonce"], "n0nce")                    # 带本次编号，root 据此认这次的结果
        self.assertEqual(text.splitlines()[0], "nonce=n0nce")
        self.assertEqual(text.splitlines()[-1], "done=1")        # 写完整了才有最后一行
        self.assertEqual(f["env_https_proxy"], "http://***@127.0.0.1:8090")   # 代理里的用户名密码不落进所有人可读的文件
        self.assertNotIn("pass@", text)
        boots = list(Path(self.last_out).parent.glob("boot-*.txt"))
        self.assertEqual(len(boots), 1)                                   # 开机后第一份另存
        self.assertEqual(boots[0].stat().st_mode & 0o777, 0o644)          # 主账号读得到（11-20 实测是 600，读不到）
        for key in ("at", "user", "uid", "gid", "real_home", "home_match", "uptime_s", "env_path", "console_user", "self_logged_in",
                    "home_mode", "sys_proxy", "net_feishu_direct", "net_github_direct"):
            self.assertIn(key, f)

    def test_home_from_service_config_not_directory_service(self):
        """11-20 冷启动：目录服务没就绪时 dscl 读不出家目录；凭据按服务配置里的 HOME 找，照样读得到。"""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        home = Path(d.name) / "home"
        (home / ".dt").mkdir(parents=True)
        (home / ".dt" / "probe.secret").write_text("x")
        bin_ = Path(d.name) / "bin"
        bin_.mkdir()
        (bin_ / "dscl").write_text("#!/bin/bash\necho 'DS Error: -14009'; exit 1\n")
        (bin_ / "sleep").write_text("#!/bin/bash\n")    # 不真等 60 秒
        for f in bin_.iterdir():
            f.chmod(0o755)
        env = dict(os.environ, HOME=str(home), DT_PROBE_OUT=str(Path(d.name) / "r.txt"),
                   PATH=f"{bin_}:{self.fakebin()}:{os.environ['PATH']}")
        r = subprocess.run(["bash", str(PROBE)], capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        f = dict(l.split("=", 1) for l in (Path(d.name) / "r.txt").read_text().splitlines())
        self.assertEqual(f["secret_read"], "yes")
        self.assertEqual(f["real_home"], "")
        self.assertEqual(f["home_match"], "no")

    def test_existing_boot_copies_fixed_to_644(self):
        """L3 第 3 轮 P2-07：已经存在的开机副本（旧版在 umask 077 下建成 600）每次都改回 644，主账号读得到。"""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        boot = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
        boot = re.match(r"\{ sec = (\d+),", boot).group(1)
        old, cur = Path(d.name) / "boot-1.txt", Path(d.name) / f"boot-{boot}.txt"
        for f in (old, cur):
            f.write_text("x")
            f.chmod(0o600)
        env = dict(os.environ, DT_PROBE_OUT=str(Path(d.name) / "result.txt"), DT_PROBE_SECRET=str(Path(d.name) / "s"),
                   PATH=f"{self.fakebin()}:{os.environ['PATH']}")
        r = subprocess.run(["bash", "-c", 'umask 077; exec bash "$0"', str(PROBE)], capture_output=True, text=True,
                           env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        for f in (old, cur):
            self.assertEqual(f.stat().st_mode & 0o777, 0o644, f.name)
        self.assertEqual(cur.read_text(), "x")          # 已有的开机副本不覆盖

    def test_missing_secret(self):
        _, f = self.run_probe(None)
        self.assertEqual(f["secret_read"], "no")
        self.assertNotIn("secret_sha256", f)

    def test_one_line_per_key(self):
        """多行的错误信息压成一行，结果文件每行都是 key=value。"""
        text, _ = self.run_probe("x")
        for line in text.splitlines():
            self.assertRegex(line, r"^[a-z0-9_]+=")


class Setup(unittest.TestCase):
    def test_syntax(self):
        for p in (PF, PROBE, SETUP, GUARD, OPS / "dt_account_init_user.sh"):
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{p.name}: {r.stderr}")

    def test_no_bare_var_before_chinese(self):
        """macOS 自带的 bash 3.2 在没设 UTF-8 的环境（sudo、LaunchDaemon）里，会把「$u：」的中文首字节当成变量名的一部分吞掉，
        中文前的变量一律写成 ${u}。"""
        for p in (PF, PROBE, SETUP, GUARD, OPS / "dt_account_init_user.sh"):
            bad = [l for l in p.read_text().splitlines() if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*[^\x00-\x7f]", l)]
            self.assertEqual(bad, [], p.name)

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
                self.assertRegex(line.strip(), r'^(\[ "\$home" = "/Users/\$u" \]|\[ -d|\[ -n \"\$home\" \] && echo|chown "\$u:staff" "\$home"|chmod 700 "\$home"|echo|out=\$\(sudo -u "\$u")', line)
        self.assertIn('root_path /Users', s)
        self.assertIn('out=$(sudo -u "$u" -H /bin/bash -s -- "$home" "$MARK" < "$INIT_USER")', s)

    def test_result_file_read_as_account(self):
        """探针结果在账号自己的目录里：内容只以账号身份读，root 不按路径碰它。"""
        s = code(SETUP)
        lines = [l for l in s.splitlines() if "result.txt" in l]
        self.assertEqual(lines, ['read_result() { sudo -u "$1" /bin/cat "$SHARED/probe/$1/result.txt" 2>/dev/null; }'])

    def test_shared_dirs_checked_before_use(self):
        s = code(SETUP)
        self.assertIn('mkdir "$SHARED"', s)
        self.assertLess(s.index('sticky_dir /Users/Shared'), s.index('mkdir "$SHARED"'))
        self.assertLess(s.index('root_dir "$DIR"'), s.index('harden "$u"'))
        self.assertLess(s.index('root_dir "$SHARED"'), s.index('chmod 755 "$SHARED"'))
        self.assertLess(s.index('root_dir "$SHARED/probe"'), s.index('install -d -o "$u" -g staff -m 755 "$SHARED/probe/$u"'))

    def root_dir(self, path, acl=None, name="root_dir", ls_fails=False):
        """acl 给了就把 ls -led 换成桩，假装目录上有这条 ACL；ls_fails 假装读不出 ACL。"""
        stub = f"ls() {{ command ls \"$@\"; echo ' 0: {acl}'; }}\n" if acl else ""
        if ls_fails:
            stub = "ls() { return 1; }\n"
        return subprocess.run(["bash", "-c", f". {GUARD}\n{stub}{name} \"$1\"", "x", str(path)]).returncode == 0

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

    @unittest.skipUnless(sys.platform == "darwin", "ACL、粘滞位按 macOS 的 ls -le、stat -f 测")
    def test_root_dir_rejects_allow_acl(self):
        """L3 P2-05：权限位看着只有 root 能写，但 ACL 单独放行了别的账号，也不算。"""
        self.assertFalse(self.root_dir("/", acl="user:dt-gate allow add_file,add_subdirectory"))
        self.assertFalse(self.root_dir("/", acl="user:dt-gate allow delete_child"))
        self.assertTrue(self.root_dir("/", acl="group:everyone deny delete"))   # 拒绝类 ACL 只会收紧

    @unittest.skipUnless(sys.platform == "darwin", "ACL 按 macOS 的 ls -le 测")
    def test_acl_unreadable_rejected(self):
        """L3 第 3 轮 P2-05：ACL 读不出来不当成没有。"""
        self.assertFalse(self.root_dir("/", ls_fails=True))

    @unittest.skipUnless(sys.platform == "darwin", "按 macOS 的 stat -f 测")
    def test_root_path_checks_every_level(self):
        """L3 第 3 轮 P2-05：从 / 起每一级都要只有 root 能改；不认相对路径、. 和 ..。"""
        for good in ("/", "/Library", "/Library/Application Support", "/Library/LaunchDaemons", "/Users"):
            self.assertTrue(self.root_dir(good, name="root_path"), good)
        for bad in ("/private/tmp/x", "/tmp", "Library", "/Library/../Library", "/Library/./LaunchDaemons"):
            self.assertFalse(self.root_dir(bad, name="root_path"), bad)
        d = tempfile.mkdtemp(dir="/private/tmp")                 # 上一级谁都能写：下面再干净也不算
        self.addCleanup(os.rmdir, d)
        self.assertFalse(self.root_dir(d, name="root_path"))
        # 某一级上有放行 ACL（桩对每一级都生效）：不算
        self.assertFalse(self.root_dir("/Library/LaunchDaemons", acl="user:dt-gate allow add_file", name="root_path"))

    @unittest.skipUnless(sys.platform == "darwin", "按 macOS 的 stat -f 测")
    def test_sticky_dir(self):
        self.assertTrue(self.root_dir("/private/tmp", name="sticky_dir"))   # root 的、带粘滞位、上一级只有 root 能改
        self.assertFalse(self.root_dir("/tmp", name="sticky_dir"))          # 是链接
        self.assertFalse(self.root_dir("/", name="sticky_dir"))             # 没有粘滞位
        self.assertFalse(self.root_dir("/private/tmp", acl="user:dt-gate allow delete_child", name="sticky_dir"))


class HomePath(unittest.TestCase):
    """L3 P1-01：家目录只认正好等于 /Users/<账号>，不认 ..、多一层、结尾斜杠；不过就不 chown / chmod。"""

    def harden(self, home):
        stubs = (f'dscl() {{ echo "NFSHomeDirectory: {home}"; }}\n'
                 'root_dir() { return 0; }\nroot_path() { return 0; }\nuid_ok() { return 0; }\nno_link() { return 0; }\n'
                 'chown() { echo "CHOWN $*"; }\nchmod() { echo "CHMOD $*"; }\nsudo() { echo "SUDO"; }\n')
        return subprocess.run(["bash", "-c", stubs + func(SETUP, "harden") + "\nharden dt-gate"],
                              capture_output=True, text=True)

    def test_rejects_non_canonical(self):
        for home in ("/Users/../Library", "/Users/dt-gate/", "/Users/dt-gate/x", "/Users/./dt-gate",
                     "/Users/dt-gate/..", "/Users/other", "/Library", "/var/dt-gate", ""):
            r = self.harden(home)
            self.assertNotEqual(r.returncode, 0, home)
            self.assertNotIn("CHOWN", r.stdout, home)
            self.assertNotIn("CHMOD", r.stdout, home)

    def test_uid_rechecked_before_touching(self):
        """L3 第 3 轮 P1-01：紧挨着 chown、sudo -u 之前再核一次 uid，不过就不动。"""
        stubs = ('dscl() { echo "NFSHomeDirectory: /Users/dt-gate"; }\nuid_ok() { echo UIDCHECK; return 1; }\n'
                 'no_link() { return 0; }\n'
                 'root_path() { return 0; }\nchown() { echo CHOWN; }\nchmod() { echo CHMOD; }\nsudo() { echo SUDO; }\n')
        r = subprocess.run(["bash", "-c", stubs + func(SETUP, "harden") + "\nharden dt-gate"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("UIDCHECK", r.stdout)
        for word in ("CHOWN", "CHMOD", "SUDO"):
            self.assertNotIn(word, r.stdout)


class AccountGuards(unittest.TestCase):
    """L3 P2-02（第 3 轮）：账号核对里任何一项查不清（目录服务、dseditgroup、ps 出错）都当成「不过」；远程管理开着时要核权限。"""

    MEMBER = {True: 'echo "yes $3 is a member of $5"; return 0', False: 'echo "no $3 is NOT a member of $5"; return 67',
              None: 'echo "Group not found."; return 64'}

    def bash(self, stubs, body):
        return subprocess.run(["bash", "-c", f". {GUARD}\n{stubs}\n{body}"], capture_output=True, text=True)

    def remote(self, on, group, member):
        stubs = (f'launchctl() {{ return {0 if on is True else 113 if on is False else on}; }}\n'
                 f'dscl() {{ return {0 if group else 1}; }}\n'
                 f'dseditgroup() {{ {self.MEMBER[member]}; }}\n')
        return self.bash(stubs, func(SETUP, "remote_access") +
                         '\nremote_access dt-gate com.openssh.sshd com.apple.access_ssh 远程登录')

    def test_remote_access(self):
        self.assertEqual(self.remote(False, False, False).returncode, 0)   # 没开
        self.assertEqual(self.remote(True, True, False).returncode, 0)     # 开着，不在名单里
        r = self.remote(True, False, False)                                # 开着，所有人都能登
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[停下]", r.stdout)
        self.assertNotEqual(self.remote(True, True, True).returncode, 0)   # 开着，在名单里
        self.assertNotEqual(self.remote(True, True, None).returncode, 0)   # 开着，查不清
        r = self.remote(5, True, False)                                    # 审查第 4 轮 P3-1：launchctl 别的出错不当成没开
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("查不清远程登录开没开", r.stdout)

    def test_primary_group_and_wheel(self):
        """审查第 4 轮 P3-2：主组是 wheel、在 wheel 组里、主组读不出，都停下。"""
        base = ('id() { return 0; }\nuid_ok() { return 0; }\nfdesetup() { :; }\n'
                'sudo() { echo "User $3 is not allowed to run sudo"; }\n'
                'defaults() { return 1; }\nremote_access() { return 0; }\nard_access() { return 0; }\n')
        def run(pg, wheel):
            dscl = f'dscl() {{ case "$4" in PrimaryGroupID) {pg} ;; *) return 1 ;; esac; }}\n'
            dse = ('dseditgroup() { if [ "$5" = wheel ]; then ' + self.MEMBER[wheel] + '; else '
                   + self.MEMBER[False] + '; fi; }\n')
            return self.bash(base + dscl + dse, func(SETUP, "check_account") + "\ncheck_account dt-gate")
        self.assertEqual(run('echo "PrimaryGroupID: 20"', False).returncode, 0)
        for pg, wheel, words in (('echo "PrimaryGroupID: 0"', False, "主组是 wheel"),
                                 ('return 1', False, "读不出 dt-gate 的主组"),
                                 ('echo "PrimaryGroupID: 20"', True, "wheel 组里"),
                                 ('echo "PrimaryGroupID: 20"', None, "wheel 组里或查不清")):
            r = run(pg, wheel)
            self.assertNotEqual(r.returncode, 0, words)
            self.assertIn(words, r.stdout)

    def test_is_member_three_states(self):
        for member, code in ((True, 0), (False, 1), (None, 2)):
            r = self.bash(f'dseditgroup() {{ {self.MEMBER[member]}; }}', "is_member dt-gate admin")
            self.assertEqual(r.returncode, code, member)

    def test_admin_unclear_stops(self):
        """「查不清是不是管理员」不当成「不是」。"""
        stubs = ('id() { return 0; }\nuid_ok() { return 0; }\n' f'dseditgroup() {{ {self.MEMBER[None]}; }}\n'
                 'dscl() { return 1; }\nfdesetup() { :; }\nsudo() { echo "User $3 is not allowed to run sudo"; }\n'
                 'defaults() { return 1; }\nremote_access() { return 0; }\nard_access() { return 0; }\n')
        r = self.bash(stubs, func(SETUP, "check_account") + "\ncheck_account dt-gate")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("查不清 dt-gate 是不是管理员", r.stdout)

    def test_uid_checked_first(self):
        """L3 第 3 轮 P1-01：uid 不过直接停，后面的核对都不做。"""
        stubs = 'id() { return 0; }\nuid_ok() { return 1; }\ndseditgroup() { echo CALLED; }\n'
        r = self.bash(stubs, func(SETUP, "check_account") + "\ncheck_account dt-gate")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("uid", r.stdout)
        self.assertNotIn("CALLED", r.stdout)

    def test_uid_ok(self):
        """uid 大于 0、目录服务里只对应这一个账号；目录服务出错、空、uid 是 0、重复都不过。"""
        def run(uid, listing, dscl_rc=0):
            stubs = (f'id() {{ echo {uid}; }}\n'
                     f'dscl() {{ printf "%s\\n" {listing}; return {dscl_rc}; }}\n')
            return self.bash(stubs, "uid_ok dt-gate").returncode
        self.assertEqual(run(503, "'root 0' 'nat 501' 'dt-gate 503'"), 0)
        self.assertNotEqual(run(0, "'dt-gate 0'"), 0)                                # uid 是 0（哪怕目录服务里只有它）
        self.assertNotEqual(run(503, "'dt-gate 503' 'evil 503'"), 0)                 # 重复
        self.assertNotEqual(run(503, "'dt-gate 503'", dscl_rc=1), 0)                 # 目录服务出错
        self.assertNotEqual(run(503, "''"), 0)                                       # 空
        self.assertNotEqual(run(503, "'other 503'"), 0)                              # 对应的是别的账号
        self.assertNotEqual(run("x", "'dt-gate 503'"), 0)

    def ard(self, pgrep_rc, all_users="0", privs="No such key: naprivs", groups=("staff",), member=False, defaults_rc=0):
        stubs = (f'pgrep() {{ return {pgrep_rc}; }}\n'
                 f'defaults() {{ echo "{all_users}"; return {defaults_rc}; }}\n'
                 f'dscl() {{ case "$2" in -read) echo "{privs}";; -list) printf "%s\\n" {" ".join(groups)};; esac; }}\n'
                 f'dseditgroup() {{ {self.MEMBER[member]}; }}\n')
        return self.bash(stubs, func(SETUP, "ard_access") + "\nard_access dt-gate")

    def test_ard_access(self):
        """远程管理开着时不再只提示：所有本地用户、账号带 naprivs、在 ARD 授权组里、查不清，都停下。"""
        self.assertEqual(self.ard(1).returncode, 0)                                   # 没开
        self.assertNotEqual(self.ard(2).returncode, 0)                                # pgrep 出错
        self.assertEqual(self.ard(0).returncode, 0)                                   # 开着，没给它权限
        self.assertNotEqual(self.ard(0, all_users="1").returncode, 0)                 # 允许所有本地用户
        # L3 第 4 轮 P2-02：读不出、没有这一项、读出别的值，都是查不清（原来一律当成 0 放过）
        r = self.ard(0, all_users="The domain/default pair does not exist", defaults_rc=1)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("查不清", r.stdout)
        self.assertNotEqual(self.ard(0, all_users="0", defaults_rc=1).returncode, 0)   # 退出码不是 0 时输出不算数
        self.assertNotEqual(self.ard(0, all_users="garbage").returncode, 0)
        self.assertNotEqual(self.ard(0, privs="naprivs: -1073741569").returncode, 0)  # 账号上有权限
        self.assertNotEqual(self.ard(0, privs="DS Error: -14009").returncode, 0)      # 查不清
        g = ("staff", "com.apple.local.ard_admin")
        self.assertEqual(self.ard(0, groups=g, member=False).returncode, 0)
        self.assertNotEqual(self.ard(0, groups=g, member=True).returncode, 0)
        self.assertNotEqual(self.ard(0, groups=g, member=None).returncode, 0)

    def no_proc(self, procs=None, err=None, rc=None):
        if rc is not None:
            stub = f"ps() {{ return {rc}; }}\n"
        elif err:
            stub = f"ps() {{ echo '{err}' >&2; return 1; }}\n"
        elif procs is None:
            stub = "ps() { return 1; }\n"                                            # 这个账号没有进程
        else:
            stub = "ps() { printf '%s\\n' " + " ".join(f"'{p}'" for p in procs) + "; }\n"
        return subprocess.run(["bash", "-c", stub + func(SETUP, "no_processes") + "\nno_processes dt-gate"],
                              capture_output=True, text=True)

    def test_no_processes_ignores_system_agents(self):
        """macOS 给每个账号起的自带后台进程（SIP 保护目录里的）不算；别的程序、解释器在跑就停下；ps 出错也停下。"""
        system = ["/usr/sbin/distnoted", "/usr/libexec/lsd",
                  "/System/Library/Frameworks/CoreServices.framework/Versions/A/Support/mdbulkimport"]
        self.assertEqual(self.no_proc(system).returncode, 0)
        self.assertEqual(self.no_proc(None).returncode, 0)
        for other in ("/usr/local/bin/node", "/Users/dt-gate/bin/x", "/tmp/x", "/Applications/A.app/Contents/MacOS/A",
                      "/bin/bash", "/bin/sh", "/bin/zsh", "/usr/bin/python3", "/usr/bin/perl5.34", "/usr/bin/osascript"):
            r = self.no_proc(system + [other])
            self.assertNotEqual(r.returncode, 0, other)
            self.assertIn(other, r.stdout)
        r = self.no_proc(err="ps: something broke")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("查不清", r.stdout)
        # L3 第 4 轮 P2-02：没输出、没报错但退出码不是 1（异常退出），不能当成没有进程
        for rc in (2, 70, 137):
            r = self.no_proc(rc=rc)
            self.assertNotEqual(r.returncode, 0, rc)
            self.assertIn("查不清", r.stdout)

    def test_checks_present(self):
        s = code(SETUP)
        for needle in ('sudo -l -U "$u"', "autoLoginUser", "com.apple.access_ssh", "com.apple.access_screensharing",
                       "ARDAgent", 'no_processes "$u"', 'uid_ok "$u"', 'ard_access "$u"', 'is_member "$u" admin'):
            self.assertIn(needle, s)

    def test_init_refuses_root(self):
        """兜底：以 root 身份跑账号初始化就停下，什么都不写（uid 被改成 0 的情况，L3 第 3 轮 P1-01）。"""
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        fake = Path(d.name) / "bin"
        fake.mkdir()
        (fake / "id").write_text("#!/bin/bash\necho 0\n")
        (fake / "id").chmod(0o755)
        home = Path(d.name) / "home"
        home.mkdir()
        r = subprocess.run(["bash", str(OPS / "dt_account_init_user.sh"), str(home), "# mark"], capture_output=True,
                           text=True, env=dict(os.environ, PATH=f"{fake}:{os.environ['PATH']}"))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(list(home.iterdir()), [])

    def test_no_relative_self_after_cd(self):
        """两个脚本开头都 cd /：之后再用相对的 $0 就找不到自己（sudo bash ops/dt_worker_pf.sh install）。"""
        for path in (SETUP, PF):
            c = code(path)
            after = c[c.index("\ncd /\n"):]
            self.assertNotRegex(after, r'(install|cp)[^\n]*(?<!basename )"\$0"', path.name)

    def test_root_paths_checked_before_writes(self):
        """L3 第 3 轮 P2-05：两个脚本都先核对路径再写，用的是同一份核对函数。"""
        for path in (SETUP, PF):
            c = code(path)
            self.assertIn('. "$HERE/dt_root_guard.sh"', c)
            self.assertNotIn("root_dir() {", c)                   # 不各写一份
        c = code(SETUP)
        self.assertLess(c.index('root_path "$(dirname "$DIR")"'), c.index('mkdir -m 755 "$DIR"'))
        self.assertLess(c.index('root_path /Library/LaunchDaemons'), c.index('write_plist "$u" "$nonce"'))
        c = code(PF)
        self.assertLess(c.index('root_path "$(dirname "$DIR")"'), c.index('mkdir -m 755 "$DIR"'))
        self.assertLess(c.index('root_path "$(dirname "$PLIST")"'), c.index('cat > "$PLIST"'))
        self.assertLess(c.index('root_path "$DIR" ||'), c.index('write_status "state=applying"'))


class ProbeResult(unittest.TestCase):
    """L3 P1-04、P2-01：读到的测试凭据要和 root 记下的 SHA-256 一致；结果要带本次编号、写完整、进程退出码 0。"""

    def verdict(self, want, got_lines):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / "probe").mkdir()
        if want:
            (Path(d.name) / "probe" / "dt-gate.sha256").write_text(want + "\n")
        args = " ".join(repr(l) for l in got_lines) or '""'   # Python 3.9（CI）的 f-string 里不能有反斜杠
        stub = f'DIR={d.name}\nread_result() {{ printf "%s\\n" {args}; }}\n'
        return subprocess.run(["bash", "-c", stub + func(SETUP, "secret_verdict") + "\nsecret_verdict dt-gate"],
                              capture_output=True, text=True)

    def test_secret_verdict(self):
        h = "a" * 64
        self.assertEqual(self.verdict(h, ["secret_read=yes", f"secret_sha256={h}"]).returncode, 0)
        self.assertNotEqual(self.verdict(h, ["secret_read=yes", "secret_sha256=" + "b" * 64]).returncode, 0)
        self.assertNotEqual(self.verdict(h, ["secret_read=no"]).returncode, 0)
        self.assertNotEqual(self.verdict(None, ["secret_sha256="]).returncode, 0)   # 没记录也不算

    def test_load_waits_for_this_run(self):
        s = func(SETUP, "load_probe")
        self.assertIn('grep -qx "nonce=$nonce"', s)
        self.assertIn('grep -qx "done=1"', s)
        self.assertIn("last exit code = 0", s)
        self.assertNotIn("stat -f %m", code(SETUP))   # 不再拿秒级修改时间当完成凭证
        self.assertIn("<key>DT_PROBE_NONCE</key><string>$nonce</string>", SETUP.read_text())
        self.assertIn("<key>HOME</key><string>/Users/$u</string>", SETUP.read_text())


@unittest.skipUnless(sys.platform == "darwin" and HAVE_ACCOUNTS, "要 macOS 的 pfctl 和本机的两个账号")
class PfApply(unittest.TestCase):
    """L3 P1-03 故障注入：apply 换上假 pfctl（加载、主规则集、别的锚点、开关、回读都可控），核对不过就写 state=failed；
    规则没变不重新加载（L3 第 3 轮 P1-02）。root 动手前的核对（dt_root_guard.sh）换成桩，按场景放行或不放行。"""

    FAKE = r"""#!/bin/bash
S=$FAKE_DIR; sc=$SCENARIO
case "$*" in
  "-s info") if [ "$sc" = disabled ] || { [ "$sc" = reenable ] && [ ! -e "$S/enabled" ]; }; then echo "Status: Disabled"
             else echo "Status: Enabled"; fi ;;
  "-q -E") [ "$sc" = disabled ] && exit 1
           cp "$STATUS_PATH" "$S/status_at_enable"; cp "$S/kills" "$S/kills_at_enable" 2>/dev/null; : > "$S/enabled"; exit 0 ;;
  "-s rules")
    case $sc in
      no_anchor) ;;
      early_pass) echo 'pass out quick all'; echo 'anchor "com.apple/*" all' ;;
      *) echo 'scrub-anchor "com.apple/*" all fragment reassemble'; echo 'anchor "com.apple/*" all' ;;
    esac ;;
  "-s Interfaces -v") echo "lo0"; [ "$sc" = skip_iface ] && echo "lo0 (skip)"; echo "en0"; exit 0 ;;
  "-s nat") echo 'nat-anchor "com.apple/*" all'; echo 'rdr-anchor "com.apple/*" all'
            [ "$sc" = nat_pass ] && echo 'nat pass on en0 from 192.168.2.0/24 to any -> (en0)'
            [ "$sc" = other_nat_anchor ] && echo 'nat-anchor "vpn/*" all'; exit 0 ;;
  "-a com.apple/250.ApplicationFirewall -s nat") [ "$sc" = sibling_nat_pass ] && echo 'rdr pass on lo0 proto tcp to port 3350 -> 127.0.0.1 port 9999'; exit 0 ;;
  "-a com.apple/dt-worker -s nat") [ "$sc" = own_nat_pass ] && echo 'rdr pass on lo0 proto tcp to port 3350 -> 127.0.0.1 port 9999'; exit 0 ;;
  "-a com.apple/dt-worker -s Anchors") [ "$sc" = own_sub_anchor ] && echo "  com.apple/dt-worker/x"; exit 0 ;;
  "-a "*" -s nat") exit 0 ;;
  "-a com.apple -s Anchors") [ "$sc" = anchors_fail ] && exit 1
                             echo "  com.apple/250.ApplicationFirewall"; echo "  com.apple/dt-worker"
                             [ "$sc" = odd_anchor ] && echo "  AirDrop"; exit 0 ;;
  "-a com.apple/250.ApplicationFirewall -s Anchors") [ "$sc" = nested_pass ] && echo "  com.apple/250.ApplicationFirewall/x"; exit 0 ;;
  "-a com.apple/250.ApplicationFirewall/x -s Anchors") exit 0 ;;
  "-a com.apple/250.ApplicationFirewall/x -s rules") echo 'pass out quick on lo0 all' ;;
  "-a com.apple/250.ApplicationFirewall -s rules")
    [ "$sc" = sibling_pass ] && echo 'pass out quick proto tcp all flags S/SA keep state'
    [ "$sc" = sibling_pass_both ] && echo 'pass quick on en0 all'
    [ "$sc" = sibling_pass_in ] && echo 'pass in quick proto tcp to port 5000 keep state'
    [ "$sc" = sibling_fail ] && exit 1
    echo 'block drop in all'; exit 0 ;;
  "-q -f /etc/pf.conf") exit 0 ;;
  "-q -a com.apple/dt-worker -f "*) cp "${@: -1}" "$S/loaded.rules"; cp "$STATUS_PATH" "$S/status_at_load"
                                    echo x >> "$S/loads"
                                    [ "$sc" = load_fail ] && exit 1; exit 0 ;;
  "-nvf "*) /sbin/pfctl -nvf "${@: -1}" 2>/dev/null ;;
  "-a com.apple/dt-worker -s rules")
    out=$(/sbin/pfctl -nvf "$S/loaded.rules" 2>/dev/null)
    case $sc in
      partial) echo "$out" | sed '$d' ;;
      dup) echo "$out" | sed '$d'; echo "$out" | sed -n 1p ;;
      tampered) echo "$out" | sed 's/block return/pass/' ;;
      *) echo "$out" ;;
    esac ;;
  *) echo "没想到的调用：$*" >&2; exit 3 ;;
esac
"""

    GUARD_STUB = """root_dir() { return 0; }
root_path() { [ "$SCENARIO" != dir_bad ]; }
uid_ok() { [ "$SCENARIO" != uid_bad ]; }
user_list() {
  case $SCENARIO in
    ds_fail) return 1 ;;
    ds_flaky) [ -e "$FAKE_DIR/ds_once" ] || { : > "$FAKE_DIR/ds_once"; return 1; } ;;
  esac
  echo "root 0"; echo "dt-worker 502"
  [ "$SCENARIO" = no_secretary ] || echo "dt-secretary 504"
}
"""

    PKILL = """#!/bin/bash
echo "$*" >> "$FAKE_DIR/kills"
exit 1
"""

    # L3 第 4 轮 P1-03：发了信号要回查。kill_stuck：工人的进程一直在；pgrep_err：pgrep 出错（查不清）
    PGREP = """#!/bin/bash
case "$SCENARIO:$2" in
  kill_stuck:dt-worker) exit 0 ;;
  pgrep_err:*) exit 3 ;;
esac
exit 1
"""

    def setup_dir(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        fake = d / "pfctl"
        fake.write_text(self.FAKE)
        fake.chmod(0o755)
        script = PF.read_text().replace("/sbin/pfctl", str(fake))
        script = script.replace('DIR="/Library/Application Support/DigitalTwin"', f'DIR="{d}"')
        script = script.replace("chown root:wheel", ": chown root:wheel")   # 不是 root，改不了属主
        pk = d / "pkill"
        pk.write_text(self.PKILL)
        pk.chmod(0o755)
        script = script.replace("/usr/bin/pkill", str(pk))
        pg = d / "pgrep"
        pg.write_text(self.PGREP)
        pg.chmod(0o755)
        script = script.replace("/usr/bin/pgrep", str(pg)).replace("      sleep 0.5\n", "      sleep 0\n")
        # 账号在不在只按桩 user_list 判（本机可能真有 dt-worker，id 查得到会让场景不受控）
        script = script.replace('if ! id "$u" >/dev/null 2>&1; then', "if true; then")
        script = script.replace("DS_TRIES=12", "DS_TRIES=3").replace("    sleep 5 ", "    sleep 0 ")
        script = re.sub(r'^\[ "\$\(id -u\)" = 0 \].*$', "", script, flags=re.M)
        (d / "pf.sh").write_text(script)
        (d / "dt_root_guard.sh").write_text(self.GUARD_STUB)
        return d

    def apply(self, scenario, d=None):
        d = d or self.setup_dir()
        self.last_dir = d
        env = dict(os.environ, FAKE_DIR=str(d), SCENARIO=scenario, STATUS_PATH=str(d / "pf-dt-worker.status"))
        r = subprocess.run(["bash", str(d / "pf.sh"), "apply"], capture_output=True, text=True, env=env)
        st = d / "pf-dt-worker.status"
        status = st.read_text() if st.exists() else ""
        fields = dict(kv.split("=", 1) for kv in status.split() if "=" in kv)
        at_load = (d / "status_at_load").read_text() if (d / "status_at_load").exists() else ""
        self.loads = len((d / "loads").read_text().split()) if (d / "loads").exists() else 0
        self.kills = (d / "kills").read_text().splitlines() if (d / "kills").exists() else []
        return r, fields, at_load

    def test_ok(self):
        r, f, at_load = self.apply("ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(f["state"], "ok")
        self.assertEqual(f["users"], "dt-worker,dt-secretary")
        self.assertEqual(f["rules"], "13")         # 工人 2 条放行 + 2 条出站拒绝 + 2 条入站拒绝，秘书 3 + 2 + 2
        self.assertEqual(f["reloaded"], "yes")
        self.assertRegex(f["boot"], r"^\d+$")      # 记本次开机时间（审查第 4 轮 P2-2）
        self.assertIn("state=applying", at_load)  # 加载前先写「进行中」
        # 重新加载过：两个账号的进程全部结束（旧规则下的连接不过新规则，审查第 4 轮 P2-4）
        self.assertEqual(self.kills, ["-KILL -u dt-worker", "-KILL -u dt-secretary"])

    def test_unchanged_not_reloaded(self):
        """L3 第 3 轮 P1-02：实际加载的和应有的一样，就不重新加载（两次偶发漏过都在重新加载附近）；被改了才重新加载。"""
        d = self.setup_dir()
        self.apply("ok", d)
        self.assertEqual(self.loads, 1)
        (d / "kills").unlink()
        st = d / "pf-dt-worker.status"
        before = st.read_text()
        r, f, _ = self.apply("ok", d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((f["state"], f["reloaded"]), ("ok", "no"))
        self.assertEqual(self.loads, 1)
        self.assertEqual(self.kills, [])           # 没重新加载，不动账号的进程
        self.assertNotIn("applying", before)       # 平时的核对不写「进行中」（审查第 4 轮 P2-1）
        (d / "loaded.rules").write_text("# 被人清空了\n")
        r, f, _ = self.apply("ok", d)
        self.assertEqual((f["state"], f["reloaded"]), ("ok", "yes"))
        self.assertEqual(self.loads, 2)

    def test_faults_end_failed(self):
        for scenario, why in (("load_fail", "规则没加载上"), ("partial", "不一致"), ("dup", "不一致"),
                              ("tampered", "不一致"), ("no_anchor", "没挂"), ("disabled", "pf_没开"),
                              ("early_pass", "主规则集里锚点前面有：pass_out_quick_all"),
                              ("sibling_pass", "com.apple/250.ApplicationFirewall_里有：pass_out_quick"),
                              ("nested_pass", "com.apple/250.ApplicationFirewall/x_里有：pass_out_quick"),
                              ("sibling_pass_both", "com.apple/250.ApplicationFirewall_里有：pass_quick"),
                              ("sibling_pass_in", "com.apple/250.ApplicationFirewall_里有：pass_in_quick"),
                              ("own_nat_pass", "com.apple/dt-worker_的地址转换里有：rdr_pass"),
                              ("own_sub_anchor", "com.apple/dt-worker_下有子锚点：com.apple/dt-worker/x"),
                              ("kill_stuck", "dt-worker_的进程结束不掉"),
                              ("pgrep_err", "查不清_dt-worker_还有没有进程（pgrep_返回_3）"),
                              ("skip_iface", "有网卡被_pf_跳过：lo0_(skip)"),
                              ("nat_pass", "主规则集的地址转换里有：nat_pass"),
                              ("other_nat_anchor", "主规则集的地址转换里有：nat-anchor_\"vpn/*\""),
                              ("sibling_nat_pass", "com.apple/250.ApplicationFirewall_的地址转换里有：rdr_pass"),
                              ("ds_fail", "目录服务查不清"),
                              ("sibling_fail", "读不出_com.apple/250.ApplicationFirewall_的规则"),
                              ("anchors_fail", "读不出_com.apple_下的锚点"),
                              ("odd_anchor", "认不出_com.apple_下的锚点名"),
                              ("uid_bad", "uid")):
            r, f, _ = self.apply(scenario)
            self.assertEqual(r.returncode, 1, scenario)
            self.assertEqual(f["state"], "failed", scenario)
            self.assertIn(why, f["reason"], scenario)
            self.assertNotIn("users", f, scenario)    # 失败时不声称管上了谁
            status = (Path(self.last_dir) / "pf-dt-worker.status").read_text()
            self.assertEqual(len(status.splitlines()), 1, status)   # 状态只有一行：原因里没混进子 shell 里陷阱写的东西
            self.assertNotIn("行出错", status, scenario)
            if scenario not in ("load_fail", "partial", "dup", "tampered", "kill_stuck", "pgrep_err"):
                self.assertEqual(self.loads, 0, scenario)   # 前面的核对不过，根本不加载
            # 判定不在生效：两个账号正在跑的进程一律结束（审查第 4 轮 P2-5）；目录服务查不清时不知道账号在不在，只报不清
            if scenario == "ds_fail":
                self.assertEqual(self.kills, [], scenario)
                self.assertIn("查不清_dt-worker_在不在", f["reason"])
            elif scenario not in ("kill_stuck", "pgrep_err"):
                self.assertEqual(self.kills, ["-KILL -u dt-worker", "-KILL -u dt-secretary"] * (1 + (scenario in ("partial", "dup", "tampered", "disabled"))), scenario)

    def test_directory_unclear_keeps_rules(self):
        """审查第 4 轮 P1-1：目录服务一时查不清，不能当成「没有秘书」把秘书从规则里删掉；短暂查不清时重试后照常。"""
        d = self.setup_dir()
        self.apply("ok", d)
        rules_before = (d / "pf-dt-worker.rules").read_text()
        r, f, _ = self.apply("ds_fail", d)
        self.assertEqual(f["state"], "failed")
        self.assertEqual((d / "pf-dt-worker.rules").read_text(), rules_before)   # 规则文件、加载的规则都不动
        self.assertEqual(self.loads, 1)
        r, f, _ = self.apply("ds_flaky", d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((f["state"], f["users"]), ("ok", "dt-worker,dt-secretary"))
        r, f, _ = self.apply("no_secretary", d)      # 目录服务说清了没有秘书：才不管它
        self.assertEqual((f["state"], f["users"]), ("ok", "dt-worker"))

    def test_kill_checked_and_independent_of_status(self):
        """L3 第 4 轮 P1-03：结束进程后回查，还有剩就是 failed（原来 pkill 的结果一律忽略，测试里 pkill 返回 1 也照样 ok）；
        状态文件写不成时照样结束进程（原来 set -e 下写状态失败，后面的清进程就跳过了）。"""
        r, f, _ = self.apply("kill_stuck")
        self.assertEqual((r.returncode, f["state"]), (1, "failed"))
        self.assertEqual(self.kills.count("-KILL -u dt-worker"), 20)   # 重新加载后 10 次、判 failed 后又 10 次
        d = self.setup_dir()
        script = (d / "pf.sh").read_text().replace('> "$STATUS.tmp"', '> "$DIR/no-such-dir/x"', 1)
        (d / "pf.sh").write_text(script)
        r, f, _ = self.apply("no_anchor", d)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.kills, ["-KILL -u dt-worker", "-KILL -u dt-secretary"])

    def test_reenable_writes_applying_and_kills_first(self):
        """L3 第 4 轮 P1-03：pf 被关过、规则没变，原来直接重新打开就写 ok；现在打开前先写「进行中」、结束两个账号的进程。"""
        d = self.setup_dir()
        self.apply("ok", d)
        (d / "kills").unlink()
        r, f, _ = self.apply("reenable", d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((f["state"], f["reloaded"], f["reenabled"]), ("ok", "no", "yes"))
        self.assertIn("state=applying", (d / "status_at_enable").read_text())
        self.assertEqual((d / "kills_at_enable").read_text().split("\n")[:2], ["-KILL -u dt-worker", "-KILL -u dt-secretary"])

    def test_unsafe_dir_writes_nothing(self):
        """L3 第 3 轮 P2-05：状态目录不是只有 root 能改的，连状态文件都不写。"""
        r, f, _ = self.apply("dir_bad")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(f, {})
        self.assertEqual(self.loads, 0)


def load_verify():
    spec = importlib.util.spec_from_file_location("dt_pf_verify", VERIFY)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Verify(unittest.TestCase):
    """真机越权测试（ops/dt_pf_verify.py）的判定和探测本身；真正以各账号身份跑要 sudo，在 Mac 上由常浩跑。"""

    def setUp(self):
        self.v = load_verify()

    def t(self, host, port, kind="tcp"):
        return {"kind": kind, "what": "x", "host": host, "port": port}

    def test_judge(self):
        j = self.v.judge
        self.assertEqual(j("dt-secretary", self.t("127.0.0.1", 8090), "open", "open"), "符合")
        self.assertEqual(j("dt-secretary", self.t("::ffff:127.0.0.1", 8090), "open", "open"), "符合")
        self.assertEqual(j("dt-secretary", self.t("127.0.0.1", 3350), "open:51234", "open:51200"), "没挡住")   # 秘书连不到中台
        self.assertEqual(j("dt-secretary", self.t("127.0.0.1", 3350), "refused", "open"), "符合")
        self.assertEqual(j("dt-worker", self.t("127.0.0.1", 3350), "open", "open"), "符合")
        self.assertEqual(j("dt-worker", self.t("open.feishu.cn", 443), "open", "open"), "没挡住")   # 公网直连
        self.assertEqual(j("dt-worker", self.t("223.5.5.5", 53, "dns"), "answer:5353", "answer:5354"), "没挡住")
        self.assertEqual(j("dt-worker", self.t("223.5.5.5", 53, "dns"), "timeout", "timeout"), "没法判断")
        self.assertEqual(j("dt-worker", self.t("api.github.com", 443, "proxy"), "200", "200"), "符合")
        self.assertEqual(j("dt-worker", self.t("api.github.com", 443, "proxy"), "refused", "200"), "该通不通")

    def test_probe_open_and_refused(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        self.addCleanup(srv.close)
        port = srv.getsockname()[1]
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        cport = closed.getsockname()[1]
        closed.close()
        got = self.v.probe([self.t("127.0.0.1", port), self.t("127.0.0.1", cport)])
        self.assertRegex(got[0], r"^open:\d+$")   # 带本地端口
        self.assertEqual(got[1], "refused")

    def test_judge_optional_skip(self):
        """本机没有 IPv6 公网时 root 也连不上 IPv6 公网 DNS：记「跳过」，不算测试不完整；别的连不上照样「没法判断」。"""
        t = {**self.t("2400:3200::1", 53, "dns"), "optional": True}
        self.assertEqual(self.v.judge("dt-worker", t, "error:65", "error:65"), "跳过")
        self.assertEqual(self.v.judge("dt-worker", t, "answer:5000", "answer:5001"), "没挡住")

    def test_parse_result_rejects_incomplete(self):
        """L3 第 3 轮 P2-06：账号那边退出码不是 0、读不懂、条数不对，都算没跑完（不会报「都挡住了」）。"""
        ok = self.v.parse_result(0, b'["refused", "timeout"]', b"", 2)
        self.assertEqual(ok, ["refused", "timeout"])
        for code, out, n in ((1, b"[]", 0), (0, b"not json", 1), (0, b'["refused"]', 2), (0, b'{"a": 1}', 1)):
            with self.assertRaises(RuntimeError):
                self.v.parse_result(code, out, b"boom", n)

    def test_own_targets_tcp_udp(self):
        """root 自己的靶子：TCP、UDP 都连得上（UDP 回声），布防后收到的连接记下来源，给了 on_hit 就当场抓现场。"""
        seen = []
        own = self.v.Targets(["127.0.0.1", "::1"], on_hit=lambda kind, port: seen.append((kind, port)) or {"pf": 1})
        self.addCleanup(own.close)
        specs = own.specs()
        self.assertEqual(sorted((t["kind"], t["host"]) for t in specs),
                         [("tcp", "127.0.0.1"), ("tcp", "::1"), ("udp", "127.0.0.1"), ("udp", "::1")])
        ts = self.v.own_targets(own)
        self.assertIn(("udp", "::ffff:127.0.0.1"), {(t["kind"], t["host"]) for t in ts})   # IPv4 映射地址也试 UDP
        got = self.v.probe(specs)          # 映射地址测试隔离（#212）当成外连拦下，真机上由 root 对照覆盖
        self.assertTrue(all(self.v.reached(g) for g in got), got)
        self.assertEqual(own.hits, [])                 # 没布防：root 对照不算
        own.armed = True
        got = self.v.probe(specs[:2])
        self.assertTrue(all(self.v.reached(g) for g in got), got)
        self.assertEqual(len(own.hits), 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual({h["pf"]["pf"] for h in own.hits}, {1})

    def test_probe_stress_counts_and_holds(self):
        """压力模式账号那一半：每个靶子都记尝试次数和各种结果；连上了先拿着连接，记下本地端口。"""
        self.v.HOLD = 0.05
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        self.addCleanup(srv.close)
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        cport = closed.getsockname()[1]
        closed.close()
        rows = self.v.probe_stress([self.t("127.0.0.1", srv.getsockname()[1]), self.t("127.0.0.1", cport)], 0.3)
        self.assertGreater(rows[0]["n"], 0)
        self.assertEqual(rows[0]["got"].get("open"), rows[0]["n"])
        self.assertTrue(all(isinstance(o["lport"], int) for o in rows[0]["opens"]))
        self.assertEqual(rows[1]["got"], {"refused": rows[1]["n"]})
        self.assertEqual(rows[1]["opens"], [])

    def test_stress_collects_both_accounts(self):
        """压力模式起账号进程、收结果这一段，用系统自带的 3.9 真起子进程走一遍（只去掉 sudo）。
        11-20 真机：先写完脚本关掉输入管道，communicate() 又去 flush 它，两个账号都报「flush of closed file」、立刻被结束。"""
        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        cport = closed.getsockname()[1]
        closed.close()
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        self.addCleanup(srv.close)
        ts = [self.t("127.0.0.1", cport), self.t("127.0.0.1", srv.getsockname()[1])]
        real = subprocess.Popen

        def no_sudo(cmd, *a, **kw):
            self.assertEqual(cmd[:4], ["sudo", "-u", cmd[2], "-H"])
            return real(cmd[4:], *a, **kw)
        self.v.subprocess.Popen = no_sudo
        self.addCleanup(setattr, self.v.subprocess, "Popen", real)
        procs = {u: self.v.run_as(u, ts, "probe-stress", 1, popen=True) for u in ("dt-worker", "dt-secretary")}
        seen = []
        outs = self.v.collect(procs, {u: len(ts) for u in procs}, 60, lambda u, kind, lport: seen.append((u, kind, lport)))
        # 审查第 4 轮 P2-7：账号一连上就报一行，root 边跑边读、当场抓现场（这里记下谁、什么协议、哪个本地端口）
        for u in procs:
            self.assertIsInstance(outs[u], list, outs[u])
            opens = [o["lport"] for o in outs[u][1]["opens"]]
            self.assertTrue(opens)
            self.assertEqual(sorted(p for w, k, p in seen if w == u and k == "tcp"), sorted(opens))
            got = outs[u][0]["got"]   # 每秒上万次连关掉的端口，偶尔有一次 error:22，不影响这里要核的
            self.assertGreater(got.get("refused", 0), 0, got)
            self.assertEqual(outs[u][0]["opens"], [])

    def test_collect_reports_failures(self):
        """账号进程出错退出、结果条数不对、超时，都记成没跑完（不会当成「没连上」）。"""
        py = "/usr/bin/python3"
        procs = {"a": subprocess.Popen([py, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE),
                 "b": subprocess.Popen([py, "-c", "print('[]')"], stdout=subprocess.PIPE, stderr=subprocess.PIPE),
                 "c": subprocess.Popen([py, "-c", "import time; time.sleep(30)"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)}
        outs = self.v.collect(procs, {"a": 1, "b": 1, "c": 1}, 2)
        self.assertIn("退出码 3", str(outs["a"]))
        self.assertIn("boom", str(outs["a"]))
        self.assertIn("条数", str(outs["b"]))
        self.assertIsInstance(outs["c"], Exception)

    def test_control_required(self):
        """审查第 4 轮 P2-6：对照组 dt-gate 连不上 root 自己连得上的靶子、没跑完，都是测试不完整（不报「全部符合」）。"""
        own, other = {**self.t("127.0.0.1", 40000), "own": True}, self.t("127.0.0.1", 5000)
        ts, ctrl = [own, other], ["open:1", "open:2"]
        ok = {"accounts": {"dt-gate": [{"got": "open:3"}, {"got": "refused"}]}}
        self.assertEqual(self.v.control_problems(ok, ts, ctrl), 0)
        bad = {"accounts": {"dt-gate": [{"got": "timeout"}, {"got": "open:4"}]}}
        self.assertEqual(self.v.control_problems(bad, ts, ctrl), 2)
        self.assertEqual(self.v.control_problems({"accounts": {}}, ts, ctrl), 2)
        hit = {"n": 3, "got": {"open": 1, "refused": 2}, "opens": []}
        miss = {"n": 3, "got": {"refused": 3}, "opens": []}
        self.assertEqual(self.v.control_incomplete([other], [hit], [hit]), [])
        self.assertIn("dt-gate 一次都没连上", self.v.control_incomplete([other], [miss], [hit])[0])
        self.assertIn("root 没跑完", self.v.control_incomplete([other], [hit], None)[0])
        self.assertIn("dt-gate 没跑完", self.v.control_incomplete([other], RuntimeError("x"), [hit])[0])

    def test_report_path_checked(self):
        """审查第 4 轮 P3-4：写记录前核对目录从 / 起只有 root 能改，不过就不写。"""
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        self.assertFalse(self.v.root_path_ok(str(d)))   # 真用 dt_root_guard.sh 核：自己账号的临时目录不过
        self.v.DIR, self.v.OUT_DIR = str(d), str(d / "verify")
        with self.assertRaises(SystemExit):
            self.v.write_report("", {})
        self.assertFalse((d / "verify").exists())

    def test_judge_stress(self):
        """账号没跑完、某个靶子一次都没试到，都算不完整；连上的每一次都算漏过，并记离最近一次重新加载差几毫秒。"""
        ts = [self.t("127.0.0.1", 1), self.t("::1", 2)]
        ok = [{"n": 5, "got": {"refused": 5}, "opens": []}, {"n": 5, "got": {"refused": 5}, "opens": []}]
        leaks, inc, _ = self.v.judge_stress(["dt-worker"], ts, {"dt-worker": ok}, [[10.0, 10.1]])
        self.assertEqual((leaks, inc), ([], []))
        leaks, inc, _ = self.v.judge_stress(["dt-worker", "dt-secretary"], ts,
                                            {"dt-worker": ok, "dt-secretary": RuntimeError("退出码 1")}, [])
        self.assertEqual(leaks, [])
        self.assertIn("dt-secretary 没跑完", inc[0])
        zero = [ok[0], {"n": 0, "got": {}, "opens": []}]
        _, inc, _ = self.v.judge_stress(["dt-worker"], ts, {"dt-worker": zero}, [])
        self.assertIn("一次都没试到", inc[0])
        hit = [{"n": 5, "got": {"refused": 4, "open": 1}, "opens": [{"at": 10.35, "lport": 5555}]}, ok[1]]
        leaks, _, _ = self.v.judge_stress(["dt-worker"], ts, {"dt-worker": hit}, [[10.0, 10.1], [11.0, 11.1]])
        self.assertEqual(len(leaks), 1)
        self.assertEqual((leaks[0]["user"], leaks[0]["lport"], leaks[0]["ms_from_reload"]), ("dt-worker", 5555, 250))
        self.assertEqual(self.v.nearest_reload(10.05, [[10.0, 10.1]]), 0)   # 正在重新加载
        self.assertIsNone(self.v.nearest_reload(10.0, []))

    def test_targets_cover_every_path(self):
        self.v.listening = lambda: {("127.0.0.1", 3350), ("127.0.0.1", 8090), ("127.0.0.1", 8086), ("*", 5433), ("::1", 7000)}
        self.v.local_addrs = lambda: ["192.168.1.16", "10.8.0.2", "2409::1"]

        class Own:
            def specs(self):
                return [{"kind": k, "what": "x", "host": h, "port": 40000, "own": True}
                        for h in ("127.0.0.1", "::1", "192.168.1.16", "2409::1") for k in ("tcp", "udp")]
        ts = self.v.targets(Own())
        kinds = {(t["kind"], t["what"], t["host"], t["port"]) for t in ts}
        for need in (("tcp", "本机回环 IPv4", "127.0.0.1", 8086), ("tcp", "本机回环 IPv4", "127.0.0.1", 3350),
                     ("tcp", "IPv4 映射地址", "::ffff:127.0.0.1", 8086), ("tcp", "本机回环 IPv6", "::1", 7000),
                     ("tcp", "本机网卡地址", "10.8.0.2", 5433), ("tcp", "本机网卡地址", "2409::1", 5433),
                     ("tcp", "公网直连", "open.feishu.cn", 443), ("dns", "公网 DNS", "223.5.5.5", 53),
                     ("dns", "公网 DNS", "2400:3200::1", 53)):
            self.assertIn(need, kinds)
        hosts = {(t["kind"], t["host"]) for t in ts if t.get("own")}
        for need in (("udp", "::1"), ("udp", "2409::1"), ("udp", "192.168.1.16"), ("udp", "::ffff:127.0.0.1"),
                     ("tcp", "::ffff:127.0.0.1")):
            self.assertIn(need, hosts)                 # UDP、IPv6、本机网卡地址（含 utun）都有 root 自己的靶子
        self.assertIn("proxy", {t["kind"] for t in ts})

    def test_stress_targets_only_blocked(self):
        """压力模式只连该挡的靶子（准连的端口不在里面）。"""
        self.v.listening = lambda: {("127.0.0.1", 3350), ("127.0.0.1", 8090), ("127.0.0.1", 5178), ("*", 5000)}
        self.v.local_addrs = lambda: ["192.168.1.16"]

        class Own:
            def specs(self):
                return [{"kind": "tcp", "what": "x", "host": "127.0.0.1", "port": 40000, "own": True}]
        ts = self.v.stress_targets(Own(), ["dt-worker", "dt-secretary"])
        ports = {(t["host"], t["port"]) for t in ts}
        self.assertNotIn(("127.0.0.1", 8090), ports)
        self.assertIn(("127.0.0.1", 5178), ports)
        self.assertIn(("127.0.0.1", 5000), ports)
        self.assertIn(("223.5.5.5", 443), ports)
        self.assertTrue(all(not self.v.should_reach(u, t) for t in ts for u in ("dt-worker", "dt-secretary")))
        self.assertIn('verify stress', PF.read_text())

    def test_own_listeners_not_listed_as_system(self):
        """root 自己的靶子先开、后枚举本机监听，lsof 会把它们也列出来；不能再当「本机已有监听」进靶子，
        否则压力模式的对照组（root、dt-gate）会去连它们、被记成漏过（11-21 stress 的 28 条误报：::1 上的 60363）。"""
        self.v.listening = lambda: {("127.0.0.1", 5000), ("127.0.0.1", 60362), ("::1", 5000), ("::1", 60363),
                                    ("192.168.1.16", 60366), ("*", 60366)}   # 别的服务和靶子同端口、监听在全网卡
        self.v.local_addrs = lambda: ["192.168.1.16"]

        class Own:
            def specs(self):
                return [{"kind": "tcp", "what": "x", "host": h, "port": p, "own": True}
                        for h, p in (("127.0.0.1", 60362), ("::1", 60363), ("192.168.1.16", 60366))] + \
                       [{"kind": "udp", "what": "x", "host": "::1", "port": 56916, "own": True}]
        own_ports = {60362, 60363, 60366}
        for ts in (self.v.targets(Own()), self.v.stress_targets(Own(), ["dt-worker", "dt-secretary"])):
            others = [t for t in ts if not t.get("own")]     # 压力模式对照组连的就是这些
            self.assertEqual([t for t in others if t["port"] in own_ports], [])
            self.assertIn(("::1", 5000), {(t["host"], t["port"]) for t in others})
            self.assertEqual(len([t for t in ts if t.get("own") and t["host"] == "::1" and t["kind"] == "tcp"]), 1)
            self.assertEqual(self.v.control_targets(ts, Own())[1], [])
        # 按端口排除、不按「地址 + 端口」：否则全网卡的 60366 会生成「192.168.1.16:60366」，连过去落到 root 靶子上
        self.assertNotIn(("192.168.1.16", 60366), {(t["host"], t["port"]) for t in self.v.targets(Own()) if not t.get("own")})
        # 不变量：对照组名单里混进靶子端口时报出来（测试不完整），不连
        bad = [{"kind": "tcp", "what": "本机回环 IPv6", "host": "::1", "port": 60363}, {"kind": "tcp", "host": "x", "port": 443}]
        others, clash = self.v.control_targets(bad, Own())
        self.assertEqual(([t["port"] for t in others], [t["port"] for t in clash]), ([443], [60363]))

    def test_enumeration_failure_is_incomplete(self):
        """L3 第 4 轮 P2-08：lsof、ifconfig 出错不能当成「本机没有监听 / 没有网卡地址」接着测，算测试不完整。"""
        real = self.v.subprocess.run
        self.addCleanup(setattr, self.v.subprocess, "run", real)
        for cmd in ("lsof", "ifconfig"):
            self.v.subprocess.run = lambda c, *a, **kw: subprocess.CompletedProcess(c, 1, "", "boom")
            with self.assertRaises(self.v.Incomplete, msg=cmd):
                self.v.listening() if cmd == "lsof" else self.v.local_addrs()
        self.v.subprocess.run = lambda c, *a, **kw: subprocess.CompletedProcess(c, 0, "", "")
        with self.assertRaises(self.v.Incomplete):
            self.v.local_addrs()                 # 连回环都没列出来：输出不对
        self.assertIn("except Incomplete", VERIFY.read_text())   # main 里接住，退出码 2（不会当成 1「漏过」或 0）

    def test_bind_errors_recorded(self):
        """L3 第 4 轮 P2-08：开不了靶子的地址不再静默跳过。地址不在本机（EADDRNOTAVAIL）记下跳过，别的错算测试不完整。"""
        own = self.v.Targets(["127.0.0.1", "192.0.2.1"])   # 192.0.2.1 是文档保留地址，本机没有
        self.addCleanup(own.close)
        self.assertEqual(own.bind_errors, [])
        self.assertEqual({b["host"] for b in own.vanished}, {"192.0.2.1"})
        real = self.v.socket.socket

        class Denied(real):
            def bind(self, addr):
                raise OSError(errno.EACCES, "denied")
        self.v.socket.socket = Denied
        self.addCleanup(setattr, self.v.socket, "socket", real)
        own2 = self.v.Targets(["127.0.0.1"])
        self.addCleanup(own2.close)
        self.assertEqual(len(own2.bind_errors), 2)
        self.assertEqual(own2.specs(), [])

    def test_reload_coverage(self):
        """L3 第 4 轮 P2-06：重新加载线程卡住、中途异常退出、出错、没覆盖到整段测试，都报出来（原来成功过一次就不报）。"""
        def run(cmd, secs=0.6, interval=0.05):
            rel = self.v.Reloader(interval, cmd)
            start = time.time()
            rel.start()
            time.sleep(secs)
            end = time.time()
            rel.stop.set()
            rel.join(5)
            return self.v.reload_coverage(rel, start, end)
        self.assertEqual(run(["/usr/bin/true"]), [])
        self.assertTrue(any("出错" in b for b in run(["/usr/bin/false"])))
        self.assertTrue(any("出错退出" in b for b in run(["/no/such/pfctl"])))
        self.v.RELOAD_TIMEOUT = 0.1
        self.assertTrue(any("没完" in b for b in run(["/bin/sleep", "1"])))
        rel = self.v.Reloader(1.0, ["/usr/bin/true"])
        rel.times = [[100.0, 100.1]]               # 只在开头加载过一次，之后没动静
        self.assertTrue(any("空档" in b for b in self.v.reload_coverage(rel, 99.0, 200.0)))
        self.assertTrue(any("空档" in b for b in self.v.reload_coverage(self.v.Reloader(1.0), 0.0, 1.0)))   # 一次都没有
        self.assertIn("reload_coverage(rel, started, ended)", VERIFY.read_text())

    def test_inbound_judge(self):
        ts = [{"kind": "tcp", "host": "127.0.0.1", "port": 1, "takeover": False},
              {"kind": "udp", "host": "::1", "port": 2, "takeover": False}]
        self.assertEqual(self.v.inbound_judge("dt-worker", ts, {"root": ["refused", "refused"]}, [])[0], 0)
        self.assertEqual(self.v.inbound_judge("dt-worker", ts, {"root": ["open:5", "refused"]}, [])[0], 1)
        self.assertEqual(self.v.inbound_judge("dt-worker", ts, {"root": ["refused", "refused"]}, ["HIT tcp x 1"])[0], 1)
        self.assertEqual(self.v.inbound_judge("dt-worker", ts, {"root": ["refused"]}, [])[0], 2)          # 条数不对
        self.assertEqual(self.v.inbound_judge("dt-gate", ts, {"root": ["open:5", "answer:6"]}, [])[0], 0)
        self.assertEqual(self.v.inbound_judge("dt-gate", ts, {"root": ["open:5", "timeout"]}, [])[0], 2)   # 对照连不上
        self.assertEqual(self.v.inbound_judge("dt-worker", ts, {"dt-secretary": ["没试", "refused"]}, [])[1][0]["port"], 2)

    def test_inbound_runs_end_to_end(self):
        """入站测试整段用 3.9 真起子进程走一遍（去掉 sudo，本机没有门卫规则）：每个账号的监听都开起来、root / dt-gate / 另一个
        账号都去连了、监听那边记到了连进来的——没有门卫时应判「没挡住」，dt-gate 的对照连得上。"""
        free = []
        for _ in range(2):
            x = socket.socket()
            x.bind(("127.0.0.1", 0))
            free.append(x.getsockname()[1])
            x.close()
        self.v.ALLOWED = {"dt-worker": {free[0]}, "dt-secretary": {free[1]}}
        self.v.account_exists = lambda u: True
        self.v.own_addrs = lambda: ["127.0.0.1", "::1"]
        real_popen, real_run = subprocess.Popen, subprocess.run

        def strip(cmd):   # subprocess.run 内部也走 Popen：只去掉 run_as 加的 sudo 前缀
            return cmd[4:] if cmd[:2] == ["sudo", "-u"] else cmd
        self.v.subprocess.Popen = lambda cmd, *a, **kw: real_popen(strip(cmd), *a, **kw)
        self.v.subprocess.run = lambda cmd, *a, **kw: real_run(strip(cmd), *a, **kw)
        self.addCleanup(setattr, self.v.subprocess, "Popen", real_popen)
        self.addCleanup(setattr, self.v.subprocess, "run", real_run)
        with contextlib.redirect_stdout(io.StringIO()):
            worst, report = self.v.inbound()
        self.assertEqual(worst, 1, report)
        for server in ("dt-worker", "dt-secretary", "dt-gate"):
            rows = report[server]["rows"]
            self.assertTrue(rows, server)
            self.assertTrue(all("error" not in r for r in rows), rows)
            self.assertTrue(report[server]["hits"], server)
        w = report["dt-worker"]["rows"]
        self.assertEqual({r["by"] for r in w}, {"root", "dt-gate", "dt-secretary"})
        # 秘书只连被工人占住的端口（秘书准连的 free[1]），那正是冒充场景
        self.assertEqual({(r["host"], r["port"]) for r in w if r["by"] == "dt-secretary"}, {("127.0.0.1", free[1])})
        self.assertTrue(all(r["verdict"] == "没挡住" for r in w))
        self.assertTrue(all(r["verdict"] == "对照" for r in report["dt-gate"]["rows"]))

    def test_runs_accounts_from_stdin(self):
        src = VERIFY.read_text()
        self.assertIn('["sudo", "-u", user, "-H", "/usr/bin/python3", "-I", "-", mode, json.dumps(ts)]', src)
        self.assertIn('cwd="/"', src)            # 当前目录在主账号家目录下时，别的账号进不去
        self.assertIn("\ncd /\n", SETUP.read_text())
        self.assertIn("dt_pf_verify.py", PF.read_text())


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
