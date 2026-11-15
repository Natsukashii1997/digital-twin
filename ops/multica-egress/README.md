# Multica 隔离执行器出网（#172，从 #135 拆出）

托管「受限会话」要求容器里的 agent 碰不到宿主上的服务。#135 实测发现两处缺口：经宿主代理 8086 能访问宿主回环服务；经宿主局域网 IP 能连端口。另外，Colima 虚拟机管理面可达，虚拟机 dnsmasq 的 53 被转发到宿主所有网卡。本目录是修复后的配置**源文件**和核对、自测工具，安装副本在仓库外。

| 源文件 | 安装位置 | 作用 |
|---|---|---|
| `runtime.compose.yml` | `~/Documents/Codex/Multica-local/runtime.compose.yml` | 执行器只接内部网络 `agent-net`（172.30.0.0/24，`internal`），只能到 backend（固定 .2）和出口代理（固定 .3）；出口代理另接 `egress-net`（172.31.0.0/24）；compose 默认网络固定为 172.29.0.0/24；三个网络都关 IPv6。执行器和代理 `restart: "no"`、放在 profile `agent` 里，不会自动启动 |
| `egress/`（Dockerfile、`squid.conf`、`allowed_domains.txt`） | `~/Documents/Codex/Multica-local/egress/` | 白名单出口代理：只接受执行器网段（agent-net 的 .128/25）来的、`CONNECT` 到白名单域名 443 的请求；拒绝 IP 形式的目标和普通 HTTP；域名自己解析后落在内网 / 回环 / 保留地址或解析不出就拒绝；上游只经宿主 8086 |
| `colima-provision.sh` | `~/.colima/multica/colima.yaml` 的 `provision` 第一项 `script`（每次虚拟机启动都跑） | 虚拟机 iptables：`DOCKER-USER`、`DT-EGRESS` 一次事务重建；agent-net 只放行执行器网段（.128/25）到 backend:8080、代理:3128，backend 用不了代理；egress-net 只放行到宿主 8086；默认网络（backend、frontend、postgres）只许网段内互通，宿主和外网一律拒绝；`INPUT` 拒绝容器网段访问虚拟机；停掉并屏蔽 dnsmasq |
| `agent-up.sh` | 不安装，从仓库主目录运行 | **拉起执行器的标准入口**：先核对，通过才拉起；任何一步核对不过都停掉并回读（fail-closed）。不是强制访问控制，见「信任边界」 |
| `verify.py` | 不安装，从仓库主目录运行 | 只读核对：安装副本与源文件一致（drift）、虚拟机防火墙和 53 监听（vm）、代理容器（proxy）、执行器与 backend（agent） |
| `selftest.py`、`dst-selftest.sh`、`app-selftest.sh` | 不安装 | 执行器里的连通性反向 / 正向自测；squid 解析后地址拒绝的模拟测试；backend 网络空间的出站自测 |

网段在 `runtime.compose.yml`、`colima-provision.sh`、`squid.conf`、`verify.py` 四处一一对应，改一处要同步其余几处。

## 拉起与停止执行器

```bash
ops/multica-egress/agent-up.sh        # 核对通过才拉起出口代理和执行器
ops/multica-egress/agent-up.sh down   # 只停执行器和出口代理，并回读确认
```

- Multica-local 的 `control.sh start` 只起 postgres、backend、frontend，不起执行器；虚拟机重启后执行器也不会自己起来。夜跑前要先跑 `agent-up.sh`。
- `agent-up.sh` 的顺序：`verify.py drift` + `verify.py vm` → 起代理 → `verify.py proxy` → 起执行器 → `verify.py agent`。任何一步不过（包括第一步：执行器之前已在跑、后来防火墙被改坏），都停掉执行器和代理并回读；停下来了退出码 1，没能确认停下退出码 2（要人工处置）。`down` 同样回读，Docker 连不上也算没确认。
- 平时想确认防线还在：`python3 ops/multica-egress/verify.py all`（执行器要在跑）。

### 信任边界

`agent-up.sh` 是**标准入口，不是强制访问控制**：能直接操作宿主 Docker 的人或进程（`docker start`、`docker compose up isolated-agent` 这样点名服务、设 `COMPOSE_PROFILES`）都能绕开它。这一层防的是**容器里的 agent**，宿主上的 Docker 调用者属于可信边界（常浩本人和本机受信任的会话）。为此派单入口不依赖有没有走 `agent-up.sh`：夜跑主控 `hermes/scripts/multica_night.py` 在开跑前和每次放 stage 前都跑 `verify.py all`，不过就不开跑 / 不放 stage，并按 5.3a 停机、冻结执行器。夜跑之外在 Multica 网页里手动给执行器派单，是常浩自己的操作，不在这层保护之内。

## 安装 / 更新

