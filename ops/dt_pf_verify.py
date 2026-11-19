"""门卫规则的真机越权测试（ADR-015 批 0E-1，L3 P1-02）：证明 pf 实际挡住了数据包，不只是规则文字里有 block。

由 `sudo bash ops/dt_worker_pf.sh verify` 以 root 运行：
  1. root 找出本机正在监听的 TCP 端口和本机各网卡地址，定出要试的目标；
  2. root 自己先连一遍当对照（root 不受门卫规则限制：root 连得上、账号连不上，才说明是门卫挡的）；
  3. 以 dt-worker、dt-secretary（受门卫规则管）和 dt-gate（控制面，不受管，只作对照）的身份各连一遍
     （sudo -u <账号> /usr/bin/python3 -I -，本文件经标准输入传过去，账号不需要能读到仓库）；
  4. 逐项判定，结果写到 /Library/Application Support/DigitalTwin/verify/<时间>.json（所有人可读）。

试的东西：本机回环 IPv4 / IPv6、IPv4 映射地址（::ffff:127.0.0.1）、本机各网卡地址（局域网、utun）上的监听端口；
公网直连 TCP 443、公网 DNS（UDP 53）；准连的本机端口和经过滤代理 8090 上网（这两样应该通）。
ICMP 和系统解析器代发的 DNS pf 按账号认不出，不在这里试，记为剩余风险。

退出码：0 全部符合；1 有该挡没挡住（或该通不通）；2 有项目没法判断（对照组 root 自己也连不上）。

诊断模式 `sudo bash ops/dt_worker_pf.sh verify repeat 30`：11-19 试装两次各偶发漏过一项（工人 127.0.0.1:5000、
秘书 127.0.0.1:5178，端口不同、换一次就挡住了），像是 pf 偶尔认不出连接属于哪个账号就放行。这个模式挑几类该挡的目标
（本机回环、IPv4 映射、本机网卡地址、公网直连）每个连 N 次，统计漏过的比例；漏过时记下本地端口，root 随即抓 pf 的连接表
（pfctl -ss）里对应的那几条和锚点里每条规则的命中计数，写到 verify/repeat-<时间>.json。
"""
import json
import os
import socket
import subprocess
import sys
import time

ALLOWED = {"dt-worker": {3350, 8090}, "dt-secretary": {3352, 3353, 8090}}
CONTROL_ACCOUNTS = ("dt-gate",)
PUBLIC_TCP = [("open.feishu.cn", 443), ("223.5.5.5", 443)]   # 国内直连本来就通的；223.5.5.5 用 IP，不经 DNS
PUBLIC_DNS = ["223.5.5.5", "8.8.8.8"]
PROXY = ("127.0.0.1", 8090)
OUT_DIR = "/Library/Application Support/DigitalTwin/verify"
REACHED = ("open", "answer", "200")


def reached(got):
    return got.split(":", 1)[0] in REACHED


# ---------- 以账号身份运行的部分：逐项去连，只报结果 ----------

