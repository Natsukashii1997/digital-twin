"""持久调度（B2.2）：调度决策、调度预留、开单关联与恢复。只用标准库；状态都在 B1 状态库（twin_state.py），不另建库。

  python3 twin_scheduler.py plan [--db F] [--dispatch D]
      只读决策：每个未收尾的任务给出 decision / reason / evidence / next_action，不改任何状态（只读打开库，库不存在不建）。
  python3 twin_scheduler.py tick [--execute --mode read|write] [--limit N]
      不带 --execute 等于 plan；带 --execute 还要环境变量 DT_SCHED_ENABLE=1（正式环境未启用，默认关闭）：
      预留 → dispatch.py new --sched-key 开单 → 按关联键核对台账并登记成任务的当前 Run。只开单，不开跑（开跑仍走确认门）。
  python3 twin_scheduler.py recover
      跨会话 / 重启恢复：按关联键核对每个未结预留，能证明就关联，证明不了就 unsafe，确认没开出单才 void；
      另列游离单（带关联键、对应预留却已收口的单），有游离单或 unsafe 时退出码 1。
  python3 twin_scheduler.py observe
      只读：活动 Run 与台账 running 单的生命体征 alive / suspect / lost / finished / not_started / unknown。
  python3 twin_scheduler.py cancel
      已撤销任务上还在活动的 Run：逐个给处理建议并记事件。命令行从不发信号（真实路径只出建议）。
  python3 twin_scheduler.py report [--quiet]
      低噪声报告：只报错误和要常浩决定的、还没确认送达的情形；--quiet 时没有待报就什么都不输出。只读，不标记：
      输出不等于送达，投递端确认送达后调 ack，确认前每轮都会再报（宁可重复不漏报）。
  python3 twin_scheduler.py ack --fingerprint FP [FP ...]
      投递端确认送达后标记；幂等；不在当前情形里的指纹不标记并返回失败。

崩溃窗口与补偿（不声称跨状态库与工单的 exactly-once，靠关联键对账收口）：
  ① 预留前崩溃：什么都没写，下一轮照常决策。
  ② 预留后、开单前（或开单中途）崩溃：预留留着占名额；恢复时台账按关联键找不到单、租约已过，并且能证明没开单
     （从没走到拉开单子进程那一步，或登记过的开单子进程已确认退出）→ void；走到了那一步却没登记子进程 → unsafe；
     有 sched_key 文件却没有 order.json 的半截目录 → unsafe。dispatch new 在派单锁里只给仍是 claimed 的预留开单，
     作废也在这把锁里，所以迟到的旧关联键开不出单；万一出现游离单，决策判 unsafe、报告报错误。
  ③ 开单后、关联前崩溃：恢复时按关联键找到恰好一张活动单 → 记单号并关联；多张、已不在活动状态 → unsafe。
  ④ 关联后崩溃：Run 已是任务的当前 Run，“一任务一活动 Run”阻止重派，预留已 linked。

决策（decision）：
  dispatch  可以派：目标已授权、任务 ready、依赖都完成、没有进行中的 Run、没有未结预留、台账可读、并发名额有空
  hold      条件满足但现在不能派（名额满、台账读不到、另一个调度器正在开单），下一轮再看
  skip      按规则不该派（未授权、依赖没完成、阻塞、待验收、正在跑等），等对应的人或事件
  unsafe    状态矛盾或归属证明不了，要常浩处理；调度器不碰
顺序：按任务创建先后（先建先派）；调度器不自己重排常浩的目标，名额不够时后面的任务给 hold 并写明原因。
并发：总共最多 MAX_RUNNING 张、同一项目最多 1 张，名额 = 未结预留 + 已关联任务的活动 Run + 台账里在跑的单（三者按单号去重）。
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import twin_state as ts  # noqa: E402  同目录（安装副本在 ~/.hermes/scripts/）

MAX_RUNNING = 2            # 与 dispatch.py 一致：总并发 2、同一仓库 1
LOCK_NAME = ".dispatch.lock"   # 与 dispatch.py 同一把派单根目录文件锁
PS_ENV = {**os.environ, "LC_ALL": "C"}   # ps 输出固定成 C 语言环境，和 run_worker 记下的启动时间逐字可比
TOOLS = ("claude", "codex")
DECISIONS = ("dispatch", "hold", "skip", "unsafe")
REASONS = {
    "eligible": "满足全部派单条件",
    "goal_not_authorized": "目标还没有常浩授权（或已收尾）",
    "task_not_ready": "任务还在 observed，没有进入 ready",
    "deps_unmet": "依赖任务还没完成",
    "blocked_decision": "等常浩决定",
    "blocked_external": "等外部条件恢复",
    "blocked_technical": "技术失败后阻塞，失败原因不明或进程没确认退出，不出重试建议",
    "retry_suggested": "已知可恢复的技术失败，没到上限，执行进程已退出：可以建议重试",
    "retry_exhausted": "技术重试已到上限",
    "retry_repeated": "同一种失败连续出现，重试大概率无效",
    "in_review": "待验收：执行完成不等于验收通过",
    "running": "已有进行中的 Run",
    "claim_open": "另一个调度预留正在开单",
    "claim_unsafe": "调度预留归属证明不了，要常浩收口",
    "queue_unsafe": "状态矛盾（见 queue 的 unsafe 桶）",
    "orphan_order": "有带本任务关联键的游离单（预留已收口却开出了单），归属与是否会被执行都说不清",
    "retry_over_limit": "重试次数超过上限",
    "no_executor": "任务负责方不是车间（claude / codex），调度器不派",
    "no_project": "目标没写项目，派不了单",
    "ledger_unavailable": "派单台账读不到，不能确认并发和已有工单",
    "slot_full_global": "总并发名额已满",
    "slot_full_project": "同一项目已有单占着名额",
}
NEXT = {
    "eligible": "预留后开单（tick --execute；正式环境未启用）",
    "goal_not_authorized": "等常浩授权目标",
    "task_not_ready": "目标授权后 set_ready",
    "deps_unmet": "等依赖完成，recover 会自动放行",
    "blocked_decision": "等常浩在飞书私聊里决定",
    "blocked_external": "外部来源恢复后带依据 unblock",
    "blocked_technical": "人工看失败原因（回执、日志）后再定",
    "retry_suggested": "建议技术重试（retry_task），不早于 evidence.not_before；正式环境不自动执行",
    "retry_exhausted": "常浩决定是否继续",
    "retry_repeated": "常浩看失败原因后决定",
    "in_review": "核对回执、测试与 PR，再由非执行方记验收结论",
    "running": "等车间回执；生命体征见 observe",
    "claim_open": "等该预留开单或过期后由恢复核对",
    "claim_unsafe": "常浩核对该预留和工单后手动收口",
    "queue_unsafe": "人工核对状态矛盾",
    "orphan_order": "常浩核对游离单：要用就人工关联，不用就 dispatch.py clean 放弃；调度器不自动处理",
    "retry_over_limit": "常浩决定是否继续",
    "no_executor": "需要车间执行时把负责方改成 claude 或 codex",
    "no_project": "给目标补上项目（名册 id）",
    "ledger_unavailable": "先恢复派单台账可读，下一轮再看",
    "slot_full_global": "等在跑的单结束，下一轮再看",
    "slot_full_project": "等该项目在跑的单结束，下一轮再看",
}
CATEGORY = {"eligible": "dispatch", "claim_open": "hold", "ledger_unavailable": "hold", "slot_full_global": "hold",
            "slot_full_project": "hold", "claim_unsafe": "unsafe", "queue_unsafe": "unsafe", "retry_over_limit": "unsafe",
            "orphan_order": "unsafe"}


def _decision(t, reason, **evidence):
    return {"task_id": t["id"], "goal_id": t["goal_id"], "title": t["title"], "decision": CATEGORY.get(reason, "skip"),
            "reason": reason, "why": REASONS[reason], "evidence": evidence, "next_action": NEXT[reason]}


def _project_of(store, run, goals, tasks):
    t = tasks.get(run["task_id"])
    g = goals.get(t["goal_id"]) if t else None
    return (g or {}).get("project") or (run.get("evidence") or {}).get("project")


def occupied(store, ledger):
    """当前占着并发名额的东西：[{key, project, kind}]。未结预留、已关联任务的活动 Run、台账里 running 的单，按单号去重。"""
    goals = {g["id"]: g for g in store.list_goals()}
    tasks = {t["id"]: t for t in store.list_tasks()}
    out, orders = [], set()
    for c in store.claims(states=ts.CLAIM_OPEN):
        out.append({"key": c["sched_key"], "project": c["project"], "kind": f"claim:{c['state']}"})
        if c["order_no"]:
            orders.add(c["order_no"])
    for r in store.list_runs():
        if r["task_id"] and r["status"] in ts.RUN_ACTIVE and r["order_no"] not in orders:
            out.append({"key": r["order_no"] or r["id"], "project": _project_of(store, r, goals, tasks), "kind": f"run:{r['status']}"})
            if r["order_no"]:
                orders.add(r["order_no"])
    for o in ledger.get("orders", []):
        if o.get("status") == "running" and o.get("no") not in orders:
            out.append({"key": o["no"], "project": o.get("project"), "kind": "order:running"})
            orders.add(o["no"])
    return out


def decide(store, ledger=None, max_running=MAX_RUNNING):
    """纯函数：读状态库与台账，给每个未收尾的任务一条决策；不写库、不写台账、不调外部命令。
    同一状态重复评估结果相同（不含当前时间，按任务创建顺序）。返回 {ledger, decisions, occupied, counts}。"""
    ledger = ledger or store.read_orders()
    q = store.queue(ledger=ledger)
    unsafe = {}
    for u in q["unsafe"]:
        tid = u.get("task_id") if u.get("task_id") else u["id"]
        unsafe.setdefault(tid, u["why"])
    goals = {g["id"]: g for g in store.list_goals()}
    open_claims = {c["task_id"]: c for c in store.claims(states=ts.CLAIM_OPEN)}
    orphans = {}
    for o in orphan_orders(store, ledger):
        if o["task_id"]:
            orphans.setdefault(o["task_id"], []).append(o)
    busy = occupied(store, ledger)
    decisions = []
    for t in store.list_tasks():
        if t["status"] in ts.TASK_TERMINAL:
            continue
        g = goals[t["goal_id"]]
        cl = open_claims.get(t["id"])
        cur = store.current_run(t["id"])
        run_ev = {"run": cur["id"], "order_no": cur["order_no"], "run_status": cur["status"]} if cur else {}
        if g["status"] != "authorized":
            d = _decision(t, "goal_not_authorized", goal=g["id"], goal_status=g["status"])
        elif t["status"] == "observed":
            d = _decision(t, "task_not_ready", goal=g["id"])
        elif cl:
            d = _decision(t, "claim_unsafe" if cl["state"] == "unsafe" else "claim_open", sched_key=cl["sched_key"],
                          claim_state=cl["state"], order_no=cl["order_no"], lease_until=cl["lease_until"], note=cl["note"])
        elif t["id"] in orphans:
            d = _decision(t, "orphan_order", orphans=orphans[t["id"]], **run_ev)
        elif t["id"] in unsafe:
            d = _decision(t, "queue_unsafe", unsafe=unsafe[t["id"]], **run_ev)
        elif t["status"] == "running":
            d = _decision(t, "running", **run_ev)
        elif t["status"] == "review":
            d = _decision(t, "in_review", verdict=t["verdict"], **run_ev)
        elif t["status"] == "blocked":
            kind = t["blocked_kind"]
            reason = {"decision": "blocked_decision", "external": "blocked_external", "dependency": "deps_unmet"}.get(
                kind, "blocked_technical")
            ev = {"blocked_kind": kind, "blocked_reason": t["blocked_reason"]}
            if kind == "dependency":
                ev["depends_on"] = store._unmet(t)
            if reason == "blocked_technical":
                reason, extra = retry_advice(store, t, cur, ledger)
                ev.update(retries=t["retries"], max_retries=store.max_retries, **extra)
            d = _decision(t, reason, **ev, **run_ev)
        else:   # ready
            d = _ready_decision(store, t, g, ledger, busy, max_running)
            if d["decision"] == "dispatch":    # 同一轮里后面的任务要看到这个名额已被占
                busy.append({"key": f"plan:{t['id']}", "project": g["project"], "kind": "plan"})
        decisions.append(d)
    counts = {k: sum(1 for d in decisions if d["decision"] == k) for k in DECISIONS}
    return {"ledger": ledger["status"], "decisions": decisions, "occupied": occupied(store, ledger), "counts": counts}


RECOVERABLE = ("timeout", "worker_failed")
RETRY_BACKOFF = 600        # 秒：重试建议的退避基数，第 n 次重试不早于上次结束 + 基数 × 2^n


def failure_class(run):
    """失败分级：timeout / worker_failed 是已知可恢复的技术失败；lost（过期，进程状态不明）、回执不完整等一律 unknown。"""
    if not run:
        return "unknown"
    if run["status"] == "stale":
        return "lost"
    rc = run["receipt"] or {}
    if run["status"] != "failed" or any(k not in rc for k in ts.RECEIPT_REQUIRED):
        return "unknown"
    if rc.get("timed_out"):
        return "timeout"
    if rc.get("worker_error") or rc.get("exit_code") != 0:
        return "worker_failed"
    return "unknown"


def retry_advice(store, t, cur, ledger):
    """S5：blocked(technical) 的任务能不能出重试建议。只出建议，不执行；不从 decision 阻塞推断放权（那类不走这里）。"""
    cls = failure_class(cur)
    ev = {"failure": cls}
    if t["retries"] >= store.max_retries:
        return "retry_exhausted", ev
    if cls not in RECOVERABLE:
        return "blocked_technical", ev
    o = next((x for x in ledger.get("orders", []) if cur and x.get("no") == cur["order_no"]), None)
    if ledger.get("status") != "ok" or (cur["order_no"] and (o is None or o.get("status") in ts.RUN_ACTIVE)):
        ev["ledger_status"] = o.get("status") if o else ledger.get("status")
        return "blocked_technical", ev       # 执行进程没确认退出（台账还在跑或查不到）
    prev = [r for r in store.list_runs(t["id"]) if r["id"] != cur["id"] and r["status"] in ("failed", "stale")]
    if prev and failure_class(prev[-1]) == cls and (prev[-1]["receipt"] or {}).get("worker_error") == (cur["receipt"] or {}).get("worker_error"):
        ev["previous"] = prev[-1]["id"]
        return "retry_repeated", ev
    ended = ts._parse(cur["ended_at"])
    if ended:
        ev["not_before"] = (ended + dt.timedelta(seconds=RETRY_BACKOFF * 2 ** t["retries"])).isoformat(timespec="seconds")
    return "retry_suggested", ev


def _ready_decision(store, t, g, ledger, busy, max_running):
    unmet = store._unmet(t)
    if unmet:
        return _decision(t, "deps_unmet", depends_on=unmet)
    if t["retries"] > store.max_retries:
        return _decision(t, "retry_over_limit", retries=t["retries"], max_retries=store.max_retries)
    if t["owner"] not in TOOLS:
        return _decision(t, "no_executor", owner=t["owner"])
    if not g["project"]:
        return _decision(t, "no_project", goal=g["id"])
    if ledger["status"] != "ok":
        return _decision(t, "ledger_unavailable", ledger=ledger["status"])
    same = [b["key"] for b in busy if b["project"] == g["project"]]
    slots = {"global": f"{len(busy)}/{max_running}", "project": f"{len(same)}/1"}
    if len(busy) >= max_running:
        return _decision(t, "slot_full_global", slots=slots, occupied=[b["key"] for b in busy])
    if same:
        return _decision(t, "slot_full_project", slots=slots, occupied=same, project=g["project"])
    return _decision(t, "eligible", goal=g["id"], project=g["project"], tool=t["owner"], retries=t["retries"],
                     depends_on=t["depends_on"], ledger="ok", slots=slots)


# ---------- 进程身份（预留方、开单子进程、车间 worker 的身份都用它记录与核对）----------
def _ps(pid):
    """(状态, 启动时间) ；进程不在返回 (None, None)；ps 本身失败返回 ("?", None)。"""
    try:
        r = subprocess.run(["ps", "-o", "stat=", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True,
                           timeout=10, env=PS_ENV)
    except (OSError, ValueError, subprocess.SubprocessError):
        return "?", None
    parts = r.stdout.split()
    if r.returncode != 0 or not parts:
        return None, None
    return parts[0], " ".join(parts[1:]) or None


def proc_start(pid):
    """进程启动时间（ps 的 lstart，秒级）；进程不在或查不到返回 None。和 pid 一起防 PID 复用。"""
    return _ps(pid)[1]


def self_identity():
    pid = os.getpid()
    return {"pid": pid, "host": socket.gethostname(), "started": proc_start(pid)}


def check_identity(ident):
    """核对一份记录下来的进程身份：
    match    进程在、启动时间与记录一致（同一台机器）
    gone     进程不在、已是僵尸，或同一 PID 的启动时间对不上（PID 已复用）
    unknown  证据不够：没有记录 / 没记启动时间 / 别的主机 / ps 查不了——不能据此判断死活，更不能发信号"""
    if not isinstance(ident, dict) or not isinstance(ident.get("pid"), int) or ident["pid"] <= 1:
        return "unknown"
    if ident.get("host") and ident["host"] != socket.gethostname():
        return "unknown"
    st, started = _ps(ident["pid"])
    if st is None:
        return "gone"
    if st == "?" or not ident.get("started"):
        return "unknown"
    if st.startswith("Z"):
        return "gone"
    return "match" if started == ident["started"] else "gone"


@contextlib.contextmanager
def ledger_lock(dispatch_dir, wait=30):
    """和 dispatch.py 同一把派单根目录文件锁。锁顺序：先派单锁、后状态库事务；持状态库事务时绝不再来拿这把锁。"""
    root = Path(dispatch_dir)
    with open(root / LOCK_NAME, "a") as f:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise ts.StateError(f"派单锁 {LOCK_NAME} 等了 {wait} 秒还被占着")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def find_by_key(store, dispatch_dir, sched_key):
    """按调度关联键在台账里找单：返回 (带这个键的工单, 有 sched_key 文件却没有 order.json 的目录名)。"""
    ledger = store.read_orders(dispatch_dir)
    orders = [o for o in ledger["orders"] if o.get("sched_key") == sched_key]
    partial = []
    for name in ledger["ignored"]:
        try:
            if (Path(ledger["path"]) / name / "sched_key").read_text().strip() == sched_key:
                partial.append(name)
        except OSError:
            pass
    return ledger, orders, partial


def reconcile_claim(store, sched_key, dispatch_dir, settled=False):
    """核对一个未结预留（claimed / ordered）和台账，按证据收口。settled=True 表示调用方自己的开单子进程已经退出
    （tick 里开单返回之后），不必再等预留方。返回 {"result": linked / released / void / unsafe / wait, "why", ...}。
    台账按关联键找到恰好一张活动单 → 记单号并关联（adopt）；找到多张、只有半截目录、单已不在活动状态、
    记过单号却按键找不到 → unsafe；什么都没有：预留方或开单子进程还在、或租约没到 → wait，都确认退出且租约已过 → void。"""
    dispatch_dir = Path(dispatch_dir or store.dispatch_dir)
    with ledger_lock(dispatch_dir):          # 和 dispatch new 的编号段互斥：锁里看到的就是全部已编号的单
        cl = store.get_claim(sched_key)
        if cl["state"] not in ("claimed", "ordered"):
            return {"result": "noop", "state": cl["state"]}
        ledger, orders, partial = find_by_key(store, dispatch_dir, sched_key)
        if ledger["status"] not in ("ok", "missing") or ledger["errors"]:
            return {"result": "wait", "why": f"台账 {ledger['status']}，有读不了的单，先不判断"}

        def unsafe(why):
            store.claim_close(sched_key, "unsafe", note=why)
            return {"result": "unsafe", "why": why}

        if len(orders) + len(partial) > 1:
            return unsafe(f"同一关联键对应多张单或目录：{'、'.join([o['no'] for o in orders] + partial)}")
        if orders:
            o = orders[0]
            if o.get("status") not in ts.RUN_ACTIVE:
                return unsafe(f"按关联键找到单 {o['no']}，但它已是 {o.get('status')}，要人工核对后再关联")
            if store.claim_ordered(sched_key, o["no"])["claim"]["state"] == "unsafe":
                return {"result": "unsafe", "why": store.get_claim(sched_key)["note"]}
            res = store.claim_link(sched_key, agent=o.get("tool") or "unknown", status=o["status"])
            if not res["applied"] and res["claim"]["state"] != "linked":
                return {"result": "unsafe", "why": res["claim"]["note"]}
            return {"result": "linked", "order_no": o["no"], "run": res.get("run") or res["claim"]["run_id"]}
        if cl["state"] == "ordered":
            return unsafe(f"预留记过单号 {cl['order_no']}，台账里按关联键却找不到")
        lease_over = ts._age(store.clock(), cl["lease_until"]) > 0
        if settled:
            people = []
        else:
            people = [check_identity(cl["owner"])]
            if "child" in cl["owner"]:
                people.append(check_identity(cl["owner"]["child"]))
        if partial:
            if "match" in people or not lease_over:
                return {"result": "wait", "why": f"目录 {partial[0]} 还没有 order.json，开单可能还在进行"}
            return unsafe(f"开单中途中断：目录 {partial[0]} 有关联键却没有 order.json")
        if settled:
            store.claim_close(sched_key, "released", note="开单没有开出单（开单进程已退出，台账里没有这个关联键）")
            return {"result": "released"}
        if "match" in people:
            return {"result": "wait", "why": "预留方或开单进程还在"}
        if not lease_over:
            return {"result": "wait", "why": f"租约到 {cl['lease_until']} 才过期"}
        if "unknown" in people:
            return unsafe("租约已过，台账里没有这个关联键的单，但预留方进程身份核对不了")
        if "spawn" in cl["owner"] and "child" not in cl["owner"]:
            # F1：已经到了拉开单子进程那一步、却没登记子进程身份——子进程可能已启动、还在锁前慢慢 fetch，不能证明没开单
            return unsafe("开单子进程可能已经启动但没登记身份，证明不了没有开单：占住任务，等常浩核对")
        why = ("预留方已退出、从没走到拉开单子进程那一步" if "spawn" not in cl["owner"]
               else "预留方和登记过的开单子进程都已退出")
        store.claim_close(sched_key, "void", note=f"{why}、租约已过、台账里没有这个关联键的单")
        return {"result": "void"}


def orphan_orders(store, ledger):
    """F1：带关联键、但对应预留已收口（或状态库里根本没有、或已关联到别的单）的单和半截目录。只读；不删、不关联。"""
    out = []
    root = Path(ledger.get("path") or store.dispatch_dir)
    items = [(o["no"], o.get("sched_key"), "order") for o in ledger.get("orders", []) if o.get("sched_key")]
    for name in ledger.get("ignored", []):
        try:
            items.append((name, (root / name / "sched_key").read_text().strip(), "half"))
        except OSError:
            pass
    for no, key, what in items:
        try:
            cl = store.get_claim(key)
        except ts.NotFound:
            cl = None
        if cl and cl["state"] in ("claimed", "ordered"):
            continue           # 未结预留由恢复按键处理
        if cl and cl["state"] == "linked" and cl["order_no"] == no:
            continue
        why = ("状态库里没有这个关联键的预留" if not cl else
               f"关联键的预留已是 {cl['state']}" + (f"（关联的是 {cl['order_no']}）" if cl["order_no"] and cl["order_no"] != no else ""))
        out.append({"order_no": no, "sched_key": key, "task_id": cl["task_id"] if cl else None,
                    "claim_state": cl["state"] if cl else None, "kind": what,
                    "why": why + ("；目录没有 order.json" if what == "half" else "") + "：不自动关联、不删除，等常浩核对"})
    return out


def recover(store, dispatch_dir=None):
    """恢复一轮：逐个核对未结预留，并列出游离单。"""
    claims = recover_claims(store, dispatch_dir)
    return {"claims": claims, "orphans": orphan_orders(store, store.read_orders(dispatch_dir))}


def recover_claims(store, dispatch_dir=None):
    """跨会话 / 重启恢复：逐个核对未结预留（unsafe 的留给常浩）。重复执行不会重复改变状态。"""
    out = {}
    for c in store.claims(states=("claimed", "ordered")):
        out[c["sched_key"]] = reconcile_claim(store, c["sched_key"], dispatch_dir)
    return out


class DispatchExecutor:
    """真实开单：子进程跑 dispatch.py new --sched-key（只开单，开跑仍要经过现有确认门，调度器不调 dispatch run）。
    argv 可换成测试用的包装脚本。"""

    def __init__(self, mode, argv=None, timeout=600):
        if mode not in ("read", "write"):
            raise ts.StateError("开单模式只能是 read / write")
        self.mode, self.timeout = mode, timeout
        self.argv = argv or [sys.executable, str(HERE / "dispatch.py")]

    def create(self, decision, sched_key, task_file, on_spawn, before_spawn=None, db=None):
        ev = decision["evidence"]
        cmd = [*self.argv, "new", "--project", ev["project"], "--tool", ev["tool"], "--mode", self.mode,
               "--task-file", str(task_file), "--sched-key", sched_key]
        env = dict(os.environ)
        if db:
            env["DT_STATE_DB"] = str(db)      # dispatch 在派单锁里按这个库核对预留
        if before_spawn:
            before_spawn()                    # 先持久记下“要拉子进程了”，再拉（F1）
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        on_spawn({"pid": p.pid, "host": socket.gethostname(), "started": proc_start(p.pid)})
        out, err = p.communicate(timeout=self.timeout)
        last = (out.strip().splitlines() or ["{}"])[-1]
        try:
            res = json.loads(last)
        except ValueError:
            res = {"ok": False, "msg": (err or out)[-300:]}
        return {"ok": bool(res.get("ok")), "order_no": (res.get("order") or {}).get("no"), "msg": res.get("msg", "")}


def task_text(store, decision, sched_key):
    t = store.get_task(decision["task_id"])
    g = store.get_goal(t["goal_id"])
    return (f"# 目标：{t['title']}\n\n所属长期目标：{g['title']}（{g['id']}）\n任务：{t['id']}\n"
            f"验收：{t['acceptance'] or '（未写）'}\n下一步：{t['next_step'] or '（未写）'}\n\n"
            f"（调度器开单，关联键 {sched_key}；开跑前仍须常浩确认）\n")


def tick(store, executor, dispatch_dir=None, limit=1, owner=None, max_running=MAX_RUNNING, key=None):
    """调度一轮：决策 → 预留 → 开单 → 按关联键核对并关联。每个 dispatch 决策最多 limit 个。
    开单抛异常（结果不明）时预留留着，由 recover_claims 按证据收口；绝不在这里重试。"""
    dispatch_dir = Path(dispatch_dir or store.dispatch_dir)
    out = {"claimed": [], "refused": [], "linked": [], "released": [], "unsafe": [], "pending": []}
    plan = decide(store, store.read_orders(dispatch_dir), max_running)
    for d in [x for x in plan["decisions"] if x["decision"] == "dispatch"][:limit]:
        sk = (key or new_key)(d["task_id"])
        c = claim(store, d["task_id"], sk, owner, max_running, dispatch_dir)
        if not c["applied"]:
            out["refused"].append({"task_id": d["task_id"], "reason": c["reason"]})
            continue
        out["claimed"].append({"task_id": d["task_id"], "sched_key": sk})
        tasks = dispatch_dir / ".sched-tasks"
        tasks.mkdir(mode=0o700, exist_ok=True)
        tf = tasks / f"{sk}.md"
        tf.write_text(task_text(store, d, sk), encoding="utf-8")
        try:
            res = executor.create(d, sk, tf, lambda ident, sk=sk: store.claim_set_child(sk, ident),
                                  before_spawn=lambda sk=sk: store.claim_mark_spawning(sk), db=store.path)
        except Exception as e:  # noqa: BLE001 —— 结果不明：不重试，留给恢复
            out["pending"].append({"task_id": d["task_id"], "sched_key": sk, "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        r = reconcile_claim(store, sk, dispatch_dir, settled=True)   # 以台账按关联键找到的单为准，不信开单输出
        if r["result"] == "linked" and res.get("order_no") and res["order_no"] != r["order_no"]:
            r["warning"] = f"开单输出说是 {res['order_no']}，台账按关联键找到 {r['order_no']}，已按台账关联"
        bucket = {"linked": "linked", "released": "released", "unsafe": "unsafe"}.get(r["result"], "pending")
        out[bucket].append({"task_id": d["task_id"], "sched_key": sk, **{k: v for k, v in r.items() if k != "result"},
                            "dispatch_msg": res.get("msg", "")[:200]})
    return out


def new_key(task_id):
    return f"S{time.strftime('%Y%m%d%H%M%S')}-{task_id}-{os.urandom(3).hex()}"


def claim(store, task_id, sched_key, owner=None, max_running=MAX_RUNNING, dispatch_dir=None, lease=ts.SCHED_LEASE):
    """预留一个任务：在 claim_task 的写事务里重读台账、重新 decide，结论仍是 dispatch 才落预留。"""
    def check(s):
        for d in decide(s, s.read_orders(dispatch_dir), max_running)["decisions"]:
            if d["task_id"] == task_id:
                return d
        return {"decision": "skip", "reason": "not_found", "task_id": task_id}
    return store.claim_task(task_id, sched_key, owner or self_identity(), check, lease=lease)


# ---------- S3 生命体征：台账 + 回执 + 进程身份组合判定 ----------
VITALS = ("alive", "suspect", "lost", "finished", "not_started", "unknown")


def _ps_command(pid):
    try:
        r = subprocess.run(["ps", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True, timeout=10, env=PS_ENV)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def check_dispatch(o):
    """dispatch 自己的身份（order.json 的 dispatch_pid / run_host / run_started）：进程在且启动不晚于写 running 的时刻 → match。"""
    pid, started = o.get("dispatch_pid"), o.get("run_started")
    if not isinstance(pid, int) or pid <= 1 or not started:
        return "unknown"
    if o.get("run_host") and o["run_host"] != socket.gethostname():
        return "unknown"
    st, lstart = _ps(pid)
    if st is None or (st and st.startswith("Z")):
        return "gone"
    if st == "?" or not lstart:
        return "unknown"
    try:
        began = dt.datetime.strptime(lstart, "%a %b %d %H:%M:%S %Y")
        mark = dt.datetime.fromisoformat(started).replace(tzinfo=None)
    except ValueError:
        return "unknown"
    return "match" if began <= mark else "gone"


def worker_state(order_dir, o):
    """本轮车间的身份与核对结果：(worker.json 内容, missing / previous / mismatch / match / gone / unknown)。
    previous：worker.json 早于本轮开跑（上一轮留下的）；mismatch：记的结果目录不是这张单的；
    match 还要求该进程的命令行里带着 <单号>/out，否则 unknown（防 PID 复用到别的程序）。"""
    out = Path(order_dir) / "out"
    try:
        w = json.loads((out / "worker.json").read_text())
    except (OSError, ValueError):
        return None, "missing"
    if not isinstance(w, dict):
        return None, "missing"
    if o.get("run_started") and str(w.get("at") or "") < o["run_started"]:
        return w, "previous"
    try:
        if Path(str(w.get("out") or "")).resolve() != out.resolve():
            return w, "mismatch"
    except OSError:
        return w, "mismatch"
    state = check_identity(w)
    if state == "match":
        cmd = _ps_command(w["pid"])
        if not cmd or f"{o['no']}/out" not in cmd:
            return w, "unknown"
    return w, state


def _fresh_receipt(order_dir, o):
    try:
        rc = json.loads((Path(order_dir) / "out" / "receipt.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(rc, dict):
        return None
    return rc if str(rc.get("ended") or "") >= str(o.get("run_started") or "") else None


def vital_of(order, ledger_status="ok"):
    """一张单的生命体征。心跳只作证据：不会因为心跳旧了就判停，也不会因此判失败或发信号。"""
    if ledger_status != "ok":
        return "unknown", "台账读不到", {}
    if order is None:
        return "unknown", "台账里没有这张单", {}
    ls = order.get("status")
    if ls in ("new", "queued"):
        return "not_started", f"单还没开跑（{ls}）", {}
    if ls != "running":
        return "finished", f"台账已有结果 {ls}，对账会结算", {}
    d = order.get("_dir") or ""
    w, ws = worker_state(d, order)
    ds = check_dispatch(order)
    rc = _fresh_receipt(d, order)
    ev = {"worker": ws, "dispatch": ds, "receipt": bool(rc),
          "worker_pid": (w or {}).get("pid"), "worker_pgid": (w or {}).get("pgid")}
    if ws == "match":
        return "alive", "车间进程在，身份（pid、启动时间、主机、命令行）都对上", ev
    if rc and ws == "gone":
        why = "车间已退出并写了回执，等 dispatch 收尾" if ds == "match" else "车间已退出并写了回执，但 dispatch 已不在，台账停在 running"
        return "finished", why, ev
    if ws == "gone":
        return "lost", "车间进程确认已不在、没有本轮回执：不自动判失败、不重试，留给人工", ev
    if ds == "match":
        return "suspect", "dispatch 还在，但车间身份核对不了（刚启动、没写身份或证据不全）", ev
    return "unknown", "没有可核对的进程证据，不下判断", ev


def observe(store, ledger=None, now=None):
    """S3：所有活动 Run（已在状态库的）和台账里 running 的单的生命体征。只读：不改库、不改台账、不发信号。"""
    ledger = ledger or store.read_orders()
    by_no = {o["no"]: o for o in ledger.get("orders", [])}
    now = now or store.clock()
    out, seen = [], set()
    for r in store.list_runs():
        if r["status"] not in ts.RUN_ACTIVE:
            continue
        o = by_no.get(r["order_no"]) if r["order_no"] else None
        vital, why, ev = vital_of(o, ledger["status"]) if r["order_no"] else ("unknown", "Run 没有工单号", {})
        age = ts._age(now, r["heartbeat_at"])
        out.append({"run": r["id"], "task_id": r["task_id"], "order_no": r["order_no"], "ledger_status": (o or {}).get("status"),
                    "vital": vital, "why": why, "evidence": {**ev, "heartbeat_age": None if age is None else int(age)},
                    "cancel_requested": bool(r["cancel_requested_at"])})
        seen.add(r["order_no"])
    for o in ledger.get("orders", []):
        if o.get("status") == "running" and o["no"] not in seen:
            vital, why, ev = vital_of(o)
            out.append({"run": None, "task_id": None, "order_no": o["no"], "ledger_status": "running", "vital": vital,
                        "why": why, "evidence": ev, "cancel_requested": False})
    return out


# ---------- S4 取消闭环 ----------
def _production(dispatch_dir):
    return Path(dispatch_dir).expanduser().resolve() == (Path.home() / ".hermes/dispatch").resolve()


def cancel(store, ledger=None, send=False, wait=10.0, record=True):
    """S4：处理 queue 的 cancel_pending（已撤销任务上还在活动的 Run）。
    只有车间身份全对上（match）、且它是自己进程组的头（pgid = pid = sid）时，才可能对该进程组发 SIGTERM；
    send=True 只给隔离测试用（真实派单根目录直接拒绝，命令行也不提供），真实路径只出建议。
    发信号后有界等待并复核；停不下来只报告，不升级到 SIGKILL。身份不明、已退出、PID 复用一律不发信号。
    结果（含建议）记成 run 上的事件，同一情形只记一次。"""
    dispatch_dir = Path(store.dispatch_dir)
    if send and _production(dispatch_dir):
        raise ts.NotAuthorized("真实派单根目录上不发终止信号（B2.2 只在测试受控子进程上发），只出建议")
    ledger = ledger or store.read_orders()
    by_no = {o["no"]: o for o in ledger.get("orders", [])}
    out = []
    for item in store.queue(ledger=ledger)["cancel_pending"]:
        r = store.get_run(item["id"])
        o = by_no.get(r["order_no"]) if r["order_no"] else None
        vital, why, ev = vital_of(o, ledger["status"]) if r["order_no"] else ("unknown", "Run 没有工单号", {})
        res = {"run": r["id"], "task_id": r["task_id"], "order_no": r["order_no"], "vital": vital, "why": why, "evidence": ev}
        if vital == "finished":
            res.update(action="none", advice="已结束：对账结算 Run，结果不回写已撤销的任务")
        elif vital == "not_started":
            res.update(action="advice", advice=f"单还没开跑：确认后人工 dispatch.py clean {r['order_no']} 放弃")
        elif vital != "alive":
            res.update(action="advice", advice="身份核对不了或进程已不在：不发信号，人工核对进程和台账")
        else:
            w, _ = worker_state(o["_dir"], o)
            if not (w.get("pid") == w.get("pgid") == w.get("sid")):
                res.update(action="advice", advice="车间不是独立进程组的头，不能只停它：人工处理")
            elif not send:
                res.update(action="advice", advice=f"确认后对车间进程组 {w['pgid']} 发 SIGTERM（本批真实环境不自动执行）")
            else:
                res.update(_terminate(store, r, o, w, wait))
        if record:
            key = f"run.cancel:{r['id']}:{res['action']}:{res.get('result', vital)}:{(ev or {}).get('worker_pid')}"
            store.ingest_event("system", f"run.cancel_{res.get('result') or res['action']}", key, "system",
                               {k: v for k, v in res.items() if k != "evidence"}, "run", r["id"])
        out.append(res)
    return out


def _terminate(store, r, o, w, wait):
    sent_key = f"run.cancel_signal:{r['id']}:{w['pid']}:{w['started']}"
    _, state = worker_state(o["_dir"], o)            # 发信号前最后再核一次身份（防检查与动作之间进程换人）
    if state != "match":
        return {"action": "advice", "result": "identity_changed", "advice": "发信号前复核身份不一致：没有发信号，人工核对"}
    first = store.ingest_event("system", "run.cancel_signal", sent_key, "system",
                               {"pgid": w["pgid"], "signal": "SIGTERM", "order_no": r["order_no"]}, "run", r["id"])
    if first["applied"]:
        try:
            os.killpg(w["pgid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if worker_state(o["_dir"], o)[1] != "match":
            return {"action": "signal", "result": "stopped", "advice": "车间已停；dispatch 收尾后对账结算 Run（任务保持撤销）"}
        time.sleep(0.1)
    return {"action": "signal", "result": "still_running",
            "advice": f"SIGTERM 后 {wait:g} 秒仍在：不升级 SIGKILL，人工处理进程组 {w['pgid']}"}


# ---------- S6 低噪声报告：只报错误和要常浩决定的，同一情形只报一次（去重记在状态库事件里）----------
LEVELS = {"error": "错误", "needs_user": "要常浩决定"}


def findings(store, ledger=None):
    """当前所有值得报的情形，每条带 level / kind / object / reason / why / fingerprint。只读。"""
    ledger = ledger or store.read_orders()
    items = []

    def add(level, kind, obj, reason, why, anchor, **extra):
        fp = hashlib.sha1(json.dumps([kind, obj, reason, anchor], ensure_ascii=False).encode()).hexdigest()[:16]
        items.append({"level": level, "kind": kind, "object": obj, "reason": reason, "why": why, "fingerprint": fp, **extra})

    plan = decide(store, ledger)
    tasks = {t["id"]: t for t in store.list_tasks()}
    for d in plan["decisions"]:
        t = tasks[d["task_id"]]
        if d["decision"] == "unsafe":
            add("error", "task", d["task_id"], d["reason"], d["why"], t["updated_at"], next_action=d["next_action"])
        elif d["reason"] in ("blocked_decision", "in_review", "retry_suggested", "retry_exhausted", "retry_repeated"):
            add("needs_user", "task", d["task_id"], d["reason"], d["why"], t["updated_at"], next_action=d["next_action"])
    for v in observe(store, ledger):
        if v["vital"] == "lost" or (v["vital"] == "finished" and v["evidence"].get("dispatch") == "gone"):
            add("error", "run", v["run"] or v["order_no"], v["vital"], v["why"], v["order_no"])
        elif v["vital"] in ("suspect", "unknown") and v["ledger_status"] == "running":
            add("needs_user", "run", v["run"] or v["order_no"], v["vital"], v["why"], v["order_no"])
    for o in orphan_orders(store, ledger):
        add("error", "order", o["order_no"], "orphan", o["why"], o["sched_key"], task_id=o["task_id"])
    for c in cancel(store, ledger, send=False, record=False):
        if c["action"] == "advice":
            add("needs_user", "cancel", c["run"], c["vital"], c["advice"], c["order_no"])
    return items


def _reported(store, fp):
    return bool(store.conn.execute("SELECT 1 FROM events WHERE idem_key=?", (f"sched.report:{fp}",)).fetchone())


def report(store, ledger=None):
    """待报的情形（还没确认送达的）。只读、不标记：输出不等于送达，送达确认后才 ack（至少一次，宁可重复不漏报）。"""
    return [f for f in findings(store, ledger) if not _reported(store, f["fingerprint"])]


def deliver(store, sender, ledger=None):
    """可信投递适配层的接口（F2）：sender(items) 只有明确确认送达才返回 True（全部）或已送达的指纹列表（部分）；
    返回别的值、抛异常、超时都当没送到，保留待报。返回 {"pending": 剩余待报数, "delivered": 本次确认数}。"""
    items = report(store, ledger)
    if not items:
        return {"pending": 0, "delivered": 0}
    try:
        confirmed = sender(items)
    except Exception:  # noqa: BLE001 —— 投递失败：不标记
        confirmed = None
    if confirmed is True:
        got = items
    elif isinstance(confirmed, (list, tuple, set)):
        want = {x for x in confirmed if isinstance(x, str)}
        got = [f for f in items if f["fingerprint"] in want]
    else:
        got = []
    ack(store, got)
    return {"pending": len(items) - len(got), "delivered": len(got)}


def ack(store, items):
    """标记已送达：每条一个 sched.report 事件，幂等键 sched.report:<指纹>，重复 ack 不重复记；只记种类 / 对象 / 原因，不记原文。"""
    for f in items:
        store.ingest_event("system", "sched.report", f"sched.report:{f['fingerprint']}", "system",
                           {k: f[k] for k in ("level", "kind", "object", "reason")})
    return len(items)


# ---------- 命令行 ----------
def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False, sort_keys=True))
    sys.exit(0 if ok else 1)


def cmd_plan(a):
    with ts.Store(a.db, dispatch_dir=a.dispatch, readonly=True) as s:
        out = decide(s)
    c = out["counts"]
    msg = "；".join(f"{k} {v}" for k, v in c.items() if v) or "没有未收尾的任务"
    done(True, f"调度决策（只读，台账 {out['ledger']}）：{msg}", **out)


def cmd_tick(a):
    if not a.execute:
        return cmd_plan(a)
    if os.environ.get("DT_SCHED_ENABLE") != "1":
        done(False, "自动开单没有启用（需要 DT_SCHED_ENABLE=1；正式启用要常浩单独批准），本轮只决策不执行，用 plan 看结果")
    if not a.mode:
        done(False, "带 --execute 时必须写明 --mode read|write")
    with ts.Store(a.db, dispatch_dir=a.dispatch) as s:
        if s.dispatch_dir.resolve() != (Path.home() / ".hermes/dispatch").resolve():
            done(False, "dispatch.py 只认 ~/.hermes/dispatch，--dispatch / DT_DISPATCH 指向别处时不能真实开单（关联键会对不上）")
        out = tick(s, DispatchExecutor(a.mode), limit=a.limit)
    msg = "；".join(f"{k} {len(v)}" for k, v in out.items() if v) or "没有可派的任务"
    done(not (out["unsafe"] or out["pending"]), f"调度一轮：{msg}", **out)


def cmd_observe(a):
    with ts.Store(a.db, dispatch_dir=a.dispatch, readonly=True) as s:
        out = observe(s)
    c = {k: sum(1 for v in out if v["vital"] == k) for k in VITALS}
    done(True, "生命体征（只读）：" + ("；".join(f"{k} {v}" for k, v in c.items() if v) or "没有活动的单"), vitals=out, counts=c)


def cmd_cancel(a):
    with ts.Store(a.db, dispatch_dir=a.dispatch) as s:
        out = cancel(s, send=False)
    done(True, f"待停的 Run {len(out)} 个（只出建议，不发信号）", cancels=out)


def cmd_report(a):
    """只输出待报，不标记（F2）：stdout 只是待投递内容，送达确认后由投递端调 ack --fingerprint。没确认前每轮都会再报。"""
    with ts.Store(a.db, dispatch_dir=a.dispatch, readonly=True) as s:
        new = report(s)
    if not new and a.quiet:
        return
    msg = "；".join(f"{LEVELS[f['level']]}：{f['kind']} {f['object']} {f['why']}" for f in new) or "没有待报的情况"
    print(json.dumps({"ok": True, "msg": msg, "findings": new, "acked": False}, ensure_ascii=False, sort_keys=True))


def cmd_ack(a):
    """投递端确认送达后调用：只认当前待报里的指纹；不认识的不标记并返回失败。"""
    with ts.Store(a.db, dispatch_dir=a.dispatch) as s:
        cur = {f["fingerprint"]: f for f in findings(s)}
        known = [cur[fp] for fp in a.fingerprint if fp in cur]
        unknown = [fp for fp in a.fingerprint if fp not in cur]
        ack(s, known)
    done(not unknown, f"已标记送达 {len(known)} 条" + (f"；{len(unknown)} 个指纹不在当前情形里，没有标记" if unknown else ""),
         acked=[f["fingerprint"] for f in known], unknown=unknown)


def cmd_recover(a):
    with ts.Store(a.db, dispatch_dir=a.dispatch) as s:
        out = recover(s)
    bad = {k: v for k, v in out["claims"].items() if v["result"] == "unsafe"}
    msg = f"核对 {len(out['claims'])} 个未结预留" + (f"，{len(bad)} 个要常浩处理" if bad else "")
    if out["orphans"]:
        msg += f"；游离单 {len(out['orphans'])} 张（带关联键却没有未结预留），要常浩核对"
    done(not bad and not out["orphans"], msg, **out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help=f"状态库路径，默认 $DT_STATE_DB 或 {ts.DEFAULT_DB}")
    ap.add_argument("--dispatch", help=f"派单根目录，默认 $DT_DISPATCH 或 {ts.DEFAULT_DISPATCH}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    t = sub.add_parser("tick")
    t.add_argument("--execute", action="store_true")
    t.add_argument("--mode", choices=("read", "write"))
    t.add_argument("--limit", type=int, default=1)
    sub.add_parser("recover")
    sub.add_parser("observe")
    sub.add_parser("cancel")
    sub.add_parser("report").add_argument("--quiet", action="store_true")
    sub.add_parser("ack").add_argument("--fingerprint", nargs="+", required=True)
    a = ap.parse_args(argv)
    try:
        {"plan": cmd_plan, "tick": cmd_tick, "recover": cmd_recover, "observe": cmd_observe, "cancel": cmd_cancel,
         "report": cmd_report, "ack": cmd_ack}[a.cmd](a)
    except ts.StateError as e:
        done(False, str(e), error=type(e).__name__)


if __name__ == "__main__":
    main()
