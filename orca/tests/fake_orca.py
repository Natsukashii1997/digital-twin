#!/usr/bin/env python3
"""假 orca：只认 chatgpt_relay 用到的 eval / terminal send，状态放在 FAKE_ORCA_STATE 指向的 JSON 里。

state 字段：
  composer / streaming_before   发送前页面状态
  reply                         发出后 ChatGPT 的回复原文
  stream_polls                  回复出现后还要“生成中”几次轮询
  never_reply                   true 时一直不回复
  mangle                        true 时输入框里的内容被截断
  fail                          设为某个命令名时，该命令返回 ok:false
  calls                         记录收到的命令（测试读）
"""
import json
import os
import re
import sys

path = os.environ["FAKE_ORCA_STATE"]
with open(path) as f:
    s = json.load(f)
args = [a for a in sys.argv[1:] if a != "--json"]
s.setdefault("calls", []).append(args)


def out(result=None, ok=True):
    s.setdefault("user", 0)
    with open(path, "w") as f:
        json.dump(s, f)
    print(json.dumps({"ok": ok, "result": result} if ok else {"ok": False, "error": {"code": "fake_fail"}}))
    sys.exit(0)


if s.get("fail") == args[0]:
    out(ok=False)

if args[0] == "eval":
    js = args[args.index("--expression") + 1]
    if "/*relay:state*/" in js:
        sent = s.get("sent", False)
        replying = sent and not s.get("never_reply")
        polls = s.get("polls", 0)
        if replying:
            s["polls"] = polls + 1
        streaming = s.get("streaming_before", False) if not sent else (replying and polls < s.get("stream_polls", 1))
        out({"result": json.dumps({
            "url": "https://chatgpt.com/c/fake",
            "composer": s.get("composer", True),
            "streaming": streaming,
            "user": 1 if sent else 0,
            "assistant": 1 if replying else 0,
            "last_user": s.get("composer_text", "") if sent else "",
            "last": (s["reply"] if not streaming else s["reply"][:5]) if replying else "",
        })})
    if "/*relay:insert*/" in js:
        s["inserted"] = js
        text = json.loads(re.search(r"insertText', false, (\".*\")\);", js).group(1))
        s["composer_text"] = "\n\n".join(text.split("\n"))  # 模拟编辑器把换行变成段落
        if s.get("mangle"):
            s["composer_text"] = s["composer_text"][:10]
        out({"result": "true"})
    if "/*relay:composer*/" in js:
        out({"result": s.get("composer_text", "")})
    if "/*relay:send*/" in js:
        s["sent"] = True
        out({"result": "true"})
if args[:2] == ["terminal", "send"]:
    out({"accepted": True})
out(ok=False)
