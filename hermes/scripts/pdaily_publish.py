"""个人项目日报写稿与核对（#111 批 2a）：读 1c 的事实快照，让 Hermes 写卡片要素和完整报告，脚本核对后
按甲版生成飞书卡片 JSON；模型失败或核对不过就出事实版（标「分析降级」）。send 子命令负责发送（#155）。

规格：docs/个人项目日报-V2-设计.md 第 3 节（篇幅、留痕）、4.0（卡片）、4.1–4.9（完整报告）、第 6 节（证据）、7.1。
用法：
  python3 pdaily_publish.py build [--date 统计日] [--facts 快照] [--reply 模型回复文件] [--fallback] [--out 目录]
      默认读 DigitalTwin/daily/<统计日>.facts.json（没有、或是统计日结束前取的，就先跑 pdaily_facts 重取），
      调 Hermes 单次写稿，写出 <统计日>.card.json、.report.md、.card-fields.json、.build.json，最后打印一行 JSON 摘要。
      --reply 用现成的模型回复（回放、测试用），--fallback 直接出事实版。取数本身失败时出一张「取数失败」的最小卡片。
  python3 pdaily_publish.py material [--date 统计日] [--facts 快照]   只打印交给模型的压缩材料
  python3 pdaily_publish.py send [--date 统计日] [--out 目录] [--force]
      建完整报告云文档，按甲版现做卡片，拦工作内容（PRD D22：猛鬼兽只报个人项目），以猛鬼兽机器人发到常浩私聊并回读，
      记 <统计日>.delivery.json；同一统计日只发一次。卡片里带工程自检结果（#159）。成功不输出，失败打印一句原因（#155，定时任务 10:00）。
分工：能从快照算出来的（核心数据五行、成果为空时是没进展还是没数据、留痕）由脚本算；判断（什么算实质推进、拍板类型、
风险等级、推荐、今日计划）由模型写。脚本核对：编号、百分比、PR 数、「已安装 / 已验收」这类说法要有快照出处，
七段齐全、三个数和卡片一致；编造类问题判不通过（出事实版），写法问题（最晚时间格式、超长）自动修正并记提醒。
留痕（P-13）：模型取 Hermes 会话库里这次写稿会话实际用过的主模型（含回退）。
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdaily_data  # noqa: E402
import pdaily_facts  # noqa: E402

DAILY = pdaily_facts.DAILY
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
HERMES = os.environ.get("DT_HERMES_CLI", "hermes")
PROMPT = Path(os.environ.get("DT_PDAILY_PROMPT", Path(__file__).resolve().parent.parent / "prompts" / "pdaily_report.md"))
BUDGET = int(os.environ.get("DT_PDAILY_BUDGET", "1200"))   # 写稿最多等多少秒
# 写稿的思考强度（常浩 11-11 定 medium，#157）：同一份材料实测 high 要 8 分钟、medium 3 分钟，拍板、风险、成果几乎不少；
# 模型跟随 Hermes 主模型（Opus 5.5），这里只降思考。要改就设这个变量，不用改代码。
REASONING = os.environ.get("DT_PDAILY_REASONING", "medium")
CARD_MAX = None       # 卡片各板块不折叠、全部放卡片上（常浩 2026-11-10 定稿：「卡片上的信息不要折叠，放全」）
TODAY_MAX = 3         # 今日推进卡片最多 3 条（4.0）
REPORT_TODAY_MAX = 5  # 完整报告今日计划最多 5 项（4.7）
CLIP = 300            # 材料里每段原文最多保留多少字
ACT_CLIP = 100        # 行动记录每个字段最多保留多少字（行动多，压短些）
# 卡片字段硬上限：比提示词给模型的字数（20 / 45 / 15 / 50）宽一点，留余量，超了才截断
LIMITS = {"headline": 60, "item": 24, "recommendation": 50, "reply": 20, "text": 50, "owner_action": 50}
WEEK = "一二三四五六日"
DECISION_TYPES = ("阻断", "限时", "普通")
LEVELS = {"高": "🔴", "中": "🟠", "低": "🟡", "待识别": "⚪"}
CATEGORIES = ("交付风险", "协作阻塞", "运行风险", "管理失真")
PRIORITIES = ("P0", "P1")
SECTIONS = ["一、需要你拍板", "二、昨日关键成果", "三、各项目推进", "四、风险、阻塞与跨项目依赖",
            "五、Hermes 代理述职", "六、今日推进计划", "七、机制改进与沉淀", "覆盖与可信度"]
KIND_NAMES = {"merge": "合并", "ruling": "裁决", "dispatch": "派单", "supervise": "主控与监护", "refuse": "拒绝",
              "install": "安装", "other": "其他", "cron": "定时任务"}
SELF_NAMES = {"猛鬼兽", "Hermes", "hermes"}   # 猛鬼兽自身（定时任务、技能、配置、授权）也在日报范围里
REF = re.compile(r"(?:(?<=PR)|(?<=pr)|(?<=Pr)|(?<=issue)|(?<=Issue)|(?<=ISSUE)|(?<![A-Za-z0-9_&/.-]))#(\d+)")  # 裸写的 #N、PR#N；中文紧挨着也算（「驿窗#12」）
QREF = re.compile(r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)?(?!(?:PR|pr|Pr|issue|Issue|ISSUE)#)([A-Za-z][\w.-]*)#(\d+)")  # repo#N、owner/repo#N；PR#N、issue#N 当裸编号
PCT = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
CLAIM = re.compile(r"已(?:经)?(?:实机)?(?:安装|发布|上线|装好|装上|装机|部署|交付|验收)|(?:安装|发布|上线|部署)(?:完成|成功|好了)"
                   r"|发布到.{0,12}?了|装好了|验收(?:已)?通过|通过(?:了)?(?:实机)?验收")
NEGATION = re.compile(r"未|不|没|尚|无法|缺|否|失败|自述|待核|待验")
INSTALL_WORDS = re.compile(r"安装|发布|上线|装好|装上|装机|部署|交付")
PR_COUNT = re.compile(r"合并(?:了)?\s*(\d+)\s*个\s*PR")
TRAIL = "。．.；;，,、 "
CARD_MARK, REPORT_MARK = "<<<CARD>>>", "<<<REPORT>>>"


class GenError(Exception):
    """写稿没成：调用失败、回复格式不对。原因进留痕行和 build.json，不含凭据。"""


def s(x):
    """取字符串字段：不是字符串（模型写成列表、数字）一律当 None，交给核对报错，不让它在别处崩。"""
    return x.strip() if isinstance(x, str) and x.strip() else None


def clip(v, n=CLIP):
    v = "" if v is None else str(v)
    return v if len(v) <= n else v[:n] + "…"


# ---------- 材料 ----------

def material(facts):
    """压缩给模型的材料：例行定时任务按结果合并成计数（有授权缺口的逐条留着），长原文截断；判断要用的字段都留着。"""
    projects = []
    for p in facts.get("projects") or []:
        d = p.get("yesterday_delta") or {}
        commits = d.get("commits")
        projects.append({
            "project_id": p.get("project_id"), "name": p.get("name"), "objective": p.get("objective"),
            "milestones": p.get("milestones"), "freshness": p.get("freshness"),
            "current_state": p.get("current_state"),
            "merged_prs": None if d.get("merged_prs") is None else [{
                "number": x.get("number"), "title": x.get("title"), "engineering": x.get("engineering"),
                "delivery": x.get("delivery"), "acceptance": x.get("acceptance"),
                "claims": [clip(q) for c in x.get("claims") or [] for q in c.get("quotes") or []][:3],
            } for x in d["merged_prs"]],
            "closed_issues": None if d.get("closed_issues") is None else [
                {"number": x.get("number"), "title": x.get("title")} for x in d["closed_issues"]],
            "commits": None if commits is None else sum(commits.values()),
            "current_changed": d.get("current_changed"),
            "installs": p.get("installs"), "notes": p.get("notes"), "errors": p.get("errors"),
        })
    routine, actions = {}, []
    for a in facts.get("actions") or []:
        if a.get("kind") == "cron" and a.get("result") == "调用成功" and not a.get("auth_gap"):
            key = f"{a.get('result')}｜{clip(a.get('verification'), 40)}"
            routine[key] = routine.get(key, 0) + 1
            continue
        actions.append({k: (clip(v, ACT_CLIP) if isinstance(v, str) else v) for k, v in a.items() if k != "action_id"})
    decisions = [dict(d, candidate=i) for i, d in enumerate(facts.get("decisions") or [])]
    return {
        "stat_day": facts.get("stat_day"), "window": facts.get("window"), "fetched_at": facts.get("fetched_at"),
        "projects": projects,
        "actions": actions, "routine_cron": routine,
        "decisions": decisions, "risks": facts.get("risks"), "cross_refs": facts.get("cross_refs"),
        "observations": facts.get("observations"), "counts": facts.get("counts"),
        "activity": facts.get("activity"), "coverage": facts.get("coverage"), "notes": facts.get("notes"),
    }


# ---------- 写稿 ----------

def query(facts):
    return PROMPT.read_text(encoding="utf-8") + "\n\n## 材料\n\n```json\n" + \
        json.dumps(material(facts), ensure_ascii=False, separators=(",", ":")) + "\n```\n"


def session_model(session_id):
    """这次写稿会话实际用过的主模型（含回退，按首次使用排），查不到返回 None。只读打开，库不在不新建。"""
    db = HERMES_HOME / "state.db"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        rows = con.execute("select model from session_model_usage where session_id=? and task='' "
                           "order by first_seen", (session_id,)).fetchall()
        con.close()
    except sqlite3.Error:
        return None
    models = list(dict.fromkeys(r[0] for r in rows if r[0]))
    return "，回退 ".join(models) if models else None


def generate(facts, workdir):
    """调 Hermes 单次写稿，返回 (回复全文, 会话 ID, 模型)。失败抛 GenError。"""
    q = Path(workdir) / f"{facts['stat_day']}.query.md"
    q.write_text(query(facts), encoding="utf-8")
    cmd = [HERMES, "chat", "-Q", "--query-file", str(q), "-t", "todo", "--source", "tool",
           "--max-turns", "3", "--run-budget", str(BUDGET), "--reasoning", REASONING]
    # Claude 一次写完整稿要几分钟；Hermes 对非流式调用默认 240 秒没回就掐断并落回退模型（11-11 两次）。
    # 显式给足写稿预算；去掉 Claude Code 会话里继承的变量，免得插件拒绝调用（CURRENT 11-11）。
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env.setdefault("HERMES_API_CALL_STALE_TIMEOUT", str(BUDGET))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=BUDGET + 120, cwd=str(workdir), env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise GenError(f"Hermes 跑不起来（{type(e).__name__}）")
    sid = (re.findall(r"session_id:\s*(\S+)", r.stderr) or [None])[-1]   # -Q 模式把会话号打在 stderr
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()
        raise GenError(f"Hermes 退出码 {r.returncode}：{clip(tail[-1] if tail else '', 120)}")
    return r.stdout.strip(), sid, session_model(sid) if sid else None


def parse_reply(text):
    """拆成 (卡片要素 dict, 完整报告 Markdown)。格式不对抛 GenError。"""
    if CARD_MARK not in text or REPORT_MARK not in text:
        raise GenError("回复里没有 <<<CARD>>> / <<<REPORT>>> 分隔")
    card_part, report = text.split(CARD_MARK, 1)[1].split(REPORT_MARK, 1)
    card_part = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", card_part.strip())
    try:
        card = json.loads(card_part)
    except ValueError as e:
        raise GenError(f"卡片要素不是合法 JSON（{getattr(e, 'msg', e)}）")
    if not isinstance(card, dict):
        raise GenError("卡片要素不是 JSON 对象")
    return card, report.strip()


# ---------- 项目名 ----------

class Roster:
    """名册里的项目：按 id、全名、括号里的简称（「个人 AI 工作台（数码世界）」→ 数码世界）认。"""
    def __init__(self, facts):
        self.by_id = {p.get("project_id"): p for p in facts.get("projects") or [] if p.get("project_id")}
        self.alias = {}
        for pid, p in self.by_id.items():
            name = p.get("name") or pid
            for a in [pid, name] + re.findall(r"[（(]([^）)]+)[）)]", name) + [re.sub(r"[（(].*?[）)]", "", name).strip()]:
                if a and len(a) >= 2:
                    self.alias.setdefault(a, pid)

    def resolve(self, name):
        """返回 project_id、"hermes"（猛鬼兽自身）或 None（不认识）。"""
        if name in SELF_NAMES:
            return "hermes"
        return self.alias.get(name)

    def name(self, pid):
        p = self.by_id.get(pid)
        return (p.get("name") or pid) if p else ("猛鬼兽" if pid in (None, "hermes") else pid)


def split_names(v):
    return [x.strip() for x in re.split(r"[↔、,，/]| 和 ", v or "") if x.strip()]


def risk_projects(card):
    out = set()
    for r in card.get("risks") or []:
        if isinstance(r, dict):
            out.update(split_names(s(r.get("project"))))
    return sorted(out)


# ---------- 算数 ----------

def roster_size(facts):
    cov = (facts.get("coverage") or {}).get("projects") or {}
    return cov.get("projects", len(facts.get("projects") or []))


def unread(facts):
    return list(((facts.get("coverage") or {}).get("projects") or {}).get("errors") or {})


def num(v):
    return "未知" if v is None else str(v)


def delta_unknown(facts):
    """GitHub 取不到合并 PR 的项目（成果为空时，要说是没数据不是没进展）。"""
    return [p.get("project_id") for p in facts.get("projects") or []
            if (p.get("yesterday_delta") or {}).get("merged_prs") is None]


def action_counts(facts):
    """代理动作：定时任务单列，其余按类别计数；有来源取不到就写「N+」并写明。"""
    by = (facts.get("activity") or {}).get("actions_by_kind")
    srcs = ((facts.get("coverage") or {}).get("actions") or {})
    bad = [k for k, v in srcs.items() if not (v or {}).get("ok")]
    if by is None:
        return None, "行动记录取不到"
    main = {k: v for k, v in by.items() if k != "cron" and v}
    parts = [f"{KIND_NAMES.get(k, k)} {v}" for k, v in sorted(main.items(), key=lambda x: -x[1])]
    if by.get("cron"):
        parts.append(f"另定时任务 {by['cron']} 次")
    note = "、".join(parts) or "无"
    if bad:
        return f"{sum(main.values())}+", note + f"；{'、'.join(bad)}取不到"
    return str(sum(main.values())), note


def progress_ids(card):
    p = card.get("progress_projects")
    return None if not isinstance(p, list) else sorted({x for x in p if isinstance(x, str)})


def metrics(facts, card):
    """核心数据速览五行（4.0），写法照甲版样例。"""
    n = roster_size(facts)
    progress = progress_ids(card)
    act_v, act_n = action_counts(facts)
    dec = [d for d in card.get("decisions") or [] if isinstance(d, dict)]
    degraded = card.get("degraded")
    by_type = [f"{t} {sum(1 for d in dec if d.get('type') == t)} 件" for t in DECISION_TYPES
               if any(d.get("type") == t for d in dec)]
    deadlines = sorted(d["deadline"] for d in dec if s(d.get("deadline")))
    risks = [r for r in card.get("risks") or [] if isinstance(r, dict)]
    cats = [f"{c.replace('风险', '')} {sum(1 for r in risks if r.get('category') == c)}"
            for c in CATEGORIES if any(r.get("category") == c for r in risks)]
    merged = (facts.get("activity") or {}).get("merged_prs")
    if degraded:
        dec_v, dec_n = (f"候选 {len(dec)}", "分析降级，类型未判断")
    else:
        dec_v = f"<font color='red'>**{len(dec)}**</font>" if dec else "0"
        dec_n = "、".join(by_type) + (f"，最晚 {deadlines[0]}" if deadlines else "")
    return [
        ("有推进的项目", "未知" if progress is None else f"**{len(progress)}** / {n}",
         "分析降级，没判断" if progress is None else f"其余 {n - len(progress)} 个无实质推进"),
        ("合并 PR", num(merged), "统计日 04:00→04:00" if merged is not None else "GitHub 取不到"),
        ("待你拍板", dec_v, dec_n),
        ("风险 / 阻塞", f"{len(risk_projects(card))} 个项目", "、".join(cats)),
        ("Hermes 代理动作", act_v or "未知", act_n),
    ]


def header_numbers(card):
    """完整报告开头三个数（4.1），和卡片同源。"""
    progress = progress_ids(card)
    return ("未知" if progress is None else str(len(progress)),
            str(len([d for d in card.get("decisions") or [] if isinstance(d, dict)])), str(len(risk_projects(card))))


# ---------- 规整（写法问题自动修，记提醒） ----------

def norm_deadline(v, year):
    """「2026-11-09 06:00」「11-09 06:00」「11月9日 06:00」→「11-09 06:00」；只有时刻、写不清的返回 None。"""
    m = re.fullmatch(r"(?:(\d{4})-)?(\d{1,2})[-月/](\d{1,2})日?\s*(\d{1,2}):(\d{2})", v.strip())
    if not m:
        return None
    _, mo, d, h, mi = m.groups()
    return f"{int(mo):02d}-{int(d):02d} {int(h):02d}:{mi}"


def deadline_source(deadline, cand):
    """最晚时间只认对应拍板候选的 deadline 字段、事项原文或证据里写着的同一个日期时间。"""
    if not isinstance(cand, dict):
        return False
    md, hm = deadline.split()
    text = json.dumps({k: cand.get(k) for k in ("deadline", "item", "evidence", "why_owner")}, ensure_ascii=False)
    return f"{md}T{hm}" in text or f"{md} {hm}" in text or \
        f"{int(md[:2])}-{int(md[3:])} {hm}" in text


def cut(v, n):
    """截到 n 字以内：尽量停在最后一个标点上，不在词中间切。"""
    head = v[:n]
    k = max(head.rfind(c) for c in "，；。、,;")
    return (head[:k] if k >= n // 2 else head).rstrip(TRAIL) + "…"


def normalize(facts, card):
    """就地规整卡片要素，返回提醒清单。只修写法：去尾部标点、去回复外层「回「」」、最晚时间换格式或去掉、超长截断。"""
    warns = []
    cands = facts.get("decisions") or []
    year = (facts.get("stat_day") or "2000")[:4]
    for key in ("decisions", "achievements", "risks", "today"):
        for i, x in enumerate(card.get(key) or []):
            if not isinstance(x, dict):
                continue
            for f in ("item", "recommendation", "text", "owner_action", "reply"):
                v = s(x.get(f))
                if v is None:
                    continue
                if f == "reply":
                    v = re.sub(r"^(?:回复?|回答)?\s*[「“\"](.*)[」”\"]\s*(?:即可|就行)?$", r"\1", v)
                v = v.rstrip(TRAIL)
                if len(v) > LIMITS[f]:
                    warns.append(f"{key}[{i}].{f} 超过 {LIMITS[f]} 字，已截断")
                    v = cut(v, LIMITS[f])
                x[f] = v
            if key == "decisions" and x.get("deadline") is not None:
                raw = x.get("deadline")
                dl = norm_deadline(raw, year) if isinstance(raw, str) else None
                ci = x.get("candidate")
                cand = cands[ci] if isinstance(ci, int) and not isinstance(ci, bool) and 0 <= ci < len(cands) else None
                if dl is None:
                    warns.append(f"decisions[{i}] 的最晚时间「{raw}」写法认不出（要 MM-DD HH:MM），已去掉")
                elif not deadline_source(dl, cand):
                    warns.append(f"decisions[{i}] 的最晚时间「{dl}」在对应拍板候选里找不到出处，已去掉")
                    dl = None
                x["deadline"] = dl
    h = s(card.get("headline"))
    if h and len(h) > LIMITS["headline"]:
        warns.append(f"headline 超过 {LIMITS['headline']} 字，已截断")
        card["headline"] = cut(h, LIMITS["headline"])
    card.pop("progress_note", None)        # 说明列用脚本文案（样例：「其余 N 个无实质推进」）
    return warns


# ---------- 核对（编造类，不通过就出事实版） ----------

def known_refs(facts):
    """快照里出现过的编号：裸的 N（PR、issue、原文 #N）和带仓库的 repo#N。"""
    dump = json.dumps(facts, ensure_ascii=False)
    bare = set(REF.findall(dump)) | {m for m in re.findall(r'"(?:number|to_number)": (\d+)', dump)}
    qual = {f"{r}#{n}" for r, n in QREF.findall(dump)}
    qual |= {f"{r}#{n}" for r, n in re.findall(r"github\.com/[\w.-]+/([\w.-]+)/(?:pull|issues)/(\d+)", dump)}
    for p in facts.get("projects") or []:
        d = p.get("yesterday_delta") or {}
        for x in (d.get("merged_prs") or []) + (d.get("closed_issues") or []):
            qual.add(f"{p.get('project_id')}#{x.get('number')}")
            bare.add(str(x.get("number")))
    bare |= {q.split("#")[1] for q in qual}
    return bare, qual, dump


