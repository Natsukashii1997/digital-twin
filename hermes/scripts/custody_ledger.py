#!/usr/bin/env python3
"""托管账本（#133）：每个托管决定的决定号、原文、额度预占、送达结果、处置状态，只有这里一处真源。

依据：托管模式规格 §3.5（上限与额度）、§3.6（汇报与撤回）、§3.8（关闭与排空）；ARCHITECTURE 5.7 第 8、9、10 条。
本脚本只记账、预占、证明排空，**不让托管生效**：不发评论、不合并、不发飞书（汇报卡和推翻在 custody_report.py）。

存哪里：
  twin-state 库（DT_STATE_DB，v4 起）的 custody_decisions（决定的当前状态）和 custody_quota（额度预占）；
  每次变化在同一个事务里记一条事件 custody.decision.<动作>（object_type = custody_decision），事件只追加不改。
  原文（收到的待决事项、发出的回复）存 DT_CUSTODY_DIR（默认 ~/Library/Application Support/DigitalTwin/custody）
  /<托管号>/<决定号>.json（目录 700、文件 600），保留 90 天：每次记新决定时顺手删掉托管号日期早于 90 天的目录。

决定（action）三种：reply（回复 blocked 子单）、merge（托管合并）、suspend（挂起：底线、拿不准、闸不过，只记账、进汇报）。
撤回能力（revocability，记账时写定）：reply 只能是 correctable（可纠正），merge 只能是 revertible（可回退）；
irrevocable（不可撤回）只许出现在挂起里——撤不回的动作一律挂起（规格 §3.6），账本在入口就拒。

送达状态（status）：
  issued     已出未送达（额度已预占）          → executing（送达闸 / 合并执行器开始做）| void（作废）
  executing  正在送达 / 正在合并                → done | failed | unknown
  unknown    结果不明（只读核对前不重发）        → done | failed | unreconciled（对账）
  unreconciled 对账也核不清（UNRECONCILED）      → done | failed（之后核清了）
  done 已送达 / 已合并；failed 确认没送达 / 没合并；void 作废（排空、过期、手动）；suspended 挂起（不占额度）
额度（custody_quota）跟着走：issued / executing → reserved；done → used；failed / void → released；unknown / unreconciled → frozen。
  三种额度：reply（每次托管的回复数）、issue（每张子单被托管决定的次数，跨托管累计，从严）、merge（每次托管的合并数）；
  上限只从当前有效、已签的策略第 2 节读（custody.verify_policy 的 limits）；占用 = reserved + used + frozen。
  预占和准入复核在同一个 BEGIN IMMEDIATE 事务里：两个决定同时抢最后一个额度，写锁让它们排队，后来的数到已满就拒。
  准入复核在持写锁时现读 custody.read_status（它不加锁，读原子替换的文件）：排空命令要拿同一把写锁才能作废，所以
  「复核时还开着、刚记完就转排空」的决定一定会被随后的排空看到并作废。

处置状态（disposition，常浩点「推翻」后变化）：
  open 未处理 → overturned 已推翻·已记录 → corrected 已纠正（只限 reply）| reverted 已回退 / revert_failed 回退失败（只限 merge）；
  revert_failed → reverted（再试成功）。推翻只能在决定不在途时（不是 issued / executing）。

排空（custody.drain_proof 调 drain）：先把这次托管里 issued 的决定全部作废、释放额度（规格 §3.8 第 2 步），
  再看还有没有 executing、unknown、unreconciled 的决定或没释放的预占；一项都没有才算排空完成。

给后续批次的接口（#134 出口闸 / 送达闸、#136 合并执行器）：
  import custody_ledger as L; with L.open_store() as st:
    L.issue(st, session_id=, project=, action=, target=, question=, summary=, basis=, cites=, revocability=, expires_at=,
            received=, reply=)                  出口闸记账 + 原子预占（拒绝抛 LedgerError，code 给机器判）
    L.begin(st, d) / L.finish(st, d, "done"|"failed"|"unknown", outcome) / L.reconcile(st, d, 结果, evidence) / L.void(st, d, 原因)
  送达闸、合并执行器开始做之前必须调 begin（它现读托管准入、核有效期，过期就作废）；结果不明一律 finish(unknown)，不重发。
命令行（最后一行都是一行 JSON：{"ok", "msg", ...}）：
  custody_ledger.py show d-…             一个决定的全部字段（只读）
  custody_ledger.py list [--session c-…] 决定列表（只读）
  custody_ledger.py drain-check --session c-…  只读：排空还差什么（不作废）
威胁模型同 custody.py：防模型自报、文本冒充、重放；不防同一 macOS 用户下的恶意本地进程。
"""
import argparse
import contextlib
import datetime as dt
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import custody as C  # noqa: E402  同目录
import twin_state as TS  # noqa: E402

