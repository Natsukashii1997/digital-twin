#!/usr/bin/env python3
"""ChatGPT 聊天审核通道（RELAY-01，#35）。

在 ORCA 内嵌浏览器的 ChatGPT 聊天页里发出送审包，等回复结束后把原文落盘、解析结论；
结论是 REWORK 时，把回执路径交给 ORCA 终端里的 Claude。

只做搬运和判定，不做审核本身。结论缺失、互相矛盾、超时一律不算通过（fail closed）。

用法：
  chatgpt_relay.py status --worktree W --page P
  chatgpt_relay.py review --worktree W --page P --packet 送审包.md --out 回执.md [--deliver-to 终端]
  chatgpt_relay.py collect …同 review 参数…   # 等待中途断掉时收取已写完的回复，不重发
  chatgpt_relay.py deliver --record 回执.md --deliver-to 终端   # 回执已保存但交付失败时重试交付

退出码：0 PASS；10 REWORK；20 NEEDS_HUMAN；2 通道出错（没有回执）；3 回执已保存、交付失败。
发送前会留 `<回执>.sending` 标记，回执落盘后才删；标记还在说明上次发送结果不明，review 拒绝重发，先用 collect 核对。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time

ORCA = os.environ.get("ORCA_BIN", "orca")

FOOTER = (
    "\n\n---\n"
    "【回执格式】请在回复的最后一行单独写一行结论，三选一："
    "`VERDICT: PASS`、`VERDICT: REWORK`、`VERDICT: NEEDS_HUMAN`。"
    "涉及合并、安装、改真实配置或需要常浩拍板的，写 NEEDS_HUMAN。"
)

# 选择器集中放这里：ChatGPT 改版时只改这一处，并重跑实测。
COMPOSER = '#prompt-textarea, .ProseMirror[contenteditable="true"]'
SEND_BTN = '[data-testid="send-button"], button[aria-label="发送"], button[aria-label="Send prompt"]'
STOP_BTN = '[data-testid="stop-button"], button[aria-label*="停止"], button[aria-label^="Stop"]'

# 消息：旧版是 [data-message-author-role]；2026-10 版是 h4[data-conversation-role]（「你说：」「ChatGPT 说：」），正文在它的父节点里。
# 页面末尾会多渲染一份带「You」前缀的用户消息，所以 last_user 取最后一条回复之前的那条用户消息。
# 输入框里常驻一个隐藏的「停止」按钮，只有可见的才算正在生成。
STATE_JS = r"""/*relay:state*/(() => {
  const q = s => [...document.querySelectorAll(s)];
  let m = q('[data-message-author-role]').map(e => ({role: e.dataset.messageAuthorRole, text: e.innerText}));
  if (!m.length) m = q('h4[data-conversation-role]').map(h => {
    const t = h.parentElement.innerText, label = h.innerText;
    return {role: h.getAttribute('data-conversation-role'), text: (t.startsWith(label) ? t.slice(label.length) : t).trim()};
  });
  const a = m.filter(x => x.role === 'assistant');
  const ai = m.map(x => x.role).lastIndexOf('assistant');
  const before = (ai < 0 ? m : m.slice(0, ai)).filter(x => x.role === 'user');
  return JSON.stringify({
    url: location.href,
    composer: !!document.querySelector('%(composer)s'),
    streaming: q('%(stop)s').some(b => b.getClientRects().length > 0),
    user: m.filter(x => x.role === 'user').length,
    assistant: a.length,
    last_user: before.length ? before[before.length - 1].text : '',
    last: a.length ? a[a.length - 1].text : ''
  });
})()""" % {"composer": COMPOSER, "stop": STOP_BTN}

# 先清空再填入；填没填对由 COMPOSER_TEXT_JS 回读后在 Python 里逐字比对（编辑器异步渲染，不能当场判断）。
INSERT_JS = r"""/*relay:insert*/(() => {
  const el = document.querySelector('%s');
  if (!el) return false;
  el.focus();
  document.execCommand('selectAll');
  document.execCommand('delete');
  document.execCommand('insertText', false, %%s);
  return true;
})()""" % COMPOSER

COMPOSER_TEXT_JS = r"""/*relay:composer*/(document.querySelector('%s') || {}).innerText || ''""" % COMPOSER

SEND_JS = r"""/*relay:send*/(() => {
  const b = document.querySelector('%s');
  if (!b || b.disabled) return false;
  b.click();
  return true;
})()""" % SEND_BTN

VERDICT_RE = re.compile(r"^\s*`?VERDICT\s*[:：]\s*(PASS|REWORK|NEEDS_HUMAN)`?\s*$", re.M)
EXIT = {"PASS": 0, "REWORK": 10, "NEEDS_HUMAN": 20}


class RelayError(Exception):
    pass


def orca(*args, timeout=60):
    try:
        p = subprocess.run([ORCA, *args, "--json"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RelayError("orca %s 超过 %ss 没有返回" % (args[0], timeout))
    try:
        data = json.loads(p.stdout)
    except ValueError:
        raise RelayError("orca %s 输出不是 JSON：%s" % (args[0], (p.stderr or p.stdout).strip()[:300]))
    if p.returncode != 0 or not isinstance(data, dict) or data.get("ok") is not True \
            or not isinstance(data.get("result"), dict):
        detail = data.get("error", data) if isinstance(data, dict) else data
        raise RelayError("orca %s 失败（退出码 %s）：%s" % (
            args[0], p.returncode, json.dumps(detail, ensure_ascii=False)[:300]))
    return data["result"]


class Page:
    def __init__(self, worktree, page):
        self.worktree, self.page = worktree, page

    def eval(self, js):
        """返回页面表达式结果的字符串形式（orca eval 把 true 也返回成 "true"）。"""
        r = orca("eval", "--worktree", self.worktree, "--page", self.page, "--expression", js)
        if "result" not in r:
            raise RelayError("orca eval 没有返回 result 字段")
        return str(r["result"])

    def state(self):
        return json.loads(self.eval(STATE_JS))


def parse_verdict(text):
    """只认最后一个非空行；正文里举例写到的 VERDICT 行不算结论。"""
    lines = [l for l in text.splitlines() if l.strip()]
    m = VERDICT_RE.match(lines[-1]) if lines else None
    return m.group(1) if m else "NEEDS_HUMAN"


def conversation(url):
    m = re.search(r"/c/([0-9a-f-]+)", url)
    return m.group(1) if m else url


def squash(s):
    return re.sub(r"\s+", "", s)


def squash_bubble(s):
    """已发出的用户消息气泡会渲染 Markdown 代码（去掉反引号和代码块语言行）、折叠长文并加「显示更多」，比对时去掉这些。"""
    s = re.sub(r"(?m)^\s*```[\w+-]*\s*$", "", s)
    s = squash(s).replace("`", "")
    return re.sub(r"…?(显示更多|Showmore)$", "", s)


def bubble_matches(bubble, sent):
    """长消息气泡只显示开头，所以要求气泡内容是送审包的开头，且至少 300 字（短包则要求全文）。"""
    b, w = squash_bubble(bubble), squash_bubble(sent)
    return len(b) >= min(300, len(w)) and w.startswith(b)


def fill_composer(page, text, poll):
    if page.eval(INSERT_JS % json.dumps(text)) != "true":
        raise RelayError("页面没有输入框")
    for _ in range(10):
        if squash(page.eval(COMPOSER_TEXT_JS)) == squash(text):
            return
        time.sleep(max(poll, 0.01) / 3)
    raise RelayError("输入框内容和送审包不一致，未发送")


def send_and_wait(page, text, timeout, poll, settle, marker):
    """发出送审包并等回复写完。回复必须同时满足：属于这份送审包（最后一条回复之前的用户消息核对一致）、
    会话没变、看不到停止按钮、文本至少 3 次读取不变且持续 settle 秒。"""
    before = page.state()
    if not before["composer"]:
        raise RelayError("页面没有输入框：没登录或不在聊天页（%s）" % before["url"])
    if before["streaming"]:
        raise RelayError("ChatGPT 还在回复上一条，拒绝插话")
    fill_composer(page, text, poll)
    marker.create(before["url"])  # 从这里起发送结果可能不明
    if page.eval(SEND_JS) != "true":
        marker.clear()  # 按钮没点到，确定没发
        raise RelayError("发送按钮不可用")
    deadline = time.monotonic() + timeout
    conv, prev, since, seen = None, None, 0.0, 0
    while time.monotonic() < deadline:
        time.sleep(poll)
        st = page.state()
        if st["user"] <= before["user"] or st["assistant"] <= before["assistant"]:
            prev = None
            continue
        if conv is None:
            conv = conversation(st["url"])
        elif conversation(st["url"]) != conv:
            raise RelayError("等回复期间会话变了：%s" % st["url"])
        if not bubble_matches(st["last_user"], text):
            raise RelayError("最新回复前的用户消息不是这份送审包，拒绝收取")
        if st["streaming"] or not st["last"].strip():
            prev = None
            continue
        now = time.monotonic()
        if st["last"] != prev:
            prev, since, seen = st["last"], now, 1
            continue
        seen += 1
        if seen >= 3 and now - since >= settle:
            return prev, st["url"]
    raise RelayError("等回复超时（%ss）；消息可能已发出，用 collect 核对" % timeout)


class Marker:
    """发送标记：点发送前建，回执落盘后删。还在就说明上次发送结果不明。"""

    def __init__(self, out, packet):
        self.path, self.packet = out + ".sending", packet

    def exists(self):
        return os.path.exists(self.path)

    def create(self, url):
        with open(self.path, "x", encoding="utf-8") as f:
            json.dump({"packet_sha256": hashlib.sha256(self.packet.encode()).hexdigest(), "page": url,
                       "time": datetime.datetime.now().astimezone().isoformat(timespec="seconds")},
                      f, ensure_ascii=False)

    def clear(self):
        if self.exists():
            os.unlink(self.path)


def write_record(path, packet, reply, url, verdict):
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    head = [
        "# ChatGPT 审核回执原文",
        "",
        "- 时间：%s" % now,
        "- 会话：%s" % url,
        "- 送审包 sha256：`%s`" % hashlib.sha256(packet.encode()).hexdigest(),
        "- 解析结论：**%s**（relay 解析；以下为 ChatGPT 原文，未改动）" % verdict,
        "",
        "---",
        "",
    ]
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(head) + reply.rstrip() + "\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, path)  # 原子发布，且不覆盖已有回执
    except FileExistsError:
        raise RelayError("回执文件已存在，拒绝覆盖：%s" % path)
    finally:
        os.unlink(tmp)


def deliver(terminal, path):
    msg = ("ChatGPT 审核结论 REWORK，回执原文：%s 。请先读原文，按仓库 AGENTS.md 逐条核实属实后再返修；"
           "不属实的写明理由。返修完写新的送审包，不要自行合并或安装。" % os.path.abspath(path))
    orca("terminal", "send", "--terminal", terminal, "--text", msg, "--enter")


def cmd_status(a):
    st = Page(a.worktree, a.page).state()
    st["last"], st["last_user"] = st["last"][:80], st["last_user"][:80]
    print(json.dumps(st, ensure_ascii=False))
    return 0 if st["composer"] else 2


def read_packet(a):
    if os.path.exists(a.out):
        raise RelayError("回执文件已存在，拒绝覆盖：%s" % a.out)
    with open(a.packet, encoding="utf-8") as f:
        return f.read()


def cmd_review(a):
    packet = read_packet(a)
    marker = Marker(a.out, packet)
    if marker.exists():
        raise RelayError("上次送审结果不明（%s），先用 collect 核对，不要重发" % marker.path)
    reply, url = send_and_wait(Page(a.worktree, a.page), packet + FOOTER, a.timeout, a.poll, a.settle, marker)
    return finish(a, packet, reply, url, marker)


def cmd_collect(a):
    """等待中途断掉时，不重发、直接收取页面上已写完的回复：最后一条回复之前的用户消息必须是这份送审包，
    且隔 settle 秒读两次都没在生成、文本不变。"""
    packet = read_packet(a)
    page = Page(a.worktree, a.page)
    reads = [page.state()]
    time.sleep(a.settle)
    reads.append(page.state())
    for st in reads:
        if st["streaming"]:
            raise RelayError("ChatGPT 还在生成，稍后再收")
        if not st["assistant"] or not st["last"].strip():
            raise RelayError("页面上没有可收取的回复")
        if not bubble_matches(st["last_user"], packet + FOOTER):
            raise RelayError("最新回复前的用户消息不是这份送审包，拒绝收取")
    if reads[0]["last"] != reads[1]["last"] or conversation(reads[0]["url"]) != conversation(reads[1]["url"]):
        raise RelayError("回复还在变化，稍后再收")
    return finish(a, packet, reads[1]["last"], reads[1]["url"], Marker(a.out, packet))


def finish(a, packet, reply, url, marker):
    verdict = parse_verdict(reply)
    write_record(a.out, packet, reply, url, verdict)
    marker.clear()
    result = {"verdict": verdict, "record": a.out, "url": url, "delivered": False}
    code = EXIT[verdict]
    if verdict == "REWORK" and a.deliver_to:
        try:
            deliver(a.deliver_to, a.out)
            result["delivered"] = True
        except RelayError as e:
            result["deliver_error"] = "%s；回执已保存，用 deliver 重试" % e
            code = 3
    print(json.dumps(result, ensure_ascii=False))
    return code


def cmd_deliver(a):
    with open(a.record, encoding="utf-8") as f:
        head = f.read(2000)
    if "- 解析结论：**REWORK**" not in head:
        raise RelayError("回执结论不是 REWORK，不交付：%s" % a.record)
    deliver(a.deliver_to, a.record)
    print(json.dumps({"record": a.record, "delivered": True}, ensure_ascii=False))
    return 10


def main(argv=None):
    ap = argparse.ArgumentParser(description="ChatGPT 聊天审核通道")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "review", "collect"):
        s = sub.add_parser(name)
        s.add_argument("--worktree", required=True, help="ORCA 工作区选择器，如 path:/…")
        s.add_argument("--page", required=True, help="ChatGPT 标签的 browserPageId")
        if name != "status":
            s.add_argument("--packet", required=True)
            s.add_argument("--out", required=True)
            s.add_argument("--deliver-to", help="REWORK 时把回执交给这个 ORCA 终端")
            s.add_argument("--settle", type=float, default=10, help="回复文本须保持不变的秒数")
    r = sub.choices["review"]
    r.add_argument("--timeout", type=float, default=900)
    r.add_argument("--poll", type=float, default=3)
    d = sub.add_parser("deliver")
    d.add_argument("--record", required=True)
    d.add_argument("--deliver-to", required=True)
    a = ap.parse_args(argv)
    try:
        return {"status": cmd_status, "review": cmd_review, "collect": cmd_collect,
                "deliver": cmd_deliver}[a.cmd](a)
    except RelayError as e:
        print("relay 出错：%s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
