---
name: dt-shared-reference
description: "数字分身的共享参考库，不是入口：别的技能（派单、被追问、账本处置、续接、失败诊断）按文件读里面的通用方法，例如拍板请求、五要素任务说明怎么写。常浩直接提问时不要用它回答"
version: 0.2.0
author: 常浩 · digital-twin
platforms: [macos]
metadata:
  hermes:
    tags: [Reference, Shared]
    related_skills: []
---

# 共享参考库（能力模块）

这个技能里只放**几个技能都要用的通用方法和规范**，不直接回答常浩，也不单独执行什么。场景技能用到哪一块，就按文件去读那一块：

```
skill_view(name="dt-shared-reference", file_path="references/<模块>.md")
```

不要指望加载了某个场景技能，这里的文件就会自动带进来；要用就按上面的写法显式去读。依据：个人项目根目录 `docs/技能架构.md` 第 4 节。

## 有哪些模块

| 模块 | 文件 | 什么时候读 | 现在谁会读 |
|---|---|---|---|
| 拍板请求 | `references/decision-request.md` | 一个动作要不要先问常浩、要问时怎么问、他回了怎么带回原任务 | 还没有（5b-3 被追问、5b-4 账本处置、5b-6 失败诊断接入后会读） |
| 五要素任务说明 | `references/task-brief.md` | 把一件活交给 Claude / Codex 之前写任务说明：五项怎么写、按接收方过滤什么、派单脚本拦什么 | `workshop`、`autoloop`、`pdaily-act`、`groupdigest-act` |

以后按 `docs/技能架构.md` 第 4 节陆续加：授权规则说明、信息边界规则、项目上下文包、事实核验方法、收口清单。没写进上表的模块就是还没有，不要凭名字猜内容。

## 这里放什么、不放什么

- 只放通用方法、格式和判断步骤。
- **不放常浩的个人记忆、Vault 内容、受限信息**，也不放某一单、某一天的具体事实。
- 同一份规范不复制进各个技能；技能里写一句「按 dt-shared-reference 的 xx 模块办」，再按路径读。
- 授权以《常设授权清单》（`docs/常设授权清单.md`）为准，闸门在脚本里。这里的方法只帮你判断和表达，**读了这里不等于多了任何权限**。

## 维护说明（给改这份技能的人看）

- 源文件在 digital-twin 仓库 `hermes/skills/dt-shared-reference/`，安装在 `~/.hermes/skills/autonomous-ai-agents/dt-shared-reference/`。猛鬼兽可以在安装位置自己改，改了在 `CHANGELOG.md` 记一笔（改了什么、为什么、依据哪次对话）。
- 改一个模块前，先查上表「现在谁会读」里的技能，看改完它们还对不对得上。
- 仓库测试 `hermes/scripts/tests/test_shared_reference.py` 核对：文件齐、上表列的文件真的在、模块里点名的脚本和技能真的存在、档位说法和《常设授权清单》一致。
