#!/usr/bin/env python3
"""未闭环账本（#266 批 5a，规格 docs/未闭环账本-规格.md）：每件进了系统、还没完的事占一行，
写着现在谁负责、下一步是什么、什么时候再看；到点没动静就标「停了」。

只读：不续接、不恢复、不派单、不合并、不调 AI，不改任何来源方的数据。写的只有自己的输出目录
DigitalTwin/openloop/（ledger.json 当前账本、history/<日期>.json 每天一份快照）。
唯一会动到别处的是扫描前对名册各项目跑一次 `git fetch origin <主分支>`（只更新远端跟踪引用，不动工作区和本地分支）：
本机主目录常落后远端，不拉就会拿旧的 CURRENT 和 GitHub 的现状比，报出假的「对不上」；--no-fetch 关掉。

来源（5a-1、5a-2；都只收名册里的个人项目）：
  GitHub     名册活跃仓库里开着的 PR，现查（gh api graphql）；不用合并官的 facts.jsonl（停更，且只记第一次看到的样子）
  主线清单   各项目 CURRENT「主线」节里的 ✅▶⏳❓⏸📌✖；✅ ✖ 不进账本，⏸ 📌 只计数（parked），解析不了的记 unparsed；
             同名的主线只认最上面一节（下面的是它以前的样子），标题写「旧主线」的不收，两种都列在 coverage 里
  桌面会话   最后一次答复写着「当前状态：〔…〕」的 Claude 桌面会话：复用拍板清单脚本（my-skills 的 decision_inbox.py）的
             scan()、sift()，按原样调用、不改它；会话在哪个项目、记着哪些 PR，另从会话元数据里读
  驿窗送审   每一次送 ChatGPT 审查的接力：只读驿窗的 handoffs.json（字段以驿窗 handoffs.rs 为准，它改结构这里要跟着改）
  新通道派单 派单网关签出的每张写入任务凭证一行（#195 缺口 3）：凭证库 grants.db、中台库 hub.db 都只读打开（库不在不建库，
             算「还没装」），PR 按固定分支 <执行方>/<单号> 现查；只收名册里沙盒和活跃项目的单。分支上有 PR 时另出一行 PR，
             并成一行（PR 的状态照 GitHub 那一列判）
同一件事并成一行（规格第 7 节）：主线项、会话、接力里明文写的 PR 号，正好指到一个开着的 PR 就并到那个 PR；接力还并到发起它的会话。
并到一起后状态取最需要人的，其余来源记在 links。提到不止一个开着的 PR 的不并，只在两边的 links 里互相记一笔（related）。

一行的字段照规格第 4 节：id、what、state、owner、evidence、next、check_at、blocked、done_when、links、since；
另带 source、project、evidence_at、stalled（到 check_at 证据没变）。owner 只照来源写的填，判不出来写「未知」。
「记录和事实对不上」（规格第 6 节末）单列在 mismatches：CURRENT 的节标题或主线项写着待合并 / 未合并，
而那个 PR（或那张 issue 的 PR）已合并或已关。

处置记录（#361 前置，docs/技能架构.md 台账 D-13）：猛鬼兽处理完账本里一件事，经 dispose 记一条处置（当前事实、卡在哪、
负责人、下一步、下次检查时间、怎样算完、谁记的，可带出处和备注），追加到 DigitalTwin/openloop/dispositions.jsonl（只追加）。
下次 scan 时每行多一个 disposition（这一行和并进来的各来源里最近的一条处置）；来源算出来的 state、owner、next、check_at 一概不动，
处置和来源分开看。「记录和事实对不上」那一类也能记处置，但不能写成已完成（规格 5b-4：交给事实核对）。技能不直接改账本。

命令：scan（扫描并写账本）、show（打印账本）、dispose（记一条处置）。未安装；接日报、午晚单在 5a-3。
"""
import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import merge_officer  # noqa: E402  复用风险档判定（L0 = 不用审）和检查失败的结论集合
import netwait  # noqa: E402  唤醒后等网络
import pdaily_data  # noqa: E402  复用 gh 取数（带等网络重试）、读主分支快照
import progress_data  # noqa: E402  名册路径

TZ = ZoneInfo("Asia/Shanghai")
# 写进行里的几句固定说法。午晚单（openloop_slip.py）靠它们分「合并 / 亲手做 / 拍板」和「可能已过期」，改这里两边一起变
MERGE_NEXT = "常浩决定合并"
SAID = "会话最后一次答复〔{}〕"
GONE_NOTE = "（它的 PR 都已合并或关闭，这条可能已经过期）"
HANDS = ["合并", "点卡", "亲手做"]   # 要常浩亲手做的三种事，午晚单按这个顺序排；另一种是「拍板」
VERSION = 1
UNKNOWN = "未知"
OWNER = "常浩"
STATES = ("卡住", "等你", "待验证", "等外部", "在做")   # 按「最需要人」排（规格第 7 节）
BRANCH_OWNER = {"claude": "Claude", "codex": "Codex", "hermes": "猛鬼兽"}   # 分支前缀就是谁在写（工程流程第 2 节）
SLIPS = (dt.time(12, 30), dt.time(21, 0))   # 午晚两张单（规格第 13 节第 4 条）；「等你」的事过了下一张还没回算停了
# 多久没动静算「停了」，单位分钟。前五个是规格第 6 节的默认值（推断，先跑一周再调）；后三个规格没写，按最近的一类给。
WAIT = {
    "unreviewed": 240,      # PR 提交了没人审
    "ci_failed": 120,       # CI 失败没人分析
    "rework": 240,          # 审查要求返修没人接
    "in_progress": 1440,    # 一直在做没进展
    "draft": 1440,          # 草稿 PR：按「一直在做没进展」
    "ci_running": 120,      # 检查一直没跑完：按 CI 类
    "conflict": 240,        # 和主分支有冲突没人变基：按返修类
    "session_bg": 120,      # 会话没下文：〔后台进行中〕没写预计时间的（规格第 6 节）
    "reply_not_returned": 30,   # 回信没送回会话（规格第 6 节）
    "relay_wait": 120,      # 送出去等 ChatGPT 回信：规格没写，按「会话没下文」
    "relay_failed": 30,     # 送审没送成：规格没写，按「回信没送回会话」
    # 新通道派单（#195 缺口 3，规格第 6 节；推断，先跑一周再调）
    "dispatch_unsent": 30,  # 派单事件记了账、没确认送到中台
    "worker_queue": 15,     # 中台排上了、没有工人接：规格第 6 节「派单建了没启动」
    "worker_run": 150,      # 工人在跑：每张单累计上限 2 小时，再留半小时
    "publish_wait": 120,    # 工人交接了、还没推分支开 PR：按「会话没下文」
    "dispatch_idle": 240,   # 这一轮结束了，既没交接也没 PR：按「PR 提交了没人审」
}
SESSION_DAYS = 14                       # 只看最近这么多天动过的会话（和拍板清单脚本的默认一样）
RELAY_DAYS = dt.timedelta(days=7)       # 已结束的接力只往回看这么久
PRIMARY = ("pr", "dispatch", "session", "relay", "mainline")   # 并成一行时，行的编号和名字用哪个来源的
RECENT_WINS = ("等外部", "在做")         # 并在一起的几条状态相同时：这两种取最近有动静的那条，其余取最先到点的
# 会话自己写的预计时间：「约 40 分钟」「再等 2 小时」「大约半小时」。「已跑了约 30 分钟」说的是过去，不算
ETA = re.compile(r"(?<![了已过大])(?:约|大约|大概|预计|再等?|再过)\s*(半|\d+(?:\.\d+)?)\s*(分钟|个?小时)")
VERDICT = {"pass": "通过", "revise": "需修改"}
STALE_LEDGER = dt.timedelta(hours=24)   # show：账本这么久没更新就在最上面说一句
PR_PAGE, PR_PAGES = 50, 4               # 每个仓库最多取 200 个开着的 PR，到了上限在 coverage 里标出来
FILES_MAX = 100                         # 文件列表只取前 100 个：取不全就不判 L0
FETCH_TIMEOUT = 60                      # 拉一个项目最多等这么多秒：代理没通时不让整本账挂着
LOOKUP_MAX = 60                         # 每个仓库一次最多核这么多个「待合并」里提到的号

PR_NODE = """number title url isDraft createdAt updatedAt headRefName headRefOid mergeable changedFiles
files(first:%d){nodes{path}}
commits(last:1){nodes{commit{oid committedDate statusCheckRollup{contexts(first:100){nodes{__typename
... on CheckRun{name status conclusion completedAt} ... on StatusContext{context state createdAt}}}}}}}
reviews(last:50){nodes{state submittedAt commit{oid}}}""" % FILES_MAX     # pr_row 要的字段：开着的 PR 和派单分支上的 PR 共用
PR_QUERY = """query($o:String!,$n:String!,$c:String){repository(owner:$o,name:$n){pullRequests(states:OPEN,first:%d,after:$c,
orderBy:{field:CREATED_AT,direction:ASC}){pageInfo{hasNextPage endCursor}nodes{%s}}}}""" % (PR_PAGE, PR_NODE)
TASK_ID = re.compile(r"^t-\d{8}-[0-9a-f]{6}$")     # 和 autoloop_grants.TASK_ID 同一写法
DISPATCH_OWNER = {"claude": "Claude", "codex": "Codex"}
STOPPED = "停下了"      # 工人侧启动脚本没交接的一轮，结论以它开头（ops/dt_worktree.stop_text、autoloop_publish.STOPPED）
EVENT_GRACE = dt.timedelta(minutes=2)    # 派单事件发出去这么久还没确认送到，才算没送到（网关 60 秒后补发）
DISPATCH_OLD = dt.timedelta(days=7)      # 已撤销 / 收掉 / 过期这么久、上一份账本里也没有的单不再查（查询量不随历史单越涨越多）
HUB_MISSING = "中台库不在（还没装）"
SQL_CHUNK = 500                          # 一条 IN 查询最多带几个单号（老版本 sqlite 一条语句最多 999 个参数）
LOOKUP = ("i%d: issueOrPullRequest(number:%d){__typename ... on PullRequest{state mergedAt closedAt} "
          "... on Issue{state closedAt closedByPullRequestsReferences(first:10,includeClosedPrs:true){nodes{number state mergedAt closedAt}}}}")

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
MAINLINE = re.compile(r"^(?:#{1,6}\s*)?\**(旧)?(?:当前)?主线(?:清单)?\**(?:\s*[：:]\s*(.*?))?\**\s*$")   # 一节主线的标题行
INLINE = re.compile(r"^\s*(?:[-*+]\s+)?\**(旧)?(?:当前)?主线\**\s*[：:]?\s*(.*)$")   # 标题和各项写在同一行的主线
RULE = re.compile(r"^-{3,}\s*$")
MARK = "[✅▶⏳❓⏸📌✖]"
ITEM = re.compile(r"^(\s*)(?:[-*+]\s+|\d+[.、)）]\s*)?(" + MARK + r")\ufe0f?\s*(.*)$")
ODD_ITEM = re.compile(r"^\s*\S{0,3}\s*" + MARK)   # 标记不在行首（表格行之类）：认不出是哪一项，记「未解析」
BULLET = re.compile(r"^[-*+]\s+(.*)$")   # 顶格、没有标记的列表项：主线节里出现就是「未解析」
LOOKS_LIKE = re.compile(r"主线.*完成\s*\d+\s*/\s*\d+")   # 像主线标题、却没被认成一节的行：记「未解析」
LABEL_END = re.compile(r"[：:（(。；;，,]")
NUM = re.compile(r"(?<![A-Za-z0-9_./-])#(\d+)")   # 本仓库的号；「postpane#466」这种别的仓库的不算
PENDING = re.compile(r"(?:待|等)[^，,。；;（）()\s]{0,6}合并|未合并")   # 待合并、待常浩合并、待常浩定合并、等常浩合并、未合并
CLAUSE = re.compile(r"[，,；;。（）()]")
SENTENCE = re.compile(r"[。；;]")
LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")   # [PR #39](https://…)：只留方括号里的字，网址里的号和括号不参与断句
MARK_STATE = {"▶": "在做", "⏳": "等外部", "❓": "等你"}
PARKED = {"⏸": "先不做", "📌": "挂起"}
DONE = "✅✖"


class Fail(Exception):
    pass


# ---------- 时间 ----------

def current():
    v = os.environ.get("DT_NOW")
    return parse_ts(v) if v else dt.datetime.now(TZ).replace(microsecond=0)


