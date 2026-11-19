"""夹具（#212）：故意不走统一隔离的假测试模块——先导入被测脚本，再 import _isolation。

test_isolation_guard.py 用它证明守护测试会判失败；本目录不是包，discover 不会收它，也不会被执行。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import autoloop_config  # noqa: E402,F401
import _isolation  # noqa: E402,F401
