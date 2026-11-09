"""个人项目日报写稿与核对（#111 批 2a）：读 1c 的事实快照，让 Hermes 写卡片要素和完整报告，脚本核对后
按甲版生成飞书卡片 JSON；模型失败或核对不过就出事实版（标「分析降级」）。本批只生成，不发送。

规格：docs/个人项目日报-V2-设计.md 第 3 节（篇幅、留痕）、4.0（卡片）、4.1–4.9（完整报告）、第 6 节（证据）、7.1。
用法：
  python3 pdaily_publish.py build [--date 统计日] [--facts 快照] [--reply 模型回复文件] [--fallback] [--out 目录]
      默认读 DigitalTwin/daily/<统计日>.facts.json（没有就先跑 pdaily_facts 生成），调 Hermes 单次写稿，
      写出 <统计日>.card.json、<统计日>.report.md、<统计日>.build.json，最后打印一行 JSON 摘要。
      --reply 用现成的模型回复（回放、测试用），--fallback 直接出事实版。
  python3 pdaily_publish.py material [--date 统计日] [--facts 快照]   只打印交给模型的压缩材料
分工：能从快照算出来的数（合并 PR、代理动作、覆盖）由脚本算；判断（什么算实质推进、拍板类型、风险等级、推荐、
今日计划）由模型写；脚本只核对格式、数字一致、出处存在，不替模型判断。
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
CARD_MAX = 5          # 卡片每块最多几条（第 3 节「篇幅」）
TODAY_MAX = 3         # 今日推进卡片最多 3 条（4.0）
REPORT_TODAY_MAX = 5  # 完整报告今日计划最多 5 项（4.7）
CLIP = 300            # 材料里每段原文最多保留多少字
ACT_CLIP = 100        # 行动记录每个字段最多保留多少字（行动多，压短些）
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
REF = re.compile(r"(?<![\w&/])#(\d+)")
CARD_MARK, REPORT_MARK = "<<<CARD>>>", "<<<REPORT>>>"


class GenError(Exception):
    """写稿没成：调用失败、回复格式不对。原因进留痕行和 build.json，不含凭据。"""


# ---------- 材料 ----------

def clip(s, n=CLIP):
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + "…"


def material(facts):
    """压缩给模型的材料：定时任务按结果合并成计数（失败的逐条留着），长原文截断；判断要用的字段都留着。"""
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
            key = f"{a.get('result')}｜{clip(a.get('verification'), 20)}"
            routine[key] = routine.get(key, 0) + 1
            continue
        actions.append({k: (clip(v, ACT_CLIP) if isinstance(v, str) else v) for k, v in a.items() if k != "action_id"})
    return {
        "stat_day": facts.get("stat_day"), "window": facts.get("window"), "fetched_at": facts.get("fetched_at"),
        "projects": projects,
        "actions": actions, "routine_cron": routine,
        "decisions": facts.get("decisions"), "risks": facts.get("risks"), "cross_refs": facts.get("cross_refs"),
        "observations": facts.get("observations"), "counts": facts.get("counts"),
        "activity": facts.get("activity"), "coverage": facts.get("coverage"), "notes": facts.get("notes"),
    }


# ---------- 写稿 ----------

def query(facts):
    return PROMPT.read_text(encoding="utf-8") + "\n\n## 材料\n\n```json\n" + \
        json.dumps(material(facts), ensure_ascii=False, separators=(",", ":")) + "\n```\n"


def session_model(session_id):
    """这次写稿会话实际用过的主模型（含回退，按首次使用排），查不到返回 None。"""
    try:
        con = sqlite3.connect(HERMES_HOME / "state.db", timeout=10)
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
           "--max-turns", "3", "--run-budget", str(BUDGET)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=BUDGET + 120, cwd=str(workdir))
    except (OSError, subprocess.SubprocessError) as e:
        raise GenError(f"Hermes 跑不起来（{type(e).__name__}）")
    sid = (re.findall(r"session_id:\s*(\S+)", r.stderr + r.stdout) or [None])[-1]
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()
        raise GenError(f"Hermes 退出码 {r.returncode}：{clip(tail[-1] if tail else '', 120)}")
    out = re.sub(r"\n?session_id:\s*\S+\s*$", "", r.stdout.strip())
    return out, sid, session_model(sid) if sid else None


def parse_reply(text):
    """拆成 (卡片要素 dict, 完整报告 Markdown)。格式不对抛 GenError。"""
    if CARD_MARK not in text or REPORT_MARK not in text:
        raise GenError("回复里没有 <<<CARD>>> / <<<REPORT>>> 分隔")
    card_part, report = text.split(CARD_MARK, 1)[1].split(REPORT_MARK, 1)
    card_part = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", card_part.strip())
    try:
        card = json.loads(card_part)
    except ValueError as e:
        raise GenError(f"卡片要素不是合法 JSON（{e.msg}，第 {e.lineno} 行）")
    if not isinstance(card, dict):
        raise GenError("卡片要素不是 JSON 对象")
    return card, report.strip()


# ---------- 算数 ----------

def roster_size(facts):
    cov = (facts.get("coverage") or {}).get("projects") or {}
    return cov.get("projects", len(facts.get("projects") or []))


def unread(facts):
    return list(((facts.get("coverage") or {}).get("projects") or {}).get("errors") or {})


def num(v):
    return "未知" if v is None else str(v)


def action_counts(facts):
    """代理动作：定时任务单列，其余按类别计数；有来源取不到就整体记「未知」并写明。"""
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
        note += f"；{'、'.join(bad)}取不到"
        return f"{sum(main.values())}+", note
    return str(sum(main.values())), note


def risk_projects(card):
    return sorted({r.get("project") for r in card.get("risks") or [] if r.get("project")})


def metrics(facts, card):
    """核心数据速览五行（4.0）。"""
    n = roster_size(facts)
    progress = card.get("progress_projects")
    other = n - len(progress) if isinstance(progress, list) else None
    act_v, act_n = action_counts(facts)
    dec = card.get("decisions") or []
    by_type = [f"{t} {sum(1 for d in dec if d.get('type') == t)}" for t in DECISION_TYPES
               if any(d.get("type") == t for d in dec)]
    deadlines = sorted(d["deadline"] for d in dec if d.get("deadline"))
    risks = card.get("risks") or []
    cats = [f"{c.replace('风险', '')} {sum(1 for r in risks if r.get('category') == c)}"
            for c in CATEGORIES if any(r.get("category") == c for r in risks)]
    merged = (facts.get("activity") or {}).get("merged_prs")
    rows = [
        ("有推进的项目", "未知" if progress is None else f"**{len(progress)}** / {n}",
         card.get("progress_note") or ("" if other is None else f"其余 {other} 个无实质推进")),
        ("合并 PR", num(merged), "统计日 04:00→04:00" if merged is not None else "GitHub 取不到"),
        ("待你拍板", f"<font color='red'>**{len(dec)}**</font>" if dec else "0",
         ("、".join(by_type) or ("类型未判断" if dec else "")) + (f"，最晚 {deadlines[0]}" if deadlines else "")),
        ("风险 / 阻塞", f"{len(risk_projects(card))} 个项目", "、".join(cats)),
        ("Hermes 代理动作", act_v or "未知", act_n),
    ]
    return rows


def header_numbers(facts, card):
    """完整报告开头三个数（4.1），和卡片同源。"""
    progress = card.get("progress_projects")
    return ("未知" if progress is None else str(len(progress)), str(len(card.get("decisions") or [])),
            str(len(risk_projects(card))))


# ---------- 核对 ----------

def facts_refs(facts):
    """快照里出现过的编号：PR、issue、原文里写的 #N。"""
    dump = json.dumps(facts, ensure_ascii=False)
    refs = set(REF.findall(dump))
    refs |= {str(m) for m in re.findall(r'"(?:number|to_number)": (\d+)', dump)}
    return refs, dump


