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
PINNED = ("suspended", "unknown", "unreconciled", "executing", "issued")


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
def result_text(d):
    o, s = d["outcome"] or {}, d["status"]
    if s == "done":
        return f"已合并（合并提交 {str(o.get('merge_commit', '?'))[:7]}）" if d["action"] == "merge" else "已送达"
    if s == "failed":
        return "没合并" if d["action"] == "merge" else "没送达"
    if s == "unreconciled":
        return "UNRECONCILED：对账核不清，额度冻结" + (f"（{clip(o.get('reconcile'), 40)}）" if o.get("reconcile") else "")
    if s == "unknown":
        return "结果不明，等对账，额度冻结"
    if s == "void":
        return f"已作废（{d['void_reason']}）"
    return {"suspended": "挂起，等你定", "issued": "在途：已出未送达", "executing": "在途：正在做"}[s]


def headline(d):
    if d["status"] == "unreconciled":
        return "🔴 UNRECONCILED"
    return {"reply": "回复", "merge": "合并", "suspend": "🟠 挂起"}[d["action"]] if d["status"] != "unknown" else "🔴 结果不明"


def item_elements(d, buttons=True):
    lines = [f"**{md_escape(L.describe_target(d))}** · {headline(d)} · `{d['id']}`",
             f"问：{md_escape(clip(d['question']) or '—')} → 定：**{md_escape(clip(d['summary']) or '—')}**",
             f"<font color='grey'>依据：{md_escape(clip(d['basis']))} · 结果：{md_escape(result_text(d))} · "
             f"{L.REVOCABILITY[d['revocability']] if d['action'] != 'suspend' else '—'}"
             + (f" · 处置：{L.DISPOSITION[d['disposition']]}" if d["disposition"] != "open" else "") + "</font>"]
    text = {"tag": "markdown", "content": "\n".join(lines)}
    if not (buttons and d["disposition"] == "open" and d["status"] not in L.IN_FLIGHT + ("void",)):
        return [text]
    btn = {"tag": "button", "text": {"tag": "plain_text", "content": "推翻"}, "type": "danger", "size": "small",
           "behaviors": [{"type": "callback", "value": {"custody": "overturn", "ref": d["id"]}}]}
    return [{"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px",
             "columns": [{"tag": "column", "width": "weighted", "weight": 1, "elements": [text]},
                         {"tag": "column", "width": "auto", "vertical_align": "center", "elements": [btn]}]}]


