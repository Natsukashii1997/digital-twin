#!/usr/bin/env python3
"""飞书可信身份桥接（B2.1 T03）：核实“哪条飞书消息触发了这次调用”，只在它是常浩本人在私聊里说的时候才映射为 user。

真值来自飞书，不来自文本：拿 message_id 用猛鬼兽应用的 bot 身份回读消息（lark-cli im +messages-mget --as bot），核对
① 发送者是用户且 open_id 等于本机受控配置里的 owner_open_id（twin_state.owner_open_id）；
② 是常浩和 Hermes 的私聊：回读结果只给 chat_id，不给会话类型，所以必须等于本机配置的 owner_chat_id（twin_state.owner_chat_id）；
   没配 owner_chat_id 一律拒；回读里就算带 chat_type=p2p 也只是附加证据，不能替代 chat_id 绑定；明说是群聊 / 话题群的拒；
   回读到的 message_id 必须就是传入的那条，sender_type 必须明确是 user（缺失不猜）——P0-1，ChatGPT 评审 2026-10-29；
③ 在时效内（默认 10 分钟；lark-cli 给的时间只到分钟，另留 2 分钟偏差）；
④ 这条消息没被用过——占用登记在状态库 identity_uses（Store.claim_message），由调用方和状态变化放在同一事务里。
模型文本、系统提示里的 User ID、环境变量 HERMES_SESSION_* 都只是线索：命令是模型拼的，VAR=x cmd 就能覆盖，
所以 HERMES_SESSION_MESSAGE_ID 只当“去飞书查哪条”的指针，不当依据。

局限（见 docs/B2.1-事件与身份协议.md）：只证明“这一回合由常浩的消息触发”，不证明“常浩批准了这个动作”；模型有 shell，
脚本层任何检查都可能被模型拼的命令绕过（含 DT_LARK_CLI、PATH），硬保证要网关侧签发绑定 message_id 的凭据，留 B2.x。
本模块不输出、不记录任何 open_id / chat_id 值；拒绝原因只说“不是常浩本人”。

  python3 twin_identity.py verify --message-id om_xxx            只核实、不占用；末行 JSON ok/msg
  python3 twin_identity.py setup-chat --message-id om_xxx [--write]   安装时用：从常浩发的一条私聊取 chat_id 写进 600 配置
      （先用 bot 身份查会话 chat_mode，必须是 p2p 才写；查不到就 fail closed 给手工配置说明——P0-2）
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LARK = os.environ.get("DT_LARK_CLI", "lark-cli")
PROFILE = "cli_a96f5f9ac63adcd4"               # 猛鬼兽应用（PRD D12：飞书只保留这一个机器人）；bot 身份读自己收到的消息
AGENT_ENV = ("HERMES", "OPENCLAW", "LARK_CHANNEL")   # lark-cli 看到这些变量会改读另一套配置，调用前去掉（同 report_data.py）
MAX_AGE = 600          # 秒：消息多久以内算“这一回合”
SKEW = 120             # 秒：时钟偏差 + create_time 只到分钟
MESSAGE_ID = re.compile(r"^om_[A-Za-z0-9]{6,}$")
CHAT_ID = re.compile(r"^oc_[A-Za-z0-9_]{6,}$")
METHOD = "feishu_message"
TIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


class IdentityError(Exception):
    """核实做不下去（回读失败、输出不对）。原因里不含身份值。"""


def strip_env():
    return {k: v for k, v in os.environ.items() if not k.lstrip("_").startswith(AGENT_ENV)}


def normalize(m):
    """lark-cli 返回的一条消息 → 统一形状；缺的留 None。只取核实要用的字段，不带正文、不带名字。"""
    s = m.get("sender") or {}
    open_id = None
    if s.get("id") and s.get("id_type") in (None, "open_id"):
        open_id = s["id"]
    elif isinstance(s.get("sender_id"), dict):
        open_id = s["sender_id"].get("open_id")
    return {"message_id": m.get("message_id"), "sender_open_id": open_id, "sender_type": s.get("sender_type"),
            "chat_id": m.get("chat_id"), "chat_type": m.get("chat_type") or m.get("chat_mode"),
            "create_time": m.get("create_time"), "deleted": bool(m.get("deleted"))}


def _lark_json(args, timeout, what):
    """跑一条 bot 身份的 lark-cli 命令，返回解析后的 JSON 字典；跑不起来、输出不对、飞书报错抛 IdentityError。"""
    cmd = [LARK, "--profile", PROFILE, *args, "--as", "bot", "--format", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=strip_env())
    except (OSError, subprocess.SubprocessError) as e:
        raise IdentityError(f"lark-cli 跑不起来：{type(e).__name__}")
    d = None
    for raw in (r.stdout, r.stderr):      # lark-cli 的错误 JSON 走 stderr
        try:
            d = json.loads(raw)
            break
        except ValueError:
            continue
    if not isinstance(d, dict):
        raise IdentityError(f"lark-cli 输出不是 JSON（退出码 {r.returncode}）")
    if not d.get("ok", True):
        raise IdentityError(f"{what}失败：{str((d.get('error') or {}).get('message', '未知错误'))[:120]}")
    return d


def lark_fetch(message_id, timeout=60):
    """用 bot 身份回读一条消息。查不到返回 None；跑不起来、输出不对、飞书报错、回读不止一条 / 不是这条 / 字段不全抛 IdentityError。"""
    if not MESSAGE_ID.match(message_id or ""):
        raise IdentityError("message_id 不是飞书消息 id（om_ 开头）")
    d = _lark_json(["im", "+messages-mget", "--message-ids", message_id, "--no-reactions"], timeout, "回读")
    data = d.get("data") or {}
    msgs = data.get("messages") if isinstance(data, dict) else None
    if not msgs:
        return None
    if not isinstance(msgs, list) or len(msgs) != 1 or not isinstance(msgs[0], dict):
        raise IdentityError(f"回读应恰好一条消息，实际 {len(msgs) if isinstance(msgs, list) else '?'} 条")
    m = normalize(msgs[0])
    if m["message_id"] != message_id:
        raise IdentityError("回读到的不是这条消息（message_id 不一致）")
    if not all(m.get(k) for k in ("message_id", "sender_open_id", "sender_type", "chat_id", "create_time")):
        raise IdentityError("回读结果字段不全（发送者 / 会话 / 时间缺一不可）")
    return m


def lark_chat_mode(chat_id, timeout=60):
    """用 bot 身份查一个会话的类型（P0-2）：GET /open-apis/im/v1/chats/{chat_id}，2026-10-29 真实探针确认 data.chat_mode 给
    p2p / group / topic（证据 evidence/b2.1-rework-20261029/probe-chat.masked.json，已打码）。返回小写的 chat_mode；
    接口没给出类型返回 None（调用方必须 fail closed）；跑不起来、报错抛 IdentityError。不记录、不输出 chat_id 值。"""
    if not CHAT_ID.match(chat_id or ""):
        raise IdentityError("chat_id 不是飞书会话 id（oc_ 开头）")
    d = _lark_json(["api", "GET", f"/open-apis/im/v1/chats/{chat_id}"], timeout, "查会话")
    data = d.get("data") or {}
    mode = data.get("chat_mode") if isinstance(data, dict) else None
    return str(mode).strip().lower() or None if mode else None


def write_secret(target, value):
    """把一行机密写进 600 文件：先写同目录临时文件（O_EXCL、600），再原子替换；任何一步失败都删临时文件、原文件不动，异常原样抛出。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = target.with_name(f".{target.name}.tmp{os.getpid()}")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value + "\n")
        os.chmod(str(tmp), 0o600)
        os.replace(str(tmp), str(target))
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def parse_time(v, tz=None):
    """create_time：lark-cli 给本机时区的 'YYYY-MM-DD HH:MM'；也接受 ISO 和秒 / 毫秒时间戳。返回带时区的时间或 None。"""
    if v is None or v == "":
        return None
    tz = tz or dt.datetime.now().astimezone().tzinfo
    s = str(v).strip()
    if s.isdigit():
        n = int(s)
        return dt.datetime.fromtimestamp(n / 1000 if n > 10 ** 11 else n, tz)
    for fmt in TIME_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    try:
        d = dt.datetime.fromisoformat(s)
        return d if d.tzinfo else d.replace(tzinfo=tz)
    except ValueError:
        return None


