#!/usr/bin/env python3
"""未闭环账本的追问登记与指标（#266 批 5a-4，规格 docs/未闭环账本-规格.md 第 9、10 节）。

常浩每追问一次「这事怎么样了」，就是系统的一次缺陷。这里把每次追问记一行，按当时的账本分三类：
  漏收    问到的事不在当时的账本里：来源没接全
  追问    在账本里，但问之前的午晚单、日报都没出现过：阈值太松，或没送到他面前
  已报过  在账本里、之前报过了：不算缺陷，照样记一行（看得出他是不是没看单子）
记在 DigitalTwin/openloop/asks.jsonl，只追加。

两个入口：
  record  飞书里常浩问猛鬼兽进度时，猛鬼兽先 find 找到账本里的那一行，再 record 记一行（status=确认）
  screen  每周从 Claude 桌面会话记录里按问法粗筛（参照会话复盘找「纠正」的做法：去掉代码块、贴进来的内容、
          宿主注入、引号里的短词，只看短消息），对一遍账本，记成 status=待核；同一句话不重复记
recheck：判法改了以后重判已记的追问，结论不同的追加一行更正（supersedes 指向原来那行；周报只算最新的结论）。
week：一周基线（第 10 节）：你主动追问率，加上现在已经能算的无声中断率、结果证据覆盖率、没核实审查就合并的；
写 DigitalTwin/openloop/weekly/<结束日>.json。

「当时的账本」= 问的时刻之前最近的一份（ledger.json 或 history/<日期>.json；history 每天只留最后一次扫描）。
「报过」= 问之前发出的午晚单列着它（runs.jsonl 记的 ids；旧记录没有 ids 的按卡片文字找），或问之前发出的日报
拍板快照的出处写着它（「未闭环账本 <编号>」，PR 照日报去重的办法按项目和号找），或那天日报的账本风险拿它当了例子，
或发出去的日报卡片上写着「项目名 #号」（PR 才认，如风险表里「PR 开着超过 2 天：数字分身 #210 #211」）。

只读：账本、午晚单、日报、会话记录、合并账本都只读，写的只有 asks.jsonl 和 weekly/。不调 AI，不发飞书。未安装。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import openloop as ol  # noqa: E402

VERDICTS = ("漏收", "追问", "已报过")
DEFECTS = ("漏收", "追问")
SENT = ("sent", "sent_unverified")
SHORT = 120          # 粗筛只看这么短的消息：长消息多是派活、贴材料，里面的「做到哪了」多半不是在追问
FIND_MAX = 10
HASH = re.compile(r"(?<![0-9a-zA-Z])[0-9a-f]{7,40}(?![0-9a-zA-Z])")
# 追问进度的问法（规则偏召回，结果标待核，由人核）
ASK_PATTERNS = [re.compile(p) for p in (
    r"(?:做|进行|推进|弄|改|跑|走)到哪(?:了|一步|儿|里)",
    r"(?:进度|进展)(?:怎么样|如何|咋样|呢|到哪)",
    r"怎么(?:还)?没(?:好|完|做完|弄完|动静|下文|合并|发出?来?|上线|装)",
    r"(?:好|完|做完|弄完|合并|合上|跑完|装好|上线)了(?:吗|没有?|么)",
    r"还(?:差|剩)(?:什么|多少|哪些|几)",
    r"(?:现在|目前)(?:是)?(?:什么|啥)(?:情况|状态)",
    r"怎么样了",
    r"有(?:没有)?(?:进展|下文|结果|动静)(?:了)?(?:吗|没)",
    r"卡在哪",
    # 下面这几种是拿 11-15～11-21 的真会话核出来的漏网问法
    r"(?:汇报|报告|说下|说一下|讲下|讲一下)[^。，,；;]{0,10}(?:进度|进展|完成情况)",
    r"(?:进度|进展)[^。，,；;]{0,6}汇报",
    r"(?:最新|目前|现在|整体)(?:的)?(?:进度|进展)",
    r"什么进度|进度是(?:什么|啥)样",
    r"还有(?:什么|哪些|啥)没(?:做|完|弄)",
    r"完成情况|都(?:完成|做完)了[么吗没]",
    r"(?:不记得|忘了)[^。，,]{0,4}(?:进展|进度|做到哪)",
)]
STRIP = [re.compile(p, re.S) for p in (
    r"```.*?(?:```|$)", r"<pasted_content[^>]*>.*?(?:</pasted_content[^>]*>|$)",
    r"<system-reminder>.*?(?:</system-reminder>|$)", r"[「“\"‘][^」”\"’\n]{1,20}[」”\"’]")]


class Fail(Exception):
    pass


# ---------- 文件 ----------

def asks_path():
    return ol.out_dir() / "asks.jsonl"


def daily_dir():
    return Path(os.environ.get("DT_DAILY", "~/Library/Application Support/DigitalTwin/daily")).expanduser()


def merge_ledger():
    return Path(os.environ.get("DT_MERGE_DIR", "~/Library/Application Support/DigitalTwin/merge")).expanduser() / "auto-merges.jsonl"


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_lines(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if isinstance(x, dict):
                    out.append(x)
    except OSError:
        pass
    return out


def append(rec):
    path = asks_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def projects_or_empty():
    try:
        return ol.roster()
    except Exception:   # 名册读不了只影响「日报里按项目和号找 PR」这一种对法，不拖垮登记
        return []


# ---------- 账本 ----------

def ledgers(folder=None):
    """所有能读的账本：[(扫描时间, 账本)]，按时间排。"""
    folder = folder or ol.out_dir()
    out = []
    for path in [folder / "ledger.json", *sorted((folder / "history").glob("*.json"))]:
        led = read_json(path)
        at = ol.parse_ts((led or {}).get("generated_at"))
        if at and all(at != t for t, _ in out):   # ledger.json 和当天快照常常是同一次扫描，只算一份
            out.append((at, led))
    return sorted(out, key=lambda x: x[0])


def ledger_before(at, folder=None, snaps=None):
    """问的时刻之前最近的一份账本；没有返回 None。snaps 是读好的 ledgers()，一次粗筛只读一遍。"""
    got = [led for t, led in (ledgers(folder) if snaps is None else snaps) if t <= at]
    return got[-1] if got else None


def ids_of(r):
    """一行认哪些编号：自己的、并进来的（不含只记一笔的 related）。"""
    out = {r.get("id")}
    for x in (r.get("links") or []) + (r.get("parts") or []):
        if isinstance(x, dict) and not x.get("related"):
            out.add(x.get("id"))
    return {i for i in out if i}


def locate(led, target):
    """账本里哪一行是这件事：（行, 是不是只计数的）；找不到返回 (None, False)。"""
    for r in led.get("rows") or []:
        if target in ids_of(r):
            return r, False
    for r in led.get("parked") or []:
        if r.get("id") == target:
            return r, True
    return None, False


def target_of(id_=None, pr=None, session=None):
    if id_:
        return id_
    if pr:
        m = re.fullmatch(r"\s*([\w.-]+)\s*#\s*(\d+)\s*", pr)
        if not m:
            raise Fail(f"PR 要写成 仓库#号（如 digital-twin#211），收到的是「{pr}」")
        return f"pr:{m.group(1)}#{m.group(2)}"
    if session:
        return session if session.startswith("session:") else f"session:{session}"
    return None


# ---------- 报过没有 ----------

def needles(r):
    """午晚单旧记录没有 ids 时，按卡片上的字找：PR 写「仓库#号」，别的写「项目｜事」（同 openloop_slip.name）。"""
    out = {f"{m.group(1)}#{m.group(2)}" for i in ids_of(r) for m in [re.match(r"pr:(.+)#(\d+)$", i)] if m}
    if r.get("what"):
        out.add(f"{r.get('project') or '—'}｜{ol.clip(r.get('what'), 26)}")
    return out


