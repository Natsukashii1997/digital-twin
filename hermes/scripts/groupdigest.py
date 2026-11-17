"""AI 前沿群每日学习报告（#192，常浩 2026-11-16 定）：猛鬼兽读飞书外部群「Codex重置许愿群」前一天的全部消息，
顺着链接抓源头，对着常浩的项目和目标写深度学习报告，10:45 发到常浩私聊。

三步，同一天的产物都在 DigitalTwin/groupdigest/<日期>.*：
  collect  取数，不调模型：群消息（猛鬼兽应用、常浩的用户身份）、源头正文（agent-reach read）、常浩的坐标
           （名册各项目的阶段目标、Vault 个人上下文、全局规则的标题）→ material.json
  build    猛鬼兽写稿（hermes chat 单次）→ 脚本核对（编号、网址、板块、说成已做）→ card-fields.json、report.md、build.json；
           核对不过带着问题重写一次，再不过出事实版（只列原始内容和链接）
  send     建飞书云文档（完整报告）→ 出卡片 → 发常浩私聊 → 回读 → delivery.json；同一天只发一次
定时任务：10:00 groupdigest_build_job.py（collect + build），10:45 groupdigest_send_job.py（send）。

防编造：模型只写材料编号（M3 是第 3 条群消息，S2 是第 2 个源头），链接由脚本按编号填；报告里出现材料以外的网址就不通过。
统计日是前一天 00:00 到 24:00（北京时间）。群内容是外部群里的公开分享，只发常浩私聊。
Vault 个人上下文是受限内容：只交给模型读，报告里只写结论，不贴原文（PRD 8.3）；material.json 权限 600。

环境变量：DT_GD_DIR 产物目录；DT_GD_GROUP 群 chat_id；DT_GD_FOLDER 云文档文件夹；DT_GD_VAULT Vault 个人上下文目录；
DT_GD_RULES 全局规则文件；DT_GD_PROMPT 提示词；DT_GD_REASONING 思考强度；DT_GD_BUDGET 写稿最多等几秒；
DT_GD_WAIT 唤醒后等飞书连通最多几秒；DT_GD_SEND_WAIT 发送时等写稿最多几秒；DT_LARK_CLI、DT_HERMES_CLI、DT_REACH_CLI 换命令；
DT_NOW（ISO 时间）指定现在。
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from feishu_home import home_chat  # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
DIR = Path(os.environ.get("DT_GD_DIR", "~/Library/Application Support/DigitalTwin/groupdigest")).expanduser()
GROUP = os.environ.get("DT_GD_GROUP", "oc_6f7635b44df5806b4de2891e133f993f")   # Codex重置许愿群（外部群）
GROUP_NAME = "Codex重置许愿群"
FOLDER = os.environ.get("DT_GD_FOLDER", "MKmvfAZSvlby0sdDzwIcrXVZn2g")          # 和个人项目日报同一个文件夹
VAULT = Path(os.environ.get("DT_GD_VAULT", "~/Documents/Obsidian Vault/个人上下文")).expanduser()
VAULT_NOTES = ("关于我.md", "个人认知体系长期目标.md", "当前时间预算与资源边界.md")
RULES = Path(os.environ.get("DT_GD_RULES", "~/.claude/CLAUDE.md")).expanduser()
PROMPT = Path(os.environ.get("DT_GD_PROMPT", Path(__file__).resolve().parent.parent / "prompts" / "groupdigest_report.md"))
LARK = os.environ.get("DT_LARK_CLI", "lark-cli")
HERMES = os.environ.get("DT_HERMES_CLI", "hermes")
REACH = os.environ.get("DT_REACH_CLI", "agent-reach")
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
PROFILE = "cli_a96f5f9ac63adcd4"                       # 猛鬼兽应用（PRD D12）
AGENT_ENV = ("HERMES", "OPENCLAW", "LARK_CHANNEL")     # lark-cli 看到这些变量会改读另一套配置，调用前去掉
REASONING = os.environ.get("DT_GD_REASONING", "high")
BUDGET = int(os.environ.get("DT_GD_BUDGET", "1800"))
WAIT = int(os.environ.get("DT_GD_WAIT", "300"))        # 唤醒后代理没通时 lark-cli 输出不是 JSON（#176），最多等这么久
SEND_WAIT = int(os.environ.get("DT_GD_SEND_WAIT", "900"))
MSG_CLIP, SRC_CLIP, NOTE_CLIP = 4000, 6000, 3000       # 每条消息、每个源头、每篇 Vault 笔记最多给模型多少字
MAX_SOURCES = 30                                        # 每天最多抓这么多个源头，其余记「没抓（超过上限）」
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) digital-twin-groupdigest/1.0"
RATE_WAIT = float(os.environ.get("DT_GD_RATE_WAIT", "15"))   # 源头被限流后隔几秒再试
MIN_TEXT = 200                                          # 源头正文少于这么多字算没抓到
SKIP_HOSTS = ("x.com", "twitter.com", "t.co", "feishu.cn", "larksuite.com", "feishucdn.com", "larkoffice.com")
WEEK = "一二三四五六日"
CARD_MARK, REPORT_MARK = "<<<CARD>>>", "<<<REPORT>>>"
SECTIONS = ["一、昨日信息梳理", "二、对你的帮助", "三、行动清单", "四、思考采纳建议（提案）", "五、路径建议"]
COVERAGE = "六、覆盖与可信度"
LIMITS = {"title": 32, "detail": 160, "owner": 10,
          "task": 60, "where": 20, "done": 50, "snippet": 160, "idea": 80, "why": 60, "question": 50}
SLACK = 1.5
# 卡片三个板块（常浩 11-16 定）：信息只讲事，今日行动清单是确定能做的，思考采纳是要他想清楚的提案
# 键 → (必填字段, 选填字段, 最少条数, 最多条数)
LISTS = {"highlights": (("title", "detail"), (), 0, 6),   # 常浩 11-17：只要值得学的，一天全是资讯就可以一条没有
         "todo": (("task", "where", "done", "owner"), ("snippet",), 0, 4),
         "consider": (("idea", "why", "question", "where"), (), 0, 3)}
EMPTY_FIELDS = {"highlights": [], "todo": [], "consider": []}
DONE_CLAIM = re.compile(r"已(?:经)?(?:帮你)?(?:写入|采纳|加进|加入|改好|装好|安装|落地)")
URL = re.compile(r"https?://[^\s<>\"'()\[\]（）【】，。；！？]+")
REF = re.compile(r"\[([MS])(\d+)\](?!\()")
CARD_HEAD = re.compile(r'<card title="([^"]*)"(?: subtitle="([^"]*)")?>')


class Fail(Exception):
    """一句话原因，不含凭据。"""


def now():
    t = dt.datetime.fromisoformat(os.environ["DT_NOW"]) if os.environ.get("DT_NOW") else dt.datetime.now(TZ)
    return t.replace(tzinfo=TZ) if t.tzinfo is None else t.astimezone(TZ)


def default_day():
    return now().date() - dt.timedelta(days=1)


def clip(v, n):
    s = str(v or "")
    return s if len(s) <= n else s[:n] + "…"


def short_day(day):
    d = dt.date.fromisoformat(day)
    return f"{d:%m-%d}（周{WEEK[d.weekday()]}）"


def write_json(path, data, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if private:
        tmp.chmod(0o600)
    tmp.replace(path)


# ---------- 飞书 ----------

def lark(args, what, identity, cwd=None, wait=0):
    """跑一条 lark-cli 命令，返回 data。输出不是 JSON（多半是网络没通）时每 30 秒重试，最多等 wait 秒。"""
    env = {k: v for k, v in os.environ.items() if not k.lstrip("_").startswith(AGENT_ENV)}
    deadline = time.monotonic() + wait
    while True:
        try:
            r = subprocess.run([LARK, "--profile", PROFILE, *args, "--as", identity, "--format", "json"],
                               capture_output=True, text=True, timeout=180, env=env, cwd=cwd)
        except (OSError, subprocess.SubprocessError) as e:
            raise Fail(f"{what}：lark-cli 跑不起来（{type(e).__name__}）")
        d = None
        for raw in (r.stdout, r.stderr):          # lark-cli 的错误 JSON 走 stderr
            try:
                d = json.loads(raw)
                break
            except ValueError:
                continue
        if isinstance(d, dict):
            break
        if time.monotonic() >= deadline:
            raise Fail(f"{what}：lark-cli 输出不是 JSON（退出码 {r.returncode}）")
        time.sleep(min(30, max(1, deadline - time.monotonic())))
    if not d.get("ok", True):
        raise Fail(f"{what}失败：{clip((d.get('error') or {}).get('message', '未知错误'), 200)}")
    return d.get("data") or {}


# ---------- collect ----------

def strip_url(u):
    return u.rstrip(".,;:!?)…'\"*_")


def host_of(u):
    return (urlsplit(u).hostname or "").lower()


def skip_link(u):
    """X、飞书的链接不抓（推文原文已在卡片里）；只有域名的首页（正文里顺手写的 claude.ai 之类）也不抓。"""
    h = host_of(u)
    return any(h == s or h.endswith("." + s) for s in SKIP_HOSTS) or urlsplit(u).path in ("", "/")


def parse_message(m, i):
    """一条群消息 → 材料条目。卡片取标题（博主）、副标题和正文；成员发言照录。"""
    content = str(m.get("content") or "")
    kind = {"interactive": "card", "text": "text", "post": "text"}.get(m.get("msg_type"), "other")
    head = CARD_HEAD.search(content)
    body = html.unescape(re.sub(r"</?card[^>]*>", "", content)).strip()
    links = list(dict.fromkeys(strip_url(u) for u in URL.findall(body)))
    x = next((u for u in links if host_of(u) in ("x.com", "twitter.com") and "/status/" in u), None)
    gist = re.search(r"\*\*What's going on[^*\n]*\*\*\s*\n(.+)", body)   # 多帖卡片写「What's going on · 1/2」
    sender = m.get("sender") or {}
    return {"id": f"M{i}", "time": str(m.get("create_time") or "")[-5:], "sender": sender.get("name") or "群成员",
            "kind": kind, "title": html.unescape(head.group(1)) if head else None,
            "subtitle": html.unescape(head.group(2) or "") if head else None,
            "gist": gist.group(1).strip() if gist else None, "text": clip(body, MSG_CLIP) if body else f"[{m.get('msg_type')}]",
            "links": links, "x_url": x, "app_link": m.get("message_app_link")}


def fetch_messages(day):
    start = dt.datetime.combine(day, dt.time(0), TZ)
    end = start + dt.timedelta(days=1)
    data = lark(["im", "+chat-messages-list", "--chat-id", GROUP, "--start", start.isoformat(), "--end", end.isoformat(),
                 "--order", "asc", "--page-all", "--no-reactions"], "读群消息", "user", wait=WAIT)
    msgs = [m for m in data.get("messages") or [] if not m.get("deleted")]
    return [parse_message(m, i) for i, m in enumerate(msgs, 1)]


class _Text(HTMLParser):
    """网页 → 纯文本：去掉脚本、样式、导航、页眉页脚，留标题和正文。"""
    SKIP = {"script", "style", "noscript", "nav", "header", "footer", "svg", "form", "aside"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth, self.parts, self.title, self._in_title = 0, [], "", False

    def handle_starttag(self, tag, attrs):
        self.depth += tag in self.SKIP
        self._in_title = tag == "title"
        if tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "tr", "div", "section", "article", "pre"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        self.depth -= tag in self.SKIP and self.depth > 0
        self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.depth:
            self.parts.append(data)


def read_direct(url):
    """直接抓网页（大多数博客、更新日志、文档能直接读）→ (标题, 正文) 或 None。"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,text/plain;q=0.9"})
        with urllib.request.urlopen(req, timeout=30) as r:
            if not re.match(r"text/(html|plain)", r.headers.get("Content-Type", "")):
                return None
            raw = r.read(3_000_000).decode(r.headers.get_content_charset() or "utf-8", "replace")
    except (OSError, ValueError):
        return None
    p = _Text()
    try:
        p.feed(raw)
    except Exception:  # noqa: BLE001  坏网页只是读不到
        return None
    text = re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))).strip()
    return (p.title.strip() or None, text) if len(text) >= MIN_TEXT else None