DEFAULT_DIR = "~/Library/Application Support/DigitalTwin/custody"
RAW_KEEP_DAYS = 90
OBJ = "custody_decision"

ACTIONS = {"reply": "回复", "merge": "合并", "suspend": "挂起"}
REVOCABILITY = {"correctable": "可纠正", "revertible": "可回退", "irrevocable": "不可撤回"}
ALLOWED_REVOCABILITY = {"reply": ("correctable",), "merge": ("revertible",), "suspend": tuple(REVOCABILITY)}
STATUS = {"issued": "已出未送达", "executing": "进行中", "done": "已完成", "failed": "没做成", "unknown": "结果不明",
          "unreconciled": "UNRECONCILED", "void": "已作废", "suspended": "挂起"}
DISPOSITION = {"open": "未处理", "overturned": "已推翻·已记录", "corrected": "已纠正", "reverted": "已回退",
               "revert_failed": "回退失败"}
STATUS_MOVES = {
    "issued": ("executing", "void"),
    "executing": ("done", "failed", "unknown"),
    "unknown": ("done", "failed", "unreconciled"),
    "unreconciled": ("done", "failed"),
}
QUOTA_OF_STATUS = {"issued": "reserved", "executing": "reserved", "done": "used", "failed": "released",
                   "void": "released", "unknown": "frozen", "unreconciled": "frozen"}
QUOTA_HELD = ("reserved", "used", "frozen")
IN_FLIGHT = ("issued", "executing")
UNSETTLED = ("executing", "unknown", "unreconciled")
DISPOSITION_MOVES = {
    ("open", "overturned"),
    ("overturned", "corrected"), ("overturned", "reverted"), ("overturned", "revert_failed"),
    ("revert_failed", "reverted"),
}
DISPOSITION_ACTION = {"corrected": ("reply",), "reverted": ("merge",), "revert_failed": ("merge",)}
DECISION_ID = re.compile(r"^d-\d{6}-[0-9a-f]{6}$")
SESSION_ID = re.compile(r"^c-(\d{8})-\d{6}-[0-9a-f]{12}$")


class LedgerError(Exception):
    """账本拒绝的操作。code 给机器判，str 给人看。拒绝时库和原文都不改。"""

    def __init__(self, msg, code="Rejected"):
        super().__init__(msg)
        self.code = code


# ---------- 路径、时间 ----------
def raw_dir():
    return Path(os.environ.get("DT_CUSTODY_DIR") or DEFAULT_DIR).expanduser()


def open_store(now=None, path=None):
    """打开 twin-state 库（DT_STATE_DB）；给了 now 就用它当事件时间（测试、回放用）。"""
    clock = (lambda: now) if now else None
    return TS.Store(path, clock=clock)


def _now(now):
    return now or C.now_tz()


