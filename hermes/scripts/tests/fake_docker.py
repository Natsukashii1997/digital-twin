"""假 docker（M5 #25、#27 自测）：只认主控发来的 `exec <容器> bash -c <脚本> proc list|kill`。
执行器里的进程放在 FAKE_DOCKER_PROCS 指向的 JSON 里：
  procs       当前残留的进程行
  exit_after  再被 list 几次后它们自己退出（模拟 agent 进程收尾慢），缺省不自己退
  stubborn    true 时 kill 也杀不掉
  fail        true 时命令失败（容器不在、docker 不通）
  ident       身份闸（#172）要的 GitHub 身份事实；ident_fail 为 true 时身份查询失败；ident_raw 有值时原样输出（格式不对的情形）
  labels      docker inspect 出的容器标签（缺省是 Multica-local 隔离执行器的 compose 标签）；inspect_fail 为 true 时 inspect 失败
  service     docker ps 按 compose 标签列出的容器名（缺省只有被查的容器本身）
冻结（#174 起由名册生成）：update --restart=no / stop / inspect -f '{{.State.Running}} {{.HostConfig.RestartPolicy.Name}}'。
  freeze_log  stop 时往这个文件追加一行 stop；stop_probe [标记文件, 记录文件]：stop 那一刻标记里有 EMERGENCY_REQUESTED 就记 yes
  state       inspect 回读的输出（缺省按 update / stop 之后的真实状态算）；unstoppable 为 true 时 stop 停不住
冻结命令不受 fail 影响（fail 模拟的是 exec 查进程失败）。
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
if args[:1] == ["update"] or args[:1] == ["stop"] or (args[:1] == ["inspect"] and "{{.State.Running}}" in " ".join(args)):
    act = args[0] if args[0] != "inspect" else "inspect-state"
    w["calls"][-1].update(action=act, container=args[-1])
    if act == "update":
        w["restart"] = "no"
    elif act == "stop":
        if w.get("freeze_log"):
            with open(w["freeze_log"], "a", encoding="utf-8") as fh:
                fh.write("stop\n")
        if w.get("stop_probe"):
            marker, seen = w["stop_probe"]
            if Path(marker).exists() and "EMERGENCY_REQUESTED" in Path(marker).read_text(encoding="utf-8"):
                with open(seen, "a", encoding="utf-8") as fh:
                    fh.write("yes\n")
        if not w.get("unstoppable"):
            w["running"] = False
    else:
        print(w["state"] if "state" in w else f"{str(w.get('running', True)).lower()} {w.get('restart', 'unless-stopped')}")
    p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
    sys.exit(0)
if w.get("fail"):
    p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
    print("Error response from daemon: container is not running", file=sys.stderr)
    sys.exit(1)
if args[:1] == ["inspect"]:                     # 身份闸（#174）：核对容器的 compose 标签
    w["calls"][-1].update(action="inspect", container=args[-1])
    p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
    if w.get("inspect_fail"):
        print("Error: No such object: " + args[-1], file=sys.stderr)
        sys.exit(1)
    print(json.dumps(w.get("labels", {"com.docker.compose.project": "multica", "com.docker.compose.service": "isolated-agent",
                                      "com.docker.compose.oneoff": "False"})))
    sys.exit(0)
if args[:1] == ["ps"]:                          # 身份闸（#174）：这个 compose 服务有哪些容器
    w["calls"][-1].update(action="ps", container=None, args=args)
    p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
    print("\n".join(w.get("service", ["agent-1"])))
    sys.exit(0)
if args[:1] == ["exec"] and args[2:4] == ["bash", "-c"] and args[5:6] == ["ident"]:   # 运行前身份闸（#172）
    w["calls"][-1]["action"] = "ident"
    p.write_text(json.dumps(w, ensure_ascii=False), encoding="utf-8")
    if w.get("ident_fail"):
        print("gh api user 失败：HTTP 401: Bad credentials token ghp_abcdefghijklmnopqrstuvwxyz0123456789", file=sys.stderr)
        sys.exit(1)
    print(w["ident_raw"] if "ident_raw" in w else json.dumps(w.get("ident", {})))
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
