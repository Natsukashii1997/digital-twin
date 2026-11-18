"""隔离自测：托管合并（custody_merge.py，#136）。

库、原文目录、影子记录、夜跑目录都放临时目录；gh 只读调用换成桩（gh_read），合并执行器换成桩（run_custody_merge）；
merge_act 里的 subprocess.run 换成「一调用就失败」，证明托管合并全程不在宿主跑任何命令（PR 里的测试、安装脚本更不会跑）。
不联网、不碰 ~/Library/Application Support/DigitalTwin、不读钥匙串。
单跑：python3 -B -m unittest discover -s hermes/scripts/tests -p test_custody_merge.py -v
"""
import copy
import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import custody as C  # noqa: E402
import custody_ledger as L  # noqa: E402
import custody_merge as CM  # noqa: E402
import merge_act as MA  # noqa: E402
import merge_officer as mo  # noqa: E402
import multica_night as MN  # noqa: E402

TZ = C.TZ
T0 = dt.datetime(2026, 11, 14, 23, 40, tzinfo=TZ)            # 15:40Z
SID = "c-20261114-233000-0123456789ab"
REPO = "Natsukashii1997/dt-sandbox"
HEAD = "a" * 40
BASE = "b" * 40
NIGHT = "2026-11-14-sbx"
EXECUTOR = "shanhao1997"
LIMITS = {"reply": {"allowed": True, "per_issue": 2, "per_session": 10}, "merge": {"allowed": True, "per_session": 5}}
SCOPE = {"repos": {REPO: {"effects": "CI（跑测试）", "checked": "2026-11-14"}}, "l0_paths": ["docs/**", "CURRENT.md"]}


