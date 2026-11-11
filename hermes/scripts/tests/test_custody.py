"""隔离自测：托管状态底座（custody.py，#131）。

状态文件、策略文件放临时目录（DT_CUSTODY_FILE / DT_CUSTODY_POLICY）；常浩的 open_id、私聊 chat_id 用环境变量给假值；
飞书回读打桩 custody.fetch_message（走真实的 twin_identity.verify_message），命令行走假 lark-cli（fake_lark.py）。
策略文件用仓库里的真模板 docs/托管策略-模板.md 填出来，模板改了这里会跟着验。
不联网、不碰 ~/Library/Application Support/DigitalTwin、不碰 ~/.hermes。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_custody.py -v
"""
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
SCRIPTS = HERE.parent
TEMPLATE = SCRIPTS.parents[1] / "docs/托管策略-模板.md"
sys.path.insert(0, str(SCRIPTS))
import custody as C  # noqa: E402

TZ = C.TZ
OWNER = "ou_fixture_owner_0001"
CHAT = "oc_fixture_p2p_0001"
D = dt.datetime(2026, 11, 11, tzinfo=TZ)        # 11-11 00:00（上海）


def at(h, m=0, day=0):
    return D + dt.timedelta(days=day, hours=h, minutes=m)


def fill(template, version=3, valid="签署日起 30 天；到期前 3 天日报提醒续签，不自动续", drain="2 小时"):
    """按常浩会怎么填来填模板：只动正文里的〔填…〕，说明文字（引用块、第 6 节）里提到的〔填〕原样留着。"""
    out, in_sign = [], False
    for line in template.splitlines():
        if line.startswith("## "):
            in_sign = line.startswith("## 6. 签署")
        if not in_sign and not line.lstrip().startswith(">"):
            line = line.replace("〔填，整数，比上一份大，不复用〕", str(version))
            line = re.sub(r"〔填，例：签署日起[^〕]*〕", valid, line)
            line = line.replace("〔填，例：2 小时〕", drain)
            line = line.replace("〔填：是 / 否〕", "是").replace("〔填：没有〕", "没有")
            line = re.sub(r"〔填，例：(\d+)〕", r"\1", line)
            line = re.sub(r"〔填，例：`docs/\*\*`、`CURRENT\.md`〕", "`docs/**`、`CURRENT.md`", line)
            line = line.replace("〔填〕", "dt-sandbox（只有 CI）" if "|" in line else "x")
        out.append(line)
    return "\n".join(out) + "\n"


