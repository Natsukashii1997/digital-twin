# 0E-2 中台和网关搬进 dt-gate：常浩照做的命令清单

ADR-015 批 0E-2。脚本是 `ops/dt_gate_hub.py`（root 跑）和 `ops/dt_gate_user.py`（以各账号身份跑的小工具）。AI 不跑这些命令、不看任何钥匙和令牌；钥匙和令牌只在脚本进程里转交，屏幕上不会出现。

## 搬完是什么样

| | 搬之前 | 搬之后 |
|---|---|---|
| 中台（agent-autoloop） | 主账号 LaunchAgent `agent-autoloop`，登录后才起 | 系统服务 `cn.digital-twin.gate.hub`，以 dt-gate 身份运行，开机就起，地址不变（127.0.0.1:3350） |
| 中台数据和钥匙 | `~/.agent-autoloop`（主账号能读） | `/Users/dt-gate/.agent-autoloop`（只有 dt-gate 能读）；master.key、本人密钥都换新 |
| 运行的代码 | 主账号 nvm 里的 node、`~/.agent-autoloop/app` | `/Library/Application Support/DigitalTwin/gate/` 里 root 所有的副本（node 核过官方签名、引擎锁定提交、本仓库控制面脚本和名册） |
| 中台里的钥匙 | 本人、local / hermes 来源、desktop 通道、两个接单程序 | 只剩新的本人密钥和 dt-worker |
| 凭证镜像、任务单 | `/Users/Shared/dt-autoloop/{grants,tasks}`（归主账号） | `/Users/Shared/dt-gate/{grants,tasks}`（root 建，只有 dt-gate 能写，工人只读） |
| 只读窗口 3352 | 没装 | 系统服务 `cn.digital-twin.gate.readonly`（dt-gate），主账号拿令牌副本 |
| 秘书窄窗口 3353 | 没有 | 系统服务 `cn.digital-twin.gate.intake`（dt-gate），只收秘书提议、关秘书、撤销单三件事 |
| 主账号原来的中台 | — | 改名为 `~/.agent-autoloop.moved-to-gate-<时间>` 留着（里面的钥匙已作废），回滚用；原位置放一个说明文件 |
| 凭证库、飞书桥接登记、秘书开关与账本、托管账本 | 主账号 DigitalTwin/ 下（都还没装、没在用） | 不搬：dt-gate 里从空的新库开始（0E-3、0E-5 装的时候直接装在 dt-gate 里） |

## 开始前

- PR #210（0E-1）和本 PR 都已合并；门卫规则已装（`sudo bash /Users/Shared/dt-autoloop/dt_worker_pf.sh install` 那次），里面已经放行 dt-secretary 连 3352、3353。
- 中台里没有在跑的轮次、没有没答完的卡片；两条派单规则是暂停的（preflight 会核）。
- 在主账号终端，仓库主目录切到 main 并拉到最新：

```bash
cd ~/Documents/ChatGPT/个人/work/digital-twin && git checkout main && git pull
```

## 第 1 步：复制运行副本（stage）

引擎用现在正在跑的那个提交 `d3a44ca`（只搬家、不顺带升级引擎）；本仓库填刚合并的 main 的**完整 40 位提交号**（在 GitHub 的 PR 页面上看合并提交，和本机 `git rev-parse origin/main` 对一下；脚本不认分支名，因为本机的引用主账号能改）。这是**唯一一次**从仓库里的脚本用 sudo：仓库里那份主账号能改，之后一律用 stage 装好的、root 所有的入口。

```bash
sudo /usr/bin/python3 -B -E -s ops/dt_gate_hub.py stage --engine-repo ~/Documents/ChatGPT/个人/work/agent-autoloop --engine-commit d3a44cac4460f4e0417c3d407baa853de7aa25ff --repo ~/Documents/ChatGPT/个人/work/digital-twin --repo-commit <合并提交的 40 位号> --node ~/.nvm/versions/node/v24.16.0/bin/node
```

看到「副本好了：引擎 d3a44cac4460、本仓库 …、node v24.16.0」就对了。node 签名核不过、版本不够 24、取出来的文件和提交里的对不上，都会停下。

之后的命令都用这个入口（先在终端里设一次变量，后面的命令照抄）：

```bash
GATE="/Library/Application Support/DigitalTwin/gate/bin/dt-gate-hub"
```

## 第 2 步：核对（只看不改）

```bash
sudo "$GATE" preflight
```

每一行都要是「通过」。有「不过」就停下，把输出发回 Claude 会话。

## 第 3 步：搬家

```bash
sudo "$GATE" migrate --confirm
```

会依次打印 1～6 步，最后一行「搬好了：中台以 dt-gate 身份运行」。中途任何一步出错会自动退回：dt-gate 这边停掉、改名留证，主账号中台放回去重新启用。中途被打断（关了终端、进程被杀）也不怕：每一步都记在 `gate/migrating.json`，跑一次「回滚」那条命令就照记录收拾好。

最后一步「核对和搬家前一致」如果说对不上、并提到定时项，是停机那几十秒里有到期的定时任务在新中台补跑了；脚本已自动退回，再跑一次 migrate 即可。

## 第 4 步：装两个窗口

```bash
sudo "$GATE" windows --confirm
```

