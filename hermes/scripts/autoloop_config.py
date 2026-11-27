#!/usr/bin/env python3
"""中台配置（批 0B-2，#195；规格 docs/秘书代批-规格.md §5、§6、§9，方案 docs/agent-autoloop-底座-方案.md 5.1）。

按名册生成本机中台 rules.json 里数字分身这一块，其余原样保留：
  - 来源 hermes：只有派单网关（autoloop_grants.py）持有它的钥匙；
  - Agent dt-claude / dt-codex：跑在工人账号 dt-worker 上的接单程序（主账号的 claude / codex 是上游试跑用的，不动），
    program 指向工人侧启动脚本 /Users/Shared/dt-autoloop/bin/dt-worktree-<执行方>（#205 0C：建工作目录、核凭证、交接）；
  - 通道 feishu（answerers 只有常浩的 open_id）和 secretary（answerers 只有「秘书」）：两把通道钥匙分开，
    秘书的钥匙答不出 by=常浩，飞书桥接的钥匙答不出 by=秘书（中台按通道核 answerers）；
    defaultChannels 显式写成原有的通道（原来一个通道都没有时回退成 ["feishu"]：中台告警等没写通道的卡片会发到飞书），
    新增的两个通道里秘书只收派单规则的卡片：别的规则自己点名 secretary 的，生成时直接拒绝并报规则名；
  - 派单规则 dispatch-<仓库>-<执行方>：只接 hermes 来源、只接 autoloop_grants.event_type 那一种类型，
    按事件对象（= 任务单号）分事项，不开 emits（工人的回执发不出任何事件，触发不了派单规则），卡片只发 feishu 和 secretary。
    现在只给名册里的沙盒生成（批 2 前不往正式仓库派）。

装进中台是改常驻配置，要常浩说「装」才跑（install / issue-source-key 都要 --confirm）。两条命令共用一把跨进程锁
（DigitalTwin/autoloop/config.lock），检查、写配置、确认载入、暂停、签钥匙都在锁里，同一时间只有一条在跑。顺序保证没有派单能跑起来：
  1. install：生成草稿 → agent-autoloop check 校验 → 备份原文件 → 原子替换 → 等中台载入（只读打开中台库，读它正在用的配置，
     和文件逐字段比，不只看 /v1/state 露出来的名字、来源、类型、执行方）
     → 逐条暂停派单规则 → 回读核对已暂停；hermes 来源钥匙已经存在时，派单规则一条也不许新增、不许改（只能原样重装）；
  2. issue-source-key：中台正在用的配置和 rules.json 逐字段一致（同上）、没有多出来的派单规则、全部暂停着，
     才签 hermes 来源钥匙，写到 600 文件（autoloop_grants 读它），不打印钥匙；
  派单规则一直暂停到 0C 的启动脚本就绪，由常浩决定什么时候恢复。暂停失败时 install 报错，钥匙签不出来（第 2 步的门）。

  autoloop_config.py plan                 只读：打印要改的地方（和现在的 rules.json 比）
  autoloop_config.py install --confirm --target ~/.agent-autoloop          常浩说「装」之后
  autoloop_config.py issue-source-key --confirm --target ~/.agent-autoloop
  autoloop_config.py issue-channel-key feishu|secretary --confirm --target ~/.agent-autoloop
                                          签通道钥匙（批 0B-4）：中台正在用的配置和 rules.json 一致、这条通道的 answerers
                                          正好是预期的（feishu 只有常浩的 open_id，secretary 只有「秘书」）才签，写到 600 文件
  （--target 必须写明，且和实际要改的中台目录一致，免得继承来的环境变量把操作落到别的中台上）
  autoloop_config.py status               只读：来源、通道、派单规则是否暂停、钥匙有没有（只列名字）
  autoloop_config.py add-rules <片段名> --confirm --target ~/.agent-autoloop
  autoloop_config.py remove-rules <片段名> --confirm --target ~/.agent-autoloop
                                          把手写的规则片段（本仓库 ops/autoloop_rules/<片段名>.json，2026-11-27 第一条自动规则起）
                                          并进 / 拿出中台规则：只认副本里这个目录下按名字找到的文件；片段只能有 sources / agents / rules，
                                          不许碰数字分身这一块（dispatch-* 规则、hermes 来源、dt-claude / dt-codex、feishu / secretary
                                          通道的定义、defaultChannels），不许接 hermes 来源、不许点名秘书通道；同名的规则、Agent、来源
                                          整条换成片段里的（可重复跑）。和 install 不同，**不暂停、不恢复任何规则**（派单规则的暂停状态原样
                                          保留）；同样在配置锁里：check 校验 → 备份 → 原子替换 → 等中台载入（片段里的每条规则、每个 Agent
                                          逐字段在中台在用的配置里），没载入就把原文件换回去。remove-rules 只拿掉片段里的规则，以及
                                          剩下的规则不再用到的片段 Agent、来源。
环境变量：AGENT_AUTOLOOP_HOME（中台目录，默认 ~/.agent-autoloop）、DT_AUTOLOOP_CLI（中台命令行）、DT_ROSTER、
DT_OWNER_OPEN_ID（同 twin_state）、DT_HERMES_SOURCE_KEY_FILE（同 autoloop_grants）、DT_AUTOLOOP_EVIDENCE（备份目录）、
DT_AUTOLOOP_CHANNEL_KEY_DIR（通道钥匙文件所在目录，默认 DigitalTwin/autoloop/，文件名 <通道>-channel.key）。
"""
import argparse
import contextlib
import copy
import datetime as dt
import difflib
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autoloop_grants as G  # noqa: E402
import hub_listener  # noqa: E402