def read_source(url):
    """一个源头 → (标题, 正文, 后端, 错误)。先直接抓；抓不到（JS 页面、拦机器人）再交给 agent-reach，
    它的网页读取走第三方读取服务，有频率限制（11-13 实测连抓 30 个被限流），被限流时隔一会儿再试，最多 3 次。"""
    got = read_direct(url)
    if got:
        return got[0], clip(got[1], SRC_CLIP), "direct", None
    for n in range(3):
        got = read_once(url)
        if not (got[3] and re.search(r"429|rate limit", got[3], re.I)) or n == 2:
            return got
        time.sleep(RATE_WAIT * (n + 1))


def read_once(url):
    try:
        r = subprocess.run([REACH, "read", url], capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return None, None, None, f"读不了（{type(e).__name__}）"
    if not d.get("ok"):
        return None, None, d.get("backend"), clip((d.get("error") or {}).get("message") if isinstance(d.get("error"), dict)
                                                   else d.get("error") or "读不了", 120)
    it = (d.get("items") or [{}])[0]
    text = str(it.get("text") or it.get("content") or "").strip()
    if len(text) < MIN_TEXT:
        return it.get("title"), None, d.get("backend"), "正文太短或取不到"
    return it.get("title"), clip(text, SRC_CLIP), d.get("backend"), None


def fetch_sources(items):
    """群消息里除 X、飞书以外的链接按出现顺序去重，抓前 MAX_SOURCES 个。X 推文原文已在卡片里。"""
    order, seen = [], {}
    for it in items:
        for u in it["links"]:
            if skip_link(u):
                continue
            key = u.split("#")[0].rstrip("/")
            if key in seen:
                if it["id"] not in seen[key]["from"]:
                    seen[key]["from"].append(it["id"])
                continue
            seen[key] = {"url": u, "from": [it["id"]]}
            order.append(seen[key])
    todo, rest = order[:MAX_SOURCES], order[MAX_SOURCES:]
    with ThreadPoolExecutor(3) as pool:          # 6 个并发在 11-13 实测被读取服务限流 7 个
        got = list(pool.map(lambda s: read_source(s["url"]), todo))
    out = []
    for i, (s, (title, text, backend, err)) in enumerate(zip(todo, got), 1):
        out.append({"id": f"S{i}", "url": s["url"], "from": s["from"], "ok": err is None, "title": title, "text": text,
                    "backend": backend, "error": err})
    skipped = [{"url": s["url"], "from": s["from"]} for s in rest]
    return out, skipped


def note_body(path):
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[-1]
    return clip(text.strip(), NOTE_CLIP)


def coordinates():
    """常浩的坐标：名册各项目的阶段目标、Vault 个人上下文、全局规则的标题（判断有没有重复的规则）。取不到的记下原因。"""
    out = {"projects": [], "vault": [], "rules": [], "errors": []}
    try:
        import pdaily_data
        import progress_data
        for p in json.loads(progress_data.ROSTER.read_text(encoding="utf-8"))["projects"]:
            rec = {"id": p["id"], "name": p["name"], "type": p.get("type"), "goal": None}
            try:
                root = Path(os.path.expanduser(p["path"]))
                rec["goal"] = pdaily_data.goal(root, pdaily_data.main_ref(root, p.get("main") or "main")).get("text")
            except Exception as e:  # noqa: BLE001  一个项目读不到只记下，不拖垮整份
                out["errors"].append(f"{p['name']} 的阶段目标读不到（{type(e).__name__}）")
            out["projects"].append(rec)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"项目名册读不到（{type(e).__name__}）")
    for name in VAULT_NOTES:
        try:
            out["vault"].append({"note": f"个人上下文/{name}", "text": note_body(VAULT / name)})
        except OSError as e:
            out["errors"].append(f"Vault 个人上下文/{name} 读不到（{type(e).__name__}）")
    try:
        out["rules"] = [l[3:].strip() for l in RULES.read_text(encoding="utf-8").splitlines() if l.startswith("## ")]
    except OSError:
        out["errors"].append("全局规则读不到")
    return out


