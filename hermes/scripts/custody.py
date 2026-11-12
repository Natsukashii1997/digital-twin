#!/usr/bin/env python3
"""托管状态底座（#131）：托管开没开、谁开的、按哪份已签策略开的，只有这里一处真源。

依据：托管模式规格 §3.7、§3.8；ARCHITECTURE 5.7 第 2、9 条；ADR-014（含修订 1）；常设授权清单第 6 节。
本脚本只管状态、开关来源和策略签署，**不让托管生效**：落地工单验收、常浩签策略之前，任何闸都不按托管放行。
不发飞书、不装定时任务、不碰驿窗和 webhook；状态文件只有本脚本写。

用法（最后一行都是一行 JSON：{"ok", "msg", ...}；通过退出码 0，拒绝退出码 1）：
  custody.py status [--json]                     现在的托管状态 + 策略核验 + 准入判定（只读；--json 给机器读，不带 open_id）
  custody.py verify                               重算策略全文 SHA-256，和签署记录比对（只读）
  custody.py on --source feishu --message-id om_x 常浩私聊说「托管」或「托管到 X 点」开启
  custody.py on --source window                   已签策略的默认时段内开启（时段内只开一次）
  custody.py off --source <标签> [--skip-tonight]  关闭：on → draining；任何来源都可以（标签只是记录，自报、不核验）
  custody.py off --source <标签> --drained        排空完成：draining → off；要本脚本自己能证明排空完成（drain_proof），
                                                  账本（#133）接入前一律拒绝（DrainUnverified），排空到截止自动冻结
  custody.py freeze --source <标签> --reason 原因 [--item 未解决事项 ...]   转 frozen（on / draining）；已冻结时只补充事项
  custody.py unfreeze --message-id om_x          常浩私聊说「解除冻结」：frozen → off（只有这一条路；解除不等于重新开启）
  custody.py sign --message-id om_x              常浩私聊说「签托管策略 v<版本> <SHA-256 前 12 位>」签 policy.md
  custody.py revoke --source <标签> [--reason 原因]  撤销当前签署（持久记下，恢复原文件也不再生效）；托管开着就转排空
Hermes 会话里 --message-id 可以省略，读 HERMES_SESSION_MESSAGE_ID（只是“去飞书查哪条”的指针，真值从飞书回读）。

状态机（custody_state = off | on | draining | frozen）：
  off      → on        只有 on：飞书私聊（twin_identity 回读核验常浩本人 + 私聊 + 10 分钟内 + 消息没用过，口令以回读原文为准；
                       时刻有歧义一律拒绝，一次最多 24 小时）或已签策略的默认时段（在时段内、这个时段没开过也没被停过）；
                       两种都要求策略核验通过
  on       → draining  off / revoke（任何来源）；或到了 until（开启时定的结束时刻）自动转
  draining → off       只有 off --drained，且 drain_proof 证明排空完成
  draining → frozen    freeze；或到了排空截止还没 --drained，自动转（「到点没收完」，fail closed）
  on       → frozen    freeze（冻结是收紧方向，允许）
  frozen   → off       只有 unfreeze（常浩私聊「解除冻结」）；frozen 时 on / off / off --drained 一律拒绝
自动转（到 until、到排空截止）是读的时候按时钟算出来的：status / verify 只读、只报算出来的状态；
任何写命令先把算出来的结果落盘，再做本次转换。所以没有定时任务也不会“到点了还显示开着”。
默认时段「停过」：时段内发生的关闭、撤销、冻结、到点结束、排空完成、解除冻结，都把这个时段记为用过，当晚不再自动开；
常浩再发一条新的「托管」照样能开。

状态文件 custody.json（DT_CUSTODY_FILE，默认 ~/Library/Application Support/DigitalTwin/custody.json，600）：
  schema "dt-custody/1"；state；updated_at；
  session（当前这次托管，off 时为 null）：id、started_at、until、source{kind feishu|window, message_id, open_id | window}、
          policy{version, sha256}、drain_minutes、window_def{start, end}、drain{started_at, deadline, source}、
          frozen{at, reason, items, source}、events[]；
  signature（当前签署）：version、sha256（全文）、message_id、open_id、signed_at、expires_at、valid_days、revoked{at, source, reason}；
  signed_versions（签过的全部版本号，严格递增、不复用）；used_messages{message_id: {at, purpose}}（防重放）；
  consumed_windows{时段开始日期: 原因}；history（结束了的托管，留最近 30 次）。
  读的时候逐字段校验（类型、时区、先后、各状态必需字段），任何一处不对都算坏。
  写：同目录临时文件（600）→ fsync → rename，原子替换；全程持 custody.json.lock 的排他锁（最多等 10 秒）。
  第一次写之前先落初始化标记 custody.json.initialized：之后状态文件不见了不当成第一次用（冻结、版本、用过的消息会丢），
  报错要人工从备份恢复；确认要彻底重来，连标记一起删。
  读不了、格式不对、丢了：只读接口给 state=None + error、不准入；写命令全部拒绝（frozen 不能靠写坏或删掉文件解除）。

策略文件 policy.md（DT_CUSTODY_POLICY，默认 ~/Library/Application Support/DigitalTwin/custody/policy.md），按
docs/托管策略-模板.md 填。签署时校验：除模板里两行固定说明（EXEMPT_LINES，逐字相同才豁免）外不许有〔填…〕；
版本、有效期（签署日起 N 天）只从开头的表里读，排空截止（N 小时 / N 分钟）、默认时段（每晚 HH:MM 到次日 HH:MM，结束早于开始）
只从「## 1. 默认托管时段」读，引用块不参与，每项恰好一处；文件版本 = 口令版本；版本比签过的都大；全文 SHA-256 前 12 位 = 口令。
verify 每次重算：没签、撤销、文件不见、内容和签署时不同、字段读不出、过期 → 无策略。

给后续批次的接口（#133 账本、#134 夜跑主控 / 出口闸 / 送达闸、#136 托管合并）：
  python 里 import custody：read_status(now) 只读，返回 {"state", "error", "session", "policy", "admission", ...}；
  admission = {"eligible", "reasons"}：状态能读、是 on、策略有效、会话策略就是当前有效策略才 eligible。下游不得只看 state=="on"，
  要看 admission；它只管托管这一层，夜跑授权、额度、合并条件各闸自己核；每次执行前现读，不缓存。
  命令行：custody.py status --json（同一份结构）。
  排空：#133 在 drain_proof 里接账本后，off --drained 才可能成功；到截止还有对账不清的合并调 freeze --item …（已冻结也能补）。
威胁模型同 twin_identity：防模型自报、文本冒充、重放；不防同一 macOS 用户下的恶意本地进程。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
import twin_identity as I  # noqa: E402  同目录

SCHEMA = "dt-custody/1"
TZ = ZoneInfo("Asia/Shanghai")
STATES = ("off", "on", "draining", "frozen")
DEFAULT_FILE = "~/Library/Application Support/DigitalTwin/custody.json"
DEFAULT_POLICY = "~/Library/Application Support/DigitalTwin/custody/policy.md"
LOCK_WAIT = 10.0            # 秒：等锁上限
HISTORY_KEEP = 30
USED_KEEP_DAYS = 30         # 用过的消息 ID 留多久（消息本身 10 分钟就过时效，留 30 天足够防重放）
EVENTS_KEEP = 50
MAX_DRAIN_MINUTES = 24 * 60
MAX_VALID_DAYS = 366
SOURCE_LABEL = re.compile(r"^[\w\-一-鿿]{1,32}$")
PUNCT = " \t\r\n　。.!！~～"
PLACEHOLDER = "〔填"
SIGN_TEXT = re.compile(r"^签托管策略\s*[vV](\d+)\s+([0-9a-fA-F]{12})$")
UNTIL_TEXT = re.compile(r"^(今天|明天)?\s*(凌晨|早上|上午|中午|下午|傍晚|晚上)?\s*(?:"
                        r"(?P<ch>\d{1,2})\s*[:：]\s*(?P<cm>\d{2})|(?P<h>\d{1,2})\s*(?:点|时)\s*(?:(?P<half>半)|(?P<m>\d{1,2})\s*分?)?)$")
# 时段词 → (允许的钟点, 换算)：钟点不在范围里就拒绝，不猜（第 1 轮审查 P1-04：「今晚 12 点」曾被算成次日中午）
PERIODS = {
    "凌晨": (range(0, 6), 0), "早上": (range(5, 12), 0), "上午": (range(5, 12), 0),
    "中午": ((11, 12, 1, 2), None), "下午": (range(1, 7), 12), "傍晚": (range(5, 8), 12), "晚上": (range(6, 12), 12),
}
MAX_UNTIL = dt.timedelta(hours=24)


class Rejected(Exception):
    """转换不允许、来源不认、签署不过。code 给机器判，str 给人看。"""

    def __init__(self, msg, code="Rejected"):
        super().__init__(msg)
        self.code = code


# ---------- 路径、时间 ----------
def state_path():
    return Path(os.environ.get("DT_CUSTODY_FILE") or DEFAULT_FILE).expanduser()


def policy_path():
    return Path(os.environ.get("DT_CUSTODY_POLICY") or DEFAULT_POLICY).expanduser()


def now_tz():
    return dt.datetime.now(TZ)


def iso(t):
    return t.astimezone(TZ).isoformat(timespec="seconds") if t else None


def parse_iso(s):
    return dt.datetime.fromisoformat(s) if s else None


# ---------- 策略文件 ----------
# 模板（docs/托管策略-模板.md）里提到〔填〕本身的两行固定说明：只有和它们逐字相同的行不算待填项（第 1 轮审查 P1-06：
# 不按引用块、章节整段豁免）。模板改了这两行，test_custody 用真模板会失败，提醒同步这里。
EXEMPT_LINES = frozenset({
    "> **所有〔填〕都要填掉**；留着任何一处〔填〕，整份策略不生效。",
    "1. 填完全部〔填〕，把文件交给猛鬼兽或亲手放到上面的位置。",
})


def unfilled(text):
    """还留着〔填…〕的行号（上面两行固定说明除外）。"""
    return [n for n, line in enumerate(text.splitlines(), 1) if PLACEHOLDER in line and line.strip() not in EXEMPT_LINES]


def sections(text):
    """按「## 」标题切段：{标题行: [正文行]}；第一个「## 」之前的叫 "前言"。引用块（> 开头）不算正文，不参与取字段。"""
    out, cur = {"前言": []}, "前言"
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line.strip()
            out.setdefault(cur, [])
            continue
        if not line.lstrip().startswith(">"):
            out[cur].append(line)
    return out


