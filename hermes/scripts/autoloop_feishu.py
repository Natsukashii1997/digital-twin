#!/usr/bin/env python3
"""飞书桥接：中台 feishu 通道（批 0B-4 第 4a 批，#195；规格 docs/秘书代批-规格.md §6、§7，0B-3 实测结论）。

用 feishu 通道钥匙向中台领卡片，以猛鬼兽机器人发到常浩私聊，报送达；卡片状态变了就改那条消息。点击不在这里收：
猛鬼兽网关的 merge-buttons 插件把按钮点击转给 autoloop_answer.py（这里只负责发卡和登记）。
机器人身份（ADR-015 修订 1 批 0E-3，全局坑 G-43）：用哪个飞书机器人发卡、回话，常浩在这个机器人下的 open_id 和私聊号从哪来，
只在 Bot 一处定义；默认是猛鬼兽（Bot，读 twin_state 的身份配置）。门卫（gatekeeper.py，跑在 dt-gate）换成 GateBot，
发卡、改卡、报送达、状态机、本人点击核对和卡片库都和这里共用一份，不另写。

0B-3 实测：网关转来的点击里有点击人 open_id 和聊天号，**没有原消息号**。所以每张卡发出前先登记一个随机令牌，
按钮里只带 {"autoloop": 令牌, "o": 选项号, "v": 这条消息展示的版本}；点击回来按令牌找到卡片号和送达的消息号（中台要求 ref 和
送达的那条一致），按 v 核对点的是不是登记的当前版本（#218 第 3 轮 P1-03：旧版本按钮的迟到点击直接拒，不排队）。
同一张卡只有一个令牌：送达报告丢了、中台再给一次时，用同一个幂等键重发，飞书回原消息，令牌也不变。

卡片库 cards.db（DigitalTwin/autoloop/，600）：
  feishu_cards  卡片号 → 令牌、送达的消息号、私聊号、种类、选项数、版本、飞书送达时间（秘书白天等 30 分钟从这里起算；按版本：
                version 是最后送到常浩那里的版本，delivered_at 是它送到的时刻，开着的卡改版成功后两者一起换，#218 审查 P1-02）、
                中台确认送达时间（报 delivered 成功后才有；没有之前本人的点击只排队，不提交，见 autoloop_answer；也按版本：
                改版时清空，只给报过的那一版记，#218 第 2 轮 P1-03）
  claims 的 version：本人点击时卡片在飞书上的版本；改版后旧版本排队的选择不转到新版本（autoloop_answer.submit 作废、请常浩重点）
  claims        每张卡每方一行（owner 本人 / secretary 秘书）和进度；claim_log 只追加，记每次状态变化
  secretary_cards  秘书控制面（secretary_gate.py，第 4c 批）在 secretary 通道领到的卡片：卡片号 → 版本、自己生成的 ref、
                领到时间、中台确认送达时间（报 delivered 成功后才有；没有之前秘书不答这张卡）
  和第 4c 批秘书的契约（规格 §7、§10 第 4 条「秘书获准送达前本人优先；获准后以中台先受理的为准」）：
    - 秘书从 reserved 改成 approved（唯一的送达授权点）必须在 db(write=True) 里做，并先调 owner_holds()：
      本人已有 queued / inflight / unknown / answered 的一行，秘书就不许获准（作废自己的预占）；
    - 本人一侧从不改秘书的行：秘书已 approved 时本人照样提交，谁先被中台受理算谁，后到的一方收到 changed=false。

恢复闸（批 0D）：领卡前先过 autoloop_recovery.ensure_open()；领了卡以后闸关了、或这张卡上次发送结果不明正在冻结，算「暂缓」
（Deferred），不向中台报 failed；发新卡走出口账本（autoloop_exits，幂等键
send_feishu:card-<卡片号>、冻结范围 card:<卡片号>、对账标记就是卡片令牌）：先记要发、再发，发送结果不明不重发，对账在私聊里
按令牌找那条消息。改卡（patch）本身幂等，不记账；结束了的卡改不了时另发的那条通知不带令牌，只过闸、不记账（重复了也只是多一条结束通知）。

  autoloop_feishu.py run          常驻：长轮询领卡片、发送、报送达（装成常驻服务是 D 档，要常浩说「装」）
  autoloop_feishu.py once         只领一次（测试、手动补发）
  autoloop_feishu.py show [--json]  只读：登记过的卡片、答卷进度和最近的变化记录
环境变量：DT_AUTOLOOP_CARDS_DB、DT_AUTOLOOP_CHANNEL_KEY_DIR（同 autoloop_config）、AGENT_AUTOLOOP_HOME、DT_LARK_CLI。
单写入者（#218 第 3 轮 P2-01，专项续审 P1-01 / P2-02 / P2-03）：发卡、改卡、报送达都在桥接锁里做（cards.db 同目录的
cards.db.bridge.lock，进程级排他锁，不可重入：锁里只调带下划线的内部实现）。deliver 在改飞书消息之前先核本地登记，登记已是
更高版本就拒（不把消息改回旧版）；写登记一律带「版本不回退」条件。对账补报送达（autoloop_answer.reconcile → report_delivered）
同样拿这把锁。报完送达就放锁，再提交排队的本人回答（autoloop_answer.flush，submit 自己会再核版本），中台慢时不堵别的卡。
中台库读不到（PR #218 第 5 轮 P1-01，常浩 11-19 选甲）：中台每递一次卡（GET /cards/next）都算一次尝试、1 分钟后再递，9 次
（或离第一次尝试超过一小时）后放弃（given_up，改版也不再递；见中台 kernel/decide.ts 的 nextCard、failed）。放弃后中台不会自己
再送，要本人「重新送」才恢复（agent-autoloop `abc8d41` 起：`agent-autoloop redeliver <卡片号>`，次数和一小时从头算；另有一种：
来源用同一个 key 把同一条通知再说一遍，中台也会重开）。所以：
  - 领卡前先确认中台库能读（hub_readable），读不到这轮不领、不用掉尝试次数，私聊提醒常浩（同一原因 1 小时最多 1 次）；
  - 领到后仍读不到这张卡的版本（极小窗口、或卡不见了），或本地登记的版本比中台的还新（中台库被恢复成旧快照，中台下次还是递
    这一版，第 6 轮 P2-01）：不发，向中台报 failed 并写明原因（中台按 1、3、7…分钟退避重试，状态里能看到原因），放锁后提醒；
    报 failed 本身出错（卡不在、5xx、没回应）也照样提醒，写明中台可能没记上、请人工核查（单独的提醒键，第 6 轮 P2-02）；
  - 中台已是更新的版本：不发、不报，中台 1 分钟后递最新版本；
  - run 每轮只读扫一次中台里 feishu 通道已放弃送达的卡，私聊告诉常浩（哪张、标题、试了几次、最后原因），并给出重新送的命令
    `agent-autoloop redeliver <卡片号>`（#278；重新送要本人钥匙，只给常浩手动用，桥接不自动调）。每一轮放弃只说一次：提醒键是
    「卡片号 + 这一轮第一次尝试的时间」（中台重新送时把它清掉、下一次递卡重新记），所以重新送之后又放弃会再提醒一次，
    中间桥接没扫到也一样。话按「重新送有没有用」分四种（照中台 kernel/decide.ts 的 worthSending）：卡片还开着 → 给命令；
    已处理完、之前送到过（只是那条消息没更新成最后结果）→ 说明可以不管，命令照给；仅告知的通知、发出不到 24 小时、没送到过 →
    说明你还没看到、给命令；其余（没送到就已结束，或通知过了 24 小时）→ 说明重新送也不会再发。同一轮放弃里送达报告迟到补上
    （中台 delivered 把次数和第一次尝试时间清空、放弃标记留着）的不算放弃，不提醒。
提醒记在 cards.db 的 alerts 表（提醒键 → 上次提醒时间），发不出去不记、下次再试。
不调用 AI。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_config as C  # noqa: E402
import hub_listener  # noqa: E402
from tidings_weekly import SendError, lark  # noqa: E402

CHANNEL = "feishu"
TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
OWNER_HOLDS = ("queued", "inflight", "unknown", "answered")
SCHEMA = """
CREATE TABLE IF NOT EXISTS feishu_cards (card_id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, message_id TEXT,
  chat_id TEXT NOT NULL, kind TEXT NOT NULL, options INTEGER NOT NULL, version INTEGER NOT NULL, delivered_at TEXT,
  hub_delivered_at TEXT);
