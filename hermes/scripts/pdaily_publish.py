"""个人项目日报写稿与核对（#111 批 2a）：读 1c 的事实快照，让 Hermes 写卡片要素和完整报告，脚本核对后
按乙版生成飞书卡片 JSON（常浩 2026-11-19 定，#226）；模型失败、或核对不过重写一次仍不过，就出事实版（标「分析降级」）。
send 子命令负责发送（#155）。

乙版的思路：一件事只出现一次，按「接下来归谁」摆。四板块：核心数据速览 → 昨日关键成果 → 风险与阻塞（按原因归类，
每类带去向，项目明细折叠）→ 需要你拍板（表格：#、事项、推荐、已挂）。风险的去向只有两种：「→ 拍板 N」和「只观察」。
不再有「今日推进」和单列的「工程自检」：自检有问题并进风险表的一行，正常时页脚一句。

规格：docs/个人项目日报-V2-设计.md 第 3 节（篇幅、留痕）、4.0（卡片）、4.1–4.9（完整报告）、第 6 节（证据）、7.1。
用法：
  python3 pdaily_publish.py build [--date 统计日] [--facts 快照] [--reply 模型回复文件] [--fallback] [--out 目录]
      默认读 DigitalTwin/daily/<统计日>.facts.json（没有、或是统计日结束前取的，就先跑 pdaily_facts 重取），
      调 Hermes 单次写稿，写出 <统计日>.card.json、.report.md、.card-fields.json、.build.json，最后打印一行 JSON 摘要。
      --reply 用现成的模型回复（回放、测试用），--fallback 直接出事实版。取数本身失败时出一张「取数失败」的最小卡片。
  python3 pdaily_publish.py material [--date 统计日] [--facts 快照]   只打印交给模型的压缩材料
  python3 pdaily_publish.py send [--date 统计日] [--out 目录] [--force]
      建完整报告云文档，按乙版现做卡片，拦工作内容（PRD D22：猛鬼兽只报个人项目），以猛鬼兽机器人发到常浩私聊并回读，
      记 <统计日>.delivery.json；同一统计日只发一次。卡片里带工程自检结果（#159）。成功不输出，失败打印一句原因（#155，定时任务 10:00）。
分工：能从快照算出来的（核心数据四行、已挂几天、成果为空时是没进展还是没数据、留痕）由脚本算；判断（什么算实质推进、
拍板类型、风险怎么归类和去向、推荐）由模型写。脚本核对：编号、百分比、PR 数、「已安装 / 已验收」这类说法要有快照出处，
六段齐全、三个数和卡片一致；每类风险有去向、指向的拍板存在、同一类不拆两条、缺材料的事不进拍板；
不通过带着问题让模型重写一次，再不过出事实版；写法问题（最晚时间格式、超长）自动修正并记提醒；
「同一件事在风险和拍板两处各说各的」判不准，只记提醒（overlaps），重写时一并告诉模型。
留痕（P-13）：模型取 Hermes 会话库里这次写稿会话实际用过的主模型（含回退）。
"""
import argparse
import datetime as dt
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdaily_data  # noqa: E402
import pdaily_facts  # noqa: E402
import pdaily_act  # noqa: E402  拍板账（#235）；它反过来 import 本模块，Python 会给它半初始化的本模块，它只在函数里用

DAILY = pdaily_facts.DAILY
HERMES_HOME = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
HERMES = os.environ.get("DT_HERMES_CLI", "hermes")
PROMPT = Path(os.environ.get("DT_PDAILY_PROMPT", Path(__file__).resolve().parent.parent / "prompts" / "pdaily_report.md"))
BUDGET = int(os.environ.get("DT_PDAILY_BUDGET", "1200"))   # 写稿最多等多少秒
# 写稿的思考强度（常浩 11-11 定 medium，#157）：同一份材料实测 high 要 8 分钟、medium 3 分钟，拍板、风险、成果几乎不少；
# 模型跟随 Hermes 主模型（Opus 5.5），这里只降思考。要改就设这个变量，不用改代码。
REASONING = os.environ.get("DT_PDAILY_REASONING", "medium")
CLIP = 300            # 材料里每段原文最多保留多少字
ACT_CLIP = 100        # 行动记录每个字段最多保留多少字（行动多，压短些）
# 卡片字段硬上限：比提示词给模型的字数（20 / 45 / 15 / 50）宽一点，留余量，超了才截断
LIMITS = {"headline": 60, "item": 24, "recommendation": 50, "text": 50, "cause": 20, "scope": 45, "detail": 400, "task": 300}
WEEK = "一二三四五六日"
DECISION_TYPES = ("阻断", "限时", "普通")
LEVELS = {"高": "🔴", "中": "🟠", "低": "🟡", "待识别": "⚪"}
DECISION_KINDS = ("选", "做")      # 选：要常浩在几种做法里定；做：要常浩亲手做（验收、实测、登录）
NUMS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
# 完整报告的段落顺序和卡片一致（#226）：成果 → 风险 → 拍板，后面是明细
ACH_SECTION = "一、昨日关键成果"
SECTIONS = [ACH_SECTION, "二、风险、阻塞与跨项目依赖", "三、需要你拍板", "四、各项目推进",
            "五、Hermes 代理述职", "六、机制改进与沉淀", "覆盖与可信度"]
