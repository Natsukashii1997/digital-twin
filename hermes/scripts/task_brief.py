"""五要素任务说明的核对（#358，docs/技能架构.md 第 9 节 5b-1）。

派单前确定性地核一遍任务说明写没写全，写法见技能参考库 hermes/skills/dt-shared-reference/references/task-brief.md：
目标、交付物、验收、边界、交给谁五项，每项一行「名字：内容」，内容不能空。认这几种写法：
  目标：…   /   目标: …   /   - **目标**：…   /   # 目标：…   /   标题「## 目标」下面一行写内容
别名：「验收标准」算验收，「范围」算边界（两份旧技能的写法）。

两个入口共用：dispatch.py new（旧车间开单）核五要素和「交给谁」是否就是实际派的那家；autoloop_grants.propose（新底座，
任务单给 dt-worker 看）另核不许夹带受限内容（疑似密钥、Vault 路径、sensitivity 标记），门卫点卡批准时（_decide）再核一遍，
升级前登记的旧提议也过这道检查。受限内容只认得出明显的写法，挡的是误抄，不是完整的敏感信息识别。
不 import 别的模块；门卫目录是手工装的一份副本，重装门卫时要连这个文件一起装（AGENTS 结构表 gatekeeper 一行）。
"""
import re

FIELDS = ("目标", "交付物", "验收", "边界", "交给谁")
ALIASES = {"验收标准": "验收", "范围": "边界"}
_NAMES = "|".join(sorted([*FIELDS, *ALIASES], key=len, reverse=True))
_LINE = re.compile(rf"^\s*(?:[-*>]\s*)?(?:#{{1,6}}\s*)?\**\s*({_NAMES})\s*\**\s*[：:]\s*\**\s*(.*?)\s*$")
_HEADING = re.compile(rf"^\s*#{{1,6}}\s*({_NAMES})\s*$")
_BARE = re.compile(rf"^\s*(?:[-*>]\s*)?\**\s*({_NAMES})\s*\**\s*[：:]?\s*$")   # 只写了名字的一行（「**交付物**」）
_SECRET = re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|gho_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}"
                     r"|xox[abpr]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----")
_RESTRICTED = re.compile(r"Obsidian Vault|^\s*(?:[-*>]\s*)?\**\s*sensitivity\s*\**\s*[:：]", re.M | re.I)
_WORKERS = {"claude": "Claude", "codex": "Codex"}


def fields(text):
    """{名字: 内容}，只收内容不空的；同一项写了几次取第一次。"""
    out, lines = {}, (text or "").splitlines()
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if m:
            name, value = m.groups()
        else:
            h = _HEADING.match(line)
            if not h:
                continue
            name = h.group(1)
            nxt = next((x for x in lines[i + 1:] if x.strip()), "")
            # 标题下面紧跟的是另一个标题、或另一项（「交付物：…」「**交付物**」）：这一项算空，不拿下一项冒充（#405 审查 P1-01）
            value = "" if nxt.strip().startswith("#") or _LINE.match(nxt) or _BARE.match(nxt) else nxt.strip()
        name = ALIASES.get(name, name)
        if value and name not in out:
            out[name] = value
    return out


def missing(text):
    """缺了（或写了但内容空）的要素，按固定顺序。"""
    got = fields(text)
    return [f for f in FIELDS if f not in got]


def leaks(text):
    """夹带的受限内容，给出一句说明（不回显原文）。"""
    out = []
    if _SECRET.search(text or ""):
        out.append("有疑似密钥或令牌")
    if _RESTRICTED.search(text or ""):
        out.append("有 Vault 路径或 sensitivity 标记（受限内容不进工人的任务单）")
    return out


_NEG = re.compile(r"(?:不要|不用|不给|别给|别用|别交给|不交给|不派给|不是|除了|而非|非)\s*(claude|codex)", re.I)
_EITHER = re.compile(r"(claude|codex)[^\n]{0,12}?(?:或者?|还是|/|\bor\b)[^\n]{0,12}?(claude|codex)|都行|都可以|任一|任选|随便", re.I)


def mismatch(text, executor):
    """「交给谁」和实际执行方（claude / codex）对不上时返回一句说明，对得上或判不了返回 None（#405 审查 P2-01）：
    - 先认出被否定的那家（「不要 Claude」「别给 Codex」「除了 Claude」），实际要派的正是它 → 对不上；
    - 否定之后只剩一家 → 要和实际一致；
    - 两家都在、又都没被否定：只有「Claude 或 Codex」「都行」这类明说哪家都可以的才放行，否则看不出交给谁、不放；
    - 两家都没写（「跟随默认执行方」「按派单参数里的执行方」）→ 不判。"""
    if executor not in _WORKERS:
        return None
    who = fields(text).get("交给谁", "")
    named = {k for k in _WORKERS if k in who.lower()}
    neg = {m.lower() for m in _NEG.findall(who)}
    pos = named - neg
    if executor in neg:
        return f"任务说明写不交给 {_WORKERS[executor]}，实际要派给 {_WORKERS[executor]}"
    if len(pos) == 1 and executor not in pos:
        return f"任务说明写交给 {_WORKERS[pos.pop()]}，实际要派给 {_WORKERS[executor]}"
    if len(pos) == 2 and not _EITHER.search(who):
        return f"任务说明的交给谁同时写了 Claude 和 Codex，看不出交给哪家（实际要派给 {_WORKERS[executor]}）"
    return None


def problems(text, worker=False, executor=None):
    """一句一个问题；空列表 = 可以派。worker=True 时另核受限内容（新底座的任务单 dt-worker 读得到）；
    给了 executor（claude / codex）时另核「交给谁」和它对不对得上。"""
    out = []
    lack = missing(text)
    if lack:
        out.append("任务说明缺" + "、".join(lack) + "（五要素：目标、交付物、验收、边界、交给谁，每项一行「名字：内容」）")
    if worker:
        out += leaks(text)
    if executor and mismatch(text, executor):
        out.append(mismatch(text, executor))
    return out
