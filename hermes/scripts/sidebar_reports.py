"""销售 AI 侧边栏的两份固定汇报（亚古兽，常浩 2026-11-19 定）：版式由本脚本固定，亚古兽只写内容 JSON。

  feedback   需求反馈：每天 18:00 私发常浩确认，常浩回「发群」后用他本人身份发到测试反馈群（版式 11-13 定稿、11-17 补空行）
  progress   项目推进进度：每周三 18:30 私发常浩（版式 11-18 定稿卡片）

用法（亚古兽在终端里跑）：
  python3 sidebar_reports.py feedback status                 上一期草稿、取数起点、上期待解决 / 待上线清单（上一期以群里常浩发的版本为准）
  python3 sidebar_reports.py feedback render  草稿.json      只核对并打印飞书 post，不发
  python3 sidebar_reports.py feedback send    草稿.json      核对 → 私发常浩（同一天已发过就原地改）→ 读回 → 存档
  python3 sidebar_reports.py feedback group   [--date D]     常浩确认后发群：核对 @ 的人都在群里 → 本人身份发 → 读回 @
  python3 sidebar_reports.py progress status                 上一期数据、本期起止和第几周
  python3 sidebar_reports.py progress render  数据.json      只核对并打印卡片 JSON，不发
  python3 sidebar_reports.py progress send    数据.json      核对 → 私发常浩卡片（同一期已发过就原地更新）→ 读回 → 存档

核对不过退出码 2，stderr 逐条写原因；飞书调用失败退出码 1。存档在 DigitalTwin/sidebar/（feedback/日期.json、progress/截止日期.json）。
"""
import argparse
import copy
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import lark_pages  # noqa: E402  读列表翻到底，翻不完报错（#312）
import memdaily_data as md  # noqa: E402  lark-cli 调用（亚古兽应用、重试、去掉 Hermes 环境变量）共用

HOME = Path(os.environ.get("DT_SIDEBAR_HOME", md.DT_HOME / "sidebar"))
OWNER = "ou_6fac84a8164f84c0c591a9b3a98b462c"        # 常浩（亚古兽应用下的 open_id）
GROUP = "oc_32562463635b4b2ee03d1808c080d1b5"        # 销售AI侧边栏-测试反馈群
BASE = "https://vm94j8bzy7.feishu.cn/base/XTPpbsyWza4rixsRuCwcALI8nOh"
URL_ROADMAP = BASE + "?table=tblsfV4mKschNic4&view=vewsr4pDuD"   # 卡片底部按钮：完整功能排期
CONFIRM_NOTE = "上面卡片里「下个节点」「本周」「下周」「待排期」是我按推进计划表和群消息推的，待你确认；要改直接回复，我原地改这张卡片。"


class Invalid(Exception):
    """内容 JSON 没通过核对；args[0] 是问题清单。"""


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def archives(kind):
    d = HOME / kind
    return sorted(d.glob("*.json")) if d.exists() else []


def previous(kind, key):
    """严格早于 key（日期字符串）的最近一期存档，没有返回 None。"""
    old = [p for p in archives(kind) if p.stem < key]
    return load(old[-1]) if old else None


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


# ====================== 需求反馈（飞书 post） ======================
TYPES = {"fixed": "✅", "launched": "♐",   # 已上线单独一组（常浩 2026-11-19 定）
         "pending": "✴️", "planned": "⬆️"}
LABEL = {"fixed": "已修复", "launched": "已上线", "pending": "待解决", "planned": "待上线"}
OPEN_TYPES = {"pending", "planned"}
ZWSP = re.compile(r"(?<=[一-鿿])(?=[0-9A-Za-z])|(?<=[0-9A-Za-z%])(?=[一-鿿])")


