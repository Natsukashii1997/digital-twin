#!/usr/bin/env python3
"""托管合并（#136，托管模式规格 §4；ARCHITECTURE 5.5「托管合并」）：常浩不在场、托管开着时，猛鬼兽替常浩合并已批准夜跑开出的 PR。

下面条件同时满足才合；任何一条不满足就不合，在账本里记一笔挂起（写给常浩看的白话），进托管汇报：
  0. 夜跑归属：夜跑号是当前夜跑、有开跑授权、没冻结、仓库是夜跑计划里的；PR 作者是名册 executor_gate 的执行器账号，
     开在夜跑开跑之后，并且夜跑 run-log 里这家族某张子单的 run 记下了这个 PR 的地址；
  1. 合并官：用当前 merge_rules 对现读的 PR 判定，「建议合并」且风险档 L0–L2；
  2. 测试：merge_rules.json 的 custody.required_checks 里给这个仓库登记的检查，在待合并的 HEAD 上都真的跑完且 SUCCESS
     （跳过 / 中立不算），其余检查也没有失败或还在跑的；没登记、没有 CI 就挂起。**不在宿主跑 PR 里的任何脚本**（测试、安装、构建都不跑）；
     「按规则触发的 Codex 验证」：merge_rules 现在没有机器可读的触发条件，按没触发处理（以后加了触发条件要在这里接上）；
  3. 审核凭证：L0 要改动路径全部落在已签策略第 3 节的 L0 路径白名单里；L1–L2 要一份等效审核凭证（见 review_credential），
     驿窗接力记录现在不存送审包指纹（ARCHITECTURE 5.5 末段），不作为凭证来源；
  4. 副作用：仓库在已签策略第 3 节的可托管合并仓库表里；PR 没改 custody.side_effect_paths 里的工作流和副作用配置；
  5. 范围与现读远端：PR 开着、不是草稿、合进 main、HEAD 还是要合的这个、分支是 agent/ 开头且就在本仓库（不是 fork）、
     GitHub 合并状态 CLEAN；合并请求用 sha 绑定 HEAD（run_custody_merge）；
  6. 授权与额度：托管准入、已签策略允许托管合并，额度在 custody_ledger.issue 里原子预占，动手前 begin 再现读准入；
  7. 只由合并执行器执行：只调 merge_act.run_custody_merge（凭据在函数里从钥匙串取，取不到就停）。
同一个 PR × HEAD 在这次托管里已经合过、正在合或结果没核清，不再合（结果不明不重发，ARCHITECTURE 7.6）。

结果：merged → 账本 done（带合并提交号），agreement.csv 这一行记 owner=custody（不进影子一致率）；
failed → 账本 failed，释放额度；unsure → 账本 unknown（额度冻结），马上只读对账一次：
  回读已合并且 HEAD 对得上 → done；PR 已关闭 → failed；PR 还开着但 HEAD 变了 → failed（绑定 sha 的请求合不了别的版本）；
  PR 还开着、HEAD 没变：发出合并请求超过 10 分钟才判 failed，没到就留着等下次对账；回读失败或核不清 → UNRECONCILED，额度继续冻结。

用法（最后一行都是一行 JSON：{"ok", "status", ...}）：
  custody_merge.py merge --session c-… --night 夜跑号 owner/repo#PR@40位HEAD   托管合并一个 PR（#134 夜跑主控调用）
  custody_merge.py reconcile d-…                                              结果不明的托管合并再只读对账一次
等效审核凭证：DT_CUSTODY_DIR/review/<owner>__<repo>/<PR>-<HEAD>.json，见 review_credential。
威胁模型同 custody.py：防 agent 自报、文本冒充、重放；不防同一 macOS 用户下的恶意本地进程。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import custody as C  # noqa: E402
import custody_ledger as L  # noqa: E402
import merge_act as MA  # noqa: E402
import merge_officer as mo  # noqa: E402

PR_FIELDS = MA.VIEW_FIELDS + ",author,createdAt,headRefName,isCrossRepository,headRepository,headRepositoryOwner"
REF = re.compile(r"^([A-Za-z0-9][\w.-]*/[\w.-]+)#([1-9]\d*)@([0-9a-f]{40})$")
SHA = re.compile(r"^[0-9a-f]{40}$")
OK_TIERS = ("L0", "L1", "L2")
DECISION_TTL = dt.timedelta(minutes=15)       # 预占到动手的有效期
RECONCILE_GRACE = dt.timedelta(minutes=10)    # 合并请求发出多久后，PR 还开着才算确定没合
REVIEW_SOURCES = ("orca-chatgpt-relay",)      # 等效审核凭证认的回执来源（orca/chatgpt_relay.py 写的回执）
RELAY_META = re.compile(r"\A<!-- relay-record (\{.*\}) -->\n")
RELAY_SEP = "\n---\n\n"
RELAY_VERDICT = re.compile(r"^\s*`?VERDICT\s*[:：]\s*(PASS|REWORK|NEEDS_HUMAN)`?\s*$")
RELAY_TIME = re.compile(r"^- 时间：(\S+)\s*$", re.M)
SETTLED_OR_BUSY = ("issued", "executing", "done", "unknown", "unreconciled")

# 挂起原因：代码 → 给常浩看的白话（汇报卡照抄，不许内部术语）
PLAIN = {
    "NotAdmitted": "托管现在不是开着的",
    "PolicyNoMerge": "你签的规则没允许替你合并",
    "RepoNotListed": "这个仓库不在你签的可合并名单里",
    "NoExecutor": "这个仓库没有替你合并用的专用账号",
    "NightUnproven": "证明不了这个 PR 是今晚批准的夜跑做的",
    "PrUnreadable": "读不到这个 PR 现在的状态",
    "NotOpen": "这个 PR 已经不是打开的",
    "Draft": "这个 PR 还是草稿",
    "WrongBase": "这个 PR 不是合进主分支",
    "HeadMoved": "这个 PR 在检查时又有了新提交",
    "NotAgentBranch": "这个 PR 不是 agent 的分支开的",
    "NotClean": "这个 PR 和主分支有冲突或落后",
    "OfficerNo": "合并官没建议合并",
    "TooRisky": "改动风险高，要你亲自看",
    "SideEffect": "改动碰了会触发自动流程的配置",
    "NoCI": "这个仓库没有可信的自动测试",
    "CIFail": "自动测试没过",
    "CIPending": "自动测试还没跑完",
    "L0Outside": "改动不全在你允许的文档范围里",
    "NoReview": "没有能核实的审查通过记录",
    "Unsettled": "上次合并它的结果还没核清，不重复合并",
    "Duplicate": "这个版本已经替你合过或正在合",
    "QuotaExhausted": "这次托管能合并的个数用完了",
    "Expired": "检查完到动手前超时了",
    "LedgerError": "记账没成功，不敢合",
}


LEDGER_CODE = {"NotAdmitted": "NotAdmitted", "WrongSession": "NotAdmitted", "NotAllowed": "PolicyNoMerge",
               "NoLimits": "PolicyNoMerge", "QuotaExhausted": "QuotaExhausted", "Expired": "Expired"}


class Hold(Exception):
    """一条合并条件不满足：code 给机器和测试判，plain 给常浩看（白话），detail 是原文（只进原文目录，不上汇报卡）。"""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}：{detail}")
        self.code, self.detail = code, str(detail)[:500]
        self.plain = PLAIN.get(code, PLAIN["LedgerError"])


def custody_rules(rules):
    c = rules.get("custody") or {}
    return {"required_checks": c.get("required_checks") or {}, "side_effect_paths": c.get("side_effect_paths") or []}


# ---------- 各项条件（只读）----------

def check_policy(st, repo):
    """6 / 4：托管准入、策略允许托管合并、仓库在第 3 节表里。返回当前有效策略。"""
    if not st["admission"]["eligible"]:
        raise Hold("NotAdmitted", "；".join(st["admission"]["reasons"]))
    pol = st["policy"]
    if not ((pol.get("limits") or {}).get("merge") or {}).get("allowed"):
        raise Hold("PolicyNoMerge")
    if repo not in ((pol.get("merge_scope") or {}).get("repos") or {}):
        raise Hold("RepoNotListed", repo)
    if repo not in MA.CUSTODY_CRED:
        raise Hold("NoExecutor", repo)
    return pol


def check_night(night_id, repo, pr, p):
    """0：PR 属于当前已批准的夜跑。证明不了一律 Hold。"""
    import multica_night as MN
    try:
        if not night_id or MN.current_id() != night_id:
            raise Hold("NightUnproven", f"{night_id} 不是当前夜跑（当前：{MN.current_id()}）")
        d = MN.ROOT / night_id
        plan, state = MN.load(d / "plan.json"), MN.load(d / "state.json", {})
        if plan.get("repo") != repo:
            raise Hold("NightUnproven", f"夜跑计划的仓库是 {plan.get('repo')}")
        auth = state.get("auth") or {}
        if not auth.get("started_at"):
            raise Hold("NightUnproven", "夜跑没有开跑授权记录")
        if state.get("frozen") or state.get("released"):
            raise Hold("NightUnproven", "夜跑冻结过")
        created, started = MN.parse(p.get("createdAt")), MN.parse(auth["started_at"])
        if not created or created < started:
            raise Hold("NightUnproven", f"PR 开于 {p.get('createdAt')}，早于夜跑开跑 {auth['started_at']}")
        entry, why = MN.trusted_entry(plan)
        if why:
            raise Hold("NightUnproven", why)
        login = MN.trusted_gate(entry)["github_login"]
        if ((p.get("author") or {}).get("login") or "").lower() != login.lower():
            raise Hold("NightUnproven", f"PR 作者 {(p.get('author') or {}).get('login')} 不是执行器账号 {login}")
        issues = state.get("issues") or {}
        children = {issues[c["key"]]["identifier"] for c in plan.get("children") or [] if c.get("key") in issues}
        want = f"https://github.com/{repo}/pull/{pr}".lower()
        log = d / "run-log.jsonl"
        hits = []
        for line in (log.read_text(encoding="utf-8").splitlines() if log.exists() else []):
            with contextlib.suppress(ValueError):
                rec = json.loads(line)
                if (isinstance(rec, dict) and rec.get("event") == "run" and rec.get("issue") in children
                        and str(rec.get("pr_url") or "").strip().rstrip("/").lower() == want):
                    hits.append(rec["issue"])
        if not hits:
            raise Hold("NightUnproven", f"夜跑 run-log 里没有哪张子单的 run 记下 {want}")
        return sorted(set(hits))
    except Hold:
        raise
    except (MN.Unclear, OSError, ValueError, KeyError, TypeError) as e:
        raise Hold("NightUnproven", f"夜跑记录读不清（{type(e).__name__}：{e}）")


def check_scope(p, head):
    """5：现读远端的 PR 状态。"""
    if p.get("state") != "OPEN":
        raise Hold("NotOpen", p.get("state"))
    if p.get("isDraft"):
        raise Hold("Draft")
    if p.get("baseRefName") != "main":
        raise Hold("WrongBase", p.get("baseRefName"))
    if p.get("headRefOid") != head:
        raise Hold("HeadMoved", p.get("headRefOid"))
    if not str(p.get("headRefName") or "").startswith("agent/"):
        raise Hold("NotAgentBranch", p.get("headRefName"))
    if p.get("isCrossRepository") is not False:
        raise Hold("NotAgentBranch", "来自 fork 或看不出来源")
    if p.get("mergeable") == "CONFLICTING" or p.get("mergeStateStatus") != "CLEAN":
        raise Hold("NotClean", f"{p.get('mergeable')} / {p.get('mergeStateStatus')}")


def check_officer(repo, p, rules):
    """1：合并官对这个 PR × HEAD 现判。返回 (风险档, 事实快照)。"""
    facts = mo.facts_of(repo, p)
    tier, sug, why = mo.evaluate(facts, rules)
    if sug != mo.MERGE:
        raise Hold("OfficerNo", f"{sug}：{'；'.join(why)}")
    if tier not in OK_TIERS:
        raise Hold("TooRisky", tier)
    return tier, facts


def check_side_effects(paths, crules):
    hit = [x for x in paths if any(re.search(rx, x, re.I) for rx in crules["side_effect_paths"])]
    if hit:
        raise Hold("SideEffect", "、".join(hit[:5]))


def check_ci(p, repo, crules):
    """2：登记的必需检查在这个 HEAD 上真的 SUCCESS，其余检查没有失败或在跑的。statusCheckRollup 和 headRefOid 来自同一次读取，
    检查结果就是这个 HEAD 的；合并请求再用 sha 绑定。"""
    names = crules["required_checks"].get(repo)
    if not names:
        raise Hold("NoCI", f"merge_rules.json 的 custody.required_checks 没给 {repo} 登记必须通过的检查")
    got, failed, waiting = {}, [], []
    for c in p.get("statusCheckRollup") or []:
        name = c.get("name") or c.get("context") or "?"
        status = str(c.get("status") or "COMPLETED").upper()
        result = str(c.get("conclusion") or c.get("state") or "").upper()
        got.setdefault(name, []).append((status, result))
        if status != "COMPLETED" or result in MA.CHECK_WAIT or not result:
            waiting.append(name)
        elif result not in MA.CHECK_OK:
            failed.append(f"{name}（{result}）")
    if failed:
        raise Hold("CIFail", "、".join(failed))
    if waiting:
        raise Hold("CIPending", "、".join(waiting))
    bad = [n for n in names if not got.get(n) or any(r != "SUCCESS" for _, r in got[n])]
    if bad:
        raise Hold("NoCI", "必须通过的检查没有成功跑完：" + "、".join(bad))


def check_review(tier, repo, pr, p, pol):
    """3：L0 看策略白名单；L1–L2 看等效审核凭证。"""
    paths = sorted(f.get("path", "") for f in p.get("files") or [])
    if tier == "L0":
        allow = (pol.get("merge_scope") or {}).get("l0_paths") or []
        out = [x for x in paths if not any(C.glob_match(g, x) for g in allow)]
        if out or not paths:
            raise Hold("L0Outside", "、".join(out[:5]) or "没有改动文件")
        return {"kind": "l0_whitelist", "patterns": allow}
    return review_credential(repo, pr, p["headRefOid"], p.get("baseRefOid"))


def credential_path(repo, pr, head):
    return L.raw_dir() / "review" / repo.replace("/", "__") / f"{pr}-{head}.json"


def _sha256(b):
    return hashlib.sha256(b).hexdigest()


def read_relay_receipt(text):
    """orca/chatgpt_relay.py 写的回执（同它的 read_record）：首行元数据、正文摘要对得上、正文最后一个非空行的结论和元数据一致。
    返回 (元数据, 回执时间)。安装副本里没有 orca/，所以在这里按同一格式核对，不导入它。"""
    m = RELAY_META.match(text)
    if not m or RELAY_SEP not in text:
        raise ValueError("不是 chatgpt_relay 写的回执")
    meta = json.loads(m.group(1))
    head, body = text.split(RELAY_SEP, 1)
    lines = [x for x in body.splitlines() if x.strip()]
    vm = RELAY_VERDICT.match(lines[-1]) if lines else None
    if _sha256(body.encode()) != meta.get("reply_sha256") or (vm.group(1) if vm else "NEEDS_HUMAN") != meta.get("verdict"):
        raise ValueError("回执不完整或被改过")
    tm = RELAY_TIME.search(head)
    when = dt.datetime.fromisoformat(tm.group(1)) if tm else None
    if not when or when.tzinfo is None:
        raise ValueError("回执里没有带时区的时间")
    return meta, when


def head_commit_time(repo, head):
    c = MA.gh_read(["api", f"repos/{repo}/commits/{head}"], "取提交时间")
    return C.parse_iso(((c.get("commit") or {}).get("committer") or {}).get("date", "").replace("Z", "+00:00"))


def review_credential(repo, pr, head, base):
    """L1–L2 的等效审核凭证（规格 §4 第 3 条）：DT_CUSTODY_DIR/review/<owner>__<repo>/<PR>-<HEAD>.json，由送审工具写，字段：
      {"repo", "pr", "head_sha", "base_sha", "packet_path", "packet_sha256", "receipt_source": "orca-chatgpt-relay", "receipt_path"}
    逐项核：仓库、PR、HEAD、base 和现读的一致；送审包文件全文 SHA-256 对得上，且包里写着完整 HEAD、base 和 PR 号；
    回执是 chatgpt_relay 写的、没被改过、结论 PASS、绑定的就是这份送审包；回执时间晚于这个 HEAD 的提交时间。任何一项不对或核不清就 Hold。
    现在还没有送审工具写这个文件，所以 L1–L2 的托管合并实际上都会挂起。"""
    path = credential_path(repo, pr, head)
    try:
        cred = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cred, dict):
            raise ValueError("凭证不是对象")
        if (cred.get("repo"), cred.get("pr"), cred.get("head_sha"), cred.get("base_sha")) != (repo, pr, head, base):
            raise ValueError("凭证绑定的仓库 / PR / HEAD / base 和现在的不一致")
        if cred.get("receipt_source") not in REVIEW_SOURCES:
            raise ValueError(f"回执来源 {cred.get('receipt_source')} 不认")
        packet = Path(cred["packet_path"]).read_bytes()
        if not re.fullmatch(r"[0-9a-f]{64}", str(cred.get("packet_sha256"))) or _sha256(packet) != cred["packet_sha256"]:
            raise ValueError("送审包和凭证里的指纹对不上")
        text = packet.decode("utf-8")
        if head not in text or not base or base not in text or not re.search(rf"#{pr}\b", text):
            raise ValueError("送审包里没写这个 PR 的完整 HEAD / base / PR 号")
        meta, when = read_relay_receipt(Path(cred["receipt_path"]).read_text(encoding="utf-8"))
        if meta.get("packet_sha256") != cred["packet_sha256"]:
            raise ValueError("回执审的不是这份送审包")
        if meta.get("verdict") != "PASS":
            raise ValueError(f"回执结论是 {meta.get('verdict')}")
    except FileNotFoundError as e:
        raise Hold("NoReview", f"没有凭证或凭证引用的文件：{e.filename or path}")
    except (OSError, ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
        raise Hold("NoReview", f"{path.name}：{e}")
    try:
        committed = head_commit_time(repo, head)
    except (MA.Stop, ValueError, AttributeError) as e:
        raise Hold("NoReview", f"取不到 HEAD 的提交时间（{e}）")
    if not committed or when <= committed:
        raise Hold("NoReview", f"回执时间 {when.isoformat()} 不晚于 HEAD 提交时间 {committed}")
    return {"kind": "credential", "path": str(path), "packet_sha256": cred["packet_sha256"], "receipt_at": when.isoformat()}


def prior(store, session_id, repo, pr, head):
    """这次托管里同一个 PR × HEAD 已有的决定（新的在后）。"""
    return [d for d in L.decisions(store, session_id)
            if d["action"] in ("merge", "suspend") and (d["target"].get("repo"), d["target"].get("pr"), d["target"].get("head"))
            == (repo, pr, head)]


def gate(store, session_id, night_id, repo, pr, head, status, rules):
    """全部条件逐项核对（只读，不短路：一次列出全部原因）。返回 (holds, info)。"""
    holds, info = [], {"night_issues": None, "tier": None, "review": None, "pr": None}
    for d in prior(store, session_id, repo, pr, head):
        if d["action"] == "merge" and d["status"] in ("unknown", "unreconciled"):
            holds.append(Hold("Unsettled", d["id"]))
        elif d["action"] == "merge" and d["status"] in SETTLED_OR_BUSY:
            holds.append(Hold("Duplicate", d["id"]))
    pol = {}
    try:
        pol = check_policy(status, repo)
    except Hold as h:
        holds.append(h)
    try:
        p = MA.gh_read(["pr", "view", str(pr), "--repo", repo, "--json", PR_FIELDS], f"{repo}#{pr} 取状态")
    except MA.Stop as e:
        holds.append(Hold("PrUnreadable", e.why))
        return holds, info
    info["pr"] = p
    crules = custody_rules(rules)
    paths = sorted(f.get("path", "") for f in p.get("files") or [])
    steps = [lambda: info.__setitem__("night_issues", check_night(night_id, repo, pr, p)),
             lambda: check_scope(p, head),
             lambda: check_side_effects(paths, crules),
             lambda: check_ci(p, repo, crules)]
    for step in steps:
        try:
            step()
        except Hold as h:
            holds.append(h)
    try:
        info["tier"], _ = check_officer(repo, p, rules)
    except Hold as h:
        holds.append(h)
    else:
        if pol and p.get("headRefOid") == head:
            try:
                info["review"] = check_review(info["tier"], repo, pr, p, pol)
            except Hold as h:
                holds.append(h)
    return holds, info


# ---------- 写给常浩看的字 ----------

def plain_title(p, pr):
    title = L.JARGON.sub("", " ".join(str((p or {}).get("title") or "").split()))[:24]
    return f"合并 PR #{pr}" + (f"「{title}」" if title else "")


def project_of(repo):
    for name, full in L.sandbox_projects().items():
        if full == repo:
            return name
    raise L.LedgerError(f"{repo} 不是名册里的沙盒仓库，托管只管沙盒", "BadInput")


def suspend(store, session_id, repo, pr, head, holds, p, now, status_reader=None):
    """记一笔挂起。同一个 PR × HEAD 在这次托管里已经挂起过就不再记（避免夜跑主控反复调用刷屏）。"""
    for d in prior(store, session_id, repo, pr, head):
        if d["action"] == "suspend":
            return d
    plain = "；".join(dict.fromkeys(h.plain for h in holds))
    basis = ("没替你合：" + plain)[:100]
    return L.issue(store, session_id=session_id, project=project_of(repo), action="suspend",
                   target={"repo": repo, "pr": pr, "head": head}, context=plain_title(p, pr)[:40],
                   question=f"agent 开的 PR #{pr} 能不能替你合进主分支", basis=basis,
                   ask_owner="看一下这个 PR：要合就在电脑上合，不合不用管", revocability="revertible",
                   received={"repo": repo, "pr": pr, "head": head, "holds": [{"code": h.code, "detail": h.detail} for h in holds]},
                   now=now, status_reader=status_reader)


def merge_basis(tier):
    if tier == "L0":
        return "只改了你允许的文档，自动测试全过，合并官建议合并；不对可以用 git 撤回"
    return "自动测试全过，审查通过，合并官建议合并；不对可以用 git 撤回"


# ---------- 记录 ----------

def record_custody(repo, pr, head, p, rules):
    """agreement.csv 记 owner=custody（不进影子一致率，merge_officer.rates 排除）。这一行还没有就按现判补一行。
    合并已经做成，记不上不改变结果：返回 True / False，调用方写进输出。"""
    try:
        _record_custody(repo, pr, head, p, rules)
        return True
    except (OSError, ValueError, KeyError, MA.Stop, mo.GhError):
        return False


def _record_custody(repo, pr, head, p, rules):
    rows = mo.read_rows()
    if p is None and not any((r["repo"], r["pr"], r["head_sha"]) == (repo, str(pr), head) for r in rows):
        p = MA.gh_read(["pr", "view", str(pr), "--repo", repo, "--json", mo.PR_FIELDS], f"{repo}#{pr} 补记合并官判定")
    hit = [r for r in rows if (r["repo"], r["pr"], r["head_sha"]) == (repo, str(pr), head)]
    if not hit and p:
        row = mo.new_row(mo.facts_of(repo, p), rules, "live", mo.now())
        rows.append(row)
        hit = [row]
    for r in hit:
        r["owner"], r["owner_at"] = "custody", mo.now()
    mo.write_rows(rows)


@contextlib.contextmanager
def merge_lock():
    """同一时刻只做一个托管合并（检查到记账之间不让第二个进程插进来合同一个 PR）。"""
    d = L.raw_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(d / "merge.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


# ---------- 对账 ----------

def reconcile_merge(store, d, now=None):
    """结果不明的托管合并只读对账一次（不重发）。返回对账后的决定。"""
    now = now or C.now_tz()
    t = d["target"]
    try:
        p = MA.gh_read(["pr", "view", str(t["pr"]), "--repo", t["repo"], "--json", "state,mergeCommit,headRefOid"], "对账回读")
    except MA.Stop as e:
        return L.reconcile(store, d["id"], "unreconciled", {"checked": f"回读 PR 失败：{e.why}"})
    state, head, sha = p.get("state"), p.get("headRefOid"), (p.get("mergeCommit") or {}).get("oid")
    checked = f"回读 {t['repo']}#{t['pr']}：{state}，HEAD {str(head)[:12]}"
    if state == "MERGED" and head == t["head"] and SHA.match(str(sha)):
        d = L.reconcile(store, d["id"], "done", {"checked": checked, "pr_state": "MERGED", "merge_commit": sha})
        record_custody(t["repo"], t["pr"], t["head"], None, mo.load_rules())
        return d
    if state == "CLOSED":
        return L.reconcile(store, d["id"], "failed", {"checked": checked, "pr_state": "CLOSED"})
    if state == "OPEN" and head != t["head"]:
        return L.reconcile(store, d["id"], "failed", {"checked": checked + "（换了版本，绑定 sha 的请求合不了）", "pr_state": "OPEN"})
    sent = C.parse_iso(str(d["outcome"].get("sent_at") or d["updated_at"]))
    if state == "OPEN" and now - sent >= RECONCILE_GRACE:
        return L.reconcile(store, d["id"], "failed", {"checked": checked + f"（请求发出 {RECONCILE_GRACE} 后仍开着）", "pr_state": "OPEN"})
    if state == "OPEN":
        return d                                       # 刚发出去，等下次对账
    return L.reconcile(store, d["id"], "unreconciled", {"checked": checked + "（核不清）"})


# ---------- 主流程 ----------

def custody_merge(session_id, night_id, repo, pr, head, now=None, status_reader=None):
    """托管合并一个 PR × HEAD。返回 {"status": merged|failed|unsure|suspended|refused, "decision", "holds", ...}。
    refused：连挂起都记不了（不是这次托管、账本出错、仓库不是沙盒），原因在 why。"""
    status_reader = status_reader or (lambda t: C.read_status(now=t))
    out = {"repo": repo, "pr": pr, "head": head, "session": session_id, "decision": None, "holds": []}
    rules = mo.load_rules()
    with merge_lock(), L.open_store() as store:
        status = status_reader(now or C.now_tz())
        holds, info = gate(store, session_id, night_id, repo, pr, head, status, rules)
        p = info["pr"]

        def held(hs):
            out["holds"] = [{"code": h.code, "plain": h.plain, "detail": h.detail} for h in hs]
            try:
                d = suspend(store, session_id, repo, pr, head, hs, p, now, status_reader)
            except L.LedgerError as e:
                return {**out, "status": "refused", "why": f"挂起也记不了：{e}"}
            return {**out, "status": "suspended", "decision": d["id"]}

        if holds:
            return held(holds)
        try:
            d = L.issue(store, session_id=session_id, project=project_of(repo), action="merge",
                        target={"repo": repo, "pr": pr, "head": head}, context=plain_title(p, pr)[:40],
                        summary=f"合并 PR #{pr}", basis=merge_basis(info["tier"]), revocability="revertible",
                        expires_at=(now or C.now_tz()) + DECISION_TTL,
                        received={"repo": repo, "pr": pr, "head": head, "tier": info["tier"], "review": info["review"],
                                  "night": night_id, "night_issues": info["night_issues"]},
                        now=now, status_reader=status_reader)
        except L.LedgerError as e:
            return held([Hold(LEDGER_CODE.get(e.code, "LedgerError"), str(e))])
        out["decision"] = d["id"]
        try:
            L.begin(store, d["id"], now=now, status_reader=status_reader)
        except L.LedgerError as e:
            with contextlib.suppress(L.LedgerError):
                L.void(store, d["id"], f"动手前没过：{e}")
            return held([Hold(LEDGER_CODE.get(e.code, "NotAdmitted"), str(e))])
        sent_at = C.iso(now or C.now_tz())
        try:
            res = MA.run_custody_merge({"repo": repo, "pr": pr, "head": head})
        except MA.Stop as e:                           # 凭据取不到：run_custody_merge 一个写请求都没发
            L.finish(store, d["id"], "failed", {"why": e.why, "sent_at": None}, now=now)
            return {**out, "status": "failed", "why": e.why}
        except Exception as e:  # noqa: BLE001  其余异常：可能已经发出，按结果不明处理，不重发
            L.finish(store, d["id"], "unknown", {"why": f"{type(e).__name__}：{e}"[:300], "sent_at": sent_at}, now=now)
            d = reconcile_merge(store, L.get(store, d["id"]), now)
            return {**out, "status": "unsure", "ledger_status": d["status"]}
        why = res.get("why") or ""
        if res["status"] == "merged":
            L.finish(store, d["id"], "done", {"merge_commit": res["merge_commit"], "sent_at": sent_at}, now=now)
            return {**out, "status": "merged", "merge_commit": res["merge_commit"],
                    "agreement_recorded": record_custody(repo, pr, head, p, rules)}
        if res["status"] == "failed":
            L.finish(store, d["id"], "failed", {"why": why, "sent_at": sent_at}, now=now)
            return {**out, "status": "failed", "why": why}
        L.finish(store, d["id"], "unknown", {"why": why, "sent_at": sent_at}, now=now)
        d = reconcile_merge(store, L.get(store, d["id"]), now)
        return {**out, "status": "unsure", "ledger_status": d["status"], "why": why}


def cmd_reconcile(decision_id):
    with merge_lock(), L.open_store() as store:
        d = L.get(store, decision_id)
        if d["action"] != "merge" or d["status"] not in ("executing", "unknown", "unreconciled"):
            return {"ok": False, "msg": f"{decision_id} 不是待对账的托管合并（{d['action']} / {d['status']}）"}
        d = reconcile_merge(store, d)
        return {"ok": True, "msg": f"对账后：{L.STATUS[d['status']]}", "status": d["status"]}


def main(argv=None):
    a = argparse.ArgumentParser(description="托管合并（#136）")
    sub = a.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--session", required=True)
    m.add_argument("--night", required=True)
    m.add_argument("ref", help="owner/repo#PR@40位HEAD")
    r = sub.add_parser("reconcile")
    r.add_argument("decision")
    a = a.parse_args(argv)
    try:
        if a.cmd == "reconcile":
            res = cmd_reconcile(a.decision)
        else:
            ref = REF.match(a.ref)
            if not ref:
                res = {"ok": False, "status": "refused", "msg": f"看不懂 {a.ref}（要写成 owner/repo#PR@40位HEAD）"}
            else:
                res = custody_merge(a.session, a.night, ref.group(1), int(ref.group(2)), ref.group(3))
                res = {"ok": res["status"] == "merged", **res}
    except (L.LedgerError, OSError, ValueError, KeyError) as e:
        res = {"ok": False, "status": "refused", "msg": f"{type(e).__name__}：{e}"}
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
