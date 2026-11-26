#!/bin/bash
# 数字分身 0A（digital-twin#190）：在「工人账号」dt-worker 里运行一次。
# 用法：登录 dt-worker 后打开「终端」，粘贴：  bash /Users/Shared/dt-autoloop/setup-dt-worker.sh
# 做的事：装 Node 24 和 Claude 命令行、登录 Claude / Codex（浏览器里点确认）、
#         给后台用的 Claude 长期令牌（没登录 dt-worker 时钥匙串打不开，接单程序改成系统服务后靠它，常浩 11-24 选甲）、
#         把这个账号接到主账号的中台上（只拿接单密钥，不拿 GitHub 写凭据）、建派单工作目录和交接目录（0C）。
# 令牌过期（一年）或要换：删掉 ~/.dt/claude-oauth-token 再跑一次。
# 可以重复跑：已经做过的步骤会跳过。
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
[ -f ~/.codex/auth.json ] || echo "[注意] Codex 的登录不在 ~/.codex/auth.json：没登录 dt-worker 时可能读不到，回到 Claude 会话说一声"

# 4b. 给后台用的 Claude 长期令牌：接单程序是开机自起的系统服务，dt-worker 没登录时登录钥匙串是锁着的，
#     claude 读不到浏览器登录的凭据；setup-token 出的令牌存成只有本账号能读的文件，claude-proxy.sh 读它
TOKEN_DIR="$HOME/.dt"
TOKEN_FILE="$TOKEN_DIR/claude-oauth-token"
# 先核目录和文件，再写任何东西（#382 L3 第 2 轮 P2-07）：~/.dt 要是本账号的真目录、不是链接，
# 令牌文件和临时文件都不能是链接，令牌文件有的话要是普通文件
[ -L "$TOKEN_DIR" ] && stop "$TOKEN_DIR 是链接，不对；删掉后重跑这个脚本。"
if [ -e "$TOKEN_DIR" ]; then
  [ -d "$TOKEN_DIR" ] || stop "$TOKEN_DIR 不是目录；删掉后重跑这个脚本。"
else
  mkdir -m 700 "$TOKEN_DIR"
fi
{ [ -d "$TOKEN_DIR" ] && [ ! -L "$TOKEN_DIR" ] && [ -O "$TOKEN_DIR" ]; } || stop "$TOKEN_DIR 不是本账号的目录；删掉后重跑这个脚本。"
chmod 700 "$TOKEN_DIR"
{ [ -L "$TOKEN_FILE" ] || [ -L "$TOKEN_FILE.tmp" ]; } && stop "$TOKEN_FILE（或 .tmp）是链接，不对；删掉后重跑这个脚本。"
{ [ ! -e "$TOKEN_FILE" ] || [ -f "$TOKEN_FILE" ]; } || stop "$TOKEN_FILE 不是普通文件；删掉后重跑这个脚本。"
if [ -s "$TOKEN_FILE" ]; then
  echo "Claude 长期令牌已经有了，跳过（过期或要换就删掉 $TOKEN_FILE 再跑）。"
else
  say "给后台用的 Claude 长期令牌：浏览器里点确认，终端最后会显示一串 sk-ant-oat 开头的令牌"
  "$CLAUDE" setup-token
  printf '\n把上面那串令牌粘贴到这里，回车（屏幕上不显示）：'
  IFS= read -rs tok
  echo
  tok=$(printf '%s' "$tok" | tr -d '[:space:]')
  case $tok in
    sk-ant-oat*) ;;
    *) unset tok; stop "这不像 Claude 的长期令牌（应以 sk-ant-oat 开头），没保存。重跑这个脚本再试。" ;;
  esac
  rm -f "$TOKEN_FILE.tmp"
  # set -C：临时文件已经在（包括别人刚放的链接）就不写
  ( umask 077; set -C; printf '%s\n' "$tok" > "$TOKEN_FILE.tmp" ) || { unset tok; stop "写不进 $TOKEN_FILE.tmp，没保存。"; }
  unset tok
  chmod 600 "$TOKEN_FILE.tmp"
  mv -f "$TOKEN_FILE.tmp" "$TOKEN_FILE"
  echo "已存到 $TOKEN_FILE（只有本账号能读）"
fi
# 每次都重核（已有的文件也核，第 1 轮 P2-04）：令牌是普通文件、不是链接，文件 600
{ [ -f "$TOKEN_FILE" ] && [ ! -L "$TOKEN_FILE" ]; } || stop "$TOKEN_FILE 不是普通文件；删掉后重跑这个脚本。"
chmod 600 "$TOKEN_FILE"

# 5. 给中台用的启动脚本：先补代理再启动（后台读不到终端设置）
mkdir -p -m 700 ~/.agent-autoloop/bin
for pair in "claude:$CLAUDE" "codex:$CODEX"; do
  name=${pair%%:*}; real=${pair#*:}
  token_line=""
  # claude：有长期令牌就用它（系统服务里没有图形会话、钥匙串锁着）；没有就照旧读钥匙串
  [ "$name" = claude ] && token_line='[ -r "$HOME/.dt/claude-oauth-token" ] && { CLAUDE_CODE_OAUTH_TOKEN=$(cat "$HOME/.dt/claude-oauth-token"); export CLAUDE_CODE_OAUTH_TOKEN; }'
  cat > ~/.agent-autoloop/bin/$name-proxy.sh <<EOF
#!/bin/sh
export HTTPS_PROXY=$PROXY HTTP_PROXY=$PROXY NO_PROXY=localhost,127.0.0.1,::1,.local
export https_proxy=$PROXY http_proxy=$PROXY no_proxy=localhost,127.0.0.1,::1,.local
export PATH="$(dirname "$real"):\$PATH"
$token_line
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

# 6b. 派单工作目录和交接目录（数字分身 0C，#205）：启动脚本在 ~/dt-work/<仓库> 里干活，交接包放 ~/dt-outbox，
#     主账号的受信发布只读交接目录（家目录本来就允许 staff 组进入，这里只把交接目录设成可读）
say "建派单工作目录和交接目录"
mkdir -p ~/dt-work
chmod 700 ~/dt-work
for repo in dt-sandbox; do
  mkdir -p ~/dt-work/"$repo"
done
mkdir -p ~/dt-outbox
chmod 755 ~/dt-outbox

# 7. 收尾核对
say "核对"
~/.agent-autoloop/app/bin/agent-autoloop.ts --version || true
~/.agent-autoloop/bin/claude-proxy.sh auth status 2>/dev/null | grep -E '"loggedIn"' || true
[ -s ~/.dt/claude-oauth-token ] && echo "Claude 长期令牌：已存（系统服务没登录时用它）"
"$CODEX" login status 2>&1 | head -1 || true
[ -e "$S/handoff/worker.key" ] && echo "交接密钥文件还在（没删掉）" || echo "交接密钥文件已删除"
[ -d ~/dt-work/dt-sandbox ] && [ -d ~/dt-outbox ] && echo "派单工作目录 ~/dt-work、交接目录 ~/dt-outbox：已建"
grep -q '127.0.0.1:8090' ~/.zprofile ~/.agent-autoloop/bin/claude-proxy.sh && echo "上网出口：过滤代理 8090"
printf '\n全部做完了。点苹果菜单 → 注销 dt-worker，回到主账号装接单程序的系统服务（ops/dt_worker_service.py install）。\n'
