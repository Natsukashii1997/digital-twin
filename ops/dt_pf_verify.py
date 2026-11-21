"""门卫规则的真机越权测试（ADR-015 批 0E-1，L3 P1-02）：证明 pf 实际挡住了数据包，不只是规则文字里有 block。

由 `sudo bash ops/dt_worker_pf.sh verify` 以 root 运行：
  1. root 在本机每个地址（127.0.0.1、::1、各网卡上的局域网 / utun 地址，IPv4 和 IPv6）上自己开 TCP、UDP 回声监听当靶子
     ——靶子一定在、一定连得上，账号连不上只能是门卫挡的；另外再挑本机已有的几个 TCP 监听（11-19 两次漏过都在这类上）；
  2. root 自己先连一遍当对照（root 不受门卫规则限制）；
  3. 以 dt-worker、dt-secretary（受门卫规则管）和 dt-gate（控制面，不受管，只作对照）的身份各连一遍
     （sudo -u <账号> /usr/bin/python3 -I -，本文件经标准输入传过去，账号不需要能读到仓库）；
  4. 逐项判定，结果写到 /Library/Application Support/DigitalTwin/verify/<时间>.json（所有人可读）。

试的东西：上面这些本机靶子（TCP、UDP）、IPv4 映射地址（::ffff:127.0.0.1，TCP 和 UDP）、公网直连 TCP 443、公网 DNS（UDP 53，
IPv4 和 IPv6；本机没有 IPv6 公网时 root 也连不上 IPv6 那项，记「跳过」）；准连的本机端口和经过滤代理 8090 上网（这两样应该通）。
UDP 的规则不分端口（一律挡），所以用 root 自己的 UDP 回声靶子证明数据面，不逐个去试系统的 UDP 端口（那些端口不回话，
分不清是被挡还是没回）。ICMP 和系统解析器代发的 DNS pf 按账号认不出，常浩 11-20 接受为已知风险，不在这里试。

入站（L3 第 4 轮 P1-05）：两个受管账号各自开 TCP、UDP 监听（各本机地址，另试占另一个受管账号准连的端口），root、dt-gate、
另一个受管账号去连，都必须连不上、监听那边一次也没收到；dt-gate 开的监听 root 必须连得上（对照）。

退出码：0 全部符合；1 有该挡没挡住（或该通不通）；2 测试本身不完整（账号没跑起来、结果条数不对、对照组 root 自己也连不上、
lsof / ifconfig 出错、开不了靶子、压力模式的重新加载没覆盖整段测试）。

压力模式 `sudo bash ops/dt_worker_pf.sh verify stress 600`（L3 第 3 轮 P1-02、P2-06）：11-19 试装时两次偶发漏过都在规则重新加载附近。
这个模式让两个账号同时、连续 N 秒不停去连上面这些该挡的靶子，root 同时每秒强制重新加载一次门卫规则（和开机任务加载的是同一份规则文件）。
任何一次连上了：账号那边先不断开（拿着连接 2 秒），root 的靶子在收到连接的那一刻当场抓 pf 现场——连接表里对应的那条、
这个连接属于哪个账号（lsof）、门卫锚点每条规则的命中计数、主规则集，并记下离它最近的一次重新加载差几毫秒。
判定前先核对：root 自己的靶子 root 都连得上、每个账号都跑完、结果条数对、每个靶子都试到过、重新加载没出错、测试后规则仍完好；
有一项不满足就是退出码 2，不会报「都挡住了」。
"""
import errno
import json
import os
import socket
import subprocess
import sys
import threading
import time

ALLOWED = {"dt-worker": {3350, 8090}, "dt-secretary": {3352, 3353, 8090}}
CONTROL_ACCOUNTS = ("dt-gate",)
PUBLIC_TCP = [("open.feishu.cn", 443), ("223.5.5.5", 443)]   # 国内直连本来就通的；223.5.5.5 用 IP，不经 DNS
PUBLIC_DNS = ["223.5.5.5", "8.8.8.8", "2400:3200::1"]         # 最后一个是 IPv6（阿里公共 DNS）
PUBLIC_HOSTS = [h for h, _ in PUBLIC_TCP] + PUBLIC_DNS
PROXY = ("127.0.0.1", 8090)
DIR = "/Library/Application Support/DigitalTwin"
OUT_DIR = DIR + "/verify"
RULES = DIR + "/pf-dt-worker.rules"
ANCHOR = "com.apple/dt-worker"
REACHED = ("open", "answer", "200")
HOLD = 2.0          # 压力模式里连上了先拿着连接这么久，让 root 抓现场
ON_OPEN = None      # 压力模式里账号这边一连上就调它（往标准错误报一行），root 实时读到就抓现场（审查第 4 轮 P2-7）
GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dt_root_guard.sh")
ECHO = b"dt-verify"
DNS_Q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x06feishu\x02cn\x00" + b"\x00\x01\x00\x01"


RELOAD_TIMEOUT = 10  # 一次强制重新加载超过这么久没完，算出错（L3 第 4 轮 P2-06）
SERVE_SECONDS = 600  # 入站测试里账号开着监听的上限（root 连完就结束它；提前自己退了算测试不完整）


class Incomplete(Exception):
    """测试本身做不完整（枚举本机监听、网卡出错等）：退出码 2，不会当成「全部符合」（L3 第 4 轮 P2-08）。"""


def reached(got):
    return got.split(":", 1)[0] in REACHED


def family(host):
    return socket.AF_INET6 if ":" in host else socket.AF_INET


# ---------- 以账号身份运行的部分：逐项去连，只报结果 ----------