def names_ok(facts):
    ok = set(SELF_NAMES)
    for p in facts.get("projects") or []:
        ok.update(x for x in (p.get("project_id"), p.get("name")) if x)
    return ok


def split_names(s):
    return [x.strip() for x in re.split(r"[↔、,，/]| 和 ", s or "") if x.strip()]


def deadline_ok(deadline, dump):
    """最晚时间要有出处：快照里能找到同一个日期时间。写法「MM-DD HH:MM」或「HH:MM」。"""
    m = re.fullmatch(r"(?:(\d{1,2})-(\d{1,2}) )?(\d{1,2}):(\d{2})", deadline.strip())
    if not m:
        return False
    mo, d, h, mi = m.groups()
    hm = f"{int(h):02d}:{mi}"
    if not mo:
        return hm in dump
    md = f"{int(mo):02d}-{int(d):02d}"
    return f"{md}T{hm}" in dump or f"{md} {hm}" in dump or f"{int(mo)}-{int(d)} {hm}" in dump


def check(facts, card, report):
    """返回问题清单，空表示通过。只查格式、数字一致、出处存在，不评判写得好不好。"""
    errs = []
    refs, dump = facts_refs(facts)
    names = names_ok(facts)
    ids = {p.get("project_id") for p in facts.get("projects") or []}

    def need_list(key, fields, allowed=None):
        v = card.get(key)
        if not isinstance(v, list):
            errs.append(f"卡片要素缺 {key}（要一个列表，没有就给空列表）")
            return []
        for i, x in enumerate(v):
            if not isinstance(x, dict):
                errs.append(f"{key}[{i}] 不是对象")
                continue
            for f in fields:
                if not isinstance(x.get(f), str) or not x.get(f).strip():
                    errs.append(f"{key}[{i}] 缺 {f}")
            for f, ok in (allowed or {}).items():
                if x.get(f) not in ok:
                    errs.append(f"{key}[{i}].{f}「{x.get(f)}」不在 {'/'.join(ok)} 里")
            for n in split_names(x.get("project")):
                if n not in names:
                    errs.append(f"{key}[{i}] 的项目「{n}」不在名册里")
        return [x for x in v if isinstance(x, dict)]

    if not isinstance(card.get("headline"), str) or not card["headline"].strip():
        errs.append("卡片要素缺 headline（一句话）")
    progress = card.get("progress_projects")
    if not isinstance(progress, list):
        errs.append("卡片要素缺 progress_projects（实质推进的项目 id 列表）")
    else:
        delta = set(((facts.get("counts") or {}).get("projects_with_delta")) or [])
        for p in progress:
            if p not in ids:
                errs.append(f"progress_projects 里的「{p}」不是名册项目 id")
            elif p not in delta:
                errs.append(f"progress_projects 里的「{p}」昨天没有任何增量，不能算实质推进")
    decs = need_list("decisions", ("project", "item", "recommendation", "reply"), {"type": DECISION_TYPES})
    for i, d in enumerate(decs):
        if d.get("deadline") and not deadline_ok(str(d["deadline"]), dump):
            errs.append(f"decisions[{i}] 的最晚时间「{d['deadline']}」在材料里找不到出处")
    need_list("achievements", ("project", "text"))
    need_list("risks", ("project", "text"), {"level": tuple(LEVELS), "category": CATEGORIES})
    today = need_list("today", ("item", "owner_action"), {"priority": PRIORITIES})
    if len(today) > TODAY_MAX:
        errs.append(f"今日推进卡片最多 {TODAY_MAX} 条，给了 {len(today)} 条")
    mentioned = set()
    for key in ("decisions", "achievements", "risks"):
        for x in card.get(key) or []:
            if isinstance(x, dict):
                mentioned.update(split_names(x.get("project")))
    for i, t in enumerate(today):
        text = f"{t.get('item', '')}{t.get('owner_action', '')}"
        if not any(n in text for n in mentioned):
            errs.append(f"today[{i}]「{clip(t.get('item'), 30)}」和拍板、成果、风险里的项目都对不上")

    # 完整报告
    pos = [report.find(s) for s in SECTIONS]
    for s, p in zip(SECTIONS, pos):
        if p < 0:
            errs.append(f"完整报告缺「{s}」")
    found = [p for p in pos if p >= 0]
    if found != sorted(found):
        errs.append("完整报告七段顺序不对")
    m = re.search(r"实质推进\s*(\S+?)\s*个项目｜待你决策\s*(\S+?)\s*件｜阻塞\s*/\s*风险\s*(\S+?)\s*个项目", report)
    want = header_numbers(facts, card)
    if not m:
        errs.append("完整报告开头缺「实质推进 N 个项目｜待你决策 N 件｜阻塞 / 风险 N 个项目」")
    elif m.groups() != want:
        errs.append(f"完整报告开头三个数 {'/'.join(m.groups())} 和卡片 {'/'.join(want)} 对不上")
    sec6 = report[pos[5]:pos[6]] if pos[5] >= 0 and pos[6] > pos[5] else ""
    items6 = re.findall(r"^\s*(?:\d+[.、]|[-*])\s", sec6, re.M)
    if len(items6) > REPORT_TODAY_MAX:
        errs.append(f"今日推进计划最多 {REPORT_TODAY_MAX} 项，写了 {len(items6)} 项")
    if (facts.get("activity") or {}).get("merged_prs") is None and re.search(r"合并\s*0\s*个|0\s*个\s*PR", report):
        errs.append("合并数取不到，报告里不能写 0")
    cardtext = json.dumps(card, ensure_ascii=False)
    for where, text in (("卡片", cardtext), ("完整报告", report)):
        bad = sorted({r for r in REF.findall(text) if r not in refs}, key=int)
        if bad:
            errs.append(f"{where}里的编号 {'、'.join('#' + b for b in bad)} 在材料里找不到")
    if re.search(r"\d+(?:\.\d+)?\s*%", cardtext + report) and not re.search(r"\d+(?:\.\d+)?\s*%", dump):
        errs.append("写了百分比，材料里没有任何百分比出处（不编完成百分比）")
    return errs


