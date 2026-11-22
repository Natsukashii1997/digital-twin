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
     （常浩 11-19 定；秘书自己的对账 secretary_gate.reconcile 不改，核不清的由常浩 secretary_gate settle 定）——
     例外（常浩 11-20 选甲）：② 重派 / ③ 返修只差网关补发派单事件的不挡（补发要过闸，挡了就是死循环；中台按 id 去重，
     补发不会多派）；
  5. 结算开闸：先持凭证库写锁、再持出口账本写锁，两把都拿到后重判秘书只差网关补发（冻结、撤销进不来），再看一遍——对账期间又出现结果不明、还有已开始没记结果的受保护动作（出口账本 executing、
     本人回答 inflight、秘书代批 executing，第 5 步在凭证库锁里重判只差网关补发的秘书 ② / ③ 除外，L3 第 5 轮 P1-08）就不开。
     都过了才记下纪元和报告。仍冻结的范围（结果不明、核不清）
     各自继续拒绝，别的照常放行。
派单事件（autoloop_grants 的 hub_events）不在这里补发：中台按同一来源同一 id 去重，开闸后照常 send-pending 补发。

**按「将来在 dt-gate 里运行」设计**（ADR-015）：闸的状态和出口账本在同一个库（运行账号的 DigitalTwin/autoloop/exits.db），
另有库旁的关闸标记目录 gate-dirty.d/（L3 第 4 轮 P1-08：关闸先建标记、不靠数据库锁；有标记就当关着）。0E-2 搬进 dt-gate 时
两者都不搬（和 0D 会话 11-22 对过）：dt-gate 从空库开始，闸在新库里「还没对过账」、天然关着，要重新 pin 并对账才开；
搬之前主账号 hold、writes 排空、leftovers 为空（结果不明、核不清、在做的、关闸标记、凭证、派单事件、点击、秘书、托管、
任务锁都清零）才搬，搬完主账号保持停写；回滚时主账号 release rolled_back 之前，搬家脚本先以 dt-gate 身份跑 cards-after，
补 mismatched_after 看不到的 dt-gate 那一半（dt-gate 开闸后发过的卡片号会被退回旧快照的中台复用）；
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
  autoloop_recovery.py leftovers                 只读：控制面账本里还没结清的东西（0E-2 搬家前核；不建库）
  autoloop_recovery.py cards-after --max-card N  只读：本账号账本里卡片号大于 N 的（0E-2 回滚前以 dt-gate 身份核；不建库）
  autoloop_recovery.py release --point P --outcome retired|rolled_back --confirm          维护结束