def slips_before(r, at):
    hits, folder = [], ol.out_dir() / "slips"
    mine = ids_of(r)
    for rec in read_lines(folder / "runs.jsonl"):
        when = ol.parse_ts(rec.get("at"))
        if rec.get("result") not in SENT or not when or when >= at:
            continue
        if "ids" in rec:
            hit = bool(mine & set(rec.get("ids") or []) | mine & set(rec.get("ids_gone") or []))
        else:
            try:   # 卡片是 ensure_ascii=False 写的，原文就能按字找
                text = (folder / f"{rec.get('slot')}.card.json").read_text(encoding="utf-8")
            except OSError:
                continue
            hit = any(n in text for n in needles(r))
        if hit:
            hits.append({"where": f"{'晚间单' if str(rec.get('slot')).endswith('晚') else '午间单'} {rec.get('slot')}", "at": rec.get("at")})
    return hits


def names_of(p):
    """项目在卡片上可能的叫法：名册全名、括号里的、括号前的（「个人 AI 工作台（数码世界）」→ 三种都认），再加仓库名。"""
    full = str(p.get("name") or "").strip()
    out = {full, ol.key_of(p)}
    m = re.match(r"^(.*?)[（(](.+?)[）)]\s*$", full)
    if m:
        out |= {m.group(1).strip(), m.group(2).strip()}
    return {n for n in out if n}


