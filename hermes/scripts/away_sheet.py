#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""托管单（#305）：常浩不在场、由一个会话替他盯着各条线时，飞书私聊里只有这一张单子。

有进展就在原地更新这张卡片；只有新冒出要他做的事，才另发一句提醒（回复在卡片下面，点一下能跳回卡片）。
卡片样子和文字写法是常浩 2026-11-21 看过真卡片后定稿的（乙一 · 对齐留白，登记在 docs/DECIDED.md）：
流水单，按时间往下加；时间单独一栏；办完的事灰字一行；要他做的事是标题、这件事是做什么的、进展、红字「要你做」四行。
基线在 tests/fixtures/away_sheet/card.json；改样子先走「改动确认」（全局规则第 15 节）。

脚本不调 AI，只管这张单子：每件事的文字由托管会话写进状态文件，脚本排版、发卡、原地更新、回读。
谁来盯会话、怎么代批不归它管。序号由脚本按冒出来的先后发，发了不再变。

  start --by <谁在托管>            开一张新单子（还不发）
  ask --task --what --about --progress --todo   加一件要常浩做的事，打印它的编号
  update <编号> [--what …] [--about …] [--progress …] [--todo …]   改一件还没办完的事的文字
  resolve <编号> --result <一句话>  这件办完了：原地变成一行灰字，编号保留
  done --project --text            加一件办完的事
  note --text                      加一行要他知道的提醒（⚠️）
  doing [--item 任务｜说明｜到哪了 …]  整个换掉「正在办」那几条；不带 --item 就是清空
  end                              托管结束：标题改成已结束，写明这张单子不再更新
  build                            只打印卡片 JSON
  push                             第一次是发卡片，之后是原地更新；回读；新冒出的事另发一句提醒
  replies                          打印常浩引用这张单子（或提醒）回的话
  show                             打印这张单子现在记了什么

除 start 外都对当前这张单子操作（--sheet 指定别的）。时间用 --at（HH:MM 或 ISO），不写就是现在。
状态在 ~/Library/Application Support/DigitalTwin/away/<单号>/sheet.json，发送记录在同目录 runs.jsonl。
环境变量：DT_AWAY_DIR 换目录；DT_NOW（ISO 时间）指定现在；DT_LARK_CLI 换 lark-cli。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aihot_hot import Fail, lark  # noqa: E402  以猛鬼兽机器人跑 lark-cli（去掉会让它改读别的配置的环境变量）
from feishu_home import home_chat  # noqa: E402

TZ = dt.timezone(dt.timedelta(hours=8))
MAX_TAGS = 180     # 飞书卡片正文最多 199 个组件（2026-11-21 实测：195 能发，200 报 element exceeds the limit），留余量
NUMS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
ORDER = {"done": 1, "note": 2, "ask": 3}
INNER = re.compile(r"#\d|\bPR\b|\bissue\b", re.I)
ASK_FIELDS = ("task", "what", "about", "progress", "todo")
HOW = "要回话：引用这张单子，写上编号和你的选择，可以一次回完几件。"
HOW_ENDED = "托管已经结束，这张单子不再更新，引用它回话也没人接。还等你的事，请到 Claude 里对应的会话办。"
FOOT = "单子只有这一张，有进展就在原地更新；只有新冒出要你做的事才另发一句提醒。办完的事是灰字，要你做的是黑字加红字。"


# ---------- 时间和文件 ----------

def current():
    raw = os.environ.get("DT_NOW")
    return dt.datetime.fromisoformat(raw).astimezone(TZ) if raw else dt.datetime.now(TZ)


def iso(t):
    return t.astimezone(TZ).replace(microsecond=0).isoformat()


def when(raw, now):
    """--at 的值：HH:MM（算今天）或 ISO；不写就是现在。"""
    if not raw:
        return iso(now)
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        h, m = raw.split(":")
        return iso(now.replace(hour=int(h), minute=int(m), second=0))
    try:
        t = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise Fail(f"时间看不懂：{raw}（写 HH:MM 或 ISO 时间）")
    return iso(t if t.tzinfo else t.replace(tzinfo=TZ))


def at(s):
    return dt.datetime.fromisoformat(s).astimezone(TZ)


def hm(s):
    return at(s).strftime("%H:%M")


