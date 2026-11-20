"""记忆日报取数（亚古兽，PRD D20 2026-11-11 修订）：给亚古兽写《常浩-记忆日报汇总》用的材料。只读。

亚古兽接着写 Aily 的记忆日报（六模块，7/15 永久模板），取数口径沿用 Aily v6.0 的规则：
- 私聊：常浩当天所有有活动的私聊，排除机器人（查联系人拿不到名字的就是机器人：猛鬼兽、亚古兽、Aily 等）。
- 群聊：白名单群当天全读；其他群只收常浩发过言或被 @ 的。
- 会议：以视频会议里常浩实际参会的记录和妙记为准；日历有 ≠ 参加；永久不去的会议跳过。
- 统计日：D 当天 00:00—23:59:59（北京时间），次日早上写。周末没有工作往来就跳过。

本机配置（白名单群、永久不去的会议、重点人）在 ~/Library/Application Support/DigitalTwin/memdaily/config.json，不进仓库。
输出：python3 memdaily_data.py --date 2026-11-11 打印 Markdown 材料；--json 打印结构化结果。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import netwait  # noqa: E402  唤醒后等网络、网络错误判断的共用实现（P9）
from report_data import clean, wd  # noqa: E402  消息清洗、星期写法和工作日报共用

LARK = os.environ.get("DT_LARK_CLI", "lark-cli")
PROFILE = "cli_a965c429ef785bd3"               # 亚古兽应用，常浩的用户身份
ME = "ou_6fac84a8164f84c0c591a9b3a98b462c"      # 常浩（亚古兽应用下的 open_id）
AGENT_ENV = ("HERMES", "OPENCLAW", "LARK_CHANNEL")
DOC = os.environ.get("DT_MEMDAILY_DOC", "TCG9dy7UcoZIaexTJcfcAN8Mn9g")   # 《常浩-记忆日报汇总》
DT_HOME = Path(os.environ.get("DT_HOME", "~/Library/Application Support/DigitalTwin")).expanduser()
CONFIG = Path(os.environ.get("DT_MEMDAILY_CONFIG", DT_HOME / "memdaily" / "config.json"))
VOCAB = Path(os.environ.get("DT_VOCAB", Path(__file__).parents[1] / "prompts" / "project_vocab.md")).expanduser()
HEAD = re.compile(r"^## 📅 (\d{4}-\d{2}-\d{2})", re.M)
# 写到哪几份文档（2026-11-17 常浩选方案甲）：旧汇总文档到了飞书单文档 4 万块上限，原样归档、不再写；
# 从 11-14 起每月一份《常浩-记忆日报汇总 · YYYY-MM》，写满提前开「续」。登记文件只由 memdaily_publish 写。
REGISTRY = Path(os.environ.get("DT_MEMDAILY_DOCS", DT_HOME / "memdaily" / "docs.json"))


class Fail(Exception):
    pass


def lark(*args, identity="user", cwd=None, tries=None, sleep=time.sleep, clock=time.monotonic):
    """以亚古兽应用调 lark-cli（默认常浩的用户身份；推送用 identity="bot"），返回 data。

    飞书报「可重试」或网络错误（netwait.is_net_error：超时、连接断开、连不上、刷新令牌失败、导出要重试；输出不是 JSON
    时看原文）时，按 netwait.Budget 每 15 秒重试，这一次调用合计最多等 DT_NET_WAIT_SECONDS 秒（默认 300，P9 起和
    pdaily 的唤醒等待同一套，#176）。11-12 首跑：睡眠醒来读文档超时、飞书「could not export … Retry the export」；
    11-14 07:45 唤醒后网络没通，刷新令牌失败（dial tcp）。写文档的调用传 tries=1，不重试，免得写两遍。"""
    # lark-cli 看到 Hermes 的环境变量会改读另一套配置，这里去掉（同 report_data.lark）
    env = {k: v for k, v in os.environ.items() if not k.lstrip("_").startswith(AGENT_ENV)}
    budget = netwait.Budget(0 if tries == 1 else None, sleep=sleep, clock=clock)
    while True:
        r = subprocess.run([LARK, "--profile", PROFILE, *args, "--as", identity], capture_output=True, text=True,
                           timeout=300, env=env, cwd=cwd)
        try:
            d = json.loads(r.stdout or r.stderr)
        except ValueError:
            raw = r.stderr or r.stdout
            if not netwait.is_net_error(raw) or not budget.retry():
                raise Fail(f"{args[0]} {args[1]} 输出不是 JSON（{raw[:200]}）")
            continue
        if d.get("ok", True):
            return d.get("data")
        err = d.get("error") or {}
        msg = str(err.get("message", "未知错误"))
        if not (err.get("retryable") or netwait.is_net_error(msg)) or not budget.retry():
            raise Fail(f"{args[0]} {args[1]}：{msg[:200]}")


def legacy():
    return {"token": DOC, "title": "常浩-记忆日报汇总 v4.0", "month": None, "note": "2026-04-27～2026-11-13 的归档，不再写入"}


def load_registry():
    """{"folder": 新文档放哪个文件夹（空 = 不指定，飞书放到常浩云空间的默认位置）, "docs": [{token, title, month}, …]}，按建的先后。

    没有登记文件 = 只有旧汇总文档（month 为 None）。"""
    try:
        reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        docs = [x for x in reg.get("docs") or [] if isinstance(x, dict) and x.get("token")]
        return {"folder": str(reg.get("folder") or ""), "docs": docs or [legacy()]}
    except (OSError, ValueError, AttributeError):
        return {"folder": "", "docs": [legacy()]}


def save_registry(reg):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(REGISTRY)


def read_order(reg, d):
    """找「已写过」和「前一篇」要读的文档，新的在前：d 所在月及以前的月文档（同月后建的在前），最后是旧汇总文档。"""
    month = d.isoformat()[:7]
    monthly = [(x["month"], i, x) for i, x in enumerate(reg["docs"]) if x.get("month") and x["month"] <= month]
    return [x for *_, x in sorted(monthly, key=lambda t: t[:2], reverse=True)] + [x for x in reg["docs"] if not x.get("month")]


def load_config(path=CONFIG):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def work_lines(path=VOCAB):
    """项目词表里「工作线」表的名称（个人项目不算），任务主题写成「T线-名称」。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    m = re.search(r"^## 工作线\s*$(.*?)(?=^## )", text, re.M | re.S)
    rows = re.findall(r"^\|\s*([^|\s][^|]*?)\s*\|", m.group(1), re.M) if m else []
    return [r for r in rows if r not in ("名称",) and not set(r) <= set("-: ")]


