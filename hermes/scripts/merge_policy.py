#!/usr/bin/env python3
"""合并策略记录与账本（#257，ADR-016）：按策略合并的授权来源、开关、交出登记和合并账本。只读写本机文件，不碰 GitHub。

常浩 2026-11-20 定：L0–L2 的 PR 由合并执行器（merge_act.py）按策略直接合并，留记录、日报汇报；影子门槛废止。
授权不再是「常浩点过的一张卡片」（owner_direct），而是这里的一条策略记录（owner_approved_policy）：

- 策略内容就是 merge_rules.json 里决定「哪些能自动合」的那几项（scope()：风险档、覆盖仓库、例外和敏感路径、上限等），
  只此一份；策略记录绑定它的 SHA-256。规则文件里这些内容变了，哈希对不上，策略自动失效（回到发卡片），要常浩重签。
- 签：常浩在猛鬼兽私聊发「开自动合并 <范围哈希前 12 位>」，Hermes 调 sign --message-id；这里回读核验发送人、私聊、
  时效（twin_identity.verify_message，同 ARCHITECTURE 5.2a (b)），一条消息只用一次。
- 关：任何入口都可以（off）；关了立即回到发卡片。重新开要再签一次。
- 威胁模型同 5.2a：防模型自报、文本冒充、重放；不防同一 macOS 用户下的恶意本地进程。

文件（DT_MERGE_DIR，默认 ~/Library/Application Support/DigitalTwin/merge/，目录 700、文件 600）：
  policy.json         策略记录：enabled、signature（范围哈希、规则版本、签署消息、签署人、时间）、用过的消息、最近 50 条开关记录
  auto-merges.jsonl   合并账本，只追加。每次按策略合并先记一行 intent（要合），再记结果，同一个 id：
                        merged   合并接口回执说「这次合上了」并给了合并提交号，回读对得上（只有这种算按策略合并、能用撤回口令）
                        failed   确定没合：请求没发出去，或 GitHub 明确拒绝且回读 PR 还开着
                        unsure   请求发出去了、没拿到确定结果（超时、断网、回读失败、没归类的报错）
                      只有 intent（进程中途没了）和 unsure 都算结果不明：不重发，合并官巡视时只读核对，核对后再记一行：
                        merged_unknown  PR 已合并，但证明不了是这次合的（别处也可能合了它）：不算按策略合并，不能用撤回口令
                        not_merged      等够时间后 PR 仍没合并（或已关闭、换了版本）：交出登记作废，要合得重新交出
                      另有 revert_intent（要开撤回 PR）、revert_opened（撤回 PR 已开）、bypass（PR 已合并但没经合并执行器）三种单行记录。
  handoffs.json       交出登记：会话调 merge_act.py auto 时记下「仓库、PR、完整 HEAD、谁、什么时候」。绑定版本：之后 PR 有新提交，登记作废。
  auto.lock           合并锁：按策略合并、合并官巡视一次只跑一个（上限判断和写账本在同一把锁里），一轮可能占几分钟。
  policy.lock         开关锁：只在读改写 policy.json 的那一下持有。开、关不等合并锁：巡视正跑着，「关自动合并」也立刻生效；
                      merge_act 在发合并命令之前再读一次开关。

用法：
  merge_policy.py status [--json]          现在开没开、有效没有、今天合了几个、等着的交出、结果不明的；没签时给出要发的口令
  merge_policy.py sign --message-id <id>   按常浩那条私聊消息签并开启
  merge_policy.py off [--source 来源] [--reason 原因]
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import merge_officer as mo  # noqa: E402

SCHEMA = "dt-merge-policy/1"
POLICY_TYPE = "owner_approved_policy"
SIGN_TEXT = re.compile(r"^开自动合并\s+([0-9a-fA-F]{12})$")
PUNCT = " \t\r\n　。.!！~～"
SCOPE_KEYS = ("sensitive", "repo_sensitive", "l0_paths", "rule_docs", "direction_docs", "l1_max_files", "ask_labels", "auto")
HISTORY_KEEP = 50
USED_KEEP_DAYS = 30
LOCK_WAIT = float(os.environ.get("DT_MERGE_LOCK_WAIT", "180"))      # 等合并锁最多几秒（自测时调小）
DONE = ("merged", "failed", "unsure", "merged_unknown", "not_merged")
SIG_FIELDS = ("scope_sha256", "rules_version", "message_id", "open_id", "signed_at")


class Rejected(Exception):
    """签不了、状态文件有问题。"""


def home():
    return Path(os.environ.get("DT_MERGE_DIR", "~/Library/Application Support/DigitalTwin/merge")).expanduser()


def _write(path, text):
    """同目录临时文件（600）→ fsync → rename。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(path))


