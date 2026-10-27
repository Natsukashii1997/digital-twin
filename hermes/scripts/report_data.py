"""日报 / 周报取数：给 Hermes 按 T1 方案 A 写日报和周报用的材料（只读，只用标准库）。

用法：python3 report_data.py daily [--date YYYY-MM-DD]     默认写昨天
      python3 report_data.py weekly [--week-start YYYY-MM-DD] 默认上周一
读：并跑日报文档里前一篇的「二、还开着」「三、卡点」（还没有就读台账种子）；
    当天常浩参与的私聊、常浩发过言或被 @ 的群、会议和妙记、日程、飞书任务；项目进展（progress_data.py）。
    周报读本周各篇新日报、上一篇新周报的「六、下周焦点」。
输出：Markdown 到 stdout，第一行是「# 日报材料」「# 周报材料」，或「SKIP：原因」（已写过、周末没动静）。
飞书读不了时输出「取数失败：原因」，退出码 1。
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

LARK = os.environ.get("DT_LARK_CLI", "lark-cli")
PROFILE = "cli_a96f5f9ac63adcd4"              # 猛鬼兽应用，常浩的用户身份
ME = "ou_5ad2ac4359de95dc41b5e89edd68ae6a"     # 常浩
HERMES_BOT = "ou_12bf9332a4a8c513c2b30777ca9def28"  # 和猛鬼兽的私聊不算材料
DAILY_DOC = os.environ.get("DT_DAILY_DOC", "WZyhdQdM0oMk2jxSlOTcJPVKnQg")
WEEKLY_DOC = os.environ.get("DT_WEEKLY_DOC", "QYEhdzNgUof8mtxW0cqcYErgnDc")
SEED = Path(os.environ.get("DT_LEDGER_SEED", "~/Library/Application Support/DigitalTwin/ledger-seed.md")).expanduser()
VOCAB = Path(os.environ.get("DT_VOCAB", Path(__file__).parents[1] / "prompts" / "project_vocab.md")).expanduser()  # 仓库和安装位置都是 scripts 的兄弟目录
PROGRESS = Path(os.environ.get("DT_PROGRESS", Path(__file__).with_name("progress_data.py")))
CLIP = 300          # 每条消息最多保留多少字
WEEKDAYS = "一二三四五六日"
DAILY_HEAD = re.compile(r"^## 📅 (\d{4}-\d{2}-\d{2})", re.M)
WEEKLY_HEAD = re.compile(r"^## 第(\d+)周 (\d{4})\.\d+（(\d{4})\.(\d{2})\.(\d{2})", re.M)


class Fail(Exception):
    pass


def lark(*args):
    r = subprocess.run([LARK, "--profile", PROFILE, *args, "--as", "user"], capture_output=True, text=True, timeout=180)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        raise Fail(f"{args[0]} {args[1]} 输出不是 JSON（{(r.stderr or r.stdout)[:200]}）")
    if not d.get("ok", True):
        raise Fail(f"{args[0]} {args[1]}：{d.get('error', {}).get('message', '未知错误')[:200]}")
    return d.get("data")


def fetch_doc(token):
    return lark("docs", "+fetch", "--doc", token, "--doc-format", "markdown")["document"]["content"]


def entries(content, head):
    """把文档按「## 标题」切成 [(key, 正文)]，key 由 head 正则的第一组给出。"""
    ms = list(head.finditer(content))
    out = []
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(content)
        nxt = re.search(r"^## ", content[m.end():end], re.M)   # 别的二级标题截断
        out.append((m, content[m.start(): m.end() + nxt.start() if nxt else end]))
    return out


def section(body, head):
    m = re.search(rf"^### {head}、.*$", body, re.M)
    if not m:
        return ""
    nxt = re.search(r"^### |^---\s*$", body[m.end():], re.M)
    return body[m.start(): m.end() + nxt.start() if nxt else len(body)].strip()


def wd(d):
    return f"{d.isoformat()}（周{WEEKDAYS[d.weekday()]}）"


def clean(m):
    t = m.get("msg_type")
    c = str(m.get("content") or "")
    if t == "image":
        return "[图片]"
    if t == "sticker":
        return "[表情]"
    if t == "file":
        f = re.search(r'name="([^"]+)"', c)
        return f"[文件：{f.group(1) if f else ''}]"
    if t == "interactive":
        f = re.search(r'title="([^"]+)"', c)
        return f"[卡片：{f.group(1) if f else ''}]"
    c = re.sub(r"!\[Image\]\([^)]*\)", "[图片]", c)
    c = re.sub(r"\s+", " ", c).strip()
    return c[:CLIP] + ("…" if len(c) > CLIP else "")


