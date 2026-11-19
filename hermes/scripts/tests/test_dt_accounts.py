"""控制面 / 秘书账号初始化（ADR-015 批 0E-1）：门卫规则生成与加载核对、探针输出、初始化脚本的护栏、真机越权测试的判定。

不用 sudo、不改 pf、不装 LaunchDaemon：门卫规则测 `rules` 子命令打印出的内容，apply 换上假 pfctl 做故障注入；
探针用环境变量把路径指到临时目录，以当前账号跑一次；root 脚本里的函数抽出来、把系统命令换成桩再测。
"""
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OPS = Path(__file__).parents[3] / "ops"
PF = OPS / "dt_worker_pf.sh"
PROBE = OPS / "dt_account_probe.sh"
SETUP = OPS / "dt_accounts_setup.sh"
VERIFY = OPS / "dt_pf_verify.py"
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
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(lines[0].startswith("pass out quick inet proto tcp from any to 127.0.0.1 port {"))
        self.assertEqual(lines[1], f"block return out quick proto {{ tcp, udp }} from any to any user {user}")

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
        if secret_text is not None:
            secret.write_text(secret_text)
            secret.chmod(0o600)
        env = dict(os.environ, DT_PROBE_OUT=str(out), DT_PROBE_SECRET=str(secret), DT_PROBE_NONCE="n0nce",
                   HTTPS_PROXY="http://user:pass@127.0.0.1:8090",
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
        for key in ("at", "user", "uid", "gid", "real_home", "home_match", "env_path", "console_user", "self_logged_in",
                    "home_mode", "sys_proxy", "net_feishu_direct", "net_github_direct"):
            self.assertIn(key, f)

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
        for p in (PF, PROBE, SETUP, OPS / "dt_account_init_user.sh"):
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{p.name}: {r.stderr}")

    def test_no_bare_var_before_chinese(self):
        """macOS 自带的 bash 3.2 在没设 UTF-8 的环境（sudo、LaunchDaemon）里，会把「$u：」的中文首字节当成变量名的一部分吞掉，
        中文前的变量一律写成 ${u}。"""
        for p in (PF, PROBE, SETUP, OPS / "dt_account_init_user.sh"):
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
        self.assertIn('root_dir /Users', s)
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

    def root_dir(self, path, acl=None, name="root_dir"):
        """acl 给了就把 ls -led 换成桩，假装目录上有这条 ACL。"""
        stub = f"ls() {{ command ls \"$@\"; echo ' 0: {acl}'; }}\n" if acl else ""
        return subprocess.run(["bash", "-c", f"{stub}{func(SETUP, name)}\n{name} \"$1\"", "x", str(path)]).returncode == 0

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

    @unittest.skipUnless(sys.platform == "darwin", "按 macOS 的 stat -f 测")
    def test_sticky_dir(self):
        self.assertTrue(self.root_dir("/private/tmp", name="sticky_dir"))   # root 的、带粘滞位
        self.assertFalse(self.root_dir("/tmp", name="sticky_dir"))          # 是链接
        self.assertFalse(self.root_dir("/", name="sticky_dir"))             # 没有粘滞位
        self.assertFalse(self.root_dir("/private/tmp", acl="user:dt-gate allow delete_child", name="sticky_dir"))


class HomePath(unittest.TestCase):
    """L3 P1-01：家目录只认正好等于 /Users/<账号>，不认 ..、多一层、结尾斜杠；不过就不 chown / chmod。"""

    def harden(self, home):
        stubs = (f'dscl() {{ echo "NFSHomeDirectory: {home}"; }}\n'
                 'root_dir() { return 0; }\nno_link() { return 0; }\n'
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


class AccountGuards(unittest.TestCase):
    """L3 P2-02：远程登录 / 屏幕共享开着时，账号要不在系统的允许名单里；允许名单不存在 = 所有人都能登。"""

    def remote(self, on, group, member):
        stubs = (f'launchctl() {{ return {0 if on else 1}; }}\n'
                 f'dscl() {{ return {0 if group else 1}; }}\n'
                 f'dseditgroup() {{ return {0 if member else 1}; }}\n')
        return subprocess.run(["bash", "-c", stubs + func(SETUP, "remote_access") +
                               '\nremote_access dt-gate com.openssh.sshd com.apple.access_ssh 远程登录'],
                              capture_output=True, text=True)

    def test_remote_access(self):
        self.assertEqual(self.remote(False, False, False).returncode, 0)   # 没开
        self.assertEqual(self.remote(True, True, False).returncode, 0)     # 开着，不在名单里
        r = self.remote(True, False, False)                                # 开着，所有人都能登
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("[停下]", r.stdout)
        self.assertNotEqual(self.remote(True, True, True).returncode, 0)   # 开着，在名单里

    def test_no_processes_ignores_system_agents(self):
        """macOS 给每个账号起的自带后台进程（SIP 保护目录里的）不算；别的程序在跑就停下。"""
        def run(procs):
            stub = "ps() { printf '%s\\n' " + " ".join(f"'{p}'" for p in procs) + "; }\n"
            return subprocess.run(["bash", "-c", stub + func(SETUP, "no_processes") + "\nno_processes dt-gate"],
                                  capture_output=True, text=True)
        system = ["/usr/sbin/distnoted", "/usr/libexec/lsd",
                  "/System/Library/Frameworks/CoreServices.framework/Versions/A/Support/mdbulkimport"]
        self.assertEqual(run(system).returncode, 0)
        for other in ("/usr/local/bin/node", "/Users/dt-gate/bin/x", "/tmp/x", "/Applications/A.app/Contents/MacOS/A"):
            r = run(system + [other])
            self.assertNotEqual(r.returncode, 0, other)
            self.assertIn(other, r.stdout)

    def test_checks_present(self):
        s = code(SETUP)
        for needle in ('sudo -l -U "$u"', "autoLoginUser", "com.apple.access_ssh", "com.apple.access_screensharing",
                       "ARDAgent", 'no_processes "$u"'):
            self.assertIn(needle, s)


class ProbeResult(unittest.TestCase):
    """L3 P1-04、P2-01：读到的测试凭据要和 root 记下的 SHA-256 一致；结果要带本次编号、写完整、进程退出码 0。"""

    def verdict(self, want, got_lines):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        (Path(d.name) / "probe").mkdir()
        if want:
            (Path(d.name) / "probe" / "dt-gate.sha256").write_text(want + "\n")
        stub = f'DIR={d.name}\nread_result() {{ printf "%s\\n" {" ".join(repr(l) for l in got_lines) or "\"\""}; }}\n'
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


@unittest.skipUnless(sys.platform == "darwin" and HAVE_ACCOUNTS, "要 macOS 的 pfctl、dscl 和本机的两个账号")
class PfApply(unittest.TestCase):
    """L3 P1-03 故障注入：apply 换上假 pfctl（加载、主规则集、开关、回读都可控），逐条核对不过就写 state=failed。"""

    FAKE = r"""#!/bin/bash
S=$FAKE_DIR; sc=$SCENARIO
case "$*" in
  "-s info") [ "$sc" = disabled ] && echo "Status: Disabled" || echo "Status: Enabled" ;;
  "-q -E") [ "$sc" = disabled ] && exit 1; exit 0 ;;
  "-s rules") [ "$sc" = no_anchor ] || echo 'anchor "com.apple/*" all' ;;
  "-q -f /etc/pf.conf") exit 0 ;;
  "-q -a com.apple/dt-worker -f "*) cp "${@: -1}" "$S/loaded.rules"; cp "$STATUS_PATH" "$S/status_at_load"
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

    def apply(self, scenario):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(d)])
        fake = d / "pfctl"
        fake.write_text(self.FAKE)
        fake.chmod(0o755)
        script = PF.read_text().replace("/sbin/pfctl", str(fake))
        script = script.replace('DIR="/Library/Application Support/DigitalTwin"', f'DIR="{d}"')
        script = script.replace("chown root:wheel", ": chown root:wheel")   # 不是 root，改不了属主
        script = re.sub(r'^\[ "\$\(id -u\)" = 0 \].*$', "", script, flags=re.M)
        (d / "pf.sh").write_text(script)
        env = dict(os.environ, FAKE_DIR=str(d), SCENARIO=scenario, STATUS_PATH=str(d / "pf-dt-worker.status"))
        r = subprocess.run(["bash", str(d / "pf.sh"), "apply"], capture_output=True, text=True, env=env)
        status = (d / "pf-dt-worker.status").read_text()
        fields = dict(kv.split("=", 1) for kv in status.split() if "=" in kv)
        at_load = (d / "status_at_load").read_text() if (d / "status_at_load").exists() else ""
        return r, fields, at_load

    def test_ok(self):
        r, f, at_load = self.apply("ok")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(f["state"], "ok")
        self.assertEqual(f["users"], "dt-worker,dt-secretary")
        self.assertEqual(f["rules"], "9")          # 工人 2 条放行 + 2 条拒绝，秘书 3 + 2
        self.assertIn("state=applying", at_load)  # 加载前先写「进行中」

    def test_faults_end_failed(self):
        for scenario, why in (("load_fail", "规则没加载上"), ("partial", "不一致"), ("dup", "不一致"),
                              ("tampered", "不一致"), ("no_anchor", "没挂"), ("disabled", "pf_没开")):
            r, f, _ = self.apply(scenario)
            self.assertEqual(r.returncode, 1, scenario)
            self.assertEqual(f["state"], "failed", scenario)
            self.assertIn(why, f["reason"], scenario)
            self.assertNotIn("users", f, scenario)    # 失败时不声称管上了谁


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
        self.assertEqual(j("dt-secretary", self.t("127.0.0.1", 3350), "open", "open"), "没挡住")   # 秘书连不到中台
        self.assertEqual(j("dt-secretary", self.t("127.0.0.1", 3350), "refused", "open"), "符合")
        self.assertEqual(j("dt-worker", self.t("127.0.0.1", 3350), "open", "open"), "符合")
        self.assertEqual(j("dt-worker", self.t("open.feishu.cn", 443), "open", "open"), "没挡住")   # 公网直连
        self.assertEqual(j("dt-worker", self.t("223.5.5.5", 53, "dns"), "answer", "answer"), "没挡住")
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
        self.assertEqual(self.v.probe([self.t("127.0.0.1", port), self.t("127.0.0.1", cport)]), ["open", "refused"])

    def test_probe_repeat_records_local_port(self):
        """诊断模式：每个目标连 n 次，连上的记下本地端口（root 拿它去 pf 连接表里找）。"""
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(8)
        self.addCleanup(srv.close)
        rows = self.v.probe_repeat([self.t("127.0.0.1", srv.getsockname()[1])], 3)[0]
        self.assertEqual([r["r"] for r in rows], ["open"] * 3)
        self.assertTrue(all(isinstance(r["lport"], int) for r in rows))
        self.assertIn('"$@"', PF.read_text())   # verify repeat <次数> 传得进去

    def test_targets_cover_every_path(self):
        self.v.listening = lambda: {("127.0.0.1", 3350), ("127.0.0.1", 8090), ("127.0.0.1", 8086), ("*", 5433), ("::1", 7000)}
        self.v.local_addrs = lambda: ["192.168.1.16", "10.8.0.2", "2409::1"]
        ts = self.v.targets()
        kinds = {(t["what"], t["host"], t["port"]) for t in ts}
        for need in (("本机回环 IPv4", "127.0.0.1", 8086), ("本机回环 IPv4", "127.0.0.1", 3350),
                     ("IPv4 映射地址", "::ffff:127.0.0.1", 8086), ("本机回环 IPv6", "::1", 7000),
                     ("本机网卡地址", "10.8.0.2", 5433), ("本机网卡地址", "2409::1", 5433),
                     ("公网直连", "open.feishu.cn", 443), ("公网 DNS", "223.5.5.5", 53)):
            self.assertIn(need, kinds)
        self.assertIn("proxy", {t["kind"] for t in ts})

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