def pr_json(**kw):
    p = {"number": 8, "title": "README 加「怎么跑测试」", "state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE",
         "headRefOid": HEAD, "baseRefOid": BASE, "labels": [], "files": [{"path": "docs/run.md"}],
         "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
         "body": "Closes #3", "closingIssuesReferences": [{"number": 3}], "mergedAt": None, "closedAt": None,
         "baseRefName": "main", "mergeStateStatus": "CLEAN", "url": f"https://github.com/{REPO}/pull/8",
         "author": {"login": EXECUTOR}, "createdAt": "2026-11-14T15:35:00Z", "headRefName": "agent/claude/d2b456c8758f",
         "isCrossRepository": False, "headRepository": {"name": "dt-sandbox"}, "headRepositoryOwner": {"login": "Natsukashii1997"}}
    p.update(kw)
    return p


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="digital-twin-custody-merge-test-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "twin-state.db"
        self.raw = self.root / "custody"
        self.patch(mock.patch.dict(os.environ, {"DT_STATE_DB": str(self.db), "DT_CUSTODY_DIR": str(self.raw),
                                                "DT_GH_RETRY_WAITS": "", "DT_NOW": T0.isoformat()}))
        shadow = self.root / "shadow"
        for k, v in (("SHADOW", shadow), ("LEDGER", shadow / "agreement.csv"), ("FACTS", shadow / "facts.jsonl")):
            self.patch(mock.patch.object(mo, k, v))
        st = L.TS.Store(self.db, clock=lambda: T0).open()
        self.ledger_id = L.register_session(st, SID, T0)
        st.close()
        self.patch(mock.patch.object(L, "sandbox_projects", return_value={"dt-sandbox": REPO}))
        # 夜跑记录
        self.nights = self.root / "runs"
        self.patch(mock.patch.object(MN, "ROOT", self.nights))
        self.write_night()
        self.patch(mock.patch.object(MN, "trusted_entry", side_effect=lambda plan: (self.entry(), None)))
        # 规则：沙盒登记一个必需检查
        rules = mo.load_rules()
        rules["custody"]["required_checks"] = {REPO: ["test"]}
        self.rules = rules
        self.patch(mock.patch.object(mo, "load_rules", side_effect=lambda path=None: copy.deepcopy(self.rules)))
        # 托管状态
        self.state, self.eligible, self.limits, self.scope = "on", True, copy.deepcopy(LIMITS), copy.deepcopy(SCOPE)
        self.reader = lambda t: self.status()
        # GitHub
        self.pr = pr_json()
        self.after = {"state": "MERGED", "mergeCommit": {"oid": "c" * 40}, "headRefOid": HEAD}
        self.commit_date = "2026-11-14T15:34:00Z"
        self.reads = []
        self.patch(mock.patch.object(MA, "gh_read", side_effect=self.gh_read))
        self.merges = []
        self.result = {"status": "merged", "merge_commit": "c" * 40, "why": ""}
        self.patch(mock.patch.object(MA, "run_custody_merge", side_effect=self.run_merge))
        self.patch(mock.patch.object(MA.subprocess, "run", side_effect=AssertionError("托管合并不许在宿主跑命令")))
        if hasattr(MA, "run_tests"):                      # main 已按「不变量 2」删掉了主账号跑 PR 测试的函数；还在就拦住
            self.patch(mock.patch.object(MA, "run_tests", side_effect=AssertionError("托管合并不许跑 PR 里的测试")))

    def patch(self, p):
        p.start()
        self.addCleanup(p.stop)

    def entry(self):
        return {"sandbox": True, "executor_gate": {"github_login": EXECUTOR, "container": "c", "compose_project": "p",
                                                   "compose_service": "s", "protection_baseline": "b.json",
                                                   "workspaces_root": "/w", "docker": ["docker"]}}

    def write_night(self, state=None, plan=None, log=None, current=NIGHT):
        d = self.nights / NIGHT
        d.mkdir(parents=True, exist_ok=True)
        (self.nights / "night-current").write_text(current)
        (d / "plan.json").write_text(json.dumps(plan or {"repo": REPO, "workspace_id": "w",
                                                         "children": [{"key": "A", "stage": 1}, {"key": "B", "stage": 2}]}))
        (d / "state.json").write_text(json.dumps(state or {
            "issues": {"PARENT": {"identifier": "DTS-1"}, "A": {"identifier": "DTS-2"}, "B": {"identifier": "DTS-3"}},
            "auth": {"approved_by": "常浩", "started_at": "2026-11-14T15:30:00Z", "deadline": "2026-11-14T19:30:00Z"}}))
        rows = log if log is not None else [{"event": "run", "issue": "DTS-2", "pr_url": f"https://github.com/{REPO}/pull/8"}]
        (d / "run-log.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def status(self):
        return {"state": self.state, "session": {"id": SID, "ledger": {"id": self.ledger_id}},
                "policy": {"ok": True, "limits": self.limits, "merge_scope": self.scope},
                "admission": {"eligible": self.eligible, "reasons": [] if self.eligible else ["托管不是开着（draining）"]}}

    def gh_read(self, args, what):
        self.reads.append(args)
        if args[:2] == ["pr", "view"]:
            fields = args[args.index("--json") + 1]
            if fields == "state,mergeCommit,headRefOid":
                if isinstance(self.after, Exception):
                    raise self.after
                return copy.deepcopy(self.after)
            return copy.deepcopy(self.pr)
        if args[0] == "api" and "/commits/" in args[1]:
            return {"commit": {"committer": {"date": self.commit_date}}}
        raise AssertionError(f"没想到的 gh 调用：{args}")

    def run_merge(self, card):
        self.merges.append(card)
        if isinstance(self.result, Exception):
            raise self.result
        return {"identity": "custody", **card, **self.result}

    def go(self, night=NIGHT, pr=8, head=HEAD, now=T0):
        return CM.custody_merge(SID, night, REPO, pr, head, now=now, status_reader=self.reader)

    def decisions(self):
        with L.open_store() as st:
            return L.decisions(st, SID)

    def quotas(self, did):
        with L.open_store() as st:
            return sorted(q["state"] for q in L.quotas(st, did))

    def rows(self):
        return mo.read_rows()


class HappyPath(Base):
    def test_l0_merge_records_everything(self):
        out = self.go()
        self.assertEqual(out["status"], "merged", out)
        self.assertEqual(self.merges, [{"repo": REPO, "pr": 8, "head": HEAD}])
        [d] = self.decisions()
        self.assertEqual((d["action"], d["status"], d["outcome"]["merge_commit"]), ("merge", "done", "c" * 40))
        self.assertEqual(self.quotas(d["id"]), ["used"])
        self.assertEqual(L.check_owner_text("merge", d), [])
        raw = L.read_raw(d)["received"]
        self.assertEqual((raw["tier"], raw["night"], raw["night_issues"]), ("L0", NIGHT, ["DTS-2"]))
        [row] = self.rows()
        self.assertEqual((row["repo"], row["pr"], row["head_sha"], row["owner"]), (REPO, "8", HEAD, "custody"))
        self.assertTrue(out["agreement_recorded"])

    def test_existing_agreement_row_gets_owner_custody(self):
        row = mo.new_row(mo.facts_of(REPO, self.pr), self.rules, "live", "2026-11-14T23:00:00+08:00")
        mo.write_rows([row])
        self.go()
        [row] = self.rows()
        self.assertEqual(row["owner"], "custody")

    def test_no_host_command_and_only_readonly_gh(self):
        self.go()
        for args in self.reads:
            self.assertTrue(args[:2] == ["pr", "view"] or args[0] == "api", args)
            self.assertNotIn("merge", args)


class EachConditionHolds(Base):
    """每个条件单独不满足：挂起（记一笔白话），不调合并执行器、不占合并额度。"""

    def assert_held(self, code, out=None):
        out = out or self.go()
        self.assertEqual(out["status"], "suspended", out)
        self.assertIn(code, [h["code"] for h in out["holds"]], out)
        self.assertEqual(self.merges, [])
        ds = self.decisions()
        self.assertEqual([d["action"] for d in ds], ["suspend"])
        self.assertEqual(self.quotas(ds[0]["id"]), [])
        self.assertEqual(L.check_owner_text("suspend", ds[0]), [])
        self.assertEqual(self.rows(), [])
        return out

    def test_admission(self):
        self.eligible, self.state = False, "draining"
        self.assert_held("NotAdmitted")

    def test_policy_does_not_allow_merge(self):
        self.limits["merge"]["allowed"] = False
        self.assert_held("PolicyNoMerge")

    def test_repo_not_in_policy_list(self):
        self.scope["repos"] = {}
        self.assert_held("RepoNotListed")

    def test_repo_without_executor_credential(self):
        with mock.patch.dict(MA.CUSTODY_CRED, clear=True):
            self.assert_held("NoExecutor")

    def test_night_not_current(self):
        self.assert_held("NightUnproven", self.go(night="2026-11-13-old"))

    def test_night_variants(self):
        good_state = json.loads((self.nights / NIGHT / "state.json").read_text())
        cases = {
            "没有开跑授权": dict(state={**good_state, "auth": {}}),
            "冻结过": dict(state={**good_state, "frozen": True}),
            "计划的仓库不同": dict(plan={"repo": "Natsukashii1997/other", "children": [{"key": "A", "stage": 1}]}),
            "run-log 没记这个 PR": dict(log=[{"event": "run", "issue": "DTS-2", "pr_url": ""}]),
            "只有父单记了": dict(log=[{"event": "run", "issue": "DTS-1", "pr_url": f"https://github.com/{REPO}/pull/8"}]),
            "记的是别的 PR": dict(log=[{"event": "run", "issue": "DTS-2", "pr_url": f"https://github.com/{REPO}/pull/80"}]),
            "run-log 是空的": dict(log=[]),
        }
        for why, kw in cases.items():
            with self.subTest(why):
                self.setUp()
                self.write_night(**kw)
                self.assert_held("NightUnproven")

    def test_pr_author_and_time(self):
        for why, pr in {"作者不是执行器": pr_json(author={"login": "Natsukashii1997"}),
                        "开在夜跑之前": pr_json(createdAt="2026-11-14T15:00:00Z")}.items():
            with self.subTest(why):
                self.setUp()
                self.pr = pr
                self.assert_held("NightUnproven")

    def test_scope(self):
        cases = {"NotOpen": dict(state="CLOSED"), "Draft": dict(isDraft=True), "WrongBase": dict(baseRefName="dev"),
                 "HeadMoved": dict(headRefOid="d" * 40), "NotAgentBranch": dict(headRefName="feature/x"),
                 "NotClean": dict(mergeStateStatus="BEHIND")}
        for code, kw in cases.items():
            with self.subTest(code):
                self.setUp()
                self.pr = pr_json(**kw)
                self.assert_held(code)
        with self.subTest("fork"):
            self.setUp()
            self.pr = pr_json(isCrossRepository=True)
            self.assert_held("NotAgentBranch")

    def test_pr_unreadable(self):
        self.patch(mock.patch.object(MA, "gh_read", side_effect=MA.Stop("网络断了", "等等")))
        self.assert_held("PrUnreadable")

    def test_officer_not_merge(self):
        for why, kw in {"正文没写 Closes": dict(body="", closingIssuesReferences=[]),
                        "敏感路径（L3）": dict(files=[{"path": "docs/secret.md"}])}.items():
            with self.subTest(why):
                self.setUp()
                self.pr = pr_json(**kw)
                self.assert_held("OfficerNo")

    def test_side_effect_config(self):
        self.pr = pr_json(files=[{"path": "docs/run.md"}, {"path": "Makefile"}])
        self.assert_held("SideEffect")

    def test_ci(self):
        cases = {
            "NoCI": dict(statusCheckRollup=[]),
            "CIFail": dict(statusCheckRollup=[{"name": "test", "status": "COMPLETED", "conclusion": "FAILURE"}]),
            "CIPending": dict(statusCheckRollup=[{"name": "test", "status": "IN_PROGRESS", "conclusion": ""}]),
        }
        for code, kw in cases.items():
            with self.subTest(code):
                self.setUp()
                self.pr = pr_json(**kw)
                self.assert_held(code)
        with self.subTest("必需检查被跳过不算"):
            self.setUp()
            self.pr = pr_json(statusCheckRollup=[{"name": "test", "status": "COMPLETED", "conclusion": "SKIPPED"}])
            self.assert_held("NoCI")
        with self.subTest("只有别的检查过了"):
            self.setUp()
            self.pr = pr_json(statusCheckRollup=[{"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"}])
            self.assert_held("NoCI")
        with self.subTest("仓库没登记必需检查（dt-sandbox 现状）"):
            self.setUp()
            self.rules["custody"]["required_checks"] = {}
            self.assert_held("NoCI")

    def test_l0_outside_policy_whitelist(self):
        self.pr = pr_json(files=[{"path": "README.md"}])
        self.assert_held("L0Outside")

    def test_l1_without_credential(self):
        self.pr = pr_json(files=[{"path": "src/app.py"}])
        self.assert_held("NoReview")

    def test_quota_exhausted(self):
        self.limits["merge"]["per_session"] = 1
        self.go()                                         # 用掉唯一一个
        self.merges.clear()
        self.pr = pr_json(number=9, url=f"https://github.com/{REPO}/pull/9", headRefOid="e" * 40)
        self.write_night(log=[{"event": "run", "issue": "DTS-3", "pr_url": f"https://github.com/{REPO}/pull/9"}])
        out = CM.custody_merge(SID, NIGHT, REPO, 9, "e" * 40, now=T0, status_reader=self.reader)
        self.assertEqual(out["status"], "suspended")
        self.assertEqual([h["code"] for h in out["holds"]], ["QuotaExhausted"])
        self.assertEqual(self.merges, [])

    def test_admission_lost_before_begin_voids(self):
        calls = []

        def reader(t):
            calls.append(t)
            s = self.status()
            if len(calls) > 2:                            # gate、issue 时开着，begin 时已经转排空
                s["admission"] = {"eligible": False, "reasons": ["托管不是开着（draining）"]}
            return s
        out = CM.custody_merge(SID, NIGHT, REPO, 8, HEAD, now=T0, status_reader=reader)
        self.assertEqual(out["status"], "suspended", out)
        self.assertEqual(self.merges, [])
        merge = [d for d in self.decisions() if d["action"] == "merge"][0]
        self.assertEqual((merge["status"], self.quotas(merge["id"])), ("void", ["released"]))

    def test_suspend_recorded_once_per_pr_head(self):
        self.pr = pr_json(isDraft=True)
        self.go()
        self.go()
        self.assertEqual(len(self.decisions()), 1)

    def test_not_current_session_is_refused_not_recorded(self):
        self.eligible = False
        out = CM.custody_merge("c-20261115-233000-ba9876543210", NIGHT, REPO, 8, HEAD, now=T0, status_reader=self.reader)
        self.assertEqual(out["status"], "refused")
        self.assertEqual(self.decisions(), [])

    def test_plain_texts_have_no_jargon(self):
        for code, text in CM.PLAIN.items():
            with self.subTest(code):
                self.assertIsNone(L.JARGON.search(text), text)
        self.assertIsNone(L.JARGON.search(CM.plain_title(pr_json(title="修 HEAD 和 L1 的问题，D 档"), 8)))


class Results(Base):
    def test_failed_releases_quota(self):
        self.result = {"status": "failed", "merge_commit": None, "why": "422 Head branch was modified"}
        out = self.go()
        self.assertEqual(out["status"], "failed")
        [d] = self.decisions()
        self.assertEqual((d["status"], self.quotas(d["id"])), ("failed", ["released"]))
        self.assertEqual(self.rows(), [])

    def test_no_executor_token_is_failed_without_sending(self):
        self.result = MA.Stop("钥匙串里没有合并执行器令牌", "常浩重新生成")
        out = self.go()
        self.assertEqual(out["status"], "failed")
        [d] = self.decisions()
        self.assertEqual(d["status"], "failed")

    def test_unsure_freezes_and_never_resends(self):
        self.result = {"status": "unsure", "merge_commit": None, "why": "超时"}
        self.after = {"state": "OPEN", "mergeCommit": None, "headRefOid": HEAD}      # 刚发出去，还开着：等
        out = self.go()
        self.assertEqual((out["status"], out["ledger_status"]), ("unsure", "unknown"))
        [d] = self.decisions()
        self.assertEqual(self.quotas(d["id"]), ["frozen"])
        out2 = self.go()                                  # 夜跑主控再调一次：不重发
        self.assertEqual(out2["status"], "suspended")
        self.assertIn("Unsettled", [h["code"] for h in out2["holds"]])
        self.assertEqual(len(self.merges), 1)
        with L.open_store() as st:                        # 过了 10 分钟再对账：还开着、版本没变 → 确定没合，释放
            d = CM.reconcile_merge(st, L.get(st, d["id"]), now=T0 + dt.timedelta(minutes=11))
        self.assertEqual((d["status"], self.quotas(d["id"])), ("failed", ["released"]))
        self.assertEqual(len(self.merges), 1)

    def test_unsure_readback_merged_counts_as_used(self):
        self.result = {"status": "unsure", "merge_commit": None, "why": "超时"}
        out = self.go()
        self.assertEqual((out["status"], out["ledger_status"]), ("unsure", "done"))
        [d] = self.decisions()
        self.assertEqual((self.quotas(d["id"]), d["outcome"]["merge_commit"]), (["used"], "c" * 40))
        self.assertEqual(self.rows()[0]["owner"], "custody")

    def test_unsure_readback_fails_is_unreconciled(self):
        self.result = {"status": "unsure", "merge_commit": None, "why": "超时"}
        self.after = MA.Stop("回读失败", "等等")
        out = self.go()
        self.assertEqual(out["ledger_status"], "unreconciled")
        [d] = self.decisions()
        self.assertEqual(self.quotas(d["id"]), ["frozen"])
        self.assertIn("Unsettled", [h["code"] for h in self.go()["holds"]])
        self.assertEqual(len(self.merges), 1)

    def test_unsure_variants(self):
        cases = {"已关闭": ({"state": "CLOSED", "mergeCommit": None, "headRefOid": HEAD}, "failed"),
                 "换了版本": ({"state": "OPEN", "mergeCommit": None, "headRefOid": "d" * 40}, "failed"),
                 "合上的不是这个版本": ({"state": "MERGED", "mergeCommit": {"oid": "c" * 40}, "headRefOid": "d" * 40}, "unreconciled")}
        for why, (after, want) in cases.items():
            with self.subTest(why):
                self.setUp()
                self.result = {"status": "unsure", "merge_commit": None, "why": "超时"}
                self.after = after
                self.assertEqual(self.go()["ledger_status"], want)

    def test_executor_exception_is_unknown_not_resent(self):
        self.result = RuntimeError("进程中途出错")
        self.after = {"state": "OPEN", "mergeCommit": None, "headRefOid": HEAD}
        out = self.go()
        self.assertEqual((out["status"], out["ledger_status"]), ("unsure", "unknown"))
        self.go()
        self.assertEqual(len(self.merges), 1)

    def test_merged_twice_is_duplicate(self):
        self.go()
        out = self.go()
        self.assertEqual(out["status"], "suspended")
        self.assertIn("Duplicate", [h["code"] for h in out["holds"]])
        self.assertEqual(len(self.merges), 1)

    def test_reconcile_cli(self):
        self.result = {"status": "unsure", "merge_commit": None, "why": "超时"}
        self.after = {"state": "OPEN", "mergeCommit": None, "headRefOid": HEAD}
        self.go()
        [d] = self.decisions()
        self.after = {"state": "MERGED", "mergeCommit": {"oid": "c" * 40}, "headRefOid": HEAD}
        with mock.patch("builtins.print") as pr:
            self.assertEqual(CM.main(["reconcile", d["id"]]), 0)
        self.assertEqual(json.loads(pr.call_args[0][0])["status"], "done")


class ReviewCredential(Base):
    """L1–L2 的等效审核凭证。"""

    def setUp(self):
        super().setUp()
        self.pr = pr_json(files=[{"path": "src/app.py"}])
        self.make_credential()

    def make_credential(self, verdict="PASS", when="2026-11-14T23:38:00+08:00", packet=None, **over):
        d = self.root / "review-src"
        d.mkdir(exist_ok=True)
        packet = packet if packet is not None else f"repo {REPO} / PR #8 / head_sha {HEAD} / base_sha {BASE}\n\n送审内容"
        (d / "packet.md").write_text(packet, encoding="utf-8")
        psha = hashlib.sha256(packet.encode()).hexdigest()
        body = f"审查意见：可以。\n\nVERDICT: {verdict}\n"
        meta = {"review_id": "r-1", "packet_sha256": psha, "conversation": "x", "verdict": verdict,
                "reply_sha256": hashlib.sha256(body.encode()).hexdigest()}
        receipt = (f"<!-- relay-record {json.dumps(meta, ensure_ascii=False)} -->\n# ChatGPT 审核回执原文\n\n- 时间：{when}\n"
                   f"\n---\n\n{body}")
        (d / "receipt.md").write_text(receipt, encoding="utf-8")
        cred = {"repo": REPO, "pr": 8, "head_sha": HEAD, "base_sha": BASE, "packet_path": str(d / "packet.md"),
                "packet_sha256": psha, "receipt_source": "orca-chatgpt-relay", "receipt_path": str(d / "receipt.md")}
        cred.update(over)
        path = CM.credential_path(REPO, 8, HEAD)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cred), encoding="utf-8")
        return d

    def test_valid_credential_merges(self):
        out = self.go()
        self.assertEqual(out["status"], "merged", out)
        [d] = self.decisions()
        self.assertEqual(L.read_raw(d)["received"]["review"]["kind"], "credential")

    def test_bad_credentials_hold(self):
        cases = {
            "绑定的 HEAD 不对": dict(head_sha="d" * 40),
            "base 不对": dict(base_sha="d" * 40),
            "PR 不对": dict(pr=9),
            "来源不认": dict(receipt_source="pr-comment"),
            "包指纹不对": dict(packet_sha256="0" * 64),
            "回执要返工": dict(verdict="REWORK"),
            "回执早于提交": dict(when="2026-11-14T23:30:00+08:00"),
            "包里没写 HEAD": dict(packet=f"repo {REPO} / PR #8 / base_sha {BASE}"),
        }
        for why, kw in cases.items():
            with self.subTest(why):
                self.setUp()
                self.make_credential(**kw)
                out = self.go()
                self.assertEqual(out["status"], "suspended", (why, out))
                self.assertEqual([h["code"] for h in out["holds"]], ["NoReview"], why)
                self.assertEqual(self.merges, [])

    def test_tampered_receipt(self):
        d = self.root / "review-src"
        text = (d / "receipt.md").read_text(encoding="utf-8")
        (d / "receipt.md").write_text(text.replace("可以", "不行"), encoding="utf-8")
        self.assertEqual([h["code"] for h in self.go()["holds"]], ["NoReview"])

    def test_missing_credential(self):
        CM.credential_path(REPO, 8, HEAD).unlink()
        self.assertEqual([h["code"] for h in self.go()["holds"]], ["NoReview"])


