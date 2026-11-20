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
#   1. 核对：账号存在、uid 大于 0 且只对应这一个账号、是标准账号（不在 admin 组）、没有 sudo 权限、不是自动登录账号、
#      远程登录 / 屏幕共享 / 远程管理（ARD）开着时它没有权限（这几项不过就停下）；登录界面已隐藏、不能解锁文件保险箱只提示，
#      不替你改。目录服务、dseditgroup、ps 查不清的一律当「不过」（L3 第 3 轮 P1-01、P2-02）
#   2. 家目录必须正好是 /Users/<账号>，改成 700（系统默认 750，同组 staff 的其他账号能列出里面有什么）
#   3. 以账号自己的身份（ops/dt_account_init_user.sh）：登录 shell 的 umask 设成 077，放测试凭据 ~/.dt/probe.secret
#      （600，随机数，不是真凭据；0E-2 起真凭据也这样存成只有账号能读的文件，常浩 11-19 定）；它的 SHA-256 由 root 记下
#      root 不按路径碰账号能改的任何文件，只动「只有 root 能改里面条目」的目录里的东西（L3 P1-01）
#   4. 装探针：LaunchDaemon cn.digital-twin.probe.<账号>，以那个账号身份、在没人登录它时运行 ops/dt_account_probe.sh，
#      开机、安装时各跑一次，之后每 10 分钟一次（开机后第一份另存 boot-<开机时间>.txt）；结果写到 /Users/Shared/dt-accounts/probe/<账号>/result.txt（所有人可读，不含凭据）。
#      每次安装给探针一个新的随机编号，结果里带上它、最后一行是 done=1、探针进程退出码为 0，才算这次的结果；
#      读到的测试凭据要和 root 记下的 SHA-256 一致才算「读得到」。
#      结果文件由账号自己写，账号能伪造它：安装时粗查一遍账号下有没有系统自带之外的程序在跑（现在这两个账号什么都不跑）。
#      这只是粗查，不算严格证明（同一个系统程序也能被拿来跑别的脚本）；探针只用来诊断，不当作授权或身份凭证（L3 P2-01）。
# root 写任何文件之前先核对路径：从 / 起每一级都只有 root 能改（ops/dt_root_guard.sh，和门卫规则共用，L3 P2-05）。
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
# 从仓库里运行时当前目录在主账号家目录下（700），以别的账号身份起的进程进不去：一律从 / 出发
cd /

[ "$(id -u)" = 0 ] || { echo "要用 sudo 运行：sudo bash $0 ${1:-install}"; exit 1; }
# shellcheck source=dt_root_guard.sh
. "$HERE/dt_root_guard.sh" || { echo "找不到 $HERE/dt_root_guard.sh"; exit 1; }

label() { echo "cn.digital-twin.probe.$1"; }
plist() { echo "/Library/LaunchDaemons/$(label "$1").plist"; }