def card_mentions(text, names, n):
    """卡片上写了「项目名 + 一串 PR 号」且其中有 #n（「数字分身 #173 #187 #211」）。项目名和号之间只许是空白和别的号，
    「数字分身 #173｜自建技能库 #211」里的 #211 不算数字分身的。"""
    return any(re.search(re.escape(name) + r"\s*(?:#\d+[\s、，,]*)*#" + n + r"(?!\d)", text) for name in names)


def daily_before(r, at, projects):
    hits, folder = [], daily_dir()
    mine = ids_of(r)
    roster_id = {ol.key_of(p): p.get("id") for p in projects}
    names = {ol.key_of(p): names_of(p) for p in projects}
    prs = {(roster_id.get(m.group(1)), m.group(2)) for i in mine for m in [re.match(r"pr:(.+)#(\d+)$", i)] if m}
    on_card = {(m.group(1), m.group(2)) for i in mine for m in [re.match(r"pr:(.+)#(\d+)$", i)] if m}
    sources = {f"未闭环账本 {i}" for i in mine}
    example = f"{r.get('project') or '—'}｜{ol.clip(r.get('what'), 40)}"
    for path in sorted(folder.glob("*.delivery.json")):
        d = read_json(path) or {}
        when, day = ol.parse_ts(d.get("at")), path.name[: -len(".delivery.json")]
        if not d.get("sent") or not when or when >= at:
            continue
        how = None
        for dec in (read_json(folder / f"{day}.decisions.sent.json") or {}).get("decisions") or []:
            for s in dec.get("sources") or []:
                text = " ".join(str(x) for x in (dec.get("item"), s.get("item"), s.get("source"), *(s.get("evidence") or [])))
                if s.get("source") in sources or any(pid and s.get("project_id") == pid and re.search(rf"#{n}(?!\d)", text) for pid, n in prs):
                    how = f"拍板 {dec.get('n')}"
        if not how:
            for k in (read_json(folder / f"{day}.facts.json") or {}).get("risks") or []:
                if str(k.get("item") or "").startswith("未闭环账本") and any(str(e).startswith(example) for e in k.get("evidence") or []):
                    how = "风险例子"
        if not how and on_card:
            try:   # 发出去的卡片原文（风险表、项目明细里写的「项目名 #号」）
                card = (folder / f"{day}.card.sent.json").read_text(encoding="utf-8")
            except OSError:
                card = ""
            if any(card_mentions(card, names.get(repo) or {repo}, n) for repo, n in on_card):
                how = "卡片提到"
        if how:
            hits.append({"where": f"日报 {day}（{how}）", "at": d.get("at")})
    return hits


def judge(target, at, projects=None, folder=None, snaps=None):
    """按问的时刻判这件事：返回登记用的字段（verdict 等）；问之前还没有账本返回 None。"""
    led = ledger_before(at, folder, snaps)
    if led is None:
        return None
    base = {"ledger_at": led.get("generated_at"), "row": None, "state": None, "stalled": False, "parked": False, "reported": []}
    r, parked = locate(led, target) if target else (None, False)
    if r is None:
        return {**base, "verdict": "漏收"}
    projects = projects_or_empty() if projects is None else projects
    seen = slips_before(r, at) + daily_before(r, at, projects)
    return {**base, "verdict": "已报过" if seen else "追问", "row": r.get("id"), "what": r.get("what"),
            "state": "只计数" if parked else r.get("state"), "stalled": bool(r.get("stalled")), "parked": parked, "reported": seen}


