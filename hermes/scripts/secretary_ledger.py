#!/usr/bin/env python3
"""秘书账本（#195 批 0B-4b）：每个秘书代批决定的决定号、原文、额度预占、送达结果、处置状态，只有这里一处真源。

依据：秘书代批规格（docs/秘书代批-规格.md）第 2 节（代批决定记账本）、第 3 节（四类）、第 4 节（九条交常浩）、第 6、7 节（先预占后执行、
结果不明冻结）、第 8 节（额度、排空、推翻、统计）。复用方式「甲」（常浩 11-18 定）：通用函数全在 custody_ledger.py，本脚本只用
SECRETARY 这个「账本用途」调用它们，另写秘书自己的入口检查（动作、对象、准入、额度）和统计。
本脚本只记账、预占、证明排空，**不让代批生效**：不答卡片、不重派、不留言、不合并、不发飞书（送达是 0B-4c）。

存哪里：
  twin-state 库（DT_STATE_DB，v5 起）的 secretary_decisions / secretary_quota / secretary_sessions；每次变化同一个事务里记一条事件
  secretary.decision.<动作>（object_type = secretary_decision）。账本身份存 meta 的 secretary_ledger_id，和托管账本分开。
  原文存 DT_SECRETARY_DIR（默认 ~/Library/Application Support/DigitalTwin/secretary）/<秘书会话号>/<决定号>.json（700 / 600），
  保留 90 天。库文件不在就拒绝（LedgerMissing），不隐式新建。

决定（action）五种：ask（① 答提问卡）、redispatch（② 放弃后重派一次）、reject（③ 退回 PR）、merge（④ 合并 PR）、
  handoff（交常浩：不占额度、不要准入，但只能记进当前这次秘书会话（开着、排空、冻结中都算）并核对账本身份；
  必须写 category（这件事属于哪一类，统计用）和 reason（规格第 4 节九条之一的代号，见 HANDOFF_REASONS））。
撤回能力：ask / redispatch / reject 只能 correctable，merge 只能 revertible，irrevocable 只许 handoff。
对象（target）：ask {"card": 正整数, "task": 任务号, "generation": 正整数}；redispatch {"task", "generation"}；
  reject / merge {"task", "repo": 名册里这个项目的完整仓库名, "pr": 正整数, "head": 40 位小写十六进制}；handoff 按 category 用上面的形状。
  任务号用 autoloop_grants.TASK_ID 校验（t-YYYYMMDD-6 位十六进制）。入口只核格式（项目名、对象各项）；四类在准入分支再核名册：
  项目现在在名册里指向的仓库要等于签署时固定的（secretary signature.projects），reject / merge 的 repo 要等于它。
  handoff 不核名册（项目被移出名册、清单失效时也要能交常浩），只核格式、category、reason、当前会话和账本身份。
原文（received / reply，P1-04）：四类都要 received（可信侧取的事项快照，非空字典）和 reply（明确的决定，非空字典；① 要带选项号
  choice）；handoff 要 received，reply 可以不写。不合格 BadInput，什么都不写。
清单版本（policy_version / policy_sha256）：四类取准入时核验过的清单，handoff 取当前会话绑定的清单；写进决定行和 issue 事件。
准入（非 handoff，持账本写锁时现读 secretary.read_status）：admission eligible、会话对得上、账本身份对得上；清单里这一类是「是」；
  项目在清单第 2 节；合并的仓库在清单第 4 节白名单里。
额度（同一个 BEGIN IMMEDIATE 事务里原子预占，占用 = reserved + used + frozen，状态跟着走同 custody_ledger）：
  day（scope = 当天日期 Asia/Shanghai，上限「每天代批总数」，四类都占）、task（scope = 任务号，上限「每张任务最多代批」，跨会话累计）、
  merge_day（scope = 当天日期，只 merge 占）、redispatch（scope = 任务号，上限 1，只 redispatch 占）。
写给常浩看的五项：复用 custody_ledger.check_owner_text / JARGON；ask 按「回复」的必填规则（要写 agent 问了什么），
  redispatch / reject / merge 按「合并」的必填规则（没有提问可写），handoff 按「挂起」的必填规则。
送达状态、额度状态、处置状态的转换规则和托管账本完全一样（custody_ledger 头注释）；状态 handoff 对应托管的 suspended。

给 0B-4c 的接口（import secretary_ledger as SL; with SL.open_store() as st:）：
  SL.issue(st, session_id=, project=, action=, target=, basis=, revocability=, category=, context=, question=, summary=, ask_owner=,
           cites=, expires_at=, received=, reply=, reason=, card_version=)  后闸记账 + 原子预占（拒绝抛 custody_ledger.LedgerError）
  SL.begin(st, d) / SL.finish(st, d, "done"|"failed"|"unknown", outcome) / SL.reconcile(st, d, 结果, evidence) / SL.void(st, d, 原因)
  SL.drain(st, 会话号, 账本身份, now) 排空（secretary.drain_proof / ledger_cleanup 调）；SL.stats(st, "YYYY-MM-DD") 统计。
  对账证据：合并同托管（pr_state + merge_commit）；其余三类 done 要 found=true 并带 ref（回读到的回答 / 事件 / 评论），failed 要 found=false。
  同一张卡同一版本只许一个可送达决定（规格第 6 节，0B-4c）：twin-state v6 的部分唯一索引 secretary_decisions_card，
  ① 记账时必须给 card_version（卡片版本，可信侧现读的），和 target.card 一起写进 card_id / card_version 两列；
  已有一个已出未送达、正在送达、已送达、结果不明或对账不清的 ① 时再记，拒绝（DuplicateCard），没做成、作废的不占。
  结果不明冻结这张任务的后续代批（第 7 节）由送达闸 secretary_gate.py 从账本现算。
命令行（只读；最后一行都是一行 JSON：{"ok", "msg", ...}）：
  secretary_ledger.py show d-…                一个决定的全部字段
  secretary_ledger.py list [--session s-…]    决定列表
  secretary_ledger.py drain-check --session s-…  排空还差什么（不作废）
  secretary_ledger.py stats --day YYYY-MM-DD  那一天每类的总数、代批数、交常浩数和原因、被推翻数
威胁模型同 custody.py：防模型自报、文本冒充、重放；不防同一 macOS 用户下的恶意本地进程。
"""
import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autoloop_grants as G  # noqa: E402  同目录：任务号格式只认这一处
import custody as C  # noqa: E402
import custody_ledger as L  # noqa: E402
import secretary as S  # noqa: E402