# 核对一个账号；硬问题返回 1，软问题只打印提示。uid 不过就直接停：后面的「以账号身份」都靠它不是 root
check_account() {
  local u=$1 bad=0 m
  if ! id "$u" >/dev/null 2>&1; then
    echo "  [停下] 没有 $u 这个账号：请在「系统设置 → 用户与群组」建成「标准」账号"
    return 1
  fi
  if ! uid_ok "$u"; then
    echo "  [停下] $u 的 uid 是 0、和别的账号重复，或目录服务查不清（dscl . -list /Users UniqueID 查看）"
    return 1
  fi
  is_member "$u" admin && m=0 || m=$?
  if [ "$m" = 0 ]; then
    echo "  [停下] $u 是管理员：请在「系统设置 → 用户与群组」改成「标准」"
    bad=1
  elif [ "$m" != 1 ]; then
    echo "  [停下] 查不清 $u 是不是管理员（dseditgroup -o checkmember -m $u admin）"
    bad=1
  fi
  # 主组不能是 wheel（0），也不能在 wheel 组里（审查第 4 轮 P3-2）
  m=$(dscl . -read "/Users/$u" PrimaryGroupID 2>/dev/null | awk '{print $2}')
  case $m in
    ''|*[!0-9]*) echo "  [停下] 读不出 $u 的主组（dscl . -read /Users/$u PrimaryGroupID）"; bad=1 ;;
    0) echo "  [停下] $u 的主组是 wheel（0）：请改成 staff（20）"; bad=1 ;;
  esac
  is_member "$u" wheel && m=0 || m=$?
  if [ "$m" != 1 ]; then
    echo "  [停下] $u 在 wheel 组里或查不清（dseditgroup -o checkmember -m $u wheel）"
    bad=1
  fi
  if [ "$(dscl . -read "/Users/$u" IsHidden 2>/dev/null | awk '{print $2}')" = 1 ]; then
    echo "  登录界面：已隐藏"
  else
    echo "  [提示] 登录界面还能看到 ${u}；要藏起来：sudo dscl . create /Users/$u IsHidden 1"
  fi
  if fdesetup list 2>/dev/null | cut -d, -f1 | grep -qx "$u"; then
    echo "  [提示] $u 能解锁文件保险箱；要去掉：sudo fdesetup remove -user $u"
  else
    echo "  文件保险箱：不能解锁"
  fi
  # 以实际权限为准：admin 组之外，sudoers 里单独授权也算
  if sudo -l -U "$u" 2>&1 | grep -q "is not allowed to run sudo"; then
    echo "  sudo：没有权限"
  else
    echo "  [停下] $u 有 sudo 权限（sudo -l -U $u 查看）；请从 /etc/sudoers 和 /etc/sudoers.d 里去掉"
    bad=1
  fi
  if [ "$(defaults read /Library/Preferences/com.apple.loginwindow autoLoginUser 2>/dev/null)" = "$u" ]; then
    echo "  [停下] $u 是自动登录账号：请在「系统设置 → 用户与群组 → 自动以此身份登录」改成「关闭」"
    bad=1
  fi
  remote_access "$u" com.openssh.sshd com.apple.access_ssh "远程登录" || bad=1
  remote_access "$u" com.apple.screensharing com.apple.access_screensharing "屏幕共享" || bad=1
  ard_access "$u" || bad=1
  return $bad
}

# 远程管理（ARD）：没开就行；开着时这个账号不能有权限——「所有本地用户」没勾、账号记录上没有 naprivs 权限、
# 不在 ARD 的几个授权组里（L3 第 3 轮 P2-02：开着时不再只提示）。哪一项查不清都停下。
ard_access() {
  local u=$1 rc all privs groups g m
  pgrep -qx ARDAgent && rc=0 || rc=$?
  if [ "$rc" = 1 ]; then echo "  远程管理：没开"; return 0; fi
  [ "$rc" = 0 ] || { echo "  [停下] 查不清远程管理开没开（pgrep 出错）"; return 1; }
  all=$(defaults read /Library/Preferences/com.apple.RemoteManagement ARD_AllLocalUsers 2>/dev/null || echo 0)
  if [ "$all" != 0 ]; then
    echo "  [停下] 远程管理开着，允许所有本地用户：请在「系统设置 → 通用 → 共享 → 远程管理」改成「仅这些用户」且不含 $u"
    return 1
  fi
  privs=$(dscl . -read "/Users/$u" naprivs 2>&1) || true
  case $privs in
    "No such key: naprivs"*|"naprivs: 0") ;;
    *) echo "  [停下] 远程管理开着，$u 有远程管理权限或查不清（${privs%%$'\n'*}）"; return 1 ;;
  esac
  groups=$(dscl . -list /Groups 2>/dev/null) || { echo "  [停下] 读不出本机的组，查不清远程管理权限"; return 1; }
  for g in com.apple.local.ard_admin com.apple.local.ard_interact com.apple.local.ard_manage com.apple.local.ard_reports; do
    printf '%s\n' "$groups" | grep -qx "$g" || continue
    is_member "$u" "$g" && m=0 || m=$?
    [ "$m" = 1 ] || { echo "  [停下] 远程管理开着，$u 在 $g 里或查不清"; return 1; }
  done
  echo "  远程管理：开着，$u 没有权限"
}

