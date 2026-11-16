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
SECTIONS = ["一、今日一句话", "二、昨日要点", "三、和你有关的", "四、今天就能做的", "五、值得深入学的",
            "六、值得永久采纳的（提案）", "七、路径建议"]
COVERAGE = "八、覆盖与可信度"
LIMITS = {"headline": 80, "title": 30, "point": 120, "for_you": 80, "action": 60, "why": 60, "what": 50, "focus": 60,
          "rule": 80, "where": 20, "direction": 160}
SLACK = 1.5
LISTS = {"highlights": (("title", "point", "for_you"), 1, 5), "do_today": (("action", "why"), 0, 3),
         "learn": (("what", "focus"), 0, 3), "adopt": (("rule", "where", "why"), 0, 3)}
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
            rec = {"name": p["name"], "type": p.get("type"), "goal": None}
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
    for key in ("headline", "direction"):
        v = str(fields.get(key) or "").strip()
        if not v:
            errs.append(f"卡片要素缺 {key}")
        too_long(v, key, key)
        out[key] = v
    for key, (texts, lo, hi) in LISTS.items():
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
            for t in texts:
                v = str(r.get(t) or "").strip()
                if not v:
                    errs.append(f"{key} 第 {n} 条缺 {t}")
                too_long(v, t, f"{key} 第 {n} 条的 {t}")
                row[t] = v
            refs = [str(x).strip() for x in r.get("refs") or [] if str(x).strip()]
            bad = [x for x in refs if x not in ids]
            if not refs:
                errs.append(f"{key} 第 {n} 条没有出处编号")
            if bad:
                errs.append(f"{key} 第 {n} 条引用了材料里没有的编号 {'、'.join(bad)}")
            row["refs"] = refs
            if key == "do_today":
                try:
                    row["minutes"] = max(5, min(240, int(r.get("minutes"))))
                except (TypeError, ValueError):
                    errs.append(f"do_today 第 {n} 条的 minutes 不是数字")
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
    foreign = sorted({u for u in (strip_url(x) for x in URL.findall(report)) if u.split("#")[0].rstrip("/") not in ok_urls})
    if foreign:
        errs.append(f"完整报告里有材料以外的网址（{clip('、'.join(foreign), 200)}）：只写编号，链接由脚本填")
    claims = sorted(set(DONE_CLAIM.findall(report + json.dumps(out, ensure_ascii=False))))
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
    fields = {"headline": f"猛鬼兽今天没写成分析（{clip(reason, 40)}），先给你昨天群里的原始清单",
              "highlights": [{"title": clip(i["title"] or i["sender"], LIMITS["title"]),
                              "point": clip(i["gist"] or i["text"].splitlines()[0], LIMITS["point"]),
                              "for_you": "", "refs": [i["id"]]} for i in cards[:8]],
              "do_today": [], "learn": [], "adopt": [], "direction": ""}
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
        fields = {"headline": "昨天群里没有新消息", "highlights": [], "do_today": [], "learn": [], "adopt": [], "direction": ""}
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
    return s.translate(str.maketrans({"[": "［", "]": "］", "<": "＜", ">": "＞", "`": "'", "\\": "＼"}))


def refs_md(refs, links):
    return " ".join(f"[{r}]({links[r]})" if links.get(r) else r for r in refs)


def md(content, **kw):
    return {"tag": "markdown", "content": content, **kw}


def title_of(day):
    return f"🧭 AI 前沿群学习报告 · {short_day(day)}"


STYLES = {"a": "甲 · 全文清单", "b": "乙 · 行动优先", "c": "丙 · 主题面板"}
STYLE = os.environ.get("DT_GD_STYLE", "a")       # 常浩挑定前先用甲版；三版都发过私聊给他比（11-16）
ADOPT_NOTE = "<font color='grey'>　提案，你点头才落地</font>"


def numbered(rows, fmt, links):
    return md("\n".join(f"{n}. {fmt(r)}　{refs_md(r['refs'], links)}" for n, r in enumerate(rows, 1)))


def grey_box(elements, **kw):
    """一整行灰底块（飞书卡片没有单独的面板组件，用单列 column_set 的背景色做）。"""
    return {"tag": "column_set", "flex_mode": "none", "background_style": "grey", "horizontal_spacing": "8px", **kw,
            "columns": [{"tag": "column", "width": "weighted", "weight": 1, "vertical_spacing": "4px", "elements": elements}]}