def only_one(lines, pattern, what, errors):
    """在 lines 里找 pattern：恰好一处才取；没有或不止一处都记错误（P1-07：不拿全文第一个匹配，重复、矛盾一律拒绝）。"""
    hits = [m for m in (re.search(pattern, ln) for ln in lines) if m]
    if len(hits) == 1:
        return hits[0]
    errors.append(f"{what}{'没写' if not hits else f'写了 {len(hits)} 处，只能有一处'}")
    return None


def parse_policy(text):
    """读出本脚本要用的四项，位置固定：版本、有效期在开头的表里（第一个「## 」之前），默认时段和排空截止在「## 1. 默认托管时段」里；
    引用块不参与；每项恰好一处。其余条款归出口闸、合并执行器（#134、#136）。返回 (字段, 错误列表)。"""
    errors, out = [], {}
    sec = sections(text)
    head = sec["前言"]
    one = [v for k, v in sec.items() if k.startswith("## 1. 默认托管时段")]
    if len(one) != 1:
        errors.append("要有且只有一节「## 1. 默认托管时段」")
    body = one[0] if len(one) == 1 else []
    m = only_one(head, r"^\|\s*版本\s*\|\s*(.*?)\s*\|\s*$", "「版本」行", errors)
    if m:
        if re.fullmatch(r"[1-9]\d{0,8}", m.group(1)):
            out["version"] = int(m.group(1))
        else:
            errors.append("「版本」要写成一个正整数")
    m = only_one(head, r"^\|\s*有效期\s*\|\s*(.*?)\s*\|\s*$", "「有效期」行", errors)
    if m:
        d = re.fullmatch(r"签署日起\s*(\d+)\s*天(?:[；;，,].*)?", m.group(1))
        if d and 1 <= int(d.group(1)) <= MAX_VALID_DAYS:
            out["valid_days"] = int(d.group(1))
        else:
            errors.append(f"「有效期」要以「签署日起 N 天」开头（N 在 1–{MAX_VALID_DAYS}）")
    m = only_one(body, r"排空截止[：:]\s*(\d+)\s*(小时|分钟)", "「排空截止」", errors)
    if m:
        minutes = int(m.group(1)) * (60 if m.group(2) == "小时" else 1)
        if 0 < minutes <= MAX_DRAIN_MINUTES:
            out["drain_minutes"] = minutes
        else:
            errors.append("「排空截止」要大于 0、不超过 24 小时")
    m = only_one(body, r"每晚\s*(\d{1,2})[:：](\d{2})\s*到次日\s*(\d{1,2})[:：](\d{2})", "「每晚 HH:MM 到次日 HH:MM」", errors)
    if m:
        sh, sm, eh, em = (int(g) for g in m.groups())
        if not (sh < 24 and eh < 24 and sm < 60 and em < 60):
            errors.append("默认时段的时刻不对")
        elif (eh, em) >= (sh, sm):
            errors.append("默认时段写的是「到次日」，结束时刻要早于开始时刻（例：每晚 23:30 到次日 08:00）")
        else:
            out["window"] = {"start": f"{sh:02d}:{sm:02d}", "end": f"{eh:02d}:{em:02d}"}
    return out, errors