def collect(day):
    items = fetch_messages(day)
    sources, skipped = fetch_sources(items)
    mat = {"day": day.isoformat(), "group": {"id": GROUP, "name": GROUP_NAME},
           "window": f"{day} 00:00 到 {day + dt.timedelta(days=1)} 00:00（北京时间）",
           "fetched_at": now().isoformat(timespec="seconds"), "items": items, "sources": sources,
           "skipped_sources": skipped, "context": coordinates()}
    mat["coverage"] = {"messages": len(items), "cards": sum(i["kind"] == "card" for i in items),
                       "member_posts": sum(i["kind"] == "text" for i in items),
                       "sources": len(sources), "sources_ok": sum(s["ok"] for s in sources), "sources_skipped": len(skipped),
                       "not_covered": "X 推文的回复和引用推文没抓（agent-reach 的 X 渠道没装），只用卡片里的推文原文"}
    write_json(DIR / f"{day}.material.json", mat, private=True)
    return mat


# ---------- build ----------

def model_view(mat):
    """交给模型的材料：去掉网址（模型只认编号），留下判断要用的东西。"""
    items = [{k: v for k, v in i.items() if k not in ("links", "x_url", "app_link")} for i in mat["items"]]
    srcs = [{"id": s["id"], "from": s["from"], "title": s["title"], "host": host_of(s["url"]), "text": s["text"]}
            for s in mat["sources"] if s["ok"]]
    return {"day": mat["day"], "group": mat["group"]["name"], "items": items, "sources": srcs,
            "sources_failed": [{"id": s["id"], "from": s["from"], "host": host_of(s["url"]), "error": s["error"]}
                               for s in mat["sources"] if not s["ok"]],
            "context": mat["context"], "coverage": mat["coverage"]}


def query(mat, feedback=None):
    q = PROMPT.read_text(encoding="utf-8")
    if feedback:
        q += "\n\n## 上一稿没通过核对，按下面的问题改正后整份重写\n\n" + "\n".join(f"- {f}" for f in feedback)
    return q + "\n\n## 材料\n\n```json\n" + json.dumps(model_view(mat), ensure_ascii=False, separators=(",", ":")) + "\n```\n"


def session_model(sid):
    """这次写稿会话实际用的主模型（含回退），查不到返回 None。只读打开。"""
    try:
        con = sqlite3.connect(f"file:{HERMES_HOME / 'state.db'}?mode=ro", uri=True, timeout=10)
        rows = con.execute("select model from session_model_usage where session_id=? and task='' order by first_seen",
                           (sid,)).fetchall()
        con.close()
    except sqlite3.Error:
        return None
    models = list(dict.fromkeys(r[0] for r in rows if r[0]))
    return "，回退 ".join(models) if models else None


def generate(mat, feedback=None):
    """调 Hermes（猛鬼兽）单次写稿，返回 (回复全文, 模型)。失败抛 Fail。"""
    qf = DIR / f"{mat['day']}.query.md"
    qf.write_text(query(mat, feedback), encoding="utf-8")
    qf.chmod(0o600)
    cmd = [HERMES, "chat", "-Q", "--query-file", str(qf), "-t", "todo", "--source", "tool",
           "--max-turns", "3", "--run-budget", str(BUDGET), "--reasoning", REASONING]
    # 同个人项目日报：给足写稿时间，去掉 Claude Code 会话里继承的变量（CURRENT 11-11）
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env.setdefault("HERMES_API_CALL_STALE_TIMEOUT", str(BUDGET))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=BUDGET + 120, cwd=str(DIR), env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise Fail(f"Hermes 跑不起来（{type(e).__name__}）")
    sid = (re.findall(r"session_id:\s*(\S+)", r.stderr) or [None])[-1]
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()
        raise Fail(f"Hermes 退出码 {r.returncode}：{clip(tail[-1] if tail else '', 120)}")
    return r.stdout.strip(), session_model(sid) if sid else None


def parse_reply(text):
    if CARD_MARK not in text or REPORT_MARK not in text:
        raise Fail("回复里没有 <<<CARD>>> / <<<REPORT>>> 分隔")
    card_part, report = text.split(CARD_MARK, 1)[1].split(REPORT_MARK, 1)
    card_part = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", card_part.strip())
    try:
        fields = json.loads(card_part)
    except ValueError as e:
        raise Fail(f"卡片要素不是合法 JSON（{getattr(e, 'msg', e)}）")
    if not isinstance(fields, dict):
        raise Fail("卡片要素不是 JSON 对象")
    return fields, report.strip()