def base():
    return Path(os.environ.get("DT_AWAY_DIR", "~/Library/Application Support/DigitalTwin/away")).expanduser()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load(sheet_id=None):
    if not sheet_id:
        try:
            sheet_id = json.loads((base() / "state.json").read_text(encoding="utf-8"))["current"]
        except (OSError, ValueError, KeyError):
            raise Fail("还没有托管单，先跑 start")
    try:
        sheet = json.loads((base() / sheet_id / "sheet.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Fail(f"托管单 {sheet_id} 读不了")
    if not isinstance(sheet, dict) or sheet.get("version") != 1:
        raise Fail(f"托管单 {sheet_id} 不是这个脚本写的格式")
    return sheet


def save(sheet):
    write_json(base() / sheet["id"] / "sheet.json", sheet)


def log(sheet, rec):
    with open(base() / sheet["id"] / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------- 卡片 ----------

def md(text, size=None):
    e = {"tag": "markdown", "content": text}
    if size:
        e["text_size"] = size
    return e


def red(text):
    return f"<font color='red'>**{text}**</font>"


def grey(text):
    return md(f"<font color='grey'>{text}</font>", "notation")


HR = {"tag": "hr"}


def line(time, right):
    """流水里的一行：左边时间（定宽 48px，飞书分栏的 weight 只认 1–5，窄栏要用定宽），右边内容。"""
    return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px",
            "columns": [{"tag": "column", "width": "48px", "vertical_align": "top",
                         "elements": [md(f"<font color='grey'>{time}</font>")]},
                        {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top",
                         "vertical_spacing": "4px", "elements": right}]}


def panel(title, text):
    return {"tag": "collapsible_panel", "expanded": False, "background_color": "grey-50",
            "border": {"color": "grey", "corner_radius": "6px"}, "padding": "4px 10px 8px 10px", "vertical_spacing": "8px",
            "header": {"title": {"tag": "plain_text", "content": title}, "width": "fill", "vertical_align": "center",
                       "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "color": "grey", "size": "16px 16px"},
                       "icon_position": "right", "icon_expanded_angle": -180},
            "elements": [md(text)]}


def num(n):
    return NUMS[n - 1] if 1 <= n <= len(NUMS) else f"({n})"


def tags(o):
    if isinstance(o, dict):
        return ("tag" in o) + sum(tags(v) for v in o.values())
    if isinstance(o, list):
        return sum(tags(v) for v in o)
    return 0


def is_open(r):
    return r["kind"] == "ask" and not r.get("done_at")


def ordered(sheet):
    rows = list(enumerate(sheet["rows"]))
    rows.sort(key=lambda p: (at(p[1]["at"]).replace(second=0), ORDER[p[1]["kind"]], p[0]))
    return [r for _, r in rows]


def plain(r):
    """办完的一行（办完的事，或已经办完的「要你做」）的文字，不带颜色。"""
    if r["kind"] == "ask":
        return f"✅ {num(r['n'])} **{r['task']}**｜{r['result']}"
    return f"✅ **{r['project']}**｜{r['text']}"


def elements(sheet, fold):
    """正文组件。fold：最早的几件办完的事收成一行折叠（装不下时用）。"""
    rows = ordered(sheet)
    waiting = [r for r in rows if is_open(r)]
    old = [r for r in rows if r["kind"] != "note" and not is_open(r)][:fold]
    if waiting:
        top = md(red(f"还等你 {len(waiting)} 件") + "　往下找红字。每件开头的圆圈数字是它的编号，按冒出来的先后编，回话时带上编号就行。")
    else:
        top = md("**现在没有要你做的事**　下面是已经办的和正在办的。")
    els = [top, HR, line(hm(sheet["started_at"]), [md("▶ 托管开始")])]
    if old:
        els.append(panel(f"✅ {hm(old[0]['at'])}–{hm(old[-1]['at'])}　办完 {len(old)} 件（点开看）",
                         "\n".join(f"- {hm(r['at'])} {plain(r)[2:]}" for r in old)))
    day = at(sheet["started_at"]).date()
    for r in rows:
        if any(r is o for o in old):
            continue
        if at(r["at"]).date() != day:
            day = at(r["at"]).date()
            els.append(grey(f"—— {day.strftime('%m-%d')} ——"))
        t = hm(r["at"])
        if is_open(r):
            row = line(t, [md(f"**{num(r['n'])} {r['task']}｜{r['what']}**"), md(r["about"]),
                           md(f"<font color='grey'>{r['progress']}</font>"), md(red("要你做：" + r["todo"]))])
            row["margin"] = "8px 0px 8px 0px"
            els.append(row)
        elif r["kind"] == "note":
            els.append(line(t, [md("⚠️ " + r["text"])]))
        else:
            els.append(line(t, [md(f"<font color='grey'>{plain(r)}</font>")]))
    if sheet.get("ended_at"):
        els.append(line(hm(sheet["ended_at"]), [md("■ 托管结束")]))
    els.append(HR)
    if sheet.get("doing") and not sheet.get("ended_at"):
        els.append(md("**⏳ 正在办，不用你管**\n" + "\n".join(
            f"- **{d['task']}**" + (f"（{d['about']}）" if d.get("about") else "") + f"：{d['text']}" for d in sheet["doing"])))
    els.append(grey(HOW_ENDED if sheet.get("ended_at") else HOW))
    els.append(grey(f"托管方：{sheet['by']}｜" + FOOT))
    return els


def build_card(sheet, now):
    waiting = sum(1 for r in sheet["rows"] if is_open(r))
    foldable = sum(1 for r in sheet["rows"] if r["kind"] != "note" and not is_open(r))
    fold = 0
    els = elements(sheet, fold)
    while tags(els) > MAX_TAGS:
        if fold >= foldable:
            raise Fail(f"单子装不下了：要你做的事和提醒太多（{tags(els)} 个组件，上限 {MAX_TAGS}）。先 end 这一张，再 start 新的一张")
        fold = max(fold + 1, 2)
        els = elements(sheet, fold)
    started = at(sheet["started_at"]).strftime("%m-%d %H:%M")
    if sheet.get("ended_at"):
        title = f"托管结束｜还等你 {waiting} 件" if waiting else "托管结束｜没有等你的事"
        sub, color = f"{started} 开始｜{hm(sheet['ended_at'])} 结束", "grey"
    else:
        title = f"托管流水｜还等你 {waiting} 件" if waiting else "托管流水｜现在没有等你的事"
        sub, color = f"{started} 开始｜更新于 {now.strftime('%H:%M')}", "red" if waiting else "green"
    return {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title},
                       "subtitle": {"tag": "plain_text", "content": sub}, "template": color},
            "body": {"elements": els}}


