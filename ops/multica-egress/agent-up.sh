#!/bin/bash
# 拉起 Multica 隔离执行器的标准入口（#172 返修，fail-closed）。从仓库主目录运行：
#   ops/multica-egress/agent-up.sh          核对通过才拉起出口代理和执行器
#   ops/multica-egress/agent-up.sh down     停掉执行器和出口代理（其余服务不动），并回读确认
# 顺序：安装副本一致 + 虚拟机防火墙 → 拉起出口代理 → 核对代理 → 拉起执行器 → 核对执行器和 backend。
# 任何一步不过（包括第一步：执行器之前已在跑、后来防火墙被改坏），都停掉执行器和代理并回读；停不下来退出码 2。
# 执行器和代理是 restart: "no" + profile agent，虚拟机重启、control.sh start 都不会自动拉起它们。
# 信任边界：能直接操作宿主 Docker 的人（docker start、compose 点名服务）可以绕开本脚本，这一层不设防；
# 防的是容器里的 agent。夜跑入口 multica_night.py 在开跑和每次放 stage 前另跑 verify.py all，不过就不派、冻结。
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
runtime_dir="${MULTICA_DIR:-$HOME/Documents/Codex/Multica-local}"
export PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export DOCKER_CONFIG="$runtime_dir/docker"
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/multica/docker.sock}"
compose=(docker compose --project-directory "$runtime_dir" -f "$runtime_dir/compose.yml" -f "$runtime_dir/local.override.yml" -f "$runtime_dir/runtime.compose.yml" --profile agent)
names=(multica-isolated-agent-1 multica-egress-proxy-1)

# 停掉并回读：两个容器都不存在、或都不在跑，才算停了。docker 本身连不上也算没确认（返回 1）。
stop_agent() {
  "${compose[@]}" stop -t 10 isolated-agent egress-proxy >/dev/null 2>&1
  "${compose[@]}" rm -f isolated-agent egress-proxy >/dev/null 2>&1
  local n out
  for n in "${names[@]}"; do
    if ! out="$(docker ps -a --filter "name=^${n}\$" --format '{{.State}}' 2>&1)"; then
      echo "回读失败（docker 连不上）：$n 可能仍在运行：${out:0:160}" >&2
      return 1
    fi
    if [[ "$out" == running* || "$out" == restarting* ]]; then
      echo "$n 仍在运行（$out）" >&2
      return 1
    fi
  done
  return 0
}

fail() {  # 停掉并回读；停不下来退出码 2，停下来了退出码 1
  echo "$1" >&2
  if stop_agent; then
    echo "执行器和出口代理已停止（已回读）。" >&2
    exit 1
  fi
  echo "!!! 执行器或出口代理没能确认停下，可能仍在运行，需要人工处置：docker ps / colima stop -p multica" >&2
  exit 2
}

if [[ "${1:-up}" == down ]]; then
  if stop_agent; then
    echo "执行器和出口代理已停止（已回读）。"
    exit 0
  fi
  echo "!!! 执行器或出口代理没能确认停下，可能仍在运行，需要人工处置：docker ps / colima stop -p multica" >&2
  exit 2
fi

if ! python3 "$here/verify.py" drift || ! python3 "$here/verify.py" vm; then
  fail "防火墙或安装副本核对没通过，不拉起执行器；已在跑的也停掉。"
fi
"${compose[@]}" up -d --pull never egress-proxy || fail "出口代理起不来。"
python3 "$here/verify.py" proxy || fail "出口代理核对没通过。"
"${compose[@]}" up -d --pull never isolated-agent || fail "执行器起不来。"
python3 "$here/verify.py" agent || fail "执行器核对没通过。"
echo "核对全部通过，执行器已拉起。"
