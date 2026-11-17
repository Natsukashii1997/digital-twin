#!/usr/bin/env python3
"""常浩点中台卡片按钮后的处理（批 0B-4 第 4a 批，#195；规格 docs/秘书代批-规格.md §6、§7「本人点击锁」，0B-3 实测）。

merge-buttons 插件把 {"autoloop": 令牌, "o": 选项号} 的点击转来：
  autoloop_answer.py <令牌> <选项号> --chat <网关给的聊天号> --operator <网关给的点击人>
  autoloop_answer.py reconcile [--card <卡片号>]     对账（桥接常驻时每轮也会跑）
核对（缺一样就不答）：点击人 = 常浩 open_id、聊天号 = 常浩私聊（网关报的聊天类型不可信，不看）——这两样不对就**不回话**，
只记日志（防止别处的人反复打 /card 文本骚扰私聊）；令牌登记过、已有送达消息号、登记的私聊和现在的一致、选项号在范围。

本人这一行（claims 表 who = owner）的状态：
  queued    中台还没确认送达（桥接已发飞书、还没报 delivered 成功）：只记下选择，桥接报送达成功后马上提交（flush）
  inflight  正在提交；记着进程号
  answered  中台受理了，回读 state、choice、by 都对上
  settled   中台说这张卡已经有了回答（秘书或别处先到），按中台的结果结算
  unknown   结果不明：连不上、超时、HTTP 5xx、回应读不懂、回读对不上。**不重答**（中台只认第一次，重答可能变成第二个回答）
  （释放）  能证明中台没受理时删掉这一行（claim_log 留底），常浩可以再点：请求没发出去、中台明确拒绝（400/401/403/404/409）、
            或对账时确认「发送进程已退出、过了 60 秒、卡仍开着」
对账（reconcile）：queued 且已确认送达 → 提交；inflight 超过 2 分钟且进程已不在（或超过 10 分钟，防进程号被复用）、unknown →
用只读窗口回读：已决定且回答人是常浩、选项一致 → answered；已决定 / 已结束但不是这一答 → settled；仍开着 → 满足上面的释放条件才释放，
否则保持 unknown。读不到就不动。
秘书：本人一侧从不改秘书的行；秘书已获准送达时本人照样提交，以中台先受理的为准（契约见 autoloop_feishu 头注释）。

已知局限（PR #202 审查 P1-01）：点击人、聊天号是网关传进来的命令行参数；和猛鬼兽同一个 macOS 账号的进程
（包括有终端工具的猛鬼兽模型）既能伪造这些参数，也能直接读 feishu 通道钥匙文件。同账号内没有软件办法堵住。
常浩 2026-11-17 选甲：本批代码合并但**不签 feishu 通道钥匙、不装桥接**；隔离在 ADR-015 定（方向：独立账号 dt-gate +
单独的飞书机器人直接收点击，通道钥匙只放在那个账号里），0C 前定，定好后桥接搬过去再启用。

不调用 AI。退出码：0 已答上；1 没答；2 结果不明；3 已排队等送达确认。
"""
import argparse
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_feishu as F  # noqa: E402

DEFINITE = (400, 401, 403, 404, 409)     # 中台明确没受理
STALE = 120                              # 秒：在途超过这么久、发送进程已不在 → 对账
HARD_STALE = 600                         # 秒：不管进程号还在不在都对账（进程号可能被复用）
GRACE = 60                               # 秒：发送进程退出后至少等这么久、卡仍开着，才认定中台没受理
SECRETARY_LIVE = ("approved", "delivered", "unknown")


class Quiet(F.Fail):
    """来源不对（不是常浩、不是私聊、令牌格式不对、本机没配身份）：不回话，只记日志。"""


def identities():
    import twin_state as T
    try:
        owner, home = T.owner_open_id(), T.owner_chat_id()
    except T.StateError as e:
        raise Quiet(str(e))
    if not owner or not home:                    # 没配置就一律不认，不拿空值去比
        raise Quiet("本机没配置常浩的 open_id 或私聊号（twin_identity.py setup / setup-chat）")
    return owner, home


def check(token, option, chat, operator):
    owner, home = identities()
    if not operator or operator != owner:
        raise Quiet("点按钮的不是常浩本人")
    if not chat or chat != home:
        raise Quiet("不是在常浩和猛鬼兽的私聊里点的")
    if not F.TOKEN_RE.match(token or ""):
        raise Quiet("按钮里的令牌格式不对")
    card = F.card_by_token(token)
    if not card:
        raise F.Fail("这张卡没有登记过（不是飞书桥接发的）")
    if not card.get("message_id"):
        raise F.Fail("这张卡还没发成功")
    if card["chat_id"] != home:
        raise F.Fail("这张卡登记的私聊和现在的对不上")
    if not (0 <= option < card["options"]):
        raise F.Fail("选项号不在这张卡的选项里")
    return card


