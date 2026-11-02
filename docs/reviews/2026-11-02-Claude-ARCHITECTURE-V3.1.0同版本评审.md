# Claude 同版本独立复核：ARCHITECTURE.md V3.1.0 · A+

- **评审对象**：`ARCHITECTURE.md`（V3.1.0 · A+，606 行，43000 字节）
- **SHA-256**：`8d7bb2749dd4f7b38f1cab3c377c18e6cce8ede81e4d1d2d59e10793c7edcfbf`
- **评审方**：Claude Code（Opus 5.5），2026-11-02
- **对应**：V3.1.0 第 12.4 节「Claude 对本文件 SHA-256 给同版本反馈」及第 13 节检查表。只对上述摘要有效；文本一改，本结论失效，需重新绑定。
- **本次没有做的事**：没有改代码，没有起 worker，没有向 ChatGPT 发送，没有推送或合并，也没有动正式目录。

## 总结论

**NEEDS_HUMAN（R1，需 Owner 选定 SUPERVISED 的可信签发来源）+ REWORK（R2–R5，定向文字修订，不改架构）。**

整体架构认可，不需要另起方案。双模式、PASS 不等于合并许可、B11 现有禁令如实保留、ORCA 原生优先、角色分工、单一基线文件，这些都成立。剩下的问题集中在一处：**SUPERVISED 的「可信启动」和「执行桥」没有写明最低标准**。如果不写清楚，M1 很可能重新陷入 B8–B11 那种「身份怎么证明」的反复复核里，A+ 想要的「先跑通有人值守真实闭环」就会落空。

## 第 13 节检查表逐条结论

| # | 检查项 | 结论 | 说明 |
|---|---|---|---|
| 1 | 五角色主责；Codex 是否按需 | PASS | 与 Owner 在 V3.0→V3.1 中保留的「按需」一致；Owner 11-02 关于额度的说法已由 3.1 节「额度充足时可提高专项覆盖」承接 |
| 2 | Claude 质疑 ChatGPT 的证据机制、无一言堂、无无限返修 | PASS | 6.2–6.4 节：严重度和可信度分开，一次异议加一次复核，返修预算 1/2 轮，旧 PASS 失效 |
| 3 | SUPERVISED 不等 Secure Enclave，且保持现有禁令 | **NEEDS_HUMAN + REWORK** | 方向对；但 R1（可信来源）、R3（人工试点与 M1 的关系）、R4（执行桥是什么）没写清，实际仍可能被 G1 卡住 |
| 4 | 通过显式 Run 授权，而非打开 ORCA 或合并文档来偷换授权 | **NEEDS_HUMAN** | 4.3、5.2(1) 排除了偷换途径，但没有给出任何可接受的正面来源，见 R1 |
| 5 | L0–L2 自动合并范围策略、CI/部署副作用 | **REWORK（小）** | 5.5 节条件完整；第 6 条「保护规则现读」在本仓库不可用，见 R5 |
| 6 | B11-R5、误写租约、NO-GO 如实保留 | PASS | 已逐项核实，见「核实过的事实」 |
| 7 | ORCA 原生优先，不删必要的幂等与审查租约 | PASS | 7.1、7.3、10.2 节；建议配合 R4 写明最小执行桥直接用 ORCA CLI |
| 8 | Obsidian / GitHub / Skills 真源、权限与回流 | PASS | 2.2、8.1–8.3 节 |
| 9 | 术语自洽，CI 未被虚构 | PASS | 4.4 节取消 W0–W4；本仓库确实没有 `.github/workflows`，文中没有把 CI 写成现有能力 |
| 10 | `ARCHITECTURE.md` 唯一总纲；history 与 AGENTS 迁移可安全实施 | PASS | 12.1、12.3 节；M0 盘点时注意下面列出的两份未跟踪文件 |

## 定向修订意见（每条附证据）

### R1【NEEDS_HUMAN】写明 SUPERVISED 的最低可信签发来源

