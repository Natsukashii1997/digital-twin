#!/bin/bash
# backend 所在默认网络（app-net）的出站自测（#172 返修）。在宿主上跑，要求 backend 在跑、出口代理镜像已构建。
# 借出口代理镜像进入 backend 的网络命名空间（--network container:），只做 TCP 连接，不发应用层请求：
#   应拒：宿主网关 192.168.5.2、虚拟机、宿主所有活动网卡地址上的常见端口、外网 IP
#   应通：postgres:5432、frontend:3000（app-net 内部）
# 最后一行「不符合 0」才算通过。
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
runtime_dir="${MULTICA_DIR:-$HOME/Documents/Codex/Multica-local}"
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_CONFIG="$runtime_dir/docker"
export DOCKER_HOST="unix://$HOME/.colima/multica/docker.sock"
image="$(sed -n 's/^    image: \(dt-egress-proxy:[^ ]*\)$/\1/p' "$here/runtime.compose.yml")"
deny="192.168.5.2:8086 192.168.5.2:43101 192.168.5.2:22 192.168.5.1:22 172.29.0.1:22 1.1.1.1:443 8.8.8.8:53 140.82.112.3:443 20.205.243.166:443 104.18.32.47:443 160.79.104.10:443"
for ip in $(ifconfig | awk '/inet / && $2 != "127.0.0.1" {print $2}'); do
  for p in 22 53 5000 7000 8086 5310 43101; do deny="$deny $ip:$p"; done
done
allow="postgres:5432 frontend:3000"
docker run --rm --network container:multica-backend-1 --entrypoint bash "$image" -c '
t() { if timeout 4 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; then echo 通; else echo 拒; fi; }
bad=0; n=0
for hp in '"$deny"'; do r=$(t "${hp%:*}" "${hp##*:}"); n=$((n+1)); v=符合; [ "$r" = 拒 ] || { v="!!! 不符合"; bad=$((bad+1)); }; echo "应拒 | $hp | 实际$r | $v"; done
for hp in '"$allow"'; do r=$(t "${hp%:*}" "${hp##*:}"); n=$((n+1)); v=符合; [ "$r" = 通 ] || { v="!!! 不符合"; bad=$((bad+1)); }; echo "应通 | $hp | 实际$r | $v"; done
echo "== backend 出站：合计 $n 项，不符合 $bad"; [ $bad = 0 ]'
