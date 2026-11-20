"""发钥匙之前核对本机端口上在听的是谁（ADR-015 批 0E-2，独立审查 P1-2）。

中台、只读窗口、秘书窄窗口都是回环 TCP；中台一停（重启、崩溃后 KeepAlive 的 30 秒），端口就空着，主账号里的进程抢绑
127.0.0.1:3350 就能冒充中台，收下 dt-gate 发过去的本人密钥、来源钥匙、通道钥匙。dt-gate 里的客户端在带钥匙请求之前调
check(url)：127.0.0.1 这个端口上必须有 DT_HUB_LISTENER_UID 这个账号的进程在 LISTEN，否则当作连不上（抛 OSError）。
保证的边界（L3 第 2 轮 P2-02）：check 用 lsof -a -u <uid> 只看这个账号的进程，**不检查别的账号有没有也在听**；
能当作「独占」靠的是中台（agent-autoloop 的 Node HTTP Server，没开 reusePort）、只读窗口和窄窗口（Python HTTPServer）
都独占绑定 127.0.0.1:端口，别的账号先占了，dt-gate 的服务就起不来，查到的就是「没有」，不放行。
非 root 的 lsof 能不能看到别的账号的进程取决于它的编译方式（lsof -h 里的安全模式），这里不依赖这一点。
要看「端口上全部监听者」的是 root 侧（dt_gate_hub 的 Host.listeners，不带 uid 全量扫）。

只在设了 DT_HUB_LISTENER_UID 时生效（dt_gate_hub.py 给 dt-gate 的服务设）；主账号和测试里不设，行为不变。
生效时地址只认 127.0.0.1：别的回环地址、localhost（可能先连 ::1）、外部地址一律不发钥匙。
检查和真正连接之间仍有极短的空隙；彻底堵住要靠门卫规则按监听账号放行（pf 的 user 条件），见 0E-2 交接。
"""
import os
import subprocess
import urllib.parse

LSOF = "/usr/sbin/lsof"                 # 正式环境（macOS）固定用系统的；测试按平台注入（Linux 在 /usr/bin/lsof）


class NotOurListener(OSError):
    """端口上在听的不是该在听的账号。"""


def listener_uids(port, lsof=None, uid=None, timeout=10):
    """本机 TCP 端口上处于 LISTEN 的进程的 uid 列表（看不到的进程不在里面）。lsof 不在、跑不起来抛 OSError。
    给了 uid 就只扫这个账号的进程（-a -u，机器忙时快得多，全量扫描会超过 10 秒）：只回答「这个账号有没有在听」；
    root 要看全部账号（找冒充者）时不给 uid。"""
    only = ["-a", "-u", str(int(uid))] if uid is not None else []
    r = subprocess.run([lsof or LSOF, "-nP", *only, f"-iTCP:{int(port)}", "-sTCP:LISTEN", "-Fu"], capture_output=True,
                       text=True, timeout=timeout)
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
        uids = listener_uids(u.port or 80, uid=int(want), timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        raise NotOurListener(f"查不清 {u.port} 端口在听的是谁：{type(e).__name__}")
    if not uids or any(x != int(want) for x in uids):
        raise NotOurListener(f"{u.hostname}:{u.port} 上在听的不是该在听的账号（uid {want}），不发钥匙")