def fb_check(draft, prev=None):
    """问题清单（空 = 通过）。prev 是上一期存档：上期 ✴️ / ⬆️ 每期必须复核带上（11-18 常浩）。"""
    errs = []
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(draft.get("date", ""))):
        errs.append("date 要写成 YYYY-MM-DD")
    if not draft.get("cutoff"):
        errs.append("cutoff 要写本期取数截止时间（YYYY-MM-DD HH:MM），下一期从这里接着读")
    items = draft.get("items") or []
    if not items:
        errs.append("items 为空：群里没有新反馈时不出这一期，直接回复常浩一句「今天群里没有新反馈，本期不出」")
    order = [i.get("type") for i in items]
    if order != sorted(order, key=lambda t: list(TYPES).index(t) if t in TYPES else 9):
        errs.append("类型顺序要按 已修复 → 已上线 → 待解决 → 待上线 排")
    for n, it in enumerate(items, 1):
        t = it.get("type")
        if t not in TYPES:
            errs.append(f"第 {n} 项 type 只能是 fixed / launched / pending / planned")
            continue
        if t == "fixed" and it.get("title"):
            errs.append(f"第 {n} 项是已修复，类型行固定写「已修复」，不要写 title")
        if t != "fixed" and not str(it.get("title", "")).strip():
            errs.append(f"第 {n} 项是{LABEL[t]}，要写 title（问题或需求概括）")
        qs = it.get("quotes") or []
        if not qs:
            errs.append(f"第 {n} 项没有原话")
        for q in qs:
            if not str(q.get("text", "")).strip():
                errs.append(f"第 {n} 项有一条原话是空的")
            if not re.fullmatch(r"ou_[0-9a-z]+", str(q.get("open_id", ""))):
                errs.append(f"第 {n} 项「{q.get('name', '?')}」的 open_id 不对（要群消息里发送人的 ou_ 开头 ID）")
    if prev and not errs:
        errs += carry_check(items, prev.get("items") or [])
    return errs


def texts(it):
    return [q.get("text") for q in it.get("quotes") or []]


def carry_check(items, old):
    """上期未完成项原样带过；已确认修复保留原话，已确认上线保留原标题和原话。"""
    errs = []
    by_title = {(i["type"], i.get("title")): i for i in items}
    fixed_quotes = [texts(i) for i in items if i["type"] == "fixed"]
    for o in old:
        if o["type"] not in OPEN_TYPES:
            continue
        cur = by_title.get((o["type"], o.get("title")))
        if cur is None and o["type"] == "planned":
            cur = by_title.get(("launched", o.get("title")))
        if cur:
            if not set(texts(o)) <= set(texts(cur)):
                errs.append(f"上期{LABEL[o['type']]}「{o['title']}」带过来时原话要照旧，不能删改")
            continue
        if o["type"] == "pending" and any(set(texts(o)) <= set(q) for q in fixed_quotes):
            continue   # 群里确认修好了，改列已修复
        if o["type"] == "pending":
            errs.append(f"上期待解决「{o['title']}」本期没带上：群里没确认修复就原标题、原话照旧继续列待解决；确认修复了就改列已修复，原话照旧")
        else:
            errs.append(f"上期待上线「{o['title']}」本期没带上：未确认上线就原标题、原话照旧继续列待上线；确认上线就改列已上线，原标题、原话照旧")
    # 同类型内「旧的在前、按提出时间」不在这里卡：11-18 那期补带了 11-13 漏掉的旧待上线，按提出时间排在上期带来的前面
    return errs


def fb_render(draft):
    """飞书 post 内容（zh_cn）。逐行版式同 2026-11-17、11-18 两期定稿。"""
    def head(emoji, text):
        return [{"tag": "text", "text": emoji + " ", "style": []}, {"tag": "text", "text": text, "style": ["bold"]}]

    def quotes(qs):
        # 原话里的换行折成空格：引用块每行都要以「> 」开头，换行会让后半句掉出引用（11-19 试跑发现）
        txt = "\n".join(f'> “{" ".join(str(q["text"]).split())}”<at user_id="{q["open_id"]}"></at>' for q in qs)
        return [{"tag": "md", "text": ZWSP.sub("​", txt)}]

    blank = [{"tag": "text", "text": ""}]
    rows = [[{"tag": "text", "text": "📅", "style": []},
             {"tag": "text", "text": f"{draft['date']} 需求反馈", "style": ["underline", "bold"]}], blank]
    last = None
    for it in draft["items"]:
        t = it["type"]
        if last and t != last:
            rows.append(blank)
        rows.append(head(TYPES[t], LABEL[t] if t == "fixed" else f"{LABEL[t]}：{it['title']}"))
        rows.append(quotes(it["quotes"]))
        last = t
    return {"zh_cn": {"title": "", "content": rows}}


