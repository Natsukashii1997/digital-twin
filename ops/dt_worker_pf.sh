#!/bin/bash
# 工人、秘书账号的门卫规则（digital-twin#190 0A 隔离门；ADR-015 批 0E-1 加秘书）：macOS 自带防火墙 pf。
# 白名单、默认拒绝（L3 P1-02）：两个账号的 TCP / UDP 只准连下面列出的本机端口，其余一律拒绝——
# 本机其他端口、局域网、公网直连（包括公网 DNS）都挡，上网只能经过滤代理 8090：
#   dt-worker     中台 3350、过滤代理 8090
#   dt-secretary  只读窗口 3352、秘书窄接口 3353（0E-2 由 dt-gate 开）、过滤代理 8090；连不到中台 3350
# dt-gate 是控制面，不在这里限制。
# 两个账号也不能对外提供服务（入站一律拒绝，审查第 4 轮 P2-8）：不然一个账号先占住另一个准连的端口
# （中台、过滤代理没在跑，或秘书窄接口还没开），就能冒充它、在两个账号之间搭出一条通道。
# 承诺的范围（常浩 11-20 定，L3 第 3 轮 P1-02）：这两个账号的 TCP、UDP 只能经 8090 出去。pf 按账号认不出的
# 接受为已知风险、不在承诺里：系统服务替账号代发的流量（系统解析器 mDNSResponder 代发的 DNS 查询、trustd 联网查证书、
# nsurlsessiond 后台下载这一类），和 ICMP（ping）——真要堵得全机禁用。经 IPv6 套接字连 ::ffff:127.0.0.1 这类 IPv4 映射地址，发出去的是 IPv4 包，照样按上面的规则判。
# 规则没在生效（状态不是 state=ok、超过 10 分钟没刷新、或不是本次开机写的）时，工人的启动脚本拒绝开工
# （常浩 11-20 定，L3 第 3 轮 P1-03）；规则重新加载过、或判定不在生效时，结束两个账号的全部进程（审查第 4 轮 P2-4、P2-5）。
#
# 用法（主账号终端，要输管理员密码）：
#   sudo bash ops/dt_worker_pf.sh install     装上：规则 + 开机和每 5 分钟自动重新加载
#   sudo bash ops/dt_worker_pf.sh uninstall   拆掉
#   sudo bash ops/dt_worker_pf.sh status      看规则有没有在生效
#   sudo bash ops/dt_worker_pf.sh verify      真机越权测试：以各账号身份去连，证明真挡住了（ops/dt_pf_verify.py）
#   sudo bash ops/dt_worker_pf.sh verify stress 600  诊断：两个账号连续 600 秒不停去连，同时反复强制重新加载规则，
#                                                   看重新加载那一刻会不会漏过（漏过时当场抓 pf 的现场）
#   bash ops/dt_worker_pf.sh rules dt-worker [dt-secretary]   只打印规则，不用 sudo（测试用）
# apply 由开机任务调用，不用手动运行；每次按本机现有账号重新生成规则，dt-secretary 建好后 5 分钟内自动管上。
# 实际加载的规则和应有的一模一样时不重新加载（L3 第 3 轮 P1-02：11-19 两次偶发漏过都在重新加载附近，平时不再重载）。
#
# 不改 /etc/pf.conf：规则挂在系统自带的 com.apple/* 锚点下面（com.apple/dt-worker，名字沿用 0A）。
# 每次逐项核对：目录服务查得清账号（查不清就重试约一分钟，仍不清就不动规则、写 failed——不当成账号不存在）；
# 每个账号的 uid 大于 0、只对应这一个账号；主规则集里真的挂着 com.apple/* 锚点、锚点前面只有 scrub 类规则，
# com.apple 下别的锚点里没有出站的 quick 放行，没有网卡被 pf 跳过（set skip），没有带 pass 的地址转换规则
# （否则数据包轮不到门卫规则）；实际加载的规则和规则文件解析出来的一模一样。要重新加载时先把状态写成 state=applying。
# 全部对上才写 state=ok，中途任何一步出错都写 state=failed 和原因（所有人可读）。状态里记本次开机时间 boot=。
# 工人的启动脚本、每日巡检都据此判断门卫规则是不是在生效。
# 写任何文件之前先核对路径：从 / 起每一级都只有 root 能改（ops/dt_root_guard.sh，和账号初始化共用）。
set -Eeuo pipefail   # -E：函数里出错也触发 ERR，apply 中途任何一步失败都落到 state=failed

