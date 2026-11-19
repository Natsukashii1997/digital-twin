#!/usr/bin/env python3
"""恢复闸：先对账、再放行受保护副作用（批 0D 恢复门，#195；方案 docs/agent-autoloop-底座-方案.md 5.3「恢复顺序」、
5.5 不变量 3；ADR-015 生效顺序第 3 条；设计 docs/恢复门-0D-设计.md）。

中台自己会恢复接单、排队和重试，「恢复轮次 ≠ 恢复外部写操作」。所以每个受保护副作用的入口（受信发布、合并、发飞书卡片、
回答卡片、往中台发派单）动手前都过 ensure_open()：

**闸什么时候关**（任一变化都关，记的「纪元」对不上就关）：
  - 开机（kern.boottime 变了；Linux 上读 boot_id）、睡醒（kern.waketime 变了）；
  - 中台重启或换版本（中台 up.pid 的进程号或进程启动时间变了、/healthz 的提交号变了）；中台连不上、认不出中台进程也算关着；
  - 任何账本里出现「结果不明」（出口账本 exits、本人点击 claims 的 unknown、秘书代批的 unknown）——断网、超时、强杀都会走到这里；
  - 维护停写（hold：升级、回滚、搬家），只由 release 结束。

**检查和领资格之间没有空隙**（审查 P1-03）：ensure_open() 只是先看一眼、需要时对账；真正领写入资格的那个写事务里再
admit(con, 纪元)——出口账本落盘 executing、派单事件领取、本人回答领发送资格都这样做（不经出口账本的入口用 admission()）。
结果不明的登记、维护停写都要出口账本这把写锁，所以「先关闸」和「先领资格」一定分得出先后。

**对账顺序**（reconcile；走完才开闸，无法确认的停下并报告，从不重做）：
  1. 本机账本：出口账本里「在做、但做的进程已经不在」的条目改成结果不明（强杀、断电、睡死）；
  2. 凭证与镜像：按凭证库已提交的状态重写工人只读镜像，并回读核对（任务、状态、代次、作废代次）；对不上不开闸；
  3. 中台：/healthz 通、提交号等于锁定的版本（hub-pin.json，升级流程验证通过后才改）；不是就不开闸；
  4. 外部回读：出口账本里结果不明的逐条只读回读 GitHub / 飞书；本人点击里结果不明、在途过久的只读回读中台卡片；
     连不上的留着下次，对不上的记「核不清」交常浩；秘书账本里还有结果不明、核不清、还在送的代批就不开闸
     （常浩 11-19 定；秘书自己的对账 secretary_gate.reconcile 不改，核不清的由常浩定）；
  5. 结算开闸：记下这次的纪元和报告。仍冻结的范围（结果不明、核不清）各自继续拒绝，别的照常放行。
派单事件（autoloop_grants 的 hub_events）不在这里补发：中台按同一来源同一 id 去重，开闸后照常 send-pending 补发。

**按「将来在 dt-gate 里运行」设计**（ADR-015）：闸的状态和出口账本在同一个库（运行账号的 DigitalTwin/autoloop/exits.db），
凭证库、卡片库、中台目录、锁定版本文件都从运行账号的家目录或环境变量取，不写死主账号路径。恢复闸本身不签、不用中台钥匙
（中台只读 /healthz 和 up.pid）；但外部回读**有认证依赖**（审查 P2-02）：回读卡片用 autoloop_answer 的只读窗口（通道钥匙），
回读 GitHub 用运行账号的 gh 登录，回读飞书用 DT_EXITS_LARK_PROFILE 指定的机器人登录配置——0E-2 要把这些放在 dt-gate 自己名下
（门卫机器人的配置），不把秘书账号的机器人凭据复制过来。

**维护停写与恢复点**（审查 P1-06）：升级 / 回滚 / 搬家先 hold（停写，登记恢复点：快照、中台版本、快照时最大卡片号），
排空在做的写（in_flight_writes 为空）再做快照；新版本核对通过、锁定后 release retired（升级前快照作废，restorable 再也不认）；
失败时只能用仍在维护的恢复点回滚，回滚后核对卡片号没有错位才 release rolled_back，对不上保持停写交常浩。

  autoloop_recovery.py status [--json]          只读：闸开没开、为什么、冻结了哪些范围（工程自检以后接这个）
  autoloop_recovery.py reconcile                手动对账一次（入口平时会自己调）
  autoloop_recovery.py pin --commit <40 位> --confirm   锁定中台版本（安装 / 升级验证通过后，常浩说了才跑）
  autoloop_recovery.py hold --point P --snapshot 目录 --commit C --max-card N --confirm    维护开始（停写、登记恢复点）
  autoloop_recovery.py writes                    只读：还在做的写（排空了才能快照）
  autoloop_recovery.py release --point P --outcome retired|rolled_back --confirm          维护结束
环境变量：DT_HUB（中台地址，没有就读中台 config.json）、DT_HUB_PIN（锁定版本文件）、AGENT_AUTOLOOP_HOME，
以及 autoloop_exits、autoloop_grants、autoloop_feishu 的路径变量。不调用 AI。
"""
import argparse
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_exits as E  # noqa: E402
import autoloop_grants as G  # noqa: E402

