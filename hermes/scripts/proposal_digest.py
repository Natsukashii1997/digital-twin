#!/usr/bin/env python3
"""每周提案收口（#61，《工程流程》第 8 节第 2 条）：把待决提案打包成选择题发到常浩私聊，常浩回复后写回结论。

三类来源，各自的结论写回它原来的位置：
  会话复盘   ~/session-retro/*/提案.md 里的「### A1 …」条目；结论写成条目标题下一行「- 结论：采纳（日期，说明）」
  牛马之家   每周整理目录里最新一期（YYYY-MM-DD.md）的「### W…」条目和「上次未决」；结论按该目录 decisions.md
             自己的约定追加一行「- 编号　采纳/否决/待定　日期　说明」。被新条目「取代」的旧编号不单列，新条目定了一并记上
  LESSONS    默认不列（常浩 2026-11-07：hook 命中只是关键词匹配，太吵）。设 DT_LESSONS_MIN_PROJECTS=N 才列：agent-kit
             LESSONS.md 里「升级：未升级」、近 7 天被 hook 在 N 个以上名册项目碰到的全局坑（只是候选）；
             结论记在影子目录 lessons-decisions.md（同上格式）
「延期」记成「待定」，下周一还会再列；没提到的编号保持待决。采纳只记结论，不替常浩落地那条规则。

用法：
  proposal_digest.py [send] [--dry-run] [--force] 汇总并发卡片（不带子命令就是 send，定时任务每周一这样跑；没有待决提案或本期已发时不输出）
  proposal_digest.py list                         打印最近一期的编号（Hermes 技能核对用）
  proposal_digest.py apply "采纳 1 3，否 2，延期 其余" [--dry-run]   写回结论（只在常浩本人私聊回复后由 Hermes 调用）

回复的写法（脚本只认这一种，Hermes 技能先把常浩的话整理成这样）：动词后跟编号，动词有 采纳 / 否（否决）/ 延期（待定）；
编号用空格、逗号或顿号隔开，可写范围「4-6」，「其余」指这一期还没点到的全部编号。

环境变量：DT_RETRO_ROOT、DT_NIUMA_REVIEW、DT_LESSONS、AGENT_KIT_STATE_DIR（读 lessons-hits.jsonl）、DT_ROSTER、
DT_PROPOSAL_DIR（期号状态）、DT_SHADOW_DIR（lessons-decisions.md）、FEISHU_HOME_CHANNEL（私聊 chat_id）、
DT_LARK_CLI（换 lark-cli，自测用）、DT_NOW（ISO 时间），DT_LESSONS_MIN_PROJECTS（默认 0 = 不列 LESSONS 候选）。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from tidings_weekly import SendError, send

HOME = Path.home()
RETRO_ROOT = Path(os.environ.get("DT_RETRO_ROOT", "~/session-retro")).expanduser()
NIUMA = Path(os.environ.get("DT_NIUMA_REVIEW", "~/Library/Application Support/NiumaHome/evidence/weekly-review")).expanduser()
LESSONS = Path(os.environ.get("DT_LESSONS", "~/Documents/ChatGPT/个人/work/agent-kit/LESSONS.md")).expanduser()
KIT_STATE = Path(os.environ.get("AGENT_KIT_STATE_DIR", "~/Library/Application Support/agent-kit")).expanduser()
ROSTER = Path(os.environ.get("DT_ROSTER", "~/Documents/ChatGPT/个人/work/digital-twin/projects.json")).expanduser()
STATE_DIR = Path(os.environ.get("DT_PROPOSAL_DIR", "~/Library/Application Support/DigitalTwin/proposals")).expanduser()
SHADOW = Path(os.environ.get("DT_SHADOW_DIR", "~/Library/Application Support/DigitalTwin/shadow")).expanduser()
STATE = STATE_DIR / "digest.json"
LESSONS_LEDGER = SHADOW / "lessons-decisions.md"
MIN_PROJECTS = int(os.environ.get("DT_LESSONS_MIN_PROJECTS", "0"))   # 0 = 不列
BJ = dt.timezone(dt.timedelta(hours=8))
OVERDUE_DAYS = 7
TITLE_MAX = 48
ADOPT, REJECT, DEFER = "采纳", "否决", "待定"
SEP = "　"   # 全角空格，decisions.md 的分隔符

RETRO_HEAD = re.compile(r"^### ([A-Z]\d+) (.+?)(?: · (.+))?\s*$")
NIUMA_HEAD = re.compile(r"^### (W\d{4}-\d{2}-\d{2}-\d+)\s*$")
NIUMA_OPEN = re.compile(r"^- (W\d{4}-\d{2}-\d{2}-\d+)\s+(.+)$")
DECISION = re.compile(r"^- (\S+)[\s　]+(采纳|否决|待定)[\s　]+(\S+)[\s　]*(.*)$")
SUPERSEDE = re.compile(r"取代 ?(W\d{4}-\d{2}-\d{2}-\d+)")
LESSON_HEAD = re.compile(r"^## (G-\d+) (.+?)\s*$")


class ReplyError(ValueError):
    """回复看不懂，或编号不对；一条都不写。"""


def today():
    v = os.environ.get("DT_NOW")
    t = dt.datetime.fromisoformat(v) if v else dt.datetime.now(BJ)
    return (t if t.tzinfo else t.replace(tzinfo=BJ)).astimezone(BJ)


def short(s):
    s = re.sub(r"\*\*|`", "", s).strip()
    s = re.split(r"[。；]", s)[0]
    return s if len(s) <= TITLE_MAX else s[:TITLE_MAX - 1] + "…"


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + "-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_decisions(path):
    """{编号: 最后一次的结论}（后写的覆盖先写的，同 decisions.md 的约定）。"""
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = DECISION.match(line.strip())
            if m:
                out[m.group(1)] = m.group(2)
    return out


def append_decision(path, pid, verdict, day, note, header=None):
    text = path.read_text(encoding="utf-8") if path.exists() else (header or "")
    if text and not text.endswith("\n"):
        text += "\n"
    atomic_write(path, text + f"- {pid}{SEP}{verdict}{SEP}{day}{SEP}{note}\n")


# ---------- 三类来源 ----------

def collect_retro():
    items = []
    for f in sorted(RETRO_ROOT.glob("*/提案.md")):
        run = f.parent.name
        day = run.split("_")[-1]
        section_ok, cur = True, None
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                section_ok = not any(w in line for w in ("已覆盖", "筛掉"))
                cur = None
                continue
            m = RETRO_HEAD.match(line)
            if m and section_ok:
                cur = {"key": f"retro:{run}:{m.group(1)}", "source": "retro", "group": f"会话复盘（{run.replace('_', ' 至 ')}）",
                       "id": m.group(1), "title": short(m.group(2)) + (f" · {m.group(3)}" if m.group(3) else ""),
                       "date": day, "file": str(f), "decided": False}
                items.append(cur)
            elif line.startswith("#"):
                cur = None
            elif cur and line.startswith("- 结论："):
                cur["decided"] = line[len("- 结论："):].startswith((ADOPT, REJECT))   # 延期的下周再列
    return [i for i in items if not i["decided"]]


def niuma_latest():
    files = sorted(p for p in NIUMA.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", p.name))
    return files[-1] if files else None


def collect_niuma():
    latest = niuma_latest()
    if latest is None:
        return []
    decided = read_decisions(NIUMA / "decisions.md")
    items, supersede, cur, section = [], {}, None, ""
    for line in latest.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            section, cur = line[3:], None
            continue
        m = NIUMA_HEAD.match(line)
        if m:
            cur = {"key": f"niuma:{m.group(1)}", "source": "niuma", "group": f"牛马之家每周整理（{latest.stem}）",
                   "id": m.group(1), "title": "", "date": m.group(1)[1:11], "file": str(NIUMA / "decisions.md")}
            items.append(cur)
            continue
        m = NIUMA_OPEN.match(line)
        if m and "上次未决" in section:
            items.append({"key": f"niuma:{m.group(1)}", "source": "niuma", "group": "牛马之家上次未决",
                          "id": m.group(1), "title": short(m.group(2)), "date": m.group(1)[1:11],
                          "file": str(NIUMA / "decisions.md")})
            continue
        if cur:
            if not cur["title"] and line.startswith("- **建议**："):
                cur["title"] = short(line.split("：", 1)[1])
            for old in SUPERSEDE.findall(line):
                if old != cur["id"]:
                    supersede[old] = cur["id"]
    for i in items:
        i["absorbs"] = sorted(o for o, n in supersede.items() if n == i["id"] and decided.get(o) not in (ADOPT, REJECT))
    return [i for i in items if i["id"] not in supersede and decided.get(i["id"]) not in (ADOPT, REJECT)]


def roster_paths():
    try:
        projects = json.loads(ROSTER.read_text(encoding="utf-8"))["projects"]
    except (OSError, ValueError, KeyError):
        return []
    return sorted(((str(Path(p["path"]).expanduser()), p["id"]) for p in projects if p.get("path")), key=lambda x: -len(x[0]))


def collect_lessons(now):
    if MIN_PROJECTS <= 0 or not LESSONS.exists():
        return []
    lessons, cur = {}, None
    for line in LESSONS.read_text(encoding="utf-8").splitlines():
        m = LESSON_HEAD.match(line)
        if m:
            cur = m.group(1)
            lessons[cur] = {"title": re.sub(r"（\d{4}-\d{2}-\d{2}.*?）$", "", m.group(2)), "upgraded": True}
        elif line.startswith("## "):
            cur = None
        elif cur and line.startswith("- 升级："):
            lessons[cur]["upgraded"] = not line[len("- 升级："):].strip().startswith("未升级")
    hits_file = KIT_STATE / "lessons-hits.jsonl"
    if not hits_file.exists():
        return []
    paths, since, by = roster_paths(), now - dt.timedelta(days=7), {}
    for line in hits_file.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            t = dt.datetime.fromisoformat(row["time"].replace("Z", "+00:00"))
        except (ValueError, KeyError, TypeError):
            continue
        if t < since:
            continue
        cwd = row.get("cwd") or ""
        pid = next((i for p, i in paths if cwd == p or cwd.startswith(p + "/")), None)
        if pid is None:
            continue
        for gid in row.get("ids") or []:
            by.setdefault(gid, set()).add(pid)
    decided = read_decisions(LESSONS_LEDGER)
    out = []
    for gid, projects in sorted(by.items(), key=lambda x: (-len(x[1]), x[0])):
        info = lessons.get(gid)
        if not info or info["upgraded"] or len(projects) < MIN_PROJECTS or decided.get(gid) in (ADOPT, REJECT):
            continue
        out.append({"key": f"lessons:{gid}", "source": "lessons", "group": "LESSONS 升级候选（hook 命中，不等于真出过事）",
                    "id": gid, "title": short(info["title"]) + f"（{len(projects)} 个项目：{'、'.join(sorted(projects))}）",
                    "date": now.date().isoformat(), "file": str(LESSONS_LEDGER)})
    return out


def collect(now):
    return collect_retro() + collect_niuma() + collect_lessons(now)


# ---------- 卡片 ----------

def number(items):
    return [{**i, "n": k} for k, i in enumerate(items, 1)]


def overdue(item, now):
    try:
        return (now.date() - dt.date.fromisoformat(item["date"])).days > OVERDUE_DAYS
    except ValueError:
        return False


def build_card(issue_no, items, now):
    n_over = sum(1 for i in items if overdue(i, now))
    lines = [f"共 {len(items)} 条{f'，其中 {n_over} 条已超过 7 天（⏰）' if n_over else ''}。《工程流程》第 8 节第 2 条：提案 7 天内要有结论。"]
    group = None
    for i in items:
        if i["group"] != group:
            group = i["group"]
            lines += ["", f"**{group}**"]
        lines.append(f"{i['n']}. {'⏰ ' if overdue(i, now) else ''}{i['id']} {i['title']}")
    lines += ["", "回复写法：**采纳 1 3，否 2，延期 4**（可写范围 4-6、「其余」）。没提到的编号下周一再列；采纳只记结论，落地另开单。"]
    return {"schema": "2.0",
            "header": {"title": {"tag": "plain_text", "content": f"📮 待决提案 · 第 {issue_no} 期（{now:%m/%d}）"},
                       "template": "blue"},
            "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines).strip()}]}}


# ---------- 状态 ----------

def read_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"issues": []}


def write_state(state):
    atomic_write(STATE, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


# ---------- 回复解析 ----------

VERBS = [("不采纳", REJECT), ("采纳", ADOPT), ("同意", ADOPT), ("通过", ADOPT), ("否决", REJECT), ("驳回", REJECT),
         ("否", REJECT), ("延期", DEFER), ("待定", DEFER), ("暂缓", DEFER)]
TOKEN = re.compile(r"(?P<verb>" + "|".join(v for v, _ in VERBS) + r")|(?P<a>\d+)\s*(?:-|~|～|到|至)\s*(?P<b>\d+)"
                   r"|(?P<num>\d+)|(?P<rest>其余|其他|剩下的|剩下)|(?P<sep>[\s,，、;；。.第号条]+)")


def parse_reply(text, total):
    """「采纳 1 3，否 2，延期 其余」→ {1: 采纳, 3: 采纳, 2: 否决, ...}。看不懂或编号不对抛 ReplyError。"""
    verb, out, pos, rest_verb, verb_used = None, {}, 0, None, True
    text = (text or "").strip()

    def put(n, v):
        if not 1 <= n <= total:
            raise ReplyError(f"没有第 {n} 条（这一期是 1–{total}）")
        if out.get(n, v) != v:
            raise ReplyError(f"第 {n} 条既写了{out[n]}又写了{v}")
        out[n] = v

    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise ReplyError(f"看不懂「{text[pos:pos + 8]}」")
        pos = m.end()
        if m.group("sep"):
            continue
        if m.group("verb"):
            if not verb_used:
                raise ReplyError(f"「{verb_label(verb)}」后面没写编号")
            verb, verb_used = dict(VERBS)[m.group("verb")], False
            continue
        if verb is None:
            raise ReplyError("编号前面要先写 采纳 / 否 / 延期")
        verb_used = True
        if m.group("rest"):
            if rest_verb not in (None, verb):
                raise ReplyError("「其余」只能用一次")
            rest_verb = verb
        elif m.group("num"):
            put(int(m.group("num")), verb)
        else:
            a, b = int(m.group("a")), int(m.group("b"))
            if a > b:
                raise ReplyError(f"范围 {a}-{b} 写反了")
            for n in range(a, b + 1):
                put(n, verb)
    if verb is not None and not verb_used:
        raise ReplyError(f"「{verb_label(verb)}」后面没写编号")
    if rest_verb:
        for n in range(1, total + 1):
            out.setdefault(n, rest_verb)
    if not out:
        raise ReplyError("没写任何编号")
    return out


def verb_label(v):
    return {ADOPT: "采纳", REJECT: "否", DEFER: "延期"}.get(v, v)


# ---------- 写回 ----------

def write_retro(item, verdict, day, note):
    path = Path(item["file"])
    lines = path.read_text(encoding="utf-8").split("\n")
    at = next((k for k, l in enumerate(lines) if (m := RETRO_HEAD.match(l)) and m.group(1) == item["id"]), None)
    if at is None:
        raise OSError(f"{path.name} 里找不到 {item['id']}")
    word = "延期" if verdict == DEFER else verdict
    new = f"- 结论：{word}（{day}，{note}）"
    end = next((k for k in range(at + 1, len(lines)) if lines[k].startswith("#")), len(lines))
    old = next((k for k in range(at + 1, end) if lines[k].startswith("- 结论：")), None)
    if old is None:
        lines.insert(at + 1, new)
    else:
        lines[old] = new
    atomic_write(path, "\n".join(lines))


def write_item(item, verdict, day, note):
    if item["source"] == "retro":
        write_retro(item, verdict, day, note)
    elif item["source"] == "niuma":
        path = Path(item["file"])
        append_decision(path, item["id"], verdict, day, note)
        for old in item.get("absorbs") or []:
            append_decision(path, old, verdict, day, f"随 {item['id']}（已被它取代）")
    else:
        append_decision(Path(item["file"]), item["id"], verdict, day, note,
                        header="# LESSONS 升级候选的结论\n\n由 proposal_digest.py 按常浩私聊回复写入，格式同牛马之家 decisions.md。\n\n")


# ---------- 命令 ----------

def cmd_send(dry_run=False, force=False):
    now = today()
    state = read_state()
    day = now.date().isoformat()
    issues = state.setdefault("issues", [])
    if issues and issues[-1]["date"] == day and issues[-1].get("message_id") and not (force or dry_run):
        return 0
    items = number(collect(now))
    if not items:
        if dry_run:
            print("没有待决提案，这周不发。")
        return 0
    no = issues[-1]["no"] if issues and issues[-1]["date"] == day else len(issues) + 1
    card = build_card(no, items, now)
    if dry_run:
        print(json.dumps({"msg_type": "interactive", "content": card}, ensure_ascii=False, indent=2))
        return 0
    chat = os.environ.get("FEISHU_HOME_CHANNEL", "")
    if not chat.startswith("oc_"):
        print("待决提案没发出：没有私聊 chat_id（FEISHU_HOME_CHANNEL）。")
        return 1
    idem = f"proposal-digest-{day}" + (f"-{int(now.timestamp())}" if force else "")
    try:
        mid = send(chat, "interactive", card, idem)
    except SendError as e:
        print(f"待决提案没发出：{e}")
        return 1
    issue = {"no": no, "date": day, "message_id": mid, "items": items, "answers": []}
    if issues and issues[-1]["date"] == day:
        issues[-1] = {**issue, "answers": issues[-1].get("answers", [])}
    else:
        issues.append(issue)
    try:
        write_state(state)
    except OSError as e:
        print(f"待决提案已发出，但期号状态没写进（{e.strerror or type(e).__name__}），常浩回复后要先重发一期再记。")
        return 1
    return 0


def latest_issue():
    issues = read_state().get("issues") or []
    return issues[-1] if issues else None


def cmd_list():
    issue = latest_issue()
    if not issue:
        print("还没发过待决提案。")
        return 1
    print(f"第 {issue['no']} 期（{issue['date']}），共 {len(issue['items'])} 条：")
    for i in issue["items"]:
        print(f"{i['n']}. [{i['source']}] {i['id']} {i['title']}")
    return 0


def cmd_apply(reply, dry_run=False):
    state = read_state()
    issues = state.get("issues") or []
    if not issues:
        print("没记下：还没发过待决提案，不知道编号对应哪条。")
        return 1
    issue = issues[-1]
    items = {i["n"]: i for i in issue["items"]}
    try:
        picks = parse_reply(reply, len(items))
    except ReplyError as e:
        print(f"没记下：{e}。一条都没写，请按「采纳 1 3，否 2」重说。")
        return 1
    day = today().date().isoformat()
    note = f"常浩飞书回复（提案收口第 {issue['no']} 期）"
    done, failed = [], []
    for n in sorted(picks):
        item, v = items[n], picks[n]
        if dry_run:
            done.append((n, item, v))
            continue
        try:
            write_item(item, v, day, note)
            done.append((n, item, v))
        except OSError as e:
            failed.append((n, item, str(e)))
    by = {}
    for n, item, v in done:
        by.setdefault(v, []).append(f"{n}（{item['id']}）")
    head = "将记下" if dry_run else "已记下"
    print(f"{head}第 {issue['no']} 期：" + "；".join(f"{verb_label(v)} " + "、".join(xs) for v, xs in by.items()) + "。")
    left = [n for n in items if n not in picks]
    if left:
        print(f"没提到的 {len(left)} 条下周一再列：" + "、".join(map(str, left)) + "。")
    for n, item, why in failed:
        print(f"第 {n} 条（{item['id']}）没写进：{why}")
    if not dry_run:
        issue.setdefault("answers", []).append({"at": today().isoformat(timespec="seconds"), "reply": reply,
                                                 "applied": {str(n): v for n, _, v in done}})
        write_state(state)
    return 1 if failed else 0


def main(argv=None):
    a = argparse.ArgumentParser(description="每周提案收口：汇总待决提案发到常浩私聊，按回复写回结论")
    a.add_argument("--dry-run", action="store_true")
    a.add_argument("--force", action="store_true")
    sub = a.add_subparsers(dest="cmd")
    s = sub.add_parser("send")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--force", action="store_true")
    sub.add_parser("list")
    p = sub.add_parser("apply")
    p.add_argument("reply")
    p.add_argument("--dry-run", action="store_true")
    a = a.parse_args(argv)
    try:
        if a.cmd in (None, "send"):
            return cmd_send(a.dry_run, a.force)
        if a.cmd == "list":
            return cmd_list()
        return cmd_apply(a.reply, a.dry_run)
    except OSError as e:
        print(f"提案收口没跑成：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