def verify_message(message_id, owner, fetch=None, now=None, max_age=MAX_AGE, owner_chat_id=None):
    """核实一条飞书消息是不是常浩本人最近在私聊里发的。纯函数：fetch(message_id) 给回读结果（见 normalize），
    owner / owner_chat_id 由调用方从本机受控配置读。返回 {"ok", "reason"/"age_s"...}，任何分支都不带身份值。"""
    fetch = fetch or lark_fetch
    now = now or dt.datetime.now().astimezone()

    def fail(reason, retryable=False):
        """retryable=True 是临时失败（本机配置缺失、回读失败 / 出错）：调用方可以不记事件、稍后用同一事件重试（P1-2）；
        其余是永久拒绝。"""
        return {"ok": False, "method": METHOD, "message_id": message_id, "reason": reason, "retryable": retryable}

    if not owner:
        return fail("还没配置常浩的 open_id（owner_open_id）", retryable=True)
    if not MESSAGE_ID.match(message_id or ""):
        return fail("message_id 不是飞书消息 id（om_ 开头）")
    try:
        m = fetch(message_id)
    except IdentityError as e:
        return fail(f"回读飞书失败：{e}", retryable=True)
    except Exception as e:  # noqa: BLE001 —— 回读环节任何意外都按不通过，不带出细节
        return fail(f"回读飞书出错：{type(e).__name__}", retryable=True)
    if not m:
        return fail("飞书查不到这条消息（id 不存在，或机器人看不到）")
    if m.get("message_id") != message_id:
        return fail("回读到的不是这条消息（message_id 不一致）")
    if m.get("deleted"):
        return fail("这条消息已撤回")
    if m.get("sender_type") != "user":
        return fail("发送者不是用户（应用 / 系统消息，或回读没给出发送者类型，不猜）")
    if not m.get("sender_open_id") or m["sender_open_id"] != owner:
        return fail("发送者不是常浩本人")
    if not owner_chat_id:
        return fail("还没配置 owner_chat_id，不能确认是常浩和 Hermes 的私聊（回读带 p2p 标志也不算）", retryable=True)
    kind = str(m.get("chat_type") or "").lower()
    if kind and kind != "p2p":
        return fail("回读显示这不是私聊（群聊 / 话题群）")
    if not m.get("chat_id") or m["chat_id"] != owner_chat_id:
        return fail("不是本机配置的那条常浩和 Hermes 的私聊会话（chat_id 不一致；p2p 标志只是附加证据）")
    at = parse_time(m.get("create_time"), tz=now.tzinfo)
    if at is None:
        return fail("消息没有可用的时间")
    age = (now - at).total_seconds()
    if age > max_age + SKEW:
        return fail(f"消息已过时效（约 {int(age)} 秒前，上限 {max_age} 秒）")
    if age < -SKEW:
        return fail("消息时间在未来，时钟不对")
    return {"ok": True, "method": METHOD, "message_id": message_id, "age_s": max(int(age), 0), "chat": "p2p"}