HEAD_RE = re.compile(r"^(✅|♐|✴|⬆)\s*\*\*(.+?)\*\*\s*$")
QUOTE_RE = re.compile(r"^(?:>\s*)?“(.*)”@(.+?)\s*$")
TYPE_OF = {"✅": "fixed", "♐": "launched", "✴": "pending", "⬆": "planned"}


def parse_group_post(msg):
    """把群里那条《需求反馈》（消息接口给的 markdown 正文 + mentions）解析回 items。
    原话里的 @ 按出现顺序对 mentions，名字对不上、条数对不上都算读不懂，不猜。"""
    ments = list(msg.get("mentions") or [])
    items = []
    for raw in str(msg.get("content", "")).splitlines():
        line = raw.replace("\u200b", "").replace("\ufe0f", "").strip()
        h = HEAD_RE.match(line)
        if h:
            t = TYPE_OF[h.group(1)]
            label, _, title = h.group(2).partition("：")
            if label != LABEL[t] or (t != "fixed") != bool(title.strip()):
                raise md.Fail(f"群里那期的类型行读不懂：{line[:60]}")
            items.append({"type": t, "quotes": []} if t == "fixed" else {"type": t, "title": title.strip(), "quotes": []})
            continue
        q = QUOTE_RE.match(line)
        if q:
            m = ments.pop(0) if ments else {}
            if not items or m.get("name") != q.group(2):
                raise md.Fail(f"群里那期的原话和 @ 对不上：{line[:60]}")
            items[-1]["quotes"].append({"text": q.group(1), "name": m["name"], "open_id": m["id"]})
    if not items or ments:
        raise md.Fail(f"群里那期（{msg.get('message_id')}）读不出条目，或 @ 比原话多，先人工核对")
    return items


def group_post(prev, run):
    """群里常浩发的那期《需求反馈》（同一天发了几次取最后一条），没有返回 None。读群失败直接报错，不退回私聊存档。"""
    title = f"{prev['date']} 需求反馈"
    msgs = md.listing(run, "读群里上一期", "im", "+chat-messages-list", "--chat-id", GROUP, "--start", f"{prev['date']}T00:00:00+08:00",
                      "--order", "asc", "--page-all", "--no-reactions", identity="user")   # 翻到底，翻不完报错（#312）
    hits = [m for m in msgs
            if (m.get("sender") or {}).get("id") == OWNER and m.get("msg_type") == "post" and not m.get("deleted")
            and title in (str(m.get("content", "")).splitlines() or [""])[0]]
    return max(hits, key=lambda m: str(m.get("create_time", ""))) if hits else None


def fb_previous(key, run=md.lark):
    """上一期（严格早于 key 的最近一期），以群里实际发出的版本为准（2026-11-20 常浩定：他会自己改了再发群，
    11-19 那期删了 ✴️ 微信支付截图、加了两条 ♐，只看私聊存档就带错了）。找到群里版本就写回存档：
    items 换成群里那版，私聊原版留在 dm_items。返回 (上一期, 依据说明)；没有上一期返回 (None, None)。"""
    old = [p for p in archives("feedback") if p.stem < key]
    if not old:
        return None, None
    prev = load(old[-1])
    msg = group_post(prev, run)
    if not msg:
        return prev, "私聊存档（群里没找到常浩发的这期）"
    items = parse_group_post(msg)
    prev.setdefault("dm_items", prev.get("items") or [])
    prev.update(items=items, group_message_id=msg["message_id"],
                group_sent_at=prev.get("group_sent_at") or msg.get("create_time"))
    save(old[-1], prev)
    return prev, f"群里常浩发的版本（{msg['message_id']}，{msg.get('create_time')}）"


