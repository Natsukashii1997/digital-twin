#!/usr/bin/env python3
"""门卫机器人（ADR-015 修订 1 批 0E-3a，#195）：不带 AI 的授权组件，跑在控制面账号 dt-gate。

用飞书「门卫」应用的身份把中台 feishu 通道的卡片发到常浩私聊，自己经飞书长连接直接收卡片点击（不经 Hermes 网关、
不经秘书），核对点击人是绑定的常浩、聊天是绑定的私聊、点的是登记的那条卡片消息、令牌和版本都对，再用 feishu 通道钥匙
把回答交给中台。发卡、改卡、报送达、本人点击状态机、对账、卡片库全部复用 autoloop_feishu / autoloop_answer（换成
GateBot），这里只多三样：门卫应用凭据、身份绑定、收点击。

凭据（常浩 11-19 定：真凭据存成账号家目录里只有它能读的文件，不用钥匙串）：
  应用编号  DigitalTwin/gate/app.json（setup --app-id 写）
  应用密钥  ~/.dt/gate-app.secret（常浩亲手贴进 dt-gate，600、属 dt-gate、不能是链接；不经任何 AI）
  lark-cli 经环境变量 LARKSUITE_CLI_APP_ID / LARKSUITE_CLI_APP_SECRET 拿凭据（本机 1.0.96 实测认这两个变量、
  不碰钥匙串），配置目录单独放 DigitalTwin/gate/lark-cli/。lark-cli 本体必须是绝对路径、从 / 起每一级只有 root 或
  dt-gate 能改（DT_GATE_LARK_CLI，默认 /Library/Application Support/DigitalTwin/bin/lark-cli）：别人能换掉它就能偷密钥。
代码信任（L3 P1-04）：真正的保证在部署：门卫脚本装在 root 所有、只有 root 能改的目录（不许有符号链接，导入前递归核）；所有命令（常驻的 run 和
  手动的 setup、doctor、bind）都只经 root 装的入口 ops/dt_gatekeeper.sh 跑：清空环境、用 /usr/bin/python3 -I -S -B、
  脚本路径写死（主账号能改的 python3、标准库、PYTHON* 变量都碰不到 dt-gate 进程）。main 对每个子命令都要求 -I -S -B，
  run 另要求代码属 root（0E-3c 安装）。代码里的检查是加固：作为主程序启动时，导入任何同目录脚本之前先核解释器、本目录
  每个条目和 sys.path；读密钥前再核一次，连同已加载的全部模块（每个进程核过一次即可：能改这些属性的只有 root 和本账号）。
状态文件（P1-02）：门卫目录、gate.db、app.json 每次用前核路径信任（含 ACL）、不认链接；卡片库 cards.db 在 run、doctor、show 用前核。

收点击：lark-cli event consume card.action.trigger（飞书长连接，门卫主动连出去，本机不开端口；dt-gate 是控制面，
门卫规则不限制它）。本机事件总线 ready 且飞书长连接 connected（以总线自己的 bus.log 为准，L3 第 3 轮）才领中台卡片；
断开、重连、lark-cli 或总线报丢事件时暂停领卡（丢事件还私聊提醒常浩再点），领到卡、发出去之前再看一次（L3 P1-03、P2-02）。
点击在长连接断开期间点的，飞书会在卡片上提示失败，常浩再点一次即可。每条回调核对：
  - event_id 第一次见（gate.db 的 events 表，先记再办：同一条回调最多办一次）；host = im_message；只认按钮，不认表单；
  - 按钮值只有两种：{"autoloop": 令牌, "o": 选项号, "v": 版本号}（中台卡片）、{"gate_bind": 绑定码}（绑定卡），多一个字段都不认；
  - 中台卡片：回调必须带消息号，交给 autoloop_answer.answer 核点击人 = 绑定的 open_id、聊天 = 绑定的私聊、消息号 = 登记的
    卡片消息、令牌、选项、版本，之后照 0B-4a 的状态机走（排队、在途、结果不明、对账、本人优先）。
身份绑定（ADR-015 §3.2：门卫的 open_id 由 dt-gate 自己从常浩的一次点击取得并登记，不从猛鬼兽配置复制、不接受秘书声明）：
  分两步（L3 第 1 轮 P1-01：不能谁点了绑定卡就算常浩，要点卡的人和操作 dt-gate 终端的人是同一个人）：
  ① bind start --email <常浩的飞书邮箱> | --open-id <ou_…>：只有 dt-gate 账号里能发起；发一张绑定卡（按钮带一次性绑定码，
    10 分钟有效）。常浩点了，门卫只把这条回调的点击人和私聊号记成「待确认」，在点的那张卡上现给一个 6 位确认码（点击时才
    生成，只有点卡的人看得到）；已有别人点过这张卡就不认（常浩重新 bind start）；
  ② bind confirm <确认码>：常浩回到 dt-gate 终端输入，先显示待绑定的人（open_id 尾号、私聊号）、输入 yes 确认，确认码
    对上了才正式登记（点击后 10 分钟内，输错 3 次这次绑定作废）。
  邮箱 / open_id 只用来把卡送到，登记的值只取回调。已绑定时要 --replace 才能换绑（同样两步，换到别的私聊会告诉旧私聊）；
  绑定前不领中台卡片（领了发不出去，还会用掉中台的重试次数）、不认任何点击。

  gatekeeper.py setup --app-id cli_…       写应用编号
  gatekeeper.py doctor [--online]           自检：编号、密钥文件权限、lark-cli 路径、绑定；--online 再问飞书机器人信息
  gatekeeper.py bind start|confirm <确认码>|show
  gatekeeper.py run                         常驻：收点击 + 领中台卡片（装成 dt-gate 的系统服务是 D 档，常浩说「装」才装）
  gatekeeper.py show                        只读：绑定、卡片、答卷进度
环境变量：DT_GATE_HOME、DT_GATE_SECRET_FILE、DT_GATE_LARK_CLI；卡片库、中台、通道钥匙同 autoloop_feishu。
不调用 AI。猛鬼兽那条链路（merge-buttons 插件 → autoloop_answer）先保留；切换（停猛鬼兽桥接、中台 feishu 通道的
回答人换成门卫 open_id）是 0E-3c，另做。
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import select
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT_UID = 0                             # 门卫代码要属 root（测试换成本账号）


# ---------- 代码路径信任（导入同目录脚本之前就要用，只靠标准库）----------
class Untrusted(Exception):
    """路径别人能改。trusted() 把它换成 F.Fail。"""


def allow_acl(path):
    """macOS 的 ACL 能在权限位之外单独给别的账号放行（读、写、加文件）：有放行条目、或读不出 ACL 都返回 True（不认）。
    和 ops/dt_root_guard.sh 的 no_allow_acl 同一判法（拒绝类条目只会收紧，不管）。不是 macOS 返回 False。"""
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(["/bin/ls", "-led", str(path)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return True
    if r.returncode != 0:
        return True
    return any(" allow " in line for line in r.stdout.splitlines()[1:])


def check_path(path, what, checked=None):
    """从 / 起每一级（含 path 本身）只有 root 或本账号能改：属主是 root 或本账号，组和其他人不能写（带粘滞位的上级目录
    除外：别人删不掉、换不掉里面不属于他的条目），没有放行别人的 ACL。不过就抛 Untrusted。返回解析掉链接后的真实路径。
    checked：同一轮里已经核过的目录（集合），共同的上级只核一次。"""
    real = Path(path).resolve()
    for q in (real, *real.parents):
        if checked is not None and q in checked:
            break
        try:
            st = os.stat(q)
        except OSError:
            raise Untrusted(f"{what}：找不到 {q}")
        sticky = stat.S_ISDIR(st.st_mode) and st.st_mode & stat.S_ISVTX and q != real
        if st.st_uid not in (0, os.geteuid()) or (st.st_mode & 0o022 and not sticky) or allow_acl(q):
            raise Untrusted(f"{what}：路径上的 {q} 别人能改（属主、权限位或 ACL），不用（换掉它就能拿到门卫密钥）")
        if checked is not None and stat.S_ISDIR(st.st_mode) and not sticky:   # 靠粘滞位放过的不算核过（复核 P2-1）
            checked.add(q)
    return real


def search_path():
    return list(sys.path)


def loaded_files():
    return [m.__file__ for m in list(sys.modules.values()) if isinstance(getattr(m, "__file__", None), str)]


def nearest(p):
    """还不存在的路径看最近的已有上级：别人能在那里建目录、文件，就能塞进模块。"""
    p = Path(p).absolute()
    while not os.path.lexists(p) and p != p.parent:
        p = p.parent
    return p


def code_paths():
    """门卫会跑到的代码：解释器、本目录每个条目和 __pycache__ 里每个文件、sys.path 每一项、已加载的每个模块文件。"""
    yield sys.executable, "Python 解释器"
    yield HERE, "门卫脚本目录"
    for f in sorted(HERE.iterdir()):
        yield f, f"门卫脚本目录里的 {f.name}"
        if f.name == "__pycache__" and f.is_dir():
            for g in sorted(f.iterdir()):
                yield g, f"门卫的 __pycache__/{g.name}"
    for e in search_path():
        yield nearest(e or "."), f"模块搜索路径 {e or '.'}"
    for f in loaded_files():
        yield nearest(f), f"已加载的模块 {Path(f).name}"


def check_code():
    done = set()
    for p, what in code_paths():
        check_path(p, what, done)


def root_tree():
    """门卫代码目录（含全部子目录）里每个条目都属 root、没有符号链接（L3 第 3 轮 P1-04-A：root 的链接指向别人能改的文件，
    check_path 解析目标时会放过属本账号的目标，lstat 只看链接本身也会放过）。只靠标准库，导入同目录脚本之前就能跑。"""
    for top, dirs, files in os.walk(HERE, followlinks=False):
        for name in [None, *dirs, *files]:
            p = Path(top) / name if name else Path(top)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                raise Untrusted(f"门卫代码目录里不许有符号链接：{p}")
            if st.st_uid != ROOT_UID:
                raise Untrusted(f"门卫代码 {p} 不属 root：要装在只有 root 能改的目录（0E-3c 安装）")


if __name__ == "__main__":                       # 作为主程序：导入同目录脚本之前先核（L3 P1-04；被测试导入时不跑）
    try:
        check_code()
        root_tree()
    except Untrusted as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        sys.exit(1)

sys.path.insert(0, str(HERE))
import autoloop_answer as A  # noqa: E402
import autoloop_feishu as F  # noqa: E402
import tidings_weekly as TW  # noqa: E402

EVENT_KEY = "card.action.trigger"
APP_RE = re.compile(r"^cli_[0-9a-f]{16}$")
OPEN_ID_RE = re.compile(r"^ou_[0-9A-Za-z_-]{8,64}$")
CHAT_RE = re.compile(r"^oc_[0-9A-Za-z_-]{8,64}$")
MSG_RE = re.compile(r"^om_[0-9A-Za-z_-]{8,80}$")
NONCE_RE = re.compile(r"^[0-9a-f]{16}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BIND_TTL = 600                           # 秒：绑定卡有效期；点了以后确认码也是这么久
BIND_TRIES = 3                           # bind confirm 输错几次这次绑定作废
READY_MARK = f"[event] ready event_key={EVENT_KEY}"   # lark-cli event consume 连上本机事件总线后在 stderr 打的固定一行
LOSS_RE = re.compile(r"^WARN: (event seq gap|consume backpressure|event .* dropped|sink write failed|Process error|"
                     r"dropping oversized frame)")       # lark-cli 1.0.96 consume/loop.go 里确定丢了事件的几种诊断（stderr）
BUSLOG_LOSS_RE = re.compile(r"^(\[feishu\] drop |WARN: backpressure on conn )")   # 同版本 bus.log 里适配器、hub 丢事件
SDK_STATES = (("trying to reconnect", "reconnecting"), ("disconnected to ", "disconnected"), ("connected to ", "connected"))
#   lark-cli 1.0.96 websocket/sdk_log_patterns.go：SDK 日志按前缀判长连接状态（顺序同 feishu.go 的 tryNotify）
LOG_TIME_RE = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} ")   # bus.log 每行开头的时间（log.LstdFlags）
CONNECT_WAIT = 120                        # 秒：总线 ready 后，飞书长连接不在 connected（连接中、断了、认不出）超过这么久就重起 consume
NOTICE_RETRY_WINDOW = 50 * 60             # 秒：通知只在这么久内自动重发（飞书幂等键只保证一小时内去重），过了交人工核查
ENTRY_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
ENTRY_ENV = {"PATH", "LANG", "CPATH", "LIBRARY_PATH", "MANPATH", "SDKROOT", "__CF_USER_TEXT_ENCODING"}   # 入口 env -i 之后，
#   /usr/bin/python3（xcrun 转发壳）自己会补后五个（本机 11-20 实测），别的变量出现就说明没经入口
EVENT_KEEP = 30 * 86400                  # 秒：回调去重记录留这么久（飞书重投远短于这个）
DEFAULT_LARK = "/Library/Application Support/DigitalTwin/bin/lark-cli"
SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (key TEXT PRIMARY KEY, chat_id TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL,
  tries INTEGER NOT NULL DEFAULT 0, last_try_at TEXT, last_error TEXT, sent_at TEXT, gave_up_at TEXT);
CREATE TABLE IF NOT EXISTS binding (id INTEGER PRIMARY KEY CHECK (id = 1), open_id TEXT NOT NULL, chat_id TEXT NOT NULL,
  bound_at TEXT NOT NULL, event_id TEXT NOT NULL, message_id TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bind_pending (nonce TEXT PRIMARY KEY, message_id TEXT, sent_at TEXT NOT NULL,
  replace_ok INTEGER NOT NULL, cand_open_id TEXT, cand_chat_id TEXT, cand_event_id TEXT, code_hash TEXT, clicked_at TEXT,
  tries INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS binding_log (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, open_id TEXT, chat_id TEXT,
  event_id TEXT, note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, at TEXT NOT NULL, outcome TEXT);
"""