# 缺材料的事不进拍板：推荐里说「给不出倾向」的，应该先由猛鬼兽把材料补齐再问
NO_MATERIAL = re.compile(r"不给倾向|暂不推荐|无法推荐|给不出推荐|材料不足|材料里没有|缺材料")
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
    keys, recent, held = remembered()
    decisions = [dict(d, candidate=i, **({"already_decided": True} if pdaily_act.cand_key(d) in keys else {}))
                 for i, d in enumerate(facts.get("decisions") or []) if isinstance(d, dict)]
    return {
        "stat_day": facts.get("stat_day"), "window": facts.get("window"), "fetched_at": facts.get("fetched_at"),
        "projects": projects,
        "actions": actions, "routine_cron": routine,
        "decisions": decisions, "ledger": {"recently_decided": recent, "on_hold": held},
        "yesterday_checks": yesterday_checks(facts),
        "risks": facts.get("risks"), "cross_refs": facts.get("cross_refs"),
        "observations": facts.get("observations"), "counts": facts.get("counts"),
        "activity": facts.get("activity"), "coverage": facts.get("coverage"), "notes": facts.get("notes"),
    }


def yesterday_checks(facts):
    """前一天日报派的「猛鬼兽查」只读单和回执摘要（#236）；读不了就当没有。"""
    try:
        prev = (dt.date.fromisoformat(facts["stat_day"]) - dt.timedelta(days=1)).isoformat()
        return pdaily_act.checks_for_material(prev)
    except (KeyError, TypeError, ValueError, OSError):
        return []


def remembered():
    """拍板账（#235）：近期定过的候选键、定过的事摘要、先放着的件数；账读不了就当空的，不挡写稿。"""
    try:
        return pdaily_act.remembered()
    except (OSError, ValueError):
        return set(), [], 0


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


def generate(facts, workdir, problems=None):
    """调 Hermes 单次写稿，返回 (回复全文, 会话 ID, 模型)。失败抛 GenError。
    problems：上一稿核对没过的问题清单；给了就带着问题让模型整份重写（另存一份 query）。"""
    q = Path(workdir) / f"{facts['stat_day']}.query{'.retry' if problems else ''}.md"
    text = query(facts)
    if problems:
        text += "\n\n## 上一稿没过核对\n\n上一稿有下面这些问题。请整份重写（格式不变，还是 <<<CARD>>> 和 <<<REPORT>>> 两部分），" \
                "只改这些问题，别的照旧：\n\n" + "\n".join(f"- {e}" for e in problems[:30]) + "\n"
    q.write_text(text, encoding="utf-8")
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


def dicts(card, key):
    return [x for x in card.get(key) or [] if isinstance(x, dict)]


def cand_list(x, cands):
    """这条拍板对应的候选：认 candidates（序号列表）和旧写法 candidate（单个序号）；序号不对的忽略。"""
    v = x.get("candidates") if isinstance(x.get("candidates"), list) else [x.get("candidate")]
    return [cands[i] for i in v if isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(cands)
            and isinstance(cands[i], dict)]


def age_days(facts, x):
    """已挂几天：对应候选里最早的 since 到统计日结束，按日历日算；没有候选、没有 since 或不足 1 天返回 None。"""
    try:
        end = pdaily_data.parse_ts((facts.get("window") or {})["end"])
    except (KeyError, TypeError, ValueError):
        return None
    days = []
    for c in cand_list(x, facts.get("decisions") or []):
        try:
            since = pdaily_data.parse_ts(c.get("since"))
        except (TypeError, ValueError, AttributeError):
            continue
        if since:
            days.append((end.date() - since.astimezone(end.tzinfo).date()).days)
    return max(days) if days and max(days) >= 1 else None


def num_mark(n):
    return NUMS[n - 1] if 1 <= n <= len(NUMS) else f"({n})"


def to_list(r):
    """风险的去向：指向哪几条拍板（1 起的编号，去重按序）；空列表 = 只观察。写得不对的返回 None。"""
    v = r.get("to_decisions")
    if not isinstance(v, list) or any(not isinstance(i, int) or isinstance(i, bool) for i in v):
        return None
    return sorted(set(v))


