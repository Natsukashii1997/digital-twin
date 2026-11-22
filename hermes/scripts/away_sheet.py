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
  build                            只打印卡片 JSON（下一次 push 会推的样子）
  push                             第一次是发卡片，之后是原地更新；回读；新冒出的事另发一句提醒
  adopt --message-id <消息号>       发了却没拿到消息号、飞书上又不止一张时，人工指定哪一张是这张单子
  replies                          打印常浩引用这张单子（或提醒）回的话和每句现在的状态；只读，不记账
  ack <消息号> --version <版本> --result <一句话>   这句话办完了（或处理过了）才记账
  show                             打印这张单子现在记了什么

除 start 外都对当前这张单子操作（要指定别的，把 --sheet <单号> 写在子命令前面）。时间用 --at（HH:MM 或 ISO），不写就是现在；
HH:MM 比现在还晚就算昨天的。要常浩做的事不能排到已有编号的前面（编号和流水里的先后要一致）。
改单子的命令当场检查卡片装不装得下（飞书卡片正文最多 199 个组件），装不下就不收这一条。
同一时间只有一条命令在改（文件锁），几条命令并行发也不丢。
状态在 ~/Library/Application Support/DigitalTwin/away/：state.json 记当前是哪张，<单号>/ 下是 sheet.json、
runs.jsonl（每次推送、认领、ack 各一条；推送分开记卡片、提醒、回读三步）、card.json（最近一次推的卡片）。

发到哪、认谁（L3 审查 P1-03）：只认本机固定位置的两个文件 owner_open_id、owner_chat_id（twin_identity.py setup /
setup-chat 写的；位置按系统账号的家目录算，不认环境变量、不认 HOME；必须是本人名下的普通文件、权限 600），
并向飞书核实那个会话是单聊。已知局限：同一个 macOS 账号下的进程仍然改得了这两个文件，和本项目其他脚本一样，
真隔离要等独立账号（ADR-015）。

发卡片（P1-02）：发之前先把「要发」记进单子；发了却没拿到消息号算结果不明，这张单子冻结：不能开下一张，
下次 push 先去飞书找（机器人发的、页脚带这张单子单号的卡片）：找到一张就认领、接着原地更新；一张都没有，
要离上次发满 120 秒才重发；不止一张就停下，用 adopt 指定。每次 push 版本号加一，写在页脚「单号 … · 第 N 版」，
回读要对上这一版才算推到了（P2-01）。提醒按批记账（P2-02）：每批先记再发，结果不明的下次先对账。
push 一次最多要跑七八条飞书命令（每条最多 60 秒），都在文件锁里；外层给它的超时不要短于 10 分钟。

回话（P1-04、P1-05、P2-03、P2-04、P2-05）：replies 只认常浩本人（发送人是用户、ID 类型 open_id、ID 对得上）
在这个单聊里引用这张单子或它的提醒发的消息，一页页翻到底，翻不完就报错不截断。每句带 state：
  pending             还没办，可以办
  edited              他改过这句：改过的不当授权，请他重发一条新的
  recalled            他撤回了，不用办
  other_type          不是文字（图片、合并转发等）：不当指令，内容不给
  acked               办过了，之后没变
  edited_after_ack    办过之后他又改了
  recalled_after_ack  办过之后他撤回了
  missing_after_ack   办过的那句现在读不到了
读了不算办了：办完（或处理过）才 ack，带上 replies 给的 version；ack 时那句已经变了就不收，重新 replies。
托管会话中途断了，没 ack 的下次还是 pending：合并这类动作，办之前先查是不是已经办过。
只读引用回复；在话题里回的不读（飞书把话题回复放在另一处），卡片上写的就是「引用这张单子」。
撤回、编辑还没有常浩本人的真实样本，字段按 lark-cli 列消息的输出（deleted、updated、update_time）处理。

