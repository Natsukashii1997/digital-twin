#!/usr/bin/env python3
"""0A 隔离门反向测试（digital-twin#190，方案 docs/agent-autoloop-底座-方案.md 第 5.2 节、第 9 节 0A）。

在「工人账号」dt-worker 里运行，逐项尝试越权，**每一项都应该被挡住**：
  读主账号文件、改主账号的 rules.json、用接单密钥看全局 / 回答卡片 / 触发规则 / 冒充来源、
  拿到 GitHub 凭据、sudo、连主账号的其他本机服务、经代理绕回本机、往主账号会执行的目录里写东西。

只读：除了尝试本身（都会被拒绝），不写主账号任何东西；结果写到共享目录 results/ 和标准输出。
用法：python3 /Users/Shared/dt-autoloop/autoloop_isolation_check.py [--main-home /Users/nat]
退出码：全部挡住 0；有没挡住的 1；跑错 2。
"""
import argparse
import datetime as dt
import getpass
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HUB = "http://127.0.0.1:3350"
HUB_PORT = 3350
PROXY_PORTS = {8086, 1080}  # 上网要用的本机代理，允许连
RESULTS = Path("/Users/Shared/dt-autoloop/results")
MAIN_PATH_DIRS = ["/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/bin", "/bin"]

BLOCKED, REACHABLE, ERROR, SKIPPED = "挡住了", "没挡住", "出错", "跳过"


def item(group, name, result, detail=""):
    return {"group": group, "name": name, "result": result, "detail": detail}


# ---------- 文件 ----------

def try_read(path):
    p = Path(path)
    try:
        if p.is_dir():
            os.listdir(p)
        else:
            with open(p, "rb") as f:
                f.read(1)
        return REACHABLE, "读到了"
    except PermissionError:
        return BLOCKED, "没有权限"
    except FileNotFoundError:
        return BLOCKED, "看不到（不存在或上级目录不可进）"
    except OSError as e:
        return ERROR, f"{type(e).__name__}: {e}"


def try_append(path):
    try:
        with open(path, "a"):
            pass
        return REACHABLE, "能写"
    except (PermissionError, FileNotFoundError):
        return BLOCKED, "不能写"
    except OSError as e:
        return ERROR, f"{type(e).__name__}: {e}"


def file_checks(main_home):
    h = Path(main_home)
    out = []
    for name, path in [
        ("列出主账号家目录", h),
        ("读中台本人密钥 config.json", h / ".agent-autoloop/config.json"),
        ("读 Hermes 的 .env", h / ".hermes/.env"),
        ("读主账号钥匙串目录", h / "Library/Keychains"),
        ("读主账号「文稿」", h / "Documents"),
        ("读数字分身证据目录", h / "Library/Application Support/DigitalTwin"),
    ]:
        r, d = try_read(path)
        out.append(item("文件", name, r, d))
    r, d = try_append(h / ".agent-autoloop/rules.json")
    out.append(item("文件", "改主账号 rules.json", r, d))
    return out


# ---------- 中台接口（用 dt-worker 自己的接单密钥） ----------

def classify_http(status):
    if status in (401, 403):
        return BLOCKED
    if 200 <= status < 300:
        return REACHABLE
    return ERROR