def pick_chats(msgs):
    """返回 (要的会话 {chat_id: [msg]}, 略过的群数, 略过的私聊说明)。

    私聊：都要（含常浩发给自己的备忘），和猛鬼兽的除外。群和话题群：常浩当天发过言或被 @ 才要。"""
    by = {}
    for m in msgs:
        by.setdefault(m["chat_id"], []).append(m)
    keep, skipped_groups, skipped_p2p = {}, 0, 0
    for cid, ms in by.items():
        if ms[0]["chat_type"] == "p2p":
            partner = (ms[0].get("chat_partner") or {}).get("open_id")
            if partner == HERMES_BOT:
                skipped_p2p += 1
                continue
            keep[cid] = ms
        elif any(x["sender"].get("id") == ME for x in ms) or \
                any(ME == y.get("id") for x in ms for y in x.get("mentions") or []):
            keep[cid] = ms
        else:
            skipped_groups += 1
    return keep, skipped_groups, skipped_p2p


def chat_title(ms):
    if ms[0]["chat_type"] == "p2p":
        if (ms[0].get("chat_partner") or {}).get("open_id") == ME:
            return "常浩发给自己的备忘"
        other = next((x["sender"].get("name") for x in ms if x["sender"].get("id") != ME), None)
        return f"私聊 {other or '（对方当天没说话）'}"
    return f"群「{ms[0].get('chat_name') or ms[0]['chat_id'][-6:]}」"


def day_range(d):
    return f"{d.isoformat()}T00:00:00+08:00", f"{d.isoformat()}T23:59:59+08:00"


def messages(d):
    s, e = day_range(d)
    data = lark("im", "+messages-search", "--start", s, "--end", e, "--page-all", "--page-size", "50",
                "--no-reactions", "--exclude-sender-type", "bot")
    return data.get("messages") or []


def prior_daily(content, d):
    """并跑文档里日期早于 d 的最新一篇；同一天已写过返回 'done'。"""
    best = None
    for m, body in entries(content, DAILY_HEAD):
        day = m.group(1)
        if day == d.isoformat():
            return "done", None
        if day < d.isoformat() and (best is None or day > best[0]):
            best = (day, body)
    return "ok", best


def daily(d):
    content = fetch_doc(DAILY_DOC)
    state, prev = prior_daily(content, d)
    if state == "done":
        print(f"SKIP：{d.isoformat()} 的日报已经在并跑文档里，不重写")
        return
    msgs = messages(d)
    keep, skipped_groups, skipped_p2p = pick_chats(msgs)
    s, e = day_range(d)
    meetings = (lark("vc", "+search", "--start", d.isoformat(), "--end", d.isoformat(), "--participant-ids", ME) or {}).get("items") or []
    minutes = (lark("minutes", "+search", "--start", d.isoformat(), "--end", (d + dt.timedelta(days=1)).isoformat(),
                    "--participant-ids", "me") or {}).get("items") or []
    agenda = lark("calendar", "+agenda", "--start", s, "--end", e) or []
    mine_spoke = sum(1 for m in msgs if m["sender"].get("id") == ME)
    if d.weekday() >= 5 and not mine_spoke and not meetings and not minutes:
        print(f"SKIP：{wd(d)} 是周末，常浩没有发言、没有会议，不生成（不算断档）")
        return

    print(f"# 日报材料：{wd(d)}\n")
    print("## 前一篇的台账\n")
    if prev:
        print(f"依据并跑文档 {prev[0]} 那篇（常浩在文档里改过的，以文档为准）：\n")
        for h in ("二", "三"):
            print(section(prev[1], h) or f"（{h}段为空）", "\n")
    elif SEED.exists():
        print(f"并跑文档里还没有更早的日报，用台账种子（{SEED.name}，常浩 2026-10-27 确认）：\n")
        print(SEED.read_text(encoding="utf-8").strip(), "\n")
    else:
        raise Fail(f"并跑文档里没有前一篇，台账种子 {SEED} 也不存在")

    print(f"## 消息（{len(keep)} 个会话；私聊全收，群只收常浩发言或被 @ 的）\n")
    for cid, ms in sorted(keep.items(), key=lambda kv: kv[1][-1]["create_time"]):
        print(f"### {chat_title(ms)}")
        for m in sorted(ms, key=lambda x: x["create_time"]):
            who = "常浩" if m["sender"].get("id") == ME else m["sender"].get("name") or "?"
            print(f"- {m['create_time'][11:16]} {who}：{clean(m)}")
        print()

    print(f"## 会议（{len(meetings)} 场）和妙记（{len(minutes)} 条）\n")
    for x in meetings:
        print(f"- 会议：{x.get('display_info', '').splitlines()[0]}")
    tokens = [re.search(r"/minutes/(\w+)", x.get("meta_data", {}).get("app_link", "")) for x in minutes]
    tokens = [t.group(1) for t in tokens if t]
    if tokens:
        det = lark("minutes", "+detail", "--minute-tokens", ",".join(tokens), "--summary", "--todo")
        print(json.dumps(det, ensure_ascii=False)[:6000])
    print()

    print(f"## 日程（{len(agenda)} 个；日历有 ≠ 参加，要有消息或纪要佐证才算参加）\n")
    for ev in agenda:
        st = ev.get("start_time", {}).get("datetime", "")[11:16]
        en = ev.get("end_time", {}).get("datetime", "")[11:16]
        org = (ev.get("event_organizer") or {}).get("display_name", "")
        print(f"- {st}-{en} {ev.get('summary', '')}｜组织：{org}｜我的回复：{ev.get('self_rsvp_status', '')}")
    print()

    tasks = []   # 只要常浩是负责人、当天新建或截止在 D-7～D+14 的；只关注的、远期的不算
    for t in (lark("task", "+get-related-tasks", "--include-complete=false") or {}).get("items") or []:
        if not any(x.get("id") == ME and x.get("role") == "assignee" for x in t.get("members") or []):
            continue
        due = (t.get("due") or {}).get("timestamp")
        due = dt.datetime.fromtimestamp(int(due) / 1000).date() if due else None
        if (t.get("created_at") or "")[:10] == d.isoformat() or (due and -7 <= (due - d).days <= 14):
            tasks.append((t, due))
    print(f"## 飞书任务（常浩负责、近期到期或当天新建的未完成任务 {len(tasks)} 个）\n")
    for t, due in tasks:
        print(f"- {t.get('summary', '')}｜截止：{due or '无'}｜新建：{(t.get('created_at') or '')[:10]}")
    print()

    print("## 项目进展（progress_data.py）\n")
    r = subprocess.run([sys.executable, str(PROGRESS), "--date", d.isoformat()], capture_output=True, text=True, timeout=300)
    print(r.stdout.strip() or f"读取失败：{r.stderr.strip()[:200]}", "\n")

    if VOCAB.exists():
        print("## 项目词表\n")
        print(VOCAB.read_text(encoding="utf-8").strip(), "\n")

    print("## 覆盖（写进日报末尾的覆盖行）\n")
    p2p = sum(1 for ms in keep.values() if ms[0]["chat_type"] == "p2p")
    print(f"- 私聊 {p2p} 人；群聊 {len(keep) - p2p} 个（略过常浩没发言的群 {skipped_groups} 个）；"
          f"会议 {len(meetings)}；妙记 {len(minutes)}；日程 {len(agenda)}；任务 {len(tasks)}；"
          f"消息共 {len(msgs)} 条（不含机器人；和猛鬼兽的私聊 {skipped_p2p} 个不算）")
    print("- 没接的来源：文档编辑记录、派单回执以外的仓库细节（在项目进展里）")


