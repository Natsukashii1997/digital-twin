#!/usr/bin/env python3
"""Multica 隔离执行器出网防线的只读核对（#172 返修）。在宿主上跑，任何一项不符或读不到都判失败（退出码 1）。

  verify.py drift   安装副本（Multica-local、colima.yaml）和本目录源文件逐字一致
  verify.py vm      虚拟机防火墙：DOCKER-USER / DT-EGRESS 与 colima-provision.sh 完全一致、跳转在最前；INPUT 前三条；
                    br_netfilter；dnsmasq 没在跑；虚拟机和宿主 TCP、UDP 53 都没有监听；默认网络、agent-net 没开 IPv6、网段对
                    （egress-net 随代理创建，在 proxy 里核对）
  verify.py proxy   出口代理容器：不自动重启、网络和地址对、容器里的 squid 配置与源文件一致、对外只听 3128、没有 IPv6 地址
  verify.py agent   执行器容器：不自动重启、只接 agent-net、代理变量指向出口代理、没有 IPv6 地址；backend 在 agent-net 上
                    固定 .2、对外只听 8080
  verify.py all     以上全部（执行器和代理要在跑）

只读：只执行 inspect、cat、iptables -S、ss 之类的查询，不改任何东西。agent-up.sh 在拉起执行器前后调用它。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get("MULTICA_DIR", str(Path.home() / "Documents/Codex/Multica-local")))
COLIMA_YAML = Path(os.environ.get("COLIMA_YAML", str(Path.home() / ".colima/multica/colima.yaml")))
DOCKER = ["/opt/homebrew/bin/docker"]
ENV = dict(os.environ, DOCKER_CONFIG=str(RUNTIME / "docker"),
           DOCKER_HOST=os.environ.get("DOCKER_HOST", f"unix://{Path.home()}/.colima/multica/docker.sock"),
           PATH="/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin")
AGENT, PROXY, BACKEND = "multica-isolated-agent-1", "multica-egress-proxy-1", "multica-backend-1"
NETS = {"default": "172.29.0.0/24", "agent-net": "172.30.0.0/24", "egress-net": "172.31.0.0/24"}
INPUT_HEAD = ["-A INPUT -s 172.30.0.0/24 -j DROP", "-A INPUT -s 172.31.0.0/24 -j DROP",
              "-A INPUT -s 172.29.0.0/24 -m conntrack --ctstate NEW -j DROP"]

problems = []


def bad(msg):
    problems.append(msg)
    print("  ✗ " + msg)


def good(msg):
    print("  ✓ " + msg)


def run(cmd, timeout=60):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=ENV)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd[:4])} 失败（{r.returncode}）：{(r.stderr or r.stdout).strip()[:160]}")
    return r.stdout


def vm(cmd):
    return run(["colima", "ssh", "-p", "multica", "--", "sudo", "sh", "-c", cmd])


def docker(*a):
    return run(DOCKER + list(a))


def check(name, fn):
    try:
        fn()
    except Exception as e:  # 读不到就判失败
        bad(f"{name}：核对失败 {type(e).__name__}: {str(e)[:200]}")


def provision_script():
    """colima.yaml 里 provision 第一项的 script（去掉 6 格缩进）。"""
    lines = COLIMA_YAML.read_text(encoding="utf-8").splitlines()
    i = next(n for n, l in enumerate(lines) if l.startswith("provision:"))
    j = next(n for n in range(i, len(lines)) if lines[n].strip() == "script: |")
    out = []
    for l in lines[j + 1:]:
        if l.strip() and not l.startswith("      "):
            break
        out.append(l[6:])
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out) + "\n"


def expected_chain(chain):
    src = (HERE / "colima-provision.sh").read_text(encoding="utf-8")
    return [l for l in src.splitlines() if l.startswith(f"-A {chain} ")]


# ---------- drift ----------
def drift():
    print("[drift] 安装副本与源文件")
    pairs = [("runtime.compose.yml", RUNTIME / "runtime.compose.yml"),
             ("egress/Dockerfile", RUNTIME / "egress/Dockerfile"),
             ("egress/squid.conf", RUNTIME / "egress/squid.conf"),
             ("egress/allowed_domains.txt", RUNTIME / "egress/allowed_domains.txt")]
    for rel, inst in pairs:
        def one(rel=rel, inst=inst):
            if (HERE / rel).read_bytes() != inst.read_bytes():
                bad(f"{inst} 与源文件 {rel} 不一致")
            else:
                good(f"{rel} 一致")
        check(rel, one)

    def prov():
        if provision_script() != (HERE / "colima-provision.sh").read_text(encoding="utf-8"):
            bad(f"{COLIMA_YAML} 的 provision script 与 colima-provision.sh 不一致")
        else:
            good("colima.yaml provision 一致")
    check("provision", prov)


# ---------- vm ----------
def vm_checks():
    print("[vm] 虚拟机防火墙与监听")

    def chains():
        for chain in ("DOCKER-USER", "DT-EGRESS"):
            got = [l for l in vm(f"iptables -S {chain}").splitlines() if l.startswith("-A ")]
            want = expected_chain(chain)
            if chain == "DOCKER-USER":
                # Docker 可能在链尾补 RETURN；要求第一条是跳转 DT-EGRESS，其余只能是 RETURN
                ok = got[:1] == want[:1] and all(l in want for l in got)
            else:
                ok = got == want
            (good if ok else bad)(f"{chain} {'与 provision 一致' if ok else '与 provision 不一致：' + json.dumps(got, ensure_ascii=False)[:400]}")
        fwd = [l for l in vm("iptables -S FORWARD").splitlines() if l.startswith("-A ")]
        (good if fwd[:1] == ["-A FORWARD -j DOCKER-USER"] else bad)(f"FORWARD 第一条：{fwd[:1]}")
        inp = [l for l in vm("iptables -S INPUT").splitlines() if l.startswith("-A ")]
        (good if inp[:3] == INPUT_HEAD else bad)(f"INPUT 前三条：{inp[:3]}")
    check("iptables", chains)

    def sysctl():
        v = vm("sysctl -n net.bridge.bridge-nf-call-iptables").strip()
        (good if v == "1" else bad)(f"bridge-nf-call-iptables={v}")
    check("br_netfilter", sysctl)

    def dns():
        st = subprocess.run(["colima", "ssh", "-p", "multica", "--", "systemctl", "is-active", "dnsmasq"],
                            capture_output=True, text=True, env=ENV).stdout.strip()
        (good if st != "active" else bad)(f"虚拟机 dnsmasq：{st or '未知'}")
        ls = vm("ss -Hlntu '( sport = :53 )'").strip()
        (good if not ls else bad)(f"虚拟机 53 监听：{ls or '无'}")
        host = []
        for proto in ("-iTCP:53", "-iUDP:53"):
            r = subprocess.run(["lsof", "-nP", proto], capture_output=True, text=True)
            for l in r.stdout.splitlines()[1:]:
                name = l.split()[8] if len(l.split()) > 8 else ""
                local = name.split("->")[0]
                # 只算本机 53 端口上的监听 / 绑定；出站查询（本地随机端口 -> 远端 :53）不算
                if local.endswith(":53") and (proto == "-iUDP:53" or "(LISTEN)" in l):
                    host.append(l)
        (good if not host else bad)(f"宿主 53 监听：{host[:3] or '无'}")
    check("53", dns)

    check("networks", lambda: nets(("default", "agent-net")))


def nets(which):
    names = docker("network", "ls", "--format", "{{.Name}}").split()
    for short in which:
        subnet = NETS[short]
        hit = [n for n in names if n == f"multica_{short}"]
        if not hit:
            bad(f"网络 multica_{short} 不存在")
            continue
        info = json.loads(docker("network", "inspect", hit[0]))[0]
        subs = [c.get("Subnet") for c in (info.get("IPAM") or {}).get("Config") or []]
        ok = info.get("EnableIPv6") is False and subs == [subnet] and (short != "agent-net" or info.get("Internal") is True)
        (good if ok else bad)(f"{hit[0]}：IPv6={info.get('EnableIPv6')} 网段={subs} internal={info.get('Internal')}")


# ---------- containers ----------
def inspect(name):
    return json.loads(docker("inspect", name))[0]


def listeners(name):
    """容器网络命名空间里对外（非 127.x / ::1）监听的 TCP 端口和绑定的 UDP 端口。借出口代理镜像（Debian）读 /proc/net。"""
    image = inspect(PROXY)["Config"]["Image"]
    out = docker("run", "--rm", "--network", f"container:{name}", "--entrypoint", "sh", image, "-c",
                 "for f in tcp tcp6 udp udp6; do echo \"== $f\"; cat /proc/net/$f; done")
    ports, kind = set(), None
    for l in out.splitlines():
        if l.startswith("== "):
            kind = l[3:]
            continue
        f = l.split()
        if len(f) < 4 or f[0] == "sl":
            continue
        addr, port = f[1].split(":")
        if kind.startswith("tcp") and f[3] != "0A":
            continue
        if kind.endswith("6"):
            # /proc/net/tcp6 每 32 位一组、组内小端：::1 是 0…0 01000000，::ffff:127.x 是 0000000000000000FFFF0000 xx00007F
            loop = addr == "0" * 24 + "01000000" or (addr.startswith("0000000000000000FFFF0000") and addr.endswith("7F"))
        else:
            loop = addr.endswith("7F")  # 小端，最高位字节在最后
        if not loop:
            ports.add(f"{kind}/{int(port, 16)}")
    return ports


def tcp_only(ports, want):
    """对外 TCP 监听恰好是 want（v4、v6 都算同一个端口）；UDP 只允许 32768 以上的临时端口（squid、Go 的 DNS 客户端套接字）。
    agent-net 到这两个容器只放行 TCP 8080 / 3128（DT-EGRESS），UDP 本来就到不了。"""
    tcp = {int(p.split("/")[1]) for p in ports if p.startswith("tcp")}
    udp = {int(p.split("/")[1]) for p in ports if p.startswith("udp")}
    return tcp == {want} and all(u >= 32768 for u in udp)


def no_ipv6(name):
    v6 = docker("exec", name, "cat", "/proc/net/if_inet6").split("\n")
    addrs = [l.split()[0] for l in v6 if l.strip() and l.split()[0] != "0" * 31 + "1"]
    (good if not addrs else bad)(f"{name} IPv6 地址：{addrs or '无'}")


def nets_of(c):
    return {k.split("_", 1)[1]: v.get("IPAddress") for k, v in c["NetworkSettings"]["Networks"].items()}


def proxy_checks():
    print("[proxy] 出口代理容器")

    def body():
        c = inspect(PROXY)
        (good if c["State"]["Running"] else bad)(f"{PROXY} 运行中={c['State']['Running']}")
        rp = c["HostConfig"]["RestartPolicy"]["Name"]
        (good if rp == "no" else bad)(f"{PROXY} 重启策略={rp}")
        n = nets_of(c)
        (good if n == {"agent-net": "172.30.0.3", "egress-net": "172.31.0.3"} else bad)(f"{PROXY} 网络={n}")
        nets(("egress-net",))
        for rel in ("squid.conf", "allowed_domains.txt"):
            inside = docker("exec", PROXY, "cat", f"/etc/squid/{rel}")
            (good if inside == (HERE / "egress" / rel).read_text(encoding="utf-8") else bad)(f"容器内 {rel} 与源文件{'一致' if inside == (HERE / 'egress' / rel).read_text(encoding='utf-8') else '不一致'}")
        lp = listeners(PROXY)
        (good if tcp_only(lp, 3128) else bad)(f"{PROXY} 对外监听={sorted(lp)}")
        no_ipv6(PROXY)
    check("proxy", body)


def agent_checks():
    print("[agent] 执行器与 backend")

    def body():
        c = inspect(AGENT)
        (good if c["State"]["Running"] else bad)(f"{AGENT} 运行中={c['State']['Running']}")
        rp = c["HostConfig"]["RestartPolicy"]["Name"]
        (good if rp == "no" else bad)(f"{AGENT} 重启策略={rp}")
        n = nets_of(c)
        ok = list(n) == ["agent-net"] and n["agent-net"].startswith("172.30.0.") and int(n["agent-net"].rsplit(".", 1)[1]) >= 128
        (good if ok else bad)(f"{AGENT} 网络={n}")
        env = dict(e.split("=", 1) for e in c["Config"]["Env"] if "=" in e)
        want = "http://egress-proxy:3128"
        ok = all(env.get(k) == want for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"))
        (good if ok else bad)(f"{AGENT} 代理变量指向出口代理={ok}")
        (good if c["HostConfig"].get("Privileged") is False and c["HostConfig"].get("CapDrop") == ["ALL"] else bad)(
            f"{AGENT} 非特权、去掉全部权限")
        no_ipv6(AGENT)
        b = inspect(BACKEND)
        bn = nets_of(b)
        (good if bn.get("agent-net") == "172.30.0.2" and set(bn) == {"default", "agent-net"} else bad)(f"backend 网络={bn}")
        lb = listeners(BACKEND)
        (good if tcp_only(lb, 8080) else bad)(f"backend 对外监听={sorted(lb)}")
    check("agent", body)


def main():
    mode = (sys.argv[1:] or ["all"])[0]
    steps = {"drift": [drift], "vm": [vm_checks], "proxy": [proxy_checks], "agent": [agent_checks],
             "all": [drift, vm_checks, proxy_checks, agent_checks]}
    if mode not in steps:
        print(__doc__)
        return 2
    for fn in steps[mode]:
        fn()
    print(f"== {mode}：{'通过' if not problems else f'{len(problems)} 项不符，不能拉起 / 继续使用执行器'}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