# ---------- 改单子 ----------

def start(by, now, raw_at=None):
    t = when(raw_at, now)
    sheet_id = at(t).strftime("%Y-%m-%d-%H%M")
    if (base() / sheet_id / "sheet.json").exists():
        raise Fail(f"托管单 {sheet_id} 已经有了")
    sheet = {"version": 1, "id": sheet_id, "by": by, "started_at": t, "ended_at": None,
             "message_id": None, "pushed_at": None, "reminders": [], "rows": [], "doing": []}
    save(sheet)
    write_json(base() / "state.json", {"current": sheet_id})
    return sheet


def warn_inner(fields):
    hit = [v for v in fields if v and INNER.search(v)]
    if hit:
        print("提醒：文字里有 PR 号、issue 这类内部说法，常浩不在场时看不懂，换成人话：" + "；".join(hit), file=sys.stderr)


def add_ask(sheet, fields, t):
    missing = [k for k in ASK_FIELDS if not (fields.get(k) or "").strip()]
    if missing:
        raise Fail("要常浩做的事五项都要写：" + "、".join(missing) + " 没写")
    warn_inner([fields["task"], fields["what"]])
    n = max([r["n"] for r in sheet["rows"] if r["kind"] == "ask"], default=0) + 1
    row = {"kind": "ask", "n": n, "at": t, "done_at": None, "result": None, "notified": False}
    row.update({k: fields[k].strip() for k in ASK_FIELDS})
    sheet["rows"].append(row)
    return n


def find_ask(sheet, n):
    for r in sheet["rows"]:
        if r["kind"] == "ask" and r["n"] == n:
            return r
    raise Fail(f"没有第 {n} 件")


