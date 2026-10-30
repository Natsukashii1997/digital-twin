#!/usr/bin/env python3
"""ChatGPT 聊天审核通道（RELAY-01，#35）。

在 ORCA 内嵌浏览器的 ChatGPT 聊天页里发出送审包，等回复结束后把原文落盘、解析结论；
结论是 REWORK 时，把回执路径交给 ORCA 终端里的 Claude。

只做搬运和判定，不做审核本身。结论缺失、互相矛盾、超时一律不算通过（fail closed）。

用法：
  chatgpt_relay.py status --worktree W --page P
  chatgpt_relay.py review --worktree W --page P --packet 送审包.md --out 回执.md [--deliver-to 终端]
  chatgpt_relay.py collect …同 review 参数…   # 等待中途断掉时收取已写完的回复，不重发

退出码：0 PASS；10 REWORK；20 NEEDS_HUMAN；2 通道出错（没有回执）。
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
STATE_JS = r"""/*relay:state*/(() => {
  const q = s => [...document.querySelectorAll(s)];
  const msgs = role => {
    const old = q('[data-message-author-role="' + role + '"]');
    if (old.length) return old.map(e => e.innerText);
    return q('h4[data-conversation-role="' + role + '"]').map(h => {
      const t = h.parentElement.innerText, label = h.innerText;
      return (t.startsWith(label) ? t.slice(label.length) : t).trim();
    });
  };
  const u = msgs('user'), a = msgs('assistant');
  return JSON.stringify({
    url: location.href,
    composer: !!document.querySelector('%(composer)s'),
    streaming: !!document.querySelector('%(stop)s'),
    user: u.length,
    assistant: a.length,
    last_user: u.length ? u[u.length - 1] : '',
    last: a.length ? a[a.length - 1] : ''
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


def orca(*args):
    p = subprocess.run([ORCA, *args, "--json"], capture_output=True, text=True)
    try:
        data = json.loads(p.stdout)
    except ValueError:
        raise RelayError("orca %s 输出不是 JSON：%s" % (args[0], (p.stderr or p.stdout).strip()[:300]))
    if not data.get("ok"):
        raise RelayError("orca %s 失败：%s" % (args[0], json.dumps(data.get("error", data), ensure_ascii=False)[:300]))
    return data["result"]


class Page:
    def __init__(self, worktree, page):
        self.worktree, self.page = worktree, page

    def eval(self, js):
        """返回页面表达式结果的字符串形式（orca eval 把 true 也返回成 "true"）。"""
        r = orca("eval", "--worktree", self.worktree, "--page", self.page, "--expression", js)
        return str(r["result"])

    def state(self):
        return json.loads(self.eval(STATE_JS))


def parse_verdict(text):
    found = set(VERDICT_RE.findall(text))
    return found.pop() if len(found) == 1 else "NEEDS_HUMAN"


def squash(s):
    return re.sub(r"\s+", "", s)


def squash_bubble(s):
    """已发出的用户消息气泡会渲染 Markdown 代码（去掉反引号和代码块语言行）、折叠长文并加「显示更多」，比对时去掉这些。"""
    s = re.sub(r"(?m)^\s*```[\w+-]*\s*$", "", s)
    s = squash(s).replace("`", "")
    return re.sub(r"…?(显示更多|Showmore)$", "", s)


def fill_composer(page, text, poll):
    if page.eval(INSERT_JS % json.dumps(text)) != "true":
        raise RelayError("页面没有输入框")
    for _ in range(10):
        if squash(page.eval(COMPOSER_TEXT_JS)) == squash(text):
            return
        time.sleep(max(poll, 0.01) / 3)
    raise RelayError("输入框内容和送审包不一致，未发送")


def send_and_wait(page, text, timeout, poll, settle=2):
    before = page.state()
    if not before["composer"]:
        raise RelayError("页面没有输入框：没登录或不在聊天页（%s）" % before["url"])
    if before["streaming"]:
        raise RelayError("ChatGPT 还在回复上一条，拒绝插话")
    fill_composer(page, text, poll)
    if page.eval(SEND_JS) != "true":
        raise RelayError("发送按钮不可用")
    deadline = time.monotonic() + timeout
    sent = False
    prev, same = None, 0
    while time.monotonic() < deadline:
        time.sleep(poll)
        st = page.state()
        sent = sent or st["user"] > before["user"]
        if not sent or st["assistant"] <= before["assistant"] or st["streaming"]:
            prev, same = None, 0
            continue
        same = same + 1 if st["last"] == prev else 0
        prev = st["last"]
        if same >= settle - 1 and prev.strip():
            return prev, st["url"]
    raise RelayError("等回复超时（%ss）；%s" % (timeout, "消息已发出" if sent else "没确认消息发出"))


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
    with open(path, "x", encoding="utf-8") as f:
        f.write("\n".join(head) + reply.rstrip() + "\n")


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
    reply, url = send_and_wait(Page(a.worktree, a.page), packet + FOOTER, a.timeout, a.poll)
    return finish(a, packet, reply, url)


def cmd_collect(a):
    """等待中途断掉时，不重发、直接收取页面上已写完的回复；先核对最后一条用户消息就是这份送审包。"""
    packet = read_packet(a)
    st = Page(a.worktree, a.page).state()
    if st["streaming"]:
        raise RelayError("ChatGPT 还在生成，稍后再收")
    if not st["assistant"] or not st["last"].strip():
        raise RelayError("页面上没有可收取的回复")
    if squash_bubble(st["last_user"]) != squash_bubble(packet + FOOTER):
        raise RelayError("页面最后一条用户消息不是这份送审包，拒绝收取")
    return finish(a, packet, st["last"], st["url"])


def finish(a, packet, reply, url):
    verdict = parse_verdict(reply)
    write_record(a.out, packet, reply, url, verdict)
    if verdict == "REWORK" and a.deliver_to:
        deliver(a.deliver_to, a.out)
    print(json.dumps({"verdict": verdict, "record": a.out, "url": url,
                      "delivered": bool(verdict == "REWORK" and a.deliver_to)}, ensure_ascii=False))
    return EXIT[verdict]


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
    r = sub.choices["review"]
    r.add_argument("--timeout", type=float, default=900)
    r.add_argument("--poll", type=float, default=3)
    a = ap.parse_args(argv)
    try:
        return {"status": cmd_status, "review": cmd_review, "collect": cmd_collect}[a.cmd](a)
    except RelayError as e:
        print("relay 出错：%s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