def http(method, path, key, body=None, opener=None):
    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(HUB + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with opener.open(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def read_worker_key(home):
    cfg = Path(home) / ".agent-autoloop/config.json"
    try:
        return json.loads(cfg.read_text())["worker"]["key"]
    except (OSError, KeyError, ValueError):
        return None


def hub_checks(key, call=http):
    if not key:
        return [item("中台", "接单密钥", ERROR, "这个账号还没接到中台（~/.agent-autoloop/config.json 里没有接单密钥）")]
    rule = urllib.parse.quote("试一下", safe="")
    out = []
    for name, method, path, body in [
        ("用接单密钥看全局状态", "GET", "/v1/state", None),
        ("用接单密钥回答卡片", "POST", "/v1/cards/1/answer", {"choice": 0}),
        ("用接单密钥结掉卡片", "POST", "/v1/cards/1/resolve", {"reason": "越权测试"}),
        ("用接单密钥触发规则", "POST", f"/v1/rules/{rule}/run", {}),
        ("用接单密钥冒充来源发事件", "POST", "/v1/events", {"id": "isolation-test", "type": "isolation.test"}),
    ]:
        try:
            status = call(method, path, key, body)
            out.append(item("中台", name, classify_http(status), f"HTTP {status}"))
        except OSError as e:
            out.append(item("中台", name, ERROR, f"{type(e).__name__}: {e}"))
    return out


# ---------- 凭据与提权 ----------

def run(cmd, env=None, timeout=30, limit=300):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        text = (r.stdout + r.stderr).strip()
        return r.returncode, text if limit is None else text[:limit]
    except FileNotFoundError:
        return None, "没有这个命令"
    except PermissionError:
        return None, "没有权限运行这个命令"
    except subprocess.TimeoutExpired:
        return None, "超时"


def credential_checks(private_repo):
    out = []
    code, text = run(["sudo", "-n", "true"])
    out.append(item("提权", "sudo 不输密码", REACHABLE if code == 0 else BLOCKED, text or f"退出码 {code}"))
    code, text = run(["/opt/homebrew/bin/gh", "auth", "status"])
    out.append(item("GitHub", "gh 已登录", REACHABLE if code == 0 else BLOCKED, text.splitlines()[0] if text else ""))
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/usr/bin/false")
    code, text = run(["git", "ls-remote", "--heads", private_repo], env=env, timeout=60)
    out.append(item("GitHub", "读私有仓库（有凭据才读得到）", REACHABLE if code == 0 else BLOCKED,
                    "读到了" if code == 0 else (text.splitlines()[-1] if text else f"退出码 {code}")))
    cred = Path.home() / ".git-credentials"
    out.append(item("GitHub", "本账号存着 git 凭据文件", REACHABLE if cred.exists() else BLOCKED, str(cred)))
    return out


# ---------- 本机服务与代理 ----------

PORT_RE = re.compile(r"[.:](\d+)$")


def listening_ports(netstat_text):
    ports = set()
    for line in netstat_text.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0].startswith("tcp") and parts[5] == "LISTEN":
            m = PORT_RE.search(parts[3])
            if m:
                ports.add(int(m.group(1)))
    return sorted(ports)


def can_connect(host, port, timeout=1.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def proxy_connect(proxy_port, host, port, timeout=3.0):
    """经本机代理 CONNECT 到 host:port；代理回 200 就是能绕回去。"""
    try:
        with socket.create_connection(("127.0.0.1", proxy_port), timeout=timeout) as s:
            s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
            line = s.recv(200).split(b"\r\n", 1)[0].decode(errors="replace")
            return line.split()[1:2] == ["200"], line
    except OSError as e:
        return False, f"{type(e).__name__}"


def service_checks(ports, lan_ip, connect=can_connect, via_proxy=proxy_connect):
    out = []
    targets = [p for p in ports if p != HUB_PORT and p not in PROXY_PORTS]
    if not ports:
        # 一个监听端口都没读到，说明读端口清单失败了；不能当成「都连不上」
        return [item("本机服务", "读本机监听端口", ERROR, "没读到任何监听端口")]
    for host in ["127.0.0.1"] + ([lan_ip] if lan_ip else []):
        open_ports = [p for p in targets if connect(host, p)]
        out.append(item("本机服务", f"直连 {host} 上的其他服务",
                        REACHABLE if open_ports else BLOCKED,
                        f"连得上的端口：{open_ports}" if open_ports else f"试了 {len(targets)} 个端口都连不上"))
        proxied = [p for p in targets if via_proxy(8086, host, p)[0]]
        out.append(item("本机服务", f"经代理 8086 绕回 {host}",
                        REACHABLE if proxied else BLOCKED,
                        f"绕得回去的端口：{proxied}" if proxied else "代理没放行"))
    return out


def lan_address():
    code, text = run(["ipconfig", "getifaddr", "en0"])
    return text.strip() if code == 0 and text.strip() else None


# ---------- 让主账号执行自己的东西 ----------

def exec_checks(dirs=MAIN_PATH_DIRS, shared=Path("/Users/Shared/dt-autoloop")):
    out = []
    writable = [d for d in dirs if os.path.isdir(d) and os.access(d, os.W_OK)]
    out.append(item("间接执行", "往主账号的命令目录里放程序", REACHABLE if writable else BLOCKED,
                    f"能写：{writable}" if writable else "都不能写"))
    shared_w = [str(p) for p in [shared, shared / "agent-autoloop.git", shared / "install.sh",
                                 shared / Path(__file__).name] if p.exists() and os.access(p, os.W_OK)]
    out.append(item("间接执行", "改主账号提供的引擎副本、安装脚本、本测试脚本", REACHABLE if shared_w else BLOCKED,
                    f"能改：{shared_w}" if shared_w else "都不能改"))
    return out


def summarize(items):
    bad = [i for i in items if i["result"] == REACHABLE]
    err = [i for i in items if i["result"] == ERROR]
    return {"total": len(items), "not_blocked": len(bad), "errors": len(err), "passed": not bad and not err}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--main-home", default="/Users/nat")
    ap.add_argument("--private-repo", default="https://github.com/Natsukashii1997/digital-twin.git")
    ap.add_argument("--allow-main", action="store_true", help="在主账号里试跑（结果大多会是没挡住）")
    a = ap.parse_args(argv)
    me = getpass.getuser()
    if Path.home() == Path(a.main_home) and not a.allow_main:
        print("现在是主账号。请在工人账号里运行。", file=sys.stderr)
        return 2
    items = []
    items += file_checks(a.main_home)
    items += hub_checks(read_worker_key(Path.home()))
    items += credential_checks(a.private_repo)
    code, netstat = run(["netstat", "-an", "-p", "tcp"], limit=None)
    items += service_checks(listening_ports(netstat if code == 0 else ""), lan_address())
    items += exec_checks()
    report = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "user": me,
              "summary": summarize(items), "items": items}
    for i in items:
        mark = {BLOCKED: "✅", REACHABLE: "❌", ERROR: "⚠️", SKIPPED: "·"}[i["result"]]
        print(f"{mark} [{i['group']}] {i['name']}：{i['result']}（{i['detail']}）")
    s = report["summary"]
    print(f"\n共 {s['total']} 项，没挡住 {s['not_blocked']} 项，出错 {s['errors']} 项。"
          + ("全部挡住。" if s["passed"] else "没有全部挡住，回到 Claude 会话说一声。"))
    try:
        RESULTS.mkdir(parents=True, exist_ok=True)
        f = RESULTS / f"isolation-{me}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        f.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果已存到 {f}")
    except OSError as e:
        print(f"结果没存下来：{e}", file=sys.stderr)
    return 0 if s["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
