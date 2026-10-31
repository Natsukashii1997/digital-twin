#!/usr/bin/env python3
"""ORCA 自动循环驱动（RELAY-02，#37）：Claude 开发 → ChatGPT 聊天审核 → 返修 → 收口 / 通知常浩。

一次运行对应一个任务，所有中间产物放在运行目录（--run-dir）里，按轮次编号：
  task.md                  任务说明（首次运行时从 --task 复制；之后 --task 内容必须和它一致，否则拒绝续跑）
  state.json               任务摘要、开工时间、审核会话地址（第 1 轮在项目首页开新会话，之后固定在这个会话）、
                           每轮送出的送审包摘要（回执只有和它、和当前送审包都对得上才认）
  .lock                    运行目录锁：同一目录同时只能有一个驱动在跑，第二个直接退出
  errors.jsonl             每次出错停下的记录（含通知发没发出去）
  round-N-packet.md        Claude 写的第 N 轮送审包
  round-N.ready            Claude 写完送审包后创建的空文件；驱动只认它，不猜送审包写没写完
  round-N-回执.md          relay 落盘的 ChatGPT 回执
  handoff/<键>.json        交给 Claude 的消息：sending（结果不明）/ done，同一条只发一次
  notified/<键>            已发出的飞书通知，同一条只发一次

流程：交任务给 ORCA 终端里的 Claude → 等 round-1.ready → 送审（relay 自己核对会话、聊天模式和思考强度「高」）→
PASS 收口；REWORK 把回执交回 Claude、等下一轮 ready；返修超过 --max-rework 轮（D3-5 上限 2）或 NEEDS_HUMAN
就停下，用猛鬼兽 bot 飞书私聊通知常浩（只含结论和路径）。进程中断后用同一命令重跑即续跑：
已有回执的轮次核对通过后直接读回执，发送结果不明的用 collect 收，交给 Claude 的消息和通知都不重发。
每轮送审前和 PASS 收口前查一次任务开工后有没有被合并的 PR：执行方不许合并，查到就停下等常浩。

用法：
  relay_loop.py run --run-dir D --task 任务.md --repo 仓库路径 --worktree ORCA工作区 --page 页 \\
                    --project ChatGPT项目首页 --claude-term 终端 [--max-rework 2]

退出码：0 PASS；20 NEEDS_HUMAN（含返修超限、查到越权合并）；2 出错（已尝试通知并记入 errors.jsonl；排除后重跑同一命令续跑）；
3 同一运行目录已有驱动在跑，本次什么都没做。
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chatgpt_relay as relay  # noqa: E402

LARK = os.environ.get("DT_LARK_CLI", "lark-cli")
LARK_PROFILE = "cli_a96f5f9ac63adcd4"   # 猛鬼兽应用（同 hermes/scripts/report_data.py）
AGENT_ENV = ("HERMES", "OPENCLAW", "LARK_CHANNEL")   # lark-cli 看到这些变量会改读另一套配置，调用前去掉
OWNER_FILE = os.environ.get("DT_OWNER_FILE", "~/Library/Application Support/DigitalTwin/owner_open_id")
MAX_REWORK = 2   # D3-5：返修上限是政策，命令行只能调小；要放宽须常浩批准后改这里


def log(msg):
    print("%s %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def write_json(path, data):
    tmp = "%s.tmp-%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Run:
    def __init__(self, run_dir):
        self.dir = os.path.abspath(run_dir)
        for sub in ("handoff", "notified"):
            os.makedirs(os.path.join(self.dir, sub), exist_ok=True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def round(self, n):
        return self.path("round-%d-packet.md" % n), self.path("round-%d.ready" % n), self.path("round-%d-回执.md" % n)

    def lock(self):
        """运行目录锁（flock，进程退出自动释放），覆盖读任务、交付、送审、收口和通知的全过程。拿不到返回 None。"""
        f = open(self.path(".lock"), "a")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            f.close()
            return None
        return f

    def state(self):
        p = self.path("state.json")
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as f:
            try:
                return json.load(f)
            except ValueError:
                raise relay.RelayError("运行目录的 state.json 损坏，需要人核对：%s" % p)

    def save(self, **kv):
        st = self.state()
        st.update(kv)
        write_json(self.path("state.json"), st)


# 以下五个函数是和外部打交道的地方，单测替换它们。

class PromptBlocked(relay.RelayError):
    """终端上的 Claude 正停在权限或信任确认菜单，orca 拒收这条消息（确定没发出）。"""


def terminal_send(terminal, text):
    try:
        relay.orca("terminal", "send", "--terminal", terminal, "--text", text, "--enter")
    except relay.RelayError as e:
        if e.code == "agent_prompt_blocked":
            raise PromptBlocked(str(e))
        raise


def goto_chat(a, url):
    relay.orca("goto", "--worktree", a.worktree, "--page", a.page, "--url", url)
    page = relay.Page(a.worktree, a.page)
    for _ in range(40):
        time.sleep(0.5)
        try:
            st = page.state()
        except relay.RelayError:
            continue
        if relay.same_chat(st["url"], url) and st["composer"] and not st["streaming"]:
            return
    raise relay.RelayError("ChatGPT 页面 20 秒内没有到达 %s" % url)


def run_relay(argv):
    return relay.main(argv)


def merged_prs(a, since):
    """任务开工后被合并的 PR 编号（执行方不许合并，驱动自己也不合并）。"""
    try:
        r = subprocess.run(["gh", "pr", "list", "--state", "merged", "--search", "merged:>=" + since,
                            "--json", "number", "--limit", "50"], capture_output=True, text=True, timeout=60, cwd=a.repo)
        prs = json.loads(r.stdout) if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, ValueError):
        prs = None
    if not isinstance(prs, list):
        raise relay.RelayError("查不到任务开工后有没有被合并的 PR（gh pr list 失败），不能确认执行方没有越权")
    return sorted(p["number"] for p in prs)


def lark_send(text, key):
    with open(os.path.expanduser(OWNER_FILE), encoding="utf-8") as f:
        owner = f.read().strip()
    env = {k: v for k, v in os.environ.items() if not k.lstrip("_").startswith(AGENT_ENV)}
    try:
        r = subprocess.run([LARK, "--profile", LARK_PROFILE, "im", "+messages-send", "--as", "bot", "--user-id", owner,
                            "--text", text, "--idempotency-key", key], capture_output=True, text=True, timeout=60, env=env)
    except subprocess.TimeoutExpired:
        raise relay.RelayError("飞书通知 60 秒没有返回，结果不明（重跑会带同一幂等键补发）")
    try:
        ok = r.returncode == 0 and json.loads(r.stdout).get("ok") is True
    except ValueError:
        ok = False
    if not ok:
        raise relay.RelayError("飞书通知发送失败（退出码 %s）：%s" % (r.returncode, (r.stderr or r.stdout).strip()[:200]))


def hand_to_claude(run, a, key, text):
    """同一条消息只交一次：先记 sending 再发，发完记 done。sending 还在说明上次结果不明，要人核对终端后加 --confirm-not-received。
    orca 因确认菜单拒收（agent_prompt_blocked）是确定没发出：撤掉 sending 立即报错，人处理完菜单后重跑即可，不用确认。"""
    p = os.path.join(run.dir, "handoff", key + ".json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            try:
                status = json.load(f)["status"]
            except (ValueError, KeyError):
                status = "sending"   # 记录损坏按结果不明处理
        if status == "done":
            return False
        if not a.confirm_not_received:
            raise relay.RelayError("上次交给 Claude 的「%s」结果不明：先到终端核对有没有收到，确认没收到再加 --confirm-not-received" % key)
    write_json(p, {"status": "sending", "terminal": a.claude_term, "time": relay.now_iso()})
    try:
        terminal_send(a.claude_term, text)
    except PromptBlocked:
        os.unlink(p)
        raise relay.RelayError("Claude 终端（%s）正停在权限或信任确认菜单，「%s」没有发出；到终端处理菜单后重跑同一命令"
                               % (a.claude_term, key))
    write_json(p, {"status": "done", "terminal": a.claude_term, "time": relay.now_iso()})
    return True


def notify(run, key, text):
    """同一条通知只发一次；飞书侧再用幂等键兜底（进程在发出后、记账前中断时不重复）。"""
    p = os.path.join(run.dir, "notified", key)
    if os.path.exists(p):
        return False
    lark_send(text, "dt-relay-" + hashlib.sha256((run.dir + "\0" + key).encode()).hexdigest()[:32])
    with open(p, "w", encoding="utf-8") as f:
        f.write(relay.now_iso() + "\n")
    return True


def kickoff_text(run, a):
    packet, ready, _ = run.round(1)
    return ("【RELAY-02 自动推进 · 任务 %s】任务说明：%s 。在 %s 里按仓库 AGENTS.md 开发：开分支、写测试、提交、推送、开 PR；"
            "不要合并、不要安装、不改正式配置、不发飞书消息，被权限拦下就停下写进送审包，不要换办法绕过。做完后把送审包写到 %s ，内容含 PR 链接和 HEAD、做了什么、测试命令和原样输出、"
            "完整 diff、遗留问题；全部写好之后最后再创建空文件 %s 。驱动只看这个文件，它出现就开始送审。"
            % (os.path.basename(run.dir), run.path("task.md"), a.repo, packet, ready))


def rework_text(run, n, record):
    packet, ready, _ = run.round(n + 1)
    return ("【RELAY-02 自动推进 · 任务 %s】ChatGPT 第 %d 轮审核结论 REWORK，回执原文：%s 。同一条只处理一次。"
            "请读原文，按仓库 AGENTS.md 逐条核实：属实的返修（提交、推送），不属实的写明理由；超出任务范围的只记录不修。"
            "然后把新送审包写到 %s ，内容含逐条回应、测试命令和原样输出、本轮 diff；最后创建空文件 %s 。不要合并、不要安装。"
            % (os.path.basename(run.dir), n, record, packet, ready))


def wait_ready(ready, packet, a):
    deadline = time.monotonic() + a.claude_timeout
    while not os.path.exists(ready):
        if time.monotonic() > deadline:
            raise relay.RelayError("等 Claude 超时（%ss），没有出现 %s" % (int(a.claude_timeout), ready))
        time.sleep(a.poll)
    body = ""
    if os.path.exists(packet):
        with open(packet, encoding="utf-8") as f:
            body = f.read()
    if not body.strip():
        raise relay.RelayError("%s 已出现，但送审包 %s 不存在或为空" % (ready, packet))


def chat_url(project, conv):
    return re.sub(r"/project/?$", "", project.split("?")[0]) + "/c/" + conv


def record_url(record):
    with open(record, encoding="utf-8") as f:
        m = re.search(r"^- 会话：(\S+)$", f.read(), re.M)
    if not m:
        raise relay.RelayError("回执里没有会话地址：%s" % record)
    return m.group(1)


def check_record(run, n, text, record):
    """回执只有同时对得上才认（RELAY-02 P1-01）：回执元数据里的送审包摘要和送审编号 = 当前送审包；
    本运行目录为第 n 轮记下的送审包摘要和任务摘要 = 当前的；已固定审核会话时，回执的会话 = 该会话。
    对不上（旧任务、旧版本留下的回执，或送审包事后被改）一律拒绝复用，不判 PASS。"""
    meta = relay.read_record(record)
    st = run.state()
    ledger = st.get("rounds", {}).get(str(n), {})
    chat = relay.conversation(st["chat"]) if st.get("chat") else None
    problems = [what for what, ok in (
        ("回执里的送审包摘要", meta.get("packet_sha256") == relay.sha(text)),
        ("回执里的送审编号", meta.get("review_id") == relay.review_id(text)),
        ("本运行目录记下的送审包", ledger.get("packet_sha256") == relay.sha(text)),
        ("本运行目录记下的任务", ledger.get("task_sha256") == st.get("task_sha256")),
        ("审核会话", chat is None or meta.get("conversation") == chat)) if not ok]
    if problems:
        raise relay.RelayError("第 %d 轮回执 %s 和当前送审包或任务对不上（%s），拒绝复用、不判结论，需要人核对"
                               % (n, record, "、".join(problems)))
    return meta


def review_round(run, a, n):
    """送审第 n 轮，返回回执结论。已有回执核对后再读；送审包有发送标记（上次结果不明）就到标记的会话里 collect，不重发。"""
    packet, ready, record = run.round(n)
    with open(packet, encoding="utf-8") as f:
        text = f.read()
    if not os.path.exists(record):
        st = run.state()
        rounds = st.get("rounds", {})
        mine = {"packet_sha256": relay.sha(text), "task_sha256": st.get("task_sha256")}
        if rounds.get(str(n), mine) != mine:
            raise relay.RelayError("第 %d 轮送审包在送出后被改过，需要人核对：%s" % (n, packet))
        rounds[str(n)] = mine
        run.save(rounds=rounds)   # 先记账再送审：回执落盘时账上一定有这一轮
        marker = relay.Marker(text)
        chat = st.get("chat") or a.project
        base = ["--worktree", a.worktree, "--page", a.page, "--packet", packet, "--out", record]
        if marker.exists():
            raw = marker.load().get("conversation")
            conv = raw and relay.conversation("/c/" + raw)   # 没绑定或只是临时编号都不算
            if not conv:
                raise relay.RelayError("第 %d 轮发送结果不明，且没有绑定会话，需要人到 ChatGPT 页面核对" % n)
            goto_chat(a, chat_url(a.project, conv))
            log("第 %d 轮上次发送结果不明，collect 收取" % n)
            deadline = time.monotonic() + a.review_timeout
            while True:   # 回复可能还在写（思考强度「高」要几分钟）：没收到就隔一会儿再收，直到超时
                code = run_relay(["collect", *base])
                if os.path.exists(record) or time.monotonic() > deadline:
                    break
                time.sleep(a.poll * 3)
        else:
            goto_chat(a, chat)
            log("第 %d 轮送审（%s）" % (n, chat))
            code = run_relay(["review", *base, "--chat", chat, "--timeout", str(a.review_timeout)])
        if not os.path.exists(record):
            raise relay.RelayError("第 %d 轮送审没有拿到回执（relay 退出码 %s）" % (n, code))
    meta = check_record(run, n, text, record)
    if not run.state().get("chat"):
        run.save(chat=record_url(record))
    return meta["verdict"], record


def init_task(run, a):
    """首次运行把 --task 复制成 task.md 并记下摘要；续跑时 --task 必须和 task.md 一致（同一运行目录只跑同一个任务）。"""
    with open(a.task, encoding="utf-8") as f:
        body = f.read()
    task = run.path("task.md")
    if os.path.exists(task):
        with open(task, encoding="utf-8") as f:
            if f.read() != body:
                raise relay.RelayError("运行目录 %s 属于另一个任务（task.md 和 --task 内容不同），拒绝续跑" % run.dir)
    else:
        with open(task, "w", encoding="utf-8") as f:
            f.write(body)
    st = run.state()
    if st.get("task_sha256", relay.sha(body)) != relay.sha(body):
        raise relay.RelayError("运行目录记下的任务摘要和 task.md 不一致，需要人核对：%s" % run.dir)
    if "task_sha256" not in st:
        run.save(task_sha256=relay.sha(body))
    return body.split("\n", 1)[0].lstrip("# ").strip() or os.path.basename(run.dir)


def guard(run, a, head, n):
    """执行方不许合并：任务开工后有 PR 被合并就停下等常浩（驱动自己不合并，合并由 PASS 之后的闸门负责）。"""
    since = run.state().get("kickoff_at")
    found = merged_prs(a, since) if since else []
    if found:
        notify(run, "merged-%s" % "-".join(map(str, found)),
               "%s：第 %d 轮时发现任务开工后有 PR 被合并（%s），执行方不许合并，自动推进已停下，需要你核对。"
               % (head, n, "、".join("#%d" % x for x in found)))
        return True
    return False


def record_failure(run, n, e, notified, notify_error):
    """出错停下的状态落盘（P1-05）：通知发不出去时，这里仍留着出错和没通知上的记录。"""
    entry = {"time": relay.now_iso(), "round": n, "error": "%s: %s" % (type(e).__name__, e),
             "notified": notified, "notify_error": notify_error}
    try:
        with open(run.path("errors.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e2:
        log("出错记录也写不进去：%s" % e2)


def run_task(a):
    run, lock, n = None, None, 0
    head = "【数字分身·自动推进】%s" % os.path.basename(os.path.abspath(a.run_dir))
    try:   # 受控异常边界从建运行目录开始（P1-05）：任何异常都走下面的出错处理，不直接抛出
        run = Run(a.run_dir)
        lock = run.lock()
        if lock is None:
            log("运行目录 %s 已有驱动在跑，本次退出，什么都没做" % run.dir)
            return 3
        head = "【数字分身·自动推进】%s" % init_task(run, a)
        n = 1
        if not run.state().get("kickoff_at"):
            run.save(kickoff_at=datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        if hand_to_claude(run, a, "kickoff", kickoff_text(run, a)):
            log("任务已交给 Claude（%s）" % a.claude_term)
        while True:
            packet, ready, record = run.round(n)
            if not os.path.exists(record):
                log("等 Claude 写第 %d 轮送审包" % n)
                wait_ready(ready, packet, a)
                if guard(run, a, head, n):
                    return 20
            verdict, record = review_round(run, a, n)
            log("第 %d 轮结论 %s：%s" % (n, verdict, record))
            if verdict == "PASS":
                if guard(run, a, head, n):
                    return 20
                notify(run, "pass", "%s：第 %d 轮 ChatGPT 审核 PASS，等合并闸门。回执：%s" % (head, n, record))
                return 0
            if verdict == "NEEDS_HUMAN":
                notify(run, "needs-human-%d" % n, "%s：第 %d 轮 ChatGPT 判 NEEDS_HUMAN，需要你裁决。回执：%s" % (head, n, record))
                return 20
            if n > a.max_rework:
                notify(run, "over-limit", "%s：返修已到 %d 轮上限（D3-5）仍是 REWORK，需要你裁决。最后回执：%s"
                       % (head, a.max_rework, record))
                return 20
            if hand_to_claude(run, a, "rework-%d" % n, rework_text(run, n, record)):
                log("第 %d 轮回执已交给 Claude 返修" % n)
            n += 1
    except Exception as e:   # noqa: BLE001 —— 无人值守：没预料到的异常也要停下、落盘、尽量通知
        log("出错：%s: %s" % (type(e).__name__, e))
        if run is None:
            return 2   # 连运行目录都建不起来：没处记账，也就不发通知（无法去重）
        notified, notify_error = False, None
        try:
            notify(run, "error-%d-%s" % (n, relay.sha(str(e))[:8]), "%s：第 %d 轮自动推进停下了：%s。排除后重跑同一命令续跑。"
                   % (head, n, e))
            notified = True
        except Exception as e2:   # noqa: BLE001
            notify_error = "%s: %s" % (type(e2).__name__, e2)
            log("通知也失败了：%s" % notify_error)
        record_failure(run, n, e, notified, notify_error)
        return 2
    finally:
        if lock:
            lock.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="ORCA 自动循环驱动")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--run-dir", required=True)
    r.add_argument("--task", required=True, help="任务说明（首次运行时复制进运行目录）")
    r.add_argument("--repo", required=True, help="Claude 干活的仓库或 worktree 路径")
    r.add_argument("--worktree", required=True, help="ChatGPT 标签所在的 ORCA 工作区选择器")
    r.add_argument("--page", required=True, help="ChatGPT 标签的 browserPageId")
    r.add_argument("--project", required=True, help="ChatGPT 项目首页地址（第 1 轮在这里开新会话）")
    r.add_argument("--claude-term", required=True, help="ORCA 终端里跑 Claude 的终端 id")
    r.add_argument("--max-rework", type=int, default=MAX_REWORK, choices=range(MAX_REWORK + 1),
                   help="返修上限（D3-5 最多 %d 轮，只能调小）" % MAX_REWORK)
    r.add_argument("--claude-timeout", type=float, default=4 * 3600)
    r.add_argument("--review-timeout", type=float, default=2400)
    r.add_argument("--poll", type=float, default=10)
    r.add_argument("--confirm-not-received", action="store_true", help="已到终端核对过，上次交给 Claude 的消息确实没收到")
    a = ap.parse_args(argv)
    return run_task(a)


if __name__ == "__main__":
    sys.exit(main())
