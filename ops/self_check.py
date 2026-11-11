"""工程自检：纯脚本、不调用模型。有问题才打印一段中文，没问题什么都不打印。

用法：python3 self_check.py [--monthly]
每天 10:00 由个人项目日报发送（pdaily_publish.py send，#159）调用，输出并进日报卡片：空输出算正常，退出码非 0 卡片里写「自检没跑成」。
也可以手动跑看结果。
（launchd 起的 python 读不了 ~/Documents，所以要读仓库、名册和 Vault 的检查放在这里，不放 health_check.py。）
每天：仓库 main 上已提交的源文件和安装副本逐字节比对，只报缺失或不一致的路径，安装位置多出的文件不管。
每天：再在 github-center 目录下跑 node bin/health.js（Github·Tidings 健康检查），它的输出原样作为一段。
每天：跑同目录 ai_access.py status --json（AI 接入，P-13），车间默认执行方不能用、或配置读不了才说；没装 ai_access.py 就跳过（安装副本比对会报缺失）。
每月 1 号（或 --monthly）：另查沙盒超过 30 天没动的目录，和名册仓库自检（同目录 check_repos.py，只报失败项）。
只读：不改文件、不发飞书、不写日志。安装时和 check_repos.py 同放 ~/.hermes/scripts/。
环境变量：DT_REPO 仓库路径；HERMES_HOME、DT_BIN 两个安装根目录；DT_TODAY（YYYY-MM-DD）指定今天，自测用；
TIDINGS_REPO、TIDINGS_NODE 改 github-center 目录和 node 路径，自测用。
"""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

HOME = Path.home()
REPO = Path(os.environ.get("DT_REPO", "~/Documents/ChatGPT/个人/work/digital-twin")).expanduser()
HERMES = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
BIN = Path(os.environ.get("DT_BIN", "~/Library/Application Support/DigitalTwin/bin")).expanduser()
OPS_TARGETS = {"ops/health_check.py": [BIN], "ops/check_repos.py": [BIN, HERMES / "scripts"],
               "ops/self_check.py": [HERMES / "scripts"]}
SANDBOX_DIRS = [HOME / "Sandbox", HOME / "Documents/Codex"]   # M3-Q3：沙盒和 Codex 按日期建的旧目录
SANDBOX_STALE_DAYS = 30
CHECK_REPOS_SCRIPT = Path(__file__).with_name("check_repos.py")
CHECK_REPOS_TIMEOUT = 600   # 名册有多个项目，每个项目会执行多次 gh / git 查询
TIDINGS_REPO = Path(os.environ.get("TIDINGS_REPO", "~/Documents/ChatGPT/个人/work/github-center")).expanduser()
TIDINGS_NODE = os.environ.get("TIDINGS_NODE", str(HOME / ".nvm/versions/node/v24.16.0/bin/node"))   # PATH 里没有 nvm
TIDINGS_TIMEOUT = 120
AI_ACCESS_SCRIPT = Path(os.environ.get("DT_AI_ACCESS_SCRIPT", HERMES / "scripts/ai_access.py")).expanduser()
AI_ACCESS_TIMEOUT = 120
LIST_LIMIT = 10
PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + str(HOME / ".local/bin")


def run(cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "PATH": PATH})


def tilde(p):
    return str(p).replace(str(HOME), "~", 1)


def git(*args):
    """返回字节输出；git 起不来或超时按失败返回。"""
    try:
        return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, timeout=60,
                              env={**os.environ, "PATH": PATH})
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(["git"], 124, b"", str(e).encode())


def git_err(r):
    s = r.stderr.decode(errors="replace").strip()
    return s.splitlines()[-1][:200] if s else f"退出码 {r.returncode}"


def install_pairs(files):
    """main 上的仓库路径 → 安装路径。"""
    pairs = []
    for f in files:
        p = PurePosixPath(f)
        if p.parent == PurePosixPath("hermes/scripts") and p.suffix in (".py", ".json"):   # .json：脚本读的规则（如 merge_rules.json，#65）
            pairs.append((f, HERMES / "scripts" / p.name))
        elif f.startswith("hermes/scripts/assets/"):      # 脚本用的固定素材（如 Tidings 周报横幅，#55）
            pairs.append((f, HERMES / "scripts" / p.relative_to("hermes/scripts")))
        elif f.startswith("hermes/skills/"):
            pairs.append((f, HERMES / "skills/autonomous-ai-agents" / p.relative_to("hermes/skills")))
        elif f.startswith("hermes/prompts/"):
            pairs.append((f, HERMES / "prompts" / p.relative_to("hermes/prompts")))
        pairs += [(f, root / p.name) for root in OPS_TARGETS.get(f, [])]
    return pairs


def check_drift():
    """只看已提交的 main，不看工作区；返回问题行。"""
    r = git("ls-tree", "-r", "-z", "--name-only", "main", "--",
            "hermes/scripts", "hermes/skills", "hermes/prompts", *OPS_TARGETS)
    if r.returncode != 0:
        return [f"取不到仓库 main（{tilde(REPO)}：{git_err(r)}）"]
    issues = []
    for src, dst in install_pairs(r.stdout.decode().split("\0")):
        blob = git("show", f"main:{src}")
        if blob.returncode != 0:
            issues.append(f"取不到 main:{src}（{git_err(blob)}）")
            continue
        try:
            if dst.read_bytes() != blob.stdout:
                issues.append(f"{tilde(dst)} 和 {src} 内容不同")
        except FileNotFoundError:
            issues.append(f"{tilde(dst)} 缺失（源：{src}）")
        except OSError as e:
            issues.append(f"读不了 {tilde(dst)}（{e.strerror or e}）")
    return issues


