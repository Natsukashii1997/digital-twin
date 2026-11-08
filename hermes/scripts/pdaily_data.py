"""个人项目日报取数（#110 批 1a）：把一个统计日里名册项目的事实整理成结构化记录，给 Hermes 写日报用（只读，只用标准库）。

规格：docs/个人项目日报-V2-设计.md 第 3、5、6 节。
用法：python3 pdaily_data.py [--date 统计日] [--project id或别名]
  统计日 D = D 04:00 到 D+1 04:00（Asia/Shanghai，写死，不看机器时区）；不给 --date 时取最近一个已经结束的统计日。
输出：JSON 到 stdout。每个项目一条记录：阶段目标、里程碑、昨日增量、当前状态、新鲜度、证据、取数错误。
  取不到的来源记为 null 并写进 errors 和 coverage，不当成 0；没有新动作的项目照样给出当前状态。
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
# 安装、验收只认固定格式的完成记录（白名单，V2 第 6 节：文档写了不等于做了）。格式之外的写法一律「未知」，
# 宁可漏报，不误报。格式取自名册各项目 CURRENT 的真实写法（2026-11-09 核过）：
#   节标题里单独一段：「，已安装，」「已安装到本机、」「已发布 `b18150e21b61` 回读通过」「已发布 nm-…」「已发布 653」
#   条目行开头（冒号必须有，冒号后不能是「否、无、未……」）：「- **已安装**：」「- 已安装：」「- **已安装（11-08 22:56，常浩说「…」）**：」「- **已合并、已安装（…）**」「- **已安装、已重启（…）**」
#   发布与回读行：「- 发布与回读：… 产物 `70ad4a4648bb` / 已装 `70ad4a4648bb` …」（两个哈希一致才算）
#   验收：一段话以「常浩 / 用户 / 本人 / 所有者」开头、以「验收通过」结尾，例如「常浩实机验收通过」「交接补记：常浩实机验收乙通过」
# 同一节里任何一行写了撤回（已回滚、已卸载、没生效、cmp / 回读不一致……），这一节的安装、验收都不算。
SPLIT = re.compile(r"[（）()，,；;、。：:]")
INSTALL_CLAUSE = re.compile(r"^(?:已安装|已上线)(?:到本机)?$|"
                            r"^已发布\s*`?(?:[0-9a-f]{7,40}|nm-[\w-]+|wb-[\w-]+|\d+)`?(?P<rb>\s*回读[^，]{0,6}通过)?$")
INSTALL_LINE = re.compile(r"^\s*[-*]\s*(?:\*\*)?(?:已合并、)?已安装(?:、[^*（：:]{1,8})?(?:（(?P<note>[^）]*)）)?(?:\*\*)?\s*[：:]"
                          r"(?!\s*(?:否|❌|✗|✘|无|未|没|不|待))")
READBACK_LINE = re.compile(r"^\s*[-*]\s*(?:\*\*)?发布与回读(?:\*\*)?\s*[：:].*?产物\s*`?([0-9a-f]{7,40})`?\s*/\s*已装\s*`?([0-9a-f]{7,40})`?")
ACCEPT_CLAUSE = re.compile(r"^(?:常浩|用户|本人|所有者)(?:已经?)?(?:实机|真机|真屏)?验收(?P<mid>[^，]{0,8}?)通过了?$")
SHAKY = re.compile(r"未|没|不|无|非|别|待|失败|部分|一半|只|/|？|\?|吗|应|可能|预计|估计|算|Claude|Codex|自测|自验", re.I)
# 撤回只认明确写法；「与授权不一致之处」是 CURRENT 模板小标题，「回滚：命令」是回滚办法，都不算撤回
REVOKED = re.compile(r"已回滚|回滚了|已撤回|已卸载|卸载了|没生效|未生效|装回旧版|"
                     r"(?:cmp|回读|哈希|校验值?|产物|已装)[^，。；\n]{0,8}不一致", re.I)
VERIFIED = re.compile(r"回读|cmp|校验值|sha256|sha1|\bsha\b", re.I)   # 和安装记录写在同一条才算有回读，否则只是自述
REF_CLAUSE = re.compile(r"^(?:PR\s*)?#\d+(?:\s*PR\s*#\d+)?(?:\s*(?:squash\s*)?(?:合并为\s*)?`[0-9a-f]{7,40}`)?$")
HANDOVER = "交接记录（非本人回执）"                        # CURRENT、PR 标题都是 Claude 写的，不是常浩本人的回执
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


def clean(line):
    """去掉删除线和加粗记号；未勾选、已勾选的清单行，带问号的行，都不当完成记录（返回 None）。"""
    line = re.sub(r"~~.*?~~", "", line)
    if re.match(r"^\s*[-*]\s*\[[ xX]\]", line) or re.search(r"[?？]|吗", line):
        return None
    return line


def clauses(line):
    return [c.strip().replace("**", "") for c in SPLIT.split(re.sub(r"^\s*[-*]\s*", "", line))]


def accepted_in(line):
    """一行里有没有「常浩……验收通过」这样单独成段的记录。"""
    line = clean(line)
    return line is not None and any((m := ACCEPT_CLAUSE.match(c)) and not SHAKY.search(m.group("mid")) and not SHAKY.search(c)
                                    for c in clauses(line))


def section_status(title, body):
    """一节 CURRENT 的安装、验收记录：(安装证据等级或 None, 有没有验收)。只认白名单格式，有撤回整节不算。"""
    lines = [title, *body.splitlines()]
    if any(REVOKED.search(re.sub(r"~~.*?~~", "", x)) for x in lines):
        return None, False
    installs = []   # 每条安装记录有没有回读
    t = clean(title)
    for c in clauses(t) if t is not None else []:
        m = INSTALL_CLAUSE.match(c)
        if m:
            installs.append(bool(m.groupdict().get("rb")))
    body_lines = body.splitlines()
    for i, raw in enumerate(body_lines):
        line = clean(raw)
        if line is None:
            continue
        m = INSTALL_LINE.match(line)
        if m and not SHAKY.search(re.sub(r"常浩说「[^」]*」", "", m.group("note") or "")):
            indent = len(raw) - len(raw.lstrip())
            block = [line]   # 条目行加下面缩进更深的子条目，算同一条记录
            for nxt in body_lines[i + 1:]:
                if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= indent:
                    break
                block.append(nxt)
            installs.append(bool(VERIFIED.search("\n".join(block))))
        r = READBACK_LINE.match(line)
        if r and r.group(1) == r.group(2):
            installs.append(True)   # 产物哈希和已装哈希一致，本身就是回读
    basis = None if not installs else "有回读或 cmp 记录" if any(installs) else "仅自述"
    return basis, any(accepted_in(x) for x in lines)


def title_refs(title):
    """节标题里专门写交付编号的段：「（#247）」「（#67，PR #68 `fa81bc8`）」「#43 导航…」开头的号。
    「常浩实点 #196 后说……」这种从句里顺带提到的号不算。"""
    nums = {int(m.group(1))} if (m := re.match(r"#(\d+)\s", title)) else set()
    for c in SPLIT.split(title.replace("、", "，")):
        if REF_CLAUSE.match(c.strip()):
            nums |= {int(n) for n in REF.findall(c)}
    return nums


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