SOURCE = "hermes"
WORKER = "dt-worker"
AGENTS = {"claude": ("dt-claude", "Claude Code（dt-worker）"), "codex": ("dt-codex", "Codex（dt-worker）")}
PROGRAM_DIR = "/Users/Shared/dt-autoloop/bin"     # 工人侧启动脚本（ops/dt_worktree.py，#205 0C），归主账号所有、工人只能运行
NEW_CHANNELS = ("feishu", "secretary")
SECRETARY = "秘书"
PAUSE_REASON = "0C 启动脚本就绪前暂停（#195 0B-2），由常浩决定恢复"
PROMPT = ("这一轮的事件对象是任务单号。任务的五要素在 /Users/Shared/dt-gate/tasks/<任务单号>.md；"
          "开工前核对 /Users/Shared/dt-gate/grants/status.json 里这张单有效、动作在列。"
          "只做任务单写明的范围，只改文件、推非 main 分支、开 PR，不合并。")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
LOCK_WAIT = 10.0                          # 等另一个配置操作结束的最长秒数


class Rejected(Exception):
    """拒绝：说人话的原因，不带钥匙。"""


class Busy(Rejected):
    """配置锁在别人手里，等不到：不是拒绝这件事本身，过一会儿再来。"""


# ---------- 路径 ----------
def home():
    return Path(os.environ.get("AGENT_AUTOLOOP_HOME") or "~/.agent-autoloop").expanduser()


def rules_file():
    return home() / "rules.json"


def evidence_dir():
    return Path(os.environ.get("DT_AUTOLOOP_EVIDENCE") or G.SUPPORT / "evidence/autoloop-config").expanduser()


def source_key_file():
    return Path(os.environ.get("DT_HERMES_SOURCE_KEY_FILE") or G.SUPPORT / "autoloop/hermes-source.key").expanduser()


def channel_key_file(channel):
    d = Path(os.environ.get("DT_AUTOLOOP_CHANNEL_KEY_DIR") or G.SUPPORT / "autoloop").expanduser()
    return d / f"{channel}-channel.key"


def cli():
    return os.environ.get("DT_AUTOLOOP_CLI", "agent-autoloop")


def lock_file():
    return Path(os.environ.get("DT_AUTOLOOP_LOCK") or G.SUPPORT / "autoloop/config.lock").expanduser()


@contextlib.contextmanager
def config_lock():
    """install、issue-source-key 共用的跨进程排他锁（flock）：检查到签发之间不会插进另一个配置操作。派单网关点卡批准
    （核规则 → 签凭证 → 发事件）和 send_pending 发每个派单事件也拿它（#369 第 4 轮 P1-01）：核过的规则在事件送到中台之前
    不会被 install 换掉。不可重入（重入由 autoloop_grants.route_lock 自己管）。"""
    f = lock_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(f.parent, 0o700)
    fd = os.open(f, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        end = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise Busy("另一个中台配置操作还在进行，稍后再试")
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)                      # 关掉就释放锁


# ---------- 生成 ----------
def sandbox_repos():
    """名册里的沙盒仓库：短名 → owner/repo（和 autoloop_grants 的写法一致）。"""
    try:
        r = G.read_roster()
    except G.Rejected as e:
        raise Rejected(str(e)) from None
    out = {}
    for p in r.get("inventory", []):
        if p.get("sandbox") and str(p.get("path", "")).startswith("github:"):
            gh = p["path"].split(":", 1)[1]
            out[gh.split("/")[-1]] = gh
    return out


