#!/usr/bin/env python3
"""工人账号的过滤代理（digital-twin#190，0A 隔离门）。

dt-worker 上网只能走这里（门卫规则 dt_worker_pf.sh 只准它连中台 3350 和本代理 8090）。
本代理只接 CONNECT 到 443 端口，目标解析出来只要有一个不是公网地址（本机、局域网、链路本地、保留段）就拒绝，
放行的再转给本机加速器的代理（127.0.0.1:8086）。普通 HTTP 请求一律拒绝，免得同一条连接里换目标绕过检查。
以主账号身份常驻（launchd 标签 cn.digital-twin.egress），工人改不了、关不掉。

已知的剩余风险：域名先解析成公网地址、加速器直连时再解析成本机地址（DNS 重绑定），本代理挡不住；
加速器走日本线路时在远端解析，不受影响。
用法：/usr/bin/python3 dt_worker_egress.py [--port 8090] [--upstream-port 8086]
"""
import argparse
import asyncio
import datetime as dt
import ipaddress
import re
import socket
import sys
from pathlib import Path

LISTEN_HOST = "127.0.0.1"
PORT = 8090
UPSTREAM_PORT = 8086
ALLOWED_PORTS = {443}
MAX_HEAD = 8192
HEAD_TIMEOUT = 15
MAX_CONN = 128
LOG = Path.home() / "Library/Application Support/DigitalTwin/evidence/egress/egress.log"
LOG_MAX = 5 * 1024 * 1024
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

DENY = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
BAD = b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
BUSY = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def blocked_ip(text):
    """不是公网地址就拦（本机、局域网、链路本地、运营商共享段、保留段、组播）。"""
    a = ipaddress.ip_address(text.split("%", 1)[0])
    if a.version == 6 and a.ipv4_mapped:
        a = a.ipv4_mapped
    return not a.is_global or a.is_multicast


def parse_connect(head):
    """CONNECT 请求头 → (host, port)；不是 CONNECT 抛 PermissionError，格式不对抛 ValueError。"""
    first = head.split(b"\r\n", 1)[0].decode("ascii", "replace").split()
    if len(first) != 3 or not first[2].startswith("HTTP/1."):
        raise ValueError("请求行格式不对")
    if first[0] != "CONNECT":
        raise PermissionError("只接受 CONNECT")
    target = first[1]
    if target.startswith("["):
        host, sep, rest = target[1:].partition("]")
        if not sep or not rest.startswith(":"):
            raise ValueError("地址格式不对")
        ipaddress.IPv6Address(host)
        port = rest[1:]
    else:
        host, sep, port = target.rpartition(":")
        if not sep or not HOST_RE.match(host):
            raise ValueError("地址格式不对")
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError("端口不对")
    return host.lower(), int(port)


async def check_target(host, port, resolve):
    """放行返回 None，拒绝返回原因。resolve(host, port) → 地址列表。"""
    if port not in ALLOWED_PORTS:
        return f"只放行 {sorted(ALLOWED_PORTS)} 端口"
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
        return "本机或局域网名字"
    try:
        ips = [str(ipaddress.ip_address(host))]
    except ValueError:
        try:
            ips = await resolve(host, port)
        except OSError as e:
            return f"解析失败（{type(e).__name__}）"
    if not ips:
        return "解析不到地址"
    bad = [ip for ip in ips if blocked_ip(ip)]
    return f"指向非公网地址 {bad[0]}" if bad else None


async def system_resolve(host, port):
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({i[4][0] for i in infos})


def log(line, path=None):
    path = path or LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > LOG_MAX:
            path.replace(path.with_suffix(".log.1"))
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


class Egress:
    def __init__(self, upstream=(LISTEN_HOST, UPSTREAM_PORT), resolve=system_resolve, log_path=None):
        self.upstream = upstream
        self.resolve = resolve
        self.log_path = log_path
        self.slots = asyncio.Semaphore(MAX_CONN)

    async def handle(self, reader, writer):
        if self.slots.locked():
            writer.write(BUSY)
            await self._close(writer)
            return
        async with self.slots:
            await self._serve(reader, writer)

    async def _serve(self, reader, writer):
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            await self._close(writer)
            return
        try:
            host, port = parse_connect(head)
        except PermissionError:
            log("拒绝 非 CONNECT 请求", self.log_path)
            writer.write(BAD)
            await self._close(writer)
            return
        except ValueError as e:
            log(f"拒绝 {e}", self.log_path)
            writer.write(DENY)
            await self._close(writer)
            return
        reason = await check_target(host, port, self.resolve)
        if reason:
            log(f"拒绝 {host}:{port} {reason}", self.log_path)
            writer.write(DENY)
            await self._close(writer)
            return
        try:
            up_r, up_w = await asyncio.wait_for(asyncio.open_connection(*self.upstream), HEAD_TIMEOUT)
        except (OSError, asyncio.TimeoutError) as e:
            log(f"失败 {host}:{port} 连不上加速器代理（{type(e).__name__}）", self.log_path)
            writer.write(GATEWAY)
            await self._close(writer)
            return
        log(f"放行 {host}:{port}", self.log_path)
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        up_w.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))

    @staticmethod
    async def _close(writer):
        try:
            await writer.drain()
            writer.close()
        except (ConnectionError, OSError):
            pass


async def serve(port, upstream_port):
    egress = Egress(upstream=(LISTEN_HOST, upstream_port))
    server = await asyncio.start_server(egress.handle, LISTEN_HOST, port, limit=MAX_HEAD)
    log(f"启动 监听 {LISTEN_HOST}:{port}，上游 {LISTEN_HOST}:{upstream_port}")
    async with server:
        await server.serve_forever()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--upstream-port", type=int, default=UPSTREAM_PORT)
    a = ap.parse_args(argv)
    try:
        asyncio.run(serve(a.port, a.upstream_port))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
