#!/usr/bin/env python3
"""Multica 夜跑主控（DT-ORC M5，#92）：Hermes 用它在 DT 沙盒里建单、按 stage 推进、边跑边导 run-log，
到上限时按 ARCHITECTURE 5.3a 独立停机。纯脚本，不调用模型；agent 崩了它照样能停。

用法：
  multica_night.py plan PLAN.json                     建父单和子单（子单停在 backlog），记为当前夜跑
  multica_night.py start --approved-by 常浩 [--hours 4] [--max-runs 20]
                                                      开跑前核对，记下授权，把第 1 个 stage 移出 backlog
  multica_night.py tick                               Hermes 无模型定时任务每 5 分钟调一次：对账、导出、查上限、推进或停机
  multica_night.py stop --reason 文字                 人工停机（撤销授权），步骤同自动停机
  multica_night.py status                             打印当前夜跑的状态
  multica_night.py report                             早上的摘要：时长、做了什么、出错和处理、GitHub 越界核对、待常浩定的事
  multica_night.py release --by 常浩                  常浩核对过冻结现场后解除当前夜跑（容器要他手动重启）

推进方式（方案甲，常浩 2026-11-08 选定）：父单指派给常浩（成员），Multica 的系统 Child-Done 规则因此只发收件箱通知、
不唤醒任何 agent；一个 stage 的子单都到 in_review / done / cancelled、且这些子单没有在跑的 run，就由本脚本把下一 stage
移出 backlog。今晚不合并，所以不等 done。

停机条件（任一触发）：到时长上限；总 run 数到上限；同一子单连续失败到上限；子单被 agent 标为 blocked；
子单停滞（不在 backlog、没交付、也没有在跑的 run，超过 stall_minutes）；run 记录核不清（命令失败、读不懂、
以前见过的 run 不见了、消息序号有缺口）；父单之外的 issue 在开跑后被改动；家族里出现自建 Wakeup 或启用中的 Autopilot；
父单不再指派给成员；本脚本自己出异常。

停机步骤（5.3a）：禁用家族 issue 上的全部 Wakeup → 暂停启用中的 Autopilot → 取消 5 种非终态 run
→ 没交付的子单改 blocked（--no-start；不在子单上评论，评论会唤醒 agent）、原因写在父单 → 回读。
回读时 run 全部终态、Wakeup 和 Autopilot 都停了才算停机成功；否则冻结：执行计划里的 freeze_cmds（停隔离执行器容器、
去掉自动重启），记 UNRECONCILED，打印告警（Hermes 定时任务把输出发到常浩私聊），之后不再推进、不再重试；
冻结期间每次 tick 发现有 run 在跑就再取消一次并告警，直到常浩 release。

所有计数存在夜跑目录的 state.json，进程或机器重启后从这里恢复，不清零。tick 没事时什么都不打印。

环境变量：DT_NIGHT_ROOT 夜跑根目录（默认 ~/Library/Application Support/DigitalTwin/runs）；DT_MULTICA_CLI 换 multica（自测用）；
DT_GH_CLI 换 gh；DT_ROSTER 名册（开跑前核对仓库标了 sandbox: true）；DT_NOW（ISO 时间）指定现在；DT_NIGHT_READBACK「次数,间隔秒」停机回读（默认 6,5）。
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

MULTICA = os.environ.get("DT_MULTICA_CLI", "multica")
GH = os.environ.get("DT_GH_CLI", "gh")
ROSTER = Path(os.environ.get("DT_ROSTER", "~/Documents/ChatGPT/个人/work/digital-twin/projects.json")).expanduser()
ROOT = Path(os.environ.get("DT_NIGHT_ROOT", "~/Library/Application Support/DigitalTwin/runs")).expanduser()
BJ = dt.timezone(dt.timedelta(hours=8))
UTC = dt.timezone.utc
TIMEOUT = 60
ACTIVE = ("deferred", "queued", "dispatched", "waiting_local_directory", "running")   # 5 种非终态（5.3a）
TERMINAL = ("completed", "failed", "cancelled")
DELIVERED = ("in_review", "done", "cancelled")
WAKEUP_OFF = ("disabled", "expired", "cancelled", "completed", "deleted", "paused")
DEFAULT_LIMITS = {"hours": 4, "max_runs": 20, "max_consecutive_failures": 2, "stall_minutes": 30}
TICK_GAP = dt.timedelta(minutes=15)       # 两次 tick 相隔超过这个就记一笔（多半是睡眠，G-20）


class Unclear(Exception):
    """multica 命令失败、超时或输出读不懂：按 5.3a 一律当作核不清。"""


# ---------- 时间 ----------

def now():
    v = os.environ.get("DT_NOW")
    t = dt.datetime.fromisoformat(v) if v else dt.datetime.now(UTC)
    return t if t.tzinfo else t.replace(tzinfo=BJ)


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def bj(t):
    """页面和消息里的时间一律按北京时间显示（G-02）。"""
    t = parse(t) if isinstance(t, str) else t
    return t.astimezone(BJ).strftime("%m-%d %H:%M") if t else "—"


# ---------- 文件 ----------

def load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise


def dump(p, data):
    p = Path(p)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def append(p, rec):
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def current_id():
    p = ROOT / "night-current"
    return p.read_text(encoding="utf-8").strip() if p.exists() else None


# ---------- 外部命令 ----------

def mc(plan, *args, out_json=True, stdin=None):
    """跑一条 multica 命令，钉死在计划里的工作区（和可选的 profile）上。"""
    cmd = [MULTICA, *args]
    if out_json:
        cmd += ["--output", "json"]
    cmd += ["--workspace-id", plan["workspace_id"]]
    if plan.get("profile"):
        cmd += ["--profile", plan["profile"]]
    what = "multica " + " ".join(a for a in args[:3] if not a.startswith("-"))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, input=stdin)
    except (OSError, subprocess.SubprocessError) as e:
        raise Unclear(f"{what}：跑不起来（{type(e).__name__}）")
    if r.returncode:
        raise Unclear(f"{what}：退出码 {r.returncode}，{(r.stderr or r.stdout).strip()[:300]}")
    if not out_json:
        return r.stdout
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Unclear(f"{what}：输出不是 JSON")


def items(data, key):
    """multica 有的列表直接是数组，有的包在 {key: [...]} 里。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get(key), list):
        return data[key]
    raise Unclear(f"读不懂 {key} 列表的格式")


