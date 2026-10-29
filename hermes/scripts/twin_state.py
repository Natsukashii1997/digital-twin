"""持久任务状态与事件底座（B1）：Goal / Task / Run / Event / Ref 五类对象，SQLite 存储，只用标准库。

库用法：
    from twin_state import Store
    with Store() as s:
        g = s.create_goal("把日报并跑验收做完", by="user", status="authorized")
        t = s.create_task(g["id"], "补台账状态机", by="hermes")
        s.set_ready(t["id"], by="hermes")
        r = s.start_run(t["id"], agent="claude", by="hermes", order_no="D20261029-1")
        s.receive_receipt(r["id"], receipt_dict)          # 车间回执（幂等）
命令行（每条命令最后输出一行 JSON，字段 ok / msg，和 dispatch.py 一样）：
    python3 twin_state.py init | backup | restore --from F --yes | recover | sync-orders
    python3 twin_state.py goal-new / goal-authorize / goal-complete / goal-cancel
    python3 twin_state.py task-new / task-ready / task-block / task-unblock / task-retry / task-complete / task-cancel
    python3 twin_state.py run-start / run-finish / run-link / ref-add / show / list / events

数据库默认在 ~/Library/Application Support/DigitalTwin/twin-state.db（环境变量 DT_STATE_DB 可改，自测用临时路径），
权限 700/600，不进 GitHub、不进 Obsidian；备份在同目录 backups/。所有写操作都是一个事务：状态变化和事件记录一起落盘，
中途失败整体回滚。

任务主状态：observed（已发现，尚未授权）→ ready（已授权，可以推进）→ running → review → completed；
blocked（有明确阻塞：decision / dependency / external / technical）；canceled。Run 层沿用派单台账的状态
（new / queued / running / done / guard / failed / merged / cleaned / abandoned）另加 stale（过期），不和任务状态混用。

授权边界（程序校验，不靠提示词）：只有 by="user" 能授权目标、解开 decision 类阻塞、完成或撤销目标；
Hermes 只能在已授权目标范围内推进任务；Run 成功只把任务送到 review，不会自动完成任务或目标；完成任务要有证据。
旧工单只读同步（sync_orders）：order.json 变成未关联的 Run，不自动推断成长期任务；要关联用 link_run。
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = "~/Library/Application Support/DigitalTwin/twin-state.db"
DISPATCH = Path(os.environ.get("DT_DISPATCH", "~/.hermes/dispatch")).expanduser()
SCHEMA_VERSION = 1

GOAL_STATUSES = ("observed", "authorized", "completed", "canceled")
TASK_STATUSES = ("observed", "ready", "running", "blocked", "review", "completed", "canceled")
RUN_STATUSES = ("new", "queued", "running", "done", "guard", "failed", "merged", "cleaned", "abandoned", "stale")
RUN_ACTIVE = ("new", "queued", "running")
RUN_SUCCESS = ("done", "merged", "cleaned")
BLOCK_KINDS = ("decision", "dependency", "external", "technical")
ACTORS = ("user", "hermes", "claude", "codex", "system")
SOURCES = ("user", "discovery", "agent", "system")
OBJECT_TYPES = ("goal", "task", "run")
MAX_RETRIES = 2            # technical 类阻塞最多自动重试几次，超过就要常浩定
STALE_AFTER = 2 * 3600     # 秒：running 的 Run 超过这么久没有心跳就算过期（派单默认超时 3600）
SUMMARY_CLIP = 1000        # 回执里的车间总结最多存多少字

TASK_TRANSITIONS = {
    ("observed", "ready"), ("observed", "blocked"), ("observed", "canceled"),
    ("ready", "running"), ("ready", "blocked"), ("ready", "canceled"),
    ("running", "review"), ("running", "blocked"), ("running", "canceled"),
    ("blocked", "ready"), ("blocked", "canceled"),
    ("review", "completed"), ("review", "ready"), ("review", "blocked"), ("review", "canceled"),
}
RUN_TRANSITIONS = {
    ("new", "queued"), ("new", "running"), ("new", "abandoned"), ("queued", "running"), ("queued", "abandoned"),
    ("running", "done"), ("running", "guard"), ("running", "failed"), ("running", "stale"), ("running", "abandoned"),
    ("done", "merged"), ("done", "abandoned"), ("merged", "cleaned"), ("guard", "abandoned"), ("failed", "abandoned"),
    ("stale", "done"), ("stale", "guard"), ("stale", "failed"), ("stale", "abandoned"),
}

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE goals (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('observed','authorized','completed','canceled')),
  scope TEXT NOT NULL DEFAULT '', project TEXT, phase TEXT NOT NULL DEFAULT '', done_when TEXT NOT NULL DEFAULT '',
  authorized_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE tasks (
  id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id), title TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('observed','ready','running','blocked','review','completed','canceled')),
  depends_on TEXT NOT NULL DEFAULT '[]', acceptance TEXT NOT NULL DEFAULT '', next_step TEXT NOT NULL DEFAULT '',
  blocked_kind TEXT CHECK (blocked_kind IS NULL OR blocked_kind IN ('decision','dependency','external','technical')),
  blocked_reason TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT 'hermes', retries INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX tasks_goal ON tasks(goal_id);
CREATE TABLE runs (
  id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), agent TEXT NOT NULL, order_no TEXT UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('new','queued','running','done','guard','failed','merged','cleaned','abandoned','stale')),
  session_id TEXT, receipt TEXT NOT NULL DEFAULT '{}', evidence TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '',
  started_at TEXT, ended_at TEXT, heartbeat_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX runs_task ON runs(task_id);
CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idem_key TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL CHECK (source IN ('user','discovery','agent','system')), kind TEXT NOT NULL, actor TEXT NOT NULL,
  object_type TEXT, object_id TEXT, payload TEXT NOT NULL DEFAULT '{}', evidence TEXT NOT NULL DEFAULT '',
  applied INTEGER NOT NULL DEFAULT 1, result TEXT NOT NULL DEFAULT '', dup_count INTEGER NOT NULL DEFAULT 0,
  at TEXT NOT NULL, last_seen_at TEXT);
CREATE INDEX events_object ON events(object_type, object_id);
CREATE TABLE refs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, object_type TEXT NOT NULL, object_id TEXT NOT NULL, kind TEXT NOT NULL,
  ref TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  UNIQUE (object_type, object_id, kind, ref));
"""
MIGRATIONS = [SCHEMA]  # 第 n 项把库从版本 n 升到 n+1；只追加，不改已发布的项


class StateError(Exception):
    """状态底座拒绝的操作；message 直接给常浩看。"""


class IllegalTransition(StateError):
    pass


class NotAuthorized(StateError):
    pass


class EvidenceMissing(StateError):
    pass