def build_card(session, kind, rows):
    """甲版（逐条清单，待常浩定稿）：摘要 → 冻结说明 → 要你先看（挂起 / 结果不明 / UNRECONCILED / 在途）→ 按项目分组 → 作废与说明。"""
    sid = session["id"]
    frozen_ever = bool(session.get("frozen")) or kind == "frozen"
    start = C.parse_iso(session["started_at"]).astimezone(C.TZ)
    end = (session.get("ended") or {}).get("at") or (session.get("frozen") or {}).get("at")
    when = start.strftime("%m-%d %H:%M") + (" – " + C.parse_iso(end).astimezone(C.TZ).strftime("%m-%d %H:%M") if end else "")
    title = "⚠️ 托管未能安全收尾" if frozen_ever else "🌙 托管汇报 · 已安全收尾"
    count = lambda pred: sum(1 for d in rows if pred(d))   # noqa: E731
    voids = [d for d in rows if d["status"] == "void"]
    shown = [d for d in rows if d["status"] != "void"]
    els = [{"tag": "markdown", "content":
            f"<font color='grey'>托管 {sid} · {when} · 策略 v{session['policy']['version']}</font>\n"
            f"决定 **{len(rows)}** · 回复 **{count(lambda d: d['action'] == 'reply')}** · 合并 **{count(lambda d: d['action'] == 'merge')}**"
            f" · 挂起 **{count(lambda d: d['status'] == 'suspended')}** · 作废 **{len(voids)}**"}]
    if frozen_ever:
        f = session.get("frozen") or {}
        items = "；".join(f.get("items") or []) or "见下方置顶"
        els.append({"tag": "markdown", "content":
                    f"**冻结**：{md_escape(f.get('reason', '排空没收完'))}（未解决：{md_escape(items)}）。已禁止新决定、新合并和重新开启托管"
                    + ("；你核对后在私聊说「解除冻结」。" if kind == "frozen" else "；已解除冻结。")})
    pinned = [d for d in shown if d["status"] in PINNED]
    if pinned:
        els.append({"tag": "markdown", "content": f"**要你先看 · {len(pinned)}**"})
        for d in pinned:
            els += item_elements(d)
    rest = [d for d in shown if d["status"] not in PINNED]
    for project in sorted({d["project"] for d in rest}):
        group = [d for d in rest if d["project"] == project]
        els.append({"tag": "hr"})
        els.append({"tag": "markdown", "content": f"**{md_escape(project)} · {len(group)} 条**"})
        for d in group:
            els += item_elements(d)
    if not rows:
        els.append({"tag": "markdown", "content": "这次托管没有做任何决定。"})
    foot = []
    if voids:
        foot.append(f"作废 {len(voids)} 条：" + "；".join(f"{L.describe_target(d)}（{d['void_reason']}）" for d in voids[:5])
                    + ("…" if len(voids) > 5 else ""))
    foot.append("点「推翻」：记下你的判断、列出受影响的后续 run / 分支 / PR / 依据它的决定；合并类会开 revert PR，revert PR 仍由你点合并。")
    els += [{"tag": "hr"}, {"tag": "markdown", "content": "<font color='grey'>" + md_escape("\n".join(foot)) + "</font>"}]
    card = {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "red" if frozen_ever else "blue"},
            "body": {"elements": els}}
    return fit(card)


def fit(card):
    """卡片太大就从后往前删明细，末尾写还剩几条没列（用 custody_ledger.py list 看全部）。"""
    els = card["body"]["elements"]
    dropped = 0
    while len(json.dumps(card, ensure_ascii=False).encode()) > CARD_LIMIT and len(els) > 4:
        els.pop(-3)
        dropped += 1
    if dropped:
        els.insert(-2, {"tag": "markdown", "content": f"（卡片放不下，后面 {dropped} 块没列，用 custody_ledger.py list 看全部）"})
    return card


