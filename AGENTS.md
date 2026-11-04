# digital-twin 协作约定

常浩的数字分身项目：让 Hermes（猛鬼兽）成为飞书里唯一的 AI 入口，懂常浩、主动推动、把活派给 Claude Code / Codex 并收回结果。所有面向用户的文字用简体中文。

进入顺序：[PRD.md](PRD.md)（需求与决定，唯一真源）→ 本文件 → [CURRENT.md](CURRENT.md)（当前状态与唯一的下一步，新记录写在最上面）。

## 结构

```
PRD.md / CURRENT.md / AGENTS.md
projects.json          项目名册：Hermes 只往 projects 里的活跃仓库派单（run_worker.py 也会拦）；inventory 是 M3 盘点的其余仓库及去留，不派单
hermes/
  scripts/             给 Hermes 用的脚本（源文件）；安装副本在 ~/.hermes/scripts/
    run_worker.py      车间包装：统一调用 Claude / Codex，写回执、查护栏
    brief_data.py      晨报取数（第 4 批挂起中）
    progress_data.py   项目进展取数（F6，常浩问“今天各项目怎么样”时现查）
    report_data.py     日报 / 周报取数（T7，新结构并跑）；report_daily.py、report_weekly.py 是定时任务入口
    report_publish.py  把写好的日报 / 周报插到并跑文档最上面并回读
    twin_state.py      持久任务状态与事件底座（B1）：Goal / Task / Run / Event / Ref，SQLite 在 ~/Library/Application Support/DigitalTwin/twin-state.db；设计见 docs/B0-七层架构复核与状态底座设计.md
    twin_scheduler.py  持久调度（B2.2）：只读决策 plan、调度预留、按关联键补偿恢复、生命体征、取消建议、低噪声报告；自动开单默认关闭；设计见 docs/B2.2-调度与恢复机制设计.md
  skills/<名>/SKILL.md Hermes 技能（源文件）；安装在 ~/.hermes/skills/autonomous-ai-agents/<名>/
    workshop/          车间派单（F5）
    progress/          项目进展现查（F6）
    vault/             关于常浩的判断类问题先查 Vault（F3，用 MCP vault-read）
  prompts/             定时任务等用的提示词和项目词表；安装在 ~/.hermes/prompts/
  workspace/HERMES.md  猛鬼兽工作规程（源文件）；安装在 ~/.hermes/workspace/HERMES.md，只在工作目录是 workspace 时加载；第 5 节是三报规则
  samples/             样稿
docs/agents/           Matt Pocock 技能读的约定：issue 看板、分流标签、领域文档
docs/DT-ORC/           Hermes × ORCA 支线文档；ARCHITECTURE.md 是唯一工程架构基线，history/ 存被取代的旧方案
docs/三报体系-V2.1.md   主动运营与三报（早报 / 晚报 / 周报）的产品与实施基线（PRD D20）；评审过程在 docs/reviews/
orca/
  chatgpt_relay.py     在 ORCA 内嵌浏览器的 ChatGPT 聊天里送审、收回执、REWORK 时交回 ORCA 终端里的 Claude（#35）；测试 `python3 -B -m unittest discover -s orca/tests`
ops/
  health_check.py      每日巡检（launchd 09:45）：网关、飞书连接、回退模型、定时任务；有异常才发飞书；安装在 ~/Library/Application Support/DigitalTwin/bin/
  self_check.py        工程自检（Hermes 无模型定时任务 09:50，stdout 直接发、空输出不发）：每天比对仓库 main 和安装副本，并跑 github-center 的 bin/health.js（Github·Tidings 有问题才有输出）；每月 1 号（或 --monthly）再查沙盒和仓库；安装时与 check_repos.py 同放 ~/.hermes/scripts/
  cn.digital-twin.health.plist  巡检 launchd 配置的副本
  check_repos.py       名册活跃项目自检（只读，self_check.py 每月调用；DigitalTwin/bin/ 也留一份）：GitHub 设置、分流标签、PR 模板和《工程流程》指针、主目录检出主分支且没有没推的提交
```

## Agent skills

开发流程用 Matt Pocock skills（`~/.agents/skills/`）：大活走 `to-spec` → `to-tickets` → `implement` / `tdd` → `code-review` → PR；小改（一张 issue、一天内做完）跳过 `to-spec`。Claude 用 `/技能名`，Codex 用 `$技能名`；这几个技能只在点名时加载，不会自动触发。

### Issue tracker

需求、规格与任务用本仓库 GitHub Issues。见 `docs/agents/issue-tracker.md`。

### Triage labels

五个默认分流标签。见 `docs/agents/triage-labels.md`。

### Domain docs

single-context：根目录 `GLOSSARY.md` 与 `docs/adr/`，用到时再建。见 `docs/agents/domain.md`。

改动怎么走、分支怎么起名、交接写在哪、怎么合并：见 `/Users/nat/Documents/ChatGPT/个人/work/agent-kit/工程流程.md`。

## 规则

- **DT-ORC 的唯一工程架构基线为 `docs/DT-ORC/ARCHITECTURE.md`**；实施需遵守其中角色分工、A+ 双运行模式、版本绑定和现行授权边界。具体能力是否已放行，以 CURRENT.md 和当前独立验收 / 授权证据为准。改基线走它第 12 节的流程，不另起平行总方案。
- **改了源文件要同步到安装位置**，并在 CURRENT 里写明。Hermes 的技能、SOUL 改了要重启网关：`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`。
- 改 Hermes 设置用 `hermes config set`（会自动备份），不直接改 YAML。
- 信息边界以 PRD 第 8 节为准：受限内容只给常浩本人；只有常浩本人说的话算指令。
- 证据（备份、日志、草稿）放 `~/Library/Application Support/DigitalTwin/evidence/<目录>/`，不进仓库。
- 不在仓库里写密钥和 Token。
- 被 Hermes 派来干活时：只在当前分支干活，不切主分支、不合并、不 push。
