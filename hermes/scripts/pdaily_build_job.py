"""个人项目日报 09:00 写稿（#155）：Hermes 无模型定时任务入口，等于 `pdaily_publish.py build --quiet`。
写稿约十分钟，09:45 由 pdaily_send_job.py 发送；这里成功不输出，取数失败的原因记在 build 产物里，由 09:45 那次报。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import pdaily_publish  # noqa: E402

if __name__ == "__main__":
    pdaily_publish.main(["build", "--quiet"])
    sys.exit(0)
