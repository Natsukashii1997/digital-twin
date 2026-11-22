"""Hermes 每日巡检：纯脚本、不调用模型。正常时只写日志，有异常才用猛鬼兽机器人身份发飞书私聊。

用法：/usr/bin/python3 health_check.py [--dry-run]
--dry-run 只打印，不发消息。
由 launchd 任务 cn.digital-twin.health 每天 09:45 调用，只查网关、飞书连接、24 小时内异常退出、回退模型、
辅助任务失败和定时任务；网关、异常退出、回退、辅助任务猛鬼兽和亚古兽各查一遍（#147），另提醒飞书技能源头有更新。
另查两件唤醒类的事（P9，全局坑 G-19 / G-20）：网关 launchd plist 的 EnvironmentVariables 里代理变量齐不齐
（只读键名，不读、不输出值；也不读任何进程的环境变量，G-42）；定时任务最近一次晚点或失败时，附那段时间 pmset 的睡眠 / 唤醒记录。
另查工人账号 dt-worker 的两道隔离（#190）：过滤代理在跑且拦得住本机地址、门卫规则（pf）在生效。
launchd 起的 python 读不了 ~/Documents，沙盒和仓库自检已搬到 self_check.py（Hermes 无模型定时任务调用）。
"""
import datetime as dt
import json
import os
import plistlib
import pwd
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
HERMES = HOME / ".hermes"
LOG_DIR = HOME / "Library/Application Support/DigitalTwin/evidence/health"
LARK_PROFILE = "cli_a96f5f9ac63adcd4"             # 猛鬼兽
DM_CHAT = "oc_ce2cbf195f8de9f80bc505a00cb4456d"   # 猛鬼兽和常浩的私聊
HEARTBEAT_STALE_MIN = 10
AGENT_SKILLS = HOME / ".agents/skills"   # 飞书 lark-* 技能的源头；两只机器人各有一份复制件
# (名字, launchd 标签, 相对 HERMES 的目录)；猛鬼兽是默认 profile，亚古兽是 profiles/agumon（#147）
BOTS = (("猛鬼兽", "ai.hermes.gateway", ""), ("亚古兽", "ai.hermes.gateway-agumon", "profiles/agumon"))
EGRESS_PORT = 8090                       # 工人账号的过滤代理（ops/dt_worker_egress.py，#190）
PF_STATUS = Path("/Library/Application Support/DigitalTwin/pf-dt-worker.status")   # 门卫规则每 5 分钟刷新一次
PF_STALE_MIN = 10   # 每 5 分钟重载并逐条回读一次；超过两个周期没刷新就算停了（L3 P1-03）
PF_USERS = ("dt-worker", "dt-secretary")   # 门卫规则要管的账号：本机有哪个就要管上哪个（ADR-015 0E-1）
LAUNCH_AGENTS = HOME / "Library/LaunchAgents"
PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")   # G-19：大小写两套
LATE_MIN = 10                            # 定时任务晚这么多分钟才派发算晚点
PMSET_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) [+-]\d{4} (Sleep|Wake|DarkWake) *\t(.*)$")   # 不含「Wake Requests」
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(HOME / ".local/bin")


def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "PATH": PATH})


def bot_home(sub):
    return HERMES / sub if sub else HERMES


def check_gateway(label="ai.hermes.gateway", home=None):
    home = home or HERMES
    issues = []
    out = run(["launchctl", "list"]).stdout
    line = next((l for l in out.splitlines() if l.endswith("\t" + label)), "")
    if not line:
        return [f"Hermes 网关常驻任务没有加载（{label}）"]
    if line.split("\t")[0] == "-":
        issues.append(f"Hermes 网关进程没在运行（上次退出码 {line.split(chr(9))[1]}）")
    try:
        st = json.loads((home / "gateway_state.json").read_text())
        if st.get("gateway_state") != "running":
            issues.append(f"网关状态是 {st.get('gateway_state')}，不是 running")
        fs = st.get("platforms", {}).get("feishu", {})
        if fs.get("state") != "connected":
            issues.append(f"飞书连接状态是 {fs.get('state')}（{fs.get('error_message') or '无错误信息'}）")
        age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(st["updated_at"])
        if age > dt.timedelta(minutes=HEARTBEAT_STALE_MIN):
            issues.append(f"网关心跳已经 {int(age.total_seconds() // 60)} 分钟没更新")
    except Exception as e:
        issues.append(f"读不了网关状态文件（{e}）")
    return issues