def refs_in(*texts):
    text = " ".join(x for x in texts if isinstance(x, str))
    return set(REF.findall(text)) | {n for _, n in QREF.findall(text)}


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
    """核心数据速览四行（#226）：推进、合并、要你拍板、风险。代理动作不再占一行（明细在完整报告述职段）。"""
    n = roster_size(facts)
    progress = progress_ids(card)
    dec, risks = dicts(card, "decisions"), dicts(card, "risks")
    degraded = card.get("degraded")
    merged = (facts.get("activity") or {}).get("merged_prs")
    ages = [a for a in (age_days(facts, d) for d in dec) if a]
    oldest = f"最久的挂了 {max(ages)} 天" if ages else ""
    if degraded:
        dec_v, dec_n = f"候选 {len(dec)}", "；".join(x for x in ("分析降级，没判断", oldest) if x)
        risk_n = "分析降级，没归类、没判断去向"
    else:
        dec_v = f"<font color='red'>**{len(dec)}**</font>" if dec else "0"
        do = sum(1 for d in dec if d.get("kind") == "做")
        kinds = [f"{len(dec) - do} 件要你选" if len(dec) - do else "", f"{do} 件要你动手" if do else ""]
        deadlines = sorted(d["deadline"] for d in dec if s(d.get("deadline")))
        dec_n = "；".join(x for x in ("、".join(k for k in kinds if k), f"最晚 {deadlines[0]}" if deadlines else "", oldest) if x)
        mine = sum(1 for r in risks if to_list(r))
        check = sum(1 for r in risks if not to_list(r) and isinstance(r.get("check"), dict))
        watch = len(risks) - mine - check
        risk_n = "、".join(x for x in (f"{mine} 类要你定" if mine else "", f"{check} 类猛鬼兽查" if check else "",
                                      f"{watch} 类只观察" if watch else "") if x)
    return [
        ("有推进的项目", "未知" if progress is None else f"**{len(progress)}** / {n}",
         "分析降级，没判断" if progress is None else f"其余 {n - len(progress)} 个无实质推进"),
        ("合并 PR", num(merged), ("统计日 04:00→04:00" if merged is not None else "GitHub 取不到") + auto_merge_note(facts)),
        ("需要你拍板", dec_v, dec_n),
        ("风险与阻塞", f"{len(risks)} 类", risk_n),
    ]


def auto_merged(facts):
    """统计日内猛鬼兽按策略合并的 PR（#257；事实快照的 auto_merges.merged）。没有、算不出来都是空列表。"""
    return [m for m in ((facts.get("auto_merges") or {}).get("merged") or []) if isinstance(m, dict)]


def auto_merge_note(facts):
    n = len(auto_merged(facts))
    return f"；**其中猛鬼兽替你合并 {n} 个**" if n else ""


def auto_merge_panel(facts):
    """速览表下面的折叠面板「替你合并了什么」（甲版，常浩 2026-11-20 看了三张真卡片预览后选定，#259）：
    一条一行：仓库#号、标题、档、合并官怎么判的、撤回口令；结果不明的排在后面标出。没有按策略合并的日子返回 None，卡片和原来一样。"""
    merged = auto_merged(facts)
    if not merged:
        return None
    def ref_of(m):
        return f"{str(m.get('repo') or '?').split('/')[-1]}#{m.get('pr')}"

    lines = []
    for i, m in enumerate(merged, 1):
        ref = ref_of(m)
        judge = s(m.get("suggestion")) or "未知"
        if judge == "退回":
            judge += f"：{s(m.get('reasons')) or '未写原因'}（按你定的不拦）"
        tail = f"｜已开撤回 PR #{m['revert'].get('revert_pr')}" if isinstance(m.get("revert"), dict) else f"｜撤回口令「撤回 {ref}」"
        lines.append(f"{i}. **{ref}** {clip(m.get('title'), 60)}｜{s(m.get('tier')) or '?'}｜合并官：{judge}{tail}")
    unsure = [m for m in ((facts.get("auto_merges") or {}).get("unsettled") or []) if isinstance(m, dict)]
    for m in unsure:
        lines.append(f"- ⚠️ 已合并、来源不明：**{ref_of(m)}** {clip(m.get('title'), 60)}，合并请求发出后没拿到结果，"
                     "证明不了是猛鬼兽合的；不算在上面，不能用撤回口令" if m.get("unknown_merge") else
                     f"- ⚠️ 结果不明：**{ref_of(m)}** {clip(m.get('title'), 60)}，"
                     "合并请求发了、还没核对清楚，合并官下一轮只读核对")
    lines.append("<font color='grey'>不同意哪条，回「撤回 仓库#号」，猛鬼兽开撤回 PR 并合并；装过机的另外问你要不要重装。</font>")
    return panel(f"替你合并了什么 · {len(merged)} 个 · 展开", lines)


def header_numbers(card):
    """完整报告开头三个数（4.1），和卡片同源：实质推进的项目数、拍板件数、风险类数。"""
    progress = progress_ids(card)
    return ("未知" if progress is None else str(len(progress)), str(len(dicts(card, "decisions"))), str(len(dicts(card, "risks"))))


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


def drop_decided(facts, card):
    """拍板里常浩已经定过（或先放着没到期）的，直接去掉、记提醒，不算核对失败（#235 审查 P1-3）；
    风险的去向编号跟着改：指到被去掉那条的删掉，后面的往前挪。"""
    decs = card.get("decisions")
    if not isinstance(decs, list):
        return []
    keys, cands = remembered()[0], facts.get("decisions") or []
    gone = []
    for i, d in enumerate(decs, 1):
        mine = [pdaily_act.cand_key(c) for c in cand_list(d, cands)] if isinstance(d, dict) else []
        if mine and all(k in keys for k in mine):
            gone.append(i)
    if not gone:
        return []
    card["decisions"] = [d for i, d in enumerate(decs, 1) if i not in gone]
    for r in card.get("risks") or []:
        to = to_list(r) if isinstance(r, dict) else None
        if to is not None:
            r["to_decisions"] = [n - sum(1 for g in gone if g < n) for n in to if n not in gone]
    return [f"拍板第 {'、'.join(map(str, gone))} 条常浩已经定过（或说了先放着），已去掉"]