def link_of(mat):
    """编号 → 链接：群消息优先 X 原帖，其次消息里第一个链接，再次飞书消息链接；源头用原链接。"""
    out = {}
    for i in mat["items"]:
        out[i["id"]] = i["x_url"] or (i["links"][0] if i["links"] else None) or i["app_link"]
    for s in mat["sources"]:
        out[s["id"]] = s["url"]
    return out


def allowed_urls(mat):
    urls = {s["url"] for s in mat["sources"]} | {s["url"] for s in mat.get("skipped_sources") or []}
    for i in mat["items"]:
        urls |= set(i["links"]) | {u for u in (i["x_url"], i["app_link"]) if u}
    return {u.split("#")[0].rstrip("/") for u in urls}


def roster_ids():
    """名册里能派单的项目 id（只认 projects，inventory 不派单）。读不到返回空集，标了 Claude 的条目就都改成你自己做。"""
    try:
        import progress_data
        return {p["id"] for p in json.loads(progress_data.ROSTER.read_text(encoding="utf-8"))["projects"]}
    except Exception:  # noqa: BLE001
        return set()


def check(mat, fields, report):
    """核对模型稿。返回 (问题清单, 提醒清单, 规整后的卡片要素)。问题清单不空就不能发。"""
    errs, warns = [], []
    ids = set(link_of(mat))
    out = {}

    def too_long(v, limit_key, name):
        # 字数是给模型的上限；略超照用（截在半句话上更难读，11-13 实测），超过 1.5 倍才打回重写
        if len(v) > LIMITS[limit_key] * SLACK:
            errs.append(f"{name} 有 {len(v)} 字，超过上限 {LIMITS[limit_key]} 字太多，压短重写")
        elif len(v) > LIMITS[limit_key]:
            warns.append(f"{name} 略超 {LIMITS[limit_key]} 字")
    for key, (need, opt, lo, hi) in LISTS.items():
        rows = fields.get(key) if isinstance(fields.get(key), list) else []
        if len(rows) < min(lo, len(mat["items"])):
            errs.append(f"{key} 至少要 {lo} 条")
        if len(rows) > hi:
            warns.append(f"{key} 超过 {hi} 条，只留前 {hi} 条")
            rows = rows[:hi]
        clean = []
        for n, r in enumerate(rows, 1):
            if not isinstance(r, dict):
                errs.append(f"{key} 第 {n} 条不是对象")
                continue
            row = {}
            for t in need + opt:
                v = str(r.get(t) or "").strip()
                if t == "snippet":
                    v = v.replace("`", "").strip()
                if not v and t in need:
                    errs.append(f"{key} 第 {n} 条缺 {t}")
                if v:
                    too_long(v, t, f"{key} 第 {n} 条的 {t}")
                    row[t] = v
            refs = list(dict.fromkeys(str(x).strip() for x in r.get("refs") or [] if str(x).strip()))
            bad = [x for x in refs if x not in ids]
            if not refs:
                errs.append(f"{key} 第 {n} 条没有出处编号")
            if bad:
                errs.append(f"{key} 第 {n} 条引用了材料里没有的编号 {'、'.join(bad)}")
            row["refs"] = refs
            if key == "highlights":
                row["read"] = r.get("read") is True
            if key == "todo":
                # 谁做（常浩 11-16）：能派给 Claude 的写 claude 并给名册项目 id，他回「派 1 3」时猛鬼兽照这个开单
                row["owner"] = "claude" if row.get("owner", "").strip().lower() == "claude" else "you"
                projects = list(dict.fromkeys(str(x).strip() for x in r.get("projects") or [] if str(x).strip()))
                if row["owner"] == "claude":
                    roster = roster_ids()
                    bad = [x for x in projects if x not in roster]
                    projects = [x for x in projects if x in roster]
                    if bad:
                        warns.append(f"todo 第 {n} 条的项目 {'、'.join(bad)} 不在名册里，派单时不算")
                    if not projects:
                        warns.append(f"todo 第 {n} 条标了 Claude 但没有名册里的项目，改成你自己做")
                        row["owner"] = "you"
                row["projects"] = projects if row["owner"] == "claude" else []
                try:
                    row["minutes"] = max(5, min(240, int(r.get("minutes"))))
                except (TypeError, ValueError):
                    errs.append(f"todo 第 {n} 条的 minutes 不是数字")
            clean.append(row)
        out[key] = clean
    pos = [report.find("## " + s) for s in SECTIONS]
    missing = [s for s, p in zip(SECTIONS, pos) if p < 0]
    if missing:
        errs.append(f"完整报告缺板块：{'、'.join(missing)}")
    elif pos != sorted(pos):
        errs.append("完整报告的板块顺序不对")
    bad_refs = sorted({f"{k}{n}" for k, n in REF.findall(report)} - ids)
    if bad_refs:
        errs.append(f"完整报告引用了材料里没有的编号 {'、'.join(bad_refs)}")
    ok_urls = allowed_urls(mat)
    card_text = json.dumps(out, ensure_ascii=False)
    foreign = sorted({u for u in (strip_url(x) for x in URL.findall(report + "\n" + card_text))
                      if u.split("#")[0].rstrip("/") not in ok_urls})
    if foreign:
        errs.append(f"稿子里有材料以外的网址（{clip('、'.join(foreign), 200)}）：只写编号，链接由脚本填")
    text = report + card_text
    # 「没有任何一条已经采纳」这类否定句不算（11-13 实测误报）：前面 8 个字里有否定词就跳过
    claims = sorted({m.group(0) for m in DONE_CLAIM.finditer(text)
                     if not re.search(r"[没未不无非]", text[max(0, m.start() - 8):m.start()])})
    if claims:
        errs.append(f"把提案说成已经做了（{'、'.join(claims)}）：猛鬼兽只出提案，常浩点头才落地")
    return errs, warns, out


def render_refs(text, links):
    return REF.sub(lambda m: f"[{m.group(1)}{m.group(2)}]({links[m.group(1) + m.group(2)]})"
                   if links.get(m.group(1) + m.group(2)) else m.group(0), text)


def coverage_md(mat):
    c = mat["coverage"]
    lines = [f"## {COVERAGE}", "",
             f"- 群消息：{c['messages']} 条（搬运卡片 {c['cards']}，成员发言 {c['member_posts']}），统计 {mat['window']}",
             f"- 源头：抓了 {c['sources']} 个，读到正文 {c['sources_ok']} 个"
             + (f"，超过每天上限没抓 {c['sources_skipped']} 个" if c["sources_skipped"] else ""),
             f"- 没覆盖：{c['not_covered']}"]
    failed = [s for s in mat["sources"] if not s["ok"]]
    if failed:
        lines.append("- 没读到的源头：" + "；".join(f"[{s['id']}]({s['url']})（{s['error']}）" for s in failed))
    if mat["context"]["errors"]:
        lines.append("- 你的坐标没读全：" + "；".join(mat["context"]["errors"]))
    lines.append("- 链接都由脚本按材料编号填；M 是群消息（优先指向 X 原帖），S 是源头原文。")
    return "\n".join(lines)


