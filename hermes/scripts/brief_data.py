"""晨报取数：读牛马之家日报接口，整理成给 Hermes 写晨报用的材料（只用标准库）。

用法：python3 brief_data.py [--today YYYY-MM-DD]
输出：Markdown 材料到 stdout。接口读不到时输出一行「取数失败」及原因，退出码 1。
"""
import datetime as dt
import json
import re
import sys
import urllib.request

API = "http://127.0.0.1:5280/api/reports?withSources=1"
STALL_WEEKS = 4      # 挂起满几周进提醒
BLOCK_REPEAT = 3     # 同一阻塞连续出现几份日报进提醒
WEEKDAYS = "一二三四五六日"


def fetch():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(API, timeout=20) as r:
        return json.load(r)


def section(body, head):
    """取「### 四、…」到下一个「### 」之间的正文。"""
    m = re.search(rf"^### {head}、.*?$", body, re.M)
    if not m:
        return ""
    nxt = re.search(r"^### ", body[m.end():], re.M)
    return body[m.end(): m.end() + nxt.start()] if nxt else body[m.end():]


def rows(text):
    """表格行 → 列表；跳过表头。"""
    out = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) >= 4 and cells[0].isdigit():
            out.append(cells)
    return out


def parse_due(due, report_date, today):
    """把「最晚 10/26」「今日」「月底前」等换成 (日期或 None, 说明)。"""
    m = re.search(r"(\d{1,2})/(\d{1,2})", due)
    if m:
        d = dt.date(report_date.year, int(m.group(1)), int(m.group(2)))
        return d, due
    if "今日" in due or "今天" in due:
        return report_date, due
    if "月底" in due:
        nm = (report_date.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        return nm - dt.timedelta(days=1), due
    return None, due


def label(d, today):
    if d is None:
        return ""
    if d < today:
        return f"已过期（{(today - d).days} 天）"
    if d == today:
        return "今天到期"
    if (d - today).days <= 3:
        return f"{(d - today).days} 天后到期"
    return ""


def fmt_date(d):
    return f"{d.month}/{d.day} 周{WEEKDAYS[d.weekday()]}"


def main():
    today = dt.date.today()
    if "--today" in sys.argv:
        today = dt.date.fromisoformat(sys.argv[sys.argv.index("--today") + 1])
    try:
        data = fetch()
    except Exception as e:  # 接口不通时让 cron 带出失败原因
        print(f"取数失败：牛马之家日报接口读不到（{e}）")
        sys.exit(1)

    reports = data["reports"]
    daily = [r for r in reports if r["kind"] == "日报" and r["date"] < today.isoformat()]
    weekly = [r for r in reports if r["kind"] == "周报"]
    stale = [s["role"] for s in data.get("sources", []) if s.get("stale")]

    base = next((r for r in daily if r["body"].strip()), None)
    if not base:
        print("取数失败：没有找到今天之前有正文的日报")
        sys.exit(1)
    bdate = dt.date.fromisoformat(base["date"])
    body = base["body"]
    # 同一篇里出现第二个「### 一、」，说明另一天的日报标题没被识别、并了进来；只取第一份
    parts = [m.start() for m in re.finditer(r"^### 一、", body, re.M)]
    merged = len(parts) > 1
    if merged:
        body = body[: parts[1]]

    print(f"# 晨报材料（今天 {fmt_date(today)}；依据 {fmt_date(bdate)} 日报）\n")

    print("## 行动与阻塞（日报第四段，脚本已算好到期情况）\n")
    print("序号 | 类型 | 事项 | 负责人 | 截止（原文） | 到期判断 | 状态")
    for c in rows(section(body, "四")):
        no, kind, item, owner, due, status = (c + [""] * 6)[:6]
        d, raw = parse_due(due, bdate, today)
        print(f"{no} | {kind} | {item} | {owner} | {raw} | {label(d, today) or '-'} | {status}")

    sug = rows(section(body, "六"))
    if sug:
        print("\n## 日报里的 AI 建议（日报第六段）\n")
        for c in sug:
            print(f"- [{c[1]}] {c[2]}")

    # 阻塞复现：最近几份日报的第四段都出现同一阻塞（按事项前 8 个字比对）
    recent = [r for r in daily if r["body"].strip()][:BLOCK_REPEAT]
    repeats = []
    for c in rows(section(body, "四")):
        if "阻塞" in c[1]:
            key = re.sub(r"\W", "", c[2])[:8]
            hit = sum(key in re.sub(r"\W", "", section(r["body"], "四")) for r in recent)
            if hit >= BLOCK_REPEAT:
                repeats.append(c[2])
    if repeats:
        print(f"\n## 阻塞复现（连续 {BLOCK_REPEAT} 份日报都有）\n")
        for x in repeats:
            print(f"- {x}")

    if weekly:
        w = weekly[0]
        stalls = []
        for m in re.finditer(r"([^、，：（(\n*]{2,20}?)（连续\s*(\d+)\s*周挂起）", w["body"]):
            if int(m.group(2)) >= STALL_WEEKS and m.group(1) not in [s[0] for s in stalls]:
                stalls.append((m.group(1).strip(), int(m.group(2))))
        if stalls:
            print(f"\n## 长期挂起（{w['title']}，满 {STALL_WEEKS} 周）\n")
            for name, n in stalls:
                print(f"- {name}：连续 {n} 周挂起")

    # 日报断档：最近 7 个自然日里的工作日
    have = {r["date"]: bool(r["body"].strip()) for r in reports if r["kind"] == "日报"}
    gaps = []
    for i in range(1, 8):
        d = today - dt.timedelta(days=i)
        if d.weekday() < 5:
            if d.isoformat() not in have:
                gaps.append(f"{fmt_date(d)} 没有日报")
            elif not have[d.isoformat()]:
                gaps.append(f"{fmt_date(d)} 日报正文为空")
    if merged:
        # 混进来的多半就是“没有日报”的那天，合成一条，不重复报
        missing = [g for g in gaps if g.endswith("没有日报")]
        gaps = [g for g in gaps if g not in missing]
        who = "、".join(g.replace(" 没有日报", "") for g in missing) or "某一天"
        gaps.append(f"{who} 的日报标题没被识别，内容混进了 {fmt_date(bdate)} 日报（日报文档里补上日期标题即可）")
    if gaps or stale:
        print("\n## 日报来源状况\n")
        for g in gaps:
            print(f"- {g}")
        if stale:
            print(f"- 接口返回的是缓存旧结果（来源：{'、'.join(stale)}），最新内容可能还没同步")

    print("\n来源：牛马之家日报接口 /api/reports")


if __name__ == "__main__":
    main()
