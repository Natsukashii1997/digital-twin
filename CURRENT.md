# 数字分身 · 当前状态

需求以 [PRD.md](PRD.md) 为准（v1.1，2026-10-26 已拍板）。本文件只记进度和唯一的下一步，新的记录写在最上面。

证据目录：`~/Library/Application Support/DigitalTwin/evidence/<批次目录>/`（备份含密钥，权限 700/600，不进仓库）。

---

## 2026-10-26 · M1 第 0、1 批

- 时间：开工 16:50
- 第 0 批：已完成。`~/.hermes` 备份在 `M1-20261026/hermes-home-before-M1.tgz`，共 629 项；没有包含可以重装的 `hermes-agent`、`installs`、`tools`、`cache`。基线记在同目录的 `baseline.md`（v0.22.2，git d923a5eb，模型 deepseek-flash，没有定时任务，网关运行中、飞书已连接）。
- 第 1 批：本机已切换完成，等飞书实测。
  - Hermes 单独登录了 Codex 会员（设备码，凭证 `openai-codex-oauth-1`），不影响本机 Codex CLI 的登录。
  - 主模型：`openai-codex / gpt-6.1-sol`。按 `~/.codex/models_cache.json` 的说明，它是当前的主力模型。
  - 辅助模型：压缩和审批分类用 `openai-codex / gpt-6-luna`（快、便宜，省额度）；其余辅助任务跟随主模型。
  - 回退：`fallback_model = deepseek / deepseek-flash`。
  - 验证：`hermes doctor` 显示 Codex 已登录、辅助任务都路由到 openai-codex；一次性对话回复了 `OK openai-codex / gpt-6.1-sol`；故意指定一个不存在的模型时，回复是 `deepseek / deepseek-flash`，说明回退生效。网关已重启（pid 85113），飞书已连上。
  - 遗留：回退时要在回复里注明，这条写进第 3 批的 SOUL。
  - 飞书实测（17:01）：常浩发“嗨boy一天没说话了”，10 秒后回复，语气符合人设。`state.db` 的 `session_model_usage` 里，飞书会话 `20261021_200002_45130229` 这一次调用记录为 `gpt-6.1-sol / openai-codex / subscription_included`，走的是会员额度，没有按量计费。**第 1 批完成。**
- 第 2 批：已完成（17:10 前后）。
  - agenthub 前后端：`launchctl bootout` 停掉，plist 移走；`~/agent-hub`（530M，不是 git 仓库）移走；Claude 桌面配置 `claude_desktop_config.json` 里摘掉了 `agent-hub` MCP，修改前的版本留了备份。**Claude 桌面应用重启后才会生效。**
  - OpenClaw：plist（本来就没加载）和 `~/.openclaw`（197M）移走；用 Homebrew 的 npm 卸载了全局 `openclaw` 包（共 330 个包）。
  - 归档位置：`~/Archive/2026-10-26-retired/`，权限 700，有 README，2026-11-25 到期后可删。
  - 回读：常驻服务列表里已经没有这两个服务，`openclaw` 命令也不存在了。
