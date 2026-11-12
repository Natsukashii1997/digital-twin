# Multica 隔离执行器出网（#172，从 #135 拆出）

托管「受限会话」要求容器里的 agent 碰不到宿主上的服务。#135 实测发现两处缺口：经宿主代理 8086 能访问宿主回环服务；经宿主局域网 IP 能连端口。另外，Colima 虚拟机管理面可达，虚拟机 dnsmasq 的 53 被转发到宿主所有网卡。本目录是修复后的配置**源文件**，安装副本在仓库外。

| 源文件 | 安装位置 | 作用 |
|---|---|---|
| `runtime.compose.yml` | `~/Documents/Codex/Multica-local/runtime.compose.yml` | 执行器只接内部网络 `agent-net`（172.30.0.0/24，`internal`，没有出口），只能到 backend 和出口代理；出口代理另接 `egress-net`（172.31.0.0/24） |
| `egress/`（Dockerfile、`squid.conf`、`allowed_domains.txt`） | `~/Documents/Codex/Multica-local/egress/` | 白名单出口代理：只接受 `CONNECT` 到白名单域名的 443；拒绝 IP 形式的目标和普通 HTTP；上游只经宿主 8086，自己不直接出网 |
| `colima-provision.sh` | `~/.colima/multica/colima.yaml` 的 `provision` 第一项 `script`（每次虚拟机启动都跑） | 虚拟机 iptables：`DT-EGRESS` 链只放行 egress-net 到 `192.168.5.2:8086`；`INPUT` 拒绝两个网段访问虚拟机自身；停掉没人用的 dnsmasq |

两份文件里的网段一一对应，改一处要同步另一处。

## 安装 / 更新

1. 确认没有冻结中的夜跑（`DigitalTwin/runs/*/state.json` 全部 `STOPPED` / `FINISHED`），记下 `multica issue list --output json` 的状态。
2. 先备份要被覆盖的文件，放到 `DigitalTwin/evidence/<目录>/`：`colima.yaml`，以及 Multica-local 的 `runtime.compose.yml` 和 `egress/`。
3. 拷文件：`runtime.compose.yml`、`egress/*` 拷到 Multica-local；`colima-provision.sh` 的内容缩进 6 格，替换 `colima.yaml` 里 `provision` 第一项的 `script`。
4. 用 Multica-local `control.sh` 里的同一组 compose 文件，先 `build egress-proxy`，再 `down` 和 `up -d --pull never`。网络配置变了，必须先 `down`。
5. `colima stop -p multica && colima start -p multica`，确认规则开机后还在：`iptables -S DT-EGRESS` 有 9 行；宿主 `lsof -iTCP:53 -sTCP:LISTEN` 为空。
6. 跑自测：证据目录 `135-defense/fix-egress/selftest.py`，在执行器容器里以 `node` 用户执行，要求 41 项全部「符合」。前后 Multica 状态一致。

## 回滚

把备份的 `colima.yaml`、`runtime.compose.yml` 放回原处，删掉 Multica-local 的 `egress/`，执行 compose `down`、`up -d --pull never`，再重启 Colima。回滚后就回到 #135 实测时的状态，两处网络缺口重新出现。

## 白名单

`egress/allowed_domains.txt` 每行一个完整域名，不用通配。**加域名要常浩同意。** 被拒的请求在 `docker logs multica-egress-proxy-1` 里，带 `TCP_DENIED`；夜跑因缺域名失败时，先看这里。包仓库（npm、PyPI）暂不在白名单里。

## 已知限制

- 只管隔离执行器。backend、frontend、postgres 和构建容器仍在默认网络上，宿主只放行 8086，和改之前一样。
- 运行前身份闸（#135 文档第五节 3）不在本目录，另行实现。
