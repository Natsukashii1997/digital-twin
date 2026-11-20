"""Hermes 无模型定时任务的入口（#92）：定时任务的 --script 不能带参数，这里固定调 multica_night.py tick。
没有当前夜跑、没事发生时什么都不打印（定时任务就不发消息）。
开跑前先等 GitHub 连得上（P9，netwait）：每 5 分钟一跑，最多只等 60 秒；等不到第一次输出原因，之后 6 小时内只记录不刷屏。"""
import sys

import netwait
from multica_night import main

netwait.guard("multica-night-tick", ("github",), "Multica 夜跑主控这一轮", quiet_hours=6, limit=min(60, netwait.wait_seconds()))
sys.exit(main(["tick"]))
