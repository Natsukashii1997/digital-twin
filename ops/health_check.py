"""Hermes 每日巡检：纯脚本、不调用模型。正常时只写日志，有异常才用猛鬼兽机器人身份发飞书私聊。

用法：/usr/bin/python3 health_check.py [--dry-run]
--dry-run 只打印，不发消息。
由 launchd 任务 cn.digital-twin.health 每天 09:45 调用，只查网关、飞书连接、回退模型和定时任务。
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
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(HOME / ".local/bin")


def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
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


def yesterday():
    return dt.date.today() - dt.timedelta(days=1)


def check_fallback(day=None):
    """昨天主对话和辅助任务（标题生成、压缩等）有没有走回退模型（DeepSeek）。"""
    start = dt.datetime.combine(day or yesterday(), dt.time()).timestamp()
    end = start + 86400
    try:
        # 不用 mode=ro：网关停着时 WAL 的 -shm 文件可能不在，只读连接会打不开；这里只做查询
        con = sqlite3.connect(HERMES / "state.db", timeout=10)
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
    issues = [f"昨天有 {n} 次主对话调用走了回退模型 deepseek-flash（Codex 额度或登录可能有问题）"] if n else []
    if aux:
        issues.append(f"昨天辅助任务走了回退模型 deepseek-flash：{'、'.join(aux)}")
    return issues


# Hermes 源码里写死的日志原文（agent/context_compressor.py、agent/auxiliary_client.py），升级改了措辞这项就会失效
AUX_LOG_ERRORS = [
    ("Failed to generate context summary", "昨天压缩失败 {} 次（长会话会越积越长）"),
    ("no Codex OAuth token found", "昨天 {} 次取不到 Codex 凭证，辅助任务会回退或失败；先查凭证冷却，见 agent-kit G-13"),
]


def check_aux_errors(day=None):
    """昨天 agent.log 里辅助任务直接失败的记录：这类失败不进会话库，只能看日志。"""
    prefix = (day or yesterday()).isoformat()
    counts = [0] * len(AUX_LOG_ERRORS)
    try:
        with open(HERMES / "logs/agent.log", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith(prefix):
                    for i, (needle, _) in enumerate(AUX_LOG_ERRORS):
                        counts[i] += needle in line
    except Exception as e:
        return [f"读不了 Hermes 日志（{e}）"]
    return [msg.format(c) for (_, msg), c in zip(AUX_LOG_ERRORS, counts) if c]


def check_cron():
    r = run(["hermes", "cron", "incidents"])
    text = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or not text or "no " in text.lower()[:40] or "没有" in text[:20]:
        return []
    return [f"定时任务有未处理的失败记录：{text.splitlines()[0][:120]}"]


def send(msg, key, now):
    r = run(["lark-cli", "--profile", LARK_PROFILE, "im", "+messages-send", "--as", "bot",
             "--chat-id", DM_CHAT, "--text", msg, "--idempotency-key", key])
    with open(LOG_DIR / f"{now[:7]}.log", "a") as f:
        f.write(f"{now} {key} 发送 {'成功' if r.returncode == 0 else '失败：' + (r.stderr or r.stdout)[:200]}\n")


def main():
    dry = "--dry-run" in sys.argv
    issues = check_gateway() + check_fallback() + check_aux_errors() + check_cron()
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


if __name__ == "__main__":
    main()
