#!/usr/bin/env python3
"""ChatGPT 聊天审核通道（RELAY-01，#35）。

在 ORCA 内嵌浏览器的 ChatGPT 聊天页里发出送审包，等回复结束后把原文落盘、解析结论；
结论是 REWORK 时，把回执路径交给 ORCA 终端里的 Claude。

只做搬运和判定，不做审核本身。结论缺失、互相矛盾、超时一律不算通过（fail closed）。

用法：
  chatgpt_relay.py status --worktree W --page P
  chatgpt_relay.py review --worktree W --page P --chat 会话或项目首页地址 --packet 送审包.md --out 回执.md [--deliver-to 终端]
  chatgpt_relay.py collect …同 review 参数（不要 --chat）…   # 等待中途断掉时收取已写完的回复，不重发
  chatgpt_relay.py deliver --record 回执.md --deliver-to 终端 [--confirm-not-received]   # 只重试交付

退出码：0 PASS；10 REWORK；20 NEEDS_HUMAN；2 通道出错（没有回执）；3 回执已保存、交付失败。

身份与幂等（状态目录 $RELAY_STATE_DIR，默认 ~/Library/Application Support/DigitalTwin/relay/）：
- 发出的消息第一行是送审编号 R-<送审包 sha256 前 16 位>；页面气泡必须以它开头才认作这份送审包。
- 点发送前按送审包摘要建 sending/<sha256>.json，回执落盘后才删；它还在就说明上次发送结果不明，
  同一送审包（换 --out 也一样）拒绝重发，先用 collect 核对。
- 回执首行是 relay-record 元数据（送审编号、正文摘要、结论），deliver 先校验再交付；
  交付状态记在 delivered/<回执编号>.json（含目标终端），交付全程持有 flock 进程锁：并发交付直接拒绝；
  已交付到同一终端的再调用不重发；改交别的终端、或上次结果不明，都要人确认后才发。
"""
import argparse
import datetime
import fcntl
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
    composer: q('%(composer)s').filter(e => e.getClientRects().length > 0).length === 1,
    streaming: q('%(stop)s').some(b => b.getClientRects().length > 0),
    user: m.filter(x => x.role === 'user').length,
    assistant: a.length,
    last_user: before.length ? before[before.length - 1].text : '',
    last: a.length ? a[a.length - 1].text : ''
  });
})()""" % {"composer": COMPOSER, "stop": STOP_BTN}

# 先清空再填入；填没填对由 COMPOSER_TEXT_JS 回读后在 Python 里逐字比对（编辑器异步渲染，不能当场判断）。
# 输入框只认「可见且恰好一个」：页面上有多个编辑框（如编辑旧消息）时拒绝填写。
INSERT_JS = r"""/*relay:insert*/(() => {
  const els = [...document.querySelectorAll('%s')].filter(e => e.getClientRects().length > 0);
  if (els.length !== 1) return false;
  const el = els[0];
  el.focus();
  document.execCommand('selectAll');
  document.execCommand('delete');
  document.execCommand('insertText', false, %%s);
  return true;
})()""" % COMPOSER

COMPOSER_TEXT_JS = r"""/*relay:composer*/(() => {
  const els = [...document.querySelectorAll('%s')].filter(e => e.getClientRects().length > 0);
  return els.length === 1 ? els[0].innerText : '';
})()""" % COMPOSER

# 最后一次核对和点击在同一段脚本里同步完成（P1-01-R4）：Python 侧检查之后、点击之前，共用标签可能被切走或改动。
# 参数是发送前核对过的页面地址和送审消息；页面地址去掉查询串后必须完全一致（新会话首页同样适用）。
# 返回 sent 才算点了；其余返回值都是没点的原因。
SEND_JS = r"""/*relay:send*/((url, text) => {
  const norm = u => u.split('?')[0].split('#')[0].replace(/\/+$/, '');
  const squash = s => s.replace(/\s+/g, '');
  const vis = s => [...document.querySelectorAll(s)].filter(e => e.getClientRects().length > 0);
  if (norm(location.href) !== norm(url)) return 'chat';
  const els = vis('%(composer)s');
  if (els.length !== 1) return 'composer';
  if (squash(els[0].innerText) !== squash(text)) return 'text';
  if (vis('%(stop)s').length) return 'streaming';
  const b = document.querySelector('%(send)s');
  if (!b || b.disabled) return 'button';
  b.click();
  return 'sent';
})(%%s, %%s)""" % {"composer": COMPOSER, "stop": STOP_BTN, "send": SEND_BTN}

# 撰写器模式与思考强度（RELAY-02，#37）：常浩要求用「聊天」模式、思考强度「高」；切换会话后页面会变回「中」。
# 读：模式按钮的 aria-label、模型按钮文本的最后一行（「思考强度\n高」）。调：打开模型菜单 → 强度滑块按 End 到最高档 → Esc 关菜单。
MODE_LABEL, MODEL_LABEL = "切换模式，当前模式：ChatGPT", "选择 ChatGPT 模型"
MODE_JS = r"""/*relay:mode*/(() => {
  const vis = [...document.querySelectorAll('button')].filter(b => b.getClientRects().length > 0);
  const m = vis.find(b => b.getAttribute('aria-label') === '%s');
  const s = vis.find(b => b.getAttribute('aria-label') === '%s');
  return JSON.stringify({chat: !!m, strength: s ? s.innerText.trim().split('\n').pop().trim() : ''});
})()""" % (MODE_LABEL, MODEL_LABEL)
OPEN_MODEL_JS = r"""/*relay:open-model*/(() => {
  const b = [...document.querySelectorAll('button')].find(b => b.getClientRects().length > 0 && b.getAttribute('aria-label') === '%s');
  if (!b) return false;
  for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'])
    b.dispatchEvent(new (t.startsWith('pointer') ? PointerEvent : MouseEvent)(t, {bubbles: true}));
  return true;
})()""" % MODEL_LABEL
SET_HIGH_JS = r"""/*relay:set-high*/(() => {
  const s = document.querySelector('[role=menu] [role=slider]');
  if (!s) return false;
  s.focus();
  s.dispatchEvent(new KeyboardEvent('keydown', {key: 'End', code: 'End', bubbles: true}));
  return true;
})()"""
CLOSE_MENU_JS = r"""/*relay:close-menu*/(() => {
  const m = document.querySelector('[role=menu]');
  if (m) m.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true}));
  return !document.querySelector('[role=menu]');
})()"""

SEND_REFUSED = {
    "chat": "点发送前页面已不在指定的会话上",
    "composer": "点发送前输入框不是恰好一个",
    "text": "点发送前输入框内容已和送审包不一致",
    "streaming": "点发送前 ChatGPT 正在回复",
    "button": "发送按钮不可用",
}

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
    """会话 ID；还没进入具体会话（如项目首页）时为 None。
    新会话发出后，地址先是页面临时编号 /c/local-chatgpt%3A…，服务器分配真实编号后才换掉（RELAY-02 实测）；临时编号也当 None。"""
    m = re.search(r"/c/([^/?#]+)", url)
    if not m or m.group(1).startswith("local-") or "%3A" in m.group(1).upper() or ":" in m.group(1):
        return None
    return m.group(1)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def review_id(packet):
    return "R-" + sha(packet)[:16]


def message_for(packet):
    """实际发出的消息：第一行是送审编号（由送审包全文摘要得出），末尾是回执格式要求。"""
    return "【RELAY 送审编号 %s】\n\n%s%s" % (review_id(packet), packet, FOOTER)


def squash(s):
    return re.sub(r"\s+", "", s)


def squash_bubble(s):
    """已发出的用户消息气泡会渲染 Markdown 代码（去掉反引号和代码块语言行）、折叠长文并加「显示更多」，比对时去掉这些。"""
    s = re.sub(r"(?m)^\s*```[\w+-]*\s*$", "", s)
    s = squash(s).replace("`", "")
    return re.sub(r"…?(显示更多|Showmore)$", "", s)


def bubble_matches(bubble, sent):
    """长消息气泡只显示开头，所以要求气泡是已发消息的开头，且至少 300 字（短包则要求全文）。
    已发消息第一行就是送审编号（由送审包全文摘要得出），所以这同时要求编号一致：两份送审包模板再像也分得开。"""
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


def read_mode(page, wait):
    """切会话后强度按钮先显示默认值、稍后才换成会话记住的值（实测「中」约 1 秒后变「高」），所以要连续两次读数一致才算。"""
    prev = json.loads(page.eval(MODE_JS))
    for _ in range(10):
        time.sleep(wait)
        st = json.loads(page.eval(MODE_JS))
        if st == prev:
            return st
        prev = st
    raise RelayError("撰写器的模式和思考强度读数一直在变，拒绝发送")


def ensure_chat_high(page, wait=1.0):
    """撰写器必须是聊天模式；思考强度不是「高」就调高，调完回读，仍不是「高」就拒绝发送。返回是否动过设置。"""
    st = read_mode(page, wait)
    if not st["chat"]:
        raise RelayError("撰写器不是聊天模式，拒绝发送")
    if st["strength"] == "高":
        return False
    if page.eval(OPEN_MODEL_JS) != "true":
        raise RelayError("找不到模型按钮，没法把思考强度调到「高」，拒绝发送")
    time.sleep(wait)
    page.eval(SET_HIGH_JS)
    time.sleep(wait)
    page.eval(CLOSE_MENU_JS)
    st = read_mode(page, wait)
    if not st["chat"] or st["strength"] != "高":
        raise RelayError("思考强度没能调到「高」（现在「%s」），拒绝发送" % st["strength"])
    return True


def same_chat(url, expected):
    """expected 是会话地址时比会话 ID；是项目首页等其他地址时比去掉查询串后的路径。"""
    if conversation(expected):
        return conversation(url) == conversation(expected)
    norm = lambda u: u.split("?")[0].split("#")[0].rstrip("/")
    return norm(url) == norm(expected)


def send_and_wait(page, packet, chat, timeout, poll, settle, marker):
    """发出送审包并等回复写完。回复必须同时满足：在发送时所在的会话里（新会话以发送后第一次进入的为准，
    之后不能再变）、最新回复之前的用户消息带这份送审包的编号、看不到停止按钮、文本至少 3 次读取不变且持续 settle 秒。
    不靠消息条数：长会话里 ChatGPT 只渲染最近几轮，条数会停住不涨。最新回复前的用户消息还不是本包，就当回复没到、
    继续等（别人插话的情形会等到超时，按结果不明处理）。"""
    text = message_for(packet)
    before = page.state()
    if not same_chat(before["url"], chat):
        raise RelayError("页面不在指定的会话上（现在 %s，要求 %s），拒绝发送" % (before["url"], chat))
    if not before["composer"]:
        raise RelayError("页面没有输入框：没登录或不在聊天页（%s）" % before["url"])
    if before["streaming"]:
        raise RelayError("ChatGPT 还在回复上一条，拒绝插话")
    if bubble_matches(before["last_user"], text):
        raise RelayError("这份送审包（%s）在本会话里已经有回复，拒绝重发；要收那条回复用 collect" % review_id(packet))
    conv = conversation(before["url"])
    ensure_chat_high(page, wait=min(1.0, poll))
    fill_composer(page, text, poll)
    marker.create(conv, before["url"])  # 从这里起发送结果可能不明
    sent = page.eval(SEND_JS % (json.dumps(before["url"]), json.dumps(text)))
    if sent in SEND_REFUSED:
        marker.clear()  # 脚本在点击前拒绝了，确定没发
        raise RelayError(SEND_REFUSED[sent] + "，没有点发送")
    if sent != "sent":
        raise RelayError("发送脚本返回 %r，发送结果不明；先用 collect 核对" % sent)
    deadline = time.monotonic() + timeout
    prev, since, seen = None, 0.0, 0
    while time.monotonic() < deadline:
        time.sleep(poll)
        st = page.state()
        cur = conversation(st["url"])
        if conv is None and cur is not None:
            conv = cur
            marker.bind(conv)
        elif cur != conv:
            raise RelayError("发送后会话变了：%s" % st["url"])
        if not bubble_matches(st["last_user"], text) or st["streaming"] or not st["last"].strip():
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


def state_dir(kind):
    d = os.path.join(os.environ.get("RELAY_STATE_DIR")
                     or os.path.expanduser("~/Library/Application Support/DigitalTwin/relay"), kind)
    os.makedirs(d, exist_ok=True)
    return d


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


class Marker:
    """发送标记：按送审包摘要存放，点发送前建，回执落盘后删。还在就说明这份送审包上次发送结果不明。"""

    def __init__(self, packet):
        self.path = os.path.join(state_dir("sending"), sha(packet) + ".json")
        self.packet = packet

    def exists(self):
        return os.path.exists(self.path)

    def load(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def create(self, conv, page_url):
        with open(self.path, "x", encoding="utf-8") as f:
            json.dump({"packet_sha256": sha(self.packet), "review_id": review_id(self.packet),
                       "conversation": conv, "page": page_url, "time": now_iso()}, f, ensure_ascii=False)

    def bind(self, conv):
        data = self.load()
        data["conversation"] = conv
        tmp = "%s.tmp-%d" % (self.path, os.getpid())
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)  # 原子替换：中途失败时原标记不变
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def clear(self):
        if self.exists():
            os.unlink(self.path)


RECORD_META = re.compile(r"\A<!-- relay-record (\{.*\}) -->\n")
SEPARATOR = "\n---\n\n"


def write_record(path, packet, reply, url, verdict):
    body = reply.rstrip() + "\n"
    meta = {"review_id": review_id(packet), "packet_sha256": sha(packet), "conversation": conversation(url),
            "verdict": verdict, "reply_sha256": sha(body)}
    head = [
        "<!-- relay-record %s -->" % json.dumps(meta, ensure_ascii=False),
        "# ChatGPT 审核回执原文",
        "",
        "- 时间：%s" % now_iso(),
        "- 会话：%s" % url,
        "- 送审编号：`%s`；送审包 sha256：`%s`" % (meta["review_id"], meta["packet_sha256"]),
        "- 解析结论：**%s**（relay 解析；以下为 ChatGPT 原文，未改动）" % verdict,
    ]
    tmp = "%s.tmp-%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(head) + SEPARATOR + body)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)  # 原子发布，且不覆盖已有回执
        except FileExistsError:
            raise RelayError("回执文件已存在，拒绝覆盖：%s" % path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_record(path):
    """读回回执并校验：首行元数据完整、正文摘要一致、正文重新解析出的结论和元数据一致。"""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = RECORD_META.match(text)
    if not m or SEPARATOR not in text:
        raise RelayError("不是本通道生成的回执：%s" % path)
    meta = json.loads(m.group(1))
    body = text.split(SEPARATOR, 1)[1]
    if sha(body) != meta.get("reply_sha256") or parse_verdict(body) != meta.get("verdict"):
        raise RelayError("回执不完整或被改过：%s" % path)
    return meta


def record_id(meta):
    return "%s-%s" % (meta["review_id"], meta["reply_sha256"][:12])


def deliver(terminal, path, meta, confirm_not_received=False):
    """交付状态：sending（结果不明）/ done，记着交给了哪个终端。
    同一回执的交付全程持有进程锁（flock，进程退出自动释放），并发的第二个交付直接拒绝。
    已交付到同一终端的再调用返回 already、不重发；交给别的终端、或上次结果不明，都要人确认后才发。"""
    rid = record_id(meta)
    d = state_dir("delivered")
    state = os.path.join(d, rid + ".json")
    with open(os.path.join(d, rid + ".lock"), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RelayError("回执 %s 正由另一个进程交付，拒绝并发交付" % rid)
        if os.path.exists(state):
            with open(state, encoding="utf-8") as f:
                last = json.load(f)
            if last["status"] == "done" and last["terminal"] == terminal:
                return "already"
            if not confirm_not_received:
                if last["status"] == "done":
                    raise RelayError("回执 %s 已交付给终端 %s，不是 %s；确认原终端没处理、要改交新终端，"
                                     "再加 --confirm-not-received" % (rid, last["terminal"], terminal))
                raise RelayError("回执 %s 上次交付结果不明：先到终端核对有没有收到这个回执编号，"
                                 "确认没收到再加 --confirm-not-received 重试" % rid)

        def mark(status):
            with open(state, "w", encoding="utf-8") as f:
                json.dump({"status": status, "terminal": terminal, "record": os.path.abspath(path),
                           "time": now_iso()}, f, ensure_ascii=False)

        mark("sending")
        msg = ("ChatGPT 审核结论 REWORK（回执编号 %s），回执原文：%s 。同一回执编号只处理一次，处理过就忽略本条。"
               "请先读原文，按仓库 AGENTS.md 逐条核实属实后再返修；不属实的写明理由。"
               "返修完写新的送审包，不要自行合并或安装。" % (rid, os.path.abspath(path)))
        orca("terminal", "send", "--terminal", terminal, "--text", msg, "--enter")
        mark("done")
        return "sent"


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
    marker = Marker(packet)
    if marker.exists():
        raise RelayError("这份送审包（%s）上次发送结果不明（%s），先用 collect 核对，不要重发"
                         % (review_id(packet), marker.path))
    reply, url = send_and_wait(Page(a.worktree, a.page), packet, a.chat, a.timeout, a.poll, a.settle, marker)
    return finish(a, packet, reply, url, marker)


def cmd_collect(a):
    """等待中途断掉时，不重发、直接收取页面上已写完的回复：最新回复之前的用户消息必须带这份送审包的编号；
    有发送标记时页面会话必须是标记记下的会话；隔 settle 秒读两次都没在生成、文本和会话不变。"""
    packet = read_packet(a)
    marker = Marker(packet)
    expect = marker.load()["conversation"] if marker.exists() else None
    page = Page(a.worktree, a.page)
    reads = [page.state()]
    time.sleep(a.settle)
    reads.append(page.state())
    for st in reads:
        if st["streaming"]:
            raise RelayError("ChatGPT 还在生成，稍后再收")
        if not st["assistant"] or not st["last"].strip():
            raise RelayError("页面上没有可收取的回复")
        if expect and conversation(st["url"]) != expect:
            raise RelayError("页面会话和发送标记记下的（%s）不一致，拒绝收取" % expect)
        if not bubble_matches(st["last_user"], message_for(packet)):
            raise RelayError("最新回复前的用户消息不是这份送审包（%s），拒绝收取" % review_id(packet))
    if reads[0]["last"] != reads[1]["last"] or reads[0]["url"] != reads[1]["url"]:
        raise RelayError("回复还在变化，稍后再收")
    return finish(a, packet, reads[1]["last"], reads[1]["url"], marker)


def finish(a, packet, reply, url, marker):
    verdict = parse_verdict(reply)
    write_record(a.out, packet, reply, url, verdict)
    meta = read_record(a.out)  # 写后回读
    marker.clear()
    result = {"verdict": verdict, "record": a.out, "review_id": meta["review_id"], "url": url, "delivered": False}
    code = EXIT[verdict]
    if verdict == "REWORK" and a.deliver_to:
        try:
            deliver(a.deliver_to, a.out, meta)
            result["delivered"] = True
        except RelayError as e:
            result["deliver_error"] = "%s；回执已保存，用 deliver 重试" % e
            code = 3
    print(json.dumps(result, ensure_ascii=False))
    return code


def cmd_deliver(a):
    meta = read_record(a.record)
    if meta["verdict"] != "REWORK":
        raise RelayError("回执结论不是 REWORK，不交付：%s" % a.record)
    how = deliver(a.deliver_to, a.record, meta, a.confirm_not_received)
    print(json.dumps({"record": a.record, "record_id": record_id(meta), "delivered": True,
                      "already": how == "already"}, ensure_ascii=False))
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
    r.add_argument("--chat", required=True, help="必须在这个会话（或项目首页，表示开新会话）里发送；页面不在这里就拒绝")
    r.add_argument("--timeout", type=float, default=900)
    r.add_argument("--poll", type=float, default=3)
    d = sub.add_parser("deliver")
    d.add_argument("--record", required=True)
    d.add_argument("--deliver-to", required=True)
    d.add_argument("--confirm-not-received", action="store_true", help="已到终端核对过，上次交付确实没收到")
    a = ap.parse_args(argv)
    try:
        return {"status": cmd_status, "review": cmd_review, "collect": cmd_collect,
                "deliver": cmd_deliver}[a.cmd](a)
    except (RelayError, OSError) as e:
        print("relay 出错：%s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