CREATE TABLE IF NOT EXISTS claims (card_id INTEGER NOT NULL, who TEXT NOT NULL CHECK (who IN ('owner', 'secretary')),
  state TEXT NOT NULL, choice INTEGER, at TEXT NOT NULL, pid INTEGER, note TEXT, attempt TEXT, version INTEGER,
  PRIMARY KEY (card_id, who));
CREATE TABLE IF NOT EXISTS claim_log (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL, who TEXT NOT NULL,
  at TEXT NOT NULL, state TEXT NOT NULL, choice INTEGER, note TEXT, attempt TEXT);
CREATE TABLE IF NOT EXISTS secretary_cards (card_id INTEGER PRIMARY KEY, version INTEGER NOT NULL, ref TEXT NOT NULL UNIQUE,
  leased_at TEXT NOT NULL, hub_delivered_at TEXT);
CREATE TABLE IF NOT EXISTS alerts (key TEXT PRIMARY KEY, at TEXT NOT NULL);
"""
ALERT_EVERY = 3600                       # 秒：同一原因的提醒最多这么久一次


class Fail(Exception):
    """说人话的原因，不带钥匙。"""


class Deferred(Fail):
    """暂缓：恢复闸关着、或这张卡上一次发送结果不明正在冻结。不是失败，不向中台报 failed（审查 P1-04）。"""


class HubError(Fail):
    """中台调用失败。sent=False：请求根本没发出去（钥匙、配置读不到）；status：中台回的 HTTP 码，None 表示没拿到有效回应。"""
    def __init__(self, msg, status=None, sent=True):
        super().__init__(msg)
        self.status, self.sent = status, sent


class Bot:
    """发卡、回话、认本人的那一个飞书机器人。open_id 按应用区分（G-43），所以本人的 open_id、私聊号跟着机器人走。
    默认是猛鬼兽：lark-cli 用猛鬼兽应用（tidings_weekly），身份读 twin_state（twin_identity.py setup / setup-chat 配的）。"""
    name = "猛鬼兽"
    unbound = "本机没配置常浩的 open_id 或私聊号（twin_identity.py setup / setup-chat）"
    no_chat = "没配置常浩私聊的 chat_id（先 twin_identity.py setup-chat），或格式不对"

    def lark(self, args, what):
        return lark(args, what)                  # 运行时取本模块的 lark（测试替换 F.lark 照样生效）

    def send_text(self, chat, text, key):
        from tidings_weekly import send
        send(chat, "text", {"text": text}, key)

    def owner(self):
        """(常浩的 open_id, 常浩私聊号)，没配置的项是空值；读配置出错抛 Fail。"""
        import twin_state as T
        try:
            return T.owner_open_id(), T.owner_chat_id()
        except T.StateError as e:
            raise Fail(str(e))

    def owner_chat(self):
        """只要私聊号（发卡、提醒用）：只读私聊号的配置，不连带读 open_id（审查 P2-6：原链路发卡只看私聊号）。"""
        import twin_state as T
        try:
            return T.owner_chat_id()
        except T.StateError as e:
            raise Fail(str(e))


BOT = Bot()


def use(bot):
    """换机器人（门卫启动时调用一次）。一个进程只用一个机器人。"""
    global BOT
    BOT = bot


def owner_holds(con, card_id):
    """本人对这张卡已有排队、在途、结果不明或已答上的一行（给第 4c 批秘书获准前调用，须在同一个写事务里）。"""
    r = con.execute("SELECT state FROM claims WHERE card_id = ? AND who = 'owner'", (card_id,)).fetchone()
    return bool(r) and r["state"] in OWNER_HOLDS


def log_claim(con, card_id, who, state, choice=None, note=None, attempt=None):
    con.execute("INSERT INTO claim_log (card_id, who, at, state, choice, note, attempt) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (card_id, who, now(), state, choice, note, attempt))


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------- 卡片库 ----------
def db_file():
    return Path(os.environ.get("DT_AUTOLOOP_CARDS_DB") or C.G.SUPPORT / "autoloop/cards.db").expanduser()


@contextlib.contextmanager
def db(write=False):
    """write=True 时开 BEGIN IMMEDIATE：本人点击和秘书获准送达靠这把写锁互斥。"""
    f = db_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(f.parent, 0o700)
    if not f.exists():
        os.close(os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    con = sqlite3.connect(f, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        for table in ("claims", "claim_log"):    # 先查后建：旧结构直接拒用，旧库一点不动（不迁移、不补表）
            cols = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            if cols and ("attempt" not in cols or (table == "claims" and "version" not in cols)):
                raise Fail(f"{f} 是旧结构（{table} 缺 attempt / version 列），拒绝使用；要迁移请单独处理")
        con.executescript(SCHEMA)
        if write:
            con.execute("BEGIN IMMEDIATE")
        yield con
        if write and con.in_transaction:          # 调用方可能已在 with 里提前提交（autoloop_answer.submit，续审第 1 轮 P1-08）
            con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


@contextlib.contextmanager
def bridge_lock():
    """桥接单写入者锁（#218 第 3 轮 P2-01）：同一时间只有一个桥接进程在发卡、改卡、报送达，后来的等着。"""
    f = db_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(f"{f}.bridge.lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)                             # 关掉文件描述符即释放锁


def bridge_busy():
    """桥接锁现在有人拿着吗（不等）。给恢复闸排空用（第 2 轮预审 P2-C）：停写之后桥接在锁里先看闸，锁空着就没有桥接写在途。"""
    f = db_file()
    if not Path(f"{f}.bridge.lock").exists():
        return False
    fd = os.open(f"{f}.bridge.lock", os.O_WRONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def _still_open(epoch):
    """持桥接锁后再核一次开闸资格（第 2 轮预审 P2-C）：入口看过闸之后、拿到锁之前，可能已经停写或出现结果不明。
    改卡、补发结束通知、报送达都在这之后，排空时看到锁空着就说明没有桥接写在途。关了抛 Deferred（暂缓，不报 failed）。"""
    import autoloop_exits as E
    import autoloop_recovery as R
    try:
        with R.admission(epoch):                 # 锁顺序：桥接锁 → 出口账本（出口账本持有者从不等桥接锁）
            pass
    except E.Refused as e:
        raise Deferred(str(e))


def card_by_token(token):
    with db() as con:
        r = con.execute("SELECT * FROM feishu_cards WHERE token = ?", (token,)).fetchone()
    return dict(r) if r else None


# ---------- 中台（feishu 通道钥匙） ----------
def channel_key(channel=CHANNEL):
    f = C.channel_key_file(channel)
    try:
        key = f.read_text(encoding="utf-8").strip()
    except OSError:
        raise Fail(f"没有 {channel} 通道钥匙：常浩说「签」以后 autoloop_config.py issue-channel-key {channel} 才有")
    if not C.KEY_RE.match(key):
        raise Fail(f"{channel} 通道钥匙文件格式不对")
    return key


def hub_target(channel=CHANNEL):
    """发请求前要的东西：中台地址和通道钥匙。读不到就抛 HubError(sent=False)：请求还没发出去。
    channel：用哪条通道的钥匙（飞书桥接用 feishu；秘书控制面 secretary_gate 用 secretary，同一套协议和错误分类）。"""
    try:
        url, _ = C.hub_config()
    except Exception as e:                       # config.json 读不到、缺字段：一律算没发出去
        raise HubError(f"读不到中台配置（{type(e).__name__}）", sent=False)
    try:
        return url, channel_key(channel)
    except Fail as e:
        raise HubError(str(e), sent=False)


def hub(method, path, body=None, timeout=20, channel=CHANNEL):
    url, key = hub_target(channel)
    req = urllib.request.Request(url + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        hub_listener.check(url)                  # dt-gate 里：端口上不是 dt-gate 在听就不带通道钥匙去（0E-2）
    except OSError as e:
        raise HubError(str(e), sent=False)
    try:
        with opener.open(req, timeout=timeout) as r:
            raw = r.read().decode()
    except urllib.error.HTTPError as e:
        raise HubError(f"中台回 HTTP {e.code}（{method} {path.split('?')[0]}）", status=e.code)
    except OSError as e:
        raise HubError(f"连不上中台或没等到回应（{type(e).__name__}）")
    try:
        out = json.loads(raw or "{}")
    except ValueError:
        raise HubError("中台的回应不是 JSON")
    if not isinstance(out, dict):
        raise HubError("中台的回应格式不对")
    return out


# ---------- 卡片样子 ----------
def outcome(card):
    chosen = card["options"][card["choice"]] if card.get("choice") is not None and card.get("options") else card.get("answer")
    who = "秘书替你" if card.get("by") == C.SECRETARY else "你"
    if card["state"] == "decided":
        return f"{who}选了「{chosen}」，已记录，等待处理。"
    if card["state"] == "done":
        return f"{who}选了「{chosen}」。" + (f"已执行：{card['result']}" if card.get("result") else "已执行。")
    if card["state"] == "closed":
        return (f"不用决定了：{card.get('result') or ''}" if card.get("kind") == "ask" else card.get("result")) or "已结束。"
    return None


def render(card, token):
    """卡片现在的样子：开着时每个选项一个按钮（按钮只带令牌、选项号和这一版的版本号），结束后写结果、不再有按钮。
    不放文字输入框：网关转不来表单内容，秘书也不写自由文字。"""
    is_open = card["state"] == "open"
    buttons = [{"tag": "button", "text": {"tag": "plain_text", "content": label},
                "type": "primary" if i == card.get("recommend") else "default", "width": "fill",
                "behaviors": [{"type": "callback", "value": {"autoloop": token, "o": i, "v": card["version"]}}]}
               for i, label in enumerate(card.get("options") or [])] if is_open and card.get("kind") != "fyi" else []
    done = outcome(card)
    header = {"title": {"tag": "plain_text", "content": card.get("title") or "中台卡片"},
              "template": "blue" if is_open else ("grey" if card["state"] == "closed" else "green")}
    if card.get("rule") and card.get("rule") != card.get("title"):
        header["subtitle"] = {"tag": "plain_text", "content": f"来源：{card['rule']}"}
    return {"schema": "2.0", "config": {"update_multi": True, "enable_forward": False}, "header": header,
            "body": {"elements": [*([{"tag": "markdown", "content": card["text"]}] if card.get("text") else []),
                                  *buttons, *([{"tag": "markdown", "content": f"**{done}**"}] if done and not is_open else [])]}}


# ---------- 发送 ----------
def owner_chat():
    chat = BOT.owner_chat()
    if not (chat or "").startswith("oc_"):
        raise Fail(BOT.no_chat)
    return chat


def token_for(card, chat):
    """同一张卡只有一个令牌：先登记再发，重发时沿用。"""
    with db(write=True) as con:
        r = con.execute("SELECT token FROM feishu_cards WHERE card_id = ?", (card["id"],)).fetchone()
        if r:
            return r["token"]
        token = secrets.token_hex(8)
        con.execute("INSERT INTO feishu_cards (card_id, token, chat_id, kind, options, version) VALUES (?, ?, ?, ?, ?, ?)",
                    (card["id"], token, chat, card.get("kind") or "ask", len(card.get("options") or []), card["version"]))
        return token


def send_card(chat, content, key):
    out = BOT.lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "interactive",
                "--content", json.dumps(content, ensure_ascii=False), "--idempotency-key", key], "发卡片")
    mid = out.get("message_id")
    if not mid:
        raise SendError("发卡片没拿到消息号")
    return mid


def deliver(card):
    """发一张卡（ref 为空）或改那条消息（ref 不为空）；返回消息号。持桥接锁做（锁外的直接调用也串行、也核版本）。
    直接调用也过恢复闸（L3 第 4 轮 P1-06）：入口看一眼、锁里再核，关着抛 Deferred（暂缓，不是失败）。"""
    import autoloop_exits as E
    import autoloop_recovery as R
    try:
        epoch = R.ensure_open()
    except E.Refused as e:
        raise Deferred(str(e))
    with bridge_lock():
        _still_open(epoch)
        return _deliver(card)


def _deliver(card):
    """deliver 的内部实现，调用方必须已持桥接锁。改飞书之前先核本地登记的版本（专项续审 P2-03）。"""
    with db() as con:
        r = con.execute("SELECT version FROM feishu_cards WHERE card_id = ?", (card["id"],)).fetchone()
    if r and r["version"] > card["version"]:
        raise Fail(f"本地已登记第 {r['version']} 版，不把消息改回第 {card['version']} 版")
    if card.get("ref"):
        with db() as con:
            r = con.execute("SELECT token, message_id, chat_id FROM feishu_cards WHERE card_id = ?", (card["id"],)).fetchone()
        if not r and card["state"] == "open":          # 开着、却不是本桥接发的卡：不改（改了按钮也对不上任何登记）
            raise Fail("这张卡不是飞书桥接发的，改不了")
        if r and r["message_id"] and r["message_id"] != card["ref"]:   # 中台记的消息号和登记的不一致：不猜，不改
            raise Fail(f"中台给的消息号和登记的对不上（卡片 {card['id']}）")
        content = render(card, r["token"] if r else "")
        try:
            BOT.lark(["im", "messages", "patch", "--message-id", card["ref"],
                  "--data", json.dumps({"content": json.dumps(content, ensure_ascii=False)}, ensure_ascii=False)], "改卡片")
        except SendError:
            if card["state"] == "open":                # 开着的卡：原消息的按钮还能用，不另发
                raise
            if not r:                                  # 本机没登记过这张卡：证明不了原消息发给的是现在绑定的人，不另发（#255 L3 P2-14）
                raise Fail(f"卡片 {card['id']} 本机没有发送登记，改不了原消息，也不另发")
            chat = owner_chat()
            if r["chat_id"] != chat:                   # 发卡后换绑过：结果不另发到新私聊（门卫 #255 独立复核 P2-6）
                raise Fail(f"卡片 {card['id']} 发在换绑前的私聊，改不了原消息，也不另发到新私聊")
            send_card(chat, content, f"autoloop-card-{card['id']}-v{card['version']}")
        with db(write=True) as con:                    # 开着的卡改版送到了：送达时间从这一版起算（结束的卡不再计时）
            con.execute("UPDATE feishu_cards SET delivered_at = CASE WHEN ? AND version != ? THEN ? ELSE delivered_at END, "
                        "hub_delivered_at = CASE WHEN version != ? THEN NULL ELSE hub_delivered_at END, "
                        "version = ? WHERE card_id = ? AND version <= ?",          # 版本不回退（P2-01）
                        (card["state"] == "open", card["version"], now(), card["version"], card["version"], card["id"],
                         card["version"]))
        return card["ref"]
    chat = owner_chat()
    token = token_for(card, chat)
    content = render(card, token)
    import autoloop_exits as E

    def do():
        try:                                       # 幂等键只用卡片号：报告丢了重发不出第二条
            return {"message_id": send_card(chat, content, f"autoloop-card-{card['id']}")}
        except SendError as e:                     # 只有 lark-cli 在本机根本没跑起来，才算确定没发出去（审查 P1-05）；
            msg = str(e)                           # 飞书报错、超时、输出读不懂、没拿到消息号：请求可能已到飞书，结果不明
            if "跑不起来" in msg and "Timeout" not in msg:
                raise E.Definite(msg)
            raise
    try:                                           # 写前账本（批 0D）：先记要发，再发；结果不明不重发，对账按令牌找消息
        got = E.guard("send_feishu", f"send_feishu:card-{card['id']}", f"card:{card['id']}",
                      {"chat": chat, "marker": token, "msg_type": "interactive"}, do)
    except E.Definite as e:
        raise SendError(str(e))
    except E.Refused as e:
        raise Deferred(str(e))
    except Exception as e:                         # 结果不明（出口账本已记、闸已关）：也是暂缓，不报 failed（第 2 轮预审 P2-B）
        raise Deferred(f"发卡结果不明，冻结这张卡等对账：{type(e).__name__}: {str(e)[:200]}")
    mid = got.get("message_id")
    if not mid:                                    # 账本说发过却没有消息号（人工结算没填）：不猜，暂缓（第 2 轮预审 P2-B2）
        raise Deferred(f"出口账本记这张卡已发出，但没有消息号（卡片 {card['id']}），请常浩按消息号结算")
    with db(write=True) as con:
        con.execute("UPDATE feishu_cards SET message_id = ?, version = ?, "
                    "delivered_at = CASE WHEN delivered_at IS NULL OR version != ? THEN ? ELSE delivered_at END, "
                    "hub_delivered_at = CASE WHEN version != ? THEN NULL ELSE hub_delivered_at END "
                    "WHERE card_id = ? AND version <= ?",                        # 版本不回退（P2-01）
                    (mid, card["version"], card["version"], now(), card["version"], card["id"], card["version"]))
    return mid


def newer_elsewhere(card):
    """这次领到的卡能不能处理：返回 None（核过了），或 (种类, 原因)。种类 unreadable：中台库读不到这张卡或版本不是整数
    （读不到不等于没有新版本：不发，fail-closed）；stale：中台已是更新的版本（中台 1 分钟后递最新版）；conflict：本地登记的
    版本比中台的还新（中台库可能被恢复成旧快照：中台下次还是递这一版，要报 failed 走退避、写明原因，#218 第 6 轮 P2-01）。"""
    import autoloop_publish as P
    cur = P.hub_card(card["id"])
    if not cur or type(cur.get("version")) is not int:
        return "unreadable", "读不到中台库里这张卡的版本，核不了是不是最新版"
    if cur["version"] > card["version"]:
        return "stale", f"中台这张卡已经是第 {cur['version']} 版"
    with db() as con:
        r = con.execute("SELECT version FROM feishu_cards WHERE card_id = ?", (card["id"],)).fetchone()
    if r and r["version"] > card["version"]:
        return "conflict", f"本地已登记第 {r['version']} 版，比中台的第 {card['version']} 版还新（中台库可能被恢复成旧快照）"
    return None


def hub_readable():
    """中台库能不能读（只读打开、能查 cards）。读不到返回 False（不领卡：领一次就用掉中台一次尝试）。"""
    import autoloop_publish as P
    try:
        con = P._hub()
        try:
            con.execute("SELECT id FROM cards LIMIT 1").fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError, Fail):
        return False
    return True