def dispatch_rule(repo, executor):
    return {"name": G.rule_name(repo, executor),
            "when": {"source": SOURCE, "types": [G.event_type(repo, executor)]},
            "to": AGENTS[executor][0], "cwd": f"~/dt-work/{repo}", "title": "dt-{subject}", "prompt": PROMPT,
            "parallel": 2, "resume": True, "channels": list(NEW_CHANNELS)}


def agent_spec(executor):
    """派单规则交给的 Agent（dt-claude / dt-codex）应有的定义：跑在 dt-worker、program 挂工人侧启动脚本。"""
    return {"name": AGENTS[executor][1], "worker": WORKER, "use": executor, "access": "edit",
            "program": f"{PROGRAM_DIR}/dt-worktree-{executor}"}


def type_hits(pattern, etype):
    """中台 when.types 里的一项会不会接到 etype：原样相等、「*」、「x.*」前缀；别的带 * 的写法认不准，一律当接得到。"""
    if not isinstance(pattern, str):
        return True
    if pattern == etype or pattern == "*":
        return True
    if pattern.endswith(".*") and "*" not in pattern[:-2]:
        return etype.startswith(pattern[:-1])
    return "*" in pattern


def dispatch_route_problem(live, repo, executor):
    """中台正在用的配置 live 里，repo / executor 这一类派单事件实际交给谁（PR #369 L3 第 3 轮 P1-01）。
    要正好一条同名规则、整条（交给哪个 Agent、工作目录、标题、提示词、并发、续接、通道）和 dispatch_rule 生成的一样
    （中台载入时补的 group = 规则名、tell = needed 两个默认值照补；暂停状态不算规则内容，不拦），它交给的 Agent 定义也和
    agent_spec 一样，并且没有别的规则也接 hermes 来源的这类事件。都对返回 None，否则返回一句原因。"""
    name, etype = G.rule_name(repo, executor), G.event_type(repo, executor)
    rules = live.get("rules") if isinstance(live, dict) else None
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        return "中台配置里的规则不是列表，核不了"
    same = [r for r in rules if r.get("name") == name]
    if not same:
        return f"中台没有接 {executor} 派单的规则 {name}"
    if len(same) > 1:
        return f"中台有 {len(same)} 条同名的派单规则 {name}"
    have = {k: v for k, v in same[0].items() if k != "paused"}
    have.setdefault("group", name)
    have.setdefault("tell", "needed")
    want = {"group": name, "tell": "needed", **dispatch_rule(repo, executor)}
    if have != want:
        diff = sorted(k for k in set(have) | set(want) if have.get(k) != want.get(k))
        return f"中台的派单规则 {name} 和应有的不一样（{'、'.join(diff)}），实际交给谁、在哪做对不上卡片"
    aid = AGENTS[executor][0]
    agents = live.get("agents")
    if not isinstance(agents, dict) or agents.get(aid) != agent_spec(executor):
        return f"中台里 Agent {aid} 的定义和应有的不一样（或没有），派单规则交给的不是受控的工人"
    others = []
    for r in rules:
        when = r.get("when")
        if r is same[0] or not isinstance(when, dict) or when.get("source") != SOURCE:
            continue
        types = when.get("types")
        if not isinstance(types, list) or any(type_hits(t, etype) for t in types):
            others.append(str(r.get("name")))
    if others:
        return f"中台还有别的规则也接 {etype}：{'、'.join(others[:3])}，批准后不只交给 {aid}"
    return None


