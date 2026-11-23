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
| 凭证库、出口账本和关闸标记、中台版本锁定（`hub-pin.json`）、飞书桥接登记、秘书开关与账本、托管账本 | 主账号 DigitalTwin/ 下（11-22 首单「先跑后搬」起凭证库、出口账本、`hub-pin.json` 在用） | 不搬（常浩 11-23 定，DECIDED 11-23 第 5 条：出口账本、hub-pin 都不带，dt-gate 从新账本开始、搬完重新锁版本；凭证库同样不搬）：dt-gate 里从空的新库开始，`hub-pin.json` 搬完按「以后要用到的」重新 `pin`。搬之前主账号这边必须收尾干净（preflight 硬核对，见「开始前」）；主账号的账本原样留在原处，不删不动 |
| 0D 恢复闸 | 主账号的（闸的状态在出口账本里） | 主账号控制面停写（恢复点 `gate-migrate-<时间>`），搬完保持停写、回滚时结束；dt-gate 的闸从新账本开始、关着，锁定中台版本并对账后才开（见「以后要用到的」） |
| 工人侧启动脚本 | `/Users/Shared/dt-autoloop/bin/dt-worktree-*`（首单装的 main 版，读 `/Users/Shared/dt-autoloop/` 的镜像） | 换成本 PR 之后的版本（读 `/Users/Shared/dt-gate/`），搬之前换（见「开始前」）；位置不变，中台 Agent 的 program 不用改 |

## 开始前

- PR #210（0E-1）、#313（门卫监听者规则）和本 PR 都已合并；门卫规则装的是 #313 之后的新版（`sudo bash ops/dt_worker_pf.sh install`，装成 `/Library/Application Support/DigitalTwin/dt_worker_pf.sh` 这份 root 副本），里面已经放行 dt-secretary 连 3352、3353。旧版门卫不认搬家写的标记，第 4b 步会停下、自动退回。
- 中台里没有在跑的轮次、没有没答完的卡片；两条派单规则是暂停的（首单收尾时重新暂停；preflight 会核）。
- **首单（以及之后在主账号布局里跑的单）全部收尾**：PR 已人工发布、合并，凭证已 `complete`（或撤销）；凭证库里没有还有效或冻结着的凭证、没有没送达的派单事件；出口账本、本人点击里没有结果不明、核不清、还在送、排着队的；托管、秘书账本按各自的排空证明（`drain_blockers`）收完：没有已出未送达、在做、结果不明、核不清的决定，没有决定已收口而额度还占着的，没有有原文账本里却没有的；没有关闸标记。preflight 的「主账号控制面账本里没有没结清的东西」一条一条列出来，有任何一样都不搬：dt-gate 里是新账本，搬过去就忘了。
- **换工人启动脚本**（拉完 main 之后，在主账号终端；换完以后它读 `/Users/Shared/dt-gate/`，搬好之前派单规则保持暂停）：

```bash
install -m 755 ops/dt_worktree.py /Users/Shared/dt-autoloop/bin/dt-worktree-claude && install -m 755 ops/dt_worktree.py /Users/Shared/dt-autoloop/bin/dt-worktree-codex
```

  preflight 会核装着的启动脚本和副本里的 `ops/dt_worktree.py` 逐字节一样。回滚到主账号中台后还要接着派单的，启动脚本读的目录要另行处理（回 Claude 会话说）。
- 在主账号终端，仓库主目录切到 main 并拉到最新：

```bash
cd ~/Documents/ChatGPT/个人/work/digital-twin && git checkout main && git pull
```

## 第 1 步：复制运行副本（stage）

引擎用主账号中台现在正在跑的那个提交（只搬家、不顺带升级引擎；11-20 引擎批 D 升级后是 `abc8d41efdc97efe4c25bac8376460424baec726`，跑之前用 `git -C ~/.agent-autoloop/app rev-parse HEAD` 再核一次，以它为准）；本仓库填刚合并的 main 的**完整 40 位提交号**（在 GitHub 的 PR 页面上看合并提交，和本机 `git rev-parse origin/main` 对一下；脚本不认分支名，因为本机的引用主账号能改）。这是**唯一一次**从仓库里的脚本用 sudo：仓库里那份主账号能改，之后一律用 stage 装好的、root 所有的入口。