def fb_status(today=None, run=md.lark):
    """上一期（早于今天的最近一期，以群里发出的版本为准）。今天已出过（试跑或常浩让改过）时，仍从上一期截止时间读起、
    对照上一期复核，发送时原地改今天那条，免得漏掉今天早些时候的反馈。"""
    today = (today or dt.date.today()).isoformat()
    last, source = fb_previous(today, run)
    cur = HOME / "feedback" / f"{today}.json"
    out = {"today": today, "today_sent": load(cur).get("dm_message_id") if cur.exists() else None}
    if not last:
        return {**out, "previous": None, "read_from": None, "note": "还没有更早的存档，取数起点问常浩"}
    return {**out, "previous": last.get("date"), "read_from": last.get("cutoff"),
            "previous_group_sent": bool(last.get("group_message_id")), "previous_source": source,
            "carry": [{"type": i["type"], "title": i.get("title"), "quotes": i.get("quotes")}
                      for i in last.get("items", []) if i["type"] in OPEN_TYPES]}


def fb_send(path, run=md.lark):
    draft = load(path)
    prev, _ = fb_previous(draft.get("date", ""), run)
    errs = fb_check(draft, prev)
    if errs:
        raise Invalid(errs)
    arch = HOME / "feedback" / f"{draft['date']}.json"
    old = load(arch) if arch.exists() else {}
    if old.get("group_message_id"):
        raise Invalid([f"{draft['date']} 这期已经发到群里（{old['group_message_id']}），用户身份消息不能改，不再动"])
    content = json.dumps(fb_render(draft), ensure_ascii=False)
    mid = old.get("dm_message_id")
    if mid:
        run("im", "+messages-edit", "--message-id", mid, "--msg-type", "post", "--content", content, identity="bot", tries=1)
    else:
        mid = run("im", "+messages-send", "--user-id", OWNER, "--msg-type", "post", "--content", content,
                  identity="bot", tries=1)["message_id"]
    back = readback(mid, run)
    if f"{draft['date']} 需求反馈" not in back:
        raise md.Fail(f"读回 {mid} 没看到标题，去飞书私聊里看一眼")
    save(arch, {**draft, "dm_message_id": mid, "sent_at": now()})
    return {"message_id": mid, "edited": bool(old.get("dm_message_id")), "archive": str(arch)}


def fb_group(date=None, run=md.lark):
    """常浩在私聊里确认后才调：用他本人身份发群，只发一次。"""
    p = HOME / "feedback" / f"{date}.json" if date else (archives("feedback") or [None])[-1]
    if not p or not p.exists():
        raise Invalid([f"没有 {date or '任何'} 的需求反馈存档"])
    draft = load(p)
    if not draft.get("dm_message_id"):
        raise Invalid([f"{draft['date']} 还没私发常浩确认过，不能发群"])
    if draft.get("group_message_id"):
        raise Invalid([f"{draft['date']} 已经发过群（{draft['group_message_id']}），不重复发"])
    members = lark_pages.read_all(lambda extra: run("im", "+chat-members-list", "--chat-id", GROUP, "--page-all", *extra),
                                  "读群成员", md.Fail, ("users", "truncations"))   # 翻到底，翻不完报错（#312）
    if members["truncations"]:
        raise md.Fail("读群成员：飞书按安全设置截断了名单，没读全，不拿半截名单判断谁不在群里")
    ids = {u.get("member_id") or u.get("open_id") for u in members["users"]}
    at = {q["open_id"]: q.get("name", "?") for it in draft["items"] for q in it["quotes"]}
    missing = [f"{n}（{i}）" for i, n in at.items() if i not in ids]
    if missing:
        raise Invalid([f"这些被 @ 的人不在群里，先问常浩怎么处理：{'、'.join(missing)}"])
    content = json.dumps(fb_render(draft), ensure_ascii=False)
    mid = run("im", "+messages-send", "--chat-id", GROUP, "--msg-type", "post", "--content", content,
              identity="user", tries=1)["message_id"]
    draft.update(group_message_id=mid, group_sent_at=now())
    save(p, draft)
    msgs = (run("im", "+messages-mget", "--message-ids", mid, identity="user") or {}).get("messages") or [{}]
    got = {m.get("id") for m in msgs[0].get("mentions") or []}
    lost = [n for i, n in at.items() if i not in got]
    return {"message_id": mid, "mentions_ok": not lost, "mentions_missing": lost}


