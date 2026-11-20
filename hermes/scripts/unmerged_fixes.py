#!/usr/bin/env python3
"""已修未合并（复盘 N3，常浩 2026-11-20 批）：每周提案卡片末尾的一栏，只提醒、不编号、不用回复。

来由：11-12 那轮 8 个审计高危修复 PR 开了 7 天才合并（my-skills#93 更久），trainee-hub 同一行 CI 修复跟着 #77～#80
各带一份；修好了却没进 main，每周清单里看不到（补充复盘 2026-11-20 第三、四节，N3）。

列两类，范围是项目名册里写了 github 的项目：
  a. 开着超过 3 天、标题像修复类的 PR（编号、标题、开了几天）。修复类：标题以 fix / 「修」开头（「修改」「修订」不算），
     或任意位置有 返修、返工、回滚、热修、修复、修正、补改、hotfix、bugfix、高危、审计。前面几个词同起步骨架
     commit-msg.mjs 的 FIX_WORDS（my-skills#122）；高危、审计是 N3 加的，审计修复 PR 的标题多半只写这两个词。
  b. 同一仓库开着的 PR 里出现同一段改动。判断方法：
     - 每个 PR 取 `gh pr diff`（对它自己的目标分支），按文件切成「改动块」：一个 hunk 里连续的增删行，中间没有上下文行；
     - 块内每行连同 +/- 号、去掉行尾空白后整段比较；同一文件、整段一样才算同一处。不比行号，同一改动落在不同位置也算；
     - 太小的块不算：去掉空白后，含字母、数字或汉字的行合计不到 MIN_CHARS 个字符（单独的「}」「import os」之类）；
     - 交接文件不比：CURRENT.md、HANDOFF.md、路径里带「交接」的。
     同一组 PR 合成一条，写明是哪几个文件、几处。

失败路径（全局坑 G-39）：没查到不当成 0 条，卡片上写「没查到：原因」，并写明是哪几个仓库。
  - 名册读不了：整栏没查到。
  - 找不到 gh、gh 没登录、被限流、连不上 GitHub、累计 2 次超时：后面的仓库不再查（查也是同样结果），都标这个原因。
  - 其他错误（仓库不在、没权限、输出不是 JSON）只算这个仓库没查到，其余仓库照查。
  - 某个 PR 的 diff 取不到（例如改动太大）：这个仓库的 a 类照列，b 类写明哪个 PR 没比。
  - 开着的 PR 到了 LIST_LIMIT 上限：写明可能没列全。

环境变量：DT_GH（换 gh，自测用；默认先找 PATH，再找 /opt/homebrew/bin、/usr/local/bin，Hermes 定时任务的 PATH 不全）。
"""
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess

import netwait

STALE_DAYS = 3
GH_TIMEOUT = 60
MAX_TIMEOUTS = 2
LIST_LIMIT = 100
MIN_CHARS = 16
SHOW_MAX = 10
TITLE_MAX = 40
GROUP = "已修未合并"

FIX_TITLE = re.compile(r"^fix\b|^修(?!改|订)|返修|返工|回滚|热修|修复|修正|补改|hotfix|bugfix|高危|审计", re.I)
HANDOFF = re.compile(r"(^|/)(CURRENT|HANDOFF)\.md$|交接", re.I)
WORD = re.compile(r"[0-9A-Za-z一-鿿]")
AUTH = re.compile(r"\b401\b|bad credentials|gh auth login|not logged in|authentication required|requires authentication", re.I)
RATE = re.compile(r"rate limit", re.I)
GONE = re.compile(r"could not resolve to a (repository|pullrequest)|\b404\b|not found", re.I)   # 在 NET 前面判：「resolve」也是网络词


class GhError(Exception):
    """gh 没取到。stop=True：后面的仓库查也是同样结果，不再查。"""

    def __init__(self, why, stop=False):
        super().__init__(why)
        self.why, self.stop = why, stop