def given_up_cards():
    """中台库只读：feishu 通道已放弃送达的卡 [{card_id, title, tries, error, first_try_at, ref, state, fyi, created_at}]；
    读不到返回 None。first_try_at：这一轮第一次尝试的时间（重新送后从头记，用来认「这是第几轮放弃」）；ref：之前送到过的
    那条消息；state、fyi、created_at：卡片现在的状态、是不是仅告知的通知、什么时候建的（判断重新送有没有用）。
    次数是 0 且没有第一次尝试时间的不算：那是放弃之后送达报告补上了（中台 delivered 清这两项、不清放弃标记），已经送到。"""
    import autoloop_publish as P
    try:
        con = P._hub()
        try:
            rows = con.execute("SELECT d.card_id, c.title, d.tries, d.error, d.first_try_at, d.ref, c.state, c.fyi, "
                               "c.created_at FROM deliveries d LEFT JOIN cards c ON c.id = d.card_id "
                               "WHERE d.channel = ? AND d.given_up = 1 AND NOT (d.first_try_at IS NULL AND d.tries = 0) "
                               "ORDER BY d.card_id", (CHANNEL,)).fetchall()
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError, Fail):
        return None
    return [dict(r) for r in rows]


def maintenance():
    """维护停写（恢复闸 hold）中返回恢复点编号，否则 None；读不了出口账本当作在维护（宁可不发）。
    维护期间给常浩的提醒也停（L3 第 4 轮 P1-06，常浩 11-21 选甲），维护结束后下一轮照常补上；平时闸关着照常提醒。"""
    import autoloop_recovery as R
    try:
        return (R.gate_row() or {}).get("hold")
    except Exception:
        return "?"


