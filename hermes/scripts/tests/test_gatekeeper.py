"""门卫机器人（ADR-015 修订 1 批 0E-3a）自测：凭据与 lark-cli 路径、身份绑定、回调核对、和 0B-4a 状态机共用。
飞书一律换成记录器或假 lark-cli；中台换成假的；不碰 ~/.agent-autoloop、正式中台和 dt-gate。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_answer as A  # noqa: E402
import autoloop_exits as E  # noqa: E402
import autoloop_feishu as F  # noqa: E402
import autoloop_recovery as Rv  # noqa: E402
import gatekeeper as G  # noqa: E402

APP = "cli_0123456789abcdef"
OWNER = "ou_gate_owner_0001"
CHAT = "oc_gate_home_0001"
OTHER = "ou_somebody_else_01"
CARD = {"id": 7, "version": 1, "ref": None, "kind": "ask", "state": "open", "title": "选一个", "text": "说明",
        "options": ["甲", "乙"], "recommend": 1, "choice": None, "answer": None, "by": None, "result": None, "rule": "规则A"}


class Lark:
    """门卫 lark-cli 记录器：发消息按顺序给消息号，改卡、发文字都记下来。"""
    def __init__(self):
        self.calls, self.n, self.fail = [], 0, set()

    def __call__(self, args, what):
        self.calls.append(args)
        if what in self.fail:
            raise F.SendError(f"{what}失败")
        if args[:2] == ["im", "+messages-send"] or args[:3] == ["api", "POST", "/open-apis/im/v1/messages"]:
            self.n += 1
            return {"message_id": f"om_gate_msg_{self.n:04d}"}
        return {}

    def cards(self):
        out = []
        for a in self.calls:
            if a[:2] == ["im", "+messages-send"] and a[a.index("--msg-type") + 1] == "interactive":
                out.append(json.loads(a[a.index("--content") + 1]))
            elif a[:1] == ["api"]:
                out.append(json.loads(json.loads(a[a.index("--data") + 1])["content"]))
        return out

    def texts(self):
        return [json.loads(a[a.index("--content") + 1])["text"] for a in self.calls
                if a[:2] == ["im", "+messages-send"] and a[a.index("--msg-type") + 1] == "text"]


def event(value, eid="ev_1", operator=OWNER, chat=CHAT, mid="om_gate_msg_0001", **kw):
    return {"type": "card.action.trigger", "event_id": eid, "operator_id": operator, "chat_id": chat, "message_id": mid,
            "host": "im_message", "action_tag": "button", "form_value": "", "token": "c-delayed",
            "action_value": json.dumps(value), **kw}


REAL_CODE_TRUSTED = G.code_trusted


def skip_code_trust(test):
    """门卫自身代码的路径信任（code_trusted）看的是本机解释器和仓库目录的属主、权限，换台机器（CI 的
    /opt/hostedtoolcache）结论就不同；除专测它的用例外一律跳过，它自己在临时目录里专门测。"""
    p = mock.patch.object(G, "code_trusted", lambda: None)
    p.start()
    test.addCleanup(p.stop)


class Base(unittest.TestCase):
    def setUp(self):
        skip_code_trust(self)
        G._BUS.clear()
        self.addCleanup(G._BUS.clear)
        G._OFFLINE[0] = None
        self.addCleanup(G._OFFLINE.__setitem__, 0, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": str(t / "dt/autoloop/cards.db"),
                                                "DT_EXITS_DB": str(t / "dt/autoloop/exits.db"),
                                                "DT_GATE_HOME": str(t / "dt/gate"),
                                                "DT_GATE_SECRET_FILE": str(t / "home/.dt/gate-app.secret")})
        self.env.start()
        self.addCleanup(self.env.stop)
        for gate in (mock.patch.object(Rv, "ensure_open"), mock.patch.object(Rv, "admit")):   # 恢复闸放行（闸本身见 test_autoloop_feishu）
            gate.start()
            self.addCleanup(gate.stop)
        self.lark = Lark()
        p = mock.patch.object(G.GATE, "lark", self.lark)
        p.start()
        self.addCleanup(p.stop)
        old = F.BOT
        F.use(G.GATE)
        self.addCleanup(F.use, old)

    def bind(self):
        G.bind_start(open_id=OWNER)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        rec = G.handle_event(event({"gate_bind": nonce}, eid="ev_bind", mid=f"om_gate_msg_{self.lark.n:04d}"))
        self.assertTrue(rec["ok"], rec)
        G.bind_confirm(self.shown_code())
        return nonce

    def shown_code(self):
        """门卫在点的那张卡上（改卡失败就发文字）给的 6 位确认码：只有点卡的人看得到。"""
        for a in reversed(self.lark.calls):
            m = re.search(r"确认码：\**([0-9]{6})", " ".join(a))
            if m:
                return m.group(1)
        self.fail("门卫没给确认码")

    def click_bind(self, nonce, eid, operator=OWNER, chat=CHAT):
        return G.handle_event(event({"gate_bind": nonce}, eid=eid, operator=operator, chat=chat,
                                    mid=f"om_gate_msg_{self.lark.n:04d}"))


class Config(Base):
    def test_setup_checks_app_id_and_writes_private_file(self):
        with self.assertRaisesRegex(F.Fail, "格式不对"):
            G.setup("cli_bad")
        G.setup(APP)
        f = Path(os.environ["DT_GATE_HOME"]) / "app.json"
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(G.app_id(), APP)

    def test_secret_must_be_private_regular_file(self):
        f = Path(os.environ["DT_GATE_SECRET_FILE"])
        with self.assertRaisesRegex(F.Fail, "读不到"):
            G.read_secret()
        f.parent.mkdir(parents=True)
        f.write_text("s3cret\n")
        os.chmod(f, 0o644)
        with self.assertRaisesRegex(F.Fail, "600"):
            G.read_secret()
        os.chmod(f, 0o600)
        self.assertEqual(G.read_secret(), "s3cret")
        f.rename(f.with_name("real"))
        f.symlink_to(f.with_name("real"))
        with self.assertRaisesRegex(F.Fail, "链接"):                    # 不跟着链接读
            G.read_secret()

    def test_lark_cli_must_be_absolute_and_not_writable_by_others(self):
        d = Path(self.tmp.name) / "bin"
        d.mkdir()
        exe = d / "lark-cli"
        exe.write_text("#!/bin/sh\n")
        os.chmod(exe, 0o755)
        with mock.patch.dict(os.environ, {"DT_GATE_LARK_CLI": "lark-cli"}):
            with self.assertRaisesRegex(F.Fail, "绝对路径"):
                G.lark_cli()
        with mock.patch.dict(os.environ, {"DT_GATE_LARK_CLI": str(exe)}):
            self.assertEqual(G.lark_cli(), str(exe.resolve()))
            self.assertIn(exe.resolve(), G._LARK_OK)                     # 核过的按进程记下（#255 复核 P2-1），下面每次换条件前清掉
            G._LARK_OK.clear()
            os.chmod(d, 0o777)                                           # 目录别人能写：换掉 lark-cli 就能拿到密钥
            with self.assertRaisesRegex(F.Fail, "别人能改"):
                G.lark_cli()
            os.chmod(d, 0o755)
            os.chmod(exe, 0o775)
            with self.assertRaisesRegex(F.Fail, "别人能改"):
                G.lark_cli()
            os.chmod(exe, 0o755)
            with mock.patch.object(G, "allow_acl", lambda p: Path(p) == d.resolve()):   # 权限位没问题，但 ACL 放行了别人
                with self.assertRaisesRegex(F.Fail, "别人能改"):
                    G.lark_cli()

    def test_secret_is_not_read_when_gate_code_can_be_replaced(self):
        """审查 P1-1、L3 第 1 轮 P1-04：解释器、门卫脚本目录、__pycache__、sys.path、已加载的模块别人能改，就不读密钥。
        在临时目录里摆一套（不看本机和 CI 的真实解释器、标准库目录的属主）。"""
        f = Path(os.environ["DT_GATE_SECRET_FILE"])
        f.parent.mkdir(parents=True)
        f.write_text("s3cret\n")
        os.chmod(f, 0o600)
        t = Path(self.tmp.name)
        code, lib, py = t / "code", t / "lib", t / "py/python3"
        (code / "__pycache__").mkdir(parents=True)
        (code / "gatekeeper.py").write_text("")
        lib.mkdir()
        (lib / "mod.py").write_text("")
        (t / "open").mkdir()
        py.parent.mkdir()
        py.write_text("")
        os.chmod(py, 0o755)
        ok = []

        def read():
            ok.clear()
            return G.read_secret()
        with mock.patch.object(G, "code_trusted", REAL_CODE_TRUSTED), mock.patch.object(G, "_CODE_OK", ok), \
                mock.patch.object(G, "HERE", code), mock.patch.object(G.sys, "executable", str(py)), \
                mock.patch.object(G, "search_path", lambda: [str(lib), str(t / "open/not-yet/sub")]), \
                mock.patch.object(G, "loaded_files", lambda: [str(lib / "mod.py")]):
            self.assertEqual(read(), "s3cret")                          # 都只有本账号能改，能过
            for target, mode, what in ((py, 0o777, "Python 解释器"), (code, 0o777, "门卫脚本目录"),
                                       (code / "__pycache__", 0o777, "__pycache__"),
                                       (code / "gatekeeper.py", 0o666, "门卫脚本目录里的 gatekeeper.py"),
                                       (lib, 0o777, "模块搜索路径"),
                                       (t / "open", 0o777, "模块搜索路径 .*not-yet"),  # 还不存在的看最近的上级
                                       (lib / "mod.py", 0o666, "已加载的模块 mod.py")):
                old = stat.S_IMODE(target.stat().st_mode)
                os.chmod(target, mode)
                with self.assertRaisesRegex(F.Fail, what):
                    read()
                os.chmod(target, old)
            self.assertEqual(read(), "s3cret")
            os.chmod(lib / "mod.py", 0o666)
            self.assertEqual(G.read_secret(), "s3cret")                 # 同一进程核过一次就不再核（缓存）

    def test_launch_requires_isolated_flags_and_root_owned_code(self):
        """P1-04：常驻要 -I -S -B 启动、门卫代码属 root；不满足 run 不起。"""
        code = Path(self.tmp.name) / "code"
        (code / "__pycache__").mkdir(parents=True)
        (code / "gatekeeper.py").write_text("")
        (code / "__pycache__/x.pyc").write_text("")
        flags = types.SimpleNamespace(isolated=1, no_site=1, dont_write_bytecode=1)
        with mock.patch.object(G, "HERE", code):
            with mock.patch.object(G.sys, "flags", types.SimpleNamespace(isolated=1, no_site=0, dont_write_bytecode=1)):
                with self.assertRaisesRegex(F.Fail, "-I -S -B"):
                    G.launch_ok()
            entry_env = {"PATH": G.ENTRY_PATH, "LANG": "en_US.UTF-8", "SDKROOT": "/x", "__CF_USER_TEXT_ENCODING": "0x0",
                         "DT_HUB_LISTENER_UID": str(os.geteuid())}
            with mock.patch.object(G.sys, "flags", flags):
                with self.assertRaisesRegex(F.Fail, "环境没被清空"):   # L3 第 2 轮 P1-04：没经入口的 env -i
                    G.launch_ok()
                no_listener = {k: v for k, v in entry_env.items() if k != "DT_HUB_LISTENER_UID"}
                for env in (no_listener, {**entry_env, "DT_HUB_LISTENER_UID": str(os.geteuid() + 1)}):
                    with mock.patch.dict(os.environ, env, clear=True):   # 中台监听核对没写、或不是运行账号：不起
                        with self.assertRaisesRegex(F.Fail, "DT_HUB_LISTENER_UID"):
                            G.launch_ok()
                with mock.patch.dict(os.environ, entry_env, clear=True):
                    with self.assertRaisesRegex(F.Fail, "不属 root"):        # 临时目录属本账号，不属 root
                        G.launch_ok()
                    with mock.patch.object(G, "ROOT_UID", os.geteuid()):
                        G.launch_ok()
                        (code / "sub").mkdir()                          # 子目录里的也核（L3 第 3 轮 P1-04-A：递归）
                        G.launch_ok()
                        evil = Path(self.tmp.name) / "evil.py"
                        evil.write_bytes(b"")
                        (code / "sub" / "autoloop_feishu.py").symlink_to(evil)   # root 的链接指向别人能改的文件
                        with self.assertRaisesRegex(F.Fail, "符号链接"):
                            G.launch_ok()
                    os.environ["PATH"] = "/usr/local/bin:" + G.ENTRY_PATH   # PATH 多了主账号能改的目录
                    with self.assertRaisesRegex(F.Fail, "PATH"), mock.patch.object(G, "ROOT_UID", os.geteuid()):
                        G.launch_ok()
            with mock.patch.object(G, "launch_ok", side_effect=F.Fail("门卫常驻要用 -I -S -B")), \
                    mock.patch.object(G.F, "run") as card_loop:
                with self.assertRaisesRegex(F.Fail, "-I -S -B"):
                    G.run()
                card_loop.assert_not_called()

    def test_entry_listener_check_keeps_channel_key_away_from_other_listeners(self):
        """入口写的 DT_HUB_LISTENER_UID 生效后：3350 上在听的不是门卫自己的账号，门卫不带通道钥匙去，算没发出去。"""
        opened = []
        with mock.patch.dict(os.environ, {"DT_HUB_LISTENER_UID": str(os.geteuid())}), \
                mock.patch.object(F, "hub_target", lambda channel="feishu": ("http://127.0.0.1:3350", "k")), \
                mock.patch.object(F.hub_listener, "listener_uids", lambda *a, **k: [os.geteuid() + 1]), \
                mock.patch.object(F.urllib.request.OpenerDirector, "open", lambda *a, **k: opened.append(a)):
            with self.assertRaises(F.HubError) as e:
                F.hub("GET", "/v1/cards/next?wait=1")
        self.assertFalse(e.exception.sent)
        self.assertEqual(opened, [])

    def test_main_checks_code_before_importing_siblings(self):
        """P1-04：作为主程序启动时，导入同目录脚本之前先核；目录别人能改就退出，同目录被换掉的脚本一行都不跑。"""
        code = Path(self.tmp.name) / "code"
        code.mkdir()
        (code / "gatekeeper.py").write_text((HERE.parent / "gatekeeper.py").read_text(encoding="utf-8"), encoding="utf-8")
        marker = Path(self.tmp.name) / "imported"
        (code / "autoloop_answer.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
        os.chmod(code, 0o777)
        r = subprocess.run([sys.executable, "-I", "-S", "-B", str(code / "gatekeeper.py"), "show"],
                           capture_output=True, text=True, timeout=120)
        os.chmod(code, 0o755)
        self.assertEqual(r.returncode, 1, r.stderr)
        try:                                                            # 本机解释器可信就该挡在脚本目录；CI 的解释器
            G.check_path(sys.executable, "解释器")                       # （/opt/hostedtoolcache）不可信，会更早挡在解释器
            self.assertIn("门卫脚本目录", r.stdout)
        except G.Untrusted:
            self.assertIn("Python 解释器", r.stdout)
        self.assertFalse(marker.exists())                               # 不管挡在哪，同目录被换掉的脚本一行都没跑

    def test_sticky_parent_exemption_is_not_cached_as_checked(self):
        """复核 P2-1：/tmp 这类谁都能写、带粘滞位的目录，作为上级被放过，不能顺带让它自己当目标时也被放过。"""
        d = Path(self.tmp.name) / "sticky"
        (d / "child").mkdir(parents=True)
        os.chmod(d, 0o1777)
        done = set()
        G.check_path(d / "child", "子目录", done)                          # 作为上级：粘滞位豁免
        with self.assertRaises(G.Untrusted):
            G.check_path(G.nearest(d / "nope/sub"), "还不存在的搜索路径", done)   # 落到它自己：别人能在里面建目录

    def test_every_command_requires_the_entry_script_and_launcher_is_locked_down(self):
        """复核 P1-1：手动命令也只能经入口脚本（-I -S -B）；入口脚本清空环境、用系统 python3、路径写死。"""
        out = []
        with mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            for argv in (["show"], ["doctor"], ["bind", "show"], ["bind", "confirm", "123456"], ["setup", "--app-id", APP]):
                self.assertEqual(G.main(argv), 1)
                self.assertIn("入口脚本", out[-1])
        self.assertFalse((Path(os.environ["DT_GATE_HOME"]) / "app.json").exists())
        sh = (HERE.parent.parent.parent / "ops/dt_gatekeeper.sh").read_text(encoding="utf-8")
        self.assertIn("exec /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin", sh)
        self.assertIn("GATE_UID=$(/usr/bin/id -u dt-gate)", sh)          # 中台监听核对写死成 dt-gate 的 uid
        self.assertIn('DT_HUB_LISTENER_UID="$GATE_UID"', sh)
        self.assertIn('/usr/bin/python3 -I -S -B "$GATE_DIR/gatekeeper.py" "$@"', sh)
        self.assertEqual(subprocess.run(["/bin/sh", "-n", str(HERE.parent.parent.parent / "ops/dt_gatekeeper.sh")]).returncode, 0)

    @unittest.skipUnless(sys.platform == "darwin", "ACL 只在 macOS 上核")
    def test_acl_allow_entry_is_detected(self):
        f = Path(self.tmp.name) / "acl-file"
        f.write_text("x")
        self.assertFalse(G.allow_acl(f))
        import subprocess
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(f)], check=True)
        self.assertTrue(G.allow_acl(f))
        self.assertTrue(G.allow_acl(Path(self.tmp.name) / "missing"))  # 读不出来也不认


class ExitsReader(Base):
    """#255 L3 P2-22：门卫进程里，出口账本对账读私聊用门卫自己的身份（门卫发的消息只有门卫看得到）。"""
    def test_reconcile_reads_with_the_gatekeeper_identity(self):
        self.lark.calls.clear()
        with mock.patch.dict(os.environ, {"DT_EXITS_LARK_PROFILE": "cli_mengguishou"}), \
                mock.patch("subprocess.run", side_effect=AssertionError("不该走猛鬼兽的登录配置")):
            self.assertEqual(E.lark_read(["im", "+chat-messages-list", "--chat-id", CHAT], "对账"), {})
        self.assertEqual(self.lark.calls, [["im", "+chat-messages-list", "--chat-id", CHAT]])
        self.lark.fail.add("对账")
        with self.assertRaisesRegex(E.Unreachable, "对账"):       # 门卫读失败：当读不到，保持结果不明
            E.lark_read(["im", "+chat-messages-list"], "对账")

    def test_switching_back_to_the_default_bot_restores_the_profile_reader(self):
        F.use(F.Bot())                                          # 换回猛鬼兽：对账照旧走 DT_EXITS_LARK_PROFILE
        self.assertIsNone(E._READER[0])
        F.use(G.GATE)
        self.assertIsNotNone(E._READER[0])