def raw_msg(mid, text, when, sender=OWNER, chat=CHAT, msg_type="text"):
    """lark-cli im +messages-mget 的一条消息（2026-10-29 探针形状）。"""
    return {"message_id": mid, "chat_id": chat, "content": text, "create_time": when.astimezone().strftime("%Y-%m-%d %H:%M"),
            "deleted": False, "msg_type": msg_type,
            "sender": {"id": sender, "id_type": "open_id", "sender_type": "user"}}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-custody-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / "dt" / "custody.json"
        self.policy = self.root / "dt" / "custody" / "policy.md"
        self.policy.parent.mkdir(parents=True)
        self.template = TEMPLATE.read_text(encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DT_OWNER", "HERMES_SESSION", "DT_CUSTODY"))}
        env.update(DT_CUSTODY_FILE=str(self.state), DT_CUSTODY_POLICY=str(self.policy),
                   DT_OWNER_OPEN_ID=OWNER, DT_OWNER_CHAT_ID=CHAT)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        self.msgs = {}
        f = mock.patch.object(C, "fetch_message", side_effect=self._fetch)
        f.start()
        self.addCleanup(f.stop)
        self.n = 0

    def _fetch(self, mid):
        m = self.msgs.get(mid)
        return None if m is None else C.I.normalize(m) | {"text": m["content"] if m["msg_type"] == "text" else None}

    def say(self, text, when, **kw):
        """常浩在私聊说一句话，返回消息 id。"""
        self.n += 1
        mid = f"om_fixturemsg{self.n:04d}"
        self.msgs[mid] = raw_msg(mid, text, when, **kw)
        return mid

    def write_policy(self, **kw):
        self.policy.write_text(fill(self.template, **kw), encoding="utf-8")
        return hashlib.sha256(self.policy.read_bytes()).hexdigest()

    def sign(self, when=None, version=3, **kw):
        when = when or at(20)
        sha = self.write_policy(version=version, **kw)
        C.do_sign(self.say(f"签托管策略 v{version} {sha[:12]}", when), now=when)
        return sha

    def doc(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def rejects(self, code, fn, *a, **kw):
        with self.assertRaises(C.Rejected) as cm:
            fn(*a, **kw)
        self.assertEqual(cm.exception.code, code, str(cm.exception))
        return str(cm.exception)

    def status(self, now):
        return C.read_status(now=now)


class PolicyTemplateTests(Base):
    def test_real_template_is_unfilled_and_filled_template_parses(self):
        self.assertTrue(C.unfilled(self.template))                     # 模板原样：有未填项
        filled = fill(self.template)
        self.assertEqual(C.unfilled(filled), [])                      # 说明文字里提到〔填〕不算
        self.assertIn("〔填〕", filled)                                 # 确实还留在说明里
        fields, errors = C.parse_policy(filled)
        self.assertEqual(errors, [])
        self.assertEqual(fields, {"version": 3, "valid_days": 30, "drain_minutes": 120,
                                  "window": {"start": "23:30", "end": "08:00"}})

    def test_parse_policy_errors(self):
        _, errors = C.parse_policy(fill(self.template, valid="30 天", drain="很快"))
        self.assertEqual(len(errors), 2)
        self.assertEqual(C.parse_policy(fill(self.template, drain="90 分钟"))[0]["drain_minutes"], 90)
        self.assertTrue(C.parse_policy(fill(self.template, drain="25 小时"))[1])


class SignTests(Base):
    def test_sign_records_everything(self):
        sha = self.sign(when=at(20))
        sig = self.doc()["signature"]
        self.assertEqual((sig["version"], sig["sha256"], sig["open_id"], sig["valid_days"]), (3, sha, OWNER, 30))
        self.assertEqual(sig["message_id"], "om_fixturemsg0001")
        self.assertEqual(sig["signed_at"], C.iso(at(20)))
        self.assertEqual(sig["expires_at"], C.iso(at(20, day=30)))
        self.assertEqual(self.doc()["signed_versions"], [3])
        self.assertEqual(oct(self.state.stat().st_mode & 0o777), "0o600")
        v = C.verify_policy(self.doc(), at(21))
        self.assertTrue(v["ok"], v)
        self.assertEqual((v["version"], v["drain_minutes"]), (3, 120))

    def test_unfilled_rejected(self):
        self.policy.write_text(self.template, encoding="utf-8")
        sha = hashlib.sha256(self.policy.read_bytes()).hexdigest()
        msg = self.rejects("Unfilled", C.do_sign, self.say(f"签托管策略 v1 {sha[:12]}", at(20)), now=at(20))
        self.assertIn("未填项", msg)
        self.assertFalse(self.state.exists())                         # 拒绝什么都不写

    def test_one_leftover_placeholder_rejected(self):
        text = fill(self.template).replace("排空截止：2 小时", "排空截止：〔填，例：2 小时〕")
        self.policy.write_text(text, encoding="utf-8")
        sha = hashlib.sha256(text.encode()).hexdigest()
        self.rejects("Unfilled", C.do_sign, self.say(f"签托管策略 v3 {sha[:12]}", at(20)), now=at(20))

    def test_hash_mismatch(self):
        sha = self.write_policy()
        wrong = ("0" if sha[0] != "0" else "1") + sha[1:12]
        msg = self.rejects("HashMismatch", C.do_sign, self.say(f"签托管策略 v3 {wrong}", at(20)), now=at(20))
        self.assertIn(sha[:12], msg)

    def test_version_mismatch_between_command_and_file(self):
        sha = self.write_policy(version=3)
        self.rejects("VersionMismatch", C.do_sign, self.say(f"签托管策略 v4 {sha[:12]}", at(20)), now=at(20))

    def test_version_reuse_and_rollback_rejected(self):
        self.sign(version=3, when=at(20))
        sha = self.write_policy(version=3, drain="1 小时")              # 改了内容但不换版本
        self.rejects("VersionReuse", C.do_sign, self.say(f"签托管策略 v3 {sha[:12]}", at(20, 5)), now=at(20, 5))
        sha = self.write_policy(version=2)                             # 版本回退
        self.rejects("VersionReuse", C.do_sign, self.say(f"签托管策略 v2 {sha[:12]}", at(20, 6)), now=at(20, 6))
        self.sign(version=5, when=at(20, 7))                            # 跳号可以
        self.assertEqual(self.doc()["signed_versions"], [3, 5])
        self.assertEqual(self.doc()["signature"]["version"], 5)

    def test_bad_command_text_and_non_text(self):
        sha = self.write_policy()
        self.rejects("BadCommand", C.do_sign, self.say(f"签吧 {sha[:12]}", at(20)), now=at(20))
        self.rejects("BadCommand", C.do_sign, self.say(f"签托管策略 v3 {sha[:11]}", at(20)), now=at(20))
        self.rejects("BadCommand", C.do_sign, self.say("[图片]", at(20), msg_type="image"), now=at(20))

    def test_identity_failures(self):
        sha = self.write_policy()
        text = f"签托管策略 v3 {sha[:12]}"
        self.assertIn("不是常浩本人", self.rejects("NotAuthorized", C.do_sign, self.say(text, at(20), sender="ou_other"), now=at(20)))
        self.assertIn("chat_id", self.rejects("NotAuthorized", C.do_sign, self.say(text, at(20), chat="oc_group_x"), now=at(20)))
        self.assertIn("过时效", self.rejects("NotAuthorized", C.do_sign, self.say(text, at(19)), now=at(20)))
        self.assertIn("查不到", self.rejects("NotAuthorized", C.do_sign, "om_nosuchmessage", now=at(20)))
        self.rejects("NotAuthorized", C.do_sign, None, now=at(20))
        with mock.patch.dict(os.environ, {"DT_OWNER_OPEN_ID": "", "DT_OWNER_FILE": str(self.root / "missing")}):
            self.rejects("NotAuthorized", C.do_sign, self.say(text, at(20)), now=at(20))
        self.assertFalse(self.state.exists())

    def test_missing_policy_file(self):
        mid = self.say("签托管策略 v3 0123456789ab", at(20))
        self.rejects("PolicyMissing", C.do_sign, mid, now=at(20))

    def test_sign_only_when_off(self):
        self.sign(version=3, when=at(20))
        C.do_on("feishu", self.say("托管", at(21)), now=at(21))
        sha = self.write_policy(version=4)
        self.rejects("BadTransition", C.do_sign, self.say(f"签托管策略 v4 {sha[:12]}", at(21, 5)), now=at(21, 5))

    def test_verify_after_sign_content_changed_expired_unsigned_missing(self):
        self.assertEqual(C.verify_policy(C.empty_doc(), at(20))["code"], "unsigned")
        self.sign(when=at(20))
        self.assertTrue(C.verify_policy(self.doc(), at(20, 1))["ok"])
        self.assertEqual(C.verify_policy(self.doc(), at(20, day=30))["code"], "expired")
        self.assertTrue(C.verify_policy(self.doc(), at(19, 59, day=30))["ok"])
        self.policy.write_text(self.policy.read_text(encoding="utf-8") + " ", encoding="utf-8")   # 改一个字节
        self.assertEqual(C.verify_policy(self.doc(), at(20, 1))["code"], "changed")
        self.policy.unlink()
        self.assertEqual(C.verify_policy(self.doc(), at(20, 1))["code"], "missing")

    def test_verify_rechecks_fields_even_if_hash_matches(self):
        """签署记录被手改成指向一份字段齐全、只剩一处〔填〕的文件（哈希对得上）：verify 仍判无效，不只看哈希。"""
        text = fill(self.template).replace("| 托管合并（只按", "| 托管合并〔填〕（只按")
        self.assertEqual(C.parse_policy(text)[1], [])
        self.policy.write_text(text, encoding="utf-8")
        doc = C.empty_doc()
        doc["signature"] = {"version": 3, "sha256": hashlib.sha256(self.policy.read_bytes()).hexdigest(),
                            "expires_at": C.iso(at(0, day=9)), "signed_at": C.iso(at(0))}
        self.assertEqual(C.verify_policy(doc, at(20))["code"], "invalid")


class OnSourceTests(Base):
    def test_feishu_on_plain_runs_to_window_end(self):
        self.sign(when=at(20))
        mid = self.say("托管。", at(22))
        doc, r = C.do_on("feishu", mid, now=at(22))
        s = doc["session"]
        self.assertEqual(doc["state"], "on")
        self.assertEqual(s["until"], C.iso(at(8, day=1)))
        self.assertEqual(s["source"], {"kind": "feishu", "message_id": mid, "open_id": OWNER})
        self.assertEqual(s["policy"]["version"], 3)
        self.assertEqual(s["drain_minutes"], 120)
        self.assertIn(mid, doc["used_messages"])
        st = self.status(at(22, 1))
        self.assertEqual(st["state"], "on")
        self.assertNotIn(OWNER, json.dumps(st, ensure_ascii=False))     # 只读输出不带 open_id

    def test_feishu_on_until_parsing(self):
        cases = {"托管到 7 点": at(7, day=1), "托管到下午3点半": at(15, 30, day=1), "托管到23:30": at(23, 30),
                 "托管到晚上 11 点": at(23), "托管到明天早上8点": at(8, day=1)}
        for text, want in cases.items():
            self.assertEqual(C.parse_on_text(text, at(22, 10), {"start": "23:30", "end": "08:00"}), want, text)
        for text in ("今晚不托管", "停止托管", "托管吧", "托管到很晚", "不托管", "我回来了"):
            with self.assertRaises(C.Rejected, msg=text):
                C.parse_on_text(text, at(22), {"start": "23:30", "end": "08:00"})

    def test_feishu_on_wrong_text_rejected_and_message_not_consumed(self):
        self.sign(when=at(20))
        mid = self.say("今晚不托管", at(22))
        self.rejects("BadCommand", C.do_on, "feishu", mid, now=at(22))
        self.assertEqual(self.doc()["state"], "off")
        self.assertNotIn(mid, self.doc()["used_messages"])

    def test_feishu_on_identity_checked(self):
        self.sign(when=at(20))
        self.rejects("NotAuthorized", C.do_on, "feishu", self.say("托管", at(22), sender="ou_other"), now=at(22))
        self.rejects("NotAuthorized", C.do_on, "feishu", self.say("托管", at(22), chat="oc_group_x"), now=at(22))
        self.rejects("NotAuthorized", C.do_on, "feishu", self.say("托管", at(21, 40)), now=at(22))
        self.rejects("NotAuthorized", C.do_on, "feishu", None, now=at(22))
        self.assertEqual(self.doc()["state"], "off")

    def test_other_sources_cannot_open(self):
        self.sign(when=at(20))
        for src in ("yichuang", "local", "hermes", "ledger", ""):
            self.rejects("NotAuthorized", C.do_on, src, now=at(23, 40))
        self.assertEqual(self.doc()["state"], "off")

    def test_on_needs_valid_policy(self):
        self.rejects("PolicyInvalid", C.do_on, "feishu", self.say("托管", at(22)), now=at(22))     # 没签
        self.sign(when=at(20))
        self.policy.write_text(self.policy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.rejects("PolicyInvalid", C.do_on, "feishu", self.say("托管", at(22)), now=at(22))     # 被改
        self.write_policy()                                                                    # 改回原样
        self.rejects("PolicyInvalid", C.do_on, "window", now=at(23, 40, day=30))                   # 过期

    def test_window_on(self):
        self.sign(when=at(20))
        self.rejects("NotAuthorized", C.do_on, "window", now=at(22))                    # 时段外
        self.rejects("BadCommand", C.do_on, "window", "om_fixturemsg0001", now=at(23, 40))
        doc, _ = C.do_on("window", now=at(23, 40))
        self.assertEqual(doc["session"]["source"], {"kind": "window", "window": "2026-11-11"})
        self.assertEqual(doc["session"]["until"], C.iso(at(8, day=1)))
        C.do_off("feishu", now=at(1, day=1))                                            # 半夜关了
        C.do_off("ledger", drained=True, now=at(1, 30, day=1))
        msg = self.rejects("NotAuthorized", C.do_on, "window", now=at(2, day=1))          # 这个时段不再自动开
        self.assertIn("2026-11-11", msg)
        C.do_on("window", now=at(23, 31, day=1))                                        # 下一晚照常

    def test_window_on_after_midnight_uses_previous_start(self):
        self.sign(when=at(20))
        doc, _ = C.do_on("window", now=at(3, day=1))
        self.assertEqual(doc["session"]["source"]["window"], "2026-11-11")

    def test_skip_tonight(self):
        self.sign(when=at(20))
        doc, r = C.do_off("feishu", skip_tonight=True, now=at(21))          # 关着时说「今晚不托管」
        self.assertTrue(r["unchanged"])
        self.assertIn("2026-11-11", doc["consumed_windows"])
        self.rejects("NotAuthorized", C.do_on, "window", now=at(23, 40))
        C.do_on("feishu", self.say("托管", at(23, 45)), now=at(23, 45))    # 常浩亲口开不受影响


class TransitionTests(Base):
    def setUp(self):
        super().setUp()
        self.sign(when=at(20))

    def open(self, when=None):
        when = when or at(22)
        return C.do_on("feishu", self.say("托管到 7 点", when), now=when)

    def test_on_rejected_when_not_off(self):
        self.open()
        self.rejects("BadTransition", C.do_on, "feishu", self.say("托管", at(22, 5)), now=at(22, 5))      # on
        C.do_off("yichuang", now=at(22, 10))
        self.rejects("BadTransition", C.do_on, "feishu", self.say("托管", at(22, 15)), now=at(22, 15))    # draining
        C.do_freeze("ledger", "合并对账不清", ["dt-sandbox#7"], now=at(22, 20))
        self.rejects("BadTransition", C.do_on, "feishu", self.say("托管", at(22, 25)), now=at(22, 25))    # frozen

    def test_off_any_source_on_to_draining(self):
        self.open()
        doc, r = C.do_off("驿窗", now=at(22, 10))
        self.assertEqual(doc["state"], "draining")
        self.assertEqual(doc["session"]["drain"], {"started_at": C.iso(at(22, 10)), "deadline": C.iso(at(0, 10, day=1)),
                                                   "source": "驿窗"})
        doc, r = C.do_off("feishu", now=at(22, 20))                        # 排空中再关：不变，截止不顺延
        self.assertTrue(r["unchanged"])
        self.assertEqual(doc["session"]["drain"]["deadline"], C.iso(at(0, 10, day=1)))
        self.rejects("BadCommand", C.do_off, "bad label!", now=at(22, 30))

    def test_off_when_off_is_noop(self):
        doc, r = C.do_off("feishu", now=at(21))
        self.assertEqual((doc["state"], r["unchanged"]), ("off", True))

    def test_drained_only_from_draining(self):
        self.rejects("BadTransition", C.do_off, "ledger", drained=True, now=at(21))          # off
        self.open()
        self.rejects("BadTransition", C.do_off, "ledger", drained=True, now=at(22, 5))       # on：不能跳过排空
        C.do_off("feishu", now=at(22, 10))
        doc, r = C.do_off("ledger", drained=True, now=at(22, 30))
        self.assertEqual((doc["state"], doc["session"]), ("off", None))
        last = doc["history"][-1]
        self.assertEqual(last["ended"]["how"], "排空完成")
        self.assertEqual([(e["from"], e["to"]) for e in last["events"]], [("off", "on"), ("on", "draining"), ("draining", "off")])

    def test_until_auto_drains_then_deadline_freezes(self):
        self.open()                                                        # 托管到 07:00，排空 2 小时
        self.assertEqual(self.status(at(6, 59, day=1))["state"], "on")
        st = self.status(at(7, day=1))
        self.assertEqual(st["state"], "draining")
        self.assertEqual(st["session"]["drain"]["deadline"], C.iso(at(9, day=1)))
        self.assertEqual(self.doc()["state"], "on")                        # 只读：文件没变
        st = self.status(at(9, day=1))
        self.assertEqual(st["state"], "frozen")
        self.assertIn("排空截止已过", st["session"]["frozen"]["reason"])
        self.rejects("BadTransition", C.do_off, "ledger", drained=True, now=at(9, 1, day=1))   # 截止后不能再宣称收完
        self.assertEqual(self.doc()["state"], "frozen")                    # 拒绝也把时钟推进落了盘

    def test_drained_before_deadline_reaches_off(self):
        self.open()
        C.do_off("ledger", drained=True, now=at(8, day=1))                # 07:00 自动排空，08:00 收完
        self.assertEqual(self.doc()["state"], "off")
        self.assertEqual(self.doc()["history"][-1]["drain"]["source"], "到点（until）")

    def test_freeze(self):
        self.rejects("BadTransition", C.do_freeze, "ledger", "x", now=at(21))                 # off
        self.open()
        doc, _ = C.do_freeze("ledger", "合并对账不清", ["dt-sandbox#7 HEAD abc1234 对账不清"], now=at(22, 5))  # on → frozen
        self.assertEqual(doc["state"], "frozen")
        self.assertEqual(doc["session"]["frozen"]["items"], ["dt-sandbox#7 HEAD abc1234 对账不清"])
        doc, r = C.do_freeze("ledger", "又一次", now=at(22, 6))
        self.assertTrue(r["unchanged"])
        self.assertEqual(doc["session"]["frozen"]["reason"], "合并对账不清")
        self.rejects("BadCommand", C.do_freeze, "ledger", "  ", now=at(22, 7))

    def test_draining_to_frozen(self):
        self.open()
        C.do_off("feishu", now=at(22, 5))
        doc, _ = C.do_freeze("ledger", "排空截止到，合并没对上", now=at(23))
        self.assertEqual(doc["state"], "frozen")

    def test_frozen_only_unfreeze(self):
        self.open()
        C.do_freeze("ledger", "合并对账不清", now=at(22, 5))
        self.rejects("BadTransition", C.do_off, "feishu", now=at(22, 6))
        self.rejects("BadTransition", C.do_off, "ledger", drained=True, now=at(22, 6))
        self.rejects("BadTransition", C.do_on, "feishu", self.say("托管", at(22, 6)), now=at(22, 6))
        self.rejects("BadTransition", C.do_on, "window", now=at(23, 40))
        self.rejects("BadCommand", C.do_unfreeze, self.say("解冻吧", at(22, 7)), now=at(22, 7))
        self.rejects("NotAuthorized", C.do_unfreeze, self.say("解除冻结", at(22, 7), sender="ou_other"), now=at(22, 7))
        self.rejects("NotAuthorized", C.do_unfreeze, None, now=at(22, 7))
        self.assertEqual(self.doc()["state"], "frozen")
        mid = self.say("解除冻结", at(22, 8))
        doc, _ = C.do_unfreeze(mid, now=at(22, 8))
        self.assertEqual((doc["state"], doc["session"]), ("off", None))
        self.assertEqual(doc["history"][-1]["ended"]["how"], "解除冻结")
        self.open(at(22, 10))                                              # 解除后可以重新开

    def test_unfreeze_rejected_unless_frozen(self):
        self.rejects("BadTransition", C.do_unfreeze, self.say("解除冻结", at(21)), now=at(21))
        self.open()
        self.rejects("BadTransition", C.do_unfreeze, self.say("解除冻结", at(22, 1)), now=at(22, 1))
        C.do_off("feishu", now=at(22, 2))
        self.rejects("BadTransition", C.do_unfreeze, self.say("解除冻结", at(22, 3)), now=at(22, 3))

    def test_auto_frozen_session_unfreezes(self):
        self.open()
        C.do_unfreeze(self.say("解除冻结", at(9, 30, day=1)), now=at(9, 30, day=1))   # 读到的是自动冻结
        self.assertEqual(self.doc()["history"][-1]["frozen"]["source"], "到点（排空截止）")


class ReplayTests(Base):
    def test_message_used_once_across_commands(self):
        self.sign(when=at(20))
        sign_mid = "om_fixturemsg0001"
        self.msgs[sign_mid]["content"] = "托管"          # 同一条消息改口令再用：仍然拒绝（以 id 为准）
        self.assertEqual(self.rejects("Replay", C.do_on, "feishu", sign_mid, now=at(20, 5)).count("已经用过"), 1)
        mid = self.say("托管", at(22))
        C.do_on("feishu", mid, now=at(22))
        C.do_off("feishu", now=at(22, 1))
        C.do_off("ledger", drained=True, now=at(22, 2))
        self.rejects("Replay", C.do_on, "feishu", mid, now=at(22, 3))
        C.do_on("feishu", self.say("托管", at(22, 4)), now=at(22, 4))
        C.do_freeze("ledger", "x", now=at(22, 5))
        un = self.say("解除冻结", at(22, 6))
        C.do_unfreeze(un, now=at(22, 6))
        C.do_on("feishu", self.say("托管", at(22, 7)), now=at(22, 7))
        C.do_freeze("ledger", "y", now=at(22, 8))
        self.rejects("Replay", C.do_unfreeze, un, now=at(22, 9))


class FileTests(Base):
    def test_corrupt_file_refuses_writes_and_status_reports(self):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text("{bad", encoding="utf-8")
        st = self.status(at(21))
        self.assertIsNone(st["state"])
        self.assertTrue(st["error"])
        self.rejects("Corrupt", C.do_off, "feishu", now=at(21))
        self.state.write_text(json.dumps({**C.empty_doc(), "state": "frozen"}), encoding="utf-8")   # frozen 却没 session
        self.rejects("Corrupt", C.do_off, "feishu", now=at(21))

    def test_no_temp_files_left_and_lock_timeout(self):
        self.sign(when=at(20))
        self.assertEqual(sorted(p.name for p in self.state.parent.iterdir()),
                         ["custody", "custody.json", "custody.json.lock"])
        import fcntl
        fd = os.open(str(self.state.with_name("custody.json.lock")), os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with mock.patch.object(C, "LOCK_WAIT", 0.2):
                with self.assertRaises(C.Rejected) as cm:
                    with C.locked(wait=0.2):
                        pass
            self.assertEqual(cm.exception.code, "Locked")
        finally:
            os.close(fd)

    def test_concurrent_window_on_only_one_wins(self):
        """8 个进程同时在时段内开启：锁保证只有一个成功，其余见到 on 被拒。"""
        self.sign(when=at(20))
        code = ("import sys, datetime as dt; sys.path.insert(0, sys.argv[1]); import custody as C\n"
                "now = dt.datetime.fromisoformat(sys.argv[2])\n"
                "try:\n    C.do_on('window', now=now); print('ok')\n"
                "except C.Rejected as e:\n    print(e.code)\n")
        procs = [subprocess.Popen([sys.executable, "-B", "-c", code, str(SCRIPTS), C.iso(at(23, 40))],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=os.environ.copy())
                 for _ in range(8)]
        outs = [p.communicate(timeout=60) for p in procs]
        results = sorted(o.strip() for o, _ in outs)
        self.assertEqual(results.count("ok"), 1, outs)
        self.assertEqual(results.count("BadTransition") + results.count("NotAuthorized"), 7, outs)
        self.assertEqual(self.doc()["state"], "on")


class CliTests(Base):
    """命令行全程走假 lark-cli：口令从回读的原文里取。"""

    def setUp(self):
        super().setUp()
        self.fake = self.root / "fake"
        self.fake.mkdir()
        self.env = {**os.environ, "DT_LARK_CLI": str(HERE / "fake_lark.py"), "FAKE_LARK_DIR": str(self.fake)}

    def publish(self):
        (self.fake / "state.json").write_text(json.dumps({"mget": self.msgs}), encoding="utf-8")

    def cli(self, *args, ok=True, env=None):
        self.publish()
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "custody.py"), *args],
                           capture_output=True, text=True, env=env or self.env)
        self.assertEqual(r.returncode, 0 if ok else 1, r.stdout + r.stderr)
        self.assertNotIn(OWNER, r.stdout + r.stderr)
        self.assertNotIn(CHAT, r.stdout + r.stderr)
        if args[0] == "status" and "--json" in args:
            return json.loads(r.stdout)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["ok"], ok, out)
        return out

    def test_roundtrip(self):
        now = C.now_tz()
        self.assertIn("还没签", self.cli("verify", ok=False)["msg"])
        sha = self.write_policy()
        out = self.cli("sign", "--message-id", self.say(f"签托管策略 v3 {sha[:12]}", now))
        self.assertEqual(out["sha256"], sha)
        self.assertTrue(self.cli("verify")["ok"])
        mid = self.say("托管到 23 点" if now.hour < 22 else "托管", now)
        self.assertEqual(self.cli("on", "--source", "feishu", "--message-id", mid)["state"], "on")
        self.assertEqual(self.cli("on", "--source", "feishu", "--message-id", mid, ok=False)["error"], "BadTransition")
        st = self.cli("status", "--json")
        self.assertEqual((st["state"], st["policy"]["ok"], st["session"]["source"]["open_id"]), ("on", True, "已核验（不显示）"))
        self.assertIn("托管开着", self.cli("status")["msg"])
        self.assertEqual(self.cli("off", "--source", "yichuang")["state"], "draining")
        self.assertEqual(self.cli("freeze", "--source", "ledger", "--reason", "对账不清", "--item", "dt-sandbox#7")["state"], "frozen")
        self.assertIn("已冻结", self.cli("status")["msg"])
        self.assertEqual(self.cli("off", "--source", "feishu", ok=False)["error"], "BadTransition")
        env = {**self.env, "HERMES_SESSION_MESSAGE_ID": self.say("解除冻结", now)}     # 会话里的消息 id 只是指针
        self.assertEqual(self.cli("unfreeze", env=env)["state"], "off")
        self.assertEqual(self.cli("on", "--source", "yichuang", ok=False)["error"], "NotAuthorized")

    def test_status_reports_corrupt(self):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text("[]", encoding="utf-8")
        self.assertTrue(self.cli("status", "--json", ok=False)["error"])
        self.assertEqual(self.cli("off", "--source", "feishu", ok=False)["error"], "Corrupt")


if __name__ == "__main__":
    unittest.main()