def record(question, via, at, target=None, text=None, status="确认", key=None, projects=None, extra=None, snaps=None):
    """记一行。target 是账本编号；没有 target 只有 text = 猛鬼兽找过、账本里没有。"""
    if not target and not text:
        raise Fail("要么给账本编号（--id / --pr / --session），要么用 --text 写清问的是哪件事（=找过、账本里没有）")
    got = judge(target, at, projects, snaps=snaps)
    if got is None:
        raise Fail(f"{ol.stamp(at)} 之前还没有账本，判不了")
    rec = {"at": ol.iso(at), "recorded_at": ol.iso(ol.current()), "via": via, "status": status,
           "question": ol.clip(question, 200), "target": target, "text": ol.clip(text, 200) if text else None, **got, **(extra or {})}
    if key:
        rec["key"] = key
    append(rec)
    return rec


# ---------- 重判 ----------

def rec_key(a):
    """一行追问的身份：更正行有自己的 id；粗筛的有 key；飞书记的按 时间|对象|原话。"""
    return a.get("id") or a.get("key") or f"{a.get('at')}|{a.get('target') or a.get('text')}|{a.get('question')}"


def live(asks):
    """去掉被更正过的行，只留每次追问最新的结论（asks.jsonl 只追加，更正是新的一行带 supersedes）。"""
    gone = {a.get("supersedes") for a in asks if a.get("supersedes")}
    return [a for a in asks if rec_key(a) not in gone]


def recheck(projects=None, write=True):
    """判法改了之后，按现在的判法把有账本编号的追问重判一遍；结论不同的追加一行更正（supersedes 指向原来那行）。"""
    projects = projects_or_empty() if projects is None else projects
    snaps, out = ledgers(), []
    for a in live(read_lines(asks_path())):
        at = ol.parse_ts(a.get("at"))
        if not a.get("target") or not at:
            continue
        got = judge(a["target"], at, projects, snaps=snaps)
        if got is None or got["verdict"] == a.get("verdict"):
            continue
        now = ol.iso(ol.current())
        fix = {**a, **got, "id": f"{rec_key(a)}~{now}", "supersedes": rec_key(a), "rechecked_at": now,
               "was": a.get("verdict")}
        if write:
            append(fix)
        out.append(fix)
    return out


# ---------- 找 ----------

def find(words, led):
    """账本里像这件事的行：每个词都要出现在编号、事、下一步、项目里。"""
    words = [w for w in words if w.strip()]
    out = []
    for r, parked in [(r, False) for r in led.get("rows") or []] + [(r, True) for r in led.get("parked") or []]:
        hay = " ".join(str(x) for x in (*ids_of(r), r.get("what"), r.get("next"), r.get("project"),
                                          *[x.get("what") for x in r.get("links") or [] if isinstance(x, dict)]))
        if all(w.lower() in hay.lower() for w in words):
            out.append((r, parked))
    return out


# ---------- Claude 会话粗筛 ----------

def asks_in(text):
    """这句话像不像在追问进度：返回命中的问法；不像返回 []。"""
    body = text
    for p in STRIP:
        body = p.sub(" ", body)
    body = body.strip()
    if not body or len(body) > SHORT:
        return []
    return [m.group(0) for p in ASK_PATTERNS for m in [p.search(body)] if m]


def log_index(logs_dir, since):
    """{记录文件名: 路径}；最后修改早于窗口起点的不可能有窗口里的话，不收（同会话复盘的剪枝）。"""
    out = {}
    for p in Path(logs_dir).glob("*/*.jsonl"):
        try:
            if p.stat().st_mtime >= since.timestamp():
                out[p.stem] = p
        except OSError:
            continue
    return out


