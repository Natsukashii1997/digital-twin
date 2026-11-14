# digital-twin 协作约定

常浩的数字分身项目：让 Hermes（猛鬼兽）成为飞书里唯一的 AI 入口，懂常浩、主动推动、把活派给 Claude Code / Codex 并收回结果。所有面向用户的文字用简体中文。

进入顺序：[PRD.md](PRD.md)（需求与决定，唯一真源）→ 本文件 → [CURRENT.md](CURRENT.md)（当前状态与唯一的下一步，新记录写在最上面）。

## 结构

```
PRD.md / CURRENT.md / AGENTS.md
ai_access.json         AI 接入配置（P-13，代替设置页）：执行方、功能清单、例外、对外开放、数据对象和出厂默认值；见下面「AI 接入」
projects.json          项目名册：Hermes 只往 projects 里的活跃仓库派单（run_worker.py 也会拦）；inventory 是 M3 盘点的其余仓库及去留，不派单
hermes/
  scripts/             给 Hermes 用的脚本（源文件）；安装副本在 ~/.hermes/scripts/
    run_worker.py      车间包装：统一调用 Claude / Codex，开跑前查执行方能不能用，写回执（含执行方、接入方式、模型、怎么选的）、查护栏
    ai_access.py       AI 接入自检与切换：status 打印四块，check 查一家，set 切换（常浩的选择存在 DigitalTwin/ai-access.json）
    brief_data.py      晨报取数（第 4 批挂起中）
    progress_data.py   项目进展取数（F6，常浩问“今天各项目怎么样”时现查）
    pdaily_data.py     个人项目日报取数（#110，PRD D22）：按统计日（04:00→04:00，Asia/Shanghai）给名册每个项目出一条记录（阶段目标、里程碑、昨日增量、工程 / 交付 / 验收三项、当前状态、新鲜度），JSON 输出；交付、验收一律记「未核实」，只附自述摘录（当天 CURRENT 节、PR 标题），等 #110 1b 的行动记录再判；目标、CURRENT、新鲜度读主分支在统计日结束时的快照，不读工作区；取不到记 null 不当 0；GitHub 遇到网络问题（唤醒后代理没通）最多等 300 秒重试（DT_GH_WAIT_SECONDS，#176）；只读，未安装
    actions.py         代理行动记录（#110 1b，V2 5.4）：actions.jsonl 的结构（定稿不再改）和 record() 记录函数；--date 按统计日列出行动：actions.jsonl 加六个来源现读现转（合并官、merge-runs、派单台账、定时任务库、夜跑 run-log、裁决文件），每条带授权档位、来源，低于应有档位的标 auth_gap；裁决文件只摘原文不判批准拒绝；安装只认 record() 写的；只读，未安装，暂无脚本调用 record()
    pdaily_publish.py  个人项目日报写稿与核对（#111 批 2a）：读 facts.json，经 hermes chat 单次让 Hermes 写卡片要素和完整报告（提示词 prompts/pdaily_report.md），脚本核对编号、百分比、PR 数、「已安装 / 已验收」说法、三个数和七段，写法问题自动修并记提醒，编造类不通过就出「分析降级」事实版，再坏出最小卡片；按甲版定稿（11-10，五板块全放卡片不折叠、速览表列宽 25/15/60）生成卡片 JSON；send 建完整报告云文档、拦工作内容（台账编号、工作日报文档等一律拒发）、以猛鬼兽机器人发常浩私聊并回读，记 delivery.json，同一统计日只发一次（#155）
    pdaily_build_job.py / pdaily_send_job.py  个人项目日报定时任务入口（#155）：Hermes 无模型定时任务 09:15 写稿、10:00 发送（#159，原 09:00 / 09:45）；发送成功不输出，没发成输出一句原因发到私聊
    pdaily_facts.py    个人项目日报事实快照（#110 1c）：把 pdaily_data 的项目记录和 actions 的行动合成 DigitalTwin/daily/<统计日>.facts.json，给 Hermes 写日报用；含拍板候选、风险四类、跨项目明文引用（查被引用方关没关）、观察池（每次现算）、覆盖与可信度；只给候选和出处，不定级、不排序、不推荐；取不到写 null 不当 0；--dry-run 只打印；未安装
    report_data.py     工作日报 / 周报取数（T7，新结构并跑；2026-11-11 常浩定从猛鬼兽撤下，定时任务已暂停，PRD D20）；report_daily.py、report_weekly.py 是定时任务入口；开跑前先等主模型通道连通，最多 300 秒，到时间照常放行交给回退（#140，记录在 evidence/reports/model-route-wait.jsonl）
    report_publish.py  把写好的日报 / 周报插到并跑文档最上面并回读
    memdaily_data.py   记忆日报取数（亚古兽，PRD D20 11-11 修订）：亚古兽应用、常浩的用户身份读 D 当天的私聊（排除机器人）、白名单群（全读）和常浩参与的群、实际参会的视频会议和妙记、日程、任务，以及《常浩-记忆日报汇总》前一篇的行动 & 阻塞；周末没有工作往来、或这天已写过就跳过；白名单群、永久不去的会议、重点人在本机 DigitalTwin/memdaily/config.json（不进仓库）
    memdaily_publish.py 记忆日报写稿与写入：hermes -p agumon 单次写六模块内容 JSON（提示词 prompts/memdaily_report.md），脚本核对（状态、工作线、人名群名、议题时间对得上材料、私生活兜底删行），不过重写一次；按 7/15 永久模板排成 DocxXML（Aily v6.0 列宽），插到文档最顶部，写前存整份快照、已有同一天不重写、写后回读；亚古兽机器人私聊三段式推送，同一天只发一次
    memdaily_job.py    记忆日报定时任务入口（亚古兽 profile，无模型，每天 07:30 写前一天）；成功或跳过不输出，没写成输出一句原因
    twin_state.py      持久任务状态与事件底座（B1）：Goal / Task / Run / Event / Ref，SQLite 在 ~/Library/Application Support/DigitalTwin/twin-state.db；设计见 docs/B0-七层架构复核与状态底座设计.md
    twin_scheduler.py  持久调度（B2.2）：只读决策 plan、调度预留、按关联键补偿恢复、生命体征、取消建议、低噪声报告；自动开单默认关闭；设计见 docs/B2.2-调度与恢复机制设计.md
    custody.py         托管状态底座（#131，托管模式规格 §3.7、§3.8）：custody_state = off | on | draining | frozen 的唯一真源，只有它写 DigitalTwin/custody.json（逐字段校验、原子替换 + 排他锁；写过后文件丢了不当成第一次用）；status --json 带统一准入判定 admission（下游不得只看 state）；on 只认飞书私聊（twin_identity 回读核验 + 口令原文 + 防重放，时刻有歧义就拒绝）和已签策略的默认时段，off / revoke 任何来源都可以，到 until 自动排空、到排空截止没收完自动冻结，off --drained 要本脚本能证明排空完成（账本 #133 接入前一律拒绝），frozen 只能 unfreeze（私聊「解除冻结」）；sign 按私聊口令「签托管策略 v<版本> <SHA-256 前 12 位>」签 DigitalTwin/custody/policy.md（不许未填项、字段位置固定且唯一、版本单调、全文哈希），revoke 持久撤销，verify 每次重算；工程自检只读显示；不发飞书、不装定时任务，托管在常浩签策略、落地工单验收前不生效；未安装
    aihot_hot.py       AI HOT 热点榜推送（#163）：Hermes 无模型定时任务每个工作日 09:45（周一到周五，不跟节假日调休）调用，取 aihot.news 热点榜 TOP10 和每条事件摘要，按常浩 11-11 定稿的「名次栏」样式排成卡片（不要表头，横幅 assets/aihot-banner.png 就是标题，同 GitHub 周榜卡片；标题只链到 aihot.news，不放来源 / 参与者 / 时间），以猛鬼兽机器人发常浩私聊并回读；同一天只发一次；成功不输出，取不到数据或没发成输出一句原因（退出码 1）；记录在 DigitalTwin/aihot/（runs.jsonl、<日期>.card.json、state.json）
    feishu_home.py     私聊 chat_id（#165）：先看环境变量 FEISHU_HOME_CHANNEL，没有再从 ~/.hermes/.env 只读这一项；无模型定时任务的环境里没有这个变量，往私聊发消息的脚本（aihot_hot、tidings_weekly、proposal_digest、merge_act）都用它
    tidings_weekly.py  Tidings 周报推送（#55、#58）：Hermes 无模型定时任务每 15 分钟调用，按 github-center 设置页（data/settings.json 的 weekly：开关、星期、时间，默认周一 19:30，不早于每日更新时间，github-center #83）到点才发，把 github-center 写好的周报卡片插上横幅（assets/tidings-banner.png）发到常浩私聊；成功不输出
    skill_health.py    skill 月度体检推送（#151）：Hermes 无模型定时任务每月 1 号 10:30 调用，在 my-skills 下跑 node scripts/monthly-health.mjs --days 30，标题改粗体、表格改成逐条列表、末尾加「抽查 <名字>」提示，stdout 发到常浩私聊；失败退出码 1，由 Hermes 发失败通知；只读
    multica_night.py   Multica 夜跑主控（#92，DT-ORC M5 / 5.3a）：Hermes 无模型定时任务每 5 分钟经 multica_night_tick.py 调 tick（approve 记下的开跑时刻到了就先 start）；按计划文件在 DT 沙盒建父单和分 stage 子单，子单交付（in_review）后推进下一 stage，边跑边导 run-log 和消息到 DigitalTwin/runs/<夜跑>/，到上限按 5.3a 停机（禁用 Wakeup、暂停 Autopilot、取消非终态 run、回读），回读不过就冻结执行器容器；开跑前核对名册 sandbox: true；开跑和每次放 stage 前跑执行器身份闸（#174：信任基准在名册 dt-sandbox 的 executor_gate，保护基线在 hermes/baselines/，两者只读已合并的 origin/main；不通过就不放，tick 里不通过停机并冻结执行器；冻结命令只按名册生成，名册读不到就不执行任何命令、告警请人手动停）；不合并、不送审；计划样例 hermes/samples/multica-night-2026-11-08.json，执行单 docs/DT-ORC/migration/2026-11-08-M5-首夜执行单.md
    merge_officer.py   影子合并官（#61，只读）：按 merge_rules.json（带版本号）对名册活跃仓库的 open PR 给「建议合并 / 退回 / 问常浩」，读真实结局算一致率，记在 ~/Library/Application Support/DigitalTwin/shadow/agreement.csv（PR 状态快照 facts.jsonl）；replay 回放改规则前后的一致率；只报告是否达到同类 20 次、≥90%，不合并、不放权
    merge_act.py       飞书一句话合并（#67）：merge_officer.py 给 merge_rules.json 的 act.repos 里的仓库每个新「PR × 版本」发卡片；常浩点卡片上的「合并」按钮或引用卡片回「合并」，merge-officer 技能调本脚本核对版本 / 冲突 / 落后 / 本机主分支 / 风险档（L3 不走飞书），在干净 worktree 跑测试，带 --match-head-commit 合并；点「退回」或回「退回 原因」就在 PR 留言。决定记在 agreement.csv 的 owner 列；托管合并（#136）只能调 run_custody_merge，凭据在函数里从本机钥匙串取合并执行器专用令牌（#132）
    proposal_digest.py 每周提案收口（#61）：每周一把会话复盘、牛马之家每周整理的待决提案按编号发到常浩私聊（LESSONS 升级候选默认不列，DT_LESSONS_MIN_PROJECTS 打开）；每条附 Hermes 的推荐（带模型定时任务经 proposal_digest_draft.py 取草稿、写推荐后 send --recommend），常浩回「按推荐」或逐条回复后由 proposal-digest 技能调用 apply 写回各来源的结论；期号状态在 DigitalTwin/proposals/digest.json
  skills/<名>/SKILL.md Hermes 技能（源文件）；安装在 ~/.hermes/skills/autonomous-ai-agents/<名>/
    workshop/          车间派单（F5）
    progress/          项目进展现查（F6）
    vault/             关于常浩的判断类问题先查 Vault（F3，用 MCP vault-read）
    merge-officer/     常浩点 PR 卡片的「合并 / 退回」按钮或引用卡片回复时调 merge_act.py（#67、#78）
    proposal-digest/   记下常浩对每周待决提案的选择（#61，调 proposal_digest.py apply）
  plugins/merge-buttons/ PR 卡片按钮（#78）：注册 /card，把「合并 / 退回」点击转给 merge_act.py button（不经过模型）；安装在 ~/.hermes/plugins/，config.yaml 的 plugins.enabled 里启用
  plugins/workdesk-gate/ 亚古兽工具拦截（#142）：飞书里发起人不是常浩（open_id 或企业 user_id `e9cgcf77`）时只放行安全工具（提问、读文档、文档评论、网页、技能查看、生图、语音、待办），本机命令行和定时任务不受限；牛马之家只在常浩本人私聊可用并写入真实提问人；安装在 ~/.hermes/profiles/agumon/plugins/，测试 hermes/scripts/tests/test_workdesk_gate_plugin.py
  prompts/             定时任务等用的提示词和项目词表；安装在 ~/.hermes/prompts/
  workspace/HERMES.md  猛鬼兽工作规程（源文件）；安装在 ~/.hermes/workspace/HERMES.md，只在工作目录是 workspace 时加载；第 5 节是个人项目日报规则（猛鬼兽只报个人项目）
  samples/             样稿
  baselines/           身份闸的 GitHub 保护基线（#174）：dt-sandbox 规则集和 main 分支保护，名册 executor_gate 指向它；改保护规则先改 GitHub 再改这里
docs/agents/           Matt Pocock 技能读的约定：issue 看板、分流标签、领域文档
docs/DT-ORC/           Hermes × ORCA 支线文档；ARCHITECTURE.md 是唯一工程架构基线，history/ 存被取代的旧方案
docs/三报体系-V2.1.md   主动运营与三报（早报 / 晚报 / 周报）的产品与实施基线（PRD D20）；评审过程在 docs/reviews/
docs/常设授权清单.md     Hermes 的常设授权四档（PRD D21）：动作前查，日报按它写依据；只有常浩能改
docs/个人项目日报-V2-设计.md  个人项目代理管理日报规格与样稿（PRD D22，#108）：每天 08:00 私聊简报 + 本地完整日报，只覆盖个人项目
docs/夜间行动提案-设计.md      夜间行动提案（PRD D23）：每天 22:30 私聊提出今晚可替常浩推进的事，常浩回复即当晚授权，夜里只做到开 PR
docs/托管模式-设计.md          托管模式（PRD D24，ADR-014 及修订 1）：常浩不在场时猛鬼兽以常浩身份回答已批准夜跑里 blocked 子单、托管合并，三道闸、记账、事后汇报可推翻；策略模板 docs/托管策略-模板.md（仓库里的永不生效）
orca/
  chatgpt_relay.py     在 ORCA 内嵌浏览器的 ChatGPT 聊天里送审、收回执、REWORK 时交回 ORCA 终端里的 Claude（#35）；测试 `python3 -B -m unittest discover -s orca/tests`
ops/
  health_check.py      每日巡检（launchd 09:45）：网关、飞书连接、24 小时内异常退出、回退模型、辅助任务失败（猛鬼兽、亚古兽各查一遍，#147）、定时任务，另提醒飞书 lark-* 技能源头比机器人副本新；有异常才发飞书；安装在 ~/Library/Application Support/DigitalTwin/bin/
  self_check.py        工程自检（#159 起不单独发：每天 10:00 个人项目日报发送时调用，结果并进日报卡片，正常只写一句，有问题加「🔧 工程自检」一块）：每天比对仓库 main 和安装副本，并跑 github-center 的 bin/health.js（Github·Tidings 有问题才有输出）、custody.py status（托管没关、策略失效或快到期才有输出，#131）、ai_access.py status（车间默认执行方不能用、Hermes 主模型走的本机 Claude 没登录才有输出，#180）；每月 1 号（或 --monthly）再查沙盒和仓库；安装时与 check_repos.py 同放 ~/.hermes/scripts/
  cn.digital-twin.health.plist  巡检 launchd 配置的副本
  check_repos.py       名册活跃项目自检（只读，self_check.py 每月调用；DigitalTwin/bin/ 也留一份）：GitHub 设置、分流标签、PR 模板和《工程流程》指针、主目录检出主分支且没有没推的提交
```