def _read_json(path, empty):
    """文件不在给 empty()；在但读不出来抛 Rejected（不当成空的：策略、交出登记读坏了都按「没有授权」处理）。"""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty()
    except (OSError, ValueError) as e:
        raise Rejected(f"{path} 读不出来（{type(e).__name__}），要人工核对")
    if not isinstance(d, dict):
        raise Rejected(f"{path} 内容不是对象，要人工核对")
    return d


@contextlib.contextmanager
def locked(wait=None, name="auto.lock"):
    """合并锁（默认）或开关锁（name="policy.lock"）。等不到抛 Rejected（调用方按「这一轮没做」处理）。"""
    wait = LOCK_WAIT if wait is None else wait
    home().mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(home() / name), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Rejected(f"{'另一个按策略合并正在跑' if name == 'auto.lock' else '策略记录正被另一个进程改'}（等锁超过 {wait:g} 秒）")
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)


# ---------- 授权范围 ----------

def scope(rules):
    """决定「哪些 PR 能按策略合并」的全部内容：改了其中任何一项都要常浩重签。"""
    out = {k: rules.get(k) for k in SCOPE_KEYS}
    out["auto"] = {k: v for k, v in (rules.get("auto") or {}).items() if k != "note"}     # 说明文字不算范围
    out["repos"] = (rules.get("act") or {}).get("repos") or {}
    return out