def parse_ts(s):
    """GitHub 的 …Z、git 的 %cI、会话记录里带毫秒的，都转成北京时间；读不懂返回 None。"""
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(re.sub(r"\.\d+", "", str(s).strip()).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (t if t.tzinfo else t.replace(tzinfo=TZ)).astimezone(TZ)


def iso(t):
    return t.isoformat(timespec="seconds") if t else None


def stamp(t):
    return t.strftime("%m-%d %H:%M") if t else "时间未知"


def next_slip(t):
    """t 之后的第一张午晚单的时间。"""
    for day in (t.date(), t.date() + dt.timedelta(days=1)):
        for at in SLIPS:
            slip = dt.datetime.combine(day, at, tzinfo=TZ)
            if slip > t:
                return slip


def clip(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------- 一行 ----------

def make(id_, source, project, what, state, owner, at, evidence, next_, wait=None, check_at=None, blocked="", done_when="", **more):
    """at 是最近证据的时间；check_at 没给就按 at + WAIT[wait] 算，at 取不到就没有 check_at（不催，但也不当没事：evidence 里写明）。"""
    if check_at is None and wait and at:
        check_at = at + dt.timedelta(minutes=WAIT[wait])
    return {
        "id": id_, "what": clip(what, 80), "state": state, "owner": owner,
        "evidence": f"{stamp(at)} {evidence}", "next": next_, "check_at": iso(check_at),
        "blocked": blocked, "done_when": done_when, "links": [], "since": None,
        "source": source, "project": project, "evidence_at": iso(at), "stalled": False, **more,
    }


# ---------- 名册 ----------

def roster():
    try:
        projects = json.loads(progress_data.ROSTER.read_text(encoding="utf-8"))["projects"]
    except (OSError, ValueError, KeyError) as e:
        raise Fail(f"读不了项目名册 {progress_data.ROSTER}（{e}）")
    return [p for p in projects if p.get("category") == "活跃"]


def key_of(p):
    """账本里项目叫什么：有 GitHub 仓库用仓库名（PR 的编号就是 pr:<仓库名>#<号>），没有用名册 id。"""
    return (p.get("github") or "").split("/")[-1] or p["id"]


def root_of(p):
    return Path(p["path"]).expanduser()


# ---------- GitHub：开着的 PR ----------

def graphql(hub, query, **variables):
    args = ["api", "graphql", "-f", f"query={query}"]
    for k, v in variables.items():
        if v is not None:
            args += ["-f", f"{k}={v}"]
    return hub._run(*args)   # pdaily_data.GitHub 的取数入口：等网络重试、报错收成一句，这里不另写一套


def open_prs(hub, repo):
    """（PR 列表，是不是到了上限）。"""
    owner, name = repo.split("/", 1)
    nodes, cursor = [], None
    for _ in range(PR_PAGES):
        data = graphql(hub, PR_QUERY, o=owner, n=name, c=cursor)
        prs = (((data or {}).get("data") or {}).get("repository") or {}).get("pullRequests")
        if prs is None:
            raise Fail("GitHub 没有返回这个仓库的 PR 列表")
        nodes += prs.get("nodes") or []
        page = prs.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return nodes, False
        cursor = page.get("endCursor")
    return nodes, True


def checks_of(commit):
    """头提交上的检查：failing [(名字, 完成时间)]、pending [名字]、has 有没有检查。"""
    nodes = (((commit.get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes")) or []
    failing, pending = [], []
    for c in nodes:
        if c.get("__typename") == "StatusContext":
            state = str(c.get("state") or "").upper()
            if state in ("FAILURE", "ERROR"):
                failing.append((c.get("context") or "检查", parse_ts(c.get("createdAt"))))
            elif state in ("PENDING", "EXPECTED"):
                pending.append(c.get("context") or "检查")
        elif str(c.get("status") or "").upper() != "COMPLETED":
            pending.append(c.get("name") or "检查")
        elif str(c.get("conclusion") or "").upper() in merge_officer.FAILING:
            failing.append((c.get("name") or "检查", parse_ts(c.get("completedAt"))))
    return {"has": bool(nodes), "failing": failing, "pending": pending}


def github_reviews(node):
    """GitHub 上有结论的审查（通过 / 要求修改）；只留评论的不算结论。"""
    head, out = node.get("headRefOid"), []
    for r in ((node.get("reviews") or {}).get("nodes")) or []:
        verdict = {"APPROVED": "pass", "CHANGES_REQUESTED": "revise"}.get(r.get("state"))
        if verdict:
            out.append({"verdict": verdict, "at": parse_ts(r.get("submittedAt")), "by": "GitHub 审查",
                        "current": ((r.get("commit") or {}).get("oid")) == head})
    return out


def tier_of(repo, node, rules):
    """风险档；规则读不了、文件列表取不全或是空的，返回 None（不判 L0）。"""
    paths = [f.get("path") or "" for f in ((node.get("files") or {}).get("nodes")) or []]
    if not rules or not paths or (node.get("changedFiles") or 0) > len(paths):
        return None
    return merge_officer.tier_of(repo, paths, rules)


def is_current(record, head, pushed):
    """这条审查记录审的是不是 PR 现在的头提交。驿窗记了受审提交就比提交号；没记就看回信之后有没有新提交。"""
    if "current" in record:
        return record["current"]
    if record.get("head"):
        return bool(head) and (head.startswith(record["head"]) or record["head"].startswith(head))
    return not (pushed and record["at"] and record["at"] < pushed)


def pr_row(repo, node, rules, reviews=(), known=True, now=None):
    """一个开着的 PR 现在是哪种状态（规格第 5 节 GitHub 一列）。按下面的顺序，先成立的算数：
    检查失败 → 卡住；有一轮送审送出去还没回 → 等外部（有冲突也先等审完）；草稿 → 在做；
    有冲突、4 小时内没有提交也不在返修 → 卡住（有动静的照下面判，证据里写一句有冲突）；
    审查要求修改：4 小时内算在做（在返修），过了还没新提交才卡住；检查在跑 → 在做；审过或只改文档 → 等你；
    最近 4 小时内有新提交、还没审 → 在做；其余 → 待验证。（常浩 11-22 回「修账本判法」，见规格第 5 节）
    reviews：驿窗送审给的记录（有结论的回信，或还在等回信的 flying），和 GitHub 上的审查一起按时间排，最新一条算数。
    active_at：PR 最近一次有动静（提交或审查记录）的时间，并行时比它旧的会话、主线项说法不参与定状态。"""
    now = now or current()
    name = repo.split("/")[-1]
    commit = ((((node.get("commits") or {}).get("nodes")) or [{}])[-1] or {}).get("commit") or {}
    pushed = parse_ts(commit.get("committedDate")) or parse_ts(node.get("updatedAt"))
    writer = BRANCH_OWNER.get((node.get("headRefName") or "").split("/")[0], UNKNOWN)
    who = writer if writer != UNKNOWN else "开这个 PR 的会话"
    checks = checks_of(commit)
    ci = "检查全过" if checks["has"] else "仓库没有 CI"
    records = sorted(list(reviews) + github_reviews(node), key=lambda r: iso(r["at"]) or "")
    last = records[-1] if records else None
    fresh = bool(last) and is_current(last, node.get("headRefOid") or "", pushed)
    active = max((t for t in (pushed, last["at"] if last else None) if t), default=None)
    base = dict(id_=f"pr:{name}#{node['number']}", source="pr", project=name, what=node.get("title") or "",
                done_when="PR 合并或关闭", url=node.get("url"), active_at=iso(active))
    conflict = node.get("mergeable") == "CONFLICTING"
    reworking = bool(last and last["verdict"] == "revise" and fresh and last["at"] and now - last["at"] < dt.timedelta(minutes=WAIT["rework"]))
    recent = bool(pushed and now - pushed < dt.timedelta(minutes=WAIT["conflict"]))
    note = "；和主分支有冲突" if conflict else ""
    if checks["failing"]:
        names = "、".join(n for n, _ in checks["failing"])
        failed = max((t for _, t in checks["failing"] if t), default=pushed)
        return make(state="卡住", owner=writer, at=failed, evidence=f"检查失败：{names}", next_=f"{who}看失败原因并修",
                    wait="ci_failed", blocked=f"CI 失败：{names}", **base)
    if last and last.get("flying"):   # 正在审：等审查方，冲突留到审完再处理（0D 就是有意留着冲突送审的）
        return make(state="等外部", owner="ChatGPT", at=last["at"], evidence=f"已送审（{last['by']}），对方在看" + ("；和主分支有冲突，审完再处理" if conflict else ""),
                    next_=f"等 {last['label']}回信", wait="relay_wait", **base)
    if node.get("isDraft"):
        return make(state="在做", owner=writer, at=pushed, evidence="最近一次提交（草稿 PR）",
                    next_=f"{who}做完后转成可审", wait="draft", **base)
    if conflict and not (reworking or recent):   # 有冲突、4 小时没人动：没人在变基
        return make(state="卡住", owner=writer, at=pushed, evidence="最近一次提交；和主分支有冲突",
                    next_=f"{who}变基解决冲突", wait="conflict", blocked="和主分支有冲突", **base)
    if last and last["verdict"] == "revise" and fresh:
        if reworking:   # 回信刚到：写代码的一方在返修，还没到「没人接」的门槛
            return make(state="在做", owner=writer, at=last["at"], evidence=f"{last['by']}要求修改，{who}返修中（还没有新提交）{note}",
                        next_=f"{who}返修后重新送审", wait="rework", **base)
        return make(state="卡住", owner=writer, at=last["at"], evidence=f"{last['by']}要求修改，之后没有新提交",
                    next_=f"{who}返修后重新送审", wait="rework", blocked=f"审查要求修改（{last['by']}）", **base)
    if checks["pending"]:
        return make(state="在做", owner=writer, at=pushed, evidence="最近一次提交，检查还在跑：" + "、".join(checks["pending"]) + note,
                    next_="等检查跑完", wait="ci_running", **base)
    tier = tier_of(repo, node, rules)
    passed = bool(last) and last["verdict"] == "pass" and fresh
    if passed or tier == "L0":
        why = f"{last['by']}通过" if passed else "只改文档（L0，不用审）"
        at = max((t for t in (pushed, last["at"] if last else None) if t), default=None)
        if conflict:   # 审过了但有冲突：先变基，合不了
            return make(state="在做", owner=writer, at=pushed, evidence=f"{why}，{ci}{note}", next_=f"{who}变基解决冲突，之后等常浩合并",
                        wait="conflict", **base)
        return make(state="等你", owner=OWNER, at=at, evidence=f"{why}，{ci}", next_=MERGE_NEXT,
                    check_at=next_slip(at) if at else None, **base)
    stale = f"；上一次审的是旧版本（{last['by']}）" if last else "，没有审查或送审记录" if known else "，GitHub 上没有审查，驿窗的送审记录这次没读到"
    if pushed and now - pushed < dt.timedelta(minutes=WAIT["unreviewed"]):   # 规格第 5 节「最近有新提交 = 在做」
        return make(state="在做", owner=writer, at=pushed, evidence=f"最近一次提交，{ci}{stale}（刚提交，还在改或等送审）{note}",
                    next_="送审或请人审", wait="unreviewed", **base)
    return make(state="待验证", owner=UNKNOWN, at=pushed, evidence=f"最近一次提交，{ci}{stale}",
                next_="送审或请人审", wait="unreviewed", **base)


def github_source(projects, hub, rules, reviews=None, known=True, now=None):
    """reviews：{PR 的编号: [驿窗送审记录]}；known：送审记录这次读到了没有。"""
    reviews = reviews or {}
    rows, cover = [], {"ok": [], "failed": {}, "no_repo": [], "capped": []}
    for p in projects:
        repo, name = p.get("github"), key_of(p)
        if not repo:
            cover["no_repo"].append(name)
            continue
        try:
            nodes, capped = open_prs(hub, repo)
        except pdaily_data.Fail as e:
            cover["failed"][name] = str(e)
            continue
        except Fail as e:
            cover["failed"][name] = str(e)
            continue
        cover["ok"].append(name)
        if capped:
            cover["capped"].append(name)
        rows += [pr_row(repo, n, rules, reviews.get(f"pr:{name}#{n['number']}", ()), known, now) for n in nodes]
    return rows, cover


# ---------- 新通道派单（#195 缺口 3） ----------

def grants_db():
    """同 autoloop_grants.db_path()（测试核两边一致）；不导入它：它不和本脚本装在一起，导入还会带进飞书身份模块。"""
    return Path(os.environ.get("DT_GRANTS_DB") or "~/Library/Application Support/DigitalTwin/autoloop/grants.db").expanduser()


def hub_db():
    """同 autoloop_publish.hub_db()。"""
    return Path(os.environ.get("AGENT_AUTOLOOP_HOME") or "~/.agent-autoloop").expanduser() / "hub.db"


def dispatch_outbox():
    """同 autoloop_publish.outbox()：工人交接包放这里，主账号能读。"""
    return Path(os.environ.get("DT_PUBLISH_OUTBOX") or "/Users/dt-worker/dt-outbox")


def read_only(path):
    """只读打开：库不在就报错，不建库（sqlite 默认会建一个空库）。"""
    con = sqlite3.connect(f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def hub_ts(v):
    """中台库里的时间：ISO 文字或毫秒数。"""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return dt.datetime.fromtimestamp(v / 1000 if v > 1e11 else v, TZ)
    return parse_ts(v)


def sandboxes():
    """名册 inventory 里标了 sandbox 的仓库 {仓库名: owner/仓库}（同 autoloop_grants.allowed_repos 的认法）。"""
    try:
        inv = json.loads(progress_data.ROSTER.read_text(encoding="utf-8")).get("inventory") or []
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    for x in inv:
        path = str(x.get("path") or "")
        if x.get("sandbox") and path.startswith("github:"):
            out[path.split("/")[-1]] = path[len("github:"):]
    return out


def read_grants():
    """凭证库：(凭证列表, {单号: 派单事件列表})。库不在返回 None。"""
    db = grants_db()
    if not db.exists():
        return None
    try:
        con = read_only(db)
        try:
            grants = [dict(r) for r in con.execute(
                "SELECT task_id, repo, github, executor, rule, status, generation, valid_until, scope_text, created_at, "
                "updated_at FROM grants ORDER BY created_at")]
            events = {}
            for e in con.execute("SELECT task_id, generation, kind, sent, at, claimed_at FROM hub_events ORDER BY at"):
                events.setdefault(e["task_id"], []).append(dict(e))
        finally:
            con.close()
    except sqlite3.Error as e:
        raise Fail(f"凭证库读不了（{type(e).__name__}）")
    return grants, events


def read_runs(task_ids):
    """中台库：{单号: [这张单的轮次]}；库不在返回 None，读不了抛 Fail。"""
    db = hub_db()
    if not db.exists():
        return None
    out = {t: [] for t in task_ids}
    ids = list(out)
    try:
        con = read_only(db)
        try:
            for i in range(0, len(ids), SQL_CHUNK):   # runs 是视图，每查一次都扫一遍：按批一条 IN 查询，不按单号逐条查
                part = ids[i:i + SQL_CHUNK]
                for r in con.execute("SELECT id, rule, lane, state, attempts, created_at, claimed_at, seen_at, finished_at, summary "
                                     f"FROM runs WHERE lane IN ({','.join('?' * len(part))}) ORDER BY id", part):
                    out[r["lane"]].append(dict(r))
        finally:
            con.close()
    except sqlite3.Error as e:
        raise Fail(f"中台库读不了（{type(e).__name__}）")
    return out


def branch_prs(hub, repo, branches):
    """{分支: [这条分支上的 PR，新的在前]}，开着、合并、关掉的都要。"""
    owner, name = repo.split("/", 1)
    fields = " ".join(f'b{i}: pullRequests(headRefName:"{b}",states:[OPEN,MERGED,CLOSED],first:5,'
                      f'orderBy:{{field:CREATED_AT,direction:DESC}}){{nodes{{state mergedAt closedAt isCrossRepository {PR_NODE}}}}}'
                      for i, b in enumerate(branches))
    data = hub._run("api", "graphql", "-f", f"query=query($o:String!,$n:String!){{repository(owner:$o,name:$n){{{fields}}}}}",
                    "-f", f"o={owner}", "-f", f"n={name}")
    got = ((data or {}).get("data") or {}).get("repository")
    if got is None:
        raise Fail("GitHub 没有返回这个仓库的 PR")
    # 别人从 fork 开的同名分支 PR 不是工人交的（合并官对 fork 来的 PR 也一律问常浩）：只认来源是本仓库的
    return {b: [x for x in ((got.get(f"b{i}") or {}).get("nodes")) or [] if not x.get("isCrossRepository")]
            for i, b in enumerate(branches)}


def handed_off(task_id, since=None):
    """工人交接包的头提交（交接清单 handoff.json 里的 head）；没有、读不了返回 None（只当线索，不当证据）。
    since：最后一轮开跑的时间。交接清单比它旧的是上一轮留下的，不算这一轮交接了。"""
    f = dispatch_outbox() / task_id / "handoff.json"
    try:
        head = json.loads(f.read_text(encoding="utf-8")).get("head")
        if since and dt.datetime.fromtimestamp(f.stat().st_mtime, TZ) < since:
            return None
    except (OSError, ValueError, AttributeError):
        return None
    return head if isinstance(head, str) and re.match(r"^[0-9a-f]{40}$", head) else None


def finished(g, now):
    """这张凭证已经完了：撤销、收掉，或过了有效期（开着的、冻结的都算）。"""
    until = parse_ts(g["valid_until"])
    return g["status"] in ("revoked", "done") or (g["status"] in ("active", "frozen") and until is not None and until <= now)


def slip_after(at):
    """「等你」的行按证据时间算下次检查：过了下一张单还没动静就算停了（规格第 6 节）；证据时间取不到就不催。"""
    return next_slip(at) if at else None


def parts_of(prev):
    """上一份账本里的每一部分（并成一行的拆开）：{编号: 那一部分}。"""
    return {m["id"]: m for x in (prev or {}).get("rows", []) for m in (x.get("parts") or [x])}


def carry_task(prev, task_id, with_pr=True):
    """上一份账本里这张单的那部分，原样带回来、标沿用；with_pr：连同它指到的 PR 那部分（沙盒的 PR 行只有派单来源会出；
    这次 PR 查到了就不带旧的，由调用方照这次的实况出）。上一份里没有返回 []。"""
    old = parts_of(prev)
    me = old.get(f"dispatch:{task_id}")
    if not me or "state" not in me:
        return []
    out = []
    for i in [me["id"]] + [r for r in me.get("refs") or [] if r.startswith("pr:") and with_pr]:
        m = old.get(i)
        if m:
            kept = {k: v for k, v in m.items() if k != "parts"}
            out.append({**kept, "links": [], "carried": True, "carried_from": m.get("carried_from") or prev.get("generated_at")})
    return out


def dispatch_row(g, events, runs, prs, now, hub_ok=True, pr_ok=True):
    """一张派单现在是哪种状态（规格第 5 节「新通道派单」一列）。返回 None 表示这张单已经完了、不进账本。
    按下面的顺序，先成立的算数：
    撤销 / 完成 / 过期：还有开着的 PR → 等你（合并或关掉）；还有活着的轮次 → 卡住；其余不收。
    冻结 → 等你（恢复还是撤销）；派单事件发出 2 分钟还没确认送到中台 → 卡住；轮次 unknown → 卡住；
    轮次在跑 / 排队 → 在做；分支上的 PR 合并了 → 等你（收掉这张单）；PR 关了没合并 → 等你（续接还是停单）；
    PR 开着 → 在做（照 PR 那一行判，两行并成一行）；最后一轮「停下了」或被取消 → 卡住；
    最后一轮正常结束、有交接包 → 等外部（推分支开 PR，首单由常浩在场手动做）；正常结束、没交接包 → 待验证；
    派单送到了中台却没有轮次 → 卡住（通道暂停或没有派单规则，中台只记不接）。"""
    tid, gen = g["task_id"], g["generation"]
    worker = DISPATCH_OWNER.get(g["executor"], UNKNOWN)
    first = clip((g["scope_text"] or "").strip().splitlines()[0] if (g["scope_text"] or "").strip() else "", 50)
    base = dict(id_=f"dispatch:{tid}", source="dispatch", project=g["repo"], what=f"{tid}（{worker}）：{first}",
                done_when="PR 合并后收掉这张单，或停单")
    mine = [r for r in runs if r.get("rule") == g["rule"]]
    live = [r for r in mine if r["state"] in ("queued", "claimed", "unknown")]
    last = mine[-1] if mine else None
    open_pr = next((x for x in prs if x.get("state") == "OPEN"), None)
    merged = next((x for x in prs if x.get("state") == "MERGED"), None)
    closed = next((x for x in prs if x.get("state") == "CLOSED"), None)
    pr_id = lambda x: f"pr:{g['repo']}#{x['number']}"   # noqa: E731
    refs = {"_refs": [pr_id(open_pr)]} if open_pr else {}
    note = "" if hub_ok else "；中台库这次没读到，轮次不明"
    note += "" if pr_ok else "；PR 这次没查到"
    if finished(g, now):    # 冻结的过期了也算完：过期后恢复一律被拒（autoloop_grants.unfreeze）
        word = {"revoked": "已撤销", "done": "已收掉"}.get(g["status"], "凭证已过期")
        if open_pr:
            at = parse_ts(g["updated_at"])
            return make(state="等你", owner=OWNER, at=at, evidence=f"{word}，分支上的 PR #{open_pr['number']} 还开着{note}",
                        next_="常浩决定合并还是关掉这个 PR", check_at=slip_after(at), ask="合并", **refs, **base)
        if live:
            r = live[-1]
            return make(state="卡住", owner=UNKNOWN, at=parse_ts(g["updated_at"]), evidence=f"{word}，中台里轮次 {r['id']} 还是 {r['state']}{note}",
                        next_=f"在电脑上用 agent-autoloop stop {r['id']} 停掉", wait="worker_queue", blocked="停单后轮次还活着", **base)
        return None
    if g["status"] == "frozen":
        at = parse_ts(g["updated_at"])
        return make(state="等你", owner=OWNER, at=at, evidence=f"已冻结（第 {gen} 代作废）{note}",
                    next_="常浩决定恢复（私聊「恢复 <单号> <冻结码>」）还是撤销", check_at=slip_after(at), ask="拍板", **refs, **base)
    # -2 是作废前已领取的事件（可能已送到，靠代次拦截），send-pending 不会再发：不算「没送到」
    pending = [e for e in events if e["generation"] == gen and e["sent"] in (0, 2)]
    stuck = [e for e in pending if (hub_ts(e.get("claimed_at") or e["at"]) or now) <= now - EVENT_GRACE]
    if stuck:
        e = stuck[0]
        return make(state="卡住", owner="猛鬼兽", at=hub_ts(e.get("claimed_at") or e["at"]),
                    evidence=f"派单事件（{e['kind']}）没确认送到中台{note}", next_="猛鬼兽跑 autoloop_grants.py send-pending 补发",
                    wait="dispatch_unsent", blocked="派单事件没送到中台", **base)
    if live:
        r = live[-1]
        if r["state"] == "unknown":
            return make(state="卡住", owner=UNKNOWN, at=hub_ts(r.get("seen_at") or r.get("claimed_at")),
                        evidence=f"中台轮次 {r['id']}：接单程序 5 分钟没报，不确定还在不在跑", next_="在电脑上看 dt-worker 的工人进程",
                        wait="worker_queue", blocked="接单程序没消息", **base)
        if r["state"] == "claimed":
            return make(state="在做", owner=worker, at=hub_ts(r.get("claimed_at") or r.get("created_at")),
                        evidence=f"中台轮次 {r['id']} 在跑", next_=f"等{worker}这一轮做完", wait="worker_run", **refs, **base)
        retry = f"（没跑成 {r['attempts']} 次，等重试）" if r.get("attempts") else ""
        return make(state="在做", owner=worker, at=hub_ts(r.get("created_at")), evidence=f"中台轮次 {r['id']} 排队等工人接{retry}",
                    next_=f"等工人接单（{worker}）", wait="worker_queue", **refs, **base)
    if merged and not open_pr:
        at = parse_ts(merged.get("mergedAt"))
        return make(state="等你", owner=OWNER, at=at, evidence=f"PR #{merged['number']} 已合并，凭证还开着",
                    next_="常浩说「<单号> 做完了，收掉」，猛鬼兽跑 complete", check_at=slip_after(at), ask="拍板", **base)
    closed_at = parse_ts(closed.get("closedAt")) if closed else None
    reworked = last and closed_at and (hub_ts(last.get("finished_at")) or closed_at) > closed_at   # PR 关掉之后又续接做了一轮
    if closed and not open_pr and not reworked:
        return make(state="等你", owner=OWNER, at=closed_at, evidence=f"PR #{closed['number']} 关了、没合并",
                    next_="常浩决定续接返修还是停单", check_at=slip_after(closed_at), ask="拍板", **base)
    if open_pr:   # 照 PR 那一行判；这一行只是挂着，不另催
        return make(state="在做", owner=worker, at=parse_ts(open_pr.get("updatedAt")), evidence=f"工人已交付，PR #{open_pr['number']} 开着",
                    next_="按 PR 的状态办", **refs, **base)
    if last and (last["state"] == "cancelled" or (last["state"] in ("done", "nothing") and str(last.get("summary") or "").startswith(STOPPED))):
        why = clip(str(last.get("summary") or "").splitlines()[0] if last.get("summary") else "被取消", 80)
        return make(state="卡住", owner=UNKNOWN, at=hub_ts(last.get("finished_at")), evidence=f"中台轮次 {last['id']}：{why}",
                    next_="看停下的原因，处理后续接或停单", wait="dispatch_idle", blocked=why, **base)
    if last:
        if handed_off(tid, hub_ts(last.get("claimed_at") or last.get("created_at"))):
            return make(state="等外部", owner="受信发布", at=hub_ts(last.get("finished_at")),
                        evidence=f"中台轮次 {last['id']} 做完，工人已交接{note}",
                        next_="推分支、开 PR（受信发布还没接通时，由常浩在场手动做）", wait="publish_wait", **base)
        return make(state="待验证", owner=UNKNOWN, at=hub_ts(last.get("finished_at")),
                    evidence=f"中台轮次 {last['id']} 结束了，没有交接包也没有 PR（可能只提了问题，看中台卡片）{note}",
                    next_="看这一轮的结论", wait="dispatch_idle", **base)
    if pending:   # 刚记账、还在送：没到 2 分钟不催
        e = pending[0]
        return make(state="在做", owner="猛鬼兽", at=hub_ts(e.get("claimed_at") or e["at"]), evidence=f"派单事件（{e['kind']}）正在送往中台",
                    next_="等送到中台", wait="dispatch_unsent", **base)
    sent = [e for e in events if e["generation"] == gen and e["sent"] == 1]
    if sent and hub_ok:
        return make(state="卡住", owner=UNKNOWN, at=hub_ts(sent[-1].get("claimed_at") or sent[-1]["at"]),
                    evidence="中台收到了派单，但没有排出轮次（通道暂停或没有这条派单规则，中台只记不接）",
                    next_="看通道（autoloop_config.py status），恢复后续接，或停单", wait="dispatch_unsent",
                    blocked="中台没有接这张单", **base)
    said = "派单已送到中台" if sent else "凭证已签，还没有派单事件或轮次"
    return make(state="待验证", owner=UNKNOWN, at=parse_ts(g["created_at"]), evidence=f"{said}{note}",
                next_="看派单事件（autoloop_grants.py show --task <单号>）", wait="dispatch_unsent", **base)


def gone(part):
    """带回来的那部分指到的 PR 已经不开着了：证据后面补一句，午晚单把它归进「可能已过期」，不再催合并。"""
    ev = part.get("evidence") or ""
    return part if GONE_NOTE in ev else {**part, "evidence": ev + GONE_NOTE}


def check_carried(rows, open_now, checked, hub):
    """带回来的 PR 行核一次现状（复审 P1）：这个仓库这次 GitHub 那一类取到了而它不在开着的里面，或者是沙盒、按号查到已合并 /
    已关闭，就去掉它，指着它的派单那部分补「可能已过期」；查不了的照旧带着。open_now：这次新取到的开着的 PR 编号。"""
    boxes, dropped = sandboxes(), set()
    for x in rows:
        if x.get("source") != "pr" or not x.get("carried") or x["id"] in open_now:
            continue
        name, _, n = x["id"][len("pr:"):].rpartition("#")
        if name in checked:
            dropped.add(x["id"])
        elif name in boxes and n.isdigit():
            try:
                node = lookup(hub, boxes[name], [int(n)]).get(int(n)) or {}
            except Exception:   # 查不了：照旧带着，不当成已经合了
                continue
            if node.get("state") in ("MERGED", "CLOSED"):
                dropped.add(x["id"])
    out = []
    for x in rows:
        if x["id"] in dropped:
            continue
        refs = [r for r in x.get("refs") or [] if r.startswith("pr:")]
        out.append(gone(x) if x.get("carried") and refs and all(r in dropped for r in refs) else x)
    return out


def dispatch_source(projects, hub, rules, reviews=None, known=True, now=None, prev=None):
    """（行，覆盖情况）。行里有派单分支上开着的 PR 时，另出一行 PR（pr_row），派单那一行带 _refs 指过去，合并时并成一行。
    中台库或这个仓库的 PR 这次没取到的单：上一份账本里有它，就把它那部分（连同它的 PR 行）原样带回来、标沿用，不按缺的数据重判；
    上一份里没有才照现有数据判，证据里写明缺了什么。"""
    now = now or current()
    cover = {"installed": True, "rows": 0, "skipped": 0, "old": 0, "carried": 0, "hub": "ok", "pr_failed": {}}
    got = read_grants()
    if got is None:
        if ((prev or {}).get("coverage") or {}).get("dispatch", {}).get("installed"):   # 装过又不在了（比如搬进 dt-gate）：不当没装
            raise Fail("凭证库不在了（上一份账本里还读得到）")
        cover["installed"] = False
        return [], cover
    grants, events = got
    old, pc = parts_of(prev), ((prev or {}).get("coverage") or {}).get("dispatch") or {}
    allowed = {key_of(p): p.get("github") for p in projects if p.get("github")}
    allowed.update(sandboxes())
    mine = [g for g in grants if TASK_ID.match(str(g.get("task_id") or "")) and g.get("repo") in allowed
            and g.get("executor") in DISPATCH_OWNER]
    cover["skipped"] = len(grants) - len(mine)
    trust = pc.get("installed") and not pc.get("failed") and "old" in pc   # 上一份是这个版本完整扫出来的，「上一份没有」才说明它完了
    stale = [g for g in mine if trust and f"dispatch:{g['task_id']}" not in old and finished(g, now - DISPATCH_OLD)
             and (parse_ts(g["updated_at"]) or now) <= now - DISPATCH_OLD]   # 完了一周、上一份也没有的：不再查
    cover["old"] = len(stale)
    mine = [g for g in mine if g not in stale]
    try:
        runs = read_runs([g["task_id"] for g in mine])
    except Fail as e:
        runs, cover["hub"] = None, str(e)
    if runs is None and cover["hub"] == "ok":   # 装过又不在了按读坏了办，同凭证库
        cover["hub"] = "中台库不在了（上一份账本里还读得到）" if pc.get("hub") == "ok" else HUB_MISSING
    by_repo = {}
    for g in mine:
        by_repo.setdefault(g["github"], []).append(f"{g['executor']}/{g['task_id']}")
    prs = {}
    for repo, branches in by_repo.items():
        try:
            prs.update({(repo, b): v for b, v in branch_prs(hub, repo, branches).items()})
        except (pdaily_data.Fail, Fail) as e:
            cover["pr_failed"][repo.split("/")[-1]] = str(e)
    rows = []
    for g in mine:
        branch = f"{g['executor']}/{g['task_id']}"
        on_branch = prs.get((g["github"], branch), [])
        hub_ok, pr_ok = runs is not None, g["github"].split("/")[-1] not in cover["pr_failed"]
        hub_broken = not hub_ok and cover["hub"] != HUB_MISSING   # 中台库读坏了才沿用；库不在（还没装）照现有数据判，不一直沿用旧行
        live_pr = next((x for x in on_branch if x.get("state") == "OPEN"), None)
        # 冻结、停了单而 PR 还开着：只看凭证和 PR 就判得出，不用轮次，PR 查到了就现判
        by_grant = pr_ok and ((g["status"] == "frozen" and not finished(g, now)) or (finished(g, now) and live_pr))
        kept = carry_task(prev, g["task_id"], with_pr=not pr_ok) if (hub_broken or not pr_ok) and not by_grant else []
        if kept:
            if pr_ok:   # 只是中台库没读到：PR 照这次的实况，派单那部分指到这次开着的 PR；原来指的 PR 不开了补「可能已过期」
                me, live = kept[0], f"pr:{g['repo']}#{live_pr['number']}" if live_pr else None
                if not live and any(r.startswith("pr:") for r in me.get("refs") or []):
                    me = gone(me)
                kept = [{**me, "refs": [live] if live else []}]
            rows += kept
            cover["carried"] += 1
        else:
            row = dispatch_row(g, events.get(g["task_id"], []), (runs or {}).get(g["task_id"], []), on_branch, now,
                               hub_ok=hub_ok, pr_ok=pr_ok)
            if row is None:
                continue
            rows.append(row)
        if live_pr and pr_ok:
            name = g["repo"]
            rows.append(pr_row(g["github"], live_pr, rules, (reviews or {}).get(f"pr:{name}#{live_pr['number']}", ()), known, now))
    cover["rows"] = len(rows)
    return rows, cover


# ---------- 主线清单 ----------

def label_of(text):
    """主线项的名字：到第一个冒号、括号、逗号、句号为止。"""
    text = re.sub(r"[*`]", "", text).strip()
    m = LABEL_END.search(text)
    label = text[: m.start()].strip() if m and m.start() else text
    return label or text


def inline_mainline(line, no):
    """「主线：名字｜完成 5/12：✅ 甲 ✅ 乙 ⏳ 丙」这种一行写完的主线 → 一节；不是这种写法返回 None。
    拆出来的项只有数字（「主线：x｜✅2 ▶1」是答复里的计数行，不是清单）时不拆，整行记「未解析」。"""
    m = INLINE.match(line)
    first = re.search(MARK, line)
    if not m or not first or first.start() < m.start(2):
        return None
    rest = m.group(2)
    cut = re.search(MARK, rest).start()
    items = [{"mark": piece[0], "text": piece[1:].strip("\ufe0f ；;，,。"), "line": no} for piece in re.split("(?=" + MARK + ")", rest[cut:]) if piece]
    section = {"title": rest[:cut].strip(" ：:") or "（没写名字）", "old": bool(m.group(1)), "line": no, "end": no, "items": items, "unparsed": []}
    if any(re.fullmatch(r"[\d\s｜|]*", it["text"]) for it in items):
        section.update(items=[], stray=True, unparsed=[{"text": clip(line, 160), "line": no}])
    return section


def mainlines(text):
    """CURRENT 里的主线节：[{title, old, line, end, items:[{mark, text, line}], unparsed:[{text, line}]}]。行号从 1 起。
    认三种写法：标题行加下面的清单；一行写完的；像主线标题却认不出的（stray，只带一条 unparsed）。"""
    out, cur = [], None
    for no, line in enumerate(text.splitlines(), 1):
        one = inline_mainline(line, no)
        if one:
            out.append(one)
            cur = None
            continue
        m = MAINLINE.match(line)
        if m:
            cur = {"title": m.group(2) or "（没写名字）", "old": bool(m.group(1)), "line": no, "end": no, "items": [], "unparsed": []}
            out.append(cur)
            continue
        if cur is None:
            if LOOKS_LIKE.search(line):
                out.append({"title": "像主线、没认出来", "old": False, "stray": True, "line": no, "end": no, "items": [],
                            "unparsed": [{"text": clip(line, 160), "line": no}]})
            continue
        if HEADING.match(line) or RULE.match(line):
            cur = None
            continue
        cur["end"] = no
        item = ITEM.match(line)
        if item:
            cur["items"].append({"mark": item.group(2), "text": item.group(3).strip(), "line": no})
        elif ODD_ITEM.match(line) or BULLET.match(line):
            cur["unparsed"].append({"text": clip(line, 160), "line": no})
    return out


def blame_times(root, ref, a, b):
    """CURRENT.md 第 a 到 b 行各自最近一次改动的时间：{行号: 时间}；取不到返回 {}。"""
    try:
        out = pdaily_data.git(root, "blame", "--porcelain", "-L", f"{a},{b}", ref, "--", "CURRENT.md")
    except pdaily_data.Fail:
        return {}
    at, line_sha, sha = {}, {}, None
    for line in out.splitlines():
        m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", line)
        if m:
            sha = m.group(1)
            line_sha[int(m.group(2))] = sha
        elif line.startswith("committer-time ") and sha:
            at[sha] = dt.datetime.fromtimestamp(int(line.split()[1]), TZ)
    return {no: at.get(s) for no, s in line_sha.items()}


def mainline_key(title):
    m = NUM.search(title)
    return f"#{m.group(1)}" if m else re.sub(r"[\s「」“”\"']+", "", label_of(title.split("｜")[0]))[:16]


def mainline_rows(name, section, times, seen):
    """一节主线 →（行，只计数的，对不上的候选）。seen 记这个项目已经用过的编号：两节主线同名、两项同名时后来的加 ~2、~3。"""
    rows, parked, pending = [], [], []
    for it in section["items"]:
        mark, text = it["mark"], it["text"]
        label = label_of(text)
        id_ = f"mainline:{name}/{mainline_key(section['title'])}/" + re.sub(r"\s+", "", label)[:24]
        seen[id_] = seen.get(id_, 0) + 1   # 做完的项也占序号：前一项标 ✅ 之后，后一项的编号不变
        if seen[id_] > 1:
            id_ += f"~{seen[id_]}"
        if mark in DONE:
            continue
        if PENDING.search(text):
            pending.append({"text": text, "line": it["line"], "kind": "主线项"})
        if mark in PARKED:
            parked.append({"id": id_, "project": name, "what": clip(label, 80), "mark": mark, "why": PARKED[mark], "since": None})
            continue
        at = times.get(it["line"])
        tail = clip(text[len(label):].lstrip("：:，,。 "), 120)
        asked = text.split("❓", 1)[1].strip() if "❓" in text else None   # 「▶ …，❓ 等常浩合并」：项里另写了等常浩的事
        base = dict(id_=id_, source="mainline", project=name, what=label, done_when="主线项标 ✅", line=it["line"],
                    _refs=sorted({f"pr:{name}#{n}" for n in NUM.findall(text)}))
        if mark == "❓" or asked is not None:
            ask = clip(asked if asked is not None else (tail or label), 120)
            rows.append(make(state="等你", owner=OWNER, at=at, evidence="CURRENT 这一项最近一次改动", next_=f"常浩定：{ask}",
                             check_at=next_slip(at) if at else None, **base))
        elif mark == "▶":
            rows.append(make(state="在做", owner=UNKNOWN, at=at, evidence="CURRENT 这一项最近一次改动",
                             next_=f"来源没写下一步（原文：{tail}）" if tail else "来源没写下一步", wait="in_progress", **base))
        else:
            rows.append(make(state="等外部", owner=UNKNOWN, at=at, evidence="CURRENT 这一项最近一次改动", next_="等前一项完成", **base))
    return rows, parked, pending


def heading_candidates(text):
    return [{"text": m.group(2), "line": no, "kind": "节标题"} for no, line in enumerate(text.splitlines(), 1)
            for m in [HEADING.match(line)] if m and PENDING.search(m.group(2))]


def pending_numbers(text):
    """一句「待合并」的话指的是哪些号。先看写着待合并的那半句（逗号、括号之间）里的号；没有就看它所在的那一句
    （句号、分号之间），再没有就往前一句一句找。写着「已合并」的半句里的号不算。"""
    def numbers(part):
        merged = {n for c in CLAUSE.split(part) if "已合并" in c for n in NUM.findall(c)}
        return {int(n) for n in NUM.findall(part) if n not in merged}
    sentences, found = SENTENCE.split(LINK.sub(r"\1", text)), set()
    for i, sentence in enumerate(sentences):
        if not PENDING.search(sentence):
            continue
        near = {int(n) for c in CLAUSE.split(sentence) if PENDING.search(c) for n in NUM.findall(c)}
        if near:
            found |= near
            continue
        for back in range(i, -1, -1):
            if numbers(sentences[back]):
                found |= numbers(sentences[back])
                break
    return sorted(found)


def mainline_source(projects, fetch):
    rows, parked, unparsed, candidates = [], [], [], []
    cover = {"ok": [], "none": [], "failed": {}, "old": [], "fetch_failed": {}}
    for p in projects:
        name, root, main = key_of(p), root_of(p), p.get("main") or "main"
        if fetch and p.get("github"):
            try:
                r = subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin", main], capture_output=True, text=True,
                                   timeout=FETCH_TIMEOUT, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
                if r.returncode:
                    cover["fetch_failed"][name] = clip(r.stderr or r.stdout or f"git 退出码 {r.returncode}", 120)
            except subprocess.TimeoutExpired:
                cover["fetch_failed"][name] = f"{FETCH_TIMEOUT} 秒没拉完"
            except OSError as e:
                cover["fetch_failed"][name] = clip(e, 120)
        try:
            ref = pdaily_data.main_ref(root, main)
            text = pdaily_data.show(root, ref, "CURRENT.md")
        except pdaily_data.Fail as e:
            cover["failed"][name] = str(e)
            continue
        if ref is None and os.path.isdir(str(root)):   # 目录在、分支找不到（改过名、名册旧了）：这是没取到，不是「这个项目没有 CURRENT」
            cover["failed"][name] = f"找不到主分支 {main}（本机和远端都没有，名册里写的可能旧了）"
            continue
        if text is None:
            cover["none"].append(name)
            continue
        cover["ok"].append(name)
        seen = {}
        for c in heading_candidates(text):
            candidates.append({**c, "project": name, "repo": p.get("github")})
        names = set()
        for s in mainlines(text):
            title = clip(s["title"], 60)
            if s.get("stray"):   # 像主线却认不出的行、只有计数的行：只报「未解析」，不占这条主线的名字
                unparsed += [{"project": name, "mainline": title, **u} for u in s["unparsed"]]
                continue
            if s["old"]:
                cover["old"].append({"project": name, "title": title, "why": "标题写着旧主线"})
                continue
            if mainline_key(s["title"]) in names:   # CURRENT 新记录在上：下面同名的一节是这条主线以前的样子
                cover["old"].append({"project": name, "title": title, "why": "同名主线的旧快照，只认最上面一节"})
                continue
            names.add(mainline_key(s["title"]))
            got, held, pend = mainline_rows(name, s, blame_times(root, ref, s["line"], s["end"]), seen)
            rows += got
            parked += held
            unparsed += [{"project": name, "mainline": title, **u} for u in s["unparsed"]]
            candidates += [{**c, "project": name, "repo": p.get("github")} for c in pend]
    return rows, parked, unparsed, candidates, cover


# ---------- Claude 桌面会话 ----------

def inbox_module():
    """拍板清单脚本，按文件加载（它在 my-skills 仓库里，不在本仓库）。"""
    path = Path(os.environ.get("DT_DECISION_INBOX",
                               "~/Documents/ChatGPT/个人/work/my-skills/skills/decision-inbox/scripts/decision_inbox.py")).expanduser()
    if not path.exists():
        raise Fail(f"找不到拍板清单脚本 {path}")
    try:
        spec = importlib.util.spec_from_file_location("decision_inbox", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:   # 别的仓库的脚本，加载出什么错都只让这一个来源取不到
        raise Fail(f"拍板清单脚本加载不了（{type(e).__name__}：{e}）")


def read_metas(meta_dir):
    """桌面应用的会话元数据：{会话号: 元数据}。scan() 给的行里没有工作目录和 PR 所在的仓库，这里补读。"""
    out = {}
    for path in Path(meta_dir).glob("**/local_*.json"):
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("sessionId"):
            out[m["sessionId"]] = m
    return out


def project_at(projects, cwd):
    """这个工作目录属于名册里的哪个项目（项目目录本身，或它下面的 worktree）；都不是返回 None。"""
    if not cwd:
        return None
    best = None
    for p in projects:
        root = str(root_of(p)).rstrip("/")
        if (cwd == root or cwd.startswith(root + "/")) and (best is None or len(root) > len(str(root_of(best)))):
            best = p
    return best


def session_row(row, name, meta):
    """拍板清单脚本给的一个会话 → 账本的一行（规格第 5 节「会话的状态词」一列）；状态词不归账本管的返回 None。"""
    word, at = row.get("word"), parse_ts(row.get("statusAt"))
    said = f"{SAID.format(word)}：{clip(row.get('status'), 80)}"
    step = clip(row.get("next"), 200) or "来源没写下一步"
    refs = sorted({f"pr:{(x.get('repo') or '').split('/')[-1] or name}#{x.get('prNumber')}"
                   for x in meta.get("prs") or [] if isinstance(x, dict) and x.get("prNumber")})
    base = dict(id_=f"session:{row['id']}", source="session", project=name, what=row.get("title") or "（无标题）",
                done_when="会话写出〔已结束〕", _refs=refs)
    if word == "后台进行中":
        eta = ETA.search(f"{row.get('status') or ''} {row.get('next') or ''}")
        amount = 0.5 if eta and eta.group(1) == "半" else float(eta.group(1)) if eta else 0
        due = at + dt.timedelta(minutes=amount * (1 if eta.group(2) == "分钟" else 60)) if eta and at else None
        return make(state="在做", owner=f"会话「{clip(row.get('title') or '无标题', 30)}」", at=at, evidence=said, next_=step,
                    wait="session_bg", check_at=due, **base)
    if row.get("replyUnanswered"):   # sift() 的标记：常浩回过话，过了两小时会话还没出新状态
        own = [r for r in row.get("userAfter") or [] if not r.get("peer")]
        replied = parse_ts(own[-1].get("at")) if own else at
        return make(state="卡住", owner=UNKNOWN, at=replied, evidence="常浩回过话，会话之后没有新状态", next_="打开这个会话看一眼，或把那句话重发给它",
                    check_at=replied, blocked="常浩回过话，会话 2 小时没出新状态（多半没接到）", **base)
    if word == "受阻":
        return make(state="卡住", owner=UNKNOWN, at=at, evidence=said, next_=step, check_at=next_slip(at) if at else None,
                    blocked=clip(row.get("status"), 120) or "会话写了〔受阻〕，没写原因", **base)
    if word in ("等你决定", "等你操作"):
        done = GONE_NOTE if row.get("prDone") else ""
        return make(state="等你", owner=OWNER, at=at, evidence=said + done, next_=step, check_at=next_slip(at) if at else None, **base)
    return None


def session_source(projects, now):
    """（行，覆盖情况，{命令行会话号: 账本里的会话行编号}）。"""
    try:
        di = inbox_module()
    except Fail as e:
        return [], {"failed": str(e)}, {}
    meta_dir = os.environ.get("DT_OPENLOOP_META_DIR") or di.META_DIR
    scanned, skipped = di.scan(meta_dir, os.environ.get("DT_OPENLOOP_LOGS_DIR") or di.PROJECTS_DIR, SESSION_DAYS, now.timestamp())
    inbox = os.environ.get("DT_OPENLOOP_INBOX_DIR") or di.INBOX_DIR
    waiting, dropped = di.sift(scanned, di.load_ignored(inbox), di.listed_before(inbox)[1], now.timestamp())
    metas = read_metas(meta_dir)
    rows, cli, outside, bad = [], {}, 0, 0
    for r in waiting + [x for x in scanned if x.get("word") == "后台进行中"]:
        try:
            meta = metas.get(r["id"]) or {}
            p = project_at(projects, meta.get("originCwd") or meta.get("cwd"))
            got = session_row(r, key_of(p), meta) if p else None
        except Exception:   # 这一个会话的数据不是预想的样子：跳过它，记个数
            bad += 1
            continue
        if not p:
            outside += 1
        elif got:
            rows.append(got)
            for k in ("cliSessionId", "lastAssistantCliSessionId"):
                if meta.get(k):
                    cli[meta[k]] = got["id"]
    known = set(di.WAITING) | {"后台进行中", "已结束"}
    return rows, {"scanned": len(scanned), "rows": len(rows), "not_personal": outside, "bad": bad, "skipped": skipped, "dropped": dropped,
                  "other_words": sum(1 for x in scanned if x.get("word") not in known)}, cli


# ---------- 驿窗送审 ----------

def handoffs_path():
    return Path(os.environ.get("DT_POSTPANE_HANDOFFS",
                               "~/Library/Application Support/app.postpane.mac/chatgpt/handoffs.json")).expanduser()


def at_ms(ms):
    return dt.datetime.fromtimestamp(ms / 1000, TZ).replace(microsecond=0) if ms else None


def relay_facts(h):
    """一条接力走到哪了。回信算送回了：之后有一步 back 且会话那边开始处理（旧记录没有 delivery 也算），
    或驿窗在后台替会话跑了（wake），或会话又交了一轮（work）。"""
    steps = [s for s in h.get("steps") or [] if isinstance(s, dict)]

    def last(kind, rows=steps):
        return next((s for s in reversed(rows) if s.get("kind") == kind), None)
    ask, reply = last("ask"), last("reply")
    replied = reply is not None and (ask is None or steps.index(reply) > steps.index(ask))
    after = steps[steps.index(reply) + 1:] if replied else []
    back = last("back", after)
    got = any((s.get("kind") == "back" and s.get("delivery") in (None, "started")) or s.get("kind") in ("wake", "work") for s in after)
    why = ""
    if replied and not got:
        delivery = (back or {}).get("delivery")
        why = {"waiting": "留给会话了，会话还没接走", "taken": "插件接走了，会话还没开始处理", "expired": "留了一小时没被接走",
               "refused": "会话那边拒收", "cancelled": "被取消"}.get(delivery) or clip((last("fail", after) or {}).get("text"), 80)
    # 第几轮照驿窗自己的算法（gate.rs 的 counted）：常浩上次发话之后算数的回信数，没重新起算时加上前面接力带过来的
    start = min(int(h.get("roundsFrom") or 0), len(steps))
    chain = steps[start:]
    done = (0 if start else int(h.get("carried") or 0)) + sum(
        1 for i, x in enumerate(chain) if x.get("kind") == "reply" and not (i and chain[i - 1].get("kind") == "format"))
    n = max(done if replied else done + 1, 1)
    mending = replied and bool(after) and after[-1].get("kind") == "format"   # 驿窗请 ChatGPT 只补最后几行结论，还在等
    return {"mending": after[-1] if mending else None, "ask": ask, "reply": reply, "replied": replied, "got": got, "back": back, "why": why, "stop": last("stop"),
            "end": last("end"), "verdict": (reply or {}).get("verdict") if replied else None,
            "label": f"{h['risk']} 第 {n} 轮" if h.get("risk") else f"第 {n} 轮"}


def relay_row(h, name, f, superseded, now):
    """（行或 None，没收的原因）。规格第 5 节「驿窗接力」一列：送出对方在看 → 等外部；回信没送回会话、送审失败 → 卡住。"""
    live = h.get("status") in ("opening", "open")
    updated = at_ms(h.get("updatedAt"))
    said = VERDICT.get(f["verdict"], "没写结论")
    base = dict(id_=f"relay:{h['id']}", source="relay", project=name, what=h.get("title") or "送审", done_when="回信送回会话、接力结束")
    if not live and superseded:
        return None, None
    row = relay_state(h, f, live, updated, said, base)
    if row and not live and parse_ts(row["evidence_at"]) and now - parse_ts(row["evidence_at"]) > RELAY_DAYS:
        return None, "too_old"           # 按回信、失败的那一刻算，不按驿窗后来自动收尾的时间
    return row, None


def relay_state(h, f, live, updated, said, base):
    if h.get("status") == "failed":
        return make(state="卡住", owner=UNKNOWN, at=at_ms(h.get("phaseAt")) or updated, evidence="送审没送成",
                    next_="重新送审，或告诉发起的会话没送成", wait="relay_failed", blocked=f"送审失败：{h.get('error') or '原因没写'}", **base)
    if live and f["mending"] and not h.get("halt"):
        return make(state="等外部", owner="ChatGPT", at=at_ms(f["mending"].get("at")), evidence="回信没写结论，驿窗已请 ChatGPT 补上，对方在看",
                    next_=f"等 {f['label']}补上结论", wait="relay_wait", **base)
    if f["replied"] and not f["got"]:
        if not live and not (f["end"] or {}).get("auto"):
            return None            # 常浩自己结束的接力：他看过了
        return make(state="卡住", owner=UNKNOWN, at=at_ms(f["reply"].get("at")),
                    evidence=f"ChatGPT 已回信（{said}），没送回会话" + ("" if live else "，接力已自动结束"),
                    next_=f"把「{said}」的回信转给发起的会话（在驿窗里转回，或直接告诉它结论）", wait="reply_not_returned",
                    blocked="回信没送回会话" + (f"：{f['why']}" if f["why"] else ""), **base)
    if not live:
        return None
    if h.get("halt"):
        return make(state="卡住", owner=UNKNOWN, at=at_ms((f["stop"] or {}).get("at")) or updated, evidence="驿窗自动往返停下",
                    next_="常浩或发起的会话接手（驿窗已把这条接力交还给人）", wait="reply_not_returned", blocked=clip(h["halt"], 120), **base)
    if not f["replied"]:
        return make(state="等外部", owner="ChatGPT", at=at_ms((f["ask"] or {}).get("at")) or at_ms(h.get("phaseAt")),
                    evidence="已送 ChatGPT 审查，对方在看", next_=f"等 {f['label']}回信", wait="relay_wait", **base)
    if f["verdict"] == "pass":
        return None                # 通过、回信也送回了：送审这件事完了
    return make(state="在做", owner=UNKNOWN, at=at_ms((f["back"] or f["reply"]).get("at")), evidence=f"回信（{said}）已送回会话",
                next_="发起的会话返修后交回", wait="rework", **base)


def relay_source(projects, now, sessions):
    """（行，覆盖情况，{PR 的编号: [审查记录]}）。sessions：{命令行会话号: 账本里的会话行编号}。"""
    path = handoffs_path()
    if not path.exists():
        return [], {"missing": True}, {}
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("不是列表")
        items = [h for h in items if isinstance(h, dict) and h.get("id")]
    except (OSError, ValueError) as e:
        return [], {"failed": f"读不了 {path.name}（{e}）"}, {}
    repos = {(p.get("github") or "").lower(): p for p in projects if p.get("github")}
    rows, reviews, cover = [], {}, {"read": len(items), "rows": 0, "not_personal": 0, "too_old": 0, "bad": 0}
    for h in items:
        try:
            relay_item(h, items, projects, repos, sessions, now, rows, reviews, cover)
        except Exception:   # 这一条接力的字段不是预想的样子（驿窗改了结构）：跳过它，记个数
            cover["bad"] += 1
    cover["rows"] = len(rows)
    return rows, cover, reviews


def relay_item(h, items, projects, repos, sessions, now, rows, reviews, cover):
    """一条接力 → 可能的一行、可能的一条审查记录；直接改 rows、reviews、cover。"""
    repo, _, number = str(h.get("pr") or "").partition("#")
    named = repos.get(repo.lower()) if number.isdigit() else None
    p = named or project_at(projects, h.get("cwd"))
    if not p:
        cover["not_personal"] += 1
        return
    name, f = key_of(p), relay_facts(h)
    pr_id = f"pr:{name}#{number}" if named else None
    newer = [o for o in items if o is not h and (o.get("createdAt") or 0) > (h.get("createdAt") or 0)
             and (o.get("claudeSession") == h.get("claudeSession") or (h.get("pr") and o.get("pr") == h.get("pr")))]
    row, why = relay_row(h, name, f, bool(newer), now)
    if why:
        cover[why] += 1
    if row:
        row["_refs"] = [pr_id] if pr_id else []
        row["_session"] = sessions.get(h.get("claudeSession"))
        rows.append(row)
    if pr_id and f["verdict"] in VERDICT:
        reviews.setdefault(pr_id, []).append({"verdict": f["verdict"], "at": at_ms(f["reply"].get("at")), "by": f"驿窗 {f['label']}", "head": h.get("head")})
    elif pr_id and row and row["state"] == "等外部":
        reviews.setdefault(pr_id, []).append({"verdict": None, "flying": True, "at": parse_ts(row["evidence_at"]), "by": f"驿窗 {f['label']}", "label": f["label"]})


# ---------- 同一件事并成一行 ----------

def merge(rows, since, now):
    """规格第 7 节。只靠明文：refs 里正好一个开着的 PR 就并过去，session_ref 指到的会话也并；不止一个 PR 的不并、互相记一笔。
    一行里最多一个 PR：要并的两边各带着不同的 PR（会话记着 #2，它发起的接力写的是 #1），就不并，互相记一笔。
    并成一行后，各部分自己原来的样子留在 parts 里：下次哪个来源取不到，按 parts 把它那部分带回来。"""
    for r in rows:
        if "_refs" in r:
            r["refs"] = r.pop("_refs")
        if "_session" in r:
            r["session_ref"] = r.pop("_session")
        if not r.get("carried"):
            r["since"] = since.get(r["id"]) or iso(now)
    by_id = {r["id"]: r for r in rows}
    parent = {i: i for i in by_id}
    pr_of = {i: i if r["source"] == "pr" else None for i, r in by_id.items()}   # 每一组里的那个 PR
    related = []

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(a, b):
        ra, rb = find(a), find(b)
        if ra != rb and pr_of[ra] and pr_of[rb]:
            related.append((a, b))
            return
        parent[ra] = rb
        pr_of[rb] = pr_of[rb] or pr_of[ra]
    for r in rows:
        prs = [i for i in r.get("refs") or [] if i in by_id and i != r["id"]]
        if len(prs) == 1:
            join(r["id"], prs[0])
        related += [(r["id"], i) for i in prs if len(prs) > 1]
        if r.get("session_ref") in by_id:
            join(r["id"], r["session_ref"])
    groups = {}
    for r in rows:
        groups.setdefault(find(r["id"]), []).append(r)

    def need(m):
        check = parse_ts(m.get("check_at"))
        ts = check.timestamp() if check else None
        return (STATES.index(m["state"]), ts is None, (-ts if m["state"] in RECENT_WINS else ts) if ts else 0, PRIMARY.index(m["source"]), m["id"])
    out, home = [], {}
    for members in groups.values():
        members.sort(key=lambda m: (PRIMARY.index(m["source"]), m["id"]))
        main = members[0]
        # 会话、主线项、接力的说法比 PR 最近一次提交或审查记录还旧的，已经过时：不参与定状态，只记在 links（标 old）
        active = parse_ts(main.get("active_at")) if main["source"] == "pr" else None
        old = {m["id"] for m in members[1:] if active and parse_ts(m.get("evidence_at")) and parse_ts(m["evidence_at"]) < active}
        lead = min((m for m in members if m["id"] not in old), key=need)
        row = {**lead, **{k: main.get(k) for k in ("id", "what", "source", "project", "done_when", "refs", "session_ref")}}
        asks = [m["ask"] for m in members if m.get("ask") and m["state"] == lead["state"] and m["id"] not in old]
        if asks:   # 新通道派单自己说了要他做哪种（冻结单并进只改文档的 PR，两行都是等你、PR 行当了主导）：拍板优先
            row["ask"] = "拍板" if "拍板" in asks else asks[0]
        if len(members) > 1:
            row.pop("url", None)
            row.pop("line", None)
            if main.get("url"):
                row["url"] = main["url"]
            row["since"] = min(m["since"] for m in members)
            row["parts"] = [dict(m) for m in members]
            row["links"] = [{**{k: m[k] for k in ("id", "state", "what", "since", "next", "blocked")}, **({"old": True} if m["id"] in old else {})}
                            for m in members[1:]]
            same = [m["blocked"] for m in sorted(members, key=need) if m["state"] == lead["state"] and m["blocked"] and m["id"] not in old]
            row["blocked"] = "；".join(dict.fromkeys(same))   # 同样最需要人的几条各自卡在哪都写上，先到点的在前
            if any(m.get("carried") for m in members):
                row.update(carried=True, carried_from=min(m["carried_from"] for m in members if m.get("carried")))
            else:
                row.pop("carried", None)
        for m in members:
            home[m["id"]] = row
        out.append(row)
    for a, b in related:
        for here, there in ((a, b), (b, a)):
            if home[here] is not home[there] and all(x["id"] != there for x in home[here]["links"]):
                home[here]["links"].append({"id": there, "state": by_id[there]["state"], "what": by_id[there]["what"], "related": True})
    return out


# ---------- 记录和事实对不上 ----------

def lookup(hub, repo, numbers):
    """{号: GitHub 上这个号现在的样子}；查不到的号不在里面。"""
    owner, name = repo.split("/", 1)
    fields = " ".join(LOOKUP % (n, n) for n in numbers)
    data = hub._run("api", "graphql", "-f", f"query=query($o:String!,$n:String!){{repository(owner:$o,name:$n){{{fields}}}}}",
                    "-f", f"o={owner}", "-f", f"n={name}", partial=True)
    got = ((data or {}).get("data") or {}).get("repository") or {}
    return {n: got[f"i{n}"] for n in numbers if got.get(f"i{n}")}


def fact_of(n, node, named=()):
    """这个号是不是已经合并 / 关了；还开着返回 None。named：这句话里点了名的号，issue 的 PR 已经点了名就不再重复说。"""
    def ended(x, what):
        if x.get("state") == "MERGED":
            return f"{what}已合并（{stamp(parse_ts(x.get('mergedAt')))}）"
        if x.get("state") == "CLOSED":
            return f"{what}已关闭、没合并（{stamp(parse_ts(x.get('closedAt')))}）"
    if node.get("__typename") == "PullRequest":
        return ended(node, f"PR #{n} ")
    prs = ((node.get("closedByPullRequestsReferences") or {}).get("nodes")) or []
    if prs:
        if any(x.get("number") in named for x in prs):
            return None
        facts = [ended(x, f"issue #{n} 的 PR #{x.get('number')} ") for x in prs]
        return "；".join(facts) if all(facts) else None
    if node.get("state") == "CLOSED":
        return f"issue #{n} 已关闭（{stamp(parse_ts(node.get('closedAt')))}），没找到它的 PR"


def mismatches_of(candidates, hub):
    """（对不上的，没核成的 {项目: 原因}）。"""
    by_repo, out, failed, used = {}, [], {}, {}
    for c in candidates:
        c["numbers"] = pending_numbers(c["text"])
        if c["repo"] and c["numbers"]:
            by_repo.setdefault(c["repo"], []).append(c)
    for repo, items in by_repo.items():
        numbers = sorted({n for c in items for n in c["numbers"]})
        if len(numbers) > LOOKUP_MAX:
            failed[items[0]["project"]] = f"「待合并」里提到 {len(numbers)} 个号，只核了前 {LOOKUP_MAX} 个"
            numbers = numbers[:LOOKUP_MAX]
        try:
            states = lookup(hub, repo, numbers)
        except (pdaily_data.Fail, Fail) as e:
            failed[items[0]["project"]] = str(e)
            continue
        for c in items:
            facts = [f for f in (fact_of(n, states[n], c["numbers"]) for n in c["numbers"] if n in states) if f]
            if facts:
                id_ = f"mismatch:{c['project']}/CURRENT:{'+'.join(f'#{n}' for n in c['numbers'])}@{c['kind']}"
                used[id_] = used.get(id_, 0) + 1   # 两节标题提到同一个号：后来的加 ~2，各记各的进账本时间
                out.append({"id": id_ + (f"~{used[id_]}" if used[id_] > 1 else ""),
                            "project": c["project"], "where": f"CURRENT.md 第 {c['line']} 行（{c['kind']}）",
                            "text": clip(c["text"], 200), "fact": "；".join(facts), "since": None})
    return out, failed


# ---------- 账本 ----------

def out_dir():
    return Path(os.environ.get("DT_OPENLOOP_DIR", "~/Library/Application Support/DigitalTwin/openloop")).expanduser()


# ---------- 处置记录 ----------

DISPOSE_NEED = (("fact", "当前事实"), ("owner", "负责人"), ("next", "下一步"), ("done_when", "怎样算完"), ("by", "谁记的"))
DISPOSE_MORE = (("blocked", "卡在哪"), ("ref", "出处"), ("note", "备注"))   # 可以不写：没卡住、没有消息号
DISPOSE_MAX = 200                     # 每项最多这么多字：处置是一句话，不是报告；超了拒绝，不悄悄截
DONE_WORDS = re.compile(r"已完成|已办完|已结束|已收口|已闭环|已解决|做完了|完成了|办完了|可以关")   # 对不上的不许这样写


def dispositions_path(folder=None):
    return (folder or out_dir()) / "dispositions.jsonl"


def disposable(ledger):
    """账本里能记处置的编号 → 哪一类：每一行、并进这一行的各来源（parts，只是「相关」的不算），和对不上的那几条。"""
    out = {}
    for r in ledger.get("rows") or []:
        for x in [r, *(r.get("parts") or [])]:
            if x.get("id"):
                out.setdefault(x["id"], "row")
    for m in ledger.get("mismatches") or []:
        if m.get("id"):
            out[m["id"]] = "mismatch"
    return out


def dispose(id_, fields, now=None, folder=None):
    """记一条处置，返回记下的那条。编号要在当前账本里；必填项不能空、每项不超过 DISPOSE_MAX 字；
    下次检查时间要晚于现在；对不上的那一类不能写成已完成。只追加 dispositions.jsonl，不碰 ledger.json。"""
    now = now or current()
    ledger = read_ledger(folder)
    if ledger is None:
        raise Fail(f"还没有账本：{(folder or out_dir()) / 'ledger.json'} 不存在或读不了，先跑 scan")
    kind = disposable(ledger).get(id_)
    if kind is None:
        raise Fail(f"账本里没有 {id_}（按 {ledger.get('generated_at')} 那份账本）：编号照 show 的原样写，并成一行的写那一行或其中任一来源的编号")
    rec = {}
    for key, label in DISPOSE_NEED + DISPOSE_MORE:
        v = " ".join(str(fields.get(key) or "").split())
        if not v and (key, label) in DISPOSE_NEED:
            raise Fail(f"处置缺{label}")
        if len(v) > DISPOSE_MAX:
            raise Fail(f"{label}超过 {DISPOSE_MAX} 字，写短一点")
        rec[key] = v
    check = parse_ts(fields.get("check_at"))
    if not check:
        raise Fail("下次检查时间读不懂：写成 2026-11-28T10:00（北京时间）这样")
    if check <= now:
        raise Fail(f"下次检查时间 {stamp(check)} 不晚于现在 {stamp(now)}")
    if kind == "mismatch":
        hit = next((label for key, label in DISPOSE_NEED + DISPOSE_MORE if DONE_WORDS.search(rec[key])), None)
        if hit:
            raise Fail(f"{id_} 是「记录和事实对不上」：只能交给事实核对，处置的{hit}不能写成已完成")
    rec = {"at": iso(now), "id": id_, "kind": kind, **rec, "check_at": iso(check), "ledger_at": ledger.get("generated_at")}
    path = dispositions_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    with open(path, "ab") as f:
        fcntl.flock(f, fcntl.LOCK_EX)        # 几个会话同时记：整行写完再放锁，不交错
        try:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return rec


def read_dispositions(folder=None):
    """(每个编号最近的一条处置, 读不懂的行数)。文件没有就是没有处置；坏行（写到一半、手改坏了）跳过并计数，不拖垮扫描。"""
    latest, bad = {}, 0
    try:
        with open(dispositions_path(folder), "rb") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            raw = f.read()
    except FileNotFoundError:
        return {}, 0
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            ok = isinstance(rec, dict) and isinstance(rec.get("id"), str) and parse_ts(rec.get("at"))
        except ValueError:
            ok = False
        if not ok:
            bad += 1
            continue
        old = latest.get(rec["id"])
        if old is None or parse_ts(rec["at"]) >= parse_ts(old["at"]):   # 同一时刻记了两条：后写的算
            latest[rec["id"]] = rec
    return latest, bad


def attach(ledger, folder=None):
    """把处置并进账本：每行、每条对不上的多一个 disposition（它和并进来的各来源里最近的一条，没有就是 None）。
    只加这一个字段，来源算出来的 state、owner、next、check_at 不动；账本里已经没有的编号，处置留在文件里不报错，只计数。"""
    latest, bad = read_dispositions(folder)
    used = set()
    for r in ledger.get("rows") or []:
        ids = [r["id"], *(x["id"] for x in r.get("parts") or [])]
        mine = [latest[i] for i in ids if i in latest]
        used.update(i for i in ids if i in latest)
        r["disposition"] = max(mine, key=lambda d: parse_ts(d["at"])) if mine else None
    for m in ledger.get("mismatches") or []:
        m["disposition"] = latest.get(m["id"])
        used.update([m["id"]] if m["id"] in latest else [])
    ledger.setdefault("coverage", {})["dispositions"] = {
        "rows": sum(1 for x in (ledger.get("rows") or []) + (ledger.get("mismatches") or []) if x.get("disposition")),
        "orphan": len(set(latest) - used), "bad": bad}
    return ledger


def ask_kind(r):
    """一行「等你」的事要常浩做哪种：合并 / 点卡 / 亲手做 / 拍板。午晚单收前三种，日报的拍板候选收最后一种。
    PR 行的「下一步」可能来自并进来的会话：写着合并才算合并，别的是要他亲手做的事。"""
    ev, nx = r.get("evidence") or "", r.get("next") or ""
    if r.get("ask") in HANDS + ["拍板"]:     # 来源自己说了要他做哪种（新通道派单：停了单而 PR 还开着是合并，冻结、收掉是拍板）
        return r["ask"]                     # 先看这个：冻结的单并进它开着的 PR 那一行，行的来源是 pr，却是要他拍板
    if r.get("source") == "pr":
        return "合并" if "合并" in nx else "亲手做"
    if re.search("交接卡|任务卡", nx):
        return "点卡"
    if SAID.format("等你操作") in ev:
        return "亲手做"
    return "拍板"


def waiting(ledger):
    """账本里「等你」的行分三堆：（要他亲手做的，等他拍板的，可能已过期的）。
    亲手做的按 合并 → 点卡 → 亲手做 排，同一类里挂得久的在前；过期的是它的 PR 都已合并或关闭的。"""
    rows = [r for r in ledger.get("rows") or [] if r.get("state") == "等你"]
    gone = [r for r in rows if GONE_NOTE in (r.get("evidence") or "")]
    live = [r for r in rows if r not in gone]
    todo = sorted((r for r in live if ask_kind(r) != "拍板"), key=lambda r: (HANDS.index(ask_kind(r)), r.get("evidence_at") or ""))
    return todo, [r for r in live if ask_kind(r) == "拍板"], gone


def day_ledger(day, folder=None):
    """某一天的账本快照（history/<日期>.json，那天最后一次扫描）；没有或读不了返回 None。"""
    try:
        data = json.loads(((folder or out_dir()) / "history" / f"{day}.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read_ledger(folder=None):
    try:
        data = json.loads(((folder or out_dir()) / "ledger.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def carry(prev, kind, failed, source=None):
    """这次没取到的，把上一份账本里它的东西原样带过来（标 carried、记下原来是哪次取的），不当成事情没了。
    kind：rows / parked / mismatches；failed：{项目: 原因}，或 True（整个来源这次取不到）；source：只带这一类来源的行。
    上一份里并成一行的，按各部分自己原来的样子（parts）带，带过来之后照常重新并行。"""
    if not failed:
        return []
    out = []
    for x in (prev or {}).get(kind, []):
        for m in (x.get("parts") or [x]) if kind == "rows" else [x]:
            if (failed is True or m.get("project") in failed) and (source is None or m.get("source") == source):
                kept = {k: v for k, v in m.items() if k != "parts"}
                if kind == "rows":
                    kept["links"] = []
                out.append({**kept, "carried": True, "carried_from": m.get("carried_from") or prev.get("generated_at")})
    return out


def guarded(source, *args):
    """会话、驿窗送审读的是别的项目的文件：里面出什么没料到的错，都只让这一个来源取不到，不拖垮整本账。"""
    try:
        return source(*args)
    except Exception as e:
        return [], {"failed": f"出错（{type(e).__name__}：{e}）"}, {}


def build(now, projects, hub, rules, prev=None, fetch=True):
    prev = prev or {}
    sessions, s_cover, cli = guarded(session_source, projects, now)
    relays, r_cover, reviews = guarded(relay_source, projects, now, cli)
    known = not r_cover.get("failed")
    if not known and "reviews" in prev:   # 送审记录这次没读到：PR 的状态照上一份记下的送审记录判，不说成「没有记录」
        reviews, known = {k: [{**r, "at": parse_ts(r.get("at"))} for r in v] for k, v in prev["reviews"].items()}, True
    prs, gh_cover = github_source(projects, hub, rules, reviews, known, now)
    open_prs_now, checked = {r["id"] for r in prs}, tuple(f"pr:{name}#" for name in gh_cover["ok"])
    done = [r for r in relays if any(ref.startswith(checked) and ref not in open_prs_now for ref in r["_refs"])] if checked else []
    relays = [r for r in relays if r not in done]
    if "rows" in r_cover:
        r_cover.update(rows=len(relays), pr_done=len(done))   # 它审的 PR 已经合并或关了：送审这件事不再算没完
    try:
        dispatch, d_cover = dispatch_source(projects, hub, rules, reviews, known, now, prev)
    except Exception as e:   # 凭证库读坏了、或出了没料到的错：只让这一个来源取不到，沿用上一份账本里它的行
        d_cover = {"failed": f"出错（{type(e).__name__}：{e}）" if not isinstance(e, Fail) else str(e),
                   "installed": ((prev.get("coverage") or {}).get("dispatch") or {}).get("installed")}   # 记着装过，下次库不在也不当没装
        tasks = [i.split(":", 1)[1] for i, m in parts_of(prev).items() if m.get("source") == "dispatch"]
        dispatch = [x for t in tasks for x in carry_task(prev, t)]   # 连同沙盒的 PR 行（GitHub 那一类不补它）
        d_cover["carried"] = sum(1 for x in dispatch if x["source"] == "dispatch")
    gh_old = carry(prev, "rows", gh_cover["failed"], "pr")
    dispatch = check_carried(dispatch, {r["id"] for r in prs}, gh_cover.get("ok") or [], hub)
    seen_prs = {r["id"] for r in prs + gh_old}
    dispatch = [r for r in dispatch if not (r["source"] == "pr" and r["id"] in seen_prs)]   # 活跃项目的 PR 上面已经有了（新取的或沿用的）
    lines, parked, unparsed, candidates, ml_cover = mainline_source(projects, fetch)
    mismatches, unchecked = mismatches_of(candidates, hub)
    parked += carry(prev, "parked", ml_cover["failed"])
    mismatches += carry(prev, "mismatches", {**ml_cover["failed"], **unchecked})
    old_sessions, old_relays = carry(prev, "rows", bool(s_cover.get("failed")), "session"), carry(prev, "rows", bool(r_cover.get("failed")), "relay")
    s_cover["carried"], r_cover["carried"] = len(old_sessions), len(old_relays)
    since = {x["id"]: x.get("since") for kind in ("rows", "parked", "mismatches") for x in prev.get(kind, [])}
    since.update({x["id"]: x["since"] for r in prev.get("rows", []) for x in (r.get("parts") or []) + (r.get("links") or []) if x.get("since")})
    rows = merge(prs + gh_old + dispatch + sessions + old_sessions + relays + old_relays
                 + lines + carry(prev, "rows", ml_cover["failed"], "mainline"), since, now)
    for x in parked + mismatches:
        x["since"] = since.get(x["id"]) or iso(now)
    for r in rows:
        check = parse_ts(r.get("check_at"))
        r["stalled"] = bool(check and now >= check)
    rows.sort(key=lambda r: (STATES.index(r["state"]), not r["stalled"], r.get("check_at") or "9", r["id"]))
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    counts.update({"停了": sum(1 for r in rows if r["stalled"]), "只计数": len(parked), "对不上": len(mismatches), "未解析": len(unparsed),
                   "会话没写状态行": (s_cover.get("skipped") or {}).get("没写状态行", 0),
                   "沿用": sum(1 for x in rows + parked + mismatches if x.get("carried"))})
    return {
        "version": VERSION, "generated_at": iso(now), "counts": counts, "rows": rows, "parked": parked,
        "mismatches": mismatches, "unparsed": unparsed,
        "reviews": {k: [{**r, "at": iso(r["at"])} for r in v] for k, v in reviews.items()},
        "coverage": {"projects": len(projects), "github": gh_cover, "mainline": ml_cover, "sessions": s_cover, "relays": r_cover,
                     "dispatch": d_cover,
                     "mismatch_unchecked": unchecked, "rules": None if rules else "合并规则读不了，没判 L0（不用审）"},
    }


def write(ledger, folder=None):
    folder = folder or out_dir()
    (folder / "history").mkdir(parents=True, exist_ok=True)
    text = json.dumps(ledger, ensure_ascii=False, indent=1) + "\n"
    for path in (folder / "ledger.json", folder / "history" / f"{ledger['generated_at'][:10]}.json"):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    return folder / "ledger.json"


def scan(now=None, fetch=True, hub=None, folder=None, save=True):
    now = now or current()
    try:
        rules = merge_officer.load_rules()
    except (OSError, ValueError):
        rules = None
    hub = hub or pdaily_data.GitHub(None, wait=netwait.wait_seconds())
    ledger = attach(build(now, roster(), hub, rules, prev=read_ledger(folder), fetch=fetch), folder)
    if save:
        write(ledger, folder)
    return ledger


# ---------- 打印 ----------

def summary(ledger):
    c = ledger["counts"]
    parts = [f"{s} {c[s]}" for s in STATES if c.get(s)]
    return (f"未闭环账本｜{stamp(parse_ts(ledger['generated_at']))} 扫描｜共 {len(ledger['rows'])} 行"
            + (f"：{'｜'.join(parts)}" if parts else "") + f"｜其中停了 {c['停了']}｜对不上 {c['对不上']}｜只计数 {c['只计数']}｜未解析 {c['未解析']}"
            + (f"｜沿用上一份 {c['沿用']} 条" if c.get("沿用") else "") + (f"｜没取到 {len(gaps(ledger))} 处" if gaps(ledger) else ""))


def gaps(ledger):
    """这次没取到、没核成的：账本不全要说出来，不能看着像没事。"""
    cov, out = ledger["coverage"], []
    for src, label in (("github", "GitHub"), ("mainline", "主线清单")):
        for name, why in (cov[src].get("failed") or {}).items():
            out.append(f"{label}·{name}：{why}（这个项目沿用上一份账本里的）")
    for name in cov["github"].get("capped") or []:
        out.append(f"GitHub·{name}：开着的 PR 超过 {PR_PAGE * PR_PAGES} 个，只取了前面的")
    for name, why in (cov["mainline"].get("fetch_failed") or {}).items():
        if name in (cov["mainline"].get("failed") or {}):   # 整个没读到的上面已经说了，这里不再说「读的是本机已有的版本」
            continue
        out.append(f"拉取·{name}：{why}（CURRENT 读的是本机已有的版本，可能旧）")
    d = cov.get("dispatch") or {}
    if d.get("installed") is not False and not d.get("failed"):
        if d.get("hub") not in (None, "ok"):
            out.append(f"新通道派单：{d['hub']}（轮次不明：上一份账本里有的单沿用，其余只按凭证和 PR 判）")
        for name, why in (d.get("pr_failed") or {}).items():
            out.append(f"新通道派单·{name}：PR 没查到（{why}）")
        if d.get("carried"):
            out.append(f"新通道派单：{d['carried']} 张单这次缺数据，沿用上一份账本里的")
    for src, label in (("sessions", "会话"), ("relays", "驿窗送审"), ("dispatch", "新通道派单")):
        if (cov.get(src) or {}).get("failed"):
            kept = cov[src].get("carried")
            out.append(f"{label}：{cov[src]['failed']}（" + (f"沿用上一份账本里的 {kept} 条" if kept else "上一份账本里也没有，这一类这次是空的") + "）")
        elif (cov.get(src) or {}).get("bad"):
            out.append(f"{label}：有 {cov[src]['bad']} 条的数据不是预想的样子，跳过了")
    for name, why in (cov.get("mismatch_unchecked") or {}).items():
        out.append(f"对不上·{name}：{why}（这个项目的「待合并」没核全，沿用上一份里它的）")
    if cov.get("rules"):
        out.append(cov["rules"])
    if (cov.get("dispositions") or {}).get("bad"):
        out.append(f"处置记录：dispositions.jsonl 有 {cov['dispositions']['bad']} 行读不懂，跳过了")
    return out


def disposition_line(d):
    """show 里有处置的行多这一行：谁什么时候记的、记了什么。处置是另记的，不改上面那行来源算出来的东西。"""
    return (f"处置：{stamp(parse_ts(d.get('at')))} {d.get('by')} 记｜事实：{d.get('fact')}"
            + (f"｜卡在：{d['blocked']}" if d.get("blocked") else "")
            + f"｜谁：{d.get('owner')}｜下一步：{d.get('next')}｜下次看：{stamp(parse_ts(d.get('check_at')))}｜怎样算完：{d.get('done_when')}"
            + (f"｜出处：{d['ref']}" if d.get("ref") else "") + (f"｜备注：{d['note']}" if d.get("note") else ""))


def render(ledger, now=None):
    now = now or current()
    out = [summary(ledger)]
    made = parse_ts(ledger["generated_at"])
    if made and now - made > STALE_LEDGER:
        out.append(f"⚠ 账本上次更新是 {stamp(made)}，已超过 24 小时，下面的可能过期")
    for state in STATES:
        rows = [r for r in ledger["rows"] if r["state"] == state]
        if not rows:
            continue
        out.append(f"\n【{state}】{len(rows)}")
        for r in rows:
            flag = "⚠ 停了｜" if r["stalled"] else ""
            out.append(f"  {flag}{r['id']}｜{r['what']}" + (f"（沿用 {stamp(parse_ts(r.get('carried_from')))} 那份）" if r.get("carried") else ""))
            out.append(f"      谁：{r['owner']}｜证据：{r['evidence']}｜下一步：{r['next']}"
                       + (f"｜卡在：{r['blocked']}" if r["blocked"] else "")
                       + (f"｜下次看：{stamp(parse_ts(r['check_at']))}" if r["check_at"] else "｜下次看：不催")
                       + f"｜进账本：{stamp(parse_ts(r['since']))}")
            if r.get("disposition"):
                out.append("      " + disposition_line(r["disposition"]))
            together = [f"{x['id']}（{x['state']}" + (f"，下一步：{x['next']}" if x["state"] in STATES[:2] and x.get("next") else "") + "）"
                        for x in r["links"] if not x.get("related")]
            nearby = [x["id"] for x in r["links"] if x.get("related")]
            if together or nearby:
                out.append("      " + "｜".join(([f"并在这一行：{'、'.join(together)}"] if together else [])
                                              + ([f"相关、没并：{'、'.join(nearby)}"] if nearby else [])))
    if ledger["mismatches"]:
        out.append(f"\n【记录和事实对不上】{len(ledger['mismatches'])}")
        for m in ledger["mismatches"]:
            out.append(f"  {m['project']} {m['where']}：「{m['text']}」→ {m['fact']}")
            if m.get("disposition"):
                out.append("      " + disposition_line(m["disposition"]))
    if ledger["unparsed"]:
        out.append(f"\n【主线清单里没解析出来的】{len(ledger['unparsed'])}")
        for u in ledger["unparsed"]:
            out.append(f"  {u['project']} CURRENT.md 第 {u['line']} 行（主线：{u['mainline']}）：{u['text']}")
    if ledger["parked"]:
        marks = "｜".join(f"{m} {sum(1 for x in ledger['parked'] if x['mark'] == m)}" for m in PARKED)
        out.append(f"\n【只计数，不催】{marks}")
        for x in ledger["parked"]:
            out.append(f"  {x['mark']} {x['project']}｜{x['what']}")
    quiet = ledger["counts"].get("会话没写状态行")
    if quiet:
        out.append(f"\n【会话没写状态行】{quiet} 个：最后一次答复没有「当前状态：〔…〕」，判不了状态，只计数")
    old = ledger["coverage"]["mainline"].get("old") or []
    if old:
        out.append("\n【没收的旧主线】" + "；".join(f"{o['project']}「{o['title']}」（{o.get('why', '旧主线')}）" for o in old))
    miss = gaps(ledger)
    if miss:
        out.append("\n【这次没取到的】")
        out += [f"  {m}" for m in miss]
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="未闭环账本：扫描并打印还没完的事（只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="扫一遍各来源，写 ledger.json 和当天快照")
    s.add_argument("--no-fetch", action="store_true", help="不先拉各项目的远端主分支（CURRENT 可能读到旧的）")
    s.add_argument("--dry-run", action="store_true", help="只打印账本 JSON，不写文件")
    v = sub.add_parser("show", help="打印当前账本")
    v.add_argument("--json", action="store_true", help="原样输出 ledger.json")
    d = sub.add_parser("dispose", help="给账本里一行记一条处置（只追加 dispositions.jsonl，下次 scan 并进这一行）")
    d.add_argument("--id", required=True, help="账本编号，照 show 的原样写")
    d.add_argument("--fact", required=True, help="当前事实")
    d.add_argument("--blocked", default="", help="卡在哪；没卡住不写")
    d.add_argument("--owner", required=True, help="现在谁负责")
    d.add_argument("--next", required=True, help="下一步：谁做什么")
    d.add_argument("--check-at", required=True, help="下次检查时间，如 2026-11-28T10:00（北京时间），要晚于现在")
    d.add_argument("--done-when", required=True, help="怎样算完")
    d.add_argument("--by", required=True, help="谁记的，如 猛鬼兽")
    d.add_argument("--ref", default="", help="出处：常浩那条消息的消息号、卡片版本等")
    d.add_argument("--note", default="", help="备注：他的原话和你的理解（原话里有受限内容的不抄）")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "dispose":
            rec = dispose(a.id, {k: getattr(a, k) for k in ("fact", "blocked", "owner", "next", "check_at", "done_when", "by", "ref", "note")})
            print(json.dumps({"ok": True, "disposition": rec}, ensure_ascii=False))
            return 0
        if a.cmd == "scan":
            ledger = scan(fetch=not a.no_fetch, save=not a.dry_run)
            if a.dry_run:
                print(json.dumps(ledger, ensure_ascii=False, indent=1))
            else:
                print(summary(ledger))
                print(out_dir() / "ledger.json")
            return 0
        ledger = read_ledger()
        if ledger is None:
            print(f"还没有账本：{out_dir() / 'ledger.json'} 不存在或读不了，先跑 scan")
            return 1
        print(json.dumps(ledger, ensure_ascii=False, indent=1) if a.json else render(ledger))
        return 0
    except Fail as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False) if a.cmd == "dispose" else f"账本没更新：{e}")
        return 1
    except Exception as e:   # 没料到的错也收成一句：定时跑的时候要看得出「今天没更新」，不是一屏回溯
        why = f"出错（{type(e).__name__}：{e}）"
        print(json.dumps({"ok": False, "why": why + "，处置没记上"}, ensure_ascii=False) if a.cmd == "dispose" else f"账本没更新：{why}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
