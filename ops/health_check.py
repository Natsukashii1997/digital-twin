"""Hermes 每日巡检：纯脚本、不调用模型。正常时只写日志，有异常才用猛鬼兽机器人身份发飞书私聊。

用法：/usr/bin/python3 health_check.py [--dry-run] [--sandbox]   （--dry-run 只打印，不发消息；--sandbox 不是 1 号也查沙盒）
由 launchd 任务 cn.digital-twin.health 每天 09:45 调用。每月 1 号另查沙盒，有超过 30 天没动的就单独发一条提醒，只提醒、不删。
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
SANDBOX_DIRS = [HOME / "Sandbox", HOME / "Documents/Codex"]   # M3-Q3：沙盒和 Codex 按日期建的旧目录
SANDBOX_STALE_DAYS = 30
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(HOME / ".local/bin")


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PATH": PATH})


def check_gateway():
    issues = []
    out = run(["launchctl", "list"]).stdout
    line = next((l for l in out.splitlines() if l.endswith("\tai.hermes.gateway")), "")
    if not line:
        return ["Hermes 网关常驻任务没有加载（ai.hermes.gateway）"]
    if line.split("\t")[0] == "-":
        issues.append(f"Hermes 网关进程没在运行（上次退出码 {line.split(chr(9))[1]}）")
    try:
        st = json.loads((HERMES / "gateway_state.json").read_text())
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


def check_fallback():
    """昨天主对话有没有走回退模型（DeepSeek）。"""
    y = dt.date.today() - dt.timedelta(days=1)
    start = dt.datetime.combine(y, dt.time()).timestamp()
    end = start + 86400
    try:
        # 不用 mode=ro：网关停着时 WAL 的 -shm 文件可能不在，只读连接会打不开；这里只做查询
        con = sqlite3.connect(HERMES / "state.db", timeout=10)
        n = con.execute(
            "select coalesce(sum(u.api_call_count),0) from session_model_usage u "
            "join sessions s on s.id=u.session_id "
            "where u.billing_provider='deepseek' and (u.task is null or u.task='') "
            "and s.source!='oneshot' "   # 排除本机 hermes -z 的一次性测试
            "and u.last_seen>=? and u.last_seen<?", (start, end)).fetchone()[0]
        con.close()
    except Exception as e:
        return [f"读不了 Hermes 会话库（{e}）"]
    return [f"昨天有 {n} 次主对话调用走了回退模型 deepseek-flash（Codex 额度或登录可能有问题）"] if n else []


def check_cron():
    r = run(["hermes", "cron", "incidents"])
    text = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or not text or "no " in text.lower()[:40] or "没有" in text[:20]:
        return []
    return [f"定时任务有未处理的失败记录：{text.splitlines()[0][:120]}"]


def check_sandbox():
    """顶层目录按自身修改时间算；返回超过 30 天没动的路径。"""
    cutoff = dt.datetime.now().timestamp() - SANDBOX_STALE_DAYS * 86400
    return [p for d in SANDBOX_DIRS if d.is_dir() for p in sorted(d.iterdir())
            if not p.name.startswith(".") and p.stat().st_mtime < cutoff]


def send(msg, key, now):
    r = run(["lark-cli", "--profile", LARK_PROFILE, "im", "+messages-send", "--as", "bot",
             "--chat-id", DM_CHAT, "--text", msg, "--idempotency-key", key])
    with open(LOG_DIR / f"{now[:7]}.log", "a") as f:
        f.write(f"{now} {key} 发送 {'成功' if r.returncode == 0 else '失败：' + (r.stderr or r.stdout)[:200]}\n")


def main():
    dry = "--dry-run" in sys.argv
    issues = check_gateway() + check_fallback() + check_cron()
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / f"{now[:7]}.log", "a") as f:
        f.write(f"{now} {'OK' if not issues else 'ISSUES: ' + ' | '.join(issues)}\n")
    if not issues:
        print(f"{now} 巡检正常")
    else:
        msg = "🩺 Hermes 巡检发现问题\n" + "\n".join(f"- {i}" for i in issues) + \
              "\n处理：重启网关 `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`；详情见 ~/.hermes/logs"
        print(msg)
        if not dry:
            send(msg, f"health-{now}", now)
    if dt.date.today().day != 1 and "--sandbox" not in sys.argv:
        return
    stale = check_sandbox()
    with open(LOG_DIR / f"{now[:7]}.log", "a") as f:
        f.write(f"{now} 沙盒超过 {SANDBOX_STALE_DAYS} 天：{len(stale)} 项\n")
    if not stale:
        return
    home = str(HOME)
    msg = (f"🧹 沙盒提醒（每月 1 号）：~/Sandbox 和 ~/Documents/Codex 里有 {len(stale)} 项超过 {SANDBOX_STALE_DAYS} 天没动\n"
           + "\n".join(f"- {str(p).replace(home, '~')}" for p in stale[:10])
           + (f"\n- ……另有 {len(stale) - 10} 项" if len(stale) > 10 else "")
           + "\n只是提醒，不会自动删；要清理就让 Claude 或 Codex 列清单给你定。")
    print(msg)
    if not dry:
        send(msg, f"sandbox-{now[:7]}", now)


if __name__ == "__main__":
    main()
