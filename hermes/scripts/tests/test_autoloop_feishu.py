"""飞书桥接与本人点击（#195 批 0B-4 第 4a 批）自测。
单元部分不起中台、不发飞书；端到端部分在临时目录起真中台（同 test_autoloop_hub_e2e，本机没有命令行时跳过），
走「中台出卡 → 桥接领卡、发卡、报送达 → 常浩点击 → 核对、本人在途、答中台 → 只读回读 → 桥接改卡」。
飞书发送一律换成记录器；不碰 ~/.agent-autoloop 和正式中台。"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import autoloop_answer as A  # noqa: E402
import autoloop_config as C  # noqa: E402
import autoloop_feishu as F  # noqa: E402
from test_autoloop_hub_e2e import CLI, OWNER, SKIP, HubFixture  # noqa: E402

CHAT = "oc_fixture_home"
CARD = {"id": 7, "version": 1, "ref": None, "kind": "ask", "state": "open", "title": "选一个", "text": "说明",
        "options": ["甲", "乙"], "recommend": 1, "choice": None, "answer": None, "by": None, "result": None, "rule": "规则A"}


class Lark:
    """lark-cli 记录器：发卡返回固定消息号，改卡记下来；fail_patch 时改卡报错。"""
    def __init__(self, mid="om_fixture_1", fail_patch=False):
        self.calls, self.mid, self.fail_patch = [], mid, fail_patch

    def __call__(self, args, what, cwd=None):
        self.calls.append(args)
        if args[:2] == ["im", "+messages-send"]:
            return {"message_id": self.mid}
        if args[:3] == ["im", "messages", "patch"] and self.fail_patch:
            raise F.SendError("改不了")
        return {}

    def sent(self):
        return [json.loads(a[a.index("--content") + 1]) for a in self.calls if a[:2] == ["im", "+messages-send"]]

    def keys(self):
        return [a[a.index("--idempotency-key") + 1] for a in self.calls if a[:2] == ["im", "+messages-send"]]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": self.tmp.name + "/dt/autoloop/cards.db",
                                                "DT_OWNER_OPEN_ID": OWNER, "DT_OWNER_CHAT_ID": CHAT})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.lark = Lark()
        p = mock.patch.object(F, "lark", self.lark)
        p.start()
        self.addCleanup(p.stop)

    def register(self, card=CARD):
        return F.deliver(dict(card))

    def token(self):
        btn = self.lark.sent()[-1]["body"]["elements"][1]
        return btn["behaviors"][0]["value"]["autoloop"]


class Render(Base):
    def test_open_card_buttons_carry_only_token_and_option(self):
        c = F.render(CARD, "0123456789abcdef")
        buttons = [e for e in c["body"]["elements"] if e["tag"] == "button"]
        self.assertEqual([b["behaviors"][0]["value"] for b in buttons], [{"autoloop": "0123456789abcdef", "o": 0},
                                                                         {"autoloop": "0123456789abcdef", "o": 1}])
        self.assertEqual([b["type"] for b in buttons], ["default", "primary"])            # 推荐项高亮
        self.assertEqual(c["header"]["subtitle"]["content"], "来源：规则A")
        self.assertNotIn("form", json.dumps(c))                                           # 不放文字输入框

    def test_settled_and_fyi_cards_have_no_buttons(self):
        for card in ({**CARD, "state": "decided", "choice": 0, "by": "秘书"}, {**CARD, "kind": "fyi", "options": []},
                     {**CARD, "state": "closed", "result": "不用了"}):
            c = F.render(card, "0123456789abcdef")
            self.assertFalse([e for e in c["body"]["elements"] if e["tag"] == "button"])
        self.assertIn("秘书替你选了「甲」", json.dumps(F.render({**CARD, "state": "decided", "choice": 0, "by": "秘书"}, "x"),
                                                     ensure_ascii=False))


class Deliver(Base):
    def test_first_send_registers_token_and_resend_reuses_it(self):
        self.assertEqual(self.register(), "om_fixture_1")
        tok = self.token()
        self.assertRegex(tok, r"^[0-9a-f]{16}$")
        self.register()                                              # 送达报告丢了，中台再给一次（ref 仍为空）
        self.assertEqual(self.token(), tok)
        self.assertEqual(self.lark.keys(), ["autoloop-card-7", "autoloop-card-7"])       # 同一个幂等键：飞书回原消息
        row = F.card_by_token(tok)
        self.assertEqual((row["card_id"], row["message_id"], row["chat_id"], row["options"]), (7, "om_fixture_1", CHAT, 2))
        self.assertTrue(row["delivered_at"])
        self.assertEqual(Path(os.environ["DT_AUTOLOOP_CARDS_DB"]).stat().st_mode & 0o777, 0o600)

    def test_update_patches_and_falls_back_only_for_settled_cards(self):
        self.register()
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 2, "state": "decided", "choice": 1, "by": OWNER})
        patch = [a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]][-1]
        self.assertEqual(patch[patch.index("--message-id") + 1], "om_fixture_1")
        self.lark.fail_patch = True
        with self.assertRaises(F.SendError):                         # 开着的卡改不了：报失败，不另发
            F.deliver({**CARD, "ref": "om_fixture_1", "version": 3})
        F.deliver({**CARD, "ref": "om_fixture_1", "version": 4, "state": "closed", "result": "不用了"})
        self.assertEqual(self.lark.keys()[-1], "autoloop-card-7-v4")

    def test_once_reports_delivered_or_failed(self):
        posts = []

        def hub(method, path, body=None, timeout=20):
            if method == "GET":
                return {"card": dict(CARD)}
            posts.append((path, body))
            return {}
        with mock.patch.object(F, "hub", hub):
            self.assertTrue(F.once()["ok"])
            self.lark.mid = None                                     # 发卡没拿到消息号
            F.once()
        self.assertEqual(posts[0], ("/v1/cards/7/delivered", {"version": 1, "ref": "om_fixture_1"}))
        self.assertEqual(posts[1][0], "/v1/cards/7/failed")


class Answer(Base):
    def setUp(self):
        super().setUp()
        self.register()
        self.tok = self.token()
        self.hub_calls = []
        self.hub_reply = {"changed": True}
        p = mock.patch.object(F, "hub", self.fake_hub)
        p.start()
        self.addCleanup(p.stop)
        self.read_ok = None
        p = mock.patch.object(A, "readback", lambda cid, opt, owner: self.read_ok)
        p.start()
        self.addCleanup(p.stop)

    def fake_hub(self, method, path, body=None, timeout=20):
        self.hub_calls.append((path, body))
        if isinstance(self.hub_reply, Exception):
            raise self.hub_reply
        return self.hub_reply

    def claim(self):
        with F.db() as con:
            r = con.execute("SELECT * FROM claims WHERE card_id = 7").fetchone()
        return dict(r) if r else None

    def test_owner_click_answers_with_registered_ref(self):
        code, said, rec = A.answer(self.tok, 1, CHAT, OWNER)
        self.assertEqual(code, 0, said)
        self.assertEqual(self.hub_calls, [("/v1/cards/7/answer", {"choice": 1, "by": OWNER, "ref": "om_fixture_1"})])
        self.assertEqual((self.claim()["who"], self.claim()["state"], self.claim()["choice"]), ("owner", "answered", 1))

    def test_three_checks_refuse_without_calling_hub(self):
        for args, why in (((self.tok, 0, CHAT, "ou_someone_else"), "不是常浩"), ((self.tok, 0, "oc_group", OWNER), "私聊"),
                          (("ffffffffffffffff", 0, CHAT, OWNER), "没有登记"), (("bad", 0, CHAT, OWNER), "格式"),
                          ((self.tok, 2, CHAT, OWNER), "选项号"), ((self.tok, -1, CHAT, OWNER), "选项号"),
                          ((self.tok, 0, CHAT, ""), "不是常浩")):
            code, said, _ = A.answer(*args)
            self.assertEqual(code, 1)
            self.assertIn(why, said)
        self.assertEqual(self.hub_calls, [])
        self.assertIsNone(self.claim())

    def test_card_not_yet_delivered_is_refused(self):
        with F.db(write=True) as con:                                # 先登记了令牌、发卡失败：没有送达的消息号
            con.execute("INSERT INTO feishu_cards (card_id, token, chat_id, kind, options, version) "
                        "VALUES (8, 'aaaaaaaaaaaaaaaa', ?, 'ask', 2, 1)", (CHAT,))
        code, said, _ = A.answer("aaaaaaaaaaaaaaaa", 0, CHAT, OWNER)
        self.assertEqual(code, 1)
        self.assertIn("还没发成功", said)
        self.assertEqual(self.hub_calls, [])

    def test_missing_identity_config_refuses(self):
        with mock.patch.dict(os.environ, {"DT_OWNER_CHAT_ID": "", "DT_OWNER_CHAT_FILE": self.tmp.name + "/none"}):
            code, said, _ = A.answer(self.tok, 0, "", OWNER)
            self.assertEqual(code, 1)
            self.assertIn("没配置", said)
            with self.assertRaisesRegex(F.Fail, "setup-chat"):
                F.owner_chat()
        self.assertEqual(self.hub_calls, [])

    def test_token_is_single_use(self):
        A.answer(self.tok, 0, CHAT, OWNER)
        code, said, _ = A.answer(self.tok, 1, CHAT, OWNER)
        self.assertEqual(code, 1)
        self.assertIn("已经点过", said)
        self.assertEqual(len(self.hub_calls), 1)

    def test_secretary_approved_wins_reserved_loses(self):
        with F.db(write=True) as con:
            con.execute("INSERT INTO claims (card_id, who, state, choice, at) VALUES (7, 'secretary', 'approved', 0, 'x')")
        code, said, _ = A.answer(self.tok, 1, CHAT, OWNER)
        self.assertEqual(code, 1)
        self.assertIn("秘书已经获准", said)
        self.assertEqual(self.hub_calls, [])
        with F.db(write=True) as con:
            con.execute("UPDATE claims SET state = 'reserved' WHERE card_id = 7")
        self.assertEqual(A.answer(self.tok, 1, CHAT, OWNER)[0], 0)              # 秘书只预占、没获准：本人优先
        self.assertEqual((self.claim()["who"], self.claim()["note"]), ("owner", "秘书的预占作废，本人优先"))

    def test_hub_outcomes(self):
        self.hub_reply = {"changed": False}
        self.assertEqual(A.answer(self.tok, 0, CHAT, OWNER)[0], 1)
        self.assertEqual(self.claim()["state"], "settled")
        self.reset()
        self.hub_reply = F.Fail("中台回 HTTP 403（POST /v1/cards/7/answer）")
        self.assertEqual(A.answer(self.tok, 0, CHAT, OWNER)[0], 1)
        self.assertEqual(self.claim()["state"], "rejected")
        self.reset()
        self.hub_reply = F.Fail("连不上中台（TimeoutError）")
        code, said, _ = A.answer(self.tok, 0, CHAT, OWNER)
        self.assertEqual((code, self.claim()["state"]), (2, "unknown"))
        self.assertIn("不会重答", said)
        self.reset()
        self.hub_reply, self.read_ok = {"changed": True}, "对不上"
        self.assertEqual(A.answer(self.tok, 0, CHAT, OWNER)[0], 2)
        self.assertEqual(self.claim()["state"], "unknown")

    def reset(self):
        with F.db(write=True) as con:
            con.execute("DELETE FROM claims")

    def test_main_replies_only_to_home_chat(self):
        with mock.patch.object(A, "tell") as tell, mock.patch("sys.stdout"):
            self.assertEqual(A.main([self.tok, "0", "--chat", "oc_group", "--operator", OWNER]), 1)
        self.assertEqual(tell.call_args[0][0], CHAT)
        self.assertLessEqual(len(tell.call_args[0][2]), 50)                     # 飞书幂等键上限


class ChannelKey(unittest.TestCase):
    def test_refuses_wrong_answerers_or_live_mismatch(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"DT_AUTOLOOP_CHANNEL_KEY_DIR": d, "DT_OWNER_OPEN_ID": OWNER,
                                             "DT_AUTOLOOP_LOCK": d + "/config.lock"}), \
                mock.patch.object(C, "check_target"), mock.patch.object(C, "key_names", return_value=set()), \
                mock.patch.object(C, "run_cli", return_value=(0, "k" * 40, "")) as cli:
            rules = {"channels": {"feishu": {"answerers": [OWNER, "ou_other"]}, "secretary": {"answerers": ["秘书"]}}}
            with mock.patch.object(C, "load_current", return_value=rules), mock.patch.object(C, "mismatches", return_value=[]):
                with self.assertRaisesRegex(C.Rejected, "回答人"):
                    C.issue_channel_key("feishu", True, "x")
                self.assertEqual(C.issue_channel_key("secretary", True, "x")["channel"], "secretary")
            rules["channels"]["feishu"]["answerers"] = [OWNER]
            with mock.patch.object(C, "load_current", return_value=rules), mock.patch.object(C, "mismatches", return_value=["通道 feishu"]):
                with self.assertRaisesRegex(C.Rejected, "不一致"):
                    C.issue_channel_key("feishu", True, "x")
            self.assertEqual(cli.call_count, 1)
            self.assertFalse((Path(d) / "feishu-channel.key").exists())

    def test_refuses_unknown_channel_and_missing_confirm(self):
        with self.assertRaisesRegex(C.Rejected, "只签"):
            C.issue_channel_key("desktop", True, "~/.agent-autoloop")
        with self.assertRaisesRegex(C.Rejected, "--confirm"):
            C.issue_channel_key("feishu", False, "~/.agent-autoloop")


@unittest.skipIf(SKIP, "本机没有 agent-autoloop 命令行")
class FeishuEndToEnd(HubFixture):
    COMMAND = f"{sys.executable} {HERE / 'fixtures' / 'ask_receipt.py'} {{prompt}}"

    def setUp(self):
        super().setUp()
        root = Path(self.key_file).parents[2]
        for p in (mock.patch.dict(os.environ, {"DT_AUTOLOOP_CARDS_DB": str(root / "dt/autoloop/cards.db"),
                                               "DT_AUTOLOOP_CHANNEL_KEY_DIR": str(root / "dt/autoloop"),
                                               "DT_AUTOLOOP_LOCK": str(root / "dt/autoloop/config.lock"),
                                               "DT_OWNER_CHAT_ID": CHAT}),
                  mock.patch.object(C.G, "ROSTER", self.roster)):
            p.start()
            self.addCleanup(p.stop)
        self.lark = Lark(mid="om_e2e_1")
        p = mock.patch.object(F, "lark", self.lark)
        p.start()
        self.addCleanup(p.stop)

    def wait_card(self):
        for _ in range(60):
            out = F.once(wait=1)
            if out:
                return out
        self.fail("中台没出卡片")

    def test_click_to_answer_round_trip(self):
        C.install(confirm=True, target=self.home)
        rules = json.loads(C.rules_file().read_text(encoding="utf-8"))
        next(r for r in rules["rules"] if r["name"] == "试一下")["channels"] = ["feishu"]
        C.rules_file().write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        self._wait_live(lambda live: next(r for r in live["rules"] if r["name"] == "试一下").get("channels") == ["feishu"])
        with self.assertRaisesRegex(F.Fail, "没有 feishu 通道钥匙"):
            F.once(wait=1)
        out = C.issue_channel_key("feishu", True, str(self.home))
        self.assertEqual(Path(out["file"]).stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(C.Rejected, "已经有了"):
            C.issue_channel_key("feishu", True, str(self.home))

        env = {**os.environ, "AGENT_AUTOLOOP_HOME": str(self.home)}
        import subprocess
        subprocess.run([CLI, "try"], env=env, capture_output=True, timeout=60)
        got = self.wait_card()
        self.assertEqual((got["ok"], got["ref"]), (True, "om_e2e_1"))
        tok = self.lark.sent()[-1]["body"]["elements"][1]["behaviors"][0]["value"]["autoloop"]

        self.assertEqual(A.answer(tok, 1, CHAT, "ou_intruder")[0], 1)           # 别人点：不交中台
        code, said, rec = A.answer(tok, 1, CHAT, OWNER)
        self.assertEqual(code, 0, said)
        st = C.state()
        card = next(c for c in st.get("cards", []) + st.get("recentCards", []) if c["id"] == got["card"])
        self.assertEqual((card["choice"], card["by"]), (1, OWNER))
        self.assertEqual(A.answer(tok, 0, CHAT, OWNER)[0], 1)                    # 再点：以第一次为准

        for _ in range(30):                                                     # 中台把状态变了的卡再给桥接：改原消息
            if F.once(wait=1) and any(a[:3] == ["im", "messages", "patch"] for a in self.lark.calls):
                break
            time.sleep(0.2)
        patch = [a for a in self.lark.calls if a[:3] == ["im", "messages", "patch"]]
        self.assertTrue(patch)
        self.assertIn("你选了「绿色」", patch[0][patch[0].index("--data") + 1])


if __name__ == "__main__":
    unittest.main()