# ---------- 事实版 ----------

def fallback(facts, reason):
    """模型不可用或核对不过时的事实版：只摘快照，不判断；标「分析降级」。返回 (卡片要素, 完整报告)。"""
    names = {p.get("project_id"): p.get("name") or p.get("project_id") for p in facts.get("projects") or []}
    name = lambda pid: names.get(pid) or ("猛鬼兽" if pid in (None, "hermes") else pid)
    decs = [{"type": None, "project": name(d.get("project_id")), "item": clip(d.get("item"), 60),
             "recommendation": "未判断（分析降级）", "deadline": d.get("deadline"), "reply": None}
            for d in facts.get("decisions") or []]
    ach = []
    for p in facts.get("projects") or []:
        prs = (p.get("yesterday_delta") or {}).get("merged_prs") or []
        if prs:
            ach.append((len(prs), {"project": name(p.get("project_id")),
                                   "text": f"合并 {len(prs)} 个 PR（最近：#{prs[-1].get('number')} {clip(prs[-1].get('title'), 30)}）；交付、验收未核实"}))
    ach = [x for _, x in sorted(ach, key=lambda x: -x[0])]
    risks = [{"level": r.get("level") if r.get("level") in LEVELS else "待识别", "category": r.get("category"),
              "project": name(r.get("project_id")), "text": clip(r.get("item"), 60)} for r in facts.get("risks") or []]
    card = {"headline": f"分析降级：{reason}。以下只摘事实，没有判断和推荐",
            "progress_projects": None, "progress_note": "降级：没判断哪些算实质推进",
            "decisions": decs, "achievements": ach, "risks": risks, "today": [], "degraded": reason}
    lines = [f"⚠️ 分析降级：{reason}。本报告只摘事实快照，没有定级、排序和推荐；拍板类型、风险等级都未判断。", ""]
    lines.append(f"实质推进 未知 个项目｜待你决策 {len(decs)} 件｜阻塞 / 风险 {len(risk_projects(card))} 个项目")
    lines += ["", "## 一、需要你拍板（候选，未判断）"] + ([f"- {d['project']}：{d['item']}" for d in decs] or ["- 无"])
    lines += ["", "## 二、昨日关键成果（只列合并，交付、验收未核实）"] + ([f"- {a['project']}：{a['text']}" for a in ach] or ["- 无"])
    lines += ["", "## 三、各项目推进"]
    for p in facts.get("projects") or []:
        st = p.get("current_state") or {}
        obj = (p.get("objective") or {}).get("text") or "未设目标"
        lines.append(f"- {name(p.get('project_id'))}：目标 {clip(obj, 60)}｜当前 {clip(st.get('top_section'), 60)}"
                     f"｜下一步 {clip(st.get('next_step'), 60) if st.get('next_reliable') else '未知（CURRENT 不可信或没写）'}")
    lines += ["", "## 四、风险、阻塞与跨项目依赖（候选，未定级）"] + ([f"- {r['category']}｜{r['project']}：{r['text']}" for r in risks] or ["- 无"])
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
    parts.append(f"合并 PR {num(act.get('merged_prs'))}、关闭 issue {num(act.get('closed_issues'))}、提交 {num(act.get('commits'))}")
    return "；".join(parts)


