"""定时任务入口：hermes cron 的 --script 不能带参数，这里固定成 report_data.py weekly。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import report_data  # noqa: E402

sys.argv = [sys.argv[0], "weekly"]
report_data.main()
