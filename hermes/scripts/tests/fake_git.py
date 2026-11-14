"""假 git（执行器侧，#174 身份闸采集层集成测试）：只认 `[-C 目录] config --list --show-origin --show-scope -z`。
FAKE_GIT_SCENARIO 指向的 JSON：
  global  [[scope, origin, key, value 或 null], ...]：在任何目录下都输出（system / global / command 这些层）
  repos   目录 → 同样格式的条目：-C 这个目录时追加输出（local / worktree 层）
  fail    "global" 或目录 → [退出码, stderr]
输出格式和真 git 一致：scope NUL origin NUL key 换行 value NUL（没有值的布尔项只有 key）。"""
import json
import os
import sys
from pathlib import Path

w = json.loads(Path(os.environ["FAKE_GIT_SCENARIO"]).read_text(encoding="utf-8"))
args = sys.argv[1:]
where = None
if args[:1] == ["-C"]:
    where, args = args[1], args[2:]
if args != ["config", "--list", "--show-origin", "--show-scope", "-z"]:
    sys.stderr.write(f"假 git 不认识：{args}\n")
    sys.exit(2)
fail = w.get("fail", {}).get(where or "global")
if fail:
    sys.stderr.write(fail[1] + "\n")
    sys.exit(fail[0])
rows = w.get("global", []) + (w.get("repos", {}).get(where, []) if where else [])
sys.stdout.write("".join(f"{s}\0{o}\0{k}" + ("" if v is None else "\n" + v) + "\0" for s, o, k, v in rows))