def read_policy(path=None):
    """返回 (bytes, text)；文件不在或读不了抛 Rejected。"""
    p = path or policy_path()
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raise Rejected(f"策略文件不在：{p}", "PolicyMissing")
    except OSError as e:
        raise Rejected(f"读不了策略文件 {p}（{e.strerror or e}）", "PolicyMissing")
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError:
        raise Rejected("策略文件不是 UTF-8 文本", "PolicyInvalid")


def verify_policy(doc, now=None, path=None):
    """每次使用都重算：没签、文件不见、内容和签署时不同、读不出字段、过期 → ok=False。不改任何东西。"""
    now = now or now_tz()
    sig = (doc or {}).get("signature")
    base = {"ok": False, "path": str(path or policy_path())}
    if not sig:
        return {**base, "code": "unsigned", "reason": "还没签托管策略"}
    base.update(version=sig["version"], expires_at=sig["expires_at"])
    if sig.get("revoked"):
        r = sig["revoked"]
        return {**base, "code": "revoked", "reason": f"托管策略 v{sig['version']} 已于 {r['at']} 撤销（{r['source']}），"
                                                    "恢复原文件也不再生效；要用就换更高版本重签"}
    try:
        raw, text = read_policy(path)
    except Rejected as e:
        return {**base, "code": "missing", "reason": str(e)}
    sha = hashlib.sha256(raw).hexdigest()
    if sha != sig["sha256"]:
        return {**base, "code": "changed", "reason": f"策略文件内容和签署 v{sig['version']} 时不一样（被改过），视为无策略；要用就换版本重签"}
    fields, errors = parse_policy(text)
    if unfilled(text) or errors or fields.get("version") != sig["version"]:
        return {**base, "code": "invalid", "reason": "策略文件读不出必需字段或版本和签署记录不一致"}
    if now >= parse_iso(sig["expires_at"]):
        return {**base, "code": "expired", "reason": f"托管策略 v{sig['version']} 已于 {sig['expires_at']} 过期，要续签（换版本）"}
    return {"ok": True, "code": "valid", "path": base["path"], "version": sig["version"], "sha256": sha,
            "signed_at": sig["signed_at"], "expires_at": sig["expires_at"], "drain_minutes": fields["drain_minutes"],
            "window": fields["window"]}


# ---------- 默认时段 ----------
def _hm(s):
    h, m = s.split(":")
    return int(h), int(m)


def windows_around(window, now):
    """now 前后几天的默认时段 [(key, start, end)]；key 是时段开始那天的日期。"""
    (sh, sm), (eh, em) = _hm(window["start"]), _hm(window["end"])
    local = now.astimezone(TZ)
    out = []
    for back in (1, 0, -1):
        day = local.date() - dt.timedelta(days=back)
        start = dt.datetime(day.year, day.month, day.day, sh, sm, tzinfo=TZ)
        end = dt.datetime(day.year, day.month, day.day, eh, em, tzinfo=TZ)
        if end <= start:
            end += dt.timedelta(days=1)
        out.append((day.isoformat(), start, end))
    return out


def current_window(window, now):
    for key, start, end in windows_around(window, now):
        if start <= now < end:
            return key, start, end
    return None


def tonight_window(window, now):
    """「今晚」的时段：在时段内就是当前这个，不在就是下一个。"""
    cur = current_window(window, now)
    if cur:
        return cur
    return next((k, s, e) for k, s, e in windows_around(window, now) if s > now)


def next_window_end(window, now):
    """不带时刻的「托管」开到哪：下一个默认时段结束时刻（例：08:00）。"""
    eh, em = _hm(window["end"])
    local = now.astimezone(TZ)
    t = local.replace(hour=eh, minute=em, second=0, microsecond=0)
    return t if t > local else t + dt.timedelta(days=1)


# ---------- 口令 ----------
def clean_text(text):
    return (text or "").strip(PUNCT)


def parse_on_text(text, now, window):
    """「托管」→ 开到下一个默认时段结束；「托管到 X 点」→ 开到指定时刻。别的话（含「今晚不托管」「停止托管」）不算开启口令。
    时刻：[今天|明天][凌晨|早上|上午|中午|下午|傍晚|晚上] + H 点[半|M 分] 或 HH:MM（今晚=今天晚上，明早=明天早上）。
    分不清的一律拒绝，请常浩说明确时刻：不带时段词的「12 点」、凌晨 / 晚上的「12 点」（说「0 点」或「中午 12 点」）、
    时段词和钟点对不上（「早上 9 点」以外的「早上 3 点」）、凌晨 0–5 点说「明天」（分不清今天还是明天）。
    结果必须晚于现在、不超过 24 小时；没写日期取下一次出现的这个时刻。"""
    t = clean_text(text)
    if t == "托管":
        return next_window_end(window, now)
    if not t.startswith("托管到"):
        raise Rejected("这条消息不是开启托管的口令（应为「托管」或「托管到 X 点」）", "BadCommand")
    rest = t[len("托管到"):].strip().replace("今晚", "今天晚上").replace("明早", "明天早上")
    m = UNTIL_TEXT.match(rest)
    if not m:
        raise Rejected("看不懂「托管到」后面的时刻（例：托管到 7 点、托管到下午 3 点半、托管到 23:30）", "BadCommand")
    day, period = m.group(1), m.group(2)
    clock = m.group("ch") is not None
    hour = int(m.group("ch") if clock else m.group("h"))
    minute = int(m.group("cm")) if clock else (30 if m.group("half") else int(m.group("m") or 0))
    ambiguous = "分不清是中午还是半夜，请说「中午 12 点」或「0 点」（也可以写 24 小时制，如 23:30）"
    if period:
        allowed, shift = PERIODS[period]
        if hour not in allowed:
            raise Rejected(f"「{period}{hour} 点」对不上（{period}只认 {'、'.join(map(str, allowed))} 点）；请说明确时刻", "BadCommand")
        hour = (hour + 12 if hour in (1, 2) else hour) if period == "中午" else hour + shift
    elif not clock and hour == 12:
        raise Rejected(f"「12 点」{ambiguous}", "BadCommand")
    if hour == 24 and minute == 0 and not period:
        hour = 0                                 # 24 点 / 24:00 = 当天结束，即下一个 0 点
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise Rejected("「托管到」的时刻不对", "BadCommand")
    local = now.astimezone(TZ)
    at_today = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day == "今天":
        until = at_today
        if until <= local:
            raise Rejected("说的是今天，这个时刻已经过了", "BadCommand")
    elif day == "明天":
        if local.hour < 6:
            raise Rejected("凌晨说「明天」分不清是今天白天还是明天，请直接说时刻（如「托管到 8 点」）", "BadCommand")
        until = at_today + dt.timedelta(days=1)
    else:
        until = at_today if at_today > local else at_today + dt.timedelta(days=1)
    if until - local > MAX_UNTIL:
        raise Rejected("托管一次最多开 24 小时，这个时刻太远了", "BadCommand")
    return until


