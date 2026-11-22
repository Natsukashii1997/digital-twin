# root 动手前的核对（ADR-015 批 0E-1，L3 P1-01、P2-05）：dt_worker_pf.sh 和 dt_accounts_setup.sh 共用这一份（source 进来），
# 保护逻辑只写一处。门卫规则装好后，这份和 dt_worker_pf.sh 一起放在 /Library/Application Support/DigitalTwin/ 里。
# 只定义函数，不做任何事；任何一步查不清（命令出错、读不出 ACL、目录服务没回话）一律当成「不过」。

# 一个目录「只有 root 能改里面的条目」：属 root、不是链接、同组和其他人不能写、没有放行权限的 ACL
# （macOS 的 ACL 能在权限位之外单独给某个账号加 add_file、delete_child 等权限，有就一律不认）。
# 这样的目录里，别的账号换不掉任何一项，root 按路径操作其中的条目才安全。ACL 读不出来也不认（L3 第 3 轮 P2-05）。
root_dir() {
  local m
  [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -f %u "$1" 2>/dev/null)" = 0 ] || return 1
  m=$(stat -f %Lp "$1" 2>/dev/null) && [ -n "$m" ] && [ $(( 0$m & 022 )) = 0 ] || return 1
  no_allow_acl "$1"
}

# 目录上没有放行权限的 ACL（拒绝类 ACL 只会收紧，不管）；读不出来就不认
no_allow_acl() {
  local acl
  acl=$(ls -led "$1" 2>/dev/null) || return 1
  ! printf '%s\n' "$acl" | sed 1d | grep -q ' allow '
}

# 从 / 起每一级都是 root_dir：上一级别人能改，下面的目录项就能被整个换掉（L3 第 3 轮 P2-05：/Users 要连 / 一起核）。
# 只认绝对路径，不认 . 和 ..。
root_path() {
  local p=$1 cur="" part
  case $p in /*) ;; *) return 1 ;; esac
  case "/$p/" in */./*|*/../*) return 1 ;; esac
  root_dir / || return 1
  local IFS=/
  for part in ${p#/}; do
    [ -n "$part" ] || continue
    cur="$cur/$part"
    root_dir "$cur" || return 1
  done
}

# /Users/Shared 谁都能往里建东西，靠粘滞位保证别人删不掉、换不掉 root 建的目录：属 root、不是链接、有粘滞位、没有放行 ACL；
# 它的上一级（/Users）要是 root_path
sticky_dir() {
  local m
  root_path "$(dirname "$1")" || return 1
  [ -d "$1" ] && [ ! -L "$1" ] && [ "$(stat -f %u "$1" 2>/dev/null)" = 0 ] || return 1
  m=$(stat -f %p "$1" 2>/dev/null) && [ -n "$m" ] && [ $(( 0$m & 01000 )) != 0 ] || return 1
  no_allow_acl "$1"
}

# 账号的 uid 大于 0、在目录服务里只对应这一个账号（L3 第 3 轮 P1-01）：uid 被改成 0，「以账号身份」就又成了 root；
# 两个账号共用 uid，pf 和文件权限都分不开它们。目录服务查不出来（出错、空）一律不过。
uid_ok() {
  local uid list
  uid=$(id -u "$1" 2>/dev/null) || return 1
  case $uid in ''|*[!0-9]*) return 1 ;; esac
  [ "$uid" -gt 0 ] || return 1
  list=$(user_list) || return 1
  [ "$(printf '%s\n' "$list" | awk -v id="$uid" '$2 == id {print $1}')" = "$1" ]
}

# 目录服务里的全部账号，一行「名字 uid」。出错或查出空返回 1：是「查不清」，不能当成「没有这个账号」
# （审查第 4 轮 P1-1：开机头一分钟目录服务常没就绪，id 查不到不等于账号不存在）。
user_list() {
  local list
  list=$(dscl . -list /Users UniqueID 2>/dev/null) || return 1
  [ -n "$list" ] || return 1
  printf '%s\n' "$list"
}

# 是不是某个组的成员：0 是，1 不是，2 查不清（组不存在、目录服务出错）。
# dseditgroup 的退出码分不出「不是成员」和「出错」，按它打印的第一个词认（L3 第 3 轮 P2-02）。
is_member() {
  local out
  out=$(dseditgroup -o checkmember -m "$1" "$2" 2>&1)
  case $out in
    "yes "*) return 0 ;;
    "no "*) return 1 ;;
    *) return 2 ;;
  esac
}
