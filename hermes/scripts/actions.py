"""代理行动记录（#110 批 1b）：actions.jsonl 的结构、记录函数，以及把现有六个来源转成同一结构的适配器（只读、只用标准库）。

规格：docs/个人项目日报-V2-设计.md 5.4（结构，定稿后不再改）、第 2 节和 docs/常设授权清单.md（授权档位）、第 6 节（证据）。
用法：
  python3 actions.py [--date 统计日]     打印统计日（D 04:00 到 D+1 04:00，Asia/Shanghai）内的行动，JSON
  python3 actions.py record '<JSON>'      往 actions.jsonl 追加一条行动记录，打印写下的那条
约定（常浩 2026-11-09 定）：
  - 历史不迁移：六个来源每次取数时现读现转，不写进 actions.jsonl；actions.jsonl 只放以后各脚本用 record() 直接写的记录。
  - 自由文字不下结论：裁决文件（evidence/**/hermes-*.md）的 decision、result 记「不明」，只摘首段原文；
    只有结构化来源（派单回执的 refused、合并官记录的 owner 列）才记「拒绝」「执行」。
  - 安装（kind=install）只认 record() 写下的记录，不从证据目录、CURRENT 的文字里猜。
  - 授权档位低于这类动作应有的档位（授权清单：卡片合并 B；安装、新建或修改定时任务 D；夜跑 C），标 auth_gap。
取不到的来源在 coverage 里记 ok=false、count=null 和原因，不当成 0。
"""
import argparse
import csv
import datetime as dt
import fcntl
import json
import os
import re
import sqlite3
import sys
import urllib.parse
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdaily_data  # noqa: E402  复用统计日窗口、时间解析
import progress_data  # noqa: E402  复用名册、派单台账路径

TZ = pdaily_data.TZ
DT_HOME = "~/Library/Application Support/DigitalTwin"
ACTIONS = Path(os.environ.get("DT_ACTIONS", f"{DT_HOME}/actions.jsonl")).expanduser()
SHADOW = Path(os.environ.get("DT_SHADOW_DIR", f"{DT_HOME}/shadow")).expanduser()
MERGE_RUNS = Path(os.environ.get("DT_MERGE_RUNS", f"{DT_HOME}/merge-runs")).expanduser()
NIGHT_ROOT = Path(os.environ.get("DT_NIGHT_ROOT", f"{DT_HOME}/runs")).expanduser()
EVIDENCE = Path(os.environ.get("DT_EVIDENCE", f"{DT_HOME}/evidence")).expanduser()
CRON = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "cron"

# 5.4 的字段，顺序即输出顺序；定稿后不再改
FIELDS = ("action_id", "ts", "project_id", "kind", "request", "actor", "auth_tier", "auth_source",
          "decision", "result", "verification", "follow_up")
KINDS = ("merge", "ruling", "dispatch", "supervise", "refuse", "cron", "install", "other")
DECISIONS = ("执行", "拒绝", "升级给常浩", "不明")
RESULTS = ("已发起", "调用成功", "业务已验证", "失败", "不明")   # 另外 null：没执行（拒绝、升级给常浩）
TIERS = ("A", "B", "C", "D")                                    # 另外 null：授权来源没接入，不知道
REQUIRED = {"merge": "B", "install": "D", "cron": "D"}          # actions.jsonl 里各类动作应有的最低档位
QUOTE_MAX = 400


class Fail(Exception):
    pass


# ---------- 结构与记录 ----------

def stamp(value, what="ts"):
    """带时区的 ISO 时间（北京时间显示）。字符串没写时区的，按北京时间算（派单台账、merge-runs 目录名都是本机时间）。"""
    t = pdaily_data.parse_ts(value) if isinstance(value, str) else value
    if t is None:
        raise ValueError(f"{what} 没写")
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ)
    return t.astimezone(TZ).isoformat(timespec="seconds")


