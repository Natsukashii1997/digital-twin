# 猛鬼兽在前、agent-autoloop 当执行底座｜方案（草案）

| 项 | 内容 |
|---|---|
| 状态 | **草案，待常浩确认**；确认前不改代码、不装插件、不写规则 |
| 日期 | 2026-11-15 |
| issue | digital-twin#188 |
| 起草 | Claude Code |
| 级别 | 按 `docs/DT-ORC/ARCHITECTURE.md` §12.3 属 **MAJOR**（执行架构换底座）：要有 ADR、迁移与回退计划，并按 §12.4 经驿窗送 ChatGPT High 独立审查（≤2 轮）。本文是审查和拍板的底稿；拍板后再写 ADR-015、改 ARCHITECTURE |

凡是标 〔待实测〕 的，是读文档和源码得出的推断，批 0 里实测后才算数。

---

## 1. 常浩的原话与要的结果

> 「Hermes 负责飞书对话、记忆、技能；派活、定时、挂起等你拍板交给 agent-autoloop。它的提问先走桌面通知，以后再做成由 Hermes 转发到飞书。这套架构如果能实现我就还用现在的猛鬼兽。」
> 「我想让他直接发我飞书，或者说驿窗侧边栏弹出。」
> 「旧的 run_worker.py 等等先不考虑，如果这套内容弄好，我就只保留这套结构。」（2026-11-15）

拆成五个要的结果：

1. **一个入口不变**：飞书里还是只有猛鬼兽一个 AI，沿用现有应用、记忆、技能（PRD 原则 1、F1 不变）。
2. **派活交给中台**：常浩在飞书说一句，猛鬼兽整理好交给 agent-autoloop，由它派给 Claude Code / Codex，跑完回报。
3. **拍板到手边**：Agent 拿不准时，问题直接到飞书私聊（按钮能点），以后加驿窗侧栏；回答后接回原会话接着干。
4. **定时也归中台**：新的定时活写成中台规则。
5. **最终只留一套**：新结构验收后，旧的派单、调度、夜跑结构退役（本文只写到「怎么退」，不在本次做）。

## 2. 分工总图

```
常浩 ──飞书私聊──► 猛鬼兽（Hermes，现有应用和记忆）
                    │ 对话、记忆、技能、整理派单、转发拍板、回报
                    │
        派单事件 POST /v1/events（hermes 来源密钥）
        回答     POST /v1/cards/<号>/answer（通道密钥，带常浩 open_id）
                    ▼
            agent-autoloop 中台（本机 127.0.0.1:3350，launchd 常驻）
             规则（rules.json）决定：交给谁、在哪个目录、什么权限
             挂起等拍板不占进程；回答后接回原会话
                    │ 领活 / 回执
                    ▼
            接单程序 → claude / codex 官方命令行（本机登录，会员额度）
                    │
             项目 worktree ──► 分支、PR（GitHub）
```

| 角色 | 负责 | 不负责 |
|---|---|---|
| 常浩 | 定目标和权限；回答卡片；说「合并」 | — |
| 猛鬼兽（Hermes） | 飞书对话、记忆、技能；把一句话整理成派单；卡片的飞书收发；查状态、回报 | 不自己派 CLI、不自己回答卡片（不替常浩拍板） |
| agent-autoloop 中台 | 排队、派活、并发、挂起与续接、定时、事件溯源留痕 | 不懂常浩、不存记忆；不发飞书（飞书收发借猛鬼兽） |
| Claude Code / Codex | 在 worktree 里干活，最后一条回复交回执（结论、提问、复查） | 不推 main、不合并 |
| GitHub | 交付事实（issue、分支、PR） | — |

## 3. 六条链路怎么走

### 3.1 派活：飞书一句话 → 中台