环境变量：DT_AWAY_DIR 换目录；DT_NOW（ISO 时间）指定现在；DT_LARK_CLI 换 lark-cli。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aihot_hot import Fail, lark  # noqa: E402  以猛鬼兽机器人跑 lark-cli（去掉会让它改读别的配置的环境变量）

TZ = dt.timezone(dt.timedelta(hours=8))
MAX_TAGS = 180     # 飞书卡片正文最多 199 个组件（2026-11-21 实测：195 能发，200 报 element exceeds the limit），留余量
END_TAGS = 195     # 结束时多一行「托管结束」：进行中每次改动都保证不超过 MAX_TAGS，所以结束状态一定推得出去
QUIET = 120        # 发了却没拿到消息号：离上次发满这么多秒、去飞书对账也没找到，才重发
PAGE_MAX = 200     # 读私聊最多翻这么多页（每页 50 条）；翻不完就报错，不截断着用
TEXT_TYPES = ("text", "post")
OWNER_DIR = Path(pwd.getpwuid(os.getuid()).pw_dir) / "Library" / "Application Support" / "DigitalTwin"   # 不认 HOME
NUMS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
ORDER = {"done": 1, "note": 2, "ask": 3}
INNER = re.compile(r"#\d|(?<![A-Za-z])(PR|issue)(?![A-Za-z])", re.I)
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
    """--at 的值：HH:MM（算今天，比现在还晚就算昨天）或 ISO；不写就是现在。"""
    if not raw:
        return iso(now)
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}", raw):
            h, m = raw.split(":")
            t = now.replace(hour=int(h), minute=int(m), second=0)
            return iso(t - dt.timedelta(days=1) if t > now + dt.timedelta(minutes=1) else t)
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


