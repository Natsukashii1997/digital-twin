#!/usr/bin/env python3
"""秘书窄窗口（ADR-015 批 0E-2；0E-3b 加派单提议；#380 加冻结、收掉、补发）：dt-gate 里的本机接口 127.0.0.1:3353，别的账号只能经它
交下面这几件事给控制面。

控制面（凭证库、秘书账本、中台本人密钥）都在 dt-gate 里，主账号（0E-5 之前的秘书）碰不到。秘书侧要交的东西只有：
  POST /v1/secretary/proposal      秘书巡视的一份提议（JSON，最多 16 KB），等于在 dt-gate 里跑 secretary_gate.py submit --file
  POST /v1/secretary/off           关秘书（转排空），等于 secretary.py off --source 秘书窄窗口；关是安全方向，谁都可以
  POST /v1/tasks/<单号>/revoke     撤销一张单的凭证，等于 autoloop_grants.py revoke；撤销只收紧，谁都可以
     （body 可带 {"reason": "…"}，200 字内）
  POST /v1/tasks/<单号>/freeze     冻结一张单，等于 autoloop_grants.py freeze（body 同上，没写原因记「秘书窄窗口」）；收紧，谁都可以。
     结果里的冻结码会回到调用方（派单技能要把它转告常浩）
  POST /v1/tasks/<单号>/complete   收掉一张单，等于 autoloop_grants.py complete；收紧，谁都可以
  POST /v1/dispatch/send-pending   补发记了账、还没确认送到中台的派单事件，等于 autoloop_grants.py send-pending：只补发已经签过、
     代次仍有效的事件，不新增授权（恢复闸、规则核对照网关原样做）
     续接（resume-dispatch，会让工人再跑一轮）不开窗口，只在电脑上做（常浩 2026-11-24 选甲，#380）
  POST /v1/dispatch/proposal       写入派单提议 {"repo", "executor", "scope", "request_id", "hours"?}（0E-3b），等于在 dt-gate 里跑
     autoloop_grants.py propose --scope-file … --request-id …：只登记（同一个请求号重交返回原提议；待批张数、每小时张数有上限，
     #369 P2-02），确认卡由门卫发到常浩私聊，他点「批准派单」才签凭证。递提议不算授权
     （ADR-015 §3.3：私聊打字可以触发提议、不能完成批准），所以和秘书提议一样只要令牌
都要 Authorization: Bearer <令牌>（DigitalTwin/autoloop/intake.token，600；dt_gate_hub.py windows 给主账号一份）。
返回 {"code": 工具的退出码, "result": 工具最后一行 JSON}；工具本身的判定（准入、额度、交常浩）不在这里重做。
不收别的：没有批准派单（只认门卫卡上的点击）、没有续接、没有回答卡片、没有开秘书，也没有任何读接口（读用只读窗口 3352）。
提议和补发同一时间只办一件（排队），每件最多 10 分钟；关秘书、撤销、冻结、收掉另排一队，不等提议（停是该最快生效的方向）。dt-worker 的门卫规则连不到这里；dt-secretary 只准连 3352、3353、8090。

  gate_intake.py serve [--port 3353]
  gate_intake.py dispatch-propose --repo R --executor claude|codex --scope-file F [--hours 12] [--request-id ID]
      客户端（在主账号 / 秘书账号里跑，猛鬼兽派单技能用）：读本账号的令牌副本，POST 到窗口，原样打印窗口回的 JSON；
      没给 --request-id 就新生成一个，输出里都带 request_id；结果不明时带同一个 --request-id 再跑一次，网关收过就原样返回
      那张提议（again: true），不会多出一张卡。
      退出码：0 网关收下了，1 网关拒了（原因在 result.why）或请求不对，2 连不上窗口或结果不明。
  gate_intake.py revoke|freeze --task 单号 [--reason 原话]
  gate_intake.py complete --task 单号
      客户端（猛鬼兽派单技能「停 / 冻 / 收掉 t-…」用）：POST /v1/tasks/<单号>/<动作>，原样打印网关的结果（含 runs_stopped、
      runs_stop_error、in_flight、mirror_error、冻结的 freeze_code）。退出码：0 生效（mirror_error 也算生效，看字段），1 网关拒了
      （已经是终态、没有这张单等，原因在 why）或请求不对，2 连不上窗口或结果不明（可能已经生效，要核）。
  gate_intake.py send-pending
      客户端：POST /v1/dispatch/send-pending，原样打印网关补发的结果（每个事件一条，含 event_key、sent、held）。
      退出码：0 补发跑完了（逐条看 sent），1 网关拒了，2 连不上窗口或结果不明。
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
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{16,64}$")  # 和 autoloop_grants.REQUEST_ID 一样
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
    if not isinstance(p, dict) or set(p) - {"repo", "executor", "scope", "hours", "request_id"}:
        raise Refused("派单提议只收 repo、executor、scope、hours、request_id")
    repo, executor, scope, hours = p.get("repo"), p.get("executor"), p.get("scope"), p.get("hours", 12)
    rid = p.get("request_id")
    if not isinstance(rid, str) or not REQUEST_ID_RE.match(rid):
        raise Refused("request_id 要是 16 到 64 位小写十六进制（客户端生成，重试带同一个）")
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
                                           "--hours", str(hours), "--request-id", rid])
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
            task = re.fullmatch(r"/v1/tasks/([^/]+)/(revoke|freeze|complete)", self.path)
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
                elif self.path == "/v1/dispatch/send-pending":
                    reason_of(body)
                    with proposals:
                        code, out = tool("autoloop_grants.py", ["send-pending"])
                elif task and TASK_ID.match(task.group(1)):
                    r, action = reason_of(body), task.group(2)
                    args = [action, "--task", task.group(1)]
                    if action != "complete":
                        args += ["--reason", f"{SOURCE}：{r}" if r else SOURCE]
                    with stops:
                        code, out = tool("autoloop_grants.py", args)
                else:
                    return self._send(404, {"error": "只有 /v1/secretary/proposal、/v1/secretary/off、"
                                                     "/v1/tasks/<单号>/revoke|freeze|complete、/v1/dispatch/proposal、"
                                                     "/v1/dispatch/send-pending"})
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


def _post(path, payload, timeout):
    """客户端：带本账号的令牌 POST 到窗口（不走代理）。返回 (退出码, 窗口回的 JSON)；出错返回 (退出码, {"ok": False, "why"})。"""
    try:
        token = load_token()
    except (Refused, OSError) as e:
        return 2, {"ok": False, "why": f"读不到窗口令牌：{e}"}
    req = urllib.request.Request(intake_url() + path, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
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
    return 0, out if isinstance(out, dict) else {}


def propose_dispatch(repo, executor, scope_text, hours=12, timeout=TIMEOUT + 20, request_id=None):
    """客户端：把派单提议交给窗口。返回 (退出码, 要打印的 JSON，都带 request_id)。"""
    rid = request_id or secrets.token_hex(16)
    code, out = _post("/v1/dispatch/proposal", {"repo": repo, "executor": executor, "scope": scope_text, "hours": hours,
                                                "request_id": rid}, timeout)
    if code:
        return code, {**out, "request_id": rid}
    result = out.get("result")
    if out.get("code") == 0 and isinstance(result, dict) and result.get("code"):
        return 0, {**result, "request_id": rid}
    return 1, {**(result if isinstance(result, dict) else {"ok": False, "why": f"网关没给结果（退出码 {out.get('code')}）"}),
               "request_id": rid}


TASK_ACTIONS = ("revoke", "freeze", "complete")


def task_action(action, task_id, reason="", timeout=TIMEOUT + 20):
    """客户端：撤销 / 冻结 / 收掉一张单。返回 (退出码, 要打印的 JSON)：0 生效（网关退出码 0，或 2 = 已生效但镜像没写成），
    1 拒了，2 不明。"""
    if action not in TASK_ACTIONS:
        return 1, {"ok": False, "why": f"动作只能是 {'、'.join(TASK_ACTIONS)}"}
    if not TASK_ID.match(task_id or ""):
        return 1, {"ok": False, "why": "单号要像 t-20261124-ab12cd"}
    code, out = _post(f"/v1/tasks/{task_id}/{action}", {"reason": reason[:200]} if action != "complete" else {}, timeout)
    if code:
        return code, out
    result = out.get("result")
    if not isinstance(result, dict):        # 网关没打印 JSON（报错堆栈）：可能已经生效
        return 2, {"ok": False, "why": f"网关没给结果（退出码 {out.get('code')}），停可能已经生效，要在电脑上核"}
    if out.get("code") in (0, 2):
        return 0, result
    return 1, result


def send_pending(timeout=TIMEOUT + 20):
    """客户端：补发。返回 (退出码, 要打印的 JSON)：0 网关跑完了（结果是每个事件一条的列表），1 拒了，2 不明。"""
    code, out = _post("/v1/dispatch/send-pending", {}, timeout)
    if code:
        return code, out
    result = out.get("result")
    if out.get("code") == 0 and isinstance(result, list):
        return 0, result
    if isinstance(result, dict):
        return 1, result
    return 2, {"ok": False, "why": f"网关没给结果（退出码 {out.get('code')}），补发结果不明"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve").add_argument("--port", type=int, default=3353)
    d = sub.add_parser("dispatch-propose")
    d.add_argument("--repo", required=True)
    d.add_argument("--executor", required=True)
    d.add_argument("--scope-file", required=True)
    d.add_argument("--hours", type=int, default=12)
    d.add_argument("--request-id")
    for action in TASK_ACTIONS:
        r = sub.add_parser(action)
        r.add_argument("--task", required=True)
        if action != "complete":
            r.add_argument("--reason", default="")
    sub.add_parser("send-pending")
    a = ap.parse_args(argv)
    if a.cmd in TASK_ACTIONS or a.cmd == "send-pending":
        code, out = task_action(a.cmd, a.task, getattr(a, "reason", "")) if a.cmd in TASK_ACTIONS else send_pending()
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return code
    if a.cmd == "dispatch-propose":
        try:
            scope = Path(a.scope_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(json.dumps({"ok": False, "why": f"读不了五要素文件：{type(e).__name__}"}, ensure_ascii=False))
            return 1
        code, out = propose_dispatch(a.repo, a.executor, scope, a.hours, request_id=a.request_id)
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
