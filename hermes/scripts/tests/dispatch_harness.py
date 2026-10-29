"""测试用：在隔离目录里跑真实的 dispatch.py——只换派单根目录、名册、worker 路径，可注入延迟复现竞态。
环境变量：H_ROOT（隔离根目录，派单根目录是 H_ROOT/dispatch、名册是 H_ROOT/roster.json）、H_WORKER（假车间脚本）、
H_DELAY（next_no / orders 读完后睡几秒，拉大竞态窗口）、H_GO（这个文件出现才开始，几个进程同时起跑）、H_LOCK_WAIT（等锁上限）、
H_PRELOCK_DELAY（拿派单锁之前先睡几秒：模拟锁前的 git fetch / gh 拖很久）、H_DONE_FILE（进程退出时写这个文件和退出码）。
不碰 ~/.hermes、真实工单与外部服务。"""
import atexit
import importlib.util
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("dispatch", HERE.parent / "dispatch.py")
d = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(d)
root = Path(os.environ["H_ROOT"])
d.ROOT, d.WT_ROOT, d.ROSTER = root / "dispatch", root / "worktrees", root / "roster.json"
d.WORKER = Path(os.environ.get("H_WORKER", HERE / "fake_worker_proc.py"))
if os.environ.get("H_LOCK_WAIT") and hasattr(d, "LOCK_WAIT"):
    d.LOCK_WAIT = float(os.environ["H_LOCK_WAIT"])
delay = float(os.environ.get("H_DELAY") or 0)
if delay:
    real_next, real_orders = d.next_no, d.orders

    def next_no():
        n = real_next()
        time.sleep(delay)
        return n

    def orders():
        o = real_orders()
        time.sleep(delay)
        return o

    d.next_no, d.orders = next_no, orders
prelock = float(os.environ.get("H_PRELOCK_DELAY") or 0)
if prelock:
    real_lock = d.dispatch_lock

    def dispatch_lock():
        time.sleep(prelock)
        return real_lock()

    d.dispatch_lock = dispatch_lock
done_file = os.environ.get("H_DONE_FILE")
if done_file:
    atexit.register(lambda: Path(done_file).write_text("exited"))
go = os.environ.get("H_GO")
if go:
    while not Path(go).exists():
        time.sleep(0.005)
sys.argv = ["dispatch.py", *sys.argv[1:]]
d.main()
