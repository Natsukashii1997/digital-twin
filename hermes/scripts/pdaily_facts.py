"""个人项目日报事实快照（#110 批 1c）：把 1a 的项目记录（pdaily_data）和 1b 的代理行动（actions）合成一份统计日的事实快照，
给 #111 的 Hermes 写卡片和完整报告用。只读取数，只写 DigitalTwin/daily/<统计日>.facts.json 这一个文件。

规格：docs/个人项目日报-V2-设计.md 第 4 节（各段要什么）、5.1（项目记录）、5.5（拍板事项）、5.6（观察池）、第 6 节（证据）。
用法：python3 pdaily_facts.py [--date 统计日] [--dry-run]
  默认写 DigitalTwin/daily/<统计日>.facts.json 并打印路径和条数；--dry-run 只把 JSON 打到 stdout，不写文件。
约定（常浩 2026-11-09 定）：
  - 只给候选和出处，不定级、不排序、不给推荐：拍板候选的「类型」「推荐」「不决定的影响」、风险的「等级」都留空（null），
    由 #111 的 Hermes 写稿时判断；和 1a「甲：只摘原文」同一个思路。
  - Hermes 对话记录（受阻、超出授权的回复）不接：要读常浩的私聊，属受限信息；覆盖里写「没接」。
  - 观察池不存状态：每次往回扫 14 天现算（失败、合并官不一致），历史日期能回放；新坑只列统计日内新加的；
    采纳后的效果跟踪以后另做。
取不到的来源写进 coverage，不当成 0；一个来源出错不拖垮整份快照。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import actions  # noqa: E402
import openloop  # noqa: E402  未闭环账本：读某天的快照、分「等你拍板」的行（5a-3b）
import pdaily_data  # noqa: E402
import progress_data  # noqa: E402

TZ = pdaily_data.TZ
DAILY = Path(os.environ.get("DT_DAILY", "~/Library/Application Support/DigitalTwin/daily")).expanduser()
LOOKBACK = dt.timedelta(days=14)    # 观察池往回看多久
OPEN_PR_DAYS = 2                    # PR 开着超过这么多天算协作阻塞候选
IDLE_DAYS = 7                       # 活跃项目这么多天没有动作算管理失真（4.4）
DIRTY_MANY = 10                     # 主目录未提交文件到这么多，列「待识别」（4.5：先识别性质，不直接算高风险）
LEDGER_DECISIONS = 5                # 未闭环账本来的拍板候选每天最多几件（常浩 2026-11-21 定）
LEDGER_EXAMPLES = 5                 # 账本那一行风险带几件卡得最久的当例子
EPOCH = dt.datetime(2000, 1, 1, tzinfo=TZ)
ENTRY = re.compile(r"^\+## [LG]-\d+ ")
DROP = re.compile(r"^-## ([LG]-\d+) ")
NOT_CONNECTED = [
    "Hermes 对话记录（受阻、超出授权的回复）：没接，要读常浩私聊（常浩 2026-11-09 定）",
    "交付风险：各项目没有里程碑期限和验收记录，取不到延期、验收失败",
    "安装记录：只认 actions.py record(kind=install) 写的，现在还没有脚本写",
    "跨项目引用：只扫合并 PR 的标题和正文里写了编号的原句、开着的 PR 的标题；issue 正文没扫",
]


def cand(kind, project_id, item, why, source, evidence=None, **extra):
    """一条拍板候选（5.5 的事实部分）。type、recommendation、impact、reply（最短回复）留给 Hermes 判断；
    deadline 现在没有来源能给，留 None。"""
    return {"kind": kind, "project_id": project_id, "item": item, "why_owner": why, "source": source,
            "evidence": evidence or [], "type": None, "recommendation": None, "impact": None, "reply": None,
            "deadline": extra.get("deadline"), "status": "待你决定", "since": extra.get("since")}


def risk(category, project_id, item, evidence, level=None):
    """一条风险候选（4.5）。level 只在「待识别」时预填，其余由 Hermes 定级。"""
    return {"category": category, "project_id": project_id, "item": item, "evidence": evidence, "level": level}


def age_days(ts, now):
    return (now - pdaily_data.parse_ts(ts)).total_seconds() / 86400


# ---------- 拍板候选 ----------

def decisions(w, recs, acts, notes):
    out = []
    wide = (EPOCH, w[1])                      # 待决的事是截至统计日结束的状态，不只看当天新出现的
    try:
        for rec, _ in actions.merge_officer(wide, []):   # 宽窗口读到的历史坏行每天都一样，不进 notes
            if rec["follow_up"] == "常浩决定合不合并":
                out.append(cand("merge", rec["project_id"], rec["request"], "合并官判「问常浩」或 L3，常浩还没点卡片",
                                "shadow/agreement.csv", [rec["verification"]], since=rec["ts"]))
    except Exception as e:   # 一个来源出错：写进 notes，其余候选照出
        notes.append(f"拍板候选：合并官读不了：{e}")
    try:
        for rec, _ in actions.dispatch(wide, []):
            # 等确认开跑是当前状态，照列；回执里的「要常浩决定」只收统计日内开跑的（老回执多半早处理了，查不到）
            fresh = w[0] <= pdaily_data.parse_ts(rec["ts"]) < w[1]
            if rec["follow_up"] == "常浩确认开跑或取消" or (fresh and (rec["follow_up"] or "").startswith("常浩决定：")):
                out.append(cand("dispatch", rec["project_id"], rec["request"],
                                "写入单要常浩确认开跑" if rec["follow_up"] == "常浩确认开跑或取消" else "车间回执写了要常浩决定",
                                f"派单台账 {rec['action_id']}", [rec["verification"], rec["follow_up"]], since=rec["ts"]))
    except Exception as e:
        notes.append(f"拍板候选：派单台账读不了：{e}")
    out.extend(proposals(w, notes))
    for a in acts:
        if a["auth_gap"]:
            out.append(cand("auth", a["project_id"], a["request"], f"执行了但{a['auth_gap']}：请核对当时的授权",
                            f"{a['origin']} {a['action_id']}", [a["auth_source"] or "", a["verification"] or ""], since=a["ts"]))
        elif a["follow_up"] == "常浩处理受阻的子单":
            out.append(cand("blocked", a["project_id"], a["request"], "夜跑子单受阻，要常浩处理",
                            f"{a['origin']} {a['action_id']}", [a["verification"] or ""], since=a["ts"]))
    for r in recs:
        st = r["current_state"]
        if st.get("next_step") and "常浩" in st["next_step"] and st.get("next_reliable") is not False:   # 过期的不当依据（第 6 节）
            out.append(cand("current", r["project_id"], st["next_step"],
                            "CURRENT 的下一步原文点名常浩（原句摘录，不判断）",
                            f"CURRENT「{st.get('top_section') or ''}」", [f"下一步出处：{st.get('next_source')}",
                                                                     f"可信：{st.get('next_reliable')}"]))
    return out


def proposals(w, notes):
    """待决提案：DigitalTwin/proposals/digest.json 里统计日结束前最近一期、到统计日结束还没回复的条目。
    proposal_digest.py 不回写条目的 decided：常浩的回复记在这一期的 answers[].applied（{序号: 采纳 / 否决 / 待定}），
    回过的（包括待定，下周会再列一期）都不算待决。每期会把上期没定的重列，所以只看最近一期，按 key 去重。"""
    f = Path(os.environ.get("DT_PROPOSALS", "~/Library/Application Support/DigitalTwin/proposals")).expanduser() / "digest.json"
    if not f.exists():
        return []
    try:
        issues = [i for i in json.loads(f.read_text(encoding="utf-8"))["issues"]
                  if i.get("date") and i["date"] < w[1].date().isoformat()]   # 每周一 10:00 发，统计日结束后才发的期不算
        if not issues:
            return []
        iss = max(issues, key=lambda i: i["date"])
        answered = set()
        for a in iss.get("answers") or []:
            if a.get("at") and pdaily_data.parse_ts(a["at"]) < w[1]:
                answered.update(str(n) for n in (a.get("applied") or {}))
        out, seen = [], set()
        for it in iss.get("items") or []:
            key = it.get("key") or it.get("id")
            if str(it.get("n")) in answered or key in seen:
                continue
            seen.add(key)
            out.append(cand("proposal", "hermes", it.get("title"), "待决提案要常浩回复（每周收口）",
                            f"proposals/digest.json 第 {iss.get('no')} 期第 {it.get('n')} 条 {it.get('id')}",
                            [f"Hermes 推荐：{it.get('rec')}（{it.get('why')}）" if it.get("rec") else "还没有推荐",
                             it.get("file") or ""], since=iss.get("date")))
        return out
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        notes.append(f"拍板候选：待决提案读不了：{e}")
        return []


# ---------- 风险四类 ----------

def risks(w, recs, acts, coverage):
    out = []
    for name, c in coverage["actions"].items():
        if not c["ok"]:
            out.append(risk("运行风险", "hermes", f"行动来源「{name}」取不到", [c["error"]]))
    for pid, errs in coverage["projects"]["errors"].items():
        out.append(risk("运行风险", pid, "项目取数有失败", errs))
    for a in acts:
        if a["result"] == "失败":
            cat = "协作阻塞" if a["kind"] in ("dispatch", "refuse") else "运行风险"
            out.append(risk(cat, a["project_id"], a["request"], [a["verification"] or "", f"{a['origin']} {a['action_id']}"]))
        elif a["kind"] == "supervise" and a["follow_up"] == "处理这张故障单":
            out.append(risk("运行风险", a["project_id"], a["request"], [a["verification"] or ""]))
        elif (a["kind"] == "dispatch" and a["result"] == "不明" and a["origin"] == "派单台账"
              and (a["verification"] or "").startswith("没有回执")):   # 没开跑就放弃的写入单也是「不明」，不算
            out.append(risk("协作阻塞", a["project_id"], a["request"], [a["verification"], "没有回执"]))
        elif a["follow_up"] == "核对主分支、远端的改动":
            out.append(risk("运行风险", a["project_id"], a["request"], [a["verification"] or "", "派单护栏报警"]))
    for r in recs:
        pid, st, fr, obj = r["project_id"], r["current_state"], r["freshness"], r.get("objective") or {}
        paused = obj.get("kind") == "暂停"
        for pr in st.get("open_prs") or []:
            if pr.get("created") and age_days(pr["created"], w[1]) > OPEN_PR_DAYS:
                out.append(risk("协作阻塞", pid, f"PR #{pr['number']} 开着超过 {OPEN_PR_DAYS} 天：{pr['title']}",
                                [f"开于 {pr['created']}"]))
        if fr.get("stale"):
            out.append(risk("管理失真", pid, "CURRENT 过期：下一步不当可信依据",
                            [f"CURRENT 最近一节 {fr.get('top_section_date')}，主分支最近一次合并 {fr.get('last_merge')}"]))
        if obj.get("kind") in ("未设", "未知"):
            out.append(risk("管理失真", pid, f"阶段目标{obj.get('kind')}", [obj.get("source") or "CURRENT / README 文件头"]))
        last = max([t for t in (fr.get("last_merge"), fr.get("current_last_commit")) if t], default=None,
                   key=pdaily_data.parse_ts)
        if not paused and last and age_days(last, w[1]) >= IDLE_DAYS:
            out.append(risk("管理失真", pid, f"{IDLE_DAYS} 天以上没有新动作", [f"主分支最近一次提交 {last}"]))
        if st.get("checkout_branch") and st["checkout_branch"] != r.get("main_branch", "main") and not paused:
            out.append(risk("管理失真", pid, f"主目录检出的是 {st['checkout_branch']}，不是主分支", ["本机 git"], "待识别"))
        if (st.get("dirty_files") or 0) >= DIRTY_MANY:
            out.append(risk("管理失真", pid, f"主目录有 {st['dirty_files']} 处未提交改动", ["本机 git status"], "待识别"))
    return out


# ---------- 未闭环账本来的候选（#288，批 5a-3b；规格 docs/未闭环账本-规格.md 第 8 节） ----------

def from_ledger(day, w, dec, projects, notes):
    """读统计日那天的账本快照（openloop.py 写的 history/<日期>.json），返回（拍板候选，风险）。常浩 2026-11-21 定：
    拍板候选每天最多 LEDGER_DECISIONS 件，挂得久的先；别的来源已经列了同一个 PR 的不重复；其余只在 notes 里说还有几件。
    风险不逐件放：卡住和到点没动静的合成一行，带最久的几件当例子。
    账本目录都没有 = 还没装，不算缺口；装了但这天没有快照 = 扫描没跑成，要说出来（规格第 14 节）。"""
    folder = openloop.out_dir()
    if not folder.exists():
        return [], []
    led = openloop.day_ledger(day)
    if led is None:
        notes.append(f"未闭环账本：{day} 这天没有账本快照（扫描没跑成），账本来的候选这次没有")
        return [], [risk("运行风险", "hermes", "未闭环账本这天没更新", [f"{folder / 'history' / f'{day}.json'} 不存在或读不了"])]
    ids = {openloop.key_of(p): p["id"] for p in projects}      # 账本里项目用仓库名，日报用名册 id
    listed = {(c.get("project_id"), n) for c in dec or []
              for n in re.findall(r"#(\d+)", " ".join(str(x) for x in (c.get("item"), c.get("source"), *(c.get("evidence") or []))))}

    def repeated(r):
        for ref in [r.get("id")] + list(r.get("refs") or []):   # 新通道派单并进 PR 行时，这一行自己就是 pr:…
            m = re.match(r"pr:(.+)#(\d+)$", str(ref))
            if m and (ids.get(m.group(1)), m.group(2)) in listed:
                return True
        return False

    DECIDE_WHY = {"dispatch": "账本里等常浩拍板（新通道派单：冻结了恢复还是撤销、PR 合并了收不收掉、PR 关了续接还是停单）",
                  None: "账本里等常浩拍板（会话停在〔等你决定〕，或主线清单的 ❓）"}

    def title(r):   # 主线清单的项常常只有几个字（「常浩决定」），带上是哪条主线
        m = re.match(r"mainline:[^/]+/([^/]+)/", str(r.get("id") or ""))
        return f"{m.group(1)}：{r.get('what')}" if m else r.get("what")
    decide = openloop.waiting(led)[1]
    fresh = sorted((r for r in decide if not repeated(r)), key=lambda r: r.get("evidence_at") or "")
    cands = [cand("openloop", ids.get(r.get("project")) or "hermes", title(r), DECIDE_WHY["dispatch" if r.get("ask") else None],   # 只有新通道派单的行带 ask（并进 PR 行时也跟着它）
                  f"未闭环账本 {r.get('id')}", [r.get("evidence") or "", f"下一步：{r.get('next') or ''}"], since=r.get("evidence_at"))
             for r in fresh[:LEDGER_DECISIONS]]
    if len(decide) > len(fresh):
        notes.append(f"拍板候选：未闭环账本里有 {len(decide) - len(fresh)} 件和别的来源是同一个 PR，没重复列")
    if len(fresh) > LEDGER_DECISIONS:
        notes.append(f"拍板候选：未闭环账本另有 {len(fresh) - LEDGER_DECISIONS} 件等常浩拍板没列（每天最多 {LEDGER_DECISIONS} 件，挂得久的先）")
    rows = led.get("rows") or []
    stuck, stalled = [r for r in rows if r.get("state") == "卡住"], [r for r in rows if r.get("stalled")]
    if not stuck and not stalled:
        return cands, []
    worst = sorted(stuck, key=lambda r: r.get("evidence_at") or "")[:LEDGER_EXAMPLES]
    return cands, [risk("协作阻塞", "hermes", f"未闭环账本：卡住 {len(stuck)} 件、到点没动静（停了）{len(stalled)} 件",
                        [f"{r.get('project') or '—'}｜{openloop.clip(r.get('what'), 40)}｜卡在：{openloop.clip(r.get('blocked'), 60) or '没写'}" for r in worst]
                        + [f"账本 {led.get('generated_at')} 扫描；明细：openloop.py show"])]


# ---------- 跨项目引用（4.5：只认明文） ----------

def snippet(line, m, n=100):
    """引用前后各 n 字的原文摘录：长句只留开头的话，后面的编号会被截掉，证据对不上。"""
    a, b = max(0, m.start() - n), min(len(line), m.end() + n)
    return ("…" if a else "") + line[a:b] + ("…" if b < len(line) else "")


def cross_refs(w, recs, projects):
    """合并 PR 的标题和正文、开着的 PR 的标题里，明文写了名册里别的仓库编号的（niuma-home#31、github-center #83、
    owner/repo#31），各记一条，并查被引用的那张现在关没关（to_closed：True / False，取不到 None）。
    不推测依赖，不建图谱；「这边已合并、那边还开着」这类状态冲突影不影响另一条主线，由 Hermes 判断。"""
    names, repos = {}, {}
    for p in projects:
        gh = p.get("github") or ""
        if p.get("id") and gh.count("/") == 1:          # 名册写错（不是「所有者/仓库」）的跳过，不拖垮整份
            names[gh.split("/")[1].lower()] = p["id"]
            repos[p["id"]] = gh
    if not names:
        return []
    # 边界只排 ASCII：「配合niuma-home#31」这种中文紧贴的也要认（\w 会把汉字当词字符）
    pat = re.compile(r"(?<![A-Za-z0-9_/.-])(?:([A-Za-z0-9_.-]+)/)?(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                     + r") ?#(\d+)(?![0-9])", re.I)    # 右边也不用 \b：「niuma-home#31的接口」要认
    out, seen = [], set()
    for r in recs:
        texts = [(f"合并的 PR #{pr['number']}", "已合并", "\n".join(pr.get("ref_lines") or [pr.get("title") or ""]))
                 for pr in (r["yesterday_delta"].get("merged_prs") or [])]
        texts += [(f"开着的 PR #{pr['number']}", "开着", pr.get("title") or "")
                  for pr in (r["current_state"].get("open_prs") or [])]
        for where, state, text in texts:
            for line in text.splitlines():
                for m in pat.finditer(line):
                    to = names[m.group(2).lower()]
                    if m.group(1) and m.group(1).lower() != repos[to].split("/")[0].lower():
                        continue                        # 别人 fork 的同名仓库不算
                    key = (r["project_id"], where, to, m.group(3))
                    if to == r["project_id"] or key in seen:
                        continue
                    seen.add(key)
                    out.append({"from_project": r["project_id"], "where": where, "from_state": state, "to_project": to,
                                "to_ref": f"{m.group(2)}#{m.group(3)}", "to_number": int(m.group(3)), "to_closed": None,
                                "quote": snippet(line, m)})
    hub = pdaily_data.GitHub(w)
    for to in sorted({x["to_project"] for x in out}):
        owner, name = repos[to].split("/", 1)
        mine = [x for x in out if x["to_project"] == to]
        states = hub.states(owner, name, sorted({x["to_number"] for x in mine})) or {}   # 取不到：留 None，不当成开着
        for x in mine:
            x["to_closed"] = states.get(x["to_number"])
    return out


# ---------- 观察池（5.6：每次往回扫 14 天现算，不存状态） ----------

def observations(w, projects, notes):
    span = (w[1] - LOOKBACK, w[1])
    out = []
    try:
        out.extend(new_lessons(w, projects, notes))   # 新坑只列统计日内新加的：14 天窗口会把整份新建的 LESSONS 都算进来
    except Exception as e:
        notes.append(f"观察池：LESSONS 读不了：{e}")
    try:
        out.extend(officer_misses(span))
    except Exception as e:
        notes.append(f"观察池：合并官记录读不了：{e}")
    try:
        fails = {}
        for name, fn in (("定时任务库", actions.cron), ("派单台账", actions.dispatch)):
            for rec, _ in fn(span, []):
                if rec["result"] == "失败":
                    # 定时任务按任务号归类，同一任务失败 ≥2 次才算重复；派单每张单是不同的事，各记一次，重不重复由 Hermes 读原因判断
                    key = (name, job_id(rec) if name == "定时任务库" else f"{rec['project_id']} {rec['action_id']}")
                    fails.setdefault(key, []).append(rec)
        for (name, what), rs in fails.items():
            out.append({"kind": "重复失败" if len(rs) >= 2 else "失败", "what": f"{name}：{what}",
                        "count": len(rs), "first": min((r["ts"] for r in rs), key=pdaily_data.parse_ts),
                        "last": max((r["ts"] for r in rs), key=pdaily_data.parse_ts),
                        "evidence": [r["action_id"] for r in rs][:10], "threshold_met": len(rs) >= 2})
    except Exception as e:
        notes.append(f"观察池：失败记录读不了：{e}")
    return out


JOB = re.compile(r"（([^（），]+)(?:，[^（）]*)?）$")


def job_id(rec):
    """定时任务行动按任务号归类：request 是「定时任务「名」（job_id）」，任务名可能重名、改名，任务号不变。"""
    m = JOB.search(rec["request"] or "")
    return m.group(1) if m else rec["request"]


def new_lessons(span, projects, notes):
    """统计日内各项目 LESSONS 新加的条目（主分支上 +## L-xx / G-xx 标题行）；agent-kit 的是全局坑。一条新坑本身不够提案门槛，
    同一问题在 ≥2 个项目或任务里出现才够，这要 Hermes 读内容判断，这里只列出来。
    同一提交里同号的条目又有删除行的（改标题、调顺序、从根目录挪到 docs/），不算新坑。"""
    out = []
    names = ("docs/LESSONS.md", "LESSONS.md")
    for p in projects:
        if not p.get("path"):
            continue                                    # 没登记目录：不能拿当前目录顶替
        root = Path(p["path"]).expanduser()
        if not any((root / n).exists() for n in names):
            continue
        try:
            ref = pdaily_data.main_ref(root, p.get("main") or "main")
            if not ref:
                raise pdaily_data.Fail("找不到主分支")
            r = subprocess.run(["git", "-C", str(root), "log", "--first-parent", "-p", "--format=@@%cI",
                                f"--since={span[0].isoformat()}", f"--until={span[1].isoformat()}", ref, "--", *names],
                               capture_output=True, text=True, env=pdaily_data.git_env())
            if r.returncode:
                raise pdaily_data.Fail((r.stderr.strip().splitlines() or [f"git 退出码 {r.returncode}"])[-1])
        except (pdaily_data.Fail, OSError, ValueError) as e:   # ValueError：输出不是 UTF-8
            notes.append(f"观察池：{p.get('id')} 的 LESSONS 读不了：{e}")
            continue
        commits, cur, fname = [], None, None
        for line in r.stdout.splitlines():
            if line.startswith("@@") and not line.startswith("@@ "):
                cur = {"when": line[2:], "added": [], "removed": set()}
                commits.append(cur)
            elif line.startswith("+++ "):
                fname = line[6:] if line.startswith("+++ b/") else fname
            elif cur and ENTRY.match(line) and "<一句话现象>" not in line:   # 模板标题和占位不算
                cur["added"].append((fname, line[4:].strip()))
            elif cur and DROP.match(line):
                cur["removed"].add(DROP.match(line).group(1))
        for c in commits:
            if not pdaily_data.inside(c["when"], span):   # git 的 --until 含右端，窗口右端 04:00 整的提交在这里剔掉
                continue
            for fn, title in c["added"]:
                if title.split(" ", 1)[0] in c["removed"]:
                    continue
                out.append({"kind": "新坑", "what": f"{p['id']} {fn}：{title}", "count": 1,
                            "first": c["when"], "last": c["when"], "evidence": [f"{p['id']}:{fn}"], "threshold_met": False})
    return out


def officer_misses(span):
    """合并官判断和真实结局对不上的（agreement.csv 的 agree=否），按「判了什么 → 结局」归类计数；够 2 次标可升提案。
    按策略合并的（owner=策略合并，#257）不算：合并官判「退回」的第一版不拦，那个结局是策略自己促成的，不是常浩推翻了判断。"""
    f = actions.SHADOW / "agreement.csv"
    if not f.exists():
        return []
    import csv
    groups = {}
    with f.open(encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh):
            if (r.get("agree") != "否" or r.get("source") != "live" or not r.get("outcome_at")
                    or r.get("owner") == actions.POLICY_OWNER):
                continue
            try:
                r["outcome_at"] = actions.stamp(r["outcome_at"])     # 统一成北京时间，first / last 才能直接比
                at = pdaily_data.parse_ts(r["outcome_at"])
            except (ValueError, TypeError):
                continue
            if span[0] <= at < span[1]:
                groups.setdefault((r.get("tier"), r.get("suggestion"), r.get("outcome")), []).append(r)
    return [{"kind": "合并官和结局不一致", "what": f"{tier} 判「{sug}」，结局是「{res}」", "count": len(rs),
             "first": min(r["outcome_at"] for r in rs), "last": max(r["outcome_at"] for r in rs),
             "evidence": [f"{r['repo']}#{r['pr']}" for r in rs][:10], "threshold_met": len(rs) >= 2}
            for (tier, sug, res), rs in groups.items()]


