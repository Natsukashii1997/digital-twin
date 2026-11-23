#!/usr/bin/env python3
"""托管账本（#133）：每个托管决定的决定号、原文、额度预占、送达结果、处置状态，只有这里一处真源。

依据：托管模式规格 §3.5（上限与额度）、§3.6（汇报与撤回）、§3.8（关闭与排空）；ARCHITECTURE 5.7 第 8、9、10 条。
本脚本只记账、预占、证明排空，**不让托管生效**：不发评论、不合并、不发飞书（汇报卡和推翻在 custody_report.py）。

存哪里：
  twin-state 库（DT_STATE_DB，v4 起）的 custody_decisions（决定的当前状态）和 custody_quota（额度预占）；
  每次变化在同一个事务里记一条事件 custody.decision.<动作>（object_type = custody_decision），事件只追加不改。
  原文（收到的待决事项、发出的回复）存 DT_CUSTODY_DIR（默认 ~/Library/Application Support/DigitalTwin/custody）
  /<托管号>/<决定号>.json（目录 700、文件 600），保留 90 天：每次记新决定时顺手删掉托管号日期早于 90 天的目录。
  库文件不在就拒绝（LedgerMissing），不隐式新建（第 1 轮审查 P0-02）：账本丢了不能被当成「一个决定都没有」。
账本身份（P0-02）：第一次登记托管时生成 L-16 位十六进制，存库的 meta（custody_ledger_id）；每次开启托管在 custody_sessions 登记
  （托管号 → 账本身份），custody 把身份记进会话。准入、排空、汇报都核对「库还是那一份、这次托管登记过」，对不上抛 LedgerMismatch；
  原文目录里有、账本里没有的决定（库被旧备份覆盖后记的）算排空没收完。挂起（suspend）不要准入，但只能记进现在这次托管
  （custody.json 里的当前会话，排空、冻结中也算），并核对账本身份；已结束的托管不能再往里记（第 2 轮 P1-02）。

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

排空（custody.drain_proof、custody.ledger_cleanup、汇报卡发送前都调 drain）：给了账本身份就先核对；把这次托管里 issued 的决定
  全部作废、释放额度（规格 §3.8 第 2 步），再看还有没有 executing、unknown、unreconciled 的决定、没释放的预占、有原文没账本的决定；
  一项都没有才算排空完成。

写给常浩看的五项（汇报卡照抄，常浩 11-14 要求「看得懂到底做了什么」）：context 这件事在做什么（任务或 PR 的名字，≤40 字）、
  question agent 问了什么（带选项，≤80）、summary 我替你定的（单独能看懂，≤20）、basis 为什么（写对常浩的影响，≤100）、
  ask_owner 常浩要做什么（只在挂起时，≤60）；按动作必填，不许出现挂起、作废、HEAD、L1、D 档、决定号这类内部术语（JARGON），
  不合格就不记账（BadText）。

给后续批次的接口（#134 出口闸 / 送达闸、#136 合并执行器）：
  import custody_ledger as L; with L.open_store() as st:
    L.issue(st, session_id=, project=, action=, target=, context=, question=, summary=, basis=, ask_owner=, cites=, revocability=,
            expires_at=, received=, reply=)  出口闸记账 + 原子预占（拒绝抛 LedgerError，code 给机器判）
    project 必须是名册（已合并的 origin/main）里 sandbox: true 的项目名，原样匹配（P1-01：额度键只有一种写法）；
    target：回复 {"issue": 任务编号（Multica 规范写法，如 DTS-9）, "repo", "night": 夜跑号}（night 用来从夜跑记录读后来怎样）；
            合并 {"repo": 名册里的完整仓库名, "pr": 整数, "head": 40 位小写十六进制}
    L.begin(st, d) / L.finish(st, d, "done"|"failed"|"unknown", outcome) / L.reconcile(st, d, 结果, evidence) / L.void(st, d, 原因)
    合并 finish(done) 必须带 outcome.merge_commit（40 位，推翻时靠它开撤销 PR）；reconcile 的 evidence 是结构化字典
    （见 reconcile_errors：checked 必填；合并 pr_state + merge_commit，回复 comment_found + comment_id），合并核实做成时写进 outcome。
    issue / begin 拿到写锁之后才取时间（P2-01）。
  送达闸、合并执行器开始做之前必须调 begin（它现读托管准入、核有效期，过期就作废）；结果不明一律 finish(unknown)，不重发。
账本用途（#195 0B-4b）：秘书代批（secretary_ledger.py）复用这里的通用函数。和表名、事件前缀、对象类型、会话号格式、
  账本身份（meta 键）、原文目录、动作和状态的叫法有关的都收在 Use 里：托管是 CUSTODY（默认值，下面各函数不传 use 就是托管，
  行为、报错文字、事件名和原来一样），秘书是 secretary_ledger.SECRETARY（secretary_* 三张表、secretary.decision.*、s-… 会话号）。
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
import stat
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
# 写给常浩看的字（汇报卡上照抄）：要白话，不许内部术语（常浩 11-14：「看不懂到底做了什么，就没法判断」）
JARGON = re.compile(r"挂起|作废|排空|送达|子单|决定号|托管号|审核凭证|可纠正|可回退|不可撤回|UNRECONCILED|HEAD|\bL[0-3]\b|[A-D]\s?档|策略\s?v\d")
OWNER_TEXT = {  # 字段 → (中文名, 最长几个字, 哪些动作必填)
    "context": ("这件事在做什么（任务或 PR 的名字）", 40, ("reply", "merge", "suspend")),
    "question": ("agent 问了什么（带上选项）", 80, ("reply", "suspend")),
    "summary": ("我替你定的（一句能单独看懂的话，例：重试 3 次）", 20, ("reply", "merge")),
    "basis": ("为什么（写对常浩的影响，不只写规则名）", 100, ("reply", "merge", "suspend")),
    "ask_owner": ("常浩要做什么（例：回复我「同意改」或「不改」）", 60, ("suspend",)),
}
SESSION_ID = re.compile(r"^c-(\d{8})-\d{6}-[0-9a-f]{12}$")
ISSUE_ID = re.compile(r"^[A-Z][A-Z0-9]*-[1-9]\d*$")       # Multica 子单的规范写法（DTS-9）
SHA = re.compile(r"^[0-9a-f]{40}$")


class Use:
    """账本用途（#195 0B-4b）：托管和秘书共用一套账本函数（甲方案，常浩 11-18 定），各用各的表、事件、会话号、账本身份和原文目录。
    名字都从 table 推出来：<table>_decisions / _quota / _sessions，事件 <table>.decision.<动作>（对象类型 <table>_decision），
    会话登记事件 <table>.session.register（对象类型 <table>_session），账本身份存 meta 的 <table>_ledger_id。
    describe / reconcile_errors / status_reader 用 lambda 包着，调用时才找函数（测试打桩、两个模块互相导入都不受影响）。"""

    def __init__(self, *, table, who, sid_name, session_id, raw_env, raw_default, actions, status, disposition_action,
                 describe, reconcile_errors, status_reader):
        self.table = table
        self.decisions, self.quota, self.sessions = f"{table}_decisions", f"{table}_quota", f"{table}_sessions"
        self.event, self.obj = f"{table}.decision", f"{table}_decision"
        self.session_event, self.session_obj = f"{table}.session.register", f"{table}_session"
        self.meta_key = f"{table}_ledger_id"
        self.who = who                          # 报错里的叫法：托管 / 秘书
        self.sid_name = sid_name                # 会话号的叫法：托管号 / 秘书会话号
        self.session_id = session_id            # 会话号正则，第 1 组是日期 YYYYMMDD（原文 90 天清理按它）
        self.raw_env, self.raw_default = raw_env, raw_default
        self.actions, self.status = actions, status
        self.disposition_action = disposition_action
        self.describe, self.reconcile_errors, self.status_reader = describe, reconcile_errors, status_reader


CUSTODY = Use(table="custody", who="托管", sid_name="托管号", session_id=SESSION_ID, raw_env="DT_CUSTODY_DIR",
              raw_default=DEFAULT_DIR, actions=ACTIONS, status=STATUS, disposition_action=DISPOSITION_ACTION,
              describe=lambda d: describe_target(d), reconcile_errors=lambda d, r, e: reconcile_errors(d, r, e),
              status_reader=lambda t: C.read_status(now=t))


class LedgerError(Exception):
    """账本拒绝的操作。code 给机器判，str 给人看。拒绝时库和原文都不改。"""

    def __init__(self, msg, code="Rejected"):
        super().__init__(msg)
        self.code = code


# ---------- 路径、时间 ----------
def raw_dir(use=CUSTODY):
    return Path(os.environ.get(use.raw_env) or use.raw_default).expanduser()


def db_path(path=None):
    return Path(path or os.environ.get("DT_STATE_DB") or TS.DEFAULT_DB).expanduser()


def open_store(now=None, path=None, use=CUSTODY):
    """打开 twin-state 库（DT_STATE_DB）；给了 now 就用它当事件时间（测试、回放用）。
    库文件不存在就拒绝（LedgerMissing），不隐式新建（第 1 轮审查 P0-02）：账本丢了不能被当成「一个决定都没有」。
    twin-state 库本来就由 twin_state.py init 建；托管只用已有的库。"""
    p = db_path(path)
    if not p.is_file():
        raise LedgerError(f"{use.who}账本所在的状态库不见了：{p}（不自动新建；要人工从备份恢复或确认后用 twin_state.py init 建）",
                          "LedgerMissing")
    clock = (lambda: now) if now else None
    return TS.Store(p, clock=clock)


# ---------- 托管会话登记（P0-02）----------
def ledger_id(store, use=CUSTODY):
    """这个账本的身份：第一次登记托管时生成，存在库的 meta 里。库被换成别的（新建、旧备份前的）就对不上。"""
    r = store.conn.execute("SELECT value FROM meta WHERE key=?", (use.meta_key,)).fetchone()
    return r[0] if r else None


def register_session(store, session_id, now=None, use=CUSTODY):
    """托管开启时登记（custody.do_on 调）：返回账本身份，custody 把它记进会话（session.ledger.id）。重复登记同一会话不变。"""
    if not use.session_id.match(session_id or ""):
        raise LedgerError(f"{use.sid_name}不对：{session_id}", "BadInput")
    now = _now(now)
    with store._tx() as c:
        lid = ledger_id(store, use)
        if not lid:
            lid = "L-" + secrets.token_hex(8)
            c.execute("INSERT INTO meta VALUES (?, ?)", (use.meta_key, lid))
        c.execute(f"INSERT OR IGNORE INTO {use.sessions} VALUES (?,?,?)", (session_id, lid, C.iso(now)))
        store._record("system", use.session_event, "system", use.session_obj, session_id, {"ledger_id": lid})
    return lid


def check_session(store, session_id, expected_ledger, use=CUSTODY):
    """账本和这次托管对得上：账本身份等于开启时记下的、这次托管在账本里登记过。对不上抛 LedgerMismatch。"""
    lid = ledger_id(store, use)
    if not expected_ledger or lid != expected_ledger:
        raise LedgerError(f"账本身份对不上（{use.who}开启时记的是 {expected_ledger or '没有'}，现在的库是 {lid or '没有'}）："
                          "库可能被换成新建的或旧备份", "LedgerMismatch")
    if not store.conn.execute(f"SELECT 1 FROM {use.sessions} WHERE session_id=? AND ledger_id=?", (session_id, lid)).fetchone():
        raise LedgerError(f"账本里没有{use.who} {session_id} 的开启登记：库可能被换成开启之前的备份", "LedgerMismatch")


def raw_orphans(store, session_id, use=CUSTODY):
    """原文目录里有、账本里没有的决定（P0-02：库被旧备份覆盖后，之后记的决定会只剩原文）。"""
    d = raw_dir(use) / session_id
    # 不用 is_dir()：它把「不是目录」「失效链接」都说成 False，会被当成没有原文（0E-2 L3 第 3 轮 P1-04）。
    # 只有确实不存在（FileNotFoundError）才算没有；查不了、不是目录（含链接）、读不了一律报错
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        return []
    except OSError as e:
        raise LedgerError(f"{use.who}原文目录 {d} 查不了（{type(e).__name__}），核不了有没有账本里没有的决定", "RawUnreadable")
    if not stat.S_ISDIR(st.st_mode):
        raise LedgerError(f"{use.who}原文目录 {d} 不是目录（链接或文件），核不了有没有账本里没有的决定", "RawUnreadable")
    try:                                # glob 碰到读不了的目录会静默返回空，读不了必须报出来（0E-2 返修审查）
        names = os.listdir(d)
    except OSError as e:
        raise LedgerError(f"{use.who}原文目录 {d} 读不了（{type(e).__name__}），核不了有没有账本里没有的决定", "RawUnreadable")
    ids = {n[:-5] for n in names if n.endswith(".json") and DECISION_ID.match(n[:-5])}
    have = {r[0] for r in store.conn.execute(f"SELECT id FROM {use.decisions} WHERE session_id=?", (session_id,))}
    return sorted(ids - have)


def sandbox_projects():
    """可信的沙盒项目（P1-01）：名册（已合并的 origin/main，和夜跑主控同一份）里 sandbox: true 的仓库。返回 {项目名: 完整仓库名}。"""
    import multica_night as MN
    d = MN.committed_json(MN.ROSTER_FILE)
    out = {}
    for e in d.get("projects", []) + d.get("inventory", []):
        path = str((e or {}).get("path") or "")
        if isinstance(e, dict) and e.get("sandbox") is True and path.startswith("github:"):
            full = path[len("github:"):]
            out[full.split("/")[-1]] = full
    return out


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


def save_raw(session_id, decision_id, received, reply, now, use=CUSTODY):
    """原文另存（规格 §3.6）：收到的待决事项 + 发出的回复。先于记账写，记账失败时留下的孤儿文件 90 天后一起清。"""
    p = raw_dir(use) / session_id / f"{decision_id}.json"
    _write_private(p, json.dumps({"decision": decision_id, "session": session_id, "saved_at": C.iso(now),
                                  "received": received, "reply": reply}, ensure_ascii=False, indent=2) + "\n")
    return str(p)


def read_raw(decision):
    """读一个决定的原文；过了保留期或读不了返回 None。"""
    try:
        return json.loads(Path(decision["raw_path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def prune_raw(now, use=CUSTODY):
    """删掉托管号日期早于 90 天的原文目录（托管号 c-YYYYMMDD-…）。只删认得出的目录，别的不碰。返回删了哪些。"""
    root, gone = raw_dir(use), []
    if not root.is_dir():
        return gone
    cutoff = (now.astimezone(C.TZ) - dt.timedelta(days=RAW_KEEP_DAYS)).strftime("%Y%m%d")
    for d in root.iterdir():
        m = use.session_id.match(d.name)
        if m and d.is_dir() and not d.is_symlink() and m.group(1) < cutoff:
            shutil.rmtree(d)
            gone.append(d.name)
    return gone


# ---------- 读 ----------
def get(store, decision_id, use=CUSTODY):
    r = store.conn.execute(f"SELECT * FROM {use.decisions} WHERE id=?", (decision_id,)).fetchone()
    if not r:
        raise LedgerError(f"账本里没有决定 {decision_id}", "NotFound")
    return _row(r)


def decisions(store, session_id=None, use=CUSTODY):
    if session_id:
        rows = store.conn.execute(f"SELECT * FROM {use.decisions} WHERE session_id=? ORDER BY created_at, rowid", (session_id,))
    else:
        rows = store.conn.execute(f"SELECT * FROM {use.decisions} ORDER BY created_at, rowid")
    return [_row(r) for r in rows]


def quotas(store, decision_id, use=CUSTODY):
    return [dict(r) for r in store.conn.execute(f"SELECT * FROM {use.quota} WHERE decision_id=? ORDER BY id", (decision_id,))]


def used(store, kind, scope, use=CUSTODY):
    return store.conn.execute(f"SELECT COUNT(*) FROM {use.quota} WHERE kind=? AND scope=? AND state IN (?,?,?)",
                              (kind, scope, *QUOTA_HELD)).fetchone()[0]


def drain_blockers(store, session_id, use=CUSTODY):
    """只读：这次托管还有什么没收完（不作废任何东西）。issued 也算——排空时 drain 会先作废它们。"""
    out = []
    for d in decisions(store, session_id, use):
        if d["status"] in IN_FLIGHT + UNSETTLED:
            out.append(f"{d['id']}（{use.actions[d['action']]} {use.describe(d)}）{use.status[d['status']]}")
    held = store.conn.execute(f"SELECT q.decision_id, q.kind, q.state FROM {use.quota} q JOIN {use.decisions} d"
                              " ON d.id = q.decision_id WHERE d.session_id=? AND q.state IN ('reserved','frozen')"
                              " AND d.status NOT IN (?,?,?,?,?)", (session_id, *IN_FLIGHT, *UNSETTLED)).fetchall()
    for q in held:                      # 状态已收口、额度却没跟着收口：账本自相矛盾，按没收完处理
        out.append(f"{q['decision_id']} 的 {q['kind']} 额度还是 {q['state']}（和决定状态对不上）")
    for x in raw_orphans(store, session_id, use):
        out.append(f"{x} 有原文、账本里却没有（账本可能被旧备份覆盖）")
    return out


def describe_target(d):
    t = d["target"] or {}
    if d["action"] == "merge" or t.get("pr"):
        return f"{t.get('repo', '?')}#{t.get('pr', '?')}" + (f"@{str(t['head'])[:7]}" if t.get("head") else "")
    return str(t.get("issue") or t.get("repo") or "?")


# ---------- 写 ----------
def _event(store, kind, by, decision_id, payload, use=CUSTODY):
    store._record(store._source(by), f"{use.event}.{kind}", by, use.obj, decision_id, payload)


def _set_status(store, d, to, by, now, *, use=CUSTODY, **fields):
    if to not in STATUS_MOVES.get(d["status"], ()):
        raise LedgerError(f"决定 {d['id']} 现在是「{use.status[d['status']]}」，不能变成「{use.status[to]}」", "BadTransition")
    sets = {"status": to, "updated_at": C.iso(now), **fields}
    store.conn.execute(f"UPDATE {use.decisions} SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?", (*sets.values(), d["id"]))
    q = QUOTA_OF_STATUS[to]
    store.conn.execute(f"UPDATE {use.quota} SET state=?, updated_at=? WHERE decision_id=? AND state != 'released'",
                       (q, C.iso(now), d["id"]))
    _event(store, to, by, d["id"], {"from": d["status"], "to": to, "quota": q,
                                    **{k: (json.loads(v) if k == "outcome" else v) for k, v in fields.items()}}, use)


def _admitted(store, session_id, now, status_reader, use=CUSTODY):
    """托管这一层的准入（custody.admission）+ 会话对得上 + 账本是开启时登记的那一份；不过就抛 LedgerError。返回当前有效策略。"""
    st = status_reader(now)
    if not st["admission"]["eligible"]:
        raise LedgerError(f"{use.who}不准入：" + "；".join(st["admission"]["reasons"]), "NotAdmitted")
    sess = st.get("session") or {}
    if sess.get("id") != session_id:
        raise LedgerError(f"{use.who}会话对不上：现在开着的是 {sess.get('id')}，这个决定属于 {session_id}", "WrongSession")
    check_session(store, session_id, (sess.get("ledger") or {}).get("id"), use)
    return st["policy"]


def current_session(store, session_id, now, status_reader, use=CUSTODY):
    """不要准入的记账（托管的挂起、秘书的交常浩）也只能记进现在这次（开着、排空、冻结中都算），并核对账本身份（第 2 轮 P1-02）。
    返回现在这次会话（秘书的交常浩要取会话绑定的清单版本）。"""
    sess = (status_reader(now).get("session") or {})
    if sess.get("id") != session_id:
        raise LedgerError(f"{use.who} {session_id} 不是现在这次（已结束或没开），不能再往里记", "WrongSession")
    check_session(store, session_id, (sess.get("ledger") or {}).get("id"), use)
    return sess


def new_decision_id(now):
    return "d-" + now.astimezone(C.TZ).strftime("%y%m%d") + "-" + secrets.token_hex(3)


def record(store, *, use, session_id, row, payload, cites, exp, fixed_now, now, received, reply, by, gate):
    """记账的通用骨架（托管 issue、秘书 issue 共用）：生成决定号 → 先存原文 → 一个 BEGIN IMMEDIATE 事务里核依据、
    拿到写锁后才取时间（P2-01）、核有效期、gate(now) 做准入和额度检查（返回 (状态, 额度 [(kind, scope, cap, 说明)], 补充列)，
    只数不写；补充列是只有准入时才知道的值，例如秘书决定依据的清单版本，同时写进决定行和事件）→ 写决定行、额度行、事件。
    任何一步抛异常，库里什么都不写、原文删掉。
    row 是决定行除 id / session_id / status / disposition / expires_at / raw_path / 时间以外的列；payload 是事件里除
    status / quota 以外的字段。返回决定（dict）。"""
    prune_raw(now, use)
    for _ in range(5):
        did = new_decision_id(now)
        if not store.conn.execute(f"SELECT 1 FROM {use.decisions} WHERE id=?", (did,)).fetchone():
            break
    else:
        raise LedgerError("生成不出不重复的决定号（随机源异常）", "Corrupt")
    raw_path = save_raw(session_id, did, received, reply, now, use)
    try:
        with store._tx() as c:
            missing = [x for x in cites if not c.execute(f"SELECT 1 FROM {use.decisions} WHERE id=?", (x,)).fetchone()]
            if missing:
                raise LedgerError(f"依据引用的决定不在账本里：{'、'.join(missing)}", "BadInput")
            now = fixed_now or C.now_tz()                    # 拿到写锁之后再取时间（P2-01）
            if exp and exp <= now:
                raise LedgerError("有效期已经过了（拿到账本写锁时）", "BadInput")
            status, holds, extra = gate(now)
            ts = C.iso(now)
            full = {"id": did, "session_id": session_id, **row, **extra, "status": status, "disposition": "open",
                    "expires_at": C.iso(exp) if exp else None, "raw_path": raw_path, "created_at": ts, "updated_at": ts}
            c.execute(f"INSERT INTO {use.decisions} ({', '.join(full)}) VALUES ({','.join('?' * len(full))})", tuple(full.values()))
            for kind, scope, _, _ in holds:
                c.execute(f"INSERT INTO {use.quota} (session_id, kind, scope, decision_id, state, created_at, updated_at)"
                          " VALUES (?,?,?,?, 'reserved', ?, ?)", (session_id, kind, scope, did, ts, ts))
            _event(store, "issue", by, did, {**payload, **extra, "status": status, "quota": [[k, s] for k, s, _, _ in holds]}, use)
    except BaseException:
        with contextlib.suppress(OSError):
            Path(raw_path).unlink()
        raise
    return get(store, did, use)


def check_holds(store, holds, use=CUSTODY):
    """额度检查（在 record 的写事务里调）：每项 占用 = reserved + used + frozen，到上限就拒（QuotaExhausted）。"""
    for kind, scope, cap, what in holds:
        n = used(store, kind, scope, use)
        if n >= cap:
            raise LedgerError(f"{what}已用完（{n}/{cap}）", "QuotaExhausted")


def check_owner_text(action, fields):
    """写给常浩看的五项：按动作必填、限长、不许内部术语。返回错误列表（空 = 通过）。"""
    errors = []
    for key, (name, limit, need) in OWNER_TEXT.items():
        text = (fields.get(key) or "").strip()
        if action in need and not text:
            errors.append(f"缺「{name}」")
        elif len(text) > limit:
            errors.append(f"「{name}」超过 {limit} 个字")
        hit = JARGON.search(text)
        if hit:
            errors.append(f"「{name}」里有内部术语「{hit.group(0)}」，要换成常浩看得懂的话")
    return errors


def issue(store, *, session_id, project, action, target, basis, revocability, context="", question="", summary="",
          ask_owner="", cites=(), expires_at=None, received=None, reply=None, by="hermes", now=None, status_reader=None):
    """出口闸记账（规格 §2 第 ④ 步）：决定号、原文、额度原子预占。reply / merge 要托管准入、策略允许、额度够；suspend 只记账。
    返回决定（dict）。任何一项不满足抛 LedgerError，库里什么都不写。"""
    fixed_now = now
    now = _now(now)
    status_reader = status_reader or (lambda t: C.read_status(now=t))
    if action not in ACTIONS:
        raise LedgerError(f"动作类型只能是 {'/'.join(ACTIONS)}：{action}", "BadInput")
    if revocability not in ALLOWED_REVOCABILITY[action]:
        raise LedgerError(f"{ACTIONS[action]}的撤回能力不能是「{REVOCABILITY.get(revocability, revocability)}」"
                          "（撤不回的动作一律挂起）", "BadInput")
    if not SESSION_ID.match(session_id or ""):
        raise LedgerError(f"托管号不对：{session_id}", "BadInput")
    repos = sandbox_projects()
    if project not in repos:
        raise LedgerError(f"项目「{project}」不是名册里的沙盒项目（{'、'.join(repos) or '名册里没有'}），托管只管沙盒", "BadInput")
    bad = check_owner_text(action, {"context": context, "question": question, "summary": summary, "basis": basis,
                                    "ask_owner": ask_owner})
    if bad:
        raise LedgerError("写给常浩的字不合格：" + "；".join(bad), "BadText")
    if not isinstance(target, dict):
        raise LedgerError("对象（target）要是一个字典", "BadInput")
    issue_key = None
    if action in ("reply", "suspend") and target.get("issue") is not None and not ISSUE_ID.match(str(target["issue"])):
        raise LedgerError(f"子单编号要是 Multica 的规范写法（如 DTS-9）：{target['issue']!r}", "BadInput")
    if action == "reply":
        if not target.get("issue"):
            raise LedgerError("回复要写子单（target.issue）", "BadInput")
        issue_key = f"{project}:{target['issue']}"           # 项目名来自名册、编号是规范写法：同一张子单只有一种写法（P1-01）
    if action == "merge":
        if target.get("repo") != repos[project] or not isinstance(target.get("pr"), int) or not SHA.match(str(target.get("head"))):
            raise LedgerError(f"合并要写完整仓库名（{repos[project]}）、PR 号（整数）、完整 HEAD（40 位）", "BadInput")
    exp = None
    if action != "suspend":
        exp = C.parse_iso(expires_at) if isinstance(expires_at, str) else expires_at
        if not isinstance(exp, dt.datetime) or exp.tzinfo is None:
            raise LedgerError("回复、合并要写将来的有效期（带时区）", "BadInput")
    cites = list(cites or ())
    if any(not DECISION_ID.match(str(c)) for c in cites):
        raise LedgerError(f"依据只能引用决定号：{cites}", "BadInput")

    def gate(t):
        if action == "suspend":              # 不要准入（排空、冻结时也能记），但只能记进现在这次托管，并核对账本身份（第 2 轮 P1-02）
            current_session(store, session_id, t, status_reader)
            return "suspended", [], {}
        pol = _admitted(store, session_id, t, status_reader)
        lim = (pol.get("limits") or {}).get(action)
        if not lim:
            raise LedgerError("有效策略里读不出额度上限，不记账", "NoLimits")
        if not lim["allowed"]:
            raise LedgerError(f"策略没允许托管{ACTIONS[action]}", "NotAllowed")
        holds = [(action, session_id, lim["per_session"], f"这次托管的{ACTIONS[action]}额度")]
        if action == "reply":
            holds.append(("issue", issue_key, lim["per_issue"], f"子单 {target['issue']} 的托管次数"))
        check_holds(store, holds)
        return "issued", holds, {}

    row = {"project": project, "action": action, "target": _j(target), "issue_key": issue_key, "context": context.strip(),
           "question": question.strip(), "summary": summary.strip(), "ask_owner": ask_owner.strip(), "basis": basis.strip(),
           "cites": _j(cites), "revocability": revocability}
    payload = {"session": session_id, "project": project, "action": action, "target": target, "revocability": revocability,
               "cites": cites, "expires_at": C.iso(exp) if exp else None}
    return record(store, use=CUSTODY, session_id=session_id, row=row, payload=payload, cites=cites, exp=exp,
                  fixed_now=fixed_now, now=now, received=received, reply=reply, by=by, gate=gate)


def begin(store, decision_id, by="hermes", now=None, status_reader=None, use=CUSTODY):
    """送达闸、合并执行器动手之前调：现读托管准入、核有效期，issued → executing。过期就作废并释放额度，再抛 LedgerError。"""
    fixed_now = now
    status_reader = status_reader or use.status_reader
    expired = None
    with store._tx():
        now = fixed_now or C.now_tz()                        # 拿到写锁之后再取时间（P2-01）
        d = get(store, decision_id, use)
        if d["status"] != "issued":
            raise LedgerError(f"决定 {decision_id} 现在是「{use.status[d['status']]}」，不能开始送达", "BadTransition")
        if now >= C.parse_iso(d["expires_at"]):
            _set_status(store, d, "void", "system", now, use=use, void_reason="过期")
            expired = d["expires_at"]
        else:
            _admitted(store, d["session_id"], now, status_reader, use)
            _set_status(store, d, "executing", by, now, use=use)
    if expired:
        raise LedgerError(f"决定 {decision_id} 已于 {expired} 过期，已作废、额度已释放", "Expired")
    return get(store, decision_id, use)


def finish(store, decision_id, result, outcome=None, by="hermes", now=None, use=CUSTODY):
    """送达 / 合并的结果：done（确认做成，额度计为已用）、failed（确认没做成，释放）、unknown（结果不明，额度冻结、不重发）。"""
    if result not in ("done", "failed", "unknown"):
        raise LedgerError(f"结果只能是 done / failed / unknown：{result}", "BadInput")
    now = _now(now)
    with store._tx():
        d = get(store, decision_id, use)
        if d["status"] != "executing":
            raise LedgerError(f"决定 {decision_id} 现在是「{use.status[d['status']]}」，不能记结果（要先 begin）", "BadTransition")
        if d["action"] == "merge" and result == "done" and not SHA.match(str((outcome or {}).get("merge_commit"))):
            raise LedgerError("合并做成要带完整的合并提交号（outcome.merge_commit，40 位）：推翻时要靠它开撤销 PR", "BadInput")
        _set_status(store, d, result, by, now, use=use, outcome=_j({**d["outcome"], **(outcome or {})}))
    return get(store, decision_id, use)


def reconcile_errors(d, result, evidence):
    """对账证据要结构化（P1-02、P2-02）：由 #134 / #136 的只读核对填，不是模型自由写结论。
    共同：checked（看了什么，非空）。合并：pr_state（MERGED / OPEN / CLOSED）；done 要 MERGED + merge_commit（40 位）；
    failed 要 OPEN 或 CLOSED。回复：comment_found（真 / 假）；done 要真并带 comment_id；failed 要假。unreconciled 只要 checked。"""
    if not isinstance(evidence, dict) or not str(evidence.get("checked") or "").strip():
        return ["对账证据要是字典，并写 checked（看了什么）"]
    if result == "unreconciled":
        return []
    if d["action"] == "merge":
        st = evidence.get("pr_state")
        if result == "done" and not (st == "MERGED" and SHA.match(str(evidence.get("merge_commit")))):
            return ["合并核实做成要 pr_state=MERGED 并带完整 merge_commit"]
        if result == "failed" and st not in ("OPEN", "CLOSED"):
            return ["合并核实没做成要 pr_state=OPEN 或 CLOSED"]
        return []
    found = evidence.get("comment_found")
    if result == "done" and not (found is True and evidence.get("comment_id")):
        return ["回复核实送到要 comment_found=true 并带 comment_id"]
    if result == "failed" and found is not False:
        return ["回复核实没送到要 comment_found=false"]
    return []


def reconcile(store, decision_id, result, evidence, by="hermes", now=None, use=CUSTODY):
    """只读对账之后记结论（7.6）：done（核实做成了）、failed（核实没做成，释放额度）、unreconciled（核不清，额度继续冻结）。
    executing（进程中途没了）也能对账。evidence 见 reconcile_errors；合并核实做成时把 merge_commit 写进 outcome。"""
    if result not in ("done", "failed", "unreconciled"):
        raise LedgerError(f"对账结论只能是 done / failed / unreconciled：{result}", "BadInput")
    now = _now(now)
    with store._tx():
        d = get(store, decision_id, use)
        if d["status"] not in ("executing", "unknown", "unreconciled"):
            raise LedgerError(f"决定 {decision_id} 现在是「{use.status[d['status']]}」，不用对账", "BadTransition")
        bad = use.reconcile_errors(d, result, evidence)
        if bad:
            raise LedgerError("对账证据不够：" + "；".join(bad), "BadEvidence")
        if d["status"] == "unreconciled" and result == "unreconciled":
            _event(store, "reconcile", by, d["id"], {"to": "unreconciled", "evidence": evidence}, use)
        else:
            if d["status"] == "executing":           # executing 没有直接到 done / failed 以外的路：先记成结果不明，再对账
                _set_status(store, d, "unknown", "system", now, use=use)
                d = get(store, decision_id, use)
            out = {**d["outcome"], "reconcile": evidence}
            if d["action"] == "merge" and result == "done":
                out["merge_commit"] = evidence["merge_commit"]
            if d["action"] == "reply" and result == "done":
                out["comment_id"] = evidence["comment_id"]
            _set_status(store, d, result, by, now, use=use, outcome=_j(out))
    return get(store, decision_id, use)


def void(store, decision_id, reason, by="system", now=None, use=CUSTODY):
    """作废（只限 issued：还没开始送达的）：释放额度。送达闸以后见到它就不发。"""
    if not (reason or "").strip():
        raise LedgerError("作废要写原因", "BadInput")
    now = _now(now)
    with store._tx():
        d = get(store, decision_id, use)
        if d["status"] != "issued":
            raise LedgerError(f"决定 {decision_id} 现在是「{use.status[d['status']]}」，只有已出未送达的才能作废", "BadTransition")
        _set_status(store, d, "void", by, now, use=use, void_reason=reason)
    return get(store, decision_id, use)


def drain(store, session_id, ledger=None, now=None, use=CUSTODY):
    """排空（规格 §3.8 第 2、4、5 步）：先作废这次托管里全部已出未送达的决定、释放额度，再返回还没收完的事项（空 = 排空完成）。
    作废和查询在同一个写事务里：查完之前，出口闸记不进新决定、送达闸也开始不了。
    给了 ledger（托管开启时记下的账本身份）就先核对账本是不是那一份（P0-02），对不上抛 LedgerMismatch；custody.drain_proof 必须给。"""
    now = _now(now)
    with store._tx():
        if ledger is not None:
            check_session(store, session_id, ledger, use)
        for d in decisions(store, session_id, use):
            if d["status"] == "issued":
                _set_status(store, d, "void", "system", now, use=use, void_reason=f"{use.who}排空")
        return drain_blockers(store, session_id, use)


def set_disposition(store, decision_id, to, note, by="user", now=None, use=CUSTODY):
    """处置状态转换（规格 §3.6）。推翻（overturned）只能由常浩（by="user"，调用方先核实是常浩私聊里的点击）；
    已纠正只限回复，已回退 / 回退失败只限合并。note 必须写（推翻原因、纠正评论、revert PR、失败原因）。"""
    if to not in DISPOSITION:
        raise LedgerError(f"处置状态不认识：{to}", "BadInput")
    if not (note or "").strip():
        raise LedgerError("处置要写说明", "BadInput")
    now = _now(now)
    with store._tx():
        d = get(store, decision_id, use)
        if (d["disposition"], to) not in DISPOSITION_MOVES:
            raise LedgerError(f"决定 {decision_id} 的处置是「{DISPOSITION[d['disposition']]}」，不能变成「{DISPOSITION[to]}」",
                              "BadTransition")
        if to == "overturned":
            if by != "user":
                raise LedgerError(f"只有常浩能推翻{use.who}决定", "NotOwner")
            if d["status"] in IN_FLIGHT:
                raise LedgerError(f"决定 {decision_id} 还在途（{use.status[d['status']]}），等它有结果再推翻", "InFlight")
        if d["action"] not in use.disposition_action.get(to, (d["action"],)):
            raise LedgerError(f"{use.actions[d['action']]}类决定不能记成「{DISPOSITION[to]}」", "BadTransition")
        store.conn.execute(f"UPDATE {use.decisions} SET disposition=?, updated_at=? WHERE id=?", (to, C.iso(now), d["id"]))
        _event(store, f"disposition.{to}", by, d["id"], {"from": d["disposition"], "to": to, "note": note}, use)
    return get(store, decision_id, use)


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