def try_tcp(host, port, timeout=3, hold=0.0):
    s = socket.socket(family(host), socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        lport = s.getsockname()[1]   # 带上本地端口：漏过时拿它去 pf 连接表里找
        if ON_OPEN:
            ON_OPEN("tcp", lport)
        if hold:
            time.sleep(hold)         # 先不断开：root 那边在抓现场
        return f"open:{lport}"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as e:
        return f"error:{e.errno}"
    finally:
        s.close()


def try_udp(host, port, timeout=3, payload=ECHO, check=None):
    """发一个 UDP 包等回话：回了 = 包过去了（answer）；被挡时 pf 回 ICMP，收的时候报 refused 或别的错。"""
    s = socket.socket(family(host), socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        lport = s.getsockname()[1]
        s.send(payload)
        data = s.recv(512)
        if not (check or (lambda d: d == payload))(data):
            return "garbage"
        if ON_OPEN:
            ON_OPEN("udp", lport)
        return f"answer:{lport}"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as e:
        return f"error:{e.errno}"
    finally:
        s.close()


def try_dns(server, timeout=3):
    return try_udp(server, 53, timeout, DNS_Q, lambda d: d[:2] == DNS_Q[:2])


def try_proxy(host="api.github.com", port=443, timeout=8):
    try:
        with socket.create_connection(PROXY, timeout=timeout) as s:
            s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
            line = s.recv(256).split(b"\r\n", 1)[0].decode(errors="replace")
            return "200" if " 200 " in line + " " else f"proxy:{line[:40]}"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as e:
        return f"error:{e.errno}"


def probe_one(t, timeout=3, hold=0.0):
    if t["kind"] == "tcp":
        return try_tcp(t["host"], t["port"], timeout, hold)
    if t["kind"] == "udp":
        return try_udp(t["host"], t["port"], timeout)
    if t["kind"] == "dns":
        return try_dns(t["host"], timeout)
    return try_proxy()


def probe(targets):
    return [probe_one(t) for t in targets]


def probe_stress(targets, seconds):
    """连续 seconds 秒轮流去连每个靶子；连上了先拿着 HOLD 秒。返回每个靶子的尝试次数、各种结果的计数、每次连上的时间和本地端口。"""
    out = [{"n": 0, "got": {}, "opens": [], "log": []} for _ in targets]
    end = time.time() + seconds
    while time.time() < end:
        for i, t in enumerate(targets):
            at = time.time()
            g = probe_one(t, timeout=2 if t["host"] in PUBLIC_HOSTS else 0.5, hold=HOLD)
            key = g.split(":", 1)[0] if reached(g) else g
            row = out[i]
            row["n"] += 1
            row["got"][key] = row["got"].get(key, 0) + 1
            row["log"].append([round(at, 3), key])   # 每次尝试的时间和结果：事后和重新加载的时刻对照（11-21 真机）
            if reached(g):
                row["opens"].append({"at": at, "lport": int(g.split(":", 1)[1]) if ":" in g else None})
    return out


# ---------- root 的部分：靶子、对照、判定 ----------

class Targets:
    """root 自己开的 TCP、UDP 回声监听（每个本机地址各一对）。armed 之后收到的连接都是账号连进来的（root 对照已做完）：
    记下来源；给了 on_hit 就当场抓 pf 现场（账号那边还拿着连接），再放手。"""

    def __init__(self, addrs, on_hit=None):
        self.socks, self.hits, self.armed, self.on_hit = [], [], False, on_hit
        self.bind_errors, self.vanished = [], []   # 开不了靶子的地址：地址已经不在本机（vanished）只记下，别的错算测试不完整
        self.stop = threading.Event()
        self.lock = threading.Lock()
        for a in addrs:
            for kind in ("tcp", "udp"):
                s = socket.socket(family(a), socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM)
                if family(a) == socket.AF_INET6:
                    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                try:
                    s.bind((a, 0))
                except OSError as e:
                    s.close()
                    # 只有「这个地址确实不在本机了」（网卡刚切换：报 EADDRNOTAVAIL，重读一遍网卡地址也没有它）才跳过，记进报告；
                    # 别的错不知道为什么开不了，算不完整
                    (self.vanished if gone(a, e) else self.bind_errors).append(
                        {"kind": kind, "host": a, "error": f"{e.errno}:{e.strerror}"})
                    continue
                if kind == "tcp":
                    s.listen(128)
                s.settimeout(0.2)
                self.socks.append((kind, a, s.getsockname()[1], s))
        self.threads = [threading.Thread(target=self.serve, args=x, daemon=True) for x in self.socks]
        for th in self.threads:
            th.start()

    def specs(self):
        return [{"kind": k, "what": f"root 自己的 {k.upper()} 靶子", "host": a, "port": p, "own": True}
                for k, a, p, _ in self.socks]

    def hit(self, kind, host, port, peer):
        if not self.armed:
            return
        ev = {"at": time.time(), "kind": kind, "host": host, "port": port, "peer": list(peer[:2])}
        if self.on_hit:
            ev["pf"] = self.on_hit(kind, peer[1])
        with self.lock:
            self.hits.append(ev)

    def serve(self, kind, host, port, s):
        while not self.stop.is_set():
            try:
                if kind == "tcp":
                    conn, peer = s.accept()
                    self.hit(kind, host, port, peer)
                    conn.close()
                else:
                    data, peer = s.recvfrom(512)
                    self.hit(kind, host, port, peer)
                    s.sendto(data, peer)
            except socket.timeout:
                continue
            except OSError:
                if self.stop.is_set():
                    break

    def close(self):
        self.stop.set()
        for th in self.threads:
            th.join(2)
        for *_, s in self.socks:
            s.close()


def settle():
    """靶子线程每 0.2 秒收一次连接：TCP 握手在内核里就完成了，探测那边已经返回，靶子可能还没 accept。
    布防前等一会儿，别把 root 自己对照的晚到连接记成账号连进来的（CI 偶发 1≠2、本机偶发 3≠2）。"""
    time.sleep(0.6)


def listening():
    """本机正在监听的 TCP（地址, 端口），地址 * 表示所有网卡。"""
    r = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "n"], capture_output=True, text=True)
    if r.returncode != 0:   # 出错时不知道漏了哪些监听，不能当成「本机没有监听」（L3 第 4 轮 P2-08）
        raise Incomplete(f"lsof 列不出本机监听（退出码 {r.returncode}）：{r.stderr.strip()[:200]}")
    socks = set()
    for line in r.stdout.splitlines():
        if not line.startswith("n"):
            continue
        addr, _, port = line[1:].rpartition(":")
        if port.isdigit():
            socks.add((addr.strip("[]"), int(port)))
    return socks