# ---------- 汇总 ----------

def attach(recs, acts, projects):
    """行动按 project_id 挂到项目；名册之外的（hermes 自己的定时任务、夜跑、裁决）单列。安装记录接到交付。"""
    ids = {r["project_id"] for r in recs}
    mains = {p.get("id"): p.get("main") or "main" for p in projects}
    for r in recs:
        mine = [a for a in acts if a["project_id"] == r["project_id"]]
        r["main_branch"] = mains.get(r["project_id"], "main")
        r["actions"] = [a["action_id"] for a in mine]
        r["installs"] = [a["action_id"] for a in mine if a["kind"] == "install" and a["result"] == "业务已验证"]
        for pr in (r["yesterday_delta"].get("merged_prs") or []):
            if r["installs"]:
                pr["delivery_note"] = f"统计日内有 {len(r['installs'])} 条回读验证过的安装记录（见 installs），没按 PR 对应"
    return [a["action_id"] for a in acts if a["project_id"] not in ids]


def build(day, projects, gh_wait=0):
    w = pdaily_data.window(day)
    notes = []
    p1 = pdaily_data.collect(day, projects, gh_wait)
    p2 = actions.collect(day)
    recs, acts = p1["projects"], p2["actions"]
    failed = []

    def part(name, fn, *args):   # 一段出意外：这段记 None（未知）并写明，其余照出（第 6 节）
        try:
            return fn(*args)
        except Exception as e:
            failed.append(name)
            notes.append(f"{name}算不出来：{type(e).__name__}：{e}")
            return None
    others = part("行动挂到项目", attach, recs, acts, projects)
    coverage = {"projects": p1["coverage"], "actions": p2["coverage"], "action_notes": p2["notes"],
                "stale": [r["project_id"] for r in recs if r["freshness"].get("stale")],
                "not_connected": NOT_CONNECTED, "failed_parts": failed}
    dec = part("拍板候选", decisions, w, recs, acts, notes)
    rsk = part("风险", risks, w, recs, acts, coverage)
    loop = part("未闭环账本", from_ledger, day, w, dec, projects, notes)   # 账本来的拍板候选和一条风险（5a-3b）
    if loop and dec is not None:
        dec.extend(loop[0])
    if loop and rsk is not None:
        rsk.extend(loop[1])
    refs = part("跨项目引用", cross_refs, w, recs, projects)
    obs = part("观察池", observations, w, projects, notes)
    auto = part("按策略合并", actions.policy_book, w)     # 「替你合并了什么」的原料（#257）；账本还没有时三样都是空的
    ids = {r["project_id"] for r in recs}
    yd = lambda r, k: r["yesterday_delta"].get(k)
    unknown = [r["project_id"] for r in recs if any(yd(r, k) is None for k in ("merged_prs", "closed_issues", "commits"))]
    delta = [r["project_id"] for r in recs
             if yd(r, "merged_prs") or yd(r, "closed_issues") or any((yd(r, "commits") or {}).values())]

    def total(k, size=len):   # 有项目这一项取不到：合计写 None（第 6 节：不写成 0）
        vals = [yd(r, k) for r in recs]
        return None if any(v is None for v in vals) else sum(size(v) for v in vals)
    by_kind = {}
    for a in acts:
        by_kind[a["kind"]] = by_kind.get(a["kind"], 0) + 1
    return {
        "stat_day": day.isoformat(), "timezone": "Asia/Shanghai",
        "window": {"start": w[0].isoformat(), "end": w[1].isoformat()},
        "fetched_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
        "projects": recs,
        "actions": acts, "other_actions": others,
        "decisions": dec, "risks": rsk, "cross_refs": refs, "observations": obs,
        "auto_merges": auto,
        "counts": {   # 开头三个数的原料（4.1）：「实质推进」要不要算，由 Hermes 看成果判断
            "projects_with_delta": delta,
            "projects_delta_unknown": unknown,      # 增量有一项取不到的项目：不当成「没有新动作」
            "decision_candidates": None if dec is None else len(dec),
            "risk_projects": None if rsk is None else sorted({x["project_id"] for x in rsk} & ids),
            # 不归名册项目的风险：Hermes 自己的（来源取不到、定时任务），以及没对上项目的派单、merge-runs
            "non_project_risks": None if rsk is None else sum(1 for x in rsk if x["project_id"] not in ids),
        },
        "activity": {"merged_prs": total("merged_prs"), "closed_issues": total("closed_issues"),
                     "commits": total("commits", lambda c: sum(c.values())),
                     "actions_by_kind": by_kind},
        "coverage": coverage,
        "notes": notes,
    }


