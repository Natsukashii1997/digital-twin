"""隔离自测：秘书开关与代批清单签署（secretary.py，#195 批 0B-4b）。

状态文件、清单、账本库、原文目录全放临时目录（DT_SECRETARY_FILE / DT_SECRETARY_POLICY / DT_SECRETARY_DIR / DT_STATE_DB，
托管的 DT_CUSTODY_* 也指到临时目录）；常浩的 open_id、私聊 chat_id 用环境变量给假值；飞书回读打桩 custody.fetch_message
（走真实的 twin_identity.verify_message）；名册打桩 secretary.dispatch_projects（读名册本身的用例打桩 multica_night.committed_json）。
清单用仓库里的真模板 docs/代批清单-模板.md 填出来，模板改了这里会跟着验。
不联网、不碰 ~/Library/Application Support/DigitalTwin、不碰 ~/.hermes、不碰 ~/.agent-autoloop。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_secretary.py -v
"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import datetime as dt
import fcntl
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
TEMPLATE = SCRIPTS.parents[1] / "docs/代批清单-模板.md"
sys.path.insert(0, str(SCRIPTS))
import custody as C  # noqa: E402
import secretary as S  # noqa: E402
import secretary_ledger as SL  # noqa: E402
import twin_state as TS  # noqa: E402

REAL_DISPATCH = S.dispatch_projects                 # setUp 里会打桩；读名册本身的用例用真的

TZ = C.TZ
OWNER = "ou_fixture_owner_0001"
CHAT = "oc_fixture_p2p_0001"
D = dt.datetime(2026, 11, 18, tzinfo=TZ)        # 11-18 00:00（上海）
REPO = "Natsukashii1997/dt-sandbox"
ROSTER = {"dt-sandbox": REPO, "niuma-home": "Natsukashii1997/niuma-home"}
WL = ((REPO, "只有 CI，没有部署、发布、外部通知"),)
ISOLATE = ("DT_OWNER", "HERMES_SESSION", "DT_CUSTODY", "DT_SECRETARY", "DT_STATE_DB", "DT_ROSTER", "DT_NIGHT")


def at(h, m=0, day=0):
    return D + dt.timedelta(days=day, hours=h, minutes=m)


def fill(template, version=3, valid="签署日起 30 天；到期前 3 天日报提醒续签，不自动续", cats=("是", "是", "是", "是"),
         projects=("dt-sandbox",), day="10", task="3", merge_day="2", whitelist=WL, drain="2 小时"):
    """按常浩会怎么填来填模板：只动正文里的〔填…〕，引用块和第 6 节里提到的〔填〕原样留着。"""
    out, sec = [], ""
    for line in template.splitlines():
        if line.startswith("## "):
            sec = line
        if sec.startswith("## 6. 签署") or line.lstrip().startswith(">"):
            out.append(line)
            continue
        line = line.replace("〔填，整数，比上一份大，不复用〕", str(version))
        line = re.sub(r"〔填，例：签署日起[^〕]*〕", valid, line)
        for mark, v in zip(("① 提问卡", "② 放弃后重派一次", "③ 退回 PR", "④ 合并 PR"), cats):
            if mark in line:
                line = line.replace("〔填：是 / 否〕", v)
        if line.startswith("- 项目：〔填"):
            out += [f"- 项目：{p}" for p in projects]
            continue
        if line.startswith("| 〔填，例：Natsukashii1997"):
            out += [f"| {r} | {n} |" for r, n in whitelist]
            continue
        line = line.replace("〔填，例：10〕", day).replace("〔填，例：3〕", task).replace("〔填，例：2〕", merge_day)
        line = line.replace("〔填，例：2 小时〕", drain)
        out.append(line)
    return "\n".join(out) + "\n"


def raw_msg(mid, text, when, sender=OWNER, chat=CHAT):
    """lark-cli im +messages-mget 的一条消息（同 test_custody）。"""
    return {"message_id": mid, "chat_id": chat, "content": text, "create_time": when.astimezone().strftime("%Y-%m-%d %H:%M"),
            "deleted": False, "msg_type": "text", "sender": {"id": sender, "id_type": "open_id", "sender_type": "user"}}


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-secretary-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        dtd = self.root / "dt"
        self.state = dtd / "secretary.json"
        self.policy = dtd / "secretary" / "policy.md"
        self.policy.parent.mkdir(parents=True)
        self.db = dtd / "twin-state.db"
        self.template = TEMPLATE.read_text(encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(ISOLATE)}
        env.update(DT_SECRETARY_FILE=str(self.state), DT_SECRETARY_POLICY=str(self.policy), DT_SECRETARY_DIR=str(dtd / "secretary"),
                   DT_STATE_DB=str(self.db), DT_CUSTODY_FILE=str(dtd / "custody.json"), DT_CUSTODY_POLICY=str(dtd / "custody" / "policy.md"),
                   DT_CUSTODY_DIR=str(dtd / "custody"), DT_OWNER_OPEN_ID=OWNER, DT_OWNER_CHAT_ID=CHAT)
        p = mock.patch.dict(os.environ, env, clear=True)
        p.start()
        self.addCleanup(p.stop)
        for f in (S.state_path(), S.policy_path(), SL.raw_dir(), Path(os.environ["DT_STATE_DB"])):   # 隔离自检：全在临时目录
            self.assertTrue(str(f).startswith(str(self.root)), f)
        TS.Store(self.db).open().close()                    # 账本所在的库要先有（不自动新建）
        self.roster = dict(ROSTER)
        p = mock.patch.object(S, "dispatch_projects", side_effect=lambda: dict(self.roster))
        p.start()
        self.addCleanup(p.stop)
        self.msgs = {}
        p = mock.patch.object(C, "fetch_message", side_effect=self._fetch)
        p.start()
        self.addCleanup(p.stop)
        self.n = 0

    def _fetch(self, mid):
        m = self.msgs.get(mid)
        return None if m is None else C.I.normalize(m) | {"text": m["content"]}

    def say(self, text, when, **kw):
        self.n += 1
        mid = f"om_fixturemsg{self.n:04d}"
        self.msgs[mid] = raw_msg(mid, text, when, **kw)
        return mid

    def write_policy(self, text=None, **kw):
        self.policy.write_text(text if text is not None else fill(self.template, **kw), encoding="utf-8")
        return hashlib.sha256(self.policy.read_bytes()).hexdigest()

    def sign(self, when=None, version=3, **kw):
        when = when or at(9)
        sha = self.write_policy(version=version, **kw)
        S.do_sign(self.say(f"签代批清单 v{version} {sha[:12]}", when), now=when)
        return sha

    def turn_on(self, when=None):
        when = when or at(10)
        S.do_on(self.say("开秘书", when), now=when)
        return S.load()["session"]

    def doc(self):
        return json.loads(self.state.read_text(encoding="utf-8"))

    def rejects(self, code, fn, *a, **kw):
        with self.assertRaises(C.Rejected) as cm:
            fn(*a, **kw)
        self.assertEqual(cm.exception.code, code, str(cm.exception))
        return str(cm.exception)

    def errors_of(self, text):
        return S.parse_policy(text)[1]


class PolicyTemplateTests(Base):
    def test_real_template_is_unfilled_and_filled_template_parses(self):
        self.assertTrue(S.C.unfilled(self.template, S.EXEMPT_LINES))
        filled = fill(self.template)
        self.assertEqual(C.unfilled(filled, S.EXEMPT_LINES), [])
        self.assertIn("〔填〕", filled)                    # 两行固定说明里还留着
        self.assertTrue(C.unfilled(filled))               # 用托管的豁免行就不算填完：两套模板各认各的
        fields, errors = S.parse_policy(filled)
        self.assertEqual(errors, [])
        self.assertEqual(fields, {"version": 3, "valid_days": 30,
                                  "categories": {"ask": True, "redispatch": True, "reject": True, "merge": True},
                                  "projects": ["dt-sandbox"], "limits": {"day": 10, "task": 3, "merge_day": 2, "redispatch": 1},
                                  "whitelist": {REPO: "只有 CI，没有部署、发布、外部通知"}, "wait_minutes": 30, "drain_minutes": 120})

    def test_template_itself_cannot_be_signed(self):
        sha = self.write_policy(self.template)
        msg = self.rejects("Unfilled", S.do_sign, self.say(f"签代批清单 v1 {sha[:12]}", at(9)), now=at(9))
        self.assertIn("未填项", msg)
        self.assertFalse(self.state.exists())

    def test_quoted_lines_do_not_count(self):
        filled = fill(self.template)
        extra = filled.replace("## 3. 额度\n", "## 3. 额度\n> 每天代批总数：99 次\n> - 项目：niuma-home\n")
        self.assertEqual(S.parse_policy(extra)[0]["limits"]["day"], 10)
        self.assertEqual(self.errors_of(extra), [])

    def test_each_field_missing_duplicate_or_out_of_range_is_rejected(self):
        good = fill(self.template)
        line = {
            "版本": "| 版本 | 3 |", "有效期": "| 有效期 | 签署日起 30 天；到期前 3 天日报提醒续签，不自动续 |",
            "①": "| ① 提问卡 | 是 |", "②": "| ② 放弃后重派一次 | 是 |", "③": "| ③ 退回 PR | 是 |", "④": "| ④ 合并 PR | 是 |",
            "项目": "- 项目：dt-sandbox", "每天": "- 每天代批总数：10 次", "每张": "- 每张任务最多代批：3 次",
            "合并": "- 每天合并：2 个", "重派": "- 放弃后重派：每张任务 1 次（规格定的，只能是 1）",
            "白名单": f"| {REPO} | 只有 CI，没有部署、发布、外部通知 |",
            "等": "- 白天先等：30 分钟（从飞书成功送达起算；托管时段不等）",
            "排空": "- 排空截止：2 小时。到点没收完就冻结并立即报告常浩，常浩说「解除冻结」才结束。",
        }
        for k, v in line.items():
            self.assertIn(v + "\n", good, k)
        cases = {f"缺{k}": good.replace(v + "\n", "") for k, v in line.items() if k != "白名单"}
        cases.update({f"重复{k}": good.replace(v + "\n", v + "\n" + v + "\n") for k, v in line.items()})
        cases.update({
            "版本不是整数": good.replace("| 版本 | 3 |", "| 版本 | 三 |"),
            "有效期 0 天": good.replace("签署日起 30 天", "签署日起 0 天"),
            "有效期 367 天": good.replace("签署日起 30 天", "签署日起 367 天"),
            "① 写可以": good.replace("| ① 提问卡 | 是 |", "| ① 提问卡 | 可以 |"),
            "项目名乱写": good.replace("- 项目：dt-sandbox", "- 项目：dt sandbox!"),
            "每天 0": good.replace("每天代批总数：10 次", "每天代批总数：0 次"),
            "每天 101": good.replace("每天代批总数：10 次", "每天代批总数：101 次"),
            "每张 0": good.replace("每张任务最多代批：3 次", "每张任务最多代批：0 次"),
            "合并 101": good.replace("每天合并：2 个", "每天合并：101 个"),
            "重派 2": good.replace("每张任务 1 次", "每张任务 2 次"),
            "重派 0": good.replace("每张任务 1 次", "每张任务 0 次"),
            "白名单行看不懂": good.replace(f"| {REPO} |", "| dt-sandbox |"),
            "白名单没写说明": good.replace(f"| {REPO} | 只有 CI，没有部署、发布、外部通知 |", f"| {REPO} |  |"),
            "白名单没表头": good.replace("| 仓库 | 合并到 main 触发的工作流 / 外部动作 |\n|---|---|\n", ""),
            "等 0 分钟": good.replace("白天先等：30 分钟", "白天先等：0 分钟"),
            "等 241 分钟": good.replace("白天先等：30 分钟", "白天先等：241 分钟"),
            "等 31 分钟": good.replace("白天先等：30 分钟", "白天先等：31 分钟"),
            "排空 0": good.replace("排空截止：2 小时", "排空截止：0 分钟"),
            "排空 25 小时": good.replace("排空截止：2 小时", "排空截止：25 小时"),
            "少一节": good.replace("## 5. 等待与排空", "## 5. 等待"),
            "多一节": good + "\n## 2. 项目\n\n- 项目：niuma-home\n",
        })
        for name, text in cases.items():
            with self.subTest(name):
                self.assertNotEqual(text, good)
                self.assertTrue(self.errors_of(text), name)

    def test_merge_on_needs_whitelist_and_off_allows_empty(self):
        self.assertIn("④ 合并 PR 填了「是」，合并白名单至少要有一行", self.errors_of(fill(self.template, whitelist=())))
        fields, errors = S.parse_policy(fill(self.template, cats=("是", "是", "是", "否"), whitelist=()))
        self.assertEqual((errors, fields["whitelist"], fields["categories"]["merge"]), ([], {}, False))

    def test_roster_checks(self):
        fields = S.parse_policy(fill(self.template, projects=("dt-sandbox", "niuma-home")))[0]
        self.assertEqual(S.check_roster(fields, ROSTER), [])
        self.assertTrue(S.check_roster(fields, {"dt-sandbox": REPO}))                                  # 项目不在名册
        f2 = S.parse_policy(fill(self.template, whitelist=(("Natsukashii1997/niuma-home", "只有 CI"),)))[0]
        self.assertTrue(S.check_roster(f2, ROSTER))                                                   # 白名单不是清单项目的仓库
        f3 = S.parse_policy(fill(self.template, whitelist=(("someone/dt-sandbox", "只有 CI"),)))[0]
        self.assertTrue(S.check_roster(f3, ROSTER))                                                   # 同名不同主人

    def test_dispatch_projects_reads_committed_roster_strictly(self):
        roster = {"projects": [
            {"github": "o/ok", "write_allowed": True, "category": "活跃"},
            {"github": "o/nofield", "category": "活跃"},
            {"github": "o/str", "write_allowed": "true", "category": "活跃"},
            {"github": "o/paused", "write_allowed": True, "category": "暂停"},
            {"github": None, "write_allowed": True, "category": "活跃"},
        ], "inventory": [
            {"path": "github:o/sandbox", "sandbox": True},
            {"path": "github:o/strsandbox", "sandbox": "true"},
            {"path": "~/local", "sandbox": True},
            {"path": "github:invalid", "sandbox": True},            # 不是 owner/repo：不算（第 2 轮 P1-05）
            {"path": "github:o/bad name", "sandbox": True},
        ]}
        import multica_night as MN
        with mock.patch.object(MN, "committed_json", return_value=roster) as cj:
            self.assertEqual(REAL_DISPATCH(), {"ok": "o/ok", "sandbox": "o/sandbox"})
        cj.assert_called_once_with(MN.ROSTER_FILE)


class SignTests(Base):
    def test_sign_records_signature_and_verify_returns_fields(self):
        sha = self.sign()
        sig = self.doc()["signature"]
        self.assertEqual((sig["version"], sig["sha256"], sig["valid_days"], sig["open_id"]), (3, sha, 30, OWNER))
        self.assertEqual(oct(self.state.stat().st_mode & 0o777), "0o600")
        v = S.verify_policy(S.load(), at(11))
        self.assertTrue(v["ok"], v)
        self.assertEqual((v["repos"], v["limits"]["day"], v["wait_minutes"]), ({"dt-sandbox": REPO}, 10, 30))

    def test_sign_rejections_write_nothing(self):
        sha = self.write_policy()
        cases = [
            ("BadCommand", self.say(f"签托管策略 v3 {sha[:12]}", at(9))),
            ("BadCommand", self.say(f"签代批清单 3 {sha[:12]}", at(9))),
            ("NotAuthorized", self.say(f"签代批清单 v3 {sha[:12]}", at(9), sender="ou_someone_else")),
            ("NotAuthorized", self.say(f"签代批清单 v3 {sha[:12]}", at(9), chat="oc_some_group")),
            ("NotAuthorized", self.say(f"签代批清单 v3 {sha[:12]}", at(8, 45))),          # 15 分钟前（时效 10 分钟 + 2 分钟偏差）
            ("VersionMismatch", self.say(f"签代批清单 v4 {sha[:12]}", at(9))),
            ("HashMismatch", self.say(f"签代批清单 v3 {'0' * 12}", at(9))),
        ]
        for code, mid in cases:
            with self.subTest(code=code, mid=mid):
                self.rejects(code, S.do_sign, mid, now=at(9))
                self.assertFalse(self.state.exists())

    def test_replay_and_version_must_increase(self):
        sha = self.write_policy()
        mid = self.say(f"签代批清单 v3 {sha[:12]}", at(9))
        S.do_sign(mid, now=at(9))
        self.rejects("Replay", S.do_sign, mid, now=at(9, 1))
        self.rejects("VersionReuse", S.do_sign, self.say(f"签代批清单 v3 {sha[:12]}", at(9, 2)), now=at(9, 2))
        sha2 = self.write_policy(version=2)
        self.rejects("VersionReuse", S.do_sign, self.say(f"签代批清单 v2 {sha2[:12]}", at(9, 3)), now=at(9, 3))
        self.sign(when=at(9, 4), version=4)
        self.assertEqual(self.doc()["signed_versions"], [3, 4])

    def test_invalid_inventory_repo_name_cannot_be_signed_into_state(self):
        """第 2 轮 P1-05：名册 inventory 里 path 是 github:invalid 的沙盒不算可代批项目，清单写它签不了，状态文件不动。"""
        import multica_night as MN
        roster = {"projects": [], "inventory": [{"path": "github:invalid", "sandbox": True}, {"path": "github:" + REPO, "sandbox": True}]}
        S.dispatch_projects.side_effect = lambda: REAL_DISPATCH()
        sha = self.write_policy(projects=("dt-sandbox", "invalid"))
        with mock.patch.object(MN, "committed_json", return_value=roster):
            self.rejects("RosterMismatch", S.do_sign, self.say(f"签代批清单 v3 {sha[:12]}", at(9)), now=at(9))
        self.assertFalse(self.state.exists())
        self.assertFalse(C.marker_path(self.state).exists())

    def test_state_that_would_not_load_back_is_never_written(self):
        """第 2 轮 P1-05 的第二道防线：fn 改出了结构不合格的状态，落盘前就拒绝，原文件不变、读得回来。"""
        self.sign()
        before = self.state.read_bytes()

        def corrupt(doc):
            doc["signature"]["projects"] = {"dt-sandbox": "invalid"}
        self.rejects("Corrupt", S.transaction, corrupt, now=at(10))
        self.assertEqual(self.state.read_bytes(), before)
        S.load()

    def test_roster_mismatch_or_unreadable_blocks_signing(self):
        sha = self.write_policy(projects=("dt-sandbox", "ghost"))
        self.rejects("RosterMismatch", S.do_sign, self.say(f"签代批清单 v3 {sha[:12]}", at(9)), now=at(9))
        sha = self.write_policy()
        S.dispatch_projects.side_effect = RuntimeError("git 不在")
        self.rejects("RosterUnavailable", S.do_sign, self.say(f"签代批清单 v3 {sha[:12]}", at(9)), now=at(9))
        self.assertFalse(self.state.exists())

    def test_changed_file_revoked_expired_and_roster_drift_invalidate(self):
        sha = self.sign()
        original = self.policy.read_bytes()
        self.policy.write_bytes(original + b"\n")
        self.assertEqual(S.verify_policy(S.load(), at(11))["code"], "changed")
        self.policy.write_bytes(original)
        self.assertTrue(S.verify_policy(S.load(), at(11))["ok"])
        self.roster.pop("dt-sandbox")
        self.assertEqual(S.verify_policy(S.load(), at(11))["code"], "roster_changed")      # 项目被移出名册
        self.roster["dt-sandbox"] = REPO
        S.dispatch_projects.side_effect = RuntimeError("git 不在")
        self.assertEqual(S.verify_policy(S.load(), at(11))["code"], "roster")
        S.dispatch_projects.side_effect = lambda: dict(self.roster)
        self.assertEqual(S.verify_policy(S.load(), at(0, day=31))["code"], "expired")
        self.policy.unlink()
        self.assertEqual(S.verify_policy(S.load(), at(11))["code"], "missing")
        self.policy.write_bytes(original)
        S.do_revoke("feishu", "不用了", now=at(12))
        self.assertEqual(S.verify_policy(S.load(), at(12, 1))["code"], "revoked")       # 恢复原文件也不再生效
        self.assertEqual(hashlib.sha256(self.policy.read_bytes()).hexdigest(), sha)
        self.assertTrue(S.do_revoke("feishu", now=at(12, 2))[1]["unchanged"])
        self.sign(when=at(13), version=5)
        self.assertTrue(S.verify_policy(S.load(), at(13, 1))["ok"])

    def test_revoke_without_signature_and_sign_while_on(self):
        self.rejects("BadTransition", S.do_revoke, "feishu", now=at(9))
        self.sign()
        self.turn_on()
        sha = self.write_policy(version=4)
        self.rejects("BadTransition", S.do_sign, self.say(f"签代批清单 v4 {sha[:12]}", at(11)), now=at(11))


class ProjectPinTests(Base):
    """P1-01：签署时固定项目 → 仓库，之后名册改指向、短名冲突都让清单失效。"""

    def test_signature_records_project_repos(self):
        self.sign(projects=("dt-sandbox", "niuma-home"))
        self.assertEqual(self.doc()["signature"]["projects"], {"dt-sandbox": REPO, "niuma-home": "Natsukashii1997/niuma-home"})
        v = S.verify_policy(S.load(), at(11))
        self.assertEqual(v["repos"], {"dt-sandbox": REPO, "niuma-home": "Natsukashii1997/niuma-home"})

    def test_repointed_project_invalidates_until_resigned(self):
        self.sign()
        self.roster["dt-sandbox"] = "someone-else/dt-sandbox"
        v = S.verify_policy(S.load(), at(11))
        self.assertEqual(v["code"], "roster_changed")
        self.assertIn("someone-else/dt-sandbox", v["reason"])
        self.roster["dt-sandbox"] = REPO
        self.assertTrue(S.verify_policy(S.load(), at(11))["ok"])
        self.roster["dt-sandbox"] = "someone-else/dt-sandbox"     # 改指向后要换版本重签，重签记下新的映射
        self.sign(when=at(12), version=4, whitelist=(("someone-else/dt-sandbox", "只有 CI"),))
        self.assertEqual(self.doc()["signature"]["projects"], {"dt-sandbox": "someone-else/dt-sandbox"})
        self.assertTrue(S.verify_policy(S.load(), at(12, 1))["ok"])

    def test_short_name_conflict_cannot_sign_nor_verify(self):
        import multica_night as MN
        roster = {"projects": [{"github": "a/dt-sandbox", "write_allowed": True, "category": "活跃"}],
                  "inventory": [{"path": "github:" + REPO, "sandbox": True}]}
        with mock.patch.object(MN, "committed_json", return_value=roster):
            with self.assertRaises(S.RosterConflict) as cm:
                REAL_DISPATCH()
        self.assertIn("dt-sandbox → Natsukashii1997/dt-sandbox、a/dt-sandbox", str(cm.exception))
        same = {"projects": [{"github": REPO, "write_allowed": True, "category": "活跃"}], "inventory": [{"path": "github:" + REPO, "sandbox": True}]}
        with mock.patch.object(MN, "committed_json", return_value=same):
            self.assertEqual(REAL_DISPATCH(), {"dt-sandbox": REPO})            # 同一个仓库登记两次不算冲突
        sha = self.write_policy()
        S.dispatch_projects.side_effect = S.RosterConflict("名册里短名冲突：dt-sandbox → a、b")
        self.rejects("RosterConflict", S.do_sign, self.say(f"签代批清单 v3 {sha[:12]}", at(9)), now=at(9))
        self.assertFalse(self.state.exists())
        S.dispatch_projects.side_effect = lambda: dict(self.roster)
        self.sign(when=at(9, 1))
        S.dispatch_projects.side_effect = S.RosterConflict("名册里短名冲突：dt-sandbox → a、b")
        v = S.verify_policy(S.load(), at(11))
        self.assertEqual(v["code"], "roster")
        self.assertIn("短名冲突", v["reason"])

    def test_whitelist_checked_against_signed_mapping(self):
        self.sign()
        doc = self.doc()                                   # 签署记录里的映射被改成别的仓库（格式合法）：白名单对不上 → 无效
        doc["signature"]["projects"] = {"dt-sandbox": "other/dt-sandbox"}
        self.state.write_text(json.dumps(doc), encoding="utf-8")
        self.roster["dt-sandbox"] = "other/dt-sandbox"
        v = S.verify_policy(S.load(), at(11))
        self.assertEqual(v["code"], "invalid")
        self.assertIn("不是签署时第 2 节项目的仓库", v["reason"])
        fields = S.parse_policy(fill(self.template))[0]
        self.assertEqual(S.check_signed_projects(fields, {"dt-sandbox": REPO}, {"dt-sandbox": REPO}), (None, []))
        self.assertEqual(S.check_signed_projects(fields, {"dt-sandbox": REPO, "niuma-home": "N/niuma-home"}, ROSTER)[0], "invalid")

    def test_signature_projects_structure_is_checked(self):
        self.sign()
        good = self.doc()
        for name, value in {"没有": None, "空": {}, "值和键对不上": {"dt-sandbox": "o/other"}, "值不是仓库": {"dt-sandbox": "dt-sandbox"}}.items():
            with self.subTest(name):
                d = json.loads(json.dumps(good))
                if value is None:
                    d["signature"].pop("projects")
                else:
                    d["signature"]["projects"] = value
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    S.check_doc(d)

    def test_removed_project_blocks_decisions_but_handoff_still_records(self):
        """P1-02 端到端：项目被移出名册 → 准入不过、普通代批被拒；同一事项交常浩能记下、不占额度。"""
        self.sign()
        s = self.turn_on()
        self.roster.pop("dt-sandbox")
        st = S.read_status(at(10, 1))
        self.assertFalse(st["admission"]["eligible"])
        target = {"card": 7, "task": "t-20261118-abcdef", "generation": 1}
        with SL.open_store() as led:
            with self.assertRaises(SL.LedgerError) as cm:
                SL.issue(led, session_id=s["id"], project="dt-sandbox", action="ask", target=target, context="示例服务加超时",
                         question="超时 30 秒还是 60 秒", summary="选 30 秒", basis="和现有配置一致", revocability="correctable",
                         expires_at=at(11), now=at(10, 2), received={"card": 7, "question": "超时调多少"}, reply={"choice": 1})
            self.assertEqual(cm.exception.code, "NotAdmitted")
            h = SL.issue(led, session_id=s["id"], project="dt-sandbox", action="handoff", category="ask", reason="gate", target=target,
                         context="示例服务加超时", question="超时 30 秒还是 60 秒", ask_owner="回复我选哪个",
                         basis="这个项目的仓库在名册里变了，清单要重签", revocability="irrevocable", now=at(10, 3),
                         received={"card": 7, "question": "超时调多少"})
            self.assertEqual((h["status"], SL.quotas(led, h["id"])), ("handoff", []))
            self.assertEqual((h["policy_version"], h["policy_sha256"]), (3, s["policy"]["sha256"]))


class StateMachineTests(Base):
    def test_on_records_session_and_registers_ledger(self):
        sha = self.sign()
        s = self.turn_on()
        self.assertRegex(s["id"], r"^s-20261118-100000-[0-9a-f]{12}$")
        self.assertEqual((s["policy"], s["drain_minutes"], s["source"]["open_id"]), ({"version": 3, "sha256": sha}, 120, OWNER))
        with SL.open_store() as st:
            self.assertEqual(SL.ledger_id(st), s["ledger"]["id"])
            SL.check_session(st, s["id"], s["ledger"]["id"])
        st = S.read_status(at(11))
        self.assertEqual((st["state"], st["admission"]), ("on", {"eligible": True, "reasons": []}))
        self.assertEqual(st["session"]["source"]["open_id"], "已核验（不显示）")

    def test_on_rejections(self):
        self.rejects("PolicyInvalid", S.do_on, self.say("开秘书", at(10)), now=at(10))       # 没签
        self.sign()
        cases = [
            ("BadCommand", self.say("开启秘书", at(10))), ("BadCommand", self.say("托管", at(10))),
            ("BadCommand", self.say("开一下秘书", at(10))), ("BadCommand", self.say("开 秘书", at(10))),
            ("BadCommand", self.say("签代批清单 v3 000000000000", at(10))),
            ("NotAuthorized", self.say("开秘书", at(10), sender="ou_someone_else")),
            ("NotAuthorized", self.say("开秘书", at(10), chat="oc_some_group")),
            ("NotAuthorized", self.say("开秘书", at(9, 45))),
        ]
        before = self.state.read_bytes()
        for code, mid in cases:
            with self.subTest(code=code, mid=mid):
                self.rejects(code, S.do_on, mid, now=at(10))
                self.assertEqual(self.state.read_bytes(), before)
        self.rejects("NotAuthorized", S.do_on, None, now=at(10))

    def test_on_text_strips_only_surrounding_blank_and_punctuation(self):
        """P2-01：和托管 custody.parse_on_text 同一处理（clean_text）：首尾空白和句号、感叹号去掉后恰好是「开秘书」。"""
        self.sign()
        S.do_on(self.say("  开秘书！\n", at(10)), now=at(10))
        self.assertEqual(self.doc()["state"], "on")

    def test_on_replay_and_on_when_not_off(self):
        self.sign()
        mid = self.say("开秘书", at(10))
        S.do_on(mid, now=at(10))
        self.rejects("BadTransition", S.do_on, self.say("开秘书", at(10, 1)), now=at(10, 1))
        S.do_off("feishu", now=at(10, 2))
        self.rejects("BadTransition", S.do_on, self.say("开秘书", at(10, 3)), now=at(10, 3))      # 排空中
        S.do_off("feishu", drained=True, now=at(10, 4))
        self.rejects("Replay", S.do_on, mid, now=at(10, 5))
        S.do_on(self.say("开秘书", at(10, 6)), now=at(10, 6))
        S.do_freeze("hermes", "测试", now=at(10, 7))
        self.rejects("BadTransition", S.do_on, self.say("开秘书", at(10, 8)), now=at(10, 8))     # 冻结中

    def test_on_refuses_without_ledger(self):
        self.sign()
        self.db.unlink()
        self.rejects("LedgerUnavailable", S.do_on, self.say("开秘书", at(10)), now=at(10))
        self.assertEqual(self.doc()["state"], "off")
        self.assertFalse(self.db.exists())                                                       # 不隐式新建

    def test_off_drain_and_drained(self):
        self.sign()
        s = self.turn_on()
        doc, r = S.do_off("feishu", drained=False, now=at(11))
        self.assertEqual((r["result"], r["deadline"]), ("draining", C.iso(at(13))))
        self.assertEqual(S.do_off("feishu", now=at(11, 1))[1], {"result": "draining", "unchanged": True})
        self.rejects("BadTransition", S.do_on, self.say("开秘书", at(11, 2)), now=at(11, 2))
        doc, r = S.do_off("feishu", drained=True, now=at(11, 3))
        self.assertEqual((doc["state"], doc["session"], doc["history"][-1]["id"], doc["history"][-1]["ended"]["how"]),
                         ("off", None, s["id"], "排空完成"))
        self.assertEqual(S.do_off("feishu", now=at(11, 4))[1], {"result": "off", "unchanged": True})

    def test_drained_needs_draining_and_proof(self):
        self.sign()
        s = self.turn_on()
        self.rejects("BadTransition", S.do_off, "feishu", drained=True, now=at(10, 1))           # 还开着
        with SL.open_store() as st:
            d = SL.issue(st, session_id=s["id"], project="dt-sandbox", action="ask",
                         target={"card": 7, "task": "t-20261118-abcdef", "generation": 1},
                         context="示例服务加超时", question="超时 30 秒还是 60 秒", summary="选 30 秒", basis="和现有配置一致，只影响沙盒",
                         revocability="correctable", expires_at=at(11), now=at(10, 2), received={"card": 7, "question": "超时调多少", "options": ["30 秒", "60 秒"]}, reply={"choice": 1})
            SL.begin(st, d["id"], now=at(10, 3))
        S.do_off("feishu", now=at(10, 4))
        msg = self.rejects("DrainUnverified", S.do_off, "feishu", drained=True, now=at(10, 5))
        self.assertIn("进行中", msg)
        with SL.open_store() as st:
            SL.finish(st, d["id"], "done", {"ref": "答卡回读"}, now=at(10, 6))
        S.do_off("feishu", drained=True, now=at(10, 7))
        self.assertEqual(self.doc()["state"], "off")

    def test_drain_proof_fails_closed_on_ledger_problems(self):
        self.sign()
        self.turn_on()
        S.do_off("feishu", now=at(10, 1))
        doc = self.doc()
        doc["session"]["ledger"]["id"] = "L-" + "0" * 16
        self.state.write_text(json.dumps(doc), encoding="utf-8")
        self.assertIn("账本身份对不上", self.rejects("DrainUnverified", S.do_off, "feishu", drained=True, now=at(10, 2)))
        self.db.unlink()
        self.assertIn("不见了", self.rejects("DrainUnverified", S.do_off, "feishu", drained=True, now=at(10, 3)))

    def test_freeze_paths_and_items(self):
        self.rejects("BadTransition", S.do_freeze, "hermes", "x", now=at(9))
        self.sign()
        self.turn_on()
        self.rejects("BadCommand", S.do_freeze, "hermes", " ", now=at(10, 1))
        self.rejects("BadCommand", S.do_freeze, "带 空格 的 标签 !", "x", now=at(10, 1))
        S.do_freeze("hermes", "合并结果不明", ["dt-sandbox#7"], now=at(10, 2))
        self.assertEqual(self.doc()["state"], "frozen")
        self.assertEqual(S.do_freeze("hermes", "再冻", ["dt-sandbox#7"], now=at(10, 3))[1], {"result": "frozen", "unchanged": True})
        self.assertEqual(S.do_freeze("hermes", "再冻", ["dt-sandbox#8"], now=at(10, 4))[1]["added"], ["dt-sandbox#8"])
        self.assertEqual(self.doc()["session"]["frozen"]["items"], ["dt-sandbox#7", "dt-sandbox#8"])
        for drained in (False, True):
            self.rejects("BadTransition", S.do_off, "feishu", drained=drained, now=at(10, 5))
        # draining → frozen 也行
        S.do_unfreeze(self.say("解除冻结", at(10, 6)), now=at(10, 6))
        self.turn_on(at(10, 7))
        S.do_off("feishu", now=at(10, 8))
        S.do_freeze("hermes", "排空时发现问题", now=at(10, 9))
        self.assertEqual(self.doc()["state"], "frozen")

    def test_draining_past_deadline_freezes_by_clock(self):
        self.sign()
        self.turn_on()
        S.do_off("feishu", now=at(11))                       # 截止 13:00
        self.assertEqual(S.read_status(at(12, 59))["state"], "draining")
        st = S.read_status(at(13))
        self.assertEqual((st["state"], st["admission"]["eligible"]), ("frozen", False))
        self.assertEqual(self.doc()["state"], "draining")    # 只读不落盘
        self.rejects("BadTransition", S.do_off, "feishu", drained=True, now=at(13, 1))
        doc = self.doc()                                     # 命令被拒，但到点推进落盘了
        self.assertEqual((doc["state"], doc["session"]["frozen"]["source"]), ("frozen", "到点（排空截止）"))

    def test_unfreeze_only_with_exact_words_from_owner(self):
        self.sign()
        self.turn_on()
        self.rejects("BadTransition", S.do_unfreeze, self.say("解除冻结", at(10, 1)), now=at(10, 1))   # 没冻结
        S.do_freeze("hermes", "测试", now=at(10, 2))
        for mid, code in ((self.say("解冻", at(10, 3)), "BadCommand"), (self.say("关秘书", at(10, 3)), "BadCommand"),
                          (self.say("解除冻结", at(10, 3), sender="ou_x"), "NotAuthorized")):
            self.rejects(code, S.do_unfreeze, mid, now=at(10, 3))
        self.rejects("NotAuthorized", S.do_unfreeze, None, now=at(10, 3))
        doc, _ = S.do_unfreeze(self.say("解除冻结。", at(10, 4)), now=at(10, 4))
        self.assertEqual((doc["state"], doc["history"][-1]["ended"]["how"]), ("off", "解除冻结"))
        self.assertEqual(doc["history"][-1]["ended"]["open_id"], OWNER)

    def test_revoke_while_on_drains_and_policy_change_blocks_admission(self):
        self.sign()
        self.turn_on()
        self.policy.write_bytes(self.policy.read_bytes() + b"\n")
        st = S.read_status(at(10, 1))
        self.assertEqual(st["state"], "on")
        self.assertFalse(st["admission"]["eligible"])
        self.assertIn("被改过", "；".join(st["admission"]["reasons"]))
        doc, r = S.do_revoke("feishu", "不用了", now=at(10, 2))
        self.assertEqual((doc["state"], r["state"], r["deadline"]), ("draining", "draining", C.iso(at(12, 2))))

    def test_entering_draining_or_frozen_voids_issued_decisions(self):
        self.sign()
        s = self.turn_on()
        with SL.open_store() as st:
            d = SL.issue(st, session_id=s["id"], project="dt-sandbox", action="redispatch",
                         target={"task": "t-20261118-abcdef", "generation": 1}, context="示例服务加超时",
                         question="中台停掉了这件事，要不要再派一次", summary="再派一次", basis="第一次是网络超时，再派一次不影响别的",
                         revocability="correctable", expires_at=at(11), now=at(10, 1), received={"task": "t-20261118-abcdef", "stopped": "连续 10 次没跑成"}, reply={"redispatch": 1})
        S.do_off("feishu", now=at(10, 2))
        with SL.open_store() as st:
            self.assertEqual(SL.get(st, d["id"])["status"], "void")
            self.assertEqual({q["state"] for q in SL.quotas(st, d["id"])}, {"released"})

    def test_corrupt_or_missing_state_rejects_everything(self):
        self.sign()
        good = self.state.read_text(encoding="utf-8")
        bad = {
            "不是 JSON": "{",
            "schema 不对": good.replace("dt-secretary/1", "dt-custody/1"),
            "on 没有 session": json.dumps({**json.loads(good), "state": "on"}),
            "多字段": json.dumps({**json.loads(good), "consumed_windows": {}}),
        }
        for name, text in bad.items():
            with self.subTest(name):
                self.state.write_text(text, encoding="utf-8")
                st = S.read_status(at(10))
                self.assertIsNone(st["state"])
                self.assertFalse(st["admission"]["eligible"])
                self.rejects("Corrupt", S.do_on, self.say("开秘书", at(10)), now=at(10))
                self.rejects("Corrupt", S.do_off, "feishu", now=at(10))
                self.rejects("Corrupt", S.do_freeze, "hermes", "x", now=at(10))
                self.rejects("Corrupt", S.do_revoke, "feishu", now=at(10))
        self.state.unlink()                                  # 写过（有初始化标记）以后文件丢了：不当成第一次用
        self.assertTrue(C.marker_path(self.state).exists())
        self.assertIn("秘书状态写过", S.read_status(at(10))["error"])
        self.rejects("Corrupt", S.do_on, self.say("开秘书", at(10)), now=at(10))

    def test_session_structure_is_checked(self):
        self.sign()
        self.turn_on()
        good = self.doc()
        variants = {
            "会话号格式": lambda d: d["session"].update(id="c-20261118-100000-0123456789ab"),
            "没有账本身份": lambda d: d["session"].pop("ledger"),
            "多 until": lambda d: d["session"].update(until=C.iso(at(12))),
            "source 少 open_id": lambda d: d["session"]["source"].pop("open_id"),
            "on 却有 drain": lambda d: d["session"].update(drain={"started_at": C.iso(at(10)), "deadline": C.iso(at(11)), "source": "x"}),
            "历史重复会话号": lambda d: d["history"].append({"id": d["session"]["id"]}),
        }
        for name, change in variants.items():
            with self.subTest(name):
                d = json.loads(json.dumps(good))
                change(d)
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    S.check_doc(d)

    def test_lock_timeout_names_secretary(self):
        self.sign()
        fd = os.open(str(self.state.with_name("secretary.json.lock")), os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with self.assertRaises(C.Rejected) as cm:
                with C.locked(self.state, wait=0.2, what=S.WHAT):
                    pass
            self.assertEqual(cm.exception.code, "Locked")
            self.assertIn("秘书状态正被别的进程改", str(cm.exception))
        finally:
            os.close(fd)


class EndToEndTests(Base):
    """真 read_status 当账本的准入（不打桩 status_reader）。"""

    def ask(self, st, now, **kw):
        kw.setdefault("target", {"card": 7, "task": "t-20261118-abcdef", "generation": 1})
        return SL.issue(st, project="dt-sandbox", action="ask", context="示例服务加超时", question="超时 30 秒还是 60 秒",
                        summary="选 30 秒", basis="和现有配置一致，只影响沙盒", revocability="correctable",
                        expires_at=now + dt.timedelta(minutes=30), now=now, received={"card": 7, "question": "超时调多少", "options": ["30 秒", "60 秒"]}, reply={"choice": 1}, **kw)

    def test_real_admission_gates_the_ledger(self):
        self.sign(cats=("是", "否", "是", "是"))
        s = self.turn_on()
        with SL.open_store() as st:
            self.ask(st, at(10, 1), session_id=s["id"])
            with self.assertRaises(SL.LedgerError) as cm:
                SL.issue(st, session_id=s["id"], project="dt-sandbox", action="redispatch",
                         target={"task": "t-20261118-abcdef", "generation": 1}, context="示例服务加超时",
                         question="要不要再派一次", summary="再派一次", basis="网络超时", revocability="correctable",
                         expires_at=at(11), now=at(10, 2), received={"task": "t-20261118-abcdef", "stopped": "连续 10 次没跑成"}, reply={"redispatch": 1})
            self.assertEqual(cm.exception.code, "NotAllowed")
        S.do_off("feishu", now=at(10, 3))
        with SL.open_store() as st:
            with self.assertRaises(SL.LedgerError) as cm:
                self.ask(st, at(10, 4), session_id=s["id"])
            self.assertEqual(cm.exception.code, "NotAdmitted")
            h = SL.issue(st, session_id=s["id"], project="dt-sandbox", action="handoff", category="ask", reason="gate",
                         target={"card": 8, "task": "t-20261118-abcdef", "generation": 1}, context="示例服务加超时",
                         question="超时 30 秒还是 60 秒", ask_owner="回复我选哪个", basis="秘书已经关了，交给你",
                         revocability="irrevocable", now=at(10, 5), received={"card": 8, "question": "超时调多少"})
            self.assertEqual(h["status"], "handoff")
        S.do_off("feishu", drained=True, now=at(10, 6))
        with SL.open_store() as st:                          # 会话结束后不能再往里记
            with self.assertRaises(SL.LedgerError) as cm:
                SL.issue(st, session_id=s["id"], project="dt-sandbox", action="handoff", category="ask", reason="gate",
                         target={"card": 9, "task": "t-20261118-abcdef", "generation": 1}, context="示例服务加超时",
                         question="超时 30 秒还是 60 秒", ask_owner="回复我选哪个", basis="秘书已经关了，交给你",
                         revocability="irrevocable", now=at(10, 7), received={"card": 8, "question": "超时调多少"})
            self.assertEqual(cm.exception.code, "WrongSession")

    def test_policy_swap_with_new_signature_does_not_admit_old_session(self):
        self.sign()
        s = self.turn_on()
        # 开着时换文件 → 准入不过；会话清单和现在的不一样也不过（签署要先关，这里直接改状态文件模拟）
        doc = self.doc()
        doc["session"]["policy"]["sha256"] = "f" * 64
        self.state.write_text(json.dumps(doc), encoding="utf-8")
        st = S.read_status(at(10, 1))
        self.assertIn("不是同一份", "；".join(st["admission"]["reasons"]))
        with SL.open_store() as stt:
            with self.assertRaises(SL.LedgerError) as cm:
                self.ask(stt, at(10, 2), session_id=s["id"])
            self.assertEqual(cm.exception.code, "NotAdmitted")


class CustodyDefaultsTests(Base):
    """custody.py 为复用加的可选参数：不传时和原来逐字一样。"""

    def test_defaults_unchanged(self):
        self.assertEqual(C.unfilled("> **所有〔填〕都要填掉**；留着任何一处〔填〕，整份策略不生效。\n〔填〕\n"), [2])
        self.assertEqual(C.unfilled("> **所有〔填〕都要填掉**；留着任何一处〔填〕，整份清单不生效。\n"), [1])
        p = self.root / "c" / "custody.json"
        self.assertEqual(C.load(p), C.empty_doc())
        C.save(C.empty_doc(), p)
        p.unlink()
        with self.assertRaises(ValueError) as cm:
            C.load(p)
        self.assertIn("但托管状态写过", str(cm.exception))
        fd = os.open(str(p.with_name("custody.json.lock")), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            with self.assertRaises(C.Rejected) as cm:
                with C.locked(p, wait=0.1):
                    pass
            self.assertIn("托管状态正被别的进程改", str(cm.exception))
        finally:
            os.close(fd)
        self.assertRegex(C.new_session_id(at(10), C.empty_doc()), r"^c-20261118-100000-[0-9a-f]{12}$")
        with mock.patch.object(C.secrets, "token_hex", return_value="0" * 12):
            msg = self.rejects("Corrupt", C.new_session_id, at(10), {"history": [{"id": "c-20261118-100000-" + "0" * 12}]})
        self.assertEqual(msg, "生成不出不重复的托管会话 ID（随机源异常），不开启")


class CliTests(Base):
    def run_cli(self, *args):
        r = subprocess.run([sys.executable, "-B", str(SCRIPTS / "secretary.py"), *args], capture_output=True, text=True,
                           env=dict(os.environ), timeout=60)
        return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])

    def test_status_and_verify_read_only(self):
        code, out = self.run_cli("status")
        self.assertEqual((code, out["state"]), (0, "off"))
        self.assertIn("还没签代批清单", out["msg"])
        code, out = self.run_cli("verify")
        self.assertEqual((code, out["code"]), (1, "unsigned"))
        self.assertFalse(self.state.exists())
        code, out = self.run_cli("off", "--source", "feishu")
        self.assertEqual((code, out["ok"], out["state"]), (0, True, "off"))
        code, out = self.run_cli("freeze", "--source", "hermes", "--reason", "x")
        self.assertEqual((code, out["error"]), (1, "BadTransition"))


if __name__ == "__main__":
    unittest.main()