def readback(mid, run):
    msgs = (run("im", "+messages-mget", "--message-ids", mid, identity="bot") or {}).get("messages") or []
    return msgs[0].get("content", "") if msgs else ""


# ====================== 项目推进进度（飞书卡片 2.0） ======================
# 版式逐项沿用 2026-11-18 定稿（DigitalTwin/evidence/progress-card-20261118/build.py 的 card_final）：
# 三张等宽指标卡（蓝色数字）→「已交付」标签列表 →「接下来」本周（青）/ 下周（橙字黄底）两块并排 →「待排期」→ 按钮。
def md_(content, **kw):
    d = {"tag": "markdown", "content": content}
    d.update(kw)
    return d


def box(elements, bg="grey-50", margin="0px", spacing="4px", padding="12px 12px 12px 12px", align="left", height=None):
    d = {"tag": "interactive_container", "width": "fill", "background_style": bg, "corner_radius": "8px",
         "padding": padding, "vertical_spacing": spacing, "margin": margin, "horizontal_align": align, "elements": elements}
    if height:
        d.update(height=height, vertical_align="top")
    return d


def g(s):
    return f"<font color='grey'>{s}</font>"


def metric(num, label, sub):
    els = [md_(f"**<font color='blue'>{num}</font>**", text_align="center", text_size="heading-0"),
           md_(f"**{label}**", text_align="center", text_size="notation"),
           md_(g(sub), text_align="center", text_size="notation")]
    return {"tag": "column", "width": "weighted", "weight": 1,
            "elements": [box(els, spacing="2px", padding="12px 4px 12px 4px", align="center")]}


def row(tag_name, color, text, when):
    return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px", "columns": [
        {"tag": "column", "width": "60px", "elements": [md_(f"<text_tag color='{color}'>{tag_name}</text_tag>")]},
        {"tag": "column", "width": "weighted", "weight": 1, "elements": [md_(text)]},
        {"tag": "column", "width": "44px", "elements": [md_(g(when), text_size="notation", text_align="right")]}]}


def numbered(items):
    return md_("\n".join(f"{i}. **{a}**：{b}" for i, (a, b) in enumerate(items, 1)))


def week(title, sub, items, color, bg):
    return {"tag": "column", "width": "weighted", "weight": 1, "elements": [box(
        [md_(f"**<font color='{color}'>{title}</font>**"), md_(g(sub), text_size="notation"), numbered(items)], bg=bg)]}


