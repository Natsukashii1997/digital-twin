"""常浩和猛鬼兽私聊的 chat_id（#165）：给要往私聊发消息的脚本共用。

先看环境变量 FEISHU_HOME_CHANNEL；没有时从 Hermes 的 .env（DT_HERMES_ENV，默认 ~/.hermes/.env）只读这一项。
Hermes 无模型定时任务给脚本的环境经过清理，里面没有 FEISHU_HOME_CHANNEL（AI HOT 试跑、Tidings 11-09 都因此没发出），
带模型任务的终端里也没有，所以不能只读环境变量。"""
import os
from pathlib import Path


def home_chat():
    chat = os.environ.get("FEISHU_HOME_CHANNEL", "")
    env_file = Path(os.environ.get("DT_HERMES_ENV", "~/.hermes/.env")).expanduser()
    if not chat and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "FEISHU_HOME_CHANNEL":
                chat = v.strip().strip("'\"")
    return chat