SHA = re.compile(r"^[0-9a-f]{40}$")


class HubDown(Exception):
    pass


# ---------- 纪元 ----------
def _sysctl_sec(name):
    try:
        out = subprocess.run(["/usr/sbin/sysctl", "-n", name], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return "?"
    m = re.search(r"sec = (\d+)", out)
    return m.group(1) if m else "?"


def hub_home():
    return Path(os.environ.get("AGENT_AUTOLOOP_HOME") or "~/.agent-autoloop").expanduser()


def hub_url():
    if os.environ.get("DT_HUB"):
        return os.environ["DT_HUB"].rstrip("/")
    try:
        return json.loads((hub_home() / "config.json").read_text(encoding="utf-8"))["url"].rstrip("/")
    except (OSError, ValueError, KeyError, TypeError):
        raise HubDown("读不到中台地址（config.json）")


def healthz():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(hub_url() + "/healthz", timeout=5) as r:
            out = json.loads(r.read().decode())
    except (OSError, ValueError) as e:
        raise HubDown(f"中台连不上（{type(e).__name__}）")
    if not isinstance(out, dict) or out.get("ok") is not True:
        raise HubDown("中台 /healthz 不正常")
    return str(out.get("commit") or "")


def hub_process():
    """中台进程：up.pid 里的进程号 + 进程启动时间（进程号会被复用，只看号不够），并核对这个进程确实是 agent-autoloop。
    认不出（没有 up.pid、进程不在、查不了、不是中台）一律抛 HubDown，闸不开（审查 P2-01）。"""
    try:
        pid = int((hub_home() / "up.pid").read_text().strip().split()[0])
    except (OSError, ValueError, IndexError):
        raise HubDown("认不出中台进程（读不到 up.pid）")
    start = E.proc_start(pid)
    if start in (None, "?"):
        raise HubDown("认不出中台进程（up.pid 里的进程不在或查不了）")
    try:
        cmd = subprocess.run(["/bin/ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        raise HubDown("认不出中台进程（查不了命令行）")
    if "agent-autoloop" not in cmd:
        raise HubDown("up.pid 里的进程不是中台")
    return f"{pid}@{start}"


def current_epoch():
    """开机、睡醒、中台进程、中台版本；中台连不上抛 HubDown。"""
    return {"boot": E.boot_id(), "wake": _sysctl_sec("kern.waketime"),
            "hub_commit": healthz(), "hub_process": hub_process()}


# ---------- 锁定版本 ----------
def pin_path():
    return Path(os.environ.get("DT_HUB_PIN") or G.SUPPORT / "autoloop/hub-pin.json").expanduser()


def pinned():
    try:
        c = json.loads(pin_path().read_text(encoding="utf-8")).get("commit")
    except (OSError, ValueError, AttributeError):
        return None
    return c if isinstance(c, str) and SHA.match(c) else None


def pin(commit, confirm=False):
    if not confirm:
        raise E.Refused("锁定中台版本要常浩说了才跑：加 --confirm")
    if not SHA.match(commit or ""):
        raise E.Refused("提交号要完整的 40 位")
    p = pin_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"commit": commit, "at": E.iso(E.now_tz())}, f)
    os.replace(tmp, p)
    with E.db() as con:
        E.mark_dirty(con, "锁定版本改了")
    return {"pinned": commit}


# ---------- 闸 ----------
def gate_row():
    with E.db(write=False) as con:
        r = con.execute("SELECT * FROM gate WHERE id = 1").fetchone()
        return dict(r) if r else None


def closed_reason(row, epoch):
    if row and row.get("hold"):
        return f"维护中（恢复点 {row['hold']}），停写到维护结束"
    if not row or not row.get("epoch"):
        return "还没对过账"
    if row["dirty"]:
        return row.get("dirty_reason") or "出现结果不明，要先对账"
    old = json.loads(row["epoch"])
    words = {"boot": "电脑重启过", "wake": "电脑睡醒过", "hub_commit": "中台换了版本", "hub_process": "中台重启过"}
    changed = [words[k] for k in words if old.get(k) != epoch.get(k)]
    return "、".join(changed) + "，要先对账" if changed else None


@contextlib.contextmanager
def recovery_lock():
    p = E.db_path().with_name("recovery.lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)                   # 同一时刻只有一个对账；后来的等它走完再看闸
        yield
    finally:
        os.close(fd)


def ensure_open(readers=None):
    """入口共用：闸开着就放行；关着就先对账（同一把锁串行），对完还关着就拒绝（Refused，说清原因）。
    返回这次核对时的纪元。只是「看一眼」：真正领写入资格时，调用方必须在自己登记的写事务里再 admit(con, 纪元)。"""
    try:
        epoch = current_epoch()
    except HubDown as e:
        raise E.Refused(f"恢复闸关着：{e}")
    if closed_reason(gate_row(), epoch) is None:
        return epoch
    with recovery_lock():
        try:
            epoch = current_epoch()
        except HubDown as e:
            raise E.Refused(f"恢复闸关着：{e}")
        if closed_reason(gate_row(), epoch) is None:            # 等锁期间别人已经对完
            return epoch
        rep = reconcile(readers)
    if not rep["open"]:
        raise E.Refused(f"恢复闸关着：{rep['why']}")
    return rep["epoch"]


def admit(con, epoch):
    """在调用方领写入资格的同一个出口账本写事务里核开闸资格（审查 P1-03）：闸记的纪元正是刚核过的那个、没有待对的
    结果不明、不在维护停写。结果不明的登记（mark_dirty）、维护停写（hold）都要这把写锁，所以和领资格有先后。"""
    r = con.execute("SELECT epoch, dirty, dirty_reason, hold FROM gate WHERE id = 1").fetchone()
    if r is None or epoch is None or r["epoch"] != json.dumps(epoch, sort_keys=True):
        raise E.Refused("恢复闸关着：纪元变了或还没对过账，要先对账")
    if r["hold"]:
        raise E.Refused(f"恢复闸关着：维护中（恢复点 {r['hold']}）")
    if r["dirty"]:
        raise E.Refused(f"恢复闸关着：{r['dirty_reason'] or '出现结果不明'}，要先对账")


@contextlib.contextmanager
def admission(epoch=None):
    """给不经出口账本的入口用（派单事件、本人回答）：拿出口账本写锁、核开闸资格，调用方在 with 里领自己的发送资格并提交，
    退出 with 才放锁。锁顺序固定为「自己的库 → 出口账本」，不反过来。"""
    epoch = epoch or ensure_open()
    with E.db() as con:
        admit(con, epoch)
        yield con


# ---------- 维护停写与恢复点（升级、回滚、搬家；审查 P1-06） ----------
def hold(point_id, snapshot, hub_commit, max_card):
    """维护开始：先停写（闸关、入口和 admit 都拒绝），再登记恢复点。之后排空在做的动作、再做快照。"""
    if not re.match(r"^[\w.-]{1,64}$", point_id or ""):
        raise E.Refused("恢复点编号写法不对")
    with E.db() as con:
        r = con.execute("SELECT hold FROM gate WHERE id = 1").fetchone()
        if r and r["hold"]:
            raise E.Refused(f"已经在维护（恢复点 {r['hold']}）")
        con.execute("INSERT INTO recovery_points (id, created_at, hub_commit, snapshot, max_card, state) "
                    "VALUES (?,?,?,?,?, 'held')", (point_id, E.iso(E.now_tz()), hub_commit, str(snapshot), int(max_card)))
        con.execute("INSERT INTO gate (id, hold) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET hold = excluded.hold",
                    (point_id,))
    return {"held": point_id}


def in_flight_writes():
    """还在做的写：出口账本 executing、本人回答 inflight、派单事件在途。排空完才能做快照。"""
    out = []
    with E.db(write=False) as con:
        out += [f"出口 {r['key']}" for r in con.execute("SELECT key FROM exits WHERE state = 'executing'")]
    try:
        import autoloop_feishu as F
        if F.db_file().exists():
            with F.db() as con:
                out += [f"本人回答 卡片 {r[0]}" for r in con.execute("SELECT card_id FROM claims WHERE state = 'inflight'")]
    except ImportError:
        pass
    if G.db_path().exists():
        with G.tx() as con:
            out += [f"派单事件 {r[0]}" for r in con.execute("SELECT event_key FROM hub_events WHERE sent = 2")]
    return out


def restorable(point_id):
    """恢复点还能不能用来回滚：只有仍在维护（held）的能用。新版本锁定、维护结束后（retired），升级前的快照作废——
    之后的卡片、轮次、对外动作会丢，卡片号会被复用（不靠文档约定，回滚前必须过这一关）。"""
    with E.db(write=False) as con:
        r = con.execute("SELECT * FROM recovery_points WHERE id = ?", (point_id,)).fetchone()
    if r is None or r["state"] != "held":
        raise E.Refused(f"恢复点 {point_id} 不能用来回滚（{'没有登记' if r is None else r['state']}）")
    return dict(r)


def mismatched_after(max_card):
    """回滚后核对：快照之后有没有发过卡片（桥接卡片库、出口账本里卡片号大于快照时最大号的）。有就说明停写没守住，不能开闸。"""
    bad = []
    with E.db(write=False) as con:
        for (k,) in con.execute("SELECT key FROM exits WHERE kind = 'send_feishu' AND key LIKE 'send_feishu:card-%'"):
            n = k.rsplit("-", 1)[-1]
            if n.isdigit() and int(n) > max_card:
                bad.append(k)
    try:
        import autoloop_feishu as F
        if F.db_file().exists():
            with F.db() as con:
                bad += [f"cards.db 卡片 {r[0]}" for r in con.execute("SELECT card_id FROM feishu_cards WHERE card_id > ?",
                                                                      (max_card,))]
    except ImportError:
        pass
    return bad


def release(point_id, outcome, note=""):
    """维护结束。outcome：retired（新版本核对通过、已锁定，升级前快照作废）或 rolled_back（已用这个恢复点回滚）。
    回滚的先核对卡片号没有错位；对不上就保持停写，交常浩。结束后闸要重新对账才开。"""
    if outcome not in ("retired", "rolled_back"):
        raise E.Refused("维护结束只能是 retired 或 rolled_back")
    point = restorable(point_id)
    if outcome == "rolled_back":
        bad = mismatched_after(point["max_card"])
        if bad:
            raise E.Refused(f"回滚后卡片号可能错位（{'、'.join(bad[:5])}），保持停写，交常浩")
    with E.db() as con:
        con.execute("UPDATE recovery_points SET state = ?, closed_at = ?, note = ? WHERE id = ? AND state = 'held'",
                    (outcome, E.iso(E.now_tz()), note[:300], point_id))
        con.execute("UPDATE gate SET hold = NULL WHERE id = 1 AND hold = ?", (point_id,))
        E.mark_dirty(con, f"维护结束（{outcome}），要先对账")
    return {"released": point_id, "outcome": outcome}


# ---------- 对账 ----------
def _check_mirror():
    """第 2 步：凭证库存在时，按已提交的状态重写镜像并回读核对。没有凭证库不算错（还没签过凭证）。"""
    if not G.db_path().exists():
        return None, "还没有凭证库，跳过"
    try:
        G.refresh_status()
        doc = json.loads(G.status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, G.Rejected) as e:
        return f"工人镜像写不成或读不回（{type(e).__name__}）", None
    with G.tx() as con:
        want = {r["task_id"]: (r["status"], r["generation"], sorted(G.voided_of(con, r["task_id"])))
                for r in con.execute("SELECT * FROM grants")}
    got = {k: (v.get("status"), v.get("generation"), v.get("voided_generations"))
           for k, v in (doc.get("tasks") or {}).items()}
    if want != got:
        return "工人镜像和凭证库对不上", None
    return None, f"镜像和凭证库一致（{len(want)} 张）"


def _answers():
    """第 4 步里本人点击那一块：只对结果不明、在途过久的（只读回读中台卡片），不提交排队的（那是开闸后的动作）。"""
    try:
        import autoloop_answer as A
        import autoloop_feishu as F
    except ImportError as e:
        return [f"本人点击账本读不了：{e}"]
    if not F.db_file().exists():
        return []
    try:
        return [f"卡片 {cid}：{text}" for cid, text in A.reconcile(queued=False)]
    except F.Fail as e:
        return [f"本人点击对账没做成：{e}"]


def _secretary():
    """秘书账本里结果不明、核不清、还在送的代批（常浩 11-19「都按推荐」：有就不开闸；秘书自己的对账逻辑不改，
    由 secretary_gate.reconcile 结掉，核不清的由常浩定）。库不在、还没有秘书表就是没有（不新建）；读不了不开闸。
    返回 (不开闸的原因或 None, 说明)。"""
    import sqlite3
    import twin_state as TS
    p = Path(os.environ.get("DT_STATE_DB", TS.DEFAULT_DB)).expanduser()
    if not p.exists():
        return None, "还没有状态库，跳过"
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'secretary_decisions'").fetchone():
                return None, "还没有秘书账本，跳过"
            rows = con.execute("SELECT id, status FROM secretary_decisions WHERE action != 'handoff' AND status IN "
                               "('executing', 'unknown', 'unreconciled') ORDER BY created_at").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"秘书账本读不了（{type(e).__name__}）", None
    if rows:
        listed = "、".join(f"{r[0]}（{r[1]}）" for r in rows[:5])
        return f"秘书账本里有 {len(rows)} 个代批结果不明或还在送（{listed}），秘书对完账才开闸", None
    return None, "秘书账本没有结果不明的代批"


def reconcile(readers=None):
    """按顺序对账一次，开闸或说明为什么不开。不做任何外部写（只重写本机的工人镜像）。"""
    steps, why = [], None
    row = gate_row() or {}
    seen = row.get("dirty", 0)
    if row.get("hold"):                                                   # 维护停写只由 release 结束，对账不开
        why = f"维护中（恢复点 {row['hold']}），停写到维护结束"                             # 只清这个数，对账期间新出的不明留到下次
    with E.db() as con:                                                   # 1 本机账本
        swept = E.sweep(con, E.now_tz())
    steps.append({"step": "本机账本", "note": f"强杀留下的 {len(swept)} 条改成结果不明" if swept else "没有中断的动作"})
    bad, note = _check_mirror()                                           # 2 凭证与镜像
    steps.append({"step": "凭证与镜像", "note": bad or note})
    why = why or bad
    try:                                                                  # 3 中台
        epoch = current_epoch()
        want = pinned()
        if want is None:
            hub_note = "没有锁定中台版本（hub-pin.json），不开闸"
        elif epoch["hub_commit"] != want:
            hub_note = f"中台版本 {epoch['hub_commit'][:12]} 不是锁定的 {want[:12]}，不开闸"
        else:
            hub_note = None
        steps.append({"step": "中台", "note": hub_note or f"通，版本是锁定的 {want[:12]}"})
        why = why or hub_note
    except HubDown as e:
        epoch = None
        steps.append({"step": "中台", "note": str(e)})
        why = why or str(e)
    exits = E.reconcile(readers)                                          # 4 外部回读
    answers = _answers()
    sec_bad, sec_note = _secretary()
    steps.append({"step": "外部回读", "exits": exits, "answers": answers, "secretary": sec_bad or sec_note})
    why = why or sec_bad
    with E.db() as con:                                                   # 5 结算开闸
        frozen = E.frozen_scopes(con)
        report = {"steps": steps, "frozen": frozen}
        now_dirty = (con.execute("SELECT dirty FROM gate WHERE id = 1").fetchone() or [0])[0]
        if why is None and now_dirty != seen:
            why = "对账期间又出现结果不明，要再对一次"
        if why is None:
            con.execute("INSERT INTO gate (id, epoch, opened_at, dirty, dirty_reason, report) VALUES (1, ?, ?, 0, NULL, ?) "
                        "ON CONFLICT(id) DO UPDATE SET epoch = excluded.epoch, opened_at = excluded.opened_at, dirty = 0, "
                        "dirty_reason = NULL, report = excluded.report",
                        (json.dumps(epoch, sort_keys=True), E.iso(E.now_tz()), json.dumps(report, ensure_ascii=False)))
        else:
            con.execute("INSERT INTO gate (id, dirty, dirty_reason, report) VALUES (1, 1, ?, ?) ON CONFLICT(id) DO UPDATE "
                        "SET dirty = 1, dirty_reason = excluded.dirty_reason, report = excluded.report",
                        (why[:300], json.dumps(report, ensure_ascii=False)))
    return {"open": why is None, "why": why, "frozen": frozen, "steps": steps, "epoch": epoch}


def status():
    """只读：闸、原因、冻结的范围、上次对账报告。"""
    row = gate_row()
    try:
        reason = closed_reason(row, current_epoch())
    except HubDown as e:
        reason = str(e)
    with E.db(write=False) as con:
        frozen = E.frozen_scopes(con)
        stuck = E.stalled(con, E.now_tz())
    return {"open": reason is None, "why": reason, "frozen": frozen, "stalled": stuck, "pinned": pinned(),
            "opened_at": (row or {}).get("opened_at"), "last_report": json.loads((row or {}).get("report") or "null")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").add_argument("--json", action="store_true")
    sub.add_parser("reconcile")
    p = sub.add_parser("pin")
    p.add_argument("--commit", required=True)
    p.add_argument("--confirm", action="store_true")
    h = sub.add_parser("hold")
    for k in ("--point", "--snapshot", "--commit"):
        h.add_argument(k, required=True)
    h.add_argument("--max-card", type=int, required=True)
    h.add_argument("--confirm", action="store_true")
    sub.add_parser("writes")
    r = sub.add_parser("release")
    r.add_argument("--point", required=True)
    r.add_argument("--outcome", required=True)
    r.add_argument("--note", default="")
    r.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd in ("hold", "release") and not a.confirm:
            raise E.Refused("维护开始 / 结束要常浩说了才跑：加 --confirm")
        if a.cmd == "status":
            out = status()
        elif a.cmd == "reconcile":
            with recovery_lock():
                out = reconcile()
        elif a.cmd == "hold":
            out = hold(a.point, a.snapshot, a.commit, a.max_card)
        elif a.cmd == "writes":
            out = {"in_flight": in_flight_writes()}
        elif a.cmd == "release":
            out = release(a.point, a.outcome, a.note)
        else:
            out = pin(a.commit, a.confirm)
    except E.Refused as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("open", True) else 2


if __name__ == "__main__":
    sys.exit(main())
