"""lark-cli 读列表：翻到底，翻不完就报错（#312）。

为什么要有：lark-cli 读列表有页数上限，到了上限照样退出码 0、不报错，只在 data 里留 has_more=true 和 page_token
（1.0.96，2026-11-22 实测，证据 DigitalTwin/evidence/2026-11-22-312-sweep/）：
- im +chat-messages-list --page-all：默认 10 页（每页 50 条）。同一个群读 30 天，实际 655 条，默认读法只回 487 条。
- im +messages-search --page-all：默认 20 页、最多 40 页；im +chat-members-list --page-all：默认 10 页。
- vc +search、minutes +search、task +get-related-tasks 不带 --page-all 时只回一页（会议、妙记每页 15 条）。
调用方只取 messages / items 就会悄悄少读，日报、学习报告、对账都不知道。

做法：read_all 反复调 fetch，把上一轮给的 page_token 用 --page-token 传回去接着读，直到 has_more 为假。
带 --page-token 接着读和一口气读完逐条一致（同一批证据；+messages-search 每次返回的顺序本来就不固定，集合一致）。
还有下一页却没给翻页标记、翻页标记重复、翻了 ROUNDS 轮还没到底，都报错，不拿半截列表往下用。
只看 has_more：有的命令读完了也带着 page_token（task +get-related-tasks 实测如此）。

托管单 away_sheet.py 自带同样的写法（不依赖别的模块，按最小环境跑），不走这里。"""

ROUNDS = 100   # 最多续翻几轮。一轮是一次 lark-cli 调用（带 --page-all 的一轮最多 10～20 页），100 轮远超一天的量


class Incomplete(Exception):
    pass


def read_all(fetch, what, error=Incomplete, keys=("messages",), rounds=ROUNDS):
    """把一个列表读全，返回 {键: [全部条目]}。

    fetch(extra)：跑一次 lark-cli 读命令并返回它的 data；extra 是要追加的参数（第一轮是 []，之后是 ["--page-token", 标记]）。
    what：报错时说在读什么。error：没读全时抛哪种异常（各脚本传自己的 Fail，沿用它们已有的失败处理）。
    keys：data 里要攒起来的列表字段。"""
    out, token, seen = {k: [] for k in keys}, None, set()
    for _ in range(rounds):
        data = fetch(["--page-token", token] if token else [])
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise error(f"{what}：lark-cli 返回的不是预想的样子，没法判断读没读全")
        for k in keys:
            out[k] += data.get(k) or []
        if not data.get("has_more"):
            return out
        token = data.get("page_token")
        if not token:
            raise error(f"{what}：还有下一页，但飞书没给翻页标记，没读全")
        if token in seen:
            raise error(f"{what}：翻页标记和前面重复，没读全")
        seen.add(token)
    raise error(f"{what}：翻了 {rounds} 轮还没到底，没读全，不截断着用")