def normalize(facts, card):
    """就地规整卡片要素，返回提醒清单。只修写法：去尾部标点、最晚时间换格式或去掉、超长截断；旧字段 today、reply 丢掉。"""
    warns = []
    cands = facts.get("decisions") or []
    year = (facts.get("stat_day") or "2000")[:4]
    card.pop("today", None)
    warns += drop_decided(facts, card)
    for i, r in enumerate(card.get("risks") or []):     # 只读单任务超长：截断记提醒，不让整份降级（复审 P3-5）
        c = r.get("check") if isinstance(r, dict) else None
        if isinstance(c, dict) and s(c.get("task")) and len(c["task"]) > LIMITS["task"]:
            c["task"] = cut(c["task"], LIMITS["task"])
            warns.append(f"risks[{i}].check.task 超过 {LIMITS['task']} 字，已截断")
    for key in ("decisions", "achievements", "risks"):
        for i, x in enumerate(card.get(key) or []):
            if not isinstance(x, dict):
                continue
            x.pop("reply", None)
            for f in ("item", "recommendation", "text", "cause", "scope", "detail"):
                v = s(x.get(f))
                if v is None:
                    continue
                v = v.rstrip(TRAIL)
                if len(v) > LIMITS[f]:
                    warns.append(f"{key}[{i}].{f} 超过 {LIMITS[f]} 字，已截断")
                    v = cut(v, LIMITS[f])
                x[f] = v
            if key == "decisions" and x.get("deadline") is not None:
                raw = x.get("deadline")
                dl = norm_deadline(raw, year) if isinstance(raw, str) else None
                if dl is None:
                    warns.append(f"decisions[{i}] 的最晚时间「{raw}」写法认不出（要 MM-DD HH:MM），已去掉")
                elif not any(deadline_source(dl, c) for c in cand_list(x, cands)):
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
            unknown = d.get("merged_prs") is None or d.get("closed_issues") is None   # GitHub 没取到：核不了，不当没合并（#176）
            if not (unknown or d.get("merged_prs") or d.get("closed_issues") or rec.get("installs")):
                errs.append(f"progress_projects 里的「{p}」昨天没有合并、关闭或安装，只有提交或 CURRENT 改动不算实质推进")
    decs = need_list("decisions", ("project", "item", "recommendation"), {"type": DECISION_TYPES, "kind": DECISION_KINDS})
    ach = need_list("achievements", ("project", "text"))
    risks = need_list("risks", ("cause", "scope"), {"level": tuple(LEVELS)})
    for i, d in enumerate(decs):
        if NO_MATERIAL.search(s(d.get("recommendation")) or ""):
            errs.append(f"decisions[{i}]「{clip(d.get('item'), 30)}」的推荐说给不出倾向：缺材料的事不进拍板，"
                        "放到风险里写「只观察」，等材料齐了再问")
    seen = {}
    for j, r in enumerate(risks):
        names = r.get("projects")
        if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names):
            errs.append(f"risks[{j}] 缺 projects（涉及的项目名列表，至少一个）")
            names = []
        for n in names:
            if roster.resolve(n) is None:
                errs.append(f"risks[{j}] 的项目「{n}」不在名册里")
        cause = s(r.get("cause"))
        if cause and cause in seen:
            errs.append(f"risks[{j}] 和 risks[{seen[cause]}] 是同一类「{cause}」，要合成一类")
        seen.setdefault(cause, j)
        to = to_list(r)
        c = r.get("check")
        if c is not None:                        # 猛鬼兽查（#236）：只给只观察的风险；项目在名册里；任务写清
            if not isinstance(c, dict) or roster.resolve(s(c.get("project")) or "") in (None, "hermes") or not s(c.get("task")):
                errs.append(f"risks[{j}].check 要写成 {{\"project\": 名册项目名, \"task\": 要查什么、交什么}}")
            elif to_list(r):
                errs.append(f"risks[{j}] 已经指到拍板，就不用猛鬼兽查；check 只给不用常浩定的风险")
        if to is None:
            errs.append(f"risks[{j}] 缺 to_decisions（指向第几条拍板的编号列表；不用常浩定就给空列表，表示只观察）")
            continue
        for n in to:
            if not 1 <= n <= len(decs):
                errs.append(f"risks[{j}] 的去向写拍板第 {n} 条，但拍板只有 {len(decs)} 条")
    if sum(1 for r in risks if isinstance(r.get("check"), dict)) > pdaily_act.CHECKS_MAX:
        errs.append(f"猛鬼兽查（check）每天最多 {pdaily_act.CHECKS_MAX} 类")
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
    a0, a1 = pos[SECTIONS.index(ACH_SECTION)], pos[SECTIONS.index(ACH_SECTION) + 1]
    if a0 >= 0 and a1 > a0:                      # 报告「昨日关键成果」一段逐行按行首项目名查
        for line in report[a0:a1].splitlines()[1:]:
            m = re.match(r"^\s*(?:\d+[.、]|[-*])\s*\**([^：:*]+?)\**\s*[：:]", line)
            pid = roster.resolve(m.group(1).strip()) if m else None
            if pid and pid != "hermes":
                for c in claims_unverified(line, roster.by_id.get(pid)):
                    errs.append(f"完整报告成果段「{clip(c, 30)}」：快照里没有安装 / 验收记录")

    # 完整报告
    for name, q in zip(SECTIONS, pos):
        if q < 0:
            errs.append(f"完整报告缺「## {name}」")
    found = [q for q in pos if q >= 0]
    if found != sorted(found):
        errs.append("完整报告各段顺序不对")
    if re.search(r"^#{1,3}\s*\S*今日推进", report, re.M):
        errs.append("完整报告不再有「今日推进计划」一段：要做的事写在拍板和风险的去向里")
    plain = report.replace("*", "")
    m = re.search(r"实质推进\s*(\S+?)\s*个项目\s*[｜|]\s*待你决策\s*(\S+?)\s*件\s*[｜|]\s*风险\s*(\S+?)\s*类", plain)
    want = header_numbers(card)
    if not m:
        errs.append("完整报告开头缺「实质推进 N 个项目｜待你决策 N 件｜风险 N 类」")
    elif m.groups() != want:
        errs.append(f"完整报告开头三个数 {'/'.join(m.groups())} 和卡片 {'/'.join(want)} 对不上")
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


