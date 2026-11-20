#!/usr/bin/env python3
"""未闭环账本（#266 批 5a，规格 docs/未闭环账本-规格.md）：每件进了系统、还没完的事占一行，
写着现在谁负责、下一步是什么、什么时候再看；到点没动静就标「停了」。

只读：不续接、不恢复、不派单、不合并、不调 AI，不改任何来源方的数据。写的只有自己的输出目录
DigitalTwin/openloop/（ledger.json 当前账本、history/<日期>.json 每天一份快照）。
唯一会动到别处的是扫描前对名册各项目跑一次 `git fetch origin <主分支>`（只更新远端跟踪引用，不动工作区和本地分支）：
本机主目录常落后远端，不拉就会拿旧的 CURRENT 和 GitHub 的现状比，报出假的「对不上」；--no-fetch 关掉。

来源（5a-1）：
  GitHub     名册活跃仓库里开着的 PR，现查（gh api graphql）；不用合并官的 facts.jsonl（停更，且只记第一次看到的样子）
  主线清单   各项目 CURRENT「主线」节里的 ✅▶⏳❓⏸📌✖；✅ ✖ 不进账本，⏸ 📌 只计数（parked），解析不了的记 unparsed

一行的字段照规格第 4 节：id、what、state、owner、evidence、next、check_at、blocked、done_when、links、since；
另带 source、project、evidence_at、stalled（到 check_at 证据没变）。owner 只照来源写的填，判不出来写「未知」。
「记录和事实对不上」（规格第 6 节末）单列在 mismatches：CURRENT 的节标题或主线项写着待合并 / 未合并，
而那个 PR（或那张 issue 的 PR）已合并或已关。

命令：scan（扫描并写账本）、show（打印账本）。未安装；接日报、午晚单在 5a-3。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import merge_officer  # noqa: E402  复用风险档判定（L0 = 不用审）和检查失败的结论集合
import netwait  # noqa: E402  唤醒后等网络
import pdaily_data  # noqa: E402  复用 gh 取数（带等网络重试）、读主分支快照
import progress_data  # noqa: E402  名册路径

TZ = ZoneInfo("Asia/Shanghai")
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
}
STALE_LEDGER = dt.timedelta(hours=24)   # show：账本这么久没更新就在最上面说一句
PR_PAGE, PR_PAGES = 50, 4               # 每个仓库最多取 200 个开着的 PR，到了上限在 coverage 里标出来
FILES_MAX = 100                         # 文件列表只取前 100 个：取不全就不判 L0
LOOKUP_MAX = 60                         # 每个仓库一次最多核这么多个「待合并」里提到的号

PR_QUERY = """query($o:String!,$n:String!,$c:String){repository(owner:$o,name:$n){pullRequests(states:OPEN,first:%d,after:$c,
orderBy:{field:CREATED_AT,direction:ASC}){pageInfo{hasNextPage endCursor}nodes{
number title url isDraft createdAt updatedAt headRefName headRefOid mergeable changedFiles
files(first:%d){nodes{path}}
commits(last:1){nodes{commit{oid committedDate statusCheckRollup{contexts(first:100){nodes{__typename
... on CheckRun{name status conclusion completedAt} ... on StatusContext{context state createdAt}}}}}}}
reviews(last:50){nodes{state submittedAt commit{oid}}}}}}}""" % (PR_PAGE, FILES_MAX)
LOOKUP = ("i%d: issueOrPullRequest(number:%d){__typename ... on PullRequest{state mergedAt closedAt} "
          "... on Issue{state closedAt closedByPullRequestsReferences(first:10,includeClosedPrs:true){nodes{number state mergedAt closedAt}}}}")

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
MAINLINE = re.compile(r"^(?:#{1,6}\s*)?(旧)?主线[：:]\s*(.+?)\s*$")
RULE = re.compile(r"^-{3,}\s*$")
ITEM = re.compile(r"^(\s*)(?:[-*+]\s+)?([✅▶⏳❓⏸📌✖])️?\s*(.*)$")
BULLET = re.compile(r"^[-*+]\s+(.*)$")   # 顶格、没有标记的列表项：主线节里出现就是「未解析」
LABEL_END = re.compile(r"[：:（(。；;，,]")
NUM = re.compile(r"(?<![A-Za-z0-9_./-])#(\d+)")   # 本仓库的号；「postpane#466」这种别的仓库的不算
PENDING = re.compile(r"(?:待|等)[^，,。；;（）()\s]{0,6}合并|未合并")   # 待合并、待常浩合并、待常浩定合并、等常浩合并、未合并
CLAUSE = re.compile(r"[，,；;。（）()]")
SENTENCE = re.compile(r"[。；;]")
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


def pr_row(repo, node, rules, reviews=()):
    """一个开着的 PR 现在是哪种状态（规格第 5 节 GitHub 一列）。几种情况同时成立时取最需要人的那种。
    reviews：别的来源给的审查记录（5a-2 的驿窗送审），和 GitHub 上的审查一起按时间排，最新一条有结论的算数。"""
    name = repo.split("/")[-1]
    commit = ((((node.get("commits") or {}).get("nodes")) or [{}])[-1] or {}).get("commit") or {}
    pushed = parse_ts(commit.get("committedDate")) or parse_ts(node.get("updatedAt"))
    writer = BRANCH_OWNER.get((node.get("headRefName") or "").split("/")[0], UNKNOWN)
    who = writer if writer != UNKNOWN else "开这个 PR 的会话"
    checks = checks_of(commit)
    ci = "检查全过" if checks["has"] else "仓库没有 CI"
    verdicts = sorted(list(reviews) + github_reviews(node), key=lambda r: iso(r["at"]) or "")
    last = verdicts[-1] if verdicts else None
    base = dict(id_=f"pr:{name}#{node['number']}", source="pr", project=name, what=node.get("title") or "",
                done_when="PR 合并或关闭", url=node.get("url"))
    if node.get("isDraft"):
        return make(state="在做", owner=writer, at=pushed, evidence="最近一次提交（草稿 PR）",
                    next_=f"{who}做完后转成可审", wait="draft", **base)
    if checks["failing"]:
        names = "、".join(n for n, _ in checks["failing"])
        failed = max((t for _, t in checks["failing"] if t), default=pushed)
        return make(state="卡住", owner=writer, at=failed, evidence=f"检查失败：{names}", next_=f"{who}看失败原因并修",
                    wait="ci_failed", blocked=f"CI 失败：{names}", **base)
    if node.get("mergeable") == "CONFLICTING":
        return make(state="卡住", owner=writer, at=pushed, evidence="最近一次提交；和主分支有冲突",
                    next_=f"{who}变基解决冲突", wait="conflict", blocked="和主分支有冲突", **base)
    if last and last["verdict"] == "revise" and last["current"]:
        return make(state="卡住", owner=writer, at=last["at"], evidence=f"{last['by']}要求修改，之后没有新提交",
                    next_=f"{who}返修后重新送审", wait="rework", blocked=f"审查要求修改（{last['by']}）", **base)
    if checks["pending"]:
        return make(state="在做", owner=writer, at=pushed, evidence="最近一次提交，检查还在跑：" + "、".join(checks["pending"]),
                    next_="等检查跑完", wait="ci_running", **base)
    tier = tier_of(repo, node, rules)
    if (last and last["verdict"] == "pass" and last["current"]) or tier == "L0":
        why = f"{last['by']}通过" if last and last["verdict"] == "pass" and last["current"] else "只改文档（L0，不用审）"
        at = max((t for t in (pushed, last["at"] if last else None) if t), default=None)
        return make(state="等你", owner=OWNER, at=at, evidence=f"{why}，{ci}", next_="常浩决定合并",
                    check_at=next_slip(at) if at else None, **base)
    stale = f"；上一次审的是旧版本（{last['by']}）" if last else "，没有审查或送审记录"
    return make(state="待验证", owner=UNKNOWN, at=pushed, evidence=f"最近一次提交，{ci}{stale}",
                next_="送审或请人审", wait="unreviewed", **base)


def github_source(projects, hub, rules):
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
        rows += [pr_row(repo, n, rules) for n in nodes]
    return rows, cover


# ---------- 主线清单 ----------

def label_of(text):
    """主线项的名字：到第一个冒号、括号、逗号、句号为止。"""
    text = re.sub(r"[*`]", "", text).strip()
    m = LABEL_END.search(text)
    label = text[: m.start()].strip() if m and m.start() else text
    return label or text


def mainlines(text):
    """CURRENT 里的主线节：[{title, old, line, end, items:[{mark, text, line}], unparsed:[{text, line}]}]。行号从 1 起。"""
    out, cur = [], None
    for no, line in enumerate(text.splitlines(), 1):
        m = MAINLINE.match(line)
        if m:
            cur = {"title": m.group(2), "old": bool(m.group(1)), "line": no, "end": no, "items": [], "unparsed": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        if HEADING.match(line) or RULE.match(line):
            cur = None
            continue
        cur["end"] = no
        item = ITEM.match(line)
        if item:
            cur["items"].append({"mark": item.group(2), "text": item.group(3).strip(), "line": no})
        elif BULLET.match(line):
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
    return f"#{m.group(1)}" if m else re.sub(r"\s+", "", label_of(title.split("｜")[0]))[:16]


def mainline_rows(name, section, times, seen):
    """一节主线 →（行，只计数的，对不上的候选）。seen 记这个项目已经用过的编号：两节主线同名、两项同名时后来的加 ~2、~3。"""
    rows, parked, pending = [], [], []
    for it in section["items"]:
        mark, text = it["mark"], it["text"]
        if mark in DONE:
            continue
        label = label_of(text)
        id_ = f"mainline:{name}/{mainline_key(section['title'])}/" + re.sub(r"\s+", "", label)[:24]
        seen[id_] = seen.get(id_, 0) + 1
        if seen[id_] > 1:
            id_ += f"~{seen[id_]}"
        if PENDING.search(text):
            pending.append({"text": text, "line": it["line"], "kind": "主线项"})
        if mark in PARKED:
            parked.append({"id": id_, "project": name, "what": clip(label, 80), "mark": mark, "why": PARKED[mark], "since": None})
            continue
        at = times.get(it["line"])
        tail = clip(text[len(label):].lstrip("：:，,。 "), 120)
        asked = text.split("❓", 1)[1].strip() if "❓" in text else None   # 「▶ …，❓ 等常浩合并」：项里另写了等常浩的事
        base = dict(id_=id_, source="mainline", project=name, what=label, done_when="主线项标 ✅", line=it["line"])
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
    sentences, found = SENTENCE.split(text), set()
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
            r = subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin", main], capture_output=True, text=True)
            if r.returncode:
                cover["fetch_failed"][name] = clip(r.stderr or r.stdout or f"git 退出码 {r.returncode}", 120)
        try:
            ref = pdaily_data.main_ref(root, main)
            text = pdaily_data.show(root, ref, "CURRENT.md")
        except pdaily_data.Fail as e:
            cover["failed"][name] = str(e)
            continue
        if text is None:
            cover["none"].append(name)
            continue
        cover["ok"].append(name)
        seen = {}
        for c in heading_candidates(text):
            candidates.append({**c, "project": name, "repo": p.get("github")})
        for s in mainlines(text):
            if s["old"]:
                cover["old"].append({"project": name, "title": clip(s["title"], 60)})
                continue
            got, held, pend = mainline_rows(name, s, blame_times(root, ref, s["line"], s["end"]), seen)
            rows += got
            parked += held
            unparsed += [{"project": name, "mainline": clip(s["title"], 60), **u} for u in s["unparsed"]]
            candidates += [{**c, "project": name, "repo": p.get("github")} for c in pend]
    return rows, parked, unparsed, candidates, cover


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
    by_repo, out, failed = {}, [], {}
    for c in candidates:
        c["numbers"] = pending_numbers(c["text"])
        if c["repo"] and c["numbers"]:
            by_repo.setdefault(c["repo"], []).append(c)
    for repo, items in by_repo.items():
        numbers = sorted({n for c in items for n in c["numbers"]})[:LOOKUP_MAX]
        try:
            states = lookup(hub, repo, numbers)
        except (pdaily_data.Fail, Fail) as e:
            failed[items[0]["project"]] = str(e)
            continue
        for c in items:
            facts = [f for f in (fact_of(n, states[n], c["numbers"]) for n in c["numbers"] if n in states) if f]
            if facts:
                out.append({"id": f"mismatch:{c['project']}/CURRENT:{'+'.join(f'#{n}' for n in c['numbers'])}@{c['kind']}",
                            "project": c["project"], "where": f"CURRENT.md 第 {c['line']} 行（{c['kind']}）",
                            "text": clip(c["text"], 200), "fact": "；".join(facts), "since": None})
    return out, failed


# ---------- 账本 ----------

def out_dir():
    return Path(os.environ.get("DT_OPENLOOP_DIR", "~/Library/Application Support/DigitalTwin/openloop")).expanduser()


def read_ledger(folder=None):
    try:
        data = json.loads(((folder or out_dir()) / "ledger.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def carry(prev, source, cover):
    """这一类来源里这次没取到的项目，把上一份账本里它的行原样带过来（标 carried），不当成事情没了。"""
    lost = set(cover.get("failed") or {})
    return [{**r, "carried": True} for r in (prev or {}).get("rows", []) if r.get("source") == source and r.get("project") in lost]


def build(now, projects, hub, rules, prev=None, fetch=True):
    prs, gh_cover = github_source(projects, hub, rules)
    lines, parked, unparsed, candidates, ml_cover = mainline_source(projects, fetch)
    mismatches, unchecked = mismatches_of(candidates, hub)
    rows = prs + carry(prev, "pr", gh_cover) + lines + carry(prev, "mainline", ml_cover)
    since = {x["id"]: x.get("since") for kind in ("rows", "parked", "mismatches") for x in (prev or {}).get(kind, [])}
    for x in rows + parked + mismatches:
        x["since"] = since.get(x["id"]) or iso(now)
    for r in rows:
        check = parse_ts(r.get("check_at"))
        r["stalled"] = bool(check and now >= check)
    rows.sort(key=lambda r: (STATES.index(r["state"]), not r["stalled"], r.get("check_at") or "9", r["id"]))
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    counts.update({"停了": sum(1 for r in rows if r["stalled"]), "只计数": len(parked), "对不上": len(mismatches), "未解析": len(unparsed)})
    return {
        "version": VERSION, "generated_at": iso(now), "counts": counts, "rows": rows, "parked": parked,
        "mismatches": mismatches, "unparsed": unparsed,
        "coverage": {"projects": len(projects), "github": gh_cover, "mainline": ml_cover, "mismatch_unchecked": unchecked,
                     "rules": None if rules else "合并规则读不了，没判 L0（不用审）"},
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
    ledger = build(now, roster(), hub, rules, prev=read_ledger(folder), fetch=fetch)
    if save:
        write(ledger, folder)
    return ledger


# ---------- 打印 ----------

def summary(ledger):
    c = ledger["counts"]
    parts = [f"{s} {c[s]}" for s in STATES if c.get(s)]
    return (f"未闭环账本｜{stamp(parse_ts(ledger['generated_at']))} 扫描｜共 {len(ledger['rows'])} 行"
            + (f"：{'｜'.join(parts)}" if parts else "") + f"｜其中停了 {c['停了']}｜对不上 {c['对不上']}｜只计数 {c['只计数']}｜未解析 {c['未解析']}")


def gaps(ledger):
    """这次没取到、没核成的：账本不全要说出来，不能看着像没事。"""
    cov, out = ledger["coverage"], []
    for src, label in (("github", "GitHub"), ("mainline", "主线清单")):
        for name, why in (cov[src].get("failed") or {}).items():
            out.append(f"{label}·{name}：{why}（沿用上一份账本里的行）")
    for name in cov["github"].get("capped") or []:
        out.append(f"GitHub·{name}：开着的 PR 超过 {PR_PAGE * PR_PAGES} 个，只取了前面的")
    for name, why in (cov["mainline"].get("fetch_failed") or {}).items():
        out.append(f"拉取·{name}：{why}（CURRENT 读的是本机已有的版本，可能旧）")
    for name, why in (cov.get("mismatch_unchecked") or {}).items():
        out.append(f"对不上·{name}：{why}（这个项目的「待合并」没核）")
    if cov.get("rules"):
        out.append(cov["rules"])
    return out


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
            out.append(f"  {flag}{r['id']}｜{r['what']}" + ("（沿用上一份）" if r.get("carried") else ""))
            out.append(f"      谁：{r['owner']}｜证据：{r['evidence']}｜下一步：{r['next']}"
                       + (f"｜卡在：{r['blocked']}" if r["blocked"] else "")
                       + (f"｜下次看：{stamp(parse_ts(r['check_at']))}" if r["check_at"] else "｜下次看：不催")
                       + f"｜进账本：{stamp(parse_ts(r['since']))}")
    if ledger["mismatches"]:
        out.append(f"\n【记录和事实对不上】{len(ledger['mismatches'])}")
        for m in ledger["mismatches"]:
            out.append(f"  {m['project']} {m['where']}：「{m['text']}」→ {m['fact']}")
    if ledger["unparsed"]:
        out.append(f"\n【主线清单里没解析出来的】{len(ledger['unparsed'])}")
        for u in ledger["unparsed"]:
            out.append(f"  {u['project']} CURRENT.md 第 {u['line']} 行（主线：{u['mainline']}）：{u['text']}")
    if ledger["parked"]:
        marks = "｜".join(f"{m} {sum(1 for x in ledger['parked'] if x['mark'] == m)}" for m in PARKED)
        out.append(f"\n【只计数，不催】{marks}")
        for x in ledger["parked"]:
            out.append(f"  {x['mark']} {x['project']}｜{x['what']}")
    old = ledger["coverage"]["mainline"].get("old") or []
    if old:
        out.append("\n【没收的旧主线】" + "；".join(f"{o['project']}「{o['title']}」" for o in old))
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
    a = ap.parse_args(argv)
    try:
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
        print(f"账本没更新：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