class Quiet(F.Fail):
    """来源不对（不是绑定的人、不是那张卡、不认的回调）：不回话，只记日志。"""


class BindNotReady(F.Fail):
    """绑定卡已经送到、发卡接口还没返回消息号时被点了：核不了点的是不是这张卡，不记候选，请点的人几秒后再点。"""


# ---------- 文件与配置 ----------
def gate_home():
    return Path(os.environ.get("DT_GATE_HOME") or F.C.G.SUPPORT / "gate").expanduser()


def secret_file():
    return Path(os.environ.get("DT_GATE_SECRET_FILE") or "~/.dt/gate-app.secret").expanduser()


def trusted(path, what, checked=None):
    """check_path，不过就抛 F.Fail。"""
    try:
        return check_path(path, what, checked)
    except Untrusted as e:
        raise F.Fail(str(e))


_DIRS_OK = set()


def private_dir(d):
    """门卫自己的状态目录：从 / 起每一级只有 root 或本账号能改（含 ACL，L3 第 1 轮 P1-02），本账号的改成 700。
    每个进程每个目录核一次（能改这些属性的只有 root 和本账号）。"""
    d = Path(d)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    real = d.resolve()
    if real not in _DIRS_OK:
        trusted(real, "门卫状态目录")
        _DIRS_OK.add(real)
    if os.stat(real).st_uid == os.geteuid():
        os.chmod(real, 0o700)
    return d