@contextlib.contextmanager
def locked():
    """同一时间只有一条命令读改写（托管会话常把几条命令并行发出）。"""
    base().mkdir(parents=True, exist_ok=True)
    with open(base() / "lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
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
    for k, v in (("rev", 0), ("sending", None), ("chat_id", None), ("acks", {})):
        sheet.setdefault(k, v)
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


def mark(sheet):
    """页脚里的单号和版本：对账靠单号找回这张卡，回读靠「第几版」确认真的更新上去了。"""
    return f"单号 {sheet['id']} · 第 {sheet.get('rev', 0)} 版"


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
    els.append(grey(f"托管方：{sheet['by']}｜{FOOT}｜{mark(sheet)}"))
    return els


def build_card(sheet, now):
    waiting = sum(1 for r in sheet["rows"] if is_open(r))
    foldable = sum(1 for r in sheet["rows"] if r["kind"] != "note" and not is_open(r))
    fold = 0
    els = elements(sheet, fold)
    while tags(els) > MAX_TAGS and fold < foldable:
        fold = max(fold + 1, 2)
        els = elements(sheet, fold)
    if tags(els) > (END_TAGS if sheet.get("ended_at") else MAX_TAGS):
        raise Fail(f"这张单子装不下了：要你做的事和提醒太多（{tags(els)} 个组件，上限 {MAX_TAGS}）")
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
    if not (by or "").strip():
        raise Fail("要写谁在托管（--by）")
    try:
        last = load()
    except Fail:
        last = None
    if last and not last.get("ended_at"):
        raise Fail(f"托管单 {last['id']} 还没结束，先 end（再 push 一次，把结束状态推到飞书）")
    if last and last.get("sending"):       # 发了却没拿到消息号：飞书上可能已经有那张单子，先对清楚
        raise Fail(f"托管单 {last['id']} 上次发卡片结果不明（飞书上可能已经有这张单子），先 push 对账")
    if last and last.get("message_id") and last.get("dirty"):
        raise Fail(f"托管单 {last['id']} 的结束状态还没推到飞书，先 push")
    t = when(raw_at, now)
    sheet_id = at(t).strftime("%Y-%m-%d-%H%M")
    if (base() / sheet_id / "sheet.json").exists():
        raise Fail(f"托管单 {sheet_id} 已经有了")
    sheet = {"version": 1, "id": sheet_id, "by": by.strip(), "started_at": t, "ended_at": None, "message_id": None,
             "chat_id": None, "sending": None, "rev": 0, "pushed_at": None, "dirty": True, "reminders": [], "acks": {},
             "rows": [], "doing": []}
    save(sheet)
    write_json(base() / "state.json", {"current": sheet_id})
    return sheet


def placed(sheet, t, ask=False):
    """一行的时间：不能早于开单；要常浩做的事不能排到已有编号的前面（编号按先后发，流水也按先后排）。"""
    if at(t) < at(sheet["started_at"]).replace(second=0):
        raise Fail(f"时间 {hm(t)} 比开单（{hm(sheet['started_at'])}）还早")
    if ask:
        latest = max([at(r["at"]) for r in sheet["rows"] if r["kind"] == "ask"], default=None)
        if latest and at(t).replace(second=0) < latest.replace(second=0):
            raise Fail(f"要常浩做的事不能排到已有编号的前面：最后一件是 {latest.strftime('%H:%M')}，这件写的是 {hm(t)}")
    return t


def add_row(sheet, kind, t, **fields):
    empty = [k for k, v in fields.items() if not (v or "").strip()]
    if empty:
        raise Fail("、".join(empty) + " 不能空着")
    sheet["rows"].append(dict({"kind": kind, "at": placed(sheet, t)}, **{k: v.strip() for k, v in fields.items()}))


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
    row = {"kind": "ask", "n": n, "at": placed(sheet, t, ask=True), "done_at": None, "result": None, "notified": False}
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
    warn_inner([changed.get("what")])
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


# ---------- 发到哪、认谁 ----------

def owner():
    """常浩的 open_id 和他与猛鬼兽的私聊号：只认 OWNER_DIR 下的两个文件，不认环境变量。"""
    out = []
    for name, what, prefix in (("owner_open_id", "常浩的 open_id", "ou_"), ("owner_chat_id", "常浩的私聊号", "oc_")):
        p = OWNER_DIR / name
        try:
            st = os.lstat(p)
            lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except (OSError, ValueError):
            raise Fail(f"本机没配置{what}（{p}，用 twin_identity.py setup / setup-chat 写），不发也不读")
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
            raise Fail(f"{what}的配置文件 {p} 必须是本人名下的普通文件、权限 600，否则不认")
        if not lines or not lines[0].startswith(prefix):
            raise Fail(f"{what}的配置文件 {p} 里不是 {prefix} 开头的 ID")
        out.append(lines[0])
    return out


def private_chat():
    """常浩的 open_id 和私聊号，并向飞书核实这个会话确实是单聊（只看 oc_ 开头不够，群也是 oc_）。"""
    who, chat = owner()
    if lark(["im", "chats", "get", "--params", json.dumps({"chat_id": chat})], "查会话").get("chat_mode") != "p2p":
        raise Fail("本机配置的私聊号不是单聊，不发也不读")
    return who, chat


def chat_messages(chat, start):
    """这个会话里从 start 起的全部消息，旧的在前。一页页翻到底；翻不完就报错，不截断（截断会漏掉后面的回话）。"""
    out, token = [], None
    for _ in range(PAGE_MAX):
        args = ["im", "+chat-messages-list", "--chat-id", chat, "--start", start, "--order", "asc", "--page-size", "50",
                "--no-reactions"]
        data = lark(args + (["--page-token", token] if token else []), "读私聊")
        out += data.get("messages") or []
        if not data.get("has_more"):
            return out
        token = data.get("page_token")
        if not token:
            raise Fail("读私聊：还有下一页，但飞书没给翻页标记")
    raise Fail(f"读私聊：翻了 {PAGE_MAX} 页还没到底，不截断着用")


def one_message(mid, what):
    m = (lark(["im", "+messages-mget", "--message-ids", mid], what).get("messages") or [{}])[0]
    return m if m.get("message_id") == mid else {}


def from_bot(m):
    return (m.get("sender") or {}).get("sender_type") == "app" and not m.get("deleted")


def before(t):
    """对账从哪儿读起：比记下的时间早 5 分钟（本机和飞书的钟可能差一点）。"""
    return iso(at(t) - dt.timedelta(minutes=5))


def is_card(sheet, m):
    return m.get("msg_type") == "interactive" and from_bot(m) and f"单号 {sheet['id']} ·" in str(m.get("content"))


# ---------- 推送 ----------

def claim(sheet, mid):
    """认下飞书上已有的那张卡片：第一次要发时就在单子上的事算随卡片到了，之后才加的还要另发提醒。"""
    for r in sheet["rows"]:
        if r["kind"] == "ask" and r["n"] in sheet["sending"]["asks"]:
            r["notified"] = True
    sheet.update(message_id=mid, sending=None)
    save(sheet)


def first_send(sheet, now, content, rec):
    """发卡片。发之前先把「要发」落盘；上次发了却没拿到消息号的，先去飞书对账，不盲目重发。认领到旧卡返回 False。"""
    if sheet["sending"]:
        found = [m["message_id"] for m in chat_messages(sheet["chat_id"], before(sheet["sending"]["first"])) if is_card(sheet, m)]
        if len(found) > 1:
            raise Fail(f"飞书上有 {len(found)} 张这张单子的卡片（{'、'.join(found)}），不知道认哪张：用 adopt --message-id 指定一张")
        if found:
            claim(sheet, found[0])
            rec["adopted"] = found[0]
            return False
        wait = QUIET - (now - at(sheet["sending"]["last"])).total_seconds()
        if wait > 0:
            raise Fail(f"上次发卡片结果不明，飞书上现在没找到；{int(wait) + 1} 秒后再 push（到时还找不到才重发）")
    chat = private_chat()[1]
    if sheet["sending"] and chat != sheet["chat_id"]:      # 上次可能已经发到原来那个会话了，换个会话再发就是两张
        raise Fail("本机配置的私聊号和上次发卡片时不一样了，不重发；先把配置改回去，或人工核对后用 adopt")
    sending = sheet["sending"] or {"first": iso(now), "asks": [r["n"] for r in sheet["rows"] if is_open(r)], "tries": 0}
    sheet.update(chat_id=chat, sending=dict(sending, last=iso(now), tries=sending["tries"] + 1))
    save(sheet)
    mid = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "interactive", "--content", content,
                "--idempotency-key", f"away-sheet-{sheet['id']}"], "发托管单").get("message_id")
    if not mid:
        raise Fail("发托管单没拿到消息号")
    for r in sheet["rows"]:
        if is_open(r):
            r["notified"] = True
    sheet.update(message_id=mid, sending=None)
    save(sheet)
    return True