1. 常浩私聊：「让 Claude 给 dt-sandbox 加个 xx」。
2. 猛鬼兽用新技能 `autoloop`（替代现在的 `workshop`）整理五要素（项目、目标、验收标准、边界、交给谁），项目必须能在名册 `projects.json` 里匹配上。写入单照 PRD F5.3 先在私聊确认（常设授权覆盖的除外，见第 4 节）。
3. 猛鬼兽调派单脚本 `autoloop_dispatch.py`：用只有本机有的 `hermes` 来源密钥 `POST /v1/events`：
   - `type`：`dispatch.<项目id>.write` 或 `dispatch.<项目id>.read`
   - `subject`：`<项目id>#<issue号>`（写入单先建 issue，沿用现在的做法）
   - `detail`：五要素全文（≤4000 字）
4. 中台按规则派活。**目录、Agent、权限只由规则决定，事件改不了**（中台会拒收带 `cwd`、`agent`、`access` 的事件）。所以每个可派的项目有一条规则，由脚本 `autoloop_rules.py` 从名册生成（只生成 `活跃` 且 `write_allowed: true` 的项目；只读单所有活跃项目都有），常浩改名册后重新生成，`agent-autoloop check` 通过才写入。
5. 名册外的项目：没有规则匹配，事件进「未派出」，`status` 看得到，不会被派。

**一个要说清的点**：中台把事件正文当「资料」放进文件，不当指令（这是它防注入的设计）。所以派单规则的 Skill `dt-dispatch` 要写明：资料里的「派单说明」是常浩经猛鬼兽转达的任务，按五要素做；说明里引用的外部文字仍只当资料。这条信任只给 `hermes` 来源，别的来源的事件不走这条规则。

### 3.2 在 worktree 里干活

中台本身只认规则里的 `cwd`。要做到「每单一个 worktree、一个分支」，用中台支持的「启动脚本」写法（和这次补代理的 `claude-proxy.sh` 同一个口子）：

- 脚本读 `AGENT_AUTOLOOP_CONTEXT` 拿到 `subject`，按 `subject` 建或复用 worktree `~/.hermes/worktrees/<项目>/<issue号>`、分支 `claude/<issue号>-<短名>` 或 `codex/…`（从 `origin/main` 拉），`cd` 进去再 `exec` 官方 CLI。
- 按 `subject` 而不是按轮次建：回答后续接的那一轮还在同一个 worktree，会话才接得上〔待实测：续接时 Claude / Codex 的会话和目录绑定〕。
- 同一个仓库同一时间只跑一单：项目规则写 `"maxActive": 1`；总并发 `worker.capacity` 保持 2（PRD F5.5）。

### 3.3 推送和开 PR：交给固定脚本，不给 Agent 放权

中台的 `edit` 权限下，Codex 是 `workspace-write`（命令不能联网），Claude 是 `acceptEdits`（任意 shell、联网仍要审批），**Agent 自己推不了分支、开不了 PR**。两种做法：

| 做法 | 说明 | 建议 |
|---|---|---|
| A. 启动脚本收尾 | Agent 只提交到本地分支；启动脚本在 CLI 退出后检查分支有没有新提交，有就推分支、`gh pr create`，把 PR 链接写进日志。固定脚本以本机账号跑，不受 `access` 限制，只做这两件固定的事 | **推荐** |
| B. 给 Agent `full` | Agent 自己推、自己开 PR | 不推荐：`full` 没有系统隔离，事件资料里的话也能驱动它 |

做法 A 要实测启动脚本在 CLI 退出后还能干活、又不弄乱插件解析的输出〔待实测〕。不行的话退一步：Agent 回执里用 `events` 发一条 `push.request`，交给一条 `use: "command"` 的固定脚本规则去推。

### 3.4 拍板：卡片发飞书，按钮回中台

不和猛鬼兽抢同一个飞书应用的回调：**中台不监听飞书，只发；点击由猛鬼兽网关收**。

