"""自测用的假 gh：不联网，按 FAKE_GH_STATE 目录里的文件回话，并把每次调用记进 calls.jsonl。

install(bin_dir, state_dir) 在 bin_dir 放一个名叫 gh 的可执行脚本，测试把 bin_dir 放到 PATH 最前面。
state 目录里的文件：
  fail          有这个文件时一律失败，stderr 输出文件内容（模拟断网、代理问题）
  remote        假 GitHub 背后的裸仓库路径；pr merge 在这里做 squash 合并
  pr_state      pr view 返回的状态，默认 OPEN；pr merge 成功后写成 MERGED
  merge_fail    有这个文件时 pr merge 失败
  pr_list.json / issue_list.json   pr list / issue list 返回的 JSON
  api.json      gh api <路径> 的回话：{路径: 回话}，回话是字符串就原样输出，否则输出 JSON；路径不在里面按 404 失败
  fail_match    参数里含这个文件内容的调用失败（模拟只有某个仓库连不上）
"""
import json
import os
import subprocess
import sys
from pathlib import Path


def install(bin_dir, state_dir):
    bin_dir, state_dir = Path(bin_dir), Path(state_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "gh"
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" -B "{Path(__file__).resolve()}" "$@"\n')
    exe.chmod(0o755)
    return exe


def calls(state_dir):
    f = Path(state_dir) / "calls.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def opt(args, name):
    return args[args.index(name) + 1] if name in args else None


def main(args):
    state = Path(os.environ["FAKE_GH_STATE"])
    with open(state / "calls.jsonl", "a") as f:
        f.write(json.dumps(args, ensure_ascii=False) + "\n")
    if (state / "fail").exists():
        sys.stderr.write((state / "fail").read_text())
        return 1
    f = state / "fail_match"
    if f.exists() and any(f.read_text().strip() in a for a in args):
        sys.stderr.write("error connecting to api.github.com\n")
        return 1
    kind, verb = args[0], args[1]
    if kind == "api":
        f = state / "api.json"
        api = json.loads(f.read_text()) if f.exists() else {}
        if verb not in api:
            sys.stderr.write("gh: Not Found (HTTP 404)\n")
            return 1
        out = api[verb]
        print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))
        return 0
    if (kind, verb) == ("issue", "create"):
        print("https://github.com/o/fx/issues/7")
    elif (kind, verb) == ("pr", "create"):
        (state / "pr_head").write_text(opt(args, "-H"))
        print("https://github.com/o/fx/pull/8")
    elif (kind, verb) == ("pr", "view"):
        f = state / "pr_state"
        print(f.read_text() if f.exists() else "OPEN")
    elif (kind, verb) == ("pr", "merge"):
        if (state / "merge_fail").exists():
            sys.stderr.write("Pull request is not mergeable\n")
            return 1
        bare, head = (state / "remote").read_text(), opt(args, "--match-head-commit")
        ref = f"refs/heads/{(state / 'pr_head').read_text()}"

        def g(*a):
            return subprocess.run(["git", "--git-dir", bare, *a], capture_output=True, text=True, check=True).stdout.strip()
        if g("rev-parse", ref) != head:
            sys.stderr.write("head commit mismatch\n")
            return 1
        squash = g("commit-tree", f"{head}^{{tree}}", "-p", g("rev-parse", "refs/heads/main"), "-m", "squash 合并")
        g("update-ref", "refs/heads/main", squash)
        g("update-ref", "-d", ref)  # 仓库设置了合并后自动删分支
        (state / "pr_state").write_text("MERGED")
    elif verb == "list":
        f = state / f"{kind}_list.json"
        print(f.read_text() if f.exists() else "[]")
    # issue edit / issue close / pr close：只记调用
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