def remind(sheet, now, rec, problems):
    """原地更新不弹通知，新冒出要他做的事（哪怕是结束时才冒出的）另发一句。按批记账：先记再发，结果不明的下次先对账。"""
    fresh = [r for r in sheet["rows"] if is_open(r) and not r["notified"]]
    if fresh:
        names = "；".join(f"{num(r['n'])} {r['task']}｜{r['what']}" for r in fresh)
        sheet["reminders"].append({"batch": len(sheet["reminders"]) + 1, "asks": [r["n"] for r in fresh], "at": iso(now),
                                   "text": f"托管单多了 {len(fresh)} 件要你做的：{names}。细节和怎么回，看上面引用的那张单子。",
                                   "tried": None, "message_id": None})
        for r in fresh:
            r["notified"] = True
        save(sheet)
    todo = [b for b in sheet["reminders"] if not b.get("message_id") and not b.get("dropped")]
    if not todo:
        return
    how = []
    unsure = [b for b in todo if b["tried"]]
    listed = chat_messages(sheet["chat_id"], before(unsure[0]["at"])) if unsure else []
    for b in todo:
        if b["tried"]:
            hit = [m for m in listed if from_bot(m) and m.get("reply_to") == sheet["message_id"]
                   and str(m.get("content")).strip() == b["text"]]
            if hit:                                                  # 上次其实发出去了
                b["message_id"] = hit[0]["message_id"]
                how.append("found")
                continue
            if not any(is_open(find_ask(sheet, n)) for n in b["asks"]):
                b["dropped"] = True                                  # 这一批的事都办完了，不用再提醒
                how.append("dropped")
                continue
            wait = QUIET - (now - at(b["tried"])).total_seconds()
            if wait > 0:
                how.append("unknown")
                problems.append(f"第 {b['batch']} 批提醒上次结果不明，飞书上现在没找到；{int(wait) + 1} 秒后再 push")
                continue
        b["tried"] = iso(now)
        save(sheet)
        try:
            b["message_id"] = lark(["im", "+messages-reply", "--message-id", sheet["message_id"], "--text", b["text"],
                                    "--idempotency-key", f"away-{sheet['id']}-b{b['batch']}"], "发提醒").get("message_id")
            why = "没拿到消息号"
        except Fail as e:
            why = str(e)
        how.append("sent" if b["message_id"] else "unknown")
        if not b["message_id"]:
            problems.append(f"第 {b['batch']} 批提醒结果不明（{why}），下次 push 先对账")
    save(sheet)
    rec["remind"] = next(x for x in ("unknown", "sent", "found", "dropped") if x in how)   # 几批结果不一样时记最要紧的
    rec["reminded"] = sorted(n for b in todo if b.get("message_id") for n in b["asks"])