# ---------- 状态 ----------
def _age(at):
    try:
        return (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(at)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def owner_row(con, card_id):
    r = con.execute("SELECT * FROM claims WHERE card_id = ? AND who = 'owner'", (card_id,)).fetchone()
    return dict(r) if r else None


def set_state(card_id, state, note=None, pid=None):
    with F.db(write=True) as con:
        r = owner_row(con, card_id)
        if not r:
            return
        con.execute("UPDATE claims SET state = ?, at = ?, pid = ?, note = COALESCE(?, note) WHERE card_id = ? AND who = 'owner'",
                    (state, F.now(), pid, note, card_id))
        F.log_claim(con, card_id, "owner", state, r["choice"], note)


def release(card_id, note):
    with F.db(write=True) as con:
        r = owner_row(con, card_id)
        if r:
            con.execute("DELETE FROM claims WHERE card_id = ? AND who = 'owner'", (card_id,))
            F.log_claim(con, card_id, "owner", "released", r["choice"], note)


def claim(card, option):
    """登记本人这一答。返回 (状态, 附言)。已有本人的行就不再登记。"""
    reconcile(card["card_id"])                   # 上一次在途或结果不明的，先对一次账
    with F.db(write=True) as con:
        r = owner_row(con, card["card_id"])
        if r and r["state"] == "answered":
            raise F.Fail("这张卡你已经点过了，以第一次为准")
        if r and r["state"] == "settled":
            raise F.Fail("这张卡已经有了回答")
        if r:
            raise F.Fail("上一次点击还在处理或等对账，先别重复点")
        fresh = con.execute("SELECT hub_delivered_at FROM feishu_cards WHERE card_id = ?", (card["card_id"],)).fetchone()
        state = "inflight" if fresh and fresh["hub_delivered_at"] else "queued"
        sec = con.execute("SELECT state FROM claims WHERE card_id = ? AND who = 'secretary'", (card["card_id"],)).fetchone()
        note = "秘书已获准替你回答，以中台先受理的为准" if sec and sec["state"] in SECRETARY_LIVE else None
        con.execute("INSERT INTO claims (card_id, who, state, choice, at, pid, note) VALUES (?, 'owner', ?, ?, ?, ?, ?)",
                    (card["card_id"], state, option, F.now(), os.getpid() if state == "inflight" else None, note))
        F.log_claim(con, card["card_id"], "owner", state, option, note)
    return state, note


# ---------- 提交与回读 ----------
def read_card(card_id):
    """用只读窗口看中台里这张卡；读不到返回 None。"""
    import autoloop_readonly as R
    try:
        snap = R.read()
    except Exception:                            # 只读窗口自己的错误类型不止一种：一律算读不到
        return None
    for c in snap.get("cards", []) + snap.get("recentCards", []):
        if c.get("id") == card_id:
            return c
    return None


def judge(c, choice, owner):
    """按中台里的样子判：answered / settled / open / None（读不到）。"""
    if not c:
        return None
    if c.get("state") in ("decided", "done"):
        return "answered" if c.get("choice") == choice and c.get("by") == owner else "settled"
    return "settled" if c.get("state") == "closed" else "open"


def describe(c):
    if not c:
        return "读不到"
    who = "秘书" if c.get("by") == "秘书" else ("你" if c.get("by") else "无人")
    return f"{c.get('state')}，{who}选了第 {c.get('choice')} 项" if c.get("choice") is not None else str(c.get("state"))


def submit(card_id):
    """提交本人排着队或在途的一答。返回 (退出码, 给常浩的话)；没有可提交的返回 None。"""
    owner = identities()[0]
    with F.db(write=True) as con:
        r = owner_row(con, card_id)
        card = con.execute("SELECT * FROM feishu_cards WHERE card_id = ?", (card_id,)).fetchone()
        if not r or r["state"] not in ("queued", "inflight") or not card:
            return None
        if not card["hub_delivered_at"]:
            return 3, "已记下你的选择，卡片送达确认后马上交给中台。"
        con.execute("UPDATE claims SET state = 'inflight', at = ?, pid = ? WHERE card_id = ? AND who = 'owner'",
                    (F.now(), os.getpid(), card_id))
        F.log_claim(con, card_id, "owner", "inflight", r["choice"])
        choice, ref = r["choice"], card["message_id"]
    try:
        out = F.hub("POST", f"/v1/cards/{card_id}/answer", {"choice": choice, "by": owner, "ref": ref})
    except F.HubError as e:
        if not e.sent or e.status in DEFINITE:
            release(card_id, str(e))
            return 1, f"这次没交上：{e}。中台没有受理，你可以再点一次。"
        set_state(card_id, "unknown", str(e))
        return 2, f"这次回答结果不明（{e}），我不会重答，会自动对账后告诉你。"
    except Exception as e:                       # 请求可能已发出：按结果不明处理
        set_state(card_id, "unknown", f"{type(e).__name__}")
        return 2, "这次回答结果不明，我不会重答，会自动对账后告诉你。"
    c = read_card(card_id)
    if not out.get("changed"):
        set_state(card_id, "settled", f"中台说已有回答：{describe(c)}")
        return 1, f"这张卡已经有了回答（{describe(c)}），中台只认第一次，你这次没有改变结果。"
    if judge(c, choice, owner) == "answered":
        set_state(card_id, "answered")
        return 0, "收到，已交给中台。"
    set_state(card_id, "unknown", f"回读没对上：{describe(c)}")
    return 2, f"已交给中台，但回读没对上（{describe(c)}），会自动对账后告诉你。"


def flush(card_id):
    """桥接报送达成功后调用：有排着队的本人回答就提交，并在私聊回一句。"""
    try:
        got = submit(card_id)
    except F.Fail:
        return None
    if got and got[0] != 3:
        tell(identities()[1], got[1])
    return got


def reconcile(card_id=None):
    """对账。返回给常浩看的变化列表 [(卡片号, 一句话)]；读不到中台就不动。"""
    notes = []
    try:
        owner = identities()[0]
    except F.Fail:
        return notes
    sql = "SELECT * FROM claims WHERE who = 'owner' AND state IN ('queued', 'inflight', 'unknown')"
    with F.db() as con:
        rows = [dict(r) for r in (con.execute(sql) if card_id is None else con.execute(sql + " AND card_id = ?", (card_id,)))]
    for r in rows:
        cid, age = r["card_id"], _age(r["at"])
        if r["state"] == "queued":
            got = submit(cid)
            if got and got[0] != 3:
                notes.append((cid, got[1]))
            continue
        if r["state"] == "inflight" and age < HARD_STALE and (age < STALE or pid_alive(r["pid"])):
            continue                             # 还在跑
        c = read_card(cid)
        verdict = judge(c, r["choice"], owner)
        if verdict == "answered":
            set_state(cid, "answered", "对账：中台里是你这一答")
            notes.append((cid, "对账结果：你上次的回答中台已经收下了。"))
        elif verdict == "settled":
            set_state(cid, "settled", f"对账：{describe(c)}")
            notes.append((cid, f"对账结果：这张卡已经有了别的回答（{describe(c)}）。"))
        elif verdict == "open" and not pid_alive(r["pid"]) and age >= GRACE:
            release(cid, "对账：发送进程已退出、超过 60 秒、卡仍开着，确认中台没受理")
            notes.append((cid, "对账结果：你上次的点击中台没有收下，卡片还开着，请再点一次。"))
        elif r["state"] == "inflight":
            set_state(cid, "unknown", "对账：在途过久")
    return notes


# ---------- 入口 ----------
def answer(token, option, chat, operator):
    """返回 (退出码, 给常浩的话或 None（不回话）, 记录)。"""
    try:
        card = check(token, option, chat, operator)
    except Quiet as e:
        return 1, None, {"ok": False, "quiet": True, "why": str(e)}
    except F.Fail as e:
        return 1, f"这次点击没有交给中台：{e}。", {"ok": False, "why": str(e)}
    try:
        F.hub_target()                           # 钥匙、配置读不到就先说，不登记
        state, note = claim(card, option)
    except F.Fail as e:
        return 1, f"这次点击没有交给中台：{e}。", {"ok": False, "card": card["card_id"], "why": str(e)}
    if state == "queued":
        return 3, "已记下你的选择，卡片送达确认后马上交给中台。", {"ok": True, "card": card["card_id"], "queued": True}
    code, said = submit(card["card_id"]) or (1, "这次点击没有交给中台。")
    if note:
        said += f"（{note}）"
    return code, said, {"ok": code == 0, "card": card["card_id"], "code": code}


def tell(chat, text):
    from tidings_weekly import SendError, send
    try:
        send(chat, "text", {"text": text}, f"al-ans-{secrets.token_hex(8)}")   # 飞书幂等键限 50 字
    except SendError:
        pass                                     # 回话发不出不影响答卷结果；结果在卡片上会更新


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["reconcile"]:
        ap = argparse.ArgumentParser(description="对账")
        ap.add_argument("--card", type=int)
        a = ap.parse_args(argv[1:])
        print(json.dumps({"at": F.now(), "notes": reconcile(a.card)}, ensure_ascii=False))
        return 0
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("token")
    ap.add_argument("option", type=int)
    ap.add_argument("--chat", default="")
    ap.add_argument("--operator", default="")
    ap.add_argument("--no-reply", action="store_true", help="不在私聊回话（测试用）")
    a = ap.parse_args(argv)
    code, said, rec = answer(a.token, a.option, a.chat, a.operator)
    print(json.dumps({"at": F.now(), **rec}, ensure_ascii=False), flush=True)
    if said and not a.no_reply:                  # 只回常浩私聊，不回点击来的那个聊天
        tell(identities()[1], said)
    return code


if __name__ == "__main__":
    sys.exit(main())