# ---------- 卡片 ----------

def short_day(day):
    d = dt.date.fromisoformat(day)
    return f"{d.month:02d}-{d.day:02d}（周{WEEK[d.weekday()]}）"


def window_text(facts):
    w = facts.get("window") or {}
    s, e = (pdaily_data.parse_ts(w[k]) for k in ("start", "end"))
    return f"{s:%m-%d %H:%M} → {e:%m-%d %H:%M}"


def more(items, total):
    return f"\n<font color='grey'>另 {total - len(items)} 条见完整报告</font>" if total > len(items) else ""


def md(text):
    return {"tag": "markdown", "content": text}


def section(title, items, empty, total=None):
    body = "\n".join(items) if items else empty
    return [{"tag": "hr"}, md(f"**{title}**"), md(body + more(items, total if total is not None else len(items)))]


def sort_decisions(decs):
    return sorted(decs, key=lambda d: DECISION_TYPES.index(d["type"]) if d.get("type") in DECISION_TYPES else 9)


def sort_risks(risks):
    order = list(LEVELS)
    return sorted(risks, key=lambda r: order.index(r["level"]) if r.get("level") in order else 9)


def build_card(facts, card, trace, doc_url=None, doc_error=None):
    """甲版卡片 JSON 2.0（4.0），排版和样例 hermes/samples/pdaily-card-2026-11-08.json 一致；默认宽度。"""
    day = facts["stat_day"]
    sub = [f"统计日 {window_text(facts)}"]
    if unread(facts):
        sub.append(f"{len(unread(facts))} 个项目未读到")
    if card.get("degraded"):
        sub.append("分析降级")
    rows = [{"k": k, "v": v, "n": n} for k, v, n in metrics(facts, card)]
    els = [md("**📈 核心数据速览**"), {
        "tag": "table", "page_size": 10, "row_height": "low",
        "header_style": {"text_align": "left", "text_size": "normal", "background_style": "grey", "bold": True, "lines": 1},
        "columns": [{"name": k, "display_name": n, "data_type": "lark_md", "width": "auto"}
                    for k, n in (("k", "指标"), ("v", "昨日"), ("n", "说明"))],
        "rows": rows}]
    decs = sort_decisions(card.get("decisions") or [])
    def dline(d):
        t = d.get("type")
        tag = "<font color='red'>**" + t + "**</font>" if t in ("阻断", "限时") else f"<font color='grey'>{t or '待定'}</font>"
        s = f"{tag}｜**{d['project']}**：{d['item']}"
        if d.get("recommendation"):
            s += f"。推荐：{d['recommendation']}"
        if d.get("deadline"):
            s += f"。最晚 **{d['deadline']}**"
        if d.get("reply"):
            s += f"，回「{d['reply']}」"
        return s
    els += section("🎯 需要你拍板", [dline(d) for d in decs[:CARD_MAX]], "今天没有要你拍板的事", len(decs))
    ach = card.get("achievements") or []
    els += section("✅ 昨日关键成果", [f"{i}. **{a['project']}**：{a['text']}" for i, a in enumerate(ach[:CARD_MAX], 1)],
                   card.get("achievements_empty") or "昨天没有新成果", len(ach))
    risks = sort_risks(card.get("risks") or [])
    els += section("⚠️ 风险与阻塞", [f"{LEVELS.get(r.get('level'), '⚪')} **{r.get('category') or '待分类'}｜{r['project']}**：{r['text']}"
                                   for r in risks[:CARD_MAX]], "没有风险和阻塞", len(risks))
    today = card.get("today") or []
    els += section("💡 今日推进", [f"{i}. **{t['priority']} {t['item']}**：{t['owner_action']}" for i, t in enumerate(today[:TODAY_MAX], 1)],
                   "分析降级，没有排今日计划" if card.get("degraded") else "今天没有排推进事项", len(today))
    els.append({"tag": "hr"})
    if doc_url:
        els.append({"tag": "button", "text": {"tag": "plain_text", "content": "📄 查看完整报告"},
                    "type": "primary_filled", "size": "medium",
                    "behaviors": [{"type": "open_url", "default_url": doc_url, "pc_url": doc_url,
                                   "ios_url": doc_url, "android_url": doc_url}]})
    else:
        trace += f"｜完整报告未生成：{doc_error or '未知原因'}"
    els.append({"tag": "markdown", "content": f"<font color='grey'>{trace}</font>", "text_size": "notation"})
    return {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": f"📅 猛鬼兽日报 · {short_day(day)}"},
                       "subtitle": {"tag": "plain_text", "content": "｜".join(sub)}, "template": "blue"},
            "body": {"elements": els}}


