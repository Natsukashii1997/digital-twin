"""隔离自测：python3 -B -m unittest discover -s hermes/scripts/tests -v。

ops/dt_worker_egress.py（工人账号的过滤代理）：判定逻辑，加上在临时端口起一个真代理、假上游，走一遍放行和拒绝。
不联网（解析打桩）、不碰加速器、日志写临时目录。
"""
import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[3] / "ops/dt_worker_egress.py"
spec = importlib.util.spec_from_file_location("dt_worker_egress", SCRIPT)
eg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eg)


def fixed(*ips):
    async def resolve(host, port):
        return list(ips)
    return resolve


async def failing(host, port):
    raise OSError("nxdomain")


class BlockedIp(unittest.TestCase):
    def test_private_and_local_are_blocked(self):
        for ip in ["127.0.0.1", "127.8.8.8", "10.1.2.3", "172.16.10.232", "192.168.1.1", "169.254.1.1",
                   "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1"]:
            self.assertTrue(eg.blocked_ip(ip), ip)

    def test_public_is_allowed(self):
        for ip in ["8.8.8.8", "104.18.0.1", "2606:4700::1"]:
            self.assertFalse(eg.blocked_ip(ip), ip)


class ParseConnect(unittest.TestCase):
    def test_host_port(self):
        self.assertEqual(eg.parse_connect(b"CONNECT API.anthropic.com:443 HTTP/1.1\r\nHost: x\r\n\r\n"),
                         ("api.anthropic.com", 443))

    def test_ipv6(self):
        self.assertEqual(eg.parse_connect(b"CONNECT [::1]:443 HTTP/1.1\r\n\r\n"), ("::1", 443))

    def test_plain_http_is_refused(self):
        with self.assertRaises(PermissionError):
            eg.parse_connect(b"GET http://127.0.0.1:5280/ HTTP/1.1\r\n\r\n")

    def test_garbage(self):
        for head in [b"CONNECT x HTTP/1.1\r\n\r\n", b"CONNECT a b:443 HTTP/1.1\r\n\r\n",
                     b"CONNECT -bad-:443 HTTP/1.1\r\n\r\n", b"CONNECT a.com:0 HTTP/1.1\r\n\r\n",
                     b"CONNECT a.com:443 FOO\r\n\r\n"]:
            with self.assertRaises(ValueError, msg=head):
                eg.parse_connect(head)


class CheckTarget(unittest.TestCase):
    def check(self, host, port=443, resolve=fixed("8.8.8.8")):
        return asyncio.run(eg.check_target(host, port, resolve))

    def test_public_domain_passes(self):
        self.assertIsNone(self.check("api.anthropic.com"))

    def test_only_443(self):
        self.assertIn("443", self.check("api.anthropic.com", 80))
        self.assertIn("443", self.check("127.0.0.1", 5280))

    def test_ip_literals(self):
        self.assertIsNotNone(self.check("127.0.0.1"))
        self.assertIsNotNone(self.check("172.16.10.232"))
        self.assertIsNone(self.check("8.8.8.8", resolve=failing))

    def test_local_names(self):
        for host in ["localhost", "a.localhost", "mac.local"]:
            self.assertIsNotNone(self.check(host))

    def test_domain_resolving_to_loopback(self):
        self.assertIn("127.0.0.1", self.check("127.0.0.1.nip.io", resolve=fixed("127.0.0.1")))

    def test_any_private_answer_blocks(self):
        self.assertIsNotNone(self.check("mixed.example", resolve=fixed("8.8.8.8", "10.0.0.1")))

    def test_resolve_failure_blocks(self):
        self.assertIsNotNone(self.check("nope.example", resolve=failing))
        self.assertIsNotNone(self.check("empty.example", resolve=fixed()))


class EndToEnd(unittest.TestCase):
    """真起代理和假上游：假上游收到 CONNECT 就回 200，然后原样回显。"""

    def run_case(self, request, resolve=fixed("8.8.8.8"), payload=b"ping"):
        async def go():
            seen = []

            async def upstream(r, w):
                head = await r.readuntil(b"\r\n\r\n")
                seen.append(head)
                w.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
                await w.drain()
                data = await r.read(100)
                w.write(data)
                await w.drain()
                w.close()

            up = await asyncio.start_server(upstream, "127.0.0.1", 0)
            up_port = up.sockets[0].getsockname()[1]
            with tempfile.TemporaryDirectory() as d:
                egress = eg.Egress(upstream=("127.0.0.1", up_port), resolve=resolve, log_path=Path(d) / "e.log")
                srv = await asyncio.start_server(egress.handle, "127.0.0.1", 0, limit=eg.MAX_HEAD)
                port = srv.sockets[0].getsockname()[1]
                r, w = await asyncio.open_connection("127.0.0.1", port)
                w.write(request + payload)
                await w.drain()
                out = await asyncio.wait_for(r.read(1000), 5)
                rest = await asyncio.wait_for(r.read(1000), 5) if out.endswith(b"\r\n\r\n") and b" 200 " in out else b""
                w.close()
                srv.close()
                up.close()
                lp = Path(d) / "e.log"
                logtext = lp.read_text(encoding="utf-8") if lp.exists() else ""
            return out + rest, seen, logtext

        return asyncio.run(go())

    def test_allowed_goes_through_upstream(self):
        out, seen, logtext = self.run_case(b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertIn(b" 200 ", out)
        self.assertTrue(out.endswith(b"ping"))
        self.assertEqual(seen, [b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com:443\r\n\r\n"])
        self.assertIn("放行 api.anthropic.com:443", logtext)

    def test_loopback_is_refused_and_upstream_untouched(self):
        out, seen, logtext = self.run_case(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
        self.assertTrue(out.startswith(b"HTTP/1.1 403"))
        self.assertEqual(seen, [])
        self.assertIn("拒绝 127.0.0.1:443", logtext)

    def test_plain_http_is_refused(self):
        out, seen, _ = self.run_case(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
        self.assertTrue(out.startswith(b"HTTP/1.1 405"))
        self.assertEqual(seen, [])

    def test_oversized_head_is_dropped(self):
        out, seen, _ = self.run_case(b"CONNECT a.com:443 HTTP/1.1\r\nX: " + b"a" * 10000 + b"\r\n\r\n")
        self.assertEqual(out, b"")
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