def project_verified(p, words):
    """项目有没有能撑住「已安装 / 已验收」的结构化证据：安装认 installs，验收认合并 PR 的 acceptance。"""
    if INSTALL_WORDS.search(words):
        return bool(p.get("installs"))
    prs = (p.get("yesterday_delta") or {}).get("merged_prs") or []
    return any(x.get("acceptance") not in (None, "未核实", "未知") for x in prs)


def claims_unverified(text, record):
    """一句话里有没有没证据的「已安装 / 已发布 / 验收通过」：按逗号句号切成小句，带否定词的小句不算。"""
    bad = []
    for clause in re.split(r"[，。；;,、！!？?\n]", text or ""):
        if CLAIM.search(clause) and not NEGATION.search(clause) and not project_verified(record or {}, clause):
            bad.append(clause.strip())
    return bad


def section_positions(report):
    out = []
    for name in SECTIONS:
        m = re.search(rf"^#{{1,3}}\s*{re.escape(name)}", report, re.M)
        out.append(m.start() if m else -1)
    return out


def check(facts, card, report):
    """返回问题清单，空表示通过。只查编造和结构，不评判写得好不好。先跑 normalize。"""
    errs = []
    roster = Roster(facts)
    bare, qual, dump = known_refs(facts)

    def need_list(key, fields, allowed=None):
        v = card.get(key)
        if not isinstance(v, list):
            errs.append(f"卡片要素缺 {key}（要一个列表，没有就给空列表）")
            return []
        out = []
        for i, x in enumerate(v):
            if not isinstance(x, dict):
                errs.append(f"{key}[{i}] 不是对象")
                continue
            for f in fields:
                if s(x.get(f)) is None:
                    errs.append(f"{key}[{i}] 缺 {f}（要非空字符串）")
            for f, ok in (allowed or {}).items():
                if x.get(f) not in ok:
                    errs.append(f"{key}[{i}].{f}「{x.get(f)}」不在 {'/'.join(ok)} 里")
            for n in split_names(s(x.get("project"))):
                if roster.resolve(n) is None:
                    errs.append(f"{key}[{i}] 的项目「{n}」不在名册里")
            out.append(x)
        return out

    if s(card.get("headline")) is None:
        errs.append("卡片要素缺 headline（一句话）")
    progress = card.get("progress_projects")
    if not isinstance(progress, list):
        errs.append("卡片要素缺 progress_projects（实质推进的项目 id 列表）")
    else:
        for p in progress:
            rec = roster.by_id.get(p) if isinstance(p, str) else None
            if rec is None:
                errs.append(f"progress_projects 里的「{p}」不是名册项目 id")
                continue
            d = rec.get("yesterday_delta") or {}
            if not (d.get("merged_prs") or d.get("closed_issues") or rec.get("installs")):
                errs.append(f"progress_projects 里的「{p}」昨天没有合并、关闭或安装，只有提交或 CURRENT 改动不算实质推进")
    need_list("decisions", ("project", "item", "recommendation", "reply"), {"type": DECISION_TYPES})
    ach = need_list("achievements", ("project", "text"))
    need_list("risks", ("project", "text"), {"level": tuple(LEVELS), "category": CATEGORIES})
    today = need_list("today", ("item", "owner_action"), {"priority": PRIORITIES})
    if len(today) > TODAY_MAX:
        errs.append(f"今日推进卡片最多 {TODAY_MAX} 条，给了 {len(today)} 条")
    mentioned = set()
    for key in ("decisions", "achievements", "risks"):
        for x in card.get(key) or []:
            if isinstance(x, dict):
                mentioned.update(split_names(s(x.get("project"))))
    pids = {roster.resolve(n) for n in mentioned} - {None}
    words = set(mentioned) | {a for a, pid in roster.alias.items() if pid in pids} | (SELF_NAMES if "hermes" in pids else set())
    for i, t in enumerate(today):
        text = f"{s(t.get('item')) or ''}{s(t.get('owner_action')) or ''}"
        if not any(n in text for n in words):       # 全名、简称、id 都算点到
            errs.append(f"today[{i}]「{clip(t.get('item'), 30)}」和拍板、成果、风险里的项目都对不上")
    for i, a in enumerate(ach):
        for n in split_names(s(a.get("project"))):
            rec = roster.by_id.get(roster.resolve(n))
            for c in claims_unverified(s(a.get("text")), rec):
                errs.append(f"achievements[{i}]「{clip(c, 30)}」：快照里没有安装 / 验收记录，只能写未核实")
            m = PR_COUNT.search(s(a.get("text")) or "")
            prs = ((rec or {}).get("yesterday_delta") or {}).get("merged_prs")
            if m and rec is not None and len(split_names(s(a.get("project")))) == 1 and \
                    (prs is None or int(m.group(1)) != len(prs)):
                errs.append(f"achievements[{i}] 写合并 {m.group(1)} 个 PR，快照是 {num(None if prs is None else len(prs))}")
    if s(card.get("headline")):
        for c in claims_unverified(card["headline"], None):
            errs.append(f"headline「{clip(c, 30)}」：没有安装 / 验收记录")
    pos = section_positions(report)
    if pos[1] >= 0 and pos[2] > pos[1]:          # 报告第二段（昨日关键成果）逐行按行首项目名查
        for line in report[pos[1]:pos[2]].splitlines()[1:]:
            m = re.match(r"^\s*(?:\d+[.、]|[-*])\s*\**([^：:*]+?)\**\s*[：:]", line)
            pid = roster.resolve(m.group(1).strip()) if m else None
            if pid and pid != "hermes":
                for c in claims_unverified(line, roster.by_id.get(pid)):
                    errs.append(f"完整报告二段「{clip(c, 30)}」：快照里没有安装 / 验收记录")

    # 完整报告
    for name, p in zip(SECTIONS, pos):
        if p < 0:
            errs.append(f"完整报告缺「## {name}」")
    found = [p for p in pos if p >= 0]
    if found != sorted(found):
        errs.append("完整报告七段顺序不对")
    plain = report.replace("*", "")
    m = re.search(r"实质推进\s*(\S+?)\s*个项目\s*[｜|]\s*待你决策\s*(\S+?)\s*件\s*[｜|]\s*阻塞\s*/\s*风险\s*(\S+?)\s*个项目", plain)
    want = header_numbers(card)
    if not m:
        errs.append("完整报告开头缺「实质推进 N 个项目｜待你决策 N 件｜阻塞 / 风险 N 个项目」")
    elif m.groups() != want:
        errs.append(f"完整报告开头三个数 {'/'.join(m.groups())} 和卡片 {'/'.join(want)} 对不上")
    if pos[5] >= 0 and pos[6] > pos[5]:
        items6 = re.findall(r"^(?:\d+[.、]|[-*])\s", report[pos[5]:pos[6]], re.M)   # 只数顶格条目
        if len(items6) > REPORT_TODAY_MAX:
            errs.append(f"今日推进计划最多 {REPORT_TODAY_MAX} 项，写了 {len(items6)} 项")
    if (facts.get("activity") or {}).get("merged_prs") is None and re.search(r"合并\s*0\s*个|0\s*个\s*PR", plain):
        errs.append("合并数取不到，报告里不能写 0")
    cardtext = json.dumps(card, ensure_ascii=False)
    for where, text in (("卡片", cardtext), ("完整报告", report)):
        badq = sorted({f"{r}#{n}" for r, n in QREF.findall(text) if f"{r}#{n}" not in qual})
        badb = sorted({n for n in REF.findall(text) if n not in bare}, key=int)
        if badq or badb:
            errs.append(f"{where}里的编号 {'、'.join(badq + ['#' + b for b in badb])} 在材料里找不到")
        have = set(PCT.findall(dump))
        badp = sorted({p for p in PCT.findall(text) if p not in have})
        if badp:
            errs.append(f"{where}里的百分比 {'、'.join(p + '%' for p in badp)} 在材料里找不到（不编完成百分比）")
    return errs


