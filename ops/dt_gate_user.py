"""控制面搬家的账号侧小工具（ADR-015 批 0E-2）：由 ops/dt_gate_hub.py 以 root「以某个账号的身份」调用，只动这个账号自己的东西。

为什么单独一份：root 不按路径碰别的账号家目录里的任何东西（那些目录归账号自己，别人能换成链接）；要在 dt-gate、主账号、
dt-worker 家目录里写的，都以那个账号的身份跑本脚本，权限由系统按账号挡。以 dt-gate 身份跑时，本脚本必须是 root 装好的只读
副本（/Library/Application Support/DigitalTwin/gate/repo/<提交>/ops/），不能是主账号仓库里的，否则主账号改一行就能在
dt-gate 里执行代码。

命令（都只用 macOS 自带的 /usr/bin/python3（3.9）标准库）：
  put <文件> [--mode 600] [--mkdir]   从标准输入收内容，原子写到自己目录里的文件（O_EXCL 临时文件 + 改名，不跟随链接）
  mkdirs <目录>...                    建自己的目录（700），已有的核对属自己、不是链接
  prepare <暂存目录>                  中台备份到了 dt-gate 之后：config.json 只留 url、hub（去掉接单程序、来源、通道、本人密钥），
                                      master.key 换新（43 字符，主账号见过的那把作废）
  new-owner <名字> --cli <命令>       在中台里签一把新的本人密钥，直接写进 config.json 的 owner（钥匙不出本进程、不打印）
  prune-keys --cli <命令> --home <中台目录> --keep 种类:名字 ...   删掉中台里除 --keep 以外的全部访问钥匙（要中台在跑、
                                      本人密钥已是新的；设了 DT_HUB_LISTENER_UID 时先核端口上在听的是本账号，再带密钥去）
  snapshot <中台目录>                 只读打印中台的对账数：轮次 / 卡片按状态计数、暂停的规则、钥匙（种类和名字，不含哈希）、
                                      规则文件哈希、事件最大编号、派单规则名、中台地址（config 的 url、hub.listen，不含密钥）、
                                      secrets.env 里的变量名（不含值）
  unmark <路径> --prefix <开头>        路径是普通文件、内容以这个开头（搬家时放的说明文件）就删掉；不存在也算好；别的一律拒绝
  token <文件>                        没有就生成一个随机令牌（600），有就核对格式；不打印令牌
  set-worker-key <config.json>        从标准输入收新的接单钥匙，写进工人配置的 worker.key（dt-worker 换钥匙用）
"""
import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hermes/scripts"))
import hub_listener  # noqa: E402

KEY_SHAPE = re.compile(r"^[A-Za-z0-9_-]{43}$")
TOKEN_SHAPE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
LIVE_RUNS = ("queued", "claimed", "unknown")         # 还没结束的轮次
LIVE_CARDS = ("open", "decided")                     # 还要人回答、或答了还没交回去的卡片


class Refused(Exception):
    pass


def _own_dir(d):
    st = os.lstat(d)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise Refused(f"{d} 不是本账号自己的目录（或是链接）")


def mkdirs(paths):
    for p in paths:
        p = Path(p)
        if p.exists() or p.is_symlink():
            _own_dir(p)
            continue
        p.mkdir(mode=0o700, parents=True)
        _own_dir(p)


def put(path, data, mode=0o600, make_parent=False):
    path = Path(path)
    if make_parent:
        mkdirs([path.parent])
    _own_dir(path.parent)
    tmp = path.parent / f".{path.name}.tmp-{secrets.token_hex(6)}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)              # 目标是链接时换掉的是链接本身，不跟随
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path):
    with open(path, "rb") as fh:
        return json.loads(fh.read().decode("utf-8"))


def prepare(staging):
    staging = Path(staging)
    _own_dir(staging)
    cfg = _read_json(staging / "config.json")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("url"), str) or not isinstance(cfg.get("hub"), dict):
        raise Refused("备份里的 config.json 不是中台配置（缺 url 或 hub）")
    keep = {"url": cfg["url"], "hub": cfg["hub"]}
    put(staging / "config.json", (json.dumps(keep, ensure_ascii=False, indent=2) + "\n").encode())
    put(staging / "master.key", secrets.token_urlsafe(32).encode())
    if not KEY_SHAPE.match((staging / "master.key").read_text().strip()):
        raise Refused("新 master.key 格式不对")
    return {"dropped": sorted(set(cfg) - set(keep))}