def overlaps(facts, card):
    """同一件事有没有在风险和拍板两处各说各的：某类风险的明细里点到「某项目的 #N」，某条只涉及这一个项目的拍板也点到 #N，
    而这类风险的去向没指到那条拍板。返回提醒清单，只提醒不拦：裸编号各仓库会重号、明细是自由文本，判不准，
    不值得为它把整份日报降成事实版（11-18 实跑：十个项目的同类 PR 合成一类后，#7 在两个仓库重号，误拦过）。"""
    roster = Roster(facts)
    decs, out = dicts(card, "decisions"), []

    def owner(seg):
        head = seg.strip().split(" ")[0]
        hit = [pid for a, pid in roster.alias.items() if seg.strip().startswith(a) or (len(head) >= 2 and a.startswith(head))]
        return hit[0] if len(set(hit)) == 1 else None

    for j, r in enumerate(dicts(card, "risks")):
        to = to_list(r) or []
        pairs = {(owner(seg), n) for seg in re.split(r"[｜|]", s(r.get("detail")) or "") for n in refs_in(seg)}
        for i, d in enumerate(decs, 1):
            pids = {roster.resolve(n) for n in split_names(s(d.get("project")))} - {None}
            if len(pids) != 1 or i in to:
                continue
            both = sorted({n for pid, n in pairs if pid in pids} & refs_in(d.get("item"), d.get("recommendation")), key=int)
            if both:
                out.append(f"risks[{j}]「{clip(r.get('cause'), 20)}」和拍板第 {i} 条都点到 {roster.name(next(iter(pids)))} "
                           f"{'、'.join('#' + b for b in both)}：是同一件事的话，这类风险的去向要指到拍板 {i}")
    return out


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
    decs = [{"type": None, "kind": None, "candidates": [i], "project": name(d.get("project_id")), "item": clip(d.get("item"), 60),
             "recommendation": None, "deadline": iso_deadline(d.get("deadline")),
             "source": d.get("source"), "evidence": (d.get("evidence") or [])[:2]}
            for i, d in enumerate(facts.get("decisions") or [])
            if isinstance(d, dict) and pdaily_act.cand_key(d) not in remembered()[0]]
    ach = []
    for p in facts.get("projects") or []:
        prs = (p.get("yesterday_delta") or {}).get("merged_prs") or []
        if prs:
            last = latest_pr(prs)
            ach.append((len(prs), {"project": name(p.get("project_id")),
                                   "text": f"合并 {len(prs)} 个 PR（最近：#{last.get('number')} {clip(last.get('title'), 30)}）；交付、验收未核实"}))
    ach = [x for _, x in sorted(ach, key=lambda x: -x[0])]
    items = [{"category": r.get("category") or "待分类", "project": name(r.get("project_id")), "text": clip(r.get("item"), 60),
              "evidence": r.get("evidence") or []} for r in facts.get("risks") or [] if isinstance(r, dict)]
    risks = []                   # 事实版不归因：按快照自带的类别合并，去向不判断
    for cat in dict.fromkeys(x["category"] for x in items):
        mine = [x for x in items if x["category"] == cat]
        names = list(dict.fromkeys(x["project"] for x in mine))
        risks.append({"level": "待识别", "cause": cat, "scope": f"{len(names)} 个项目 {len(mine)} 条", "projects": names,
                      "detail": clip("｜".join(f"{x['project']}：{x['text']}" for x in mine), 600), "to_decisions": None})
    card = {"headline": f"分析降级：{reason}。以下只摘事实，没有判断和推荐", "progress_projects": None,
            "decisions": decs, "achievements": ach, "risks": risks, "degraded": reason}
    unknown = delta_unknown(facts)
    src = lambda x: f"（出处：{'；'.join(str(e) for e in ([x.get('source')] if x.get('source') else []) + list(x.get('evidence') or []))}）" \
        if x.get("source") or x.get("evidence") else ""
    lines = [f"⚠️ 分析降级：{reason}。本报告只摘事实快照，没有定级、排序和推荐；拍板类型、风险等级都未判断。", ""]
    lines.append(f"实质推进 未知 个项目｜待你决策 {len(decs)} 件｜风险 {len(risks)} 类")
    empty2 = f"- 未知：{len(unknown)} 个项目 GitHub 取不到（{'、'.join(name(u) for u in unknown)}）" if unknown else "- 昨天没有合并"
    lines += ["", f"## {SECTIONS[0]}（只列合并，交付、验收未核实）"] + [f"- {a['project']}：{a['text']}" for a in ach]
    if not ach or unknown:
        lines.append(empty2)
    lines += ["", f"## {SECTIONS[1]}（候选，未归因、未定去向）"] + \
        ([f"- {x['category']}｜{x['project']}：{x['text']}{src(x)}" for x in items] or ["- 无"])
    lines += ["", f"## {SECTIONS[2]}（候选，未判断）"] + ([f"- {d['project']}：{d['item']}{src(d)}" for d in decs] or ["- 无"])
    lines += ["", f"## {SECTIONS[3]}"]
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
    v, n = action_counts(facts)
    lines += ["", f"## {SECTIONS[4]}", f"- 共 {v or '未知'} 条：{n}（明细在本地完整日报）"]
    lines += ["", f"## {SECTIONS[5]}", "- 无（降级时不提沉淀提案）"]
    lines += ["", f"## {SECTIONS[6]}", coverage_line(facts)]
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


