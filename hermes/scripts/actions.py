"""代理行动记录（#110 批 1b）：actions.jsonl 的结构、记录函数，以及把现有七个来源转成同一结构的适配器（只读、只用标准库）。

规格：docs/个人项目日报-V2-设计.md 5.4（结构，定稿后不再改）、第 2 节和 docs/常设授权清单.md（授权档位）、第 6 节（证据）。
用法：
  python3 actions.py [--date 统计日]     打印统计日（D 04:00 到 D+1 04:00，Asia/Shanghai）内的行动，JSON
  python3 actions.py record '<JSON>'      往 actions.jsonl 追加一条行动记录，打印写下的那条
约定（常浩 2026-11-09 定）：
  - 历史不迁移：七个来源每次取数时现读现转，不写进 actions.jsonl；actions.jsonl 只放以后各脚本用 record() 直接写的记录。
  - 自由文字不下结论：裁决文件（evidence/**/hermes-*.md）的 decision、result 记「不明」，只摘首段原文；
    只有结构化来源（派单单子和回执、合并官记录的 owner 列）才记「拒绝」「执行」「升级给常浩」。
  - 安装（kind=install）只认 record() 写下的记录，不从证据目录、CURRENT 的文字里猜。
授权档位（auth_gap）：执行了、但档位低于这类动作应有的档位，或档位不知道，就标出来。应有档位按授权清单：
  卡片合并 B；按策略合并 A（#257，ADR-016：授权来源是策略记录）；安装、新建或修改定时任务、写入类派单 D（清单第 4 节：写入派单降到 C 还没开始）；夜跑 C。
  C 档授权文件逐项写明的 D 档动作（清单 5.1），记 auth_tier=D、auth_source 写到「文件路径 第几条」；只写了 C 文件的照样标。
action_id 约定（以后脚本直接写同一个动作时用同样的号，actions.jsonl 的那条为准，适配器那条不再列）：
  shadow:<仓库>#<PR>@<版本前 12 位>、shadow-backfill:<统计日>、owner:<仓库>#<PR>@<版本前 12 位>:<合并|退回>、policy:<仓库>#<PR>@<版本前 12 位>、merge-run:<目录名>、
  dispatch:<单号>:open（开单）、dispatch:<单号>:queued（写入单确认后排队、还没开跑）、dispatch:<单号>（开跑）、  cron:<执行号>、cron-job:<任务号>、cron-incident:<故障单号>:<日期>、night:<夜跑>:<行号>、ruling:<证据目录下的路径>。
取不到的来源在 coverage 里记 ok=false、count=null 和原因，不当成 0；来源里个别坏行跳过并写进 notes，不拖垮整个来源。
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
MERGE_BOOK = Path(os.environ.get("DT_MERGE_DIR", f"{DT_HOME}/merge")).expanduser() / "auto-merges.jsonl"   # 按策略合并的账本（#257）
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
ROW_ERRORS = (ValueError, TypeError, AttributeError, KeyError, IndexError)   # 来源里一行坏了：跳过这一行
QUOTE_MAX = 400


class Fail(Exception):
    pass


# ---------- 结构与记录 ----------

def stamp(value, what="ts"):
    """带时区的 ISO 时间（北京时间显示）。字符串没写时区的，按北京时间算（派单台账、merge-runs 目录名都是本机时间）。"""
    if not value:
        raise ValueError(f"{what} 没写")
    if isinstance(value, str):
        t = pdaily_data.parse_ts(value)
    elif isinstance(value, dt.datetime):
        t = value
    else:
        raise ValueError(f"{what} 要写成 ISO 时间字符串，收到 {value!r}")
    if t.tzinfo is None:
        t = t.replace(tzinfo=TZ)
    return t.astimezone(TZ).isoformat(timespec="seconds")


def check(rec):
    """一条记录合不合 5.4 的结构；不合就抛 ValueError，写明哪个字段。"""
    if not isinstance(rec, dict):
        raise ValueError("要一个 JSON 对象")
    extra = set(rec) - set(FIELDS)
    if extra:
        raise ValueError(f"不认识的字段：{'、'.join(sorted(extra))}")
    for k in ("action_id", "ts", "kind", "request", "actor", "decision"):
        if not rec.get(k):
            raise ValueError(f"缺字段：{k}")
        if not isinstance(rec[k], str):
            raise ValueError(f"{k} 要是字符串，收到 {rec[k]!r}")
    for k, allowed in (("kind", KINDS), ("decision", DECISIONS)):
        if rec[k] not in allowed:
            raise ValueError(f"{k} 只能是 {' / '.join(allowed)}，收到「{rec[k]}」")
    for k in ("project_id", "auth_source", "verification", "follow_up"):
        if rec.get(k) is not None and not isinstance(rec[k], str):
            raise ValueError(f"{k} 要是字符串或 null，收到 {rec[k]!r}")
    if rec.get("result") is not None and rec["result"] not in RESULTS:
        raise ValueError(f"result 只能是 {' / '.join(RESULTS)} 或 null，收到「{rec['result']}」")
    if rec.get("auth_tier") is not None and rec["auth_tier"] not in TIERS:
        raise ValueError(f"auth_tier 只能是 A / B / C / D 或 null，收到「{rec['auth_tier']}」")
    if rec["decision"] == "执行" and rec.get("result") is None:
        raise ValueError("decision 是「执行」时 result 不能空")
    return {k: rec.get(k) for k in FIELDS}


def record(**fields):
    """往 actions.jsonl 追加一条。action_id、ts 不给（或给 null）就自动生成；时间没写时区按北京时间。返回写下的那条。"""
    fields = dict(fields)
    fields["ts"] = stamp(fields.get("ts") or dt.datetime.now(TZ))
    if not fields.get("action_id"):
        fields["action_id"] = f"{fields.get('kind') or 'other'}-{uuid.uuid4().hex[:12]}"
    rec = check(fields)
    ACTIONS.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    with open(ACTIONS, "a+b") as f:   # 几个脚本同时写：加锁，一次写一整行
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0, os.SEEK_END)
        if f.tell():                  # 上一个写的人被杀、留下没换行的半行：先补换行，别让这一条接在坏行后面
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                data = b"\n" + data
        f.write(data)
    return rec


def gap(rec, required):
    """执行了、但授权档位低于应有档位（或不知道），返回说明；否则 None。拒绝、升级、不明的不算。"""
    if rec["decision"] != "执行" or not required:
        return None
    have = rec.get("auth_tier")
    if have and TIERS.index(have) >= TIERS.index(required):
        return None
    return f"授权不足：这类动作要 {required} 档，记录是{f' {have} 档' if have else '「不知道」'}"


def inside(ts, w):
    return w[0] <= pdaily_data.parse_ts(ts) < w[1]


def when(value, w, notes, where):
    """来源里一行的时间：在统计日内返回规范写法，不在返回 None；没写或写法不对记进 notes 并跳过这一行。"""
    try:
        ts = stamp(value)
    except ROW_ERRORS as e:
        notes.append(f"{where} 时间认不出（{value!r}：{e}），跳过")
        return None
    return ts if inside(ts, w) else None


def from_file(w, notes):
    """actions.jsonl 里统计日内的记录；坏行跳过并写进 notes。文件不存在 = 还没有人写过，是 0 条。"""
    if not ACTIONS.exists():
        return []
    out, seen = [], set()
    for i, line in enumerate(ACTIONS.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = check(json.loads(line))
            rec["ts"] = stamp(rec["ts"])
            dup = rec["action_id"] in seen
        except ROW_ERRORS as e:
            notes.append(f"actions.jsonl 第 {i} 行跳过：{e}")
            continue
        if dup:
            notes.append(f"actions.jsonl 第 {i} 行和前面的 action_id 重复（{rec['action_id']}），只用第一条")
            continue
        seen.add(rec["action_id"])
        if inside(rec["ts"], w):
            out.append((rec, REQUIRED.get(rec["kind"])))
    return out


# ---------- 适配器（历史来源，现读现转） ----------

def quote(text, n=QUOTE_MAX):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + "…（后文截断）"


def who(text):
    """「Claude 会话 local_… 执行」「Hermes（常浩开跑授权）」这类记录开头写的是谁；认不出记「不明」。"""
    m = re.match(r"\s*(Claude|Codex|Hermes)", text or "")
    return m.group(1) if m else "不明"


def roster_ids():
    """名册：GitHub 仓库 → 项目 id；仓库短名 → 项目 id。读不了就只用原名。"""
    try:
        projects = json.loads(progress_data.ROSTER.read_text())["projects"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    ids = {}
    for p in projects:
        if isinstance(p, dict) and p.get("id"):
            if p.get("github"):
                ids[p["github"]] = ids[p["github"].split("/")[-1]] = p["id"]
            ids.setdefault(p["id"], p["id"])
    return ids


def act(**kw):
    return {k: kw.get(k) for k in FIELDS}


def merge_officer(w, notes):
    """合并官 shadow/agreement.csv：
    - 影子判定（A 档）：同一个「PR + 版本」只算一条，时间取第一次判定，内容取统计日结束前的最后一次（V2 第 6 节去重）；
      判「问常浩」或 L3 的，只有这个 PR 还没有结局、常浩还没点过这个版本的卡片、而且这是它最新的版本，
      才写「常浩决定」的后续（结局、点击都按现在的记录）；
    - 历史回填（source=backfill，事后判定，不计入放权门槛）：统计日内的合成一条，写明条数，不逐条列、不带后续；
    - owner 列（常浩点卡片的「合并」「退回」，B 档）：merge_act 回读到 PR 已合并、或留言成功后才写这一列。"""
    ledger = SHADOW / "agreement.csv"
    if not ledger.exists():
        raise Fail(f"找不到 {ledger}")
    with ledger.open(encoding="utf-8", errors="replace", newline="") as f:
        rows = list(csv.DictReader(f))
    ids, out, versions, owners, backfill, latest, decided = roster_ids(), [], {}, {}, [], {}, set()
    for i, r in enumerate(rows, 2):
        try:
            if not (r.get("repo") and r.get("pr") and r.get("head_sha")):
                raise ValueError("仓库、PR 号或版本没写")
            key = (r["repo"], r["pr"], r["head_sha"])
            r["_at"] = pdaily_data.parse_ts(stamp(r.get("evaluated_at")))
        except ROW_ERRORS as e:
            notes.append(f"agreement.csv 第 {i} 行跳过：{e}")
            continue
        if r.get("source") == "backfill":
            if w[0] <= r["_at"] < w[1]:
                backfill.append(r)
        elif r["_at"] < w[1]:
            versions.setdefault(key, []).append(r)
            pr = key[:2]
            if pr not in latest or r["_at"] >= latest[pr][0]:
                latest[pr] = (r["_at"], key[2])
        if r.get("owner") and r.get("owner_at"):
            decided.add(key)
            if r["owner"] != POLICY_OWNER:           # 按策略合并的在 policy_merges 里记（账本更全），这里不重复
                owners.setdefault(key + (r["owner"],), r)
    for (repo, pr, sha), rs in versions.items():
        rs.sort(key=lambda r: r["_at"])
        first, last = rs[0], rs[-1]
        if not w[0] <= first["_at"] < w[1]:
            continue
        why = f"：{last['reasons']}" if last.get("reasons") else ""
        ending = next((r for r in reversed(rs) if r.get("outcome")), None)
        proof = (f"最后一次判定（{stamp(last['_at'])}，规则 v{last.get('rules_version')}，{last.get('tier')}）："
                 f"{last.get('suggestion')}{why}；这个版本到统计日结束共判定 {len(rs)} 次")
        if ending:
            proof += f"；PR 结局：{ending['outcome']}（{ending.get('outcome_at')}）"
        ask = last.get("suggestion") == "问常浩" or last.get("tier") == "L3"
        out.append((act(
            action_id=f"shadow:{repo}#{pr}@{sha[:12]}", ts=stamp(first["_at"]), project_id=ids.get(repo, repo),
            kind="ruling", request=f"影子合并官判定 {repo}#{pr}@{sha[:7]}：{last.get('title')}", actor="Hermes",
            auth_tier="A", auth_source="常设授权清单 A 档：影子合并官判定（#61）", decision="执行", result="调用成功",
            verification=proof,
            follow_up="常浩决定合不合并" if ask and not ending and (repo, pr, sha) not in decided
                                                and latest[(repo, pr)][1] == sha else None), None))
    if backfill:
        backfill.sort(key=lambda r: r["_at"])
        repos = sorted({r["repo"] for r in backfill})
        out.append((act(
            action_id=f"shadow-backfill:{w[0].date().isoformat()}", ts=stamp(backfill[0]["_at"]), project_id="hermes",
            kind="ruling", request=f"合并官回填历史 PR 判定 {len(backfill)} 条（{len(repos)} 个仓库，事后判定）", actor="Hermes",
            auth_tier="A", auth_source="常设授权清单 A 档：影子合并官判定（#61）", decision="执行", result="调用成功",
            verification=f"仓库：{'、'.join(repos)}；回填看不到当时的检查和冲突状态，一致率单列，不计入放权门槛"), None))
    for (repo, pr, sha, owner), r in owners.items():
        ts = when(r["owner_at"], w, notes, f"agreement.csv {repo}#{pr} 的 owner_at")
        if not ts:
            continue
        merge = owner == "合并"
        if merge:
            proof = "merge_act 回读到 PR 已合并后才写 owner 列"
            if r.get("outcome"):
                proof += f"；GitHub 结局：{r['outcome']}（{r.get('outcome_at')}，{(r.get('outcome_head') or '')[:7]}）"
        else:
            proof = "merge_act 在 PR 留言成功后才写 owner 列"
        out.append((act(
            action_id=f"owner:{repo}#{pr}@{sha[:12]}:{owner}", ts=ts, project_id=ids.get(repo, repo),
            kind="merge" if merge else "other", request=f"常浩点 PR 卡片「{owner}」：{repo}#{pr}@{sha[:7]}",
            actor="Hermes", auth_tier="B", auth_source=f"PR 卡片 {repo}#{pr}@{sha[:7]}（agreement.csv owner 列）",
            decision="执行", result="业务已验证" if merge else "调用成功", verification=proof),
            REQUIRED["merge"] if merge else None))
    return out


POLICY_OWNER = "策略合并"      # agreement.csv 的 owner 列：merge_act 按策略合并的（同 merge_officer.POLICY_OWNER）


def policy_book(w):
    """按策略合并的账本（merge/auto-merges.jsonl，#257）在统计日内的三样，给行动记录和日报「替你合并了什么」用：
    merged：合上的（时间取记结果的那一行）；unsettled：发了合并命令、结果还不明的；
    bypass：PR 已合并、账本里却没有合并执行器记录的（会话或网页上直接合的），按合并官发现的时间算。"""
    import merge_policy as MP            # 只用它读账本；装了日报没装它时，这个来源记「取不到」，别的来源照常
    out = {"merged": [], "unsettled": [], "bypass": []}
    for m in MP.merges(MERGE_BOOK):
        name = m["repo"].split("/")[-1]
        base = {"repo": m["repo"], "pr": str(m["pr"]), "title": m.get("title"), "url": m.get("url"), "head": m.get("head"),
                "tier": m.get("tier"), "suggestion": m.get("suggestion"), "reasons": m.get("reasons") or "",
                "ready_by": (m.get("ready") or {}).get("by"), "id": m["id"], "auth": m.get("auth") or {}}
        if m["status"] == "merged" and inside(stamp(m["done_at"]), w):
            out["merged"].append({**base, "at": stamp(m["done_at"]), "merge_commit": m.get("merge_commit"),
                                  "revert_cmd": f"撤回 {name}#{m['pr']}", "revert": m.get("revert")})
        elif m["status"] in ("pending", "unsure") and pdaily_data.parse_ts(stamp(m["at"])) < w[1]:
            out["unsettled"].append({**base, "at": stamp(m["at"])})
    for r in MP.lines(MERGE_BOOK)[0]:
        if r["event"] == "bypass" and inside(stamp(r["at"]), w):
            out["bypass"].append({k: r.get(k) for k in ("repo", "pr", "title", "head", "tier", "suggestion", "merged_at")}
                                 | {"at": stamp(r["at"])})
    return out


def policy_merges(w, notes):
    """按策略合并（#257，A 档：做完在日报里报；授权来源是策略记录 owner_approved_policy，不是逐张卡片）：
    合上的各一条 merge、「业务已验证」（合并执行器回读到已合并才记）；结果不明的一条 merge、结果「不明」，带后续。
    没合上的不算行动；没经合并执行器的合并不是 Hermes 的动作，不记在这里（在事实快照的 auto_merges.bypass）。"""
    if not MERGE_BOOK.exists():
        return []
    ids, out = roster_ids(), []
    book = policy_book(w)
    for m, done in [(m, True) for m in book["merged"]] + [(m, False) for m in book["unsettled"]]:
        auth = m["auth"]
        source = (f"合并策略记录 owner_approved_policy（范围 {str(auth.get('scope_sha256'))[:12]}，"
                  f"{str(auth.get('signed_at'))[:10]} 签，消息 {auth.get('message_id')}）")
        judged = f"合并官判定 {m['tier']} {m['suggestion']}" + (f"（{m['reasons']}）" if m["reasons"] else "")
        ref = f"{m['repo']}#{m['pr']}@{str(m['head'])[:7]}"
        if done:
            proof = (f"回读已合并，合并提交 {str(m['merge_commit'])[:7]}；{judged}；{m['ready_by'] or '未注明'}交出；"
                     f"审查结论未核实；撤回口令「{m['revert_cmd']}」")
            if m["revert"]:
                proof += f"；已开撤回 PR {m['revert'].get('revert_url')}"
        else:
            proof = f"合并命令已发出，回读没成功；{judged}"
        out.append((act(
            action_id=f"policy:{m['repo']}#{m['pr']}@{str(m['head'])[:12]}", ts=m["at"], project_id=ids.get(m["repo"], m["repo"]),
            kind="merge", request=f"按策略合并 {ref}：{m['title']}", actor="Hermes", auth_tier="A", auth_source=source,
            decision="执行", result="业务已验证" if done else "不明", verification=proof,
            follow_up=None if done else "合并官下一轮巡视只读核对，不重发"), "A"))
    return out


RUN_DIR = re.compile(r"^(.+)-(\d+)-([0-9a-f]{7})-(\d{8}-\d{6})$")


def merge_runs(w, notes):
    """merge_act 合并前在干净 worktree 跑的测试（merge-runs/<仓库>-<PR>-<版本>-<时间>/test.log；#190 起不再在本机跑，只读历史），记 supervise：
    合并本身看 owner 那条，这里不再算一次合并。test.log 不记退出码，结果记「不明」，摘最后一行。"""
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
        name, pr, sha, at = m.groups()
        try:
            ts = stamp(dt.datetime.strptime(at, "%Y%m%d-%H%M%S"))
        except ValueError as e:
            notes.append(f"merge-runs 目录 {d.name} 的时间认不出：{e}")
            continue
        if not inside(ts, w):
            continue
        log = d / "test.log"
        last = ((log.read_text(encoding="utf-8", errors="replace").strip().splitlines() or ["（空）"])[-1]
                if log.exists() else "（没有 test.log）")
        out.append((act(
            action_id=f"merge-run:{d.name}", ts=ts, project_id=ids.get(name, name), kind="supervise",
            request=f"合并前在干净 worktree 跑测试：{name}#{pr}@{sha}", actor="Hermes", auth_tier="B",
            auth_source=f"PR 卡片 {name}#{pr}@{sha}", decision="执行", result="不明",
            verification=f"日志 {log}；最后一行：{quote(last, 120)}（test.log 不记退出码）"), None))
    return out


def dispatch(w, notes):
    """派单台账 ~/.hermes/dispatch/<单号>/：order.json（单子）+ out/receipt.json（回执）+ task.md。
    - 开单（时间取开单时间，action_id 加 :open）：写入单要常浩确认，记「升级给常浩」；只读单不用确认，记「已发起」；
      写入单不管当天有没有开跑都记这一条，「升级给常浩」的条数不随确认有没有跨过 04:00 变；
    - 开跑（时间取开跑时间）：running 的记「已发起」，不看可能是上一次留下的回执；其他按回执记，refused 的记「拒绝」；
      跑没跑过看有没有回执或单子上的退出码（clean 会把跑过没合并的单改成 abandoned），没记开跑时间的老单子按开单时间算；
      写入单确认后在排队、还没开跑的（queued、没有开跑时间）也算执行，记「已发起」（action_id 加 :queued）；
    - 只读单 A 档；写入单要 D 档（清单第 4 节），确认记录没接入台账，档位记 null，执行了会标 auth_gap；
    - 续跑会改写单子的开跑时间，之前那次开跑看不到（之前的回执在 out/receipt-N.json，没接）。"""
    root = progress_data.DISPATCH
    if not root.exists():
        raise Fail(f"找不到 {root}")
    out = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        try:
            out.extend(one_order(d, w, notes))
        except ROW_ERRORS + (OSError,) as e:
            notes.append(f"派单 {d.name} 跳过：{e}")
    return out


def one_order(d, w, notes):
    f = d / "order.json"
    if not f.exists():   # 只有 task.md 的残单：task.md 是统计日内写的才提一句，旧残单不天天提
        task = d / "task.md"
        if task.exists() and inside(stamp(dt.datetime.fromtimestamp(task.stat().st_mtime, TZ)), w):
            notes.append(f"派单 {d.name} 只有 task.md、没有 order.json，跳过")
        return []
    order = json.loads(f.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(order, dict):
        raise ValueError("order.json 不是对象")
    status, mode = order.get("status"), order.get("mode")
    created = stamp(order["created"], "开单时间") if order.get("created") else None
    started = stamp(order["run_started"], "开跑时间") if order.get("run_started") else None
    rf = d / "out" / "receipt.json"
    receipt = None
    if rf.exists():
        try:
            receipt = json.loads(rf.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            pass
        if not isinstance(receipt, dict):
            notes.append(f"派单 {d.name} 的回执读不了（不是 JSON 对象），当成还没有回执")
            receipt = None
    ran = bool(started) or receipt is not None or order.get("exit_code") not in (None, "")
    guessed = ran and not started and bool(created)
    if guessed:   # 跑过、但单子没记开跑时间（老单子）：按开单时间算开跑，说明里写明
        started = created
    pending = status == "queued" and not started          # 确认了、在排队，还没开跑
    opened_today = bool(created) and inside(created, w)
    ran_today = bool(started) and inside(started, w)
    if not (opened_today or ran_today):
        return []
    task = d / "task.md"
    what = (next((x.strip() for x in task.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()), "")
            if task.exists() else "")
    tool = {"claude": "Claude", "codex": "Codex"}.get(order.get("tool"), order.get("tool")) or "不明"
    if mode == "read":
        tier, source, required = "A", "常设授权清单 A 档：派只读单", None
    else:
        tier, source, required = None, f"派单 {d.name}：写入单要 D 档，常浩的确认没记进台账", "D"
    req = f"Hermes 派单 {d.name}（{tool}，{'只读' if mode == 'read' else mode}）：{quote(what, 80)}"
    out = []
    if opened_today and (mode != "read" or not ran_today):
        later = f"；{started} 开跑" if started and not guessed else ""
        if mode == "read":
            dec, res, proof = "执行", "已发起", f"开了只读单（不用确认），统计日内没开跑（单子状态 {status}）{later}"
            follow = None
        elif status == "abandoned" and not ran:
            dec, res, proof, follow = "不明", "不明", "开了写入单，没开跑就放弃了", None
        else:
            dec, res, proof = "升级给常浩", None, "开了写入单，等常浩确认开跑" + later
            follow = None if ran or pending else "常浩确认开跑或取消"
        out.append((act(action_id=f"dispatch:{d.name}:open", ts=created, project_id=order.get("project"), kind="dispatch",
                        request=req, actor="Hermes", auth_tier=tier, auth_source=source, decision=dec, result=res,
                        verification=proof, follow_up=follow), None))
    if pending and opened_today and mode != "read":   # 只读单的排队已经记在开单那条里
        out.append((act(action_id=f"dispatch:{d.name}:queued", ts=created, project_id=order.get("project"), kind="dispatch",
                        request=req, actor=tool, auth_tier=tier, auth_source=source, decision="执行", result="已发起",
                        verification="确认了，在排队，还没开跑（单子状态 queued）"), required))
    if not ran_today:
        return out
    kind, actor, decision, follow = "dispatch", (receipt or {}).get("executor") or tool, "执行", None
    refused = (receipt or {}).get("refused")
    if status == "running":
        result, proof = "已发起", "还在跑（单子状态 running），回执可能是上一次留下的，不算"
    elif refused:
        kind, actor, decision, result = "refuse", "Hermes", "拒绝", None
        proof = f"回执 refused={refused}：{receipt.get('reason')}"
    elif receipt is None:
        code = order.get("exit_code")
        result = "不明" if code in (None, "") else "调用成功" if str(code) == "0" else "失败"
        proof = f"没有回执（单子状态 {status}，单子记的退出码 {code if code not in (None, '') else '没记'}）"
    else:
        bad = receipt.get("timed_out") or receipt.get("worker_error") or receipt.get("exit_code") not in (0, "0")
        result = "失败" if bad else "调用成功"
        proof = (f"回执：退出码 {receipt.get('exit_code')}，超时 {bool(receipt.get('timed_out'))}，"
                 f"提交 {len(receipt.get('committed_files') or [])} 个文件，未提交 {len(receipt.get('uncommitted') or [])} 处")
        if receipt.get("guard_main_changed") or receipt.get("guard_remote_changed"):
            proof += "；护栏：主分支或远端被改动"
            follow = "核对主分支、远端的改动"
        if receipt.get("need_decision"):
            follow = f"常浩决定：{quote(str(receipt['need_decision']), 120)}"
    if status == "queued":
        proof += "；现在在排队等重跑"
    if guessed:
        proof += "；开跑时间没记，按开单时间算"
    out.append((act(
        action_id=f"dispatch:{d.name}", ts=started, project_id=order.get("project"), kind=kind, request=req,
        actor=actor, auth_tier=tier, auth_source=source, decision=decision, result=result, verification=proof,
        follow_up=follow), required))
    return out


def cron(w, notes):
    """Hermes 定时任务（~/.hermes/cron）：
    - executions.db 每次执行一条：照登记的任务跑，不另查授权；completed 只算「调用成功」，不算业务做成；
    - jobs.json 里统计日内新建的任务各一条：新建定时任务要 D 档，创建时的授权没接入，档位记 null，会标 auth_gap；
    - cron_incidents 故障单：统计日内首次出现、告警或关闭的，各一条；告警落在当天的才算「升级给常浩」。"""
    db = CRON / "executions.db"
    if not db.exists():
        raise Fail(f"找不到 {db}")
    jobs, out = {}, []
    try:
        raw = json.loads((CRON / "jobs.json").read_text())
        for j in (raw["jobs"] if isinstance(raw, dict) else raw):
            jobs[j["id"]] = j
    except (OSError,) + ROW_ERRORS as e:
        notes.append(f"定时任务名读不了（jobs.json：{e}），只写任务号")
    name = {k: j.get("name") or k for k, j in jobs.items()}
    if Path(f"{db}-wal").exists():
        notes.append("定时任务库有还没合并进主库的写入（executions.db-wal），最近几次执行可能没读到")
    # immutable=1：只读快照，不在 Hermes 的目录里建 -wal、-shm（库是 WAL 模式，mode=ro 打不开）
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro&immutable=1", uri=True)
    try:
        execs = con.execute("select id, job_id, status, claimed_at, delivery_outcome, error from executions").fetchall()
        incs = con.execute("select id, job_id, state, failure_type, first_seen_at, last_seen_at, alerted_at, closed_at, error "
                           "from cron_incidents").fetchall()
    finally:
        con.close()
    status_map = {"completed": "调用成功", "failed": "失败", "running": "已发起", "claimed": "已发起"}
    for eid, job, status, claimed, delivery, error in execs:
        ts = when(claimed, w, notes, f"定时任务执行 {eid}")
        if not ts:
            continue
        proof = f"状态 {status}，投递 {delivery or '没记'}"
        if error:
            proof += f"；错误：{quote(str(error).splitlines()[0], 120)}"
        out.append((act(
            action_id=f"cron:{eid}", ts=ts, project_id="hermes", kind="cron",
            request=f"定时任务「{name.get(job, job)}」（{job}）", actor="Hermes", auth_tier=None,
            auth_source=f"已登记的定时任务 {job}", decision="执行", result=status_map.get(status, "不明"),
            verification=proof), None))
    for jid, j in jobs.items():
        if j.get("created_at") and when(j["created_at"], w, notes, f"定时任务 {jid} 的 created_at"):
            ts = stamp(j["created_at"])
            out.append((act(
                action_id=f"cron-job:{jid}", ts=ts, project_id="hermes", kind="cron",
                request=f"新建定时任务「{name[jid]}」（{jid}，{j.get('schedule_display') or j.get('schedule')}）", actor="Hermes",
                auth_tier=None, auth_source="jobs.json 的 created_at；创建时的授权没接入", decision="执行", result="调用成功",
                verification=f"现在的状态：{'暂停' if j.get('paused_at') else '启用' if j.get('enabled') else '停用'}"), "D"))
    for iid, job, state, ftype, first, last, alerted, closed, error in incs:
        seen_at = [when(x, w, notes, f"定时任务故障单 {iid}") if x else None for x in (first, alerted, closed)]
        hits = [t for t in seen_at if t]
        if not hits:
            continue
        alerted_today = bool(seen_at[1])
        out.append((act(
            action_id=f"cron-incident:{iid}:{w[0].date().isoformat()}", ts=min(hits), project_id="hermes", kind="supervise",
            request=f"定时任务故障单「{name.get(job, job)}」（{job}，{ftype}）", actor="Hermes", auth_tier="A",
            auth_source="常设授权清单 A 档：只读巡检", decision="升级给常浩" if alerted_today else "执行",
            result=None if alerted_today else "调用成功",
            verification=f"故障单状态 {state}，首次 {first}，最近 {last}，告警 {alerted or '没有'}，关闭 {closed or '没有'}"
                         + (f"；错误：{quote(str(error).splitlines()[0], 120)}" if error else ""),
            follow_up=None if state in ("closed", "resolved") else "处理这张故障单"), None))
    return out


def night(w, notes):
    """夜跑 runs/<夜跑>/run-log.jsonl（C 档，单次授权）：approve、start、advance、run（子单）、stopped、release 各记一条；
    旧格式（没有 event 字段，一行一个子单）按 run 记。执行者：有 approve 记录的夜跑由 Hermes 定时任务（tick）拉起，记 Hermes；
    没有的（手工演练）记「不明」——approved_by 写的是批准人，不拿来当执行者；release 的 by 写的就是解除的人，按开头认
    （Claude、Codex、Hermes，认不出记「不明」）；子单的执行者是 Multica 执行器。停机按同一夜的 stop_readback 回读结论算。"""
    if not NIGHT_ROOT.exists():
        raise Fail(f"找不到 {NIGHT_ROOT}")
    out = []
    for log in sorted(NIGHT_ROOT.glob("*/run-log.jsonl")):
        name, rows = log.parent.name, []
        for i, line in enumerate(log.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                r = None
            if not isinstance(r, dict):
                notes.append(f"夜跑 {name} 第 {i} 行不是 JSON 对象，跳过")
                continue
            rows.append((i, r))
        start = next((r for _, r in rows if r.get("event") == "start"), {})
        auth_by = start.get("approved_by")
        readback = next((r for _, r in rows if r.get("event") == "stop_readback"), None)
        actor = "Hermes" if any(r.get("event") == "approve" for _, r in rows) else "不明"
        for i, r in rows:
            try:
                item = night_row(name, i, r, w, notes, auth_by, readback, actor)
            except ROW_ERRORS as e:
                notes.append(f"夜跑 {name} 第 {i} 行跳过：{e}")
                continue
            if item:
                out.append(item)
    return out


def night_row(name, i, r, w, notes, auth_by, readback, actor):
    ev = r.get("event") or "run"
    if ev not in ("approve", "start", "advance", "run", "stopped", "release"):
        return None           # plan、stop_begin、stop_readback、proc_*、remote_snapshot 是过程记录，并进这几条
    ts = when(r.get("ts") or r.get("completed_at") or r.get("created_at"), w, notes, f"夜跑 {name} 第 {i} 行")
    if not ts:
        return None
    base = dict(action_id=f"night:{name}:{i}", ts=ts, project_id="hermes", actor=actor, decision="执行",
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
    else:                     # release：谁解除的写在 by，不另查授权
        rec = act(**{**base, "kind": "supervise", "actor": who(r.get("by")), "auth_tier": None,
                     "auth_source": r.get("by") or "没写由谁解除", "result": "调用成功", "request": f"夜跑 {name} 解除冻结"})
        required = None
    return rec, required


def rulings(w, notes):
    """证据目录里 Hermes 的裁决原文 evidence/**/hermes-*.md：自由文字，不判断批准还是拒绝，也不按标题分类
    （decision、result 记「不明」，kind 一律 ruling），只摘首段原文。时间取文件修改时间（文件里没写时间）。"""
    if not EVIDENCE.exists():
        raise Fail(f"找不到 {EVIDENCE}")
    out = []
    for f in sorted(EVIDENCE.rglob("hermes-*.md")):
        try:
            ts = stamp(dt.datetime.fromtimestamp(f.stat().st_mtime, TZ))
            if not inside(ts, w):
                continue
            title = f.stem.split("-", 2)[-1] if f.stem.count("-") >= 2 else f.stem
            para = next((p for p in f.read_text(encoding="utf-8", errors="replace").split("\n\n") if p.strip()), "")
        except (OSError,) + ROW_ERRORS as e:
            notes.append(f"裁决文件 {f.name} 跳过：{e}")
            continue
        out.append((act(
            action_id=f"ruling:{f.relative_to(EVIDENCE)}", ts=ts, project_id="hermes", kind="ruling",
            request=f"Hermes 裁决记录「{title}」（{f.parent.name}）", actor="Hermes", auth_tier=None,
            auth_source=str(f.relative_to(EVIDENCE)), decision="不明", result="不明",
            verification=f"首段原文：{quote(para)}（时间取文件修改时间；批准还是拒绝不从文字判断）"), None))
    return out


SOURCES = (("合并官", merge_officer), ("按策略合并", policy_merges), ("merge-runs", merge_runs), ("派单台账", dispatch),
           ("定时任务库", cron), ("夜跑", night), ("裁决文件", rulings))


def collect(day):
    w = pdaily_data.window(day)
    notes, coverage, seen, actions = [], {}, set(), []
    for name, fn in (("actions.jsonl", from_file), *SOURCES):
        try:
            items = fn(w, notes)
        except Exception as e:   # 一个来源出任何错：这个来源记取不到，别的来源照常（V2 第 6 节：写「未知」）
            coverage[name] = {"ok": False, "count": None, "error": f"{type(e).__name__}：{e}"}
            continue
        n = 0
        for rec, required in items:
            if rec["action_id"] in seen:   # actions.jsonl 先读：以后脚本直接写的同一动作，以文件为准
                continue
            seen.add(rec["action_id"])
            actions.append({**rec, "origin": name, "auth_gap": gap(rec, required)})
            n += 1
        coverage[name] = {"ok": True, "count": n, "error": None}
    # 同一时刻：开单（:open）排在排队、开跑前面，按事情发生的顺序
    # action_id 里的数字按大小排（夜跑第 9 行在第 10 行前）
    actions.sort(key=lambda a: (pdaily_data.parse_ts(a["ts"]), not a["action_id"].endswith(":open"),
                                re.sub(r"\d+", lambda m: m.group().zfill(12), a["action_id"])))
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
        except (ValueError, TypeError, OSError) as e:
            print(f"没写：{e}")
            raise SystemExit(1)
        return
    p = argparse.ArgumentParser(description="代理行动记录：统计日内的行动（actions.jsonl + 七个来源现读现转）")
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
