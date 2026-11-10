"""Hermes 每日巡检：纯脚本、不调用模型。正常时只写日志，有异常才用猛鬼兽机器人身份发飞书私聊。

用法：/usr/bin/python3 health_check.py [--dry-run]
--dry-run 只打印，不发消息。
由 launchd 任务 cn.digital-twin.health 每天 09:45 调用，只查网关、飞书连接、24 小时内异常退出、回退模型、
辅助任务失败和定时任务；网关、异常退出、回退、辅助任务猛鬼兽和亚古兽各查一遍（#147），另提醒飞书技能源头有更新。
launchd 起的 python 读不了 ~/Documents，沙盒和仓库自检已搬到 self_check.py（Hermes 无模型定时任务调用）。
"""
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
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


def check_bots():
    """每只机器人各查一遍：网关、异常退出、回退模型、辅助任务失败；问题前标明是哪只。"""
    issues = []
    for name, label, sub in BOTS:
        home = bot_home(sub)
        found = (check_gateway(label, home) + check_unclean_exits(home)
                 + check_fallback(home=home) + check_aux_errors(home=home))
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


def send(msg, key, now):
    r = run(["lark-cli", "--profile", LARK_PROFILE, "im", "+messages-send", "--as", "bot",
             "--chat-id", DM_CHAT, "--text", msg, "--idempotency-key", key])
    with open(LOG_DIR / f"{now[:7]}.log", "a") as f:
        f.write(f"{now} {key} 发送 {'成功' if r.returncode == 0 else '失败：' + (r.stderr or r.stdout)[:200]}\n")


def main():
    dry = "--dry-run" in sys.argv
    issues = check_bots() + check_skill_sync() + check_cron()
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
