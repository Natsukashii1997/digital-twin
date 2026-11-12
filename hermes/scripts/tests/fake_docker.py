"""假 docker（M5 #25、#27 自测）：只认主控发来的 `exec <容器> bash -c <脚本> proc list|kill`。
执行器里的进程放在 FAKE_DOCKER_PROCS 指向的 JSON 里：
  procs       当前残留的进程行
  exit_after  再被 list 几次后它们自己退出（模拟 agent 进程收尾慢），缺省不自己退
  stubborn    true 时 kill 也杀不掉
  fail        true 时命令失败（容器不在、docker 不通）
  ident       身份闸（#172）要的 GitHub 身份事实；ident_fail 为 true 时身份查询失败
每次调用记进 calls。"""
import json
import os
import sys
from pathlib import Path

p = Path(os.environ["FAKE_DOCKER_PROCS"])
w = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
args = sys.argv[1:]
if args[:1] == ["--context"]:
    args = args[2:]
action = args[-1] if args else ""
w.setdefault("calls", []).append({"container": args[1] if len(args) > 1 else None, "action": action})
if w.get("fail"):
    p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
    print("Error response from daemon: container is not running", file=sys.stderr)
    sys.exit(1)
if args[:1] == ["exec"] and args[2:4] == ["bash", "-c"] and args[5:6] == ["ident"]:   # 运行前身份闸（#172）
    w["calls"][-1]["action"] = "ident"
    p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
    if w.get("ident_fail"):
        print("gh api user 失败：HTTP 401: Bad credentials", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(w.get("ident", {})))
    sys.exit(0)
if args[:1] != ["exec"] or args[2:4] != ["bash", "-c"] or action not in ("list", "kill", "orphans", "kill-orphans"):
    print(f"假 docker 不认识：{args[:4]}", file=sys.stderr)
    sys.exit(2)
procs = w.get("procs", [])


def orphan(line):        # 每行「pid ppid 命令」，PPID 为 1 的是孤儿
    return line.split()[1] == "1"


if action == "kill" and not w.get("stubborn"):
    procs = []
elif action == "kill-orphans" and not w.get("stubborn"):
    procs = [x for x in procs if not orphan(x)]
elif action == "list" and procs and w.get("exit_after") is not None:
    if w["exit_after"] <= 0:
        procs = []
    else:
        w["exit_after"] -= 1
w["procs"] = procs
p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
shown = [x for x in procs if orphan(x)] if action in ("orphans", "kill-orphans") else procs
sys.stdout.write("".join(x + "\n" for x in shown))
