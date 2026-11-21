#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""未闭环账本午晚单定时任务入口（#293，批 5a-3c；规格 docs/未闭环账本-规格.md 第 8、14 节）。

Hermes 无模型定时任务每天 12:30、21:00 调用：先等飞书和 GitHub 连得上（P9，netwait），
再扫一遍账本（openloop.scan，约 70–90 秒），最后发这一档的单（openloop_slip.send）。
21:00 这次扫描留下的 history/<日期>.json 也是第二天 10:00 日报读的（pdaily_facts.from_ledger）。

成功、这一档没事、这一档发过都不输出。扫描没成就不发单（上一份账本里的待办可能已经办完，照旧单发会误导），
输出一句原因、退出码 1；单没发成同样输出一句、退出码 1。输出由定时任务发到常浩私聊。不调 AI。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import netwait  # noqa: E402
import openloop as ol  # noqa: E402
import openloop_slip as sl  # noqa: E402


def main():
    now = ol.current()
    title = sl.slot(now)[0]
    netwait.guard("openloop-slip", ("feishu", "github"), f"这一档的{title}")
    try:
        ol.scan(now)
    except Exception as e:   # 扫不成什么原因都收成一句：定时跑的时候要看得出「没发、为什么」
        why = str(e) if isinstance(e, ol.Fail) else f"{type(e).__name__}：{e}"
        print(f"⚠️ {title}没发：账本没扫成（{why}），没拿旧账本发单；下一档照常再跑，等不及可以手动跑 openloop_job.py")
        return 1
    return sl.main(["send"])


if __name__ == "__main__":
    sys.exit(main())
