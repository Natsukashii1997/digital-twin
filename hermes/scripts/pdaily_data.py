"""个人项目日报取数（#110 批 1a）：把一个统计日里名册项目的事实整理成结构化记录，给 Hermes 写日报用（只读，只用标准库）。

规格：docs/个人项目日报-V2-设计.md 第 3、5、6、7 节。
用法：python3 pdaily_data.py [--date 统计日] [--project id或别名]
  统计日 D = D 04:00 到 D+1 04:00（Asia/Shanghai，写死，不看机器时区）；不给 --date 时取最近一个已经结束的统计日。
输出：JSON 到 stdout。每个项目一条记录：阶段目标、里程碑、昨日增量、当前状态、新鲜度、证据、取数错误。
  取不到的来源记为 null 并写进 errors 和 coverage，不当成 0；没有新动作的项目照样给出当前状态。
  合并的 PR：工程「已合并」；交付、验收一律「未核实」，只附自述摘录（claims：当天 CURRENT 节、PR 标题），不下结论。
  阶段目标、CURRENT、新鲜度都读主分支在统计日结束（D+1 04:00）时的快照，不读工作区。
名册读不了：输出「取数失败：原因」，退出码 1。
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import progress_data  # noqa: E402  复用名册路径、CURRENT 的“下一步”抽取

TZ = ZoneInfo("Asia/Shanghai")
DAY_START = dt.time(4, 0)          # 统计日从 04:00 开始：常浩常干到凌晨
STALE = dt.timedelta(days=1)       # CURRENT 比最近一次合并早这么多就算过期
GOAL = re.compile(r"^当前阶段目标：(.+)$")
GOAL_HEAD = 12                     # 目标行只在文件头这么多行里找
# 交付（安装 / 发布）、验收不从 CURRENT 的文字下结论（常浩 2026-11-09 定「甲：只摘原文」）：
# CURRENT 是 Claude 写的自述，V2 第 6 节规定文档写了「已安装」不算安装成功、Claude 说做完不算常浩通过；
# 前几轮审查也证明正则判断中文语义总有漏网写法。所以 1a 一律记「未核实」，只把当天那一节里提到安装、发布、
# 验收的句子原样摘出来（claims），标明是自述摘录；真正的判定等 1b 接入安装记录和回读证据。
UNVERIFIED = "未核实"
CLAIMS_NOTE = "CURRENT / PR 标题的自述摘录，未判定；安装、验收以行动记录和回读证据为准（#110 1b）"
NO_CLAIMS_NOTE = "没有摘录：当天 CURRENT 没有对得上的节，或对上的节里没提安装、发布、验收；不代表没安装"
CLAIM = re.compile(r"安装|装机|已装|发布|上线|验收|回读|cmp|回滚|卸载|生效|重启", re.I)
CLAIM_MAX = 12                     # 每节最多摘这么多句，多出来的记 omitted
QUOTE_MAX = 400                    # 每句最多这么多字，超出的标「（后文截断）」：后半句可能有否定、回滚
CUT_MARK = "…（后文截断）"
SPLIT = re.compile(r"[（）()，,；;、。：:]")
RANGE = re.compile(r"^#(\d+)\s*[–—\-~～]\s*#?(\d+)$")   # 「（#257–#265）」：一批票写成范围
RANGE_MAX = 30                     # 范围最多展开这么多个号，写错成「#1–#999」不至于挂满
REF_CLAUSE = re.compile(r"^(?:PR\s*)?#\d+(?:\s*PR\s*#\d+)?(?:\s*(?:squash\s*)?(?:合并为\s*)?`[0-9a-f]{7,40}`)?$")
PR_IN_TITLE = re.compile(r"PR\s*#(\d+)")
HEAD_UPDATED = re.compile(r"更新[：:]\s*(\d{4}-\d{2}-\d{2})")   # 文件头「更新：2026-11-08。……唯一下一步：……」
TITLE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})|（(\d{4}-\d{2}-\d{2})")   # 节的日期：标题开头，或第一个括号开头
GH_LIMIT = {"merged_prs": 200, "closed_issues": 200, "open_prs": 100, "milestones": 100}
REF = re.compile(r"#(\d+)")
CLOSES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)", re.I)
SECTION_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
CHECK_REF = re.compile(r"^\s*- \[([ xX])\]\s*#(\d+)", re.M)   # 以 issue 号开头的清单项：「- [ ] #109 批 0」
MIN_CHILDREN = 2                   # 至少两条这样的清单项才算父 issue；一条顺带提到 issue 号的多半是验收条件
MILESTONE_QUERY = """query($o:String!,$n:String!){repository(owner:$o,name:$n){issues(states:OPEN,first:100){nodes{
number title body subIssues(first:100){nodes{number state}}}}}}"""


class Fail(Exception):
    pass


def short(text, n=40):
    return text if len(text) <= n else text[:n] + "…"


def quote(line):
    line = line.strip()
    return line if len(line) <= QUOTE_MAX else line[:QUOTE_MAX] + CUT_MARK


def claims_of(title, body):
    """一节 CURRENT 里提到安装、发布、验收的句子，原样摘出（不判断真假）：节标题算一句，正文每行算一句。
    返回 (摘录, 超出条数上限没列出的句数)。"""
    lines = [title, *(x for x in body.splitlines() if x.strip())]
    hits = [quote(x) for x in lines if CLAIM.search(x)]
    return hits[:CLAIM_MAX], max(0, len(hits) - CLAIM_MAX)


def title_refs(title):
    """节标题里专门写交付编号的段，分成 (issue 号, PR 号)：「（#247）」「（#67，PR #68 `fa81bc8`）」「#43 导航…」开头的号。
    「常浩实点 #196 后说……」「修 PR #196 引入的回归」这种从句里顺带提到的号都不算。"""
    issues = {int(m.group(1))} if (m := re.match(r"#(\d+)\s", title)) else set()
    prs = set()
    for c in SPLIT.split(title):
        c = c.strip()
        r = RANGE.match(c)
        if r and 0 <= int(r.group(2)) - int(r.group(1)) < RANGE_MAX:
            issues |= set(range(int(r.group(1)), int(r.group(2)) + 1))
        elif REF_CLAUSE.match(c):
            named = {int(n) for n in PR_IN_TITLE.findall(c)}
            prs |= named
            issues |= {int(n) for n in REF.findall(c)} - named
    return issues, prs