def state_file(f, what):
    """门卫的状态文件（app.json、gate.db、cards.db）：存在就必须是本账号的普通文件（不认链接）、不给别人任何权限、
    没有放行别人的 ACL（P1-02）。所在目录已核过只有 root 和本账号能改，核完到打开之间别人换不掉它。不存在返回 False。"""
    try:
        st = os.lstat(f)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077 or allow_acl(f):
        raise F.Fail(f"{what} {f} 必须是本账号的普通文件、不给别人任何权限、没有放行别人的 ACL（不认链接）")
    return True


def cards_trusted():
    """卡片库 cards.db（发卡登记的私聊、消息号、令牌都在里面，由 autoloop_feishu 管）：所在目录和文件同门卫状态。"""
    f = F.db_file()
    trusted(nearest(f.parent), "卡片库目录")     # 还没建就核最近的上级（之后由 F.db 在那里建）
    state_file(f, "卡片库")


def app_id():
    f = private_dir(gate_home()) / "app.json"
    try:
        if not state_file(f, "门卫应用编号文件"):
            raise OSError
        v = json.loads(f.read_text(encoding="utf-8")).get("app_id")
    except (OSError, ValueError, AttributeError):
        raise F.Fail("没有门卫的应用编号：先 gatekeeper.py setup --app-id cli_…")
    if not APP_RE.match(v or ""):
        raise F.Fail("app.json 里的应用编号格式不对")
    return v


def setup(app):
    if not APP_RE.match(app or ""):
        raise F.Fail("应用编号格式不对（应是 cli_ 加 16 位十六进制）")
    f = private_dir(gate_home()) / "app.json"
    tmp = f.with_name(f".app.json.{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"app_id": app}) + "\n")
    os.replace(tmp, f)
    return {"ok": True, "app_id": app, "file": str(f)}


def read_secret():
    """门卫应用密钥：属本账号、600、普通文件（不认链接）、一行。内容只交给 lark-cli 子进程的环境，不打印、不写日志。"""
    code_trusted()
    f = secret_file()
    try:
        fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise F.Fail(f"读不到门卫应用密钥 {f}（不存在、是链接或没权限）：请常浩亲手放进 dt-gate")
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o077 or allow_acl(f):
            raise F.Fail(f"门卫应用密钥 {f} 必须是本账号的普通文件、权限 600、没有放行别人的 ACL")
        try:
            v = fh.read().strip()
        except UnicodeDecodeError:
            raise F.Fail(f"门卫应用密钥 {f} 不是 UTF-8 文字")
    if not v or len(v) > 128 or any(c.isspace() for c in v):
        raise F.Fail(f"门卫应用密钥 {f} 内容格式不对")
    return v


_CODE_OK = []


def code_trusted():
    """读密钥前再核一次门卫会跑到的代码（P1-04 纵深防御；此时已加载的模块也都在里面，含标准库）。真正挡住「导入时就偷
    密钥」的是部署（见 launch_ok）。每个进程核过一次即可：能改这些属性的只有 root 和本账号。"""
    if _CODE_OK:
        return
    try:
        check_code()
    except Untrusted as e:
        raise F.Fail(str(e))
    _CODE_OK.append(True)


def root_code():
    """root_tree，不过就抛 F.Fail（run、doctor、main 用；导入前那次在文件开头）。"""
    try:
        root_tree()
    except Untrusted as e:
        raise F.Fail(str(e))


def launch_flags():
    """所有命令都要 -I -S -B（不读 PYTHON* 环境变量、不加载任何 site-packages、不写字节码）。本身挡不住已经跑起来的
    恶意解释器，作用是逼人走 root 装的入口 ops/dt_gatekeeper.sh（复核 P1-1）。"""
    f = sys.flags
    if not (f.isolated and f.no_site and f.dont_write_bytecode):
        raise F.Fail("门卫只经入口脚本跑（/Library/Application Support/DigitalTwin/bin/gatekeeper，即 /usr/bin/python3 "
                     "-I -S -B）；不要直接 python3 gatekeeper.py")


def launch_env():
    """防误用：PATH 正好是入口写的，除入口给的 PATH、LANG 和 xcrun 转发壳自己补的几个外没有别的变量。只核变量名，
    直接跑 Python 的人照样能凑出这套环境，所以它不是「走过入口」的证明（L3 第 3 轮 P1-04-B）；真正的边界是 root 装的
    入口、固定解释器、root 代码目录和 LaunchDaemon（0E-3c 实装验收）。"""
    extra = sorted(set(os.environ) - ENTRY_ENV)
    if os.environ.get("PATH") != ENTRY_PATH or extra:
        raise F.Fail(f"门卫只经入口脚本跑（环境没被清空：{', '.join(extra[:5]) or 'PATH 不对'}）")


def launch_ok():
    """门卫每个命令的启动要求（P1-04）：-I -S -B（launch_flags）、环境清空过（launch_env）、代码属 root（root_code）。
    这些都是加固，真正的边界是 root 装的入口和代码目录（0E-3c 安装后实测验收）。不满足抛 Fail。"""
    launch_flags()
    launch_env()
    root_code()


def lark_cli():
    """lark-cli 的路径：绝对路径，从 / 起每一级只有 root 或本账号能改（见 trusted）。"""
    p = Path(os.environ.get("DT_GATE_LARK_CLI") or DEFAULT_LARK)
    if not p.is_absolute():
        raise F.Fail("DT_GATE_LARK_CLI 必须是绝对路径")
    real = p.resolve()
    try:
        st = os.stat(real)
    except OSError:
        raise F.Fail(f"找不到 lark-cli：{real}")
    if not stat.S_ISREG(st.st_mode) or not os.access(real, os.X_OK):
        raise F.Fail(f"{real} 不是能执行的文件")
    return str(trusted(real, "lark-cli"))


# ---------- 门卫机器人 ----------
class GateBot(F.Bot):
    name = "门卫"
    unbound = "门卫还没绑定常浩（gatekeeper.py bind start），绑定前一律不认点击"
    no_chat = unbound

    def env(self):
        """lark-cli 子进程的环境：只给凭据和单独的配置目录，不继承别的，也不传代理变量（审查 P2-10：经主账号的
        加速器代理出去，代理若解 TLS 就能看到换令牌请求里的密钥；飞书直连即可，0E-1 的探针测过 dt-gate 直连）。"""
        home = private_dir(gate_home())
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(Path.home()), "LANG": "en_US.UTF-8",
               "LARKSUITE_CLI_APP_ID": app_id(), "LARKSUITE_CLI_APP_SECRET": read_secret(),
               "LARKSUITE_CLI_BRAND": "feishu", "LARKSUITE_CLI_CONFIG_DIR": str(private_dir(home / "lark-cli")),
               "LARKSUITE_CLI_LOG_DIR": str(private_dir(home / "lark-cli-log")),
               "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1", "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1"}
        return env

    def command(self, args):
        """(命令, 环境)。凭据或路径不对抛 SendError：调用方都按「发不出去」处理（不会误报成中台的事）。"""
        try:
            return [lark_cli(), *args], self.env()
        except F.Fail as e:
            raise F.SendError(str(e))

    def lark(self, args, what):
        cmd, env = self.command([*args, "--as", "bot", "--format", "json"])
        return TW.run_lark(cmd, what, env)

    def send_text(self, chat, text, key):
        self.lark(["im", "+messages-send", "--chat-id", chat, "--msg-type", "text",
                   "--content", json.dumps({"text": text}, ensure_ascii=False), "--idempotency-key", key], "发消息")

    def owner(self):
        b = binding()
        return (b["open_id"], b["chat_id"]) if b else (None, None)

    def owner_chat(self):
        return self.owner()[1]


