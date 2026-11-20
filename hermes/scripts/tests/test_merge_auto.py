"""按策略合并（#257，ADR-016）自测：策略记录的签署与失效、auto 交出与合并、例外、巡视合并、上限、结果不明的只读核对、
绕过记录、撤回。夹具同 test_merge_act（真 git 临时仓库、假 gh、假 lark-cli），不联网、不碰真实仓库和记录。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import merge_officer as mo  # noqa: E402
import merge_policy as MP  # noqa: E402
from test_merge_act import ACT, Fixture, sh  # noqa: E402

EOF_NET = 'Post "https://api.github.com/graphql": EOF'


class Auto(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.dir = self.tmp / "merge"

    def rules_now(self):
        return json.loads(self.rules.read_text(encoding="utf-8"))

    def set_auto(self, **kw):
        rules = self.rules_now()
        rules["auto"].update(kw)
        self.rules.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")

    def sign(self):
        """直接写一条有效的策略记录（签署流程另测）。"""
        self.dir.mkdir(parents=True, exist_ok=True)
        sig = {"type": MP.POLICY_TYPE, "scope_sha256": MP.scope_sha(self.rules_now()), "rules_version": "2",
               "message_id": "om_sign", "open_id": "ou_owner", "signed_at": "2026-11-07T09:00:00+08:00"}
        (self.dir / "policy.json").write_text(json.dumps(
            {"schema": MP.SCHEMA, "enabled": True, "signature": sig, "used_messages": {}, "history": []}), encoding="utf-8")

    def auto(self, ref="app#5", *args, env=None):
        return self.cli(ACT, "auto", ref, *args, env=env)

    def book(self):
        f = self.dir / "auto-merges.jsonl"
        return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def events(self):
        return [r["event"] for r in self.book()]

    def handoffs(self):
        f = self.dir / "handoffs.json"
        return json.loads(f.read_text(encoding="utf-8"))["handoffs"] if f.exists() else []

    def state(self, number=5):
        return json.loads((self.fake / "prs.json").read_text())["o/app"][str(number)]["state"]

    def merges_called(self):
        return [c for c in self.calls() if c[:2] == ["pr", "merge"]]

    def fail(self, **plan):
        (self.fake / "fail.json").write_text(json.dumps(plan))

    # ---------- 策略没生效：和原来一样 ----------

    def test_off_by_default_nothing_merges_and_cards_as_before(self):
        r = self.auto()
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.assertIn("自动合并没开（还没签）", r.stdout)
        self.assertIn("仍等常浩说合并", r.stdout)
        self.assertEqual((self.state(), self.merges_called(), self.book(), self.handoffs()), ("OPEN", [], [], []))
        self.scan()
        self.assertEqual(len(self.calls("lark.jsonl")), 1)             # 没开策略：每个新版本照旧发卡片
        self.assertEqual(self.state(), "OPEN")

    def test_scope_change_voids_signature(self):
        """签过之后规则里的授权范围变了（多了一个仓库、放宽了风险档）：策略自动失效，回到发卡片。"""
        self.sign()
        rules = self.rules_now()
        rules["act"]["repos"]["other"] = {"ci": True}
        self.rules.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
        r = self.auto()
        self.assertEqual(r.returncode, 3)
        self.assertIn("范围哈希对不上", r.stdout)
        self.assertEqual(self.merges_called(), [])
        for key, value in (("sensitive", []), ("direction_docs", []), ("auto", {"tiers": ["L0", "L1", "L2", "L3"]})):
            changed = {**self.rules_now(), key: value}
            self.assertNotEqual(MP.scope_sha(changed), MP.scope_sha(self.rules_now()), key)
        same = {**self.rules_now(), "note": "改说明不算", "return_when": {}}
        same["auto"] = {**same["auto"], "note": "这里的说明也不算"}
        self.assertEqual(MP.scope_sha(same), MP.scope_sha(self.rules_now()))

    def test_corrupt_policy_file_is_not_authority(self):
        self.dir.mkdir(parents=True)
        (self.dir / "policy.json").write_text("{坏的", encoding="utf-8")
        r = self.auto()
        self.assertEqual(r.returncode, 3)
        self.assertIn("读不出来", r.stdout)
        self.assertEqual(self.merges_called(), [])

    # ---------- 合并 ----------

    def test_handoff_merges_and_records(self):
        self.sign()
        main_head = sh(self.main, "rev-parse", "HEAD")
        r = self.auto(f"app#5@{self.head[:7]}", "--by", "测试会话")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"已按策略合并 app#5@{self.head[:7]} → fffffff", r.stdout)
        self.assertIn("没拉本机主目录、没安装", r.stdout)
        self.assertIn("撤回：merge_act.py revert app#5", r.stdout)
        (merge,) = self.merges_called()
        self.assertEqual(merge[merge.index("--match-head-commit") + 1], self.head)        # 合并命令绑定判定时的版本
        self.assertIn("--squash", merge)
        intent, done = self.book()
        self.assertEqual((intent["event"], done["event"], intent["id"]), ("intent", "merged", done["id"]))
        for key, want in (("repo", "o/app"), ("pr", "5"), ("title", "PR 5"), ("head", self.head), ("base", self.base),
                          ("tier", "L0"), ("rules_version", "2"), ("suggestion", mo.MERGE), ("checks", ["test"]),
                          ("identity", "owner-gh")):
            self.assertEqual(intent[key], want, key)
        self.assertEqual(intent["auth"], {"type": "owner_approved_policy", "scope_sha256": MP.scope_sha(self.rules_now()),
                                          "message_id": "om_sign", "signed_at": "2026-11-07T09:00:00+08:00"})
        self.assertEqual((intent["ready"]["mode"], intent["ready"]["by"]), ("handoff", "测试会话"))
        self.assertIn("未核实", intent["review"])
        self.assertEqual((done["merge_commit"], done["by_this_call"]), ("f" * 40, True))
        self.assertEqual(self.handoffs(), [])                                              # 合完，交出登记清掉
        (row,) = self.ledger()                                                             # 巡视之前就交出：补一行判定
        self.assertEqual((row["pr"], row["owner"], row["tier"]), ("5", mo.POLICY_OWNER, "L0"))
        self.assertEqual(sh(self.main, "rev-parse", "HEAD"), main_head)                    # 主目录没被拉
        self.assertEqual(self.calls("lark.jsonl"), [])                                     # 默认不逐条通知

    def test_return_suggestion_does_not_block_but_is_recorded(self):
        """常浩 11-20：合并官判「退回」的第一版不拦，机械检查都过就合并，退回理由写进记录。"""
        self.sign()
        self.update_pr(body="没有关联 issue")
        self.assertEqual(self.auto().returncode, 0)
        intent = self.book()[0]
        self.assertEqual((intent["suggestion"], intent["reasons"]), (mo.RETURN, "正文没有 Closes #号"))
        self.assertEqual(self.state(), "MERGED")

    def test_exceptions_are_not_merged(self):
        self.sign()
        cases = {"敏感路径": {"files": [{"path": "ops/x.sh"}]}, "定方向文档": {"files": [{"path": "docs/DECIDED.md"}]},
                 "标签 needs-info": {"labels": [{"name": "needs-info"}]}}
        for word, kw in cases.items():
            self.update_pr(files=[{"path": "docs/a.md"}], labels=[])
            self.update_pr(**kw)
            r = self.auto()
            self.assertEqual(r.returncode, 3, word)
            self.assertIn("属于例外", r.stdout)
            self.assertIn(word, r.stdout)
        self.assertEqual((self.state(), self.merges_called(), self.book(), self.handoffs()), ("OPEN", [], [], []))

    def test_tier_outside_policy_is_exception(self):
        self.set_auto(tiers=["L0"])
        self.sign()
        self.update_pr(files=[{"path": "src/a.py"}])
        r = self.auto()
        self.assertEqual(r.returncode, 3)
        self.assertIn("风险档 L1 不在按策略合并的范围里", r.stdout)
        self.scan()                                                  # 收窄以后，范围外的档位和例外一样发卡片问常浩
        self.assertEqual(len(self.calls("lark.jsonl")), 1)

    def test_uncovered_repo(self):
        self.sign()
        r = self.auto("other#9")
        self.assertEqual(r.returncode, 3)
        self.assertIn("不在合并执行器覆盖范围里", r.stdout)

    def test_wrong_head_prefix_stops(self):
        self.sign()
        r = self.auto("app#5@0000000")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不是交出的 0000000", r.stdout)
        self.assertEqual((self.merges_called(), self.handoffs()), ([], []))

    def test_malformed_pr_stops(self):
        self.sign()
        self.update_pr(files=[])
        r = self.auto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("没有改动文件", r.stdout)

    # ---------- 交出后由巡视合并 ----------

    def running(self):
        self.update_pr(statusCheckRollup=[{"name": "test", "status": "IN_PROGRESS", "conclusion": ""}], mergeStateStatus="UNSTABLE")

    def passed(self):
        self.update_pr(statusCheckRollup=[{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}], mergeStateStatus="CLEAN")

    def test_ci_running_registers_then_scan_merges(self):
        self.sign()
        self.running()
        r = self.auto()
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("CI 还在跑", r.stdout)
        self.assertIn("已登记交出", r.stdout)
        self.assertEqual([(h["pr"], h["head"], h["by"]) for h in self.handoffs()], [("5", self.head, "会话")])
        self.assertEqual(self.scan().stdout, "")                    # 还在跑：这一轮不合，也不吵
        self.assertEqual((self.state(), self.book()), ("OPEN", []))
        self.passed()
        self.assertEqual(self.scan().stdout, "")                    # 合成功的不在这里说，进日报
        self.assertEqual((self.state(), self.events(), self.handoffs()), ("MERGED", ["intent", "merged"], []))
        self.assertEqual(self.book()[0]["ready"]["by"], "会话")
        self.assertEqual(self.calls("lark.jsonl"), [])              # 不是例外：策略生效后不发卡片

    def test_scan_without_handoff_does_not_merge(self):
        self.sign()
        self.scan()
        self.assertEqual((self.state(), self.book(), self.calls("lark.jsonl")), ("OPEN", [], []))

    def test_ready_any_merges_without_handoff(self):
        self.set_auto(ready="any")
        self.sign()
        self.scan()
        self.assertEqual((self.state(), self.events()), ("MERGED", ["intent", "merged"]))
        self.assertEqual(self.book()[0]["ready"], {"mode": "any", "by": "合并官巡视", "at": None})

    def test_new_commit_voids_handoff(self):
        self.sign()
        self.running()
        self.auto()
        self.update_pr(headRefOid="b" * 40)
        self.passed()
        self.scan()
        self.assertEqual((self.state(), self.book(), self.handoffs()), ("OPEN", [], []))

    def test_handoff_kept_when_repo_listing_fails(self):
        """这一轮没列出这个仓库的 PR（GitHub 报错）：交出登记不能当成作废。"""
        self.sign()
        self.running()
        self.auto()
        import merge_act
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir)}):
            self.assertEqual(merge_act.auto_scan(self.rules_now(), [], [], set()), [])
            self.assertEqual(len(MP.handoffs()), 1)
            merge_act.auto_scan(self.rules_now(), [], [], {"o/app"})
            self.assertEqual(MP.handoffs(), [])

    def test_exception_still_gets_card_when_policy_on(self):
        self.sign()
        self.update_pr(files=[{"path": "docs/DECIDED.md"}])
        self.scan()
        (card,) = self.calls("lark.jsonl")
        text = json.loads(card[card.index("--content") + 1])["body"]["elements"][0]["content"]
        self.assertIn("问常浩", text)
        self.assertIn("改动定方向文档", text)
        r = self.cli(ACT, "merge", self.ref)                         # 例外仍可由常浩点卡片合并（owner_direct）
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.ledger()[0]["owner"], "合并")
        self.assertEqual(self.book(), [])                            # 卡片合并不进策略账本

    def test_exception_with_return_reason_still_gets_card(self):
        """例外同时有退回理由（建议是「退回」）：既不会按策略合并，也不能漏发卡片。"""
        self.sign()
        self.update_pr(files=[{"path": "PRD.md"}], body="没有关联 issue")
        self.scan()
        self.assertEqual(self.ledger()[0]["suggestion"], mo.RETURN)
        self.assertEqual(len(self.calls("lark.jsonl")), 1)
        self.assertEqual(self.auto().returncode, 3)

    # ---------- 上限 ----------

    def test_daily_cap(self):
        self.set_auto(daily_cap=1, ready="any")
        self.sign()
        data = json.loads((self.fake / "prs.json").read_text())
        data["o/app"]["6"] = self.pr(6, "6" * 40, ["docs/b.md"])
        self.set_prs(data)
        out = self.scan().stdout
        self.assertEqual(sorted(self.state(n) for n in (5, 6)), ["MERGED", "OPEN"])
        self.assertIn("到上限 1 了", out)
        self.assertEqual(self.scan().stdout, "")                    # 同一天只说一次
        left = "6" if self.state(6) == "OPEN" else "5"
        self.assertEqual(self.auto(f"app#{left}").returncode, 1)
        day2 = {"DT_NOW": "2026-11-08T10:00:00+08:00"}
        self.assertEqual(self.auto(f"app#{left}", env=day2).returncode, 0)    # 第二天继续

    # ---------- 没合上、结果不明 ----------

    def test_rejected_merge_is_failed_and_can_retry(self):
        self.sign()
        (self.fake / "merge_fail").write_text("x")
        r = self.auto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("没合上", r.stdout)
        self.assertEqual((self.state(), self.events()), ("OPEN", ["intent", "failed"]))
        self.assertEqual(len(self.handoffs()), 1)                   # 交出还在
        (self.fake / "merge_fail").unlink()
        self.scan()
        self.assertEqual((self.state(), self.events()), ("MERGED", ["intent", "failed", "intent", "merged"]))

    def test_unsure_is_reconciled_read_only_next_scan(self):
        """合并命令发了、回读没成功：记结果不明，不重发；下一轮巡视只读核对。"""
        self.sign()
        self.fail(view_merged={"n": 3, "msg": EOF_NET})
        r = self.auto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("结果不明", r.stdout)
        self.assertIn("不会重发", r.stdout)
        self.assertEqual(self.events(), ["intent", "unsure"])
        again = self.auto()                                          # 核对清楚之前不再合
        self.assertEqual(again.returncode, 1)
        self.assertIn("结果不明，还没核对清楚", again.stdout)
        self.assertEqual(len(self.merges_called()), 1)
        out = self.scan().stdout
        self.assertIn("只读核对：已按策略合并 app#5", out)
        self.assertEqual(self.events(), ["intent", "unsure", "merged"])
        self.assertTrue(self.book()[-1]["late"])
        self.assertEqual(len(self.merges_called()), 1)               # 全程只发过一次合并命令
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir)}):
            self.assertEqual((MP.unsettled(), MP.find_merged("o/app", 5)["status"]), ([], "merged"))

    def test_crash_between_intent_and_result_is_unsettled(self):
        """只记了「要合」就没了下文（进程中途没了）：按结果不明处理，只读核对后才放开。"""
        self.sign()
        self.dir.mkdir(exist_ok=True)
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir), "DT_NOW": "2026-11-07T09:30:00+08:00"}):
            MP.append({"event": "intent", "id": "m-x", "repo": "o/app", "pr": "5", "head": self.head, "title": "PR 5"})
        self.assertIn("还没核对清楚", self.auto().stdout)
        out = self.scan().stdout
        self.assertIn("只读核对：app#5", out)
        self.assertIn("没合上", out)
        self.assertEqual(self.events(), ["intent", "failed"])
        self.assertEqual(self.auto().returncode, 0)                  # 核对过没合，才重新合

    # ---------- 绕过记录 ----------

    def test_merge_outside_executor_is_recorded_once(self):
        self.sign()
        self.scan()
        self.update_pr(state="MERGED", mergedAt="2026-11-07T10:05:00Z")       # 会话或网页上直接合了
        self.scan()
        self.scan()
        (b,) = self.book()
        self.assertEqual((b["event"], b["repo"], b["pr"], b["tier"]), ("bypass", "o/app", "5", "L0"))

    def test_policy_merge_is_not_bypass(self):
        self.sign()
        self.auto()
        self.scan()                                                  # PR 已不在 open 列表：补结局
        self.assertEqual(self.events(), ["intent", "merged"])
        self.assertEqual(self.ledger()[0]["outcome"], mo.MERGED)

    # ---------- 撤回 ----------

    def test_revert_opens_pr_and_merges_it_by_same_checks(self):
        self.sign()
        self.auto()
        r = self.cli(ACT, "revert", "app#5", "合错了")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已开撤回 PR：https://github.com/o/app/pull/105", r.stdout)
        self.assertIn("已按策略合并 app#105", r.stdout)
        (call,) = [c for c in self.calls() if c[:2] == ["pr", "revert"]]
        self.assertIn("合错了", call[call.index("--body") + 1])
        self.assertEqual(self.events(), ["intent", "merged", "revert_opened", "intent", "merged"])
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir)}):
            self.assertEqual(MP.find_merged("o/app", 5)["revert"]["revert_pr"], "105")
        again = self.cli(ACT, "revert", "app#5")
        self.assertIn("已经开过撤回 PR", again.stdout)
        self.assertEqual(len([c for c in self.calls() if c[:2] == ["pr", "revert"]]), 1)

    def test_revert_pr_waits_for_owner_when_auto_off(self):
        self.set_auto(revert_auto_merge=False)
        self.sign()
        self.auto()
        r = self.cli(ACT, "revert", "app#5")
        self.assertIn("撤回 PR 没有自动合并，等常浩说合并", r.stdout)
        self.assertEqual(self.state(105), "OPEN")

    def test_revert_only_policy_merges(self):
        self.sign()
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("账本里没有按策略合并过 app#5", r.stdout)
        self.assertEqual([c for c in self.calls() if c[:2] == ["pr", "revert"]], [])

    def test_revert_failure_is_said(self):
        self.sign()
        self.auto()
        (self.fake / "revert_fail").write_text("x")
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("没撤回", r.stdout)
        self.assertEqual(self.events(), ["intent", "merged"])


class Policy(unittest.TestCase):
    """签署：常浩在私聊发「开自动合并 <范围哈希前 12 位>」。回读核验打桩，其余走真逻辑。"""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="dt-policy-"))
        self.rules = mo.load_rules(HERE.parent / "merge_rules.json")
        self.sha = MP.scope_sha(self.rules)
        self.env = unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.tmp), "DT_NOW": "2026-11-20T15:00:00+08:00"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.text = f"开自动合并 {self.sha[:12]}"
        self.said = unittest.mock.patch.object(
            MP, "verify_owner_message", lambda mid: {"message_id": mid, "text": self.text, "sender_open_id": "ou_owner"})
        self.said.start()
        self.addCleanup(self.said.stop)

    def test_repo_rules_have_auto_section(self):
        self.assertEqual(self.rules["auto"]["tiers"], ["L0", "L1", "L2"])
        self.assertNotIn("L3", self.rules["auto"]["tiers"])
        self.assertTrue(all(c.get("ci") for c in self.rules["act"]["repos"].values()))   # 覆盖的仓库都登记了 CI

    def test_sign_then_active_then_off(self):
        self.assertEqual(MP.active(self.rules)[:2], (False, "自动合并没开（还没签）"))
        sig = MP.do_sign("om_1", self.rules)
        self.assertEqual((sig["type"], sig["scope_sha256"], sig["message_id"], sig["open_id"]),
                         ("owner_approved_policy", self.sha, "om_1", "ou_owner"))
        self.assertEqual(MP.active(self.rules)[:2], (True, ""))
        self.assertEqual(oct((self.tmp / "policy.json").stat().st_mode & 0o777), "0o600")
        self.assertTrue(MP.do_off("飞书私聊", "先停一下"))
        self.assertEqual(MP.active(self.rules)[:2], (False, "自动合并没开（签过，已关）"))
        self.assertFalse(MP.do_off("本机命令"))
        hist = MP.load_state()["history"]
        self.assertEqual([(h["event"], h["source"]) for h in hist], [("on", "飞书私聊"), ("off", "飞书私聊"), ("off", "本机命令")])

    def test_message_used_once(self):
        MP.do_sign("om_1", self.rules)
        MP.do_off("x")
        with self.assertRaisesRegex(MP.Rejected, "已经用过"):
            MP.do_sign("om_1", self.rules)
        MP.do_sign("om_2", self.rules)                               # 再说一次就能再开
        self.assertTrue(MP.active(self.rules)[0])

    def test_wrong_text_or_hash_rejected(self):
        for text, word in (("开自动合并", "不是开启口令"), ("开自动合并 000000000000", "哈希对不上"),
                           (f"帮我开自动合并 {self.sha[:12]}", "不是开启口令"), (None, "不是开启口令")):
            self.text = text
            with self.assertRaisesRegex(MP.Rejected, word):
                MP.do_sign("om_9", self.rules)
        self.assertFalse(MP.active(self.rules)[0])
        self.assertFalse((self.tmp / "policy.json").exists())
        with self.assertRaisesRegex(MP.Rejected, "--message-id"):
            MP.do_sign("", self.rules)

    def test_identity_check_failure_rejected(self):
        self.said.stop()
        with unittest.mock.patch.object(MP, "owner_config", lambda: ("ou_owner", "oc_home")), \
                unittest.mock.patch.object(MP, "fetch_message", lambda mid: {
                    "message_id": mid, "sender_type": "user", "sender_open_id": "ou_someone_else", "chat_id": "oc_home",
                    "chat_type": "p2p", "create_time": "2026-11-20 14:59:30", "text": self.text}):
            with self.assertRaisesRegex(MP.Rejected, "发送者不是常浩本人"):
                MP.do_sign("om_abc123", self.rules)
        self.said.start()
        self.assertFalse(MP.active(self.rules)[0])

    def test_real_verification_path_accepts_owner(self):
        self.said.stop()
        with unittest.mock.patch.object(MP, "owner_config", lambda: ("ou_owner", "oc_home")), \
                unittest.mock.patch.object(MP, "fetch_message", lambda mid: {
                    "message_id": mid, "sender_type": "user", "sender_open_id": "ou_owner", "chat_id": "oc_home",
                    "chat_type": "p2p", "create_time": "2026-11-20 14:59:30", "text": self.text + "。"}):
            MP.do_sign("om_abc123", self.rules)
        self.said.start()
        self.assertTrue(MP.active(self.rules)[0])

    def test_status_gives_sign_words_when_off(self):
        text = MP.describe(MP.status(self.rules))
        self.assertIn(f"开自动合并 {self.sha[:12]}", text)
        self.assertIn("每个 PR 仍发卡片", text)
        MP.do_sign("om_1", self.rules)
        self.assertIn("自动合并开着：L0、L1、L2", MP.describe(MP.status(self.rules)))

    def test_ledger_folding_and_bad_lines(self):
        MP.append({"event": "intent", "id": "a", "repo": "o/r", "pr": "1", "head": "h"})
        MP.append({"event": "merged", "id": "a", "merge_commit": "c"})
        MP.append({"event": "intent", "id": "b", "repo": "o/r", "pr": "2", "head": "h"})
        with MP.ledger_path().open("a", encoding="utf-8") as f:
            f.write("不是 JSON\n")
        self.assertEqual([(m["id"], m["status"]) for m in MP.merges()], [("a", "merged"), ("b", "pending")])
        self.assertEqual(([m["id"] for m in MP.unsettled()], MP.lines()[1], MP.merged_count("2026-11-20")), (["b"], 1, 2))
        self.assertEqual(MP.find_merged("o/r", 1)["merge_commit"], "c")
        self.assertIsNone(MP.find_merged("o/r", 2))


if __name__ == "__main__":
    unittest.main()
