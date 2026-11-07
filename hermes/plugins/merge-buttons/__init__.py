"""PR 卡片按钮（digital-twin #78）。

飞书适配器把卡片按钮点击转成一条 `/card button {"merge_officer": "merge"|"return", "ref": "仓库#PR@版本"}`。
本插件注册 /card：认出合并官的按钮后，在后台起 `merge_act.py button`，立刻返回 None。
不回复，是因为网关回复按钮点击会失败（拿回调 token 当消息号）；进度和结果由 merge_act.py 直接发到私聊。
核对规则全在 merge_act.py（只认常浩私聊、只认发过的卡片和版本），这里只做转交。
"""
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

PYTHON = os.environ.get("DT_MERGE_PYTHON", "/usr/bin/python3")
SCRIPT = Path(os.environ.get("DT_MERGE_ACT", "~/.hermes/scripts/merge_act.py")).expanduser()
LOG = Path(os.environ.get("DT_SHADOW_DIR", "~/Library/Application Support/DigitalTwin/shadow")).expanduser() / "buttons.log"
REF = re.compile(r"^[\w.-]+#\d+@[0-9a-f]{7,40}$")


def _chat_id():
    try:
        from gateway.session_context import get_session_env
        return get_session_env("HERMES_SESSION_CHAT_ID") or ""
    except Exception:
        return os.environ.get("HERMES_SESSION_CHAT_ID", "")


def parse(raw_args):
    """`button {...}` → (verb, ref)；不是合并官的按钮返回 None。"""
    tag, _, payload = (raw_args or "").strip().partition(" ")
    if tag != "button":
        return None
    try:
        value = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    verb, ref = value.get("merge_officer"), str(value.get("ref") or "")
    if verb not in ("merge", "return") or not REF.match(ref):
        return None
    return verb, ref


def handle(raw_args):
    picked = parse(raw_args)
    if picked is None:
        return None
    verb, ref = picked
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    try:
        log.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {verb} {ref}\n")
        log.flush()
        subprocess.Popen([PYTHON, str(SCRIPT), "button", verb, ref, "--chat", _chat_id()],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    finally:
        log.close()
    return None


def register(ctx):
    ctx.register_command("card", handler=handle, description="PR 卡片按钮：合并 / 退回（digital-twin #78）")
