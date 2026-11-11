"""个人项目日报 10:00 发送（#155、#159，常浩 2026-11-11 定），卡片带工程自检：Hermes 无模型定时任务入口，等于 `pdaily_publish.py send`。
成功不输出；没发成时输出一句原因，由定时任务发到常浩私聊。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdaily_publish  # noqa: E402

if __name__ == "__main__":
    sys.exit(pdaily_publish.main(["send"]))
