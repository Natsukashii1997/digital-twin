"""Tidings 周报推送（#55）：每周一 11:30 把 github-center 写好的周报卡片，用猛鬼兽身份发到常浩私聊。纯脚本、不调用模型。

用法：python3 tidings_weekly.py [--dry-run] [--force]
由 Hermes 无模型定时任务调用：成功什么都不打印（不再多发一条消息），出错打印原因并以退出码 1 结束（定时任务会提醒）。
--dry-run 只打印要发的消息，不上传、不发送、不记状态；--force 本期已发过也再发一次。

先检查再发（github-center docs/handoff/weekly-hermes.md）：
- 最新一份周报的统计日应是昨天（北京时间）。没有任何周报、统计日早于昨天、卡片文件为空或读不出来：只发一句说明，不发旧榜单。
- 卡片原样发出，只在最前面插一张固定横幅（同目录 assets/tidings-banner.png）。横幅按内容哈希缓存 image_key，换图才重传；
  上传失败照发不带图，并打印一句提醒。
- 同一统计日只发一次：本机状态文件记已发的统计日，发送时还带飞书幂等键。

环境变量：TIDINGS_REPO github-center 目录；FEISHU_HOME_CHANNEL 私聊 chat_id（Hermes 的 .env 里有，无模型任务会加载）；
DT_TIDINGS_STATE 状态文件；DT_TIDINGS_BANNER 横幅；DT_LARK_CLI 换 lark-cli（自测用）；DT_NOW（ISO 时间）指定现在，自测用。
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

LARK = os.environ.get("DT_LARK_CLI", "lark-cli")
PROFILE = "cli_a96f5f9ac63adcd4"               # 猛鬼兽应用（PRD D12：飞书只保留这一个机器人）
AGENT_ENV = ("HERMES", "OPENCLAW", "LARK_CHANNEL")   # lark-cli 看到这些变量会改读另一套配置，调用前去掉（同 twin_identity.py）
REPO = Path(os.environ.get("TIDINGS_REPO", "~/Documents/ChatGPT/个人/work/github-center")).expanduser()
STATE = Path(os.environ.get("DT_TIDINGS_STATE", "~/Library/Application Support/DigitalTwin/tidings_weekly.json")).expanduser()
BANNER = Path(os.environ.get("DT_TIDINGS_BANNER") or Path(__file__).with_name("assets") / "tidings-banner.png")
BJ = dt.timezone(dt.timedelta(hours=8))
TIMEOUT = 60


class SendError(Exception):
    """lark-cli 跑不起来、输出不对或飞书报错。"""


def md(day):
    return f"{int(day[5:7])} 月 {int(day[8:10])} 日"


def strip_env():
    return {k: v for k, v in os.environ.items() if not k.lstrip("_").startswith(AGENT_ENV)}


def lark(args, what, cwd=None):
    """跑一条 bot 身份的 lark-cli 命令，返回 data；出错抛 SendError（原因里不含凭据）。"""
    cmd = [LARK, "--profile", PROFILE, *args, "--as", "bot", "--format", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, env=strip_env(), cwd=cwd)
    except (OSError, subprocess.SubprocessError) as e:
        raise SendError(f"{what}：lark-cli 跑不起来（{type(e).__name__}）")
    d = None
    for raw in (r.stdout, r.stderr):      # lark-cli 的错误 JSON 走 stderr
        try:
            d = json.loads(raw)
            break
        except ValueError:
            continue
    if not isinstance(d, dict):
        raise SendError(f"{what}：lark-cli 输出不是 JSON（退出码 {r.returncode}）")
    if not d.get("ok", True):
        raise SendError(f"{what}失败：{str((d.get('error') or {}).get('message', '未知错误'))[:200]}")
    return d.get("data") or {}


def read_state():
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STATE)


def latest_weekly():
    """最新一期的统计日（按 .feishu.md 或 .card.json 的文件名），没有返回 None。"""
    days = {p.name.split(".")[0] for p in (REPO / "data/weekly").glob("*.*") if p.name.endswith((".feishu.md", ".card.json"))}
    return max(days) if days else None


def load_card(day):
    """读卡片；为空、读不出来、不是卡片返回 None。"""
    try:
        card = json.loads((REPO / "data/weekly" / f"{day}.card.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(card, dict) and isinstance(card.get("body"), dict) and isinstance(card["body"].get("elements"), list)
    return card if ok else None


def plan(now):
    """决定这次发什么：("card", 统计日, 卡片) 或 ("text", 键, 一句说明)。"""
    expected = (now.astimezone(BJ).date() - dt.timedelta(days=1)).isoformat()
    day = latest_weekly()
    if day is None:
        return "text", expected, "本周周报没有生成：没有找到任何周报文件，榜单可能没更新。"
    if day < expected:
        return "text", expected, f"本周周报没有生成：最新数据停在 {md(day)}，榜单可能没更新。"
    card = load_card(day)
    if card is None:
        return "text", expected, f"本周周报没有生成：{md(day)}的卡片文件为空或读不出来。"
    return "card", day, card


def banner_key(state, warnings):
    """横幅的 image_key：内容没变用缓存，变了或没有就上传；失败返回 None 并记一句提醒。"""
    try:
        sha = hashlib.sha256(BANNER.read_bytes()).hexdigest()
    except OSError:
        warnings.append(f"横幅图片读不出来（{BANNER}），本期卡片没带图。")
        return None
    cached = state.get("banner") or {}
    if cached.get("sha256") == sha and cached.get("image_key"):
        return cached["image_key"]
    try:
        key = lark(["im", "images", "create", "--data", '{"image_type":"message"}', "--file", f"image={BANNER.name}"],
                   "上传横幅", cwd=str(BANNER.parent)).get("image_key")
    except SendError as e:
        warnings.append(f"{e}，本期卡片没带图。")
        return None
    if not key:
        warnings.append("上传横幅没有返回 image_key，本期卡片没带图。")
        return None
    state["banner"] = {"sha256": sha, "image_key": key}
    return key


def with_banner(card, key):
    img = {"tag": "img", "img_key": key, "alt": {"tag": "plain_text", "content": "本周 GitHub 涨星榜"},
           "scale_type": "crop_center", "preview": False, "corner_radius": "8px"}
    return {**card, "body": {**card["body"], "elements": [img, *card["body"]["elements"]]}}


def send(chat, msg_type, content, key):
    data = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", msg_type,
                 "--content", json.dumps(content, ensure_ascii=False), "--idempotency-key", key], "发送")
    return data.get("message_id")


def main(argv=None):
    a = argparse.ArgumentParser(description="把 Tidings 周报卡片发到常浩私聊")
    a.add_argument("--dry-run", action="store_true", help="只打印要发的消息")
    a.add_argument("--force", action="store_true", help="本期已发过也再发")
    a = a.parse_args(argv)
    now = dt.datetime.fromisoformat(os.environ["DT_NOW"]) if os.environ.get("DT_NOW") else dt.datetime.now(BJ)
    kind, day, payload = plan(now)
    state = read_state()
    sent = state.setdefault("sent", {})
    tag = f"{kind}:{day}"
    if tag in sent and not (a.force or a.dry_run):
        return 0
    if a.dry_run:
        print(json.dumps({"msg_type": "interactive" if kind == "card" else "text", "content": payload}, ensure_ascii=False, indent=2))
        return 0
    chat = os.environ.get("FEISHU_HOME_CHANNEL", "")
    if not chat.startswith("oc_"):
        print("Tidings 周报没发出：没有私聊 chat_id（FEISHU_HOME_CHANNEL）。")
        return 1
    warnings = []
    # 飞书幂等键挡住同一期重复发；--force 换一个键才能真的再发。
    idem = f"tidings-weekly-{'miss-' if kind == 'text' else ''}{day}" + (f"-{int(now.timestamp())}" if a.force else "")
    try:
        if kind == "card":
            key = banner_key(state, warnings)
            mid = send(chat, "interactive", with_banner(payload, key) if key else payload, idem)
        else:
            mid = send(chat, "text", {"text": payload}, idem)
    except SendError as e:
        write_state(state)          # 横幅已上传的话留着 image_key，下次不用再传
        print(f"Tidings 周报没发出：{e}")
        return 1
    sent[tag] = {"at": now.isoformat(timespec="seconds"), "message_id": mid}
    try:
        write_state(state)
    except OSError as e:            # 已经发出，不报“没发出”；只提醒下次可能重发
        warnings.append(f"状态文件写不进（{e.strerror or type(e).__name__}），下次可能重发一次。")
    for w in warnings:
        print(f"Tidings 周报已发出，但{w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
