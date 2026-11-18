#!/usr/bin/env python3
"""影子合并官（#61，《工程流程》第 8 节第 4 条）：只读，只给建议，不合并、不推送、不改 PR。

对名册（projects.json）里活跃仓库的 open PR，按带版本号的 merge_rules.json 给出「建议合并 / 退回 / 问常浩」，
记进 agreement.csv；PR 有了真实结局（常浩合并或关闭）后自动标出是否一致，按风险档（L0–L3）算一致率。
判定思路取自 orca/merge_gate.py（在 claude/37-* 分支）：绑定完整 HEAD/base、敏感路径交给人、有硬性问题一律退回
（不能靠「交给人」绕过）；风险档按 docs/DT-ORC/ARCHITECTURE.md 4.2，路径只能上调风险。

一致怎么算：
  - 只比对同一个 HEAD：判定后 PR 又有新提交，旧判定标「取代」，不计入；新 HEAD 另判一次。
  - 建议合并 ↔ 合并、退回 ↔ 关闭 为一致；反过来为不一致。「问常浩」本来就交给人，标「不计」。
  - 历史回填（backfill）事后才判，看不到当时的检查和冲突状态，单列一致率，不计入放权门槛。
放权门槛（同类 20 次、一致率 ≥90%）只报告达标与否，不自动放权；放权要常浩批准、写成策略记录。

用法：
  merge_officer.py [scan] [--verbose]    判定新的 open PR，并给已关闭的 PR 补上结局（不带子命令就是 scan，定时任务这样跑；成功不输出）；
                                         merge_rules.json 的 act.repos 里的仓库，每个新判定发一张飞书卡片（#67，执行在 merge_act.py）
  merge_officer.py backfill              回填名册仓库的历史 PR（只做一次；已记过的不重复）
  merge_officer.py report [--json]       按风险档报告一致率和是否达标
  merge_officer.py replay --rules 新.json  用记下的 PR 状态按新旧两版规则重判，给出一致率前后对比（只读）

环境变量：DT_ROSTER 名册；DT_MERGE_RULES 规则文件（默认同目录 merge_rules.json）；DT_SHADOW_DIR 记录目录；
DT_GH 换 gh（自测用）；DT_NOW（ISO 时间）指定现在，自测用。
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROSTER = Path(os.environ.get("DT_ROSTER", "~/Documents/ChatGPT/个人/work/digital-twin/projects.json")).expanduser()
RULES = Path(os.environ.get("DT_MERGE_RULES") or HERE / "merge_rules.json")
SHADOW = Path(os.environ.get("DT_SHADOW_DIR", "~/Library/Application Support/DigitalTwin/shadow")).expanduser()
LEDGER = SHADOW / "agreement.csv"
FACTS = SHADOW / "facts.jsonl"
GH = os.environ.get("DT_GH", "gh")
BJ = dt.timezone(dt.timedelta(hours=8))
TIMEOUT = 60

MERGE, RETURN, ASK = "建议合并", "退回", "问常浩"
MERGED, CLOSED = "合并", "关闭"
TIERS = ("L0", "L1", "L2", "L3")
FIELDS = ["evaluated_at", "source", "repo", "pr", "title", "head_sha", "base_sha", "rules_version",
          "tier", "suggestion", "reasons", "outcome", "outcome_at", "outcome_head", "agree", "owner", "owner_at"]
PR_FIELDS = ("number,title,state,isDraft,mergeable,headRefOid,baseRefOid,labels,files,"
             "statusCheckRollup,body,closingIssuesReferences,mergedAt,closedAt")
END_FIELDS = "number,state,headRefOid,mergedAt,closedAt"
SHA = re.compile(r"^[0-9a-f]{40}$")
CLOSES = re.compile(r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\s+(\S+/\S+)?#\d+", re.I)
FAILING = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}


class GhError(Exception):
    """gh 跑不起来、输出不是 JSON 或报错。"""


def now():
    v = os.environ.get("DT_NOW")
    t = dt.datetime.fromisoformat(v) if v else dt.datetime.now(BJ)
    return (t if t.tzinfo else t.replace(tzinfo=BJ)).isoformat(timespec="seconds")


def gh_json(args, what):
    try:
        r = subprocess.run([GH, *args], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        raise GhError(f"{what}：gh 跑不起来（{type(e).__name__}）")
    if r.returncode != 0:
        last = ((r.stderr or r.stdout).strip().splitlines() or ["gh 失败"])[-1]
        raise GhError(f"{what}：{last}"[:300])
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise GhError(f"{what}：gh 输出不是 JSON")


# ---------- 规则与判定（纯函数） ----------

def load_rules(path=None):
    rules = json.loads(Path(path or RULES).read_text(encoding="utf-8"))
    for k in ("version", "sensitive", "l0_paths", "rule_docs", "l1_max_files", "return_when", "threshold"):
        if k not in rules:
            raise ValueError(f"规则文件缺少 {k}")
    return rules


def _any(patterns, p):
    return any(re.search(rx, p, re.I) for rx in patterns)


def facts_of(repo, pr):
    """把 gh 的 PR JSON 收成判定要用的状态快照（也是回放的依据）。"""
    checks = []
    for c in pr.get("statusCheckRollup") or []:
        checks.append(str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper())
    return {
        "repo": repo, "pr": pr["number"], "title": pr.get("title") or "",
        "head_sha": pr.get("headRefOid") or "", "base_sha": pr.get("baseRefOid") or "",
        "draft": bool(pr.get("isDraft")), "mergeable": pr.get("mergeable") or "UNKNOWN",
        "labels": sorted(l.get("name", "") for l in pr.get("labels") or []),
        "paths": sorted(f.get("path", "") for f in pr.get("files") or []),
        "checks": checks,
        "closes": bool(pr.get("closingIssuesReferences")) or bool(CLOSES.search(pr.get("body") or "")),
    }


def tier_of(repo, paths, rules):
    name = repo.split("/")[-1]
    sens = rules["sensitive"] + rules.get("repo_sensitive", {}).get(name, [])
    if any(_any(sens, p) for p in paths):
        return "L3"
    if paths and all(_any(rules["l0_paths"], p) and not _any(rules["rule_docs"], p) for p in paths):
        return "L0"
    return "L1" if len(paths) <= rules["l1_max_files"] else "L2"


def evaluate(f, rules):
    """返回 (风险档, 建议, 理由列表)；不改动入参。有退回理由时一律退回。"""
    ret, ask = [], []
    rw = rules["return_when"]
    if not (SHA.match(f.get("head_sha") or "") and SHA.match(f.get("base_sha") or "")):
        ret.append("缺少完整 40 位 HEAD/base SHA")
    paths = f.get("paths") or []
    if not paths:
        ret.append("没有改动文件")
    bad = [p for p in paths if not p or p.startswith("/") or ".." in p.split("/")]
    if bad:
        ret.append("改动路径不合法：" + "、".join(bad[:3]))
    tier = tier_of(f["repo"], paths, rules)
    if rw.get("draft") and f.get("draft"):
        ret.append("还是草稿")
    if rw.get("conflicting") and f.get("mergeable") == "CONFLICTING":
        ret.append("和 base 有冲突")
    failing = [c for c in f.get("checks") or [] if c in FAILING]
    if rw.get("failing_checks") and failing:
        ret.append(f"检查没过（{len(failing)} 项）")
    if rw.get("missing_closes") and not f.get("closes"):
        ret.append("正文没有 Closes #号")
    if tier == "L3":
        name = f["repo"].split("/")[-1]
        sens = rules["sensitive"] + rules.get("repo_sensitive", {}).get(name, [])
        ask.append("改动敏感路径：" + "、".join([p for p in paths if _any(sens, p)][:5]))
    labels = sorted(set(f.get("labels") or []) & set(rules.get("ask_labels") or []))
    if labels:
        ask.append("标签 " + "、".join(labels))
    if ret:
        return tier, RETURN, ret + ask
    if ask:
        return tier, ASK, ask
    return tier, MERGE, []


def judge(suggestion, head, outcome, outcome_head):
    """一个判定和真实结局比：是 / 否 / 不计（问常浩）/ 取代（之后又有新提交）/ 空（还没结局）。"""
    if outcome not in (MERGED, CLOSED):
        return ""
    if head != outcome_head:
        return "取代"
    if suggestion == ASK:
        return "不计"
    return "是" if (suggestion == MERGE) == (outcome == MERGED) else "否"


CUSTODY = "custody"   # owner=custody：托管合并（#136），不计入影子一致率、不作为升档证据（ARCHITECTURE 5.5）


def rates(rows, threshold):
    """rows 里每条要有 source、tier、agree。按档算 live 与 backfill 的一致率和是否达标。托管合并的行（owner=custody）不算。"""
    rows = [r for r in rows if r.get("owner") != CUSTODY]
    out = {}
    for t in TIERS:
        d = {"tier": t}
        for src in ("live", "backfill"):
            rs = [r for r in rows if r["tier"] == t and r["source"] == src]
            n = sum(1 for r in rs if r["agree"] in ("是", "否"))
            yes = sum(1 for r in rs if r["agree"] == "是")
            d[src] = {"n": n, "agree": yes, "rate": (yes / n) if n else None,
                      "return_agree": sum(1 for r in rs if r["agree"] == "是" and r["suggestion"] == RETURN),
                      "ask": sum(1 for r in rs if r["suggestion"] == ASK),
                      "pending": sum(1 for r in rs if not r["agree"]),
                      "superseded": sum(1 for r in rs if r["agree"] == "取代")}
        lv = d["live"]
        d["qualified"] = lv["n"] >= threshold["min_samples"] and lv["rate"] is not None and lv["rate"] >= threshold["min_rate"]
        out[t] = d
    return out


# ---------- 记录 ----------

def read_rows():
    if not LEDGER.exists():
        return []
    with LEDGER.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def write_rows(rows):
    SHADOW.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=SHADOW, prefix=".agreement-", suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, LEDGER)


def read_facts():
    out = {}
    if FACTS.exists():
        for line in FACTS.read_text(encoding="utf-8").splitlines():
            try:
                f = json.loads(line)
            except ValueError:
                continue
            out[(f["repo"], str(f["pr"]), f["head_sha"])] = f
    return out


def append_facts(facts):
    if not facts:
        return
    SHADOW.mkdir(parents=True, exist_ok=True)
    with FACTS.open("a", encoding="utf-8") as fh:
        for f in facts:
            fh.write(json.dumps(f, ensure_ascii=False, sort_keys=True) + "\n")


def active_repos():
    projects = json.loads(ROSTER.read_text(encoding="utf-8"))["projects"]
    return [p["github"] for p in projects if p.get("category") == "活跃" and p.get("github")]


def new_row(f, rules, source, at):
    tier, sug, why = evaluate(f, rules)
    return {"evaluated_at": at, "source": source, "repo": f["repo"], "pr": str(f["pr"]), "title": f["title"],
            "head_sha": f["head_sha"], "base_sha": f["base_sha"], "rules_version": rules["version"],
            "tier": tier, "suggestion": sug, "reasons": "；".join(why),
            "outcome": "", "outcome_at": "", "outcome_head": "", "agree": ""}


def settle(row, end):
    state = end.get("state")
    if state not in ("MERGED", "CLOSED"):
        return False
    row["outcome"] = MERGED if state == "MERGED" else CLOSED
    row["outcome_at"] = end.get("mergedAt") or end.get("closedAt") or ""
    row["outcome_head"] = end.get("headRefOid") or ""
    row["agree"] = judge(row["suggestion"], row["head_sha"], row["outcome"], row["outcome_head"])
    return True


# ---------- 命令 ----------

def cmd_scan(verbose=False):
    rules = load_rules()
    rows = read_rows()
    seen = {(r["repo"], r["pr"], r["head_sha"]) for r in rows}
    at, errors, added, facts = now(), [], [], []
    for repo in active_repos():
        try:
            prs = gh_json(["pr", "list", "--repo", repo, "--state", "open", "--limit", "100", "--json", PR_FIELDS],
                          f"{repo} 取 open PR")
        except GhError as e:
            errors.append(str(e))
            continue
        for pr in prs:
            f = facts_of(repo, pr)
            if (repo, str(f["pr"]), f["head_sha"]) in seen:
                continue
            row = new_row(f, rules, "live", at)
            rows.append(row)
            added.append(row)
            facts.append(f)
            seen.add((repo, row["pr"], row["head_sha"]))
        open_nums = {str(pr["number"]) for pr in prs}
        pending = [r for r in rows if r["repo"] == repo and not r["outcome"] and r["pr"] not in open_nums]
        if not pending:
            continue
        try:
            ends = gh_json(["pr", "list", "--repo", repo, "--state", "all", "--limit", "300", "--json", END_FIELDS],
                           f"{repo} 取 PR 结局")
        except GhError as e:
            errors.append(str(e))
            continue
        by = {str(e["number"]): e for e in ends}
        for r in pending:
            end = by.get(r["pr"])
            if end is None:
                try:
                    end = gh_json(["pr", "view", r["pr"], "--repo", repo, "--json", END_FIELDS], f"{repo}#{r['pr']} 取结局")
                except GhError as e:
                    errors.append(str(e))
                    continue
            settle(r, end)
    append_facts(facts)
    write_rows(rows)
    if added and not os.environ.get("DT_NO_CARDS"):         # act.repos 里的仓库发卡片，等常浩在飞书决定（#67）
        import merge_act
        by_key = {(f["repo"], str(f["pr"]), f["head_sha"]): f for f in facts}
        errors += merge_act.send_cards(added, by_key)
    if verbose:
        for r in added:
            print(f"{r['repo']}#{r['pr']} {r['tier']} {r['suggestion']}  {r['title'][:40]}"
                  + (f"（{r['reasons']}）" if r["reasons"] else ""))
        print(f"新判定 {len(added)} 条，记录在 {LEDGER}")
    for e in errors:
        print(f"影子合并官：{e}")
    return 1 if errors else 0


def cmd_backfill():
    rules = load_rules()
    rows = read_rows()
    seen = {(r["repo"], r["pr"], r["head_sha"]) for r in rows}
    at, errors, n, facts = now(), [], 0, []
    for repo in active_repos():
        try:
            prs = gh_json(["pr", "list", "--repo", repo, "--state", "all", "--limit", "500", "--json", PR_FIELDS],
                          f"{repo} 取历史 PR")
        except GhError as e:
            errors.append(str(e))
            continue
        for pr in prs:
            if pr.get("state") not in ("MERGED", "CLOSED"):
                continue
            f = facts_of(repo, pr)
            if (repo, str(f["pr"]), f["head_sha"]) in seen:
                continue
            row = new_row(f, rules, "backfill", at)
            settle(row, pr)
            rows.append(row)
            facts.append(f)
            seen.add((repo, row["pr"], row["head_sha"]))
            n += 1
    append_facts(facts)
    write_rows(rows)
    print(f"回填 {n} 条历史 PR，记录在 {LEDGER}")
    for e in errors:
        print(f"影子合并官：{e}")
    return 1 if errors else 0


def _pct(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def format_report(rs, rules, total, custody=0):
    th = rules["threshold"]
    lines = [f"影子合并官一致率（规则 v{rules['version']}；门槛：同类 {th['min_samples']} 次、一致率 ≥{th['min_rate'] * 100:.0f}%；只报告，不自动放权）",
             "", "| 档 | 计入 | 一致 | 一致率 | 其中退回且一致 | 达标 | 问常浩 | 待结局 | 被新提交取代 | 回填（不计入门槛） |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for t in TIERS:
        d = rs[t]
        lv, bf = d["live"], d["backfill"]
        lines.append(f"| {t} | {lv['n']} | {lv['agree']} | {_pct(lv['rate'])} | {lv['return_agree']} | "
                     f"{'达标' if d['qualified'] else '未达标'} | {lv['ask']} | {lv['pending']} | {lv['superseded']} | "
                     f"{bf['agree']}/{bf['n']}（{_pct(bf['rate'])}） |")
    lines += ["", "「其中退回且一致」很少时，一致率主要来自「建议合并 ↔ 合并」；PR 绝大多数会被合并，这时高一致率不说明会拦。",
              f"共 {total} 条判定" + (f"（其中托管合并 {custody} 条，不计入）" if custody else "") + f"；记录：{LEDGER}"]
    return "\n".join(lines)


def cmd_report(as_json=False):
    rules = load_rules()
    rows = read_rows()
    rs = rates(rows, rules["threshold"])
    custody = sum(1 for r in rows if r.get("owner") == CUSTODY)
    if as_json:
        print(json.dumps({"rules_version": rules["version"], "tiers": rs, "total": len(rows), "custody": custody},
                         ensure_ascii=False, indent=2))
    else:
        print(format_report(rs, rules, len(rows), custody))
    return 0


def replay(rows, facts, old, new):
    """用记下的 PR 状态按两版规则重判，返回 (旧一致率, 新一致率, 结论变了的条目)。"""
    before, after, changed = [], [], []
    for r in rows:
        f = facts.get((r["repo"], r["pr"], r["head_sha"]))
        if f is None or r.get("owner") == CUSTODY:
            continue
        pair = []
        for rules, bucket in ((old, before), (new, after)):
            tier, sug, why = evaluate(f, rules)
            bucket.append({"source": r["source"], "tier": tier, "suggestion": sug,
                           "agree": judge(sug, r["head_sha"], r["outcome"], r["outcome_head"])})
            pair.append((tier, sug, "；".join(why)))
        if pair[0][:2] != pair[1][:2]:
            changed.append({"repo": r["repo"], "pr": r["pr"], "outcome": r["outcome"] or "待结局",
                            "before": pair[0][:2], "after": pair[1][:2], "why": pair[1][2]})
    return rates(before, old["threshold"]), rates(after, new["threshold"]), changed


def cmd_replay(new_path):
    old, new = load_rules(), load_rules(new_path)
    b, a, changed = replay(read_rows(), read_facts(), old, new)
    print(f"回放：规则 v{old['version']} → v{new['version']}（同一批 PR 状态重判）\n")
    print("| 档 | 实时 前 | 实时 后 | 达标 前 → 后 | 回填 前 | 回填 后 |")
    print("|---|---|---|---|---|---|")
    for t in TIERS:
        fmt = lambda d: f"{d['agree']}/{d['n']}（{_pct(d['rate'])}）"
        print(f"| {t} | {fmt(b[t]['live'])} | {fmt(a[t]['live'])} | "
              f"{'达标' if b[t]['qualified'] else '未达标'} → {'达标' if a[t]['qualified'] else '未达标'} | "
              f"{fmt(b[t]['backfill'])} | {fmt(a[t]['backfill'])} |")
    print(f"\n结论变了 {len(changed)} 条" + ("：" if changed else "。"))
    for c in changed[:30]:
        print(f"- {c['repo']}#{c['pr']}（{c['outcome']}）：{c['before'][0]} {c['before'][1]} → {c['after'][0]} {c['after'][1]}"
              + (f"（{c['why']}）" if c["why"] else ""))
    return 0


def main(argv=None):
    a = argparse.ArgumentParser(description="影子合并官：只读，只给建议")
    a.add_argument("--verbose", action="store_true")
    sub = a.add_subparsers(dest="cmd")
    s = sub.add_parser("scan")
    s.add_argument("--verbose", action="store_true")
    sub.add_parser("backfill")
    r = sub.add_parser("report")
    r.add_argument("--json", action="store_true")
    p = sub.add_parser("replay")
    p.add_argument("--rules", required=True)
    a = a.parse_args(argv)
    try:
        if a.cmd in (None, "scan"):
            return cmd_scan(a.verbose)
        if a.cmd == "backfill":
            return cmd_backfill()
        if a.cmd == "report":
            return cmd_report(a.json)
        return cmd_replay(a.rules)
    except (OSError, ValueError, KeyError) as e:
        print(f"影子合并官没跑成：{type(e).__name__}：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