def style_a(f, links):
    """甲 · 全文清单：五块按顺序全放卡片，每条带编号链接。"""
    els = [md(f"**{esc(f['headline'])}**")]
    for icon, name, key, fmt, note in [
            ("📌", "昨日要点", "highlights", lambda r: f"**{esc(r['title'])}**：{esc(r['point'])}"
             + (f"\n<font color='grey'>和你：{esc(r['for_you'])}</font>" if r.get("for_you") else ""), ""),
            ("⚡", "今天就能做", "do_today", lambda r: f"{esc(r['action'])}（约 {r['minutes']} 分钟）\n<font color='grey'>{esc(r['why'])}</font>", ""),
            ("📚", "值得深入学", "learn", lambda r: f"{esc(r['what'])}\n<font color='grey'>带着这个问题读：{esc(r['focus'])}</font>", ""),
            ("🧱", "值得永久采纳", "adopt", lambda r: f"{esc(r['rule'])}（建议放 {esc(r['where'])}）\n<font color='grey'>{esc(r['why'])}</font>",
             ADOPT_NOTE)]:
        if f[key]:
            els += [{"tag": "hr"}, md(f"{icon} **{name}**{note}"), numbered(f[key], fmt, links)]
    if f.get("direction"):
        els += [{"tag": "hr"}, md(f"🧭 **路径建议**\n{esc(f['direction'])}")]
    return els


def style_b(f, links):
    """乙 · 行动优先：先摆今天能做的事（分钟数在左栏），要点只留标题和「和你的关系」，学习、采纳各一行。"""
    els = [md(f"**{esc(f['headline'])}**")]
    if f["do_today"]:
        els.append(md("⚡ **今天就做**"))
        for r in f["do_today"]:
            els.append({"tag": "column_set", "flex_mode": "none", "background_style": "grey", "horizontal_spacing": "8px",
                        "columns": [
                            {"tag": "column", "width": "52px", "vertical_align": "top", "elements": [
                                md(f"**<font color='red'>{r['minutes']}</font>**\n<font color='grey'>分钟</font>", text_align="center")]},
                            {"tag": "column", "width": "weighted", "weight": 1, "vertical_spacing": "2px", "elements": [
                                md(f"{esc(r['action'])}　{refs_md(r['refs'], links)}"),
                                md(f"<font color='grey'>{esc(r['why'])}</font>")]}]})
    if f["highlights"]:
        els += [{"tag": "hr"}, md("📌 **昨日要点**"), numbered(
            f["highlights"], lambda r: f"**{esc(r['title'])}**" + (f"\n<font color='grey'>{esc(r['for_you'])}</font>"
                                                                  if r.get("for_you") else ""), links)]
    if f["learn"]:
        els += [{"tag": "hr"}, md("📚 **去学**"), numbered(f["learn"], lambda r: esc(r["what"]), links)]
    if f["adopt"]:
        els += [md(f"🧱 **可采纳**{ADOPT_NOTE}"),
                numbered(f["adopt"], lambda r: f"{esc(r['rule'])} <font color='grey'>→ {esc(r['where'])}</font>", links)]
    if f.get("direction"):
        els += [{"tag": "hr"}, md(f"🧭 **方向**　{esc(f['direction'])}")]
    return els