def day_range(d):
    return f"{d.isoformat()}T00:00:00+08:00", f"{d.isoformat()}T23:59:59+08:00"


def prior_block(content, d):
    """记忆日报里日期早于 d 的最新一篇 (日期, 正文)；d 已经写过返回 'done'。"""
    ms = list(HEAD.finditer(content))
    best = None
    for i, m in enumerate(ms):
        day = m.group(1)
        if day == d.isoformat():
            return "done", None
        if day < d.isoformat() and (best is None or day > best[0]):
            end = ms[i + 1].start() if i + 1 < len(ms) else len(content)
            best = (day, content[m.start():end])
    return "ok", best


def section(body, title):
    """取「### 四、⚡ 行动 & 阻塞」这类模块的正文（到下一个 ### 为止）。"""
    m = re.search(rf"^###\s*{re.escape(title)}.*$", body, re.M)
    if not m:
        return ""
    nxt = re.search(r"^### ", body[m.end():], re.M)
    return body[m.start(): m.end() + (nxt.start() if nxt else len(body) - m.end())].strip()


def tidy(text):
    """去掉空表格行和空行。10-27 起的旧篇表格是空壳、值散在表外，整理后一行一个值，写稿时能读。"""
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln and not re.fullmatch(r"\|[\s|:\-]*\|?", ln))


def is_bot(open_id, run):
    """查联系人：机器人能查到但没有名字（2026-11-11 实测猛鬼兽、亚古兽都是这样）。"""
    try:
        user = (run("contact", "+get-user", "--user-id", open_id) or {}).get("user") or {}
    except Fail:
        return False   # 查不到按真人处理：宁可多收，由写稿时的过滤规则剔除
    return not user.get("name")


def mentioned(m):
    return any(ME == y.get("id") for y in m.get("mentions") or [])


