#!/usr/bin/env python3
"""秘书窄窗口（ADR-015 批 0E-2）：dt-gate 里的本机接口 127.0.0.1:3353，别的账号只能经它交三件事给控制面。

控制面（凭证库、秘书账本、中台本人密钥）都在 dt-gate 里，主账号（0E-5 之前的秘书）碰不到。秘书侧要交的东西只有：
  POST /v1/secretary/proposal      秘书巡视的一份提议（JSON，最多 16 KB），等于在 dt-gate 里跑 secretary_gate.py submit --file
  POST /v1/secretary/off           关秘书（转排空），等于 secretary.py off --source 秘书窄窗口；关是安全方向，谁都可以
  POST /v1/tasks/<单号>/revoke     撤销一张单的凭证，等于 autoloop_grants.py revoke；撤销只收紧，谁都可以
     （body 可带 {"reason": "…"}，200 字内）
都要 Authorization: Bearer <令牌>（DigitalTwin/autoloop/intake.token，600；dt_gate_hub.py windows 给主账号一份）。
返回 {"code": 工具的退出码, "result": 工具最后一行 JSON}；工具本身的判定（准入、额度、交常浩）不在这里重做。
不收别的：没有派单提议（要门卫发确认卡，0E-3）、没有回答卡片、没有开秘书，也没有任何读接口（读用只读窗口 3352）。
提议同一时间只办一件（排队），每件最多 10 分钟；关秘书、撤销单另排一队，不等提议（停是该最快生效的方向）。dt-worker 的门卫规则连不到这里；dt-secretary 只准连 3352、3353、8090。

  gate_intake.py serve [--port 3353]
环境变量：DT_INTAKE_TOKEN_FILE（默认 DigitalTwin/autoloop/intake.token）、DT_INTAKE_INBOX（提议暂存，默认 DigitalTwin/secretary/intake）。
"""
import argparse
import hmac
import http.server
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPPORT = Path("~/Library/Application Support/DigitalTwin").expanduser()
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
TASK_ID = re.compile(r"^t-\d{8}-[0-9a-f]{6}$")
MAX_BODY = 16 * 1024                   # 同 secretary_gate.MAX_PROPOSAL
SOURCE = "秘书窄窗口"
TIMEOUT = 600


class Refused(Exception):
    pass


def token_file():
    return Path(os.environ.get("DT_INTAKE_TOKEN_FILE") or SUPPORT / "autoloop/intake.token").expanduser()


def inbox():
    return Path(os.environ.get("DT_INTAKE_INBOX") or SUPPORT / "secretary/intake").expanduser()


def load_token():
    f = token_file()
    st = os.lstat(f)
    if not os.path.isfile(f) or os.path.islink(f) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refused(f"令牌文件必须是本账号的 600 普通文件：{f}")
    token = f.read_text(encoding="utf-8").strip()
    if not TOKEN_RE.match(token):
        raise Refused(f"令牌文件内容不对：{f}")
    return token


def run_tool(script, args):
    """在本账号里跑一个控制面脚本；返回 (退出码, 最后一行 JSON 或 None)。"""
    try:
        r = subprocess.run([sys.executable, "-B", "-E", "-s", str(HERE / script), *args], capture_output=True,
                           text=True, timeout=TIMEOUT, cwd=str(HERE))
    except subprocess.TimeoutExpired:
        return None, None
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    try:
        out = json.loads(lines[-1]) if lines else None
    except ValueError:
        out = None
    return r.returncode, out


def submit(body, tool=run_tool):
    try:
        p = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("提议不是 JSON")
    if not isinstance(p, dict):
        raise Refused("提议要是 JSON 对象")
    d = inbox()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    f = d / f"p-{secrets.token_hex(8)}.json"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(body)
    try:
        return tool("secretary_gate.py", ["submit", "--file", str(f)])
    finally:
        try:
            f.unlink()
        except OSError:
            pass


def reason_of(body):
    if not body.strip():
        return ""
    try:
        r = json.loads(body.decode("utf-8")).get("reason", "")
    except (UnicodeDecodeError, ValueError, AttributeError):
        raise Refused("body 要是 JSON 对象")
    if not isinstance(r, str) or len(r) > 200:
        raise Refused("reason 要是 200 字以内的文字")
    return r.strip()


def make_handler(token, tool=run_tool):
    proposals, stops = threading.Lock(), threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def _send(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):  # noqa: N802
            given = self.headers.get("Authorization", "")
            if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
                return self._send(401, {"error": "令牌不对"})
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._send(411, {"error": "要带 Content-Length"})
            if n < 0 or n > MAX_BODY:
                return self._send(413, {"error": f"最多 {MAX_BODY} 字节"})
            body = self.rfile.read(n)
            task = re.fullmatch(r"/v1/tasks/([^/]+)/revoke", self.path)
            try:
                if self.path == "/v1/secretary/proposal":
                    with proposals:
                        code, out = submit(body, tool)
                elif self.path == "/v1/secretary/off":
                    reason_of(body)
                    with stops:
                        code, out = tool("secretary.py", ["off", "--source", SOURCE])
                elif task and TASK_ID.match(task.group(1)):
                    r = reason_of(body)
                    with stops:
                        code, out = tool("autoloop_grants.py", ["revoke", "--task", task.group(1), "--reason",
                                                                 f"{SOURCE}：{r}" if r else SOURCE])
                else:
                    return self._send(404, {"error": "只有 /v1/secretary/proposal、/v1/secretary/off、/v1/tasks/<单号>/revoke"})
            except Refused as e:
                return self._send(400, {"error": str(e)})
            if code is None:
                return self._send(504, {"error": "控制面超时，结果不明"})
            return self._send(200, {"code": code, "result": out})

        def _refuse(self):
            self._send(405, {"error": "只收 POST"})

        do_GET = do_PUT = do_DELETE = do_PATCH = _refuse  # noqa: N815

        def log_message(self, *args):
            pass

    return Handler


def serve(port, tool=run_tool):
    return http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(load_token(), tool))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve").add_argument("--port", type=int, default=3353)
    a = ap.parse_args(argv)
    try:
        srv = serve(a.port)
    except (Refused, OSError) as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