# ---------- 事实版 ----------

def iso_deadline(v):
    """快照里的 ISO 时间 → 卡片写法 MM-DD HH:MM；不是时间就原样不显示。"""
    try:
        t = pdaily_data.parse_ts(v)
    except (TypeError, ValueError, AttributeError):
        return None
    return f"{t:%m-%d %H:%M}" if t else None


def latest_pr(prs):
    return max(prs, key=lambda x: x.get("merged_at") or "")


def fallback(facts, reason):
    """模型不可用或核对不过时的事实版：只摘快照，不判断；带出处，标「分析降级」。返回 (卡片要素, 完整报告)。"""
    roster = Roster(facts)
    name = roster.name
    decs = [{"type": None, "project": name(d.get("project_id")), "item": clip(d.get("item"), 60),
             "recommendation": None, "reply": None,
             "deadline": iso_deadline(d.get("deadline")),
             "source": d.get("source"), "evidence": (d.get("evidence") or [])[:2]}
            for d in facts.get("decisions") or []]
    ach = []
    for p in facts.get("projects") or []:
        prs = (p.get("yesterday_delta") or {}).get("merged_prs") or []
        if prs:
            last = latest_pr(prs)
            ach.append((len(prs), {"project": name(p.get("project_id")),
                                   "text": f"合并 {len(prs)} 个 PR（最近：#{last.get('number')} {clip(last.get('title'), 30)}）；交付、验收未核实"}))
    ach = [x for _, x in sorted(ach, key=lambda x: -x[0])]
    risks = [{"level": r.get("level") if r.get("level") in LEVELS else "待识别", "category": r.get("category") or "待分类",
              "project": name(r.get("project_id")), "text": clip(r.get("item"), 60), "evidence": r.get("evidence") or []}
             for r in facts.get("risks") or []]
    card = {"headline": f"分析降级：{reason}。以下只摘事实，没有判断和推荐", "progress_projects": None,
            "decisions": decs, "achievements": ach, "risks": risks, "today": [], "degraded": reason}
    unknown = delta_unknown(facts)
    src = lambda x: f"（出处：{'；'.join(str(e) for e in ([x.get('source')] if x.get('source') else []) + list(x.get('evidence') or []))}）" \
        if x.get("source") or x.get("evidence") else ""
    lines = [f"⚠️ 分析降级：{reason}。本报告只摘事实快照，没有定级、排序和推荐；拍板类型、风险等级都未判断。", ""]
    lines.append(f"实质推进 未知 个项目｜待你决策 {len(decs)} 件｜阻塞 / 风险 {len(risk_projects(card))} 个项目")
    lines += ["", "## 一、需要你拍板（候选，未判断）"] + ([f"- {d['project']}：{d['item']}{src(d)}" for d in decs] or ["- 无"])
    empty2 = f"- 未知：{len(unknown)} 个项目 GitHub 取不到（{'、'.join(name(u) for u in unknown)}）" if unknown else "- 昨天没有合并"
    lines += ["", "## 二、昨日关键成果（只列合并，交付、验收未核实）"] + [f"- {a['project']}：{a['text']}" for a in ach]
    if not ach or unknown:
        lines.append(empty2)
    lines += ["", "## 三、各项目推进"]
    for p in facts.get("projects") or []:
        st = p.get("current_state") or {}
        obj = p.get("objective")
        obj = (obj.get("text") if isinstance(obj, dict) else obj) or "未设目标"
        d = p.get("yesterday_delta") or {}
        prs, commits = d.get("merged_prs"), d.get("commits")
        inc = f"合并 {num(None if prs is None else len(prs))}、提交 {num(None if commits is None else sum(commits.values()))}"
        nxt = clip(st.get("next_step"), 60) if st.get("next_reliable") and st.get("next_step") else "未知（CURRENT 不可信或没写）"
        lines.append(f"- {name(p.get('project_id'))}：目标 {clip(obj, 60)}｜昨日 {inc}"
                     f"｜当前 {clip(st.get('top_section'), 60) if st.get('top_section') else '未知'}｜下一步 {nxt}")
    lines += ["", "## 四、风险、阻塞与跨项目依赖（候选，未定级）"] + \
        ([f"- {r['category']}｜{r['project']}：{r['text']}{src(r)}" for r in risks] or ["- 无"])
    v, n = action_counts(facts)
    lines += ["", "## 五、Hermes 代理述职", f"- 共 {v or '未知'} 条：{n}（明细在本地完整日报）"]
    lines += ["", "## 六、今日推进计划", "- 分析降级，没有排今日计划；请看上面的拍板候选和风险。"]
    lines += ["", "## 七、机制改进与沉淀", "- 无（降级时不提沉淀提案）"]
    lines += ["", "## 覆盖与可信度", coverage_line(facts)]
    return card, "\n".join(lines)