def screen(start, end, projects=None, di=None, write=True):
    """[start, end) 之间 Claude 桌面会话里常浩追问进度的话，对账本记成待核。返回（新记的行，覆盖情况）。"""
    di = di or ol.inbox_module()
    projects = ol.roster() if projects is None else projects
    meta_dir = os.environ.get("DT_OPENLOOP_META_DIR") or di.META_DIR
    logs_dir = os.environ.get("DT_OPENLOOP_LOGS_DIR") or di.PROJECTS_DIR
    done = {x.get("key") for x in read_lines(asks_path()) if x.get("key")}
    cover = {"sessions": 0, "not_personal": 0, "hits": 0, "already": 0, "before_ledger": 0, "bad": 0}
    out, index = [], log_index(logs_dir, start)
    snaps = ledgers()
    for sid, meta in sorted(ol.read_metas(meta_dir).items()):
        p = ol.project_at(projects, meta.get("originCwd") or meta.get("cwd"))
        logs = sorted({index[meta[k]] for k in ("cliSessionId", "lastAssistantCliSessionId") if meta.get(k) in index})
        if not logs:
            continue
        if not p:
            cover["not_personal"] += 1
            continue
        cover["sessions"] += 1
        name, first = ol.key_of(p), bool((meta.get("spawnedFrom") or {}).get("sessionId"))
        for log in logs:
            try:
                handle = open(log, encoding="utf-8", errors="replace")
            except OSError:
                cover["bad"] += 1
                continue
            with handle:
                lines = [x for x in handle if '"user"' in x]   # 记录很大：先按字面筛掉不是用户消息的行，再解析
            for line in lines:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                words = di.user_words(entry) if isinstance(entry, dict) else None
                if not words:
                    continue
                if first:   # 别的会话开出来的：第一条是交来的任务，不是常浩的话
                    first = False
                    continue
                at = ol.parse_ts(entry.get("timestamp"))
                if words.startswith(di.PEER) or not at or not (start <= at < end):
                    continue
                hit = asks_in(words)
                if not hit:
                    continue
                cover["hits"] += 1
                key = f"claude:{sid}:{entry.get('uuid') or entry.get('timestamp')}"
                if key in done:
                    cover["already"] += 1
                    continue
                nums = sorted({int(n) for n in ol.NUM.findall(words)})
                target = f"pr:{name}#{nums[0]}" if len(nums) == 1 else f"session:{sid}"
                extra = {"session": sid, "project": name, "matched": hit,
                         "target_guess": "话里写的 PR" if len(nums) == 1 else "这个会话本身"}
                if not write:
                    got = judge(target, at, projects, snaps=snaps)
                    if got is None:
                        cover["before_ledger"] += 1
                        continue
                    out.append({"at": ol.iso(at), "question": ol.clip(words, 200), "target": target, **got, **extra})
                    continue
                try:
                    out.append(record(words, "Claude 会话", at, target=target, status="待核", key=key, projects=projects, extra=extra, snaps=snaps))
                except Fail:
                    cover["before_ledger"] += 1
                    continue
                done.add(key)
    return out, cover


# ---------- 一周基线 ----------

def day_range(end_day, days=7):
    start = dt.datetime.combine(end_day - dt.timedelta(days=days - 1), dt.time(0), tzinfo=ol.TZ)
    return start, start + dt.timedelta(days=days)


def evidence_coverage(projects):
    """结果证据覆盖率：各项目 CURRENT（主分支）主线里标 ✅ 的项，写了提交号或 PR 号的比例。"""
    total = with_ev = 0
    per, failed = {}, {}
    for p in projects:
        name, root = ol.key_of(p), ol.root_of(p)
        try:
            text = ol.pdaily_data.show(root, ol.pdaily_data.main_ref(root, p.get("main") or "main"), "CURRENT.md")
        except ol.pdaily_data.Fail as e:
            failed[name] = str(e)
            continue
        if not text:
            continue
        seen, a, b = set(), 0, 0
        for s in ol.mainlines(text):
            k = ol.mainline_key(s["title"])
            if s.get("stray") or s["old"] or k in seen:
                continue
            seen.add(k)
            for it in s["items"]:
                if it["mark"] == "✅":
                    a += 1
                    b += bool(ol.NUM.search(it["text"]) or HASH.search(it["text"]))
        if a:
            per[name] = {"done": a, "with_evidence": b}
        total, with_ev = total + a, with_ev + b
    return {"done": total, "with_evidence": with_ev, "rate": round(with_ev / total, 3) if total else None,
            "projects": per, "failed": failed}


def unverified_merges(start, end):
    path = merge_ledger()
    if not path.exists():
        return {"count": None, "note": "合并放权还没上线（合并账本不存在）"}
    n = sum(1 for x in read_lines(path) if "未核实" in str(x.get("review") or "")
            and (lambda t: t and start <= t < end)(ol.parse_ts(x.get("at"))))
    return {"count": n, "note": None}


