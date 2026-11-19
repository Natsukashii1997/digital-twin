#!/bin/bash
# 工人、秘书账号的门卫规则（digital-twin#190 0A 隔离门；ADR-015 批 0E-1 加秘书）：macOS 自带防火墙 pf。
# 白名单、默认拒绝（L3 P1-02）：两个账号的 TCP / UDP 只准连下面列出的本机端口，其余一律拒绝——
# 本机其他端口、局域网、公网直连（包括公网 DNS）都挡，上网只能经过滤代理 8090：
#   dt-worker     中台 3350、过滤代理 8090
#   dt-secretary  只读窗口 3352、秘书窄接口 3353（0E-2 由 dt-gate 开）、过滤代理 8090；连不到中台 3350
# dt-gate 是控制面，不在这里限制。
# pf 只能按账号认 TCP / UDP，挡不住的两样记为剩余风险：系统解析器（mDNSResponder）代发的 DNS 查询，
# 和 ICMP（ping）。经 IPv6 套接字连 ::ffff:127.0.0.1 这类 IPv4 映射地址，发出去的是 IPv4 包，照样按上面的规则判。
#
# 用法（主账号终端，要输管理员密码）：
#   sudo bash ops/dt_worker_pf.sh install     装上：规则 + 开机和每 5 分钟自动重新加载
#   sudo bash ops/dt_worker_pf.sh uninstall   拆掉
#   sudo bash ops/dt_worker_pf.sh status      看规则有没有在生效
#   sudo bash ops/dt_worker_pf.sh verify      真机越权测试：以各账号身份去连，证明真挡住了（ops/dt_pf_verify.py）
#   sudo bash ops/dt_worker_pf.sh verify repeat 30   诊断：每类目标连 30 次，看有没有偶发漏过
#   bash ops/dt_worker_pf.sh rules dt-worker [dt-secretary]   只打印规则，不用 sudo（测试用）
# apply 由开机任务调用，不用手动运行；每次按本机现有账号重新生成规则，dt-secretary 建好后 5 分钟内自动管上。
#
# 不改 /etc/pf.conf：规则挂在系统自带的 com.apple/* 锚点下面（com.apple/dt-worker，名字沿用 0A）。
# 每次加载先把状态文件写成 state=applying，加载后逐条回读核对（实际加载的规则必须和规则文件解析出来的一模一样、
# 主规则集里真的挂着 com.apple/* 锚点、每个账号的 uid 只对应这一个账号），全部对上才写 state=ok；
# 中途任何一步出错都写 state=failed 和原因（所有人可读）。每日巡检据此判断门卫规则是不是还在、秘书账号有没有管上。
set -Eeuo pipefail   # -E：函数里出错也触发 ERR，apply 中途任何一步失败都落到 state=failed

WORKER=dt-worker
SECRETARY=dt-secretary
ANCHOR=com.apple/dt-worker
DIR="/Library/Application Support/DigitalTwin"
RULES="$DIR/pf-dt-worker.rules"
SELF="$DIR/dt_worker_pf.sh"
STATUS="$DIR/pf-dt-worker.status"
LABEL=cn.digital-twin.pf-dt-worker
PLIST="/Library/LaunchDaemons/$LABEL.plist"

# 每个账号准连的本机端口；其余一律拒绝
ports_for() {
  case "$1" in
    "$WORKER") echo "3350, 8090" ;;
    "$SECRETARY") echo "3352, 3353, 8090" ;;
    *) return 1 ;;
  esac
}

# 打印规则；参数是要管的账号（pf 不认不存在的用户名，所以只传本机已有的）
print_rules() {
  echo "# 由 dt_worker_pf.sh 生成，不要手改（digital-twin#190、ADR-015 0E-1）"
  local u p
  for u in "$@"; do
    p=$(ports_for "$u") || { echo "不认识的账号：$u" >&2; return 1; }
    echo "pass out quick inet proto tcp from any to 127.0.0.1 port { $p } user $u"
    # 不写地址族：IPv4、IPv6 一起挡
    echo "block return out quick proto { tcp, udp } from any to any user $u"
  done
}

# 本机现有的、要管的账号（逗号分隔）：工人必须有，秘书有就管
present_users() {
  id "$WORKER" >/dev/null 2>&1 || return 1
  if id "$SECRETARY" >/dev/null 2>&1; then echo "$WORKER,$SECRETARY"; else echo "$WORKER"; fi
}

if [ "${1:-}" = rules ]; then
  shift
  print_rules "$@"
  exit
fi

[ "$(id -u)" = 0 ] || { echo "要用 sudo 运行：sudo bash $0 ${1:-install}"; exit 1; }