# ---------- 飞书核验 ----------
def fetch_message(message_id):
    """回读一条消息并带正文；测试打桩这里。"""
    return I.lark_fetch(message_id, with_text=True)


def owner_config():
    import twin_state as T   # 同目录；只用它读本机受控配置
    try:
        return T.owner_open_id(), T.owner_chat_id()
    except T.StateError as e:
        raise Rejected(str(e), "NotAuthorized")


def verify_owner_message(message_id, now):
    """常浩本人、和猛鬼兽的私聊、10 分钟内（twin_identity.verify_message 同一核验）。返回回读结果（带 text、sender_open_id）。"""
    owner, chat = owner_config()
    box = {}

    def fetch(mid):
        box["m"] = fetch_message(mid)
        return box["m"]

    # lark-cli 的 create_time 是本机时区：按本机时区比时效（同 twin_state 的用法）
    v = I.verify_message(message_id, owner, fetch=fetch, now=now.astimezone(), owner_chat_id=chat)
    if not v["ok"]:
        raise Rejected(f"身份核实不通过：{v['reason']}", "NotAuthorized")
    return box["m"]


def claim(doc, message_id, purpose, now):
    used = doc.setdefault("used_messages", {})
    if message_id in used:
        raise Rejected(f"这条飞书消息已经用过（{used[message_id]['at']}，{used[message_id]['purpose']}）：一条消息只换一次动作，"
                       "要再做请常浩再说一次", "Replay")
    used[message_id] = {"at": iso(now), "purpose": purpose}


# ---------- 状态文件 ----------
def empty_doc():
    return {"schema": SCHEMA, "state": "off", "updated_at": None, "session": None, "signature": None,
            "signed_versions": [], "used_messages": {}, "consumed_windows": {}, "history": []}


class Bad(ValueError):
    pass


def _need(cond, what):
    if not cond:
        raise Bad(what)


def _time(v, what):
    """带时区的 ISO 时间；不是就 Bad。"""
    _need(isinstance(v, str), f"{what} 不是时间")
    try:
        t = dt.datetime.fromisoformat(v)
    except ValueError:
        raise Bad(f"{what} 不是合法时间")
    _need(t.tzinfo is not None, f"{what} 没有时区")
    return t


def _int(v, what, lo, hi):
    _need(isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi, f"{what} 不是 {lo}–{hi} 的整数")
    return v


def _str(v, what, pattern=None):
    _need(isinstance(v, str) and v and (pattern is None or re.fullmatch(pattern, v)), f"{what} 格式不对")
    return v


def _keys(d, what, required, optional=()):
    _need(isinstance(d, dict), f"{what} 不是对象")
    missing = [k for k in required if k not in d]
    extra = [k for k in d if k not in required and k not in optional]
    _need(not missing and not extra, f"{what} 字段不对（缺 {missing}，多 {extra}）")


HHMM = r"(?:[01]\d|2[0-3]):[0-5]\d"
SHA = r"[0-9a-f]{64}"


def check_signature(sig):
    _keys(sig, "signature", ("version", "sha256", "message_id", "open_id", "signed_at", "expires_at", "valid_days"), ("revoked",))
    _int(sig["version"], "signature.version", 1, 10 ** 9)
    _str(sig["sha256"], "signature.sha256", SHA)
    _str(sig["message_id"], "signature.message_id", r"om_[A-Za-z0-9]{6,}")
    _str(sig["open_id"], "signature.open_id")
    _int(sig["valid_days"], "signature.valid_days", 1, MAX_VALID_DAYS)
    signed, expires = _time(sig["signed_at"], "signature.signed_at"), _time(sig["expires_at"], "signature.expires_at")
    _need(expires - signed == dt.timedelta(days=sig["valid_days"]), "signature 的到期时间和有效天数对不上")
    if sig.get("revoked") is not None:
        _keys(sig["revoked"], "signature.revoked", ("at", "source", "reason"))
        _time(sig["revoked"]["at"], "signature.revoked.at")
        _str(sig["revoked"]["source"], "signature.revoked.source")
        _need(isinstance(sig["revoked"]["reason"], str), "signature.revoked.reason 格式不对")


def check_session(s, state):
    _keys(s, "session", ("id", "started_at", "until", "source", "policy", "drain_minutes", "window_def", "drain", "frozen", "events"))
    _str(s["id"], "session.id", r"c-\d{8}-\d{6}")
    started, until = _time(s["started_at"], "session.started_at"), _time(s["until"], "session.until")
    _need(started < until <= started + MAX_UNTIL + dt.timedelta(minutes=1), "session.until 要晚于开始、不超过 24 小时")
    src = s["source"]
    _need(isinstance(src, dict) and src.get("kind") in ("feishu", "window"), "session.source.kind 不对")
    if src["kind"] == "feishu":
        _keys(src, "session.source", ("kind", "message_id", "open_id"))
        _str(src["message_id"], "session.source.message_id", r"om_[A-Za-z0-9]{6,}")
        _str(src["open_id"], "session.source.open_id")
    else:
        _keys(src, "session.source", ("kind", "window"))
        _str(src["window"], "session.source.window", r"\d{4}-\d{2}-\d{2}")
    _keys(s["policy"], "session.policy", ("version", "sha256"))
    _int(s["policy"]["version"], "session.policy.version", 1, 10 ** 9)
    _str(s["policy"]["sha256"], "session.policy.sha256", SHA)
    _int(s["drain_minutes"], "session.drain_minutes", 1, MAX_DRAIN_MINUTES)
    _keys(s["window_def"], "session.window_def", ("start", "end"))
    _str(s["window_def"]["start"], "session.window_def.start", HHMM)
    _str(s["window_def"]["end"], "session.window_def.end", HHMM)
    _need(isinstance(s["events"], list), "session.events 不是列表")
    if s["drain"] is not None:
        _keys(s["drain"], "session.drain", ("started_at", "deadline", "source"))
        a, b = _time(s["drain"]["started_at"], "session.drain.started_at"), _time(s["drain"]["deadline"], "session.drain.deadline")
        _need(a < b <= a + dt.timedelta(minutes=s["drain_minutes"]), "session.drain 截止时间不对")
        _str(s["drain"]["source"], "session.drain.source")
    if s["frozen"] is not None:
        _keys(s["frozen"], "session.frozen", ("at", "reason", "items", "source"))
        _time(s["frozen"]["at"], "session.frozen.at")
        _str(s["frozen"]["reason"], "session.frozen.reason")
        _str(s["frozen"]["source"], "session.frozen.source")
        _need(isinstance(s["frozen"]["items"], list) and all(isinstance(i, str) for i in s["frozen"]["items"]),
              "session.frozen.items 格式不对")
    want = {"on": (False, False), "draining": (True, False), "frozen": (None, True)}[state]
    for key, need in zip(("drain", "frozen"), want):
        if need is not None:
            _need((s[key] is not None) == need, f"状态 {state} 和 session.{key} 对不上")