def push(sheet, now):
    """发卡片或原地更新，回读；新冒出要他做的事另发一句提醒。返回记进 runs.jsonl 的一条（卡片、提醒、回读分开记）。"""
    sheet["rev"] += 1
    save(sheet)
    rec = {"at": iso(now), "rev": sheet["rev"], "card": "failed", "remind": "none", "readback": "skipped", "result": "failed"}
    problems = []
    try:
        card = build_card(sheet, now)
        content = json.dumps(card, ensure_ascii=False)
        if not sheet["message_id"] and first_send(sheet, now, content, rec):
            rec["card"] = "sent"
        else:
            lark(["im", "messages", "patch", "--message-id", sheet["message_id"],
                  "--data", json.dumps({"content": content}, ensure_ascii=False)], "更新托管单")
            rec["card"] = "patched"
            remind(sheet, now, rec, problems)
        sheet["pushed_at"] = iso(now)
        rec.update(message_id=sheet["message_id"], tags=tags(card["body"]))
        save(sheet)                        # 「飞书上已是最新」要等回读对上才记（dirty 留着）
        write_json(base() / sheet["id"] / "card.json", card)
        try:
            seen = one_message(sheet["message_id"], "回读")
            ok = seen.get("msg_type") == "interactive" and all(
                x in str(seen.get("content")) for x in (card["header"]["title"]["content"], mark(sheet)))
            rec["readback"] = "ok" if ok else "mismatch"
        except Fail as e:
            rec["readback"] = "failed"
            problems.insert(0, str(e))
        if rec["readback"] == "mismatch":
            problems.insert(0, "回读对不上卡片内容")
        if rec["readback"] == "ok":        # 回读对上这一版才算推到了：没对上就保持「没确认」，不让开下一张，下次 push 重推重核
            sheet["dirty"] = False
            save(sheet)
        if problems:
            rec["result"] = "partial"
            raise Fail(f"托管单已{'发' if rec['card'] == 'sent' else '更新'}（{sheet['message_id']}），但" + "；".join(problems))
        rec["result"] = "ok"
        return rec
    except Fail as e:
        if sheet["sending"]:               # 要发的记下了，却没拿到消息号：发没发出去不知道
            rec["card"] = "unknown"
        rec["error"] = str(e)
        raise
    finally:
        log(sheet, rec)