def material_index(mat):
    """完整报告末尾的材料目录：每个编号是什么、在哪。"""
    links = link_of(mat)
    rows = ["## 附：材料目录", ""]
    for i in mat["items"]:
        name = i["title"] or i["sender"]
        rows.append(f"- [{i['id']}]({links[i['id']]}) {i['time']} {name}：{clip(i['gist'] or i['text'].splitlines()[0], 80)}"
                    if links[i["id"]] else f"- {i['id']} {i['time']} {name}：{clip(i['gist'] or i['text'], 80)}")
    for s in mat["sources"]:
        rows.append(f"- [{s['id']}]({s['url']}) {clip(s['title'] or host_of(s['url']), 60)}" + ("" if s["ok"] else f"（没读到：{s['error']}）"))
    return "\n".join(rows)


def fallback(mat, reason):
    """事实版：模型没写成或核对不过时，只列原始内容和链接，不做分析。"""
    cards = [i for i in mat["items"] if i["kind"] == "card"] or mat["items"]
    fields = dict(EMPTY_FIELDS, highlights=[{"title": clip(i["title"] or i["sender"], LIMITS["title"]),
                               "detail": clip(i["gist"] or i["text"].splitlines()[0], LIMITS["detail"]),
                               "read": False, "refs": [i["id"]]} for i in cards[:8]])
    report = "\n\n".join([f"> 这是事实版：{reason}。下面只列原始内容和链接，没有分析。",
                          "## 昨天群里的全部内容", material_index(mat).split("\n", 2)[2]])
    return fields, report


def trace(model, degraded):
    who = f"脚本事实版（{degraded}）｜接入：本机脚本" if degraded else f"Hermes（{model or '模型未知'}）｜接入：Hermes 派单"
    return f"生成：{who}｜{now():%Y-%m-%d %H:%M}"


def build(day, reply=None):
    """写稿 + 核对，产物落盘，返回 build 元数据。reply 给现成回复（回放、测试用），不调模型。"""
    d = day.isoformat()
    mat = json.loads((DIR / f"{d}.material.json").read_text(encoding="utf-8"))
    meta = {"day": d, "model": None, "degraded": None, "attempts": [], "warnings": []}
    fields = report = None
    if not mat["items"]:
        fields = dict(EMPTY_FIELDS)
        report, meta["empty"] = "昨天群里没有新消息。", True
    feedback, checked = None, False
    for n in range(0 if fields else 2):
        checked = False
        try:
            text = reply
            if text is None:
                text, meta["model"] = generate(mat, feedback)
            (DIR / f"{d}.reply{n + 1}.md").write_text(text, encoding="utf-8")
            f, rep = parse_reply(text)
            checked = True
            errs, warns, f = check(mat, f, rep)
        except Fail as e:
            errs, warns = [str(e)], []
        meta["attempts"].append({"errors": errs, "warnings": warns})
        if not errs:
            fields, report, meta["warnings"] = f, rep, warns
            break
        feedback = errs
        if reply is not None:
            break
    if fields is None:
        meta["degraded"] = "核对没通过" if checked else "模型没写成"
        fields, report = fallback(mat, meta["degraded"])
    links = link_of(mat)
    meta["trace"] = trace(meta["model"], meta["degraded"])
    doc = "\n\n".join([meta["trace"], render_refs(report, links), coverage_md(mat), material_index(mat)])
    write_json(DIR / f"{d}.card-fields.json", fields)
    (DIR / f"{d}.report.md").write_text(doc + "\n", encoding="utf-8")
    meta["coverage"] = mat["coverage"]
    write_json(DIR / f"{d}.build.json", meta)
    return meta


# ---------- 卡片 ----------

def esc(s):
    """模型写的文字放进卡片前转义 lark_md 语法字符，免得变成链接、@ 或标签。"""
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(s or ""))
    return s.translate(str.maketrans({"[": "［", "]": "］", "<": "＜", ">": "＞", "`": "'", "\\": "＼", "*": "&#42;"}))


def refs_md(refs, links):
    return " ".join(f"[{r}]({links[r]})" if links.get(r) else r for r in refs)


def md(content, **kw):
    return {"tag": "markdown", "content": content, **kw}


def title_of(day):
    return f"🧭 AI 前沿群学习报告 · {short_day(day)}"


# 二 · 表格对照（常浩 11-16 第二轮选定并改：去掉原话和「这些对你的帮助」，「行动清单」改叫「今日行动清单」；11-17 思考采纳建议挪到今日行动清单前，派单提问放最后、浅红底加红色问号）。
# 三个板块各用一种颜色，数字块、标题都跟着它：信息蓝、行动绿、采纳橙
COLOR = {"info": "blue", "todo": "green", "consider": "orange"}
ADOPT_NOTE = "<font color='grey'>提案，你想清楚、点头才落地</font>"


def stats(f, cov):
    """顶上一排数字：读了多少消息，梳理出多少要点，多少件可做，多少条要你想。"""
    cell = lambda n, label, color: {"tag": "column", "width": "weighted", "weight": 1, "padding": "8px",
                                    "background_style": f"{color}-50" if color != "grey" else "grey", "vertical_spacing": "0px",
                                    "elements": [md(f"<font color='{color}'>**{n}**</font>", text_align="center", text_size="heading-3"),
                                                 md(f"<font color='grey'>{label}</font>", text_align="center", text_size="notation")]}
    return {"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px", "columns": [
        cell(cov["messages"], "条消息", "grey"), cell(len(f["highlights"]), "条要点", COLOR["info"]),
        cell(len(f["todo"]), "件可做", COLOR["todo"]), cell(len(f["consider"]), "条待你想", COLOR["consider"])]}


def head(icon, name, note, color):
    return md(f"{icon} <font color='{color}'>**{name}**</font>　<font color='grey'>{note}</font>")


def inline_code(snippet):
    """命令放进它那一条的格子里（常浩 11-16：单独一块「第 1 条要用到」很突兀）。"""
    clean = re.sub(r"[\x00-\x1f\x7f`]", " ", snippet).strip()   # Python 3.9（CI）的 f-string 花括号里不能有反斜杠
    return f"`{clean}`"


def owner_tag(r):
    return ("<text_tag color='indigo'>Claude</text_tag>" if r.get("owner") == "claude"
            else "<text_tag color='neutral'>你</text_tag>")


def table(cols, rows):
    """飞书卡片表格（同个人项目日报速览表的写法）。cols = [(键, 列名, 宽度)]。"""
    return {"tag": "table", "page_size": 10, "row_height": "auto", "row_max_height": "999px",
            "header_style": {"text_align": "left", "text_size": "normal", "background_style": "grey", "bold": True, "lines": 1},
            "columns": [{"name": k, "display_name": n, "data_type": "lark_md", "width": w, "vertical_align": "top"}
                        for k, n, w in cols], "rows": rows}


