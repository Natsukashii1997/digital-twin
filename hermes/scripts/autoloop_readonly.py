#!/usr/bin/env python3
"""中台只读窗口（批 0B-2，#195；规格 docs/秘书代批-规格.md §5「秘书读卡片不直接拿本人密钥」、§9）。

秘书（0B-4 的巡视脚本）和驿窗共用：用本人密钥读中台 /v1/state，只按白名单交出看卡片、看进度要的字段。
不交出：本人密钥、卡片的带权限链接（link，谁拿到谁能答）、通道消息号（ref）、本人写的回答原文（answer）和结案说明
（result）、提问留给下一轮的说明（about）、会话号、用量、回执摘要、事件详情。
没有回答、结案、停止、暂停、补跑等任何写操作：snapshot() 只读，serve 只认 GET /v1/snapshot。

  autoloop_readonly.py show                         打印一次快照（JSON）
  autoloop_readonly.py serve [--port 3352]          给驿窗的本机只读接口（只听 127.0.0.1；要带 Bearer 令牌，令牌在
                                                    DigitalTwin/autoloop/readonly.token，600，第一次运行时生成）
serve 现在不装成常驻服务；要装（launchd）时单独问常浩。工人账号 dt-worker 的门卫规则只放行 3350、8090，连不到这里。
环境变量：AGENT_AUTOLOOP_HOME（同 autoloop_config）、DT_READONLY_TOKEN_FILE。
"""
import argparse
import hmac
import http.server
import json
import os
import secrets
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_config as C  # noqa: E402
import autoloop_grants as G  # noqa: E402

CARD_FIELDS = ("id", "version", "kind", "state", "title", "text", "options", "recommend", "choice", "by", "rule", "lane",
               "createdAt", "decidedAt", "closedAt")
RUN_FIELDS = ("id", "rule", "agent", "state", "attempts", "createdAt", "claimedAt", "finishedAt", "stopping", "manual")
RUN_EVENT_FIELDS = ("source", "type", "subject")
LINE_FIELDS = ("id", "source", "types", "agent")
CHANNEL_FIELDS = ("id", "name", "online")
PATH = "/v1/snapshot"


def _pick(d, fields):
    return {k: d[k] for k in fields if k in d}


def snapshot(s):
    """把 /v1/state 按白名单收窄。新字段默认不出去：上游加了什么，这里不改就看不到。"""
    return {"now": s.get("now"),
            "cards": [_pick(c, CARD_FIELDS) for c in s.get("cards", [])],
            "recentCards": [_pick(c, CARD_FIELDS) for c in s.get("recentCards", [])],
            "runs": [{**_pick(r, RUN_FIELDS), "events": [_pick(e, RUN_EVENT_FIELDS) for e in r.get("events", [])]}
                     for r in s.get("runs", [])],
            "lines": [{**_pick(line, LINE_FIELDS), "paused": bool(line.get("paused"))} for line in s.get("lines", [])],
            "channels": [_pick(c, CHANNEL_FIELDS) for c in s.get("channels", [])]}


def read():
    return snapshot(C.state())


def token_file():
    return Path(os.environ.get("DT_READONLY_TOKEN_FILE") or G.SUPPORT / "autoloop/readonly.token").expanduser()


def load_token():
    f = token_file()
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(f.parent, 0o700)
        try:
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(secrets.token_urlsafe(32) + "\n")
        except FileExistsError:
            pass
    if os.stat(f).st_mode & 0o077:
        raise C.Rejected(f"只读令牌文件权限必须是 600：{f}")
    return f.read_text(encoding="utf-8").strip()


def make_handler(token, fetch):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            if self.path != PATH:
                return self._send(404, {"error": "只有 GET /v1/snapshot"})
            given = self.headers.get("Authorization", "")
            if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
                return self._send(401, {"error": "令牌不对"})
            try:
                return self._send(200, fetch())
            except C.Rejected as e:
                return self._send(502, {"error": str(e)})

        def _refuse(self):
            self._send(405, {"error": "只读"})

        do_POST = do_PUT = do_DELETE = do_PATCH = _refuse  # noqa: N815

        def log_message(self, *args):
            pass

    return Handler


def serve(port, fetch=read):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(load_token(), fetch))
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    sub.add_parser("serve").add_argument("--port", type=int, default=3352)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "show":
            print(json.dumps(read(), ensure_ascii=False, indent=1))
            return 0
        srv = serve(a.port)
    except C.Rejected as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
