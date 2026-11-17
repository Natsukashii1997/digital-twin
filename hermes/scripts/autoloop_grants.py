#!/usr/bin/env python3
"""任务凭证与派单网关（批 0B-1，#195；方案 docs/agent-autoloop-底座-方案.md 5.1，规格 docs/秘书代批-规格.md §2、§5、§8）。

写入类派单是 D 档：只有常浩本人在私聊里批准，才签发一张任务凭证。凭证写明仓库、范围（五要素原文的哈希）、执行方、
允许的动作（只有 edit / push_branch / open_pr）、到期时间、状态和**执行代次**。秘书（猛鬼兽）不签凭证、不往凭证里加权限。

批准分两步，把常浩的话绑定到这一单的具体内容（防止模型拼参数）：
  1. propose：猛鬼兽整理好五要素，本脚本算出确认码（仓库、执行方、动作、到期、范围原文的 SHA-256 前 6 位），
     猛鬼兽把五要素和「派单 <确认码>」发给常浩；
  2. approve：常浩在私聊回「派单 <确认码>」，本脚本用 twin_identity 回读核验（常浩本人、私聊、10 分钟内、消息没用过），
     口令以回读原文为准，确认码对得上一条没过期的提议，才签凭证（代次 1）并发第一个派单事件。

网关只有两个入口往中台发 hermes 来源的事件：approve（本人批准建任务）和 resume（凭证有效时续接：重派、返修、更正）。
冻结、撤销是安全方向，本机调用即可；解冻（恢复）要常浩私聊「恢复 <单号>」，恢复时代次 +1，被作废的旧代次永久失效。

工人（dt-worker）只能读共享目录里的状态镜像 /Users/Shared/dt-autoloop/grants/status.json（状态、代次、到期、动作），
读不到凭证库，也改不了镜像。check() 是所有出口共用的判定：存在、有效、没过期、动作在列、代次是当前的。

  autoloop_grants.py propose --repo dt-sandbox --executor claude --scope-file 五要素.md [--hours 12]
  autoloop_grants.py approve --message-id om_xxx          常浩回了「派单 <确认码>」后由猛鬼兽调用
  autoloop_grants.py resume-dispatch --task T --kind redispatch|rework|correction --reason 原因
  autoloop_grants.py freeze --task T --reason 原因 | revoke --task T [--reason] | complete --task T
  autoloop_grants.py unfreeze --message-id om_xxx        常浩私聊「恢复 <单号>」
  autoloop_grants.py check --task T --action edit --generation 1
  autoloop_grants.py show [--task T]
环境变量：DT_GRANTS_DB（默认 DigitalTwin/autoloop/grants.db）、DT_GRANTS_STATUS（共享镜像）、DT_HUB（中台地址）、
DT_HERMES_SOURCE_KEY_FILE（hermes 来源密钥文件，600；0B-2 建）。
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import twin_identity as I  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
SUPPORT = Path("~/Library/Application Support/DigitalTwin").expanduser()
ROSTER = Path(os.environ.get("DT_ROSTER") or HERE.parents[1] / "projects.json")
ACTIONS = ("edit", "push_branch", "open_pr")
EXECUTORS = ("claude", "codex")
KINDS = ("redispatch", "rework", "correction")
PROPOSAL_TTL = dt.timedelta(minutes=30)
MAX_HOURS = 72
APPROVE_TEXT = re.compile(r"^派单\s*([0-9a-f]{6})$")
UNFREEZE_TEXT = re.compile(r"^恢复\s*(t-\d{8}-[0-9a-f]{6})$")
TASK_ID = re.compile(r"^t-\d{8}-[0-9a-f]{6}$")
REPO_NAME = re.compile(r"^[\w.-]{1,100}$")
MAX_SCOPE = 3000
SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
  task_id TEXT PRIMARY KEY, repo TEXT NOT NULL, github TEXT NOT NULL, scope_text TEXT NOT NULL, scope_hash TEXT NOT NULL,
  executor TEXT NOT NULL, rule TEXT NOT NULL, actions TEXT NOT NULL, valid_until TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','frozen','revoked','done')),
  generation INTEGER NOT NULL, generation_started_at TEXT NOT NULL,
  approval_message TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voided_generations (task_id TEXT NOT NULL, generation INTEGER NOT NULL, at TEXT NOT NULL,
  reason TEXT NOT NULL, PRIMARY KEY (task_id, generation));
CREATE TABLE IF NOT EXISTS grant_log (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, at TEXT NOT NULL,
  what TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (code TEXT PRIMARY KEY, repo TEXT NOT NULL, executor TEXT NOT NULL,
  scope_text TEXT NOT NULL, actions TEXT NOT NULL, valid_until TEXT NOT NULL, created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL, task_id TEXT);
CREATE TABLE IF NOT EXISTS used_messages (message_id TEXT PRIMARY KEY, purpose TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hub_events (event_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, rule TEXT NOT NULL,
  executor TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL, hub_event INTEGER, at TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0);
"""