GATE = GateBot()


# ---------- 门卫库 ----------
@contextlib.contextmanager
def db(write=False):
    f = private_dir(gate_home()) / "gate.db"
    if not state_file(f, "门卫库"):
        os.close(os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
    con = sqlite3.connect(f, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        if write:
            con.execute("BEGIN IMMEDIATE")
        yield con
        if write:
            con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()


def binding():
    with db() as con:
        r = con.execute("SELECT * FROM binding WHERE id = 1").fetchone()
    return dict(r) if r else None


def bind_card(nonce, done=None, final=True):
    """绑定卡：开着时一个按钮（只带绑定码）；点了以后去掉按钮写确认码（final=False，仍是橙色），绑好后写结果（绿色）。
    核对码是绑定码末 4 位，终端里同时打印，对上再点（只防点错卡，不当身份证明；身份靠确认码回到终端，P1-01）。"""
    text = ("在终端里刚发起门卫绑定，才点下面的按钮：点了以后卡上会出确认码，回到终端输入才算绑定，之后门卫只认你在这个"
            f"私聊里点的卡片。没发起过就别点。核对码：**{nonce[-4:]}**（和终端里显示的一致再点）")
    elements = [{"tag": "markdown", "content": text}]
    if done:
        elements.append({"tag": "markdown", "content": f"**{done}**"})
    else:
        elements.append({"tag": "button", "text": {"tag": "plain_text", "content": "绑定"}, "type": "primary",
                         "width": "fill", "behaviors": [{"type": "callback", "value": {"gate_bind": nonce}}]})
    return {"schema": "2.0", "config": {"update_multi": True, "enable_forward": False},
            "header": {"title": {"tag": "plain_text", "content": "门卫绑定"}, "template": "green" if done and final else "orange"},
            "body": {"elements": elements}}


def unanswered_cards(chat):
    """登记在这个私聊、还没答完的中台卡：中台里还开着的（cards / recentCards 里 state=open），或本人回答还在排队、在途、
    结果不明的。换绑前查（常浩 11-21 定甲，L3 第 2 轮 P2-04：有没答完的卡就不许换绑）。读不到中台就抛 Fail，不当成没有。"""
    import autoloop_readonly as R
    cards_trusted()
    with F.db() as con:
        ids = [r[0] for r in con.execute("SELECT card_id FROM feishu_cards WHERE chat_id = ?", (chat,))]
        pending = {r[0] for r in con.execute("SELECT card_id FROM claims WHERE who = 'owner' AND state IN "
                                             "('queued', 'inflight', 'unknown')")}
    if not ids:
        return []
    try:
        snap = R.read()
    except Exception as e:                       # 只读窗口的错误类型不止一种：一律算读不到
        raise F.Fail(f"读不到中台（{type(e).__name__}），核不了旧私聊还有没有没答完的卡，先不换绑")
    open_ids = {c.get("id") for c in snap.get("cards", []) + snap.get("recentCards", []) if c.get("state") == "open"}
    return sorted(cid for cid in ids if cid in open_ids or cid in pending)


def rebind_blocked(chat):
    left = unanswered_cards(chat)
    if left:
        raise F.Fail(f"旧私聊还有没答完的中台卡（卡片 {', '.join(map(str, left))}）：先在旧私聊答完或在中台撤掉，再换绑"
                     "（常浩 11-21 定：有没答完的卡就不许换绑）")


def bind_start(email=None, open_id=None, replace=False):
    """发绑定卡。邮箱 / open_id 只用来把卡送到；登记的 open_id、私聊号只取常浩点击的那条回调。"""
    if bool(email) == bool(open_id):
        raise F.Fail("--email 和 --open-id 二选一")
    if email and not EMAIL_RE.match(email):
        raise F.Fail("邮箱格式不对")
    if open_id and not OPEN_ID_RE.match(open_id):
        raise F.Fail("open_id 格式不对（门卫应用下的 ou_…）")
    old = binding()
    if old and not replace:
        raise F.Fail("门卫已经绑定过；要换绑加 --replace（换绑后旧的 open_id 不再认）")
    if old:
        rebind_blocked(old["chat_id"])
    nonce = secrets.token_hex(8)
    with db(write=True) as con:                  # 同一时间只有一张有效的绑定卡
        con.execute("DELETE FROM bind_pending")
        con.execute("INSERT INTO bind_pending (nonce, message_id, sent_at, replace_ok) VALUES (?, NULL, ?, ?)",
                    (nonce, F.now(), int(replace)))
    content = json.dumps(bind_card(nonce), ensure_ascii=False)
    try:
        if open_id:
            out = GATE.lark(["im", "+messages-send", "--user-id", open_id, "--msg-type", "interactive",
                             "--content", content, "--idempotency-key", f"gate-bind-{nonce}"], "发绑定卡")
        else:                                    # +messages-send 只收 chat_id / open_id；按邮箱走原始接口
            out = GATE.lark(["api", "POST", "/open-apis/im/v1/messages", "--params", json.dumps({"receive_id_type": "email"}),
                             "--data", json.dumps({"receive_id": email, "msg_type": "interactive", "content": content,
                                                   "uuid": f"gate-bind-{nonce}"}, ensure_ascii=False)], "发绑定卡")
    except F.SendError:
        with db(write=True) as con:
            con.execute("DELETE FROM bind_pending WHERE nonce = ?", (nonce,))
        raise
    mid = out.get("message_id")
    if not MSG_RE.match(mid or ""):
        with db(write=True) as con:
            con.execute("DELETE FROM bind_pending WHERE nonce = ?", (nonce,))
        raise F.Fail("发绑定卡没拿到消息号")
    with db(write=True) as con:
        con.execute("UPDATE bind_pending SET message_id = ? WHERE nonce = ?", (mid, nonce))
    return {"ok": True, "check_code": nonce[-4:], "expires_in": BIND_TTL,
            "next": f"在飞书门卫私聊里核对码 {nonce[-4:]} 一致再点「绑定」（{BIND_TTL // 60} 分钟内，门卫要在 run）；"
                    "卡上会出 6 位确认码，再在这里运行 gatekeeper.py bind confirm <确认码>"}


def code_hash(nonce, code):
    return hashlib.sha256(f"{nonce}:{code}".encode()).hexdigest()


def handle_bind(ev, nonce):
    """第一步：常浩点了绑定卡。绑定码有效、没过期、点的是这张绑定卡，才把回调里的点击人和私聊号记成待确认，在这张卡上
    现给 6 位确认码（L3 第 1 轮 P1-01）；正式登记要 bind confirm。同一个人再点给新码、旧码作废；别人点过就不认。"""
    operator, chat, mid = ev.get("operator_id"), ev.get("chat_id"), ev.get("message_id")
    if not (OPEN_ID_RE.match(operator or "") and CHAT_RE.match(chat or "") and MSG_RE.match(mid or "")):
        raise Quiet("绑定回调缺点击人、聊天号或消息号")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    with db(write=True) as con:
        p = con.execute("SELECT * FROM bind_pending WHERE nonce = ?", (nonce,)).fetchone()
        if not p:
            raise Quiet("没有这次绑定（绑定码不对、已用过或已被新的绑定取代）")
        if A._age(p["sent_at"]) > BIND_TTL:
            raise Quiet("绑定卡已过期")
        if p["message_id"] is None:              # 卡已到、发卡接口还没返回消息号（L3 第 2 轮 P2-03）：不记候选，请他稍后再点
            raise BindNotReady()
        if p["message_id"] != mid:
            raise Quiet("点的不是这次发出的绑定卡")
        if p["cand_open_id"] and (p["cand_open_id"], p["cand_chat_id"]) != (operator, chat):
            raise Quiet("这张绑定卡已经有别人点过")
        if con.execute("SELECT 1 FROM binding WHERE id = 1").fetchone() and not p["replace_ok"]:
            raise Quiet("已经绑定过，这次绑定没带 --replace")
        con.execute("UPDATE bind_pending SET cand_open_id = ?, cand_chat_id = ?, cand_event_id = ?, code_hash = ?, "
                    "clicked_at = ?, tries = 0 WHERE nonce = ?", (operator, chat, ev["event_id"], code_hash(nonce, code),
                                                                  F.now(), nonce))
    text = (f"确认码：**{code}**。回到 dt-gate 终端运行 gatekeeper.py bind confirm {code}（{BIND_TTL // 60} 分钟内）"
            "才算绑定完成。不是你在终端发起的绑定就别输入，也别把确认码告诉别人。")
    try:
        GATE.lark(["im", "messages", "patch", "--message-id", mid,
                   "--data", json.dumps({"content": json.dumps(bind_card(nonce, text, final=False), ensure_ascii=False)},
                                        ensure_ascii=False)], "改绑定卡")
    except F.SendError:
        GATE.send_text(chat, text.replace("**", ""), f"gate-code-{secrets.token_hex(8)}")
    return {"ok": True, "pending_confirm": operator[-6:]}


def bind_candidate():
    """等确认的那次点击：点击人、私聊号、点击时间（bind confirm 写入前给常浩看，复核 P2-5）。"""
    with db() as con:
        p = con.execute("SELECT cand_open_id, cand_chat_id, clicked_at FROM bind_pending WHERE cand_open_id IS NOT NULL").fetchone()
    if not p:
        raise F.Fail("没有等确认的绑定：先 bind start，在飞书门卫私聊点「绑定」")
    return dict(p)


def bind_confirm(code):
    """第二步：常浩在 dt-gate 终端输入卡上的确认码，对上了才正式登记（只有 dt-gate 账号能写门卫库，所以能跑这一步的
    就是能操作 dt-gate 终端的人）。输错 BIND_TRIES 次这次绑定作废；点击后 BIND_TTL 秒内有效。"""
    if not re.fullmatch(r"[0-9]{6}", code or ""):
        raise F.Fail("确认码是 6 位数字")
    with F.bridge_lock():                        # 和发卡同一把锁（L3 第 3 轮 P2-04）：正在发的卡先发完、登记完，这里才看得见；
        out = _bind_confirm(code)                # 还没开始发的要等换绑写完，发卡时读到的就是新绑定
    pending = flush_notices(force=True)          # 放锁后：换绑通知当场先发一次，发不出去留在库里由常驻循环补发
    return {**out, **({"notices_pending": pending} if pending else {})}


def _bind_confirm(code):
    b = binding()
    if b:                                        # 发起换绑之后旧私聊又收到了卡：写入前再查一次（确认码不消耗）
        rebind_blocked(b["chat_id"])
    err = None
    with db(write=True) as con:                  # 输错也要把次数记上，所以出错原因放到事务提交后再抛
        p = con.execute("SELECT * FROM bind_pending WHERE cand_open_id IS NOT NULL").fetchone()
        old = con.execute("SELECT open_id, chat_id FROM binding WHERE id = 1").fetchone()
        if not p:
            err = "没有等确认的绑定：先 bind start，在飞书门卫私聊点「绑定」"
        elif A._age(p["clicked_at"]) > BIND_TTL:
            con.execute("DELETE FROM bind_pending")
            err = "确认码已过期，这次绑定作废；重新 bind start"
        elif not hmac.compare_digest(p["code_hash"] or "", code_hash(p["nonce"], code)):
            tries = p["tries"] + 1
            if tries >= BIND_TRIES:
                con.execute("DELETE FROM bind_pending")
                err = f"确认码连错 {BIND_TRIES} 次，这次绑定作废；重新 bind start"
            else:
                con.execute("UPDATE bind_pending SET tries = ? WHERE nonce = ?", (tries, p["nonce"]))
                err = f"确认码不对（还能再试 {BIND_TRIES - tries} 次）"
        elif old and not p["replace_ok"]:
            con.execute("DELETE FROM bind_pending")
            err = "已经绑定过，这次绑定没带 --replace"
        else:
            operator, chat, nonce = p["cand_open_id"], p["cand_chat_id"], p["nonce"]
            con.execute("DELETE FROM bind_pending")
            con.execute("INSERT OR REPLACE INTO binding (id, open_id, chat_id, bound_at, event_id, message_id) "
                        "VALUES (1, ?, ?, ?, ?, ?)", (operator, chat, F.now(), p["cand_event_id"], p["message_id"]))
            con.execute("INSERT INTO binding_log (at, open_id, chat_id, event_id, note) VALUES (?, ?, ?, ?, ?)",
                        (F.now(), operator, chat, p["cand_event_id"], "换绑" if old else "首次绑定"))
            if old and old["chat_id"] != chat:   # 换绑到别的私聊：告诉旧的那个，和登记同一个事务记下、送到为止（P2-4、L3 第 2 轮 P2-06）
                con.execute("INSERT OR IGNORE INTO notices (key, chat_id, text, created_at) VALUES (?, ?, ?, ?)",
                            (f"gate-rebound-{nonce}", old["chat_id"],
                             f"门卫已改绑到另一个人（open_id 尾号 {operator[-6:]}），以后不再认你在这里点的卡片。"
                             "不是你在 dt-gate 里发起的换绑，请马上停门卫并核查。", F.now()))
    if err:
        raise F.Fail(err)
    done = f"已绑定（open_id 尾号 {operator[-6:]}）。以后门卫只认你在这个私聊里点的卡片。"
    notify = []
    try:
        GATE.lark(["im", "messages", "patch", "--message-id", p["message_id"],
                   "--data", json.dumps({"content": json.dumps(bind_card(nonce, done), ensure_ascii=False)},
                                        ensure_ascii=False)], "改绑定卡")
    except F.SendError as e:
        notify.append(str(e)[:120])
    return {"ok": True, "open_id": operator, "chat_id": chat, **({"notify_error": notify} if notify else {})}


def flush_notices(force=False, every=60):
    """补发还没送到的通知（目前只有换绑告诉旧私聊）：发成功才记 sent_at，发不出去记次数和原因、隔 every 秒再试（force 不等）。
    飞书幂等键只保证一小时内去重（lark-cli im +messages-send 说明），而「发不出去」里含结果不明（飞书收了、回应没回来），
    所以只在 NOTICE_RETRY_WINDOW（50 分钟）内自动重发：过了就停、记 gave_up_at、给现在绑定的私聊提醒一次请人工核查，
    show 里看得到（L3 第 3 轮 P2-06）。两个进程同时补发时，窗口内重复的由幂等键去重。返回还没送到、也还没放弃的条数。"""
    with db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM notices WHERE sent_at IS NULL AND gave_up_at IS NULL "
                                             "ORDER BY created_at")]
    left = 0
    for r in rows:
        if A._age(r["created_at"]) > NOTICE_RETRY_WINDOW:
            with db(write=True) as con:
                con.execute("UPDATE notices SET gave_up_at = ? WHERE key = ? AND gave_up_at IS NULL", (F.now(), r["key"]))
            F.alert(f"gate-notice-{r['key']}"[:60], f"门卫换绑后给旧私聊的通知 {r['created_at']} 起一直没确认送到（最后原因："
                    f"{(r['last_error'] or '未知')[:60]}），已停止自动重发（再发可能重复），请人工核查旧私聊有没有收到。", every=None)
            continue
        if not force and r["last_try_at"] and A._age(r["last_try_at"]) < every:
            left += 1
            continue
        try:
            GATE.send_text(r["chat_id"], r["text"], r["key"])
        except F.SendError as e:
            left += 1
            with db(write=True) as con:
                con.execute("UPDATE notices SET tries = tries + 1, last_try_at = ?, last_error = ? WHERE key = ?",
                            (F.now(), str(e)[:200], r["key"]))
            continue
        with db(write=True) as con:
            con.execute("UPDATE notices SET sent_at = ?, last_try_at = ? WHERE key = ?", (F.now(), F.now(), r["key"]))
    return left