def check(rec):
    """一条记录合不合 5.4 的结构；不合就抛 ValueError，写明哪个字段。"""
    extra = set(rec) - set(FIELDS)
    if extra:
        raise ValueError(f"不认识的字段：{'、'.join(sorted(extra))}")
    for k in ("action_id", "ts", "kind", "request", "actor", "decision"):
        if not rec.get(k):
            raise ValueError(f"缺字段：{k}")
    for k, allowed in (("kind", KINDS), ("decision", DECISIONS)):
        if rec[k] not in allowed:
            raise ValueError(f"{k} 只能是 {' / '.join(allowed)}，收到「{rec[k]}」")
    if rec.get("result") is not None and rec["result"] not in RESULTS:
        raise ValueError(f"result 只能是 {' / '.join(RESULTS)} 或 null，收到「{rec['result']}」")
    if rec.get("auth_tier") is not None and rec["auth_tier"] not in TIERS:
        raise ValueError(f"auth_tier 只能是 A / B / C / D 或 null，收到「{rec['auth_tier']}」")
    if rec["decision"] == "执行" and rec.get("result") is None:
        raise ValueError("decision 是「执行」时 result 不能空")
    return {k: rec.get(k) for k in FIELDS}


def record(**fields):
    """往 actions.jsonl 追加一条。action_id、ts 不给就自动生成；时间没写时区按北京时间。返回写下的那条。"""
    fields = dict(fields)
    fields["ts"] = stamp(fields.get("ts") or dt.datetime.now(TZ))
    fields.setdefault("action_id", f"{fields.get('kind') or 'other'}-{uuid.uuid4().hex[:12]}")
    rec = check(fields)
    ACTIONS.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIONS, "a", encoding="utf-8") as f:   # 几个脚本同时写：加锁，一次写一整行
        fcntl.flock(f, fcntl.LOCK_EX)
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def gap(rec, required):
    """执行了、但授权档位低于应有档位（或不知道），返回说明；否则 None。拒绝、升级的不算。"""
    if rec["decision"] != "执行" or not required:
        return None
    have = rec.get("auth_tier")
    if have and TIERS.index(have) >= TIERS.index(required):
        return None
    return f"授权不足：这类动作要 {required} 档，记录是{f' {have} 档' if have else '「不知道」'}"


def inside(ts, w):
    return w[0] <= pdaily_data.parse_ts(ts) < w[1]


def when(value, w, notes, where):
    """来源里一行的时间：在统计日内返回规范写法，不在返回 None；写法不对记进 notes 并跳过这一行，不让整个来源取不到。"""
    if not value:
        return None
    try:
        ts = stamp(value)
    except (ValueError, TypeError) as e:
        notes.append(f"{where} 时间认不出（{value}：{e}），跳过")
        return None
    return ts if inside(ts, w) else None


