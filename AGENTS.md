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
    pdaily_publish.py  个人项目日报写稿与核对（#111 批 2a；#226 改乙版）：读 facts.json，经 hermes chat 单次让 Hermes 写卡片要素和完整报告（提示词 prompts/pdaily_report.md），脚本核对编号、百分比、PR 数、「已安装 / 已验收」说法、三个数和六段，另核对每类风险有去向、指向的拍板存在、同一类不拆两条、缺材料的事不进拍板（「同一件事在风险和拍板两处各说各的」判不准，只记提醒）；写法问题自动修并记提醒，不通过就带着问题让模型重写一次，再不过出「分析降级」事实版，再坏出最小卡片；按乙版定稿（常浩 2026-11-19 选定：四板块 核心数据速览 → 昨日关键成果 → 风险与阻塞 → 需要你拍板；风险按原因一类一行、带去向「→ 拍板 N」或「只观察」，项目明细在折叠面板；拍板是 #、事项、推荐、已挂四列的表，已挂几天由脚本按候选起始时间算；没有今日推进；速览表列宽 25/15/60 是 11-10 定稿）生成卡片 JSON；send 建完整报告云文档、拦工作内容（台账编号、工作日报文档等一律拒发）、以猛鬼兽机器人发常浩私聊并回读，记 delivery.json，同一统计日只发一次（#155）；工程自检有问题时并进风险表的一行、原文进折叠面板，正常时页脚一句
    pdaily_build_job.py / pdaily_send_job.py  个人项目日报定时任务入口（#155）：Hermes 无模型定时任务 09:15 写稿、10:00 发送（#159，原 09:00 / 09:45）；发送成功不输出，没发成输出一句原因发到私聊
    pdaily_facts.py    个人项目日报事实快照（#110 1c）：把 pdaily_data 的项目记录和 actions 的行动合成 DigitalTwin/daily/<统计日>.facts.json，给 Hermes 写日报用；含拍板候选、风险四类、跨项目明文引用（查被引用方关没关）、观察池（每次现算）、覆盖与可信度；只给候选和出处，不定级、不排序、不推荐；取不到写 null 不当 0；--dry-run 只打印；未安装
    report_data.py     工作日报 / 周报取数（T7，新结构并跑；2026-11-11 常浩定从猛鬼兽撤下，定时任务已暂停，PRD D20）；report_daily.py、report_weekly.py 是定时任务入口；开跑前先等主模型通道连通，最多 300 秒，到时间照常放行交给回退（#140，记录在 evidence/reports/model-route-wait.jsonl）
    report_publish.py  把写好的日报 / 周报插到并跑文档最上面并回读
    memdaily_data.py   记忆日报取数（亚古兽，PRD D20 11-11 修订）：亚古兽应用、常浩的用户身份读 D 当天的私聊（排除机器人）、白名单群（全读）和常浩参与的群、实际参会的视频会议和妙记、日程、任务，以及《常浩-记忆日报汇总》前一篇的行动 & 阻塞；周末没有工作往来、或这天已写过就跳过；白名单群、永久不去的会议、重点人在本机 DigitalTwin/memdaily/config.json（不进仓库）
    memdaily_publish.py 记忆日报写稿与写入：hermes -p agumon 单次写六模块内容 JSON（提示词 prompts/memdaily_report.md），脚本核对（状态、工作线、人名群名、议题时间对得上材料、私生活兜底删行），不过重写一次；按 7/15 永久模板排成 DocxXML（Aily v6.0 列宽），插到当月文档《常浩-记忆日报汇总 · YYYY-MM》最顶部（11-17 常浩选方案甲：旧汇总文档 TCG9dy7Uco… 满飞书 4 万块上限，原样归档；当月第一篇自动建新文档，写前数块数加本篇估算，超过 3.6 万提前开「续」，飞书仍报超限就开续篇再写一次；各份文档登记在 DigitalTwin/memdaily/docs.json），写前存整份快照、已有同一天不重写、写后回读；亚古兽机器人私聊三段式推送（新开文档时多一行说明），同一天只发一次
    memdaily_job.py    记忆日报定时任务入口（亚古兽 profile，无模型，每天 07:30 写前一天）；成功或跳过不输出，没写成输出一句原因
    sidebar_reports.py 销售 AI 侧边栏固定汇报（亚古兽，常浩 2026-11-19 定）：feedback 需求反馈（每天 18:00 私发常浩确认，回「发群」后用他本人身份发测试反馈群，只发一次）、progress 项目推进进度卡片（每周三 18:30 私发）；版式按 11-13/11-17/11-18 定稿固定在脚本里，亚古兽只写内容 JSON，脚本核对（上期待解决 / 待上线必须复核带上、本周下周条数相同等）后发送，同一期原地改；存档 DigitalTwin/sidebar/；回归基准是定稿原件 tests/fixtures/sidebar/；做法在技能 skills/sales-sidebar/（三份说明和样例；功能同步通知常浩说了才出）；装在 ~/.hermes/profiles/agumon/ 下
    twin_state.py      持久任务状态与事件底座（B1）：Goal / Task / Run / Event / Ref，SQLite 在 ~/Library/Application Support/DigitalTwin/twin-state.db；设计见 docs/B0-七层架构复核与状态底座设计.md
    twin_scheduler.py  持久调度（B2.2）：只读决策 plan、调度预留、按关联键补偿恢复、生命体征、取消建议、低噪声报告；自动开单默认关闭；设计见 docs/B2.2-调度与恢复机制设计.md
    custody.py         托管状态底座（#131，托管模式规格 §3.7、§3.8）：custody_state = off | on | draining | frozen 的唯一真源，只有它写 DigitalTwin/custody.json（逐字段校验、原子替换 + 排他锁；写过后文件丢了不当成第一次用）；status --json 带统一准入判定 admission（下游不得只看 state）；on 只认飞书私聊（twin_identity 回读核验 + 口令原文 + 防重放，时刻有歧义就拒绝）和已签策略的默认时段，off / revoke 任何来源都可以，到 until 自动排空、到排空截止没收完自动冻结，开启时在托管账本登记、把账本身份记进会话（库不在就不开启），每次进入排空 / 冻结都尽力清账（作废已出未送达的决定），off --drained 要本脚本读托管账本证明排空完成（先核对账本身份和开启登记，再作废已出未送达的决定，再核对没有在途、结果不明、UNRECONCILED、有原文没账本的，#133），frozen 只能 unfreeze（私聊「解除冻结」）；sign 按私聊口令「签托管策略 v<版本> <SHA-256 前 12 位>」签 DigitalTwin/custody/policy.md（不许未填项、字段位置固定且唯一、第 2 节的额度上限必填、版本单调、全文哈希），revoke 持久撤销，verify 每次重算；工程自检只读显示；不发飞书、不装定时任务，托管在常浩签策略、落地工单验收前不生效；未安装
    custody_ledger.py  托管账本（#133，规格 §3.5、§3.6、§3.8）：twin-state v4 的 custody_decisions / custody_quota / custody_sessions 三张表（库不在就报错，不隐式新建；账本身份 + 开启登记，库被换成新建的或旧备份就对不上），每次变化同事务记 custody.decision.* 事件；决定号、对象、依据（可引用其他决定号）、撤回能力、送达状态、处置状态、有效期、作废原因；原文另存 DigitalTwin/custody/<托管号>/<决定号>.json（600，90 天后清）；项目只认名册里的沙盒、子单号只认规范写法（额度键只有一种写法）；回复、合并要托管准入（custody.admission，持写锁时现读）和策略允许，额度在同一个 BEGIN IMMEDIATE 事务里原子预占（每次托管的回复数、合并数，每张子单的托管次数跨托管累计）；确认失败或作废释放，结果不明冻结、按结构化证据对账后才计为已用或释放（合并做成必须带合并提交号）；撤不回的动作只能记成挂起；写给常浩看的五项（这件事在做什么、agent 问了什么、我替你定的、为什么、常浩要做什么）按动作必填、限长、不许内部术语，不合格不记账；drain 给 custody.drain_proof 用；只读命令 show / list / drain-check；给 #134、#136 的接口在头注释；未安装
    custody_report.py  托管汇报卡与推翻（#133）：收尾（排空完成或冻结）后按账本发私聊卡片（按项目分组，挂起 / 结果不明 / UNRECONCILED 置顶，冻结过标题写「托管未能安全收尾」；乙版表格 + 白话改写，常浩 11-14 定：「等你处理」「替你定了的」各一张六列表格（哪件事、agent 问你什么、我怎么处理的 / 我替你定的、为什么、后来怎样、你要做什么 / 你不同意怎么办），「后来怎样」现读夜跑记录，表下一排「推翻：我替你定的」按钮），只发常浩私聊，任何返回前先核对账本身份，发卡前清账，每张卡一个随机令牌（页脚小字），回读核到托管号、令牌和按钮数才算发过（没核实的下次先按原消息号回读，核实不了才重发），同一收尾只发一次；每条可推翻的决定带「推翻」，按钮经 merge-buttons 插件转来（带卡片令牌），只认常浩私聊、只认最新一张核实过的卡上真有按钮的决定，账本身份对不上不处理；推翻分阶段、可恢复（进度在 <决定号>.overturn.json，再点一次或电脑上 resume 只补做没完成的）：记「已推翻·已记录」、写规则补丁候选（DigitalTwin/custody/rule-candidates.jsonl，同一决定只记一次）、列下游影响清单（custody_impact.py，四类规则，PR 翻完所有页、以 PR 的全部提交为种子，夜跑记录坏行或不完整、PR 被强推过都写「没列全」）；合并类推翻由合并执行器开 revert PR（merge_act.run_custody_revert，只开不合；合并还没核清就等核清再开，中断或不确定时只按分支只读查、核验提交和正文对得上且唯一才认领，不再推送），再发「合并 revert PR」按钮卡（回读核实），常浩点了交给 merge_act.merge_revert_as_owner：和飞书卡片合并同一套检查（含 CI 必须通过、敏感路径拦下），另要求登记的测试检查真的成功（dt-sandbox 现在没有 CI，撤销 PR 会停下），过了才用本机 gh 合并；谁在收尾后调 send 由 #134 接；未安装
    autoloop_grants.py 任务凭证与派单网关（#195 批 0B-1，规格 docs/秘书代批-规格.md §2、§5、§8）：写入派单两步批准（propose 随机生成 8 位确认码、插入不覆盖，记完整仓库和内容哈希，由脚本自己以猛鬼兽机器人把五要素和「派单 <确认码>」发到常浩私聊 → 常浩回「派单 <确认码>」→ approve 经 twin_identity 回读核验、防重放，确认消息已送达、内容和名册映射都没变才签凭证），只派名册里的沙盒和 write_allowed 明确为 true 的活跃项目（缺字段不算）；凭证写仓库、五要素哈希、执行方、动作（只 edit / push_branch / open_pr）、到期、状态、执行代次；网关只有两个入口往中台发 hermes 来源事件（approve 建任务、resume-dispatch 按有效凭证续接：重派 / 返修 / 更正），事件只带单号、代次、类别，五要素写在工人只读的任务单 /Users/Shared/dt-autoloop/tasks/<单号>.md；先记账后发，逐个在同一写事务里先按已提交状态写镜像、再现读凭证后领取，提交后才发（镜像写不成就不发），结果不明的过 60 秒、凭证仍有效才补发（中台同一来源同一 id 去重），send-pending 补发；freeze（作废当前代次、生成冻结码）/ revoke / complete 本机可调；一律先在凭证库提交、再持写锁按已提交状态写镜像，镜像写不成时变化照样生效、报 mirror_error（退出码 2）、派发停下；unfreeze 要常浩私聊「恢复 <单号> <冻结码>」且这次冻结已由推翻流程记过更正（record_resolution，0B-5 调用，本批没有命令行，恢复实际关闭），代次 +1，作废的代次永久失效；check 是主账号出口共用的判定，工人侧按镜像核只防误操作；凭证库 DigitalTwin/autoloop/grants.db（600，结构第 2 版，旧库拒用），工人只读镜像 /Users/Shared/dt-autoloop/grants/status.json（不含范围原文和消息号）；已知局限：和猛鬼兽同一 macOS 账号，挡不住蓄意作恶的模型，是否加独立账号 dt-gate 在 ADR-015 定（PR #199 审查 P0-01）；未安装，hermes 来源密钥 0B-2 建
    autoloop_config.py 中台配置（#195 批 0B-2，规格 §5、§6）：按名册生成本机中台 rules.json 里数字分身这一块、其余原样保留：来源 hermes（只有派单网关持钥）、Agent dt-claude / dt-codex（跑在 dt-worker，0C 起 program 挂工人侧启动脚本 /Users/Shared/dt-autoloop/bin/dt-worktree-<执行方>；hermes 钥匙在时也能改 Agent，不碰派单规则）、通道 feishu（answerers 只有常浩）和 secretary（只有「秘书」），defaultChannels 显式写成原有通道（原来没有通道时回退成飞书），别的规则点名 secretary 就拒装，派单规则 dispatch-<仓库>-<执行方> 只接 hermes 来源的那一种类型、按任务单号分事项、不开 emits，现在只给名册沙盒生成；plan 只读看差异；install、issue-source-key 共用跨进程锁 DigitalTwin/autoloop/config.lock；install --confirm --target <中台目录>（常浩说「装」才跑；--target 必须写明且和实际要改的目录一致）校验 → 备份到 evidence/autoloop-config/ → 原子替换 → 等中台载入（只读打开中台库读它正在用的配置，和文件逐字段比）→ 逐条暂停派单规则并回读，hermes 钥匙已在时派单规则不许新增、删减或修改；issue-source-key --confirm --target <中台目录> 在中台正在用的配置和文件逐字段一致、没有普通规则点名秘书、派单规则全部暂停时才签 hermes 来源钥匙写 600 文件（不打印）；派单规则一直暂停到 0C 启动脚本就绪、由常浩决定恢复；issue-channel-key feishu|secretary --confirm --target <中台目录>（0B-4）在在用配置一致、该通道回答人正好是预期的（feishu 只有常浩、secretary 只有「秘书」）时签通道钥匙写 DigitalTwin/autoloop/<通道>-channel.key（600）；配置和 hermes 钥匙已装（2026-11-17 常浩说「装」），通道钥匙未签
    autoloop_publish.py 受信发布最小版（#205 批 0C，方案 5.5 不变量 1、第 12 节第 6 条）：只在 dt-gate 账号里跑（按有效用户判），0D 之前整体关闭（REAL_PUBLISH 闸：推分支、开 PR 都不做，只有测试在进程内注入本地裸仓库和假 gh 才走一遍，远端和 gh 不从环境变量取）；取工人交接目录 /Users/dt-worker/dt-outbox/<单号>/ 里的交接包，只当资料（清单逐字段白名单、包只有本单分支一个引用、哈希对得上，只取 Git 对象、关钩子、不检出不运行）；按凭证库（autoloop_grants.check）动手前一起预检 push_branch、open_pr，推和开 PR 前再各现读一次，仓库从凭证取；清单里的代次不当证据，交接包要落到中台库里当前代次这张单正常结束的一轮（run 号只读核事项、规则、Agent、状态、排进来的时间、派单事件代次；被启动脚本挡回去的「停下了」轮次不算；还要有当前代次的正面证据：这一轮有当前代次的派单事件，或是回答卡片的一轮、顺着卡片追到提问那一轮最终落到当前代次的派单，一轮里派单和回答合在一起时每个回答照样追溯，只有定时复查的轮次 0C 不发布）；基线必须是 main 上的提交，不收合并提交，基线到头提交之间每个提交和最终差异碰到 merge_rules.json 的高风险路径就不发、交常浩；推送目标写死成 <执行方>/<单号>、不 force（dt-sandbox 规则集 11-18 起放行 claude/**、codex/**）、远端同名分支有别的提交不覆盖；同一分支有开着的 PR，目标本仓库 main、来源本仓库、分支和头提交都对才沿用，关掉或合并过的不再开；记录在 DigitalTwin/autoloop/publish.jsonl；publish --task 手动跑、scan 只读；按 ADR-015 归 dt-gate（控制面）写，状态只放自己的目录，用运行账号的 git / gh 登录；不接中台、未安装、不配真实凭据，0E 在 dt-gate 配机器账号令牌前不真跑；写前账本 0D、正式出口批 3
    autoloop_readonly.py 中台只读窗口（#195 批 0B-2，规格 §5，秘书和驿窗共用）：用本人密钥读 /v1/state，按白名单交出卡片（含 lane、rule、选项、状态）、轮次和其事件的来源 / 类型 / 对象、规则是否暂停、通道是否在线；不交出密钥、卡片带权限链接、消息号、回答原文、结案说明、about、会话、用量、回执摘要；show 打印一次，serve 给驿窗的本机只读接口（127.0.0.1:3352，Bearer 令牌在 DigitalTwin/autoloop/readonly.token，600），只认 GET /v1/snapshot，令牌文件内容不合格式就拒绝启动；只做展示，不能证明卡片属于哪一单（可信绑定 0B-4 由网关送达时补记，追溯不了的交常浩）；不装常驻服务（要装时单独问常浩）
    autoloop_feishu.py 飞书桥接（#195 批 0B-4 第 4a 批，规格 §6、§7）：用 feishu 通道钥匙向中台长轮询领卡片，以猛鬼兽机器人发到常浩私聊（每个选项一个按钮，按钮只带 {"autoloop": 令牌, "o": 选项号, "v": 这一版的版本号}，不放文字框），报送达，发卡后先落盘消息号和版本再报送达，中台收下后记「中台确认送达」并提交送达前排队的本人回答（崩溃后可按登记原样重报）；卡片状态变了改原消息（开着的卡改不了报失败、不另发；中台给的消息号和登记的不一致就不改）；DigitalTwin/autoloop/cards.db（600）登记令牌 ↔ 卡片号、送达消息号、飞书送达时间（按版本记，开着的卡改版送到后重算、中台确认送达时间清空，确认只记给报过的那一版；秘书白天等 30 分钟从这里起算）和中台确认送达时间，同一张卡只有一个令牌、幂等键只用卡片号；claims 表每张卡每方一行、claim_log 只追加；给 4c 秘书的契约在头注释（秘书获准前调 owner_holds，本人一侧不改秘书的行）；中台错误分「没发出去 / HTTP 码 / 没拿到回应」；发卡、改卡、报送达（含对账补报）都在桥接锁里做（单写入者，run 也走它）；中台每递一次卡都算一次尝试、9 次后永久放弃，所以领卡前先确认中台库能读，读不到这轮不领、私聊提醒常浩（1 小时最多 1 次）；领到后才读不到这张卡的版本、或本地登记的版本比中台还新（中台库被恢复成旧快照），就不发、向中台报 failed 写明原因（中台退避重试），报 failed 本身出错也照样提醒、请人工核查；中台已是更新的版本就不发不报，中台 1 分钟后递最新版；run 每轮只读扫中台里 feishu 通道已放弃送达的卡，每张私聊说一次请常浩手动处理（自动恢复要中台扩展点，同验收 12 向上游要）；报完送达放锁再提交排队的本人回答（#218 第 5 轮，常浩 11-19 选甲）；改飞书消息之前先核登记，不把消息改回旧版，登记的版本不回退（#218 第 3 轮 P2-01 及专项续审）；run 常驻（每轮先对账，装成服务要单独问常浩）、once、show；未安装
    autoloop_answer.py 常浩点中台卡片后的处理（0B-4 4a）：插件转来令牌、选项号、版本号、聊天号、点击人；点击人 = 常浩 open_id、聊天号 = 常浩私聊（本机没配就一律拒）不对就不回话只记日志；令牌登记过、登记私聊一致、选项号在范围、按钮带的版本号等于登记的当前版本（没带版本的旧样式按钮、旧版本按钮的迟到点击一律拒、不排队，登记时写锁里再核一次，#218 第 3 轮 P1-03）；每次点击一个不复用的尝试号，状态 queued（没消息号或中台还没确认送达，只排队）/ inflight / answered / settled / unknown，所有改状态都按尝试号 + 预期状态条件更新；发送只有 submit 一个入口、只能从 queued 原子领一次资格；只在能证明中台没受理（没发出去、400/401/403/404/409）时自动释放，5xx、超时、回读对不上、发送中途进程没了都记结果不明，从不重答、不自动放开，卡已有结果才自动结算，卡还开着要常浩明确同意（release --confirm）；reconcile 对账（桥接每轮也跑；送达报告被中台收下但本地没记上的，按登记原样重报）；排队的回答记着点击时的卡片版本，卡片改版后作废、请常浩在新版上重点（#218 第 2 轮 P1-03，提交到中台之间改版要中台版本核对才堵得住）；秘书已获准时本人照样提交、以中台先受理的为准；已知局限：同一 macOS 账号的进程能伪造参数、也能读通道钥匙（PR #202 审查 P1-01；常浩 11-17 选甲：钥匙不签、不装，隔离在 ADR-015 定，方向是独立账号 dt-gate + 单独飞书机器人直接收点击）；未安装
    autoloop_exits.py  出口账本（#195 批 0D 恢复门，设计 docs/恢复门-0D-设计.md）：推分支、开 PR、发飞书、合并先记账（executing，带进程号和开机时间）、再动手、再记结果；幂等键 <动作>:<单号或仓库>:<对象>:<版本>，做成的直接回原结果不重做；只有 do() 抛 Definite 才算确定没做成、可以重做，其他异常一律结果不明；入口和对账都扫「在做而进程已不在」的改成结果不明（强杀）；同一冻结范围（task: / repo: / card:）有在做或不明的就拒绝新动作，冻结范围再有动作先只读对这个范围的账；带任务的动作在凭证库写锁里现读凭证并落盘 executing（和冻结、撤销串行，0C 审查 P1-07）；对账只读回读 git ls-remote / gh / 私聊消息，连不上保持不明、对不上记核不清（settle --confirm 由常浩定）；库在运行账号的 DigitalTwin/autoloop/exits.db（700/600），0E-2 搬进 dt-gate；未安装
    autoloop_recovery.py 恢复闸（#195 批 0D）：开机、睡醒、中台重启或换版本、中台连不上、出现结果不明之后，受保护副作用的入口（ensure_open）先按顺序对账——本机账本 → 凭证与工人镜像 → 中台（/healthz 版本等于锁定版本 hub-pin.json）→ 外部回读（出口账本、本人点击的不明）→ 结算开闸；仍冻结的范围继续拒绝；不需要任何钥匙；status 只读、reconcile 手动对账、pin --commit --confirm 锁定中台版本（常浩说了才跑）；未安装
    button_probe.py    按钮实测探针（#195 批 0B-3，规格 §5「卡片回答的信任」）：send 以猛鬼兽机器人往常浩私聊发一张测试卡（按钮只带 {"probe": 随机令牌}），回读核对后登记令牌 ↔ 消息号（--unregistered 发不登记的卡，测令牌对不上）；show 只读，把 merge-buttons 插件记下的点击和登记的卡对照，说清谁点的（是不是常浩）、哪个聊天、哪张卡、第几次点；点击只记录不触发动作；记录在 evidence/button-probe/（700 / 600）
    secretary.py       秘书开关与代批清单签字（#195 批 0B-4b，规格 §8）：和 custody.py 共用一套底座（状态文件、原子写、锁、初始化标记、飞书回读核验、防重放、签字语义），各用各的文件和口令：DigitalTwin/secretary.json、DigitalTwin/secretary/policy.md（按 docs/代批清单-模板.md 填；四类开关、项目只认名册里沙盒或 write_allowed 的活跃项目、四项额度（②重派必须每张任务 1 次）、合并白名单、白天先等只能 30 分钟、排空截止）；四态 off / on / draining / frozen：签了清单不算开，常浩私聊「开秘书」才开（常浩 11-18 定），关是任何来源都可以（常浩私聊「关秘书」由 Hermes 调 off）→ 排空，排空完成要本脚本读秘书账本证明，到截止没收完自动冻结，冻结只有私聊「解除冻结」能解；签字口令「签代批清单 v<版本> <SHA-256 前 12 位>」；status --json 带准入判定 admission；未安装、未签、不生效
    secretary_ledger.py 秘书账本（#195 批 0B-4b，规格 §2、§8）：twin-state v5 的 secretary_decisions / secretary_quota / secretary_sessions，和托管账本共用 custody_ledger 的通用函数（按「账本用途」区分表、事件前缀、原文目录 DigitalTwin/secretary/）；五种动作 ask（①）/ redispatch（②）/ reject（③）/ merge（④）/ handoff（交常浩，不占额度、只记当前会话，写明九条原因之一给统计）；四类额度在同一个写事务里原子预占：每天代批总数、每张任务代批次数（跨会话累计）、每天合并数、②每张任务 1 次；准入现读 secretary 的 admission、清单里这一类开着、项目在清单里、合并仓库在白名单；stats 按天统计；只读命令 show / list / drain-check / stats；未安装
    secretary_gate.py  秘书代批控制面（#195 批 0B-4c 第 1 小批，规格 §5–§7、ADR-015：以后整体放进 dt-gate，路径全走环境变量）：收秘书侧交来的提议（只信选项号和白话，卡片、轮次、事件从中台库、任务和代次从凭证库、开关和清单从 secretary、本人状态从 cards.db 重取）；前闸全确定性（准入、清单类别、①的模型运行证据（模型名、是否回退不收提议自报：按会话号只读 Hermes 自己的运行记录 state.db，核只有一条输入、正常结束、没调工具、全部模型调用都是主模型、输入第一行是这张卡这一版的标记、最后输出只许有约定字段且和提议逐项严格相同（含 error、类型也要一样），#218 两轮审查）、卡片 → 轮次 → 网关派单事件 → 任务单号和代次的绑定（复用 autoloop_publish.bind_run）、卡片开着且版本一致、诱导词、白天从这一版在飞书送达起满 30 分钟（改版重算，托管开着不等，没满只返回「还不能办」）、秘书通道确认的是这一版、任务有结果不明的秘书决定），不过记「交常浩」，received 用可信侧取的卡片快照；过了在账本预占（同一张卡同一版本最多一个可送达决定，twin-state v6 部分唯一索引），再持 cards.db 写锁做后闸和获准（先 cards.db 后账本；本人占着就让出），用 secretary 通道钥匙送达（自己领卡、落盘 ref、报送达、回答 by=秘书），回读核实；没发出去或 400/401/403/404/409 记没做成、其余记结果不明、从不重答，reconcile 按回读证据结算；②③从前闸到续接发出全程持这张任务的受信发布锁（和 publish 同一把，#218 审查 P1-03）；①卡在送达中超过 2 分钟、回读给不出结论的一律记对账不清（额度不退、冻结这张任务、交常浩），不再拿 cards.db 推断没发；任务有卡住超过 2 分钟的秘书决定也算冻结，同任务第二张卡不能再获准（#218 第 3 轮 P1-04 / P1-05，常浩 11-19 选甲）；中台说已决定却缺回答人或选项的也记对账不清（同 autoloop_answer.judge）；提议的 schema 要严格整数 1；**①的送达开关 REAL_ASK 关着**：中台收回答不核版本，获准到送达之间改版会让旧答案落到新版本上，要中台按版本核对（规格第 9 节验收 12，先向上游要扩展点）后才打开，关着时①一律交常浩（常浩 11-19 选甲，不破零补丁）；②重派走 autoloop_grants.resume_dispatch；③发布记录代次要是当前的、留言前再读一次 PR，用机器账号留言再派返修，留言开关 REAL_COMMENT 关着（0E-4 接凭据），现有审查记录绑不了 PR 和受审提交，③一律交常浩；命令 submit / reconcile / lease / show；未安装、secretary 通道钥匙未签
    aihot_hot.py       AI HOT 热点榜推送（#163）：Hermes 无模型定时任务每个工作日 09:45（周一到周五，不跟节假日调休）调用，取 aihot.news 热点榜 TOP10 和每条事件摘要，按常浩 11-11 定稿的「名次栏」样式排成卡片（不要表头，横幅 assets/aihot-banner.png 就是标题，同 GitHub 周榜卡片；标题只链到 aihot.news，不放来源 / 参与者 / 时间），以猛鬼兽机器人发常浩私聊并回读；同一天只发一次；成功不输出，取不到数据或没发成输出一句原因（退出码 1）；记录在 DigitalTwin/aihot/（runs.jsonl、<日期>.card.json、state.json）
    groupdigest.py     AI 前沿群每日学习报告（#192，PRD D25）：collect 以常浩的用户身份读外部群「Codex重置许愿群」前一天全部消息，顺着外链抓源头（先直接抓，抓不到经 agent-reach read，被限流隔一会再试），读名册各项目阶段目标、Vault 个人上下文和全局规则标题；build 经 hermes chat 单次让猛鬼兽写卡片要素和完整报告（提示词 prompts/groupdigest_report.md），模型只写材料编号 M/S，脚本按编号填链接，卡片按常浩 11-16 定稿、11-17 调整（二 · 表格对照改：顶上四个数字块，昨日信息梳理只讲事、不放原话，每条信息下附「原文与译文 · 展开」折叠面板和原地址，思考采纳建议、今日行动清单各一张表，最后是派单提问（浅红底、红色问号），底部完整报告按钮；11-17 定只写值得学的内容，纯资讯不写），完整报告五节；核对编号、网址、板块、说成已做，行动的「谁做」只认 claude / you、项目只认名册，不过带着问题重写一次，再不过出事实版；send 建飞书云文档、以猛鬼兽机器人发常浩私聊并回读，同一天只发一次；todos / mark 给 groupdigest-act 技能用（看当天行动清单、记派单和不做，同一条同一项目不派两次）；产物在 DigitalTwin/groupdigest/；入口 groupdigest_build_job.py（10:00）、groupdigest_send_job.py（10:45，发送时写稿没完会再等最多 15 分钟）；成功不输出，没发好输出一句原因
    feishu_home.py     私聊 chat_id（#165）：先看环境变量 FEISHU_HOME_CHANNEL，没有再从 ~/.hermes/.env 只读这一项；无模型定时任务的环境里没有这个变量，往私聊发消息的脚本（aihot_hot、tidings_weekly、proposal_digest、merge_act、groupdigest）都用它
    tidings_weekly.py  Tidings 周报推送（#55、#58）：Hermes 无模型定时任务每 15 分钟调用，按 github-center 设置页（data/settings.json 的 weekly：开关、星期、时间，默认周一 19:30，不早于每日更新时间，github-center #83）到点才发，把 github-center 写好的周报卡片插上横幅（assets/tidings-banner.png）发到常浩私聊；成功不输出
    skill_health.py    skill 月度体检推送（#151）：Hermes 无模型定时任务每月 1 号 10:30 调用，在 my-skills 下跑 node scripts/monthly-health.mjs --days 30，标题改粗体、表格改成逐条列表、末尾加「抽查 <名字>」提示，stdout 发到常浩私聊；失败退出码 1，由 Hermes 发失败通知；只读
    multica_night.py   Multica 夜跑主控（#92，DT-ORC M5 / 5.3a）：Hermes 无模型定时任务每 5 分钟经 multica_night_tick.py 调 tick（approve 记下的开跑时刻到了就先 start）；按计划文件在 DT 沙盒建父单和分 stage 子单，子单交付（in_review）后推进下一 stage，边跑边导 run-log 和消息到 DigitalTwin/runs/<夜跑>/，到上限按 5.3a 停机（禁用 Wakeup、暂停 Autopilot、取消非终态 run、回读），回读不过就冻结执行器容器；开跑前核对名册 sandbox: true；开跑和每次放 stage 前跑执行器身份闸（#174：信任基准在名册 dt-sandbox 的 executor_gate，保护基线在 hermes/baselines/，两者只读已合并的 origin/main；不通过就不放，tick 里不通过停机并冻结执行器；冻结命令只按名册生成，名册读不到就不执行任何命令、告警请人手动停）；不合并、不送审；计划样例 hermes/samples/multica-night-2026-11-08.json，执行单 docs/DT-ORC/migration/2026-11-08-M5-首夜执行单.md
    merge_officer.py   影子合并官（#61，只读）：按 merge_rules.json（带版本号）对名册活跃仓库的 open PR 给「建议合并 / 退回 / 问常浩」，读真实结局算一致率，记在 ~/Library/Application Support/DigitalTwin/shadow/agreement.csv（PR 状态快照 facts.jsonl）；replay 回放改规则前后的一致率；只报告是否达到同类 20 次、≥90%，不合并、不放权
    merge_act.py       飞书一句话合并（#67）：merge_officer.py 给 merge_rules.json 的 act.repos 里的仓库每个新「PR × 版本」发卡片；常浩点卡片上的「合并」按钮或引用卡片回「合并」，merge-officer 技能调本脚本核对版本 / 冲突 / 落后 / 本机主分支 / 风险档（L3 不走飞书），要求这个版本的 CI 全部通过（主账号不执行 PR 里的代码，没 CI 的仓库不走飞书合并，#190），带 --match-head-commit 合并；点「退回」或回「退回 原因」就在 PR 留言。决定记在 agreement.csv 的 owner 列；托管合并（#136）只能调 run_custody_merge，凭据在函数里从本机钥匙串取合并执行器专用令牌（#132）；托管合并被推翻后 run_custody_revert 用同一凭据开 revert PR（#133，只开不合，本机 git 配置隔离）
    proposal_digest.py 每周提案收口（#61）：每周一把会话复盘、牛马之家每周整理的待决提案按编号发到常浩私聊（LESSONS 升级候选默认不列，DT_LESSONS_MIN_PROJECTS 打开）；另有「再犯与待提升」（#216，常浩 11-18 定）：调 agent-kit 主目录的 `node tools/weekly-review.mjs --json`（agent-kit#80，认条目只在那边做），只列写了「再犯：G-xx」的全局坑和〔提议〕提升的项目坑，每期最多 7 条，结论记在 DigitalTwin/shadow/guards-decisions.md，取不到时卡片明写原因；每条附 Hermes 的推荐（带模型定时任务经 proposal_digest_draft.py 取草稿、写推荐后 send --recommend），常浩回「按推荐」或逐条回复后由 proposal-digest 技能调用 apply 写回各来源的结论；期号状态在 DigitalTwin/proposals/digest.json
  skills/<名>/SKILL.md Hermes 技能（源文件）；安装在 ~/.hermes/skills/autonomous-ai-agents/<名>/（亚古兽的技能见 self_check 的 AGUMON_SKILLS，装在 ~/.hermes/profiles/agumon/skills/productivity/<名>/）
    workshop/          车间派单（F5）
    sales-sidebar/     亚古兽：销售 AI 侧边栏的需求反馈、进度卡片、功能同步通知和项目问答（2026-11-19）；项目档案在亚古兽工作目录（不进仓库）；两个定时任务挂这个技能
    progress/          项目进展现查（F6）
    vault/             关于常浩的判断类问题先查 Vault（F3，用 MCP vault-read）
    merge-officer/     常浩点 PR 卡片的「合并 / 退回」按钮或引用卡片回复时调 merge_act.py（#67、#78）
    proposal-digest/   记下常浩对每周待决提案的选择（#61，调 proposal_digest.py apply）
    groupdigest-act/   群学习报告回复即派单（#192）：常浩回「都派」「派 1 3，2 不做」时，把选中的行动写成任务说明，经车间派单交给 Claude（回复即这几条的 D 档逐项授权，同 D23），调 groupdigest.py todos / mark 记账
  plugins/merge-buttons/ 卡片按钮（#78、#133、#195）：注册 /card，把 PR 卡片的「合并 / 退回」点击转给 merge_act.py button，托管汇报卡的「推翻 / 合并 revert PR」连同卡片令牌（--card）转给 custody_report.py button；中台卡片 {"autoloop": 令牌, "o": 选项号, "v": 版本号}（多带字段不认；没带 v 的旧样式按钮照样转、不带 --version，由脚本拒绝并在私聊说明，#218 第 3 轮 P1-03）连同版本号（--version）、聊天号、点击人（HERMES_SESSION_USER_ID）转给 autoloop_answer.py（#195 0B-4）；按钮实测探针 {"probe": 令牌} 不起脚本，只把网关给的点击人、聊天、消息号的样子（不落回调 token 原文）记进 evidence/button-probe/clicks.jsonl（#195 0B-3）（都不经过模型）；安装在 ~/.hermes/plugins/，config.yaml 的 plugins.enabled 里启用
  plugins/workdesk-gate/ 亚古兽工具拦截（#142）：飞书里发起人不是常浩（open_id 或企业 user_id `e9cgcf77`）时只放行安全工具（提问、读文档、文档评论、网页、技能查看、生图、语音、待办），本机命令行和定时任务不受限；牛马之家只在常浩本人私聊可用并写入真实提问人；安装在 ~/.hermes/profiles/agumon/plugins/，测试 hermes/scripts/tests/test_workdesk_gate_plugin.py
  prompts/             定时任务等用的提示词和项目词表；安装在 ~/.hermes/prompts/
  workspace/HERMES.md  猛鬼兽工作规程（源文件）；安装在 ~/.hermes/workspace/HERMES.md，只在工作目录是 workspace 时加载；第 5 节是个人项目日报规则（猛鬼兽只报个人项目）
  samples/             样稿
  baselines/           身份闸的 GitHub 保护基线（#174）：dt-sandbox 规则集和 main 分支保护，名册 executor_gate 指向它；改保护规则先改 GitHub 再改这里