def trace_line(facts, model, degraded):
    fetched = pdaily_data.parse_ts(facts["fetched_at"]) if facts.get("fetched_at") else None
    who = f"生成：脚本事实版（{degraded}）｜接入：本机脚本" if degraded else \
        f"生成：Hermes（{model or '模型查不到'}）｜接入：Hermes 派单"
    return f"{who}｜统计日 {window_text(facts)}｜取数 {f'{fetched:%m-%d %H:%M}' if fetched else '未知'}"


def report_doc(facts, card, report, trace):
    """完整报告全文：标题 + 留痕 + 模型（或事实版）正文。"""
    return f"# 📅 猛鬼兽日报｜统计日 {short_day(facts['stat_day'])}\n\n{trace}\n\n{report.strip()}\n"


# ---------- 入口 ----------

def load_facts(path, day):
    f = Path(path) if path else DAILY / f"{day}.facts.json"
    if not f.exists() and not path:
        projects = json.loads(pdaily_facts.progress_data.ROSTER.read_text())["projects"]
        pdaily_facts.write(pdaily_facts.build(day, projects))
    return json.loads(f.read_text(encoding="utf-8"))


def build(facts, out, reply=None, force_fallback=False):
    """出一天的卡片和完整报告（不发送）。返回 build 摘要 dict。"""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    day = facts["stat_day"]
    meta = {"stat_day": day, "model": None, "session_id": None, "degraded": None, "check_errors": []}
    card = report = None
    if force_fallback:
        meta["degraded"] = "按要求出事实版"
    else:
        try:
            if reply is None:
                reply, meta["session_id"], meta["model"] = generate(facts, out)
            (out / f"{day}.reply.md").write_text(reply, encoding="utf-8")
            card, report = parse_reply(reply)
            meta["check_errors"] = check(facts, card, report)
            if meta["check_errors"]:
                meta["degraded"] = f"模型稿核对没过 {len(meta['check_errors'])} 处"
        except GenError as e:
            meta["degraded"] = f"模型没写成：{e}"
    if meta["degraded"]:
        card, report = fallback(facts, meta["degraded"])
    trace = trace_line(facts, meta["model"], meta["degraded"])
    (out / f"{day}.report.md").write_text(report_doc(facts, card, report, trace), encoding="utf-8")
    (out / f"{day}.card-fields.json").write_text(json.dumps(card, ensure_ascii=False, indent=1), encoding="utf-8")
    preview = build_card(facts, card, trace, doc_error="预览，未发送")
    (out / f"{day}.card.json").write_text(json.dumps(preview, ensure_ascii=False, indent=1), encoding="utf-8")
    meta["trace"] = trace
    (out / f"{day}.build.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def main(argv=None):
    p = argparse.ArgumentParser(description="个人项目日报写稿与核对（不发送）")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("build", "material"):
        s = sub.add_parser(name)
        s.add_argument("--date", help="统计日 YYYY-MM-DD，默认最近一个已经结束的统计日")
        s.add_argument("--facts", help="事实快照文件，默认 DigitalTwin/daily/<统计日>.facts.json")
    b = sub.choices["build"]
    b.add_argument("--reply", help="用现成的模型回复文件（回放、测试用）")
    b.add_argument("--fallback", action="store_true", help="直接出事实版")
    b.add_argument("--out", help="输出目录，默认 DigitalTwin/daily/")
    a = p.parse_args(argv)
    try:
        day = dt.date.fromisoformat(a.date) if a.date else pdaily_data.default_day()
        facts = load_facts(a.facts, day)
    except (OSError, ValueError, KeyError) as e:
        print(json.dumps({"ok": False, "error": f"读不了事实快照：{e}"}, ensure_ascii=False))
        return 1
    if a.cmd == "material":
        print(json.dumps(material(facts), ensure_ascii=False, indent=1))
        return 0
    reply = Path(a.reply).read_text(encoding="utf-8") if a.reply else None
    meta = build(facts, a.out or DAILY, reply=reply, force_fallback=a.fallback)
    print(json.dumps({"ok": True, **meta}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
