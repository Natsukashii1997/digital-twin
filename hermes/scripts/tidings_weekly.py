"""Tidings 周报推送（#55）：到点把 github-center 写好的周报卡片，用猛鬼兽身份发到常浩私聊。纯脚本、不调用模型。

用法：python3 tidings_weekly.py [--dry-run] [--force]
由 Hermes 无模型定时任务每 15 分钟调用一次：没到点、成功都什么都不打印（不多发消息），出错打印原因并以退出码 1 结束（定时任务会提醒）。
--dry-run 只打印这次会怎么做（要发的消息，或为什么不发），不上传、不发送、不记状态；
--force 不看设置和时间、本期已发过也再发一次（手动补发用）。

什么时候发（#58，github-center #61）：读 github-center 设置页存的 data/settings.json 里的 weekly（开关、星期、时间）。
- 没存过、读不出来或某项不合规，按默认值：开、周一、19:30（同 github-center src/settings/index.js 的 DEFAULTS.weekly）。
- 周报时间不能早于每日更新时间（github-center #83：统计日改按美国太平洋时间，出榜推后 8 小时）。设置文件是原样从服务器拉回来的，
  旧设置可能还是 11:30：和 github-center 的 readSettings 一样，每日更新早于 17:00 的按 18:00，周报早于每日更新的按每日更新后 1.5 小时（最晚 23:45）。
- 关着不发；过了最近一次设定时刻，这一期还没发过才发。能发到设定那天结束（数据要等时可延到设定时刻后 3 小时 15 分），
  过了就不补（整天错过不隔天补发）。

先检查再发（github-center docs/handoff/weekly-hermes.md）：
- 最新一份周报的统计日应是设定那天的前一天（北京时间）。还没有就先等，到设定时刻后 3 小时仍然没有、
  统计日更早、卡片文件为空或读不出来：只发一句说明，不发旧榜单。
- 卡片原样发出，只在最前面插一张固定横幅（同目录 assets/tidings-banner.png，横幅本身就是标题，按原比例完整显示）。横幅按内容哈希缓存 image_key，换图才重传；
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
import re
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
DEFAULT_WEEKLY = {"on": True, "day": 1, "at": "19:30"}   # 同 github-center DEFAULTS.weekly；day 0 = 周日（#83 前 11:30）
DEFAULT_DAILY_AT = "18:00"                                # 同 github-center DEFAULTS.daily.at
EARLIEST_DAILY = "17:00"                                  # 同 github-center EARLIEST
WEEKLY_AFTER = 90                                         # 周报早于每日更新时，迁到每日更新后这么多分钟
DAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]
WAIT = dt.timedelta(hours=3)                              # 到点后数据还没生成，最多等这么久再发说明
TICK = dt.timedelta(minutes=15)                           # 定时任务的间隔：等满 3 小时后至少还有一次机会发说明


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


def is_time(t):
    return isinstance(t, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) is not None


def read_weekly():
    """设置页的周报推送设置；文件没有、读不出来或某项不合规，那一项用默认值；周报早于每日更新时按迁移规则（同 github-center 的 readSettings）。"""
    try:
        s = json.loads((REPO / "data/settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        s = None
    s = s if isinstance(s, dict) else {}
    w = s.get("weekly") if isinstance(s.get("weekly"), dict) else {}
    d = s.get("daily") if isinstance(s.get("daily"), dict) else {}
    daily = d.get("at") if is_time(d.get("at")) and d["at"] >= EARLIEST_DAILY and int(d["at"][3:]) % 15 == 0 else DEFAULT_DAILY_AT
    on, day, at = w.get("on"), w.get("day"), w.get("at")
    at = at if is_time(at) else DEFAULT_WEEKLY["at"]
    if at < daily:
        m = min(int(daily[:2]) * 60 + int(daily[3:]) + WEEKLY_AFTER, 23 * 60 + 45)
        at = f"{m // 60:02d}:{m % 60:02d}"
    return {
        "on": on if isinstance(on, bool) else DEFAULT_WEEKLY["on"],
        "day": day if type(day) is int and 0 <= day <= 6 else DEFAULT_WEEKLY["day"],
        "at": at,
    }


def due(now, w, state):
    """到点了返回 (这一期的设定时刻, None)，不发返回 (None, 原因)。
    这一期 = 最近一次已过的设定时刻；从它起能发到那天结束，要等数据时延到它后 3 小时 15 分（设得很晚也能跨过午夜）。"""
    local = now.astimezone(BJ)
    if not w["on"]:
        return None, "周报推送在设置页关着"
    start = local.replace(hour=int(w["at"][:2]), minute=int(w["at"][3:]), second=0, microsecond=0)
    start -= dt.timedelta(days=(local.isoweekday() % 7 - w["day"]) % 7)
    if start > local:
        start -= dt.timedelta(days=7)
    end = max(dt.datetime.combine(start.date() + dt.timedelta(days=1), dt.time(), BJ), start + WAIT + TICK)
    if local >= end:
        return None, f"还没到{DAYS[w['day']]} {w['at']}"
    if state.get("last_slot") == start.isoformat():
        return None, "这一期已经发过"
    return start, None


def plan(now, start):
    """决定这次发什么：("card", 统计日, 卡片)、("text", 键, 一句说明)，数据还没就绪、还在等返回 None。
    start 是这一期的设定时刻；--force 时为 None，按现在算、不等。"""
    expected = ((start or now).astimezone(BJ).date() - dt.timedelta(days=1)).isoformat()
    day = latest_weekly()
    card = load_card(day) if day is not None and day >= expected else None
    if card is not None:
        return "card", day, card
    if start is not None and now < start + WAIT:
        return None
    if day is None:
        return "text", expected, "本周周报没有生成：没有找到任何周报文件，榜单可能没更新。"
    if day < expected:
        return "text", expected, f"本周周报没有生成：最新数据停在 {md(day)}，榜单可能没更新。"
    return "text", expected, f"本周周报没有生成：{md(day)}的卡片文件为空或读不出来。"


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
           "scale_type": "fit_horizontal", "preview": False, "corner_radius": "8px"}
    return {**card, "body": {**card["body"], "elements": [img, *card["body"]["elements"]]}}


def send(chat, msg_type, content, key):
    data = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", msg_type,
                 "--content", json.dumps(content, ensure_ascii=False), "--idempotency-key", key], "发送")
    return data.get("message_id")


def main(argv=None):
    a = argparse.ArgumentParser(description="把 Tidings 周报卡片发到常浩私聊")
    a.add_argument("--dry-run", action="store_true", help="只打印这次会怎么做")
    a.add_argument("--force", action="store_true", help="不看设置和时间、本期已发过也再发")
    a = a.parse_args(argv)
    now = dt.datetime.fromisoformat(os.environ["DT_NOW"]) if os.environ.get("DT_NOW") else dt.datetime.now(BJ)
    if now.tzinfo is None:          # DT_NOW 没写时区按北京时间
        now = now.replace(tzinfo=BJ)
    state = read_state()
    start = None
    if not a.force:
        start, why = due(now, read_weekly(), state)
        if start is None:
            if a.dry_run:
                print(f"不发：{why}。")
            return 0
    picked = plan(now, start)
    if picked is None:
        if a.dry_run:
            print("等数据：昨天那期周报还没生成，到设定时间后 3 小时仍没有再发说明。")
        return 0
    kind, day, payload = picked
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
    if start is not None:           # 这一期只发一次；--force 手动补发不算，不挡当期的正式推送
        state["last_slot"] = start.isoformat()
    try:
        write_state(state)
    except OSError as e:            # 已经发出，不报“没发出”；只提醒下次可能重发
        warnings.append(f"状态文件写不进（{e.strerror or type(e).__name__}），下次可能重发一次。")
    for w in warnings:
        print(f"Tidings 周报已发出，但{w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
