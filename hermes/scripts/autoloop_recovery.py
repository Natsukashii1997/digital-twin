#!/usr/bin/env python3
"""恢复闸：先对账、再放行受保护副作用（批 0D 恢复门，#195；方案 docs/agent-autoloop-底座-方案.md 5.3「恢复顺序」、
5.5 不变量 3；ADR-015 生效顺序第 3 条；设计 docs/恢复门-0D-设计.md）。

中台自己会恢复接单、排队和重试，「恢复轮次 ≠ 恢复外部写操作」。所以每个受保护副作用的入口（受信发布、合并、发飞书卡片、
回答卡片、往中台发派单）动手前都过 ensure_open()：

**闸什么时候关**（任一变化都关，记的「纪元」对不上就关）：
  - 开机（kern.boottime 变了；Linux 上读 boot_id）、睡醒（kern.waketime 变了）；
  - 中台重启或换版本（中台 up.pid 的进程号或进程启动时间变了、/healthz 的提交号变了）；中台连不上也算关着；
  - 任何账本里出现「结果不明」（出口账本 exits、本人点击 claims 的 unknown）——断网、超时、强杀都会走到这里。

**对账顺序**（reconcile；走完才开闸，无法确认的停下并报告，从不重做）：
  1. 本机账本：出口账本里「在做、但做的进程已经不在」的条目改成结果不明（强杀、断电、睡死）；
  2. 凭证与镜像：按凭证库已提交的状态重写工人只读镜像，并回读核对（任务、状态、代次、作废代次）；对不上不开闸；
  3. 中台：/healthz 通、提交号等于锁定的版本（hub-pin.json，升级流程验证通过后才改）；不是就不开闸；
  4. 外部回读：出口账本里结果不明的逐条只读回读 GitHub / 飞书；本人点击里结果不明、在途过久的只读回读中台卡片；
     连不上的留着下次，对不上的记「核不清」交常浩；
  5. 结算开闸：记下这次的纪元和报告。仍冻结的范围（结果不明、核不清）各自继续拒绝，别的照常放行。
派单事件（autoloop_grants 的 hub_events）不在这里补发：中台按同一来源同一 id 去重，开闸后照常 send-pending 补发。

**按「将来在 dt-gate 里运行」设计**（ADR-015）：闸的状态和出口账本在同一个库（运行账号的 DigitalTwin/autoloop/exits.db），
凭证库、卡片库、中台目录、锁定版本文件都从运行账号的家目录或环境变量取，不写死主账号路径；本脚本不需要任何钥匙
（中台只读 /healthz，回读卡片用的是 autoloop_answer 现有的只读窗口）。

  autoloop_recovery.py status [--json]          只读：闸开没开、为什么、冻结了哪些范围（工程自检以后接这个）
  autoloop_recovery.py reconcile                手动对账一次（入口平时会自己调）
  autoloop_recovery.py pin --commit <40 位> --confirm   锁定中台版本（安装 / 升级验证通过后，常浩说了才跑）
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
    """中台进程：up.pid 里的进程号 + 进程启动时间（进程号会被复用，只看号不够）。读不到返回 '?'。"""
    try:
        pid = int((hub_home() / "up.pid").read_text().strip().split()[0])
    except (OSError, ValueError, IndexError):
        return "?"
    return f"{pid}@{E.proc_start(pid) or 'gone'}"


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
    """入口共用：闸开着就放行；关着就先对账（同一把锁串行），对完还关着就拒绝（Refused，说清原因）。"""
    try:
        epoch = current_epoch()
    except HubDown as e:
        raise E.Refused(f"恢复闸关着：{e}")
    if closed_reason(gate_row(), epoch) is None:
        return
    with recovery_lock():
        if closed_reason(gate_row(), current_epoch()) is None:   # 等锁期间别人已经对完
            return
        rep = reconcile(readers)
    if not rep["open"]:
        raise E.Refused(f"恢复闸关着：{rep['why']}")


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


def reconcile(readers=None):
    """按顺序对账一次，开闸或说明为什么不开。不做任何外部写（只重写本机的工人镜像）。"""
    steps, why = [], None
    seen = (gate_row() or {}).get("dirty", 0)                             # 只清这个数，对账期间新出的不明留到下次
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
    steps.append({"step": "外部回读", "exits": exits, "answers": answers})
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
    return {"open": why is None, "why": why, "frozen": frozen, "steps": steps}


def status():
    """只读：闸、原因、冻结的范围、上次对账报告。"""
    row = gate_row()
    try:
        reason = closed_reason(row, current_epoch())
    except HubDown as e:
        reason = str(e)
    with E.db(write=False) as con:
        frozen = E.frozen_scopes(con)
    return {"open": reason is None, "why": reason, "frozen": frozen, "pinned": pinned(),
            "opened_at": (row or {}).get("opened_at"), "last_report": json.loads((row or {}).get("report") or "null")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").add_argument("--json", action="store_true")
    sub.add_parser("reconcile")
    p = sub.add_parser("pin")
    p.add_argument("--commit", required=True)
    p.add_argument("--confirm", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "status":
            out = status()
        elif a.cmd == "reconcile":
            with recovery_lock():
                out = reconcile()
        else:
            out = pin(a.commit, a.confirm)
    except E.Refused as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if out.get("open", True) else 2


if __name__ == "__main__":
    sys.exit(main())