# ---------- 收点击 ----------
def parse_value(raw):
    """回调里的按钮值（lark-cli 给的是 JSON 字符串）→ 字典；不认的返回 None。"""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    return raw if isinstance(raw, dict) else None


def seen(event_id):
    """记下这条回调；已经记过（飞书重投、重放）返回 True。顺手清掉 30 天前的记录。"""
    with db(write=True) as con:
        cutoff = (dt.datetime.now().astimezone() - dt.timedelta(seconds=EVENT_KEEP)).isoformat(timespec="seconds")
        con.execute("DELETE FROM events WHERE at < ?", (cutoff,))
        cur = con.execute("INSERT OR IGNORE INTO events (event_id, at) VALUES (?, ?)", (event_id, F.now()))
        return cur.rowcount != 1


def note_outcome(event_id, rec):
    with db(write=True) as con:
        con.execute("UPDATE events SET outcome = ? WHERE event_id = ?",
                    (json.dumps(rec, ensure_ascii=False)[:500], event_id))


def handle_event(ev):
    """处理一条 card.action.trigger 回调，返回记录（写日志用，不含令牌）。只有核对过的常浩点击才回话。"""
    if not isinstance(ev, dict) or ev.get("type") != EVENT_KEY:
        return {"ok": False, "quiet": True, "why": "不是卡片回调"}
    eid = ev.get("event_id")
    if not isinstance(eid, str) or not eid or len(eid) > 128:
        return {"ok": False, "quiet": True, "why": "回调没有 event_id"}
    if seen(eid):
        return {"ok": False, "quiet": True, "event": eid, "why": "这条回调已经处理过（重投或重放）"}
    try:
        rec = _handle(ev)
    except Exception as e:                       # 记过 event_id 了：核对过是常浩本人才回一句，让他再点（审查 P2-8）
        rec = {"ok": False, "why": f"{type(e).__name__}: {str(e)[:200]}"}
        b = binding()
        if b and ev.get("operator_id") == b["open_id"] and ev.get("chat_id") == b["chat_id"]:
            A.tell(b["chat_id"], "门卫这次没处理成你的点击（内部出错），请稍后在卡片上再点一次。")
    note_outcome(eid, rec)
    return {"event": eid, **rec}