def style_c(f, links, cov):
    """丙 · 主题面板：一排数字说明当天的量，每个要点一块灰底面板，下方左右两栏放「去做」和「去学 / 采纳」。"""
    stat = lambda n, label: {"tag": "column", "width": "weighted", "weight": 1, "background_style": "grey", "elements": [
        md(f"**{n}**\n<font color='grey'>{label}</font>", text_align="center")]}
    els = [{"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "6px", "columns": [
               stat(cov["messages"], "条消息"), stat(f"{cov['sources_ok']}/{cov['sources']}", "源头读到"),
               stat(len(f["highlights"]), "个要点"), stat(len(f["do_today"]), "件可做")]},
           md(f"**{esc(f['headline'])}**")]
    for n, r in enumerate(f["highlights"], 1):
        els.append(grey_box([md(f"<text_tag color='indigo'>要点 {n}</text_tag> **{esc(r['title'])}**"),
                             md(esc(r["point"])),
                             *([md(f"<font color='grey'>→ 和你：{esc(r['for_you'])}</font>")] if r.get("for_you") else []),
                             md(refs_md(r["refs"], links), text_size="notation")]))
    left = [md("⚡ **去做**")] + [md(f"{esc(r['action'])} <text_tag color='indigo'>{r['minutes']} 分钟</text_tag> "
                                     f"{refs_md(r['refs'], links)}") for r in f["do_today"]]
    right = [md("📚 **去学**")] + [md(f"{esc(r['what'])}\n<font color='grey'>{esc(r['focus'])}</font> {refs_md(r['refs'], links)}")
                                    for r in f["learn"]]
    if f["adopt"]:
        right += [md(f"🧱 **采纳**{ADOPT_NOTE}")] + [
            md(f"{esc(r['rule'])} <font color='grey'>→ {esc(r['where'])}</font> {refs_md(r['refs'], links)}") for r in f["adopt"]]
    if f["do_today"] or f["learn"] or f["adopt"]:
        els += [{"tag": "hr"}, {"tag": "column_set", "flex_mode": "stretch", "horizontal_spacing": "12px", "columns": [
            {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top", "vertical_spacing": "6px", "elements": left},
            {"tag": "column", "width": "weighted", "weight": 1, "vertical_align": "top", "vertical_spacing": "6px", "elements": right}]}]
    if f.get("direction"):
        els += [{"tag": "hr"}, md(f"🧭 **路径建议**\n{esc(f['direction'])}")]
    return els


def build_card(day, fields, meta, mat, doc_url=None, doc_error=None, style=None, title=None):
    style = style if style in STYLES else (STYLE if STYLE in STYLES else "a")
    links = link_of(mat)
    c = mat["coverage"]
    els = {"a": lambda: style_a(fields, links), "b": lambda: style_b(fields, links),
           "c": lambda: style_c(fields, links, c)}[style]()
    if doc_url:
        els.append({"tag": "button", "text": {"tag": "plain_text", "content": "查看完整报告"}, "type": "primary_filled",
                    "behaviors": [{"type": "open_url", "default_url": doc_url}]})
    elif doc_error:
        els.append(md(f"<font color='red'>完整报告没建成：{esc(clip(doc_error, 80))}，本地在 DigitalTwin/groupdigest/{day}.report.md</font>"))
    foot = f"读了 {c['messages']} 条消息、{c['sources_ok']}/{c['sources']} 个源头｜{meta['trace']}"
    els += [{"tag": "hr"}, md(f"<font color='grey'>{esc(foot)}</font>", text_size="notation")]
    sub = (f"今天 {len(fields['do_today'])} 件可做 · {len(fields['highlights'])} 个要点" if style == "b"
           else f"{GROUP_NAME}｜猛鬼兽深度梳理")
    title = title or title_of(day)
    return {"schema": "2.0", "config": {"update_multi": True, "summary": {"content": title}},
            "header": {"title": {"tag": "plain_text", "content": title},
                       "subtitle": {"tag": "plain_text", "content": sub}, "template": "indigo"},
            "body": {"elements": els}}


def preview(day):
    """把甲乙丙三版卡片都发到常浩私聊给他挑（常浩 11-16：要看飞书里的实际样子）。不记 delivery，不算当天已发。
    返回 [(版本, 消息号或错误)]。"""
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
    out = []
    for v, name in STYLES.items():
        title = f"【样式{name}】{title_of(day)}"
        card = build_card(day, fields, meta, mat, doc_url, doc_error, style=v, title=title)
        try:
            mid = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "interactive",
                        "--content", json.dumps(card, ensure_ascii=False),
                        "--idempotency-key", f"groupdigest-preview-{day}-{v}-{int(time.time())}"], f"发样式{name}", "bot").get("message_id")
            got = lark(["im", "+messages-mget", "--message-ids", mid], "回读", "bot")
            m = (got.get("messages") or [{}])[0]
            ok = m.get("msg_type") == "interactive" and title in str(m.get("content"))
            out.append((v, mid if ok else f"已发 {mid}，回读对不上"))
        except Fail as e:
            out.append((v, f"没发成：{e}"))
    return out


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
    p.add_argument("cmd", choices=("collect", "build", "run", "send", "card", "preview"))
    p.add_argument("--date", help="统计日 YYYY-MM-DD，默认昨天")
    p.add_argument("--reply", help="build / run：用现成的模型回复文件（回放、测试用）")
    p.add_argument("--force", action="store_true", help="send：这一天已经发过也再发一次")
    p.add_argument("--quiet", action="store_true", help="成功不输出（定时任务用）")
    a = p.parse_args(argv)
    try:
        day = dt.date.fromisoformat(a.date) if a.date else default_day()
    except ValueError:
        print(f"日期写法不对：{a.date}")
        return 1
    reply = Path(a.reply).read_text(encoding="utf-8") if a.reply else None
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