# 远程登录 / 屏幕共享：服务没开就行；开着时，系统的允许名单（com.apple.access_*）要存在且不含这个账号。
# 允许名单不存在 = 「所有用户」都能登录。
# 名单里有没有它要分清「不是成员」和「查不清」，查不清当成能登录（L3 第 3 轮 P2-02）。
remote_access() {
  local u=$1 job=$2 group=$3 name=$4 m=2 rc
  # 只有「找不到这个服务」（返回 113）才算没开；别的出错是查不清（审查第 4 轮 P3-1）
  launchctl print "system/$job" >/dev/null 2>&1 && rc=0 || rc=$?
  case $rc in
    0) ;;
    113) echo "  ${name}：没开"; return 0 ;;
    *) echo "  [停下] 查不清${name}开没开（launchctl print system/$job 返回 ${rc}）"; return 1 ;;
  esac
  if dscl . -read "/Groups/$group" >/dev/null 2>&1; then
    is_member "$u" "$group" && m=0 || m=$?
  fi
  if [ "$m" = 1 ]; then
    echo "  ${name}：开着，$u 不在允许名单里"
  else
    echo "  [停下] ${name}开着，$u 能登录或查不清：请在「系统设置 → 通用 → 共享 → ${name}」改成「仅这些用户」且不含 $u"
    return 1
  fi
}

# 账号下有没有别的进程在跑（探针结果由账号自己写，有别的进程就可能被改）；粗查，不是严格证明。
# macOS 会给每个账号起几个自带的后台进程（distnoted、lsd、mdbulkimport 等），它们在系统完整性保护（SIP）的
# /System、/usr（/usr/local 除外）、/bin、/sbin 里，账号改不了，不算；只数这些目录之外的程序。
# 但 shell、python、perl 这类解释器虽然在系统目录里，也能跑账号自己的脚本：照样算（L3 第 3 轮 P2-01 附带）。
# ps 出错（不是「这个账号没有进程」）就停下，不当成没有进程（L3 第 3 轮 P2-02）。
no_processes() {
  local out err rc others
  err=$(mktemp)
  out=$(ps -U "$1" -o comm= 2>"$err") && rc=0 || rc=$?
  if [ "$rc" != 0 ] && [ -s "$err" ]; then
    echo "  [停下] 查不清 ${1} 下有没有进程在跑：$(head -1 "$err")"
    rm -f "$err"
    return 1
  fi
  rm -f "$err"
  others=$(printf '%s\n' "$out" | grep . |
    grep -Ev '^/(System|bin|sbin)/|^/usr/(bin|sbin|libexec|lib)/' ; printf '%s\n' "$out" |
    grep -E '^/(bin|usr/bin)/(ba|z|k|c|tc|da)?sh$|^/usr/bin/(python|perl|ruby|osascript|env|nohup|screen|tmux|swift|node|php|tclsh|expect)' || true)
  [ -z "$others" ] && return 0
  echo "  [停下] ${1} 下有系统以外的进程在跑；探针结果可能被改，请先停掉："
  echo "$others" | sed 's/^/    /'
  return 1
}

# root 往账号自己能改的目录里写之前，拒绝符号链接：否则账号能把它指向系统文件，让 root 去改（重跑时尤其要防）
no_link() {
  local p
  for p in "$@"; do
    if [ -L "$p" ]; then echo "  [停下] $p 是符号链接，不碰；请人工查看"; return 1; fi
  done
}

# root_dir、root_path、sticky_dir、uid_ok、is_member 在 ops/dt_root_guard.sh（和门卫规则共用）

