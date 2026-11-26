#!/usr/bin/env python3
"""中台只读窗口（批 0B-2，#195；规格 docs/秘书代批-规格.md §5「秘书读卡片不直接拿本人密钥」、§9）。

秘书（0B-4 的巡视脚本）和驿窗共用：用本人密钥读中台 /v1/state，只按白名单交出看卡片、看进度要的字段。
不交出：本人密钥、卡片的带权限链接（link，谁拿到谁能答）、通道消息号（ref）、本人写的回答原文（answer）和结案说明
（result）、提问留给下一轮的说明（about）、会话号、用量、回执摘要、事件详情。
没有回答、结案、停止、暂停、补跑等任何写操作：snapshot() 只读，serve 只认 GET /v1/snapshot。

这是展示和巡视用的窗口，**不能证明一张卡属于哪一单**：中台状态里卡片不带创建它的轮次，轮次的事件也不带事件号，同一规则、
同一 lane 可以跨多轮、多代。规格 §5 的可信绑定（卡片 → 轮次 → 事件号 → 凭证、代次）由 0B-4 的网关在送达时补记；
来源追溯不了的卡片一律交常浩。

  autoloop_readonly.py show                         打印一次快照（JSON）
  autoloop_readonly.py serve [--port 3352]          给驿窗的本机只读接口（只听 127.0.0.1；要带 Bearer 令牌，令牌在
                                                    DigitalTwin/autoloop/readonly.token，600，第一次运行时生成）
搬家（0E-2）以后中台和本人密钥都在 dt-gate 里，show 只在 dt-gate 里跑得通；主账号（猛鬼兽的 autoloop 技能）用下面两个只读客户端：
  autoloop_readonly.py fetch                        经只读窗口取一次快照（和 show 同样的 JSON）：GET DT_READONLY_URL
                                                    （默认 http://127.0.0.1:3352）/v1/snapshot，令牌读本账号的 readonly.token 副本
                                                    （dt_gate_hub.py windows 给的；这里不生成）；取不到打印 {"ok": false, "why"}、退出码 1
  autoloop_readonly.py grant [--task 单号]          读 dt-gate 写的凭证镜像（DT_GRANTS_STATUS，默认
                                                    /Users/Shared/dt-gate/grants/status.json，谁都能读、只有 dt-gate 能写），列出每张单的
                                                    仓库、执行方、状态、代次、到期，另按现在的时间判 usable / why_not（和网关 check 同一套判定）。
                                                    镜像在凭证库变化提交后才写，写不成时会比凭证库晚一步；不含范围原文、消息号、冻结码、
                                                    派单事件送没送到。主账号里搬家前留下的 grants.db 是旧库，不读它
serve 现在不装成常驻服务；要装（launchd）时单独问常浩。工人账号 dt-worker 的门卫规则只放行 3350、8090，连不到这里。
环境变量：AGENT_AUTOLOOP_HOME（同 autoloop_config）、DT_READONLY_TOKEN_FILE。
"""
import argparse
import hmac
import http.server
import json
import os
import re
import secrets
import sys
import urllib.request
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
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")


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
    token = f.read_text(encoding="utf-8").strip()
    if not TOKEN_RE.match(token):         # 空文件、只有空白、被截断的都不能拿来开服务
        raise C.Rejected(f"只读令牌文件内容不对（应为至少 32 位的 URL 安全字符）：{f}；删掉它重新生成")
    return token


def window_url():
    return (os.environ.get("DT_READONLY_URL") or "http://127.0.0.1:3352").rstrip("/")


def fetch(url=None, timeout=30):
    """经 dt-gate 的只读窗口取快照（不走代理）。令牌读本账号的副本，不生成。连不上、HTTP 出错、回应不对都抛 OSError。"""
    token = token_file().read_text(encoding="utf-8").strip()
    if not TOKEN_RE.match(token):
        raise OSError(f"只读令牌文件内容不对：{token_file()}")
    req = urllib.request.Request((url or window_url()).rstrip("/") + PATH, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
    except ValueError as e:
        raise OSError(f"只读窗口的回应不是 JSON：{type(e).__name__}")
    if not isinstance(out, dict):
        raise OSError("只读窗口的回应不是对象")
    return out


def grants(task_id=None, now=None):
    """读凭证镜像（工人读的那份），按网关 check_row 的判定补 usable / why_not。读不到、格式不对抛 C.Rejected。"""
    f = G.status_path()
    try:
        doc = json.loads(f.read_text(encoding="utf-8"))
        tasks, updated = doc["tasks"], doc.get("updated_at")
        if not isinstance(tasks, dict):
            raise TypeError("tasks")
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise C.Rejected(f"读不到凭证镜像 {f}（{type(e).__name__}）")
    if task_id is not None and task_id not in tasks:
        raise C.Rejected(f"凭证镜像里没有 {task_id} 这张单（镜像更新于 {updated}）")
    now = now or G.now_tz()
    out = []
    for tid in ([task_id] if task_id is not None else sorted(tasks)):
        t = tasks[tid]
        try:
            row = {**t, "actions": json.dumps(t["actions"])}
            why = G.check_row(row, None, t["generation"], now, t.get("voided_generations") or [])
        except (KeyError, TypeError, ValueError) as e:
            raise C.Rejected(f"凭证镜像里 {tid} 这一条格式不对（{type(e).__name__}）")
        out.append({"task_id": tid, **{k: t.get(k) for k in ("repo", "executor", "rule", "status", "generation",
                                                             "valid_until", "actions")},
                    "usable": why is None, "why_not": why, "mirror_updated_at": updated})
    return out


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
    sub.add_parser("fetch")
    sub.add_parser("grant").add_argument("--task")
    sub.add_parser("serve").add_argument("--port", type=int, default=3352)
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        try:
            out = fetch()
        except OSError as e:          # HTTPError、URLError 都是 OSError
            print(json.dumps({"ok": False, "why": f"连不上 dt-gate 的只读窗口（{e}）"}, ensure_ascii=False))
            return 1
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 0
    try:
        if a.cmd == "show":
            print(json.dumps(read(), ensure_ascii=False, indent=1))
            return 0
        if a.cmd == "grant":
            print(json.dumps(grants(a.task), ensure_ascii=False, indent=1))
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