def coverage_line(facts):
    cov = facts.get("coverage") or {}
    proj = cov.get("projects") or {}
    bad = [k for k, v in (cov.get("actions") or {}).items() if not (v or {}).get("ok")]
    act = facts.get("activity") or {}
    parts = [f"项目读到 {proj.get('read_ok', '未知')} / {proj.get('projects', '未知')}"]
    if proj.get("errors"):
        parts.append("没读到：" + "、".join(proj["errors"]))
    parts.append("行动来源取不到：" + ("、".join(bad) if bad else "无"))
    if cov.get("stale"):
        parts.append("CURRENT 过期：" + "、".join(cov["stale"]))
    if cov.get("failed_parts"):
        parts.append("算不出来：" + "、".join(cov["failed_parts"]))
    if cov.get("not_connected"):
        parts.append("没接：" + "；".join(cov["not_connected"]))
    parts.append(f"合并 PR {num(act.get('merged_prs'))}、关闭 issue {num(act.get('closed_issues'))}、提交 {num(act.get('commits'))}")
    return "；".join(parts)


# ---------- 卡片 ----------

def short_day(day):
    d = dt.date.fromisoformat(day)
    return f"{d.month:02d}-{d.day:02d}（周{WEEK[d.weekday()]}）"


def window_text(facts):
    w = facts.get("window") or {}
    try:
        st, en = (pdaily_data.parse_ts(w[k]) for k in ("start", "end"))
    except (KeyError, TypeError, ValueError):
        return "窗口未知"
    return f"{st:%m-%d %H:%M} → {en:%m-%d %H:%M}"