ORIG_REFS = 3       # 每条信息的「原文与译文」面板最多放几条出处
ORIG_CLIP = 700     # 每条出处原文最多多少字；卡片超大时逐步收紧
CARD_MAX = 27000    # 卡片 JSON 字节上限（飞书卡片约 30KB，留余量）


def original_md(ref, mat, n):
    """一条出处的原文（常浩 11-17：要群里 X 卡片那种「原文与译文 · 展开」）。
    X 卡片取「原文与译文」那段（中文 + Original），没有就取摘要；群成员发言取原话；源头取标题加开头一段。"""
    item = next((i for i in mat["items"] if i["id"] == ref), None)
    if item:
        who = item.get("title") or item.get("sender") or ""
        lines = (item.get("text") or "").splitlines()
        start = next((k for k, l in enumerate(lines) if "原文与译文" in l), None)
        if start is not None:
            end = next((k for k in range(start + 1, len(lines)) if lines[k].strip() == "▲"), len(lines))
            body = [l.strip() for l in lines[start + 1:end] if l.strip()]
        else:
            body = [item.get("gist") or item.get("text") or ""]
        out, used = [], 0
        for l in body:
            if l in ("**中文**", "**Original**"):
                out.append(f"**{l.strip('*')}**")
                continue
            if used >= n:
                break
            out.append(esc(clip(l, n - used)))
            used += len(l)
        return f"<font color='grey'>{ref} · {esc(who)}</font>\n" + "\n".join(out)
    src = next((x for x in mat["sources"] if x["id"] == ref), None)
    if src and src.get("ok"):
        text = re.sub(r"\s+", " ", src.get("text") or "").strip()
        return f"<font color='grey'>{ref} · {esc(src.get('title') or '')}</font>\n{esc(clip(text, n))}（节选，全文点原地址）"
    return None


def orig_panel(refs, mat, n):
    """折叠的原文面板，默认收起；群消息在前、源头在后，最多 ORIG_REFS 条。"""
    picked = [r for r in refs if r.startswith("M")] + [r for r in refs if r.startswith("S")]
    blocks = [b for b in (original_md(r, mat, n) for r in picked[:ORIG_REFS]) if b]
    if not blocks:
        return None
    return {"tag": "collapsible_panel", "expanded": False, "background_color": "grey-50",
            "border": {"color": "grey", "corner_radius": "6px"}, "padding": "4px 10px 8px 10px", "vertical_spacing": "8px",
            "header": {"title": {"tag": "plain_text", "content": "原文与译文 · 展开"}, "width": "fill", "vertical_align": "center",
                       "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "color": "grey", "size": "16px 16px"},
                       "icon_position": "right", "icon_expanded_angle": -180},
            "elements": [md(b) for b in blocks]}


def dispatch_ask(mine):
    """结尾的派单提问（常浩 11-16 定要问；11-17 要显眼、用红色问号）：浅红底色块，问句加大加粗，下一行写怎么回。
    他回复后猛鬼兽按 groupdigest-act 技能开单。没有标 Claude 的条目就不问。"""
    if not mine:
        return None
    if len(mine) == 1:
        q, how = f"要我把第 {mine[0]} 条写成任务派给 Claude 吗？", "回「**派**」就开单；不派就不用回。"
    else:
        example = f"派 {' '.join(str(n) for n in mine[:2])}" + (f"，{mine[-1]} 不做" if len(mine) > 2 else "")
        q = f"要我把第 {'、'.join(str(n) for n in mine)} 条写成任务派给 Claude 吗？"
        how = f"回「**都派**」，或「**{example}**」；不派就不用回。"
    return {"tag": "column_set", "flex_mode": "none", "columns": [
        {"tag": "column", "width": "weighted", "weight": 1, "background_style": "red-50",
         "padding": "12px 14px 12px 14px", "vertical_spacing": "4px", "elements": [
             md(f"❓ **{q}**", text_size="heading-4"), md(how)]}]}


def body(f, links, degraded=None, mat=None, orig=ORIG_CLIP):
    """信息像一篇阅读稿（编号标题 → 总结和出处），思考采纳建议、今日行动清单各一张表横着看，最后是派单提问。"""
    els = []
    if degraded:
        els.append(md(f"<font color='red'>猛鬼兽今天没写成分析（{esc(degraded)}），下面先给你昨天群里的原始内容。</font>"))
    if not f["highlights"]:
        els.append(md("昨天群里的消息都是资讯，没有值得学的内容。" if mat and mat["items"] else "昨天群里没有新消息。"))
    else:
        els.append(head("📰", "昨日信息梳理", "只挑值得学的方法、技巧和工具，按价值排", COLOR["info"]))
        for n, r in enumerate(f["highlights"], 1):
            tag = " <text_tag color='green'>值得精读</text_tag>" if r.get("read") else ""
            # 常浩 11-16：去掉原话后要紧凑些。标题和正文放进同一列、行距 4px，出处接在正文末尾，条与条之间只留卡片默认间距
            # 常浩 11-17：每条下面放「原文与译文 · 展开」折叠面板，后面再跟原地址跳转
            panel = orig_panel(r["refs"], mat, orig) if mat else None
            parts = [md(f"<number_tag background_color='{COLOR['info']}' font_color='white'>{n}</number_tag> **{esc(r['title'])}**{tag}",
                        text_size="heading-4"), md(esc(r["detail"]))]
            if panel:
                parts.append(panel)
            parts.append(md(f"<font color='grey'>原地址</font> {refs_md(r['refs'], links)}", text_size="notation"))
            els.append({"tag": "column_set", "flex_mode": "none", "columns": [
                {"tag": "column", "width": "weighted", "weight": 1, "vertical_spacing": "4px", "elements": parts}]})
    # 常浩 11-17：思考采纳建议放在今日行动清单前面，派单提问放到卡片最后、要一眼看出是在问他
    if f["consider"] or f["todo"]:
        els.append({"tag": "hr"})
    if f["consider"]:
        els += [head("🤔", "思考采纳建议", ADOPT_NOTE, COLOR["consider"]),
                table([("i", "建议采纳", "36%"), ("y", "为什么", "24%"), ("q", "先想清楚", "24%"), ("w", "放哪", "16%")],
                      [{"i": f"**{esc(r['idea'])}** {refs_md(r['refs'], links)}", "y": esc(r["why"]),
                        "q": f"*{esc(r['question'])}*", "w": esc(r["where"])} for r in f["consider"]])]
    if f["todo"]:
        if f["consider"]:
            els.append({"tag": "hr"})   # 常浩 11-17：思考和行动之间也要分割线
        els += [head("✅", "今日行动清单", "学完就能动手", COLOR["todo"]),
                table([("t", "做什么", "42%"), ("w", "谁做 · 在哪", "18%"), ("m", "时长", "12%"), ("d", "做到什么算完", "28%")],
                      [{"t": f"**{n}. {esc(r['task'])}** {refs_md(r['refs'], links)}" + (f"\n{inline_code(r['snippet'])}" if r.get("snippet") else ""),
                        "w": f"{owner_tag(r)}\n{esc(r['where'])}", "m": f"{r['minutes']} 分钟", "d": esc(r["done"])}
                       for n, r in enumerate(f["todo"], 1)])]
        ask = dispatch_ask([n for n, r in enumerate(f["todo"], 1) if r.get("owner") == "claude"])
        if ask:
            els.append(ask)
    return els