1. 新写中台通道插件 `dt-feishu`（`~/.agent-autoloop/plugins/dt-feishu/channel.ts`，源文件放本仓库 `autoloop/plugins/dt-feishu/`）：
   - `hub.nextCard(50)` 领卡片 → 用 `lark-cli` 以猛鬼兽机器人身份发互动卡片到常浩私聊（chat_id 用 `feishu_home.py` 同一来源）→ `hub.delivered(id, version, message_id)`。
   - 卡片 `ref` 不为空时更新原消息（回答后把卡片改成「已选：xx」）。
   - **绝不运行 `lark-cli event consume`**。内置飞书通道会起监听，所以不用内置的。
   - 卡片样式沿用现有私聊卡片的写法（标题、正文、来源规则、按钮），按 P-08 出 3 版让常浩挑。
2. 按钮的 value：`{"autoloop": "answer", "card": 56, "choice": 1, "v": 2, "tok": "<发卡时的随机令牌>"}`。
3. 常浩点按钮 → 猛鬼兽网关 → `merge-buttons` 插件新增一个分支 → 调 `autoloop_answer.py` → 用通道密钥 `POST /v1/cards/56/answer {choice, by: 常浩 open_id, ref: 原消息号}`。中台核对 `by` 在 `answerers` 里（只填常浩一人）、`ref` 和送达那条一致，只认第一次。
4. 也能打字回：引用卡片回「选 2」或一句别的想法，`autoloop` 技能调同一个脚本（`--text`）。
5. 桌面通知保留作兜底（两个通道同时发，飞书没送达时 Mac 上还有提示）。

### 3.5 回流和状态

- 每条派单规则写 `tell`，让每一轮做完都出一张仅告知的卡片（结论一句话 + PR 链接），经 `dt-feishu` 发到私聊，带派单的 issue 号（PRD F1.2、F6.1）〔待实测：`tell` 的取值和每轮通知的样子〕。
- 常浩问「后台在干嘛」：`autoloop` 技能跑 `agent-autoloop status --json`，猛鬼兽用人话转述：等你的、在跑的、今天做完的、花了多少。
- 每一轮的来龙去脉：`agent-autoloop why <轮次>`，派单回报里附轮次号。

### 3.6 定时

新的定时活直接写中台规则（`cron`）。现有的 Hermes 定时任务（个人项目日报写稿和发送、AI HOT、Tidings、skill 月度体检）**先不动**，等批 6 再逐个评估迁不迁，迁的话一个一个迁、一个一个验收。

### 3.7 驿窗侧栏（第二通道，后做）

2026-11-15 核实（只读 postpane 源码）：

- 驿窗**没有给外部程序推卡片的接口**：不开网络端口（postpane AGENTS.md:137），MCP 的 `send_message` 等是会话之间传话，不弹侧栏卡片。
- 但它已有「标题 + 选项 + 提交」的提问卡，DeepSeek 插件用的是**文件通道**（`<数据目录>/deepseek-channel/asks/<id>.json`，答案写 `<id>.answer`，`deepseek_plugin/asks.rs`）。照这个加一个「外部」来源，就能让中台的 `dt-postpane` 通道把卡片写成文件、读回答案。
- **碰到已定内容**：postpane `docs/ROADMAP.md:3` 写着「第三方应用只做一键唤起」。推卡片属于改定位，按 P-14 要单独做「改动确认」；动 UI 还要出 3 版方案（postpane AGENTS.md:116）。
- 所以放在批 5，飞书跑稳之后再单独提。

## 4. 授权、信息边界和 D21 重做

- **能回答卡片的只有常浩**：通道 `answerers` 只填常浩的 open_id；猛鬼兽只转交点击，不自己回答。本方案不做托管。
- **真正拦得住的是权限和脚本，不是提示词**：中台的「先问清单」（`askFirst`：删数据、停服、花钱、对外发消息、改产品方向、新建或轮换密钥、修改权限）是写进提示词的要求，Agent 不照做中台拦不住（agent-autoloop SECURITY.md）。所以：
  - 默认 `access: edit`，不开 `full`；
  - 推送、开 PR 由固定脚本做，只推本单分支，不碰 main（3.3）；
  - 不合并：合并仍是常浩说「合并」或点合并官卡片。