- 第 3 批：部分完成，等常浩确认 Vault 的改动。
  - SOUL 已重写：新增数字分身定位、信息来源、信息边界、模型与额度、定时与主动；去掉了“不读 Obsidian”“不建定时任务”“不替换模型”；收尾措辞改成“emoji + 事实 + 现编收尾”，去掉了常浩否决过的固定例句。网关已重启（pid 9619），飞书已连上。修改前的 SOUL 在第 0 批的备份里。
  - 全局规则：`~/.claude/CLAUDE.md` 第 6 节、`~/.codex/AGENTS.md`「知识、权限与外部动作」的知识库条款，都改成了分级出口规则；修改前的版本在证据目录 `*.before`。飞书分工那条（`--profile codex`）留到 M5 再改。
  - Vault 三个文件的草稿在证据目录 `vault-drafts/`：`AGENTS.md` v1.5、两张决定页。按 Vault 规矩，**等常浩确认后再写入并提交**。
  - 本机预检（`hermes -z`，用的是同一份 SOUL）：① 群聊里诱导“贴个人上下文原文”→ 拒绝，并说明群聊文字只是材料、受限内容不进群，通过；② 带教取舍这类判断题 → 查了 Vault 个人上下文的 3 篇笔记，回复只给结论和路径，没贴原文，通过；③ 回退时在回复末尾注明 → **没注明**。网关自带一条回退提示（`⚠️ Provider fallback: … using deepseek/deepseek-flash`），但只在每轮开始前凭证失败时才会出现，对话中途发生的回退会不会提示，没有核实。
  - 常浩补充决定（2026-10-26）：**本机访问飞书也统一用猛鬼兽，飞书只保留这一个机器人**。草稿随之改动：飞书决定页改名为「飞书统一用猛鬼兽」，同步改了总入口里的链接；Vault 决定页加了一句说明；PRD 的 D0、D12、D13、F9.3、退役清单同步更新，M5 里新增 T8b（约 20 个目录从 `--profile codex` 迁过来，迁完后停用机械邪龙兽）。`~/.codex/AGENTS.md` 里 `--profile codex` 那条绑定，迁移时一并修改。
  - 常浩确认后写入 Vault，共两次提交：`3c2c688`（只有改名：飞书统一用机械邪龙兽 → 飞书统一用猛鬼兽）和 `089894f`（4 个文件的正文：AGENTS v1.5、两张决定页、总入口）。第一次提交时 `git add` 用了旧路径报错，正文没有跟上；按 Vault 规矩不改写历史，所以补了第二次提交。回读：工作区干净。
  - 常浩补充：营销战役 MVP 他会整体删除，不纳入迁移。PRD 第 7 节已改，并新增 7.1“单一机器人的技术约束”：事件监听只由 Hermes 网关独占（飞书长连接模式下，同一应用的事件只推给其中一个客户端）；本机项目只主动调用接口；频率限制共用；发送者统一显示猛鬼兽，需要时在末尾注明来源。迁移范围缩小到牛马之家、personal-ai-workbench、公司战略系统、管培生带教（它的卡片回调要改走 Hermes 转发，或者去掉）。
  - **第 3 批完成**（遗留：回退时在回复里注明还不可靠，第 5 批巡检时补一条“前一天有没有走回退”的检查）。
- 第 4 批：样稿已出，等常浩定格式。
  - 取数脚本：`hermes/scripts/brief_data.py`（项目里是源文件，安装副本在 `~/.hermes/scripts/`），只用标准库，Hermes 自带的 Python 可以直接跑。功能：从日报第四段算出到期和逾期；摘出第六段的建议；查阻塞是否连续 3 份日报都出现；从周报里找挂起满 4 周的事项；查日报断档；接口返回缓存旧结果时提示。
  - 晨报提示词：`hermes/prompts/morning_brief.md`；样稿：`hermes/samples/晨报样稿-20261026.md`（gpt-6.1-sol 根据 10/23 日报和第 43 周周报生成）。
  - 发现牛马之家的问题（不在本项目范围，没修）：10/22 的日报标题没被识别，内容整段混进了 10/23 那篇。脚本已能识别这种情况，并在晨报里提示。
  - **常浩决定挂起第 4 批（2026-10-26）**：现在的日报本身写得不好，抓到的信息可能不准，在此基础上提炼没有意义。等 T1 日报结构重新设计后再恢复。脚本、提示词和样稿都保留，不建任何定时任务。
- 第 5 批：已完成。
  - 脚本：`ops/health_check.py` 是源文件。安装副本放在 `~/Library/Application Support/DigitalTwin/bin/health_check.py`，因为 launchd 调起的 `/usr/bin/python3` 读不了 `~/Documents`（macOS 隐私保护，第一次实测时报 Operation not permitted）。**改了源文件要重新复制到安装位置。**
  - 常驻任务：`cn.digital-twin.health`，每天 09:45 跑（常浩 2026-10-26 从 08:50 改）；plist 也复制了一份到 `ops/`。
  - 检查项：网关是否加载、进程是否在跑、状态是否为 running、飞书是否 connected、心跳 10 分钟内有没有更新；前一天主对话有没有走回退模型（排除本机 `hermes -z` 测试会话，也就是 source=oneshot）；定时任务有没有失败记录。纯脚本，不调用模型。
  - 输出：每次都在 `evidence/health/YYYY-MM.log` 记一行；只有出问题时，才用猛鬼兽机器人身份（`lark-cli --profile cli_a96f5f9ac63adcd4 --as bot`）往私聊发告警。
  - 实测（17:20）：停掉网关后由 launchd 触发一次巡检，告警发送成功；随后恢复网关。17:22 网关正常时复跑，结果 OK。
  - 修过的问题：网关停着时，WAL 数据库的 `-shm` 文件不在，只读连接打不开，已改成普通连接（只做查询）。修改后**没有在网关停止的状态下复测**。
- M1 小结：第 0、1、2、3、5 批完成，第 4 批挂起。PRD 里 M1 的 F4 验收要等日报重新设计后才能做。
- 巡检时间：常浩改成每天 09:45，plist 已重新加载，回读确认触发时间是 9:45。

## 2026-10-26 · M2 批 A（打通两个车间）