def weekly(start):
    end = start + dt.timedelta(days=6)
    year, week, _ = start.isocalendar()
    wcontent = fetch_doc(WEEKLY_DOC)
    prev = None
    for m, body in entries(wcontent, WEEKLY_HEAD):
        if int(m.group(1)) == week and m.group(2) == str(year):
            print(f"SKIP：第{week}周周报已经在并跑文档里，不重写")
            return
        if prev is None or m.start() < prev[0]:   # 新的写在最上面
            prev = (m.start(), body)
    content = fetch_doc(DAILY_DOC)
    days = [(m.group(1), body) for m, body in entries(content, DAILY_HEAD)
            if start.isoformat() <= m.group(1) <= end.isoformat()]
    if not days:
        raise Fail(f"并跑文档里没有 {start}～{end} 的日报")
    print(f"# 周报材料：第{week}周 {year}.{week}（{start:%Y.%m.%d} - {end:%m.%d}）\n")
    print("## 上一篇周报的「六、下周焦点」\n")
    print(section(prev[1], "六") if prev else "（并跑文档里还没有周报，第一篇；一段写「无（第一篇）」）", "\n")
    print(f"## 本周日报（{len(days)} 篇，按日期）\n")
    for day, body in sorted(days):
        print(body.strip(), "\n")
    r = subprocess.run([sys.executable, str(PROGRESS), "--date", end.isoformat()], capture_output=True, text=True, timeout=300)
    print("## 项目进展（周日当天的快照，供参考）\n")
    print(r.stdout.strip()[:4000], "\n")
    print(f"来源：并跑日报 {len(days)} 篇")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["daily", "weekly"])
    ap.add_argument("--date", help="日报写哪一天，默认昨天")
    ap.add_argument("--week-start", help="周报从哪个周一开始，默认上周一")
    a = ap.parse_args()
    today = dt.date.today()
    try:
        if a.kind == "daily":
            daily(dt.date.fromisoformat(a.date) if a.date else today - dt.timedelta(days=1))
        else:
            ws = dt.date.fromisoformat(a.week_start) if a.week_start else today - dt.timedelta(days=today.weekday() + 7)
            weekly(ws)
    except (Fail, subprocess.TimeoutExpired) as e:
        print(f"取数失败：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