def render(current, owner_open_id, repos):
    """在现有配置上生成数字分身这一块；同样的输入总得到同样的输出（可重复跑）。"""
    if not re.match(r"^ou_[\w-]+$", owner_open_id or ""):
        raise Rejected("常浩的 open_id 没配好（twin_state.owner_open_id）")
    others = secretary_routes(current)
    if others:      # 秘书通道只收派单规则的卡片（规格 §6）；别的规则点名它，秘书就能领到、答到不该它答的卡片
        raise Rejected(f"这些规则把卡片发给秘书通道，不装：{'、'.join(others)}")
    listeners = hermes_listeners(current)
    if listeners:   # hermes 来源只给派单规则用（#369 第 4 轮 P1-01）：别的规则接 hermes 事件，批准过的派单会被它一起接走
        raise Rejected(f"这些不是派单规则却接 hermes 来源的事件，不装：{'、'.join(listeners)}")
    new = copy.deepcopy(current)
    new.setdefault("sources", {})[SOURCE] = {"name": "猛鬼兽派单网关"}
    agents = new.setdefault("agents", {})
    for executor, (aid, _) in AGENTS.items():
        agents[aid] = agent_spec(executor)
    channels = new.setdefault("channels", {})
    if "defaultChannels" in new:
        new["defaultChannels"] = [c for c in new["defaultChannels"] if c != "secretary"]
    else:   # 不写时发到所有通道：先把原有的写死，新增的两个通道不收别处的卡片
        new["defaultChannels"] = [c for c in channels if c not in NEW_CHANNELS] or ["feishu"]
    channels["feishu"] = {"name": "飞书", "answerers": [owner_open_id]}
    channels["secretary"] = {"name": "秘书", "answerers": [SECRETARY]}
    rules = [r for r in new.get("rules", []) if not str(r.get("name", "")).startswith("dispatch-")]
    rules += [dispatch_rule(repo, ex) for repo in sorted(repos) for ex in sorted(AGENTS)]
    new["rules"] = rules
    return new


def hermes_listeners(rules):
    """不是派单规则（名字不以 dispatch- 开头）、却接 hermes 来源事件的规则名。"""
    return [str(r.get("name")) for r in rules.get("rules", []) if isinstance(r, dict)
            and not str(r.get("name", "")).startswith("dispatch-")
            and isinstance(r.get("when"), dict) and r["when"].get("source") == SOURCE]


def secretary_routes(rules):
    """把卡片显式发给秘书通道的非派单规则（秘书只该收派单规则的卡片，规格 §6）。生成配置和核对运行态共用。"""
    return [r.get("name", "?") for r in rules.get("rules", [])
            if not str(r.get("name", "")).startswith("dispatch-") and "secretary" in (r.get("channels") or [])]