def check_doc(doc):
    """完整结构校验（第 1 轮审查 P1-01）：任何关键字段缺失、类型不对、时间不合法都算坏，读接口给 state=None、写接口全拒。"""
    _keys(doc, "状态文件", ("schema", "state", "updated_at", "session", "signature", "signed_versions", "used_messages",
                         "consumed_windows", "history"))
    _need(doc["schema"] == SCHEMA, "schema 不是 dt-custody/1")
    _need(doc["state"] in STATES, "state 不对")
    if doc["updated_at"] is not None:
        _time(doc["updated_at"], "updated_at")
    if doc["state"] == "off":
        _need(doc["session"] is None, "off 却有 session")
    else:
        check_session(doc["session"], doc["state"])
    vs = doc["signed_versions"]
    _need(isinstance(vs, list) and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in vs)
          and all(a < b for a, b in zip(vs, vs[1:])), "signed_versions 要是严格递增的正整数")
    if doc["signature"] is not None:
        check_signature(doc["signature"])
        _need(vs and doc["signature"]["version"] == vs[-1], "signature 版本不是签过的最大版本")
    _need(isinstance(doc["used_messages"], dict), "used_messages 不是对象")
    for k, v in doc["used_messages"].items():
        _keys(v, f"used_messages.{k}", ("at", "purpose"))
        _time(v["at"], f"used_messages.{k}.at")
    _need(isinstance(doc["consumed_windows"], dict) and all(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", k) and isinstance(v, str) for k, v in doc["consumed_windows"].items()),
        "consumed_windows 格式不对")
    _need(isinstance(doc["history"], list) and all(isinstance(h, dict) for h in doc["history"]), "history 格式不对")
    return doc


def marker_path(path=None):
    p = path or state_path()
    return p.with_name(p.name + ".initialized")


def load(path=None):
    """读状态文件。从没写过（初始化标记也没有）算第一次用：off、空记录。
    写过以后文件不见了不当成第一次（第 1 轮审查 P1-02）：冻结、签过的版本、用过的消息都会丢，所以抛 ValueError，
    要人工从备份恢复（或确认要彻底重来时连同 custody.json.initialized 一起删）。读不了、格式不对也抛 ValueError。"""
    p = path or state_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        if marker_path(p).exists():
            raise ValueError(f"{p} 不见了，但托管状态写过（有 {marker_path(p).name}）：不当成第一次用，要人工从备份恢复")
        return empty_doc()
    except OSError as e:
        raise ValueError(f"读不了 {p}（{e.strerror or e}）")
    try:
        return check_doc(json.loads(raw))
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"{p} 内容不对（{e}），要人工核对")