docs/LESSONS.md        本项目踩过的坑（L-xx）；跨项目的坑在 agent-kit/LESSONS.md（G-xx）
docs/DECIDED.md        已定内容登记表（全局规则第 15 节）：常浩定稿过的东西，改之前先单独确认；界面类附基线截图路径
docs/design/           定稿的界面源文件（工作台 workbench.html：首页和汇报页），示例数据
docs/agents/           Matt Pocock 技能读的约定：issue 看板、分流标签、领域文档
docs/DT-ORC/           Hermes × ORCA 支线文档；ARCHITECTURE.md 是唯一工程架构基线，history/ 存被取代的旧方案
docs/三报体系-V2.1.md   主动运营与三报（早报 / 晚报 / 周报）的产品与实施基线（PRD D20）；评审过程在 docs/reviews/
docs/常设授权清单.md     Hermes 的常设授权四档（PRD D21）：动作前查，日报按它写依据；只有常浩能改
docs/个人项目日报-V2-设计.md  个人项目代理管理日报规格与样稿（PRD D22，#108）：每天 08:00 私聊简报 + 本地完整日报，只覆盖个人项目
docs/夜间行动提案-设计.md      夜间行动提案（PRD D23）：每天 22:30 私聊提出今晚可替常浩推进的事，常浩回复即当晚授权，夜里只做到开 PR
docs/代批清单-模板.md          秘书代批清单模板（#195 0B-4b，仓库里的永不生效）；签好的放 DigitalTwin/secretary/policy.md
docs/托管模式-设计.md          托管模式（PRD D24，ADR-014 及修订 1）：常浩不在场时猛鬼兽以常浩身份回答已批准夜跑里 blocked 子单、托管合并，三道闸、记账、事后汇报可推翻；策略模板 docs/托管策略-模板.md（仓库里的永不生效）
orca/
  chatgpt_relay.py     在 ORCA 内嵌浏览器的 ChatGPT 聊天里送审、收回执、REWORK 时交回 ORCA 终端里的 Claude（#35）；测试 `python3 -B -m unittest discover -s orca/tests`
