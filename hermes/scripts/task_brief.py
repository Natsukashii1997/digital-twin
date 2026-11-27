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


def mismatch(text, executor):
    """「交给谁」只点名了 Claude、Codex 里的一家，而实际派给另一家：返回一句说明；没点名（如「跟随默认执行方」）或对得上返回 None。"""
    named = {k for k in _WORKERS if k in fields(text).get("交给谁", "").lower()}
    if executor in _WORKERS and len(named) == 1 and executor not in named:
        return f"任务说明写交给 {_WORKERS[named.pop()]}，实际要派给 {_WORKERS[executor]}"
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
