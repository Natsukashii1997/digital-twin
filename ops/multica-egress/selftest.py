# #172 执行器出网自测（源文件，README「安装 / 更新」第 6 步）。在隔离执行器容器里以 node 用户跑：
#   docker exec -i -e HOST_IPS="<宿主活动网卡地址>" -e APP_IPS="<app-net 里 postgres、frontend 的地址>" \
#     multica-isolated-agent-1 python3 - < ops/multica-egress/selftest.py
# 只做连接、TLS 握手和只读请求，不发写请求，不打印令牌。最后一行「不符合 0」才算通过。
import os, socket, ssl, subprocess, json

PROXY = ("egress-proxy", 3128)
HOSTS = [x for x in os.environ.get("HOST_IPS", "").split() if x]  # 宿主所有活动网卡地址（宿主侧枚举后传入）
APP_IPS = [x for x in os.environ.get("APP_IPS", "").split() if x]  # app-net 里 postgres、frontend 的真实地址（宿主侧查出后传入）
res = []


def rec(group, name, ok_expected, ok, detail=""):
    verdict = "符合" if ok == ok_expected else "!!! 不符合"
    res.append(verdict)
    print(f"[{group}] {name} | 预期{'通' if ok_expected else '拒'} | 实际{'通' if ok else '拒'} | {verdict} | {detail}"[:240])


def tcp(host, port, t=3):
    s = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
    s.settimeout(t)
    try:
        s.connect((host, port)); return True, "connected"
    except Exception as e:
        return False, type(e).__name__
    finally:
        s.close()


def connect_via_proxy(target, tls_host=None):
    try:
        s = socket.create_connection(PROXY, timeout=8)
    except Exception as e:
        return False, "代理连不上 " + type(e).__name__
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    s.settimeout(15)
    try:
        line = s.recv(400).split(b"\r\n")[0].decode(errors="replace")
    except Exception as e:
        s.close(); return False, type(e).__name__
    if " 200" not in line:
        s.close(); return False, line
    if not tls_host:
        s.close(); return True, line
    try:
        ctx = ssl.create_default_context()
        t = ctx.wrap_socket(s, server_hostname=tls_host)
        cert = t.getpeercert(); t.close()
        cn = dict(x[0] for x in cert["subject"]).get("commonName", "?")
        return True, f"{line} | TLS 证书 CN={cn}"
    except Exception as e:
        return False, f"{line} | TLS 失败 {type(e).__name__}"


def get_via_proxy(url):
    s = socket.create_connection(PROXY, timeout=8)
    s.sendall(f"GET {url} HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n".encode())
    s.settimeout(10)
    line = s.recv(300).split(b"\r\n")[0].decode(errors="replace"); s.close()
    return (" 200" in line), line


# A 组：宿主网络隔离（直连）
for h, p in [("192.168.5.2", 8086), ("192.168.5.2", 43101), ("192.168.5.2", 5000), ("192.168.5.2", 22),
             ("1.1.1.1", 443), ("140.82.112.3", 443)] + [(ip, port) for ip in HOSTS for port in (5000, 7000, 53, 22, 8086, 5310)]:
    ok, d = tcp(h, p); rec("A 直连", f"{h}:{p}", False, ok, d)
# A 组：经出口代理访问宿主 / 内网 / 非白名单
for t in ["127.0.0.1:8085", "127.0.0.1:5310", "localhost:5280", "[::1]:43101", "192.168.5.2:8086",
          *[f"{ip}:5000" if ":" not in ip else f"[{ip}]:5000" for ip in HOSTS], "172.18.0.1:22", "127.0.0.1:50055", "example.com:443", "github.com:22", "github.com.evil.example:443",
          # IP 的各种写法、尾随点、伪装 authority、IDN、解析到回环的域名（#172 返修补测）
          "0x7f000001:443", "2130706433:443", "017700000001:443", "127.1:443", "0177.0.0.1:443", "[::ffff:127.0.0.1]:443", "[::1]:443",
          "[0:0:0:0:0:0:0:1]:443", "172.30.0.2:443", "172.29.0.1:443", "localhost.:443", "localtest.me:443", "127.0.0.1.nip.io:443",
          "github.com@127.0.0.1:443", "127.0.0.1#github.com:443", "github.com%2e:443", "xn--gthub-cta.com:443", "evilgithub.com:443"]:
    ok, d = connect_via_proxy(t); rec("A 经代理", f"CONNECT {t}", False, ok, d)
