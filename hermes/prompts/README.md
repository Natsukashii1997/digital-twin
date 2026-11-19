# prompts

存放定时任务等场景给 Hermes 用的提示词（源文件）。

- `morning_brief.md`：工作日晨报提示词，配合 `hermes/scripts/brief_data.py` 整理好的材料使用；样稿在 `hermes/samples/`。第 4 批已挂起，目前没有建定时任务（见 CURRENT.md）。
- `daily_report.md` / `weekly_report.md`：Hermes 按 T1 方案 A 写日报、周报的提示词，配合 `report_data.py` 的材料、用 `report_publish.py` 写进并跑文档（T7）。定时任务「日报（新结构·并跑）」每天 07:30、「周报（新结构·并跑）」周一 07:45。
- `project_vocab.md`：日报「项目」字段的词表（常浩 2026-10-27 确认）。

安装位置：`~/.hermes/prompts/`（`report_data.py` 按 `scripts` 的兄弟目录找词表）。改了源文件要复制过去。

亚古兽的提示词（文件名以 `memdaily_`、`sidebar_` 开头）装在 `~/.hermes/profiles/agumon/prompts/`：

- `sidebar_feedback.md` / `sidebar_progress.md` / `sidebar_notice.md`：销售 AI 侧边栏的需求反馈（每天 18:00）、项目推进进度（每周三 18:30）、功能同步通知（常浩说了才出），配合 `hermes/scripts/sidebar_reports.py`；样例 `sidebar_notice_sample.md`（11-12 通知原文）、`sidebar_progress_sample.json`（11-18 定稿卡片的内容 JSON）。
