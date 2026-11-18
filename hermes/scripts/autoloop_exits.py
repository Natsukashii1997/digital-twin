#!/usr/bin/env python3
"""出口账本：受保护副作用的写前账本（批 0D 恢复门，#195；方案 docs/agent-autoloop-底座-方案.md 5.3「写前账本」、
5.5 不变量 3「恢复闸」；设计 docs/恢复门-0D-设计.md）。

推分支、开 PR、发飞书、合并这几种撤不回或会重复的动作，一律「先记账、再动手、再记结果」：
  executing     已记下要做、正在做（记着进程号和进程启动时间、开机时间）
  done          确认做成（有结果或回读证据）            —— 同一幂等键再来，直接回原结果，不再做
  failed        确认没做成（动作明确报没发出去，或回读证明没有）—— 可以重做（新的一次尝试）
  unknown       结果不明：超时、断网、回应读不懂、进程被强杀（executing 而进程已不在 / 换了开机）
  unreconciled  对账也核不清（UNRECONCILED）：外部系统可达，但看到的情况对不上，只有常浩能定（settle --confirm）
不靠异常捕获判断强杀：每个入口和恢复闸都会扫「executing 而做的进程已经不在」的条目，一律改成 unknown。

**冻结**：同一冻结范围（scope：task:<单号>、repo:<owner/name>、card:<卡片号>……）只要有一条 executing（进程还在）、
unknown 或 unreconciled，这个范围里的任何新动作都拒绝（同一时刻也只能有一个在做，这就是串行化）。范围按秘书代批规格第 7 节：
回答结果不明冻结这张任务，合并结果不明冻结这个仓库。冻结不是一张单独的表，是账本状态推出来的，所以不会和账本不一致。

**结果不明从不重做**：同一范围再有动作要做时，先只读对一次这个范围的账；只读回读外部系统（GitHub 分支 / PR / 合并、飞书私聊里的消息）对账，凭证据记成 done 或 failed；
外部系统连不上就保持 unknown 等下次；连得上但对不上就记 unreconciled，交常浩。

**和任务凭证串行**（0C 审查 P1-07「核凭证与推分支之间的竞态」）：带 task 的动作，在凭证库的写锁里现读凭证判定、并在
同一把锁下把 executing 落盘，才放锁动手。冻结、撤销也要这把写锁，所以两者有先后：冻结先提交 → 判定失败、不动手；
executing 先落盘 → 这个动作已经开始（撤不回），冻结的输出里会列出它（autoloop_grants.freeze 的 in_flight），结果出来后照常对账；
之后的步骤（例如推完分支再开 PR）重新判定，冻结后一律拒绝。

**恢复闸**：每个动作先过 autoloop_recovery.ensure_open()：开机、睡醒、中台重启或换版本、出现结果不明之后，对账走完才放行。

账本放在**运行账号自己的** DigitalTwin/autoloop/exits.db（700 / 600）；不写死主账号路径，0E-2 整体搬进 dt-gate。
本脚本只读外部系统做对账，不做任何外部写；外部写由调用方传进来的 do() 做。

回答中台卡片不经本账本：本人点击的写前账本在 autoloop_answer（claims：queued → inflight → answered / unknown，
从不重答），秘书回答在 0B-4c 的秘书账本；两者都过同一道恢复闸。派单事件的写前账本在 autoloop_grants（hub_events，
中台按同一来源同一 id 去重，补发安全）。

  autoloop_exits.py show [--json]                       只读：账本、冻结的范围
  autoloop_exits.py settle --key K --result done|failed --note 说明 --confirm   常浩核实后手动结算一条核不清的
环境变量：DT_EXITS_DB、DT_GH、DT_LARK_CLI。不调用 AI。
"""
import argparse
import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_grants as G  # noqa: E402

