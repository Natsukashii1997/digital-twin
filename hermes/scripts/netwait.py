"""常驻任务先等环境就绪（P9，项目坑 L-03，全局坑 G-19 / G-20）：Hermes 无模型定时任务共用的等待与重试。

Mac 睡眠时定时任务晚点，醒来那一刻代理和网络常常还没通。原来 report_data（等主模型通道）、pdaily_data（gh 重试）、
memdaily_data（lark-cli 重试）各写一份，判断「是不是网络问题」的规则也各不一样；现在都用这里的三样：

- gate / guard：开跑闸门。先等要用到的地址都连得上（收到任何 HTTP 响应都算通，403 也算），最多等 DT_NET_WAIT_SECONDS 秒
  （默认 300，0 = 不等也不探，测试用）；等不到就明确失败、写原因，不悄悄跳过。每次等待记一行到
  evidence/netwait/netwait.jsonl（DT_NETWAIT_LOG 可改），只记目标名、地址、错误类型，不记环境变量。
- is_net_error：一段报错算不算网络问题（连不上、超时、断开、解析失败、令牌刷新请求失败、飞书导出要重试）。
- Budget：网络类失败的重试预算，从第一次失败算起总共最多等多少秒，每 INTERVAL 秒重试一次。

guard 是给入口脚本用的：等不到时打印一句「⚠️ <任务>没跑：……」并以退出码 1 结束，由 Hermes 定时任务发到私聊；
quiet_hours > 0 的高频任务（每 5 / 30 分钟一跑），同一任务在这段时间里已经报过一次、之后没通过，就只记录不再打印。
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

INTERVAL = 15          # 秒：两次探测 / 重试之间
DEFAULT_WAIT = 300     # 秒：唤醒后代理一般一两分钟内就通（#140、#176 的实测）
TARGETS = {"feishu": "https://open.feishu.cn/", "github": "https://api.github.com/", "aihot": "https://aihot.news/"}
PROBE_BY_PROVIDER = (("claude", "https://api.anthropic.com/"), ("anthropic", "https://api.anthropic.com/"),
                     ("openai-codex", "https://chatgpt.com/"))   # 提供方名里含这些字就用对应地址；换执行方不用改代码
NET_ERROR = re.compile(r"connect|timed? ?out|\beof\b|reset|handshake|resolve|no such host|network is unreachable|dial tcp"
                       r"|token refresh request failed|retry the export|could not export|SSL_ERROR_SYSCALL", re.I)


def log_path():
    return Path(os.environ.get("DT_NETWAIT_LOG") or
                "~/Library/Application Support/DigitalTwin/evidence/netwait/netwait.jsonl").expanduser()


def wait_seconds():
    """最多等几秒：DT_NET_WAIT_SECONDS，默认 300；写错了按默认，负数当 0。"""
    try:
        return max(0, int(os.environ.get("DT_NET_WAIT_SECONDS", str(DEFAULT_WAIT))))
    except ValueError:
        return DEFAULT_WAIT


def is_net_error(text):
    return bool(NET_ERROR.search(text or ""))


class Budget:
    """网络类失败的重试预算：从第一次失败算起总共最多等 limit 秒，整轮共用（预算用完后面的调用不再等）。"""

    def __init__(self, limit=None, interval=INTERVAL, sleep=time.sleep, clock=time.monotonic):
        self.limit = wait_seconds() if limit is None else max(0, limit)
        self.interval, self.sleep, self.clock = interval, sleep, clock
        self.first, self.slept = None, 0

    def retry(self):
        """刚遇到一次网络类失败：预算内睡 interval 秒后返回 True（调用方重试），预算用完返回 False。

        已用时间取「时钟走过的」和「睡过的」里大的那个：sleep 被换成假的时（测试）也一定会用完，不会空转。"""
        now = self.clock()
        if self.first is None:
            self.first = now
        if max(now - self.first, self.slept) + self.interval > self.limit:
            return False
        self.sleep(self.interval)
        self.slept += self.interval
        return True


def hermes_config():
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "config.yaml"


def model_url():
    """主模型的探测地址：DT_MODEL_PROBE_URL 优先，否则按 Hermes 配置 model.provider 查表；认不出返回 None（不探）。"""
    if os.environ.get("DT_MODEL_PROBE_URL"):
        return os.environ["DT_MODEL_PROBE_URL"]
    try:
        m = re.search(r"^model:[ \t]*\n(?:[ \t]+.*\n)*?[ \t]+provider:[ \t]*['\"]?([\w.-]+)",
                      hermes_config().read_text(encoding="utf-8"), re.M)
    except OSError:
        return None
    provider = m.group(1).lower() if m else ""
    return next((url for key, url in PROBE_BY_PROVIDER if key in provider), None) if provider else None


def target_url(name):
    if name == "model":
        return model_url()
    env = os.environ.get(f"DT_PROBE_URL_{name.upper()}")   # 测试把探测地址换成本机端口
    return env or TARGETS[name]


def reachable(url, timeout=10):
    """返回 (通不通, 错误)。收到任何 HTTP 响应（含 403 等）都算通；探测本身出什么错都只算不通，不抛出。"""
    try:
        urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=timeout).close()
    except urllib.error.HTTPError:
        return True, ""
    except Exception as e:   # URLError、超时、连接被拒、代理回坏响应
        return False, f"{type(e).__name__}: {e}"[:200]
    return True, ""


def _record(rec):
    p = log_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass   # 记录写不了不影响任务


def wait_ready(kind, targets, limit=None, probe=reachable, sleep=time.sleep, clock=time.monotonic):
    """等 targets（feishu / github / model / aihot）都连得上。返回记录 dict：ok、waited_s、attempts、error、skipped。

    limit=0：不等也不探，直接算通（不写记录）。认不出探测地址的目标（主模型提供方不在表里）跳过，记在 skipped。"""
    limit = wait_seconds() if limit is None else limit
    if limit <= 0:
        return {"kind": kind, "ok": True, "waited_s": 0, "attempts": 0, "error": "", "skipped": []}
    urls, skipped = {}, []
    for t in targets:
        u = target_url(t)
        if u:
            urls[t] = u
        else:
            skipped.append(t)
    start, attempts, pending, err = clock(), 0, dict(urls), ""
    while pending:
        attempts += 1
        for t, u in list(pending.items()):
            ok, why = probe(u)
            if ok:
                pending.pop(t)
            else:
                err = f"{t}：{why}"
        if not pending or clock() - start + INTERVAL > limit:
            break
        sleep(INTERVAL)
    rec = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "kind": kind, "ok": not pending,
           "targets": list(targets), "waited_s": round(clock() - start, 1), "attempts": attempts,
           "error": "" if not pending else err, "skipped": skipped}
    return rec


class NotReady(Exception):
    pass


def gate(kind, targets, **kw):
    """等不到就抛 NotReady（带原因一句话）；等到了返回记录。记录都写进 netwait.jsonl。"""
    rec = wait_ready(kind, targets, **kw)
    if rec["attempts"]:
        _record(rec)
    if not rec["ok"]:
        raise NotReady(f"网络 / 代理 {int(rec['waited_s'])} 秒没通（{rec['error']}）")
    return rec


def _reported_recently(kind, hours, now=None):
    """这个任务 hours 小时内已经报过一次没通、之后没有通过的记录。"""
    now = now or dt.datetime.now().astimezone()
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        try:
            r = json.loads(line)
            at = dt.datetime.fromisoformat(r["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if r.get("kind") != kind:
            continue
        if now - at > dt.timedelta(hours=hours) or r.get("ok"):
            return False
        if r.get("reported"):
            return True
    return False


def guard(kind, targets, title, quiet_hours=0, **kw):
    """入口脚本开跑前调用：通了就返回；等不到就打印「⚠️ <title>没跑：原因」并 sys.exit(1)。

    quiet_hours > 0：同一任务这段时间里已报过、之后没通过，就只记录、不打印，以退出码 0 结束（高频任务不刷屏）。"""
    rec = wait_ready(kind, targets, **kw)
    if rec["ok"]:
        if rec["attempts"]:
            _record(rec)
        return rec
    quiet = quiet_hours > 0 and _reported_recently(kind, quiet_hours)
    rec["reported"] = not quiet
    _record(rec)
    if quiet:
        sys.exit(0)
    print(f"⚠️ {title}没跑：网络 / 代理 {int(rec['waited_s'])} 秒没通（{rec['error']}）。"
          "电脑可能刚从睡眠醒来，代理还没起来；下一次照常再跑，等不及可以手动补跑。")
    sys.exit(1)