def md(text):
    return {"tag": "markdown", "content": text}


def section(title, items, empty, total):
    body = "\n".join(items) if items else empty
    if total > len(items):
        body += f"\n<font color='grey'>共 {total} 条，另 {total - len(items)} 条见完整报告</font>"
    return [{"tag": "hr"}, md(f"**{title}**"), md(body)]


def sort_decisions(decs):
    return sorted(decs, key=lambda d: DECISION_TYPES.index(d["type"]) if d.get("type") in DECISION_TYPES else 9)


def sort_risks(risks):
    order = list(LEVELS)
    return sorted(risks, key=lambda r: order.index(r["level"]) if r.get("level") in order else 9)


def dline(d):
    """拍板一行，照样例：标签｜**项目·事项**：推荐。最晚 **时间**，回「…」。"""
    t = d.get("type")
    tag = f"<font color='red'>**{t}**</font>" if t in ("阻断", "限时") else f"<font color='grey'>{t or '候选'}</font>"
    line = f"{tag}｜**{d.get('project')}·{d.get('item')}**"
    if d.get("recommendation"):
        line += f"：{d['recommendation']}"
    tail = []
    if d.get("deadline"):
        tail.append(f"最晚 **{d['deadline']}**")
    if d.get("reply"):
        tail.append(f"回「{d['reply']}」")
    return line + ("。" + "，".join(tail) if tail else "")