def _j(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _row(r):
    d = dict(r)
    for k, default in (("target", {}), ("cites", []), ("outcome", {})):
        try:
            d[k] = json.loads(d[k]) if d[k] else default
        except ValueError:
            d[k] = default
    return d


# ---------- 原文 ----------
def _write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def save_raw(session_id, decision_id, received, reply, now):
    """原文另存（规格 §3.6）：收到的待决事项 + 发出的回复。先于记账写，记账失败时留下的孤儿文件 90 天后一起清。"""
    p = raw_dir() / session_id / f"{decision_id}.json"
    _write_private(p, json.dumps({"decision": decision_id, "session": session_id, "saved_at": C.iso(now),
                                  "received": received, "reply": reply}, ensure_ascii=False, indent=2) + "\n")
    return str(p)


def read_raw(decision):
    """读一个决定的原文；过了保留期或读不了返回 None。"""
    try:
        return json.loads(Path(decision["raw_path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def prune_raw(now):
    """删掉托管号日期早于 90 天的原文目录（托管号 c-YYYYMMDD-…）。只删认得出的目录，别的不碰。返回删了哪些。"""
    root, gone = raw_dir(), []
    if not root.is_dir():
        return gone
    cutoff = (now.astimezone(C.TZ) - dt.timedelta(days=RAW_KEEP_DAYS)).strftime("%Y%m%d")
    for d in root.iterdir():
        m = SESSION_ID.match(d.name)
        if m and d.is_dir() and not d.is_symlink() and m.group(1) < cutoff:
            shutil.rmtree(d)
            gone.append(d.name)
    return gone


# ---------- 读 ----------
def get(store, decision_id):
    r = store.conn.execute("SELECT * FROM custody_decisions WHERE id=?", (decision_id,)).fetchone()
    if not r:
        raise LedgerError(f"账本里没有决定 {decision_id}", "NotFound")
    return _row(r)


def decisions(store, session_id=None):
    if session_id:
        rows = store.conn.execute("SELECT * FROM custody_decisions WHERE session_id=? ORDER BY created_at, rowid", (session_id,))
    else:
        rows = store.conn.execute("SELECT * FROM custody_decisions ORDER BY created_at, rowid")
    return [_row(r) for r in rows]


def quotas(store, decision_id):
    return [dict(r) for r in store.conn.execute("SELECT * FROM custody_quota WHERE decision_id=? ORDER BY id", (decision_id,))]


def used(store, kind, scope):
    return store.conn.execute("SELECT COUNT(*) FROM custody_quota WHERE kind=? AND scope=? AND state IN (?,?,?)",
                              (kind, scope, *QUOTA_HELD)).fetchone()[0]


def drain_blockers(store, session_id):
    """只读：这次托管还有什么没收完（不作废任何东西）。issued 也算——排空时 drain 会先作废它们。"""
    out = []
    for d in decisions(store, session_id):
        if d["status"] in IN_FLIGHT + UNSETTLED:
            out.append(f"{d['id']}（{ACTIONS[d['action']]} {describe_target(d)}）{STATUS[d['status']]}")
    held = store.conn.execute("SELECT q.decision_id, q.kind, q.state FROM custody_quota q JOIN custody_decisions d"
                              " ON d.id = q.decision_id WHERE d.session_id=? AND q.state IN ('reserved','frozen')"
                              " AND d.status NOT IN (?,?,?,?,?)", (session_id, *IN_FLIGHT, *UNSETTLED)).fetchall()
    for q in held:                      # 状态已收口、额度却没跟着收口：账本自相矛盾，按没收完处理
        out.append(f"{q['decision_id']} 的 {q['kind']} 额度还是 {q['state']}（和决定状态对不上）")
    return out


def describe_target(d):
    t = d["target"] or {}
    if d["action"] == "merge" or t.get("pr"):
        return f"{t.get('repo', '?')}#{t.get('pr', '?')}" + (f"@{str(t['head'])[:7]}" if t.get("head") else "")
    return str(t.get("issue") or t.get("repo") or "?")


# ---------- 写 ----------
def _event(store, kind, by, decision_id, payload):
    store._record(store._source(by), f"custody.decision.{kind}", by, OBJ, decision_id, payload)


def _set_status(store, d, to, by, now, **fields):
    if to not in STATUS_MOVES.get(d["status"], ()):
        raise LedgerError(f"决定 {d['id']} 现在是「{STATUS[d['status']]}」，不能变成「{STATUS[to]}」", "BadTransition")
    sets = {"status": to, "updated_at": C.iso(now), **fields}
    store.conn.execute(f"UPDATE custody_decisions SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), d["id"]))
    q = QUOTA_OF_STATUS[to]
    store.conn.execute("UPDATE custody_quota SET state=?, updated_at=? WHERE decision_id=? AND state != 'released'",
                       (q, C.iso(now), d["id"]))
    _event(store, to, by, d["id"], {"from": d["status"], "to": to, "quota": q,
                                    **{k: (json.loads(v) if k == "outcome" else v) for k, v in fields.items()}})


def _admitted(session_id, now, status_reader):
    """托管这一层的准入（custody.admission）+ 会话对得上；不过就抛 LedgerError。返回当前有效策略。"""
    st = status_reader(now)
    if not st["admission"]["eligible"]:
        raise LedgerError("托管不准入：" + "；".join(st["admission"]["reasons"]), "NotAdmitted")
    sid = (st.get("session") or {}).get("id")
    if sid != session_id:
        raise LedgerError(f"托管会话对不上：现在开着的是 {sid}，这个决定属于 {session_id}", "WrongSession")
    return st["policy"]


def new_decision_id(now):
    return "d-" + now.astimezone(C.TZ).strftime("%y%m%d") + "-" + secrets.token_hex(3)


def issue(store, *, session_id, project, action, target, basis, revocability, question="", summary="", cites=(),
          expires_at=None, received=None, reply=None, by="hermes", now=None, status_reader=None):
    """出口闸记账（规格 §2 第 ④ 步）：决定号、原文、额度原子预占。reply / merge 要托管准入、策略允许、额度够；suspend 只记账。
    返回决定（dict）。任何一项不满足抛 LedgerError，库里什么都不写。"""
    now = _now(now)
    status_reader = status_reader or (lambda t: C.read_status(now=t))
    if action not in ACTIONS:
        raise LedgerError(f"动作类型只能是 {'/'.join(ACTIONS)}：{action}", "BadInput")
    if revocability not in ALLOWED_REVOCABILITY[action]:
        raise LedgerError(f"{ACTIONS[action]}的撤回能力不能是「{REVOCABILITY.get(revocability, revocability)}」"
                          "（撤不回的动作一律挂起）", "BadInput")
    if not SESSION_ID.match(session_id or ""):
        raise LedgerError(f"托管号不对：{session_id}", "BadInput")
    if not (project or "").strip() or not (basis or "").strip():
        raise LedgerError("决定要写项目和依据", "BadInput")
    if not isinstance(target, dict):
        raise LedgerError("对象（target）要是一个字典", "BadInput")
    issue_key = None
    if action == "reply":
        if not str(target.get("issue") or "").strip():
            raise LedgerError("回复要写子单（target.issue）", "BadInput")
        issue_key = f"{project}:{target['issue']}"
    if action == "merge" and not (target.get("repo") and target.get("pr") and target.get("head")):
        raise LedgerError("合并要写仓库、PR、HEAD（target.repo / pr / head）", "BadInput")
    exp = None
    if action != "suspend":
        exp = C.parse_iso(expires_at) if isinstance(expires_at, str) else expires_at
        if not isinstance(exp, dt.datetime) or exp.tzinfo is None or exp <= now:
            raise LedgerError("回复、合并要写将来的有效期（带时区）", "BadInput")
    cites = list(cites or ())
    if any(not DECISION_ID.match(str(c)) for c in cites):
        raise LedgerError(f"依据只能引用决定号：{cites}", "BadInput")
    prune_raw(now)
    for _ in range(5):
        did = new_decision_id(now)
        if not store.conn.execute("SELECT 1 FROM custody_decisions WHERE id=?", (did,)).fetchone():
            break
    else:
        raise LedgerError("生成不出不重复的决定号（随机源异常）", "Corrupt")
    raw_path = save_raw(session_id, did, received, reply, now)
    try:
        with store._tx() as c:
            missing = [x for x in cites if not c.execute("SELECT 1 FROM custody_decisions WHERE id=?", (x,)).fetchone()]
            if missing:
                raise LedgerError(f"依据引用的决定不在账本里：{'、'.join(missing)}", "BadInput")
            holds = []
            if action != "suspend":
                pol = _admitted(session_id, now, status_reader)
                lim = (pol.get("limits") or {}).get(action)
                if not lim:
                    raise LedgerError("有效策略里读不出额度上限，不记账", "NoLimits")
                if not lim["allowed"]:
                    raise LedgerError(f"策略没允许托管{ACTIONS[action]}", "NotAllowed")
                holds = [(action, session_id, lim["per_session"], f"这次托管的{ACTIONS[action]}额度")]
                if action == "reply":
                    holds.append(("issue", issue_key, lim["per_issue"], f"子单 {target['issue']} 的托管次数"))
                for kind, scope, cap, what in holds:
                    n = used(store, kind, scope)
                    if n >= cap:
                        raise LedgerError(f"{what}已用完（{n}/{cap}）", "QuotaExhausted")
            status = "suspended" if action == "suspend" else "issued"
            ts = C.iso(now)
            c.execute("INSERT INTO custody_decisions (id, session_id, project, action, target, issue_key, question, summary,"
                      " basis, cites, revocability, status, disposition, expires_at, raw_path, created_at, updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'open',?,?,?,?)",
                      (did, session_id, project, action, _j(target), issue_key, question, summary, basis, _j(cites),
                       revocability, status, C.iso(exp) if exp else None, raw_path, ts, ts))
            for kind, scope, _, _ in holds:
                c.execute("INSERT INTO custody_quota (session_id, kind, scope, decision_id, state, created_at, updated_at)"
                          " VALUES (?,?,?,?, 'reserved', ?, ?)", (session_id, kind, scope, did, ts, ts))
            _event(store, "issue", by, did, {"session": session_id, "project": project, "action": action, "target": target,
                                             "status": status, "revocability": revocability, "cites": cites,
                                             "expires_at": C.iso(exp) if exp else None,
                                             "quota": [[k, s] for k, s, _, _ in holds]})
    except BaseException:
        with contextlib.suppress(OSError):
            Path(raw_path).unlink()
        raise
    return get(store, did)


def begin(store, decision_id, by="hermes", now=None, status_reader=None):
    """送达闸、合并执行器动手之前调：现读托管准入、核有效期，issued → executing。过期就作废并释放额度，再抛 LedgerError。"""
    now = _now(now)
    status_reader = status_reader or (lambda t: C.read_status(now=t))
    expired = None
    with store._tx():
        d = get(store, decision_id)
        if d["status"] != "issued":
            raise LedgerError(f"决定 {decision_id} 现在是「{STATUS[d['status']]}」，不能开始送达", "BadTransition")
        if now >= C.parse_iso(d["expires_at"]):
            _set_status(store, d, "void", "system", now, void_reason="过期")
            expired = d["expires_at"]
        else:
            _admitted(d["session_id"], now, status_reader)
            _set_status(store, d, "executing", by, now)
    if expired:
        raise LedgerError(f"决定 {decision_id} 已于 {expired} 过期，已作废、额度已释放", "Expired")
    return get(store, decision_id)


def finish(store, decision_id, result, outcome=None, by="hermes", now=None):
    """送达 / 合并的结果：done（确认做成，额度计为已用）、failed（确认没做成，释放）、unknown（结果不明，额度冻结、不重发）。"""
    if result not in ("done", "failed", "unknown"):
        raise LedgerError(f"结果只能是 done / failed / unknown：{result}", "BadInput")
    now = _now(now)
    with store._tx():
        d = get(store, decision_id)
        if d["status"] != "executing":
            raise LedgerError(f"决定 {decision_id} 现在是「{STATUS[d['status']]}」，不能记结果（要先 begin）", "BadTransition")
        _set_status(store, d, result, by, now, outcome=_j({**d["outcome"], **(outcome or {})}))
    return get(store, decision_id)


def reconcile(store, decision_id, result, evidence, by="hermes", now=None):
    """只读对账之后记结论（7.6）：done（核实做成了）、failed（核实没做成，释放额度）、unreconciled（核不清，额度继续冻结）。
    executing（进程中途没了）也能对账。evidence 必须写：对账看了什么。"""
    if result not in ("done", "failed", "unreconciled"):
        raise LedgerError(f"对账结论只能是 done / failed / unreconciled：{result}", "BadInput")
    if not (evidence or "").strip():
        raise LedgerError("对账要写依据（看了什么）", "BadInput")
    now = _now(now)
    with store._tx():
        d = get(store, decision_id)
        if d["status"] not in ("executing", "unknown", "unreconciled"):
            raise LedgerError(f"决定 {decision_id} 现在是「{STATUS[d['status']]}」，不用对账", "BadTransition")
        if d["status"] == "unreconciled" and result == "unreconciled":
            _event(store, "reconcile", by, d["id"], {"to": "unreconciled", "evidence": evidence})
        else:
            if d["status"] == "executing":           # executing 没有直接到 done / failed 以外的路：先记成结果不明，再对账
                _set_status(store, d, "unknown", "system", now)
                d = get(store, decision_id)
            _set_status(store, d, result, by, now, outcome=_j({**d["outcome"], "reconcile": evidence}))
    return get(store, decision_id)


def void(store, decision_id, reason, by="system", now=None):
    """作废（只限 issued：还没开始送达的）：释放额度。送达闸以后见到它就不发。"""
    if not (reason or "").strip():
        raise LedgerError("作废要写原因", "BadInput")
    now = _now(now)
    with store._tx():
        d = get(store, decision_id)
        if d["status"] != "issued":
            raise LedgerError(f"决定 {decision_id} 现在是「{STATUS[d['status']]}」，只有已出未送达的才能作废", "BadTransition")
        _set_status(store, d, "void", by, now, void_reason=reason)
    return get(store, decision_id)


def drain(store, session_id, now=None):
    """排空（规格 §3.8 第 2、4、5 步）：先作废这次托管里全部已出未送达的决定、释放额度，再返回还没收完的事项（空 = 排空完成）。
    作废和查询在同一个写事务里：查完之前，出口闸记不进新决定、送达闸也开始不了。"""
    now = _now(now)
    with store._tx():
        for d in decisions(store, session_id):
            if d["status"] == "issued":
                _set_status(store, d, "void", "system", now, void_reason="托管排空")
        return drain_blockers(store, session_id)


def set_disposition(store, decision_id, to, note, by="user", now=None):
    """处置状态转换（规格 §3.6）。推翻（overturned）只能由常浩（by="user"，调用方先核实是常浩私聊里的点击）；
    已纠正只限回复，已回退 / 回退失败只限合并。note 必须写（推翻原因、纠正评论、revert PR、失败原因）。"""
    if to not in DISPOSITION:
        raise LedgerError(f"处置状态不认识：{to}", "BadInput")
    if not (note or "").strip():
        raise LedgerError("处置要写说明", "BadInput")
    now = _now(now)
    with store._tx():
        d = get(store, decision_id)
        if (d["disposition"], to) not in DISPOSITION_MOVES:
            raise LedgerError(f"决定 {decision_id} 的处置是「{DISPOSITION[d['disposition']]}」，不能变成「{DISPOSITION[to]}」",
                              "BadTransition")
        if to == "overturned":
            if by != "user":
                raise LedgerError("只有常浩能推翻托管决定", "NotOwner")
            if d["status"] in IN_FLIGHT:
                raise LedgerError(f"决定 {decision_id} 还在途（{STATUS[d['status']]}），等它有结果再推翻", "InFlight")
        if d["action"] not in DISPOSITION_ACTION.get(to, (d["action"],)):
            raise LedgerError(f"{ACTIONS[d['action']]}类决定不能记成「{DISPOSITION[to]}」", "BadTransition")
        store.conn.execute("UPDATE custody_decisions SET disposition=?, updated_at=? WHERE id=?", (to, C.iso(now), d["id"]))
        _event(store, f"disposition.{to}", by, d["id"], {"from": d["disposition"], "to": to, "note": note})
    return get(store, decision_id)


# ---------- 命令行 ----------
def done_line(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False, default=str))
    sys.exit(0 if ok else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("decision")
    sub.add_parser("list").add_argument("--session")
    sub.add_parser("drain-check").add_argument("--session", required=True)
    a = ap.parse_args(argv)
    try:
        with TS.Store(readonly=True) as st:
            if a.cmd == "show":
                d = get(st, a.decision)
                d["quota"] = quotas(st, a.decision)
                print(json.dumps(d, ensure_ascii=False, indent=2))
                done_line(True, f"{d['id']}：{ACTIONS[d['action']]}，{STATUS[d['status']]}，{DISPOSITION[d['disposition']]}")
            if a.cmd == "list":
                rows = decisions(st, a.session)
                for d in rows:
                    print(f"{d['id']}  {d['session_id']}  {d['project']}  {ACTIONS[d['action']]}  {describe_target(d)}  "
                          f"{STATUS[d['status']]}  {DISPOSITION[d['disposition']]}")
                done_line(True, f"共 {len(rows)} 个决定")
            if a.cmd == "drain-check":
                b = drain_blockers(st, a.session)
                done_line(not b, "排空已收完（drain 时还会作废已出未送达的）" if not b else "还没收完：" + "；".join(b), blockers=b)
    except (LedgerError, TS.StateError, sqlite3.Error) as e:
        done_line(False, str(e), error=getattr(e, "code", type(e).__name__))


if __name__ == "__main__":
    main()