def build(session_id, doc=None, store=None):
    session, kind = find_session(session_id, doc)
    with (contextlib.nullcontext(store) if store else L.open_store()) as st:
        rows = L.decisions(st, session_id)
    return session, kind, rows, build_card(session, kind, rows)


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
    ref = d["id"]
    lines = [f"**↩️ revert PR 已开：[{rv['repo']}#{rv['pr']}]({rv['url']})**",
             f"回退托管决定 `{ref}`（{md_escape(L.describe_target(d))}），HEAD `{rv['head'][:7]}`。",
             "点「合并 revert PR」：现读 PR，仍开着、HEAD 没变、可合并才用你的身份合并。"]
    btn = {"tag": "button", "text": {"tag": "plain_text", "content": "合并 revert PR"}, "type": "primary_filled",
           "behaviors": [{"type": "callback", "value": {"custody": "merge_revert", "ref": ref}}]}
    return {"schema": "2.0", "header": {"title": {"tag": "plain_text", "content": f"↩️ 托管回退 · {ref}"}, "template": "orange"},
            "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)}, btn]}}


def do_overturn(st, d, say, now, gh=None, night_root=None, revert=None):
    try:
        d = L.set_disposition(st, d["id"], "overturned", "常浩在汇报卡点「推翻」", by="user", now=now)
    except L.LedgerError as e:
        if e.code == "BadTransition" and d["disposition"] != "open":
            say(f"{d['id']} 已经是「{L.DISPOSITION[d['disposition']]}」，不用再推翻。")
            return 0
        say(f"没推翻 {d['id']}：{e}")
        return 1
    append_candidate(d, now)
    try:
        imp = IMP.impacts(st, d, gh=gh, night_root=night_root)
    except Exception as e:  # noqa: BLE001  影响清单出错不能吞掉推翻本身：照样记下，说明没列全
        imp = {"items": [], "gaps": [{"rule": r, "why": f"清单没算出来（{type(e).__name__}：{e}）"} for r in IMP.RULES]}
    rec = {"at": C.iso(now), "decision": d["id"], "impacts": imp}
    msg = [f"已推翻 {d['id']}（{L.ACTIONS[d['action']]} {L.describe_target(d)}），记下了：以后同类按你的判断。",
           "受影响的后续事项：", *IMP.describe(imp)]
    code = 0
    if d["action"] == "reply":
        msg.append(f"要改正 agent 的做法：在子单上评论「撤回 {d['id']}：……」并写正确答案。")
    elif d["action"] == "merge":
        mc = (d["outcome"] or {}).get("merge_commit")
        if d["status"] != "done" or not mc:
            L.set_disposition(st, d["id"], "revert_failed", f"没有可回退的合并提交（{L.STATUS[d['status']]}）", by="system", now=now)
            msg.append("这次合并没确认做成（没有合并提交号），不开 revert PR；记为「回退失败」，请你在电脑上核对。")
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
                msg.append(f"已开 revert PR：{rv['url']}；合并要你点下一张卡片上的按钮。")
            else:
                L.set_disposition(st, d["id"], "revert_failed", rv.get("why") or rv["status"], by="system", now=now)
                msg.append(f"revert PR 没开成（{rv['status']}：{rv.get('why')}），记为「回退失败」，请你在电脑上处理。")
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
        say(f"没合并：{d['id']} 没有开成的 revert PR 记录。")
        return 1
    if d["disposition"] != "reverted":
        say(f"没合并：{d['id']} 的处置是「{L.DISPOSITION[d['disposition']]}」，不是已回退。")
        return 1
    run_gh = run_gh or (lambda args: MA.gh_read(args, "取 revert PR 状态"))
    owner_merge = owner_merge or MA.run_owner_merge
    fields = "state,headRefOid,baseRefName,mergeStateStatus,isDraft"
    try:
        p = json.loads(run_gh(["pr", "view", str(rv["pr"]), "--repo", rv["repo"], "--json", fields]))
    except Exception as e:  # noqa: BLE001
        say(f"没合并：取不到 revert PR 状态（{getattr(e, 'why', e)}），PR 没动。")
        return 1
    bad = [why for ok, why in ((p.get("state") == "OPEN", f"PR 不是开着的（{p.get('state')}）"),
                               (p.get("headRefOid") == rv["head"], "PR 的 HEAD 和开出来时不一样"),
                               (p.get("baseRefName") == "main", "base 不是 main"),
                               (not p.get("isDraft"), "是草稿"),
                               (p.get("mergeStateStatus") == "CLEAN", f"不能直接合并（{p.get('mergeStateStatus')}）")) if not ok]
    if bad:
        say(f"没合并 revert PR {rv['url']}：{'；'.join(bad)}。PR 没动，请到 GitHub 上看。")
        return 1
    res = owner_merge({"repo": rv["repo"], "pr": rv["pr"], "head": rv["head"]})
    try:
        after = json.loads(run_gh(["pr", "view", str(rv["pr"]), "--repo", rv["repo"], "--json", "state"]))
    except Exception:  # noqa: BLE001
        after = {}
    if after.get("state") == "MERGED":
        rec["revert_merged"] = {"at": C.iso(C.now_tz()), "by": "常浩（卡片按钮）"}
        write_json(overturn_path(d), rec)
        say(f"✅ revert PR 已合并：{rv['url']}")
        return 0
    say(f"revert PR 不确定合上没有（{res.get('why') or '回读不是已合并'}），请到 GitHub 上看：{rv['url']}")
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
            say(f"没处理：{ref} 不在发过的汇报卡上（只认汇报卡上的决定）。")
            return 1
        lock_path = L.raw_dir() / d["session_id"] / f".{ref}.lock"
        lock = open(lock_path, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            say(f"{ref} 正在处理，等上一次的结果就好。")
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
