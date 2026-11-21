#!/usr/bin/env python3
"""秘书汇报卡与推翻（#195 批 0B-5，规格 docs/秘书代批-规格.md 第 8 节「推翻」「汇报」）。

复用托管汇报卡（custody_report.py，#133 已审）的零件，不另写一套：乙版表格和「推翻」按钮（section / overturn_buttons）、发卡回读
（read_back / card_refs）、规则补丁候选（append_candidate）、开撤销 PR 和「合并撤销 PR」按钮卡（revert_step / deliver_revert_card /
do_merge_revert）、下游影响清单的格式和规则 3、4（custody_impact）。这些零件加了「用途」参数，默认仍是托管，托管行为不变。
秘书自己的：按天和按托管时段取决定、每行怎么写（五种动作）、推翻多出来的冻结任务、停掉在跑的轮次、改选卡、记更正。

汇报（只发常浩私聊 twin_state.owner_chat_id，回读核对，同一份只发一次）：
  day --day YYYY-MM-DD      每天 21:00 一张（白天的：那一天记账的全部秘书决定；按 Asia/Shanghai 日期，同 secretary_ledger.stats）；
  custody --session c-…     托管时段结束一张（托管排空完成或冻结后；取托管开着时记账的秘书决定）；
  alert                     立刻报：秘书冻结了、有结果不明 / 对账不清的决定，没报过的才报（一项只报一次）。
  卡片：「等你处理」（交常浩、结果不明、对账不清、还在途）和「替你定了的」（做成、没做成）两张六列表，表下「推翻」按钮
  （按钮值 {"secretary": "overturn", "ref": 决定号, "card": 卡片令牌}）。发卡前核对账本身份（每个涉及的秘书会话：库还是开启时
  登记的那一份、没有有原文没账本的决定）。记录在 DT_SECRETARY_DIR/reports/<份>.json（同 custody report.json 的结构）：
  同一份有回读核实过的就不再发（--force 才再发）；只有没核实的，先按原消息号回读，核实了不重发，核实不了才换新令牌重发。
  21:00 定时、alert 每分钟由谁调都是定时任务（D 档），本批只写命令，不装。

推翻（按钮经 merge-buttons 插件转来：button overturn <决定号> --chat --operator --card；只认常浩本人、常浩私聊、最新一张核实过的
  汇报卡上真有「推翻」按钮的决定；同一决定同时只处理一次点击）。分阶段、可恢复，进度在 DT_SECRETARY_DIR/<会话>/<决定号>.overturn.json：
    registered 登记推翻（处置改成「已推翻·已记录」）→ candidate 规则补丁候选（DT_SECRETARY_DIR/rule-candidates.jsonl，进每周提案）
    → freeze 冻结任务凭证并作废当前代次（autoloop_grants.freeze；凭证已是终态的不用）→ stop 停掉这件事排队或在跑的轮次
      （中台 POST /v1/runs/<号>/cancel，本人密钥；回读到全部终态才算完，没停下记「等停下」，再点或 resume 再查；冻结后旧代次的
      剩余动作本来就会被启动脚本和发布出口拒绝）→ impacts 下游影响清单 → 合并：revert 开撤销 PR（只开不合）→ revert_card
      「合并撤销 PR」按钮卡 → 答卡：reselect_card 发一张同样选项的改选卡（按钮 {"secretary": "reselect", "ref", "card", "o"}）
      → 其余：resolution 记更正（autoloop_grants.record_resolution，被替代的决定号和下游怎么处理）→ result 把结果发到私聊。
  只有做成、结果不明、对账不清的决定才冻结、停轮次（没做成的什么都没发生，只登记推翻和规则候选）。
  改选（button reselect <决定号> --option N）：只认最新一张核实过的改选卡，记更正（常浩改选第几项）、处置改成「已纠正」。
  恢复任务仍是常浩私聊「恢复 <单号> <冻结码>」（autoloop_grants.unfreeze，代次 +1）：更正记过才恢复得了。
  **更正事件开关 REAL_CORRECTION 关着**：常浩改选的答案现在送不到工人手里（工人只认任务单五要素，启动脚本要另加读更正的地方，
  0C 文件、另开单）；开关关着时 resume 只报告、不发「更正」事件。
  resume <决定号>：电脑上续做中断的推翻（不看卡片令牌，只续做常浩点过的）。

  secretary_report.py day [--day YYYY-MM-DD] [--force] [--card-only]
  secretary_report.py custody --session c-… [--force] [--card-only]
  secretary_report.py alert [--card-only]
  secretary_report.py button overturn|merge_revert|reselect <决定号> --chat <chat_id> --operator <open_id> [--card <令牌>] [--option N]
  secretary_report.py resume <决定号>
环境变量：DT_SECRETARY_DIR、DT_STATE_DB（同 secretary_ledger）、DT_OWNER_OPEN_ID / DT_OWNER_CHAT_ID（同 twin_state）、
DT_GRANTS_DB 等（autoloop_grants）、AGENT_AUTOLOOP_HOME（中台）、DT_PUBLISH_LOG（受信发布记录）、DT_GH（GitHub 只读）。
不调用 AI；不装定时任务。威胁模型同 custody_report：防模型自报、文本冒充、重放；同账号的恶意进程靠 ADR-015 账号隔离。
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
import autoloop_config as AC  # noqa: E402
import autoloop_grants as G  # noqa: E402
import autoloop_publish as P  # noqa: E402
import custody as C  # noqa: E402
import custody_impact as IMP  # noqa: E402
import custody_ledger as L  # noqa: E402
import custody_report as CR  # noqa: E402
import secretary as S  # noqa: E402
import secretary_ledger as SL  # noqa: E402
import twin_state as T  # noqa: E402
from tidings_weekly import SendError, send  # noqa: E402

USE = SL.SECRETARY
KEY = "secretary"                 # 按钮值的键
WHO = "秘书"
WAITING = ("handoff", "unknown", "unreconciled", "executing", "issued")
DECIDED = ("done", "failed")
EFFECTS = ("done", "unknown", "unreconciled")      # 可能已经有了效果：推翻时要冻结任务、停轮次
LIVE_RUNS = ("queued", "claimed")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CUSTODY_SID = re.compile(r"^c-\d{8}-\d{6}-[0-9a-f]{12}$")
REAL_CORRECTION = False           # 更正事件：工人侧能读到更正内容之前整体关闭（见头注释）
TEST_CORRECTION = False           # 测试在进程内打开；不经环境变量
ReportError = CR.ReportError
md = CR.md_escape


# ---------- 路径与记录 ----------
def reports_dir():
    return L.raw_dir(USE) / "reports"


def report_path(name):
    return reports_dir() / f"{name}.json"


def read_report(name):
    try:
        return json.loads(report_path(name).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"sent": []}
    except (OSError, ValueError) as e:
        raise ReportError(f"汇报记录读不了：{e}", "Corrupt")


def owner_chat():
    try:
        chat = T.owner_chat_id()
    except Exception as e:  # noqa: BLE001  没配就不发、不认任何点击
        raise ReportError(f"常浩私聊没配（{type(e).__name__}）", "NoChat")
    if not str(chat or "").startswith("oc_"):
        raise ReportError("常浩私聊没配（DT_OWNER_CHAT_ID）", "NoChat")
    return chat


# ---------- 每行怎么写（表格样式同托管乙版） ----------
def short_ref(d):
    t = d["target"] or {}
    if t.get("pr"):
        return f"{str(t.get('repo', '?')).split('/')[-1]} PR #{t['pr']}"
    if t.get("card"):
        return f"卡片 {t['card']}（任务 {t.get('task', '?')}）"
    return f"任务 {t.get('task', '?')}"


def what_happened(d):
    s, a = d["status"], d["action"]
    if a == "handoff":
        return "停在这一步，等你定"
    done = {"ask": "工人收到了这个答案，接着做", "redispatch": "已经重新派了一次，任务重新排队",
            "reject": "已在 PR 留言退回、派工人返修一轮", "merge": "已合进 main"}[a]
    return {"done": done, "failed": "没做成，什么都没发生", "unknown": "做了但没拿到结果，还在核对",
            "unreconciled": "核对了还是不确定做成没有", "executing": "正在做", "issued": "还没开始"}.get(s, "—")


def how_handled(d):
    if d["action"] == "handoff":
        return f"**没替你定**（{SL.HANDOFF_REASONS.get(d['reason'], '拿不准')}）"
    if d["status"] in ("unknown", "unreconciled"):
        return f"**{md(d['summary'])}**，但不确定做成没有"
    if d["status"] == "failed":
        return f"想{md(d['summary'])}，没做成"
    return f"**{md(d['summary'])}**"


def owner_action(d):
    t = d["target"] or {}
    if d["disposition"] != "open":
        return f"你已处理：{L.DISPOSITION[d['disposition']]}"
    if d["action"] == "handoff":
        return md(d["ask_owner"])
    if d["status"] in ("unknown", "unreconciled"):
        if d["action"] == "merge":
            return f"到 GitHub 看 PR #{t.get('pr')} 合上没有；不同意也可以点「推翻」，核清后我开撤销 PR"
        return "等我核对，不用操作；不同意可以点「推翻」"
    if d["status"] in ("executing", "issued"):
        return "等结果，不用操作"
    if d["status"] == "failed":
        return "点「推翻」：我记下以后同类事按你的来"
    return {"merge": "点「推翻」：我先冻结这张任务，再开一个撤销这次合并的 PR，你点合并才生效",
            "ask": "点「推翻」：我先冻结这张任务、停掉在跑的，再发一张卡让你重选",
            }.get(d["action"], "点「推翻」：我先冻结这张任务、停掉在跑的，再列出受影响的后续，你决定恢复还是不做")


def row_of(d, waiting):
    ask = d["question"] or {"redispatch": "（不是工人问的：中台停下了，按清单可以重派一次）",
                            "reject": "（不是工人问的：审查没过，按清单可以退回返修）",
                            "merge": "（不是工人问的：审查通过、测试全过，按清单可以直接合）"}.get(d["action"]) or "—"
    if d["action"] == "handoff":
        ask = d["question"] or "—"
    return {"what": f"**{md(d['context'])}**\n{short_ref(d)}", "ask": md(ask), "did": how_handled(d),
            "why": md(d["basis"]), "after": md(what_happened(d)), "todo": owner_action(d)}


def can_overturn(d):
    return d["action"] != "handoff" and d["disposition"] == "open" and d["status"] not in L.IN_FLIGHT + ("void",)


# ---------- 取决定 ----------
def rows_of_day(st, day):
    return [L._row(r) for r in st.conn.execute(f"SELECT * FROM {USE.decisions} WHERE substr(created_at, 1, 10) = ? "
                                               "ORDER BY created_at, rowid", (day,))]


def custody_window(session_id, doc=None):
    """托管时段（custody.json 里收尾了的这次托管）：返回 (开始, 结束)。还开着、还在排空、找不到都拒绝（收尾后才汇报）。"""
    session, _ = CR.find_session(session_id, doc)
    end = (session.get("ended") or {}).get("at") or (session.get("frozen") or {}).get("at")
    if not end:
        raise ReportError(f"托管 {session_id} 没有结束时刻", "NotEnded")
    return C.parse_iso(session["started_at"]), C.parse_iso(end)


def rows_in(st, start, end):
    out = []
    for r in st.conn.execute(f"SELECT * FROM {USE.decisions} ORDER BY created_at, rowid"):
        at = C.parse_iso(r["created_at"])
        if start <= at <= end:
            out.append(L._row(r))
    return out


def ledger_of(session_id, doc=None):
    """secretary.json 里这次秘书会话开启时记下的账本身份（现在这次或历史里的）；找不到返回 None。"""
    try:
        doc = doc or S.load()
    except ValueError as e:
        raise ReportError(f"秘书状态读不了：{e}", "Corrupt")
    for s in ([doc["session"]] if doc.get("session") else []) + list(reversed(doc.get("history") or [])):
        if s.get("id") == session_id:
            return (s.get("ledger") or {}).get("id")
    return None


def check_ledger(st, session_ids, doc=None):
    """账本身份统一核对（同 custody_report.check_ledger）：每个涉及的秘书会话都登记过、库还是开启时那一份、没有有原文没账本的决定。"""
    for sid in sorted(set(session_ids)):
        try:
            L.check_session(st, sid, ledger_of(sid, doc), USE)
        except L.LedgerError as e:
            raise ReportError(f"秘书账本对不上，不处理：{e}", e.code)
        orphans = L.raw_orphans(st, sid, USE)
        if orphans:
            raise ReportError(f"秘书账本对不上，不处理：{len(orphans)} 个决定有原文、账本里却没有（账本可能被旧备份覆盖）",
                              "LedgerMismatch")


# ---------- 卡片 ----------
def build_card(title, lead, rows, token=None, mark="", frozen_note=""):
    waiting = [d for d in rows if d["status"] in WAITING or d["action"] == "handoff"]
    decided = [d for d in rows if d not in waiting and d["status"] in DECIDED]
    els = [{"tag": "markdown", "content": lead}]
    if frozen_note:
        els.append({"tag": "markdown", "content": frozen_note})
    sec = {"token": token, "key": KEY, "row_of": row_of, "can": can_overturn, "lister": "secretary_ledger.py list"}
    if waiting:
        els += CR.section("等你处理", waiting, True, **sec)
    if decided:
        els.append({"tag": "hr"})
        els += CR.section("替你定了的", decided, False, **sec)
    foot = "点「推翻」后，我会冻结那张任务、停掉在跑的，记下以后同类事按你的意思办，并告诉你哪些后续受影响；撤销合并要你再点一次合并才生效。"
    els += [{"tag": "hr"}, {"tag": "markdown", "content": f"<font color='grey'>{md(foot)}</font>"},
            {"tag": "markdown", "content": f"<font color='grey'>编号 {mark}" + (f" · {token}" if token else "") + "</font>"}]
    card = {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": title}, "template": "red" if frozen_note else "blue"},
            "body": {"elements": els}}
    return CR.fit(card)


def lead_text(rows, when):
    done = [d for d in rows if d["action"] != "handoff" and d["status"] == "done"]
    n = {a: sum(1 for d in done if d["action"] == a) for a in ("ask", "redispatch", "reject", "merge")}
    hand = [d for d in rows if d["action"] == "handoff"]
    if not rows:
        return f"<font color='grey'>{when}</font>\n秘书这段时间没替你做任何决定，也没有交给你的事。"
    text = (f"<font color='grey'>{when}</font>\n秘书按你签的代批清单，替你答了 {n['ask']} 张卡、重派 {n['redispatch']} 次、"
            f"退回 {n['reject']} 个 PR、合并 {n['merge']} 个 PR")
    return text + (f"；交给你 {len(hand)} 件，见「等你处理」。" if hand else "；没有交给你的事。")


def frozen_note(doc=None):
    try:
        doc = doc or S.load()
    except ValueError:
        return "**秘书状态读不了**：在电脑上看 secretary.py status。"
    if doc.get("state") != "frozen":
        return ""
    f = (doc.get("session") or {}).get("frozen") or {}
    return (f"**秘书已经冻结**（{md(f.get('reason') or '原因见电脑')}）：不再替你做新决定。你核对清楚后，在私聊说「解除冻结」。")


def scope(kind, arg=None, st=None, doc=None, now=None):
    """一份汇报取哪些决定：返回 (份名, 标题, 开头一句, 决定列表, 冻结说明)。"""
    now = now or C.now_tz()
    if kind == "day":
        day = arg or now.astimezone(C.TZ).date().isoformat()
        if not DAY.match(day):
            raise ReportError(f"日期要写成 YYYY-MM-DD：{day}", "BadInput")
        rows = rows_of_day(st, day)
        waiting = sum(1 for d in rows if d["status"] in WAITING or d["action"] == "handoff")
        decided = sum(1 for d in rows if d["action"] != "handoff" and d["status"] in DECIDED)
        return (f"day-{day}", f"📋 秘书今天：替你定了 {decided} 件，{waiting} 件等你处理", lead_text(rows, f"{day} 白天"),
                rows, frozen_note(doc))
    if kind == "custody":
        if not CUSTODY_SID.match(arg or ""):
            raise ReportError(f"托管号不对：{arg}", "BadInput")
        start, end = custody_window(arg)
        rows = rows_in(st, start, end)
        when = start.astimezone(C.TZ).strftime("%m-%d %H:%M") + " – " + end.astimezone(C.TZ).strftime("%m-%d %H:%M") + " 托管时段"
        return (f"custody-{arg}", f"🌙 托管时段秘书替你定了 {sum(1 for d in rows if d['action'] != 'handoff')} 件",
                lead_text(rows, when), rows, frozen_note(doc))
    raise ReportError(f"不认识的汇报：{kind}", "BadInput")


def reported(d, card):
    """这个「推翻」按钮是不是在某一份汇报最新一张核实过的卡上（同 custody_report.reported：重发后旧卡不认）。"""
    if not CR.CARD_TOKEN.match(card or ""):
        return False
    try:
        files = sorted(reports_dir().glob("*.json"))
    except OSError:
        return False
    for f in files:
        try:
            sent = json.loads(f.read_text(encoding="utf-8")).get("sent") or []
        except (OSError, ValueError):
            continue
        verified = [x for x in sent if x.get("status") == "verified"]
        if verified and verified[-1].get("card") == card and d["id"] in (verified[-1].get("actionable") or []):
            return True
    return False


def _write(name, rec):
    reports_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    CR.write_json(report_path(name), rec)


def deliver(name, card_of, rows, force=False, now=None, sender=None, reader=None, extra=None):
    """发一份汇报并回读（同 custody_report.send_report 的做法）。card_of(token) 给出卡片；extra 并进记录条目。返回记录条目。"""
    now = now or C.now_tz()
    chat = owner_chat()
    reader = reader or CR.read_back
    rec = read_report(name)
    if not force:
        if any(x.get("status") == "verified" for x in rec["sent"]):
            return {"unchanged": True, "name": name}
        last = next((x for x in reversed(rec["sent"]) if x.get("message_id")), None)
        if last:
            ok, why = reader(last["message_id"], [name, last.get("card") or "（没有令牌）"], "推翻：",
                             len(last.get("actionable") or []))
            if ok:
                last.update(status="verified", verified_at=C.iso(now))
                last.pop("error", None)
                _write(name, rec)
                return {**last, "rechecked": True}
            last["recheck"] = why
    token = secrets.token_hex(8)
    card = card_of(token)
    entry = {"at": C.iso(now), "card": token, "decisions": [d["id"] for d in rows],
             "actionable": CR.card_refs(card, "overturn", KEY), "message_id": None, "status": "unverified", **(extra or {})}
    rec["sent"].append(entry)
    try:
        entry["message_id"] = (sender or send)(chat, "interactive", card, f"secretary-{hashlib.sha1(name.encode()).hexdigest()[:12]}-{token}")
        if not entry["message_id"]:
            raise SendError("发卡片没拿到消息号")
    except SendError as e:
        entry["error"] = str(e)
    _write(name, rec)                                         # 发出去了先记下消息号，回读失败下次还能按它核实
    if entry["message_id"]:
        ok, why = reader(entry["message_id"], [name, token], "推翻：", len(entry["actionable"]))
        entry["status"] = "verified" if ok else "unverified"
        if not ok:
            entry["error"] = why
        _write(name, rec)
    if entry.get("error"):
        raise ReportError(entry["error"] + ("" if entry["message_id"] else "（没发出去）"), "SendFailed")
    return entry


def send_report(kind, arg=None, force=False, now=None, sender=None, reader=None, card_only=False, store=None, doc=None):
    now = now or C.now_tz()
    try:
        cm = contextlib.nullcontext(store) if store else SL.open_store(now)
    except L.LedgerError as e:
        raise ReportError(f"秘书账本打不开，不汇报：{e}", e.code)
    with cm as st:
        name, title, lead, rows, note = scope(kind, arg, st, doc, now)
        check_ledger(st, [d["session_id"] for d in rows], doc)
        if card_only:
            return {"name": name, "card": build_card(title, lead, rows, None, name, note), "decisions": len(rows)}
        return deliver(name, lambda tok: build_card(title, lead, rows, tok, name, note), rows, force, now, sender, reader)


def alerted():
    """已经在核实过的立刻报里报过的事项（每份立刻报 alert-<摘要>.json 的 items）。"""
    told = set()
    for f in sorted(reports_dir().glob("alert-*.json")) if reports_dir().is_dir() else []:
        try:
            sent = json.loads(f.read_text(encoding="utf-8")).get("sent") or []
        except (OSError, ValueError):
            continue
        told |= {i for x in sent if x.get("status") == "verified" for i in x.get("items") or []}
    return told


def send_alert(now=None, sender=None, reader=None, card_only=False, store=None, doc=None):
    """立刻报：秘书冻结、结果不明 / 对账不清的决定。没在核实过的立刻报里出现过的才报（一项只报一次）；没有要报的返回 None。"""
    now = now or C.now_tz()
    try:
        doc = doc or S.load()
    except ValueError as e:
        raise ReportError(f"秘书状态读不了：{e}", "Corrupt")
    with (contextlib.nullcontext(store) if store else SL.open_store(now)) as st:
        rows = [L._row(r) for r in st.conn.execute(f"SELECT * FROM {USE.decisions} WHERE status IN ('unknown','unreconciled') "
                                                   "ORDER BY created_at, rowid")]
        f = (doc.get("session") or {}).get("frozen") if doc.get("state") == "frozen" else None
        items = [f"{d['id']}:{d['status']}" for d in rows] + ([f"frozen:{doc['session']['id']}:{f.get('at')}"] if f else [])
        told = alerted()
        new = [i for i in items if i not in told]
        if not new:
            return None
        check_ledger(st, [d["session_id"] for d in rows], doc)
        show = [d for d in rows if f"{d['id']}:{d['status']}" in new]
        title = "⚠️ 秘书：有事要你马上知道"
        lead = (f"<font color='grey'>{now.astimezone(C.TZ).strftime('%m-%d %H:%M')}</font>\n"
                + (f"有 {len(show)} 件秘书做了但不确定做成没有，相关的任务和仓库先不再代批，核对清楚前不再替你做。" if show
                   else "秘书冻结了。"))
        note = frozen_note(doc) if f else ""
        name = "alert-" + hashlib.sha1("\n".join(sorted(new)).encode()).hexdigest()[:12]
        if card_only:
            return {"name": name, "card": build_card(title, lead, show, None, name, note), "items": new}
        return deliver(name, lambda tok: build_card(title, lead, show, tok, name, note), show, False, now, sender, reader,
                       extra={"items": new})


# ---------- 推翻 ----------
def hub_live_runs(task_id):
    """中台库只读：这件事（事项 = 任务号，所有规则）还在排队或在跑的轮次。读不了返回 None。"""
    try:
        con = P._hub()
    except Exception:  # noqa: BLE001
        return None
    try:
        return [dict(r) for r in con.execute(f"SELECT id, state FROM runs WHERE lane = ? AND state IN "
                                             f"({','.join('?' * len(LIVE_RUNS))}) ORDER BY id", (task_id, *LIVE_RUNS))]
    except Exception:  # noqa: BLE001
        return None
    finally:
        con.close()


def freeze_step(d, rec, now):
    """冻结任务凭证、作废当前代次（规格第 8 节推翻第 1 步）。凭证已经是终态（撤销、完成）就不用冻。返回这一步做完没有。"""
    task = d["target"]["task"]
    try:
        out = G.freeze(task, f"常浩推翻秘书决定 {d['id']}", now=now)
    except G.Rejected as e:
        if "终态" in str(e):
            rec["freeze"] = {"status": "not_needed", "why": str(e)[:200]}
            return True
        rec["freeze"] = {"status": "failed", "why": str(e)[:200]}
        return False
    except Exception as e:  # noqa: BLE001  凭证库打不开等：下次再试
        rec["freeze"] = {"status": "failed", "why": f"{type(e).__name__}：{e}"[:200]}
        return False
    rec["freeze"] = {"status": "frozen", "task": task, "generation": out["generation"], "code": out.get("freeze_code"),
                     "already": bool(out.get("already")), **({"mirror_error": out["mirror_error"]} if out.get("mirror_error") else {})}
    return True


def stop_step(d, rec, cancel=None):
    """停掉这件事还在排队或在跑的轮次（第 2 步）：本人密钥调中台取消，回读到全部终态才算完。返回做完没有。"""
    task = d["target"]["task"]
    live = hub_live_runs(task)
    if live is None:
        rec["stop"] = {"status": "unknown", "why": "中台库读不了，不知道还有没有在跑的轮次"}
        return False
    asked = list((rec.get("stop") or {}).get("asked") or [])
    errors = []
    for r in live:
        if r["id"] in asked:
            continue
        try:
            (cancel or (lambda rid: AC.hub("POST", f"/v1/runs/{rid}/cancel")))(r["id"])
            asked.append(r["id"])
        except Exception as e:  # noqa: BLE001  已经结束的会回 400：下面回读为准
            errors.append(f"轮次 {r['id']}：{getattr(e, 'args', [e])[0]}"[:120])
            asked.append(r["id"])
    left = hub_live_runs(task)
    if left is None:
        rec["stop"] = {"status": "unknown", "asked": asked, "why": "取消后中台库读不了"}
        return False
    rec["stop"] = {"status": "stopped" if not left else "stopping", "asked": asked, "left": [r["id"] for r in left],
                   **({"errors": errors} if errors else {})}
    return not left


def impacts_of(st, d, gh=None):
    """下游影响清单（第 3 步，格式和规则名同 custody_impact）：合并用它的规则 3（之后的合并和引用）+ 4；其余：
    规则 1 这张任务在决定之后的轮次（中台库）、规则 2 之后受信发布开 / 推的 PR、规则 4 依据它做的其他秘书决定。"""
    if d["action"] == "merge":
        return IMP.impacts(st, d, gh=gh, use=USE)
    items, gaps = [], []
    since = C.parse_iso(d["created_at"])
    task = d["target"]["task"]
    try:
        con = P._hub()
        try:
            runs = [dict(r) for r in con.execute("SELECT id, state, created_at FROM runs WHERE lane = ? ORDER BY id", (task,))]
        finally:
            con.close()
        for r in runs:
            at = IMP.parse_time(r.get("created_at"))
            if at and at > since:
                items.append({"rule": 1, "what": f"轮次 {r['id']}（{r['state']}）", "ref": str(r["id"]), "url": ""})
    except Exception as e:  # noqa: BLE001
        gaps.append({"rule": 1, "why": f"中台库读不了（{type(e).__name__}）"})
        gaps.append({"rule": 2, "why": "规则 1 没取到轮次，PR 只按受信发布记录列"})
    try:
        for line in P.log_path().read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                gaps.append({"rule": 2, "why": "受信发布记录有坏行，可能漏列"})
                continue
            at = IMP.parse_time(e.get("at")) if isinstance(e, dict) else None
            if at and at > since and e.get("task_id") == task and e.get("what") == "发布" and e.get("pr"):
                ref = str(e["pr"])
                if not any(i["ref"] == ref for i in items):
                    items.append({"rule": 2, "what": f"PR {ref.rsplit('/', 1)[-1]}（{str(e.get('head') or '')[:7]}）", "ref": ref,
                                  "url": ref})
    except FileNotFoundError:
        pass
    except OSError as e:
        gaps.append({"rule": 2, "why": f"受信发布记录读不了（{type(e).__name__}）"})
    for x, _ in IMP.descendants(st, d["id"], USE):
        items.append({"rule": 4, "what": f"「{x['context'] or SL.describe_target(x)}」：{x['summary'] or '没替你定'}（{x['id']}）",
                      "ref": x["id"], "url": ""})
    return {"items": items, "gaps": gaps}


def options_of(d):
    raw = L.read_raw(d) or {}
    opts = ((raw.get("received") or {}).get("options")) or []
    return [str(o) for o in opts] if isinstance(opts, list) else []


def reselect_card(d, opts, token):
    old = ((L.read_raw(d) or {}).get("reply") or {}).get("choice")
    lines = [f"**重选：{md(d['context'])}**（{short_ref(d)}）",
             f"工人问的是：{md(d['question'] or '—')}", f"秘书当时选的是第 {old} 项。你点一个："]
    btns = [{"tag": "column", "width": "auto", "elements": [
        {"tag": "button", "text": {"tag": "plain_text", "content": f"改选：{CR.clip(o, 14)}"}, "type": "primary",
         "size": "small", "behaviors": [{"type": "callback", "value": {KEY: "reselect", "ref": d["id"], "card": token, "o": i}}]}]}
        for i, o in enumerate(opts, 1)]
    return {"schema": "2.0", "header": {"title": {"tag": "plain_text", "content": f"↩️ 重选秘书替你答的卡：{CR.clip(d['context'], 16)}"},
                                        "template": "orange"},
            "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)},
                                  {"tag": "column_set", "flex_mode": "flow", "horizontal_spacing": "8px", "columns": btns},
                                  {"tag": "markdown", "content": f"<font color='grey'>编号 {d['id']} · {token}</font>"}]}}


def deliver_reselect(d, rec, save, now, chat, sender=None, reader=None):
    """发改选卡并回读（同 deliver_revert_card：上次发了没核实的先按原消息号回读，核实不了才换新令牌重发）。"""
    opts = options_of(d)
    reader = reader or CR.read_back
    if not opts:
        rec["reselect_card"] = {"status": "failed", "error": "原文里没有卡片的选项，发不了改选卡"}
        return False
    rc = rec.get("reselect_card") or {}
    if rc.get("message_id") and rc.get("status") != "verified":
        good, _ = reader(rc["message_id"], [d["id"], rc.get("card") or "（没有令牌）"], "改选：", len(opts))
        if good:
            rc["status"] = "verified"
            rec["reselect_card"] = rc
            return True
    token = secrets.token_hex(8)
    rc = rec["reselect_card"] = {"card": token, "at": C.iso(now), "message_id": None, "status": "unverified", "options": len(opts)}
    save()
    try:
        rc["message_id"] = (sender or send)(chat, "interactive", reselect_card(d, opts, token), f"secretary-reselect-{token}")
    except SendError as e:
        rc["error"] = str(e)
        return False
    save()
    if not rc["message_id"]:
        rc["error"] = "发卡片没拿到消息号"
        return False
    good, why = reader(rc["message_id"], [d["id"], token], "改选：", len(opts))
    rc["status"] = "verified" if good else "unverified"
    if not good:
        rc["error"] = why
    return good


def resolution_text(d, rec):
    rv = rec.get("revert") or {}
    if d["action"] == "merge":
        down = (f"开了撤销 PR {rv.get('url')}，常浩点合并才生效" if rv.get("status") == "opened" else
                "合并没合上，不用撤销" if rv.get("status") == "not_needed" else f"撤销 PR 还没开成（{rv.get('status')}）")
    else:
        down = "之后的提交、PR、轮次保留，由常浩决定恢复还是撤销这张任务"
    return f"常浩推翻秘书决定（{SL.ACTIONS[d['action']]}：{CR.clip(d['summary'], 20)}）；下游：{down}"[:500]


def resolution_step(d, rec, now, text=None):
    """记更正（第 4 步「更正先持久记在账本」，恢复任务的前提）。这次冻结已经记过（同一任务另一个决定推翻时记的）也算做完。"""
    fz = rec.get("freeze") or {}
    if fz.get("status") != "frozen":
        rec["resolution"] = {"status": "not_needed", "why": "这张任务没有冻结（凭证已是终态）"}
        return True
    try:
        G.record_resolution(fz["task"], fz["generation"], d["id"], text or resolution_text(d, rec), now=now)
        rec["resolution"] = {"status": "recorded", "at": C.iso(now), "text": text or resolution_text(d, rec)}
        return True
    except G.Rejected as e:
        if "已经记过更正" in str(e):
            rec["resolution"] = {"status": "recorded_before", "why": str(e)[:200]}
            return True
        rec["resolution"] = {"status": "failed", "why": str(e)[:200]}
        return False
    except Exception as e:  # noqa: BLE001
        rec["resolution"] = {"status": "failed", "why": f"{type(e).__name__}：{e}"[:200]}
        return False


def grant_ro(task_id):
    """凭证库只读读一张凭证（不经 autoloop_grants.tx：那会建库）；读不了返回 None。"""
    import sqlite3
    import urllib.parse
    try:
        con = sqlite3.connect(f"file:{urllib.parse.quote(str(G.db_path()))}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    con.row_factory = sqlite3.Row
    try:
        r = con.execute("SELECT * FROM grants WHERE task_id = ?", (task_id,)).fetchone()
        return dict(r) if r else None
    except sqlite3.Error:
        return None
    finally:
        con.close()


def correction_step(d, rec, now):
    """恢复之后发「更正」事件（第 4 步最后）：只给答卡类、改选记过、任务已经恢复（有效、代次比冻结时大）。开关关着只报告。"""
    fz, rs = rec.get("freeze") or {}, rec.get("reselected")
    if d["action"] != "ask" or not rs or fz.get("status") != "frozen" or (rec.get("correction") or {}).get("status") == "sent":
        return
    row = grant_ro(fz["task"])
    if not row or row["status"] != "active" or row["generation"] <= fz["generation"]:
        return
    if not (REAL_CORRECTION or TEST_CORRECTION):
        rec["correction"] = {"status": "switch_off", "why": "工人侧还读不到更正内容，更正事件开关关着"}
        return
    try:
        out = G.resume_dispatch(fz["task"], "correction", f"常浩改选第 {rs['o']} 项（替代 {d['id']}）", now=now)
        rec["correction"] = {"status": "sent", "event_key": out["event_key"], "at": C.iso(now)}
    except Exception as e:  # noqa: BLE001
        rec["correction"] = {"status": "failed", "why": f"{type(e).__name__}：{e}"[:200]}


def overturn_message(d, rec):
    msg = [f"已推翻：{d['context']}（{short_ref(d)}），秘书当时定的是「{d['summary']}」。记下了，以后同类事按你的意思办。"]
    fz, sp = rec.get("freeze") or {}, rec.get("stop") or {}
    if fz.get("status") == "frozen":
        msg.append(f"任务 {fz['task']} 已冻结（第 {fz['generation']} 代作废）：新的轮次起不来、不再推分支开 PR，秘书也不再替它做决定。")
    elif fz.get("status") == "failed":
        msg.append(f"任务冻结没成功（{fz.get('why')}），再点一次「推翻」会重试；在那之前它还可能接着跑。")
    if sp.get("status") == "stopped" and sp.get("asked"):
        msg.append(f"在排队或在跑的 {len(sp['asked'])} 个轮次已停下。")
    elif sp.get("status") in ("stopping", "unknown"):
        msg.append(f"还有轮次没确认停下（{sp.get('why') or '、'.join(map(str, sp.get('left') or []))}）；冻结后它们剩下的推送、开 PR 会被拒，"
                   "再点一次「推翻」我会再查。")
    if "impacts" in rec:
        msg += ["受影响的后续：", *IMP.describe(rec["impacts"])]
    if d["action"] == "merge":
        rv, rc = rec.get("revert") or {}, rec.get("revert_card") or {}
        s = rv.get("status")
        msg.append({"opened": f"已开撤销 PR：{rv.get('url')}；" + ("要你点上面卡片里的「合并撤销 PR」才生效。" if rc.get("status") == "verified"
                                                              else "带「合并撤销 PR」按钮的卡片没发成，再点一次「推翻」我会重发。"),
                    "waiting": "这次合并还没核清合上没有，撤销 PR 等核清再开：核清后再点一次「推翻」。",
                    "not_needed": "这次合并没合上，不用撤销。",
                    "unsure": f"撤销 PR 开没开成不确定（{rv.get('why')}）；我不会再推一次，再点「推翻」我只去 GitHub 上查。"}.get(
            s, f"撤销 PR 没开成（{rv.get('why') or '原因不明'}），原因解决后再点一次「推翻」会重试。"))
    if d["action"] == "ask" and d["status"] in EFFECTS:
        if (rec.get("reselect_card") or {}).get("status") == "verified":
            msg.append("我发了一张同样选项的卡，你点一个就记下你的选择。注意：现在工人还收不到改选的答案（工人那边还不支持更正），"
                       "恢复后它会按冻结前的状态接着做；在那之前建议改选后先别恢复。")
        else:
            msg.append("改选卡没发成，再点一次「推翻」我会重发。")
    if fz.get("status") == "frozen" and fz.get("code"):
        if d["action"] != "ask" or rec.get("reselected"):
            msg.append(f"要让它接着做：私聊发「恢复 {fz['task']} {fz['code']}」（代次加 1）；不做了就跟我说，我撤销这张任务。")
    return "\n".join(msg)


def finished(d, rec):
    st = rec["stages"]
    need = ["registered", "candidate", "impacts", "result"]
    if d["status"] in EFFECTS:
        need += ["freeze", "stop"]
        if d["action"] == "merge":
            need.append("revert_card" if (rec.get("revert") or {}).get("status") == "opened" else "revert")
        need.append("reselect_card" if d["action"] == "ask" else "resolution")
    return all(k in st for k in need)


def do_overturn(st, d, say, now, chat, sender=None, gh=None, revert=None, lookup=None, cancel=None, reader=None):
    """推翻（分阶段、可恢复；阶段见头注释）。中断后再点「推翻」或电脑上 resume，只补做没完成的阶段。返回退出码。"""
    rec = CR.read_overturn(d, USE)
    resumed = rec is not None
    if rec is None:
        if d["disposition"] != "open":
            say(f"「{d['context']}」这件你已经处理过（{L.DISPOSITION[d['disposition']]}），不用再推翻。")
            return 0
        rec = {"decision": d["id"], "at": C.iso(now), "stages": {}}
    stages = rec["stages"]

    def save():
        CR.write_json(CR.overturn_path(d, USE), rec)

    if "registered" not in stages:
        save()                                                # 先落进度文件，再改账本：中断后能从这里续做
        if d["disposition"] == "open":
            try:
                d = L.set_disposition(st, d["id"], "overturned", "常浩在秘书汇报卡点「推翻」", by="user", now=now, use=USE)
            except L.LedgerError as e:
                say(f"没推翻「{d['context']}」：{e}")
                return 1
        stages["registered"] = C.iso(now)
        save()
    elif finished(d, rec) and not rec.get("reselected"):
        say(f"「{d['context']}」你已经推翻过了（{L.DISPOSITION[d['disposition']]}），不用再推翻。")
        return 0
    if "candidate" not in stages:
        CR.append_candidate(d, now, USE, by="常浩（秘书汇报卡「推翻」）")
        stages["candidate"] = C.iso(now)
        save()
    ok = True
    effects = d["status"] in EFFECTS
    if effects and "freeze" not in stages:
        if freeze_step(d, rec, now):
            stages["freeze"] = C.iso(now)
        else:
            ok = False
        save()
    if effects and "stop" not in stages:
        if stop_step(d, rec, cancel):
            stages["stop"] = C.iso(now)
        else:
            ok = False
        save()
    if "impacts" not in stages:
        try:
            rec["impacts"] = impacts_of(st, d, gh)
        except Exception as e:  # noqa: BLE001  清单出错不能吞掉推翻本身：照样记下，说明没列全
            rec["impacts"] = {"items": [], "gaps": [{"rule": r, "why": f"清单没算出来（{type(e).__name__}：{e}）"} for r in IMP.RULES]}
        stages["impacts"] = C.iso(now)
        save()
    if effects and d["action"] == "merge":
        if "revert" not in stages:
            if CR.revert_step(st, d, rec, save, now, revert, lookup, use=USE, who=WHO):
                stages["revert"] = C.iso(now)
            save()
            d = L.get(st, d["id"], USE)
        if (rec.get("revert") or {}).get("status") == "opened" and "revert_card" not in stages:
            if CR.deliver_revert_card(d, rec, save, now, chat, sender, key=KEY, who=WHO):
                stages["revert_card"] = C.iso(now)
            save()
        rs = (rec.get("revert") or {}).get("status")
        ok = ok and rs in ("opened", "waiting", "not_needed") and (rs != "opened" or "revert_card" in stages)
    if effects and d["action"] == "ask" and "reselect_card" not in stages and "freeze" in stages:
        if deliver_reselect(d, rec, save, now, chat, sender, reader):
            stages["reselect_card"] = C.iso(now)
        else:
            ok = False
        save()
    if effects and d["action"] != "ask" and "resolution" not in stages and "freeze" in stages \
            and (d["action"] != "merge" or (rec.get("revert") or {}).get("status") in ("opened", "not_needed")):
        if resolution_step(d, rec, now):
            stages["resolution"] = C.iso(now)
        else:
            ok = False
        save()
    correction_step(d, rec, now)
    sig = [stages.get(k) is not None for k in ("freeze", "stop", "revert", "revert_card", "reselect_card", "resolution")]
    sig.append((rec.get("correction") or {}).get("status"))
    if (stages.get("result") or {}).get("sig") != sig or resumed:
        if say(overturn_message(d, rec)):
            stages["result"] = {"at": C.iso(now), "sig": sig}
        else:
            ok = False
        save()
    return 0 if ok else 1


def do_reselect(st, d, say, card, option, now):
    """常浩点改选卡上的一项：只认最新一张核实过的改选卡；记更正（被替代的决定号、改选第几项），处置改成「已纠正」。"""
    rec = CR.read_overturn(d, USE) or {}
    rc, fz = rec.get("reselect_card") or {}, rec.get("freeze") or {}
    opts = options_of(d)
    if not CR.CARD_TOKEN.match(card or "") or card != rc.get("card") or rc.get("status") != "verified":
        say("没记下：这个按钮不是最新一张改选卡上的，请用最新那张。")
        return 1
    if type(option) is not int or not 1 <= option <= len(opts):
        say("没记下：选项号不在卡片的选项里。")
        return 1
    if rec.get("reselected"):
        say(f"你已经改选过了（第 {rec['reselected']['o']} 项），不用再选。")
        return 0
    if d["disposition"] != "overturned" or fz.get("status") != "frozen":
        say(f"没记下：「{d['context']}」现在不在等你改选的状态。")
        return 1
    text = f"常浩改选第 {option} 项「{CR.clip(opts[option - 1], 40)}」（替代秘书决定 {d['id']}）"
    if not resolution_step(d, rec, now, text):
        CR.write_json(CR.overturn_path(d, USE), rec)
        say(f"没记下：更正没写进凭证库（{(rec.get('resolution') or {}).get('why')}），再点一次会重试。")
        return 1
    rec["reselected"] = {"o": option, "at": C.iso(now)}
    rec["stages"]["resolution"] = C.iso(now)
    CR.write_json(CR.overturn_path(d, USE), rec)
    with contextlib.suppress(L.LedgerError):
        L.set_disposition(st, d["id"], "corrected", text, by="user", now=now, use=USE)
    say(f"记下了：你改选第 {option} 项「{opts[option - 1]}」。要让任务接着做，私聊发「恢复 {fz['task']} {fz['code']}」；"
        "注意现在工人还收不到改选的答案（工人那边还不支持更正），恢复后它会按冻结前的状态接着做。")
    return 0


# ---------- 按钮 ----------
def cmd_button(verb, ref, chat, operator="", card=None, option=None, now=None, sender=None, resume=False, **kw):
    """按钮入口（merge-buttons 插件后台调用；网关回复按钮点击会失败，结果都直接发到私聊）。只认常浩本人、常浩私聊。
    resume=True 是电脑上续做推翻：不看令牌和点击人，只续做常浩点过「推翻」、已有进度的。"""
    now = now or C.now_tz()
    try:
        home = owner_chat()
        owner = T.owner_open_id()
    except Exception as e:  # noqa: BLE001
        print(f"不处理：{e}")
        return 1
    if not chat or chat != home or (not resume and (not operator or operator != owner)):
        print(f"不处理：点击不是常浩本人在私聊里点的（{chat or '没有 chat_id'}）")
        return 1
    click = hashlib.sha1(f"{verb} {ref} {now.isoformat()} {os.getpid()}".encode()).hexdigest()[:16]
    n = [0]

    def say(content):
        if resume:
            print(content)
        try:
            (sender or send)(chat, "text", {"text": content}, f"secretary-btn-{click}-{n[0]}")
            return True
        except SendError as e:
            print(f"私聊没发出去：{e}")
            return False
        finally:
            n[0] += 1

    if verb not in ("overturn", "merge_revert", "reselect") or not L.DECISION_ID.match(ref or ""):
        say(f"看不懂这个按钮（{verb} {ref}）。")
        return 1
    try:
        store = SL.open_store(now)
    except L.LedgerError as e:
        say(f"没处理：秘书账本打不开（{e}）。")
        return 1
    try:
        with store as st:
            return _button(st, verb, ref, chat, card, option, now, sender, resume, say, kw)
    except ReportError as e:
        say(f"没处理：{e}")
        return 1


def _button(st, verb, ref, chat, card, option, now, sender, resume, say, kw):
    try:
        d = L.get(st, ref, USE)
    except L.LedgerError as e:
        say(f"没处理：{e}")
        return 1
    if verb == "overturn":
        if resume:
            if CR.read_overturn(d, USE) is None:
                say(f"没处理：「{d['context']}」没有进行到一半的推翻（要先在汇报卡上点「推翻」）。")
                return 1
        elif not reported(d, card):
            say(f"没处理：这个按钮不在最新一张、核对过的秘书汇报卡上，请用最新那张（{ref}）。")
            return 1
    check_ledger(st, [d["session_id"]])
    lock_path = L.raw_dir(USE) / d["session_id"] / f".{ref}.lock"
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
            return do_overturn(st, L.get(st, ref, USE), say, now, chat, sender,
                               **{k: v for k, v in kw.items() if k in ("gh", "revert", "lookup", "cancel", "reader")})
        if verb == "reselect":
            return do_reselect(st, L.get(st, ref, USE), say, card, option, now)
        return CR.do_merge_revert(d, say, card, kw.get("merge"), use=USE)
    finally:
        lock.close()


# ---------- 命令行 ----------
def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False, default=str))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("day")
    s.add_argument("--day")
    s.add_argument("--force", action="store_true")
    s.add_argument("--card-only", action="store_true")
    s = sub.add_parser("custody")
    s.add_argument("--session", required=True)
    s.add_argument("--force", action="store_true")
    s.add_argument("--card-only", action="store_true")
    sub.add_parser("alert").add_argument("--card-only", action="store_true")
    b = sub.add_parser("button")
    b.add_argument("verb", choices=("overturn", "merge_revert", "reselect"))
    b.add_argument("ref")
    b.add_argument("--chat", default="")
    b.add_argument("--operator", default="")
    b.add_argument("--card", default="")
    b.add_argument("--option", type=int)
    sub.add_parser("resume").add_argument("ref")
    a = ap.parse_args(argv)
    if a.cmd == "button":
        return cmd_button(a.verb, a.ref, a.chat, a.operator, card=a.card, option=a.option)
    if a.cmd == "resume":
        try:
            chat = owner_chat()
        except ReportError as e:
            return done(False, str(e), error=e.code)
        return cmd_button("overturn", a.ref, chat, resume=True)
    try:
        if a.cmd == "alert":
            r = send_alert(card_only=a.card_only)
            if r is None:
                return done(True, "没有要立刻报的")
        else:
            r = send_report(a.cmd, a.day if a.cmd == "day" else a.session, force=a.force, card_only=a.card_only)
        if a.card_only:
            print(json.dumps(r["card"], ensure_ascii=False, indent=1))
            return done(True, f"{r['name']}（没发）")
        return done(True, "这一份已经发过了" if r.get("unchanged") else f"已发并回读（{r.get('message_id')}）",
                    **{k: v for k, v in r.items() if k != "card"})
    except (ReportError, L.LedgerError) as e:
        return done(False, str(e), error=getattr(e, "code", type(e).__name__))


if __name__ == "__main__":
    sys.exit(main())