WORKER=dt-worker
SECRETARY=dt-secretary
ANCHOR=com.apple/dt-worker
DIR="/Library/Application Support/DigitalTwin"
RULES="$DIR/pf-dt-worker.rules"
SELF="$DIR/dt_worker_pf.sh"
STATUS="$DIR/pf-dt-worker.status"
GUARD="$DIR/dt_root_guard.sh"
DS_TRIES=12                                 # 目录服务查不清时最多试几次（每次隔 5 秒）
LABEL=cn.digital-twin.pf-dt-worker
PLIST="/Library/LaunchDaemons/$LABEL.plist"
HERE=$(cd "$(dirname "$0")" && pwd)

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
    # 入站按「收包的套接字属于谁」判：这个账号监听的端口谁都连不上；它自己连出去的回包走连接状态，不经这条
    echo "block return in quick proto { tcp, udp } from any to any user $u"
  done
}

# 本机现有的、要管的账号（逗号分隔）：工人必须有，秘书有就管。打印 NONE 是目录服务里确实没有工人，
# UNCLEAR 是目录服务查不清——不能当成「没有秘书」把它从规则里删掉（审查第 4 轮 P1-1）。
# 结果只靠打印、不靠返回码；apply 调它时在命令替换里先关掉 ERR 陷阱（set -E 下子 shell 里出错会先跑一遍 fail）
present_users() {
  local names
  names=$(user_list | awk '{print $1}') || { echo UNCLEAR; return 0; }
  case $'\n'"$names"$'\n' in *$'\n'"$WORKER"$'\n'*) ;; *) echo NONE; return 0 ;; esac
  case $'\n'"$names"$'\n' in
    *$'\n'"$SECRETARY"$'\n'*) echo "$WORKER,$SECRETARY" ;;
    *) echo "$WORKER" ;;
  esac
}

if [ "${1:-}" = rules ]; then
  shift
  print_rules "$@"
  exit
fi

[ "$(id -u)" = 0 ] || { echo "要用 sudo 运行：sudo bash $0 ${1:-install}"; exit 1; }
cd /
# 核对函数：从仓库运行时用旁边那份，开机任务用装在 $DIR 里的那份（和本脚本同一目录）
# shellcheck source=dt_root_guard.sh
. "$HERE/dt_root_guard.sh" || { echo "找不到 $HERE/dt_root_guard.sh"; exit 1; }

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
  printf 'at=%s boot=%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$(boot_sec)" "$*" > "$STATUS.tmp"
  chmod 644 "$STATUS.tmp"
  mv -f "$STATUS.tmp" "$STATUS"
}

fail() {
  trap - ERR
  write_status "state=failed enabled=$(pf_enabled) reason=${1// /_}"
  kill_accounts
  cat "$STATUS"
  exit 1
}

# 本次开机的时间（秒），写进状态：重启后旧的 state=ok 不算数（审查第 4 轮 P2-2）。读不出就是空，工人那边当不过。
boot_sec() { /usr/sbin/sysctl -n kern.boottime 2>/dev/null | sed -n 's/^{ sec = \([0-9][0-9]*\),.*/\1/p'; }

# 结束两个受管账号的全部进程（审查第 4 轮 P2-4、P2-5）：规则换过后，旧规则下建好的连接还挂在 pf 的连接状态表里、
# 不再过新规则；门卫判定不在生效时，正在跑的 CLI 也不能接着联网。账号不在、没有进程（pkill 返回 1）都不算错。
kill_accounts() {
  local u
  for u in "$WORKER" "$SECRETARY"; do
    id "$u" >/dev/null 2>&1 || continue
    /usr/bin/pkill -KILL -u "$u" 2>/dev/null || true
  done
}

pf_enabled() { /sbin/pfctl -s info 2>/dev/null | grep -q 'Status: Enabled' && echo yes || echo no; }

# 主规则集里挂着 com.apple/* 锚点：没挂，锚点里的规则一条都不生效
anchor_hooked() { /sbin/pfctl -s rules 2>/dev/null | grep -qx 'anchor "com.apple/\*" all'; }

