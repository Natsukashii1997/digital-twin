"""持久调度（B2.2）：调度决策、调度预留、开单关联与恢复。只用标准库；状态都在 B1 状态库（twin_state.py），不另建库。

  python3 twin_scheduler.py plan [--db F] [--dispatch D]
      只读决策：每个未收尾的任务给出 decision / reason / evidence / next_action，不改任何状态（只读打开库，库不存在不建）。

决策（decision）：
  dispatch  可以派：目标已授权、任务 ready、依赖都完成、没有进行中的 Run、没有未结预留、台账可读、并发名额有空
  hold      条件满足但现在不能派（名额满、台账读不到、另一个调度器正在开单），下一轮再看
  skip      按规则不该派（未授权、依赖没完成、阻塞、待验收、正在跑等），等对应的人或事件
  unsafe    状态矛盾或归属证明不了，要常浩处理；调度器不碰
顺序：按任务创建先后（先建先派）；调度器不自己重排常浩的目标，名额不够时后面的任务给 hold 并写明原因。
并发：总共最多 MAX_RUNNING 张、同一项目最多 1 张，名额 = 未结预留 + 已关联任务的活动 Run + 台账里在跑的单（三者按单号去重）。
"""
import argparse
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import twin_state as ts  # noqa: E402  同目录（安装副本在 ~/.hermes/scripts/）

MAX_RUNNING = 2            # 与 dispatch.py 一致：总并发 2、同一仓库 1
TOOLS = ("claude", "codex")
DECISIONS = ("dispatch", "hold", "skip", "unsafe")
REASONS = {
    "eligible": "满足全部派单条件",
    "goal_not_authorized": "目标还没有常浩授权（或已收尾）",
    "task_not_ready": "任务还在 observed，没有进入 ready",
    "deps_unmet": "依赖任务还没完成",
    "blocked_decision": "等常浩决定",
    "blocked_external": "等外部条件恢复",
    "blocked_technical": "技术失败后阻塞（重试建议见 S5）",
    "in_review": "待验收：执行完成不等于验收通过",
    "running": "已有进行中的 Run",
    "claim_open": "另一个调度预留正在开单",
    "claim_unsafe": "调度预留归属证明不了，要常浩收口",
    "queue_unsafe": "状态矛盾（见 queue 的 unsafe 桶）",
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
    "blocked_technical": "看失败原因；技术可恢复且没到上限时出重试建议",
    "in_review": "核对回执、测试与 PR，再由非执行方记验收结论",
    "running": "等车间回执；生命体征见 observe",
    "claim_open": "等该预留开单或过期后由恢复核对",
    "claim_unsafe": "常浩核对该预留和工单后手动收口",
    "queue_unsafe": "人工核对状态矛盾",
    "retry_over_limit": "常浩决定是否继续",
    "no_executor": "需要车间执行时把负责方改成 claude 或 codex",
    "no_project": "给目标补上项目（名册 id）",
    "ledger_unavailable": "先恢复派单台账可读，下一轮再看",
    "slot_full_global": "等在跑的单结束，下一轮再看",
    "slot_full_project": "等该项目在跑的单结束，下一轮再看",
}
CATEGORY = {"eligible": "dispatch", "claim_open": "hold", "ledger_unavailable": "hold", "slot_full_global": "hold",
            "slot_full_project": "hold", "claim_unsafe": "unsafe", "queue_unsafe": "unsafe", "retry_over_limit": "unsafe"}


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
                ev.update(retries=t["retries"], max_retries=store.max_retries)
            d = _decision(t, reason, **ev, **run_ev)
        else:   # ready
            d = _ready_decision(store, t, g, ledger, busy, max_running)
            if d["decision"] == "dispatch":    # 同一轮里后面的任务要看到这个名额已被占
                busy.append({"key": f"plan:{t['id']}", "project": g["project"], "kind": "plan"})
        decisions.append(d)
    counts = {k: sum(1 for d in decisions if d["decision"] == k) for k in DECISIONS}
    return {"ledger": ledger["status"], "decisions": decisions, "occupied": occupied(store, ledger), "counts": counts}


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


# ---------- 进程身份（预留方、车间 worker 的身份都用它记录与核对）----------
def proc_start(pid):
    """进程启动时间（ps 的 lstart，秒级）；进程不在或查不到返回 None。和 pid 一起防 PID 复用。"""
    try:
        r = subprocess.run(["ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=10)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return " ".join(r.stdout.split()) or None


def self_identity():
    pid = os.getpid()
    return {"pid": pid, "host": socket.gethostname(), "started": proc_start(pid)}


def claim(store, task_id, sched_key, owner=None, max_running=MAX_RUNNING, dispatch_dir=None, lease=ts.SCHED_LEASE):
    """预留一个任务：在 claim_task 的写事务里重读台账、重新 decide，结论仍是 dispatch 才落预留。"""
    def check(s):
        for d in decide(s, s.read_orders(dispatch_dir), max_running)["decisions"]:
            if d["task_id"] == task_id:
                return d
        return {"decision": "skip", "reason": "not_found", "task_id": task_id}
    return store.claim_task(task_id, sched_key, owner or self_identity(), check, lease=lease)


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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help=f"状态库路径，默认 $DT_STATE_DB 或 {ts.DEFAULT_DB}")
    ap.add_argument("--dispatch", help=f"派单根目录，默认 $DT_DISPATCH 或 {ts.DEFAULT_DISPATCH}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    a = ap.parse_args(argv)
    try:
        {"plan": cmd_plan}[a.cmd](a)
    except ts.StateError as e:
        done(False, str(e), error=type(e).__name__)


if __name__ == "__main__":
    main()
