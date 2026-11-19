"""个人项目日报的拍板账与按钮（#235，从 #226 拆出的第 2 批；常浩 2026-11-19 定）。

日报卡片「需要你拍板」表下有一排按钮（`全部按推荐`、`① 按推荐`…），按钮只带 {"pdaily": 卡片令牌, "n": 编号}（0 = 其余全部）。
merge-buttons 插件把点击转给本脚本 `button`，核对后记账；插件再把一句话注入常浩私聊的会话，由猛鬼兽按 pdaily-act 技能办。
不做、先放着、改推荐靠常浩引用卡片回一句，由 pdaily-act 技能调 `record` 记账。本脚本不调 AI、不派单、不合并。

按钮和编号只认发卡时存下的拍板快照 DigitalTwin/daily/<统计日>.decisions.sent.json（send 写，哈希记进 delivery.json）：
发卡后就算重新写过稿，按钮也只对应常浩看到的那一版（#235 独立审查 P1-1）。
拍板账 DigitalTwin/daily/decisions.jsonl，只追加：每条「定了」一行（id、统计日、编号、事项、推荐、选择、来源），
办理结果另起一行（ref 指向那条的 id）。读的时候按 id 合并。写账在文件锁里做。

  python3 pdaily_act.py button 令牌 编号 --chat 聊天号 --operator 点击人   插件调；只认常浩本人、常浩私聊、最新一张发出且回读过的日报卡片
  python3 pdaily_act.py after --day 统计日 --entries id,id --injected 1|0   插件调（后台）：卡片原地改「已定」；没能叫猛鬼兽开工就在私聊说怎么办
  python3 pdaily_act.py todos [--day 统计日]                              技能调：这张卡的拍板，每条带状态、推荐、对应候选的出处
  python3 pdaily_act.py record --day 统计日 --n 1,3 --choice 按推荐|不做|先放着|自己做 [--note 说明]   技能调（常浩引用卡片回复时）
  python3 pdaily_act.py done --entry id --result 一句话 [--order 单号]     技能调：记办理结果
  python3 pdaily_act.py say --text 一句话                                  插件调（后台）：点击人核对过、但办不了时在私聊说原因
  python3 pdaily_act.py checks --day 统计日                                发送成功后（后台）：风险里写了「猛鬼兽查」的，派只读单去查（#236）

「猛鬼兽查」（#236，第 3 批）：风险的去向除了「→ 拍板 N」「只观察」，还可以是猛鬼兽派只读单核对（常设授权 A 档：派只读单不用问）。
卡片要素里这类风险带 check = {"project": 项目名, "task": 要查什么、交什么}；日报发出后 checks 每天最多开 2 张只读单（不点名执行方，
跟随车间默认），按顺序跑，遇到车间忙就隔 2 分钟再试、最多等 90 分钟；记在 DigitalTwin/daily/<统计日>.checks.json。
第二天写稿时，pdaily_publish 把这些单的回执摘要放进材料，写进风险段。
每条命令最后输出一行 JSON（ok、msg）。
核对规则：button 的点击人、聊天号来自网关（HERMES_SESSION_USER_ID、会话聊天号），和本机配置的常浩 open_id、私聊号逐字比；
令牌必须等于最新一张发出且回读过的日报卡片的令牌（同 merge_act / autoloop_answer 的做法）。不是常浩、不是私聊、令牌不对：不回话，只记日志。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdaily_data  # noqa: E402
import pdaily_facts  # noqa: E402
import pdaily_publish as P  # noqa: E402  pdaily_publish 也 import 本模块：P 只在函数里用

DAILY = pdaily_facts.DAILY
TOKEN = re.compile(r"^[0-9a-f]{16}$")
CHOICES = ("按推荐", "不做", "先放着", "自己做")
HOLD_DAYS = 7            # 「先放着」「自己做」7 天内不再展开（常浩 11-19「都按推荐」）
REMEMBER_DAYS = 30       # 定了、办成了的事 30 天内不再问；更久的当新事
PENDING_DAYS = 2         # 按推荐但还没有办理结果的：2 天内不再问，过了重新拿出来（注入成功不等于办了，审查 P2-5）
TZ = pdaily_data.TZ


class Quiet(Exception):
    """来源不对：不回话，只记日志。"""


class Fail(Exception):
    """来源对、但办不了：原因写进输出。"""


def now():
    return dt.datetime.now(TZ)


def ledger():
    return DAILY / "decisions.jsonl"


def log(line):
    with contextlib.suppress(OSError):
        DAILY.mkdir(parents=True, exist_ok=True)
        with open(DAILY / "buttons.log", "a", encoding="utf-8") as f:
            f.write(f"{now().isoformat(timespec='seconds')} {line}\n")


def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False))
    return 0 if ok else 1


# ---------- 拍板账 ----------

@contextlib.contextmanager
def locked(name="decisions"):
    DAILY.mkdir(parents=True, exist_ok=True)
    with open(DAILY / f"{name}.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield


def rows():
    try:
        lines = ledger().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        with contextlib.suppress(ValueError):
            x = json.loads(ln)
            if isinstance(x, dict):
                out.append(x)
    return out


def append(x):
    with open(ledger(), "a", encoding="utf-8") as f:
        f.write(json.dumps(x, ensure_ascii=False) + "\n")


def entries():
    """id → 合并后的记录（定了那一行 + 后来的办理结果）。"""
    out = {}
    for x in rows():
        if x.get("ref") in out:
            out[x["ref"]].setdefault("results", []).append({k: x.get(k) for k in ("at", "result", "order")})
        elif x.get("id"):
            out[x["id"]] = dict(x)
    return out


def cand_key(c):
    """拍板候选的稳定键：类别 + 项目 + 事项原文。合并官判定带版本号，版本变了就是新事。"""
    raw = json.dumps([c.get("kind"), c.get("project_id"), c.get("item")], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def decision_keys(facts, d):
    """一条拍板对应的候选键；不是来自候选的（模型合并出来的），用项目 + 事项算一个。"""
    cands = facts.get("decisions") or []
    keys = [cand_key(c) for c in P.cand_list(d, cands)]
    if keys:
        return keys
    raw = json.dumps(["card", d.get("project"), d.get("item")], ensure_ascii=False)
    return ["c" + hashlib.sha1(raw.encode()).hexdigest()[:11]]


def snapshot(facts, decisions):
    """发卡时存的拍板快照：编号、事项、推荐、候选键和出处。返回 (快照 dict, 哈希)。"""
    cands = facts.get("decisions") or []
    snap = {"day": facts.get("stat_day"), "decisions": [
        {"n": n, "project": d.get("project"), "item": d.get("item"), "kind": d.get("kind"), "type": d.get("type"),
         "recommendation": d.get("recommendation"), "deadline": d.get("deadline"), "keys": decision_keys(facts, d),
         "sources": [{k: c.get(k) for k in ("kind", "project_id", "item", "source", "evidence", "since")}
                     for c in P.cand_list(d, cands)]}
        for n, d in enumerate(decisions, 1)]}
    text = json.dumps(snap, ensure_ascii=False, sort_keys=True)
    return snap, hashlib.sha256(text.encode()).hexdigest()


def buttonable(d):
    """哪些拍板放「按推荐」按钮：有推荐、不是要常浩亲手做的（审查 P1-3、P3）。"""
    return bool(P.s(d.get("recommendation"))) and d.get("kind") != "做"


def remembered(at=None):
    """给第二天写稿用：近期定过的（不再问）和先放着还没到期的（只计数）。返回 (键集合, 摘要列表, 先放着件数)。
    按推荐：办成了 30 天不再问；还没结果的 2 天不再问；结果是「没办」的不算定过。不做 30 天；先放着、自己做 7 天。"""
    at = at or now()
    keys, recent, held = set(), [], 0
    for e in entries().values():
        try:
            age = (at - dt.datetime.fromisoformat(e["at"])).days
        except (KeyError, TypeError, ValueError):
            continue
        last = (e.get("results") or [{}])[-1]
        choice, result = e.get("choice"), P.s(last.get("result")) or ""
        if choice in ("先放着", "自己做"):
            if age < HOLD_DAYS:
                keys.update(e.get("keys") or [])
                held += choice == "先放着"
            continue
        if choice == "按推荐":
            if result.startswith("没办"):
                continue
            window = REMEMBER_DAYS if result else PENDING_DAYS
        else:
            window = REMEMBER_DAYS
        if age < window:
            keys.update(e.get("keys") or [])
            recent.append({"day": e.get("day"), "item": e.get("item"), "choice": choice,
                           "result": last.get("result"), "order": last.get("order")})
    return keys, recent[-20:], held


# ---------- 卡片 ----------

def delivered(day=None):
    """最新一张（或指定统计日）发出的日报卡片：(统计日, delivery 记录)。最新一张没回读通过、或没有拍板快照，
    就当没有，不往前找旧卡（审查 P1-2）。"""
    files = [DAILY / f"{day}.delivery.json"] if day else sorted(DAILY.glob("*.delivery.json"), reverse=True)
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not rec.get("sent"):
            continue
        d = rec.get("stat_day") or f.name.split(".")[0]
        ok = rec.get("read_back") and rec.get("message_id") and rec.get("decisions_sha")
        return (d, rec) if ok else (None, None)
    return None, None


def card_decisions(day, rec=None):
    """发卡时存的拍板快照；哈希和 delivery 记录对不上就不认。"""
    snap = json.loads((DAILY / f"{day}.decisions.sent.json").read_text(encoding="utf-8"))
    if rec is not None:
        sha = hashlib.sha256(json.dumps(snap, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if sha != rec.get("decisions_sha"):
            raise Fail(f"{day} 日报的拍板快照和发卡记录对不上，不认按钮")
    return snap["decisions"]


def state_of(day, rec):
    """这张卡上每个编号现在的状态：编号 → 最近一条记录。只认记在这一版快照上的记录：当天重发过，
    旧卡上的记录不会安到新卡的同号事项上（复审 P2-B）。"""
    out = {}
    for e in entries().values():
        if e.get("day") == day and e.get("snap") == rec.get("decisions_sha"):
            out[e.get("n")] = e
    return out


def record_choices(day, rec, ns, choice, via, note=None):
    """在锁里记账；已经定过的编号不重复记。返回 (新记的条目, 已经定过的条目)。"""
    decs = card_decisions(day, rec)
    if not decs:
        raise Fail(f"{day} 的日报卡片上没有拍板")
    bad = [n for n in ns if not 1 <= n <= len(decs)]
    if bad:
        raise Fail(f"编号 {'、'.join(map(str, bad))} 不在 {day} 的日报卡片上（只有 1–{len(decs)}）")
    new, old = [], []
    with locked():
        st = state_of(day, rec)
        for n in dict.fromkeys(ns):
            if n in st:
                old.append(st[n])
                continue
            d = decs[n - 1]
            e = {"id": f"p-{day[2:].replace('-', '')}-{n}-{secrets.token_hex(2)}", "at": now().isoformat(timespec="seconds"),
                 "day": day, "snap": rec.get("decisions_sha"), "n": n, "keys": d.get("keys") or [], "project": d.get("project"), "item": d.get("item"),
                 "kind": d.get("kind"), "recommendation": d.get("recommendation"), "choice": choice, "via": via}
            if note:
                e["note"] = P.clip(note, 200)
            append(e)
            new.append(e)
    return new, old


def status_line(e):
    last = (e.get("results") or [{}])[-1]
    tail = f"，办理：{last['result']}" if last.get("result") else "，还没有办理结果"
    return f"{label(e['n'])} 已经定过（{e.get('choice')}{tail}）"


MARKS = {"按推荐": "✅ 按推荐", "不做": "✖ 不做", "先放着": "⏸ 先放着", "自己做": "✅ 你来做"}


def refresh_card(day, rec):
    """把发出去的卡片原地改：定了的那行「已挂」换成选择，按钮去掉；没有可点的了，按钮整排去掉。
    整段在卡片锁里（审查 P2-4）；卡片锁和记账锁分开，改卡片时调飞书再慢也不挡常浩记账（复审 P2-A）。"""
    with locked("card"):
        sent = DAILY / f"{day}.card.sent.json"
        card = json.loads(sent.read_text(encoding="utf-8"))
        st = state_of(day, rec)
        els = card["body"]["elements"]
        for e in els:
            if e.get("tag") == "table" and [c["name"] for c in e["columns"]] == ["n", "i", "r", "a"]:
                for k, row in enumerate(e["rows"], 1):
                    if k in st:
                        row["a"] = MARKS.get(st[k].get("choice"), "✅ 已定")
        keep = []
        for e in els:
            if e.get("tag") == "column_set" and any(btn_value(c).get("pdaily") for c in e.get("columns") or []):
                cols = [c for c in e["columns"] if btn_value(c).get("n") not in st]
                if not [c for c in cols if btn_value(c).get("n")]:
                    continue
                for c in cols:
                    if btn_value(c).get("n") == 0 and st:
                        c["elements"][0]["text"]["content"] = "其余全部按推荐"
                e = dict(e, columns=cols)
            keep.append(e)
        card["body"]["elements"] = keep
        text = json.dumps(card, ensure_ascii=False)
        P.lark(["im", "messages", "patch", "--message-id", rec["message_id"],
                "--data", json.dumps({"content": text}, ensure_ascii=False)], "改卡片", "bot")
        sent.write_text(text, encoding="utf-8")


def btn_value(col):
    try:
        return col["elements"][0]["behaviors"][0]["value"] or {}
    except (KeyError, IndexError, TypeError):
        return {}


def tell(text):
    P.lark(["im", "+messages-send", "--chat-id", P.CHAT, "--msg-type", "text",
            "--content", json.dumps({"text": text}, ensure_ascii=False)], "发私聊", "bot")


def label(n):
    return P.num_mark(n)


# ---------- 命令 ----------

def cmd_button(a):
    import twin_state as T
    try:
        owner, home = T.owner_open_id(), T.owner_chat_id()
    except T.StateError as e:
        raise Quiet(str(e))
    if not owner or not home:
        raise Quiet("本机没配置常浩的 open_id 或私聊号（twin_identity.py setup / setup-chat）")
    if a.operator != owner:
        raise Quiet("点按钮的不是常浩本人")
    if a.chat != home:
        raise Quiet("不是在常浩私聊里点的")
    if not TOKEN.match(a.token) or not 0 <= a.n <= 20:
        raise Quiet("按钮参数格式不对")
    day, rec = delivered()
    if not rec or rec.get("card_token") != a.token:
        raise Fail("这不是最新一张日报卡片上的按钮：旧卡片的按钮不再算数，请在最新一张上点")
    decs = card_decisions(day, rec)
    st = state_of(day, rec)
    if a.n == 0:
        ns = [d["n"] for d in decs if buttonable(d) and d["n"] not in st]
    elif 1 <= a.n <= len(decs) and buttonable(decs[a.n - 1]):
        ns = [a.n]
    else:
        raise Fail(f"{label(a.n)} 不能按推荐办（没有推荐，或是要你亲手做的），请引用卡片回一句怎么定")
    new, old = record_choices(day, rec, ns, "按推荐", "按钮")
    if not new:
        return done(True, "这几条已经定过了", day=day, entries=[],
                    tell="；".join(status_line(e) for e in old) or "没有还没定、能按推荐办的了")
    what = "、".join(label(e["n"]) for e in new)
    return done(True, f"记下：{day} 日报 {what} 按推荐", day=day, entries=[e["id"] for e in new],
                inject=f"【日报按钮】我在 {day} 的日报卡片上点了「{what} 按推荐」（拍板账 {'、'.join(e['id'] for e in new)}）。"
                       "请按 pdaily-act 技能办理，办完回我。")


def cmd_after(a):
    day, rec = delivered(a.day)
    if not rec:
        raise Fail(f"找不到 {a.day} 发出、回读过的日报卡片")
    try:
        refresh_card(day, rec)
    except (P.SendError, OSError, ValueError, KeyError) as e:
        log(f"改卡片失败 {day}：{e}")
    if a.injected == "0":
        es = [entries().get(i) for i in a.entries.split(",") if i]
        what = "、".join(label(e["n"]) for e in es if e)
        tell(f"已记下 {day} 日报的 {what} 按推荐。按钮暂时还不能直接叫我开工，请引用这张日报卡片回一句「办 {what}」，我就接着办。")
    return done(True, "卡片已更新")


def cmd_todos(a):
    day, rec = delivered(a.day)
    if not rec:
        raise Fail("最新一张日报卡片没发成或没回读通过" if not a.day else f"找不到 {a.day} 发出、回读过的日报卡片")
    decs = card_decisions(day, rec)
    st = state_of(day, rec)
    out = []
    for d in decs:
        e = st.get(d["n"]) or {}
        out.append({**{k: d.get(k) for k in ("n", "project", "item", "kind", "recommendation", "deadline", "sources")},
                    "mark": label(d["n"]),
                    "decided": {k: e.get(k) for k in ("id", "choice", "via", "at", "note", "results")} if e else None})
    return done(True, f"{day} 日报共 {len(out)} 件拍板，{len(st)} 件已定", day=day, report=str(DAILY / f"{day}.report.md"),
                decisions=out)


def cmd_record(a):
    ns = [int(x) for x in re.findall(r"\d+", a.n)]
    if a.choice not in CHOICES:
        raise Fail(f"选择只能是 {'/'.join(CHOICES)}")
    day, rec = delivered(a.day)
    if not rec:
        raise Fail(f"找不到 {a.day or '最新'} 发出、回读过的日报卡片")
    new, old = record_choices(day, rec, ns, a.choice, "回复", a.note)
    with contextlib.suppress(P.SendError, OSError, ValueError, KeyError):
        if new:
            refresh_card(day, rec)
    return done(True, f"记下 {len(new)} 条" + (f"；{'；'.join(status_line(e) for e in old)}，没改" if old else ""),
                entries=[{"id": e["id"], "n": e["n"], "choice": e["choice"]} for e in new],
                already=[{"id": e["id"], "n": e["n"], "choice": e.get("choice")} for e in old])


def cmd_done(a):
    with locked():
        if a.entry not in entries():
            raise Fail(f"拍板账里没有 {a.entry}")
        append({"ref": a.entry, "at": now().isoformat(timespec="seconds"), "result": P.clip(a.result, 200), "order": a.order})
    return done(True, "已记办理结果")


def cmd_say(a):
    tell(P.clip(a.text, 300))
    return done(True, "已发")


DISPATCH = Path(os.environ.get("DT_DISPATCH", Path(__file__).with_name("dispatch.py")))
CHECKS_MAX = 2
CHECK_WAIT, CHECK_TRIES = 120, 45          # 车间忙：隔 2 分钟再试，最多 45 次（90 分钟）
READ_ONLY = ("只读：不得修改项目文件，不得运行会写缓存或产物的测试 / 构建，不提交、不丢弃任何改动；"
             "主目录里没提交的改动就在现有工作区读，不要换新 worktree。")


def dispatch(*args, timeout=None):
    r = subprocess.run([sys.executable, str(DISPATCH), *args], capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except ValueError:
        return {"ok": False, "msg": P.clip((r.stderr or r.stdout).strip(), 200) or f"退出码 {r.returncode}"}


def check_tasks(day):
    """卡片要素里写了「猛鬼兽查」的风险：[(风险, 项目 id, 任务原文)]，最多 2 条；项目不在名册的跳过。"""
    facts = json.loads((DAILY / f"{day}.facts.json").read_text(encoding="utf-8"))    # 发卡后马上跑，读的就是刚发的这一稿
    fields = json.loads((DAILY / f"{day}.card-fields.json").read_text(encoding="utf-8"))
    roster = P.Roster(facts)
    out = []
    for r in P.dicts(fields, "risks"):
        c = r.get("check") if isinstance(r.get("check"), dict) else None
        pid = roster.resolve(P.s((c or {}).get("project")) or "")
        if c and pid and pid != "hermes" and P.s(c.get("task")):
            out.append((r, pid, c["task"]))
    return out[:CHECKS_MAX]


def task_text(day, r, task):
    return (f"# 日报风险核对（{day} 日报，猛鬼兽派的只读单）\n\n"
            f"- **目标**：{task}\n"
            f"- **背景**：日报「风险与阻塞」里的一类：{r.get('cause')}（{r.get('scope')}）。明细：{r.get('detail') or '无'}\n"
            f"- **交付物**：一段中文结论（每个点写清看到了什么、出处是哪个文件或命令），写在最后的回复里。\n"
            f"- **验收标准**：问到的每一点都有结论或写明查不到、为什么查不到；不猜。\n"
            f"- **边界**：{READ_ONLY}只查这一件事。\n"
            f"- **交给谁**：车间默认执行方。\n")


def cmd_checks(a):
    rec_file = DAILY / f"{a.day}.checks.json"
    if rec_file.exists():
        return done(True, "这一天已经派过了")
    tasks = check_tasks(a.day)
    recs = []
    for j, (r, pid, task) in enumerate(tasks):
        f = DAILY / "checks" / f"{a.day}-{j + 1}.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(task_text(a.day, r, task), encoding="utf-8")
        out = dispatch("new", "--project", pid, "--mode", "read", "--task-file", str(f), timeout=300)
        no = (out.get("order") or {}).get("no")
        recs.append({"cause": r.get("cause"), "project": pid, "task": task, "order": no, "msg": out.get("msg")})
        rec_file.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")   # 开一张记一张：跑的时候崩了也知道开过
    for x in recs:
        if not x["order"]:
            continue
        for _ in range(CHECK_TRIES):
            out = dispatch("run", x["order"])
            if not out.get("busy"):
                break
            time.sleep(CHECK_WAIT)
        x["run"] = out.get("msg")
        rec_file.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")
    if not tasks:
        rec_file.write_text("[]", encoding="utf-8")
    return done(True, f"派了 {sum(1 for x in recs if x['order'])} 张只读单", checks=recs)


def checks_for_material(day):
    """前一个统计日派的只读单和回执摘要（给写稿用）。没有就是空列表。"""
    try:
        recs = json.loads((DAILY / f"{day}.checks.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for x in recs if isinstance(recs, list) else []:
        receipt = {}
        if x.get("order"):
            with contextlib.suppress(OSError, ValueError):
                receipt = json.loads((Path(os.environ.get("DT_DISPATCH_ROOT", "~/.hermes/dispatch")).expanduser()
                                      / x["order"] / "out" / "receipt.json").read_text(encoding="utf-8"))
        out.append({"cause": x.get("cause"), "project": x.get("project"), "task": x.get("task"), "order": x.get("order"),
                    "finished": bool(receipt), "exit_code": receipt.get("exit_code"),
                    "summary": P.clip(receipt.get("summary"), 600) if receipt.get("summary") else None,
                    "need_decision": P.clip(receipt.get("need_decision"), 200) if receipt.get("need_decision") else None})
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="个人项目日报的拍板账与按钮（#235）")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("button")
    b.add_argument("token")
    b.add_argument("n", type=int)
    b.add_argument("--chat", default="")
    b.add_argument("--operator", default="")
    f = sub.add_parser("after")
    f.add_argument("--day", required=True)
    f.add_argument("--entries", default="")
    f.add_argument("--injected", choices=("0", "1"), default="1")
    t = sub.add_parser("todos")
    t.add_argument("--day")
    r = sub.add_parser("record")
    r.add_argument("--day")
    r.add_argument("--n", required=True)
    r.add_argument("--choice", required=True)
    r.add_argument("--note")
    d = sub.add_parser("done")
    d.add_argument("--entry", required=True)
    d.add_argument("--result", required=True)
    d.add_argument("--order")
    c = sub.add_parser("checks")
    c.add_argument("--day", required=True)
    s = sub.add_parser("say")
    s.add_argument("--text", required=True)
    a = p.parse_args(argv)
    try:
        return {"button": cmd_button, "after": cmd_after, "todos": cmd_todos, "record": cmd_record, "done": cmd_done, "say": cmd_say, "checks": cmd_checks}[a.cmd](a)
    except Quiet as e:
        log(f"不认 {a.cmd}：{e}")
        return done(False, "不认")
    except Fail as e:
        log(f"{a.cmd} 没办成：{e}")
        return done(False, str(e))
    except (OSError, ValueError, P.SendError, subprocess.SubprocessError) as e:
        log(f"{a.cmd} 出错：{type(e).__name__}：{e}")
        return done(False, f"出错了（{type(e).__name__}）")


if __name__ == "__main__":
    sys.exit(main())