def alert(key, text, every=ALERT_EVERY):
    """私聊提醒常浩；同一提醒键 every 秒内只提醒一次（every=None：只提醒一次）。发不出去不记，下次再试。返回是否发了。
    持桥接锁做、锁里先看是否在维护（维护停写排空看得见它；在维护就不发不记，返回 False）。调用方不能已持桥接锁。"""
    with bridge_lock():
        if maintenance():
            return False
        return _alert(key, text, every)


def _alert(key, text, every):
    with db() as con:
        r = con.execute("SELECT at FROM alerts WHERE key = ?", (key,)).fetchone()
    if r:
        try:
            age = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(r["at"])).total_seconds()
        except (TypeError, ValueError):
            age = float("inf")
        if every is None or age < every:
            return False
    try:
        BOT.send_text(owner_chat(), text, f"al-alert-{secrets.token_hex(8)}")   # 飞书幂等键限 50 字
    except (SendError, Fail):
        return False
    with db(write=True) as con:
        con.execute("INSERT OR REPLACE INTO alerts (key, at) VALUES (?, ?)", (key, now()))
    return True


FYI_WINDOW = 24 * 3600                   # 秒：仅告知的通知建了这么久以后中台不再送（中台 worthSending）


def given_up_todo(c, now=None):
    """放弃提醒的后半句：这张卡重新送有没有用、常浩要做什么。照中台 kernel/decide.ts 的 worthSending：
    卡片开着、或之前送到过（有 ref）、或是建了不到 24 小时的仅告知通知，重新送才会发。"""
    cmd = f"agent-autoloop redeliver {c['card_id']}"
    if c.get("state") in (None, "open"):
        return f"这张卡不会再自动送到你这里。要让它重新送，在电脑终端运行：{cmd}"
    if c.get("ref"):                             # 已回答或结案、之前送到过：没送到的只是把那条消息更新成最后结果
        return f"这张卡已经处理完，只是飞书里那条消息没更新成最后结果，可以不管；想更新就在电脑终端运行：{cmd}"
    try:
        made = dt.datetime.fromisoformat(str(c.get("created_at")).replace("Z", "+00:00"))
        age = ((now or dt.datetime.now().astimezone()) - made).total_seconds()
    except (TypeError, ValueError):
        age = float("inf")
    if c.get("fyi") and age < FYI_WINDOW:
        return f"这是一条只告知你的通知，还没送到你这里。要让它重新送，在电脑终端运行：{cmd}（通知发出满 24 小时后就不会再送）"
    return "这张卡没送到你这里就已经结束（或是发出超过 24 小时的通知），重新送也不会再发。"