def save(doc, path=None):
    """同目录临时文件（600）→ fsync → rename；失败时删临时文件、原文件不动。"""
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_name(f".{p.name}.tmp{os.getpid()}")
    data = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    marker = marker_path(p)
    if not marker.exists():                 # 先落初始化标记再写状态：之后状态文件丢了也不会被当成第一次
        fd = os.open(str(marker), os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(iso(now_tz()) + "\n")
            f.flush()
            os.fsync(f.fileno())
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(p))
        dfd = os.open(str(p.parent), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


@contextlib.contextmanager
def locked(path=None, wait=LOCK_WAIT):
    p = path or state_path()
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(p.with_name(p.name + ".lock")), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Rejected(f"托管状态正被别的进程改（等锁超过 {wait:g} 秒），稍后再试", "Locked")
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


# ---------- 状态机 ----------
def event(session, now, frm, to, source, note=""):
    ev = session.setdefault("events", [])
    ev.append({"at": iso(now), "from": frm, "to": to, "source": source, **({"note": note} if note else {})})
    del ev[:-EVENTS_KEEP]


def consume_window_at(doc, at, reason):
    """at 落在策略默认时段里，就把这个时段记为用过（之后不再自动开）。"""
    sig_window = ((doc.get("session") or {}).get("window_def")) or None
    if not sig_window:
        return
    cur = current_window(sig_window, at)
    if cur:
        doc["consumed_windows"].setdefault(cur[0], reason)


def advance(doc, now):
    """按时钟算：on 到 until → draining；draining 到截止 → frozen。原地改 doc，返回是否改了。"""
    changed = False
    s = doc.get("session")
    if doc["state"] == "on" and s.get("until") and now >= parse_iso(s["until"]):
        until = parse_iso(s["until"])
        deadline = until + dt.timedelta(minutes=s["drain_minutes"])
        s["drain"] = {"started_at": iso(until), "deadline": iso(deadline), "source": "到点（until）"}
        event(s, until, "on", "draining", "到点（until）")
        consume_window_at(doc, until, "到点结束")
        doc["state"], changed = "draining", True
    if doc["state"] == "draining" and now >= parse_iso(s["drain"]["deadline"]):
        deadline = parse_iso(s["drain"]["deadline"])
        s["frozen"] = {"at": iso(deadline), "reason": "排空截止已过，没有收完（没人确认排空完成）", "items": [], "source": "到点（排空截止）"}
        event(s, deadline, "draining", "frozen", "到点（排空截止）")
        consume_window_at(doc, deadline, "排空截止自动冻结")
        doc["state"], changed = "frozen", True
    return changed


def prune(doc, now):
    cutoff = now - dt.timedelta(days=USED_KEEP_DAYS)
    doc["used_messages"] = {k: v for k, v in doc["used_messages"].items() if parse_iso(v["at"]) >= cutoff}
    keep = sorted(doc["consumed_windows"])[-60:]
    doc["consumed_windows"] = {k: doc["consumed_windows"][k] for k in keep}
    del doc["history"][:-HISTORY_KEEP]


def transaction(fn, now=None, path=None):
    """持锁：读 → 按时钟推进 → fn(doc) 改 → 落盘。fn 抛 Rejected 时什么都不写（含消息占用）。返回 (doc, fn 的结果)。"""
    now = now or now_tz()
    with locked(path):
        try:
            doc = load(path)
        except ValueError as e:
            raise Rejected(f"托管状态文件有问题，拒绝一切改动：{e}", "Corrupt")
        advanced = advance(doc, now)
        try:
            result = fn(doc)
        except Rejected:
            if advanced:              # 时钟推进照样落盘，本次动作不落
                doc = load(path)
                advance(doc, now)
                prune(doc, now)
                doc["updated_at"] = iso(now)
                save(doc, path)
            raise
        prune(doc, now)
        doc["updated_at"] = iso(now)
        save(doc, path)
        return doc, result


def require_policy(doc, now, path=None):
    v = verify_policy(doc, now, path)
    if not v["ok"]:
        raise Rejected(f"没有有效的托管策略，不能开启：{v['reason']}", "PolicyInvalid")
    return v


def do_on(source, message_id=None, now=None, path=None, policy=None):
    now = now or now_tz()
    m = None
    if source == "feishu" and message_id:
        m = verify_owner_message(message_id, now)      # 联网回读放在锁外；占用登记和状态变化在锁内同一次落盘

    def fn(doc):
        if doc["state"] != "off":
            hint = {"frozen": "托管已冻结，只有常浩私聊「解除冻结」才能解除，解除前不能重新开启",
                    "draining": "托管正在排空，排空完成前不能重新开启", "on": "托管已经开着"}[doc["state"]]
            raise Rejected(hint, "BadTransition")
        if source not in ("feishu", "window"):
            raise Rejected(f"开启只认飞书私聊（feishu）和已签策略的默认时段（window），不认「{source}」", "NotAuthorized")
        pol = require_policy(doc, now, policy)
        if source == "feishu":
            if not message_id:
                raise Rejected("飞书开启必须带 --message-id（常浩这条消息的 id）", "NotAuthorized")
            claim(doc, message_id, "托管开启", now)
            until = parse_on_text(m.get("text"), now, pol["window"])
            src = {"kind": "feishu", "message_id": message_id, "open_id": m["sender_open_id"]}
        else:
            if message_id:
                raise Rejected("默认时段开启不带消息 id", "BadCommand")
            cur = current_window(pol["window"], now)
            if not cur:
                raise Rejected(f"现在不在策略的默认托管时段（每晚 {pol['window']['start']} 到次日 {pol['window']['end']}）里", "NotAuthorized")
            key, _, end = cur
            if key in doc["consumed_windows"]:
                raise Rejected(f"{key} 这个默认时段已经开过或被关过（{doc['consumed_windows'][key]}），不再自动开", "NotAuthorized")
            doc["consumed_windows"][key] = "默认时段开启"
            until = end
            src = {"kind": "window", "window": key}
        doc["state"] = "on"
        doc["session"] = {"id": "c-" + now.astimezone(TZ).strftime("%Y%m%d-%H%M%S"), "started_at": iso(now),
                          "until": iso(until), "source": src,
                          "policy": {"version": pol["version"], "sha256": pol["sha256"]},
                          "drain_minutes": pol["drain_minutes"], "window_def": pol["window"],
                          "drain": None, "frozen": None, "events": []}
        event(doc["session"], now, "off", "on", source)
        return {"until": iso(until)}

    return transaction(fn, now, path)


def drain_proof(doc, now):
    """排空完成的证明（第 1 轮审查 P1-08）：要由本脚本自己读账本核对「未送达决定已作废、在途合并已对账、没有 UNRECONCILED」。
    账本（#133）还没有，所以现在一律证明不了：off --drained 全部拒绝，排空到截止自动冻结，常浩私聊「解除冻结」收尾。
    #133 接入时在这里读账本；#134、#136 在这之前不得启用托管。返回 (能否证明, 原因)。"""
    return False, "账本（#133）还没接入，证明不了未送达决定已作废、在途合并已对账：不能宣称排空完成（到截止会自动冻结）"


def check_label(source):
    if not SOURCE_LABEL.match(source or ""):
        raise Rejected("--source 要是 1–32 个字的标签（如 feishu、window、yichuang、ledger）", "BadCommand")


def do_off(source, drained=False, skip_tonight=False, now=None, path=None, policy=None):
    now = now or now_tz()

    def fn(doc):
        check_label(source)
        st, s = doc["state"], doc.get("session")
        if st == "frozen":
            raise Rejected("托管已冻结：关不掉也不能宣称排空完成，只有常浩私聊「解除冻结」才能到 off", "BadTransition")
        if drained and skip_tonight:
            raise Rejected("--drained 和 --skip-tonight 不能一起用：先确认排空，再另说「今晚不托管」", "BadCommand")
        if drained:
            if st != "draining":
                raise Rejected(f"只有排空中（draining）才能确认排空完成，现在是 {st}", "BadTransition")
            ok, why = drain_proof(doc, now)
            if not ok:
                raise Rejected(why, "DrainUnverified")
            consume_window_at(doc, now, f"排空完成（{source}）")
            s["ended"] = {"at": iso(now), "source": source, "how": "排空完成"}
            event(s, now, "draining", "off", source, "排空完成")
            doc["history"].append(s)
            doc["state"], doc["session"] = "off", None
            return {"result": "off"}
        if skip_tonight:
            v = verify_policy(doc, now, policy)
            win = (s or {}).get("window_def") or (v.get("window") if v["ok"] else None)
            if not win:
                raise Rejected("没有有效策略，读不出默认时段，「今晚不托管」无从记起（没有策略默认时段本来也不会开）", "PolicyInvalid")
            key = tonight_window(win, now)[0]
            doc["consumed_windows"][key] = f"今晚不托管（{source}）"
        if st == "on":
            deadline = now + dt.timedelta(minutes=s["drain_minutes"])
            s["drain"] = {"started_at": iso(now), "deadline": iso(deadline), "source": source}
            event(s, now, "on", "draining", source)
            consume_window_at(doc, now, f"关闭（{source}）")
            doc["state"] = "draining"
            return {"result": "draining", "deadline": iso(deadline)}
        return {"result": st, "unchanged": True}

    return transaction(fn, now, path)


def do_freeze(source, reason, items=(), now=None, path=None):
    now = now or now_tz()

    def fn(doc):
        check_label(source)
        if not (reason or "").strip():
            raise Rejected("冻结要写原因（--reason）", "BadCommand")
        st, s = doc["state"], doc.get("session")
        if st == "off":
            raise Rejected("托管关着，没有可冻结的托管", "BadTransition")
        if st == "frozen":                  # 只补充未解决事项（去重），状态不变、不能借此解除
            new = [str(i) for i in items if str(i) not in s["frozen"]["items"]]
            if not new:
                return {"result": "frozen", "unchanged": True}
            s["frozen"]["items"] += list(dict.fromkeys(new))
            event(s, now, "frozen", "frozen", source, "补充未解决事项：" + "；".join(new))
            return {"result": "frozen", "added": new}
        s["frozen"] = {"at": iso(now), "reason": reason.strip(), "items": [str(i) for i in items], "source": source}
        event(s, now, st, "frozen", source, reason.strip())
        consume_window_at(doc, now, f"冻结（{source}）")
        doc["state"] = "frozen"
        return {"result": "frozen"}

    return transaction(fn, now, path)


def do_unfreeze(message_id, now=None, path=None):
    now = now or now_tz()
    if not message_id:
        raise Rejected("解除冻结必须带 --message-id（常浩在私聊说「解除冻结」的那条消息）", "NotAuthorized")
    m = verify_owner_message(message_id, now)

    def fn(doc):
        if doc["state"] != "frozen":
            raise Rejected(f"只有冻结（frozen）才需要解除，现在是 {doc['state']}", "BadTransition")
        claim(doc, message_id, "解除冻结", now)
        if clean_text(m.get("text")) != "解除冻结":
            raise Rejected("这条消息不是「解除冻结」", "BadCommand")
        s = doc["session"]
        consume_window_at(doc, now, "解除冻结（解除不等于重新开启）")
        s["ended"] = {"at": iso(now), "source": "feishu", "how": "解除冻结", "message_id": message_id,
                      "open_id": m["sender_open_id"]}
        event(s, now, "frozen", "off", "feishu", "解除冻结")
        doc["history"].append(s)
        doc["state"], doc["session"] = "off", None
        return {"result": "off"}

    return transaction(fn, now, path)


def do_revoke(source, reason="", now=None, path=None):
    """撤销当前签署（第 1 轮审查 P1-03）：持久记下，之后恢复原文件也不再生效，只能换更高版本重签。撤销是收紧方向，任何来源都可以
    （标签自报）；托管开着就同时进入排空。"""
    now = now or now_tz()

    def fn(doc):
        check_label(source)
        sig = doc["signature"]
        if not sig:
            raise Rejected("还没签过托管策略，没有可撤销的", "BadTransition")
        if sig.get("revoked"):
            return {"result": "revoked", "unchanged": True, "version": sig["version"]}
        sig["revoked"] = {"at": iso(now), "source": source, "reason": (reason or "").strip()}
        out = {"result": "revoked", "version": sig["version"], "state": doc["state"]}
        if doc["state"] == "on":
            s = doc["session"]
            deadline = now + dt.timedelta(minutes=s["drain_minutes"])
            s["drain"] = {"started_at": iso(now), "deadline": iso(deadline), "source": f"撤销策略（{source}）"}
            event(s, now, "on", "draining", source, "撤销策略")
            consume_window_at(doc, now, f"撤销策略（{source}）")
            doc["state"] = out["state"] = "draining"
            out["deadline"] = iso(deadline)
        return out

    return transaction(fn, now, path)


def do_sign(message_id, now=None, path=None, policy=None):
    now = now or now_tz()
    if not message_id:
        raise Rejected("签署必须带 --message-id（常浩发「签托管策略 v<版本> <哈希前 12 位>」的那条消息）", "NotAuthorized")
    m = verify_owner_message(message_id, now)

    def fn(doc):
        if doc["state"] != "off":
            raise Rejected(f"托管进行中（{doc['state']}）不换策略：一次托管只按一份策略走，结束后再签", "BadTransition")
        claim(doc, message_id, "签托管策略", now)
        cmd = SIGN_TEXT.match(clean_text(m.get("text")))
        if not cmd:
            raise Rejected("这条消息不是签署口令（应为「签托管策略 v<版本> <SHA-256 前 12 位>」）", "BadCommand")
        want_ver, want_prefix = int(cmd.group(1)), cmd.group(2).lower()
        raw, text = read_policy(policy)
        lines = unfilled(text)
        if lines:
            raise Rejected(f"策略还有未填项（〔填…〕，第 {'、'.join(map(str, lines[:10]))} 行），整份不生效", "Unfilled")
        fields, errors = parse_policy(text)
        if errors:
            raise Rejected("策略读不出必需字段：" + "；".join(errors), "PolicyInvalid")
        if fields["version"] != want_ver:
            raise Rejected(f"口令签的是 v{want_ver}，文件里是 v{fields['version']}", "VersionMismatch")
        top = max(doc["signed_versions"], default=0)
        if want_ver <= top:
            raise Rejected(f"版本 v{want_ver} 不比签过的最大版本 v{top} 大：版本单调递增、不复用，改了内容要换新版本号", "VersionReuse")
        sha = hashlib.sha256(raw).hexdigest()
        if sha[:12] != want_prefix:
            raise Rejected(f"哈希对不上：文件全文 SHA-256 前 12 位是 {sha[:12]}，口令是 {want_prefix}（文件可能被改过，或抄错了）",
                           "HashMismatch")
        expires = now + dt.timedelta(days=fields["valid_days"])
        doc["signature"] = {"version": want_ver, "sha256": sha, "message_id": message_id, "open_id": m["sender_open_id"],
                            "signed_at": iso(now), "expires_at": iso(expires), "valid_days": fields["valid_days"]}
        doc["signed_versions"].append(want_ver)
        return {"version": want_ver, "sha256": sha, "expires_at": iso(expires)}

    return transaction(fn, now, path)


# ---------- 只读 ----------
def public_session(s):
    """给人 / 机器看的 session：去掉 open_id。"""
    if not s:
        return None
    s = json.loads(json.dumps(s))
    for d in (s.get("source"), s.get("ended")):
        if isinstance(d, dict) and "open_id" in d:
            d["open_id"] = "已核验（不显示）"
    return s


def admission(st):
    """统一的只读准入判定（第 1 轮审查建议）：托管状态能读、是 on、策略有效、会话开启时的策略就是现在这份有效策略，才 eligible。
    这只是「托管这一层」放不放行；夜跑授权、账本额度、合并条件仍由各闸自己核。每次执行前现读，不缓存。"""
    reasons = []
    if st["error"]:
        reasons.append(f"托管状态读不了：{st['error']}")
    else:
        if st["state"] != "on":
            reasons.append(f"托管不是开着（{st['state']}）")
        pol, s = st["policy"], st["session"]
        if not pol["ok"]:
            reasons.append(f"策略无效：{pol['reason']}")
        elif s and (s["policy"]["version"], s["policy"]["sha256"]) != (pol["version"], pol["sha256"]):
            reasons.append("这次托管开启时的策略和现在的有效策略不是同一份")
    return {"eligible": not reasons, "reasons": reasons}


def read_status(now=None, path=None, policy=None):
    """只读：按时钟算出现在的状态（不落盘），附策略核验和准入判定。读不了状态文件时 error 非空、state 为 None、不准入。"""
    now = now or now_tz()
    try:
        doc = load(path)
    except ValueError as e:
        st = {"schema": SCHEMA, "state": None, "error": str(e), "session": None, "policy": None, "checked_at": iso(now)}
    else:
        advance(doc, now)
        st = {"schema": SCHEMA, "state": doc["state"], "error": None, "session": public_session(doc.get("session")),
              "policy": verify_policy(doc, now, policy), "signed_versions": doc["signed_versions"],
              "last": public_session(doc["history"][-1]) if doc["history"] else None, "checked_at": iso(now)}
    st["admission"] = admission(st)
    return st


def short(t):
    return parse_iso(t).astimezone(TZ).strftime("%m-%d %H:%M") if t else "—"


def describe(st):
    if st["error"]:
        return f"托管状态读不了：{st['error']}（当作托管不可用）"
    s, p = st["session"], st["policy"]
    pol = (f"策略 v{p['version']} 有效，到 {short(p['expires_at'])}" if p["ok"]
           else "还没签托管策略" if p["code"] == "unsigned" else f"策略无效：{p['reason']}")
    if st["state"] == "off":
        return f"托管关着。{pol}"
    src = "飞书私聊" if s["source"]["kind"] == "feishu" else f"默认时段（{s['source']['window']}）"
    head = f"托管 {s['id']}：{src}开启于 {short(s['started_at'])}，按策略 v{s['policy']['version']}（{s['policy']['sha256'][:12]}）"
    if st["state"] == "on":
        return f"托管开着。{head}，到 {short(s['until'])} 自动转排空。{pol}"
    if st["state"] == "draining":
        return f"托管排空中（{s['drain']['source']}，{short(s['drain']['started_at'])} 起），截止 {short(s['drain']['deadline'])}。{head}。{pol}"
    f = s["frozen"]
    items = "；".join(f["items"]) if f["items"] else "无明细"
    return (f"托管已冻结（{short(f['at'])}，{f['reason']}；未解决：{items}）。禁止新决定、新合并和重新开启；"
            f"常浩核对后在猛鬼兽私聊说「解除冻结」才回到 off。{head}")


# ---------- 命令行 ----------
def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False, default=str))
    sys.exit(0 if ok else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    sub.add_parser("verify")
    o = sub.add_parser("on")
    o.add_argument("--source", required=True)
    o.add_argument("--message-id")
    f = sub.add_parser("off")
    f.add_argument("--source", required=True)
    f.add_argument("--drained", action="store_true")
    f.add_argument("--skip-tonight", action="store_true")
    z = sub.add_parser("freeze")
    z.add_argument("--source", required=True)
    z.add_argument("--reason", required=True)
    z.add_argument("--item", action="append", default=[])
    for name in ("unfreeze", "sign"):
        sub.add_parser(name).add_argument("--message-id")
    r = sub.add_parser("revoke")
    r.add_argument("--source", required=True)
    r.add_argument("--reason", default="")
    a = ap.parse_args(argv)
    mid = getattr(a, "message_id", None)
    if a.cmd in ("unfreeze", "sign") or (a.cmd == "on" and a.source == "feishu"):
        mid = mid or os.environ.get("HERMES_SESSION_MESSAGE_ID")

    if a.cmd == "status":
        st = read_status()
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
            sys.exit(0 if not st["error"] else 1)
        done(not st["error"], describe(st), state=st["state"])
    if a.cmd == "verify":
        try:
            doc = load()
        except ValueError as e:
            done(False, f"托管状态文件有问题：{e}", error="Corrupt")
        v = verify_policy(doc)
        done(v["ok"], f"托管策略 v{v['version']} 有效，到 {short(v['expires_at'])}" if v["ok"] else v["reason"],
             **{k: v[k] for k in ("code", "version", "sha256", "expires_at") if k in v})
    try:
        if a.cmd == "on":
            doc, r = do_on(a.source, mid)
            done(True, f"托管已开启，到 {short(r['until'])} 自动转排空", state="on", **r)
        if a.cmd == "off":
            doc, r = do_off(a.source, drained=a.drained, skip_tonight=a.skip_tonight)
            msg = {"draining": f"托管进入排空，截止 {short(r.get('deadline'))}", "off": "排空完成，托管已关闭"}.get(r["result"])
            if r.get("unchanged"):
                msg = f"托管本来就是 {r['result']}，没有变化" + ("；已记下今晚不托管" if a.skip_tonight else "")
            done(True, msg, state=doc["state"], **r)
        if a.cmd == "freeze":
            doc, r = do_freeze(a.source, a.reason, a.item)
            done(True, "托管已冻结" + ("（本来就冻结着）" if r.get("unchanged") else
                                    f"（补充了 {len(r['added'])} 项未解决事项）" if r.get("added") else ""), state="frozen")
        if a.cmd == "revoke":
            doc, r = do_revoke(a.source, a.reason)
            done(True, f"托管策略 v{r['version']} " + ("本来就撤销了" if r.get("unchanged") else "已撤销，恢复原文件也不再生效")
                 + ("；托管进入排空" if r.get("deadline") else ""), state=doc["state"])
        if a.cmd == "unfreeze":
            do_unfreeze(mid)
            done(True, "已解除冻结，托管关闭（off）", state="off")
        if a.cmd == "sign":
            doc, r = do_sign(mid)
            done(True, f"托管策略 v{r['version']} 已签（{r['sha256'][:12]}），到 {short(r['expires_at'])}", **r)
    except Rejected as e:
        done(False, str(e), error=e.code)


if __name__ == "__main__":
    main()
