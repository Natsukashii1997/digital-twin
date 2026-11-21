#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未闭环账本的午晚单（#281，批 5a-3a；规格 docs/未闭环账本-规格.md 第 8 节，DECIDED 2026-11-20 提速决定第 6 条）。

每天中午、晚上各一张：从账本里筛出要常浩亲手做的事（要合并的 PR、要点的交接卡、要他亲手操作的），
排成一张表发到他私聊，他照着从上往下一次做完。等他拍板的事不上这张单，只在卡片最下面计数。

卡片样子是常浩 2026-11-20 看过三张真卡片后选的乙版（一张四列表：#、哪件事、你要做什么、已挂），
基线在 tests/fixtures/openloop_slip/card.json；改版式先走「改动确认」（全局规则第 15 节）。

  build   只打印这一档的卡片 JSON，不发、不记
  send    以猛鬼兽机器人发到常浩私聊并回读；同一档只发一次；没有要亲手做的事就不发

send 成功不输出；没发成输出一句原因、退出码 1。不扫描账本（发单前先扫是定时任务入口的事，5a-3c）、不调 AI。
环境变量：DT_OPENLOOP_DIR 账本目录；DT_ROSTER 名册；DT_LARK_CLI 换 lark-cli；DT_NOW（ISO 时间）指定现在。
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import openloop as ol  # noqa: E402
from aihot_hot import Fail, lark  # noqa: E402  以猛鬼兽机器人跑 lark-cli（去掉会让它改读别的配置的环境变量）
from feishu_home import home_chat  # noqa: E402

MAX_ROWS = 7          # 表里最多几件，多的收进折叠（常浩没单独定，先按推荐值）
STALE_HOURS = 2       # 账本比这更旧，卡片最上面说一句
EVENING_FROM = 17     # 这个钟点起算晚间单


kind = ol.ask_kind   # 这一行要常浩做哪种事；判法在账本脚本里，日报的拍板候选用同一份
pick = ol.waiting    # （上单的，等他拍板的，可能已过期的）


def name(r, repos):
    """这件事叫什么：PR 带链接，别的写「项目｜事」。"""
    m = re.match(r"pr:(.+)#(\d+)$", r.get("id") or "") if r.get("source") == "pr" else None
    if not m:
        return f"{r.get('project') or '—'}｜{ol.clip(r.get('what'), 26)}"
    label, repo = f"{m.group(1)}#{m.group(2)}", repos.get(m.group(1))
    return (f"[{label}](https://github.com/{repo}/pull/{m.group(2)})" if repo else label) + f" {ol.clip(r.get('what'), 26)}"


def age(r, now):
    t = ol.parse_ts(r.get("evidence_at"))
    if not t:
        return "—"
    days = (now.date() - t.astimezone(ol.TZ).date()).days
    return "今天" if days <= 0 else f"{days} 天"


def ago(r, now):
    """折叠里每件后面括号里的一小段：今天的写「今天」，别的写「N 天前」。"""
    a = age(r, now)
    return a if a in ("今天", "—") else f"{a}前"


def slot(now):
    """（卡片上的叫法，记账用的一个字）。"""
    return ("午间单", "午") if now.hour < EVENING_FROM else ("晚间单", "晚")


def md(text):
    return {"tag": "markdown", "content": text}


def panel(title, text):
    """折叠面板，默认收起（写法同日报）。"""
    return {"tag": "collapsible_panel", "expanded": False, "background_color": "grey-50",
            "border": {"color": "grey", "corner_radius": "6px"}, "padding": "4px 10px 8px 10px", "vertical_spacing": "8px",
            "header": {"title": {"tag": "plain_text", "content": title}, "width": "fill", "vertical_align": "center",
                       "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined", "color": "grey", "size": "16px 16px"},
                       "icon_position": "right", "icon_expanded_angle": -180},
            "elements": [md(text)]}