# 家目录本身由 root 收紧（它的上一级 /Users 只有 root 能改，账号换不掉这个目录项）；
# 家目录里面的东西一律以账号自己的身份写（ops/dt_account_init_user.sh 经标准输入交给它），
# root 不按路径碰账号能改的任何文件：账号就算同时在偷换链接、硬链接、目录，能改坏的也只有它本来就能改的东西（L3 P1-01）。
harden() {
  local u=$1 home
  home=$(dscl . -read "/Users/$u" NFSHomeDirectory | awk '{print $2}') || { echo "  [停下] 读不出 $u 的家目录"; return 1; }
  # 只认 /Users/<账号> 这一个写法：不认 ..、多一层、结尾斜杠（L3 P1-01）
  [ "$home" = "/Users/$u" ] || { echo "  [停下] $u 的家目录不是 /Users/${u}：$home"; return 1; }
  # 动手前再核一次 uid（check_account 核过；这里紧挨着 chown、sudo -u，防中间被改，L3 第 3 轮 P1-01）
  uid_ok "$u" || { echo "  [停下] $u 的 uid 是 0、不唯一，或目录服务查不清"; return 1; }
  # 上一级 /Users 连同 / 都只有 root 能改，账号换不掉这个目录项（L3 第 3 轮 P2-05）
  root_path /Users || { echo "  [停下] /Users（或 /）不是只有 root 能改的目录"; return 1; }
  [ -d "$home" ] && no_link "$home" || { echo "  [停下] $u 的家目录 $home 不存在或不是普通目录"; return 1; }
  chown "$u:staff" "$home"
  chmod 700 "$home"
  echo "  家目录 ${home}：$(stat -f %Lp "$home")"
  local out sum
  out=$(sudo -u "$u" -H /bin/bash -s -- "$home" "$MARK" < "$INIT_USER") || { echo "$out"; echo "  [停下] 以 $u 身份初始化没做完"; return 1; }
  echo "$out" | grep -v '^secret_sha256='
  sum=$(echo "$out" | sed -n 's/^secret_sha256=\([0-9a-f]\{64\}\)$/\1/p')
  [ -n "$sum" ] || { echo "  [停下] 没拿到测试凭据的 SHA-256"; return 1; }
  # root 自己的目录里记下应有的 SHA-256，探针读到的要和它一致；目录已有的话先核对再写（L3 第 3 轮 P2-05）
  if [ -e "$DIR/probe" ] || [ -L "$DIR/probe" ]; then
    root_dir "$DIR/probe" || { echo "  [停下] $DIR/probe 不是只有 root 能改的目录"; return 1; }
  fi
  install -d -o root -g wheel -m 700 "$DIR/probe"
  root_dir "$DIR/probe" || { echo "  [停下] $DIR/probe 不是只有 root 能改的目录"; return 1; }
  ( umask 077; echo "$sum" > "$DIR/probe/$u.sha256.tmp" ) && mv -f "$DIR/probe/$u.sha256.tmp" "$DIR/probe/$u.sha256"
}

write_plist() {
  local u=$1 nonce=$2 p
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
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>/Users/$u</string>
    <key>PATH</key><string>/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>DT_PROBE_NONCE</key><string>$nonce</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>600</integer>
  <key>Umask</key><integer>63</integer>
</dict>
</plist>
EOF
  chown root:wheel "$p"
  chmod 644 "$p"
}

# 装上并跑一次；等这次的结果（带本次编号、最后一行 done=1、进程退出码 0，最多 40 秒）
load_probe() {
  local u=$1 nonce=$2 p l
  p=$(plist "$u"); l=$(label "$u")
  # bootout 是异步的，等旧任务卸完再装，装失败隔几秒重试（全局坑 G-18）
  launchctl bootout "system/$l" 2>/dev/null || true
  for _ in $(seq 20); do launchctl print "system/$l" >/dev/null 2>&1 || break; sleep 0.5; done
  local ok=no
  for _ in 1 2 3; do
    if launchctl bootstrap system "$p" 2>/dev/null; then ok=yes; break; fi
    sleep 3
  done
  [ "$ok" = yes ] || { echo "  [停下] 探针没装上：sudo launchctl bootstrap system $p"; return 1; }
  local got=no
  for _ in $(seq 40); do
    if read_result "$u" | grep -qx "nonce=$nonce" && read_result "$u" | tail -1 | grep -qx "done=1"; then got=yes; break; fi
    sleep 1
  done
  [ "$got" = yes ] || { echo "  [停下] 40 秒没等到这次的探针结果"; return 1; }
  for _ in $(seq 10); do launchctl print "system/$l" 2>/dev/null | grep -q 'last exit code = 0' && return 0; sleep 1; done
  echo "  [停下] 探针进程退出码不是 0：sudo launchctl print system/$l"
  return 1
}

# 结果文件在账号自己的目录里：以账号身份读，账号把它换成指向别处的链接也读不到它本来读不到的东西
read_result() { sudo -u "$1" /bin/cat "$SHARED/probe/$1/result.txt" 2>/dev/null; }

