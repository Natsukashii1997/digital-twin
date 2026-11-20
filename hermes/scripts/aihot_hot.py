"""AI HOT 热点榜推送（#163）：每个工作日 09:45 把 aihot.news「AI 热点榜」TOP10 做成卡片，用猛鬼兽身份发到常浩私聊。纯脚本、不调用模型。

用法：python3 aihot_hot.py [--dry-run] [--force]
由 Hermes 无模型定时任务 `45 9 * * 1-5` 调用（周一到周五，不跟节假日调休，常浩 2026-11-11 定）。
成功什么都不打印；取不到数据（接口失败、不到 10 条、榜单 48 小时没更新）或发送、回读失败时，打印一句原因、退出码 1，
由定时任务把这句发到私聊。--dry-run 只打印要发的卡片，不上传、不发送、不记状态；--force 当天已发过也再发一次（手动补发）。

样式（常浩 2026-11-11 选定「名次栏」，样稿在证据目录 aihot-card-design-20261111/samples/chosen-card-5c.json）：
不要表头，横幅就是标题（同 GitHub 周榜卡片），聊天列表预览文字放 config.summary；最上面一张固定横幅（同目录 assets/aihot-banner.png，按内容哈希缓存 image_key，换图才重传，上传失败照发不带图并提醒）；
「TOP 3」每条一行灰底，左列奖牌和红色大号名次，右边标题 + 话题标签 + 浅灰概括；「No.04–10」左列灰色名次，右边标题 + 标签 + 一句概括。
不放来源数、参与者、更新时间和原文链接；标题只链到 aihot.news。接口不返回热度指数和涨跌，不显示。

数据：GET <base>hot-topics（10 条）；概括取 GET <base>stories/{publicId} 的 digest 第一段，按整句截，不出省略号。
单条详情取不到时那条只显示标题。接口内容一律当不可信文本：只取白名单字段、去控制字符、截断、转义 lark_md 语法字符。

记录（证据随机制出生）：每次运行在 DigitalTwin/aihot/runs.jsonl 记一行；当天发出的卡片存 <日期>.card.json；状态（已发日期、横幅 image_key）在 state.json。

环境变量：FEISHU_HOME_CHANNEL 私聊 chat_id（无模型定时任务的环境里没有，这时从 ~/.hermes/.env 读，#165）；DT_AIHOT_DIR 记录目录；DT_AIHOT_BASE 接口地址（自测用 file://）；
DT_AIHOT_BANNER 横幅；DT_LARK_CLI 换 lark-cli；DT_NOW（ISO 时间）指定现在。
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).parent))
from feishu_home import home_chat  # noqa: E402
import netwait  # noqa: E402  唤醒后先等网络（P9）

LARK = os.environ.get("DT_LARK_CLI", "lark-cli")
PROFILE = "cli_a96f5f9ac63adcd4"                       # 猛鬼兽应用（PRD D12）
AGENT_ENV = ("HERMES", "OPENCLAW", "LARK_CHANNEL")     # lark-cli 看到这些变量会改读另一套配置，调用前去掉（同 tidings_weekly.py）
BASE = os.environ.get("DT_AIHOT_BASE", "https://aihot.news/api/v1/")
DIR = Path(os.environ.get("DT_AIHOT_DIR", "~/Library/Application Support/DigitalTwin/aihot")).expanduser()
BANNER = Path(os.environ.get("DT_AIHOT_BANNER") or Path(__file__).with_name("assets") / "aihot-banner.png")
BJ = dt.timezone(dt.timedelta(hours=8))
UA = "digital-twin-aihot/1.0"
TIMEOUT = 20
TRIES = 3                      # 榜单接口偶发失败时，同一次运行里最多试 3 次；单条详情只试 1 次，取不到就不放概括
STALE = dt.timedelta(hours=48)
WEEK = "一二三四五六日"
MEDAL = {1: "🥇", 2: "🥈", 3: "🥉"}
TOPICS = [   # 按标题关键词打的话题标签，顺序即优先级；都没命中记「动态」
    (r"IPO|融资|估值|收购|上市|营收|财报", "💰 资本", "orange"),
    (r"TPU|GPU|芯片|算力|数据中心|服务器", "🔧 算力", "purple"),
    (r"调查|诉讼|起诉|监管|罚款|情报|禁令|泄露", "⚠️ 风波", "red"),
    (r"开源", "🔓 开源", "green"),
    (r"模型|发布|开放", "🚀 新模型", "blue"),
    (r"测试|新增|上线|接入|功能|推出", "🧩 新功能", "turquoise"),
]
FOOT = "👉 点标题进 AI HOT 看详情"
TOP_LEN, REST_LEN = 120, 70     # 前三、后七概括的字数上限（整句累加）


class Fail(Exception):
    """这次没发成的原因，一句话。"""


# ---------- 取数 ----------

def get_json(path, tries=1):
    last = None
    for n in range(tries):
        try:
            req = urllib.request.Request(BASE + path, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError) as e:
            last = e
            if n + 1 < tries:
                time.sleep(float(os.environ.get("DT_AIHOT_RETRY_WAIT", "10")))
    raise Fail(f"接口取不到（{type(last).__name__}）")


def clean(s, limit):
    """去控制字符、压空白、截断；把会被 lark_md 当语法的字符换成全角，防止外部文字变成链接、@ 或富文本标签。"""
    s = re.sub(r"[\x00-\x1f\x7f​-‏ -‮⁦-⁩]", " ", str(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    s = s.translate(str.maketrans({"[": "［", "]": "］", "<": "＜", ">": "＞", "*": "＊", "~": "～",
                                   "`": "'", "\\": "＼", "_": "＿"}))
    return s[:limit]


def aihot_url(u):
    """只收 aihot.news 的 http/https 链接；括号等会打断 markdown 链接的字符做百分号编码。其余返回 None。"""
    if not isinstance(u, str) or len(u) > 300 or re.search(r"\s", u):
        return None
    p = urlsplit(u)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not (host == "aihot.news" or host.endswith(".aihot.news")):
        return None
    return quote(u, safe=":/?#@!$&'*+,;=%-._~")


def sentences(text):
    return [s for s in re.split(r"(?<=[。！？])", text) if s.strip()]


def summary(digest, limit):
    """digest 第一段按整句累加到 limit 以内；第一句就超长时按分句（，；：）累加，收成一句加句号。不出省略号。"""
    paras = [p for p in str(digest or "").split("\n") if p.strip()]
    first = clean(paras[0], 2000) if paras else ""
    if not first:
        return ""
    out = ""
    for s in sentences(first):
        if len(out) + len(s) > limit:
            break
        out += s
    if out:
        return out
    for c in re.split(r"(?<=[，；：])", sentences(first)[0]):
        if out and len(out) + len(c) > limit:
            break
        out += c
    return out.rstrip("，；：。") + "。"


def topic(title):
    for pat, label, color in TOPICS:
        if re.search(pat, title):
            return label, color
    return "📰 动态", "neutral"


def fetch(now):
    """取榜单和每条的概括。不够 10 条、榜单过期抛 Fail；单条详情取不到只是没有概括。"""
    raw = get_json("hot-topics", TRIES)
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise Fail("接口返回的格式不对")
    out, latest = {}, None
    for it in items:
        if not isinstance(it, dict):
            continue
        rank = it.get("rank")
        if type(rank) is not int or not 1 <= rank <= 10 or rank in out:
            continue
        title = clean(it.get("title"), 80)
        if not title:
            continue
        links = it.get("links") if isinstance(it.get("links"), dict) else {}
        try:
            t = dt.datetime.fromisoformat(str(it.get("latestAt")).replace("Z", "+00:00"))
            latest = t if latest is None or t > latest else latest
        except ValueError:
            pass
        story = re.search(r"/story/([0-9A-Za-z-]{8,64})$", str(links.get("story") or ""))
        out[rank] = {"rank": rank, "title": title, "url": aihot_url(links.get("aihot")) or aihot_url(links.get("story")),
                     "story": story.group(1) if story else None, "digest": ""}
    if len(out) < 10:
        raise Fail(f"榜单只拿到 {len(out)} 条，不到 10 条")
    if latest is None or now - latest > STALE:
        raise Fail("榜单超过 48 小时没更新")
    for it in out.values():
        if it["story"]:
            try:
                st = get_json(f"stories/{it['story']}").get("story") or {}
                it["digest"] = st.get("digest") if isinstance(st.get("digest"), str) else ""
            except (Fail, AttributeError):
                it["digest"] = ""
    return [out[r] for r in range(1, 11)]


# ---------- 排版 ----------

def md(content, **kw):
    return {"tag": "markdown", "content": content, **kw}


def build_card(items, now, banner_key=None):
    els = []
    if banner_key:
        els.append({"tag": "img", "img_key": banner_key, "alt": {"tag": "plain_text", "content": "当日 AI HOT 热门榜"},
                    "scale_type": "fit_horizontal", "preview": False, "corner_radius": "8px"})
    els.append(md("🔥 **TOP 3**"))
    for i in items:
        if i["rank"] == 4:
            els += [{"tag": "hr"}, md("📈 **No.04–10**")]
        top = i["rank"] <= 3
        label, color = topic(i["title"])
        title = f"**[{i['title']}]({i['url']})**" if i["url"] else f"**{i['title']}**"
        right = [md(f"{title}　<text_tag color='{color}'>{label}</text_tag>")]
        s = summary(i["digest"], TOP_LEN if top else REST_LEN)
        if s:
            right.append(md(f"<font color='grey'>{s}</font>"))
        num = (f"{MEDAL[i['rank']]}\n**<font color='red'>{i['rank']:02d}</font>**" if top
               else f"**<font color='grey'>{i['rank']:02d}</font>**")
        row = {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px", "columns": [
            {"tag": "column", "width": "48px", "vertical_align": "top",
             "elements": [md(num, text_size="heading" if top else "normal", text_align="center")]},
            {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top", "vertical_spacing": "4px",
             "elements": right}]}
        if top:
            row["background_style"] = "grey"
        els.append(row)
    els += [{"tag": "hr"}, md(f"<font color='grey'>{FOOT}｜数据：aihot.news</font>", text_size="notation")]
    # 不要表头，横幅就是标题（常浩 11-11，同 GitHub 周榜卡片）；聊天列表里的预览文字用 config.summary
    return {"schema": "2.0",
            "config": {"update_multi": True, "summary": {"content": summary_text(now)}},
            "body": {"elements": els}}


def summary_text(now):
    return f"🔥 AI 热点榜 TOP10 · {now:%m-%d}（周{WEEK[now.weekday()]}）"


# ---------- 飞书 ----------

def lark(args, what, cwd=None):
    """跑一条 bot 身份的 lark-cli 命令，返回 data；出错抛 Fail（原因里不含凭据）。"""
    env = {k: v for k, v in os.environ.items() if not k.lstrip("_").startswith(AGENT_ENV)}
    try:
        r = subprocess.run([LARK, "--profile", PROFILE, *args, "--as", "bot", "--format", "json"],
                           capture_output=True, text=True, timeout=60, env=env, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as e:
        raise Fail(f"{what}：lark-cli 跑不起来（{type(e).__name__}）")
    for raw in (r.stdout, r.stderr):      # lark-cli 的错误 JSON 走 stderr
        try:
            d = json.loads(raw)
            break
        except ValueError:
            d = None
    if not isinstance(d, dict):
        raise Fail(f"{what}：lark-cli 输出不是 JSON（退出码 {r.returncode}）")
    if not d.get("ok", True):
        raise Fail(f"{what}失败：{str((d.get('error') or {}).get('message', '未知错误'))[:200]}")
    return d.get("data") or {}


def banner_key(state, warnings):
    """横幅的 image_key：内容没变用缓存，变了或没有就上传；失败返回 None 并记一句提醒。"""
    try:
        sha = hashlib.sha256(BANNER.read_bytes()).hexdigest()
    except OSError:
        warnings.append(f"横幅图片读不出来（{BANNER.name}），今天卡片没带图。")
        return None
    cached = state.get("banner") or {}
    if cached.get("sha256") == sha and cached.get("image_key"):
        return cached["image_key"]
    try:
        key = lark(["im", "images", "create", "--data", '{"image_type":"message"}', "--file", f"image={BANNER.name}"],
                   "上传横幅", cwd=str(BANNER.parent)).get("image_key")
    except Fail as e:
        warnings.append(f"{e}，今天卡片没带图。")
        return None
    if not key:
        warnings.append("上传横幅没有返回 image_key，今天卡片没带图。")
        return None
    state["banner"] = {"sha256": sha, "image_key": key}
    return key


# ---------- 状态与记录 ----------

def read_state():
    try:
        s = json.loads((DIR / "state.json").read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def log_run(rec):
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        with open(DIR / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass                      # 记录写不进不影响发送结果


def main(argv=None):
    a = argparse.ArgumentParser(description="把 AI HOT 热点榜 TOP10 卡片发到常浩私聊")
    a.add_argument("--dry-run", action="store_true", help="只打印要发的卡片")
    a.add_argument("--force", action="store_true", help="当天已发过也再发一次")
    a = a.parse_args(argv)
    now = dt.datetime.fromisoformat(os.environ["DT_NOW"]) if os.environ.get("DT_NOW") else dt.datetime.now(BJ)
    now = now.replace(tzinfo=BJ) if now.tzinfo is None else now.astimezone(BJ)
    day = now.date().isoformat()
    state = read_state()
    sent = state.setdefault("sent", {})
    if day in sent and not (a.force or a.dry_run):
        return 0
    if not a.dry_run:
        netwait.guard("aihot", ("aihot", "feishu"), "今天的 AI HOT 热点榜")   # 唤醒后先等网络（P9），等不到说原因、退出码 1
    rec = {"day": day, "at": now.isoformat(timespec="seconds"), "ok": False}
    warnings = []
    try:
        items = fetch(now)
        if a.dry_run:
            print(json.dumps(build_card(items, now, "img_DRY_RUN"), ensure_ascii=False, indent=1))
            return 0
        chat = home_chat()
        if not chat.startswith("oc_"):
            raise Fail("没有私聊 chat_id（FEISHU_HOME_CHANNEL，环境变量和 ~/.hermes/.env 里都没有）")
        card = build_card(items, now, banner_key(state, warnings))
        idem = f"aihot-hot-{day}" + (f"-{int(now.timestamp())}" if a.force else "")
        mid = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "interactive",
                    "--content", json.dumps(card, ensure_ascii=False), "--idempotency-key", idem], "发卡片").get("message_id")
        if not mid:
            raise Fail("发卡片没拿到消息号")
        rec["message_id"] = mid
        sent[day] = {"at": now.isoformat(timespec="seconds"), "message_id": mid}
        got = lark(["im", "+messages-mget", "--message-ids", mid], "回读")
        m = (got.get("messages") or [{}])[0]
        if m.get("msg_type") != "interactive" or FOOT not in str(m.get("content")):   # 没有表头，核对底部固定的一行字
            raise Fail(f"卡片已发（{mid}），但回读对不上卡片内容")
        rec["ok"] = True
        try:
            write_json(DIR / f"{day}.card.json", card)
        except OSError:
            pass
    except Fail as e:
        rec["error"] = str(e)
        print(f"AI 热点榜今天没发成：{e}。")
    finally:
        if not a.dry_run:
            try:
                write_json(DIR / "state.json", state)     # 已发日期、横幅 image_key；发出后回读失败也算已发，不重发
            except OSError as e:
                warnings.append(f"状态文件写不进（{e.strerror or type(e).__name__}），下次可能重发一次。")
            rec["warnings"] = warnings
            log_run(rec)
    if not rec["ok"]:
        return 1
    for w in warnings:
        print(f"AI 热点榜已发出，但{w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