def collect(d, run=lark, config=None):
    """取 D 当天的全部材料，返回 dict。skip 不为空表示不写（已写过 / 周末没有工作往来）。"""
    config = load_config() if config is None else config
    s, e = day_range(d)
    facts = {"date": d.isoformat(), "title": f"📅 {wd(d)}", "skip": None, "prev": None}
    prev = None
    for doc in read_order(load_registry(), d):   # 新文档还空着时，前一篇从更早的文档读
        content = (run("docs", "+fetch", "--doc", doc["token"], "--doc-format", "markdown") or {}).get("document", {}).get("content", "")
        state, prev = prior_block(content, d)
        if state == "done":
            facts["skip"] = f"{d.isoformat()} 已经写在记忆日报里，不重写"
            return facts
        if prev:
            break
    if prev:
        facts["prev"] = {"date": prev[0], "actions": tidy(section(prev[1], "四、"))}

    msgs = (run("im", "+messages-search", "--start", s, "--end", e, "--page-all", "--page-size", "50",
                "--no-reactions", "--exclude-sender-type", "bot") or {}).get("messages") or []
    p2p, groups = {}, {}
    for m in msgs:
        (p2p if m.get("chat_type") == "p2p" else groups).setdefault(m["chat_id"], []).append(m)

    bots, private = [], []
    for cid, ms in p2p.items():
        partner = (ms[0].get("chat_partner") or {}).get("open_id")
        if partner and partner != ME and is_bot(partner, run):
            bots.append(cid)
            continue
        if partner == ME:
            name = "常浩发给自己的备忘"
        else:
            name = next((x["sender"].get("name") for x in ms if x["sender"].get("id") != ME), None) or "（对方当天没说话）"
        private.append({"name": name, "messages": ms})

    white = {g["chat_id"]: g["name"] for g in config.get("whitelist_groups") or []}
    kept, skipped = [], 0
    for cid, ms in groups.items():
        if cid in white:
            continue
        if any(x["sender"].get("id") == ME for x in ms) or any(mentioned(x) for x in ms):
            kept.append({"name": ms[0].get("chat_name") or cid[-6:], "whitelist": False, "messages": ms})
        else:
            skipped += 1
    for cid, name in white.items():
        ms = (run("im", "+chat-messages-list", "--chat-id", cid, "--start", s, "--end", e, "--order", "asc",
                  "--page-all", "--no-reactions") or {}).get("messages") or []
        ms = [x for x in ms if (x.get("sender") or {}).get("sender_type", "user") != "app"]
        if ms:
            kept.append({"name": name, "whitelist": True, "messages": ms})

    meetings = (run("vc", "+search", "--start", d.isoformat(), "--end", d.isoformat(), "--participant-ids", ME) or {}).get("items") or []
    minutes = (run("minutes", "+search", "--start", d.isoformat(), "--end", (d + dt.timedelta(days=1)).isoformat(),
                   "--participant-ids", "me") or {}).get("items") or []
    tokens = [t.group(1) for t in (re.search(r"/minutes/(\w+)", (x.get("meta_data") or {}).get("app_link", "")) for x in minutes) if t]
    minute_detail = run("minutes", "+detail", "--minute-tokens", ",".join(tokens), "--summary", "--todo") if tokens else None
    excluded = config.get("excluded_meetings") or []
    agenda = []
    for ev in run("calendar", "+agenda", "--start", s, "--end", e) or []:
        summary = ev.get("summary", "")
        agenda.append({"time": f"{ev.get('start_time', {}).get('datetime', '')[11:16]}-{ev.get('end_time', {}).get('datetime', '')[11:16]}",
                       "summary": summary, "organizer": (ev.get("event_organizer") or {}).get("display_name", ""),
                       "rsvp": ev.get("self_rsvp_status", ""), "excluded": any(k in summary for k in excluded)})

    tasks = []   # 常浩负责、当天新建或截止在 D-7～D+14 的未完成任务
    for t in (run("task", "+get-related-tasks", "--include-complete=false") or {}).get("items") or []:
        if not any(x.get("id") == ME and x.get("role") == "assignee" for x in t.get("members") or []):
            continue
        due = (t.get("due") or {}).get("timestamp")
        due = dt.datetime.fromtimestamp(int(due) / 1000).date() if due else None
        if (t.get("created_at") or "")[:10] == d.isoformat() or (due and -7 <= (due - d).days <= 14):
            tasks.append({"summary": t.get("summary", ""), "due": due.isoformat() if due else None})

    mine = sum(1 for m in msgs if m["sender"].get("id") == ME)
    if d.weekday() >= 5 and not mine and not meetings and not minutes:
        facts["skip"] = f"{wd(d)} 是周末，常浩没有发言、没有会议，不写（不算断档）"
        return facts

    facts.update({
        "private": private, "groups": kept, "meetings": [x.get("display_info", "").splitlines()[0] for x in meetings if x.get("display_info")],
        "minutes": minute_detail, "agenda": agenda, "tasks": tasks,
        "work_lines": work_lines(), "priority_people": config.get("priority_people") or [],
        "dropped_topics": config.get("dropped_topics") or [],
        "coverage": {"private": len(private), "groups": len(kept), "whitelist_groups": sum(1 for g in kept if g["whitelist"]),
                     "skipped_groups": skipped, "bot_chats": len(bots), "meetings": len(meetings), "minutes": len(minutes),
                     "agenda": len(agenda), "tasks": len(tasks), "messages": len(msgs) + sum(len(g["messages"]) for g in kept if g["whitelist"]),
                     "config": bool(config)},
    })
    return facts


