---
name: workshop
description: "派 Claude Code / Codex 车间跑单（统一包装脚本）"
version: 0.5.0
author: 常浩 · digital-twin
platforms: [macos]
metadata:
  hermes:
    tags: [Coding-Agent, Claude, Codex, Dispatch]
    related_skills: []
---

# 车间：派 Claude Code / Codex 跑单

常浩让你“叫 Claude / Codex 去做……”“让车间改一下……”时用这个技能。**一律通过派单脚本 `~/.hermes/scripts/dispatch.py`**，不要直接敲 `claude`、`codex`、`git worktree`、`git merge`。需求总纲见 `~/Documents/ChatGPT/个人/work/digital-twin/PRD.md` 的 F5、F6；改动从 issue 到合并怎么走见 `~/Documents/ChatGPT/个人/work/agent-kit/工程流程.md`。脚本每条命令最后输出一行 JSON：`ok` 表示成败，`msg` 是可以直接转给常浩的一句话。

下面用 `D` 代表 `/usr/bin/python3 ~/.hermes/scripts/dispatch.py`。

## 1. 整理成派单

1. **项目**：先读名册 `~/Documents/ChatGPT/个人/work/digital-twin/projects.json`，按 `name` 和 `aliases` 匹配，取 `id`。匹配不上，告诉常浩“不在名册里”，问要不要加进去；匹配到多个，先问。不要自己去读名册外的项目来代替派单。
2. **只读还是写入**：要改代码、改文档、提交的就是写入单；看、查、总结、出方案的是只读单。
3. **交给谁**：常浩点名就听他的；没点名时，读代码、查问题、改后端默认 Codex，写文档、梳理方案、改界面默认 Claude，在回复里说一句为什么。
4. **任务说明**写进一个临时文件，必须包含五要素：目标、交付物、验收标准（怎么算做完）、边界（哪些不做）、交给谁。车间看不到你和常浩的对话，要写清楚。

## 2. 开单和确认门

1. 先开单拿到单号：`D new --project <id> --tool <claude|codex> --mode <read|write> --task-file <任务文件>`。写入单会自动建 worktree（`~/.hermes/worktrees/<项目>/<单号>`）和分支 `hermes/<单号>`；名册里有 `github` 的项目，分支从 GitHub 上的主分支拉出，并自动建一张 issue（`msg` 里会带 issue 号；建不成也照常开单，`msg` 会说明）。
2. **只读单不用确认**，直接开跑（第 3 节）。
3. **写入单先发给常浩确认**，格式：
   ```
   📝 待确认派单 <单号>
   项目：xx　车间：Claude/Codex　类型：写入
   目标：…
   验收：…
   边界：…
   回复「可以」开跑，或者直接说要改哪里。
   ```
   只有常浩回复“可以 / 行 / 跑吧”等明确同意，才开跑。他要改，就改任务文件（`~/.hermes/dispatch/<单号>/task.md`）后再发一次确认；他说不要了，执行 `D clean <单号>`。

## 3. 开跑

1. 用 terminal 工具**后台**运行（`background=true`）：`D run <单号>`，并告诉常浩“已派给 X，单号 …”。
2. 返回 `busy: true` 表示在排队（同时最多 2 张单，同一个仓库一次只跑 1 张）：告诉常浩“排队中”，等手上的单跑完收到通知后，再对排队的单执行 `D run <单号>`。

## 4. 回报

写入单跑完、状态是 done 时，先执行 `D pr <单号>`：推分支、开 PR，返回 `pr_url`。`ok: false` 时（提交里像有密钥、推不上去、PR 没开成）把 `msg` 原样写进回报，不要重试。续跑后再跑完，同样执行一次，新提交会推到原来的 PR。