class NotFound(StateError):
    pass


def _json(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _loads(s, default):
    try:
        return json.loads(s) if s else default
    except ValueError:
        return default


def _parse(ts):
    return dt.datetime.fromisoformat(ts) if ts else None


def clip_receipt(receipt):
    """回执只留结构化字段，车间总结截到 SUMMARY_CLIP 字。"""
    r = dict(receipt or {})
    if isinstance(r.get("summary"), str):
        r["summary"] = r["summary"][:SUMMARY_CLIP]
    return r


def receipt_evidence(receipt):
    keys = ("committed_files", "head_before", "head_after", "branch", "exit_code", "timed_out", "worker_error",
            "guard_main_changed", "guard_remote_changed", "need_decision", "ended")
    return {k: receipt[k] for k in keys if k in (receipt or {})}


def receipt_key(run_id, receipt):
    return f"run.receipt:{run_id}:{hashlib.sha1(_json(receipt).encode()).hexdigest()[:16]}"


class Store:
    """一个 Store 对应一个库文件；用 with 打开，所有公开写方法各自是一个事务。"""

    def __init__(self, path=None, clock=None):
        self.path = Path(path or os.environ.get("DT_STATE_DB", DEFAULT_DB)).expanduser()
        self.clock = clock or (lambda: dt.datetime.now().astimezone())
        self.conn = None
        self._depth = 0

    # ---------- 打开、迁移、备份 ----------
    def open(self):
        if self.conn:
            return self
        created = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        if created:
            os.chmod(self.path, 0o600)
        return self

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    def _migrate(self):
        with self._tx() as c:
            version = 0
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                version = int(row[0]) if row else 0
            if version > len(MIGRATIONS):
                raise StateError(f"库版本 {version} 比程序支持的 {len(MIGRATIONS)} 新，请升级脚本")
            for step in MIGRATIONS[version:]:
                for stmt in step.split(";"):  # 不用 executescript：它会先把当前事务提交掉
                    if stmt.strip():
                        c.execute(stmt)
            c.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(len(MIGRATIONS)),))
            if version == 0:
                c.execute("INSERT OR REPLACE INTO meta VALUES ('created_at', ?)", (self.now(),))

    def version(self):
        return int(self.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])

    def backup(self, dest=None):
        """在线一致备份（SQLite backup API），默认到同目录 backups/。"""
        dest = Path(dest) if dest else self.path.parent / "backups" / f"{self.path.stem}-{self.clock():%Y%m%d-%H%M%S}.db"
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        out = sqlite3.connect(str(dest))
        try:
            self.conn.backup(out)
        finally:
            out.close()
        os.chmod(dest, 0o600)
        return dest

    def restore(self, src):
        """用备份文件整体替换当前库内容；调用方负责先确认。"""
        src = Path(src)
        if not src.exists():
            raise NotFound(f"备份文件不存在：{src}")
        # immutable=1：备份文件没人写，不建 -shm / -wal；系统 Python 3.9 自带的 SQLite 只给 mode=ro 会打不开 WAL 库
        inp = sqlite3.connect(f"file:{src}?mode=ro&immutable=1", uri=True)
        try:
            inp.backup(self.conn)
        finally:
            inp.close()
        self._migrate()
        return self.summary()

    # ---------- 事务与事件 ----------
    @contextlib.contextmanager
    def _tx(self):
        """可重入：最外层 BEGIN IMMEDIATE / COMMIT，内层用 SAVEPOINT；哪一层抛异常就回滚到哪一层。"""
        depth = self._depth
        self.conn.execute("BEGIN IMMEDIATE" if depth == 0 else f"SAVEPOINT sp{depth}")
        self._depth = depth + 1
        try:
            yield self.conn
        except BaseException:
            self._depth = depth
            if depth == 0:
                self.conn.execute("ROLLBACK")
            else:
                self.conn.execute(f"ROLLBACK TO sp{depth}")
                self.conn.execute(f"RELEASE sp{depth}")
            raise
        else:
            self._depth = depth
            self.conn.execute("COMMIT" if depth == 0 else f"RELEASE sp{depth}")

    def now(self):
        return self.clock().isoformat(timespec="seconds")

    def _record(self, source, kind, actor, obj_type=None, obj_id=None, payload=None, evidence="", idem_key=None,
                applied=True, result=""):
        """记一条事件。没给幂等键就按对象上的事件序号生成，保证唯一；给了就由调用方保证同一事件同一个键。"""
        if actor not in ACTORS or source not in SOURCES:
            raise StateError(f"actor / source 不合法：{actor} / {source}")
        if idem_key is None:
            n = self.conn.execute("SELECT COUNT(*) FROM events WHERE object_type IS ? AND object_id IS ?",
                                  (obj_type, obj_id)).fetchone()[0]
            idem_key = f"{kind}:{obj_id}:{n + 1}"
        cur = self.conn.execute(
            "INSERT INTO events (idem_key, source, kind, actor, object_type, object_id, payload, evidence, applied, result, at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (idem_key, source, kind, actor, obj_type, obj_id, _json(payload or {}), evidence, int(applied), result, self.now()))
        return cur.lastrowid

    def ingest_event(self, source, kind, idem_key, actor="system", payload=None, object_type=None, object_id=None,
                     evidence="", handler=None):
        """三个事件入口共用：用户消息（source=user）、主动发现（discovery）、Agent 回执（agent）。
        同一个幂等键只处理一次：重复进入只给原事件加 dup_count，不再调用 handler。
        handler(store) 在同一事务里改状态；它抛 StateError 时事件记为 applied=0 并保留原因，状态不变。"""
        if not idem_key:
            raise StateError("事件必须带幂等键")
        with self._tx() as c:
            row = c.execute("SELECT id, applied, result FROM events WHERE idem_key=?", (idem_key,)).fetchone()
            if row:
                c.execute("UPDATE events SET dup_count=dup_count+1, last_seen_at=? WHERE id=?", (self.now(), row["id"]))
                return {"applied": False, "duplicate_of": row["id"], "first_applied": bool(row["applied"]),
                        "result": row["result"]}
            try:
                with self._tx():
                    result = handler(self) if handler else None
                    eid = self._record(source, kind, actor, object_type, object_id, payload, evidence, idem_key,
                                       True, "" if result is None else _json(result))
                return {"applied": True, "event_id": eid, "result": result}
            except StateError as e:
                # 内层已回滚 handler 的改动；事件本身要留痕，记为拒绝
                eid = self._record(source, kind, actor, object_type, object_id, payload, evidence, idem_key, False, str(e))
                return {"applied": False, "event_id": eid, "rejected": str(e)}

    # ---------- 读取 ----------
    def _row(self, table, key, value):
        r = self.conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
        return dict(r) if r else None

    def get_goal(self, goal_id):
        g = self._row("goals", "id", goal_id)
        if not g:
            raise NotFound(f"没有这个目标：{goal_id}")
        return g

    def get_task(self, task_id):
        t = self._row("tasks", "id", task_id)
        if not t:
            raise NotFound(f"没有这个任务：{task_id}")
        t["depends_on"] = _loads(t["depends_on"], [])
        return t

    def get_run(self, run_id):
        r = self._row("runs", "id", run_id) or self._row("runs", "order_no", run_id)
        if not r:
            raise NotFound(f"没有这个 Run 或工单：{run_id}")
        r["receipt"], r["evidence"] = _loads(r["receipt"], {}), _loads(r["evidence"], {})
        return r

    def show(self, obj_id):
        for getter in (self.get_goal, self.get_task, self.get_run):
            try:
                return getter(obj_id)
            except NotFound:
                continue
        raise NotFound(f"没有这个对象：{obj_id}")

    def list_goals(self, status=None):
        q, args = "SELECT * FROM goals", ()
        if status:
            q, args = q + " WHERE status=?", (status,)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY created_at, id", args)]

    def list_tasks(self, goal_id=None, status=None):
        where, args = [], []
        if goal_id:
            where.append("goal_id=?"), args.append(goal_id)
        if status:
            where.append("status=?"), args.append(status)
        q = "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at, id"
        out = []
        for r in self.conn.execute(q, args):
            t = dict(r)
            t["depends_on"] = _loads(t["depends_on"], [])
            out.append(t)
        return out

    def list_runs(self, task_id=None, status=None, unlinked=False):
        where, args = [], []
        if task_id:
            where.append("task_id=?"), args.append(task_id)
        if status:
            where.append("status=?"), args.append(status)
        if unlinked:
            where.append("task_id IS NULL")
        q = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at, id"
        out = []
        for r in self.conn.execute(q, args):
            d = dict(r)
            d["receipt"], d["evidence"] = _loads(d["receipt"], {}), _loads(d["evidence"], {})
            out.append(d)
        return out

    def events(self, object_type=None, object_id=None, kind=None):
        where, args = [], []
        if object_type:
            where.append("object_type=?"), args.append(object_type)
        if object_id:
            where.append("object_id=?"), args.append(object_id)
        if kind:
            where.append("kind=?"), args.append(kind)
        q = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id"
        out = []
        for r in self.conn.execute(q, args):
            e = dict(r)
            e["payload"] = _loads(e["payload"], {})
            out.append(e)
        return out

    def refs(self, object_type, object_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM refs WHERE object_type=? AND object_id=? ORDER BY id", (object_type, object_id))]

    def summary(self):
        out = {"path": str(self.path), "schema_version": self.version()}
        for table in ("goals", "tasks", "runs"):
            out[table] = {r[0]: r[1] for r in self.conn.execute(f"SELECT status, COUNT(*) FROM {table} GROUP BY status")}
        out["events"] = self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        out["refs"] = self.conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
        return out

    # ---------- 编号与通用转换 ----------
    def _new_id(self, table, prefix):
        day = f"{self.clock():%Y%m%d}"
        like = f"{prefix}{day}-%"
        seq = [int(r[0].rsplit("-", 1)[1]) for r in self.conn.execute(f"SELECT id FROM {table} WHERE id LIKE ?", (like,))
               if r[0].rsplit("-", 1)[1].isdigit()]
        return f"{prefix}{day}-{max(seq, default=0) + 1}"

    def _touch(self, table, obj_id, **fields):
        fields["updated_at"] = self.now()
        sets = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), obj_id))

    def _task_to(self, task, to, actor, source="system", kind=None, reason="", **fields):
        """任务状态转换：查表，非法就拒绝；成功记一条事件。"""
        if task["status"] == to:
            return False
        if (task["status"], to) not in TASK_TRANSITIONS:
            raise IllegalTransition(f"任务 {task['id']} 不能从 {task['status']} 变成 {to}")
        if to == "blocked":
            if fields.get("blocked_kind") not in BLOCK_KINDS or not reason:
                raise StateError("阻塞必须写明类型（decision / dependency / external / technical）和原因")
            fields["blocked_reason"] = reason
        elif task["status"] == "blocked":
            fields.setdefault("blocked_kind", None)
            fields.setdefault("blocked_reason", "")
        self._touch("tasks", task["id"], status=to, **fields)
        self._record(source, kind or f"task.{to}", actor, "task", task["id"],
                     {"from": task["status"], "to": to, "reason": reason, **{k: v for k, v in fields.items() if k != "blocked_reason"}})
        task["status"] = to
        return True

    def _run_to(self, run, to, actor, source="agent", **fields):
        if run["status"] == to:
            return False
        if (run["status"], to) not in RUN_TRANSITIONS:
            raise IllegalTransition(f"Run {run['id']} 不能从 {run['status']} 变成 {to}")
        if to not in RUN_ACTIVE and not run.get("ended_at") and to != "stale":
            fields["ended_at"] = self.now()
        self._touch("runs", run["id"], status=to, **fields)
        self._record(source, f"run.{to}", actor, "run", run["id"], {"from": run["status"], "to": to})
        run["status"] = to
        return True

    # ---------- Goal ----------
    def create_goal(self, title, by, description="", status="observed", scope="", project=None, phase="", done_when="",
                    idem_key=None):
        if status not in ("observed", "authorized"):
            raise StateError("新建目标只能是 observed 或 authorized")
        if status == "authorized" and by != "user":
            raise NotAuthorized("只有常浩本人能授权目标；别人新建的目标先是 observed")
        with self._tx() as c:
            gid = self._new_id("goals", "G")
            now = self.now()
            c.execute("INSERT INTO goals (id, title, description, status, scope, project, phase, done_when, authorized_at,"
                      " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (gid, title, description, status, scope, project, phase, done_when,
                       now if status == "authorized" else None, now, now))
            self._record("user" if by == "user" else "discovery", "goal.create", by, "goal", gid,
                         {"title": title, "status": status}, idem_key=idem_key)
            return self.get_goal(gid)

    def authorize_goal(self, goal_id, by, note=""):
        if by != "user":
            raise NotAuthorized("只有常浩本人能授权目标")
        with self._tx():
            g = self.get_goal(goal_id)
            if g["status"] == "authorized":
                return {"applied": False, "status": "authorized"}
            if g["status"] != "observed":
                raise IllegalTransition(f"目标 {goal_id} 当前是 {g['status']}，不能再授权")
            self._touch("goals", goal_id, status="authorized", authorized_at=self.now())
            self._record("user", "goal.authorize", by, "goal", goal_id, {"note": note})
            return {"applied": True, "status": "authorized"}

    def complete_goal(self, goal_id, by, note=""):
        """完成目标是常浩的决定：所有任务都已完成或撤销，且至少有一个完成的任务或一条目标证据。"""
        if by != "user":
            raise NotAuthorized("只有常浩本人能把目标标为完成")
        with self._tx():
            g = self.get_goal(goal_id)
            if g["status"] == "completed":
                return {"applied": False, "status": "completed"}
            if g["status"] != "authorized":
                raise IllegalTransition(f"目标 {goal_id} 当前是 {g['status']}，不能完成")
            tasks = self.list_tasks(goal_id)
            open_tasks = [t["id"] for t in tasks if t["status"] not in ("completed", "canceled")]
            if open_tasks:
                raise IllegalTransition(f"目标 {goal_id} 还有没收尾的任务：{'、'.join(open_tasks)}")
            if not any(t["status"] == "completed" for t in tasks) and not self.refs("goal", goal_id):
                raise EvidenceMissing(f"目标 {goal_id} 没有任何完成的任务或证据，不能标完成")
            self._touch("goals", goal_id, status="completed")
            self._record("user", "goal.complete", by, "goal", goal_id, {"note": note})
            return {"applied": True, "status": "completed"}

    def cancel_goal(self, goal_id, by, reason=""):
        if by != "user":
            raise NotAuthorized("只有常浩本人能撤销目标")
        with self._tx():
            g = self.get_goal(goal_id)
            if g["status"] == "canceled":
                return {"applied": False, "status": "canceled"}
            if g["status"] == "completed":
                raise IllegalTransition(f"目标 {goal_id} 已完成，不能撤销")
            self._touch("goals", goal_id, status="canceled")
            self._record("user", "goal.cancel", by, "goal", goal_id, {"reason": reason})
            for t in self.list_tasks(goal_id):
                if t["status"] not in ("completed", "canceled"):
                    self._task_to(t, "canceled", by, "user", reason=f"目标已撤销：{reason}")
            return {"applied": True, "status": "canceled"}

    # ---------- Task ----------
    def create_task(self, goal_id, title, by, acceptance="", next_step="", depends_on=(), owner="hermes", idem_key=None):
        with self._tx() as c:
            g = self.get_goal(goal_id)
            if g["status"] in ("completed", "canceled"):
                raise IllegalTransition(f"目标 {goal_id} 已 {g['status']}，不能再加任务")
            for dep in depends_on:
                self.get_task(dep)
            tid = self._new_id("tasks", "T")
            now = self.now()
            c.execute("INSERT INTO tasks (id, goal_id, title, status, depends_on, acceptance, next_step, owner, created_at,"
                      " updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (tid, goal_id, title, "observed", _json(list(depends_on)), acceptance, next_step, owner, now, now))
            self._record("user" if by == "user" else "discovery", "task.create", by, "task", tid,
                         {"title": title, "goal": goal_id}, idem_key=idem_key)
            return self.get_task(tid)

    def _unmet(self, task):
        return [d for d in task["depends_on"] if self.get_task(d)["status"] != "completed"]

    def set_ready(self, task_id, by, note=""):
        """observed → ready：目标必须已授权；依赖没完成就先 blocked(dependency)，依赖完成后 recover() 会放行。"""
        with self._tx():
            t = self.get_task(task_id)
            g = self.get_goal(t["goal_id"])
            if g["status"] != "authorized":
                raise NotAuthorized(f"目标 {g['id']} 当前是 {g['status']}，未经常浩授权的任务不能进入 ready")
            if t["status"] == "ready":
                return {"applied": False, "status": "ready"}
            if t["status"] != "observed":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}：阻塞用 unblock / retry 解，review 用 rework 退回")
            unmet = self._unmet(t)
            if unmet:
                self._task_to(t, "blocked", by, "user" if by == "user" else "system", reason=f"等依赖完成：{'、'.join(unmet)}",
                              blocked_kind="dependency")
                return {"applied": True, "status": "blocked", "unmet": unmet}
            self._task_to(t, "ready", by, "user" if by == "user" else "system", reason=note)
            return {"applied": True, "status": "ready"}

    def block_task(self, task_id, by, kind, reason):
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] == "blocked" and t["blocked_kind"] == kind and t["blocked_reason"] == reason:
                return {"applied": False, "status": "blocked"}
            if t["status"] == "blocked":
                self._touch("tasks", task_id, blocked_kind=kind, blocked_reason=reason)
                self._record("system", "task.block.update", by, "task", task_id, {"kind": kind, "reason": reason})
                return {"applied": True, "status": "blocked"}
            self._task_to(t, "blocked", by, "user" if by == "user" else "system", reason=reason, blocked_kind=kind)
            return {"applied": True, "status": "blocked"}

    def unblock_task(self, task_id, by, note=""):
        """解阻塞：decision 类只有常浩能解；dependency 类要依赖真的完成了（常浩可以强制）；其余任何人带说明都可以。"""
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] != "blocked":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，没有阻塞可解")
            if t["blocked_kind"] == "decision" and by != "user":
                raise NotAuthorized(f"任务 {task_id} 等的是常浩的决定（{t['blocked_reason']}），{by} 不能替他解开")
            if t["blocked_kind"] == "dependency" and by != "user" and self._unmet(t):
                raise IllegalTransition(f"任务 {task_id} 的依赖还没完成：{'、'.join(self._unmet(t))}")
            if self.get_goal(t["goal_id"])["status"] != "authorized":
                raise NotAuthorized(f"目标 {t['goal_id']} 已不在授权状态，不能继续推进")
            self._task_to(t, "ready", by, "user" if by == "user" else "system", kind="task.unblock", reason=note)
            return {"applied": True, "status": "ready"}

    def retry_task(self, task_id, by):
        """技术类失败的自动重试：只对 technical 阻塞有效，最多 MAX_RETRIES 次；decision / external 类不能靠重试绕过。"""
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] != "blocked":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，不是失败待重试")
            if t["blocked_kind"] != "technical":
                raise NotAuthorized(f"任务 {task_id} 的阻塞是 {t['blocked_kind']} 类（{t['blocked_reason']}），不能用技术重试绕过")
            if t["retries"] >= MAX_RETRIES:
                raise StateError(f"任务 {task_id} 已重试 {t['retries']} 次，超过上限，要常浩定怎么办")
            if self.get_goal(t["goal_id"])["status"] != "authorized":
                raise NotAuthorized(f"目标 {t['goal_id']} 已不在授权状态，不能重试")
            self._task_to(t, "ready", by, "system", kind="task.retry", reason=f"第 {t['retries'] + 1} 次重试",
                          retries=t["retries"] + 1)
            return {"applied": True, "status": "ready", "retries": t["retries"] + 1}

    def task_evidence(self, task_id):
        return {"refs": self.refs("task", task_id),
                "runs": [r["id"] for r in self.list_runs(task_id) if r["status"] in RUN_SUCCESS]}

    def complete_task(self, task_id, by, note=""):
        """review → completed：要有证据（外部引用，或成功的 Run）。"""
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] == "completed":
                return {"applied": False, "status": "completed"}
            if t["status"] != "review":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，只有 review 里的任务能标完成")
            ev = self.task_evidence(task_id)
            if not ev["refs"] and not ev["runs"]:
                raise EvidenceMissing(f"任务 {task_id} 没有证据（没有外部引用，也没有成功的 Run），不能标完成")
            self._task_to(t, "completed", by, "user" if by == "user" else "system", reason=note, next_step="")
            return {"applied": True, "status": "completed", "evidence": ev}

    def rework_task(self, task_id, by, note):
        """review → ready：验证没过，退回重做。"""
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] != "review":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，不在 review")
            self._task_to(t, "ready", by, "user" if by == "user" else "system", kind="task.rework", reason=note)
            return {"applied": True, "status": "ready"}

    def cancel_task(self, task_id, by, reason=""):
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] == "canceled":
                return {"applied": False, "status": "canceled"}
            if t["status"] != "observed" and by != "user":
                raise NotAuthorized(f"任务 {task_id} 已经授权推进（{t['status']}），只有常浩能撤销")
            self._task_to(t, "canceled", by, "user" if by == "user" else "system", reason=reason)
            for r in self.list_runs(task_id):
                if r["status"] in RUN_ACTIVE:
                    self._run_to(r, "abandoned", by, "system")
            return {"applied": True, "status": "canceled"}

    def update_task(self, task_id, by, **fields):
        allowed = {"next_step", "acceptance", "owner", "title"}
        bad = set(fields) - allowed
        if bad:
            raise StateError(f"不能这样改任务字段：{'、'.join(sorted(bad))}")
        with self._tx():
            self.get_task(task_id)
            self._touch("tasks", task_id, **fields)
            self._record("system", "task.update", by, "task", task_id, fields)
            return self.get_task(task_id)

    # ---------- Run ----------
    def start_run(self, task_id, agent, by, order_no=None, session_id=None, status="running", idem_key=None, note=""):
        """给 ready 的任务开一次执行。一个任务同时只能有一个进行中的 Run；带幂等键时同一键只开一次。"""
        if status not in RUN_ACTIVE:
            raise StateError(f"新 Run 的状态只能是 {'/'.join(RUN_ACTIVE)}")
        with self._tx() as c:
            if idem_key:
                row = c.execute("SELECT id, result FROM events WHERE idem_key=?", (idem_key,)).fetchone()
                if row:
                    c.execute("UPDATE events SET dup_count=dup_count+1, last_seen_at=? WHERE id=?", (self.now(), row["id"]))
                    rid = _loads(row["result"], {}).get("run")
                    return {"applied": False, "duplicate_of": row["id"], "run": self.get_run(rid) if rid else None}
            t = self.get_task(task_id)
            active = [r["id"] for r in self.list_runs(task_id) if r["status"] in RUN_ACTIVE]
            if active:
                raise IllegalTransition(f"任务 {task_id} 已有进行中的 Run：{'、'.join(active)}，不重复派单")
            if t["status"] != "ready":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，只有 ready 的任务能开始执行")
            if self.get_goal(t["goal_id"])["status"] != "authorized":
                raise NotAuthorized(f"目标 {t['goal_id']} 不在授权状态，不能执行")
            if order_no and c.execute("SELECT 1 FROM runs WHERE order_no=?", (order_no,)).fetchone():
                raise StateError(f"工单 {order_no} 已登记过，要关联请用 link_run")
            rid = self._new_id("runs", "R")
            now = self.now()
            c.execute("INSERT INTO runs (id, task_id, agent, order_no, status, session_id, note, started_at, heartbeat_at,"
                      " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, task_id, agent, order_no, status, session_id, note,
                       now if status == "running" else None, now, now, now))
            self._task_to(t, "running", by, "user" if by == "user" else "system", reason=f"开跑 {rid}")
            self._record("system", "run.start", by, "run", rid, {"task": task_id, "agent": agent, "order_no": order_no},
                         idem_key=idem_key, result=_json({"run": rid}))
            return {"applied": True, "run": self.get_run(rid)}

    def heartbeat(self, run_id):
        with self._tx():
            r = self.get_run(run_id)
            if r["status"] != "running":
                raise IllegalTransition(f"Run {r['id']} 不在 running（{r['status']}），没有心跳可记")
            self._touch("runs", r["id"], heartbeat_at=self.now())

    def _settle_task_after_run(self, run, by):
        """Run 到了终态，把结果反映到任务上：done → review；need_decision / guard → blocked(decision)；
        failed / stale → blocked(technical)。任务不在 running（没关联或已另行处理）就不动它。"""
        if not run["task_id"]:
            return None
        t = self.get_task(run["task_id"])
        if t["status"] != "running":
            return None
        receipt = run["receipt"]
        rid = run["id"]
        if receipt.get("need_decision"):
            self._task_to(t, "blocked", by, "agent", reason=f"{rid} 需要常浩定：{receipt['need_decision']}", blocked_kind="decision")
        elif run["status"] == "done":
            self._task_to(t, "review", by, "agent", reason=f"{rid} 跑完", next_step=f"核对 {rid} 的回执、测试与 PR")
        elif run["status"] == "guard":
            self._task_to(t, "blocked", by, "agent", reason=f"{rid} 触发护栏（主分支或远端被动过），不重试", blocked_kind="decision")
        elif run["status"] in ("failed", "stale"):
            why = receipt.get("worker_error") or run.get("note") or run["status"]
            self._task_to(t, "blocked", by, "agent", reason=f"{rid} {('超时' if receipt.get('timed_out') else '失败')}：{why}",
                          blocked_kind="technical")
        elif run["status"] == "abandoned":
            self._task_to(t, "blocked", by, "agent", reason=f"{rid} 已放弃，要常浩定下一步", blocked_kind="decision")
        return t["status"]

    def finish_run(self, run_id, status, by, receipt=None, evidence=None, note="", idem_key=None):
        """记 Run 的结果。同一回执重复进入是空操作；终态后再给不同结果会被拒绝。"""
        receipt = clip_receipt(receipt)
        idem_key = idem_key or receipt_key(run_id, {"status": status, "receipt": receipt, "evidence": evidence or {}})

        def apply(store):
            r = store.get_run(run_id)
            if r["status"] not in RUN_ACTIVE and r["status"] != "stale":
                raise IllegalTransition(f"Run {r['id']} 已是 {r['status']}，不能再改成 {status}")
            merged = {**r["receipt"], **receipt}
            ev = {**r["evidence"], **receipt_evidence(merged), **(evidence or {})}
            store._touch("runs", r["id"], receipt=_json(merged), evidence=_json(ev),
                         session_id=merged.get("session_id") or r["session_id"], note=note or r["note"])
            r["receipt"], r["evidence"] = merged, ev
            source = "agent" if by in ("claude", "codex") else "system"
            if r["status"] in ("new", "queued") and status not in RUN_ACTIVE:
                store._run_to(r, "running", by, source)  # 还没开跑就有了结果：补一步 running，保持转换表完整
            store._run_to(r, status, by, source)
            return {"run": r["id"], "status": status, "task_status": store._settle_task_after_run(r, by)}

        out = self.ingest_event("agent" if by in ("claude", "codex", "hermes") else "system", "run.finish", idem_key, by,
                                {"status": status}, "run", run_id, handler=apply)
        if out.get("rejected"):
            raise IllegalTransition(out["rejected"])
        return out

    def receive_receipt(self, run_id, receipt, by="hermes", idem_key=None):
        """Agent 回执入口：按回执内容判结果（护栏 → guard，超时 / 出错 / 非零退出 → failed，否则 done）。"""
        receipt = clip_receipt(receipt)
        if receipt.get("guard_main_changed") or receipt.get("guard_remote_changed"):
            status = "guard"
        elif receipt.get("timed_out") or receipt.get("worker_error") or (receipt.get("exit_code") or 0) != 0:
            status = "failed"
        else:
            status = "done"
        return self.finish_run(run_id, status, by, receipt, idem_key=idem_key or receipt_key(run_id, receipt))

    def update_run(self, run_id, status, by, evidence=None, note=""):
        """done 之后的台账变化（merged / cleaned / abandoned）。"""
        with self._tx():
            r = self.get_run(run_id)
            if r["status"] == status:
                return {"applied": False, "status": status}
            ev = {**r["evidence"], **(evidence or {})}
            self._touch("runs", r["id"], evidence=_json(ev), note=note or r["note"])
            self._run_to(r, status, by, "system")
            return {"applied": True, "status": status, "task_status": self._settle_task_after_run(r, by)}

    def link_run(self, run_id, task_id, by):
        """把既有 Run（按 id 或工单号）关联到任务。进行中的 Run 关联到 ready 的任务时，任务进入 running。"""
        with self._tx():
            r = self.get_run(run_id)
            t = self.get_task(task_id)
            if r["task_id"] == task_id:
                return {"applied": False, "run": r["id"]}
            if r["task_id"]:
                raise StateError(f"Run {r['id']} 已关联任务 {r['task_id']}，不能再改")
            self._touch("runs", r["id"], task_id=task_id)
            self._record("system", "run.link", by, "run", r["id"], {"task": task_id, "order_no": r["order_no"]})
            if r["status"] in RUN_ACTIVE and t["status"] == "ready":
                if self.get_goal(t["goal_id"])["status"] != "authorized":
                    raise NotAuthorized(f"目标 {t['goal_id']} 不在授权状态")
                self._task_to(t, "running", by, "system", reason=f"关联进行中的 {r['id']}")
            return {"applied": True, "run": r["id"], "task_status": t["status"]}

    # ---------- 外部引用与证据 ----------
    def add_ref(self, object_type, object_id, kind, ref, url="", note="", by="system"):
        if object_type not in OBJECT_TYPES:
            raise StateError(f"对象类型只能是 {'/'.join(OBJECT_TYPES)}")
        with self._tx() as c:
            self.show(object_id)
            cur = c.execute("INSERT OR IGNORE INTO refs (object_type, object_id, kind, ref, url, note, created_at)"
                            " VALUES (?,?,?,?,?,?,?)", (object_type, object_id, kind, ref, url, note, self.now()))
            if cur.rowcount:
                self._record("system", "ref.add", by, object_type, object_id, {"kind": kind, "ref": ref, "url": url})
            return {"applied": bool(cur.rowcount)}

    def check_ref(self, object_type, object_id, kind, ref, probe, by="system"):
        """核对外部来源。probe(kind, ref) 返回 dict；返回 None 或抛异常都算不可达：只记事件，不动任何状态，
        更不会据此推断任务完成。"""
        try:
            seen = probe(kind, ref)
        except Exception as e:  # noqa: BLE001 —— 外部来源的任何异常都只算“不可达”
            seen = None
            why = f"{type(e).__name__}: {e}"[:200]
        else:
            why = "probe 返回空"
        with self._tx():
            if seen is None:
                self._record("system", "ref.unreachable", by, object_type, object_id, {"kind": kind, "ref": ref, "why": why})
                return {"reachable": False, "why": why}
            self._record("system", "ref.checked", by, object_type, object_id, {"kind": kind, "ref": ref, "seen": seen})
            return {"reachable": True, "seen": seen}

    # ---------- 与派单台账兼容 ----------
    @staticmethod
    def read_orders(dispatch_dir=None):
        """只读扫描派单目录：每张单的 order.json，有回执就带上。坏文件跳过。"""
        root = Path(dispatch_dir or DISPATCH).expanduser()
        out = []
        for f in sorted(root.glob("D*/order.json")):
            try:
                o = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(o, dict) or not o.get("no"):
                continue
            rc = f.parent / "out/receipt.json"
            try:
                o["_receipt"] = json.loads(rc.read_text(encoding="utf-8")) if rc.is_file() else {}
                if not isinstance(o["_receipt"], dict):
                    o["_receipt"] = {}
            except (OSError, ValueError):
                o["_receipt"] = {}
            o["_dir"] = str(f.parent)
            out.append(o)
        return out

    def sync_orders(self, dispatch_dir=None, by="system"):
        """把派单台账同步成 Run：新工单登记为未关联的 Run（不建目标、不建任务）；状态变了才更新；
        已关联任务的 Run 到终态时按同一套规则反映到任务。同一份 order.json（同一个 updated）只处理一次。"""
        stats = {"seen": 0, "created": 0, "updated": 0, "unchanged": 0, "skipped": []}
        for o in self.read_orders(dispatch_dir):
            stats["seen"] += 1
            status = o.get("status")
            if status not in RUN_STATUSES:
                stats["skipped"].append(f"{o['no']}：状态 {status} 不认识")
                continue
            key = f"order.sync:{o['no']}:{o.get('updated') or o.get('created')}:{status}"
            res = self.ingest_event("system", "order.sync", key, by, {"no": o["no"], "status": status}, "run", o["no"],
                                    handler=lambda s, o=o, status=status: s._apply_order(o, status, by))
            if not res["applied"]:
                stats["unchanged"] += 1
            elif res["result"] == "created":
                stats["created"] += 1
            else:
                stats["updated"] += 1
        return stats

    def _apply_order(self, o, status, by):
        receipt = clip_receipt(o.get("_receipt"))
        evidence = {k: o[k] for k in ("project", "mode", "branch", "base", "issue", "issue_url", "pr", "pr_url",
                                      "merged_to", "cwd", "exit_code") if k in o}
        evidence.update(receipt_evidence(receipt))
        evidence["order_dir"] = o.get("_dir", "")
        row = self.conn.execute("SELECT id FROM runs WHERE order_no=?", (o["no"],)).fetchone()
        if not row:
            rid = self._new_id("runs", "R")
            now = self.now()
            self.conn.execute(
                "INSERT INTO runs (id, task_id, agent, order_no, status, session_id, receipt, evidence, note, started_at,"
                " ended_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, None, o.get("tool") or "unknown", o["no"], status, receipt.get("session_id"), _json(receipt),
                 _json(evidence), "来自派单台账", receipt.get("started") or o.get("created"),
                 receipt.get("ended") if status not in RUN_ACTIVE else None, now, now))
            self._record("system", "run.import", by, "run", rid, {"order_no": o["no"], "status": status})
            return "created"
        r = self.get_run(row["id"])
        merged = {**r["receipt"], **receipt}
        ev = {**r["evidence"], **evidence}
        self._touch("runs", r["id"], receipt=_json(merged), evidence=_json(ev),
                    session_id=merged.get("session_id") or r["session_id"])
        r["receipt"], r["evidence"] = merged, ev
        if r["status"] != status:
            if (r["status"], status) not in RUN_TRANSITIONS:
                # 台账是执行层的事实来源：和本地状态机对不上时以台账为准，但记下来
                self._touch("runs", r["id"], status=status, ended_at=r["ended_at"] or (None if status in RUN_ACTIVE else self.now()))
                self._record("system", "run.resync", by, "run", r["id"], {"from": r["status"], "to": status, "note": "台账跳过了中间状态"})
                r["status"] = status
            else:
                self._run_to(r, status, by, "system")
            if status not in RUN_ACTIVE:
                self._settle_task_after_run(r, by)
        return "updated"

    # ---------- 恢复 ----------
    def recover(self, now=None, stale_after=STALE_AFTER, probe=None, by="system"):
        """重启或定时巡检时调用。
        - running 的 Run：probe(run) 能给出台账状态就按它定；给不出时，超过 stale_after 秒没心跳的标 stale，任务转 blocked(technical)。
        - running 却没有进行中 Run 的任务：blocked(technical)。
        - blocked(dependency) 的任务：依赖都完成了就放行到 ready（条件恢复）。
        每一步都是独立事务，做到哪一步记到哪一步。"""
        now = now or self.clock()
        out = {"stale": [], "settled": [], "orphaned": [], "released": [], "kept": []}
        for r in self.list_runs(status="running"):
            seen = None
            if probe:
                try:
                    seen = probe(r)
                except Exception:  # noqa: BLE001
                    seen = None
            if seen in RUN_STATUSES and seen != "running":
                with self._tx():
                    r = self.get_run(r["id"])
                    if (r["status"], seen) in RUN_TRANSITIONS:
                        self._run_to(r, seen, by, "system")
                        self._settle_task_after_run(r, by)
                        out["settled"].append(f"{r['id']}→{seen}")
                        continue
            last = _parse(r["heartbeat_at"] or r["started_at"] or r["created_at"])
            age = (now - last).total_seconds() if last else None
            if age is not None and age <= stale_after:
                out["kept"].append(r["id"])
                continue
            with self._tx():
                r = self.get_run(r["id"])
                self._touch("runs", r["id"], note=f"恢复时发现 {int(age) if age else '?'} 秒没有心跳")
                r["note"] = f"恢复时发现 {int(age) if age is not None else '?'} 秒没有心跳"
                self._run_to(r, "stale", by, "system")
                self._settle_task_after_run(r, by)
                out["stale"].append(r["id"])
        for t in self.list_tasks(status="running"):
            if not any(r["status"] in RUN_ACTIVE for r in self.list_runs(t["id"])):
                with self._tx():
                    self._task_to(self.get_task(t["id"]), "blocked", by, "system", reason="恢复时没有进行中的 Run",
                                  blocked_kind="technical")
                out["orphaned"].append(t["id"])
        for t in self.list_tasks(status="blocked"):
            if t["blocked_kind"] == "dependency" and not self._unmet(t) \
                    and self.get_goal(t["goal_id"])["status"] == "authorized":
                with self._tx():
                    self._task_to(self.get_task(t["id"]), "ready", by, "system", kind="task.unblock", reason="依赖已完成")
                out["released"].append(t["id"])
        with self._tx():
            self._record("system", "recover", by, None, None, {k: v for k, v in out.items() if v})
        return out


