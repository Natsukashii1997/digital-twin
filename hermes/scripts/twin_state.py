"""持久任务状态与事件底座（B1）：Goal / Task / Run / Event / Ref 五类对象，SQLite 存储，只用标准库。

库用法：
    from twin_state import Store
    with Store() as s:
        g = s.create_goal("把日报并跑验收做完", by="user", status="authorized")
        t = s.create_task(g["id"], "补台账状态机", by="hermes")
        s.set_ready(t["id"], by="hermes")
        r = s.start_run(t["id"], agent="claude", by="hermes", order_no="D20261029-1")["run"]
        s.receive_receipt(r["id"], receipt_dict)          # 车间回执（幂等）→ 任务进 review
        s.accept_task(t["id"], by="codex", basis="PR #26 测试 105 项通过", cites=[r["id"]])
        s.complete_task(t["id"], by="hermes")
命令行（每条命令最后输出一行 JSON，字段 ok / msg，和 dispatch.py 一样）：
    python3 twin_state.py init | backup | restore --from F --yes | recover | sync-orders
    python3 twin_state.py goal-new / goal-authorize / goal-complete / goal-cancel
    python3 twin_state.py task-new / task-ready / task-block / task-unblock / task-retry / task-accept /
                          task-complete / task-rework / task-cancel
    python3 twin_state.py run-start / run-finish / run-receipt / run-resume / run-link / ref-add / show / list / events

数据库默认在 ~/Library/Application Support/DigitalTwin/twin-state.db（环境变量 DT_STATE_DB 可改，自测用临时路径），
权限 700/600，不进 GitHub、不进 Obsidian；备份在同目录 backups/。所有写操作都是一个事务：状态变化和事件记录一起落盘，
中途失败整体回滚。

任务主状态：observed（已发现，尚未授权）→ ready（已授权，可以推进）→ running → review → completed；
blocked（有明确阻塞：decision / dependency / external / technical）；canceled。Run 层沿用派单台账的状态
（new / queued / running / done / guard / failed / merged / cleaned / abandoned）另加 stale（过期），不和任务状态混用。

事实归属：Goal / Task 的授权与推进状态以本库为准；Run 的执行结果以派单台账和回执为准（本库只同步，冲突时拦截并记事件）；
合并与代码以 GitHub 为准；常浩的长期认知以 Obsidian 为准；消息原始事实以飞书为准。

授权边界（程序校验）：只有 by="user" 能授权目标、解开 decision 类阻塞、完成或撤销目标、把 decision 类阻塞改成别的类型；
Hermes 只能在已授权目标范围内推进任务。**by="user" 是内部可信调用约定，不是身份认证**：调用方（Hermes 技能）必须先核实
发言人是常浩本人（飞书 open_id），命令行上 --by user 还要带 --sender 并与本机受控配置里的 open_id 一致（环境变量
DT_OWNER_OPEN_ID，或文件 DT_OWNER_FILE，默认 ~/Library/Application Support/DigitalTwin/owner_open_id，单行、权限 600；
两处都没配置时 --by user 一律拒绝）。个人身份标识不写进仓库源码；真正的身份桥接留 B2。
执行完成 ≠ 验收通过：Run done 只把任务送到 review；complete_task 要先有 accept_task 记下的验收结论（执行方不能自验，
结论必须引用证据）。旧 Run 的迟到结果只记在它自己身上，不改当前 Run 正在推进的任务。
旧工单只读同步（sync_orders）：order.json 变成未关联的 Run，不自动推断成长期任务；要关联用 link_run。
当前 Run 记在 tasks.current_run_id（start_run 开出来的、或 link_run 关联进来的进行中的 Run）；后关联的历史 Run 只作证据。
审查 Run（start_run(purpose="review")，B3）只对 review 里的任务开，任务状态不变、不成为当前 Run，回执只作证据。
调度预留（B2.2，库 v3 的 sched_claims）：调度器开单前先在这里落预留；同一任务同时最多一个未结预留，预留与候选复核、
并发名额检查在同一个 BEGIN IMMEDIATE 事务里（claim_task）。决策与恢复逻辑在 twin_scheduler.py。
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

DEFAULT_DB = "~/Library/Application Support/DigitalTwin/twin-state.db"
DEFAULT_DISPATCH = "~/.hermes/dispatch"
DEFAULT_OWNER_FILE = "~/Library/Application Support/DigitalTwin/owner_open_id"   # 常浩的飞书 open_id 只放本机受控配置，不进仓库
DEFAULT_OWNER_CHAT_FILE = "~/Library/Application Support/DigitalTwin/owner_chat_id"   # 常浩和 Hermes 私聊的 chat_id（B2.1 身份桥接判私聊用），同样 600
OWNER_HINT = ("还没配置常浩的 open_id：设环境变量 DT_OWNER_OPEN_ID，或把 open_id 单行写进 "
              f"{DEFAULT_OWNER_FILE}（chmod 600；路径可用 DT_OWNER_FILE 改）；配置前 --by user 一律拒绝")
SCHEMA_VERSION = 4

GOAL_STATUSES = ("observed", "authorized", "completed", "canceled")
TASK_STATUSES = ("observed", "ready", "running", "blocked", "review", "completed", "canceled")
TASK_TERMINAL = ("completed", "canceled")
RUN_STATUSES = ("new", "queued", "running", "done", "guard", "failed", "merged", "cleaned", "abandoned", "stale")
RUN_ACTIVE = ("new", "queued", "running")
RUN_RESULT = ("done", "guard", "failed", "stale", "abandoned")       # 这些状态算“一次执行的结果”，会反映到任务
RUN_SUCCESS = ("done", "merged", "cleaned")
RUN_FINAL = ("cleaned", "abandoned")                                 # 台账里不会再变的终态
RUN_PURPOSES = ("execute", "review")   # execute：执行任务的 Run；review：对 review 里的任务开的只读审查 Run（B3），只作证据
CLAIM_STATES = ("claimed", "ordered", "linked", "released", "void", "unsafe")
CLAIM_OPEN = ("claimed", "ordered", "unsafe")   # 未结预留：占着任务和并发名额；unsafe 要人工收口
CLAIM_TRANSITIONS = {
    ("claimed", "ordered"), ("claimed", "released"), ("claimed", "void"), ("claimed", "unsafe"),
    ("ordered", "linked"), ("ordered", "unsafe"),
    ("unsafe", "linked"), ("unsafe", "released"), ("unsafe", "void"),
}
# claimed 已预留未开单 → ordered 已开单（知道单号）→ linked Run 已关联（此后由“一任务一活动 Run”防重复）；
# released 开单前主动放弃；void 恢复时证明没有开出单、预留方也已不在；unsafe 归属证明不了，只有常浩能收口
SCHED_LEASE = 900          # 秒：预留的租约；过期不自动释放，由恢复按单号关联键与预留方进程核对后决定
RUN_RANK = {"new": 0, "queued": 1, "running": 2, "stale": 2, "done": 3, "guard": 3, "failed": 3,
            "merged": 4, "cleaned": 5, "abandoned": 5}
BLOCK_KINDS = ("decision", "dependency", "external", "technical")
ACTORS = ("user", "hermes", "claude", "codex", "system")
SOURCES = ("user", "discovery", "agent", "system")
OBJECT_TYPES = ("goal", "task", "run")
QUEUE_BUCKETS = ("ready", "waiting_deps", "blocked_decision", "blocked_external", "blocked_technical",
                 "cancel_pending", "review", "unsafe")   # B2.1 T05 调度消费查询的分桶
MAX_RETRIES = 2            # 临时保护：technical 类阻塞最多自动重试几次，超过就要常浩定；按故障原因分级的策略留到 B2
STALE_AFTER = 2 * 3600     # 秒：没有台账可查的 Run 超过这么久没有心跳就算过期；有台账的只提示 suspect，不单凭时间判定
SUMMARY_CLIP = 1000        # 回执里的车间总结最多存多少字
RECEIPT_REQUIRED = ("exit_code", "ended")  # 回执缺这些字段就不能判成功（run_worker 一定会写）
RECEIPT_KEEP = ("project", "tool", "mode", "cwd", "started", "ended", "exit_code", "timed_out", "worker_error",
                "session_id", "need_decision", "branch", "head_before", "head_after", "committed_files", "uncommitted",
                "guard_main_changed", "guard_remote_changed", "note_main_moved", "note_remote_moved", "summary",
                "executor", "access", "model", "chosen_by", "refused", "reason")   # 后六项：AI 接入留痕（P-13）

TASK_TRANSITIONS = {
    ("observed", "ready"), ("observed", "blocked"), ("observed", "canceled"),
    ("ready", "running"), ("ready", "blocked"), ("ready", "canceled"),
    ("running", "review"), ("running", "blocked"), ("running", "canceled"),
    ("blocked", "ready"), ("blocked", "running"), ("blocked", "canceled"),
    ("review", "completed"), ("review", "ready"), ("review", "running"), ("review", "blocked"), ("review", "canceled"),
}
# blocked → running、review → running 只走 resume_run（同一工单续跑），不开放给 start_run
RUN_TRANSITIONS = {
    ("new", "queued"), ("new", "running"), ("new", "abandoned"), ("queued", "running"), ("queued", "abandoned"),
    ("running", "done"), ("running", "guard"), ("running", "failed"), ("running", "stale"), ("running", "abandoned"),
    ("done", "merged"), ("done", "abandoned"), ("done", "running"), ("failed", "running"), ("failed", "abandoned"),
    ("merged", "cleaned"), ("guard", "abandoned"), ("guard", "running"),
    ("stale", "done"), ("stale", "guard"), ("stale", "failed"), ("stale", "abandoned"), ("stale", "running"),
}
# done / failed / stale / guard → running 是“续跑”（dispatch run --followup），只走 resume_run；guard 的续跑只有常浩能定

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
  current_run_id TEXT,
  verdict TEXT CHECK (verdict IS NULL OR verdict IN ('passed','failed')), verdict_by TEXT,
  verdict_basis TEXT NOT NULL DEFAULT '', verdict_cites TEXT NOT NULL DEFAULT '[]', verdict_at TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX tasks_goal ON tasks(goal_id);
CREATE TABLE runs (
  id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id), agent TEXT NOT NULL, order_no TEXT UNIQUE,
  status TEXT NOT NULL CHECK (status IN ('new','queued','running','done','guard','failed','merged','cleaned','abandoned','stale')),
  purpose TEXT NOT NULL DEFAULT 'execute' CHECK (purpose IN ('execute','review')),
  session_id TEXT, receipt TEXT NOT NULL DEFAULT '{}', evidence TEXT NOT NULL DEFAULT '{}', note TEXT NOT NULL DEFAULT '',
  cancel_requested_at TEXT, cancel_requested_by TEXT,
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
MIGRATIONS = [
    SCHEMA,   # v1 在 2026-10-29 返修时定稿（此前没有任何已部署的库）
    # v2（B2.1 T03）：飞书消息占用登记——常浩的一条消息只换一次 user 动作（防重放）；由 claim_message 在和状态变化同一个事务里写
    "CREATE TABLE identity_uses (message_id TEXT PRIMARY KEY, used_at TEXT NOT NULL, purpose TEXT NOT NULL DEFAULT '')",
    # v3（B2.2 S2）：调度预留。部分唯一索引保证同一任务同时最多一个未结预留（claimed / ordered / unsafe）
    "CREATE TABLE sched_claims (sched_key TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id), project TEXT,"
    " state TEXT NOT NULL CHECK (state IN ('claimed','ordered','linked','released','void','unsafe')),"
    " owner TEXT NOT NULL DEFAULT '{}', order_no TEXT, run_id TEXT, lease_until TEXT NOT NULL,"
    " note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
    "CREATE UNIQUE INDEX sched_claims_open ON sched_claims(task_id) WHERE state IN ('claimed','ordered','unsafe')",
    # v4（托管 #133）：托管账本。决定的当前状态在 custody_decisions，额度预占在 custody_quota；每次变化另在 events 记一条
    # custody.decision.*（object_type = custody_decision），和状态变化同一个事务。读写逻辑在 custody_ledger.py
    "CREATE TABLE custody_decisions (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, project TEXT NOT NULL,"
    " action TEXT NOT NULL CHECK (action IN ('reply','merge','suspend')), target TEXT NOT NULL DEFAULT '{}',"
    " issue_key TEXT, context TEXT NOT NULL DEFAULT '', question TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',"
    " ask_owner TEXT NOT NULL DEFAULT '',"
    " basis TEXT NOT NULL, cites TEXT NOT NULL DEFAULT '[]',"
    " revocability TEXT NOT NULL CHECK (revocability IN ('correctable','revertible','irrevocable')),"
    " status TEXT NOT NULL CHECK (status IN ('issued','executing','done','failed','unknown','unreconciled','void','suspended')),"
    " disposition TEXT NOT NULL DEFAULT 'open' CHECK (disposition IN ('open','overturned','corrected','reverted','revert_failed')),"
    " expires_at TEXT, void_reason TEXT NOT NULL DEFAULT '', outcome TEXT NOT NULL DEFAULT '{}',"
    " raw_path TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
    "CREATE INDEX custody_decisions_session ON custody_decisions(session_id);"
    "CREATE TABLE custody_quota (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,"
    " kind TEXT NOT NULL CHECK (kind IN ('reply','issue','merge')), scope TEXT NOT NULL DEFAULT '',"
    " decision_id TEXT NOT NULL REFERENCES custody_decisions(id),"
    " state TEXT NOT NULL CHECK (state IN ('reserved','used','released','frozen')),"
    " created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
    "CREATE INDEX custody_quota_count ON custody_quota(kind, session_id, scope, state)",
]   # 第 n 项把库从版本 n 升到 n+1；改表结构只追加，不改已有项


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


def _age(now, ts):
    """now 距 ts 多少秒；ts 没有时区（台账里的本机时间）就按 now 的时区算。"""
    last = _parse(ts)
    if last is None:
        return None
    if last.tzinfo is None and now.tzinfo is not None:
        last = last.replace(tzinfo=now.tzinfo)
    elif last.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=last.tzinfo)
    return (now - last).total_seconds()


def _local_config(env_name, file_env, default_file, what):
    """本机受控配置：先读环境变量，再读单行文件（取第一个非空行；文件权限不是 600 不认）。没配置返回 None。"""
    v = (os.environ.get(env_name) or "").strip()
    if v:
        return v
    f = Path(os.environ.get(file_env) or default_file).expanduser()
    try:
        mode = stat.S_IMODE(f.stat().st_mode)
        lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return None
    if mode & 0o077:
        raise NotAuthorized(f"{what}配置文件 {f} 的权限是 {mode:o}，必须是 600（chmod 600 该文件），否则不认")
    return lines[0] if lines else None


def owner_open_id():
    """常浩的飞书 open_id：环境变量 DT_OWNER_OPEN_ID，或 DT_OWNER_FILE / 默认文件。源码里不写死（F4）。"""
    return _local_config("DT_OWNER_OPEN_ID", "DT_OWNER_FILE", DEFAULT_OWNER_FILE, "身份")


def owner_chat_id():
    """常浩和 Hermes 私聊的 chat_id：环境变量 DT_OWNER_CHAT_ID，或 DT_OWNER_CHAT_FILE / 默认文件（twin_identity.py setup-chat 写）。"""
    return _local_config("DT_OWNER_CHAT_ID", "DT_OWNER_CHAT_FILE", DEFAULT_OWNER_CHAT_FILE, "私聊会话")


def _diff(old, new):
    """new 里和 old 不一样的字段（冲突留痕用：只记差异，不记整份）。"""
    return {k: v for k, v in (new or {}).items() if (old or {}).get(k) != v}


def clip_receipt(receipt):
    """回执只留白名单字段，车间总结截到 SUMMARY_CLIP 字；别的原始内容不进长期库。"""
    if not isinstance(receipt, dict):
        return {}
    r = {k: receipt[k] for k in RECEIPT_KEEP if k in receipt}
    if isinstance(r.get("summary"), str):
        r["summary"] = r["summary"][:SUMMARY_CLIP]
    return r


def receipt_evidence(receipt):
    keys = ("committed_files", "head_before", "head_after", "branch", "exit_code", "timed_out", "worker_error",
            "guard_main_changed", "guard_remote_changed", "need_decision", "ended")
    return {k: receipt[k] for k in keys if k in (receipt or {})}


def receipt_key(run_id, receipt):
    return f"run.receipt:{run_id}:{hashlib.sha1(_json(receipt).encode()).hexdigest()[:16]}"


def classify_receipt(receipt):
    """回执 → (Run 状态, 补充说明)。护栏优先；缺必要字段或无法判定的一律不算成功。"""
    receipt = receipt or {}
    if receipt.get("guard_main_changed") or receipt.get("guard_remote_changed"):
        return "guard", ""
    missing = [k for k in RECEIPT_REQUIRED if k not in receipt]
    if missing:
        return "failed", f"回执不完整：缺 {'、'.join(missing)}，不能当成功"
    code = receipt.get("exit_code")
    if not isinstance(code, int) or isinstance(code, bool):
        return "failed", f"回执的 exit_code 不是整数（{code!r}），不能当成功"
    if receipt.get("timed_out") or receipt.get("worker_error") or code != 0:
        return "failed", ""
    return "done", ""


class Store:
    """一个 Store 对应一个库文件；用 with 打开，所有公开写方法各自是一个事务。"""

    def __init__(self, path=None, clock=None, dispatch_dir=None, max_retries=MAX_RETRIES, readonly=False):
        self.path = Path(path or os.environ.get("DT_STATE_DB", DEFAULT_DB)).expanduser()
        self.readonly = readonly   # 只读打开（B2.2 调度决策用）：库不存在不建、不迁移、写操作被 SQLite 拒绝
        self.dispatch_dir = Path(dispatch_dir or os.environ.get("DT_DISPATCH", DEFAULT_DISPATCH)).expanduser()
        self.clock = clock or (lambda: dt.datetime.now().astimezone())
        self.max_retries = max_retries
        self.conn = None
        self._depth = 0

    # ---------- 打开、迁移、备份 ----------
    def open(self):
        if self.conn:
            return self
        if self.readonly:
            if not self.path.is_file():
                raise NotFound(f"状态库不存在：{self.path}（只读打开不建库）")
            self.conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA busy_timeout=5000")
            if self.version() > len(MIGRATIONS):
                v = self.version()
                self.close()
                raise StateError(f"库版本 {v} 比程序支持的 {len(MIGRATIONS)} 新，请升级脚本")
            return self
        created = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.conn = sqlite3.connect(str(self.path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        try:
            self._migrate()
        except BaseException:
            self.close()   # 版本比程序新等迁移失败时不留打开的连接（回退时旧程序会走到这里）
            raise
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
        """在线一致备份（SQLite backup API），默认到同目录 backups/；同一秒再备份自动加序号，不覆盖。"""
        if dest:
            dest = Path(dest)
        else:
            base = self.path.parent / "backups" / f"{self.path.stem}-{self.clock():%Y%m%d-%H%M%S}"
            dest, n = base.with_suffix(".db"), 1
            while dest.exists():
                n += 1
                dest = base.with_name(f"{base.name}-{n}.db")
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        out = sqlite3.connect(str(dest))
        try:
            self.conn.backup(out)
        finally:
            out.close()
        os.chmod(dest, 0o600)
        return dest

    def restore(self, src):
        """用备份文件整体替换当前库内容；先做完整性和版本检查。调用方负责先确认。"""
        src = Path(src)
        if not src.exists():
            raise NotFound(f"备份文件不存在：{src}")
        # immutable=1：备份文件没人写，不建 -shm / -wal；系统 Python 3.9 自带的 SQLite 只给 mode=ro 会打不开 WAL 库
        inp = sqlite3.connect(f"file:{src}?mode=ro&immutable=1", uri=True)
        try:
            try:
                check = inp.execute("PRAGMA integrity_check").fetchone()[0]
                has_meta = inp.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone()
                row = inp.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() if has_meta else None
            except sqlite3.DatabaseError as e:
                raise StateError(f"备份文件不是可用的状态库：{e}")
            if check != "ok":
                raise StateError(f"备份文件完整性检查没过：{check}")
            if not row:
                raise StateError("备份文件里没有 schema_version，不是本库的备份")
            if int(row[0]) > len(MIGRATIONS):
                raise StateError(f"备份的库版本 {row[0]} 比程序支持的 {len(MIGRATIONS)} 新")
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

    def _seen(self, idem_key):
        """幂等键已处理过就计一次重复并返回原事件，否则返回 None。"""
        row = self.conn.execute("SELECT id, applied, result, object_id FROM events WHERE idem_key=?", (idem_key,)).fetchone()
        if row:
            self.conn.execute("UPDATE events SET dup_count=dup_count+1, last_seen_at=? WHERE id=?", (self.now(), row["id"]))
        return row

    def ingest_event(self, source, kind, idem_key, actor="system", payload=None, object_type=None, object_id=None,
                     evidence="", handler=None):
        """三个事件入口共用：用户消息（source=user）、主动发现（discovery）、Agent 回执（agent）。
        同一个幂等键只处理一次：重复进入只给原事件加 dup_count，不再调用 handler。
        handler(store) 在同一事务里改状态；它抛 StateError 时事件记为 applied=0 并保留原因，状态不变。"""
        if not idem_key:
            raise StateError("事件必须带幂等键")
        with self._tx():
            row = self._seen(idem_key)
            if row:
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

    # ---------- 身份：飞书消息占用（B2.1 T03）----------
    def claim_message(self, message_id, purpose=""):
        """登记“常浩的这条飞书消息已经换过一次 user 动作”；同一条消息再来一律拒绝（防重放）。
        和状态变化放在同一个事务里：动作被拒绝时整体回滚，消息还能再用。"""
        with self._tx() as c:
            row = c.execute("SELECT used_at, purpose FROM identity_uses WHERE message_id=?", (message_id,)).fetchone()
            if row:
                raise NotAuthorized(f"这条飞书消息已经用过（{row['used_at']}，{row['purpose'] or '未注明'}）：一条消息只换一次动作，"
                                    "要再做请常浩再说一次")
            c.execute("INSERT INTO identity_uses (message_id, used_at, purpose) VALUES (?,?,?)", (message_id, self.now(), purpose))
            return {"message_id": message_id, "claimed": True}

    def release_message(self, message_id):
        """动作没成功时把占用退回去（命令行用：拒绝、出错都退）。返回退掉的条数。"""
        with self._tx() as c:
            return c.execute("DELETE FROM identity_uses WHERE message_id=?", (message_id,)).rowcount

    # ---------- 读取 ----------
    def _row(self, table, key, value):
        r = self.conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _task_dict(t):
        t["depends_on"] = _loads(t["depends_on"], [])
        t["verdict_cites"] = _loads(t["verdict_cites"], [])
        return t

    @staticmethod
    def _run_dict(r):
        r["receipt"], r["evidence"] = _loads(r["receipt"], {}), _loads(r["evidence"], {})
        return r

    def get_goal(self, goal_id):
        g = self._row("goals", "id", goal_id)
        if not g:
            raise NotFound(f"没有这个目标：{goal_id}")
        return g

    def get_task(self, task_id):
        t = self._row("tasks", "id", task_id)
        if not t:
            raise NotFound(f"没有这个任务：{task_id}")
        return self._task_dict(t)

    def get_run(self, run_id):
        r = self._row("runs", "id", run_id) or self._row("runs", "order_no", run_id)
        if not r:
            raise NotFound(f"没有这个 Run 或工单：{run_id}")
        return self._run_dict(r)

    def show(self, obj_id):
        for getter in (self.get_goal, self.get_task, self.get_run):
            try:
                return getter(obj_id)
            except NotFound:
                continue
        raise NotFound(f"没有这个对象：{obj_id}")

    def _select(self, table, where, args, order="rowid"):
        q = f"SELECT * FROM {table}" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {order}"
        return [dict(r) for r in self.conn.execute(q, args)]

    def list_goals(self, status=None):
        return self._select("goals", ["status=?"] if status else [], (status,) if status else ())

    def list_tasks(self, goal_id=None, status=None):
        where, args = [], []
        if goal_id:
            where.append("goal_id=?"), args.append(goal_id)
        if status:
            where.append("status=?"), args.append(status)
        return [self._task_dict(t) for t in self._select("tasks", where, args)]

    def list_runs(self, task_id=None, status=None, unlinked=False, cancel_pending=False):
        where, args = [], []
        if task_id:
            where.append("task_id=?"), args.append(task_id)
        if status:
            where.append("status=?"), args.append(status)
        if unlinked:
            where.append("task_id IS NULL")
        if cancel_pending:
            where.append("cancel_requested_at IS NOT NULL"), where.append(f"status IN ({','.join('?' * len(RUN_ACTIVE))})")
            args.extend(RUN_ACTIVE)
        return [self._run_dict(r) for r in self._select("runs", where, args)]

    def current_run(self, task_id):
        """任务当前有效的 Run：tasks.current_run_id 指向的那个——由 start_run 开出来、或 link_run 关联进来的进行中的 Run；
        后关联的历史 Run 只作证据，不改归属（不按插入顺序）。字段为空（任务只挂过已结束的 Run）时回退到
        该任务最近开始（started_at，没有就按 created_at）的 Run；一个 Run 都没有返回 None。"""
        t = self._row("tasks", "id", task_id)
        if t and t["current_run_id"]:
            r = self._row("runs", "id", t["current_run_id"])
            if r:
                return self._run_dict(r)
        runs = self.list_runs(task_id)
        if not runs:
            return None
        return max(reversed(runs), key=lambda r: r["started_at"] or r["created_at"] or "")

    def events(self, object_type=None, object_id=None, kind=None):
        where, args = [], []
        if object_type:
            where.append("object_type=?"), args.append(object_type)
        if object_id:
            where.append("object_id=?"), args.append(object_id)
        if kind:
            where.append("kind=?"), args.append(kind)
        out = self._select("events", where, args, order="id")
        for e in out:
            e["payload"] = _loads(e["payload"], {})
        return out

    def refs(self, object_type, object_id):
        return self._select("refs", ["object_type=?", "object_id=?"], (object_type, object_id), order="id")

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
        """任务状态转换：查表，非法就拒绝；成功记一条事件。离开 blocked 清阻塞字段，回到 ready / running 清验收结论。"""
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
        if to in ("ready", "running") and task.get("verdict"):
            fields.update(verdict=None, verdict_by=None, verdict_basis="", verdict_cites="[]", verdict_at=None)
        self._touch("tasks", task["id"], status=to, **fields)
        self._record(source, kind or f"task.{to}", actor, "task", task["id"],
                     {"from": task["status"], "to": to, "reason": reason,
                      **{k: v for k, v in fields.items() if k not in ("blocked_reason", "verdict_cites")}})
        task["status"] = to
        return True

    def _run_to(self, run, to, actor, source="agent", kind=None, **fields):
        if run["status"] == to:
            return False
        if (run["status"], to) not in RUN_TRANSITIONS:
            raise IllegalTransition(f"Run {run['id']} 不能从 {run['status']} 变成 {to}")
        if to not in RUN_ACTIVE and not run.get("ended_at") and to != "stale":
            fields["ended_at"] = self.now()
        if to == "running":
            fields.setdefault("heartbeat_at", self.now())
            fields.setdefault("ended_at", None)
        self._touch("runs", run["id"], status=to, **fields)
        self._record(source, kind or f"run.{to}", actor, "run", run["id"], {"from": run["status"], "to": to})
        run["status"] = to
        return True

    @staticmethod
    def _source(by):
        return "user" if by == "user" else "system"

    # ---------- Goal ----------
    def create_goal(self, title, by, description="", status="observed", scope="", project=None, phase="", done_when="",
                    idem_key=None):
        """新建目标。带幂等键重复进入时返回既有目标（事件 dup_count 加一），不抛库异常。"""
        if status not in ("observed", "authorized"):
            raise StateError("新建目标只能是 observed 或 authorized")
        if status == "authorized" and by != "user":
            raise NotAuthorized("只有常浩本人能授权目标；别人新建的目标先是 observed")
        with self._tx() as c:
            if idem_key:
                row = self._seen(idem_key)
                if row:
                    return self.get_goal(row["object_id"])
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
            open_tasks = [t["id"] for t in tasks if t["status"] not in TASK_TERMINAL]
            if open_tasks:
                raise IllegalTransition(f"目标 {goal_id} 还有没收尾的任务：{'、'.join(open_tasks)}")
            if not any(t["status"] == "completed" for t in tasks) and not self.refs("goal", goal_id):
                raise EvidenceMissing(f"目标 {goal_id} 没有任何完成的任务或证据，不能标完成")
            self._touch("goals", goal_id, status="completed")
            self._record("user", "goal.complete", by, "goal", goal_id, {"note": note})
            return {"applied": True, "status": "completed"}

    def cancel_goal(self, goal_id, by, reason=""):
        """撤销目标：未收尾的任务一起撤销；进行中的 Run 记“已请求取消”，等执行层真正停下来再结算。"""
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
            pending = []
            for t in self.list_tasks(goal_id):
                if t["status"] not in TASK_TERMINAL:
                    pending += self._cancel_task(t, by, f"目标已撤销：{reason}")
            return {"applied": True, "status": "canceled", "cancel_pending": pending}

    # ---------- Task ----------
    def create_task(self, goal_id, title, by, acceptance="", next_step="", depends_on=(), owner="hermes", idem_key=None):
        """新建任务。带幂等键重复进入时返回既有任务。依赖只能指向同一目标下的任务。"""
        with self._tx() as c:
            if idem_key:
                row = self._seen(idem_key)
                if row:
                    return self.get_task(row["object_id"])
            g = self.get_goal(goal_id)
            if g["status"] in ("completed", "canceled"):
                raise IllegalTransition(f"目标 {goal_id} 已 {g['status']}，不能再加任务")
            for dep in depends_on:
                d = self.get_task(dep)
                if d["goal_id"] != goal_id:
                    raise StateError(f"依赖 {dep} 属于目标 {d['goal_id']}，不能跨目标依赖")
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

    def _require_authorized(self, task, what):
        g = self.get_goal(task["goal_id"])
        if g["status"] != "authorized":
            raise NotAuthorized(f"目标 {g['id']} 当前是 {g['status']}，{what}")
        return g

    def set_ready(self, task_id, by, note=""):
        """observed → ready：目标必须已授权；依赖没完成就先 blocked(dependency)，依赖完成后 recover() 会放行。"""
        with self._tx():
            t = self.get_task(task_id)
            self._require_authorized(t, "未经常浩授权的任务不能进入 ready")
            if t["status"] == "ready":
                return {"applied": False, "status": "ready"}
            if t["status"] != "observed":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}：阻塞用 unblock / retry 解，review 用 rework 退回")
            unmet = self._unmet(t)
            if unmet:
                self._task_to(t, "blocked", by, self._source(by), reason=f"等依赖完成：{'、'.join(unmet)}",
                              blocked_kind="dependency")
                return {"applied": True, "status": "blocked", "unmet": unmet}
            self._task_to(t, "ready", by, self._source(by), reason=note)
            return {"applied": True, "status": "ready"}

    def block_task(self, task_id, by, kind, reason):
        """记阻塞。已阻塞的任务可以更新原因；改类型要过权限：非 user 只能把类型升级成 decision（更严），
        decision 类只有 user 能改成别的，任何人都不能把类型降级后再走技术重试。"""
        if kind not in BLOCK_KINDS or not reason:
            raise StateError("阻塞必须写明类型（decision / dependency / external / technical）和原因")
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] != "blocked":
                self._task_to(t, "blocked", by, self._source(by), reason=reason, blocked_kind=kind)
                return {"applied": True, "status": "blocked"}
            if t["blocked_kind"] == kind and t["blocked_reason"] == reason:
                return {"applied": False, "status": "blocked"}
            if t["blocked_kind"] != kind and by != "user":
                if t["blocked_kind"] == "decision":
                    raise NotAuthorized(f"任务 {task_id} 在等常浩的决定（{t['blocked_reason']}），{by} 不能改成 {kind} 类阻塞")
                if kind != "decision":
                    raise NotAuthorized(f"{by} 只能把阻塞升级成 decision，不能把 {t['blocked_kind']} 改成 {kind}")
            self._touch("tasks", task_id, blocked_kind=kind, blocked_reason=reason)
            self._record(self._source(by), "task.block.update", by, "task", task_id,
                         {"from": t["blocked_kind"], "kind": kind, "reason": reason})
            return {"applied": True, "status": "blocked"}

    def unblock_task(self, task_id, by, note=""):
        """解阻塞：decision 类只有常浩能解；dependency 类要依赖真的完成了（常浩可以强制）；
        technical 类非 user 解开算一次技术重试（受上限约束）；external 类非 user 解开必须写说明。"""
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] != "blocked":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，没有阻塞可解")
            kind = t["blocked_kind"]
            if by != "user":
                if kind == "decision":
                    raise NotAuthorized(f"任务 {task_id} 等的是常浩的决定（{t['blocked_reason']}），{by} 不能替他解开")
                if kind == "dependency" and self._unmet(t):
                    raise IllegalTransition(f"任务 {task_id} 的依赖还没完成：{'、'.join(self._unmet(t))}")
                if kind == "technical":
                    return self._retry(t, by)
                if kind == "external" and not note:
                    raise StateError(f"任务 {task_id} 是外部来源阻塞，{by} 解开必须说明来源已恢复的依据")
            self._require_authorized(t, "不能继续推进")
            self._task_to(t, "ready", by, self._source(by), kind="task.unblock", reason=note)
            return {"applied": True, "status": "ready"}

    def _retry(self, t, by):
        if t["blocked_kind"] != "technical":
            raise NotAuthorized(f"任务 {t['id']} 的阻塞是 {t['blocked_kind']} 类（{t['blocked_reason']}），不能用技术重试绕过")
        if t["retries"] >= self.max_retries:
            raise StateError(f"任务 {t['id']} 已重试 {t['retries']} 次，超过上限，要常浩定怎么办")
        self._require_authorized(t, "不能重试")
        self._task_to(t, "ready", by, "system", kind="task.retry", reason=f"第 {t['retries'] + 1} 次重试",
                      retries=t["retries"] + 1)
        return {"applied": True, "status": "ready", "retries": t["retries"] + 1}

    def retry_task(self, task_id, by):
        """技术类失败的自动重试：只对 technical 阻塞有效，最多 max_retries 次；decision / external / dependency 不能靠重试绕过。"""
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] != "blocked":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，不是失败待重试")
            return self._retry(t, by)

    def task_evidence(self, task_id):
        """任务的证据：外部引用（按 kind:ref 标识）和成功的 Run。只是材料，不是验收结论。"""
        return {"refs": [f"{r['kind']}:{r['ref']}" for r in self.refs("task", task_id)],
                "runs": [r["id"] for r in self.list_runs(task_id) if r["status"] in RUN_SUCCESS]}

    def accept_task(self, task_id, by, basis, cites, passed=True):
        """记验收结论（review 里）：结论必须说明依据并引用本任务已有的证据（Run id 或 kind:ref）；
        执行方不能给自己的 Run 下结论；passed=False 等于退回 ready 重做。B3 的 Codex 独立审查接在这里。"""
        if by not in ("user", "hermes", "claude", "codex"):
            raise NotAuthorized("验收结论只能由常浩、Hermes、Claude 或 Codex 给出")
        if not basis or not cites:
            raise EvidenceMissing("验收结论必须写依据，并引用至少一项证据（Run id 或 kind:ref）")
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] != "review":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，只有 review 里的任务能验收")
            ev = self.task_evidence(task_id)
            known = set(ev["refs"]) | set(ev["runs"])
            bad = [c for c in cites if c not in known]
            if bad:
                raise EvidenceMissing(f"引用的证据不在任务 {task_id} 上：{'、'.join(bad)}（已有：{'、'.join(sorted(known)) or '无'}）")
            cur = self.current_run(task_id)
            if by != "user" and cur and cur["agent"] == by:
                raise NotAuthorized(f"{by} 是任务 {task_id} 的执行方，不能给自己的结果下验收结论")
            if not passed:
                self._task_to(t, "ready", by, self._source(by), kind="task.rework", reason=f"验收不通过：{basis}")
                return {"applied": True, "status": "ready", "verdict": "failed"}
            self._touch("tasks", task_id, verdict="passed", verdict_by=by, verdict_basis=basis, verdict_cites=_json(list(cites)),
                        verdict_at=self.now())
            self._record(self._source(by), "task.verdict", by, "task", task_id, {"verdict": "passed", "basis": basis, "cites": list(cites)})
            return {"applied": True, "status": "review", "verdict": "passed"}

    def complete_task(self, task_id, by, note=""):
        """review → completed：要有通过的验收结论（accept_task），而且结论之后当前 Run 没有再变过。"""
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] == "completed":
                return {"applied": False, "status": "completed"}
            if t["status"] != "review":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，只有 review 里的任务能标完成")
            if t["verdict"] != "passed":
                raise EvidenceMissing(f"任务 {task_id} 还没有通过的验收结论（先 accept_task），有证据不等于验收通过")
            cur = self.current_run(task_id)
            if cur and cur["status"] in RUN_ACTIVE:
                raise IllegalTransition(f"任务 {task_id} 的当前 Run {cur['id']} 还在 {cur['status']}，不能标完成")
            self._task_to(t, "completed", by, self._source(by), reason=note, next_step="")
            return {"applied": True, "status": "completed", "verdict_by": t["verdict_by"], "cites": t["verdict_cites"]}

    def rework_task(self, task_id, by, note):
        """review → ready：验证没过，退回重做（清掉验收结论）。"""
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] != "review":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，不在 review")
            self._task_to(t, "ready", by, self._source(by), kind="task.rework", reason=note)
            return {"applied": True, "status": "ready"}

    def _cancel_task(self, t, by, reason):
        """撤销任务本体；进行中的 Run 不直接改成 abandoned（进程可能还在跑），记“已请求取消”，返回这些 Run 的 id。"""
        self._task_to(t, "canceled", by, self._source(by), reason=reason)
        pending = []
        for r in self.list_runs(t["id"]):
            if r["status"] in RUN_ACTIVE and not r["cancel_requested_at"]:
                self._touch("runs", r["id"], cancel_requested_at=self.now(), cancel_requested_by=by)
                self._record(self._source(by), "run.cancel_requested", by, "run", r["id"],
                             {"task": t["id"], "reason": reason, "note": "等执行层真正停下来后再结算"})
                pending.append(r["id"])
        return pending

    def cancel_task(self, task_id, by, reason=""):
        with self._tx():
            t = self.get_task(task_id)
            if t["status"] == "canceled":
                return {"applied": False, "status": "canceled"}
            if t["status"] != "observed" and by != "user":
                raise NotAuthorized(f"任务 {task_id} 已经授权推进（{t['status']}），只有常浩能撤销")
            pending = self._cancel_task(t, by, reason)
            return {"applied": True, "status": "canceled", "cancel_pending": pending}

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
    def start_run(self, task_id, agent, by, order_no=None, session_id=None, status="running", idem_key=None, note="",
                  purpose="execute"):
        """给 ready 的任务开一次执行（purpose=execute，任务随之 running，它成为当前 Run）；
        或给 review（待验收）的任务开一次只读审查（purpose=review，B3 的独立审查）：任务状态不变、不成为当前 Run、
        审查方不能是当前 Run 的执行方，其回执只作证据（run.review_result），结论要另走 accept_task。
        一个任务同时只能有一个进行中的 Run；带幂等键时同一键只开一次。"""
        if status not in RUN_ACTIVE:
            raise StateError(f"新 Run 的状态只能是 {'/'.join(RUN_ACTIVE)}")
        if purpose not in RUN_PURPOSES:
            raise StateError(f"Run 的用途只能是 {'/'.join(RUN_PURPOSES)}")
        with self._tx() as c:
            if idem_key:
                row = self._seen(idem_key)
                if row:
                    rid = _loads(row["result"], {}).get("run")
                    return {"applied": False, "duplicate_of": row["id"], "run": self.get_run(rid) if rid else None}
            t = self.get_task(task_id)
            active = [r["id"] for r in self.list_runs(task_id) if r["status"] in RUN_ACTIVE]
            if active:
                raise IllegalTransition(f"任务 {task_id} 已有进行中的 Run：{'、'.join(active)}，不重复派单")
            if purpose == "review":
                if t["status"] != "review":
                    raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，只有 review（待验收）的任务能开审查 Run")
                cur = self.current_run(task_id)
                if cur and cur["agent"] == agent:
                    raise NotAuthorized(f"{agent} 是任务 {task_id} 的执行方（{cur['id']}），不能由它审查自己的结果")
            elif t["status"] != "ready":
                raise IllegalTransition(f"任务 {task_id} 当前是 {t['status']}，只有 ready 的任务能开始执行")
            self._require_authorized(t, "不能执行")
            if order_no and c.execute("SELECT 1 FROM runs WHERE order_no=?", (order_no,)).fetchone():
                raise StateError(f"工单 {order_no} 已登记过，要关联请用 link_run")
            rid = self._new_id("runs", "R")
            now = self.now()
            c.execute("INSERT INTO runs (id, task_id, agent, order_no, status, purpose, session_id, note, started_at,"
                      " heartbeat_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (rid, task_id, agent, order_no, status, purpose, session_id, note,
                       now if status == "running" else None, now, now, now))
            if purpose == "execute":
                self._task_to(t, "running", by, self._source(by), reason=f"开跑 {rid}", current_run_id=rid)
            self._record("system", "run.start", by, "run", rid,
                         {"task": task_id, "agent": agent, "order_no": order_no, "purpose": purpose},
                         idem_key=idem_key, result=_json({"run": rid}))
            return {"applied": True, "run": self.get_run(rid)}

    def heartbeat(self, run_id):
        with self._tx():
            r = self.get_run(run_id)
            if r["status"] != "running":
                raise IllegalTransition(f"Run {r['id']} 不在 running（{r['status']}），没有心跳可记")
            self._touch("runs", r["id"], heartbeat_at=self.now())

    def _settle_task_after_run(self, run, by):
        """Run 到了结果状态，把结果反映到任务上：done → review；need_decision / guard / abandoned → blocked(decision)；
        failed / stale → blocked(technical)。只有任务的当前 Run 能改任务，而且任务得还在 running；
        否则只记事件（迟到结果 / 未应用），不动任务。"""
        if not run["task_id"] or run["status"] not in RUN_RESULT:
            return None
        if run.get("purpose") == "review":
            self._record("agent", "run.review_result", by, "run", run["id"],
                         {"task": run["task_id"], "status": run["status"],
                          "note": "审查 Run 的结果只作证据，不结算任务；结论另走 accept_task"})
            return "evidence"
        t = self.get_task(run["task_id"])
        cur = self.current_run(t["id"])
        rid = run["id"]
        if cur and cur["id"] != rid:
            self._record("agent", "run.late_result", by, "run", rid,
                         {"task": t["id"], "status": run["status"], "current_run": cur["id"], "task_status": t["status"],
                          "note": "旧 Run 的迟到结果，只记在它自己身上，不改任务"})
            return "late"
        if t["status"] != "running":
            self._record("agent", "run.result_not_applied", by, "run", rid,
                         {"task": t["id"], "status": run["status"], "task_status": t["status"],
                          "note": "任务已不在 running（撤销、人工处理或已结算），结果不回写"})
            return None
        receipt = run["receipt"]
        if receipt.get("need_decision"):
            self._task_to(t, "blocked", by, "agent", reason=f"{rid} 需要常浩定：{receipt['need_decision']}", blocked_kind="decision")
        elif run["status"] == "done":
            self._task_to(t, "review", by, "agent", reason=f"{rid} 跑完", next_step=f"核对 {rid} 的回执、测试与 PR，再记验收结论")
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
            r["receipt"], r["evidence"], r["note"] = merged, ev, note or r["note"]
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
        """Agent 回执入口：按回执内容判结果（护栏 → guard；缺必要字段、超时、出错、非零退出 → failed；否则 done）。
        回执不完整时失败原因写进 Run 的 note，任务会转技术阻塞而不是 review。"""
        receipt = clip_receipt(receipt)
        status, why = classify_receipt(receipt)
        return self.finish_run(run_id, status, by, receipt, note=why, idem_key=idem_key or receipt_key(run_id, receipt))

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

    def _resume_check(self, task, run, by, count_retry):
        """续跑前的任务侧检查，不写库。返回 (拒绝原因, 要一并改的任务字段)：原因是 StateError 子类的实例，None 表示可以续跑。
        规则：只有任务的当前 Run 能续跑；任务不能已 completed / canceled 或还是 observed；decision / external /
        dependency 类阻塞只有常浩能靠续跑解开；failed 的续跑算一次技术重试，受上限约束；目标必须仍是 authorized。"""
        cur = self.current_run(task["id"])
        if cur and cur["id"] != run["id"]:
            return IllegalTransition(f"Run {run['id']} 不是任务 {task['id']} 的当前 Run（当前是 {cur['id']}），不能续跑"), {}
        if task["status"] in TASK_TERMINAL or task["status"] == "observed":
            return IllegalTransition(f"任务 {task['id']} 已 {task['status']}，不能续跑"), {}
        fields = {}
        if task["status"] == "blocked":
            if task["blocked_kind"] != "technical" and by != "user":
                return NotAuthorized(f"任务 {task['id']} 在等常浩或外部条件（{task['blocked_kind']}：{task['blocked_reason']}），"
                                     f"{by} 不能靠续跑解开"), {}
            if task["blocked_kind"] == "technical" and count_retry:
                if task["retries"] >= self.max_retries:
                    return StateError(f"任务 {task['id']} 已重试 {task['retries']} 次，超过上限，要常浩定怎么办"), {}
                fields["retries"] = task["retries"] + 1
        g = self.get_goal(task["goal_id"])
        if g["status"] != "authorized":
            return NotAuthorized(f"目标 {g['id']} 当前是 {g['status']}，不能续跑"), {}
        return None, fields

    def _resume_task(self, run, by, count_retry, observed=False):
        """同一工单续跑后把任务拉回 running（检查见 _resume_check）。
        observed=False 是主动续跑：检查不过直接抛出（resume_run 在改 Run 之前已先查过一遍，这里不会再抛）。
        observed=True 是台账观察到的续跑：执行事实已经发生，检查不过只记 run.resume_conflict，任务不动。
        审查 Run（purpose=review）的续跑不碰任务。"""
        if not run["task_id"] or run.get("purpose") == "review":
            return None
        t = self.get_task(run["task_id"])
        err, fields = self._resume_check(t, run, by, count_retry)
        if err:
            if not observed:
                raise err
            cur = self.current_run(t["id"])
            self._record("system", "run.resume_conflict", by, "run", run["id"],
                         {"task": t["id"], "task_status": t["status"], "blocked_kind": t["blocked_kind"],
                          "reason": t["blocked_reason"], "current_run": cur["id"] if cur else None, "why": str(err),
                          "note": "台账里已经实际续跑：Run 照实记 running，任务不改，请人工核对"})
            return "conflict"
        if t["status"] == "running":
            return "running"
        self._task_to(t, "running", by, self._source(by), kind="task.resume", reason=f"{run['id']} 续跑", **fields)
        return "running"

    def resume_run(self, run_id, by, note=""):
        """主动续跑（dispatch run --followup，由 Hermes 登记）：done / failed / stale 的 Run 回到 running；guard 的续跑只有常浩能定。
        先做授权与任务状态检查（_resume_check），不合法直接拒绝：Run 不变、不留 running、不留 followup 事件。"""
        with self._tx():
            r = self.get_run(run_id)
            if r["status"] == "running":
                return {"applied": False, "status": "running"}
            if r["status"] == "guard" and by != "user":
                raise NotAuthorized(f"Run {r['id']} 触发过护栏，续跑要常浩定")
            if (r["status"], "running") not in RUN_TRANSITIONS:
                raise IllegalTransition(f"Run {r['id']} 当前是 {r['status']}，不能续跑")
            count_retry = r["status"] == "failed"
            if r["task_id"] and r.get("purpose") != "review":
                err, _ = self._resume_check(self.get_task(r["task_id"]), r, by, count_retry)
                if err:
                    raise err
            self._run_to(r, "running", by, "system", kind="run.followup", note=note or r["note"])
            return {"applied": True, "status": "running", "task_status": self._resume_task(r, by, count_retry)}

    def link_run(self, run_id, task_id, by):
        """把既有 Run（按 id 或工单号）关联到任务。进行中的 Run 只能挂到 ready 且目标已授权、没有别的活动 Run 的任务上
        （任务随之 running，它成为当前 Run）；已结束的 Run 只能挂到未收尾的任务上作证据，不改当前 Run 的归属；
        项目对不上一律拒绝。"""
        with self._tx():
            r = self.get_run(run_id)
            t = self.get_task(task_id)
            if r["task_id"] == task_id:
                return {"applied": False, "run": r["id"]}
            if r["task_id"]:
                raise StateError(f"Run {r['id']} 已关联任务 {r['task_id']}，不能再改")
            if t["status"] in TASK_TERMINAL:
                raise IllegalTransition(f"任务 {task_id} 已 {t['status']}，不能再关联 Run")
            g = self.get_goal(t["goal_id"])
            rp = r["evidence"].get("project")
            if rp and g.get("project") and rp != g["project"]:
                raise StateError(f"Run {r['id']} 属于项目 {rp}，任务 {task_id} 的目标属于 {g['project']}，不能关联")
            if r["status"] in RUN_ACTIVE:
                if t["status"] != "ready":
                    raise IllegalTransition(f"进行中的 Run 只能关联 ready 的任务，任务 {task_id} 当前是 {t['status']}")
                active = [x["id"] for x in self.list_runs(task_id) if x["status"] in RUN_ACTIVE]
                if active:
                    raise IllegalTransition(f"任务 {task_id} 已有进行中的 Run：{'、'.join(active)}")
                self._require_authorized(t, "不能关联进行中的 Run")
            self._touch("runs", r["id"], task_id=task_id)
            self._record("system", "run.link", by, "run", r["id"], {"task": task_id, "order_no": r["order_no"]})
            if r["status"] in RUN_ACTIVE:
                self._task_to(t, "running", by, self._source(by), reason=f"关联进行中的 {r['id']}", current_run_id=r["id"])
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
    def read_order(d):
        """只读读一张单的目录：返回 (order 或 None, 错误或 None)。两个都是 None 表示这不是工单目录（没有 order.json）。
        order 带 _receipt（out/receipt.json，坏的当空）、_dir，以及 _hash：order.json 与 receipt.json 原文的哈希，
        同步幂等键用——同一份内容只处理一次，同一秒连写两次也分得开（C2）。"""
        d = Path(d)
        try:
            raw = (d / "order.json").read_bytes()
            o = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return None, None
        except (OSError, ValueError) as e:
            return None, f"{d.name}：{type(e).__name__}"
        if not isinstance(o, dict) or not o.get("no"):
            return None, f"{d.name}：不是工单"
        rc = d / "out/receipt.json"
        rraw = b""
        try:
            rraw = rc.read_bytes() if rc.is_file() else b""
            o["_receipt"] = json.loads(rraw.decode("utf-8")) if rraw else {}
            if not isinstance(o["_receipt"], dict):
                o["_receipt"] = {}
        except (OSError, ValueError):
            o["_receipt"] = {}
        o["_dir"] = str(d)
        o["_hash"] = hashlib.sha1(raw + b"\0" + rraw).hexdigest()[:16]
        return o, None

    def read_orders(self, dispatch_dir=None):
        """只读扫描派单目录。返回 {"status": ok / missing / unreadable, "orders", "errors", "ignored"}：
        目录不存在、读不了、没有任何工单是三种不同情况，调用方据此决定能不能下判断；
        有 task.md 或 out/ 却没有 order.json 的 D* 目录不是工单，只记进 ignored（C1：对账时看得见，不导入、不报错）。"""
        root = Path(dispatch_dir or self.dispatch_dir).expanduser()
        out = {"status": "ok", "path": str(root), "orders": [], "errors": [], "ignored": []}
        if not root.is_dir():
            out["status"] = "missing"
            return out
        try:
            dirs = sorted(p for p in root.glob("D*") if p.is_dir())
        except OSError as e:
            out["status"], out["errors"] = "unreadable", [str(e)]
            return out
        for d in dirs:
            o, err = self.read_order(d)
            if o:
                out["orders"].append(o)
            elif err:
                out["errors"].append(err)
            else:
                out["ignored"].append(d.name)
        if not out["orders"] and out["errors"] and len(out["errors"]) == len(dirs) - len(out["ignored"]):
            out["status"] = "unreadable"
        return out

    def ledger_status(self, order_no, ledger):
        for o in ledger["orders"]:
            if o.get("no") == order_no:
                return o.get("status")
        return None

    @staticmethod
    def order_key(o):
        """台账同步的幂等键：单号 + 文件内容哈希（C2）。写入后通知和定时对账算出同一个键，互相去重。"""
        tail = o.get("_hash") or f"{o.get('updated') or o.get('created')}:{o.get('status')}"
        return f"order.sync:{o['no']}:{tail}"

    def _sync_one(self, o, by, stats):
        stats["seen"] += 1
        status = o.get("status")
        if status not in RUN_STATUSES:
            stats["skipped"].append(f"{o['no']}：状态 {status} 不认识")
            return None
        res = self.ingest_event("system", "order.sync", self.order_key(o), by,
                                {"no": o["no"], "status": status, "hash": o.get("_hash")}, "run", o["no"],
                                handler=lambda s, o=o, status=status: s._apply_order(o, status, by))
        if not res["applied"] or res["result"] == "unchanged":
            stats["unchanged"] += 1
        elif res["result"] == "created":
            stats["created"] += 1
        elif res["result"] == "conflict":
            stats["conflicts"].append(o["no"])
        elif res["result"] == "resume_conflict":
            stats["conflicts"].append(o["no"])   # Run 改了（续跑事实），任务没动：两边都算
            stats["updated"] += 1
        else:
            stats["updated"] += 1
        return res

    @staticmethod
    def _stats(ledger_status, errors=(), ignored=0):
        return {"ledger": ledger_status, "seen": 0, "created": 0, "updated": 0, "unchanged": 0, "conflicts": [],
                "skipped": list(errors), "ignored": ignored}

    def sync_orders(self, dispatch_dir=None, by="system"):
        """把派单台账同步成 Run：新工单登记为未关联的 Run（不建目标、不建任务）；状态变了才更新；
        已关联任务的 Run 到结果状态时按同一套规则反映到任务。同一份 order.json + receipt.json（同一个内容哈希）只处理一次。
        台账是执行层事实来源，但状态回退、护栏降级、终态再变一律拦下来记 run.conflict（先判冲突再合并，冲突时不碰
        回执与证据）；台账里实际发生的续跑照实把 Run 记成 running，任务侧不合规则时记 run.resume_conflict，
        两种冲突都进返回值的 conflicts。没有 order.json 的目录只计 ignored。"""
        ledger = self.read_orders(dispatch_dir)
        stats = self._stats(ledger["status"], ledger["errors"], len(ledger["ignored"]))
        for o in ledger["orders"]:
            self._sync_one(o, by, stats)
        return stats

    def trusted_order_dir(self, order_dir):
        """P1-3（ChatGPT 评审 2026-10-29）：只认派单根目录（self.dispatch_dir）的直接子目录——实路径比对、目录本身不能是符号链接。
        信封里 source="dispatch" 的字符串不算来源证明，路径边界才算。不合规抛 NotAuthorized。返回实路径。"""
        d = Path(order_dir).expanduser()
        if d.is_symlink():
            raise NotAuthorized(f"工单目录是符号链接，不同步：{d.name}")
        root = Path(self.dispatch_dir).expanduser().resolve()
        real = d.resolve()
        if real.parent != root:
            raise NotAuthorized(f"工单目录不在派单根目录之内（必须是 {root.name}/ 下的直接子目录），不同步")
        return real

    def sync_order(self, order_dir, by="system"):
        """只同步一张单（B2.1 T04：dispatch.py 写入台账后的通知走这里）。和 sync_orders 用同一个幂等键，两条路互相去重。
        order_dir 必须通过 trusted_order_dir，且目录名等于 order.json 的 no（P1-3）。"""
        real = self.trusted_order_dir(order_dir)
        o, err = self.read_order(real)
        if o and o["no"] != real.name:
            raise NotAuthorized(f"工单目录名 {real.name} 与 order.json 的单号不一致，不同步")
        stats = self._stats("ok" if o else "missing", [err] if err else [], 0 if (o or err) else 1)
        if o:
            self._sync_one(o, by, stats)
        return stats

    def reconcile(self, dispatch_dir=None, by="system"):
        """定时对账（写入后通知的兜底，B2.1 T04）：整个台账同步一遍，这次才补上的单（新建或更新）列进 missed——
        通知开着时它们就是漏掉的通知；另带没有 order.json 的目录名和冲突单，给人看。"""
        ledger = self.read_orders(dispatch_dir)
        stats = self._stats(ledger["status"], ledger["errors"], len(ledger["ignored"]))
        stats["ignored_dirs"] = list(ledger["ignored"])
        stats["missed"] = []
        for o in ledger["orders"]:
            before = (stats["created"], stats["updated"])
            self._sync_one(o, by, stats)
            if (stats["created"], stats["updated"]) != before:
                stats["missed"].append(o["no"])
        return stats

    def _resync_kind(self, src, dst):
        """台账状态和本地不一致时怎么处理：normal（查表合法）/ followup（续跑）/ skip（跳过中间状态）/ undo_stale /
        conflict（回退、护栏降级、终态再变）。"""
        if (src, dst) in RUN_TRANSITIONS:
            if dst == "running" and src in ("done", "failed", "guard"):
                return "followup"
            if src == "stale" and dst == "running":
                return "undo_stale"
            return "normal"
        if src in RUN_FINAL or src == "guard":
            return "conflict"
        if dst in RUN_ACTIVE:
            return "conflict"
        return "skip" if RUN_RANK[dst] > RUN_RANK[src] else "conflict"

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
                " ended_at, heartbeat_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, None, o.get("tool") or "unknown", o["no"], status, receipt.get("session_id"), _json(receipt),
                 _json(evidence), "来自派单台账", receipt.get("started") or o.get("created"),
                 receipt.get("ended") if status not in RUN_ACTIVE else None, now, now, now))
            self._record("system", "run.import", by, "run", rid, {"order_no": o["no"], "status": status})
            return "created"
        r = self.get_run(row["id"])
        # 先判状态冲突，再决定写什么：冲突的台账内容只进事件留痕，已确认的回执、证据、会话一个字段都不动
        how = None if r["status"] == status else self._resync_kind(r["status"], status)
        why = "台账状态回退、护栏降级或终态再变，没有应用，请人工核对"
        if how == "followup" and r["status"] == "guard":
            how, why = "conflict", "护栏后的续跑要常浩定，没有应用"
        if how == "conflict":
            self._record("system", "run.conflict", by, "run", r["id"],
                         {"local": r["status"], "ledger": status, "order_no": o["no"], "note": why,
                          "ledger_receipt": _diff(r["receipt"], receipt), "ledger_evidence": _diff(r["evidence"], evidence)})
            return "conflict"
        merged = {**r["receipt"], **receipt}
        ev = {**r["evidence"], **evidence}
        changed = merged != r["receipt"] or ev != r["evidence"]
        if changed:
            self._touch("runs", r["id"], receipt=_json(merged), evidence=_json(ev),
                        session_id=merged.get("session_id") or r["session_id"])
        r["receipt"], r["evidence"] = merged, ev
        if how is None:
            if status == "running":
                self._touch("runs", r["id"], heartbeat_at=self.now())  # 台账文件还在变，就当有心跳
            return "updated" if changed else "unchanged"   # 只是重写了文件（内容哈希变了）但没新东西：算 unchanged
        if how in ("followup", "undo_stale"):
            was = r["status"]
            self._run_to(r, "running", by, "system", kind="run.followup" if how == "followup" else "run.resync")
            if self._resume_task(r, by, count_retry=(was == "failed"), observed=True) == "conflict":
                return "resume_conflict"   # Run 已照实记 running；任务侧没动，由 sync_orders 计入 conflicts
            return "updated"
        if how == "skip":
            self._touch("runs", r["id"], status=status, ended_at=r["ended_at"] or (None if status in RUN_ACTIVE else self.now()))
            self._record("system", "run.resync", by, "run", r["id"], {"from": r["status"], "to": status, "note": "台账跳过了中间状态"})
            r["status"] = status
        else:
            self._run_to(r, status, by, "system")
        if status in RUN_RESULT:
            self._settle_task_after_run(r, by)
        return "updated"

    # ---------- 调度预留（B2.2 S2：决策与恢复逻辑在 twin_scheduler.py）----------
    def _has_claims(self):
        """只读打开的 v2 库还没有预留表：当作没有任何预留。"""
        return bool(self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sched_claims'").fetchone())

    @staticmethod
    def _claim_dict(r):
        d = dict(r)
        d["owner"] = _loads(d["owner"], {})
        return d

    def get_claim(self, sched_key):
        r = self.conn.execute("SELECT * FROM sched_claims WHERE sched_key=?", (sched_key,)).fetchone() \
            if self._has_claims() else None
        if not r:
            raise NotFound(f"没有这个调度预留：{sched_key}")
        return self._claim_dict(r)

    def claims(self, task_id=None, states=None):
        if not self._has_claims():
            return []
        where, args = [], []
        if task_id:
            where.append("task_id=?"), args.append(task_id)
        if states:
            where.append(f"state IN ({','.join('?' * len(states))})"), args.extend(states)
        sql = "SELECT * FROM sched_claims" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY rowid"
        return [self._claim_dict(r) for r in self.conn.execute(sql, args)]

    def claim_task(self, task_id, sched_key, owner, check, by="system", lease=SCHED_LEASE):
        """调度预留：在一个 BEGIN IMMEDIATE 事务里先跑 check(store)（twin_scheduler 的候选复核 + 并发名额），
        结论是 dispatch 才插入预留。两个调度器同时来：写锁让它们排队，后来的复核时已能看到先来的预留；
        万一绕过复核，部分唯一索引 sched_claims_open 还会拒绝同一任务的第二个未结预留。
        返回 {"applied": True, "claim"}，或 {"applied": False, "reason", "evidence"}——拒绝不写任何东西。"""
        with self._tx() as c:
            verdict = check(self)
            if verdict.get("decision") != "dispatch" or verdict.get("task_id") != task_id:
                return {"applied": False, "reason": verdict.get("reason", "not_eligible"), "evidence": verdict.get("evidence", {})}
            now = self.now()
            until = (self.clock() + dt.timedelta(seconds=lease)).isoformat(timespec="seconds")
            project = (verdict.get("evidence") or {}).get("project")
            try:
                c.execute("INSERT INTO sched_claims (sched_key, task_id, project, state, owner, lease_until, created_at, updated_at)"
                          " VALUES (?,?,?,?,?,?,?,?)", (sched_key, task_id, project, "claimed", _json(owner), until, now, now))
            except sqlite3.IntegrityError as e:
                return {"applied": False, "reason": "claim_open", "evidence": {"error": str(e)}}
            self._record("system", "sched.claim", by, "task", task_id,
                         {"sched_key": sched_key, "project": project, "lease_until": until, "owner": owner})
            return {"applied": True, "claim": self.get_claim(sched_key)}

    def _claim_to(self, claim, to, by, note="", **fields):
        if claim["state"] == to:
            return False
        if (claim["state"], to) not in CLAIM_TRANSITIONS:
            raise IllegalTransition(f"调度预留 {claim['sched_key']} 不能从 {claim['state']} 变成 {to}")
        if claim["state"] == "unsafe" and by != "user":
            raise NotAuthorized(f"调度预留 {claim['sched_key']} 归属证明不了（unsafe），只有常浩能收口")
        sets = {"state": to, "updated_at": self.now(), **fields}
        if note:
            sets["note"] = note
        self.conn.execute(f"UPDATE sched_claims SET {', '.join(f'{k}=?' for k in sets)} WHERE sched_key=?",
                          (*sets.values(), claim["sched_key"]))
        self._record(self._source(by), f"sched.{to}", by, "task", claim["task_id"],
                     {"sched_key": claim["sched_key"], "from": claim["state"], "to": to, "note": note,
                      **fields})
        claim.update(sets)
        return True

    def claim_mark_spawning(self, sched_key):
        """开单子进程拉起之前先落这个持久标记（B2.2 返修 F1）：标记存在而子进程没登记，就不能证明没开单，恢复时不许 void。"""
        with self._tx():
            cl = self.get_claim(sched_key)
            if cl["state"] != "claimed":
                raise IllegalTransition(f"调度预留 {sched_key} 当前是 {cl['state']}，不能再开单")
            owner = {**cl["owner"], "spawn": {"at": self.now()}}
            self.conn.execute("UPDATE sched_claims SET owner=?, updated_at=? WHERE sched_key=?", (_json(owner), self.now(), sched_key))
            return owner

    def claim_set_child(self, sched_key, child):
        """记下预留方拉起的开单子进程身份（twin_scheduler 执行器 Popen 后立即调用）；恢复时预留方和子进程都确认退出才算没人在开单。"""
        with self._tx():
            cl = self.get_claim(sched_key)
            if cl["state"] != "claimed":
                raise IllegalTransition(f"调度预留 {sched_key} 当前是 {cl['state']}，不再记开单进程")
            owner = {**cl["owner"], "child": child}
            self.conn.execute("UPDATE sched_claims SET owner=?, updated_at=? WHERE sched_key=?", (_json(owner), self.now(), sched_key))
            return owner

    def claim_ordered(self, sched_key, order_no, by="system"):
        """claimed → ordered：开单成功、知道单号了。同一单号重复调用不变；已记了别的单号 → unsafe（一个预留两张单）。"""
        with self._tx():
            cl = self.get_claim(sched_key)
            if cl["order_no"] and cl["order_no"] != order_no:
                self._claim_to(cl, "unsafe", by, f"同一预留出现两张单：{cl['order_no']}、{order_no}")
                return {"applied": False, "claim": cl}
            if cl["state"] == "ordered":
                return {"applied": False, "claim": cl}
            self._claim_to(cl, "ordered", by, order_no=order_no)
            return {"applied": True, "claim": cl}

    def claim_link(self, sched_key, agent, by="system", status="new"):
        """ordered → linked：把预留开出的单登记成任务的当前 Run。台账写入后通知已把这张单导入成未关联的 Run 就 link_run，
        否则 start_run(order_no=…)（幂等键 sched.link:<预留>）。任务在开单期间变了（撤销、被别的 Run 占了等）：
        单已经存在，归属说不清——预留转 unsafe 留给常浩，不重派。"""
        with self._tx() as c:
            cl = self.get_claim(sched_key)
            if cl["state"] == "linked":
                return {"applied": False, "claim": cl}
            if cl["state"] != "ordered" or not cl["order_no"]:
                raise IllegalTransition(f"调度预留 {sched_key} 当前是 {cl['state']}，还没有单号，不能关联")
            try:
                row = c.execute("SELECT id, task_id FROM runs WHERE order_no=?", (cl["order_no"],)).fetchone()
                if row:
                    self.link_run(row["id"], cl["task_id"], by)
                    rid = row["id"]
                else:
                    rid = self.start_run(cl["task_id"], agent, by, order_no=cl["order_no"], status=status,
                                         idem_key=f"sched.link:{sched_key}", note=f"调度预留 {sched_key}")["run"]["id"]
            except StateError as e:
                self._claim_to(cl, "unsafe", by, f"单 {cl['order_no']} 已开出，但关联任务被拒：{e}")
                return {"applied": False, "claim": cl, "error": str(e)}
            self._claim_to(cl, "linked", by, run_id=rid)
            return {"applied": True, "claim": cl, "run": rid}

    def claim_close(self, sched_key, to, by="system", note=""):
        """收口一个未结预留：released（开单前主动放弃）/ void（恢复证明没开出单）/ unsafe（归属证明不了）。
        unsafe 的预留只有常浩能再收口。"""
        if to not in ("released", "void", "unsafe"):
            raise StateError("预留只能收口成 released / void / unsafe")
        with self._tx():
            cl = self.get_claim(sched_key)
            changed = self._claim_to(cl, to, by, note)
            return {"applied": changed, "claim": cl}

    # ---------- 调度消费查询（B2.1 T05：B2.2 的持久调度层读这里，本批不执行调度）----------
    def queue(self, dispatch_dir=None, ledger=None):
        """只读：按“下一步能不能自动推进”把对象分桶，每项带 id / title / since 和原因，不改任何状态。
        ready              已授权、依赖都完成、没有进行中 Run 的 ready 任务——可以派
        waiting_deps       ready 或 blocked(dependency)，但依赖没完成
        blocked_decision   等常浩决定（只有经核实的 user 事件能解）
        blocked_external   等外部条件
        blocked_technical  技术失败：retryable（重试没到上限）/ needs_human（到上限，要常浩定）
        cancel_pending     已请求取消、执行层还没停的 Run（B2.2 去停）
        review             待验收的任务（带有没有结论、当前 Run 有没有回执、引用数）
        unsafe             不能自动推进：目标不再 authorized、ready 却有活动 Run、running 却没活动 Run、
                           当前 Run 在台账里查不到、待验收却没回执、外部引用最近一次探针不可达、台账冲突没处理
        ledger 可传入已读好的台账（read_orders 的返回），调度决策同一轮只读一次台账、前后一致。"""
        ledger = ledger or self.read_orders(dispatch_dir)
        out = {k: [] for k in QUEUE_BUCKETS}
        out["ledger"] = ledger["status"]
        goals = {g["id"]: g for g in self.list_goals()}

        def item(t, **extra):
            return {"id": t["id"], "goal_id": t["goal_id"], "title": t["title"], "status": t["status"],
                    "since": t["updated_at"], **extra}

        def unsafe(obj, why, **extra):
            out["unsafe"].append({"id": obj["id"], "title": obj.get("title"), "why": why, "since": obj.get("updated_at"), **extra})

        for t in self.list_tasks():
            if t["status"] in TASK_TERMINAL or t["status"] == "observed":
                continue
            g = goals.get(t["goal_id"]) or {}
            if g.get("status") != "authorized":
                unsafe(t, f"目标 {t['goal_id']} 当前是 {g.get('status')}，不再推进")
                continue
            unmet = self._unmet(t)
            active = [r["id"] for r in self.list_runs(t["id"]) if r["status"] in RUN_ACTIVE]
            if t["status"] == "ready":
                if unmet:
                    out["waiting_deps"].append(item(t, depends_on=unmet))
                elif active:
                    unsafe(t, f"ready 却有进行中的 Run：{'、'.join(active)}", runs=active)
                else:
                    out["ready"].append(item(t, next_step=t["next_step"], acceptance=t["acceptance"], retries=t["retries"]))
            elif t["status"] == "blocked":
                kind = t["blocked_kind"]
                entry = item(t, kind=kind, reason=t["blocked_reason"])
                if kind == "dependency":
                    out["waiting_deps"].append(item(t, depends_on=unmet, reason=t["blocked_reason"]))
                elif kind == "decision":
                    out["blocked_decision"].append(entry)
                elif kind == "external":
                    out["blocked_external"].append(entry)
                else:
                    stuck = t["retries"] >= self.max_retries
                    out["blocked_technical"].append({**entry, "retries": t["retries"], "retryable": not stuck, "needs_human": stuck})
            elif t["status"] == "review":
                cur = self.current_run(t["id"])
                out["review"].append(item(t, verdict=t["verdict"], run=cur["id"] if cur else None,
                                          has_receipt=bool(cur and cur["receipt"]), refs=len(self.refs("task", t["id"]))))
                if not cur or not cur["receipt"]:
                    unsafe(t, "待验收却没有回执可核", run=cur["id"] if cur else None)
            elif t["status"] == "running":
                cur = self.current_run(t["id"])
                if not cur or cur["status"] not in RUN_ACTIVE:
                    unsafe(t, "running 却没有进行中的 Run（等 recover 处理）", run=cur["id"] if cur else None)
                elif cur["order_no"] and ledger["status"] != "ok":
                    unsafe(t, f"台账 {ledger['status']}，当前 Run {cur['id']} 的状态查不到", run=cur["id"])
                elif cur["order_no"] and self.ledger_status(cur["order_no"], ledger) is None:
                    unsafe(t, f"当前 Run {cur['id']} 的工单 {cur['order_no']} 在台账里找不到", run=cur["id"])
            refs = self.events(object_type="task", object_id=t["id"])
            last_ref = next((e for e in reversed(refs) if e["kind"].startswith("ref.")), None)
            if last_ref and last_ref["kind"] == "ref.unreachable":
                unsafe(t, f"外部引用最近一次探针不可达：{last_ref['payload']}", since=last_ref["at"])
        for r in self.list_runs(cancel_pending=True):
            out["cancel_pending"].append({"id": r["id"], "task_id": r["task_id"], "order_no": r["order_no"], "status": r["status"],
                                          "since": r["cancel_requested_at"], "by": r["cancel_requested_by"]})
        for r in self.list_runs():
            evs = self.events(object_type="run", object_id=r["id"])
            last = max((e["id"] for e in evs if e["kind"] in ("run.conflict", "run.resume_conflict")), default=None)
            if last is None:
                continue
            if not [e for e in evs if e["id"] > last and e["applied"] and e["kind"] not in ("run.conflict", "run.resume_conflict")]:
                e = next(x for x in evs if x["id"] == last)
                out["unsafe"].append({"id": r["id"], "task_id": r["task_id"], "order_no": r["order_no"],
                                      "why": f"台账冲突没有处理：{e['kind']}", "since": e["at"], "conflict": e["payload"]})
        return out

    # ---------- 恢复 ----------
    def recover(self, now=None, stale_after=STALE_AFTER, probe=None, dispatch_dir=None, by="system"):
        """重启或定时巡检时调用。
        - running 的 Run：先问探针（probe(run) 返回台账状态；没给探针就直接读派单台账）：台账说还在跑就保留，
          台账给了结果就按同步规则结算；台账读不到（目录缺失 / 读不了 / 没这张单）时不下判断，记 unknown。
          只有没有工单号、也查不到台账的 Run 才按时间判过期：超过 stale_after 秒没心跳标 stale，任务转 blocked(technical)。
          有台账但超过 stale_after 秒没变化的只列进 suspect，留给 B2 用进程存活判断。
        - running 却没有进行中 Run 的任务：blocked(technical)。
        - blocked(dependency) 的任务：依赖都完成了就放行到 ready（条件恢复）。
        每一步都是独立事务，做到哪一步记到哪一步。"""
        now = now or self.clock()
        ledger = self.read_orders(dispatch_dir) if probe is None else None
        out = {"ledger": ledger["status"] if ledger else "probe", "stale": [], "settled": [], "suspect": [],
               "unknown": [], "orphaned": [], "released": [], "kept": []}
        for r in self.list_runs(status="running"):
            seen, known = None, False
            if probe:
                try:
                    seen = probe(r)
                    known = seen is not None
                except Exception:  # noqa: BLE001
                    seen, known = None, False
            elif r["order_no"]:
                if ledger["status"] == "ok":
                    seen = self.ledger_status(r["order_no"], ledger)
                    known = seen is not None
            age = _age(now, r["heartbeat_at"] or r["created_at"])
            if known and seen == "running":
                (out["suspect"] if age is not None and age > stale_after else out["kept"]).append(r["id"])
                continue
            if known and seen in RUN_STATUSES:
                with self._tx():
                    r = self.get_run(r["id"])
                    how = self._resync_kind(r["status"], seen)
                    if how in ("normal", "skip"):
                        if how == "normal":
                            self._run_to(r, seen, by, "system")
                        else:
                            self._touch("runs", r["id"], status=seen, ended_at=self.now())
                            self._record("system", "run.resync", by, "run", r["id"], {"from": "running", "to": seen})
                            r["status"] = seen
                        self._settle_task_after_run(r, by)
                        out["settled"].append(f"{r['id']}→{seen}")
                        continue
                    self._record("system", "run.conflict", by, "run", r["id"], {"local": "running", "ledger": seen})
                    out["unknown"].append(r["id"])
                    continue
            if r["order_no"] and not known:
                out["unknown"].append(r["id"])  # 台账不可用或没这张单：不下判断
                continue
            if age is not None and age <= stale_after:
                out["kept"].append(r["id"])
                continue
            with self._tx():
                r = self.get_run(r["id"])
                note = f"恢复时发现 {int(age) if age is not None else '?'} 秒没有心跳，也没有台账可查"
                self._touch("runs", r["id"], note=note)
                r["note"] = note
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


def notify_order(order_dir, db=None, by="system", dispatch_dir=None):
    """B2.1 T04：派单台账写入后的通知入口（dispatch.py 的 save() 调）。状态库文件不存在就跳过、绝不建库
    （正式库只由 init 建）；存在才同步这一张单。dispatch_dir 是调用方自己的派单根目录（dispatch.py 传 ROOT），
    order_dir 必须在它之内（P1-3）。返回和 sync_orders 同形状的统计。"""
    path = Path(db or os.environ.get("DT_STATE_DB", DEFAULT_DB)).expanduser()
    if not path.exists():
        out = Store._stats("skipped", [f"状态库 {path} 不存在，没有同步（不自动建库）"])
        out["db"] = str(path)
        return out
    with Store(path, dispatch_dir=dispatch_dir) as s:
        out = s.sync_order(order_dir, by)
    out["db"] = str(path)
    return out


# ---------- 命令行 ----------
class _Done(BaseException):
    """命令行结果（P1-1）：done() 抛它、不直接退出，run_cli 据 ok 决定提交还是回滚，main() 顶层打印并退出。
    继承 BaseException：中间任何 except Exception 都接不到它。"""

    def __init__(self, ok, msg, kw):
        super().__init__(msg)
        self.ok, self.msg, self.kw = ok, msg, kw


def done(ok, msg, **kw):
    raise _Done(ok, msg, kw)


def _exit(d):
    print(json.dumps({"ok": d.ok, "msg": d.msg, **d.kw}, ensure_ascii=False, default=str))
    sys.exit(0 if d.ok else 1)


def _by(a):
    """--by 的程序校验：角色必须合法。--by user 的身份核实在 _user_turn（进命令之前做），这里不再认 --sender：
    角色名和命令行参数都不是身份，身份只能从飞书回读来（B2.1 T03）。"""
    if a.by not in ACTORS:
        done(False, f"--by 只能是 {'/'.join(ACTORS)}")
    return a.by


def _user_turn(a):
    """--by user 的准入（B2.1 T03）：必须给 --message-id（Hermes 会话里可省略，读 HERMES_SESSION_MESSAGE_ID——只是指针），
    用猛鬼兽 bot 身份回读飞书核实这条消息是常浩本人最近在私聊里发的。这里只核实、不碰库；占用登记（一条消息只换一次动作）
    由 run_cli 和业务动作放进同一个事务（P1-1）。任何一步不过都拒绝；成功返回 message_id。"""
    if getattr(a, "by", None) != "user":
        return None
    mid = getattr(a, "message_id", None) or os.environ.get("HERMES_SESSION_MESSAGE_ID")
    if not mid:
        done(False, "--by user 必须带 --message-id <常浩这条指令的飞书消息 id>（Hermes 会话里可省略，读 HERMES_SESSION_MESSAGE_ID）："
                    "状态库会用猛鬼兽 bot 身份回读飞书核实是常浩本人在私聊里说的，角色名不是身份", error="NotAuthorized")
    try:
        owner, chat = owner_open_id(), owner_chat_id()
    except StateError as e:
        done(False, str(e), error="NotAuthorized")
    if not owner:
        done(False, OWNER_HINT, error="NotAuthorized")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import twin_identity   # 同目录
    v = twin_identity.verify_message(mid, owner, owner_chat_id=chat)
    if not v["ok"]:
        done(False, f"身份核实不通过：{v['reason']}", error="NotAuthorized", message_id=mid)
    return mid


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        done(False, f"读不了 JSON 文件 {path}：{e}")


def _type_of(obj_id):
    return {"G": "goal", "T": "task"}.get(obj_id[:1].upper(), "run")


BY = [("--by", dict(required=True)),
      ("--message-id", dict(help="--by user 时必填：常浩这条指令的飞书消息 id（Hermes 会话里可省略，读 HERMES_SESSION_MESSAGE_ID）；"
                                 "状态库回读飞书核实，一条消息只换一次动作"))]
COMMANDS = {
    "init": [], "backup": [("--to",)], "restore": [("--from", dict(dest="src", required=True)), ("--yes", dict(action="store_true"))],
    "recover": [("--stale-after", dict(type=int, default=STALE_AFTER)), ("--dispatch",), ("--no-sync", dict(action="store_true"))],
    "sync-orders": [("--dispatch",)],
    "reconcile": [("--dispatch",)],
    "queue": [("--dispatch",), ("--bucket", dict(choices=QUEUE_BUCKETS))],
    "goal-new": [("--title", dict(required=True)), *BY, ("--description", dict(default="")),
                 ("--status", dict(default="observed")), ("--scope", dict(default="")), ("--project",),
                 ("--phase", dict(default="")), ("--done-when", dict(default="")), ("--key",)],
    "goal-authorize": [("id",), *BY, ("--note", dict(default=""))],
    "goal-complete": [("id",), *BY, ("--note", dict(default=""))],
    "goal-cancel": [("id",), *BY, ("--reason", dict(default=""))],
    "task-new": [("--goal", dict(required=True)), ("--title", dict(required=True)), *BY,
                 ("--acceptance", dict(default="")), ("--next", dict(default="")), ("--depends", dict(default="")),
                 ("--owner", dict(default="hermes")), ("--key",)],
    "task-ready": [("id",), *BY, ("--note", dict(default=""))],
    "task-block": [("id",), *BY, ("--kind", dict(required=True, choices=BLOCK_KINDS)), ("--reason", dict(required=True))],
    "task-unblock": [("id",), *BY, ("--note", dict(default=""))],
    "task-retry": [("id",), *BY],
    "task-accept": [("id",), *BY, ("--basis", dict(required=True)), ("--cites", dict(required=True)),
                    ("--fail", dict(action="store_true"))],
    "task-complete": [("id",), *BY, ("--note", dict(default=""))],
    "task-rework": [("id",), *BY, ("--note", dict(required=True))],
    "task-cancel": [("id",), *BY, ("--reason", dict(default=""))],
    "run-start": [("--task", dict(required=True)), ("--agent", dict(required=True)), *BY, ("--order",), ("--session",), ("--key",),
                  ("--purpose", dict(default="execute", choices=RUN_PURPOSES, help="review：给 review 里的任务开只读审查 Run"))],
    "run-finish": [("id",), ("--status", dict(required=True, choices=RUN_STATUSES)), *BY, ("--receipt",), ("--key",)],
    "run-receipt": [("id",), ("--receipt", dict(required=True)), ("--by", dict(default="hermes")), ("--message-id",), ("--key",)],
    "run-resume": [("id",), *BY, ("--note", dict(default=""))],
    "run-link": [("run",), ("--task", dict(required=True)), *BY],
    "ref-add": [("--type", dict(required=True, choices=OBJECT_TYPES)), ("--id", dict(required=True)),
                ("--kind", dict(required=True)), ("--ref", dict(required=True)), ("--url", dict(default="")),
                ("--note", dict(default=""))],
    "show": [("id",)], "list": [("--goal",), ("--status",)], "events": [("id",)],
}


def _run(s, a, turn):
    """跑一条命令，把结果收成 _Done（不抛 StateError）：--by user 时先登记消息占用，再做动作，二者同一事务由 run_cli 保证。"""
    try:
        if turn:
            s.claim_message(turn, purpose=a.cmd)
        _commands(s, a)
        return _Done(False, f"命令 {a.cmd} 没有给出结果", {"error": "Internal"})
    except _Done as d:
        return d
    except StateError as e:
        kw = {"error": type(e).__name__}
        if turn:
            kw["message_id"] = turn
        return _Done(False, str(e), kw)


def run_cli(a):
    """P1-1：--by user 的网络核实放在事务之前；消息占用和业务动作放在同一个 _tx 里，只有 _Done(ok=True) 才提交——
    拒绝、StateError、进程中途被杀（任何 BaseException）都整体回滚，占用和半截动作一起退，常浩不用再说一遍。"""
    with Store(a.db) as s:
        turn = _user_turn(a)
        if not turn:
            d = _run(s, a, None)
        else:
            with s._tx():
                d = _run(s, a, turn)
                if not d.ok:
                    raise d          # _tx 回滚后继续往上抛，main 打印
    raise d


def _commands(s, a):
    if True:
        if a.cmd == "init":
            done(True, f"状态库就绪：{s.path}（schema v{s.version()}）", **s.summary())
        if a.cmd == "backup":
            done(True, f"已备份到 {s.backup(a.to)}")
        if a.cmd == "restore":
            if not a.yes:
                done(False, "restore 会整体替换当前状态库，确认后加 --yes")
            done(True, f"已从 {a.src} 恢复", **s.restore(a.src))
        if a.cmd == "sync-orders":
            st = s.sync_orders(a.dispatch)
            done(True, f"派单台账已同步（台账 {st['ledger']}）", **st)
        if a.cmd == "queue":
            q = s.queue(a.dispatch)
            if a.bucket:
                done(True, f"{a.bucket}：{len(q[a.bucket])} 项", bucket=a.bucket, items=q[a.bucket], ledger=q["ledger"])
            counts = {k: len(q[k]) for k in QUEUE_BUCKETS}
            done(True, "；".join(f"{k} {v}" for k, v in counts.items() if v) or "没有待推进的对象", counts=counts, **q)
        if a.cmd == "reconcile":
            st = s.reconcile(a.dispatch)
            body = f"补上 {len(st['missed'])} 单" if st["missed"] else "没有漏掉的"
            if st["conflicts"]:
                body += f"；冲突 {len(st['conflicts'])} 单要人工核对"
            if st["ignored_dirs"]:
                body += f"；{len(st['ignored_dirs'])} 个目录没有 order.json 不算工单"
            done(True, f"对账完成（台账 {st['ledger']}）：{body}", **st)
        if a.cmd == "recover":
            synced = None if a.no_sync else s.sync_orders(a.dispatch)
            out = s.recover(stale_after=a.stale_after, dispatch_dir=a.dispatch)
            body = "；".join(f"{k} {len(v)}" for k, v in out.items() if isinstance(v, list) and v) or "没有需要处理的"
            done(True, f"恢复完成（台账 {out['ledger']}）：{body}", synced=synced, **out)
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
            done(True, f"已建任务 {t['id']}（{t['status']}，归 {a.goal}）", task=t)
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
        if a.cmd == "task-accept":
            cites = [c for c in a.cites.split(",") if c]
            r = s.accept_task(a.id, _by(a), a.basis, cites, passed=not a.fail)
            done(True, f"任务 {a.id} 验收{'不通过，退回 ready' if a.fail else '通过，可标完成'}", **r)
        if a.cmd == "task-complete":
            done(True, f"任务 {a.id} 已完成", **s.complete_task(a.id, _by(a), a.note))
        if a.cmd == "task-rework":
            done(True, f"任务 {a.id} 退回 ready", **s.rework_task(a.id, _by(a), a.note))
        if a.cmd == "task-cancel":
            done(True, f"任务 {a.id} 已撤销", **s.cancel_task(a.id, _by(a), a.reason))
        if a.cmd == "run-start":
            r = s.start_run(a.task, a.agent, _by(a), a.order, a.session, idem_key=a.key, purpose=a.purpose)
            if not r["applied"]:
                done(True, f"同一事件已处理过，不重复开跑（Run {r['run']['id'] if r['run'] else '?'}）", **r)
            if a.purpose == "review":
                done(True, f"已开始审查 {r['run']['id']}（任务 {a.task} 状态不变，回执只作证据）", **r)
            done(True, f"已开始 {r['run']['id']}（任务 {a.task} → running）", **r)
        if a.cmd == "run-finish":
            rc = _read_json(a.receipt) if a.receipt else None
            done(True, f"Run {a.id} 记为 {a.status}", **s.finish_run(a.id, a.status, _by(a), rc, idem_key=a.key))
        if a.cmd == "run-receipt":
            out = s.receive_receipt(a.id, _read_json(a.receipt), _by(a), a.key)
            done(True, "回执已处理" if out["applied"] else "同一回执已处理过，不重复更新", **out)
        if a.cmd == "run-resume":
            done(True, f"Run {a.id} 续跑", **s.resume_run(a.id, _by(a), a.note))
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
    except _Done as d:
        _exit(d)
    except StateError as e:
        _exit(_Done(False, str(e), {"error": type(e).__name__}))


if __name__ == "__main__":
    main()
