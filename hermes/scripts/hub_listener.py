"""发钥匙之前核对本机端口上在听的是谁（ADR-015 批 0E-2，独立审查 P1-2）。

中台、只读窗口、秘书窄窗口都是回环 TCP；中台一停（重启、崩溃后 KeepAlive 的 30 秒），端口就空着，主账号里的进程抢绑
127.0.0.1:3350 就能冒充中台，收下 dt-gate 发过去的本人密钥、来源钥匙、通道钥匙。dt-gate 里的客户端在带钥匙请求之前调
check(url)：端口上必须有监听、而且监听的进程全都属于 DT_HUB_LISTENER_UID 这个 uid，否则当作连不上（抛 OSError）。
非 root 的 lsof 看不到别的账号的进程：冒充者属主账号时，dt-gate 这边看到的是「没人在听」，同样不放行。

只在设了 DT_HUB_LISTENER_UID 时生效（dt_gate_hub.py 给 dt-gate 的服务设）；主账号和测试里不设，行为不变。
生效时地址只认 127.0.0.1：别的回环地址、localhost（可能先连 ::1）、外部地址一律不发钥匙。
检查和真正连接之间仍有极短的空隙；彻底堵住要靠门卫规则按监听账号放行（pf 的 user 条件），见 0E-2 交接。
"""
import os
import subprocess
import urllib.parse

LSOF = "/usr/sbin/lsof"


class NotOurListener(OSError):
    """端口上在听的不是该在听的账号。"""


def listener_uids(port):
    """本机 TCP 端口上处于 LISTEN 的进程的 uid 列表（看不到的进程不在里面）。"""
    r = subprocess.run([LSOF, "-nP", f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fu"], capture_output=True, text=True,
                       timeout=10)
    if r.returncode not in (0, 1):
        raise NotOurListener(f"lsof 出错：{r.stderr.strip()[:100]}")
    return [int(line[1:]) for line in r.stdout.splitlines() if line.startswith("u") and line[1:].isdigit()]


def check(url, want=None):
    want = os.environ.get("DT_HUB_LISTENER_UID") if want is None else want
    if want in (None, ""):
        return
    u = urllib.parse.urlsplit(url)
    if u.hostname != "127.0.0.1":           # 只认这一个地址：localhost 可能先连 ::1，那里有人单独抢绑、这边的 lsof 看不到（复核 P2-e）
        raise NotOurListener(f"设了监听核对时中台地址只能是 127.0.0.1（现在 {u.hostname}），不发钥匙")
    try:
        uids = listener_uids(u.port or 80)
    except (OSError, subprocess.SubprocessError) as e:
        raise NotOurListener(f"查不清 {u.port} 端口在听的是谁：{type(e).__name__}")
    if not uids or any(x != int(want) for x in uids):
        raise NotOurListener(f"{u.hostname}:{u.port} 上在听的不是该在听的账号（uid {want}），不发钥匙")