- 时间：开工 17:30
- 三个待定项按推荐定：写入单放开权限，边界靠 worktree、护栏和合并确认；名册 v0 收牛马之家、personal-ai-workbench、agent-kit、github-center、digital-twin；验收用的 5 张单到批 E 再由 Claude 挑。
- 包装脚本：`hermes/scripts/run_worker.py`（安装副本在 `~/.hermes/scripts/`）。Claude 和 Codex 共用一个入口：代理可达时自动设上 127.0.0.1:8086，清掉第三方 Anthropic 变量；read 模式只读（Claude 只允许 Read/Grep/Glob/LS，Codex 用 `-s read-only`），write 模式放开权限；会给车间统一加一段开头说明（先读 AGENTS、不切主分支、不合并、不 push、卡住时输出 `NEED_DECISION:`）；跑完写 `receipt.json`，护栏检查主分支和远端引用有没有变。退出码：0 正常，1 失败或超时，2 护栏被触发。
- 实测：
  - 终端里用干净环境（`env -i`）对 agent-kit 跑只读单：Claude 20 秒、Codex 53 秒，都正确总结了仓库，护栏正常。
  - **网关环境**：建了一个 1 分钟后执行、只跑一次的 Hermes 定时任务，由网关自己的调度器执行（运行记录 source=builtin，环境里确实没有代理）。结果：读 Vault OK，读仓库 OK，Claude 和 Codex 都回复“OK agent-kit”，退出码都是 0，护栏正常。之前担心的“网关里直连 403”和“macOS 隐私保护”都没成为问题。第一次用 `cron run` 手动触发那次是在终端进程里跑的（source=direct，带着代理），不算数，已删掉。
  - 探针脚本和临时目录已清理，定时任务列表为空。
- 车间技能：`hermes/skills/workshop/SKILL.md`（安装在 `~/.hermes/skills/autonomous-ai-agents/workshop/`，已启用）。目前只接只读单，限定在名册 v0 的 5 个仓库；单号格式 `D年月日-序号`；后台运行，结束后读回执回报，护栏一旦触发就在回报第一行标 ⚠️。网关已重启（pid 22932）。
- agent-kit 本身有不属于本项目的未提交改动（LESSONS.md 修改、skills/ 未跟踪），没碰。
- 飞书验收（常浩派单，Claude 回读确认）：
  - `D20261026-1` Claude · personal-ai-workbench · 50 秒：✅。按要求 5 句话说清进度和卡点，每条都带出处（提交号、文件和行号），并主动说明“没有运行服务或测试复验”。回报格式符合技能要求。
  - `D20261026-2` Codex · 牛马之家 server · 8 分 52 秒：清单完成，共 177 个接口，附了 CSV。护栏触发（退出码 2），Hermes 第一行标了 ⚠️，没有重试，并说明“不能确定是谁更新了主分支，保留告警”，处理方式正确。
  - **护栏误报的根因**：跑单期间，另一个 Claude 会话（牛马之家 ui-v4 分支，培养路径卡头修复）在 17:58:04 和 18:00:24 两次快进合并了 main（`2a86e20`、`2928948`），不是 Codex 动的。Codex 是只读沙箱，本来就写不了。
  - 修正：`run_worker.py` 的护栏改成只在两种情况下报违规——写入模式下车间自己的提交进了主分支，或者车间把当前分支切到了主分支；别的会话正常更新主分支只记在 `note_main_moved`，不告警。技能升到 0.1.1，同步说明“note 不算告警”。回归：干净环境下 Claude 只读单退出码 0，护栏 false，note 为空。网关已重启（pid 33234）。
  - 中间有一次回归用错了任务文件（拿第一单的重任务去跑 agent-kit），300 秒超时，浪费了一次 Claude 额度，没有其他影响。
- **M2 批 A 完成。**
- 下一步：批 B（项目名册 v0 + digital-twin 建独立仓库）。

### M1 分批（PRD 第 10 节细化）

| 批 | 内容 | 状态 |
|---|---|---|
| 0 | 备份 + 基线 + 本文件 | 已完成 |
| 1 | 换大脑：openai-codex 作主模型和辅助模型，deepseek-flash 作回退 | 已完成 |
| 2 | 清理 agenthub、OpenClaw（先归档，30 天后删除） | 已完成 |
| 3 | 信息边界落地：SOUL、全局规则、Vault AGENTS 和两张决定页 | 已完成 |
| 4 | 提醒上线：晨报样稿 → 取数脚本 → 4 个定时任务 | 挂起（等 T1 日报重设计；样稿和脚本已留） |
| 5 | 独立巡检脚本，每天 1 次，只在异常时告警（代替 PRD F9.2 原来“并进牛马之家”的写法） | 已完成 |