def build_card(day, fields, meta, mat, doc_url=None, doc_error=None, title=None):
    links = link_of(mat)
    c = mat["coverage"]
    els = [stats(fields, c), *body(fields, links, meta.get("degraded"), mat)]
    n = ORIG_CLIP
    while n > 100 and len(json.dumps(els, ensure_ascii=False).encode()) > CARD_MAX:
        n -= 150   # 原文太长、卡片发不出去时逐步收紧原文
        els = [stats(fields, c), *body(fields, links, meta.get("degraded"), mat, n)]
    if doc_url:
        els.append({"tag": "button", "text": {"tag": "plain_text", "content": "查看完整报告（含路径建议、材料目录）"},
                    "type": "primary_filled", "width": "fill", "behaviors": [{"type": "open_url", "default_url": doc_url}]})
    elif doc_error:
        els.append(md(f"<font color='red'>完整报告没建成：{esc(clip(doc_error, 80))}，本地在 DigitalTwin/groupdigest/{day}.report.md</font>"))
    foot = f"读了 {c['messages']} 条消息、{c['sources_ok']}/{c['sources']} 个源头｜{meta['trace']}"
    els += [{"tag": "hr"}, md(f"<font color='grey'>{esc(foot)}</font>", text_size="notation")]
    title = title or title_of(day)
    return {"schema": "2.0", "config": {"update_multi": True, "summary": {"content": title}},
            "header": {"title": {"tag": "plain_text", "content": title},
                       "subtitle": {"tag": "plain_text", "content": f"{GROUP_NAME}｜猛鬼兽深度梳理"}, "template": "indigo"},
            "body": {"elements": els, "vertical_spacing": "12px"}}


def preview(day):
    """把卡片发到常浩私聊看实际样子（常浩 11-16：要看飞书里的实际版本）。标题加【预览】，不记 delivery，不算当天已发。
    返回消息号。"""
    meta = json.loads((DIR / f"{day}.build.json").read_text(encoding="utf-8"))
    fields = json.loads((DIR / f"{day}.card-fields.json").read_text(encoding="utf-8"))
    mat = json.loads((DIR / f"{day}.material.json").read_text(encoding="utf-8"))
    chat = home_chat()
    if not chat.startswith("oc_"):
        raise Fail("没有私聊 chat_id")
    try:
        doc_url, doc_error = create_doc(day, DIR), None
    except Fail as e:
        doc_url, doc_error = None, str(e)
    title = f"【预览】{title_of(day)}"
    card = build_card(day, fields, meta, mat, doc_url, doc_error, title=title)
    mid = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "interactive",
                "--content", json.dumps(card, ensure_ascii=False),
                "--idempotency-key", f"groupdigest-preview-{day}-{int(time.time())}"], "发预览", "bot").get("message_id")
    got = lark(["im", "+messages-mget", "--message-ids", mid], "回读", "bot")
    m = (got.get("messages") or [{}])[0]
    if not (m.get("msg_type") == "interactive" and title_of(day) in str(m.get("content"))):
        raise Fail(f"预览已发（{mid}），但回读对不上标题")
    return mid


def failure_card(day, reason):
    return {"schema": "2.0", "config": {"summary": {"content": title_of(day)}},
            "header": {"title": {"tag": "plain_text", "content": title_of(day)}, "template": "red"},
            "body": {"elements": [md(f"⚠️ 今天的群学习报告没做出来：{esc(reason)}。")]}}


# ---------- send ----------

def create_doc(day, out):
    d = dt.date.fromisoformat(day)
    data = lark(["docs", "+create", "--parent-token", FOLDER, "--doc-format", "markdown",
                 "--title", f"猛鬼兽 · AI 前沿群学习报告 · {day}（周{WEEK[d.weekday()]}）",
                 "--content", f"@{day}.report.md"], "建完整报告文档", "user", cwd=str(out))
    url = (data.get("document") or {}).get("url")
    if not url:
        raise Fail("建完整报告文档：返回里没有文档地址")
    return url


def wait_build(day):
    """10:45 发送时写稿可能还没完：building 标记还在就每 30 秒看一次，最多等 SEND_WAIT 秒。"""
    deadline = time.monotonic() + SEND_WAIT
    while not (DIR / f"{day}.build.json").exists() and (DIR / f"{day}.building").exists() and time.monotonic() < deadline:
        time.sleep(30)


def send(day, force=False):
    """发到常浩私聊。成功返回 None，失败返回一句原因。"""
    deliv = DIR / f"{day}.delivery.json"
    if deliv.exists() and not force:
        try:
            if json.loads(deliv.read_text(encoding="utf-8")).get("sent"):
                return None
        except ValueError:
            pass
    wait_build(day)
    rec = {"day": day, "sent": False, "at": None, "doc_url": None, "doc_error": None, "message_id": None,
           "read_back": False, "error": None}
    try:
        meta = json.loads((DIR / f"{day}.build.json").read_text(encoding="utf-8"))
        fields = json.loads((DIR / f"{day}.card-fields.json").read_text(encoding="utf-8"))
        mat = json.loads((DIR / f"{day}.material.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = None
    try:
        chat = home_chat()
        if not chat.startswith("oc_"):
            raise Fail("没有私聊 chat_id（FEISHU_HOME_CHANNEL，环境变量和 ~/.hermes/.env 里都没有）")
        if meta is None:
            err = "10:00 的取数写稿没跑成"
            try:
                err = json.loads((DIR / f"{day}.collect-error.json").read_text(encoding="utf-8"))["error"]
            except (OSError, ValueError, KeyError):
                pass
            card = failure_card(day, err)
            rec["error"] = err
        else:
            try:
                rec["doc_url"] = create_doc(day, DIR)
            except Fail as e:
                rec["doc_error"] = str(e)
            card = build_card(day, fields, meta, mat, rec["doc_url"], rec["doc_error"])
        text = json.dumps(card, ensure_ascii=False)
        (DIR / f"{day}.card.sent.json").write_text(text, encoding="utf-8")
        data = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "interactive", "--content", text,
                     "--idempotency-key", f"groupdigest-{day}" + (f"-{int(time.time())}" if force else "")], "发卡片", "bot")
        rec["message_id"], rec["sent"] = data.get("message_id"), bool(data.get("message_id"))
        rec["at"] = now().isoformat(timespec="seconds")
        if rec["sent"]:
            got = lark(["im", "+messages-mget", "--message-ids", rec["message_id"]], "回读", "bot")
            m = (got.get("messages") or [{}])[0]
            rec["read_back"] = m.get("msg_type") == "interactive" and title_of(day) in str(m.get("content"))
    except Fail as e:
        rec["error"] = str(e)
    write_json(deliv, rec)
    if not rec["sent"]:
        return rec["error"] or "发卡片没拿到消息号"
    if not rec["read_back"]:
        return f"卡片已发（{rec['message_id']}），但回读对不上标题"
    if meta is None:
        return f"只发了失败说明：{rec['error']}"
    return None