def week(end_day, projects=None, folder=None):
    start, end = day_range(end_day)
    projects = projects_or_empty() if projects is None else projects
    snaps = [(t, led) for t, led in ledgers(folder) if start <= t < end]
    seen, stalled = set(), set()
    for _, led in snaps:
        for r in led.get("rows") or []:
            seen.add(r.get("id"))
            if r.get("stalled"):
                stalled.add(r.get("id"))
        seen |= {r.get("id") for r in led.get("parked") or []}
    days = {t.date() for t, _ in snaps}
    missing = [str(start.date() + dt.timedelta(days=i)) for i in range(7) if start.date() + dt.timedelta(days=i) not in days]
    asks = [a for a in live(read_lines(asks_path())) if (lambda t: t and start <= t < end)(ol.parse_ts(a.get("at")))]
    count = {s: {v: sum(1 for a in asks if a.get("status") == s and a.get("verdict") == v) for v in VERDICTS} for s in ("确认", "待核")}
    sure = sum(count["确认"][v] for v in DEFECTS)
    rough = sure + sum(count["待核"][v] for v in DEFECTS)
    silent = sum(1 for a in asks if a.get("status") == "确认" and a.get("verdict") == "追问" and a.get("stalled"))

    def rate(n, d):
        return round(n / d, 3) if d else None
    return {
        "window": [str(start.date()), str((end - dt.timedelta(days=1)).date())], "generated_at": ol.iso(ol.current()),
        "ledger": {"snapshots": len(snaps), "missing_days": missing, "items": len(seen), "stalled_items": len(stalled)},
        "asks": count,
        "metrics": {
            "你主动追问率": {"value": rate(sure, len(seen)), "with_unchecked": rate(rough, len(seen)), "defects": sure,
                       "defects_with_unchecked": rough, "items": len(seen),
                       "how": "当周「追问」+「漏收」（status=确认）÷ 当周账本里出现过的事；with_unchecked 把 Claude 会话粗筛的待核也算上"},
            "无声中断率": {"value": rate(silent, len(stalled)), "silent": silent, "stalled_items": len(stalled),
                      "how": "你追问的事当时已经停了、却没报过（追问且停了，status=确认）÷ 当周账本里停过的事；history 每天只留最后一次扫描，停过的事偏少"},
            "结果证据覆盖率": evidence_coverage(projects),
            "没核实审查就合并的": unverified_merges(start, end),
            "有效自治闭环率、自动恢复成功率": {"value": None, "note": "要有自动恢复才有分子（批 5b）"},
        },
    }


def render_week(w):
    m, led = w["metrics"], w["ledger"]

    def pct(x):
        return "—" if x is None else f"{x * 100:.1f}%"
    a, ev, mg = m["你主动追问率"], m["结果证据覆盖率"], m["没核实审查就合并的"]
    out = [f"未闭环账本 · 一周基线｜{w['window'][0]} 到 {w['window'][1]}",
           f"你主动追问率：{pct(a['value'])}（{a['defects']} ÷ {a['items']}）｜算上待核：{pct(a['with_unchecked'])}（{a['defects_with_unchecked']} ÷ {a['items']}）",
           "  追问登记：" + "｜".join(f"{s} " + "、".join(f"{v} {w['asks'][s][v]}" for v in VERDICTS) for s in ("确认", "待核")),
           f"无声中断率：{pct(m['无声中断率']['value'])}（{m['无声中断率']['silent']} ÷ {m['无声中断率']['stalled_items']}）",
           f"结果证据覆盖率：{pct(ev['rate'])}（主线 ✅ {ev['done']} 项，带提交号或 PR 号 {ev['with_evidence']} 项）",
           f"没核实审查就合并的：{mg['note'] or mg['count']}",
           f"有效自治闭环率、自动恢复成功率：{m['有效自治闭环率、自动恢复成功率']['note']}",
           f"账本：这一周 {led['snapshots']} 份快照，出现过 {led['items']} 件事，停过 {led['stalled_items']} 件"]
    if not any(w["asks"][s][v] for s in ("确认", "待核") for v in VERDICTS):
        out.append("⚠ 这一周一条追问登记都没有：入口还没装、没跑粗筛，或者真没人问；0% 不等于没缺陷")
    if led["missing_days"]:
        out.append(f"⚠ 这几天没有账本快照（扫描没跑成或还没装）：{'、'.join(led['missing_days'])}，分母偏小")
    if ev.get("failed"):
        out.append("⚠ 结果证据覆盖率没读到：" + "；".join(f"{k}（{v}）" for k, v in ev["failed"].items()))
    return "\n".join(out)


# ---------- 命令行 ----------