# 数据包轮得到门卫规则（L3 第 3 轮 P1-03）：主规则集里 com.apple/* 锚点前面只能是 scrub 类；com.apple 下别的锚点
# （名字排在 dt-worker 前面的先判）和它们的子锚点里不能有 quick 放行——有的话，命中它的包在进门卫规则之前就放行了。
# 查不出来一律不过。不过时把挡路的那条规则打印出来（进状态文件的原因）。
order_ok() {
  local main before ifs nat hit
  main=$(/sbin/pfctl -s rules 2>/dev/null) || { echo "读不出主规则集"; return 1; }
  before=$(printf '%s\n' "$main" | sed '/^anchor "com.apple\/\*" all$/,$d')
  before=$(printf '%s\n' "$before" | grep -v '^scrub' | grep . | head -1 || true)
  [ -z "$before" ] || { echo "主规则集里锚点前面有：$before"; return 1; }
  # set skip 的网卡上 pf 一条规则都不执行（审查第 4 轮 P2-3）
  ifs=$(/sbin/pfctl -s Interfaces -v 2>/dev/null) || { echo "读不出 pf 的网卡列表"; return 1; }
  hit=$(printf '%s\n' "$ifs" | grep -F '(skip)' | head -1 || true)
  [ -z "$hit" ] || { echo "有网卡被 pf 跳过：$hit"; return 1; }
  # 带 pass 的地址转换规则命中的包不过过滤规则；主规则集里只认系统自带的 com.apple/* 转换锚点，别的锚点查不到里面
  nat=$(/sbin/pfctl -s nat 2>/dev/null) || { echo "读不出主规则集的地址转换规则"; return 1; }
  hit=$(printf '%s\n' "$nat" | grep -E '(^|[[:space:]])pass([[:space:]]|$)|-anchor ' \
        | grep -vE '^(nat|rdr|binat)-anchor "com\.apple/\*" all$' | head -1 || true)
  [ -z "$hit" ] || { echo "主规则集的地址转换里有：$hit"; return 1; }
  no_quick_pass com.apple 0
}

no_quick_pass() {
  local subs sub rules hit
  [ "$2" -lt 5 ] || { echo "$1 下的锚点层数太多，查不完"; return 1; }
  subs=$(/sbin/pfctl -a "$1" -s Anchors 2>/dev/null) || { echo "读不出 $1 下的锚点"; return 1; }
  for sub in $subs; do
    case $sub in "$1"/?*) ;; *) echo "认不出 $1 下的锚点名：$sub"; return 1 ;; esac   # pfctl 打印的是完整路径
    [ "$sub" = "$ANCHOR" ] && continue
    rules=$(/sbin/pfctl -a "$sub" -s rules 2>/dev/null) || { echo "读不出 $sub 的规则"; return 1; }
    # 只有入站的 quick 放行绕不过门卫的出站拒绝，不算（隔空投送等会加；审查第 4 轮 P3-5）
    hit=$(printf '%s\n' "$rules" | grep -E '^pass[[:space:]]' | grep -vE '^pass in[[:space:]]' \
          | grep -E '[[:space:]]quick([[:space:]]|$)' | head -1 || true)
    [ -z "$hit" ] || { echo "$sub 里有：$hit"; return 1; }
    rules=$(/sbin/pfctl -a "$sub" -s nat 2>/dev/null) || { echo "读不出 $sub 的地址转换规则"; return 1; }
    hit=$(printf '%s\n' "$rules" | grep -E '(^|[[:space:]])pass([[:space:]]|$)' | head -1 || true)
    [ -z "$hit" ] || { echo "$sub 的地址转换里有：$hit"; return 1; }
    no_quick_pass "$sub" $(( $2 + 1 )) || return 1
  done
}