然后读 `~/.hermes/dispatch/<单号>/out/receipt.json`，在飞书回报：
- 第一行：✅/❌/⚠️ 单号 · 交给谁 · 用时
- 车间总结（取 `summary` 的要点，不整段照贴）
- 写入单再加四项：PR 链接（`pr_url`，常浩可以在手机 GitHub 上看改动）、分支、提交了哪些文件（`committed_files`）、有没有未提交的改动（`uncommitted`）；最后问“合并吗？”
- 有 `need_decision` 时，把问题原样转给常浩
- `guard_main_changed` 或 `guard_remote_changed` 只要有一个是 true，第一行用 ⚠️，说明车间把提交弄进了主分支或动了远端，不要重试
- `guard_remote_changed` 只看本单分支在远端有没有出现或变化（车间自己 push 了）
- `note_main_moved` 或 `note_remote_moved` 不为空：主分支或别的远端引用在此期间被别的会话正常更新了，末尾提一句即可，不算告警

## 5. 车间卡住了（need_decision）

1. 回执里 `need_decision` 不为空，说明车间停下来等常浩拍板。原样转给常浩，开头写“❓ <单号> 需要你定：”。
2. 常浩回答后，把他的答复写进一个临时文件（例如 `~/.hermes/dispatch/<单号>/answer.md`），后台运行 `D run <单号> --followup <这个文件>`。车间会在**原来的会话**里接着做，前面的上下文都在。
3. 跑完照第 4 节回报。

## 6. 话题

- 常浩在某个话题里回复时，你在那个话题里收到的是一个新会话，看不到主对话的上下文。先从他引用的那条消息里找单号，再读 `~/.hermes/dispatch/<单号>/order.json` 和 `task.md` 恢复这张单的情况，然后接着处理。
- 话题里只谈这一张单；别的事让常浩回主对话说。

## 7. 合并和清理

- 只有常浩明确说“合并”才执行 `D merge <单号>`。脚本会依次检查：worktree 是否干净、主仓库是否在主分支上。名册里有 `github` 的项目接着走 GitHub：
  1. 推送前检查：本地主分支有没推到 GitHub 的提交（多半是别的会话的）时，返回 `need_confirm: true`，`msg` 里列出这些提交的标题。把 `msg` 原样转给常浩，问“这些提交能推吗？”。他明确回“推”后，执行 `D merge <单号> --push-main <返回的 push_main>`；他不同意就停下，不合并。
  2. 推本地主分支（只有上一步确认过才推）；本地和 GitHub 分叉时停下来报告。
  3. 分支变基到最新的主分支，跑名册里的测试命令，测试过了才推分支。
  4. 在 GitHub 上 squash 合并 PR，关联的 issue 自动关闭。
  5. 主目录拉齐。拉不下来时 `ok` 仍是 true、`synced: false`：GitHub 上已经合并了，把 `msg` 转给常浩，说明本地主目录要人工处理。
- 名册没有 `github` 的项目照旧：变基、测试、本地快进合并，不 push。
- 合并失败（要确认没推的提交、分叉、冲突、测试没过、推不上去、GitHub 合并失败）就把 `msg` 原样转给常浩，问他怎么处理，不要自己改代码硬合，不要自己加 `--push-main`。
- 合并成功后执行 `D clean <单号>` 删掉 worktree 和分支（走 GitHub 的单会先确认 PR 是 MERGED）。常浩说“不要了 / 放弃”时也执行 `D clean <单号>`，有 PR 和 issue 的会一起关掉。
- 想看还有哪些单没收尾：`D list`。

## 失败信号

- `run` 返回 `ok: false` 且不是 `busy`：看 `stderr_tail` 和 `~/.hermes/dispatch/<单号>/out/stderr.log` 的最后几行，说明原因。同一张单最多重试一次。
- 护栏被触发（单的状态是 `guard`）：立刻报告，不重试，也不合并。

## 验收方式

常浩在飞书说“让 Claude 在 digital-twin 里改……”：你先开单、发待确认派单 → 他回“可以”（可能在话题里回） → 开跑 → 车间卡住时把问题转给他、他答复后续跑 → 开 PR、回报 PR 链接和改动 → 他说“合并”就合并（有没推的提交先问他）并清理，说“不要了”就清理。