# ---------- 时间 ----------

def window(day):
    """统计日 day 的 [开始, 结束)，带时区。"""
    start = dt.datetime.combine(day, DAY_START, TZ)
    return start, start + dt.timedelta(days=1)


def default_day(now=None):
    """最近一个已经结束的统计日：08:00 发报时是前一天；04:00 之前跑，前一天还没结束，取再前一天。"""
    now = (now or dt.datetime.now(TZ)).astimezone(TZ)
    return (now - dt.timedelta(hours=DAY_START.hour)).date() - dt.timedelta(days=1)


def parse_ts(s):
    """GitHub 的 2026-11-08T05:34:12Z、git 的 %cI，都转成带时区的时间。"""
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def inside(ts, w):
    t = parse_ts(ts) if isinstance(ts, str) else ts
    return t is not None and w[0] <= t < w[1]


# ---------- 本机仓库 ----------

def git_env():
    """git 报错固定英文：show() 按报错文字区分「没有这个文件」。每次现取，跟着当时的环境变量走。"""
    return {**os.environ, "LC_ALL": "C"}


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=git_env())
    if r.returncode:
        raise Fail((r.stderr or r.stdout).strip()[:200] or f"git 退出码 {r.returncode}")
    return r.stdout


def commits(root, w, main):
    """统计日内的提交数，按分支归；一个提交只算在第一个含它的分支下，主分支优先。按提交时间算，不按作者时间。
    列分支时就记下各分支指向的提交，后面按提交号查：别的会话中途删掉分支也不出错。"""
    heads = [line.split(" ", 1)[::-1] for line in
             git(root, "for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads").splitlines() if line]
    heads.sort(key=lambda bh: bh[0] != main)
    seen, out = set(), {}
    for b, sha in heads:
        rows = git(root, "log", sha, f"--since={w[0].isoformat()}", f"--until={w[1].isoformat()}",
                   "--format=%H %cI", "--").split("\n")
        mine = [h for h, _, t in (r.partition(" ") for r in rows if r) if inside(t, w) and h not in seen]
        seen.update(mine)
        if mine:
            out[b] = len(mine)
    return out


