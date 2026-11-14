#!/usr/bin/env python3
"""托管汇报卡与推翻（#133，规格 §3.6；ARCHITECTURE 5.7 第 10 条）。

汇报：托管收尾（排空完成到 off、或冻结）后，按账本给常浩私聊发一张卡片：按项目分组，挂起、结果不明、UNRECONCILED 置顶，
  每条一行「子单 · 问了什么 · 替你定了什么 · 依据 · 结果 · 撤回能力」，每条带「推翻」按钮；这次托管冻结过，标题写「托管未能安全收尾」。
  发出后回读核对（消息类型是卡片、内容里有托管号）；同一次托管、同一种收尾（final / frozen）只发一次（--force 才再发）。
  发送记录在 DT_CUSTODY_DIR/<托管号>/report.json。
推翻：卡片按钮的 value 是 {"custody": "overturn", "ref": 决定号}；merge-buttons 插件的 /card 认出它，后台调
  `custody_report.py button overturn <决定号> --chat <chat_id>`。只认常浩私聊里的点击、只认发过的汇报卡上的决定；
  处置状态改成「已推翻·已记录」，写一条规则补丁候选（DT_CUSTODY_DIR/rule-candidates.jsonl），列下游影响清单（custody_impact），
  结果发到私聊、另存 DT_CUSTODY_DIR/<托管号>/<决定号>.overturn.json。合并类推翻再由合并执行器开 revert PR
  （merge_act.run_custody_revert）：开成就记「已回退」并发一张带「合并 revert PR」按钮的卡片，revert PR 的合并仍由常浩点（B 档）；
  没开成就记「回退失败」并写原因，转常浩处理。
  「合并 revert PR」按钮 {"custody": "merge_revert", "ref": 决定号}：现读 PR，仍开着、HEAD 是开出来时的、base 是 main、
  可合并（CLEAN）才用本机 gh（常浩本人）带 --match-head-commit 合并，再回读。

用法（最后一行都是一行 JSON：{"ok", "msg", ...}）：
  custody_report.py card --session c-…        只打印卡片 JSON（不发）
  custody_report.py send --session c-… [--force]
  custody_report.py button overturn|merge_revert <决定号> --chat <chat_id>
不调用 AI；不装定时任务。谁在收尾后调 send 由 #134 接（夜跑主控 / custody 技能）。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import custody as C  # noqa: E402
import custody_impact as IMP  # noqa: E402
import custody_ledger as L  # noqa: E402
from tidings_weekly import SendError, lark, send  # noqa: E402  同一套猛鬼兽机器人发送

CARD_LIMIT = 25000          # 字节：飞书卡片上限约 30KB，留余量；超了就截断，末尾写还有几条
CLIP = 60
MAX_ROWS = 20              # 一张表最多列几条（再多卡片会超长）


class ReportError(Exception):
    def __init__(self, msg, code="Rejected"):
        super().__init__(msg)
        self.code = code


def clip(text, n=CLIP):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def md_escape(text):
    return str(text).replace("<", "＜").replace(">", "＞").replace("*", "＊").replace("[", "［").replace("]", "］")


def home_chat():
    from feishu_home import home_chat as hc
    return hc()


# ---------- 托管会话 ----------
def find_session(session_id, doc=None):
    """从 custody.json 找这次托管：现在这次（冻结中）或历史里的（已收尾）。返回 (session, kind)；kind = frozen | final。
    还开着、还在排空、找不到都拒绝：汇报只在收尾之后发。"""
    try:
        doc = doc or C.load()
    except ValueError as e:
        raise ReportError(f"托管状态读不了：{e}", "Corrupt")
    cur = doc.get("session")
    if cur and cur["id"] == session_id:
        if doc["state"] == "frozen":
            return cur, "frozen"
        raise ReportError(f"托管 {session_id} 还是 {doc['state']}：排空完成或冻结之后才汇报", "NotEnded")
    for s in reversed(doc["history"]):
        if s["id"] == session_id:
            return s, "final"
    raise ReportError(f"托管状态里没有 {session_id}", "NotFound")


def report_path(session_id):
    return L.raw_dir() / session_id / "report.json"


def read_report(session_id):
    try:
        return json.loads(report_path(session_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"sent": []}
    except (OSError, ValueError) as e:
        raise ReportError(f"汇报记录读不了：{e}", "Corrupt")


def write_json(path, data):
    L._write_private(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ---------- 卡片 ----------
# 常浩 11-14：「从文字表述来我还是不清楚到底做什么」。所以卡片上每句话都要回答他的问题：在做什么事、agent 问了什么、
# 我怎么定的、为什么、后来怎样、他要做什么；不出现内部术语（custody_ledger.JARGON），编号只放第二行小字。
RUN_WORDS = {"completed": "跑完了", "running": "正在跑", "queued": "排队中", "failed": "失败了", "cancelled": "被取消了",
             "canceled": "被取消了", "in_progress": "正在跑"}
WAITING = ("suspended", "unknown", "unreconciled", "executing", "issued")      # 等常浩处理的放最上面


def short_ref(d):
    t = d["target"] or {}
    if d["action"] == "merge" or t.get("pr"):
        return f"{str(t.get('repo', '?')).split('/')[-1]} PR #{t.get('pr', '?')}"
    return f"任务 {t.get('issue') or '?'}"


def later(d, night_root=None):
    """回复之后 agent 做了什么：现读夜跑记录（custody_impact.later_runs），读不到就说读不到，不编。"""
    try:
        runs = IMP.later_runs(d, night_root)
    except IMP.Unavailable:
        return "还不知道（夜跑记录读不到）"
    if not runs:
        return "agent 还没开始处理"
    last = runs[-1]
    text = f"agent 后来又跑了 {len(runs)} 次，最近一次{RUN_WORDS.get(last.get('status'), '状态不明')}"
    prs = [r["pr_url"] for r in runs if IMP.PR_URL.match(r.get("pr_url") or "")]
    if prs:
        text += f"，开了 PR #{prs[-1].rsplit('/', 1)[-1]}"
    return text


def what_happened(d, night_root=None):
    s, o = d["status"], d["outcome"] or {}
    if d["action"] == "merge":
        return {"done": "已合进 main", "failed": "没合上，PR 还开着", "unknown": "合并时没拿到结果，还在核对合上没有",
                "unreconciled": "核对了还是不确定合上没有" + (f"（{clip(o.get('reconcile'), 40)}）" if o.get("reconcile") else ""),
                "executing": "正在合并", "issued": "还没开始合并"}.get(s, "—")
    if d["action"] == "suspend":
        return "agent 在这一步停下了，这个任务没往下做"
    return {"done": later(d, night_root), "failed": "回复没发出去，agent 没收到",
            "unknown": "回复发出时没拿到结果，还在核对 agent 收到没有",
            "unreconciled": "核对了还是不确定 agent 收到没有", "executing": "回复正在发", "issued": "回复写好了，还没发"}.get(s, "—")


def how_handled(d):
    if d["action"] == "suspend":
        return "**没替你定**"
    if d["status"] in ("unknown", "unreconciled"):
        return f"**{md_escape(d['summary'])}**，但不确定做成没有"
    if d["status"] == "failed":
        return f"想{md_escape(d['summary'])}，没做成" if d["action"] == "merge" else f"想回复「{md_escape(d['summary'])}」，没发出去"
    return f"**{md_escape(d['summary'])}**"


def owner_action(d):
    """最后一列：常浩要做什么。等他处理的写回复什么；替他定了的写不同意时点什么、再做什么。"""
    t = d["target"] or {}
    if d["disposition"] != "open":
        return f"你已处理：{L.DISPOSITION[d['disposition']]}"
    if d["action"] == "suspend":
        return md_escape(d["ask_owner"])
    if d["status"] in ("unknown", "unreconciled"):
        if d["action"] == "merge":
            return f"到 GitHub 看 PR #{t.get('pr')} 合上没有，回复我「#{t.get('pr')} 已合并」或「#{t.get('pr')} 没合并」"
        return f"到任务 {t.get('issue')} 上看 agent 收到回复没有，回复我「收到了」或「没收到」"
    if d["status"] in ("executing", "issued"):
        return "等结果，不用操作"
    if d["action"] == "merge":
        return "点「推翻」：我开一个撤销这次合并的 PR，你点合并才生效" if d["status"] == "done" else "点「推翻」：我记下以后按你的来"
    return f"点「推翻」，再到任务 {t.get('issue')} 上写你要的答案" if d["status"] == "done" else "点「推翻」：我记下以后按你的来"


def can_overturn(d):
    return d["disposition"] == "open" and d["status"] not in L.IN_FLIGHT + ("void",)


def table(rows, waiting, night_root=None):
    cols = [("what", "哪件事", "120px"), ("ask", "agent 问你什么", "auto"),
            ("did", "我怎么处理的" if waiting else "我替你定的", "auto"), ("why", "为什么", "auto"),
            ("after", "后来怎样", "auto"), ("todo", "你要做什么" if waiting else "你不同意怎么办", "auto")]

    def row(d):
        ask = d["question"] or ("（不是 agent 问的：按托管规则可以直接合）" if d["action"] == "merge" else "—")
        return {"what": f"**{md_escape(d['context'])}**\n{short_ref(d)}", "ask": md_escape(ask), "did": how_handled(d),
                "why": md_escape(d["basis"]), "after": md_escape(what_happened(d, night_root)), "todo": owner_action(d)}
    # 飞书表格：单元格用 lark_md；自定义列宽只收 80–600px（11-14 试发 70px 被拒）；超过 10 行表格自己分页
    return {"tag": "table", "page_size": 10, "row_height": "auto", "freeze_first_column": True,
            "header_style": {"text_align": "left", "background_style": "grey", "bold": True, "lines": 1},
            "columns": [{"name": n, "display_name": t, "data_type": "lark_md", "width": w} for n, t, w in cols],
            "rows": [row(d) for d in rows]}


def overturn_buttons(rows):
    """飞书表格里放不了按钮：表下一排「推翻：我替你定的」，自动换行。没有可推翻的就不放。"""
    btns = [{"tag": "column", "width": "auto", "elements": [
        {"tag": "button", "text": {"tag": "plain_text", "content": f"推翻：{clip(d['summary'] or d['context'], 14)}"},
         "type": "danger", "size": "small", "behaviors": [{"type": "callback", "value": {"custody": "overturn", "ref": d["id"]}}]}]}
        for d in rows if can_overturn(d)]
    return [{"tag": "column_set", "flex_mode": "flow", "horizontal_spacing": "8px", "columns": btns}] if btns else []


def section(title, rows, waiting, night_root=None):
    """一块：标题 + 表格 + 推翻按钮；超过 MAX_ROWS 条只列前面的，写明还有几条没列（按钮只给列出来的）。"""
    shown, more = rows[:MAX_ROWS], len(rows) - MAX_ROWS
    els = [{"tag": "markdown", "content": f"**{title} · {len(rows)} 件**"}, table(shown, waiting, night_root),
           *overturn_buttons(shown)]
    if more > 0:
        els.append({"tag": "markdown", "content": f"<font color='grey'>还有 {more} 件没列（卡片放不下），在电脑上用 custody_ledger.py list 看全部</font>"})
    return els


def build_card(session, kind, rows, night_root=None):
    """乙版（表格，常浩 11-14 选定）+ 白话改写（常浩 11-14 定「按改写后改」）：
    标题说做了几件、几件等你处理 → 开头一句说托管替你做了什么 → 冻结时说清锁住了什么、你要做什么 →
    「等你处理」一张表 → 「替你定了的」一张表 → 表下「推翻」按钮 → 页脚说没发出去的回复和推翻会发生什么。"""
    frozen_ever = bool(session.get("frozen")) or kind == "frozen"
    start = C.parse_iso(session["started_at"]).astimezone(C.TZ)
    end = (session.get("ended") or {}).get("at") or (session.get("frozen") or {}).get("at")
    when = start.strftime("%m-%d %H:%M") + (" – " + C.parse_iso(end).astimezone(C.TZ).strftime("%m-%d %H:%M") if end else "")
    voids = [d for d in rows if d["status"] == "void"]
    waiting = [d for d in rows if d["status"] in WAITING]
    decided = [d for d in rows if d["status"] in ("done", "failed")]
    projects = "、".join(sorted({d["project"] for d in rows})) or "没有项目"
    if frozen_ever:
        title = "⚠️ 托管未能安全收尾"
    else:
        title = f"🌙 托管汇报：替你定了 {len(decided)} 件，{len(waiting)} 件等你处理"
    ending = "没能正常结束" if frozen_ever else "已正常结束"
    replies = sum(1 for d in decided if d["action"] == "reply" and d["status"] == "done")
    merges = sum(1 for d in decided if d["action"] == "merge" and d["status"] == "done")
    lead = f"你不在的时候，猛鬼兽按你签的托管规则，替你回答了 agent 的 {replies} 个问题、合并了 {merges} 个 PR"
    lead += f"；有 {len(waiting)} 件要你处理，见最上面一张表。" if waiting else "；没有要你处理的事。"
    if not rows:
        lead = "你不在的时候，agent 没来问过问题，猛鬼兽也没替你做任何决定。"
    els = [{"tag": "markdown", "content": f"<font color='grey'>{when} · {md_escape(projects)} 夜跑 · {ending}</font>\n{lead}"}]
    if frozen_ever:
        f = session.get("frozen") or {}
        items = "；".join(f.get("items") or []) or "见下面「等你处理」"
        locked = ("为了安全，托管已经锁住：不再替你做新决定、不再合并，也不能重新开托管。你核对清楚后，在私聊说「解除冻结」。"
                  if kind == "frozen" else "当时托管锁住了，你已经解除。")
        els.append({"tag": "markdown", "content": f"**托管结束时还有事情没核对清**（{md_escape(items)}）。{locked}"})
    if waiting:
        els += section("等你处理", waiting, True, night_root)
    if decided:
        els.append({"tag": "hr"})
        els += section("替你定了的", decided, False, night_root)
    foot = []
    if voids:
        foot.append(f"另有 {len(voids)} 个回复没发出去：" + "；".join(
            f"{short_ref(d)}「{clip(d['question'], 30)}」，写好了「{clip(d['summary'], 20)}」" for d in voids[:5])
            + ("…" if len(voids) > 5 else "") + "，但托管先结束了，agent 没收到，等于没定。")
    foot.append("点「推翻」后，我会记下以后同类事按你的意思办，并告诉你哪些后续工作受影响；撤销合并要你再点一次合并才生效。")
    els += [{"tag": "hr"}, {"tag": "markdown", "content": "<font color='grey'>" + md_escape("\n".join(foot)) + "</font>"}]
    els.append({"tag": "markdown", "content": f"<font color='grey'>编号 {session['id']}</font>"})    # 回读和查账用
    card = {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "red" if frozen_ever else "blue"},
            "body": {"elements": els}}
    return fit(card)


def fit(card):
    """卡片太大就从后往前删明细，末尾写还剩几条没列（用 custody_ledger.py list 看全部）。"""
    els = card["body"]["elements"]
    dropped = 0
    while len(json.dumps(card, ensure_ascii=False).encode()) > CARD_LIMIT and len(els) > 5:
        els.pop(-4)                         # 末尾三块（分隔线、页脚、编号）留着
        dropped += 1
    if dropped:
        els.insert(-3, {"tag": "markdown", "content": f"（卡片放不下，后面 {dropped} 块没列出来，在电脑上用 custody_ledger.py list 看全部）"})
    return card


def build(session_id, doc=None, store=None, night_root=None):
    session, kind = find_session(session_id, doc)
    with (contextlib.nullcontext(store) if store else L.open_store()) as st:
        rows = L.decisions(st, session_id)
    return session, kind, rows, build_card(session, kind, rows, night_root)


def send_report(session_id, force=False, now=None, chat=None, doc=None, store=None):
    """发汇报卡并回读。返回发送记录；同一次托管同一种收尾已发过就返回 {"unchanged": True}。"""
    now = now or C.now_tz()
    session, kind, rows, card = build(session_id, doc, store)
    rec = read_report(session_id)
    if not force and any(x["kind"] == kind and x.get("message_id") for x in rec["sent"]):
        return {"unchanged": True, "kind": kind}
    chat = chat if chat is not None else home_chat()
    if not chat.startswith("oc_"):
        raise ReportError("没有私聊 chat_id（FEISHU_HOME_CHANNEL，环境变量和 ~/.hermes/.env 里都没有）", "NoChat")
    key = f"custody-{kind}-{hashlib.sha1(session_id.encode()).hexdigest()[:16]}" + (f"-{int(now.timestamp())}" if force else "")
    entry = {"kind": kind, "at": C.iso(now), "decisions": [d["id"] for d in rows], "message_id": None, "read_back": False}
    try:
        entry["message_id"] = send(chat, "interactive", card, key)
        if not entry["message_id"]:
            raise SendError("发卡片没拿到消息号")
        got = lark(["im", "+messages-mget", "--message-ids", entry["message_id"]], "回读")
        m = (got.get("messages") or [{}])[0]
        entry["read_back"] = m.get("msg_type") == "interactive" and session_id in str(m.get("content"))
        if not entry["read_back"]:
            entry["error"] = "卡片已发，但回读对不上（不是卡片或没有托管号）"
    except SendError as e:
        entry["error"] = str(e)
    rec["sent"].append(entry)
    write_json(report_path(session_id), rec)          # 发出去了就记下（含回读失败），按钮靠它认卡片
    if entry.get("error"):
        raise ReportError(entry["error"] + ("" if entry["message_id"] else "（没发出去）"), "SendFailed")
    return entry


# ---------- 按钮 ----------
def reported(decision):
    """这个决定是不是在发出去的汇报卡上（只认发过的卡片）。"""
    rec = read_report(decision["session_id"])
    return any(decision["id"] in x.get("decisions", []) and x.get("message_id") for x in rec["sent"])


def overturn_path(decision):
    return L.raw_dir() / decision["session_id"] / f"{decision['id']}.overturn.json"


def append_candidate(d, now):
    """规则补丁候选（规格 §3.6「已推翻·已记录」）：以后同类按常浩的判断；具体怎么改规则由常浩在周提案里定。"""
    p = L.raw_dir() / "rule-candidates.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": C.iso(now), "decision": d["id"], "session": d["session_id"], "project": d["project"],
                            "action": d["action"], "target": d["target"], "question": d["question"], "summary": d["summary"],
                            "basis": d["basis"], "by": "常浩（汇报卡「推翻」）",
                            "candidate": "以后同类事项按常浩的判断处理；常浩没写理由，规则怎么改待他补充"}, ensure_ascii=False) + "\n")


def revert_card(d, rv):
    lines = [f"**撤销 PR 已开：[{rv['repo'].split('/')[-1]} PR #{rv['pr']}]({rv['url']})**",
             f"它合进 main 后，「{md_escape(d['context'])}」那次合并（{short_ref(d)}）的改动就撤掉了。",
             "点「合并撤销 PR」：我先看这个 PR 还开着、开出来后没被改过、GitHub 允许直接合，再用你的身份合并。"]
    btn = {"tag": "button", "text": {"tag": "plain_text", "content": "合并撤销 PR"}, "type": "primary_filled",
           "behaviors": [{"type": "callback", "value": {"custody": "merge_revert", "ref": d["id"]}}]}
    return {"schema": "2.0", "header": {"title": {"tag": "plain_text", "content": f"↩️ 撤销托管合并：{clip(d['context'], 20)}"},
                                        "template": "orange"},
            "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)}, btn]}}


def do_overturn(st, d, say, now, gh=None, night_root=None, revert=None):
    try:
        d = L.set_disposition(st, d["id"], "overturned", "常浩在汇报卡点「推翻」", by="user", now=now)
    except L.LedgerError as e:
        if e.code == "BadTransition" and d["disposition"] != "open":
            say(f"「{d['context']}」这件你已经处理过（{L.DISPOSITION[d['disposition']]}），不用再推翻。")
            return 0
        say(f"没推翻「{d['context']}」：{e}")
        return 1
    append_candidate(d, now)
    try:
        imp = IMP.impacts(st, d, gh=gh, night_root=night_root)
    except Exception as e:  # noqa: BLE001  影响清单出错不能吞掉推翻本身：照样记下，说明没列全
        imp = {"items": [], "gaps": [{"rule": r, "why": f"清单没算出来（{type(e).__name__}：{e}）"} for r in IMP.RULES]}
    rec = {"at": C.iso(now), "decision": d["id"], "impacts": imp}
    msg = [f"已推翻：{d['context']}（{short_ref(d)}），我当时定的是「{d['summary'] or '没替你定'}」。记下了，以后同类事按你的意思办。",
           "受影响的后续工作：", *IMP.describe(imp)]
    code = 0
    if d["action"] == "reply":
        msg.append(f"要让 agent 改过来：到任务 {d['target'].get('issue')} 上评论「撤回 {d['id']}：你要的答案」（{d['id']} 是这次决定的编号）。")
    elif d["action"] == "merge":
        mc = (d["outcome"] or {}).get("merge_commit")
        if d["status"] != "done" or not mc:
            L.set_disposition(st, d["id"], "revert_failed", f"没有可回退的合并提交（{L.STATUS[d['status']]}）", by="system", now=now)
            msg.append("这次合并没确认合上，所以不开撤销 PR；请你在电脑上核对这个 PR 合上没有。")
            code = 1
        else:
            revert = revert or __import__("merge_act").run_custody_revert
            try:
                rv = revert(d["target"]["repo"], mc, d["id"], f"Revert：托管决定 {d['id']}（{L.describe_target(d)}）")
            except Exception as e:  # noqa: BLE001  merge_act.Stop（没凭据）等：一律记回退失败
                rv = {"status": "failed", "why": getattr(e, "why", None) or f"{type(e).__name__}：{e}"}
            rec["revert"] = rv
            if rv["status"] == "opened":
                L.set_disposition(st, d["id"], "reverted", f"revert PR {rv['repo']}#{rv['pr']} {rv['url']}", by="system", now=now)
                msg.append(f"已开撤销 PR：{rv['url']}；要你点下一张卡片上的「合并撤销 PR」才生效。")
            else:
                L.set_disposition(st, d["id"], "revert_failed", rv.get("why") or rv["status"], by="system", now=now)
                msg.append(f"撤销 PR 没开成（{rv.get('why') or rv['status']}），请你在电脑上处理。")
                code = 1
    write_json(overturn_path(d), rec)
    say("\n".join(msg))
    if rec.get("revert", {}).get("status") == "opened":
        say(revert_card(d, rec["revert"]), card=True)
    return code


def do_merge_revert(d, say, run_gh=None, owner_merge=None):
    """常浩点「合并 revert PR」：现读 PR，核对后用本机 gh（常浩本人）合并，再回读。"""
    import merge_act as MA
    try:
        rec = json.loads(overturn_path(d).read_text(encoding="utf-8"))
        rv = rec["revert"]
        assert rv["status"] == "opened"
    except (OSError, ValueError, KeyError, AssertionError):
        say(f"没合并：「{d['context']}」没有开成的撤销 PR。")
        return 1
    if d["disposition"] != "reverted":
        say(f"没合并：「{d['context']}」现在是「{L.DISPOSITION[d['disposition']]}」，没有待合并的撤销 PR。")
        return 1
    run_gh = run_gh or (lambda args: MA.gh_read(args, "取 revert PR 状态"))
    owner_merge = owner_merge or MA.run_owner_merge
    fields = "state,headRefOid,baseRefName,mergeStateStatus,isDraft"
    try:
        p = json.loads(run_gh(["pr", "view", str(rv["pr"]), "--repo", rv["repo"], "--json", fields]))
    except Exception as e:  # noqa: BLE001
        say(f"没合并：查不到撤销 PR 现在的状态（{getattr(e, 'why', e)}），PR 没动。")
        return 1
    bad = [why for ok, why in ((p.get("state") == "OPEN", f"PR 不是开着的（{p.get('state')}）"),
                               (p.get("headRefOid") == rv["head"], "PR 开出来后又被改过"),
                               (p.get("baseRefName") == "main", "不是合进 main 的"),
                               (not p.get("isDraft"), "还是草稿"),
                               (p.get("mergeStateStatus") == "CLEAN", f"GitHub 显示现在不能直接合（{p.get('mergeStateStatus')}）")) if not ok]
    if bad:
        say(f"没合并撤销 PR {rv['url']}：{'；'.join(bad)}。PR 没动，请到 GitHub 上看。")
        return 1
    res = owner_merge({"repo": rv["repo"], "pr": rv["pr"], "head": rv["head"]})
    try:
        after = json.loads(run_gh(["pr", "view", str(rv["pr"]), "--repo", rv["repo"], "--json", "state"]))
    except Exception:  # noqa: BLE001
        after = {}
    if after.get("state") == "MERGED":
        rec["revert_merged"] = {"at": C.iso(C.now_tz()), "by": "常浩（卡片按钮）"}
        write_json(overturn_path(d), rec)
        say(f"✅ 撤销 PR 已合并：{rv['url']}，那次合并的改动已经撤掉。")
        return 0
    say(f"撤销 PR 不确定合上没有（{res.get('why') or '合并后查到的状态不是已合并'}），请到 GitHub 上看：{rv['url']}")
    return 1


def cmd_button(verb, ref, chat, now=None, sender=None, **kw):
    """按钮入口（merge-buttons 插件后台调用）。网关回复按钮点击会失败，进度和结果都直接发到私聊。"""
    now = now or C.now_tz()
    home = home_chat()
    if not chat or chat != home:
        print(f"不处理：点击不是来自常浩私聊（{chat or '没有 chat_id'}）")
        return 1
    click = hashlib.sha1(f"{verb} {ref} {now.isoformat()} {os.getpid()}".encode()).hexdigest()[:16]
    n = [0]

    def say(content, card=False):
        sender_fn = sender or send
        try:
            sender_fn(chat, "interactive" if card else "text", content if card else {"text": content}, f"custody-btn-{click}-{n[0]}")
        except SendError as e:
            print(f"私聊没发出去：{e}")
        n[0] += 1

    if verb not in ("overturn", "merge_revert") or not L.DECISION_ID.match(ref or ""):
        say(f"看不懂这个按钮（{verb} {ref}）。")
        return 1
    with L.open_store(now) as st:
        try:
            d = L.get(st, ref)
        except L.LedgerError as e:
            say(f"没处理：{e}")
            return 1
        if not reported(d):
            say(f"没处理：这个按钮对应的决定不在发给你的汇报卡上（{ref}）。")
            return 1
        lock_path = L.raw_dir() / d["session_id"] / f".{ref}.lock"
        lock = open(lock_path, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            say("上一次点击还在处理，等它的结果就好。")
            lock.close()
            return 1
        try:
            if verb == "overturn":
                return do_overturn(st, d, say, now, **{k: v for k, v in kw.items() if k in ("gh", "night_root", "revert")})
            return do_merge_revert(d, say, **{k: v for k, v in kw.items() if k in ("run_gh", "owner_merge")})
        finally:
            lock.close()


# ---------- 命令行 ----------
def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False, default=str))
    sys.exit(0 if ok else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("card").add_argument("--session", required=True)
    s = sub.add_parser("send")
    s.add_argument("--session", required=True)
    s.add_argument("--force", action="store_true")
    b = sub.add_parser("button")
    b.add_argument("verb", choices=("overturn", "merge_revert"))
    b.add_argument("ref")
    b.add_argument("--chat", default="")
    a = ap.parse_args(argv)
    if a.cmd == "button":
        sys.exit(cmd_button(a.verb, a.ref, a.chat))
    try:
        if a.cmd == "card":
            _, kind, rows, card = build(a.session)
            print(json.dumps(card, ensure_ascii=False, indent=1))
            done(True, f"{kind} 汇报卡，{len(rows)} 个决定（没发）")
        r = send_report(a.session, force=a.force)
        done(True, "这次托管的汇报已经发过了" if r.get("unchanged") else f"汇报卡已发并回读（{r['message_id']}）", **r)
    except (ReportError, L.LedgerError) as e:
        done(False, str(e), error=e.code)


if __name__ == "__main__":
    main()
