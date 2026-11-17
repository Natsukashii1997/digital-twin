#!/usr/bin/env python3
"""飞书桥接：中台 feishu 通道（批 0B-4 第 4a 批，#195；规格 docs/秘书代批-规格.md §6、§7，0B-3 实测结论）。

用 feishu 通道钥匙向中台领卡片，以猛鬼兽机器人发到常浩私聊，报送达；卡片状态变了就改那条消息。点击不在这里收：
猛鬼兽网关的 merge-buttons 插件把按钮点击转给 autoloop_answer.py（这里只负责发卡和登记）。

0B-3 实测：网关转来的点击里有点击人 open_id 和聊天号，**没有原消息号**。所以每张卡发出前先登记一个随机令牌，
按钮里只带 {"autoloop": 令牌, "o": 选项号}；点击回来按令牌找到卡片号和送达的消息号（中台要求 ref 和送达的那条一致）。
同一张卡只有一个令牌：送达报告丢了、中台再给一次时，用同一个幂等键重发，飞书回原消息，令牌也不变。

卡片库 cards.db（DigitalTwin/autoloop/，600）：
  feishu_cards  卡片号 → 令牌、送达的消息号、私聊号、种类、选项数、版本、飞书送达时间（秘书白天等 30 分钟从这里起算）、
                中台确认送达时间（报 delivered 成功后才有；没有之前本人的点击只排队，不提交，见 autoloop_answer）
  claims        每张卡每方一行（owner 本人 / secretary 秘书）和进度；claim_log 只追加，记每次状态变化
  和第 4c 批秘书的契约（规格 §7、§10 第 4 条「秘书获准送达前本人优先；获准后以中台先受理的为准」）：
    - 秘书从 reserved 改成 approved（唯一的送达授权点）必须在 db(write=True) 里做，并先调 owner_holds()：
      本人已有 queued / inflight / unknown / answered 的一行，秘书就不许获准（作废自己的预占）；
    - 本人一侧从不改秘书的行：秘书已 approved 时本人照样提交，谁先被中台受理算谁，后到的一方收到 changed=false。

  autoloop_feishu.py run          常驻：长轮询领卡片、发送、报送达（装成常驻服务是 D 档，要常浩说「装」）
  autoloop_feishu.py once         只领一次（测试、手动补发）
  autoloop_feishu.py show [--json]  只读：登记过的卡片、答卷进度和最近的变化记录
环境变量：DT_AUTOLOOP_CARDS_DB、DT_AUTOLOOP_CHANNEL_KEY_DIR（同 autoloop_config）、AGENT_AUTOLOOP_HOME、DT_LARK_CLI。
不调用 AI。
"""
import argparse
import contextlib
import datetime as dt
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
from tidings_weekly import SendError, lark  # noqa: E402

CHANNEL = "feishu"
TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")
OWNER_HOLDS = ("queued", "inflight", "unknown", "answered")
SCHEMA = """
CREATE TABLE IF NOT EXISTS feishu_cards (card_id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE, message_id TEXT,
  chat_id TEXT NOT NULL, kind TEXT NOT NULL, options INTEGER NOT NULL, version INTEGER NOT NULL, delivered_at TEXT,
  hub_delivered_at TEXT);
CREATE TABLE IF NOT EXISTS claims (card_id INTEGER NOT NULL, who TEXT NOT NULL CHECK (who IN ('owner', 'secretary')),
  state TEXT NOT NULL, choice INTEGER, at TEXT NOT NULL, pid INTEGER, note TEXT, attempt TEXT, PRIMARY KEY (card_id, who));
CREATE TABLE IF NOT EXISTS claim_log (id INTEGER PRIMARY KEY AUTOINCREMENT, card_id INTEGER NOT NULL, who TEXT NOT NULL,
  at TEXT NOT NULL, state TEXT NOT NULL, choice INTEGER, note TEXT, attempt TEXT);
"""


class Fail(Exception):
    """说人话的原因，不带钥匙。"""


class HubError(Fail):
    """中台调用失败。sent=False：请求根本没发出去（钥匙、配置读不到）；status：中台回的 HTTP 码，None 表示没拿到有效回应。"""
    def __init__(self, msg, status=None, sent=True):
        super().__init__(msg)
        self.status, self.sent = status, sent


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
        con.executescript(SCHEMA)
        if write:
            con.execute("BEGIN IMMEDIATE")
        yield con
        if write:
            con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def card_by_token(token):
    with db() as con:
        r = con.execute("SELECT * FROM feishu_cards WHERE token = ?", (token,)).fetchone()
    return dict(r) if r else None


# ---------- 中台（feishu 通道钥匙） ----------
def channel_key():
    f = C.channel_key_file(CHANNEL)
    try:
        key = f.read_text(encoding="utf-8").strip()
    except OSError:
        raise Fail("没有 feishu 通道钥匙：常浩说「签」以后 autoloop_config.py issue-channel-key feishu 才有")
    if not C.KEY_RE.match(key):
        raise Fail("feishu 通道钥匙文件格式不对")
    return key


def hub_target():
    """发请求前要的东西：中台地址和通道钥匙。读不到就抛 HubError(sent=False)：请求还没发出去。"""
    try:
        url, _ = C.hub_config()
    except Exception as e:                       # config.json 读不到、缺字段：一律算没发出去
        raise HubError(f"读不到中台配置（{type(e).__name__}）", sent=False)
    try:
        return url, channel_key()
    except Fail as e:
        raise HubError(str(e), sent=False)


