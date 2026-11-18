#!/bin/bash
# 控制面、秘书两个账号的初始化（ADR-015 批 0E-1）：dt-gate、dt-secretary 由常浩在系统设置里建成「标准」账号，
# 这个脚本在主账号里用 sudo 跑，不用登录进那两个账号。
#
# 用法（主账号终端，要输管理员密码）：
#   sudo bash ops/dt_accounts_setup.sh install   核对账号 → 收紧目录权限 → 装「不登录读凭据」探针并跑一次
#   sudo bash ops/dt_accounts_setup.sh status    只看：账号核对结果和探针最近一次的结果
#   sudo bash ops/dt_accounts_setup.sh remove-probe   拆掉探针（结果文件保留）
#
# 做的事（可重复跑）：
#   1. 核对：账号存在、是标准账号（不在 admin 组）、登录界面已隐藏、不能解锁文件保险箱；后两项只提示，不替你改
#   2. 家目录改成 700（系统默认 750，同组 staff 的其他账号能列出里面有什么）
#   3. 以账号自己的身份（ops/dt_account_init_user.sh）：登录 shell 的 umask 设成 077，放测试值文件 ~/.dt/probe.secret（600，随机数，不是真凭据）
#      root 不按路径碰账号能改的任何文件，只动「只有 root 能改里面条目」的目录里的东西（L3 P1-01）
#   4. 装探针：LaunchDaemon cn.digital-twin.probe.<账号>，以那个账号身份、在没人登录它时运行 ops/dt_account_probe.sh，
#      开机和安装时各跑一次；结果写到 /Users/Shared/dt-accounts/probe/<账号>/result.txt（所有人可读，不含凭据）
# 不建账号、不改密码、不装中台和猛鬼兽（0E-2、0E-5 再加）、不碰 dt-worker。
set -euo pipefail

ACCOUNTS="dt-gate dt-secretary"
HERE=$(cd "$(dirname "$0")" && pwd)
DIR="/Library/Application Support/DigitalTwin"
PROBE_SRC="$HERE/dt_account_probe.sh"
INIT_USER="$HERE/dt_account_init_user.sh"
PROBE="$DIR/dt_account_probe.sh"
SHARED=/Users/Shared/dt-accounts
MARK="# umask（数字分身 ADR-015 0E-1）"

[ "$(id -u)" = 0 ] || { echo "要用 sudo 运行：sudo bash $0 ${1:-install}"; exit 1; }

label() { echo "cn.digital-twin.probe.$1"; }
plist() { echo "/Library/LaunchDaemons/$(label "$1").plist"; }

# 核对一个账号；硬问题（不存在、是管理员）返回 1，软问题只打印提示
check_account() {
  local u=$1 bad=0
  if ! id "$u" >/dev/null 2>&1; then
    echo "  [停下] 没有 $u 这个账号：请在「系统设置 → 用户与群组」建成「标准」账号"
    return 1
  fi
  if dseditgroup -o checkmember -m "$u" admin >/dev/null 2>&1; then
    echo "  [停下] $u 是管理员：请在「系统设置 → 用户与群组」改成「标准」"
    bad=1
  fi
  if [ "$(dscl . -read "/Users/$u" IsHidden 2>/dev/null | awk '{print $2}')" = 1 ]; then
    echo "  登录界面：已隐藏"
  else
    echo "  [提示] 登录界面还能看到 $u；要藏起来：sudo dscl . create /Users/$u IsHidden 1"
  fi
  if fdesetup list 2>/dev/null | cut -d, -f1 | grep -qx "$u"; then
    echo "  [提示] $u 能解锁文件保险箱；要去掉：sudo fdesetup remove -user $u"
  else
    echo "  文件保险箱：不能解锁"
  fi
  return $bad
}

# root 往账号自己能改的目录里写之前，拒绝符号链接：否则账号能把它指向系统文件，让 root 去改（重跑时尤其要防）
no_link() {
  local p
  for p in "$@"; do
    if [ -L "$p" ]; then echo "  [停下] $p 是符号链接，不碰；请人工查看"; return 1; fi
  done
}

# 一个目录「只有 root 能改里面的条目」：属 root、不是链接、同组和其他人不能写。
# 这样的目录里，账号换不掉任何一项，root 按路径操作其中的条目才安全。
root_dir() {
  [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -f %u "$1")" = 0 ] && [ $(( 0$(stat -f %Lp "$1") & 022 )) = 0 ]
}

# 家目录本身由 root 收紧（它的上一级 /Users 只有 root 能改，账号换不掉这个目录项）；
# 家目录里面的东西一律以账号自己的身份写（ops/dt_account_init_user.sh 经标准输入交给它），
# root 不按路径碰账号能改的任何文件：账号就算同时在偷换链接、硬链接、目录，能改坏的也只有它本来就能改的东西（L3 P1-01）。
harden() {
  local u=$1 home
  home=$(dscl . -read "/Users/$u" NFSHomeDirectory | awk '{print $2}')
  case "$home" in /Users/?*) ;; *) echo "  [停下] $u 的家目录不在 /Users 下：$home"; return 1 ;; esac
  root_dir "$(dirname "$home")" || { echo "  [停下] $(dirname "$home") 不是只有 root 能改的目录"; return 1; }
  [ -d "$home" ] && no_link "$home" || { echo "  [停下] $u 的家目录 $home 不存在或不是普通目录"; return 1; }
  chown "$u:staff" "$home"
  chmod 700 "$home"
  echo "  家目录 $home：$(stat -f %Lp "$home")"
  sudo -u "$u" -H /bin/bash -s -- "$home" "$MARK" < "$INIT_USER" || { echo "  [停下] 以 $u 身份初始化没做完"; return 1; }
}