def _cli(cli, *args, timeout=60):
    r = subprocess.run([cli, *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise Refused(f"agent-autoloop {' '.join(args[:2])} 失败：{(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def new_owner(name, cli, home):
    home = Path(home)
    key = _cli(cli, "key", "new", "owner", name).strip()
    if not KEY_SHAPE.match(key):
        raise Refused("新签的本人密钥格式不对")
    cfg = _read_json(home / "config.json")
    cfg["owner"] = key
    put(home / "config.json", (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode())
    return {"owner": name}


def list_keys(cli):
    out = []
    for line in _cli(cli, "key", "list").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out.append(f"{parts[0]}:{parts[1]}")
    return out


def prune_keys(cli, keep, home):
    keep = set(keep)
    hub_listener.check(_read_json(Path(home) / "config.json")["url"])     # key remove 会带本人密钥去
    before = list_keys(cli)
    missing = keep - set(before)
    if missing:
        raise Refused(f"要保留的钥匙不在中台里：{sorted(missing)}")
    removed = []
    for k in before:
        if k in keep:
            continue
        kind, name = k.split(":", 1)
        _cli(cli, "key", "remove", kind, name)
        removed.append(k)
    after = list_keys(cli)
    if sorted(after) != sorted(keep):
        raise Refused(f"删完以后中台里的钥匙是 {sorted(after)}，和应留的 {sorted(keep)} 对不上")
    return {"removed": removed, "kept": sorted(after)}


def snapshot(home):
    home = Path(home)
    db = sqlite3.connect(f"file:{home / 'hub.db'}?mode=ro", uri=True, timeout=10)
    try:
        def grouped(view):
            return dict(db.execute(f"SELECT state, COUNT(*) FROM {view} GROUP BY state ORDER BY state").fetchall())
        runs, cards = grouped("runs"), grouped("cards")
        pauses = [r[0] for r in db.execute("SELECT name FROM pauses ORDER BY name")]
        keys = [f"{k}:{n}" for k, n in db.execute("SELECT kind, name FROM keys ORDER BY kind, name")]
        events = db.execute("SELECT MAX(id) FROM events").fetchone()[0] or 0
        max_card = db.execute("SELECT MAX(CAST(id AS INTEGER)) FROM cards").fetchone()[0] or 0
    finally:
        db.close()
    rules = home / "rules.json"
    names = [r.get("name") for r in _read_json(rules).get("rules", []) if isinstance(r, dict)] if rules.exists() else []
    cfg = _read_json(home / "config.json")
    env = home / "secrets.env"
    env_names = []
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                env_names.append(line.split("=", 1)[0].replace("export ", "").strip())
    return {
        "runs": runs, "cards": cards, "pauses": pauses, "keys": keys, "max_event": events, "max_card": max_card,
        "live_runs": sum(runs.get(s, 0) for s in LIVE_RUNS),
        "live_cards": sum(cards.get(s, 0) for s in LIVE_CARDS),
        "rules_sha256": hashlib.sha256(rules.read_bytes()).hexdigest() if rules.exists() else None,
        "dispatch_rules": sorted(n for n in names if isinstance(n, str) and n.startswith("dispatch-")),
        "url": cfg.get("url"), "listen": (cfg.get("hub") or {}).get("listen"), "secrets_env": env_names,
    }


def unmark(path, prefix):
    path = Path(path)
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return {"removed": False}
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
        raise Refused(f"{path} 不是本账号的普通文件")
    with open(path, "rb") as fh:
        head = fh.read(len(prefix.encode()) + 16)
    if not head.decode("utf-8", "replace").startswith(prefix):
        raise Refused(f"{path} 不是搬家时放的说明文件")
    os.unlink(path)
    return {"removed": True}


def token(path):
    path = Path(path)
    if path.is_symlink():
        raise Refused(f"{path} 是链接")
    if not path.exists():
        put(path, (secrets.token_urlsafe(32) + "\n").encode(), make_parent=True)
        made = True
    else:
        made = False
    st = os.lstat(path)
    if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
        raise Refused(f"{path} 不是本账号的 600 文件")
    if not TOKEN_SHAPE.match(path.read_text(encoding="utf-8").strip()):
        raise Refused(f"{path} 里的令牌格式不对")
    return {"made": made}


def set_worker_key(config, key):
    key = key.strip()
    if not KEY_SHAPE.match(key):
        raise Refused("新接单钥匙格式不对")
    cfg = _read_json(config)
    if not isinstance(cfg.get("worker"), dict):
        raise Refused(f"{config} 里没有 worker 段")
    cfg["worker"]["key"] = key
    put(config, (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode())
    return {"worker": cfg["worker"].get("name")}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("put")
    p.add_argument("path")
    p.add_argument("--mode", default="600")
    p.add_argument("--mkdir", action="store_true")
    sub.add_parser("mkdirs").add_argument("paths", nargs="+")
    sub.add_parser("prepare").add_argument("staging")
    p = sub.add_parser("new-owner")
    p.add_argument("name")
    p.add_argument("--cli", required=True)
    p.add_argument("--home", required=True)
    p = sub.add_parser("prune-keys")
    p.add_argument("--cli", required=True)
    p.add_argument("--home", required=True)
    p.add_argument("--keep", action="append", required=True)
    sub.add_parser("snapshot").add_argument("home")
    sub.add_parser("token").add_argument("path")
    sub.add_parser("set-worker-key").add_argument("config")
    p = sub.add_parser("unmark")
    p.add_argument("path")
    p.add_argument("--prefix", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "put":
            put(a.path, sys.stdin.buffer.read(), int(a.mode, 8), a.mkdir)
            out = {"ok": True}
        elif a.cmd == "mkdirs":
            mkdirs(a.paths)
            out = {"ok": True}
        elif a.cmd == "prepare":
            out = prepare(a.staging)
        elif a.cmd == "new-owner":
            out = new_owner(a.name, a.cli, a.home)
        elif a.cmd == "prune-keys":
            out = prune_keys(a.cli, a.keep, a.home)
        elif a.cmd == "snapshot":
            out = snapshot(a.home)
        elif a.cmd == "unmark":
            out = unmark(a.path, a.prefix)
        elif a.cmd == "token":
            out = token(a.path)
        else:
            out = set_worker_key(a.config, sys.stdin.read())
    except (Refused, OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as e:
        print(json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
