# Colima 虚拟机防火墙（#172）。源文件；安装方式：把下面整段放进 ~/.colima/multica/colima.yaml 的
# provision 第一项（mode: system）的 script，替换原来只有两条 DOCKER-USER 规则的那段。每次虚拟机启动都会跑。
# 网段与 ops/multica-egress/runtime.compose.yml 一一对应：
#   172.30.0.0/24 agent-net（内部网络：执行器、backend、出口代理）
#   172.31.0.0/24 egress-net（只有出口代理，上游只许宿主 8086）
(for i in $(seq 1 60); do
  if iptables -L DOCKER-USER >/dev/null 2>&1; then
    # 1. 容器经虚拟机转发出去的流量
    iptables -N DT-EGRESS 2>/dev/null || iptables -F DT-EGRESS
    iptables -A DT-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
    iptables -A DT-EGRESS -s 172.30.0.0/24 -d 172.30.0.0/24 -j RETURN
    iptables -A DT-EGRESS -s 172.30.0.0/24 -j DROP
    iptables -A DT-EGRESS -s 172.31.0.0/24 -d 172.31.0.0/24 -j RETURN
    iptables -A DT-EGRESS -s 172.31.0.0/24 -d 192.168.5.2 -p tcp --dport 8086 -j RETURN
    iptables -A DT-EGRESS -s 172.31.0.0/24 -j DROP
    # 其他容器（backend、frontend、postgres、构建容器）保持原规则：宿主只放行 8086
    iptables -A DT-EGRESS -d 192.168.5.2 -p tcp --dport 8086 -j RETURN
    iptables -A DT-EGRESS -d 192.168.5.2 -j DROP
    # 旧版两条规则若还在就删掉（由 DT-EGRESS 取代）
    while iptables -D DOCKER-USER -d 192.168.5.2 -p tcp --dport 8086 -j ACCEPT 2>/dev/null; do :; done
    while iptables -D DOCKER-USER -d 192.168.5.2 -j DROP 2>/dev/null; do :; done
    iptables -C DOCKER-USER -j DT-EGRESS 2>/dev/null || iptables -I DOCKER-USER -j DT-EGRESS
    # 2. 容器访问虚拟机自身（sshd 22、dnsmasq 53、docker 等管理面）：agent-net、egress-net 一律拒绝
    iptables -C INPUT -s 172.30.0.0/24 -j DROP 2>/dev/null || iptables -I INPUT -s 172.30.0.0/24 -j DROP
    iptables -C INPUT -s 172.31.0.0/24 -j DROP 2>/dev/null || iptables -I INPUT -s 172.31.0.0/24 -j DROP
    exit 0
  fi
  sleep 2
done) &
# 3. dnsmasq：虚拟机和容器的 DNS 都直接用 1.1.1.1 / 8.8.8.8，不经它，但它监听 53。
#    macOS 普通用户只能在通配地址上绑 1024 以下的端口，Lima 转发时因此把虚拟机的 53 开在宿主所有网卡上
#    （dnsmasq 只监听 127.0.0.1 也一样）。所以直接停掉、不再开机自启。
systemctl disable --now dnsmasq 2>/dev/null || true
rm -f /etc/dnsmasq.d/dt-local-only.conf