def try_tcp(host, port, timeout=3):
    fam = socket.AF_INET6 if ":" in host else socket.AF_INET
    s = socket.socket(fam, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return f"open:{s.getsockname()[1]}"   # 带上本地端口：漏过时拿它去 pf 连接表里找、和 root 对照组的端口比
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as e:
        return f"error:{e.errno}"
    finally:
        s.close()


def try_dns(server, timeout=3):
    q = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x06feishu\x02cn\x00" + b"\x00\x01\x00\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.connect((server, 53))
        s.send(q)
        return "answer" if s.recv(512)[:2] == q[:2] else "garbage"
    except ConnectionRefusedError:
        return "refused"
    except socket.timeout:
        return "timeout"
    except OSError as e:
        return f"error:{e.errno}"
    finally:
        s.close()


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


def probe_repeat(targets, n):
    """每个目标连 n 次；连上的记下本地端口，给 root 去 pf 连接表里找。"""
    out = []
    for t in targets:
        rows = []
        for _ in range(n):
            fam = socket.AF_INET6 if ":" in t["host"] else socket.AF_INET
            s = socket.socket(fam, socket.SOCK_STREAM)
            s.settimeout(1 if t["what"] != "公网直连" else 2)
            at = time.time()
            try:
                s.connect((t["host"], t["port"]))
                rows.append({"at": at, "r": "open", "lport": s.getsockname()[1]})
            except ConnectionRefusedError:
                rows.append({"at": at, "r": "refused"})
            except socket.timeout:
                rows.append({"at": at, "r": "timeout"})
            except OSError as e:
                rows.append({"at": at, "r": f"error:{e.errno}"})
            finally:
                s.close()
        out.append(rows)
    return out


def probe(targets):
    out = []
    for t in targets:
        if t["kind"] == "tcp":
            r = try_tcp(t["host"], t["port"])
        elif t["kind"] == "dns":
            r = try_dns(t["host"])
        else:
            r = try_proxy()
        out.append(r)
    return out


# ---------- root 的部分：定目标、跑对照、判定 ----------

def listening():
    """本机正在监听的 TCP（地址, 端口），地址 * 表示所有网卡。"""
    r = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-F", "n"], capture_output=True, text=True)
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
    addrs = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("inet", "inet6"):
            a = parts[1].split("%")[0]
            if a.startswith(("127.", "::1", "fe80")):
                continue
            addrs.append(a)
    return addrs


def targets():
    socks = listening()
    v4_ports = sorted({p for a, p in socks if a in ("127.0.0.1", "*")})
    v6_ports = sorted({p for a, p in socks if a in ("::1", "*")})
    any_ports = sorted({p for a, p in socks if a == "*"})
    allowed = set().union(*ALLOWED.values())
    # 准连的端口都试（应该通）；监听在所有网卡（*）上的端口全试——11-19 试装时工人连得上 127.0.0.1:5000（ControlCenter
    # 的 AirPlay，监听在 *），要看清是不是凡是 * 上的都漏；其余挑前几个（应该挡）
    pick = lambda ports, n: sorted({p for p in ports if p in allowed or p in any_ports} |
                                   set([p for p in ports if p not in allowed and p not in any_ports][:n]))
    ts = []
    for p in pick(v4_ports, 6):
        ts.append({"kind": "tcp", "what": "本机回环 IPv4", "host": "127.0.0.1", "port": p})
    for p in pick(v4_ports, 3):
        ts.append({"kind": "tcp", "what": "IPv4 映射地址", "host": "::ffff:127.0.0.1", "port": p})
    for p in pick(v6_ports, 3):
        ts.append({"kind": "tcp", "what": "本机回环 IPv6", "host": "::1", "port": p})
    for a in local_addrs():
        for p in any_ports[:2]:
            ts.append({"kind": "tcp", "what": "本机网卡地址", "host": a, "port": p})
    for h, p in PUBLIC_TCP:
        ts.append({"kind": "tcp", "what": "公网直连", "host": h, "port": p})
    for d in PUBLIC_DNS:
        ts.append({"kind": "dns", "what": "公网 DNS", "host": d, "port": 53})
    ts.append({"kind": "proxy", "what": "经过滤代理 8090 上网", "host": "api.github.com", "port": 443})
    return ts


def run_as(user, ts, mode="probe", n=None):
    src = open(__file__, "rb").read()
    extra = [str(n)] if n else []
    # 在 /（所有人可进）里起进程，不受当前目录权限影响
    r = subprocess.run(["sudo", "-u", user, "-H", "/usr/bin/python3", "-I", "-", mode, json.dumps(ts)] + extra,
                       input=src, capture_output=True, timeout=1800, cwd="/")
    if r.returncode != 0:
        raise RuntimeError(f"退出码 {r.returncode}：{r.stderr.decode(errors='replace')[-400:]}")
    return json.loads(r.stdout)


def should_reach(user, t):
    if t["kind"] == "proxy":
        return True
    return t["kind"] == "tcp" and t["host"] in ("127.0.0.1", "::ffff:127.0.0.1") and t["port"] in ALLOWED[user]


def judge(user, t, got, ctrl):
    if should_reach(user, t):
        return "符合" if reached(got) else "该通不通"
    if reached(got):
        return "没挡住"
    return "符合" if reached(ctrl) else "没法判断"


def account_exists(u):
    return subprocess.run(["id", u], capture_output=True).returncode == 0


def repeat_targets():
    """诊断模式的目标：每类挑一个该挡、root 连得上的。"""
    socks = listening()
    allowed = set().union(*ALLOWED.values())
    v4 = sorted(p for a, p in socks if a == "127.0.0.1" and p not in allowed)
    star = sorted(p for a, p in socks if a == "*" and p not in allowed)
    ts = []
    if v4:
        ts.append({"kind": "tcp", "what": "本机回环 IPv4", "host": "127.0.0.1", "port": v4[0]})
        ts.append({"kind": "tcp", "what": "IPv4 映射地址", "host": "::ffff:127.0.0.1", "port": v4[0]})
    if star:
        ts.append({"kind": "tcp", "what": "本机回环 IPv4（* 监听）", "host": "127.0.0.1", "port": star[0]})
        lan = [a for a in local_addrs() if "." in a]
        if lan:
            ts.append({"kind": "tcp", "what": "本机网卡地址", "host": lan[0], "port": star[0]})
    for h, p in PUBLIC_TCP:
        ts.append({"kind": "tcp", "what": "公网直连", "host": h, "port": p})
    return ts


def pf_snapshot(lports):
    """漏过的那几个本地端口在 pf 连接表里的样子，和锚点里每条规则的命中计数。"""
    states = subprocess.run(["/sbin/pfctl", "-ss"], capture_output=True, text=True).stdout.splitlines()
    hits = [l for l in states if any(f":{p} " in l + " " or f"[{p}]" in l for p in lports)]
    rules = subprocess.run(["/sbin/pfctl", "-a", "com.apple/dt-worker", "-vsr"], capture_output=True, text=True).stdout
    return {"states": hits[:50], "anchor_rules": rules}


def repeat(n):
    ts = repeat_targets()
    report = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "n": n, "targets": ts, "accounts": {}}
    leaked_any = False
    for u in ALLOWED:
        if not account_exists(u):
            continue
        print(f"== {u}（每个目标连 {n} 次）")
        try:
            got = run_as(u, ts, "probe-repeat", n)
        except Exception as e:
            print(f"  没跑起来：{e}")
            report["accounts"][u] = {"error": str(e)[:500]}
            continue
        lports = [r["lport"] for rows in got for r in rows if r["r"] == "open"]
        snap = pf_snapshot(lports)
        for t, rows in zip(ts, got):
            k = sum(r["r"] == "open" for r in rows)
            leaked_any |= k > 0
            print(f"  {t['what']} {t['host']}:{t['port']}：{n} 次里漏过 {k} 次")
        report["accounts"][u] = {"results": got, "pf": snap}
    os.makedirs(OUT_DIR, mode=0o755, exist_ok=True)
    path = os.path.join(OUT_DIR, "repeat-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o644)
    print(("有漏过" if leaked_any else f"{n} 次都挡住了") + f"；记录在 {path}")
    return 1 if leaked_any else 0


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "probe":   # 以账号身份跑的那一半
        print(json.dumps(probe(json.loads(sys.argv[2]))))
        return 0
    if len(sys.argv) >= 4 and sys.argv[1] == "probe-repeat":
        print(json.dumps(probe_repeat(json.loads(sys.argv[2]), int(sys.argv[3]))))
        return 0
    if os.geteuid() != 0:
        print("要用 sudo 运行：sudo bash ops/dt_worker_pf.sh verify")
        return 1
    if len(sys.argv) >= 3 and sys.argv[1] == "repeat":
        return repeat(int(sys.argv[2]))
    ts = targets()
    ctrl = probe(ts)
    report = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "targets": ts, "root": ctrl, "accounts": {}}
    worst = 0
    for u in list(ALLOWED) + list(CONTROL_ACCOUNTS):
        if not account_exists(u):
            print(f"== {u}：账号不存在，跳过")
            continue
        try:
            got = run_as(u, ts)
        except Exception as e:   # 一个账号跑不起来不影响别的账号，照样写记录
            print(f"== {u}：以这个账号身份没跑起来：{e}")
            report["accounts"][u] = {"error": str(e)[:500]}
            worst = max(worst, 2) if worst != 1 else 1
            continue
        rows = []
        print(f"== {u}" + ("（控制面，不受门卫规则管，只作对照）" if u in CONTROL_ACCOUNTS else ""))
        leaked = [g.split(":", 1)[1] for t, g in zip(ts, got)
                  if u not in CONTROL_ACCOUNTS and reached(g) and not should_reach(u, t) and g.startswith("open:")]
        if leaked:   # 漏过时马上抓 pf 连接表：看是不是撞上了别人（比如前面 root 对照组）留下的连接记录
            report.setdefault("pf_on_leak", {})[u] = pf_snapshot([int(p) for p in leaked])
        for t, g, c in zip(ts, got, ctrl):
            v = "对照" if u in CONTROL_ACCOUNTS else judge(u, t, g, c)
            rows.append({**t, "got": g, "root": c, "verdict": v})
            print(f"  [{v}] {t['what']} {t['host']}:{t['port']} → {g}（root：{c}）")
            if v in ("没挡住", "该通不通"):
                worst = 1
            elif v == "没法判断" and worst == 0:
                worst = 2
        report["accounts"][u] = rows
    os.makedirs(OUT_DIR, mode=0o755, exist_ok=True)
    path = os.path.join(OUT_DIR, time.strftime("%Y%m%d-%H%M%S") + ".json")
    with open(path, "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    os.chmod(path, 0o644)
    print({0: "全部符合", 1: "有该挡没挡住或该通不通", 2: "有项目没法判断"}[worst] + f"；记录在 {path}")
    print("没试的剩余风险：ICMP（ping）、系统解析器代发的 DNS 查询，pf 按账号认不出")
    return worst


if __name__ == "__main__":
    sys.exit(main())