def table(cols, rows):
    """卡片表格：表头灰底加粗、自动行高、换行不溢出（常浩 2026-11-10 定稿的写法）。cols = [(字段, 表头, 宽度)]。"""
    return {"tag": "table", "page_size": 10, "row_height": "auto", "row_max_height": "999px",
            "header_style": {"text_align": "left", "text_size": "normal", "background_style": "grey", "bold": True, "lines": 1},
            "columns": [{"name": k, "display_name": n, "data_type": "lark_md", "width": w, "vertical_align": "top"}
                        for k, n, w in cols],
            "rows": rows}


def panel(title, blocks):
    """折叠面板，默认收起（写法同 groupdigest 的「原文与译文 · 展开」）。"""
    return {"tag": "collapsible_panel", "expanded": False, "background_color": "grey-50",
            "border": {"color": "grey", "corner_radius": "6px"}, "padding": "4px 10px 8px 10px", "vertical_spacing": "8px",
            "header": {"title": {"tag": "plain_text", "content": title}, "width": "fill", "vertical_align": "center",
                       "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "color": "grey", "size": "16px 16px"},
                       "icon_position": "right", "icon_expanded_angle": -180},
            "elements": [md(x) for x in blocks]}


def project_label(v):
    """事项前的项目名：一两个照写，三个以上写「N 个项目」（明细在风险的折叠面板里）。"""
    names = split_names(v)
    return v if len(names) <= 2 else f"{len(names)} 个项目"


def decision_rows(facts, decs):
    rows = []
    for n, d in enumerate(decs, 1):
        item = f"**{project_label(d.get('project'))}·{d.get('item')}**"
        if d.get("type") in ("阻断", "限时"):
            item = f"<font color='red'>**{d['type']}**</font> " + item
        if d.get("kind") == "做":
            item += "（要你动手）"
        if d.get("deadline"):
            item += f"\n<font color='red'>最晚 {d['deadline']}</font>"
        age = age_days(facts, d)
        rows.append({"n": num_mark(n), "i": item, "r": d.get("recommendation") or "<font color='grey'>未判断</font>",
                     "a": f"<font color='red'>{age} 天</font>" if age else "—"})
    return rows


def risk_rows(risks, degraded):
    rows = []
    for r in risks:
        to = to_list(r)
        if degraded or to is None:
            go = "<font color='grey'>未判断</font>"
        elif to:
            go = f"**→ 拍板 {''.join(num_mark(n) for n in to)}**"
        else:
            go = "猛鬼兽派只读单查，明天报" if isinstance(r.get("check"), dict) else "只观察"
        rows.append({"c": f"{LEVELS.get(r.get('level'), '⚪')} {r.get('cause') or '待分类'}", "w": r.get("scope") or "", "g": go})
    return rows


def card_mark(token):
    """页脚的卡片令牌尾号（同托管汇报卡）：回读时靠它认出发出去的就是这一张。"""
    return f"卡片 {token[-6:]}"


def decision_buttons(token, ns):
    """拍板表下一排按钮（乙版定稿的排法）：全部按推荐 + ① 按推荐…；按钮只带 {"pdaily": 令牌, "n": 编号}，0 = 其余全部。
    ns：能按推荐办的编号（有推荐、不是要常浩亲手做的，pdaily_act.buttonable）。"""
    def b(text, value, kind):
        return {"tag": "column", "width": "auto", "elements": [{
            "tag": "button", "text": {"tag": "plain_text", "content": text}, "type": kind, "size": "small",
            "behaviors": [{"type": "callback", "value": {"pdaily": token, "n": value}}]}]}
    cols = [b("全部按推荐", 0, "primary_filled")] + [b(f"{num_mark(k)} 按推荐", k, "primary") for k in ns]
    return {"tag": "column_set", "flex_mode": "flow", "horizontal_spacing": "8px", "columns": cols}