def main_ref(root, main):
    """读 CURRENT、README 用哪个分支：远端主分支和本机主分支里较新的那个。
    不读工作区：主目录可能检出别的分支（管培生工作台）、可能还没拉齐（数码世界），读到的不是项目的正式状态。"""
    refs = []
    for ref in (f"origin/{main}", main):
        r = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%ct", ref], capture_output=True, text=True, env=git_env())
        if r.returncode == 0 and r.stdout.strip():
            refs.append((int(r.stdout.strip()), ref))
    return max(refs)[1] if refs else None


def as_of(root, ref, w):
    """主分支在统计日结束时的那个提交：阶段目标、CURRENT、新鲜度都按它读，回放历史统计日和当天跑结果一致，
    04:00 以后的提交不混进前一天。统计日结束前主分支还没有提交，返回 None。"""
    end = (w[1] - dt.timedelta(seconds=1)).isoformat()
    return git(root, "rev-list", "-1", "--first-parent", f"--before={end}", ref).strip() or None


def show(root, ref, name):
    """某分支上的文件内容；没有这个文件返回 None，git 别的失败抛 Fail（不当成「没有文件」）。"""
    if not ref:
        return None
    r = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{name}"], capture_output=True, text=True, env=git_env())
    if r.returncode == 0:
        return r.stdout
    if "does not exist" in r.stderr or "exists on disk, but not in" in r.stderr:
        return None
    raise Fail((r.stderr or r.stdout).strip()[:200] or f"git show 退出码 {r.returncode}")


def goal(root, ref):
    """CURRENT.md（没有就 README.md）文件头里的「当前阶段目标：」一行（#114）。"""
    for name in ("CURRENT.md", "README.md"):
        text = show(root, ref, name)
        if text is None:
            continue
        for line in text.splitlines()[:GOAL_HEAD]:
            m = GOAL.match(line.strip())
            if m:
                text = re.sub(r"（\d{4}-\d{2}-\d{2} 常浩确认[^）]*）$", "", m.group(1)).strip()
                kind = "常态维护" if text.startswith("常态维护") else "暂停" if text.startswith("暂停") else "目标"
                return {"kind": kind, "text": text, "source": name}
    return {"kind": "未设", "text": None, "source": None}


def sections(text):
    """CURRENT 按二级标题切节：[(标题, 标题里的日期或 None, 正文)]，保持原顺序（最新在上）。
    日期写错（如 2026-11-31）当没有日期，不让整份日报崩掉。"""
    parts = re.split(r"^(?=## )", text, flags=re.M)
    out = []
    for p in parts:
        if not p.startswith("## "):
            continue
        title, _, body = p.partition("\n")
        date = None   # 标题开头或第一个括号里的日期；「复盘 2026-11-01 事故（2026-11-08）」取 11-08
        m = TITLE_DATE.search(title[3:].strip())
        for d in ([m.group(1) or m.group(2)] if m else SECTION_DATE.findall(title)):
            try:
                date = dt.date.fromisoformat(d).isoformat()
                break
            except ValueError:
                continue
        out.append((title[3:].strip(), date, body))
    return out


