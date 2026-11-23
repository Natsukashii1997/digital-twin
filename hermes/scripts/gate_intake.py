#!/usr/bin/env python3
"""秘书窄窗口（ADR-015 批 0E-2；0E-3b 加派单提议）：dt-gate 里的本机接口 127.0.0.1:3353，别的账号只能经它交四件事给控制面。

控制面（凭证库、秘书账本、中台本人密钥）都在 dt-gate 里，主账号（0E-5 之前的秘书）碰不到。秘书侧要交的东西只有：
  POST /v1/secretary/proposal      秘书巡视的一份提议（JSON，最多 16 KB），等于在 dt-gate 里跑 secretary_gate.py submit --file
  POST /v1/secretary/off           关秘书（转排空），等于 secretary.py off --source 秘书窄窗口；关是安全方向，谁都可以
  POST /v1/tasks/<单号>/revoke     撤销一张单的凭证，等于 autoloop_grants.py revoke；撤销只收紧，谁都可以
     （body 可带 {"reason": "…"}，200 字内）
  POST /v1/dispatch/proposal       写入派单提议 {"repo", "executor", "scope", "hours"?}（0E-3b），等于在 dt-gate 里跑
     autoloop_grants.py propose --scope-file …：只登记，确认卡由门卫发到常浩私聊，他点「批准派单」才签凭证。递提议不算授权
     （ADR-015 §3.3：私聊打字可以触发提议、不能完成批准），所以和秘书提议一样只要令牌
都要 Authorization: Bearer <令牌>（DigitalTwin/autoloop/intake.token，600；dt_gate_hub.py windows 给主账号一份）。
返回 {"code": 工具的退出码, "result": 工具最后一行 JSON}；工具本身的判定（准入、额度、交常浩）不在这里重做。
不收别的：没有批准派单（只认门卫卡上的点击）、没有回答卡片、没有开秘书，也没有任何读接口（读用只读窗口 3352）。
提议同一时间只办一件（排队），每件最多 10 分钟；关秘书、撤销单另排一队，不等提议（停是该最快生效的方向）。dt-worker 的门卫规则连不到这里；dt-secretary 只准连 3352、3353、8090。

  gate_intake.py serve [--port 3353]
  gate_intake.py dispatch-propose --repo R --executor claude|codex --scope-file F [--hours 12]
      客户端（在主账号 / 秘书账号里跑，猛鬼兽派单技能用）：读本账号的令牌副本，POST 到窗口，原样打印窗口回的 JSON；
      退出码：0 网关收下了，1 网关拒了（原因在 result.why）或请求不对，2 连不上窗口或结果不明。
环境变量：DT_INTAKE_TOKEN_FILE（默认 DigitalTwin/autoloop/intake.token）、DT_INTAKE_INBOX（提议暂存，默认 DigitalTwin/secretary/intake）、
DT_INTAKE_URL（客户端用，默认 http://127.0.0.1:3353）。
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
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUPPORT = Path("~/Library/Application Support/DigitalTwin").expanduser()
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
TASK_ID = re.compile(r"^t-\d{8}-[0-9a-f]{6}$")
MAX_BODY = 16 * 1024                   # 同 secretary_gate.MAX_PROPOSAL
SOURCE = "秘书窄窗口"
TIMEOUT = 600
REPO_RE = re.compile(r"^[\w.-]{1,100}$")          # 和 autoloop_grants.REPO_NAME 一样；网关自己还会再核
EXECUTORS = ("claude", "codex")
MAX_SCOPE = 3000                                  # 同 autoloop_grants.MAX_SCOPE


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
    """在本账号里跑一个控制面脚本；返回 (退出码, 它打印的 JSON 或 None)。"""
    try:
        r = subprocess.run([sys.executable, "-B", "-E", "-s", str(HERE / script), *args], capture_output=True,
                           text=True, timeout=TIMEOUT, cwd=str(HERE))
    except subprocess.TimeoutExpired:
        return None, None
    return r.returncode, last_json(r.stdout)


def last_json(stdout):
    """工具打印的结果：整段是一个 JSON（autoloop_grants 按缩进打印多行）就用它，否则取最后一行。都不是返回 None。"""
    text = (stdout or "").strip()
    try:
        return json.loads(text) if text else None
    except ValueError:
        pass
    lines = [ln for ln in text.splitlines() if ln.strip()]
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return None


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


def dispatch(body, tool=run_tool):
    """派单提议：字段核过形状后把五要素写成私有临时文件，交网关 propose（名册、中台规则、恢复闸由网关核）。"""
    try:
        p = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Refused("派单提议不是 JSON")
    if not isinstance(p, dict) or set(p) - {"repo", "executor", "scope", "hours"}:
        raise Refused("派单提议只收 repo、executor、scope、hours")
    repo, executor, scope, hours = p.get("repo"), p.get("executor"), p.get("scope"), p.get("hours", 12)
    if not isinstance(repo, str) or not REPO_RE.match(repo):
        raise Refused("repo 要是名册里的仓库短名")
    if executor not in EXECUTORS:
        raise Refused("executor 只能是 claude 或 codex")
    if not isinstance(scope, str) or not scope.strip() or len(scope) > MAX_SCOPE:
        raise Refused(f"scope（五要素）不能为空，最多 {MAX_SCOPE} 字")
    if type(hours) is not int:
        raise Refused("hours 要是整数")
    d = inbox()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    f = d / f"d-{secrets.token_hex(8)}.md"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(scope)
    try:
        return tool("autoloop_grants.py", ["propose", "--repo", repo, "--executor", executor, "--scope-file", str(f),
                                           "--hours", str(hours)])
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
                elif self.path == "/v1/dispatch/proposal":
                    with proposals:
                        code, out = dispatch(body, tool)
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
                    return self._send(404, {"error": "只有 /v1/secretary/proposal、/v1/secretary/off、/v1/tasks/<单号>/revoke、"
                                                     "/v1/dispatch/proposal"})
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


def intake_url():
    return (os.environ.get("DT_INTAKE_URL") or "http://127.0.0.1:3353").rstrip("/")


def propose_dispatch(repo, executor, scope_text, hours=12, timeout=TIMEOUT + 20):
    """客户端：把派单提议交给窗口。返回 (退出码, 要打印的 JSON)。不走代理。"""
    try:
        token = load_token()
    except (Refused, OSError) as e:
        return 2, {"ok": False, "why": f"读不到窗口令牌：{e}"}
    body = json.dumps({"repo": repo, "executor": executor, "scope": scope_text, "hours": hours}, ensure_ascii=False).encode()
    req = urllib.request.Request(intake_url() + "/v1/dispatch/proposal", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            why = json.loads(e.read() or b"{}").get("error")
        except ValueError:
            why = None
        return (2 if e.code >= 500 else 1), {"ok": False, "why": f"窗口拒收（HTTP {e.code}）：{why or '没有说明'}"}
    except (OSError, ValueError) as e:
        return 2, {"ok": False, "why": f"连不上 dt-gate 的窗口或回应不对（{type(e).__name__}），结果不明"}
    result = out.get("result") if isinstance(out, dict) else None
    if out.get("code") == 0 and isinstance(result, dict) and result.get("code"):
        return 0, result
    return 1, result if isinstance(result, dict) else {"ok": False, "why": f"网关没给结果（退出码 {out.get('code')}）"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve").add_argument("--port", type=int, default=3353)
    d = sub.add_parser("dispatch-propose")
    d.add_argument("--repo", required=True)
    d.add_argument("--executor", required=True)
    d.add_argument("--scope-file", required=True)
    d.add_argument("--hours", type=int, default=12)
    a = ap.parse_args(argv)
    if a.cmd == "dispatch-propose":
        try:
            scope = Path(a.scope_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(json.dumps({"ok": False, "why": f"读不了五要素文件：{type(e).__name__}"}, ensure_ascii=False))
            return 1
        code, out = propose_dispatch(a.repo, a.executor, scope, a.hours)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return code
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