# Hermes 源码里写死的日志原文（gateway/lifecycle_ledger），升级改了措辞这项就会失效
UNCLEAN_EXIT = "exited UNCLEANLY"


def check_unclean_exits(home=None, now=None):
    """过去 24 小时网关有没有异常退出（被杀、崩溃、整机睡死）：新进程起来时在 errors.log 记一条。
    每日巡检只看当下状态，夜里断了几小时又自己恢复的情况只能靠这条补上（#147）。"""
    home = home or HERMES
    now = now or dt.datetime.now()
    since = now - dt.timedelta(hours=24)
    hits = []
    try:
        with open(home / "logs/errors.log", encoding="utf-8", errors="replace") as f:
            for line in f:
                if UNCLEAN_EXIT not in line:
                    continue
                try:
                    at = dt.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if since <= at <= now:
                    hits.append((at, line))
    except FileNotFoundError:
        return []
    except Exception as e:
        return [f"读不了网关错误日志（{e}）"]
    if not hits:
        return []
    at, line = hits[-1]
    last = f"{at:%m-%d %H:%M} 重新拉起"
    if "last_heartbeat_at=" in line:
        try:
            hb = dt.datetime.fromisoformat(line.split("last_heartbeat_at=", 1)[1].split()[0]).astimezone()
            last = f"心跳停在 {hb:%m-%d %H:%M}，{last}"
        except ValueError:
            pass
    return [f"过去 24 小时网关异常退出 {len(hits)} 次（最近一次{last}）"]


def check_skill_sync():
    """两只机器人的 lark-* 技能是从 ~/.agents/skills 复制的，源头更新不会自动同步：源头比副本新就提醒。
    机器人自己（后台复盘）改过的副本比源头新，不报（#147）。"""
    stale = []
    for name, _, sub in BOTS:
        names = sorted(d.parent.name for d in (bot_home(sub) / "skills/lark").glob("lark-*/SKILL.md")
                       if (AGENT_SKILLS / d.parent.name / "SKILL.md").exists()
                       and (AGENT_SKILLS / d.parent.name / "SKILL.md").stat().st_mtime > d.stat().st_mtime + 1)
        if names:
            stale.append(f"{name} {'、'.join(names)}")
    if not stale:
        return []
    return [f"飞书技能源头（~/.agents/skills）比机器人副本新，核对后同步（副本里机器人自己加的内容先留存）：{'；'.join(stale)}"]


def env_key_names(path):
    """plist 的 EnvironmentVariables 里有哪些变量名。只取键名，值不进内存以外的任何地方（不打印、不记日志）。"""
    with open(path, "rb") as f:
        env = plistlib.load(f).get("EnvironmentVariables") or {}
    return set(env) if isinstance(env, dict) else set()


def dotenv_key_names(path):
    """.env 里写了哪些变量名（只看等号左边）。"""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    return {m.group(1) for line in lines if (m := re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line))}


def check_gateway_proxy(label, home=None, agents=None):
    """G-19：网关 plist 里代理变量齐不齐（大小写两套）。只报缺了哪些变量名；plist 不在由 check_gateway 报「没有加载」。"""
    path = (agents or LAUNCH_AGENTS) / f"{label}.plist"
    try:
        keys = env_key_names(path)
    except FileNotFoundError:
        return []
    except Exception as e:   # noqa: BLE001  格式坏了也只报文件名和错误类型
        return [f"读不了网关配置 {path.name}（{type(e).__name__}），代理变量没法核对"]
    missing = [k for k in PROXY_KEYS if k not in keys]
    if not missing:
        return []
    backed = all(k in dotenv_key_names((home or HERMES) / ".env") for k in missing)
    why = ("对应 .env 里有，网关启动时会读进来，暂时不影响；但说明 plist 被 `hermes gateway restart` 之类重写过"
           if backed else "对应 .env 里也没有，网关会直连、模型请求超时")
    return [f"网关 plist（{path.name}）的 EnvironmentVariables 缺代理变量 {', '.join(missing)}（只核对了变量名）：{why}；"
            "按全局坑 G-19 补回，改完 `plutil -lint`，再 bootout / bootstrap 重装"]