KINDS = ("push_branch", "open_pr", "send_feishu", "merge")
KEY = re.compile(r"^(push_branch|open_pr|send_feishu|merge):[\w./@:+-]{1,300}$")
SCOPE = re.compile(r"^(task|repo|card|chat):[\w./@:+-]{1,200}$")
UNSETTLED = ("executing", "unknown", "unreconciled")
HARD_STALE = dt.timedelta(minutes=30)       # executing 超过这么久，不管进程在不在都当结果不明（防进程号复用、卡死）
SEND_SETTLE = dt.timedelta(minutes=5)       # 飞书消息列表可能滞后：结果不明的发送这么久以后还找不到，才认定没发出去
SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS exits (key TEXT PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL, target TEXT NOT NULL,
  task_id TEXT, generation INTEGER,
  state TEXT NOT NULL CHECK (state IN ('executing','done','failed','unknown','unreconciled')),
  pid INTEGER, proc TEXT, boot TEXT, attempts INTEGER NOT NULL DEFAULT 1, outcome TEXT, evidence TEXT,
  created_at TEXT NOT NULL, started_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS exits_scope ON exits (scope, state);
CREATE TABLE IF NOT EXISTS exit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, at TEXT NOT NULL,
  state TEXT NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS gate (id INTEGER PRIMARY KEY CHECK (id = 1), epoch TEXT, opened_at TEXT,
  dirty INTEGER NOT NULL DEFAULT 1, dirty_reason TEXT, report TEXT)
"""


class Refused(Exception):
    """不动手：说人话的原因（闸关着、范围冻结、凭证不让、上一次结果不明）。"""


class Definite(Exception):
    """do() 用它报「确定没做成」（请求根本没发出去、对方明确拒绝）：记 failed，可以重做。其他异常一律当结果不明。"""


class Unreachable(Exception):
    """对账时外部系统连不上：保持 unknown，下次再对。"""


def now_tz():
    return G.now_tz()


def iso(t):
    return G.iso(t)


# ---------- 库 ----------
def db_path():
    return Path(os.environ.get("DT_EXITS_DB") or G.SUPPORT / "autoloop/exits.db").expanduser()


@contextlib.contextmanager
def db(write=True):
    """write=True：BEGIN IMMEDIATE，判定和写入之间没有空隙。库不存在就新建（700 目录、600 文件）。"""
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    if not p.exists():
        os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    con = sqlite3.connect(p, isolation_level=None, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        try:
            v = con.execute("PRAGMA user_version").fetchone()[0]
            if v != SCHEMA_VERSION:
                if v or con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone():
                    raise Refused(f"出口账本结构是第 {v} 版，本脚本要第 {SCHEMA_VERSION} 版；没有自动迁移")
                for stmt in SCHEMA.split(";"):
                    con.execute(stmt)
                con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            yield con
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


def log(con, key, state, note=None, at=None):
    con.execute("INSERT INTO exit_log (key, at, state, note) VALUES (?,?,?,?)", (key, iso(at or now_tz()), state, note))


def mark_dirty(con, reason):
    """出现结果不明：恢复闸关上，下一个动作前先对账（autoloop_recovery.ensure_open）。dirty 是计数：对账只清掉
    它开始时看到的那个数，对账期间又出现的结果不明会让闸继续关着、再对一次。"""
    con.execute("INSERT INTO gate (id, dirty, dirty_reason) VALUES (1, 1, ?) "
                "ON CONFLICT(id) DO UPDATE SET dirty = gate.dirty + 1, dirty_reason = excluded.dirty_reason", (reason[:300],))


# ---------- 进程与开机 ----------
def boot_id():
    """这次开机的标识：macOS 读 kern.boottime，Linux（CI）读 /proc 的 boot_id；都读不到返回 '?'（当成进程不在）。"""
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", "kern.boottime"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"sec = (\d+)", out)
        if m:
            return m.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() or "?"
    except OSError:
        return "?"


def proc_start(pid):
    """进程启动时间（ps -o lstart）；进程不在返回 None。和进程号一起判断「还是不是那个进程」。"""
    try:
        r = subprocess.run(["/bin/ps", "-o", "lstart=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return r.stdout.strip() or None


def alive(row, now, boot=None):
    """executing 的条目，做它的进程是不是还在做：同一次开机、进程号在、启动时间一致、没超过 HARD_STALE。"""
    if row["boot"] != (boot or boot_id()) or row["boot"] == "?":
        return False
    if now - G.parse_iso(row["started_at"]) > HARD_STALE:
        return False
    return row["proc"] is not None and proc_start(row["pid"]) == row["proc"]


def sweep(con, now, boot=None):
    """把「executing 而进程已不在」的条目改成 unknown（强杀、断电、睡死）。返回改了的幂等键。"""
    changed = []
    for r in con.execute("SELECT * FROM exits WHERE state = 'executing'").fetchall():
        if not alive(r, now, boot):
            con.execute("UPDATE exits SET state = 'unknown', updated_at = ? WHERE key = ? AND state = 'executing'",
                        (iso(now), r["key"]))
            log(con, r["key"], "unknown", "做它的进程已不在（强杀、重启或超时），结果不明，不重做", now)
            changed.append(r["key"])
    if changed:
        mark_dirty(con, f"强杀后发现 {len(changed)} 条结果不明")
    return changed


def frozen_scopes(con):
    return sorted({r[0] for r in con.execute(
        f"SELECT scope FROM exits WHERE state IN ({','.join('?' * len(UNSETTLED))})", UNSETTLED)})


# ---------- 动手 ----------
def _begin(key, kind, scope, target, task, now):
    """在出口账本（和凭证库，带 task 时）的写锁里判定，落盘 executing。返回 None 表示可以动手，否则返回已有结果。"""
    boot, me = boot_id(), os.getpid()
    proc = proc_start(me)
    with db() as con:                                      # 扫描单独提交：后面判定拒绝时不会把它一起回滚
        sweep(con, now, boot)
    with db() as con:
        row = con.execute("SELECT * FROM exits WHERE key = ?", (key,)).fetchone()
        if row and row["state"] == "done":
            return {"done": True, "again": True, **json.loads(row["outcome"] or "{}")}
        if row and row["state"] in UNSETTLED:
            raise Refused(f"这个动作上一次结果不明或还在进行（{key}），先对账，不重做")
        busy = con.execute(f"SELECT key, state FROM exits WHERE scope = ? AND key != ? AND state IN "
                           f"({','.join('?' * len(UNSETTLED))})", (scope, key, *UNSETTLED)).fetchone()
        if busy:
            word = "正在进行" if busy["state"] == "executing" else "结果不明，已冻结"
            raise Refused(f"{scope} 里有别的动作{word}（{busy['key']}），先等它或对账")
        t = iso(now)
        if row:                                            # 上一次确认没做成：重做，记一次新的尝试
            con.execute("UPDATE exits SET state = 'executing', pid = ?, proc = ?, boot = ?, attempts = attempts + 1, "
                        "target = ?, outcome = NULL, evidence = NULL, started_at = ?, updated_at = ? WHERE key = ?",
                        (me, proc, boot, json.dumps(target, sort_keys=True), t, t, key))
        else:
            con.execute("INSERT INTO exits (key, kind, scope, target, task_id, generation, state, pid, proc, boot, "
                        "created_at, started_at, updated_at) VALUES (?,?,?,?,?,?, 'executing', ?,?,?,?,?,?)",
                        (key, kind, scope, json.dumps(target, sort_keys=True), task and task[0], task and task[2],
                         me, proc, boot, t, t, t))
        log(con, key, "executing", None, now)
    return None


def _finish(key, state, outcome=None, note=None):
    with db() as con:
        con.execute("UPDATE exits SET state = ?, outcome = ?, updated_at = ? WHERE key = ? AND state = 'executing' "
                    "AND pid = ?", (state, json.dumps(outcome or {}, ensure_ascii=False, sort_keys=True),
                                    iso(now_tz()), key, os.getpid()))
        log(con, key, state, note)
        if state == "unknown":
            mark_dirty(con, f"{key} 结果不明")


def guard(kind, key, scope, target, do, task=None, now=None):
    """做一个受保护的副作用。do() 返回结果（dict）；do() 抛 Definite = 确定没做成；其他异常 = 结果不明（原样抛出）。
    task=(单号, 动作, 代次) 时在凭证库写锁里现读凭证（和冻结、撤销串行）。同一幂等键已做成的，直接回原结果。"""
    if kind not in KINDS or not KEY.match(key or "") or not key.startswith(kind + ":"):
        raise Refused(f"幂等键或动作不对：{key}")
    if not SCOPE.match(scope or ""):
        raise Refused(f"冻结范围写法不对：{scope}")
    import autoloop_recovery as R
    R.ensure_open()
    with db(write=False) as con:                           # 这个范围冻着：先只读对一次这个范围的账（网络恢复后自己解开）
        stuck = con.execute(f"SELECT 1 FROM exits WHERE scope = ? AND state IN ({','.join('?' * len(UNSETTLED))}) "
                            "LIMIT 1", (scope, *UNSETTLED)).fetchone()
    if stuck:
        reconcile(scope=scope)
    now = now or now_tz()
    if task:
        task_id, action, generation = task
        with G.tx() as gcon:                               # 凭证库写锁：冻结、撤销进不来
            why = G.check_row(gcon.execute("SELECT * FROM grants WHERE task_id = ?", (task_id,)).fetchone(),
                              action, generation, now, G.voided_of(gcon, task_id))
            if why:
                raise Refused(f"{why}（{action}）")
            again = _begin(key, kind, scope, target, task, now)    # executing 在放凭证库锁之前落盘
    else:
        again = _begin(key, kind, scope, target, None, now)
    if again:
        return again
    try:
        out = do()
    except Definite as e:
        _finish(key, "failed", {"why": str(e)[:300]}, f"确定没做成：{str(e)[:200]}")
        raise
    except BaseException as e:                             # 超时、断网、读不懂、被中断：结果不明，不重做
        _finish(key, "unknown", {"why": f"{type(e).__name__}: {str(e)[:200]}"}, "结果不明")
        raise
    _finish(key, "done", out or {})
    return {"done": True, "again": False, **(out or {})}


def in_flight(scope):
    """只读：这个范围里正在进行或结果不明的动作（冻结、撤销时报给常浩）。"""
    with db(write=False) as con:
        return [dict(key=r["key"], state=r["state"]) for r in con.execute(
            f"SELECT key, state FROM exits WHERE scope = ? AND state IN ({','.join('?' * len(UNSETTLED))})",
            (scope, *UNSETTLED))]


# ---------- 对账（只读外部系统） ----------
def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise Unreachable(f"{Path(cmd[0]).name} 跑不起来或超时（{type(e).__name__}）")
    if r.returncode != 0:
        raise Unreachable(f"{Path(cmd[0]).name} 失败：{(r.stderr or r.stdout).strip()[:200]}")
    return r.stdout


def read_push(t, row, now):
    """远端分支正好是这次的头提交 → 做成；远端没有这条分支 → 没做成；是别的提交 → 核不清。"""
    out = _run(["git", "-c", "core.hooksPath=/dev/null", "ls-remote", t["remote"], f"refs/heads/{t['branch']}"])
    sha = out.split()[0] if out.split() else None
    if sha == t["head"]:
        return "done", {"remote_head": sha}
    if sha is None:
        return "failed", {"remote_head": None}
    return None, {"remote_head": sha}


def _gh():
    return os.environ.get("DT_GH", "gh")


def read_pr(t, row, now):
    """这条分支上有一个头提交是这次的、目标 main 的 PR → 做成；一个也没有 → 没做成；多个或对不上 → 核不清。"""
    prs = json.loads(_run([_gh(), "pr", "list", "-R", t["github"], "--head", t["branch"], "--state", "all",
                           "--json", "number,url,state,headRefOid,baseRefName"]) or "[]")
    ours = [p for p in prs if p.get("headRefOid") == t["head"] and p.get("baseRefName") == "main"]
    if len(ours) == 1 and len(prs) == 1:
        return "done", {"pr": ours[0].get("url"), "number": ours[0].get("number")}
    if not prs:
        return "failed", {"prs": 0}
    return None, {"prs": len(prs), "ours": len(ours)}


def read_merge(t, row, now):
    """PR 已合并且合并时的头提交是这次的 → 做成（记合并提交）；还开着、头提交没变 → 没做成；其他 → 核不清。"""
    p = json.loads(_run([_gh(), "pr", "view", str(t["number"]), "-R", t["github"], "--json",
                         "state,headRefOid,mergeCommit"]) or "{}")
    if p.get("state") == "MERGED" and p.get("headRefOid") == t["head"] and (p.get("mergeCommit") or {}).get("oid"):
        return "done", {"merge_commit": p["mergeCommit"]["oid"]}
    if p.get("state") == "OPEN" and p.get("headRefOid") == t["head"]:
        return "failed", {"state": "OPEN"}
    return None, {"state": p.get("state"), "head": p.get("headRefOid")}


def read_send(t, row, now):
    """私聊里找带这次标记的机器人消息（标记是卡片令牌这类随机串）：找到一条 → 做成（记消息号）；
    动作开始 5 分钟以后还找不到 → 没做成；更早 → 结果仍不明（消息列表可能滞后）；找到多条 → 核不清。"""
    from tidings_weekly import SendError, lark
    start = G.parse_iso(row["started_at"]) - dt.timedelta(minutes=1)
    try:
        data = lark(["im", "+chat-messages-list", "--chat-id", t["chat"], "--start", start.isoformat(),
                     "--end", (now + dt.timedelta(minutes=1)).isoformat(), "--order", "asc", "--page-all"], "对账")
    except SendError as e:
        raise Unreachable(str(e))
    hits = [m for m in (data.get("messages") or [])
            if t["marker"] in json.dumps(m, ensure_ascii=False)]
    if len(hits) == 1:
        return "done", {"message_id": hits[0].get("message_id")}
    if len(hits) > 1:
        return None, {"hits": len(hits)}
    if now - G.parse_iso(row["started_at"]) < SEND_SETTLE:
        raise Unreachable("消息列表可能还没更新，过几分钟再对")
    return "failed", {"hits": 0}


READERS = {"push_branch": read_push, "open_pr": read_pr, "merge": read_merge, "send_feishu": read_send}


def reconcile(readers=None, now=None, scope=None):
    """对账：先把强杀留下的 executing 改成 unknown，再逐条只读回读外部系统（scope 给了就只对这个范围）。
    返回每条的处理。不做任何外部写。"""
    readers = {**READERS, **(readers or {})}
    now = now or now_tz()
    with db() as con:
        sweep(con, now)
        rows = [dict(r) for r in con.execute("SELECT * FROM exits WHERE state IN ('unknown', 'unreconciled') "
                                             "AND (? IS NULL OR scope = ?) ORDER BY created_at", (scope, scope))]
    notes = []
    for r in rows:
        try:
            verdict, evidence = readers[r["kind"]](json.loads(r["target"]), r, now)
        except Unreachable as e:
            notes.append({"key": r["key"], "state": r["state"], "note": f"外部系统连不上，下次再对：{e}"})
            continue
        except Exception as e:                             # 回读本身出错（记录缺字段、回应读不懂）：保持原状，不猜
            notes.append({"key": r["key"], "state": r["state"], "note": f"回读出错，保持原状：{type(e).__name__}"})
            continue
        new = verdict or "unreconciled"
        if new == r["state"]:
            notes.append({"key": r["key"], "state": new, "note": "仍核不清，交常浩"})
            continue
        with db() as con:
            cur = con.execute("UPDATE exits SET state = ?, evidence = ?, outcome = COALESCE(?, outcome), updated_at = ? "
                              "WHERE key = ? AND state = ?",
                              (new, json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                               json.dumps(evidence, ensure_ascii=False, sort_keys=True) if new == "done" else None,
                               iso(now), r["key"], r["state"]))
            if cur.rowcount:
                log(con, r["key"], new, f"对账：{json.dumps(evidence, ensure_ascii=False)[:200]}", now)
        notes.append({"key": r["key"], "state": new, "evidence": evidence})
    return notes


def settle(key, result, note, confirm=False):
    """常浩核实后手动结算一条核不清的（unreconciled）；结果不明的不许手动跳过对账。"""
    if not confirm:
        raise Refused("手动结算要常浩明确同意：加 --confirm")
    if result not in ("done", "failed") or not (note or "").strip():
        raise Refused("结果只能是 done 或 failed，并写明依据")
    with db() as con:
        cur = con.execute("UPDATE exits SET state = ?, evidence = ?, updated_at = ? WHERE key = ? AND state = 'unreconciled'",
                          (result, json.dumps({"manual": note}, ensure_ascii=False), iso(now_tz()), key))
        if cur.rowcount != 1:
            raise Refused("只有「核不清」的条目能手动结算；结果不明的先对账")
        log(con, key, result, f"常浩确认：{note[:200]}")
    return {"key": key, "state": result}


def show():
    with db(write=False) as con:
        rows = [dict(r) for r in con.execute("SELECT key, kind, scope, state, attempts, outcome, evidence, updated_at "
                                             "FROM exits ORDER BY updated_at DESC LIMIT 200")]
        return {"frozen": frozen_scopes(con), "exits": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show").add_argument("--json", action="store_true")
    s = sub.add_parser("settle")
    s.add_argument("--key", required=True)
    s.add_argument("--result", required=True)
    s.add_argument("--note", required=True)
    s.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    try:
        out = show() if a.cmd == "show" else settle(a.key, a.result, a.note, a.confirm)
    except Refused as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