# ---------- 回复即派单（常浩 11-16） ----------
# 卡片结尾问要不要把标了 Claude 的行动派出去；常浩回「都派」「派 1 3，2 不做」时，groupdigest-act 技能先跑 todos 看清单，
# 把选中的写成任务说明经车间派单（dispatch.py，点名 Claude），每开一张单跑一次 mark，同一条同一项目不会派两次。

def latest_sent_day():
    for f in sorted(DIR.glob("*.delivery.json"), reverse=True):
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("sent"):
                return f.name.split(".")[0]
        except ValueError:
            continue
    return None


def dispatch_state(day):
    try:
        return json.loads((DIR / f"{day}.dispatch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"day": day, "items": {}}


def todos(day=None):
    """某天（默认最近一张发出去的卡片）的今日行动清单和每条的派单情况。"""
    day = day or latest_sent_day()
    if not day:
        raise Fail("还没有发出去的群学习报告")
    try:
        fields = json.loads((DIR / f"{day}.card-fields.json").read_text(encoding="utf-8"))
        mat = json.loads((DIR / f"{day}.material.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Fail(f"{day} 的报告文件读不到")
    try:
        deliv = json.loads((DIR / f"{day}.delivery.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        deliv = {}
    links, state = link_of(mat), dispatch_state(day)
    items = []
    for n, r in enumerate(fields.get("todo") or [], 1):
        items.append({"n": n, **{k: r.get(k) for k in ("task", "where", "minutes", "done", "snippet", "owner", "projects")},
                      "refs": [{"id": x, "url": links.get(x)} for x in r.get("refs") or []],
                      "decided": state["items"].get(str(n))})
    return {"day": day, "card_message_id": deliv.get("message_id"), "doc_url": deliv.get("doc_url"),
            "report": str(DIR / f"{day}.report.md"), "items": items,
            "pending": [i["n"] for i in items if i["owner"] == "claude" and not i["decided"]]}


def mark(day, item, project=None, order=None, skip=False):
    """记下一条行动的去向：派出去的单号（按项目），或常浩说不做。返回一句话。"""
    info = todos(day)
    row = next((i for i in info["items"] if i["n"] == item), None)
    if not row:
        raise Fail(f"{info['day']} 没有第 {item} 条行动")
    state = dispatch_state(info["day"])
    rec = state["items"].setdefault(str(item), {"decision": None, "orders": []})
    at = now().isoformat(timespec="seconds")
    if skip:
        if rec["orders"]:
            raise Fail(f"第 {item} 条已经派过单（{'、'.join(o['order'] for o in rec['orders'])}），不能再记成不做")
        rec.update(decision="skipped", at=at)
        msg = f"已记下：第 {item} 条不做"
    else:
        if row["owner"] != "claude":
            raise Fail(f"第 {item} 条是你自己做的，不派单")
        if project not in row["projects"] or not order:
            raise Fail(f"第 {item} 条只能派到 {'、'.join(row['projects'])}，且要带单号")
        if any(o["project"] == project for o in rec["orders"]):
            raise Fail(f"第 {item} 条在 {project} 已经派过单，不重复派")
        rec["orders"].append({"project": project, "order": order, "at": at})
        rec.update(decision="dispatched", at=at)
        msg = f"已记下：第 {item} 条在 {project} 派了 {order}"
    write_json(DIR / f"{info['day']}.dispatch.json", state)
    return msg


# ---------- 入口 ----------

def run_build(day, reply=None):
    """collect + build。失败原因写进 collect-error.json，由 10:45 的发送报出来。"""
    d = day.isoformat()
    DIR.mkdir(parents=True, exist_ok=True)
    marker = DIR / f"{d}.building"
    marker.write_text(now().isoformat(timespec="seconds"), encoding="utf-8")
    try:
        collect(day)
        return build(day, reply)
    except Exception as e:  # noqa: BLE001  任何失败都要留一句原因
        write_json(DIR / f"{d}.collect-error.json", {"error": f"取数失败（{clip(e, 160)}）"})
        raise
    finally:
        marker.unlink(missing_ok=True)


def main(argv=None):
    p = argparse.ArgumentParser(description="AI 前沿群每日学习报告：取数、写稿、发送")
    p.add_argument("cmd", choices=("collect", "build", "run", "send", "card", "preview", "todos", "mark"))
    p.add_argument("--date", help="统计日 YYYY-MM-DD，默认昨天")
    p.add_argument("--reply", help="build / run：用现成的模型回复文件（回放、测试用）")
    p.add_argument("--force", action="store_true", help="send：这一天已经发过也再发一次")
    p.add_argument("--quiet", action="store_true", help="成功不输出（定时任务用）")
    p.add_argument("--item", type=int, help="mark：第几条行动")
    p.add_argument("--project", help="mark：派到哪个名册项目")
    p.add_argument("--order", help="mark：车间单号")
    p.add_argument("--skip", action="store_true", help="mark：常浩说这条不做")
    a = p.parse_args(argv)
    try:
        day = dt.date.fromisoformat(a.date) if a.date else default_day()
    except ValueError:
        print(f"日期写法不对：{a.date}")
        return 1
    reply = Path(a.reply).read_text(encoding="utf-8") if a.reply else None
    if a.cmd in ("todos", "mark"):
        try:
            res = (todos(a.date) if a.cmd == "todos"
                   else mark(a.date, a.item, a.project, a.order, a.skip))
        except Fail as e:
            print(json.dumps({"ok": False, "msg": str(e)}, ensure_ascii=False))
            return 1
        print(json.dumps(res if a.cmd == "todos" else {"ok": True, "msg": res}, ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "send":
        err = send(day.isoformat(), a.force)
        if err:
            print(f"⚠️ 今天的 AI 前沿群学习报告没发好：{err}。详情在 DigitalTwin/groupdigest/{day}.delivery.json")
        return 0
    try:
        if a.cmd == "collect":
            res = collect(day)["coverage"]
        elif a.cmd == "build":
            res = build(day, reply)
        elif a.cmd == "preview":
            res = preview(day.isoformat())
        elif a.cmd == "card":
            d = day.isoformat()
            res = build_card(d, *(json.loads((DIR / f"{d}.{n}.json").read_text(encoding="utf-8"))
                                  for n in ("card-fields", "build", "material")), doc_url="https://example.invalid/preview")
        else:
            res = run_build(day, reply)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"ok": False, "day": day.isoformat(), "error": clip(e, 200)}, ensure_ascii=False))
        return 1
    if not a.quiet:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
