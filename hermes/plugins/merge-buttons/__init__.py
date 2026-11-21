"""卡片按钮（digital-twin #78；#133 加托管汇报卡；#195 加中台卡片和实测探针）。

飞书适配器把卡片按钮点击转成一条 `/card button {...}`。本插件注册 /card，认出四类按钮，除探针外都在后台起脚本，都立刻返回 None：
  - 合并官 PR 卡片：{"merge_officer": "merge"|"return", "ref": "仓库#PR@版本"} → `merge_act.py button`；
  - 托管汇报卡：{"custody": "overturn"|"merge_revert", "ref": "d-YYMMDD-六位十六进制", "card": "16 位十六进制"}
    → `custody_report.py button … --card <令牌>`（card 是发卡时的随机令牌，脚本核对是不是核实过的那张卡；格式不对就不转）；
  - 中台卡片（#195 批 0B-4）：{"autoloop": "16 位十六进制令牌", "o": 选项号, "v": 版本号} → `autoloop_answer.py <令牌> <选项号>
    --version <版本号> --chat <聊天号> --operator <点击人>`（点击人取网关的 HERMES_SESSION_USER_ID，0B-3 实测是飞书给的 open_id；
    v 是按钮所在那一版的版本号，#218 第 3 轮 P1-03）；没带 v 的旧样式按钮照样转，不带 --version，由脚本拒绝并在私聊说明；
  - 个人项目日报的拍板按钮（#235）：{"pdaily": "16 位十六进制卡片令牌", "n": 编号（0 = 其余全部）} → 先**同步**跑
    `pdaily_act.py button <令牌> <编号> --chat <聊天号> --operator <点击人>`（核对常浩本人、常浩私聊、最新一张日报卡、记拍板账），
    认了就用 ctx.inject_message 往常浩私聊的会话注入一句话，让猛鬼兽按 pdaily-act 技能办；再在后台跑 `pdaily_act.py after`
    把卡片原地改成「已定」（注入没成，after 顺带在私聊说请引用卡片回一句）。注入要 Hermes 配置
    plugins.entries.merge-buttons.allow_gateway_injection: true（常浩点头才开）；没开时 inject_message 返回 False，照样记账。
  - 秘书汇报卡（#195 批 0B-5）：{"secretary": "overturn"|"merge_revert", "ref": 决定号, "card": 令牌}、改选卡
    {"secretary": "reselect", "ref": 决定号, "card": 令牌, "o": 选项号} → `secretary_report.py button <verb> <决定号> --chat <聊天号>
    --operator <点击人> --card <令牌> [--option <选项号>]`（键要正好是这几个、令牌必带；点击人、令牌、选项由脚本核对）；
  - 按钮实测探针（#195 批 0B-3）：{"probe": "16 位十六进制"} → 不起脚本，只把网关给的会话信息（点击人、聊天、消息号的样子）
    追加到 evidence/button-probe/clicks.jsonl，由 `button_probe.py show` 和登记的测试卡对照。只记录，不触发任何动作。
除日报按钮的注入外都不回复，是因为网关回复按钮点击会失败（拿回调 token 当消息号）；进度和结果由脚本直接发到私聊。
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
ANSWER_SCRIPT = Path(os.environ.get("DT_AUTOLOOP_ANSWER", "~/.hermes/scripts/autoloop_answer.py")).expanduser()
PDAILY_SCRIPT = Path(os.environ.get("DT_PDAILY_ACT", "~/.hermes/scripts/pdaily_act.py")).expanduser()
CUSTODY_SCRIPT = Path(os.environ.get("DT_CUSTODY_REPORT", "~/.hermes/scripts/custody_report.py")).expanduser()
SECRETARY_SCRIPT = Path(os.environ.get("DT_SECRETARY_REPORT", "~/.hermes/scripts/secretary_report.py")).expanduser()
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


_CTX = None   # register 时存下插件上下文：日报按钮要用它注入会话


def pdaily(value):
    """日报拍板按钮（#235）：脚本核对、记账后，注入一句话叫猛鬼兽办；卡片在后台原地改。"""
    token, n = str(value.get("pdaily")), value.get("n")
    if set(value) != {"pdaily", "n"} or not CARD.match(token) or type(n) is not int or not 0 <= n <= 20:
        return None
    chat, operator = _chat_id(), _session("HERMES_SESSION_USER_ID")
    try:
        r = subprocess.run([PYTHON, str(PDAILY_SCRIPT), "button", token, str(n), "--chat", chat, "--operator", operator],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        out = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
    except (OSError, subprocess.SubprocessError, ValueError):
        out = {}
    _log(f"pdaily {n}：{out.get('msg', '脚本没给结果')}")
    if not out.get("ok"):
        if out.get("msg") and out["msg"] != "不认":       # 点击人核对过、但办不了（旧卡片等）：在私聊说一句
            _spawn(PDAILY_SCRIPT, ["say", "--text", out["msg"]], f"pdaily say {n}")
        return None
    if not out.get("entries"):
        if out.get("tell"):                             # 已经定过：说一句现在的状态（审查 P3）
            _spawn(PDAILY_SCRIPT, ["say", "--text", out["tell"]], f"pdaily say {n}")
        return None
    injected = False
    if _CTX is not None and out.get("inject"):
        try:
            injected = bool(_CTX.inject_message(out["inject"], origin={
                "platform": "feishu", "chat_id": chat, "chat_type": "dm", "user_id": operator}))
        except Exception:  # noqa: BLE001  注入失败不影响记账：after 会在私聊说怎么办
            injected = False
    return _spawn(PDAILY_SCRIPT, ["after", "--day", str(out.get("day")), "--entries", ",".join(out["entries"]),
                                  "--injected", "1" if injected else "0"], f"pdaily after {n} injected={injected}")


def _log(line):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


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
    if isinstance(value, dict) and "pdaily" in value:
        return pdaily(value)
    if isinstance(value, dict) and "secretary" in value:
        return secretary(value)
    if isinstance(value, dict) and "autoloop" in value:       # 中台卡片只认 {"autoloop": 令牌, "o": 选项号[, "v": 版本号]}
        o, v = value.get("o"), value.get("v")
        if set(value) - {"v"} != {"autoloop", "o"} or not CARD.match(str(value["autoloop"])) or type(o) is not int \
                or not 0 <= o < 100 or ("v" in value and (type(v) is not int or not 0 < v < 10 ** 9)):
            return None
        ver = ["--version", str(v)] if "v" in value else []
        return _spawn(ANSWER_SCRIPT, [value["autoloop"], str(o), *ver, "--chat", _chat_id(),
                                      "--operator", _session("HERMES_SESSION_USER_ID")], f"autoloop {o} v{v}")
    picked = parse(raw_args)
    if picked is None:
        return None
    script, verb, ref, extra = picked
    return _spawn(script, ["button", verb, ref, "--chat", _chat_id(), *extra], f"{verb} {ref}")


def secretary(value):
    """秘书汇报卡的「推翻」、撤销卡的「合并撤销 PR」、改选卡（#195 0B-5）：键要正好对、令牌必带，后台交给 secretary_report.py。"""
    verb, ref, card, o = value.get("secretary"), str(value.get("ref") or ""), str(value.get("card") or ""), value.get("o")
    keys = {"secretary", "ref", "card"} | ({"o"} if verb == "reselect" else set())
    if verb not in ("overturn", "merge_revert", "reselect") or set(value) != keys or not DECISION.match(ref) or not CARD.match(card) \
            or (verb == "reselect" and (type(o) is not int or not 0 < o < 100)):
        return None
    opt = ["--option", str(o)] if verb == "reselect" else []
    return _spawn(SECRETARY_SCRIPT, ["button", verb, ref, "--chat", _chat_id(), "--operator", _session("HERMES_SESSION_USER_ID"),
                                     "--card", card, *opt], f"secretary {verb} {ref}")


def _spawn(script, args, what):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    log = open(LOG, "a", encoding="utf-8")
    try:
        log.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {what}\n")
        log.flush()
        subprocess.Popen([PYTHON, str(script), *args], stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    finally:
        log.close()
    return None


def register(ctx):
    global _CTX
    _CTX = ctx
    ctx.register_command("card", handler=handle, description="卡片按钮：PR 合并 / 退回（#78），托管推翻 / 合并 revert PR（#133），秘书推翻 / 改选（#195 0B-5），中台卡片、按钮实测探针（#195），日报拍板（#235）")