环境变量：DT_HUB（中台地址，没有就读中台 config.json）、DT_HUB_PIN（锁定版本文件）、AGENT_AUTOLOOP_HOME，
以及 autoloop_exits、autoloop_grants、autoloop_feishu 的路径变量。不调用 AI。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import sqlite3
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
    if E.read_markers():                                 # 关闸标记（P1-08）：账本那笔没记上也算关着
        return "出现结果不明（关闸标记），要先对账"
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
    结果不明、不在维护停写、没有关闸标记。维护停写（hold）要这把写锁，和领资格有先后；关闸先建标记文件（不等锁）、
    再在库里记一笔：标记一出现，之后的领资格都被拦，在它之前领到的等同于关闸之前领到的（L3 第 4 轮 P1-08）。"""
    r = con.execute("SELECT epoch, dirty, dirty_reason, hold FROM gate WHERE id = 1").fetchone()
    if r is None or epoch is None or r["epoch"] != json.dumps(epoch, sort_keys=True):
        raise E.Refused("恢复闸关着：纪元变了或还没对过账，要先对账")
    if r["hold"]:
        raise E.Refused(f"恢复闸关着：维护中（恢复点 {r['hold']}）")
    if r["dirty"]:
        raise E.Refused(f"恢复闸关着：{r['dirty_reason'] or '出现结果不明'}，要先对账")
    if E.read_markers():
        raise E.Refused("恢复闸关着：出现结果不明（关闸标记），要先对账")


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


SECRETARY_STALE = dt.timedelta(seconds=120)   # 同 secretary_gate.STALE
EXPIRY_MARGIN = dt.timedelta(seconds=60)      # 凭证再过这么久就到期的续接事件，不算「只差网关补发」（续审第 2 轮 P1-08）
RO_TIMEOUT = 3                                # 卡片库、状态库只读最多等几秒：第 5 步持着出口账本写锁读，读不了就不开闸（续审第 2 轮预审 P2）


def in_flight_writes():
    """还在做的写：出口账本 executing、本人回答 inflight、飞书桥接或秘书领卡正持桥接锁（发卡改卡、报送达、领卡登记）、派单事件刚领走在发、
    秘书代批在送。排空完才能做快照（第 2 轮预审 P2-C：桥接和秘书原来没列）。停写（hold）之后桥接每轮在锁里先看闸，
    所以「锁空着」就说明没有桥接写在途。派单事件只列刚领走（60 秒内）的：更早的那次 POST 已经结束（有超时），只是结果
    不明，开闸后按同一 id 补发；列进来停写期间又补发不了，排空永远完不成。"""
    out = []
    with E.db(write=False) as con:
        out += [f"出口 {r['key']}" for r in con.execute("SELECT key FROM exits WHERE state = 'executing'")]
    out += [f"本人回答 卡片 {c}" for c in _owner_inflight()]
    try:
        import autoloop_feishu as F
        if F.bridge_busy():                      # 不看 cards.db 在不在：桥接 / 秘书领卡持锁时库可能还没建（L3 第 3 轮 P1-06）
            out.append("飞书桥接或秘书领卡正持桥接锁")
    except ImportError:
        pass
    if G.db_path().exists():
        now = G.now_tz()
        with G.tx() as con:
            out += [f"派单事件 {r[0]}" for r in con.execute("SELECT event_key, claimed_at FROM hub_events WHERE sent = 2")
                    if now - G.parse_iso(r[1]) < G.STALE]
    out += [f"秘书代批 {d}" for d in _secretary_sending()]
    return out


def _owner_inflight():
    """本人回答还在发（claims inflight）的卡片号。只读打开卡片库（不建表、不要写锁：开闸前调用方正持出口账本写锁，
    锁顺序是「卡片库 → 出口账本」，这里只读、不等卡片库的写锁）。库或表不在就是没有；读不了当作有。"""
    try:
        import autoloop_feishu as F
    except ImportError:
        return []
    f = F.db_file()
    if not f.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{f}?mode=ro", uri=True, timeout=RO_TIMEOUT)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'claims'").fetchone():
                return []
            return [str(r[0]) for r in con.execute("SELECT card_id FROM claims WHERE state = 'inflight'")]
        finally:
            con.close()
    except sqlite3.Error as e:
        return [f"?（卡片库读不了：{type(e).__name__}）"]


def _started(con, waits=()):
    """开闸前最后一关（L3 第 5 轮 P1-08，常浩 11-21 经飞书选甲）：已开始、还没记结果的受保护动作——出口账本 executing、
    本人回答 inflight、秘书代批 executing（不论多久）。它们结果不明时先关闸再记，关闸那笔可能正等着出口账本这把写锁
    （标记文件又没写成）；这时开闸，抢到锁的新入口就会先于关闸登记被放行。闸关着时新动作领不到资格，所以这里只是等手上
    已开始的做完。在出口账本写事务里调用：这期间没有新的领资格。读不了一律算有（不开）。
    waits 是「只差网关补发」的秘书 ② / ③（11-20 选甲：结果不明也不挡闸，挡了就死锁——补发要过闸），不算进来。
    它由第 5 步在凭证库写锁里重新算（续审第 1 轮），凭证库锁一直持到开闸提交，这期间冻结、撤销、续接进不来；
    凭证库不在时就是第 4 步算的（那时根本没有可补发的事件，名单是空的）。"""
    return ([f"出口 {r[0]}" for r in con.execute("SELECT key FROM exits WHERE state = 'executing'")]
            + [f"本人回答 卡片 {c}" for c in _owner_inflight()]
            + [f"秘书代批 {d}" for d in _secretary_sending(fresh_only=False) if d not in waits])


def _secretary_sending(fresh_only=True):
    """秘书账本里正在送的代批（executing）。fresh_only 时只列 2 分钟内动过的（排空用：更早的是进程中途没了，由秘书对账
    处理，不算在途）；开闸前那一关全列。库或表不在就是没有；读不了当作有。"""
    import twin_state as TS
    p = Path(os.environ.get("DT_STATE_DB", TS.DEFAULT_DB)).expanduser()
    if not p.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=RO_TIMEOUT)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'secretary_decisions'").fetchone():
                return []
            now = G.now_tz()
            return [r[0] for r in con.execute("SELECT id, updated_at FROM secretary_decisions WHERE status = 'executing'")
                    if not fresh_only or now - G.parse_iso(r[1]) < SECRETARY_STALE]
        finally:
            con.close()
    except sqlite3.Error as e:
        return [f"（秘书账本读不了：{type(e).__name__}）"]


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


def _secretary(waits=None, gcon=None):
    """秘书账本里结果不明、核不清、还在送的代批（常浩 11-19「都按推荐」：有就不开闸），由 secretary_gate.reconcile 结掉
    （11-21 常浩定秘书对账也按决定号认续接事件，和这里同一套 G.bound_event），核不清的由常浩定。库不在、还没有秘书表就是没有（不新建）；读不了不开闸。
    返回 (不开闸的原因或 None, 说明)；给了 waits（列表）就把只差网关补发的决定号放进去，第 5 步不再把它们算成在途。
    gcon：调用方已持的凭证库写事务（第 5 步用，见 reconcile），不给就每条自己开一个。"""
    import twin_state as TS
    p = Path(os.environ.get("DT_STATE_DB", TS.DEFAULT_DB)).expanduser()
    if not p.exists():
        return None, "还没有状态库，跳过"
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=RO_TIMEOUT)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'secretary_decisions'").fetchone():
                return None, "还没有秘书账本，跳过"
            rows = con.execute("SELECT id, status, action, task_id, outcome, target FROM secretary_decisions "
                               "WHERE action != 'handoff' AND status IN ('executing', 'unknown', 'unreconciled') "
                               "ORDER BY created_at").fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"秘书账本读不了（{type(e).__name__}）", None
    waiting = [r[0] for r in rows if _waits_on_gateway(r, gcon)]
    if waits is not None:
        waits.extend(waiting)
    rows = [r for r in rows if r[0] not in waiting]
    if rows:
        listed = "、".join(f"{r[0]}（{r[1]}）" for r in rows[:5])
        return f"秘书账本里有 {len(rows)} 个代批结果不明或还在送（{listed}），秘书对完账才开闸", None
    if waiting:
        return None, f"秘书账本有 {len(waiting)} 个续接只差网关补发（{'、'.join(waiting[:5])}），开闸后 send-pending 补发、秘书对账结掉"
    return None, "秘书账本没有结果不明的代批"


def _waits_on_gateway(r, gcon=None):
    """秘书的 ② 重派、③ 返修只差网关补发：这次续接的派单事件已在凭证库记账、还没确认送到中台（sent 0 或 2）。
    这种不挡闸（第 2 轮预审 P1-A，常浩 11-20 选甲）：事件的结果由网关自己记着，中台按同一来源同一 id 去重，补发不会多派；
    而补发 send-pending 本身要过闸——挡闸就成了死循环（秘书等事件发出、事件等开闸）。开闸后补发送到，秘书对账记 done。
    认事件只认和这次决定绑定的（L3 第 3 轮 P1-09）：凭证库里正好一条带这个决定号的续接日志（和事件同一事务记的），
    日志里的事件就是它；事件的任务、续接种类要对上，代次要等于日志记的、也等于决定里记的（有记的话）；决定自己记了
    事件号的也要是同一个（G.bound_event，秘书对账同一套）。不按时间猜：别的代次、人工续接的事件一律不算。凭证对这条事件
    的代次已失效（冻结、撤销、过期）的补发不了，也不算（第 4 轮）。找不到、对不上、已作废或已送达的照常挡闸，
    由秘书对账结掉（核不清的由常浩 secretary_gate settle 定）。刚领走在发的（sent=2、领走不到 60 秒，同 G.STALE）还不是
    「只差补发」，是一次正在进行的发送：照常挡闸，等它出结果或过了 60 秒再算（续审第 1 轮）。凭证有效期按「此刻 + 60 秒」
    判（EXPIRY_MARGIN）：判完到开闸提交之间时间还在走，快到期的不靠「开闸后补发」放过（续审第 2 轮）。开闸之后才到期的
    不在这里管：那时事件补发不了，秘书对账按 sent 结掉（② 没领走记没做成，其余记核不清等常浩 settle），闸不会因此重新关上。"""
    did, status, action, task_id, outcome, target = r
    if action not in ("redispatch", "reject") or status not in ("executing", "unknown") or not G.db_path().exists():
        return False
    try:
        key = (json.loads(outcome or "{}") or {}).get("event_key")
        gen = (json.loads(target or "{}") or {}).get("generation")
    except (ValueError, AttributeError):
        return False
    kind = "redispatch" if action == "redispatch" else "rework"
    try:
        if gcon is None:
            with G.tx() as own:
                verdict, e, _ = G.bound_event(own, task_id, did, kind, gen, key, now=G.now_tz() + EXPIRY_MARGIN)
        else:
            verdict, e, _ = G.bound_event(gcon, task_id, did, kind, gen, key, now=G.now_tz() + EXPIRY_MARGIN)
        if verdict != "ok" or e["invalid"]:                       # 凭证已失效的事件补发不了，不算只差补发（第 4 轮）
            return False
        if e["sent"] == 0:
            return True
        return e["sent"] == 2 and G.now_tz() - G.parse_iso(e["claimed_at"]) >= G.STALE
    except (sqlite3.Error, ValueError, TypeError, KeyError, AttributeError):
        return False


def reconcile(readers=None):
    """按顺序对账一次，开闸或说明为什么不开。不做任何外部写（只重写本机的工人镜像）。"""
    steps, why = [], None
    row = gate_row() or {}
    seen = row.get("dirty", 0)
    marks = E.read_markers()                                              # 只清这几个标记，对账期间新建的留到下次
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
    waits = []
    sec_bad, sec_note = _secretary(waits)
    steps.append({"step": "外部回读", "exits": exits, "answers": answers, "secretary": sec_bad or sec_note})
    why = why or sec_bad
    with contextlib.ExitStack() as locks:                                 # 5 结算开闸
        # 先拿凭证库写锁、再拿出口账本写锁（锁顺序「凭证库 → 出口账本」），两把都拿到后把第 4 步认定的「只差网关补发」重新判一次，
        # 判完到开闸提交之间冻结、撤销、续接都进不来；凭证自然过期按此刻的时间判（续审第 1 轮 P1-08）。凭证库还不在就没有可补发的。
        # 已经知道不开闸就不拿凭证库锁（不白挡冻结、撤销）。拿着凭证库锁时出口账本只等 5 秒：等不到就这次不开闸，
        # 不让冻结、撤销跟着干等（它们自己最多等 30 秒）。
        gcon = None
        if why is None and G.db_path().exists():
            try:
                gcon = locks.enter_context(G.tx())
            except sqlite3.Error as e:
                why = f"凭证库这会儿打不开（{type(e).__name__}），不开闸"
        try:
            con = locks.enter_context(E.db(timeout=5 if gcon is not None else 30))
        except sqlite3.OperationalError as e:
            locks.close()
            return {"open": False, "why": f"出口账本这会儿被别的写入者占着（{e}），这次不开闸，下次再对", "frozen": [],
                    "steps": steps, "epoch": epoch}
        if gcon is not None:                      # 两把锁都拿到之后才重判：等出口账本锁那几秒里凭证可能自然过期（续审第 2 轮）
            waits = []
            sec_bad, _ = _secretary(waits, gcon)
            why = why or sec_bad
        frozen = E.frozen_scopes(con)
        report = {"steps": steps, "frozen": frozen}
        now_dirty = (con.execute("SELECT dirty FROM gate WHERE id = 1").fetchone() or [0])[0]
        if why is None and now_dirty != seen:
            why = "对账期间又出现结果不明，要再对一次"
        busy = _started(con, waits) if why is None else []
        if busy:
            why = f"还有 {len(busy)} 个受保护的动作已开始、没记结果（{'、'.join(busy[:5])}），等它们做完再开闸"
        if why is None and "?" in marks:
            why = "关闸标记目录读不了，不开闸"
        if why is None and set(E.read_markers()) - set(marks):
            why = "对账期间又出现结果不明（关闸标记），要再对一次"
        if why is None:
            E.clear_markers(marks)
            con.execute("INSERT INTO gate (id, epoch, opened_at, dirty, dirty_reason, report) VALUES (1, ?, ?, 0, NULL, ?) "
                        "ON CONFLICT(id) DO UPDATE SET epoch = excluded.epoch, opened_at = excluded.opened_at, dirty = 0, "
                        "dirty_reason = NULL, report = excluded.report",
                        (json.dumps(epoch, sort_keys=True), E.iso(E.now_tz()), json.dumps(report, ensure_ascii=False)))
        else:
            con.execute("INSERT INTO gate (id, dirty, dirty_reason, report) VALUES (1, 1, ?, ?) ON CONFLICT(id) DO UPDATE "
                        "SET dirty = 1, dirty_reason = excluded.dirty_reason, report = excluded.report",
                        (why[:300], json.dumps(report, ensure_ascii=False)))
    return {"open": why is None, "why": why, "frozen": frozen, "steps": steps, "epoch": epoch}


# ---------- 搬家前的只读汇总（0E-2；和 0D 会话 11-22 对过口径） ----------
def _ro(path):
    """只读打开（不建库、不建表、不等写锁太久）。"""
    import urllib.parse
    return sqlite3.connect(f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True, timeout=RO_TIMEOUT)


def _rows(path, table, sql, args=()):
    """库或表不在就是没有；读不了返回 None（调用方当作有）。"""
    if not Path(path).exists():
        return []
    try:
        con = _ro(path)
        try:
            if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone():
                return []
            return con.execute(sql, args).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return None


def leftovers():
    """只读：这个账号的控制面账本里还没结清的东西（0E-2 搬家用：dt-gate 从空的新账本开始，这些搬过去就忘了）。
    不依赖出口账本在不在、不新建任何库。返回 {"hold": 维护中的恢复点或 None, "items": [说明…]}；读不了的库记一条「读不了」。
    列的是：出口账本里结果不明 / 核不清 / 在做的、关闸标记、本人或秘书的点击排队 / 在发 / 结果不明的、秘书代批没结清的、
    派单事件没确认送达而凭证这一代还有效的、还有效或冻结着的任务凭证。"""
    items, hold = [], None
    p = E.db_path()
    r = _rows(p, "gate", "SELECT hold FROM gate WHERE id = 1")
    if r is None:
        items.append("出口账本读不了")
    elif r:
        hold = r[0][0]
    r = _rows(p, "exits", f"SELECT key, state FROM exits WHERE state IN ({','.join('?' * len(E.UNSETTLED))})",
              E.UNSETTLED)
    items += ["出口账本读不了"] if r is None else [f"出口 {k}（{s}）" for k, s in r]
    m = E.read_markers()
    if m:
        items.append(f"关闸标记 {len(m)} 个")
    try:
        import autoloop_feishu as F
        r = _rows(F.db_file(), "claims", "SELECT card_id, who, state FROM claims WHERE state IN ('queued', 'inflight', 'unknown')")
        items += ["卡片库读不了"] if r is None else [f"{'本人' if w == 'owner' else '秘书'}点击 卡片 {c}（{s}）" for c, w, s in r]
    except ImportError:
        pass
    import twin_state as TS
    r = _rows(Path(os.environ.get("DT_STATE_DB", TS.DEFAULT_DB)).expanduser(), "secretary_decisions",
              "SELECT id, status FROM secretary_decisions WHERE action != 'handoff' "
              "AND status IN ('executing', 'unknown', 'unreconciled')")
    items += ["秘书账本读不了"] if r is None else [f"秘书代批 {d}（{s}）" for d, s in r]
    g = G.db_path()
    r = _rows(g, "grants", "SELECT task_id, status FROM grants WHERE status IN ('active', 'frozen')")
    items += ["凭证库读不了"] if r is None else [f"任务凭证 {t}（{s}）" for t, s in r]
    r = _rows(g, "hub_events", "SELECT e.event_key FROM hub_events e JOIN grants g ON g.task_id = e.task_id "
                               "WHERE e.sent != 1 AND g.status IN ('active', 'frozen')")   # 凭证已结束的补发不了，不算
    items += ["凭证库读不了"] if r is None else [f"派单事件 {k}（没确认送达）" for (k,) in r]
    r = _rows(Path(os.environ.get("DT_STATE_DB", TS.DEFAULT_DB)).expanduser(), "custody_decisions",
              "SELECT id, status FROM custody_decisions WHERE status IN ('issued', 'executing', 'unknown', 'unreconciled')")
    items += ["托管账本读不了"] if r is None else [f"托管决定 {d}（{s}）" for d, s in r]   # 0D 会话 11-22：表存在就查
    items += _held_task_locks(g.parent / "task-locks")
    return {"hold": hold, "items": list(dict.fromkeys(items))}


def _held_task_locks(d):
    """任务锁被持着（受信发布、秘书 ② / ③ 或续接正在处理这张单）：试拿一下，拿得到马上放（0D 会话 11-22）。
    目录不在就是没有；只开已有的锁文件，不新建。"""
    out = []
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".lock"))
    except FileNotFoundError:
        return []
    except OSError:
        return ["任务锁目录读不了"]
    for n in names:
        try:
            fd = os.open(d / n, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            out.append(f"任务锁 {n} 打不开")
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
        except BlockingIOError:
            out.append(f"任务锁 {n[:-5]} 正被持着")
        finally:
            os.close(fd)
    return out


def cards_after(max_card):
    """只读：本账号出口账本、卡片库里卡片号大于 max_card 的（0E-2 回滚前以 dt-gate 身份查，补主账号 mismatched_after
    看不到的那一半）。不建库。读不了的记一条。"""
    bad = []
    r = _rows(E.db_path(), "exits", "SELECT key FROM exits WHERE kind = 'send_feishu' AND key LIKE 'send_feishu:card-%'")
    if r is None:
        bad.append("出口账本读不了")
    else:
        bad += [k for (k,) in r if k.rsplit("-", 1)[-1].isdigit() and int(k.rsplit("-", 1)[-1]) > max_card]
    try:
        import autoloop_feishu as F
        r = _rows(F.db_file(), "feishu_cards", "SELECT card_id FROM feishu_cards WHERE card_id > ?", (max_card,))
        bad += ["卡片库读不了"] if r is None else [f"cards.db 卡片 {c}" for (c,) in r]
    except ImportError:
        pass
    return bad


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
    sub.add_parser("leftovers")
    c = sub.add_parser("cards-after")
    c.add_argument("--max-card", type=int, required=True)
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
        elif a.cmd == "leftovers":
            out = leftovers()
        elif a.cmd == "cards-after":
            out = {"cards": cards_after(a.max_card)}
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
