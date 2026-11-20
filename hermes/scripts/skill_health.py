"""skill 月度体检推送（#151）：纯脚本、不调用模型。

用法：python3 skill_health.py [--days 30]
由 Hermes 无模型定时任务每月 1 号 10:30 调用：stdout 原样发到常浩飞书私聊；退出码非 0 算任务失败，由 Hermes 发失败通知。
做的事：在 my-skills 目录下跑 node scripts/monthly-health.mjs --days N（统计每个自建 skill 的调用、最近使用、版本、
evals / CHANGELOG，退役与合并只出提案），再把它的 Markdown 改成飞书消息能显示的样子：标题改粗体、表格每行改成一条列表，
其余原样；末尾加一句怎么抽查判得对不对。
只读：不改文件、不写日志；发飞书由 Hermes 负责。安装在 ~/.hermes/scripts/。
环境变量：MY_SKILLS_REPO 改 my-skills 目录，SKILL_HEALTH_NODE 改 node 路径，自测用。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import netwait  # noqa: E402  唤醒后先等网络（P9）

HOME = Path.home()
REPO = Path(os.environ.get("MY_SKILLS_REPO", "~/Documents/ChatGPT/个人/work/my-skills")).expanduser()
NODE = os.environ.get("SKILL_HEALTH_NODE", str(HOME / ".nvm/versions/node/v24.16.0/bin/node"))   # PATH 里没有 nvm
SCRIPT = "scripts/monthly-health.mjs"
TIMEOUT = 300   # 要扫 Codex 全部会话记录
FOOTER = "要看某个 skill 判得对不对，在 Claude 里回复「抽查 <名字>」。"
HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator(line):
    return bool(re.fullmatch(r"\|?[\s:|-]+\|?", line.strip())) and "-" in line


def to_feishu(md):
    """标题 → 粗体；表格 → 每行一条「- **第一列**：列名 值，……」；其余行原样。"""
    out, header = [], None
    for line in md.splitlines():
        if line.lstrip().startswith("|"):
            if header is None:
                header = cells(line)
            elif not is_separator(line):
                row = cells(line)
                rest = "，".join(f"{h} {v}" for h, v in zip(header[1:], row[1:]))
                out.append(f"- **{row[0]}**：{rest}")
            continue
        header = None
        m = HEADING.match(line)
        out.append(f"**{m.group(1).strip()}**" if m else line)
    return "\n".join(out).strip()


def days_arg(argv):
    if "--days" in argv:
        i = argv.index("--days")
        if i + 1 >= len(argv) or not argv[i + 1].isdigit() or int(argv[i + 1]) <= 0:
            raise SystemExit("--days 后面要跟正整数")
        return argv[i + 1]
    return "30"


def main(argv=None):
    days = days_arg(sys.argv[1:] if argv is None else argv)
    netwait.guard("skill-health", ("feishu",), "skill 月度体检")   # 结果要经飞书发出，先等网络（P9）
    try:
        r = subprocess.run([NODE, SCRIPT, "--days", days], cwd=REPO, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"skill 月度体检没跑完：{TIMEOUT} 秒超时（{SCRIPT}）", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"skill 月度体检没跑起来：{e}", file=sys.stderr)
        return 1
    if r.returncode != 0 or not r.stdout.strip():
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [f"退出码 {r.returncode}，没有输出"]
        print(f"skill 月度体检失败：{tail[0][:200]}", file=sys.stderr)
        return 1
    print(to_feishu(r.stdout) + "\n\n" + FOOTER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
