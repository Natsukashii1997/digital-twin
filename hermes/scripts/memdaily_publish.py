"""记忆日报写稿、写入、推送（亚古兽，PRD D20 2026-11-11 修订）。

    build --date D   取数 → 亚古兽（hermes -p agumon 单次）写内容 JSON → 核对 → 按 7/15 模板排成 XML，存 DigitalTwin/memdaily/
    write --date D   插到《常浩-记忆日报汇总》最顶部：写前存整份文档快照，已有同一天就不重写，写后回读（标题、表格非空、列宽）
    push  --date D   亚古兽机器人私聊常浩三段式：「📅 D 日报已写入」、文档链接、3～5 行关键摘要
    run   --date D   三步连做；定时任务用（memdaily_job.py）

不覆盖主文档（Aily 07-01 覆盖过历史日报，是硬约束）。--doc 换成测试文档、--no-push 不发消息，用来出样稿。
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memdaily_data as md  # noqa: E402

DIR = md.DT_HOME / "memdaily"
TEMPLATE = Path(os.environ.get("DT_MEMDAILY_TEMPLATE", DIR / "template-0715.md"))
PROMPT = Path(os.environ.get("DT_MEMDAILY_PROMPT", Path(__file__).resolve().parent.parent / "prompts" / "memdaily_report.md"))
HERMES = os.environ.get("DT_HERMES_CLI", "hermes")
AGUMON_HOME = Path(os.environ.get("DT_AGUMON_HOME", "~/.hermes/profiles/agumon")).expanduser()
BUDGET = int(os.environ.get("DT_MEMDAILY_BUDGET", "1500"))      # 写稿最多等多少秒
REASONING = os.environ.get("DT_MEMDAILY_REASONING", "")          # 空 = 用亚古兽配置（2026-11-11 起 xhigh）
DOC_URL = "https://vm94j8bzy7.feishu.cn/docx/{}"
KEEP_BACKUPS = 14

STATUS = ("✅ 已完成", "🔄 进行中", "⏳ 待处理", "🔴 阻塞", "⛔ 已放弃")
ACTION_TYPES = ("⏩ 行动", "🟠 阻塞", "✅ 完成")
DECISION_STATUS = ("待验证", "已执行", "已确认")
INSIGHT_TYPES = ("新洞察", "延续", "修正")
EMOJI = ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟")
PRIVATE = re.compile(r"胎儿|怀孕|备孕|孕期|体重|离婚|恋爱|相亲|住院|生病|看病")   # 私生活硬约束的兜底，命中整行不写
TIME = re.compile(r"^\s*(\d{1,2}:\d{2})(?:\s*[-–~至]\s*(\d{1,2}:\d{2}))?")

# 表头和列宽照 7/15 永久模板（Aily v6.0 列宽字典，2026-11-11 在测试文档上实测生效）
TABLES = {
    "items": (("序号", "事项名称", "当前状态", "涉及人", "出处", "任务主题"), (55, 411, 107, 190, 168, 207)),
    "p2p": (("序号", "对话方", "核心内容摘要", "结论/行动项"), (55, 160, 548, 375)),
    "groups": (("序号", "群名", "核心内容摘要", "结论/行动项"), (55, 160, 548, 375)),
    "meetings": (("序号", "时间", "会议名称", "涉及人", "核心内容"), (55, 160, 182, 182, 559)),
    "decisions": (("ID", "决策事项", "选择", "背景", "逻辑", "排除", "状态"), (55, 247, 179, 198, 198, 198, 63)),
    "insights": (("洞察ID", "类型", "具体内容"), (55, 160, 923)),
    "actions": (("序号", "类型", "任务/阻塞事项", "负责人", "截止时间", "状态"), (55, 160, 414, 187, 187, 135)),
    "methods": (("序号", "场景方法", "行为溯源", "底层模型", "方法定义", "沉淀状态"), (55, 160, 246, 168, 374, 135)),
    "suggestions": (("序号", "建议类型", "建议内容", "依据"), (55, 160, 310, 613)),
}
FIELDS = {
    "items": ("name", "status", "people", "source", "topic"),
    "p2p": ("party", "points", "conclusion"),
    "groups": ("name", "points", "conclusion"),
    "meetings": ("time", "name", "people", "content"),
    "decisions": ("item", "choice", "background", "logic", "exclude", "status"),
    "insights": ("type", "content"),
    "actions": ("type", "task", "owner", "due", "status"),
    "methods": ("scene", "trace", "model", "definition"),
    "suggestions": ("type", "content", "basis"),
}


class GenError(Exception):
    pass


def now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def paths(d):
    return {k: DIR / f"{d}.{k}" for k in ("draft.json", "xml", "build.json", "delivery.json", "query.md", "reply.txt")}


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(p, data):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------- 写稿 ----------

def query(facts, problems=None):
    parts = [PROMPT.read_text(encoding="utf-8"), "---", md.material(facts)]
    if TEMPLATE.exists():
        parts += ["---", "# 模板样例（只学写法和颗粒度，不抄内容）", TEMPLATE.read_text(encoding="utf-8")]
    if problems:
        parts += ["---", "# 上一稿没通过核对，改掉这些再输出完整 JSON", *[f"- {p}" for p in problems]]
    return "\n\n".join(parts)


def session_model(session_id):
    """这次写稿会话实际用过的主模型（含回退），查不到返回 None。只读打开亚古兽的会话库。"""
    try:
        con = sqlite3.connect(f"file:{AGUMON_HOME / 'state.db'}?mode=ro", uri=True, timeout=10)
        rows = con.execute("select model from session_model_usage where session_id=? and task='' order by first_seen",
                           (session_id,)).fetchall()
        con.close()
    except sqlite3.Error:
        return None
    models = list(dict.fromkeys(r[0] for r in rows if r[0]))
    return "，回退 ".join(models) if models else None


def generate(text, workdir, run=subprocess.run):
    """亚古兽单次写稿，返回 (回复, 会话 ID, 模型)。失败抛 GenError。"""
    q = Path(workdir) / "memdaily.query.md"
    q.write_text(text, encoding="utf-8")
    cmd = [HERMES, "-p", "agumon", "chat", "-Q", "--query-file", str(q), "-t", "todo", "--source", "tool",
           "--max-turns", "3", "--run-budget", str(BUDGET)] + (["--reasoning", REASONING] if REASONING else [])
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_BASE_URL", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    env.setdefault("HERMES_API_CALL_STALE_TIMEOUT", str(BUDGET))
    try:
        r = run(cmd, capture_output=True, text=True, timeout=BUDGET + 120, cwd=str(workdir), env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise GenError(f"亚古兽跑不起来（{type(e).__name__}）")
    sid = (re.findall(r"session_id:\s*(\S+)", r.stderr or "") or [None])[-1]
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        raise GenError(f"亚古兽退出码 {r.returncode}：{(tail[-1] if tail else '')[:120]}")
    return (r.stdout or "").strip(), sid, session_model(sid) if sid else None


def parse(text):
    body = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text.strip())
    start, end = body.find("{"), body.rfind("}")
    try:
        draft = json.loads(body[start:end + 1]) if start >= 0 else None
    except ValueError as e:
        raise GenError(f"回复不是合法 JSON（{getattr(e, 'msg', e)}）")
    if not isinstance(draft, dict):
        raise GenError("回复里没有 JSON 对象")
    return draft


def chat_times(facts):
    """{会话名: 出现过的 HH:MM}，私聊和群聊分开。"""
    def times(ms):
        return {m.get("create_time", "")[11:16] for m in ms}
    return ({p["name"]: times(p["messages"]) for p in facts.get("private") or []},
            {g["name"]: times(g["messages"]) for g in facts.get("groups") or []})


def match(name, known):
    """材料里对得上的名字（允许「许铭凯（Mingkai）」对「许铭凯」）。"""
    base = re.sub(r"[（(].*?[）)]|⭐", "", name).strip()
    return next((k for k in known if k == name or (base and (base in k or k in name))), None)


def to_hm(t):
    h, m = t.split(":")
    return f"{int(h):02d}:{m}"


def validate(draft, facts):
    """返回 (整理后的稿, 必须改的问题, 提醒)。问题不为空就不能用。"""
    problems, notes, out = [], [], {}
    lines = set(facts.get("work_lines") or []) | {"其他"}
    p2p_t, grp_t = chat_times(facts)
    summary = draft.get("summary")
    if not isinstance(summary, list) or not 1 <= len(summary) <= 5 or not all(isinstance(x, str) and x.strip() for x in summary):
        problems.append("summary 要 3～5 条非空字符串（当天没有工作往来时给一条「⚠️ 今日无工作互动记录」）")
    out["summary"] = [str(x).strip() for x in summary or [] if str(x).strip()][:5]
    for key, fields in FIELDS.items():
        rows = draft.get(key, [])
        if not isinstance(rows, list):
            problems.append(f"{key} 要是数组")
            rows = []
        kept = []
        for i, row in enumerate(rows, 1):
            where = f"{key} 第 {i} 行"
            if not isinstance(row, dict) or any(f not in row for f in fields):
                problems.append(f"{where} 缺字段（要有 {'、'.join(fields)}）")
                continue
            if PRIVATE.search(json.dumps(row, ensure_ascii=False)):
                notes.append(f"{where} 像是私生活内容，已删掉")
                continue
            r = {f: row[f] if f == "points" else str(row[f]).strip() for f in fields}
            if key in ("items", "actions") and r["status"] not in STATUS:
                problems.append(f"{where} 状态「{r['status']}」不在 {'/'.join(STATUS)} 里")
            if key == "items" and (not r["topic"].startswith("T线-") or r["topic"][3:] not in lines):
                problems.append(f"{where} 任务主题「{r['topic']}」要写成 T线-工作线名，工作线只能选材料里的")
            if key == "actions" and r["type"] not in ACTION_TYPES:
                problems.append(f"{where} 类型「{r['type']}」不在 {'/'.join(ACTION_TYPES)} 里")
            if key == "decisions" and r["status"] not in DECISION_STATUS:
                problems.append(f"{where} 状态「{r['status']}」不在 {'/'.join(DECISION_STATUS)} 里")
            if key == "insights" and r["type"] not in INSIGHT_TYPES:
                problems.append(f"{where} 类型「{r['type']}」不在 {'/'.join(INSIGHT_TYPES)} 里")
            if key in ("p2p", "groups"):
                known = p2p_t if key == "p2p" else grp_t
                name = r["party" if key == "p2p" else "name"]
                hit = match(name, known)
                if not hit:
                    problems.append(f"{where}「{name}」不是材料里的{'私聊对象' if key == 'p2p' else '群'}")
                pts = r["points"]
                if not isinstance(pts, list) or not 1 <= len(pts) <= 5:
                    problems.append(f"{where} points 要 1～5 条")
                    pts = []
                clean = []
                for j, p in enumerate(pts, 1):
                    p = re.sub(r"^\s*(?:[0-9]️⃣|🔟|\d+[.、])\s*", "", str(p)).strip()
                    t = TIME.match(p)
                    if not t:
                        problems.append(f"{where} 第 {j} 个议题没有以时间开头")
                    elif hit and any(to_hm(x) not in known[hit] for x in t.groups() if x):
                        problems.append(f"{where} 第 {j} 个议题的时间 {t.group(0).strip()} 在材料这段对话里找不到")
                    clean.append(p)
                r["points"] = clean
            if any(not (v if isinstance(v, list) else str(v).strip()) for v in r.values()):
                problems.append(f"{where} 有空字段（没有就写「—」或「待定」）")
            kept.append(r)
        out[key] = kept
    out["skipped_meetings"] = str(draft.get("skipped_meetings") or "").strip()
    return out, problems, notes


# ---------- 排版 ----------

def esc(s):
    return html.escape(str(s), quote=True)


def cell(v):
    return f'<td vertical-align="top"><p>{esc(v)}</p></td>'


def points_cell(points):
    return '<td vertical-align="top">' + "".join(f"<p>{EMOJI[min(i, 9)]} {esc(p)}</p>" for i, p in enumerate(points)) + "</td>"


def table(key, rows, bold=False):
    heads, widths = TABLES[key]
    th = "".join(f'<th vertical-align="top"><h4>{"<b>" + h + "</b>" if bold else h}</h4></th>' for h in heads)
    cols = "".join(f'<col width="{w}"/>' for w in widths)
    return f"<table><colgroup>{cols}</colgroup><thead><tr>{th}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def render(draft, facts, meta):
    """按 7/15 永久模板排成 DocxXML；序号、D/I 编号在这里加。"""
    day = facts["date"].replace("-", "")
    x = [f"<h2>{esc(facts['title'])}</h2>",
         f'<p><span text-color="gray">生成：亚古兽（{esc(meta.get("model") or "模型查不到")}）｜接入：Hermes 派单｜{esc(meta.get("time") or now())}</span></p>']

    def block(key, rows, empty, bold=False):
        x.append(table(key, rows, bold) if rows else f"<p>{esc(empty)}</p>")

    x.append("<h3>一、📋 今日事项</h3>")
    block("items", [f"<tr>{cell(i)}{cell(r['name'])}{cell(r['status'])}{cell(r['people'])}{cell(r['source'])}{cell(r['topic'])}</tr>"
                    for i, r in enumerate(draft["items"], 1)], "⚠️ 今日无工作互动记录", bold=True)
    x.append("<h3>二、💬 沟通记录</h3><h4>① 私聊摘录</h4>")
    block("p2p", [f"<tr>{cell(i)}{cell(r['party'])}{points_cell(r['points'])}{cell(r['conclusion'])}</tr>"
                  for i, r in enumerate(draft["p2p"], 1)], "（今日无工作相关私聊）")
    x.append("<h4>② 群聊摘录</h4>")
    white = {g["name"] for g in facts.get("groups") or [] if g.get("whitelist")}
    block("groups", [f"<tr>{cell(i)}{cell(r['name'] + (' ⭐' if match(r['name'], white) and '⭐' not in r['name'] else ''))}"
                     f"{points_cell(r['points'])}{cell(r['conclusion'])}</tr>" for i, r in enumerate(draft["groups"], 1)],
          "（今日无工作相关群聊）")
    x.append("<h4>③ 会议纪要</h4>")
    block("meetings", [f"<tr>{cell(i)}{cell(r['time'])}{cell(r['name'])}{cell(r['people'])}{cell(r['content'])}</tr>"
                       for i, r in enumerate(draft["meetings"], 1)], "（今日无有参会证据的会议）")
    if draft.get("skipped_meetings"):
        x.append(f"<p>{esc(draft['skipped_meetings'])}</p>")
    x.append("<h3>三、🧠 决策 &amp; 洞察</h3><h4>① 关键决策</h4>")
    block("decisions", [f"<tr>{cell(f'D-{day}-{i}')}{cell(r['item'])}{cell(r['choice'])}{cell(r['background'])}{cell(r['logic'])}"
                        f"{cell(r['exclude'])}{cell(r['status'])}</tr>" for i, r in enumerate(draft["decisions"], 1)], "（今日无关键决策）")
    x.append("<h4>② 核心洞察</h4>")
    block("insights", [f"<tr>{cell(f'I-{day}-{i}')}{cell(r['type'])}{cell(r['content'])}</tr>"
                       for i, r in enumerate(draft["insights"], 1)], "（今日无核心洞察）")
    x.append("<h3>四、⚡ 行动 &amp; 阻塞</h3>")
    block("actions", [f"<tr>{cell(i)}{cell(r['type'])}{cell(r['task'])}{cell(r['owner'])}{cell(r['due'])}{cell(r['status'])}</tr>"
                      for i, r in enumerate(draft["actions"], 1)], "（今日无行动与阻塞）")
    x.append("<h3>五、📚 方法论沉淀</h3>")
    block("methods", [f"<tr>{cell(i)}{cell(r['scene'])}{cell(r['trace'])}{cell(r['model'])}{cell(r['definition'])}{cell('⏳ 待确认')}</tr>"
                      for i, r in enumerate(draft["methods"], 1)], "（今日无新增方法论）")
    x.append("<h3>六、💡 AI 建议</h3>")
    block("suggestions", [f"<tr>{cell(i)}{cell(r['type'])}{cell(r['content'])}{cell(r['basis'])}</tr>"
                          for i, r in enumerate(draft["suggestions"], 1)], "（今日无新增高价值建议，跳过。）")
    return "".join(x)


def table_count(draft):
    return sum(1 for k in TABLES if draft.get(k))


# ---------- 三步 ----------

def build(d, collect=md.collect, gen=generate):
    """取数、写稿、核对、排版。返回 build 记录；skip 时不写稿。核对不过重写一次，再不过抛 GenError。"""
    DIR.mkdir(parents=True, exist_ok=True)
    p = paths(d)
    facts = collect(dt.date.fromisoformat(d))
    if facts.get("skip"):
        rec = {"date": d, "skip": facts["skip"], "time": now()}
        save_json(p["build.json"], rec)
        return rec
    problems, rec = None, {}
    for attempt in (1, 2):
        text, sid, model = gen(query(facts, problems), DIR)
        p["reply.txt"].write_text(text, encoding="utf-8")
        try:
            draft, problems, notes = validate(parse(text), facts)
        except GenError as e:
            draft, problems, notes = None, [str(e)], []
        rec = {"date": d, "attempt": attempt, "session": sid, "model": model, "time": now(), "problems": problems, "notes": notes}
        if not problems:
            break
    save_json(p["build.json"], rec)
    if problems:
        raise GenError(f"两次写稿都没通过核对：{'；'.join(problems[:3])}")
    save_json(p["draft.json"], draft)
    p["xml"].write_text(render(draft, facts, rec), encoding="utf-8")
    rec["tables"] = table_count(draft)
    save_json(p["build.json"], rec)
    return rec


def backup(d, doc, xml):
    b = DIR / "backup"
    b.mkdir(parents=True, exist_ok=True)
    (b / f"{d}.{doc}.before.xml").write_text(xml, encoding="utf-8")
    olds = sorted(b.glob("*.before.xml"), key=lambda f: f.stat().st_mtime)
    for f in olds[:-KEEP_BACKUPS]:
        f.unlink()


def fetch_xml(doc, run):
    return (run("docs", "+fetch", "--doc", doc, "--doc-format", "xml") or {}).get("document", {}).get("content", "")


def first_block(xml, title):
    """文档第一个 <h2> 到第二个 <h2> 之间；第一个 h2 不是 title 返回 None。"""
    ms = list(re.finditer(r"<h2[^>]*>(.*?)</h2>", xml))
    if not ms or html.unescape(re.sub(r"<[^>]+>", "", ms[0].group(1))).strip() != title:
        return None
    return xml[ms[0].start(): ms[1].start() if len(ms) > 1 else len(xml)]


def verify(doc, title, tables, run):
    """写后回读：顶部是这一篇、表格数对、没有空壳单元格、第一张表列宽对。返回问题列表。"""
    seg = first_block(fetch_xml(doc, run), title)
    if seg is None:
        return ["回读时文档第一篇不是这一天"]
    issues = []
    if seg.count("<table") != tables:
        issues.append(f"表格数 {seg.count('<table')}，应为 {tables}")
    if re.search(r'<td[^>]*><p></p></td>', seg):
        issues.append("有空单元格（可能又成了空壳表格）")
    blocks = ((run("api", "GET", f"/open-apis/docx/v1/documents/{doc}/blocks", "--params", '{"page_size":100}') or {}).get("items") or [])
    first = next((b for b in blocks if b.get("block_type") == 31), None)
    if tables and first and tuple((first.get("table") or {}).get("property", {}).get("column_width") or ()) not in {w for _, w in TABLES.values()}:
        issues.append("第一张表的列宽不对")
    return issues


def write(d, doc=md.DOC, run=md.lark):
    """插到文档最顶部并回读。返回 'written' / 'exists'；回读不过抛 GenError（不重试，免得写两遍）。"""
    p = paths(d)
    rec = read_json(p["build.json"], {})
    if not p["xml"].exists():
        raise GenError(f"{d} 还没有排好的稿（先跑 build）")
    title = f"📅 {md.wd(dt.date.fromisoformat(d))}"
    xml = fetch_xml(doc, run)
    if re.search(rf"<h2[^>]*>[^<]*{re.escape(d)}", xml):
        return "exists"
    backup(d, doc, xml)
    run("docs", "+update", "--doc", doc, "--command", "block_insert_after", "--block-id", "0",
        "--content", f"@{p['xml'].name}", cwd=str(DIR), tries=1)
    issues = verify(doc, title, rec.get("tables", 0), run)
    deliv = read_json(p["delivery.json"], {})
    deliv.update({"doc": doc, "written_at": now(), "read_back": not issues, "issues": issues})
    save_json(p["delivery.json"], deliv)
    if issues:
        raise GenError(f"写进去了但回读不对：{'；'.join(issues)}（写前快照在 memdaily/backup/）")
    return "written"


def push_text(d, doc, draft, rec):
    lines = [f"📅 {d} 日报已写入", f"飞书文档：{DOC_URL.format(doc)}", "今日关键摘要："]
    lines += [f"{EMOJI[i]} {s}" for i, s in enumerate(draft.get("summary") or [])]
    lines.append(f"生成：亚古兽（{rec.get('model') or '模型查不到'}）｜接入：Hermes 派单")
    return "\n".join(lines)


def push(d, doc=md.DOC, run=md.lark):
    """亚古兽机器人私聊常浩；同一天只发一次（idempotency key + delivery.json）。"""
    p = paths(d)
    deliv = read_json(p["delivery.json"], {})
    if deliv.get("pushed_at"):
        return "pushed-before"
    text = push_text(d, doc, read_json(p["draft.json"], {}), read_json(p["build.json"], {}))
    run("im", "+messages-send", "--user-id", md.ME, "--text", text, "--idempotency-key", f"memdaily-{d}-{doc[-8:]}", identity="bot")
    deliv["pushed_at"] = now()
    save_json(p["delivery.json"], deliv)
    return "pushed"


def run_all(d, doc=md.DOC, do_push=True):
    """返回一句话结果；失败抛 GenError / md.Fail。"""
    rec = build(d)
    if rec.get("skip"):
        return f"跳过：{rec['skip']}"
    state = write(d, doc)
    if do_push:
        push(d, doc)
    return f"{d} {'已写入' if state == 'written' else '文档里已有，没重写'}{'并推送' if do_push else ''}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("build", "write", "push", "run"))
    ap.add_argument("--date", required=True)
    ap.add_argument("--doc", default=md.DOC, help="目标文档 token（出样稿时填测试文档）")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    try:
        if a.cmd == "build":
            print(json.dumps(build(a.date), ensure_ascii=False))
        elif a.cmd == "write":
            print(write(a.date, a.doc))
        elif a.cmd == "push":
            print(push(a.date, a.doc))
        else:
            print(run_all(a.date, a.doc, not a.no_push))
    except (GenError, md.Fail) as e:
        print(f"记忆日报 {a.date} 没写成：{e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