def check_sandbox():
    """顶层目录按自身修改时间算；返回（超过 30 天没动的路径，扫不了的目录）。"""
    cutoff = dt.datetime.now().timestamp() - SANDBOX_STALE_DAYS * 86400
    stale, errors = [], []
    for d in SANDBOX_DIRS:
        try:
            if d.is_dir():
                stale += [p for p in sorted(d.iterdir())
                          if not p.name.startswith(".") and p.stat().st_mtime < cutoff]
        except OSError as e:
            errors.append(f"扫不了 {tilde(d)}（{e.strerror or e}）")
    return stale, errors


def check_repos():
    """读取只读自检的末行 JSON，只返回失败项；未拿到完整结果也要提醒。"""
    try:
        r = run([sys.executable, "-B", str(CHECK_REPOS_SCRIPT)], timeout=CHECK_REPOS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return [f"仓库自检未完成（超过 {CHECK_REPOS_TIMEOUT} 秒）"]
    except OSError as e:
        return [f"仓库自检未完成（无法启动：{e}）"]
    if r.returncode not in (0, 1):
        return [f"仓库自检未完成（退出码 {r.returncode}）"]
    try:
        result = json.loads(r.stdout.strip().splitlines()[-1])
        if (not isinstance(result, dict)
                or not isinstance(result.get("failed"), list)
                or not all(isinstance(item, str) for item in result["failed"])
                or result.get("ok") is not (not result["failed"])
                or r.returncode != int(bool(result["failed"]))):
            raise ValueError("结果字段或退出码不一致")
    except (ValueError, IndexError):
        return ["仓库自检未完成（末行 JSON 缺失、无效或与退出码不一致）"]
    return result["failed"]


def check_tidings():
    """health.js 有问题才打印；返回它的输出，或一句没跑完的原因。"""
    try:
        r = subprocess.run([TIDINGS_NODE, "bin/health.js"], cwd=TIDINGS_REPO, capture_output=True, text=True,
                           timeout=TIDINGS_TIMEOUT, env={**os.environ, "PATH": PATH})
    except subprocess.TimeoutExpired:
        return f"Tidings 自检未完成（超过 {TIDINGS_TIMEOUT} 秒）"
    except OSError as e:
        return f"Tidings 自检未完成（无法启动：{e.strerror or e}）"
    if r.returncode != 0:
        return f"Tidings 自检未完成（退出码 {r.returncode}）"
    return r.stdout.strip()


def check_ai_access():
    """车间默认执行方能不能用；能用返回空列表。"""
    if not AI_ACCESS_SCRIPT.exists():
        return []
    try:
        r = run([sys.executable, "-B", str(AI_ACCESS_SCRIPT), "status", "--json"], timeout=AI_ACCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        return [f"AI 接入自检未完成（超过 {AI_ACCESS_TIMEOUT} 秒）"]
    except OSError as e:
        return [f"AI 接入自检未完成（无法启动：{e.strerror or e}）"]
    if r.returncode != 0:
        return [f"AI 接入自检未完成：{(r.stderr or r.stdout).strip()[-200:] or f'退出码 {r.returncode}'}"]
    try:
        st = json.loads(r.stdout)
        w = st["workers"][st["default_worker"]]
    except (ValueError, KeyError, TypeError):
        return ["AI 接入自检未完成（输出不是预期的 JSON）"]
    if w.get("state") == "可用":
        return []
    return [f"车间默认执行方 {w.get('name')} 现在{w.get('state')}（{w.get('detail')}）。没点名的派单会报错停下，不会换成另一家",
            "处理：在终端登录它，或改默认：python3 ~/.hermes/scripts/ai_access.py set default 另一家"]


def bullets(items):
    lines = [f"- {i}" for i in items[:LIST_LIMIT]]
    return lines + ([f"- ……另有 {len(items) - LIST_LIMIT} 项"] if len(items) > LIST_LIMIT else [])


def today():
    return dt.date.fromisoformat(os.environ["DT_TODAY"]) if os.environ.get("DT_TODAY") else dt.date.today()


def main():
    sections = []
    drift = check_drift()
    if drift:
        sections.append([f"🔁 安装副本不一致：{len(drift)} 项和仓库 main 对不上", *bullets(drift),
                         "处理：从仓库 main 复制到安装位置；技能、提示词改了要重启网关。"])
    ai = check_ai_access()
    if ai:
        sections.append(["🤖 AI 接入提醒", *bullets(ai)])
    tidings = check_tidings()
    if tidings:
        sections.append([tidings])
    if today().day == 1 or "--monthly" in sys.argv:
        stale, errors = check_sandbox()
        if stale or errors:
            head = "🧹 沙盒提醒（每月 1 号）"
            if stale:
                head += f"：~/Sandbox 和 ~/Documents/Codex 里有 {len(stale)} 项超过 {SANDBOX_STALE_DAYS} 天没动"
            sections.append([head, *bullets([tilde(p) for p in stale]), *bullets(errors)]
                            + (["只是提醒，不会自动删；要清理就让 Claude 或 Codex 列清单给你定。"] if stale else []))
        failed = check_repos()
        if failed:
            sections.append(["📦 仓库自检提醒（每月 1 号）", *bullets(failed)])
    if sections:
        print("🔧 工程自检\n\n" + "\n\n".join("\n".join(s) for s in sections))


if __name__ == "__main__":
    main()
