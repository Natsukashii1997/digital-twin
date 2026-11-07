#!/usr/bin/env python3
"""飞书一句话合并（#67）：影子合并官给新 PR 发卡片，常浩引用卡片回「合并」或「退回 原因」后，由 Hermes 调用本脚本执行。

授权是单次的 owner_direct：每次都是常浩本人对一张卡片（仓库、PR、完整 HEAD）说了话；本脚本只认发过的卡片，不认别的 PR 或版本。
不放权、不变基、不改代码、不安装、不写 CURRENT。

合并前逐项核对，任一不满足就停下说明（《工程流程》第 5 节、ARCHITECTURE 5.5）：
  1. 仓库在 merge_rules.json 的 act.repos 里；风险档不是 L3（L3 不走飞书）；
  2. PR 还开着、不是草稿、HEAD 仍是卡片上的、base 是主分支、没冲突、不落后（mergeStateStatus 为 CLEAN）；
  3. 本机主目录的主分支没有没推的提交；
  4. 登记了 ci 的仓库：这个 HEAD 的 GitHub 检查全部通过（还在跑、没过、一项都没有都停下），不在本机跑测试；
     没登记 ci 的：在干净 worktree 里检出这个 HEAD，先跑 setup（装依赖）再跑 test，通过；
  5. 合并命令带 --match-head-commit（服务端再校验一次 HEAD），合并后回读确认已合并。
合并后主目录停在主分支、又没有未提交改动时才 git pull --ff-only；否则只说明为什么没拉。

用法：
  merge_act.py cards                         列出还没处理的卡片（Hermes 技能核对用）
  merge_act.py merge <仓库#PR@HEAD前缀>        例如 digital-twin#68@1a2b3c4
  merge_act.py return <仓库#PR@HEAD前缀> 原因   在 PR 里留言退回（不关 PR）
环境变量同 merge_officer.py，另有 DT_MERGE_RUNS（测试运行目录，默认 DigitalTwin/merge-runs）。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
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


class Stop(Exception):
    """核对没过：不合并，把原因告诉常浩。"""


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
    if row["tier"] == "L3":
        lines.append("⚠️ 高风险（L3），飞书里不合并。请在电脑上让 Claude 会话处理。")
    else:
        lines.append("长按这张卡片选「回复」：回 **合并**，Hermes 核对版本、跑测试后合并；回 **退回 原因**，它把原因留言到 PR。")
    return {"schema": "2.0",
            "header": {"title": {"tag": "plain_text", "content": f"🔀 待你决定 · {ref}"},
                       "template": COLOR.get(row["suggestion"], "blue")},
            "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)}]}}


def home_chat():
    from proposal_digest import home_chat as hc       # 同一套：环境变量，没有再从 ~/.hermes/.env 只读这一项
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
        raise Stop(f"看不懂「{ref}」，要写成 仓库#PR@版本，例如 digital-twin#68@1a2b3c4")
    name, pr, h = m.groups()
    hits = [c for c in read_cards()["cards"]
            if c["repo"].split("/")[-1] == name and str(c["pr"]) == pr and c["head"].startswith(h)]
    if not hits:
        raise Stop(f"没发过 {ref} 这张卡片（只认发过的卡片上的版本）")
    if len({c["head"] for c in hits}) > 1:
        raise Stop(f"{ref} 对得上不止一个版本，请写更长的版本号")
    return hits[-1]


# ---------- 记录 ----------

def record_owner(card, decision):
    rows = mo.read_rows()
    for r in rows:
        if (r["repo"], r["pr"], r["head_sha"]) == (card["repo"], str(card["pr"]), card["head"]):
            r["owner"], r["owner_at"] = decision, mo.now()
    mo.write_rows(rows)


# ---------- 执行 ----------

def git(cwd, *args, timeout=120):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        last = ((r.stderr or r.stdout).strip().splitlines() or ["未知"])[-1]
        raise Stop(f"git {args[0]} 失败：{last}"[:300])
    return r.stdout.strip()


def project_of(name):
    for p in json.loads(mo.ROSTER.read_text(encoding="utf-8"))["projects"]:
        if (p.get("github") or "").split("/")[-1] == name:
            return Path(p["path"]).expanduser(), p.get("main") or "main"
    raise Stop(f"名册里没有 {name}")


def require_ci_passed(p):
    """statusCheckRollup（只含当前 HEAD 的检查）全部通过才往下走，否则停下并说明哪项在跑、哪项没过。
    免费版私有仓库没有分支保护，GitHub 不拦，所以这里自己把 CI 当合并条件。"""
    checks = p.get("statusCheckRollup") or []
    if not checks:
        raise Stop("这个版本没有任何 CI 检查结果（该有 CI 却没跑），在电脑上看一下")
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
        raise Stop("CI 没过：" + "、".join(failed) + "，交回开这个 PR 的会话修")
    if waiting:
        raise Stop("CI 还在跑：" + "、".join(waiting) + "，跑完再回「合并」")


def check_pr(card, rules, main, ci=False):
    p = mo.gh_json(["pr", "view", str(card["pr"]), "--repo", card["repo"], "--json", VIEW_FIELDS], f"{card['repo']}#{card['pr']} 取状态")
    if p.get("state") != "OPEN":
        raise Stop(f"PR 已经是 {p.get('state')}，不用再合")
    if p.get("headRefOid") != card["head"]:
        raise Stop(f"PR 在卡片之后又有提交（现在是 {str(p.get('headRefOid'))[:7]}），按新卡片再决定")
    if p.get("baseRefName") != main:
        raise Stop(f"PR 不是合进 {main}（是 {p.get('baseRefName')}）")
    if p.get("isDraft"):
        raise Stop("PR 还是草稿")
    tier = mo.tier_of(card["repo"], sorted(f.get("path", "") for f in p.get("files") or []), rules)
    if tier == "L3":
        raise Stop("改动碰了敏感路径（L3），飞书里不合并，请在电脑上让 Claude 会话处理")
    state = p.get("mergeStateStatus")
    if p.get("mergeable") == "CONFLICTING" or state == "DIRTY":
        raise Stop("和主分支有冲突，要交回开这个 PR 的会话去变基")
    if state == "BEHIND":
        raise Stop("落后主分支，要交回开这个 PR 的会话去变基")
    if ci:
        require_ci_passed(p)                          # CI 没过或在跑时 GitHub 报 UNSTABLE，先说清楚是 CI
    if state != "CLEAN":
        raise Stop(f"GitHub 现在的合并状态是 {state}，不是 CLEAN，稍后再试或在电脑上看")
    return p


def run_tests(path, card, conf):
    cmd = " && ".join(c for c in (conf.get("setup"), conf["test"]) if c)
    timeout = int(conf.get("timeout", 900))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run = RUNS / f"{card['repo'].split('/')[-1]}-{card['pr']}-{card['head'][:7]}-{stamp}"
    run.mkdir(parents=True, exist_ok=True)
    wt = run / "src"
    git(path, "worktree", "prune")
    git(path, "fetch", "-q", "origin", f"refs/pull/{card['pr']}/head")
    if git(path, "rev-parse", "FETCH_HEAD") != card["head"]:
        raise Stop("从 GitHub 取到的 PR 版本和卡片上的不一致")
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
        tail = "\n".join(out.strip().splitlines()[-5:])
        raise Stop(f"测试没过（日志 {run / 'test.log'}）：\n{tail}")
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


def cmd_merge(ref):
    card = find_card(ref)
    rules = mo.load_rules()
    conf = act_repos(rules).get(card["repo"].split("/")[-1])
    if not conf:
        raise Stop("这个仓库还没开飞书合并（merge_rules.json 的 act.repos）")
    path, main = project_of(card["repo"].split("/")[-1])
    ci = bool(conf.get("ci"))
    check_pr(card, rules, main, ci)
    git(path, "fetch", "-q", "origin")
    ahead = git(path, "rev-list", "--count", f"origin/{main}..{main}")
    if ahead != "0":
        raise Stop(f"本机主目录的 {main} 有 {ahead} 个没推的提交，按《工程流程》第 5 节要先由你决定推不推")
    if ci:
        tested = "CI 通过"
    elif not conf.get("test"):
        raise Stop("这个仓库没登记 CI，也没登记测试命令（merge_rules.json 的 act.repos）")
    else:
        run = run_tests(path, card, conf)
        tested = f"测试通过，日志在 {run / 'test.log'}"
        check_pr(card, rules, main)                   # 测试要几分钟，合并前再读一次
    mr = subprocess.run([mo.GH, "pr", "merge", str(card["pr"]), "--repo", card["repo"], "--squash",
                         "--match-head-commit", card["head"]], capture_output=True, text=True, timeout=mo.TIMEOUT)
    after = mo.gh_json(["pr", "view", str(card["pr"]), "--repo", card["repo"], "--json", "state,mergeCommit"], "合并后回读")
    if after.get("state") != "MERGED":
        why = ((mr.stderr or mr.stdout or "").strip().splitlines() or ["没有输出"])[-1]
        raise Stop(f"合并没成（gh：{why[:200]}），回读 PR 还是 {after.get('state')}")
    record_owner(card, "合并")
    sha = ((after.get("mergeCommit") or {}).get("oid") or "")[:7]
    print(f"已合并 {card_ref(card['repo'], card['pr'], card['head'])}（{card['title']}）→ {sha}。{tested}。")
    print(sync_main(path, main) + "。如果要安装或写 CURRENT，交给 Claude 会话。")
    return 0


def cmd_return(ref, reason):
    card = find_card(ref)
    reason = (reason or "").strip()
    if not reason:
        raise Stop("退回要写原因")
    p = mo.gh_json(["pr", "view", str(card["pr"]), "--repo", card["repo"], "--json", "state,headRefOid"], "取状态")
    if p.get("state") != "OPEN":
        raise Stop(f"PR 已经是 {p.get('state')}")
    body = f"常浩在飞书退回（Hermes 代发）：{reason}\n\n针对的版本：`{card['head']}`"
    r = subprocess.run([mo.GH, "pr", "comment", str(card["pr"]), "--repo", card["repo"], "--body", body],
                       capture_output=True, text=True, timeout=mo.TIMEOUT)
    if r.returncode != 0:
        raise Stop("留言没发出去：" + ((r.stderr or "").strip().splitlines()[-1:] or ["gh 失败"])[0])
    record_owner(card, "退回")
    print(f"已在 {card['repo']}#{card['pr']} 留言退回：{reason}。PR 没关，等开 PR 的会话改。")
    return 0


def cmd_cards():
    rows = {(r["repo"], r["pr"], r["head_sha"]): r for r in mo.read_rows()}
    left = [c for c in read_cards()["cards"] if not rows.get((c["repo"], str(c["pr"]), c["head"]), {}).get("owner")]
    if not left:
        print("没有等你决定的 PR 卡片。")
    for c in left:
        print(f"{card_ref(c['repo'], c['pr'], c['head'])}  {c['tier']} {c['suggestion']}  {c['title']}（{c['sent_at'][:16]}）")
    return 0


def main(argv=None):
    a = argparse.ArgumentParser(description="飞书一句话合并：按常浩对卡片的回复合并或退回 PR")
    sub = a.add_subparsers(dest="cmd", required=True)
    sub.add_parser("cards")
    m = sub.add_parser("merge")
    m.add_argument("ref")
    r = sub.add_parser("return")
    r.add_argument("ref")
    r.add_argument("reason", nargs="+")
    a = a.parse_args(argv)
    try:
        if a.cmd == "cards":
            return cmd_cards()
        if a.cmd == "merge":
            return cmd_merge(a.ref)
        return cmd_return(a.ref, " ".join(a.reason))
    except Stop as e:
        print(f"没{'合并' if a.cmd == 'merge' else '退回'}：{e}")
        return 1
    except (mo.GhError, OSError, ValueError, KeyError, subprocess.SubprocessError) as e:
        print(f"没{'合并' if a.cmd == 'merge' else '退回'}：{type(e).__name__}：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