## AI 接入

全局规则 P-13 要求：本应用里 AI 产出的东西，谁做的、怎么接进来的、读什么、写到哪里都看得清，改设置就能切换。本项目没有图形界面，所以用配置文件代替设置页，用自检命令代替状态页：

- 配置：仓库根目录 [ai_access.json](ai_access.json)（功能清单、出厂默认值，改了要和本节一起改）；常浩切换后的选择存在 `~/Library/Application Support/DigitalTwin/ai-access.json`，只由 `ai_access.py set` 写，下一次调用生效，不用改代码、不用重启。
- 自检命令：`python3 ~/.hermes/scripts/ai_access.py status`（`--json` 给机器读）。工程自检（并进每天 10:00 的个人项目日报）会跑它，车间默认执行方不能用、或 Hermes 主模型走的本机 Claude 没登录时才在日报里说（后者附带代理的登录命令，#180、全局坑 G-27）。
- 切换车间默认执行方：`python3 ~/.hermes/scripts/ai_access.py set default claude|codex`。Hermes 自己的模型用 `hermes config set model.default …` 切换，要重启网关。
- Hermes 自己的模型（2026-11-10 起，PRD F10.1、F10.3 修订）：主对话和带模型的定时任务用 Claude `claude-opus-5-5[1m]`，经插件 `claude-subscription-directsdk-experimental` 调本机官方 `claude` 命令行（本机 CLI，Claude 会员额度）；辅助任务用 openai-codex `gpt-6-luna`；回退 deepseek-flash。亚古兽 2026-11-11 起主模型改为 openai-codex `gpt-6.1-sol`、思考 xhigh（Codex 会员额度），其余同猛鬼兽；配置在 `~/.hermes/profiles/agumon/config.yaml`，用 `hermes -p agumon config set …` 改，改完重启 `ai.hermes.gateway-agumon`。