def wakeup_on(w):
    if w.get("enabled") is False:
        return False
    return str(w.get("status") or "").lower() not in WAKEUP_OFF


# ---------- 夜跑 ----------

class Night:
    def __init__(self, nid):
        self.id = nid
        self.d = ROOT / nid
        self.plan = load(self.d / "plan.json")
        self.state = load(self.d / "state.json", {})
        self.limits = {**DEFAULT_LIMITS, **self.plan.get("limits", {}), **self.state.get("auth", {}).get("limits", {})}

    def save(self):
        dump(self.d / "state.json", self.state)

    def log(self, event, **kw):
        append(self.d / "run-log.jsonl", {"ts": iso(now()), "event": event, **kw})

    def mc(self, *args, **kw):
        return mc(self.plan, *args, **kw)

    # 家族：父单 + 全部子单（key → identifier）
    def children(self):
        return [c for c in self.plan["children"]]

    def ident(self, key):
        return self.state["issues"][key]["identifier"]

    def family(self):
        return ["PARENT"] + [c["key"] for c in self.children()]

    def stage_of(self, key):
        return next(c["stage"] for c in self.children() if c["key"] == key)

    def last_stage(self):
        return max(c["stage"] for c in self.children())

    def stage_keys(self, n):
        return [c["key"] for c in self.children() if c["stage"] == n]


def fill(text, night):
    def sub(m):
        k = m.group(1)
        return night.ident(k) if k in night.state.get("issues", {}) else m.group(0)
    return re.sub(r"\{([A-Z][A-Z0-9_]*)\}", sub, text)


def validate(plan):
    errs = []
    for f in ("id", "workspace_id", "owner_member_id", "agents", "parent", "children"):
        if f not in plan:
            errs.append(f"缺 {f}")
    if errs:
        return errs
    keys, stages = [], set()
    for c in plan["children"]:
        if c.get("key") in keys or c.get("key") == "PARENT" or not re.fullmatch(r"[A-Z][A-Z0-9_]*", c.get("key", "")):
            errs.append(f"子单 key 不合规或重复：{c.get('key')}")
        if c.get("agent") not in plan["agents"]:
            errs.append(f"{c.get('key')} 的 agent 不在 agents 里：{c.get('agent')}")
        for ref in re.findall(r"\{([A-Z][A-Z0-9_]*)\}", c.get("description", "")):
            earlier = [x["key"] for x in plan["children"] if x["stage"] < c["stage"]]
            if ref != "PARENT" and ref not in earlier:
                errs.append(f"{c['key']} 引用了不在更早 stage 的 {{{ref}}}")
        keys.append(c.get("key"))
        stages.add(c.get("stage"))
    if stages != set(range(1, len(stages) + 1)):
        errs.append("stage 要从 1 开始连续编号")
    return errs


# ---------- plan ----------

