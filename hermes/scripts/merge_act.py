#!/usr/bin/env python3
"""合并执行器：唯一的合并入口。两种授权，各走各的命令，合并前的核对是同一套：

1. 卡片合并（#67，owner_direct，单次）：合并官给 PR 发卡片，常浩点卡片上的「合并」「退回」按钮（#78），或引用卡片回「合并」
   「退回 原因」后，由 Hermes 调 merge / return / button。每次都是常浩本人对一张卡片（仓库、PR、完整 HEAD）说了话；
   只认发过的卡片，不认别的 PR 或版本。
2. 按策略合并（#257，ADR-016，owner_approved_policy）：常浩 2026-11-20 定，L0–L2 的 PR 不再逐张等他点。策略记录有效
   （merge_policy.active）时，开 PR 的会话调 auto 交出一个版本，机械检查全过就合；当时还不能合的（CI 在跑等）由合并官
   每轮巡视再试（auto_scan）。例外不合，仍发卡片问常浩：L3、改定方向文档、ask_labels、改动文件没看全、来自 fork 的 PR（merge_officer.exception_reasons）。
   合并官判「退回」的不拦，理由写进账本。每次合并在 merge_policy 的账本先记「要合」、再记结果；合并走 REST 合并接口，
   只有接口回执说这次合上了才记成按策略合并（_merge_verdict，和托管合并同一套证据标准）；结果不明的不重发，巡视时只读核对。
   合并请求由独立的看门进程起、自带硬期限（发起它的进程死了也到点收场）。结果不明、PR 还开着且版本没变的，没有
   「GitHub 那头不会再处理这次请求」的凭据，所以一直挂着不结算、不放开重新交出：PR 换了版本（旧请求绑的是旧版本）
   或关掉了，且本机没有这次请求的进程，才记没合上（reconcile_unsettled，#260-P1-01）。脚本不停任何进程（#260-P2-14）。
   合并后不拉本机主目录、不安装、不发布（常浩定：装机、发布、部署不放）。
   撤回用 revert：撤回 PR 只自动合并开出当时核对过并记下的那个版本；核对到内容：撤回提交的树必须正好等于
   「把原合并提交倒回去」的结果，只取 Git 对象、不检出、不运行（_revert_content_why，#260-P1-07）。

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
                                             按策略合并（#257）：交出这个 PR 现在的版本并当场试合（这个 PR 上一次按策略合并
                                             结果不明、刚被核对结清时，必须写 @版本前缀）。退出码：0 已合并；2 已登记、
                                             现在还不能合（合并官巡视时再试）；3 不归策略管，仍要常浩决定（例外、策略没生效、
                                             仓库没覆盖）；1 其他（结果不明、被上限拦下、出错）
  merge_act.py revert <仓库#PR> [原因]        撤回一个按策略合并的 PR：开撤回 PR，记进账本
环境变量同 merge_officer.py，另有 DT_GH_RETRY_WAITS（只读调用网络瞬断时每次重试前等几秒，逗号分隔，默认 3,5）、
DT_MERGE_REQ_TIMEOUT（按策略合并、开撤回 PR 的请求最多活几秒，默认 60）、DT_PGREP / DT_PS（查进程表用的 pgrep、ps，自测用）。

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
import signal
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
                     r"could not resolve host|temporary failure in name resolution|dial tcp: lookup|"
                     r"client connection lost|connection refused|GOAWAY|stream error", re.I)
RATE_ERR = re.compile(r"rate limit", re.I)
AUTH_ERR = re.compile(r"\b401\b|bad credentials|gh auth login|not logged in|authentication", re.I)
DENY_ERR = re.compile(r"\b403\b|permission|not accessible|forbidden", re.I)
GONE_ERR = re.compile(r"no pull requests found|could not resolve to a pullrequest|\b404\b|not found", re.I)
UNCHANGED = "PR 没有任何改动"
STEP = ["核对版本"]                                    # 现在做到哪一步，失败提示开头说卡在这里
UNSURE = "不确定合上没有"
# GitHub 给的 HTTP 状态码只认 gh api 的「(HTTP 4xx)」写法：裸数字会认错，传输出错时 gh 的报错里带着请求地址，
# PR 号正好是 409、429 这类数字就会被当成拒绝（#260 送审前自审）。
HTTP_4XX = re.compile(r"\(HTTP (4\d\d)\)")
REJECTED = re.compile(r"protected ref|rule violations|not mergeable|base branch policy|Head branch was modified|"
                      r"Base branch was modified|rate limit|\(HTTP 4\d\d\)|bad credentials|not accessible|"
                      r"permission", re.I)                     # GitHub 明确拒绝合并（限流也是：这次请求没被处理）
# 明确拒绝里，过一阵可能自己好的只有这几类（限流、版本或主分支刚变、另一个合并正在进行）：下一轮巡视再试。
# 其余一律当成等多久都不会自己好（登录、权限、要人审批、分支规则、校验失败……）：当天不再对这个版本发请求（#260-P2-13）。
# 反过来列是有意的：漏认一种临时拒绝只是当天少试几次；漏认一种永久拒绝是每半小时撞一次。
TRANSIENT = re.compile(r"rate limit|\(HTTP 429\)|secondary|abuse|Head branch was modified|Base branch was modified|"
                       r"\(HTTP 409\)|merge already in progress", re.I)
SHA40 = re.compile(r"^[0-9a-f]{40}$")
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


def update_cards(fn):
    """cards.json 的读改写只走这里（#260-P2-11）：拿它自己的锁 cards.lock（不挂在合并锁上：按钮点击和巡视各有各的
    生命周期，锁只在读改写这一下持有）→ 读 → fn(data) 改 → 写到带进程号的临时文件 → 换名。巡视发卡片（send_cards）和
    退回按钮（ask_return）都是写入者，各改各的那一条，不会互相盖掉。"""
    mo.SHADOW.mkdir(parents=True, exist_ok=True)
    with open(mo.SHADOW / "cards.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_cards()
        fn(data)
        tmp = CARDS.with_name(f".{CARDS.name}.tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, CARDS)
    return data


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
    sent = {(c["repo"], str(c["pr"]), c["head"]) for c in read_cards()["cards"]}
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
        card = {"message_id": mid, "repo": r["repo"], "pr": r["pr"], "head": r["head_sha"],
                "tier": r["tier"], "suggestion": r["suggestion"], "title": r["title"], "sent_at": mo.now()}
        update_cards(lambda d: d["cards"].append(card))      # 发一张记一张；发消息不在锁里
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

def with_merge_lock(fn, *args):
    """卡片合并 / 退回也拿合并锁（审查 #260-P2-01、P2-02）：同一个 PR 不会被卡片和按策略合并同时发写请求，
    agreement.csv 也只在锁里改。等不到锁就什么都不做、请常浩过会儿再点：不再有「等不到就绕过锁直接写」。"""
    lock = MP.locked()
    try:
        lock.__enter__()
    except MP.Rejected:
        raise Stop("合并官正在巡视，或另一个合并正在跑（等了 3 分钟没等到）", "过几分钟{again}")
    try:
        return fn(*args)
    finally:
        lock.__exit__(None, None, None)


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


REQ_TIMEOUT = float(os.environ.get("DT_MERGE_REQ_TIMEOUT") or mo.TIMEOUT)     # 按策略合并、开撤回 PR 的请求最多活几秒
PGREP = os.environ.get("DT_PGREP", "pgrep")
PS = os.environ.get("DT_PS", "ps")
# 看门进程（#260-P1-01）：请求的硬期限不能靠发起它的进程。发起方被杀掉之后，子进程不会跟着死；所以 gh 由这个独立的
# 小进程起，它自己计时，到点杀掉 gh 再退出（退出码 124），和发起方死活无关。gh 起不来退出码 127。
# 收到 TERM / HUP / INT 时先杀 gh 再退；被 KILL 掉拦不住，那种情况巡视时查进程表（live_requests）会看到，告诉常浩，脚本不去停它。
WATCHDOG = r"""
import signal, subprocess, sys
try:
    p = subprocess.Popen(sys.argv[2:])
