"""派单：开单、跑单、合并、清理、列表（只用标准库）。Hermes 的 workshop 技能调用它。

  python3 dispatch.py new   --project ID [--tool claude|codex] --mode read|write --task-file F
                           （不带 --tool 就用 AI 接入配置里车间的执行方，见 ai_access.py；回执和台账记下怎么选的）
  python3 dispatch.py run   单号 [--followup 追加说明文件]   （放在后台跑；结束后读 out/receipt.json）
  python3 dispatch.py pr    单号     （写入单跑完、回报前调用：推分支、开 PR）
  python3 dispatch.py merge 单号 [--push-main 提交号]   （常浩说“合并”后才调用）
  python3 dispatch.py clean 单号     （合并后或放弃时，删 worktree 和分支）
  python3 dispatch.py list [--all]

每张单一个目录 ~/.hermes/dispatch/<单号>/：order.json（台账）、task.md、out/。
续跑前把已有回执归档为 out/receipt-N.json；out/receipt.json 仍是最新回执。
写入单在 ~/.hermes/worktrees/<项目>/<单号> 建 worktree，分支 hermes/<单号>。
名册里有 github 的项目（工程流程见 agent-kit/工程流程.md）：分支从 origin 的主分支拉出，开单时建 issue，
跑完用 pr 开 PR，merge 走 GitHub squash 合并再拉齐主目录；本地主分支有没推的提交时先列给常浩，
他确认后带 --push-main 再合。没有 github 的项目照旧从本地主分支拉出、本地快进合并。
并发：同时最多 2 张单在跑；同一个仓库同一时间只跑 1 张。开单编号、开跑前的“并发检查 + 写 running”都在派单根目录的
文件锁 .dispatch.lock 里做（只锁这一小段，不包住车间执行；锁顺序固定：派单锁 → 状态库事务，从不反向）。
run 时车间进程放进自己的进程组（start_new_session），order.json 记下 dispatch 自己的进程身份，worker 自己写 out/worker.json。
new --sched-key：调度器开单时带的关联键（B2.2），写进 order.json 和单目录里的 sched_key 文件；手工派单不带。
带关联键时在派单锁里、建目录前读一次状态库（$DT_STATE_DB）核对预留：只有仍是 claimed、项目一致、还没单号的才开单；
状态库不存在、读不了、预留已收口（void / released / unsafe / linked）一律拒绝（B2.2 返修 F1：迟到的旧关联键开不出单）。
同一把锁里接着查派单根目录直接子目录：已有同键单（sched_key 标记或 order.json 任一带同键，含还没写 order.json 的半截目录）
就拒绝并列出单号，读不清某个目录也拒绝；同一派单根目录、同一关联键最多建一个单目录（B2.2 终审 P0），不删、不自动关联。
每条命令最后输出一行 JSON，字段 ok 表示成败、msg 是给常浩看的一句话。
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
ROOT = HOME / ".hermes/dispatch"
WT_ROOT = HOME / ".hermes/worktrees"
WORKER = HOME / ".hermes/scripts/run_worker.py"
ROSTER = Path(os.environ.get("DT_ROSTER", "~/Documents/ChatGPT/个人/work/digital-twin/projects.json")).expanduser()
NOTIFY_LOG = Path(os.environ.get("DT_STATE_NOTIFY_LOG", "~/Library/Application Support/DigitalTwin/state-notify.log")).expanduser()
MAX_RUNNING = 2
LOCK_NAME = ".dispatch.lock"   # 派单根目录下的文件锁（twin_scheduler.py 恢复时用同一把）
LOCK_WAIT = 30                 # 秒：等锁上限；锁只在编号、并发检查这类小段持有，等不到说明有异常
SCHED_KEY = re.compile(r"S[0-9A-Za-z._-]{1,96}")
# 推送前检查只查密钥和 Token（M3 B5-3）；受限内容靠规矩防
SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{30,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[abp]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY"
    r"|(?i:app_?secret|client_secret|api[_-]?key|access_token|refresh_token|password|passwd|secret)[\"' ]*[:=][\"' ]*[A-Za-z0-9_./+-]{16,}")


def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


def git(cwd, *args, check=False):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        done(False, f"git {' '.join(args)} 失败：{(r.stderr or r.stdout).strip()[:300]}")
    return r


def gh(*args):
    """调用 GitHub CLI；找不到 gh 或超时也按失败返回，由调用方决定停不停。"""
    exe = shutil.which("gh", path=os.pathsep.join([os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"]))
    if not exe:
        return subprocess.CompletedProcess(["gh"], 127, "", "找不到 gh 命令")
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(["gh"], 124, "", "gh 超时")


def gh_err(r):
    return (r.stderr or r.stdout).strip()[:300]


def secret_hits(cwd, rng):
    """要推的提交里新增的行有没有像密钥的；只报文件和开头几个字符，不把密钥本身转出去。"""
    r = git(cwd, "log", "-p", "--no-color", "--format=commit %h", rng)
    if r.returncode != 0:
        done(False, f"凭据扫描读取 git log 失败（退出码 {r.returncode}），没有推送；请排查后重试")
    log = r.stdout
    hits, file = [], None
    for line in log.splitlines():
        if line.startswith("+++ b/"):
            file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            hits += [f"{file}：{m.group(0)[:6]}…" for m in SECRET.finditer(line)]
    return hits[:5]


def task_title(text, no):
    """issue 和 PR 的标题：取任务说明里的“目标”，没有就取第一行。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, l in enumerate(lines):
        if m := re.match(r"^[#*\-\s]*目标[*\s]*[：:]\s*(.+)", l):
            return f"{m[1].strip('* ')[:80]}（派单 {no}）"
        if re.fullmatch(r"#+\s*目标\s*", l) and i + 1 < len(lines):
            return f"{lines[i + 1].strip('#*- ')[:80]}（派单 {no}）"
    return f"{(lines[0].strip('#*- ') if lines else 'Hermes 派单')[:80]}（派单 {no}）"