def build_card(facts, card, trace, doc_url=None, doc_error=None, selfcheck=None):
    """甲版卡片 JSON 2.0（4.0），排版和样例 hermes/samples/pdaily-card-2026-11-08.json 一致；默认宽度。
    selfcheck：工程自检结果（#159）。None 不显示（预览）；"" 表示正常，只在底部灰字写一句；有内容就在
    「风险与阻塞」后面加一块「🔧 工程自检」。"""
    sub = [f"统计日 {window_text(facts)}"]
    if unread(facts):
        sub.append(f"{len(unread(facts))} 个项目未读到")
    if card.get("degraded"):
        sub.append("分析降级")
    rows = [{"k": k, "v": v, "n": n} for k, v, n in metrics(facts, card)]
    # 常浩 2026-11-10 定稿（DigitalTwin/daily/2026-11-09.card.restored.json）：「昨日」列收窄、「说明」列加宽，自动行高、换行不溢出
    els = [md("**📈 核心数据速览**"), {
        "tag": "table", "page_size": 10, "row_height": "auto", "row_max_height": "999px",
        "header_style": {"text_align": "left", "text_size": "normal", "background_style": "grey", "bold": True, "lines": 1},
        "columns": [{"name": k, "display_name": n, "data_type": "lark_md", "width": w, "vertical_align": "top"}
                    for k, n, w in (("k", "指标", "25%"), ("v", "昨日", "15%"), ("n", "说明", "60%"))],
        "rows": rows}]
    decs = sort_decisions([d for d in card.get("decisions") or [] if isinstance(d, dict)])
    els += section("🎯 需要你拍板", [dline(d) for d in decs[:CARD_MAX]], "今天没有要你拍板的事", len(decs))
    ach = [a for a in card.get("achievements") or [] if isinstance(a, dict)]
    unknown = delta_unknown(facts)
    empty = f"未知：{len(unknown)} 个项目 GitHub 取不到，没法判断昨天有没有成果" if unknown else "昨天没有新成果"
    els += section("✅ 昨日关键成果", [f"{i}. **{a.get('project')}**：{a.get('text')}" for i, a in enumerate(ach[:CARD_MAX], 1)],
                   empty, len(ach))
    risks = sort_risks([r for r in card.get("risks") or [] if isinstance(r, dict)])
    els += section("⚠️ 风险与阻塞", [f"{LEVELS.get(r.get('level'), '⚪')} **{r.get('category') or '待分类'}｜{r.get('project')}**：{r.get('text')}"
                                   for r in risks[:CARD_MAX]], "没有风险和阻塞", len(risks))
    if selfcheck:
        els += section("🔧 工程自检", [selfcheck], "", 1)
    today = sorted([t for t in card.get("today") or [] if isinstance(t, dict)],
                   key=lambda t: PRIORITIES.index(t["priority"]) if t.get("priority") in PRIORITIES else 9)
    els += section("💡 今日推进", [f"{i}. **{t.get('priority')} {t.get('item')}**：{t.get('owner_action')}"
                                 for i, t in enumerate(today[:TODAY_MAX], 1)],
                   "分析降级，没有排今日计划" if card.get("degraded") else "今天没有排推进事项", len(today))
    els.append({"tag": "hr"})
    if doc_url:
        els.append({"tag": "button", "text": {"tag": "plain_text", "content": "📄 查看完整报告"},
                    "type": "primary_filled", "size": "medium",
                    "behaviors": [{"type": "open_url", "default_url": doc_url, "pc_url": doc_url,
                                   "ios_url": doc_url, "android_url": doc_url}]})
    else:
        trace += f"｜完整报告未生成：{doc_error or '未知原因'}"
    if selfcheck == "":
        trace += "｜工程自检：正常"
    els.append({"tag": "markdown", "content": f"<font color='grey'>{trace}</font>", "text_size": "notation"})
    return {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": f"📅 猛鬼兽日报 · {short_day(facts['stat_day'])}"},
                       "subtitle": {"tag": "plain_text", "content": "｜".join(sub)}, "template": "blue"},
            "body": {"elements": els}}


def failure_card(day, reason):
    """取数或生成整体失败时的最小卡片（7.3：失败也要留证据、让常浩知道）。"""
    return {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": f"📅 猛鬼兽日报 · {short_day(day)}"},
                       "subtitle": {"tag": "plain_text", "content": "今天的日报没出成"}, "template": "red"},
            "body": {"elements": [md(f"**⚠️ 日报没出成**\n{reason}\n今天的项目情况请直接问我，或等修好后补发。"),
                                  {"tag": "markdown", "content": "<font color='grey'>生成：本机脚本｜接入：本机脚本</font>",
                                   "text_size": "notation"}]}}


def who(model, degraded):
    return f"生成：脚本事实版（{degraded}）｜接入：本机脚本" if degraded else \
        f"生成：Hermes（{model or '模型查不到'}）｜接入：Hermes 派单"


