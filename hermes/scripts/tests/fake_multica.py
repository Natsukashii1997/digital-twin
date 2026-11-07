#!/usr/bin/env python3
"""假 multica CLI（multica_night.py 自测用）：不联网。世界状态在 FAKE_MULTICA_WORLD 指向的 JSON 里，每次调用记进 world["calls"]。

world：
  issues     {identifier: {id, identifier, title, description, status, assignee_type, assignee_id, stage, parent_issue_id, updated_at}}
  runs       [{id, issue, status, created_at, started_at, completed_at, attempt, agent_id, error, result}]
  messages   {run_id: [{seq, type, content}]}
  wakeups    {identifier: [{id, status}]}
  autopilots [{id, status}]
  now        假时间（改动 issue 时写进 updated_at）
  owner      成员 id（指派给它的是 member，其余是 agent）
  start_runs 改 todo 时是否给 agent 单开一个 queued run（默认 true）
  fail       [命令前缀]：命中就退出码 1
  stuck      [run id]：cancel-task 对它们不生效（模拟取消不掉）
参数里的 --workspace-id、--profile、--output 都忽略，但会记下来供断言。
"""
import json
import os
import sys

path = os.environ["FAKE_MULTICA_WORLD"]
with open(path, encoding="utf-8") as f:
    w = json.load(f)
args = sys.argv[1:]
stdin = sys.stdin.read() if "--description-stdin" in args or "--content-stdin" in args else None
w.setdefault("calls", []).append({"args": args, "stdin": stdin})


def save():
    with open(path, "w", encoding="utf-8") as f:
        json.dump(w, f, ensure_ascii=False)


def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default


def pos():
    """去掉 --flag value 之后的位置参数。"""
    out, skip = [], False
    for a in args:
        if skip:
            skip = False
            continue
        if a.startswith("--"):
            skip = a not in ("--no-start", "--description-stdin", "--content-stdin", "--allow-duplicate")
            continue
        out.append(a)
    return out


def emit(data):
    save()
    print(json.dumps(data, ensure_ascii=False))
    sys.exit(0)


def die(msg):
    save()
    print(msg, file=sys.stderr)
    sys.exit(1)


p = pos()
line = " ".join(p)
for prefix in w.get("fail", []):
    if line.startswith(prefix):
        die("fake: 注入失败 " + prefix)

issues = w.setdefault("issues", {})


def touch(i):
    i["updated_at"] = w.get("now", "2026-11-08T14:00:00Z")


if p[:2] == ["issue", "create"]:
    n = max([int(k.split("-")[1]) for k in issues] + [10]) + 1
    ident = f"SBX-{n}"
    aid = opt("--assignee-id")
    i = {"id": f"id-{ident}", "identifier": ident, "title": opt("--title"), "description": stdin,
         "status": opt("--status", "backlog"), "assignee_id": aid,
         "assignee_type": "member" if aid == w.get("owner") else "agent",
         "stage": int(opt("--stage")) if opt("--stage") else None, "parent_issue_id": opt("--parent"),
         "project_id": opt("--project")}
    touch(i)
    issues[ident] = i
    emit(i)
if p[:2] == ["issue", "get"]:
    if w.get("weird_get"):
        emit([])            # 输出形状不对，模拟 multica 升级改了格式
    if p[2] not in issues:
        die("fake: 没有这张单")
    emit(issues[p[2]])
if p[:2] == ["issue", "runs"]:
    emit([r for r in w.get("runs", []) if r["issue"] == p[2]])
if p[:3] == ["issue", "wakeup", "list"]:
    emit(w.get("wakeups", {}).get(p[3], []))
if p[:3] == ["issue", "wakeup", "disable"]:
    for x in w.get("wakeups", {}).get(p[3], []):
        if x["id"] == p[4]:
            x["status"] = "disabled"
    emit({"ok": True})
if p[:2] == ["autopilot", "list"]:
    aps = w.get("autopilots", [])
    emit({"autopilots": aps, "total": len(aps)})
if p[:2] == ["autopilot", "update"]:
    for a in w.get("autopilots", []):
        if a["id"] == p[2]:
            a["status"] = opt("--status")
    emit({"ok": True})
if p[:2] == ["issue", "run-messages"]:
    since = int(opt("--since", "0"))
    emit([m for m in w.get("messages", {}).get(p[2], []) if m["seq"] > since])
if p[:2] == ["issue", "status"]:
    i = issues[p[2]]
    i["status"] = p[3]
    touch(i)
    if p[3] == "todo" and i["assignee_type"] == "agent" and "--no-start" not in args and w.get("start_runs", True):
        rid = f"run-{p[2]}-{len(w.setdefault('runs', [])) + 1}"
        w["runs"].append({"id": rid, "issue": p[2], "status": "queued", "created_at": w.get("now"), "started_at": None,
                          "completed_at": None, "attempt": 1, "agent_id": i["assignee_id"], "error": None, "result": {}})
    emit(i)
if p[:2] == ["issue", "cancel-task"]:
    for r in w.get("runs", []):
        if r["id"] == p[2] and r["id"] not in w.get("stuck", []):
            r["status"] = "cancelled"
            r["completed_at"] = w.get("now")
    emit({"ok": True})
if p[:3] == ["issue", "comment", "add"]:
    w.setdefault("comments", []).append({"issue": p[3], "content": stdin})
    touch(issues[p[3]])
    emit({"ok": True})
if p[:2] == ["issue", "list"]:
    lo, hi = opt("--updated-since"), opt("--updated-until")
    off, lim = int(opt("--offset", "0")), int(opt("--limit", "50"))
    hits = [i for i in issues.values() if lo <= i.get("updated_at", "") < hi]
    emit({"issues": hits[off:off + lim], "has_more": off + lim < len(hits)})
die("fake: 不认识的命令 " + line)