def hub(method, path, body=None, timeout=20):
    url, key = hub_target()
    req = urllib.request.Request(url + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
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
    """卡片现在的样子：开着时每个选项一个按钮（按钮只带令牌和选项号），结束后写结果、不再有按钮。
    不放文字输入框：网关转不来表单内容，秘书也不写自由文字。"""
    is_open = card["state"] == "open"
    buttons = [{"tag": "button", "text": {"tag": "plain_text", "content": label},
                "type": "primary" if i == card.get("recommend") else "default", "width": "fill",
                "behaviors": [{"type": "callback", "value": {"autoloop": token, "o": i}}]}
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
    import twin_state as T
    try:
        chat = T.owner_chat_id()
    except T.StateError as e:
        raise Fail(str(e))
    if not (chat or "").startswith("oc_"):
        raise Fail("没配置常浩私聊的 chat_id（先 twin_identity.py setup-chat），或格式不对")
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
    out = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "interactive",
                "--content", json.dumps(content, ensure_ascii=False), "--idempotency-key", key], "发卡片")
    mid = out.get("message_id")
    if not mid:
        raise SendError("发卡片没拿到消息号")
    return mid


def deliver(card):
    """发一张卡（ref 为空）或改那条消息（ref 不为空），然后报送达；返回消息号。"""
    if card.get("ref"):
        with db() as con:
            r = con.execute("SELECT token, message_id FROM feishu_cards WHERE card_id = ?", (card["id"],)).fetchone()
        if not r and card["state"] == "open":          # 开着、却不是本桥接发的卡：不改（改了按钮也对不上任何登记）
            raise Fail("这张卡不是飞书桥接发的，改不了")
        if r and r["message_id"] and r["message_id"] != card["ref"]:   # 中台记的消息号和登记的不一致：不猜，不改
            raise Fail(f"中台给的消息号和登记的对不上（卡片 {card['id']}）")
        content = render(card, r["token"] if r else "")
        try:
            lark(["im", "messages", "patch", "--message-id", card["ref"],
                  "--data", json.dumps({"content": json.dumps(content, ensure_ascii=False)}, ensure_ascii=False)], "改卡片")
        except SendError:
            if card["state"] == "open":                # 开着的卡：原消息的按钮还能用，不另发
                raise
            send_card(owner_chat(), content, f"autoloop-card-{card['id']}-v{card['version']}")
        with db(write=True) as con:
            con.execute("UPDATE feishu_cards SET version = ? WHERE card_id = ?", (card["version"], card["id"]))
        return card["ref"]
    chat = owner_chat()
    token = token_for(card, chat)
    mid = send_card(chat, render(card, token), f"autoloop-card-{card['id']}")   # 幂等键只用卡片号：报告丢了重发不出第二条
    with db(write=True) as con:
        con.execute("UPDATE feishu_cards SET message_id = ?, version = ?, delivered_at = COALESCE(delivered_at, ?) "
                    "WHERE card_id = ?", (mid, card["version"], now(), card["id"]))
    return mid


def once(wait=50):
    """领一张、处理一张；没有卡片返回 None。"""
    card = hub("GET", f"/v1/cards/next?wait={wait}", timeout=wait + 20).get("card")
    if not card:
        return None
    try:
        ref = deliver(card)
    except (SendError, Fail) as e:
        hub("POST", f"/v1/cards/{card['id']}/failed", {"version": card["version"], "error": str(e)[:300]})
        return {"card": card["id"], "ok": False, "why": str(e)}
    confirm(card["id"])
    return {"card": card["id"], "ok": True, "ref": ref}


def report_delivered(card_id):
    """按登记的版本和消息号报送达（发卡后先落盘、再报，所以崩溃后可以照登记的原样重报；中台对同一 ref 重报是幂等的），
    中台收下后记「中台确认送达」。登记里没有消息号就不报。"""
    with db() as con:
        r = con.execute("SELECT message_id, version FROM feishu_cards WHERE card_id = ?", (card_id,)).fetchone()
    if not r or not r["message_id"]:
        raise Fail(f"卡片 {card_id} 还没有登记的消息号，不能报送达")
    hub("POST", f"/v1/cards/{card_id}/delivered", {"version": r["version"], "ref": r["message_id"]})
    with db(write=True) as con:
        con.execute("UPDATE feishu_cards SET hub_delivered_at = COALESCE(hub_delivered_at, ?) WHERE card_id = ?",
                    (now(), card_id))


def confirm(card_id):
    """报送达，再把送达前点的、排着队的本人回答提交掉（发送资格只有一个执行者领得到，见 autoloop_answer.submit）。"""
    report_delivered(card_id)
    import autoloop_answer as A
    A.flush(card_id)


def run():
    import autoloop_answer as A
    while True:
        try:
            for cid, text in A.reconcile():            # 在途过久、结果不明、排队的本人回答，每轮先对一次账
                print(json.dumps({"at": now(), "card": cid, "reconcile": text}, ensure_ascii=False), flush=True)
                A.tell(A.identities()[1], text)
            out = once()
            if out:
                print(json.dumps({"at": now(), **out}, ensure_ascii=False), flush=True)
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