class AgreementRate(unittest.TestCase):
    """owner=custody 的行不进影子一致率，也不进回放。"""

    def rows(self):
        base = {"source": "live", "tier": "L0", "suggestion": mo.MERGE, "agree": "是", "owner": ""}
        return [dict(base), dict(base), {**base, "owner": "custody"}, {**base, "owner": "custody", "agree": "否"}]

    def test_rates_exclude_custody(self):
        rs = mo.rates(self.rows(), {"min_samples": 1, "min_rate": 0.9})
        self.assertEqual((rs["L0"]["live"]["n"], rs["L0"]["live"]["agree"]), (2, 2))

    def test_replay_skips_custody(self):
        rules = mo.load_rules()
        f = {"repo": REPO, "pr": 8, "title": "t", "head_sha": HEAD, "base_sha": BASE, "draft": False, "mergeable": "MERGEABLE",
             "labels": [], "paths": ["docs/a.md"], "checks": [], "closes": True}
        rows = [{"repo": REPO, "pr": "8", "head_sha": HEAD, "source": "live", "outcome": mo.MERGED, "outcome_head": HEAD,
                 "owner": "custody"}]
        before, after, changed = mo.replay(rows, {(REPO, "8", HEAD): f}, rules, rules)
        self.assertEqual(before["L0"]["live"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