def scope_sha(rules):
    return hashlib.sha256(json.dumps(scope(rules), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def auto_conf(rules):
    return rules.get("auto") or {}


# ---------- 策略记录 ----------

def _empty_state():
    return {"schema": SCHEMA, "enabled": False, "signature": None, "used_messages": {}, "history": []}


def load_state():
    d = _read_json(home() / "policy.json", _empty_state)
    if d.get("schema") != SCHEMA or not isinstance(d.get("enabled"), bool):
        raise Rejected("policy.json 结构不对，要人工核对")
    return d


def _save_state(d, event, source, note=""):
    at = mo.now()
    d["history"] = (d.get("history") or [])[-(HISTORY_KEEP - 1):] + [{"at": at, "event": event, "source": source, "note": note}]
    old = (dt.datetime.fromisoformat(at) - dt.timedelta(days=USED_KEEP_DAYS)).isoformat()
    d["used_messages"] = {k: v for k, v in (d.get("used_messages") or {}).items() if v.get("at", "") >= old}
    d["updated_at"] = at
    _write(home() / "policy.json", json.dumps(d, ensure_ascii=False, indent=2) + "\n")


def active(rules):
    """现在能不能按策略合并：返回 (能不能, 一句原因, 签署记录)。任何读不出、对不上都是不能。"""
    try:
        d = load_state()
    except Rejected as e:
        return False, str(e), None
    sig = d.get("signature")
    if not d["enabled"]:
        return False, "自动合并没开" + ("（签过，已关）" if sig else "（还没签）"), sig
    if (not isinstance(sig, dict) or sig.get("type") != POLICY_TYPE
            or not all(isinstance(sig.get(k), str) and sig[k] for k in SIG_FIELDS)):
        return False, "策略记录不完整（签署记录缺字段或类型不对），要常浩重签", None
    if sig["scope_sha256"] != scope_sha(rules):
        return False, "合并规则在签过之后改了（范围哈希对不上），要常浩重签", sig
    return True, "", sig


def owner_config():
    import twin_state as T   # 只用它读本机受控配置（常浩的 open_id、和猛鬼兽的私聊号）
    try:
        return T.owner_open_id(), T.owner_chat_id()
    except T.StateError as e:
        raise Rejected(str(e))


def fetch_message(message_id):
    import twin_identity as I
    return I.lark_fetch(message_id, with_text=True)


def verify_owner_message(message_id):
    """常浩本人、和猛鬼兽的私聊、10 分钟内。返回回读到的消息（带 text、sender_open_id）。"""
    import twin_identity as I
    owner, chat = owner_config()
    box = {}

    def fetch(mid):
        box["m"] = fetch_message(mid)
        return box["m"]

    now = dt.datetime.fromisoformat(mo.now()).astimezone()    # lark-cli 的 create_time 是本机时区
    v = I.verify_message(message_id, owner, fetch=fetch, now=now, owner_chat_id=chat)
    if not v["ok"]:
        raise Rejected(f"身份核实不通过：{v['reason']}")
    return box["m"]


def do_sign(message_id, rules=None):
    rules = rules or mo.load_rules()
    if not message_id:
        raise Rejected("要带 --message-id（常浩发「开自动合并 <范围哈希前 12 位>」的那条私聊消息）")
    if not auto_conf(rules).get("tiers"):
        raise Rejected("合并规则里没有 auto 一节，这一版规则不支持按策略合并")
    m = verify_owner_message(message_id)              # 联网回读放在锁外
    cmd = SIGN_TEXT.match((m.get("text") or "").strip(PUNCT))
    if not cmd:
        raise Rejected("这条消息不是开启口令（应为「开自动合并 <范围哈希前 12 位>」）")
    sha = scope_sha(rules)
    if cmd.group(1).lower() != sha[:12]:
        raise Rejected(f"哈希对不上：现在的授权范围是 {sha[:12]}，口令里是 {cmd.group(1).lower()}（规则改过，或抄错了）")
    with locked(10, "policy.lock"):
        d = load_state()
        if message_id in (d.get("used_messages") or {}):
            raise Rejected("这条飞书消息已经用过：一条消息只开一次，要再开请常浩再说一次")
        at = mo.now()
        d.setdefault("used_messages", {})[message_id] = {"at": at, "purpose": "开自动合并"}
        d["signature"] = {"type": POLICY_TYPE, "scope_sha256": sha, "rules_version": rules["version"],
                          "message_id": message_id, "open_id": m.get("sender_open_id"), "signed_at": at}
        d["enabled"] = True
        _save_state(d, "on", "飞书私聊", f"范围 {sha[:12]}，规则 v{rules['version']}")
    return d["signature"]


def do_off(source, reason=""):
    """关。只拿开关锁，不等合并锁：巡视或合并正跑着也立刻关上，正在核对的那个 PR 在发合并命令前会再读一次开关。"""
    with locked(10, "policy.lock"):
        d = load_state()
        was = d["enabled"]
        d["enabled"] = False
        _save_state(d, "off", source or "未注明", reason)
    return was


# ---------- 账本 ----------

def ledger_path():
    return home() / "auto-merges.jsonl"


def append(rec):
    """追加一行。O_APPEND 一次写完；调用方在合并锁里（bypass 记录除外，它只是旁注）。"""
    home().mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps({"schema": "dt-merge-auto/1", "at": mo.now(), **rec}, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(str(ledger_path()), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def lines(path=None):
    """账本原样的每一行（坏行跳过并计数）。返回 (记录列表, 坏行数)。path：读别处的账本（日报取数、测试）。"""
    out, bad = [], 0
    try:
        text = (path or ledger_path()).read_text(encoding="utf-8")
    except FileNotFoundError:
        return out, bad
    for line in text.splitlines():
        try:
            r = json.loads(line)
            if not isinstance(r, dict) or not r.get("event"):
                raise ValueError
            out.append(r)
        except ValueError:
            bad += 1
    return out, bad


def merges(path=None):
    """按 id 把 intent 和结果合成一条（结果覆盖同名字段，at 留 intent 的、结果时间放 done_at）。
    还没有结果的 status 是 pending：结果不明。按 intent 的时间排序。"""
    by, revert = {}, {}
    for r in lines(path)[0]:
        if r["event"] == "intent":
            by[r["id"]] = {**r, "status": "pending"}
        elif r["event"] in DONE and r.get("id") in by:
            by[r["id"]].update({k: v for k, v in r.items() if k not in ("at", "event")}, status=r["event"], done_at=r["at"])
        elif r["event"] == "revert_opened":
            revert[r.get("id")] = r
        elif r["event"] == "revert_intent" and r.get("id") in by:
            by[r["id"]]["revert_intent"] = {"at": r["at"], "reason": r.get("reason")}
    for i, r in revert.items():
        if i in by:
            by[i]["revert"] = {k: r.get(k) for k in ("at", "revert_pr", "revert_url", "reason")}
    return sorted(by.values(), key=lambda r: r["at"])


def unsettled(repo=None, pr=None):
    """结果不明的合并：记了要合却没有结果，或结果就是 unsure。这些 PR 不再自动重试。"""
    return [m for m in merges() if m["status"] in ("pending", "unsure")
            and (repo is None or (m["repo"], str(m["pr"])) == (repo, str(pr)))]


def merged_count(day):
    """这一天（北京时间日历日）已经合了几个；结果不明、来源不明的也算，免得顶着上限反复发合并请求。"""
    return sum(1 for m in merges() if m["at"][:10] == day and m["status"] in ("merged", "pending", "unsure", "merged_unknown"))


def find_merged(repo, pr, statuses=("merged",)):
    """账本里这个 PR 最近一条按策略合上的记录（撤回只认它）。statuses 加上 merged_unknown 可以查「合并执行器对它发过请求」。"""
    hits = [m for m in merges() if (m["repo"], str(m["pr"])) == (repo, str(pr)) and m["status"] in statuses]
    return hits[-1] if hits else None


def bypassed():
    return {(r["repo"], str(r["pr"])) for r in lines()[0] if r["event"] == "bypass"}


# ---------- 交出登记 ----------

def _handoff_doc():
    d = _read_json(home() / "handoffs.json", lambda: {"handoffs": []})
    if not isinstance(d.get("handoffs"), list):
        raise Rejected("handoffs.json 结构不对，要人工核对")
    return d


def handoffs():
    return _handoff_doc()["handoffs"]


def _save_handoffs(d):
    _write(home() / "handoffs.json", json.dumps(d, ensure_ascii=False, indent=2) + "\n")


def add_handoff(repo, pr, head, by):
    """登记交出（同一个 PR 只留最新的版本）。调用方持合并锁。"""
    d = _handoff_doc()
    keep = [h for h in d["handoffs"] if (h["repo"], str(h["pr"])) != (repo, str(pr))]
    old = next((h for h in d["handoffs"] if (h["repo"], str(h["pr"]), h["head"]) == (repo, str(pr), head)), None)
    d["handoffs"] = keep + [old or {"repo": repo, "pr": str(pr), "head": head, "by": by, "at": mo.now()}]
    _save_handoffs(d)
    return d["handoffs"][-1]


def drop_handoff(repo, pr):
    d = _handoff_doc()
    keep = [h for h in d["handoffs"] if (h["repo"], str(h["pr"])) != (repo, str(pr))]
    if len(keep) != len(d["handoffs"]):
        d["handoffs"] = keep
        _save_handoffs(d)


def note_once(key, day):
    """同一件事一天只说一次（上限拦下）：第一次返回 True 并记下。调用方持合并锁。"""
    d = _handoff_doc()
    said = d.setdefault("said", {})
    if said.get(key) == day:
        return False
    said[key] = day
    _save_handoffs(d)
    return True


# ---------- 命令 ----------

def status(rules=None):
    rules = rules or mo.load_rules()
    ok, why, sig = active(rules)
    day = mo.now()[:10]
    conf = auto_conf(rules)
    try:
        waiting = handoffs()
    except Rejected as e:
        waiting, why = [], why or str(e)
    return {"active": ok, "why": why, "signature": sig, "scope_sha256": scope_sha(rules), "rules_version": rules["version"],
            "tiers": conf.get("tiers") or [], "repos": sorted(scope(rules)["repos"]), "daily_cap": conf.get("daily_cap"),
            "merged_today": merged_count(day), "handoffs": waiting,
            "unsettled": [{k: m.get(k) for k in ("id", "repo", "pr", "head", "at", "status")} for m in unsettled()],
            "ledger": str(ledger_path()), "bad_lines": lines()[1]}


def describe(st):
    out = []
    if st["active"]:
        sig = st["signature"]
        out.append(f"自动合并开着：{'、'.join(st['tiers'])} 的 PR 按策略合并（规则 v{st['rules_version']}，范围 {st['scope_sha256'][:12]}，"
                   f"{sig['signed_at'][:16]} 签）。今天合了 {st['merged_today']} 个，上限 {st['daily_cap']}。")
    else:
        out.append(f"自动合并没生效：{st['why']}。现在每个 PR 仍发卡片等常浩点。")
        if st["tiers"]:
            out.append(f"要开：常浩在猛鬼兽私聊发「开自动合并 {st['scope_sha256'][:12]}」，范围是 {'、'.join(st['tiers'])}，"
                       f"仓库 {'、'.join(st['repos'])}，每天最多 {st['daily_cap']} 个。")
    for h in st["handoffs"]:
        out.append(f"等着合：{h['repo']}#{h['pr']}@{h['head'][:7]}（{h['by']} {h['at'][:16]} 交出）")
    for m in st["unsettled"]:
        out.append(f"结果不明，不再自动重试：{m['repo']}#{m['pr']}@{str(m['head'])[:7]}（{m['at'][:16]}），到 GitHub 上看合上没有")
    if st["bad_lines"]:
        out.append(f"账本有 {st['bad_lines']} 行读不出来：{st['ledger']}")
    return "\n".join(out)


def main(argv=None):
    a = argparse.ArgumentParser(description="合并策略记录：开关、签署、状态")
    sub = a.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    g = sub.add_parser("sign")
    g.add_argument("--message-id", default="")
    o = sub.add_parser("off")
    o.add_argument("--source", default="本机命令")
    o.add_argument("--reason", default="")
    a = a.parse_args(argv)
    try:
        if a.cmd == "status":
            st = status()
            print(json.dumps(st, ensure_ascii=False, indent=2) if a.json else describe(st))
            return 0
        if a.cmd == "sign":
            sig = do_sign(a.message_id)
            print(f"已开自动合并：范围 {sig['scope_sha256'][:12]}，规则 v{sig['rules_version']}。合了什么进日报；要关，回「关自动合并」。")
            return 0
        was = do_off(a.source, a.reason)
        print("已关自动合并：之后每个 PR 发卡片等常浩点。" if was else "自动合并本来就没开。")
        return 0
    except Rejected as e:
        print(f"没办成：{e}")
        return 1
    except (OSError, ValueError, KeyError) as e:
        print(f"没办成：{type(e).__name__}：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
