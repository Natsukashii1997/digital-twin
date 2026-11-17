"""临时中台的接单命令（批 0B-4 飞书桥接端到端测试）：没有回答时交一张两选项的提问卡，有回答时说选了什么。"""
import json
import os

ctx = {}
try:
    with open(os.environ.get("AGENT_AUTOLOOP_CONTEXT", ""), encoding="utf-8") as f:
        ctx = json.load(f)
except (OSError, ValueError):
    pass
answers = ctx.get("answers") or []
if answers:
    print(f"你选了{answers[0].get('chosen')}，试跑通过")
else:
    print("问了一个问题\n\n```agent-autoloop")
    print(json.dumps({"ask": [{"title": "试跑：选一个颜色", "text": "这是一次试跑，选哪个都行。",
                               "options": ["蓝色", "绿色"], "recommend": 0, "key": "try-color"}]}, ensure_ascii=False))
    print("```")