def adopt(sheet, mid, now):
    """人工认领：发了却没拿到消息号，飞书上又不止一张这张单子的卡片时，指定认哪一张。"""
    if sheet["message_id"]:
        raise Fail(f"这张单子已经有消息号了（{sheet['message_id']}）")
    if not sheet["sending"]:
        raise Fail("这张单子还没发过，不用认领，直接 push")
    chat = private_chat()[1]
    m = one_message(mid, "读这条消息")
    if m.get("chat_id") != chat or chat != sheet["chat_id"] or not is_card(sheet, m):
        raise Fail("这条消息不是这张单子的卡片（要是机器人发在常浩私聊里的卡片，页脚带这张单子的单号）")
    claim(sheet, mid)
    sheet["dirty"] = True
    save(sheet)
    log(sheet, {"at": iso(now), "adopt": mid})


# ---------- 回话 ----------

def version(m):
    """一条消息现在这一版的指纹：改过、撤回过都会变。"""
    raw = json.dumps([bool(m.get("deleted")), m.get("update_time"), m.get("msg_type"), m.get("content")], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def reads(sheet):
    """读回话前的检查：单子发过，而且就是发在常浩的单聊里。返回他的 open_id、私聊号、这张单子在飞书上的消息号（卡片和提醒）。"""
    if not sheet["message_id"]:
        raise Fail("这张单子还没发")
    who, chat = private_chat()
    if sheet["chat_id"] != chat:
        raise Fail("这张单子不是发在常浩私聊里的，不读回复")
    return who, chat, {sheet["message_id"]} | {b["message_id"] for b in sheet["reminders"] if b.get("message_id")}


def his(m, who, mine):
    """常浩本人引用这张单子（或它的提醒）发的：发送人是用户、ID 类型是 open_id、ID 对得上，引用对象对得上。"""
    s = m.get("sender") or {}
    return (s.get("sender_type"), s.get("id_type"), s.get("id")) == ("user", "open_id", who) and m.get("reply_to") in mine


def state(m, ack):
    if ack:
        if ack["version"] == version(m):
            return "acked"
        return "recalled_after_ack" if m.get("deleted") else "edited_after_ack"
    if m.get("deleted"):
        return "recalled"
    if m.get("msg_type") not in TEXT_TYPES:
        return "other_type"
    return "edited" if m.get("updated") else "pending"


def replies(sheet):
    """常浩引用这张单子（或它下面的提醒）回的话，旧的在前，每句带现在的状态和版本。只读，不记账。
    字段以 2026-11-22 实测的 lark-cli 输出为准：sender.{sender_type,id_type,id}、reply_to、msg_type、deleted、updated、update_time。"""
    who, chat, mine = reads(sheet)
    acks, out = sheet["acks"], []
    for m in chat_messages(chat, sheet["started_at"]):
        mid = m.get("message_id")
        if not his(m, who, mine) and mid not in acks:        # 办过的那句即使撤回后变了样子，也要报出来
            continue
        st = state(m, acks.get(mid))
        shown = m.get("msg_type") in TEXT_TYPES and not m.get("deleted")
        out.append({"message_id": mid, "at": m.get("create_time"), "state": st, "version": version(m),
                    "msg_type": m.get("msg_type"), "text": m.get("content") if shown else None, "ack": acks.get(mid)})
    for mid in sorted(set(acks) - {m["message_id"] for m in out}):
        gone = "acked" if acks[mid]["version"] == "missing" else "missing_after_ack"       # 读不到这件事本身也 ack 过了
        out.append({"message_id": mid, "at": None, "state": gone, "version": "missing",
                    "msg_type": None, "text": None, "ack": acks[mid]})
    return out


def ack(sheet, mid, ver, result, now):
    """这句话办完了（或处理过了）。先向飞书核实它还是 replies 给的那一版；变了就不收，重新 replies。"""
    if not (result or "").strip():
        raise Fail("要写一句怎么办的（--result）")
    who, chat, mine = reads(sheet)
    if ver == "missing":
        if mid not in sheet["acks"]:
            raise Fail("只有办过、现在读不到的那句才能这样记")
    else:
        m = one_message(mid, "读这句话")
        if m.get("chat_id") != chat or not (his(m, who, mine) or mid in sheet["acks"]):
            raise Fail("这条不是常浩引用这张单子回的话")
        if version(m) != ver:
            raise Fail("这句话在你读过之后变了（改过或撤回了），重新跑 replies 看它现在的状态")
    sheet["acks"][mid] = {"version": ver, "at": iso(now), "result": result.strip()}
    save(sheet)
    log(sheet, {"at": iso(now), "ack": mid, "version": ver, "result": result.strip()})


def show(sheet):
    out = [f"托管单 {sheet['id']}｜{sheet['by']}｜" + ("已结束" if sheet.get("ended_at") else "进行中")
           + ("｜还没发" if not sheet.get("message_id") else f"｜上次推送 {sheet['pushed_at']}")
           + ("｜有改动还没推" if sheet.get("dirty") and sheet.get("message_id") else "")
           + ("｜上次发卡片结果不明，先 push 对账" if sheet.get("sending") else "")
           + ("｜有提醒还没确认发到，再 push 一次" if any(
               not b.get("message_id") and not b.get("dropped") for b in sheet["reminders"]) else "")]
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
    s = sub.add_parser("adopt")
    s.add_argument("--message-id", required=True)
    s = sub.add_parser("ack")
    s.add_argument("message_id")
    s.add_argument("--version", required=True, help="replies 给的 version")
    s.add_argument("--result", required=True)
    for name in ("build", "push", "replies", "show"):
        sub.add_parser(name)
    a = ap.parse_args(argv)
    now = current()
    try:
        if a.cmd in ("build", "show", "replies"):          # 只读，不用等锁
            sheet = load(a.sheet)
            if a.cmd == "show":
                print(show(sheet))
            else:
                out = replies(sheet) if a.cmd == "replies" else build_card(dict(sheet, rev=sheet["rev"] + 1), now)
                print(json.dumps(out, ensure_ascii=False, indent=1))
            return 0
        with locked():
            if a.cmd == "start":
                print(start(a.by, now, a.at)["id"])
                return 0
            sheet = load(a.sheet)
            if a.cmd == "push":
                print(json.dumps(push(sheet, now), ensure_ascii=False))
                return 0
            if a.cmd == "adopt":
                adopt(sheet, a.message_id, now)
                return 0
            if a.cmd == "ack":
                ack(sheet, a.message_id, a.version, a.result, now)
                return 0
            if sheet.get("ended_at"):
                raise Fail(f"托管单 {sheet['id']} 已经结束，不能再改")
            if a.cmd == "ask":
                print(add_ask(sheet, {k: getattr(a, k) for k in ASK_FIELDS}, when(a.at, now)))
            elif a.cmd == "update":
                update_ask(sheet, a.n, {k: getattr(a, k) for k in ASK_FIELDS[1:]})
            elif a.cmd == "resolve":
                resolve(sheet, a.n, a.result, when(a.at, now))
            elif a.cmd == "done":
                warn_inner([a.project])
                add_row(sheet, "done", when(a.at, now), project=a.project, text=a.text)
            elif a.cmd == "note":
                add_row(sheet, "note", when(a.at, now), text=a.text)
            elif a.cmd == "doing":
                set_doing(sheet, a.item)
            elif a.cmd == "end":
                sheet["ended_at"] = when(a.at, now)
            try:
                build_card(sheet, now)                     # 当场看装不装得下，装不下就不收这一条
            except Fail as e:
                raise Fail(f"{e}。这一条没记进去：先 end 这一张、push 一次，再 start 新的一张，把还没办完的事加到新单子上")
            sheet["dirty"] = True
            save(sheet)
            return 0
    except Fail as e:
        print(f"托管单没办成：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
