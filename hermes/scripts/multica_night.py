#!/usr/bin/env python3
"""Multica 夜跑主控（DT-ORC M5，#92）：Hermes 用它在 DT 沙盒里建单、按 stage 推进、边跑边导 run-log，
到上限时按 ARCHITECTURE 5.3a 独立停机。纯脚本，不调用模型；agent 崩了它照样能停。

用法：
  multica_night.py plan PLAN.json                     建父单和子单（子单停在 backlog），记为当前夜跑
  multica_night.py start --approved-by 常浩 [--hours 4] [--max-runs 20]
                                                      开跑前核对，记下授权，把第 1 个 stage 移出 backlog
  multica_night.py approve --by Hermes --start-at 2026-11-08T22:00 [--hours 4] [--max-runs 20]
                                                      记下“几点开跑”的批准，到点由 tick 执行 start（过了 30 分钟还没开就不开）
  multica_night.py tick                               Hermes 无模型定时任务每 5 分钟调一次（经 multica_night_tick.py，定时任务不能带参数）：
                                                      到点开跑；对账、导出、查上限、推进或停机
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
DELIVERED = ("in_review", "done", "cancelled")   # 停机时这些子单不改 blocked
STAGE_DONE = ("in_review", "done")                # 推进下一 stage 只认这两种；子单被取消算违规（不自动放行）
WAKEUP_OFF = ("disabled", "expired", "cancelled", "completed", "deleted", "paused")
DEFAULT_LIMITS = {"hours": 4, "max_runs": 20, "max_consecutive_failures": 2, "stall_minutes": 30}
TICK_GAP = dt.timedelta(minutes=15)       # 两次 tick 相隔超过这个就记一笔（多半是睡眠，G-20）


class Unclear(Exception):
    """multica 命令失败、超时或输出读不懂：按 5.3a 一律当作核不清。"""


# ---------- 时间 ----------

def now():
    v = os.environ.get("DT_NOW")
    t = parse(v) if v else dt.datetime.now(UTC)
    return t if t.tzinfo else t.replace(tzinfo=BJ)


def iso(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    """Hermes 用系统自带的 Python 3.9 跑脚本，它的 fromisoformat 不认结尾的 Z，也不认 6 位以外的小数秒。"""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)\.(\d+)(.*)$", s)
    if m:
        s = f"{m.group(1)}.{(m.group(2) + '000000')[:6]}{m.group(3)}"
    return dt.datetime.fromisoformat(s)


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
    cur = current_id()
    if cur and cur != plan["id"] and (ROOT / cur / "state.json").exists():
        old = load(ROOT / cur / "state.json")
        pending = (old.get("approval") or {}) and not old["approval"].get("result")
        if (old.get("auth") or pending) and not old.get("released"):
            print(f"当前夜跑 {cur} 已开跑或已批准开跑、还没 release：不能换成新的夜跑，否则它没人看守。")
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

def collect(night, full=False):
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
                if full:     # 最后一次导出：已记账的 run 也要重新核对消息文件和远端消息（第 3 轮 P0-01）
                    export_messages(night, rid, rec)
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


def file_seqs(p):
    """导出文件里已有的序号。读不懂的行记为 None（证据异常）。"""
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                out.append(None)     # 空行也算读不懂（第 3 轮 P2-01）
                continue
            try:
                seq = json.loads(line).get("seq")
            except (ValueError, AttributeError):
                seq = None
            out.append(seq if isinstance(seq, int) and not isinstance(seq, bool) else None)
    return out


def export_messages(night, rid, rec):
    p = night.d / "messages" / f"{rid}.jsonl"
    have = file_seqs(p)
    # 追加后、存状态前进程被杀：按文件里已有的序号去重；同时核对文件本身从 1 连续，不只看最大值（第 2 轮 P2-01）
    if have and (None in have or have != list(range(1, len(have) + 1))):
        rec["gap"] = True
    rec["last_seq"] = max([rec["last_seq"]] + [s for s in have if s is not None])
    msgs = items(night.mc("issue", "run-messages", rid, "--since", str(rec["last_seq"])), "messages")
    if any(not isinstance(m.get("seq"), int) or isinstance(m.get("seq"), bool) for m in msgs):
        rec["gap"] = True          # 序号读不懂：记为证据异常，不是跳过了事
    for m in sorted((m for m in msgs if isinstance(m.get("seq"), int)), key=lambda m: m["seq"]):
        seq = m["seq"]
        if seq <= rec["last_seq"]:
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


def identity_drift_one(night, key, i):
    """单张子单的核心身份字段和计划一致：负责人是计划里的 agent、父单、stage、项目。"""
    c = next(c for c in night.children() if c["key"] == key)
    ident, out = night.ident(key), []
    if not isinstance(i, dict):
        return [f"{ident} 读不清"]
    if i.get("assignee_type") != "agent" or i.get("assignee_id") != night.plan["agents"][c["agent"]]:
        out.append(f"{ident} 负责人不是计划里的 {c['agent']}")
    if i.get("parent_issue_id") != night.state["issues"]["PARENT"]["id"]:
        out.append(f"{ident} 父单不对")
    if i.get("stage") != c["stage"]:
        out.append(f"{ident} stage 不对")
    if night.plan.get("project_id") and i.get("project_id") != night.plan["project_id"]:
        out.append(f"{ident} 项目不对")
    return out


def identity_drift(night, issues):
    """父单负责人是计划里的常浩（Child-Done 不唤醒 agent），每张子单的身份字段和计划一致。issues：key → issue。"""
    out = []
    parent = issues.get("PARENT")
    if not (isinstance(parent, dict) and owner_parent(night, parent)):
        out.append(f"{night.ident('PARENT')} 负责人不是计划里的常浩（Child-Done 可能唤醒 agent）")
    for c in night.children():
        out += identity_drift_one(night, c["key"], issues.get(c["key"]))
    return out


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
        if snap["issues"][k].get("status") == "cancelled":
            why.append(f"{night.ident(k)} 被取消，后面 stage 依赖的产物不明，不自动放行")
        if c["stage"] < st.get("stage", 0) and active_runs(snap, [k]):
            why.append(f"{night.ident(k)}（stage {c['stage']}，已交付）又开了 run：异常重入")
    why += identity_drift(night, snap["issues"])
    for k, ws in snap["wakeups"].items():
        if any(wakeup_on(w) for w in ws):
            why.append(f"{night.ident(k)} 上出现了未授权的 Wakeup")
    if any(str(a.get("status")).lower() == "active" for a in snap["autopilots"]):
        why.append("工作区里有启用中的 Autopilot")
    strays = stray_runs(night)
    if strays:
        why.append("计划里的 agent 在家族外开了 run：" + "、".join(f"{n} {str(r.get('id'))[:8]}" for n, r in strays[:3]))
    for c in night.children():   # 后面的 stage 必须还在 backlog、没有 run
        if c["stage"] > st.get("stage", 0):
            k = c["key"]
            if snap["issues"][k].get("status") != "backlog" or snap["runs"][k]:
                why.append(f"{night.ident(k)}（stage {c['stage']}）被提前移出 backlog 或开过 run")
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
    st.setdefault("advanced", {}).setdefault(str(n), iso(t))   # 补推进时不改最初的推进时刻（停滞按它算）
    night.save()
    moved = []
    for k in night.stage_keys(n):
        if emergency_file(night.id).exists():
            raise Unclear("应急冻结已触发，不再推进")
        iss = night.mc("issue", "get", night.ident(k))
        drift = identity_drift_one(night, k, iss)
        if drift:
            raise Unclear("推进前核对身份不符：" + "；".join(drift))
        if iss.get("status") != "backlog":
            continue
        if emergency_file(night.id).exists():   # 改状态（会触发 agent）前再查一次（第 3 轮 P1-01A）
            raise Unclear("应急冻结已触发，不再推进")
        night.mc("issue", "status", night.ident(k), "todo")
        moved.append(night.ident(k))
    night.log("advance", stage=n, issues=moved)
    return moved


def stray_runs(night):
    """计划里的 agent 在开跑后、家族之外开出的 run（别的 issue、chat 都算）。一页 200 条还没翻到开跑前，就当核不清。"""
    if not night.state.get("auth"):
        return []
    start = night.state["auth"]["started_at"]
    fam = {v["id"] for v in night.state["issues"].values()}
    out = []
    for name, aid in night.plan["agents"].items():
        runs = items(night.mc("agent", "tasks", aid, "--limit", "200"), "tasks")
        newer = [r for r in runs if (r.get("created_at") or "") >= start]
        if len(runs) >= 200 and len(newer) == len(runs):
            raise Unclear(f"{name} 开跑后的 run 超过 200 条，核不全")
        out += [(name, r) for r in newer if r.get("issue_id") not in fam]
    return out


def owner_parent(night, parent):
    return parent.get("assignee_type") == "member" and parent.get("assignee_id") == night.plan["owner_member_id"]


def final_export(night):
    """停机后最后一次导出：把刚被取消的 run 也写进 run-log、收齐消息，并纳入停机判定（第 2 轮 P0-01）。
    返回 (问题, 仍在跑的 run id)：导出时发现 run 消失、序号缺口、读不懂，都算核不清，不能写 STOPPED；
    回读之后又冒出来的非终态 run 交给调用方再取消、再回读。"""
    try:
        collect(night, full=True)
    except Exception as e:      # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        night.state["evidence_incomplete"] = msg
        night.save()
        night.log("evidence_incomplete", error=msg)
        return msg, []
    return None, [rid for rid, rec in night.state.get("runs", {}).items() if rec.get("status") in ACTIVE]


def stop(night, reasons, finished=False, unclear=False):
    """5.3a 停机。返回要打印的文字；没停干净就冻结。任何一步出错都只记下来、接着做后面的步骤，最后由回读判定。
    unclear：触发原因本身就是 run 记录核不清。这时“现在看得见的 run 都结束了”证明不了“全部 run 都结束了”，回读通过也冻结。"""
    st, errs = night.state, []
    st["stopping"] = {"at": iso(now()), "reasons": reasons, "finished": finished, "unclear": unclear}
    night.save()
    night.log("stop_begin", reasons=reasons, finished=finished)
    fam = night.family()

    def step(desc, fn):
        try:
            return fn()
        except Exception as e:      # noqa: BLE001 —— 停机路上不能因为任何一步的意外停下
            errs.append(f"{desc}：{type(e).__name__}: {e}")
            return None

    for k in fam:   # ① 禁用 Wakeup
        for w in step(f"读 {night.ident(k)} 的 Wakeup", lambda k=k: items(night.mc("issue", "wakeup", "list", night.ident(k)), "wakeups")) or []:
            if wakeup_on(w):
                step(f"禁用 {night.ident(k)} 的 Wakeup", lambda k=k, w=w: night.mc("issue", "wakeup", "disable", night.ident(k), w["id"]))
    for a in step("读 Autopilot", lambda: items(night.mc("autopilot", "list"), "autopilots")) or []:   # ② 暂停 Autopilot
        if str(a.get("status")).lower() == "active":
            step("暂停 Autopilot", lambda a=a: night.mc("autopilot", "update", a["id"], "--status", "paused"))
    for k in fam:   # ③ 取消家族里的非终态 run
        for r in step(f"读 {night.ident(k)} 的 run", lambda k=k: items(night.mc("issue", "runs", night.ident(k)), "runs")) or []:
            if r.get("status") in ACTIVE:
                step(f"取消 {night.ident(k)} 的 run", lambda r=r: night.mc("issue", "cancel-task", r["id"]))
    for _, r in step("列家族外的 run", lambda: stray_runs(night)) or []:   # ③b 家族外的也取消
        if r.get("status") in ACTIVE:
            step("取消家族外的 run", lambda r=r: night.mc("issue", "cancel-task", r["id"]))
    parent = step("读父单", lambda: night.mc("issue", "get", night.ident("PARENT")))   # ④ Child-Done 保持中和：父单指派给常浩
    if isinstance(parent, dict) and not owner_parent(night, parent):
        step("父单改回指派给常浩", lambda: night.mc("issue", "assign", night.ident("PARENT"), "--to-id",
                                             night.plan["owner_member_id"], "--no-start"))
    if not finished:   # ⑤ 没交付的子单改 blocked（--no-start，不在子单上评论），原因写在父单
        for c in night.children():
            k = c["key"]
            iss = step(f"读 {night.ident(k)}", lambda k=k: night.mc("issue", "get", night.ident(k)))
            if isinstance(iss, dict) and iss.get("status") not in DELIVERED + ("backlog", "blocked"):
                step(f"把 {night.ident(k)} 改为 blocked", lambda k=k: night.mc("issue", "status", night.ident(k), "blocked", "--no-start"))
        step("在父单写停机原因", lambda: night.mc("issue", "comment", "add", night.ident("PARENT"), "--content-stdin",
                                          stdin="夜跑主控停机：" + "；".join(reasons) + "。后面的 stage 留在 backlog，不再推进。"))
    ok, left = readback(night, errs)   # ⑥ 回读
    if ok and unclear:
        ok, left = False, ["触发原因是 run 记录核不清，回读看得见的 run 都已终态也证明不了全部已终止"]
    night.log("stop_readback", ok=ok, left=left, errors=errs)
    gap, late = final_export(night)    # ⑦ 最后一次导出（成功、冻结都做），结果纳入判定
    if ok and late:                    # 回读之后又冒出在跑的 run：再取消、再回读、再导出一次
        for rid in late:
            try:
                night.mc("issue", "cancel-task", rid)
            except Exception as e:     # noqa: BLE001
                errs.append(f"取消回读后出现的 run {rid[:8]}：{type(e).__name__}: {e}")
        ok, left = readback(night, errs)
        gap2, late = final_export(night)
        gap = gap or gap2
        night.log("stop_readback_again", ok=ok, left=left, late=late)
        if late:
            ok, left = False, left + [f"回读之后又出现 {len(late)} 个非终态 run，取消后仍没结束"]
    if ok and gap:
        ok, left = False, [f"最后一次导出时发现运行记录核不清（{gap}），不能证明全部 run 已终止"]
    if ok and emergency_file(night.id).exists():   # 停机途中出现了应急冻结：以冻结为准（第 3 轮 P1-01B）
        ok, left = False, ["停机途中出现了应急冻结标记，以应急冻结为准"]
    if ok:
        st["outcome"] = "FINISHED" if finished else "STOPPED"
        st["ended_at"] = iso(now())
        st.pop("stopping", None)
        night.save()
        night.log("stopped", outcome=st["outcome"], reasons=reasons)
        head = "夜跑全部 stage 已交付，触发源都已关停（回读确认）" if finished else "夜跑已停机（回读确认：run 全部终态，Wakeup、Autopilot 都已停）"
        return f"{head}。原因：{'；'.join(reasons)}。release 之前新出现的 run 都会被取消。"
    return freeze(night, reasons, left + errs)


def readback(night, errs):
    """停机成功 = 家族内外的相关 run 全部终态，且 Wakeup、Autopilot 都停了、父单仍指派给常浩（Child-Done 不唤醒 agent）。"""
    tries, gap = (int(x) for x in os.environ.get("DT_NIGHT_READBACK", "12,5").split(","))
    left = []
    for i in range(max(tries, 1)):
        left = []
        try:
            present = set()
            for k in night.family():
                for r in items(night.mc("issue", "runs", night.ident(k)), "runs"):
                    present.add(r.get("id"))
                    if r.get("status") not in TERMINAL:
                        left.append(f"{night.ident(k)} 的 run {str(r.get('id'))[:8]} 仍是 {r.get('status')}")
            lost = [rid for rid in night.state.get("runs", {}) if rid not in present]
            if lost:      # 以前见过的 run 这次没列出来：看不见不等于结束了
                left.append(f"以前见过的 {len(lost)} 个 run 回读时不见了（{', '.join(x[:8] for x in lost[:3])}）")
            for k in night.family():
                if any(wakeup_on(w) for w in items(night.mc("issue", "wakeup", "list", night.ident(k)), "wakeups")):
                    left.append(f"{night.ident(k)} 上仍有启用中的 Wakeup")
            for name, r in stray_runs(night):
                if r.get("status") not in TERMINAL:
                    left.append(f"{name} 在家族外的 run {str(r.get('id'))[:8]} 仍是 {r.get('status')}")
            if any(str(a.get("status")).lower() == "active" for a in items(night.mc("autopilot", "list"), "autopilots")):
                left.append("仍有启用中的 Autopilot")
            parent = night.mc("issue", "get", night.ident("PARENT"))
            if not (isinstance(parent, dict) and owner_parent(night, parent)):
                left.append("父单没有指派给常浩，Child-Done 可能唤醒 agent")
        except Exception as e:      # noqa: BLE001 —— 回读读不清一律当没停干净
            left.append(f"回读失败：{type(e).__name__}: {e}")
        if not left:
            return True, []
        if i + 1 < tries:
            time.sleep(gap)
    return False, left


def hard_freeze(plan):
    """停执行器（freeze_cmds），再用 freeze_check 独立回读：容器真的停了、也不会自动重启才算 verified。
    只依赖计划和本机命令，不碰夜跑状态，应急路径也用它。返回 (各命令结果, verified, 回读输出)。"""
    results = []
    for cmd in plan.get("freeze_cmds", []):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
            results.append({"cmd": cmd, "code": r.returncode, "out": (r.stderr or r.stdout).strip()[:200]})
        except Exception as e:   # noqa: BLE001 —— 一条命令出问题，后面的照样执行
            results.append({"cmd": cmd, "code": None, "out": type(e).__name__})
    chk, seen = plan.get("freeze_check") or {}, ""
    if chk.get("cmd"):
        try:
            r = subprocess.run(chk["cmd"], capture_output=True, text=True, timeout=TIMEOUT)
            seen = (r.stdout or r.stderr).strip()
            verified = r.returncode == 0 and seen == chk.get("expect")
        except Exception as e:   # noqa: BLE001 —— 回读不了就是没确认
            seen, verified = type(e).__name__, False
    else:
        verified = False         # 没有独立回读手段，就不能说已经冻住
    return results, verified, seen


def freeze_lines(night, results, verified, seen):
    if verified:
        return [f"已停隔离执行器容器并去掉自动重启（回读：{seen}）。"]
    bad = [" ".join(r["cmd"]) for r in results if r["code"] != 0]
    out = ["⚠️ 执行器没确认停住" + (f"，失败的命令：{'；'.join(bad)}" if bad else "") + f"（回读：{seen or '无'}）。"
           "每次检查都会再试一次；请尽快手动停容器。"]
    if not night.plan.get("freeze_cmds"):
        out.append("⚠️ 计划里没有 freeze_cmds。")
    return out


def freeze(night, reasons, problems):
    st = night.state
    results, verified, seen = hard_freeze(night.plan)
    st.update(frozen=True, freeze_verified=verified, outcome="UNRECONCILED", ended_at=iso(now()))
    st.pop("stopping", None)
    night.save()
    night.log("freeze", reasons=reasons, problems=problems, commands=results, verified=verified, check=seen)
    lines = ["⚠️ 夜跑停机没有回读通过，已冻结（UNRECONCILED），不会再推进或重试任务。",
             f"停机原因：{'；'.join(reasons)}", "没停干净的：" + "；".join(problems[:6])]
    lines += freeze_lines(night, results, verified, seen)
    lines.append(f"恢复只由常浩在终端里核对后做（见执行单第六节）。记录在 {night.d}")
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
    snap = {"runs": {}, "wakeups": {}, "issues": {}}
    for k in night.family():
        iss = night.mc("issue", "get", night.ident(k))
        snap["issues"][k] = iss
        if k != "PARENT" and iss.get("status") != "backlog":
            why.append(f"{night.ident(k)} 不在 backlog（{iss.get('status')}）")
        snap["runs"][k] = items(night.mc("issue", "runs", night.ident(k)), "runs")
        if any(wakeup_on(w) for w in items(night.mc("issue", "wakeup", "list", night.ident(k)), "wakeups")):
            why.append(f"{night.ident(k)} 上有启用中的 Wakeup")
    why += identity_drift(night, snap["issues"])
    if emergency_file(night.id).exists():
        why.append("这次夜跑触发过应急冻结")
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


def watch_after(night):
    """夜跑结束后（STOPPED / FINISHED / 冻结）、release 之前继续看着，绝不推进：
    家族内外新出现的非终态 run 一律取消并告警（早上要接着干活，先 release 再触发）；
    冻结了却没确认执行器停住的，每次都再试一次硬停并告警（重试硬停不等于重新执行任务）。"""
    st, out, code = night.state, [], 0
    if st.get("frozen") and not st.get("freeze_verified"):
        results, verified, seen = hard_freeze(night.plan)
        st["freeze_verified"] = verified
        night.save()
        night.log("freeze_retry", commands=results, verified=verified, check=seen)
        out += freeze_lines(night, results, verified, seen)
        code = 1
    found = [(night.ident(k), r) for k in night.family() for r in items(night.mc("issue", "runs", night.ident(k)), "runs")]
    found += [(f"{n}（家族外）", r) for n, r in stray_runs(night)]
    left = []
    for who, r in found:
        if r.get("status") not in ACTIVE:
            continue
        try:
            night.mc("issue", "cancel-task", r["id"])
            left.append(f"{who} 的 run {str(r['id'])[:8]}（{r.get('status')}，已取消）")
        except Unclear as e:
            left.append(f"{who} 的 run {str(r['id'])[:8]}（{r.get('status')}，取消失败：{e}）")
    if left:
        night.log("after_end_guard", runs=left)
        out.append(f"⚠️ 夜跑已结束（{st['outcome']}），还没 release 却有 run 开跑：" + "；".join(left) +
                   "。要接着干活，先在终端 release 再触发。" + ("请核对容器是不是被重启了。" if st.get("frozen") else ""))
        code = 1
    return code, "\n".join(out)


START_WINDOW = dt.timedelta(minutes=30)   # 定时开跑：过了批准的时刻这么久还没开（比如电脑睡了），就不开


def cmd_approve(night, by, start_at, hours, max_runs):
    """记下“几点开跑”的批准，到点由 tick 开跑（定时任务不能带参数，也不在会话里干等，G-12）。"""
    if night.state.get("auth"):
        print("这次夜跑已经开跑过。")
        return 1
    t = parse(start_at)
    t = t if t.tzinfo else t.replace(tzinfo=BJ)
    night.state["approval"] = {"by": by, "start_at": iso(t), "hours": hours, "max_runs": max_runs, "at": iso(now())}
    night.save()
    night.log("approve", by=by, start_at=iso(t), hours=hours, max_runs=max_runs)
    print(f"已记下：{by} 批准夜跑 {night.id} 在 {bj(t)} 开跑（过了 {bj(t + START_WINDOW)} 还没开就不开）。")
    return 0


def scheduled_start(night, t):
    ap = night.state.get("approval")
    if not ap or ap.get("result"):
        return 0, ""
    at_ = parse(ap["start_at"])
    if t < at_:
        return 0, ""
    if t - at_ > START_WINDOW:
        ap["result"] = "missed"
        night.save()
        night.log("start_missed", start_at=ap["start_at"])
        return 1, f"夜跑 {night.id} 没开跑：批准在 {bj(at_)} 开跑，主控 {bj(t)} 才检查到，已过开跑窗口。"
    ap["result"] = "started"     # 先记账：start 本身失败也不每 5 分钟重来一次
    night.save()
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cmd_start(night, ap["by"], ap.get("hours"), ap.get("max_runs"))
    if code:
        ap["result"] = "refused"
        night.save()
    return code, buf.getvalue().strip()


def cmd_tick(night):
    st, t = night.state, now()
    if not st.get("auth"):
        return scheduled_start(night, t)
    if emergency_file(night.id).exists() and not st.get("frozen"):   # 卡住期间触发过应急冻结：接管为冻结状态
        em = load(emergency_file(night.id), {})
        st.update(frozen=True, freeze_verified=bool(em.get("verified")), outcome="UNRECONCILED", ended_at=em.get("at") or iso(t))
        st.pop("stopping", None)
        night.save()
        night.log("emergency_adopted", emergency=em)
        return 1, "⚠️ 上一次检查卡住时已触发应急冻结，现记为 UNRECONCILED，不再推进；恢复由常浩在终端核对后做。"
    if st.get("outcome"):
        try:
            return watch_after(night)
        except Exception as e:      # noqa: BLE001
            return 1, f"⚠️ 夜跑已结束（{st['outcome']}），核对 run 失败：{type(e).__name__}: {e}"
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
        s = st["stopping"]
        out.append(stop(night, s["reasons"] + ["上次停机没做完，接着做"], s.get("finished", False), s.get("unclear", False)))
        return (1 if night.state.get("frozen") else 0), "\n".join(out)
    try:
        snap = collect(night)
    except Unclear as e:      # run 记录核不清：停机，且回读通过也冻结（P0：看不见不等于结束了）
        out.append(stop(night, [f"UNRECONCILED：{e}"], unclear=True))
        return (1 if night.state.get("frozen") else 0), "\n".join(out)
    try:
        why = violations(night, snap, t)
    except Unclear as e:
        why = [f"核对上限时读不清：{e}"]
    if why:
        out.append(stop(night, why))
        return (1 if night.state.get("frozen") else 0), "\n".join(out)
    n = st["stage"]
    keys = night.stage_keys(n)
    behind = [k for k in keys if snap["issues"][k].get("status") == "backlog"]
    if behind:   # 上次推进做到一半就中断了：补推进（advance 只动还在 backlog 的单）
        try:
            moved = advance(night, n, t)
        except Unclear as e:
            out.append(stop(night, [f"补推进 stage {n} 时结果不明：{e}"]))
            return (1 if night.state.get("frozen") else 0), "\n".join(out)
        out.append(f"夜跑 stage {n} 上次推进没做完，已补上：{'、'.join(moved)}。")
        return 0, "\n".join(out)
    # 交付 = 当前 stage 都到 in_review / done，且整个家族没有非终态 run（P1-05：旧 stage 重入时不推进）
    if all(snap["issues"][k].get("status") in STAGE_DONE for k in keys) and not active_runs(snap):
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


def cmd_release(night, by, confirm):
    """--by 只是审计字段，不是身份凭证。一台单用户的 Mac 上，命令行认不出“是不是常浩本人”，这里能做的是两道关：
    --confirm 必须写出「夜跑 id:当前结果」（绑定这次夜跑和它现在的状态，防误放）；
    冻结（UNRECONCILED）的只能在交互式终端里 release——agent 的非交互调用一律拒绝（Hermes 批复：冻结恢复不下放）。"""
    st = night.state
    if not st.get("outcome"):
        print("夜跑还在进行，先 stop 再 release。")
        return 1
    want = f"{night.id}:{st['outcome']}"
    if confirm != want:
        print(f"没有 release：--confirm 要写成 {want}（绑定这次夜跑和它现在的状态）。")
        return 1
    if (st.get("frozen") or emergency_file(night.id).exists()) and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("没有 release：这次夜跑已冻结（UNRECONCILED），只能由常浩在交互式终端里核对现场后 release。")
        night.log("release_refused", by=by, reason="frozen-non-interactive")
        return 1
    st["released"] = {"by": by, "at": iso(now()), "interactive": sys.stdin.isatty()}
    night.save()
    night.log("release", by=by)
    cur = ROOT / "night-current"
    if current_id() == night.id:
        cur.unlink()
    print(f"夜跑 {night.id} 已解除，定时任务不再管它。" + ("容器若已冻结，由你手动恢复。" if night.state.get("frozen") else ""))
    return 0


LOCK_WAIT = 120                          # 人工命令最多等主控 2 分钟
LOCK_STALE = dt.timedelta(minutes=15)    # tick 持锁超过这么久就告警（多半卡在 multica 超时上）
_LOCKS = []                              # 持有到进程结束


def grab_lock(path, wait):
    """拿到锁返回 None；拿不到返回持锁的时长（timedelta，读不到持锁时刻就当已卡死）。"""
    f = open(path, "a+")
    deadline = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.seek(0)
            f.truncate()
            f.write(iso(now()))
            f.flush()
            _LOCKS.append(f)
            return None
        except BlockingIOError:
            if time.monotonic() >= deadline:
                break
            time.sleep(2)
    f.seek(0)
    try:
        since = parse(f.read().strip() or None)
    except ValueError:
        since = None
    return now() - since if since else LOCK_STALE * 2


def emergency_file(nid):
    return ROOT / nid / "emergency.json"


def emergency_freeze(nid, why):
    """应急停机（P1-02）：持锁的主控卡住时，不等它，直接硬停执行器并告警。
    不碰 state.json（那是持锁进程的），只写 emergency.json；持锁进程推进前会看到它，下一次拿到锁的 tick 接管为冻结状态。
    已正常 STOPPED / FINISHED、或已 release 的夜跑不需要。"""
    st = load(ROOT / nid / "state.json", {})
    if not st.get("auth") or st.get("released") or st.get("outcome") in ("STOPPED", "FINISHED"):
        return ""
    plan = load(ROOT / nid / "plan.json")
    at = iso(now())
    # 先原子写入阻断标记，再停执行器（第 2 轮 P1-01）：卡住的主控只要看到标记就不再推进，不必等硬停做完
    dump(emergency_file(nid), {"status": "EMERGENCY_REQUESTED", "at": at, "why": why})
    results, verified, seen = hard_freeze(plan)
    dump(emergency_file(nid), {"status": "EMERGENCY_FROZEN" if verified else "EMERGENCY_UNVERIFIED", "at": at, "why": why,
                               "commands": results, "verified": verified, "check": seen})
    append(ROOT / nid / "run-log.jsonl", {"ts": iso(now()), "event": "emergency_freeze", "why": why,
                                          "commands": results, "verified": verified, "check": seen})
    head = f"⚠️ 夜跑应急冻结：{why}。没有等卡住的主控，直接停了执行器。"
    tail = "执行器已确认停住、不会自动重启。" if verified else "⚠️ 执行器没确认停住，请立刻手动停容器。"
    return f"{head}{tail}恢复由常浩在终端核对后做。"


def guarded_stop(nid, why, unclear=False):
    """停机，外加兜底：停机本身崩了就直接冻结（只依赖计划和本机命令），冻结也崩了就喊人。tick 和人工 stop 共用（P2-01）。
    返回 (ok, 文字)：只有回读确认的 STOPPED / FINISHED 才算 ok；冻结和“停机冻结都失败”都不算，退出码据此非零（第 2 轮 P1-02）。"""
    try:
        text = stop(Night(nid), why, unclear=unclear)
    except Exception as e2:      # noqa: BLE001
        try:
            text = freeze(Night(nid), why, [f"停机过程异常：{type(e2).__name__}: {e2}"])
        except Exception as e3:  # noqa: BLE001
            return False, f"⚠️ 夜跑停机和冻结都失败了，请立刻手动停执行器容器：{type(e3).__name__}: {e3}"
    try:
        ok = Night(nid).state.get("outcome") in ("STOPPED", "FINISHED")
    except Exception:            # noqa: BLE001 —— 连状态都读不回来，就不能说停好了
        ok = False
    return ok, text


def main(argv=None):
    a = argparse.ArgumentParser(description="Multica 夜跑主控（DT-ORC M5）")
    sub = a.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("file")
    s = sub.add_parser("start")
    s.add_argument("--approved-by", required=True)
    s.add_argument("--hours", type=float)
    s.add_argument("--max-runs", type=int)
    ap = sub.add_parser("approve")
    ap.add_argument("--by", required=True)
    ap.add_argument("--start-at", required=True, help="开跑时刻，ISO 格式；不写时区按北京时间")
    ap.add_argument("--hours", type=float)
    ap.add_argument("--max-runs", type=int)
    sub.add_parser("tick")
    x = sub.add_parser("stop")
    x.add_argument("--reason", required=True)
    sub.add_parser("status")
    sub.add_parser("report")
    r = sub.add_parser("release")
    r.add_argument("--by", required=True, help="只作审计记录，不是身份凭证")
    r.add_argument("--confirm", required=True, help="写成「夜跑 id:当前结果」，例如 2026-11-08-todo-lite-v0.2:STOPPED")
    for q in (s, ap, x, r, sub.choices["tick"], sub.choices["status"], sub.choices["report"]):
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
    held = grab_lock(ROOT / nid / "tick.lock", wait=0 if a.cmd == "tick" else LOCK_WAIT)
    if held is not None:
        mins = int(held.total_seconds() // 60)
        if a.cmd == "tick" and held <= LOCK_STALE:
            return 0          # 上一次 tick 还在跑（比如在停机回读），这次跳过
        if a.cmd in ("tick", "stop"):
            text = emergency_freeze(nid, f"主控已持锁 {mins} 分钟没放（多半卡在 multica 不响应上）" if a.cmd == "tick"
                                    else f"人工停机时主控已持锁 {mins} 分钟")
            print(text or f"⚠️ 夜跑主控已持锁 {mins} 分钟没放。")
            return 1
        print(f"主控正忙（已持锁 {mins} 分钟），稍后再试；要急停就用 stop。")
        return 1
    night = Night(nid)
    if a.cmd == "tick":
        try:
            code, text = cmd_tick(night)
        except Exception as e:     # 主控自己出错也要停（5.3a：不依赖任何一方“还活着”）
            night = Night(nid)
            night.log("crash", error=traceback.format_exc()[-1500:])
            why = [f"主控脚本异常：{type(e).__name__}: {e}"]
            if night.state.get("auth") and not night.state.get("outcome"):
                text = guarded_stop(nid, why)[1]
            else:
                text = f"⚠️ 夜跑主控异常：{type(e).__name__}: {e}"
            code = 1
        if text:
            print(text)
        return code
    if a.cmd == "start":
        return cmd_start(night, a.approved_by, a.hours, a.max_runs)
    if a.cmd == "approve":
        return cmd_approve(night, a.by, a.start_at, a.hours, a.max_runs)
    if a.cmd == "stop":
        ap = night.state.get("approval")
        if not night.state.get("auth") and ap and not ap.get("result"):
            ap["result"] = "revoked"
            night.save()
            night.log("approval_revoked", reason=a.reason)
            print(f"已撤销 {bj(ap['start_at'])} 开跑的批准，不会开跑。原因：{a.reason}")
            return 0
        if not night.state.get("auth") or night.state.get("outcome"):
            print("夜跑没在进行，不用停。")
            return 1
        ok, text = guarded_stop(nid, [f"人工停机：{a.reason}"])
        print(text)
        return 0 if ok else 1
    if a.cmd == "status":
        return cmd_status(night)
    if a.cmd == "report":
        return cmd_report(night)
    return cmd_release(night, a.by, a.confirm)


if __name__ == "__main__":
    sys.exit(main())