def build_card(facts, card, trace, doc_url=None, doc_error=None, selfcheck=None, token=None):
    """乙版卡片 JSON 2.0（常浩 2026-11-19 在私聊三版预览里选定，#226）；默认宽度。四板块：
    核心数据速览 → 昨日关键成果 → 风险与阻塞（按原因一类一行，带去向；项目明细在折叠面板里）→ 需要你拍板（表格）。
    有按策略合并的日子（#257），速览表「合并 PR」一行的说明带一句「其中猛鬼兽替你合并 N 个」，表下多一个默认收起的折叠面板
    「替你合并了什么」（常浩 2026-11-20 选甲，#259）；四板块不变。
    selfcheck：工程自检结果（#159）。None 不显示（预览）；"" 表示正常，只在底部灰字写一句；有内容就并进风险表
    最后一行「猛鬼兽自检有问题」，原文放进折叠面板。
    token：卡片令牌（#235）。给了就在拍板表下加一排「按推荐」按钮；预览不给，不放按钮。"""
    sub = [f"统计日 {window_text(facts)}"]
    if unread(facts):
        sub.append(f"{len(unread(facts))} 个项目未读到")
    degraded = card.get("degraded")
    if degraded:
        sub.append("分析降级")
    # 速览表列宽是常浩 2026-11-10 定稿（DigitalTwin/daily/2026-11-09.card.restored.json）：「昨日」窄、「说明」宽
    els = [md("**📈 核心数据速览**"),
           table((("k", "指标", "25%"), ("v", "昨日", "15%"), ("n", "说明", "60%")),
                 [{"k": k, "v": v, "n": n} for k, v, n in metrics(facts, card)])]
    merged_panel = auto_merge_panel(facts)                # 甲版（#259）：不加板块，收在速览表下面
    if merged_panel:
        els.append(merged_panel)
    ach = dicts(card, "achievements")
    unknown = delta_unknown(facts)
    empty = f"未知：{len(unknown)} 个项目 GitHub 取不到，没法判断昨天有没有成果" if unknown else "昨天没有新成果"
    els += [{"tag": "hr"}, md("**✅ 昨日关键成果**"),
            md("\n".join(f"{i}. **{a.get('project')}**：{a.get('text')}" for i, a in enumerate(ach, 1)) or empty)]
    risks = dicts(card, "risks")
    rows = risk_rows(risks, degraded)
    details = [f"**{r.get('cause') or '待分类'}**：{r['detail']}" for r in risks if s(r.get("detail"))]
    if selfcheck:
        first = selfcheck.strip().splitlines()[0]
        rows.append({"c": "🟠 猛鬼兽自检有问题", "w": cut(first, 45) if len(first) > 45 else first, "g": "处理办法见明细"})
        details.append(f"**猛鬼兽自检**\n{selfcheck.strip()}")
    els += [{"tag": "hr"}, md(f"**⚠️ 风险与阻塞 · {len(rows)} 类**" if rows else "**⚠️ 风险与阻塞**")]
    if rows:
        els.append(table((("c", "问题", "30%"), ("w", "涉及", "45%"), ("g", "去向", "25%")), rows))
        if details:
            els.append(panel("每类涉及哪些项目 · 展开", details))
    else:
        els.append(md("没有风险和阻塞"))
    decs = dicts(card, "decisions")
    els += [{"tag": "hr"}, md(f"**🎯 需要你拍板 · {len(decs)} 件**" if decs else "**🎯 需要你拍板**")]
    if decs:
        els.append(table((("n", "#", "8%"), ("i", "事项", "34%"), ("r", "推荐", "43%"), ("a", "已挂", "15%")),
                         decision_rows(facts, decs)))
        ns = [n for n, d in enumerate(decs, 1) if pdaily_act.buttonable(d)]
        if token and not degraded and ns and len(decs) <= 20:      # 降级卡、没有能按推荐办的：不放按钮（审查 P1-3）
            els.append(decision_buttons(token, ns))
            els.append(md("<font color='grey'>点「按推荐」就交给猛鬼兽办；不做、先放着或想改，引用这张卡片回一句，"
                          "例如「③ 不做」「④ 先放着」。点了或回了才算你定了，只管点到的那几条，只做到开 PR。</font>"))
        else:
            els.append(md("<font color='grey'>想定哪条，引用这张卡片回一句，例如「① 按推荐」「③ 不做」「都按推荐」。"
                          "回了才算你定了，只管点到的那几条。</font>"))
    else:
        els.append(md("今天没有要你拍板的事"))
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
    if token:
        trace += f"｜{card_mark(token)}"
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
    facts = pdaily_facts.build(day, projects, pdaily_data.netwait.wait_seconds())   # 唤醒后代理没通：gh 最多等 300 秒（#176、P9）
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
            generated = reply is None
            if generated:
                reply, meta["session_id"], meta["model"] = generate(facts, out)
            (out / f"{day}.reply.md").write_text(reply, encoding="utf-8")
            card, report = parse_reply(reply)
            meta["warnings"] = normalize(facts, card)
            meta["check_errors"] = check(facts, card, report)
            if meta["check_errors"] and generated:      # 核对没过：带着问题重写一次（#226）；现成回复（回放、测试）不重写
                meta["first_errors"] = meta["check_errors"]
                (out / f"{day}.reply.first.md").write_text(reply, encoding="utf-8")
                reply, meta["session_id"], meta["model"] = generate(
                    facts, out, problems=meta["check_errors"] + overlaps(facts, card))
                (out / f"{day}.reply.md").write_text(reply, encoding="utf-8")
                card, report = parse_reply(reply)
                meta["warnings"] = normalize(facts, card)
                meta["check_errors"] = check(facts, card, report)
            if not meta["check_errors"]:
                meta["warnings"] += overlaps(facts, card)
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