def update_ask(sheet, n, fields):
    r = find_ask(sheet, n)
    if r["done_at"]:
        raise Fail(f"第 {n} 件已经办完了，不能再改")
    changed = {k: v.strip() for k, v in fields.items() if k in ASK_FIELDS[1:] and v and v.strip()}
    if not changed:
        raise Fail("没写要改哪一项")
    r.update(changed)


def resolve(sheet, n, result, t):
    r = find_ask(sheet, n)
    if r["done_at"]:
        raise Fail(f"第 {n} 件已经办完了")
    if not (result or "").strip():
        raise Fail("办完了要写一句结果（--result）")
    r.update(done_at=t, result=result.strip())


def set_doing(sheet, items):
    doing = []
    for raw in items or []:
        parts = [p.strip() for p in raw.split("｜")]
        if len(parts) != 3 or not parts[0] or not parts[2]:
            raise Fail(f"--item 写成「任务｜说明｜到哪了」（说明可以空着）：{raw}")
        doing.append({"task": parts[0], "about": parts[1], "text": parts[2]})
    sheet["doing"] = doing


# ---------- 收发 ----------

def chat_id():
    chat = home_chat()
    if not chat.startswith("oc_"):
        raise Fail("没有私聊 chat_id（FEISHU_HOME_CHANNEL，环境变量和 ~/.hermes/.env 里都没有）")
    return chat


def push(sheet, now):
    """发卡片或原地更新，回读；新冒出要他做的事另发一句提醒。返回记进 runs.jsonl 的一条。"""
    rec = {"at": iso(now), "result": None}
    try:
        card = build_card(sheet, now)
        content = json.dumps(card, ensure_ascii=False)
        fresh = [r for r in sheet["rows"] if is_open(r) and not r["notified"]]
        if not sheet.get("message_id"):
            mid = lark(["im", "+messages-send", "--chat-id", chat_id(), "--msg-type", "interactive", "--content", content,
                        "--idempotency-key", f"away-sheet-{sheet['id']}"], "发托管单").get("message_id")
            if not mid:
                raise Fail("发托管单没拿到消息号")
            sheet["message_id"] = mid
            rec["result"] = "sent"
        else:
            lark(["im", "messages", "patch", "--message-id", sheet["message_id"],
                  "--data", json.dumps({"content": content}, ensure_ascii=False)], "更新托管单")
            rec["result"] = "patched"
            if fresh and not sheet.get("ended_at"):
                names = "；".join(f"{num(r['n'])} {r['task']}｜{r['what']}" for r in fresh)
                text = f"托管单多了 {len(fresh)} 件要你做的：{names}。细节和怎么回，看上面引用的那张单子。"
                rid = lark(["im", "+messages-reply", "--message-id", sheet["message_id"], "--text", text, "--idempotency-key",
                            f"away-{sheet['id']}-n{'-'.join(str(r['n']) for r in fresh)}"], "发提醒").get("message_id")
                sheet["reminders"].append({"message_id": rid, "at": iso(now), "asks": [r["n"] for r in fresh]})
                rec["reminded"] = [r["n"] for r in fresh]
        for r in fresh:
            r["notified"] = True
        sheet["pushed_at"] = iso(now)
        rec.update(message_id=sheet["message_id"], tags=tags(card["body"]))
        save(sheet)
        write_json(base() / sheet["id"] / "card.json", card)
        m = (lark(["im", "+messages-mget", "--message-ids", sheet["message_id"]], "回读").get("messages") or [{}])[0]
        if m.get("msg_type") != "interactive" or card["header"]["title"]["content"] not in str(m.get("content")):
            rec["result"] += "_unverified"
            raise Fail(f"托管单已{'发' if rec['result'].startswith('sent') else '更新'}（{sheet['message_id']}），但回读对不上卡片内容")
        return rec
    except Fail as e:
        rec["result"] = rec["result"] or "failed"
        rec["error"] = str(e)
        raise
    finally:
        log(sheet, rec)


def replies(sheet):
    """常浩引用这张单子（或它下面的提醒）回的话，旧的在前。只认他私聊里用户发的、引用对象是这张单子的消息；
    私聊里的用户只有他本人。字段以 2026-11-21 实测的 lark-cli 输出为准：sender.sender_type、reply_to。"""
    if not sheet.get("message_id"):
        raise Fail("这张单子还没发")
    mine = {sheet["message_id"]} | {r["message_id"] for r in sheet.get("reminders", []) if r.get("message_id")}
    data = lark(["im", "+chat-messages-list", "--chat-id", chat_id(), "--start", sheet["started_at"],
                 "--order", "asc", "--page-size", "50", "--page-all", "--page-limit", "20"], "读私聊")
    return [{"message_id": m.get("message_id"), "at": m.get("create_time"), "text": m.get("content")}
            for m in data.get("messages") or []
            if (m.get("sender") or {}).get("sender_type") == "user" and m.get("reply_to") in mine]