def check_given_up():
    """把中台已放弃送达的卡逐张私聊告诉常浩，并给出重新送的命令（#278）。每一轮放弃只说一次：提醒键带这一轮第一次尝试的
    时间，常浩重新送（agent-autoloop redeliver）之后又放弃是新的一轮，再说一次。返回这次提醒了的卡片号。"""
    told = []
    for c in given_up_cards() or []:
        cid = c["card_id"]
        with db() as con:                            # 这张卡以前提醒过别的一轮：这次是重新送之后又放弃
            again = con.execute("SELECT 1 FROM alerts WHERE key LIKE ? LIMIT 1", (f"given-up:{cid}:%",)).fetchone()
        why = (c.get("error") or "没有记录原因")[:120]
        what = "重新送之后又放弃了送达" if again else "已放弃送达"
        text = (f"飞书桥接：中台卡片 {cid}「{c.get('title') or '（没有标题）'}」{what}（试了 {c.get('tries')} 次，"
                f"最后原因：{why}）。{given_up_todo(c)}")
        if alert(f"given-up:{cid}:{c.get('first_try_at') or ''}", text, every=None):
            told.append(cid)
    return told


def once(wait=50, ready=None):
    """领一张、处理一张；没有卡片返回 None。恢复闸关着（批 0D）就不领卡（返回 paused）；领卡前先确认中台库能读（读不到这轮不领，
    返回 paused）；处理在桥接锁里做，锁里先核这张卡的版本；报完送达放锁，再提交排队的本人回答。
    ready（门卫用，同 run）：长轮询要等一阵，领到卡后发卡前再问一次；这时不能干活（收点击的长连接断了）就不发，
    向中台报 failed 写明原因（中台退避重递）。这一问只在桥接锁里、核过恢复闸之后做：报 failed 也是写中台，维护停写时
    不许写（#255 L3 P1-05）。猛鬼兽不传，行为不变。"""
    import autoloop_exits as E
    import autoloop_recovery as R
    try:
        epoch = R.ensure_open()
    except E.Refused as e:
        return {"ok": False, "paused": str(e)}
    if not hub_readable():                       # 第 5 轮 P1-01：领一次就用掉中台一次尝试，读不到就先别领
        why = "读不到中台库，暂停领卡"
        alert("hub-unreadable", f"飞书桥接：{why}，中台卡片暂时送不到你这里（不会用掉中台的重试次数）；中台库恢复后自动继续。")
        return {"ok": False, "paused": why}
    card = hub("GET", f"/v1/cards/next?wait={wait}", timeout=wait + 20).get("card")
    if not card:
        return None
    def not_ready():
        try:
            return ready() if ready else None
        except Exception as e:                   # 核对本身出错也报 failed，不让卡空等中台租约过期（复核 P2-6）
            return f"领到卡后核对能不能发时出错（{type(e).__name__}）"
    with bridge_lock():
        try:
            _still_open(epoch)
        except Deferred as e:                    # 领卡之后闸关了：什么都不写，中台过一会儿再给这张卡
            return {"card": card["id"], "ok": False, "deferred": True, "why": str(e)}
        why = not_ready()                        # 长轮询和等锁期间长连接都可能断了：锁里、核过闸才问（#255 复核 P2-5、L3 P1-05）
        if why:
            hub("POST", f"/v1/cards/{card['id']}/failed", {"version": card["version"], "error": why[:300]})
            return {"card": card["id"], "ok": False, "paused": why}
        got = newer_elsewhere(card)
        if got and got[0] == "stale":            # 旧版本：不改消息、不报送达，中台 1 分钟后递最新版本
            return {"card": card["id"], "ok": True, "skipped": f"领到的是第 {card['version']} 版，{got[1]}，不处理"}
        if got:                                  # 领到后读不到 / 版本冲突：报 failed 写明原因（中台退避重试、状态里看得到）
            why = f"{got[1]}，这次没发"
            try:                                 # 报失败本身出错也不能吞掉提醒（#218 第 6 轮 P2-02）
                hub("POST", f"/v1/cards/{card['id']}/failed", {"version": card["version"], "error": why})
                reported = None
            except Fail as e:
                reported = str(e)[:80]
        else:
            try:
                ref = _deliver(card)
            except Deferred as e:                # 暂缓不是失败（批 0D 审查 P1-04）：不报 failed，中台过一会儿会再给这张卡
                return {"card": card["id"], "ok": False, "deferred": True, "why": str(e)}
            except (SendError, Fail) as e:
                hub("POST", f"/v1/cards/{card['id']}/failed", {"version": card["version"], "error": str(e)[:300]})
                return {"card": card["id"], "ok": False, "why": str(e)}
            _report_delivered(card["id"])
    if got:                                      # 放锁后再提醒（不占着锁发消息）
        if reported is None:
            key = "hub-unreadable" if got[0] == "unreadable" else f"version-conflict:{card['id']}"
            alert(key, f"飞书桥接：卡片 {card['id']} {why}；中台会稍后重试。")
        else:                                    # 中台没记上：单独的提醒键，不被上面的限频压掉
            alert(f"report-failed:{card['id']}", f"飞书桥接：卡片 {card['id']} {why}；向中台报「没发成」也失败了"
                  f"（{reported}），中台可能没记上这次失败，请人工核查这张卡。")
        return {"card": card["id"], "ok": False, "why": why, **({"report_error": reported} if reported else {})}
    _flush(card["id"])                           # 锁外提交（第 5 轮 P2 建议）
    return {"card": card["id"], "ok": True, "ref": ref}


