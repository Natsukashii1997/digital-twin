"""车间包装：让 Hermes 用同一种方式调用 Claude Code 或 Codex 跑一张单（只用标准库）。

用法：
  python3 run_worker.py --tool claude|codex --cwd 仓库或worktree --task-file 任务.md --out 结果目录
                        [--mode read|write] [--resume 会话ID] [--timeout 秒] [--main 主分支名]

做的事：
- 设置代理（本机 claude 不走系统代理，直连会 403），清掉第三方 Anthropic 变量
- read 模式只读；write 模式放开权限（PRD M2 决定：边界靠独立 worktree + 下面的护栏 + 合并须常浩确认）
- 跑完写 结果目录/receipt.json，并检查护栏：车间有没有把东西弄进主分支、远端分支有没有变（有没有 push）。
  主分支被别的会话正常更新（例如另一个 Claude 会话合并了自己的分支）不算违规，只记在 note_main_moved。
- 只接项目名册（digital-twin/projects.json）里登记的仓库；worktree 按所属主仓库匹配
退出码：0 正常；1 车间失败或超时；2 护栏被触发（主分支或远端被动过）；3 不在名册里或名册不允许写入。
"""
import argparse
import datetime as dt
import glob
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

PROXY = os.environ.get("DT_PROXY", "http://127.0.0.1:8086")
ROSTER = Path(os.environ.get("DT_ROSTER", "~/Documents/ChatGPT/个人/work/digital-twin/projects.json")).expanduser()
PREAMBLE = """你是被常浩的数字分身 Hermes 派来干活的车间。规矩：
- 先读本仓库根目录的 AGENTS.md（没有就读 README），按项目规矩工作。
- 只在当前目录和当前分支干活：不要切换到主分支，不要合并，不要 push，不要删除分支。
- 需要常浩拍板才能继续时，停下来，在最后单独一行输出：NEED_DECISION: <问题和可选项>
- 结束时用简体中文写：做了什么、改了哪些文件、测试或检查结果、还剩什么。

任务：
"""


def git(cwd, *args):
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def snapshot(cwd, main):
    return {
        "head": git(cwd, "rev-parse", "HEAD"),
        "branch": git(cwd, "branch", "--show-current"),
        "main": git(cwd, "rev-parse", f"refs/heads/{main}"),
        "remotes": git(cwd, "for-each-ref", "refs/remotes", "--format=%(refname) %(objectname)"),
    }


def roster_lookup(cwd):
    """按 cwd 所属的主仓库（worktree 也算）在名册里找项目；找不到返回 None。"""
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    repo = os.path.realpath(os.path.dirname(common)) if common else None
    for proj in json.loads(ROSTER.read_text())["projects"]:
        if repo and os.path.realpath(os.path.expanduser(proj["path"])) == repo:
            return proj
    return None


def proxy_up():
    host, port = PROXY.split("//")[-1].split(":")
    try:
        socket.create_connection((host, int(port)), timeout=1).close()
        return True
    except OSError:
        return False


def find_bin(name):
    extra = ["/usr/local/bin", "/opt/homebrew/bin", str(Path.home() / ".local/bin"),
             *sorted(glob.glob(str(Path.home() / ".nvm/versions/node/*/bin")), reverse=True)]
    return shutil.which(name, path=os.pathsep.join([*os.environ.get("PATH", "").split(os.pathsep), *extra]))


