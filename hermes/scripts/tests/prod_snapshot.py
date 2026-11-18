"""正式环境元数据摘要（#212）：「故意改坏」类反向验证、全量测试的前后，核对本机正式目录没被动过。

只读：逐个条目只取路径、类型、大小、修改时间（符号链接另取指向），不打开任何文件、不读内容（密钥文件也只看元数据）。
家目录按系统账号取（不信环境变量 HOME：测试隔离会把 HOME 改到临时目录，这里要看的是真的正式目录）。

默认核对 DEFAULT_ROOTS：~/.agent-autoloop、~/.hermes、~/Library/LaunchAgents、~/Library/Application Support/DigitalTwin、
/Users/Shared/dt-autoloop。--root 可换成别的目录（可写多个），--exclude 按 fnmatch 排除绝对路径（如常驻服务自己在写的日志；
排除的只是这一条不比，目录下面的条目照样核对，要整棵排除写 '<目录>/*'）。

命令行（变了退出码 3，并逐条列出变了什么）：
    python3 hermes/scripts/tests/prod_snapshot.py take 前.json            # 取一次摘要存文件
    python3 hermes/scripts/tests/prod_snapshot.py check 前.json           # 现在再取一次，和 前.json 比
    python3 hermes/scripts/tests/prod_snapshot.py diff 前.json 后.json    # 比两份已存的摘要
    python3 hermes/scripts/tests/prod_snapshot.py run -- <命令…>          # 取 → 跑命令 → 取 → 比；没变时退出码同命令
改坏脚本里（每改坏一处、跑完测试就核一次，变了立刻停）：
    import prod_snapshot as ps
    base = ps.take()
    ...                                   # 改坏一处、跑测试、还原
    ps.assert_unchanged(base)             # 变了抛 ps.ProdChanged，消息里写明哪些条目新增 / 删除 / 变了
"""
import argparse
import fnmatch
import hashlib
import json
import os
import pwd
import stat
import subprocess
import sys
from pathlib import Path

REAL_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
DEFAULT_ROOTS = [REAL_HOME / ".agent-autoloop", REAL_HOME / ".hermes", REAL_HOME / "Library/LaunchAgents",
                 REAL_HOME / "Library/Application Support/DigitalTwin", Path("/Users/Shared/dt-autoloop")]
CHANGED = 3
SHOW = 50


class ProdChanged(AssertionError):
    """正式目录的元数据变了。"""


def _kind(mode):
    if stat.S_ISLNK(mode):
        return "l"
    if stat.S_ISDIR(mode):
        return "d"
    if stat.S_ISREG(mode):
        return "f"
    return "o"


def _meta(path, st):
    k = _kind(st.st_mode)
    m = [k, st.st_size if k != "d" else 0, st.st_mtime_ns]
    if k == "l":
        try:
            m.append(os.readlink(path))                 # 链接指向是元数据，不读目标内容
        except OSError:
            m.append(None)
    return m


def _walk(root, excludes):
    """root 下每个条目 → [类型, 大小, 修改时间(ns)(, 链接指向)]；目录的大小记 0（增删条目时目录本身照样记修改时间）。"""
    out = {}
    try:
        st = os.stat(root)                              # 目录本身是链接时跟过去核对它指向的目录
    except FileNotFoundError:
        return None
    out["."] = _meta(root, st)
    if not stat.S_ISDIR(st.st_mode):
        return out
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            it = list(os.scandir(d))
        except OSError as e:
            out[os.path.relpath(d, root) + "/"] = ["!", type(e).__name__, 0]
            continue
        for e in it:
            skip = any(fnmatch.fnmatch(e.path, pat) for pat in excludes)   # 排除的条目不记，目录照样往下走
            try:
                st = e.stat(follow_symlinks=False)
            except OSError as err:
                if not skip:
                    out[os.path.relpath(e.path, root)] = ["!", type(err).__name__, 0]
                continue
            if not skip:
                out[os.path.relpath(e.path, root)] = _meta(e.path, st)
            if stat.S_ISDIR(st.st_mode):
                stack.append(e.path)
    return out


def digest(entries):
    if entries is None:
        return None
    h = hashlib.sha256()
    for k in sorted(entries):
        h.update(json.dumps([k, entries[k]], ensure_ascii=False).encode())
    return h.hexdigest()


def take(roots=None, excludes=()):
    """取摘要：{"roots": {目录: {"count", "digest", "entries"}}, "excludes": [...]}；目录不存在时 entries 为 null。"""
    snap = {"roots": {}, "excludes": list(excludes)}
    for r in roots or DEFAULT_ROOTS:
        r = str(Path(r))
        entries = _walk(r, list(excludes))
        snap["roots"][r] = {"count": None if entries is None else len(entries), "digest": digest(entries),
                            "entries": entries}
    return snap