ops/
  health_check.py      每日巡检（launchd 09:45）：网关、飞书连接、24 小时内异常退出、回退模型、辅助任务失败（猛鬼兽、亚古兽各查一遍，#147）、定时任务、工人账号的过滤代理和门卫规则（#190），另提醒飞书 lark-* 技能源头比机器人副本新；有异常才发飞书；安装在 ~/Library/Application Support/DigitalTwin/bin/
  self_check.py        工程自检（#159 起不单独发：每天 10:00 个人项目日报发送时调用，结果并进日报卡片，正常只在页脚写一句，有问题并进「风险与阻塞」表的一行、原文放折叠面板，#226）：每天比对仓库 main 和安装副本，并跑 github-center 的 bin/health.js（Github·Tidings 有问题才有输出）、custody.py status（托管没关、策略失效或快到期才有输出，#131）、ai_access.py status（车间默认执行方不能用、Hermes 主模型走的本机 Claude 没登录才有输出，#180）；每月 1 号（或 --monthly）再查沙盒和仓库；安装时与 check_repos.py 同放 ~/.hermes/scripts/
  cn.digital-twin.health.plist  巡检 launchd 配置的副本
  dt_worktree.py       工人侧启动脚本（#205 批 0C）：装在 /Users/Shared/dt-autoloop/bin/（归主账号，工人只能运行），以 dt-worktree-claude / dt-worktree-codex 的名字挂在中台 Agent dt-claude / dt-codex 的 program 上（引擎零补丁），用 /usr/bin/python3；每轮先准入：事件都来自 hermes 来源、同一任务单号，按工人只读的凭证镜像和任务单核状态、到期、改文件动作、代次（回答 / 复查沿用上次派单的代次）、执行方 / 仓库 / 规则、五要素哈希，续接时还要等于第一轮钉下的范围，每单跨轮累计 2 小时；不过就不启动 CLI，交一条「停下了」通知（中台按本轮结束，不重派）；临时问题写「没跑成」，中台判 failed、5 分钟后重派；CLI 在独立进程组里跑，取消时转发信号、7 秒内整组强杀，超时同样整组停，CLI 退出后清掉剩下的后代再提交；接单程序的取消覆盖整轮（提交后、换交接包和清单前各看一次），掐着上限自己退出的也按超时算；工作目录固定 ~/dt-work/<仓库>/wt/<单号>、分支 <执行方>/<单号>，续接回同一目录；提示词末尾补一段「只改文件、不推不开 PR 不切分支」；CLI 输出原样交给中台；退出后提交改动；只有正常结束（不是取消、不是超时）、后代清干净、再核一次凭证有效，才把交接包写进 ~/dt-outbox/<单号>/；出了问题没交接的一轮（准入不过、取消含落在换清单之后、超时、进程没清干净、交接前凭证失效、提交或交接出错）以「停下了」给中台结论（取消不带通知卡），受信发布据此不认这一轮；正常结束只是没改动的一轮不补「停下了」（会盖掉 CLI 的回执），记成剩余风险，0D 结果账本补；换清单是提交点，最后检查和换清单之间屏蔽取消信号；不推、不碰 GitHub 写凭据；未安装
  autoloop_isolation_check.py  0A 隔离门反向测试（#190）：在工人账号 dt-worker 里运行，逐项尝试越权（读主账号文件、改 rules.json、用接单密钥看全局 / 回答卡片 / 触发规则 / 冒充来源、GitHub 凭据、sudo、连主账号其他本机服务、连加速器代理、经过滤代理绕回本机、往主账号会执行的目录里写），每项都应被挡住；读不到监听端口判出错不判通过；结果写 /Users/Shared/dt-autoloop/results/；共享副本在 /Users/Shared/dt-autoloop/（连同 dt-worker 安装脚本 setup-dt-worker.sh（源文件 ops/dt_worker_setup.sh，可重复跑）、门卫脚本 dt_worker_pf.sh、只读引擎副本）
  dt_worker_egress.py  工人账号的过滤代理（#190，常浩 11-17 同意装，D 档）：dt-worker 唯一的上网出口 127.0.0.1:8090，只接 CONNECT 到 443，目标解析出非公网地址就拒绝，放行的转给加速器代理 8086；普通 HTTP 一律拒绝；以主账号身份常驻（launchd cn.digital-twin.egress，配置副本 cn.digital-twin.egress.plist），安装在 ~/Library/Application Support/DigitalTwin/bin/；日志 evidence/egress/；剩余风险：DNS 重绑定且加速器直连时挡不住
  dt_worker_pf.sh      工人账号的门卫规则（#190）：pf 规则挂在 com.apple/dt-worker 锚点，dt-worker 只准连本机 3350（中台）和 8090（过滤代理），本机其他端口、局域网一律拒绝；常浩用 `sudo bash /Users/Shared/dt-autoloop/dt_worker_pf.sh install` 装，开机和每 5 分钟由 LaunchDaemon cn.digital-twin.pf-dt-worker 重新加载并写状态文件 /Library/Application Support/DigitalTwin/pf-dt-worker.status；health_check.py 每天查过滤代理拦不拦本机、门卫状态文件新不新
  autoloop_drill.py    中台升级、回滚、搬家演练（#195 批 0D）：从本机中台程序只读克隆两个版本到临时目录，临时中台（随机端口、不装服务；拒绝用 ~/.agent-autoloop 和 3350），按「快照 → 停旧 → 起新 → 核对挂起的卡片和轮次 → 通过留新版 / 不通过用旧版程序恢复快照」演练，另演练搬到另一个目录；run [--out 证据目录]；测试 hermes/scripts/tests/test_autoloop_drill.py（同时演练断网、超时、强杀）
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
| `memdaily` | 记忆日报（亚古兽，PRD D20 11-11 修订：每天 07:30 写前一天，接着写《常浩-记忆日报汇总》六模块；11-17 起按月一份新文档） | Hermes 亚古兽（openai-codex gpt-6.1-sol，思考 xhigh），仅支持 Hermes；核对不过就不写、报原因，不换家 | Hermes 派单（`hermes -p agumon chat` 单次） | 常浩的飞书私聊、群、会议与妙记、日程、任务（亚古兽应用，用户身份）；前一篇行动 & 阻塞 | 当月《常浩-记忆日报汇总 · YYYY-MM》顶部（旧汇总文档已归档）；DigitalTwin/memdaily/（含 docs.json 文档登记）；亚古兽私聊推送 |
| `sidebar` | 销售 AI 侧边栏固定汇报（亚古兽，11-19 常浩定：需求反馈每天 18:00、进度卡片每周三 18:30 私发确认；功能同步通知说了才出） | Hermes 亚古兽，仅支持 Hermes；核对不过改两轮，再不过报原因，不换家 | Hermes 派单（带模型的定时任务） | 测试反馈群消息和截图、多维表格（功能清单、推进计划、使用反馈）、常浩私聊（亚古兽应用，用户身份）；上一期存档 | 亚古兽私聊常浩；常浩回「发群」后以他本人身份发测试反馈群；DigitalTwin/sidebar/ |
| `groupdigest` | AI 前沿群每日学习报告（#192，PRD D25：10:00 取数写稿、10:45 发私聊，每天发） | Hermes（主模型 Opus 5.5，思考 high），仅支持 Hermes；核对不过出脚本事实版，不换家 | Hermes 派单（`hermes chat` 单次） | 外部群「Codex重置许愿群」前一天全部消息（猛鬼兽应用，用户身份）、外链源头正文、名册阶段目标、Vault 个人上下文（受限，用而不露） | DigitalTwin/groupdigest/；飞书完整报告云文档；常浩私聊卡片；常浩回「都派 / 派 1 3」后经车间派单交给 Claude（groupdigest-act 技能，点名 Claude，执行方和留痕按 `workshop`） |
| `secretary` | 秘书代批（#195 批 0B；0B-4 才有判断和送达，现在只有中台 secretary 通道和只读窗口，不生效） | Hermes，仅支持 Hermes（hermes chat 单次、最小工具集，模型不持通道钥匙） | Hermes 派单 | 中台只读窗口的卡片快照、任务凭证 | 0B-4 起：中台 secretary 通道回答（by=秘书）、托管账本；现在不写 |
| `proposal-digest` | 每周待决提案的推荐 | Hermes，仅支持 Hermes | Hermes 派单 | 会话复盘提案、牛马之家每周整理、agent-kit 每周汇总的再犯与待提升 | 推荐文件、飞书卡片 |
| `relay` | ORCA ChatGPT 送审 | ChatGPT 网页，仅支持 ChatGPT 网页（送审就是要另一家独立审） | 网页桥 | 送审包 | 回执、DigitalTwin/relay/；返工时交给 ORCA 终端里的 Claude |