```bash
sudo /usr/bin/python3 -B -E -s ops/dt_gate_hub.py stage --engine-repo ~/Documents/ChatGPT/个人/work/agent-autoloop --engine-commit abc8d41efdc97efe4c25bac8376460424baec726 --repo ~/Documents/ChatGPT/个人/work/digital-twin --repo-commit <合并提交的 40 位号> --node ~/.nvm/versions/node/v24.16.0/bin/node
```

看到「副本好了：引擎 abc8d41efdc9、本仓库 …、node v24.16.0」就对了。node 签名核不过、版本不够 24、取出来的文件和提交里的对不上，都会停下。

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

会依次打印 0～6 步（第 0 步是主账号控制面停写、等在途的写做完、再核一遍没结清的，最多 2 分钟；第 4b 步写门卫标记 `/Library/Application Support/DigitalTwin/hub-in-gate`，让门卫只放行 dt-gate 在听的 3350 / 3352 / 3353，要看到 `guard=yes`），最后一行「搬好了：中台以 dt-gate 身份运行」。中途任何一步出错会自动退回：dt-gate 这边停掉、改名留证，主账号中台放回去重新启用。中途被打断（关了终端、进程被杀）也不怕：每一步都记在 `gate/migrating.json`，跑一次「回滚」那条命令就照记录收拾好。

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

三个服务都应是「在跑」，中台健康检查报出 `abc8d41efdc9…`（即第 1 步填的引擎提交）。再跑一次门卫的越权测试（约 5 分钟），看「监听者规则」一段全部符合：

```bash
sudo bash ops/dt_worker_pf.sh verify
```

把两段输出发回 Claude 会话，由 Claude 对照验收句判断。

## 以后要用到的

- 主账号 `~/.hermes/scripts/autoloop_grants.py` 在 `hermes/install_hold.txt` 里钉在首单装的那一版（镜像和任务单还在 `/Users/Shared/dt-autoloop/`），本 PR 合并后工程自检不会催着重装它；搬完以后网关只在 dt-gate 的副本里跑，回 Claude 会话删掉 install_hold 里这一行。搬之前别单独重装它：会和装着的工人启动脚本读的目录对不上，派单停下。
- 以 dt-gate 身份跑控制面工具（只认白名单里的；中台命令行只放行看和停的子命令，签钥匙、备份、恢复不经这里，免得钥匙打到终端上）：

```bash
sudo "$GATE" run agent-autoloop status
```

- 派单规则提示词里的路径改成了 `/Users/Shared/dt-gate/…`：派单恢复之前，在 dt-gate 里重装一次中台配置（会先给备份、只改派单规则这一块）。你说「装」再跑：

```bash
sudo "$GATE" run autoloop_config install --confirm --target /Users/dt-gate/.agent-autoloop
```

- dt-gate 的 0D 恢复闸从新账本开始、关着：锁定中台版本（填第 1 步的引擎提交，你说了才跑）再对一次账，才会放行受保护的写：

