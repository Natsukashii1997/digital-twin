"""AI 前沿群学习报告 10:00 取数写稿（#192）：Hermes 无模型定时任务入口，等于 `groupdigest.py run --quiet`。
抓源头加写稿约二三十分钟，10:45 由 groupdigest_send_job.py 发送；这里不输出，失败原因记在产物里，由 10:45 那次报。
开跑前先等飞书和主模型连得上（P9，netwait）：等不到不取数，原因记进 collect-error.json，同样由 10:45 那次报。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import groupdigest  # noqa: E402
import netwait  # noqa: E402

if __name__ == "__main__":
    try:
        netwait.gate("groupdigest-build", ("feishu", "model"))
    except netwait.NotReady as e:
        groupdigest.record_collect_error(groupdigest.default_day(), f"取数失败（{e}）")
        sys.exit(0)
    groupdigest.main(["run", "--quiet"])
    sys.exit(0)
