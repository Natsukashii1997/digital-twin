"""隔离自测：飞书可信身份桥接（twin_identity.py，B2.1 T03）。

纯函数 verify_message 用注入的 fetch 和固定时钟；lark_fetch 打桩 subprocess.run；命令行走假 lark-cli（fake_lark.py）
和临时的 600 配置文件。不联网、不碰 ~/.hermes、不碰真实配置；每条断言都检查输出不带 open_id / chat_id 的值。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_twin_identity.py -v
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
import twin_identity as ti  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 10, 29, 15, 10, tzinfo=TZ)
OWNER = "ou_fixture_owner_0001"
CHAT = "oc_fixture_p2p_0001"
MID = "om_fixturemessage0001"
OTHER = "om_fixtureother0002"


def msg(**kw):
    """normalize 之后的形状。"""
    m = {"message_id": MID, "sender_open_id": OWNER, "sender_type": "user", "chat_id": CHAT, "chat_type": None,
         "create_time": "2026-10-29 15:05", "deleted": False}
    m.update(kw)
    return m


def raw(**kw):
    """lark-cli im +messages-mget 返回的一条消息（2026-10-29 用 bot 身份实测的字段形状，值为虚构）。"""
    m = {"chat_id": CHAT, "content": "{\"text\":\"合并\"}", "create_time": "2026-10-29 15:05", "deleted": False,
         "message_id": MID, "msg_type": "text",
         "sender": {"id": OWNER, "id_type": "open_id", "name": "虚构", "sender_type": "user", "tenant_key": "t"},
         "updated": False}
    m.update(kw)
    return m


class VerifyTests(unittest.TestCase):
    def verify(self, m, owner=OWNER, chat=CHAT, mid=MID, **kw):
        return ti.verify_message(mid, owner, fetch=lambda _: m, now=NOW, owner_chat_id=chat, **kw)

    def assertRejected(self, r, text):
        self.assertFalse(r["ok"], r)
        self.assertIn(text, r["reason"])
        dumped = json.dumps(r, ensure_ascii=False).replace(MID, "")
        for secret in (OWNER, CHAT, "ou_", "oc_"):
            self.assertNotIn(secret, dumped)   # 消息 id 可以带，身份值不能

    def test_owner_recent_private_message_passes(self):
        r = self.verify(msg())
        self.assertTrue(r["ok"], r)
        self.assertEqual((r["age_s"], r["chat"], r["method"]), (300, "p2p", "feishu_message"))
        self.assertNotIn(OWNER, json.dumps(r))
        self.assertNotIn(CHAT, json.dumps(r))

    def test_other_sender_rejected_without_leaking_ids(self):
        self.assertRejected(self.verify(msg(sender_open_id="ou_someone_else_0002")), "不是常浩本人")
        self.assertRejected(self.verify(msg(sender_open_id=None)), "不是常浩本人")

    def test_app_or_system_sender_rejected(self):
        self.assertRejected(self.verify(msg(sender_type="app")), "不是用户")

    def test_group_or_unknown_chat_rejected(self):
        self.assertRejected(self.verify(msg(chat_id="oc_some_group_0003")), "私聊")
        self.assertRejected(self.verify(msg(chat_type="group")), "私聊")
        self.assertRejected(self.verify(msg(chat_id=None)), "私聊")

    def test_no_chat_config_fails_closed(self):
        self.assertRejected(self.verify(msg(), chat=None), "owner_chat_id")

    def test_explicit_p2p_flag_accepted_without_chat_config(self):
        self.assertTrue(self.verify(msg(chat_type="p2p", chat_id="oc_whatever_0004"), chat=None)["ok"])

    def test_stale_future_or_timeless_message_rejected(self):
        self.assertRejected(self.verify(msg(create_time="2026-10-29 14:30")), "过时效")
        self.assertRejected(self.verify(msg(create_time="2026-10-29 15:20")), "未来")
        self.assertRejected(self.verify(msg(create_time=None)), "时间")
        self.assertRejected(self.verify(msg(create_time="昨天")), "时间")
        # create_time 只到分钟：上限 600 秒另加 120 秒偏差
        self.assertTrue(self.verify(msg(create_time="2026-10-29 15:00"))["ok"])
        self.assertTrue(self.verify(msg(create_time="2026-10-29 14:59"))["ok"])
        self.assertRejected(self.verify(msg(create_time="2026-10-29 14:57")), "过时效")
        self.assertRejected(self.verify(msg(create_time="2026-10-29 15:00"), max_age=60), "过时效")

    def test_deleted_rejected(self):
        self.assertRejected(self.verify(msg(deleted=True)), "撤回")

    def test_fetch_failures_rejected(self):
        def boom(_):
            raise ti.IdentityError("回读失败：permission denied")

        def crash(_):
            raise RuntimeError(OWNER)

        self.assertRejected(ti.verify_message(MID, OWNER, fetch=boom, now=NOW, owner_chat_id=CHAT), "回读飞书失败")
        self.assertRejected(ti.verify_message(MID, OWNER, fetch=crash, now=NOW, owner_chat_id=CHAT), "RuntimeError")
        self.assertRejected(ti.verify_message(MID, OWNER, fetch=lambda _: None, now=NOW, owner_chat_id=CHAT), "查不到")

    def test_bad_inputs_rejected(self):
        self.assertRejected(self.verify(msg(), owner=None), "open_id")
        self.assertRejected(self.verify(msg(), mid="not-a-message-id"), "om_")
        self.assertRejected(self.verify(msg(), mid=""), "om_")
        self.assertRejected(self.verify(msg(), mid=None), "om_")

    def test_parse_time_formats(self):
        at = dt.datetime(2026, 10, 29, 15, 5, tzinfo=TZ)
        self.assertEqual(ti.parse_time("2026-10-29 15:05", TZ), at)
        self.assertEqual(ti.parse_time("2026-10-29T15:05:30", TZ), at.replace(second=30))
        self.assertEqual(ti.parse_time(str(int(at.timestamp()) * 1000), TZ), at)
        self.assertEqual(ti.parse_time(str(int(at.timestamp())), TZ), at)
        self.assertEqual(ti.parse_time("2026-10-29T07:05:00+00:00", TZ), at)
        self.assertIsNone(ti.parse_time("", TZ))
        self.assertIsNone(ti.parse_time("x", TZ))
        self.assertIsNone(ti.parse_time(None, TZ))


class LarkFetchTests(unittest.TestCase):
    def run_fake(self, stdout="", stderr="", code=0):
        calls = []

        def fake_run(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, code, stdout, stderr)

        with mock.patch.object(ti.subprocess, "run", side_effect=fake_run), \
                mock.patch.dict(os.environ, {"HERMES_SESSION_USER_ID": "x", "OPENCLAW_X": "y", "KEEP_ME": "1"}):
            out = ti.lark_fetch(MID)
        return out, calls

    def test_bot_identity_fixed_profile_and_env_stripping(self):
        out, calls = self.run_fake(json.dumps({"ok": True, "data": {"messages": [raw()], "total": 1}}))
        cmd, kw = calls[0]
        self.assertEqual(cmd[1:5], ["--profile", ti.PROFILE, "im", "+messages-mget"])
        self.assertEqual(cmd[cmd.index("--message-ids") + 1], MID)
        self.assertEqual(cmd[cmd.index("--as") + 1], "bot")
        self.assertNotIn("HERMES_SESSION_USER_ID", kw["env"])
        self.assertNotIn("OPENCLAW_X", kw["env"])
        self.assertIn("KEEP_ME", kw["env"])
        self.assertEqual(out, msg())

    def test_error_and_garbage_outputs(self):
        with self.assertRaisesRegex(ti.IdentityError, "回读失败：perm"):
            self.run_fake(stderr=json.dumps({"ok": False, "error": {"message": "perm denied"}}), code=1)
        with self.assertRaisesRegex(ti.IdentityError, "不是 JSON"):
            self.run_fake(stdout="not json", code=1)
        self.assertIsNone(self.run_fake(json.dumps({"ok": True, "data": {"messages": [], "total": 0}}))[0])
        with self.assertRaisesRegex(ti.IdentityError, "om_"):
            ti.lark_fetch("bad id")

    def test_normalize_sender_id_variants(self):
        self.assertEqual(ti.normalize({"sender": {"sender_id": {"open_id": OWNER}, "sender_type": "user"}})["sender_open_id"], OWNER)
        self.assertIsNone(ti.normalize({"sender": {"id": "u1", "id_type": "user_id"}})["sender_open_id"])
        self.assertIsNone(ti.normalize({})["sender_open_id"])


class CliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-identity-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fake = self.root / "fake"
        self.fake.mkdir()
        recent = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.state = {"mget": {
            MID: raw(create_time=recent),
            OTHER: raw(message_id=OTHER, create_time=recent,
                       sender={"id": "ou_other_0009", "id_type": "open_id", "sender_type": "user"}),
        }}
        (self.fake / "state.json").write_text(json.dumps(self.state), encoding="utf-8")
        self.owner = self.root / "owner"
        self.owner.write_text(OWNER + "\n", encoding="utf-8")
        self.owner.chmod(0o600)
        self.chat = self.root / "chat"
        self.chat.write_text(CHAT + "\n", encoding="utf-8")
        self.chat.chmod(0o600)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("DT_OWNER", "HERMES_SESSION"))}
        self.env.update(DT_LARK_CLI=str(HERE / "fake_lark.py"), FAKE_LARK_DIR=str(self.fake),
                        DT_OWNER_FILE=str(self.owner), DT_OWNER_CHAT_FILE=str(self.chat))

    def cli(self, *args, ok=True, env=None):
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "twin_identity.py"), *args],
                           capture_output=True, text=True, env=env or self.env)
        self.assertEqual(r.returncode, 0 if ok else 1, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["ok"], ok, out)
        for secret in (OWNER, CHAT, "ou_other"):
            self.assertNotIn(secret, r.stdout + r.stderr)
        return out

    def test_verify_roundtrip_with_fake_lark(self):
        out = self.cli("verify", "--message-id", MID)
        self.assertEqual((out["chat"], out["method"]), ("p2p", "feishu_message"))
        calls = [json.loads(l) for l in (self.fake / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(calls[-1]["args"][calls[-1]["args"].index("--as") + 1], "bot")
        self.assertEqual(calls[-1]["agent_env"], [])    # HERMES* / OPENCLAW* 去掉了
        self.assertIn("不是常浩本人", self.cli("verify", "--message-id", OTHER, ok=False)["msg"])
        self.assertIn("查不到", self.cli("verify", "--message-id", "om_nobody0000", ok=False)["msg"])
        self.assertIn("om_", self.cli("verify", ok=False)["msg"])
        env = {**self.env, "HERMES_SESSION_MESSAGE_ID": MID}      # 环境变量只是指针
        self.assertTrue(self.cli("verify", env=env)["ok"])

    def test_verify_requires_local_config(self):
        # 指向不存在的文件（不能去掉变量：那样会读到本机真实配置）
        env = {**self.env, "DT_OWNER_FILE": str(self.root / "missing")}
        self.assertIn("open_id", self.cli("verify", "--message-id", MID, ok=False, env=env)["msg"])
        env = {**self.env, "DT_OWNER_CHAT_FILE": str(self.root / "missing")}
        self.assertIn("owner_chat_id", self.cli("verify", "--message-id", MID, ok=False, env=env)["msg"])
        self.chat.chmod(0o644)
        self.assertIn("600", self.cli("verify", "--message-id", MID, ok=False)["msg"])

    def test_setup_chat_preview_then_write_600(self):
        target = self.root / "new" / "owner_chat_id"
        env = {**self.env, "DT_OWNER_CHAT_FILE": str(target)}
        out = self.cli("setup-chat", "--message-id", MID, env=env)
        self.assertEqual(out["write"], False)
        self.assertFalse(target.exists())
        self.assertIn("不是常浩本人", self.cli("setup-chat", "--message-id", OTHER, "--write", ok=False, env=env)["msg"])
        self.assertFalse(target.exists())
        out = self.cli("setup-chat", "--message-id", MID, "--write", env=env)
        self.assertEqual(out["write"], True)
        self.assertEqual(target.read_text(encoding="utf-8").strip(), CHAT)
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o600")
        self.assertTrue(self.cli("verify", "--message-id", MID, env=env)["ok"])


if __name__ == "__main__":
    unittest.main()
