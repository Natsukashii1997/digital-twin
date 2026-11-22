"""生产守卫（批 0D）：测试模块整体隔离，前后核对本机正式中台和正式状态目录没被动过。

来由：2026-11-17 漏隔离的测试把配置装进了正式中台；2026-11-18 写 0D 时，旧测试没指临时目录、新代码默认走正式路径，
在正式 DigitalTwin/autoloop/ 里建了 exits.db（已恢复，记在 CURRENT）。所以不再靠每个测试自己记得隔离：

1. setUpModule 一开始就把 digital-twin 中台相关的**所有**路径和地址变量指到这个模块自己的临时目录
   （出口账本、锁定版本、凭证库、镜像、任务单、卡片库、通道钥匙、来源钥匙、只读令牌、配置锁、证据、中台目录），
   中台地址指到一个不监听的端口；测试里再 patch 的照样生效，忘了 patch 的也碰不到正式环境。
2. 前后核对：~/.agent-autoloop 的 rules.json、config.json、master.key、secrets.env（内容哈希）；正式 DigitalTwin/autoloop/
   目录里有哪些文件、大小和修改时间；工人共享目录 /Users/Shared/dt-autoloop 下 grants、tasks。变了就让这个模块报错。
家目录按系统账号取，不信环境变量 HOME。只读，不调中台、不跑命令行。

用法（测试模块里）：
    from prod_guard import setUpModule, tearDownModule  # noqa: F401
已有自己 setUpModule 的模块，在里面先调 prod_guard.setUpModule()，tearDownModule 最后调 prod_guard.tearDownModule()。
"""
import hashlib
import os
import pwd
import tempfile
from pathlib import Path

HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
HUB_FILES = [HOME / ".agent-autoloop" / n for n in ("rules.json", "config.json", "master.key", "secrets.env")]
DIRS = [HOME / "Library/Application Support/DigitalTwin/autoloop", Path("/Users/Shared/dt-autoloop/grants"),
        Path("/Users/Shared/dt-autoloop/tasks")]
_state = []


def sandbox_env(root):
    a = root / "dt/autoloop"
    return {"DT_EXITS_DB": str(a / "exits.db"), "DT_HUB_PIN": str(a / "hub-pin.json"), "DT_GRANTS_DB": str(a / "grants.db"),
            "DT_GRANTS_STATUS": str(root / "shared/grants/status.json"), "DT_GRANTS_TASKS": str(root / "shared/tasks"),
            "DT_AUTOLOOP_CARDS_DB": str(a / "cards.db"), "DT_AUTOLOOP_CHANNEL_KEY_DIR": str(a),
            "DT_HERMES_SOURCE_KEY_FILE": str(a / "hermes-source.key"), "DT_READONLY_TOKEN_FILE": str(a / "readonly.token"),
            "DT_AUTOLOOP_LOCK": str(a / "config.lock"), "DT_AUTOLOOP_EVIDENCE": str(root / "evidence"),
            "AGENT_AUTOLOOP_HOME": str(root / "no-hub"), "DT_HUB": "http://127.0.0.1:9"}


def snapshot():
    out = {}
    for f in HUB_FILES:
        try:
            out[str(f)] = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            out[str(f)] = None
    for d in DIRS:
        try:
            out[str(d)] = sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in d.iterdir())
        except OSError:
            out[str(d)] = None
    return out


def setUpModule():
    tmp = tempfile.TemporaryDirectory(prefix="dt-prod-guard-")
    env = sandbox_env(Path(tmp.name))
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    _state.append((snapshot(), tmp, saved))


def tearDownModule():
    before, tmp, saved = _state.pop()
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    tmp.cleanup()
    after = snapshot()
    changed = [k for k in after if after[k] != before.get(k)]
    if changed:
        raise AssertionError(f"测试动到了正式环境：{changed}")