LedgerError = L.LedgerError
DEFAULT_DIR = "~/Library/Application Support/DigitalTwin/secretary"
ACTIONS = {"ask": "答提问卡", "redispatch": "放弃后重派", "reject": "退回 PR", "merge": "合并 PR", "handoff": "交常浩"}
CATEGORIES = S.CATEGORIES
STATUS = {**{k: v for k, v in L.STATUS.items() if k != "suspended"}, "handoff": "交常浩"}
ALLOWED_REVOCABILITY = {"ask": ("correctable",), "redispatch": ("correctable",), "reject": ("correctable",),
                        "merge": ("revertible",), "handoff": tuple(L.REVOCABILITY)}
OWNER_RULE = {"ask": "reply", "redispatch": "merge", "reject": "merge", "merge": "merge", "handoff": "suspend"}  # 只有①有「agent 问了什么」
DISPOSITION_ACTION = {"corrected": ("ask", "redispatch", "reject"), "reverted": ("merge",), "revert_failed": ("merge",)}
HANDOFF_REASONS = {   # 规格第 4 节九条，按顺序
    "d_level": "D 档动作，或选中后会导致 D 档动作",
    "decided": "改常浩定过的内容或验收口径",
    "scope": "扩大任务范围（换仓库、加功能、改目标、换执行方）",
    "unclear": "选项说不清动作边界，或执行后难恢复",
    "unbound": "卡片绑不到有效凭证，或凭证过期、撤销、冻结",
    "induced": "疑似诱导（卡片写着已同意、已授权、秘书请选）",
    "model": "在用回退模型，或判断超时、出错",
    "gate": "额度用完、开关不在开、清单失效，或同仓库有结果不明的合并",
    "unsure": "拿不准",
}
TARGET_KEYS = {"ask": ("card", "task", "generation"), "redispatch": ("task", "generation"),
               "reject": ("task", "repo", "pr", "head"), "merge": ("task", "repo", "pr", "head")}