def pg_check(d):
    errs = []
    p = d.get("period") or {}
    for k in ("start", "end"):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(p.get(k, ""))):
            errs.append(f"period.{k} 要写成 YYYY-MM-DD")
    if not str(p.get("stage", "")).strip():
        errs.append("period.stage 要写阶段，如「全员试用第 2 周」")
    dl = d.get("delivered") or {}
    for k, n in (("new", 3), ("opt", 3), ("bug", 2)):
        for x in dl.get(k) or []:
            if not (isinstance(x, list) and len(x) == n and all(str(v).strip() for v in x)):
                errs.append(f"delivered.{k} 每项要是 {n} 个非空字段：" + ("[功能, 说明, 日期或待验证]" if n == 3 else "[问题, 日期]"))
                break
    if not any(dl.get(k) for k in ("new", "opt", "bug")):
        errs.append("delivered 三类都空：本期没有交付也要如实写，先问常浩这一期还发不发")
    fb = d.get("feedback") or {}
    if not (isinstance(fb.get("done"), int) and isinstance(fb.get("total"), int) and 0 <= fb["done"] <= fb["total"] and fb["total"] > 0):
        errs.append("feedback 要写整数 done / total（使用反馈表已闭环数 / 总数）")
    nn = d.get("next_node") or {}
    if not (str(nn.get("date", "")).strip() and str(nn.get("label", "")).strip()):
        errs.append("next_node 要写 date 和 label（如 11-20、今日待办上线）")
    tw, nw = d.get("this_week") or {}, d.get("next_week") or {}
    for name, w in (("this_week", tw), ("next_week", nw)):
        if not str(w.get("sub", "")).strip() or not w.get("items"):
            errs.append(f"{name} 要有 sub（一句话重点）和 items")
        for x in w.get("items") or []:
            if not (isinstance(x, list) and len(x) == 2 and all(str(v).strip() for v in x)):
                errs.append(f"{name}.items 每条写成 [事项, 做到什么]（11-18 常浩：表述严谨清晰）")
                break
    if tw.get("items") and nw.get("items") and len(tw["items"]) != len(nw["items"]):
        errs.append(f"本周 {len(tw['items'])} 条、下周 {len(nw['items'])} 条：两块条数要相同，电脑端并排才等高（11-18 定稿）")
    for x in d.get("backlog") or []:
        if not (isinstance(x, list) and len(x) == 2 and all(str(v).strip() for v in x)):
            errs.append("backlog 每条写成 [事项, 做到什么]")
            break
    if not str(d.get("summary", "")).strip():
        errs.append("summary 要写一句消息预览，如「本周今日待办上线，下周用户路径和班主任」")
    return errs


def pg_render(d):
    dl = d["delivered"]
    news, opts, bugs = dl.get("new") or [], dl.get("opt") or [], dl.get("bug") or []
    total = len(news) + len(opts) + len(bugs)
    fb = d["feedback"]
    rate = round(fb["done"] * 100 / fb["total"])
    end = d["period"]["end"]
    els = [{"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px", "margin": "0px 0px 12px 0px", "columns": [
        metric(f"{total} 项", "已交付", f"截至 {end[5:]}"),
        metric(f"{rate}%", "反馈闭环", f"{fb['done']}/{fb['total']} 项"),
        metric(d["next_node"]["date"], "下个节点", d["next_node"]["label"])]}]
    els.append(md_(f"**已交付** {g(f'功能上新 {len(news)} · 体验优化 {len(opts)} · Bug 修复 {len(bugs)}')}", margin="0px 0px 8px 0px"))
    rows = [row("功能上新", "blue", f"**{a}**：{b}", c) for a, b, c in news]
    rows += [row("体验优化", "turquoise", f"**{a}**：{b}", c) for a, b, c in opts]
    rows += [row("Bug 修复", "neutral", a, c) for a, c in bugs]
    if d.get("usage"):
        rows.append(md_(g(f"使用数据：{d['usage']}。"), text_size="notation", margin="4px 0px 0px 0px"))
    els.append(box(rows, margin="0px 0px 12px 0px"))
    tw, nw = d["this_week"], d["next_week"]
    nxt = f"本周 {len(tw['items'])} 项 · 下周 {len(nw['items'])} 项"
    els.append(md_(f"**接下来** {g(nxt)}", margin="0px 0px 8px 0px"))
    els.append({"tag": "column_set", "flex_mode": "stretch", "horizontal_spacing": "8px", "margin": "0px 0px 12px 0px",
                "columns": [week("本周", tw["sub"], tw["items"], "turquoise", "turquoise-50"),
                            week("下周", nw["sub"], nw["items"], "orange", "yellow-50")]})
    if d.get("backlog"):
        bl = f"{len(d['backlog'])} 项 · 时间待定"
        els.append(md_(f"**待排期** {g(bl)}", margin="0px 0px 8px 0px"))
        els.append(box([numbered(d["backlog"])], margin="0px 0px 12px 0px"))
    els.append({"tag": "button", "text": {"tag": "plain_text", "content": "查看完整功能排期"}, "type": "primary_filled",
                "width": "fill", "behaviors": [{"type": "open_url", "default_url": URL_ROADMAP}]})
    start = d["period"]["start"]
    return {"schema": "2.0",
            "config": {"update_multi": True, "width_mode": "default",
                       "summary": {"content": f"侧边栏项目进度｜已交付 {total} 项，{d['summary']}"}},
            "header": {"title": {"tag": "plain_text", "content": "销售 AI 侧边栏｜项目推进进度"},
                       "subtitle": {"tag": "plain_text", "content": f"{start} 至 {end[5:]} · {d['period']['stage']}"},
                       "template": "blue",
                       "icon": {"tag": "standard_icon", "token": "wiki-bitable_colorful"}},
            "body": {"direction": "vertical", "padding": "12px 12px 20px 12px", "vertical_spacing": "0px", "elements": els}}


