"""卡片按钮（digital-twin #78；#133 加托管汇报卡）。

飞书适配器把卡片按钮点击转成一条 `/card button {...}`。本插件注册 /card，认出三类按钮，前两类在后台起脚本，都立刻返回 None：
  - 合并官 PR 卡片：{"merge_officer": "merge"|"return", "ref": "仓库#PR@版本"} → `merge_act.py button`；
  - 托管汇报卡：{"custody": "overturn"|"merge_revert", "ref": "d-YYMMDD-六位十六进制", "card": "16 位十六进制"}
    → `custody_report.py button … --card <令牌>`（card 是发卡时的随机令牌，脚本核对是不是核实过的那张卡；格式不对就不转）；
  - 按钮实测探针（#195 批 0B-3）：{"probe": "16 位十六进制"} → 不起脚本，只把网关给的会话信息（点击人、聊天、消息号的样子）
    追加到 evidence/button-probe/clicks.jsonl，由 `button_probe.py show` 和登记的测试卡对照。只记录，不触发任何动作。
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
CARD = re.compile(r"^[0-9a-f]{16}$")
PROBE_DIR = Path(os.environ.get("DT_PROBE_DIR", "~/Library/Application Support/DigitalTwin/evidence/button-probe")).expanduser()


def _session(key):
    try:
        from gateway.session_context import get_session_env
        return get_session_env(key) or ""
    except Exception:
        return os.environ.get(key, "")


def _chat_id():
    return _session("HERMES_SESSION_CHAT_ID")


def probe(value):
    """探针按钮：记下这次点击网关给了什么。消息号只记样子（前缀、长度）：网关把回调 token 当消息号传进来，不落原文。"""
    token = str(value.get("probe") or "")
    if not CARD.match(token):
        return None
    mid = _session("HERMES_SESSION_MESSAGE_ID")
    rec = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "token": token,
           "operator": _session("HERMES_SESSION_USER_ID"), "operator_alt": _session("HERMES_SESSION_USER_ID_ALT"),
           "user_name": _session("HERMES_SESSION_USER_NAME"), "chat_id": _chat_id(),
           "chat_type": _session("HERMES_SESSION_CHAT_TYPE"), "platform": _session("HERMES_SESSION_PLATFORM"),
           "message_id_prefix": mid[:3], "message_id_len": len(mid)}
    try:
        PROBE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(PROBE_DIR / "clicks.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass                      # 记不下就当没点过：show 里看不到这次点击，不影响网关
    return None


def parse(raw_args):
    """`button {...}` → (脚本, verb, ref, 额外参数)；不认识的按钮返回 None。"""
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
            return SCRIPT, verb, ref, []
        return None
    verb, ref = value.get("custody"), str(value.get("ref") or "")
    card = value.get("card")
    if verb in ("overturn", "merge_revert") and DECISION.match(ref) and (card is None or CARD.match(str(card))):
        return CUSTODY_SCRIPT, verb, ref, (["--card", str(card)] if card is not None else [])
    return None


def handle(raw_args):
    tag, _, payload = (raw_args or "").strip().partition(" ")
    try:
        value = json.loads(payload) if tag == "button" else None
    except ValueError:
        value = None
    if isinstance(value, dict) and "probe" in value:          # 探针只认 {"probe": 令牌}，多带别的键一律不认
        return probe(value) if set(value) == {"probe"} else None
    picked = parse(raw_args)
    if picked is None:
        return None
    script, verb, ref, extra = picked
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    try:
        log.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {verb} {ref}\n")
        log.flush()
        subprocess.Popen([PYTHON, str(script), "button", verb, ref, "--chat", _chat_id(), *extra],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    finally:
        log.close()
    return None


def register(ctx):
    ctx.register_command("card", handler=handle, description="卡片按钮：PR 合并 / 退回（#78），托管推翻 / 合并 revert PR（#133），按钮实测探针（#195）")