def report_delivered(card_id):
    """持桥接锁报送达（对账补报走这里，专项续审 P2-02）。往中台写，过恢复闸：入口看一眼，锁里再核（闸关了抛 Deferred）。"""
    import autoloop_exits as E
    import autoloop_recovery as R
    try:
        epoch = R.ensure_open()
    except E.Refused as e:
        raise Deferred(str(e))
    with bridge_lock():
        _still_open(epoch)
        _report_delivered(card_id)


def _report_delivered(card_id):
    """report_delivered 的内部实现，调用方必须已持桥接锁。按登记的版本和消息号报送达（发卡后先落盘、再报，所以崩溃后可以照登记的原样重报；中台对同一 ref 重报是幂等的），
    中台收下后记「中台确认送达」。登记里没有消息号就不报。"""
    with db() as con:
        r = con.execute("SELECT message_id, version FROM feishu_cards WHERE card_id = ?", (card_id,)).fetchone()
    if not r or not r["message_id"]:
        raise Fail(f"卡片 {card_id} 还没有登记的消息号，不能报送达")
    hub("POST", f"/v1/cards/{card_id}/delivered", {"version": r["version"], "ref": r["message_id"]})
    with db(write=True) as con:                        # 只给报过的这一版记确认：报送期间又改了版就不记（#218 第 2 轮 P1-03）
        con.execute("UPDATE feishu_cards SET hub_delivered_at = COALESCE(hub_delivered_at, ?) WHERE card_id = ? AND version = ?",
                    (now(), card_id, r["version"]))


