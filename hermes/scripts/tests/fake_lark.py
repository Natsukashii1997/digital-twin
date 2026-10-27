#!/usr/bin/env python3
"""假 lark-cli：不联网。按 FAKE_LARK_DIR 里的 state.json 回话，每次调用记进 calls.jsonl。

state.json：{"docs": {token: markdown}, "im": [消息], "vc": [], "minutes": [], "agenda": [], "tasks": []}
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
elif cmd == "im +messages-search":
    out({"messages": state.get("im", []), "has_more": False})
elif cmd == "vc +search":
    out({"items": state.get("vc", [])})
elif cmd == "minutes +search":
    out({"items": state.get("minutes", [])})
elif cmd == "calendar +agenda":
    out(state.get("agenda", []))
elif cmd == "task +get-related-tasks":
    out({"items": state.get("tasks", [])})
else:
    print(json.dumps({"ok": False, "error": {"message": f"fake 不认识 {cmd}"}}))