def issue_body(text, no, tool):
    return f"{text.strip()}\n\n---\nHermes 派单 {no} · 车间 {tool} · 分支 `hermes/{no}`。跑完开 PR，交接写在 PR 里。"


def project(pid):
    for p in json.loads(ROSTER.read_text())["projects"]:
        if p["id"] == pid:
            return {**p, "path": os.path.expanduser(p["path"])}
    done(False, f"项目 {pid} 不在名册里")


def load(no):
    f = ROOT / no / "order.json"
    if not f.exists():
        done(False, f"没有这张单：{no}")
    return json.loads(f.read_text())


def save(o):
    o["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    (ROOT / o["no"] / "order.json").write_text(json.dumps(o, ensure_ascii=False, indent=2))
    notify_state(o["no"])


def notify_state(no):
    """B2.1 T04：台账写入后通知状态库同步这一张单（写入后通知为主，twin_state.py reconcile 定时对账兜底）。
    默认关闭：DT_STATE_NOTIFY=1 才动作；状态库文件不存在就跳过、绝不建库；任何异常只追加一行到 NOTIFY_LOG，
    不改台账、不改命令结果、不往 stdout / stderr 写——派单本身永远不受影响。"""
    if os.environ.get("DT_STATE_NOTIFY") != "1":
        return None
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import twin_state   # 同目录（安装副本在 ~/.hermes/scripts/）
        out = twin_state.notify_order(ROOT / no, dispatch_dir=ROOT)    # 内部可信通知：根目录是自己的 ROOT，不是信封说的
        if out.get("conflicts"):
            _notify_log(f"{no} 台账与状态库冲突，没有应用，请人工核对：{'、'.join(out['conflicts'])}")
        if out.get("skipped"):
            _notify_log(f"{no} 没有同步：{'；'.join(str(x) for x in out['skipped'])}")
        return out
    except Exception as e:  # noqa: BLE001 —— 通知失败不能影响派单
        _notify_log(f"{no} 通知状态库失败：{type(e).__name__}: {str(e)[:200]}")
        return None


def _notify_log(line):
    try:
        NOTIFY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(NOTIFY_LOG, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass


def orders():
    return [json.loads(f.read_text()) for f in sorted(ROOT.glob("D*/order.json"))]


@contextlib.contextmanager
def dispatch_lock():
    """派单根目录的排他文件锁（flock，进程退出自动释放）。持锁期间不调外部命令、不跑车间。"""
    ROOT.mkdir(parents=True, exist_ok=True)
    with open(ROOT / LOCK_NAME, "a") as f:
        deadline = dt.datetime.now() + dt.timedelta(seconds=LOCK_WAIT)
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if dt.datetime.now() > deadline:
                    done(False, f"派单锁 {LOCK_NAME} 等了 {LOCK_WAIT} 秒还被占着，没有动作，请检查是否有卡住的派单进程")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def check_sched_key(key, project):
    """调度单的最后一道防线：持派单锁时只读状态库（锁顺序派单锁 → 状态库，与调度器一致）。不合格直接 done(False)。"""
    try:
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        import twin_state
        with twin_state.Store(readonly=True) as s:
            cl = s.get_claim(key)
    except Exception as e:  # noqa: BLE001 —— 核对不了就不开调度单
        done(False, f"调度关联键 {key} 核对不了（{type(e).__name__}: {str(e)[:120]}），没有开单")
    if cl["state"] != "claimed" or cl["order_no"]:
        done(False, f"调度关联键 {key} 的预留已是 {cl['state']}" + (f"（单号 {cl['order_no']}）" if cl["order_no"] else "")
             + "，不再开单")
    if cl["project"] != project:
        done(False, f"调度关联键 {key} 属于项目 {cl['project']}，不是 {project}，没有开单")


def check_sched_key_unused(key):
    """同键只开一张：持派单锁时扫派单根目录直接子目录（不跟符号链接、不往下扫），sched_key 标记或 order.json 带同键即拒绝。
    标记在建目录的同一把锁里写，所以只有目录、两样都没有的是手工单开到一半，不算占用；读不清的目录一律拒绝。"""
    taken, unclear = [], []
    for d in sorted(ROOT.glob("D*")):
        if d.is_symlink() or not d.is_dir():
            continue
        try:
            keys = set()
            if (d / "sched_key").exists():
                keys.add((d / "sched_key").read_text().strip())
            if (d / "order.json").exists():
                keys.add(json.loads((d / "order.json").read_text()).get("sched_key"))
        except (OSError, ValueError, AttributeError, TypeError):
            unclear.append(d.name)
            continue
        if key in keys:
            taken.append(d.name)
    if taken:
        done(False, f"调度关联键 {key} 已开过单 {'、'.join(taken)}，不再开第二张；请用 twin_scheduler.py recover 核对",
             existing=taken)
    if unclear:
        done(False, f"派单目录 {'、'.join(unclear)} 读不清是否占用调度关联键 {key}，没有开单；请人工核对后再用 recover 收口")


def busy_reason(o):
    """并发检查：返回 (在跑的单, 是否同仓库)；名额有空返回 None。"""
    running = [x for x in orders() if x["status"] == "running" and x["no"] != o["no"]]
    if len(running) >= MAX_RUNNING or any(x["project"] == o["project"] for x in running):
        return running, any(x["project"] == o["project"] for x in running)
    return None


def queue_busy(o, busy):
    running, same = busy
    o["status"] = "queued"
    save(o)
    done(False, f"{o['no']} 排队中：现在有 {len(running)} 张单在跑" + ("，且同一仓库已有单在跑" if same else ""), busy=True)


def next_no():
    day = dt.date.today().strftime("%Y%m%d")
    seq = [int(d.name.split("-")[1]) for d in ROOT.glob(f"D{day}-*") if d.name.split("-")[1].isdigit()]
    return f"D{day}-{max(seq, default=0) + 1}"


def pick_tool(a):
    """返回 (车间, 怎么选的)。点名优先；调度器开单带的是任务负责方；都没有就读 AI 接入配置，读不了就停（不猜）。"""
    if a.tool:
        return a.tool, "调度器任务负责方" if a.sched_key else "点名"
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import ai_access
    try:
        return ai_access.resolve("workshop")
    except ai_access.AccessError as e:
        done(False, f"没点名车间，又{e}，没有开单")


def cmd_new(a):
    a.tool, chosen_by = pick_tool(a)
    p = project(a.project)
    if a.mode == "write" and not p.get("write_allowed"):
        done(False, f"名册不允许往 {p['name']} 写入")
    hub = a.mode == "write" and p.get("github")
    if hub:  # 从 origin 的主分支拉：本地主分支上别的会话没推的提交不会混进 PR
        git(p["path"], "fetch", "origin", p["main"], check=True)
    if a.sched_key and not SCHED_KEY.fullmatch(a.sched_key):
        done(False, f"调度关联键格式不对：{a.sched_key}")
    with dispatch_lock():   # 编号 + 建目录原子：两个进程同时开单不会撞号
        if a.sched_key:     # 迟到的旧关联键在这里被拦下（作废预留也在这把锁里做，二者不会交叉）
            check_sched_key(a.sched_key, p["id"])
            check_sched_key_unused(a.sched_key)   # 同键已建过目录就拒（检查和下面建目录在同一把锁里）
        no = next_no()
        d = ROOT / no
        d.mkdir(parents=True)
        if a.sched_key:     # 关联键先落盘：开单中途失败也能被恢复按键找到
            (d / "sched_key").write_text(a.sched_key)
    shutil.copy(a.task_file, d / "task.md")
    o = {"no": no, "project": p["id"], "tool": a.tool, "tool_source": chosen_by, "mode": a.mode, "status": "new",
         "created": dt.datetime.now().isoformat(timespec="seconds"), "cwd": p["path"]}
    if a.sched_key:
        o["sched_key"] = a.sched_key
    note = ""
    if a.mode == "write":
        wt = WT_ROOT / p["id"] / no
        wt.parent.mkdir(parents=True, exist_ok=True)
        start = f"refs/remotes/origin/{p['main']}" if hub else f"refs/heads/{p['main']}"
        git(p["path"], "worktree", "add", "--no-track", "-b", f"hermes/{no}", str(wt), start, check=True)
        # 依赖不进 git：主仓库有 node_modules 就链接过去，车间能直接跑测试
        if (Path(p["path"]) / "node_modules").is_dir() and not (wt / "node_modules").exists():
            (wt / "node_modules").symlink_to(Path(p["path"]) / "node_modules")
        o.update(cwd=str(wt), branch=f"hermes/{no}", base=git(p["path"], "rev-parse", start).stdout.strip())
    if hub:  # 写入单自动建 issue（M3 B5-1）；建不成不拦开单，回报里说明
        text = (d / "task.md").read_text(encoding="utf-8")
        r = gh("issue", "create", "-R", p["github"], "-t", task_title(text, no), "-b", issue_body(text, no, a.tool),
               "-l", "ready-for-agent")
        m = re.search(r"/issues/(\d+)", r.stdout)
        if r.returncode == 0 and m:
            o.update(issue=int(m[1]), issue_url=m.string[:m.end()].split()[-1])
            note = f"，issue #{o['issue']}"
        else:
            o["issue_error"] = gh_err(r)
            note = f"，issue 没建成：{o['issue_error']}"
    save(o)
    done(True, f"已开单 {no}（{p['name']} · {a.tool}，{chosen_by} · {'写入' if a.mode == 'write' else '只读'}）{note}", order=o)


def archive_receipt(receipt, content):
    numbers = [int(m[1]) for p in receipt.parent.iterdir()
               if (m := re.fullmatch(r"receipt-([0-9]+)\.json", p.name))]
    archive = receipt.with_name(f"receipt-{max(numbers, default=0) + 1}.json")
    # 先写完整的独立副本，再原子发布；link 不覆盖已占用的路径。
    # 不能直接链接 receipt.json：worker 会原位截断它，连带改坏历史。
    with tempfile.NamedTemporaryFile(dir=receipt.parent, prefix=".receipt-", suffix=".tmp") as backup:
        backup.write(content)
        backup.flush()
        os.fsync(backup.fileno())
        os.link(backup.name, archive)


def cmd_run(a):
    o = load(a.no)
    if o["status"] in ("running", "merged", "cleaned"):
        done(False, f"{a.no} 当前状态是 {o['status']}，不能再跑")
    busy = busy_reason(o)
    if busy:
        queue_busy(o, busy)
    task = ROOT / a.no / "task.md"
    if a.followup:  # 卡住后续跑：在原会话里接着做
        task = ROOT / a.no / f"followup-{dt.datetime.now():%H%M%S}.md"
        shutil.copy(a.followup, task)
    receipt = ROOT / a.no / "out/receipt.json"
    resume = None
    if a.followup:
        try:
            if receipt.is_symlink():
                raise ValueError("receipt.json 是符号链接，不能安全续跑")
            try:
                previous = receipt.read_bytes()
            except FileNotFoundError:
                previous = None  # 兼容原行为：没有回执时从新会话开始。
            if previous is not None:
                rc = json.loads(previous)
                if not isinstance(rc, dict):
                    raise ValueError("receipt.json 必须是 JSON 对象")
                resume = rc.get("session_id")
                archive_receipt(receipt, previous)
        except (OSError, ValueError) as e:
            done(False, f"{a.no} 续跑前读取或归档回执失败，未启动车间：{e}")
    if o.get("issue") and not a.followup:  # 确认门里改过任务的话，让 issue 跟上开跑时的版本；失败不拦
        r = gh("issue", "edit", str(o["issue"]), "-R", project(o["project"])["github"],
               "-b", issue_body(task.read_text(encoding="utf-8"), o["no"], o["tool"]))
        o["issue_sync_error"] = gh_err(r) if r.returncode else None
    with dispatch_lock():   # 锁里重读、重查再写 running：两个 run 同时进来不会都看到空位
        now = load(a.no)["status"]
        if now in ("running", "merged", "cleaned"):
            done(False, f"{a.no} 当前状态是 {now}，不能再跑")
        busy = busy_reason(o)
        if busy:
            queue_busy(o, busy)
        # dispatch 自己的身份：pid + 主机 + 写 running 的时刻（进程一定早于此刻启动，核对时据此排除 PID 复用）
        o.update(status="running", run_started=dt.datetime.now().isoformat(timespec="seconds"),
                 run_host=socket.gethostname(), dispatch_pid=os.getpid())
        save(o)
    cmd = [sys.executable, str(WORKER), "--tool", o["tool"], "--cwd", o["cwd"], "--task-file", str(task),
           "--out", str(ROOT / a.no / "out"), "--mode", o["mode"], "--timeout", str(a.timeout),
           "--chosen-by", o.get("tool_source") or "未记录"]
    if resume:
        cmd += ["--resume", resume]
    r = subprocess.run(cmd, capture_output=True, text=True, start_new_session=True)   # 车间自成进程组，取消时只停它
    o["status"] = {0: "done", 2: "guard"}.get(r.returncode, "failed")
    o["exit_code"] = r.returncode
    save(o)
    rc = json.loads(receipt.read_text()) if receipt.exists() else {}
    if r.returncode == 4 and rc.get("refused") == "executor_unavailable":   # 执行方不可用：没开跑，原样报给常浩
        done(False, f"{a.no} 没开跑：{rc.get('reason')}", receipt=rc, unavailable=True)
    done(r.returncode == 0, f"{a.no} 跑完：{o['status']}", receipt={k: v for k, v in rc.items() if k != "summary"},
         stderr_tail=r.stderr[-500:] if r.returncode else "")


def pr_body(o, p, files, summary):
    listed = "\n".join(f"- `{f}`" for f in files) or "- （没有）"
    return f"""{f"Closes #{o['issue']}" if o.get("issue") else "（没有关联 issue）"}

Hermes 派单 {o['no']} · 车间 {o['tool']} · 分支 `{o['branch']}`

## 做了什么
{summary.strip() or "（回执里没有车间总结）"}

## 提交的文件
{listed}

## 测试
- 车间自报见上面的总结；合并时脚本会在变基后再跑名册里的测试命令（{p.get("test") or "名册没写测试命令"}）。

## 遗留 / 范围外发现
- 见上面的总结。

## 要常浩定的事
- 合并吗？在飞书回“合并”。
"""


def cmd_pr(a):
    o = load(a.no)
    p = project(o["project"])
    if o["mode"] != "write" or o["status"] != "done" or not p.get("github"):
        done(False, f"{a.no} 不是已完成、有 GitHub 远端的写入单（状态 {o['status']}），不开 PR")
    wt, remote = o["cwd"], f"refs/remotes/origin/{p['main']}"
    if git(wt, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        done(False, f"{a.no} 的 worktree 还有没提交的改动，先处理再开 PR")
    if git(wt, "rev-parse", "HEAD").stdout.strip() == o.get("base"):
        done(False, f"{a.no} 没有任何提交，不开 PR")
    git(wt, "fetch", "origin", p["main"], check=True)
    if hits := secret_hits(wt, f"{remote}..HEAD"):
        done(False, f"{a.no} 的提交里像有密钥或 Token，没有推送：{'；'.join(hits)}", secret_hits=hits)
    r = git(wt, "push", "--force-with-lease", "-u", "origin", f"HEAD:refs/heads/{o['branch']}")
    if r.returncode != 0:
        done(False, f"{a.no} 推分支失败：{(r.stderr or r.stdout).strip()[:300]}")
    if o.get("pr"):
        done(True, f"{a.no} 已把新提交推到 PR #{o['pr']}：{o['pr_url']}", pr_url=o["pr_url"])
    rc_file = ROOT / a.no / "out/receipt.json"
    summary = (json.loads(rc_file.read_text()).get("summary") or "") if rc_file.exists() else ""
    files = git(wt, "diff", "--name-only", f"{remote}...HEAD").stdout.split()
    text = (ROOT / a.no / "task.md").read_text(encoding="utf-8")
    r = gh("pr", "create", "-R", p["github"], "-B", p["main"], "-H", o["branch"],
           "-t", task_title(text, a.no), "-b", pr_body(o, p, files, summary))
    m = re.search(r"https://\S+/pull/(\d+)", r.stdout)
    if r.returncode != 0 or not m:
        done(False, f"{a.no} 分支已推送，PR 没开成：{gh_err(r)}")
    o.update(pr=int(m[1]), pr_url=m[0])
    save(o)
    done(True, f"{a.no} 已开 PR：{o['pr_url']}", pr_url=o["pr_url"])


def merge_github(a, o, p):
    """常浩说“合并”后：推送前检查 → 变基 → 测试 → GitHub squash 合并 → 主目录拉齐（工程流程第 5 节）。"""
    repo, wt, main = p["path"], o["cwd"], p["main"]
    local, remote = f"refs/heads/{main}", f"refs/remotes/origin/{main}"
    if not o.get("pr"):
        done(False, f"{a.no} 还没开 PR，先执行 pr {a.no}")
    git(repo, "fetch", "origin", main, check=True)
    ahead = git(repo, "log", "--format=%h %s", f"{remote}..{local}").stdout.splitlines()
    behind = git(repo, "rev-list", f"{local}..{remote}").stdout.split()
    if ahead and behind:  # M3 B5-4：分叉不自动处理
        done(False, f"{p['name']} 本地 {main} 和 GitHub 分叉了（本地多 {len(ahead)} 个、远端多 {len(behind)} 个提交），"
             "没有合并，请人工处理", unpushed=ahead)
    if ahead:  # 多半是别的会话提交了没推：列给常浩，他回“推”后带 --push-main 再来
        tip = git(repo, "rev-parse", local).stdout.strip()
        if not (a.push_main and len(a.push_main) >= 7 and tip.startswith(a.push_main)):
            why = "你确认之后本地又有了新提交，" if a.push_main else ""
            done(False, f"{why}{p['name']} 本地 {main} 有 {len(ahead)} 个没推到 GitHub 的提交：{'；'.join(ahead[:10])}。"
                 f"确认能推，再执行 merge {a.no} --push-main {tip[:12]}",
                 unpushed=ahead, push_main=tip[:12], need_confirm=True)
        if hits := secret_hits(repo, f"{remote}..{local}"):
            done(False, f"{p['name']} 本地 {main} 没推的提交里像有密钥或 Token，没有推送：{'；'.join(hits)}", secret_hits=hits)
        r = git(repo, "push", "origin", f"{local}:refs/heads/{main}")
        if r.returncode != 0:
            done(False, f"推本地 {main} 失败，没有合并：{(r.stderr or r.stdout).strip()[:300]}")
    r = git(wt, "rebase", remote)
    if r.returncode != 0:
        git(wt, "rebase", "--abort")
        done(False, f"{a.no} 和最新 {main} 有冲突，变基失败，已撤回变基。需要人工处理或派单解决冲突")
    if p.get("test"):
        t = subprocess.run(p["test"], shell=True, cwd=wt, capture_output=True, text=True, timeout=1800)
        (ROOT / a.no / "merge-test.log").write_text(t.stdout + t.stderr)
        if t.returncode != 0:
            done(False, f"{a.no} 合并前测试没过（{p['test']}），没有合并。日志：{ROOT / a.no / 'merge-test.log'}")
    # 续跑、变基后 HEAD 可能已变，不能沿用开 PR 时的扫描结果。
    if hits := secret_hits(wt, f"{remote}..HEAD"):
        done(False, f"{a.no} 的提交里像有密钥或 Token，没有推送：{'；'.join(hits)}", secret_hits=hits)
    r = git(wt, "push", "--force-with-lease", "origin", f"HEAD:refs/heads/{o['branch']}")
    if r.returncode != 0:
        done(False, f"{a.no} 变基后推分支失败，没有合并：{(r.stderr or r.stdout).strip()[:300]}")
    head = git(wt, "rev-parse", "HEAD").stdout.strip()
    r = gh("pr", "merge", str(o["pr"]), "-R", p["github"], "--squash", "--match-head-commit", head)
    if r.returncode != 0:
        done(False, f"{a.no} 在 GitHub 上合并 PR #{o['pr']} 失败：{gh_err(r)}")
    o["status"] = "merged"
    save(o)
    git(repo, "fetch", "origin", main)
    o["merged_to"] = git(repo, "rev-parse", remote).stdout.strip()
    save(o)
    r = git(repo, "merge", "--ff-only", remote)
    if r.returncode != 0:
        done(True, f"{a.no} 已在 GitHub 合并进 {main}（{o['merged_to'][:7]}），但本地主目录没拉下来"
             f"（多半和未提交的改动重叠）：{(r.stderr or r.stdout).strip()[:200]}。没有 stash、没有 reset，请人工处理",
             synced=False, tested=bool(p.get("test")))
    done(True, f"{a.no} 已 squash 合并进 {p['name']} 的 {main}（{o['merged_to'][:7]}），主目录已拉齐",
         synced=True, tested=bool(p.get("test")))


def cmd_merge(a):
    o = load(a.no)
    if o["mode"] != "write" or o["status"] != "done":
        done(False, f"{a.no} 不是已完成的写入单（状态 {o['status']}），不能合并")
    p = project(o["project"])
    repo, wt, main = p["path"], o["cwd"], p["main"]
    if git(wt, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        done(False, f"{a.no} 的 worktree 还有没提交的改动，先处理再合并")
    if git(repo, "branch", "--show-current").stdout.strip() != main:
        done(False, f"{p['name']} 主仓库当前不在 {main} 分支上，不能自动合并，请手动处理")
    # 主仓库有别的未提交改动不拦：快进合并只在这些改动和本单改的文件重叠时才会失败，交给 git 判断
    if git(wt, "rev-parse", "HEAD").stdout.strip() == o.get("base"):
        done(False, f"{a.no} 没有任何提交，没东西可合并")
    if p.get("github"):
        merge_github(a, o, p)
    # 主分支在跑单期间前进了：先把单的分支变基到最新主分支上
    r = git(wt, "rebase", f"refs/heads/{main}")
    if r.returncode != 0:
        git(wt, "rebase", "--abort")
        done(False, f"{a.no} 和最新 {main} 有冲突，变基失败，已撤回变基。需要人工处理或派单解决冲突")
    if p.get("test"):
        t = subprocess.run(p["test"], shell=True, cwd=wt, capture_output=True, text=True, timeout=1800)
        (ROOT / a.no / "merge-test.log").write_text(t.stdout + t.stderr)
        if t.returncode != 0:
            done(False, f"{a.no} 合并前测试没过（{p['test']}），没有合并。日志：{ROOT / a.no / 'merge-test.log'}")
    r = git(repo, "merge", "--ff-only", o["branch"])
    if r.returncode != 0:
        done(False, f"快进合并失败（多半是主仓库里有未提交的改动和本单改了同一批文件）：{(r.stderr or r.stdout).strip()[:300]}")
    o["status"] = "merged"
    o["merged_to"] = git(repo, "rev-parse", "HEAD").stdout.strip()
    save(o)
    done(True, f"{a.no} 已合并进 {p['name']} 的 {main}（{o['merged_to'][:7]}），没有 push",
         tested=bool(p.get("test")))


def cmd_clean(a):
    o = load(a.no)
    if o["mode"] != "write":
        done(False, f"{a.no} 是只读单，没有 worktree 要清理")
    if o["status"] == "running":
        done(False, f"{a.no} 还在跑，不能清理")
    p = project(o["project"])
    if o["status"] == "merged" and o.get("pr"):  # squash 合并后本地分支不算已合并，先确认 PR 真的合了
        r = gh("pr", "view", str(o["pr"]), "-R", p["github"], "--json", "state", "-q", ".state")
        if r.returncode != 0 or r.stdout.strip() != "MERGED":
            done(False, f"{a.no} 的 PR #{o['pr']} 状态不是 MERGED（{r.stdout.strip() or gh_err(r)}），没有清理")
    git(p["path"], "worktree", "remove", "--force", o["cwd"])
    # 本地快进合并的用 -d；GitHub squash 合并的（上面已确认 MERGED）和放弃的用 -D
    git(p["path"], "branch", "-d" if o["status"] == "merged" and not o.get("pr") else "-D", o["branch"])
    if o["status"] != "merged" and p.get("github"):  # 放弃：关掉 PR 和 issue，删远端分支
        if o.get("pr"):
            gh("pr", "close", str(o["pr"]), "-R", p["github"], "-c", f"派单 {a.no} 已放弃。")
            git(p["path"], "push", "origin", "--delete", o["branch"])
        if o.get("issue"):
            gh("issue", "close", str(o["issue"]), "-R", p["github"], "-r", "not planned", "-c", f"派单 {a.no} 已放弃。")
    o["status"] = "cleaned" if o["status"] == "merged" else "abandoned"
    save(o)
    done(True, f"{a.no} 的 worktree 和分支已清理（{o['status']}）")


def cmd_list(a):
    rows = [x for x in orders() if a.all or x["status"] not in ("cleaned", "abandoned")]
    done(True, f"{len(rows)} 张单", orders=[{k: x.get(k) for k in ("no", "project", "tool", "mode", "status", "branch", "issue", "pr_url")} for x in rows])


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new")
    s.add_argument("--project", required=True)
    s.add_argument("--tool", choices=["claude", "codex"], help="点名车间；不带就用 AI 接入配置里车间的执行方")
    s.add_argument("--mode", choices=["read", "write"], required=True)
    s.add_argument("--task-file", required=True)
    s.add_argument("--sched-key", help="调度器开单时的关联键（B2.2）；手工派单不带")
    s = sub.add_parser("run")
    s.add_argument("no")
    s.add_argument("--followup")
    s.add_argument("--timeout", type=int, default=3600)
    s = sub.add_parser("merge")
    s.add_argument("no")
    s.add_argument("--push-main", help="常浩确认可以推的本地主分支提交号（merge 列出没推的提交时给出）")
    for name in ("pr", "clean"):
        sub.add_parser(name).add_argument("no")
    sub.add_parser("list").add_argument("--all", action="store_true")
    a = ap.parse_args()
    {"new": cmd_new, "run": cmd_run, "pr": cmd_pr, "merge": cmd_merge, "clean": cmd_clean, "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    main()