def current_state(root, ref, w):
    """CURRENT 最上面一节、下一步（及其出处）、最后一次提交 CURRENT 的时间、统计日内改过没有，以及当天日期的各节（摘录用）。
    ref 是主分支在统计日结束时的提交。没有 CURRENT 返回 None；git 读不了抛 Fail。"""
    text = show(root, ref, "CURRENT.md")
    if text is None:
        return None
    secs = sections(text)
    # --first-parent：普通合并（no-ff）时取合并提交的时间，不沿着分支走到分支上的旧提交
    stamp = git(root, "log", "-1", "--first-parent", "--format=%cI", ref, "--", "CURRENT.md").strip()
    nxt = progress_data.next_step(text.splitlines())
    top_next = progress_data.next_step(f"## {secs[0][0]}\n{secs[0][2]}".splitlines()) if secs else None
    head = re.split(r"^## ", text, maxsplit=1, flags=re.M)[0]
    head_next = progress_data.next_step(head.splitlines())
    m = HEAD_UPDATED.search(head)
    head_updated = None
    if m:
        try:
            head_updated = dt.date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            pass
    top_date = secs[0][1] if secs else None
    source = None if not nxt else "最上面一节" if nxt == top_next else "文件头" if nxt == head_next else "「唯一下一步」节"
    return {
        "top_section": secs[0][0] if secs else None,
        "top_date": top_date,
        "next_step": nxt,
        # 最上面一节没写下一步时，next_step 会退到文件头或旧的「唯一下一步」节，那里可能是几天前的。
        # 文件头写了「更新：日期」且不早于最上面一节，才当它是现行的（多数项目把正式的下一步写在文件头）
        "next_source": source,
        "next_current": source == "最上面一节" or (source == "文件头" and bool(head_updated)
                                                  and (top_date is None or head_updated >= top_date)),
        "last_commit": stamp or None,
        # ref 是统计日结束时的快照：截至那时 CURRENT 最后一次提交落在窗口里，就是当天改过（04:00 以后的提交看不到）
        "changed": inside(stamp, w),
        # 快照截在 D+1 04:00，标着 D+1 的节一定是凌晨写的，也算统计日 D 的
        "dated_today": [(t, b) for t, d, b in secs if d in (w[0].date().isoformat(), w[1].date().isoformat())],
    }


def last_change(root, ref):
    """主分支上最近一次不只改 CURRENT 的提交时间，当作「最近一次合并」：不依赖 gh，也不限于统计日。
    直接推到主分支的文档提交也算在内，宁可多报过期。从来没有这样的提交返回 None。"""
    return git(root, "log", "-1", "--first-parent", "--format=%cI", ref, "--", ".", ":(exclude)CURRENT.md").strip() or None


# ---------- GitHub ----------

