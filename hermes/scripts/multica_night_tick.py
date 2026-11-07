"""Hermes 无模型定时任务的入口（#92）：定时任务的 --script 不能带参数，这里固定调 multica_night.py tick。
没有当前夜跑、没事发生时什么都不打印（定时任务就不发消息）。"""
import sys

from multica_night import main

sys.exit(main(["tick"]))