def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
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
        r = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%ct", ref], capture_output=True, text=True)
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
    r = subprocess.run(["git", "-C", str(root), "show", f"{ref}:{name}"], capture_output=True, text=True)
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
        date = None
        for d in SECTION_DATE.findall(title):
            try:
                date = dt.date.fromisoformat(d).isoformat()
                break
            except ValueError:
                continue
        out.append((title[3:].strip(), date, body))
    return out


def current_state(root, ref, w):
    """CURRENT 最上面一节、下一步、最后一次提交 CURRENT 的时间、统计日内改过没有，以及当天日期的各节（找安装、验收证据用）。
    ref 是主分支在统计日结束时的提交。没有 CURRENT 返回 None；git 读不了抛 Fail。"""
    text = show(root, ref, "CURRENT.md")
    if text is None:
        return None
    secs = sections(text)
    stamp = git(root, "log", "-1", "--format=%cI", ref, "--", "CURRENT.md").strip()
    # 改过没有看统计日窗口里的提交，不看最后一次：04:00 以后又提交一次、回放历史日期时，最后一次都会落在窗口外
    rows = git(root, "log", ref, f"--since={w[0].isoformat()}", f"--until={w[1].isoformat()}", "--format=%cI",
               "--", "CURRENT.md").split()
    return {
        "top_section": secs[0][0] if secs else None,
        "top_date": secs[0][1] if secs else None,
        "next_step": progress_data.next_step(text.splitlines()),
        "last_commit": stamp or None,
        "changed": any(inside(t, w) for t in rows),
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
                         "--json", "number,title,createdAt,isDraft,mergedAt,closedAt")
        return [{"number": r["number"], "title": r["title"], "created": r.get("createdAt")}
                for r in self._cap(repo, "open_prs", rows) if not r.get("mergedAt") and not r.get("closedAt")]

    def milestones(self, repo):
        owner, name = repo.split("/", 1)
        data = self._run("api", "graphql", "-f", f"query={MILESTONE_QUERY}", "-f", f"o={owner}", "-f", f"n={name}")
        nodes = (((data or {}).get("data") or {}).get("repository") or {}).get("issues", {}).get("nodes") or []
        return [m for m in (milestone(n) for n in self._cap(repo, "milestones", nodes)) if m]


def milestone(issue):
    """开着的父 issue 才算里程碑：先认原生子任务，再认正文清单里引用了别的 issue 号的；只有验收清单的不算（#110 评论）。"""
    subs = ((issue.get("subIssues") or {}).get("nodes")) or []
    if subs:
        done = sum(1 for s in subs if s.get("state") == "CLOSED")
        return {"number": issue["number"], "title": issue["title"], "done": done, "total": len(subs), "source": "原生子任务"}
    items = CHECK_REF.findall(issue.get("body") or "")
    if len(items) >= MIN_CHILDREN:
        done = sum(1 for mark, _ in items if mark.lower() == "x")
        return {"number": issue["number"], "title": issue["title"], "done": done, "total": len(items), "source": "正文清单"}
    return None