# squid 把端口后面的多余部分截掉，规整成 github.com:443（代理日志为 CONNECT github.com:443，上游也是这个），等同白名单目标，应通
ok, d = connect_via_proxy("github.com:443.evil.example:443"); rec("A 经代理", "CONNECT github.com:443.evil.example:443（规整为 github.com:443）", True, ok, d)
for u in ["http://127.0.0.1:8085/", "http://github.com/"]:
    try:
        ok, d = get_via_proxy(u)
    except Exception as e:
        ok, d = False, type(e).__name__
    rec("A 经代理", f"GET {u}", False, ok, d)
# B 组：虚拟机管理面、同主机其他服务
for h, p in [("172.30.0.1", 22), ("172.30.0.1", 53), ("172.30.0.1", 2375), ("172.30.0.1", 2376), ("172.18.0.1", 22),
             ("172.18.0.1", 2375), ("172.17.0.1", 22), ("192.168.5.1", 22), ("192.168.5.1", 2375),
             ("postgres", 5432), ("frontend", 3000), ("172.18.0.2", 5432),
             # agent-net 里只放行 backend:8080、代理:3128；其他端口一律拒（#172 返修）
             ("172.30.0.2", 22), ("172.30.0.2", 5432), ("172.30.0.2", 6060), ("172.30.0.3", 22), ("172.30.0.3", 3129), ("172.30.0.1", 8080),
             ("172.29.0.1", 22), ("172.31.0.1", 22)] + [(ip, port) for ip in APP_IPS for port in (5432, 3000, 8080)]:
    try:
        ok, d = tcp(h, p)
    except Exception as e:
        ok, d = False, type(e).__name__
    rec("B 管理面", f"{h}:{p}", False, ok, d)
# 外部 DNS 解析（内部网络不应能解析外网名字）
try:
    socket.getaddrinfo("github.com", 443); rec("A DNS", "解析 github.com", False, True, "能解析")
except Exception as e:
    rec("A DNS", "解析 github.com", False, False, type(e).__name__)

# 正向对照
ok, d = tcp("backend", 8080); rec("正向", "backend:8080", True, ok, d)
ok, d = tcp("172.30.0.2", 8080); rec("正向", "172.30.0.2:8080", True, ok, d)
# IPv6：容器里不应有任何 IPv6 地址（含链路本地），否则同网桥可能绕开 IPv4 防火墙
v6 = [l.split()[0] for l in open("/proc/net/if_inet6").read().splitlines() if l.strip() and l.split()[0] != "0" * 31 + "1"]
rec("B IPv6", "容器 IPv6 地址", False, bool(v6), " ".join(v6) or "无")
for host in ["github.com", "api.github.com", "api.anthropic.com", "claude.ai", "chatgpt.com", "api.openai.com", "auth.openai.com", "GitHub.COM"]:
    ok, d = connect_via_proxy(f"{host}:443", tls_host=host.lower()); rec("正向", f"经代理 TLS {host}", True, ok, d)  # GitHub.COM：域名大小写不敏感，应通
try:
    who = subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, timeout=30)
    rec("正向", "gh api user", True, who.returncode == 0, (who.stdout or who.stderr).strip()[:80])
    lr = subprocess.run(["git", "ls-remote", "https://github.com/Natsukashii1997/dt-sandbox", "refs/heads/main"], capture_output=True, text=True, timeout=60)
    rec("正向", "git ls-remote dt-sandbox", True, lr.returncode == 0, lr.stdout.strip()[:60] or lr.stderr.strip()[:80])
except Exception as e:
    rec("正向", "gh/git", True, False, type(e).__name__)

print("== 合计", len(res), "项，不符合", sum(1 for r in res if r != "符合"))