```bash
sudo "$GATE" run autoloop_recovery pin --commit abc8d41efdc97efe4c25bac8376460424baec726 --confirm
sudo "$GATE" run autoloop_recovery reconcile
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

回滚要主账号登录着跑（主账号中台是登录项，没登录起不来）。顺序：① 停掉 dt-gate 的三个服务（哪个卸不掉——launchctl 一直没说「找不到」——就停下，它的 plist 留着；plist 是链接或目录也停下）；② 以 root 用 lsof 核中台、只读窗口、秘书窄窗口三个端口（窗口按 windows 装的时候记下的端口）上没有任何账号在听，有人在听或查不清就停下；③ 把主账号中台目录放回；④ **门卫还开着时**启用主账号中台，让它先占住中台端口，以 root 用 lsof 核端口上只有主账号在听，才关门卫监听者规则（先核门卫目录只有 root 能改，再删标记、看到 `guard=no`），关了再核一次监听者、做健康检查；⑤ 都过了才结束停写、归档记录。任何一步停下时停写和记录都留着，门卫没关的就开着（主账号中台在门卫后面谁都连不上，别的账号抢绑了端口也收不到连接），看清原因后再跑一次接着来；主账号中台目录已放回的，再跑一次从第 ④ 步接着做。搬到一半被打断的，也用这条收拾（同样先在门卫后面启用主账号中台、核过监听者再关门卫）。搬家之后 dt-gate 里新记下的东西不会带回去，回滚前会在 dt-gate 家里留一份备份。做过第 5 步的，dt-worker 要重新接入主账号中台。

## 剩余风险（写明，不在本批解决）

- **装的那一刻主账号是可信的**：stage 从主账号的仓库取代码、取 node。node 核了 Node.js 官方签名；仓库代码按你给的提交号取，主账号要是事先被改了仓库，装进去的就是改过的。所以第 1 步要在合并后的 main 上跑。
- **本机端口谁都能抢绑**：中台重启或崩溃后的几十秒里 3350 空着，主账号的进程可以抢先绑上冒充中台。dt-gate 自己的服务、`run agent-autoloop` 带钥匙访问前都会核对端口上在听的是 dt-gate（`hermes/scripts/hub_listener.py`，只认 127.0.0.1），搬家脚本等服务起来时也按监听进程核；但 **dt-worker 重连时带的接单钥匙**、核对和真正连接之间的极短空隙，靠软件挡不住，要门卫规则按监听账号放行（pf 的 `user` 条件：3350、3352、3353 只放行 dt-gate 在听的），在 0E-1 的 `dt_worker_pf.sh` 里加。**先后顺序**：门卫规则加上 → 第 5 步换工人钥匙 → 恢复派单规则；`rotate-worker` 会读门卫规则文件核这一条，没有就停下。
- **更新前有备份**：`update` 换引擎前先在 dt-gate 家里备份中台库（`~dt-gate/.agent-autoloop-update-<时间>`）；新的起不来自动退回上一份副本，库要退回时用这份备份（`restore` 不经 `run`，要的时候回 Claude 会话说）。
- **主账号里还留着旧东西**：`~/.agent-autoloop.moved-to-gate-<时间>`、`DigitalTwin/autoloop/hermes-source.key`、evidence 里的旧备份都含旧钥匙，搬完都已作废；回滚要用，先不删。
- **主账号的「试一下」规则和本机接单程序停用**：它们的钥匙作废了（符合 ADR 第 3.4 节：主账号不再接 AI 的活）。主账号的 `agent-autoloop` 命令行（链接到搬走的 app）不能再用，改用上面的 `run`；原位置放了说明文件，别的副本的命令行也不会悄悄新建一个空中台。
- **工人侧启动脚本还没装**：`/Users/Shared/dt-autoloop/bin/dt-worktree-*` 现在不存在；以后装 0C 启动脚本时用本 PR 之后的版本（默认读 `/Users/Shared/dt-gate/`）。旧的 `/Users/Shared/dt-autoloop/` 下没有 grants、tasks，不用清理。
- **名册副本只在重装时更新**：主账号改 `projects.json` 扩不了派单范围，这是有意的；要改就重跑第 1 步和 `update`。
- **主账号控制面搬完保持停写**：主账号不再做受保护的写（ADR-015 第 3.4 节）。回滚时用同一个恢复点结束（release rolled_back）；恢复闸核对不过（快照之后发过卡片）会保持停写，脚本打印手工命令，交 Claude 会话看。
- **过滤代理 8090 没人登录时不在**：0E-1 发现的；它转发到的加速器 8086 也是主账号登录后才有，单把 8090 改成开机服务不够，另议。
- **8090 也有「重启空档被抢绑」的问题**：过滤代理以主账号身份跑，它重启的空档里别的进程能抢绑 8090，冒充它接工人的出网请求；门卫的监听者规则只管 3350 / 3352 / 3353，管不到它。改成系统服务时一并处理（同样按监听账号放行）。