apply() {
  # 状态文件、规则文件都写在 $DIR 里：先确认从 / 起每一级只有 root 能改，不然连状态都不写（写了也不可信）
  root_path "$DIR" || { echo "$DIR 不是只有 root 能改的目录，不动手"; exit 1; }
  trap 'fail "第 $LINENO 行出错"' ERR
  tries=0
  while :; do
    users=$(trap - ERR; present_users)
    [ "$users" = UNCLEAR ] && [ $(( tries += 1 )) -lt "$DS_TRIES" ] || break
    sleep 5                                 # 开机头一分钟目录服务常没就绪
  done
  case $users in
    NONE) fail "没有 $WORKER 这个账号" ;;
    UNCLEAR) fail "目录服务查不清本机账号，规则不动" ;;
  esac
  for u in ${users//,/ }; do uid_ok "$u" || fail "$u 的 uid 是 0、不唯一，或目录服务查不清"; done
  write_rules "$users"
  # 系统默认 pf.conf 里有 com.apple/* 锚点；被人换掉了就按系统默认的重新加载一次
  anchor_hooked || /sbin/pfctl -q -f /etc/pf.conf 2>/dev/null || true
  anchor_hooked || fail "主规则集没挂 com.apple/* 锚点"
  # 命令替换里先关掉 ERR 陷阱：不然 order_ok 不过时陷阱在子 shell 里先跑一遍 fail，原因里混进别的内容、进程也杀两遍
  why=$(trap - ERR; order_ok) || fail "有规则排在门卫规则前面放行：$why"
  # 实际加载的规则必须和规则文件解析出来的一模一样（同一个 pfctl 打印，按 uid 展开）；一样就不重新加载
  expected=$(/sbin/pfctl -nvf "$RULES" 2>/dev/null)
  [ -n "$expected" ] || fail "规则文件解析不出来"
  loaded=$(/sbin/pfctl -a "$ANCHOR" -s rules 2>/dev/null || true)
  reloaded=no
  if [ "$loaded" != "$expected" ]; then
    write_status "state=applying"           # 只在真要重新加载时写：平时每 5 分钟一次的核对不让工人撞上「进行中」（审查第 4 轮 P2-1）
    /sbin/pfctl -q -a "$ANCHOR" -f "$RULES" || fail "规则没加载上（pf 保留了旧规则）"
    reloaded=yes
    kill_accounts                           # 旧规则下建好的连接不过新规则
    loaded=$(/sbin/pfctl -a "$ANCHOR" -s rules 2>/dev/null || true)
  fi
  [ "$(pf_enabled)" = yes ] || /sbin/pfctl -q -E 2>/dev/null || true
  [ "$(pf_enabled)" = yes ] || fail "pf 没开"
  [ "$loaded" = "$expected" ] || {
    diff <(echo "$expected") <(echo "$loaded") | head -20 >&2 || true
    fail "加载上的规则和应有的不一致"
  }
  trap - ERR
  write_status "state=ok enabled=yes rules=$(echo "$loaded" | grep -c 'user = ') users=$users reloaded=$reloaded"
}

case "${1:-install}" in
  install)
    # 先核对再动手（L3 第 3 轮 P2-05）：DIR 的上一级、DIR 本身（已有的话）、LaunchDaemons 从 / 起每一级都只有 root 能改
    root_path "$(dirname "$DIR")" || { echo "[停下] $(dirname "$DIR") 不是只有 root 能改的目录，请人工查看"; exit 1; }
    root_path "$(dirname "$PLIST")" || { echo "[停下] $(dirname "$PLIST") 不是只有 root 能改的目录，请人工查看"; exit 1; }
    if [ -e "$DIR" ] || [ -L "$DIR" ]; then
      root_dir "$DIR" || { echo "[停下] $DIR 不是只有 root 能改的目录，请人工查看"; exit 1; }
    else
      mkdir -m 755 "$DIR"
      chown root:wheel "$DIR"
      root_dir "$DIR" || { echo "[停下] 新建的 $DIR 不对，请人工查看"; exit 1; }
    fi
    install -o root -g wheel -m 755 "$HERE/$(basename "$0")" "$SELF"   # 上面 cd / 了，不能用相对的 $0
    install -o root -g wheel -m 644 "$HERE/dt_root_guard.sh" "$GUARD"
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
    # 真机越权测试：以各账号身份逐项去连，证明数据包真被挡住（L3 P1-02）；要用旁边的 dt_pf_verify.py
    [ -f "$HERE/dt_pf_verify.py" ] || { echo "找不到 $HERE/dt_pf_verify.py：请在仓库里运行 sudo bash ops/dt_worker_pf.sh verify"; exit 1; }
    shift
    /usr/bin/python3 -I "$HERE/dt_pf_verify.py" "$@"   # verify stress <秒数>：诊断重新加载时会不会漏过
    ;;
  uninstall)
    launchctl bootout system "$PLIST" 2>/dev/null || true
    /sbin/pfctl -q -a "$ANCHOR" -F rules 2>/dev/null || true
    rm -f "$PLIST" "$RULES" "$SELF" "$GUARD" "$STATUS"
    echo "门卫规则已拆掉（pf 本身保持开启，没有别的规则时不影响上网）。"
    ;;
  *)
    echo "用法：sudo bash $0 install|uninstall|status|verify"
    exit 1
    ;;
esac
