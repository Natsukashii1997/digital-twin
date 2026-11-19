---
name: sales-sidebar
description: "销售 AI 侧边栏项目（亚古兽负责）：需求反馈日报、项目推进进度卡片、功能同步通知，以及项目问答"
version: 1.0.0
author: 常浩 · digital-twin
platforms: [macos]
metadata:
  hermes:
    tags: [Sales-Sidebar, Feishu, Report, Project]
    related_skills: [lark-im, lark-base, lark-doc]
---

# 销售 AI 侧边栏

常浩 2026-11-19 定：销售 AI 侧边栏（企微侧边栏）是公司项目，由你（亚古兽）负责跟进。下面这些情况用这个技能：

- 定时任务：每天 18:00 的需求反馈、每周三 18:30 的项目推进进度；
- 常浩在私聊里提到「需求反馈 / 反馈汇总」「进度卡片 / 项目进度 / 周报」「出个通知 / 功能同步通知」，或者回复其中某一期（「改一下……」「发群」「先不发」）；
- 常浩问这个项目的事（进度、某个功能、某条反馈、排期、谁负责、还有什么没定）。

## 先读什么

1. **项目档案** `~/.hermes/profiles/agumon/workspace/销售侧边栏-项目档案.md`：目标、四板块框架、谁是谁、资料 token、版本路线、最近一次进度快照、已定做法、还没定的事。每次用这个技能都先读。
2. 再按场景读本技能的一份说明：

| 场景 | 说明 | 频率与去向 |
|-|-|-|
| 需求反馈 | [references/feedback.md](references/feedback.md) | 每天 18:00 私发常浩确认；他回「发群」才用他本人身份发测试反馈群 |
| 项目推进进度 | [references/progress.md](references/progress.md)，样例 [progress-sample.json](references/progress-sample.json) | 每周三 18:30 私发常浩卡片，不发群 |
| 功能同步通知 | [references/notice.md](references/notice.md)，样例 [notice-sample.md](references/notice-sample.md) | 常浩说了才出；先私发确认，他回「发群」再发 |
| 项目问答 | 只读项目档案，再现查 | 现查测试反馈群、三张表和存档 `~/Library/Application Support/DigitalTwin/sidebar/`，说明数据截至什么时候；表和群对不上时两边都说 |

## 不能破的规矩

- **版式是常浩逐项定稿的**：需求反馈 11-13 定、11-17 补空行；进度卡片 11-18 定；通知按 11-12 那条。需求反馈和进度卡片一律用脚本 `~/.hermes/profiles/agumon/scripts/sidebar_reports.py` 生成和发送，你只写内容 JSON，不自己拼飞书消息，不加、删、改区块。
- **同一期原地改**：常浩要改，就改内容 JSON 再跑 `send`，不另发一条。
- **发群**：以常浩本人身份发群，每次都要他在私聊里明确回「发群」，而且只发一次；用户身份发出的消息改不了。
- **处理情况按可核验确认更新**：「已修复」「已上线」默认以开发同学（主要是卢文）在测试反馈群里的回复为准；常浩在私聊明确确认具体条目时，按他圈定的范围更新，不外推到相似条目。私下会议、开发同步录音里的说法不算。
- **不编数据**：拿不到、对不上的写进回复问常浩；口径说明、待核实这类话不写进卡片或反馈。
- **还没定的事不自己定**：见项目档案第七节，碰到时提醒常浩。
- **改项目档案**：先在私聊提议改哪几行，常浩同意后再改，改完告诉他。
