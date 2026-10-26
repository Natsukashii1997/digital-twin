"""派单：开单、跑单、合并、清理、列表（只用标准库）。Hermes 的 workshop 技能调用它。

  python3 dispatch.py new   --project ID --tool claude|codex --mode read|write --task-file F
  python3 dispatch.py run   单号 [--followup 追加说明文件]   （放在后台跑；结束后读 out/receipt.json）
  python3 dispatch.py merge 单号     （常浩说“合并”后才调用）
  python3 dispatch.py clean 单号     （合并后或放弃时，删 worktree 和分支）
  python3 dispatch.py list [--all]

每张单一个目录 ~/.hermes/dispatch/<单号>/：order.json（台账）、task.md、out/。
写入单在 ~/.hermes/worktrees/<项目>/<单号> 建 worktree，分支 hermes/<单号>，从名册里的主分支拉出。
并发：同时最多 2 张单在跑；同一个仓库同一时间只跑 1 张。
每条命令最后输出一行 JSON，字段 ok 表示成败、msg 是给常浩看的一句话。
"""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
ROOT = HOME / ".hermes/dispatch"
WT_ROOT = HOME / ".hermes/worktrees"
WORKER = HOME / ".hermes/scripts/run_worker.py"
ROSTER = Path(os.environ.get("DT_ROSTER", "~/Documents/ChatGPT/个人/work/digital-twin/projects.json")).expanduser()
MAX_RUNNING = 2


def done(ok, msg, **kw):
    print(json.dumps({"ok": ok, "msg": msg, **kw}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


def git(cwd, *args, check=False):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        done(False, f"git {' '.join(args)} 失败：{(r.stderr or r.stdout).strip()[:300]}")
    return r


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


def orders():
    return [json.loads(f.read_text()) for f in sorted(ROOT.glob("D*/order.json"))]


def next_no():
    day = dt.date.today().strftime("%Y%m%d")
    seq = [int(d.name.split("-")[1]) for d in ROOT.glob(f"D{day}-*") if d.name.split("-")[1].isdigit()]
    return f"D{day}-{max(seq, default=0) + 1}"


def cmd_new(a):
    p = project(a.project)
    if a.mode == "write" and not p.get("write_allowed"):
        done(False, f"名册不允许往 {p['name']} 写入")
    no = next_no()
    d = ROOT / no
    d.mkdir(parents=True)
    shutil.copy(a.task_file, d / "task.md")
    o = {"no": no, "project": p["id"], "tool": a.tool, "mode": a.mode, "status": "new",
         "created": dt.datetime.now().isoformat(timespec="seconds"), "cwd": p["path"]}
    if a.mode == "write":
        wt = WT_ROOT / p["id"] / no
        wt.parent.mkdir(parents=True, exist_ok=True)
        git(p["path"], "worktree", "add", "-b", f"hermes/{no}", str(wt), f"refs/heads/{p['main']}", check=True)
        # 依赖不进 git：主仓库有 node_modules 就链接过去，车间能直接跑测试
        if (Path(p["path"]) / "node_modules").is_dir() and not (wt / "node_modules").exists():
            (wt / "node_modules").symlink_to(Path(p["path"]) / "node_modules")
        o.update(cwd=str(wt), branch=f"hermes/{no}", base=git(p["path"], "rev-parse", f"refs/heads/{p['main']}").stdout.strip())
    save(o)
    done(True, f"已开单 {no}（{p['name']} · {a.tool} · {'写入' if a.mode == 'write' else '只读'}）", order=o)


def cmd_run(a):
    o = load(a.no)
    if o["status"] in ("running", "merged", "cleaned"):
        done(False, f"{a.no} 当前状态是 {o['status']}，不能再跑")
    running = [x for x in orders() if x["status"] == "running" and x["no"] != a.no]
    if len(running) >= MAX_RUNNING or any(x["project"] == o["project"] for x in running):
        o["status"] = "queued"
        save(o)
        done(False, f"{a.no} 排队中：现在有 {len(running)} 张单在跑" +
             ("，且同一仓库已有单在跑" if any(x["project"] == o["project"] for x in running) else ""),
             busy=True)
    task = ROOT / a.no / "task.md"
    if a.followup:  # 卡住后续跑：在原会话里接着做
        task = ROOT / a.no / f"followup-{dt.datetime.now():%H%M%S}.md"
        shutil.copy(a.followup, task)
    receipt = ROOT / a.no / "out/receipt.json"
    resume = json.loads(receipt.read_text()).get("session_id") if a.followup and receipt.exists() else None
    o["status"] = "running"
    save(o)
    cmd = [sys.executable, str(WORKER), "--tool", o["tool"], "--cwd", o["cwd"], "--task-file", str(task),
           "--out", str(ROOT / a.no / "out"), "--mode", o["mode"], "--timeout", str(a.timeout)]
    if resume:
        cmd += ["--resume", resume]
    r = subprocess.run(cmd, capture_output=True, text=True)
    o["status"] = {0: "done", 2: "guard"}.get(r.returncode, "failed")
    o["exit_code"] = r.returncode
    save(o)
    rc = json.loads(receipt.read_text()) if receipt.exists() else {}
    done(r.returncode == 0, f"{a.no} 跑完：{o['status']}", receipt={k: v for k, v in rc.items() if k != "summary"},
         stderr_tail=r.stderr[-500:] if r.returncode else "")


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
    git(p["path"], "worktree", "remove", "--force", o["cwd"])
    # 已合并的用 -d；没合并（放弃）的用 -D
    git(p["path"], "branch", "-d" if o["status"] == "merged" else "-D", o["branch"])
    o["status"] = "cleaned" if o["status"] == "merged" else "abandoned"
    save(o)
    done(True, f"{a.no} 的 worktree 和分支已清理（{o['status']}）")


def cmd_list(a):
    rows = [x for x in orders() if a.all or x["status"] not in ("cleaned", "abandoned")]
    done(True, f"{len(rows)} 张单", orders=[{k: x.get(k) for k in ("no", "project", "tool", "mode", "status", "branch")} for x in rows])


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("new")
    s.add_argument("--project", required=True)
    s.add_argument("--tool", choices=["claude", "codex"], required=True)
    s.add_argument("--mode", choices=["read", "write"], required=True)
    s.add_argument("--task-file", required=True)
    s = sub.add_parser("run")
    s.add_argument("no")
    s.add_argument("--followup")
    s.add_argument("--timeout", type=int, default=3600)
    for name in ("merge", "clean"):
        sub.add_parser(name).add_argument("no")
    sub.add_parser("list").add_argument("--all", action="store_true")
    a = ap.parse_args()
    {"new": cmd_new, "run": cmd_run, "merge": cmd_merge, "clean": cmd_clean, "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    main()