# ---------- 命令行 ----------
def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False, default=str))
    sys.exit(0 if ok else 1)


def _by(a):
    if a.by not in ACTORS:
        done(False, f"--by 只能是 {'/'.join(ACTORS)}")
    return a.by


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        done(False, f"读不了 JSON 文件 {path}：{e}")


def _type_of(obj_id):
    return {"G": "goal", "T": "task"}.get(obj_id[:1].upper(), "run")


COMMANDS = {
    "init": [], "backup": [("--to",)], "restore": [("--from", dict(dest="src", required=True)), ("--yes", dict(action="store_true"))],
    "recover": [("--stale-after", dict(type=int, default=STALE_AFTER)), ("--dispatch",), ("--no-sync", dict(action="store_true"))],
    "sync-orders": [("--dispatch",)],
    "goal-new": [("--title", dict(required=True)), ("--by", dict(required=True)), ("--description", dict(default="")),
                 ("--status", dict(default="observed")), ("--scope", dict(default="")), ("--project",),
                 ("--phase", dict(default="")), ("--done-when", dict(default="")), ("--key",)],
    "goal-authorize": [("id",), ("--by", dict(required=True)), ("--note", dict(default=""))],
    "goal-complete": [("id",), ("--by", dict(required=True)), ("--note", dict(default=""))],
    "goal-cancel": [("id",), ("--by", dict(required=True)), ("--reason", dict(default=""))],
    "task-new": [("--goal", dict(required=True)), ("--title", dict(required=True)), ("--by", dict(required=True)),
                 ("--acceptance", dict(default="")), ("--next", dict(default="")), ("--depends", dict(default="")),
                 ("--owner", dict(default="hermes")), ("--key",)],
    "task-ready": [("id",), ("--by", dict(required=True)), ("--note", dict(default=""))],
    "task-block": [("id",), ("--by", dict(required=True)), ("--kind", dict(required=True, choices=BLOCK_KINDS)),
                   ("--reason", dict(required=True))],
    "task-unblock": [("id",), ("--by", dict(required=True)), ("--note", dict(default=""))],
    "task-retry": [("id",), ("--by", dict(required=True))],
    "task-complete": [("id",), ("--by", dict(required=True)), ("--note", dict(default=""))],
    "task-rework": [("id",), ("--by", dict(required=True)), ("--note", dict(required=True))],
    "task-cancel": [("id",), ("--by", dict(required=True)), ("--reason", dict(default=""))],
    "run-start": [("--task", dict(required=True)), ("--agent", dict(required=True)), ("--by", dict(required=True)),
                  ("--order",), ("--session",), ("--key",)],
    "run-finish": [("id",), ("--status", dict(required=True, choices=RUN_STATUSES)), ("--by", dict(required=True)),
                   ("--receipt",), ("--key",)],
    "run-receipt": [("id",), ("--receipt", dict(required=True)), ("--by", dict(default="hermes")), ("--key",)],
    "run-link": [("run",), ("--task", dict(required=True)), ("--by", dict(required=True))],
    "ref-add": [("--type", dict(required=True, choices=OBJECT_TYPES)), ("--id", dict(required=True)),
                ("--kind", dict(required=True)), ("--ref", dict(required=True)), ("--url", dict(default="")),
                ("--note", dict(default=""))],
    "show": [("id",)], "list": [("--goal",), ("--status",)], "events": [("id",)],
}


