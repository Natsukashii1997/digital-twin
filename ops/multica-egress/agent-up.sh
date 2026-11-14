#!/bin/bash
# 拉起 Multica 隔离执行器的唯一入口（#172 返修，fail-closed）。从仓库主目录运行：
#   ops/multica-egress/agent-up.sh          核对通过才拉起出口代理和执行器
#   ops/multica-egress/agent-up.sh down     停掉执行器和出口代理（其余服务不动）
# 顺序：安装副本一致 + 虚拟机防火墙 → 拉起出口代理 → 核对代理 → 拉起执行器 → 核对执行器和 backend。
# 拉起后任何一步不过，就把执行器和代理都停掉，退出码 1。执行器和代理是 restart: "no" + profile agent，
# 虚拟机重启、control.sh start 都不会自动拉起它们，每次都要经这里。
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
runtime_dir="${MULTICA_DIR:-$HOME/Documents/Codex/Multica-local}"
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_CONFIG="$runtime_dir/docker"
export DOCKER_HOST="unix://$HOME/.colima/multica/docker.sock"
compose=(docker compose --project-directory "$runtime_dir" -f "$runtime_dir/compose.yml" -f "$runtime_dir/local.override.yml" -f "$runtime_dir/runtime.compose.yml" --profile agent)

stop_agent() {
  "${compose[@]}" stop isolated-agent egress-proxy >/dev/null 2>&1
  "${compose[@]}" rm -f isolated-agent egress-proxy >/dev/null 2>&1
}

if [[ "${1:-up}" == down ]]; then
  stop_agent
  echo "执行器和出口代理已停止。"
  exit 0
fi

if ! python3 "$here/verify.py" drift || ! python3 "$here/verify.py" vm; then
  echo "防火墙或安装副本核对没通过，不拉起执行器。" >&2
  exit 1
fi
"${compose[@]}" up -d --pull never egress-proxy || { stop_agent; echo "出口代理起不来，已停。" >&2; exit 1; }
if ! python3 "$here/verify.py" proxy; then
  stop_agent; echo "出口代理核对没通过，已停。" >&2; exit 1
fi
"${compose[@]}" up -d --pull never isolated-agent || { stop_agent; echo "执行器起不来，已停。" >&2; exit 1; }
if ! python3 "$here/verify.py" agent; then
  stop_agent; echo "执行器核对没通过，已停。" >&2; exit 1
fi
echo "核对全部通过，执行器已拉起。"
