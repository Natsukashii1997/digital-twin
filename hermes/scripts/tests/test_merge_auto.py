"""按策略合并（#257，ADR-016）自测：策略记录的签署与失效、auto 交出与合并、例外、巡视合并、上限、结果不明的只读核对、
绕过记录、撤回。夹具同 test_merge_act（真 git 临时仓库、假 gh、假 lark-cli），不联网、不碰真实仓库和记录。"""
import _isolation  # noqa: F401  统一测试隔离（#212），必须是第一句：在导入任何被测脚本之前
import json
import os
import signal
import subprocess
import sys
import time
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import merge_officer as mo  # noqa: E402
import merge_policy as MP  # noqa: E402
from test_merge_act import ACT, OFFICER, Fixture, sh  # noqa: E402

EOF_NET = 'Post "https://api.github.com/graphql": EOF'


class Auto(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.dir = self.tmp / "merge"
        (self.fake / "real_git").write_text(str(self.remote))      # 假 gh 的合并、撤回真的动 Git 对象（撤回要按内容核对）

    def remote_sha(self, ref="refs/heads/main"):
        return sh(self.remote, "rev-parse", ref)

    def kill(self, pids):
        for pid in pids or []:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

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
        """账本里的事件，不含 started（每次合并请求起来都有一行，另有用例专门核对）。"""
        return [r["event"] for r in self.book() if r["event"] != "started"]

    def requests(self):
        """本机还活着的、对 app#5 这个版本的合并请求进程（看门进程和它起的 gh）。"""
        import merge_act
        return merge_act.live_requests("pulls/5/merge", "repos/o/app/pulls/5/merge", f"sha={self.head}")

    def wait_for(self, cond, what, secs=240):             # 机器忙的时候起一个子进程都要好几秒
        end = time.monotonic() + secs
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.05)
        raise AssertionError(f"等了 {secs} 秒没等到：{what}")

    def revert_requests(self):
        import merge_act
        return merge_act.live_requests("pr revert", "pr revert 5 --repo o/app ", str(self.fake))

    def start_bg(self, *args, **env):
        """后台起一个 merge_act.py（要在请求发出之后把它杀掉的用例用）。结束时把留下的进程清掉。"""
        p = subprocess.Popen([sys.executable, "-B", str(ACT), *args], env=self.env(env),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.kill(), p.communicate(), self.kill((self.requests() or []) + (self.revert_requests() or []))))
        return p

    def start_auto(self, **env):
        return self.start_bg("auto", "app#5", **env)

    def handoffs(self):
        f = self.dir / "handoffs.json"
        return json.loads(f.read_text(encoding="utf-8"))["handoffs"] if f.exists() else []

    def state(self, number=5):
        return json.loads((self.fake / "prs.json").read_text())["o/app"][str(number)]["state"]

    def merges_called(self):
        """按策略合并发出的合并请求（REST 合并接口）。"""
        return [c for c in self.calls() if c[:3] == ["api", "-X", "PUT"]]

    def scan_at(self, now):
        r = self.cli(OFFICER, "scan", env={"DT_NOW": now})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def intent(self, at, mid="m-x"):
        """账本里只有「要合」的一条（进程在记了要合之后没了）。"""
        self.dir.mkdir(exist_ok=True)
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir), "DT_NOW": at}):
            MP.append({"event": "intent", "id": mid, "repo": "o/app", "pr": "5", "head": self.head, "title": "PR 5"})

    def mp(self, fn, *args):
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir)}):
            return fn(*args)

    def gh_fails(self, **plan):      # 不叫 fail：那会盖住 unittest 自己的 fail，断言失败会变成 TypeError
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
        landed = self.remote_sha()                                                         # 假 gh 在裸仓库 main 上做出的 squash 提交
        self.assertNotEqual(landed, self.base)
        self.assertIn(f"已按策略合并 app#5@{self.head[:7]} → {landed[:7]}", r.stdout)
        self.assertIn("没拉本机主目录、没安装", r.stdout)
        self.assertIn("撤回：merge_act.py revert app#5", r.stdout)
        (merge,) = self.merges_called()
        self.assertEqual(merge[3:], ["repos/o/app/pulls/5/merge", "-f", "merge_method=squash", "-f", f"sha={self.head}"])   # 绑定判定时的版本
        intent, started, done = self.book()
        self.assertEqual((intent["event"], done["event"], intent["id"]), ("intent", "merged", done["id"]))
        self.assertEqual((started["event"], started["id"], started["limit"]), ("started", intent["id"], 60.0))   # 请求的起止都有记录
        self.assertEqual(done["ended"], {"how": "自己退出", "exit": 0})
        for key, want in (("repo", "o/app"), ("pr", "5"), ("title", "PR 5"), ("head", self.head), ("base", self.base),
                          ("tier", "L0"), ("rules_version", "2"), ("suggestion", mo.MERGE), ("checks", ["test"]),
                          ("identity", "owner-gh")):
            self.assertEqual(intent[key], want, key)
        self.assertEqual(intent["auth"], {"type": "owner_approved_policy", "scope_sha256": MP.scope_sha(self.rules_now()),
                                          "message_id": "om_sign", "signed_at": "2026-11-07T09:00:00+08:00"})
        self.assertEqual((intent["ready"]["mode"], intent["ready"]["by"]), ("handoff", "测试会话"))
        self.assertIn("未核实", intent["review"])
        self.assertEqual((done["merge_commit"], done["base_ref"]), (landed, "main"))       # 合并提交号取自接口回执
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

    def test_file_list_not_fully_visible_is_exception(self):
        """gh 一次最多给 100 个改动文件，改名的只给新路径：看不全就判不了有没有碰敏感路径，按例外交常浩。"""
        self.sign()
        cases = {"只读得到 1 个": {"changedFiles": 116},
                 "有文件改名": {"files": [{"path": "docs/b.md", "changeType": "RENAMED"}]},
                 "来自别的仓库（fork）": {"isCrossRepository": True, "headRepositoryOwner": {"login": "stranger"}},
                 "来自别的仓库（fork）的 PR": {"headRepositoryOwner": {"login": "stranger"}}}      # 光是标志说不是外来的不够
        for word, kw in cases.items():
            self.update_pr(files=[{"path": "docs/a.md"}], changedFiles=1, isCrossRepository=False, headRepositoryOwner={"login": "o"})
            self.update_pr(**kw)
            r = self.auto()
            self.assertEqual(r.returncode, 3, word + r.stdout)
            self.assertIn("属于例外", r.stdout)
            self.assertIn(word, r.stdout)
        self.assertEqual((self.state(), self.merges_called(), self.book(), self.handoffs()), ("OPEN", [], [], []))
        self.scan()                                                  # 和别的例外一样发卡片问常浩
        self.assertEqual(len(self.calls("lark.jsonl")), 1)

    def test_unknown_file_total_or_origin_is_exception_and_gets_card(self):
        """#260-P2-09：GitHub 这次没给改动文件总数 / 来源仓库：核对不了，按例外交常浩（发卡片），不是不声不响地卡着。"""
        self.sign()
        self.update_pr(changedFiles=None)
        r = self.auto()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("属于例外", r.stdout)
        self.assertIn("没给出改动文件总数", r.stdout)
        self.assertEqual((self.state(), self.merges_called(), self.book(), self.handoffs()), ("OPEN", [], [], []))
        self.update_pr(changedFiles=1, isCrossRepository=None)        # 来源仓库读不到：同样
        r = self.auto()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("没给出 PR 的来源仓库", r.stdout)
        self.assertEqual((self.state(), self.merges_called(), self.book(), self.handoffs()), ("OPEN", [], [], []))
        self.scan()
        (card,) = self.calls("lark.jsonl")                           # 巡视发卡片问常浩
        self.assertIn("来源仓库", json.dumps(card, ensure_ascii=False))

    def test_off_during_checks_stops_before_merge(self):
        """核对到一半常浩关了自动合并：合并命令发出前再读一次开关，不合、不记要合。"""
        self.sign()
        (self.fake / "off_on_view").write_text("x")
        r = self.auto()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("自动合并没开（签过，已关）", r.stdout)
        self.assertEqual((self.state(), self.merges_called(), self.book()), ("OPEN", [], []))

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

    def test_handoff_not_in_listing_is_checked_one_by_one(self):
        """#260-P2-10：交出登记的 PR 不在这一轮的列表里（没列全、没取到），不等于它关了：逐个核实，
        核实不了就留着；确认还开着、版本没变的照样合；确认关了或换了版本才作废。"""
        self.sign()
        self.running()
        self.auto()
        (self.fake / "hide_from_list").write_text("5")
        self.gh_fails(view={"n": 9, "msg": EOF_NET})                     # 列表里没有它，单独去读也读不到
        self.scan()
        self.assertEqual(len(self.handoffs()), 1)
        self.gh_fails(view={"n": 0, "msg": ""})
        self.scan()                                                  # 读到了：还开着、版本没变、CI 在跑
        self.assertEqual((len(self.handoffs()), self.book()), (1, []))
        self.passed()
        self.scan()                                                  # 不在列表里也照样合
        self.assertEqual((self.state(), self.events(), self.handoffs()), ("MERGED", ["intent", "merged"], []))

    def test_handoff_dropped_only_when_confirmed_closed_or_moved(self):
        self.sign()
        self.running()
        (self.fake / "hide_from_list").write_text("5")
        for change in ({"state": "CLOSED"}, {"headRefOid": "b" * 40}):
            self.update_pr(state="OPEN", headRefOid=self.head)
            self.assertEqual(self.auto().returncode, 2)
            self.assertEqual(len(self.handoffs()), 1)
            self.update_pr(**change)
            self.scan()
            self.assertEqual(self.handoffs(), [], change)
        self.assertEqual(self.book(), [])

    def test_more_than_100_open_prs_are_all_seen(self):
        """#260-P2-10：一个仓库开着 106 个 PR，交出的那个排在第 106 个：照样判到、合到，交出登记不会被误删。"""
        self.sign()
        data = json.loads((self.fake / "prs.json").read_text())
        five = data["o/app"].pop("5")
        for n in range(200, 305):
            data["o/app"][str(n)] = self.pr(n, f"{n:040x}", ["docs/x.md"])
        data["o/app"]["5"] = five
        self.set_prs(data)
        self.running()
        self.assertEqual(self.auto().returncode, 2)
        self.passed()
        self.scan()
        self.assertEqual((self.state(), self.handoffs()), ("MERGED", []))
        self.assertEqual(len({r["pr"] for r in self.ledger() if r["repo"] == "o/app"}), 106)
        (listing,) = [c for c in self.calls() if c[:2] == ["pr", "list"] and "o/app" in c and "open" in c]
        self.assertEqual(listing[listing.index("--limit") + 1], "1000")

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
        self.gh_fails(view_merged={"n": 3, "msg": EOF_NET})
        r = self.auto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("结果不明", r.stdout)
        self.assertIn("不会重发", r.stdout)
        self.assertEqual(self.events(), ["intent", "unsure"])
        again = self.auto()                                          # 会话再交出：先当场只读核对，不发第二个合并请求
        self.assertEqual(again.returncode, 1)
        self.assertIn("只读核对：app#5", again.stdout)                # 只看得到 PR 已合并，证明不了是这次合的
        self.assertIn("来源不明", again.stdout)
        self.assertEqual(self.events(), ["intent", "unsure", "merged_unknown"])
        self.assertEqual(self.scan().stdout, "")                     # 巡视没有可核对的了
        self.assertEqual(len(self.merges_called()), 1)               # 全程只发过一次合并请求
        self.assertEqual((self.mp(MP.unsettled), self.mp(MP.find_merged, "o/app", 5)), ([], None))
        r = self.cli(ACT, "revert", "app#5")                         # 来源不明的不能用撤回口令
        self.assertIn("账本里没有按策略合并过 app#5", r.stdout)
        self.scan()
        self.assertNotIn("bypass", self.events())                    # 也不重复记成「没经合并执行器」

    def test_unsure_is_reconciled_even_when_policy_off(self):
        """结果不明之后常浩关了自动合并：只读核对照做（不然这条一直挂着，日报天天报结果不明）；不合新的。"""
        self.sign()
        self.gh_fails(view_merged={"n": 3, "msg": EOF_NET})
        self.auto()
        self.assertEqual(self.events(), ["intent", "unsure"])
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir)}):
            self.assertTrue(MP.do_off("测试"))
        out = self.scan().stdout
        self.assertIn("来源不明", out)
        self.assertEqual(self.events(), ["intent", "unsure", "merged_unknown"])
        self.assertEqual(len(self.merges_called()), 1)

    def test_crash_between_intent_and_result_is_unsettled(self):
        """只记了「要合」就没了下文（进程中途没了，请求发没发出去不知道）：按结果不明处理。PR 还开着、版本没变，
        等多久都不算「没合上」（#260-P1-01：本机进程没了、过了多久，都不是 GitHub 那头的凭据）；PR 换了版本才结清。"""
        self.sign()
        self.intent("2026-11-07T09:30:00+08:00")
        self.assertIn("还没核对清楚", self.auto().stdout)
        out = self.scan().stdout                                     # 过了 10 分钟：说一次怎么办，不结算
        self.assertIn("只读核对：app#5", out)
        self.assertIn("还开着、版本没变", out)
        self.assertIn("推一个新提交", out)
        self.assertEqual(self.events(), ["intent"])
        self.assertEqual(self.scan_at("2026-11-07T23:30:00+08:00"), "")           # 同一天不重复说；过了 14 小时也不结算
        self.assertIn("推一个新提交", self.scan_at("2026-11-09T10:00:00+08:00"))   # 两天后仍然挂着，每天说一次
        self.assertEqual((self.events(), self.merges_called(), self.state()), (["intent"], [], "OPEN"))
        frozen = self.auto()
        self.assertEqual(frozen.returncode, 1)
        self.assertIn("推一个新提交", frozen.stdout)
        new_head = self.pr_commit(5, {"docs/a.md": "hi again\n"})    # 推了新提交：旧请求绑着旧版本，合不了它
        self.update_pr(headRefOid=new_head)
        r = self.auto(f"app#5@{new_head[:7]}")                       # 会话看过新版本、写明版本号重新交出：结清旧的，再合新版本
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("没合上", r.stdout)
        self.assertEqual(self.events(), ["intent", "not_merged", "intent", "merged"])
        (merge,) = self.merges_called()
        self.assertIn(f"sha={new_head}", merge)

    def test_rerunning_the_old_handoff_after_a_new_commit_never_merges_the_new_version(self):
        """第 4 轮审查问：cmd_auto 当场核对把旧请求结成 not_merged 之后，同一次调用会接着登记、试合当前版本。
        调用方把原来那条交出命令原样重跑，会不会把没人交出过的新版本合进去？不会：交出命令带着版本前缀，
        auto_try 用它当 want，PR 现在不是这个版本就停下、不登记、不发请求。新版本要合，得有人交出新版本。"""
        self.sign()
        self.intent("2026-11-07T09:00:00+08:00")                     # 旧版本 self.head 的请求结果不明
        old = self.head
        new_head = self.pr_commit(5, {"docs/a.md": "someone else pushed\n"})
        self.update_pr(headRefOid=new_head)                          # 之后有人推了新提交
        r = self.auto(f"app#5@{old[:7]}")                            # 原样重跑当初的交出命令
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("没合上", r.stdout)                             # 旧请求按远端依据（版本变了）结清
        self.assertIn(f"不是交出的 {old[:7]}", r.stdout)
        self.assertEqual((self.events(), self.merges_called(), self.handoffs(), self.state()),
                         (["intent", "not_merged"], [], [], "OPEN"))
        self.assertEqual(self.scan_at("2026-11-07T10:20:00+08:00"), "")           # 巡视也不合：没有新版本的交出登记
        self.assertEqual(self.merges_called(), [])
        r = self.auto(f"app#5@{new_head[:7]}")                       # 会话看过新版本、交出它：这才合
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (merge,) = self.merges_called()
        self.assertIn(f"sha={new_head}", merge)

    def test_rerunning_a_handoff_without_version_after_a_new_commit_stops(self):
        """#260-P1-01 第 5 轮：旧版本的请求结果不明，PR 被推了新版本，会话异常恢复后重跑当初没写版本号的交出命令。
        当场核对把旧请求结清之后停下：不登记新版本、不记新的要合、不发请求，PR 仍开着；要写明版本号再交出。"""
        self.sign()
        self.intent("2026-11-07T09:00:00+08:00")
        new_head = self.pr_commit(5, {"docs/a.md": "someone else pushed\n"})
        self.update_pr(headRefOid=new_head)
        r = self.auto("app#5")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("没合上", r.stdout)
        self.assertIn("写明版本号", r.stdout)
        self.assertEqual((self.events(), self.handoffs(), self.merges_called(), self.state()),
                         (["intent", "not_merged"], [], [], "OPEN"))
        self.assertEqual(self.scan_at("2026-11-07T10:20:00+08:00"), "")           # 巡视也不合
        self.assertEqual(self.merges_called(), [])
        r = self.auto("app#5")                                       # 旧请求已经结清，再跑一次同样的命令：照样停下（第 6 轮）
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("写明版本号", r.stdout)
        self.assertEqual((self.merges_called(), self.handoffs(), self.events()), ([], [], ["intent", "not_merged"]))
        r = self.auto(f"app#5@{new_head[:7]}")                       # 写明看过的版本：才合
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (merge,) = self.merges_called()
        self.assertIn(f"sha={new_head}", merge)

    def test_patrol_settles_first_then_handoff_without_version_still_stops(self):
        """#260-P1-01 第 6 轮：巡视先把旧请求结清（PR 换了版本），会话之后才重跑没写版本号的交出命令。
        这时已经没有结果不明的记录，也不能把这条命令当成对新版本的授权：停下，不新增要合、不登记、不发请求。"""
        self.sign()
        self.intent("2026-11-07T09:00:00+08:00")
        new_head = self.pr_commit(5, {"docs/a.md": "someone else pushed\n"})
        self.update_pr(headRefOid=new_head)
        self.assertIn("没合上", self.scan_at("2026-11-07T10:20:00+08:00"))     # 巡视先结清
        self.assertEqual((self.mp(MP.unsettled), self.events()), ([], ["intent", "not_merged"]))
        r = self.auto("app#5", env={"DT_NOW": "2026-11-07T10:30:00+08:00"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("换了版本", r.stdout)
        self.assertIn("写明版本号", r.stdout)
        self.assertEqual((self.events(), self.handoffs(), self.merges_called(), self.state()),
                         (["intent", "not_merged"], [], [], "OPEN"))
        r = self.auto(f"app#5@{new_head[:7]}", env={"DT_NOW": "2026-11-07T10:35:00+08:00"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (merge,) = self.merges_called()
        self.assertIn(f"sha={new_head}", merge)

    def test_leading_zeros_in_pr_number_are_the_same_pr(self):
        """#260-P1-01 第 7 轮：gh 和 GitHub 把 app#05、app#0005 都当成 app#5；账本按字符串比。PR 号不规范的话，
        前导零能绕过「有过没合上就要写版本号」和「结果不明先冻结」。两种都要拦住，账本和交出登记里也只记 5。"""
        self.sign()
        self.intent("2026-11-07T09:00:00+08:00")                     # 结果不明、PR 还开着、版本没变
        for ref in ("app#05", "app#0005"):
            r = self.auto(ref)
            self.assertEqual(r.returncode, 1, ref + r.stdout + r.stderr)
            self.assertIn("还没核对清楚", r.stdout)                  # 冻结：不重发
        self.assertEqual((self.merges_called(), self.events()), ([], ["intent"]))
        new_head = self.pr_commit(5, {"docs/a.md": "someone else pushed\n"})
        self.update_pr(headRefOid=new_head)
        self.scan_at("2026-11-07T10:20:00+08:00")                    # 巡视结清：app#5 有过没合上
        r = self.auto("app#005", env={"DT_NOW": "2026-11-07T10:30:00+08:00"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("写明版本号", r.stdout)
        self.assertEqual((self.merges_called(), self.handoffs(), self.events()), ([], [], ["intent", "not_merged"]))
        r = self.auto(f"app#05@{new_head[:7]}", env={"DT_NOW": "2026-11-07T10:35:00+08:00"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        intents = [b for b in self.book() if b["event"] == "intent"]
        self.assertEqual([b["pr"] for b in intents], ["5", "5"])     # 账本里只有规范写法
        (merge,) = self.merges_called()
        self.assertEqual(merge[3], "repos/o/app/pulls/5/merge")
        r = self.cli(ACT, "revert", "app#05")                        # 撤回也认同一个 PR
        self.assertIn("已开撤回 PR", r.stdout)

    def test_settled_because_closed_says_closed(self):
        """#260-P2-17：旧请求是因为 PR 被关掉才结清的，提示要说 PR 已关闭，不说换了版本。"""
        self.sign()
        self.intent("2026-11-07T09:00:00+08:00")
        self.update_pr(state="CLOSED")
        self.scan_at("2026-11-07T10:20:00+08:00")
        self.assertEqual(self.book()[-1]["state_now"], "CLOSED")
        self.update_pr(state="OPEN")                                 # 有人重新打开
        r = self.auto("app#5", env={"DT_NOW": "2026-11-07T10:30:00+08:00"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PR 已关闭", r.stdout)
        self.assertNotIn("换了版本", r.stdout)
        self.assertEqual(self.merges_called(), [])

    def test_unsettled_is_never_resent_by_patrol(self):
        """#260-P1-01：交出登记还在、检查全过、上一次请求结果不明。巡视只读核对，不管过多久都不自己重发，也不放开重新交出。"""
        self.sign()
        self.running()
        self.assertEqual(self.auto().returncode, 2)                  # 真的交出登记
        self.passed()
        self.intent("2026-11-07T09:55:00+08:00")                     # 5 分钟前记了要合，然后进程没了
        self.assertEqual(self.scan().stdout, "")                     # 离发请求不到 10 分钟：先不说
        for now in ("2026-11-07T10:20:00+08:00", "2026-11-07T10:50:00+08:00", "2026-11-08T10:50:00+08:00"):
            self.scan_at(now)
            self.assertEqual((self.events(), len(self.handoffs()), self.merges_called(), self.state()), (["intent"], 1, [], "OPEN"), now)
        self.update_pr(state="CLOSED")                               # 关掉了：关着的 PR 合不了，这才结清、交出登记作废
        out = self.scan_at("2026-11-08T11:20:00+08:00")
        self.assertIn("没合上（PR 现在是 CLOSED", out)
        self.assertEqual((self.events(), self.handoffs(), self.merges_called()), (["intent", "not_merged"], [], []))

    # ---------- 合并请求的硬期限、发起方死掉之后（#260-P1-01） ----------

    def test_parent_killed_after_request_started_old_request_is_reported_not_killed(self):
        """发起合并的进程在请求发出之后被杀：请求的进程（看门进程 + gh）还活着。巡视不下结论，也不去停它
        （#260-P2-14：按命令行认进程有认错的可能）：过了期限告诉常浩进程号。进程没了以后 PR 还开着，照样不结算。"""
        self.sign()
        (self.fake / "merge_hang").write_text("900")
        p = self.start_auto(DT_MERGE_REQ_TIMEOUT="900")              # 期限放得很长：专门测「看门进程也指望不上」时巡视怎么办
        self.wait_for(lambda: self.merges_called() and any(b["event"] == "started" for b in self.book()), "合并请求发出")
        p.kill()
        p.wait()
        alive = self.requests()
        self.assertGreaterEqual(len(alive), 2)                       # 发起方死了，看门进程和 gh 都还在
        self.assertEqual(self.scan_at("2026-11-07T10:05:00+08:00"), "")           # 不到 10 分钟：不说，也不动它
        out = self.scan_at("2026-11-07T10:20:00+08:00")
        self.assertIn("还活着", out)
        self.assertIn("脚本不会去停它", out)
        for pid in alive:
            self.assertIn(str(pid), out)
        self.assertEqual(sorted(self.requests()), sorted(alive))     # 一个都没被停
        self.assertEqual(self.scan_at("2026-11-07T10:50:00+08:00"), "")           # 一天说一次
        self.update_pr(state="CLOSED")                               # 进程还活着：PR 关了也不下结论
        self.scan_at("2026-11-07T11:20:00+08:00")
        self.assertEqual(self.events(), ["intent"])
        self.update_pr(state="OPEN")
        self.kill(alive)                                             # 常浩在电脑上确认后手动停掉
        self.wait_for(lambda: self.requests() == [], "旧请求被手动停掉")
        self.assertIn("还开着、版本没变", self.scan_at("2026-11-08T10:00:00+08:00"))   # 进程没了、PR 还开着：仍然不结算
        self.assertEqual((self.events(), self.state(), len(self.merges_called())), (["intent"], "OPEN", 1))

    def test_request_dies_by_its_own_deadline_after_parent_is_killed(self):
        """请求的期限不靠发起它的进程：发起方被杀之后，看门进程到点照样杀掉 gh、自己退出。"""
        self.sign()
        (self.fake / "merge_hang").write_text("900")
        p = self.start_auto(DT_MERGE_REQ_TIMEOUT="30")
        self.wait_for(lambda: self.merges_called() and any(b["event"] == "started" for b in self.book()), "合并请求发出")
        p.kill()
        p.wait()
        self.assertEqual(self.events(), ["intent"])                  # 发起方死在请求结束之前：没有它来记结果、来杀子进程
        self.wait_for(lambda: self.requests() == [], "到期后请求的进程自己没了", 240)
        self.assertEqual((self.state(), len(self.merges_called())), ("OPEN", 1))

    def test_request_timeout_is_unsure_and_leaves_no_process(self):
        self.sign()
        (self.fake / "merge_hang").write_text("900")
        r = self.auto(env={"DT_MERGE_REQ_TIMEOUT": "1"})
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("结果不明", r.stdout)
        self.assertIn("超过 1 秒没回应", r.stdout)
        self.assertEqual(self.requests(), [])                        # 返回时请求的进程已经不在
        intent, started, unsure = self.book()
        self.assertEqual((started["event"], started["limit"], unsure["event"], unsure["ended"]),
                         ("started", 1.0, "unsure", {"how": "到期杀掉", "exit": 124}))
        self.assertEqual(self.scan().stdout, "")                     # 刚发出：不说
        out = self.scan_at("2026-11-07T10:20:00+08:00")              # 本机的请求已经结束，也不等于 GitHub 没收到：不结算
        self.assertIn("还开着、版本没变", out)
        self.assertEqual(self.events(), ["intent", "unsure"])
        self.update_pr(headRefOid="d" * 40)                          # PR 换了版本：结清
        self.assertIn("没合上", self.scan_at("2026-11-07T10:50:00+08:00"))
        self.assertEqual(self.events(), ["intent", "unsure", "not_merged"])

    def test_cannot_prove_request_dead_stays_unsettled(self):
        """进程表查不了：证明不了旧请求的进程已经不在，PR 关了、换了版本也不结算。"""
        self.sign()
        self.intent("2026-11-07T09:00:00+08:00")
        self.update_pr(state="CLOSED")
        blind = {"DT_PGREP": str(self.tmp / "no-pgrep")}
        r = self.cli(OFFICER, "scan", env={"DT_NOW": "2026-11-07T10:20:00+08:00", **blind})
        self.assertIn("进程表查不了", r.stdout)
        r = self.cli(OFFICER, "scan", env={"DT_NOW": "2026-11-07T10:50:00+08:00", **blind})
        self.assertEqual(r.stdout, "")                               # 一天说一次
        self.assertEqual(self.events(), ["intent"])
        self.update_pr(state="OPEN")
        self.assertIn("还没核对清楚", self.auto().stdout)
        self.update_pr(state="MERGED", mergeCommit={"oid": "a" * 40})             # 已合并不用证明进程：照样记来源不明
        r = self.cli(OFFICER, "scan", env={"DT_NOW": "2026-11-07T11:20:00+08:00", **blind})
        self.assertIn("来源不明", r.stdout)

    def test_ps_failure_on_a_found_process_is_not_no_process(self):
        """#260-P1-01：pgrep 筛出了一个进程、ps 却读不出它的命令行，而那个进程还在：不能当成「没有这次请求的进程」。
        那个进程确实已经没了（筛出来之后退出）的，才可以跳过。"""
        import merge_act
        mark = f"repos/o/app/pulls/5/merge sha={self.head}"
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)", mark])
        self.addCleanup(lambda: (sleeper.kill(), sleeper.wait()))
        self.wait_for(lambda: sleeper.pid in (self.requests() or []), "带着请求特征的进程出现在进程表里")
        bad_ps = self.tmp / "bad-ps"
        bad_ps.write_text("#!/bin/sh\nexit 1\n")
        bad_ps.chmod(0o755)
        with unittest.mock.patch.object(merge_act, "PS", str(bad_ps)):
            self.assertIsNone(self.requests())                       # 进程还在、命令行读不出：查不了
        self.sign()
        self.intent("2026-11-07T09:00:00+08:00")
        self.update_pr(state="CLOSED")
        r = self.cli(OFFICER, "scan", env={"DT_NOW": "2026-11-07T10:20:00+08:00", "DT_PS": str(bad_ps)})
        self.assertIn("进程表查不了", r.stdout)
        self.assertEqual(self.events(), ["intent"])                  # 不结算
        gone = self.tmp / "stale-pgrep"                              # pgrep 报了一个已经退出的进程号：确实没了，可以跳过
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        gone.write_text(f"#!/bin/sh\necho {dead.pid}\n")
        gone.chmod(0o755)
        with unittest.mock.patch.object(merge_act, "PGREP", str(gone)), unittest.mock.patch.object(merge_act, "PS", str(bad_ps)):
            self.assertEqual(self.requests(), [])

    def test_no_process_is_ever_killed_by_the_script(self):
        """#260-P2-14：脚本里没有停进程的路径（只有等自己起的那一个请求时的连组收场）。"""
        import merge_act
        self.assertFalse(hasattr(merge_act, "kill_requests"))
        src = Path(merge_act.__file__).read_text(encoding="utf-8")
        self.assertEqual(src.count("os.kill("), 1)                   # 只有 live_requests 里的信号 0 探测
        self.assertIn("os.kill(pid, 0)", src)
        self.assertEqual(src.count("os.killpg("), 1)                 # _gh_finish：收自己刚起的那个请求的进程组

    def test_gh_not_runnable_means_nothing_was_sent(self):
        import merge_act
        res = merge_act._gh_finish(merge_act._gh_start([str(self.tmp / "no-such-gh"), "api"]))
        self.assertEqual((res["launched"], res["ended"]), (False, {"how": "没起来", "exit": 127}))

    def test_network_cut_is_unsure_even_if_pr_still_open(self):
        """合并请求发出时断网：回读 PR 还开着也不能记「没合上」放开重试（请求可能还在路上）。"""
        self.sign()
        self.gh_fails(merge={"n": 1, "msg": "x"})
        r = self.auto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("结果不明", r.stdout)
        self.assertEqual((self.state(), self.events()), ("OPEN", ["intent", "unsure"]))
        self.assertEqual(self.scan().stdout, "")
        self.assertEqual((self.events(), len(self.merges_called())), (["intent", "unsure"], 1))

    def test_reply_lost_after_merge_is_unknown_source(self):
        """GitHub 合上了、回执丢在路上：没有回执就不算这次合的。"""
        self.sign()
        self.gh_fails(merge_lost={"n": 1, "msg": "x"})
        self.assertIn("结果不明", self.auto().stdout)
        self.assertEqual((self.state(), self.events()), ("MERGED", ["intent", "unsure"]))
        self.assertIn("来源不明", self.scan().stdout)
        self.assertEqual(self.events()[-1], "merged_unknown")

    def test_merged_elsewhere_is_not_claimed(self):
        """#260-P1-02：记了要合之后被别处（网页、卡片、会话直接 gh）抢先合了：不记成按策略合并，不给撤回资格。"""
        self.sign()
        (self.fake / "other_merges_first").write_text("x")
        r = self.auto()
        self.assertEqual(r.returncode, 1)
        self.assertIn("结果不明", r.stdout)
        self.assertEqual((self.state(), self.events()), ("MERGED", ["intent", "unsure"]))
        self.assertIn("来源不明", self.scan().stdout)
        self.assertIsNone(self.mp(MP.find_merged, "o/app", 5))
        self.assertEqual(self.ledger()[0]["owner"], "")              # 判定记录里也不记「策略合并」

    def test_label_added_between_reads_is_exception(self):
        """#260-P1-04：第一次读取没有 needs-info，最后一次读取时有了：按例外办，不合，交出登记作废。"""
        self.sign()
        (self.fake / "label_on_view2").write_text("")
        r = self.auto()
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertIn("标签 needs-info", r.stdout)
        self.assertEqual((self.state(), self.merges_called(), self.book(), self.handoffs()), ("OPEN", [], [], []))

    def test_off_and_merge_start_share_one_lock(self):
        """#260-P1-03：最后读开关、记要合、起合并请求在开关锁里做。开关锁被占着（正在关）时，合并请求起不来。"""
        self.sign()
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir)}), MP.locked(1, "policy.lock"):
            r = self.auto()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("没办成", r.stdout)
        self.assertEqual((self.state(), self.merges_called(), self.book()), ("OPEN", [], []))

    def test_card_merge_takes_merge_lock(self):
        """#260-P2-01、P2-02：卡片合并也拿合并锁；等不到就什么都不做，请常浩过会儿再点。"""
        self.scan()                                                  # 策略没开：发了卡片
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir)}), MP.locked(1):
            r = self.cli(ACT, "merge", self.ref, env={"DT_MERGE_LOCK_WAIT": "0.3"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("合并官正在巡视", r.stdout)
        self.assertEqual((self.state(), [c for c in self.calls() if c[:2] == ["pr", "merge"]]), ("OPEN", []))
        self.assertEqual(self.cli(ACT, "merge", self.ref).returncode, 0)
        self.assertEqual((self.state(), self.ledger()[0]["owner"]), ("MERGED", "合并"))

    def test_card_merge_waits_for_unsettled_policy_merge(self):
        self.scan()
        self.sign()
        self.intent("2026-11-07T09:58:00+08:00")
        r = self.cli(ACT, "merge", self.ref)
        self.assertEqual(r.returncode, 1)
        self.assertIn("结果不明", r.stdout)
        self.assertEqual(self.state(), "OPEN")

    def test_card_sent_when_pr_becomes_exception_or_policy_turns_off(self):
        """#260-P2-05：判定过之后才加 needs-info 的补一张卡片；策略后来关了，没发过卡片的开着的 PR 也补。"""
        self.sign()
        self.scan()
        self.assertEqual(self.calls("lark.jsonl"), [])               # 普通 PR、策略生效：不发卡片
        self.update_pr(labels=[{"name": "needs-info"}])
        self.scan()
        self.scan()
        (card,) = self.calls("lark.jsonl")                           # 版本没变也补发，只发一次
        self.assertIn("needs-info", json.dumps(card, ensure_ascii=False))
        self.update_pr(labels=[])
        self.set_prs({**json.loads((self.fake / "prs.json").read_text()),
                      "o/app": {**json.loads((self.fake / "prs.json").read_text())["o/app"],
                                "6": self.pr(6, "6" * 40, ["docs/b.md"])}})
        self.scan()
        self.assertEqual(len(self.calls("lark.jsonl")), 1)           # 新来的普通 PR：策略生效，不发
        self.mp(MP.do_off, "测试")
        self.scan()
        self.assertEqual(len(self.calls("lark.jsonl")), 2)           # 关了以后：补上

    # ---------- 巡视时合并被拒绝（#260-P2-13） ----------

    def test_patrol_says_permanent_rejection_once_and_stops_hammering(self):
        """权限不够这类不会自己好的拒绝：说一次、交出登记留着、当天不再每轮撞；会话重新交出当场再试，第二天巡视再试一次。"""
        self.sign()
        self.running()
        self.auto()
        self.passed()
        (self.fake / "merge_deny").write_text("x")
        out = self.scan().stdout
        self.assertIn("没合上", out)
        self.assertIn("HTTP 403", out)
        self.assertIn("今天不再重试", out)
        self.assertEqual((len(self.handoffs()), len(self.merges_called())), (1, 1))
        self.assertTrue(self.book()[-1]["permanent"])
        self.assertEqual(self.scan().stdout, "")                     # 同一天：不再说，也不再发请求
        self.assertEqual(len(self.merges_called()), 1)
        out = self.scan_at("2026-11-08T10:00:00+08:00")              # 第二天：再试一次、再说一次
        self.assertIn("HTTP 403", out)
        self.assertEqual(len(self.merges_called()), 2)
        (self.fake / "merge_deny").unlink()
        self.assertEqual(self.auto(env={"DT_NOW": "2026-11-08T10:05:00+08:00"}).returncode, 0)   # 修好后会话重新交出：当场合

    def test_patrol_says_transient_rejection_once_but_keeps_retrying(self):
        self.sign()
        self.running()
        self.auto()
        self.passed()
        (self.fake / "merge_fail").write_text("x")
        out = self.scan().stdout
        self.assertIn("没合上", out)
        self.assertIn("下一轮巡视会再试", out)
        self.assertFalse(self.book()[-1]["permanent"])
        self.assertEqual(self.scan().stdout, "")                     # 不重复提醒，但照样再试
        self.assertEqual((len(self.merges_called()), len(self.handoffs())), (2, 1))
        (self.fake / "merge_fail").unlink()
        self.scan()
        self.assertEqual(self.state(), "MERGED")

    def rejected_with(self, text):
        """交出过、检查全过，巡视时合并接口照 text 拒绝。返回 (巡视说的话, 账本最后一行, 第二轮巡视后一共发了几次合并请求)。"""
        self.sign()
        self.running()
        self.auto()
        self.passed()
        (self.fake / "merge_error").write_text(text)
        out = self.scan().stdout
        last = self.book()[-1]
        self.scan()
        return out, last, len(self.merges_called())

    def test_rate_limit_403_is_transient(self):
        """#260-P2-13：限流也可能回 403，不能因为是 403 就当成权限问题、当天不再试。"""
        out, last, sent = self.rejected_with("gh: You have exceeded a secondary rate limit. Please wait a few minutes before you try again. (HTTP 403)")
        self.assertEqual((last["event"], last["permanent"], sent, self.state(), len(self.handoffs())), ("failed", False, 2, "OPEN", 1))
        self.assertIn("下一轮巡视会再试", out)

    def test_rate_limit_429_is_rejected_and_transient(self):
        """429 原来不在明确拒绝里，会被记成结果不明、把 PR 一直挂住。"""
        out, last, sent = self.rejected_with("gh: API rate limit exceeded for user ID 1. (HTTP 429)")
        self.assertEqual((last["event"], last["permanent"], sent, self.state()), ("failed", False, 2, "OPEN"))
        self.assertIn("下一轮巡视会再试", out)

    def test_review_required_405_is_permanent_for_the_day(self):
        out, last, sent = self.rejected_with("gh: At least 1 approving review is required by reviewers with write access. (HTTP 405)")
        self.assertEqual((last["event"], last["permanent"], sent, self.state(), len(self.handoffs())), ("failed", True, 1, "OPEN", 1))
        self.assertIn("今天不再重试", out)
        self.assertIn("approving review", out)

    def test_validation_failed_422_is_permanent_for_the_day(self):
        out, last, sent = self.rejected_with("gh: Validation Failed (HTTP 422)")
        self.assertEqual((last["event"], last["permanent"], sent), ("failed", True, 1))
        self.assertIn("今天不再重试", out)

    def test_explicit_rejection_with_failed_readback_is_failed_not_unsure(self):
        """GitHub 回了 4xx（这里是限流 429），紧跟着的回读也失败（同样被限流或断网）：服务端已经答复不处理这次请求，
        记没合上、下一轮再试；不能记成结果不明，那样 PR 会一直挂着要人推新提交才放开（#260 送审前自审）。"""
        self.sign()
        (self.fake / "merge_error").write_text("gh: API rate limit exceeded for user ID 1. (HTTP 429)")
        self.gh_fails(view_after_put={"n": 9, "msg": EOF_NET})
        r = self.auto()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("没合上", r.stdout)
        self.assertEqual((self.events(), self.book()[-1]["permanent"], self.mp(MP.unsettled)), (["intent", "failed"], False, []))
        self.gh_fails(view_after_put={"n": 0, "msg": ""})
        (self.fake / "merge_error").unlink()
        self.scan()                                                  # 交出登记还在：下一轮巡视照常再试
        self.assertEqual(self.state(), "MERGED")

    def test_status_codes_count_only_in_gh_form(self):
        """状态码只认 gh api 的「(HTTP 4xx)」写法。传输出错时 gh 的报错里带着请求地址，PR 号恰好是 429、422 这类数字，
        不能当成 GitHub 明确拒绝（不然请求其实到了 GitHub 时会被记成没合上、再发一次）。"""
        import merge_act
        head = "a" * 40
        opened = {"state": "OPEN", "headRefOid": head, "mergeCommit": None}
        res = {"launched": True, "returncode": 1}
        for n in ("401", "403", "405", "409", "422", "429"):
            text = f'Put "https://api.github.com/repos/o/r/pulls/{n}/merge": http2: client connection lost'
            self.assertEqual(merge_act._merge_verdict(res, {}, text, opened, head), "unsure", n)
            self.assertEqual(merge_act._merge_verdict(res, {}, text, None, head), "unsure", n)
            self.assertIsNone(merge_act.TRANSIENT.search(f"something about pulls/{n}/merge"), n)
        self.assertEqual(merge_act._merge_verdict(res, {}, "gh: Validation Failed (HTTP 422)", None, head), "failed")
        self.assertEqual(merge_act._merge_verdict(res, {}, "gh: Validation Failed (HTTP 422)", opened, head), "failed")
        self.assertEqual(merge_act._merge_verdict(res, {}, "gh: Server Error (HTTP 502)", None, head), "unsure")
        self.assertEqual(merge_act._merge_verdict(res, {}, "Cannot update this protected ref", None, head), "unsure")  # 没有状态码：要回读

    def test_unreadable_state_is_not_settled(self):
        """只读核对拿到的 PR 状态或版本是空的：没有依据，不结算。"""
        self.sign()
        self.intent("2026-11-07T09:00:00+08:00")
        self.update_pr(state=None)
        self.scan()
        self.update_pr(state="OPEN", headRefOid=None)
        self.scan_at("2026-11-08T10:00:00+08:00")
        self.assertEqual((self.events(), len(self.mp(MP.unsettled))), (["intent"], 1))

    def test_base_branch_modified_is_transient(self):
        out, last, sent = self.rejected_with("gh: Base branch was modified. Review and try the merge again. (HTTP 405)")
        self.assertEqual((last["event"], last["permanent"], sent), ("failed", False, 2))

    def test_cards_file_updates_do_not_lose_each_other(self):
        """#260-P2-11：cards.json 有两个写入者（巡视发卡片、退回按钮）。四个进程同时各写 25 条，一条都不能丢。"""
        code = ("import sys, merge_act as A\n"
                "for i in range(25):\n"
                "    A.update_cards(lambda d: d['cards'].append({'who': sys.argv[1], 'i': i}))\n")
        procs = [subprocess.Popen([sys.executable, "-B", "-c", code, str(n)], cwd=str(HERE.parent), env=self.env())
                 for n in range(4)]
        self.assertEqual([p.wait(timeout=120) for p in procs], [0, 0, 0, 0])
        cards = json.loads((self.shadow / "cards.json").read_text(encoding="utf-8"))["cards"]
        self.assertEqual(len({(c["who"], c["i"]) for c in cards}), 100)
        self.assertEqual([f.name for f in self.shadow.iterdir() if ".tmp" in f.name], [])

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
        self.assertEqual(self.events(), ["intent", "merged", "revert_intent", "revert_opened", "intent", "merged"])
        with unittest.mock.patch.dict(os.environ, {"DT_MERGE_DIR": str(self.dir)}):
            self.assertEqual(MP.find_merged("o/app", 5)["revert"]["revert_pr"], "105")
        again = self.cli(ACT, "revert", "app#5")
        self.assertIn("已经开过撤回 PR", again.stdout)
        self.assertIn("撤回 PR 已经合并", again.stdout)
        self.assertEqual(len([c for c in self.calls() if c[:2] == ["pr", "revert"]]), 1)

    def test_revert_pr_waits_for_owner_when_auto_off(self):
        self.set_auto(revert_auto_merge=False)
        self.sign()
        self.auto()
        r = self.cli(ACT, "revert", "app#5")
        self.assertIn("撤回 PR 没有自动合并，等常浩说合并", r.stdout)
        self.assertEqual(self.state(105), "OPEN")

    def reverted(self, **before):
        """按策略合了 app#5，再开出撤回 PR app#105（before：开撤回 PR 之前先改 app#5 的状态，撤回 PR 会照抄）。"""
        self.sign()
        self.assertEqual(self.auto().returncode, 0)
        if before:
            self.update_pr(**before)
        return self.cli(ACT, "revert", "app#5", "合错了")

    RUNNING = {"statusCheckRollup": [{"name": "test", "status": "IN_PROGRESS", "conclusion": ""}], "mergeStateStatus": "UNSTABLE"}
    PASSED = {"statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}], "mergeStateStatus": "CLEAN"}

    def reverts_called(self):
        return [c for c in self.calls() if c[:2] == ["pr", "revert"]]

    def merges_of(self, number):
        return [c for c in self.merges_called() if c[3] == f"repos/o/app/pulls/{number}/merge"]

    def test_revert_records_head_at_open_and_resumes_with_it(self):
        """撤回 PR 开出时 CI 还在跑：记下开出时的版本、登记交出；之后再跑一次撤回命令（或等巡视）接着合的就是这个版本。"""
        r = self.reverted(**self.RUNNING)
        self.assertIn("已开撤回 PR：https://github.com/o/app/pull/105", r.stdout)
        self.assertIn("现在还不能合", r.stdout)
        opened = [b for b in self.book() if b["event"] == "revert_opened"][0]
        born = self.remote_sha("refs/pull/105/head")                 # 假 gh 在裸仓库里做出的撤回提交
        self.assertEqual(opened["revert_head"], born)
        self.assertEqual([(h["pr"], h["head"]) for h in self.handoffs()], [("105", born)])
        self.update_pr(105, **self.PASSED)
        again = self.cli(ACT, "revert", "app#5")                     # #260-P2-03：再跑一次接着合，不重复开
        self.assertIn("已按策略合并 app#105", again.stdout)
        self.assertEqual((self.state(105), len(self.reverts_called())), ("MERGED", 1))
        (merge,) = self.merges_of(105)
        self.assertIn("sha=" + born, merge)

    # ---------- 撤回 PR 按内容核对（#260-P1-07） ----------

    def modifying_pr(self):
        """把 app#5 换成「改 README 一行」的 PR（+1 −1），撤回它也是 +1 −1：这样才做得出同文件、同行数、内容不同的冒充提交。"""
        self.head = self.pr_commit(5, {"README.md": "v2\n"})
        self.update_pr(headRefOid=self.head, files=[{"path": "README.md", "additions": 1, "deletions": 1}])

    def forge(self, readme, extra=False):
        """做一个落在 main 最新提交上、只改 README.md 的提交，让假 gh 把它当成撤回 PR 开出后被人换上的版本。
        extra：再在上面叠一个空提交（撤回提交下面夹着别的提交）。返回它的提交号。"""
        sh(self.main, "fetch", "-q", "origin")
        sh(self.main, "checkout", "-q", "--detach", "origin/main")
        (self.main / "README.md").write_text(readme)
        sh(self.main, "commit", "-qam", "Revert pr 5")
        if extra:
            sh(self.main, "commit", "-q", "--allow-empty", "-m", "extra")
        head = sh(self.main, "rev-parse", "HEAD")
        sh(self.main, "push", "-q", "-f", "origin", "HEAD:refs/forged/105")
        sh(self.main, "checkout", "-q", "main")
        (self.fake / "revert_head").write_text(head)
        return head

    def opened(self):
        return [b for b in self.book() if b["event"] == "revert_opened"]

    def test_revert_swapped_for_same_files_same_line_counts_different_content_is_not_auto_merged(self):
        """撤回 PR 开出后、第一次读取前，被换成「同一个文件、同样的增删行数、内容不同」的提交。GitHub 给的文件名和
        行数和老实的撤回一模一样；按内容核对才认得出：不记 revert_head，不自动合并，之后再跑、巡视也不合。"""
        self.modifying_pr()
        self.sign()
        self.assertEqual(self.auto().returncode, 0)
        bad = self.forge("v9\n")                                     # 老实的撤回是改回 v1
        r = self.cli(ACT, "revert", "app#5", "合错了")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已开撤回 PR：https://github.com/o/app/pull/105", r.stdout)
        self.assertIn("文件内容对不上", r.stdout)
        self.assertIn("撤回 PR 没有自动合并", r.stdout)
        pr105 = json.loads((self.fake / "prs.json").read_text())["o/app"]["105"]
        self.assertEqual((pr105["headRefOid"], pr105["files"]), (bad, [{"path": "README.md", "additions": 1, "deletions": 1}]))
        (opened,) = self.opened()
        self.assertNotIn("revert_head", opened)
        self.assertIn("文件内容对不上", opened["head_unverified"])
        self.scan()
        self.cli(ACT, "revert", "app#5")
        self.assertEqual((self.state(105), self.merges_of(105), self.handoffs()), ("OPEN", [], []))

    def test_revert_with_another_commit_underneath_is_not_auto_merged(self):
        """最后的内容和老实的撤回一样，但撤回 PR 的版本下面夹着一个不在 main 上的提交：也不认。"""
        self.modifying_pr()
        self.sign()
        self.auto()
        self.forge("v1\n", extra=True)
        r = self.cli(ACT, "revert", "app#5")
        self.assertIn("夹着不在 main 上的提交", r.stdout)
        self.assertNotIn("revert_head", self.opened()[0])
        self.assertEqual((self.state(105), self.merges_of(105)), ("OPEN", []))

    def test_honest_revert_is_verified_and_merged_even_after_main_moved_on(self):
        """原合并之后 main 又往前走了：老实的撤回提交（落在 main 最新提交上，只把原合并倒回去）照样核对得上、自动合并。"""
        self.modifying_pr()
        self.sign()
        self.auto()
        merged = self.remote_sha()
        sh(self.main, "pull", "-q", "--ff-only")
        (self.main / "other.md").write_text("later\n")
        sh(self.main, "add", ".")
        sh(self.main, "commit", "-qm", "later")
        sh(self.main, "push", "-q", "origin", "main")
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已按策略合并 app#105", r.stdout)
        born = self.remote_sha("refs/pull/105/head")
        self.assertEqual(self.opened()[0]["revert_head"], born)
        self.assertEqual(sh(self.remote, "rev-list", "--parents", "-n", "1", born).split()[1], sh(self.main, "rev-parse", "HEAD"))
        self.assertNotEqual(sh(self.main, "rev-parse", "HEAD"), merged)
        self.assertEqual((sh(self.remote, "show", "refs/heads/main:README.md"), sh(self.remote, "show", "refs/heads/main:other.md")),
                         ("v1", "later"))

    def test_revert_content_check_that_could_not_run_is_retried_next_time(self):
        """开出撤回 PR 时内容核对这一次做不了（取对象时网络断了）：不记成对不上，记下开出时读到的版本；
        下次再跑撤回命令时再核，核上了就接着合那个版本（#260 送审前自审）。版本之后被换掉的照样不合。"""
        import contextlib
        import io
        import merge_act
        self.modifying_pr()
        self.sign()
        self.auto()
        real, calls = merge_act._revert_content_why, []

        def flaky(*a):
            calls.append(a)
            return ("暂时核对不了撤回提交的内容（连不上 GitHub）", True) if len(calls) <= 2 else real(*a)   # 开出时、紧接着再核，都没核成

        buf = io.StringIO()
        with unittest.mock.patch.dict(os.environ, self.env()), unittest.mock.patch.object(merge_act.mo, "GH", str(self.fake / "gh")), \
                unittest.mock.patch.object(merge_act.mo, "RULES", self.rules), unittest.mock.patch.object(merge_act.mo, "ROSTER", self.roster), \
                unittest.mock.patch.object(merge_act, "_revert_content_why", flaky), contextlib.redirect_stdout(buf):
            self.assertEqual(merge_act.cmd_revert("app#5", ""), 0)
        self.assertIn("暂时核对不了", buf.getvalue())
        (opened,) = self.opened()
        born = self.remote_sha("refs/pull/105/head")
        self.assertEqual((opened.get("head_pending"), "revert_head" in opened), (born, False))
        self.assertEqual((self.state(105), self.merges_of(105)), ("OPEN", []))
        r = self.cli(ACT, "revert", "app#5")                         # 再跑一次：这回核得上，接着合开出时的那个版本
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已按策略合并 app#105", r.stdout)
        checked = [b for b in self.book() if b["event"] == "revert_checked"]
        self.assertEqual((self.events()[-3:], [c.get("revert_head") for c in checked]), (["revert_checked", "intent", "merged"], [born]))
        (merge,) = self.merges_of(105)
        self.assertIn("sha=" + born, merge)

    def test_revert_pending_head_replaced_by_wrong_content_is_not_merged(self):
        """开出时内容没核成，之后撤回 PR 被换成内容不对的提交：再核的是开出时读到的版本；现在的版本不是它，不合。"""
        import contextlib
        import io
        import merge_act
        self.modifying_pr()
        self.sign()
        self.auto()
        with unittest.mock.patch.dict(os.environ, self.env()), unittest.mock.patch.object(merge_act.mo, "GH", str(self.fake / "gh")), \
                unittest.mock.patch.object(merge_act.mo, "RULES", self.rules), unittest.mock.patch.object(merge_act.mo, "ROSTER", self.roster), \
                unittest.mock.patch.object(merge_act, "_revert_content_why", lambda *a: ("暂时核对不了撤回提交的内容（x）", True)), \
                contextlib.redirect_stdout(io.StringIO()):
            merge_act.cmd_revert("app#5", "")
        bad = self.forge("v9\n")
        self.update_pr(105, headRefOid=bad)
        r = self.cli(ACT, "revert", "app#5")
        self.assertIn("在开出之后又有新提交", r.stdout)
        self.assertEqual((self.state(105), self.merges_of(105)), ("OPEN", []))

    def test_lost_revert_with_wrong_content_is_not_adopted(self):
        """认领也按内容核对：回应丢了的那个撤回 PR 内容不对，不认它，也不重复开。"""
        self.modifying_pr()
        self.sign()
        self.auto()
        self.forge("v9\n")
        self.gh_fails(revert_lost={"n": 1, "msg": "x"})
        self.cli(ACT, "revert", "app#5")
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("核对不上，不认它是撤回 PR", r.stdout)
        self.assertIn("文件内容对不上", r.stdout)
        self.assertEqual((len(self.reverts_called()), self.opened()), (1, []))

    def test_revert_head_changed_while_waiting_for_ci_requires_new_authorization(self):
        """#260-P1-07：撤回 PR 开出后等 CI 的时候被推了新提交。再跑撤回命令不把新版本当成授权过的：停下说明；
        巡视也不合（开出时的交出登记作废）。新版本要合，得有人重新交出。"""
        self.reverted(**self.RUNNING)
        self.update_pr(105, headRefOid="d" * 40, **self.PASSED)
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("在开出之后又有新提交", r.stdout)
        self.assertIn(self.remote_sha("refs/pull/105/head")[:7], r.stdout)
        self.assertIn("ddddddd", r.stdout)
        self.assertIn("不自动合并", r.stdout)
        self.assertIn("merge_act.py auto app#105", r.stdout)
        self.assertEqual((self.state(105), self.merges_of(105)), ("OPEN", []))
        self.scan()                                                  # 巡视发现新版本：交出登记作废，不合
        self.assertEqual((self.state(105), self.merges_of(105), self.handoffs()), ("OPEN", [], []))
        self.assertEqual(self.cli(ACT, "revert", "app#5").stdout.count("不自动合并"), 1)       # 再跑多少次都一样
        self.assertEqual(self.auto("app#105").returncode, 0)         # 会话看过新版本、重新交出：这才是新的授权
        (merge,) = self.merges_of(105)
        self.assertIn("sha=" + "d" * 40, merge)

    def test_revert_head_changed_after_network_cut_requires_new_authorization(self):
        """#260-P1-07：撤回 PR 开出、版本记下之后断网，没走到合并；网络恢复前它被推了新提交。续接时同样停下。"""
        self.sign()
        self.auto()
        self.gh_fails(view_revert={"skip": 1, "n": 9, "msg": EOF_NET})   # 开出后第一次读到了（记下版本），接着就断网
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已开撤回 PR", r.stdout)
        self.assertIn("撤回 PR 还没合并", r.stdout)
        self.assertNotIn("没撤回", r.stdout)
        self.gh_fails(view_revert={"n": 0, "msg": ""})
        self.update_pr(105, headRefOid="d" * 40)
        again = self.cli(ACT, "revert", "app#5")
        self.assertIn("在开出之后又有新提交", again.stdout)
        self.assertEqual((self.state(105), self.merges_of(105), len(self.reverts_called())), ("OPEN", [], 1))

    def test_revert_head_changed_between_two_reads_requires_new_authorization(self):
        """看撤回 PR 状态时还是开出的版本，真要合的那次读取已经变了：同样不合。"""
        r = self.reverted(**self.RUNNING)
        self.update_pr(105, **self.PASSED)
        import merge_act
        real = merge_act.gh_read
        seen = []

        def racing(args, what):                                      # 第一次读完之后有人推了新提交
            out = real(args, what)
            if args[:3] == ["pr", "view", "105"] and not seen:
                seen.append(1)
                self.update_pr(105, headRefOid="d" * 40)
            return out

        import contextlib
        import io
        buf = io.StringIO()
        with unittest.mock.patch.dict(os.environ, self.env()), unittest.mock.patch.object(merge_act.mo, "GH", str(self.fake / "gh")), \
                unittest.mock.patch.object(merge_act.mo, "RULES", self.rules), unittest.mock.patch.object(merge_act.mo, "ROSTER", self.roster), \
                unittest.mock.patch.object(merge_act, "gh_read", racing), contextlib.redirect_stdout(buf):
            self.assertEqual(merge_act.cmd_revert("app#5", ""), 0)
        self.assertIn("在开出之后又有新提交", buf.getvalue())
        self.assertEqual((self.state(105), self.merges_of(105)), ("OPEN", []))

    def test_revert_head_not_read_at_open_is_never_auto_merged(self):
        """撤回 PR 开出来了，紧接着读它的版本没读到：不知道开出时是哪个版本，以后也不自动合并；说清撤回 PR 已开、怎么合。"""
        self.sign()
        self.auto()
        self.gh_fails(view_revert={"n": 9, "msg": "GraphQL: Could not resolve to a PullRequest"})
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("已开撤回 PR：https://github.com/o/app/pull/105", r.stdout)
        self.assertIn("撤回 PR 没有自动合并：没记下它开出时的版本", r.stdout)
        self.assertNotIn("没撤回", r.stdout)
        self.assertEqual(self.events(), ["intent", "merged", "revert_intent", "revert_opened"])
        self.assertNotIn("revert_head", self.book()[-1])
        self.gh_fails(view_revert={"n": 0, "msg": ""})
        again = self.cli(ACT, "revert", "app#5")
        self.assertIn("撤回 PR 没有自动合并", again.stdout)
        self.assertEqual((self.state(105), self.merges_of(105), len(self.reverts_called())), ("OPEN", [], 1))
        self.assertEqual(self.auto("app#105").returncode, 0)         # 会话看过、交出，才合

    def test_revert_lost_reply_is_found_checked_and_adopted_without_auto_merge(self):
        """开撤回 PR 的请求发出去、回应丢了：再跑一次从 PR 列表里按记录号找到它，核对身份和内容后认领，不重复开；
        认领的没看到开出时的版本，不自动合并（#260-P2-03、P1-07）。"""
        self.sign()
        self.auto()
        self.gh_fails(revert_lost={"n": 1, "msg": "x"})
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("不确定撤回 PR 开出来没有", r.stdout)
        again = self.cli(ACT, "revert", "app#5")
        self.assertIn("上次开的撤回 PR 找到了：https://github.com/o/app/pull/105", again.stdout)
        self.assertIn("撤回 PR 没有自动合并：没记下它开出时的版本（事后认领的", again.stdout)
        self.assertEqual((len(self.reverts_called()), self.state(105), self.merges_of(105)), (1, "OPEN", []))
        opened = [b for b in self.book() if b["event"] == "revert_opened"][0]
        self.assertTrue(opened["adopted"])
        self.assertNotIn("revert_head", opened)
        self.assertEqual([c for c in self.calls() if "--search" in c], [])       # 不靠正文搜索（有索引延迟）

    def test_revert_candidate_that_does_not_check_out_is_not_adopted(self):
        """正文带着记录号、但身份或内容对不上的 PR，不认它是撤回 PR；对得上的不止一个也不认。都停下交人，不重复开。"""
        cases = {"改动和原 PR 反过来对不上": {"files": [{"path": "docs/a.md"}, {"path": "src/extra.py"}]},
                 "增删行数": {"files": [{"path": "docs/a.md", "additions": 9, "deletions": 0}]},
                 "目标分支是 release": {"baseRefName": "release"},
                 "早于这次撤回": {"createdAt": "2026-11-07T01:00:00+08:00"},      # 早就有的 PR，后来把正文改成带记录号的
                 "读不到": {"createdAt": None},
                 "来源分支不在本仓库": {"isCrossRepository": True, "headRepositoryOwner": {"login": "stranger"}}}
        self.sign()
        self.update_pr(files=[{"path": "docs/a.md", "additions": 3, "deletions": 1}])
        self.auto()
        self.gh_fails(revert_lost={"n": 1, "msg": "x"})
        self.cli(ACT, "revert", "app#5")
        good = json.loads((self.fake / "prs.json").read_text())["o/app"]["105"]
        self.assertEqual(good["files"], [{"path": "docs/a.md", "additions": 1, "deletions": 3}])
        def put(**change):                                           # 整个换掉，不留上一种情况改过的字段
            data = json.loads((self.fake / "prs.json").read_text())
            data["o/app"]["105"] = {**good, **change}
            self.set_prs(data)

        for word, change in cases.items():
            put(**change)
            r = self.cli(ACT, "revert", "app#5")
            self.assertEqual(r.returncode, 1, word)
            self.assertIn("核对不上，不认它是撤回 PR", r.stdout)
            if word != "增删行数":
                self.assertIn(word, r.stdout)
        put(state="CLOSED")                                          # 对得上，但已经被人关掉：停下交人，不认、不重开
        for now in ("2026-11-07T10:00:00+08:00", "2026-11-07T12:00:00+08:00"):
            r = self.cli(ACT, "revert", "app#5", env={"DT_NOW": now})
            self.assertEqual(r.returncode, 1)
            self.assertIn("已经被关掉", r.stdout)
        self.assertEqual((len(self.reverts_called()), self.opened()), (1, []))
        put()
        data = json.loads((self.fake / "prs.json").read_text())
        data["o/app"]["106"] = dict(good, number=106)                # 第二个正文带同一个记录号的 PR
        self.set_prs(data)
        r = self.cli(ACT, "revert", "app#5")
        self.assertEqual(r.returncode, 1)
        self.assertIn("认不出哪个是撤回 PR", r.stdout)
        self.assertEqual((len(self.reverts_called()), [b for b in self.book() if b["event"] == "revert_opened"]), (1, []))

    def test_revert_request_still_alive_is_not_reopened_and_not_killed(self):
        """开撤回 PR 的请求发出之后，发起它的进程被杀、请求还挂着：再跑一次不能重新开（会开出两个），也不去停它。
        进程没了以后：第一次看到「进程不在、列表里也没有」只记一笔；那之后满 10 分钟仍没有，才重新开。"""
        self.sign()
        self.auto()
        (self.fake / "revert_hang").write_text("900")
        p = self.start_bg("revert", "app#5", DT_MERGE_REQ_TIMEOUT="900")
        self.wait_for(self.reverts_called, "开撤回 PR 的请求发出")
        p.kill()
        p.wait()
        alive = self.revert_requests()
        self.assertGreaterEqual(len(alive), 2)
        for now in ("2026-11-07T10:01:00+08:00", "2026-11-07T10:20:00+08:00"):   # 过没过期限都一样：说还在跑，给进程号，不停它
            r = self.cli(ACT, "revert", "app#5", env={"DT_NOW": now})
            self.assertEqual(r.returncode, 1)
            self.assertIn("上一次开撤回 PR 的请求还在跑", r.stdout)
            self.assertIn(str(alive[0]), r.stdout)
            self.assertIn("脚本不会去停", r.stdout)
        self.assertEqual(sorted(self.revert_requests()), sorted(alive))
        self.kill(alive)                                             # 常浩在电脑上确认后手动停掉
        self.wait_for(lambda: self.revert_requests() == [], "旧的撤回请求被手动停掉")
        (self.fake / "revert_hang").unlink()
        r = self.cli(ACT, "revert", "app#5", env={"DT_NOW": "2026-11-07T10:20:00+08:00"})
        self.assertEqual(r.returncode, 1)                            # 离第一次要开已经 20 分钟，但刚确认进程不在：先记一笔
        self.assertIn("刚确认本机已经没有那次请求的进程", r.stdout)
        self.assertEqual(self.events()[-1], "revert_quiet")
        r = self.cli(ACT, "revert", "app#5", env={"DT_NOW": "2026-11-07T10:25:00+08:00"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("还找不到", r.stdout)
        self.assertEqual(len(self.reverts_called()), 1)
        r = self.cli(ACT, "revert", "app#5", env={"DT_NOW": "2026-11-07T10:31:00+08:00"})   # 记过之后满 10 分钟：重新开
        self.assertIn("已开撤回 PR：https://github.com/o/app/pull/105", r.stdout)
        self.assertEqual(len(self.reverts_called()), 2)

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
        self.assertEqual(self.events(), ["intent", "merged", "revert_intent", "revert_unsure"])
        self.assertTrue(self.book()[-1]["sent"])
        again = self.cli(ACT, "revert", "app#5")                     # 刚失败、GitHub 上找不到：先不重新开
        self.assertIn("还找不到", again.stdout)
        self.assertEqual(len(self.reverts_called()), 1)
        (self.fake / "revert_fail").unlink()
        soon = self.cli(ACT, "revert", "app#5", env={"DT_NOW": "2026-11-07T10:09:00+08:00"})
        self.assertIn("还找不到", soon.stdout)
        later = self.cli(ACT, "revert", "app#5", env={"DT_NOW": "2026-11-07T10:20:00+08:00"})    # 请求结束后过了 10 分钟仍没有：重新开
        self.assertIn("已开撤回 PR", later.stdout)
        self.assertEqual(len(self.reverts_called()), 2)


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

    def sent_at(self):
        """30 秒前发的消息，写成 lark-cli 那样的本机时区时间（CI 在 UTC：写死北京时间的字符串会被当成未来的消息）。"""
        import datetime as dt
        now = dt.datetime.fromisoformat(os.environ["DT_NOW"]).astimezone()
        return (now - dt.timedelta(seconds=30)).strftime("%Y-%m-%d %H:%M:%S")

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

    def test_off_and_sign_do_not_wait_for_merge_lock(self):
        """合并官巡视、按策略合并占着合并锁的时候，「关自动合并」照样立刻生效（开关用自己的小锁）。"""
        MP.do_sign("om_1", self.rules)
        with MP.locked():
            with unittest.mock.patch.object(MP.time, "sleep", side_effect=AssertionError("不该等合并锁")):
                self.assertTrue(MP.do_off("飞书私聊"))
                self.assertFalse(MP.active(self.rules)[0])
                MP.do_sign("om_2", self.rules)
        self.assertTrue(MP.active(self.rules)[0])

    def test_facts_keep_file_total_and_renames(self):
        f = mo.facts_of("o/app", {"number": 1, "changedFiles": 3, "files": [
            {"path": "b.md", "changeType": "RENAMED"}, {"path": "a.md", "changeType": "MODIFIED"}]})
        self.assertEqual((f["files_total"], f["renamed"], f["paths"], f["from_fork"]), (3, ["b.md"], ["a.md", "b.md"], None))
        why = mo.exception_reasons(f, self.rules)                    # 文件没看全、有改名、来源仓库没给出（#260-P2-09）
        self.assertEqual(len(why), 3, why)
        self.assertTrue(any("没给出 PR 的来源仓库" in w for w in why))
        own = {"number": 1, "files": [], "isCrossRepository": False, "headRepository": {"name": "App"}, "headRepositoryOwner": {"login": "O"}}
        self.assertFalse(mo.facts_of("o/app", own)["from_fork"])
        for change in ({"isCrossRepository": True}, {"headRepository": {"name": "app2"}}, {"headRepositoryOwner": None}):
            self.assertTrue(mo.facts_of("o/app", {**own, **change})["from_fork"], change)
        fresh = mo.facts_of("o/app", {**own, "files": [{"path": "a.md"}]})            # 现读的状态里 GitHub 没给文件总数
        self.assertEqual(mo.exception_reasons(fresh, self.rules), ["GitHub 没给出改动文件总数，核对不了改动文件有没有看全"])
        old = {"repo": "o/app", "pr": 1, "paths": ["a.md"], "labels": []}             # 早先存的快照根本没有这两个键：照原来判
        self.assertEqual(mo.exception_reasons(old, self.rules), [])

    def test_cap_counts_by_settle_day_and_keeps_unsettled(self):
        """#260-P2-12：上限按「合上那天」数，不按发请求那天；结果不明的没结清就一直占一个名额。"""
        def at(now, **rec):
            with unittest.mock.patch.dict(os.environ, {"DT_NOW": now}):
                MP.append(rec)
        for i in "abcd":
            at("2026-11-19T23:59:50+08:00", event="intent", id=i, repo="o/r", pr=i, head="h")
        at("2026-11-20T00:00:10+08:00", event="merged", id="a", merge_commit="c")          # 跨零点合上的
        at("2026-11-20T00:00:20+08:00", event="failed", id="b", why="x")                    # 没合上的不占
        at("2026-11-20T08:00:00+08:00", event="unsure", id="c", why="x")                    # 结果不明
        self.assertEqual([MP.merged_count(d) for d in ("2026-11-19", "2026-11-20", "2026-11-21")], [2, 3, 2])
        at("2026-11-21T09:00:00+08:00", event="merged_unknown", id="c")                     # 核对出来源不明：算核对那天
        at("2026-11-21T09:00:00+08:00", event="not_merged", id="d", why="x")
        self.assertEqual([MP.merged_count(d) for d in ("2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22")], [0, 1, 1, 0])

    def test_incomplete_signature_is_not_authority(self):
        """#260-P2-04：签署记录缺字段或类型不对，哪怕范围哈希对得上也不算授权。"""
        MP.do_sign("om_1", self.rules)
        good = json.loads((self.tmp / "policy.json").read_text(encoding="utf-8"))
        self.assertTrue(MP.active(self.rules)[0])
        for change in ({"type": "run_authorization"}, {"open_id": ""}, {"signed_at": None}, {"rules_version": 2}, {"message_id": ""}):
            bad = {**good, "signature": {**good["signature"], **change}}
            (self.tmp / "policy.json").write_text(json.dumps(bad), encoding="utf-8")
            ok, why, _ = MP.active(self.rules)
            self.assertFalse(ok, change)
            self.assertIn("不完整", why)

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
                    "chat_type": "p2p", "create_time": self.sent_at(), "text": self.text}):
            with self.assertRaisesRegex(MP.Rejected, "发送者不是常浩本人"):
                MP.do_sign("om_abc123", self.rules)
        self.said.start()
        self.assertFalse(MP.active(self.rules)[0])

    def test_real_verification_path_accepts_owner(self):
        self.said.stop()
        with unittest.mock.patch.object(MP, "owner_config", lambda: ("ou_owner", "oc_home")), \
                unittest.mock.patch.object(MP, "fetch_message", lambda mid: {
                    "message_id": mid, "sender_type": "user", "sender_open_id": "ou_owner", "chat_id": "oc_home",
                    "chat_type": "p2p", "create_time": self.sent_at(), "text": self.text + "。"}):
            MP.do_sign("om_abc123", self.rules)
        self.said.start()
        self.assertTrue(MP.active(self.rules)[0])

    def test_status_gives_sign_words_when_off(self):
        text = MP.describe(MP.status(self.rules))
        self.assertIn(f"开自动合并 {self.sha[:12]}", text)
        self.assertIn("每个 PR 仍发卡片", text)
        MP.do_sign("om_1", self.rules)
        self.assertIn("自动合并开着：L0、L1、L2", MP.describe(MP.status(self.rules)))

    def test_revert_attempts_fold_first_time_and_quiet_mark(self):
        """撤回的几次尝试折成一条：first_at 留第一次的时间（找候选、比创建时间用），quiet_at 是最近一次尝试结束的记录，
        又要开一次时清掉重算。"""
        def at(now, **rec):
            with unittest.mock.patch.dict(os.environ, {"DT_NOW": now}):
                MP.append({"id": "a", "repo": "o/r", "pr": "1", **rec})

        at("2026-11-20T15:00:00+08:00", event="intent", head="h")
        at("2026-11-20T15:00:05+08:00", event="merged", merge_commit="c")
        at("2026-11-20T16:00:00+08:00", event="revert_intent", reason="x")
        self.assertEqual(MP.find_merged("o/r", 1)["revert_intent"],
                         {"at": "2026-11-20T16:00:00+08:00", "first_at": "2026-11-20T16:00:00+08:00", "reason": "x"})
        at("2026-11-20T16:01:00+08:00", event="revert_unsure", why="timeout")
        self.assertEqual(MP.find_merged("o/r", 1)["revert_intent"]["quiet_at"], "2026-11-20T16:01:00+08:00")
        at("2026-11-20T16:20:00+08:00", event="revert_intent", reason="x")
        again = MP.find_merged("o/r", 1)["revert_intent"]
        self.assertEqual((again["at"], again["first_at"], "quiet_at" in again),
                         ("2026-11-20T16:20:00+08:00", "2026-11-20T16:00:00+08:00", False))
        at("2026-11-20T16:21:00+08:00", event="revert_quiet")
        self.assertEqual(MP.find_merged("o/r", 1)["revert_intent"]["quiet_at"], "2026-11-20T16:21:00+08:00")

    def test_intent_without_id_does_not_break_the_ledger(self):
        MP.append({"event": "intent", "repo": "o/r", "pr": "1", "head": "h"})          # 缺记录号的坏行
        MP.append({"event": "intent", "id": "b", "repo": "o/r", "pr": "2", "head": "h"})
        self.assertEqual([m["id"] for m in MP.merges()], ["b"])

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
