"""猛鬼兽 · 托管单的回话不进模型（#305，常浩 2026-11-22 选甲）。

常浩在飞书私聊里引用托管单的卡片或提醒回的话，是回给托管会话的（托管会话用 away_sheet.py replies 经飞书接口读，
不经过网关）。规程（HERMES.md 第 4 节）只能约束模型，模型每轮仍会回一句；这里在 pre_gateway_dispatch 钩子里、
交给模型之前就把这类消息丢掉，网关日志记一行「pre_gateway_dispatch skip: reason=away-sheet-reply」。

只丢同时满足这几条的消息：飞书、单聊、不是机器人发的、不在话题里；聊天号就是本机配置的常浩私聊号
（DigitalTwin/owner_chat_id）；发送人如果给的是 open_id，要等于 DigitalTwin/owner_open_id；引用的消息号是
托管单账本里发在这个私聊的某张单子（已经结束的也算）的卡片或提醒（away_sheet.muted_ids）。
其余一律照常交给猛鬼兽。任何一步出错（脚本没装、身份文件不对、账本读不了）都照常放行并记一条警告：
最坏就是回到没装插件的样子，不会吞掉别的消息。在话题里回的不丢，和 replies 不读话题回复一致，猛鬼兽会照规程说一声。
"""
import importlib.util
import logging
import os
import pwd
from pathlib import Path

logger = logging.getLogger(__name__)
SCRIPT = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".hermes" / "scripts" / "away_sheet.py"
SKIP = {"action": "skip", "reason": "away-sheet-reply"}
_MODULE = None


def _away():
    """装好的 away_sheet.py（只加载一次；换版本要重启网关，和其他脚本一样）。"""
    global _MODULE
    if _MODULE is None:
        spec = importlib.util.spec_from_file_location("away_sheet_for_mute", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE = mod
    return _MODULE


def _platform(source):
    p = getattr(source, "platform", None)
    return str(getattr(p, "value", p) or "").lower()


def _drop(event=None, **kwargs):
    try:
        source = getattr(event, "source", None)
        quoted = getattr(event, "reply_to_message_id", None)
        if (not quoted or source is None or _platform(source) != "feishu" or getattr(source, "chat_type", "") != "dm"
                or getattr(source, "is_bot", False) or getattr(source, "thread_id", None)):
            return None
        away = _away()
        who, chat = away.owner()
        if getattr(source, "chat_id", None) != chat:
            return None
        user = getattr(source, "user_id", None) or ""
        if user.startswith("ou_") and user != who:
            return None
        if quoted in away.muted_ids(chat):
            return dict(SKIP)
    except Exception as e:                       # noqa: BLE001  放行，不吞消息
        logger.warning("away-sheet-mute: 判断出错，照常交给猛鬼兽（%s）", type(e).__name__)
    return None


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _drop)
