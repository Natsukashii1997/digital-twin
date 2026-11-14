# Colima 虚拟机防火墙（#172）。源文件；安装方式见 README「安装 / 更新」第 3 步：整段放进 ~/.colima/multica/colima.yaml
# provision 第一项（mode: system）的 script。每次虚拟机启动都跑，前台同步执行，不放后台。
# 网段与 ops/multica-egress/runtime.compose.yml 一一对应：
#   172.29.0.0/24 app-net（compose 默认网络：backend、frontend、postgres）
#   172.30.0.0/24 agent-net（内部网络：执行器、backend .2、出口代理 .3）
#   172.31.0.0/24 egress-net（只有出口代理，上游只许宿主 8086）
# 本脚本拦不住容器启动，所以执行器和出口代理不自动启动（restart: "no" + profile agent），只能经 agent-up.sh 拉起；
# agent-up.sh 先跑 verify.sh 核对本脚本的结果，规则不全、顺序不对都不拉起（fail-closed）。

# 0. 同一网桥内的容器互访也要经过 iptables，agent-net 里才能只放行 backend:8080 和代理:3128
modprobe br_netfilter
sysctl -qw net.bridge.bridge-nf-call-iptables=1

# 1. 容器经虚拟机转发的流量。iptables-restore --noflush 一次提交：DOCKER-USER 和 DT-EGRESS 在同一个事务里清空重建，
#    没有「旧链已清、新规则未加」的放行窗口。DOCKER-USER 归用户所有，Docker 只建链、加 FORWARD 跳转，不改里面的规则，
#    所以本脚本先于或晚于 dockerd 跑都一样。
iptables-restore --noflush <<'RULES'
*filter
:DOCKER-USER - [0:0]
:DT-EGRESS - [0:0]
-A DOCKER-USER -j DT-EGRESS
-A DOCKER-USER -j RETURN
-A DT-EGRESS -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A DT-EGRESS -s 172.30.0.0/24 -d 172.30.0.2/32 -p tcp -m tcp --dport 8080 -j RETURN
-A DT-EGRESS -s 172.30.0.0/24 -d 172.30.0.3/32 -p tcp -m tcp --dport 3128 -j RETURN
-A DT-EGRESS -s 172.30.0.0/24 -j DROP
-A DT-EGRESS -s 172.31.0.0/24 -d 192.168.5.2/32 -p tcp -m tcp --dport 8086 -j RETURN
-A DT-EGRESS -s 172.31.0.0/24 -j DROP
-A DT-EGRESS -s 172.29.0.0/24 -d 192.168.5.2/32 -j DROP
-A DT-EGRESS -d 192.168.5.2/32 -p tcp -m tcp --dport 8086 -j RETURN
-A DT-EGRESS -d 192.168.5.2/32 -j DROP
COMMIT
RULES
# 说明：
# - agent-net 只放行到 backend:8080、代理:3128；同网段其他端口、以后加进来的服务一律拒绝（回包走第一条 ESTABLISHED）。
# - egress-net 只放行到宿主 8086。代理的 DNS 经 Docker 内置 DNS（容器内 127.0.0.11，由 dockerd 在虚拟机里转发），不经这里。
# - app-net（backend、frontend、postgres）不能连宿主任何端口，含 8086：它们没配代理，本来也不用。
#   这样 backend 即使被借去发请求，也到不了宿主 8086，进而到不了宿主回环。
# - 其他容器（docker build 用的默认网桥）保持原规则：宿主只放行 8086。

# 2. 容器访问虚拟机自身（sshd、docker 等管理面）。INPUT 不能整链重建，逐条删掉再插到最前面；verify.sh 核对前三条。
#    app-net 只拒新连接：宿主经 docker-proxy 访问 backend、frontend 时，容器回给虚拟机的包要放行。
for rule in "-s 172.29.0.0/24 -m conntrack --ctstate NEW -j DROP" "-s 172.31.0.0/24 -j DROP" "-s 172.30.0.0/24 -j DROP"; do
  while iptables -D INPUT $rule 2>/dev/null; do :; done
  iptables -I INPUT 1 $rule
done

# 3. dnsmasq：虚拟机和容器的 DNS 都直接用 1.1.1.1 / 8.8.8.8，不经它，但它监听 53。
#    macOS 普通用户只能在通配地址上绑 1024 以下的端口，Lima 转发时因此把虚拟机的 53 开在宿主所有网卡上
#    （dnsmasq 只监听 127.0.0.1 也一样）。所以停掉并屏蔽；没停掉由 verify.sh 查出（虚拟机和宿主 TCP、UDP 53 都不许有监听）。
systemctl disable --now dnsmasq 2>/dev/null
systemctl mask dnsmasq 2>/dev/null
rm -f /etc/dnsmasq.d/dt-local-only.conf
true