def write(facts):
    DAILY.mkdir(parents=True, exist_ok=True)
    f = DAILY / f"{facts['stat_day']}.facts.json"
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, f)        # 写完再换名：读的人不会读到半份
    return f


def main():
    p = argparse.ArgumentParser(description="个人项目日报事实快照：1a 项目记录 + 1b 代理行动 + 拍板候选、风险、覆盖")
    p.add_argument("--date", help="统计日 YYYY-MM-DD（D 04:00 到 D+1 04:00），默认最近一个已经结束的统计日")
    p.add_argument("--dry-run", action="store_true", help="只打印 JSON，不写文件")
    a = p.parse_args()
    try:
        day = dt.date.fromisoformat(a.date) if a.date else pdaily_data.default_day()
    except ValueError:
        print(f"取数失败：日期写法不对：{a.date}")
        raise SystemExit(1)
    try:
        projects = json.loads(progress_data.ROSTER.read_text())["projects"]
    except (OSError, ValueError, KeyError) as e:
        print(f"取数失败：读不了项目名册 {progress_data.ROSTER}（{e}）")
        raise SystemExit(1)
    facts = build(day, projects, pdaily_data.netwait.wait_seconds())   # 唤醒后代理没通：gh 最多等 300 秒（#176、P9）
    if a.dry_run:
        print(json.dumps(facts, ensure_ascii=False, indent=2))
        return
    f = write(facts)
    n = lambda k: "未知" if facts[k] is None else len(facts[k])
    failed = facts["coverage"]["failed_parts"]
    print(f"已写 {f}：{len(facts['projects'])} 个项目，{len(facts['actions'])} 条行动，"
          f"{n('decisions')} 条拍板候选，{n('risks')} 条风险候选" + (f"；算不出来的：{'、'.join(failed)}" if failed else ""))


if __name__ == "__main__":
    main()