1. 确认没有冻结中的夜跑（`DigitalTwin/runs/*/state.json` 全部 `STOPPED` / `FINISHED`），记下 `multica issue list --output json` 的状态。
2. 先备份要被覆盖的文件，放到 `DigitalTwin/evidence/<目录>/`：`colima.yaml`，以及 Multica-local 的 `runtime.compose.yml` 和 `egress/`。
3. 拷文件：`runtime.compose.yml`、`egress/*` 拷到 Multica-local；`colima-provision.sh` 的内容每行缩进 6 格，替换 `colima.yaml` 里 `provision` 第一项的 `script`（`verify.py drift` 会逐字比对）。
4. 先停执行器和代理（`agent-up.sh down`），再用 `control.sh` 里的同一组 compose 文件加 `--profile agent`：`build egress-proxy`，然后 `down` 再 `up -d --pull never`（只起非 agent 服务；网络配置变了，必须先 `down`）。
5. `colima stop -p multica && colima start -p multica`：规则只在虚拟机启动时装，重启也顺带清掉旧连接的状态（conntrack），改规则后**一律重启虚拟机**，不在运行中改。
6. `agent-up.sh` 拉起执行器（它会跑 `verify.py`）。然后跑自测：`selftest.py`（命令见文件开头）、`app-selftest.sh` 要求「不符合 0」；`dst-selftest.sh` 要求「符合」。前后 Multica 状态一致。

## 回滚（安全回退）

目标是**停用执行器**，不是回到有缺口的旧配置；全过程不启动任何不满足隔离条件的执行器。

1. `agent-up.sh down`，退出码 0 才算停下（回读过）；有在跑的夜跑先按 ARCHITECTURE 5.3a 停机。到这里就已经安全：执行器不在跑，也不会自动起来。
2. 只有在需要恢复旧配置排查问题时，才放回备份的 `colima.yaml`、`runtime.compose.yml`、删掉 Multica-local 的 `egress/`。旧配置里执行器是 `restart: unless-stopped`、没有 profile，所以：
   - 放回之前，第 1 步已经把执行器和代理容器删掉了（`agent-up.sh down` 会 `rm -f`），旧配置的「自动重启」没有容器可拉；
   - 放回之后先 `docker compose …（同一组文件） down`（执行器容器已删，这一步只停其余服务、按旧配置重建网络），再**只点名启动其余服务**：`docker compose …（同一组文件） up -d --pull never postgres backend frontend`。不要用不带服务名的 `up`，否则会按旧配置创建并启动执行器；
   - 核对：`docker ps -a --filter name=multica-isolated-agent-1` 为空（容器不存在，重启虚拟机也不会出现）。
   此时环境不满足托管的安全条件，不能开夜跑、不能托管。
3. 重新启用：按「安装 / 更新」重装本目录的配置，`agent-up.sh` 和三份自测全部通过后才算恢复。

## 白名单

`egress/allowed_domains.txt` 每行一个完整域名，不用通配。**加域名要常浩同意。** 被拒的请求在 `docker logs multica-egress-proxy-1` 里，带 `TCP_DENIED`；夜跑因缺域名失败时，先看这里。包仓库（npm、PyPI）暂不在白名单里。

## 已知限制

- **宿主 8086 会再解析一次。** 真正连目标的是宿主上的加速器 `fc-core`（闭源、只监听 127.0.0.1:8086，规则改不了）。squid 拿白名单域名自己解析一次、拦掉内网结果，但 8086 连之前会再解析，两次结果不同时这里拦不住；#135 也证明 8086 本身会转发到宿主回环。要利用这一点，前提是能让 github.com、api.anthropic.com、chatgpt.com 等白名单域名在 8086 那一侧解析到内网地址（篡改这些域名的 DNS 或 8086 的解析），超出本项目能控制的范围。常浩 2026-11-14 选择接受这个剩余风险（「按推荐返修」）；要彻底消除，只能换掉 8086、另建可控出口。
- backend 同时接默认网络和 agent-net。执行器能调用 backend 的 API（它就是任务服务器）。backend 的 VCS 连接接口会按调用方给的 URL 发请求，agent 令牌能触发，所以：本机关掉 VCS 集成（`runtime.compose.yml`），默认网络断宿主和外网（`colima-provision.sh`），backend 对外只听 8080（`verify.py` 核对）。代价是 Multica 网页里要连外网的功能（GitHub 导入 skill、VCS / GitHub 集成、遥测）不可用，常浩 2026-11-14 同意。盘点见 `docs/DT-ORC/migration/2026-11-12-出网修复与复验.md` 第七节。
- 构建容器（docker build 用的默认网桥）的出网和改之前一样：只能连宿主 8086。
- 运行前身份闸（#135 文档第五节 3）在 `hermes/scripts/multica_night.py`（#174），不在本目录。
