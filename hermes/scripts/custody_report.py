#!/usr/bin/env python3
"""托管汇报卡与推翻（#133，规格 §3.6；ARCHITECTURE 5.7 第 10 条）。

汇报：托管收尾（排空完成到 off、或冻结）后，按账本给常浩私聊发一张卡片（乙版表格 + 白话，常浩 11-14 定）：
  「等你处理」「替你定了的」各一张六列表格，表下一排「推翻：我替你定的」按钮；这次托管冻结过，标题写「托管未能安全收尾」。
  发卡前：只发常浩私聊（home_chat）；先核对账本身份（check_ledger：会话里记的 ledger.id、开启登记、没有有原文没账本的决定），
  对不上不发，「已经发过」这类提前返回也在核对之后；再在账本里排空这次托管（已出未送达的作废）。
  卡片页脚小字写托管号和这张卡的令牌；回读（飞书只给渲染后的文字）要有托管号、令牌，「[推翻：」按钮数等于 actionable 才算核实。
  每次发送在 DT_CUSTODY_DIR/<托管号>/report.json 记一条：kind（final / frozen）、随机卡片令牌 card、decisions（卡上列的）、
  actionable（卡上真有「推翻」按钮的）、message_id、status（unverified 发了还没核实 / verified 按消息号回读核实了）。
  同一种收尾有 verified 就不再发（--force 才再发）；只有 unverified 的，先按原消息号回读，核实了就不重发，核实不了才换新令牌重发。
推翻：按钮 value 是 {"custody": "overturn", "ref": 决定号, "card": 卡片令牌}；merge-buttons 插件的 /card 认出它，后台调
  `custody_report.py button overturn <决定号> --chat <chat_id> --card <令牌>`。只认常浩私聊、只认最新一张 verified 汇报卡上
  actionable 里的决定（--force 重发后旧卡不认），并先过 check_ledger（账本对不上不处理）。
  分阶段、可恢复，进度记在 DT_CUSTODY_DIR/<托管号>/<决定号>.overturn.json 的 stages：
    registered 登记推翻（处置改成「已推翻·已记录」）→ candidate 规则补丁候选（rule-candidates.jsonl，同一决定只记一次）
    → impacts 下游影响清单（custody_impact，取不到的写「没列全」）→ revert 开撤销 PR（只限合并）
    → revert_card 发带「合并撤销 PR」按钮的卡片并回读 → result 把结果发到私聊。
  中断后再点一次「推翻」，或在电脑上跑 `custody_report.py resume <决定号>`（只续做常浩点过的），只补做没完成的阶段。
  开撤销 PR（merge_act.run_custody_revert，只开不合）：合并还没核清（结果不明 / 核不清）时记「等核清再开」，不算失败；
  确定没开成记「回退失败」，再点会重试；开 PR 前先记操作凭证（决定号、仓库、分支、合并提交号），执行器推送之前再经 on_head
  把本地撤销提交号落盘。中断或不确定时：没记下提交号＝没推过，重新开；记下了就只按分支 custody-revert/<决定号> 只读查，
  revert_pr_matches 核验（HEAD 等于记下的提交号、来源就是目标仓库；另核开着合进 main、只有一个提交、提交说明写着回退这次合并提交、
  PR 正文有决定号和合并提交号）且唯一才认领，不再推送；对不上、不唯一、查不全都保持不确定。
合并撤销 PR：按钮 {"custody": "merge_revert", "ref": 决定号, "card": 撤销卡令牌}，先过 check_ledger，只认最新一张、回读核实过的撤销卡；
  交给 merge_act.merge_revert_as_owner：和飞书卡片合并同一套检查（开着、HEAD 没变、合进 main、不是草稿、没碰敏感路径、
  CI 全部通过、CLEAN；撤销 PR 没有 CI 也停），并要求 merge_rules.json 的 revert_required_checks 里登记的测试检查真的 SUCCESS
  （跳过 / 中立不算，没登记就停；dt-sandbox 现在没有 CI，所以撤销 PR 现在都会停在这里），过了才用本机 gh（常浩本人）带
  --match-head-commit 合并、回读。

用法（card / send 最后一行是一行 JSON：{"ok", "msg", ...}）：
  custody_report.py card --session c-…        只打印卡片 JSON（不发、不清账）
  custody_report.py send --session c-… [--force]
  custody_report.py button overturn|merge_revert <决定号> --chat <chat_id> [--card <令牌>]
  custody_report.py resume <决定号>            电脑上续做中断的推翻（结果照样发私聊）
不调用 AI；不装定时任务。谁在收尾后调 send 由 #134 接（夜跑主控 / custody 技能）。
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
        runs, problems = IMP.later_runs(d, night_root)
    except IMP.Unavailable:
        return "还不知道（夜跑记录读不到）"
    if problems:
        return "还不知道（夜跑记录不完整）"
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


def overturn_buttons(rows, token=None):
    """飞书表格里放不了按钮：表下一排「推翻：我替你定的」，自动换行。没有可推翻的就不放。
    token 是这张卡的随机令牌（发送时才有，预览没有）：按钮带上它，点击时核对是不是发出去并核实过的那张卡（第 1 轮审查 P1-04）。"""
    def value(d):
        return {"custody": "overturn", "ref": d["id"], **({"card": token} if token else {})}
    btns = [{"tag": "column", "width": "auto", "elements": [
        {"tag": "button", "text": {"tag": "plain_text", "content": f"推翻：{clip(d['summary'] or d['context'], 14)}"},
         "type": "danger", "size": "small", "behaviors": [{"type": "callback", "value": value(d)}]}]}
        for d in rows if can_overturn(d)]
    return [{"tag": "column_set", "flex_mode": "flow", "horizontal_spacing": "8px", "columns": btns}] if btns else []


def section(title, rows, waiting, night_root=None, token=None):
    """一块：标题 + 表格 + 推翻按钮；超过 MAX_ROWS 条只列前面的，写明还有几条没列（按钮只给列出来的）。"""
    shown, more = rows[:MAX_ROWS], len(rows) - MAX_ROWS
    els = [{"tag": "markdown", "content": f"**{title} · {len(rows)} 件**"}, table(shown, waiting, night_root),
           *overturn_buttons(shown, token)]
    if more > 0:
        els.append({"tag": "markdown", "content": f"<font color='grey'>还有 {more} 件没列（卡片放不下），在电脑上用 custody_ledger.py list 看全部</font>"})
    return els


def build_card(session, kind, rows, night_root=None, token=None):
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
        els += section("等你处理", waiting, True, night_root, token)
    if decided:
        els.append({"tag": "hr"})
        els += section("替你定了的", decided, False, night_root, token)
    foot = []
    if voids:
        foot.append(f"另有 {len(voids)} 个回复没发出去：" + "；".join(
            f"{short_ref(d)}「{clip(d['question'], 30)}」，写好了「{clip(d['summary'], 20)}」" for d in voids[:5])
            + ("…" if len(voids) > 5 else "") + "，但托管先结束了，agent 没收到，等于没定。")
    foot.append("点「推翻」后，我会记下以后同类事按你的意思办，并告诉你哪些后续工作受影响；撤销合并要你再点一次合并才生效。")
    els += [{"tag": "hr"}, {"tag": "markdown", "content": "<font color='grey'>" + md_escape("\n".join(foot)) + "</font>"}]
    els.append({"tag": "markdown", "content": f"<font color='grey'>编号 {session['id']}" + (f" · {token}" if token else "") + "</font>"})  # 回读核对用
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


def build(session_id, doc=None, store=None, night_root=None, token=None):
    session, kind = find_session(session_id, doc)
    with (contextlib.nullcontext(store) if store else L.open_store()) as st:
        rows = L.decisions(st, session_id)
    return session, kind, rows, build_card(session, kind, rows, night_root, token)


def card_refs(card, verb):
    """卡片里真有按钮的决定（第 1 轮审查 P2-03）：从最终的卡片 JSON 里数，截断掉的不算。"""
    out = []

    def walk(x):
        if isinstance(x, dict):
            for b in x.get("behaviors") or []:
                v = b.get("value") if isinstance(b, dict) else None
                if isinstance(v, dict) and v.get("custody") == verb and v.get("ref") not in out:
                    out.append(v["ref"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(card)
    return out


def read_back(message_id, markers, button=None, count=0):
    """按消息号回读（第 2 轮审查 P2-02）：是卡片、内容里有全部 markers（托管号或决定号，加这张卡的令牌；令牌写在页脚小字里，
    飞书回读只给渲染后的文字，读不到按钮的 value），按钮 button（回读里显示成「[按钮字]」）正好 count 个，才算核实。
    返回 (核实了没有, 没核实的原因)。"""
    try:
        got = lark(["im", "+messages-mget", "--message-ids", message_id], "回读")
    except SendError as e:
        return False, f"回读没成功（{e}）"
    m = (got.get("messages") or [{}])[0]
    content = str(m.get("content"))
    if m.get("msg_type") != "interactive" or not all(x in content for x in markers):
        return False, "卡片已发，但回读对不上（不是卡片，或没有编号和这张卡的令牌）"
    if button and content.count(f"[{button}") != count:
        return False, f"卡片已发，但回读到的按钮数对不上（应有 {count} 个「{button}」）"
    return True, ""


def ledger_of(session_id, doc=None):
    """custody.json 里这次托管开启时记下的账本身份（现在这次或历史里的）；找不到返回 None。"""
    try:
        doc = doc or C.load()
    except ValueError as e:
        raise ReportError(f"托管状态读不了：{e}", "Corrupt")
    for s in ([doc["session"]] if doc.get("session") else []) + list(reversed(doc.get("history") or [])):
        if s.get("id") == session_id:
            return (s.get("ledger") or {}).get("id")
    return None


def check_ledger(st, session_id, lid):
    """账本身份统一核对（第 2 轮审查 P1-02）：库还是开启时登记的那一份、这次托管登记过、没有「有原文、账本里没有」的决定。
    汇报（含提前返回）、推翻、合并撤销 PR 都先过这一关；对不上抛 ReportError（LedgerMismatch），什么都不做。"""
    try:
        L.check_session(st, session_id, lid)
    except L.LedgerError as e:
        raise ReportError(f"托管账本对不上，不处理：{e}", e.code)
    try:
        orphans = L.raw_orphans(st, session_id)
    except L.LedgerError as e:
        raise ReportError(f"托管账本对不上，不处理：{e}", e.code)
    if orphans:
        raise ReportError(f"托管账本对不上，不处理：{len(orphans)} 个决定有原文、账本里却没有（账本可能被旧备份覆盖）", "LedgerMismatch")


def send_report(session_id, force=False, now=None, chat=None, doc=None, store=None, night_root=None):
    """发汇报卡并回读（第 1 轮审查 P1-04、P1-07、P2-03、P2-04；第 2 轮 P1-02、P2-02）。
    只发常浩私聊（chat 必须等于 home_chat）。先打开账本核对身份（check_ledger），任何返回（包括「已经发过」）都在这之后；
    再在账本里排空这次托管（已出未送达的决定作废、释放额度）。每次发送记一条：kind、随机卡片令牌 card、decisions（卡上列的）、
    actionable（卡上真有「推翻」按钮的）、message_id、status（unverified 发了还没核实 / verified 回读核实了）。
    同一种收尾已有 verified 就不再发（--force 才再发）；只有 unverified 的，先按原消息号回读，核实了就记 verified、不重发，
    核实不了才换新令牌重发。"""
    now = now or C.now_tz()
    home = home_chat()
    if not home.startswith("oc_"):
        raise ReportError("没有私聊 chat_id（FEISHU_HOME_CHANNEL，环境变量和 ~/.hermes/.env 里都没有）", "NoChat")
    if chat is not None and chat != home:
        raise ReportError(f"汇报卡只发常浩私聊，不发 {chat}", "WrongChat")
    session, kind = find_session(session_id, doc)
    lid = (session.get("ledger") or {}).get("id")
    try:
        cm = contextlib.nullcontext(store) if store else L.open_store()
    except L.LedgerError as e:
        raise ReportError(f"托管账本打不开，不汇报：{e}", e.code)
    with cm as st:
        check_ledger(st, session_id, lid)
        rec = read_report(session_id)
        mine = [x for x in rec["sent"] if x["kind"] == kind]
        if not force:
            if any(x.get("status") == "verified" for x in mine):
                return {"unchanged": True, "kind": kind}
            last = next((x for x in reversed(mine) if x.get("message_id")), None)
            if last:
                ok, why = read_back(last["message_id"], [session_id, last.get("card") or "（没有令牌）"], "推翻：",
                                    len(last.get("actionable") or []))
                if ok:
                    last.update(status="verified", verified_at=C.iso(now))
                    last.pop("error", None)
                    write_json(report_path(session_id), rec)
                    return {**last, "rechecked": True}
                last["recheck"] = why
        try:
            L.drain(st, session_id, lid, now)
        except L.LedgerError as e:
            raise ReportError(f"汇报前清账没过：{e}", e.code)
        token = secrets.token_hex(8)
        _, kind, rows, card = build(session_id, doc, st, night_root, token)
    entry = {"kind": kind, "at": C.iso(now), "card": token, "decisions": [d["id"] for d in rows],
             "actionable": card_refs(card, "overturn"), "message_id": None, "status": "unverified"}
    rec["sent"].append(entry)
    try:
        entry["message_id"] = send(home, "interactive", card, f"custody-{kind}-{token}")
        if not entry["message_id"]:
            raise SendError("发卡片没拿到消息号")
    except SendError as e:
        entry["error"] = str(e)
    write_json(report_path(session_id), rec)          # 发出去了先记下消息号，回读失败下次还能按它核实
    if entry["message_id"]:
        ok, why = read_back(entry["message_id"], [session_id, token], "推翻：", len(entry["actionable"]))
        if ok:
            entry["status"] = "verified"
        else:
            entry["error"] = why
        write_json(report_path(session_id), rec)
    if entry.get("error"):
        raise ReportError(entry["error"] + ("" if entry["message_id"] else "（没发出去）"), "SendFailed")
    return entry


# ---------- 按钮 ----------
CARD_TOKEN = re.compile(r"^[0-9a-f]{16}$")


def reported(decision, card):
    """这个决定的「推翻」按钮是不是在最新一张发出去并核实过的汇报卡上（第 2 轮审查 P2-02：--force 重发或收尾后再发新卡，
    旧卡的按钮就不认了）：令牌等于最新一张 verified 卡的、决定在那张卡的 actionable 里。"""
    if not CARD_TOKEN.match(card or ""):
        return False
    verified = [x for x in read_report(decision["session_id"])["sent"] if x.get("status") == "verified"]
    return bool(verified) and verified[-1].get("card") == card and decision["id"] in verified[-1].get("actionable", [])


def overturn_path(decision):
    return L.raw_dir() / decision["session_id"] / f"{decision['id']}.overturn.json"


def read_overturn(decision):
    try:
        return json.loads(overturn_path(decision).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        raise ReportError(f"推翻进度读不了：{e}", "Corrupt")


def append_candidate(d, now):
    """规则补丁候选（规格 §3.6「已推翻·已记录」）：以后同类按常浩的判断；具体怎么改规则由常浩在周提案里定。
    同一个决定只记一条（推翻中断后续做时不重复记）。"""
    p = L.raw_dir() / "rule-candidates.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValueError):
                if json.loads(line).get("decision") == d["id"]:
                    return
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": C.iso(now), "decision": d["id"], "session": d["session_id"], "project": d["project"],
                            "action": d["action"], "target": d["target"], "question": d["question"], "summary": d["summary"],
                            "basis": d["basis"], "by": "常浩（汇报卡「推翻」）",
                            "candidate": "以后同类事项按常浩的判断处理；常浩没写理由，规则怎么改待他补充"}, ensure_ascii=False) + "\n")


def revert_card(d, rv, token):
    lines = [f"**撤销 PR 已开：[{rv['repo'].split('/')[-1]} PR #{rv['pr']}]({rv['url']})**",
             f"它合进 main 后，「{md_escape(d['context'])}」那次合并（{short_ref(d)}）的改动就撤掉了。",
             "点「合并撤销 PR」：我先看这个 PR 还开着、开出来后没被改过、CI 全部通过、没碰敏感文件，再用你的身份合并。"]
    btn = {"tag": "button", "text": {"tag": "plain_text", "content": "合并撤销 PR"}, "type": "primary_filled",
           "behaviors": [{"type": "callback", "value": {"custody": "merge_revert", "ref": d["id"], "card": token}}]}
    return {"schema": "2.0", "header": {"title": {"tag": "plain_text", "content": f"↩️ 撤销托管合并：{clip(d['context'], 20)}"},
                                        "template": "orange"},
            "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)}, btn,
                                  {"tag": "markdown", "content": f"<font color='grey'>编号 {d['id']} · {token}</font>"}]}}   # 回读核对用


REVERT_LOOKUP_LIMIT = 100


def find_revert_pr(repo, branch):
    """只读对账（P1-03）：按分支 custody-revert/<决定号> 查有没有撤销 PR（含来源仓库、PR 正文和提交）。返回 PR 列表；
    查不了、或条数到了上限（查不全，证明不了「恰好一个」）抛 merge_act.Stop。"""
    import merge_act as MA
    got = MA.gh_read(["pr", "list", "--repo", repo, "--head", branch, "--state", "all", "--limit", str(REVERT_LOOKUP_LIMIT),
                      "--json", f"{MA.REVERT_PR_FIELDS},body,commits"], "查撤销 PR")
    got = got if isinstance(got, list) else []
    if len(got) >= REVERT_LOOKUP_LIMIT:
        raise MA.Stop(f"分支 {branch} 上的 PR 有 {len(got)} 个以上，查不全", "到 GitHub 上看")
    return got


def revert_pr_matches(p, d, merge_commit, head, repo):
    """只读认领撤销 PR（第 2 轮审查 P1-01、定向复核 P1-01）：身份靠 HEAD 等于推送前落盘的本地撤销提交号（完整 40 位）、
    来源分支就在目标仓库本身；提交说明和 PR 正文是谁都能写的文字，只作附加核对：开着、合进 main、只有一个提交且就是 HEAD、
    提交说明写着回退的正是这次合并提交（「This reverts commit <提交号>」）、PR 正文写着决定号和合并提交号。
    不靠分支名同名认领。返回没对上的原因列表（空 = 对上了）。"""
    import merge_act as MA
    bad = []
    if not head or p.get("headRefOid") != head:
        bad.append("PR 的提交不是推送前记下的那个撤销提交")
    if not MA.same_repo(p, repo):
        bad.append("PR 不是从目标仓库本身的分支开的")
    commits = p.get("commits") or []
    if p.get("state") != "OPEN" or p.get("baseRefName") != "main":
        bad.append(f"PR 不是开着合进 main 的（{p.get('state')} → {p.get('baseRefName')}）")
    if len(commits) != 1 or commits[0].get("oid") != p.get("headRefOid"):
        bad.append(f"PR 里有 {len(commits)} 个提交，不是只有一个撤销提交")
    elif f"This reverts commit {merge_commit}" not in f"{commits[0].get('messageHeadline', '')}\n{commits[0].get('messageBody', '')}":
        bad.append("提交说明里没写回退的是这次合并提交")
    if d["id"] not in str(p.get("body")) or merge_commit not in str(p.get("body")):
        bad.append("PR 正文里没有这个决定和合并提交")
    return bad


def revert_step(st, d, rec, save, now, revert=None, lookup=None):
    """推翻合并的「开撤销 PR」阶段。结果记在 rec["revert"]["status"]：
    opened 开成了；not_needed 合并本来没合上；waiting 合并还没核清，等核清再开（不算失败）；
    failed 确定没开成、GitHub 上没留东西（再点「推翻」会重试）；unsure 开没开成不确定（之后只读查，不再推送）。返回这个阶段做完没有。"""
    import merge_act as MA
    rv = rec.get("revert") or {}
    if d["status"] in ("unknown", "unreconciled"):
        rec["revert"] = {"status": "waiting", "why": "合并还没核清合上没有"}
        return False
    if d["status"] != "done":
        rec["revert"] = {"status": "not_needed", "why": "这次合并没合上"}
        return True
    mc = (d["outcome"] or {}).get("merge_commit")
    branch = MA.REVERT_BRANCH.format(decision=d["id"])
    if not mc:
        rv = {"status": "failed", "why": "账本里没有合并提交号，开不了撤销 PR"}
    elif rv.get("status") in ("opening", "unsure") and rv.get("head"):   # 推送前记下了提交号：可能推过，只读查、核验后认领，不再推送
        repo = d["target"]["repo"]
        try:
            found = (lookup or find_revert_pr)(repo, branch)
            checked = [(x, revert_pr_matches(x, d, mc, rv["head"], repo)) for x in found]
            hit = [x for x, bad in checked if not bad]
            why = ("GitHub 上没有这个分支的撤销 PR" if not found else
                   "分支上的 PR 对不上：" + "；".join(b for _, bad in checked for b in bad) if not hit else
                   f"分支上有 {len(hit)} 个对得上的 PR，认不出是哪个" if len(hit) > 1 else "")
        except Exception as e:  # noqa: BLE001
            hit, why = [], f"查不了 GitHub（{getattr(e, 'why', None) or e}）"
        if len(hit) == 1:
            rv = {**rv, "status": "opened", "repo": repo, "pr": hit[0]["number"], "url": hit[0]["url"],
                  "branch": branch, "claimed": "只读核验后认领"}
        else:
            rv = {**rv, "status": "unsure", "repo": repo, "branch": branch, "why": why or rv.get("why") or "上次开撤销 PR 时中断了"}
    elif rv.get("status") != "opened":
        # 没开过、上次确定没开成、或上次中断在记下提交号之前（推送在落盘之后，没提交号就一定没推过）：开
        rec["revert"] = {"status": "opening", "at": C.iso(now), "decision": d["id"], "repo": d["target"]["repo"],   # 中断后只读核验的凭证
                         "branch": branch, "merge_commit": mc}
        save()

        def on_head(head):                                    # 推送之前落盘本地撤销提交号；写不下就抛，执行器不推
            rec["revert"]["head"] = head
            save()
        revert = revert or MA.run_custody_revert
        try:
            rv = revert(d["target"]["repo"], mc, d["id"], f"Revert：托管决定 {d['id']}（{L.describe_target(d)}）", on_head=on_head)
        except MA.Stop as e:                                  # 没凭据等：推之前就停了
            rv = {"status": "failed", "why": e.why}
        except Exception as e:  # noqa: BLE001  不知道停在哪一步：按不确定处理；记下过提交号的之后只读查
            rv = {**rec["revert"], "status": "unsure", "why": f"{type(e).__name__}：{e}"}
    rec["revert"] = rv
    if rv["status"] == "opened":
        if d["disposition"] != "reverted":
            L.set_disposition(st, d["id"], "reverted", f"revert PR {rv['repo']}#{rv['pr']} {rv['url']}", by="system", now=now)
        return True
    if rv["status"] == "failed" and d["disposition"] == "overturned":
        L.set_disposition(st, d["id"], "revert_failed", rv.get("why") or "没开成", by="system", now=now)
    return False


def overturn_message(d, rec):
    msg = [f"已推翻：{d['context']}（{short_ref(d)}），我当时定的是「{d['summary'] or '没替你定'}」。记下了，以后同类事按你的意思办。",
           "受影响的后续工作：", *IMP.describe(rec["impacts"])]
    if d["action"] == "reply":
        msg.append(f"要让 agent 改过来：到任务 {d['target'].get('issue')} 上评论「撤回 {d['id']}：你要的答案」（{d['id']} 是这次决定的编号）。")
    elif d["action"] == "merge":
        rv, rc = rec.get("revert") or {}, rec.get("revert_card") or {}
        s, why = rv.get("status"), rv.get("why") or ""
        if s == "opened":
            msg.append(f"已开撤销 PR：{rv['url']}；要你点上面卡片里的「合并撤销 PR」才生效。" if rc.get("status") == "verified" else
                       f"已开撤销 PR：{rv['url']}；带「合并撤销 PR」按钮的卡片没发成，再点一次「推翻」我会重发。")
        elif s == "waiting":
            msg.append("这次合并还没核清合上没有，撤销 PR 等核清再开：核清确认合上后，再点一次「推翻」，"
                       f"或在电脑上跑 custody_report.py resume {d['id']}。")
        elif s == "not_needed":
            msg.append("这次合并没合上，不用撤销。")
        elif s == "unsure":
            msg.append(f"撤销 PR 开没开成不确定（{why}）。我不会再推一次，只去 GitHub 上查分支 {rv.get('branch')}；"
                       "你也可以到 GitHub 上看，再点一次「推翻」我会重新查。")
        else:
            msg.append(f"撤销 PR 没开成（{why or '原因不明'}），请你在电脑上处理；原因解决后再点一次「推翻」会重试。")
    return "\n".join(msg)


def finished(d, rec):
    """推翻的每个阶段都做完了：合并类还要撤销卡核实过（或本来不用撤销）。等核清的（waiting）不算做完。"""
    st = rec["stages"]
    if not all(k in st for k in ("registered", "candidate", "impacts", "result")):
        return False
    return d["action"] != "merge" or "revert_card" in st or (rec.get("revert") or {}).get("status") == "not_needed"


def do_overturn(st, d, say, now, chat, sender=None, gh=None, night_root=None, revert=None, lookup=None):
    """推翻（第 1 轮审查 P1-03：分阶段、可恢复）。进度记在 <决定号>.overturn.json 的 stages：
    registered 登记推翻 → candidate 规则补丁候选（同一决定只记一次）→ impacts 下游影响清单 → revert 开撤销 PR（只限合并）
    → revert_card 发撤销卡并回读 → result 把结果发到私聊。中断后再点「推翻」或在电脑上 resume，只补做没完成的阶段。"""
    rec = read_overturn(d)
    resumed = rec is not None
    if rec is None:
        if d["disposition"] != "open":
            say(f"「{d['context']}」这件你已经处理过（{L.DISPOSITION[d['disposition']]}），不用再推翻。")
            return 0
        rec = {"decision": d["id"], "at": C.iso(now), "stages": {}}
    stages = rec["stages"]

    def save():
        write_json(overturn_path(d), rec)

    if "registered" not in stages:
        save()                                              # 先落进度文件，再改账本：中断后能从这里续做
        if d["disposition"] == "open":
            try:
                d = L.set_disposition(st, d["id"], "overturned", "常浩在汇报卡点「推翻」", by="user", now=now)
            except L.LedgerError as e:
                say(f"没推翻「{d['context']}」：{e}")
                return 1
        stages["registered"] = C.iso(now)
        save()
    elif finished(d, rec):
        say(f"「{d['context']}」你已经推翻过了（{L.DISPOSITION[d['disposition']]}），不用再推翻。")
        return 0
    if "candidate" not in stages:
        append_candidate(d, now)
        stages["candidate"] = C.iso(now)
        save()
    if "impacts" not in stages:
        try:
            rec["impacts"] = IMP.impacts(st, d, gh=gh, night_root=night_root)
        except Exception as e:  # noqa: BLE001  影响清单出错不能吞掉推翻本身：照样记下，说明没列全
            rec["impacts"] = {"items": [], "gaps": [{"rule": r, "why": f"清单没算出来（{type(e).__name__}：{e}）"} for r in IMP.RULES]}
        stages["impacts"] = C.iso(now)
        save()
    ok = True
    if d["action"] == "merge":
        if "revert" not in stages:
            if revert_step(st, d, rec, save, now, revert, lookup):
                stages["revert"] = C.iso(now)
            save()
            d = L.get(st, d["id"])
        if (rec.get("revert") or {}).get("status") == "opened" and "revert_card" not in stages:
            if deliver_revert_card(d, rec, save, now, chat, sender):
                stages["revert_card"] = C.iso(now)
            save()
        ok = (rec.get("revert") or {}).get("status") in ("opened", "waiting", "not_needed") and \
            ((rec.get("revert") or {}).get("status") != "opened" or "revert_card" in stages)
    sig = [(rec.get("revert") or {}).get("status"), (rec.get("revert_card") or {}).get("status")]
    if (stages.get("result") or {}).get("sig") != sig or resumed:      # 再点一次时照样告诉常浩现在到哪了
        if say(overturn_message(d, rec)):
            stages["result"] = {"at": C.iso(now), "sig": sig}
        else:
            ok = False
        save()
    return 0 if ok else 1


def deliver_revert_card(d, rec, save, now, chat, sender=None):
    """发带「合并撤销 PR」按钮的卡片并回读（P1-03、P1-04）。上次发了没核实的，先按原消息号回读；核实不了才换新令牌重发。"""
    rv, rc = rec["revert"], rec.get("revert_card") or {}
    if rc.get("message_id") and rc.get("status") != "verified":
        good, why = read_back(rc["message_id"], [d["id"], rc.get("card") or "（没有令牌）"], "合并撤销 PR", 1)
        if good:
            rc["status"] = "verified"
            rec["revert_card"] = rc
            return True
    token = secrets.token_hex(8)
    rc = rec["revert_card"] = {"card": token, "at": C.iso(now), "message_id": None, "status": "unverified"}
    save()
    try:
        rc["message_id"] = (sender or send)(chat, "interactive", revert_card(d, rv, token), f"custody-rvcard-{token}")
    except SendError as e:
        rc["error"] = str(e)
        return False
    save()
    if not rc["message_id"]:
        rc["error"] = "发卡片没拿到消息号"
        return False
    good, why = read_back(rc["message_id"], [d["id"], token], "合并撤销 PR", 1)
    rc["status"] = "verified" if good else "unverified"
    if not good:
        rc["error"] = why
    return good


def do_merge_revert(d, say, card, merge=None):
    """常浩点「合并撤销 PR」（P0-01）：先核对按钮来自最新一张、核实过的撤销卡，再交给 merge_act.merge_revert_as_owner：
    和飞书卡片合并同一套检查（开着、HEAD 没变、合进 main、不是草稿、没碰敏感路径、CI 全部通过、CLEAN），过了才用本机 gh 合并、回读。"""
    import merge_act as MA
    rec = read_overturn(d) or {}
    rv, rc = rec.get("revert") or {}, rec.get("revert_card") or {}
    if rv.get("status") != "opened":
        say(f"没合并：「{d['context']}」没有开成的撤销 PR。")
        return 1
    if not CARD_TOKEN.match(card or "") or card != rc.get("card") or rc.get("status") != "verified":
        say(f"没合并：这个按钮不是最新一张撤销卡上的，请用最新那张卡片上的「合并撤销 PR」（{rv['url']}）。")
        return 1
    if rec.get("revert_merged"):
        say(f"撤销 PR 已经合过了：{rv['url']}。")
        return 0
    if d["disposition"] != "reverted":
        say(f"没合并：「{d['context']}」现在是「{L.DISPOSITION[d['disposition']]}」，没有待合并的撤销 PR。")
        return 1
    try:
        res = (merge or MA.merge_revert_as_owner)(rv["repo"], rv["pr"], rv["head"])
    except MA.Stop as e:
        why = re.sub(r"（L[0-3]）", "", e.why)              # 写给常浩的字不出现风险档术语
        say(f"{e.verdict or '没合并撤销 PR'}：{why}。{e.state}。{e.todo.replace('{again}', '再点一次「合并撤销 PR」')}。{rv['url']}")
        return 1
    if res["status"] in ("merged", "merged_unattributed"):
        rec["revert_merged"] = {"at": C.iso(C.now_tz()), "by": "常浩（卡片按钮）" if res["status"] == "merged" else "不是这次点击合的",
                                "merge_commit": res.get("merge_commit")}
        write_json(overturn_path(d), rec)
        say(f"✅ 撤销 PR 已合并：{rv['url']}，那次合并的改动已经撤掉。" if res["status"] == "merged" else
            f"撤销 PR 已经是合上的了，但不是这次点击合的（可能有人先合了）：{rv['url']}。那次合并的改动已经撤掉。")
        return 0
    say(f"撤销 PR 没合上（{res.get('why') or '合并后查到的状态不是已合并'}），请到 GitHub 上看：{rv['url']}")
    return 1


def cmd_button(verb, ref, chat, card=None, now=None, sender=None, resume=False, **kw):
    """按钮入口（merge-buttons 插件后台调用）。网关回复按钮点击会失败，进度和结果都直接发到私聊。
    resume=True 是电脑上续做推翻（custody_report.py resume）：不看卡片令牌，但只续做常浩点过「推翻」、已有进度文件的。"""
    now = now or C.now_tz()
    home = home_chat()
    if not chat or chat != home:
        print(f"不处理：点击不是来自常浩私聊（{chat or '没有 chat_id'}）")
        return 1
    click = hashlib.sha1(f"{verb} {ref} {now.isoformat()} {os.getpid()}".encode()).hexdigest()[:16]
    n = [0]

    def say(content):
        if resume:
            print(content)
        try:
            (sender or send)(chat, "text", {"text": content}, f"custody-btn-{click}-{n[0]}")
            return True
        except SendError as e:
            print(f"私聊没发出去：{e}")
            return False
        finally:
            n[0] += 1

    if verb not in ("overturn", "merge_revert") or not L.DECISION_ID.match(ref or ""):
        say(f"看不懂这个按钮（{verb} {ref}）。")
        return 1
    try:
        store = L.open_store(now)
    except L.LedgerError as e:
        say(f"没处理：托管账本打不开（{e}）。")
        return 1
    try:
        with store as st:
            return _button(st, verb, ref, chat, card, now, sender, resume, say, kw)
    except ReportError as e:
        say(f"没处理：{e}")
        return 1


def _button(st, verb, ref, chat, card, now, sender, resume, say, kw):
    """按钮的核对和分派（cmd_button 打开账本后调）：推翻要核实过的汇报卡令牌（resume 要已有进度）；同一个决定同时只处理一次点击。"""
    try:
        d = L.get(st, ref)
    except L.LedgerError as e:
        say(f"没处理：{e}")
        return 1
    if verb == "overturn":
        if resume:
            if read_overturn(d) is None:
                say(f"没处理：「{d['context']}」没有进行到一半的推翻（要先在汇报卡上点「推翻」）。")
                return 1
        elif not reported(d, card):
            say(f"没处理：这个按钮不在最新一张、核对过的汇报卡上，请用最新那张（{ref}）。")
            return 1
    check_ledger(st, d["session_id"], ledger_of(d["session_id"]))          # 第 2 轮 P1-02：推翻、合并撤销 PR 都先核账本身份
    lock_path = L.raw_dir() / d["session_id"] / f".{ref}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = open(lock_path, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        say("上一次点击还在处理，等它的结果就好。")
        lock.close()
        return 1
    try:
        if verb == "overturn":
            return do_overturn(st, L.get(st, ref), say, now, chat, sender,
                               **{k: v for k, v in kw.items() if k in ("gh", "night_root", "revert", "lookup")})
        return do_merge_revert(d, say, card, kw.get("merge"))
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
    b.add_argument("--card", default="")
    sub.add_parser("resume").add_argument("ref")
    a = ap.parse_args(argv)
    if a.cmd == "button":
        sys.exit(cmd_button(a.verb, a.ref, a.chat, card=a.card))
    if a.cmd == "resume":
        sys.exit(cmd_button("overturn", a.ref, home_chat(), resume=True))
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