class Rejected(Exception):
    """拒绝：说人话的原因，不带身份值、不带密钥。"""


# ---------- 路径、时间 ----------
def db_path():
    return Path(os.environ.get("DT_GRANTS_DB") or SUPPORT / "autoloop/grants.db").expanduser()


def status_path():
    return Path(os.environ.get("DT_GRANTS_STATUS") or "/Users/Shared/dt-autoloop/grants/status.json").expanduser()


def now_tz():
    return dt.datetime.now(TZ)


def iso(t):
    return t.astimezone(TZ).isoformat(timespec="seconds")


def parse_iso(s):
    return dt.datetime.fromisoformat(s)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- 库 ----------
@contextlib.contextmanager
def tx(path=None):
    """一个写事务：BEGIN IMMEDIATE 拿写锁，读和写在同一事务里，判定和状态变化之间没有空隙。"""
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    fresh = not p.exists()
    con = sqlite3.connect(p, isolation_level=None, timeout=30)
    if fresh:
        os.chmod(p, 0o600)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE")
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def log(con, task_id, what, now, **detail):
    con.execute("INSERT INTO grant_log (task_id, at, what, detail) VALUES (?,?,?,?)",
                (task_id, iso(now), what, json.dumps(detail, ensure_ascii=False, sort_keys=True)))