def run_cli(a):
    with Store(a.db) as s:
        if a.cmd == "init":
            done(True, f"状态库就绪：{s.path}（schema v{s.version()}）", **s.summary())
        if a.cmd == "backup":
            done(True, f"已备份到 {s.backup(a.to)}")
        if a.cmd == "restore":
            if not a.yes:
                done(False, "restore 会整体替换当前状态库，确认后加 --yes")
            done(True, f"已从 {a.src} 恢复", **s.restore(a.src))
        if a.cmd == "sync-orders":
            done(True, "派单台账已同步", **s.sync_orders(a.dispatch))
        if a.cmd == "recover":
            synced = None if a.no_sync else s.sync_orders(a.dispatch)
            out = s.recover(stale_after=a.stale_after)
            done(True, "恢复完成：" + ("；".join(f"{k} {len(v)}" for k, v in out.items() if v) or "没有需要处理的"),
                 synced=synced, **out)
        if a.cmd == "goal-new":
            g = s.create_goal(a.title, _by(a), a.description, a.status, a.scope, a.project, a.phase, a.done_when, a.key)
            done(True, f"已建目标 {g['id']}（{g['status']}）", goal=g)
        if a.cmd == "goal-authorize":
            done(True, f"目标 {a.id} 已授权", **s.authorize_goal(a.id, _by(a), a.note))
        if a.cmd == "goal-complete":
            done(True, f"目标 {a.id} 已完成", **s.complete_goal(a.id, _by(a), a.note))
        if a.cmd == "goal-cancel":
            done(True, f"目标 {a.id} 已撤销", **s.cancel_goal(a.id, _by(a), a.reason))
        if a.cmd == "task-new":
            deps = [d for d in a.depends.split(",") if d]
            t = s.create_task(a.goal, a.title, _by(a), a.acceptance, a.next, deps, a.owner, a.key)
            done(True, f"已建任务 {t['id']}（observed，归 {a.goal}）", task=t)
        if a.cmd == "task-ready":
            r = s.set_ready(a.id, _by(a), a.note)
            done(True, f"任务 {a.id} 现在是 {r['status']}", **r)
        if a.cmd == "task-block":
            done(True, f"任务 {a.id} 已阻塞（{a.kind}）", **s.block_task(a.id, _by(a), a.kind, a.reason))
        if a.cmd == "task-unblock":
            done(True, f"任务 {a.id} 已解阻塞", **s.unblock_task(a.id, _by(a), a.note))
        if a.cmd == "task-retry":
            r = s.retry_task(a.id, _by(a))
            done(True, f"任务 {a.id} 第 {r['retries']} 次重试，回到 ready", **r)
        if a.cmd == "task-complete":
            done(True, f"任务 {a.id} 已完成", **s.complete_task(a.id, _by(a), a.note))
        if a.cmd == "task-rework":
            done(True, f"任务 {a.id} 退回 ready", **s.rework_task(a.id, _by(a), a.note))
        if a.cmd == "task-cancel":
            done(True, f"任务 {a.id} 已撤销", **s.cancel_task(a.id, _by(a), a.reason))
        if a.cmd == "run-start":
            r = s.start_run(a.task, a.agent, _by(a), a.order, a.session, idem_key=a.key)
            if not r["applied"]:
                done(True, f"同一事件已处理过，不重复开跑（Run {r['run']['id'] if r['run'] else '?'}）", **r)
            done(True, f"已开始 {r['run']['id']}（任务 {a.task} → running）", **r)
        if a.cmd == "run-finish":
            rc = _read_json(a.receipt) if a.receipt else None
            done(True, f"Run {a.id} 记为 {a.status}", **s.finish_run(a.id, a.status, _by(a), rc, idem_key=a.key))
        if a.cmd == "run-receipt":
            out = s.receive_receipt(a.id, _read_json(a.receipt), _by(a), a.key)
            done(True, "回执已处理" if out["applied"] else "同一回执已处理过，不重复更新", **out)
        if a.cmd == "run-link":
            done(True, f"Run {a.run} 已关联任务 {a.task}", **s.link_run(a.run, a.task, _by(a)))
        if a.cmd == "ref-add":
            done(True, f"{a.type} {a.id} 加引用 {a.kind}:{a.ref}", **s.add_ref(a.type, a.id, a.kind, a.ref, a.url, a.note))
        if a.cmd == "show":
            obj = s.show(a.id)
            done(True, a.id, object=obj, refs=s.refs(_type_of(obj["id"]), obj["id"]))
        if a.cmd == "list":
            done(True, "列表", goals=s.list_goals(), tasks=s.list_tasks(a.goal, a.status), runs=s.list_runs(status=a.status))
        if a.cmd == "events":
            done(True, f"{a.id} 的事件", events=s.events(object_id=a.id))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", help=f"状态库路径，默认 $DT_STATE_DB 或 {DEFAULT_DB}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, flags in COMMANDS.items():
        p = sub.add_parser(name)
        for flag, *rest in flags:
            p.add_argument(flag, **(rest[0] if rest else {}))
    a = ap.parse_args(argv)
    try:
        run_cli(a)
    except StateError as e:
        done(False, str(e), error=type(e).__name__)


if __name__ == "__main__":
    main()
