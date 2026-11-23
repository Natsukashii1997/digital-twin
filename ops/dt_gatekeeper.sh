#!/bin/sh
# 门卫入口（ADR-015 0E-3a；L3 第 1 轮 P1-04、复核 P1-1）。0E-3c 由 root 装成
#   /Library/Application Support/DigitalTwin/bin/gatekeeper   （root:wheel 755）
# 门卫脚本和它导入的同目录脚本整目录装到 GATE_DIR（root:wheel，目录 755、文件 644，只有 root 能改）。
# 门卫的所有命令（常驻 run、手动 setup / doctor / bind / show）都只经这里跑，例如：
#   sudo -u dt-gate "/Library/Application Support/DigitalTwin/bin/gatekeeper" doctor --online
# 清空环境（不带 PYTHON*、DEVELOPER_DIR、代理、主账号能改的 PATH；HOME 由 Python 从账号记录取），用系统自带的
# /usr/bin/python3，-I -S -B，脚本路径写死：主账号能改的 python3、标准库、site-packages 都进不了 dt-gate 的门卫进程。
# 0E-2 如要给门卫传中台路径，在这里写死，不从调用方环境取。
set -eu
GATE_DIR="/Library/Application Support/DigitalTwin/gatekeeper"
exec /usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin LANG=en_US.UTF-8 \
  /usr/bin/python3 -I -S -B "$GATE_DIR/gatekeeper.py" "$@"
