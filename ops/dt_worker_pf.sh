#!/bin/bash
# 工人账号的门卫规则（digital-twin#190，0A 隔离门）：macOS 自带防火墙 pf。
# dt-worker 只准连本机的中台 3350 和过滤代理 8090；本机其他端口、局域网地址一律拒绝。
#
# 用法（主账号终端，要输管理员密码）：
#   sudo bash ops/dt_worker_pf.sh install     装上：规则 + 开机和每 5 分钟自动重新加载
#   sudo bash ops/dt_worker_pf.sh uninstall   拆掉
#   sudo bash ops/dt_worker_pf.sh status      看规则有没有在生效
# apply 由开机任务调用，不用手动运行。
#
# 不改 /etc/pf.conf：规则挂在系统自带的 com.apple/* 锚点下面（com.apple/dt-worker）。
# 每次加载后写一份状态文件（所有人可读），每日巡检据此判断门卫规则是不是还在。
set -euo pipefail

WORKER=dt-worker
ANCHOR=com.apple/dt-worker
DIR="/Library/Application Support/DigitalTwin"
RULES="$DIR/pf-dt-worker.rules"
SELF="$DIR/dt_worker_pf.sh"
STATUS="$DIR/pf-dt-worker.status"
LABEL=cn.digital-twin.pf-dt-worker
PLIST="/Library/LaunchDaemons/$LABEL.plist"

[ "$(id -u)" = 0 ] || { echo "要用 sudo 运行：sudo bash $0 ${1:-install}"; exit 1; }

write_rules() {
  cat > "$RULES" <<EOF
# 由 dt_worker_pf.sh 生成，不要手改（digital-twin#190）
table <dt_local> const { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 169.254.0.0/16, 100.64.0.0/10, 0.0.0.0/8, 224.0.0.0/4 }
table <dt_local6> const { ::1, fe80::/10, fc00::/7, ff00::/8 }
pass out quick inet proto tcp from any to 127.0.0.1 port { 3350, 8090 } user $WORKER
block return out quick inet proto { tcp, udp } from any to <dt_local> user $WORKER
block return out quick inet6 proto { tcp, udp } from any to <dt_local6> user $WORKER
EOF
  chown root:wheel "$RULES"
  chmod 644 "$RULES"
}

apply() {
  id "$WORKER" >/dev/null 2>&1 || { echo "没有 $WORKER 这个账号" | tee "$STATUS"; exit 1; }
  # 主规则里要有 com.apple/* 锚点（系统默认 pf.conf 里有）；没有就按系统默认的重新加载一次
  /sbin/pfctl -s Anchors 2>/dev/null | grep -q '^ *com.apple' || /sbin/pfctl -q -f /etc/pf.conf
  /sbin/pfctl -q -a "$ANCHOR" -f "$RULES"
  /sbin/pfctl -s info 2>/dev/null | grep -q 'Status: Enabled' || /sbin/pfctl -q -E
  n=$(/sbin/pfctl -a "$ANCHOR" -s rules 2>/dev/null | grep -c "user = " || true)
  enabled=$(/sbin/pfctl -s info 2>/dev/null | grep -q 'Status: Enabled' && echo yes || echo no)
  printf 'at=%s enabled=%s rules=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$enabled" "$n" > "$STATUS"
  chmod 644 "$STATUS"
  [ "$enabled" = yes ] && [ "$n" -ge 3 ] || { cat "$STATUS"; exit 1; }
}

case "${1:-install}" in
  install)
    mkdir -p "$DIR"
    chown root:wheel "$DIR"
    chmod 755 "$DIR"
    install -o root -g wheel -m 755 "$0" "$SELF"
    write_rules
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$SELF</string><string>apply</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>300</integer>
</dict>
</plist>
EOF
    chown root:wheel "$PLIST"
    chmod 644 "$PLIST"
    # bootout 是异步的，等旧任务卸完再装，装失败隔几秒重试（全局坑 G-18）
    launchctl bootout "system/$LABEL" 2>/dev/null || true
    for _ in $(seq 20); do launchctl print "system/$LABEL" >/dev/null 2>&1 || break; sleep 0.5; done
    apply
    ok=no
    for _ in 1 2 3; do
      if launchctl bootstrap system "$PLIST" 2>/dev/null; then ok=yes; break; fi
      sleep 3
    done
    [ "$ok" = yes ] || { echo "规则已生效，但开机自动加载没装上；手动运行：sudo launchctl bootstrap system $PLIST"; exit 1; }
    echo "门卫规则已装上并生效："
    cat "$STATUS"
    ;;
  apply)
    [ -f "$RULES" ] || write_rules
    apply
    ;;
  status)
    cat "$STATUS" 2>/dev/null || echo "没有状态文件（还没装）"
    /sbin/pfctl -a "$ANCHOR" -s rules 2>/dev/null
    ;;
  uninstall)
    launchctl bootout system "$PLIST" 2>/dev/null || true
    /sbin/pfctl -q -a "$ANCHOR" -F rules 2>/dev/null || true
    rm -f "$PLIST" "$RULES" "$SELF" "$STATUS"
    echo "门卫规则已拆掉（pf 本身保持开启，没有别的规则时不影响上网）。"
    ;;
  *)
    echo "用法：sudo bash $0 install|uninstall|status"
    exit 1
    ;;
esac
