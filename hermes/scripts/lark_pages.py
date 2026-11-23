"""lark-cli 读列表：翻到底，翻不完就报错（#312）。

为什么要有：lark-cli 读列表有页数上限，到了上限照样退出码 0、不报错，只在 data 里留 has_more=true 和 page_token
（1.0.96，2026-11-22 实测，证据 DigitalTwin/evidence/2026-11-22-312-sweep/）：
- im +chat-messages-list --page-all：默认 10 页（每页 50 条）。同一个群读 30 天，实际 655 条，默认读法只回 487 条。
- im +messages-search --page-all：默认 20 页、最多 40 页；im +chat-members-list --page-all：默认 10 页。
- vc +search、minutes +search、task +get-related-tasks 不带 --page-all 时只回一页（会议、妙记每页 15 条）。
调用方只取 messages / items 就会悄悄少读，日报、学习报告、对账都不知道。

做法：read_all 反复调 fetch，把上一轮给的 page_token 用 --page-token 传回去接着读，直到 has_more 是 False。
带 --page-token 接着读和一口气读完逐条一致（同一批证据；+messages-search 每次返回的顺序本来就不固定，集合一致）。
只有「返回里明写着 has_more=false，要的列表字段也都是列表」才算读完；别的一律报错，不拿没法证明读全的列表往下用（PR #335 审查 P1-01）：
返回不是对象、要的列表字段没有或不是列表、has_more 没有或不是布尔值、还有下一页却没给翻页标记（或标记不是非空字符串）、
翻页标记重复、翻了 ROUNDS 轮还没到底。上面六个命令在空结果时也都带 has_more=false 和空列表（实测，claude-08），所以空结果不会被误判。
只看 has_more，不看有没有 page_token：有的命令读完了也带着 page_token（vc +search、minutes +search、task +get-related-tasks 实测如此）。

托管单 away_sheet.py 自带同样的写法（不依赖别的模块，按最小环境跑），不走这里。"""

ROUNDS = 100   # 最多续翻几轮。一轮是一次 lark-cli 调用（带 --page-all 的一轮最多 10～20 页），100 轮远超一天的量


class Incomplete(Exception):
    pass


def read_all(fetch, what, error=Incomplete, keys=("messages",), optional=(), rounds=ROUNDS, exhausted=None):
    """把一个列表读全，返回 {键: [全部条目]}。

    fetch(extra)：跑一次 lark-cli 读命令并返回它的 data；extra 是要追加的参数（第一轮是 []，之后是 ["--page-token", 标记]）。
    what：报错时说在读什么。error：没法证明读全时抛哪种异常（各脚本传自己的 Fail，沿用它们已有的失败处理）。
    keys：data 里要攒起来的列表字段，每一轮都必须有、必须是列表。
    optional：可以没有（或是 null）的列表字段，有就必须是列表（群成员的 truncations）。
    exhausted：翻到 rounds 轮上限时抛哪种异常（连得上、确定读不全，#338 出口账本要单独认出来）；不给就用 error。"""
    out, token, seen = {k: [] for k in (*keys, *optional)}, None, set()
    for _ in range(rounds):
        data = fetch(["--page-token", token] if token else [])
        if not isinstance(data, dict):
            raise error(f"{what}：lark-cli 返回的不是预想的样子，没法确认读没读全")
        for k in keys:
            if not isinstance(data.get(k), list):
                raise error(f"{what}：返回里没有 {k} 列表，没法确认读没读全")
            out[k] += data[k]
        for k in optional:
            if data.get(k) is not None and not isinstance(data[k], list):
                raise error(f"{what}：返回里的 {k} 不是列表，没法确认读没读全")
            out[k] += data.get(k) or []
        more = data.get("has_more")
        if more is False:
            return out
        if more is not True:
            raise error(f"{what}：返回里没有有效的 has_more，没法确认读没读全")
        token = data.get("page_token")
        if not isinstance(token, str) or not token:
            raise error(f"{what}：还有下一页，但飞书没给翻页标记，没读全")
        if token in seen:
            raise error(f"{what}：翻页标记和前面重复，没读全")
        seen.add(token)
    raise (exhausted or error)(f"{what}：翻了 {rounds} 轮还没到底，没读全，不截断着用")