def clip(text, n=80):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def find_gh():
    if os.environ.get("DT_GH"):
        return os.environ["DT_GH"]
    return shutil.which("gh", path=os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"]))


class Gh:
    def __init__(self, exe, timeout=None):
        self.exe, self.timeout = exe, timeout or GH_TIMEOUT
        self.down = None if exe else "找不到 gh 命令"
        self.timeouts = 0

    def run(self, args):
        if self.down:
            raise GhError(self.down, stop=True)
        try:
            r = subprocess.run([self.exe, *args], capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.timeouts += 1
            if self.timeouts >= MAX_TIMEOUTS:
                self.down = f"gh 已有 {self.timeouts} 次超过 {self.timeout} 秒没回应"
                raise GhError(self.down, stop=True)
            raise GhError(f"gh 超过 {self.timeout} 秒没回应")
        except OSError as e:
            self.down = f"gh 启动不了（{e.strerror or e}）"
            raise GhError(self.down, stop=True)
        if r.returncode:
            full = (r.stderr or r.stdout).strip() or f"退出码 {r.returncode}"
            last = " ".join(full.splitlines()[-1].split())
            msg = last if len(last) <= 120 else "…" + last[-119:]        # 归类看全文，显示取最后一行的结尾（原因多在后面）
            if AUTH.search(full):
                self.down = f"gh 没登录或登录失效（{msg}）"
            elif RATE.search(full):
                self.down = f"被 GitHub 限流（{msg}）"
            elif not GONE.search(full) and netwait.is_net_error(full):
                self.down = f"连不上 GitHub（{msg}）"
            if self.down:
                raise GhError(self.down, stop=True)
            raise GhError(f"gh 出错（{msg}）")
        return r.stdout


# ---------- 判定（纯函数，回放也用这几个） ----------

def is_fix(title):
    return bool(FIX_TITLE.search((title or "").strip()))


def parse_time(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))   # 3.9 的 fromisoformat 不认结尾的 Z


def stale_fixes(prs, now, days=STALE_DAYS):
    """开着超过 days 天的修复类 PR，开得最久的在前。createdAt 不对抛 ValueError / KeyError（按这个仓库没查到算）。"""
    out = []
    for p in prs:
        age = now - parse_time(p["createdAt"])
        if age > dt.timedelta(days=days) and is_fix(p.get("title")):
            out.append({"number": p["number"], "title": p.get("title") or "", "days": age.days})
    return sorted(out, key=lambda x: (-x["days"], x["number"]))


def unquote(path):
    """git 给非 ASCII 路径加的引号和八进制转义还原成原文。"""
    if not (path.startswith('"') and path.endswith('"')):
        return path
    return path[1:-1].encode("ascii", "backslashreplace").decode("unicode_escape").encode("latin-1").decode("utf-8", "replace")


def diff_path(line, prefix):
    p = unquote(line[4:].rstrip("\t").strip())
    return p[2:] if p.startswith(prefix) else None


def change_blocks(diff):
    """{(文件, 块指纹): 示例行}：一个 hunk 里连续的增删行算一块，太小的和交接文件不算。"""
    out, block = {}, []
    path, in_hunk = None, False

    def flush():
        if path and block and not HANDOFF.search(path):
            body = [(l[0], l[1:].strip()) for l in block]
            if sum(len("".join(t.split())) for _, t in body if WORD.search(t)) >= MIN_CHARS:
                key = hashlib.sha1("\n".join(l.rstrip() for l in block).encode("utf-8")).hexdigest()
                ex = next((t for s, t in body if s == "+" and WORD.search(t)), None) or next(t for _, t in body if WORD.search(t))
                out.setdefault((path, key), clip(ex, 60))
        block.clear()

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            path, in_hunk = None, False
        elif not in_hunk:
            if line.startswith("--- "):
                path = diff_path(line, "a/")
            elif line.startswith("+++ "):
                path = diff_path(line, "b/") or path       # 删掉的文件 +++ 是 /dev/null，留 --- 那边的路径
            elif line.startswith("@@"):
                in_hunk = True
        elif line.startswith("@@"):
            flush()
        elif line[:1] in ("+", "-"):
            block.append(line)
        elif not line.startswith("\\"):                    # 「\ No newline at end of file」不打断
            flush()
    flush()
    return out


def duplicate_changes(diffs):
    """diffs：{PR 编号: diff 文本}。返回同一段改动出现在 2 个以上 PR 的，按 PR 组合并：
    [{"prs": [编号…], "files": [{"file", "blocks", "example"}…]}]，涉及 PR 多的在前。"""
    seen = {}
    for n, text in diffs.items():
        for k, ex in change_blocks(text).items():
            seen.setdefault(k, [set(), ex])[0].add(n)
    groups = {}
    for (path, _), (prs, ex) in seen.items():
        if len(prs) < 2:
            continue
        g = groups.setdefault(tuple(sorted(prs)), {})
        f = g.setdefault(path, {"file": path, "blocks": 0, "example": ex})
        f["blocks"] += 1
    return [{"prs": list(k), "files": sorted(v.values(), key=lambda f: f["file"])}
            for k, v in sorted(groups.items(), key=lambda kv: (-len(kv[0]), kv[0]))]


# ---------- 取数 ----------

def roster_repos(path):
    """名册里写了 github 的项目：([(仓库短名, owner/repo)…], 读不了的原因)。"""
    try:
        projects = json.loads(path.read_text(encoding="utf-8"))["projects"]
        repos = sorted({p["github"] for p in projects if isinstance(p, dict) and p.get("github")})
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [], f"项目名册 {path} 读不了（{type(e).__name__}：{clip(e, 60)}）"
    return [(r.split("/")[-1], r) for r in repos], None


def scan(repos, now, gh):
    """逐个仓库查，一个仓库失败不影响其他仓库。"""
    res = {"repos": [name for name, _ in repos], "stale": [], "dups": [], "failed": {}, "partial": {}}
    for name, repo in repos:
        try:
            prs = json.loads(gh.run(["pr", "list", "-R", repo, "--state", "open", "--limit", str(LIST_LIMIT),
                                     "--json", "number,title,createdAt"]) or "null")
            if not isinstance(prs, list):
                raise TypeError("不是列表")
            stale = stale_fixes(prs, now)
        except GhError as e:
            res["failed"][name] = e.why
            continue
        except (ValueError, KeyError, TypeError) as e:
            res["failed"][name] = f"gh 输出格式不对（{type(e).__name__}：{clip(e, 60)}）"
            continue
        if len(prs) >= LIST_LIMIT:
            res["partial"].setdefault(name, []).append(f"开着的 PR 到了 {LIST_LIMIT} 个上限，可能没列全")
        res["stale"] += [{**s, "repo": name} for s in stale]
        if len(prs) < 2:
            continue
        diffs = {}
        for p in sorted(prs, key=lambda p: p["number"]):
            try:
                diffs[p["number"]] = gh.run(["pr", "diff", str(p["number"]), "-R", repo, "--color", "never"])
            except GhError as e:
                if e.stop:
                    res["partial"].setdefault(name, []).append(f"从 #{p['number']} 起的改动没取到（{e.why}），重复改动没比完")
                    break
                res["partial"].setdefault(name, []).append(f"#{p['number']} 的改动没取到（{e.why}），重复改动没比它")
        res["dups"] += [{**d, "repo": name} for d in duplicate_changes(diffs)]
    return res


def collect(roster, now, gh=None):
    repos, err = roster_repos(roster)
    if err:
        return {"error": err}
    return scan(repos, now, gh or Gh(find_gh()))


# ---------- 卡片上的这一栏 ----------

def short(s):
    s = " ".join(str(s).split())
    return s if len(s) <= TITLE_MAX else s[:TITLE_MAX - 1] + "…"


def render(res):
    """返回 (这一栏的行, 有没有要看的)。查到了条目、或有没查到的仓库，才算有要看的；全查了且没有条目时只写一行「没有」。"""
    head = f"**{GROUP}**（只提醒，不用回复）"
    if res.get("error"):
        return [head, f"⚠️ 本期没查到（不是 0 条）：{res['error']}。"], True
    repos, failed = res["repos"], res["failed"]
    if not repos:
        return [head, "名册里没有写了 github 的项目，这一栏没查。"], False
    ok = len(repos) - len(failed)
    lines = [head]
    if not failed and not res["stale"] and not res["dups"] and not res["partial"]:
        return [head, f"名册 {len(repos)} 个仓库都查了：没有开着超过 {STALE_DAYS} 天的修复类 PR，也没有重复的同一处改动。"], False
    if ok:
        lines.append(f"开着超过 {STALE_DAYS} 天的修复类 PR：" + ("" if res["stale"] else f"没有（查了 {ok} 个仓库）"))
        stale = sorted(res["stale"], key=lambda s: (-s["days"], s["repo"], s["number"]))
        lines += [f"- {s['repo']}#{s['number']} {short(s['title'])}（开了 {s['days']} 天）" for s in stale[:SHOW_MAX]]
        if len(stale) > SHOW_MAX:
            lines.append(f"- 另有 {len(stale) - SHOW_MAX} 个没列")
        lines.append("多个开着的 PR 里重复的同一处改动：" + ("" if res["dups"] else f"没有（查了 {ok} 个仓库）"))
        for d in res["dups"][:SHOW_MAX]:
            files, n = d["files"], sum(f["blocks"] for f in d["files"])
            prs = "、".join(f"#{p}" for p in d["prs"])
            if len(files) == 1:
                what = f"{files[0]['file']} {n} 处（如「{files[0]['example']}」）"
            else:
                what = "、".join(f["file"] for f in files[:3]) + (f" 等 {len(files)} 个文件" if len(files) > 3 else "") + f"，共 {n} 处"
            lines.append(f"- {d['repo']} {prs}：{what}")
        if len(res["dups"]) > SHOW_MAX:
            lines.append(f"- 另有 {len(res['dups']) - SHOW_MAX} 组没列")
    by = {}
    for name, why in failed.items():
        by.setdefault(why, []).append(name)
    for why, names in by.items():
        who = f"全部 {len(repos)} 个仓库" if len(names) == len(repos) else "、".join(sorted(names))
        lines.append(f"⚠️ 没查到（不是 0 条）：{who}——{why}")
    for name in sorted(res["partial"]):
        lines.append(f"⚠️ {name} 只查了一部分：" + "；".join(res["partial"][name]))
    return lines, True