def _fmt(m):
    if m[0] == "!":
        return f"读不了（{m[1]}）"
    s = {"f": "文件", "d": "目录", "l": "链接", "o": "其他"}[m[0]] + (f" {m[1]} 字节" if m[0] != "d" else "")
    return s + (f" → {m[3]}" if m[0] == "l" else "")


def diff(before, after):
    """两份摘要的差别，一条一行；完全一样返回 []。"""
    out = []
    excludes = list(dict.fromkeys([*before.get("excludes", ()), *after.get("excludes", ())]))
    roots = list(dict.fromkeys([*before["roots"], *after["roots"]]))
    for r in roots:
        a = before["roots"].get(r, {}).get("entries")
        b = after["roots"].get(r, {}).get("entries")
        if r not in before["roots"] or r not in after["roots"]:
            out.append(f"{r}：只在一份摘要里核对过，不能比")
            continue
        if a is None or b is None:
            if (a is None) != (b is None):
                out.append(f"{r}：{'新建出来了' if a is None else '整个不见了'}")
            continue
        for k in sorted(set(a) | set(b)):
            p = r if k == "." else os.path.join(r, k)
            if any(fnmatch.fnmatch(p, pat) for pat in excludes):       # 核对的目录本身也按排除规则
                continue
            if k not in a:
                out.append(f"新增 {p}（{_fmt(b[k])}）")
            elif k not in b:
                out.append(f"删除 {p}（原为{_fmt(a[k])}）")
            elif a[k] != b[k]:
                what = []
                if a[k][0] != b[k][0] or a[k][1] != b[k][1] or a[k][3:] != b[k][3:]:
                    what.append(f"{_fmt(a[k])} → {_fmt(b[k])}")
                if a[k][2] != b[k][2]:
                    what.append("修改时间变了")
                out.append(f"变了 {p}（{'；'.join(what)}）")
    return out


def summary(snap):
    return {r: {"count": v["count"], "digest": v["digest"]} for r, v in snap["roots"].items()}


def report(changes):
    head = f"正式目录变了 {len(changes)} 处：\n"
    body = "\n".join(f"  {c}" for c in changes[:SHOW])
    more = f"\n  另有 {len(changes) - SHOW} 处" if len(changes) > SHOW else ""
    return head + body + more


def assert_unchanged(before, roots=None):
    """现在再取一次（同样的目录和排除规则），和 before 比；变了抛 ProdChanged。"""
    now = take(roots or list(before["roots"]), before.get("excludes", ()))
    changes = diff(before, now)
    if changes:
        raise ProdChanged(report(changes))
    return now


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _save(snap, path):
    Path(path).write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")


def _finish(before, after, out_dir=None):
    if out_dir:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        _save(before, Path(out_dir) / "before.json")
        _save(after, Path(out_dir) / "after.json")
    changes = diff(before, after)
    print(json.dumps({"before": summary(before), "after": summary(after)}, ensure_ascii=False, indent=1))
    if changes:
        print(report(changes), file=sys.stderr)
        return CHANGED
    print("正式目录元数据前后一致。", file=sys.stderr)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="正式目录元数据摘要（只读）：前后核对没被动过。变了退出码 3。")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("take", "check", "run"):
        p = sub.add_parser(name)
        p.add_argument("--root", action="append", help="要核对的目录，可写多个；不写用默认的正式目录")
        p.add_argument("--exclude", action="append", default=[], help="按 fnmatch 排除的绝对路径")
        if name == "run":
            p.add_argument("--out-dir", help="把前后两份摘要存到这个目录")
            p.add_argument("command", nargs=argparse.REMAINDER)
        else:
            p.add_argument("file")
    d = sub.add_parser("diff")
    d.add_argument("before")
    d.add_argument("after")
    a = ap.parse_args(argv)

    if a.cmd == "take":
        snap = take(a.root, a.exclude)
        _save(snap, a.file)
        print(json.dumps(summary(snap), ensure_ascii=False, indent=1))
        return 0
    if a.cmd == "check":
        before = _load(a.file)
        return _finish(before, take(a.root or list(before["roots"]), a.exclude or before.get("excludes", ())))
    if a.cmd == "diff":
        return _finish(_load(a.before), _load(a.after))
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("run 后面要写 -- <命令…>")
    before = take(a.root, a.exclude)
    code = subprocess.run(cmd).returncode
    rc = _finish(before, take(a.root, a.exclude), a.out_dir)
    print(f"命令退出码 {code}", file=sys.stderr)
    return rc or code


if __name__ == "__main__":
    sys.exit(main())
