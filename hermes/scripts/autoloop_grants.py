#!/usr/bin/env python3
"""任务凭证与派单网关（批 0B-1，#195；方案 docs/agent-autoloop-底座-方案.md 5.1，规格 docs/秘书代批-规格.md §2、§5、§8）。

写入类派单是 D 档：只有常浩本人在私聊里批准，才签发一张任务凭证。凭证写明仓库、范围（五要素原文的哈希）、执行方、
允许的动作（只有 edit / push_branch / open_pr）、到期时间、状态和**执行代次**。秘书（猛鬼兽）不签凭证、不往凭证里加权限。

批准分两步，把常浩的话绑定到这一单的具体内容（防止模型拼参数）：
  1. propose：猛鬼兽给出仓库、执行方、五要素；本脚本随机生成 8 位确认码（插入不覆盖，撞号重抽），提议里记完整的
     GitHub 仓库和内容哈希，并**由本脚本自己**以猛鬼兽机器人把五要素原文和「派单 <确认码>」发到常浩私聊（不让模型转述）；
     确认消息没发成的提议不能批；
  2. approve：常浩在私聊回「派单 <确认码>」，本脚本用 twin_identity 回读核验（常浩本人、私聊、10 分钟内、消息没用过），
     口令以回读原文为准；确认码对得上一条没过期、确认消息已送达、内容哈希没变、名册里仓库映射也没变的提议，才签凭证
     （代次 1），写工人可读的任务单，再发第一个派单事件。

网关只有两个入口往中台发 hermes 来源的事件：approve（本人批准建任务）和 resume（凭证有效时续接：重派、返修、更正）。
事件只带单号、代次和类别（方案 5.1 第 1 条「凭证内容不进事件」）；五要素原文写在工人只读的任务单
/Users/Shared/dt-gate/tasks/<单号>.md（644，不含消息号；0E-2 起在 dt-gate 的共享目录），0C 的启动脚本据此取任务。

发事件（send_pending）：先记账后发。每个事件在一个写事务里：先按已提交的状态重写镜像（写不成就不领取、不发），
再现读凭证判定（状态、到期、作废代次、代次）后领取（在途），提交后才 POST；送达后条件更新为已送达。所以发出去的每个事件，
它的任务和代次在发之前已经出现在工人镜像里。连不上中台记「结果不明」留在在途，过 60 秒、凭证仍有效才补发；
凭证已失效的不再发。冻结只作废还没领取的事件，不会把已送达的记录改掉。
  hub_events.sent：0 待发，2 在途 / 结果不明，1 已送达，-1 作废未发，-2 作废前已领取（可能已送达，靠代次拦截）。
  中台按「同一来源、同一 id」去重（agent-autoloop kernel/decide.ts ingest，tests/events.test.ts），重复投递回原事件号和
  duplicate: true，发送方照常记已送达；慢请求、进程暂停仍可能 POST 两次，靠这一点保证只派一次。

镜像只按**已提交**的状态写，写入时持有凭证库写锁（和库的变化串行），不写未提交的状态。凭证库是权威：冻结、撤销、完成
先在库里提交（旧代次立刻作废，check() 立刻拒绝），再写镜像；镜像写不成时冻结照样生效，输出里报 mirror_error、
命令行退出码 2，且之后任何派发都会因写不成镜像而停下，直到镜像写好。批准、恢复同样先提交后写镜像，写不成工人看到的是更严的
旧状态。冻结时生成一个冻结码；恢复要常浩私聊「恢复 <单号> <冻结码>」，
并且这次冻结已经由推翻流程记过更正（规格 §8 第 4 步「更正先持久记在账本」，record_resolution，0B-5 调用，本批不给命令行，
所以本批恢复入口实际关闭）；恢复时代次 +1，被作废的旧代次永久失效。

工人（dt-worker）只能读共享目录里的状态镜像 /Users/Shared/dt-gate/grants/status.json（状态、代次、到期、动作）和
任务单，读不到凭证库，也改不了镜像。check() 是主账号出口共用的判定：存在、有效、没过期、动作在列、代次是当前的。
工人侧按镜像做的检查只防正常流程误操作，不当作不可绕过的边界；权威判定在主账号的出口（方案 5.1 第 3 条）。

威胁模型与已知局限（PR #199 审查 P0-01，常浩 2026-11-17 定：沙盒阶段先接受）：本脚本和猛鬼兽跑在同一个 macOS 账号，
猛鬼兽的模型有 shell，能改身份配置（DT_OWNER_* 环境变量和配置文件）、读写凭证库、读 hermes 来源密钥，从而绕过两步批准。
这里的检查挡得住模型拼错参数、复用旧消息、事后改提议，挡不住主账号上蓄意作恶的模型（twin_identity 头注释同一局限）。
硬保证要把签发网关放进独立信任域（例如另一个 macOS 账号 dt-gate，猛鬼兽只能经窄接口递提议和消息号）；
是否这样做，在 0A–0D 之后、真实切换前的 ADR-015 里决定。在那之前，写入单只派名册里的沙盒和明确允许写入的仓库。

  autoloop_grants.py propose --repo dt-sandbox --executor claude --scope-file 五要素.md [--hours 12]
  autoloop_grants.py approve --message-id om_xxx          常浩回了「派单 <确认码>」后由猛鬼兽调用
  autoloop_grants.py resume-dispatch --task T --kind redispatch|rework|correction --reason 原因
  autoloop_grants.py freeze --task T --reason 原因 | revoke --task T [--reason] | complete --task T
  autoloop_grants.py unfreeze --message-id om_xxx        常浩私聊「恢复 <单号> <冻结码>」（要先记过更正）
  autoloop_grants.py check --task T --action edit --generation 1
  autoloop_grants.py show [--task T] | send-pending
环境变量：DT_GRANTS_DB（默认 DigitalTwin/autoloop/grants.db）、DT_GRANTS_STATUS（共享镜像）、DT_GRANTS_TASKS（任务单目录）、
DT_HUB（中台地址）、DT_HERMES_SOURCE_KEY_FILE（hermes 来源密钥文件，600；0B-2 建）。
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
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
import hub_listener  # noqa: E402
import twin_identity as I  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
SUPPORT = Path("~/Library/Application Support/DigitalTwin").expanduser()
ROSTER = Path(os.environ.get("DT_ROSTER") or HERE.parents[1] / "projects.json")
ACTIONS = ("edit", "push_branch", "open_pr")
ACTION_WORDS = {"edit": "改文件", "push_branch": "推分支（不推 main）", "open_pr": "开 PR"}
EXECUTORS = ("claude", "codex")
KINDS = ("redispatch", "rework", "correction")
PROPOSAL_TTL = dt.timedelta(minutes=30)
STALE = dt.timedelta(seconds=60)          # 在途超过这么久没结果，才当结果不明补发（POST 超时 15 秒）
MAX_HOURS = 72
APPROVE_TEXT = re.compile(r"^派单\s*([0-9a-f]{8})$")
UNFREEZE_TEXT = re.compile(r"^恢复\s*(t-\d{8}-[0-9a-f]{6})\s+([0-9a-f]{6})$")
TASK_ID = re.compile(r"^t-\d{8}-[0-9a-f]{6}$")
REPO_NAME = re.compile(r"^[\w.-]{1,100}$")
MAX_SCOPE = 3000
SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE IF NOT EXISTS grants (
  task_id TEXT PRIMARY KEY, repo TEXT NOT NULL, github TEXT NOT NULL, scope_text TEXT NOT NULL, scope_hash TEXT NOT NULL,
  executor TEXT NOT NULL, rule TEXT NOT NULL, actions TEXT NOT NULL, valid_until TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active','frozen','revoked','done')),
  generation INTEGER NOT NULL, generation_started_at TEXT NOT NULL, freeze_code TEXT,
  approval_message TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS voided_generations (task_id TEXT NOT NULL, generation INTEGER NOT NULL, at TEXT NOT NULL,
  reason TEXT NOT NULL, PRIMARY KEY (task_id, generation));
CREATE TABLE IF NOT EXISTS resolutions (task_id TEXT NOT NULL, generation INTEGER NOT NULL, decision TEXT NOT NULL,
  resolution TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (task_id, generation));
CREATE TABLE IF NOT EXISTS grant_log (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, at TEXT NOT NULL,
  what TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals (code TEXT PRIMARY KEY, repo TEXT NOT NULL, github TEXT NOT NULL,
  executor TEXT NOT NULL, scope_text TEXT NOT NULL, actions TEXT NOT NULL, valid_until TEXT NOT NULL,
  content_hash TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, notice_message TEXT, task_id TEXT);
CREATE TABLE IF NOT EXISTS used_messages (message_id TEXT PRIMARY KEY, purpose TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hub_events (event_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, rule TEXT NOT NULL,
  executor TEXT NOT NULL, generation INTEGER NOT NULL, kind TEXT NOT NULL, hub_event INTEGER, at TEXT NOT NULL,
  sent INTEGER NOT NULL DEFAULT 0, claimed_at TEXT, tries INTEGER NOT NULL DEFAULT 0)
"""


