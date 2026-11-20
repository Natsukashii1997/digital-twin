"""AI 前沿群学习报告 10:45 发送（#192，常浩 2026-11-16 定）：Hermes 无模型定时任务入口，等于 `groupdigest.py send`。
成功不输出；没发好时输出一句原因，由定时任务发到常浩私聊。开跑前先等飞书连得上（P9，netwait），等不到输出原因、退出码 1。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import groupdigest  # noqa: E402
import netwait  # noqa: E402

if __name__ == "__main__":
    netwait.guard("groupdigest-send", ("feishu",), "今天的 AI 前沿群学习报告")
    sys.exit(groupdigest.main(["send"]))