def dispatch_names(rules):
    return [r["name"] for r in rules.get("rules", []) if str(r.get("name", "")).startswith("dispatch-")]


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def load_current():
    try:
        return json.loads(rules_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Rejected(f"找不到中台规则文件：{rules_file()}")


def owner_open_id():
    import twin_state as T
    try:
        return T.owner_open_id()
    except T.StateError as e:
        raise Rejected(str(e))


def plan():
    current = load_current()
    new = render(current, owner_open_id(), sandbox_repos())
    diff = "".join(difflib.unified_diff(dumps(current).splitlines(True), dumps(new).splitlines(True),
                                        "rules.json（现在）", "rules.json（装后）"))
    return {"changed": new != current, "diff": diff, "dispatch_rules": dispatch_names(new)}


# ---------- 中台 ----------
def hub_config():
    c = json.loads((home() / "config.json").read_text(encoding="utf-8"))
    return c["url"].rstrip("/"), c["owner"]


def hub(method, path, body=None):
    try:
        url, owner = hub_config()
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:   # 没装、读不了、缺字段：一律说人话，不抛原始异常
        raise Rejected(f"中台配置读不了（{type(e).__name__}，{home() / 'config.json'}）")
    req = urllib.request.Request(url + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {owner}", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        hub_listener.check(url)                  # dt-gate 里：端口上不是 dt-gate 在听就不带本人密钥去（0E-2）
        with opener.open(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise Rejected(f"中台回 HTTP {e.code}（{method} {urllib.parse.unquote(path)}）")
    except (OSError, ValueError) as e:
        raise Rejected(f"连不上中台：{type(e).__name__}")


def state():
    return hub("GET", "/v1/state")


def run_cli(*args):
    env = {**os.environ, "AGENT_AUTOLOOP_HOME": str(home())}
    try:
        r = subprocess.run([cli(), *args], capture_output=True, text=True, timeout=60, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        raise Rejected(f"中台命令行跑不起来：{type(e).__name__}")
    return r.returncode, r.stdout, r.stderr


def key_names():
    code, out, err = run_cli("key", "list")
    if code:
        raise Rejected(f"列不出中台钥匙：{err.strip()[:200]}")
    return {tuple(line.split()[:2]) for line in out.splitlines() if line.strip()}


def lines_by_name(s):
    return {line["name"]: line for line in s.get("lines", [])}


def dispatch_rules_of(rules):
    return {r["name"]: r for r in rules.get("rules", []) if str(r.get("name", "")).startswith("dispatch-")}


def loaded_rules():
    """中台正在用的整份规则：只读打开中台库，读它记下的当前配置（agent-autoloop 内部投影 projections 里
    collection='config'、key='rules' 那一行，值是中台整理过的规则）。协议没有给这个接口，读不到就报错、不放行。"""
    db = home() / "hub.db"
    try:
        con = sqlite3.connect(f"file:{urllib.parse.quote(str(db))}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT data FROM projections WHERE collection = 'config' AND key = 'rules'").fetchone()
        finally:
            con.close()
        return json.loads(row[0])["value"]
    except (sqlite3.Error, TypeError, KeyError, ValueError) as e:
        raise Rejected(f"读不到中台正在用的规则（{type(e).__name__}），不放行")


def mismatches(rules):
    """中台正在用的配置和 rules 比数字分身这一块，逐字段比，不只看 /v1/state 露出来的几项：
    每条派单规则的全部字段（中台载入时只补 group = 规则名、tell = needed 两个默认值，补上再比；上游以后多补别的字段
    会比不上、拒绝放行）、中台在跑而 rules 里没有的派单规则、hermes 来源、dt-* Agent、feishu / secretary 通道、defaultChannels；
    另外中台在用的配置里只要有普通规则把卡片发给秘书通道就算不一致（磁盘上的新版去掉了、但没载入成功时，旧路径还在跑）。"""
    live = loaded_rules()
    want, have = dispatch_rules_of(rules), dispatch_rules_of(live)
    bad = [name for name, r in want.items() if have.get(name) != {"group": name, "tell": "needed", **r}]
    bad += [n for n in have if n not in want]
    pairs = [("sources.hermes", rules.get("sources", {}).get(SOURCE), live.get("sources", {}).get(SOURCE)),
             ("defaultChannels", rules.get("defaultChannels"), live.get("defaultChannels"))]
    pairs += [(f"channels.{c}", rules.get("channels", {}).get(c), live.get("channels", {}).get(c)) for c in NEW_CHANNELS]
    for aid, _ in AGENTS.values():
        a = rules.get("agents", {}).get(aid)
        pairs.append((f"agents.{aid}", a and {"access": "edit", **a}, live.get("agents", {}).get(aid)))
    bad += [name for name, a, b in pairs if a != b]
    bad += [f"{name}←hermes" for name in hermes_listeners(live)]     # 别的规则接 hermes 事件（#369 第 4 轮 P1-01）
    return bad + [f"{name}→secretary" for name in secretary_routes(live)]


def wait_loaded(rules, timeout=30.0):
    """等中台把新规则文件载入：中台库里记下的当前配置和文件逐字段一致。"""
    end = time.monotonic() + timeout
    while True:
        if not mismatches(rules):
            return
        if time.monotonic() >= end:
            raise Rejected("中台没有载入新规则（规则文件不对时中台继续用旧规则）")
        time.sleep(0.5)


def _atomic_write(path, text, mode):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def check_target(target):
    """装配置、签钥匙前，调用方必须写明要改哪个中台目录，且和实际解析出来的目录一致（环境变量继承来的路径不算写明）。"""
    if not target:
        raise Rejected(f"要写明目标中台目录（--target），这次实际会改：{home()}")
    if Path(target).expanduser().resolve() != home().resolve():
        raise Rejected(f"写明的目标中台目录和实际要改的不一致：写的是 {target}，实际是 {home()}")


def install(confirm=False, target=None, now=None):
    if not confirm:
        raise Rejected("装进中台要常浩确认：加 --confirm")
    check_target(target)
    with config_lock():
        return _install(now)


def _install(now):
    current = load_current()
    new = render(current, owner_open_id(), sandbox_repos())
    names = dispatch_names(new)
    if ("source", SOURCE) in key_names() and (dispatch_rules_of(new) != dispatch_rules_of(current)
                                             or mismatches(current)):
        raise Rejected("hermes 来源钥匙已经在了：派单规则不许新增、删减或修改，中台在跑的也要和文件一致（只能原样重装）；"
                       "要改先 agent-autoloop key remove source hermes 并删掉钥匙文件")
    stamp = (now or dt.datetime.now(G.TZ)).strftime("%Y%m%d-%H%M%S")
    ev = evidence_dir()
    ev.mkdir(parents=True, exist_ok=True)
    os.chmod(ev, 0o700)
    draft = ev / f"rules-{stamp}.json"
    _atomic_write(draft, dumps(new), 0o600)
    code, out, err = run_cli("check", str(draft))
    if code:
        raise Rejected(f"新规则没通过 agent-autoloop check：{(err or out).strip()[:300]}")
    backup = ev / f"rules-before-{stamp}.json"
    _atomic_write(backup, rules_file().read_text(encoding="utf-8"), 0o600)
    mode = rules_file().stat().st_mode & 0o777
    if new != current:
        _atomic_write(rules_file(), dumps(new), mode)
        try:
            wait_loaded(new)
        except Rejected:
            _atomic_write(rules_file(), backup.read_text(encoding="utf-8"), mode)
            raise
    for name in names:
        hub("POST", f"/v1/rules/{urllib.parse.quote(name, safe='')}/pause", {"reason": PAUSE_REASON})
    s = state()
    not_paused = [n for n in names if not (lines_by_name(s).get(n) or {}).get("paused")]
    if not_paused:
        raise Rejected(f"这些派单规则没暂停住：{'、'.join(not_paused)}；hermes 来源钥匙先别签")
    return {"ok": True, "changed": new != current, "dispatch_rules": names, "paused": names,
            "backup": str(backup), "draft": str(draft)}


def issue_source_key(confirm=False, target=None):
    if not confirm:
        raise Rejected("签 hermes 来源钥匙要常浩确认：加 --confirm")
    check_target(target)
    with config_lock():
        return _issue_source_key()


def _issue_source_key():
    f = source_key_file()
    if f.exists() or ("source", SOURCE) in key_names():
        raise Rejected("hermes 来源钥匙已经有了；要换先 agent-autoloop key remove source hermes 并删掉钥匙文件")
    rules = load_current()
    if SOURCE not in rules.get("sources", {}):
        raise Rejected("rules.json 里还没有 hermes 来源：先 install")
    names = dispatch_names(rules)
    differ = mismatches(rules)
    s = state()
    if differ:
        raise Rejected(f"中台正在跑的派单规则和 rules.json 不一致：{'、'.join(differ)}；先 install")
    loaded = lines_by_name(s)
    bad = [n for n in names if not (loaded.get(n) or {}).get("paused")]
    if not names or bad:
        raise Rejected(f"派单规则要全部载入并暂停着才签钥匙（没暂停：{'、'.join(bad) or '没有派单规则'}）")
    code, out, err = run_cli("key", "new", "source", SOURCE)
    key = out.strip()
    if code or not KEY_RE.match(key):
        raise Rejected(f"签钥匙失败：{err.strip()[:200]}")
    f.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(f.parent, 0o700)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(key + "\n")
    return {"ok": True, "file": str(f), "mode": "600"}


def issue_channel_key(channel, confirm=False, target=None):
    if channel not in NEW_CHANNELS:
        raise Rejected(f"只签这两条通道的钥匙：{'、'.join(NEW_CHANNELS)}")
    if not confirm:
        raise Rejected(f"签 {channel} 通道钥匙要常浩确认：加 --confirm")
    check_target(target)
    with config_lock():
        return _issue_channel_key(channel)


def _issue_channel_key(channel):
    f = channel_key_file(channel)
    if f.exists() or ("channel", channel) in key_names():
        raise Rejected(f"{channel} 通道钥匙已经有了；要换先 agent-autoloop key remove channel {channel} 并删掉钥匙文件")
    rules = load_current()
    want = [owner_open_id()] if channel == "feishu" else [SECRETARY]
    if (rules.get("channels", {}).get(channel) or {}).get("answerers") != want:
        raise Rejected(f"rules.json 里 {channel} 通道的回答人不是预期的那一个：先 install")
    differ = mismatches(rules)
    if differ:
        raise Rejected(f"中台正在用的配置和 rules.json 不一致：{'、'.join(differ)}；先 install")
    f.parent.mkdir(parents=True, exist_ok=True)       # 先占好 600 文件再签：写不了盘的问题在签之前暴露
    os.chmod(f.parent, 0o700)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        code, out, err = run_cli("key", "new", "channel", channel)
        key = out.strip()
        if code or not KEY_RE.match(key):
            raise Rejected(f"签钥匙失败：{err.strip()[:200]}")
        os.write(fd, (key + "\n").encode())
        os.fsync(fd)
    except BaseException as e:
        os.close(fd)
        f.unlink(missing_ok=True)
        if isinstance(e, Rejected):
            raise
        raise Rejected(f"中台可能已签出 {channel} 通道钥匙、但本地没写成（{type(e).__name__}）：先 agent-autoloop key list 看，"
                       f"有就经常浩同意后 agent-autoloop key remove channel {channel} 再重签")
    os.close(fd)
    return {"ok": True, "channel": channel, "file": str(f), "mode": "600"}


# ---------- 手写的规则片段（add-rules / remove-rules） ----------
RULES_DIR = HERE.parents[1] / "ops/autoloop_rules"
FRAGMENT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FRAGMENT_KEYS = {"sources", "agents", "rules"}


def fragment(name):
    """按名字读片段：只认 RULES_DIR 下的 <名字>.json（dt-gate 里是 root 所有的副本），不收任意路径。"""
    if not FRAGMENT_NAME.match(name or ""):
        raise Rejected(f"片段名只能是小写字母、数字和连字符，不认 {name!r}")
    path = RULES_DIR / f"{name}.json"
    try:
        frag = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Rejected(f"没有这个规则片段：{path}")
    except (OSError, ValueError) as e:
        raise Rejected(f"规则片段读不了（{type(e).__name__}）：{path}")
    if not isinstance(frag, dict) or not set(frag) <= FRAGMENT_KEYS or not isinstance(frag.get("rules"), list):
        raise Rejected("规则片段只能有 sources、agents、rules 三项，rules 要是列表")
    for k in ("sources", "agents"):
        if not isinstance(frag.get(k, {}), dict):
            raise Rejected(f"规则片段的 {k} 要是对象")
    names = [r.get("name") if isinstance(r, dict) else None for r in frag["rules"]]
    if not frag["rules"] or not all(isinstance(n, str) and n for n in names) or len(set(names)) != len(names):
        raise Rejected("规则片段里每条规则都要有名字，而且不重名")
    bad = [n for n in names if n.startswith("dispatch-")]
    bad += [f"{r['name']}←hermes" for r in frag["rules"] if (r.get("when") or {}).get("source") == SOURCE]
    bad += [f"{r['name']}→secretary" for r in frag["rules"] if "secretary" in (r.get("channels") or [])]
    bad += [f"sources.{SOURCE}"] * (SOURCE in frag.get("sources", {}))
    bad += [f"agents.{a}" for a in frag.get("agents", {}) if a in {aid for aid, _ in AGENTS.values()}]
    if bad:
        raise Rejected(f"规则片段碰了数字分身这一块，不装：{'、'.join(bad)}")
    return frag


def with_fragment(current, frag):
    new = copy.deepcopy(current)
    for k in ("sources", "agents"):
        new.setdefault(k, {}).update(copy.deepcopy(frag.get(k, {})))
    names = {r["name"] for r in frag["rules"]}
    new["rules"] = [r for r in new.get("rules", []) if r.get("name") not in names] + copy.deepcopy(frag["rules"])
    return new


def without_fragment(current, frag):
    new = copy.deepcopy(current)
    names = {r["name"] for r in frag["rules"]}
    new["rules"] = [r for r in new.get("rules", []) if r.get("name") not in names]
    used_agents = {r.get("to") for r in new["rules"]}
    used_sources = {(r.get(k) or {}).get("source") for r in new["rules"] for k in ("when", "emits")}
    for aid in frag.get("agents", {}):
        if aid not in used_agents:
            new.get("agents", {}).pop(aid, None)
    for sid in frag.get("sources", {}):
        if sid not in used_sources:
            new.get("sources", {}).pop(sid, None)
    return new


def dt_block(rules):
    """数字分身这一块：add-rules / remove-rules 前后必须一字不差。"""
    return {"dispatch": dispatch_rules_of(rules), "source": rules.get("sources", {}).get(SOURCE),
            "agents": {aid: rules.get("agents", {}).get(aid) for aid, _ in AGENTS.values()},
            "channels": {c: rules.get("channels", {}).get(c) for c in NEW_CHANNELS},
            "defaultChannels": rules.get("defaultChannels")}


def fragment_loaded(new, frag, removing):
    """中台在用的配置里，片段的规则和 Agent 是不是已经是 new 里的样子（中台载入时补的默认字段不算差别）。"""
    live = loaded_rules()
    live_rules = {r.get("name"): r for r in live.get("rules", []) if isinstance(r, dict)}
    want_rules = {r["name"]: r for r in new.get("rules", [])}
    for r in frag["rules"]:
        have, want = live_rules.get(r["name"]), want_rules.get(r["name"])
        if removing:
            if have is not None:
                return False
        elif have is None or any(have.get(k) != v for k, v in want.items()):
            return False
    for aid in frag.get("agents", {}):
        have, want = live.get("agents", {}).get(aid), new.get("agents", {}).get(aid)
        if want is None:
            if have is not None:
                return False
        elif not isinstance(have, dict) or any(have.get(k) != v for k, v in want.items()):
            return False
    return True


def change_fragment(name, removing, confirm=False, target=None, now=None, timeout=30.0):
    if not confirm:
        raise Rejected("改中台规则要常浩确认：加 --confirm")
    check_target(target)
    frag = fragment(name)
    with config_lock():
        current = load_current()
        new = (without_fragment if removing else with_fragment)(current, frag)
        if dt_block(new) != dt_block(current):
            raise Rejected("这次改动会改到数字分身这一块（派单规则、hermes 来源、dt-* Agent、飞书 / 秘书通道），不装")
        listeners, routes = hermes_listeners(new), secretary_routes(new)
        if listeners or routes:
            raise Rejected(f"改完会有规则接 hermes 来源或点名秘书通道，不装：{'、'.join(listeners + routes)}")
        if new == current:
            return {"ok": True, "changed": False, "fragment": name}
        stamp = (now or dt.datetime.now(G.TZ)).strftime("%Y%m%d-%H%M%S")
        ev = evidence_dir()
        ev.mkdir(parents=True, exist_ok=True)
        os.chmod(ev, 0o700)
        draft = ev / f"rules-{name}-{stamp}.json"
        _atomic_write(draft, dumps(new), 0o600)
        code, out, err = run_cli("check", str(draft))
        if code:
            raise Rejected(f"改完的规则没通过 agent-autoloop check：{(err or out).strip()[:300]}")
        backup = ev / f"rules-before-{name}-{stamp}.json"
        _atomic_write(backup, rules_file().read_text(encoding="utf-8"), 0o600)
        mode = rules_file().stat().st_mode & 0o777
        _atomic_write(rules_file(), dumps(new), mode)
        end = time.monotonic() + timeout
        while not fragment_loaded(new, frag, removing):
            if time.monotonic() >= end:
                _atomic_write(rules_file(), backup.read_text(encoding="utf-8"), mode)
                raise Rejected("中台没有载入改过的规则，已把规则文件换回原样")
            time.sleep(0.5)
        return {"ok": True, "changed": True, "fragment": name, "removed" if removing else "added":
                [r["name"] for r in frag["rules"]], "backup": str(backup), "draft": str(draft)}


def status():
    rules = load_current()
    s = state()
    loaded = lines_by_name(s)
    keys = key_names()
    return {"source_hermes": {"in_rules": SOURCE in rules.get("sources", {}), "key": ("source", SOURCE) in keys,
                              "key_file": source_key_file().exists()},
            "channels": {c: {"in_rules": c in rules.get("channels", {}), "key": ("channel", c) in keys,
                             "key_file": channel_key_file(c).exists(),
                             "answerers": len(rules.get("channels", {}).get(c, {}).get("answerers", []))}
                         for c in NEW_CHANNELS},
            "dispatch_rules": {n: {"loaded": n in loaded, "paused": bool((loaded.get(n) or {}).get("paused"))}
                               for n in dispatch_names(rules)}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("status")
    for name in ("install", "issue-source-key"):
        sp = sub.add_parser(name)
        sp.add_argument("--confirm", action="store_true")
        sp.add_argument("--target", help="要改的中台目录，必须和实际的一致，如 ~/.agent-autoloop")
    for name in ("add-rules", "remove-rules"):
        sp = sub.add_parser(name)
        sp.add_argument("fragment", help="ops/autoloop_rules/ 下的片段名（不带 .json）")
        sp.add_argument("--confirm", action="store_true")
        sp.add_argument("--target", help="要改的中台目录，必须和实际的一致，如 ~/.agent-autoloop")
    sp = sub.add_parser("issue-channel-key")
    sp.add_argument("channel", choices=NEW_CHANNELS)
    sp.add_argument("--confirm", action="store_true")
    sp.add_argument("--target", help="要改的中台目录，必须和实际的一致，如 ~/.agent-autoloop")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            out = plan()
            print(out["diff"] or "（没有要改的）")
            print(json.dumps({k: v for k, v in out.items() if k != "diff"}, ensure_ascii=False))
            return 0
        out = {"install": lambda: install(a.confirm, a.target),
               "issue-source-key": lambda: issue_source_key(a.confirm, a.target),
               "issue-channel-key": lambda: issue_channel_key(a.channel, a.confirm, a.target),
               "add-rules": lambda: change_fragment(a.fragment, False, a.confirm, a.target),
               "remove-rules": lambda: change_fragment(a.fragment, True, a.confirm, a.target),
               "status": status}[a.cmd]()
    except Rejected as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