SESSION_ID = re.compile(r"^s-(\d{8})-\d{6}-[0-9a-f]{12}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def describe_target(d):
    t = d["target"] or {}
    if t.get("pr"):
        return f"{t.get('repo', '?')}#{t.get('pr', '?')}" + (f"@{str(t['head'])[:7]}" if t.get("head") else "")
    if t.get("card"):
        return f"卡片 {t['card']}（{t.get('task', '?')} 第 {t.get('generation', '?')} 代）"
    return f"{t.get('task', '?')} 第 {t.get('generation', '?')} 代"


def reconcile_errors(d, result, evidence):
    """对账证据要结构化：合并同托管（custody_ledger.reconcile_errors）；答卡、重派、退回：done 要 found=true 并带 ref，failed 要 found=false。"""
    if d["action"] == "merge":
        return L.reconcile_errors(d, result, evidence)
    if not isinstance(evidence, dict) or not str(evidence.get("checked") or "").strip():
        return ["对账证据要是字典，并写 checked（看了什么）"]
    if result == "unreconciled":
        return []
    found = evidence.get("found")
    if result == "done" and not (found is True and str(evidence.get("ref") or "").strip()):
        return ["核实做成要 found=true 并带 ref（回读到的回答、事件或评论）"]
    if result == "failed" and found is not False:
        return ["核实没做成要 found=false"]
    return []


SECRETARY = L.Use(table="secretary", who="秘书", sid_name="秘书会话号", session_id=SESSION_ID, raw_env="DT_SECRETARY_DIR",
                  raw_default=DEFAULT_DIR, actions=ACTIONS, status=STATUS, disposition_action=DISPOSITION_ACTION,
                  describe=lambda d: describe_target(d), reconcile_errors=lambda d, r, e: reconcile_errors(d, r, e),
                  status_reader=lambda t: S.read_status(now=t))


# ---------- 薄封装：通用函数 + 秘书用途 ----------
def open_store(now=None, path=None):
    return L.open_store(now, path, use=SECRETARY)


def raw_dir():
    return L.raw_dir(SECRETARY)


def register_session(store, session_id, now=None):
    return L.register_session(store, session_id, now, use=SECRETARY)


def check_session(store, session_id, expected_ledger):
    L.check_session(store, session_id, expected_ledger, use=SECRETARY)


def ledger_id(store):
    return L.ledger_id(store, SECRETARY)


def get(store, decision_id):
    return L.get(store, decision_id, SECRETARY)


def decisions(store, session_id=None):
    return L.decisions(store, session_id, SECRETARY)


def quotas(store, decision_id):
    return L.quotas(store, decision_id, SECRETARY)


def used(store, kind, scope):
    return L.used(store, kind, scope, SECRETARY)


def drain_blockers(store, session_id):
    return L.drain_blockers(store, session_id, SECRETARY)


def begin(store, decision_id, by="hermes", now=None, status_reader=None):
    """送达闸动手之前调：现读秘书准入（会话、账本身份、清单就是开启时那份）、核有效期，issued → executing；过期就作废并释放额度。"""
    return L.begin(store, decision_id, by, now, status_reader, use=SECRETARY)


def finish(store, decision_id, result, outcome=None, by="hermes", now=None):
    return L.finish(store, decision_id, result, outcome, by, now, use=SECRETARY)


def reconcile(store, decision_id, result, evidence, by="hermes", now=None):
    return L.reconcile(store, decision_id, result, evidence, by, now, use=SECRETARY)


def void(store, decision_id, reason, by="system", now=None):
    return L.void(store, decision_id, reason, by, now, use=SECRETARY)


def drain(store, session_id, ledger=None, now=None):
    return L.drain(store, session_id, ledger, now, use=SECRETARY)


def set_disposition(store, decision_id, to, note, by="user", now=None):
    return L.set_disposition(store, decision_id, to, note, by, now, use=SECRETARY)


# ---------- 记账 ----------
def _pos_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def target_errors(category, target, repo=None):
    """对象的形状（见头注释），只看格式、不看名册；给了 repo（这个项目签署时固定的完整仓库名）再核仓库对不对。返回错误列表。"""
    want = TARGET_KEYS[category]
    if not isinstance(target, dict) or set(target) != set(want):
        return [f"{CATEGORIES[category]}的对象要正好有 {'、'.join(want)} 这几项"]
    errors = []
    if not G.TASK_ID.match(str(target["task"])):
        errors.append(f"任务号要是 t-YYYYMMDD-6 位十六进制：{target['task']!r}")
    for key in ("card", "generation", "pr"):
        if key in want and not _pos_int(target[key]):
            errors.append(f"{key} 要是正整数：{target[key]!r}")
    if "head" in want and not L.SHA.match(str(target["head"])):
        errors.append("head 要是完整的 40 位小写十六进制")
    if "repo" in want and not (isinstance(target["repo"], str) and re.fullmatch(S.REPO_FULL, target["repo"])):
        errors.append(f"仓库要写完整仓库名 owner/仓库：{target['repo']!r}")
    elif "repo" in want and repo is not None and target["repo"] != repo:
        errors.append(f"仓库要写这个项目签署时在名册里的完整仓库名（{repo}）：{target['repo']!r}")
    return errors


def raw_errors(action, received, reply):
    """原文（规格第 2 节：哪张卡 / 哪个 PR、选了什么都要能复查）：四类都要 received（可信侧取的事项快照，非空字典）和
    reply（明确的决定，非空字典；① 要带选中的选项号 choice，正整数）；交常浩要 received（触发弃权的事项），reply 可以不写。"""
    errors = []
    if not (isinstance(received, dict) and received):
        errors.append("received（收到的事项快照）要是非空字典")
    if action == "handoff":
        if reply is not None and not isinstance(reply, dict):
            errors.append("交常浩的 reply 要么不写，要么是字典")
    elif not (isinstance(reply, dict) and reply):
        errors.append("reply（明确的决定）要是非空字典")
    elif action == "ask" and not _pos_int(reply.get("choice")):
        errors.append("① 答提问卡的 reply 要带选中的选项号 choice（正整数）")
    return errors


def issue(store, *, session_id, project, action, target, basis, revocability, category=None, context="", question="",
          summary="", ask_owner="", cites=(), expires_at=None, received=None, reply=None, reason=None, card_version=None,
          by="hermes", now=None, status_reader=None):
    """后闸记账（规格第 7 节）：决定号、原文、额度原子预占。四类要准入、清单允许、额度够；handoff 只记账。
    card_version：① 必填（正整数，可信侧现读的卡片版本），同一张卡同一版本只许一个可送达的 ①（v6 唯一索引）；
    对象带卡片的交常浩可以写，用来复查。返回决定（dict）。任何一项不满足抛 LedgerError，库里什么都不写、原文删掉。"""
    fixed_now = now
    now = L._now(now)
    status_reader = status_reader or SECRETARY.status_reader
    if action not in ACTIONS:
        raise LedgerError(f"动作类型只能是 {'/'.join(ACTIONS)}：{action}", "BadInput")
    if action == "handoff":
        if category not in CATEGORIES:
            raise LedgerError(f"交常浩要写这件事属于哪一类（category：{'/'.join(CATEGORIES)}）", "BadInput")
        if reason not in HANDOFF_REASONS:
            raise LedgerError(f"交常浩要写原因代号（reason：{'/'.join(HANDOFF_REASONS)}，规格第 4 节九条）", "BadInput")
    else:
        if category not in (None, action):
            raise LedgerError(f"{ACTIONS[action]}的类别就是 {action}，不能写成 {category}", "BadInput")
        if reason is not None:
            raise LedgerError("只有交常浩才写原因代号（reason）", "BadInput")
        category = action
    if revocability not in ALLOWED_REVOCABILITY[action]:
        raise LedgerError(f"{ACTIONS[action]}的撤回能力不能是「{L.REVOCABILITY.get(revocability, revocability)}」"
                          "（撤不回的一律交常浩）", "BadInput")
    if not SESSION_ID.match(session_id or ""):
        raise LedgerError(f"秘书会话号不对：{session_id}", "BadInput")
    if not (isinstance(project, str) and re.fullmatch(S.PROJECT_NAME, project)):
        raise LedgerError(f"项目名看不懂：{project!r}", "BadInput")
    bad = raw_errors(action, received, reply)
    if bad:
        raise LedgerError("原文不合格：" + "；".join(bad), "BadInput")
    bad = L.check_owner_text(OWNER_RULE[action], {"context": context, "question": question, "summary": summary, "basis": basis,
                                                  "ask_owner": ask_owner})
    if bad:
        raise LedgerError("写给常浩的字不合格：" + "；".join(bad), "BadText")
    bad = target_errors(category, target)                # 只看格式；仓库对不对在准入分支按签署时的映射核（handoff 不核名册，P1-02）
    if bad:
        raise LedgerError("对象不对：" + "；".join(bad), "BadInput")
    exp = None
    if action != "handoff":
        exp = C.parse_iso(expires_at) if isinstance(expires_at, str) else expires_at
        if not isinstance(exp, dt.datetime) or exp.tzinfo is None:
            raise LedgerError("代批要写将来的有效期（带时区）", "BadInput")
    cites = list(cites or ())
    if any(not L.DECISION_ID.match(str(c)) for c in cites):
        raise LedgerError(f"依据只能引用决定号：{cites}", "BadInput")
    if action == "ask" and not _pos_int(card_version):
        raise LedgerError("① 答提问卡要写卡片版本（card_version，正整数）：同一张卡同一版本只许一个可送达的决定", "BadInput")
    if card_version is not None and not (_pos_int(card_version) and "card" in target):
        raise LedgerError("只有对象带卡片时才写卡片版本（正整数）", "BadInput")
    task = target["task"]

    def gate(t):
        if action == "handoff":              # 不要准入、不核名册（名册或清单失效时也要能交常浩），但只能记进现在这次，并核对账本身份
            sess = L.current_session(store, session_id, t, status_reader, SECRETARY)
            pol = sess.get("policy") or {}
            if not (_pos_int(pol.get("version")) and re.fullmatch(r"[0-9a-f]{64}", str(pol.get("sha256")))):
                raise LedgerError("现在这次秘书会话没有绑定清单版本，记不了", "WrongSession")
            return "handoff", [], {"policy_version": pol["version"], "policy_sha256": pol["sha256"]}
        pol = L._admitted(store, session_id, t, status_reader, SECRETARY)
        if (pol.get("categories") or {}).get(action) is not True:
            raise LedgerError(f"代批清单没允许「{CATEGORIES[action]}」", "NotAllowed")
        if project not in (pol.get("projects") or ()):
            raise LedgerError(f"项目「{project}」不在代批清单第 2 节里", "NotAllowed")
        signed = (pol.get("repos") or {}).get(project)
        try:
            now_repo = S.dispatch_projects().get(project)
        except Exception as e:               # noqa: BLE001  名册读不了、短名冲突就不记账（fail closed）
            raise LedgerError(f"名册读不了，不记账（{type(e).__name__}：{e}）", "RosterUnavailable")
        if not signed or now_repo != signed:
            raise LedgerError(f"项目「{project}」签署时是 {signed or '没有'}，现在名册里是 {now_repo or '没有'}，不记账", "RosterChanged")
        bad = target_errors(category, target, signed)
        if bad:
            raise LedgerError("对象不对：" + "；".join(bad), "BadInput")
        if action == "merge" and target["repo"] not in (pol.get("whitelist") or {}):
            raise LedgerError(f"{target['repo']} 不在代批清单的合并白名单里", "NotAllowed")
        lim = pol.get("limits") or {}
        if any(not _pos_int(lim.get(k)) for k in ("day", "task", "merge_day", "redispatch")):
            raise LedgerError("有效清单里读不出额度上限，不记账", "NoLimits")
        day = t.astimezone(C.TZ).date().isoformat()
        holds = [("day", day, lim["day"], f"{day} 的代批总数"), ("task", task, lim["task"], f"任务 {task} 的代批次数")]
        if action == "merge":
            holds.append(("merge_day", day, lim["merge_day"], f"{day} 的代批合并数"))
        if action == "redispatch":
            holds.append(("redispatch", task, lim["redispatch"], f"任务 {task} 的放弃后重派"))
        L.check_holds(store, holds, SECRETARY)
        return "issued", holds, {"policy_version": pol["version"], "policy_sha256": pol["sha256"]}

    row = {"project": project, "action": action, "category": category, "target": L._j(target), "task_id": task,
           "reason": reason or "", "context": context.strip(), "question": question.strip(), "summary": summary.strip(),
           "ask_owner": ask_owner.strip(), "basis": basis.strip(), "cites": L._j(cites), "revocability": revocability,
           "card_id": target.get("card"), "card_version": card_version}
    payload = {"session": session_id, "project": project, "action": action, "category": category, "target": target,
               "reason": reason, "revocability": revocability, "cites": cites, "expires_at": C.iso(exp) if exp else None,
               "card_version": card_version}
    try:
        return L.record(store, use=SECRETARY, session_id=session_id, row=row, payload=payload, cites=cites, exp=exp,
                        fixed_now=fixed_now, now=now, received=received, reply=reply, by=by, gate=gate)
    except sqlite3.IntegrityError as e:              # v6 唯一索引：同卡同版本已有一个可送达的 ①（record 已回滚、删了原文）
        if "secretary_decisions" not in str(e):
            raise
        raise LedgerError(f"卡片 {target.get('card')} 第 {card_version} 版已经有一个可送达的秘书决定，不再记第二个", "DuplicateCard")


# ---------- 统计（规格第 8 节） ----------
def stats(store, day):
    """那一天（Asia/Shanghai，按记账时间）每类的总数、代批数（非交常浩）、送达数（done）、交常浩数和原因分布、被推翻数
    （处置不是「未处理」的），以及代批省下的等待：送达成功的决定 outcome.waited_seconds（飞书送达 → 秘书送达，0B-4c 送达闸记）
    之和与条数（waited_seconds / waited_count；没记的不算）。"""
    if not DAY.match(day or ""):
        raise LedgerError(f"日期要写成 YYYY-MM-DD：{day}", "BadInput")
    out = {"day": day, "total": 0, "categories": {}, "reasons": {}}
    for k in CATEGORIES:
        out["categories"][k] = {"total": 0, "decided": 0, "delivered": 0, "handoff": 0, "reasons": {}, "overturned": 0,
                                "waited_seconds": 0, "waited_count": 0}
    rows = store.conn.execute(f"SELECT * FROM {SECRETARY.decisions} WHERE substr(created_at, 1, 10) = ?", (day,)).fetchall()
    for d in rows:
        c = out["categories"][d["category"]]
        c["total"] += 1
        out["total"] += 1
        if d["action"] == "handoff":
            c["handoff"] += 1
            c["reasons"][d["reason"]] = c["reasons"].get(d["reason"], 0) + 1
            out["reasons"][d["reason"]] = out["reasons"].get(d["reason"], 0) + 1
        else:
            c["decided"] += 1
            c["delivered"] += d["status"] == "done"
            waited = L._row(d)["outcome"].get("waited_seconds")
            if d["status"] == "done" and isinstance(waited, int) and not isinstance(waited, bool) and waited >= 0:
                c["waited_seconds"] += waited
                c["waited_count"] += 1
        c["overturned"] += d["disposition"] != "open"
    return out


# ---------- 命令行 ----------
def main(argv=None):
    done = L.done_line
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("decision")
    sub.add_parser("list").add_argument("--session")
    sub.add_parser("drain-check").add_argument("--session", required=True)
    sub.add_parser("stats").add_argument("--day", required=True)
    a = ap.parse_args(argv)
    try:
        with L.TS.Store(readonly=True) as st:
            if a.cmd == "show":
                d = get(st, a.decision)
                d["quota"] = quotas(st, a.decision)
                print(json.dumps(d, ensure_ascii=False, indent=2))
                done(True, f"{d['id']}：{ACTIONS[d['action']]}（{CATEGORIES[d['category']]}），{STATUS[d['status']]}，"
                           f"{L.DISPOSITION[d['disposition']]}，依据清单 v{d['policy_version']}（{d['policy_sha256'][:12]}）",
                     policy_version=d["policy_version"], policy_sha256=d["policy_sha256"])
            if a.cmd == "list":
                rows = decisions(st, a.session)
                for d in rows:
                    print(f"{d['id']}  {d['session_id']}  {d['project']}  {ACTIONS[d['action']]}  {describe_target(d)}  "
                          f"{STATUS[d['status']]}  {L.DISPOSITION[d['disposition']]}  清单 v{d['policy_version']}"
                          f"（{d['policy_sha256'][:12]}）")
                done(True, f"共 {len(rows)} 个决定")
            if a.cmd == "drain-check":
                b = drain_blockers(st, a.session)
                done(not b, "排空已收完（drain 时还会作废已出未送达的）" if not b else "还没收完：" + "；".join(b), blockers=b)
            if a.cmd == "stats":
                s = stats(st, a.day)
                print(json.dumps(s, ensure_ascii=False, indent=2))
                done(True, f"{a.day} 共 {s['total']} 件：" + "，".join(
                    f"{CATEGORIES[k]} {v['total']}（代批 {v['decided']}、交常浩 {v['handoff']}、推翻 {v['overturned']}）"
                    for k, v in s["categories"].items()))
    except (LedgerError, L.TS.StateError, sqlite3.Error) as e:
        done(False, str(e), error=getattr(e, "code", type(e).__name__))


if __name__ == "__main__":
    main()