# 读到的测试凭据和 root 记下的 SHA-256 对不对得上
secret_verdict() {
  local want got
  want=$(cat "$DIR/probe/$1.sha256" 2>/dev/null)
  got=$(read_result "$1" | sed -n 's/^secret_sha256=//p')
  if [ -n "$want" ] && [ "$got" = "$want" ]; then echo "  测试凭据：没人登录时读得到，内容一致"
  else echo "  [注意] 测试凭据：读不到或内容不一致（应为 ${want:-未记录}，读到 ${got:-无}）"; return 1; fi
}

show_result() {
  read_result "$1" | sed 's/^/    /' | grep . || echo "    （还没有探针结果）"
}

case "${1:-install}" in
  install)
    [ -f "$PROBE_SRC" ] && [ -f "$INIT_USER" ] || { echo "找不到 $PROBE_SRC 或 $INIT_USER"; exit 1; }
    # 先核对再动手（L3 第 3 轮 P2-05）：$DIR 的上一级、LaunchDaemons 从 / 起每一级只有 root 能改；$DIR 已有就核它本身
    root_path "$(dirname "$DIR")" || { echo "[停下] $(dirname "$DIR") 不是只有 root 能改的目录，请人工查看"; exit 1; }
    root_path /Library/LaunchDaemons || { echo "[停下] /Library/LaunchDaemons 不是只有 root 能改的目录，请人工查看"; exit 1; }
    if [ -e "$DIR" ] || [ -L "$DIR" ]; then
      root_dir "$DIR" || { echo "[停下] $DIR 不是只有 root 能改的目录，请人工查看"; exit 1; }
    else
      mkdir -m 755 "$DIR"; chown root:wheel "$DIR"
      root_dir "$DIR" || { echo "[停下] 新建的 $DIR 不对，请人工查看"; exit 1; }
    fi
    stop=0
    for u in $ACCOUNTS; do
      echo "== $u"
      check_account "$u" || { stop=1; continue; }
      no_processes "$u" || { stop=1; continue; }
      harden "$u" || stop=1
    done
    [ "$stop" = 0 ] || { echo "有账号没过核对，先按上面提示处理，再重跑。"; exit 1; }
    install -o root -g wheel -m 755 "$PROBE_SRC" "$PROBE"
    # /Users/Shared 谁都能建东西：先核对它本身属 root、有粘滞位；mkdir 不跟链接；建完（或已有）再确认是 root 的普通目录、
    # 别人不能写，之后它在 /Users/Shared 里别人删不掉、换不掉，里面的条目只有 root 能改
    sticky_dir /Users/Shared || { echo "[停下] /Users/Shared 不是属 root、带粘滞位的目录，请人工查看"; exit 1; }
    mkdir "$SHARED" 2>/dev/null || true
    root_dir "$SHARED" || { echo "[停下] $SHARED 不是 root 的普通目录或别人能写，请人工查看"; exit 1; }
    chmod 755 "$SHARED"
    if [ -e "$SHARED/probe" ] || [ -L "$SHARED/probe" ]; then
      root_dir "$SHARED/probe" || { echo "[停下] $SHARED/probe 不是 root 的普通目录"; exit 1; }
    fi
    install -d -o root -g wheel -m 755 "$SHARED/probe"
    root_dir "$SHARED/probe" || { echo "[停下] $SHARED/probe 不是 root 的普通目录"; exit 1; }
    for u in $ACCOUNTS; do
      install -d -o "$u" -g staff -m 755 "$SHARED/probe/$u"
      nonce=$(openssl rand -hex 8)
      write_plist "$u" "$nonce"
      echo "== $u 探针（编号 ${nonce}）"
      load_probe "$u" "$nonce" || stop=1
      show_result "$u"
      secret_verdict "$u" || stop=1
    done
    [ "$stop" = 0 ] || exit 1
    echo "完成。把上面的结果交给 Claude 看，或回会话说「装好了」。"
    ;;
  status)
    for u in $ACCOUNTS; do
      echo "== $u"
      check_account "$u" || true
      home=$(dscl . -read "/Users/$u" NFSHomeDirectory 2>/dev/null | awk '{print $2}')
      [ -n "$home" ] && echo "  家目录 ${home}：$(stat -f %Lp "$home" 2>/dev/null)"
      echo "  探针结果："
      show_result "$u"
      secret_verdict "$u" || true
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
