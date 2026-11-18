#!/bin/bash
# 数字分身 0A（digital-twin#190）：在「工人账号」dt-worker 里运行一次。
# 用法：登录 dt-worker 后打开「终端」，粘贴：  bash /Users/Shared/dt-autoloop/setup-dt-worker.sh
# 做的事：装 Node 24 和 Claude 命令行、登录 Claude / Codex（浏览器里点确认）、
#         把这个账号接到主账号的中台上（只拿接单密钥，不拿 GitHub 写凭据）。
set -euo pipefail

S=/Users/Shared/dt-autoloop
HUB=http://127.0.0.1:3350
# 工人只能经过滤代理 8090 上网（ops/dt_worker_egress.py）；门卫规则不许它直连加速器的 8086
PROXY=http://127.0.0.1:8090
say() { printf '\n==> %s\n' "$*"; }
stop() { printf '\n[停下] %s\n' "$*"; exit 1; }

# 0. 只能在工人账号里跑
me=$(id -un)
[ "$me" = "nat" ] && stop "现在是你的主账号。请先切换到新建的工人账号，再运行这一步。"
id -Gn | tr ' ' '\n' | grep -qx admin && stop "这个账号是「管理员」。请在系统设置里把它改成「标准」账号后再运行。"
JOINED=no
[ -f ~/.agent-autoloop/config.json ] && JOINED=yes
[ "$JOINED" = yes ] || [ -r "$S/handoff/worker.key" ] || stop "找不到交接密钥文件，请回到 Claude 会话说一声。"

# 1. 代理（这台电脑上网要走本机代理，后台服务读不到终端里的设置，见全局坑 G-19、G-27）
export HTTPS_PROXY=$PROXY HTTP_PROXY=$PROXY NO_PROXY=localhost,127.0.0.1,::1,.local
export https_proxy=$PROXY http_proxy=$PROXY no_proxy=$NO_PROXY
if grep -q 'dt-worker 代理' ~/.zprofile 2>/dev/null; then
  sed -i '' 's#127.0.0.1:8086#127.0.0.1:8090#g' ~/.zprofile
else
  cat >> ~/.zprofile <<EOF
# dt-worker 代理（数字分身 0A）
export HTTPS_PROXY=$PROXY HTTP_PROXY=$PROXY NO_PROXY=localhost,127.0.0.1,::1,.local
export https_proxy=$PROXY http_proxy=$PROXY no_proxy=localhost,127.0.0.1,::1,.local
EOF
fi

# 2. Node 24（装在这个账号自己的目录里，不碰系统）
say "安装 Node 24"
export NVM_DIR="$HOME/.nvm"
if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | PROFILE=/dev/null bash
fi
# nvm 不支持 set -u（全局坑 G-08），这一段临时关掉
set +u
# shellcheck disable=SC1091
. "$NVM_DIR/nvm.sh"
nvm install 24 >/dev/null
nvm alias default 24 >/dev/null
set -u
if ! grep -q 'NVM_DIR' ~/.zprofile 2>/dev/null; then
  printf '\nexport NVM_DIR="$HOME/.nvm"\n[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"\n' >> ~/.zprofile
fi
node -v

# 3. Claude 命令行（装在这个账号里）
say "安装 Claude 命令行"
command -v claude >/dev/null || npm install -g @anthropic-ai/claude-code >/dev/null
CLAUDE=$(command -v claude)
CODEX=/opt/homebrew/bin/codex
[ -x "$CODEX" ] || stop "找不到 Codex 命令行（$CODEX），请回到 Claude 会话说一声。"

# 4. 登录（会弹浏览器，点确认即可；用的是你同一个会员账号）
say "登录 Claude：浏览器里点确认"
claude auth status 2>/dev/null | grep -q '"loggedIn": *true' || claude auth login
say "登录 Codex：终端会显示一个网址和一串验证码，在任意浏览器打开网址、填入验证码"
"$CODEX" login status >/dev/null 2>&1 || "$CODEX" login --device-auth

# 5. 给中台用的启动脚本：先补代理再启动（后台读不到终端设置）
mkdir -p -m 700 ~/.agent-autoloop/bin
for pair in "claude:$CLAUDE" "codex:$CODEX"; do
  name=${pair%%:*}; real=${pair#*:}
  cat > ~/.agent-autoloop/bin/$name-proxy.sh <<EOF
#!/bin/sh
export HTTPS_PROXY=$PROXY HTTP_PROXY=$PROXY NO_PROXY=localhost,127.0.0.1,::1,.local
export https_proxy=$PROXY http_proxy=$PROXY no_proxy=localhost,127.0.0.1,::1,.local
export PATH="$(dirname "$real"):\$PATH"
exec "$real" "\$@"
EOF
  chmod 700 ~/.agent-autoloop/bin/$name-proxy.sh
done

# 6. 接到主账号的中台（只用接单密钥）
say "接到中台"
if [ "$JOINED" = yes ]; then
  echo "已经接过中台，跳过。"
else
# 引擎副本归主账号所有，git 默认不信任别人的仓库（dubious ownership），只给这一个副本放行
git config --global --get-all safe.directory 2>/dev/null | grep -qx "$S/agent-autoloop.git" || git config --global --add safe.directory "$S/agent-autoloop.git"
KEY=$(tr -d '[:space:]' < "$S/handoff/worker.key")
AGENT_AUTOLOOP_REPO="$S/agent-autoloop.git" sh "$S/install.sh" join "$HUB" --worker dt-worker --key "$KEY"
unset KEY
rm -f "$S/handoff/worker.key"
fi

# 7. 收尾核对
say "核对"
~/.agent-autoloop/app/bin/agent-autoloop.ts --version || true
claude auth status 2>/dev/null | grep -E '"loggedIn"' || true
"$CODEX" login status 2>&1 | head -1 || true
[ -e "$S/handoff/worker.key" ] && echo "交接密钥文件还在（没删掉）" || echo "交接密钥文件已删除"
grep -q '127.0.0.1:8090' ~/.zprofile ~/.agent-autoloop/bin/claude-proxy.sh && echo "上网出口：过滤代理 8090"
printf '\n全部做完了。回到 Claude 会话说「做好了」。\n'
