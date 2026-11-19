#!/bin/bash
# 不登录时读凭据的实测探针（ADR-015 批 0E-1）：由系统服务以 dt-gate / dt-secretary 身份运行，
# 那个账号没人登录（没有图形会话），看这时能不能读到自己目录里的凭据文件、运行环境对不对、上网走得通哪条路。
# 结果写成 key=value，给主账号看；不含任何凭据内容（只写测试凭据的 SHA-256，由 root 和安装时记下的比对）。
#
# 凭据存法常浩 11-19 定：0E-2 起真凭据存成账号家目录里只有它能读的文件（磁盘由文件保险箱加密），不用钥匙串，
# 所以这里只测文件（L3 P1-04）。
#
# 由 ops/dt_accounts_setup.sh 安装成 LaunchDaemon（开机、安装时各跑一次，之后每 10 分钟一次），不用手动运行。
# 测试时可以用环境变量改路径：DT_PROBE_OUT、DT_PROBE_SECRET。
set -uo pipefail

me=$(id -un)
# 家目录以服务配置里给的 HOME 为准（安装脚本写成 /Users/<账号>）；目录服务只拿来对照。
# 11-20 冷启动实测：开机后一分钟目录服务还没就绪，dscl 读不出来，按它找凭据就找错了地方——所以最多等它 60 秒，等不到也不影响读凭据
dshome=""
for _ in $(seq 30); do
  dshome=$(dscl . -read "/Users/$me" NFSHomeDirectory 2>/dev/null | awk '$1 == "NFSHomeDirectory:" {print $2}')
  [ -n "$dshome" ] && break
  sleep 2
done
home=${HOME:-$dshome}
out=${DT_PROBE_OUT:-/Users/Shared/dt-accounts/probe/$me/result.txt}
secret=${DT_PROBE_SECRET:-$home/.dt/probe.secret}

clean() { tr '\n\r\t' '   ' | cut -c1-200; }
kv() { printf '%s=%s\n' "$1" "$(printf '%s' "$2" | clean)"; }

# 一次网络探测：「HTTP 码/curl 退出码」。000/7 连不上、000/28 超时、000/6 解析不了、000/35 TLS 失败、000/56 代理拒绝（L3 P2-03）
probe_url() {
  local via=$1 url=$2 code rc
  if [ "$via" = direct ]; then
    code=$(curl -sS -o /dev/null -m 8 -w '%{http_code}' --noproxy '*' "$url" 2>/dev/null); rc=$?
  else
    code=$(curl -sS -o /dev/null -m 8 -w '%{http_code}' -x "http://127.0.0.1:$via" "$url" 2>/dev/null); rc=$?
  fi
  printf '%s/%s' "${code:-000}" "$rc"
}

# 代理地址只留主机和端口，去掉可能带的用户名密码（结果文件所有人可读，L3 P2-01）
redact() { sed -E 's#://[^@/]*@#://***@#'; }

{
  kv nonce "${DT_PROBE_NONCE:-}"
  kv at "$(date '+%Y-%m-%dT%H:%M:%S%z')"
  kv user "$me"
  kv uid "$(id -u)"
  kv gid "$(id -g)"
  kv env_home "${HOME:-}"
  kv real_home "$dshome"
  if [ -n "$dshome" ] && [ "${HOME:-}" = "$dshome" ]; then kv home_match yes; else kv home_match no; fi
  kv uptime_s "$(( $(date +%s) - $(sysctl -n kern.boottime 2>/dev/null | sed 's/^{ sec = \([0-9]*\),.*/\1/') ))"
  kv env_path "${PATH:-}"
  kv boot "$(sysctl -n kern.boottime 2>/dev/null | sed 's/^{ sec = \([0-9]*\),.*/\1/')"
  kv console_user "$(stat -f %Su /dev/console 2>/dev/null)"
  kv self_logged_in "$(who | awk -v u="$me" '$1 == u {print "yes"; exit}' | grep . || echo no)"
  kv home_mode "$(stat -f %Lp "$home" 2>/dev/null)"

  # 自己目录里的测试凭据（安装时放的随机数，不是真凭据）：读得到、内容对（root 比对 SHA-256）
  if [ -r "$secret" ] && [ -s "$secret" ]; then
    kv secret_read yes
    kv secret_sha256 "$(shasum -a 256 "$secret" 2>/dev/null | cut -d' ' -f1)"
  else
    kv secret_read no
  fi
  kv secret_mode "$(stat -f %Lp "$secret" 2>/dev/null)"

  # 代理与上网：系统服务读不到终端里的代理设置（G-19）
  kv env_https_proxy "$(printf '%s' "${HTTPS_PROXY:-${https_proxy:-}}" | redact)"
  kv sys_proxy "$(scutil --proxy 2>/dev/null | awk '/HTTPSEnable|HTTPSProxy|HTTPSPort/ {printf "%s=%s ", $1, $3}')"
  kv net_feishu_direct "$(probe_url direct https://open.feishu.cn)"
  kv net_github_direct "$(probe_url direct https://api.github.com)"
  kv net_github_via_8090 "$(probe_url 8090 https://api.github.com)"
  kv net_github_via_8086 "$(probe_url 8086 https://api.github.com)"
  echo "done=1"
} > "$out.tmp" 2>/dev/null

chmod 644 "$out.tmp"
# 每次开机后的第一份结果另存一份（结果文件之后每 10 分钟会被新的覆盖），留作冷启动证据
boot_copy="$(dirname "$out")/boot-$(sysctl -n kern.boottime 2>/dev/null | sed 's/^{ sec = \([0-9]*\),.*/\1/').txt"
[ -e "$boot_copy" ] || cp "$out.tmp" "$boot_copy"
mv -f "$out.tmp" "$out"