def _parse_time(s):
    try:
        t = dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.astimezone()


_pmset_cache = []


def pmset_events():
    """pmset -g log 里的睡眠 / 唤醒记录 [(本地时间, 类型, 说明)]；同一次巡检只读一遍。读不了返回 None。"""
    if not _pmset_cache:
        try:
            r = run(["pmset", "-g", "log"], timeout=300)   # 日志十几 MB，机器忙时要一两分钟
            text = r.stdout if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            text = None
        events = None
        if text is not None:
            events = []
            for line in text.splitlines():
                m = PMSET_LINE.match(line)
                if m:
                    events.append((dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), m.group(2), m.group(3)))
        _pmset_cache.append(events)
    return _pmset_cache[0]


def _event_note(kind, text):
    why = re.search(r"due to '?([^':]+)", text)
    power = re.search(r"Using (Batt|AC)\w*(?: \(Charge:\s*(\d+)%\))?", text, re.I)
    bits = [kind]
    if why and kind == "Sleep":   # 唤醒的原因多是硬件代号，不列
        bits.append(why.group(1).strip()[:40])
    if power:
        bits.append(f"电池 {power.group(2)}%" if power.group(1).lower() == "batt" and power.group(2)
                    else "电池" if power.group(1).lower() == "batt" else "插电")
    return " ".join(bits)


def sleep_summary(start, end, events=None):
    """G-20：[start, end] 这段时间机器醒没醒。start、end 是带时区的时间。"""
    events = pmset_events() if events is None else events
    if events is None:
        return "读不了 pmset 睡眠记录，没法判断当时机器醒没醒"
    lo, hi = (start.astimezone().replace(tzinfo=None), end.astimezone().replace(tzinfo=None))
    hit = [e for e in events if lo <= e[0] <= hi]
    if not hit:
        return "那段时间 pmset 里没有睡眠 / 唤醒记录，机器应该醒着，原因要另查"
    counts = {k: sum(1 for e in hit if e[1] == k) for k in ("Sleep", "DarkWake", "Wake")}
    shown = "；".join(f"{e[0]:%H:%M} {_event_note(e[1], e[2])}" for e in hit[:4]) + ("；…" if len(hit) > 4 else "")
    told = "、".join(f"{k} {n} 次" for k, n in counts.items() if n)
    return f"那段时间机器在睡（pmset：{told}；{shown}），多半是睡眠拖的（全局坑 G-20），插电不合盖就好"


