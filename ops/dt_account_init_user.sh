#!/bin/bash
# 账号自己目录里的初始化（ADR-015 批 0E-1）：由 ops/dt_accounts_setup.sh 以 dt-gate / dt-secretary 自己的身份运行
# （sudo -u <账号> bash -s < 本文件），不是 root。所以账号就算在这时偷换链接、硬链接、目录，
# 这里能改到的也只有它本来就能改的东西（L3 P1-01）。
#
# 参数：$1 家目录，$2 .zprofile 里的标记行。可重复跑。
set -euo pipefail

home=$1
mark=$2
# 兜底：万一账号的 uid 被改成 0，这里就成了 root 在改账号目录（L3 第 3 轮 P1-01），不做
[ "$(id -u)" != 0 ] || { echo "  [停下] 以 root 身份运行，不做"; exit 1; }
umask 077
cd "$home"

# 登录 shell 的 umask
grep -qF "$mark" .zprofile 2>/dev/null || printf '%s\numask 077\n' "$mark" >> .zprofile

# 测试凭据（随机数，不是真凭据），给探针看「没人登录时读不读得到」；0E-2 起真凭据也这样存（常浩 11-19 定）
mkdir -p .dt
chmod 700 .dt
[ -s .dt/probe.secret ] || openssl rand -hex 16 > .dt/probe.secret
chmod 600 .dt/probe.secret
echo "  以 $(id -un) 身份：umask 077、测试凭据 ~/.dt/probe.secret 就绪"
# 最后一行交给 root 记下，探针读到的要和它一致
printf 'secret_sha256=%s\n' "$(shasum -a 256 .dt/probe.secret | cut -d' ' -f1)"