def confirm(card_id):
    """报送达（过恢复闸，同 report_delivered；关着抛 Deferred，L3 第 4 轮 P1-06），放锁后再把送达前点的、排着队的本人回答
    提交掉（手动补报、测试用）。"""
    report_delivered(card_id)
    _flush(card_id)


def _flush(card_id):
    """提交排队的本人回答（发送资格只有一个执行者领得到，submit 自己会再核版本，见 autoloop_answer）。不持桥接锁。"""
    import autoloop_answer as A
    A.flush(card_id)


def run(ready=None):
    """常驻。ready：每轮先问一次能不能干活（门卫没绑定常浩时返回原因：这时不对账、不领卡，领了也发不出去，
    还会用掉中台的重试次数），返回 None 才干活。"""
    import autoloop_answer as A
    held_back = []                                     # 维护刚开始时被压下的对账回话：只在内存里，维护结束后补发
    paused = None
    while True:
        try:
            held = maintenance()
            why = None
            if held:                                   # 维护停写：对账回话、放弃提醒、领卡都停（P1-06），结束后照常补上
                why = (f"维护中（恢复点 {held}）" if held != "?"
                       else "读不了出口账本的闸状态，当作维护中、先不发提醒（核对出口账本）")
            elif ready:                                # 门卫：没绑定 / 收点击的长连接不在，不对账、不领卡
                why = ready()
            if why:
                if why != paused:                      # 只在暂停原因变化时记一行
                    print(json.dumps({"at": now(), "ok": False, "paused": why}, ensure_ascii=False), flush=True)
                paused = why
                time.sleep(30)
                continue
            paused = None
            notes, held_back = held_back, []
            for cid, text in A.reconcile():            # 在途过久、结果不明、排队的本人回答，每轮先对一次账
                print(json.dumps({"at": now(), "card": cid, "reconcile": text}, ensure_ascii=False), flush=True)
                notes.append((cid, text))
            for cid, text in notes:                    # 对账改了状态之后维护才开始：这句回话不会再生成，留着下一轮补发
                if not A.tell(A.identities()[1], text):
                    held_back.append((cid, text))
            for cid in check_given_up():               # 中台已放弃送达的卡：每一轮放弃私聊说一次
                print(json.dumps({"at": now(), "card": cid, "given_up": True}, ensure_ascii=False), flush=True)
            out = once(ready=ready)
            if out:
                print(json.dumps({"at": now(), **out}, ensure_ascii=False), flush=True)
            if out and out.get("paused"):              # 恢复闸关着、或中台库读不到：歇 30 秒再看，别空转
                time.sleep(30)
        except Exception as e:                         # 常驻不因一次出错退出：记下来，10 秒后再领
            why = str(e) if isinstance(e, Fail) else f"{type(e).__name__}: {str(e)[:200]}"
            print(json.dumps({"at": now(), "ok": False, "why": why}, ensure_ascii=False), flush=True)
            time.sleep(10)


def show():
    with db() as con:
        cards = [dict(r) for r in con.execute("SELECT card_id, message_id, kind, options, version, delivered_at, hub_delivered_at "
                                              "FROM feishu_cards ORDER BY card_id")]
        claims = [dict(r) for r in con.execute("SELECT * FROM claims ORDER BY card_id, who")]
        log = [dict(r) for r in con.execute("SELECT * FROM claim_log ORDER BY id DESC LIMIT 50")]
    return {"cards": cards, "claims": claims, "recent_log": log}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("once")
    sub.add_parser("show").add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "run":
            run()
        out = once() if a.cmd == "once" else show()
    except Fail as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1 if a.cmd == "show" else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
