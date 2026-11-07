#!/usr/bin/env python3
"""每周提案收口的推荐草稿（#61）：Hermes 带模型定时任务的 --script 不能带参数，用它代替 `proposal_digest.py draft`。

打印本期待决提案和原文摘录（JSON），Hermes 按 proposal-digest 技能第 0 节逐条写推荐，再跑 `proposal_digest.py send --recommend`。
没有待决提案时不输出。
"""
import sys

from proposal_digest import main

if __name__ == "__main__":
    sys.exit(main(["draft"]))
