#!/usr/bin/env python3
"""按钮实测探针（#195 批 0B-3；规格 docs/秘书代批-规格.md §5「卡片回答的信任」、方案 5.1）。

要弄清的事：常浩在飞书点卡片按钮时，猛鬼兽网关转给 merge-buttons 插件的信息里，能不能可信地拿到「谁点的」（open_id）
和「点的是哪张卡」（原消息号或卡上的令牌）。读代码的推断：点击人会进 HERMES_SESSION_USER_ID；原消息号网关不转，
HERMES_SESSION_MESSAGE_ID 是回调 token，所以用发卡时放进按钮的随机令牌对上卡。本脚本把推断变成实测记录。

用法：
  button_probe.py send [--unregistered]   以猛鬼兽机器人往常浩私聊发一张测试卡（一个按钮，value 只有 {"probe": 随机令牌}），
                                          回读核对，再把「令牌 ↔ 消息号」登记到 sent.jsonl；--unregistered 发一张不登记的卡，
                                          测令牌对不上时会不会被认成测试卡
  button_probe.py show [--json]           只读：把插件记下的点击（clicks.jsonl）和登记的卡对照，逐条说清谁点的（是不是常浩）、
                                          哪个聊天（是不是私聊）、点的是哪张卡、同一张卡点了几次
点击只被记录，不触发任何动作。记录在 DT_PROBE_DIR（默认 DigitalTwin/evidence/button-probe/，700 / 600）。
"""
import argparse
import datetime as dt
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from feishu_home import home_chat  # noqa: E402
from tidings_weekly import SendError, lark, send  # noqa: E402

DIR = Path(os.environ.get("DT_PROBE_DIR", "~/Library/Application Support/DigitalTwin/evidence/button-probe")).expanduser()
MARK = "0B-3 按钮实测"


def card(token, unregistered):
    note = ("这张卡**没有登记**，用来测令牌对不上时会不会被认错。" if unregistered
            else "点下面的按钮，只会记下这次点击带了哪些信息，不会触发任何动作。")
    return {"schema": "2.0",
            "header": {"title": {"tag": "plain_text", "content": f"🧪 {MARK}"}, "template": "grey"},
            "body": {"elements": [
                {"tag": "markdown", "content": f"{note}\n令牌尾号 `{token[-4:]}`"},
                {"tag": "button", "text": {"tag": "plain_text", "content": "点我测试"}, "type": "primary",
                 "behaviors": [{"type": "callback", "value": {"probe": token}}]}]}}


def append(name, rec):
    DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(DIR / name, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def read(name):
    try:
        lines = (DIR / name).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def do_send(unregistered):
    chat = home_chat()
    if not chat.startswith("oc_"):
        raise SendError("没有私聊 chat_id（FEISHU_HOME_CHANNEL，环境变量和 ~/.hermes/.env 里都没有）")
    token = secrets.token_hex(8)
    mid = send(chat, "interactive", card(token, unregistered), f"button-probe-{token}")
    if not mid:
        raise SendError("发卡片没拿到消息号")
    got = lark(["im", "+messages-mget", "--message-ids", mid], "回读")
    m = (got.get("messages") or [{}])[0]
    if m.get("msg_type") != "interactive" or token[-4:] not in str(m.get("content")):
        raise SendError(f"卡片已发（{mid}），但回读对不上")
    if not unregistered:
        append("sent.jsonl", {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                              "token": token, "message_id": mid, "chat_id": chat})
    return {"ok": True, "message_id": mid, "token_tail": token[-4:], "registered": not unregistered}


def owner():
    try:
        import twin_state as T
        return T.owner_open_id()
    except Exception:
        return ""


def report():
    cards = {c.get("token"): c for c in read("sent.jsonl")}
    me, home = owner(), home_chat()
    seen, rows = {}, []
    for c in read("clicks.jsonl"):
        tok = c.get("token")
        seen[tok] = seen.get(tok, 0) + 1
        sent = cards.get(tok)
        rows.append({"at": c.get("at"), "token_tail": str(tok)[-4:],
                     "card": sent.get("message_id") if sent else None,
                     "operator_is_owner": bool(me) and me in (c.get("operator"), c.get("operator_alt")),
                     "operator_kind": str(c.get("operator") or "")[:3] or None,
                     "chat_is_home": bool(home) and c.get("chat_id") == home,
                     "chat_type": c.get("chat_type"), "message_id_prefix": c.get("message_id_prefix"),
                     "message_id_len": c.get("message_id_len"), "nth_click": seen[tok]})
    return {"owner_known": bool(me), "registered_cards": len(cards), "clicks": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("send").add_argument("--unregistered", action="store_true")
    sub.add_parser("show").add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "send":
        try:
            out = do_send(a.unregistered)
        except SendError as e:
            print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
            return 1
        print(json.dumps(out, ensure_ascii=False))
        return 0
    out = report()
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    print(f"登记的测试卡 {out['registered_cards']} 张，点击 {len(out['clicks'])} 次" + ("" if out["owner_known"] else "（读不到常浩的 open_id）"))
    for r in out["clicks"]:
        print(f"- {r['at']} 令牌尾号 {r['token_tail']}：{'对上卡 ' + r['card'] if r['card'] else '没登记的卡'}；"
              f"点击人{'是' if r['operator_is_owner'] else '不是'}常浩（id 前缀 {r['operator_kind']}）；"
              f"{'私聊' if r['chat_is_home'] else '不是私聊'}（{r['chat_type']}）；"
              f"消息号前缀 {r['message_id_prefix']}、长 {r['message_id_len']}；这张卡第 {r['nth_click']} 次被点")
    return 0


if __name__ == "__main__":
    sys.exit(main())