def build_env():
    env = dict(os.environ)
    for k in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        env.pop(k, None)
    if proxy_up():
        env.update(HTTPS_PROXY=PROXY, HTTP_PROXY=PROXY, https_proxy=PROXY, http_proxy=PROXY,
                   NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")
    return env


def claude_cmd(a, task):
    cmd = [find_bin("claude"), "-p", task, "--output-format", "json"]
    if a.mode == "write":
        cmd.append("--dangerously-skip-permissions")
    else:
        cmd += ["--allowedTools", "Read", "Grep", "Glob", "LS"]
    if a.resume:
        cmd += ["--resume", a.resume]
    return cmd


def codex_cmd(a, task, out):
    cmd = [find_bin("codex"), "exec", "--json", "-C", a.cwd, "-o", str(out / "last_message.txt")]
    cmd += ["--dangerously-bypass-approvals-and-sandbox"] if a.mode == "write" else ["-s", "read-only"]
    cmd += ["resume", a.resume, task] if a.resume else [task]
    return cmd


def parse(tool, stdout, out):
    """从车间输出里取 会话ID、最后的文字、是否出错。"""
    if tool == "claude":
        try:
            j = json.loads(stdout.strip().splitlines()[-1])
            return j.get("session_id"), j.get("result") or "", bool(j.get("is_error"))
        except Exception:
            return None, stdout[-2000:], True
    sid, err = None, False
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        sid = sid or ev.get("thread_id") or ev.get("session_id")
        err = err or ev.get("type") in ("error", "turn.failed")
    last = out / "last_message.txt"
    return sid, last.read_text() if last.exists() else "", err


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tool", choices=["claude", "codex"], required=True)
    p.add_argument("--cwd", required=True)
    p.add_argument("--task-file", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--mode", choices=["read", "write"], default="read")
    p.add_argument("--resume")
    p.add_argument("--timeout", type=int, default=3600)
    p.add_argument("--main", help="主分支名，默认取名册里的配置")
    a = p.parse_args()

    a.cwd = git(a.cwd, "rev-parse", "--show-toplevel") or a.cwd
    proj = roster_lookup(a.cwd)
    if not proj or (a.mode == "write" and not proj.get("write_allowed")):
        why = "不在项目名册里" if not proj else "名册不允许写入"
        print(json.dumps({"refused": True, "cwd": a.cwd, "reason": why, "roster": str(ROSTER)}, ensure_ascii=False))
        raise SystemExit(3)
    a.main = a.main or proj["main"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    task = PREAMBLE + Path(a.task_file).read_text()
    before = snapshot(a.cwd, a.main)
    cmd = claude_cmd(a, task) if a.tool == "claude" else codex_cmd(a, task, out)
    if not cmd[0]:
        raise SystemExit(f"找不到 {a.tool} 命令")

    started = dt.datetime.now().isoformat(timespec="seconds")
    timed_out = False
    try:
        r = subprocess.run(cmd, cwd=a.cwd, env=build_env(), capture_output=True, text=True,
                           timeout=a.timeout, stdin=subprocess.DEVNULL)
        stdout, stderr, code = r.stdout, r.stderr, r.returncode
    except subprocess.TimeoutExpired as e:
        stdout, stderr, code, timed_out = e.stdout or "", e.stderr or "", -1, True
        stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
        stderr = stderr.decode() if isinstance(stderr, bytes) else stderr
    (out / "stdout.log").write_text(stdout)
    (out / "stderr.log").write_text(stderr)

    after = snapshot(a.cwd, a.main)
    main_moved = before["main"] != after["main"]
    worker_committed = before["head"] != after["head"]
    # 违规 = 车间自己的提交进了主分支，或者车间把当前分支切到了主分支；
    # 只读模式车间没有写权限，主分支变化只可能来自别的会话
    main_violation = a.mode == "write" and main_moved and (
        after["branch"] == a.main
        or (worker_committed and subprocess.run(
            ["git", "-C", a.cwd, "merge-base", "--is-ancestor", after["head"], f"refs/heads/{a.main}"]
        ).returncode == 0))
    sid, text, err = parse(a.tool, stdout, out)
    decision = next((l.split(":", 1)[1].strip() for l in text.splitlines()[::-1]
                     if l.strip().startswith("NEED_DECISION:")), None)
    changed = git(a.cwd, "diff", "--name-only", f"{before['head']}..HEAD") if before["head"] else ""
    receipt = {
        "project": proj["id"], "tool": a.tool, "mode": a.mode, "cwd": a.cwd, "started": started,
        "ended": dt.datetime.now().isoformat(timespec="seconds"),
        "exit_code": code, "timed_out": timed_out, "worker_error": err,
        "session_id": sid, "need_decision": decision,
        "branch": after["branch"], "head_before": before["head"], "head_after": after["head"],
        "committed_files": [f for f in changed.splitlines() if f],
        "uncommitted": [l for l in git(a.cwd, "status", "--porcelain").splitlines() if l],
        "guard_main_changed": main_violation,
        "note_main_moved": f"{before['main'][:7]} → {after['main'][:7]}（别的会话更新，非本单）" if main_moved and not main_violation else None,
        "guard_remote_changed": before["remotes"] != after["remotes"],
        "summary": text[-3000:],
    }
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({k: v for k, v in receipt.items() if k != "summary"}, ensure_ascii=False))
    print("---- 车间总结 ----")
    print(text[-1500:])
    if receipt["guard_main_changed"] or receipt["guard_remote_changed"]:
        raise SystemExit(2)
    if timed_out or err or code != 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
