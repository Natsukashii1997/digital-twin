"""门卫机器人（ADR-015 修订 1 批 0E-3a）自测：凭据与 lark-cli 路径、身份绑定、回调核对、和 0B-4a 状态机共用。
飞书一律换成记录器或假 lark-cli；中台换成假的；不碰 ~/.agent-autoloop、正式中台和 dt-gate。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import stat
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_answer as A  # noqa: E402
import autoloop_feishu as F  # noqa: E402
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
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": str(t / "dt/autoloop/cards.db"),
                                                "DT_GATE_HOME": str(t / "dt/gate"),
                                                "DT_GATE_SECRET_FILE": str(t / "home/.dt/gate-app.secret")})
        self.env.start()
        self.addCleanup(self.env.stop)
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
        return nonce


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
        """审查 P1-1：门卫脚本目录（或它导入的模块、__pycache__）别人能改，就不读密钥。"""
        f = Path(os.environ["DT_GATE_SECRET_FILE"])
        f.parent.mkdir(parents=True)
        f.write_text("s3cret\n")
        os.chmod(f, 0o600)
        code = Path(self.tmp.name) / "code"
        (code / "__pycache__").mkdir(parents=True)
        (code / "gatekeeper.py").write_text("")
        py = Path(self.tmp.name) / "py/python3"
        py.parent.mkdir()
        py.write_text("")
        os.chmod(py, 0o755)
        with mock.patch.object(G, "code_trusted", REAL_CODE_TRUSTED), mock.patch.object(G, "HERE", code), \
                mock.patch.object(G.sys, "executable", str(py)):
            self.assertEqual(G.read_secret(), "s3cret")                 # 解释器、脚本目录都只有本账号能改，能过
            os.chmod(py, 0o777)
            with self.assertRaisesRegex(F.Fail, "Python 解释器"):      # 解释器别人能改
                G.read_secret()
            os.chmod(py, 0o755)
            os.chmod(code, 0o777)
            with self.assertRaisesRegex(F.Fail, "门卫脚本目录"):
                G.read_secret()
            os.chmod(code, 0o755)
            os.chmod(code / "__pycache__", 0o777)
            with self.assertRaisesRegex(F.Fail, "__pycache__"):
                G.read_secret()
            os.chmod(code / "__pycache__", 0o755)
            mod = type(sys)("dt_fake_mod")
            mod.__file__ = str(code / "evil.py")
            (code / "evil.py").write_text("")
            os.chmod(code / "evil.py", 0o666)
            with mock.patch.dict(sys.modules, {"dt_fake_mod": mod}):
                with self.assertRaisesRegex(F.Fail, "evil.py"):
                    G.read_secret()

    @unittest.skipUnless(sys.platform == "darwin", "ACL 只在 macOS 上核")
    def test_acl_allow_entry_is_detected(self):
        f = Path(self.tmp.name) / "acl-file"
        f.write_text("x")
        self.assertFalse(G.allow_acl(f))
        import subprocess
        subprocess.run(["/bin/chmod", "+a", "everyone allow read", str(f)], check=True)
        self.assertTrue(G.allow_acl(f))
        self.assertTrue(G.allow_acl(Path(self.tmp.name) / "missing"))  # 读不出来也不认


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
    def test_binding_comes_from_the_click_not_from_the_routing_id(self):
        out = G.bind_start(open_id="ou_routing_only_0001")
        card = self.lark.cards()[-1]
        nonce = card["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        self.assertEqual(out["check_code"], nonce[-4:])
        self.assertIn(nonce[-4:], card["body"]["elements"][0]["content"])
        self.assertIsNone(G.binding())
        G.handle_event(event({"gate_bind": nonce}, eid="ev_b1"))
        b = G.binding()
        self.assertEqual((b["open_id"], b["chat_id"], b["message_id"]), (OWNER, CHAT, "om_gate_msg_0001"))
        patch = [a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]][-1]
        self.assertNotIn("gate_bind", patch[patch.index("--data") + 1])  # 绑好后按钮去掉
        self.assertEqual(A.identities(), (OWNER, CHAT))

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
        G.handle_event(event({"gate_bind": nonce}, eid="e_new", operator=OTHER, chat="oc_other_chat_01",
                             mid=f"om_gate_msg_{self.lark.n:04d}"))
        self.assertEqual(G.binding()["open_id"], OTHER)
        with G.db() as con:
            self.assertEqual([r["note"] for r in con.execute("SELECT note FROM binding_log ORDER BY id")], ["首次绑定", "换绑"])

    def test_rebind_to_another_chat_tells_the_old_chat(self):
        self.bind()
        G.bind_start(open_id=OTHER, replace=True)
        nonce = self.lark.cards()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["gate_bind"]
        G.handle_event(event({"gate_bind": nonce}, eid="e_r", operator=OTHER, chat="oc_other_chat_01",
                             mid=f"om_gate_msg_{self.lark.n:04d}"))
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
        self.assertEqual(self.hub_calls, [("/v1/cards/7/answer", {"choice": 1, "by": OWNER, "ref": self.mid})])
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


if __name__ == "__main__":
    unittest.main()
