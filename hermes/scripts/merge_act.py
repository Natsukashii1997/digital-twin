#!/usr/bin/env python3
"""飞书一句话合并（#67）：影子合并官给新 PR 发卡片，常浩点卡片上的「合并」「退回」按钮（#78），或引用卡片回「合并」「退回 原因」后，由 Hermes 调用本脚本执行。

授权是单次的 owner_direct：每次都是常浩本人对一张卡片（仓库、PR、完整 HEAD）说了话；本脚本只认发过的卡片，不认别的 PR 或版本。
不放权、不变基、不改代码、不安装、不写 CURRENT。

合并前逐项核对，任一不满足就停下说明（《工程流程》第 5 节、ARCHITECTURE 5.5）：
  1. 仓库在 merge_rules.json 的 act.repos 里；风险档不是 L3（L3 不走飞书）；
  2. PR 还开着、不是草稿、HEAD 仍是卡片上的、base 是主分支、没冲突、不落后（mergeStateStatus 为 CLEAN）；
  3. 本机主目录的主分支没有没推的提交；
  4. 登记了 ci 的仓库：这个 HEAD 的 GitHub 检查全部通过（还在跑、没过、一项都没有都停下），不在本机跑测试；
     没登记 ci 的：在干净 worktree 里检出这个 HEAD，先跑 setup（装依赖）再跑 test，通过；
  5. 合并命令带 --match-head-commit（服务端再校验一次 HEAD），合并后回读确认已合并。
连 GitHub 的只读调用（取状态、回读）碰到网络瞬断（EOF、SSL_ERROR_SYSCALL、超时、connection reset）时隔 3 秒、5 秒各重试一次；
合并、留言这类写操作不重试，合并靠 --match-head-commit 和回读确认，不会重复合并。
没合并、没退回时固定说四件事（一两句话）：卡在哪一步（取 PR 状态、核对版本、跑测试、执行合并、合并后回读；退回另有留言退回）、
为什么（人话的原因类别，括号里附不超过 80 字的原文）、现在的状态（PR 有没有被改）、怎么办（一个具体动作）。
合并后主目录停在主分支、又没有未提交改动时才 git pull --ff-only；否则只说明为什么没拉。

用法：
  merge_act.py cards                         列出还没处理的卡片（Hermes 技能核对用）
  merge_act.py merge <仓库#PR@HEAD前缀>        例如 digital-twin#68@1a2b3c4
  merge_act.py return <仓库#PR@HEAD前缀> 原因   在 PR 里留言退回（不关 PR）
  merge_act.py button merge|return <仓库#PR@HEAD前缀> --chat <chat_id>
                                             卡片按钮（#78）：Hermes 的 /card 插件在后台调用；进度和结果直接发到常浩私聊
环境变量同 merge_officer.py，另有 DT_MERGE_RUNS（测试运行目录，默认 DigitalTwin/merge-runs）、
DT_GH_RETRY_WAITS（只读调用网络瞬断时每次重试前等几秒，逗号分隔，默认 3,5）。

托管合并（#136）只能调 run_custody_merge（#132）：在函数里从本机钥匙串取合并执行器的专用令牌（常浩账号下只对沙盒仓库，
内容、拉取请求读写），放进最小环境（固定 github.com、空的 gh 配置目录、不继承宿主 GH_* / GITHUB_*）；取不到就停，
不退回常浩本人的 gh 登录；走 REST 合并接口，以接口返回的本次合并提交为成功证据；返回 merged / failed / unsure，unsure 只做只读对账、不重发。卡片合并（B 档）走 run_owner_merge，用本机 gh 登录。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import merge_officer as mo
from tidings_weekly import SendError, send, strip_env

CARDS = mo.SHADOW / "cards.json"
RUNS = Path(os.environ.get("DT_MERGE_RUNS", "~/Library/Application Support/DigitalTwin/merge-runs")).expanduser()
VIEW_FIELDS = mo.PR_FIELDS + ",baseRefName,mergeStateStatus,url"
REF = re.compile(r"^([\w.-]+)#(\d+)@([0-9a-f]{7,40})$")
COLOR = {mo.MERGE: "green", mo.RETURN: "orange", mo.ASK: "red"}
CHECK_OK = {"SUCCESS", "NEUTRAL", "SKIPPED"}
CHECK_WAIT = {"PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"}
NET_ERR = re.compile(r"\bEOF\b|SSL_ERROR_SYSCALL|timed? ?out|connection reset|reset by peer|no such host|"
                     r"could not resolve host|temporary failure in name resolution|dial tcp: lookup", re.I)
RATE_ERR = re.compile(r"rate limit", re.I)
AUTH_ERR = re.compile(r"\b401\b|bad credentials|gh auth login|not logged in|authentication", re.I)
DENY_ERR = re.compile(r"\b403\b|permission|not accessible|forbidden", re.I)
GONE_ERR = re.compile(r"no pull requests found|could not resolve to a pullrequest|\b404\b|not found", re.I)
UNCHANGED = "PR 没有任何改动"
STEP = ["核对版本"]                                    # 现在做到哪一步，失败提示开头说卡在这里
UNSURE = "不确定合上没有"
REJECTED = re.compile(r"protected ref|rule violations|not mergeable|base branch policy|Head branch was modified|"
                      r"\b40[1359]\b|\b422\b|bad credentials|not accessible|permission", re.I)  # GitHub 明确拒绝合并
SECURITY = "/usr/bin/security"
CUSTODY_CRED = {"Natsukashii1997/dt-sandbox": ("dt-merge-executor", "dt-sandbox")}  # 托管合并凭据（#132）：完整仓库名 → 钥匙串 (service, account)
CUSTODY_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
CUSTODY_PASS = ("HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR",  # 托管合并子进程只继承这些
                "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "all_proxy", "no_proxy")


def at(step):
    STEP[0] = step


class Stop(Exception):
    """核对没过或出错：不合并、不退回。按四段告诉常浩：卡在哪一步（创建时的 STEP）、为什么、现在的状态、怎么办。
    todo 里的 {again} 输出时换成「再点一次合并」或「再说一次「退回 原因」」。"""

    def __init__(self, why, todo, state=UNCHANGED, verdict=None):
        super().__init__(why)
        self.why, self.todo, self.state, self.verdict, self.step = why, todo, state, verdict, STEP[0]


def fail_text(verb, e):
    again = "再点一次合并" if verb == "merge" else "再说一次「退回 原因」"
    head = e.verdict or ("没合并" if verb == "merge" else "没退回")
    return f"{head}：在『{e.step}』这一步{e.why}。{e.state}。{e.todo.replace('{again}', again)}。"


def clip(text, n=80):
    text = " ".join(str(text).split())
    return text if len(text) <= n else "…" + text[-(n - 1):]


def gh_stop(text, retried=0, other="gh 报了没归类的错"):
    """把 gh 的报错归成人话的原因类别，配一个具体动作。"""
    raw = clip(text)
    if NET_ERR.search(text):
        tried = f"已自动重试 {retried} 次仍失败，" if retried else ""
        return Stop(f"连不上 GitHub（网络中途断开：{raw}）", tried + "隔一分钟{again}")
    if RATE_ERR.search(text):
        return Stop(f"被 GitHub 拒绝：超出频率限制（{raw}）", "过一小时{again}")
    if AUTH_ERR.search(text):
        return Stop(f"被 GitHub 拒绝：gh 登录失效（{raw}）", "需要常浩在电脑上重新登录 gh（gh auth login），之后{again}")
    if DENY_ERR.search(text):
        return Stop(f"被 GitHub 拒绝：权限不足（{raw}）", "在电脑上交给 Claude 会话查 gh 的权限")
    if GONE_ERR.search(text):
        return Stop(f"找不到这个 PR（{raw}）", "在电脑上看一下这个 PR 还在不在，这张卡片先不用再点")
    return Stop(f"{other}（{raw}）", "在电脑上交给 Claude 会话看")


# ---------- 卡片 ----------

def read_cards():
    try:
        return json.loads(CARDS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cards": []}


def write_cards(data):
    mo.SHADOW.mkdir(parents=True, exist_ok=True)
    tmp = CARDS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CARDS)


def act_repos(rules):
    return (rules.get("act") or {}).get("repos") or {}


def card_ref(repo, pr, head):
    return f"{repo.split('/')[-1]}#{pr}@{head[:7]}"


def build_card(row, facts, url):
    ref = card_ref(row["repo"], row["pr"], row["head_sha"])
    paths = facts.get("paths") or []
    lines = [f"**{row['title']}**", f"[{row['repo']}#{row['pr']}]({url})", "",
             f"风险档 **{row['tier']}** · Hermes 建议 **{row['suggestion']}**" + (f"：{row['reasons']}" if row["reasons"] else ""),
             f"改了 {len(paths)} 个文件：" + "、".join(f"`{p}`" for p in paths[:5]) + ("…" if len(paths) > 5 else ""),
             f"版本 `{row['head_sha'][:7]}`", ""]
    buttons = []
    if row["tier"] == "L3":
        lines.append("⚠️ 高风险（L3），飞书里不合并。请在电脑上让 Claude 会话处理。")
    else:
        lines.append("点 **合并**：Hermes 核对版本、跑测试后合并；点 **退回**：它问你原因，再留言到 PR。")
        buttons = [button("✅ 合并", "primary_filled", "merge", ref), button("↩️ 退回", "default", "return", ref)]
    elements = [{"tag": "markdown", "content": "\n".join(lines)}]
    if buttons:
        elements.append({"tag": "column_set", "flex_mode": "none", "horizontal_spacing": "8px",
                         "columns": [{"tag": "column", "width": "auto", "elements": [b]} for b in buttons]})
    return {"schema": "2.0",
            "header": {"title": {"tag": "plain_text", "content": f"🔀 待你决定 · {ref}"},
                       "template": COLOR.get(row["suggestion"], "blue")},
            "body": {"elements": elements}}


def button(label, style, verb, ref):
    """点按钮时网关把 value 转成一条 `/card button {"merge_officer": verb, "ref": ref}` 交给 Hermes（merge-officer 技能认它）。"""
    return {"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": style,
            "behaviors": [{"type": "callback", "value": {"merge_officer": verb, "ref": ref}}]}


def home_chat():
    from feishu_home import home_chat as hc           # 同一套：环境变量，没有再从 ~/.hermes/.env 只读这一项（#165）
    return hc()


def send_cards(rows, facts_by_key, chat=None):
    """scan 判出新的「PR × HEAD」后调用：act.repos 里的仓库每个发一张卡片。返回出错说明列表。"""
    rules = mo.load_rules()
    covered = act_repos(rules)
    data = read_cards()
    sent = {(c["repo"], str(c["pr"]), c["head"]) for c in data["cards"]}
    errors = []
    for r in rows:
        key = (r["repo"], r["pr"], r["head_sha"])
        if r["repo"].split("/")[-1] not in covered or key in sent:
            continue
        chat = chat if chat is not None else home_chat()     # 有要发的才去找 chat_id
        if not chat.startswith("oc_"):
            errors.append("PR 卡片没发：没有私聊 chat_id")
            break
        f = facts_by_key.get(key, {})
        url = f"https://github.com/{r['repo']}/pull/{r['pr']}"
        try:
            mid = send(chat, "interactive", build_card(r, f, url), f"merge-card-{r['repo'].split('/')[-1]}-{r['pr']}-{r['head_sha'][:12]}")
        except SendError as e:
            errors.append(f"{card_ref(*key)} 卡片没发：{e}")
            continue
        data["cards"].append({"message_id": mid, "repo": r["repo"], "pr": r["pr"], "head": r["head_sha"],
                              "tier": r["tier"], "suggestion": r["suggestion"], "title": r["title"], "sent_at": mo.now()})
    write_cards(data)
    return errors


def find_card(ref):
    m = REF.match(ref.strip())
    if not m:
        raise Stop(f"看不懂「{ref}」（要写成 仓库#PR@版本，例如 digital-twin#68@1a2b3c4）", "引用飞书里的卡片{again}")
    name, pr, h = m.groups()
    hits = [c for c in read_cards()["cards"]
            if c["repo"].split("/")[-1] == name and str(c["pr"]) == pr and c["head"].startswith(h)]
    if not hits:
        raise Stop(f"没发过 {ref} 这张卡片（只认发过的卡片上的版本）", "引用飞书里最新的卡片{again}，或等下一张卡片")
    if len({c["head"] for c in hits}) > 1:
        raise Stop(f"{ref} 对得上不止一个版本", "引用卡片、写更长的版本号{again}")
    return hits[-1]


# ---------- 记录 ----------

def record_owner(card, decision):
    rows = mo.read_rows()
    for r in rows:
        if (r["repo"], r["pr"], r["head_sha"]) == (card["repo"], str(card["pr"]), card["head"]):
            r["owner"], r["owner_at"] = decision, mo.now()
    mo.write_rows(rows)


# ---------- 执行 ----------

def gh_read(args, what):
    """只读的 gh 调用：网络瞬断时按 DT_GH_RETRY_WAITS 等几秒再试（默认 3 秒、5 秒各一次），别的错误不重试；
    最后还是失败就按原因类别变成 Stop。合并、留言这类写操作不走这里。"""
    waits = [float(x) for x in os.environ.get("DT_GH_RETRY_WAITS", "3,5").split(",") if x.strip()]
    for tried, wait in enumerate([*waits, None]):
        try:
            return mo.gh_json(args, what)
        except mo.GhError as e:
            text = str(e).split("：", 1)[-1]              # 去掉 gh_json 加的「做什么：」前缀，只留原文
            if wait is None or not NET_ERR.search(text):
                raise gh_stop(text, tried)
            time.sleep(wait)


def git(cwd, *args, timeout=120):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        last = ((r.stderr or r.stdout).strip().splitlines() or ["未知"])[-1]
        if NET_ERR.search(last):
            raise gh_stop(last)
        raise Stop(f"git {args[0]} 出错（{clip(last)}）", "在电脑上交给 Claude 会话看")
    return r.stdout.strip()


def project_of(name):
    for p in json.loads(mo.ROSTER.read_text(encoding="utf-8"))["projects"]:
        if (p.get("github") or "").split("/")[-1] == name:
            return Path(p["path"]).expanduser(), p.get("main") or "main"
    raise Stop(f"名册里没有 {name}", "在电脑上交给 Claude 会话登记名册")


def require_ci_passed(p):
    """statusCheckRollup（只含当前 HEAD 的检查）全部通过才往下走，否则停下并说明哪项在跑、哪项没过。
    免费版私有仓库没有分支保护，GitHub 不拦，所以这里自己把 CI 当合并条件。"""
    at("跑测试")
    checks = p.get("statusCheckRollup") or []
    if not checks:
        raise Stop("这个版本没有任何 CI 检查结果（该有 CI 却没跑）", "在电脑上交给 Claude 会话看 CI 为什么没跑")
    failed, waiting = [], []
    for c in checks:
        name = c.get("name") or c.get("context") or "?"
        status = str(c.get("status") or "").upper()
        result = str(c.get("conclusion") or c.get("state") or "").upper()
        if (status and status != "COMPLETED") or result in CHECK_WAIT or not result:
            waiting.append(name)
        elif result not in CHECK_OK:
            failed.append(f"{name}（{result}）")
    if failed:
        raise Stop("CI 没过：" + "、".join(failed), "交回开这个 PR 的会话修，修好会有新卡片")
    if waiting:
        raise Stop("CI 还在跑：" + "、".join(waiting), "等 CI 跑完{again}")


def check_pr(card, rules, main, ci=False):
    at("取 PR 状态")
    p = gh_read(["pr", "view", str(card["pr"]), "--repo", card["repo"], "--json", VIEW_FIELDS], f"{card['repo']}#{card['pr']} 取状态")
    at("核对版本")
    if p.get("state") != "OPEN":
        raise Stop(f"PR 已经是 {p.get('state')}", "不用再合，这张卡片可以不管了")
    if p.get("headRefOid") != card["head"]:
        raise Stop(f"PR 在卡片之后又有提交（现在是 {str(p.get('headRefOid'))[:7]}）", "等新卡片再决定")
    if p.get("baseRefName") != main:
        raise Stop(f"PR 不是合进 {main}（是 {p.get('baseRefName')}）", "在电脑上交给 Claude 会话看")
    if p.get("isDraft"):
        raise Stop("PR 还是草稿", "等开 PR 的会话转成正式 PR、发了新卡片再决定")
    tier = mo.tier_of(card["repo"], sorted(f.get("path", "") for f in p.get("files") or []), rules)
    if tier == "L3":
        raise Stop("改动碰了敏感路径（L3），飞书里不合并", "在电脑上交给 Claude 会话处理")
    state = p.get("mergeStateStatus")
    if p.get("mergeable") == "CONFLICTING" or state == "DIRTY":
        raise Stop("和主分支有冲突", "在电脑上交给开这个 PR 的会话变基，变基后会有新卡片")
    if state == "BEHIND":
        raise Stop("落后主分支", "在电脑上交给开这个 PR 的会话变基，变基后会有新卡片")
    if ci:
        require_ci_passed(p)                          # CI 没过或在跑时 GitHub 报 UNSTABLE，先说清楚是 CI
        at("核对版本")
    if state != "CLEAN":
        raise Stop(f"GitHub 现在的合并状态是 {state}，不是 CLEAN", "隔几分钟{again}，还不行就在电脑上看")
    return p


def run_tests(path, card, conf):
    cmd = " && ".join(c for c in (conf.get("setup"), conf["test"]) if c)
    timeout = int(conf.get("timeout", 900))
    at("跑测试")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run = RUNS / f"{card['repo'].split('/')[-1]}-{card['pr']}-{card['head'][:7]}-{stamp}"
    run.mkdir(parents=True, exist_ok=True)
    wt = run / "src"
    git(path, "worktree", "prune")
    git(path, "fetch", "-q", "origin", f"refs/pull/{card['pr']}/head")
    if git(path, "rev-parse", "FETCH_HEAD") != card["head"]:
        raise Stop("从 GitHub 取到的 PR 版本和卡片上的不一致", "等新卡片再决定")
    git(path, "worktree", "add", "-q", "--detach", str(wt), card["head"])
    try:
        try:
            r = subprocess.run(cmd, shell=True, cwd=wt, capture_output=True, text=True, timeout=timeout, env=strip_env())
            out, code = (r.stdout or "") + (r.stderr or ""), r.returncode
        except subprocess.TimeoutExpired:
            out, code = f"超过 {timeout} 秒没跑完", -1
    finally:
        subprocess.run(["git", "-C", str(path), "worktree", "remove", "--force", str(wt)], capture_output=True)
    (run / "test.log").write_text(f"$ {cmd}\n{out}", encoding="utf-8")
    if code != 0:
        last = (out.strip().splitlines() or ["没有输出"])[-1]
        raise Stop(f"测试没过（{clip(last)}）", "交回开这个 PR 的会话修，修好会有新卡片",
                   state=f"{UNCHANGED}，测试日志在 {run / 'test.log'}")
    return run


def sync_main(path, main):
    branch = git(path, "branch", "--show-current")
    if branch != main:
        return f"主目录没拉齐：检出的是 {branch or '游离 HEAD'}，不是 {main}"
    if git(path, "status", "--porcelain"):
        return "主目录没拉齐：有未提交的改动，没动它"
    try:
        git(path, "pull", "-q", "--ff-only")
    except Stop as e:
        return f"主目录没拉齐：{e}"
    return f"主目录已拉齐到 {git(path, 'rev-parse', '--short', 'HEAD')}"


def custody_token(repo):
    """托管合并凭据（#132）：按完整仓库名从钥匙串取合并执行器的专用令牌。没登记的仓库不读钥匙串；读不到、为空都停。"""
    if repo not in CUSTODY_CRED:
        raise Stop(f"{repo} 没有托管合并凭据（只登记了 {'、'.join(CUSTODY_CRED)}）", "托管合并只做登记过的沙盒仓库，交给常浩决定")
    service, account = CUSTODY_CRED[repo]
    try:
        r = subprocess.run([SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        raise Stop(f"读不了钥匙串（{type(e).__name__}）", "在电脑上检查钥匙串")
    token = (r.stdout or "").strip()
    if r.returncode != 0 or not token:
        raise Stop(f"钥匙串里没有合并执行器令牌（{service} / {account}）",
                   "常浩按 docs/DT-ORC/migration/2026-11-10-合并隔离实测.md 重新生成并存入钥匙串")
    return token


def custody_env(token, config_dir):
    """托管合并子进程的最小环境：不继承宿主的 GH_* / GITHUB_* 等变量，只带网络要用的代理与证书设置；
    主机固定 github.com，凭据只有 GH_TOKEN，配置目录是空的临时目录（读不到本机 gh 登录），不交互、不调试输出。"""
    env = {k: os.environ[k] for k in CUSTODY_PASS if k in os.environ}
    env.update(PATH=CUSTODY_PATH, GH_HOST="github.com", GH_TOKEN=token, GH_CONFIG_DIR=str(config_dir),
               GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", NO_COLOR="1")
    return env


def _gh_write(cmd, env, secret=None):
    """发一次 gh 写命令（不重试：没收到回应也可能已经执行，以回读为准），返回结构化结果；输出里出现令牌就打码。"""
    res = {"returncode": None, "timed_out": False, "launched": True, "why": "", "stdout": ""}
    try:
        mr = subprocess.run(cmd, capture_output=True, text=True, timeout=mo.TIMEOUT, env=env)
        res["returncode"], res["stdout"] = mr.returncode, mr.stdout or ""
        res["why"] = ((mr.stderr or mr.stdout or "").strip().splitlines() or ["没有输出"])[-1]
    except subprocess.TimeoutExpired:
        res["timed_out"] = True
        res["why"] = f"超过 {mo.TIMEOUT} 秒没回应（timeout）"
    except OSError as e:                              # gh 没起来（卸载、链接失效、没执行权限）：一个请求都没发出去
        res["launched"] = False
        res["why"] = f"gh 跑不起来（{type(e).__name__}）"
    if secret:
        res["why"] = res["why"].replace(secret, "***")
        res["stdout"] = res["stdout"].replace(secret, "***")
    return res


def run_owner_merge(card):
    """卡片合并（B 档，常浩本人点的）：用本机 gh 登录。托管合并不能走这里，用 run_custody_merge。"""
    cmd = [mo.GH, "pr", "merge", str(card["pr"]), "--repo", card["repo"], "--squash", "--match-head-commit", card["head"]]
    res = _gh_write(cmd, None)
    res.pop("stdout")
    return {**res, "identity": "owner"}


def _json_dict(text):
    try:
        d = json.loads(text)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def run_custody_merge(card):
    """托管合并（#136 调用）：凭据在函数里强制取，调用方选不了身份；取不到就停，不发任何写命令、不退回本机 gh 登录。
    走 REST 合并接口（PUT pulls/{n}/merge，sha 绑定 HEAD），以接口返回的「本次已合并 + 合并提交号」为成功证据——
    gh pr merge 对已经被别人合上的 PR 也返回 0，不能当本次合并的证据。写和回读用同一个受控环境。status：
    merged（接口说本次合上了、回读已合并、HEAD 对得上、合并提交号一致）；
    failed（GitHub 明确拒绝且回读仍开着，或 gh 没起来、请求没发出去）；
    unsure（其余一切：超时、网络中断、没归类的报错、已被别人合上、HEAD 对不上、回读失败或不是对象）——只做只读对账，不重发合并。"""
    token = custody_token(card["repo"])
    out = {"identity": "custody", "repo": card["repo"], "pr": card["pr"], "head": card["head"], "merge_commit": None}
    cmd = [mo.GH, "api", "-X", "PUT", f"repos/{card['repo']}/pulls/{card['pr']}/merge",
           "-f", "merge_method=squash", "-f", f"sha={card['head']}"]
    with tempfile.TemporaryDirectory(prefix="dt-custody-gh-") as cfg:
        env = custody_env(token, cfg)
        res = _gh_write(cmd, env, token)
        try:
            r = subprocess.run([mo.GH, "pr", "view", str(card["pr"]), "--repo", card["repo"], "--json",
                                "state,mergeCommit,headRefOid"], capture_output=True, text=True, timeout=mo.TIMEOUT, env=env)
            after = _json_dict(r.stdout) if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            after = None
    reply = _json_dict(res.pop("stdout")) or {}
    out.update(res)
    if not res["launched"]:
        out["status"] = "failed"                      # 合并请求没发出去，确定没合
        return out
    state = (after or {}).get("state")
    out["merge_commit"] = ((after or {}).get("mergeCommit") or {}).get("oid")
    rejected = res["returncode"] not in (None, 0) and REJECTED.search(res["why"]) and not NET_ERR.search(res["why"])
    if after is None or after.get("headRefOid") != card["head"]:
        out["status"] = "unsure"
    elif (res["returncode"] == 0 and reply.get("merged") is True and reply.get("sha")
          and state == "MERGED" and out["merge_commit"] == reply["sha"]):
        out["status"] = "merged"
    elif rejected and state == "OPEN":                # 只有 GitHub 明确拒绝、PR 仍开着才算确定没合
        out["status"] = "failed"
    else:
        out["status"] = "unsure"
    return out


def cmd_merge(ref):
    card = find_card(ref)
    rules = mo.load_rules()
    conf = act_repos(rules).get(card["repo"].split("/")[-1])
    if not conf:
        raise Stop("这个仓库还没开飞书合并（merge_rules.json 的 act.repos）", "在电脑上交给 Claude 会话处理")
    path, main = project_of(card["repo"].split("/")[-1])
    ci = bool(conf.get("ci"))
    check_pr(card, rules, main, ci)
    git(path, "fetch", "-q", "origin")
    ahead = git(path, "rev-list", "--count", f"origin/{main}..{main}")
    if ahead != "0":
        raise Stop(f"本机主目录的 {main} 有 {ahead} 个没推的提交", "按《工程流程》第 5 节在电脑上决定推不推，处理完{again}")
    if ci:
        tested = "CI 通过"
    elif not conf.get("test"):
        at("跑测试")
        raise Stop("这个仓库没登记 CI，也没登记测试命令（merge_rules.json 的 act.repos）", "在电脑上交给 Claude 会话登记")
    else:
        run = run_tests(path, card, conf)
        tested = f"测试通过，日志在 {run / 'test.log'}"
        check_pr(card, rules, main)                   # 测试要几分钟，合并前再读一次
    at("执行合并")
    why = run_owner_merge(card)["why"]
    at("合并后回读")
    try:
        after = gh_read(["pr", "view", str(card["pr"]), "--repo", card["repo"], "--json", "state,mergeCommit"], "合并后回读")
    except Stop as e:
        e.verdict, e.state = UNSURE, "合并命令已经发出，但回读没成功，不知道合上没有"
        e.todo = "隔一分钟{again}：会先查 PR 状态，已经合上会直接说，不会重复合并"
        raise
    if after.get("state") != "MERGED":
        at("执行合并")
        if "Head branch was modified" in why:
            stop = Stop(f"被 GitHub 拒绝：PR 版本变了（{clip(why)}）", "等新卡片再决定")
        else:
            stop = gh_stop(why, other="GitHub 没执行合并")
        stop.state = f"回读确认 PR 还是 {after.get('state')}，{UNCHANGED}"
        raise stop
    record_owner(card, "合并")
    sha = ((after.get("mergeCommit") or {}).get("oid") or "")[:7]
    print(f"已合并 {card_ref(card['repo'], card['pr'], card['head'])}（{card['title']}）→ {sha}。{tested}。")
    print(sync_main(path, main) + "。如果要安装或写 CURRENT，交给 Claude 会话。")
    return 0


def cmd_return(ref, reason):
    card = find_card(ref)
    reason = (reason or "").strip()
    if not reason:
        raise Stop("没写退回原因", "回「退回 原因」")
    at("取 PR 状态")
    p = gh_read(["pr", "view", str(card["pr"]), "--repo", card["repo"], "--json", "state,headRefOid"], "取状态")
    at("核对版本")
    if p.get("state") != "OPEN":
        raise Stop(f"PR 已经是 {p.get('state')}", "不用退回了")
    body = f"常浩在飞书退回（Hermes 代发）：{reason}\n\n针对的版本：`{card['head']}`"
    r = subprocess.run([mo.GH, "pr", "comment", str(card["pr"]), "--repo", card["repo"], "--body", body],
                       capture_output=True, text=True, timeout=mo.TIMEOUT)
    if r.returncode != 0:
        at("留言退回")
        last = ((r.stderr or r.stdout or "").strip().splitlines() or ["没有输出"])[-1]
        stop = gh_stop(last, other="留言没发出去")
        if NET_ERR.search(last):                      # 留言不重试：请求可能已经到了 GitHub
            stop.state = "不确定留言发出去没有"
            stop.todo = "先在 GitHub 的 PR 页面看一眼这条留言在不在，不在再说一次「退回 原因」"
        raise stop
    record_owner(card, "退回")
    print(f"已在 {card['repo']}#{card['pr']} 留言退回：{reason}。PR 没关，等开 PR 的会话改。")
    return 0


def cmd_cards():
    rows = {(r["repo"], r["pr"], r["head_sha"]): r for r in mo.read_rows()}
    left = [c for c in read_cards()["cards"] if not rows.get((c["repo"], str(c["pr"]), c["head"]), {}).get("owner")]
    if not left:
        print("没有等你决定的 PR 卡片。")
    for c in left:
        wait = "  【等你说退回原因】" if c.get("return_asked_at") else ""
        print(f"{card_ref(c['repo'], c['pr'], c['head'])}  {c['tier']} {c['suggestion']}  {c['title']}（{c['sent_at'][:16]}）{wait}")
    return 0


# ---------- 卡片按钮（#78） ----------

def cmd_button(verb, ref, chat):
    """点卡片按钮后由 /card 插件在后台调用。网关回复按钮点击会失败（拿回调 token 当消息号），所以进度和结果都由这里直接发到私聊。
    只认常浩私聊里的点击；同一张卡片同时只跑一个。"""
    if not chat or chat != home_chat():
        print(f"不处理：点击不是来自常浩私聊（{chat or '没有 chat_id'}）")
        return 1
    click = hashlib.sha1(f"{verb} {ref} {dt.datetime.now().isoformat()}".encode()).hexdigest()[:16]
    said = []

    def say(text):                          # lark-cli 的幂等键最长 50 个字符
        send(chat, "text", {"text": text}, f"merge-btn-{click}-{len(said)}")
        said.append(text)

    mo.SHADOW.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]", "_", ref)
    lock = open(mo.SHADOW / f"button-{safe}.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        say(f"{ref} 已经在处理了，等上一次的结果就好。")
        return 1
    try:
        if verb == "return":
            try:
                card = find_card(ref)
            except Stop as e:
                say(fail_text("return", e))
                return 1
            say(f"↩️ 退回 {ref} 的原因是？直接回「退回 原因」就行，不用引用卡片。")
            ask_return(card)                # 问出去了才打标记
            return 0
        say(f"⏳ 在核对 {ref}，稍后告诉你结果。")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["merge", ref])
        say(out.getvalue().strip() or f"没合并：{ref} 没有输出")
        return code
    finally:
        lock.close()


def ask_return(card):
    """记下「常浩点了退回、等他说原因」：merge-officer 技能看到他没引用卡片说「退回 原因」时，用 cards 里这个标记认卡片。"""
    data = read_cards()
    for c in data["cards"]:
        if (c["repo"], str(c["pr"]), c["head"]) == (card["repo"], str(card["pr"]), card["head"]):
            c["return_asked_at"] = mo.now()
    write_cards(data)


def main(argv=None):
    a = argparse.ArgumentParser(description="飞书一句话合并：按常浩对卡片的回复合并或退回 PR")
    sub = a.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cards")
    m = sub.add_parser("merge")
    m.add_argument("ref")
    r = sub.add_parser("return")
    r.add_argument("ref")
    r.add_argument("reason", nargs="+")
    b = sub.add_parser("button")
    b.add_argument("verb", choices=["merge", "return"])
    b.add_argument("ref")
    b.add_argument("--chat", default="")
    a = a.parse_args(argv)
    at("核对版本")
    if a.cmd == "button":
        try:
            return cmd_button(a.verb, a.ref, a.chat)
        except SendError as e:
            print(f"按钮结果没发出去：{e}")
            return 1
    try:
        if a.cmd == "cards":
            return cmd_cards()
        if a.cmd == "merge":
            return cmd_merge(a.ref)
        return cmd_return(a.ref, " ".join(a.reason))
    except Stop as e:
        print(fail_text(a.cmd, e))
        return 1
    except (mo.GhError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        stop = Stop(f"本机出错（{type(e).__name__}：{clip(e)}）", "在电脑上交给 Claude 会话看")
        if stop.step in ("执行合并", "合并后回读"):
            stop.verdict, stop.state = UNSURE, "合并命令可能已经发出"
            stop.todo = "先在 GitHub 看一眼 PR 状态，再交给 Claude 会话看"
        print(fail_text(a.cmd, stop))
        return 1


if __name__ == "__main__":
    sys.exit(main())
