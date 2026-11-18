#!/usr/bin/env python3
"""演练用子进程（批 0D）：经出口账本或本人点击做一个真动作，做完以后**不记结果就停住**，等父进程强杀。
用法：drill_child.py <模式> <参数 JSON>。路径、中台地址都从继承的环境变量来（父进程已指到临时目录）。"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import autoloop_exits as E  # noqa: E402

mode, p = sys.argv[1], json.loads(sys.argv[2])


def then_hang(fn):
    def do():
        out = fn()
        time.sleep(120)                                # 动作已经做了，结果还没记：在这里被强杀
        return out
    return do


if mode == "push":
    E.guard("push_branch", p["key"], p["scope"], p["target"], then_hang(lambda: subprocess.run(
        ["git", "push", "-q", p["target"]["remote"], f"{p['target']['head']}:refs/heads/{p['target']['branch']}"],
        cwd=p["cwd"], check=True)))
elif mode == "pr":
    E.guard("open_pr", p["key"], p["scope"], p["target"], then_hang(lambda: subprocess.run(
        [p["gh"], "pr", "create", "-R", p["target"]["github"], "-B", "main", "-H", p["target"]["branch"], "-t", "t",
         "-F", "/dev/null"], check=True)))
elif mode == "send":
    import autoloop_feishu as F

    def lark(args, what, cwd=None):                    # 假飞书：消息落进私聊文件，然后卡住
        chat = Path(p["chat_file"])
        msgs = json.loads(chat.read_text()) if chat.exists() else []
        mid = f"om_drill_{len(msgs) + 1}"
        msgs.append({"message_id": mid, "msg_type": "interactive", "sender": {"sender_type": "app"},
                     "body": {"content": args[args.index("--content") + 1]}})
        chat.write_text(json.dumps(msgs))
        time.sleep(120)
    F.lark = lark
    F.deliver(p["card"])
elif mode == "answer":
    import autoloop_answer as A
    import autoloop_feishu as F
    real = F.hub

    def hub(method, path, body=None, timeout=20):      # 中台已经收下回答，回应还没回来就被强杀
        out = real(method, path, body, timeout)
        if path.endswith("/answer"):
            time.sleep(120)
        return out
    F.hub = hub
    A.answer(p["token"], p["option"], p["chat"], p["owner"])