write_rules() {
  local IFS=,
  # shellcheck disable=SC2086
  print_rules $1 > "$RULES.tmp"
  chown root:wheel "$RULES.tmp"
  chmod 644 "$RULES.tmp"
  mv -f "$RULES.tmp" "$RULES"
}

# 状态文件：一行 key=value，值里不带空格
write_status() {
  printf 'at=%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" > "$STATUS.tmp"
  chmod 644 "$STATUS.tmp"
  mv -f "$STATUS.tmp" "$STATUS"
}

fail() {
  trap - ERR
  write_status "state=failed enabled=$(pf_enabled) reason=${1// /_}"
  cat "$STATUS"
  exit 1
}

pf_enabled() { /sbin/pfctl -s info 2>/dev/null | grep -q 'Status: Enabled' && echo yes || echo no; }

# 主规则集里挂着 com.apple/* 锚点：没挂，锚点里的规则一条都不生效
anchor_hooked() { /sbin/pfctl -s rules 2>/dev/null | grep -qx 'anchor "com.apple/\*" all'; }

# 一个 uid 只对应一个账号（pf 按 uid 认人，两个账号共用 uid 就分不开）
uid_unique() {
  local uid
  uid=$(id -u "$1") || return 1
  [ "$(dscl . -list /Users UniqueID | awk -v id="$uid" '$2 == id' | wc -l | tr -d ' ')" = 1 ]
}

apply() {
  write_status "state=applying"
  trap 'fail "第 $LINENO 行出错"' ERR
  users=$(present_users) || fail "没有 $WORKER 这个账号"
  for u in ${users//,/ }; do uid_unique "$u" || fail "$u 的 uid 不唯一"; done
  write_rules "$users"
  # 系统默认 pf.conf 里有 com.apple/* 锚点；被人换掉了就按系统默认的重新加载一次
  anchor_hooked || /sbin/pfctl -q -f /etc/pf.conf 2>/dev/null || true
  anchor_hooked || fail "主规则集没挂 com.apple/* 锚点"
  /sbin/pfctl -q -a "$ANCHOR" -f "$RULES" || fail "规则没加载上（pf 保留了旧规则）"
  [ "$(pf_enabled)" = yes ] || /sbin/pfctl -q -E 2>/dev/null || true
  [ "$(pf_enabled)" = yes ] || fail "pf 没开"
  # 逐条核对：实际加载的规则必须和规则文件解析出来的一模一样（同一个 pfctl 打印，按 uid 展开）
  expected=$(/sbin/pfctl -nvf "$RULES" 2>/dev/null)
  loaded=$(/sbin/pfctl -a "$ANCHOR" -s rules 2>/dev/null)
  [ -n "$expected" ] && [ "$loaded" = "$expected" ] || {
    diff <(echo "$expected") <(echo "$loaded") | head -20 >&2 || true
    fail "加载上的规则和应有的不一致"
  }
  trap - ERR
  write_status "state=ok enabled=yes rules=$(echo "$loaded" | grep -c 'user = ') users=$users"
}

case "${1:-install}" in
  install)
    mkdir -p "$DIR"
    chown root:wheel "$DIR"
    chmod 755 "$DIR"
    install -o root -g wheel -m 755 "$0" "$SELF"
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
    apply
    ;;
  status)
    cat "$STATUS" 2>/dev/null || echo "没有状态文件（还没装）"
    anchor_hooked && echo "主规则集挂着 com.apple/* 锚点" || echo "主规则集没挂 com.apple/* 锚点！"
    /sbin/pfctl -a "$ANCHOR" -s rules 2>/dev/null
    ;;
  verify)
    # 真机越权测试：以各账号身份逐项去连，证明数据包真被挡住（L3 P1-02）；要在仓库里运行（要用旁边的 dt_pf_verify.py）
    here=$(cd "$(dirname "$0")" && pwd)
    [ -f "$here/dt_pf_verify.py" ] || { echo "找不到 $here/dt_pf_verify.py：请在仓库里运行 sudo bash ops/dt_worker_pf.sh verify"; exit 1; }
    shift
    /usr/bin/python3 -I "$here/dt_pf_verify.py" "$@"   # verify repeat <次数>：诊断偶发漏过
    ;;
  uninstall)
    launchctl bootout system "$PLIST" 2>/dev/null || true
    /sbin/pfctl -q -a "$ANCHOR" -F rules 2>/dev/null || true
    rm -f "$PLIST" "$RULES" "$SELF" "$STATUS"
    echo "门卫规则已拆掉（pf 本身保持开启，没有别的规则时不影响上网）。"
    ;;
  *)
    echo "用法：sudo bash $0 install|uninstall|status|verify"
    exit 1
    ;;
esac