不调用 AI：合并官（merge_officer、merge_act、merge-buttons 插件）、托管账本与汇报卡（custody、custody_ledger、custody_report、custody_impact）、Tidings 周报推送（只转发 github-center 的卡片）、AI HOT 热点榜推送（概括按句截取、话题标签按关键词，不经模型）、三个自检脚本、skill 月度体检推送、任务凭证与中台配置（autoloop_grants、autoloop_config、autoloop_readonly）、受信发布（autoloop_publish）、出口账本与恢复闸（autoloop_exits、autoloop_recovery、ops/autoloop_drill）、按钮实测探针（button_probe）、飞书桥接（autoloop_feishu、autoloop_answer）、秘书开关与账本（secretary、secretary_ledger）、秘书代批控制面（secretary_gate）、晨报取数（挂起）。工人侧启动脚本 dt_worktree 自己不调 AI，只启动中台派给 dt-worker 的 Claude / Codex；这项派活功能（ai_access.json 的 autoloop-dispatch）在批 2 派活上线时登记。

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
- 碰中台相关脚本（autoloop_*）的测试模块一律挂 `hermes/scripts/tests/prod_guard.py`：整个模块指到临时目录，前后核对正式中台和正式状态目录（2026-11-17、11-18 两次漏隔离的教训）。
- 被 Hermes 派来干活时：只在当前分支干活，不切主分支、不合并、不 push。
