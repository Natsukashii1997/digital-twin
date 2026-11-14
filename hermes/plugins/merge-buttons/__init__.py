"""卡片按钮（digital-twin #78；#133 加托管汇报卡）。

飞书适配器把卡片按钮点击转成一条 `/card button {...}`。本插件注册 /card，认出两类按钮，在后台起脚本，立刻返回 None：
  - 合并官 PR 卡片：{"merge_officer": "merge"|"return", "ref": "仓库#PR@版本"} → `merge_act.py button`；
  - 托管汇报卡：{"custody": "overturn"|"merge_revert", "ref": "d-YYMMDD-六位十六进制"} → `custody_report.py button`。
不回复，是因为网关回复按钮点击会失败（拿回调 token 当消息号）；进度和结果由脚本直接发到私聊。
核对规则全在脚本里（只认常浩私聊、只认发过的卡片），这里只做转交。
"""
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path

PYTHON = os.environ.get("DT_MERGE_PYTHON", "/usr/bin/python3")
SCRIPT = Path(os.environ.get("DT_MERGE_ACT", "~/.hermes/scripts/merge_act.py")).expanduser()
CUSTODY_SCRIPT = Path(os.environ.get("DT_CUSTODY_REPORT", "~/.hermes/scripts/custody_report.py")).expanduser()
LOG = Path(os.environ.get("DT_SHADOW_DIR", "~/Library/Application Support/DigitalTwin/shadow")).expanduser() / "buttons.log"
REF = re.compile(r"^[\w.-]+#\d+@[0-9a-f]{7,40}$")
DECISION = re.compile(r"^d-\d{6}-[0-9a-f]{6}$")


def _chat_id():
    try:
        from gateway.session_context import get_session_env
        return get_session_env("HERMES_SESSION_CHAT_ID") or ""
    except Exception:
        return os.environ.get("HERMES_SESSION_CHAT_ID", "")


def parse(raw_args):
    """`button {...}` → (脚本, verb, ref)；不认识的按钮返回 None。"""
    tag, _, payload = (raw_args or "").strip().partition(" ")
    if tag != "button":
        return None
    try:
        value = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    if "merge_officer" in value:
        verb, ref = value.get("merge_officer"), str(value.get("ref") or "")
        if verb in ("merge", "return") and REF.match(ref):
            return SCRIPT, verb, ref
        return None
    verb, ref = value.get("custody"), str(value.get("ref") or "")
    if verb in ("overturn", "merge_revert") and DECISION.match(ref):
        return CUSTODY_SCRIPT, verb, ref
    return None


def handle(raw_args):
    picked = parse(raw_args)
    if picked is None:
        return None
    script, verb, ref = picked
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    try:
        log.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {verb} {ref}\n")
        log.flush()
        subprocess.Popen([PYTHON, str(script), "button", verb, ref, "--chat", _chat_id()],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    finally:
        log.close()
    return None


def register(ctx):
    ctx.register_command("card", handler=handle, description="卡片按钮：PR 合并 / 退回（#78），托管推翻 / 合并 revert PR（#133）")