def _handle(ev):
    if ev.get("host") != "im_message" or ev.get("action_tag") != "button" or ev.get("form_value"):
        return {"ok": False, "quiet": True, "why": "只认私聊消息卡片上的按钮"}
    value = parse_value(ev.get("action_value"))
    if value is None:
        return {"ok": False, "quiet": True, "why": "按钮值认不出"}
    if set(value) == {"gate_bind"}:
        if not NONCE_RE.match(str(value["gate_bind"])):
            return {"ok": False, "quiet": True, "why": "绑定码格式不对"}
        try:
            return handle_bind(ev, value["gate_bind"])
        except BindNotReady:                     # 只告诉点卡的这个私聊再点一次（不登记任何东西；绑定卡只发给了常浩）
            chat = ev.get("chat_id")
            if CHAT_RE.match(chat or ""):
                try:
                    GATE.send_text(chat, "门卫绑定卡还在登记，请几秒后在卡片上再点一次「绑定」。", f"gate-retry-{secrets.token_hex(8)}")
                except F.SendError:
                    pass
            return {"ok": False, "why": "绑定卡还在登记，请他稍后再点"}
        except F.SendError as e:                 # 已记成待确认，只是确认码没送到：同一个人再点一次会给新码
            return {"ok": False, "pending_confirm": True, "notify_error": str(e)[:120]}
        except F.Fail as e:
            return {"ok": False, "quiet": True, "why": str(e)}
    if set(value) != {"autoloop", "o", "v"} or type(value["o"]) is not int or type(value["v"]) is not int \
            or not F.TOKEN_RE.match(str(value["autoloop"])):
        return {"ok": False, "quiet": True, "why": "不是门卫认的按钮"}
    mid = ev.get("message_id")
    if not MSG_RE.match(mid or ""):              # 门卫的回调带消息号；没带就核不了点的是哪条，不认
        return {"ok": False, "quiet": True, "why": "回调没带消息号"}
    code, said, rec = A.answer(value["autoloop"], value["o"], ev.get("chat_id") or "", ev.get("operator_id") or "",
                               value["v"], message_id=mid)
    if said:
        A.tell(A.identities()[1], said)          # 只回绑定的私聊
    return {**rec, "said": bool(said)}


LISTENING = threading.Event()                    # 收点击这一端确实在线：这时发出去的卡点了才有人收
_LISTEN_LOCK = threading.Lock()


def loss_alert(line):
    """lark-cli 说确定丢了事件：可能漏了常浩的点击，私聊提醒他（限频）。"""
    F.alert("gate-event-loss", "门卫收点击时 lark-cli 报告丢了事件，可能漏了你刚才的点击；如果点过卡片没有反应，"
            f"请等一两分钟再点一次。（{line[:80]}）")