def pg_status(today=None):
    today = today or dt.date.today()
    p = archives("progress")
    if not p:
        return {"previous": None, "note": "还没有存档，本期起止问常浩"}
    last = load(p[-1])
    end = dt.date.fromisoformat(last["period"]["end"])
    m = re.search(r"第\s*(\d+)\s*周", last["period"].get("stage", ""))
    return {"previous": str(p[-1]), "start": (end + dt.timedelta(days=1)).isoformat(), "end": today.isoformat(),
            "stage": re.sub(r"第\s*\d+\s*周", f"第 {int(m.group(1)) + 1} 周", last["period"]["stage"]) if m else None,
            "last_this_week": last.get("this_week"), "last_next_week": last.get("next_week"), "last_backlog": last.get("backlog")}


def pg_send(path, run=md.lark):
    d = load(path)
    errs = pg_check(d)
    if errs:
        raise Invalid(errs)
    arch = HOME / "progress" / f"{d['period']['end']}.json"
    old = load(arch) if arch.exists() else {}
    content = json.dumps(pg_render(d), ensure_ascii=False)
    mid = old.get("dm_message_id")
    if mid:
        run("im", "messages", "patch", "--params", json.dumps({"message_id": mid}),
            "--data", json.dumps({"content": content}, ensure_ascii=False), identity="bot", tries=1)
    else:
        mid = run("im", "+messages-send", "--user-id", OWNER, "--msg-type", "interactive", "--content", content,
                  identity="bot", tries=1)["message_id"]
        run("im", "+messages-send", "--user-id", OWNER, "--text", CONFIRM_NOTE, identity="bot", tries=1)
    if "项目推进进度" not in readback(mid, run):
        raise md.Fail(f"读回 {mid} 没看到卡片标题，去飞书私聊里看一眼")
    save(arch, {**d, "dm_message_id": mid, "sent_at": now()})
    return {"message_id": mid, "updated": bool(old.get("dm_message_id")), "archive": str(arch)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="销售 AI 侧边栏固定汇报（需求反馈 / 项目推进进度）")
    ap.add_argument("kind", choices=["feedback", "progress"])
    ap.add_argument("action", choices=["status", "render", "send", "group"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--date")
    a = ap.parse_args(argv)
    try:
        if a.action == "status":
            out = fb_status() if a.kind == "feedback" else pg_status()
        elif a.action == "group":
            if a.kind != "feedback":
                ap.error("只有需求反馈要发群")
            out = fb_group(a.date)
        elif not a.path:
            ap.error(f"{a.action} 要给内容 JSON 的路径")
        elif a.action == "render":
            d = load(a.path)
            errs = fb_check(d, fb_previous(d.get("date", ""))[0]) if a.kind == "feedback" else pg_check(d)
            if errs:
                raise Invalid(errs)
            out = fb_render(d) if a.kind == "feedback" else pg_render(d)
        else:
            out = fb_send(a.path) if a.kind == "feedback" else pg_send(a.path)
    except Invalid as e:
        print("没通过核对，改完再跑：\n" + "\n".join(f"- {x}" for x in e.args[0]), file=sys.stderr)
        return 2
    except (md.Fail, KeyError, OSError, ValueError) as e:
        print(f"失败：{e}", file=sys.stderr)
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