def check_job_timing(home=None, now=None, late_min=LATE_MIN):
    """定时任务最近一次晚点或失败（过去 24 小时内），附那段时间的睡眠记录。只读 jobs.json，不运行 hermes。"""
    now = now or dt.datetime.now().astimezone()
    try:
        data = json.loads(((home or HERMES) / "cron/jobs.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = data.get("jobs", data) if isinstance(data, dict) else data
    issues = []
    for j in jobs if isinstance(jobs, list) else []:
        if not isinstance(j, dict) or not j.get("enabled"):
            continue
        name = str(j.get("name") or j.get("id") or "?").split("（")[0][:30]
        ld = j.get("last_dispatch") if isinstance(j.get("last_dispatch"), dict) else {}
        sched, sent = _parse_time(ld.get("scheduled_at")), _parse_time(ld.get("dispatched_at"))
        ran = _parse_time(j.get("last_run_at"))
        late = ld.get("lateness_seconds")
        if sched and sent and isinstance(late, (int, float)) and late >= late_min * 60 and now - sched <= dt.timedelta(days=1):
            issues.append(f"定时任务「{name}」{sched.astimezone():%m-%d %H:%M} 的那次晚了 {int(late // 60)} 分钟才派发；"
                          + sleep_summary(sched, sent))
        elif j.get("last_status") not in (None, "ok") and ran and now - ran <= dt.timedelta(days=1):
            start = sent or ran - dt.timedelta(minutes=30)
            issues.append(f"定时任务「{name}」{ran.astimezone():%m-%d %H:%M} 那次没跑成；" + sleep_summary(start, ran))
    return issues


def check_bots():
    """每只机器人各查一遍：网关、网关代理变量、异常退出、回退模型、辅助任务失败、定时任务晚点或失败；问题前标明是哪只。"""
    issues = []
    for name, label, sub in BOTS:
        home = bot_home(sub)
        found = (check_gateway(label, home) + check_gateway_proxy(label, home) + check_unclean_exits(home)
                 + check_fallback(home=home) + check_aux_errors(home=home) + check_job_timing(home))
        issues += [f"{name}：{i}" for i in found]
    return issues


def yesterday():
    return dt.date.today() - dt.timedelta(days=1)


def check_fallback(day=None, home=None):
    """昨天主对话和辅助任务（标题生成、压缩等）有没有走回退模型（DeepSeek）。"""
    home = home or HERMES
    start = dt.datetime.combine(day or yesterday(), dt.time()).timestamp()
    end = start + 86400
    try:
        # 不用 mode=ro：网关停着时 WAL 的 -shm 文件可能不在，只读连接会打不开；这里只做查询
        con = sqlite3.connect(home / "state.db", timeout=10)
        rows = con.execute(
            "select coalesce(u.task,''), sum(u.api_call_count) from session_model_usage u "
            "join sessions s on s.id=u.session_id "
            "where u.billing_provider='deepseek' "
            "and s.source!='oneshot' "   # 排除本机 hermes -z 的一次性测试
            "and u.last_seen>=? and u.last_seen<? group by 1 order by 1", (start, end)).fetchall()
        con.close()
    except Exception as e:
        return [f"读不了 Hermes 会话库（{e}）"]
    n = sum(c for t, c in rows if not t)
    aux = [f"{t} {c} 次" for t, c in rows if t and c]
    issues = [f"昨天有 {n} 次主对话调用走了回退模型 deepseek-flash（主模型额度、登录或网络可能有问题）"] if n else []
    if aux:
        issues.append(f"昨天辅助任务走了回退模型 deepseek-flash：{'、'.join(aux)}")
    return issues


# Hermes 源码里写死的日志原文（agent/context_compressor.py、agent/auxiliary_client.py），升级改了措辞这项就会失效
AUX_LOG_ERRORS = [
    ("Failed to generate context summary", "昨天压缩失败 {} 次（长会话会越积越长）"),
    ("no Codex OAuth token found", "昨天 {} 次取不到 Codex 凭证，辅助任务会回退或失败；先查凭证冷却，见 agent-kit G-13"),
]


def check_aux_errors(day=None, home=None):
    """昨天 agent.log 里辅助任务直接失败的记录：这类失败不进会话库，只能看日志。"""
    home = home or HERMES
    prefix = (day or yesterday()).isoformat()
    counts = [0] * len(AUX_LOG_ERRORS)
    try:
        with open(home / "logs/agent.log", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith(prefix):
                    for i, (needle, _) in enumerate(AUX_LOG_ERRORS):
                        counts[i] += needle in line
    except Exception as e:
        return [f"读不了 Hermes 日志（{e}）"]
    return [msg.format(c) for (_, msg), c in zip(AUX_LOG_ERRORS, counts) if c]


def check_cron():
    """只看还没处理的失败记录（detected、alerted）；已 ack 或已解决的不算。"""
    ids, first = [], ""
    for state in ("detected", "alerted"):
        r = run(["hermes", "cron", "incidents", "--state", state])
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == state:
                ids.append(parts[0])
            elif not first and line.strip().startswith("Error:"):
                first = line.split("Error:", 1)[1].strip()[:80]
    if not ids:
        return []
    return [f"定时任务有 {len(ids)} 条未处理的失败记录（{first or ids[0]}）；看 `hermes cron incidents`，处理完 ack"]


def check_egress(port=EGRESS_PORT):
    """过滤代理在跑，而且让它转到本机地址时回 403。"""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=3) as s:
            s.sendall(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1:443\r\n\r\n")
            line = s.recv(200).split(b"\r\n", 1)[0].decode(errors="replace")
    except OSError as e:
        return [f"工人账号的过滤代理没在运行（127.0.0.1:{port}，{type(e).__name__}）；"
                "`launchctl kickstart -k gui/$(id -u)/cn.digital-twin.egress`"]
    if line.split()[1:2] != ["403"]:
        return [f"工人账号的过滤代理没拦住本机地址（回的是「{line[:60]}」）"]
    return []


def _account_exists(name):
    try:
        pwd.getpwnam(name)
        return True
    except KeyError:
        return False


def check_pf(path=PF_STATUS, now=None, accounts=None):
    """门卫规则的状态文件由 root 每 5 分钟写一次：在生效、规则数对、没过期、本机有的工人 / 秘书账号都管上了。

    巡检以主账号身份跑，读不了 pf 的实际规则（要 root）；实际规则由开机任务每 5 分钟重新生成、加载并逐条回读，
    对上才写 state=ok，所以「10 分钟内的 state=ok」就是最近一次核对真实运行态的结果。
    0E-1 之前的旧版状态文件没有 state 字段，按旧规则判。"""
    install = "主账号终端运行 `sudo bash /Users/Shared/dt-autoloop/dt_worker_pf.sh install`"
    try:
        text = path.read_text()
        age_min = ((now or time.time()) - path.stat().st_mtime) / 60
    except OSError:
        return [f"工人账号的门卫规则没装（没有状态文件）；{install}"]
    fields = dict(kv.split("=", 1) for kv in text.split() if "=" in kv)
    rules = int(fields["rules"]) if fields.get("rules", "").isdigit() else 0
    if "state" in fields and fields["state"] != "ok":
        why = fields.get("reason", "正在加载").replace("_", " ")
        return [f"工人账号的门卫规则没在生效（{fields['state']}：{why}）；{install}"]
    if fields.get("enabled") != "yes" or rules < 3:
        return [f"工人账号的门卫规则没在生效（{text.strip()[:80]}）"]
    if age_min > PF_STALE_MIN:
        return [f"工人账号的门卫规则 {int(age_min)} 分钟没刷新，开机任务 cn.digital-twin.pf-dt-worker 可能停了"]
    if accounts is None:
        accounts = [u for u in PF_USERS if _account_exists(u)]
    managed = set(fields.get("users", "dt-worker").split(","))   # 0A 版状态文件没有 users，只管工人
    missing = [u for u in accounts if u not in managed]
    if missing:
        return [f"门卫规则没管上 {'、'.join(missing)}（账号已建，规则是旧版或没加载上）；{install}"]
    return []


def send(msg, key, now):
    r = run(["lark-cli", "--profile", LARK_PROFILE, "im", "+messages-send", "--as", "bot",
             "--chat-id", DM_CHAT, "--text", msg, "--idempotency-key", key])
    with open(LOG_DIR / f"{now[:7]}.log", "a") as f:
        f.write(f"{now} {key} 发送 {'成功' if r.returncode == 0 else '失败：' + (r.stderr or r.stdout)[:200]}\n")


def main():
    dry = "--dry-run" in sys.argv
    issues = check_bots() + check_skill_sync() + check_cron() + check_egress() + check_pf()
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / f"{now[:7]}.log", "a") as f:
        f.write(f"{now} {'OK' if not issues else 'ISSUES: ' + ' | '.join(issues)}\n")
    if not issues:
        print(f"{now} 巡检正常")
    else:
        msg = "🩺 Hermes 巡检发现问题\n" + "\n".join(f"- {i}" for i in issues) + \
              "\n处理：网关问题重启对应网关 `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`（亚古兽是 `ai.hermes.gateway-agumon`）；" \
              "详情见 ~/.hermes/logs、~/.hermes/profiles/agumon/logs"
        print(msg)
        if not dry:
            send(msg, f"health-{now}", now)


if __name__ == "__main__":
    main()
