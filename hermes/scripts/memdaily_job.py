"""定时任务入口（亚古兽 profile，无模型定时任务每天 07:30）：写前一天的记忆日报并推送。

成功或跳过不输出（推送由脚本自己发）；没写成输出一句原因，由 Hermes 发到常浩和亚古兽的私聊。
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import memdaily_data as md  # noqa: E402
import memdaily_publish as mp  # noqa: E402

if __name__ == "__main__":
    day = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    try:
        mp.run_all(day)
    except (mp.GenError, md.Fail) as e:
        print(f"⚠️ 记忆日报 {day} 没写成：{e}")