def fetched_text(facts):
    try:
        return f"取数 {pdaily_data.parse_ts(facts['fetched_at']):%m-%d %H:%M}"
    except (KeyError, TypeError, ValueError):
        return "取数时间未知"


def trace_line(facts, model, degraded):
    """卡片底部灰字：生成方｜接入｜统计日｜取数（4.0 样例的顺序）。"""
    return f"{who(model, degraded)}｜统计日 {window_text(facts)}｜{fetched_text(facts)}"


def report_doc(facts, report, model, degraded):
    """完整报告全文：标题 + 留痕（第 4 节样例顺序：生成｜接入｜取数｜统计日）+ 正文。"""
    return (f"# 📅 猛鬼兽日报｜统计日 {short_day(facts['stat_day'])}\n\n"
            f"{who(model, degraded)}｜{fetched_text(facts)}｜统计日 {window_text(facts)}\n\n{report.strip()}\n")


# ---------- 入口 ----------

def load_facts(path, day):
    """读事实快照；没给路径时，文件不在、或是统计日结束前取的（不完整），就重取一份。"""
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    f = DAILY / f"{day}.facts.json"
    facts = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    if facts is not None:
        try:
            done = pdaily_data.parse_ts(facts["fetched_at"]) >= pdaily_data.parse_ts(facts["window"]["end"])
        except (KeyError, TypeError, ValueError):
            done = False
        if done:
            return facts
    projects = json.loads(pdaily_facts.progress_data.ROSTER.read_text())["projects"]
    facts = pdaily_facts.build(day, projects)
    pdaily_facts.write(facts)
    return facts