class RealCommand(unittest.TestCase):
    """GateBot 真的起 lark-cli 子进程：凭据只经环境变量给，不继承别的环境，身份固定 --as bot。"""
    def setUp(self):
        skip_code_trust(self)

    def test_env_carries_only_credentials_and_bot_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            (t / "bin").mkdir()
            out = t / "seen.json"
            exe = t / "bin/lark-cli"
            exe.write_text(textwrap.dedent(f"""\
                #!/usr/bin/python3
                import json, os, sys
                json.dump({{"argv": sys.argv[1:], "env": dict(os.environ)}}, open({str(out)!r}, "w"))
                print(json.dumps({{"ok": True, "data": {{"message_id": "om_real_cmd_0001"}}}}))
                """))
            os.chmod(exe, 0o755)
            sec = t / "home/.dt/gate-app.secret"
            sec.parent.mkdir(parents=True)
            sec.write_text("topsecret\n")
            os.chmod(sec, 0o600)
            env = {"DT_GATE_HOME": str(t / "gate"), "DT_GATE_SECRET_FILE": str(sec), "DT_GATE_LARK_CLI": str(exe),
                   "DT_SHOULD_NOT_LEAK": "1", "HTTPS_PROXY": "http://127.0.0.1:9"}
            with mock.patch.dict(os.environ, env):
                G.setup(APP)
                G.GATE.send_text(CHAT, "你好", "k1")
            seen = json.loads(out.read_text())
            self.assertEqual(seen["argv"][-4:], ["--as", "bot", "--format", "json"])
            self.assertEqual(seen["argv"][:2], ["im", "+messages-send"])
            self.assertEqual(seen["env"]["LARKSUITE_CLI_APP_ID"], APP)
            self.assertEqual(seen["env"]["LARKSUITE_CLI_APP_SECRET"], "topsecret")
            self.assertEqual(seen["env"]["LARKSUITE_CLI_CONFIG_DIR"], str(t / "gate/lark-cli"))
            self.assertNotIn("HTTPS_PROXY", seen["env"])                  # 不经代理（审查 P2-10：代理解 TLS 能看到密钥）
            self.assertNotIn("DT_SHOULD_NOT_LEAK", seen["env"])
            self.assertFalse([k for k in seen["env"] if k.startswith(("DT_", "HERMES_", "LARK_"))])

    def test_bad_credentials_surface_as_send_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"DT_GATE_HOME": tmp + "/gate", "DT_GATE_SECRET_FILE": tmp + "/none"}):
                with self.assertRaisesRegex(F.SendError, "lark-cli|应用编号"):   # 凭据、路径不对都按发不出去报
                    G.GATE.send_text(CHAT, "x", "k")


