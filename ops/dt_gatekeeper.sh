#!/bin/sh
# 门卫入口（ADR-015 0E-3a；L3 第 1 轮 P1-04、复核 P1-1）。0E-3c 由 root 装成
#   /Library/Application Support/DigitalTwin/bin/gatekeeper   （root:wheel 755）
# 门卫脚本和它导入的同目录脚本整目录装到 GATE_DIR（root:wheel，目录 755、文件 644，只有 root 能改）。
# 门卫的所有命令（常驻 run、手动 setup / doctor / bind / show）都只经这里跑，例如：
#   sudo -u dt-gate "/Library/Application Support/DigitalTwin/bin/gatekeeper" doctor --online
# 清空环境（不带 PYTHON*、DEVELOPER_DIR、代理、主账号能改的 PATH；HOME 由 Python 从账号记录取），用系统自带的
# /usr/bin/python3，-I -S -B，脚本路径写死：主账号能改的 python3、标准库、site-packages 都进不了 dt-gate 的门卫进程。
# 0E-2 如要给门卫传中台路径，在这里写死，不从调用方环境取。
# DT_HUB_LISTENER_UID 写死成 dt-gate 的 uid（0E-2 #268 的监听核对）：门卫带通道钥匙、本人密钥连 127.0.0.1:3350 之前，
# 先核端口上在听的是 dt-gate 自己；查不到这个账号就不起（set -e）。门卫自己再核它等于运行账号（launch_env）。
# 0E-3b：门卫的派单卡线程和点击要读写凭证库、名册、工人镜像和任务单（autoloop_grants 的 DT_GRANTS_DB、DT_ROSTER、
# DT_GRANTS_STATUS、DT_GRANTS_TASKS、DT_HUB），0E-3c 装门卫时同样写死在这里（和 dt_gate_hub.py gate_env 给窗口的一致），
# 并加进 gatekeeper.py 的 ENTRY_ENV（入口以外多出来的变量门卫会拒绝启动）。
set -eu
GATE_DIR="/Library/Application Support/DigitalTwin/gatekeeper"
GATE_UID=$(/usr/bin/id -u dt-gate)
exec /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 DT_HUB_LISTENER_UID="$GATE_UID" \
  /usr/bin/python3 -I -S -B "$GATE_DIR/gatekeeper.py" "$@"