def bus_log_file():
    """门卫这个应用的 lark-cli 事件总线日志：<配置目录>/events/<应用编号>/bus.log（lark-cli 1.0.96 bus/log.go、bus.go）。"""
    return gate_home() / "lark-cli" / "events" / app_id() / "bus.log"


class BusLog:
    """读 lark-cli 事件总线自己写的 bus.log，定飞书长连接状态、发现上游丢事件（L3 第 3 轮 P1-03-A、P2-02、P2-07）。
    依据（lark-cli 1.0.96 源码）：SDK 每一行先写进 bus.log 再广播状态（websocket/feishu.go 的 sdkLogger），写文件不会像
    给 consume 的状态广播那样在队列满时静默丢掉（bus/hub.go 的 TrySend）；适配器丢事件（[feishu] drop …）和 hub 丢事件
    （WARN: backpressure on conn …）只写 bus.log；一个应用同时只有一个总线（bus.alive.lock），bus.log 只在总线启动时轮转。
    所以当前状态 = 最后一行「Bus started」之后最新的那条生命周期行，加入已有总线也拿得到（不靠总线补发）。
    丢事件只算开始看之后新写的行；文件换了（新总线启动时轮转）就从头读新文件，新文件里的都算新的。"""
    def __init__(self, path):
        self.path, self.fh, self.ino, self.pos, self.buf = path, None, None, 0, b""
        self.state, self.watched = None, False

    def close(self):
        if self.fh:
            self.fh.close()
        self.fh = None

    def poll(self):
        """读到最新，返回这次新看到的丢事件行。文件不在：状态当不在线。文件不可信抛 Fail。"""
        try:
            st = os.lstat(self.path)
        except FileNotFoundError:
            self.close()
            self.state, self.watched = None, True     # 之后出现的新文件里都算新内容
            return []
        follow = self.watched
        if self.fh is None or st.st_ino != self.ino or st.st_size < self.pos:
            self.close()
            state_file(self.path, "lark-cli 事件总线日志")
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            self.fh = os.fdopen(fd, "rb")
            self.ino, self.pos, self.buf, self.state = os.fstat(fd).st_ino, 0, b"", None
        self.watched = True
        chunk = self.fh.read()
        self.pos += len(chunk)
        self.buf += chunk
        losses = []
        while b"\n" in self.buf:
            raw, self.buf = self.buf.split(b"\n", 1)
            line = LOG_TIME_RE.sub("", raw.decode("utf-8", "replace").strip(), count=1)
            self.feed(line)
            if follow and BUSLOG_LOSS_RE.match(line):
                losses.append(line)
        return losses

    def feed(self, line):
        if line.startswith("Bus started for app="):
            self.state = None                    # 新总线：等来源报状态
        elif line.startswith("Starting source:"):
            self.state = "connecting"
        elif line.startswith("[SDK"):
            msg = line.split("] ", 1)[-1].lower()
            for prefix, state in SDK_STATES:
                if msg.startswith(prefix):
                    self.state = state
                    break
        elif (line.startswith("Source ") and " exited" in line) or line.startswith(("Bus shutting down", "Bus exited")):
            self.state = "down"


def consume_once(on_event=handle_event, log=print, on_loss=loss_alert):
    """起一次长连接消费进程，逐行处理，进程退出时返回退出码。stdin 一直开着（lark-cli 遇到 stdin 关闭就退出）。
    LISTENING 要同时满足才置：consume 进程活着、本机事件总线 ready（stderr 的 [event] ready）、bus.log 里当前总线的飞书长连接
    是 connected（BusLog，L3 第 3 轮 P1-03-A：不再靠 consume 收状态广播，广播会被静默丢掉、也不给新 consume 补当前状态）。
    以下任一出现就撤、结束这次 consume（run 隔一阵再起）：[event] exited、JSON 错误信封、进程退出、lark-cli 报确定丢了事件
    （stderr 的 LOSS_RE 或 bus.log 的 BUSLOG_LOSS_RE，同时私聊提醒常浩再点，P2-02）、bus.log 不可信、ready 后飞书长连接不在
    connected 超过 CONNECT_WAIT 秒（P2-07：连接中、断了、认不出都算）。bus.log 每轮（最多 1 秒）读一次。
    stdout 按 1 秒超时轮询读：进程退了即使有子进程还占着管道也不卡住（复核 P2-2）。"""
    LISTENING.clear()
    cmd, env = GATE.command(["event", "consume", EVENT_KEY, "--as", "bot"])
    bus = BusLog(bus_log_file())
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    st = {"alive": True, "bus": False, "source": None, "down_since": None, "stop": None}

    def update(**kw):
        with _LISTEN_LOCK:
            st.update(kw)
            if st["alive"] and st["bus"] and st["source"] == "connected" and not st["stop"]:
                LISTENING.set()
            else:
                LISTENING.clear()

    def lost(line):
        update(stop="lark-cli 报告丢了事件")
        try:
            on_loss(line)
        except Exception as e:                   # 提醒发不出去也照样停下这次 consume
            log(json.dumps({"at": F.now(), "ok": False, "why": f"丢事件提醒没发出去：{type(e).__name__}"}, ensure_ascii=False))

    def errors():                                # lark-cli 的就绪标记、丢事件、错误信封都在 stderr
        with proc.stderr:                        # 读完由本线程关（子进程占着 stderr 时主线程去关会卡住）
            for raw in proc.stderr:
                errors_line(raw)

    def errors_line(raw):
        line = raw.decode("utf-8", "replace").strip()
        if line == READY_MARK:
            update(bus=True)
        elif LOSS_RE.match(line):
            lost(line)
        elif line.startswith("[event] exited") or line.startswith("{"):
            update(stop="lark-cli 退出或报错")
        log(json.dumps({"at": F.now(), "lark_cli": line[:300]}, ensure_ascii=False))
    t = threading.Thread(target=errors, daemon=True)
    t.start()

    def watch_bus():
        try:
            losses = bus.poll()
        except (F.Fail, OSError) as e:
            update(source=None, stop=f"读不了 lark-cli 事件总线日志：{str(e)[:120]}")
            return
        for line in losses:
            log(json.dumps({"at": F.now(), "bus_log": line[:300]}, ensure_ascii=False))
            lost(line)
        now = time.monotonic()
        with _LISTEN_LOCK:
            down = st["bus"] and bus.state != "connected"
            down_since = (st["down_since"] or now) if down else None
        update(source=bus.state, down_since=down_since)
        if down_since and now - down_since > CONNECT_WAIT:
            update(stop=f"飞书长连接 {CONNECT_WAIT} 秒没连上（现在：{bus.state or '未知'}）")

    def handle(line):
        if not line.strip():
            return
        try:
            ev = json.loads(line)
        except ValueError:
            log(json.dumps({"at": F.now(), "ok": False, "why": "回调行不是 JSON"}, ensure_ascii=False))
            return
        try:
            rec = on_event(ev)
        except Exception as e:                   # 一条出错不断开长连接
            rec = {"ok": False, "why": f"{type(e).__name__}: {str(e)[:200]}"}
        log(json.dumps({"at": F.now(), **rec}, ensure_ascii=False))
    fd, buf, gone = proc.stdout.fileno(), b"", None
    try:
        while True:
            if proc.poll() is not None:
                update(alive=False)              # 进程一退就撤，不等管道读完
                gone = gone or time.monotonic()
                if time.monotonic() - gone > 5:  # 子进程占着管道还在写：不再等
                    break
            watch_bus()
            with _LISTEN_LOCK:
                stop = st["stop"]
            if stop:
                log(json.dumps({"at": F.now(), "ok": False, "why": f"{stop}，结束这次长连接、稍后重起"}, ensure_ascii=False))
                break
            if not select.select([fd], [], [], 1.0)[0]:
                if proc.poll() is not None:
                    break
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            watch_bus()                          # 等 stdout 期间 bus.log 可能变了：处理前先更新一次在线状态
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                handle(line.decode("utf-8", "replace"))
        handle(buf.decode("utf-8", "replace"))
    finally:
        update(alive=False)
        bus.close()
        if proc.poll() is None:
            proc.terminate()                     # SIGTERM：让 lark-cli 正常退订（不 kill -9）
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        t.join(timeout=5)
        for fh in (proc.stdin, proc.stdout):
            fh.close()
    return proc.returncode


