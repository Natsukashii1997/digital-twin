#!/bin/bash
# 不登录时读凭据的实测探针（ADR-015 批 0E-1）：由系统服务以 dt-gate / dt-secretary 身份运行，
# 那个账号没人登录（没有图形会话），看这时能不能读到自己目录里的凭据文件、登录钥匙串能不能用、
# 上网走得通哪条路。结果写成 key=value，给主账号看；不含任何凭据内容。
#
# 由 ops/dt_accounts_setup.sh 安装成 LaunchDaemon（开机和安装时各跑一次），不用手动运行。
# 测试时可以用环境变量改路径：DT_PROBE_OUT、DT_PROBE_SECRET、DT_PROBE_KEYCHAIN。
set -uo pipefail

me=$(id -un)
home=$(dscl . -read "/Users/$me" NFSHomeDirectory 2>/dev/null | awk '{print $2}')
out=${DT_PROBE_OUT:-/Users/Shared/dt-accounts/probe/$me/result.txt}
secret=${DT_PROBE_SECRET:-$home/.dt/probe.secret}
keychain=${DT_PROBE_KEYCHAIN:-$home/Library/Keychains/login.keychain-db}

clean() { tr '\n\r\t' '   ' | cut -c1-200; }
kv() { printf '%s=%s\n' "$1" "$(printf '%s' "$2" | clean)"; }

# 一次网络探测：返回 HTTP 码，000 是没连上
probe_url() {
  local via=$1 url=$2
  if [ "$via" = direct ]; then
    curl -sS -o /dev/null -m 8 -w '%{http_code}' --noproxy '*' "$url" 2>/dev/null || true
  else
    curl -sS -o /dev/null -m 8 -w '%{http_code}' -x "http://127.0.0.1:$via" "$url" 2>/dev/null || true
  fi
}

{
  kv at "$(date '+%Y-%m-%dT%H:%M:%S%z')"
  kv user "$me"
  kv uid "$(id -u)"
  kv env_home "${HOME:-}"
  kv real_home "$home"
  kv boot "$(sysctl -n kern.boottime 2>/dev/null | sed 's/^{ sec = \([0-9]*\),.*/\1/')"
  kv console_user "$(stat -f %Su /dev/console 2>/dev/null)"
  kv self_logged_in "$(who | awk -v u="$me" '$1 == u {print "yes"; exit}' | grep . || echo no)"
  kv home_mode "$(stat -f %Lp "$home" 2>/dev/null)"

  # 1. 自己目录里的凭据文件（安装时放的测试值，不是真凭据）
  if [ -r "$secret" ] && [ -n "$(head -c 64 "$secret")" ]; then kv secret_read yes; else kv secret_read no; fi
  kv secret_mode "$(stat -f %Lp "$secret" 2>/dev/null)"

  # 2. 登录钥匙串：没登录过的账号多半没有、或是锁着的
  if [ -e "$keychain" ]; then kv keychain_exists yes; else kv keychain_exists no; fi
  kv keychain_list "$(security list-keychains -d user 2>&1)"
  info=$(security show-keychain-info "$keychain" 2>&1); kv keychain_info_rc "$?"; kv keychain_info "$info"
  find=$(security find-generic-password -s cn.digital-twin.probe "$keychain" 2>&1); kv keychain_find_rc "$?"; kv keychain_find "$find"

  # 3. 代理与上网：系统服务读不到终端里的代理设置（G-19）
  kv env_https_proxy "${HTTPS_PROXY:-${https_proxy:-}}"
  kv sys_proxy "$(scutil --proxy 2>/dev/null | awk '/HTTPSEnable|HTTPSProxy|HTTPSPort/ {printf "%s=%s ", $1, $3}')"
  kv net_feishu_direct "$(probe_url direct https://open.feishu.cn)"
  kv net_github_direct "$(probe_url direct https://api.github.com)"
  kv net_github_via_8090 "$(probe_url 8090 https://api.github.com)"
  kv net_github_via_8086 "$(probe_url 8086 https://api.github.com)"
} > "$out.tmp" 2>/dev/null

chmod 644 "$out.tmp"
mv -f "$out.tmp" "$out"
