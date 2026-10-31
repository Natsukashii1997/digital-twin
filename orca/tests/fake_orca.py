#!/usr/bin/env python3
"""假 orca：只认 chatgpt_relay 用到的 eval / terminal send，状态放在 FAKE_ORCA_STATE 指向的 JSON 里。

state 字段：
  composer / streaming_before   发送前页面状态
  reply                         发出后 ChatGPT 的回复原文
  stream_polls                  回复出现后还要“生成中”几次轮询
  script                        发出后每次读状态依次返回 [streaming, 回复文本, url?, 回复条数?]，读完后停在最后一项；优先于 reply
  url_before                    发送前页面地址（默认在会话 /c/fake 里）
  user_before_text              发送前「最新回复前的用户消息」（默认空）
  frozen_counts                 设了就固定返回这个消息条数（模拟长会话只渲染最近几轮）
  never_reply                   true 时一直不回复
  mangle                        true 时输入框里的内容被截断
  foreign_user                  最新回复前的用户消息换成这段文字（模拟别人插话）
  send_disabled                 发送按钮不可用
  url_at_send                   点发送那一刻的页面地址（模拟核对通过后标签被切走）
  composer_text_at_send         点发送那一刻的输入框内容（模拟填好后被改动）
  streaming_at_send             点发送那一刻正在生成
  fail_after_send               点了发送，但 orca 返回失败
  fail                          设为某个命令名时，该命令返回 ok:false
  rc_nonzero                    设为某个命令名时，该命令退出码 1 但输出 ok:true
  raw_stdout                    设为某个命令名时，该命令输出 [1, 2]（不是对象）
  mode_chat                     撰写器是聊天模式（默认 true）
  strength                      思考强度（默认「高」）
  strength_stuck                true 时调强度不起作用
  strength_seq                  依次读到的思考强度（模拟切会话后读数还在变），读完停在最后一个
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
    with open(path, "w") as f:
        json.dump(s, f)
    if s.get("raw_stdout") == args[0]:
        print(json.dumps([1, 2]))
    else:
        print(json.dumps({"ok": ok, "result": result} if ok else {"ok": False, "error": {"code": "fake_fail"}}))
    sys.exit(1 if s.get("rc_nonzero") == args[0] else 0)


if s.get("fail") == args[0]:
    out(ok=False)

URL = "https://chatgpt.com/c/fake"


def page_state():
    sent = s.get("sent", False)
    replying = sent and not s.get("never_reply")
    polls = s.get("polls", 0)
    if replying:
        s["polls"] = polls + 1
    url, assistant = URL, 1 if replying else 0
    if not sent:
        url = s.get("url_before", URL)
        streaming, last = s.get("streaming_before", False), ""
    elif not replying:
        streaming, last = False, ""
    elif "script" in s:
        step = s["script"][min(polls, len(s["script"]) - 1)]
        streaming, last = step[0], step[1]
        url = step[2] if len(step) > 2 else URL
        assistant = step[3] if len(step) > 3 else 1
    else:
        streaming = polls < s.get("stream_polls", 1)
        last = s["reply"][:5] if streaming else s["reply"]
    if not replying:
        last_user = s.get("user_before_text", "")
    else:
        last_user = s.get("foreign_user", s.get("composer_text", ""))
    return {
        "url": url,
        "composer": s.get("composer", True),
        "streaming": streaming,
        "user": s.get("frozen_counts", 1 if sent else 0),
        "assistant": s.get("frozen_counts", assistant),
        "last_user": last_user,
        "last": last,
    }


if args[0] == "eval":
    js = args[args.index("--expression") + 1]
    if "/*relay:state*/" in js:
        out({"result": json.dumps(page_state())})
    if "/*relay:insert*/" in js:
        s["inserted"] = js
        text = json.loads(re.search(r"insertText', false, (\".*\")\);", js).group(1))
        s["composer_text"] = "\n\n".join(text.split("\n"))  # 模拟编辑器把换行变成段落
        if s.get("mangle"):
            s["composer_text"] = s["composer_text"][:10]
        out({"result": "true"})
    if "/*relay:composer*/" in js:
        out({"result": s.get("composer_text", "")})
    if "/*relay:mode*/" in js:
        if s.get("strength_seq"):
            s["strength"] = s["strength_seq"].pop(0)
        out({"result": json.dumps({"chat": s.get("mode_chat", True), "strength": s.get("strength", "高")})})
    if "/*relay:open-model*/" in js:
        s["menu"] = True
        out({"result": "true"})
    if "/*relay:set-high*/" in js:
        if s.get("menu") and not s.get("strength_stuck"):
            s["strength"] = "高"
        out({"result": "true" if s.get("menu") else "false"})
    if "/*relay:close-menu*/" in js:
        s["menu"] = False
        out({"result": "true"})
    if "/*relay:send*/" in js:
        # 按发送脚本的约定在「点击那一刻」核对：页面地址、输入框、生成状态、按钮；不符就不点
        url, text = json.loads("[" + js[js.rindex("})(") + 3:-1] + "]")
        norm = lambda u: u.split("?")[0].split("#")[0].rstrip("/")
        squash = lambda t: re.sub(r"\s+", "", t)
        now = s.get("composer_text_at_send", s.get("composer_text", ""))
        if norm(s.get("url_at_send", s.get("url_before", URL))) != norm(url):
            out({"result": "chat"})
        if not s.get("composer", True):
            out({"result": "composer"})
        if squash(now) != squash(text):
            out({"result": "text"})
        if s.get("streaming_at_send"):
            out({"result": "streaming"})
        if s.get("send_disabled"):
            out({"result": "button"})
        s["sent"] = True
        if s.get("fail_after_send"):
            out(ok=False)
        out({"result": "sent"})
if args[:2] == ["terminal", "send"]:
    out({"accepted": True})
out(ok=False)