- **D21 常设授权重做**：常浩已同意重做。做法是把四档对到中台的三个开关上：哪些项目有派单规则（名册生成）、规则的 `access`、写入单是否免确认（猛鬼兽派单前查 D21）。具体档位单独出一版给常浩定，本方案只定这个对法。
- **信息边界（PRD 第 8 节）**：派单 `detail` 只写任务需要的内容；受限内容不进事件（中台日志会永久保存事件）。中台日志在 `~/.agent-autoloop`，目录 700。
- **密钥**：`hermes` 来源密钥、`dt-feishu` 通道密钥用 `agent-autoloop key new` 新建（只显示一次），存 `~/Library/Application Support/DigitalTwin/autoloop/`（600），不进仓库。新建密钥要常浩点头。
- **MCP**：中台的 MCP 不配进 Claude / Codex 的用户级配置，否则一轮能自己回答自己的卡片（agent-autoloop SECURITY.md）。

## 5. 这台机器上的已知坑

| 坑 | 情况 | 处理 |
|---|---|---|
| 后台没有代理（G-19） | 2026-11-15 已踩：试跑 403 | 已修：`~/.agent-autoloop/bin/claude-proxy.sh`、`codex-proxy.sh`，规则里 `program` 指向它们，中台更新也不会冲掉 |
| 后台读不了「文稿」（G-05） | 项目都在 `~/Documents/ChatGPT/...`，中台是 launchd 起的 node，可能没有读这个目录的权限 | **批 0 第一项实测**。不通的话：常浩在系统设置给这个 node 开「完全磁盘访问」（只能常浩本人改），或换成有权限的程序来启动 |
| nvm 路径写死 | 代理脚本和 launchd 里写死了 `v24.16.0` | 换 Node 版本时一起改；自检里加一项检查 |
| 额度 | 每轮都花会员额度，试跑一次约 $0.44 | 规则别排太密；`status` 有今天和 7 天花费，并进工程自检 |

## 6. 和现有结构的关系（常浩：先不考虑，但并跑期间要防打架）

- 旧结构（`run_worker.py`、`dispatch.py`、`twin_scheduler.py`、Multica 夜跑、`workshop` 技能）**本次一律不改、不停**。
- 并跑期间的防撞规则：**同一个项目只归一边派**。第一批只给中台开 dt-sandbox（加最多一个低风险项目），名册里记一个字段 `dispatcher: autoloop | legacy`，两边都按它拦。
- **托管主线（#126，#134、#136 在做）建在 Multica 夜跑上**。新结构如果最终替换 Multica，托管要重新设计在中台上，现在的部分工作会作废。这是要常浩定的事（第 10 节问题 1）。
- AI 接入（P-13）：`ai_access.json` 新增功能 `autoloop-dispatch`（执行方 claude / codex，接入方式「本机 CLI（经 agent-autoloop 中台）」），自检命令加中台在不在、通道在不在线；`AGENTS.md`「AI 接入」一节同步。切换默认执行方 = 重新生成规则的 `to`。

## 7. 分批实施与验收