def delivered_today(day):
    try:
        return bool(json.loads((DAILY / f"{day}.delivery.json").read_text(encoding="utf-8")).get("sent"))
    except (OSError, ValueError):
        return False


def start_checks(day):
    """日报发出后，在后台派「猛鬼兽查」的只读单（#236）；起不来只记日志，不影响日报。"""
    try:
        subprocess.Popen([sys.executable, str(Path(__file__).with_name("pdaily_act.py")), "checks", "--day", day],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        pdaily_act.log(f"只读单没起来 {day}：{e}")


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


def record_build_error(day, reason, out=None):
    """写稿没成的原因写进 <统计日>.build-error.json，10:00 发送时写进失败卡片（定时任务入口的网络闸门、取数失败共用）。"""
    out = Path(out or DAILY)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{day}.build-error.json").write_text(json.dumps({"error": reason}, ensure_ascii=False), encoding="utf-8")


def build_error(day, out):
    try:
        return str(json.loads((Path(out) / f"{day}.build-error.json").read_text(encoding="utf-8"))["error"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def send(day, out, force=False):
    """把这一天的日报发到常浩私聊：建完整报告文档 → 按乙版出卡片 → 拦工作内容 → 发 → 回读 → 记 delivery.json。
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
           "read_back": False, "error": None, "card_token": secrets.token_hex(8)}   # 按钮令牌（#235）：只认最新一张卡
    try:
        meta = json.loads((out / f"{day}.build.json").read_text(encoding="utf-8"))
        ff = out / f"{day}.facts.json"
        facts = json.loads((ff if ff.exists() else DAILY / f"{day}.facts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta, facts = None, None
    if meta is None or facts is None or not (out / f"{day}.card-fields.json").exists():
        why = build_error(day, out)
        card, doc_text, fields = failure_card(day, f"09:15 的写稿没跑成{'：' + why if why else ''}，没有可发的日报"), None, None
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
                          doc_url=doc_url, doc_error=doc_error, selfcheck=run_self_check(), token=rec["card_token"])
    text = json.dumps(card, ensure_ascii=False)
    bad = work_content(text)
    if bad or not card["header"]["title"]["content"].startswith("📅 猛鬼兽日报 · "):
        rec["error"] = f"卡片里有工作内容（{'、'.join(bad) or '标题不对'}），拒发"
        deliv.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        return rec["error"]
    snap = None
    if fields is not None:                       # 拍板快照（#235 审查 P1-1）：按钮和编号只认常浩看到的这一版
        snap, rec["decisions_sha"] = pdaily_act.snapshot(facts, dicts(fields, "decisions"))
    try:
        prev_sent = bool(json.loads(deliv.read_text(encoding="utf-8")).get("sent"))
    except (OSError, ValueError):
        prev_sent = False
    rec["doc_url"], rec["doc_error"] = doc_url, doc_error
    try:
        data = lark(["im", "+messages-send", "--chat-id", CHAT, "--msg-type", "interactive", "--content", text,
                     "--idempotency-key", f"pdaily-{day}" + (f"-r{rec['card_token']}" if force else "")], "发卡片", "bot")
        rec["message_id"], rec["sent"] = data.get("message_id"), bool(data.get("message_id"))
        rec["at"] = dt.datetime.now(pdaily_data.TZ).isoformat(timespec="seconds")
        if rec["sent"]:
            got = lark(["im", "+messages-mget", "--message-ids", rec["message_id"]], "回读", "bot")
            m = (got.get("messages") or [{}])[0]
            rec["read_back"] = m.get("msg_type") == "interactive" and card["header"]["title"]["content"] in str(m.get("content")) \
                and (card_mark(rec["card_token"]) not in text or card_mark(rec["card_token"]) in str(m.get("content")))
            # 带令牌的卡：回读要见到这张卡的令牌尾号（飞书回读不带按钮的值，同托管汇报卡）
    except SendError as e:
        rec["error"] = str(e)
    if not rec["sent"] and prev_sent:            # 重发没发出去：原来那张卡的记录、卡片、快照都不动（复审 P3-C）
        (out / f"{day}.delivery.retry.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        (out / f"{day}.card.sent.json").write_text(text, encoding="utf-8")
        if snap is not None:
            (out / f"{day}.decisions.sent.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
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
        if not a.out and delivered_today(day.isoformat()):   # 发出去就派（回读没过也派：卡上写了「明天报」，复审 P3-4）
            start_checks(day.isoformat())
        if err:
            print(f"⚠️ 今天的个人项目日报没发成：{err}。详情在 DigitalTwin/daily/{day.isoformat()}.delivery.json")
        return 0
    try:
        facts = load_facts(a.facts, day)
    except Exception as e:  # noqa: BLE001  取数失败也要留一张卡片和原因（7.3）
        reason = f"取数失败（{type(e).__name__}：{clip(e, 120)}）"
        if a.cmd == "build":
            out = Path(a.out or DAILY)
            record_build_error(day.isoformat(), reason, out)   # 10:00 的失败卡片写明原因
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
