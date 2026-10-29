"""测试用假车间（真实子进程）：像 run_worker 一样先写 out/worker.json，按 H_WORK 睡几秒，写回执后退出。
每跑一次往 H_ROOT/worker-runs.log 追加一行，测试据此数车间被拉起几次。不调用模型、Git 或外部服务。"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("run_worker", HERE.parent / "run_worker.py")
rw = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rw)
ap = argparse.ArgumentParser()
ap.add_argument("--out", required=True)
a, _ = ap.parse_known_args()
out = Path(a.out)
out.mkdir(parents=True, exist_ok=True)
ident = rw.write_identity(out) if hasattr(rw, "write_identity") else {"pid": os.getpid()}   # 基线（改前）没有这个函数
with open(Path(os.environ["H_ROOT"]) / "worker-runs.log", "a") as f:
    f.write(f"{out.parent.name} {ident['pid']}\n")
time.sleep(float(os.environ.get("H_WORK") or 0))
now = dt.datetime.now().isoformat(timespec="seconds")
(out / "receipt.json").write_text(json.dumps({"exit_code": 0, "timed_out": False, "worker_error": False, "started": now,
                                              "ended": now, "need_decision": None, "summary": "假车间"}))