# ---------- 命令行（只核实、安装时配私聊 chat_id）----------
def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False, default=str))
    sys.exit(0 if ok else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--message-id", help="不给就读 HERMES_SESSION_MESSAGE_ID（只是指针，真值仍从飞书回读）")
    v.add_argument("--max-age", type=int, default=MAX_AGE)
    c = sub.add_parser("setup-chat")
    c.add_argument("--message-id", required=True, help="常浩在和 Hermes 的私聊里发的任意一条消息")
    c.add_argument("--write", action="store_true", help="不带只预览；带了才把 chat_id 写进 600 配置文件（不打印值）")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import twin_state as T   # 同目录；只用它读本机受控配置
    try:
        owner, chat = T.owner_open_id(), T.owner_chat_id()
    except T.StateError as e:
        done(False, str(e), error="NotAuthorized")
    if not owner:
        done(False, T.OWNER_HINT, error="NotAuthorized")
    mid = a.message_id or os.environ.get("HERMES_SESSION_MESSAGE_ID")
    if a.cmd == "verify":
        r = verify_message(mid, owner, max_age=a.max_age, owner_chat_id=chat)
        done(r["ok"], "是常浩本人最近在私聊里发的" if r["ok"] else f"核实不通过：{r['reason']}",
             **{k: v for k, v in r.items() if k != "ok"})
    if a.cmd == "setup-chat":
        try:
            m = lark_fetch(mid)
        except IdentityError as e:
            done(False, f"回读飞书失败：{e}")
        if not m or m.get("sender_open_id") != owner or m.get("sender_type") not in (None, "user"):
            done(False, "这条消息不是常浩本人发的，不能拿它的会话当私聊", error="NotAuthorized")
        if not m.get("chat_id"):
            done(False, "回读结果没有 chat_id")
        target = Path(os.environ.get("DT_OWNER_CHAT_FILE") or T.DEFAULT_OWNER_CHAT_FILE).expanduser()
        manual = (f"手工配置：在飞书客户端确认这条消息所在会话确实是你和猛鬼兽的私聊，再把它的 chat_id 单独一行写进 {target}，"
                  "chmod 600；确认不了就不要写，群聊写进去等于把群里任何回合都当成你的私聊")
        # P0-2：有本人消息不等于私聊。用 bot 身份查会话 chat_mode，必须明确是 p2p 才能登记；查不到一律不写（fail closed）
        try:
            mode = lark_chat_mode(m["chat_id"])
        except IdentityError as e:
            done(False, f"查不到这个会话的类型，不写（fail closed）：{e}。{manual}", error="ChatModeUnknown", path=str(target), write=False)
        if mode is None:
            done(False, f"飞书没有给出这个会话的类型，不写（fail closed）。{manual}", error="ChatModeUnknown", path=str(target), write=False)
        if mode != "p2p":
            done(False, f"这条消息所在会话不是私聊（chat_mode={mode}），不能登记成 owner_chat_id", error="NotAuthorized",
                 path=str(target), write=False)
        if not a.write:
            done(True, f"预览：会话类型已确认是私聊（p2p），会把它的 chat_id 写进 {target}（600）；确认后加 --write",
                 path=str(target), write=False, chat_mode=mode)
        try:
            write_secret(target, m["chat_id"])
        except OSError as e:
            done(False, f"写入 {target} 失败（{type(e).__name__}），原文件（如有）保持不变", error="WriteFailed", path=str(target), write=False)
        done(True, f"已写入 {target}（600，先写临时文件再原子替换）；twin_state 用它判断私聊", path=str(target), write=True, chat_mode=mode)


if __name__ == "__main__":
    main()