def grant_row(con, task_id):
    if not TASK_ID.match(task_id or ""):
        raise Rejected("单号格式不对（应为 t-日期-6 位）")
    row = con.execute("SELECT * FROM grants WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        raise Rejected(f"没有 {task_id} 这张任务凭证")
    return row


# ---------- 名册 ----------
def allowed_repos():
    """能派写入单的仓库：名册活跃项目里 write_allowed 不为 false、有 GitHub 的，加上 inventory 里标了 sandbox 的。"""
    r = json.loads(ROSTER.read_text(encoding="utf-8"))
    out = {}
    for p in r.get("projects", []):
        if p.get("github") and p.get("write_allowed", True) is not False and p.get("category") == "活跃":
            out[p["github"].split("/")[-1]] = p["github"]
    for p in r.get("inventory", []):
        if p.get("sandbox") and str(p.get("path", "")).startswith("github:"):
            gh = p["path"].split(":", 1)[1]
            out[gh.split("/")[-1]] = gh
    return out


def rule_name(repo, executor):
    """派单规则名（批 2 的规则生成器按同一写法生成）；凭证里记签发时的规则名，绑定时三者对上（规格 §5）。"""
    return f"dispatch-{repo}-{executor}"


# ---------- 判定（所有出口共用） ----------
def check_row(row, action, generation, now, voided=()):
    """返回 None 表示放行，否则返回拒绝原因。"""
    if row is None:
        return "没有这张任务凭证"
    if row["status"] != "active":
        return {"frozen": "凭证已冻结", "revoked": "凭证已撤销", "done": "任务已完成"}[row["status"]]
    if now >= parse_iso(row["valid_until"]):
        return "凭证已过期"
    if action not in json.loads(row["actions"]):
        return f"凭证不允许 {action}"
    if generation in voided:
        return f"第 {generation} 代已作废"
    if generation != row["generation"]:
        return f"代次不对（凭证现在是第 {row['generation']} 代，来的是第 {generation} 代）"
    return None


def check(task_id, action, generation, now=None, path=None):
    now = now or now_tz()
    with tx(path) as con:
        try:
            row = grant_row(con, task_id)
        except Rejected as e:
            return str(e)
        voided = {r[0] for r in con.execute("SELECT generation FROM voided_generations WHERE task_id = ?", (task_id,))}
        return check_row(row, action, generation, now, voided)


# ---------- 状态镜像（工人只读） ----------
def refresh_status(path=None, now=None):
    """事务提交之后再写镜像：镜像只反映已经落盘的状态。"""
    with tx(path) as con:
        write_status(con, now or now_tz())


def write_status(con, now):
    tasks = {}
    for r in con.execute("SELECT * FROM grants ORDER BY task_id"):
        voided = [v[0] for v in con.execute(
            "SELECT generation FROM voided_generations WHERE task_id = ? ORDER BY generation", (r["task_id"],))]
        tasks[r["task_id"]] = {"repo": r["repo"], "executor": r["executor"], "rule": r["rule"], "status": r["status"],
                               "generation": r["generation"], "voided_generations": voided,
                               "valid_until": r["valid_until"], "actions": json.loads(r["actions"]),
                               "scope_hash": r["scope_hash"]}
    doc = {"schema": 1, "updated_at": iso(now), "tasks": tasks}
    p = status_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


# ---------- 中台 ----------
def hub_url():
    return os.environ.get("DT_HUB", "http://127.0.0.1:3350")


def source_key():
    f = Path(os.environ.get("DT_HERMES_SOURCE_KEY_FILE") or SUPPORT / "autoloop/hermes-source.key").expanduser()
    try:
        if stat.S_IMODE(f.stat().st_mode) & 0o077:
            raise Rejected(f"hermes 来源密钥文件权限必须是 600：{f}")
        return f.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise Rejected("还没有 hermes 来源密钥（0B-2 建），事件没发；凭证已签，之后用 send-pending 补发")


def post_event(body):
    """发一个 hermes 来源的事件；返回中台给的事件号。同一 id 中台只记一次，所以补发安全。"""
    req = urllib.request.Request(hub_url().rstrip("/") + "/v1/events", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {source_key()}", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=15) as r:
            return json.loads(r.read().decode())["event"]
    except urllib.error.HTTPError as e:
        raise Rejected(f"中台拒收事件：HTTP {e.code}")
    except (OSError, ValueError, KeyError) as e:
        raise Rejected(f"连不上中台或回应不对：{type(e).__name__}")


def event_body(row, kind, key, reason=""):
    lines = [f"任务 {row['task_id']}（第 {row['generation']} 代）", f"仓库：{row['github']}", f"执行方：{row['executor']}",
             f"允许的动作：{'、'.join(json.loads(row['actions']))}", f"有效到：{row['valid_until']}"]
    if reason:
        lines.append(f"本次：{kind}，原因：{reason}")
    lines += ["", "五要素：", row["scope_text"]]
    return {"id": key, "type": f"dispatch.{row['repo']}.{row['executor']}.write", "subject": row["task_id"],
            "summary": f"{row['repo']} · {row['executor']} · {kind}", "detail": "\n".join(lines)[:4000]}


def record_event(con, row, kind, key, now):
    con.execute("INSERT OR IGNORE INTO hub_events (event_key, task_id, rule, executor, generation, kind, at) "
                "VALUES (?,?,?,?,?,?,?)", (key, row["task_id"], row["rule"], row["executor"], row["generation"], kind, iso(now)))


def send_pending(path=None, post=None):
    """把记了账、还没送到中台的事件发出去（同一 id 中台去重）。先记账后发，所以强杀后补发不会丢也不会多派。"""
    post = post or post_event
    with tx(path) as con:
        pending = [dict(r) for r in con.execute("SELECT * FROM hub_events WHERE sent = 0 ORDER BY at, event_key")]
        rows = {p["task_id"]: dict(grant_row(con, p["task_id"])) for p in pending}
    results = []
    for p in pending:
        row = rows[p["task_id"]]
        if row["status"] != "active" or row["generation"] != p["generation"]:
            results.append({"event_key": p["event_key"], "sent": False, "why": "凭证已不是这一代有效，作废不发"})
            with tx(path) as con:
                con.execute("UPDATE hub_events SET sent = -1 WHERE event_key = ?", (p["event_key"],))
            continue
        hub_event = post(event_body(row, p["kind"], p["event_key"]))
        with tx(path) as con:
            con.execute("UPDATE hub_events SET sent = 1, hub_event = ? WHERE event_key = ?", (hub_event, p["event_key"]))
        results.append({"event_key": p["event_key"], "sent": True, "hub_event": hub_event})
    return results


# ---------- 身份 ----------
def fetch_message(message_id):
    """回读一条消息并带正文；测试打桩这里。"""
    return I.lark_fetch(message_id, with_text=True)


def verify_owner(message_id, now):
    """常浩本人、私聊、10 分钟内（twin_identity 同一核验）；返回回读原文。"""
    import twin_state as T
    try:
        owner, chat = T.owner_open_id(), T.owner_chat_id()
    except T.StateError as e:
        raise Rejected(str(e))
    box = {}

    def fetch(mid):
        box["m"] = fetch_message(mid)
        return box["m"]

    v = I.verify_message(message_id, owner, fetch=fetch, now=now.astimezone(), owner_chat_id=chat)
    if not v["ok"]:
        raise Rejected(f"身份核实不通过：{v['reason']}")
    return re.sub(r"\s+", " ", (box["m"].get("text") or "")).strip()


def claim_message(con, message_id, purpose, now):
    row = con.execute("SELECT * FROM used_messages WHERE message_id = ?", (message_id,)).fetchone()
    if row:
        raise Rejected(f"这条飞书消息已经用过（{row['at']}，{row['purpose']}）：一条消息只换一次动作")
    con.execute("INSERT INTO used_messages (message_id, purpose, at) VALUES (?,?,?)", (message_id, purpose, iso(now)))


# ---------- 入口一：本人批准建任务 ----------
def confirm_code(repo, executor, actions, valid_until, scope_text):
    return sha256("\n".join([repo, executor, ",".join(actions), valid_until, scope_text]))[:6]


def propose(repo, executor, scope_text, hours=12, actions=ACTIONS, now=None, path=None):
    now = now or now_tz()
    if not REPO_NAME.match(repo or "") or repo not in allowed_repos():
        raise Rejected(f"{repo} 不在名册能派写入单的仓库里")
    if executor not in EXECUTORS:
        raise Rejected("执行方只能是 claude 或 codex")
    actions = tuple(a for a in ACTIONS if a in actions)
    if not actions or set(actions) - set(ACTIONS):
        raise Rejected("动作只能是 edit、push_branch、open_pr")
    scope_text = (scope_text or "").strip()
    if not scope_text or len(scope_text) > MAX_SCOPE:
        raise Rejected(f"五要素不能为空，最多 {MAX_SCOPE} 字")
    if not 0 < hours <= MAX_HOURS:
        raise Rejected(f"有效期 1 到 {MAX_HOURS} 小时")
    valid_until = iso(now + dt.timedelta(hours=hours))
    code = confirm_code(repo, executor, actions, valid_until, scope_text)
    with tx(path) as con:
        con.execute("INSERT OR REPLACE INTO proposals (code, repo, executor, scope_text, actions, valid_until, created_at, "
                    "expires_at, task_id) VALUES (?,?,?,?,?,?,?,?,NULL)",
                    (code, repo, executor, scope_text, json.dumps(list(actions)), valid_until, iso(now),
                     iso(now + PROPOSAL_TTL)))
    return {"code": code, "phrase": f"派单 {code}", "repo": repo, "executor": executor, "actions": list(actions),
            "valid_until": valid_until, "expires_at": iso(now + PROPOSAL_TTL)}


def approve(message_id, now=None, path=None, post=None):
    """常浩回了「派单 <确认码>」：核验后签凭证（代次 1），记一个 start 事件并发出去。"""
    now = now or now_tz()
    text = verify_owner(message_id, now)
    m = APPROVE_TEXT.match(text)
    if not m:
        raise Rejected("这条消息不是批准派单的口令（应为「派单 <确认码>」）")
    with tx(path) as con:
        p = con.execute("SELECT * FROM proposals WHERE code = ?", (m.group(1),)).fetchone()
        if p is None:
            raise Rejected("没有这个确认码的派单提议")
        if p["task_id"]:
            raise Rejected(f"这个提议已经签过凭证（{p['task_id']}）")
        if now >= parse_iso(p["expires_at"]):
            raise Rejected("这个提议已过期（30 分钟），请猛鬼兽重新整理后再批")
        actions = json.loads(p["actions"])
        if confirm_code(p["repo"], p["executor"], actions, p["valid_until"], p["scope_text"]) != p["code"]:
            raise Rejected("提议内容和确认码对不上，不签")
        github = allowed_repos().get(p["repo"])
        if not github:
            raise Rejected(f"{p['repo']} 已不在名册能派写入单的仓库里")
        claim_message(con, message_id, f"批准派单 {p['code']}", now)
        task_id = f"t-{now.astimezone(TZ):%Y%m%d}-{secrets.token_hex(3)}"
        con.execute("INSERT INTO grants VALUES (?,?,?,?,?,?,?,?,?,'active',1,?,?,?,?)",
                    (task_id, p["repo"], github, p["scope_text"], sha256(p["scope_text"]), p["executor"],
                     rule_name(p["repo"], p["executor"]), p["actions"], p["valid_until"], iso(now), message_id,
                     iso(now), iso(now)))
        con.execute("UPDATE proposals SET task_id = ? WHERE code = ?", (task_id, p["code"]))
        log(con, task_id, "签发", now, message=message_id, code=p["code"])
        row = grant_row(con, task_id)
        record_event(con, row, "start", f"{task_id}:g1:start", now)
    refresh_status(path, now)
    return {"task_id": task_id, "send": _try_send(path, post)}


def _try_send(path, post):
    try:
        return send_pending(path, post)
    except Rejected as e:
        return {"error": str(e)}


# ---------- 入口二：依据已有凭证续接 ----------
def resume_dispatch(task_id, kind, reason, now=None, path=None, post=None):
    now = now or now_tz()
    if kind not in KINDS:
        raise Rejected("续接只有 redispatch、rework、correction 三种")
    if not (reason or "").strip():
        raise Rejected("续接要写原因")
    with tx(path) as con:
        row = grant_row(con, task_id)
        voided = {r[0] for r in con.execute("SELECT generation FROM voided_generations WHERE task_id = ?", (task_id,))}
        why = check_row(row, "edit", row["generation"], now, voided)
        if why:
            raise Rejected(f"不能续接：{why}")
        n = con.execute("SELECT COUNT(*) FROM hub_events WHERE task_id = ? AND generation = ? AND kind = ?",
                        (task_id, row["generation"], kind)).fetchone()[0]
        key = f"{task_id}:g{row['generation']}:{kind}:{n + 1}"
        record_event(con, row, kind, key, now)
        log(con, task_id, "续接", now, kind=kind, reason=reason.strip()[:200], event=key)
    return {"event_key": key, "send": _try_send(path, post)}


# ---------- 安全方向：冻结、撤销、完成 ----------
def _set_status(task_id, status, what, reason, now, path, void):
    with tx(path) as con:
        row = grant_row(con, task_id)
        if row["status"] in ("revoked", "done"):
            raise Rejected(f"{task_id} 已经是终态（{row['status']}），不能再改")
        if void:
            con.execute("INSERT OR IGNORE INTO voided_generations VALUES (?,?,?,?)",
                        (task_id, row["generation"], iso(now), reason[:200]))
        con.execute("UPDATE grants SET status = ?, updated_at = ? WHERE task_id = ?", (status, iso(now), task_id))
        con.execute("UPDATE hub_events SET sent = -1 WHERE task_id = ? AND sent = 0", (task_id,))
        log(con, task_id, what, now, reason=reason[:200], generation=row["generation"], voided=void)
        out = {"task_id": task_id, "status": status, "generation": row["generation"]}
    refresh_status(path, now)
    return out


def freeze(task_id, reason, now=None, path=None):
    """推翻第一步：冻结并作废当前代次。冻结中不往中台投任何事件。"""
    if not (reason or "").strip():
        raise Rejected("冻结要写原因")
    return _set_status(task_id, "frozen", "冻结", reason, now or now_tz(), path, void=True)


def revoke(task_id, reason="常浩撤销", now=None, path=None):
    return _set_status(task_id, "revoked", "撤销", reason, now or now_tz(), path, void=True)


def complete(task_id, now=None, path=None):
    return _set_status(task_id, "done", "完成", "任务完成", now or now_tz(), path, void=False)


# ---------- 恢复：常浩私聊「恢复 <单号>」，代次 +1 ----------
def unfreeze(message_id, now=None, path=None):
    now = now or now_tz()
    text = verify_owner(message_id, now)
    m = UNFREEZE_TEXT.match(text)
    if not m:
        raise Rejected("这条消息不是恢复任务的口令（应为「恢复 <单号>」）")
    with tx(path) as con:
        row = grant_row(con, m.group(1))
        if row["status"] != "frozen":
            raise Rejected(f"{row['task_id']} 不是冻结状态（现在是 {row['status']}），不用恢复")
        if now >= parse_iso(row["valid_until"]):
            raise Rejected("凭证已过期，恢复不了；要继续请重新批准派单")
        claim_message(con, message_id, f"恢复 {row['task_id']}", now)
        gen = row["generation"] + 1
        con.execute("UPDATE grants SET status = 'active', generation = ?, generation_started_at = ?, updated_at = ? "
                    "WHERE task_id = ?", (gen, iso(now), iso(now), row["task_id"]))
        log(con, row["task_id"], "恢复", now, message=message_id, generation=gen)
    refresh_status(path, now)
    return {"task_id": row["task_id"], "status": "active", "generation": gen}


def show(task_id=None, path=None):
    with tx(path) as con:
        rows = [grant_row(con, task_id)] if task_id else list(con.execute("SELECT * FROM grants ORDER BY created_at"))
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys() if k not in ("scope_text", "approval_message")}
            d["actions"] = json.loads(r["actions"])
            d["voided_generations"] = [v[0] for v in con.execute(
                "SELECT generation FROM voided_generations WHERE task_id = ? ORDER BY generation", (r["task_id"],))]
            out.append(d)
        return out


# ---------- 命令行 ----------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--repo", required=True)
    p.add_argument("--executor", required=True)
    p.add_argument("--scope-file", required=True)
    p.add_argument("--hours", type=int, default=12)
    for name in ("approve", "unfreeze"):
        sub.add_parser(name).add_argument("--message-id", required=True)
    p = sub.add_parser("resume-dispatch")
    p.add_argument("--task", required=True)
    p.add_argument("--kind", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("freeze")
    p.add_argument("--task", required=True)
    p.add_argument("--reason", required=True)
    p = sub.add_parser("revoke")
    p.add_argument("--task", required=True)
    p.add_argument("--reason", default="常浩撤销")
    sub.add_parser("complete").add_argument("--task", required=True)
    p = sub.add_parser("check")
    p.add_argument("--task", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--generation", type=int, required=True)
    sub.add_parser("show").add_argument("--task")
    sub.add_parser("send-pending")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "propose":
            out = propose(a.repo, a.executor, Path(a.scope_file).read_text(encoding="utf-8"), a.hours)
        elif a.cmd == "approve":
            out = approve(a.message_id)
        elif a.cmd == "unfreeze":
            out = unfreeze(a.message_id)
        elif a.cmd == "resume-dispatch":
            out = resume_dispatch(a.task, a.kind, a.reason)
        elif a.cmd == "freeze":
            out = freeze(a.task, a.reason)
        elif a.cmd == "revoke":
            out = revoke(a.task, a.reason)
        elif a.cmd == "complete":
            out = complete(a.task)
        elif a.cmd == "check":
            why = check(a.task, a.action, a.generation)
            out = {"ok": why is None, "why": why}
        elif a.cmd == "show":
            out = show(a.task)
        else:
            out = send_pending()
    except Rejected as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if not (isinstance(out, dict) and out.get("ok") is False) else 1


if __name__ == "__main__":
    sys.exit(main())