class GitHub:
    """gh 取数。连不上一次后，后面的仓库不再等超时，直接标同一个原因。"""

    def __init__(self, w):
        self.w = w
        self.capped = set()   # (仓库, 哪一项)：返回条数到了上限，可能截断
        self.exe = shutil.which("gh", path=os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"]))
        self.down = None if self.exe else "找不到 gh 命令"

    def _run(self, *args):
        if self.down:
            raise Fail(self.down)
        try:
            r = subprocess.run([self.exe, *args], capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            self.down = "gh 60 秒没有回应"
            raise Fail(self.down)
        except OSError as e:
            self.down = f"gh 启动失败：{e}"
            raise Fail(self.down)
        if r.returncode:
            msg = (r.stderr or r.stdout).strip()
            msg = msg.splitlines()[-1][:150] if msg else f"gh 退出码 {r.returncode}"
            if "connect" in msg or "timeout" in msg.lower():
                self.down = msg
            raise Fail(msg)
        try:
            return json.loads(r.stdout or "null")
        except ValueError:
            raise Fail(f"gh 输出不是 JSON：{r.stdout.strip()[:80]}")

    def _cap(self, repo, key, rows):
        if len(rows or []) >= GH_LIMIT[key]:
            self.capped.add((repo, key))
        return rows or []

    def _range(self, kind):
        return f"{kind}:{self.w[0].isoformat()}..{self.w[1].isoformat()}"

    def merged_prs(self, repo):
        rows = self._run("pr", "list", "-R", repo, "--state", "merged", "--search", self._range("merged"),
                         "--limit", str(GH_LIMIT["merged_prs"]), "--json", "number,title,url,body,mergedAt")
        return [r for r in self._cap(repo, "merged_prs", rows) if inside(r.get("mergedAt"), self.w)]

    def closed_issues(self, repo):
        rows = self._run("issue", "list", "-R", repo, "--state", "closed", "--search", self._range("closed"),
                         "--limit", str(GH_LIMIT["closed_issues"]), "--json", "number,title,closedAt")
        return [r for r in self._cap(repo, "closed_issues", rows) if inside(r.get("closedAt"), self.w)]

    def open_prs(self, repo):
        rows = self._run("pr", "list", "-R", repo, "--state", "open", "--limit", str(GH_LIMIT["open_prs"]),
                         "--json", "number,title,createdAt")
        return [{"number": r["number"], "title": r["title"], "created": r.get("createdAt")}
                for r in self._cap(repo, "open_prs", rows)]

    def milestones(self, repo):
        owner, name = repo.split("/", 1)
        data = self._run("api", "graphql", "-f", f"query={MILESTONE_QUERY}", "-f", f"o={owner}", "-f", f"n={name}")
        nodes = ((((data or {}).get("data") or {}).get("repository") or {}).get("issues") or {}).get("nodes") or []
        nodes = self._cap(repo, "milestones", nodes)
        refs = sorted({int(n) for x in nodes if not ((x.get("subIssues") or {}).get("nodes"))
                       for _, n in CHECK_REF.findall(x.get("body") or "")})[:100]
        states = self.states(owner, name, refs) if refs else {}
        return [m for m in (milestone(n, states) for n in nodes) if m]

    def states(self, owner, name, numbers):
        """清单里引用的 issue / PR 现在是不是关了：{号: True/False}；取不到返回 None（只按勾选算，并注明）。"""
        fields = " ".join(f"i{n}: issueOrPullRequest(number:{n}){{... on Issue{{state}} ... on PullRequest{{state}}}}"
                          for n in numbers)
        try:
            data = self._run("api", "graphql", "-f", f"query=query($o:String!,$n:String!){{repository(owner:$o,name:$n){{{fields}}}}}",
                             "-f", f"o={owner}", "-f", f"n={name}")
        except Fail:
            return None
        repo = ((data or {}).get("data") or {}).get("repository") or {}
        return {n: (repo.get(f"i{n}") or {}).get("state") in ("CLOSED", "MERGED") for n in numbers if repo.get(f"i{n}")}


def milestone(issue, states=None):
    """开着的父 issue 才算里程碑：先认原生子任务，再认正文清单里引用了别的 issue 号的；只有验收清单的不算（#110 评论）。
    清单项按「勾了或引用的 issue 已关」算完成（V2 5.3）；勾选和关闭对不上的写进 notes（V2 第 6 节：来源冲突要标出）。"""
    subs = ((issue.get("subIssues") or {}).get("nodes")) or []
    if subs:
        done = sum(1 for s in subs if s.get("state") == "CLOSED")
        return {"number": issue["number"], "title": issue["title"], "done": done, "total": len(subs), "source": "原生子任务"}
    items = CHECK_REF.findall(issue.get("body") or "")
    if len(items) >= MIN_CHILDREN:
        done, notes = 0, []
        for mark, n in items:
            ticked, closed = mark.lower() == "x", (states or {}).get(int(n))
            done += ticked or bool(closed)
            if states is not None and closed is None:
                notes.append(f"#{n} 的状态没取到")
            elif ticked and closed is False:
                notes.append(f"清单勾了但 #{n} 还开着")
            elif not ticked and closed:
                notes.append(f"清单没勾但 #{n} 已关")
        if states is None:
            notes.append("清单引用的 issue 状态没取到，只按勾选算")
        return {"number": issue["number"], "title": issue["title"], "done": done, "total": len(items), "source": "正文清单",
                "notes": notes}
    return None


# ---------- 汇总一个项目 ----------

def refs_of(pr):
    """一个 PR 关联的编号，按固定顺序：自己的号在前，再按数字排标题里的号和正文里 Closes / Fixes 的号。
    正文里顺带提到的别的号不算：否则会把别的节的摘录挂到这个 PR 上。"""
    linked = {int(n) for n in REF.findall(pr.get("title", ""))} | {int(n) for n in CLOSES.findall(pr.get("body") or "")}
    return [pr["number"], *sorted(linked - {pr["number"]})]


def section_prs(title, prs):
    """一节当天 CURRENT 挂给哪些 PR。节标题里点名的 PR 号，只有它确实关联这一节的 issue 号（或这一节没写 issue 号）
    才算数：trainee-hub 的节标题会顺带写上一张票的 PR 号，直接信点名会整排错挂。没有有效点名，就按 issue 号挂给
    关联它的 PR（PR 自己的号、PR 标题里的号、正文 Closes / Fixes 的号）。"""
    issues, named = title_refs(title)
    by_num = {pr["number"]: pr for pr in prs}
    valid = [by_num[n] for n in sorted(named) if n in by_num and (not issues or issues & set(refs_of(by_num[n])[1:]))]
    if valid:
        return valid
    return [pr for pr in prs if issues & set(refs_of(pr))]


def deliverables(prs, dated_sections):
    """每个合并的 PR 一条，三个维度分开记（V2 5.2）：工程「已合并」有 GitHub 为证；交付、验收一律「未核实」，
    只附当天 CURRENT 节和 PR 标题里的自述摘录（claims），不下结论（见文件头）。挂法见 section_prs；
    一节同时挂给几个 PR 时照挂，在 claims_warnings 里注明分不清属于哪一个。没有摘录也写 claims_note，说明不代表没安装。
    返回 (每个 PR 一条, 当天没挂上任何 PR 的节)：后者照样列标题和摘录，读者才知道「有自述，只是分不清属于哪个 PR」。"""
    attached = {pr["number"]: [] for pr in prs}
    unmatched = []
    for title, body in dated_sections:
        owners = section_prs(title, prs)
        if not owners:
            quotes, omitted = claims_of(title, body)
            unmatched.append({"section": short(title), "quotes": quotes, "omitted": omitted})
        for pr in owners:
            attached[pr["number"]].append((title, body, [o["number"] for o in owners]))
    out = []
    for pr in prs:
        claims, warnings = [], []
        for title, body, owners in attached[pr["number"]]:
            quotes, omitted = claims_of(title, body)
            if quotes:
                claims.append({"source": f"CURRENT「{short(title)}」", "quotes": quotes, "omitted": omitted})
            if len(owners) > 1:
                others = "、".join(f"#{n}" for n in owners if n != pr["number"])
                warnings.append(f"「{short(title, 30)}」这一节同时挂给 {others}，分不清属于哪一个")
        if CLAIM.search(pr.get("title", "")):
            claims.append({"source": f"PR #{pr['number']} 标题", "quotes": [quote(pr["title"])], "omitted": 0})
        out.append({
            "number": pr["number"], "title": pr["title"], "url": pr.get("url"), "merged_at": pr.get("mergedAt"),
            "engineering": "已合并",
            "delivery": UNVERIFIED,
            "acceptance": UNVERIFIED,
            "matched_sections": [short(t) for t, _, _ in attached[pr["number"]]],
            "claims": claims,
            "claims_note": CLAIMS_NOTE if claims else NO_CLAIMS_NOTE,
            "claims_warnings": warnings,
            "evidence": [f"PR #{pr['number']} merged"],
        })
    return out, unmatched


def project(p, w, hub):
    """一个名册项目的记录。任何一个来源取不到：那一项记 null，原因写进 errors。"""
    root = Path(os.path.expanduser(p["path"]))
    day = w[0].date()
    rec = {"project_id": p["id"], "name": p["name"], "type": p.get("type"), "objective": None, "milestones": None,
           "yesterday_delta": {"merged_prs": None, "closed_issues": None, "commits": None, "current_changed": None,
                               "unmatched_sections": []},
           "current_state": {"top_section": None, "next_step": None, "next_source": None, "next_reliable": None, "open_prs": None,
                             "checkout_branch": None, "dirty_files": None},
           "freshness": {"current_last_commit": None, "top_section_date": None, "current_as_of": None,
                         "last_merge": None, "stale": None},
           "notes": [], "errors": []}
    delta, state, fresh = rec["yesterday_delta"], rec["current_state"], rec["freshness"]
    if not root.exists():
        rec["objective"] = {"kind": "未知", "text": None, "source": None}
        rec["errors"].append(f"本机没有仓库目录 {root}")
        return rec
    main = p.get("main", "main")
    try:
        git(root, "rev-parse", "--git-dir")
        is_repo = True
    except (Fail, OSError) as e:
        is_repo = False
        rec["errors"].append(f"本机 git：读不了仓库（不是 git 仓库或没有 git）：{e}")
    ref = main_ref(root, main) if is_repo else None   # 主分支名，写进说明用
    snap = None                                         # 主分支在统计日结束时的提交：目标、CURRENT、新鲜度都按它读
    if is_repo and not ref:   # 不是「未设目标」「没有 CURRENT」：是根本读不到
        rec["errors"].append(f"本机 git：找不到主分支 origin/{main} 和 {main}，阶段目标、CURRENT、新鲜度都读不了")
    elif ref:
        try:
            snap = as_of(root, ref, w)
            if not snap:
                rec["errors"].append(f"本机 git：{ref} 在统计日结束前还没有提交")
        except Fail as e:
            rec["errors"].append(f"本机 git：读 {ref} 在统计日结束时的提交失败：{e}")
    rec["objective"] = {"kind": "未知", "text": None, "source": None}
    if snap:
        try:
            rec["objective"] = goal(root, snap)
        except Fail as e:
            rec["errors"].append(f"本机 git：读阶段目标失败：{e}")
    if is_repo:
        try:
            delta["commits"] = commits(root, w, main)
            state["checkout_branch"] = git(root, "branch", "--show-current").strip() or "（游离 HEAD）"
            state["dirty_files"] = len(git(root, "status", "--porcelain").splitlines())
        except (Fail, OSError) as e:
            rec["errors"].append(f"本机 git：{e}")
    cur, dated, merge_ok = None, [], False
    if snap:
        try:
            cur = current_state(root, snap, w)
            fresh["last_merge"] = last_change(root, snap)
            merge_ok = True
        except Fail as e:
            rec["errors"].append(f"本机 git：读 {ref} 失败：{e}")
            cur = cur or False   # False = 读失败，None = 主分支上确实没有 CURRENT
    if cur:
        state["top_section"], state["next_step"], state["next_source"] = cur["top_section"], cur["next_step"], cur["next_source"]
        fresh["current_last_commit"], fresh["top_section_date"] = cur["last_commit"], cur["top_date"]
        delta["current_changed"] = cur["changed"]
        dated = cur["dated_today"]
        if cur["changed"] and cur["top_date"] and cur["top_date"] < day.isoformat():
            rec["notes"].append(f"CURRENT 当天有提交，但最上面一节是 {cur['top_date']} 的：可能只改了文件头或旧节")
        if not cur["next_step"]:
            rec["notes"].append("CURRENT 最上面一节、文件头和「唯一下一步」节都没写下一步")
    elif cur is None and snap:
        rec["notes"].append(f"{ref} 上没有 CURRENT.md：没有可信的下一步")
        state["next_reliable"] = False
    repo = p.get("github")
    if repo and "/" not in repo:   # GitHub 那几项留 null：名册写错不等于没有 GitHub
        rec["errors"].append(f"名册 github 字段不是「所有者/仓库」：{repo}")
    elif repo:
        for key, fn in (("merged_prs", hub.merged_prs), ("closed_issues", hub.closed_issues)):
            try:
                delta[key] = fn(repo)
            except Fail as e:
                rec["errors"].append(f"GitHub {key}：{e}")
        try:
            state["open_prs"] = hub.open_prs(repo)
        except Fail as e:
            rec["errors"].append(f"GitHub open_prs：{e}")
        try:
            rec["milestones"] = hub.milestones(repo)
        except Fail as e:
            rec["errors"].append(f"GitHub milestones：{e}")
        for key in sorted(k for r, k in hub.capped if r == repo):
            rec["notes"].append(f"GitHub {key} 到了 {GH_LIMIT[key]} 条上限，可能截断")
    else:
        delta["merged_prs"], delta["closed_issues"], state["open_prs"], rec["milestones"] = [], [], [], []
        rec["notes"].append("名册里没有 GitHub 仓库：PR、issue、里程碑按空记")
    # 合并 PR 取不到时，当天的节全算没挂上：自述照样列出来，PR 那一项仍是 null
    merged, delta["unmatched_sections"] = deliverables(delta["merged_prs"] or [], dated)
    if delta["merged_prs"] is not None:
        delta["merged_prs"] = merged
    if delta["closed_issues"] is not None:
        delta["closed_issues"] = [{"number": i["number"], "title": i["title"]} for i in delta["closed_issues"]]
    # 新鲜度：CURRENT 最近一节比主分支上最近一次别的改动早一天以上，就不把它的“下一步”当可信依据（V2 第 6 节）。
    # 最近一节的时间取「节标题日期那个统计日结束」和「CURRENT 最后一次提交」里早的那个：只改文件头的提交洗不掉旧节。
    # 只看本机主分支，不依赖 gh；读不到就是 None（未知），不当成没过期
    if cur and merge_ok and not cur["top_date"]:
        rec["notes"].append("CURRENT 最上面一节标题没有日期：新鲜度只能按 CURRENT 提交时间算，只改文件头也会显得新")
    if cur and merge_ok:
        stamps = [t for t in (parse_ts(cur["last_commit"]), cur["top_date"] and window(dt.date.fromisoformat(cur["top_date"]))[1]) if t]
        cur_ts, last_merge = (min(stamps) if stamps else None), parse_ts(fresh["last_merge"])
        fresh["current_as_of"] = cur_ts.isoformat() if cur_ts else None
        fresh["stale"] = bool(last_merge) and (cur_ts is None or last_merge - cur_ts > STALE)
        state["next_reliable"] = bool(state["next_step"]) and not fresh["stale"] and cur["next_current"]
    return rec


def collect(day, projects):
    w = window(day)
    hub = GitHub(w)
    recs = []
    for p in projects:
        try:
            recs.append(project(p, w, hub))
        except KeyError as e:   # 名册某一项缺 id、name、path：记下来，不让整份日报中断
            pid = p.get("id") or p.get("name") or "（名册项缺 id）"
            recs.append({"project_id": pid, "name": p.get("name"), "notes": [], "errors": [f"名册这一项缺字段 {e}"]})
    return {
        "stat_day": day.isoformat(), "timezone": "Asia/Shanghai",
        "window": {"start": w[0].isoformat(), "end": w[1].isoformat()},
        "fetched_at": dt.datetime.now(TZ).isoformat(timespec="seconds"),
        "projects": recs,
        "coverage": {
            "projects": len(recs),
            "read_ok": sum(1 for r in recs if not r["errors"]),
            "errors": {r["project_id"]: r["errors"] for r in recs if r["errors"]},
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="统计日 YYYY-MM-DD（D 04:00 到 D+1 04:00），默认最近一个已经结束的统计日")
    ap.add_argument("--project", help="只看一个项目（id 或别名）")
    a = ap.parse_args()
    try:
        day = dt.date.fromisoformat(a.date) if a.date else default_day()
    except ValueError:
        print(f"取数失败：--date 要写成 YYYY-MM-DD，收到「{a.date}」")
        sys.exit(1)
    try:
        projects = json.loads(progress_data.ROSTER.read_text())["projects"]
    except (OSError, ValueError, KeyError) as e:
        print(f"取数失败：读不了项目名册 {progress_data.ROSTER}（{e}）")
        sys.exit(1)
    if a.project:
        projects = progress_data.pick(projects, a.project)
        if not projects:
            print(f"取数失败：名册里没有项目「{a.project}」")
            sys.exit(1)
    json.dump(collect(day, projects), sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()
