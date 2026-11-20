#!/usr/bin/env python3
"""秘书代批开关与代批清单签署（#195 批 0B-4b）：秘书开没开、按哪份已签清单开的，只有这里一处真源。

依据：秘书代批规格（docs/秘书代批-规格.md）第 2、3、4、8、9、10 节；常浩 11-18 定：复用方式选「甲」（托管和秘书共用一套底座，
各用各的文件、口令和账本，托管现有行为不改）；签了清单不算开，要常浩私聊「开秘书」才开。
本脚本只管开关、开关来源和清单签署，**不让代批生效**：前闸、后闸、巡视、送达是 0B-4c，在那之前没有任何代码按秘书放行。
不发飞书、不装定时任务；状态文件只有本脚本写。底座（原子写、排他锁、初始化标记、飞书核验、防重放、签署校验）全部复用 custody.py。

用法（最后一行都是一行 JSON：{"ok", "msg", ...}；通过退出码 0，拒绝退出码 1）：
  secretary.py status [--json]               现在的秘书状态 + 清单核验 + 准入判定（只读；--json 给机器读，不带 open_id）
  secretary.py verify                         重算清单全文 SHA-256、重核名册，和签署记录比对（只读）
  secretary.py on --message-id om_x           常浩私聊说「开秘书」开启（回读原文去掉首尾空白和标点后恰好是「开秘书」+ 本人 + 私聊 +
                                              10 分钟内 + 消息没用过 + 清单有效）；
                                              开启时在秘书账本登记这次（secretary_ledger.register_session），账本身份记进 session.ledger.id；
                                              账本所在的库不在、打不开就不开启（LedgerUnavailable）
  secretary.py off --source <标签>            关闭：on → draining；任何来源都可以（常浩私聊「关秘书」由 Hermes 调；关是安全方向）
  secretary.py off --source <标签> --drained  排空完成：draining → off；要本脚本自己读秘书账本证明排空完成（drain_proof），证明不了拒绝
  secretary.py freeze --source <标签> --reason 原因 [--item 未解决事项 ...]   转 frozen（on / draining）；已冻结时只补充事项
  secretary.py unfreeze --message-id om_x    常浩私聊说「解除冻结」：frozen → off（只有这一条路；解除不等于重新开启）
  secretary.py sign --message-id om_x        常浩私聊说「签代批清单 v<版本> <SHA-256 前 12 位>」签 policy.md
  secretary.py revoke --source <标签> [--reason 原因]  撤销当前签署（持久记下，恢复原文件也不再生效）；秘书开着就转排空
Hermes 会话里 --message-id 可以省略，读 HERMES_SESSION_MESSAGE_ID（只是“去飞书查哪条”的指针，真值从飞书回读）。

状态机（state = off | on | draining | frozen，同 custody.STATES）：
  off      → on        只有 on：飞书私聊回读原文去掉首尾空白和标点（custody.clean_text，和托管 custody.parse_on_text 同一处理）后
                       恰好是「开秘书」（中间夹字如「开一下秘书」不算）+ 常浩本人 + 私聊 + 10 分钟内 + 消息没用过 + 清单核验通过。
                       没有结束时刻、没有默认时段：开着直到关、冻结；清单失效（过期、被改、撤销、名册变了）时状态仍是 on，
                       但准入（admission）不过，任何代批都不放行
  on       → draining  off / revoke（任何来源）；排空截止 = 转入时刻 + 清单第 5 节的「排空截止」
  draining → off       只有 off --drained，且 drain_proof 证明排空完成（核账本身份和开启登记 → 作废已出未送达的 →
                       核没有在途、结果不明、对账不清、有原文没账本的）
  draining → frozen    freeze；或到了排空截止还没 --drained，自动转（读的时候按时钟算，同 custody.advance）
  on       → frozen    freeze（冻结是收紧方向，允许）
  frozen   → off       只有 unfreeze（常浩私聊「解除冻结」，规格第 8 节原话；同样去掉首尾空白和标点后恰好是这四个字）；frozen 时 on / off / off --drained 一律拒绝
  每次进入 draining / frozen（关、撤销、冻结、到点自动冻结），释放状态锁之后尽力清账（ledger_cleanup：作废这次已出未送达的决定、
  释放额度）；账本出错不挡关闭，也不盖掉原来的拒绝原因。

状态文件 secretary.json（DT_SECRETARY_FILE，默认 ~/Library/Application Support/DigitalTwin/secretary.json，600）：
  schema "dt-secretary/1"；state；updated_at；
  session（当前这次，off 时为 null）：id（s-YYYYMMDD-HHMMSS-12 位随机十六进制，和历史不重复，账本按它建账）、started_at、
          source{message_id, open_id}、ledger{id}（秘书账本身份 L-16 位十六进制）、policy{version, sha256}、drain_minutes、
          drain{started_at, deadline, source}、frozen{at, reason, items, source}、events[]；
  signature（当前签署，结构同 custody，另加 projects{项目名: 完整仓库名}：签署时清单第 2 节每个项目在已合并名册里的仓库，
          固定项目 → 仓库的身份，之后名册改指向别的仓库就失效）；signed_versions（严格递增、不复用）；used_messages{message_id: {at, purpose}}（防重放）；
  history（结束了的，留最近 30 次）。
  读的时候逐字段校验，任何一处不对都算坏；写是同目录临时文件 → fsync → rename，持 secretary.json.lock 排他锁（最多等 10 秒）；
  第一次写之前先落初始化标记 secretary.json.initialized，之后状态文件不见了不当成第一次用（全部复用 custody 的 load / save / locked）。
  读不了、格式不对、丢了：只读接口给 state=None + error、不准入；写命令全部拒绝（frozen 不能靠写坏或删掉文件解除）。

清单文件 policy.md（DT_SECRETARY_POLICY，默认 ~/Library/Application Support/DigitalTwin/secretary/policy.md），按
docs/代批清单-模板.md 填。字段位置固定、每项恰好一处、引用块（> 开头）不参与：
  开头表：版本（正整数）、有效期（签署日起 N 天，1–366）；
  ## 1. 能批的类：四行「| ① 提问卡 | 是/否 |」「| ② 放弃后重派一次 | 是/否 |」「| ③ 退回 PR | 是/否 |」「| ④ 合并 PR | 是/否 |」；
  ## 2. 项目：「- 项目：<名字>」一行一个、至少一个、不重复；名字必须在已合并 origin/main 名册里，是 inventory 里 sandbox: true 的
          沙盒，或 projects 里 write_allowed 明确为 true 的活跃项目（和 autoloop_grants 派单范围一致）；名册里可代批范围内同一个
          短名对应多个仓库（短名冲突）一律拒绝，不靠字典覆盖挑一个；
  ## 3. 额度：每天代批总数 N 次、每张任务最多代批 N 次、每天合并 N 个（N 在 1–100）、放弃后重派：每张任务 1 次（只能是 1）；
          「每天」按 Asia/Shanghai 自然日；
  ## 4. 合并白名单：表「| owner/仓库 | 触发的工作流 / 外部动作 |」，④ 关时可以空表，④ 开时至少一行；仓库不重复，
          每个都要是第 2 节某个项目签署时在名册里的完整仓库名；可加第三列「触发条件指纹」（0B-4d：wf-16 位十六进制，
          secretary_gate.py fingerprint 现读 main 上 .github 目录算；写「不记」或留空就是没记，④ 对这个仓库一律交常浩；原来的两列表照旧能签）；
  ## 5. 等待与排空：「白天先等：30 分钟」（只能是 30：常浩 11-17 定，规格第 10 节；要改先改规格）、「排空截止：N 小时 / N 分钟」（大于 0、不超过 24 小时）。
  签署时：除模板里两行固定说明（EXEMPT_LINES，逐字相同才豁免）外不许有〔填…〕；文件版本 = 口令版本；版本比签过的都大；
  全文 SHA-256 前 12 位 = 口令；名册核对通过，并把项目 → 仓库的映射记进签署（signature.projects）。verify 每次重算：没签、撤销、
  文件不见、内容和签署时不同、字段读不出、过期、名册读不了或短名冲突（roster）、名册里的项目 → 仓库和签署时不同（roster_changed，
  要换版本重签）→ 无清单。签署只能在 off 时（一次开启只按一份清单走）。

给 0B-4c 的接口：
  python 里 import secretary：read_status(now) 只读，返回 {"state", "error", "session", "policy", "admission", ...}；
  policy 有效时带清单解析出的字段：categories{ask, redispatch, reject, merge}、projects、repos{项目: 完整仓库名（签署时固定的）}、
  limits{day, task, merge_day, redispatch}、whitelist{仓库: 说明}、triggers{仓库: 触发条件指纹或 None}、wait_minutes、drain_minutes。
  admission = {"eligible", "reasons"}：状态能读、是 on、清单有效、会话开启时的清单就是现在这份有效清单才 eligible。
  下游不得只看 state=="on"，要看 admission；它只管开关这一层，凭证、绑定、额度各闸自己核；每次执行前现读，不缓存。
  记账、额度、排空证明在 secretary_ledger.py（复用 custody_ledger 的通用函数）。
威胁模型同 custody：防模型自报、文本冒充、重放；不防同一 macOS 用户下的恶意本地进程。
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import custody as C  # noqa: E402  同目录：底座全部复用

Rejected = C.Rejected
SCHEMA = "dt-secretary/1"
STATES = C.STATES
WHAT = "秘书状态"
DEFAULT_FILE = "~/Library/Application Support/DigitalTwin/secretary.json"
DEFAULT_POLICY = "~/Library/Application Support/DigitalTwin/secretary/policy.md"
HISTORY_KEEP = 30
SIGN_TEXT = re.compile(r"^签代批清单\s*[vV](\d+)\s+([0-9a-fA-F]{12})$")
ON_TEXT = "开秘书"
UNFREEZE_TEXT = "解除冻结"
SESSION_ID = r"s-\d{8}-\d{6}-[0-9a-f]{12}"
WAIT_MINUTES = 30            # 白天先等多久：常浩 11-17 定（规格第 10 节第 1 条），清单里只能写这个数
CATEGORIES = {"ask": "① 提问卡", "redispatch": "② 放弃后重派一次", "reject": "③ 退回 PR", "merge": "④ 合并 PR"}
CATEGORY_ROWS = {"ask": r"①\s*提问卡", "redispatch": r"②\s*放弃后重派一次", "reject": r"③\s*退回\s*PR", "merge": r"④\s*合并\s*PR"}
LIMIT_LINES = {  # 键 → (正则, 叫法)；第 3 节每项恰好一处
    "day": (r"每天代批总数[：:]\s*(\d+)\s*次", "「每天代批总数：N 次」"),
    "task": (r"每张任务最多代批[：:]\s*(\d+)\s*次", "「每张任务最多代批：N 次」"),
    "merge_day": (r"每天合并[：:]\s*(\d+)\s*个", "「每天合并：N 个」"),
    "redispatch": (r"放弃后重派[：:]\s*每张任务\s*(\d+)\s*次", "「放弃后重派：每张任务 1 次」"),
}
PROJECT_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}"
REPO_FULL = r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}"
# 模板（docs/代批清单-模板.md）里提到〔填〕本身的两行固定说明：只有和它们逐字相同的行不算待填项。模板改了这两行，
# test_secretary 用真模板会失败，提醒同步这里。
EXEMPT_LINES = frozenset({
    "> **所有〔填〕都要填掉**；留着任何一处〔填〕，整份清单不生效。",
    "1. 填完全部〔填〕，把文件交给猛鬼兽或亲手放到上面的位置。",
})


# ---------- 路径 ----------
def state_path():
    return Path(os.environ.get("DT_SECRETARY_FILE") or DEFAULT_FILE).expanduser()


def policy_path():
    return Path(os.environ.get("DT_SECRETARY_POLICY") or DEFAULT_POLICY).expanduser()


# ---------- 名册 ----------
class RosterConflict(Exception):
    """名册里可代批范围内同一个短名对应多个仓库：分不清清单里的项目指哪个，一律拒绝（不靠字典覆盖挑一个）。"""


def dispatch_projects():
    """可代批的项目（和 autoloop_grants.allowed_repos 的派单范围一致，但只认已合并的 origin/main 名册，读法同
    custody_ledger.sandbox_projects）：projects 里 category 活跃、write_allowed 明确为 true、有 github 的，加上 inventory 里
    sandbox: true、path 是 github:<owner/repo> 的。缺字段、仓库名不是完整 owner/repo 写法的一律不算（两个来源同一条校验，
    PR #207 第 2 轮 P1-05：否则签名里会存进自己的结构校验不认的仓库名，签完状态文件就读不回来）。返回 {项目名（仓库短名）: 完整仓库名}；读不了抛异常（调用方 fail closed）；
    同一个短名对应多个不同仓库抛 RosterConflict（整份名册都不用：名册本身有歧义，宁可全停也不猜）。"""
    import multica_night as MN
    d = MN.committed_json(MN.ROSTER_FILE)
    fulls = []
    for e in d.get("projects", []):
        gh = e.get("github") if isinstance(e, dict) else None
        if isinstance(gh, str) and re.fullmatch(REPO_FULL, gh) and e.get("write_allowed") is True and e.get("category") == "活跃":
            fulls.append(gh)
    for e in d.get("inventory", []):
        path = str((e or {}).get("path") or "") if isinstance(e, dict) else ""
        full = path[len("github:"):] if path.startswith("github:") else ""
        if isinstance(e, dict) and e.get("sandbox") is True and re.fullmatch(REPO_FULL, full):
            fulls.append(full)
    out, clash = {}, {}
    for full in fulls:
        short = full.split("/")[-1]
        if out.setdefault(short, full) != full:
            clash.setdefault(short, {out[short]}).add(full)
    if clash:
        raise RosterConflict("名册里短名冲突：" + "；".join(f"{k} → {'、'.join(sorted(v))}" for k, v in sorted(clash.items())))
    return out


# ---------- 清单 ----------
def _section(sec, heads, title, errors):
    """取一节的正文行。标题要恰好出现一次（custody.sections 会把同名标题并成一节，所以另数标题行，重复的也拒绝）。"""
    found = [v for k, v in sec.items() if k.startswith(title)]
    if len(found) != 1 or sum(h.startswith(title) for h in heads) != 1:
        errors.append(f"要有且只有一节「{title}」")
        return []
    return found[0]


TRIGGER = r"wf-[0-9a-f]{16}"   # 触发条件指纹（secretary_gate.trigger_fingerprint 算）
NO_TRIGGER = "不记"             # 第三列写「不记」或留空：这个仓库没记指纹，④ 对它一律交常浩（0B-4d）


def parse_whitelist(body, errors):
    """第 4 节的表：表头「| 仓库 | … |」+ 分隔行，之后每行一个仓库；可以没有数据行。返回 ({仓库: 说明}, {仓库: 指纹或 None})。
    两种表头（0B-4d 加第三列，原来的两列表照旧能签、能用）：
      两列「| 仓库 | 合并到 main 触发的工作流 / 外部动作 |」：每行「| owner/仓库 | 说明 |」，读法和原来逐字一样，指纹一律 None；
      三列「| 仓库 | 合并到 main 触发的工作流 / 外部动作 | 触发条件指纹 |」：每行三格，第三格是 wf-16 位十六进制、「不记」或空。
    没记指纹的仓库 ④ 一律交常浩（合并前要现读触发条件和签署时比，规格第 3 节）。"""
    rows = [ln.strip() for ln in body if ln.strip().startswith("|")]
    if len(rows) < 2 or not re.match(r"^\|\s*仓库\s*\|", rows[0]) or not re.fullmatch(r"\|(\s*:?-{3,}:?\s*\|)+", rows[1]):
        errors.append("「## 4. 合并白名单」要有一张表，表头是「| 仓库 | 合并到 main 触发的工作流 / 外部动作 | 触发条件指纹 |」")
        return None, None
    three = bool(re.fullmatch(r"\|[^|]*\|[^|]*\|\s*触发条件指纹\s*\|", rows[0]))
    out, triggers = {}, {}
    for ln in rows[2:]:
        if three:
            m = re.fullmatch(rf"\|\s*({REPO_FULL})\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|", ln)
            fp = m.group(3) if m else ""
            if m and fp not in ("", NO_TRIGGER) and not re.fullmatch(TRIGGER, fp):
                errors.append(f"合并白名单这一行的触发条件指纹看不懂（要写 wf-16 位十六进制，或「{NO_TRIGGER}」）：{ln}")
                continue
        else:
            m = re.fullmatch(rf"\|\s*({REPO_FULL})\s*\|\s*(.*?)\s*\|", ln)
            fp = ""
        if not m or not m.group(2):
            errors.append("合并白名单这一行看不懂（要写「| owner/仓库 | 触发的工作流和外部动作 |" + (" 触发条件指纹 |" if three else "")
                          + f"」）：{ln}")
        elif m.group(1) in out:
            errors.append(f"合并白名单里 {m.group(1)} 写了不止一次")
        else:
            out[m.group(1)] = m.group(2)
            triggers[m.group(1)] = fp if re.fullmatch(TRIGGER, fp) else None
    return out, triggers


def parse_policy(text):
    """读出代批要用的字段（位置固定、每项恰好一处、引用块不参与，见头注释）。只看文件本身，不核名册（check_roster 另核）。
    返回 (字段, 错误列表)。"""
    errors, out = [], {}
    sec = C.sections(text)
    heads = [ln.strip() for ln in text.splitlines() if ln.startswith("## ")]
    C.parse_head(sec["前言"], out, errors)
    one = _section(sec, heads, "## 1. 能批的类", errors)
    cats = {}
    for key, pat in CATEGORY_ROWS.items():
        m = C.only_one(one, rf"^\|\s*{pat}\s*\|\s*(是|否)\s*\|\s*$", f"「{CATEGORIES[key]}」一行（是 / 否）", errors)
        if m:
            cats[key] = m.group(1) == "是"
    if len(cats) == len(CATEGORIES):
        out["categories"] = cats
    two = _section(sec, heads, "## 2. 项目", errors)
    names = [m.group(1) for m in (re.match(r"^\s*[-*]\s*项目[：:]\s*(.*?)\s*$", ln) for ln in two) if m]
    bad = [n for n in names if not re.fullmatch(PROJECT_NAME, n)]
    if not names:
        errors.append("「## 2. 项目」至少要有一行「- 项目：<名字>」")
    elif bad:
        errors.append(f"项目名看不懂：{'、'.join(bad)}（写名册里的仓库名，如 dt-sandbox）")
    elif len(set(names)) != len(names):
        errors.append("「## 2. 项目」里有重复的项目")
    else:
        out["projects"] = names
    three = _section(sec, heads, "## 3. 额度", errors)
    limits = {}
    for key, (pat, what) in LIMIT_LINES.items():
        m = C.only_one(three, pat, what, errors)
        if not m:
            continue
        n = int(m.group(1))
        if key == "redispatch" and n != 1:
            errors.append("「放弃后重派」只能是每张任务 1 次（规格第 8 节定的）")
        elif not 1 <= n <= C.MAX_LIMIT:
            errors.append(f"{what}的 N 要在 1–{C.MAX_LIMIT} 之间")
        else:
            limits[key] = n
    if len(limits) == len(LIMIT_LINES):
        out["limits"] = limits
    wl, triggers = parse_whitelist(_section(sec, heads, "## 4. 合并白名单", errors), errors)
    if wl is not None:
        out["whitelist"], out["triggers"] = wl, triggers
        if cats.get("merge") and not wl:
            errors.append("④ 合并 PR 填了「是」，合并白名单至少要有一行")
    five = _section(sec, heads, "## 5. 等待与排空", errors)
    m = C.only_one(five, r"白天先等[：:]\s*(\d+)\s*分钟", "「白天先等：N 分钟」", errors)
    if m:
        if int(m.group(1)) == WAIT_MINUTES:
            out["wait_minutes"] = int(m.group(1))
        else:
            errors.append(f"「白天先等」只能是 {WAIT_MINUTES} 分钟（常浩 11-17 定；要改先改规格）")
    C.parse_drain(five, out, errors)
    return out, errors


def check_roster(fields, roster):
    """签署时用：清单里的项目都在名册的可代批范围里、白名单的每个仓库都是清单项目在名册里的完整仓库名。返回错误列表。"""
    errors = [f"项目「{p}」不在名册的可代批范围里（要是沙盒，或活跃项目里 write_allowed 为 true 的）"
              for p in fields["projects"] if p not in roster]
    mine = {roster[p] for p in fields["projects"] if p in roster}
    errors += [f"合并白名单里的 {r} 不是第 2 节项目的仓库" for r in fields["whitelist"] if r not in mine]
    return errors


def check_signed_projects(fields, signed, roster):
    """verify 时用：签署时固定的项目 → 仓库（signature.projects）要和清单第 2 节一一对上、白名单仓库都在里面，
    而且现在名册里每个项目还指向同一个仓库。返回 (code, 错误列表)；没问题 code 为 None。"""
    errors = []
    if sorted(signed) != sorted(fields["projects"]):
        errors.append("签署记录里的项目和清单第 2 节对不上")
    errors += [f"合并白名单里的 {r} 不是签署时第 2 节项目的仓库" for r in fields["whitelist"] if r not in set(signed.values())]
    if errors:
        return "invalid", errors
    moved = [f"项目「{p}」签署时是 {r}，现在名册里是 {roster.get(p) or '没有'}" for p, r in sorted(signed.items()) if roster.get(p) != r]
    return ("roster_changed", moved) if moved else (None, [])


def read_policy(path=None):
    return C.read_policy(path or policy_path())


def verify_policy(doc, now=None, path=None):
    """每次使用都重算：没签、撤销、文件不见、内容和签署时不同、读不出字段、过期、名册读不了或对不上 → ok=False。不改任何东西。"""
    now = now or C.now_tz()
    sig = (doc or {}).get("signature")
    base = {"ok": False, "path": str(path or policy_path())}
    if not sig:
        return {**base, "code": "unsigned", "reason": "还没签代批清单"}
    base.update(version=sig["version"], expires_at=sig["expires_at"])
    if sig.get("revoked"):
        r = sig["revoked"]
        return {**base, "code": "revoked", "reason": f"代批清单 v{sig['version']} 已于 {r['at']} 撤销（{r['source']}），"
                                                    "恢复原文件也不再生效；要用就换更高版本重签"}
    try:
        raw, text = read_policy(path)
    except Rejected as e:
        return {**base, "code": "missing", "reason": str(e).replace("策略文件", "代批清单")}
    sha = hashlib.sha256(raw).hexdigest()
    if sha != sig["sha256"]:
        return {**base, "code": "changed", "reason": f"代批清单内容和签署 v{sig['version']} 时不一样（被改过），视为无清单；要用就换版本重签"}
    fields, errors = parse_policy(text)
    if C.unfilled(text, EXEMPT_LINES) or errors or fields.get("version") != sig["version"]:
        return {**base, "code": "invalid", "reason": "代批清单读不出必需字段或版本和签署记录不一致"}
    if now >= C.parse_iso(sig["expires_at"]):
        return {**base, "code": "expired", "reason": f"代批清单 v{sig['version']} 已于 {sig['expires_at']} 过期，要续签（换版本）"}
    try:
        roster = dispatch_projects()
    except Exception as e:                   # noqa: BLE001  名册读不了、短名冲突一律当无清单（fail closed）
        return {**base, "code": "roster", "reason": f"名册读不了或有歧义，核不了清单里的项目（{type(e).__name__}：{e}）"}
    code, bad = check_signed_projects(fields, sig["projects"], roster)
    if code == "invalid":
        return {**base, "code": "invalid", "reason": "签署记录和清单对不上：" + "；".join(bad)}
    if code:
        return {**base, "code": "roster_changed", "reason": "名册里的项目改指向了别的仓库，要换版本重签：" + "；".join(bad)}
    return {"ok": True, "code": "valid", "path": base["path"], "version": sig["version"], "sha256": sha,
            "signed_at": sig["signed_at"], "expires_at": sig["expires_at"], "categories": fields["categories"],
            "projects": fields["projects"], "repos": dict(sig["projects"]), "limits": fields["limits"],
            "whitelist": fields["whitelist"], "triggers": fields["triggers"], "wait_minutes": fields["wait_minutes"], "drain_minutes": fields["drain_minutes"]}


def require_policy(doc, now, path=None):
    v = verify_policy(doc, now, path)
    if not v["ok"]:
        raise Rejected(f"没有有效的代批清单，不能开启：{v['reason']}", "PolicyInvalid")
    return v


# ---------- 状态文件 ----------
def empty_doc():
    return {"schema": SCHEMA, "state": "off", "updated_at": None, "session": None, "signature": None,
            "signed_versions": [], "used_messages": {}, "history": []}


def check_session(s, state):
    C._keys(s, "session", ("id", "started_at", "source", "ledger", "policy", "drain_minutes", "drain", "frozen", "events"))
    C._str(s["id"], "session.id", SESSION_ID)
    C._time(s["started_at"], "session.started_at")
    C._keys(s["source"], "session.source", ("message_id", "open_id"))
    C._str(s["source"]["message_id"], "session.source.message_id", r"om_[A-Za-z0-9]{6,}")
    C._str(s["source"]["open_id"], "session.source.open_id")
    C._keys(s["ledger"], "session.ledger", ("id",))
    C._str(s["ledger"]["id"], "session.ledger.id", r"L-[0-9a-f]{16}")
    C._keys(s["policy"], "session.policy", ("version", "sha256"))
    C._int(s["policy"]["version"], "session.policy.version", 1, 10 ** 9)
    C._str(s["policy"]["sha256"], "session.policy.sha256", C.SHA)
    C._int(s["drain_minutes"], "session.drain_minutes", 1, C.MAX_DRAIN_MINUTES)
    C._need(isinstance(s["events"], list), "session.events 不是列表")
    if s["drain"] is not None:
        C._keys(s["drain"], "session.drain", ("started_at", "deadline", "source"))
        a, b = C._time(s["drain"]["started_at"], "session.drain.started_at"), C._time(s["drain"]["deadline"], "session.drain.deadline")
        C._need(a < b <= a + dt.timedelta(minutes=s["drain_minutes"]), "session.drain 截止时间不对")
        C._str(s["drain"]["source"], "session.drain.source")
    if s["frozen"] is not None:
        C._keys(s["frozen"], "session.frozen", ("at", "reason", "items", "source"))
        C._time(s["frozen"]["at"], "session.frozen.at")
        C._str(s["frozen"]["reason"], "session.frozen.reason")
        C._str(s["frozen"]["source"], "session.frozen.source")
        C._need(isinstance(s["frozen"]["items"], list) and all(isinstance(i, str) for i in s["frozen"]["items"]),
                "session.frozen.items 格式不对")
    want = {"on": (False, False), "draining": (True, False), "frozen": (None, True)}[state]
    for key, need in zip(("drain", "frozen"), want):
        if need is not None:
            C._need((s[key] is not None) == need, f"状态 {state} 和 session.{key} 对不上")


def check_signature(sig):
    """签署记录：custody 的结构 + projects{项目名: 完整仓库名}（必填、非空，键是仓库短名、值的最后一段就是键）。"""
    C._need(isinstance(sig, dict), "signature 不是对象")
    C.check_signature({k: v for k, v in sig.items() if k != "projects"})
    projects = sig.get("projects")
    C._need(isinstance(projects, dict) and projects and all(
        isinstance(k, str) and re.fullmatch(PROJECT_NAME, k) and isinstance(v, str) and re.fullmatch(REPO_FULL, v)
        and v.split("/")[-1] == k for k, v in projects.items()), "signature.projects 格式不对（要是 {项目名: owner/项目名}）")


def check_doc(doc):
    """完整结构校验（同 custody.check_doc）：任何关键字段缺失、类型不对、时间不合法都算坏，读接口给 state=None、写接口全拒。"""
    C._keys(doc, "状态文件", ("schema", "state", "updated_at", "session", "signature", "signed_versions", "used_messages", "history"))
    C._need(doc["schema"] == SCHEMA, f"schema 不是 {SCHEMA}")
    C._need(doc["state"] in STATES, "state 不对")
    if doc["updated_at"] is not None:
        C._time(doc["updated_at"], "updated_at")
    if doc["state"] == "off":
        C._need(doc["session"] is None, "off 却有 session")
    else:
        check_session(doc["session"], doc["state"])
    vs = doc["signed_versions"]
    C._need(isinstance(vs, list) and all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in vs)
            and all(a < b for a, b in zip(vs, vs[1:])), "signed_versions 要是严格递增的正整数")
    if doc["signature"] is not None:
        check_signature(doc["signature"])
        C._need(vs and doc["signature"]["version"] == vs[-1], "signature 版本不是签过的最大版本")
    C._need(isinstance(doc["used_messages"], dict), "used_messages 不是对象")
    for k, v in doc["used_messages"].items():
        C._keys(v, f"used_messages.{k}", ("at", "purpose"))
        C._time(v["at"], f"used_messages.{k}.at")
    C._need(isinstance(doc["history"], list) and all(isinstance(h, dict) for h in doc["history"]), "history 格式不对")
    ids = [C._str(h.get("id"), "history[].id", SESSION_ID) for h in doc["history"]]
    if doc["session"] is not None:
        ids.append(doc["session"]["id"])
    C._need(len(ids) == len(set(ids)), "秘书会话 ID 有重复（当前会话或历史里），账本没法按它区分")
    return doc


def load(path=None):
    return C.load(path or state_path(), check=check_doc, what=WHAT, empty=empty_doc)


# ---------- 状态机 ----------
def advance(doc, now):
    """按时钟算：draining 到排空截止 → frozen（「到点没收完」，fail closed）。原地改 doc，返回是否改了。"""
    s = doc.get("session")
    if doc["state"] == "draining" and now >= C.parse_iso(s["drain"]["deadline"]):
        deadline = C.parse_iso(s["drain"]["deadline"])
        s["frozen"] = {"at": C.iso(deadline), "reason": "排空截止已过，没有收完（没人确认排空完成）", "items": [], "source": "到点（排空截止）"}
        C.event(s, deadline, "draining", "frozen", "到点（排空截止）")
        doc["state"] = "frozen"
        return True
    return False


def prune(doc, now):
    cutoff = now - dt.timedelta(days=C.USED_KEEP_DAYS)
    doc["used_messages"] = {k: v for k, v in doc["used_messages"].items() if C.parse_iso(v["at"]) >= cutoff}
    del doc["history"][:-HISTORY_KEEP]


def transaction(fn, now=None, path=None):
    """持锁：读 → 按时钟推进 → fn(doc) 改 → 落盘（同 custody.transaction）。fn 抛 Rejected 时什么都不写（含消息占用），
    但到点推进照样落盘。落了盘、进入了 draining / frozen，就在锁外清账。返回 (doc, fn 的结果)。"""
    now = now or C.now_tz()
    p = path or state_path()
    saved = {}

    def commit(doc):
        prune(doc, now)
        doc["updated_at"] = C.iso(now)
        try:
            check_doc(doc)                       # 落盘前按读回时的结构校验再核一遍：写不进自己读不回的东西（第 2 轮 P1-05）
        except (C.Bad, ValueError, KeyError, TypeError, AttributeError) as e:
            raise Rejected(f"这次改动后的秘书状态不合结构（{e}），没有写入", "Corrupt")
        C.save(doc, p)

    try:
        with C.locked(p, what=WHAT):
            try:
                doc = load(p)
            except ValueError as e:
                raise Rejected(f"秘书状态文件有问题，拒绝一切改动：{e}", "Corrupt")
            before = doc["state"]
            advanced = advance(doc, now)
            try:
                result = fn(doc)
            except Rejected:
                if advanced:
                    doc = load(p)
                    advance(doc, now)
                    commit(doc)
                    saved["doc"] = (before, doc)
                raise
            commit(doc)
            saved["doc"] = (before, doc)
    finally:
        if "doc" in saved:
            b, d = saved["doc"]
            s = d.get("session")
            if s and d["state"] in ("draining", "frozen") and b != d["state"]:
                ledger_cleanup(s["id"], now)
    return doc, result


def register_ledger(session_id, now):
    """开启时在秘书账本登记这次（同 custody P0-02 做法）：账本不在、打不开就不开启。返回账本身份，记进 session.ledger.id。"""
    import secretary_ledger as SL            # 账本反过来要 import 本脚本，所以在这里才导入
    try:
        with SL.open_store() as st:
            return SL.register_session(st, session_id, now)
    except Exception as e:                   # noqa: BLE001  任何账本失败都不开启
        raise Rejected(f"秘书账本不可用，不开启（{type(e).__name__}：{e}）", "LedgerUnavailable")


def ledger_cleanup(session_id, now):
    """进入排空 / 冻结后，尽力把这次已出未送达的决定作废、释放额度（同 custody.ledger_cleanup）。账本出错不挡关闭。返回 (成功, 说明)。"""
    import secretary_ledger as SL
    try:
        with SL.open_store() as st:
            n = sum(1 for d in SL.decisions(st, session_id) if d["status"] == "issued")
            SL.drain(st, session_id, now=now)
        return True, f"作废 {n} 个已出未送达的决定"
    except Exception as e:                   # noqa: BLE001
        return False, f"{type(e).__name__}：{e}"


def drain_proof(doc, now):
    """排空完成的证明（同 custody.drain_proof）：本脚本自己读秘书账本，核账本身份和开启登记，作废已出未送达的，
    再核对没有在途、结果不明、对账不清、额度没收口、有原文没账本的。账本读不了也算证明不了。返回 (能否证明, 原因)。"""
    import secretary_ledger as SL
    s = doc["session"]
    try:
        with SL.open_store() as st:
            blockers = SL.drain(st, s["id"], s["ledger"]["id"], now)
    except Exception as e:                   # noqa: BLE001
        return False, f"秘书账本证明不了排空完成（{type(e).__name__}：{e}）"
    if blockers:
        return False, "排空还没收完：" + "；".join(blockers)
    return True, ""


def start_drain(doc, now, source, note=""):
    s = doc["session"]
    deadline = now + dt.timedelta(minutes=s["drain_minutes"])
    s["drain"] = {"started_at": C.iso(now), "deadline": C.iso(deadline), "source": source}
    C.event(s, now, "on", "draining", source, note)
    doc["state"] = "draining"
    return C.iso(deadline)


def end_session(doc, now, source, how, **extra):
    s = doc["session"]
    s["ended"] = {"at": C.iso(now), "source": source, "how": how, **extra}
    C.event(s, now, doc["state"], "off", source, how)
    doc["history"].append(s)
    doc["state"], doc["session"] = "off", None


def do_on(message_id, now=None, path=None, policy=None):
    """off → on：私聊回读原文去掉首尾空白和标点（custody.clean_text）后恰好是「开秘书」，其余见头注释。"""
    now = now or C.now_tz()
    if not message_id:
        raise Rejected("开秘书必须带 --message-id（常浩在私聊说「开秘书」的那条消息）", "NotAuthorized")
    m = C.verify_owner_message(message_id, now)      # 联网回读放在锁外；占用登记和状态变化在锁内同一次落盘

    def fn(doc):
        if doc["state"] != "off":
            hint = {"frozen": "秘书已冻结，只有常浩私聊「解除冻结」才能解除，解除前不能重新开启",
                    "draining": "秘书正在排空，排空完成前不能重新开启", "on": "秘书已经开着"}[doc["state"]]
            raise Rejected(hint, "BadTransition")
        C.claim(doc, message_id, "开秘书", now)
        if C.clean_text(m.get("text")) != ON_TEXT:        # 去掉首尾空白和标点后恰好是「开秘书」（同 custody.parse_on_text）
            raise Rejected(f"这条消息不是「{ON_TEXT}」", "BadCommand")
        pol = require_policy(doc, now, policy)
        sid = C.new_session_id(now, doc, prefix="s", what="秘书")
        lid = register_ledger(sid, now)
        doc["state"] = "on"
        doc["session"] = {"id": sid, "started_at": C.iso(now), "source": {"message_id": message_id, "open_id": m["sender_open_id"]},
                          "ledger": {"id": lid}, "policy": {"version": pol["version"], "sha256": pol["sha256"]},
                          "drain_minutes": pol["drain_minutes"], "drain": None, "frozen": None, "events": []}
        C.event(doc["session"], now, "off", "on", "feishu")
        return {"session": sid}

    return transaction(fn, now, path)


def do_off(source, drained=False, now=None, path=None):
    now = now or C.now_tz()

    def fn(doc):
        C.check_label(source)
        st = doc["state"]
        if st == "frozen":
            raise Rejected("秘书已冻结：关不掉也不能宣称排空完成，只有常浩私聊「解除冻结」才能到 off", "BadTransition")
        if drained:
            if st != "draining":
                raise Rejected(f"只有排空中（draining）才能确认排空完成，现在是 {st}", "BadTransition")
            ok, why = drain_proof(doc, now)
            if not ok:
                raise Rejected(why, "DrainUnverified")
            end_session(doc, now, source, "排空完成")
            return {"result": "off"}
        if st == "on":
            return {"result": "draining", "deadline": start_drain(doc, now, source)}
        return {"result": st, "unchanged": True}

    return transaction(fn, now, path)


def do_freeze(source, reason, items=(), now=None, path=None):
    now = now or C.now_tz()

    def fn(doc):
        C.check_label(source)
        if not (reason or "").strip():
            raise Rejected("冻结要写原因（--reason）", "BadCommand")
        st, s = doc["state"], doc.get("session")
        if st == "off":
            raise Rejected("秘书关着，没有可冻结的", "BadTransition")
        if st == "frozen":                  # 只补充未解决事项（去重），状态不变、不能借此解除
            new = [str(i) for i in items if str(i) not in s["frozen"]["items"]]
            if not new:
                return {"result": "frozen", "unchanged": True}
            s["frozen"]["items"] += list(dict.fromkeys(new))
            C.event(s, now, "frozen", "frozen", source, "补充未解决事项：" + "；".join(new))
            return {"result": "frozen", "added": new}
        s["frozen"] = {"at": C.iso(now), "reason": reason.strip(), "items": [str(i) for i in items], "source": source}
        C.event(s, now, st, "frozen", source, reason.strip())
        doc["state"] = "frozen"
        return {"result": "frozen"}

    return transaction(fn, now, path)


def do_unfreeze(message_id, now=None, path=None):
    now = now or C.now_tz()
    if not message_id:
        raise Rejected("解除冻结必须带 --message-id（常浩在私聊说「解除冻结」的那条消息）", "NotAuthorized")
    m = C.verify_owner_message(message_id, now)

    def fn(doc):
        if doc["state"] != "frozen":
            raise Rejected(f"只有冻结（frozen）才需要解除，现在是 {doc['state']}", "BadTransition")
        C.claim(doc, message_id, "解除秘书冻结", now)
        if C.clean_text(m.get("text")) != UNFREEZE_TEXT:  # 去掉首尾空白和标点后恰好是「解除冻结」（同托管）
            raise Rejected(f"这条消息不是「{UNFREEZE_TEXT}」", "BadCommand")
        end_session(doc, now, "feishu", "解除冻结", message_id=message_id, open_id=m["sender_open_id"])
        return {"result": "off"}

    return transaction(fn, now, path)


def do_revoke(source, reason="", now=None, path=None):
    """撤销当前签署：持久记下，之后恢复原文件也不再生效，只能换更高版本重签。收紧方向，任何来源都可以；开着就同时进入排空。"""
    now = now or C.now_tz()

    def fn(doc):
        C.check_label(source)
        sig = doc["signature"]
        if not sig:
            raise Rejected("还没签过代批清单，没有可撤销的", "BadTransition")
        if sig.get("revoked"):
            return {"result": "revoked", "unchanged": True, "version": sig["version"]}
        sig["revoked"] = {"at": C.iso(now), "source": source, "reason": (reason or "").strip()}
        out = {"result": "revoked", "version": sig["version"], "state": doc["state"]}
        if doc["state"] == "on":
            out["deadline"] = start_drain(doc, now, f"撤销清单（{source}）", "撤销清单")
            out["state"] = "draining"
        return out

    return transaction(fn, now, path)


def do_sign(message_id, now=None, path=None, policy=None):
    now = now or C.now_tz()
    if not message_id:
        raise Rejected("签署必须带 --message-id（常浩发「签代批清单 v<版本> <哈希前 12 位>」的那条消息）", "NotAuthorized")
    m = C.verify_owner_message(message_id, now)

    def fn(doc):
        if doc["state"] != "off":
            raise Rejected(f"秘书进行中（{doc['state']}）不换清单：一次开启只按一份清单走，关了再签", "BadTransition")
        C.claim(doc, message_id, "签代批清单", now)
        cmd = SIGN_TEXT.match(C.clean_text(m.get("text")))
        if not cmd:
            raise Rejected("这条消息不是签署口令（应为「签代批清单 v<版本> <SHA-256 前 12 位>」）", "BadCommand")
        want_ver, want_prefix = int(cmd.group(1)), cmd.group(2).lower()
        try:
            raw, text = read_policy(policy)
        except Rejected as e:
            raise Rejected(str(e).replace("策略文件", "代批清单"), e.code)
        lines = C.unfilled(text, EXEMPT_LINES)
        if lines:
            raise Rejected(f"清单还有未填项（〔填…〕，第 {'、'.join(map(str, lines[:10]))} 行），整份不生效", "Unfilled")
        fields, errors = parse_policy(text)
        if errors:
            raise Rejected("清单读不出必需字段：" + "；".join(errors), "PolicyInvalid")
        if fields["version"] != want_ver:
            raise Rejected(f"口令签的是 v{want_ver}，文件里是 v{fields['version']}", "VersionMismatch")
        top = max(doc["signed_versions"], default=0)
        if want_ver <= top:
            raise Rejected(f"版本 v{want_ver} 不比签过的最大版本 v{top} 大：版本单调递增、不复用，改了内容要换新版本号", "VersionReuse")
        sha = hashlib.sha256(raw).hexdigest()
        if sha[:12] != want_prefix:
            raise Rejected(f"哈希对不上：文件全文 SHA-256 前 12 位是 {sha[:12]}，口令是 {want_prefix}（文件可能被改过，或抄错了）",
                           "HashMismatch")
        try:
            roster = dispatch_projects()
        except RosterConflict as e:
            raise Rejected(f"{e}：分不清清单里的项目指哪个仓库，先改名册", "RosterConflict")
        except Exception as e:               # noqa: BLE001
            raise Rejected(f"名册读不了，核不了清单里的项目（{type(e).__name__}：{e}）", "RosterUnavailable")
        bad = check_roster(fields, roster)
        if bad:
            raise Rejected("名册对不上：" + "；".join(bad), "RosterMismatch")
        projects = {p: roster[p] for p in fields["projects"]}          # 固定项目 → 仓库（P1-01）：之后名册改指向就失效
        expires = now + dt.timedelta(days=fields["valid_days"])
        doc["signature"] = {"version": want_ver, "sha256": sha, "message_id": message_id, "open_id": m["sender_open_id"],
                            "signed_at": C.iso(now), "expires_at": C.iso(expires), "valid_days": fields["valid_days"],
                            "projects": projects}
        doc["signed_versions"].append(want_ver)
        return {"version": want_ver, "sha256": sha, "expires_at": C.iso(expires), "projects": projects}

    return transaction(fn, now, path)


# ---------- 只读 ----------
def admission(st):
    """统一的只读准入判定（同 custody.admission）：状态能读、是 on、清单有效、会话开启时的清单就是现在这份有效清单，才 eligible。
    这只是「开关这一层」放不放行；凭证、绑定、额度、合并条件仍由各闸自己核。每次执行前现读，不缓存。"""
    reasons = []
    if st["error"]:
        reasons.append(f"秘书状态读不了：{st['error']}")
    else:
        if st["state"] != "on":
            reasons.append(f"秘书不是开着（{st['state']}）")
        pol, s = st["policy"], st["session"]
        if not pol["ok"]:
            reasons.append(f"代批清单无效：{pol['reason']}")
        elif s and (s["policy"]["version"], s["policy"]["sha256"]) != (pol["version"], pol["sha256"]):
            reasons.append("这次开秘书时的清单和现在的有效清单不是同一份")
    return {"eligible": not reasons, "reasons": reasons}


def read_status(now=None, path=None, policy=None):
    """只读：按时钟算出现在的状态（不落盘），附清单核验和准入判定。读不了状态文件时 error 非空、state 为 None、不准入。"""
    now = now or C.now_tz()
    try:
        doc = load(path)
    except ValueError as e:
        st = {"schema": SCHEMA, "state": None, "error": str(e), "session": None, "policy": None, "checked_at": C.iso(now)}
    else:
        advance(doc, now)
        st = {"schema": SCHEMA, "state": doc["state"], "error": None, "session": C.public_session(doc.get("session")),
              "policy": verify_policy(doc, now, policy), "signed_versions": doc["signed_versions"],
              "last": C.public_session(doc["history"][-1]) if doc["history"] else None, "checked_at": C.iso(now)}
    st["admission"] = admission(st)
    return st


def describe(st):
    short = C.short
    if st["error"]:
        return f"秘书状态读不了：{st['error']}（当作秘书不可用）"
    s, p = st["session"], st["policy"]
    pol = (f"代批清单 v{p['version']} 有效，到 {short(p['expires_at'])}" if p["ok"]
           else "还没签代批清单" if p["code"] == "unsigned" else f"代批清单无效：{p['reason']}")
    if st["state"] == "off":
        return f"秘书关着。{pol}"
    head = f"秘书 {s['id']}：飞书私聊开启于 {short(s['started_at'])}，按清单 v{s['policy']['version']}（{s['policy']['sha256'][:12]}）"
    if st["state"] == "on":
        gate = "" if st["admission"]["eligible"] else "（但准入不过，不代批：" + "；".join(st["admission"]["reasons"]) + "）"
        return f"秘书开着{gate}。{head}。{pol}"
    if st["state"] == "draining":
        return f"秘书排空中（{s['drain']['source']}，{short(s['drain']['started_at'])} 起），截止 {short(s['drain']['deadline'])}。{head}。{pol}"
    f = s["frozen"]
    items = "；".join(f["items"]) if f["items"] else "无明细"
    return (f"秘书已冻结（{short(f['at'])}，{f['reason']}；未解决：{items}）。禁止新代批和重新开启；"
            f"常浩核对后在猛鬼兽私聊说「解除冻结」才回到 off。{head}")


# ---------- 命令行 ----------
def main(argv=None):
    done = C.done
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    sub.add_parser("verify")
    for name in ("on", "unfreeze", "sign"):
        sub.add_parser(name).add_argument("--message-id")
    f = sub.add_parser("off")
    f.add_argument("--source", required=True)
    f.add_argument("--drained", action="store_true")
    z = sub.add_parser("freeze")
    z.add_argument("--source", required=True)
    z.add_argument("--reason", required=True)
    z.add_argument("--item", action="append", default=[])
    r = sub.add_parser("revoke")
    r.add_argument("--source", required=True)
    r.add_argument("--reason", default="")
    a = ap.parse_args(argv)
    mid = getattr(a, "message_id", None) or (os.environ.get("HERMES_SESSION_MESSAGE_ID") if a.cmd in ("on", "unfreeze", "sign") else None)

    if a.cmd == "status":
        st = read_status()
        if a.json:
            print(json.dumps(st, ensure_ascii=False, indent=2))
            sys.exit(0 if not st["error"] else 1)
        done(not st["error"], describe(st), state=st["state"])
    if a.cmd == "verify":
        try:
            doc = load()
        except ValueError as e:
            done(False, f"秘书状态文件有问题：{e}", error="Corrupt")
        v = verify_policy(doc)
        done(v["ok"], f"代批清单 v{v['version']} 有效，到 {C.short(v['expires_at'])}" if v["ok"] else v["reason"],
             **{k: v[k] for k in ("code", "version", "sha256", "expires_at") if k in v})
    try:
        if a.cmd == "on":
            doc, r = do_on(mid)
            done(True, "秘书已开启（关掉要在私聊说「关秘书」）", state="on", **r)
        if a.cmd == "off":
            doc, r = do_off(a.source, drained=a.drained)
            msg = {"draining": f"秘书进入排空，截止 {C.short(r.get('deadline'))}", "off": "排空完成，秘书已关闭"}.get(r["result"])
            if r.get("unchanged"):
                msg = f"秘书本来就是 {r['result']}，没有变化"
            done(True, msg, state=doc["state"], **r)
        if a.cmd == "freeze":
            doc, r = do_freeze(a.source, a.reason, a.item)
            done(True, "秘书已冻结" + ("（本来就冻结着）" if r.get("unchanged") else
                                    f"（补充了 {len(r['added'])} 项未解决事项）" if r.get("added") else ""), state="frozen")
        if a.cmd == "revoke":
            doc, r = do_revoke(a.source, a.reason)
            done(True, f"代批清单 v{r['version']} " + ("本来就撤销了" if r.get("unchanged") else "已撤销，恢复原文件也不再生效")
                 + ("；秘书进入排空" if r.get("deadline") else ""), state=doc["state"])
        if a.cmd == "unfreeze":
            do_unfreeze(mid)
            done(True, "已解除冻结，秘书关闭（off）", state="off")
        if a.cmd == "sign":
            doc, r = do_sign(mid)
            done(True, f"代批清单 v{r['version']} 已签（{r['sha256'][:12]}），到 {C.short(r['expires_at'])}", **r)
    except Rejected as e:
        done(False, str(e), error=e.code)


if __name__ == "__main__":
    main()
