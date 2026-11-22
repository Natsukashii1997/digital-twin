#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未闭环账本追问基线的每周定时任务入口（#266 批 5a-4；规格 docs/未闭环账本-规格.md 第 9、10 节）。

Hermes 无模型定时任务每周一 09:00 调用（常浩 2026-11-22 回「建」）：先从 Claude 会话记录里粗筛上一周
（周一到周日）的追问、记成待核（openloop_ask.screen），再出这一周的基线（openloop_ask.week，写 weekly/<周日>.json），
把基线和粗筛的一行摘要打印出来，由定时任务发到常浩私聊。只读本机文件，不连网、不调 AI。

粗筛没跑成不挡基线：基线照出，第一行说粗筛为什么没跑成（这一周的「待核」就是空的）。基线也出不来就输出一句原因、退出码 1。
"""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import openloop as ol  # noqa: E402
import openloop_ask as oa  # noqa: E402


def main():
    end_day = ol.current().date() - dt.timedelta(days=1)
    start, end = oa.day_range(end_day)
    lines = []
    try:
        got, cover = oa.screen(start, end)
        lines.append(f"Claude 会话粗筛：像追问的 {cover['hits']} 句，新记 {len(got)} 条（待核），以前记过 {cover['already']}，问时还没账本 {cover['before_ledger']}")
    except Exception as e:   # 拍板清单脚本加载不了、会话目录读不了：只是这一周没有待核，基线照出
        why = str(e) if isinstance(e, (ol.Fail, oa.Fail)) else f"{type(e).__name__}：{e}"
        lines.append(f"⚠️ Claude 会话粗筛没跑成（{why}），这一周的待核是空的")
    try:
        rep = oa.week(end_day)
        folder = ol.out_dir() / "weekly"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{end_day}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"⚠️ 追问基线这周没出成（{type(e).__name__}：{e}）；可以手动跑 openloop_ask.py week")
        return 1
    print(oa.render_week(rep) + "\n" + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