# ---------- 汇总一个项目 ----------

def refs_of(pr):
    """一个 PR 关联的编号，按固定顺序：自己的号在前，再按数字排标题里的号和正文里 Closes / Fixes 的号。
    正文里顺带提到的别的号不算：否则会把别的节的安装、验收挂到这个 PR 上。"""
    linked = {int(n) for n in REF.findall(pr.get("title", ""))} | {int(n) for n in CLOSES.findall(pr.get("body") or "")}
    return [pr["number"], *sorted(linked - {pr["number"]})]


def deliverables(prs, dated_sections):
    """每个合并的 PR 一条，三个维度分开记（V2 5.2）。交付、验收只认当天日期、标题里专门写了这个编号的 CURRENT 节
    （白名单格式，见文件头），或 PR 标题里单独成段的「常浩……验收通过」，否则「未知」。证据等级另记（V2 第 6 节）：
    delivery_basis 有回读或 cmp 记录 / 仅自述；acceptance_basis 目前都是交接记录，本人回执要等行动记录（1b）。
    同一个编号当天有几个 PR 关联（如 #110 的 1a、1b），这个编号上的记录不分给它们：分不清是哪一个装的。"""
    installed, accepted = {}, {}
    for title, body in dated_sections:
        basis, acc = section_status(title, body)
        where = f"CURRENT「{title[:40]}」（节级证据）"   # 节标题写了几个交付编号，这一节的证据就算给每一个
        for n in title_refs(title):
            if basis:
                installed.setdefault(n, (where, basis))
            if acc:
                accepted.setdefault(n, where)
    for pr in prs:  # 「交接补记：常浩实机验收……通过（#242）」这类文档 PR 本身就是验收记录
        if accepted_in(pr.get("title", "")):
            for n in {pr["number"], *title_refs(pr.get("title", ""))}:
                accepted.setdefault(n, f"PR #{pr['number']} 标题")
    shared = {}
    for pr in prs:
        if accepted_in(pr.get("title", "")):   # 验收记录 PR 本身不是交付，不算「同号多 PR」
            continue
        for n in refs_of(pr)[1:]:
            shared[n] = shared.get(n, 0) + 1
    out = []
    for pr in prs:
        refs, notes = [], []
        for n in refs_of(pr):
            if n != pr["number"] and shared.get(n, 0) > 1:
                if n in installed or n in accepted:
                    notes.append(f"#{n} 当天关联 {shared[n]} 个 PR，它的安装 / 验收记录不分给本 PR")
                continue
            refs.append(n)
        inst = next((installed[n] for n in refs if n in installed), None)
        acc = next((accepted[n] for n in refs if n in accepted), None)
        out.append({
            "number": pr["number"], "title": pr["title"], "url": pr.get("url"), "merged_at": pr.get("mergedAt"),
            "engineering": "已合并",
            "delivery": "已安装或已发布" if inst else "未知",
            "delivery_basis": inst[1] if inst else None,
            "acceptance": "已通过" if acc else "未知",
            "acceptance_basis": HANDOVER if acc else None,
            "evidence": [f"PR #{pr['number']} merged", *(["安装：" + inst[0]] if inst else []),
                         *(["验收：" + acc] if acc else []), *notes],
        })
    return out


def project(p, w, hub):
    """一个名册项目的记录。任何一个来源取不到：那一项记 null，原因写进 errors。"""
    root = Path(os.path.expanduser(p["path"]))
    day = w[0].date()
    rec = {"project_id": p["id"], "name": p["name"], "type": p.get("type"), "objective": None, "milestones": None,
           "yesterday_delta": {"merged_prs": None, "closed_issues": None, "commits": None, "current_changed": None},
           "current_state": {"top_section": None, "next_step": None, "next_reliable": None, "open_prs": None,
                             "checkout_branch": None, "dirty_files": None},
           "freshness": {"current_last_commit": None, "top_section_date": None, "current_as_of": None,
                         "last_merge": None, "stale": None},
           "notes": [], "errors": []}
    delta, state, fresh = rec["yesterday_delta"], rec["current_state"], rec["freshness"]
    if not root.exists():
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
        state["top_section"], state["next_step"] = cur["top_section"], cur["next_step"]
        fresh["current_last_commit"], fresh["top_section_date"] = cur["last_commit"], cur["top_date"]
        delta["current_changed"] = cur["changed"]
        dated = cur["dated_today"]
        if cur["changed"] and cur["top_date"] and cur["top_date"] < day.isoformat():
            rec["notes"].append(f"CURRENT 当天有提交，但最上面一节是 {cur['top_date']} 的：可能只改了文件头或旧节")
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
    if delta["merged_prs"] is not None:
        delta["merged_prs"] = deliverables(delta["merged_prs"], dated)
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
        state["next_reliable"] = bool(state["next_step"]) and not fresh["stale"]
    return rec


def collect(day, projects):
    w = window(day)
    hub = GitHub(w)
    recs = [project(p, w, hub) for p in projects]
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
    day = dt.date.fromisoformat(a.date) if a.date else default_day()
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