def local_addrs():
    """本机各网卡上的非回环地址（局域网、utun 等），IPv6 链路本地地址带不了网卡名，跳过。"""
    r = subprocess.run(["ifconfig"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise Incomplete(f"ifconfig 列不出本机地址（退出码 {r.returncode}）：{r.stderr.strip()[:200]}")
    addrs = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("inet", "inet6"):
            a = parts[1].split("%")[0]
            if a.startswith(("127.", "::1", "fe80")):
                continue
            if {"tentative", "duplicated", "detached"} & set(parts):   # IPv6 地址还没就绪或冲突：谁都绑不上，不算本机地址
                continue
            addrs.append(a)
    return addrs


def gone(addr, err):
    """绑定失败是不是因为这个地址已经不在本机：EADDRNOTAVAIL 本身不够，要重读一遍网卡地址确认没有它（子 agent 审查第 4 轮）。"""
    if getattr(err, "errno", None) != errno.EADDRNOTAVAIL or addr in ("127.0.0.1", "::1"):
        return False
    try:
        return addr not in local_addrs()
    except Incomplete:
        return False


def own_addrs():
    return ["127.0.0.1", "::1"] + local_addrs()


def system_targets(n_v4=6, own_ports=()):
    """本机已有的 TCP 监听：准连的端口（应该通）、监听在所有网卡（*）上的、再挑几个只在回环上的（应该挡）。
    own_ports 是 root 自己靶子的端口：靶子先开，lsof 也会列出它们，不能再当「本机已有监听」（11-21 stress 误报）。"""
    socks = {(a, p) for a, p in listening() if p not in own_ports}
    v4_ports = sorted({p for a, p in socks if a in ("127.0.0.1", "*")})
    v6_ports = sorted({p for a, p in socks if a in ("::1", "*")})
    any_ports = sorted({p for a, p in socks if a == "*"})
    allowed = set().union(*ALLOWED.values())
    pick = lambda ports, n: sorted({p for p in ports if p in allowed or p in any_ports} |
                                   set([p for p in ports if p not in allowed and p not in any_ports][:n]))
    ts = []
    for p in pick(v4_ports, n_v4):
        ts.append({"kind": "tcp", "what": "本机回环 IPv4", "host": "127.0.0.1", "port": p})
    for p in pick(v4_ports, 3):
        ts.append({"kind": "tcp", "what": "IPv4 映射地址", "host": "::ffff:127.0.0.1", "port": p})
    for p in pick(v6_ports, 3):
        ts.append({"kind": "tcp", "what": "本机回环 IPv6", "host": "::1", "port": p})
    for a in local_addrs():
        for p in any_ports[:2]:
            ts.append({"kind": "tcp", "what": "本机网卡地址", "host": a, "port": p})
    return ts


def own_targets(own):
    ts = own.specs()
    for t in own.specs():   # 经 IPv6 套接字连 IPv4 映射地址，TCP、UDP 都试
        if t["host"] == "127.0.0.1":
            ts.append({**t, "what": t["what"] + "（IPv4 映射地址）", "host": "::ffff:127.0.0.1"})
    return ts


def public_targets():
    ts = [{"kind": "tcp", "what": "公网直连", "host": h, "port": p} for h, p in PUBLIC_TCP]
    ts += [{"kind": "dns", "what": "公网 DNS", "host": d, "port": 53, "optional": ":" in d} for d in PUBLIC_DNS]
    return ts


def own_tcp_ports(own):
    return {t["port"] for t in own.specs() if t["kind"] == "tcp"}


def targets(own):
    return (system_targets(own_ports=own_tcp_ports(own)) + own_targets(own) + public_targets() +
            [{"kind": "proxy", "what": "经过滤代理 8090 上网", "host": "api.github.com", "port": 443}])


def run_as(user, ts, mode="probe", n=None, popen=False, extra=None):
    src = open(__file__, "rb").read()
    extra = ([str(n)] if n else []) + (extra or [])
    # 在 /（所有人可进）里起进程，不受当前目录权限影响
    cmd = ["sudo", "-u", user, "-H", "/usr/bin/python3", "-I", "-", mode, json.dumps(ts)] + extra
    if popen:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/")
        p.stdin.write(src)
        p.stdin.close()
        p.stdin = None   # 脚本已经写完：communicate() 不能再碰这个管道，3.9 会去 flush 已关闭的管道而报错（11-20 真机）
        return p
    r = subprocess.run(cmd, input=src, capture_output=True, timeout=1800, cwd="/")
    return parse_result(r.returncode, r.stdout, r.stderr, len(ts))


def collect(procs, counts, timeout, on_open=None):
    """等各账号的进程跑完、收结果；没跑完的（出错、超时、结果不对）记下原因并结束它。
    标准错误边跑边读：账号报「OPEN <tcp|udp> <本地端口>」时当场调 on_open(账号, 协议, 端口)，其余留作出错说明。"""
    errs = {u: [] for u in procs}

    def drain(u, p):
        for raw in p.stderr:
            line = raw.decode(errors="replace").rstrip("\n")
            parts = line.split()
            if on_open and len(parts) == 3 and parts[0] == "OPEN" and parts[2].isdigit():
                try:
                    on_open(u, parts[1], int(parts[2]))
                except Exception as e:      # 抓现场出错不影响收结果
                    errs[u].append(f"抓现场出错：{e}")
            else:
                errs[u].append(line)
    readers = [threading.Thread(target=drain, args=(u, p), daemon=True) for u, p in procs.items()]
    for th in readers:
        th.start()
    timer = threading.Timer(timeout, lambda: [p.kill() for p in procs.values()])
    timer.start()
    outs = {}
    try:
        for u, p in procs.items():
            try:
                out = p.stdout.read()
                p.wait()
                outs[u] = (out, p.returncode)
            except Exception as e:
                p.kill()
                outs[u] = e
    finally:
        timer.cancel()
    for th in readers:
        th.join(10)
    for u in procs:
        if isinstance(outs[u], Exception):
            continue
        out, code = outs[u]
        try:
            outs[u] = parse_result(code, out, "\n".join(errs[u][-20:]).encode(), counts[u])
        except Exception as e:
            outs[u] = e
    return outs


def parse_result(code, out, err, n):
    """账号那边的结果：退出码 0、读得懂、条数和靶子一样多，不然就是没跑完（L3 第 3 轮 P2-06）。"""
    if code != 0:
        raise RuntimeError(f"退出码 {code}：{(err or b'').decode(errors='replace')[-400:]}")
    try:
        got = json.loads(out)
    except ValueError:
        raise RuntimeError("结果读不懂")
    if not isinstance(got, list) or len(got) != n:
        raise RuntimeError(f"结果条数 {len(got) if isinstance(got, list) else '?'}，应为 {n}")
    return got


def should_reach(user, t):
    if t["kind"] == "proxy":
        return True
    return t["kind"] == "tcp" and t["host"] in ("127.0.0.1", "::ffff:127.0.0.1") and t["port"] in ALLOWED[user]


def judge(user, t, got, ctrl):
    if should_reach(user, t):
        return "符合" if reached(got) else "该通不通"
    if reached(got):
        return "没挡住"
    if reached(ctrl):
        return "符合"
    return "跳过" if t.get("optional") else "没法判断"


def account_exists(u):
    return subprocess.run(["id", u], capture_output=True).returncode == 0


def pfctl(*args):
    return subprocess.run(["/sbin/pfctl", *args], capture_output=True, text=True).stdout


def pf_snapshot(kind, lport):
    """漏过的那个连接在 pf 连接表里的样子（详细版带「是哪条规则建的这条连接」：不是门卫规则，就说明 pf 当时
    没认出这个连接属于谁，审查第 4 轮 P2-7）、它属于谁、门卫锚点每条规则的命中计数、主规则集。"""
    states = [l for l in pfctl("-ss").splitlines() if f":{lport} " in l + " " or f"[{lport}]" in l]
    owner = subprocess.run(["lsof", "-nP", f"-i{kind.upper()}:{lport}", "-F", "pcuLn"],
                           capture_output=True, text=True).stdout
    return {"at": time.time(), "states": states[:50], "states_verbose": states_verbose(lport)[:50], "owner": owner[:2000],
            "anchor_rules": pfctl("-a", ANCHOR, "-vsr"), "main_rules": pfctl("-s", "rules")}


def states_verbose(lport):
    """pfctl -vv -ss 里和这个端口有关的连接（每条连同下面缩进的详细行，含 rule 编号）。"""
    blocks, cur = [], []
    for line in pfctl("-vv", "-ss").splitlines():
        if line[:1] not in (" ", "\t") and cur:
            blocks.append("\n".join(cur))
            cur = []
        cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return [b for b in blocks if f":{lport} " in b.split("\n", 1)[0] + " " or f"[{lport}]" in b.split("\n", 1)[0]]


def root_path_ok(path):
    """从 / 起每一级只有 root 能改（和门卫脚本同一份核对，ops/dt_root_guard.sh）：不然 root 写记录可能被引到别处（审查第 4 轮 P3-4）。"""
    return subprocess.run(["/bin/bash", "-c", '. "$1" && root_path "$2"', "_", GUARD, path]).returncode == 0


def write_report(prefix, report):
    if not root_path_ok(DIR):
        raise SystemExit(f"{DIR} 不是只有 root 能改的目录，不写记录")
    os.makedirs(OUT_DIR, mode=0o755, exist_ok=True)
    if not root_path_ok(OUT_DIR):
        raise SystemExit(f"{OUT_DIR} 不是只有 root 能改的目录，不写记录")
    path = os.path.join(OUT_DIR, prefix + time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o644)
    return path


def control_problems(report, ts, ctrl):
    """对照组 dt-gate（以账号身份跑、不受门卫管）必须连得上 root 自己连得上的本机靶子：连不上说明「以账号身份跑」
    这一步本身有问题（比如 sudo -u 下网络都不通），受管账号的「连不上」就证明不了是门卫挡的（审查第 4 轮 P2-6）。返回 0 或 2。"""
    gate = report["accounts"].get("dt-gate")
    if not isinstance(gate, list):
        print("== 对照不完整：dt-gate 账号不存在或没跑完，受管账号连不上不能算门卫挡的")
        report["control"] = "dt-gate 没跑完"
        return 2
    bad = [f"{t['kind']} {t['host']}:{t['port']}" for t, row, c in zip(ts, gate, ctrl)
           if t.get("own") and reached(c) and not reached(row["got"])]
    report["control"] = bad or "ok"
    if bad:
        print(f"== 对照不完整：dt-gate 连不上 root 自己的靶子 {len(bad)} 个（{'、'.join(bad[:5])}）")
        return 2
    return 0


# ---------- 入站（L3 第 4 轮 P1-05）：账号开监听，别人连不上 ----------

def serve_listen(hosts, ports, seconds):
    """以账号身份跑的那一半：在每个地址上开 TCP、UDP 监听（端口 0），另在 127.0.0.1 上试着占 ports 里的端口（冒充另一个账号
    准连的服务），第一行打印开了哪些（JSON），之后谁连进来就往标准错误报一行「HIT 协议 地址 端口」，最多 seconds 秒。"""
    socks, out = [], []
    plan = [(k, h, 0) for h in hosts for k in ("tcp", "udp")] + [(k, "127.0.0.1", p) for p in ports for k in ("tcp", "udp")]
    for kind, host, port in plan:
        s = socket.socket(family(host), socket.SOCK_STREAM if kind == "tcp" else socket.SOCK_DGRAM)
        if family(host) == socket.AF_INET6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        if kind == "tcp":   # 上一个账号刚在这个端口上收过连接，留着 TIME_WAIT；不加这个，冒充端口对照接着开同一端口会绑不上
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError as e:
            s.close()
            out.append({"kind": kind, "host": host, "port": port, "error": f"{e.errno}"})
            continue
        if kind == "tcp":
            s.listen(16)
        s.setblocking(False)
        socks.append((kind, host, s))
        out.append({"kind": kind, "host": host, "port": s.getsockname()[1], "takeover": bool(port)})
    print(json.dumps(out), flush=True)
    end = time.time() + seconds
    import select
    while time.time() < end and socks:
        ready, _, _ = select.select([s for *_, s in socks], [], [], 0.2)
        for kind, host, s in socks:
            if s not in ready:
                continue
            try:
                if kind == "tcp":
                    c, _ = s.accept()
                else:
                    data, peer = s.recvfrom(512)
            except OSError:
                continue
            # 收到就记，回包另算：包已经进来了，回包被自己的出站拒绝挡掉也是漏过（单向送数据本身就是通道）
            sys.stderr.write(f"HIT {kind} {host} {s.getsockname()[1]}\n")
            sys.stderr.flush()
            try:
                c.close() if kind == "tcp" else s.sendto(data, peer)
            except OSError:
                pass
    return 0


def inbound_targets(listeners):
    return [{"kind": l["kind"], "what": "账号自己开的监听" + ("（占另一个账号准连的端口）" if l.get("takeover") else ""),
             "host": l["host"], "port": l["port"], "takeover": l.get("takeover", False)}
            for l in listeners if "error" not in l]


def inbound_judge(server, ts, probers, hits):
    """probers：{连的人: 结果列表}。受管账号开的监听谁都不该连上、也不该收到任何东西；dt-gate 开的 root 必须连得上
    （对照：证明这套办法本身连得通）。返回（0 / 1 / 2, 明细）。"""
    rows, worst = [], 0
    for who, got in probers.items():
        if not isinstance(got, list) or len(got) != len(ts):
            rows.append({"by": who, "error": str(got)[:300]})
            worst = max(worst, 2)
            continue
        for t, g in zip(ts, got):
            if g == "没试":
                continue
            if server in CONTROL_ACCOUNTS:
                v = "对照" if reached(g) else "对照连不上"
            else:
                v = "没挡住" if reached(g) else "符合"
            rows.append({**t, "by": who, "got": g, "verdict": v})
            if v == "没挡住":
                worst = 1
            elif v == "对照连不上":
                worst = max(worst, 2)
    if server not in CONTROL_ACCOUNTS and hits:
        worst = 1
    return worst, rows


def takeover_control(ports, users):
    """冒充端口的对照：dt-gate 在同样的端口上开监听，受管账号去连必须连得上。连不上说明「连不上」可能是它自己的出站放行
    坏了，不能算对方的入站拒绝起了作用：算测试不完整（子 agent 审查第 4 轮）。"""
    p = run_as("dt-gate", [], "serve", SERVE_SECONDS, popen=True, extra=[json.dumps(ports)])
    rows, worst = [], 0
    try:
        import select
        ready = select.select([p.stdout], [], [], 60)[0]
        first = p.stdout.readline() if ready else b""
        try:
            listeners = json.loads(first) if first.strip() else None
        except ValueError:
            listeners = None
        if not isinstance(listeners, list):
            return 2, [{"by": "dt-gate", "error": "冒充端口对照的监听没开起来"}]
        ts = [t for t in inbound_targets(listeners) if t["kind"] == "tcp"]
        if len(ts) != len(ports):
            return 2, [{"by": "dt-gate", "error": f"冒充端口对照有端口开不了：{listeners}"}]
        for u in users:
            try:
                got = run_as(u, ts)
            except Exception as e:
                got = e
            w, r = inbound_judge("dt-gate", ts, {u: got}, [])
            rows += [{**x, "what": "冒充端口对照（dt-gate 开）"} for x in r]
            worst = max(worst, w)
    finally:
        p.kill()
        p.wait()
    return worst, rows


def inbound():
    """L3 第 4 轮 P1-05：两个受管账号不能对外提供服务。每个账号各开一组监听（各本机地址上的 TCP、UDP，另试占另一个受管账号
    准连的端口），root、dt-gate 去连，被占端口再由另一个受管账号去连（它准连这些端口，正是冒充的场景）：全都要连不上、
    监听那边一次也没收到；dt-gate 开的监听 root 要连得上（对照）。返回（0 / 1 / 2, 记录）。"""
    hosts = own_addrs()
    managed = [u for u in ALLOWED if account_exists(u)]
    report, worst = {}, 0
    if not account_exists("dt-gate"):
        print("== 入站：没有 dt-gate 账号，没有对照，测试不完整")
        return 2, {"error": "没有对照账号"}
    for server in managed + ["dt-gate"]:
        others = [u for u in managed if u != server]
        # 另一个受管账号准连的端口都试着占（8090 也试：过滤代理没在跑时正是冒充的机会；在跑就绑不上，记「没测」）
        ports = sorted(set().union(*(ALLOWED[u] for u in others))) if server in ALLOWED else []
        p = run_as(server, hosts, "serve", SERVE_SECONDS, popen=True, extra=[json.dumps(ports)])
        hits, errs = [], []

        def drain(p=p, hits=hits, errs=errs):
            for raw in p.stderr:
                line = raw.decode(errors="replace").rstrip("\n")
                (hits if line.startswith("HIT ") else errs).append(line)
        th = threading.Thread(target=drain, daemon=True)
        th.start()
        import select
        try:   # 第一行（开了哪些监听）等 60 秒，卡住就算没开起来
            ready = select.select([p.stdout], [], [], 60)[0]
            first = p.stdout.readline() if ready else b""
            listeners = json.loads(first) if first.strip() else None
        except ValueError:
            listeners = None
        if not isinstance(listeners, list):
            p.kill()
            p.wait()
            th.join(5)
            print(f"== 入站 {server}：监听没开起来：{' '.join(errs[-3:])[:300]}")
            report[server] = {"error": "监听没开起来"}
            worst = max(worst, 2)
            continue
        ts = inbound_targets(listeners)
        failed = [l for l in listeners if "error" in l and not l["port"]
                  and not gone(l["host"], OSError(int(l["error"]), ""))]
        untested = sorted({l["port"] for l in listeners if "error" in l and l["port"]})
        probers = {"root": probe(ts)}
        try:
            probers["dt-gate"] = run_as("dt-gate", ts) if server != "dt-gate" else None
        except Exception as e:
            probers["dt-gate"] = e
        if probers["dt-gate"] is None:
            del probers["dt-gate"]
        take = [t for t in ts if t["takeover"] and t["kind"] == "tcp"]   # 受管账号的 UDP 出站一律挡，只有 TCP 准连端口能冒充
        for u in others:
            try:
                got = run_as(u, take) if take else []
            except Exception as e:
                got = e
            if isinstance(got, list):   # 只连被占的端口：结果按全部靶子对齐，没连的记「没试」
                it = iter(got)
                got = [next(it) if t in take else "没试" for t in ts]
            probers[u] = got
        time.sleep(0.5)                  # 最后一个包到了再收
        early = p.poll() is not None     # 监听提前没了：后面的「连不上」可能只是没人在听，不能算挡住
        p.kill()
        p.wait()
        th.join(5)
        w, rows = inbound_judge(server, ts, probers, hits)
        if take and others:              # 冒充端口的对照：同一端口换 dt-gate 开，同一个受管账号去连，必须连得上
            ctrl_w, ctrl_rows = takeover_control(sorted({t["port"] for t in take}), others)
            rows += ctrl_rows
            w = 1 if 1 in (w, ctrl_w) else max(w, ctrl_w)
        if untested:
            print(f"== 入站 {server}：这些冒充端口被占用（服务在跑），没测：{untested}")
        if failed or early:               # 端口 0 都开不了（这个地址没测到），或监听中途退了
            print(f"== 入站 {server}：测试不完整：" + ("监听中途退了；" if early else "") + (f"有地址开不了监听：{failed[:3]}" if failed else ""))
            w = max(w, 2) if w != 1 else 1
        report[server] = {"listeners": listeners, "rows": rows, "hits": hits, "takeover_untested": untested}
        print(f"== 入站：{server} 开的监听" + ("（对照）" if server == "dt-gate" else ""))
        for r in rows:
            if "error" in r:
                print(f"  [没跑完] {r['by']}：{r['error']}")
            else:
                print(f"  [{r['verdict']}] {r['by']} → {r['kind']} {r['host']}:{r['port']}{'（占端口）' if r.get('takeover') else ''} → {r['got']}")
        if hits and server != "dt-gate":
            print(f"  [没挡住] {server} 的监听收到了 {len(hits)} 次：{hits[:3]}")
        worst = 1 if 1 in (worst, w) else max(worst, w)
    return worst, report


def verify():
    own = Targets(own_addrs())
    try:
        ts = targets(own)
        ctrl = probe(ts)
        settle()                                # root 对照的 TCP 连接可能还没被靶子线程收下，等它们收完再布防
        own.armed = True                        # 之后收到的都是账号连进来的（dt-gate 对照也会连进来）
        report = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "targets": ts, "root": ctrl, "accounts": {}}
        worst = 0
        if not account_exists("dt-worker"):
            print("== dt-worker：账号不存在，测试不完整")
            worst = 2
        for u in list(ALLOWED) + list(CONTROL_ACCOUNTS):
            if not account_exists(u):
                print(f"== {u}：账号不存在，跳过")
                continue
            try:
                got = run_as(u, ts)
            except Exception as e:   # 一个账号跑不起来不影响别的账号，照样写记录
                print(f"== {u}：以这个账号身份没跑完：{e}")
                report["accounts"][u] = {"error": str(e)[:500]}
                worst = max(worst, 2) if worst != 1 else 1
                continue
            print(f"== {u}" + ("（控制面，不受门卫规则管，只作对照）" if u in CONTROL_ACCOUNTS else ""))
            leaked = [(t["kind"], g.split(":", 1)[1]) for t, g in zip(ts, got)
                      if u not in CONTROL_ACCOUNTS and reached(g) and not should_reach(u, t) and ":" in g]
            if leaked:   # 漏过时马上抓 pf 连接表
                report.setdefault("pf_on_leak", {})[u] = [pf_snapshot(k, p) for k, p in leaked[:5]]
            rows = []
            for t, g, c in zip(ts, got, ctrl):
                v = "对照" if u in CONTROL_ACCOUNTS else judge(u, t, g, c)
                rows.append({**t, "got": g, "root": c, "verdict": v})
                print(f"  [{v}] {t['what']} {t['host']}:{t['port']} → {g}（root：{c}）")
                if v in ("没挡住", "该通不通"):
                    worst = 1
                elif v == "没法判断" and worst == 0:
                    worst = 2
            report["accounts"][u] = rows
        worst = max(worst, control_problems(report, ts, ctrl)) if worst != 1 else 1
        report.update(own_bind_errors=own.bind_errors, own_vanished=own.vanished)
        if own.bind_errors:
            print("== 测试不完整：有地址开不了 root 自己的靶子：" +
                  "、".join(f"{b['kind']} {b['host']}（{b['error']}）" for b in own.bind_errors[:5]))
            worst = worst or 2
        if own.vanished:
            print("== 这些地址测试时已不在本机，没开靶子：" + "、".join(b["host"] for b in own.vanished[:5]))
        try:
            w, report["inbound"] = inbound()
        except Exception as e:           # 入站这一段出错不能把出站已经判出的结果（尤其是 1）冲掉，也不能不写记录
            print(f"== 入站：测试没跑完：{e!r}"[:300])
            w, report["inbound"] = 2, {"error": repr(e)[:500]}
        worst = 1 if 1 in (worst, w) else max(worst, w)
        report["hits_on_own_targets"] = own.hits
    finally:
        own.close()
    path = write_report("", report)
    print({0: "全部符合", 1: "有该挡没挡住或该通不通", 2: "测试不完整（见上面「没法判断」「没跑完」）"}[worst] + f"；记录在 {path}")
    print("不在承诺里的已知风险（常浩 11-20 接受）：ICMP（ping）、系统解析器代发的 DNS 查询，pf 按账号认不出")
    return worst


class Reloader(threading.Thread):
    """每 interval 秒强制重新加载一次门卫规则（同一份规则文件），记下每次的起止时间。
    每次加载有超时，线程里的任何异常都记下（L3 第 4 轮 P2-06：原来卡住或中途异常退出，只要成功过一次就不报）。"""

    def __init__(self, interval, cmd=None):
        super().__init__(daemon=True)
        self.interval, self.times, self.errors, self.problems = interval, [], 0, []
        self.cmd = cmd or ["/sbin/pfctl", "-q", "-a", ANCHOR, "-f", RULES]
        self.stop = threading.Event()

    def run(self):
        try:
            while not self.stop.wait(self.interval):
                a = time.time()
                try:
                    ok = subprocess.run(self.cmd, capture_output=True, timeout=RELOAD_TIMEOUT).returncode == 0
                except subprocess.TimeoutExpired:
                    ok = False
                    self.problems.append(f"有一次重新加载超过 {RELOAD_TIMEOUT} 秒没完")
                self.times.append([a, time.time()])
                self.errors += not ok
        except Exception as e:
            self.problems.append(f"重新加载线程出错退出：{e!r}"[:300])


def reload_coverage(rel, start, end):
    """重新加载要从头到尾覆盖整段测试（L3 第 4 轮 P2-06）：线程已停下、没出过错、开头和结尾附近都有、中间没有长空档。
    返回问题清单（空 = 覆盖完整）。"""
    gap = max(5.0, 5 * rel.interval)
    bad = list(rel.problems)
    if rel.is_alive():
        bad.append("重新加载线程测试结束后没停下")
    if rel.errors:
        bad.append(f"重新加载出错 {rel.errors} 次")
    marks = [start] + [a for a, _ in rel.times] + [end]
    worst = max(b - a for a, b in zip(marks, marks[1:]))
    if not rel.times or worst > gap:
        bad.append(f"重新加载 {len(rel.times)} 次，最长空档 {worst:.1f} 秒（应不超过 {gap:g} 秒），没覆盖整段测试")
    return bad


def nearest_reload(at, times):
    """离 at 最近的一次重新加载差几毫秒；正在重新加载时为 0。"""
    best = None
    for a, b in times:
        d = 0 if a <= at <= b else round(min(abs(at - a), abs(at - b)) * 1000)
        best = d if best is None else min(best, d)
    return best


def rules_intact():
    expected = subprocess.run(["/sbin/pfctl", "-nvf", RULES], capture_output=True, text=True).stdout
    return bool(expected) and pfctl("-a", ANCHOR, "-s", "rules") == expected


def stress_targets(own, users):
    """压力模式的靶子：root 自己的靶子（全部）、本机已有的两三个该挡的 TCP 监听、公网直连 TCP、一个公网 DNS。"""
    sys_ts = [t for t in system_targets(2, own_tcp_ports(own)) if not any(should_reach(u, t) for u in users)]
    pub = [t for t in public_targets() if t["kind"] == "tcp" or t["host"] == "223.5.5.5"]
    return sys_ts + own_targets(own) + pub


def judge_stress(users, ts, outs, reload_times):
    """返回（漏过明细, 不完整原因）。"""
    leaks, incomplete, rows = [], [], {}
    for u in users:
        got = outs.get(u)
        if not isinstance(got, list):
            incomplete.append(f"{u} 没跑完：{got}")
            rows[u] = {"error": str(got)[:500]}
            continue
        rows[u] = [{**t, **row} for t, row in zip(ts, got)]
        for t, row in zip(ts, got):
            if not row.get("n"):
                incomplete.append(f"{u} 对 {t['host']}:{t['port']} 一次都没试到")
            for o in row.get("opens", []):
                leaks.append({"user": u, **t, **o, "ms_from_reload": nearest_reload(o["at"], reload_times)})
    return leaks, incomplete, rows


def suspects(rows, reload_times):
    """疑似漏过（11-21 真机）：同一个靶子绝大多数是「被拒」（门卫 block return 当场回绝），偶尔一次「超时」——
    多半是包出去了、回来的包被入站拒绝挡掉（TCP 握手完不成，root 靶子记不到）。逐个列出时间和离最近一次重新加载几毫秒。"""
    out = []
    for u, rs in rows.items():
        for r in rs if isinstance(rs, list) else []:
            got = r.get("got", {})
            if got.get("refused", 0) * 2 <= r.get("n", 0):
                continue
            for at, key in r.get("log", []):
                if key != "refused":
                    out.append({"user": u, "kind": r["kind"], "host": r["host"], "port": r["port"], "at": at, "got": key,
                                "ms_from_reload": nearest_reload(at, reload_times)})
    return out


def control_targets(ts, own):
    """压力模式对照组（root、dt-gate）连的目标：本机已有的监听和公网（11-19 两次漏过时 root 和 dt-gate 也在连这些），
    不连 root 自己的靶子（靶子收到的连接都当漏过）。另返回端口撞上自己靶子的目标：不该有（system_targets 已排除），
    有就是测试本身出了问题，按不完整报，不让它变成误报的「漏过」（11-21 stress 的 28 条）。"""
    ports = {t["port"] for t in own.specs()}
    others = [t for t in ts if not t.get("own")]
    return [t for t in others if t["port"] not in ports], [t for t in others if t["port"] in ports]


def stress(seconds, interval=1.0):
    if not os.path.exists(RULES) or not rules_intact():
        print("门卫规则没装好（规则文件不在，或加载上的和规则文件不一致），先 sudo bash ops/dt_worker_pf.sh install")
        return 2
    users = [u for u in ALLOWED if account_exists(u)]
    if "dt-worker" not in users:
        print("没有 dt-worker 账号，测试不完整")
        return 2
    own = Targets(own_addrs(), on_hit=pf_snapshot)
    report = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": seconds, "reload_interval": interval, "accounts": {}}
    leaks, incomplete, live = [], [], []
    lock = threading.Lock()

    def on_open(u, kind, lport):            # 受管账号连上了：当场抓现场（它还拿着连接）；对照组连上是应该的
        if u in ALLOWED:
            snap = {"user": u, "kind": kind, "lport": lport, **pf_snapshot(kind, lport)}
            with lock:
                live.append(snap)
    try:
        cands = stress_targets(own, users)
        ctrl = probe(cands)
        ts = [t for t, c in zip(cands, ctrl) if reached(c)]
        report["targets"] = ts
        report["skipped"] = [{**t, "root": c} for t, c in zip(cands, ctrl) if not reached(c)]
        others, clash = control_targets(ts, own)
        report.update(own_bind_errors=own.bind_errors, own_vanished=own.vanished)
        if own.bind_errors:
            incomplete.append("有地址开不了 root 自己的靶子（" +
                              "、".join(f"{b['kind']} {b['host']}" for b in own.bind_errors[:3]) + "）")
        elif [t for t in report["skipped"] if t.get("own")]:
            incomplete.append("root 自己的靶子 root 连不上")
        elif clash:
            incomplete.append("对照组要连的目标里混进了 root 自己靶子的端口（" +
                              "、".join(f"{t['host']}:{t['port']}" for t in clash[:3]) + "），测试本身有问题")
        elif not account_exists("dt-gate"):
            incomplete.append("没有 dt-gate 账号，没有对照组")
        else:
            report["anchor_before"] = pfctl("-a", ANCHOR, "-vsr")
            print(f"靶子 {len(ts)} 个（root 连不上、不算数的 {len(report['skipped'])} 个），{'、'.join(users)} 同时连 {seconds} 秒，"
                  f"dt-gate 和 root 同时连其中 {len(others)} 个本机已有监听和公网作对照，每 {interval:g} 秒强制重新加载一次规则……")
            settle()
            own.armed = True
            rel = Reloader(interval) if interval > 0 else None   # 间隔 0：不强制重新加载，看平时会不会漏
            if rel:
                rel.start()
            started = time.time()
            procs = {u: run_as(u, ts, "probe-stress", seconds, popen=True) for u in users}
            procs["dt-gate"] = run_as("dt-gate", others, "probe-stress", seconds, popen=True)
            root_rows = {}
            root_th = threading.Thread(target=lambda: root_rows.setdefault("rows", probe_stress(others, seconds)), daemon=True)
            root_th.start()
            counts = {u: len(ts) for u in users}
            counts["dt-gate"] = len(others)
            outs = collect(procs, counts, seconds + 600, on_open)
            root_th.join(seconds + 600)
            ended = time.time()
            if rel:
                rel.stop.set()
                rel.join(RELOAD_TIMEOUT + 5)
            own.armed = False
            times = rel.times if rel else []
            report["reloads"] = ({"count": len(rel.times), "errors": rel.errors, "problems": rel.problems} if rel
                                 else {"count": 0, "mode": "不强制重新加载"})
            report["anchor_after"] = pfctl("-a", ANCHOR, "-vsr")
            gate = outs.pop("dt-gate")
            leaks, incomplete, report["accounts"] = judge_stress(users, ts, outs, times)
            report["suspects"] = suspects(report["accounts"], times)
            report["control"] = {"dt-gate": gate if isinstance(gate, list) else str(gate)[:500], "root": root_rows.get("rows")}
            incomplete += control_incomplete(others, gate, root_rows.get("rows"))
            for h in own.hits:
                h["ms_from_reload"] = nearest_reload(h["at"], times)
            for x in live:
                x["ms_from_reload"] = nearest_reload(x["at"], times)
            if rel:
                incomplete += reload_coverage(rel, started, ended)
            report["started"], report["ended"] = started, ended
            if not rules_intact():
                incomplete.append("测试后加载上的规则和规则文件不一致")
    finally:
        own.close()
    report.update(leaks=leaks, hits_on_own_targets=own.hits, live_snapshots=live, incomplete=incomplete)
    path = write_report("stress-", report)
    for u, rows in report["accounts"].items():
        print(f"== {u}")
        for row in rows if isinstance(rows, list) else []:
            k = sum(v for g, v in row["got"].items() if g in REACHED)
            print(f"  {row['what']} {row['host']}:{row['port']}：试了 {row['n']} 次，连上 {k} 次（{row['got']}）")
    rl = report.get("reloads", {})
    print(f"重新加载 {rl.get('count', 0)} 次" + (f"（{rl['mode']}）" if rl.get("mode") else ""))
    sus = report.get("suspects", [])
    for h in own.hits:
        print(f"  root 靶子收到：{h['kind']} {h['host']}:{h['port']} ← {h['peer']}，离最近一次重新加载 {h.get('ms_from_reload')} 毫秒")
    for x in sus:
        print(f"  疑似漏过：{x['user']} → {x['kind']} {x['host']}:{x['port']} {x['got']}，离最近一次重新加载 {x['ms_from_reload']} 毫秒")
    if leaks or own.hits or live or sus:
        print(f"有漏过：账号报告 {len(leaks)} 次，root 靶子收到 {len(own.hits)} 次，当场抓到现场 {len(live)} 次，"
              f"疑似（本该被拒却超时）{len(sus)} 次；记录在 {path}")
        return 1
    if incomplete:
        print("测试不完整：" + "；".join(incomplete[:5]) + f"；记录在 {path}")
        return 2
    print(f"都挡住了，没有漏过；记录在 {path}")
    return 0


def control_incomplete(others, gate, root_rows):
    """对照组（dt-gate、root）对每个靶子都要连上过：连不上，同一时刻受管账号的「连不上」就不能算门卫挡的（审查第 4 轮 P2-6）。"""
    bad = []
    for who, rows in (("dt-gate", gate), ("root", root_rows)):
        if not isinstance(rows, list) or len(rows) != len(others):
            bad.append(f"对照组 {who} 没跑完：{str(rows)[:200]}")
            continue
        for t, row in zip(others, rows):
            if not any(row["got"].get(k) for k in REACHED):
                bad.append(f"对照组 {who} 一次都没连上 {t['host']}:{t['port']}")
    return bad


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "probe":   # 以账号身份跑的那一半
        print(json.dumps(probe(json.loads(sys.argv[2]))))
        return 0
    if len(sys.argv) >= 4 and sys.argv[1] == "probe-stress":
        global ON_OPEN
        ON_OPEN = lambda kind, lport: (sys.stderr.write(f"OPEN {kind} {lport}\n"), sys.stderr.flush())
        print(json.dumps(probe_stress(json.loads(sys.argv[2]), float(sys.argv[3]))))
        return 0
    if len(sys.argv) >= 5 and sys.argv[1] == "serve":
        return serve_listen(json.loads(sys.argv[2]), json.loads(sys.argv[4]), float(sys.argv[3]))
    if os.geteuid() != 0:
        print("要用 sudo 运行：sudo bash ops/dt_worker_pf.sh verify")
        return 1
    try:
        if len(sys.argv) >= 2 and sys.argv[1] == "stress":
            return stress(int(sys.argv[2]) if len(sys.argv) >= 3 else 600,
                          float(sys.argv[3]) if len(sys.argv) >= 4 else 1.0)
        return verify()
    except Incomplete as e:
        print(f"测试不完整：{e}")
        return 2
    except Exception as e:   # 没预料到的异常按「不完整」报：Python 默认退出码 1 会被读成「有漏过」
        print(f"测试没跑完：{e!r}"[:500])
        return 2


if __name__ == "__main__":
    sys.exit(main())