write_plist() {
  local u=$1 p
  p=$(plist "$u")
  cat > "$p" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$(label "$u")</string>
  <key>UserName</key><string>$u</string>
  <key>GroupName</key><string>staff</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$PROBE</string></array>
  <key>RunAtLoad</key><true/>
  <key>Umask</key><integer>63</integer>
</dict>
</plist>
EOF
  chown root:wheel "$p"
  chmod 644 "$p"
}

# 装上并跑一次；等结果文件更新（最多 40 秒）
load_probe() {
  local u=$1 p l res before
  p=$(plist "$u"); l=$(label "$u"); res="$SHARED/probe/$u/result.txt"
  before=$(stat -f %m "$res" 2>/dev/null || echo 0)
  # bootout 是异步的，等旧任务卸完再装，装失败隔几秒重试（全局坑 G-18）
  launchctl bootout "system/$l" 2>/dev/null || true
  for _ in $(seq 20); do launchctl print "system/$l" >/dev/null 2>&1 || break; sleep 0.5; done
  local ok=no
  for _ in 1 2 3; do
    if launchctl bootstrap system "$p" 2>/dev/null; then ok=yes; break; fi
    sleep 3
  done
  [ "$ok" = yes ] || { echo "  [停下] 探针没装上：sudo launchctl bootstrap system $p"; return 1; }
  for _ in $(seq 40); do
    [ "$(stat -f %m "$res" 2>/dev/null || echo 0)" -gt "$before" ] && break
    sleep 1
  done
  [ "$(stat -f %m "$res" 2>/dev/null || echo 0)" -gt "$before" ] || { echo "  [停下] 40 秒没等到探针结果：$res"; return 1; }
}

# 结果文件在账号自己的目录里：以账号身份读，账号把它换成指向别处的链接也读不到它本来读不到的东西
show_result() {
  local res="$SHARED/probe/$1/result.txt"
  sudo -u "$1" /bin/cat "$res" 2>/dev/null | sed 's/^/    /' | grep . || echo "    （还没有探针结果）"
}

case "${1:-install}" in
  install)
    [ -f "$PROBE_SRC" ] && [ -f "$INIT_USER" ] || { echo "找不到 $PROBE_SRC 或 $INIT_USER"; exit 1; }
    stop=0
    for u in $ACCOUNTS; do
      echo "== $u"
      check_account "$u" || { stop=1; continue; }
      harden "$u" || stop=1
    done
    [ "$stop" = 0 ] || { echo "有账号没过核对，先按上面提示处理，再重跑。"; exit 1; }
    mkdir -p "$DIR"; chown root:wheel "$DIR"; chmod 755 "$DIR"
    install -o root -g wheel -m 755 "$PROBE_SRC" "$PROBE"
    # /Users/Shared 谁都能建东西：mkdir 不跟链接；建完（或已有）先确认是 root 的普通目录、别人不能写，
    # 之后它在带粘滞位的 /Users/Shared 里别人删不掉、换不掉，里面的条目只有 root 能改
    mkdir "$SHARED" 2>/dev/null || true
    root_dir "$SHARED" || { echo "[停下] $SHARED 不是 root 的普通目录或别人能写，请人工查看"; exit 1; }
    chmod 755 "$SHARED"
    install -d -o root -g wheel -m 755 "$SHARED/probe"
    root_dir "$SHARED/probe" || { echo "[停下] $SHARED/probe 不是 root 的普通目录"; exit 1; }
    for u in $ACCOUNTS; do
      install -d -o "$u" -g staff -m 755 "$SHARED/probe/$u"
      write_plist "$u"
      echo "== $u 探针"
      load_probe "$u" || stop=1
      show_result "$u"
    done
    [ "$stop" = 0 ] || exit 1
    echo "完成。把上面的结果交给 Claude 看，或回会话说「装好了」。"
    ;;
  status)
    for u in $ACCOUNTS; do
      echo "== $u"
      check_account "$u" || true
      home=$(dscl . -read "/Users/$u" NFSHomeDirectory 2>/dev/null | awk '{print $2}')
      [ -n "$home" ] && echo "  家目录 $home：$(stat -f %Lp "$home" 2>/dev/null)"
      echo "  探针结果："
      show_result "$u"
    done
    ;;
  remove-probe)
    for u in $ACCOUNTS; do
      launchctl bootout "system/$(label "$u")" 2>/dev/null || true
      rm -f "$(plist "$u")"
    done
    rm -f "$PROBE"
    echo "探针已拆掉（结果文件留在 $SHARED/probe/）。"
    ;;
  *)
    echo "用法：sudo bash $0 install|status|remove-probe"
    exit 1
    ;;
esac