class Rejected(Exception):
    """拒绝：说人话的原因，不带身份值、不带密钥。"""


# ---------- 路径、时间 ----------
def db_path():
    return Path(os.environ.get("DT_GRANTS_DB") or SUPPORT / "autoloop/grants.db").expanduser()


def status_path():
    return Path(os.environ.get("DT_GRANTS_STATUS") or "/Users/Shared/dt-gate/grants/status.json").expanduser()


def tasks_dir():
    return Path(os.environ.get("DT_GRANTS_TASKS") or "/Users/Shared/dt-gate/tasks").expanduser()


def now_tz():
    return dt.datetime.now(TZ)


def iso(t):
    return t.astimezone(TZ).isoformat(timespec="seconds")


def parse_iso(s):
    return dt.datetime.fromisoformat(s)


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- 库 ----------
def _ensure_schema(con):
    v = con.execute("PRAGMA user_version").fetchone()[0]
    if v == SCHEMA_VERSION:
        return
    if v or con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'").fetchone():
        raise Rejected(f"凭证库结构是第 {v} 版，本脚本要第 {SCHEMA_VERSION} 版；没有自动迁移，请把旧库备份后移走")
    for stmt in SCHEMA.split(";"):
        con.execute(stmt)
    con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


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
        con.execute("BEGIN IMMEDIATE")
        try:
            _ensure_schema(con)
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