except OSError as e:
    sys.stderr.write(type(e).__name__)
    sys.exit(127)
def stop(*_):
    p.kill()
    sys.exit(143)
for s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
    signal.signal(s, stop)
try:
    sys.exit(p.wait(timeout=float(sys.argv[1])))
except subprocess.TimeoutExpired:
    p.kill()
    p.wait()
    sys.exit(124)
"""


def _gh_start(cmd, env=None):
    """经看门进程起一个 gh 写命令，不等它（按策略合并用：在开关锁里起，出了锁再等）。看门进程自成一个进程组，
    期限是 REQ_TIMEOUT 秒。连看门进程都起不来就返回那个 OSError。"""
    try:
        return subprocess.Popen([sys.executable, "-c", WATCHDOG, f"{REQ_TIMEOUT:g}", *cmd], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    except OSError as e:
        return e


def _gh_finish(proc):
    """等 _gh_start 起的请求，结果的样子同 _gh_write（不重试），多一项 ended：这个请求怎么结束的（写进账本）。
    返回时请求的进程一定已经不在了：正常退出、看门进程到点杀掉，或者看门进程自己也没按期收场时这里连组杀掉。"""
    res = {"returncode": None, "timed_out": False, "launched": True, "why": "", "detail": "", "stdout": ""}
    if isinstance(proc, OSError):                     # 一个请求都没发出去
        res.update(launched=False, why=f"gh 跑不起来（{type(proc).__name__}）", ended={"how": "没起来"})
        return res
    try:
        out, err = proc.communicate(timeout=REQ_TIMEOUT + 5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        res.update(timed_out=True, why=f"超过 {REQ_TIMEOUT:g} 秒没回应（timeout）", ended={"how": "连组杀掉", "exit": proc.returncode})
        return res
    if proc.returncode == 127:                        # 看门进程起来了，gh 没起来：一个请求都没发出去
        res.update(launched=False, why=f"gh 跑不起来（{clip(err) or '没有输出'}）", ended={"how": "没起来", "exit": 127})
    elif proc.returncode == 124:
        res.update(timed_out=True, why=f"超过 {REQ_TIMEOUT:g} 秒没回应（timeout）", ended={"how": "到期杀掉", "exit": 124})
    else:
        res["returncode"], res["stdout"] = proc.returncode, out or ""
        res["detail"] = (err or out or "").strip()
        res["why"] = (res["detail"].splitlines() or ["没有输出"])[-1]
        res["ended"] = {"how": "自己退出", "exit": proc.returncode}
    return res


def live_requests(grep, *needles):
    """本机进程表里还活着、完整命令行同时含 needles 的进程号（看门进程和它起的 gh 的命令行里都带着请求的地址和参数）。
    grep：不含正则特殊字符的片段，交给 pgrep -f 粗筛；筛出来的逐个用 ps 读完整命令行再核对（一次只读一个进程，
    命令行里有换行也不会认错行）。只用来看，不停任何进程（#260-P2-14）。
    查不了返回 None，调用方按「证明不了请求的进程已经不在」处理，不能当成没有：pgrep 跑不了；或者 pgrep 筛出了一个
    进程、ps 却读不出它的命令行，而它又不是「确实已经没了」（#260-P1-01：ps 失败不等于没有这个进程）。"""
    try:
        r = subprocess.run([PGREP, "-f", grep], capture_output=True, text=True, timeout=30)
        if r.returncode not in (0, 1):                # 1 是一个都没筛到
            return None
        out = []
        for pid in map(int, r.stdout.split()):
            if pid == os.getpid():
                continue
            c = subprocess.run([PS, "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=30)
            if c.returncode != 0 or not c.stdout.strip():
                try:
                    os.kill(pid, 0)                   # 信号 0 只探测，不影响进程
                except ProcessLookupError:
                    continue                          # 筛出来之后它自己退出了
                return None                           # 进程还在（或探测不了），命令行却读不出：不能当它不是
            if all(n in c.stdout for n in needles):
                out.append(pid)
        return out
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _merge_verdict(res, reply, detail, after, head):
    """REST 合并接口（PUT pulls/{n}/merge，sha 绑定 HEAD）的结果判定，托管合并和按策略合并共用：
    merged  接口回执说本次合上了并给了合并提交号，回读已合并、HEAD 对得上、合并提交号一致；
    failed  请求没发出去，或 GitHub 明确拒绝且回读 PR 仍开着；GitHub 回了 4xx 状态码（「(HTTP 4xx)」）而回读失败的
            也算：服务端已经答复不处理这次请求，回读读不到改变不了这一点（不然限流时回读跟着失败，PR 会一直挂成结果不明）；
    unsure  其余一切：超时、断网、没归类的报错、已被别处合上、HEAD 对不上、回读失败。只做只读对账，不重发。
    「PR 现在是已合并」本身不是证据：gh pr merge 对已经被别人合上的 PR 也返回 0。"""
    if not res["launched"]:
        return "failed"
    state = (after or {}).get("state")
    commit = ((after or {}).get("mergeCommit") or {}).get("oid")
    rejected = res["returncode"] not in (None, 0) and REJECTED.search(detail) and not NET_ERR.search(detail)  # 网络错误优先
    if after is None and rejected and HTTP_4XX.search(detail):
        return "failed"
    if after is None or after.get("headRefOid") != head:
        return "unsure"
    if res["returncode"] == 0 and reply.get("merged") is True and reply.get("sha") and state == "MERGED" and commit == reply["sha"]:
        return "merged"
    if rejected and state == "OPEN":                  # 只有 GitHub 明确拒绝、PR 仍开着才算确定没合
        return "failed"
    return "unsure"


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
    failed（GitHub 明确拒绝且回读仍开着；合并请求自己收到「(HTTP 4xx)」而回读失败或不是对象，也算——服务端已答复不处理
    这次请求，#260-P2-16；或 gh 没起来、请求没发出去）；
    unsure（其余一切：超时、网络中断、没归类的报错、已被别人合上、HEAD 对不上、没有状态码时的回读失败或不是对象）——
    只做只读对账，不重发合并。判定见 _merge_verdict，和按策略合并共用。"""
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
    out["merge_commit"] = ((after or {}).get("mergeCommit") or {}).get("oid") if res["launched"] else None
    out["status"] = _merge_verdict(res, reply, detail, after, card["head"])
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
    if MP.unsettled(card["repo"], card["pr"]):
        raise Stop("这个 PR 上一次按策略合并结果不明，还没核对清楚",
                   "先到 GitHub 上看它合上没有：已经合上就不用再点；还开着的话，请开 PR 的会话推一个新提交，等新卡片再决定")
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
    _mark_owner(card["repo"], card["pr"], card["head"], "合并")
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
    _mark_owner(card["repo"], card["pr"], card["head"], "退回")
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
    waiting 机械检查现在没过（已登记交出的，合并官每轮再试）；capped 今天到上限；merged；failed 回读确认没合上
    （permanent：登录、权限、分支规则这类不会自己好的拒绝）；unsure 合并命令发了、回读没成功（下一轮只读核对，不重发）。
    GitHub 没给出改动文件总数或来源仓库的，和别的例外一样返回 exception（#260-P2-09）。
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
        return _res("frozen", f"{ref} 没合并：上一次按策略合并结果不明，还没核对清楚。合并官巡视时只读核对，不会重发，这期间不要另外合并。"
                              "PR 一直开着、版本没变的不会自己放开：要合就给它推一个新提交，再重新交出。")
    path, main = project_of(name)
    at("取 PR 状态")
    p = gh_read(["pr", "view", str(pr), "--repo", repo, "--json", VIEW_FIELDS], f"{ref} 取状态")

    def judge(p):
        """按一次读取判：状态不对抛 Stop；返回 (状态快照, 档, 建议, 理由, 例外的答复或 None)。"""
        f = mo.facts_of(repo, p)
        at("核对版本")
        bad = mo.malformed(f)
        if bad:
            raise Stop("PR 状态不对：" + "；".join(bad), "")
        if want and not f["head_sha"].startswith(want):
            stop = Stop(f"PR 现在的版本是 {f['head_sha'][:7]}，不是交出的 {want[:7]}（之后又有提交）", "")
            stop.moved = True                               # 撤回续接要认出这一种（#260-P1-07）
            raise stop
        tier, sug, reasons = mo.evaluate(f, rules)
        exc = mo.exception_reasons(f, rules)
        out = exc and _res("exception", f"{ref} 属于例外，不按策略合并，仍要常浩决定：{'；'.join(exc)}。"
                           + ("L3 不走飞书合并，在电脑上处理。" if tier == "L3" else "合并官巡视时会发卡片给常浩。"),
                           tier=tier, reasons=exc)
        return f, tier, sug, reasons, out or None

    f, tier, sug, reasons, exc = judge(p)
    head = f["head_sha"]
    if exc:
        return exc
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
    f, tier, sug, reasons, exc = judge(p)                   # 以最后这次读取为准再判一遍（#260-P1-04）：两次读取之间
    if exc:                                                 # 加了 needs-info、文件集合变了，都按例外办；交出登记作废
        MP.drop_handoff(repo, pr)
        return exc
    conf = MP.auto_conf(rules)
    cap, day = conf.get("daily_cap"), mo.now()[:10]
    if cap is not None and MP.merged_count(day) >= cap:
        return _res("capped", f"{ref} 没合并：今天按策略已经合了 {MP.merged_count(day)} 个，到上限 {cap} 了。"
                              "明天自动继续；要今天合，常浩说一声。")
    mid = "m-" + re.sub(r"\D", "", mo.now())[:14] + "-" + os.urandom(3).hex()
    cmd = [mo.GH, "api", "-X", "PUT", f"repos/{repo}/pulls/{pr}/merge", "-f", "merge_method=squash", "-f", f"sha={head}"]
    at("执行合并")
    # 关的分界点（#260-P1-03）：在开关锁里最后读一次开关、记「要合」、起合并请求的子进程，出了锁再等结果。
    # 「关自动合并」拿的是同一把锁：它成功之后不会再有新的合并请求启动；已经启动的照常做完。
    with MP.locked(10, "policy.lock"):
        on, why, sig = MP.active(rules)
        if not on:
            return _res("off", f"{ref} 没合并：{why}。这个 PR 仍等常浩说合并。")
        MP.append({"event": "intent", "id": mid, "repo": repo, "pr": str(pr), "title": f["title"], "url": p.get("url"),
                   "head": head, "base": f["base_sha"], "tier": tier, "rules_version": rules["version"],
                   "suggestion": sug, "reasons": "；".join(reasons),
                   "ready": {"mode": conf.get("ready"), "by": (ready or {}).get("by") or by, "at": (ready or {}).get("at")},
                   "auth": {"type": MP.POLICY_TYPE, "scope_sha256": sig["scope_sha256"], "message_id": sig["message_id"],
                            "signed_at": sig["signed_at"]},
                   "checks": sorted({c.get("name") or c.get("context") or "?" for c in p.get("statusCheckRollup") or []}),
                   "review": "未核实（第一版不核对审查结论，ADR-016）", "identity": "owner-gh"})
        proc = _gh_start(cmd)
        if not isinstance(proc, OSError):                   # 请求的起点记进账本；它怎么结束的记在结果行的 ended 里（#260-P1-01）
            MP.append({"event": "started", "id": mid, "pid": proc.pid, "limit": REQ_TIMEOUT})
    res = _gh_finish(proc)                                  # 返回时这个请求的进程已经不在了
    reply, detail, ended = _json_dict(res.pop("stdout")) or {}, res.pop("detail"), res.pop("ended")
    at("合并后回读")
    try:
        after = gh_read(["pr", "view", str(pr), "--repo", repo, "--json", "state,mergeCommit,headRefOid,baseRefName"], "合并后回读")
        read_why = ""
    except Stop as e:
        after, read_why = None, f"；回读：{clip(e.why)}"
    verdict = _merge_verdict(res, reply, detail, after, head)
    if verdict == "merged":                                 # 有接口回执为证：这次请求合上的（#260-P1-02）
        sha = reply["sha"]
        MP.append({"event": "merged", "id": mid, "merge_commit": sha, "base_ref": after.get("baseRefName"), "ended": ended})
        MP.drop_handoff(repo, pr)
        _mark_owner(repo, pr, head, mo.POLICY_OWNER, f, rules)
        wrong = after.get("baseRefName") != main            # 目标分支在最后一次核对之后被改了（接口绑不住目标分支）
        return _res("merged", (f"⚠️ {ref} 合进的是 {after.get('baseRefName')}，不是 {main}，请常浩看一眼。" if wrong else "")
                    + f"已按策略合并 {ref}@{head[:7]} → {sha[:7]}（记录 {mid}）。没拉本机主目录、没安装；"
                    f"要安装、发布或写 CURRENT，仍先问常浩。撤回：merge_act.py revert {ref}", id=mid, merge_commit=sha, warn=wrong)
    if verdict == "failed":                                 # 确定没合：请求没发出去，或 GitHub 明确拒绝且 PR 还开着
        why = clip(res["why"])
        permanent = not res["launched"] or not TRANSIENT.search(detail)       # 只有限流、版本刚变这几类会自己好
        MP.append({"event": "failed", "id": mid, "why": why, "sent": res["launched"], "permanent": permanent, "ended": ended})
        return _res("failed", f"{ref}@{head[:7]} 没合上：" + (f"GitHub 明确拒绝（{why}），回读确认 PR 还开着，没有被改动。"
                                                             if res["launched"] else f"合并请求没发出去（{why}）。"),
                    id=mid, why=why, permanent=permanent)
    MP.append({"event": "unsure", "id": mid, "why": f"合并请求：{clip(res['why']) or '已返回'}{read_why}", "ended": ended})
    return _res("unsure", f"{ref}@{head[:7]} 结果不明：合并请求已经发出，没拿到确定的结果（{clip(res['why']) or '回执和回读对不上'}{read_why}）。"
                          "合并官巡视时只读核对，不会重发；这期间不要另外合并。之后它一直开着的话不会自己放开："
                          "要合就推一个新提交再重新交出。", id=mid)


SETTLE_HOLD = 600      # 秒。结果不明的请求发出后过了这么久还没结清，开始每天提醒常浩；重新开撤回 PR 前的静默期也是它


def _age(stamp):
    return (dt.datetime.fromisoformat(mo.now()) - dt.datetime.fromisoformat(stamp)).total_seconds()


def reconcile_unsettled(repo=None, pr=None, remind=True):
    """上次结果不明的合并（只记了要合、或记了 unsure）：只读核对，从不重发，也不停任何进程（调用方持 MP.locked()）。
    和策略现在开没开无关。repo、pr：只核对这一个 PR；remind=False 时不发每天一次的提醒（会话当场核对用）。
    核对只能看到 PR 现在的状态，证明不了是谁合的。同步合并接口也没有「这次请求在 GitHub 那头已经结束」的凭据可取：
    本机进程没了、过了多久，都不等于 GitHub 不会再处理它（#260-P1-01）。所以只在下面两种有远端依据的情况下结算：
      PR 已合并 → merged_unknown（已合并、来源不明）：不算按策略合并，不能用撤回口令；
      PR 已关闭或换了版本，且本机没有这次请求的进程 → not_merged：那次请求用 sha 绑着旧版本，GitHub 不会拿它合别的
        版本，关着的 PR 也合不了；交出登记作废，要合得由会话重新交出。
    其余一律保持结果不明，不放开重新交出：
      PR 还开着、版本没变 → 一直挂着。要合：给 PR 推一个新提交（上面第二种）再重新交出；
      进程表查不了 → 证明不了请求的进程已经不在；
      请求的进程还活着 → 脚本不去停它（按命令行认进程有认错的可能，#260-P2-14），给出进程号请常浩在电脑上确认后停。
    后三种过了 SETTLE_HOLD 每天说一次。返回要常浩知道的话。"""
    said, day = [], mo.now()[:10]

    def once(kind, m, text):
        if remind and _age(m["at"]) >= SETTLE_HOLD and MP.note_once(f"{kind}:{m['id']}", day):
            said.append(text)

    for m in MP.unsettled(repo, pr):
        ref = f"{m['repo'].split('/')[-1]}#{m['pr']}@{str(m['head'])[:7]}"
        alive = live_requests(f"pulls/{m['pr']}/merge", f"repos/{m['repo']}/pulls/{m['pr']}/merge", f"sha={m['head']}")
        try:                                               # 先查进程、后读 PR：读到的状态一定不早于这次进程检查
            after = gh_read(["pr", "view", str(m["pr"]), "--repo", m["repo"], "--json", "state,mergeCommit,headRefOid"],
                            "只读核对")
        except Stop:
            continue                                       # 这一轮读不到，下一轮再核对
        state, now_head = after.get("state"), after.get("headRefOid")
        if state == "MERGED":
            MP.append({"event": "merged_unknown", "id": m["id"], "head_now": now_head,
                       "merge_commit": (after.get("mergeCommit") or {}).get("oid")})
            MP.drop_handoff(m["repo"], m["pr"])
            said.append(f"只读核对：{ref} 现在已合并，但证明不了是这次按策略合的（合并请求发出后没拿到确定结果，别处也可能合了它）。"
                        "记成「已合并、来源不明」：不算按策略合并，不能用撤回口令，要撤回在电脑上处理。")
        elif alive is None:
            once("ps", m, f"只读核对：{ref} 没合并，但本机进程表查不了，证明不了那次合并请求的进程已经不在。先一直按结果不明挂着，"
                          "不放开重新交出；请在电脑上交给 Claude 会话看。")
        elif alive:
            once("alive", m, f"只读核对：{ref} 那次合并请求的进程过了期限还活着（进程号 {'、'.join(map(str, alive))}）。脚本不会去停它："
                             "请在电脑上确认是它之后手动停掉。停掉之前这个 PR 一直按结果不明挂着。")
        elif state == "OPEN" and now_head == m["head"]:
            once("open", m, f"只读核对：{ref} 还开着、版本没变。那次合并请求 GitHub 到底处理没有，查不到凭据，所以一直按结果不明挂着，"
                            "不重发、不放开。要合：给这个 PR 推一个新提交（旧请求绑的是旧版本，合不了新版本），再由会话重新交出；"
                            "不要了就关掉 PR。")
        elif not (state == "CLOSED" or (state == "OPEN" and SHA40.match(str(now_head or "")))):
            continue                                       # 状态或版本没读全：没有依据，不结算，下一轮再核对
        else:
            MP.append({"event": "not_merged", "id": m["id"],
                       "why": f"只读核对：PR 现在是 {state}，版本 {str(now_head)[:7]}（那次请求绑的是 {str(m['head'])[:7]}）；本机已没有这次合并请求的进程"})
            MP.drop_handoff(m["repo"], m["pr"])
            said.append(f"只读核对：{ref} 没合上（PR 现在是 {state}，版本 {str(now_head)[:7]}）。不会自动重试：还要合的话，请开 PR 的会话重新交出。")
    return said


def auto_scan(rules, open_now, settled):
    """合并官每轮巡视时调（调用方持 MP.locked()，策略已确认生效）。open_now：这一轮读到的 open PR 状态；
    settled：这一轮刚有了结局的判定行。返回要常浩知道的话（没有就是空列表）：结果不明、只读核对的结论、到上限、
    合并被 GitHub 拒绝（每个 PR 每天说一次）；合成功的默认不说，进日报（auto.notify 为 each 时每个都说）。"""
    conf = MP.auto_conf(rules)
    day = mo.now()[:10]
    covered = lambda repo: bool((act_repos(rules).get(repo.split("/")[-1]) or {}).get("ci"))  # noqa: E731
    said = reconcile_unsettled()                           # 1. 上次结果不明的：只读核对，不重发
    done = MP.bypassed()                                   # 2. 已合并却没经合并执行器的：留一条记录
    for r in settled:
        key = (r["repo"], r["pr"])
        if (r["outcome"] == mo.MERGED and covered(r["repo"]) and not r.get("owner") and key not in done
                and not MP.find_merged(*key, statuses=("merged", "merged_unknown"))):      # 来源不明的另有记录，不重复算绕过
            MP.append({"event": "bypass", "repo": r["repo"], "pr": r["pr"], "title": r["title"], "head": r["outcome_head"],
                       "tier": r["tier"], "suggestion": r["suggestion"], "merged_at": r["outcome_at"],
                       "note": "PR 已合并，账本里没有合并执行器的记录（会话或网页上直接合的）"})
            done.add(key)
    now_open = {(f["repo"], str(f["pr"])): f for f in open_now}
    hand, todo = {}, []                                    # todo：这一轮要试的 (仓库, 号, 版本, 交出登记)
    for h in list(MP.handoffs()):                          # 3. 交出登记：确认 PR 关了、合了或有了新提交才作废（#260-P2-10）
        key = (h["repo"], str(h["pr"]))
        f = now_open.get(key)
        if f is None:                                      # 不在这一轮的列表里：列表没取到、没列全，或 PR 确实不开着了。逐个核实
            try:
                p = gh_read(["pr", "view", key[1], "--repo", key[0], "--json", "state,headRefOid"], "核实交出登记")
            except Stop:
                continue                                   # 核实不了：登记留着，下一轮再看
            if p.get("state") != "OPEN" or p.get("headRefOid") != h["head"]:
                MP.drop_handoff(*key)
            elif covered(h["repo"]):
                todo.append((*key, h["head"], h))          # 还开着、版本没变，只是没列出来：照样试（auto_try 自己会完整读一遍）
        elif f["head_sha"] != h["head"]:
            MP.drop_handoff(*key)
        else:
            hand[key] = h
    for f in open_now:                                     # 4. 能合的合
        key = (f["repo"], str(f["pr"]))
        h = hand.get(key)
        if not covered(f["repo"]) or (conf.get("ready") != "any" and h is None):
            continue
        checks = f.get("checks") or []
        if (f.get("draft") or f.get("mergeable") == "CONFLICTING" or not checks or any(c not in CHECK_OK for c in checks)
                or mo.exception_reasons(f, rules)):
            continue                                       # 列表里就看得出现在不能合的，不再逐个去读
        todo.append((*key, f["head_sha"], h))
    for repo, pr, head, h in todo:
        if MP.failed_today(repo, pr, head, day):
            continue                                       # 今天已经被永久性地拒绝过（说过了）：不再每轮撞一次
        try:
            res = auto_try(repo, pr, (h or {}).get("by") or "合并官巡视", rules, want=head, register=False)
        except (Stop, MP.Rejected):
            continue                                       # 合并前的读取或核对没过（网络、版本刚变、开关锁没等到）：下一轮再试
        if res["status"] == "unsure" or res.get("warn") or (res["status"] == "merged" and conf.get("notify") == "each"):
            said.append(res["text"])
        elif res["status"] == "failed":                    # 交出登记留着；同一个 PR 一天只说一次（#260-P2-13）
            if MP.note_once(f"failed:{repo}#{pr}", day):
                said.append(res["text"] + ("这类拒绝不会自己好：今天不再重试，交出登记留着；处理好登录、权限或分支规则后，"
                                           "请开 PR 的会话重新交出，或等明天巡视再试一次。" if res.get("permanent")
                                           else "交出登记留着，合并官下一轮巡视会再试；今天不再重复提醒。"))
        elif res["status"] == "capped":
            if MP.note_once("capped", day):
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
        pending = MP.unsettled(repo, pr)
        if pending:                                        # 上次结果不明：先当场只读核对（PR 换了版本就能结清），不用等巡视
            for line in reconcile_unsettled(repo, pr, remind=False):
                print(line)
            now = {m["id"]: m["status"] for m in MP.merges()}
            if now.get(pending[-1]["id"]) == "merged_unknown":
                return 1                                   # 核对出来已经合并了（来源不明）：没有可合的
            if not want and now.get(pending[-1]["id"]) == "not_merged":
                # 刚结清的旧请求说明 PR 换了版本；没写版本号的交出命令可能是会话异常恢复后重跑的旧命令，
                # 不能拿它给没人看过的新版本建立授权（#260-P1-01，第 5 轮）。要合得写明现在的版本号再交出。
                print(f"{name}#{pr} 没合并：上一次按策略合并结果不明，刚核对结清（PR 换了版本）。这次交出没写版本号，"
                      f"不知道交出的是不是现在这个版本，所以不登记、不合并。看过现在的版本后，写明版本号再交出："
                      f"merge_act.py auto {name}#{pr}@<版本前缀>")
                return 1
        res = auto_try(repo, pr, by, mo.load_rules(), want=want, register=True)
    print(res["text"])
    return AUTO_EXIT.get(res["status"], 1)


REVERT_VIEW = ("number,url,state,body,baseRefName,headRefOid,isCrossRepository,headRepository,headRepositoryOwner,"
               "files,changedFiles,createdAt")
REVERT_LIST = 100      # 找已经开出的撤回 PR 时，一次列最近创建的多少个 PR
CLOCK_SLACK = 600      # 秒。比撤回 PR 的创建时间时，GitHub 的时钟和本机的差这么多以内都算正常


def _utc(stamp):
    return dt.datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))


def _revert_mismatch(p, repo, pr, hit, main, orig, since):
    """一个 PR 是不是「撤回 <仓库>#<pr> 的那个撤回 PR」：身份逐项核对（刚开出时、事后认领时共用，#260-P2-03）。
    p 是它的完整信息（REVERT_VIEW），orig 是原 PR 的 files、changedFiles，since 是第一次要开撤回 PR 的时间。
    返回对不上的地方；空列表才算对得上。这里比的改动只是 GitHub 给的文件名和增删行数；内容另由 _revert_content_why 核对。"""
    bad = []
    body = p.get("body") or ""
    if not (hit["id"] in body and f"{repo}#{pr}" in body and str(hit.get("merge_commit")) in body):
        bad.append("正文里的记录号、原 PR 编号、原合并提交号对不全")
    if p.get("baseRefName") != main:
        bad.append(f"目标分支是 {p.get('baseRefName')}，不是 {main}")
    if not same_repo(p, repo):
        bad.append("来源分支不在本仓库")
    if not SHA40.match(str(p.get("headRefOid") or "")):
        bad.append("读不到完整的版本号")
    try:                                                   # 早就有的 PR 后来把正文改成带记录号的，不是这次开出来的
        early = (_utc(since) - _utc(p.get("createdAt"))).total_seconds() > CLOCK_SLACK
    except (TypeError, ValueError):
        early = True
    if early:
        bad.append(f"它的创建时间（{p.get('createdAt') or '读不到'}）早于这次撤回（{str(since)[:16]}），不是这次开出来的")
    want = {f.get("path"): (f.get("deletions") or 0, f.get("additions") or 0) for f in orig.get("files") or []}
    got = {f.get("path"): (f.get("additions") or 0, f.get("deletions") or 0) for f in p.get("files") or []}
    if not want or got != want or orig.get("changedFiles") != len(want) or p.get("changedFiles") != len(got):
        bad.append("改动和原 PR 反过来对不上（文件不一样，或某个文件的增删行数不是原 PR 的减增行数）")
    return bad


def _revert_content_why(path, main, number, head, merge_commit):
    """撤回 PR 的版本 head 是不是「正好把原合并提交倒回去」：按内容核对（#260-P1-07；文件名和增删行数一样、内容不同的
    提交，_revert_mismatch 认不出来）。只取 Git 对象：在项目主目录 fetch 主分支和 refs/pull/<number>/head，不检出、
    不运行里面的任何东西（#190）。要同时成立：
      head 只有一个父提交 B；原合并提交 M 只有一个父提交（squash 合并出来的）；
      M 是 B 的祖先，B 在 origin/<main> 上：撤回提交直接落在主分支的某个提交上，下面没有夹带别的提交；
      以 M 为基、把 M 的父提交合到 B 上得到的树（git revert M 做的就是这个三方合并），等于 head 的树。
    版本号是内容的哈希：核对过的 head 不会再变，之后的合并请求又用 sha 绑着它，所以这里核对的就是会被合进去的东西。
    返回 (原因, 能不能再试)：原因是空字符串才算对得上；对不上的原因照写，能不能再试为 False。这一次做不了的（取对象时
    网络断了、GitHub 还没生成 refs/pull/<号>/head、git 命令出错、倒回去有冲突）原因以「暂时核对不了」开头、能不能再试为
    True：不算对得上，也不记成对不上，下次再跑撤回命令时重新核对（#260 送审前自审：一次网络抖动不该让撤回 PR 永远不能自动合）。"""
    if not (SHA40.match(str(head)) and SHA40.match(str(merge_commit))):
        return "撤回 PR 的版本号或原合并提交号不完整", False

    def holds(older, newer):
        return subprocess.run(["git", "-C", str(path), "merge-base", "--is-ancestor", older, newer],
                              capture_output=True, timeout=120).returncode == 0
    try:
        git(path, "fetch", "-q", "origin", main, f"refs/pull/{number}/head")
        h = git(path, "rev-list", "--parents", "-n", "1", head + "^{commit}").split()
        m = git(path, "rev-list", "--parents", "-n", "1", merge_commit + "^{commit}").split()
        if len(h) != 2:
            return f"撤回 PR 的版本有 {len(h) - 1} 个父提交，不是单独一个撤回提交", False
        if len(m) != 2:
            return f"原合并提交有 {len(m) - 1} 个父提交，不是 squash 合并出来的", False
        if not holds(merge_commit, h[1]):
            return "撤回提交没有落在包含原合并提交的提交上", False
        if not holds(h[1], f"origin/{main}"):
            return f"撤回提交下面夹着不在 {main} 上的提交", False
        want = git(path, "merge-tree", "--write-tree", "--no-messages", f"--merge-base={merge_commit}", h[1], m[1]).split()[0]
        if git(path, "rev-parse", head + "^{tree}") != want:
            return "撤回提交的内容不是正好把原合并倒回去（文件内容对不上）", False
        return "", False
    except (Stop, OSError, IndexError, subprocess.SubprocessError) as e:
        return f"暂时核对不了撤回提交的内容（{clip(getattr(e, 'why', None) or type(e).__name__)}）", True


def _find_revert(repo, hit, since):
    """正文带这条记录号的 PR（号）。用列表接口（实时；正文搜索有索引延迟，搜不到不等于没创建，#260-P2-03）：
    列表从新到旧，要么不满一页（全列到了），要么最旧的一个早于 since（第一次要开撤回 PR 的时间），才算把 since 之后
    创建的都看到了；否则证明不了没漏，抛 Stop。"""
    got = [x for x in gh_read(["pr", "list", "--repo", repo, "--state", "all", "--limit", str(REVERT_LIST),
                               "--json", "number,body,createdAt"], "找已经开出的撤回 PR") if isinstance(x, dict)]
    if len(got) >= REVERT_LIST:
        try:
            complete = min(_utc(x.get("createdAt")) for x in got) < _utc(since) - dt.timedelta(seconds=CLOCK_SLACK)
        except (TypeError, ValueError):
            complete = False
        if not complete:
            raise Stop(f"上次要开撤回 PR 之后新开的 PR 超过 {REVERT_LIST} 个，列不全，证明不了撤回 PR 没开出来", "在电脑上交给 Claude 会话看")
    return [str(x["number"]) for x in got if hit["id"] in (x.get("body") or "")]


def _adopt_revert(repo, pr, hit, path, main, orig, again):
    """上次要开撤回 PR、账本里没有它开出来的记录（超时、断网、GitHub 报错、发起的进程没了）：先找，不重发（#260-P2-03）。
    返回认领到的撤回 PR；返回 None 表示可以重新开；其余情况抛 Stop 停下，脚本不停进程、不重开已关掉的 PR。
    认领：从第一次要开到现在的 PR 列表里，正文带记录号的正好一个；它是这次撤回之后创建的，身份和内容都核对得上，
    而且还开着或已经合并（被关掉的停下交人）。
    重新开要同时满足：本机没有上次那个请求的进程；列表里一个候选都没有；「上次的请求已经结束」有记录（请求返回时记的
    revert_unsure，或这里第一次看到进程不在时记的 revert_quiet），那条记录之后又过了 SETTLE_HOLD（留给已经到了
    GitHub、撤回 PR 还没出现在列表里的请求）。"""
    alive = live_requests("pr revert", f"pr revert {pr} --repo {repo} ", hit["id"])    # 命令行里的正文带着这条记录号
    if alive is None:
        raise Stop("本机进程表查不了，证明不了上一次开撤回 PR 的请求已经停了", "在电脑上交给 Claude 会话看")
    if alive:
        raise Stop(f"上一次开撤回 PR 的请求还在跑（进程号 {'、'.join(map(str, alive))}）",
                   "过几分钟再试；它一直不退的话，在电脑上确认是它之后手动停掉，脚本不会去停")
    since = again["first_at"]
    found = _find_revert(repo, hit, since)
    if len(found) > 1:
        raise Stop(f"找到 {len(found)} 个正文带记录号 {hit['id']} 的 PR（#{'、#'.join(found)}），认不出哪个是撤回 PR",
                   "在电脑上交给 Claude 会话看")
    if found:
        cand = gh_read(["pr", "view", found[0], "--repo", repo, "--json", REVERT_VIEW], "核对找到的撤回 PR")
        bad = _revert_mismatch(cand, repo, pr, hit, main, orig, since)
        if not bad and cand.get("state") not in ("OPEN", "MERGED"):
            raise Stop(f"上次开的撤回 PR #{found[0]} 找到了，但它已经被关掉", "在电脑上看一下是谁、为什么关的：还要撤回就把它重新打开再跑一次，"
                                                                  "或在电脑上另开；脚本不会自己重开或另开")
        if not bad:
            why, retry = _revert_content_why(path, main, found[0], cand["headRefOid"], hit.get("merge_commit"))
            if retry:
                raise Stop(f"找到正文带记录号的 PR #{found[0]}，身份对得上，但{why}", "过几分钟再跑一次同一条命令")
            bad = [why] if why else []
        if bad:
            raise Stop(f"找到正文带记录号的 PR #{found[0]}，但核对不上，不认它是撤回 PR：{'；'.join(bad)}",
                       "在电脑上交给 Claude 会话看")
        rev = {"revert_pr": found[0], "revert_url": cand.get("url"), "reason": again.get("reason"),
               "head_unverified": "事后认领的，没看到它开出时的版本"}
        MP.append({"event": "revert_opened", "id": hit["id"], "repo": repo, "pr": str(pr), "adopted": True, **rev})
        print(f"上次开的撤回 PR 找到了：{rev['revert_url']}。")
        return rev
    wait = f"过 {SETTLE_HOLD // 60} 分钟再试：还找不到才会重新开"
    if not again.get("quiet_at"):
        MP.append({"event": "revert_quiet", "id": hit["id"], "repo": repo, "pr": str(pr)})
        raise Stop("上一次开撤回 PR 没留下结果，GitHub 上还找不到它；刚确认本机已经没有那次请求的进程", wait)
    if _age(again["quiet_at"]) < SETTLE_HOLD:
        raise Stop("上一次开撤回 PR 结果不明，GitHub 上还找不到它", wait)
    return None


def cmd_revert(ref, reason):
    """撤回一个按策略合并的 PR：只认账本里合并执行器按策略合过的；用 gh pr revert 开撤回 PR（经看门进程，不重试），记进账本。
    auto.revert_auto_merge 开着、策略有效时，撤回 PR 接着按同一套检查合并（它自己的 CI 过了才合），但只合
    「开出当时核对过并记下的那个版本」（revert_head，#260-P1-07）：身份（_revert_mismatch）和内容（_revert_content_why）
    都核对得上才记。之后有了新提交、开出时没核对上、或是事后认领的（没亲眼看到开出时的版本），都不自动合并，说清楚，
    要合得由会话重新交出那个版本（merge_act.py auto）。上次要开、没记下它开出来的，见 _adopt_revert。"""
    m = AUTO_REF.match(ref.strip())
    if not m:
        raise Stop(f"看不懂「{ref}」（要写成 仓库#PR，例如 my-skills#121）", "")
    name, pr, _ = m.groups()
    repo = repo_of(name)
    rules = mo.load_rules()
    with MP.locked():
        hit = MP.find_merged(repo, pr)
        if not hit:
            raise Stop(f"账本里没有按策略合并过 {name}#{pr}（只撤回合并执行器按策略合的，有接口回执为证的）", "")
        path, main = project_of(name)
        rev = hit.get("revert")
        if rev:
            print(f"{name}#{pr} 已经开过撤回 PR：{rev.get('revert_url')}（{rev['at'][:16]}）。")
        else:
            at("开撤回 PR")
            orig = gh_read(["pr", "view", str(pr), "--repo", repo, "--json", "files,changedFiles"], "读原 PR 的改动")
            again = hit.get("revert_intent")
            if again:
                rev = _adopt_revert(repo, pr, hit, path, main, orig, again)
        if not rev:
            reason = (reason or "").strip() or "常浩要求撤回"
            body = (f"撤回按策略合并的 {repo}#{pr}（合并提交 `{hit.get('merge_commit')}`，记录 {hit['id']}）。\n\n"
                    f"原因：{reason}\n\n由合并执行器开出（merge_act.py revert，#257）。")
            since = (again or {}).get("first_at") or mo.now()
            MP.append({"event": "revert_intent", "id": hit["id"], "repo": repo, "pr": str(pr), "reason": reason})
            r = _gh_finish(_gh_start([mo.GH, "pr", "revert", str(pr), "--repo", repo, "--body", body]))
            url = PULL_URL.search(r["stdout"] or "")
            if r["returncode"] != 0 or not url:                # 没拿到撤回 PR 的网址：这次请求已经结束，记下来当静默期的起点
                MP.append({"event": "revert_unsure", "id": hit["id"], "repo": repo, "pr": str(pr), "sent": r["launched"],
                           "why": clip(r["why"]), "ended": r.get("ended")})
                stop = gh_stop(r["why"], other="撤回 PR 没开出来")
                stop.state = "不确定撤回 PR 开出来没有（再跑一次会先找，不会重复开）" if r["launched"] \
                    else "没有开出撤回 PR（请求没发出去）"
                raise stop
            rev = {"revert_pr": url.group(1), "revert_url": url.group(0), "reason": reason}
            try:                                           # 马上读它开出时的版本并核对：只有这个版本以后会被自动合并
                born = gh_read(["pr", "view", rev["revert_pr"], "--repo", repo, "--json", REVERT_VIEW], "读撤回 PR 开出时的版本")
                bad = _revert_mismatch(born, repo, pr, hit, main, orig, since)
            except Stop as e:
                born, bad = {}, [f"开出后没读到它（{clip(e.why)}）"]
            retry = False
            if not bad:
                why, retry = _revert_content_why(path, main, rev["revert_pr"], born["headRefOid"], hit.get("merge_commit"))
                bad = [why] if why else []
            if retry:                                      # 身份对得上、内容这次没核成：记下这个版本，下次再跑时再核
                rev["head_pending"], rev["head_unverified"] = born["headRefOid"], bad[0]
            elif bad:
                rev["head_unverified"] = "；".join(bad)
            else:
                rev["revert_head"] = born["headRefOid"]
            MP.append({"event": "revert_opened", "id": hit["id"], "repo": repo, "pr": str(pr), **rev})
            print(f"已开撤回 PR：{url.group(0)}（撤回 {name}#{pr}，原因：{reason}）。已经装机的改动撤回后要不要重装，仍先问常浩。")
        # 接着合撤回 PR；上次停在这一步之前的，再跑一次从这里续上（#260-P2-03）。只合开出当时记下的版本（#260-P1-07）
        if not (MP.auto_conf(rules).get("revert_auto_merge") and MP.active(rules)[0]):
            print("撤回 PR 没有自动合并，等常浩说合并。")
            return 0
        rref = f"{name}#{rev['revert_pr']}"
        how = f"要合：看过它的内容后，由会话交出 merge_act.py auto {rref}，或常浩在电脑上合。"
        want = rev.get("revert_head")
        if not want and rev.get("head_pending"):          # 开出时内容没核成：开出时读到的那个版本，现在再核一次
            why, retry = _revert_content_why(path, main, rev["revert_pr"], rev["head_pending"], hit.get("merge_commit"))
            if retry:
                print(f"撤回 PR 还没合并：{why}。过一会儿再跑一次 merge_act.py revert {name}#{pr} 会接着核对、接着合。")
                return 0
            MP.append({"event": "revert_checked", "id": hit["id"], "repo": repo, "pr": str(pr),
                       **({"revert_head": rev["head_pending"]} if not why else {"head_unverified": why})})
            want = None if why else rev["head_pending"]
            rev = {**rev, "head_unverified": why or None}

        def later(e):
            why = f"在『{e.step}』这一步{e.why}" if isinstance(e, Stop) else str(e)
            return f"撤回 PR 还没合并：{why}。过一会儿再跑一次 merge_act.py revert {name}#{pr} 会接着合，或等常浩说合并。"

        def moved(now):
            return (f"撤回 PR {rref} 在开出之后又有新提交（开出时是 {want[:7]}，现在是 {str(now)[:7]}），不自动合并："
                    f"新版本没有人交出过，要常浩重新决定。{how}")

        try:
            now = gh_read(["pr", "view", rev["revert_pr"], "--repo", repo, "--json", "state,headRefOid"], "看撤回 PR 状态")
        except Stop as e:
            now, unread = None, e
        if now and now.get("state") != "OPEN":
            print("撤回 PR 已经合并。" if now.get("state") == "MERGED" else f"撤回 PR 现在是 {now.get('state')}，没有合并。")
        elif not want:
            print(f"撤回 PR 没有自动合并：没记下它开出时的版本（{rev.get('head_unverified') or '没有记录'}），"
                  f"证明不了现在的版本就是当时开出的。{how}")
        elif now is None:
            print(later(unread))
        elif now.get("headRefOid") != want:
            print(moved(now.get("headRefOid")))
        else:
            try:
                print(auto_try(repo, rev["revert_pr"], f"撤回 {name}#{pr}", rules, want=want, register=True)["text"])
            except (Stop, MP.Rejected) as e:               # 撤回 PR 已经开出、记了账；只是接着合并这一步没做成
                print(moved("另一个版本") if getattr(e, "moved", False) else later(e))     # moved：两次读取之间又被推了新提交
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
    def mark(data):
        for c in data["cards"]:
            if (c["repo"], str(c["pr"]), c["head"]) == (card["repo"], str(card["pr"]), card["head"]):
                c["return_asked_at"] = mo.now()

    update_cards(mark)


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
            return with_merge_lock(cmd_merge, a.ref)
        if a.cmd == "auto":
            return cmd_auto(a.ref, a.by)
        if a.cmd == "revert":
            return cmd_revert(a.ref, " ".join(a.reason))
        return with_merge_lock(cmd_return, a.ref, " ".join(a.reason))
    except Stop as e:
        if a.cmd in ("auto", "revert"):                  # 这两个不是从卡片来的，不套「再点一次」的说法
            todo = e.todo.replace("{again}", "再跑一次同一条命令")      # 怎么办也要说（原来这两条命令只说了卡在哪、为什么）
            print(f"{'没合并' if a.cmd == 'auto' else '没撤回'}：在『{e.step}』这一步{e.why}。{e.state}。" + (f"{todo}。" if todo else ""))
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
