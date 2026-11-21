#!/bin/bash
# squid 第二道（解析后地址拒绝）的模拟测试（#172 返修）。在宿主上跑，要求出口代理镜像已构建、egress-net 已存在。
# 正式白名单里的域名我们控制不了解析结果，所以另起一个临时 squid（同一镜像、同一份 squid.conf，只把来源换成本机、
# 白名单换成测试域名），在它自己里面发 CONNECT：
#   localtest.me        公网 DNS 解析到 127.0.0.1，模拟「白名单域名被解析到回环」  → 应 403
#   nonexistent.invalid 解析不出地址                                              → 应 403
#   github.com          正向对照                                                  → 应 200
# 临时容器不接 agent-net，用完即删；不改正式代理。
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
runtime_dir="${MULTICA_DIR:-$HOME/Documents/Codex/Multica-local}"
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_CONFIG="$runtime_dir/docker"
export DOCKER_HOST="unix://$HOME/.colima/multica/docker.sock"
image="$(sed -n 's/^    image: \(dt-egress-proxy:[^ ]*\)$/\1/p' "$here/runtime.compose.yml")"
conf="$(sed -e 's#^acl agent_net src .*#acl agent_net src 127.0.0.1/32#' -e 's#"/etc/squid/allowed_domains.txt"#"/tmp/allowed.txt"#' "$here/egress/squid.conf")"
probe='
probe() { exec 3<>/dev/tcp/127.0.0.1/3128 || { echo "$1 代理连不上"; return; }
  printf "CONNECT %s:443 HTTP/1.1\r\nHost: %s:443\r\n\r\n" "$1" "$1" >&3
  IFS= read -r -t 20 line <&3; exec 3<&- 3>&-; echo "$1 | ${line%$'"'"'\r'"'"'}"; }
printf "localtest.me\nnonexistent.invalid\ngithub.com\n" > /tmp/allowed.txt
cat > /tmp/squid.conf
squid -N -f /tmp/squid.conf & sleep 3
for h in localtest.me github.com nonexistent.invalid; do echo "解析 | $h -> $(getent ahosts $h | awk "{print \$1}" | sort -u | tr "\n" " ")"; done
probe localtest.me; probe nonexistent.invalid; probe github.com
kill %1 2>/dev/null; wait 2>/dev/null; true'
out="$(printf '%s\n' "$conf" | docker run --rm -i --network multica_egress-net --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --tmpfs /run/squid:uid=13,gid=13,mode=0750 --tmpfs /var/spool/squid:uid=13,gid=13,mode=0750 \
  --tmpfs /var/log/squid:uid=13,gid=13,mode=0750 --tmpfs /tmp:mode=1777 --entrypoint bash "$image" -c "$probe" 2>/dev/null)"
echo "$out" | grep ' | '
fail=0
echo "$out" | grep -q '^localtest.me | HTTP/1.1 403' || { echo "!!! localtest.me 没被拒"; fail=1; }
echo "$out" | grep -q '^nonexistent.invalid | HTTP/1.1 403' || { echo "!!! nonexistent.invalid 没被拒"; fail=1; }
echo "$out" | grep -q '^github.com | HTTP/1.1 200' || { echo "!!! github.com 正向不通"; fail=1; }
[[ $fail == 0 ]] && echo "== 解析后地址拒绝：符合" || echo "== 解析后地址拒绝：不符合"
exit $fail