功能清单（id 和 ai_access.json 一致）：

| id | 功能 | 执行方 | 接入方式 | 读 | 写到 |
|---|---|---|---|---|---|
| `workshop` | 车间派单 | 常浩点名的；没点名跟随默认执行方（出厂 Codex） | 本机 CLI | worktree、任务文件 | 分支提交、PR、issue、派单回执、状态库 |
| `scheduler` | 持久调度自动开单（默认关闭） | 每个任务的负责方 claude / codex | 本机 CLI（经派单脚本） | 状态库 | 同车间派单 |
| `chat` | 飞书对话（含 progress、vault、merge-officer、proposal-digest 技能） | Hermes，仅支持 Hermes | Hermes 派单 | 飞书私聊、Vault（MCP vault-read 只读）、名册、仓库 | 飞书回复、Hermes 记忆 |
| `hermes-aux` | Hermes 辅助任务（压缩、审批判断、后台复盘） | Hermes | Hermes 派单 | 当前会话 | Hermes 记忆和技能 |
| `report` | 工作日报 / 周报并跑（**已暂停**：2026-11-11 从猛鬼兽撤下，PRD D20） | Hermes，仅支持 Hermes | Hermes 派单 | 飞书私聊、群、会议妙记、日程、任务、项目进展 | 飞书并跑文档、evidence/reports |
| `pdaily` | 个人项目日报 V2（#111、#155、#159：09:15 写稿、10:00 发私聊，带工程自检） | Hermes（主模型 Opus 5.5，思考 medium，#157），仅支持 Hermes；失败出脚本事实版，不换家 | Hermes 派单（`hermes chat` 单次） | 事实快照 facts.json、提示词 pdaily_report.md | DigitalTwin/daily/ 的卡片、完整报告、模型原稿、build.json、delivery.json；飞书完整报告云文档；常浩私聊卡片 |
| `memdaily` | 记忆日报（亚古兽，PRD D20 11-11 修订：每天 07:30 写前一天，接着写《常浩-记忆日报汇总》六模块） | Hermes 亚古兽（openai-codex gpt-6.1-sol，思考 xhigh），仅支持 Hermes；核对不过就不写、报原因，不换家 | Hermes 派单（`hermes -p agumon chat` 单次） | 常浩的飞书私聊、群、会议与妙记、日程、任务（亚古兽应用，用户身份）；前一篇行动 & 阻塞 | 《常浩-记忆日报汇总》顶部；DigitalTwin/memdaily/；亚古兽私聊推送 |
| `proposal-digest` | 每周待决提案的推荐 | Hermes，仅支持 Hermes | Hermes 派单 | 会话复盘提案、牛马之家每周整理 | 推荐文件、飞书卡片 |
| `relay` | ORCA ChatGPT 送审 | ChatGPT 网页，仅支持 ChatGPT 网页（送审就是要另一家独立审） | 网页桥 | 送审包 | 回执、DigitalTwin/relay/；返工时交给 ORCA 终端里的 Claude |