def voided_of(con, task_id):
    return {r[0] for r in con.execute("SELECT generation FROM voided_generations WHERE task_id = ?", (task_id,))}


# ---------- 名册 ----------
def allowed_repos():
    """能派写入单的仓库（显式白名单）：名册活跃项目里 write_allowed 明确为 true、有 GitHub 的，加上 inventory 里标了
    sandbox 的。缺这个字段的一律不算（PR #199 第 2 轮审查 P1-01）。"""
    r = json.loads(ROSTER.read_text(encoding="utf-8"))
    out = {}
    for p in r.get("projects", []):
        if p.get("github") and p.get("write_allowed") is True and p.get("category") == "活跃":
            out[p["github"].split("/")[-1]] = p["github"]
    for p in r.get("inventory", []):
        if p.get("sandbox") and str(p.get("path", "")).startswith("github:"):
            gh = p["path"].split(":", 1)[1]
            out[gh.split("/")[-1]] = gh
    return out


def rule_name(repo, executor):
    """派单规则名（批 2 的规则生成器按同一写法生成）；凭证里记签发时的规则名，绑定时三者对上（规格 §5）。"""
    return f"dispatch-{repo}-{executor}"


def event_type(repo, executor):
    """派单事件的类型；中台派单规则按它接（autoloop_config 生成规则时用同一个函数）。"""
    return f"dispatch.{repo}.{executor}.write"