def build_card(ledger, now, repos):
    """这一档的卡片；没有要他亲手做的事返回 None。"""
    todo, decide, gone = pick(ledger)
    if not todo:
        return None
    scanned = ol.parse_ts(ledger.get("generated_at"))
    elements = []
    if not scanned or now - scanned > dt.timedelta(hours=STALE_HOURS):
        elements.append(md(f"⚠️ 账本 {ol.stamp(scanned) if scanned else '不知道哪天'} 之后没更新，下面可能不是最新的"))
    shown, rest = todo[:MAX_ROWS], todo[MAX_ROWS:]
    elements.append({
        "tag": "table", "page_size": 10, "row_height": "auto", "row_max_height": "999px",
        "header_style": {"text_align": "left", "text_size": "normal", "background_style": "grey", "bold": True, "lines": 1},
        "columns": [{"name": k, "display_name": n, "data_type": "lark_md", "width": w, "vertical_align": "top"}
                    for k, n, w in (("n", "#", "8%"), ("what", "哪件事", "37%"), ("do", "你要做什么", "42%"), ("age", "已挂", "13%"))],
        "rows": [{"n": str(i), "what": name(r, repos), "do": f"**{kind(r)}**：{ol.clip(r.get('next'), 60)}", "age": age(r, now)}
                 for i, r in enumerate(shown, 1)]})
    if rest:
        elements.append(panel(f"其余 {len(rest)} 件 · 展开", "\n".join(f"- **{kind(r)}** {name(r, repos)}（{ago(r, now)}）" for r in rest)))
    if gone:
        late = sorted(gone, key=lambda r: r.get("evidence_at") or "", reverse=True)
        elements.append(panel(f"可能已过期的 {len(gone)} 件 · 展开", "\n".join(f"- {name(r, repos)}（{ago(r, now)}）" for r in late)))
    tail = (f"另有 {len(decide)} 件等你拍板（不在这张单上）｜{len(gone)} 件可能已过期（它的 PR 已合并或关闭）"
            f"｜账本 {scanned.astimezone(ol.TZ).strftime('%H:%M') if scanned else '—'} 扫描")
    elements.append(md(f"<font color='grey'>{tail}</font>"))
    return {"schema": "2.0", "config": {"update_multi": True},
            "header": {"title": {"tag": "plain_text", "content": f"🧾 {slot(now)[0]} · {now.strftime('%m-%d')}"},
                       "subtitle": {"tag": "plain_text", "content": f"要你亲手做 {len(todo)} 件｜一张表从上往下做"}, "template": "indigo"},
            "body": {"elements": elements}}


def repos_of():
    return {ol.key_of(p): p.get("github") for p in ol.roster() if p.get("github")}


def load():
    ledger = ol.read_ledger()
    if ledger is None:
        raise Fail(f"还没有账本：{ol.out_dir() / 'ledger.json'} 不存在或读不了，先跑 openloop.py scan")
    return ledger


def read_json(path, default):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def send(now, force=False):
    """发这一档的单。返回记进 runs.jsonl 的一条；发了但回读对不上也记成发过（不重发），再抛 Fail。"""
    folder = ol.out_dir() / "slips"
    key = f"{now.strftime('%Y-%m-%d')}-{slot(now)[1]}"
    rec = {"slot": key, "at": ol.iso(now), "result": None}
    state = read_json(folder / "state.json", {})
    try:
        if key in state and not force:
            rec["result"] = "already"
            return rec
        ledger = load()
        card = build_card(ledger, now, repos_of())
        if card is None:
            rec["result"] = "nothing"
            return rec
        chat = home_chat()
        if not chat.startswith("oc_"):
            raise Fail("没有私聊 chat_id（FEISHU_HOME_CHANNEL，环境变量和 ~/.hermes/.env 里都没有）")
        idem = f"openloop-slip-{now.strftime('%Y%m%d')}-{'noon' if slot(now)[1] == '午' else 'evening'}" + (f"-{int(now.timestamp())}" if force else "")
        mid = lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "interactive",
                    "--content", json.dumps(card, ensure_ascii=False), "--idempotency-key", idem], "发卡片").get("message_id")
        if not mid:
            raise Fail("发卡片没拿到消息号")
        todo, _, gone = pick(ledger)
        # 记下单上列了哪几件（账本编号）：追问登记（openloop_ask.py）靠它判「问之前报过没有」
        rec.update(result="sent", message_id=mid, rows=len(todo), ledger_at=ledger.get("generated_at"),
                   ids=[r.get("id") for r in todo], ids_gone=[r.get("id") for r in gone])
        state[key] = {"at": ol.iso(now), "message_id": mid}
        write_json(folder / "state.json", state)
        write_json(folder / f"{key}.card.json", card)
        m = (lark(["im", "+messages-mget", "--message-ids", mid], "回读").get("messages") or [{}])[0]
        if m.get("msg_type") != "interactive" or card["header"]["title"]["content"] not in str(m.get("content")):
            rec["result"] = "sent_unverified"
            raise Fail(f"卡片已发（{mid}），但回读对不上卡片内容")
        return rec
    except Fail as e:
        rec["result"] = rec["result"] or "failed"
        rec["error"] = str(e)
        raise
    finally:
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / "runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="未闭环账本的午晚单：要常浩亲手做的事排成一张表")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="只打印这一档的卡片 JSON，不发")
    s = sub.add_parser("send", help="发到常浩私聊并回读；同一档只发一次")
    s.add_argument("--force", action="store_true", help="这一档发过也再发一张")
    a = ap.parse_args(argv)
    now = ol.current()
    try:
        if a.cmd == "build":
            card = build_card(load(), now, repos_of())
            print(json.dumps(card, ensure_ascii=False, indent=1) if card else "这一档没有要你亲手做的事，不发")
            return 0
        send(now, force=a.force)
        return 0
    except (Fail, ol.Fail) as e:
        print(f"{slot(now)[0]}没发成：{e}")
        return 1
    except Exception as e:   # 没料到的错也收成一句
        print(f"{slot(now)[0]}没发成：出错（{type(e).__name__}：{e}）")
        return 1


if __name__ == "__main__":
    sys.exit(main())