| 批 | 内容 | 验收（怎么算做完） |
|---|---|---|
| **0 实测**（不写代码） | ① 后台 Agent 能不能在 `~/Documents` 下的项目读写；② 启动脚本按 subject 建 worktree 并 exec CLI，续接时回到同一目录；③ CLI 退出后脚本还能推分支、开 PR；④ `tell` 每轮通知的样子；⑤ 回答后续接的会话和目录 | 五项各有实测记录，放 `~/Library/Application Support/DigitalTwin/evidence/188-autoloop/`；有不通的回来改方案 |
| **1 飞书拍板** | `dt-feishu` 通道插件；`merge-buttons` 新分支；`autoloop_answer.py`；`autoloop` 技能的「回答卡片」部分；卡片样式 3 版选 1 | `agent-autoloop try` 的卡片到飞书私聊；点「绿色」后 `status` 显示「试跑通过」；同一按钮点两次只算一次；引用卡片打字回也能答；猛鬼兽原有的合并、推翻按钮照常可用 |
| **2 派活** | `hermes` 来源；`autoloop_rules.py`（名册 → 规则）；worktree 启动脚本；`dt-dispatch` Skill；推送 / 开 PR 收尾；`autoloop` 技能的派单部分 | PRD F5 验收照搬，范围是 dt-sandbox：飞书发起 5 单（Claude、Codex 各至少 2 单），都在正确的 worktree 和分支完成、开出 PR、回报到私聊；main 上没有未经确认的改动；名册外的项目派不出去 |
| **3 回流和状态** | 每轮完成通知；「后台在干嘛」查询 | PRD F6 验收：不开电脑，从猛鬼兽那里知道每单做到哪 |
| **4 授权和接入** | D21 四档对到中台开关；`ai_access.json`、`AGENTS.md` 同步；中台状态并进工程自检 | 只看自检输出能答出中台谁做、怎么接、现在能不能用；越权派单被拦下的实证一次 |
| **5 驿窗通道** | 先做 postpane「改动确认」，再出 3 版 UI，再写 `dt-postpane` 通道 | 卡片在侧栏弹出、点选项后中台收到回答 |
| **6 收口与退役** | 写 ADR-015、改 ARCHITECTURE；现有定时任务逐个评估迁移；旧派单结构退役清单 | 常浩验收「只保留这一套」；退役每一项都能回滚 |

每批一张 issue、一个分支、一个 PR，按《工程流程》走；改了脚本跑全量测试（G-03）。

## 8. 回退

- 批 1–5 期间旧结构全程在，回退就是：`agent-autoloop pause <规则>` 停派单，或 `agent-autoloop uninstall`（保留数据）；`merge-buttons` 的新分支只认 `autoloop` 开头的按钮，删掉不影响合并、推翻按钮。
- 批 6 之前不删任何旧东西。

## 9. 风险

| 风险 | 对策 |
|---|---|
| 派单正文被当资料，Agent 不照做或做偏 | `dt-dispatch` Skill 写清信任边界；批 2 用 5 单验收 |
| `askFirst` 拦不住 | 靠 `access: edit` + 固定脚本推送 + 不合并；不开 `full` |
| agent-autoloop 是第三方项目，更新可能改行为 | 不开自动更新；`update` 前先读 Release 说明，在本仓库记版本；自己写的插件放本仓库 |
| 两套结构并跑打架 | 名册 `dispatcher` 字段，一个项目只归一边 |
| 中台挂了 | launchd 常驻自动拉起；工程自检查 `/healthz` |
| 同一飞书应用被两个监听抢 | `dt-feishu` 只发不收，代码评审时专门查没有 `event consume` |

## 10. 要常浩拍板的事

1. **托管主线怎么办**：托管建在 Multica 夜跑上，新结构最终会替换它。推荐：在跑的 #173 返修让那个会话做完收尾，#134、#136 **暂停**，等本方案批 2 验收后再决定托管是在中台上重做还是放弃。备选：托管照常推进，两边并行。
2. **第一批开给哪些项目**：推荐只开 dt-sandbox，跑通后再一个一个加。
3. **推送和开 PR 谁来做**：推荐 3.3 的做法 A（固定脚本收尾），不给 Agent `full`。
4. **送审**：按 ARCHITECTURE §12.4，这种级别的改动要经驿窗送 ChatGPT High 独立审查。推荐：常浩先看一遍本方案、改完再送审。
