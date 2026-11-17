#!/usr/bin/env python3
"""常浩点中台卡片按钮后的处理（批 0B-4 第 4a 批，#195；规格 docs/秘书代批-规格.md §6、§7「本人点击锁」，0B-3 实测）。

merge-buttons 插件把 {"autoloop": 令牌, "o": 选项号} 的点击转来：
  autoloop_answer.py <令牌> <选项号> --chat <网关给的聊天号> --operator <网关给的点击人>
按 0B-3 实测能信的三样核对，缺一样就不答：
  1. 点击人 = 常浩的 open_id（twin_state.owner_open_id）；
  2. 聊天号 = 常浩私聊（twin_state.owner_chat_id；网关报的聊天类型不可信，不看）；
  3. 令牌登记过（autoloop_feishu 发卡时登记），选项号在这张卡的选项里。
然后在卡片库的写事务里记「本人在途」（这张卡已有本人答卷就不再答：令牌一次性；秘书已获准送达就不抢，以中台先受理的为准），
再用 feishu 通道钥匙答中台（by = 常浩，ref = 送达的消息号），用只读窗口回读核对，最后在私聊回一句结果。
答中台时连不上或超时，记「结果不明」，不重试（重答可能变成第二次回答；中台只认第一次）。

不调用 AI。退出码：0 已答上；1 没答（核对不过、已答过、秘书在先、中台拒绝）；2 结果不明。
"""
import argparse
import json
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_feishu as F  # noqa: E402

PENDING_SECRETARY = ("approved", "delivered", "unknown")   # 秘书已获准送达或已在送：本人不抢（规格 §7、§10 第 4 条）


def identities():
    import twin_state as T
    try:
        owner, home = T.owner_open_id(), T.owner_chat_id()
    except T.StateError as e:
        raise F.Fail(str(e))
    if not owner or not home:                    # 没配置就一律不认，不拿空值去比
        raise F.Fail("本机没配置常浩的 open_id 或私聊号（twin_identity.py setup / setup-chat）")
    return owner, home


def check(token, option, chat, operator):
    """三样核对；通过返回登记的卡片，不过抛 Fail（原因写给常浩看）。"""
    owner, home = identities()
    if not operator or operator != owner:
        raise F.Fail("点按钮的不是常浩本人")
    if not chat or chat != home:
        raise F.Fail("不是在常浩和猛鬼兽的私聊里点的")
    if not F.TOKEN_RE.match(token or ""):
        raise F.Fail("按钮里的令牌格式不对")
    card = F.card_by_token(token)
    if not card or not card.get("message_id"):
        raise F.Fail("这张卡没有登记过（不是飞书桥接发的，或还没发成功）")
    if not (0 <= option < card["options"]):
        raise F.Fail("选项号不在这张卡的选项里")
    return card


def claim(card_id, option):
    """记「本人在途」。已有本人答卷、或秘书已获准送达 → 抛 Fail；秘书只是预占（未获准）→ 本人优先，改成本人。"""
    with F.db(write=True) as con:
        r = con.execute("SELECT * FROM claims WHERE card_id = ?", (card_id,)).fetchone()
        if r and r["who"] == "owner":
            raise F.Fail("这张卡你已经点过了，以第一次为准")
        if r and r["who"] == "secretary" and r["state"] in PENDING_SECRETARY:
            raise F.Fail("秘书已经获准替你回答这张卡，以中台先受理的为准；不同意可以在汇报卡上推翻")
        con.execute("INSERT OR REPLACE INTO claims (card_id, who, state, choice, at, note) VALUES (?, 'owner', 'inflight', ?, ?, ?)",
                    (card_id, option, F.now(), "秘书的预占作废，本人优先" if r else None))


def settle(card_id, state, note=None):
    with F.db(write=True) as con:
        con.execute("UPDATE claims SET state = ?, at = ?, note = COALESCE(?, note) WHERE card_id = ? AND who = 'owner'",
                    (state, F.now(), note, card_id))


def readback(card_id, option, owner):
    """用只读窗口看中台里这张卡：已决定、选的是这一项、回答人是常浩，才算核对上。"""
    import autoloop_readonly as R
    try:
        snap = R.read()
    except Exception as e:                       # 只读窗口自己的错误类型不止一种：一律算没核对上
        return f"回读失败（{type(e).__name__}）"
    for c in snap.get("cards", []) + snap.get("recentCards", []):
        if c.get("id") == card_id:
            if c.get("state") in ("decided", "done") and c.get("choice") == option and c.get("by") == owner:
                return None
            return f"中台里这张卡是 {c.get('state')}、选项 {c.get('choice')}，和你点的对不上"
    return "回读时中台里找不到这张卡"


def answer(token, option, chat, operator):
    """返回 (退出码, 给常浩的一句话, 记录)。"""
    try:
        card = check(token, option, chat, operator)
        claim(card["card_id"], option)
    except F.Fail as e:
        return 1, f"这次点击没有交给中台：{e}。", {"ok": False, "why": str(e)}
    cid = card["card_id"]
    try:
        out = F.hub("POST", f"/v1/cards/{cid}/answer", {"choice": option, "by": operator, "ref": card["message_id"]})
    except F.Fail as e:
        if "HTTP" in str(e):                     # 中台明确拒绝：没答上
            settle(cid, "rejected", str(e))
            return 1, f"中台没收下这次回答：{e}。", {"ok": False, "card": cid, "why": str(e)}
        settle(cid, "unknown", str(e))           # 连不上、超时：可能答上了也可能没有，不重答
        return 2, f"这次回答结果不明（{e}），我不会重答，会在电脑上对账后告诉你。", {"ok": False, "card": cid, "unknown": True}
    if not out.get("changed"):
        settle(cid, "settled", "中台说这张卡已经有了回答")
        return 1, "这张卡已经有了回答（中台只认第一次），你这次没有改变结果。", {"ok": False, "card": cid, "why": "already settled"}
    bad = readback(cid, option, operator)
    settle(cid, "answered" if not bad else "unknown", bad)
    if bad:
        return 2, f"已交给中台，但回读没核对上：{bad}。我会在电脑上对账。", {"ok": False, "card": cid, "unknown": True, "why": bad}
    return 0, "收到，已交给中台。", {"ok": True, "card": cid, "choice": option}


def tell(chat, text, key):
    from tidings_weekly import SendError, send
    try:
        send(chat, "text", {"text": text}, key)
    except SendError:
        pass                                     # 回话发不出不影响答卷结果；结果在卡片上会更新


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("token")
    ap.add_argument("option", type=int)
    ap.add_argument("--chat", default="")
    ap.add_argument("--operator", default="")
    ap.add_argument("--no-reply", action="store_true", help="不在私聊回话（测试用）")
    a = ap.parse_args(argv)
    code, said, rec = answer(a.token, a.option, a.chat, a.operator)
    print(json.dumps({"at": F.now(), **rec}, ensure_ascii=False), flush=True)
    if not a.no_reply:
        try:
            home = identities()[1]
        except F.Fail:
            home = ""
        if home:                                 # 只回常浩私聊，不回点击来的那个聊天（可能是别处）
            tell(home, said, f"al-ans-{a.token}-{a.option}-{secrets.token_hex(4)}")   # 飞书幂等键限 50 字
    return code


if __name__ == "__main__":
    sys.exit(main())