def from_file(w, notes):
    """actions.jsonl 里统计日内的记录；坏行跳过并写进 notes，不让一行坏掉整份。文件不存在 = 还没有人写过，是 0 条。"""
    if not ACTIONS.exists():
        return []
    out = []
    for i, line in enumerate(ACTIONS.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = check(json.loads(line))
            rec["ts"] = stamp(rec["ts"])
        except (ValueError, TypeError, AttributeError) as e:
            notes.append(f"actions.jsonl 第 {i} 行跳过：{e}")
            continue
        if inside(rec["ts"], w):
            out.append((rec, REQUIRED.get(rec["kind"])))
    return out


# ---------- 适配器（历史来源，现读现转） ----------

def quote(text, n=QUOTE_MAX):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + "…（后文截断）"


def roster_ids():
    """名册：GitHub 仓库 → 项目 id；仓库短名 → 项目 id。读不了就只用原名。"""
    try:
        projects = json.loads(progress_data.ROSTER.read_text())["projects"]
    except (OSError, ValueError, KeyError):
        return {}
    ids = {}
    for p in projects:
        if p.get("github"):
            ids[p["github"]] = ids[p["github"].split("/")[-1]] = p["id"]
        ids.setdefault(p["id"], p["id"])
    return ids


def act(**kw):
    return {k: kw.get(k) for k in FIELDS}


def merge_officer(w, notes):
    """合并官 shadow/agreement.csv：
    - 影子判定（A 档）：同一个「PR + 版本」只算一条，时间取第一次判定，内容取统计日结束前的最后一次（V2 第 6 节去重）；
    - owner 列（常浩点卡片的「合并」「退回」，B 档）：合并有 GitHub 结局就算业务已验证。"""
    ledger = SHADOW / "agreement.csv"
    if not ledger.exists():
        raise Fail(f"找不到 {ledger}")
    with ledger.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    ids, out, versions, owners = roster_ids(), [], {}, {}
    for i, r in enumerate(rows, 2):
        key = (r["repo"], r["pr"], r["head_sha"])
        try:
            r["_at"] = pdaily_data.parse_ts(stamp(r["evaluated_at"]))
        except (ValueError, TypeError) as e:
            notes.append(f"agreement.csv 第 {i} 行判定时间认不出（{r.get('evaluated_at')}：{e}），跳过")
            continue
        if r["_at"] < w[1]:
            versions.setdefault(key, []).append(r)
        if r.get("owner") and r.get("owner_at"):
            owners.setdefault(key + (r["owner"],), r)
    for (repo, pr, sha), rs in versions.items():
        rs.sort(key=lambda r: r["_at"])
        first, last = rs[0], rs[-1]
        if not w[0] <= first["_at"] < w[1]:
            continue
        why = f"：{last['reasons']}" if last.get("reasons") else ""
        out.append((act(
            action_id=f"shadow:{repo}#{pr}@{sha[:12]}", ts=stamp(first["evaluated_at"]), project_id=ids.get(repo, repo),
            kind="ruling", request=f"影子合并官判定 {repo}#{pr}@{sha[:7]}：{last['title']}", actor="Hermes",
            auth_tier="A", auth_source="常设授权清单 A 档：影子合并官判定（#61）", decision="执行", result="调用成功",
            verification=f"最后一次判定（{stamp(last['evaluated_at'])}，规则 v{last['rules_version']}，{last['tier']}）："
                         f"{last['suggestion']}{why}；这个版本到统计日结束共判定 {len(rs)} 次",
            follow_up="常浩决定合不合并" if last["suggestion"] == "问常浩" or last["tier"] == "L3" else None), None))
    for (repo, pr, sha, owner), r in owners.items():
        if not when(r["owner_at"], w, notes, f"agreement.csv {repo}#{pr} 的 owner_at"):
            continue
        merge = owner == "合并"
        if merge and r.get("outcome") == "合并":
            result, proof = "业务已验证", f"GitHub 结局：已合并（{r['outcome_at']}，{(r.get('outcome_head') or '')[:7]}）"
        elif merge:
            result, proof = "不明", f"agreement.csv 记下的 GitHub 结局：{r.get('outcome') or '还没有'}"
        else:
            result, proof = "调用成功", "merge_act 在 PR 留言成功后才记 owner 列"
        out.append((act(
            action_id=f"owner:{repo}#{pr}@{sha[:12]}:{owner}", ts=stamp(r["owner_at"]), project_id=ids.get(repo, repo),
            kind="merge" if merge else "other", request=f"常浩点 PR 卡片「{owner}」：{repo}#{pr}@{sha[:7]}",
            actor="Hermes", auth_tier="B", auth_source=f"PR 卡片 {repo}#{pr}@{sha[:7]}（agreement.csv owner 列）",
            decision="执行", result=result, verification=proof), REQUIRED["merge"] if merge else None))
    return out


RUN_DIR = re.compile(r"^(.+)-(\d+)-([0-9a-f]{7})-(\d{8}-\d{6})$")


def merge_runs(w, notes):
    """merge_act 合并前在干净 worktree 跑的测试（merge-runs/<仓库>-<PR>-<版本>-<时间>/test.log）。
    test.log 不记退出码，结果记「不明」，摘最后一行；测试没过时 merge_act 不合并，看 owner 那条有没有合并结局。"""
    if not MERGE_RUNS.exists():
        raise Fail(f"找不到 {MERGE_RUNS}")
    ids, out = roster_ids(), []
    for d in sorted(MERGE_RUNS.iterdir()):
        if not d.is_dir():
            continue
        m = RUN_DIR.match(d.name)
        if not m:
            notes.append(f"merge-runs 里认不出的目录：{d.name}")
            continue
        name, pr, sha, when = m.groups()
        ts = stamp(dt.datetime.strptime(when, "%Y%m%d-%H%M%S"))
        if not inside(ts, w):
            continue
        log = d / "test.log"
        last = (log.read_text(encoding="utf-8", errors="replace").strip().splitlines() or ["（空）"])[-1] if log.exists() else "（没有 test.log）"
        out.append((act(
            action_id=f"merge-run:{d.name}", ts=ts, project_id=ids.get(name, name), kind="merge",
            request=f"合并前在干净 worktree 跑测试：{name}#{pr}@{sha}", actor="Hermes", auth_tier="B",
            auth_source=f"PR 卡片 {name}#{pr}@{sha}", decision="执行", result="不明",
            verification=f"日志 {log}；最后一行：{quote(last, 120)}（test.log 不记退出码）"), None))
    return out


def dispatch(w, notes):
    """派单台账 ~/.hermes/dispatch/<单号>/：order.json（单子）+ out/receipt.json（回执）。
    回执里有 refused 的记「拒绝」；只读单是 A 档，写入单的授权来源没接入，档位记 null。"""
    root = progress_data.DISPATCH
    if not root.exists():
        raise Fail(f"找不到 {root}")
    out = []
    for d in sorted(root.iterdir()):
        f = d / "order.json"
        if not f.exists():
            continue
        try:
            order = json.loads(f.read_text(encoding="utf-8"))
            ts = stamp(order.get("created") or order.get("run_started"), "派单时间")
        except (ValueError, OSError) as e:
            notes.append(f"派单 {d.name} 跳过：{e}")
            continue
        if not inside(ts, w):
            continue
        rf = d / "out" / "receipt.json"
        receipt = None
        try:
            receipt = json.loads(rf.read_text(encoding="utf-8")) if rf.exists() else None
        except ValueError:
            notes.append(f"派单 {d.name} 的回执不是 JSON，当成还没有回执")
        task = d / "task.md"
        what = next((x.strip() for x in task.read_text(encoding="utf-8").splitlines() if x.strip()), "") if task.exists() else ""
        tool = (receipt or {}).get("executor") or {"claude": "Claude", "codex": "Codex"}.get(order.get("tool"), order.get("tool"))
        mode = order.get("mode")
        tier, source = ("A", "常设授权清单 A 档：派只读单") if mode == "read" else (None, f"派单 {d.name}：写入单的授权来源没接入")
        refused = (receipt or {}).get("refused")
        follow = None
        if refused:
            kind, actor, decision, result = "refuse", "Hermes", "拒绝", None
            proof = f"回执 refused={refused}：{receipt.get('reason')}"
        elif receipt is None:
            kind, actor, decision = "dispatch", tool, "执行"
            result, proof = "不明", f"还没有回执（单子状态 {order.get('status')}）"
        else:
            kind, actor, decision = "dispatch", tool, "执行"
            bad = receipt.get("timed_out") or receipt.get("worker_error") or receipt.get("exit_code") not in (0, "0")
            result = "失败" if bad else "调用成功"
            proof = (f"回执：退出码 {receipt.get('exit_code')}，超时 {bool(receipt.get('timed_out'))}，"
                     f"提交 {len(receipt.get('committed_files') or [])} 个文件，未提交 {len(receipt.get('uncommitted') or [])} 处")
            if receipt.get("guard_main_changed") or receipt.get("guard_remote_changed"):
                proof += "；护栏：主分支或远端被改动"
                follow = "核对主分支、远端的改动"
            if receipt.get("need_decision"):
                follow = f"常浩决定：{quote(str(receipt['need_decision']), 120)}"
        out.append((act(
            action_id=f"dispatch:{d.name}", ts=ts, project_id=order.get("project"), kind=kind,
            request=f"Hermes 派单 {d.name}（{tool}，{'只读' if mode == 'read' else mode}）：{quote(what, 80)}",
            actor=actor, auth_tier=tier, auth_source=source, decision=decision, result=result, verification=proof,
            follow_up=follow), None))
    return out


def cron(w, notes):
    """Hermes 定时任务库 ~/.hermes/cron/executions.db：每次执行一条（completed 只算「调用成功」，不算业务做成）；
    cron_incidents 故障单首次出现在统计日内的，各一条。定时任务照登记的跑，不另查授权，不标 auth_gap。"""
    db = CRON / "executions.db"
    if not db.exists():
        raise Fail(f"找不到 {db}")
    try:
        raw = json.loads((CRON / "jobs.json").read_text())
        jobs = {j["id"]: j.get("name") or j["id"] for j in (raw["jobs"] if isinstance(raw, dict) else raw)}
    except (OSError, ValueError, KeyError, TypeError) as e:
        jobs = {}
        notes.append(f"定时任务名读不了（jobs.json：{e}），只写任务号")
    # immutable=1：只读快照，不在 Hermes 的目录里建 -wal、-shm（库是 WAL 模式，mode=ro 打不开）；读到的最多晚一次写入
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro&immutable=1", uri=True)
    try:
        execs = con.execute("select id, job_id, status, claimed_at, delivery_outcome, error from executions").fetchall()
        incs = con.execute("select id, job_id, state, failure_type, first_seen_at, last_seen_at, alerted_at, error "
                           "from cron_incidents").fetchall()
    finally:
        con.close()
    status_map = {"completed": "调用成功", "failed": "失败", "running": "已发起", "claimed": "已发起"}
    out = []
    for eid, job, status, claimed, delivery, error in execs:
        if not when(claimed, w, notes, f"定时任务执行 {eid}"):
            continue
        proof = f"状态 {status}，投递 {delivery or '没记'}"
        if error:
            proof += f"；错误：{quote(error.splitlines()[0], 120)}"
        out.append((act(
            action_id=f"cron:{eid}", ts=stamp(claimed), project_id="hermes", kind="cron",
            request=f"定时任务「{jobs.get(job, job)}」（{job}）", actor="Hermes", auth_tier=None,
            auth_source=f"已登记的定时任务 {job}", decision="执行", result=status_map.get(status, "不明"),
            verification=proof), None))
    for iid, job, state, ftype, first, last, alerted, error in incs:
        if not when(first, w, notes, f"定时任务故障单 {iid}"):
            continue
        out.append((act(
            action_id=f"cron-incident:{iid}", ts=stamp(first), project_id="hermes", kind="supervise",
            request=f"定时任务故障单「{jobs.get(job, job)}」（{job}，{ftype}）", actor="Hermes", auth_tier="A",
            auth_source="常设授权清单 A 档：只读巡检", decision="升级给常浩" if alerted else "执行",
            result=None if alerted else "调用成功",
            verification=f"故障单状态 {state}，首次 {stamp(first)}，最近 {last}"
                         + (f"；错误：{quote((error or '').splitlines()[0], 120)}" if error else ""),
            follow_up=None if state in ("closed", "resolved") else "处理这张故障单"), None))
    return out


def night(w, notes):
    """夜跑 runs/<夜跑>/run-log.jsonl（C 档，单次授权）：approve、start、advance、run（子单）、stopped、release 各记一条；
    旧格式（没有 event 字段，一行一个子单）按 run 记。停机按同一夜的 stop_readback 回读结论算有没有验证。"""
    if not NIGHT_ROOT.exists():
        raise Fail(f"找不到 {NIGHT_ROOT}")
    out = []
    for log in sorted(NIGHT_ROOT.glob("*/run-log.jsonl")):
        name, rows = log.parent.name, []
        for i, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    rows.append((i, json.loads(line)))
                except ValueError:
                    notes.append(f"夜跑 {name} 第 {i} 行不是 JSON，跳过")
        start = next((r for _, r in rows if r.get("event") == "start"), {})
        auth_by = start.get("approved_by")
        readback = next((r for _, r in rows if r.get("event") == "stop_readback"), None)
        for i, r in rows:
            ev = r.get("event") or "run"
            ts = when(r.get("ts") or r.get("completed_at") or r.get("created_at"), w, notes, f"夜跑 {name} 第 {i} 行")
            if not ts:
                continue
            base = dict(action_id=f"night:{name}:{i}", ts=ts, project_id="hermes", actor="Hermes", decision="执行",
                        auth_tier="C" if auth_by else None, auth_source=auth_by or "这一夜没有 start 记录的批准人")
            required = "C"
            if ev == "approve":
                rec = act(**{**base, "kind": "supervise", "auth_tier": "C" if r.get("by") else None,
                             "auth_source": r.get("by") or "没写批准人", "result": "调用成功",
                             "request": f"夜跑 {name} 记下开跑时刻 {r.get('start_at')}（上限 {r.get('max_runs')} 单 / {r.get('hours')} 小时）"})
            elif ev == "start":
                rec = act(**{**base, "kind": "supervise", "result": "已发起", "request": f"夜跑 {name} 开跑",
                             "verification": f"截止 {r.get('deadline')}，上限 {json.dumps(r.get('limits'), ensure_ascii=False)}"})
            elif ev == "advance":
                rec = act(**{**base, "kind": "supervise", "result": "调用成功", "request": f"夜跑 {name} 推进到第 {r.get('stage')} 段"})
            elif ev == "run":
                status, issue_status = r.get("status"), r.get("issue_status")
                if "event" not in r:      # 旧格式：没有夜跑批准记录，发起人写在 initiator
                    base.update(auth_tier=None, auth_source=r.get("initiator") or "没写发起人")
                    required = None
                proof = f"运行状态 {status}，子单状态 {issue_status}"
                if r.get("pr_url"):
                    proof += f"，PR {r['pr_url']}"
                if r.get("error"):
                    proof += f"；错误：{quote(str(r['error']), 120)}"
                rec = act(**{**base, "kind": "dispatch", "actor": "Multica 执行器",
                             "request": f"夜跑 {name} 子单 {r.get('issue')}（第 {r.get('stage')} 段，第 {r.get('attempt')} 次）",
                             "result": {"completed": "调用成功", "failed": "失败"}.get(status, "不明"), "verification": proof,
                             "follow_up": "常浩处理受阻的子单" if issue_status == "blocked" else None})
            elif ev == "stopped":
                ok = (readback or {}).get("ok")
                rec = act(**{**base, "kind": "supervise", "request": f"夜跑 {name} 停机：{r.get('outcome')}",
                             "result": "业务已验证" if ok is True else "失败" if ok is False else "不明",
                             "verification": f"原因 {json.dumps(r.get('reasons'), ensure_ascii=False)}；停机回读 "
                                             + ("通过" if ok is True else f"没过：{readback.get('errors')}" if ok is False else "没有记录")})
            elif ev == "release":
                rec = act(**{**base, "kind": "supervise", "auth_tier": None, "auth_source": r.get("by") or "没写由谁解除",
                             "result": "调用成功", "request": f"夜跑 {name} 解除冻结"})
                required = None
            else:                 # plan、stop_begin、stop_readback、proc_*、remote_snapshot 是过程记录，并进上面几条
                continue
            out.append((rec, required))
    return out


def rulings(w, notes):
    """证据目录里 Hermes 的裁决原文 evidence/**/hermes-*.md：自由文字，不判断批准还是拒绝（decision、result 记「不明」），
    只摘首段原文。文件名里带「授权」的是授权记录，kind 记 other。时间取文件修改时间（文件里没写时间）。"""
    if not EVIDENCE.exists():
        raise Fail(f"找不到 {EVIDENCE}")
    out = []
    for f in sorted(EVIDENCE.rglob("hermes-*.md")):
        ts = stamp(dt.datetime.fromtimestamp(f.stat().st_mtime, TZ))
        if not inside(ts, w):
            continue
        title = f.stem.split("-", 2)[-1] if f.stem.count("-") >= 2 else f.stem
        para = next((p for p in f.read_text(encoding="utf-8", errors="replace").split("\n\n") if p.strip()), "")
        out.append((act(
            action_id=f"ruling:{f.relative_to(EVIDENCE)}", ts=ts, project_id="hermes",
            kind="other" if "授权" in title else "ruling", request=f"Hermes 裁决记录「{title}」（{f.parent.name}）",
            actor="Hermes", auth_tier=None, auth_source=str(f.relative_to(EVIDENCE)), decision="不明", result="不明",
            verification=f"首段原文：{quote(para)}（时间取文件修改时间；批准还是拒绝不从文字判断）"), None))
    return out


SOURCES = (("合并官", merge_officer), ("merge-runs", merge_runs), ("派单台账", dispatch),
           ("定时任务库", cron), ("夜跑", night), ("裁决文件", rulings))


def collect(day):
    w = pdaily_data.window(day)
    notes, coverage, seen, actions = [], {}, set(), []
    for name, fn in (("actions.jsonl", from_file), *SOURCES):
        try:
            items = fn(w, notes)
        except (Fail, OSError, ValueError, KeyError, sqlite3.Error) as e:
            coverage[name] = {"ok": False, "count": None, "error": str(e)}
            continue
        n = 0
        for rec, required in items:
            if rec["action_id"] in seen:   # actions.jsonl 先读：以后脚本直接写的同一动作，以文件为准
                continue
            seen.add(rec["action_id"])
            actions.append({**rec, "origin": name, "auth_gap": gap(rec, required)})
            n += 1
        coverage[name] = {"ok": True, "count": n, "error": None}
    actions.sort(key=lambda a: (pdaily_data.parse_ts(a["ts"]), a["action_id"]))
    return {"stat_day": day.isoformat(), "window": [w[0].isoformat(), w[1].isoformat()],
            "fetched_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
            "actions": actions, "coverage": coverage, "notes": notes}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "record":
        try:
            if len(sys.argv) != 3:
                raise ValueError("用法：actions.py record '<JSON>'")
            fields = json.loads(sys.argv[2])
            if not isinstance(fields, dict):
                raise ValueError("要一个 JSON 对象")
            print(json.dumps(record(**fields), ensure_ascii=False))
        except (ValueError, OSError) as e:
            print(f"没写：{e}")
            raise SystemExit(1)
        return
    p = argparse.ArgumentParser(description="代理行动记录：统计日内的行动（actions.jsonl + 六个来源现读现转）")
    p.add_argument("--date", help="统计日 YYYY-MM-DD（D 04:00 到 D+1 04:00），默认最近一个已经结束的统计日")
    a = p.parse_args()
    try:
        day = dt.date.fromisoformat(a.date) if a.date else pdaily_data.default_day()
    except ValueError:
        print(f"取数失败：日期写法不对：{a.date}")
        raise SystemExit(1)
    print(json.dumps(collect(day), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
