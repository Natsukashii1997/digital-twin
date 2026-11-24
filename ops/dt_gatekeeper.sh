#!/bin/sh
# 门卫入口（ADR-015 0E-3a；L3 第 1 轮 P1-04、复核 P1-1）。0E-3c 由 root 装成
#   /Library/Application Support/DigitalTwin/bin/gatekeeper   （root:wheel 755）
# 门卫脚本和它导入的同目录脚本整目录装到 GATE_DIR（root:wheel，目录 755、文件 644，只有 root 能改）。
# 门卫的所有命令（常驻 run、手动 setup / doctor / bind / show）都只经这里跑，例如：
#   sudo -u dt-gate "/Library/Application Support/DigitalTwin/bin/gatekeeper" doctor --online
# 清空环境（不带 PYTHON*、DEVELOPER_DIR、代理、主账号能改的 PATH；HOME 由 Python 从账号记录取），用系统自带的
# /usr/bin/python3，-I -S -B，脚本路径写死：主账号能改的 python3、标准库、site-packages 都进不了 dt-gate 的门卫进程。
# 给门卫的中台、凭证位置都在这里定，不从调用方环境取；门卫只认下面这几个变量，多一个就拒绝启动（gatekeeper.py ENTRY_ENV）。
# DT_HUB_LISTENER_UID 写死成 dt-gate 的 uid（0E-2 #268 的监听核对）：门卫带通道钥匙、本人密钥连 127.0.0.1:3350 之前，
# 先核端口上在听的是 dt-gate 自己；查不到这个账号就不起（set -e）。门卫自己再核它等于运行账号（launch_env）。
# 0E-3b 的派单卡线程和点击要读写凭证库、名册、工人镜像和任务单（autoloop_grants），和 dt_gate_hub.py 的 gate_env
# 给窗口服务的一致（0E-3c）：
#   DT_ROSTER、DT_HUB 每次启动从 dt_gate_hub 维护的 GATE_ROOT/current.json（repo.dir：当前副本，按提交号分目录）、
#   state.json（hub_url）现读，stage / update 换副本后门卫下次启动自动跟上，不用改入口；两个文件只有 root 能改。
#   读不到、不在 GATE_ROOT/repo/ 下、名册不是普通文件、中台不是 127.0.0.1 就不起。
#   DT_GRANTS_STATUS、DT_GRANTS_TASKS 写死成共享目录里的位置（同 gate_env）；DT_GRANTS_DB 和 gate_env 一样不设，
#   用 autoloop_grants 的默认值（运行账号的 DigitalTwin/autoloop/grants.db，即 dt-gate 自己的凭证库）。
set -eu
GATE_DIR="/Library/Application Support/DigitalTwin/gatekeeper"
GATE_ROOT="/Library/Application Support/DigitalTwin/gate"
GATE_SHARED="/Users/Shared/dt-gate"
GATE_UID=$(/usr/bin/id -u dt-gate)
GATE_VARS=$(/usr/bin/env -i PATH=/usr/bin:/bin /usr/bin/python3 -I -S -B - "$GATE_ROOT" <<'PY'
import json, os, stat, sys
root = os.path.realpath(sys.argv[1])
cur = json.load(open(os.path.join(root, "current.json"), encoding="utf-8"))
state = json.load(open(os.path.join(root, "state.json"), encoding="utf-8"))
repo = os.path.realpath(cur["repo"]["dir"])
if not repo.startswith(os.path.join(root, "repo") + os.sep):
    sys.exit("current.json 里的副本不在 " + root + "/repo/ 下")
roster = os.path.join(repo, "projects.json")
if not stat.S_ISREG(os.lstat(roster).st_mode):
    sys.exit("名册不是普通文件：" + roster)
hub = state["hub_url"]
if not isinstance(hub, str) or not hub.startswith("http://127.0.0.1:") or "\n" in hub:
    sys.exit("state.json 里的中台地址不是 127.0.0.1")
print(roster)
print(hub)
PY
)
DT_ROSTER=$(printf '%s\n' "$GATE_VARS" | /usr/bin/sed -n 1p)
DT_HUB=$(printf '%s\n' "$GATE_VARS" | /usr/bin/sed -n 2p)
exec /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 DT_HUB_LISTENER_UID="$GATE_UID" \
  DT_ROSTER="$DT_ROSTER" DT_HUB="$DT_HUB" \
  DT_GRANTS_STATUS="$GATE_SHARED/grants/status.json" DT_GRANTS_TASKS="$GATE_SHARED/tasks" \
  /usr/bin/python3 -I -S -B "$GATE_DIR/gatekeeper.py" "$@"