**位置**：4.3 节、5.2 节第 1 条、5.4 节最后一段、10.1 节 M1「明确定义本次 Run 授权」。

**问题**：文中只说「可信交互 / 策略验证器」「明确签发来源」，没有说什么来源算可信。B6–B11 卡住的恰恰就是这个问题：

- `FRAMEWORK-V2.md` 第 10.2 节列出 G1 的 4 个未决问题：认证来源、验证材料、防重放、验证器归属。
- 第 11.5 节 D1–D3 记为 NEEDS_HUMAN。
- B11 的 R1–R5 每轮独立复核都是 REWORK（证据目录 `~/Sandbox/2026-11-01-orca-b11-r1-evidence` … `orca-b11-r5-evidence-20261101`）。

按现在的写法，M1 会把这个开放问题原样带回来。

**建议文字**（Owner 选一项或两项都要）：

> SUPERVISED 的 `run_authorization` 只接受以下来源，**不要求 Secure Enclave、Touch ID 或签名挑战**：
> (a) Owner 本人在本机终端运行确定性授权命令（例如 `dt-run authorize`），由脚本列出任务、仓库、动作上限和有效期，Owner 交互确认后落盘授权记录。记录由脚本生成，不由模型生成。
> (b) 飞书私聊中 Owner open_id 发出的确认，经已部署的 B2.1 `twin_identity` 核验（D0 记为 ACCEPTED / DEPLOYED）。
> SUPERVISED 的威胁模型不包括同一用户下的恶意本地进程；那一类威胁归 UNATTENDED（M4）处理。

**Claude 推荐**：(a) 和 (b) 都要。(a) 用在电脑前，(b) 用在不在电脑前时。

### R2【REWORK】给 M1 设定冻结范围和退出条件

**位置**：10.1 节 M1、10.3 节工单。

**证据**：B11 每轮复核都有新问题：R1 有 5 项 P1，R4 第二轮出现新方向，R5 又出现文件级链接（`FRAMEWORK-V2.md` 第 14.4–14.6a 节，`orca-b11-r5-evidence-20261101/independent-review.txt`）。M1 如果没有退出条件，会重演这个过程。

**建议文字**：

> M1 范围冻结为 `independent-review.txt`（B11-R5，基于 `c060df8`）指出的「合成 `delivered/`、`sending/` 目录内 `.json` / `.lock` 文件级符号链接跟随」一项及其反向测试。审查轮数按 6.4 节 L2（最多 2 轮）。复核中新提出、且不在 M1 文件清单内的问题：只有对 M2 路径构成 P0 的才继续阻断，其余记入待办。超过轮数交 Owner 决定。

### R3【REWORK】写明人工直控试点可以和 M1 并行

**位置**：5.2 节最后一段、10.1 节 M2 行和表下说明。

**问题**：5.2 节允许 Owner 直接执行一次明确授权的动作，作为降级路径；但 10.1 节写「M2 在 M1 必要接线验收后」，表下又说「必须满足 M1 的最小安全准入」。人工试点要不要等 M1，前后不一致。

**证据**：R5 未关闭的缺陷只在使用**合成（测试）状态根**时才会出现，路径是测试运行经文件链接写到正式目录（`independent-review.txt` 第 1–10 行）。人工直控试点用的是真实 ORCA 和真实页面，不使用合成根和 fake 传输，不经过这条缺陷路径。

**建议文字**：

> 人工直控试点（Owner 亲手执行 `worker-start` 和 ChatGPT 发送，并如实标注「人工触发」）不经过 M1 所修的代码路径，可与 M1 并行。自动接线的 M2 仍以 M1 验收为前提。

### R4【REWORK】写明 SUPERVISED 的「最小执行桥」是什么

**位置**：5.2 节、5.6 节（「执行桥安全验收」「最小受监督执行桥 / 白名单机制」）。