## 第 5 步：给 dt-worker 换接单钥匙（等门卫规则加上「3350 只收 dt-gate 在听的」之后）

旧的接单钥匙在 0A 交接时经过主账号，换掉。但中台重启的几十秒空档里，主账号的进程能抢绑 3350 冒充中台，工人一重连就把新钥匙交出去了；所以这一步要等 0E-1 的门卫规则（`dt_worker_pf.sh`）加上按监听账号放行 3350、3352、3353 之后再做，没加脚本会停下。恢复派单规则也排在它后面。换完 dt-worker 的接单程序要重启才接得上（它现在没在跑就不用管，下次起来就用新的）。

```bash
sudo "$GATE" rotate-worker --confirm
```

## 第 6 步：看一眼

```bash
sudo "$GATE" status
```

三个服务都应是「在跑」，中台健康检查报出 `d3a44cac4460…`。把输出发回 Claude 会话，由 Claude 对照验收句判断。

## 以后要用到的

- 以 dt-gate 身份跑控制面工具（只认白名单里的；中台命令行只放行看和停的子命令，签钥匙、备份、恢复不经这里，免得钥匙打到终端上）：

```bash
sudo "$GATE" run agent-autoloop status
```

- 派单规则提示词里的路径改成了 `/Users/Shared/dt-gate/…`：派单恢复之前，在 dt-gate 里重装一次中台配置（会先给备份、只改派单规则这一块）。你说「装」再跑：

```bash
sudo "$GATE" run autoloop_config install --confirm --target /Users/dt-gate/.agent-autoloop
```

- hermes 来源钥匙搬家时作废了，派单网关要用时在 dt-gate 里重签（ADR：搬家期间不签新钥匙；你说了才签）。
- 名册、控制面脚本、引擎有更新：用入口重跑第 1 步（`sudo "$GATE" stage …`，换 `--repo-commit` / `--engine-commit`），再跑下面这条；新副本起不来会自动退回上一份：

```bash
sudo "$GATE" update --confirm
```

## 回滚

```bash
sudo "$GATE" rollback --confirm
```

停掉 dt-gate 的三个服务，主账号中台原样放回、重新启用；搬到一半被打断的，也用这条收拾（主账号没登录时下次登录自动起）。搬家之后 dt-gate 里新记下的东西不会带回去，回滚前会在 dt-gate 家里留一份备份。做过第 5 步的，dt-worker 要重新接入主账号中台。

## 剩余风险（写明，不在本批解决）

- **装的那一刻主账号是可信的**：stage 从主账号的仓库取代码、取 node。node 核了 Node.js 官方签名；仓库代码按你给的提交号取，主账号要是事先被改了仓库，装进去的就是改过的。所以第 1 步要在合并后的 main 上跑。
- **本机端口谁都能抢绑**：中台重启或崩溃后的几十秒里 3350 空着，主账号的进程可以抢先绑上冒充中台。dt-gate 自己的服务、`run agent-autoloop` 带钥匙访问前都会核对端口上在听的是 dt-gate（`hermes/scripts/hub_listener.py`，只认 127.0.0.1），搬家脚本等服务起来时也按监听进程核；但 **dt-worker 重连时带的接单钥匙**、核对和真正连接之间的极短空隙，靠软件挡不住，要门卫规则按监听账号放行（pf 的 `user` 条件：3350、3352、3353 只放行 dt-gate 在听的），在 0E-1 的 `dt_worker_pf.sh` 里加。**先后顺序**：门卫规则加上 → 第 5 步换工人钥匙 → 恢复派单规则；`rotate-worker` 会读门卫规则文件核这一条，没有就停下。
- **更新前有备份**：`update` 换引擎前先在 dt-gate 家里备份中台库（`~dt-gate/.agent-autoloop-update-<时间>`）；新的起不来自动退回上一份副本，库要退回时用这份备份（`restore` 不经 `run`，要的时候回 Claude 会话说）。
- **主账号里还留着旧东西**：`~/.agent-autoloop.moved-to-gate-<时间>`、`DigitalTwin/autoloop/hermes-source.key`、evidence 里的旧备份都含旧钥匙，搬完都已作废；回滚要用，先不删。
- **主账号的「试一下」规则和本机接单程序停用**：它们的钥匙作废了（符合 ADR 第 3.4 节：主账号不再接 AI 的活）。主账号的 `agent-autoloop` 命令行（链接到搬走的 app）不能再用，改用上面的 `run`；原位置放了说明文件，别的副本的命令行也不会悄悄新建一个空中台。
- **工人侧启动脚本还没装**：`/Users/Shared/dt-autoloop/bin/dt-worktree-*` 现在不存在；以后装 0C 启动脚本时用本 PR 之后的版本（默认读 `/Users/Shared/dt-gate/`）。旧的 `/Users/Shared/dt-autoloop/` 下没有 grants、tasks，不用清理。
- **名册副本只在重装时更新**：主账号改 `projects.json` 扩不了派单范围，这是有意的；要改就重跑第 1 步和 `update`。
- **0D 恢复门还没合并**：合并以后，搬家前要先「停写 → 排空」（`autoloop_recovery.py hold`），这一步会补进 preflight / migrate。
- **过滤代理 8090 没人登录时不在**：0E-1 发现的；它转发到的加速器 8086 也是主账号登录后才有，单把 8090 改成开机服务不够，另议。