def show(sheet):
    out = [f"托管单 {sheet['id']}｜{sheet['by']}｜" + ("已结束" if sheet.get("ended_at") else "进行中")
           + ("｜还没发" if not sheet.get("message_id") else f"｜上次推送 {sheet['pushed_at']}")]
    for r in ordered(sheet):
        if r["kind"] == "ask":
            out.append(f"{hm(r['at'])} {num(r['n'])} {r['task']}｜{r['what']}　" + (f"办完：{r['result']}" if r["done_at"] else "等常浩"))
        elif r["kind"] == "note":
            out.append(f"{hm(r['at'])} ⚠️ {r['text']}")
        else:
            out.append(f"{hm(r['at'])} ✅ {r['project']}｜{r['text']}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="托管单：常浩不在场时，飞书里一张原地更新的单子")
    ap.add_argument("--sheet", help="单号；不写就是当前这张")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start")
    s.add_argument("--by", required=True, help="谁在托管，例如：Claude 会话「数字分身｜离开时托管推进」")
    s.add_argument("--at")
    s = sub.add_parser("ask")
    for k in ASK_FIELDS:
        s.add_argument("--" + k, required=True)
    s.add_argument("--at")
    s = sub.add_parser("update")
    s.add_argument("n", type=int)
    for k in ASK_FIELDS[1:]:
        s.add_argument("--" + k)
    s = sub.add_parser("resolve")
    s.add_argument("n", type=int)
    s.add_argument("--result", required=True)
    s.add_argument("--at")
    s = sub.add_parser("done")
    s.add_argument("--project", required=True)
    s.add_argument("--text", required=True)
    s.add_argument("--at")
    s = sub.add_parser("note")
    s.add_argument("--text", required=True)
    s.add_argument("--at")
    s = sub.add_parser("doing")
    s.add_argument("--item", action="append")
    s = sub.add_parser("end")
    s.add_argument("--at")
    for name in ("build", "push", "replies", "show"):
        sub.add_parser(name)
    a = ap.parse_args(argv)
    now = current()
    try:
        if a.cmd == "start":
            print(start(a.by, now, a.at)["id"])
            return 0
        sheet = load(a.sheet)
        if a.cmd in ("ask", "update", "resolve", "done", "note", "doing", "end") and sheet.get("ended_at"):
            raise Fail(f"托管单 {sheet['id']} 已经结束，不能再改")
        if a.cmd == "ask":
            print(add_ask(sheet, {k: getattr(a, k) for k in ASK_FIELDS}, when(a.at, now)))
        elif a.cmd == "update":
            update_ask(sheet, a.n, {k: getattr(a, k) for k in ASK_FIELDS[1:]})
        elif a.cmd == "resolve":
            resolve(sheet, a.n, a.result, when(a.at, now))
        elif a.cmd == "done":
            warn_inner([a.project])
            sheet["rows"].append({"kind": "done", "at": when(a.at, now), "project": a.project.strip(), "text": a.text.strip()})
        elif a.cmd == "note":
            sheet["rows"].append({"kind": "note", "at": when(a.at, now), "text": a.text.strip()})
        elif a.cmd == "doing":
            set_doing(sheet, a.item)
        elif a.cmd == "end":
            sheet["ended_at"] = when(a.at, now)
        elif a.cmd == "build":
            print(json.dumps(build_card(sheet, now), ensure_ascii=False, indent=1))
            return 0
        elif a.cmd == "push":
            rec = push(sheet, now)
            print(json.dumps(rec, ensure_ascii=False))
            return 0
        elif a.cmd == "replies":
            print(json.dumps(replies(sheet), ensure_ascii=False, indent=1))
            return 0
        elif a.cmd == "show":
            print(show(sheet))
            return 0
        save(sheet)
        return 0
    except Fail as e:
        print(f"托管单没办成：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