class Bind(Base):
    def test_binding_comes_from_click_plus_terminal_confirm(self):
        """L3 第 1 轮 P1-01：点了绑定卡只是待确认；卡上现给确认码，回终端 bind confirm 对上才登记。登记值只取回调。"""
        out = G.bind_start(open_id="ou_routing_only_0001")
        card = self.lark.cards()[-1]
        nonce = card["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.assertEqual(out["check_code"], nonce[-4:])
        self.assertIn(nonce[-4:], card["body"]["elements"][0]["content"])
        self.assertIn("bind confirm", out["next"])
        rec = self.click_bind(nonce, "ev_b1")
        self.assertEqual(rec["pending_confirm"], OWNER[-6:])
        self.assertIsNone(G.binding())                                  # 只点了卡：还没绑定
        shown = [a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]][-1]
        content = json.loads(json.loads(shown[shown.index("--data") + 1])["content"])
        self.assertNotIn("gate_bind", json.dumps(content))               # 出码后按钮去掉
        self.assertEqual(content["header"]["template"], "orange")        # 还没绑好
        code = self.shown_code()
        out = G.bind_confirm(code)
        self.assertEqual((out["open_id"], out["chat_id"]), (OWNER, CHAT))
        b = G.binding()
        self.assertEqual((b["open_id"], b["chat_id"], b["message_id"], b["event_id"]), (OWNER, CHAT, "om_gate_msg_0001", "ev_b1"))
        shown = [a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]][-1]
        self.assertEqual(json.loads(json.loads(shown[shown.index("--data") + 1])["content"])["header"]["template"], "green")
        self.assertEqual(A.identities(), (OWNER, CHAT))
        with self.assertRaisesRegex(F.Fail, "没有等确认的绑定"):         # 用过一次就没了
            G.bind_confirm(code)

    def test_confirm_command_shows_candidate_and_needs_yes(self):
        """复核 P2-5：终端里先显示待绑定的人，输入 yes 才写；不确认不消耗确认码。"""
        G.bind_start(open_id=OWNER)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e1")
        code, out = self.shown_code(), []
        with mock.patch.object(G, "launch_ok", lambda: None), \
                mock.patch("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            with mock.patch("builtins.input", return_value="no"):
                self.assertEqual(G.main(["bind", "confirm", code]), 1)
            self.assertIn(OWNER[-6:], out[0])                            # 先给看是谁
            self.assertIsNone(G.binding())
            with mock.patch("builtins.input", side_effect=EOFError):
                self.assertEqual(G.main(["bind", "confirm", code]), 1)
            with mock.patch("builtins.input", return_value="yes"):
                self.assertEqual(G.main(["bind", "confirm", code]), 0)
        self.assertEqual(G.binding()["open_id"], OWNER)

    def test_click_before_bind_card_message_id_is_registered_asks_to_retry(self):
        """L3 第 2 轮 P2-03：绑定卡已到、发卡接口还没返回消息号时被点：不记候选，回一句请几秒后再点；再点就正常。"""
        G.bind_start(open_id=OWNER)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        with G.db(write=True) as con:
            con.execute("UPDATE bind_pending SET message_id = NULL")
        rec = self.click_bind(nonce, "e1")
        self.assertFalse(rec["ok"])
        self.assertIn("再点一次", self.lark.texts()[-1])
        with G.db() as con:
            self.assertIsNone(con.execute("SELECT cand_open_id FROM bind_pending").fetchone()[0])
        with G.db(write=True) as con:
            con.execute("UPDATE bind_pending SET message_id = ?", (f"om_gate_msg_{self.lark.n - 1:04d}",))
        G.handle_event(event({"gate_bind": nonce}, eid="e2", mid=f"om_gate_msg_{self.lark.n - 1:04d}"))
        G.bind_confirm(self.shown_code())
        self.assertEqual(G.binding()["open_id"], OWNER)

    def test_rebind_notice_to_old_chat_is_kept_until_sent(self):
        """L3 第 2 轮 P2-06：换绑通知旧私聊发不出去，记在库里，之后补发到送到为止。"""
        self.bind()
        G.bind_start(open_id=OTHER, replace=True)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e_r", operator=OTHER, chat="oc_other_chat_01")
        code = self.shown_code()
        self.lark.fail.add("发消息")
        out = G.bind_confirm(code)
        self.assertEqual(out["notices_pending"], 1)
        self.assertEqual(G.binding()["open_id"], OTHER)                 # 绑定照样生效
        self.assertEqual(len(G.show()["notices_pending"]), 1)
        self.assertEqual(G.flush_notices(), 1)                          # 刚试过：没到间隔不重发
        self.lark.fail.discard("发消息")
        self.assertEqual(G.flush_notices(force=True), 0)
        old = [a for a in self.lark.calls if a[:2] == ["im", "+messages-send"] and CHAT in a]
        self.assertIn("改绑", json.loads(old[-1][old[-1].index("--content") + 1])["text"])
        self.assertEqual(G.flush_notices(force=True), 0)                # 送到后不再发
        self.assertEqual(len([a for a in self.lark.calls if a[:2] == ["im", "+messages-send"] and CHAT in a]), len(old))

    def test_wrong_confirm_code_three_times_voids_the_bind(self):
        G.bind_start(open_id=OWNER)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e1")
        code = self.shown_code()
        wrong = f"{(int(code) + 1) % 10 ** 6:06d}"
        with self.assertRaisesRegex(F.Fail, "6 位数字"):
            G.bind_confirm("12ab56")
        with self.assertRaisesRegex(F.Fail, "还能再试 2 次"):
            G.bind_confirm(wrong)
        with self.assertRaisesRegex(F.Fail, "还能再试 1 次"):
            G.bind_confirm(wrong)
        with self.assertRaisesRegex(F.Fail, "作废"):
            G.bind_confirm(wrong)
        with self.assertRaisesRegex(F.Fail, "没有等确认的绑定"):         # 作废后对的码也不行
            G.bind_confirm(code)
        self.assertIsNone(G.binding())

    def test_confirm_code_expires(self):
        G.bind_start(open_id=OWNER)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e1")
        with G.db(write=True) as con:
            con.execute("UPDATE bind_pending SET clicked_at = '2026-01-01T00:00:00+08:00'")
        with self.assertRaisesRegex(F.Fail, "过期"):
            G.bind_confirm(self.shown_code())
        self.assertIsNone(G.binding())

    def test_someone_else_cannot_take_over_a_clicked_card_and_reclick_gives_new_code(self):
        G.bind_start(open_id=OWNER)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e1")
        first = self.shown_code()
        rec = self.click_bind(nonce, "e2", operator=OTHER, chat="oc_other_chat_01")
        self.assertIn("别人点过", rec["why"])
        self.click_bind(nonce, "e3")                                    # 同一个人再点：换新码，旧码作废
        second = self.shown_code()
        if first != second:
            with self.assertRaisesRegex(F.Fail, "不对"):
                G.bind_confirm(first)
        G.bind_confirm(second)
        self.assertEqual(G.binding()["open_id"], OWNER)

    def test_code_falls_back_to_text_when_card_patch_fails(self):
        G.bind_start(open_id=OWNER)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.lark.fail.add("改绑定卡")
        self.click_bind(nonce, "e1")
        self.assertIn("确认码", self.lark.texts()[-1])
        G.bind_confirm(self.shown_code())
        self.assertEqual(G.binding()["open_id"], OWNER)

    def test_email_route_uses_raw_api(self):
        G.bind_start(email="owner@example.com")
        a = self.lark.calls[-1]
        self.assertEqual(a[:3], ["api", "POST", "/open-apis/im/v1/messages"])
        self.assertEqual(json.loads(a[a.index("--params") + 1]), {"receive_id_type": "email"})
        self.assertEqual(json.loads(a[a.index("--data") + 1])["receive_id"], "owner@example.com")

    def test_wrong_message_expired_reused_or_malformed_bind_is_refused(self):
        G.bind_start(open_id=OWNER)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        rec = G.handle_event(event({"gate_bind": nonce}, eid="e1", mid="om_gate_msg_9999"))
        self.assertIn("不是这次发出的绑定卡", rec["why"])
        rec = G.handle_event(event({"gate_bind": "0" * 16}, eid="e2"))
        self.assertIn("没有这次绑定", rec["why"])
        rec = G.handle_event(event({"gate_bind": nonce, "extra": 1}, eid="e3"))
        self.assertEqual(rec["why"], "不是门卫认的按钮")
        with G.db(write=True) as con:
            con.execute("UPDATE bind_pending SET sent_at = '2026-01-01T00:00:00+08:00'")
        rec = G.handle_event(event({"gate_bind": nonce}, eid="e4"))
        self.assertIn("过期", rec["why"])
        self.assertIsNone(G.binding())
        self.assertEqual(self.lark.texts(), [])                         # 都不回话

    def test_new_bind_start_invalidates_old_card_and_rebind_needs_replace(self):
        G.bind_start(open_id=OWNER)
        first = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.bind()
        rec = G.handle_event(event({"gate_bind": first}, eid="e_old"))   # 第一张卡已被取代
        self.assertIn("没有这次绑定", rec["why"])
        with self.assertRaisesRegex(F.Fail, "--replace"):
            G.bind_start(open_id=OWNER)
        G.bind_start(open_id=OWNER, replace=True)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e_new", operator=OTHER, chat="oc_other_chat_01")
        self.assertEqual(G.binding()["open_id"], OWNER)                 # 换绑也要终端确认
        G.bind_confirm(self.shown_code())
        self.assertEqual(G.binding()["open_id"], OTHER)
        with G.db() as con:
            self.assertEqual([r["note"] for r in con.execute("SELECT note FROM binding_log ORDER BY id")], ["首次绑定", "换绑"])

    def test_rebind_refused_while_old_chat_has_unanswered_cards(self):
        """常浩 11-20 定甲（L3 第 2 轮 P2-04）：旧私聊还有没答完的中台卡就不许换绑；读不到中台也不许；答完了才行。
        发起换绑后旧私聊又来了卡，bind confirm 写入前再查一次、确认码不消耗。"""
        self.bind()
        F.deliver(dict(CARD))                                           # 卡片 7 登记在旧私聊
        snap = {"cards": [{"id": 7, "state": "open"}], "recentCards": []}
        import autoloop_readonly as R
        with mock.patch.object(R, "read", lambda: snap):
            with self.assertRaisesRegex(F.Fail, "没答完.*7"):
                G.bind_start(open_id=OTHER, replace=True)
        with mock.patch.object(R, "read", side_effect=OSError("连不上")):
            with self.assertRaisesRegex(F.Fail, "读不到中台"):
                G.bind_start(open_id=OTHER, replace=True)
        snap["cards"], snap["recentCards"] = [], [{"id": 7, "state": "decided"}]
        with mock.patch.object(R, "read", lambda: snap):
            G.bind_start(open_id=OTHER, replace=True)                   # 答完了：可以换
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e_r", operator=OTHER, chat="oc_other_chat_01")
        code = self.shown_code()
        card8 = dict(CARD, id=8)
        F.deliver(card8)                                                # 换绑途中旧私聊又来一张卡
        snap["cards"] = [{"id": 8, "state": "open"}]
        with mock.patch.object(R, "read", lambda: snap):
            with self.assertRaisesRegex(F.Fail, "没答完.*8"):
                G.bind_confirm(code)
        self.assertEqual(G.binding()["open_id"], OWNER)
        snap["cards"] = []
        with mock.patch.object(R, "read", lambda: snap):
            G.bind_confirm(code)                                        # 确认码没被消耗
        self.assertEqual(G.binding()["open_id"], OTHER)

    def test_rebind_confirm_waits_for_in_flight_delivery_to_old_chat(self):
        """L3 第 3 轮 P2-04：发卡那边已经拿了旧私聊号、正在发（持桥接锁）时，bind confirm 要等它发完、登记完再查，
        查到旧私聊有没答完的卡就不换绑。"""
        self.bind()
        import autoloop_readonly as R
        snap = {"cards": [], "recentCards": []}
        with mock.patch.object(R, "read", lambda: snap):
            G.bind_start(open_id=OTHER, replace=True)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e_r", operator=OTHER, chat="oc_other_chat_01")
        code = self.shown_code()
        holding, go, result = threading.Event(), threading.Event(), {}

        def deliver():                                                  # 模拟发卡线程停在拿到旧私聊号之后
            with F.bridge_lock():
                holding.set()
                go.wait(5)
                F._deliver(dict(CARD))                                  # 发到旧私聊并登记
                snap["cards"] = [{"id": 7, "state": "open"}]

        def confirm():
            try:
                result["out"] = G.bind_confirm(code)
            except F.Fail as e:
                result["err"] = str(e)
        t1 = threading.Thread(target=deliver)
        t1.start()
        self.assertTrue(holding.wait(5))
        with mock.patch.object(R, "read", lambda: snap):
            t2 = threading.Thread(target=confirm)
            t2.start()
            t2.join(0.5)
            self.assertTrue(t2.is_alive())                              # 等发卡那边放锁
            go.set()
            t1.join(5)
            t2.join(5)
        self.assertIn("没答完", result.get("err", ""), result)
        self.assertEqual(G.binding()["open_id"], OWNER)

    def test_notice_not_retried_past_idempotency_window(self):
        """L3 第 3 轮 P2-06：飞书幂等键只保证一小时：超过 50 分钟还没确认送到的通知不再自动重发，提醒一次请人工核查。"""
        self.bind()
        with G.db(write=True) as con:
            con.execute("INSERT INTO notices (key, chat_id, text, created_at, last_error) VALUES "
                        "('gate-rebound-old', 'oc_old_chat_00001', 'x', '2026-01-01T00:00:00+08:00', '超时')")
        self.assertEqual(G.flush_notices(force=True), 0)
        self.assertFalse([a for a in self.lark.calls if "oc_old_chat_00001" in a])   # 没再发
        self.assertIn("人工核查", self.lark.texts()[-1])                 # 提醒发到现在绑定的私聊
        self.assertTrue(G.show()["notices_pending"][0]["gave_up_at"])
        n = len(self.lark.calls)
        G.flush_notices(force=True)
        self.assertEqual(len(self.lark.calls), n)                       # 放弃后不再提醒、不再发

    def test_rebind_to_another_chat_tells_the_old_chat(self):
        self.bind()
        G.bind_start(open_id=OTHER, replace=True)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.click_bind(nonce, "e_r", operator=OTHER, chat="oc_other_chat_01")
        G.bind_confirm(self.shown_code())
        old = [a for a in self.lark.calls if a[:2] == ["im", "+messages-send"] and CHAT in a]
        self.assertIn("改绑", json.loads(old[-1][old[-1].index("--content") + 1])["text"])

    def test_failed_send_leaves_no_pending_card(self):
        self.lark.fail.add("发绑定卡")
        with self.assertRaises(F.SendError):
            G.bind_start(open_id=OWNER)
        with G.db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM bind_pending").fetchone()[0], 0)


class Clicks(Base):
    """中台卡片的点击：门卫核对后交给 0B-4a 的同一套状态机（中台换成假的）。"""
    def setUp(self):
        super().setUp()
        self.bind()
        self.mid = F.deliver(dict(CARD))
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET hub_delivered_at = ? WHERE card_id = 7", (F.now(),))
        btn = self.lark.cards()[-1]["body"]["elements"][1]
        self.value = btn["behaviors"][0]["value"]
        self.hub_calls = []
        for target, name, fake in ((F, "hub", self.fake_hub), (F, "hub_target", lambda channel="feishu": ("http://x", "k")),
                                   (A, "read_card", lambda cid: {"id": 7, "state": "decided", "choice": 1, "by": OWNER})):
            p = mock.patch.object(target, name, fake)
            p.start()
            self.addCleanup(p.stop)

    def fake_hub(self, method, path, body=None, timeout=20, channel="feishu"):
        self.hub_calls.append((path, body))
        return {"changed": True}

    def click(self, eid="ev_c1", **kw):
        return G.handle_event(event({**self.value, "o": 1}, eid=eid, mid=kw.pop("mid", self.mid), **kw))

    def test_card_sent_by_gate_and_owner_click_reaches_hub(self):
        self.assertEqual(self.mid, f"om_gate_msg_{self.lark.n:04d}")
        sends = [a for a in self.lark.calls if a[:2] == ["im", "+messages-send"]]
        self.assertEqual(sends[-1][sends[-1].index("--chat-id") + 1], CHAT)       # 发到绑定的私聊
        rec = self.click()
        self.assertTrue(rec["ok"], rec)
        self.assertEqual(self.hub_calls, [("/v1/cards/7/answer",                # 带点的那一版（#278：中台按版本核对）
                                           {"choice": 1, "by": OWNER, "ref": self.mid, "version": 1})])
        self.assertIn("已交给中台", self.lark.texts()[-1])

    def test_replayed_event_is_handled_once(self):
        self.click(eid="ev_same")
        rec = self.click(eid="ev_same")
        self.assertIn("已经处理过", rec["why"])
        self.assertEqual(len(self.hub_calls), 1)

    def test_wrong_person_chat_or_message_never_reaches_hub(self):
        texts = len(self.lark.texts())
        rec = self.click(eid="e1", operator=OTHER)
        self.assertTrue(rec.get("quiet"), rec)
        rec = self.click(eid="e2", chat="oc_group_chat_0001")
        self.assertTrue(rec.get("quiet"), rec)
        self.assertEqual(len(self.lark.texts()), texts)                 # 来源不对不回话
        rec = self.click(eid="e3", mid="om_gate_msg_0001")              # 点的是绑定卡那条消息，不是登记的卡片
        self.assertFalse(rec["ok"])
        self.assertIn("不是登记的那张卡", self.lark.texts()[-1])
        rec = G.handle_event(event({**self.value, "o": 1}, eid="e4", mid=""))
        self.assertEqual(rec["why"], "回调没带消息号")
        self.assertEqual(self.hub_calls, [])

    def test_non_button_forms_extra_fields_and_old_style_buttons_are_ignored(self):
        cases = [event({**self.value, "o": 1}, eid="f1", mid=self.mid, form_value='{"x": 1}'),
                 event({**self.value, "o": 1}, eid="f2", mid=self.mid, action_tag="select_static"),
                 event({**self.value, "o": 1}, eid="f3", mid=self.mid, host="im_top_notice"),
                 event({**self.value, "o": 1, "x": 2}, eid="f4", mid=self.mid),
                 event({"autoloop": self.value["autoloop"], "o": 1}, eid="f5", mid=self.mid),
                 event({**self.value, "o": "1"}, eid="f6", mid=self.mid),
                 {"type": "card.action.trigger", "operator_id": OWNER}]
        for ev in cases:
            rec = G.handle_event(ev)
            self.assertFalse(rec["ok"], ev)
            self.assertTrue(rec.get("quiet"), rec)
        self.assertEqual(self.hub_calls, [])

    def test_click_before_message_id_is_registered_asks_to_retry(self):
        """审查 P2-7：卡已发出、消息号还没写进登记时点了：不说「不是那张卡」，请他稍后再点；不交中台。"""
        with F.db(write=True) as con:
            con.execute("UPDATE feishu_cards SET message_id = NULL WHERE card_id = 7")
        self.click(eid="m1")
        self.assertIn("还在登记", self.lark.texts()[-1])
        self.assertEqual(self.hub_calls, [])

    def test_unexpected_error_after_owner_checks_tells_owner_to_retry(self):
        """审查 P2-8：event_id 记下后处理出错：核对过是常浩才回一句让他再点；别人点的不回。"""
        with mock.patch.object(G, "_handle", side_effect=RuntimeError("锁超时")):
            rec = self.click(eid="x1")
            self.assertIn("RuntimeError", rec["why"])
            self.assertIn("再点一次", self.lark.texts()[-1])
            n = len(self.lark.texts())
            self.click(eid="x2", operator=OTHER)
            self.assertEqual(len(self.lark.texts()), n)

    def test_unbound_gate_refuses_clicks_and_pauses_card_loop(self):
        with G.db(write=True) as con:
            con.execute("DELETE FROM binding")
        rec = self.click(eid="u1")
        self.assertTrue(rec.get("quiet"))
        self.assertIn("还没绑定", rec["why"])
        self.assertIn("还没绑定", G.not_ready())
        with mock.patch.object(G, "binding", lambda: {"open_id": OWNER}):
            self.assertIn("长连接", G.not_ready())                        # 绑定了也要收点击的长连接就绪（P1-03）
            G.LISTENING.set()
            self.addCleanup(G.LISTENING.clear)
            self.assertIsNone(G.not_ready())


class SharedLoop(unittest.TestCase):
    """和猛鬼兽链路共用的部分：默认机器人发卡只读私聊号；常驻循环暂停与出错；门卫领卡线程死了整个退出。"""
    def test_hermes_bot_owner_chat_reads_only_the_chat_config(self):
        """审查 P2-6：open_id 配置读不了时，猛鬼兽照旧能发卡（原来发卡只看私聊号）。"""
        import twin_state as T
        old = F.BOT
        F.use(F.Bot())
        self.addCleanup(F.use, old)
        with mock.patch.dict(os.environ, {"DT_OWNER_CHAT_ID": "oc_hermes_home_01"}), \
                mock.patch.object(T, "owner_open_id", side_effect=T.StateError("权限不对")):
            self.assertEqual(F.owner_chat(), "oc_hermes_home_01")

    def test_run_survives_ready_errors_and_logs_pause_once(self):
        calls, sleeps = [], []

        def ready():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("库读不了")
            if len(calls) <= 4:
                return "没绑定"
            raise KeyboardInterrupt                                      # 结束测试
        out = []
        with mock.patch.object(F.time, "sleep", sleeps.append), \
                mock.patch("builtins.print", lambda line, **kw: out.append(line)):
            with self.assertRaises(KeyboardInterrupt):
                F.run(ready)
        self.assertEqual(len(calls), 5)
        self.assertEqual(sum('"paused"' in o for o in out), 1)          # 暂停原因不变只记一行
        self.assertTrue(any("OSError" in o for o in out))               # ready 出错不弄死线程

    def test_gate_exits_when_card_thread_dies(self):
        def consume():
            for t in threading.enumerate():
                if t.name == "gate-cards":
                    t.join(5)
            return 0
        with mock.patch.object(F, "run", lambda ready=None: None), mock.patch.object(G, "consume_once", consume), \
                mock.patch.object(G, "launch_ok", lambda: None), mock.patch.object(G, "cards_trusted", lambda: None), \
                mock.patch.object(G.time, "sleep", lambda s: None), mock.patch("builtins.print"):
            with self.assertRaises(SystemExit) as cm:
                G.run()
        self.assertEqual(cm.exception.code, 3)
        F.use(F.Bot())


class Consume(Base):
    """长连接进程：逐行交给处理函数，坏行和处理出错都不断开；退出码照传。"""
    def test_lines_are_handled_and_errors_do_not_stop_the_stream(self):
        d = Path(self.tmp.name) / "bin"
        d.mkdir()
        exe = d / "lark-cli"
        exe.write_text(textwrap.dedent("""\
            #!/usr/bin/python3
            import json, sys
            sys.stderr.write("[event] ready event_key=card.action.trigger\\n")
            print(json.dumps({"n": 1}))
            print("not json")
            print(json.dumps({"n": 2}))
            sys.stdout.flush()
            sys.exit(3)
            """))
        os.chmod(exe, 0o755)
        sec = Path(os.environ["DT_GATE_SECRET_FILE"])
        sec.parent.mkdir(parents=True)
        sec.write_text("s\n")
        os.chmod(sec, 0o600)
        G.setup(APP)
        got, logs = [], []

        def on_event(ev):
            got.append(ev["n"])
            if ev["n"] == 1:
                raise RuntimeError("坏了")
            return {"ok": True}
        with mock.patch.dict(os.environ, {"DT_GATE_LARK_CLI": str(exe)}):
            code = G.consume_once(on_event, logs.append)
        self.assertEqual(code, 3)
        self.assertEqual(got, [1, 2])
        joined = "\n".join(logs)
        self.assertIn("RuntimeError", joined)
        self.assertIn("不是 JSON", joined)
        self.assertIn("ready", joined)



class StateFiles(Base):
    """L3 第 1 轮 P1-02：门卫目录、gate.db、app.json、cards.db 用前核路径信任（含 ACL）、不认链接，不只靠 chmod 700。"""
    def test_secret_dir_writable_by_others_is_refused(self):
        """#255 复核 P2-4：密钥文件的上级目录同状态目录的判法。"""
        sec = Path(os.environ["DT_GATE_SECRET_FILE"])
        sec.parent.mkdir(parents=True)
        sec.write_text("s\n")
        os.chmod(sec, 0o600)
        os.chmod(sec.parent, 0o777)
        with self.assertRaisesRegex(F.Fail, "门卫密钥目录"):
            G.read_secret()
        os.chmod(sec.parent, 0o700)
        self.assertEqual(G.read_secret(), "s")

    def fresh_home(self, name):
        d = Path(self.tmp.name) / name
        d.mkdir()
        os.environ["DT_GATE_HOME"] = str(d)                             # Base 的 patch.dict 收尾时还原
        return d

    def test_gate_dir_writable_by_others_or_acl_is_refused(self):
        d = self.fresh_home("loose")
        os.chmod(d.parent, 0o777)                                       # 上级目录别人能写：能把门卫目录整个换掉
        with self.assertRaisesRegex(F.Fail, "别人能改"):
            G.binding()
        os.chmod(d.parent, 0o700)
        d = self.fresh_home("acl")
        with mock.patch.object(G, "allow_acl", lambda p: Path(p) == d.resolve()):   # 权限位没问题，ACL 放行了别人
            with self.assertRaisesRegex(F.Fail, "别人能改"):
                G.binding()

    def test_gate_db_and_app_json_must_be_private_regular_files(self):
        self.bind()
        home = Path(os.environ["DT_GATE_HOME"])
        db = home / "gate.db"
        os.chmod(db, 0o644)
        with self.assertRaisesRegex(F.Fail, "门卫库"):
            G.binding()
        os.chmod(db, 0o600)
        with mock.patch.object(G, "allow_acl", lambda p: Path(p) == db):
            with self.assertRaisesRegex(F.Fail, "门卫库"):
                G.binding()
        db.rename(home / "real.db")
        db.symlink_to(home / "real.db")                                 # 链接到别处的库：不认
        with self.assertRaisesRegex(F.Fail, "门卫库"):
            G.binding()
        G.setup(APP)
        os.chmod(home / "app.json", 0o644)
        with self.assertRaisesRegex(F.Fail, "门卫应用编号文件"):
            G.app_id()

    @unittest.skipUnless(sys.platform == "darwin", "ACL 只在 macOS 上核")
    def test_real_acl_on_existing_gate_db_is_refused(self):
        self.bind()
        db = Path(os.environ["DT_GATE_HOME"]) / "gate.db"
        subprocess.run(["/bin/chmod", "+a", "everyone allow write", str(db)], check=True)
        with self.assertRaisesRegex(F.Fail, "门卫库"):
            G.binding()

    @unittest.skipUnless(sys.platform == "darwin", "ACL 只在 macOS 上核")
    def test_real_acl_on_gate_dir_is_refused(self):
        """复核 P2-3：不只 mock allow_acl，真给门卫目录加一条放行 ACL。"""
        d = self.fresh_home("real-acl")
        subprocess.run(["/bin/chmod", "+a", "everyone allow add_file,delete_child", str(d)], check=True)
        with self.assertRaisesRegex(F.Fail, "别人能改"):
            G.binding()

    def test_cards_db_parent_not_created_yet_is_checked_at_nearest_ancestor(self):
        loose = Path(self.tmp.name) / "loose"
        loose.mkdir()
        os.chmod(loose, 0o777)
        with mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": str(loose / "autoloop/cards.db")}):
            with self.assertRaisesRegex(F.Fail, "卡片库目录"):
                G.cards_trusted()

    def test_cards_db_checked_by_run_and_doctor(self):
        self.bind()
        F.deliver(dict(CARD))
        cards = Path(os.environ["DT_AUTOLOOP_CARDS_DB"])
        G.cards_trusted()
        os.chmod(cards, 0o640)
        with self.assertRaisesRegex(F.Fail, "卡片库"):
            G.cards_trusted()
        self.assertIn("error", G.doctor()["cards_db"])
        with self.assertRaisesRegex(F.Fail, "卡片库"):                  # L3 第 2 轮 P2-01：show 也核
            G.show()
        os.chmod(cards, 0o600)
        cards.rename(cards.with_name("real.db"))
        cards.symlink_to(cards.with_name("real.db"))
        with self.assertRaisesRegex(F.Fail, "卡片库"):
            G.show()
        cards.unlink()
        cards.with_name("real.db").rename(cards)
        os.chmod(cards.parent, 0o770)
        with self.assertRaisesRegex(F.Fail, "卡片库目录"):
            G.cards_trusted()


class Listening(Base):
    """L3 第 1 轮 P1-03：收点击的长连接就绪了才领中台卡片；断开、重连期间不领；领到卡时断了就不发。"""
    def setUp(self):
        super().setUp()
        self.addCleanup(G.LISTENING.clear)

    PRELUDE = """\
#!/usr/bin/python3
import json, os, subprocess, sys, time
def blog(s):
    d = os.path.join(os.environ["LARKSUITE_CLI_CONFIG_DIR"], "events", os.environ["LARKSUITE_CLI_APP_ID"])
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(os.path.join(d, "bus.log"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.write(fd, ("2026/11/21 10:00:00 " + s + "\\n").encode()); os.close(fd); time.sleep(0.3)
def err(s):
    sys.stderr.write(s + "\\n"); sys.stderr.flush(); time.sleep(0.3)
def out(n):
    print(json.dumps({"n": n})); sys.stdout.flush(); time.sleep(0.3)
READY = "[event] ready event_key=card.action.trigger"
"""

    def fake_cli(self, body):
        d = Path(self.tmp.name) / "bin"
        d.mkdir(exist_ok=True)
        exe = d / "lark-cli"
        exe.write_text(self.PRELUDE + textwrap.dedent(body))
        os.chmod(exe, 0o755)
        sec = Path(os.environ["DT_GATE_SECRET_FILE"])
        if not sec.exists():
            sec.parent.mkdir(parents=True)
            sec.write_text("s\n")
            os.chmod(sec, 0o600)
            G.setup(APP)
        return exe

    def history(self, *lines, mode=0o600):
        """consume 起来之前 bus.log 里已有的内容（加入一个已经在跑的总线）。"""
        G.setup(APP)
        f = G.bus_log_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        for d in (f.parent, f.parent.parent, f.parent.parent.parent):
            os.chmod(d, 0o700)
        f.write_text("".join(f"2026/11/21 09:00:00 {line}\n" for line in lines))
        os.chmod(f, mode)

    def consume(self, exe, **kw):
        seen, told, logs = [], [], []
        with mock.patch.dict(os.environ, {"DT_GATE_LARK_CLI": str(exe)}):
            code = G.consume_once(lambda ev: seen.append((ev["n"], G.LISTENING.is_set())) or {"ok": True},
                                  logs.append, told.append, **kw)
        return seen, told, "\n".join(logs), code

    def test_listening_needs_bus_ready_and_feishu_connected_in_bus_log(self):
        """总线 ready 还不够，要 bus.log 里当前总线的飞书长连接 connected；进程退出立刻撤。"""
        exe = self.fake_cli("""\
            out(0); blog("Bus started for app=x pid=1 addr=y"); blog("Starting source: feishu")
            err(READY); out(1)
            blog("[SDK] connected to wss://msg-frontier.feishu.cn/ws/v2?x"); out(2)
            """)
        G.LISTENING.set()                                               # 上一次的残留：起新进程时先撤掉
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(seen, [(0, False), (1, False), (2, True)])
        self.assertFalse(G.LISTENING.is_set())

    def test_disconnect_reconnect_and_source_exit_follow_bus_log(self):
        """L3 第 3 轮 P1-03-A：连接状态以 bus.log 为准（写文件不会像广播那样被静默丢掉）；consume 的 stderr 状态行不算数。"""
        exe = self.fake_cli("""\
            blog("Bus started for app=x pid=1 addr=y"); err(READY); blog("[SDK] connected to wss://a"); out(1)
            blog("[SDK ERROR] disconnected to wss://a, err: read: connection reset"); err("[source] feishu: connected"); out(2)
            blog("[SDK] trying to reconnect: 1"); out(3)
            blog("[SDK] connected to wss://a"); out(4)
            blog("Source feishu exited with error: x — shutting down bus"); out(5)
            """)
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(seen, [(1, True), (2, False), (3, False), (4, True), (5, False)])

    def test_joining_running_bus_reads_current_state_from_history(self):
        """L3 第 3 轮 P2-07：总线早就在、不给新 consume 补状态：从 bus.log 里最后一次 Bus started 之后取当前状态。
        开始看之前的旧丢事件行不算、不提醒。"""
        self.history("Bus started for app=x pid=1 addr=y", "[SDK] connected to wss://old",
                     "[feishu] drop malformed event: unmarshal error: x body=y",
                     "Bus started for app=x pid=2 addr=y", "Starting source: feishu", "[SDK] connected to wss://new")
        exe = self.fake_cli("""\
            err(READY); out(1)
            """)
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(seen, [(1, True)])
        self.assertEqual(told, [])
        self.history("Bus started for app=x pid=1 addr=y", "[SDK] connected to wss://old",
                     "Bus started for app=x pid=2 addr=y")              # 新总线还没报连上：旧总线的 connected 不算
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(seen, [(1, False)])

    def test_new_bus_rotating_bus_log_is_followed(self):
        """新总线启动时 bus.log 轮转成新文件：从头读新文件。"""
        self.history("Bus started for app=x pid=1 addr=y", "[SDK] connected to wss://a")
        exe = self.fake_cli("""\
            err(READY); out(1)
            d = os.path.join(os.environ["LARKSUITE_CLI_CONFIG_DIR"], "events", os.environ["LARKSUITE_CLI_APP_ID"])
            os.rename(os.path.join(d, "bus.log"), os.path.join(d, "bus.log.1"))
            blog("Bus started for app=x pid=2 addr=y"); blog("Starting source: feishu"); out(2)
            blog("[SDK] connected to wss://b"); out(3)
            """)
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(seen, [(1, True), (2, False), (3, True)])

    def test_upstream_loss_in_bus_log_fails_closed_and_tells_owner(self):
        """L3 第 3 轮 P2-02：适配器丢事件、hub 丢事件只写 bus.log：照样撤在线、提醒常浩、结束这次 consume。"""
        for loss in ("[feishu] drop malformed event: unmarshal error: x body=y",
                     "WARN: backpressure on conn pid=12 event_key=card.action.trigger dropped_total=3"):
            exe = self.fake_cli(f"""\
                blog("Bus started for app=x pid=1 addr=y"); err(READY); blog("[SDK] connected to wss://a"); out(1)
                time.sleep(2); blog({loss!r}); time.sleep(30)    # 先让门卫处理完点击 1（负载高时子进程会抢在前面写丢事件）
                """)
            import time as _t
            t0 = _t.monotonic()
            seen, told, logs, code = self.consume(exe)
            self.assertLess(_t.monotonic() - t0, 15)
            self.assertEqual(seen, [(1, True)])
            self.assertEqual(len(told), 1)
            self.assertIn(loss[:20], told[0])
            self.assertNotIn("body=y", logs)                             # #255 复核 P2-7：回调原文不进日志
            self.assertFalse(G.LISTENING.is_set())
            G.bus_log_file().unlink()
        for line in ("[feishu] drop oversized event: 5 bytes > cap 4", "[feishu] drop event missing header fields: event_id=\"\""):
            self.assertTrue(G.BUSLOG_LOSS_RE.match(line), line)
        self.assertFalse(G.BUSLOG_LOSS_RE.match("Event deduplicated: id=x"))

    def test_not_connected_too_long_ends_this_consume(self):
        """L3 第 3 轮 P2-07：连接中（或断了、认不出）一直不到 connected，超过 CONNECT_WAIT 就结束这次 consume 再起。"""
        exe = self.fake_cli("""\
            blog("Bus started for app=x pid=1 addr=y"); blog("Starting source: feishu"); err(READY); time.sleep(30)
            """)
        import time as _t
        t0 = _t.monotonic()
        with mock.patch.object(G, "CONNECT_WAIT", 1):
            seen, told, logs, code = self.consume(exe)
        self.assertLess(_t.monotonic() - t0, 15)
        self.assertIn("秒没连上（现在：connecting）", logs)

    def test_untrusted_bus_log_never_counts_as_online(self):
        self.history("Bus started for app=x pid=1 addr=y", "[SDK] connected to wss://a", mode=0o644)
        exe = self.fake_cli("""\
            err(READY); out(1); time.sleep(30)
            """)
        seen, told, logs, code = self.consume(exe)
        self.assertFalse([n for n, on in seen if on])
        self.assertIn("读不了 lark-cli 事件总线日志", logs)

    def test_exited_marker_drops_listening_before_process_ends(self):
        exe = self.fake_cli("""\
            blog("Bus started for app=x pid=1 addr=y"); err(READY); blog("[SDK] connected to wss://a"); out(1)
            err("[event] exited reason=bus_disconnected"); out(2)
            """)
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(seen[0], (1, True))                            # exited 后撤在线、结束这次 consume；已在路上的
        self.assertFalse([n for n, on in seen[1:] if on])                # 那条照常处理（是真点击），但已不算在线

    def test_event_loss_warning_on_stderr_fails_closed_and_tells_owner(self):
        """L3 第 2 轮 P2-02：consume 自己报确定丢了事件：撤在线、提醒常浩、结束这次 consume。"""
        exe = self.fake_cli("""\
            blog("Bus started for app=x pid=1 addr=y"); err(READY); blog("[SDK] connected to wss://a"); out(1)
            err("WARN: event seq gap 3->5, missed 1 events (dropped by bus backpressure)"); time.sleep(30)
            """)
        import time as _t
        t0 = _t.monotonic()
        seen, told, logs, code = self.consume(exe)
        self.assertLess(_t.monotonic() - t0, 15)
        self.assertEqual(seen, [(1, True)])
        self.assertEqual(len(told), 1)
        self.assertIn("seq gap", told[0])
        self.assertFalse(G.LISTENING.is_set())
        for line in ("WARN: consume backpressure, dropped oldest event", "WARN: event e1 (im.x) dropped: malformed payload",
                     "WARN: sink write failed, skipping event: x", "WARN: Process error: x"):
            self.assertTrue(G.LOSS_RE.match(line), line)
        self.assertFalse(G.LOSS_RE.match("WARN: event e1 (im.x): malformed observed_at \"x\" ignored: y"))

    def test_loss_alert_goes_to_bound_owner(self):
        self.bind()
        with mock.patch.object(F, "ALERT_EVERY", 0):
            G.loss_alert("WARN: consume backpressure, dropped oldest event")
        self.assertIn("可能漏了你刚才的点击", self.lark.texts()[-1])

    def test_every_loss_is_told_and_only_the_prefix_is_quoted(self):
        """#255 复核 P1-1：丢事件提醒不限频，每次丢都说；P2-7：只引诊断前缀，不带回调原文。"""
        self.bind()
        n = len(self.lark.texts())
        G.loss_alert("WARN: consume backpressure, dropped oldest event")
        G.loss_alert("[feishu] drop malformed event: unmarshal error: x body={\"token\":\"c-secret\"}")
        texts = self.lark.texts()[n:]
        self.assertEqual(len(texts), 2)
        self.assertNotIn("c-secret", texts[1])
        self.assertIn("[feishu] drop", texts[1])

    def test_loss_written_between_two_consumes_is_not_skipped(self):
        """#255 复核 P1-2：上一次 consume 结束后、下一次起来前总线写进的丢事件行，下一次照样算（不当历史）。"""
        self.history("Bus started for app=x pid=1 addr=y", "[feishu] drop oversized event: old", "[SDK] connected to wss://a")
        exe = self.fake_cli("""\
            err(READY); out(1)
            """)
        seen, told, logs, code = self.consume(exe)
        self.assertEqual((seen, told), ([(1, True)], []))               # 第一次看到的旧内容是历史
        with open(G.bus_log_file(), "a") as f:
            f.write("2026/11/21 10:01:00 [feishu] drop oversized event: 9 bytes > cap 4\n")
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(len(told), 1)
        self.assertIn("oversized", told[0])
        seen, told, logs, code = self.consume(exe)                       # 已经说过的不再说
        self.assertEqual(told, [])

    def test_loss_reported_on_stderr_and_bus_log_is_told_once(self):
        exe = self.fake_cli("""\
            blog("Bus started for app=x pid=1 addr=y"); err(READY); blog("[SDK] connected to wss://a"); out(1)
            blog("WARN: backpressure on conn pid=12 event_key=card.action.trigger dropped_total=1")
            err("WARN: event seq gap 3->5, missed 1 events (dropped by bus backpressure)"); time.sleep(30)
            """)
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(len(told), 1)

    def test_no_ready_mark_ends_this_consume(self):
        """#255 复核 P2-2：lark-cli 一直不打就绪标记，超过 CONNECT_WAIT 也结束这次 consume。"""
        exe = self.fake_cli("""\
            time.sleep(30)
            """)
        import time as _t
        t0 = _t.monotonic()
        with mock.patch.object(G, "READY_WAIT", 1):
            seen, told, logs, code = self.consume(exe)
        self.assertLess(_t.monotonic() - t0, 25)
        self.assertIn("没打就绪标记", logs)

    def test_long_offline_after_binding_tells_owner(self):
        """#255 复核 P2-3：绑定后一直收不了点击，超过 OFFLINE_ALERT 私聊提醒一次（限频）；没绑定不提醒。"""
        G.LISTENING.clear()
        n = len(self.lark.texts())
        with mock.patch.object(G, "OFFLINE_ALERT", 0), mock.patch("builtins.print"):
            self.assertTrue(G.cards_ready())
            self.assertEqual(len(self.lark.texts()), n)                 # 没绑定：不提醒
            self.bind()
            n = len(self.lark.texts())
            G.cards_ready()
            G._OFFLINE[0] -= 1
            G.cards_ready()
            G.cards_ready()
        texts = self.lark.texts()[n:]
        self.assertEqual(len(texts), 1)
        self.assertIn("收不了点击", texts[0])
        G.LISTENING.set()
        G.cards_ready()
        self.assertIsNone(G._OFFLINE[0])

    def test_child_holding_the_pipe_does_not_hang_after_exit(self):
        """复核 P2-2：lark-cli 退了，但它起的子进程还占着 stdout：不能卡住、就绪要撤。"""
        exe = self.fake_cli("""\
            err(READY)
            subprocess.Popen(["/bin/sleep", "60"])
            time.sleep(0.5)
            sys.exit(4)
            """)
        import time as _t
        t0 = _t.monotonic()
        seen, told, logs, code = self.consume(exe)
        self.assertEqual(code, 4)
        self.assertLess(_t.monotonic() - t0, 40)    # 卡住会等满子进程的 60 秒；起进程前的信任检查本机要几秒到十几秒
        self.assertFalse(G.LISTENING.is_set())

    def test_card_loop_claims_only_while_listening(self):
        """三段：没就绪 → 就绪后领一次、长连接断了 → 重连后再领。没就绪时一次都不调中台领卡接口。"""
        gets, sleeps = [], []

        def hub(method, path, body=None, timeout=20, channel="feishu"):
            if path.startswith("/v1/cards/next"):
                gets.append(G.LISTENING.is_set())
                if len(gets) == 1:
                    G.LISTENING.clear()                                 # 领过一次后长连接断了
                    return {}
                raise KeyboardInterrupt                                 # 重连后又领：结束测试
            return {}

        def sleep(sec):
            sleeps.append(sec)
            G.LISTENING.set()                                           # 暂停一轮后长连接（重新）就绪
        with mock.patch.object(G, "binding", lambda: {"open_id": OWNER, "chat_id": CHAT}), \
                mock.patch.object(F, "hub", hub), mock.patch.object(F, "hub_readable", lambda: True), \
                mock.patch.object(A, "reconcile", lambda: []), mock.patch.object(F, "check_given_up", lambda: []), \
                mock.patch.object(F.time, "sleep", sleep), mock.patch("builtins.print"):
            with self.assertRaises(KeyboardInterrupt):
                F.run(G.not_ready)
        self.assertEqual(gets, [True, True])
        self.assertEqual(sleeps, [30, 30])                              # 两次暂停：启动时、断开后

    def test_card_claimed_while_disconnecting_is_not_sent(self):
        self.bind()
        posts, sends = [], len(self.lark.calls)

        def hub(method, path, body=None, timeout=20, channel="feishu"):
            if path.startswith("/v1/cards/next"):
                return {"card": dict(CARD)}
            posts.append((path, body))
            return {}
        with mock.patch.object(F, "hub", hub), mock.patch.object(F, "hub_readable", lambda: True):
            out = F.once(ready=G.not_ready)                             # LISTENING 没置：长轮询期间断了
        self.assertIn("长连接", out["paused"])
        self.assertEqual(posts[0][0], "/v1/cards/7/failed")
        self.assertIn("长连接", posts[0][1]["error"])
        self.assertEqual(len(self.lark.calls), sends)                   # 没发卡
        with mock.patch.object(F, "hub", hub), mock.patch.object(F, "hub_readable", lambda: True):
            out = F.once(ready=mock.Mock(side_effect=OSError("ls 跑不起来")))   # 复核 P2-6：核对本身出错也报 failed
        self.assertIn("OSError", posts[-1][1]["error"])
        self.assertEqual(len(self.lark.calls), sends)
        order = []
        real_still_open = F._still_open

        def still_open(epoch):
            order.append("闸")
            return real_still_open(epoch)

        def ready():
            order.append("长连接")
            return "断了"
        with mock.patch.object(F, "hub", hub), mock.patch.object(F, "hub_readable", lambda: True), \
                mock.patch.object(F, "_still_open", still_open):
            out = F.once(ready=ready)                               # #255 L3 P1-05：只在锁里、核过闸之后问一次
        self.assertEqual(out["paused"], "断了")
        self.assertEqual(order, ["闸", "长连接"])
        self.assertEqual(posts[-1][1]["error"], "断了")
        self.assertEqual(len(self.lark.calls), sends)

    def test_maintenance_while_disconnected_writes_nothing_to_the_hub(self):
        """#255 L3 P1-05：领到卡时维护已开始、长连接也断了——不向中台报 failed（维护停写），也不发卡，等中台再递。"""
        self.bind()
        posts, sends = [], len(self.lark.calls)

        def hub(method, path, body=None, timeout=20, channel="feishu"):
            if path.startswith("/v1/cards/next"):
                return {"card": dict(CARD)}
            posts.append((path, body))
            return {}
        ready = mock.Mock(return_value="长连接断了")
        with mock.patch.object(F, "hub", hub), mock.patch.object(F, "hub_readable", lambda: True), \
                mock.patch.object(F, "_still_open", mock.Mock(side_effect=F.Deferred("恢复闸关着：维护中"))):
            out = F.once(ready=ready)
        self.assertTrue(out["deferred"])
        self.assertEqual(posts, [])                                     # 没写中台
        self.assertEqual(len(self.lark.calls), sends)                   # 没发卡
        ready.assert_not_called()


if __name__ == "__main__":
    unittest.main()
