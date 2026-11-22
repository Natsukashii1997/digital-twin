#!/usr/bin/env python3
"""假 lark-cli：不联网。按 FAKE_LARK_DIR 里的 state.json 回话，每次调用记进 calls.jsonl。

state.json：{"docs": {token: markdown}, "im": [消息], "mget": {message_id: 消息}, "chats": {chat_id: 会话信息}, "vc": [], "minutes": [], "agenda": [], "tasks": []}
列表（im / vc / minutes / tasks）默认一轮回完；state 里写 "page": N 就像真的 lark-cli 那样一轮只给 N 条、has_more=true 并给 page_token，
后面的要带 --page-token 才给（#312）；再写 "lose_token": true 就是还有下一页却不给标记（写成命令名如 "task +get-related-tasks" 就只有那个命令不给）。
api GET /open-apis/im/v1/chats/<chat_id> 按 chats 回话（真实接口 2026-10-29 探针：data 里有 chat_mode=p2p/group/topic）；chats 里没有的报错。
docs +update 的 block_insert_after 会把内容插到文档第一段说明之后，供写后回读。
"""
import json
import os
import sys
from pathlib import Path

root = Path(os.environ["FAKE_LARK_DIR"])
state = json.loads((root / "state.json").read_text(encoding="utf-8"))
args = sys.argv[1:]
with open(root / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps({"args": args, "agent_env": sorted(k for k in os.environ if "HERMES" in k or "OPENCLAW" in k)}, ensure_ascii=False) + "\n")


def opt(name):
    return args[args.index(name) + 1] if name in args else None


def out(data):
    print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))


def listing(key, rows):
    n = state.get("page")
    if not n:
        return out({key: rows, "has_more": False})
    start = int(opt("--page-token") or 0)
    data = {key: rows[start:start + n], "has_more": start + n < len(rows)}
    if data["has_more"] and state.get("lose_token") not in (True, cmd):
        data["page_token"] = str(start + n)
    out(data)


cmd = " ".join(a for a in args[2:4])   # 去掉 --profile x
if cmd == "docs +fetch":
    md = state["docs"][opt("--doc")]
    if opt("--detail") == "with-ids":
        title, _, rest = md.partition("\n\n")
        intro = rest.split("\n\n")[0]
        out({"document": {"content": f'<title id="t">{title[2:]}</title><p id="intro1">{intro}</p>'}})
    else:
        out({"document": {"content": md}})
elif cmd == "docs +update":
    assert opt("--command") == "block_insert_after" and opt("--block-id") == "intro1", args
    md = state["docs"][opt("--doc")]
    head, _, rest = md.partition("\n\n")
    intro, _, tail = rest.partition("\n\n")
    state["docs"][opt("--doc")] = f"{head}\n\n{intro}\n\n{opt('--content').strip()}\n\n{tail}".rstrip() + "\n"
    (root / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    out({"result": "success"})
elif cmd == "im +messages-mget":
    ids = [i for i in (opt("--message-ids") or "").split(",") if i]
    found = [state.get("mget", {})[i] for i in ids if i in state.get("mget", {})]
    out({"messages": found, "total": len(found)})
elif cmd == "api GET" and args[4].startswith("/open-apis/im/v1/chats/"):
    chat_id = args[4].rsplit("/", 1)[1]
    chats = state.get("chats", {})
    if chat_id in chats:
        out(chats[chat_id])
    else:
        print(json.dumps({"ok": False, "error": {"code": 232002, "message": "fake: chat not found"}}))
elif cmd == "im +messages-search":
    listing("messages", state.get("im", []))
elif cmd == "vc +search":
    listing("items", state.get("vc", []))
elif cmd == "minutes +search":
    listing("items", state.get("minutes", []))
elif cmd == "calendar +agenda":
    out(state.get("agenda", []))
elif cmd == "task +get-related-tasks":
    listing("items", state.get("tasks", []))
else:
    print(json.dumps({"ok": False, "error": {"message": f"fake 不认识 {cmd}"}}))