# ---------- 判定（所有出口共用） ----------
def check_row(row, action, generation, now, voided=()):
    """返回 None 表示放行，否则返回拒绝原因。action 为 None 时不查动作（发派单事件用）。"""
    if row is None:
        return "没有这张任务凭证"
    if row["status"] != "active":
        return {"frozen": "凭证已冻结", "revoked": "凭证已撤销", "done": "任务已完成"}[row["status"]]
    if now >= parse_iso(row["valid_until"]):
        return "凭证已过期"
    if action is not None and action not in json.loads(row["actions"]):
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
        return check_row(row, action, generation, now, voided_of(con, task_id))


# ---------- 共享文件（工人只读） ----------
def _write_shared(p, text):
    """原子写一个工人可读的文件：目录 755、文件 644。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o755)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, 0o644)
    os.replace(tmp, p)


def write_status(con, now):
    """按 con 里看到的状态写镜像。调用方都持有写锁，所以镜像写入和库的变化一样是串行的。"""
    tasks = {}
    for r in con.execute("SELECT * FROM grants ORDER BY task_id"):
        tasks[r["task_id"]] = {"repo": r["repo"], "executor": r["executor"], "rule": r["rule"], "status": r["status"],
                               "generation": r["generation"], "voided_generations": sorted(voided_of(con, r["task_id"])),
                               "valid_until": r["valid_until"], "actions": json.loads(r["actions"]),
                               "scope_hash": r["scope_hash"]}
    doc = {"schema": 1, "updated_at": iso(now), "tasks": tasks}
    _write_shared(status_path(), json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True))


def refresh_status(path=None, now=None):
    """状态变化提交之后，持写锁按已提交的状态写镜像（这个事务本身不改库）。写不成就抛 OSError。"""
    with tx(path) as con:
        write_status(con, now or now_tz())


def _publish_after_commit(out, path, now):
    """提交之后写镜像；写不成不撤销已提交的变化，在输出里报出（派发会因写不成镜像而停下）。"""
    try:
        refresh_status(path, now)
    except OSError as e:
        out["mirror_error"] = (f"状态镜像没写成（{type(e).__name__}）：凭证库已生效、出口按库判定；"
                               "工人侧镜像可能是旧的，派发已停，修好后跑 send-pending 重写")
    return out


def write_task_file(row):
    """工人可读的任务单：五要素原文和凭证的不变字段；不含批准消息号、不含代次（代次以镜像和事件为准）。"""
    text = "\n".join([f"# 任务 {row['task_id']}", "", f"仓库：{row['github']}", f"执行方：{row['executor']}",
                      f"允许的动作：{'、'.join(json.loads(row['actions']))}", f"有效到：{row['valid_until']}",
                      f"范围哈希：{row['scope_hash']}", "", "## 五要素", "", row["scope_text"], ""])
    _write_shared(tasks_dir() / f"{row['task_id']}.md", text)


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
        hub_listener.check(hub_url())            # dt-gate 里：端口上不是 dt-gate 在听就不带来源钥匙去（0E-2）
        with opener.open(req, timeout=15) as r:
            return json.loads(r.read().decode())["event"]
    except urllib.error.HTTPError as e:
        raise Rejected(f"中台拒收事件：HTTP {e.code}")
    except (OSError, ValueError, KeyError) as e:
        raise Rejected(f"连不上中台或回应不对：{type(e).__name__}")


def event_body(ev, row):
    """事件只带单号、代次、类别（方案 5.1 第 1 条）；五要素在任务单里。"""
    return {"id": ev["event_key"], "type": event_type(row["repo"], row["executor"]), "subject": row["task_id"],
            "summary": f"{row['repo']} · {row['executor']} · {ev['kind']}",
            "detail": json.dumps({"task_id": row["task_id"], "generation": ev["generation"], "kind": ev["kind"]},
                                 sort_keys=True)}


def record_event(con, row, kind, key, now):
    con.execute("INSERT OR IGNORE INTO hub_events (event_key, task_id, rule, executor, generation, kind, at) "
                "VALUES (?,?,?,?,?,?,?)", (key, row["task_id"], row["rule"], row["executor"], row["generation"], kind, iso(now)))


def _claim_next(con, now, seen):
    """在写事务里挑下一个要发的事件：现读凭证判定，不能发的就地作废；能发的领取为在途。返回 (事件, 凭证, 作废清单)。"""
    skipped = []
    for e in con.execute("SELECT * FROM hub_events WHERE sent IN (0, 2) ORDER BY at, rowid").fetchall():
        if e["event_key"] in seen:
            continue
        if e["sent"] == 2 and now - parse_iso(e["claimed_at"]) < STALE:
            continue                                  # 刚被领走、还在发，不抢
        row = con.execute("SELECT * FROM grants WHERE task_id = ?", (e["task_id"],)).fetchone()
        why = check_row(row, None, e["generation"], now, voided_of(con, e["task_id"]))
        if why:
            con.execute("UPDATE hub_events SET sent = ? WHERE event_key = ?", (-1 if e["sent"] == 0 else -2, e["event_key"]))
            skipped.append({"event_key": e["event_key"], "sent": False, "why": f"{why}，作废不发"})
            continue
        con.execute("UPDATE hub_events SET sent = 2, claimed_at = ?, tries = tries + 1 WHERE event_key = ?",
                    (iso(now), e["event_key"]))
        return dict(e), dict(row), skipped
    return None, None, skipped


def send_pending(path=None, post=None, now=None):
    """把记了账、还没确认送到中台的事件逐个发出去。每个：同一写事务里先按已提交状态写镜像、再现读凭证并领取 → 提交 →
    POST → 送达后条件更新。镜像写不成就一个也不领取、不发。"""
    post = post or post_event
    results, seen = [], set()
    while True:
        t = now or now_tz()
        try:
            with tx(path) as con:
                write_status(con, t)          # 领取前、同一把写锁下：要发的任务和代次先进镜像
                ev, row, skipped = _claim_next(con, t, seen)
        except OSError as e:
            raise Rejected(f"状态镜像写不了（{type(e).__name__}），事件先不发")
        results += skipped
        if ev is None:
            return results
        seen.add(ev["event_key"])
        try:
            hub_event = post(event_body(ev, row))
        except Rejected as e:
            results.append({"event_key": ev["event_key"], "sent": None, "why": f"结果不明（{e}），凭证仍有效时下次补发"})
            continue
        with tx(path) as con:
            con.execute("UPDATE hub_events SET sent = 1, hub_event = ? WHERE event_key = ? AND sent IN (2, -2)",
                        (hub_event, ev["event_key"]))
        results.append({"event_key": ev["event_key"], "sent": True, "hub_event": hub_event})


def _try_send(path, post, now):
    try:
        return send_pending(path, post, now)
    except Rejected as e:
        return {"error": str(e)}


# ---------- 身份、私聊 ----------
def fetch_message(message_id):
    """回读一条消息并带正文；测试打桩这里。"""
    return I.lark_fetch(message_id, with_text=True)


def notify_owner(text, key):
    """以猛鬼兽机器人把确认消息发到常浩私聊（批准口令必须来自的同一个会话）；返回消息号。测试打桩这里。"""
    import tidings_weekly as W
    import twin_state as T
    try:
        chat = T.owner_chat_id()
    except T.StateError as e:
        raise Rejected(str(e))
    try:
        mid = W.send(chat, "text", {"text": text}, key)
    except W.SendError as e:
        raise Rejected(f"确认消息没发出去：{e}")
    if not mid:
        raise Rejected("确认消息没发出去：飞书没回消息号")
    return mid


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
def content_hash(repo, github, executor, actions, valid_until, scope_text):
    return sha256("\n".join([repo, github, executor, ",".join(actions), valid_until, scope_text]))


def notice_text(code, github, executor, actions, valid_until, scope_text):
    until = parse_iso(valid_until).astimezone(TZ)
    return "\n".join([
        "【派单确认】猛鬼兽整理了一张写入任务，请核对：",
        f"仓库：{github}", f"执行方：{executor}", f"允许的动作：{'、'.join(ACTION_WORDS[a] for a in actions)}",
        f"有效到：{until:%m-%d %H:%M}", "", "五要素：", scope_text, "",
        f"同意就回：派单 {code}", f"（{int(PROPOSAL_TTL.total_seconds() // 60)} 分钟内有效，只认你本人在这个私聊里回；不同意不用回）"])


def propose(repo, executor, scope_text, hours=12, actions=ACTIONS, now=None, path=None):
    now = now or now_tz()
    if not REPO_NAME.match(repo or ""):
        raise Rejected(f"{repo} 不在名册能派写入单的仓库里")
    github = allowed_repos().get(repo)
    if not github:
        raise Rejected(f"{repo} 不在名册能派写入单的仓库里")
    if executor not in EXECUTORS:
        raise Rejected("执行方只能是 claude 或 codex")
    if not actions or set(actions) - set(ACTIONS):
        raise Rejected("动作只能是 edit、push_branch、open_pr")
    actions = tuple(a for a in ACTIONS if a in actions)
    scope_text = (scope_text or "").strip()
    if not scope_text or len(scope_text) > MAX_SCOPE:
        raise Rejected(f"五要素不能为空，最多 {MAX_SCOPE} 字")
    if not 0 < hours <= MAX_HOURS:
        raise Rejected(f"有效期 1 到 {MAX_HOURS} 小时")
    valid_until = iso(now + dt.timedelta(hours=hours))
    h = content_hash(repo, github, executor, actions, valid_until, scope_text)
    with tx(path) as con:
        for _ in range(5):
            code = secrets.token_hex(4)
            try:
                con.execute("INSERT INTO proposals (code, repo, github, executor, scope_text, actions, valid_until, "
                            "content_hash, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (code, repo, github, executor, scope_text, json.dumps(list(actions)), valid_until, h,
                             iso(now), iso(now + PROPOSAL_TTL)))
                break
            except sqlite3.IntegrityError:
                continue
        else:
            raise Rejected("确认码连撞 5 次，请重试")
    notice = notify_owner(notice_text(code, github, executor, actions, valid_until, scope_text), f"dt-grant-{code}")
    with tx(path) as con:
        con.execute("UPDATE proposals SET notice_message = ? WHERE code = ? AND notice_message IS NULL", (notice, code))
    return {"code": code, "repo": repo, "github": github, "executor": executor, "actions": list(actions),
            "valid_until": valid_until, "expires_at": iso(now + PROPOSAL_TTL), "notice": "已发到常浩私聊"}


def approve(message_id, now=None, path=None, post=None):
    """常浩回了「派单 <确认码>」：核验后签凭证（代次 1），写任务单，记一个 start 事件并发出去。"""
    now = now or now_tz()
    text = verify_owner(message_id, now)
    m = APPROVE_TEXT.match(text)
    if not m:
        raise Rejected("这条消息不是批准派单的口令（应为「派单 <8 位确认码>」）")
    with tx(path) as con:
        p = con.execute("SELECT * FROM proposals WHERE code = ?", (m.group(1),)).fetchone()
        if p is None:
            raise Rejected("没有这个确认码的派单提议")
        if p["task_id"]:
            raise Rejected(f"这个提议已经签过凭证（{p['task_id']}）")
        if now >= parse_iso(p["expires_at"]):
            raise Rejected("这个提议已过期（30 分钟），请猛鬼兽重新整理后再批")
        if not p["notice_message"]:
            raise Rejected("这个提议的确认消息没发到你私聊，不能批；请猛鬼兽重新提")
        actions = json.loads(p["actions"])
        if content_hash(p["repo"], p["github"], p["executor"], actions, p["valid_until"], p["scope_text"]) != p["content_hash"]:
            raise Rejected("提议内容和提出时对不上，不签")
        if allowed_repos().get(p["repo"]) != p["github"]:
            raise Rejected(f"名册里 {p['repo']} 的仓库变了或已不能派写入单，请猛鬼兽重新提")
        claim_message(con, message_id, f"批准派单 {p['code']}", now)
        task_id = f"t-{now.astimezone(TZ):%Y%m%d}-{secrets.token_hex(3)}"
        con.execute("INSERT INTO grants (task_id, repo, github, scope_text, scope_hash, executor, rule, actions, "
                    "valid_until, status, generation, generation_started_at, approval_message, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,'active',1,?,?,?,?)",
                    (task_id, p["repo"], p["github"], p["scope_text"], sha256(p["scope_text"]), p["executor"],
                     rule_name(p["repo"], p["executor"]), p["actions"], p["valid_until"], iso(now), message_id,
                     iso(now), iso(now)))
        con.execute("UPDATE proposals SET task_id = ? WHERE code = ?", (task_id, p["code"]))
        log(con, task_id, "签发", now, message=message_id, code=p["code"])
        row = grant_row(con, task_id)
        record_event(con, row, "start", f"{task_id}:g1:start", now)
        write_task_file(row)          # 写不成整笔回滚：没有任务单就没有凭证
    return {"task_id": task_id, "send": _try_send(path, post, now)}    # 发之前在领取的写锁下先写镜像


# ---------- 入口二：依据已有凭证续接 ----------
def resume_dispatch(task_id, kind, reason, now=None, path=None, post=None):
    now = now or now_tz()
    if kind not in KINDS:
        raise Rejected("续接只有 redispatch、rework、correction 三种")
    if not (reason or "").strip():
        raise Rejected("续接要写原因")
    with tx(path) as con:
        row = grant_row(con, task_id)
        why = check_row(row, "edit", row["generation"], now, voided_of(con, task_id))
        if why:
            raise Rejected(f"不能续接：{why}")
        n = con.execute("SELECT COUNT(*) FROM hub_events WHERE task_id = ? AND generation = ? AND kind = ?",
                        (task_id, row["generation"], kind)).fetchone()[0]
        key = f"{task_id}:g{row['generation']}:{kind}:{n + 1}"
        record_event(con, row, kind, key, now)
        log(con, task_id, "续接", now, kind=kind, reason=reason.strip()[:200], event=key)
    return {"event_key": key, "send": _try_send(path, post, now)}


# ---------- 收紧方向：冻结、撤销、完成（先在权威库提交，再写镜像） ----------
def _tighten(task_id, status, what, reason, now, path):
    with tx(path) as con:
        row = grant_row(con, task_id)
        if row["status"] in ("revoked", "done"):
            raise Rejected(f"{task_id} 已经是终态（{row['status']}），不能再改")
        out = {"task_id": task_id, "status": status, "generation": row["generation"]}
        if status == "frozen" and row["status"] == "frozen":
            return {**out, "freeze_code": row["freeze_code"], "already": True}
        code = secrets.token_hex(3) if status == "frozen" else None
        if status != "done":
            con.execute("INSERT OR IGNORE INTO voided_generations VALUES (?,?,?,?)",
                        (task_id, row["generation"], iso(now), reason[:200]))
        con.execute("UPDATE grants SET status = ?, freeze_code = ?, updated_at = ? WHERE task_id = ?",
                    (status, code, iso(now), task_id))
        con.execute("UPDATE hub_events SET sent = -1 WHERE task_id = ? AND sent = 0", (task_id,))
        log(con, task_id, what, now, reason=reason[:200], generation=row["generation"], voided=status != "done")
    return _publish_after_commit({**out, "freeze_code": code} if code else out, path, now)


def freeze(task_id, reason, now=None, path=None):
    """推翻第一步：冻结并作废当前代次，生成这次冻结的冻结码。冻结中不往中台投任何事件。"""
    if not (reason or "").strip():
        raise Rejected("冻结要写原因")
    return _tighten(task_id, "frozen", "冻结", reason, now or now_tz(), path)


def revoke(task_id, reason="常浩撤销", now=None, path=None):
    return _tighten(task_id, "revoked", "撤销", reason, now or now_tz(), path)


def complete(task_id, now=None, path=None):
    return _tighten(task_id, "done", "完成", "任务完成", now or now_tz(), path)


# ---------- 更正记录与恢复 ----------
def record_resolution(task_id, generation, decision, resolution, now=None, path=None):
    """记这次冻结的更正（规格 §8 第 4 步：被替代的决定号、下游怎么处理），恢复的前提。
    只由 0B-5 的推翻流程调用；本批不给命令行入口，所以本批恢复实际关闭。"""
    now = now or now_tz()
    decision, resolution = (decision or "").strip(), (resolution or "").strip()
    if not decision or len(decision) > 100 or not resolution or len(resolution) > 500:
        raise Rejected("更正要写被替代的决定号（100 字内）和下游怎么处理（500 字内）")
    with tx(path) as con:
        row = grant_row(con, task_id)
        if row["status"] != "frozen":
            raise Rejected(f"{task_id} 不是冻结状态，没有要更正的")
        if generation != row["generation"]:
            raise Rejected(f"这次冻结的是第 {row['generation']} 代，不是第 {generation} 代")
        try:
            con.execute("INSERT INTO resolutions VALUES (?,?,?,?,?)", (task_id, generation, decision, resolution, iso(now)))
        except sqlite3.IntegrityError:
            raise Rejected("这次冻结已经记过更正")
        log(con, task_id, "更正", now, generation=generation, decision=decision)
    return {"task_id": task_id, "generation": generation, "recorded": True}


def unfreeze(message_id, now=None, path=None):
    """常浩私聊「恢复 <单号> <冻结码>」：冻结码对得上这次冻结、这次冻结已记过更正，才恢复为有效，代次 +1。"""
    now = now or now_tz()
    text = verify_owner(message_id, now)
    m = UNFREEZE_TEXT.match(text)
    if not m:
        raise Rejected("这条消息不是恢复任务的口令（应为「恢复 <单号> <冻结码>」）")
    with tx(path) as con:
        row = grant_row(con, m.group(1))
        if row["status"] != "frozen":
            raise Rejected(f"{row['task_id']} 不是冻结状态（现在是 {row['status']}），不用恢复")
        if not row["freeze_code"] or not hmac.compare_digest(row["freeze_code"], m.group(2)):
            raise Rejected("冻结码对不上：冻结码只对这一次冻结有效")
        if now >= parse_iso(row["valid_until"]):
            raise Rejected("凭证已过期，恢复不了；要继续请重新批准派单")
        if con.execute("SELECT 1 FROM resolutions WHERE task_id = ? AND generation = ?",
                       (row["task_id"], row["generation"])).fetchone() is None:
            raise Rejected("这次冻结还没记更正（规格 §8：更正先持久记在账本），不能恢复")
        claim_message(con, message_id, f"恢复 {row['task_id']}", now)
        gen = row["generation"] + 1
        con.execute("UPDATE grants SET status = 'active', generation = ?, generation_started_at = ?, freeze_code = NULL, "
                    "updated_at = ? WHERE task_id = ?", (gen, iso(now), iso(now), row["task_id"]))
        log(con, row["task_id"], "恢复", now, message=message_id, generation=gen)
    return _publish_after_commit({"task_id": row["task_id"], "status": "active", "generation": gen}, path, now)


def show(task_id=None, path=None):
    with tx(path) as con:
        rows = [grant_row(con, task_id)] if task_id else list(con.execute("SELECT * FROM grants ORDER BY created_at"))
        out = []
        for r in rows:
            d = {k: r[k] for k in r.keys() if k not in ("scope_text", "approval_message", "freeze_code")}
            d["actions"] = json.loads(r["actions"])
            d["voided_generations"] = sorted(voided_of(con, r["task_id"]))
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
    if isinstance(out, dict) and out.get("ok") is False:
        return 1
    return 2 if isinstance(out, dict) and out.get("mirror_error") else 0     # 已生效，但镜像没写成


if __name__ == "__main__":
    sys.exit(main())