不调用 AI：合并官（merge_officer、merge_act、merge-buttons 插件）、Tidings 周报推送（只转发 github-center 的卡片）、AI HOT 热点榜推送（概括按句截取、话题标签按关键词，不经模型）、三个自检脚本、skill 月度体检推送、晨报取数（挂起）。

- **不可用就报错，不换家**：派单开跑前 run_worker 按配置查执行方（`claude auth status`、`codex login status`）；不能用就写 `refused: executor_unavailable` 回执、退出码 4，派单返回「没开跑」和原因，不改用另一家。额度用尽没法提前查，开跑时车间报错写进回执。AI 接入配置读不了时，没点名的派单直接拒绝开单，不猜默认值。
- **已登记的例外**：Hermes 主模型（现为 Claude）失败或额度不够时自动回退到 deepseek-flash（PRD F10.2；常浩 2026-11-07 定保留）。回复里注明，每日巡检报前一天的回退次数，自检命令显示近 7 天的回退次数。
- **留痕**：派单回执有 `executor`、`access`、`model`（Claude 取实际用的模型；Codex 取 ~/.codex/config.toml 的配置）、`chosen_by`，飞书回报第二行照抄；日报 / 周报标题下一行写「生成：Hermes（实际用的模型，含回退）｜接入：Hermes 派单｜时间」；Hermes 对话和定时任务的模型用量在 ~/.hermes/state.db、~/.hermes/cron/usage_audit.jsonl。
- **对外开放**：无。vault-read 是 Hermes 作为客户端读 Vault，不算本项目对外开放。
- **数据对象**：Hermes 模型用量（state.db 的 session_model_usage，巡检统计回退）；Hermes 定时任务用量（usage_audit.jsonl）；Claude / Codex 的 skill 调用记录（agent-kit 的 skill-usage.jsonl、~/.codex/sessions，skill 月度体检统计）。
- **密钥**：本项目不保存 AI 密钥；Claude、Codex 用本机登录，Hermes 的凭证在 Hermes 自己那里，自检只显示可用 / 未登录。

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