def main(argv=None):
    ap = argparse.ArgumentParser(description="未闭环账本：追问登记与一周基线（只读账本，不调 AI）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="在当前账本里找这件事（给猛鬼兽挑编号用）")
    f.add_argument("words", nargs="+", help="关键词，几个词都要出现")
    r = sub.add_parser("record", help="记一次追问")
    r.add_argument("--question", required=True, help="常浩的原话")
    r.add_argument("--via", default="飞书", help="从哪问的（默认 飞书）")
    r.add_argument("--at", help="问的时间（ISO，默认现在）")
    who = r.add_mutually_exclusive_group()
    who.add_argument("--id", help="账本编号（find 给的）")
    who.add_argument("--pr", help="仓库#号，如 digital-twin#211")
    who.add_argument("--session", help="会话号 local_…")
    r.add_argument("--text", help="问的是哪件事；只给它不给编号 = 找过、账本里没有，记漏收")
    s = sub.add_parser("screen", help="从 Claude 会话记录里粗筛追问，对账本记成待核")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--end", help="截止日（含，YYYY-MM-DD，默认昨天）")
    s.add_argument("--dry-run", action="store_true", help="只打印，不写 asks.jsonl")
    c = sub.add_parser("recheck", help="判法改了以后按现在的判法重判已记的追问，结论不同的追加一行更正")
    c.add_argument("--dry-run", action="store_true", help="只打印，不写")
    w = sub.add_parser("week", help="出一周基线")
    w.add_argument("--end", help="截止日（含，YYYY-MM-DD，默认昨天）")
    w.add_argument("--json", action="store_true")
    w.add_argument("--no-save", action="store_true", help="不写 weekly/<截止日>.json")
    a = ap.parse_args(argv)
    now = ol.current()
    end_day = dt.date.fromisoformat(a.end) if getattr(a, "end", None) else now.date() - dt.timedelta(days=1)
    try:
        if a.cmd == "find":
            led = ol.read_ledger()
            if led is None:
                raise Fail("还没有账本，先跑 openloop.py scan")
            got = find(a.words, led)
            print(f"账本 {ol.stamp(ol.parse_ts(led.get('generated_at')))} 扫描｜找到 {len(got)} 件" + (f"，只列前 {FIND_MAX} 件" if len(got) > FIND_MAX else ""))
            for row, parked in got[:FIND_MAX]:
                print(f"  {row['id']}｜{row.get('what')}｜{'只计数' if parked else row.get('state')}" + ("｜停了" if row.get("stalled") else ""))
            if not got:
                print("  没找到：换个词再找；确实没有就 record --text 记漏收")
            return 0
        if a.cmd == "record":
            at = ol.parse_ts(a.at) if a.at else now
            rec = record(a.question, a.via, at, target=target_of(a.id, a.pr, a.session), text=a.text)
            print(f"记了：{rec['verdict']}" + (f"｜{rec['row']}（{rec['state']}）" if rec["row"] else f"｜{rec['target'] or rec['text']} 不在 {ol.stamp(ol.parse_ts(rec['ledger_at']))} 的账本里")
                  + (f"｜之前报过：{'、'.join(x['where'] for x in rec['reported'])}" if rec["reported"] else ""))
            return 0
        if a.cmd == "screen":
            start, end = day_range(end_day, a.days)
            got, cover = screen(start, end, write=not a.dry_run)
            print(f"Claude 会话粗筛｜{start.date()} 到 {(end - dt.timedelta(days=1)).date()}｜个人项目会话 {cover['sessions']} 个｜像追问的 {cover['hits']} 句"
                  f"｜新记 {len(got)} 条（待核）｜以前记过 {cover['already']}｜问时还没账本 {cover['before_ledger']}")
            for x in got:
                print(f"  {x['at'][5:16]} {x['verdict']}｜{x['target']}｜{x['question']}")
            return 0
        if a.cmd == "recheck":
            got = recheck(write=not a.dry_run)
            print(f"重判：{len(got)} 条结论变了" + ("（只打印，没写）" if a.dry_run else ""))
            for x in got:
                print(f"  {x['at'][5:16]} {x['was']} → {x['verdict']}｜{x['target']}｜{x['question']}"
                      + (f"｜报过：{'、'.join(r['where'] for r in x['reported'])}" if x["reported"] else ""))
            return 0
        rep = week(end_day)
        if not a.no_save:
            folder = ol.out_dir() / "weekly"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{end_day}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps(rep, ensure_ascii=False, indent=1) if a.json else render_week(rep))
        return 0
    except (Fail, ol.Fail) as e:
        print(f"没记成：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
