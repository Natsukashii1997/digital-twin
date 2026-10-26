---
name: workshop
description: "派 Claude Code / Codex 车间跑单（统一包装脚本）"
version: 0.1.1
author: 常浩 · digital-twin
platforms: [macos]
metadata:
  hermes:
    tags: [Coding-Agent, Claude, Codex, Dispatch]
    related_skills: [claude-code, codex]
---

# 车间：派 Claude Code / Codex 跑单

常浩让你“叫 Claude / Codex 去做……”“让车间看一下……”时用这个技能。**一律通过包装脚本调用**，不要直接敲 `claude -p` 或 `codex exec`：网关环境没有代理，直接调用 claude 会 403；包装脚本还负责护栏和回执。需求总纲见 `~/Documents/ChatGPT/个人/work/digital-twin/PRD.md` 的 F5、F6。

## 当前阶段（M2 批 A）

- **只接只读单**（读代码、读文档、总结、查问题、给方案），`--mode read`。
- 写入单（改代码、提交）要等派单技能 `dispatch` 上线（M2 批 C），在那之前常浩要写入就告诉他“写入单还没开通”。
- 只能在下面这些仓库里跑（项目名册上线前的临时清单）：
  - 牛马之家 `~/Documents/ChatGPT/个人/work/work-desk`
  - personal-ai-workbench `~/Documents/ChatGPT/个人/work/personal-ai-workbench`
  - agent-kit `~/Documents/ChatGPT/个人/work/agent-kit`
  - github-center `~/Documents/ChatGPT/个人/work/github-center`
  - digital-twin `~/Documents/ChatGPT/个人/work/digital-twin`

## 步骤

1. 确定 **项目**（在上面的清单里）和 **交给谁**：常浩点名就听他的；没点名时，读代码、查资料类默认 Codex，写文档、梳理方案类默认 Claude，在回复里说一句为什么。
2. 生成单号 `D` + 年月日 + `-` + 当天序号，例如 `D20261026-1`。建目录 `~/.hermes/dispatch/<单号>/`，把任务写进 `task.md`：要做什么、交付什么、哪些不做。写清楚，车间看不到你和常浩的对话。
3. 用 terminal 工具**后台**运行（`background=true`），并告诉常浩“已派给 X，单号 …”：
   ```
   /usr/bin/python3 ~/.hermes/scripts/run_worker.py --tool <claude|codex> --cwd <仓库路径> --task-file ~/.hermes/dispatch/<单号>/task.md --out ~/.hermes/dispatch/<单号>/out --mode read --timeout 1800
   ```
4. 运行结束后读 `~/.hermes/dispatch/<单号>/out/receipt.json`，在飞书回报：
   - 第一行：✅/❌ 单号 · 交给谁 · 用时
   - 车间总结（取 `summary` 的要点，不整段照贴）
   - 有 `need_decision` 时，把问题原样转给常浩
   - 护栏：`guard_main_changed` 或 `guard_remote_changed` 只要有一个是 true，**第一行就改成 ⚠️**，并说明是车间把提交弄进了主分支，或者远端被动过
   - `note_main_moved` 不为空时，说明跑单期间主分支被别的会话正常更新了（不是车间干的），在回报末尾提一句即可，**不算告警**
5. 失败信号：退出码 1（车间失败或超时）就看 `out/stderr.log` 的最后几行，说明原因；退出码 2 是护栏被触发，立刻报告，不要重试。同一张单最多重试一次。

## 验收方式

常浩在飞书说“让 Claude/Codex 看一下 xx 仓库……”，你能派出去、等它跑完，并按上面的格式回报。