def cmd_plan(path):
    plan = load(path)
    errs = validate(plan)
    if errs:
        print("计划不合规，没建单：" + "；".join(errs))
        return 1
    d = ROOT / plan["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "messages").mkdir(exist_ok=True)
    night_state = load(d / "state.json", {})
    if night_state.get("auth"):
        print(f"夜跑 {plan['id']} 已经开跑过，不再建单。")
        return 1
    if (d / "plan.json").exists() and night_state.get("issues") and load(d / "plan.json") != plan:
        print(f"夜跑 {plan['id']} 已按旧计划建过单，计划又改了：先人工核对，不覆盖。")
        return 1
    dump(d / "plan.json", plan)
    night = Night(plan["id"])
    issues = night.state.setdefault("issues", {})
    try:
        if "PARENT" not in issues:
            p = night.mc("issue", "create", "--title", plan["parent"]["title"], "--description-stdin",
                         "--assignee-id", plan["owner_member_id"], "--status", "in_progress",
                         *(["--project", plan["project_id"]] if plan.get("project_id") else []),
                         stdin=plan["parent"]["description"])
            issues["PARENT"] = {"identifier": p["identifier"], "id": p["id"]}
            night.save()
        for c in sorted(night.children(), key=lambda c: c["stage"]):
            if c["key"] in issues:
                continue
            desc = fill(c["description"], night) + ("\n\n" + plan["common"] if plan.get("common") else "")
            r = night.mc("issue", "create", "--title", fill(c["title"], night), "--description-stdin",
                         "--parent", issues["PARENT"]["id"], "--stage", str(c["stage"]),
                         "--assignee-id", plan["agents"][c["agent"]], "--status", "backlog",
                         *(["--project", plan["project_id"]] if plan.get("project_id") else []),
                         stdin=desc)
            issues[c["key"]] = {"identifier": r["identifier"], "id": r["id"]}
            night.save()
    except Unclear as e:
        print(f"建单中途失败：{e}。已建的单记在 {d / 'state.json'}，核对后再跑一次 plan 会接着建。")
        return 1
    (ROOT / "night-current").write_text(plan["id"], encoding="utf-8")
    night.log("plan", issues={k: v["identifier"] for k, v in issues.items()})
    lines = [f"夜跑 {plan['id']} 已建单，子单全部停在 backlog，等常浩批准后 start：",
             f"- 父单 {issues['PARENT']['identifier']}（指派给常浩）"]
    for n in range(1, night.last_stage() + 1):
        lines.append(f"- stage {n}：" + "、".join(f"{night.ident(k)}（{c['agent']}）" for k in night.stage_keys(n)
                                                 for c in night.children() if c["key"] == k))
    print("\n".join(lines))
    return 0


# ---------- 对账 ----------

def collect(night):
    """读家族的 issue、run、Wakeup，工作区的 Autopilot；边读边导出 run-log 和消息。有任何核不清就抛 Unclear。"""
    st = night.state
    seen = st.setdefault("runs", {})
    snap = {"issues": {}, "runs": {}, "wakeups": {}, "autopilots": []}
    for key in night.family():
        ident = night.ident(key)
        snap["issues"][key] = night.mc("issue", "get", ident)
        snap["runs"][key] = items(night.mc("issue", "runs", ident), "runs")
        snap["wakeups"][key] = items(night.mc("issue", "wakeup", "list", ident), "wakeups")
    snap["autopilots"] = items(night.mc("autopilot", "list"), "autopilots")
    present = set()
    for key, runs in snap["runs"].items():
        for r in runs:
            rid = r["id"]
            present.add(rid)
            rec = seen.setdefault(rid, {"issue": key, "status": None, "last_seq": 0, "logged": False,
                                        "created_at": r.get("created_at"), "gap": False})
            rec["status"] = r.get("status")
            if rec["logged"]:
                continue
            export_messages(night, rid, rec)
            if r.get("status") in TERMINAL:
                export_messages(night, rid, rec)      # 终态后再取一次，收齐最后几条
                write_run(night, key, snap["issues"][key], r, rec)
                rec["logged"] = True
    missing = [rid for rid in seen if rid not in present]
    night.save()
    if missing:
        raise Unclear(f"以前见过的 {len(missing)} 个 run 在记录里不见了（{', '.join(m[:8] for m in missing[:3])}）")
    gaps = [rid for rid, rec in seen.items() if rec.get("gap")]
    if gaps:
        raise Unclear(f"run 消息序号有缺口（{', '.join(g[:8] for g in gaps[:3])}）")
    return snap


def export_messages(night, rid, rec):
    msgs = night.mc("issue", "run-messages", rid, "--since", str(rec["last_seq"]))
    msgs = items(msgs, "messages")
    p = night.d / "messages" / f"{rid}.jsonl"
    for m in sorted(msgs, key=lambda m: m.get("seq", 0)):
        seq = m.get("seq")
        if not isinstance(seq, int) or seq <= rec["last_seq"]:
            continue
        if seq != rec["last_seq"] + 1:
            rec["gap"] = True
        append(p, m)
        rec["last_seq"] = seq


def write_run(night, key, issue, r, rec):
    res = r.get("result") or {}
    init = ((r.get("attribution") or {}).get("initiator") or {}).get("name")
    s, c = parse(r.get("started_at")), parse(r.get("completed_at"))
    append(night.d / "run-log.jsonl", {
        "ts": iso(now()), "event": "run", "parent_issue": night.ident("PARENT"),
        "issue": night.ident(key) if key != "PARENT" else night.ident("PARENT"),
        "stage": night.stage_of(key) if key != "PARENT" else None, "issue_status": issue.get("status"),
        "run_id": r["id"], "agent_id": r.get("agent_id"), "status": r.get("status"), "attempt": r.get("attempt"),
        "initiator": init, "created_at": r.get("created_at"), "started_at": r.get("started_at"),
        "completed_at": r.get("completed_at"), "duration_s": int((c - s).total_seconds()) if s and c else None,
        "error": r.get("error"), "pr_url": res.get("pr_url") or "", "summary": (res.get("output") or "")[:1500],
        "message_count": rec["last_seq"], "messages_ref": f"messages/{r['id']}.jsonl", "incomplete": bool(rec.get("gap")),
    })


def fail_streak(night, key):
    """同一子单从最近一次往前数的连续失败次数，按持久记录算（重启后不清零）。"""
    runs = sorted((v for v in night.state.get("runs", {}).values() if v["issue"] == key),
                  key=lambda v: v.get("created_at") or "")
    n = 0
    for v in reversed(runs):
        if v["status"] == "failed":
            n += 1
        elif v["status"] in TERMINAL:
            break
    return n


def active_runs(snap, keys=None):
    return [(k, r) for k, runs in snap["runs"].items() if keys is None or k in keys
            for r in runs if r.get("status") in ACTIVE]


def out_of_family(night, t):
    """开跑后被改动过的、不在本次家族里的 issue。"""
    start = st_start(night)
    fam = {v["id"] for v in night.state["issues"].values()}
    found, offset = [], 0
    while True:
        data = night.mc("issue", "list", "--updated-since", iso(start), "--updated-until", iso(t + dt.timedelta(minutes=1)),
                        "--limit", "100", "--offset", str(offset))
        page = items(data, "issues")
        found += [i.get("identifier") or i.get("id") for i in page if i.get("id") not in fam]
        if not (isinstance(data, dict) and data.get("has_more")) or not page:
            return found
        offset += len(page)


def st_start(night):
    return parse(night.state["auth"]["started_at"])


def violations(night, snap, t):
    lim, st, why = night.limits, night.state, []
    if t >= parse(st["auth"]["deadline"]):
        why.append(f"到达时长上限（{lim['hours']} 小时）")
    if len(st.get("runs", {})) >= lim["max_runs"]:
        why.append(f"run 总数达到上限 {lim['max_runs']}")
    for c in night.children():
        k = c["key"]
        n = fail_streak(night, k)
        if n >= lim["max_consecutive_failures"]:
            why.append(f"{night.ident(k)} 连续失败 {n} 次")
        if snap["issues"][k].get("status") == "blocked":
            why.append(f"{night.ident(k)} 被 agent 标为 blocked")
    for k, ws in snap["wakeups"].items():
        if any(wakeup_on(w) for w in ws):
            why.append(f"{night.ident(k)} 上出现了未授权的 Wakeup")
    if any(str(a.get("status")).lower() == "active" for a in snap["autopilots"]):
        why.append("工作区里有启用中的 Autopilot")
    if snap["issues"]["PARENT"].get("assignee_type") != "member":
        why.append("父单不再指派给成员（Child-Done 会唤醒 agent）")
    other = out_of_family(night, t)
    if other:
        why.append("父单之外的 issue 在开跑后被改动：" + "、".join(other[:5]))
    # 停滞：已移出 backlog、没交付、没有在跑的 run，超过 stall_minutes
    stall = dt.timedelta(minutes=lim["stall_minutes"])
    for k in night.stage_keys(st.get("stage", 0)):
        status = snap["issues"][k].get("status")
        if status in DELIVERED or status == "backlog" or active_runs(snap, [k]):
            continue
        stamps = [parse(r.get("completed_at") or r.get("created_at")) for r in snap["runs"][k]]
        last = max([s for s in stamps if s] + [parse(st["advanced"][str(st["stage"])])])
        if t - last >= stall:
            why.append(f"{night.ident(k)} 停滞：{status}，{int((t - last).total_seconds() // 60)} 分钟没有在跑的 run，也没交付")
    return why


# ---------- 推进与停机 ----------

def advance(night, n, t):
    """把第 n 个 stage 的子单移出 backlog。先记账再动手；命令结果不明就抛 Unclear，由调用方停机，不重发。"""
    st = night.state
    st["stage"] = n
    st.setdefault("advanced", {})[str(n)] = iso(t)
    night.save()
    moved = []
    for k in night.stage_keys(n):
        cur = night.mc("issue", "get", night.ident(k)).get("status")
        if cur != "backlog":
            continue
        night.mc("issue", "status", night.ident(k), "todo")
        moved.append(night.ident(k))
    night.log("advance", stage=n, issues=moved)
    return moved


def stop(night, reasons, finished=False):
    """5.3a 停机。返回要打印的文字；没停干净就冻结。"""
    st, errs = night.state, []
    st["stopping"] = {"at": iso(now()), "reasons": reasons}
    night.save()
    night.log("stop_begin", reasons=reasons, finished=finished)
    fam = night.family()

    def safe(desc, *args, **kw):
        try:
            return night.mc(*args, **kw)
        except Unclear as e:
            errs.append(f"{desc}：{e}")

    for k in fam:   # ① 禁用 Wakeup
        ws = safe(f"读 {night.ident(k)} 的 Wakeup", "issue", "wakeup", "list", night.ident(k))
        for w in (items(ws, "wakeups") if ws is not None else []):
            if wakeup_on(w):
                safe(f"禁用 Wakeup {w.get('id')}", "issue", "wakeup", "disable", night.ident(k), w["id"])
    aps = safe("读 Autopilot", "autopilot", "list")          # ② 暂停 Autopilot
    for a in (items(aps, "autopilots") if aps is not None else []):
        if str(a.get("status")).lower() == "active":
            safe(f"暂停 Autopilot {a.get('id')}", "autopilot", "update", a["id"], "--status", "paused")
    for k in fam:   # ③ 取消非终态 run
        runs = safe(f"读 {night.ident(k)} 的 run", "issue", "runs", night.ident(k))
        for r in (items(runs, "runs") if runs is not None else []):
            if r.get("status") in ACTIVE:
                safe(f"取消 run {r['id'][:8]}", "issue", "cancel-task", r["id"])
    if not finished:   # ④ 没交付的子单改 blocked，原因写在父单
        for k in night.stage_keys(st.get("stage", 0)):
            iss = safe(f"读 {night.ident(k)}", "issue", "get", night.ident(k))
            if iss and iss.get("status") not in DELIVERED + ("backlog", "blocked"):
                safe(f"把 {night.ident(k)} 改为 blocked", "issue", "status", night.ident(k), "blocked", "--no-start")
        safe("在父单写停机原因", "issue", "comment", "add", night.ident("PARENT"), "--content-stdin",
             stdin="夜跑主控停机：" + "；".join(reasons) + "。后面的 stage 留在 backlog，不再推进。")
    ok, left = readback(night, errs)   # ⑤ 回读
    night.log("stop_readback", ok=ok, left=left, errors=errs)
    if ok:
        st["outcome"] = "FINISHED" if finished else "STOPPED"
        st["ended_at"] = iso(now())
        st.pop("stopping", None)
        night.save()
        night.log("stopped", outcome=st["outcome"], reasons=reasons)
        head = "夜跑全部 stage 已交付，触发源都已关停（回读确认）" if finished else "夜跑已停机（回读确认：run 全部终态，Wakeup、Autopilot 都已停）"
        return f"{head}。原因：{'；'.join(reasons)}。"
    return freeze(night, reasons, left + errs)


def readback(night, errs):
    tries, gap = (int(x) for x in os.environ.get("DT_NIGHT_READBACK", "6,5").split(","))
    left = []
    for i in range(max(tries, 1)):
        left = []
        try:
            for k in night.family():
                for r in items(night.mc("issue", "runs", night.ident(k)), "runs"):
                    if r.get("status") not in TERMINAL:
                        left.append(f"{night.ident(k)} 的 run {r['id'][:8]} 仍是 {r.get('status')}")
                if any(wakeup_on(w) for w in items(night.mc("issue", "wakeup", "list", night.ident(k)), "wakeups")):
                    left.append(f"{night.ident(k)} 上仍有启用中的 Wakeup")
            if any(str(a.get("status")).lower() == "active" for a in items(night.mc("autopilot", "list"), "autopilots")):
                left.append("仍有启用中的 Autopilot")
        except Unclear as e:
            left.append(f"回读失败：{e}")
        if not left:
            return True, []
        if i + 1 < tries:
            time.sleep(gap)
    return False, left


def freeze(night, reasons, problems):
    st, results = night.state, []
    for cmd in night.plan.get("freeze_cmds", []):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
            results.append({"cmd": cmd, "code": r.returncode, "out": (r.stderr or r.stdout).strip()[:200]})
        except (OSError, subprocess.SubprocessError) as e:
            results.append({"cmd": cmd, "code": None, "out": type(e).__name__})
    froze_ok = bool(results) and all(r["code"] == 0 for r in results)
    st.update(frozen=True, outcome="UNRECONCILED", ended_at=iso(now()))
    st.pop("stopping", None)
    night.save()
    night.log("freeze", reasons=reasons, problems=problems, commands=results)
    lines = ["⚠️ 夜跑停机没有回读通过，已冻结（UNRECONCILED），不会再推进或重试。",
             f"停机原因：{'；'.join(reasons)}", "没停干净的：" + "；".join(problems[:6])]
    lines.append("已停隔离执行器容器并去掉自动重启。" if froze_ok else
                 "⚠️ 冻结命令没全部成功，请尽快手动停容器：" + "；".join(" ".join(r["cmd"]) for r in results if r["code"] != 0)
                 if results else "⚠️ 计划里没有 freeze_cmds，容器没停，请尽快手动停。")
    lines.append(f"核对后：手动恢复容器，再跑 multica_night.py release --by 常浩。记录在 {night.d}")
    return "\n".join(lines)


# ---------- 命令 ----------

def roster_gate(plan):
    """5.3：无人值守只在名册里标了 sandbox: true、且登记了同一个 Multica 工作区的项目里跑。"""
    try:
        d = load(ROSTER)
    except (OSError, ValueError) as e:
        return f"读不了名册（{type(e).__name__}）"
    want = "github:" + str(plan.get("repo"))
    for e in d.get("projects", []) + d.get("inventory", []):
        if want in (e.get("path"), "github:" + str(e.get("github"))):
            if e.get("sandbox") is True and e.get("multica_workspace") == plan["workspace_id"]:
                return None
            return f"名册里的 {plan.get('repo')} 没标 sandbox: true 或工作区不符"
    return f"名册里没有 {plan.get('repo')}"


def preflight(night):
    gate = roster_gate(night.plan)
    why = [gate] if gate else []
    snap = {"runs": {}, "wakeups": {}}
    for k in night.family():
        iss = night.mc("issue", "get", night.ident(k))
        if k == "PARENT" and iss.get("assignee_type") != "member":
            why.append("父单要指派给成员（常浩）")
        if k != "PARENT" and iss.get("status") != "backlog":
            why.append(f"{night.ident(k)} 不在 backlog（{iss.get('status')}）")
        snap["runs"][k] = items(night.mc("issue", "runs", night.ident(k)), "runs")
        if any(wakeup_on(w) for w in items(night.mc("issue", "wakeup", "list", night.ident(k)), "wakeups")):
            why.append(f"{night.ident(k)} 上有启用中的 Wakeup")
    if active_runs(snap):
        why.append("家族里已有在跑的 run")
    if any(str(a.get("status")).lower() == "active" for a in items(night.mc("autopilot", "list"), "autopilots")):
        why.append("工作区里有启用中的 Autopilot")
    return why


def cmd_start(night, by, hours, max_runs):
    st = night.state
    if st.get("auth"):
        print("这次夜跑已经开跑过，不能再 start。")
        return 1
    try:
        why = preflight(night)
    except Unclear as e:
        why = [f"核对失败：{e}"]
    if why:
        print("开跑前核对没通过，什么都没动：" + "；".join(why))
        return 1
    t = now()
    limits = {**night.limits, **({"hours": hours} if hours else {}), **({"max_runs": max_runs} if max_runs else {})}
    st["auth"] = {"approved_by": by, "started_at": iso(t), "deadline": iso(t + dt.timedelta(hours=limits["hours"])),
                  "limits": limits, "mode": "UNATTENDED"}
    st["last_tick"] = iso(t)
    night.limits = limits
    night.save()
    night.log("start", approved_by=by, deadline=st["auth"]["deadline"], limits=limits)
    try:
        moved = advance(night, 1, t)
    except Unclear as e:
        print(stop(night, [f"开跑第 1 个 stage 时结果不明：{e}"]))
        return 1
    print(f"夜跑 {night.id} 已开跑（{by} 批准，最晚 {bj(st['auth']['deadline'])} 停）。stage 1：{'、'.join(moved)}。"
          f"上限：{limits['hours']} 小时、run {limits['max_runs']} 次、同一子单连续失败 {limits['max_consecutive_failures']} 次、"
          f"停滞 {limits['stall_minutes']} 分钟。")
    return 0


def guard_frozen(night):
    """冻结期间：发现有 run 在跑（比如容器被重启了）就再取消一次并告警，绝不推进。"""
    left = []
    for k in night.family():
        for r in items(night.mc("issue", "runs", night.ident(k)), "runs"):
            if r.get("status") in ACTIVE:
                try:
                    night.mc("issue", "cancel-task", r["id"])
                    left.append(f"{night.ident(k)} 的 run {r['id'][:8]}（{r.get('status')}，已再次取消）")
                except Unclear as e:
                    left.append(f"{night.ident(k)} 的 run {r['id'][:8]}（{r.get('status')}，取消失败：{e}）")
    if left:
        night.log("frozen_guard", runs=left)
        return "⚠️ 夜跑处于冻结状态，却发现有 run 在跑：" + "；".join(left) + "。请核对容器是不是被重启了。"
    return ""


def cmd_tick(night):
    st, t = night.state, now()
    if not st.get("auth"):
        return 0, ""
    if st.get("frozen"):
        try:
            return 0, guard_frozen(night)
        except Unclear as e:
            return 1, f"⚠️ 夜跑冻结中，核对 run 失败：{e}"
    if st.get("outcome"):
        return 0, ""
    out = []
    last = parse(st.get("last_tick"))
    if last and t - last > TICK_GAP:
        mins = int((t - last).total_seconds() // 60)
        night.log("tick_gap", minutes=mins, since=st["last_tick"])
        out.append(f"注意：两次检查相隔 {mins} 分钟（{bj(last)} → {bj(t)}），可能电脑睡眠或定时任务没按时跑。")
    st["last_tick"] = iso(t)
    st["ticks"] = st.get("ticks", 0) + 1
    night.save()
    if st.get("stopping"):   # 上次停机做到一半（进程被杀）：接着停，不推进
        out.append(stop(night, st["stopping"]["reasons"] + ["上次停机没做完，接着做"]))
        return 0, "\n".join(out)
    try:
        snap = collect(night)
        why = violations(night, snap, t)
    except Unclear as e:
        why = [f"UNRECONCILED：{e}"]
    if why:
        out.append(stop(night, why))
        return (1 if night.state.get("frozen") else 0), "\n".join(out)
    n = st["stage"]
    keys = night.stage_keys(n)
    if all(snap["issues"][k].get("status") in DELIVERED for k in keys) and not active_runs(snap, keys):
        if n < night.last_stage():
            try:
                moved = advance(night, n + 1, t)
            except Unclear as e:
                out.append(stop(night, [f"推进 stage {n + 1} 时结果不明：{e}"]))
                return (1 if night.state.get("frozen") else 0), "\n".join(out)
            out.append(f"夜跑 stage {n} 已交付（{'、'.join(night.ident(k) for k in keys)}），进入 stage {n + 1}：{'、'.join(moved)}。")
        else:
            out.append(stop(night, [f"stage 1–{n} 全部交付"], finished=True))
    return (1 if night.state.get("frozen") else 0), "\n".join(out)


def cmd_status(night):
    st, lim = night.state, night.limits
    if not st.get("auth"):
        print(f"夜跑 {night.id}：已建单，还没开跑。")
        return 0
    runs = st.get("runs", {})
    by = {}
    for v in runs.values():
        by[v["status"]] = by.get(v["status"], 0) + 1
    lines = [f"夜跑 {night.id}：{st.get('outcome') or '进行中'}{'（已冻结）' if st.get('frozen') else ''}",
             f"- 开跑 {bj(st['auth']['started_at'])}，最晚 {bj(st['auth']['deadline'])} 停；当前 stage {st.get('stage')} / {night.last_stage()}",
             f"- run {len(runs)} / {lim['max_runs']} 次：" + "，".join(f"{k} {v}" for k, v in sorted(by.items())),
             f"- 最近一次检查 {bj(st.get('last_tick'))}，共 {st.get('ticks', 0)} 次"]
    print("\n".join(lines))
    return 0


def gh_audit(night):
    """GitHub 越界核对：远端只该有 main 和 agent/ 分支；开跑后不该有合并。读不到就如实写未核对。"""
    repo, start = night.plan.get("repo"), night.state["auth"]["started_at"]
    if not repo:
        return ["GitHub：计划里没写 repo，未核对"], []
    try:
        br = subprocess.run([GH, "api", f"repos/{repo}/branches", "--paginate", "--jq", ".[].name"],
                            capture_output=True, text=True, timeout=TIMEOUT)
        prs = subprocess.run([GH, "pr", "list", "--repo", repo, "--state", "all", "--limit", "100", "--json",
                              "number,title,headRefName,url,state,createdAt,mergedAt"],
                             capture_output=True, text=True, timeout=TIMEOUT)
        if br.returncode or prs.returncode:
            raise ValueError((br.stderr or prs.stderr).strip()[:200])
        branches = br.stdout.split()
        pulls = json.loads(prs.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return [f"GitHub：读不到，未核对（{e}）"], []
    bad = [b for b in branches if b != "main" and not b.startswith("agent/")]
    merged = [p for p in pulls if p.get("mergedAt") and p["mergedAt"] >= start]
    new = [p for p in pulls if p.get("createdAt", "") >= start]
    lines = ["GitHub：" + ("远端只有 main 和 agent/ 分支" if not bad else "⚠️ 出现非 agent/ 分支：" + "、".join(bad)) + "；" +
             ("开跑后没有合并" if not merged else "⚠️ 开跑后有合并：" + "、".join(f"#{p['number']}" for p in merged))]
    return lines, new


def cmd_report(night):
    st = night.state
    if not st.get("auth"):
        print(f"夜跑 {night.id} 还没开跑。")
        return 0
    recs = [json.loads(x) for x in (night.d / "run-log.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    runs = [r for r in recs if r.get("event") == "run"]
    secs = sum(r["duration_s"] or 0 for r in runs)
    start, end = parse(st["auth"]["started_at"]), parse(st.get("ended_at")) or now()
    stopped = [r for r in recs if r.get("event") in ("stopped", "freeze")]
    gaps = [r for r in recs if r.get("event") == "tick_gap"]
    lines = [f"夜跑 {night.id} · {st.get('outcome') or '进行中'}",
             f"时长：{bj(start)} → {bj(end)}（墙钟 {int((end - start).total_seconds() // 60)} 分钟，agent 实跑 {secs // 60} 分钟）",
             f"run：{len(runs)} 次，失败 {sum(r['status'] == 'failed' for r in runs)}、取消 {sum(r['status'] == 'cancelled' for r in runs)}，"
             f"记录不完整 {sum(bool(r.get('incomplete')) for r in runs)}",
             f"推进：到 stage {st.get('stage')} / {night.last_stage()}"]
    for r in stopped:
        lines.append(f"停机：{r.get('outcome') or 'UNRECONCILED'}，原因 {'；'.join(r.get('reasons', []))}")
    if gaps:
        lines.append(f"检查间隔异常 {len(gaps)} 次（最长 {max(g['minutes'] for g in gaps)} 分钟）")
    audit, new = gh_audit(night)
    lines += audit
    if new:
        lines.append("待你看的 PR：" + "；".join(f"#{p['number']} {p['title']}（{p['state']}）" for p in new))
    print("\n".join(lines))
    return 0


def cmd_release(night, by):
    if not night.state.get("outcome"):
        print("夜跑还在进行，先 stop 再 release。")
        return 1
    night.state["released"] = {"by": by, "at": iso(now())}
    night.save()
    night.log("release", by=by)
    cur = ROOT / "night-current"
    if current_id() == night.id:
        cur.unlink()
    print(f"夜跑 {night.id} 已解除，定时任务不再管它。" + ("容器若已冻结，由你手动恢复。" if night.state.get("frozen") else ""))
    return 0


def main(argv=None):
    a = argparse.ArgumentParser(description="Multica 夜跑主控（DT-ORC M5）")
    sub = a.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("file")
    s = sub.add_parser("start")
    s.add_argument("--approved-by", required=True)
    s.add_argument("--hours", type=float)
    s.add_argument("--max-runs", type=int)
    sub.add_parser("tick")
    x = sub.add_parser("stop")
    x.add_argument("--reason", required=True)
    sub.add_parser("status")
    sub.add_parser("report")
    r = sub.add_parser("release")
    r.add_argument("--by", required=True)
    for q in (s, x, r, sub.choices["tick"], sub.choices["status"], sub.choices["report"]):
        q.add_argument("--night", help="夜跑 id；默认当前夜跑")
    a = a.parse_args(argv)
    if a.cmd == "plan":
        return cmd_plan(a.file)
    nid = a.night or current_id()
    if not nid or not (ROOT / nid / "plan.json").exists():
        if a.cmd == "tick":
            return 0
        print("没有当前夜跑。")
        return 1
    ROOT.mkdir(parents=True, exist_ok=True)
    lock = open(ROOT / nid / "tick.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if a.cmd == "tick" else 0))
    except BlockingIOError:
        return 0          # 上一次 tick 还没跑完（比如在停机回读），这次跳过
    night = Night(nid)
    if a.cmd == "tick":
        try:
            code, text = cmd_tick(night)
        except Exception as e:     # 主控自己出错也要停（5.3a：不依赖任何一方“还活着”）
            night = Night(nid)
            night.log("crash", error=traceback.format_exc()[-1500:])
            try:
                if night.state.get("auth") and not night.state.get("outcome"):
                    text = stop(night, [f"主控脚本异常：{type(e).__name__}: {e}"])
                else:
                    text = f"⚠️ 夜跑主控异常：{type(e).__name__}: {e}"
            except Exception as e2:
                text = f"⚠️ 夜跑主控异常，停机也失败了，请立刻手动停容器：{type(e2).__name__}: {e2}"
            code = 1
        if text:
            print(text)
        return code
    if a.cmd == "start":
        return cmd_start(night, a.approved_by, a.hours, a.max_runs)
    if a.cmd == "stop":
        if not night.state.get("auth") or night.state.get("outcome"):
            print("夜跑没在进行，不用停。")
            return 1
        text = stop(night, [f"人工停机：{a.reason}"])
        print(text)
        return 1 if night.state.get("frozen") else 0
    if a.cmd == "status":
        return cmd_status(night)
    if a.cmd == "report":
        return cmd_report(night)
    return cmd_release(night, a.by)


if __name__ == "__main__":
    sys.exit(main())