def chat_lines(ms):
    out = []
    for m in sorted(ms, key=lambda x: x.get("create_time", "")):
        who = "常浩" if m["sender"].get("id") == ME else m["sender"].get("name") or "?"
        out.append(f"- {m.get('create_time', '')[11:16]} {who}：{clean(m)}")
    return out


def material(facts):
    """给写稿用的 Markdown 材料。"""
    if facts.get("skip"):
        return f"SKIP：{facts['skip']}"
    c = facts["coverage"]
    out = [f"# 记忆日报材料：{facts['title'][2:]}", ""]
    out += ["## 前一篇的行动 & 阻塞（没关闭的要接着写）", ""]
    if facts.get("prev") and facts["prev"].get("actions"):
        out += [f"依据记忆日报 {facts['prev']['date']} 那篇（常浩在文档里改过的，以文档为准）：", "", facts["prev"]["actions"], ""]
    else:
        out += ["（没有找到前一篇的行动 & 阻塞）", ""]
    out += [f"## 私聊（{c['private']} 个会话；和机器人的 {c['bot_chats']} 个不算）", ""]
    for p in facts["private"]:
        out += [f"### 私聊 {p['name']}", *chat_lines(p["messages"]), ""]
    out += [f"## 群聊（{c['groups']} 个；白名单群 {c['whitelist_groups']} 个全读，其他群只收常浩发言或被 @ 的，略过 {c['skipped_groups']} 个）", ""]
    for g in facts["groups"]:
        out += [f"### 群「{g['name']}」{'（白名单）' if g['whitelist'] else ''}", *chat_lines(g["messages"]), ""]
    out += [f"## 实际参会的视频会议（{c['meetings']} 场）和妙记（{c['minutes']} 条）", ""]
    out += [f"- 会议：{x}" for x in facts["meetings"]]
    if facts.get("minutes"):
        out.append(json.dumps(facts["minutes"], ensure_ascii=False)[:8000])
    out.append("")
    out += [f"## 日程（{c['agenda']} 个；日历有 ≠ 参加，要有会议记录、妙记或消息佐证才算参加）", ""]
    for a in facts["agenda"]:
        mark = "｜永久不去，跳过" if a["excluded"] else ""
        out.append(f"- {a['time']} {a['summary']}｜组织：{a['organizer']}｜我的回复：{a['rsvp']}{mark}")
    out += ["", f"## 飞书任务（常浩负责、近期到期或当天新建的未完成任务 {c['tasks']} 个）", ""]
    out += [f"- {t['summary']}｜截止：{t['due'] or '无'}" for t in facts["tasks"]]
    out += ["", "## 任务主题可用的工作线（写成「T线-名称」，归不进写「T线-其他」）", "", "、".join(facts["work_lines"]) or "（词表没读到）"]
    if facts["priority_people"]:
        out += ["", "## 最高优先的人（整理时优先，不漏）", "", "、".join(facts["priority_people"])]
    if facts["dropped_topics"]:
        out += ["", "## 不再追踪的话题（一律不写）", "", "、".join(facts["dropped_topics"])]
    out += ["", "## 覆盖", "",
            f"- 私聊 {c['private']}；群聊 {c['groups']}（白名单 {c['whitelist_groups']}）；会议 {c['meetings']}；妙记 {c['minutes']}；"
            f"日程 {c['agenda']}；任务 {c['tasks']}；消息共 {c['messages']} 条",
            "- 没接的来源：文档编辑记录" + ("" if c["config"] else "；本机配置没读到（白名单群、永久不去的会议都没用上）")]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    d = dt.date.fromisoformat(a.date)
    try:
        facts = collect(d)
    except Fail as e:
        print(f"取数失败：{e}")
        return 1
    print(json.dumps(facts, ensure_ascii=False, indent=1) if a.json else material(facts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