def build(facts, out, reply=None, force_fallback=False):
    """出一天的卡片和完整报告（不发送）。返回 build 摘要 dict。任何一步出错都还有事实版，事实版也出错就出最小卡片。"""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    day = facts["stat_day"]
    meta = {"stat_day": day, "model": None, "session_id": None, "degraded": None, "check_errors": [], "warnings": []}
    card = report = None
    if force_fallback:
        meta["degraded"] = "按要求出事实版"
    else:
        try:
            if reply is None:
                reply, meta["session_id"], meta["model"] = generate(facts, out)
            (out / f"{day}.reply.md").write_text(reply, encoding="utf-8")
            card, report = parse_reply(reply)
            meta["warnings"] = normalize(facts, card)
            meta["check_errors"] = check(facts, card, report)
            if meta["check_errors"]:
                meta["degraded"] = f"模型稿核对没过 {len(meta['check_errors'])} 处"
        except GenError as e:
            meta["degraded"] = f"模型没写成：{e}"
        except Exception as e:  # noqa: BLE001  模型稿千奇百怪，任何意外都转事实版，不能让日报出不来
            meta["degraded"] = f"模型稿处理出错（{type(e).__name__}）"
    try:
        if meta["degraded"]:
            card, report = fallback(facts, meta["degraded"])
        preview = build_card(facts, card, trace_line(facts, meta["model"], meta["degraded"]), doc_error="预览，未发送")
        doc = report_doc(facts, report, meta["model"], meta["degraded"])
    except Exception as e:  # noqa: BLE001
        meta["degraded"] = f"事实版也没出成（{type(e).__name__}：{clip(e, 80)}）"
        card, doc, preview = None, None, failure_card(day, meta["degraded"])
    if doc is not None:
        (out / f"{day}.report.md").write_text(doc, encoding="utf-8")
        (out / f"{day}.card-fields.json").write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / f"{day}.card.json").write_text(json.dumps(preview, ensure_ascii=False, indent=1), encoding="utf-8")
    meta["trace"] = trace_line(facts, meta["model"], meta["degraded"])
    (out / f"{day}.build.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


# ---------- 发送（#155、#159：每天 10:00 发常浩私聊，带工程自检） ----------

LARK = os.environ.get("DT_LARK_CLI", "lark-cli")
PROFILE = "cli_a96f5f9ac63adcd4"                     # 猛鬼兽应用（PRD D12）
AGENT_ENV = ("HERMES", "OPENCLAW", "LARK_CHANNEL")    # lark-cli 看到这些变量会改读另一套配置，调用前去掉（同 tidings_weekly.py）
CHAT = os.environ.get("DT_PDAILY_CHAT", "oc_ce2cbf195f8de9f80bc505a00cb4456d")   # 常浩和猛鬼兽的私聊
FOLDER = os.environ.get("DT_PDAILY_FOLDER", "MKmvfAZSvlby0sdDzwIcrXVZn2g")       # 完整报告云文档放这个文件夹（11-09 同）
LARK_TIMEOUT = 120
# 猛鬼兽只报个人项目（PRD D1、D19、D22；常浩 11-11「绝对不允许再出现」）。工作日报 / 周报的痕迹一律拒发：
# 台账编号、决定编号、工作台账文档、工作日报的段落标题。
WORK_MARKS = [(re.compile(r"\[A\d{4}-\d+\]"), "工作台账编号"), (re.compile(r"\[D-\d{8}-\d+\]"), "工作决定编号"),
              (re.compile(r"WZyhdQdM0oMk2jxSlOTcJPVKnQg|QYEhdzNgUof8mtxW0cqcYErgnDc"), "工作日报 / 周报文档"),
              (re.compile(r"还开着的事（\d+ 件）|今天的变化|下个工作日先推"), "工作日报的段落")]


class SendError(Exception):
    """建文档、发卡片、回读出错。原因不含凭据。"""


def lark(args, what, identity, cwd=None):
    """跑一条 lark-cli 命令，返回 data；出错抛 SendError。"""
    env = {k: v for k, v in os.environ.items() if not k.lstrip("_").startswith(AGENT_ENV)}
    cmd = [LARK, "--profile", PROFILE, *args, "--as", identity, "--format", "json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=LARK_TIMEOUT, env=env, cwd=cwd)
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
        raise SendError(f"{what}失败：{clip((d.get('error') or {}).get('message', '未知错误'), 200)}")
    return d.get("data") or {}


SELF_CHECK = Path(os.environ.get("DT_SELF_CHECK", Path(__file__).with_name("self_check.py")))
SELF_CHECK_TIMEOUT = 900   # 每月 1 号要查名册各仓库，self_check 里这一项最多 600 秒


def run_self_check():
    """跑工程自检（原 09:50 单独发，#159 起并进日报）。返回 ""＝正常；有问题返回要显示的正文；没跑成也返回一句说明。"""
    try:
        r = subprocess.run([sys.executable, str(SELF_CHECK)], capture_output=True, text=True, timeout=SELF_CHECK_TIMEOUT,
                           cwd=str(SELF_CHECK.parent))
    except (OSError, subprocess.SubprocessError) as e:
        return f"⚠️ 自检没跑成（{type(e).__name__}），今天没有自检结果"
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()
        return f"⚠️ 自检没跑成（退出码 {r.returncode}：{clip(tail[-1] if tail else '', 120)}），今天没有自检结果"
    out = r.stdout.strip()
    return re.sub(r"^🔧 工程自检\s*", "", out).strip()


def work_content(text):
    """返回卡片 / 报告里出现的工作内容痕迹（说明列表），空表示干净。"""
    return [why for pat, why in WORK_MARKS if pat.search(text)]


def create_doc(day, doc_text, out):
    """完整报告建成云文档（用常浩的用户身份，放在 FOLDER），返回 URL。"""
    f = Path(out) / f"{day}.report.md"
    f.write_text(doc_text, encoding="utf-8")
    d = dt.date.fromisoformat(day)
    data = lark(["docs", "+create", "--parent-token", FOLDER, "--doc-format", "markdown",
                 "--title", f"猛鬼兽个人项目日报 · {day}（周{WEEK[d.weekday()]}）", "--content", f"@{f.name}"],
                "建完整报告文档", "user", cwd=str(out))
    url = (data.get("document") or {}).get("url")
    if not url:
        raise SendError("建完整报告文档：返回里没有文档地址")
    return url


def send(day, out, force=False):
    """把这一天的日报发到常浩私聊：建完整报告文档 → 按甲版出卡片 → 拦工作内容 → 发 → 回读 → 记 delivery.json。
    卡片只从本地事实快照和 build 产物现做，不接受外来卡片。成功返回 None，失败返回原因（一句话）。"""
    out = Path(out)
    deliv = out / f"{day}.delivery.json"
    if deliv.exists() and not force:
        try:
            if json.loads(deliv.read_text(encoding="utf-8")).get("sent"):
                return None                      # 同一统计日只发一次（定时任务重跑、补跑都不重复发）
        except ValueError:
            pass
    rec = {"stat_day": day, "channel": CHAT, "sent": False, "at": None, "doc_url": None, "message_id": None,
           "read_back": False, "error": None}
    try:
        meta = json.loads((out / f"{day}.build.json").read_text(encoding="utf-8"))
        ff = out / f"{day}.facts.json"
        facts = json.loads((ff if ff.exists() else DAILY / f"{day}.facts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta, facts = None, None
    if meta is None or facts is None or not (out / f"{day}.card-fields.json").exists():
        card, doc_text = failure_card(day, "09:00 的写稿没跑成，没有可发的日报"), None
    else:
        fields = json.loads((out / f"{day}.card-fields.json").read_text(encoding="utf-8"))
        doc_text = (out / f"{day}.report.md").read_text(encoding="utf-8")
        card = None
    doc_url = doc_error = None
    if doc_text is not None:
        bad = work_content(doc_text)
        if bad:
            rec["error"] = f"完整报告里有工作内容（{'、'.join(bad)}），拒发"
            deliv.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
            return rec["error"]
        try:
            doc_url = create_doc(day, doc_text, out)
        except SendError as e:
            doc_error = str(e)
        card = build_card(facts, fields, meta.get("trace") or trace_line(facts, meta.get("model"), meta.get("degraded")),
                          doc_url=doc_url, doc_error=doc_error, selfcheck=run_self_check())
    text = json.dumps(card, ensure_ascii=False)
    bad = work_content(text)
    if bad or not card["header"]["title"]["content"].startswith("📅 猛鬼兽日报 · "):
        rec["error"] = f"卡片里有工作内容（{'、'.join(bad) or '标题不对'}），拒发"
        deliv.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return rec["error"]
    (out / f"{day}.card.sent.json").write_text(text, encoding="utf-8")
    rec["doc_url"], rec["doc_error"] = doc_url, doc_error
    try:
        data = lark(["im", "+messages-send", "--chat-id", CHAT, "--msg-type", "interactive", "--content", text,
                     "--idempotency-key", f"pdaily-{day}" + ("-r" if force else "")], "发卡片", "bot")
        rec["message_id"], rec["sent"] = data.get("message_id"), bool(data.get("message_id"))
        rec["at"] = dt.datetime.now(pdaily_data.TZ).isoformat(timespec="seconds")
        if rec["sent"]:
            got = lark(["im", "+messages-mget", "--message-ids", rec["message_id"]], "回读", "bot")
            m = (got.get("messages") or [{}])[0]
            rec["read_back"] = m.get("msg_type") == "interactive" and card["header"]["title"]["content"] in str(m.get("content"))
    except SendError as e:
        rec["error"] = str(e)
    deliv.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    if not rec["sent"]:
        return rec["error"] or "发卡片没拿到消息号"
    if not rec["read_back"]:
        return f"卡片已发（{rec['message_id']}），但回读对不上标题"
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description="个人项目日报写稿、核对与发送")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("build", "material", "send"):
        sp = sub.add_parser(name)
        sp.add_argument("--date", help="统计日 YYYY-MM-DD，默认最近一个已经结束的统计日")
        if name != "send":
            sp.add_argument("--facts", help="事实快照文件，默认 DigitalTwin/daily/<统计日>.facts.json")
    b = sub.choices["build"]
    b.add_argument("--reply", help="用现成的模型回复文件（回放、测试用）")
    b.add_argument("--fallback", action="store_true", help="直接出事实版")
    b.add_argument("--quiet", action="store_true", help="成功不输出（定时任务用）")
    for name in ("build", "send"):
        sub.choices[name].add_argument("--out", help="输出目录，默认 DigitalTwin/daily/")
    sub.choices["send"].add_argument("--force", action="store_true", help="这一天已经发过也再发一次")
    a = p.parse_args(argv)
    try:
        day = dt.date.fromisoformat(a.date) if a.date else pdaily_data.default_day()
    except ValueError:
        print(json.dumps({"ok": False, "error": f"日期写法不对：{a.date}"}, ensure_ascii=False))
        return 1
    if a.cmd == "send":   # 定时任务直接把输出发到私聊：成功不输出，失败说一句原因
        err = send(day.isoformat(), a.out or DAILY, force=a.force)
        if err:
            print(f"⚠️ 今天的个人项目日报没发成：{err}。详情在 DigitalTwin/daily/{day.isoformat()}.delivery.json")
        return 0
    try:
        facts = load_facts(a.facts, day)
    except Exception as e:  # noqa: BLE001  取数失败也要留一张卡片和原因（7.3）
        reason = f"取数失败（{type(e).__name__}：{clip(e, 120)}）"
        if a.cmd == "build":
            out = Path(a.out or DAILY)
            out.mkdir(parents=True, exist_ok=True)
            (out / f"{day}.card.json").write_text(json.dumps(failure_card(day.isoformat(), reason), ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
        print(json.dumps({"ok": False, "stat_day": day.isoformat(), "error": reason}, ensure_ascii=False))
        return 1
    if a.cmd == "material":
        print(json.dumps(material(facts), ensure_ascii=False, indent=1))
        return 0
    reply = Path(a.reply).read_text(encoding="utf-8") if a.reply else None
    meta = build(facts, a.out or DAILY, reply=reply, force_fallback=a.fallback)
    if not a.quiet:
        print(json.dumps({"ok": True, **meta}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
