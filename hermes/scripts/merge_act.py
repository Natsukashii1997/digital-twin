#!/usr/bin/env python3
"""合并执行器：唯一的合并入口。两种授权，各走各的命令，合并前的核对是同一套：

1. 卡片合并（#67，owner_direct，单次）：合并官给 PR 发卡片，常浩点卡片上的「合并」「退回」按钮（#78），或引用卡片回「合并」
   「退回 原因」后，由 Hermes 调 merge / return / button。每次都是常浩本人对一张卡片（仓库、PR、完整 HEAD）说了话；
   只认发过的卡片，不认别的 PR 或版本。
2. 按策略合并（#257，ADR-016，owner_approved_policy）：常浩 2026-11-20 定，L0–L2 的 PR 不再逐张等他点。策略记录有效
   （merge_policy.active）时，开 PR 的会话调 auto 交出一个版本，机械检查全过就合；当时还不能合的（CI 在跑等）由合并官
   每轮巡视再试（auto_scan）。例外不合，仍发卡片问常浩：L3、改定方向文档、ask_labels、改动文件没看全（merge_officer.exception_reasons）。
   合并官判「退回」的不拦，理由写进账本。每次合并在 merge_policy 的账本先记「要合」、再记结果；结果不明的下一轮只读核对。
   合并后不拉本机主目录、不安装、不发布（常浩定：装机、发布、部署不放）。撤回用 revert。

不变基、不改代码、不安装、不写 CURRENT。

合并前逐项核对，任一不满足就停下说明（《工程流程》第 5 节、ARCHITECTURE 5.5）：
  1. 仓库在 merge_rules.json 的 act.repos 里；风险档不是 L3（L3 不走飞书）；
  2. PR 还开着、不是草稿、HEAD 仍是卡片上的、base 是主分支、没冲突、不落后（mergeStateStatus 为 CLEAN）；
  3. 本机主目录的主分支没有没推的提交；
  4. 仓库登记了 ci，且这个 HEAD 的 GitHub 检查全部通过（还在跑、没过、一项都没有都停下）；
     主账号不执行 PR 里的代码（#190 不变量 2）：没登记 ci 的仓库不走飞书合并，也不在本机跑测试；
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
  merge_act.py auto <仓库#PR[@HEAD前缀]> [--by 谁]
                                             按策略合并（#257）：交出这个 PR 现在的版本并当场试合。退出码：0 已合并；2 已登记、
                                             现在还不能合（合并官巡视时再试）；3 不归策略管，仍要常浩决定（例外、策略没生效、
                                             仓库没覆盖）；1 其他（结果不明、被上限拦下、出错）
  merge_act.py revert <仓库#PR> [原因]        撤回一个按策略合并的 PR：开撤回 PR，记进账本
环境变量同 merge_officer.py，另有 DT_GH_RETRY_WAITS（只读调用网络瞬断时每次重试前等几秒，逗号分隔，默认 3,5）。

托管合并（#136）只能调 run_custody_merge（#132）：在函数里从本机钥匙串取合并执行器的专用令牌（常浩账号下只对沙盒仓库，
内容、拉取请求读写），放进最小环境（固定 github.com、空的 gh 配置目录、不继承宿主 GH_* / GITHUB_*）；取不到就停，
不退回常浩本人的 gh 登录；走 REST 合并接口，以接口返回的本次合并提交为成功证据；返回 merged / failed / unsure，unsure 只做只读对账、不重发。卡片合并（B 档）走 run_owner_merge，用本机 gh 登录。
托管合并被常浩推翻（#133）后，run_custody_revert 用同一个凭据开 revert PR，只开不合；本机 git 配置隔离，凭据只经 gh 的 git-credential 取。
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
import merge_policy as MP
from tidings_weekly import SendError, send

CARDS = mo.SHADOW / "cards.json"
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
    """卡片合并 / 退回成功后写 owner 列。和合并官巡视、按策略合并用同一把锁，免得互相覆盖 agreement.csv。"""
    try:
        with MP.locked():
            _mark_owner(card["repo"], card["pr"], card["head"], decision)
    except MP.Rejected:                      # 合并官巡视占着锁太久：合并 / 退回已经做完，记录不能因此丢，照旧直接写
        _mark_owner(card["repo"], card["pr"], card["head"], decision)


def _mark_owner(repo, pr, head, decision, f=None, rules=None):
    """调用方持合并锁。这个版本还没判定过（会话在合并官巡视之前就交出了）而且给了 f，就补一行判定。"""
    rows = mo.read_rows()
    key = (repo, str(pr), head)
    hit = [r for r in rows if (r["repo"], r["pr"], r["head_sha"]) == key]
    if not hit and f is not None:
        hit = [mo.new_row(f, rules, "live", mo.now())]
        rows += hit
        mo.append_facts([f])
    for r in hit:
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
    res = {"returncode": None, "timed_out": False, "launched": True, "why": "", "detail": "", "stdout": ""}
    try:
        mr = subprocess.run(cmd, capture_output=True, text=True, timeout=mo.TIMEOUT, env=env)
        res["returncode"], res["stdout"] = mr.returncode, mr.stdout or ""
        res["detail"] = (mr.stderr or mr.stdout or "").strip()      # 完整报错，分类用；why 只给人看
        res["why"] = (res["detail"].splitlines() or ["没有输出"])[-1]
    except subprocess.TimeoutExpired:
        res["timed_out"] = True
        res["why"] = f"超过 {mo.TIMEOUT} 秒没回应（timeout）"
    except OSError as e:                              # gh 没起来（卸载、链接失效、没执行权限）：一个请求都没发出去
        res["launched"] = False
        res["why"] = f"gh 跑不起来（{type(e).__name__}）"
    if secret:
        for k in ("why", "detail", "stdout"):
            res[k] = res[k].replace(secret, "***")
    return res


def run_owner_merge(card):
    """卡片合并（B 档，常浩本人点的）：用本机 gh 登录。托管合并不能走这里，用 run_custody_merge。"""
    cmd = [mo.GH, "pr", "merge", str(card["pr"]), "--repo", card["repo"], "--squash", "--match-head-commit", card["head"]]
    res = _gh_write(cmd, None)
    res.pop("stdout"), res.pop("detail")
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
    detail = res.pop("detail")
    out.update(res)
    if not res["launched"]:
        out["status"] = "failed"                      # 合并请求没发出去，确定没合
        return out
    state = (after or {}).get("state")
    out["merge_commit"] = ((after or {}).get("mergeCommit") or {}).get("oid")
    rejected = res["returncode"] not in (None, 0) and REJECTED.search(detail) and not NET_ERR.search(detail)  # 网络错误优先
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


GIT = "git"
GIT_URL = "https://github.com/{repo}.git"      # 测试换成本地裸仓库
REVERT_BRANCH = "custody-revert/{decision}"


def _git_env(env, home):
    """托管回退里 git 的环境：在合并执行器最小环境上再隔离本机 git 配置（不读 ~/.gitconfig、系统配置和钥匙串助手），
    凭据只经 gh 的 git-credential 从 GH_TOKEN 取，不进命令行参数、不写进仓库配置；不交互。"""
    return {**env, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/usr/bin/false"}


def same_repo(p, repo):
    """PR 的来源分支就在目标仓库本身（不是从 fork 用同名分支开过来的，#133 定向复核 P1-01）。
    p 要带 gh 的 isCrossRepository、headRepository、headRepositoryOwner 字段。"""
    owner, _, name = repo.partition("/")
    return (p.get("isCrossRepository") is False
            and str((p.get("headRepositoryOwner") or {}).get("login") or "").lower() == owner.lower()
            and str((p.get("headRepository") or {}).get("name") or "").lower() == name.lower())


REVERT_PR_FIELDS = "number,url,headRefOid,state,baseRefName,isCrossRepository,headRepository,headRepositoryOwner"


def run_custody_revert(repo, merge_commit, decision, title, on_head=None):
    """托管合并被常浩推翻后开 revert PR（#133，规格 §3.6「已回退」）：只开 PR、不合并（revert PR 的合并仍由常浩点卡片，B 档）。
    凭据同 run_custody_merge：函数里从钥匙串取合并执行器令牌，最小环境，不退回本机 gh 登录。
    步骤：查令牌对应的账号（提交署名用它的 noreply 邮箱）→ 浅克隆 → 核对合并提交在 main 上 → 从 main 开分支
    custody-revert/<决定号> → git revert（合并提交有两个父提交时 -m 1）→ on_head(本地撤销提交号) → 推分支 → gh pr create → 回读 PR。
    on_head 由调用方在推送之前把完整提交号落盘（#133 定向复核 P1-01）：中断后只认 HEAD 正好是这个提交号、来源就是目标仓库的 PR；
    on_head 抛异常就停在推送之前（failed），所以「没记下提交号」一定等于「没推过」。
    返回 {"status": opened | failed | unsure, "why", "pr", "url", "head", "branch"}：
    failed＝确定没在 GitHub 上留下东西（推之前就停了）；unsure＝推了分支或建 PR 之后没拿到确定结果，要人工看；
    revert 有冲突就是 failed（转常浩处理，不自动解决冲突）。"""
    token = custody_token(repo)
    branch = REVERT_BRANCH.format(decision=decision)
    out = {"status": "failed", "why": "", "pr": None, "url": None, "head": None, "branch": branch, "repo": repo}
    with tempfile.TemporaryDirectory(prefix="dt-custody-gh-") as cfg, tempfile.TemporaryDirectory(prefix="dt-custody-revert-") as work:
        env = custody_env(token, cfg)
        genv = _git_env(env, work)
        cred = ["-c", "credential.helper=", "-c", f"credential.helper=!{mo.GH} auth git-credential"]

        def run(cmd, e, cwd=None):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=mo.TIMEOUT, env=e, cwd=cwd)
                return r.returncode, (r.stdout or "").replace(token, "***"), (r.stderr or "").replace(token, "***")
            except (OSError, subprocess.SubprocessError) as x:
                return None, "", type(x).__name__

        def stop(why, status="failed"):
            out.update(status=status, why=why)
            return out

        code, so, se = run([mo.GH, "api", "user", "--jq", ".login + \" \" + (.id|tostring)"], env)
        if code != 0 or len(so.split()) != 2:
            return stop(f"查不到合并执行器令牌对应的账号（{clip(se.strip() or so.strip() or '没有输出', 100)}）")
        login, uid = so.split()
        ident = ["-c", f"user.name={login}", "-c", f"user.email={uid}+{login}@users.noreply.github.com"]
        dest = str(Path(work) / "repo")
        code, _, se = run([GIT, *cred, "clone", "--quiet", "--no-tags", "--branch", "main", GIT_URL.format(repo=repo), dest], genv)
        if code != 0:
            return stop(f"克隆 {repo} 失败（{clip(se.strip(), 100)}）")
        code, _, _ = run([GIT, "merge-base", "--is-ancestor", merge_commit, "origin/main"], genv, dest)
        if code != 0:
            return stop(f"合并提交 {merge_commit[:7]} 不在 main 上，不能回退")
        code, so, _ = run([GIT, "rev-list", "--parents", "-n", "1", merge_commit], genv, dest)
        parents = so.split()[1:]
        mainline = ["-m", "1"] if len(parents) > 1 else []
        code, _, se = run([GIT, "checkout", "--quiet", "-b", branch, "origin/main"], genv, dest)
        if code != 0:
            return stop(f"开分支失败（{clip(se.strip(), 100)}）")
        code, _, se = run([GIT, *ident, "revert", "--no-edit", *mainline, merge_commit], genv, dest)
        if code != 0:
            run([GIT, "revert", "--abort"], genv, dest)
            return stop(f"git revert 有冲突或失败，没推任何东西（{clip(se.strip(), 100)}）")
        code, head, _ = run([GIT, "rev-parse", "HEAD"], genv, dest)
        out["head"] = head.strip()
        if code != 0 or not re.fullmatch(r"[0-9a-f]{40}", out["head"]):
            return stop("取不到撤销提交号，没推任何东西")
        if on_head:
            try:
                on_head(out["head"])
            except Exception as x:  # noqa: BLE001  记不下就不推：中断后要靠它认领
                return stop(f"记不下撤销提交号，没推任何东西（{type(x).__name__}）")
        code, _, se = run([GIT, *cred, "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}"], genv, dest)
        if code != 0:
            return stop(f"推分支 {branch} 没拿到确定结果，要到 GitHub 上看（{clip(se.strip(), 100)}）", "unsure")
        body = (f"托管决定 {decision} 被常浩推翻，回退合并提交 {merge_commit}。\n\n"
                "本 PR 由合并执行器开出；合并仍由常浩在飞书卡片上点（B 档），托管不会自己合它。")
        run([mo.GH, "pr", "create", "--repo", repo, "--base", "main", "--head", branch, "--title", title, "--body", body], env)
        code, so, _ = run([mo.GH, "pr", "view", branch, "--repo", repo, "--json", REVERT_PR_FIELDS], env)
        got = _json_dict(so) if code == 0 else None
        if (not got or got.get("headRefOid") != out["head"] or got.get("state") != "OPEN" or got.get("baseRefName") != "main"
                or not same_repo(got, repo)):
            return stop(f"分支已推（{branch}），但回读不到对得上的 revert PR，要到 GitHub 上看", "unsure")
        out.update(status="opened", pr=got["number"], url=got["url"])
    return out


def require_named_checks(p, names):
    """撤销 PR 的有效测试证据（#133 第 2 轮审查 P1-03）：require_ci_passed 把 SKIPPED / NEUTRAL 当作不阻断，全是跳过也能过；
    撤销 PR 另外要求 merge_rules.json 的 revert_required_checks[完整仓库名] 里登记的每项检查，在当前 HEAD 上真的跑完并且 SUCCESS。
    没登记就停（不知道哪项是测试，就没有测试通过的证据）。飞书卡片合并不走这里，现有规则不变。"""
    at("跑测试")
    if not names:
        raise Stop("这个仓库没登记撤销 PR 必须通过的测试检查（merge_rules.json 的 revert_required_checks）",
                   "在电脑上交给 Claude 会话：给这个仓库配好 CI 并登记检查名")
    got = {}
    for c in p.get("statusCheckRollup") or []:
        got.setdefault(c.get("name") or c.get("context"), []).append(
            (str(c.get("status") or "COMPLETED").upper(), str(c.get("conclusion") or c.get("state") or "").upper()))
    bad = [n for n in names if not got.get(n) or any(s != "COMPLETED" or r != "SUCCESS" for s, r in got[n])]
    if bad:
        raise Stop("必须通过的测试检查没有成功跑完：" + "、".join(bad), "交回开这个 PR 的会话看 CI")
    at("核对版本")


def merge_revert_as_owner(repo, pr, head):
    """撤销托管合并的 PR（#133 第 1 轮审查 P0-01）：常浩点「合并撤销 PR」后，走和飞书卡片合并同一套合并前检查（check_pr：
    PR 开着、HEAD 是开出来时的、合进 main、不是草稿、风险档不是 L3、没冲突不落后、当前 HEAD 的 CI 全部通过、CLEAN），
    任一项不过就停（Stop），不合并、转电脑上处理。撤销 PR 没有 CI 也停：沙盒没登记本机测试命令，不在宿主跑 PR 里的脚本；
    另外要求 revert_required_checks 里登记的测试检查真的 SUCCESS（require_named_checks，跳过 / 中立不算）。
    通过后用本机 gh（常浩本人，B 档）带 --match-head-commit 合并，再回读。返回 {"status", "merge_commit", "why"}：
    merged＝本次合并命令成功且回读已合并；merged_unattributed＝回读已合并但本次命令没成功（可能被别人合上），不归到这次按钮；
    failed＝回读仍没合并。"""
    card = {"repo": repo, "pr": pr, "head": head}
    rules = mo.load_rules()
    p = check_pr(card, rules, "main", ci=True)
    require_named_checks(p, (rules.get("revert_required_checks") or {}).get(repo))
    at("执行合并")
    res = run_owner_merge(card)
    at("合并后回读")
    try:
        after = gh_read(["pr", "view", str(pr), "--repo", repo, "--json", "state,mergeCommit"], "合并后回读")
    except Stop as e:
        e.verdict, e.state = UNSURE, "合并命令已经发出，但回读没成功，不知道合上没有"
        e.todo = "到 GitHub 上看这个 PR 合上没有，不要再点合并"
        raise
    sha = (after.get("mergeCommit") or {}).get("oid")
    if after.get("state") == "MERGED":
        return {"status": "merged" if res["returncode"] == 0 else "merged_unattributed", "merge_commit": sha, "why": res["why"]}
    return {"status": "failed", "merge_commit": None, "why": res["why"]}


def cmd_merge(ref):
    card = find_card(ref)
    rules = mo.load_rules()
    conf = act_repos(rules).get(card["repo"].split("/")[-1])
    if not conf:
        raise Stop("这个仓库还没开飞书合并（merge_rules.json 的 act.repos）", "在电脑上交给 Claude 会话处理")
    path, main = project_of(card["repo"].split("/")[-1])
    if not conf.get("ci"):
        at("跑测试")
        raise Stop("这个仓库没有 CI；主账号不执行 PR 里的代码，飞书合并不在本机跑测试",
                   "先给这个仓库配 GitHub CI，再在 merge_rules.json 的 act.repos 里登记 ci: true")
    check_pr(card, rules, main, ci=True)
    git(path, "fetch", "-q", "origin")
    ahead = git(path, "rev-list", "--count", f"origin/{main}..{main}")
    if ahead != "0":
        raise Stop(f"本机主目录的 {main} 有 {ahead} 个没推的提交", "按《工程流程》第 5 节在电脑上决定推不推，处理完{again}")
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
    print(f"已合并 {card_ref(card['repo'], card['pr'], card['head'])}（{card['title']}）→ {sha}。CI 通过。")
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


# ---------- 按策略合并（#257，ADR-016） ----------

AUTO_REF = re.compile(r"^([\w.-]+)#(\d+)(?:@([0-9a-f]{7,40}))?$")
PULL_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/(\d+)")
AUTO_EXIT = {"merged": 0, "waiting": 2, "exception": 3, "off": 3, "uncovered": 3}     # 其余退出码 1


def repo_of(name):
    for p in json.loads(mo.ROSTER.read_text(encoding="utf-8"))["projects"]:
        if (p.get("github") or "").split("/")[-1] == name:
            return p["github"]
    raise Stop(f"名册里没有 {name}", "在电脑上交给 Claude 会话登记名册")


def _res(status, text, **kw):
    return {"status": status, "text": text, **kw}


def auto_try(repo, pr, by, rules, want=None, register=True):
    """按策略合并一个 PR（调用方持 MP.locked()）。返回 {"status", "text", …}，status：
    off 策略没生效；uncovered 仓库不在 act.repos 或没登记 CI；exception 例外，仍问常浩；frozen 上一次结果不明还没核对；
    waiting 机械检查现在没过（已登记交出的，合并官每轮再试）；capped 今天到上限；merged；failed 回读确认没合上；
    unsure 合并命令发了、回读没成功（下一轮只读核对，不重发）。
    want：交出时的 HEAD（前缀也行），PR 现在不是这个版本就不合；register：把现在的版本登记为交出（auto 命令用）。
    判定、交出、核对、合并命令（--match-head-commit）绑的是同一个 HEAD。"""
    name = repo.split("/")[-1]
    ref = f"{name}#{pr}"
    on, why, sig = MP.active(rules)
    if not on:
        return _res("off", f"{ref} 没合并：{why}。这个 PR 仍等常浩说合并。")
    if not (act_repos(rules).get(name) or {}).get("ci"):
        return _res("uncovered", f"{ref} 没合并：这个仓库不在合并执行器覆盖范围里（merge_rules.json 的 act.repos，要登记了 CI）。仍等常浩说合并。")
    if MP.unsettled(repo, pr):
        return _res("frozen", f"{ref} 没合并：上一次按策略合并结果不明，还没核对清楚。合并官下一轮巡视会只读核对，不要另外合并。")
    path, main = project_of(name)
    at("取 PR 状态")
    p = gh_read(["pr", "view", str(pr), "--repo", repo, "--json", VIEW_FIELDS], f"{ref} 取状态")
    f = mo.facts_of(repo, p)
    head = f["head_sha"]
    at("核对版本")
    bad = mo.malformed(f)
    if bad:
        raise Stop("PR 状态不对：" + "；".join(bad), "")
    if want and not head.startswith(want):
        raise Stop(f"PR 现在的版本是 {head[:7]}，不是交出的 {want[:7]}（之后又有提交）", "")
    if f["files_total"] is None:
        raise Stop("GitHub 没给出改动文件总数，核对不了改动文件有没有看全", "")
    tier, sug, reasons = mo.evaluate(f, rules)
    exc = mo.exception_reasons(f, rules)
    if exc:
        return _res("exception", f"{ref} 属于例外，不按策略合并，仍要常浩决定：{'；'.join(exc)}。"
                                 + ("L3 不走飞书合并，在电脑上处理。" if tier == "L3" else "合并官巡视时会发卡片给常浩。"),
                    tier=tier, reasons=exc)
    ready = MP.add_handoff(repo, pr, head, by) if register else \
        next((h for h in MP.handoffs() if (h["repo"], str(h["pr"]), h["head"]) == (repo, str(pr), head)), None)
    card = {"repo": repo, "pr": str(pr), "head": head, "title": f["title"]}
    try:
        p = check_pr(card, rules, main, ci=True)
        git(path, "fetch", "-q", "origin")
        ahead = git(path, "rev-list", "--count", f"origin/{main}..{main}")
        if ahead != "0":
            raise Stop(f"本机主目录的 {main} 有 {ahead} 个没推的提交", "")
    except Stop as e:
        return _res("waiting", f"{ref}@{head[:7]} 现在还不能合：在『{e.step}』这一步{e.why}。"
                               + ("已登记交出，合并官每轮巡视会再试，条件满足就合；PR 有新提交要重新交出。" if ready else ""),
                    step=e.step, why=e.why)
    conf = MP.auto_conf(rules)
    cap, day = conf.get("daily_cap"), mo.now()[:10]
    if cap is not None and MP.merged_count(day) >= cap:
        return _res("capped", f"{ref} 没合并：今天按策略已经合了 {MP.merged_count(day)} 个，到上限 {cap} 了。"
                              "明天自动继续；要今天合，常浩说一声。")
    on, why, sig = MP.active(rules)                         # 核对花了时间：发合并命令前再读一次开关（关不等合并锁）
    if not on:
        return _res("off", f"{ref} 没合并：{why}。这个 PR 仍等常浩说合并。")
    mid = "m-" + re.sub(r"\D", "", mo.now())[:14] + "-" + os.urandom(3).hex()
    MP.append({"event": "intent", "id": mid, "repo": repo, "pr": str(pr), "title": f["title"], "url": p.get("url"),
               "head": head, "base": f["base_sha"], "tier": tier, "rules_version": rules["version"],
               "suggestion": sug, "reasons": "；".join(reasons),
               "ready": {"mode": conf.get("ready"), "by": (ready or {}).get("by") or by, "at": (ready or {}).get("at")},
               "auth": {"type": MP.POLICY_TYPE, "scope_sha256": sig["scope_sha256"], "message_id": sig["message_id"],
                        "signed_at": sig["signed_at"]},
               "checks": sorted({c.get("name") or c.get("context") or "?" for c in p.get("statusCheckRollup") or []}),
               "review": "未核实（第一版不核对审查结论，ADR-016）", "identity": "owner-gh"})
    at("执行合并")
    r = run_owner_merge(card)
    at("合并后回读")
    try:
        after = gh_read(["pr", "view", str(pr), "--repo", repo, "--json", "state,mergeCommit,headRefOid"], "合并后回读")
    except Stop as e:
        MP.append({"event": "unsure", "id": mid, "why": f"合并命令：{clip(r['why'])}；回读：{clip(e.why)}"})
        return _res("unsure", f"{ref}@{head[:7]} 结果不明：合并命令已经发出，但回读没成功（{clip(e.why)}）。"
                              "合并官下一轮巡视会只读核对，不会重发；这期间不要另外合并。", id=mid)
    return _settle(mid, repo, pr, head, after, f, rules, r)


def _settle(mid, repo, pr, head, after, f=None, rules=None, r=None, late=False):
    """按回读结果给一条 intent 记结果：已合并且版本对得上 → merged；否则 failed。late：这是事后的只读核对。"""
    ref = f"{repo.split('/')[-1]}#{pr}"
    if after.get("state") == "MERGED" and after.get("headRefOid") == head:
        sha = (after.get("mergeCommit") or {}).get("oid") or ""
        MP.append({"event": "merged", "id": mid, "merge_commit": sha, "late": late,
                   "by_this_call": bool(r and r["returncode"] == 0)})
        MP.drop_handoff(repo, pr)
        _mark_owner(repo, pr, head, mo.POLICY_OWNER, f, rules)
        return _res("merged", f"已按策略合并 {ref}@{head[:7]} → {sha[:7]}（记录 {mid}）。没拉本机主目录、没安装；"
                              f"要安装、发布或写 CURRENT，仍先问常浩。撤回：merge_act.py revert {ref}", id=mid, merge_commit=sha)
    why = clip(r["why"]) if r else f"只读核对：PR 现在是 {after.get('state')}，版本 {str(after.get('headRefOid'))[:7]}"
    MP.append({"event": "failed", "id": mid, "why": why, "late": late})
    if after.get("state") == "MERGED":                      # 合并了，但不是这次要合的版本：别处合的，不算这次的
        return _res("failed", f"{ref}@{head[:7]} 不是这次合上的：{why}。PR 现在已合并，版本是 "
                              f"{str(after.get('headRefOid'))[:7]}，不是交出的那个；到 GitHub 上看一眼是谁合的。", id=mid)
    return _res("failed", f"{ref}@{head[:7]} 没合上：{why}。回读确认 PR 是 {after.get('state')}，没有被改动。", id=mid)


def reconcile_unsettled():
    """上次结果不明的合并：只读核对，不重发（调用方持 MP.locked()）。和策略现在开没开无关：合并命令已经发过，
    关了开关也要核对清楚，不然这条一直挂着。返回要常浩知道的话。"""
    said = []
    for m in MP.unsettled():
        try:
            after = gh_read(["pr", "view", str(m["pr"]), "--repo", m["repo"], "--json", "state,mergeCommit,headRefOid"],
                            "只读核对")
        except Stop:
            continue                                       # 这一轮读不到，下一轮再核对
        said.append("只读核对：" + _settle(m["id"], m["repo"], m["pr"], m["head"], after, late=True)["text"])
    return said


def auto_scan(rules, open_now, settled, listed):
    """合并官每轮巡视时调（调用方持 MP.locked()，策略已确认生效）。open_now：这一轮读到的 open PR 状态；
    settled：这一轮刚有了结局的判定行；listed：这一轮成功列出 open PR 的仓库。返回要常浩知道的话（没有就是空列表）：
    结果不明、只读核对的结论、到上限；合成功的默认不说，进日报（auto.notify 为 each 时每个都说）。"""
    conf = MP.auto_conf(rules)
    covered = lambda repo: bool((act_repos(rules).get(repo.split("/")[-1]) or {}).get("ci"))  # noqa: E731
    said = reconcile_unsettled()                           # 1. 上次结果不明的：只读核对，不重发
    done = MP.bypassed()                                   # 2. 已合并却没经合并执行器的：留一条记录
    for r in settled:
        key = (r["repo"], r["pr"])
        if (r["outcome"] == mo.MERGED and covered(r["repo"]) and not r.get("owner") and key not in done
                and not MP.find_merged(*key)):
            MP.append({"event": "bypass", "repo": r["repo"], "pr": r["pr"], "title": r["title"], "head": r["outcome_head"],
                       "tier": r["tier"], "suggestion": r["suggestion"], "merged_at": r["outcome_at"],
                       "note": "PR 已合并，账本里没有合并执行器的记录（会话或网页上直接合的）"})
            done.add(key)
    now_open = {(f["repo"], str(f["pr"])): f for f in open_now}
    hand = {}
    for h in list(MP.handoffs()):                          # 3. 交出登记：PR 关了、合了或有了新提交就作废
        f = now_open.get((h["repo"], str(h["pr"])))
        if h["repo"] in listed and (f is None or f["head_sha"] != h["head"]):
            MP.drop_handoff(h["repo"], h["pr"])
        else:
            hand[(h["repo"], str(h["pr"]))] = h
    for f in open_now:                                     # 4. 能合的合
        key = (f["repo"], str(f["pr"]))
        h = hand.get(key)
        if not covered(f["repo"]) or (conf.get("ready") != "any" and (h is None or h["head"] != f["head_sha"])):
            continue
        checks = f.get("checks") or []
        if (f.get("draft") or f.get("mergeable") == "CONFLICTING" or not checks or any(c not in CHECK_OK for c in checks)
                or mo.exception_reasons(f, rules)):
            continue                                       # 列表里就看得出现在不能合的，不再逐个去读
        try:
            res = auto_try(f["repo"], f["pr"], (h or {}).get("by") or "合并官巡视", rules, want=f["head_sha"], register=False)
        except Stop:
            continue                                       # 合并前的读取或核对没过（网络、版本刚变）：下一轮再试
        if res["status"] == "unsure" or (res["status"] == "merged" and conf.get("notify") == "each"):
            said.append(res["text"])
        elif res["status"] == "capped":
            if MP.note_once("capped", mo.now()[:10]):
                said.append(res["text"])
            break
    return said


def cmd_auto(ref, by):
    m = AUTO_REF.match(ref.strip())
    if not m:
        raise Stop(f"看不懂「{ref}」（要写成 仓库#PR，或 仓库#PR@版本，例如 my-skills#121@1a2b3c4）", "")
    name, pr, want = m.groups()
    repo = repo_of(name)
    with MP.locked():
        res = auto_try(repo, pr, by, mo.load_rules(), want=want, register=True)
    print(res["text"])
    return AUTO_EXIT.get(res["status"], 1)


def cmd_revert(ref, reason):
    """撤回一个按策略合并的 PR：只认账本里合并执行器按策略合过的；用 gh pr revert 开撤回 PR（不重试），记进账本。
    auto.revert_auto_merge 开着、策略有效时，撤回 PR 接着按同一套检查合并（它自己的 CI 过了才合）。"""
    m = AUTO_REF.match(ref.strip())
    if not m:
        raise Stop(f"看不懂「{ref}」（要写成 仓库#PR，例如 my-skills#121）", "")
    name, pr, _ = m.groups()
    repo = repo_of(name)
    rules = mo.load_rules()
    with MP.locked():
        hit = MP.find_merged(repo, pr)
        if not hit:
            raise Stop(f"账本里没有按策略合并过 {name}#{pr}（只撤回合并执行器按策略合的）", "")
        if hit.get("revert"):
            print(f"{name}#{pr} 已经开过撤回 PR：{hit['revert'].get('revert_url')}（{hit['revert']['at'][:16]}）。")
            return 0
        reason = (reason or "").strip() or "常浩要求撤回"
        body = (f"撤回按策略合并的 {repo}#{pr}（合并提交 `{hit.get('merge_commit')}`，记录 {hit['id']}）。\n\n"
                f"原因：{reason}\n\n由合并执行器开出（merge_act.py revert，#257）。")
        at("开撤回 PR")
        r = _gh_write([mo.GH, "pr", "revert", str(pr), "--repo", repo, "--body", body], None)
        url = PULL_URL.search(r["stdout"] or "")
        if r["returncode"] != 0 or not url:
            stop = gh_stop(r["why"], other="撤回 PR 没开出来")
            stop.state = "不确定撤回 PR 开出来没有" if (r["timed_out"] or NET_ERR.search(r["why"])) else "没有开出撤回 PR"
            raise stop
        MP.append({"event": "revert_opened", "id": hit["id"], "repo": repo, "pr": str(pr), "revert_pr": url.group(1),
                   "revert_url": url.group(0), "reason": reason})
        print(f"已开撤回 PR：{url.group(0)}（撤回 {name}#{pr}，原因：{reason}）。已经装机的改动撤回后要不要重装，仍先问常浩。")
        if MP.auto_conf(rules).get("revert_auto_merge") and MP.active(rules)[0]:
            try:
                print(auto_try(repo, url.group(1), f"撤回 {name}#{pr}", rules, register=True)["text"])
            except Stop as e:                              # 撤回 PR 已经开出、记了账；只是接着合并这一步没做成
                print(f"撤回 PR 还没合并：在『{e.step}』这一步{e.why}。过一会儿对它再交出一次："
                      f"merge_act.py auto {name}#{url.group(1)}，或等常浩说合并。")
        else:
            print("撤回 PR 没有自动合并，等常浩说合并。")
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
    u = sub.add_parser("auto")
    u.add_argument("ref")
    u.add_argument("--by", default="会话")
    v = sub.add_parser("revert")
    v.add_argument("ref")
    v.add_argument("reason", nargs="*")
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
        if a.cmd == "auto":
            return cmd_auto(a.ref, a.by)
        if a.cmd == "revert":
            return cmd_revert(a.ref, " ".join(a.reason))
        return cmd_return(a.ref, " ".join(a.reason))
    except Stop as e:
        if a.cmd in ("auto", "revert"):                  # 这两个不是从卡片来的，不套「再点一次」的说法
            print(f"{'没合并' if a.cmd == 'auto' else '没撤回'}：在『{e.step}』这一步{e.why}。{e.state}。")
            return 1
        print(fail_text(a.cmd, e))
        return 1
    except MP.Rejected as e:
        print(f"没办成：{e}")
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