def not_ready():
    """领中台卡片前问：没绑定、或收点击的长连接没就绪（断了在重连）都先不领（P1-03）。"""
    if not binding():
        return GATE.unbound
    if not LISTENING.is_set():
        return "门卫收点击的长连接还没就绪（或断了在重连），先不领中台卡片：发出去点了没人收"
    return None


_NOTICE_AT = [0.0]


def cards_ready():
    """领卡线程每轮先问：顺手补发没送到的通知（最多 60 秒一次，出错只记日志），再看能不能领卡。"""
    if time.monotonic() - _NOTICE_AT[0] >= 60:
        _NOTICE_AT[0] = time.monotonic()
        try:
            left = flush_notices()
            if left:
                print(json.dumps({"at": F.now(), "ok": False, "notices_pending": left}, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"at": F.now(), "ok": False, "why": f"补发通知出错：{type(e).__name__}"}, ensure_ascii=False),
                  flush=True)
    return not_ready()


def run():
    """常驻：后台线程领中台卡片（没绑定、收点击没在线时暂停），前台收点击；长连接断了退避重连（10 秒起，最多 5 分钟；
    在线与否看 bus.log，不用等旧总线退出）。
    领卡线程死了就整个退出（退出码 3），交给系统服务重启，不留一个只收点击、不发卡的半死进程（审查 P2-3）。
    启动前核启动方式、代码属 root、卡片库（P1-04、P1-02），不过就不起。"""
    launch_ok()
    cards_trusted()
    F.use(GATE)
    cards = threading.Thread(target=F.run, kwargs={"ready": cards_ready}, daemon=True, name="gate-cards")
    cards.start()
    wait = 10
    while True:
        if not cards.is_alive():
            print(json.dumps({"at": F.now(), "ok": False, "why": "领卡线程停了，退出等重启"}, ensure_ascii=False), flush=True)
            sys.exit(3)
        started = time.monotonic()
        try:
            code = consume_once()
            why = f"长连接进程退出（退出码 {code}）"
        except (F.Fail, F.SendError, OSError) as e:
            why = str(e)
        print(json.dumps({"at": F.now(), "ok": False, "why": why, "retry_in": wait}, ensure_ascii=False), flush=True)
        time.sleep(wait)
        wait = 10 if time.monotonic() - started > 600 else min(wait * 2, 300)


def doctor(online=False):
    out = {}
    checks = (("app_id", app_id), ("secret", lambda: read_secret() and "ok"), ("lark_cli", lark_cli),
              ("code_owner", lambda: root_code() or "root"), ("cards_db", lambda: cards_trusted() or "ok"))
    for name, fn in checks:
        try:
            out[name] = fn()
        except F.Fail as e:
            out[name] = {"error": str(e)}
    b = binding()
    out["binding"] = {"open_id_tail": b["open_id"][-6:], "chat_id": b["chat_id"], "bound_at": b["bound_at"]} if b else None
    if online:
        try:
            info = GATE.lark(["api", "GET", "/open-apis/bot/v3/info"], "查机器人")
            bot = info.get("bot") or info
            out["bot"] = {"app_name": bot.get("app_name"), "activate_status": bot.get("activate_status")}
            if not bot.get("app_name"):          # 返回结构没实测（审查 P2-5）：读不到名字就判不过，不当成功
                out["bot"]["error"] = "没读到机器人名字（返回结构和预期不同，或凭据不对）"
        except F.SendError as e:
            out["bot"] = {"error": str(e)}
    out["ok"] = all(not isinstance(out.get(k), dict) for k, _ in checks) and b is not None \
        and not (online and "error" in out["bot"])
    return out


def show():
    cards_trusted()                              # 读卡片库前同样核（L3 第 2 轮 P2-01）
    with db() as con:
        log = [dict(r) for r in con.execute("SELECT at, note, open_id, chat_id FROM binding_log ORDER BY id DESC LIMIT 10")]
        events = [dict(r) for r in con.execute("SELECT event_id, at, outcome FROM events ORDER BY at DESC LIMIT 20")]
        notices = [dict(r) for r in con.execute("SELECT key, chat_id, created_at, tries, last_error, gave_up_at FROM notices "
                                                "WHERE sent_at IS NULL")]
    b = binding()
    return {"binding": b and {k: b[k] for k in ("open_id", "chat_id", "bound_at")}, "binding_log": log,
            "recent_events": events, "notices_pending": notices, **F.show()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").add_argument("--app-id", required=True)
    sub.add_parser("doctor").add_argument("--online", action="store_true")
    b = sub.add_parser("bind")
    b.add_argument("action", choices=("start", "confirm", "show"))
    b.add_argument("code", nargs="?")
    b.add_argument("--email")
    b.add_argument("--open-id")
    b.add_argument("--replace", action="store_true")
    for name in ("run", "show"):
        sub.add_parser(name)
    a = ap.parse_args(argv)
    F.use(GATE)
    try:
        launch_ok()                              # 每个子命令都要经入口脚本、代码属 root（复核 P1-1、L3 第 2 轮 P1-04）
        if a.cmd == "run":
            run()
        elif a.cmd == "setup":
            out = setup(a.app_id)
        elif a.cmd == "doctor":
            out = doctor(a.online)
        elif a.cmd == "bind" and a.action == "start":
            out = bind_start(a.email, a.open_id, a.replace)
        elif a.cmd == "bind" and a.action == "confirm":
            c = bind_candidate()
            print(f"待绑定：open_id 尾号 {c['cand_open_id'][-6:]}，私聊 {c['cand_chat_id']}，{c['clicked_at']} 点的卡。")
            try:
                yes = input("确认是你本人刚在飞书门卫私聊点的？输入 yes 继续：").strip() == "yes"
            except EOFError:
                yes = False
            out = bind_confirm(a.code) if yes else {"ok": False, "why": "没确认，这次没绑定（确认码没用掉，可以再来）"}
        elif a.cmd == "bind":
            bd = binding()
            out = {"binding": bd and {k: bd[k] for k in ("open_id", "chat_id", "bound_at")}}
        else:
            out = show()
    except (F.Fail, F.SendError) as e:
        print(json.dumps({"ok": False, "why": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=1 if a.cmd in ("show", "doctor") else None))
    return 0 if not isinstance(out, dict) or out.get("ok", True) is not False else 1


if __name__ == "__main__":
    sys.exit(main())