**问题**：现有的执行桥是 B6 的 `pilot_gate` 和 B7 的 `pilot_executor`，它们只接受 `TEST_DOUBLE` 证明，真实现场一律 NEEDS_HUMAN（`FRAMEWORK-V2.md` 第 10.2 节）。如果「执行桥」指的是它们，SUPERVISED 就又被 G1 卡住了。

**证据**：本机 ORCA 1.4.245 的 `worker-start` 只在 ready 时返回 0；失败或 `outcome_unknown` 时返回非 0，并给出 `failedStage`、`residualResources` 和恢复命令；另有 `request-show` 用来核实一次变更是否已经生效（`orca orchestration worker-start --help`、`orca skills get orchestration`）。ORCA 原生已经覆盖了 7.3 节要求的「结果不明只读对账」。

**建议文字**：

> SUPERVISED 的最小执行桥，是以固定 argv 白名单直接调用本机 `orca orchestration worker-start / worker-show / request-show / worker-stop`，并按 `run_authorization` 计数消耗。不要求复用 `pilot_gate` / `pilot_executor`；这两者归入 UNATTENDED 候选素材。ChatGPT 发送桥沿用已合并的 RELAY-01 `chatgpt_relay.py`，并要求固定会话 URL、模式和强度核对、租约、结果不明不重发（5.2 节第 4 条已有）。

### R5【REWORK（小）】本仓库没有 GitHub 分支保护，范围限制要靠本地强制

**位置**：5.2 节第 5 条、5.5 节第 6 条。

**证据**：`gh api repos/Natsukashii1997/digital-twin/branches/main/protection` 返回 HTTP 403：「Upgrade to GitHub Pro or make this repository public to enable this feature」。私有仓库在当前套餐下不能设分支保护，所以「只能推指定分支」这类结构约束 GitHub 侧做不到。

**建议文字**：

> 本仓库当前没有可用的 GitHub 分支保护（API 403）。push 和合并范围由本地执行端强制：Claude 执行方的 deny 规则（PR #40 已有越权合并检测）加上 `merge_gate.py`。5.5 节第 6 条中，保护规则读取结果为「不可用」时记录在案，不视为冲突，也不视为结构约束已生效。

## 核实过的事实（支持第 6、9 项 PASS）

- **B11-R5**：`~/Sandbox/orca-b11-r5-evidence-20261101/py39.log` 和 `py314.log` 都是 `Ran 347 tests … OK`；`independent-review.txt` 结论为 **REWORK**，问题是合成 `delivered` 目录内文件级链接跟随写入正式路径，复核方式是静态审查、未执行。R5 工作树基于 `c060df8`，5 个文件改动加 1 个新测试未提交。与 9 节、10.3 节一致。
- **真实动作开关**：`queue_adapter.py:71` 为 `LIVE_ACTIONS_ENABLED = False`，与 5.2 节一致。
- **合并资格闸门**：`merge_gate.py:26-27` 为 `AUTHORIZERS = ("owner",)`、`AUTHORIZATION_SOURCES = ("owner_direct",)`，与 5.5 节「需扩展为区分 `owner_direct` 与 `owner_approved_policy`」一致。
- **误写租约**：`~/Library/Application Support/DigitalTwin/relay/review-lease/f51c7b83….json` 和 `.lock`（11-01 12:56）仍在，与 9 节一致。
- **CI**：`digital-twin` 下没有 `.github/workflows`。

## M0 盘点时请一并处理

- 两份未跟踪文件：`docs/DT-ORC/数字分身工程化整合方案-V2.md`（Claude V2.0，应移入 `history/`），以及 `docs/reviews/` 下的两份 Claude 评审（V3.0 和本份，应随 PR 入库）。
- V3.0 原件目前只在 `~/Downloads`，应随 M0 PR 存入 `history/`。

## 预期

R1 由 Owner 选定、R2–R5 按上述文字修订后，生成新的精确文本。请重新计算 SHA-256，Claude 会对新摘要出复核；在这些修订范围内，预期结论为 PASS。之后按 12.4 节交 ChatGPT High 复核差异。
