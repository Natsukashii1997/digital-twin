#!/usr/bin/env python3
"""统一事件接入协议（B2.1 T02）：来自用户、派单台账、Agent、外部系统和定时检查的事件，先装进同一个“信封”，
再经 twin_state.Store.ingest_event 进状态库——同一事件只应用一次，状态变化和事件留痕在同一个事务里。

信封（JSON 对象）：
  source       user | dispatch | agent | external | system   （库里的 events.source 分别记 user / system / agent / discovery / system）
  source_ref   来源对象，如 feishu:om_xxx、dispatch:D20261026-4、github:owner/repo#12、cron:reconcile（可选，记进证据）
  event_id     稳定事件 id：同一件事再来必须是同一个 id（飞书 message_id、GitHub delivery id、定时任务的时段……）
  type         要做的动作，见 TYPES（goal.authorize / task.unblock / run.receipt / order.sync / note …）
  occurred_at  发生时间（ISO 字符串，可选；库另记接收时间）
  goal_id / task_id / run_id   关联对象（按 type 需要）
  payload      动作参数（title、reason、kind、receipt、basis、cites……）
  refs         证据引用 [{"kind", "ref", "url"}]（可选，动作成功后记到关联对象上）
  identity     {"role": user | hermes | claude | codex | system, "method": "feishu_message", "message_id": "om_…"}
               role=user 只能来自 source=user，必须带 method=feishu_message 和 message_id，由 twin_identity 回读飞书核实；
               信封里自带的 verified / open_id 一律不信、丢掉。其他角色不做身份核实，但永远拿不到 user 的权限。
正文（text / body / content / message）不进库：只留 sha1，便于事后对照。

乱序与迟到沿用 B1 的规则：旧 Run 的迟到结果只记 run.late_result，台账回退记 run.conflict，都不覆盖已确认的事实。
order.sync 类型直接走 Store.sync_order（幂等键 = 单号 + 文件内容哈希），不另记信封事件。
其余信封在 events 表里记为 kind = "in.<type>"（接入留痕，幂等键 evt:<source>:<event_id>），动作本身的事件由 Store 照常记。

  python3 twin_events.py ingest   --file 信封.json     接入；末行 JSON ok/msg（--file - 读标准输入）
  python3 twin_events.py validate --file 信封.json     只校验、规范化，不碰库
  python3 twin_events.py types                         列出动作类型与所需字段
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import twin_identity as I  # noqa: E402  同目录
import twin_state as T  # noqa: E402

SOURCES = {"user": "user", "dispatch": "system", "agent": "agent", "external": "discovery", "system": "system"}
DEFAULT_ROLE = {"user": "user", "dispatch": "system", "agent": None, "external": "hermes", "system": "system"}
BODY_FIELDS = ("text", "body", "content", "message")
DROP_IDENTITY = ("verified", "open_id", "sender", "sender_open_id", "chat_id", "user_id")


class EventError(T.StateError):
    """信封不合规（缺字段、来源与角色不配、正文当身份）。"""


def _need(p, *keys):
    missing = [k for k in keys if p.get(k) in (None, "")]
    if missing:
        raise EventError(f"缺字段：{'、'.join(missing)}")


def _receipt(p):
    if isinstance(p.get("receipt"), dict):
        return p["receipt"]
    if p.get("receipt_file"):
        try:
            return json.loads(Path(p["receipt_file"]).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise EventError(f"读不了回执文件 {p['receipt_file']}：{type(e).__name__}")
    raise EventError("缺字段：receipt（对象）或 receipt_file（路径）")


# 每种动作：handler(store, env, by) → 结果；need = 信封里必须有的字段（payload.x 表示 payload 里的）；说明
def _h_goal_create(s, e, by):
    p = e["payload"]
    return s.create_goal(p["title"], by, p.get("description", ""), p.get("status", "observed"), p.get("scope", ""),
                         p.get("project"), p.get("phase", ""), p.get("done_when", ""))


def _h_task_create(s, e, by):
    p = e["payload"]
    deps = p.get("depends_on") or []
    return s.create_task(e["goal_id"], p["title"], by, p.get("acceptance", ""), p.get("next_step", ""), deps,
                         p.get("owner", "hermes"))


def _h_run_start(s, e, by):
    p = e["payload"]
    return s.start_run(e["task_id"], p["agent"], by, p.get("order_no"), p.get("session_id"),
                       p.get("status", "running"), note=p.get("note", ""), purpose=p.get("purpose", "execute"))


def _h_ref_add(s, e, by):
    p = e["payload"]
    return s.add_ref(p["object_type"], p["object_id"], p["kind"], p["ref"], p.get("url", ""), p.get("note", ""), by)


TYPES = {
    "goal.create": (_h_goal_create, ("payload.title",), "建目标；by=user 可带 payload.status=authorized"),
    "goal.authorize": (lambda s, e, by: s.authorize_goal(e["goal_id"], by, e["payload"].get("note", "")), ("goal_id",), "授权目标（只认 user）"),
    "goal.complete": (lambda s, e, by: s.complete_goal(e["goal_id"], by, e["payload"].get("note", "")), ("goal_id",), "完成目标（只认 user）"),
    "goal.cancel": (lambda s, e, by: s.cancel_goal(e["goal_id"], by, e["payload"].get("reason", "")), ("goal_id",), "撤销目标（只认 user）"),
    "task.create": (_h_task_create, ("goal_id", "payload.title"), "建任务"),
    "task.ready": (lambda s, e, by: s.set_ready(e["task_id"], by, e["payload"].get("note", "")), ("task_id",), "任务可执行"),
    "task.block": (lambda s, e, by: s.block_task(e["task_id"], by, e["payload"]["kind"], e["payload"]["reason"]), ("task_id", "payload.kind", "payload.reason"), "阻塞"),
    "task.unblock": (lambda s, e, by: s.unblock_task(e["task_id"], by, e["payload"].get("note", "")), ("task_id",), "解阻塞（decision 类只认 user）"),
    "task.retry": (lambda s, e, by: s.retry_task(e["task_id"], by), ("task_id",), "重试"),
    "task.accept": (lambda s, e, by: s.accept_task(e["task_id"], by, e["payload"]["basis"], e["payload"].get("cites") or [], e["payload"].get("passed", True)), ("task_id", "payload.basis"), "验收结论"),
    "task.complete": (lambda s, e, by: s.complete_task(e["task_id"], by, e["payload"].get("note", "")), ("task_id",), "完成任务"),
    "task.rework": (lambda s, e, by: s.rework_task(e["task_id"], by, e["payload"]["note"]), ("task_id", "payload.note"), "退回返工"),
    "task.cancel": (lambda s, e, by: s.cancel_task(e["task_id"], by, e["payload"].get("reason", "")), ("task_id",), "撤销任务"),
    "task.update": (lambda s, e, by: s.update_task(e["task_id"], by, **e["payload"]), ("task_id",), "改任务字段"),
    "run.start": (_h_run_start, ("task_id", "payload.agent"), "开跑（或 purpose=review 的审查 Run）"),
    "run.finish": (lambda s, e, by: s.finish_run(e["run_id"], e["payload"]["status"], by, e["payload"].get("receipt"), e["payload"].get("evidence"), e["payload"].get("note", "")), ("run_id", "payload.status"), "Run 结束"),
    "run.receipt": (lambda s, e, by: s.receive_receipt(e["run_id"], _receipt(e["payload"]), by), ("run_id",), "车间回执（payload.receipt 或 receipt_file）"),
    "run.resume": (lambda s, e, by: s.resume_run(e["run_id"], by, e["payload"].get("note", "")), ("run_id",), "续跑"),
    "run.link": (lambda s, e, by: s.link_run(e["run_id"], e["task_id"], by), ("run_id", "task_id"), "把既有 Run 关联到任务"),
    "ref.add": (_h_ref_add, ("payload.object_type", "payload.object_id", "payload.kind", "payload.ref"), "加证据引用"),
    "order.sync": (None, ("payload.order_dir",), "同步一张派单（source=dispatch；幂等键由文件内容决定）"),
    "note": (None, (), "只留痕，不改状态"),
}
PURPOSE_TYPES = {"goal.create", "goal.authorize", "goal.complete", "goal.cancel", "task.unblock", "task.accept", "task.complete",
                 "task.cancel"}   # 常见的“要常浩定”的动作；其余类型 user 也能做，规则仍由 Store 判


def _object(e):
    typ = e["type"]
    if typ.startswith("goal."):
        return "goal", e.get("goal_id")
    if typ.startswith("task."):
        return "task", e.get("task_id")
    if typ.startswith("run."):
        return "run", e.get("run_id")
    if typ == "ref.add":
        p = e.get("payload") or {}
        return p.get("object_type"), p.get("object_id")
    for k, t in (("run_id", "run"), ("task_id", "task"), ("goal_id", "goal")):
        if e.get(k):
            return t, e[k]
    return None, None


def normalize(env):
    """校验并规范化信封：来源与角色要配、user 必须带可核实的消息指针、正文只留哈希、算出幂等键。不碰库、不联网。"""
    if not isinstance(env, dict):
        raise EventError("信封必须是 JSON 对象")
    e = {k: v for k, v in env.items() if k not in BODY_FIELDS}
    source = e.get("source")
    if source not in SOURCES:
        raise EventError(f"source 只能是 {' / '.join(SOURCES)}")
    typ = e.get("type")
    if typ not in TYPES:
        raise EventError(f"type 不认识：{typ!r}；可用：{', '.join(TYPES)}")
    for k in ("identity", "payload"):          # P2：类型不对给结构化拒绝，不漏 AttributeError / TypeError
        if e.get(k) is not None and not isinstance(e[k], dict):
            raise EventError(f"{k} 要是 JSON 对象")
    if e.get("refs") is not None and (not isinstance(e["refs"], list) or not all(isinstance(r, dict) for r in e["refs"])):
        raise EventError("refs 要是对象数组（每项 kind / ref / url / note）")
    ident = {k: v for k, v in (e.get("identity") or {}).items() if k not in DROP_IDENTITY}
    role = ident.get("role") or DEFAULT_ROLE[source]
    if role not in T.ACTORS:
        raise EventError(f"identity.role 只能是 {'/'.join(T.ACTORS)}（source=agent 必须写明是 claude / codex / hermes）")
    if source == "user" and role != "user":
        raise EventError("source=user 的事件角色只能是 user")
    if source != "user" and role == "user":
        raise EventError("只有 source=user 的事件才可能是 user 角色：Agent、台账、外部、系统事件不能以 user 身份进来")
    if role == "user" and (ident.get("method") != I.METHOD or not ident.get("message_id")):
        raise EventError("user 事件必须带 identity.method=feishu_message 和 identity.message_id，由状态库回读飞书核实；文本里说的身份不算")
    payload = dict(e.get("payload") or {})
    sha = None
    for holder in (env, e.get("payload") or {}):
        for k in BODY_FIELDS:
            v = holder.get(k)
            if isinstance(v, str) and sha is None:
                sha = hashlib.sha1(v.encode("utf-8")).hexdigest()[:16]
            payload.pop(k, None)
    occurred = e.get("occurred_at")
    if occurred is not None and not isinstance(occurred, str):
        raise EventError("occurred_at 要是 ISO 字符串")
    out = {"source": source, "db_source": SOURCES[source], "source_ref": str(e.get("source_ref") or ""),
           "event_id": e.get("event_id"), "type": typ, "occurred_at": occurred,
           "goal_id": e.get("goal_id"), "task_id": e.get("task_id"), "run_id": e.get("run_id"),
           "payload": payload, "refs": list(e.get("refs") or []),
           "identity": {"role": role, "method": ident.get("method"), "message_id": ident.get("message_id")},
           "text_sha1": sha}
    for need in TYPES[typ][1]:
        holder, key = (payload, need[8:]) if need.startswith("payload.") else (out, need)
        if holder.get(key) in (None, "", []):
            raise EventError(f"{typ} 缺字段：{need}")
    if typ == "order.sync":
        if source != "dispatch":
            raise EventError("order.sync 只能来自 source=dispatch")
        out["idem_key"] = None
    else:
        if not isinstance(out["event_id"], str) or not out["event_id"].strip():
            raise EventError("事件必须带稳定的 event_id（同一件事再来要是同一个 id）")
        out["idem_key"] = f"evt:{source}:{out['event_id'].strip()}"
    out["object_type"], out["object_id"] = _object(out)
    return out


def _brief(r):
    if isinstance(r, dict):
        out = {}
        for k in ("id", "status", "applied", "task_status", "verdict", "run", "retries", "duplicate_of", "result"):
            if k in r:
                v = r[k]
                out[k] = v["id"] if isinstance(v, dict) and "id" in v else v
        return out or {k: r[k] for k in list(r)[:4]}
    return r


def _reject(reason):
    def handler(_s):
        raise T.NotAuthorized(reason)
    return handler


REJECTED_BEFORE_HINT = "这个 event_id 先前已被拒绝并留痕，不能复用：要重新授权，请常浩再发一条新的消息、用新的 event_id"


def _retryable(e, key, reason):
    """P1-2 临时失败：不记事件、不占消息，同一 event_id 稍后可以原样重试。"""
    return {"ok": False, "type": e["type"], "event_key": key, "applied": False, "retryable": True, "recorded": False, "reason": reason}


def ingest(store, env, fetch=None, now=None, owner=None, owner_chat_id=None):
    """接入一个信封。重复（同一幂等键）直接按重复记、不再核实身份；user 事件先回读飞书核实并在同一事务里占用消息，
    再执行动作；动作被 Store 拒绝时事件记为 applied=0 并保留原因，状态和消息占用一起回滚。
    三种结果（P1-2，ChatGPT 评审 2026-10-29）：
    · 临时失败（本机配置缺失、回读飞书失败 / 出错）：不记事件，返回 retryable=True，同一 event_id 可重试；
    · 永久拒绝（不是本人、群聊、过时、重放、Store 拒绝）：记 applied=0，返回 rejected；
    · 同一 event_id 再来：返回 duplicate_of；若首次是被拒绝的，另带 rejected_before=True 与 hint——不再核实、不执行，要新消息。
    fetch / now / owner / owner_chat_id 只给测试注入；生产走 twin_identity.lark_fetch 与本机受控配置。"""
    e = normalize(env)
    if e["type"] == "order.sync":
        st = store.sync_order(e["payload"]["order_dir"])
        return {"ok": True, "type": "order.sync", "applied": bool(st["created"] or st["updated"]), **st}
    key = e["idem_key"]
    ident = e["identity"]
    by = ident["role"]
    record = {"source_ref": e["source_ref"], "event_id": e["event_id"], "type": e["type"], "occurred_at": e["occurred_at"],
              "payload": {k: (T.clip_receipt(v) if k == "receipt" else v) for k, v in e["payload"].items()},
              "identity": {"role": by, "method": ident["method"], "message_id": ident["message_id"], "verified": None},
              "text_sha1": e["text_sha1"]}
    common = dict(actor=by, payload=record, object_type=e["object_type"], object_id=e["object_id"], evidence=e["source_ref"])
    seen = store.conn.execute("SELECT 1 FROM events WHERE idem_key=?", (key,)).fetchone()
    if seen:
        out = store.ingest_event(e["db_source"], "in." + e["type"], key, **common)   # 只计一次重复，不调 handler、不核实身份
        if not out.get("first_applied"):
            out["rejected_before"] = True
            out["hint"] = REJECTED_BEFORE_HINT
        return {"ok": True, "type": e["type"], "event_key": key, **out}
    if by == "user":
        try:
            owner = owner if owner is not None else T.owner_open_id()
            chat = owner_chat_id if owner_chat_id is not None else T.owner_chat_id()
        except T.StateError as err:
            return _retryable(e, key, f"本机配置读不了：{err}")
        if not owner:
            return _retryable(e, key, T.OWNER_HINT)
        v = I.verify_message(ident["message_id"], owner, fetch=fetch, now=now, owner_chat_id=chat)
        if not v["ok"]:
            if v.get("retryable"):
                return _retryable(e, key, f"身份核实暂时做不了：{v['reason']}")
            out = store.ingest_event(e["db_source"], "in." + e["type"], key, handler=_reject(f"身份核实不通过：{v['reason']}"), **common)
            return {"ok": True, "type": e["type"], "event_key": key, **out}
        record["identity"]["verified"] = {"method": v["method"], "age_s": v["age_s"], "chat": v["chat"]}

    def handler(s):
        if by == "user":
            s.claim_message(ident["message_id"], purpose=e["type"])
        fn = TYPES[e["type"]][0]
        result = fn(s, e, by) if fn else None
        obj_type, obj_id = e["object_type"], e["object_id"]
        if obj_id is None and isinstance(result, dict):
            run = result.get("run")
            obj_id = result.get("id") or (run.get("id") if isinstance(run, dict) else None)
        for r in e["refs"]:
            if obj_type and obj_id and isinstance(r, dict) and r.get("kind") and r.get("ref"):
                s.add_ref(obj_type, obj_id, r["kind"], r["ref"], r.get("url", ""), r.get("note", ""), by)
        return _brief(result)

    out = store.ingest_event(e["db_source"], "in." + e["type"], key, handler=handler, **common)
    return {"ok": True, "type": e["type"], "event_key": key, **out}


# ---------- 命令行 ----------
def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False, default=str))
    sys.exit(0 if ok else 1)


def _load(path):
    try:
        raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        return json.loads(raw)
    except (OSError, ValueError) as e:
        done(False, f"读不了信封 {path}：{e}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help=f"状态库路径，默认 $DT_STATE_DB 或 {T.DEFAULT_DB}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ingest", "validate"):
        sub.add_parser(name).add_argument("--file", required=True, help="信封 JSON 文件；- 读标准输入")
    sub.add_parser("types")
    a = ap.parse_args(argv)
    if a.cmd == "types":
        done(True, "动作类型", types={k: {"need": list(v[1]), "note": v[2]} for k, v in TYPES.items()},
             sources=SOURCES, roles=list(T.ACTORS))
    env = _load(a.file)
    try:
        if a.cmd == "validate":
            e = normalize(env)
            done(True, f"信封合规：{e['type']}（{e['source']} / {e['identity']['role']}）", envelope=e)
        with T.Store(a.db) as s:
            out = ingest(s, env)
        rest = {k: v for k, v in out.items() if k != "ok"}
        if out.get("retryable"):
            done(False, f"暂时失败，事件没有记录，可重试（同一 event_id 原样再来即可）：{out['reason']}", **rest)
        if out.get("rejected"):
            done(False, f"事件已留痕但被拒绝（永久）：{out['rejected']}；这个 event_id 不能再用，重新授权要新的消息和新的 event_id", **rest)
        if not out.get("applied") and out.get("duplicate_of"):
            if out.get("rejected_before"):
                done(False, f"同一 event_id 先前已被拒绝，这次不再核实、不执行（原因：{out.get('result')}）；要重新授权请用新的消息和新的 event_id",
                     **rest)
            done(True, "同一事件已处理过，不重复应用", **rest)
        done(True, f"事件已接入：{out['type']}", **rest)
    except T.StateError as e:
        done(False, str(e), error=type(e).__name__)


if __name__ == "__main__":
    main()
