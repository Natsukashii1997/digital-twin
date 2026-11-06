# ADR-013 项目协同改用 Multica，移除 ORCA

- **状态**：提议（随 issue #60 的 PR 合并后生效）
- **日期**：2026-11-07
- **决定人**：常浩
- **取代**：ARCHITECTURE V3.1 第 12.2 节的 ADR-003（「ORCA 原生 Run/Task/Dispatch/消息/Worktree 是执行真源」）；ADR-001、ADR-012 中写到 ORCA 的部分改写为 Multica，原则不变
- **架构基线**：[`../ARCHITECTURE.md`](../ARCHITECTURE.md) V4.0；旧版见 [`../history/2026-11-02-V3.1-A+-基于ORCA的总架构.md`](../history/2026-11-02-V3.1-A+-基于ORCA的总架构.md)

## 背景

V3.1 把 ORCA 定为工程执行真源，ChatGPT 送审走 ORCA 内嵌浏览器（RELAY-01 `orca/chatgpt_relay.py`），循环驱动走 RELAY-02（PR #40，未合并）。到 2026-11-07：

1. **ORCA 路线没有跑出持续可用的闭环**。RELAY-01/02 能跑通单次送审（#38 → PR #39 首次真实试运行），但每次都要人工补救（代理、会话编号、续跑 collect）；M1（B11 文件隔离 + `run_authorization`）和 M2 自动接线都没有验收；`merge_gate.py` 从未进 main（只在本地 `claude/37-framework-v2-*` 分支）。
2. **送审已有更稳的通道**。驿窗（postpane）的 Chrome 插件通道直接用常浩自己 Chrome 里登录的 chatgpt.com，`relay_to_chatgpt` 带接力记录和自动往返（最多 2 轮），并已在 2026-11-07 的 L5 第一单实测过（见 postpane `docs/chatgpt-channel.md`）。
3. **Multica 提供了现成的「issue → agent → run」协同层**：多 runtime（Claude Code / Codex）、Squad、Autopilot、Wakeup、run 记录可导出。本机 2026-10-16 已试装 v0.6.2 并跑通一次派单到写回（`~/Documents/Codex/2026-10-16/https-github-com-multica-ai-multica/outputs/安装与验证记录.md`）。
4. 公司《员工 AI 应用能力考核制度 V1.0》自评结论：机制有 L6 的形状，证据只到 L3。缺的是「真跑过、有记录」，不是再多一层自建编排。

## 决定

1. **项目协同（执行层）改用本机自托管 Multica，锁定 v0.8.1**，daemon 原生跑在 Mac。ORCA 整体移除，不再作为任何流程的依赖。
2. **事实所有权三分**：Hermes `twin_state` 管业务（目标、优先级、授权、收口）；Multica 管执行（issue 的执行状态、agent、run、runtime）；GitHub 管交付（分支、PR、HEAD/base、合并）。三者之间只同步 ID 和摘要。
3. **角色即 agent**：在 Multica 里建「Claude 开发」「Codex 验证」「审查员」三个 agent；审查员把送审包经驿窗通道送 ChatGPT 聊天模式 High。**Hermes 不当 Multica agent**，只经 Multica CLI/API 建单、读 run、发晨报；飞书只走猛鬼兽一个机器人。
4. **Multica 不推送也不合并**：agent 只推 `agent/<issue>` 分支并开 PR；合并归 Hermes 合并官（先影子运行）加 `owner_approved_policy` 策略记录，常浩说「合并」前一律不合。
5. **双模式与 `run_authorization` 保留**：SUPERVISED = 常浩在场手动指派；UNATTENDED = Autopilot / Wakeup，只限名册里登记为沙盒的项目。
6. ChatGPT 送审改走驿窗通道；`orca/chatgpt_relay.py`（RELAY-01）、`hermes/scripts/dispatch.py`、`hermes/scripts/run_worker.py` 标为 Legacy；RELAY-02（PR #40）以「被 V4.0 取代」关闭。

## 理由

- 复用成熟的协同层，比继续自建 ORCA 适配器（relay_loop、work_queue、pilot_executor 等）更快拿到真实运行证据。
- 驿窗通道已有接力记录、去重、超时与自动往返，正好补上 V3.1 里最脆弱的「受控 ChatGPT 会话」。
- 合并权留在 Hermes + 策略记录，Multica 只是执行层，换掉它不影响授权体系。

## 代价与风险

见 ARCHITECTURE V4.0 第 14 节。要点：许可证、版本节奏快（锁 v0.8.1，升级走 OPS）、`MULTICA_TOKEN` 被 agent 子进程继承、工作目录 GC 可能删掉没推送的工作、遥测须关、驿窗依赖 Chrome 开着且电脑不睡。

## 迁移与回退

- **卸载 ORCA 的前提**（缺一不可）：① V4.0 首跑里至少一单经驿窗送审拿到「结论：通过」；② 常浩同意卸载变更单。之前 ORCA 只停用、不卸载。
- **卸载方式**：OPS 变更单，ORCA 应用、`/usr/local/bin/orca`、`~/.orca` 先备份再删。
- **回退**：Multica 首跑不成立时，回到「常浩手动在驿窗发起送审 + Claude 会话开发 + 常浩手动合并」的人工流程（不依赖 ORCA，也不依赖 Multica）；ORCA 已卸载的，从备份恢复后按 V3.1（history 里的原件）执行。Legacy 脚本在退役前一直留在 main，不删。

## 后果

- ARCHITECTURE 升 MAJOR 4.0（按 V3.1 第 12.3 节：执行架构变更）。
- AGENTS.md、CURRENT.md 中写 ORCA 的地方，在本 PR 合并后另开 PR 更新（本 PR 只改 `docs/DT-ORC/**`）。
- Multica 部署与首跑（批 E）以本 ADR 合并为前提。
