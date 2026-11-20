"""个人项目日报 09:15 写稿（#155、#159）：Hermes 无模型定时任务入口，等于 `pdaily_publish.py build --quiet`。
写稿约三分钟，10:00 由 pdaily_send_job.py 发送；这里成功不输出，取数失败的原因记在 build 产物里，由 10:00 那次报。
开跑前先等 GitHub 和主模型连得上（P9，netwait）：等不到不写稿，原因记进 <统计日>.build-error.json，10:00 的卡片写明。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import netwait  # noqa: E402
import pdaily_publish  # noqa: E402

if __name__ == "__main__":
    try:
        netwait.gate("pdaily-build", ("github", "model"))
    except netwait.NotReady as e:
        pdaily_publish.record_build_error(pdaily_publish.pdaily_data.default_day().isoformat(), f"写稿前{e}")
        sys.exit(0)
    pdaily_publish.main(["build", "--quiet"])
    sys.exit(0)
